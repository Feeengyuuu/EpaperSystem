"""Authoritative, exact-revision display-cache discovery and validation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat as stat_module
import threading

from PIL import Image


_THEME_MODES = {None, "day", "night"}
_CACHE_NAME_RE = re.compile(
    r"^(?P<prefix>[0-9a-f]{32})-"
    r"(?P<generation>[1-9][0-9]*)-"
    r"(?P<revision>[1-9][0-9]*)"
    r"(?:-(?P<theme>day|night))?\.png$"
)


@dataclass(frozen=True)
class CachePathIdentity:
    uuid_hash_prefix: str
    structural_generation: int
    settings_revision: int
    theme_mode: str | None


@dataclass(frozen=True)
class DisplayCacheCandidate:
    instance_uuid: str
    structural_generation: int
    settings_revision: int
    theme_mode: str | None
    cache_path: str
    promoted_at: str | None


@dataclass(frozen=True)
class _BoundCacheFile:
    fd: int
    file_stat: os.stat_result
    root_stat: os.stat_result


def _positive_int(value, field_name) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _instance_uuid(value) -> str:
    if not isinstance(value, str):
        raise TypeError("instance_uuid must be a string")
    value = value.strip()
    if not value:
        raise ValueError("instance_uuid must not be empty")
    return value


def _theme_mode(value) -> str | None:
    if value not in _THEME_MODES:
        raise ValueError("theme_mode must be day, night, or None")
    return value


def cache_identity_prefix(instance_uuid) -> str:
    """Return the shared authoritative cache prefix for one instance UUID."""

    instance_uuid = _instance_uuid(instance_uuid)
    return hashlib.sha256(instance_uuid.encode("utf-8")).hexdigest()[:32]


def parse_authoritative_cache_filename(filename):
    """Parse one direct-child authoritative filename without touching disk."""

    try:
        filename = os.fspath(filename)
    except TypeError:
        return None
    if not isinstance(filename, str) or os.path.basename(filename) != filename:
        return None
    match = _CACHE_NAME_RE.fullmatch(filename)
    if match is None:
        return None
    return CachePathIdentity(
        uuid_hash_prefix=match.group("prefix"),
        structural_generation=int(match.group("generation")),
        settings_revision=int(match.group("revision")),
        theme_mode=match.group("theme"),
    )


def authoritative_cache_path(
    cache_root,
    instance_uuid,
    structural_generation,
    settings_revision,
    theme_mode=None,
) -> str:
    """Derive the sole authoritative filename for one immutable revision."""

    instance_uuid = _instance_uuid(instance_uuid)
    structural_generation = _positive_int(
        structural_generation,
        "structural_generation",
    )
    settings_revision = _positive_int(settings_revision, "settings_revision")
    theme_mode = _theme_mode(theme_mode)
    prefix = cache_identity_prefix(instance_uuid)
    suffix = "" if theme_mode is None else f"-{theme_mode}"
    filename = (
        f"{prefix}-{structural_generation}-{settings_revision}{suffix}.png"
    )
    return str(Path(cache_root) / filename)


def parse_authoritative_cache_path(cache_root, cache_path):
    """Parse a direct child cache path, rejecting aliases and path traversal."""

    root = Path(os.path.abspath(os.fspath(cache_root)))
    path = Path(os.path.abspath(os.fspath(cache_path)))
    if path.parent != root or path.is_symlink():
        return None
    return parse_authoritative_cache_filename(path.name)


class CacheCatalog:
    """Resolve and decode-check displayable caches without plugin execution."""

    def __init__(self, cache_root):
        self.cache_root = Path(os.path.abspath(os.fspath(cache_root)))
        self._validation_cache: dict[tuple[str, int, int], bool] = {}
        self._validation_lock = threading.Lock()

    def resolve_exact(
        self,
        instance,
        resolved_theme_mode,
        runtime_instance_state,
    ) -> DisplayCacheCandidate | None:
        """Resolve only the exact current-theme cache for one immutable revision."""
        resolved_theme_mode = _theme_mode(resolved_theme_mode)
        try:
            instance_uuid = _instance_uuid(instance.instance_uuid)
            generation = _positive_int(
                instance.structural_generation,
                "structural_generation",
            )
            revision = _positive_int(
                instance.settings_revision,
                "settings_revision",
            )
        except (AttributeError, TypeError, ValueError):
            return None

        last_good = getattr(runtime_instance_state, "last_good_cache", None)
        promoted_at = None
        if (
            last_good is not None
            and last_good.structural_generation == generation
            and last_good.settings_revision == revision
            and last_good.theme_mode == resolved_theme_mode
        ):
            promoted_at = last_good.promoted_at
        candidate = DisplayCacheCandidate(
            instance_uuid=instance_uuid,
            structural_generation=generation,
            settings_revision=revision,
            theme_mode=resolved_theme_mode,
            cache_path=authoritative_cache_path(
                self.cache_root,
                instance_uuid,
                generation,
                revision,
                resolved_theme_mode,
            ),
            promoted_at=promoted_at,
        )
        return candidate if self.validate(candidate) else None

    def resolve(
        self,
        instance,
        resolved_theme_mode,
        runtime_instance_state,
    ) -> DisplayCacheCandidate | None:
        resolved_theme_mode = _theme_mode(resolved_theme_mode)
        try:
            instance_uuid = _instance_uuid(instance.instance_uuid)
            generation = _positive_int(
                instance.structural_generation,
                "structural_generation",
            )
            revision = _positive_int(
                instance.settings_revision,
                "settings_revision",
            )
        except (AttributeError, TypeError, ValueError):
            return None

        last_good = getattr(runtime_instance_state, "last_good_cache", None)
        choices: list[tuple[str | None, str | None]] = []
        current_promoted_at = None
        if (
            last_good is not None
            and last_good.structural_generation == generation
            and last_good.settings_revision == revision
            and last_good.theme_mode == resolved_theme_mode
        ):
            current_promoted_at = last_good.promoted_at
        choices.append((resolved_theme_mode, current_promoted_at))

        if (
            last_good is not None
            and last_good.structural_generation == generation
            and last_good.settings_revision == revision
        ):
            choices.append((last_good.theme_mode, last_good.promoted_at))

        # An unsuffixed exact cache is the only migration cache eligible for
        # display.  A legacy success timestamp carries no exact identity and is
        # deliberately not promoted to LastGoodCacheState here.
        choices.append((None, None))

        seen_paths = set()
        for theme_mode, promoted_at in choices:
            cache_path = authoritative_cache_path(
                self.cache_root,
                instance_uuid,
                generation,
                revision,
                theme_mode,
            )
            normalized_path = os.path.normcase(os.path.abspath(cache_path))
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            candidate = DisplayCacheCandidate(
                instance_uuid=instance_uuid,
                structural_generation=generation,
                settings_revision=revision,
                theme_mode=theme_mode,
                cache_path=cache_path,
                promoted_at=promoted_at,
            )
            if self.validate(candidate):
                return candidate
        return None

    def validate(self, candidate: DisplayCacheCandidate) -> bool:
        path = self._candidate_path(candidate)
        if path is None:
            return False

        bound = self._open_bound_cache_file(path)
        if bound is None:
            return False
        try:
            cache_key = (
                str(path),
                bound.file_stat.st_mtime_ns,
                bound.file_stat.st_size,
            )
            with self._validation_lock:
                cached = self._validation_cache.get(cache_key)
            if cached is not None:
                if not cached:
                    return False
                return self._descriptor_still_matches_path(path, bound)

            valid = self._decode_bound_png(bound.fd)
            if not self._descriptor_still_matches_path(path, bound):
                return False

            self._record_validation(path, cache_key, valid)
            return valid
        finally:
            os.close(bound.fd)

    def load_image(self, candidate: DisplayCacheCandidate):
        """Decode and return a copy from the same validated, bound file descriptor."""
        path = self._candidate_path(candidate)
        if path is None:
            return None
        bound = self._open_bound_cache_file(path)
        if bound is None:
            return None
        image = None
        try:
            image = self._copy_bound_png(bound.fd)
            if image is None:
                return None
            if not self._descriptor_still_matches_path(path, bound):
                image.close()
                return None
            cache_key = (
                str(path),
                bound.file_stat.st_mtime_ns,
                bound.file_stat.st_size,
            )
            self._record_validation(path, cache_key, True)
            return image
        finally:
            os.close(bound.fd)

    def _candidate_path(self, candidate: DisplayCacheCandidate) -> Path | None:
        if not isinstance(candidate, DisplayCacheCandidate):
            return None
        try:
            expected = authoritative_cache_path(
                self.cache_root,
                candidate.instance_uuid,
                candidate.structural_generation,
                candidate.settings_revision,
                candidate.theme_mode,
            )
        except (TypeError, ValueError):
            return None

        path = Path(os.path.abspath(os.fspath(candidate.cache_path)))
        expected_path = Path(os.path.abspath(expected))
        if os.path.normcase(str(path)) != os.path.normcase(str(expected_path)):
            return None
        identity = parse_authoritative_cache_path(self.cache_root, path)
        if identity is None:
            return None
        expected_prefix = cache_identity_prefix(candidate.instance_uuid)
        if identity != CachePathIdentity(
            uuid_hash_prefix=expected_prefix,
            structural_generation=candidate.structural_generation,
            settings_revision=candidate.settings_revision,
            theme_mode=candidate.theme_mode,
        ):
            return None
        return path

    def _record_validation(self, path: Path, cache_key, valid: bool) -> None:
        with self._validation_lock:
            self._validation_cache = {
                key: value
                for key, value in self._validation_cache.items()
                if key[0] != str(path)
            }
            self._validation_cache[cache_key] = bool(valid)

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
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
        )
        try:
            root_fd = os.open(os.fspath(self.cache_root), root_flags)
            root_stat = os.fstat(root_fd)
            if not stat_module.S_ISDIR(root_stat.st_mode):
                return None
            file_fd = os.open(path.name, file_flags, dir_fd=root_fd)
            file_stat = os.fstat(file_fd)
            path_stat = os.stat(
                path.name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if (
                not self._safe_regular_file_stat(file_stat)
                or not self._safe_regular_file_stat(path_stat)
                or not self._same_file_snapshot(file_stat, path_stat)
            ):
                return None
            result_fd = file_fd
            file_fd = None
            return _BoundCacheFile(result_fd, file_stat, root_stat)
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
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0)
        )
        try:
            root_before = os.lstat(self.cache_root)
            path_before = os.lstat(path)
            if (
                not self._safe_directory_stat(root_before)
                or not self._safe_regular_file_stat(path_before)
            ):
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

    def _descriptor_still_matches_path(
        self,
        path: Path,
        bound: _BoundCacheFile,
    ) -> bool:
        try:
            final_file_stat = os.fstat(bound.fd)
            final_path_stat = os.lstat(path)
            final_root_stat = os.lstat(self.cache_root)
        except OSError:
            return False
        return (
            self._safe_regular_file_stat(final_file_stat)
            and self._safe_regular_file_stat(final_path_stat)
            and self._safe_directory_stat(final_root_stat)
            and self._same_file_snapshot(bound.file_stat, final_file_stat)
            and self._same_file_snapshot(final_file_stat, final_path_stat)
            and self._same_identity(bound.root_stat, final_root_stat)
        )

    @staticmethod
    def _decode_bound_png(fd: int) -> bool:
        copied = CacheCatalog._copy_bound_png(fd)
        if copied is None:
            return False
        copied.close()
        return True

    @staticmethod
    def _copy_bound_png(fd: int):
        duplicate_fd = None
        copied = None
        try:
            duplicate_fd = os.dup(fd)
            stream = os.fdopen(duplicate_fd, "rb")
            duplicate_fd = None
            with stream:
                with Image.open(stream) as source:
                    copied = source.copy()
                copied.load()
                return copied
        except Exception:
            if copied is not None:
                copied.close()
            return None
        finally:
            if duplicate_fd is not None:
                os.close(duplicate_fd)

    @classmethod
    def _safe_regular_file_stat(cls, value) -> bool:
        return stat_module.S_ISREG(value.st_mode) and not cls._is_link_like(value)

    @classmethod
    def _safe_directory_stat(cls, value) -> bool:
        return stat_module.S_ISDIR(value.st_mode) and not cls._is_link_like(value)

    @staticmethod
    def _is_link_like(value) -> bool:
        reparse_flag = getattr(
            stat_module,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0,
        )
        attributes = getattr(value, "st_file_attributes", 0)
        return stat_module.S_ISLNK(value.st_mode) or bool(
            reparse_flag and attributes & reparse_flag
        )

    @staticmethod
    def _same_identity(left, right) -> bool:
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

    @classmethod
    def _same_file_snapshot(cls, left, right) -> bool:
        return (
            cls._same_identity(left, right)
            and left.st_size == right.st_size
            and left.st_mtime_ns == right.st_mtime_ns
        )

    def invalidate(self, candidate: DisplayCacheCandidate) -> None:
        try:
            path = str(Path(os.path.abspath(os.fspath(candidate.cache_path))))
        except (AttributeError, TypeError, ValueError):
            return
        with self._validation_lock:
            self._validation_cache = {
                key: value
                for key, value in self._validation_cache.items()
                if key[0] != path
            }
