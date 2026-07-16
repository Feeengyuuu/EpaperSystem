#!/usr/bin/env python3
"""Restore the fixed runtime env ownership contract without following links."""

from __future__ import annotations

import errno
import grp
import os
import pwd
import stat


ENV_DIRECTORY = "/etc/inkypi"
ENV_NAME = "inkypi.env"
SERVICE_USER = "inkypi"
SERVICE_GROUP = "inkypi"
MAX_ENV_BYTES = 1024 * 1024


def _open_runtime_directory(path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_fd = os.open(path, flags)
    metadata = os.fstat(directory_fd)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(directory_fd)
        raise RuntimeError("runtime env parent is not a directory")
    return directory_fd


def _open_or_create_env(directory_fd, name):
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        return os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        if error.errno != errno.ENOENT:
            raise
    return os.open(
        name,
        flags | os.O_CREAT | os.O_EXCL,
        0o600,
        dir_fd=directory_fd,
    )


def repair_runtime_env_permissions():
    account = pwd.getpwnam(SERVICE_USER)
    group = grp.getgrnam(SERVICE_GROUP)
    directory_fd = _open_runtime_directory(ENV_DIRECTORY)
    env_fd = -1
    try:
        directory_metadata = os.fstat(directory_fd)
        if directory_metadata.st_uid != 0:
            raise RuntimeError("runtime env directory is not root-owned")
        os.fchown(directory_fd, 0, group.gr_gid)
        os.fchmod(directory_fd, 0o770)

        env_fd = _open_or_create_env(directory_fd, ENV_NAME)
        metadata = os.fstat(env_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("runtime env path is not a regular file")
        if metadata.st_nlink != 1:
            raise RuntimeError("runtime env file has an unsafe link count")
        if metadata.st_size > MAX_ENV_BYTES:
            raise RuntimeError("runtime env file is unexpectedly large")

        os.fchown(env_fd, account.pw_uid, group.gr_gid)
        os.fchmod(env_fd, 0o600)
        os.fsync(env_fd)

        repaired = os.fstat(env_fd)
        path_metadata = os.stat(ENV_NAME, dir_fd=directory_fd, follow_symlinks=False)
        if (repaired.st_dev, repaired.st_ino) != (path_metadata.st_dev, path_metadata.st_ino):
            raise RuntimeError("runtime env path changed during permission repair")
        if (
            not stat.S_ISREG(path_metadata.st_mode)
            or path_metadata.st_uid != account.pw_uid
            or path_metadata.st_gid != group.gr_gid
            or stat.S_IMODE(path_metadata.st_mode) != 0o600
        ):
            raise RuntimeError("runtime env ownership contract was not restored")
        os.fsync(directory_fd)
    finally:
        if env_fd >= 0:
            os.close(env_fd)
        os.close(directory_fd)


if __name__ == "__main__":
    repair_runtime_env_permissions()
    print("InkyPi runtime env permissions ready")
