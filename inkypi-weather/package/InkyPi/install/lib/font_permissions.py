#!/usr/bin/env python3
"""Normalize durable font permissions without following mutable paths."""

from __future__ import annotations

try:
    import grp
except ImportError:  # pragma: no cover - grp is available on production Linux.
    grp = None
import os
from pathlib import Path
import stat
import sys


DIRECTORY_MODE = 0o750
FONT_MODE = 0o640


class FontPermissionError(RuntimeError):
    """Durable font permissions could not be normalized safely."""


def _identity(metadata) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _require_same_directory(reference, candidate) -> None:
    if _identity(reference) != _identity(candidate):
        raise FontPermissionError("durable font directory changed during normalization")
    if not stat.S_ISDIR(candidate.st_mode):
        raise FontPermissionError("durable font path is not a directory")


def _member_open_flags() -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    return flags


def _directory_open_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute():
        raise FontPermissionError("durable data directory must be absolute")
    try:
        directory_fd = os.open(os.sep, _directory_open_flags())
    except OSError as error:
        raise FontPermissionError("filesystem root cannot be opened safely") from error

    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise FontPermissionError(
                    "durable data directory contains an unsafe path component"
                )
            try:
                next_fd = os.open(
                    component,
                    _directory_open_flags(),
                    dir_fd=directory_fd,
                )
            except OSError as error:
                raise FontPermissionError(
                    "durable data directory component is a symbolic link "
                    f"or cannot be opened safely: {component}"
                ) from error
            os.close(directory_fd)
            directory_fd = next_fd
    except Exception:
        os.close(directory_fd)
        raise
    return directory_fd


def _normalize_member(directory_fd: int, name: str, *, uid: int, gid: int) -> None:
    try:
        member_fd = os.open(name, _member_open_flags(), dir_fd=directory_fd)
    except OSError as error:
        raise FontPermissionError(
            f"durable font member is a symbolic link or cannot be opened safely: {name}"
        ) from error
    try:
        metadata = os.fstat(member_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise FontPermissionError(
                f"durable font member is not a regular file: {name}"
            )
        os.fchown(member_fd, uid, gid)
        os.fchmod(member_fd, FONT_MODE)
    except OSError as error:
        raise FontPermissionError(
            f"durable font member permissions could not be normalized: {name}"
        ) from error
    finally:
        os.close(member_fd)


def normalize_font_permissions(
    data_dir: str | os.PathLike[str],
    *,
    uid: int = 0,
    gid: int,
) -> None:
    """Create and normalize DATA_DIR/fonts using only verified descriptors."""

    required = ("O_DIRECTORY", "O_NOFOLLOW", "fchmod", "fchown")
    if any(not hasattr(os, name) for name in required):
        raise FontPermissionError("fd-based font permissions require POSIX support")

    path = Path(data_dir)
    data_fd = _open_absolute_directory(path)

    try:
        opened_data = os.fstat(data_fd)
        try:
            os.mkdir("fonts", mode=DIRECTORY_MODE, dir_fd=data_fd)
        except FileExistsError:
            pass

        try:
            fonts_fd = os.open("fonts", _directory_open_flags(), dir_fd=data_fd)
        except OSError as error:
            raise FontPermissionError(
                "durable font directory is a symbolic link or cannot be opened safely"
            ) from error

        try:
            opened_fonts = os.fstat(fonts_fd)
            os.fchown(fonts_fd, int(uid), int(gid))
            os.fchmod(fonts_fd, DIRECTORY_MODE)

            for name in sorted(os.listdir(fonts_fd)):
                _normalize_member(fonts_fd, name, uid=int(uid), gid=int(gid))

            after_fonts_fd = os.fstat(fonts_fd)
            after_fonts_path = os.stat(
                "fonts", dir_fd=data_fd, follow_symlinks=False
            )
            _require_same_directory(opened_fonts, after_fonts_fd)
            _require_same_directory(opened_fonts, after_fonts_path)
        finally:
            os.close(fonts_fd)

        reopened_data_fd = _open_absolute_directory(path)
        try:
            _require_same_directory(opened_data, os.fstat(data_fd))
            _require_same_directory(opened_data, os.fstat(reopened_data_fd))
        finally:
            os.close(reopened_data_fd)
    except FontPermissionError:
        raise
    except OSError as error:
        raise FontPermissionError(
            "durable font permissions could not be normalized safely"
        ) from error
    finally:
        os.close(data_fd)


def _inkypi_gid() -> int:
    if grp is None:
        raise FontPermissionError("the inkypi group can only be resolved on POSIX")
    try:
        return int(grp.getgrnam("inkypi").gr_gid)
    except KeyError as error:
        raise FontPermissionError("the inkypi group does not exist") from error


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("usage: font_permissions.py DATA_DIR")
    normalize_font_permissions(args[0], uid=0, gid=_inkypi_gid())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FontPermissionError as error:
        raise SystemExit(f"font permission error: {error}") from error
