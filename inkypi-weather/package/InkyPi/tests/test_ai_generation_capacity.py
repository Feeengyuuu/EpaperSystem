import base64
from io import BytesIO
from pathlib import Path
import sys
import threading
import time

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.ai_image import ai_image as ai_image_module
from plugins.ai_image.ai_image import AIImage
from plugins.ai_image_multiverse import ai_image_multiverse as multiverse_module
from plugins.ai_image_multiverse.ai_image_multiverse import AIImageMultiverse
from runtime.long_task_executor import InstanceIdentity, bind_long_task_runtime
from runtime.refresh_contracts import TaskContext, TaskDeadlineExceeded
from runtime.resource_governor import (
    AI_GENERATION,
    RuntimeResourceGovernor,
)


class _DeviceConfig:
    def load_env_key(self, _name):
        return "test-key"

    def get_config(self, key, default=None):
        if key == "orientation":
            return "horizontal"
        return default

    def get_resolution(self):
        return (800, 480)


def _encoded_png():
    output = BytesIO()
    Image.new("RGB", (4, 3), "white").save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_ai_image_generation_is_single_flight_across_plugins(monkeypatch):
    lock = threading.Lock()
    both_started = threading.Event()
    state = {"active": 0, "maximum": 0, "started": 0}
    encoded = _encoded_png()

    class Images:
        def generate(self, **_kwargs):
            with lock:
                state["active"] += 1
                state["started"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
                if state["started"] >= 2:
                    both_started.set()
            both_started.wait(0.3)
            time.sleep(0.05)
            with lock:
                state["active"] -= 1
            item = type("ImageItem", (), {"b64_json": encoded, "url": None})()
            return type("ImageResponse", (), {"data": [item]})()

    class OpenAI:
        def __init__(self, **_kwargs):
            self.images = Images()

    monkeypatch.setattr(ai_image_module, "OpenAI", OpenAI)
    monkeypatch.setattr(multiverse_module, "OpenAI", OpenAI)
    device_config = _DeviceConfig()
    errors = []
    results = []

    def run(call):
        try:
            results.append(call())
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    threads = [
        threading.Thread(
            target=run,
            args=(
                lambda: AIImage({"id": "ai_image"}).generate_image(
                    {
                        "imageModel": "gpt-image-1",
                        "quality": "low",
                        "textPrompt": "first",
                    },
                    device_config,
                ),
            ),
        ),
        threading.Thread(
            target=run,
            args=(
                lambda: AIImageMultiverse(
                    {"id": "ai_image_multiverse"}
                ).generate_image(
                    {
                        "imageModel": "gpt-image-1",
                        "quality": "low",
                        "textPrompt": "second",
                        "randomizePrompt": "false",
                    },
                    device_config,
                ),
            ),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not errors
    assert len(results) == 2
    assert all(image.size == (4, 3) for image in results)
    assert state["maximum"] == 1


def test_ai_generation_admission_honors_refresh_deadline(monkeypatch):
    monkeypatch.setattr(
        ai_image_module,
        "OpenAI",
        lambda **_kwargs: pytest.fail("provider started without AI admission"),
    )
    held = RuntimeResourceGovernor().acquire(
        AI_GENERATION,
        {},
        TaskContext.never_cancelled(
            deadline_monotonic=time.monotonic() + 2,
        ),
    )
    context = TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + 0.1,
    )
    try:
        with bind_long_task_runtime(
            context,
            InstanceIdentity("ai", 1, 1),
        ):
            with pytest.raises(TaskDeadlineExceeded):
                AIImage({"id": "ai_image"}).generate_image(
                    {
                        "imageModel": "gpt-image-1",
                        "quality": "low",
                        "textPrompt": "blocked",
                    },
                    _DeviceConfig(),
                )
    finally:
        held.release()


def test_ai_generation_permit_is_released_after_provider_failure(monkeypatch):
    encoded = _encoded_png()
    calls = {"value": 0}

    class Images:
        def generate(self, **_kwargs):
            calls["value"] += 1
            if calls["value"] == 1:
                raise RuntimeError("provider failed")
            item = type("ImageItem", (), {"b64_json": encoded, "url": None})()
            return type("ImageResponse", (), {"data": [item]})()

    class OpenAI:
        def __init__(self, **_kwargs):
            self.images = Images()

    monkeypatch.setattr(ai_image_module, "OpenAI", OpenAI)
    monkeypatch.setattr(multiverse_module, "OpenAI", OpenAI)
    device_config = _DeviceConfig()

    with pytest.raises(RuntimeError, match="Open AI request failure"):
        AIImage({"id": "ai_image"}).generate_image(
            {
                "imageModel": "gpt-image-1",
                "quality": "low",
                "textPrompt": "fails",
            },
            device_config,
        )

    image = AIImageMultiverse({"id": "ai_image_multiverse"}).generate_image(
        {
            "imageModel": "gpt-image-1",
            "quality": "low",
            "textPrompt": "succeeds",
            "randomizePrompt": "false",
        },
        device_config,
    )
    assert image.size == (4, 3)
    assert calls["value"] == 2
