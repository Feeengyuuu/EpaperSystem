import base64
from io import BytesIO
from pathlib import Path
import sys
import time

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.ai_image_multiverse import ai_image_multiverse as module
from runtime.long_task_executor import (
    InstanceIdentity,
    LongTaskResult,
    bind_long_task_runtime,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded


class _Result:
    def __init__(self, data):
        self.data = data


class _CancelAfterWait:
    def __init__(self):
        self.cancelled = False
        self.wait_calls = []

    def is_set(self):
        return self.cancelled

    def wait(self, timeout):
        self.wait_calls.append(timeout)
        self.cancelled = True
        return True


def _png_bytes(size=(4, 3), color="red"):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _worker_payload(timeout_seconds=180):
    return {
        "api_key": "secret",
        "request": {"prompt": "private prompt", "params": {"n": 1}},
        "timeout_seconds": timeout_seconds,
    }


def test_horde_worker_wait_is_cancelable_instead_of_fixed_sleep(monkeypatch):
    class Client:
        def request_json(self, method, _url, **_kwargs):
            if method == "POST":
                return _Result({"id": "job-1"})
            return _Result({"done": False, "queue_position": 3, "wait_time": 20})

    cancel_event = _CancelAfterWait()
    monkeypatch.setattr(module, "get_http_client", lambda: Client())
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("fixed sleep used")),
    )

    with pytest.raises(TaskCancelled):
        module._horde_long_task(_worker_payload(), cancel_event)

    assert len(cancel_event.wait_calls) == 1
    assert 0 < cancel_event.wait_calls[0] <= 10


def test_horde_worker_returns_only_validated_png_bytes(monkeypatch):
    encoded = base64.b64encode(_png_bytes()).decode("ascii")

    class NeverCancelled:
        def is_set(self):
            return False

        def wait(self, _timeout):
            return False

    class Client:
        def request_json(self, method, _url, **_kwargs):
            if method == "POST":
                return _Result({"id": "job-1"})
            return _Result(
                {
                    "done": True,
                    "generations": [{"img": encoded, "model": "model"}],
                }
            )

    monkeypatch.setattr(module, "get_http_client", lambda: Client())

    result = module._horde_long_task(_worker_payload(), NeverCancelled())

    assert set(result) == {"image_png"}
    image = Image.open(BytesIO(result["image_png"]))
    assert image.mode == "RGB"
    assert image.size == (4, 3)


class _PlaylistManager:
    def __init__(self, current=True):
        self.current = current
        self.calls = []

    def validate_instance_revision(self, instance_uuid, **kwargs):
        self.calls.append((instance_uuid, kwargs))
        return object() if self.current else None


class _DeviceConfig:
    def __init__(self, manager=None):
        self.manager = manager or _PlaylistManager()

    def load_env_key(self, name):
        assert name == "AI_HORDE_KEY"
        return "secret"

    def get_config(self, key, default=None):
        if key == "orientation":
            return "horizontal"
        return default

    def get_resolution(self):
        return (800, 480)

    def get_playlist_manager(self):
        return self.manager


def test_generate_horde_uses_refresh_deadline_and_revalidates_identity(monkeypatch):
    captures = []
    manager = _PlaylistManager()
    device_config = _DeviceConfig(manager)
    context = TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + 5,
    )
    identity = InstanceIdentity("instance", 2, 9)

    class Handle:
        def result(self, timeout):
            captures.append(("wait_timeout", timeout))
            return LongTaskResult(
                "succeeded",
                {"image_png": _png_bytes()},
            )

    class Executor:
        def submit(self, name, payload, **kwargs):
            captures.append((name, payload, kwargs))
            assert kwargs["identity_validator"](kwargs["instance_identity"])
            return Handle()

    monkeypatch.setattr(module, "_get_horde_executor", lambda: Executor())

    with bind_long_task_runtime(context, identity):
        image = module.generate_horde_image(
            {"imageModel": "ai-horde", "quality": "standard"},
            device_config,
            "private prompt",
        )

    name, payload, kwargs = captures[0]
    assert name == "ai_horde_generate"
    assert payload["timeout_seconds"] <= 5
    assert payload["timeout_seconds"] <= 180
    assert kwargs["context"] is context
    assert kwargs["instance_identity"] == identity
    assert manager.calls == [
        (
            "instance",
            {"expected_generation": 2, "expected_settings_revision": 9},
        )
    ]
    assert captures[1][0] == "wait_timeout"
    assert image.mode == "RGB"


@pytest.mark.parametrize(
    ("result", "error_type"),
    [
        (LongTaskResult("abandoned", error_code="deadline_expired"), TaskDeadlineExceeded),
        (LongTaskResult("canceled", error_code="task_canceled"), TaskCancelled),
        (LongTaskResult("stale", error_code="stale_instance"), TaskCancelled),
    ],
)
def test_generate_horde_preserves_scheduler_cancellation_semantics(
    monkeypatch,
    result,
    error_type,
):
    class Handle:
        def result(self, timeout):
            assert timeout > 0
            return result

    class Executor:
        def submit(self, *_args, **_kwargs):
            return Handle()

    monkeypatch.setattr(module, "_get_horde_executor", lambda: Executor())

    with pytest.raises(error_type):
        module.generate_horde_image(
            {"imageModel": "ai-horde", "quality": "standard"},
            _DeviceConfig(),
            "prompt",
        )
