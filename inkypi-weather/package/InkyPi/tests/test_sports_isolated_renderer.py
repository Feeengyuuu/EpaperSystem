import sys
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    read_source_provenance,
)
from plugins.base_plugin.presentation import bind_presentation_instance_identity
from plugins.sports_dashboard import isolated_refresh
from runtime.long_task_executor import (
    MAX_PAYLOAD_BYTES,
    InstanceIdentity,
    LongTaskResult,
    _copy_primitive,
)
from runtime.refresh_contracts import TaskContext, freeze_payload
from runtime import sports_isolated_renderer


def _png_bytes(color):
    output = BytesIO()
    Image.new("RGB", (800, 480), color).save(output, format="PNG")
    return output.getvalue()


class _DeviceConfig:
    runtime_paths = SimpleNamespace(env_file="/tmp/inkypi-test.env")

    def get_config(self, key, default=None):
        return {
            "resolution": [800, 480],
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
        }.get(key, default)

    def get_resolution(self):
        return 800, 480


class _CompletedHandle:
    def __init__(self, result):
        self._result = result
        self.canceled = False

    def result(self, timeout=None):
        return self._result

    def cancel(self):
        self.canceled = True
        return True


class _RecordingExecutor:
    def __init__(self):
        self.submissions = []

    def submit(self, task_name, payload, **kwargs):
        payload = _copy_primitive(payload, max_bytes=MAX_PAYLOAD_BYTES)
        self.submissions.append((task_name, payload, kwargs))
        index = len(self.submissions) - 1
        region = sports_isolated_renderer.SPORTS_REGIONS[index]
        value = {
            "region": region,
            "region_provenance": (
                SourceProvenance.FRESH_CACHE.value
                if region != "esports"
                else SourceProvenance.LIVE.value
            ),
            "image_png": _png_bytes((20 + index, 40, 60)),
            "theme_mode": "day",
            "worker_oom_score_adj": 800,
            "worker_pid": 4242 + index,
        }
        if region == "esports":
            value.update(
                {
                    "composite_provenance": SourceProvenance.LIVE.value,
                    "skip_cache": False,
                }
            )
        return _CompletedHandle(LongTaskResult("succeeded", value=value))


def _context(seconds=10):
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + seconds
    )


def test_isolated_renderer_runs_one_short_lived_job_per_region(monkeypatch):
    executor = _RecordingExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    expected_identity = InstanceIdentity("sports", 1, 2)

    image = sports_isolated_renderer.render_sports_dashboard_isolated(
        settings=bind_presentation_instance_identity(
            {"id": "sports_dashboard"},
            "sports-instance",
        ),
        device_config=_DeviceConfig(),
        resolved_theme_context=freeze_payload(
            {
                "mode": "day",
                "palette": {"background": "#ffffff"},
            }
        ),
        context=_context(),
        instance_identity=expected_identity,
        identity_validator=lambda candidate: candidate == expected_identity,
        resource_sampler=lambda: SimpleNamespace(
            available_mb=240,
            swap_percent=10,
        ),
        start_min_available_mb=180,
        start_max_swap_percent=60,
        abort_min_available_mb=150,
        abort_max_swap_percent=70,
        now=SimpleNamespace(isoformat=lambda: "2026-07-27T09:00:00-07:00"),
    )

    assert [item[1]["region"] for item in executor.submissions] == [
        "football",
        "lower",
        "esports",
    ]
    assert executor.submissions[0][1]["base_png"] is None
    assert isinstance(executor.submissions[1][1]["base_png"], bytes)
    assert executor.submissions[2][1]["panel_provenances"] == [
        SourceProvenance.FRESH_CACHE.value,
        SourceProvenance.FRESH_CACHE.value,
    ]
    assert all(
        submission[1]["settings"]["worldCupScreenshotFallback"] is False
        for submission in executor.submissions
    )
    assert all(
        "_inkypi_presentation_instance_identity"
        not in submission[1]["settings"]
        for submission in executor.submissions
    )
    assert len(
        {
            submission[1]["cache_identity"]
            for submission in executor.submissions
        }
    ) == 1
    assert all(
        submission[2]["identity_validator"](expected_identity)
        for submission in executor.submissions
    )
    assert image.size == (800, 480)
    assert image.info["inkypi_theme_mode"] == "day"
    assert read_source_provenance(image) is SourceProvenance.LIVE


def test_isolated_renderer_normalizes_thawed_night_palette_for_pillow(monkeypatch):
    executor = _RecordingExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)

    sports_isolated_renderer.render_sports_dashboard_isolated(
        settings={"id": "sports_dashboard"},
        device_config=_DeviceConfig(),
        resolved_theme_context=freeze_payload(
            {
                "mode": "night",
                "palette": {
                    "background": (0, 0, 0),
                    "panel": (0, 0, 0),
                    "ink": (255, 255, 255),
                    "muted": (194, 196, 202),
                    "rule": (46, 48, 56),
                    "accent": (107, 204, 255),
                },
            }
        ),
        context=_context(),
        instance_identity=InstanceIdentity("sports", 1, 2),
        resource_sampler=lambda: SimpleNamespace(
            available_mb=240,
            swap_percent=10,
        ),
        start_min_available_mb=180,
        start_max_swap_percent=60,
        abort_min_available_mb=150,
        abort_max_swap_percent=70,
        now=SimpleNamespace(isoformat=lambda: "2026-07-27T01:00:00-07:00"),
    )

    for _task_name, payload, _kwargs in executor.submissions:
        palette = payload["settings"]["_inkypi_theme"]["palette"]
        assert all(type(color) is tuple for color in palette.values())


class _BlockingHandle:
    def __init__(self):
        self.canceled = False

    def result(self, timeout=None):
        if self.canceled:
            return LongTaskResult("canceled", error_code="task_canceled")
        raise TimeoutError

    def cancel(self):
        self.canceled = True
        return True


def test_parent_resource_guard_terminates_child_before_hard_pressure(caplog):
    handle = _BlockingHandle()

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedResourcePressure
    ):
        sports_isolated_renderer._wait_for_result(
            handle,
            context=_context(),
            resource_sampler=lambda: SimpleNamespace(
                available_mb=149.9,
                swap_percent=60,
            ),
            abort_min_available_mb=150,
            abort_max_swap_percent=70,
            poll_seconds=0.01,
        )

    assert handle.canceled is True
    assert "available_mb: 149.9" in caplog.text
    assert "minimum_available_mb: 150" in caplog.text


@pytest.mark.parametrize(
    "sample",
    [
        SimpleNamespace(available_mb=None, swap_percent=0),
        SimpleNamespace(available_mb=240, swap_percent=float("nan")),
        SimpleNamespace(available_mb=True, swap_percent=0),
    ],
)
def test_resource_margin_fails_closed_for_invalid_metrics(sample):
    assert not sports_isolated_renderer._resource_margin_available(
        sample,
        min_available_mb=180,
        max_swap_percent=60,
    )


def test_worker_oom_preference_round_trips_kernel_value(monkeypatch, tmp_path):
    score_path = tmp_path / "oom_score_adj"
    score_path.write_text("0", encoding="ascii")
    monkeypatch.setattr(isolated_refresh, "_is_posix_platform", lambda: True)

    assert isolated_refresh._prefer_worker_as_oom_victim(score_path) is True
    assert score_path.read_text(encoding="ascii") == str(
        isolated_refresh.WORKER_OOM_SCORE_ADJ
    )


def test_worker_oom_preference_fails_closed_when_kernel_value_is_not_applied(
    monkeypatch,
):
    class _NoopWriter:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, _value):
            return None

        def read(self):
            return "0"

    monkeypatch.setattr(isolated_refresh, "_is_posix_platform", lambda: True)
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: _NoopWriter())

    assert isolated_refresh._prefer_worker_as_oom_victim() is False


def test_posix_worker_refuses_heavy_import_without_oom_preference(monkeypatch):
    monkeypatch.setattr(isolated_refresh, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(
        isolated_refresh,
        "_prefer_worker_as_oom_victim",
        lambda: False,
    )

    with pytest.raises(RuntimeError, match="OOM isolation"):
        isolated_refresh._require_worker_oom_preference()
