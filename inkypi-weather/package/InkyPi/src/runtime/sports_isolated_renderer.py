"""Bounded parent-side orchestration for Sports Dashboard rendering."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import threading

from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from runtime.long_task_executor import LongTaskExecutor
from runtime.refresh_contracts import TaskCancelled, thaw_payload
from utils.safe_image import ImageLimits, safe_open_image


SPORTS_REGION_TASK = "sports_dashboard_region"
SPORTS_REGIONS = ("football", "lower", "esports")
# Keep the PNG plus settings safely below LongTaskExecutor's 2 MiB input cap
# when the previous region becomes the next child payload.
SPORTS_RESULT_MAX_BYTES = 1536 * 1024
DEFAULT_RESOURCE_POLL_SECONDS = 0.25
MIN_POSIX_WORKER_OOM_SCORE_ADJ = 800
_EXECUTOR = None
_EXECUTOR_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


class SportsIsolatedResourcePressure(TaskCancelled):
    """The child was stopped before system pressure could threaten the parent."""


def _get_executor():
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None or _EXECUTOR.closed:
            _EXECUTOR = LongTaskExecutor(
                {
                    SPORTS_REGION_TASK: (
                        "plugins.sports_dashboard.isolated_refresh:"
                        "render_sports_region_task"
                    )
                },
                max_workers=1,
                max_queue=0,
                poll_interval_seconds=0.05,
                terminate_grace_seconds=0.25,
                register_global=True,
            )
        return _EXECUTOR


def _finite_metric(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _resource_margin_available(sample, *, min_available_mb, max_swap_percent):
    available_mb = _finite_metric(getattr(sample, "available_mb", None))
    swap_percent = _finite_metric(getattr(sample, "swap_percent", None))
    if available_mb is None or swap_percent is None:
        return False
    return (
        available_mb >= float(min_available_mb)
        and swap_percent < float(max_swap_percent)
    )


def _device_payload(device_config):
    values = {}
    for key in ("resolution", "orientation", "width", "height", "timezone"):
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
        env_file = os.environ.get("INKYPI_ENV_FILE")
    return values, None if env_file is None else str(env_file)


def _wait_for_result(
    handle,
    *,
    context,
    resource_sampler,
    abort_min_available_mb,
    abort_max_swap_percent,
    poll_seconds,
):
    while True:
        context.raise_if_cancelled()
        try:
            sample = resource_sampler()
        except Exception:
            sample = None
        if not _resource_margin_available(
            sample,
            min_available_mb=abort_min_available_mb,
            max_swap_percent=abort_max_swap_percent,
        ):
            handle.cancel()
            try:
                handle.result(timeout=2)
            except TimeoutError:
                pass
            raise SportsIsolatedResourcePressure(
                "isolated Sports Dashboard worker stopped at the resource guard"
            )
        try:
            return handle.result(timeout=poll_seconds)
        except TimeoutError:
            continue


def render_sports_dashboard_isolated(
    *,
    settings,
    device_config,
    resolved_theme_context,
    context,
    instance_identity,
    identity_validator=None,
    resource_sampler,
    start_min_available_mb,
    start_max_swap_percent,
    abort_min_available_mb,
    abort_max_swap_percent,
    now,
):
    """Render three regions in separate children and attest the final image."""

    render_settings = thaw_payload(settings or {})
    # This marker is intentionally an in-process object and cannot cross the
    # primitive-only process boundary. Region rendering does not consume it.
    render_settings.pop("_inkypi_presentation_instance_identity", None)
    # Browser screenshots can leave a detached Chromium process behind if this
    # worker is terminated. Structured/cache fallbacks stay inside the worker.
    render_settings["worldCupScreenshotFallback"] = False
    if resolved_theme_context is not None:
        render_settings["_inkypi_theme"] = thaw_payload(resolved_theme_context)
    device_values, env_file = _device_payload(device_config)
    cache_identity = hashlib.sha256(
        (
            f"{instance_identity.instance_uuid or ''}:"
            f"{instance_identity.structural_generation}:"
            f"{instance_identity.settings_revision}"
        ).encode("utf-8")
    ).hexdigest()[:24]
    executor = _get_executor()
    base_png = None
    panel_provenances = []
    final_value = None

    for region in SPORTS_REGIONS:
        context.raise_if_cancelled()
        try:
            start_sample = resource_sampler()
        except Exception:
            start_sample = None
        if not _resource_margin_available(
            start_sample,
            min_available_mb=start_min_available_mb,
            max_swap_percent=start_max_swap_percent,
        ):
            raise SportsIsolatedResourcePressure(
                "isolated Sports Dashboard worker deferred before the next region"
            )
        payload = {
            "region": region,
            "settings": render_settings,
            "device_config": device_values,
            "env_file": env_file,
            "now": now.isoformat(),
            "base_png": base_png,
            "panel_provenances": list(panel_provenances),
            "finalize": region == SPORTS_REGIONS[-1],
            "cache_identity": cache_identity,
            "timeout_seconds": context.remaining_seconds(),
        }
        handle = executor.submit(
            SPORTS_REGION_TASK,
            payload,
            context=context,
            instance_identity=instance_identity,
            identity_validator=identity_validator,
        )
        result = _wait_for_result(
            handle,
            context=context,
            resource_sampler=resource_sampler,
            abort_min_available_mb=abort_min_available_mb,
            abort_max_swap_percent=abort_max_swap_percent,
            poll_seconds=DEFAULT_RESOURCE_POLL_SECONDS,
        )
        if result.status != "succeeded":
            raise RuntimeError(
                f"isolated Sports Dashboard {region} region failed: "
                f"{result.error_code or result.status}"
            )
        value = result.value
        if not isinstance(value, dict):
            raise RuntimeError("isolated Sports Dashboard returned an invalid result")
        if value.get("region") != region:
            raise RuntimeError("isolated Sports Dashboard returned the wrong region")
        if os.name == "posix":
            worker_oom_score_adj = value.get("worker_oom_score_adj")
            worker_pid = value.get("worker_pid")
            if (
                type(worker_oom_score_adj) is not int
                or worker_oom_score_adj < MIN_POSIX_WORKER_OOM_SCORE_ADJ
                or type(worker_pid) is not int
                or worker_pid <= 1
            ):
                raise RuntimeError(
                    "isolated Sports Dashboard returned invalid OOM isolation evidence"
                )
            logger.info(
                "Sports Dashboard isolated region completed. | region: %s | "
                "worker_pid: %s | worker_oom_score_adj: %s",
                region,
                worker_pid,
                worker_oom_score_adj,
            )
        try:
            provenance = SourceProvenance(value.get("region_provenance"))
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "isolated Sports Dashboard returned invalid provenance"
            ) from error
        image_png = value.get("image_png")
        if not isinstance(image_png, bytes) or len(image_png) > SPORTS_RESULT_MAX_BYTES:
            raise RuntimeError("isolated Sports Dashboard returned an invalid image")
        base_png = image_png
        panel_provenances.append(provenance.value)
        final_value = value

    image = safe_open_image(
        base_png,
        limits=ImageLimits(
            max_bytes=SPORTS_RESULT_MAX_BYTES,
            max_width=2048,
            max_height=2048,
            max_pixels=4_000_000,
            allowed_formats=frozenset({"PNG"}),
        ),
    ).convert("RGB")
    try:
        composite = SourceProvenance(final_value.get("composite_provenance"))
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError(
            "isolated Sports Dashboard returned invalid composite provenance"
        ) from error
    attach_source_provenance(image, composite)
    if final_value.get("skip_cache"):
        image.info["inkypi_skip_cache"] = True
    theme_mode = final_value.get("theme_mode")
    if theme_mode in {"day", "night"}:
        image.info["inkypi_theme_mode"] = theme_mode
    return image
