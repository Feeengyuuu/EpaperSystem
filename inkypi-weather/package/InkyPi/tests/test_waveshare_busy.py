import importlib
import sys
from types import ModuleType

import pytest
from PIL import Image

from display.busy_wait import DisplayBusyTimeout, wait_while_busy
from display.display_manager import DisplayManager
from display.waveshare_display import WaveshareDisplay
from runtime.refresh_contracts import TaskCancelled, TaskContext


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleep_calls = []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        self.value += seconds


def _context(clock, deadline=1.0):
    return TaskContext.never_cancelled(
        deadline_monotonic=deadline,
        clock=clock.monotonic,
    )


def test_busy_wait_times_out_without_spinning():
    clock = FakeClock()

    with pytest.raises(DisplayBusyTimeout, match="epd7in5.init"):
        wait_while_busy(
            lambda: 0,
            timeout_seconds=0.05,
            poll_interval_seconds=0.01,
            clock=clock.monotonic,
            sleeper=clock.sleep,
            task_context=_context(clock),
            stage="epd7in5.init",
        )

    assert clock.value == pytest.approx(0.05)
    assert clock.sleep_calls == pytest.approx([0.01] * 5)


def test_busy_wait_honors_cancellation_before_sleeping():
    clock = FakeClock()
    context = _context(clock)
    context.cancel_event.set()

    with pytest.raises(TaskCancelled, match="canceled"):
        wait_while_busy(
            lambda: 0,
            task_context=context,
            stage="epd.cancel",
            clock=clock.monotonic,
            sleeper=clock.sleep,
        )

    assert clock.sleep_calls == []


def _fake_epdconfig():
    module = ModuleType("display.waveshare_epd.epdconfig")
    module.RST_PIN = 17
    module.DC_PIN = 25
    module.BUSY_PIN = 24
    module.CS_PIN = 8
    module.digital_read = lambda _pin: 1
    module.delay_ms = lambda _milliseconds: None
    module.digital_write = lambda *_args: None
    module.spi_writebyte = lambda *_args: None
    module.spi_writebyte2 = lambda *_args: None
    module.module_init = lambda: 0
    module.module_exit = lambda: None
    module.SPI = type("SPI", (), {"writebytes2": lambda *_args: None})()
    return module


@pytest.mark.parametrize(
    ("module_name", "method_name", "expected_stage"),
    [
        ("display.waveshare_epd.epd7in5_V2", "ReadBusy", "epd7in5.busy"),
        ("display.waveshare_epd.epd7in3e", "ReadBusyH", "epd7in3e.busy"),
    ],
)
def test_supported_waveshare_drivers_use_shared_bounded_wait(
    monkeypatch,
    module_name,
    method_name,
    expected_stage,
):
    fake_config = _fake_epdconfig()
    monkeypatch.setitem(sys.modules, "display.waveshare_epd.epdconfig", fake_config)
    sys.modules.pop(module_name, None)
    driver_module = importlib.import_module(module_name)
    calls = []

    def fake_wait(read_busy, **kwargs):
        calls.append(kwargs)
        assert read_busy() == 1

    monkeypatch.setattr(driver_module, "wait_while_busy", fake_wait)
    driver = driver_module.EPD.__new__(driver_module.EPD)
    driver.busy_pin = fake_config.BUSY_PIN
    driver.send_command = lambda _command: None
    context = TaskContext.never_cancelled(deadline_monotonic=10**12)
    driver._inkypi_task_context = context

    getattr(driver, method_name)()

    assert calls[0]["task_context"] is context
    assert calls[0]["stage"] == expected_stage


def test_waveshare_display_scopes_task_context_to_every_hardware_stage():
    context = TaskContext.never_cancelled(deadline_monotonic=10**12)

    class FakeEpd:
        def __init__(self):
            self.contexts = []

        def record(self, _value=None):
            self.contexts.append(getattr(self, "_inkypi_task_context", None))

        init = record
        Clear = record
        display = record
        sleep = record

        @staticmethod
        def getbuffer(_image):
            return b"pixels"

    epd = FakeEpd()
    display = WaveshareDisplay.__new__(WaveshareDisplay)
    display.epd_display = epd
    display.epd_display_init = epd.init
    display.bi_color_display = False

    display.display_image_with_context(
        Image.new("RGB", (2, 1), "white"),
        task_context=context,
    )

    assert epd.contexts == [context, context, context, context]
    assert not hasattr(epd, "_inkypi_task_context")


def test_display_manager_passes_context_to_context_aware_driver():
    calls = []

    class ContextAwareDriver:
        def display_image_with_context(self, image, image_settings=(), *, task_context):
            calls.append((image, tuple(image_settings), task_context))

    manager = DisplayManager.__new__(DisplayManager)
    manager.display = ContextAwareDriver()
    context = TaskContext.never_cancelled(deadline_monotonic=10**12)
    image = Image.new("RGB", (2, 1), "white")

    manager.write_hardware(
        image,
        image_settings=("keep-width",),
        task_context=context,
    )

    assert calls == [(image, ("keep-width",), context)]
