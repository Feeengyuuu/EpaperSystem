"""Descriptor-bound storage for prepared presentation PNG artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import io
import logging
import os
from pathlib import Path
import re
import secrets
import stat as stat_module
import threading
import time
from uuid import UUID
import warnings

from PIL import Image


PRESENTATION_MAX_AGE_SECONDS = 24 * 60 * 60
MAX_PRESENTATION_FILES_PER_INSTANCE = 2
MAX_PRESENTATION_FILES = 64
MAX_PRESENTATION_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PRESENTATION_FILE_BYTES = 8 * 1024 * 1024
MAX_PRESENTATION_PIXELS = 2_000_000
MAX_PRESENTATION_DIMENSION = 4096

_THEME_MODES = {None, "day", "night"}
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_CACHE_NAME_RE = re.compile(
    r"^(?P<uuid_hash>[0-9a-f]{64})-"
    r"(?P<generation>[1-9][0-9]*)-"
    r"(?P<revision>[1-9][0-9]*)"
    r"(?:-(?P<theme>day|night))?-"
    r"(?P<request_id>[0-9a-f]{32})\.png$"
)
_SAVE_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedPresentationCandidate:
    instance_uuid: str
    structural_generation: int
    settings_revision: int
    theme_mode: str | None
    request_id: str
    cache_path: str


@dataclass(frozen=True)
class _BoundCacheFile:
    fd: int
    file_stat: os.stat_result
    root_stat: os.stat_result
    root_fd: int | None = None


@dataclass(frozen=True)
class _BoundRoot:
    root_stat: os.stat_result
    fd: int | None = None


class PresentationCacheCapacityError(OSError):
    """A prepared artifact cannot be admitted without exceeding a hard cap."""

    def __init__(self, message: str) -> None:
        super().__init__(errno.ENOSPC, message)


def _positive_int(value, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _instance_uuid(value) -> str:
    if not isinstance(value, str):
        raise TypeError("instance_uuid must be a string")
    try:
        UUID(value)
    except (AttributeError, ValueError):
        raise ValueError("instance_uuid must be a valid UUID string") from None
    return value


def _theme_mode(value) -> str | None:
    if value not in _THEME_MODES:
        raise ValueError("theme_mode must be day, night, or None")
    return value


def _request_id(value) -> str:
    if not isinstance(value, str):
        raise TypeError("request_id must be a string")
    if _REQUEST_ID_RE.fullmatch(value) is None:
        raise ValueError("request_id must be exactly 32 lowercase hexadecimal characters")
    return value


def prepared_presentation_path(
    cache_root,
    instance_uuid,
    structural_generation,
    settings_revision,
    theme_mode,
    request_id,
) -> str:
    """Derive the only direct-child pathname allowed for a prepared request."""

    instance_uuid = _instance_uuid(instance_uuid)
    structural_generation = _positive_int(
        structural_generation,
        "structural_generation",
    )
    settings_revision = _positive_int(settings_revision, "settings_revision")
    theme_mode = _theme_mode(theme_mode)
    request_id = _request_id(request_id)
    uuid_hash = hashlib.sha256(instance_uuid.encode("utf-8")).hexdigest()
    theme_suffix = "" if theme_mode is None else f"-{theme_mode}"
    filename = f"{uuid_hash}-{structural_generation}-{settings_revision}{theme_suffix}-{request_id}.png"
    root = Path(os.path.abspath(os.fspath(cache_root)))
    return str(root / filename)


class PresentationCache:
    """Save and decode prepared PNGs without following mutable path aliases."""

    def __init__(self, cache_root):
        self.cache_root = Path(os.path.abspath(os.fspath(cache_root)))

    def save(self, candidate: PreparedPresentationCandidate, image) -> None:
        path = self._candidate_path(candidate)
        if path is None:
            raise ValueError("candidate does not identify its authoritative cache path")
        payload = self._encode_safe_png(image)
        self._ensure_safe_root()

        with _SAVE_LOCK:
            root = self._open_bound_root()
            if root is None:
                raise OSError(errno.ELOOP, "presentation cache root is unsafe", str(self.cache_root))
            try:
                self._check_capacity(path, candidate, len(payload), root)
                self._atomic_publish(path, payload, root)
            finally:
                try:
                    self._close_bound_root(root)
                except OSError as error:
                    _LOGGER.warning(
                        "Prepared presentation root cleanup failed without changing the save result (%s)",
                        error,
                    )

    def validate(self, candidate: PreparedPresentationCandidate) -> bool:
        path = self._candidate_path(candidate)
        if path is None:
            return False
        bound = self._open_bound_cache_file(path)
        if bound is None:
            return False
        copied = None
        try:
            if not self._bound_is_decode_eligible(bound):
                return False
            copied = self._copy_bound_png(bound.fd)
            if copied is None:
                return False
            if not self._descriptor_still_matches_path(path, bound):
                return False
            return self._bound_is_decode_eligible(bound)
        finally:
            if copied is not None:
                copied.close()
            self._close_bound_cache_file(bound)

    def load_image(self, candidate: PreparedPresentationCandidate):
        """Return a fully detached image decoded from one bound file descriptor."""

        path = self._candidate_path(candidate)
        if path is None:
            return None
        bound = self._open_bound_cache_file(path)
        if bound is None:
            return None
        copied = None
        try:
            if not self._bound_is_decode_eligible(bound):
                return None
            copied = self._copy_bound_png(bound.fd)
            if copied is None:
                return None
            if not self._descriptor_still_matches_path(path, bound):
                copied.close()
                copied = None
                return None
            if not self._bound_is_decode_eligible(bound):
                copied.close()
                copied = None
                return None
            result = copied
            copied = None
            return result
        finally:
            if copied is not None:
                copied.close()
            self._close_bound_cache_file(bound)

    def remove(self, candidate: PreparedPresentationCandidate) -> bool:
        """Remove only the exact direct child still matching the bound descriptor."""

        path = self._candidate_path(candidate)
        if path is None:
            return False
        bound = self._open_bound_cache_file(path)
        if bound is None:
            return False
        bound_open = True
        try:
            if not self._descriptor_still_matches_path(path, bound):
                return False
            if bound.root_fd is not None:
                return self._remove_posix(path, bound)
            self._close_bound_cache_file(bound)
            bound_open = False
            return self._remove_fallback(path, bound)
        except (OSError, TypeError, NotImplementedError):
            return False
        finally:
            if bound_open:
                try:
                    self._close_bound_cache_file(bound)
                except OSError as error:
                    _LOGGER.warning(
                        "Prepared presentation descriptor cleanup failed without changing the remove result (%s)",
                        error,
                    )

    def _candidate_path(
        self,
        candidate: PreparedPresentationCandidate,
    ) -> Path | None:
        if not isinstance(candidate, PreparedPresentationCandidate):
            return None
        try:
            expected = prepared_presentation_path(
                self.cache_root,
                candidate.instance_uuid,
                candidate.structural_generation,
                candidate.settings_revision,
                candidate.theme_mode,
                candidate.request_id,
            )
            path = Path(os.path.abspath(os.fspath(candidate.cache_path)))
        except (OSError, TypeError, ValueError):
            return None
        expected_path = Path(os.path.abspath(expected))
        if os.path.normcase(str(path)) != os.path.normcase(str(expected_path)):
            return None
        if path.parent != self.cache_root:
            return None

        match = _CACHE_NAME_RE.fullmatch(path.name)
        if match is None:
            return None
        expected_hash = hashlib.sha256(candidate.instance_uuid.encode("utf-8")).hexdigest()
        if (
            match.group("uuid_hash") != expected_hash
            or int(match.group("generation")) != candidate.structural_generation
            or int(match.group("revision")) != candidate.settings_revision
            or match.group("theme") != candidate.theme_mode
            or match.group("request_id") != candidate.request_id
        ):
            return None
        return path

    def _ensure_safe_root(self) -> None:
        try:
            self.cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root_stat = os.lstat(self.cache_root)
        except OSError:
            raise
        if not self._safe_directory_stat(root_stat):
            raise OSError(errno.ELOOP, "presentation cache root is unsafe", str(self.cache_root))

    def _open_bound_root(self) -> _BoundRoot | None:
        if os.name == "posix":
            root_fd = None
            flags = (
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                root_fd = os.open(os.fspath(self.cache_root), flags)
                root_stat = os.fstat(root_fd)
                path_stat = os.lstat(self.cache_root)
                if (
                    not self._safe_directory_stat(root_stat)
                    or not self._safe_directory_stat(path_stat)
                    or not self._same_identity(root_stat, path_stat)
                ):
                    return None
                result_fd = root_fd
                root_fd = None
                return _BoundRoot(root_stat=root_stat, fd=result_fd)
            except (OSError, TypeError, NotImplementedError):
                return None
            finally:
                if root_fd is not None:
                    os.close(root_fd)

        try:
            root_before = os.lstat(self.cache_root)
            if not self._safe_directory_stat(root_before):
                return None
            root_after = os.lstat(self.cache_root)
            if not self._safe_directory_stat(root_after) or not self._same_identity(root_before, root_after):
                return None
            return _BoundRoot(root_stat=root_before)
        except (OSError, TypeError, NotImplementedError):
            return None

    @staticmethod
    def _close_bound_root(root: _BoundRoot) -> None:
        if root.fd is not None:
            try:
                os.close(root.fd)
            except OSError as error:
                _LOGGER.warning(
                    "Prepared presentation root descriptor close failed for fd %s (%s)",
                    root.fd,
                    error,
                )

    def _root_still_matches(self, root: _BoundRoot) -> bool:
        try:
            path_stat = os.lstat(self.cache_root)
            if not self._safe_directory_stat(path_stat):
                return False
            if root.fd is not None:
                descriptor_stat = os.fstat(root.fd)
                return (
                    self._safe_directory_stat(descriptor_stat)
                    and self._same_identity(root.root_stat, descriptor_stat)
                    and self._same_identity(descriptor_stat, path_stat)
                )
            return self._same_identity(root.root_stat, path_stat)
        except OSError:
            return False

    def _check_capacity(
        self,
        path: Path,
        candidate: PreparedPresentationCandidate,
        payload_size: int,
        root: _BoundRoot,
    ) -> None:
        if payload_size <= 0 or payload_size > MAX_PRESENTATION_FILE_BYTES:
            raise PresentationCacheCapacityError("prepared PNG exceeds the per-file byte cap")
        try:
            names = os.listdir(root.fd if root.fd is not None else self.cache_root)
        except (OSError, TypeError, NotImplementedError) as error:
            raise OSError("could not inspect presentation cache capacity") from error

        total_files = 0
        total_bytes = 0
        instance_files = 0
        target_size = 0
        target_exists = False
        expected_hash = hashlib.sha256(candidate.instance_uuid.encode("utf-8")).hexdigest()

        for name in names:
            try:
                item_stat = self._stat_child(name, root)
            except OSError as error:
                raise OSError("presentation cache contains an unreadable child") from error
            if not self._safe_regular_file_stat(item_stat):
                raise OSError(errno.ELOOP, "presentation cache contains an unsafe child", name)
            total_files += 1
            total_bytes += item_stat.st_size
            match = _CACHE_NAME_RE.fullmatch(name)
            if match is not None and match.group("uuid_hash") == expected_hash:
                instance_files += 1
            if name == path.name:
                target_exists = True
                target_size = item_stat.st_size

        if not self._root_still_matches(root):
            raise OSError(errno.ELOOP, "presentation cache root identity changed")
        prospective_files = total_files + (0 if target_exists else 1)
        prospective_instance = instance_files + (0 if target_exists else 1)
        prospective_bytes = total_bytes - target_size + payload_size
        if prospective_instance > MAX_PRESENTATION_FILES_PER_INSTANCE:
            raise PresentationCacheCapacityError("prepared PNG instance file cap reached")
        if prospective_files > MAX_PRESENTATION_FILES:
            raise PresentationCacheCapacityError("prepared PNG global file cap reached")
        if prospective_bytes > MAX_PRESENTATION_TOTAL_BYTES:
            raise PresentationCacheCapacityError("prepared PNG global byte cap reached")

    def _stat_child(self, name: str, root: _BoundRoot):
        if root.fd is not None:
            return os.stat(name, dir_fd=root.fd, follow_symlinks=False)
        return os.lstat(self.cache_root / name)

    def _atomic_publish(self, path: Path, payload: bytes, root: _BoundRoot) -> None:
        temporary_name = f".{path.name}.{secrets.token_hex(8)}.tmp"
        raw_fd = None
        temporary_created = False
        replaced = False
        try:
            prior = self._optional_child_stat(path.name, root)
            if prior is not None and not self._safe_regular_file_stat(prior):
                raise OSError(errno.ELOOP, "presentation target is unsafe", str(path))
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOINHERIT", 0)
            )
            if root.fd is not None:
                raw_fd = os.open(temporary_name, flags, 0o600, dir_fd=root.fd)
            else:
                raw_fd = os.open(os.fspath(self.cache_root / temporary_name), flags, 0o600)
            temporary_created = True
            if os.name != "nt":
                os.fchmod(raw_fd, 0o600)
            self._write_all(raw_fd, payload)
            os.fsync(raw_fd)
            os.close(raw_fd)
            raw_fd = None

            if not self._root_still_matches(root):
                raise OSError(errno.ELOOP, "presentation cache root identity changed")
            current = self._optional_child_stat(path.name, root)
            if not self._same_optional_snapshot(prior, current):
                raise OSError("presentation target identity changed before publish")
            if root.fd is not None:
                os.replace(
                    temporary_name,
                    path.name,
                    src_dir_fd=root.fd,
                    dst_dir_fd=root.fd,
                )
            else:
                os.replace(self.cache_root / temporary_name, path)
            temporary_created = False
            replaced = True
            self._best_effort_finalize_publish(path, root)
        except BaseException:
            if raw_fd is not None:
                os.close(raw_fd)
            if temporary_created and not replaced:
                self._unlink_child(temporary_name, root)
            raise

    def _best_effort_finalize_publish(self, path: Path, root: _BoundRoot) -> None:
        try:
            root_matches = self._root_still_matches(root)
        except Exception as error:
            _LOGGER.warning(
                "Prepared presentation publish committed but root identity recheck failed: %s",
                path,
                exc_info=error,
            )
        else:
            if not root_matches:
                _LOGGER.warning(
                    "Prepared presentation publish committed but root identity changed: %s",
                    path,
                )
        if root.fd is None:
            return
        try:
            os.fsync(root.fd)
        except Exception as error:
            _LOGGER.warning(
                "Prepared presentation publish committed but directory fsync failed: %s",
                path,
                exc_info=error,
            )

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(fd, view[offset:])
            if written <= 0:
                raise OSError("temporary presentation write was incomplete")
            offset += written

    def _optional_child_stat(self, name: str, root: _BoundRoot):
        try:
            return self._stat_child(name, root)
        except FileNotFoundError:
            return None

    @classmethod
    def _same_optional_snapshot(cls, left, right) -> bool:
        if left is None or right is None:
            return left is right
        return cls._same_file_snapshot(left, right)

    def _unlink_child(self, name: str, root: _BoundRoot) -> None:
        try:
            if root.fd is not None:
                os.unlink(name, dir_fd=root.fd)
            else:
                os.unlink(self.cache_root / name)
        except FileNotFoundError:
            pass

    def _open_bound_cache_file(self, path: Path) -> _BoundCacheFile | None:
        if os.name == "posix":
            return self._open_bound_cache_file_posix(path)
        return self._open_bound_cache_file_fallback(path)

    def _open_bound_cache_file_posix(
        self,
        path: Path,
    ) -> _BoundCacheFile | None:
        root_fd = None
        file_fd = None
        root_flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )
        file_flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
        )
        try:
            root_fd = os.open(os.fspath(self.cache_root), root_flags)
            root_stat = os.fstat(root_fd)
            root_path_stat = os.lstat(self.cache_root)
            if (
                not self._safe_directory_stat(root_stat)
                or not self._safe_directory_stat(root_path_stat)
                or not self._same_identity(root_stat, root_path_stat)
            ):
                return None
            file_fd = os.open(path.name, file_flags, dir_fd=root_fd)
            file_stat = os.fstat(file_fd)
            path_stat = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
            if (
                not self._safe_regular_file_stat(file_stat)
                or not self._safe_regular_file_stat(path_stat)
                or not self._same_file_snapshot(file_stat, path_stat)
            ):
                return None
            result_fd = file_fd
            result_root_fd = root_fd
            file_fd = None
            root_fd = None
            return _BoundCacheFile(
                fd=result_fd,
                file_stat=file_stat,
                root_stat=root_stat,
                root_fd=result_root_fd,
            )
        except (OSError, TypeError, NotImplementedError):
            return None
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if root_fd is not None:
                os.close(root_fd)

    def _open_bound_cache_file_fallback(
        self,
        path: Path,
    ) -> _BoundCacheFile | None:
        file_fd = None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        try:
            root_before = os.lstat(self.cache_root)
            path_before = os.lstat(path)
            if not self._safe_directory_stat(root_before) or not self._safe_regular_file_stat(path_before):
                return None
            file_fd = os.open(os.fspath(path), flags)
            file_stat = os.fstat(file_fd)
            path_after = os.lstat(path)
            root_after = os.lstat(self.cache_root)
            if (
                not self._safe_regular_file_stat(file_stat)
                or not self._safe_regular_file_stat(path_after)
                or not self._safe_directory_stat(root_after)
                or not self._same_identity(root_before, root_after)
                or not self._same_file_snapshot(path_before, file_stat)
                or not self._same_file_snapshot(file_stat, path_after)
            ):
                return None
            result_fd = file_fd
            file_fd = None
            return _BoundCacheFile(result_fd, file_stat, root_before)
        except (OSError, TypeError, NotImplementedError):
            return None
        finally:
            if file_fd is not None:
                os.close(file_fd)

    @staticmethod
    def _close_bound_cache_file(bound: _BoundCacheFile) -> None:
        if bound.fd >= 0:
            try:
                os.close(bound.fd)
            except OSError as error:
                _LOGGER.warning(
                    "Prepared presentation file descriptor close failed for fd %s (%s)",
                    bound.fd,
                    error,
                )
        if bound.root_fd is not None:
            try:
                os.close(bound.root_fd)
            except OSError as error:
                _LOGGER.warning(
                    "Prepared presentation root descriptor close failed for fd %s (%s)",
                    bound.root_fd,
                    error,
                )

    def _descriptor_still_matches_path(
        self,
        path: Path,
        bound: _BoundCacheFile,
    ) -> bool:
        try:
            final_file_stat = os.fstat(bound.fd)
            if bound.root_fd is not None:
                final_path_stat = os.stat(
                    path.name,
                    dir_fd=bound.root_fd,
                    follow_symlinks=False,
                )
                final_root_descriptor_stat = os.fstat(bound.root_fd)
            else:
                final_path_stat = os.lstat(path)
                final_root_descriptor_stat = bound.root_stat
            final_root_path_stat = os.lstat(self.cache_root)
        except OSError:
            return False
        return (
            self._safe_regular_file_stat(final_file_stat)
            and self._safe_regular_file_stat(final_path_stat)
            and self._safe_directory_stat(final_root_descriptor_stat)
            and self._safe_directory_stat(final_root_path_stat)
            and self._same_file_snapshot(bound.file_stat, final_file_stat)
            and self._same_file_snapshot(final_file_stat, final_path_stat)
            and self._same_identity(bound.root_stat, final_root_descriptor_stat)
            and self._same_identity(final_root_descriptor_stat, final_root_path_stat)
        )

    @staticmethod
    def _bound_is_decode_eligible(bound: _BoundCacheFile) -> bool:
        file_stat = bound.file_stat
        if file_stat.st_size <= 0 or file_stat.st_size > MAX_PRESENTATION_FILE_BYTES:
            return False
        age_ns = time.time_ns() - file_stat.st_mtime_ns
        return 0 <= age_ns <= PRESENTATION_MAX_AGE_SECONDS * 1_000_000_000

    @classmethod
    def _encode_safe_png(cls, image) -> bytes:
        if not isinstance(image, Image.Image):
            raise TypeError("image must be a Pillow Image")
        cls._validate_image_dimensions(image)
        copied = None
        try:
            image.load()
            copied = image.copy()
            copied.load()
            buffer = io.BytesIO()
            copied.save(buffer, format="PNG")
            payload = buffer.getvalue()
        except Exception as error:
            raise ValueError("image could not be encoded as PNG") from error
        finally:
            if copied is not None:
                copied.close()
        if len(payload) > MAX_PRESENTATION_FILE_BYTES:
            raise PresentationCacheCapacityError("prepared PNG exceeds the per-file byte cap")
        verified = cls._copy_png_stream(io.BytesIO(payload))
        if verified is None:
            raise ValueError("encoded presentation PNG failed verification")
        verified.close()
        return payload

    @classmethod
    def _copy_bound_png(cls, fd: int):
        duplicate_fd = None
        try:
            duplicate_fd = os.dup(fd)
            stream = os.fdopen(duplicate_fd, "rb")
            duplicate_fd = None
            with stream:
                return cls._copy_png_stream(stream)
        except Exception:
            return None
        finally:
            if duplicate_fd is not None:
                os.close(duplicate_fd)

    @classmethod
    def _copy_png_stream(cls, stream):
        copied = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(stream) as verifier:
                    cls._validate_opened_png(verifier)
                    verifier.verify()
                stream.seek(0)
                with Image.open(stream) as source:
                    cls._validate_opened_png(source)
                    source.load()
                    copied = source.copy()
                copied.load()
                return copied
        except Exception:
            if copied is not None:
                copied.close()
            return None

    @classmethod
    def _validate_opened_png(cls, image) -> None:
        if str(image.format or "").upper() != "PNG":
            raise ValueError("prepared image is not PNG")
        cls._validate_image_dimensions(image)

    @staticmethod
    def _validate_image_dimensions(image) -> None:
        width, height = image.size
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or width <= 0
            or height <= 0
            or width > MAX_PRESENTATION_DIMENSION
            or height > MAX_PRESENTATION_DIMENSION
            or width * height > MAX_PRESENTATION_PIXELS
        ):
            raise ValueError("prepared image exceeds safe dimensions")

    def _remove_posix(self, path: Path, bound: _BoundCacheFile) -> bool:
        root = _BoundRoot(bound.root_stat, bound.root_fd)
        tombstone = f".{path.name}.{secrets.token_hex(8)}.remove"
        quarantined = False
        try:
            os.replace(
                path.name,
                tombstone,
                src_dir_fd=bound.root_fd,
                dst_dir_fd=bound.root_fd,
            )
            quarantined = True
            moved_stat = os.stat(
                tombstone,
                dir_fd=bound.root_fd,
                follow_symlinks=False,
            )
            if not self._same_file_snapshot(bound.file_stat, moved_stat) or not self._root_still_matches(root):
                self._restore_or_report_child(tombstone, path.name, root)
                return False
            os.unlink(tombstone, dir_fd=bound.root_fd)
        except BaseException:
            if quarantined:
                self._restore_or_report_child(tombstone, path.name, root)
            raise
        try:
            os.fsync(bound.root_fd)
        except OSError as error:
            _LOGGER.warning(
                "Prepared presentation removal committed but directory fsync failed: %s",
                path,
                exc_info=error,
            )
        return True

    def _remove_fallback(self, path: Path, bound: _BoundCacheFile) -> bool:
        # Windows generally cannot rename a file while the CRT descriptor is
        # open.  Close only after the final descriptor/path/root comparison,
        # then verify the identity again after the atomic quarantine rename.
        tombstone = self.cache_root / f".{path.name}.{secrets.token_hex(8)}.remove"
        quarantined = False
        try:
            os.replace(path, tombstone)
            quarantined = True
            moved_stat = os.lstat(tombstone)
            if not self._same_file_snapshot(bound.file_stat, moved_stat):
                self._restore_or_report_path(tombstone, path)
                return False
            root_after = os.lstat(self.cache_root)
            if not self._safe_directory_stat(root_after) or not self._same_identity(bound.root_stat, root_after):
                self._restore_or_report_path(tombstone, path)
                return False
            os.unlink(tombstone)
        except BaseException:
            if quarantined:
                self._restore_or_report_path(tombstone, path)
            raise
        return True

    def _restore_quarantined_child(
        self,
        tombstone: str,
        destination: str,
        root: _BoundRoot,
    ) -> bool:
        try:
            os.link(
                tombstone,
                destination,
                src_dir_fd=root.fd,
                dst_dir_fd=root.fd,
                follow_symlinks=False,
            )
        except OSError:
            return False
        try:
            self._unlink_child(tombstone, root)
        except OSError:
            return False
        return True

    @staticmethod
    def _restore_quarantined_path(tombstone: Path, destination: Path) -> bool:
        try:
            os.link(tombstone, destination, follow_symlinks=False)
        except OSError:
            return False
        try:
            os.unlink(tombstone)
        except OSError:
            return False
        return True

    def _restore_or_report_child(
        self,
        tombstone: str,
        destination: str,
        root: _BoundRoot,
    ) -> bool:
        restored = self._restore_quarantined_child(tombstone, destination, root)
        if not restored:
            self._report_retained_tombstone(self.cache_root / tombstone)
        return restored

    def _restore_or_report_path(self, tombstone: Path, destination: Path) -> bool:
        restored = self._restore_quarantined_path(tombstone, destination)
        if not restored:
            self._report_retained_tombstone(tombstone)
        return restored

    @staticmethod
    def _report_retained_tombstone(tombstone: Path) -> None:
        _LOGGER.error(
            "Prepared presentation canonical path could not be restored; tombstone retained for recovery: %s",
            tombstone,
        )

    @classmethod
    def _safe_regular_file_stat(cls, value) -> bool:
        return stat_module.S_ISREG(value.st_mode) and not cls._is_link_like(value)

    @classmethod
    def _safe_directory_stat(cls, value) -> bool:
        return stat_module.S_ISDIR(value.st_mode) and not cls._is_link_like(value)

    @staticmethod
    def _is_link_like(value) -> bool:
        reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        attributes = getattr(value, "st_file_attributes", 0)
        return stat_module.S_ISLNK(value.st_mode) or bool(reparse_flag and attributes & reparse_flag)

    @staticmethod
    def _same_identity(left, right) -> bool:
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

    @classmethod
    def _same_file_snapshot(cls, left, right) -> bool:
        return (
            cls._same_identity(left, right) and left.st_size == right.st_size and left.st_mtime_ns == right.st_mtime_ns
        )
