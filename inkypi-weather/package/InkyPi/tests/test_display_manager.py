import inspect
import time
from pathlib import Path

import pytest
from PIL import Image

from src.display.display_manager import DisplayManager
from src.runtime.refresh_contracts import TaskCancelled, TaskContext
from src.runtime.runtime_state import RuntimeStateStore


class FakeDeviceConfig:
    def __init__(self, tmp_path):
        self.display_dir = tmp_path / "display"
        self.current_image_file = self.display_dir / "current_image.png"
        self.data_dir = tmp_path / "data"
        self.display_dir.mkdir()
        self.data_dir.mkdir()
        self.values = {
            "display_type": "fake",
            "orientation": "horizontal",
            "inverted_image": False,
            "image_settings": {
                "saturation": 1.0,
                "brightness": 1.0,
                "sharpness": 1.0,
                "contrast": 1.0,
                "inky_saturation": 0.5,
            },
        }

    def get_config(self, key=None, default=None):
        if key is None:
            return dict(self.values)
        return self.values.get(key, default)

    def get_resolution(self):
        return (8, 6)


class FakeDisplay:
    def __init__(self):
        self.calls = []
        self.error = None

    def display_image(self, image, image_settings=()):
        self.calls.append((image.copy(), tuple(image_settings)))
        if self.error is not None:
            raise self.error


def _manager(tmp_path):
    config = FakeDeviceConfig(tmp_path)
    manager = DisplayManager.__new__(DisplayManager)
    manager.device_config = config
    manager.display = FakeDisplay()
    manager.bind_runtime_state(RuntimeStateStore(config.data_dir / "runtime_state.json"))
    return manager


def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 5)


def test_display_manager_only_publishes_current_image_after_hardware_success(tmp_path):
    manager = _manager(tmp_path)
    first = manager.display_image(
        Image.new("RGB", (8, 6), "red"),
        task_context=_context(),
        logical_target={"id": "one"},
    )
    before = Path(manager.device_config.current_image_file).read_bytes()
    manager.display.error = RuntimeError("busy")

    with pytest.raises(RuntimeError, match="busy"):
        manager.display_image(
            Image.new("RGB", (8, 6), "blue"),
            task_context=_context(),
            logical_target={"id": "two"},
        )

    assert Path(manager.device_config.current_image_file).read_bytes() == before
    assert manager.transaction.current().commit_id == first.commit_id


def test_prepare_image_applies_pipeline_without_mutating_source(tmp_path):
    manager = _manager(tmp_path)
    source = Image.new("RGB", (4, 3), "red")

    prepared = manager.prepare_image(source)

    assert source.size == (4, 3)
    assert prepared.size == (8, 6)
    assert prepared is not source


def test_display_image_default_is_immutable_tuple():
    parameter = inspect.signature(DisplayManager.display_image).parameters[
        "image_settings"
    ]

    assert parameter.default == ()


def test_successful_hardware_write_is_the_commit_linearization_point(tmp_path):
    manager = _manager(tmp_path)

    class CancelAfterHardware:
        def __init__(self):
            self.checks = 0

        def raise_if_cancelled(self):
            self.checks += 1
            if self.checks >= 3:
                raise TaskCancelled("cancelled after hardware")

    context = CancelAfterHardware()
    commit = manager.display_image(
        Image.new("RGB", (8, 6), "green"),
        task_context=context,
        logical_target={"id": "linearized"},
    )

    assert context.checks == 2
    assert commit.hardware_written is True
    assert manager.transaction.current().commit_id == commit.commit_id
