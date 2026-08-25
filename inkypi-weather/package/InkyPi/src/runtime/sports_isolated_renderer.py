"""Bounded parent-side orchestration for Sports Dashboard rendering."""

from __future__ import annotations

import gc
import hashlib
import json
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
from runtime.sports_region_checkpoint import (
    SPORTS_REGION_ORDER,
    SportsRegionCheckpointStore,
)
from utils.safe_image import ImageLimits, safe_open_image
from utils.theme_utils import normalize_palette_colors


SPORTS_REGION_TASK = "sports_dashboard_region"
# Run the heaviest/provider-dense region before earlier results and allocator
# fragmentation reduce the child-process safety margin on 416 MiB devices.
SPORTS_REGIONS = SPORTS_REGION_ORDER
# Keep the PNG plus settings safely below LongTaskExecutor's 2 MiB input cap
# when the previous region becomes the next child payload.
SPORTS_RESULT_MAX_BYTES = 1536 * 1024
DEFAULT_RESOURCE_POLL_SECONDS = 0.25
MIN_POSIX_WORKER_OOM_SCORE_ADJ = 800
_FORCE_REFRESH_SETTING_KEYS = (
    "forceRefresh",
    "force_refresh",
    "refreshNow",
    "retry",
)
_DISPLAY_RENDER_SETTING = "_inkypiDisplayRender"
_EXECUTOR = None
_EXECUTOR_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


class SportsIsolatedResourcePressure(TaskCancelled):
    """The child was stopped before system pressure could threaten the parent."""


class SportsIsolatedCheckpointPending(TaskCancelled):
    """One permitted region completed and the durable render remains incomplete."""

    def __init__(self, *, fingerprint, completed_regions, next_region):
        self.fingerprint = str(fingerprint)
        self.completed_regions = tuple(completed_regions)
        self.next_region = str(next_region)
        super().__init__(
            "isolated Sports Dashboard checkpoint is awaiting region "
            f"{self.next_region}"
        )


def _release_parent_transient_memory():
    """Return process-boundary allocations before starting the next region."""

    collected_objects = 0
    malloc_trimmed = False
    try:
        collected_objects = gc.collect()
    except Exception:
        logger.debug(
            "Sports Dashboard parent garbage collection failed.",
            exc_info=True,
        )
    if os.name == "posix":
        try:
            import ctypes

            malloc_trim = getattr(ctypes.CDLL("libc.so.6"), "malloc_trim", None)
            if malloc_trim is not None:
                malloc_trimmed = bool(malloc_trim(0))
        except Exception:
            logger.debug(
                "Sports Dashboard parent malloc_trim is unavailable.",
                exc_info=True,
            )
    return collected_objects, malloc_trimmed


def _require_worker_isolation_evidence(value):
    if os.name != "posix":
        return None, None
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
    return worker_pid, worker_oom_score_adj


def _get_executor():
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None or _EXECUTOR.closed:
            _EXECUTOR = LongTaskExecutor(
                {
                    SPORTS_REGION_TASK: (
                        "plugins.sports_dashboard.isolated_refresh:"
                        "render_sports_region_task"
                    ),
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


def _normalized_bool_setting(settings, key):
    value = settings.get(key)
    if value is None:
        return False
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _render_semantics_digest(render_settings):
    """Hash only normalized, non-secret settings that alter this render."""

    document = {
        "force_refresh": any(
            _normalized_bool_setting(render_settings, key)
            for key in _FORCE_REFRESH_SETTING_KEYS
        ),
        "display_render": _normalized_bool_setting(
            render_settings,
            _DISPLAY_RENDER_SETTING,
        ),
        "theme": render_settings.get("_inkypi_theme"),
        "world_cup_screenshot_fallback": _normalized_bool_setting(
            render_settings,
            "worldCupScreenshotFallback",
        ),
        "sports_low_memory": _normalized_bool_setting(
            render_settings,
            "_inkypi_sports_low_memory",
        ),
    }
    encoded = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checkpoint_fingerprint(
    instance_identity,
    device_values,
    render_semantics_digest,
    attempt_token,
):
    """Bind a checkpoint to non-secret render identity and presentation facts."""

    if attempt_token is None:
        attempt_token_digest = None
    else:
        if (
            type(attempt_token) is not str
            or not attempt_token
            or len(attempt_token) > 256
        ):
            raise ValueError("Sports checkpoint attempt token is invalid")
        attempt_token_digest = hashlib.sha256(
            attempt_token.encode("utf-8")
        ).hexdigest()
    document = {
        "contract": "sports-isolated-regions-v3",
        "instance_uuid_hash": hashlib.sha256(
            str(instance_identity.instance_uuid or "").encode("utf-8")
        ).hexdigest(),
        "structural_generation": instance_identity.structural_generation,
        "settings_revision": instance_identity.settings_revision,
        "device": {
            key: device_values.get(key)
            for key in ("resolution", "orientation", "width", "height", "timezone")
        },
        "render_semantics_digest": render_semantics_digest,
        "attempt_token_digest": attempt_token_digest,
        "regions": SPORTS_REGIONS,
    }
    encoded = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        try:
            context.raise_if_cancelled()
        except TaskCancelled:
            # LongTaskExecutor.cancel() drives the existing child
            # terminate/kill/join path.  Wait briefly for that coordinator so
            # a canceled permit cannot leave a Sports worker overlapping the
            # next one.
            handle.cancel()
            try:
                handle.result(timeout=2)
            except TimeoutError:
                pass
            raise
        try:
            sample = resource_sampler()
        except Exception:
            sample = None
        if not _resource_margin_available(
            sample,
            min_available_mb=abort_min_available_mb,
            max_swap_percent=abort_max_swap_percent,
        ):
            available_mb = _finite_metric(getattr(sample, "available_mb", None))
            swap_percent = _finite_metric(getattr(sample, "swap_percent", None))
            logger.warning(
                "Sports Dashboard isolated resource guard tripped. | "
                "available_mb: %s | swap_percent: %s | minimum_available_mb: %s "
                "| maximum_swap_percent: %s",
                "unknown" if available_mb is None else f"{available_mb:.1f}",
                "unknown" if swap_percent is None else f"{swap_percent:.1f}",
                abort_min_available_mb,
                abort_max_swap_percent,
            )
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
    attempt_token=None,
    checkpoint_store: SportsRegionCheckpointStore | None = None,
):
    """Render three regions in separate children and attest the final image."""

    render_settings = thaw_payload(settings or {})
    # This marker is intentionally an in-process object and cannot cross the
    # primitive-only process boundary. Region rendering does not consume it.
    render_settings.pop("_inkypi_presentation_instance_identity", None)
    # Browser screenshots can leave a detached Chromium process behind if this
    # worker is terminated. Structured/cache fallbacks stay inside the worker.
    render_settings["worldCupScreenshotFallback"] = False
    # Keep provider payloads bounded on 416 MiB-class devices.
    render_settings["_inkypi_sports_low_memory"] = True
    if resolved_theme_context is not None:
        render_settings["_inkypi_theme"] = normalize_palette_colors(
            thaw_payload(resolved_theme_context)
        )
    device_values, env_file = _device_payload(device_config)
    if checkpoint_store is None:
        checkpoint_store = SportsRegionCheckpointStore.for_device(
            device_config,
            instance_identity,
        )
    checkpoint_fingerprint = _checkpoint_fingerprint(
        instance_identity,
        device_values,
        _render_semantics_digest(render_settings),
        attempt_token,
    )
    cache_identity = hashlib.sha256(
        (
            f"{instance_identity.instance_uuid or ''}:"
            f"{instance_identity.structural_generation}:"
            f"{instance_identity.settings_revision}"
        ).encode("utf-8")
    ).hexdigest()[:24]
    executor = _get_executor()
    base_png = None
    panel_provenances = {}
    final_value = None
    completed_regions = ()
    render_now = now.isoformat()
    checkpoint = (
        None
        if checkpoint_store is None
        else checkpoint_store.load(checkpoint_fingerprint, now=now)
    )
    if checkpoint is not None:
        base_png = checkpoint.base_png
        panel_provenances = dict(checkpoint.panel_provenances)
        final_value = checkpoint.final_value
        completed_regions = checkpoint.completed_regions
        render_now = checkpoint.render_now

    regions_to_run = SPORTS_REGIONS[len(completed_regions) :]
    if checkpoint_store is not None:
        regions_to_run = regions_to_run[:1]
    for region in regions_to_run:
        context.raise_if_cancelled()
        collected_objects, malloc_trimmed = _release_parent_transient_memory()
        logger.info(
            "Sports Dashboard parent memory maintenance completed. | "
            "before_region: %s | collected_objects: %s | malloc_trim: %s",
            region,
            collected_objects,
            malloc_trimmed,
        )
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
            "now": render_now,
            "base_png": base_png,
            "panel_provenances": dict(panel_provenances),
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
            worker_pid, worker_oom_score_adj = _require_worker_isolation_evidence(
                value
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
        panel_provenances[region] = provenance.value
        if region == SPORTS_REGIONS[-1]:
            try:
                composite_provenance = SourceProvenance(
                    value.get("composite_provenance")
                ).value
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "isolated Sports Dashboard returned invalid composite provenance"
                ) from error
            skip_cache = value.get("skip_cache")
            if type(skip_cache) is not bool:
                raise RuntimeError(
                    "isolated Sports Dashboard returned invalid cache policy"
                )
            theme_mode = value.get("theme_mode")
            if theme_mode is not None and (
                type(theme_mode) is not str or theme_mode not in ("day", "night")
            ):
                raise RuntimeError(
                    "isolated Sports Dashboard returned invalid theme mode"
                )
            final_value = {
                "composite_provenance": composite_provenance,
                "skip_cache": skip_cache,
                "theme_mode": theme_mode,
            }
        if checkpoint_store is not None:
            completed_regions = SPORTS_REGIONS[: SPORTS_REGIONS.index(region) + 1]
            checkpoint_store.save(
                fingerprint=checkpoint_fingerprint,
                completed_regions=completed_regions,
                base_png=base_png,
                panel_provenances=panel_provenances,
                render_now=render_now,
                final_value=final_value,
            )
            if region != SPORTS_REGIONS[-1]:
                raise SportsIsolatedCheckpointPending(
                    fingerprint=checkpoint_fingerprint,
                    completed_regions=completed_regions,
                    next_region=SPORTS_REGIONS[len(completed_regions)],
                )
        # Drop the completed process-boundary graph before the next iteration.
        # The next maintenance pass can then return its allocator pages to Linux
        # instead of carrying each region's transient heap into the next worker.
        payload = None
        handle = None
        result = None
        value = None
        image_png = None

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
    if checkpoint_store is not None:
        # Retain the complete checkpoint until the composite has been decoded
        # and attested.  Once this return value is safe to promote, the staged
        # regions are no longer needed for a later permit.
        checkpoint_store.clear()
    return image
