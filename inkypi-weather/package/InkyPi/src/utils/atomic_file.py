"""Strict same-directory atomic file writes with explicit durability failures."""

from __future__ import annotations

import errno
import io
import json
import operator
import os
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, TypeAlias


Pathish: TypeAlias = str | os.PathLike[str]
AtomicWriteStage: TypeAlias = Literal[
    "create_temp",
    "set_mode",
    "open_temp",
    "write",
    "file_fsync",
    "file_close",
    "replace",
    "directory_fsync",
]

_WINDOWS = os.name == "nt"


class SupportsImageSave(Protocol):
    """The subset of the Pillow image API needed by :func:`atomic_write_image`."""

    def save(self, fp: BinaryIO, *, format: str) -> object: ...


class AtomicWriteError(OSError):
    """A failure before the target was replaced."""

    def __init__(self, target: Path, stage: AtomicWriteStage) -> None:
        self.target = target
        self.stage = stage
        self.target_replaced = False
        super().__init__(f"atomic write failed during {stage} for {target}")


class AtomicCommitUncertainError(AtomicWriteError):
    """The target was replaced, but parent-directory durability is unknown."""

    def __init__(self, target: Path) -> None:
        self.target = target
        self.stage: AtomicWriteStage = "directory_fsync"
        self.target_replaced = True
        OSError.__init__(self, f"atomic write commit durability is uncertain for {target}")


def fsync_directory(directory: Pathish) -> None:
    """Synchronize a directory entry on POSIX and explicitly do nothing on Windows."""

    if _WINDOWS:
        return

    directory_path = Path(directory)
    flags = os.O_RDONLY | os.O_DIRECTORY
    directory_fd = os.open(directory_path, flags)
    try:
        os.fsync(directory_fd)
    except BaseException as primary:
        try:
            os.close(directory_fd)
        except BaseException as close_error:
            primary.add_note(f"directory descriptor close failed for {directory_path} ({type(close_error).__name__})")
        raise
    else:
        os.close(directory_fd)


def atomic_write_bytes(path: Pathish, payload: bytes, *, mode: int = 0o600) -> None:
    """Atomically publish bytes at *path* without ever truncating the old target."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    normalized_mode = _validate_mode(mode)
    target, parent = _validate_target(path)
    _atomic_write_encoded(target, parent, payload, normalized_mode)


def atomic_write_json(path: Pathish, payload: object, *, mode: int = 0o600) -> None:
    """Encode strict UTF-8 JSON in memory, then atomically publish it."""

    _validate_json_mapping_keys(payload)
    encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    atomic_write_bytes(path, encoded, mode=mode)


def atomic_write_image(
    path: Pathish,
    image: SupportsImageSave,
    *,
    image_format: str = "PNG",
    mode: int = 0o600,
) -> None:
    """Encode an image in memory, then atomically publish it."""

    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    atomic_write_bytes(path, buffer.getvalue(), mode=mode)


def _validate_mode(mode: int) -> int:
    try:
        normalized = operator.index(mode)
    except TypeError:
        raise TypeError("mode must be an integer") from None
    if normalized < 0 or normalized > 0o7777:
        raise ValueError("mode must be between 0 and 0o7777")
    return normalized


def _validate_json_mapping_keys(value: object, seen: set[int] | None = None) -> None:
    """Reject keys that ``json.dumps`` would otherwise coerce to strings."""
    if seen is None:
        seen = set()
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            _validate_json_mapping_keys(item, seen)
    elif isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for item in value:
            _validate_json_mapping_keys(item, seen)


def _validate_target(path: Pathish) -> tuple[Path, Path]:
    target = Path(path)
    parent = target.parent
    parent_stat = parent.stat()
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise NotADirectoryError(errno.ENOTDIR, "target parent is not a directory", str(parent))
    return target, parent


def _atomic_write_encoded(target: Path, parent: Path, payload: bytes, mode: int) -> None:
    raw_fd: int | None = None
    stream: BinaryIO | None = None
    temp_path: Path | None = None
    target_replaced = False
    stage: AtomicWriteStage = "create_temp"

    try:
        raw_fd, temp_name = tempfile.mkstemp(
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)

        if not _WINDOWS:
            stage = "set_mode"
            os.fchmod(raw_fd, mode)

        stage = "open_temp"
        stream = os.fdopen(raw_fd, "wb")
        raw_fd = None

        stage = "write"
        written = stream.write(payload)
        if written != len(payload):
            raise OSError("temporary file write was incomplete")
        stream.flush()

        stage = "file_fsync"
        os.fsync(stream.fileno())

        stage = "file_close"
        stream.close()
        stream = None

        stage = "replace"
        os.replace(temp_path, target)
        temp_path, target_replaced = None, True

        stage = "directory_fsync"
        fsync_directory(parent)
    except BaseException as primary:
        exposed = _exposed_failure(target, stage, target_replaced, primary)
        if not target_replaced:
            _cleanup_pre_replace(raw_fd, stream, temp_path, exposed)
        if exposed is primary:
            raise
        raise exposed from primary


def _exposed_failure(
    target: Path,
    stage: AtomicWriteStage,
    target_replaced: bool,
    primary: BaseException,
) -> BaseException:
    if not isinstance(primary, Exception):
        return primary
    if target_replaced:
        return AtomicCommitUncertainError(target)
    return AtomicWriteError(target, stage)


def _cleanup_pre_replace(
    raw_fd: int | None,
    stream: BinaryIO | None,
    temp_path: Path | None,
    primary: BaseException,
) -> None:
    if stream is not None:
        try:
            stream.close()
        except BaseException as close_error:
            _add_cleanup_note(primary, "temporary stream descriptor cleanup", temp_path, close_error)
    elif raw_fd is not None:
        try:
            os.close(raw_fd)
        except BaseException as close_error:
            _add_cleanup_note(primary, "temporary raw descriptor cleanup", temp_path, close_error)

    if temp_path is not None:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        except BaseException as unlink_error:
            primary.add_note(
                f"temporary file cleanup failed; residual temp path: {temp_path} ({type(unlink_error).__name__})"
            )


def _add_cleanup_note(
    primary: BaseException,
    operation: str,
    temp_path: Path | None,
    cleanup_error: BaseException,
) -> None:
    location = f" for {temp_path}" if temp_path is not None else ""
    primary.add_note(f"{operation} failed{location} ({type(cleanup_error).__name__})")
