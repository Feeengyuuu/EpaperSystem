import hashlib
import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
    read_source_provenance,
)
from plugins.weather.isolated_refresh import (
    WEATHER_TASK_NAME,
    generate_weather_task,
    render_weather_isolated,
)
from plugins.weather.weather import Weather
from runtime.long_task_executor import (
    InstanceIdentity,
    LongTaskExecutor,
    LongTaskResult,
    bind_long_task_runtime,
)
from runtime.presentation_cache import prepared_presentation_path
from runtime.refresh_contracts import TaskCancelled, TaskContext
from runtime.resource_deferral import ResourcePressureDeferred
from runtime.resource_governor import CHROMIUM
from utils.theme_utils import EFFECTIVE_THEME_CONTEXT_INFO_KEY


class _CompletedHandle:
    def __init__(self, result):
        self._result = result

    def result(self, timeout=None):
        return self._result

    def cancel(self):
        return True


def _resource_pressure_envelope_task(_payload, _cancel_event):
    return {
        "outcome": "resource_pressure_deferred",
        "error": {
            "code": "resource_pressure_deferred",
            "reason": "browser_resource_pressure",
            "phase": "in_flight",
            "available_mb": 61.5,
            "swap_percent": 88.25,
        },
        "worker_pid": os.getpid(),
        "worker_oom_score_adj": 800,
    }


def _valid_weather_presentation_task(payload, _cancel_event):
    presentation = payload["presentation"]
    staging_path = Path(
        prepared_presentation_path(
            presentation["cache_root"],
            presentation["instance_uuid"],
            presentation["structural_generation"],
            presentation["settings_revision"],
            presentation["theme_mode"],
            presentation["request_id"],
        )
    )
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 480), (19, 37, 73)).save(staging_path, format="PNG")
    return {
        "outcome": "presentation",
        "staging_path": str(staging_path),
        "sha256": hashlib.sha256(staging_path.read_bytes()).hexdigest(),
        "width": 800,
        "height": 480,
        "provenance": SourceProvenance.LIVE.value,
        "skip_cache": False,
        "effective_theme_context": _theme_context(),
        "weather_context": {
            "kind": "weather",
            "source": "stale child",
            "summary": "must not publish",
            "facts": [],
            "forecast": [],
        },
        "generated_at": "2026-08-11T12:00:00-07:00",
        "worker_pid": os.getpid(),
        "worker_oom_score_adj": 800,
    }


class _SuccessfulExecutor:
    def __init__(self):
        self.submissions = []

    def submit(self, task_name, payload, **kwargs):
        self.submissions.append((task_name, payload, kwargs))
        presentation = payload["presentation"]
        staging_path = Path(
            prepared_presentation_path(
                presentation["cache_root"],
                presentation["instance_uuid"],
                presentation["structural_generation"],
                presentation["settings_revision"],
                presentation["theme_mode"],
                presentation["request_id"],
            )
        )
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 480), (19, 37, 73)).save(
            staging_path,
            format="PNG",
        )
        digest = hashlib.sha256(staging_path.read_bytes()).hexdigest()
        theme = _theme_context()
        return _CompletedHandle(
            LongTaskResult(
                "succeeded",
                value={
                    "outcome": "presentation",
                    "staging_path": str(staging_path),
                    "sha256": digest,
                    "width": 800,
                    "height": 480,
                    "provenance": SourceProvenance.LIVE.value,
                    "skip_cache": False,
                    "effective_theme_context": theme,
                    "weather_context": {
                        "kind": "weather",
                        "source": "Fremont, California",
                        "summary": "Fremont, California; current 24°C",
                        "facts": [
                            {"label": "Humidity", "value": "40%"},
                        ],
                        "forecast": [
                            {"day": "Tuesday", "high": "26", "low": "14"},
                        ],
                        "icon_code": "02d",
                        "background_slug": "cloudy",
                        "weather_background_slug": "cloudy",
                    },
                    "generated_at": "2026-08-11T12:00:00-07:00",
                    "worker_pid": os.getpid() + 10_000,
                    "worker_oom_score_adj": 800,
                },
            )
        )


class _FailedExecutor:
    def __init__(self):
        self.submissions = []

    def submit(self, task_name, payload, **kwargs):
        self.submissions.append((task_name, payload, kwargs))
        return _CompletedHandle(
            LongTaskResult(
                "failed",
                error_code="weather_provider_unavailable",
                error="isolated task failed",
            )
        )


class _TamperedExecutor(_SuccessfulExecutor):
    def submit(self, task_name, payload, **kwargs):
        handle = super().submit(task_name, payload, **kwargs)
        result = handle.result()
        value = dict(result.value)
        value["sha256"] = "0" * 64
        return _CompletedHandle(LongTaskResult("succeeded", value=value))


class _InvalidOomExecutor(_SuccessfulExecutor):
    def submit(self, task_name, payload, **kwargs):
        handle = super().submit(task_name, payload, **kwargs)
        result = handle.result()
        value = dict(result.value)
        value["worker_oom_score_adj"] = 799
        return _CompletedHandle(LongTaskResult("succeeded", value=value))


class _TerminalAfterCancelHandle:
    def __init__(self):
        self.cancelled = False
        self.terminal_waited = False

    def result(self, timeout=None):
        if not self.cancelled:
            raise TimeoutError("worker exceeded the parent deadline")
        self.terminal_waited = True
        return LongTaskResult("canceled", error_code="task_canceled")

    def cancel(self):
        self.cancelled = True
        return True


class _TimeoutExecutor:
    def __init__(self):
        self.handle = _TerminalAfterCancelHandle()

    def submit(self, *_args, **_kwargs):
        return self.handle


class _EnvelopeExecutor:
    def __init__(self, error):
        self.error = error

    def submit(self, *_args, **_kwargs):
        return _CompletedHandle(
            LongTaskResult(
                "succeeded",
                value={
                    "outcome": "resource_pressure_deferred",
                    "error": self.error,
                    "worker_pid": os.getpid() + 10_000,
                    "worker_oom_score_adj": 800,
                },
            )
        )


class _DeviceConfig:
    def __init__(self, root):
        self.cache_dir = root / "cache"
        self.runtime_paths = SimpleNamespace(env_file=root / "inkypi.env")

    def get_config(self, key, default=None):
        return {
            "resolution": [800, 480],
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
            "time_format": "24h",
            "theme_mode": "auto",
        }.get(key, default)

    def get_resolution(self):
        return 800, 480

    def load_env_key(self, _key):
        return ""


def _theme_context():
    return {
        "mode": "day",
        "requested_mode": "auto",
        "source": "weather",
        "reason": "sunrise/sunset",
        "resolved_at": "2026-08-11T12:00:00-07:00",
        "timezone": "America/Los_Angeles",
        "date": "2026-08-11",
        "sunrise": "2026-08-11T06:20:00-07:00",
        "sunset": "2026-08-11T20:02:00-07:00",
        "palette": {
            "background": [255, 255, 255],
            "panel": [255, 255, 255],
            "ink": [10, 12, 15],
            "muted": [74, 78, 84],
            "rule": [10, 12, 15],
            "accent": [33, 109, 157],
        },
        "css": {
            "background": "#ffffff",
            "panel": "#ffffff",
            "ink": "#0a0c0f",
            "muted": "#4a4e54",
            "rule": "#0a0c0f",
            "accent": "#216d9d",
        },
    }


def _context():
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + 10,
    )


def test_isolated_weather_validates_staging_before_publishing_context(tmp_path):
    executor = _SuccessfulExecutor()
    published = []
    identity = InstanceIdentity(str(uuid4()), 3, 7)

    image = render_weather_isolated(
        settings={
            "latitude": "37.5485",
            "longitude": "-121.9886",
            "units": "metric",
            "weatherProvider": "OpenMeteo",
            "titleSelection": "custom",
            "customTitle": "Fremont, California",
            "weatherTimeZone": "locationTimeZone",
            "displayForecast": "true",
            "forecastDays": "4",
        },
        device_config=_DeviceConfig(tmp_path),
        context=_context(),
        instance_identity=identity,
        executor=executor,
        context_publisher=lambda plugin_id, payload, **kwargs: (
            published.append((plugin_id, payload, kwargs)) or True
        ),
    )

    assert image.size == (800, 480)
    assert image.getpixel((0, 0)) == (19, 37, 73)
    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY]["mode"] == "day"
    assert published == [
        (
            "weather",
            {
                "kind": "weather",
                "source": "Fremont, California",
                "summary": "Fremont, California; current 24°C",
                "facts": [{"label": "Humidity", "value": "40%"}],
                "forecast": [
                    {"day": "Tuesday", "high": "26", "low": "14"},
                ],
                "icon_code": "02d",
                "background_slug": "cloudy",
                "weather_background_slug": "cloudy",
            },
            {
                "generated_at": "2026-08-11T12:00:00-07:00",
                "ttl_seconds": 2 * 60 * 60,
            },
        )
    ]
    assert executor.submissions[0][2]["instance_identity"] == identity
    assert executor.submissions[0][2]["resource_kinds"] == (CHROMIUM,)
    assert not list((tmp_path / "cache" / "plugins" / "weather").glob("**/*.png"))


@pytest.mark.parametrize(
    "executor",
    [_FailedExecutor(), _TamperedExecutor(), _InvalidOomExecutor()],
)
def test_isolated_weather_never_publishes_context_after_failed_attestation(
    tmp_path,
    executor,
):
    published = []

    with pytest.raises(RuntimeError):
        render_weather_isolated(
            settings={
                "latitude": "37.5485",
                "longitude": "-121.9886",
                "units": "metric",
                "weatherProvider": "OpenMeteo",
            },
            device_config=_DeviceConfig(tmp_path),
            context=_context(),
            instance_identity=InstanceIdentity(str(uuid4()), 3, 7),
            executor=executor,
            context_publisher=lambda *args, **kwargs: published.append(
                (args, kwargs)
            ),
        )

    assert published == []
    assert not list((tmp_path / "cache" / "plugins" / "weather").glob("**/*.png"))


def test_isolated_weather_payload_omits_secrets_and_raw_provider_data(tmp_path):
    executor = _FailedExecutor()

    with pytest.raises(RuntimeError):
        render_weather_isolated(
            settings={
                "latitude": "37.5485",
                "longitude": "-121.9886",
                "units": "metric",
                "weatherProvider": "OpenWeatherMap",
                "api_key": "do-not-cross-process-boundary",
                "token": "do-not-cross-process-boundary",
                "raw_provider_data": {"secret": "do-not-cross-process-boundary"},
            },
            device_config=_DeviceConfig(tmp_path),
            context=_context(),
            instance_identity=InstanceIdentity(str(uuid4()), 3, 7),
            executor=executor,
        )

    payload = executor.submissions[0][1]
    serialized_payload = repr(payload)
    assert "do-not-cross-process-boundary" not in serialized_payload
    assert set(payload["settings"]) == {
        "latitude",
        "longitude",
        "units",
        "weatherProvider",
    }


def test_isolated_weather_waits_for_terminal_cancel_before_staging_cleanup(tmp_path):
    executor = _TimeoutExecutor()

    with pytest.raises(RuntimeError, match="timed out"):
        render_weather_isolated(
            settings={
                "latitude": "37.5485",
                "longitude": "-121.9886",
                "units": "metric",
                "weatherProvider": "OpenMeteo",
            },
            device_config=_DeviceConfig(tmp_path),
            context=_context(),
            instance_identity=InstanceIdentity(str(uuid4()), 3, 7),
            executor=executor,
        )

    assert executor.handle.cancelled is True
    assert executor.handle.terminal_waited is True
    assert list((tmp_path / "cache" / "plugins" / "weather").iterdir()) == []


def test_resource_pressure_metadata_crosses_process_boundary_as_typed_deferral(
    tmp_path,
):
    executor = LongTaskExecutor(
        {WEATHER_TASK_NAME: _resource_pressure_envelope_task},
        max_queue=0,
    )
    try:
        with pytest.raises(ResourcePressureDeferred) as caught:
            render_weather_isolated(
                settings={
                    "latitude": "37.5485",
                    "longitude": "-121.9886",
                    "units": "metric",
                    "weatherProvider": "OpenMeteo",
                },
                device_config=_DeviceConfig(tmp_path),
                context=_context(),
                instance_identity=InstanceIdentity(str(uuid4()), 3, 7),
                executor=executor,
            )
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 5)

    assert caught.value.reason == "browser_resource_pressure"
    assert caught.value.phase == "in_flight"
    assert caught.value.available_mb == 61.5
    assert caught.value.swap_percent == 88.25
    assert not list((tmp_path / "cache" / "plugins" / "weather").glob("**/*.png"))


def test_stale_identity_after_real_child_result_is_cancellation_without_publish(
    tmp_path,
):
    executor = LongTaskExecutor(
        {WEATHER_TASK_NAME: _valid_weather_presentation_task},
        max_queue=0,
    )
    identity = InstanceIdentity(str(uuid4()), 3, 7)
    validated = []
    published = []

    def identity_validator(candidate):
        validated.append(candidate)
        return False

    try:
        with pytest.raises(TaskCancelled, match="stale"):
            render_weather_isolated(
                settings={
                    "latitude": "37.5485",
                    "longitude": "-121.9886",
                    "units": "metric",
                    "weatherProvider": "OpenMeteo",
                },
                device_config=_DeviceConfig(tmp_path),
                context=_context(),
                instance_identity=identity,
                identity_validator=identity_validator,
                executor=executor,
                context_publisher=lambda *args, **kwargs: published.append(
                    (args, kwargs)
                ),
            )
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 5)

    assert validated == [identity]
    assert published == []
    assert not list((tmp_path / "cache" / "plugins" / "weather").glob("**/*.png"))


@pytest.mark.parametrize(
    "error",
    [
        {
            "code": "builtins.ValueError",
            "reason": "browser_resource_pressure",
            "phase": "start",
            "available_mb": 61.5,
            "swap_percent": 88.25,
        },
        {
            "code": "resource_pressure_deferred",
            "reason": "arbitrary_remote_exception",
            "phase": "start",
            "available_mb": 61.5,
            "swap_percent": 88.25,
        },
        {
            "code": "resource_pressure_deferred",
            "reason": "browser_resource_pressure",
            "phase": "start",
            "available_mb": 61.5,
            "swap_percent": 88.25,
            "exception_class": "runtime.Evil",
        },
    ],
)
def test_resource_pressure_handoff_rejects_non_allowlisted_error_metadata(
    tmp_path,
    error,
):
    with pytest.raises(RuntimeError) as caught:
        render_weather_isolated(
            settings={
                "latitude": "37.5485",
                "longitude": "-121.9886",
                "units": "metric",
                "weatherProvider": "OpenMeteo",
            },
            device_config=_DeviceConfig(tmp_path),
            context=_context(),
            instance_identity=InstanceIdentity(str(uuid4()), 3, 7),
            executor=_EnvelopeExecutor(error),
        )

    assert type(caught.value) is RuntimeError


def test_weather_dispatches_full_generation_when_refresh_runtime_is_bound(
    tmp_path,
    monkeypatch,
):
    import plugins.weather.isolated_refresh as isolated_refresh

    observed = []
    sentinel = object()

    def fake_isolated(**kwargs):
        observed.append(kwargs)
        return sentinel

    monkeypatch.setattr(isolated_refresh, "render_weather_isolated", fake_isolated)
    context = _context()
    identity = InstanceIdentity(str(uuid4()), 4, 9)
    identity_validator = lambda candidate: candidate == identity
    plugin = Weather({"id": "weather"})
    settings = {
        "latitude": "37.5485",
        "longitude": "-121.9886",
        "units": "metric",
        "weatherProvider": "OpenMeteo",
    }

    with bind_long_task_runtime(context, identity, identity_validator):
        result = plugin.generate_image(settings, _DeviceConfig(tmp_path))

    assert result is sentinel
    assert observed[0]["settings"] is settings
    assert observed[0]["context"] is context
    assert observed[0]["instance_identity"] == identity
    assert observed[0]["identity_validator"] is identity_validator


def test_weather_worker_stages_only_attested_presentation_and_context(
    tmp_path,
    monkeypatch,
):
    import plugins.weather.isolated_refresh as isolated_refresh
    import plugins.weather.weather as weather_module

    generated_at = datetime.fromisoformat("2026-08-11T12:00:00-07:00")
    context_payload = {
        "kind": "weather",
        "source": "Fremont, California",
        "summary": "Fremont, California; current 24C",
        "facts": [{"label": "Humidity", "value": "40%"}],
        "forecast": [{"day": "Tuesday", "high": "26", "low": "14"}],
        "icon_code": "02d",
        "background_slug": "cloudy",
        "weather_background_slug": "cloudy",
    }

    def fake_generate(plugin, _settings, _device_config):
        assert plugin._publish_weather_context(context_payload, generated_at)
        image = Image.new("RGB", (800, 480), (19, 37, 73))
        image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY] = _theme_context()
        return attach_source_provenance(image, SourceProvenance.LIVE)

    monkeypatch.setattr(Weather, "_generate_image_in_process", fake_generate)
    monkeypatch.setattr(
        isolated_refresh,
        "_require_worker_oom_preference",
        lambda: 800,
    )
    monkeypatch.setattr(
        weather_module,
        "write_context",
        lambda *args, **kwargs: pytest.fail("worker wrote the live context cache"),
    )
    identity = InstanceIdentity(str(uuid4()), 3, 7)
    request_id = uuid4().hex
    job_root = tmp_path / "job"
    staging_path = Path(
        prepared_presentation_path(
            job_root,
            identity.instance_uuid,
            identity.structural_generation,
            identity.settings_revision,
            None,
            request_id,
        )
    )

    result = generate_weather_task(
        {
            "settings": {
                "latitude": "37.5485",
                "longitude": "-121.9886",
                "units": "metric",
                "weatherProvider": "OpenMeteo",
            },
            "device_config": {"resolution": [800, 480]},
            "env_file": str(tmp_path / "inkypi.env"),
            "presentation": {
                "cache_root": str(job_root),
                "instance_uuid": identity.instance_uuid,
                "structural_generation": identity.structural_generation,
                "settings_revision": identity.settings_revision,
                "theme_mode": None,
                "request_id": request_id,
            },
        },
        SimpleNamespace(is_set=lambda: False),
    )

    assert set(result) == {
        "outcome",
        "staging_path",
        "sha256",
        "width",
        "height",
        "provenance",
        "skip_cache",
        "effective_theme_context",
        "weather_context",
        "generated_at",
        "worker_pid",
        "worker_oom_score_adj",
    }
    assert result["staging_path"] == str(staging_path)
    assert result["outcome"] == "presentation"
    assert result["sha256"] == hashlib.sha256(staging_path.read_bytes()).hexdigest()
    assert result["weather_context"] == context_payload
    assert result["generated_at"] == generated_at.isoformat()
    assert result["provenance"] == SourceProvenance.LIVE.value
    with Image.open(staging_path) as staged:
        assert staged.size == (800, 480)


def test_weather_worker_encodes_only_allowlisted_resource_deferral_metadata(
    tmp_path,
    monkeypatch,
):
    import plugins.weather.isolated_refresh as isolated_refresh

    deferred = ResourcePressureDeferred(
        reason="browser_resource_pressure",
        phase="start",
        available_mb=47.25,
        swap_percent=92.5,
    )

    def fake_generate(_plugin, _settings, _device_config):
        raise deferred

    monkeypatch.setattr(Weather, "_generate_image_in_process", fake_generate)
    monkeypatch.setattr(
        isolated_refresh,
        "_require_worker_oom_preference",
        lambda: 800,
    )
    identity = InstanceIdentity(str(uuid4()), 3, 7)
    request_id = uuid4().hex
    job_root = tmp_path / "job"

    result = generate_weather_task(
        {
            "settings": {
                "latitude": "37.5485",
                "longitude": "-121.9886",
                "units": "metric",
                "weatherProvider": "OpenMeteo",
            },
            "device_config": {"resolution": [800, 480]},
            "env_file": str(tmp_path / "inkypi.env"),
            "presentation": {
                "cache_root": str(job_root),
                "instance_uuid": identity.instance_uuid,
                "structural_generation": identity.structural_generation,
                "settings_revision": identity.settings_revision,
                "theme_mode": None,
                "request_id": request_id,
            },
        },
        SimpleNamespace(is_set=lambda: False),
    )

    assert result == {
        "outcome": "resource_pressure_deferred",
        "error": {
            "code": "resource_pressure_deferred",
            "reason": "browser_resource_pressure",
            "phase": "start",
            "available_mb": 47.25,
            "swap_percent": 92.5,
        },
        "worker_pid": os.getpid(),
        "worker_oom_score_adj": 800,
    }
    assert not job_root.exists()
