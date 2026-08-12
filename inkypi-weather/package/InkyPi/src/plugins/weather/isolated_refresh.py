"""One-shot Weather generation with a descriptor-validated PNG hand-off."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import tempfile
import threading
import time
from uuid import uuid4

from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
    read_source_provenance,
)
from plugins.context_cache import write_context
from runtime.long_task_executor import (
    InstanceIdentity,
    LongTaskExecutor,
    bind_long_task_runtime,
)
from runtime.presentation_cache import (
    PreparedPresentationCandidate,
    PresentationCache,
    prepared_presentation_path,
)
from runtime.refresh_contracts import TaskCancelled
from runtime.resource_deferral import ResourcePressureDeferred
from runtime.resource_governor import CHROMIUM
from utils.app_utils import resolve_dimensions
from utils.theme_utils import (
    EFFECTIVE_THEME_CONTEXT_INFO_KEY,
    is_valid_effective_theme_context,
    normalize_palette_colors,
)


WEATHER_TASK_NAME = "weather_full_generation"
WEATHER_CONTEXT_TTL_SECONDS = 2 * 60 * 60
WEATHER_CONTEXT_MAX_BYTES = 64 * 1024
WEATHER_CANCEL_REAP_SECONDS = 12.0
WORKER_OOM_SCORE_ADJ = 800
_PRESENTATION_OUTCOME = "presentation"
_RESOURCE_DEFERRAL_OUTCOME = "resource_pressure_deferred"
_RESOURCE_DEFERRAL_CODE = "resource_pressure_deferred"
_RESOURCE_DEFERRAL_REASONS = frozenset({"browser_resource_pressure"})
_RESOURCE_DEFERRAL_PHASES = frozenset({"start", "in_flight"})
_RESOURCE_DEFERRAL_ERROR_KEYS = frozenset(
    {
        "code",
        "reason",
        "phase",
        "available_mb",
        "swap_percent",
    }
)
_RESOURCE_DEFERRAL_RESULT_KEYS = frozenset(
    {
        "outcome",
        "error",
        "worker_pid",
        "worker_oom_score_adj",
    }
)
_WEATHER_SETTING_KEYS = frozenset(
    {
        "latitude",
        "longitude",
        "units",
        "weatherProvider",
        "titleSelection",
        "customTitle",
        "weatherTimeZone",
        "displayForecast",
        "displayGraph",
        "displayGraphIcons",
        "displayMetrics",
        "displayRain",
        "displayRefreshTime",
        "forecastDays",
        "graphIconStep",
        "moonPhase",
        "themeMode",
        "theme_mode",
        "backgroundOption",
        "backgroundColor",
        "backgroundImageFile",
        "textColor",
        "selectedFrame",
        "forceRefresh",
        "force_refresh",
        "_inkypiDisplayRender",
        "_inkypi_theme",
        "_theme_render_only",
    }
)
_WEATHER_CONTEXT_KEYS = frozenset(
    {
        "kind",
        "source",
        "summary",
        "facts",
        "forecast",
        "icon_code",
        "background_slug",
        "weather_background_slug",
        "astronomy",
    }
)
_EXECUTOR = None
_EXECUTOR_LOCK = threading.Lock()


def _get_executor():
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None or _EXECUTOR.closed:
            _EXECUTOR = LongTaskExecutor(
                {
                    WEATHER_TASK_NAME: (
                        "plugins.weather.isolated_refresh:generate_weather_task"
                    )
                },
                max_workers=1,
                max_queue=0,
                terminate_grace_seconds=5.0,
                register_global=True,
            )
    return _EXECUTOR


def _is_posix_platform():
    return os.name == "posix"


def _prefer_worker_as_oom_victim(score_path="/proc/self/oom_score_adj"):
    if not _is_posix_platform():
        return None
    try:
        with open(score_path, "w", encoding="ascii") as handle:
            handle.write(str(WORKER_OOM_SCORE_ADJ))
        with open(score_path, "r", encoding="ascii") as handle:
            applied_value = int(handle.read().strip())
        return (
            applied_value
            if applied_value >= WORKER_OOM_SCORE_ADJ
            else None
        )
    except (OSError, TypeError, ValueError):
        return None


def _require_worker_oom_preference():
    applied_value = _prefer_worker_as_oom_victim()
    if _is_posix_platform() and applied_value is None:
        raise RuntimeError(
            "isolated Weather worker could not establish OOM isolation"
        )
    return applied_value


class _WorkerDeviceConfig:
    """Minimal child-side config; secrets are resolved from the env file."""

    def __init__(self, values, env_file=None):
        self._values = dict(values or {})
        self._env_values = dict(os.environ)
        if env_file:
            try:
                from dotenv import dotenv_values

                self._env_values.update(
                    {
                        key: value
                        for key, value in dotenv_values(env_file).items()
                        if value is not None
                    }
                )
            except OSError:
                pass

    def get_config(self, key, default=None):
        return self._values.get(key, default)

    def get_resolution(self):
        resolution = self._values.get("resolution") or (800, 480)
        return int(resolution[0]), int(resolution[1])

    def load_env_key(self, key):
        try:
            from secret_schema import SecretSchema

            candidates = SecretSchema.load().resolve_names(key)
        except (KeyError, OSError, ValueError):
            candidates = (key,)
        for candidate in candidates:
            value = self._env_values.get(candidate)
            if value:
                return value
        return ""


def _isolated_settings(settings):
    return {
        key: deepcopy(value)
        for key, value in dict(settings or {}).items()
        if key in _WEATHER_SETTING_KEYS
    }


def _device_payload(device_config):
    values = {}
    for key in (
        "resolution",
        "orientation",
        "width",
        "height",
        "timezone",
        "time_format",
        "theme_mode",
        "display_theme_mode",
        "themeMode",
    ):
        try:
            value = device_config.get_config(key, default=None)
        except TypeError:
            value = device_config.get_config(key, None)
        except Exception:
            value = None
        if value is not None:
            values[key] = value
    if "resolution" not in values:
        values["resolution"] = list(device_config.get_resolution())
    runtime_paths = getattr(device_config, "runtime_paths", None)
    env_file = getattr(runtime_paths, "env_file", None)
    if env_file is None:
        env_file = getattr(device_config, "env_file", None)
    if env_file is None:
        env_file = os.environ.get("INKYPI_ENV_FILE")
    return values, None if env_file is None else os.fspath(env_file)


def _staging_parent(device_config):
    cache_root = getattr(device_config, "cache_dir", None)
    if cache_root is None:
        runtime_paths = getattr(device_config, "runtime_paths", None)
        cache_root = getattr(runtime_paths, "cache_dir", None)
    if cache_root is None:
        cache_root = os.environ.get("INKYPI_CACHE_DIR")
    if cache_root is None:
        raise RuntimeError("Weather isolation requires a runtime cache directory.")
    root = Path(os.path.abspath(os.fspath(cache_root))) / "plugins" / "weather"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Weather isolation staging root is unsafe.")
    return root


def _context_handoff(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - _WEATHER_CONTEXT_KEYS:
        raise RuntimeError("isolated Weather returned an invalid context")
    copied = deepcopy(value)
    if copied.get("kind") != "weather":
        raise RuntimeError("isolated Weather returned the wrong context kind")
    try:
        encoded = json.dumps(copied, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RuntimeError("isolated Weather returned a non-JSON context") from error
    if len(encoded) > WEATHER_CONTEXT_MAX_BYTES:
        raise RuntimeError("isolated Weather context exceeded its hand-off budget")
    return copied


def _generated_at(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise RuntimeError("isolated Weather returned an invalid generation time") from error
    if parsed.tzinfo is None:
        raise RuntimeError("isolated Weather generation time must be timezone-aware")
    return parsed.isoformat()


def _load_attested_image(value, candidate, cache, expected_dimensions):
    if not isinstance(value, dict):
        raise RuntimeError("isolated Weather returned an invalid result")
    _validate_worker_process(value)
    expected_path = os.path.abspath(candidate.cache_path)
    try:
        returned_path = os.path.abspath(os.fspath(value.get("staging_path")))
    except (OSError, TypeError, ValueError) as error:
        raise RuntimeError("isolated Weather returned an invalid staging path") from error
    if os.path.normcase(returned_path) != os.path.normcase(expected_path):
        raise RuntimeError("isolated Weather returned the wrong staging path")
    if not cache.validate(candidate):
        raise RuntimeError("isolated Weather staging PNG failed validation")
    try:
        with open(expected_path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
    except OSError as error:
        raise RuntimeError("isolated Weather staging PNG became unavailable") from error
    if digest != value.get("sha256"):
        raise RuntimeError("isolated Weather staging PNG digest did not match")
    image = cache.load_image(candidate)
    if image is None:
        raise RuntimeError("isolated Weather staging PNG could not be decoded")
    dimensions = tuple(int(item) for item in expected_dimensions)
    if image.size != dimensions or image.size != (
        value.get("width"),
        value.get("height"),
    ):
        image.close()
        raise RuntimeError("isolated Weather staging PNG dimensions did not match")
    try:
        provenance = SourceProvenance(value.get("provenance"))
    except (TypeError, ValueError) as error:
        image.close()
        raise RuntimeError("isolated Weather returned invalid provenance") from error
    attach_source_provenance(image, provenance)
    if value.get("skip_cache") is True:
        image.info["inkypi_skip_cache"] = True
    theme_context = normalize_palette_colors(
        deepcopy(value.get("effective_theme_context"))
    )
    if not is_valid_effective_theme_context(theme_context):
        image.close()
        raise RuntimeError("isolated Weather returned an invalid theme context")
    image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY] = theme_context
    return image


def _validate_worker_process(value):
    worker_pid = value.get("worker_pid")
    if (
        type(worker_pid) is not int
        or worker_pid <= 1
        or worker_pid == os.getpid()
    ):
        raise RuntimeError("isolated Weather returned an invalid worker pid")
    oom_score = value.get("worker_oom_score_adj")
    if _is_posix_platform():
        if (
            type(oom_score) is not int
            or not WORKER_OOM_SCORE_ADJ <= oom_score <= 1000
        ):
            raise RuntimeError(
                "isolated Weather did not attest the required OOM preference"
            )
    elif oom_score is not None and (
        type(oom_score) is not int
        or not WORKER_OOM_SCORE_ADJ <= oom_score <= 1000
    ):
        raise RuntimeError("isolated Weather returned an invalid OOM preference")


def _pressure_metric(value, *, field_name, maximum=None):
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise RuntimeError(
            f"isolated Weather returned an invalid {field_name} metric"
        )
    normalized = float(value)
    if (
        not math.isfinite(normalized)
        or normalized < 0
        or (maximum is not None and normalized > maximum)
    ):
        raise RuntimeError(
            f"isolated Weather returned an invalid {field_name} metric"
        )
    return normalized


def _resource_deferral_metadata(*, reason, phase, available_mb, swap_percent):
    if type(reason) is not str or reason not in _RESOURCE_DEFERRAL_REASONS:
        raise RuntimeError(
            "isolated Weather returned an invalid resource pressure reason"
        )
    if type(phase) is not str or phase not in _RESOURCE_DEFERRAL_PHASES:
        raise RuntimeError(
            "isolated Weather returned an invalid resource pressure phase"
        )
    return {
        "code": _RESOURCE_DEFERRAL_CODE,
        "reason": reason,
        "phase": phase,
        "available_mb": _pressure_metric(
            available_mb,
            field_name="available memory",
            maximum=1_000_000,
        ),
        "swap_percent": _pressure_metric(
            swap_percent,
            field_name="swap percent",
            maximum=100,
        ),
    }


def _raise_for_worker_outcome(value):
    if not isinstance(value, dict):
        raise RuntimeError("isolated Weather returned an invalid result")
    outcome = value.get("outcome")
    if outcome == _PRESENTATION_OUTCOME:
        return
    if outcome != _RESOURCE_DEFERRAL_OUTCOME:
        raise RuntimeError("isolated Weather returned an invalid outcome")
    _validate_worker_process(value)
    if set(value) != _RESOURCE_DEFERRAL_RESULT_KEYS:
        raise RuntimeError(
            "isolated Weather returned invalid resource deferral fields"
        )
    error = value.get("error")
    if not isinstance(error, dict) or set(error) != _RESOURCE_DEFERRAL_ERROR_KEYS:
        raise RuntimeError(
            "isolated Weather returned invalid resource deferral metadata"
        )
    if error.get("code") != _RESOURCE_DEFERRAL_CODE:
        raise RuntimeError(
            "isolated Weather returned an invalid resource deferral code"
        )
    metadata = _resource_deferral_metadata(
        reason=error.get("reason"),
        phase=error.get("phase"),
        available_mb=error.get("available_mb"),
        swap_percent=error.get("swap_percent"),
    )
    raise ResourcePressureDeferred(
        reason=metadata["reason"],
        phase=metadata["phase"],
        available_mb=metadata["available_mb"],
        swap_percent=metadata["swap_percent"],
    )


def _worker_resource_deferral_result(error, *, worker_pid, oom_score_adj):
    metadata = _resource_deferral_metadata(
        reason=error.reason,
        phase=error.phase,
        available_mb=error.available_mb,
        swap_percent=error.swap_percent,
    )
    return {
        "outcome": _RESOURCE_DEFERRAL_OUTCOME,
        "error": metadata,
        "worker_pid": worker_pid,
        "worker_oom_score_adj": oom_score_adj,
    }


def render_weather_isolated(
    *,
    settings,
    device_config,
    context,
    instance_identity,
    identity_validator=None,
    executor=None,
    context_publisher=write_context,
):
    """Generate Weather in one child and return only an attested image copy."""

    if not isinstance(instance_identity, InstanceIdentity):
        raise TypeError("instance_identity must be an InstanceIdentity")
    if (
        not instance_identity.instance_uuid
        or instance_identity.structural_generation is None
        or instance_identity.settings_revision is None
    ):
        raise ValueError("Weather isolation requires a complete instance identity")
    context.raise_if_cancelled()
    staging_parent = _staging_parent(device_config)
    job_root = Path(tempfile.mkdtemp(prefix="isolated-", dir=staging_parent))
    request_id = uuid4().hex
    cache_path = prepared_presentation_path(
        job_root,
        instance_identity.instance_uuid,
        instance_identity.structural_generation,
        instance_identity.settings_revision,
        None,
        request_id,
    )
    candidate = PreparedPresentationCandidate(
        instance_uuid=instance_identity.instance_uuid,
        structural_generation=instance_identity.structural_generation,
        settings_revision=instance_identity.settings_revision,
        theme_mode=None,
        request_id=request_id,
        cache_path=cache_path,
    )
    cache = PresentationCache(job_root)
    device_values, env_file = _device_payload(device_config)
    payload = {
        "settings": _isolated_settings(settings),
        "device_config": device_values,
        "env_file": env_file,
        "presentation": {
            "cache_root": str(job_root),
            "instance_uuid": instance_identity.instance_uuid,
            "structural_generation": instance_identity.structural_generation,
            "settings_revision": instance_identity.settings_revision,
            "theme_mode": None,
            "request_id": request_id,
        },
        "timeout_seconds": context.remaining_seconds(),
    }
    handle = None
    image = None
    cleanup_safe = True
    try:
        handle = (executor or _get_executor()).submit(
            WEATHER_TASK_NAME,
            payload,
            context=context,
            instance_identity=instance_identity,
            identity_validator=identity_validator,
            resource_kinds=(CHROMIUM,),
        )
        result = handle.result(timeout=max(0.01, context.remaining_seconds()))
        if result.status == "stale" and result.error_code == "stale_instance":
            raise TaskCancelled("isolated Weather result became stale")
        if result.status != "succeeded":
            raise RuntimeError(
                "isolated Weather generation failed: "
                f"{result.error_code or result.status}"
            )
        context.raise_if_cancelled()
        value = result.value
        _raise_for_worker_outcome(value)
        image = _load_attested_image(
            value,
            candidate,
            cache,
            resolve_dimensions(device_config),
        )
        weather_context = _context_handoff(value.get("weather_context"))
        generated_at = _generated_at(value.get("generated_at"))
        if weather_context is not None and not context_publisher(
            "weather",
            weather_context,
            generated_at=generated_at,
            ttl_seconds=WEATHER_CONTEXT_TTL_SECONDS,
        ):
            image.close()
            image = None
            raise RuntimeError("Weather context publication failed.")
        return image
    except TimeoutError as error:
        if handle is not None:
            handle.cancel()
            cleanup_safe = False
            try:
                # LongTaskExecutor's POSIX terminate handler is allowed one
                # cleanup grace window and one final kill/reap window.  Do not
                # race a still-live child by unlinking its staging directory.
                handle.result(timeout=WEATHER_CANCEL_REAP_SECONDS)
            except TimeoutError as reap_error:
                raise RuntimeError(
                    "isolated Weather generation timed out before the worker "
                    "reached a terminal state"
                ) from reap_error
            cleanup_safe = True
        raise RuntimeError("isolated Weather generation timed out") from error
    finally:
        if cleanup_safe:
            cache.remove(candidate)
            shutil.rmtree(job_root, ignore_errors=True)


def generate_weather_task(payload, cancel_event):
    """Generate all Weather data and presentation state in one child."""

    worker_oom_score_adj = _require_worker_oom_preference()
    _raise_if_worker_cancelled(cancel_event)

    # Heavy imports happen only after the child has become earlyoom's preferred
    # victim.  The parent process never receives provider responses or secrets.
    from plugins.weather.weather import Weather
    from runtime.refresh_contracts import TaskContext
    from utils.browser_renderer import close_browser_renderer

    device_config = _WorkerDeviceConfig(
        payload.get("device_config"),
        payload.get("env_file"),
    )
    plugin = Weather({"id": "weather"})
    captured = {
        "weather_context": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    def capture_context(context_payload, generated_at):
        captured["weather_context"] = _context_handoff(context_payload)
        captured["generated_at"] = _generated_at(generated_at)
        return True

    plugin._weather_context_publisher = capture_context
    cache, candidate = _worker_presentation_candidate(payload)
    timeout_seconds = max(
        0.01,
        min(180.0, float(payload.get("timeout_seconds") or 180.0)),
    )
    child_context = TaskContext(
        cancel_event,
        time.monotonic() + timeout_seconds,
    )
    child_identity = InstanceIdentity(
        candidate.instance_uuid,
        candidate.structural_generation,
        candidate.settings_revision,
    )
    image = None
    published = False
    try:
        with _cancel_on_sigterm(cancel_event), bind_long_task_runtime(
            child_context,
            child_identity,
        ):
            image = plugin._generate_image_in_process(
                dict(payload.get("settings") or {}),
                device_config,
            )
            _raise_if_worker_cancelled(cancel_event)
            provenance = read_source_provenance(image)
            if provenance is None:
                raise RuntimeError("Weather worker image lacked source provenance")
            theme_context = normalize_palette_colors(
                deepcopy(image.info.get(EFFECTIVE_THEME_CONTEXT_INFO_KEY))
            )
            if not is_valid_effective_theme_context(theme_context):
                raise RuntimeError("Weather worker image lacked a valid theme context")

            # PresentationCache.save is the public descriptor-bound publish
            # operation in this checkout; it performs a bounded PNG encode and
            # atomic publish beneath the candidate's authoritative root.
            cache.save(candidate, image)
            published = True
            _raise_if_worker_cancelled(cancel_event)
            with open(candidate.cache_path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            result = {
                "outcome": _PRESENTATION_OUTCOME,
                "staging_path": candidate.cache_path,
                "sha256": digest,
                "width": image.width,
                "height": image.height,
                "provenance": provenance.value,
                "skip_cache": image.info.get("inkypi_skip_cache") is True,
                "effective_theme_context": theme_context,
                "weather_context": captured["weather_context"],
                "generated_at": captured["generated_at"],
                "worker_pid": os.getpid(),
                "worker_oom_score_adj": worker_oom_score_adj,
            }
            _raise_if_worker_cancelled(cancel_event)
            return result
    except ResourcePressureDeferred as error:
        if published:
            cache.remove(candidate)
        return _worker_resource_deferral_result(
            error,
            worker_pid=os.getpid(),
            oom_score_adj=worker_oom_score_adj,
        )
    except BaseException:
        if published:
            cache.remove(candidate)
        raise
    finally:
        if image is not None:
            image.close()
        close_browser_renderer()


def _worker_presentation_candidate(payload):
    descriptor = payload.get("presentation")
    if not isinstance(descriptor, dict):
        raise RuntimeError("Weather worker presentation descriptor is missing")
    cache_root = Path(os.path.abspath(os.fspath(descriptor.get("cache_root"))))
    request_id = descriptor.get("request_id")
    instance_uuid = descriptor.get("instance_uuid")
    structural_generation = descriptor.get("structural_generation")
    settings_revision = descriptor.get("settings_revision")
    theme_mode = descriptor.get("theme_mode")
    cache_path = prepared_presentation_path(
        cache_root,
        instance_uuid,
        structural_generation,
        settings_revision,
        theme_mode,
        request_id,
    )
    candidate = PreparedPresentationCandidate(
        instance_uuid=instance_uuid,
        structural_generation=structural_generation,
        settings_revision=settings_revision,
        theme_mode=theme_mode,
        request_id=request_id,
        cache_path=cache_path,
    )
    return PresentationCache(cache_root), candidate


def _raise_if_worker_cancelled(cancel_event):
    if cancel_event.is_set():
        from runtime.refresh_contracts import TaskCancelled

        raise TaskCancelled("isolated Weather generation was canceled")


@contextmanager
def _cancel_on_sigterm(cancel_event):
    if (
        not _is_posix_platform()
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    from runtime.refresh_contracts import TaskCancelled

    previous = signal.getsignal(signal.SIGTERM)

    def cancel_and_unwind(_signum, _frame):
        setter = getattr(cancel_event, "set", None)
        if callable(setter):
            setter()
        raise TaskCancelled("isolated Weather generation was terminated")

    signal.signal(signal.SIGTERM, cancel_and_unwind)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
