"""Privileged entry guards and atomic file publication, using local fixtures."""

import hashlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
from types import SimpleNamespace
import zlib

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "install" / "lib"))

import private_score_fonts as fonts  # noqa: E402
from release_state import ReleaseLayout  # noqa: E402

COMMIT = "a" * 40
RELEASE = "deploy-20261027T120000Z-private-" + COMMIT[:8]
ARTIFACT = "b" * 64
NAME = "sports-nba-score.ttf"
PAYLOAD = (PACKAGE / "src/plugins/flight_radar/assets/fonts/Inter-Bold.ttf").read_bytes()
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
ARGS = (NAME, DIGEST, str(len(PAYLOAD)), RELEASE, COMMIT, ARTIFACT)


def test_original_font_metadata_and_required_score_glyphs_are_readable():
    result = fonts.validate_font(PAYLOAD, NAME, DIGEST, len(PAYLOAD))
    assert "Inter" in result["family"]
    assert result["digits"] == "0123456789"


def test_missing_family_is_recorded_honestly_when_style_and_score_glyphs_are_valid(monkeypatch):
    """Official Premier League raw fonts have no family name but retain a style."""
    from PIL import ImageFont

    real = ImageFont.truetype(__import__("io").BytesIO(PAYLOAD), 32)
    fake = SimpleNamespace(getname=lambda: (None, "Regular"), getbbox=real.getbbox, getmask=real.getmask)
    monkeypatch.setattr(ImageFont, "truetype", lambda *args, **kwargs: fake)
    result = fonts.validate_font(PAYLOAD, "sports-epl-score.ttf", DIGEST, len(PAYLOAD))
    assert result["family"] == "" and result["style"] == "Regular"
    fake.getname = lambda: (None, "")
    with pytest.raises(fonts.PrivateFontError, match="metadata is missing"):
        fonts.validate_font(PAYLOAD, "sports-epl-score.ttf", DIGEST, len(PAYLOAD))


def test_raw_woff1_container_is_validated_without_conversion_on_install():
    """Build an in-memory WOFF fixture from the bundled OFL Inter, never a private font."""
    count = struct.unpack_from(">H", PAYLOAD, 4)[0]
    offset = 44 + 20 * count
    records, chunks = [], []
    for index in range(count):
        tag, checksum, start, length = struct.unpack_from(">4sIII", PAYLOAD, 12 + 16 * index)
        raw = PAYLOAD[start:start + length]
        compressed = zlib.compress(raw)
        stored = compressed if len(compressed) < len(raw) else raw
        records.append(struct.pack(">4sIIII", tag, offset, len(stored), len(raw), checksum))
        padded = stored + b"\0" * (-len(stored) % 4)
        chunks.append(padded)
        offset += len(padded)
    header = struct.pack(">4sIIHHIHHIIIII", b"wOFF", 0x10000, offset, count, 0, len(PAYLOAD), 1, 0, 0, 0, 0, 0, 0)
    payload = header + b"".join(records) + b"".join(chunks)
    digest = hashlib.sha256(payload).hexdigest()
    result = fonts.validate_font(payload, "sports-seriea-score.woff", digest, len(payload))
    assert "Inter" in result["family"] and result["digits"] == "0123456789"
    with pytest.raises(fonts.PrivateFontError, match="format"):
        fonts.validate_font(payload, "sports-seriea-score.woff2", digest, len(payload))


@pytest.mark.parametrize("name,digest,size", [("../sports-nba-score.ttf", DIGEST, len(PAYLOAD)),
                                               ("sports-nfl-score.ttf", DIGEST, len(PAYLOAD)),
                                               (NAME, "A" * 64, len(PAYLOAD)),
                                               (NAME, DIGEST, 2 * 1024 * 1024 + 1),
                                               (NAME, DIGEST, True)])
def test_fixed_request_allowlist(name, digest, size):
    with pytest.raises(fonts.PrivateFontError):
        fonts.validate_request(name, digest, size)


def test_font_checksum_and_actual_format_are_validated():
    with pytest.raises(fonts.PrivateFontError, match="SHA256"):
        fonts.validate_font(PAYLOAD, NAME, "0" * 64, len(PAYLOAD))
    with pytest.raises(fonts.PrivateFontError, match="format"):
        fonts.validate_font(PAYLOAD, "sports-nba-score.woff2", DIGEST, len(PAYLOAD))


@pytest.mark.parametrize("missing", ["4", "-"])
def test_font_missing_runtime_required_glyph_is_refused(monkeypatch, missing):
    from PIL import ImageFont

    real = ImageFont.truetype(__import__("io").BytesIO(PAYLOAD), 32)
    fake = SimpleNamespace(getname=real.getname, getbbox=real.getbbox,
                           getmask=lambda char: real.getmask(chr(0x10FFFF) if char == missing else char))
    monkeypatch.setattr(ImageFont, "truetype", lambda *args, **kwargs: fake)
    with pytest.raises(fonts.PrivateFontError, match="readable"):
        fonts.validate_font(PAYLOAD, NAME, DIGEST, len(PAYLOAD))


def test_bad_font_is_rejected_before_privileged_filesystem_operations(monkeypatch):
    monkeypatch.setattr(fonts, "_open_directory", lambda *args: pytest.fail("opened device directory"))
    with pytest.raises(fonts.PrivateFontError):
        fonts.install_font(b"invalid", NAME, hashlib.sha256(b"invalid").hexdigest(), 7)


@pytest.fixture
def file_ops(tmp_path, monkeypatch):
    """Map dir_fd operations to a real local folder on Windows and POSIX.

    Hard-link/no-replace and temporary cleanup use real filesystem operations;
    ownership is recorded instead of requiring a privileged test process.
    """
    calls = []
    nofollow = getattr(os, "O_NOFOLLOW", 1 << 29)
    nonblock = getattr(os, "O_NONBLOCK", 1 << 28)
    real_open = os.open
    real_fstat = os.fstat
    real_stat = os.stat
    real_link = os.link
    real_unlink = os.unlink
    fake = SimpleNamespace(**{key: getattr(os, key) for key in dir(os)})

    def open_at(name, flags, mode=0o777, *, dir_fd=None):
        assert dir_fd == 123
        assert flags & nofollow
        return real_open(tmp_path / name, flags & ~nofollow & ~nonblock, mode)

    def fstat(fd):
        value = real_fstat(fd)
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_size=value.st_size, st_uid=0, st_gid=984)

    def link(source, target, **kwargs):
        assert kwargs == {"src_dir_fd": 123, "dst_dir_fd": 123, "follow_symlinks": False}
        calls.append("link")
        return real_link(tmp_path / source, tmp_path / target)

    fake.O_NOFOLLOW, fake.O_NONBLOCK = nofollow, nonblock
    fake.open, fake.fstat = open_at, fstat
    fake.stat = lambda name, **kwargs: real_stat(tmp_path / name, follow_symlinks=False)
    fake.link = link
    fake.unlink = lambda name, **kwargs: real_unlink(tmp_path / name)
    fake.fchown = lambda fd, uid, gid: calls.append(("owner", uid, gid))
    fake.fchmod = lambda fd, mode: calls.append(("mode", mode))
    fake.fsync = lambda fd: calls.append(("fsync", fd == 123))
    monkeypatch.setattr(fonts, "os", fake)
    monkeypatch.setattr(fonts, "_verify_directory", lambda fd, gid: calls.append("verify"))
    return tmp_path, calls, fake


def test_atomic_publish_sets_permissions_before_visibility_and_removes_temporary(file_ops):
    root, calls, _ = file_ops
    fonts._publish(123, NAME, PAYLOAD, 984)
    assert (root / NAME).read_bytes() == PAYLOAD
    assert [path.name for path in root.iterdir()] == [NAME]
    assert calls.index(("owner", 0, 984)) < calls.index("link")
    assert calls.index(("mode", 0o640)) < calls.index("link")
    assert calls[-1] == ("fsync", True)


def test_atomic_publication_never_replaces_a_concurrent_owner_file(file_ops):
    root, _, _ = file_ops
    (root / NAME).write_bytes(b"owner original")
    with pytest.raises(FileExistsError):
        fonts._publish(123, NAME, PAYLOAD, 984)
    assert (root / NAME).read_bytes() == b"owner original"
    assert [path.name for path in root.iterdir()] == [NAME]


def test_permission_failure_leaves_no_target_or_temporary_file(file_ops):
    root, _, fake = file_ops

    def fail(*args):
        raise PermissionError("permission denied")

    fake.fchown = fail
    with pytest.raises(PermissionError):
        fonts._publish(123, NAME, PAYLOAD, 984)
    assert list(root.iterdir()) == []


def test_identical_owner_font_is_noop_and_different_one_is_refused(file_ops):
    root, _, _ = file_ops
    (root / NAME).write_bytes(PAYLOAD)
    assert fonts._existing_font(123, NAME, DIGEST, 984) is True
    with pytest.raises(fonts.PrivateFontError, match="replacement is forbidden"):
        fonts._existing_font(123, NAME, "0" * 64, 984)
    assert (root / NAME).read_bytes() == PAYLOAD


@pytest.mark.parametrize("extension", ["otf", "woff", "woff2"])
def test_other_format_refuses_even_if_target_is_identical(file_ops, extension):
    root, _, _ = file_ops
    (root / NAME).write_bytes(PAYLOAD)
    (root / f"sports-nba-score.{extension}").write_bytes(b"owner other format")
    with pytest.raises(fonts.PrivateFontError, match="another format"):
        fonts._existing_font(123, NAME, DIGEST, 984)
    assert len(list(root.iterdir())) == 2


def test_directory_traversal_uses_nofollow_for_every_component(monkeypatch):
    calls, closed = [], []
    fake = SimpleNamespace(O_RDONLY=0, O_DIRECTORY=1, O_NOFOLLOW=2, O_CLOEXEC=4, sep="/")

    def open_directory(path, flags, **kwargs):
        calls.append((path, flags, kwargs))
        if path == "data":
            raise OSError("symlink refused")
        return len(calls) + 10

    fake.open, fake.close = open_directory, closed.append
    monkeypatch.setattr(fonts, "os", fake)
    path = SimpleNamespace(is_absolute=lambda: True, parts=("/", "var", "lib", "inkypi", "data", "fonts"))
    with pytest.raises(OSError, match="symlink"):
        fonts._open_directory(path)
    assert all(flags == 7 for _, flags, _ in calls)
    assert len(closed) == 4 and calls[-1][0] == "data"


def load_updater():
    loader = SourceFileLoader("private_font_updater_tests", str(PACKAGE / "install" / "inkypi-update"))
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def updater(tmp_path, monkeypatch):
    module = load_updater()
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    layout.history_dir.mkdir(exist_ok=True)
    root = layout.release_path(RELEASE)
    (root / "install" / "lib").mkdir(parents=True)
    (root / ".release-id").write_text(RELEASE)
    installed = tmp_path / "inkypi-update"
    installed.write_bytes(b"trusted updater")
    shutil.copyfile(installed, root / "install" / "inkypi-update")
    (root / "install" / "lib" / "private_score_fonts.py").write_text("# trusted helper\n")
    try:
        layout.current_link.symlink_to(root, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            pytest.skip("directory symlinks unavailable")
        import _winapi
        _winapi.CreateJunction(str(root), str(layout.current_link))
    (layout.history_dir / "committed.json").write_text(json.dumps({
        "version": 1, "release_id": RELEASE, "phase": "committed", "created_at": 1, "updated_at": 2,
        "metadata": {"artifact_sha256": ARTIFACT}, "history": [{"phase": "committed", "at": 2}]}))
    monkeypatch.setattr(module, "ReleaseLayout", lambda *args: layout)
    monkeypatch.setattr(module, "INSTALLED_UPDATER", installed)
    monkeypatch.setattr(module, "__file__", str(installed))
    return module, layout, root


def test_updater_uses_verified_current_venv_helper_under_update_lock(updater, monkeypatch, capsys):
    module, layout, root = updater
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert layout.lock_path.is_file()
        assert "input" not in kwargs and "stdin" not in kwargs  # inherits SSH stdin, not a caller file
        return SimpleNamespace(returncode=0, stdout=b'{"status":"installed"}', stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.run_private_score_font(ARGS) == 0
    assert calls == [[str(root / "venv_inkypi" / "bin" / "python"), "-I",
                      str(root / "install" / "lib" / "private_score_fonts.py"), NAME, DIGEST, str(len(PAYLOAD))]]
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["commit"] == COMMIT and receipt["artifact_sha256"] == ARTIFACT


@pytest.mark.parametrize("name", sorted(fonts.FONT_NAMES))
def test_updater_and_validator_support_exactly_the_same_fixed_name_registry(updater, monkeypatch, name):
    module, _, _ = updater
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b'{"status":"unchanged"}', stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.run_private_score_font((name, *ARGS[1:])) == 0
    assert calls[0][3] == name


@pytest.mark.parametrize("case", ["journal", "artifact", "identity", "updater", "helper", "history"])
def test_unverified_release_cannot_run_privileged_helper(updater, monkeypatch, case):
    module, layout, root = updater
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: pytest.fail("privileged helper invoked"))
    args = list(ARGS)
    if case == "journal":
        layout.journal_path.write_text("{}")
    elif case == "artifact":
        args[-1] = "c" * 64
    elif case == "identity":
        (root / ".release-id").write_text("another")
    elif case == "updater":
        module.INSTALLED_UPDATER.write_bytes(b"old updater")
    elif case == "helper":
        (root / "install" / "lib" / "private_score_fonts.py").unlink()
    else:
        (layout.history_dir / "committed.json").unlink()
    with pytest.raises(RuntimeError):
        module.run_private_score_font(args)


@pytest.mark.parametrize("args", [(), ("../../file", *ARGS[1:]), (*ARGS, "--install-root", "/tmp"),
                                   (ARGS[0], ARGS[1], "2097153", *ARGS[3:])])
def test_updater_rejects_arbitrary_names_paths_sizes_or_extra_arguments(args, monkeypatch):
    module = load_updater()
    monkeypatch.setattr(module, "UpdateLock", lambda *args: pytest.fail("opened lock for invalid input"))
    with pytest.raises(ValueError):
        module.run_private_score_font(args)


def test_updater_helper_refusal_is_terminal_without_another_write(updater, monkeypatch, capsys):
    module, _, _ = updater
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=2, stdout=b"", stderr=b'{"status":"refused","reason":"owner conflict"}\n')

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.run_private_score_font(ARGS) == 2
    assert len(calls) == 1 and "owner conflict" in capsys.readouterr().err
