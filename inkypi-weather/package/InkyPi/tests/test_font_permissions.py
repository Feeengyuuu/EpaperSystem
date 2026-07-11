import importlib.util
import os
from pathlib import Path
import stat

import pytest


HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "install" / "lib" / "font_permissions.py"
)
HAS_POSIX_FD_PERMISSIONS = all(
    hasattr(os, name)
    for name in ("O_DIRECTORY", "O_NOFOLLOW", "fchmod", "fchown", "getgid", "getuid")
)
requires_posix_fd_permissions = pytest.mark.skipif(
    not HAS_POSIX_FD_PERMISSIONS,
    reason="requires POSIX fd ownership and no-follow flags",
)


def _load_helper():
    assert HELPER_PATH.is_file(), "fd-based durable font helper is missing"
    spec = importlib.util.spec_from_file_location("font_permissions", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@requires_posix_fd_permissions
def test_normalize_font_permissions_creates_root_owned_font_directory(tmp_path):
    helper = _load_helper()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    helper.normalize_font_permissions(
        data_dir,
        uid=os.getuid(),
        gid=os.getgid(),
    )

    metadata = (data_dir / "fonts").stat()
    assert stat.S_ISDIR(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o750
    assert metadata.st_uid == os.getuid()
    assert metadata.st_gid == os.getgid()


@requires_posix_fd_permissions
def test_normalize_font_permissions_updates_regular_files_through_fds(tmp_path):
    helper = _load_helper()
    data_dir = tmp_path / "data"
    fonts = data_dir / "fonts"
    fonts.mkdir(parents=True)
    regular = fonts / "msyh.ttf"
    bold = fonts / "msyhbd.ttf"
    regular.write_bytes(b"regular")
    bold.write_bytes(b"bold")
    regular.chmod(0o600)
    bold.chmod(0o666)

    helper.normalize_font_permissions(
        data_dir,
        uid=os.getuid(),
        gid=os.getgid(),
    )

    for path in (regular, bold):
        metadata = path.stat()
        assert stat.S_IMODE(metadata.st_mode) == 0o640
        assert metadata.st_uid == os.getuid()
        assert metadata.st_gid == os.getgid()


@requires_posix_fd_permissions
def test_normalize_font_permissions_rejects_member_symlink_without_touching_target(
    tmp_path,
):
    helper = _load_helper()
    data_dir = tmp_path / "data"
    fonts = data_dir / "fonts"
    fonts.mkdir(parents=True)
    target = tmp_path / "outside.ttf"
    target.write_bytes(b"outside")
    target.chmod(0o600)
    try:
        (fonts / "msyh.ttf").symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    with pytest.raises(helper.FontPermissionError, match="symbolic link|regular file"):
        helper.normalize_font_permissions(
            data_dir,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@requires_posix_fd_permissions
def test_normalize_font_permissions_rejects_font_directory_symlink(tmp_path):
    helper = _load_helper()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    target = tmp_path / "outside-fonts"
    target.mkdir()
    target.chmod(0o700)
    try:
        (data_dir / "fonts").symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    with pytest.raises(helper.FontPermissionError, match="symbolic link|directory"):
        helper.normalize_font_permissions(
            data_dir,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert stat.S_IMODE(target.stat().st_mode) == 0o700


@requires_posix_fd_permissions
def test_normalize_font_permissions_rejects_symlinked_data_directory(tmp_path):
    helper = _load_helper()
    real_data = tmp_path / "real-data"
    real_data.mkdir()
    linked_data = tmp_path / "linked-data"
    try:
        linked_data.symlink_to(real_data, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    with pytest.raises(helper.FontPermissionError, match="symbolic link|directory"):
        helper.normalize_font_permissions(
            linked_data,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert not (real_data / "fonts").exists()


@requires_posix_fd_permissions
def test_normalize_font_permissions_rejects_non_regular_members(tmp_path):
    helper = _load_helper()
    data_dir = tmp_path / "data"
    fonts = data_dir / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "nested").mkdir()

    with pytest.raises(helper.FontPermissionError, match="regular file"):
        helper.normalize_font_permissions(
            data_dir,
            uid=os.getuid(),
            gid=os.getgid(),
        )
