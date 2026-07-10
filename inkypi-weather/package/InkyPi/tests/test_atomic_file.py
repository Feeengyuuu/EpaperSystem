import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from PIL import Image


class _StreamProxy:
    def __init__(self, stream, *, fail_operation=None):
        self._stream = stream
        self._fail_operation = fail_operation
        self._close_failed = False

    def write(self, payload):
        if self._fail_operation == "write":
            raise OSError("injected write failure")
        if self._fail_operation == "short_write":
            return self._stream.write(payload[:-1])
        return self._stream.write(payload)

    def flush(self):
        if self._fail_operation == "flush":
            raise OSError("injected flush failure")
        return self._stream.flush()

    def fileno(self):
        return self._stream.fileno()

    def close(self):
        if self._fail_operation == "close" and not self._close_failed:
            self._close_failed = True
            self._stream.close()
            raise OSError("injected close failure")
        return self._stream.close()


def _temp_files(directory: Path, target_name: str) -> list[Path]:
    return list(directory.glob(f".{target_name}.*.tmp"))


def test_atomic_write_uses_same_directory_and_durable_operation_order(tmp_path, monkeypatch):
    from src.utils import atomic_file

    target = tmp_path / "state.bin"
    events = []
    real_mkstemp = atomic_file.tempfile.mkstemp
    real_fsync = atomic_file.os.fsync
    real_replace = atomic_file.os.replace

    def recording_mkstemp(*args, **kwargs):
        fd, temp_name = real_mkstemp(*args, **kwargs)
        events.append(("create_temp", Path(temp_name).parent))
        return fd, temp_name

    def recording_fsync(fd):
        events.append(("file_fsync", fd))
        return real_fsync(fd)

    def recording_replace(source, destination):
        events.append(("replace", Path(source).parent, Path(destination)))
        return real_replace(source, destination)

    def recording_directory_fsync(directory):
        events.append(("directory_fsync", Path(directory)))

    monkeypatch.setattr(atomic_file, "_WINDOWS", False)
    monkeypatch.setattr(atomic_file.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(atomic_file.os, "fchmod", lambda _fd, _mode: events.append(("set_mode", _mode)), raising=False)
    monkeypatch.setattr(atomic_file.os, "fsync", recording_fsync)
    monkeypatch.setattr(atomic_file.os, "replace", recording_replace)
    monkeypatch.setattr(atomic_file, "fsync_directory", recording_directory_fsync)

    atomic_file.atomic_write_bytes(target, b"new-state")

    assert target.read_bytes() == b"new-state"
    assert events[0] == ("create_temp", tmp_path)
    assert [event[0] for event in events] == [
        "create_temp",
        "set_mode",
        "file_fsync",
        "replace",
        "directory_fsync",
    ]
    assert events[-1] == ("directory_fsync", tmp_path)


def test_missing_parent_fails_before_creating_temp(tmp_path, monkeypatch):
    from src.utils import atomic_file

    target = tmp_path / "missing" / "state.bin"
    monkeypatch.setattr(
        atomic_file.tempfile,
        "mkstemp",
        lambda *args, **kwargs: pytest.fail("mkstemp must not be called"),
    )

    with pytest.raises(FileNotFoundError):
        atomic_file.atomic_write_bytes(target, b"state")

    assert not target.parent.exists()


@pytest.mark.parametrize(
    ("stage", "fail_operation"),
    [
        ("create_temp", "create_temp"),
        ("set_mode", "set_mode"),
        ("open_temp", "open_temp"),
        ("write", "write"),
        ("write", "short_write"),
        ("write", "flush"),
        ("file_fsync", "file_fsync"),
        ("file_close", "file_close"),
        ("replace", "replace"),
    ],
)
def test_pre_replace_failures_preserve_old_target_and_remove_temp(
    tmp_path,
    monkeypatch,
    stage,
    fail_operation,
):
    from src.utils import atomic_file

    target = tmp_path / "state.bin"
    target.write_bytes(b"old-state")
    real_fdopen = atomic_file.os.fdopen
    real_fsync = atomic_file.os.fsync
    real_mkstemp = atomic_file.tempfile.mkstemp

    if fail_operation == "create_temp":
        monkeypatch.setattr(
            atomic_file.tempfile,
            "mkstemp",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected create failure")),
        )
    else:
        created_fds = []

        def recording_mkstemp(*args, **kwargs):
            fd, temp_name = real_mkstemp(*args, **kwargs)
            created_fds.append(fd)
            return fd, temp_name

        monkeypatch.setattr(atomic_file.tempfile, "mkstemp", recording_mkstemp)

        if fail_operation == "set_mode":
            monkeypatch.setattr(atomic_file, "_WINDOWS", False)
            monkeypatch.setattr(
                atomic_file.os,
                "fchmod",
                lambda *_args: (_ for _ in ()).throw(OSError("injected chmod failure")),
                raising=False,
            )
        elif fail_operation == "open_temp":
            monkeypatch.setattr(
                atomic_file.os,
                "fdopen",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected fdopen failure")),
            )
        elif fail_operation in {"write", "short_write", "flush", "file_close"}:
            proxy_operation = "close" if fail_operation == "file_close" else fail_operation

            def failing_fdopen(fd, *args, **kwargs):
                return _StreamProxy(real_fdopen(fd, *args, **kwargs), fail_operation=proxy_operation)

            monkeypatch.setattr(atomic_file.os, "fdopen", failing_fdopen)
        elif fail_operation == "file_fsync":
            monkeypatch.setattr(
                atomic_file.os,
                "fsync",
                lambda _fd: (_ for _ in ()).throw(OSError("injected fsync failure")),
            )
        elif fail_operation == "replace":
            monkeypatch.setattr(
                atomic_file.os,
                "replace",
                lambda *_args: (_ for _ in ()).throw(PermissionError("injected replace failure")),
            )

    with pytest.raises(atomic_file.AtomicWriteError) as caught:
        atomic_file.atomic_write_bytes(target, b"secret-new-state")

    assert caught.value.stage == stage
    assert caught.value.target == target
    assert caught.value.target_replaced is False
    assert target.read_bytes() == b"old-state"
    assert _temp_files(tmp_path, target.name) == []
    assert "secret-new-state" not in str(caught.value)

    if fail_operation != "create_temp":
        for fd in created_fds:
            with pytest.raises(OSError):
                os.fstat(fd)
        monkeypatch.setattr(atomic_file.os, "fsync", real_fsync)


def test_replace_permission_error_never_falls_back_to_direct_write(tmp_path, monkeypatch):
    from src.utils import atomic_file

    target = tmp_path / "state.bin"
    target.write_bytes(b"old-state")
    replace_calls = []

    def denied_replace(source, destination):
        replace_calls.append((source, destination))
        raise PermissionError("locked")

    monkeypatch.setattr(atomic_file.os, "replace", denied_replace)

    with pytest.raises(atomic_file.AtomicWriteError) as caught:
        atomic_file.atomic_write_bytes(target, b"new-state")

    assert caught.value.stage == "replace"
    assert replace_calls and len(replace_calls) == 1
    assert target.read_bytes() == b"old-state"


def test_post_replace_directory_failure_is_commit_uncertain_with_new_target_visible(tmp_path, monkeypatch):
    from src.utils import atomic_file

    target = tmp_path / "state.bin"
    target.write_bytes(b"old-state")
    monkeypatch.setattr(atomic_file, "_WINDOWS", False)
    monkeypatch.setattr(atomic_file.os, "fchmod", lambda *_args: None, raising=False)
    monkeypatch.setattr(
        atomic_file,
        "fsync_directory",
        lambda _directory: (_ for _ in ()).throw(OSError("directory fsync failed")),
    )

    with pytest.raises(atomic_file.AtomicCommitUncertainError) as caught:
        atomic_file.atomic_write_bytes(target, b"new-state")

    assert caught.value.stage == "directory_fsync"
    assert caught.value.target == target
    assert caught.value.target_replaced is True
    assert target.read_bytes() == b"new-state"
    assert _temp_files(tmp_path, target.name) == []


def test_fsync_directory_closes_descriptor_when_fsync_raises(tmp_path, monkeypatch):
    from src.utils import atomic_file

    primary = OSError("fsync failed")
    closed = []
    monkeypatch.setattr(atomic_file, "_WINDOWS", False)
    monkeypatch.setattr(atomic_file.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(atomic_file.os, "open", lambda *_args: 91)
    monkeypatch.setattr(atomic_file.os, "fsync", lambda _fd: (_ for _ in ()).throw(primary))
    monkeypatch.setattr(atomic_file.os, "close", lambda fd: closed.append(fd))

    with pytest.raises(OSError) as caught:
        atomic_file.fsync_directory(tmp_path)

    assert caught.value is primary
    assert closed == [91]


def test_fsync_directory_preserves_fsync_error_when_close_also_fails(tmp_path, monkeypatch):
    from src.utils import atomic_file

    primary = OSError("primary fsync failed")
    monkeypatch.setattr(atomic_file, "_WINDOWS", False)
    monkeypatch.setattr(atomic_file.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(atomic_file.os, "open", lambda *_args: 92)
    monkeypatch.setattr(atomic_file.os, "fsync", lambda _fd: (_ for _ in ()).throw(primary))
    monkeypatch.setattr(
        atomic_file.os,
        "close",
        lambda _fd: (_ for _ in ()).throw(OSError("secondary close failed")),
    )

    with pytest.raises(OSError) as caught:
        atomic_file.fsync_directory(tmp_path)

    assert caught.value is primary
    assert any("directory descriptor close" in note for note in caught.value.__notes__)


@pytest.mark.parametrize("primary", [KeyboardInterrupt(), SystemExit(12)])
def test_cleanup_failures_preserve_primary_baseexception_and_note_residual_temp(
    tmp_path,
    monkeypatch,
    primary,
):
    from src.utils import atomic_file

    target = tmp_path / "state.bin"
    target.write_bytes(b"old-state")
    residual = tmp_path / ".state.bin.injected.tmp"
    residual.write_bytes(b"temporary-secret")
    monkeypatch.setattr(atomic_file, "_WINDOWS", False)
    monkeypatch.setattr(atomic_file.tempfile, "mkstemp", lambda *args, **kwargs: (101, str(residual)))
    monkeypatch.setattr(atomic_file.os, "fchmod", lambda *_args: (_ for _ in ()).throw(primary), raising=False)
    monkeypatch.setattr(
        atomic_file.os,
        "close",
        lambda _fd: (_ for _ in ()).throw(OSError("secondary close failed")),
    )
    monkeypatch.setattr(
        atomic_file.os,
        "unlink",
        lambda _path: (_ for _ in ()).throw(OSError("secondary unlink failed")),
    )

    with pytest.raises(type(primary)) as caught:
        atomic_file.atomic_write_bytes(target, b"secret-payload")

    assert caught.value is primary
    notes = "\n".join(caught.value.__notes__)
    assert "descriptor cleanup" in notes
    assert str(residual) in notes
    assert "secret-payload" not in notes
    assert target.read_bytes() == b"old-state"
    assert residual.exists()


def test_cleanup_failures_do_not_replace_wrapped_primary_error(tmp_path, monkeypatch):
    from src.utils import atomic_file

    target = tmp_path / "state.bin"
    target.write_bytes(b"old-state")
    residual = tmp_path / ".state.bin.injected.tmp"
    residual.write_bytes(b"temp")
    primary = OSError("primary chmod failed")
    monkeypatch.setattr(atomic_file, "_WINDOWS", False)
    monkeypatch.setattr(atomic_file.tempfile, "mkstemp", lambda *args, **kwargs: (102, str(residual)))
    monkeypatch.setattr(atomic_file.os, "fchmod", lambda *_args: (_ for _ in ()).throw(primary), raising=False)
    monkeypatch.setattr(
        atomic_file.os,
        "close",
        lambda _fd: (_ for _ in ()).throw(OSError("secondary close failed")),
    )
    monkeypatch.setattr(
        atomic_file.os,
        "unlink",
        lambda _path: (_ for _ in ()).throw(OSError("secondary unlink failed")),
    )

    with pytest.raises(atomic_file.AtomicWriteError) as caught:
        atomic_file.atomic_write_bytes(target, b"secret-payload")

    assert caught.value.stage == "set_mode"
    assert caught.value.__cause__ is primary
    notes = "\n".join(caught.value.__notes__)
    assert "descriptor cleanup" in notes
    assert str(residual) in notes
    assert "secret-payload" not in notes


def test_windows_seam_skips_fchmod_and_directory_open(tmp_path, monkeypatch):
    from src.utils import atomic_file

    target = tmp_path / "state.bin"
    real_open = atomic_file.os.open

    def forbid_directory_open(path, *args, **kwargs):
        if Path(path) == tmp_path:
            pytest.fail("Windows must not open directories")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(atomic_file, "_WINDOWS", True)
    monkeypatch.setattr(
        atomic_file.os,
        "fchmod",
        lambda *_args: pytest.fail("Windows must not call fchmod"),
        raising=False,
    )
    monkeypatch.setattr(atomic_file.os, "open", forbid_directory_open)

    atomic_file.atomic_write_bytes(target, b"windows-state")
    atomic_file.fsync_directory(tmp_path)

    assert target.read_bytes() == b"windows-state"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not available on Windows")
def test_posix_mode_0600_survives_umask(tmp_path):
    from src.utils.atomic_file import atomic_write_bytes

    target = tmp_path / "state.bin"
    old_umask = os.umask(0)
    try:
        atomic_write_bytes(target, b"private", mode=0o600)
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_json_is_strict_utf8_with_trailing_newline(tmp_path):
    from src.utils.atomic_file import atomic_write_json

    target = tmp_path / "state.json"
    payload = {"message": "你好", "enabled": True}

    atomic_write_json(target, payload)

    encoded = target.read_bytes()
    assert encoded.endswith(b"\n")
    assert encoded.decode("utf-8") == json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n"
    assert json.loads(encoded) == payload


@pytest.mark.parametrize(
    "payload",
    [
        {1: "top-level"},
        {"nested": [{"valid": True}, {2: "nested"}]},
    ],
)
def test_json_rejects_non_string_mapping_keys_before_temp(
    tmp_path,
    monkeypatch,
    payload,
):
    from src.utils import atomic_file

    target = tmp_path / "state.json"
    monkeypatch.setattr(
        atomic_file.tempfile,
        "mkstemp",
        lambda *args, **kwargs: pytest.fail("mkstemp must not be called"),
    )

    with pytest.raises(TypeError, match="JSON object keys must be strings"):
        atomic_file.atomic_write_json(target, payload)

    assert not target.exists()


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        ({"value": float("nan")}, ValueError),
        ({"value": object()}, TypeError),
    ],
)
def test_json_encode_failure_happens_before_temp_creation(tmp_path, monkeypatch, payload, error_type):
    from src.utils import atomic_file

    target = tmp_path / "state.json"
    target.write_bytes(b"old-json")
    monkeypatch.setattr(
        atomic_file.tempfile,
        "mkstemp",
        lambda *args, **kwargs: pytest.fail("mkstemp must not be called"),
    )

    with pytest.raises(error_type):
        atomic_file.atomic_write_json(target, payload)

    assert target.read_bytes() == b"old-json"


def test_invalid_payload_and_mode_fail_before_temp_creation(tmp_path, monkeypatch):
    from src.utils import atomic_file

    target = tmp_path / "state.bin"
    monkeypatch.setattr(
        atomic_file.tempfile,
        "mkstemp",
        lambda *args, **kwargs: pytest.fail("mkstemp must not be called"),
    )

    with pytest.raises(TypeError):
        atomic_file.atomic_write_bytes(target, bytearray(b"not-bytes"))
    with pytest.raises(ValueError):
        atomic_file.atomic_write_bytes(target, b"bytes", mode=-1)


def test_png_round_trip(tmp_path):
    from src.utils.atomic_file import atomic_write_image

    target = tmp_path / "image.png"
    image = Image.new("RGB", (3, 2), (12, 34, 56))

    atomic_write_image(target, image)

    with Image.open(target) as loaded:
        loaded.load()
        assert loaded.format == "PNG"
        assert loaded.size == (3, 2)
        assert loaded.getpixel((1, 1)) == (12, 34, 56)


def test_image_encode_failure_preserves_old_target_and_creates_no_temp(tmp_path, monkeypatch):
    from src.utils import atomic_file

    class BrokenImage:
        def save(self, _stream, *, format):
            raise RuntimeError(f"cannot encode {format}")

    target = tmp_path / "image.png"
    target.write_bytes(b"old-image")
    monkeypatch.setattr(
        atomic_file.tempfile,
        "mkstemp",
        lambda *args, **kwargs: pytest.fail("mkstemp must not be called"),
    )

    with pytest.raises(RuntimeError, match="cannot encode PNG"):
        atomic_file.atomic_write_image(target, BrokenImage())

    assert target.read_bytes() == b"old-image"


def test_concurrent_writers_publish_one_complete_payload_and_leave_no_temp(tmp_path_factory, monkeypatch):
    from src.utils import atomic_file

    tmp_path = tmp_path_factory.mktemp("atomic-concurrent")
    target = tmp_path / "state.bin"
    payload_a = b"A" * 65536
    payload_b = b"B" * 65536
    barrier = Barrier(2)
    real_replace = atomic_file.os.replace

    def synchronized_replace(source, destination):
        barrier.wait(timeout=5)
        return real_replace(source, destination)

    monkeypatch.setattr(atomic_file.os, "replace", synchronized_replace)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(atomic_file.atomic_write_bytes, target, payload_a),
            executor.submit(atomic_file.atomic_write_bytes, target, payload_b),
        ]
        failures = []
        for future in futures:
            try:
                future.result(timeout=10)
            except atomic_file.AtomicWriteError as error:
                failures.append(error)

    assert len(failures) < 2
    assert all(error.stage == "replace" for error in failures)
    assert target.read_bytes() in {payload_a, payload_b}
    assert _temp_files(tmp_path, target.name) == []
