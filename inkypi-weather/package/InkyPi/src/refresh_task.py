import threading
import time
import os
import logging
import math
import ctypes
import gc
import hashlib
from contextlib import nullcontext
from pathlib import Path
import shutil
import psutil
import pytz
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from plugins.plugin_registry import (
    get_plugin_instance,
    plugin_allows_display_triggered_provider_refresh,
    plugin_presentation_refresh_is_provider_free,
    plugin_supports_cached_display_redraw,
    plugin_supports_day_night_theme,
    plugin_supports_live_refresh,
    plugin_supports_presentation_refresh,
)
from plugins.plugin_settings import (
    PluginSettingError,
    resolve_refresh_on_display_for_config,
)
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationPreparation,
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from plugins.base_plugin.theme_presentation import apply_media_theme_chrome
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    read_source_provenance,
)
from utils.image_utils import compute_image_hash
from utils.app_utils import get_base_ui_font, resolve_dimensions
from utils.theme_utils import (
    EFFECTIVE_THEME_CONTEXT_INFO_KEY,
    get_theme_context,
    is_valid_effective_theme_context,
    resolve_plugin_theme,
)
from model import RefreshInfo, PlaylistManager
from runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobStatus,
    LifecycleState,
    RefreshCommand,
    RefreshIntent,
    TaskCancelled,
    TaskContext,
    TaskDeadlineExceeded,
    freeze_payload,
    thaw_payload,
)
from runtime.cache_catalog import (
    CacheCatalog,
    DisplayCacheCandidate,
    authoritative_cache_path,
)
from runtime.cache_lifecycle import (
    HARD_BUDGET,
    HEALTHY_BUDGET,
    SOFT_BUDGET,
    CacheLifecycleSnapshot,
    CacheLifecycleManager,
    DiskPressureTier,
    DiskThresholds,
    LifecycleAggregate,
    LifecycleAllowance,
    STALE_TEMP_SECONDS,
    build_cache_retention,
    classify_disk_pressure,
)
from runtime.presentation_cache import (
    PreparedPresentationCandidate,
    PresentationCache,
    prepared_presentation_path,
)
from runtime.refresh_queue import QueueEntry, RefreshQueue
from runtime.plugin_deferral import PluginRefreshDeferred
from runtime.resource_deferral import ResourcePressureDeferred
from runtime.ian import (
    Ian,
    IanExecutionResult,
    IanOfferStatus,
    IanResourceSample,
    IanTurnStatus,
)
from runtime.ian_refresh_adapter import refresh_command_to_ian_request
from runtime.refresh_policy import (
    AdmissionState,
    FirstDataDueTracker,
    DueCandidate,
    DueReason,
    ResourceSample,
    ResourceThresholds,
    ResourceTier,
    classify_resource_tier,
    choose_refresh_candidate,
    evaluate_data_due,
    evaluate_presentation_due,
    soft_spacing_deadline,
)
from runtime.refresh_progress import RefreshProgressTracker
from runtime.long_task_executor import InstanceIdentity, bind_long_task_runtime
from runtime.bounded_parallel_stage import BoundedParallelStageRunner
from runtime.execution_policy import ExecutionClass, plugin_execution_class
from runtime.resource_governor import RuntimeResourceGovernor
from runtime.render_arbiter import RenderArbiter
from runtime.sports_isolated_renderer import (
    SportsIsolatedCheckpointPending,
    SportsIsolatedResourcePressure,
    render_sports_dashboard_isolated,
)
from runtime.runtime_state import (
    InstanceRuntimeState,
    LastGoodCacheState,
    PresentationCommitReceipt,
    PresentationRequestState,
    RefreshLane,
    RuntimeStateStore,
)
from runtime.scheduler_state import LifecycleController, RetryRegistry, SchedulerState
from utils.browser_renderer import get_browser_renderer
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_RENDERER_INTENTS = frozenset(
    {
        RefreshIntent.DATA_REFRESH,
        RefreshIntent.PRESENTATION_REFRESH,
        RefreshIntent.LIVE_REFRESH,
        RefreshIntent.THEME_REDRAW,
        RefreshIntent.THEME_CATCHUP,
        RefreshIntent.MANUAL_RENDER,
    }
)
_HEAVYWEIGHT_RENDERER_PLUGIN_IDS = frozenset({"sports_dashboard"})
# Keep unbounded full-dashboard renders off 416 MiB-class devices by default.
DEFAULT_HEAVYWEIGHT_RENDERER_MIN_AVAILABLE_MB = 384
DEFAULT_HEAVYWEIGHT_RENDERER_MAX_SWAP_PERCENT = 30
DEFAULT_BACKGROUND_BURST_START_MIN_AVAILABLE_MB = 115
DEFAULT_SPORTS_ISOLATED_START_MIN_AVAILABLE_MB = (
    DEFAULT_BACKGROUND_BURST_START_MIN_AVAILABLE_MB
)
DEFAULT_SPORTS_ISOLATED_START_MAX_SWAP_PERCENT = 70
DEFAULT_TICKETMASTER_BACKGROUND_START_MIN_AVAILABLE_MB = (
    DEFAULT_BACKGROUND_BURST_START_MIN_AVAILABLE_MB
)
DEFAULT_TICKETMASTER_LIVENESS_STARVATION_SECONDS = 30 * 60
DEFAULT_TICKETMASTER_LIVENESS_WINDOW_SECONDS = 90
DEFAULT_TICKETMASTER_LIVENESS_COOLDOWN_SECONDS = 5 * 60
DEFAULT_WEATHER_BACKGROUND_START_MIN_AVAILABLE_MB = 150
DEFAULT_WEATHER_BACKGROUND_START_MAX_SWAP_PERCENT = 70
DEFAULT_WEATHER_LIVENESS_WINDOW_SECONDS = 90
DEFAULT_WEATHER_LIVENESS_COOLDOWN_SECONDS = 5 * 60
DEFAULT_WEATHER_LIVENESS_CONCESSION_MIN_AVAILABLE_MB = 140
MIN_WEATHER_RESOURCE_PRESSURE_DEFERRAL_SECONDS = 5 * 60
DEFAULT_BURST_LIVENESS_ORDINARY_YIELD_SECONDS = 30
# Preserve a measured safety margin above earlyoom's default 10% line; burst
# allocations can outrun the parent worker's resource polling interval.
DEFAULT_SPORTS_ISOLATED_ABORT_MIN_AVAILABLE_MB = 70
DEFAULT_SPORTS_ISOLATED_ABORT_MAX_SWAP_PERCENT = 75
DEFAULT_SPORTS_ISOLATED_LIVENESS_STARVATION_SECONDS = 30 * 60
DEFAULT_SPORTS_ISOLATED_LIVENESS_WINDOW_SECONDS = 90
DEFAULT_SPORTS_ISOLATED_LIVENESS_COOLDOWN_SECONDS = 5 * 60
DEFAULT_SPORTS_BACKGROUND_LIVE_MIN_INTERVAL_SECONDS = 5 * 60
MAX_RESOURCE_PRESSURE_DEFERRAL_SECONDS = 5 * 60
DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS = 5 * 60
DEFAULT_ROTATION_PRESENTATION_WAIT_SECONDS = 60
DEFAULT_ROTATION_PRESENTATION_DEADLINE_SECONDS = 5 * 60
DEFAULT_ROTATION_MAX_INTERVAL_SECONDS = 7 * 60
DEFAULT_ROTATION_BACKGROUND_GUARD_SECONDS = 2 * 60
DEFAULT_ROTATION_CACHE_RECOVERY_SECONDS = 30
DEFAULT_ROTATION_HARDWARE_BUDGET_SECONDS = 60
DEFAULT_ROTATION_DEADLINE_CLEANUP_SECONDS = 5
AUTOMATIC_ROTATION_DISPLAY_PRIORITY = 85
DEFAULT_ROTATION_SCHEDULER_POLL_SECONDS = 1
DEFAULT_IDLE_SCHEDULER_POLL_SECONDS = 30
DEFAULT_INDEPENDENT_REFRESH_STARVATION_SECONDS = 5 * 60
DEFAULT_MANUAL_UPDATE_TIMEOUT_SECONDS = 180
DEFAULT_MANUAL_UPDATE_JOB_RETENTION = 50
DEFAULT_BACKGROUND_CACHE_REFRESH_MAX_PER_PASS = 2
DEFAULT_BACKGROUND_CACHE_REFRESH_MIN_AVAILABLE_MB = 150
DEFAULT_BACKGROUND_CACHE_REFRESH_MAX_SWAP_PERCENT = 70
DEFAULT_MEMORY_MAINTENANCE_INTERVAL_SECONDS = 60
DEFAULT_MEMORY_WATCHDOG_MIN_AVAILABLE_MB = 70
DEFAULT_MEMORY_WATCHDOG_MAX_SWAP_PERCENT = 75
DEFAULT_MEMORY_WATCHDOG_CONFIRMATION_MAX_AVAILABLE_MB = 115
DEFAULT_MEMORY_WATCHDOG_PRESSURE_CONFIRMATION_SECONDS = 15
DEFAULT_MEMORY_WATCHDOG_RESTART_MIN_INTERVAL_SECONDS = 30 * 60
DEFAULT_THEME_REFRESH_RETRY_COOLDOWN_SECONDS = 10 * 60
DEFAULT_THEME_CATCHUP_RETRY_COOLDOWN_SECONDS = 10 * 60
DEFAULT_DISPLAY_REFRESH_MIN_AVAILABLE_MB = 150
DEFAULT_DISPLAY_REFRESH_MAX_SWAP_PERCENT = 30
DEFAULT_DISPLAY_TRIGGERED_REFRESH_ENABLED = False
DEFAULT_IAN_ADMISSION_RETRY_SECONDS = 1.0
DEFAULT_IAN_RETAINED_LIMIT = 16
SKIP_CACHE_IMAGE_INFO_KEY = "inkypi_skip_cache"
DISPLAY_RENDER_SETTING = "_inkypiDisplayRender"


@dataclass(frozen=True)
class ActiveOperationSnapshot:
    command_id: str
    kind: str
    source: str
    intent: str
    plugin_id: str
    instance_uuid: str | None
    started_monotonic: float
    deadline_monotonic: float


@dataclass(frozen=True)
class _SportsLivenessWindow:
    instance_uuid: str
    due_since: datetime
    started_monotonic: float
    deadline_monotonic: float


@dataclass(frozen=True)
class _TicketmasterLivenessWindow:
    instance_uuid: str
    due_since: datetime
    started_monotonic: float
    deadline_monotonic: float


@dataclass(frozen=True)
class _WeatherLivenessWindow:
    instance_uuid: str
    due_since: datetime
    started_monotonic: float
    deadline_monotonic: float
    candidate: DueCandidate


class _StaleSelection(TaskCancelled):
    """A rendered playlist result no longer matches its immutable selection."""


class _CacheUnavailable(TaskCancelled):
    """A previously eligible display cache disappeared or became invalid."""


class _PreparedDisplayFailure(RuntimeError):
    """A prepared image failed after selection and needs presentation retry."""

    def __init__(self, error):
        super().__init__(str(error))
        self.original_error = error


@dataclass(frozen=True)
class _PreparedDisplaySelection:
    candidate: PreparedPresentationCandidate
    request: PresentationRequestState
    theme_mode: str | None


@dataclass(frozen=True)
class _RotationDisplayCandidate:
    instance_uuid: str
    structural_generation: int
    settings_revision: int
    theme_mode: str | None
    presentation_request_id: str | None = None


def _setting_enabled(value):
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _display_triggered_refresh_enabled(device_config):
    """Return whether display state may trigger provider work or a follow-up write."""
    try:
        configured = device_config.get_config(
            "display_triggered_refresh_enabled",
            default=DEFAULT_DISPLAY_TRIGGERED_REFRESH_ENABLED,
        )
    except Exception:
        configured = DEFAULT_DISPLAY_TRIGGERED_REFRESH_ENABLED
    return _setting_enabled(configured)


def _live_display_refresh_enabled(device_config, plugin_id, _settings):
    """Keep Sports live updates cache-only; gate other live re-display globally."""
    if str(plugin_id).strip() == "sports_dashboard":
        return False
    return _display_triggered_refresh_enabled(device_config)


def _presentation_refresh_enabled(device_config, plugin_config):
    """Allow audited per-plugin exceptions without reopening provider work globally."""

    return (
        _display_triggered_refresh_enabled(device_config)
        or plugin_presentation_refresh_is_provider_free(plugin_config)
        or plugin_allows_display_triggered_provider_refresh(plugin_config)
    )


def _settings_with_force_refresh(settings, force=False, display_render=False):
    merged = dict(settings or {})
    if force:
        merged["forceRefresh"] = True
        merged["force_refresh"] = True
    if display_render:
        merged[DISPLAY_RENDER_SETTING] = True
    return merged


def _resolved_theme_context_for_instance(
    instance,
    plugin_config,
    device_config,
    *,
    current_dt=None,
):
    """Resolve immutable instance theme metadata without loading plugin code."""
    if not plugin_supports_day_night_theme(plugin_config):
        return None
    manifest = plugin_config.get("_manifest") if plugin_config else None
    manifest_theme = getattr(manifest, "theme", None)
    palette = None
    if manifest_theme is not None:
        palette = {
            "day": manifest_theme.day,
            "night": manifest_theme.night,
        }
    return resolve_plugin_theme(
        thaw_payload(instance.settings),
        device_config,
        now=current_dt,
        palette=palette,
    )


def _resolved_theme_mode(payload):
    context = payload.get("resolved_theme_context")
    if not isinstance(context, Mapping):
        return None
    mode = context.get("mode")
    return mode if mode in {"day", "night"} else None


def _plugin_live_refresh_state(plugin, settings, current_dt, plugin_id=None):
    hook = getattr(plugin, "get_live_refresh_state", None)
    if not callable(hook):
        return None
    try:
        state = hook(settings or {}, current_dt)
    except Exception:
        if plugin_id:
            logger.exception(f"Plugin '{plugin_id}' live refresh hook failed.")
        else:
            logger.exception("Plugin live refresh hook failed.")
        return None
    if not isinstance(state, dict) or not state.get("active"):
        return None
    try:
        interval = int(state.get("interval_seconds"))
    except (TypeError, ValueError):
        return None
    return {"active": True, "interval_seconds": max(1, interval)}


def _plugin_live_refresh_due_for_instance(plugin, plugin_instance, current_dt):
    state = _plugin_live_refresh_state(
        plugin,
        plugin_instance.settings or {},
        current_dt,
        plugin_id=getattr(plugin_instance, "plugin_id", None),
    )
    if not state:
        return False
    latest_refresh_dt = plugin_instance.get_latest_refresh_dt()
    if not latest_refresh_dt:
        return True
    latest_refresh_dt = plugin_instance.align_datetime_tz(latest_refresh_dt, current_dt)
    return (current_dt - latest_refresh_dt) >= timedelta(seconds=state["interval_seconds"])


def _device_config_float(device_config, key, default):
    try:
        raw_value = device_config.get_config(key, default=default)
    except Exception:
        raw_value = default
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return float(default)


def _display_refresh_under_resource_pressure(device_config, *, log_warning=True):
    enabled = True
    try:
        enabled = _setting_enabled(device_config.get_config("display_refresh_resource_guard_enabled", default=True))
    except Exception:
        enabled = True
    if not enabled:
        return False

    min_available_mb = max(0.0, _device_config_float(
        device_config,
        "display_refresh_min_available_mb",
        DEFAULT_DISPLAY_REFRESH_MIN_AVAILABLE_MB,
    ))
    max_swap_percent = _device_config_float(
        device_config,
        "display_refresh_max_swap_percent",
        DEFAULT_DISPLAY_REFRESH_MAX_SWAP_PERCENT,
    )
    try:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
    except Exception:
        logger.exception("Could not read system memory pressure for display refresh.")
        return False

    available_mb = memory.available / (1024 * 1024)
    under_pressure = available_mb < min_available_mb or swap.percent >= max_swap_percent
    if under_pressure and log_warning:
        logger.warning(
            "Skipping synchronous display refresh due to resource pressure. | "
            "available_mb: %.1f | min_available_mb: %.1f | "
            "swap_percent: %.1f | max_swap_percent: %.1f",
            available_mb,
            min_available_mb,
            swap.percent,
            max_swap_percent,
        )
    return under_pressure


def _save_image_atomic(image, path):
    """Write a PNG/JPEG cache image without exposing a partially-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    root, ext = os.path.splitext(path)
    tmp_path = f"{root}.tmp-{os.getpid()}-{threading.get_ident()}{ext or '.png'}"
    save_format = {
        ".bmp": "BMP",
        ".gif": "GIF",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".webp": "WEBP",
    }.get((ext or ".png").lower())

    def write_image(target_path):
        with open(target_path, "wb") as handle:
            kwargs = {"format": save_format} if save_format else {}
            image.save(handle, **kwargs)
            handle.flush()
            os.fsync(handle.fileno())

    if os.name == "nt":
        write_image(path)
        return

    try:
        write_image(tmp_path)
        try:
            os.replace(tmp_path, path)
        except OSError:
            logger.exception("Atomic image replace failed; falling back to direct write: %s", path)
            write_image(path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            logger.warning("Could not remove temporary image file: %s", tmp_path)


def _load_image_copy(path):
    """Load an image copy while ensuring Windows file handles are released."""
    with open(path, "rb") as handle:
        with Image.open(handle) as image:
            return image.copy()


def _image_allows_cache(image):
    return not getattr(image, "info", {}).get(SKIP_CACHE_IMAGE_INFO_KEY)


class RefreshTask:
    """Handles the logic for refreshing the display using a background thread."""

    def __init__(
        self,
        device_config,
        display_manager,
        *,
        clock=time.monotonic,
        wall_clock=time.time,
        stop_event=None,
        refresh_queue=None,
        render_arbiter=None,
        lifecycle=None,
        retry_registry=None,
        scheduler_state=None,
        runtime_state_store=None,
        cache_lifecycle_manager=None,
        browser_renderer=None,
        display_transaction=None,
        disk_usage=None,
        sports_isolated_renderer=None,
        ian=None,
        ian_resource_sampler=None,
        ian_request_adapter=None,
        ian_retained_limit=None,
        resource_governor=None,
        parallel_image_runner=None,
    ):
        self.device_config = device_config
        self.display_manager = display_manager
        self._clock = clock
        self._wall_clock = wall_clock
        self._resource_governor = (
            resource_governor
            if resource_governor is not None
            else RuntimeResourceGovernor()
        )
        self._parallel_image_runner = (
            parallel_image_runner
            if parallel_image_runner is not None
            else BoundedParallelStageRunner(governor=self._resource_governor)
        )

        if lifecycle is not None:
            if stop_event is not None and lifecycle.stop_event is not stop_event:
                raise ValueError("lifecycle stop_event does not match injected stop_event")
            if refresh_queue is not None and lifecycle.refresh_queue is not refresh_queue:
                raise ValueError("lifecycle refresh_queue does not match injected refresh_queue")
            stop_event = lifecycle.stop_event
            refresh_queue = lifecycle.refresh_queue
        else:
            if stop_event is None:
                stop_event = threading.Event()
            if refresh_queue is None:
                refresh_queue = RefreshQueue(
                    capacity=self._config_int("manual_update_queue_capacity", 32, 1, 128),
                    manual_reserved=4,
                    clock=clock,
                    wall_clock=wall_clock,
                )
            lifecycle = LifecycleController(
                stop_event,
                refresh_queue,
                clock=clock,
                wall_clock=wall_clock,
            )

        self.stop_event = stop_event
        self.refresh_queue = refresh_queue
        self.render_arbiter = render_arbiter if render_arbiter is not None else RenderArbiter()
        self.retry_registry = retry_registry if retry_registry is not None else RetryRegistry()
        self.lifecycle = lifecycle
        self.scheduler_state = (
            scheduler_state
            if scheduler_state is not None
            else SchedulerState(self.retry_registry, clock=clock, wall_clock=wall_clock)
        )
        self.runtime_state = (
            runtime_state_store
            if runtime_state_store is not None
            else RuntimeStateStore(
                self._runtime_state_path(device_config),
                clock=clock,
                wall_clock=wall_clock,
            )
        )
        self.cache_catalog = CacheCatalog(
            os.path.join(self.device_config.plugin_image_dir, ".refresh-cache")
        )
        self.presentation_cache = PresentationCache(
            os.path.join(
                self.device_config.plugin_image_dir,
                ".refresh-presentation",
            )
        )
        self._admission_state = AdmissionState()
        self._first_data_due = FirstDataDueTracker()
        self._resource_tier = None
        self._due_counts = {lane.value: 0 for lane in RefreshLane}
        self._oldest_data_overdue_seconds = None
        self._refresh_progress = RefreshProgressTracker(clock=clock)
        self._lightweight_followup_remaining = 0
        self._background_scheduler_recheck_pending = False
        self._scheduler_due_wake_monotonic = None
        self._scheduler_probe_monotonic = None
        self._rotation_deadline_guard_active = False
        self._rotation_has_ready_candidates = False
        self._rotation_cache_starved_since = None
        self._display_transactions_enabled = False
        bind_runtime_state = getattr(display_manager, "bind_runtime_state", None)
        if callable(bind_runtime_state):
            bind_runtime_state(self.runtime_state)
            self._display_transactions_enabled = True

        self._disk_usage = shutil.disk_usage if disk_usage is None else disk_usage
        self._browser_renderer = browser_renderer
        self._display_transaction = (
            display_transaction
            if display_transaction is not None
            else getattr(display_manager, "transaction", None)
        )
        self._disk_pressure_tier = DiskPressureTier.HEALTHY
        self._cache_lifecycle_error_count = 0
        self.cache_lifecycle = (
            cache_lifecycle_manager
            if cache_lifecycle_manager is not None
            else CacheLifecycleManager(
                Path(self.device_config.plugin_image_dir),
                enabled=_setting_enabled(
                    self.device_config.get_config(
                        "cache_lifecycle_enabled",
                        default=True,
                    )
                ),
                clock=clock,
                presentation_marker_reader=self._read_presentation_marker,
            )
        )
        initial_lifecycle = self.cache_lifecycle.snapshot()
        self._cache_lifecycle_published_snapshot = (
            self._freeze_cache_lifecycle_snapshot(
                initial_lifecycle,
                aggregate=None,
                disk_tier=self._disk_pressure_tier,
                ran_at=getattr(initial_lifecycle, "ran_at", None),
                dry_run=bool(getattr(initial_lifecycle, "dry_run", False)),
            )
        )

        self.thread = None
        self._start_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._running = False
        self._waiting_event = threading.Event()
        self._execution_local = threading.local()
        self._active_operation = None
        self._attempt_count = 0
        self._completion_lock = threading.Lock()
        self._completion_events = {}
        self._transient_upload_lock = threading.Lock()
        self._transient_uploads = {}
        self.cache_refresh_lock = threading.Lock()
        self.manual_refresh_lock = threading.Lock()
        self.config_write_lock = threading.Lock()

        self.refresh_event = threading.Event()
        self.refresh_event.set()
        self.refresh_result = {}
        self._last_cache_pressure_log_monotonic = 0.0
        self._last_memory_maintenance_monotonic = 0.0
        self._last_memory_pressure_restart_monotonic = 0.0
        self._memory_watchdog_pressure_episode_active = False
        self._memory_watchdog_pressure_since_monotonic = None
        self._memory_watchdog_next_check_seconds = None
        self._libc = None
        self._restart_request = None
        self._sports_liveness_window = None
        self._sports_liveness_cooldown_until_monotonic = 0.0
        self._ticketmaster_liveness_window = None
        self._ticketmaster_liveness_cooldown_until_monotonic = 0.0
        self._ticketmaster_bootstrap_due_since = {}
        self._weather_liveness_window = None
        self._weather_liveness_cooldown_until_monotonic = 0.0
        self._burst_liveness_yield_ordinary_pending = False
        self._burst_liveness_yield_deadline_monotonic = 0.0
        self._sports_isolated_renderer = (
            render_sports_dashboard_isolated
            if sports_isolated_renderer is None
            else sports_isolated_renderer
        )
        self._ian_retained_entries = {}
        self._ian_recorded_deferrals = set()
        self._ian_retry_not_before = 0.0
        configured_retained_limit = (
            self._config_int(
                "ian_retained_limit",
                DEFAULT_IAN_RETAINED_LIMIT,
                0,
                128,
            )
            if ian_retained_limit is None
            else max(0, min(128, int(ian_retained_limit)))
        )
        self._ian_retained_limit = min(
            configured_retained_limit,
            max(0, self.refresh_queue.capacity - 1),
        )
        self._ian_last_turn_status = IanTurnStatus.IDLE.value
        self._ian_last_queue_status = None
        self._ian_request_adapter = (
            refresh_command_to_ian_request
            if ian_request_adapter is None
            else ian_request_adapter
        )
        resource_sampler = (
            self._sample_ian_resources
            if ian_resource_sampler is None
            else ian_resource_sampler
        )
        self._ian = (
            Ian(
                clock=clock,
                resource_sampler=resource_sampler,
                executor=self._execute_ian_stage,
                cancellation_probe=self._ian_request_canceled,
            )
            if ian is None
            else ian
        )

    def _config_int(self, key, default, minimum, maximum):
        try:
            value = int(self.device_config.get_config(key, default=default))
        except (TypeError, ValueError, OverflowError):
            value = default
        return max(minimum, min(maximum, value))

    def _cache_lifecycle_thresholds(self):
        defaults = DiskThresholds()
        return DiskThresholds(
            soft_min_free_bytes=self._config_int(
                "cache_lifecycle_soft_min_free_bytes",
                defaults.soft_min_free_bytes,
                0,
                1 << 63,
            ),
            hard_min_free_bytes=self._config_int(
                "cache_lifecycle_hard_min_free_bytes",
                defaults.hard_min_free_bytes,
                0,
                1 << 63,
            ),
            soft_max_used_percent=self._config_float(
                "cache_lifecycle_soft_max_used_percent",
                defaults.soft_max_used_percent,
            ),
            hard_max_used_percent=self._config_float(
                "cache_lifecycle_hard_max_used_percent",
                defaults.hard_max_used_percent,
            ),
        )

    def _sample_disk_pressure(self):
        sample_failed = False
        try:
            usage = self._disk_usage(self.device_config.plugin_image_dir)
            tier = classify_disk_pressure(
                usage.total,
                usage.used,
                usage.free,
                self._cache_lifecycle_thresholds(),
            )
        except Exception:
            self._cache_lifecycle_error_count += 1
            sample_failed = True
            tier = DiskPressureTier.HARD
        self._disk_pressure_tier = tier
        published = self._cache_lifecycle_published_snapshot
        self._cache_lifecycle_published_snapshot = replace(
            published,
            disk_tier=tier,
            error_count=(
                published.error_count + 1
                if sample_failed
                else published.error_count
            ),
        )
        return tier

    def cache_lifecycle_snapshot(self):
        """Return one immutable, redacted worker-owned lifecycle snapshot."""
        return self._cache_lifecycle_published_snapshot

    @staticmethod
    def _cache_lifecycle_count(source, name):
        try:
            return max(0, int(getattr(source, name)))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return 0

    def _freeze_cache_lifecycle_snapshot(
        self,
        metadata,
        *,
        aggregate,
        disk_tier,
        ran_at,
        dry_run,
    ):
        counters = metadata if aggregate is None else aggregate
        return CacheLifecycleSnapshot(
            enabled=bool(getattr(metadata, "enabled", False)),
            disk_tier=DiskPressureTier(disk_tier),
            ran_at=ran_at,
            dry_run=bool(dry_run),
            scanned_entries=self._cache_lifecycle_count(
                counters,
                "scanned_entries",
            ),
            candidate_entries=self._cache_lifecycle_count(
                counters,
                "candidate_entries",
            ),
            deleted_entries=self._cache_lifecycle_count(
                counters,
                "deleted_entries",
            ),
            deleted_bytes=self._cache_lifecycle_count(counters, "deleted_bytes"),
            retained_current=self._cache_lifecycle_count(
                counters,
                "retained_current",
            ),
            retained_last_good=self._cache_lifecycle_count(
                counters,
                "retained_last_good",
            ),
            retained_recent=self._cache_lifecycle_count(
                counters,
                "retained_recent",
            ),
            skipped_unsafe=self._cache_lifecycle_count(
                counters,
                "skipped_unsafe",
            ),
            error_count=(
                self._cache_lifecycle_count(counters, "error_count")
                + self._cache_lifecycle_error_count
            ),
            backlog_entries=self._cache_lifecycle_count(
                counters,
                "backlog_entries",
            ),
        )

    def _publish_cache_lifecycle_run(
        self,
        aggregate,
        *,
        disk_tier,
        now_epoch,
        dry_run,
        metadata=None,
    ):
        metadata = (
            self._cache_lifecycle_published_snapshot
            if metadata is None
            else metadata
        )
        snapshot = self._freeze_cache_lifecycle_snapshot(
            metadata,
            aggregate=aggregate,
            disk_tier=disk_tier,
            ran_at=datetime.fromtimestamp(
                float(now_epoch),
                tz=timezone.utc,
            ).isoformat(),
            dry_run=dry_run,
        )
        self._cache_lifecycle_published_snapshot = snapshot
        return snapshot

    def _cache_lifecycle_should_yield(self):
        if self.stop_event.is_set():
            return True
        try:
            return self.refresh_queue.snapshot().depth > 0
        except Exception:
            return True

    def _cache_lifecycle_budget(self, tier):
        return {
            DiskPressureTier.HEALTHY: HEALTHY_BUDGET,
            DiskPressureTier.SOFT: SOFT_BUDGET,
            DiskPressureTier.HARD: HARD_BUDGET,
        }[DiskPressureTier(tier)]

    def _snapshot_cache_retention(self, *, include_display=True):
        manager = self.device_config.get_playlist_manager()
        lifecycle_guard = getattr(manager, "instance_lifecycle_guard", None)
        guard = lifecycle_guard() if callable(lifecycle_guard) else nullcontext()
        with guard:
            instances = manager.snapshot_all_instances()
            runtime_instances = dict(self.runtime_state.snapshot().instances)

        current_display_path = None
        transaction = self._display_transaction
        if include_display and transaction is not None:
            current_reader = getattr(transaction, "current", None)
            if callable(current_reader):
                current = current_reader()
                current_display_path = (
                    None if current is None else getattr(current, "image_path", None)
                )
        return build_cache_retention(
            Path(self.device_config.plugin_image_dir),
            instances,
            runtime_instances,
            current_display_path,
        )

    def _read_presentation_marker(self):
        return self._snapshot_cache_retention(
            include_display=False,
        ).presentation_marker

    def _run_cache_lifecycle_maintenance(self, tier):
        now_monotonic = self._clock()
        aggregate = None
        metadata = None
        now_epoch = None
        dry_run = False
        try:
            if not self.cache_lifecycle.due(now_monotonic, tier):
                return False
            aggregate = LifecycleAggregate()
            allowance = LifecycleAllowance(
                self._cache_lifecycle_budget(tier).start(now_monotonic),
                aggregate,
                clock=self._clock,
                should_yield=self._cache_lifecycle_should_yield,
            )
            now_epoch = self._wall_clock()
            dry_run = _setting_enabled(
                self.device_config.get_config(
                    "cache_lifecycle_dry_run",
                    default=False,
                )
            )
            browser = self._browser_renderer
            if browser is None:
                browser = get_browser_renderer()
                self._browser_renderer = browser
            browser.cleanup_abandoned_jobs(
                now_epoch=now_epoch,
                stale_seconds=STALE_TEMP_SECONDS,
                dry_run=dry_run,
                allowance=allowance,
            )
            if self._cache_lifecycle_should_yield():
                allowance.mark_backlog()
                self._publish_cache_lifecycle_run(
                    aggregate,
                    disk_tier=tier,
                    now_epoch=now_epoch,
                    dry_run=dry_run,
                )
                return False
            retention = self._snapshot_cache_retention()
            metadata = self.cache_lifecycle.maintain(
                retention,
                now_epoch=now_epoch,
                now_monotonic=now_monotonic,
                tier=tier,
                dry_run=dry_run,
                should_yield=self._cache_lifecycle_should_yield,
                allowance=allowance,
            )
            if self._cache_lifecycle_should_yield():
                allowance.mark_backlog()
                self._publish_cache_lifecycle_run(
                    aggregate,
                    disk_tier=tier,
                    now_epoch=now_epoch,
                    dry_run=dry_run,
                    metadata=metadata,
                )
                return True
            if self._display_transaction is not None:
                self._display_transaction.maintenance(
                    now_epoch=now_epoch,
                    stale_seconds=STALE_TEMP_SECONDS,
                    dry_run=dry_run,
                    allowance=allowance,
                )
            self._publish_cache_lifecycle_run(
                aggregate,
                disk_tier=tier,
                now_epoch=now_epoch,
                dry_run=dry_run,
                metadata=metadata,
            )
            return True
        except Exception:
            self._cache_lifecycle_error_count += 1
            if aggregate is not None and now_epoch is not None:
                self._publish_cache_lifecycle_run(
                    aggregate,
                    disk_tier=tier,
                    now_epoch=now_epoch,
                    dry_run=dry_run,
                    metadata=metadata,
                )
            else:
                published = self._cache_lifecycle_published_snapshot
                self._cache_lifecycle_published_snapshot = replace(
                    published,
                    disk_tier=DiskPressureTier(tier),
                    error_count=published.error_count + 1,
                )
            logger.warning(
                "Cache lifecycle maintenance degraded. | error_count: %d",
                self._cache_lifecycle_error_count,
            )
            return False

    def _renderer_blocked_by_disk_pressure(self, command):
        if command.intent not in _RENDERER_INTENTS:
            return False
        tier = self._sample_disk_pressure()
        if tier is DiskPressureTier.HEALTHY:
            return False
        self._run_cache_lifecycle_maintenance(tier)
        return self._sample_disk_pressure() is DiskPressureTier.HARD

    @staticmethod
    def _runtime_state_path(device_config):
        data_dir = getattr(device_config, "data_dir", None)
        if data_dir is not None:
            return os.path.join(os.fspath(data_dir), "runtime_state.json")
        return os.path.join(
            os.fspath(device_config.plugin_image_dir),
            ".runtime-state.json",
        )

    @property
    def running(self):
        return self._running

    @running.setter
    def running(self, value):
        self._running = bool(value)

    @property
    def manual_update_requests(self):
        with self._completion_lock:
            return tuple(self._completion_events)

    @property
    def manual_update_request(self):
        requests = self.manual_update_requests
        return requests[0] if requests else ()

    @property
    def manual_update_jobs(self):
        return {
            job_id: payload
            for job_id in self.manual_update_requests
            if (payload := self.get_manual_update_job(job_id)) is not None
        }

    @property
    def attempt_count(self):
        return self._attempt_count

    def scheduler_snapshot(self):
        return self.scheduler_state.snapshot()

    def active_operation_snapshot(self):
        """Return the current immutable command deadline without taking a lock."""

        return self._active_operation

    def _parallel_runtime_health_snapshot(self):
        """Return bounded aggregate metrics without child or instance identity."""

        try:
            sample = dict(self._resource_governor.last_snapshot)
        except Exception:
            sample = {}
        try:
            run = dict(self._parallel_image_runner.last_run_snapshot)
        except Exception:
            run = {}
        try:
            cumulative = dict(self._parallel_image_runner.cumulative_snapshot)
        except Exception:
            cumulative = {}
        try:
            active_child_count = len(self._parallel_image_runner.active_processes)
        except Exception:
            active_child_count = 0
        try:
            throttling = dict(self._resource_governor.cpu_throttling_snapshot())
        except Exception:
            throttling = {}

        def optional_number(value):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            converted = float(value)
            return converted if math.isfinite(converted) and converted >= 0 else None

        def nonnegative_int(value, default=0, maximum=None):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return default
            return value if maximum is None else min(value, maximum)

        known_reasons = {
            "not_run",
            "resource_snapshot_unavailable",
            "serial_requested",
            "cpu_quota_below_parallel_threshold",
            "memory_below_parallel_threshold",
            "swap_above_parallel_threshold",
            "parallel_threshold_not_met",
            "parallel_batch_busy",
        }
        reason = run.get("reason")
        degrade_reason = reason if reason in known_reasons else None
        raw_admission_counts = cumulative.get("admission_tier_counts", {})
        if not isinstance(raw_admission_counts, Mapping):
            raw_admission_counts = {}
        raw_reason_counts = cumulative.get(
            "serial_fallback_reason_counts",
            {},
        )
        if not isinstance(raw_reason_counts, Mapping):
            raw_reason_counts = {}
        serial_reason_counts = {
            known_reason: nonnegative_int(raw_reason_counts.get(known_reason))
            for known_reason in sorted(known_reasons)
            if nonnegative_int(raw_reason_counts.get(known_reason)) > 0
        }
        status = run.get("status")
        if status not in {"not_run", "succeeded", "failed", "canceled"}:
            status = "unknown"

        worker_count = nonnegative_int(run.get("worker_count"), default=1, maximum=3)
        if worker_count < 1:
            worker_count = 1
        selected_tier = {
            1: "serial",
            2: "2_worker",
            3: "3_worker",
        }[worker_count]
        return {
            "resource_sample": {
                "available_mb": optional_number(sample.get("available_mb")),
                "swap_percent": optional_number(sample.get("swap_percent")),
                "cpu_quota_cores": optional_number(sample.get("cpu_quota_cores")),
            },
            "selected_tier": selected_tier,
            "worker_count": worker_count,
            "degrade_reason": degrade_reason,
            "status": status,
            "batch_duration_ms": optional_number(run.get("batch_duration_ms")) or 0.0,
            "worker_thread_count": nonnegative_int(
                run.get("worker_thread_count"),
                maximum=3,
            ),
            "child_peak_rss_bytes": nonnegative_int(
                run.get("child_peak_rss_bytes"),
                default=None,
            ),
            "cancellation_count": nonnegative_int(run.get("cancellation_count")),
            "active_child_count": nonnegative_int(active_child_count, maximum=1),
            "cumulative": {
                "admission_tier_counts": {
                    "serial": nonnegative_int(
                        raw_admission_counts.get("serial")
                    ),
                    "2_worker": nonnegative_int(
                        raw_admission_counts.get("2_worker")
                    ),
                    "3_worker": nonnegative_int(
                        raw_admission_counts.get("3_worker")
                    ),
                },
                "serial_fallback_reason_counts": serial_reason_counts,
                "batch_count": nonnegative_int(cumulative.get("batch_count")),
                "batch_duration_ms_total": (
                    optional_number(cumulative.get("batch_duration_ms_total"))
                    or 0.0
                ),
                "normalized_work_pixels_total": nonnegative_int(
                    cumulative.get("normalized_work_pixels_total")
                ),
                "child_peak_rss_bytes": nonnegative_int(
                    cumulative.get("child_peak_rss_bytes"),
                    default=None,
                ),
                "cancellation_count": nonnegative_int(
                    cumulative.get("cancellation_count")
                ),
            },
            "cpu_throttling": {
                "nr_periods": nonnegative_int(
                    throttling.get("nr_periods"),
                    default=None,
                ),
                "nr_throttled": nonnegative_int(
                    throttling.get("nr_throttled"),
                    default=None,
                ),
                "throttled_usec": nonnegative_int(
                    throttling.get("throttled_usec"),
                    default=None,
                ),
            },
        }

    def refresh_health_snapshot(self):
        """Return aggregate refresh diagnostics without instance-owned details."""
        tier = getattr(self._resource_tier, "value", self._resource_tier)
        return {
            "resource_tier": "unknown" if tier is None else str(tier),
            "due_counts": dict(self._due_counts),
            "oldest_data_overdue_seconds": self._oldest_data_overdue_seconds,
            "progress": self._refresh_progress.snapshot(),
            "ian_status": self._ian_last_turn_status,
            "ian_last_queue_status": self._ian_last_queue_status,
            "ian_retained": len(self._ian_retained_entries),
            "ian_retained_limit": self._ian_retained_limit,
            "ian_retry_not_before_monotonic": self._ian_retry_not_before,
            "parallel_runtime": self._parallel_runtime_health_snapshot(),
        }

    @property
    def restart_request(self):
        return None if self._restart_request is None else dict(self._restart_request)

    def start(self):
        """Start exactly one non-daemon command worker."""
        with self._start_lock:
            if self.thread and self.thread.is_alive():
                return
            if self.lifecycle.state is not LifecycleState.STARTING:
                raise RuntimeError(f"refresh task cannot start from {self.lifecycle.state.value}")
            self._prune_runtime_state()
            recover_display = getattr(self.display_manager, "recover_display", None)
            if self._display_transactions_enabled and callable(recover_display):
                recover_display(
                    task_context=self.make_cleanup_context(
                        self._config_int("display_timeout_seconds", 120, 1, 900)
                    )
                )
            logger.info("Starting refresh task")
            self.thread = threading.Thread(
                target=self._run,
                name="inkypi-ian-refresh-worker",
                daemon=False,
            )
            self.running = True
            self.thread.start()
            self.lifecycle.mark_running()

    def cache_refresh_in_progress(self):
        return self.cache_refresh_lock.locked()

    def manual_update_in_progress(self):
        return self.manual_refresh_lock.locked()

    def stop(self, join_timeout=None):
        """Quiesce admission and join the command worker within a bounded time."""
        with self._stop_lock:
            with self._start_lock:
                state = self.lifecycle.state
                if state is LifecycleState.STOPPED:
                    self.running = False
                    self._cleanup_all_transient_uploads()
                    return True
                if state is LifecycleState.FORCED_EXIT:
                    self.running = False
                    return False
                if state in {LifecycleState.STARTING, LifecycleState.RUNNING}:
                    self.lifecycle.begin_quiesce(reason="refresh task stopping")
                self.refresh_queue.wake()
                thread = self.thread

            if thread:
                logger.info("Stopping refresh task")
                timeout = 210.0 if join_timeout is None else max(0.0, float(join_timeout))
                thread.join(timeout=timeout)
            self.running = False
            if thread and thread.is_alive():
                if self.lifecycle.state is LifecycleState.QUIESCING:
                    self.lifecycle.begin_draining()
                self._flush_runtime_state()
                if self.lifecycle.state is LifecycleState.DRAINING:
                    self.lifecycle.mark_forced_exit("refresh worker did not stop")
                return False
            self._finalize_retained_ian_entries()
            self._cleanup_all_transient_uploads()
            if self.lifecycle.state is LifecycleState.QUIESCING:
                self.lifecycle.begin_draining()
            self._flush_runtime_state()
            if self.lifecycle.state is LifecycleState.DRAINING:
                self.lifecycle.mark_stopped()
            return self.lifecycle.state is LifecycleState.STOPPED

    def _flush_runtime_state(self):
        try:
            self.runtime_state.flush()
        except Exception:
            logger.exception("Runtime state could not be flushed during lifecycle drain")

    def _run(self):
        """Coordinate scheduled and queued refresh commands on one worker."""
        try:
            while not self.stop_event.is_set():
                entry = self._wait_for_work()
                if entry is None:
                    if self.stop_event.is_set() or not self.refresh_queue.snapshot().accepting:
                        break
                    continue
                self._process_queue_entry(entry)
        finally:
            self._finalize_retained_ian_entries()
            self.running = False
            self._cleanup_all_transient_uploads()
            self._waiting_event.clear()
            self.refresh_event.set()

    def _wait_for_work(self) -> QueueEntry | None:
        """Probe, schedule, reprobe, then wait on a non-lossy queue cursor."""
        self._reap_terminal_transient_uploads()
        token = self.refresh_queue.change_token()
        entry = self.refresh_queue.take(timeout=0)
        if entry is not None:
            return entry
        if not self.refresh_queue.snapshot().accepting:
            return None

        self._schedule_if_due()
        entry = self.refresh_queue.take(timeout=0)
        if entry is not None:
            return entry
        if not self.refresh_queue.snapshot().accepting:
            return None

        scheduler = self.scheduler_state.snapshot()
        if scheduler.next_attempt_monotonic is None:
            timeout = 30.0
        else:
            timeout = max(0.0, scheduler.next_attempt_monotonic - self._clock())
        ian_entry = self._ian_entry_ready_to_resume()
        if ian_entry is not None:
            return ian_entry
        if self._ian_retained_entries:
            timeout = min(
                timeout,
                max(0.0, self._ian_retry_not_before - self._clock()),
            )
        self._waiting_event.set()
        try:
            self.refresh_queue.wait_for_change(token, timeout=timeout)
        finally:
            self._waiting_event.clear()
        entry = self.refresh_queue.take(timeout=0)
        if entry is not None:
            return entry
        self._schedule_if_due()
        entry = self.refresh_queue.take(timeout=0)
        if entry is not None:
            return entry
        return self._ian_entry_ready_to_resume()

    def wait_until_waiting(self, timeout=1.0):
        return self._waiting_event.wait(timeout=max(0.0, float(timeout)))

    def _run_one_iteration_for_test(self):
        """Run one non-blocking scheduler/worker turn for deterministic tests."""
        entry = self.refresh_queue.take(timeout=0)
        if entry is None:
            self._schedule_if_due()
            entry = self.refresh_queue.take(timeout=0)
        if entry is None:
            entry = self._ian_entry_ready_to_resume()
        if entry is not None:
            self._process_queue_entry(entry)
        return entry

    def _ian_entry_ready_to_resume(self):
        if (
            self._ian_retained_entries
            and self._clock() >= self._ian_retry_not_before
        ):
            return next(iter(self._ian_retained_entries.values()))
        return None

    def _schedule_if_due(self):
        now = self._clock()
        scheduler = self.scheduler_state.snapshot()
        completion_recheck = bool(
            self._background_scheduler_recheck_pending
            and not self.stop_event.is_set()
            and self._restart_request is None
            and self.refresh_queue.snapshot().accepting
            and self.retry_registry.next_delay(RetryRegistry.GLOBAL_KEY, now) <= 0
        )
        self._background_scheduler_recheck_pending = False
        if (
            scheduler.next_attempt_monotonic is not None
            and now < scheduler.next_attempt_monotonic
            and not completion_recheck
        ):
            return None

        global_delay = self.retry_registry.next_delay(RetryRegistry.GLOBAL_KEY, now)
        if global_delay > 0:
            self.scheduler_state.set_next_attempt(max(
                now + global_delay, scheduler.next_attempt_monotonic or now,
            ))
            return None

        try:
            self._scheduler_due_wake_monotonic = None
            lightweight_followup = self._lightweight_followup_remaining > 0
            self.scheduler_state.record_attempt()
            self._attempt_count += 1
            restart_requested = self._memory_watchdog_should_restart()
            disk_tier = self._sample_disk_pressure()
            current_dt = self._get_current_datetime()
            self._scheduler_probe_monotonic = self._clock()
            self._observe_refresh_progress(current_dt)
            refresh_command = None
            command = self._select_prepared_display_retry_command(current_dt)
            if command is None:
                command = self._select_cached_display_command(current_dt)
            if command is not None:
                self.refresh_queue.submit(command)
            else:
                self._run_cache_lifecycle_maintenance(disk_tier)
                if disk_tier is not DiskPressureTier.HEALTHY:
                    disk_tier = self._sample_disk_pressure()
            if restart_requested:
                self._resource_tier = ResourceTier.HARD
            elif (
                command is None
                and disk_tier is not DiskPressureTier.HARD
                and not self._cache_lifecycle_should_yield()
                and self._ian_entry_ready_to_resume() is None
            ):
                if lightweight_followup:
                    refresh_command = self._select_independent_refresh_command(
                        current_dt,
                        safe_light_only=True,
                    )
                else:
                    refresh_command = self._select_independent_refresh_command(
                        current_dt
                    )
                if self.retry_registry.next_delay(RetryRegistry.GLOBAL_KEY, self._clock()) > 0:
                    # Selection can encounter a failed retry-state write. Keep
                    # the resulting cooldown and do not submit from that probe.
                    return None
                if lightweight_followup:
                    if self._is_safe_lightweight_scheduler_command(
                        refresh_command
                    ):
                        refresh_command = self._mark_lightweight_followup_command(
                            refresh_command
                        )
                        self._lightweight_followup_remaining -= 1
                    else:
                        refresh_command = None
                        self._lightweight_followup_remaining = 0
                if refresh_command is not None:
                    self._submit_independent_refresh_command(refresh_command)
            elif lightweight_followup:
                self._lightweight_followup_remaining = 0
            for window in (
                self._weather_liveness_window,
                self._ticketmaster_liveness_window,
                self._sports_liveness_window,
            ):
                if window is not None:
                    self._note_scheduler_deadline(
                        window.deadline_monotonic,
                        allow_elapsed=(
                            window.deadline_monotonic > self._scheduler_probe_monotonic
                        ),
                    )
            self._note_scheduler_deadline(
                self._burst_liveness_yield_deadline_monotonic,
                allow_elapsed=(
                    self._burst_liveness_yield_deadline_monotonic
                    > self._scheduler_probe_monotonic
                ),
            )
            next_delay = 30.0 if restart_requested else self._scheduler_poll_seconds()
            if (
                not restart_requested
                and self._memory_watchdog_next_check_seconds is not None
            ):
                next_delay = min(
                    next_delay,
                    max(0.05, self._memory_watchdog_next_check_seconds),
                )
            next_attempt = now + next_delay
            if not restart_requested and self._scheduler_due_wake_monotonic is not None:
                next_attempt = min(next_attempt, self._scheduler_due_wake_monotonic)
            if (
                completion_recheck
                and not restart_requested
                and command is None
                and refresh_command is None
                and scheduler.next_attempt_monotonic is not None
                and scheduler.next_attempt_monotonic > now
            ):
                # An early probe must not push the existing idle/spacing poll
                # later when admission is currently blocked or there is no work.
                next_attempt = min(next_attempt, scheduler.next_attempt_monotonic)
            self.scheduler_state.set_next_attempt(next_attempt)
            return command
        except Exception as error:
            self.scheduler_state.record_failure(error)
            delay = self.retry_registry.mark_failure(RetryRegistry.GLOBAL_KEY, now)
            self.scheduler_state.set_next_attempt(now + max(30.0, delay))
            logger.exception("Scheduled refresh selection failed")
            return None

    def _note_scheduler_due_at(self, due_at, current_dt):
        """Keep a future policy deadline from facts already read by selection."""
        if due_at is None:
            return
        delay = due_at.timestamp() - current_dt.timestamp()
        if not math.isfinite(delay) or delay <= 0:
            # Overdue work may be blocked by admission. It is not a zero-time
            # timer; a fresh capacity event or the bounded idle poll retries it.
            return
        reference = self._scheduler_probe_monotonic
        deadline = (self._clock() if reference is None else reference) + delay
        # This deadline was future when the probe sampled its facts. If a
        # later hook consumed that time, recheck on the next worker turn.
        self._note_scheduler_deadline(deadline, allow_elapsed=True)

    def _note_scheduler_deadline(self, deadline, *, allow_elapsed=False):
        if deadline is None or not math.isfinite(deadline):
            return
        if not allow_elapsed and deadline <= self._clock():
            return
        previous = self._scheduler_due_wake_monotonic
        self._scheduler_due_wake_monotonic = (
            deadline if previous is None else min(previous, deadline)
        )

    def _scheduler_poll_seconds(self):
        interval = self._config_float(
            "plugin_cycle_interval_seconds",
            DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
        )
        if interval <= 0:
            interval = DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS
        poll_cap = DEFAULT_IDLE_SCHEDULER_POLL_SECONDS
        try:
            active = self.device_config.get_playlist_manager().snapshot_active_playlist(
                self._get_current_datetime()
            )
            if active is not None and active.plugins:
                remaining = self._get_rotation_wait_seconds()
                if math.isfinite(remaining):
                    poll_cap = max(
                        DEFAULT_ROTATION_SCHEDULER_POLL_SECONDS,
                        min(DEFAULT_IDLE_SCHEDULER_POLL_SECONDS, remaining),
                    )
        except Exception:
            logger.exception("Could not inspect active playlist for scheduler polling.")
        return max(1.0, min(poll_cap, interval))

    def _scheduler_lightweight_burst_limit(self):
        return self._config_int(
            "scheduler_lightweight_burst_limit",
            2,
            1,
            4,
        )

    @staticmethod
    def _is_safe_lightweight_scheduler_command(command):
        return bool(
            isinstance(command, RefreshCommand)
            and command.kind is CommandKind.CACHE_REFRESH
            and command.source is CommandSource.BACKGROUND
            and command.intent is RefreshIntent.DATA_REFRESH
            and plugin_execution_class(command.plugin_id)
            is ExecutionClass.INLINE
        )

    @staticmethod
    def _mark_lightweight_followup_command(command):
        payload = thaw_payload(command.payload)
        payload["scheduler_lightweight_followup"] = True
        return replace(command, payload=freeze_payload(payload))

    def _note_lightweight_scheduler_terminal(self, command, finished):
        is_followup = bool(
            command.payload.get("scheduler_lightweight_followup") is True
        )
        if (
            finished.status is not JobStatus.SUCCEEDED
            or not self._is_safe_lightweight_scheduler_command(command)
            or self._ian_retained_entries
            or self.stop_event.is_set()
            or not self.refresh_queue.snapshot().accepting
        ):
            if is_followup:
                self._lightweight_followup_remaining = 0
            return

        if not is_followup:
            self._lightweight_followup_remaining = max(
                0,
                self._scheduler_lightweight_burst_limit() - 1,
            )
        if self._lightweight_followup_remaining > 0:
            self.scheduler_state.set_next_attempt(self._clock())

    def _finalize_lightweight_followup_entry(self, entry):
        if (
            entry.command.payload.get("scheduler_lightweight_followup")
            is not True
        ):
            return
        current = self.refresh_queue.get_entry(entry.job.id)
        if current is None or current.job.status is not JobStatus.SUCCEEDED:
            self._lightweight_followup_remaining = 0

    def _note_scheduler_terminal(self, command, finished):
        """Recheck admission after a terminal command has released its resources."""
        if (
            (finished.status not in {
                JobStatus.SUCCEEDED, JobStatus.FAILED,
                JobStatus.CANCELED, JobStatus.ABANDONED,
            } and command.id not in self._ian_retained_entries)
            or self.stop_event.is_set()
            or self._restart_request is not None
            or not self.refresh_queue.snapshot().accepting
        ):
            return
        now = self._clock()
        if self.retry_registry.next_delay(RetryRegistry.GLOBAL_KEY, now) > 0:
            return
        next_attempt = self.scheduler_state.snapshot().next_attempt_monotonic
        if next_attempt is None or next_attempt <= now:
            return
        thresholds = self._resource_thresholds()
        tier = classify_resource_tier(self._resource_sample(), thresholds)
        if tier is ResourceTier.HARD:
            return
        if tier is ResourceTier.SOFT:
            spacing_deadline = soft_spacing_deadline(self._admission_state, thresholds)
            if spacing_deadline is not None and spacing_deadline > now:
                self.scheduler_state.set_next_attempt(min(next_attempt, spacing_deadline))
                return
        # This wakes the normal selector, not a renderer. Queue priority, disk,
        # memory spacing, rotation reservations and provider backoff still apply.
        self._background_scheduler_recheck_pending = True
        logger.info(
            "Refresh command released capacity; requesting one scheduler recheck. | "
            "plugin_id: %s | status: %s | idle_poll_remaining_seconds: %.3f",
            command.plugin_id,
            finished.status.value,
            next_attempt - now,
        )

    def _defer_scheduler_after_bookkeeping_error(self):
        """Keep failed persistence from turning capacity wakes into retry loops."""
        now = self._clock()
        delay = self.retry_registry.mark_failure(RetryRegistry.GLOBAL_KEY, now)
        self.scheduler_state.set_next_attempt(now + max(30.0, delay))
        self._background_scheduler_recheck_pending = False

    def _rotation_presentation_wait_seconds(self):
        configured_wait = max(
            0.0,
            self._config_float(
                "rotation_presentation_wait_seconds",
                DEFAULT_ROTATION_PRESENTATION_WAIT_SECONDS,
            ),
        )
        cycle_interval = max(
            0.0,
            self._config_float(
                "plugin_cycle_interval_seconds",
                DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
            ),
        )
        remaining_budget = max(
            0.0,
            DEFAULT_ROTATION_PRESENTATION_DEADLINE_SECONDS - cycle_interval,
        )
        return min(configured_wait, remaining_budget)

    def _rotation_starvation_concession_seconds(self):
        cycle_interval = max(
            0.0,
            self._config_float(
                "plugin_cycle_interval_seconds",
                DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
            ),
        )
        return max(
            0.0,
            DEFAULT_ROTATION_MAX_INTERVAL_SECONDS
            - cycle_interval
            - DEFAULT_ROTATION_HARDWARE_BUDGET_SECONDS,
        )

    def _select_scheduled_command(self, current_dt) -> RefreshCommand | None:
        """Select display work using only immutable PlaylistManager APIs."""
        manager = self.device_config.get_playlist_manager()
        latest_refresh = self.device_config.get_refresh_info()
        theme_context = get_theme_context(self.device_config, now=current_dt)
        allow_display_triggered = _display_triggered_refresh_enabled(
            self.device_config
        )
        theme_info_changed = self._update_active_theme_info(
            theme_context,
            current_dt,
        )
        theme_changed = self._has_theme_changed(theme_context, current_dt)
        if theme_changed and allow_display_triggered:
            active = manager.snapshot_active_playlist(current_dt)
            eligible_instance_uuids = set()
            if active is not None:
                for instance in active.plugins:
                    plugin_config = self.device_config.get_plugin(instance.plugin_id)
                    resolved_theme = _resolved_theme_context_for_instance(
                        instance,
                        plugin_config,
                        self.device_config,
                        current_dt=current_dt,
                    )
                    if (
                        resolved_theme is not None
                        and resolved_theme.get("requested_mode") == "auto"
                    ):
                        eligible_instance_uuids.add(instance.instance_uuid)
            displayed_uuid = self.runtime_state.snapshot().displayed_instance_uuid
            if displayed_uuid is None:
                displayed_uuid = self._get_config_value("displayed_instance_uuid", None)
            selection = manager.select_theme_instance(
                current_dt,
                displayed_instance_uuid=displayed_uuid,
                displayed_playlist=None if displayed_uuid is not None else latest_refresh.playlist,
                displayed_plugin_id=None if displayed_uuid is not None else latest_refresh.plugin_id,
                displayed_name=None if displayed_uuid is not None else latest_refresh.plugin_instance,
                is_eligible=lambda instance: (
                    instance.instance_uuid in eligible_instance_uuids
                ),
                allow_fallback=False,
            )
            if selection is not None:
                if theme_info_changed:
                    self._write_device_config()
                return self._playlist_command(
                    selection.playlist_name,
                    selection.instance,
                    source=CommandSource.SCHEDULER,
                    intent=RefreshIntent.THEME_REDRAW,
                    force=False,
                    display_cached_only=True,
                    priority=80,
                    theme_context=theme_context,
                    theme_render_only=True,
                    current_dt=current_dt,
                    expected_displayed_instance_uuid=selection.instance.instance_uuid,
                )
            self._persist_active_theme(theme_context, current_dt)
            self._write_device_config()
        elif theme_changed:
            self._persist_active_theme(theme_context, current_dt)
            self._write_device_config()
        elif theme_info_changed:
            self._write_device_config()

        # Playlist rotation owns the display cadence. A currently displayed
        # live instance may refresh between ticks, but it must never delay the
        # next rotation or pull a different instance onto the screen.
        try:
            interval = float(self.device_config.get_config(
                "plugin_cycle_interval_seconds",
                default=DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
            ))
        except (TypeError, ValueError, OverflowError):
            interval = DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS
        selection = manager.reserve_next_active_instance(
            current_dt,
            latest_refresh=latest_refresh.get_refresh_datetime(),
            interval_seconds=interval,
            max_starvation_seconds=self._rotation_starvation_concession_seconds(),
        )
        if selection is not None:
            return self._playlist_command(
                selection.playlist_name,
                selection.instance,
                source=CommandSource.SCHEDULER,
                intent=RefreshIntent.DISPLAY_CACHE,
                display_cached_only=True,
                priority=AUTOMATIC_ROTATION_DISPLAY_PRIORITY,
                automatic_rotation=True,
            )

        if not allow_display_triggered:
            return None

        active = manager.snapshot_active_playlist(current_dt)
        if active is None:
            return None
        displayed_uuid = self.runtime_state.snapshot().displayed_instance_uuid
        displayed = next(
            (
                instance
                for instance in active.plugins
                if instance.instance_uuid == displayed_uuid
            ),
            None,
        )
        if (
            displayed is None
            and displayed_uuid is None
            and latest_refresh.refresh_type == "Playlist"
            and latest_refresh.playlist == active.name
        ):
            displayed = next(
                (
                    instance
                    for instance in active.plugins
                    if instance.plugin_id == latest_refresh.plugin_id
                    and instance.name == latest_refresh.plugin_instance
                ),
                None,
            )
        if displayed is None or self._snapshot_retry_delayed(displayed, current_dt):
            return None
        if not self._snapshot_live_refresh_due(displayed, current_dt):
            return None
        if _display_refresh_under_resource_pressure(
            self.device_config,
            log_warning=False,
        ):
            return None
        return self._playlist_command(
            active.name,
            displayed,
            source=CommandSource.LIVE,
            intent=RefreshIntent.LIVE_REFRESH,
            display_cached_only=True,
            priority=70,
        )

    def _select_background_commands(self, current_dt, *, skip_instance_uuid=None):
        theme_context = get_theme_context(self.device_config, now=current_dt)
        current_mode = (theme_context or {}).get("mode")
        theme_refresh_delayed = bool(
            current_mode
            and self._get_config_value("active_theme", None) != current_mode
            and self._theme_refresh_retry_delayed(theme_context, current_dt)
        )
        manager = self.device_config.get_playlist_manager()
        active = manager.snapshot_active_playlist(current_dt)
        if active is None:
            return ()
        candidates = []
        for instance in active.plugins:
            if instance.instance_uuid == skip_instance_uuid:
                continue
            if self._snapshot_retry_delayed(instance, current_dt):
                continue
            if self._snapshot_background_cache_disabled(instance):
                continue
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            resolved_theme = _resolved_theme_context_for_instance(
                instance,
                plugin_config,
                self.device_config,
                current_dt=current_dt,
            )
            theme_mode = (
                resolved_theme.get("mode")
                if isinstance(resolved_theme, Mapping)
                else None
            )
            cache_path = self._snapshot_cache_path(instance, theme_mode)
            missing = not os.path.exists(cache_path)
            reusable_theme_cache = bool(
                theme_mode
                and any(
                    os.path.exists(path)
                    for path in self._theme_cache_reuse_paths(instance, theme_mode)
                )
            )
            missing_work = missing and not reusable_theme_cache
            due = self._snapshot_should_refresh(instance, current_dt)
            live_due = self._snapshot_live_refresh_due(instance, current_dt)
            if theme_refresh_delayed and missing_work and not due and not live_due:
                continue
            if not missing_work and not due and not live_due:
                continue
            latest = self._snapshot_latest_refresh_dt(instance)
            latest_timestamp = float("-inf") if latest is None else latest.timestamp()
            candidates.append((
                not live_due,
                not missing_work,
                latest_timestamp,
                instance.plugin_id,
                instance.name,
                instance,
            ))

        limit = self._background_cache_refresh_max_per_pass()
        selected = sorted(candidates, key=lambda item: item[:5])[:limit]
        return tuple(
            self._playlist_command(
                active.name,
                item[5],
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.DATA_REFRESH,
                display_cached_only=False,
                priority=10,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
            )
            for item in selected
        )

    def _active_cache_candidates(
        self,
        active,
        theme_context,
        *,
        exact_theme_only=False,
    ):
        """Resolve exact, decodable cache candidates outside the model lock."""
        if active is None:
            return {}
        runtime_instances = self.runtime_state.snapshot().instances
        candidates = {}
        for instance in active.plugins:
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            resolved_theme = _resolved_theme_context_for_instance(
                instance,
                plugin_config,
                self.device_config,
                current_dt=None,
            )
            theme_mode = (
                resolved_theme.get("mode")
                if isinstance(resolved_theme, Mapping)
                else None
            )
            resolver = (
                self.cache_catalog.resolve_exact
                if exact_theme_only
                else self.cache_catalog.resolve
            )
            candidate = resolver(
                instance,
                theme_mode,
                runtime_instances.get(instance.instance_uuid, InstanceRuntimeState()),
            )
            if candidate is not None:
                candidates[instance.instance_uuid] = candidate
        return candidates

    def _rotation_display_candidates(self, active, theme_context, *, exact_theme_only=False):
        """Keep shuffle order while preferring this instance's exact prepared request.

        Scan request metadata only. Decode the selected file once before enqueueing
        and again at execution, so unrelated prepared files cost no image decodes.
        DATA admission continues to use the authoritative cache catalog.
        """
        candidates = {
            key: _RotationDisplayCandidate(
                value.instance_uuid, value.structural_generation,
                value.settings_revision, value.theme_mode,
            )
            for key, value in self._active_cache_candidates(
                active, theme_context, exact_theme_only=exact_theme_only,
            ).items()
        }
        if active is None:
            return candidates
        states = self.runtime_state.snapshot().instances
        for instance in active.plugins:
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            if plugin_supports_cached_display_redraw(plugin_config):
                # This audited local renderer also handles absent/expired source
                # caches. It must not depend on an obsolete formal PNG existing.
                _config, _context, local_theme = self._latest_presentation_theme(instance)
                candidates[instance.instance_uuid] = _RotationDisplayCandidate(
                    instance.instance_uuid, instance.structural_generation,
                    instance.settings_revision, local_theme,
                )
            state = states.get(instance.instance_uuid, InstanceRuntimeState())
            request = state.presentation_request
            if request is None or request.prepared_at is None:
                continue
            plugin_config, _context, theme = self._latest_presentation_theme(instance)
            if (
                not _presentation_refresh_enabled(self.device_config, plugin_config)
                or not plugin_supports_presentation_refresh(plugin_config)
                or request.structural_generation != instance.structural_generation
                or request.settings_revision != instance.settings_revision
                or request.prepared_theme_mode != theme
                or (state.presentation_receipt is not None
                    and state.presentation_receipt.request_id == request.request_id)
            ):
                continue
            try:
                if not resolve_refresh_on_display_for_config(thaw_payload(instance.settings), plugin_config):
                    continue
            except (PluginSettingError, TypeError, ValueError):
                continue
            candidates[instance.instance_uuid] = _RotationDisplayCandidate(
                instance.instance_uuid, instance.structural_generation,
                instance.settings_revision, theme, request.request_id,
            )
        return candidates

    def _rotation_cache_candidates_outside_refresh_backoff(
        self,
        candidates,
        current_dt,
    ):
        """Exclude stale caches while their data or presentation retry cools down."""
        runtime_instances = self.runtime_state.snapshot().instances
        eligible = {}
        for instance_uuid, candidate in candidates.items():
            state = runtime_instances.get(instance_uuid, InstanceRuntimeState())
            data_retry = self._parse_iso_datetime(state.data.next_retry_at)
            if data_retry is not None:
                data_retry = self._align_datetime_tz(data_retry, current_dt)
                last_attempt = self._parse_iso_datetime(state.data.last_attempt_at)
                last_failure = self._parse_iso_datetime(state.data.last_failure_at)
                if last_attempt is not None:
                    last_attempt = self._align_datetime_tz(last_attempt, current_dt)
                if last_failure is not None:
                    last_failure = self._align_datetime_tz(last_failure, current_dt)
                # A resource deferral advances the attempt timestamp but preserves
                # failure provenance; its retry gate must not hide last-good cache.
                resource_deferred = last_attempt is not None and (
                    last_failure is None or last_attempt > last_failure
                )
                if data_retry > current_dt and not resource_deferred:
                    continue
            if self._presentation_request_in_retry_backoff(state, current_dt):
                continue
            eligible[instance_uuid] = candidate
        return eligible

    def _rotation_cache_candidates_outside_display_backoff(self, candidates):
        """Keep a timed-out panel write from immediately reclaiming rotation."""
        return {
            instance_uuid: candidate
            for instance_uuid, candidate in candidates.items()
            if self.retry_registry.next_delay(
                self._rotation_display_retry_key(instance_uuid),
                self._clock(),
            )
            <= 0
        }

    def _presentation_request_in_retry_backoff(self, state, current_dt):
        request = state.presentation_request
        failed_at = self._parse_iso_datetime(state.presentation.last_failure_at)
        next_retry = self._parse_iso_datetime(state.presentation.next_retry_at)
        requested_at = self._parse_iso_datetime(
            request.requested_at if request is not None else None
        )
        if (
            failed_at is None
            or next_retry is None
            or requested_at is None
        ):
            return False
        failed_at = self._align_datetime_tz(failed_at, current_dt)
        next_retry = self._align_datetime_tz(next_retry, current_dt)
        requested_at = self._align_datetime_tz(requested_at, current_dt)
        return failed_at >= requested_at and next_retry > current_dt

    def _rotation_cache_candidates_outside_opt_in_presentation_backoff(
        self,
        active,
        candidates,
        current_dt,
    ):
        """Honor retry backoff only for opted-in presentation work."""

        instances = {
            instance.instance_uuid: instance
            for instance in active.plugins
        }
        runtime_instances = self.runtime_state.snapshot().instances
        eligible = {}
        for instance_uuid, candidate in candidates.items():
            instance = instances.get(instance_uuid)
            if instance is None:
                eligible[instance_uuid] = candidate
                continue
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            if (
                not _presentation_refresh_enabled(
                    self.device_config,
                    plugin_config,
                )
                or not plugin_supports_presentation_refresh(plugin_config)
            ):
                eligible[instance_uuid] = candidate
                continue
            try:
                refresh_before_display = resolve_refresh_on_display_for_config(
                    thaw_payload(instance.settings),
                    plugin_config,
                )
            except Exception:
                refresh_before_display = False
            if not refresh_before_display:
                eligible[instance_uuid] = candidate
                continue
            state = runtime_instances.get(instance_uuid, InstanceRuntimeState())
            if self._presentation_request_in_retry_backoff(state, current_dt):
                continue
            eligible[instance_uuid] = candidate
        return eligible

    def _select_cached_display_command(self, current_dt) -> RefreshCommand | None:
        """Select one random eligible cache without loading plugin code."""
        self._rotation_deadline_guard_active = False
        self._rotation_has_ready_candidates = False
        manager = self.device_config.get_playlist_manager()
        active = manager.snapshot_active_playlist(current_dt)
        if active is None:
            self._rotation_cache_starved_since = None
            return None
        latest_refresh = self.device_config.get_refresh_info()
        latest_display_dt = latest_refresh.get_refresh_datetime()
        try:
            interval = float(
                self.device_config.get_config(
                    "plugin_cycle_interval_seconds",
                    default=DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
                )
            )
        except (TypeError, ValueError, OverflowError):
            interval = DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS
        rotation_due = manager.should_refresh(
            latest_display_dt,
            interval,
            current_dt,
        )
        theme_context = get_theme_context(self.device_config, now=current_dt)
        theme_changed = self._has_theme_changed(theme_context, current_dt)
        if theme_changed and not rotation_due:
            self._rotation_cache_starved_since = None
            return None

        candidates = self._rotation_display_candidates(
            active,
            theme_context,
            exact_theme_only=True,
        )
        allow_display_triggered = _display_triggered_refresh_enabled(
            self.device_config
        )
        if allow_display_triggered:
            candidates = self._rotation_cache_candidates_outside_refresh_backoff(
                candidates,
                current_dt,
            )
        else:
            candidates = (
                self._rotation_cache_candidates_outside_opt_in_presentation_backoff(
                    active,
                    candidates,
                    current_dt,
                )
            )
        candidates = self._rotation_cache_candidates_outside_display_backoff(
            candidates
        )
        recovery_elapsed = None
        if rotation_due and (theme_changed or not candidates):
            if self._rotation_cache_starved_since is None:
                self._rotation_cache_starved_since = current_dt
            recovery_elapsed = max(
                0.0,
                (current_dt - self._rotation_cache_starved_since).total_seconds(),
            )
            if recovery_elapsed < DEFAULT_ROTATION_CACHE_RECOVERY_SECONDS:
                return None
            fallback_candidates = self._rotation_display_candidates(
                active,
                theme_context,
                exact_theme_only=False,
            )
            if allow_display_triggered:
                fallback_candidates = (
                    self._rotation_cache_candidates_outside_refresh_backoff(
                        fallback_candidates,
                        current_dt,
                    )
                )
            else:
                fallback_candidates = (
                    self._rotation_cache_candidates_outside_opt_in_presentation_backoff(
                        active,
                        fallback_candidates,
                        current_dt,
                    )
                )
            fallback_candidates = (
                self._rotation_cache_candidates_outside_display_backoff(
                    fallback_candidates
                )
            )
            if fallback_candidates:
                candidates = fallback_candidates
            elif not candidates:
                return None
        else:
            self._rotation_cache_starved_since = None

        self._rotation_has_ready_candidates = bool(candidates)
        starvation_cap = self._rotation_starvation_concession_seconds()
        if recovery_elapsed is not None:
            starvation_cap = max(0.0, starvation_cap - recovery_elapsed)
        selection = manager.reserve_next_active_instance(
            current_dt,
            latest_refresh=latest_display_dt,
            interval_seconds=interval,
            eligible_instance_uuids=frozenset(candidates),
            max_starvation_seconds=starvation_cap,
        )
        if selection is None:
            self._rotation_deadline_guard_active = bool(
                candidates
                and manager.should_refresh(
                    latest_display_dt,
                    interval,
                    current_dt,
                )
            )
            return None
        self._rotation_cache_starved_since = None
        candidate = candidates.get(selection.instance.instance_uuid)
        if candidate is None or (
            candidate.structural_generation
            != selection.instance.structural_generation
            or candidate.settings_revision != selection.instance.settings_revision
        ):
            return None

        if (
            allow_display_triggered
            and candidate.presentation_request_id is None
            and not plugin_supports_cached_display_redraw(
                self.device_config.get_plugin(selection.instance.plugin_id)
            )
            and not self._snapshot_background_cache_disabled(selection.instance)
            and self._snapshot_should_refresh(selection.instance, current_dt)
            and not self._snapshot_retry_delayed(selection.instance, current_dt)
            and self._restart_request is None
            and classify_resource_tier(
                self._resource_sample(),
                self._resource_thresholds(),
            ) is not ResourceTier.HARD
        ):
            return self._playlist_command(
                selection.playlist_name,
                selection.instance,
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.DATA_REFRESH,
                force=False,
                display_cached_only=False,
                priority=95,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
                automatic_rotation=True,
            )

        presentation_request_id = None
        allow_prepared_presentation = False
        plugin_config = self.device_config.get_plugin(selection.instance.plugin_id)
        if (
            _presentation_refresh_enabled(
                self.device_config,
                plugin_config,
            )
            and plugin_supports_presentation_refresh(plugin_config)
        ):
            try:
                refresh_before_display = resolve_refresh_on_display_for_config(
                    thaw_payload(selection.instance.settings),
                    plugin_config,
                )
            except PluginSettingError as error:
                logger.warning(
                    "Ignoring invalid refresh-on-display setting during rotation preflight. | plugin_id: %s | error: %s",
                    selection.instance.plugin_id,
                    error,
                )
                refresh_before_display = False
            except Exception:
                logger.exception(
                    "Rotation preflight trigger resolution failed closed. | plugin_id: %s",
                    selection.instance.plugin_id,
                )
                refresh_before_display = False

            if refresh_before_display:
                runtime_snapshot = self.runtime_state.snapshot()
                state = runtime_snapshot.instances.get(
                    selection.instance.instance_uuid,
                    InstanceRuntimeState(),
                )
                request = state.presentation_request
                if candidate.presentation_request_id is not None and (
                    request is None or request.request_id != candidate.presentation_request_id
                ):
                    manager.release_rotation_reservation(
                        selection.instance.instance_uuid,
                        expected_playlist_name=selection.playlist_name,
                    )
                    return None
                presentation_satisfied = (
                    request is None
                    and self._presentation_succeeded_since_display(
                        state,
                        latest_display_dt,
                        current_dt,
                    )
                )
                request_revision_changed = request is not None and (
                    request.structural_generation
                    != selection.instance.structural_generation
                    or request.settings_revision
                    != selection.instance.settings_revision
                )
                if not presentation_satisfied and (
                    request is None or request_revision_changed
                ):
                    request_id = uuid4().hex
                    origin_commit_id = (
                        runtime_snapshot.display_commit_id
                        or f"rotation-preflight-{request_id}"
                    )
                    request = PresentationRequestState(
                        request_id=request_id,
                        requested_at=current_dt.isoformat(),
                        structural_generation=selection.instance.structural_generation,
                        settings_revision=selection.instance.settings_revision,
                        origin_theme_mode=candidate.theme_mode,
                        origin_display_commit_id=origin_commit_id,
                    )
                    self.runtime_state.request_presentation(
                        selection.instance.instance_uuid,
                        request,
                    )
                    request = (
                        self.runtime_state.snapshot()
                        .instances.get(
                            selection.instance.instance_uuid,
                            InstanceRuntimeState(),
                        )
                        .presentation_request
                    )

                if request is not None:
                    if (
                        request.prepared_at is not None
                        and request.prepared_theme_mode == candidate.theme_mode
                    ):
                        presentation_request_id = request.request_id
                        allow_prepared_presentation = True
                    else:
                        requested_at = self._parse_iso_datetime(request.requested_at)
                        wait_seconds = self._rotation_presentation_wait_seconds()
                        resource_tier = classify_resource_tier(
                            self._resource_sample(),
                            self._resource_thresholds(),
                        )
                        wait_expired = requested_at is None or (
                            current_dt - requested_at
                        ).total_seconds() >= max(0.0, wait_seconds)
                        if (
                            resource_tier is not ResourceTier.HARD
                            and not wait_expired
                        ):
                            return None
                        if resource_tier is ResourceTier.HARD:
                            deferred = manager.defer_rotation_reservation(
                                selection.instance.instance_uuid,
                                expected_playlist_name=selection.playlist_name,
                                eligible_instance_uuids=frozenset(candidates),
                            )
                            released = False
                            if not deferred:
                                released = manager.release_rotation_reservation(
                                    selection.instance.instance_uuid,
                                    expected_playlist_name=selection.playlist_name,
                                )
                            if deferred or released:
                                self._write_device_config()
                            logger.warning(
                                "Rotation presentation preflight unavailable; deferred cached display during hard resource pressure. | plugin_id: %s | instance_uuid: %s | request_id: %s | deferred: %s | released: %s",
                                selection.instance.plugin_id,
                                selection.instance.instance_uuid,
                                request.request_id,
                                deferred,
                                released,
                            )
                            return None

                        reason = (
                            "invalid_requested_at"
                            if requested_at is None
                            else "timeout"
                        )
                        logger.warning(
                            "Rotation presentation preflight deadline expired; using the available cache to preserve automatic rotation cadence. | plugin_id: %s | instance_uuid: %s | request_id: %s | reason: %s | wait_seconds: %.1f",
                            selection.instance.plugin_id,
                            selection.instance.instance_uuid,
                            request.request_id,
                            reason,
                            wait_seconds,
                        )
        display_theme_context = (
            theme_context
            if (
                theme_changed
                and candidate.theme_mode == theme_context.get("mode")
            )
            else None
        )
        command = self._playlist_command(
            selection.playlist_name,
            selection.instance,
            source=CommandSource.SCHEDULER,
            intent=RefreshIntent.DISPLAY_CACHE,
            force=False,
            display_cached_only=True,
            priority=AUTOMATIC_ROTATION_DISPLAY_PRIORITY,
            current_dt=current_dt,
            cache_theme_mode=candidate.theme_mode,
            theme_context=display_theme_context,
            automatic_rotation=True,
            allow_prepared_presentation=allow_prepared_presentation,
            presentation_request_id=presentation_request_id,
        )
        if presentation_request_id is not None:
            prepared = self._presentation_candidate(selection.instance, request, candidate.theme_mode)
            if not self.presentation_cache.validate(prepared):
                self._invalidate_prepared_display(command, selection.instance, request, prepared)
                if manager.release_rotation_reservation(
                    selection.instance.instance_uuid,
                    expected_playlist_name=selection.playlist_name,
                ):
                    self._write_device_config()
                return None
        return command

    def _presentation_succeeded_since_display(
        self,
        state,
        latest_display_dt,
        current_dt,
    ):
        last_success = self._parse_iso_datetime(state.presentation.last_success_at)
        if last_success is None:
            return False
        last_success = self._align_datetime_tz(last_success, current_dt)
        receipt = state.presentation_receipt
        if receipt is not None:
            # A prepared display records its receipt commit as lane success.
            # That success belongs to the image already shown; only a later
            # NO_CHANGE success can satisfy the next rotation preflight.
            receipt_committed_at = self._parse_iso_datetime(receipt.committed_at)
            if receipt_committed_at is None:
                return False
            receipt_committed_at = self._align_datetime_tz(
                receipt_committed_at,
                current_dt,
            )
            if last_success <= receipt_committed_at:
                return False
        if latest_display_dt is None:
            return True
        latest_display_dt = self._align_datetime_tz(latest_display_dt, current_dt)
        return last_success > latest_display_dt

    def _select_prepared_display_retry_command(
        self,
        current_dt,
    ) -> RefreshCommand | None:
        """Retry a failed exact prepared display after presentation backoff."""
        manager = self.device_config.get_playlist_manager()
        active = manager.snapshot_active_playlist(current_dt)
        if active is None:
            return None
        runtime_snapshot = self.runtime_state.snapshot()
        displayed_uuid = runtime_snapshot.displayed_instance_uuid
        if displayed_uuid is None:
            return None
        instance = next(
            (
                candidate
                for candidate in active.plugins
                if candidate.instance_uuid == displayed_uuid
            ),
            None,
        )
        if instance is None:
            return None
        state = runtime_snapshot.instances.get(instance.instance_uuid)
        if state is None or state.presentation.last_failure_at is None:
            return None
        request = state.presentation_request
        if request is None or request.prepared_at is None:
            return None
        next_retry = self._parse_iso_datetime(state.presentation.next_retry_at)
        if next_retry is None:
            return None
        plugin_config, _theme_context, theme_mode = self._latest_presentation_theme(
            instance
        )
        if (
            not _presentation_refresh_enabled(self.device_config, plugin_config)
            or not plugin_supports_presentation_refresh(plugin_config)
            or request.structural_generation != instance.structural_generation
            or request.settings_revision != instance.settings_revision
            or request.prepared_theme_mode != theme_mode
        ):
            return None
        next_retry = self._align_datetime_tz(next_retry, current_dt)
        self._note_scheduler_due_at(next_retry, current_dt)
        if next_retry.timestamp() > current_dt.timestamp():
            return None
        return self._playlist_command(
            active.name,
            instance,
            source=CommandSource.BACKGROUND,
            intent=RefreshIntent.DISPLAY_CACHE,
            force=False,
            display_cached_only=True,
            priority=65,
            kind=CommandKind.DISPLAY,
            current_dt=current_dt,
            cache_theme_mode=theme_mode,
            expected_displayed_instance_uuid=instance.instance_uuid,
            preserve_rotation_anchor=True,
            coalescing_scope=f"presentation-followup:{request.request_id}",
            allow_prepared_presentation=True,
            presentation_request_id=request.request_id,
        )

    def _resource_sample(self) -> ResourceSample:
        """Read memory and swap once for one scheduler admission decision."""
        try:
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
        except Exception:
            logger.exception("Could not sample resources for refresh admission.")
            return ResourceSample(available_mb=None, swap_percent=None)
        return ResourceSample(
            available_mb=getattr(memory, "available", 0) / (1024 * 1024),
            swap_percent=getattr(swap, "percent", None),
        )

    def _resource_thresholds(self) -> ResourceThresholds:
        return ResourceThresholds(
            soft_min_available_mb=max(
                0.0,
                self._config_float(
                    "background_cache_refresh_min_available_mb",
                    DEFAULT_BACKGROUND_CACHE_REFRESH_MIN_AVAILABLE_MB,
                ),
            ),
            soft_max_swap_percent=self._config_float(
                "background_cache_refresh_max_swap_percent",
                DEFAULT_BACKGROUND_CACHE_REFRESH_MAX_SWAP_PERCENT,
            ),
            hard_min_available_mb=max(
                0.0,
                self._config_float(
                    "memory_watchdog_min_available_mb",
                    DEFAULT_MEMORY_WATCHDOG_MIN_AVAILABLE_MB,
                ),
            ),
            hard_max_swap_percent=self._config_float(
                "memory_watchdog_max_swap_percent",
                DEFAULT_MEMORY_WATCHDOG_MAX_SWAP_PERCENT,
            ),
            hard_swap_max_available_mb=max(
                0.0,
                self._config_float(
                    "memory_watchdog_confirmation_max_available_mb",
                    DEFAULT_MEMORY_WATCHDOG_CONFIRMATION_MAX_AVAILABLE_MB,
                ),
            ),
            soft_spacing_seconds=max(
                0.0,
                self._config_float(
                    "independent_refresh_soft_spacing_seconds",
                    60.0,
                ),
            ),
        )

    def _independent_refresh_starvation_seconds(self):
        return max(
            0.0,
            self._config_float(
                "independent_refresh_starvation_seconds",
                DEFAULT_INDEPENDENT_REFRESH_STARVATION_SECONDS,
            ),
        )

    def _observe_refresh_progress(self, current_dt):
        """Sample before admission gates; HTTP only reads the detached aggregate."""
        manager = self.device_config.get_playlist_manager()
        active = manager.snapshot_active_playlist(current_dt)
        instances = () if active is None else active.plugins
        runtime_instances = self.runtime_state.snapshot().instances
        # CacheCatalog memoizes decoding by file identity. Subsequent scheduler
        # selections reuse that validation; health polling does no file IO.
        cache_candidates = self._active_cache_candidates(
            active, get_theme_context(self.device_config, now=current_dt)
        )
        self._first_data_due.observe(
            instances, runtime_instances, now=current_dt, now_monotonic=self._clock(),
        )
        presentation_instance_uuids = set()
        for instance in instances:
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            if (
                _presentation_refresh_enabled(self.device_config, plugin_config)
                and plugin_supports_presentation_refresh(plugin_config)
            ):
                try:
                    if resolve_refresh_on_display_for_config(
                        thaw_payload(instance.settings), plugin_config
                    ):
                        presentation_instance_uuids.add(instance.instance_uuid)
                except PluginSettingError:
                    pass
        self._refresh_progress.observe(
            instances=instances,
            runtime_instances=runtime_instances,
            cache_instance_uuids=cache_candidates,
            presentation_instance_uuids=presentation_instance_uuids,
            now=current_dt,
            rotation_cycle_seconds=self._config_float(
                "plugin_cycle_interval_seconds",
                DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
            ),
        )

    def _select_independent_refresh_command(
        self,
        current_dt,
        *,
        safe_light_only=False,
    ) -> RefreshCommand | None:
        """Admit at most one ordinary renderer command for this probe."""
        self._update_burst_liveness_ordinary_yield_window()
        manager = self.device_config.get_playlist_manager()
        active = manager.snapshot_active_playlist(current_dt)
        theme_context = get_theme_context(self.device_config, now=current_dt)
        theme_transition_pending = bool(
            isinstance(theme_context, Mapping)
            and theme_context.get("mode") in {"day", "night"}
            and self._get_config_value("active_theme", None)
            != theme_context.get("mode")
        )
        if active is None:
            self._first_data_due.observe((), {}, now=current_dt, now_monotonic=self._clock())
            self._theme_due_candidate(
                manager,
                None,
                {},
                theme_context,
                current_dt,
            )
            self._due_counts = {lane.value: 0 for lane in RefreshLane}
            self._oldest_data_overdue_seconds = None
            return None

        cache_candidates = self._active_cache_candidates(active, theme_context)
        runtime_instances = self.runtime_state.snapshot().instances
        first_due_since = self._first_data_due.observe(
            active.plugins, runtime_instances, now=current_dt, now_monotonic=self._clock(),
        )
        data_candidates = []
        presentation_candidates = []
        for instance in active.plugins:
            runtime_instance = runtime_instances.get(
                instance.instance_uuid,
                InstanceRuntimeState(),
            )
            data_evaluation = evaluate_data_due(
                instance,
                runtime_instance,
                instance.instance_uuid in cache_candidates,
                current_dt,
                first_due_since=first_due_since.get(instance.instance_uuid),
            )
            self._note_scheduler_due_at(data_evaluation.next_due_at, current_dt)
            if data_evaluation.invalid_fields:
                logger.warning(
                    "Ignoring invalid refresh cadence fields. | plugin_id: %s | fields: %s",
                    instance.plugin_id,
                    ",".join(data_evaluation.invalid_fields),
                )
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            provider_presentation_due = False
            if (
                _presentation_refresh_enabled(
                    self.device_config,
                    plugin_config,
                )
                and plugin_supports_presentation_refresh(plugin_config)
            ):
                resolved_theme_context = _resolved_theme_context_for_instance(
                    instance,
                    plugin_config,
                    self.device_config,
                    current_dt=current_dt,
                )
                resolved_theme_mode = (
                    resolved_theme_context.get("mode")
                    if isinstance(resolved_theme_context, Mapping)
                    else None
                )
                presentation = evaluate_presentation_due(
                    instance,
                    runtime_instance,
                    instance.instance_uuid in cache_candidates,
                    resolved_theme_mode,
                    current_dt,
                )
                self._note_scheduler_due_at(presentation.next_due_at, current_dt)
                if presentation.candidate is not None:
                    presentation_candidates.append(presentation.candidate)
                    provider_presentation_due = (
                        plugin_allows_display_triggered_provider_refresh(
                            plugin_config
                        )
                    )
            # An opted-in provider presentation performs the same fresh fetch
            # and promotes its prepared image after the hardware commit. Avoid
            # admitting a second DATA request for the same pending display.
            if (
                data_evaluation.candidate is not None
                and not provider_presentation_due
            ):
                data_candidates.append(data_evaluation.candidate)

        thresholds = self._resource_thresholds()
        resource_sample = self._resource_sample()
        tier = classify_resource_tier(resource_sample, thresholds)
        if tier is ResourceTier.SOFT:
            self._note_scheduler_deadline(soft_spacing_deadline(self._admission_state, thresholds))
        live_candidates = self._live_due_candidates(
            active,
            runtime_instances,
            current_dt,
            tier,
        )
        theme_candidate = self._theme_due_candidate(
            manager,
            active,
            runtime_instances,
            theme_context,
            current_dt,
        )
        if safe_light_only:
            data_candidates = [
                candidate
                for candidate in data_candidates
                if plugin_execution_class(candidate.instance.plugin_id)
                is ExecutionClass.INLINE
            ]
            presentation_candidates = []
            live_candidates = []
            theme_candidate = None
        auxiliary_candidates = list(live_candidates)
        auxiliary_candidates.extend(presentation_candidates)
        if theme_candidate is not None:
            auxiliary_candidates.append(theme_candidate)
        self._resource_tier = tier
        self._due_counts = {
            RefreshLane.DATA.value: len(data_candidates),
            RefreshLane.PRESENTATION.value: len(presentation_candidates),
            RefreshLane.LIVE.value: len(live_candidates),
            RefreshLane.THEME.value: int(theme_candidate is not None),
        }
        if data_candidates:
            oldest = min(candidate.due_since for candidate in data_candidates)
            self._oldest_data_overdue_seconds = max(
                0.0,
                (current_dt - oldest).total_seconds(),
            )
        else:
            self._oldest_data_overdue_seconds = None

        reserved_instance_uuids = {
            instance.instance_uuid
            for instance in active.plugins
            if manager.validate_rotation_reservation(
                instance.instance_uuid,
                expected_playlist_name=active.name,
            )
        }
        eligible_data_candidates = list(data_candidates)
        ticketmaster_liveness_candidate = None
        ticketmaster_liveness_holds_independent = False
        sports_liveness_candidate = None
        sports_liveness_holds_independent = False
        sports_liveness_excluded_uuids = frozenset()
        weather_liveness_candidate = None
        weather_liveness_holds_independent = False
        weather_liveness_concession = False
        runnable_weather_alternative = None
        if self._weather_liveness_window is not None or any(
            candidate.instance.plugin_id == "weather"
            for candidate in data_candidates
        ):
            # AdmissionState is immutable. This dry decision deliberately drops
            # the returned state so probing an alternative cannot consume its
            # fairness or SOFT-spacing turn.
            runnable_weather_alternative = choose_refresh_candidate(
                [
                    candidate
                    for candidate in eligible_data_candidates
                    if candidate.instance.plugin_id
                    not in {"sports_dashboard", "ticketmaster_events", "weather"}
                ],
                [
                    candidate
                    for candidate in auxiliary_candidates
                    if candidate.instance.plugin_id != "weather"
                ],
                tier=tier,
                state=self._admission_state,
                now_monotonic=self._clock(),
                thresholds=thresholds,
            ).candidate
        if self._weather_liveness_window is not None:
            if runnable_weather_alternative is not None:
                self._finish_weather_liveness_window(
                    reason="runnable_alternative",
                    resource_sample=resource_sample,
                    yield_to_ordinary=(
                        runnable_weather_alternative.lane is RefreshLane.DATA
                    ),
                )
            else:
                (
                    weather_liveness_candidate,
                    weather_liveness_holds_independent,
                    weather_liveness_concession,
                ) = self._weather_liveness_decision(
                    active,
                    data_candidates,
                    runtime_instances,
                    current_dt,
                    resource_sample,
                )
        elif self._ticketmaster_liveness_window is not None:
            (
                ticketmaster_liveness_candidate,
                ticketmaster_liveness_holds_independent,
            ) = self._ticketmaster_liveness_decision(
                active,
                data_candidates,
                runtime_instances,
                current_dt,
                resource_sample,
            )
            # One expired/completed window must yield a scheduler turn before a
            # different burst renderer can reserve another quiet window.
        elif self._sports_liveness_window is not None:
            (
                sports_liveness_candidate,
                sports_liveness_holds_independent,
                sports_liveness_excluded_uuids,
            ) = self._sports_liveness_decision(
                active,
                data_candidates,
                runtime_instances,
                current_dt,
                resource_sample,
            )
        elif not self._burst_liveness_yield_ordinary_pending:
            (
                sports_liveness_candidate,
                sports_liveness_holds_independent,
                sports_liveness_excluded_uuids,
            ) = self._sports_liveness_decision(
                active,
                data_candidates,
                runtime_instances,
                current_dt,
                resource_sample,
            )
            if (
                sports_liveness_candidate is None
                and not sports_liveness_holds_independent
                and self._sports_liveness_window is None
            ):
                (
                    ticketmaster_liveness_candidate,
                    ticketmaster_liveness_holds_independent,
                ) = self._ticketmaster_liveness_decision(
                    active,
                    data_candidates,
                    runtime_instances,
                    current_dt,
                    resource_sample,
                )
            if (
                sports_liveness_candidate is None
                and not sports_liveness_holds_independent
                and self._sports_liveness_window is None
                and ticketmaster_liveness_candidate is None
                and not ticketmaster_liveness_holds_independent
                and self._ticketmaster_liveness_window is None
                and runnable_weather_alternative is None
            ):
                (
                    weather_liveness_candidate,
                    weather_liveness_holds_independent,
                    weather_liveness_concession,
                ) = self._weather_liveness_decision(
                    active,
                    data_candidates,
                    runtime_instances,
                    current_dt,
                    resource_sample,
                )
        (
            ticketmaster_margin_available,
            ticketmaster_required_available_mb,
            ticketmaster_max_swap_percent,
        ) = self._ticketmaster_background_start_margin(resource_sample)
        if (
            not ticketmaster_margin_available
            and ticketmaster_liveness_candidate is None
            and not ticketmaster_liveness_holds_independent
        ):
            eligible_data_candidates = []
            for candidate in data_candidates:
                instance = candidate.instance
                if instance.plugin_id == "ticketmaster_events":
                    next_retry_at = (
                        self._record_lane_resource_pressure_deferral(
                            instance.instance_uuid,
                            RefreshIntent.DATA_REFRESH,
                        )
                    )
                    logger.warning(
                        "Deferring ordinary Ticketmaster background data at "
                        "scheduler admission until its memory reserve is "
                        "available. | plugin_id: %s | source: %s | intent: %s | "
                        "available_mb: %s | required_available_mb: %s | "
                        "swap_percent: %s | max_swap_percent: %s | next_retry_at: %s",
                        instance.plugin_id,
                        CommandSource.BACKGROUND.value,
                        RefreshIntent.DATA_REFRESH.value,
                        resource_sample.available_mb,
                        ticketmaster_required_available_mb,
                        resource_sample.swap_percent,
                        ticketmaster_max_swap_percent,
                        next_retry_at,
                    )
                    continue
                eligible_data_candidates.append(candidate)
        (
            weather_margin_available,
            weather_required_available_mb,
            weather_max_swap_percent,
        ) = self._weather_background_start_margin(resource_sample)
        if (
            not weather_margin_available
            and weather_liveness_candidate is None
        ):
            weather_excluded_uuids = set()
            filtered_candidates = []
            for candidate in eligible_data_candidates:
                instance = candidate.instance
                if instance.plugin_id != "weather":
                    filtered_candidates.append(candidate)
                    continue
                weather_excluded_uuids.add(instance.instance_uuid)
                next_retry_at = self._record_lane_resource_pressure_deferral(
                    instance.instance_uuid,
                    RefreshIntent.DATA_REFRESH,
                )
                logger.warning(
                    "Deferring ordinary Weather background data at scheduler "
                    "admission until its browser start margin is available. | "
                    "plugin_id: %s | source: %s | intent: %s | available_mb: %s | "
                    "required_available_mb: %s | swap_percent: %s | "
                    "max_swap_percent: %s | next_retry_at: %s",
                    instance.plugin_id,
                    CommandSource.BACKGROUND.value,
                    RefreshIntent.DATA_REFRESH.value,
                    resource_sample.available_mb,
                    weather_required_available_mb,
                    resource_sample.swap_percent,
                    weather_max_swap_percent,
                    next_retry_at,
                )
            eligible_data_candidates = filtered_candidates
            if weather_excluded_uuids:
                data_candidates = [
                    candidate
                    for candidate in data_candidates
                    if candidate.instance.instance_uuid
                    not in weather_excluded_uuids
                ]
        if sports_liveness_excluded_uuids:
            data_candidates = [
                candidate
                for candidate in data_candidates
                if candidate.instance.instance_uuid
                not in sports_liveness_excluded_uuids
            ]
            eligible_data_candidates = [
                candidate
                for candidate in eligible_data_candidates
                if candidate.instance.instance_uuid
                not in sports_liveness_excluded_uuids
            ]
            auxiliary_candidates = [
                candidate
                for candidate in auxiliary_candidates
                if candidate.instance.instance_uuid
                not in sports_liveness_excluded_uuids
            ]
        if self.retry_registry.next_delay(RetryRegistry.GLOBAL_KEY, self._clock()) > 0:
            # No fairness/spacing turn is consumed when admission bookkeeping
            # has failed and armed the scheduler's own backoff.
            return None
        if (
            not sports_liveness_holds_independent
            and not ticketmaster_liveness_holds_independent
            and reserved_instance_uuids
            and self._admission_state.consecutive_data_admissions < 1
            and self._get_rotation_wait_seconds()
            > DEFAULT_ROTATION_BACKGROUND_GUARD_SECONDS
        ):
            starvation_seconds = self._independent_refresh_starvation_seconds()
            starved_data = [
                candidate
                for candidate in eligible_data_candidates
                if candidate.reason
                in {DueReason.INTERVAL, DueReason.SCHEDULED}
                and (
                    current_dt
                    - self._align_datetime_tz(candidate.due_since, current_dt)
                ).total_seconds()
                >= starvation_seconds
            ]
            if starved_data:
                if sports_liveness_candidate is not None:
                    starved_data = [sports_liveness_candidate]
                concession = choose_refresh_candidate(
                    starved_data,
                    [],
                    tier=tier,
                    state=self._admission_state,
                    now_monotonic=self._clock(),
                    thresholds=thresholds,
                )
                if concession.candidate is not None:
                    self._admission_state = concession.state
                    candidate = concession.candidate
                    return self._playlist_command(
                        active.name,
                        candidate.instance,
                        source=CommandSource.BACKGROUND,
                        intent=RefreshIntent.DATA_REFRESH,
                        force=False,
                        display_cached_only=False,
                        priority=96,
                        kind=CommandKind.CACHE_REFRESH,
                        current_dt=current_dt,
                    )

        # A presentation request for the reserved next rotation member is part
        # of the display critical path.  Give the exact same instance one due
        # data attempt first so a NO_CHANGE presentation cannot bless an old
        # cache as ready.  Once that attempt has started, presentation wins
        # over all ordinary background work and cannot be starved by a short
        # data interval.
        data_by_instance_uuid = {
            candidate.instance.instance_uuid: candidate
            for candidate in data_candidates
        }
        for presentation_candidate in presentation_candidates:
            presentation_instance = presentation_candidate.instance
            if presentation_instance.instance_uuid not in reserved_instance_uuids:
                continue
            request = runtime_instances[
                presentation_instance.instance_uuid
            ].presentation_request
            if request is None:
                continue
            data_candidate = data_by_instance_uuid.get(
                presentation_instance.instance_uuid
            )
            if (
                data_candidate is not None
                and tier is not ResourceTier.HARD
                and (
                    presentation_instance.plugin_id != "ticketmaster_events"
                    or ticketmaster_margin_available
                )
            ):
                data_attempt = data_candidate.last_attempt_at
                presentation_due = presentation_candidate.due_since
                if data_attempt is None or (
                    self._align_datetime_tz(data_attempt, presentation_due)
                    <= presentation_due
                ):
                    self._admission_state = replace(
                        self._admission_state,
                        consecutive_data_admissions=0,
                        consecutive_background_live_admissions=0,
                    )
                    return self._playlist_command(
                        active.name,
                        presentation_instance,
                        source=CommandSource.BACKGROUND,
                        intent=RefreshIntent.DATA_REFRESH,
                        force=False,
                        display_cached_only=False,
                        priority=95,
                        kind=CommandKind.CACHE_REFRESH,
                        current_dt=current_dt,
                        automatic_rotation=True,
                    )
            self._admission_state = replace(
                self._admission_state,
                consecutive_data_admissions=0,
            )
            return self._playlist_command(
                active.name,
                presentation_instance,
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.PRESENTATION_REFRESH,
                force=False,
                display_cached_only=False,
                priority=90,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
                presentation_request_id=request.request_id,
                automatic_rotation=True,
            )

        # Do not start a provider/render job that can occupy the single worker
        # across the imminent display deadline.  Existing plugin data remains
        # usable from cache and ordinary refreshes resume immediately after the
        # display commit.
        if (
            (reserved_instance_uuids or self._rotation_deadline_guard_active)
            and
            self._get_rotation_wait_seconds()
            <= DEFAULT_ROTATION_BACKGROUND_GUARD_SECONDS
        ):
            return None

        # Keep an admission window even for playlists configured shorter than
        # the normal five-minute rotation; the guard must not cover a cycle.
        ordinary_rotation_guard = min(
            DEFAULT_ROTATION_BACKGROUND_GUARD_SECONDS,
            self._config_float(
                "plugin_cycle_interval_seconds", DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
            ) / 2,
        )
        if (
            self._rotation_has_ready_candidates
            and self._get_rotation_wait_seconds() <= ordinary_rotation_guard
        ):
            # A ready rotation need not have a reservation yet. Keep long
            # renderer classes out of its write window while shorter ordinary
            # work can still use the worker. Due ages and retries are unchanged.
            def fits_rotation_window(candidate):
                return plugin_execution_class(candidate.instance.plugin_id) in {
                    ExecutionClass.PARALLEL_IMAGE, ExecutionClass.INLINE,
                }

            eligible_data_candidates = [
                item for item in eligible_data_candidates if fits_rotation_window(item)
            ]
            auxiliary_candidates = [
                item for item in auxiliary_candidates
                if item.lane not in {RefreshLane.DATA, RefreshLane.LIVE}
                or fits_rotation_window(item)
            ]
            weather_liveness_candidate = None
            sports_liveness_candidate = None
            ticketmaster_liveness_candidate = None

        if weather_liveness_candidate is not None:
            liveness_admission = choose_refresh_candidate(
                [weather_liveness_candidate],
                [],
                tier=tier,
                state=self._admission_state,
                now_monotonic=self._clock(),
                thresholds=thresholds,
            )
            self._admission_state = liveness_admission.state
            candidate = liveness_admission.candidate
            if candidate is None:
                return None
            return self._playlist_command(
                active.name,
                candidate.instance,
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.DATA_REFRESH,
                force=False,
                display_cached_only=False,
                priority=98,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
                weather_liveness_concession=weather_liveness_concession,
            )
        if weather_liveness_holds_independent:
            return None
        if self._burst_liveness_yield_ordinary_pending:
            ordinary_candidates = [
                candidate
                for candidate in eligible_data_candidates
                if candidate.instance.plugin_id
                not in {
                    "sports_dashboard",
                    "ticketmaster_events",
                    "weather",
                }
            ]
            ordinary_admission = choose_refresh_candidate(
                ordinary_candidates,
                [],
                # The concession itself just consumed the SOFT spacing
                # clock. This one-shot handoff intentionally bypasses that
                # spacing, while HARD pressure still admits nothing.
                tier=(
                    ResourceTier.HARD
                    if tier is ResourceTier.HARD
                    else ResourceTier.HEALTHY
                ),
                state=self._admission_state,
                now_monotonic=self._clock(),
                thresholds=thresholds,
            )
            self._admission_state = ordinary_admission.state
            candidate = ordinary_admission.candidate
            if candidate is None:
                return None
            self._burst_liveness_yield_ordinary_pending = False
            self._burst_liveness_yield_deadline_monotonic = 0.0
            return self._playlist_command(
                active.name,
                candidate.instance,
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.DATA_REFRESH,
                force=False,
                display_cached_only=False,
                priority=98,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
            )

        if sports_liveness_candidate is not None:
            liveness_admission = choose_refresh_candidate(
                [sports_liveness_candidate],
                [],
                tier=tier,
                state=self._admission_state,
                now_monotonic=self._clock(),
                thresholds=thresholds,
            )
            self._admission_state = liveness_admission.state
            candidate = liveness_admission.candidate
            if candidate is None:
                return None
            return self._playlist_command(
                active.name,
                candidate.instance,
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.DATA_REFRESH,
                force=False,
                display_cached_only=False,
                priority=97,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
            )
        if ticketmaster_liveness_candidate is not None:
            liveness_admission = choose_refresh_candidate(
                [ticketmaster_liveness_candidate],
                [],
                tier=tier,
                state=self._admission_state,
                now_monotonic=self._clock(),
                thresholds=thresholds,
            )
            self._admission_state = liveness_admission.state
            candidate = liveness_admission.candidate
            if candidate is None:
                return None
            return self._playlist_command(
                active.name,
                candidate.instance,
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.DATA_REFRESH,
                force=False,
                display_cached_only=False,
                priority=97,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
            )
        if sports_liveness_holds_independent:
            return None
        if ticketmaster_liveness_holds_independent:
            return None

        decision = choose_refresh_candidate(
            eligible_data_candidates,
            auxiliary_candidates,
            tier=tier,
            state=self._admission_state,
            now_monotonic=self._clock(),
            thresholds=thresholds,
        )
        self._admission_state = decision.state
        candidate = decision.candidate
        if candidate is None:
            return self._select_theme_catchup_command(
                active,
                runtime_instances,
                theme_context,
                current_dt,
                tier,
                theme_transition_pending,
            )
        if candidate.lane is RefreshLane.THEME:
            return self._playlist_command(
                active.name,
                candidate.instance,
                source=CommandSource.SCHEDULER,
                intent=RefreshIntent.THEME_REDRAW,
                force=False,
                display_cached_only=False,
                priority=80,
                kind=CommandKind.CACHE_REFRESH,
                theme_context=theme_context,
                theme_render_only=True,
                current_dt=current_dt,
                expected_displayed_instance_uuid=candidate.instance.instance_uuid,
            )
        if candidate.lane is RefreshLane.LIVE:
            return self._playlist_command(
                active.name,
                candidate.instance,
                source=CommandSource.LIVE,
                intent=RefreshIntent.LIVE_REFRESH,
                force=False,
                display_cached_only=False,
                priority=70,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
                expected_displayed_instance_uuid=(
                    candidate.instance.instance_uuid
                    if candidate.requires_displayed_instance
                    else None
                ),
                background_live_refresh=not candidate.requires_displayed_instance,
            )
        if candidate.lane is RefreshLane.PRESENTATION:
            request = runtime_instances[candidate.instance.instance_uuid].presentation_request
            if request is None:
                return None
            return self._playlist_command(
                active.name,
                candidate.instance,
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.PRESENTATION_REFRESH,
                force=False,
                display_cached_only=False,
                priority=20,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
                presentation_request_id=request.request_id,
            )
        return self._playlist_command(
            active.name,
            candidate.instance,
            source=CommandSource.BACKGROUND,
            intent=RefreshIntent.DATA_REFRESH,
            force=False,
            display_cached_only=False,
            priority=10,
            kind=CommandKind.CACHE_REFRESH,
            current_dt=current_dt,
        )

    def _select_theme_catchup_command(
        self,
        active,
        runtime_instances,
        theme_context,
        current_dt,
        tier,
        theme_transition_pending,
    ):
        """Admit one provider-free exact-theme catch-up without rotation."""
        if (
            tier is not ResourceTier.HEALTHY
            or theme_transition_pending
        ):
            return None
        target_mode = (
            theme_context.get("mode")
            if isinstance(theme_context, Mapping)
            else None
        )
        if target_mode not in {"day", "night"}:
            return None

        candidates = []
        for instance in active.plugins:
            if self._snapshot_background_cache_disabled(instance):
                continue
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            resolved_theme = _resolved_theme_context_for_instance(
                instance,
                plugin_config,
                self.device_config,
                current_dt=current_dt,
            )
            if (
                not isinstance(resolved_theme, Mapping)
                or resolved_theme.get("requested_mode") != "auto"
                or resolved_theme.get("mode") != target_mode
            ):
                continue
            runtime_instance = runtime_instances.get(
                instance.instance_uuid,
                InstanceRuntimeState(),
            )
            if self.cache_catalog.resolve_exact(
                instance,
                target_mode,
                runtime_instance,
            ) is not None:
                continue
            catchup = runtime_instance.theme_catchup
            next_retry = self._parse_iso_datetime(catchup.next_retry_at)
            if next_retry is not None:
                next_retry = self._align_datetime_tz(next_retry, current_dt)
                if catchup.target_mode == target_mode and current_dt < next_retry:
                    self._note_scheduler_due_at(next_retry, current_dt)
                    continue
            last_attempt = self._parse_iso_datetime(catchup.last_attempt_at)
            if last_attempt is not None:
                last_attempt = self._align_datetime_tz(last_attempt, current_dt)
            candidates.append((
                last_attempt is not None,
                datetime.min.replace(tzinfo=current_dt.tzinfo)
                if last_attempt is None
                else last_attempt,
                instance.instance_uuid,
                instance,
                resolved_theme,
            ))

        for _attempted, _last_attempt, _uuid, instance, resolved_theme in sorted(
            candidates,
            key=lambda item: item[:3],
        ):
            if not self.runtime_state.try_admit_theme_catchup(
                instance.instance_uuid,
                target_mode,
                current_dt.isoformat(),
            ):
                continue
            return self._playlist_command(
                active.name,
                instance,
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.THEME_CATCHUP,
                force=False,
                display_cached_only=False,
                priority=5,
                kind=CommandKind.CACHE_REFRESH,
                theme_render_only=True,
                current_dt=current_dt,
                resolved_theme_context=resolved_theme,
            )
        return None

    def _live_due_candidates(self, active, runtime_instances, current_dt, tier):
        """Return exact-display live candidates unless memory pressure is critical."""
        if tier is ResourceTier.HARD:
            return []
        displayed_uuid = self.runtime_state.snapshot().displayed_instance_uuid
        candidates = []
        for instance in active.plugins:
            is_displayed = instance.instance_uuid == displayed_uuid
            requires_displayed_instance = is_displayed and _live_display_refresh_enabled(
                self.device_config,
                instance.plugin_id,
                instance.settings,
            )
            if (
                requires_displayed_instance
                and self._snapshot_background_cache_disabled(instance)
            ):
                continue
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            if not plugin_supports_live_refresh(plugin_config):
                continue
            plugin = None
            if not requires_displayed_instance:
                if instance.plugin_id != "sports_dashboard":
                    continue
                plugin = self._get_plugin_for_snapshot(
                    instance,
                    require_live_refresh=True,
                )
                background_hook = getattr(
                    plugin,
                    "wants_background_live_refresh",
                    None,
                ) if plugin is not None else None
                if not callable(background_hook):
                    continue
                try:
                    background_enabled = bool(
                        background_hook(thaw_payload(instance.settings), current_dt)
                    )
                except Exception:
                    logger.exception(
                        "Plugin '%s' background live-refresh hook failed.",
                        instance.plugin_id,
                    )
                    continue
                if not background_enabled:
                    continue
            live_state = self._snapshot_live_refresh_state(
                instance,
                current_dt,
                plugin=plugin,
            )
            if not live_state:
                continue
            interval_seconds = live_state["interval_seconds"]
            if (
                not is_displayed
                and instance.plugin_id == "sports_dashboard"
            ):
                configured_floor = self._config_float(
                    "sports_background_live_min_interval_seconds",
                    DEFAULT_SPORTS_BACKGROUND_LIVE_MIN_INTERVAL_SECONDS,
                )
                if not math.isfinite(configured_floor) or configured_floor < 1:
                    configured_floor = (
                        DEFAULT_SPORTS_BACKGROUND_LIVE_MIN_INTERVAL_SECONDS
                    )
                interval_seconds = max(interval_seconds, configured_floor)
            runtime = runtime_instances.get(
                instance.instance_uuid,
                InstanceRuntimeState(),
            ).live
            next_retry = self._parse_iso_datetime(runtime.next_retry_at)
            if next_retry is not None:
                next_retry = self._align_datetime_tz(next_retry, current_dt)
            last_success = self._parse_iso_datetime(runtime.last_success_at)
            if last_success is None:
                due_since = current_dt
            else:
                last_success = self._align_datetime_tz(last_success, current_dt)
                due_since = datetime.fromtimestamp(
                    last_success.timestamp() + interval_seconds,
                    tz=current_dt.tzinfo,
                )
            wake_at = max(
                (due_since, next_retry) if next_retry is not None else (due_since,),
                key=lambda value: value.timestamp(),
            )
            self._note_scheduler_due_at(wake_at, current_dt)
            if current_dt.timestamp() < wake_at.timestamp():
                continue
            last_attempt = self._parse_iso_datetime(runtime.last_attempt_at)
            if last_attempt is not None:
                last_attempt = self._align_datetime_tz(last_attempt, current_dt)
            candidates.append(
                DueCandidate(
                    instance=instance,
                    lane=RefreshLane.LIVE,
                    due_since=due_since,
                    reason=DueReason.LIVE,
                    last_attempt_at=last_attempt,
                    requires_displayed_instance=requires_displayed_instance,
                    is_displayed_instance=is_displayed,
                )
            )
        return candidates

    def _theme_due_candidate(
        self,
        manager,
        active,
        runtime_instances,
        theme_context,
        current_dt,
    ):
        """Resolve one exact displayed auto-theme transition without fallback."""
        theme_info_changed = self._update_active_theme_info(
            theme_context,
            current_dt,
        )
        if not self._has_theme_changed(theme_context, current_dt):
            if theme_info_changed:
                self._write_device_config()
            return None
        displayed_uuid = self.runtime_state.snapshot().displayed_instance_uuid
        eligible_instance_uuids = set()
        if active is not None:
            for instance in active.plugins:
                plugin_config = self.device_config.get_plugin(instance.plugin_id)
                resolved_theme = _resolved_theme_context_for_instance(
                    instance,
                    plugin_config,
                    self.device_config,
                    current_dt=current_dt,
                )
                if (
                    resolved_theme is not None
                    and resolved_theme.get("requested_mode") == "auto"
                ):
                    eligible_instance_uuids.add(instance.instance_uuid)
        selection = None
        if active is not None and displayed_uuid is not None:
            selection = manager.select_theme_instance(
                current_dt,
                displayed_instance_uuid=displayed_uuid,
                displayed_playlist=None,
                displayed_plugin_id=None,
                displayed_name=None,
                is_eligible=lambda instance: (
                    instance.instance_uuid in eligible_instance_uuids
                ),
                allow_fallback=False,
            )
        if selection is None:
            self._persist_active_theme(theme_context, current_dt)
            self._write_device_config()
            return None

        runtime_instance = runtime_instances.get(
            selection.instance.instance_uuid,
            InstanceRuntimeState(),
        )
        target_mode = (
            theme_context.get("mode")
            if isinstance(theme_context, Mapping)
            else None
        )
        target_cache = (
            self.cache_catalog.resolve_exact(
                selection.instance,
                target_mode,
                runtime_instance,
            )
            if target_mode in {"day", "night"}
            else None
        )
        if (
            target_cache is not None
            and target_cache.promoted_at is not None
        ):
            if theme_info_changed:
                self._write_device_config()
            return None
        state = runtime_instance.theme
        next_retry = self._parse_iso_datetime(state.next_retry_at)
        if next_retry is not None:
            next_retry = self._align_datetime_tz(next_retry, current_dt)
            if current_dt < next_retry:
                self._note_scheduler_due_at(next_retry, current_dt)
                if theme_info_changed:
                    self._write_device_config()
                return None
        last_attempt = self._parse_iso_datetime(state.last_attempt_at)
        if last_attempt is not None:
            last_attempt = self._align_datetime_tz(last_attempt, current_dt)
        if theme_info_changed:
            self._write_device_config()
        return DueCandidate(
            instance=selection.instance,
            lane=RefreshLane.THEME,
            due_since=current_dt,
            reason=DueReason.THEME,
            last_attempt_at=last_attempt,
        )

    def _snapshot_live_refresh_state(self, instance, current_dt, plugin=None):
        plugin = plugin or self._get_plugin_for_snapshot(
            instance,
            require_live_refresh=True,
        )
        if plugin is None:
            return None
        context = TaskContext(
            self.stop_event,
            self._clock() + 5.0,
            self._clock,
        )
        with self.render_arbiter.lease(instance.plugin_id, context):
            return _plugin_live_refresh_state(
                plugin,
                thaw_payload(instance.settings),
                current_dt,
                plugin_id=instance.plugin_id,
            )

    def _snapshot_live_refresh_due(self, instance, current_dt, plugin=None):
        state = self._snapshot_live_refresh_state(instance, current_dt, plugin=plugin)
        if not state:
            return False
        latest = self._snapshot_latest_refresh_dt(instance)
        if latest is None:
            return True
        latest = self._align_datetime_tz(latest, current_dt)
        return (current_dt - latest) >= timedelta(seconds=state["interval_seconds"])

    def _snapshot_should_refresh(self, instance, current_dt):
        latest = self._snapshot_latest_refresh_dt(instance)
        if latest is None:
            return True
        latest = self._align_datetime_tz(latest, current_dt)
        refresh = instance.refresh or {}
        if "interval" in refresh:
            try:
                interval = float(refresh.get("interval"))
            except (TypeError, ValueError, OverflowError):
                interval = None
            if interval and (current_dt - latest) >= timedelta(seconds=interval):
                return True
        if "scheduled" in refresh:
            try:
                scheduled_time = datetime.strptime(str(refresh.get("scheduled")), "%H:%M").time()
            except (TypeError, ValueError):
                return False
            scheduled_dt = current_dt.replace(
                hour=scheduled_time.hour,
                minute=scheduled_time.minute,
                second=0,
                microsecond=0,
            )
            if current_dt < scheduled_dt:
                scheduled_dt -= timedelta(days=1)
            return latest < scheduled_dt <= current_dt
        return False

    def _snapshot_latest_refresh_dt(self, instance):
        state = self.runtime_state.snapshot().instances.get(instance.instance_uuid)
        if state is not None and state.last_success_at is not None:
            return self._parse_iso_datetime(state.last_success_at)
        return self._parse_iso_datetime(instance.latest_refresh_time)

    def _snapshot_retry_delayed(self, instance, current_dt):
        state = self.runtime_state.snapshot().instances.get(instance.instance_uuid)
        if state is None or state.next_retry_at is None:
            return False
        next_retry = self._parse_iso_datetime(state.next_retry_at)
        if next_retry is None:
            return False
        next_retry = self._align_datetime_tz(next_retry, current_dt)
        return current_dt < next_retry

    @staticmethod
    def _snapshot_background_cache_disabled(instance):
        if str(instance.plugin_id).strip() != "sports_dashboard":
            return False
        settings = instance.settings or {}
        return not _setting_enabled(settings.get("backgroundCacheRefreshEnabled"))

    def _get_plugin_for_snapshot(self, instance, *, require_live_refresh=False):
        plugin_config = self.device_config.get_plugin(instance.plugin_id)
        if plugin_config is None:
            logger.error("Plugin config not found for '%s'.", instance.plugin_id)
            return None
        if require_live_refresh and not plugin_supports_live_refresh(plugin_config):
            return None
        try:
            return get_plugin_instance(plugin_config)
        except Exception:
            logger.exception("Plugin '%s' could not be loaded.", instance.plugin_id)
            return None

    def _snapshot_cache_path(self, instance, theme_mode=None):
        """Return the authoritative cache path for one immutable revision.

        Human-readable plugin/name cache files are legacy compatibility
        artifacts. They are not safe scheduler inputs because deleting and
        recreating the same name can otherwise reuse another instance's image.
        """
        directory = os.path.join(self.device_config.plugin_image_dir, ".refresh-cache")
        filename = self._cache_identity_filename(
            instance.instance_uuid,
            instance.structural_generation,
            instance.settings_revision,
            theme_mode,
        )
        return os.path.join(directory, filename)

    def _theme_cache_reuse_paths(self, instance, theme_mode):
        if theme_mode not in {"day", "night"}:
            return ()
        opposite_mode = "night" if theme_mode == "day" else "day"
        return (
            self._snapshot_cache_path(instance, opposite_mode),
            self._snapshot_cache_path(instance, None),
        )

    @staticmethod
    def _cache_identity_prefix(instance_uuid):
        return hashlib.sha256(str(instance_uuid).encode("utf-8")).hexdigest()[:32]

    @classmethod
    def _cache_identity_filename(
        cls,
        instance_uuid,
        structural_generation,
        settings_revision,
        theme_mode=None,
    ):
        if theme_mode not in {None, "day", "night"}:
            raise ValueError("theme_mode must be day, night, or None")
        prefix = cls._cache_identity_prefix(instance_uuid)
        suffix = "" if theme_mode is None else f"-{theme_mode}"
        return (
            f"{prefix}-{int(structural_generation)}-"
            f"{int(settings_revision)}{suffix}.png"
        )

    def cache_path_for_snapshot(self, instance):
        """Public read-only cache location for an immutable instance snapshot."""
        plugin_config = self.device_config.get_plugin(instance.plugin_id)
        resolved_theme = _resolved_theme_context_for_instance(
            instance,
            plugin_config,
            self.device_config,
            current_dt=self._get_current_datetime(),
        )
        theme_mode = (
            resolved_theme.get("mode")
            if isinstance(resolved_theme, Mapping)
            else None
        )
        runtime_instance = self.runtime_state.snapshot().instances.get(
            instance.instance_uuid,
            InstanceRuntimeState(),
        )
        candidate = self.cache_catalog.resolve(
            instance,
            theme_mode,
            runtime_instance,
        )
        if candidate is not None:
            return candidate.cache_path
        return self._snapshot_cache_path(instance, theme_mode)

    def compatibility_cache_path_for_snapshot(self, instance):
        """Return the old name-based preview path; never use it for scheduling."""
        return os.path.join(
            self.device_config.plugin_image_dir,
            f"{instance.plugin_id}_{instance.name.replace(' ', '_')}.png",
        )

    def _process_queue_entry(self, entry: QueueEntry):
        # Work already queued (including manual work or an IAN continuation)
        # owns this turn and consumes an unused completion recheck.
        self._background_scheduler_recheck_pending = False
        if (
            self._lightweight_followup_remaining > 0
            and entry.command.payload.get("scheduler_lightweight_followup")
            is not True
        ):
            self._lightweight_followup_remaining = 0
            self.scheduler_state.set_next_attempt(
                self._clock() + self._scheduler_poll_seconds()
            )
        try:
            context = TaskContext(
                entry.cancel_event,
                entry.command.deadline_monotonic,
                self._clock,
            )
            try:
                context.raise_if_cancelled()
            except TaskDeadlineExceeded as error:
                current_entry = self.refresh_queue.get_entry(entry.job.id)
                if (
                    current_entry is None
                    or current_entry.job.status is not JobStatus.RUNNING
                ):
                    # A retained Ian snapshot can outlive executor
                    # terminalization by one turn. Discard that stale
                    # retention without reclassifying completed work.
                    self._ian_retained_entries.pop(entry.command.id, None)
                    self._ian_recorded_deferrals.discard(entry.command.id)
                    return
                if entry.cancel_event.is_set():
                    self._finish_pre_execution_entry(
                        entry,
                        JobStatus.CANCELED,
                        error_code="task_canceled",
                        error="refresh command was canceled before execution",
                    )
                else:
                    # Establish this attempt before comparing terminal lane state;
                    # otherwise old success/failure timestamps can suppress cleanup.
                    self._record_runtime_attempt(entry.command)
                    self._record_rotation_deadline_failure_safely(
                        entry.command,
                        error,
                    )
                    self._finish_pre_execution_entry(
                        entry,
                        JobStatus.ABANDONED,
                        error_code="deadline_expired",
                        error=str(error),
                    )
                return
            except TaskCancelled as error:
                self._finish_pre_execution_entry(
                    entry,
                    JobStatus.CANCELED,
                    error_code="task_canceled",
                    error=str(error),
                )
                return
            if self._uses_ian_admission(entry.command):
                self._process_ian_queue_entry(entry)
                return
            self._execute_queue_entry(entry)
        finally:
            self._finalize_lightweight_followup_entry(entry)
            # Execution cleanup and IAN retention release must finish before
            # sampling capacity. Every terminal outcome frees this queue turn;
            # the failed instance's own retry is still enforced by admission.
            finished = self.refresh_queue.get_job(entry.job.id)
            if finished is not None:
                try:
                    self._note_scheduler_terminal(entry.command, finished)
                except Exception:
                    logger.exception("Could not request scheduling after command cleanup.")

    def _uses_ian_admission(self, command):
        return (
            command.source is CommandSource.BACKGROUND
            and self._is_isolated_sports_refresh_command(command)
        )

    def _sample_ian_resources(self):
        sample = self._resource_sample()
        return IanResourceSample(
            available_mb=sample.available_mb,
            swap_percent=sample.swap_percent,
        )

    def _ian_request_canceled(self, request):
        entry = self._ian_retained_entries.get(request.request_id)
        return bool(
            self.stop_event.is_set()
            or (entry is not None and entry.cancel_event.is_set())
        )

    def _execute_ian_stage(self, request, _stage, _previous_checkpoint):
        entry = self._ian_retained_entries.get(request.request_id)
        if entry is None:
            raise RuntimeError("Ian request has no matching running refresh job")
        self._execute_queue_entry(entry, ian_admitted=True)
        current = self.refresh_queue.get_entry(entry.job.id)
        return IanExecutionResult(
            result=None if current is None else current.job.status.value,
        )

    def _ian_retry_seconds(self):
        value = self._config_float(
            "ian_admission_retry_seconds",
            DEFAULT_IAN_ADMISSION_RETRY_SECONDS,
        )
        if not math.isfinite(value):
            value = DEFAULT_IAN_ADMISSION_RETRY_SECONDS
        return max(0.05, min(30.0, value))

    def _process_ian_queue_entry(self, entry):
        request_id = entry.command.id
        if request_id not in self._ian_retained_entries:
            if len(self._ian_retained_entries) >= self._ian_retained_limit:
                self._record_resource_pressure_deferral(entry.command)
                self._ian_last_turn_status = "retained_capacity_deferred"
                logger.warning(
                    "Ian retained capacity reached; deferring new background "
                    "refresh. | plugin_id: %s | request_id: %s | "
                    "retained: %s | retained_limit: %s",
                    entry.command.plugin_id,
                    request_id,
                    len(self._ian_retained_entries),
                    self._ian_retained_limit,
                )
                self._finish_queue_entry_once(
                    entry,
                    JobStatus.CANCELED,
                    error_code="ian_retained_capacity",
                    error="Ian retained capacity is reserved for urgent work",
                )
                return
            try:
                request = self._ian_request_adapter(entry.command)
            except Exception:
                logger.exception(
                    "Could not adapt background refresh for Ian. | "
                    "plugin_id: %s | request_id: %s",
                    entry.command.plugin_id,
                    request_id,
                )
                self._finish_queue_entry_once(
                    entry,
                    JobStatus.FAILED,
                    error_code="ian_adapter_failed",
                    error="Background execution admission could not be prepared",
                )
                return
            offer = self._ian.offer(request)
            if offer.status is IanOfferStatus.REJECTED:
                if (
                    offer.reason == "ian_deadline_expired"
                    and not entry.cancel_event.is_set()
                ):
                    self._record_runtime_attempt(entry.command)
                    self._record_rotation_deadline_failure_safely(
                        entry.command,
                        TaskDeadlineExceeded(
                            "Ian rejected an expired background command"
                        ),
                    )
                status = (
                    JobStatus.ABANDONED
                    if offer.reason == "ian_deadline_expired"
                    else JobStatus.FAILED
                )
                self._finish_queue_entry_once(
                    entry,
                    status,
                    error_code=offer.reason or "ian_offer_rejected",
                    error="Ian rejected background execution admission",
                )
                return
            if offer.status is IanOfferStatus.COALESCED:
                self._ian_last_turn_status = "coalesced"
                self._finish_queue_entry_once(
                    entry,
                    JobStatus.CANCELED,
                    error_code="ian_coalesced",
                    error="Equivalent background execution is already retained",
                )
            else:
                superseded_id = offer.superseded_request_id
                if (
                    offer.status is IanOfferStatus.SUPERSEDED
                    and superseded_id is not None
                ):
                    self._finish_ian_entry(
                        superseded_id,
                        JobStatus.CANCELED,
                        error_code="ian_superseded",
                        error="A newer background execution superseded this job",
                    )
                self._ian_retained_entries[request_id] = entry
                self._ian_last_turn_status = offer.status.value
                logger.info(
                    "Ian retained background refresh. | plugin_id: %s | "
                    "request_id: %s | offer_status: %s | retained: %s",
                    entry.command.plugin_id,
                    request_id,
                    offer.status.value,
                    len(self._ian_retained_entries),
                )

        turn = self._ian.run_turn()
        self._ian_last_turn_status = turn.status.value
        if turn.status is IanTurnStatus.IDLE:
            if self._ian_retained_entries:
                for retained_id in tuple(self._ian_retained_entries):
                    self._finish_ian_entry(
                        retained_id,
                        JobStatus.FAILED,
                        error_code="ian_lost_request",
                        error="Ian lost a retained background execution",
                    )
            self._ian_retry_not_before = self._clock()
            return
        if turn.status in {
            IanTurnStatus.DEFERRED,
            IanTurnStatus.RESOURCE_UNKNOWN,
            IanTurnStatus.DRAINING,
            IanTurnStatus.COOLDOWN,
            IanTurnStatus.CHECKPOINTED,
        }:
            deferred_id = (
                None if turn.request is None else turn.request.request_id
            )
            deferred_entry = self._ian_retained_entries.get(deferred_id)
            if (
                deferred_entry is not None
                and deferred_id not in self._ian_recorded_deferrals
            ):
                self._record_resource_pressure_deferral(
                    deferred_entry.command
                )
                self._ian_recorded_deferrals.add(deferred_id)
            self._ian_retry_not_before = self._clock() + self._ian_retry_seconds()
            logger.info(
                "Ian deferred background execution turn. | status: %s | "
                "request_id: %s | retained: %s | retry_not_before: %s",
                turn.status.value,
                None if turn.request is None else turn.request.request_id,
                len(self._ian_retained_entries),
                self._ian_retry_not_before,
            )
            return
        terminal_id = (
            None if turn.request is None else turn.request.request_id
        )
        if terminal_id is None:
            for retained_id in tuple(self._ian_retained_entries):
                self._finish_ian_entry(
                    retained_id,
                    JobStatus.FAILED,
                    error_code="ian_invalid_turn",
                    error="Ian returned a terminal turn without a request",
                )
            self._ian_retry_not_before = self._clock()
            return
        if turn.status is IanTurnStatus.SUCCEEDED:
            terminal_entry = self._finish_ian_entry(
                terminal_id,
                JobStatus.SUCCEEDED,
            )
        elif turn.status is IanTurnStatus.DEADLINE_EXPIRED:
            expired_entry = self._ian_retained_entries.get(terminal_id)
            current_entry = (
                None
                if expired_entry is None
                else self.refresh_queue.get_entry(expired_entry.job.id)
            )
            if (
                expired_entry is not None
                and current_entry is not None
                and current_entry.job.status is JobStatus.RUNNING
                and not expired_entry.cancel_event.is_set()
            ):
                # Ian can observe its deadline immediately after the executor
                # has already committed a terminal success. Only a still-
                # running queue job remains eligible for deadline bookkeeping.
                self._record_runtime_attempt(expired_entry.command)
                self._record_rotation_deadline_failure_safely(
                    expired_entry.command,
                    TaskDeadlineExceeded(
                        "Ian background execution deadline expired"
                    ),
                )
            terminal_entry = self._finish_ian_entry(
                terminal_id,
                JobStatus.ABANDONED,
                error_code=turn.reason or "ian_deadline_expired",
                error="Ian background execution deadline expired",
            )
        elif turn.status is IanTurnStatus.CANCELED:
            terminal_entry = self._finish_ian_entry(
                terminal_id,
                JobStatus.CANCELED,
                error_code=turn.reason or "ian_execution_canceled",
                error="Ian background execution canceled",
            )
        elif turn.status is IanTurnStatus.FAILED:
            terminal_entry = self._finish_ian_entry(
                terminal_id,
                JobStatus.FAILED,
                error_code=turn.reason or "ian_execution_failed",
                error=(
                    "Ian background execution failed"
                    if turn.error is None
                    else str(turn.error)
                ),
            )
        else:
            terminal_entry = self._finish_ian_entry(
                terminal_id,
                JobStatus.FAILED,
                error_code="ian_invalid_turn",
                error=f"Ian returned unexpected turn status: {turn.status.value}",
            )
        queue_status = (
            None if terminal_entry is None else terminal_entry.job.status.value
        )
        self._ian_last_queue_status = queue_status
        logger.info(
            "Ian completed background admission turn. | status: %s | "
            "queue_status: %s | request_id: %s | retained: %s",
            turn.status.value,
            queue_status,
            terminal_id,
            len(self._ian_retained_entries),
        )
        self._ian_retry_not_before = self._clock()

    def _finish_ian_entry(
        self,
        request_id,
        status,
        *,
        error_code=None,
        error=None,
    ):
        entry = self._ian_retained_entries.pop(request_id, None)
        self._ian_recorded_deferrals.discard(request_id)
        if entry is None:
            return None
        return self._finish_queue_entry_once(
            entry,
            status,
            error_code=error_code,
            error=error,
        )

    def _finish_pre_execution_entry(
        self,
        entry,
        status,
        *,
        error_code=None,
        error=None,
    ):
        if entry.command.id in self._ian_retained_entries:
            return self._finish_ian_entry(
                entry.command.id,
                status,
                error_code=error_code,
                error=error,
            )
        return self._finish_queue_entry_once(
            entry,
            status,
            error_code=error_code,
            error=error,
        )

    def _finish_queue_entry_once(
        self,
        entry,
        status,
        *,
        error_code=None,
        error=None,
    ):
        current = self.refresh_queue.get_entry(entry.job.id)
        if current is None or current.job.status is not JobStatus.RUNNING:
            return current
        finished = self.refresh_queue.finish(
            entry.job.id,
            status,
            error_code=error_code,
            error=error,
        )
        self._signal_completion(finished.id)
        self._cleanup_transient_uploads(entry.job.id, entry.command)
        return self.refresh_queue.get_entry(finished.id)

    def _finalize_retained_ian_entries(self):
        for request_id in tuple(self._ian_retained_entries):
            self._finish_ian_entry(
                request_id,
                JobStatus.CANCELED,
                error_code="ian_worker_stopped",
                error="Ian background execution stopped before admission",
            )
        if self._ian_last_turn_status not in {
            IanTurnStatus.SUCCEEDED.value,
            IanTurnStatus.FAILED.value,
            IanTurnStatus.CANCELED.value,
            IanTurnStatus.DEADLINE_EXPIRED.value,
        }:
            self._ian_last_turn_status = "stopped"

    def _execute_queue_entry(self, entry: QueueEntry, *, ian_admitted=False):
        command = entry.command
        context = TaskContext(
            entry.cancel_event,
            command.deadline_monotonic,
            self._clock,
        )
        self._execution_local.context = context
        active_intent = getattr(command.intent, "value", command.intent)
        self._active_operation = ActiveOperationSnapshot(
            command_id=command.id,
            kind=command.kind.value,
            source=command.source.value,
            intent="unknown" if active_intent is None else str(active_intent),
            plugin_id=command.plugin_id,
            instance_uuid=command.instance_uuid,
            started_monotonic=self._clock(),
            deadline_monotonic=command.deadline_monotonic,
        )
        busy_lock = None
        if command.source is CommandSource.MANUAL:
            busy_lock = self.manual_refresh_lock
        elif command.kind is CommandKind.CACHE_REFRESH:
            busy_lock = self.cache_refresh_lock
        if busy_lock is not None:
            busy_lock.acquire()
        try:
            if self._renderer_blocked_by_disk_pressure(command):
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.CANCELED,
                    error_code="disk_pressure_hard",
                    error="renderer blocked while disk pressure remains hard",
                )
                self._signal_completion(finished.id)
                return
            if self._is_ticketmaster_background_data_command(command):
                if self._resolve_playlist_command(command) is None:
                    finished = self.refresh_queue.finish(
                        entry.job.id,
                        JobStatus.CANCELED,
                        error_code="stale_selection",
                        error=(
                            "playlist selection changed before Ticketmaster "
                            "resource admission"
                        ),
                    )
                    self._signal_completion(finished.id)
                    return
                resource_sample = self._resource_sample()
                (
                    margin_available,
                    required_available_mb,
                    max_swap_percent,
                ) = self._ticketmaster_background_start_margin(resource_sample)
                if not margin_available:
                    next_retry_at = self._record_resource_pressure_deferral(command)
                    logger.warning(
                        "Deferring Ticketmaster background data refresh until "
                        "its memory reserve is available. | plugin_id: %s | "
                        "source: %s | intent: %s | available_mb: %s | "
                        "required_available_mb: %s | swap_percent: %s | "
                        "max_swap_percent: %s | next_retry_at: %s",
                        command.plugin_id,
                        command.source.value,
                        command.intent.value,
                        resource_sample.available_mb,
                        required_available_mb,
                        resource_sample.swap_percent,
                        max_swap_percent,
                        next_retry_at,
                    )
                    finished = self.refresh_queue.finish(
                        entry.job.id,
                        JobStatus.CANCELED,
                        error_code="plugin_resource_reserve",
                        error=(
                            "Ticketmaster background data refresh deferred "
                            "until its memory reserve is available"
                        ),
                    )
                    self._signal_completion(finished.id)
                    return
            if self._is_weather_background_data_command(command):
                resource_sample = self._resource_sample()
                concession = bool(
                    command.payload.get("weather_liveness_concession")
                )
                if concession:
                    margin_available, required_available_mb = (
                        self._weather_concession_margin(resource_sample)
                    )
                    max_swap_percent = None
                else:
                    (
                        margin_available,
                        required_available_mb,
                        max_swap_percent,
                    ) = self._weather_background_start_margin(resource_sample)
                if not margin_available:
                    next_retry_at = self._record_resource_pressure_deferral(command)
                    logger.warning(
                        "Deferring Weather background data refresh until its "
                        "browser start margin is available. | plugin_id: %s | "
                        "source: %s | intent: %s | concession: %s | "
                        "available_mb: %s | required_available_mb: %s | "
                        "swap_percent: %s | max_swap_percent: %s | next_retry_at: %s",
                        command.plugin_id,
                        command.source.value,
                        command.intent.value,
                        concession,
                        resource_sample.available_mb,
                        required_available_mb,
                        resource_sample.swap_percent,
                        max_swap_percent,
                        next_retry_at,
                    )
                    finished = self.refresh_queue.finish(
                        entry.job.id,
                        JobStatus.CANCELED,
                        error_code="weather_browser_start_margin",
                        error=(
                            "Weather background data refresh deferred until its "
                            "browser start margin is available"
                        ),
                    )
                    self._signal_completion(finished.id)
                    return
            if (
                not ian_admitted
                and
                command.plugin_id in _HEAVYWEIGHT_RENDERER_PLUGIN_IDS
                and command.intent in _RENDERER_INTENTS
            ):
                isolated_sports_refresh = self._is_isolated_sports_refresh_command(
                    command
                )
                resource_sample = self._resource_sample()
                resource_tier = classify_resource_tier(
                    resource_sample,
                    self._resource_thresholds(),
                )
                self._resource_tier = resource_tier
                if (
                    resource_tier is ResourceTier.HARD
                    or (
                        not isolated_sports_refresh
                        and resource_tier is not ResourceTier.HEALTHY
                    )
                ):
                    next_retry_at = self._record_resource_pressure_deferral(command)
                    logger.warning(
                        "Deferring heavyweight renderer due to resource pressure. | "
                        "plugin_id: %s | intent: %s | tier: %s | "
                        "available_mb: %s | swap_percent: %s | next_retry_at: %s",
                        command.plugin_id,
                        command.intent.value,
                        resource_tier.value,
                        resource_sample.available_mb,
                        resource_sample.swap_percent,
                        next_retry_at,
                    )
                    finished = self.refresh_queue.finish(
                        entry.job.id,
                        JobStatus.CANCELED,
                        error_code=f"resource_pressure_{resource_tier.value}",
                        error=(
                            "heavyweight renderer deferred under "
                            f"{resource_tier.value} resource pressure"
                        ),
                    )
                    self._signal_completion(finished.id)
                    return
                if isolated_sports_refresh:
                    (
                        margin_available,
                        required_available_mb,
                        max_swap_percent,
                    ) = self._sports_isolated_start_margin(resource_sample)
                    if not margin_available:
                        next_retry_at = self._record_resource_pressure_deferral(
                            command
                        )
                        logger.warning(
                            "Deferring isolated Sports Dashboard data refresh "
                            "until its child-process start margin is available. | "
                            "available_mb: %s | swap_percent: %s | "
                            "required_available_mb: %s | max_swap_percent: %s | "
                            "next_retry_at: %s",
                            resource_sample.available_mb,
                            resource_sample.swap_percent,
                            required_available_mb,
                            max_swap_percent,
                            next_retry_at,
                        )
                        finished = self.refresh_queue.finish(
                            entry.job.id,
                            JobStatus.CANCELED,
                            error_code=(
                                "resource_pressure_soft"
                                if resource_tier is ResourceTier.SOFT
                                else "sports_isolated_start_margin"
                            ),
                            error=(
                                "isolated Sports Dashboard data refresh deferred "
                                "until its start margin is available"
                            ),
                        )
                        self._signal_completion(finished.id)
                        return
                else:
                    (
                        margin_available,
                        required_available_mb,
                        max_swap_percent,
                    ) = self._heavyweight_renderer_resource_margin(resource_sample)
                    if not margin_available:
                        next_retry_at = self._record_resource_pressure_deferral(command)
                        logger.warning(
                            "Deferring heavyweight renderer because "
                            "the dedicated resource margin is unavailable. | "
                            "plugin_id: %s | intent: %s | available_mb: %s | "
                            "swap_percent: %s | "
                            "required_available_mb: %s | max_swap_percent: %s | "
                            "next_retry_at: %s",
                            command.plugin_id,
                            command.intent.value,
                            resource_sample.available_mb,
                            resource_sample.swap_percent,
                            required_available_mb,
                            max_swap_percent,
                            next_retry_at,
                        )
                        finished = self.refresh_queue.finish(
                            entry.job.id,
                            JobStatus.CANCELED,
                            error_code="heavyweight_renderer_margin",
                            error=(
                                "heavyweight renderer deferred until "
                                "its dedicated resource margin is available"
                            ),
                        )
                        self._signal_completion(finished.id)
                        return
            instance_uuid_hash = (
                hashlib.sha256(command.instance_uuid.encode("utf-8")).hexdigest()[:16]
                if command.instance_uuid
                else "none"
            )
            logger.info(
                "Refresh command started. | source: %s | intent: %s | "
                "plugin_id: %s | instance_uuid_hash: %s",
                command.source.value,
                command.intent.value if command.intent is not None else "none",
                command.plugin_id,
                instance_uuid_hash,
            )
            self._record_runtime_attempt(command)
            try:
                identity = InstanceIdentity(
                    command.instance_uuid,
                    command.structural_generation,
                    command.settings_revision,
                )
                identity_validator = (
                    lambda candidate: self._isolated_instance_identity_is_current(
                        command,
                        candidate,
                    )
                )
                with bind_long_task_runtime(
                    context,
                    identity,
                    identity_validator=identity_validator,
                    parallel_image_runner=self._parallel_image_runner,
                ):
                    self._execute_command(command)
            except SportsIsolatedCheckpointPending as pending:
                try:
                    context.raise_if_cancelled()
                except (TaskDeadlineExceeded, TaskCancelled) as abort_error:
                    status, error_code, abort_message = self._abort_details(
                        abort_error
                    )
                    if (
                        isinstance(abort_error, TaskDeadlineExceeded)
                        and not entry.cancel_event.is_set()
                    ):
                        self._record_rotation_deadline_failure_safely(
                            command,
                            abort_error,
                        )
                    finished = self.refresh_queue.finish(
                        entry.job.id,
                        status,
                        error_code=error_code,
                        error=abort_message,
                    )
                    self._signal_completion(finished.id)
                    return
                try:
                    yielded = self.refresh_queue.yield_running(entry.job.id)
                except Exception as error:
                    logger.exception(
                        "Sports Dashboard checkpoint could not return its queue permit"
                    )
                    finished = self.refresh_queue.finish(
                        entry.job.id,
                        JobStatus.FAILED,
                        error_code="sports_checkpoint_yield_failed",
                        error=str(error),
                    )
                    self._signal_completion(finished.id)
                    return
                logger.info(
                    "Isolated Sports Dashboard returned its queue permit after "
                    "one durable region. | completed_regions: %s | "
                    "next_region: %s | queue_status: %s",
                    ",".join(pending.completed_regions),
                    pending.next_region,
                    yielded.status.value,
                )
                if (
                    yielded.error_code == "deadline_expired"
                    and yielded.cancel_requested_at is None
                    and not self.stop_event.is_set()
                ):
                    # The deadline can cross between the context check and the
                    # queue's atomic yield. Recover the same exact rotation
                    # failure/release bookkeeping used by direct aborts.
                    self._record_rotation_deadline_failure_safely(
                        command,
                        TaskDeadlineExceeded(
                            "Sports Dashboard checkpoint deadline expired"
                        ),
                    )
                if yielded.status is not JobStatus.QUEUED:
                    self._signal_completion(yielded.id)
                return
            except SportsIsolatedResourcePressure as error:
                next_retry_at = self._record_resource_pressure_deferral(command)
                logger.warning(
                    "Isolated Sports Dashboard worker stopped before resource "
                    "pressure could threaten the service. | next_retry_at: %s",
                    next_retry_at,
                )
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.CANCELED,
                    error_code="sports_isolated_resource_pressure",
                    error=str(error),
                )
            except PluginRefreshDeferred as error:
                next_retry_at = self._record_plugin_refresh_deferral(
                    command,
                    minimum_seconds=error.minimum_seconds,
                )
                logger.warning(
                    "Deferring plugin-requested refresh. | plugin_id: %s | "
                    "intent: %s | reason: %s | phase: %s | "
                    "minimum_seconds: %s | next_retry_at: %s",
                    command.plugin_id,
                    command.intent.value if command.intent is not None else "none",
                    error.reason,
                    error.phase,
                    error.minimum_seconds,
                    next_retry_at,
                )
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.CANCELED,
                    error_code="plugin_refresh_deferred",
                    error="plugin requested a bounded refresh retry",
                )
            except ResourcePressureDeferred as error:
                weather_background_data = self._is_weather_background_data_command(
                    command
                )
                next_retry_at = self._record_resource_pressure_deferral(
                    command,
                    minimum_seconds=(
                        MIN_WEATHER_RESOURCE_PRESSURE_DEFERRAL_SECONDS
                        if weather_background_data
                        else 0
                    ),
                )
                weather_window = self._weather_liveness_window
                if (
                    weather_background_data
                    and weather_window is not None
                    and weather_window.instance_uuid == command.instance_uuid
                    and weather_window.candidate.instance.structural_generation
                    == command.structural_generation
                    and weather_window.candidate.instance.settings_revision
                    == command.settings_revision
                ):
                    self._finish_weather_liveness_window(
                        reason="resource_pressure",
                        resource_sample=ResourceSample(
                            available_mb=error.available_mb,
                            swap_percent=error.swap_percent,
                        ),
                    )
                logger.warning(
                    "Deferring refresh after typed resource pressure. | "
                    "plugin_id: %s | intent: %s | reason: %s | phase: %s | "
                    "available_mb: %s | swap_percent: %s | next_retry_at: %s",
                    command.plugin_id,
                    command.intent.value if command.intent is not None else "none",
                    error.reason,
                    error.phase,
                    error.available_mb,
                    error.swap_percent,
                    next_retry_at,
                )
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.CANCELED,
                    error_code="resource_pressure_deferred",
                    error=str(error),
                )
            except TaskDeadlineExceeded as error:
                if not entry.cancel_event.is_set():
                    self._record_rotation_deadline_failure_safely(command, error)
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.ABANDONED,
                    error_code="deadline_expired",
                    error=str(error),
                )
            except _CacheUnavailable as error:
                if (
                    command.intent is RefreshIntent.DISPLAY_CACHE
                    and plugin_supports_cached_display_redraw(
                        self.device_config.get_plugin(command.plugin_id)
                    )
                ):
                    self._record_rotation_deadline_failure_safely(command, error)
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.CANCELED,
                    error_code="cache_unavailable",
                    error=str(error),
                )
            except _StaleSelection as error:
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.CANCELED,
                    error_code="stale_selection",
                    error=str(error),
                )
            except _PreparedDisplayFailure as error:
                try:
                    self._record_presentation_failure(
                        command,
                        error.original_error,
                        self._get_current_datetime(),
                    )
                except Exception:
                    logger.exception(
                        "Prepared display failure bookkeeping also failed"
                    )
                    self._defer_scheduler_after_bookkeeping_error()
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.FAILED,
                    error_code="presentation_display_failed",
                    error=str(error.original_error),
                )
            except TaskCancelled as error:
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.CANCELED,
                    error_code="task_canceled",
                    error=str(error),
                )
            except Exception as error:
                logger.exception(
                    "Refresh command failed. | source: %s | intent: %s | plugin_id: %s",
                    command.source,
                    command.intent,
                    command.plugin_id,
                )
                abort = self._classify_command_abort(command, context)
                if abort is None:
                    try:
                        self._record_command_failure(command, error)
                    except (TaskDeadlineExceeded, _StaleSelection, TaskCancelled) as abort_error:
                        abort = self._abort_details(abort_error)
                    except Exception:
                        logger.exception("Refresh failure bookkeeping also failed")
                        self._defer_scheduler_after_bookkeeping_error()
                if abort is None:
                    abort = self._classify_command_abort(command, context)
                if abort is None:
                    finished = self.refresh_queue.finish(
                        entry.job.id,
                        JobStatus.FAILED,
                        error_code="refresh_failed",
                        error=str(error),
                    )
                else:
                    status, error_code, abort_error = abort
                    if (
                        error_code == "deadline_expired"
                        and not entry.cancel_event.is_set()
                    ):
                        self._record_rotation_deadline_failure_safely(
                            command,
                            TaskDeadlineExceeded(abort_error),
                        )
                    finished = self.refresh_queue.finish(
                        entry.job.id,
                        status,
                        error_code=error_code,
                        error=abort_error,
                    )
            else:
                try:
                    context.raise_if_cancelled()
                except (TaskDeadlineExceeded, TaskCancelled) as abort_error:
                    status, error_code, abort_message = self._abort_details(abort_error)
                    finished = self.refresh_queue.finish(
                        entry.job.id,
                        status,
                        error_code=error_code,
                        error=abort_message,
                    )
                else:
                    degraded_data_result = bool(
                        getattr(
                            self._execution_local,
                            "degraded_data_result",
                            False,
                        )
                    )
                    if degraded_data_result:
                        finished = self.refresh_queue.finish(
                            entry.job.id,
                            JobStatus.FAILED,
                            error_code="degraded_result",
                            error=(
                                "Refresh produced a display-safe result that was "
                                "not promoted."
                            ),
                        )
                    else:
                        finished = self.refresh_queue.finish(
                            entry.job.id,
                            JobStatus.SUCCEEDED,
                        )
                    try:
                        if not degraded_data_result:
                            lane = self._lane_for_intent(command.intent)
                            retry_key = (
                                self._lane_retry_key(command.instance_uuid, lane)
                                if command.instance_uuid is not None and lane is not None
                                else command.instance_uuid or RetryRegistry.GLOBAL_KEY
                            )
                            self.retry_registry.mark_success(retry_key)
                            self.scheduler_state.record_success()
                    except Exception:
                        logger.exception("Refresh success bookkeeping failed")
            self._note_lightweight_scheduler_terminal(command, finished)
            self._signal_completion(finished.id)
        finally:
            self._cleanup_transient_uploads(entry.job.id, entry.command)
            if busy_lock is not None:
                busy_lock.release()
            self._execution_local.context = None
            self._execution_local.degraded_data_result = False
            self._execution_local.effective_theme_context = None
            self._active_operation = None
            try:
                self._run_memory_maintenance(
                    "refresh-command-finally",
                    force=(
                        command.plugin_id == "telegram_digest"
                        and command.intent
                        in {
                            RefreshIntent.DATA_REFRESH,
                            RefreshIntent.PRESENTATION_REFRESH,
                        }
                    ),
                    command=command,
                )
            except Exception:
                logger.exception("Refresh memory maintenance failed")

    def _current_task_context(self, command):
        context = getattr(self._execution_local, "context", None)
        if context is not None:
            return context
        return TaskContext.never_cancelled(
            deadline_monotonic=command.deadline_monotonic,
            clock=self._clock,
        )

    def _runtime_now_iso(self, *, offset_seconds=0.0):
        return datetime.fromtimestamp(
            float(self._wall_clock()) + float(offset_seconds),
            tz=timezone.utc,
        ).isoformat()

    def _record_runtime_attempt(self, command):
        lane = self._lane_for_intent(command.intent)
        if command.instance_uuid is None or lane is None:
            return
        try:
            self.runtime_state.record_attempt(
                command.instance_uuid,
                self._runtime_now_iso(),
                lane=lane,
            )
        except Exception:
            logger.exception(
                "Runtime refresh attempt state could not be recorded. | instance_uuid: %s",
                command.instance_uuid,
            )

    def _record_resource_pressure_deferral(self, command, *, minimum_seconds=0):
        return self._record_lane_resource_pressure_deferral(
            command.instance_uuid,
            command.intent,
            minimum_seconds=minimum_seconds,
        )

    def _record_plugin_refresh_deferral(self, command, *, minimum_seconds):
        lane = self._lane_for_intent(command.intent)
        if command.instance_uuid is None or lane is None:
            return None
        deferred_at = self._runtime_now_iso()
        next_retry_at = (
            datetime.fromisoformat(deferred_at)
            + timedelta(seconds=float(minimum_seconds))
        ).isoformat()
        try:
            self.runtime_state.record_deferral(
                command.instance_uuid,
                deferred_at,
                next_retry_at,
                lane=lane,
            )
        except Exception:
            logger.exception(
                "Plugin-requested refresh deferral could not be recorded. | "
                "plugin_id: %s",
                command.plugin_id,
            )
            self._defer_scheduler_after_bookkeeping_error()
            return None
        return next_retry_at

    def _record_lane_resource_pressure_deferral(
        self,
        instance_uuid,
        intent,
        *,
        minimum_seconds=0,
    ):
        lane = self._lane_for_intent(intent)
        if instance_uuid is None or lane is None:
            return None
        poll_seconds = self._scheduler_poll_seconds()
        spacing_seconds = self._resource_thresholds().soft_spacing_seconds
        delay_seconds = max(
            poll_seconds,
            minimum_seconds,
            min(
                MAX_RESOURCE_PRESSURE_DEFERRAL_SECONDS,
                max(poll_seconds, spacing_seconds),
            ),
        )
        deferred_at = self._runtime_now_iso()
        next_retry_at = (
            datetime.fromisoformat(deferred_at) + timedelta(seconds=delay_seconds)
        ).isoformat()
        try:
            self.runtime_state.record_deferral(
                instance_uuid,
                deferred_at,
                next_retry_at,
                lane=lane,
            )
        except Exception:
            logger.exception(
                "Runtime resource-pressure deferral could not be recorded. | "
                "instance_uuid: %s",
                instance_uuid,
            )
            self._defer_scheduler_after_bookkeeping_error()
            return None
        return next_retry_at

    def _heavyweight_renderer_resource_margin(self, sample):
        min_available_mb = self._config_float(
            "heavyweight_renderer_min_available_mb",
            DEFAULT_HEAVYWEIGHT_RENDERER_MIN_AVAILABLE_MB,
        )
        if not math.isfinite(min_available_mb) or min_available_mb < 0:
            min_available_mb = DEFAULT_HEAVYWEIGHT_RENDERER_MIN_AVAILABLE_MB
        max_swap_percent = self._config_float(
            "heavyweight_renderer_max_swap_percent",
            DEFAULT_HEAVYWEIGHT_RENDERER_MAX_SWAP_PERCENT,
        )
        if (
            not math.isfinite(max_swap_percent)
            or max_swap_percent < 0
            or max_swap_percent > 100
        ):
            max_swap_percent = DEFAULT_HEAVYWEIGHT_RENDERER_MAX_SWAP_PERCENT
        try:
            available_mb = float(sample.available_mb)
            swap_percent = float(sample.swap_percent)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False, min_available_mb, max_swap_percent
        margin_available = (
            math.isfinite(available_mb)
            and math.isfinite(swap_percent)
            and available_mb >= min_available_mb
            and swap_percent < max_swap_percent
        )
        return margin_available, min_available_mb, max_swap_percent

    @staticmethod
    def _is_weather_background_data_command(command):
        return (
            command.plugin_id == "weather"
            and command.kind is CommandKind.CACHE_REFRESH
            and command.source is CommandSource.BACKGROUND
            and command.intent is RefreshIntent.DATA_REFRESH
            and bool(command.payload.get("playlist_name"))
        )

    def _weather_background_start_margin(self, sample):
        min_available_mb = self._config_float(
            "weather_background_start_min_available_mb",
            DEFAULT_WEATHER_BACKGROUND_START_MIN_AVAILABLE_MB,
        )
        if not math.isfinite(min_available_mb) or min_available_mb < 0:
            min_available_mb = DEFAULT_WEATHER_BACKGROUND_START_MIN_AVAILABLE_MB
        max_swap_percent = self._config_float(
            "weather_background_start_max_swap_percent",
            DEFAULT_WEATHER_BACKGROUND_START_MAX_SWAP_PERCENT,
        )
        if (
            not math.isfinite(max_swap_percent)
            or max_swap_percent < 0
            or max_swap_percent > 100
        ):
            max_swap_percent = DEFAULT_WEATHER_BACKGROUND_START_MAX_SWAP_PERCENT
        try:
            available_mb = float(sample.available_mb)
            swap_percent = float(sample.swap_percent)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False, min_available_mb, max_swap_percent
        return (
            math.isfinite(available_mb)
            and math.isfinite(swap_percent)
            and available_mb >= min_available_mb
            and swap_percent < max_swap_percent,
            min_available_mb,
            max_swap_percent,
        )

    def _weather_concession_margin(self, sample):
        min_available_mb = self._config_float(
            "weather_liveness_concession_min_available_mb",
            DEFAULT_WEATHER_LIVENESS_CONCESSION_MIN_AVAILABLE_MB,
        )
        if not math.isfinite(min_available_mb) or min_available_mb < 0:
            min_available_mb = (
                DEFAULT_WEATHER_LIVENESS_CONCESSION_MIN_AVAILABLE_MB
            )
        try:
            available_mb = float(sample.available_mb)
            swap_percent = float(sample.swap_percent)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False, min_available_mb
        return (
            math.isfinite(available_mb)
            and math.isfinite(swap_percent)
            and available_mb >= min_available_mb,
            min_available_mb,
        )

    def _weather_liveness_seconds(self, key, default, maximum):
        value = self._config_float(key, default)
        if not math.isfinite(value) or value < 0:
            value = float(default)
        return min(float(maximum), value)

    def _request_burst_liveness_ordinary_yield(self):
        configured_seconds = self._weather_liveness_seconds(
            "burst_liveness_ordinary_yield_seconds",
            DEFAULT_BURST_LIVENESS_ORDINARY_YIELD_SECONDS,
            90,
        )
        # Arm only the one-shot right here. The single worker may spend longer
        # than the configured window executing the accepted burst command, so
        # the first subsequent independent selector starts the wall-clock wait.
        self._burst_liveness_yield_ordinary_pending = configured_seconds > 0
        self._burst_liveness_yield_deadline_monotonic = 0.0

    def _update_burst_liveness_ordinary_yield_window(self):
        if not self._burst_liveness_yield_ordinary_pending:
            return
        now = self._clock()
        deadline = self._burst_liveness_yield_deadline_monotonic
        if deadline <= 0:
            duration = self._weather_liveness_seconds(
                "burst_liveness_ordinary_yield_seconds",
                DEFAULT_BURST_LIVENESS_ORDINARY_YIELD_SECONDS,
                90,
            )
            if duration > 0:
                self._burst_liveness_yield_deadline_monotonic = now + duration
                return
        elif now < deadline:
            return
        self._burst_liveness_yield_ordinary_pending = False
        self._burst_liveness_yield_deadline_monotonic = 0.0

    def _finish_weather_liveness_window(
        self,
        *,
        reason,
        resource_sample,
        yield_to_ordinary=True,
    ):
        window = self._weather_liveness_window
        if window is None:
            return
        now = self._clock()
        cooldown_seconds = self._weather_liveness_seconds(
            "weather_liveness_cooldown_seconds",
            DEFAULT_WEATHER_LIVENESS_COOLDOWN_SECONDS,
            60 * 60,
        )
        self._weather_liveness_window = None
        self._weather_liveness_cooldown_until_monotonic = now + cooldown_seconds
        if yield_to_ordinary:
            self._request_burst_liveness_ordinary_yield()
        handoff = (
            "ordinary background data gets the next bounded admission turn"
            if yield_to_ordinary
            else "runnable auxiliary background work may proceed"
        )
        logger.warning(
            "Weather quiet window ended; %s. | reason: %s | instance_uuid_hash: %s | "
            "window_seconds: %.1f | cooldown_seconds: %.1f | available_mb: %s | "
            "swap_percent: %s",
            handoff,
            reason,
            hashlib.sha256(window.instance_uuid.encode("utf-8")).hexdigest()[:16],
            max(0.0, window.deadline_monotonic - window.started_monotonic),
            cooldown_seconds,
            getattr(resource_sample, "available_mb", None),
            getattr(resource_sample, "swap_percent", None),
        )

    def _submit_independent_refresh_command(self, command):
        if command.payload.get("weather_liveness_concession") is not True:
            return self.refresh_queue.submit(command)

        entry = self.refresh_queue.submit_entry(command)
        submitted = entry.job
        window = self._weather_liveness_window
        retained = bool(
            entry.job.status is JobStatus.QUEUED
            and window is not None
            and entry.command.instance_uuid == command.instance_uuid
            and entry.command.instance_uuid == window.instance_uuid
            and entry.command.structural_generation
            == command.structural_generation
            and entry.command.settings_revision == command.settings_revision
            and self._is_weather_background_data_command(entry.command)
            and entry.command.payload.get("weather_liveness_concession") is True
        )
        if retained:
            resource_sample = self._resource_sample()
            _margin_available, concession_min_mb = self._weather_concession_margin(
                resource_sample
            )
            logger.warning(
                "Weather quiet window reached its bounded concession. | "
                "instance_uuid_hash: %s | available_mb: %s | swap_percent: %s | "
                "required_available_mb: %s",
                hashlib.sha256(
                    entry.command.instance_uuid.encode("utf-8")
                ).hexdigest()[:16],
                resource_sample.available_mb,
                resource_sample.swap_percent,
                concession_min_mb,
            )
            self._finish_weather_liveness_window(
                reason="concession_submitted",
                resource_sample=resource_sample,
            )
        else:
            logger.error(
                "Weather concession submission did not retain its admission; "
                "keeping the liveness window open. | instance_uuid_hash: %s",
                hashlib.sha256(
                    str(command.instance_uuid).encode("utf-8")
                ).hexdigest()[:16],
            )
        return submitted

    def _weather_liveness_decision(
        self,
        active,
        data_candidates,
        runtime_instances,
        current_dt,
        resource_sample,
    ):
        """Reserve one bounded quiet window for a due Weather browser start."""

        now = self._clock()
        candidates_by_uuid = {
            candidate.instance.instance_uuid: candidate
            for candidate in data_candidates
            if candidate.instance.plugin_id == "weather"
        }
        active_weather = {
            instance.instance_uuid: instance
            for instance in active.plugins
            if instance.plugin_id == "weather"
        }
        normal_margin, required_mb, max_swap = (
            self._weather_background_start_margin(resource_sample)
        )
        concession_margin, _ = self._weather_concession_margin(
            resource_sample
        )
        window = self._weather_liveness_window
        if window is not None:
            active_instance = active_weather.get(window.instance_uuid)
            original_instance = window.candidate.instance
            identity_current = bool(
                active_instance is not None
                and active_instance.structural_generation
                == original_instance.structural_generation
                and active_instance.settings_revision
                == original_instance.settings_revision
            )
            runtime = runtime_instances.get(
                window.instance_uuid,
                InstanceRuntimeState(),
            ).data
            last_success = self._parse_iso_datetime(runtime.last_success_at)
            if last_success is not None:
                last_success = self._align_datetime_tz(last_success, current_dt)
            if not identity_current or (
                last_success is not None and last_success >= window.due_since
            ):
                self._finish_weather_liveness_window(
                    reason=("target_changed" if not identity_current else "completed"),
                    resource_sample=resource_sample,
                )
                return None, False, False

            target = candidates_by_uuid.get(window.instance_uuid)
            next_retry = self._parse_iso_datetime(runtime.next_retry_at)
            retry_pending = False
            if next_retry is not None:
                next_retry = self._align_datetime_tz(next_retry, current_dt)
                retry_pending = current_dt < next_retry
            if target is None and not retry_pending:
                self._finish_weather_liveness_window(
                    reason="no_longer_due",
                    resource_sample=resource_sample,
                )
                return None, False, False

            if now >= window.deadline_monotonic:
                if not concession_margin:
                    self._finish_weather_liveness_window(
                        reason="margin_unavailable",
                        resource_sample=resource_sample,
                    )
                    return None, False, False
                # The retry gate can hide a candidate created by this window's
                # own pressure deferral. Rebuild it with the currently active,
                # identity-checked snapshot before issuing the single concession.
                target = target or replace(
                    window.candidate,
                    instance=active_instance,
                )
                return target, False, True
            if normal_margin:
                if target is None and retry_pending:
                    target = replace(
                        window.candidate,
                        instance=active_instance,
                    )
                return target, False, False
            return None, True, False

        weather_candidates = sorted(
            candidates_by_uuid.values(),
            key=lambda candidate: (
                self._align_datetime_tz(candidate.due_since, current_dt),
                candidate.instance.instance_uuid,
            ),
        )
        target = weather_candidates[0] if weather_candidates else None
        if target is None:
            return None, False, False
        if normal_margin:
            # With no quiet window, Weather participates in the ordinary DATA
            # ordering. The liveness path must not grant an unnecessary
            # priority boost merely because its start margin is healthy.
            return None, False, False
        if now < self._weather_liveness_cooldown_until_monotonic:
            return None, False, False
        # A quiet window is useful only when a bounded start could eventually
        # be safe. Unknown metrics or less than 140 MiB never hold other work.
        if not concession_margin:
            return None, False, False
        window_seconds = self._weather_liveness_seconds(
            "weather_liveness_window_seconds",
            DEFAULT_WEATHER_LIVENESS_WINDOW_SECONDS,
            90,
        )
        if window_seconds <= 0:
            return None, False, False
        self._run_memory_maintenance("weather-liveness-window", force=True)
        due_since = self._align_datetime_tz(target.due_since, current_dt)
        self._weather_liveness_window = _WeatherLivenessWindow(
            instance_uuid=target.instance.instance_uuid,
            due_since=due_since,
            started_monotonic=now,
            deadline_monotonic=now + window_seconds,
            candidate=target,
        )
        logger.warning(
            "Reserving bounded quiet window for due Weather data. | "
            "instance_uuid_hash: %s | overdue_seconds: %.1f | window_seconds: %.1f | "
            "available_mb: %s | swap_percent: %s | required_available_mb: %s | "
            "max_swap_percent: %s",
            hashlib.sha256(
                target.instance.instance_uuid.encode("utf-8")
            ).hexdigest()[:16],
            max(0.0, (current_dt - due_since).total_seconds()),
            window_seconds,
            resource_sample.available_mb,
            resource_sample.swap_percent,
            required_mb,
            max_swap,
        )
        return None, True, False

    @staticmethod
    def _is_ticketmaster_background_data_command(command):
        return (
            command.plugin_id == "ticketmaster_events"
            and command.kind is CommandKind.CACHE_REFRESH
            and command.source is CommandSource.BACKGROUND
            and command.intent is RefreshIntent.DATA_REFRESH
        )

    def _ticketmaster_liveness_seconds(self, key, default, maximum):
        value = self._config_float(key, default)
        if not math.isfinite(value) or value < 0:
            value = float(default)
        return min(float(maximum), value)

    def _ticketmaster_liveness_target(self, data_candidates, current_dt):
        starvation_seconds = self._ticketmaster_liveness_seconds(
            "ticketmaster_liveness_starvation_seconds",
            DEFAULT_TICKETMASTER_LIVENESS_STARVATION_SECONDS,
            7 * 24 * 60 * 60,
        )
        eligible = []
        for candidate in data_candidates:
            if candidate.instance.plugin_id != "ticketmaster_events":
                continue
            instance_uuid = candidate.instance.instance_uuid
            if candidate.reason is DueReason.BOOTSTRAP_MISSING:
                observed_since = self._align_datetime_tz(
                    candidate.last_attempt_at or candidate.due_since,
                    current_dt,
                )
                due_since = self._ticketmaster_bootstrap_due_since.get(
                    instance_uuid
                )
                if due_since is None or observed_since < due_since:
                    due_since = observed_since
                    self._ticketmaster_bootstrap_due_since[instance_uuid] = (
                        due_since
                    )
            elif candidate.reason in {DueReason.INTERVAL, DueReason.SCHEDULED}:
                self._ticketmaster_bootstrap_due_since.pop(instance_uuid, None)
                due_since = self._align_datetime_tz(
                    candidate.due_since,
                    current_dt,
                )
            else:
                continue
            if (current_dt - due_since).total_seconds() < starvation_seconds:
                continue
            eligible.append(
                (due_since, instance_uuid, candidate)
            )
        if not eligible:
            return None, None
        due_since, _instance_uuid, candidate = min(
            eligible,
            key=lambda item: item[:2],
        )
        return candidate, due_since

    def _ticketmaster_liveness_decision(
        self,
        active,
        data_candidates,
        runtime_instances,
        current_dt,
        resource_sample,
    ):
        """Reserve bounded idle time for persistently stale Ticketmaster data."""

        now = self._clock()
        candidates_by_uuid = {
            candidate.instance.instance_uuid: candidate
            for candidate in data_candidates
            if candidate.instance.plugin_id == "ticketmaster_events"
        }
        active_ticketmaster_uuids = frozenset(
            instance.instance_uuid
            for instance in active.plugins
            if instance.plugin_id == "ticketmaster_events"
        )
        for instance_uuid, due_since in tuple(
            self._ticketmaster_bootstrap_due_since.items()
        ):
            if instance_uuid not in active_ticketmaster_uuids:
                self._ticketmaster_bootstrap_due_since.pop(instance_uuid, None)
                continue
            runtime = runtime_instances.get(
                instance_uuid,
                InstanceRuntimeState(),
            ).data
            last_success = self._parse_iso_datetime(runtime.last_success_at)
            if last_success is None:
                continue
            last_success = self._align_datetime_tz(last_success, current_dt)
            if last_success >= due_since:
                self._ticketmaster_bootstrap_due_since.pop(instance_uuid, None)
        margin_available, required_mb, max_swap = (
            self._ticketmaster_background_start_margin(resource_sample)
        )
        window = self._ticketmaster_liveness_window
        if (
            window is not None
            and window.instance_uuid not in active_ticketmaster_uuids
        ):
            logger.info(
                "Canceling Ticketmaster quiet window because its target left "
                "the active playlist. | instance_uuid_hash: %s",
                hashlib.sha256(
                    window.instance_uuid.encode("utf-8")
                ).hexdigest()[:16],
            )
            self._ticketmaster_liveness_window = None
            return None, False

        if window is not None:
            target = candidates_by_uuid.get(window.instance_uuid)
            retry_pending = False
            if target is None:
                runtime = runtime_instances.get(
                    window.instance_uuid,
                    InstanceRuntimeState(),
                ).data
                next_retry = self._parse_iso_datetime(runtime.next_retry_at)
                if next_retry is not None:
                    next_retry = self._align_datetime_tz(next_retry, current_dt)
                    retry_pending = current_dt < next_retry
                if not retry_pending:
                    logger.info(
                        "Canceling Ticketmaster quiet window because its target "
                        "is no longer due. | instance_uuid_hash: %s",
                        hashlib.sha256(
                            window.instance_uuid.encode("utf-8")
                        ).hexdigest()[:16],
                    )
                    self._ticketmaster_liveness_window = None
                    self._ticketmaster_bootstrap_due_since.pop(
                        window.instance_uuid,
                        None,
                    )
                    return None, False

        if window is not None:
            target = candidates_by_uuid.get(window.instance_uuid)
            if now >= window.deadline_monotonic:
                cooldown_seconds = self._ticketmaster_liveness_seconds(
                    "ticketmaster_liveness_cooldown_seconds",
                    DEFAULT_TICKETMASTER_LIVENESS_COOLDOWN_SECONDS,
                    60 * 60,
                )
                self._ticketmaster_liveness_window = None
                self._ticketmaster_liveness_cooldown_until_monotonic = (
                    now + cooldown_seconds
                )
                logger.warning(
                    "Ticketmaster quiet window expired before a refresh "
                    "completed; ordinary refreshes resume. | "
                    "instance_uuid_hash: %s | window_seconds: %.1f | "
                    "cooldown_seconds: %.1f | available_mb: %s | "
                    "swap_percent: %s",
                    hashlib.sha256(
                        window.instance_uuid.encode("utf-8")
                    ).hexdigest()[:16],
                    max(
                        0.0,
                        window.deadline_monotonic - window.started_monotonic,
                    ),
                    cooldown_seconds,
                    resource_sample.available_mb,
                    resource_sample.swap_percent,
                )
                return None, False
            if target is not None and margin_available:
                return target, False
            return None, True

        target, due_since = self._ticketmaster_liveness_target(
            data_candidates,
            current_dt,
        )
        if target is not None and margin_available:
            return target, False
        if (
            not margin_available
            and now < self._ticketmaster_liveness_cooldown_until_monotonic
        ):
            return None, False
        if target is None:
            return None, False

        window_seconds = self._ticketmaster_liveness_seconds(
            "ticketmaster_liveness_window_seconds",
            DEFAULT_TICKETMASTER_LIVENESS_WINDOW_SECONDS,
            5 * 60,
        )
        if window_seconds <= 0:
            return None, False

        # This is intentionally one bounded maintenance pass. The next
        # scheduler poll re-samples resources while ordinary background work is
        # held, and execution still applies the same 115 MiB / swap gate.
        self._run_memory_maintenance(
            "ticketmaster-liveness-window",
            force=True,
        )
        self._ticketmaster_liveness_window = _TicketmasterLivenessWindow(
            instance_uuid=target.instance.instance_uuid,
            due_since=due_since,
            started_monotonic=now,
            deadline_monotonic=now + window_seconds,
        )
        logger.warning(
            "Reserving bounded quiet window for starved Ticketmaster data. | "
            "instance_uuid_hash: %s | overdue_seconds: %.1f | "
            "window_seconds: %.1f | available_mb: %s | swap_percent: %s | "
            "required_available_mb: %s | max_swap_percent: %s",
            hashlib.sha256(
                target.instance.instance_uuid.encode("utf-8")
            ).hexdigest()[:16],
            max(0.0, (current_dt - due_since).total_seconds()),
            window_seconds,
            resource_sample.available_mb,
            resource_sample.swap_percent,
            required_mb,
            max_swap,
        )
        return None, True

    def _ticketmaster_background_start_margin(self, sample):
        min_available_mb = self._config_float(
            "ticketmaster_background_start_min_available_mb",
            DEFAULT_TICKETMASTER_BACKGROUND_START_MIN_AVAILABLE_MB,
        )
        if not math.isfinite(min_available_mb) or min_available_mb < 0:
            min_available_mb = (
                DEFAULT_TICKETMASTER_BACKGROUND_START_MIN_AVAILABLE_MB
            )
        max_swap_percent = self._resource_thresholds().hard_max_swap_percent
        if (
            not math.isfinite(max_swap_percent)
            or max_swap_percent < 0
            or max_swap_percent > 100
        ):
            max_swap_percent = DEFAULT_MEMORY_WATCHDOG_MAX_SWAP_PERCENT
        try:
            available_mb = float(sample.available_mb)
            swap_percent = float(sample.swap_percent)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False, min_available_mb, max_swap_percent
        return (
            math.isfinite(available_mb)
            and math.isfinite(swap_percent)
            and available_mb >= min_available_mb
            and swap_percent < max_swap_percent,
            min_available_mb,
            max_swap_percent,
        )

    @staticmethod
    def _is_isolated_sports_refresh_command(command):
        return (
            command.plugin_id == "sports_dashboard"
            and command.kind is CommandKind.CACHE_REFRESH
            and command.intent
            in {
                RefreshIntent.DATA_REFRESH,
                RefreshIntent.LIVE_REFRESH,
            }
            and bool(command.payload.get("playlist_name"))
        )

    def _sports_liveness_seconds(self, key, default, maximum):
        value = self._config_float(key, default)
        if not math.isfinite(value) or value < 0:
            value = float(default)
        return min(float(maximum), value)

    def _sports_liveness_anchor(
        self,
        candidate,
        runtime_instances,
        current_dt,
    ):
        due_since = self._align_datetime_tz(candidate.due_since, current_dt)
        runtime = runtime_instances.get(
            candidate.instance.instance_uuid,
            InstanceRuntimeState(),
        ).data
        if self._parse_iso_datetime(runtime.last_success_at) is not None:
            return due_since
        if candidate.last_attempt_at is None:
            return None
        last_attempt = self._align_datetime_tz(
            candidate.last_attempt_at,
            current_dt,
        )
        return min(due_since, last_attempt)

    def _sports_liveness_target(
        self,
        data_candidates,
        runtime_instances,
        current_dt,
    ):
        starvation_seconds = self._sports_liveness_seconds(
            "sports_isolated_liveness_starvation_seconds",
            DEFAULT_SPORTS_ISOLATED_LIVENESS_STARVATION_SECONDS,
            7 * 24 * 60 * 60,
        )
        eligible = []
        for candidate in data_candidates:
            if candidate.instance.plugin_id != "sports_dashboard":
                continue
            due_since = self._sports_liveness_anchor(
                candidate,
                runtime_instances,
                current_dt,
            )
            if due_since is None:
                continue
            if (current_dt - due_since).total_seconds() < starvation_seconds:
                continue
            eligible.append((due_since, candidate.instance.instance_uuid, candidate))
        if not eligible:
            return None, None
        due_since, _instance_uuid, candidate = min(
            eligible,
            key=lambda item: item[:2],
        )
        return candidate, due_since

    def _sports_liveness_decision(
        self,
        active,
        data_candidates,
        runtime_instances,
        current_dt,
        resource_sample,
    ):
        """Reserve a bounded idle window for a persistently overdue Sports render."""
        now = self._clock()
        candidates_by_uuid = {
            candidate.instance.instance_uuid: candidate
            for candidate in data_candidates
            if candidate.instance.plugin_id == "sports_dashboard"
        }
        active_sports_uuids = frozenset(
            instance.instance_uuid
            for instance in active.plugins
            if instance.plugin_id == "sports_dashboard"
        )
        unproven_attempted_uuids = frozenset(
            instance_uuid
            for instance_uuid in active_sports_uuids
            if self._parse_iso_datetime(
                runtime_instances.get(
                    instance_uuid,
                    InstanceRuntimeState(),
                ).data.last_attempt_at
            )
            is not None
            and self._parse_iso_datetime(
                runtime_instances.get(
                    instance_uuid,
                    InstanceRuntimeState(),
                ).data.last_success_at
            )
            is None
        )
        margin_available, required_mb, max_swap = (
            self._sports_isolated_start_margin(resource_sample)
        )
        window = self._sports_liveness_window
        if window is not None and window.instance_uuid not in active_sports_uuids:
            logger.info(
                "Canceling Sports Dashboard quiet window because its target "
                "left the active playlist. | instance_uuid_hash: %s",
                hashlib.sha256(
                    window.instance_uuid.encode("utf-8")
                ).hexdigest()[:16],
            )
            self._sports_liveness_window = None
            return None, False, active_sports_uuids

        if window is not None:
            target = candidates_by_uuid.get(window.instance_uuid)
            retry_pending = False
            if target is None:
                runtime = runtime_instances.get(
                    window.instance_uuid,
                    InstanceRuntimeState(),
                ).data
                next_retry = self._parse_iso_datetime(runtime.next_retry_at)
                if next_retry is not None:
                    next_retry = self._align_datetime_tz(next_retry, current_dt)
                    retry_pending = current_dt < next_retry
                if not retry_pending:
                    logger.info(
                        "Canceling Sports Dashboard quiet window because its "
                        "target is no longer due. | instance_uuid_hash: %s",
                        hashlib.sha256(
                            window.instance_uuid.encode("utf-8")
                        ).hexdigest()[:16],
                    )
                    self._sports_liveness_window = None
                    return None, False, active_sports_uuids

        if window is not None:
            target = candidates_by_uuid.get(window.instance_uuid)
            if now >= window.deadline_monotonic:
                cooldown_seconds = self._sports_liveness_seconds(
                    "sports_isolated_liveness_cooldown_seconds",
                    DEFAULT_SPORTS_ISOLATED_LIVENESS_COOLDOWN_SECONDS,
                    60 * 60,
                )
                self._sports_liveness_window = None
                self._sports_liveness_cooldown_until_monotonic = (
                    now + cooldown_seconds
                )
                logger.warning(
                    "Sports Dashboard quiet window expired before a refresh "
                    "completed; ordinary refreshes resume. | "
                    "instance_uuid_hash: %s | window_seconds: %.1f | "
                    "cooldown_seconds: %.1f | available_mb: %s | "
                    "swap_percent: %s",
                    hashlib.sha256(
                        window.instance_uuid.encode("utf-8")
                    ).hexdigest()[:16],
                    max(
                        0.0,
                        window.deadline_monotonic
                        - window.started_monotonic,
                    ),
                    cooldown_seconds,
                    resource_sample.available_mb,
                    resource_sample.swap_percent,
                )
                return None, False, active_sports_uuids
            if target is not None and margin_available:
                # Keep the window until execution has actually completed.
                # The child-process gate samples resources again; retaining the
                # original deadline makes a scheduler/execution margin race
                # expire into cooldown instead of silently starting over.
                return target, False, frozenset()
            return None, True, active_sports_uuids

        target, due_since = self._sports_liveness_target(
            data_candidates,
            runtime_instances,
            current_dt,
        )
        if target is not None and margin_available:
            return target, False, frozenset()
        if (
            not margin_available
            and now < self._sports_liveness_cooldown_until_monotonic
        ):
            return None, False, active_sports_uuids
        if target is None:
            return (
                None,
                False,
                (
                    unproven_attempted_uuids
                    if not margin_available
                    else frozenset()
                ),
            )

        window_seconds = self._sports_liveness_seconds(
            "sports_isolated_liveness_window_seconds",
            DEFAULT_SPORTS_ISOLATED_LIVENESS_WINDOW_SECONDS,
            5 * 60,
        )
        if window_seconds <= 0:
            return None, False, active_sports_uuids
        self._sports_liveness_window = _SportsLivenessWindow(
            instance_uuid=target.instance.instance_uuid,
            due_since=due_since,
            started_monotonic=now,
            deadline_monotonic=now + window_seconds,
        )
        logger.warning(
            "Reserving bounded quiet window for starved Sports Dashboard. | "
            "instance_uuid_hash: %s | overdue_seconds: %.1f | "
            "window_seconds: %.1f | available_mb: %s | swap_percent: %s | "
            "required_available_mb: %s | max_swap_percent: %s",
            hashlib.sha256(
                target.instance.instance_uuid.encode("utf-8")
            ).hexdigest()[:16],
            max(0.0, (current_dt - due_since).total_seconds()),
            window_seconds,
            resource_sample.available_mb,
            resource_sample.swap_percent,
            required_mb,
            max_swap,
        )
        return None, True, active_sports_uuids

    def _sports_isolated_start_margin(self, sample):
        min_available_mb = self._config_float(
            "sports_isolated_start_min_available_mb",
            DEFAULT_SPORTS_ISOLATED_START_MIN_AVAILABLE_MB,
        )
        if not math.isfinite(min_available_mb) or min_available_mb < 0:
            min_available_mb = DEFAULT_SPORTS_ISOLATED_START_MIN_AVAILABLE_MB
        max_swap_percent = self._config_float(
            "sports_isolated_start_max_swap_percent",
            DEFAULT_SPORTS_ISOLATED_START_MAX_SWAP_PERCENT,
        )
        if (
            not math.isfinite(max_swap_percent)
            or max_swap_percent < 0
            or max_swap_percent > 100
        ):
            max_swap_percent = DEFAULT_SPORTS_ISOLATED_START_MAX_SWAP_PERCENT
        try:
            available_mb = float(sample.available_mb)
            swap_percent = float(sample.swap_percent)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False, min_available_mb, max_swap_percent
        return (
            math.isfinite(available_mb)
            and math.isfinite(swap_percent)
            and available_mb >= min_available_mb
            and swap_percent < max_swap_percent,
            min_available_mb,
            max_swap_percent,
        )

    def _sports_isolated_abort_thresholds(self):
        min_available_mb = self._config_float(
            "sports_isolated_abort_min_available_mb",
            DEFAULT_SPORTS_ISOLATED_ABORT_MIN_AVAILABLE_MB,
        )
        if not math.isfinite(min_available_mb) or min_available_mb < 0:
            min_available_mb = DEFAULT_SPORTS_ISOLATED_ABORT_MIN_AVAILABLE_MB
        max_swap_percent = self._config_float(
            "sports_isolated_abort_max_swap_percent",
            DEFAULT_SPORTS_ISOLATED_ABORT_MAX_SWAP_PERCENT,
        )
        if (
            not math.isfinite(max_swap_percent)
            or max_swap_percent < 0
            or max_swap_percent > 100
        ):
            max_swap_percent = DEFAULT_SPORTS_ISOLATED_ABORT_MAX_SWAP_PERCENT
        return min_available_mb, max_swap_percent

    def _isolated_instance_identity_is_current(self, command, identity):
        expected = InstanceIdentity(
            command.instance_uuid,
            command.structural_generation,
            command.settings_revision,
        )
        return (
            identity == expected
            and self._resolve_playlist_command(command) is not None
        )

    @staticmethod
    def _lane_for_intent(intent):
        return {
            RefreshIntent.DATA_REFRESH: RefreshLane.DATA,
            RefreshIntent.PRESENTATION_REFRESH: RefreshLane.PRESENTATION,
            RefreshIntent.LIVE_REFRESH: RefreshLane.LIVE,
            RefreshIntent.THEME_REDRAW: RefreshLane.THEME,
        }.get(intent)

    @staticmethod
    def _lane_retry_key(instance_uuid, lane):
        return f"{instance_uuid}:{lane.value}"

    @staticmethod
    def _rotation_display_retry_key(instance_uuid):
        return f"{instance_uuid}:rotation-display"

    def _record_intent_success(
        self,
        command,
        instance,
        current_dt,
        theme_mode,
    ):
        lane = self._lane_for_intent(command.intent)
        if lane is None or command.instance_uuid is None:
            return
        promoted_at = current_dt.isoformat()
        last_good = LastGoodCacheState(
            theme_mode=theme_mode,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=promoted_at,
        )
        self.runtime_state.record_success(
            command.instance_uuid,
            promoted_at,
            lane=lane,
            last_good_cache=last_good,
        )
        self.retry_registry.mark_success(
            self._lane_retry_key(command.instance_uuid, lane)
        )

    def _record_intent_failure(self, command, error, current_dt):
        lane = self._lane_for_intent(command.intent)
        if lane is None or command.instance_uuid is None:
            return None
        retry_key = self._lane_retry_key(command.instance_uuid, lane)
        delay = self.retry_registry.mark_failure(retry_key, self._clock())
        self.runtime_state.record_failure(
            command.instance_uuid,
            current_dt.isoformat(),
            error,
            (current_dt + timedelta(seconds=delay)).isoformat(),
            lane=lane,
        )
        return delay

    def _record_degraded_data_result(self, command, provenance, current_dt):
        provenance_value = (
            provenance.value
            if provenance is not None
            else "non_cacheable_result"
        )
        error = RuntimeError(
            f"DATA source is display-safe but unhealthy: {provenance_value}"
        )
        self.scheduler_state.record_failure(error)
        self._record_intent_failure(command, error, current_dt)
        self.scheduler_state.set_next_attempt(
            self._clock() + self._scheduler_poll_seconds()
        )

    def _record_runtime_success(self, instance_uuid, succeeded_at):
        try:
            self.runtime_state.record_success(instance_uuid, succeeded_at)
        except Exception:
            logger.exception(
                "Runtime refresh success state could not be recorded. | instance_uuid: %s",
                instance_uuid,
            )

    def _record_runtime_failure(self, command, error, retry_delay):
        if command.instance_uuid is None:
            return
        try:
            self.runtime_state.record_failure(
                command.instance_uuid,
                self._runtime_now_iso(),
                error,
                self._runtime_now_iso(offset_seconds=retry_delay),
            )
        except Exception:
            logger.exception(
                "Runtime refresh failure state could not be recorded. | instance_uuid: %s",
                command.instance_uuid,
            )

    def _record_runtime_display_state(
        self,
        state,
        *,
        commit_id=None,
        instance_uuid=None,
        changed_at=None,
    ):
        try:
            self.runtime_state.set_display_state(
                state,
                commit_id,
                instance_uuid=instance_uuid,
                changed_at=changed_at,
            )
        except Exception:
            logger.exception(
                "Runtime display state could not be recorded. | state: %s",
                state,
            )

    def _display_image(
        self,
        image,
        *,
        context,
        image_settings=(),
        logical_target=None,
        instance_revision=None,
        force_hardware_write=False,
    ):
        if self._display_transactions_enabled:
            display_kwargs = {
                "image_settings": image_settings,
                "task_context": context,
                "logical_target": logical_target,
                "instance_revision": instance_revision,
            }
            if force_hardware_write:
                display_kwargs["force_hardware_write"] = True
            return self.display_manager.display_image(image, **display_kwargs)
        return self.display_manager.display_image(
            image,
            image_settings=image_settings,
        )

    def make_cleanup_context(self, timeout_seconds=30.0):
        """Return a bounded public context for cleanup under the shared arbiter."""
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError, OverflowError):
            timeout = 30.0
        timeout = max(0.01, min(210.0, timeout))
        return TaskContext(self.stop_event, self._clock() + timeout, self._clock)

    def _execute_command(self, command: RefreshCommand):
        self._execution_local.effective_theme_context = None
        context = self._current_task_context(command)
        context.raise_if_cancelled()
        if command.instance_uuid is not None:
            resolved = self._resolve_playlist_command(command)
            if resolved is None:
                raise _StaleSelection("playlist selection is stale")
            if command.intent is RefreshIntent.DISPLAY_CACHE:
                if (
                    command.source is CommandSource.LIVE
                    and not _live_display_refresh_enabled(
                        self.device_config,
                        command.plugin_id,
                        command.payload.get("settings"),
                    )
                ):
                    raise _StaleSelection(
                        "live display follow-up is disabled"
                    )
                plugin_config = self.device_config.get_plugin(command.plugin_id)
                if (
                    plugin_supports_cached_display_redraw(plugin_config)
                    and not (
                        command.allow_prepared_presentation
                        and plugin_supports_presentation_refresh(plugin_config)
                    )
                ):
                    image = self._redraw_cached_display(command, resolved, context, plugin_config)
                    prepared_selection = None
                else:
                    image, prepared_selection = self._load_catalog_display_image(
                        command,
                        resolved,
                    )
                self._set_render_metadata(
                    False,
                    False,
                    plugin_config,
                )
                try:
                    return self._commit_command_result(
                        command,
                        resolved,
                        image,
                        self._get_current_datetime(),
                        prepared_selection=prepared_selection,
                    )
                except (
                    TaskDeadlineExceeded,
                    _CacheUnavailable,
                    _StaleSelection,
                    TaskCancelled,
                ):
                    raise
                except Exception as error:
                    if prepared_selection is None:
                        raise
                    raise _PreparedDisplayFailure(error) from error
            if command.intent is RefreshIntent.PRESENTATION_REFRESH:
                plugin_config = self.device_config.get_plugin(command.plugin_id)
                if not _presentation_refresh_enabled(
                    self.device_config,
                    plugin_config,
                ):
                    raise _StaleSelection(
                        "display-triggered presentation refresh is disabled"
                    )
                return self._render_presentation_command(
                    command,
                    resolved,
                    context,
                )
            if (
                command.intent is RefreshIntent.LIVE_REFRESH
                and command.payload.get("background_live_refresh") is not True
                and not _live_display_refresh_enabled(
                    self.device_config,
                    command.plugin_id,
                    command.payload.get("settings"),
                )
            ):
                raise _StaleSelection(
                    "display-triggered live refresh is disabled"
                )
            image = self._render_playlist_command(command, resolved, context)
            # Cache promotion is plugin-owned work too. Reacquiring the same
            # canonical lease closes the render->commit gap against deletion
            # cleanup without holding it across unrelated queue bookkeeping.
            with self.render_arbiter.lease(command.plugin_id, context):
                return self._commit_command_result(
                    command,
                    resolved,
                    image,
                    self._get_current_datetime(),
                )

        plugin_config = self.device_config.get_plugin(command.plugin_id)
        if plugin_config is None:
            raise LookupError(f"Plugin config not found for '{command.plugin_id}'.")
        plugin = get_plugin_instance(plugin_config)
        settings = thaw_payload(command.payload.get("settings", {}))
        with self.render_arbiter.lease(command.plugin_id, context):
            context.raise_if_cancelled()
            image = plugin.render_themed_image(
                _settings_with_force_refresh(
                    settings,
                    command.force,
                    display_render=command.kind is CommandKind.DISPLAY,
                ),
                self.device_config,
                resolved_theme_context=command.payload.get(
                    "resolved_theme_context"
                ),
            )
            context.raise_if_cancelled()
        self._capture_effective_theme_context(command, image)
        self._set_render_metadata(True, False, getattr(plugin, "config", plugin_config))
        return self._commit_command_result(command, None, image, self._get_current_datetime())

    def _redraw_cached_display(self, command, resolved, context, plugin_config):
        """Re-evaluate explicitly opted-in local data just before a display write."""
        with self.render_arbiter.lease(command.plugin_id, context):
            self._require_fresh_selection(command, context)
            _config, _theme_context, current_theme = self._latest_presentation_theme(resolved.instance)
            if current_theme != _resolved_theme_mode(command.payload):
                raise _StaleSelection("local cached display theme changed")
            sample = self._resource_sample()
            if classify_resource_tier(sample, self._resource_thresholds()) is ResourceTier.HARD:
                # A frozen status image is unsafe here; leave this display turn
                # for another instance instead of falling back to old values.
                raise _CacheUnavailable("local cached display redraw deferred under hard pressure")
            started = self._clock()
            plugin = get_plugin_instance(plugin_config)
            image = plugin.render_cached_display(
                thaw_payload(resolved.instance.settings),
                self.device_config,
                resolved_theme_context=thaw_payload(command.payload.get("resolved_theme_context")),
            )
            try:
                self._require_fresh_selection(command, context)
                _config, _theme_context, current_theme = self._latest_presentation_theme(resolved.instance)
                if current_theme != _resolved_theme_mode(command.payload):
                    raise _StaleSelection("local cached display theme changed during redraw")
                if image is None or image.info.get("inkypi_theme_mode") != current_theme:
                    raise _CacheUnavailable("local cached display redraw returned an invalid theme")
            except BaseException:
                if image is not None:
                    image.close()
                raise
            logger.info(
                "Local cached display redrawn. | plugin_id: %s | elapsed_seconds: %.3f | provenance: %s",
                command.plugin_id, max(0.0, self._clock() - started),
                getattr(read_source_provenance(image), "value", "none"),
            )
            return image

    def _load_catalog_display_image(self, command, resolved):
        """Load prepared or authoritative bytes without plugin execution."""
        instance = None if resolved is None else resolved.instance
        if command.allow_prepared_presentation and instance is not None:
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            expected_request_id = command.payload.get("presentation_request_id")
            if not _presentation_refresh_enabled(
                self.device_config,
                plugin_config,
            ):
                if expected_request_id is not None:
                    raise _StaleSelection(
                        "presentation refresh policy is no longer enabled"
                    )
            elif not plugin_supports_presentation_refresh(plugin_config):
                if expected_request_id is not None:
                    raise _StaleSelection("presentation capability is no longer enabled")
            else:
                (
                    plugin_config,
                    _theme_context,
                    resolved_theme_mode,
                ) = self._latest_presentation_theme(instance)
                if not _presentation_refresh_enabled(
                    self.device_config,
                    plugin_config,
                ):
                    if expected_request_id is not None:
                        raise _StaleSelection(
                            "presentation refresh policy is no longer enabled"
                        )
                    return self._load_catalog_display_image(
                        replace(command, allow_prepared_presentation=False),
                        resolved,
                    )
                elif not plugin_supports_presentation_refresh(plugin_config):
                    if expected_request_id is not None:
                        raise _StaleSelection(
                            "presentation capability is no longer enabled"
                        )
                    return self._load_catalog_display_image(
                        replace(command, allow_prepared_presentation=False),
                        resolved,
                    )
                state = self.runtime_state.snapshot().instances.get(
                    instance.instance_uuid,
                    InstanceRuntimeState(),
                )
                request = state.presentation_request
                if expected_request_id is not None and (request is None or request.request_id != expected_request_id):
                    raise _StaleSelection("presentation display request was replaced")
                if (
                    request is not None
                    and request.structural_generation == instance.structural_generation
                    and request.settings_revision == instance.settings_revision
                    and request.prepared_at is not None
                    and request.prepared_theme_mode == resolved_theme_mode
                ):
                    candidate = self._presentation_candidate(
                        instance,
                        request,
                        resolved_theme_mode,
                    )
                    image = self.presentation_cache.load_image(candidate)
                    if image is None:
                        if not self._invalidate_prepared_display(command, instance, request, candidate):
                            raise _StaleSelection("prepared presentation changed during validation")
                        raise _CacheUnavailable("prepared presentation cache is missing or corrupt")
                    return image, _PreparedDisplaySelection(
                        candidate=candidate,
                        request=request,
                        theme_mode=resolved_theme_mode,
                    )
                if expected_request_id is not None:
                    raise _StaleSelection("exact prepared presentation is no longer displayable")

        theme_mode = command.payload.get("cache_theme_mode")
        cache_instance = instance if instance is not None else command
        runtime_instance = self.runtime_state.snapshot().instances.get(
            command.instance_uuid,
            InstanceRuntimeState(),
        )
        candidate = self.cache_catalog.resolve_exact(
            cache_instance,
            theme_mode,
            runtime_instance,
        )
        if candidate is None:
            raise _CacheUnavailable("display cache is unavailable or superseded")
        image = self.cache_catalog.load_image(candidate)
        if image is None:
            self.cache_catalog.invalidate(candidate)
            raise _CacheUnavailable("display cache is unavailable")
        if resolved is None:
            return image
        return image, None

    def _invalidate_prepared_display(self, command, instance, request, candidate):
        """Cool only the exact invalid request; preserve canonical bytes and receipts."""
        now = self._get_current_datetime()
        if not self.runtime_state.clear_prepared_presentation(
            instance.instance_uuid, request.request_id, now.isoformat(),
        ):
            return False
        self._record_presentation_failure(
            command, RuntimeError("prepared presentation cache is missing, expired or corrupt"), now,
        )
        self.presentation_cache.remove(candidate)
        return True

    def _latest_presentation_theme(self, instance):
        plugin_config = self.device_config.get_plugin(instance.plugin_id)
        resolved_theme_context = _resolved_theme_context_for_instance(
            instance,
            plugin_config,
            self.device_config,
            current_dt=self._get_current_datetime(),
        )
        resolved_theme_mode = (
            resolved_theme_context.get("mode") if isinstance(resolved_theme_context, Mapping) else None
        )
        return plugin_config, resolved_theme_context, resolved_theme_mode

    def _presentation_candidate(self, instance, request, theme_mode):
        return PreparedPresentationCandidate(
            instance_uuid=instance.instance_uuid,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            theme_mode=theme_mode,
            request_id=request.request_id,
            cache_path=prepared_presentation_path(
                self.presentation_cache.cache_root,
                instance.instance_uuid,
                instance.structural_generation,
                instance.settings_revision,
                theme_mode,
                request.request_id,
            ),
        )

    def _record_presentation_failure(self, command, error, current_dt):
        presentation_command = replace(
            command,
            intent=RefreshIntent.PRESENTATION_REFRESH,
        )
        self.scheduler_state.record_failure(error)
        self._record_intent_failure(
            presentation_command,
            error,
            current_dt,
        )
        self._release_failed_rotation_reservation(
            presentation_command
        )
        self.scheduler_state.set_next_attempt(self._clock() + self._scheduler_poll_seconds())

    def _record_pending_presentation_deadline_failure(
        self,
        command,
        error,
        current_dt,
    ):
        """Back off the exact request unless it is prepared for the current theme."""
        # Presentation mutation is owned by this single RefreshTask consumer,
        # so no prepared/replaced publication can interleave between this
        # final state check and failure bookkeeping.  If that ownership ever
        # becomes concurrent, this boundary must move into RuntimeStateStore
        # as one request-id/revision/prepared-at CAS.
        if (
            command.intent is not RefreshIntent.PRESENTATION_REFRESH
            or command.instance_uuid is None
        ):
            return False
        selection = self._resolve_playlist_command(command)
        if selection is None:
            return False
        if command.payload.get("automatic_rotation") is True and not (
            self.device_config.get_playlist_manager().validate_rotation_reservation(
                command.instance_uuid,
                expected_playlist_name=selection.playlist_name,
            )
        ):
            return False
        expected_request_id = command.payload.get("presentation_request_id")
        state = self.runtime_state.snapshot().instances.get(
            command.instance_uuid,
            InstanceRuntimeState(),
        )
        request = state.presentation_request
        if (
            request is None
            or request.request_id != expected_request_id
            or request.structural_generation != command.structural_generation
            or request.settings_revision != command.settings_revision
        ):
            return False
        if request.prepared_at is not None:
            _config, _context, theme_mode = self._latest_presentation_theme(
                selection.instance
            )
            if request.prepared_theme_mode == theme_mode:
                return False
        self._record_presentation_failure(command, error, current_dt)
        return True

    def _record_pending_rotation_deadline_failure(
        self,
        command,
        error,
        current_dt,
    ):
        """Back off the exact pending work without mutating newer ownership."""
        if command.intent is RefreshIntent.PRESENTATION_REFRESH:
            return self._record_pending_presentation_deadline_failure(
                command,
                error,
                current_dt,
            )
        if (
            command.intent
            not in {RefreshIntent.DATA_REFRESH, RefreshIntent.DISPLAY_CACHE}
            or command.instance_uuid is None
            or command.payload.get("automatic_rotation") is not True
            or (
                command.intent is RefreshIntent.DATA_REFRESH
                and (
                    command.source
                    not in {CommandSource.BACKGROUND, CommandSource.MANUAL}
                    or command.kind is not CommandKind.CACHE_REFRESH
                )
            )
            or (
                command.intent is RefreshIntent.DISPLAY_CACHE
                and (
                    command.source is not CommandSource.SCHEDULER
                    or command.kind is not CommandKind.DISPLAY
                )
            )
        ):
            return False
        # Queue coalescing can promote automatic DATA_REFRESH to MANUAL while
        # retaining the automatic reservation payload.  It still owns cleanup.
        selection = self._resolve_playlist_command(command)
        if selection is None or not (
            self.device_config.get_playlist_manager().validate_rotation_reservation(
                command.instance_uuid,
                expected_playlist_name=selection.playlist_name,
            )
        ):
            return False

        if command.intent is RefreshIntent.DISPLAY_CACHE:
            self.scheduler_state.record_failure(error)
            self.retry_registry.mark_failure(
                self._rotation_display_retry_key(command.instance_uuid),
                self._clock(),
            )
        else:
            terminal_outcome = self._data_attempt_terminal_outcome(
                command,
                current_dt,
            )
            if terminal_outcome is not None:
                return False
            self.scheduler_state.record_failure(error)
            self._record_intent_failure(command, error, current_dt)
        self._release_failed_rotation_reservation(command)
        self.scheduler_state.set_next_attempt(
            self._clock() + self._scheduler_poll_seconds()
        )
        return True

    def _data_attempt_terminal_outcome(self, command, current_dt):
        """Return a terminal result already recorded for this exact data attempt."""
        state = self.runtime_state.snapshot().instances.get(
            command.instance_uuid,
            InstanceRuntimeState(),
        ).data
        attempted_at = self._parse_iso_datetime(state.last_attempt_at)
        if attempted_at is None:
            return None
        attempted_at = self._align_datetime_tz(attempted_at, current_dt)
        succeeded_at = self._parse_iso_datetime(state.last_success_at)
        failed_at = self._parse_iso_datetime(state.last_failure_at)
        if succeeded_at is not None:
            succeeded_at = self._align_datetime_tz(succeeded_at, current_dt)
        if failed_at is not None:
            failed_at = self._align_datetime_tz(failed_at, current_dt)
        if succeeded_at is not None and succeeded_at >= attempted_at and (
            failed_at is None or succeeded_at >= failed_at
        ):
            return "success"
        if failed_at is not None and failed_at >= attempted_at:
            return "failure"
        return None

    def _record_rotation_deadline_failure_safely(self, command, error):
        try:
            return self._record_pending_rotation_deadline_failure(
                command,
                error,
                self._get_current_datetime(),
            )
        except Exception:
            logger.exception(
                "Automatic rotation deadline failure bookkeeping also failed"
            )
            self._defer_scheduler_after_bookkeeping_error()
            return False

    def _release_failed_rotation_reservation(self, command):
        if not command.payload.get("automatic_rotation"):
            return False
        playlist_name = command.payload.get("playlist_name")
        if command.instance_uuid is None or not playlist_name:
            return False
        released = (
            self.device_config.get_playlist_manager().release_rotation_reservation(
                command.instance_uuid,
                expected_playlist_name=playlist_name,
            )
        )
        if released:
            logger.warning(
                "Failed automatic rotation command released its reservation during retry backoff. | plugin_id: %s | instance_uuid: %s",
                command.plugin_id,
                command.instance_uuid,
            )
        return released

    def _render_presentation_command(self, command, resolved, context):
        started = self._clock()
        before = self._read_process_memory_stats()
        state = self.runtime_state.snapshot().instances.get(command.instance_uuid, InstanceRuntimeState())
        request = state.presentation_request
        requested_at = self._parse_iso_datetime(request.requested_at) if request else None
        current_dt = self._get_current_datetime()
        if requested_at is not None:
            requested_at = self._align_datetime_tz(requested_at, current_dt)
        waiting = None if requested_at is None else max(
            0.0, (current_dt - requested_at).total_seconds(),
        )
        outcome = "failed"
        try:
            result = self._render_presentation_command_impl(command, resolved, context)
            outcome = "completed"
            return result
        finally:
            after = self._read_process_memory_stats()
            logger.info(
                "Presentation preparation measured. | plugin_id: %s | request_id: %s | "
                "outcome: %s | elapsed_seconds: %.3f | request_age_seconds: %s | "
                "rss_before_mb: %s | rss_after_mb: %s | process_hwm_mb: %s",
                command.plugin_id, command.payload.get("presentation_request_id"), outcome,
                max(0.0, self._clock() - started), waiting,
                before.get("rss_mb"), after.get("rss_mb"), after.get("hwm_mb"),
            )

    def _render_presentation_command_impl(self, command, resolved, context):
        """Prepare presentation bytes on the shared worker."""
        selection = self._require_fresh_selection(command, context)
        instance = selection.instance
        state = self.runtime_state.snapshot().instances.get(
            instance.instance_uuid,
            InstanceRuntimeState(),
        )
        request = state.presentation_request
        expected_request_id = command.payload.get("presentation_request_id")
        if (
            request is None
            or request.request_id != expected_request_id
            or request.structural_generation != instance.structural_generation
            or request.settings_revision != instance.settings_revision
        ):
            raise _StaleSelection("presentation request changed before prepare")

        plugin_config, resolved_theme_context, theme_mode = self._latest_presentation_theme(instance)
        if not _presentation_refresh_enabled(
            self.device_config,
            plugin_config,
        ):
            raise _StaleSelection(
                "presentation refresh policy is no longer enabled"
            )
        if not plugin_supports_presentation_refresh(plugin_config):
            raise _StaleSelection("presentation capability is no longer enabled")
        plugin = get_plugin_instance(plugin_config)
        settings = bind_presentation_instance_identity(
            thaw_payload(instance.settings),
            instance.instance_uuid,
        )
        with self.render_arbiter.lease(command.plugin_id, context):
            context.raise_if_cancelled()
            mode = PresentationMode(plugin.presentation_mode(settings))
            if mode is PresentationMode.LEGACY_ASYNC:
                raise RuntimeError("legacy async presentation refresh is disabled")
            if mode is PresentationMode.NO_CHANGE:
                if not self.runtime_state.satisfy_presentation_no_change(
                    instance.instance_uuid,
                    request.request_id,
                    request.requested_at,
                ):
                    raise _StaleSelection("presentation request changed before no-change commit")
                self.retry_registry.mark_success(
                    self._lane_retry_key(
                        instance.instance_uuid,
                        RefreshLane.PRESENTATION,
                    )
                )
                return None
            origin_receipt = PresentationCommitReceipt(
                request_id=request.request_id,
                committed_at=request.requested_at,
                display_commit_id=request.origin_display_commit_id,
                structural_generation=request.structural_generation,
                settings_revision=request.settings_revision,
                theme_mode=request.origin_theme_mode,
            )
            plugin.reconcile_presentation_receipt(
                settings,
                origin_receipt,
            )
            prior_receipt = state.presentation_receipt
            if prior_receipt is not None:
                plugin.reconcile_presentation_receipt(
                    settings,
                    prior_receipt,
                )
            request_context = PresentationRequestContext(
                request_id=request.request_id,
                requested_at=request.requested_at,
                origin_display_commit_id=request.origin_display_commit_id,
                last_receipt=prior_receipt,
            )
            preparation = plugin.prepare_presentation(
                settings,
                self.device_config,
                request=request_context,
                resolved_theme_context=(
                    thaw_payload(resolved_theme_context) if resolved_theme_context is not None else None
                ),
            )
            if not isinstance(preparation, PresentationPreparation):
                raise TypeError("prepare_presentation must return PresentationPreparation")
            if preparation.request_id != request.request_id:
                raise ValueError("presentation preparation returned a different request id")
            context.raise_if_cancelled()

        if not preparation.changed:
            if not self.runtime_state.satisfy_presentation_no_change(
                instance.instance_uuid,
                request.request_id,
                request.requested_at,
            ):
                raise _StaleSelection("presentation request changed before no-change commit")
            self.retry_registry.mark_success(
                self._lane_retry_key(
                    instance.instance_uuid,
                    RefreshLane.PRESENTATION,
                )
            )
            return None

        candidate = self._presentation_candidate(
            instance,
            request,
            theme_mode,
        )
        protected_request_ids = {request.request_id}
        if prior_receipt is not None:
            protected_request_ids.add(prior_receipt.request_id)
        self.presentation_cache.save(
            candidate,
            preparation.image,
            protected_request_ids=protected_request_ids,
        )
        try:
            self._require_fresh_selection(command, context)
            if not self._presentation_request_is_current(
                instance,
                request.request_id,
                theme_mode=theme_mode,
                require_prepared=False,
            ):
                raise _StaleSelection("presentation request changed before prepared publication")
            prepared_at = self._get_current_datetime().isoformat()
            if not self.runtime_state.mark_presentation_prepared(
                instance.instance_uuid,
                request.request_id,
                prepared_at,
                theme_mode,
            ):
                raise _StaleSelection("presentation request changed before prepared publication")
            source_provenance = read_source_provenance(preparation.image)
            if (
                plugin_allows_display_triggered_provider_refresh(plugin_config)
                and source_provenance
                in {
                    SourceProvenance.LIVE,
                    SourceProvenance.FRESH_CACHE,
                }
            ):
                try:
                    # The provider-backed preparation already completed the
                    # fresh fetch. Count it for data cadence without promoting
                    # last-good until the physical display transaction commits.
                    self.runtime_state.record_success(
                        instance.instance_uuid,
                        prepared_at,
                        lane=RefreshLane.DATA,
                    )
                    self.retry_registry.mark_success(
                        self._lane_retry_key(
                            instance.instance_uuid,
                            RefreshLane.DATA,
                        )
                    )
                except Exception:
                    logger.exception(
                        "Provider presentation could not satisfy data freshness. | "
                        "plugin_id: %s | instance_uuid: %s",
                        instance.plugin_id,
                        instance.instance_uuid,
                    )
        except BaseException:
            self.presentation_cache.remove(candidate)
            raise

        self._enqueue_presentation_display_followup(
            command,
            selection,
            request,
            theme_mode,
        )
        return preparation.image

    def _presentation_request_is_current(
        self,
        instance,
        request_id,
        *,
        theme_mode,
        require_prepared,
    ):
        state = self.runtime_state.snapshot().instances.get(
            instance.instance_uuid,
            InstanceRuntimeState(),
        )
        request = state.presentation_request
        if (
            request is None
            or request.request_id != request_id
            or request.structural_generation != instance.structural_generation
            or request.settings_revision != instance.settings_revision
        ):
            return False
        if require_prepared and (request.prepared_at is None or request.prepared_theme_mode != theme_mode):
            return False
        _plugin_config, _theme_context, current_theme_mode = self._latest_presentation_theme(instance)
        return current_theme_mode == theme_mode

    def _enqueue_presentation_display_followup(
        self,
        command,
        resolved_snapshot,
        request,
        theme_mode,
    ):
        if not _display_triggered_refresh_enabled(self.device_config):
            return None
        snapshot = self.runtime_state.snapshot()
        instance = resolved_snapshot.instance
        if snapshot.displayed_instance_uuid != instance.instance_uuid:
            return None
        followup = self._playlist_command(
            resolved_snapshot.playlist_name,
            instance,
            source=CommandSource.BACKGROUND,
            intent=RefreshIntent.DISPLAY_CACHE,
            force=False,
            display_cached_only=True,
            priority=65,
            kind=CommandKind.DISPLAY,
            current_dt=self._get_current_datetime(),
            cache_theme_mode=theme_mode,
            expected_displayed_instance_uuid=instance.instance_uuid,
            preserve_rotation_anchor=True,
            coalescing_scope=f"presentation-followup:{request.request_id}",
            allow_prepared_presentation=True,
            presentation_request_id=request.request_id,
        )
        return self.refresh_queue.submit(followup)

    def _render_playlist_command(self, command, resolved, context):
        instance = resolved.instance
        plugin_config = self.device_config.get_plugin(command.plugin_id)
        if plugin_config is None:
            raise LookupError(f"Plugin config not found for '{command.plugin_id}'.")
        settings = thaw_payload(instance.settings)
        isolated_sports_refresh = self._is_isolated_sports_refresh_command(command)
        plugin = (
            None
            if isolated_sports_refresh
            else get_plugin_instance(plugin_config)
        )
        if plugin_supports_presentation_refresh(plugin_config):
            settings = bind_presentation_instance_identity(
                settings,
                instance.instance_uuid,
            )
        current_dt = self._get_current_datetime()
        resolved_theme_context = command.payload.get("resolved_theme_context")
        theme_mode = _resolved_theme_mode(command.payload)
        cache_path = self._snapshot_cache_path(instance, theme_mode)
        image_missing = not os.path.exists(cache_path)
        display_cached_only = bool(command.payload.get("display_cached_only", True))
        theme_render_only = bool(command.payload.get("theme_render_only", False))
        theme_cache_ready = not image_missing
        if theme_render_only:
            runtime_instance = self.runtime_state.snapshot().instances.get(
                instance.instance_uuid,
                InstanceRuntimeState(),
            )
            theme_cache_ready = (
                self.cache_catalog.resolve_exact(
                    instance,
                    theme_mode,
                    runtime_instance,
                )
                is not None
            )
        generated = False
        cacheable = False

        with self.render_arbiter.lease(command.plugin_id, context):
            context.raise_if_cancelled()
            if (
                command.intent is RefreshIntent.DATA_REFRESH
                and plugin is not None
                and plugin_supports_presentation_refresh(plugin_config)
                and PresentationMode(plugin.presentation_mode(settings)) is PresentationMode.PREPARED_BANK
            ):
                receipt = (
                    self.runtime_state.snapshot()
                    .instances.get(
                        instance.instance_uuid,
                        InstanceRuntimeState(),
                    )
                    .presentation_receipt
                )
                if receipt is not None:
                    plugin.reconcile_presentation_receipt(settings, receipt)
            display_under_pressure = (
                command.kind is CommandKind.DISPLAY
                and display_cached_only
                and not command.force
                and _display_refresh_under_resource_pressure(self.device_config)
            )
            if display_under_pressure:
                if theme_render_only and not theme_cache_ready:
                    self._set_render_metadata(
                        False,
                        False,
                        plugin_config,
                        theme_only=True,
                    )
                    return None
                image = self._load_snapshot_cache_or_placeholder(instance, cache_path)
            else:
                if (
                    command.kind is CommandKind.CACHE_REFRESH
                    and command.intent not in {
                        RefreshIntent.DATA_REFRESH,
                        RefreshIntent.LIVE_REFRESH,
                        RefreshIntent.THEME_REDRAW,
                    }
                    and self._cache_refresh_under_resource_pressure()
                ):
                    self._set_render_metadata(False, False, plugin_config)
                    if command.intent is RefreshIntent.THEME_CATCHUP:
                        raise _CacheUnavailable(
                            "theme catch-up deferred under resource pressure"
                        )
                    return None

                if isolated_sports_refresh:
                    (
                        _start_margin_available,
                        start_min_available_mb,
                        start_max_swap_percent,
                    ) = self._sports_isolated_start_margin(
                        self._resource_sample()
                    )
                    abort_min_available_mb, abort_max_swap_percent = (
                        self._sports_isolated_abort_thresholds()
                    )
                    image = self._sports_isolated_renderer(
                        settings=_settings_with_force_refresh(
                            settings,
                            command.force,
                            display_render=False,
                        ),
                        device_config=self.device_config,
                        resolved_theme_context=resolved_theme_context,
                        context=context,
                        instance_identity=InstanceIdentity(
                            command.instance_uuid,
                            command.structural_generation,
                            command.settings_revision,
                        ),
                        identity_validator=(
                            lambda identity: self._isolated_instance_identity_is_current(
                                command,
                                identity,
                            )
                        ),
                        resource_sampler=self._resource_sample,
                        start_min_available_mb=start_min_available_mb,
                        start_max_swap_percent=start_max_swap_percent,
                        abort_min_available_mb=abort_min_available_mb,
                        abort_max_swap_percent=abort_max_swap_percent,
                        now=current_dt,
                        attempt_token=(command.id if command.force else None),
                    )
                    generated = True
                elif theme_render_only and theme_cache_ready:
                    image = _load_image_copy(cache_path)
                elif theme_render_only:
                    image = self._render_theme_only_image(
                        plugin,
                        plugin_config,
                        instance,
                        settings,
                        resolved_theme_context,
                    )
                    generated = True
                else:
                    refresh_on_display = False
                    refresh_hook = getattr(plugin, "wants_refresh_on_display", None)
                    if callable(refresh_hook):
                        try:
                            refresh_on_display = bool(refresh_hook(settings))
                        except PluginSettingError:
                            raise
                        except Exception:
                            logger.exception(
                                "Plugin '%s' refresh-on-display hook failed.",
                                command.plugin_id,
                            )
                    live_state = None
                    if plugin_supports_live_refresh(plugin_config):
                        live_state = _plugin_live_refresh_state(
                            plugin,
                            settings,
                            current_dt,
                            plugin_id=command.plugin_id,
                        )
                    live_due = self._snapshot_live_state_due(instance, live_state, current_dt)
                    refresh_due = self._snapshot_should_refresh(instance, current_dt)
                    sports_due = command.plugin_id == "sports_dashboard" and refresh_due
                    reusable_theme_cache = bool(
                        theme_mode
                        and any(
                            os.path.exists(path)
                            for path in self._theme_cache_reuse_paths(
                                instance,
                                theme_mode,
                            )
                        )
                    )
                    lazy_theme_render = (
                        command.kind is CommandKind.DISPLAY
                        and not command.force
                        and image_missing
                        and reusable_theme_cache
                        and not refresh_on_display
                        and not live_due
                        and not refresh_due
                    )
                    should_generate = (
                        command.force
                        or image_missing
                        or refresh_on_display
                        or live_due
                        or sports_due
                        or command.kind is CommandKind.CACHE_REFRESH
                    )
                    if lazy_theme_render:
                        image = self._render_theme_only_image(
                            plugin,
                            plugin_config,
                            instance,
                            settings,
                            resolved_theme_context,
                        )
                        theme_render_only = True
                        generated = True

                if isolated_sports_refresh:
                    pass
                elif not theme_render_only and display_cached_only and not should_generate:
                    try:
                        image = _load_image_copy(cache_path)
                    except Exception:
                        logger.exception(
                            "Cached plugin image could not be loaded; refreshing synchronously. | "
                            "plugin_instance: '%s'",
                            instance.name,
                        )
                        try:
                            image = plugin.render_themed_image(
                                _settings_with_force_refresh(
                                    settings,
                                    command.force,
                                    display_render=True,
                                ),
                                self.device_config,
                                resolved_theme_context=resolved_theme_context,
                            )
                            generated = True
                        except ResourcePressureDeferred:
                            raise
                        except Exception:
                            logger.exception(
                                "Plugin instance could not refresh for scheduled display; using placeholder. | "
                                "plugin_instance: '%s'",
                                instance.name,
                            )
                            image = self._placeholder_for_snapshot(instance)
                elif not theme_render_only and should_generate:
                    image = plugin.render_themed_image(
                        _settings_with_force_refresh(
                            settings,
                            command.force,
                            display_render=command.kind is CommandKind.DISPLAY,
                        ),
                        self.device_config,
                        resolved_theme_context=resolved_theme_context,
                    )
                    generated = True
                elif not theme_render_only:
                    image = _load_image_copy(cache_path)
                cacheable = generated and _image_allows_cache(image)
                if (
                    command.kind is CommandKind.DISPLAY
                    and generated
                    and not cacheable
                    and os.path.exists(cache_path)
                ):
                    try:
                        image = _load_image_copy(cache_path)
                    except Exception:
                        logger.exception(
                            "Previous cached plugin image could not be loaded after a "
                            "non-cacheable refresh; displaying the generated image. | "
                            "plugin_instance: '%s'",
                            instance.name,
                        )
            context.raise_if_cancelled()

        self._set_render_metadata(
            generated,
            cacheable,
            getattr(plugin, "config", plugin_config),
            theme_only=theme_render_only,
        )
        self._capture_effective_theme_context(command, image)
        return image

    def _render_theme_only_image(
        self,
        plugin,
        plugin_config,
        instance,
        settings,
        resolved_theme_context,
    ):
        manifest = plugin_config.get("_manifest") if plugin_config else None
        manifest_theme = getattr(manifest, "theme", None)
        presentation = getattr(manifest_theme, "presentation", None)
        theme_mode = (
            resolved_theme_context.get("mode")
            if isinstance(resolved_theme_context, Mapping)
            else None
        )
        if presentation == "media" and theme_mode in {"day", "night"}:
            for source_path in self._theme_cache_reuse_paths(instance, theme_mode):
                if not os.path.exists(source_path):
                    continue
                try:
                    source = _load_image_copy(source_path)
                except Exception:
                    logger.exception(
                        "Reusable media theme cache could not be loaded. | "
                        "plugin_instance: '%s' | cache_path: %s",
                        instance.name,
                        source_path,
                    )
                    continue
                image = apply_media_theme_chrome(
                    source,
                    instance.plugin_id,
                    thaw_payload(resolved_theme_context),
                    resolve_dimensions(self.device_config),
                )
                image.info["inkypi_theme_mode"] = theme_mode
                return image
        return plugin.render_themed_image(
            _settings_with_force_refresh(
                settings,
                False,
                display_render=True,
            ),
            self.device_config,
            theme_render_only=True,
            resolved_theme_context=resolved_theme_context,
        )

    def _set_render_metadata(
        self,
        generated,
        cacheable,
        plugin_config,
        *,
        theme_only=False,
    ):
        self._execution_local.render_generated = bool(generated)
        self._execution_local.render_cacheable = bool(cacheable)
        self._execution_local.render_theme_only = bool(theme_only)
        self._execution_local.degraded_data_result = False
        self._execution_local.image_settings = list((plugin_config or {}).get("image_settings", []))

    def _capture_effective_theme_context(self, command, image):
        """Consume Weather's render-local context without leaking PNG metadata."""
        info = getattr(image, "info", None)
        if not isinstance(info, dict):
            return None
        effective = info.pop(EFFECTIVE_THEME_CONTEXT_INFO_KEY, None)
        if effective is None:
            return getattr(self._execution_local, "effective_theme_context", None)

        queued = command.payload.get("resolved_theme_context")
        queued_mode = _resolved_theme_mode(command.payload)
        queued_requested_mode = (
            queued.get("requested_mode") if isinstance(queued, Mapping) else None
        )
        allowed = (
            command.plugin_id == "weather"
            and command.intent
            in {RefreshIntent.DATA_REFRESH, RefreshIntent.MANUAL_RENDER}
            and command.payload.get("theme_render_only") is not True
            and is_valid_effective_theme_context(effective)
            and effective.get("requested_mode") == queued_requested_mode
            and info.get("inkypi_theme_mode") == effective.get("mode")
        )
        if not allowed:
            if queued_mode in {"day", "night"}:
                info["inkypi_theme_mode"] = queued_mode
            return None

        context = thaw_payload(effective)
        self._execution_local.effective_theme_context = context
        return context

    def _effective_theme_context(self):
        context = getattr(self._execution_local, "effective_theme_context", None)
        return context if isinstance(context, Mapping) else None

    def _snapshot_live_state_due(self, instance, state, current_dt):
        if not state:
            return False
        latest = self._snapshot_latest_refresh_dt(instance)
        if latest is None:
            return True
        latest = self._align_datetime_tz(latest, current_dt)
        return (current_dt - latest) >= timedelta(seconds=state["interval_seconds"])

    def _load_snapshot_cache_or_placeholder(self, instance, cache_path):
        if os.path.exists(cache_path):
            try:
                return _load_image_copy(cache_path)
            except Exception:
                logger.exception(
                    "Cached plugin image could not be loaded under resource pressure; using placeholder. | "
                    "plugin_instance: '%s'",
                    instance.name,
                )
        logger.warning(
            "Plugin instance image unavailable for scheduled display under resource pressure; using placeholder. | "
            "plugin_instance: '%s'",
            instance.name,
        )
        return self._placeholder_for_snapshot(instance)

    def _placeholder_for_snapshot(self, instance):
        return PlaylistRefresh(None, instance)._placeholder_image(self.device_config)

    def _resolve_playlist_command(self, command: RefreshCommand):
        playlist_name = command.payload.get("playlist_name")
        if not playlist_name:
            return None
        if not self._live_display_target_is_current(command):
            return None
        return self.device_config.get_playlist_manager().validate_selection(
            command.instance_uuid,
            expected_playlist_name=playlist_name,
            expected_generation=command.structural_generation,
            expected_settings_revision=command.settings_revision,
            current_datetime=self._get_current_datetime(),
            require_active=bool(command.payload.get("require_active", True)),
        )

    def _require_fresh_selection(self, command, context):
        context.raise_if_cancelled()
        if not self._live_display_target_is_current(command):
            raise _StaleSelection("live display target changed")
        selection = self.device_config.get_playlist_manager().validate_selection(
            command.instance_uuid,
            expected_playlist_name=command.payload.get("playlist_name"),
            expected_generation=command.structural_generation,
            expected_settings_revision=command.settings_revision,
            current_datetime=self._get_current_datetime(),
            require_active=bool(command.payload.get("require_active", True)),
        )
        if selection is None:
            raise _StaleSelection("playlist selection changed before commit")
        if command.payload.get("automatic_rotation") is True and not (
            self.device_config.get_playlist_manager().validate_rotation_reservation(
                command.instance_uuid,
                expected_playlist_name=command.payload.get("playlist_name"),
            )
        ):
            raise _StaleSelection("automatic display reservation changed before commit")
        return selection

    def _live_display_target_is_current(self, command):
        if command.payload.get("background_live_refresh") is True:
            return (
                command.source is CommandSource.LIVE
                and command.intent is RefreshIntent.LIVE_REFRESH
                and command.kind is CommandKind.CACHE_REFRESH
                and command.plugin_id == "sports_dashboard"
                and command.payload.get("expected_displayed_instance_uuid") is None
            )
        expected_displayed_uuid = command.payload.get(
            "expected_displayed_instance_uuid"
        )
        if expected_displayed_uuid is not None:
            if expected_displayed_uuid != command.instance_uuid:
                return False
            displayed_uuid = self.runtime_state.snapshot().displayed_instance_uuid
            return displayed_uuid == expected_displayed_uuid
        if command.source is not CommandSource.LIVE:
            return True
        displayed_uuid = self.runtime_state.snapshot().displayed_instance_uuid
        if displayed_uuid is not None:
            return displayed_uuid == command.instance_uuid
        latest_refresh = self.device_config.get_refresh_info()
        return (
            latest_refresh.refresh_type == "Playlist"
            and latest_refresh.playlist == command.payload.get("playlist_name")
            and latest_refresh.plugin_id == command.plugin_id
            and latest_refresh.plugin_instance == command.payload.get("instance_name")
        )

    def _staging_cache_path(self, instance, theme_mode=None):
        directory = os.path.join(self.device_config.plugin_image_dir, ".refresh-staging")
        filename = self._cache_identity_filename(
            instance.instance_uuid,
            instance.structural_generation,
            instance.settings_revision,
            theme_mode,
        )
        return os.path.join(directory, filename)

    def managed_cache_paths(self, instance_uuid, *, plugin_id=None, instance_name=None):
        """Return UUID-owned versioned cache paths for bounded cleanup.

        ``plugin_id`` and ``instance_name`` remain accepted for callers from the
        transition release, but name-based compatibility files are deliberately
        excluded: a replacement instance may own that shared alias.
        """
        paths = []
        prefix = f"{self._cache_identity_prefix(instance_uuid)}-"
        for directory_name in (".refresh-staging", ".refresh-cache"):
            directory = os.path.join(self.device_config.plugin_image_dir, directory_name)
            try:
                paths.extend(
                    os.path.join(directory, name)
                    for name in os.listdir(directory)
                    if name.startswith(prefix)
                )
            except FileNotFoundError:
                pass
        return tuple(sorted(set(paths)))

    def _commit_command_result(
        self,
        command,
        resolved_snapshot,
        image,
        current_dt,
        *,
        prepared_selection=None,
    ):
        context = self._current_task_context(command)
        context.raise_if_cancelled()
        self._capture_effective_theme_context(command, image)
        if prepared_selection is not None:
            return self._commit_prepared_display_result(
                command,
                resolved_snapshot,
                image,
                current_dt,
                prepared_selection,
            )
        if resolved_snapshot is not None:
            if image is None:
                return None
            instance = resolved_snapshot.instance
            generated = bool(getattr(self._execution_local, "render_generated", False))
            cacheable = bool(getattr(self._execution_local, "render_cacheable", False))
            source_provenance = read_source_provenance(image)
            degraded_data_result = (
                command.intent is RefreshIntent.DATA_REFRESH
                and generated
                and source_provenance
                in {
                    SourceProvenance.STALE_CACHE,
                    SourceProvenance.LOCAL_FALLBACK,
                }
            )
            theme_only = bool(
                getattr(self._execution_local, "render_theme_only", False)
            )
            effective_theme_context = self._effective_theme_context()
            theme_mode = (
                effective_theme_context.get("mode")
                if effective_theme_context is not None
                else _resolved_theme_mode(command.payload)
            )
            stage_path = None
            promoted_for_intent = False
            if generated and cacheable:
                stage_path = self._staging_cache_path(instance, theme_mode)
                _save_image_atomic(image, stage_path)
                try:
                    self._require_fresh_selection(command, context)
                    canonical_path = self._snapshot_cache_path(instance, theme_mode)
                    os.makedirs(os.path.dirname(canonical_path), exist_ok=True)
                    os.replace(stage_path, canonical_path)
                    stage_path = None
                finally:
                    if stage_path and os.path.exists(stage_path):
                        try:
                            os.remove(stage_path)
                        except OSError:
                            logger.warning("Could not remove stale staged cache: %s", stage_path)
                self._require_fresh_selection(command, context)
                promoted_for_intent = True
            elif (
                command.intent
                in {
                    RefreshIntent.THEME_REDRAW,
                    RefreshIntent.THEME_CATCHUP,
                }
                and self._exact_cache_is_valid(instance, theme_mode)
            ):
                promoted_for_intent = True

            if (
                command.intent is RefreshIntent.LIVE_REFRESH
                and command.plugin_id == "sports_dashboard"
            ):
                logger.info(
                    "Sports dashboard live cache decision. | generated: %s | "
                    "cacheable: %s | provenance: %s | promoted: %s | theme: %s",
                    generated,
                    cacheable,
                    source_provenance.value if source_provenance is not None else "none",
                    promoted_for_intent,
                    theme_mode or "none",
                )

            if (
                command.intent is RefreshIntent.THEME_CATCHUP
                and not promoted_for_intent
            ):
                raise RuntimeError(
                    "theme catch-up did not produce an exact cacheable image"
                )

            if degraded_data_result:
                self._execution_local.degraded_data_result = True
                self._record_degraded_data_result(
                    command,
                    source_provenance,
                    current_dt,
                )
            elif (
                command.intent is RefreshIntent.DATA_REFRESH
                and generated
                and not promoted_for_intent
            ):
                self._execution_local.degraded_data_result = True
                self._record_degraded_data_result(
                    command,
                    source_provenance,
                    current_dt,
                )
            elif promoted_for_intent:
                self._record_intent_success(
                    command,
                    instance,
                    current_dt,
                    theme_mode,
                )
                if (
                    command.plugin_id == "weather"
                    and command.intent is RefreshIntent.DATA_REFRESH
                    and effective_theme_context is not None
                    and effective_theme_context.get("requested_mode") == "auto"
                    and self._update_active_theme_info(
                        effective_theme_context,
                        current_dt,
                    )
                    and command.kind is not CommandKind.DISPLAY
                ):
                    self._write_device_config()
                if command.intent is RefreshIntent.LIVE_REFRESH:
                    self._enqueue_live_display_followup(
                        command,
                        resolved_snapshot,
                        current_dt,
                        theme_mode,
                    )
                elif command.intent is RefreshIntent.THEME_REDRAW:
                    self._enqueue_theme_display_followup(
                        command,
                        resolved_snapshot,
                        current_dt,
                        theme_mode,
                    )

            image_hash = compute_image_hash(image)
            latest_refresh = self.device_config.get_refresh_info()
            refresh_info = {
                "refresh_type": "Playlist",
                "playlist": resolved_snapshot.playlist_name,
                "plugin_id": instance.plugin_id,
                "plugin_instance": instance.name,
                "refresh_time": current_dt.isoformat(),
                "image_hash": image_hash,
            }
            if (
                (
                    command.source is CommandSource.LIVE
                    or command.payload.get("theme_render_only") is True
                    or command.payload.get("preserve_rotation_anchor") is True
                )
                and latest_refresh.refresh_time
                and not self._display_target_changed(latest_refresh, refresh_info)
            ):
                # RefreshInfo.refresh_time is the playlist rotation anchor.
                # Same-target live updates have their own instance success and
                # display-manifest timestamps, so they must not move it.
                refresh_info["refresh_time"] = latest_refresh.refresh_time
            refresh_record = RefreshInfo(**refresh_info)
            theme_context = command.payload.get("theme_context")
            thawed_theme_context = thaw_payload(theme_context) if theme_context else None
            display_commit = None
            display_was_invoked = False
            if command.kind is CommandKind.DISPLAY:
                self._require_fresh_selection(command, context)
                if (
                    self._force_hardware_write_requested(command)
                    or image_hash != latest_refresh.image_hash
                    or self._display_target_changed(latest_refresh, refresh_info)
                ):
                    display_was_invoked = True
                    display_commit = self._display_image(
                        image,
                        context=context,
                        image_settings=getattr(self._execution_local, "image_settings", ()),
                        logical_target={
                            "kind": "playlist",
                            "playlist": resolved_snapshot.playlist_name,
                            "plugin_id": instance.plugin_id,
                            "plugin_instance": instance.name,
                            "instance_uuid": instance.instance_uuid,
                        },
                        instance_revision=(
                            instance.structural_generation,
                            instance.settings_revision,
                        ),
                        force_hardware_write=self._force_hardware_write_requested(
                            command
                        ),
                    )

            if command.kind is CommandKind.DISPLAY:
                self._require_fresh_selection(command, context)
                if theme_context:
                    self._require_fresh_selection(command, context)

            if command.kind is CommandKind.DISPLAY:
                self._require_fresh_selection(command, context)
                # The final validation is the config commit linearization point.
                # Do not observe cancellation again after shared state is mutated.
                self._require_automatic_hardware_write(
                    command,
                    display_commit,
                    display_was_invoked=display_was_invoked,
                )
                commit_id, committed_at = self._display_commit_evidence(
                    display_commit,
                    instance.instance_uuid,
                    current_dt,
                    display_was_invoked=display_was_invoked,
                )
                self.device_config.refresh_info = refresh_record
                if thawed_theme_context:
                    self._persist_active_theme(thawed_theme_context, current_dt)
                self._write_playlist_display_commit(command)
                if command.payload.get("automatic_rotation") is True:
                    self._request_next_presentation_after_display(
                        current_dt,
                        commit_id,
                        committed_at,
                        displayed_instance_uuid=instance.instance_uuid,
                    )
                elif command.allow_prepared_presentation:
                    self._request_presentation_after_display(
                        instance,
                        commit_id,
                        committed_at,
                    )
            return image

        image_hash = compute_image_hash(image)
        latest_refresh = self.device_config.get_refresh_info()
        refresh_info = {
            "refresh_type": str(command.payload.get("refresh_type") or "Manual Update"),
            "plugin_id": command.plugin_id,
            "refresh_time": current_dt.isoformat(),
            "image_hash": image_hash,
        }
        refresh_record = RefreshInfo(**refresh_info)
        if image_hash != latest_refresh.image_hash or self._display_target_changed(latest_refresh, refresh_info):
            context.raise_if_cancelled()
            self._display_image(
                image,
                context=context,
                image_settings=getattr(self._execution_local, "image_settings", ()),
                logical_target={
                    "kind": "manual",
                    "plugin_id": command.plugin_id,
                    "refresh_type": refresh_info["refresh_type"],
                },
            )
        context.raise_if_cancelled()
        context.raise_if_cancelled()
        # This is the manual config commit linearization point. Once crossed,
        # write the candidate without another cancellation check in between.
        self.device_config.refresh_info = refresh_record
        self._write_device_config()
        if not self._display_transactions_enabled:
            self._record_runtime_display_state(
                "committed",
                instance_uuid=None,
                changed_at=current_dt.isoformat(),
            )
        return image

    def _commit_prepared_display_result(
        self,
        command,
        resolved_snapshot,
        image,
        current_dt,
        prepared_selection,
    ):
        """Commit prepared bytes only after a fresh display transaction."""
        context = self._current_task_context(command)
        instance = resolved_snapshot.instance
        self._require_fresh_selection(command, context)
        display_commit = self._display_image(
            image,
            context=context,
            image_settings=getattr(
                self._execution_local,
                "image_settings",
                (),
            ),
            logical_target={
                "kind": "playlist",
                "playlist": resolved_snapshot.playlist_name,
                "plugin_id": instance.plugin_id,
                "plugin_instance": instance.name,
                "instance_uuid": instance.instance_uuid,
            },
            instance_revision=(
                instance.structural_generation,
                instance.settings_revision,
            ),
            force_hardware_write=self._force_hardware_write_requested(command),
        )
        self._require_automatic_hardware_write(
            command,
            display_commit,
            display_was_invoked=True,
        )
        commit_id, committed_at = self._display_commit_evidence(
            display_commit,
            instance.instance_uuid,
            current_dt,
            display_was_invoked=True,
        )
        self._require_fresh_selection(command, context)
        display_snapshot = self.runtime_state.snapshot()
        if (
            display_snapshot.display_state != "committed"
            or display_snapshot.display_commit_id != commit_id
            or display_snapshot.displayed_instance_uuid != instance.instance_uuid
        ):
            raise _StaleSelection("prepared display target changed after display commit")
        if not self._presentation_request_is_current(
            instance,
            prepared_selection.request.request_id,
            theme_mode=prepared_selection.theme_mode,
            require_prepared=True,
        ):
            raise _StaleSelection("prepared presentation changed after display commit")

        stage_path = self._staging_cache_path(
            instance,
            prepared_selection.theme_mode,
        )
        _save_image_atomic(image, stage_path)
        try:
            self._require_fresh_selection(command, context)
            if not self._presentation_request_is_current(
                instance,
                prepared_selection.request.request_id,
                theme_mode=prepared_selection.theme_mode,
                require_prepared=True,
            ):
                raise _StaleSelection("prepared presentation changed before cache promotion")
            canonical_path = self._snapshot_cache_path(
                instance,
                prepared_selection.theme_mode,
            )
            os.makedirs(os.path.dirname(canonical_path), exist_ok=True)
            os.replace(stage_path, canonical_path)
            stage_path = None
        finally:
            if stage_path and os.path.exists(stage_path):
                try:
                    os.remove(stage_path)
                except OSError:
                    logger.warning(
                        "Could not remove stale prepared stage: %s",
                        stage_path,
                    )

        image_hash = compute_image_hash(image)
        latest_refresh = self.device_config.get_refresh_info()
        refresh_info = {
            "refresh_type": "Playlist",
            "playlist": resolved_snapshot.playlist_name,
            "plugin_id": instance.plugin_id,
            "plugin_instance": instance.name,
            "refresh_time": current_dt.isoformat(),
            "image_hash": image_hash,
        }
        if (
            command.payload.get("preserve_rotation_anchor") is True
            and latest_refresh.refresh_time
            and not self._display_target_changed(latest_refresh, refresh_info)
        ):
            refresh_info["refresh_time"] = latest_refresh.refresh_time
        self.device_config.refresh_info = RefreshInfo(**refresh_info)
        theme_context = command.payload.get("theme_context")
        if theme_context:
            self._persist_active_theme(thaw_payload(theme_context), current_dt)
        self._write_playlist_display_commit(command)

        receipt = PresentationCommitReceipt(
            request_id=prepared_selection.request.request_id,
            committed_at=committed_at,
            display_commit_id=commit_id,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            theme_mode=prepared_selection.theme_mode,
        )
        last_good = LastGoodCacheState(
            theme_mode=prepared_selection.theme_mode,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=committed_at,
        )
        try:
            committed = self.runtime_state.commit_presentation(
                instance.instance_uuid,
                receipt,
                last_good_cache=last_good,
            )
        except Exception:
            published = self.runtime_state.snapshot().instances.get(
                instance.instance_uuid,
                InstanceRuntimeState(),
            )
            if (
                published.presentation_request is not None
                or published.presentation_receipt != receipt
                or published.last_good_cache != last_good
            ):
                raise
            logger.warning(
                "Presentation receipt was published before persistence raised. | instance_uuid: %s | request_id: %s",
                instance.instance_uuid,
                receipt.request_id,
            )
            committed = True
        if not committed:
            raise _StaleSelection("prepared presentation changed before receipt commit")
        self.retry_registry.mark_success(
            self._lane_retry_key(
                instance.instance_uuid,
                RefreshLane.PRESENTATION,
            )
        )
        if not self.presentation_cache.remove(prepared_selection.candidate):
            logger.warning(
                "Committed prepared presentation could not be removed. | instance_uuid: %s | request_id: %s",
                instance.instance_uuid,
                prepared_selection.request.request_id,
            )
        if command.payload.get("automatic_rotation") is True:
            self._request_next_presentation_after_display(
                current_dt,
                commit_id,
                committed_at,
                displayed_instance_uuid=instance.instance_uuid,
            )
        elif command.allow_prepared_presentation:
            self._request_presentation_after_display(
                instance,
                commit_id,
                committed_at,
            )
        return image

    def _display_commit_evidence(
        self,
        display_commit,
        instance_uuid,
        current_dt,
        *,
        display_was_invoked,
    ):
        commit_id = getattr(display_commit, "commit_id", None)
        committed_at = getattr(display_commit, "committed_at", None)
        if isinstance(commit_id, str) and commit_id and isinstance(committed_at, str) and committed_at:
            return commit_id, committed_at

        snapshot = self.runtime_state.snapshot()
        if (
            not display_was_invoked
            and snapshot.display_state == "committed"
            and snapshot.display_commit_id
            and snapshot.displayed_instance_uuid == instance_uuid
        ):
            return snapshot.display_commit_id, current_dt.isoformat()

        commit_id = uuid4().hex
        committed_at = current_dt.isoformat()
        self._record_runtime_display_state(
            "committed",
            commit_id=commit_id,
            instance_uuid=instance_uuid,
            changed_at=committed_at,
        )
        return commit_id, committed_at

    def _require_automatic_hardware_write(
        self,
        command,
        display_commit,
        *,
        display_was_invoked,
    ):
        if not self._force_hardware_write_requested(command):
            return
        if not display_was_invoked:
            raise RuntimeError("forced display did not invoke the panel")
        if self._display_transactions_enabled and (
            getattr(display_commit, "hardware_written", None) is not True
        ):
            raise RuntimeError("forced display did not write the panel")

    @staticmethod
    def _force_hardware_write_requested(command):
        return bool(
            command.payload.get("automatic_rotation") is True
            or command.payload.get("force_hardware_write") is True
        )

    def _request_presentation_after_display(
        self,
        instance,
        display_commit_id,
        committed_at,
    ):
        """Record one coalesced request using metadata-only trigger resolution."""
        target = self._presentation_request_target(instance)
        if target is None:
            return False
        _plugin_config, theme_mode = target
        request = PresentationRequestState(
            request_id=uuid4().hex,
            requested_at=committed_at,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            origin_theme_mode=theme_mode,
            origin_display_commit_id=display_commit_id,
        )
        return self.runtime_state.request_presentation(
            instance.instance_uuid,
            request,
        )

    def _presentation_request_target(self, instance):
        """Resolve a provider-safe presentation target without importing plugin code."""

        plugin_config, _theme_context, theme_mode = self._latest_presentation_theme(
            instance
        )
        if (
            not _presentation_refresh_enabled(self.device_config, plugin_config)
            or not plugin_supports_presentation_refresh(plugin_config)
        ):
            return None
        try:
            requested = resolve_refresh_on_display_for_config(
                thaw_payload(instance.settings),
                plugin_config,
            )
        except PluginSettingError as error:
            logger.warning(
                "Ignoring invalid refresh-on-display setting during presentation request. | plugin_id: %s | error: %s",
                instance.plugin_id,
                error,
            )
            return None
        except Exception:
            logger.exception(
                "Presentation trigger resolution failed closed. | plugin_id: %s",
                instance.plugin_id,
            )
            return None
        if not requested:
            return None
        return plugin_config, theme_mode

    def _request_next_presentation_after_display(
        self,
        current_dt,
        display_commit_id,
        committed_at,
        *,
        displayed_instance_uuid=None,
    ):
        """Reserve and start preparing the next rotation member immediately."""
        manager = self.device_config.get_playlist_manager()
        active = manager.snapshot_active_playlist(current_dt)
        if active is None:
            return False
        eligible_instance_uuids = {
            instance.instance_uuid
            for instance in active.plugins
            if instance.instance_uuid != displayed_instance_uuid
            and self._presentation_request_target(instance) is not None
        }
        if not eligible_instance_uuids:
            return False
        selection = manager.reserve_next_active_instance(
            current_dt,
            latest_refresh=None,
            interval_seconds=0,
            eligible_instance_uuids=eligible_instance_uuids,
            max_starvation_seconds=self._rotation_starvation_concession_seconds(),
        )
        if selection is None:
            return False
        requested = self._request_presentation_after_display(
            selection.instance,
            display_commit_id,
            committed_at,
        )
        reservation_has_work = requested
        if not reservation_has_work:
            # Same-revision requests coalesce instead of being recreated. Keep
            # the newly selected member reserved while its existing request
            # still awaits preparation or display, except during retry backoff.
            state = self.runtime_state.snapshot().instances.get(
                selection.instance.instance_uuid,
                InstanceRuntimeState(),
            )
            existing_request = state.presentation_request
            receipt = state.presentation_receipt
            reservation_has_work = (
                existing_request is not None
                and existing_request.structural_generation
                == selection.instance.structural_generation
                and existing_request.settings_revision
                == selection.instance.settings_revision
                and (
                    receipt is None
                    or receipt.request_id != existing_request.request_id
                )
                and not self._presentation_request_in_retry_backoff(
                    state,
                    current_dt,
                )
            )
        if not reservation_has_work:
            manager.release_rotation_reservation(
                selection.instance.instance_uuid,
                expected_playlist_name=selection.playlist_name,
            )
        if reservation_has_work:
            logger.info(
                "Reserved next rotation member for presentation preparation. | "
                "plugin_id: %s | instance_uuid: %s | request_created: %s",
                selection.instance.plugin_id,
                selection.instance.instance_uuid,
                requested,
            )
        return reservation_has_work

    def _enqueue_live_display_followup(
        self,
        command,
        resolved_snapshot,
        current_dt,
        theme_mode,
    ):
        """Queue an exact cache-only display after a successful visible live refresh."""
        instance = resolved_snapshot.instance
        if not _live_display_refresh_enabled(
            self.device_config,
            instance.plugin_id,
            instance.settings,
        ):
            return None
        if command.payload.get("background_live_refresh") is True:
            return None
        if not self._live_display_target_is_current(command):
            return None
        followup = self._playlist_command(
            resolved_snapshot.playlist_name,
            instance,
            source=CommandSource.LIVE,
            intent=RefreshIntent.DISPLAY_CACHE,
            force=False,
            display_cached_only=True,
            priority=75,
            kind=CommandKind.DISPLAY,
            current_dt=current_dt,
            resolved_theme_context=command.payload.get("resolved_theme_context"),
            cache_theme_mode=theme_mode,
            expected_displayed_instance_uuid=instance.instance_uuid,
            coalescing_scope=f"live-followup:{command.id}",
            allow_prepared_presentation=False,
        )
        return self.refresh_queue.submit(followup)

    def _enqueue_theme_display_followup(
        self,
        command,
        resolved_snapshot,
        current_dt,
        theme_mode,
    ):
        """Queue the cache-only display half of an exact theme transition."""
        if not _display_triggered_refresh_enabled(self.device_config):
            return None
        if not self._live_display_target_is_current(command):
            return None
        instance = resolved_snapshot.instance
        followup = self._playlist_command(
            resolved_snapshot.playlist_name,
            instance,
            source=CommandSource.SCHEDULER,
            intent=RefreshIntent.DISPLAY_CACHE,
            force=False,
            display_cached_only=True,
            priority=85,
            kind=CommandKind.DISPLAY,
            theme_context=command.payload.get("theme_context"),
            current_dt=current_dt,
            resolved_theme_context=command.payload.get("resolved_theme_context"),
            cache_theme_mode=theme_mode,
            expected_displayed_instance_uuid=instance.instance_uuid,
            preserve_rotation_anchor=True,
            coalescing_scope=f"theme-followup:{command.id}",
            allow_prepared_presentation=False,
        )
        return self.refresh_queue.submit(followup)

    def _exact_cache_is_valid(self, instance, theme_mode):
        runtime_instance = self.runtime_state.snapshot().instances.get(
            instance.instance_uuid,
            InstanceRuntimeState(),
        )
        return (
            self.cache_catalog.resolve_exact(
                instance,
                theme_mode,
                runtime_instance,
            )
            is not None
        )

    @staticmethod
    def _abort_details(error):
        if isinstance(error, TaskDeadlineExceeded):
            return JobStatus.ABANDONED, "deadline_expired", str(error)
        if isinstance(error, _StaleSelection):
            return JobStatus.CANCELED, "stale_selection", str(error)
        if isinstance(error, TaskCancelled):
            return JobStatus.CANCELED, "task_canceled", str(error)
        return None

    def _classify_command_abort(self, command, context):
        try:
            if command.instance_uuid is None:
                context.raise_if_cancelled()
            else:
                self._require_fresh_selection(command, context)
        except (TaskDeadlineExceeded, _StaleSelection, TaskCancelled) as error:
            return self._abort_details(error)
        return None

    def _record_command_failure(self, command, error):
        if (
            command.intent is RefreshIntent.DISPLAY_CACHE
            and plugin_supports_cached_display_redraw(
                self.device_config.get_plugin(command.plugin_id)
            )
        ):
            # Local rendering/panel failure says nothing about vehicle DATA.
            if not self._record_rotation_deadline_failure_safely(command, error):
                self.scheduler_state.record_failure(error)
            return
        if command.intent is RefreshIntent.THEME_CATCHUP:
            current_dt = self._get_current_datetime()
            target_mode = _resolved_theme_mode(command.payload)
            if target_mode in {"day", "night"}:
                self.runtime_state.record_theme_catchup_failure(
                    command.instance_uuid,
                    target_mode,
                    current_dt.isoformat(),
                    error,
                    (
                        current_dt
                        + timedelta(
                            seconds=DEFAULT_THEME_CATCHUP_RETRY_COOLDOWN_SECONDS
                        )
                    ).isoformat(),
                )
            return
        theme_context = command.payload.get("theme_context")
        if theme_context:
            context = self._current_task_context(command)
            if command.instance_uuid is None:
                context.raise_if_cancelled()
            else:
                self._require_fresh_selection(command, context)
            self._mark_theme_refresh_failed(
                thaw_payload(theme_context),
                self._get_current_datetime(),
                error,
            )
        self.scheduler_state.record_failure(error)
        lane = self._lane_for_intent(command.intent)
        if command.instance_uuid is not None and lane is not None:
            self._record_intent_failure(
                command,
                error,
                self._get_current_datetime(),
            )
            if lane in {RefreshLane.DATA, RefreshLane.PRESENTATION}:
                self._release_failed_rotation_reservation(command)
            self.scheduler_state.set_next_attempt(
                self._clock() + self._scheduler_poll_seconds()
            )
            return
        key = command.instance_uuid or RetryRegistry.GLOBAL_KEY
        delay = self.retry_registry.mark_failure(key, self._clock())
        self._record_runtime_failure(command, error, delay)
        self.scheduler_state.set_next_attempt(self._clock() + delay)

    def _signal_completion(self, actual_job_id):
        ready = []
        with self._completion_lock:
            for requested_id, event in tuple(self._completion_events.items()):
                entry = self.refresh_queue.get_entry(requested_id)
                if entry is None or entry.job.id == actual_job_id:
                    ready.append((requested_id, event))
            for requested_id, _event in ready:
                self._completion_events.pop(requested_id, None)
        for _requested_id, event in ready:
            event.set()

    @staticmethod
    def _normalize_transient_paths(paths):
        normalized = []
        seen = set()
        for path in paths or ():
            try:
                value = os.fspath(path)
            except TypeError:
                continue
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return tuple(normalized)

    @staticmethod
    def _remove_transient_paths(paths):
        for path in paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            except OSError as error:
                logger.warning("Could not remove transient upload %s: %s", path, error)

    def _cleanup_transient_uploads(self, job_id, command=None):
        with self._transient_upload_lock:
            owned_paths = self._transient_uploads.pop(job_id, ())
        if not owned_paths and command is not None:
            payload = thaw_payload(command.payload)
            owned_paths = self._normalize_transient_paths(
                payload.get("transient_upload_paths", ())
            )
        self._remove_transient_paths(owned_paths)

    def _cleanup_all_transient_uploads(self):
        with self._transient_upload_lock:
            batches = tuple(self._transient_uploads.values())
            self._transient_uploads.clear()
        for paths in batches:
            self._remove_transient_paths(paths)

    def _reap_terminal_transient_uploads(self):
        with self._transient_upload_lock:
            job_ids = tuple(self._transient_uploads)
        for job_id in job_ids:
            entry = self.refresh_queue.get_entry(job_id)
            if entry is None or entry.job.status not in {
                JobStatus.QUEUED,
                JobStatus.RUNNING,
            }:
                self._cleanup_transient_uploads(
                    job_id,
                    entry.command if entry is not None else None,
                )

    def _get_rotation_wait_seconds(self):
        """Return time until the next playlist tick without evaluating plugins."""
        interval = self.device_config.get_config(
            "plugin_cycle_interval_seconds",
            default=DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
        )
        try:
            interval = float(interval)
        except (TypeError, ValueError):
            interval = DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS
        if interval <= 0:
            return DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS

        try:
            latest_refresh_dt = self.device_config.get_refresh_info().get_refresh_datetime()
        except Exception:
            logger.exception("Could not read latest refresh time for scheduler wait.")
            return interval
        if not latest_refresh_dt:
            return interval

        current_dt = self._get_current_datetime()
        if latest_refresh_dt.tzinfo is None and current_dt.tzinfo is not None:
            localize = getattr(current_dt.tzinfo, "localize", None)
            latest_refresh_dt = localize(latest_refresh_dt) if localize else latest_refresh_dt.replace(tzinfo=current_dt.tzinfo)
        elapsed = (current_dt - latest_refresh_dt).total_seconds()
        return max(0, min(interval, interval - elapsed))

    def _get_refresh_wait_seconds(self):
        """Return time until any scheduler work is due."""
        wait_seconds = self._get_rotation_wait_seconds()
        current_dt = self._get_current_datetime()
        live_wait_seconds = self._live_refresh_wait_seconds(current_dt)
        if live_wait_seconds is not None:
            if live_wait_seconds <= 0 < wait_seconds:
                wait_seconds = min(wait_seconds, 5.0)
            else:
                wait_seconds = min(wait_seconds, max(0, live_wait_seconds))
        return wait_seconds

    def _command_from_refresh_action(self, refresh_action, *, transient_paths=()):
        now = self._clock()
        deadline = now + self._manual_update_timeout_seconds()
        if isinstance(refresh_action, ManualRefresh):
            payload = {
                "refresh_type": "Manual Update",
                "settings": refresh_action.plugin_settings,
            }
            if transient_paths:
                payload["transient_upload_paths"] = tuple(transient_paths)
            return RefreshCommand.create(
                kind=CommandKind.DISPLAY,
                source=CommandSource.MANUAL,
                plugin_id=refresh_action.plugin_id,
                payload=payload,
                now_monotonic=now,
                deadline_monotonic=deadline,
                force=True,
                priority=100,
                intent=RefreshIntent.MANUAL_RENDER,
            )
        if isinstance(refresh_action, PlaylistRefresh):
            snapshot = refresh_action.plugin_instance.snapshot()
            return self._playlist_command(
                refresh_action.playlist.name,
                snapshot,
                source=CommandSource.MANUAL,
                intent=(
                    RefreshIntent.MANUAL_RENDER
                    if refresh_action.force or not refresh_action.display_cached_only
                    else RefreshIntent.DISPLAY_CACHE
                ),
                force=refresh_action.force,
                display_cached_only=refresh_action.display_cached_only,
                priority=100,
                deadline_monotonic=deadline,
            )
        raise TypeError(f"Unsupported refresh action: {type(refresh_action).__name__}")

    def _manual_update_plugin_id(self, refresh_action):
        try:
            return refresh_action.get_plugin_id()
        except Exception:
            return None

    def _manual_update_timeout_seconds(self):
        raw_value = self.device_config.get_config(
            "manual_update_timeout_seconds",
            default=DEFAULT_MANUAL_UPDATE_TIMEOUT_SECONDS,
        )
        try:
            value = float(raw_value)
        except (TypeError, ValueError, OverflowError):
            value = DEFAULT_MANUAL_UPDATE_TIMEOUT_SECONDS
        return max(0.01, min(600.0, value))

    def _playlist_command(
        self,
        playlist_name,
        instance,
        *,
        source,
        intent,
        force=False,
        display_cached_only=True,
        priority=50,
        deadline_monotonic=None,
        kind=CommandKind.DISPLAY,
        theme_context=None,
        theme_render_only=False,
        current_dt=None,
        resolved_theme_context=None,
        require_active=True,
        cache_theme_mode=None,
        expected_displayed_instance_uuid=None,
        preserve_rotation_anchor=False,
        coalescing_scope=None,
        allow_prepared_presentation=None,
        presentation_request_id=None,
        automatic_rotation=False,
        force_hardware_write=False,
        background_live_refresh=False,
        weather_liveness_concession=False,
    ):
        now = self._clock()
        normalized_intent = RefreshIntent(intent)
        if deadline_monotonic is None:
            deadline_monotonic = now + self._manual_update_timeout_seconds()
        if (
            automatic_rotation
            and normalized_intent is RefreshIntent.PRESENTATION_REFRESH
        ):
            try:
                rotation_wait_seconds = float(self._get_rotation_wait_seconds())
            except (TypeError, ValueError, OverflowError):
                logger.exception(
                    "Could not resolve automatic presentation rotation deadline."
                )
            else:
                if math.isfinite(rotation_wait_seconds):
                    rotation_budget_seconds = max(
                        0.01,
                        rotation_wait_seconds
                        - DEFAULT_ROTATION_DEADLINE_CLEANUP_SECONDS,
                    )
                    deadline_monotonic = min(
                        deadline_monotonic,
                        now + rotation_budget_seconds,
                    )
        if (
            expected_displayed_instance_uuid is None
            and normalized_intent is RefreshIntent.THEME_REDRAW
            and theme_render_only
        ):
            expected_displayed_instance_uuid = instance.instance_uuid
        payload = {
            "refresh_type": "Playlist",
            "playlist_name": playlist_name,
            "instance_name": instance.name,
            "settings": instance.settings,
            "refresh": instance.refresh,
            "latest_refresh_time": instance.latest_refresh_time,
            "display_cached_only": bool(display_cached_only),
            "require_active": bool(require_active),
        }
        if theme_context:
            payload["theme_context"] = theme_context
        if theme_render_only:
            payload["theme_render_only"] = True
        if expected_displayed_instance_uuid is not None:
            payload["expected_displayed_instance_uuid"] = str(
                expected_displayed_instance_uuid
            )
        if background_live_refresh:
            payload["background_live_refresh"] = True
        if weather_liveness_concession:
            payload["weather_liveness_concession"] = True
        if preserve_rotation_anchor:
            payload["preserve_rotation_anchor"] = True
        if resolved_theme_context is None:
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            resolved_theme_context = _resolved_theme_context_for_instance(
                instance,
                plugin_config,
                self.device_config,
                current_dt=current_dt,
            )
        else:
            resolved_theme_context = thaw_payload(resolved_theme_context)
        if resolved_theme_context is not None:
            payload["resolved_theme_context"] = resolved_theme_context
        if normalized_intent is RefreshIntent.DISPLAY_CACHE:
            payload["cache_theme_mode"] = cache_theme_mode
        if presentation_request_id is not None:
            payload["presentation_request_id"] = str(presentation_request_id)
        if automatic_rotation:
            payload["automatic_rotation"] = True
            # Queue expiry must hand this command back to RefreshTask so the
            # exact reservation can be released with retry bookkeeping.
            payload["rotation_deadline_cleanup"] = True
        if force_hardware_write:
            payload["force_hardware_write"] = True
        if allow_prepared_presentation is None:
            allow_prepared_presentation = (
                normalized_intent is RefreshIntent.DISPLAY_CACHE
                and source in {CommandSource.MANUAL, CommandSource.SCHEDULER}
                and coalescing_scope is None
                and expected_displayed_instance_uuid is None
            )
        plugin_config = self.device_config.get_plugin(instance.plugin_id)
        if not _presentation_refresh_enabled(
            self.device_config,
            plugin_config,
        ):
            allow_prepared_presentation = False
        return RefreshCommand.create(
            kind=kind,
            source=source,
            plugin_id=instance.plugin_id,
            instance_uuid=instance.instance_uuid,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            payload=payload,
            now_monotonic=now,
            deadline_monotonic=deadline_monotonic,
            force=force,
            priority=priority,
            intent=intent,
            coalescing_scope=coalescing_scope,
            allow_prepared_presentation=allow_prepared_presentation,
        )

    def _rejected_manual_job(self, refresh_action, error):
        now = self._wall_clock()
        return {
            "id": uuid4().hex,
            "status": "rejected",
            "plugin_id": self._manual_update_plugin_id(refresh_action),
            "refresh_type": type(refresh_action).__name__,
            "submitted_at": now,
            "completed_at": now,
            "error": error,
        }

    def manual_update(self, refresh_action):
        """Submit a bounded queue command and wait without owning job history."""
        if not self.running:
            logger.warning("Background refresh task is not running, unable to do a manual update")
            return None
        command = self._command_from_refresh_action(refresh_action)
        completion = threading.Event()
        with self._completion_lock:
            self._completion_events[command.id] = completion
        try:
            self.refresh_queue.submit(command)
        except Exception:
            with self._completion_lock:
                self._completion_events.pop(command.id, None)
            raise

        timeout = self._manual_update_timeout_seconds()
        deadline = time.monotonic() + timeout
        while True:
            job = self.get_manual_update_job(command.id)
            if job is not None and job["status"] not in {"queued", "running"}:
                break
            if job is None:
                with self._completion_lock:
                    self._completion_events.pop(command.id, None)
                raise RuntimeError("Manual update result is no longer available")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._completion_lock:
                    self._completion_events.pop(command.id, None)
                raise TimeoutError(f"Manual update timed out after {timeout:.0f} seconds")
            completion.wait(timeout=min(0.05, remaining))

        with self._completion_lock:
            self._completion_events.pop(command.id, None)
        if job["status"] == "failed":
            raise RuntimeError(job.get("error") or "Manual update failed")
        if job["status"] == "timed_out" or job.get("error_code") == "deadline_expired":
            raise TimeoutError(f"Manual update timed out after {timeout:.0f} seconds")
        if job["status"] == "canceled":
            raise TaskCancelled(job.get("error") or "Manual update canceled")
        return job

    def submit_manual_update(self, refresh_action, *, transient_paths=()):
        """Queue a manual refresh and return the bounded queue job payload."""
        if not self.running and self.refresh_queue.snapshot().accepting:
            logger.warning("Background refresh task is not running, unable to queue a manual update")
            return self._rejected_manual_job(refresh_action, "Background refresh task is not running")
        owned_paths = self._normalize_transient_paths(transient_paths)
        command = self._command_from_refresh_action(
            refresh_action,
            transient_paths=owned_paths,
        )
        if owned_paths:
            with self._transient_upload_lock:
                self._transient_uploads[command.id] = owned_paths
        try:
            job = self.refresh_queue.submit(command)
        except BaseException:
            with self._transient_upload_lock:
                self._transient_uploads.pop(command.id, None)
            raise
        return self._job_payload(self.refresh_queue.get_entry(job.id))

    def submit_playlist_display(
        self,
        instance_uuid,
        *,
        force=True,
        display_cached_only=False,
        expected_playlist_name=None,
        expected_generation=None,
        expected_settings_revision=None,
        require_active=True,
        force_hardware_write=False,
        request_presentation_after_display=False,
    ):
        """Queue an immutable, cache-only playlist display command by UUID."""
        if not self.running and self.refresh_queue.snapshot().accepting:
            raise RuntimeError("Background refresh task is not running")
        current_dt = self._get_current_datetime()
        playlist_manager = self.device_config.get_playlist_manager()
        explicit_selection = any(
            value is not None
            for value in (
                expected_playlist_name,
                expected_generation,
                expected_settings_revision,
            )
        )
        playlist_name = None
        instance = None
        if explicit_selection:
            if any(
                value is None
                for value in (
                    expected_playlist_name,
                    expected_generation,
                    expected_settings_revision,
                )
            ):
                raise ValueError("Playlist display CAS requires playlist, generation, and revision")
            selection = playlist_manager.validate_selection(
                instance_uuid,
                expected_playlist_name=expected_playlist_name,
                expected_generation=expected_generation,
                expected_settings_revision=expected_settings_revision,
                current_datetime=current_dt,
                require_active=bool(require_active),
            )
            if selection is not None:
                playlist_name = selection.playlist_name
                instance = selection.instance
        else:
            if not require_active:
                raise ValueError("Inactive playlist display requires exact CAS metadata")
            active = playlist_manager.snapshot_active_playlist(current_dt)
            if active is not None:
                playlist_name = active.name
                instance = next(
                    (
                        candidate
                        for candidate in active.plugins
                        if candidate.instance_uuid == instance_uuid
                    ),
                    None,
                )
        if instance is None:
            raise ValueError(f"Playlist instance not found or changed: {instance_uuid}")
        if force or not display_cached_only:
            logger.info(
                "Ignoring legacy playlist display render flags; display is cache-only. | "
                "instance_uuid: %s",
                instance.instance_uuid,
            )
        plugin_config = self.device_config.get_plugin(instance.plugin_id)
        resolved_theme = _resolved_theme_context_for_instance(
            instance,
            plugin_config,
            self.device_config,
            current_dt=current_dt,
        )
        resolved_theme_mode = (
            resolved_theme.get("mode")
            if isinstance(resolved_theme, Mapping)
            else None
        )
        runtime_instance = self.runtime_state.snapshot().instances.get(
            instance.instance_uuid,
            InstanceRuntimeState(),
        )
        candidate = self.cache_catalog.resolve(
            instance,
            resolved_theme_mode,
            runtime_instance,
        )
        command = self._playlist_command(
            playlist_name,
            instance,
            source=CommandSource.MANUAL,
            intent=RefreshIntent.DISPLAY_CACHE,
            force=False,
            display_cached_only=True,
            priority=100,
            require_active=bool(require_active),
            current_dt=current_dt,
            cache_theme_mode=(
                candidate.theme_mode
                if candidate is not None
                else resolved_theme_mode
            ),
            force_hardware_write=bool(force_hardware_write),
            allow_prepared_presentation=bool(
                request_presentation_after_display
            ),
        )
        job = self.refresh_queue.submit(command)
        return self._job_payload(self.refresh_queue.get_entry(job.id))

    def submit_playlist_data_refresh(
        self,
        instance_uuid,
        *,
        expected_playlist_name,
        expected_generation,
        expected_settings_revision,
        require_active=True,
    ):
        """Queue a forced data refresh for one exact immutable playlist instance."""
        if not self.running and self.refresh_queue.snapshot().accepting:
            raise RuntimeError("Background refresh task is not running")
        if any(
            value is None
            for value in (
                expected_playlist_name,
                expected_generation,
                expected_settings_revision,
            )
        ):
            raise ValueError("Playlist data refresh requires exact CAS metadata")

        current_dt = self._get_current_datetime()
        selection = self.device_config.get_playlist_manager().validate_selection(
            instance_uuid,
            expected_playlist_name=expected_playlist_name,
            expected_generation=expected_generation,
            expected_settings_revision=expected_settings_revision,
            current_datetime=current_dt,
            require_active=bool(require_active),
        )
        if selection is None:
            raise ValueError(f"Playlist instance not found or changed: {instance_uuid}")

        command = self._playlist_command(
            selection.playlist_name,
            selection.instance,
            source=CommandSource.MANUAL,
            intent=RefreshIntent.DATA_REFRESH,
            force=True,
            display_cached_only=False,
            priority=100,
            kind=CommandKind.CACHE_REFRESH,
            current_dt=current_dt,
            require_active=bool(require_active),
        )
        job = self.refresh_queue.submit(command)
        return self._job_payload(self.refresh_queue.get_entry(job.id))

    @staticmethod
    def _legacy_job_status(status):
        return {
            JobStatus.SUCCEEDED: "completed",
            JobStatus.ABANDONED: "timed_out",
        }.get(status, status.value)

    def _job_payload(self, entry):
        if entry is None:
            return None
        command = entry.command
        job = entry.job
        payload = {
            "id": job.id,
            "status": self._legacy_job_status(job.status),
            "plugin_id": command.plugin_id,
            "refresh_type": str(command.payload.get("refresh_type") or command.kind.value),
            "submitted_at": job.submitted_at,
        }
        if command.instance_uuid is not None:
            payload["instance_uuid"] = command.instance_uuid
        for key in ("started_at", "completed_at", "cancel_requested_at", "superseded_by", "error_code", "error"):
            value = getattr(job, key)
            if value is not None:
                payload[key] = value
        return payload

    def get_manual_update_job(self, job_id):
        return self._job_payload(self.refresh_queue.get_entry(job_id))

    def wait_for_job(self, job_id, timeout=1.0):
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            job = self.get_manual_update_job(job_id)
            if job is None or job["status"] not in {"queued", "running"}:
                return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return job
            self.refresh_queue.wait_for_change(
                self.refresh_queue.change_token(),
                timeout=min(0.05, remaining),
            )

    def signal_config_change(self):
        """Force a fresh scheduler probe and publish a non-lossy queue wake."""
        self._prune_runtime_state()
        if self.running:
            self.scheduler_state.set_next_attempt(self._clock())
            self.refresh_queue.wake()

    def _prune_runtime_state(self):
        get_manager = getattr(self.device_config, "get_playlist_manager", None)
        if not callable(get_manager):
            return
        try:
            manager = get_manager()
            payload = manager.to_dict()
            current_instance_uuids = {
                plugin["instance_uuid"]
                for playlist in payload.get("playlists", [])
                for plugin in playlist.get("plugins", [])
                if plugin.get("instance_uuid")
            }
            self.runtime_state.prune(current_instance_uuids)
            snapshots = tuple(
                snapshot
                for instance_uuid in sorted(current_instance_uuids)
                if (snapshot := manager.snapshot_instance(instance_uuid)) is not None
            )
            self._migrate_runtime_instances(snapshots)
        except Exception:
            logger.exception("Runtime instance tombstones could not be pruned")

    def _migrate_runtime_instances(self, instances):
        """Seed empty lane clocks and exact cache metadata without config writes."""
        for instance in instances:
            current = self.runtime_state.snapshot().instances.get(
                instance.instance_uuid,
                InstanceRuntimeState(),
            )
            data_seed = None
            if current.data.last_success_at is None:
                parsed = self._parse_iso_datetime(instance.latest_refresh_time)
                if parsed is not None:
                    data_seed = str(instance.latest_refresh_time).strip()
            last_good = None
            if current.last_good_cache is None:
                last_good = self._discover_exact_last_good_cache(instance)
            if data_seed is None and last_good is None:
                continue

            def update(previous):
                candidate = previous
                if data_seed is not None and previous.data.last_success_at is None:
                    candidate = replace(
                        candidate,
                        data=replace(candidate.data, last_success_at=data_seed),
                    )
                if last_good is not None and previous.last_good_cache is None:
                    candidate = replace(candidate, last_good_cache=last_good)
                return candidate

            self.runtime_state._update_instance(
                instance.instance_uuid,
                self._runtime_now_iso(),
                update,
            )

    def _discover_exact_last_good_cache(self, instance):
        plugin_config = self.device_config.get_plugin(instance.plugin_id)
        resolved_theme = _resolved_theme_context_for_instance(
            instance,
            plugin_config,
            self.device_config,
            current_dt=self._get_current_datetime(),
        )
        preferred_mode = (
            resolved_theme.get("mode")
            if isinstance(resolved_theme, Mapping)
            else None
        )
        modes = []
        for mode in (preferred_mode, None, "day", "night"):
            if mode not in modes:
                modes.append(mode)
        discovered = []
        for preference, mode in enumerate(modes):
            cache_path = authoritative_cache_path(
                self.cache_catalog.cache_root,
                instance.instance_uuid,
                instance.structural_generation,
                instance.settings_revision,
                mode,
            )
            candidate = DisplayCacheCandidate(
                instance_uuid=instance.instance_uuid,
                structural_generation=instance.structural_generation,
                settings_revision=instance.settings_revision,
                theme_mode=mode,
                cache_path=cache_path,
                promoted_at=None,
            )
            if not self.cache_catalog.validate(candidate):
                continue
            try:
                promoted_at = datetime.fromtimestamp(
                    os.path.getmtime(cache_path),
                    tz=timezone.utc,
                ).isoformat()
            except OSError:
                continue
            discovered.append((promoted_at, -preference, mode))
        if not discovered:
            return None
        promoted_at, _preference, mode = max(discovered)
        return LastGoodCacheState(
            theme_mode=mode,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=promoted_at,
        )

    def _get_current_datetime(self):
        """Retrieves the current datetime based on the device's configured timezone."""
        tz_str = self.device_config.get_config("timezone", default="UTC")
        try:
            timezone_info = pytz.timezone(tz_str)
        except Exception:
            logger.warning("Invalid timezone '%s'; falling back to UTC.", tz_str)
            timezone_info = pytz.UTC
        return datetime.now(timezone_info)

    def _determine_next_plugin(self, playlist_manager, latest_refresh_info, current_dt):
        """Determines the next plugin to refresh based on the active playlist, plugin cycle interval, and current time."""
        playlist = playlist_manager.determine_active_playlist(current_dt)
        if not playlist:
            playlist_manager.active_playlist = None
            logger.info(f"No active playlist determined.")
            return None, None

        playlist_manager.active_playlist = playlist.name
        if not playlist.plugins:
            logger.info(f"Active playlist '{playlist.name}' has no plugins.")
            return None, None

        latest_refresh_dt = latest_refresh_info.get_refresh_datetime()
        plugin_cycle_interval = self.device_config.get_config(
            "plugin_cycle_interval_seconds",
            default=DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
        )
        should_refresh = PlaylistManager.should_refresh(latest_refresh_dt, plugin_cycle_interval, current_dt)

        if not should_refresh:
            latest_refresh_str = latest_refresh_dt.strftime('%Y-%m-%d %H:%M:%S') if latest_refresh_dt else "None"
            logger.info(f"Not time to update display. | latest_update: {latest_refresh_str} | plugin_cycle_interval: {plugin_cycle_interval}")
            return None, None

        plugin = playlist.get_next_plugin()
        logger.info(f"Determined next plugin. | active_playlist: {playlist.name} | plugin_instance: {plugin.name}")

        return playlist, plugin

    def _determine_theme_refresh_plugin(self, playlist_manager, latest_refresh_info, current_dt):
        """Returns the currently displayed playlist plugin when possible for a theme-only redraw."""
        playlist = playlist_manager.determine_active_playlist(current_dt)
        if not playlist:
            playlist_manager.active_playlist = None
            logger.info("No active playlist determined for theme refresh.")
            return None, None

        playlist_manager.active_playlist = playlist.name
        if not playlist.plugins:
            logger.info(f"Active playlist '{playlist.name}' has no plugins for theme refresh.")
            return None, None

        displayed = None
        if (
            latest_refresh_info
            and latest_refresh_info.refresh_type == "Playlist"
            and latest_refresh_info.playlist == playlist.name
        ):
            displayed = playlist.find_plugin(latest_refresh_info.plugin_id, latest_refresh_info.plugin_instance)

        plugin = displayed or playlist.get_next_plugin()
        logger.info(f"Determined theme refresh plugin. | active_playlist: {playlist.name} | plugin_instance: {plugin.name}")
        return playlist, plugin

    def _has_theme_changed(self, theme_context, current_dt=None):
        current_mode = (theme_context or {}).get("mode")
        previous_mode = self._get_config_value("active_theme", None)
        if current_mode and previous_mode != current_mode and self._theme_refresh_retry_delayed(theme_context, current_dt):
            return False
        return bool(current_mode and previous_mode != current_mode)

    @staticmethod
    def _theme_status_info(theme_context, current_dt):
        return {
            "mode": theme_context.get("mode"),
            "source": theme_context.get("source"),
            "reason": theme_context.get("reason"),
            "date": theme_context.get("date"),
            "timezone": theme_context.get("timezone"),
            "sunrise": theme_context.get("sunrise"),
            "sunset": theme_context.get("sunset"),
            "updated_at": current_dt.isoformat(),
        }

    @staticmethod
    def _theme_status_projection(info):
        if not isinstance(info, Mapping):
            return None
        return tuple(
            info.get(key)
            for key in (
                "mode",
                "source",
                "reason",
                "date",
                "timezone",
                "sunrise",
                "sunset",
            )
        )

    def _update_active_theme_info(self, theme_context, current_dt):
        if not isinstance(theme_context, Mapping) or theme_context.get("mode") not in {
            "day",
            "night",
        }:
            return False
        info = self._theme_status_info(theme_context, current_dt)
        previous = self._get_config_value("active_theme_info", None)
        if (
            self._theme_status_projection(previous)
            == self._theme_status_projection(info)
            and isinstance(previous, Mapping)
            and set(previous) == set(info)
        ):
            return False
        self._set_config_value("active_theme_info", info)
        return True

    def _persist_active_theme(self, theme_context, current_dt):
        mode = theme_context.get("mode")
        if not mode:
            return
        self._update_active_theme_info(theme_context, current_dt)
        self._set_config_value("active_theme", mode)
        self._set_config_value("active_theme_refresh_failure", None)

    def _mark_theme_refresh_failed(self, theme_context, current_dt, error):
        mode = (theme_context or {}).get("mode")
        if not mode:
            return
        cooldown_seconds = max(0.0, self._config_float(
            "theme_refresh_retry_cooldown_seconds",
            DEFAULT_THEME_REFRESH_RETRY_COOLDOWN_SECONDS,
        ))
        retry_after = current_dt + timedelta(seconds=cooldown_seconds)
        info = {
            "mode": mode,
            "source": theme_context.get("source"),
            "reason": theme_context.get("reason"),
            "date": theme_context.get("date"),
            "failed_at": current_dt.isoformat(),
            "retry_after": retry_after.isoformat(),
            "error": str(error)[:240],
        }
        logger.warning(
            "Theme refresh failed; delaying same-theme retry. | active_theme: %s | retry_after: %s | error: %s",
            mode,
            info["retry_after"],
            info["error"],
        )
        self._set_config_value("active_theme_refresh_failure", info)
        self._write_device_config()

    def _theme_refresh_retry_delayed(self, theme_context, current_dt):
        if current_dt is None:
            return False
        current_mode = (theme_context or {}).get("mode")
        failure = self._get_config_value("active_theme_refresh_failure", None)
        if not isinstance(failure, dict) or failure.get("mode") != current_mode:
            return False
        retry_after = self._parse_datetime_config(failure.get("retry_after"), current_dt)
        if retry_after is None or current_dt >= retry_after:
            return False
        logger.info(
            "Theme refresh retry delayed after previous failure. | active_theme: %s | retry_after: %s",
            current_mode,
            retry_after.isoformat(),
        )
        return True

    def _parse_datetime_config(self, value, reference_dt):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None and getattr(reference_dt, "tzinfo", None) is not None:
            parsed = parsed.replace(tzinfo=reference_dt.tzinfo)
        return parsed

    def _set_config_value(self, key, value):
        if hasattr(self.device_config, "update_value"):
            self.device_config.update_value(key, value)
        elif hasattr(self.device_config, "config") and isinstance(self.device_config.config, dict):
            self.device_config.config[key] = value

    def _get_config_value(self, key, default=None):
        if hasattr(self.device_config, "get_config"):
            return self.device_config.get_config(key, default=default)
        if hasattr(self.device_config, "config") and isinstance(self.device_config.config, dict):
            return self.device_config.config.get(key, default)
        return default

    def _write_playlist_display_commit(self, command):
        """Persist a display commit and its automatic bag acknowledgement together."""
        if command.payload.get("automatic_rotation") is not True:
            self._write_device_config()
            return

        manager = self.device_config.get_playlist_manager()
        acknowledgement = manager.acknowledge_rotation_display(
            command.instance_uuid,
            expected_playlist_name=command.payload.get("playlist_name"),
        )
        if acknowledgement is None:
            raise _StaleSelection("automatic display reservation changed at commit")
        try:
            self._write_device_config()
        except BaseException:
            if not manager.rollback_rotation_acknowledgement(acknowledgement):
                logger.error(
                    "Automatic display acknowledgement could not be rolled back. | instance_uuid: %s",
                    command.instance_uuid,
                )
            raise
        self.retry_registry.mark_success(
            self._rotation_display_retry_key(command.instance_uuid)
        )

    def _write_device_config(self):
        with self.config_write_lock:
            self.device_config.write_config()

    def _start_due_plugin_cache_refresh(self, playlist, current_dt, skip_plugin_instance=None, displayed_plugin_instance=None, force=False, only_plugin_id=None):
        """Submit one bounded cache command per due immutable instance."""
        if not self.running:
            return
        if self.manual_update_in_progress():
            logger.info("Due plugin cache refresh skipped while manual update is running.")
            return
        if self._cache_refresh_under_resource_pressure(allow_high_swap=only_plugin_id is not None):
            return
        active = self.device_config.get_playlist_manager().snapshot_active_playlist(current_dt)
        if active is None:
            return
        skip_uuid = getattr(skip_plugin_instance, "instance_uuid", None)
        commands = []
        for instance in active.plugins:
            if skip_uuid and instance.instance_uuid == skip_uuid:
                continue
            if only_plugin_id and instance.plugin_id != only_plugin_id:
                continue
            if self._snapshot_background_cache_disabled(instance):
                continue
            missing = not os.path.exists(self._snapshot_cache_path(instance))
            if (
                not force
                and not missing
                and not self._snapshot_should_refresh(instance, current_dt)
                and not self._snapshot_live_refresh_due(instance, current_dt)
            ):
                continue
            commands.append(self._playlist_command(
                active.name,
                instance,
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.DATA_REFRESH,
                force=force,
                display_cached_only=False,
                priority=10,
                kind=CommandKind.CACHE_REFRESH,
            ))
        for command in commands[:self._background_cache_refresh_max_per_pass()]:
            self.refresh_queue.submit(command)

    def _maybe_start_background_cache_refresh(self, playlist, displayed_plugin_instance, current_dt, force=False):
        """Kick off a background cache refresh pass after a display tick."""
        if not playlist or not self._playlist_has_background_cache_refresh_due(
            playlist,
            current_dt,
            displayed_plugin_instance=displayed_plugin_instance,
        ):
            return
        only_plugin_id = None
        if not self._plugin_instance_background_cache_refresh_due(
            displayed_plugin_instance,
            current_dt,
            displayed_plugin_instance=displayed_plugin_instance,
        ):
            live_refresh_plugin = self._playlist_live_refresh_due_plugin_instance(playlist, current_dt)
            if live_refresh_plugin and not self._plugin_background_cache_refresh_disabled(live_refresh_plugin):
                logger.info("Live plugin cache refresh due after playlist display tick.")
                only_plugin_id = live_refresh_plugin.plugin_id
        self._start_due_plugin_cache_refresh(
            playlist,
            current_dt,
            skip_plugin_instance=displayed_plugin_instance if force else None,
            displayed_plugin_instance=displayed_plugin_instance,
            force=force,
            only_plugin_id=only_plugin_id,
        )

    def _config_float(self, key, default):
        raw_value = self.device_config.get_config(key, default=default)
        if isinstance(raw_value, bool):
            return float(default)
        try:
            return float(raw_value)
        except (TypeError, ValueError, OverflowError):
            return float(default)

    def _read_memory_stats(self):
        try:
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
        except Exception:
            logger.exception("Could not read system memory stats.")
            return None
        return {
            "available_mb": memory.available / (1024 * 1024),
            "memory_percent": getattr(memory, "percent", 0.0),
            "swap_percent": getattr(swap, "percent", 0.0),
        }

    def _read_process_memory_stats(self):
        try:
            memory_info = psutil.Process(os.getpid()).memory_info()
        except Exception:
            logger.debug("Could not read process memory stats.", exc_info=True)
            return {"rss_mb": None, "hwm_mb": None}

        rss_bytes = getattr(memory_info, "rss", None)
        hwm_bytes = getattr(memory_info, "peak_wset", None)
        if hwm_bytes is None and os.name == "posix":
            try:
                with open("/proc/self/status", "r", encoding="ascii") as handle:
                    for line in handle:
                        if line.startswith("VmHWM:"):
                            hwm_bytes = int(line.split()[1]) * 1024
                            break
            except (OSError, ValueError, IndexError):
                logger.debug(
                    "Could not read process high-water memory.",
                    exc_info=True,
                )

        def as_mb(value):
            try:
                return float(value) / (1024 * 1024)
            except (TypeError, ValueError, OverflowError):
                return None

        return {
            "rss_mb": as_mb(rss_bytes),
            "hwm_mb": as_mb(hwm_bytes),
        }

    def _log_skipped_command_memory_maintenance(self, reason, command, skip_reason):
        if command is None:
            return
        process_memory = self._read_process_memory_stats()
        source = getattr(command, "source", None)
        intent = getattr(command, "intent", None)
        logger.info(
            "Memory maintenance skipped for command. | reason: %s | "
            "skip_reason: %s | plugin_id: %s | source: %s | intent: %s | "
            "process_rss_mb: %s | process_hwm_mb: %s",
            reason,
            skip_reason,
            getattr(command, "plugin_id", None),
            getattr(source, "value", source),
            getattr(intent, "value", intent),
            (
                None
                if process_memory["rss_mb"] is None
                else round(process_memory["rss_mb"], 1)
            ),
            (
                None
                if process_memory["hwm_mb"] is None
                else round(process_memory["hwm_mb"], 1)
            ),
        )

    def _run_memory_maintenance(self, reason, force=False, *, command=None):
        interval_seconds = max(0.0, self._config_float(
            "memory_maintenance_interval_seconds",
            DEFAULT_MEMORY_MAINTENANCE_INTERVAL_SECONDS,
        ))
        if interval_seconds <= 0 and not force:
            self._log_skipped_command_memory_maintenance(
                reason,
                command,
                "disabled",
            )
            return None

        now = time.monotonic()
        if (
            not force
            and self._last_memory_maintenance_monotonic
            and now - self._last_memory_maintenance_monotonic < interval_seconds
        ):
            self._log_skipped_command_memory_maintenance(
                reason,
                command,
                "interval",
            )
            return None
        self._last_memory_maintenance_monotonic = now

        before = self._read_memory_stats()
        collected_objects = 0
        try:
            collected_objects = gc.collect()
        except Exception:
            logger.exception("Python garbage collection failed during memory maintenance.")
        malloc_trimmed = self._malloc_trim()
        after = self._read_memory_stats()
        process_memory = self._read_process_memory_stats()
        source = getattr(command, "source", None)
        intent = getattr(command, "intent", None)
        logger.info(
            "Memory maintenance completed. | reason: %s | plugin_id: %s | "
            "source: %s | intent: %s | process_rss_mb: %s | "
            "process_hwm_mb: %s | collected_objects: %s | "
            "malloc_trim: %s | available_mb_before: %s | available_mb_after: %s | "
            "swap_percent_after: %s",
            reason,
            getattr(command, "plugin_id", None),
            getattr(source, "value", source),
            getattr(intent, "value", intent),
            (
                None
                if process_memory["rss_mb"] is None
                else round(process_memory["rss_mb"], 1)
            ),
            (
                None
                if process_memory["hwm_mb"] is None
                else round(process_memory["hwm_mb"], 1)
            ),
            collected_objects,
            malloc_trimmed,
            None if before is None else round(before.get("available_mb", 0.0), 1),
            None if after is None else round(after.get("available_mb", 0.0), 1),
            None if after is None else round(after.get("swap_percent", 0.0), 1),
        )
        return {
            "collected_objects": collected_objects,
            "malloc_trim": malloc_trimmed,
            "before": before,
            "after": after,
        }

    def _malloc_trim(self):
        if os.name != "posix":
            return False
        try:
            if self._libc is None:
                self._libc = ctypes.CDLL("libc.so.6")
            malloc_trim = getattr(self._libc, "malloc_trim", None)
            if malloc_trim is None:
                return False
            return bool(malloc_trim(0))
        except Exception:
            logger.debug("malloc_trim is not available on this platform.", exc_info=True)
            return False

    def _memory_watchdog_state_path(self):
        return os.path.join(self.device_config.plugin_image_dir, ".memory_watchdog_last_restart")

    def _read_memory_watchdog_last_restart_epoch(self):
        try:
            with open(self._memory_watchdog_state_path(), "r", encoding="utf-8") as handle:
                return float(handle.read().strip() or "0")
        except FileNotFoundError:
            return 0.0
        except Exception:
            logger.warning("Could not read memory watchdog restart state.", exc_info=True)
            return 0.0

    def _write_memory_watchdog_last_restart_epoch(self, value):
        path = self._memory_watchdog_state_path()
        tmp_path = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(str(float(value)))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except Exception:
            logger.warning(
                "Could not atomically write memory watchdog restart state; falling back to direct write.",
                exc_info=True,
            )
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(str(float(value)))
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                logger.warning("Could not write memory watchdog restart state.", exc_info=True)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def _memory_watchdog_should_restart(self):
        watchdog_enabled = self.device_config.get_config("memory_watchdog_enabled", default=True)
        if not _setting_enabled(watchdog_enabled):
            self._memory_watchdog_pressure_episode_active = False
            self._memory_watchdog_pressure_since_monotonic = None
            self._memory_watchdog_next_check_seconds = None
            return False

        stats = self._read_memory_stats()
        if stats is None:
            self._memory_watchdog_pressure_episode_active = False
            self._memory_watchdog_pressure_since_monotonic = None
            self._memory_watchdog_next_check_seconds = None
            return False

        min_available_mb = max(0.0, self._config_float(
            "memory_watchdog_min_available_mb",
            DEFAULT_MEMORY_WATCHDOG_MIN_AVAILABLE_MB,
        ))
        max_swap_percent = self._config_float(
            "memory_watchdog_max_swap_percent",
            DEFAULT_MEMORY_WATCHDOG_MAX_SWAP_PERCENT,
        )
        under_pressure = (
            stats["available_mb"] < min_available_mb
            or stats["swap_percent"] >= max_swap_percent
        )
        if not under_pressure:
            self._memory_watchdog_pressure_episode_active = False
            self._memory_watchdog_pressure_since_monotonic = None
            self._memory_watchdog_next_check_seconds = None
            return False

        if not self._memory_watchdog_pressure_episode_active:
            self._memory_watchdog_pressure_episode_active = True
            self._run_memory_maintenance(
                "memory-watchdog-pressure",
                force=True,
            )
            stats = self._read_memory_stats()
            if stats is None:
                self._memory_watchdog_pressure_episode_active = False
                self._memory_watchdog_pressure_since_monotonic = None
                self._memory_watchdog_next_check_seconds = None
                return False
            under_pressure = (
                stats["available_mb"] < min_available_mb
                or stats["swap_percent"] >= max_swap_percent
            )
            if not under_pressure:
                self._memory_watchdog_pressure_episode_active = False
                self._memory_watchdog_pressure_since_monotonic = None
                self._memory_watchdog_next_check_seconds = None
                return False

        now_monotonic = time.monotonic()
        low_memory = stats["available_mb"] < min_available_mb
        if not low_memory:
            confirmation_max_available_mb = max(0.0, self._config_float(
                "memory_watchdog_confirmation_max_available_mb",
                DEFAULT_MEMORY_WATCHDOG_CONFIRMATION_MAX_AVAILABLE_MB,
            ))
            sustained_dual_pressure = (
                stats["available_mb"] < confirmation_max_available_mb
                and stats["swap_percent"] >= max_swap_percent
            )
            if not sustained_dual_pressure:
                self._memory_watchdog_pressure_since_monotonic = None
                self._memory_watchdog_next_check_seconds = None
                return False
            confirmation_seconds = max(0.0, self._config_float(
                "memory_watchdog_pressure_confirmation_seconds",
                DEFAULT_MEMORY_WATCHDOG_PRESSURE_CONFIRMATION_SECONDS,
            ))
            pressure_since = self._memory_watchdog_pressure_since_monotonic
            if pressure_since is None:
                self._memory_watchdog_pressure_since_monotonic = now_monotonic
                pressure_since = now_monotonic
            confirmation_remaining = confirmation_seconds - (
                now_monotonic - pressure_since
            )
            if confirmation_remaining > 0:
                self._memory_watchdog_next_check_seconds = confirmation_remaining
                return False
        self._memory_watchdog_next_check_seconds = None
        now_epoch = time.time()
        min_interval_seconds = max(0.0, self._config_float(
            "memory_watchdog_restart_min_interval_seconds",
            DEFAULT_MEMORY_WATCHDOG_RESTART_MIN_INTERVAL_SECONDS,
        ))
        if (
            self._last_memory_pressure_restart_monotonic
            and now_monotonic - self._last_memory_pressure_restart_monotonic < min_interval_seconds
        ):
            return False
        last_restart_epoch = self._read_memory_watchdog_last_restart_epoch()
        if last_restart_epoch and now_epoch - last_restart_epoch < min_interval_seconds:
            return False

        self._last_memory_pressure_restart_monotonic = now_monotonic
        self._memory_watchdog_pressure_since_monotonic = None
        self._write_memory_watchdog_last_restart_epoch(now_epoch)
        self._restart_process_for_memory_pressure(stats, min_available_mb, max_swap_percent)
        return True

    def _restart_process_for_memory_pressure(self, stats, min_available_mb, max_swap_percent):
        logger.error(
            "Requesting staged InkyPi restart due to memory pressure. | available_mb: %.1f | "
            "min_available_mb: %.1f | swap_percent: %.1f | max_swap_percent: %.1f",
            stats["available_mb"],
            min_available_mb,
            stats["swap_percent"],
            max_swap_percent,
        )
        self._restart_request = {
            "reason": "memory_pressure",
            "available_mb": stats["available_mb"],
            "min_available_mb": min_available_mb,
            "swap_percent": stats["swap_percent"],
            "max_swap_percent": max_swap_percent,
        }
        self.refresh_queue.wake()

    def _background_cache_refresh_max_per_pass(self):
        raw_value = self.device_config.get_config(
            "background_cache_refresh_max_per_pass",
            default=DEFAULT_BACKGROUND_CACHE_REFRESH_MAX_PER_PASS,
        )
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = DEFAULT_BACKGROUND_CACHE_REFRESH_MAX_PER_PASS
        if value > 1:
            logger.info(
                "Clamping legacy background cache refresh pass limit to one. | configured: %s",
                value,
            )
        return 1

    def _cache_refresh_under_resource_pressure(self, allow_high_swap=False):
        min_available_mb = self.device_config.get_config(
            "background_cache_refresh_min_available_mb",
            default=DEFAULT_BACKGROUND_CACHE_REFRESH_MIN_AVAILABLE_MB,
        )
        max_swap_percent = self.device_config.get_config(
            "background_cache_refresh_max_swap_percent",
            default=DEFAULT_BACKGROUND_CACHE_REFRESH_MAX_SWAP_PERCENT,
        )
        try:
            min_available_mb = float(min_available_mb)
        except (TypeError, ValueError):
            min_available_mb = DEFAULT_BACKGROUND_CACHE_REFRESH_MIN_AVAILABLE_MB
        try:
            max_swap_percent = float(max_swap_percent)
        except (TypeError, ValueError):
            max_swap_percent = DEFAULT_BACKGROUND_CACHE_REFRESH_MAX_SWAP_PERCENT

        try:
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
        except Exception:
            logger.exception("Could not read system memory pressure for cache refresh.")
            return False

        available_mb = memory.available / (1024 * 1024)
        under_pressure = available_mb < min_available_mb
        if under_pressure:
            now = time.monotonic()
            if now - self._last_cache_pressure_log_monotonic >= 60:
                logger.warning(
                    "Skipping background cache refresh due to resource pressure. | "
                    "available_mb: %.1f | min_available_mb: %.1f | "
                    "swap_percent: %.1f | max_swap_percent: %.1f",
                    available_mb,
                    min_available_mb,
                    swap.percent,
                    max_swap_percent,
                )
                self._last_cache_pressure_log_monotonic = now
        return under_pressure

    def _refresh_due_plugin_instances(self, playlist, current_dt, skip_plugin_instance=None, displayed_plugin_instance=None, force=False, only_plugin_id=None, max_updates=None):
        """Compatibility helper for direct callers and legacy unit tests.

        The production scheduler does not call this synchronous path; it emits
        one immutable CACHE_REFRESH command per due instance instead.
        """
        if self.manual_update_in_progress():
            logger.info("Due plugin cache refresh pass skipped while manual update is running.")
            return

        updated = False
        attempted_updates = 0
        candidates = []
        for plugin_instance in list(playlist.plugins):
            if only_plugin_id and plugin_instance.plugin_id != only_plugin_id:
                continue
            if self._is_same_plugin_instance(plugin_instance, skip_plugin_instance):
                continue
            if self._snapshot_retry_delayed(plugin_instance, current_dt):
                continue

            plugin_image_path = os.path.join(
                self.device_config.plugin_image_dir,
                plugin_instance.get_image_path(),
            )
            image_missing = not os.path.exists(plugin_image_path)
            refresh_on_display = (
                _display_triggered_refresh_enabled(self.device_config)
                and self._is_same_plugin_instance(plugin_instance, displayed_plugin_instance)
                and self._plugin_wants_refresh_on_display(plugin_instance)
            )
            live_refresh_due = self._plugin_live_refresh_due(plugin_instance, current_dt)
            refresh_due = plugin_instance.should_refresh(current_dt)
            background_cache_disabled = self._plugin_background_cache_refresh_disabled(plugin_instance)
            if background_cache_disabled:
                logger.info(
                    "Skipping background cache refresh for display-only plugin. | "
                    f"plugin_instance: '{plugin_instance.name}'"
                )
                continue
            if not force and not image_missing and not refresh_due and not refresh_on_display and not live_refresh_due:
                continue

            candidates.append((
                self._cache_refresh_candidate_sort_key(
                    plugin_instance,
                    current_dt,
                    image_missing=image_missing,
                    refresh_on_display=refresh_on_display,
                    live_refresh_due=live_refresh_due,
                    displayed_plugin_instance=displayed_plugin_instance,
                ),
                plugin_instance,
                plugin_image_path,
                image_missing,
                live_refresh_due,
            ))

        for _, plugin_instance, plugin_image_path, image_missing, live_refresh_due in sorted(candidates, key=lambda item: item[0]):
            if self.manual_update_in_progress():
                logger.info("Due plugin cache refresh pass stopped while manual update is running.")
                break
            if max_updates is not None and attempted_updates >= max_updates:
                logger.info(
                    "Due plugin cache refresh pass limit reached. | "
                    f"max_updates: {max_updates}"
                )
                break
            if self._cache_refresh_under_resource_pressure():
                logger.info(
                    "Due plugin cache refresh pass stopped due to resource pressure before generation. | "
                    f"plugin_instance: '{plugin_instance.name}'"
                )
                break
            attempted_updates += 1

            try:
                self.runtime_state.record_attempt(
                    plugin_instance.instance_uuid,
                    current_dt.isoformat(),
                )
            except Exception:
                logger.exception(
                    "Runtime background attempt state could not be recorded. | "
                    "plugin_instance: '%s'",
                    plugin_instance.name,
                )

            try:
                if image_missing:
                    logger.info(
                        "Plugin instance image missing during cache refresh. | "
                        f"plugin_instance: '{plugin_instance.name}'"
                    )
                if live_refresh_due and not force and not image_missing:
                    logger.info(
                        "Live plugin cache refresh due. | "
                        f"plugin_instance: '{plugin_instance.name}'"
                    )
                logger.info(
                    "Refreshing due plugin instance cache. | "
                    f"plugin_instance: '{plugin_instance.name}'"
                )
                plugin_config = self.device_config.get_plugin(plugin_instance.plugin_id)
                if plugin_config is None:
                    logger.error(
                        f"Plugin config not found for '{plugin_instance.plugin_id}' "
                        f"during cache refresh."
                    )
                    continue

                plugin = get_plugin_instance(plugin_config)
                image = plugin.render_themed_image(
                    _settings_with_force_refresh(plugin_instance.settings, force),
                    self.device_config,
                )
                if _image_allows_cache(image):
                    _save_image_atomic(image, plugin_image_path)
                    plugin_instance.latest_refresh_time = current_dt.isoformat()
                    self._record_runtime_success(
                        plugin_instance.instance_uuid,
                        current_dt.isoformat(),
                    )
                    self.retry_registry.mark_success(plugin_instance.instance_uuid)
                    updated = True
                else:
                    logger.warning(
                        "Plugin instance generated a non-cacheable image; leaving previous cache in place. | "
                        f"plugin_instance: '{plugin_instance.name}'"
                    )
            except Exception as error:
                logger.exception(
                    "Exception during due plugin instance cache refresh. | "
                    f"plugin_instance: '{plugin_instance.name}'"
                )
                try:
                    delay = self.retry_registry.mark_failure(
                        plugin_instance.instance_uuid,
                        self._clock(),
                    )
                    self.runtime_state.record_failure(
                        plugin_instance.instance_uuid,
                        current_dt.isoformat(),
                        error,
                        (current_dt + timedelta(seconds=delay)).isoformat(),
                    )
                except Exception:
                    logger.exception(
                        "Runtime background failure state could not be recorded. | "
                        "plugin_instance: '%s'",
                        plugin_instance.name,
                    )
            finally:
                self._run_memory_maintenance("background-cache")

        if updated:
            self._write_device_config()

    def _plugin_instance_cache_refresh_due(self, plugin_instance, current_dt, displayed_plugin_instance=None):
        if plugin_instance is None:
            return False
        plugin_image_path = os.path.join(
            self.device_config.plugin_image_dir,
            plugin_instance.get_image_path(),
        )
        if not os.path.exists(plugin_image_path):
            return True
        if plugin_instance.should_refresh(current_dt):
            return True
        if (
            _display_triggered_refresh_enabled(self.device_config)
            and self._is_same_plugin_instance(plugin_instance, displayed_plugin_instance)
            and self._plugin_wants_refresh_on_display(plugin_instance)
        ):
            return True
        return self._plugin_live_refresh_due(plugin_instance, current_dt)

    def _plugin_instance_background_cache_refresh_due(self, plugin_instance, current_dt, displayed_plugin_instance=None):
        if plugin_instance is None or self._plugin_background_cache_refresh_disabled(plugin_instance):
            return False
        return self._plugin_instance_cache_refresh_due(
            plugin_instance,
            current_dt,
            displayed_plugin_instance=displayed_plugin_instance,
        )

    def _playlist_has_cache_refresh_due(self, playlist, current_dt):
        return any(
            self._plugin_instance_cache_refresh_due(plugin_instance, current_dt)
            for plugin_instance in list(playlist.plugins)
        )

    def _playlist_has_background_cache_refresh_due(self, playlist, current_dt, displayed_plugin_instance=None):
        return any(
            self._plugin_instance_background_cache_refresh_due(
                plugin_instance,
                current_dt,
                displayed_plugin_instance=displayed_plugin_instance,
            )
            for plugin_instance in list(playlist.plugins)
        )

    def _cache_refresh_candidate_sort_key(
        self,
        plugin_instance,
        current_dt,
        image_missing=False,
        refresh_on_display=False,
        live_refresh_due=False,
        displayed_plugin_instance=None,
    ):
        priority = 0
        if image_missing:
            priority += 4
        if self._is_same_plugin_instance(plugin_instance, displayed_plugin_instance):
            priority += 3
        if refresh_on_display:
            priority += 2
        if live_refresh_due:
            priority += 1

        latest_refresh = plugin_instance.get_latest_refresh_dt()
        if latest_refresh is None:
            latest_timestamp = float("-inf")
        else:
            latest_timestamp = plugin_instance.align_datetime_tz(latest_refresh, current_dt).timestamp()

        return (-priority, latest_timestamp, plugin_instance.plugin_id, plugin_instance.name)

    def _get_plugin_for_instance(self, plugin_instance, *, require_live_refresh=False):
        plugin_config = self.device_config.get_plugin(plugin_instance.plugin_id)
        if plugin_config is None:
            logger.error(f"Plugin config not found for '{plugin_instance.plugin_id}'.")
            return None
        if require_live_refresh and not plugin_supports_live_refresh(plugin_config):
            return None
        try:
            return get_plugin_instance(plugin_config)
        except Exception:
            logger.exception(f"Plugin '{plugin_instance.plugin_id}' could not be loaded.")
            return None

    def _plugin_wants_refresh_on_display(self, plugin_instance, plugin=None):
        plugin = plugin or self._get_plugin_for_instance(plugin_instance)
        if plugin is None:
            return False
        hook = getattr(plugin, "wants_refresh_on_display", None)
        if not callable(hook):
            return False
        try:
            return bool(hook(plugin_instance.settings or {}))
        except PluginSettingError:
            raise
        except Exception:
            logger.exception(f"Plugin '{plugin_instance.plugin_id}' refresh-on-display hook failed.")
            return False

    def _plugin_live_refresh_state(self, plugin_instance, current_dt, plugin=None):
        plugin = plugin or self._get_plugin_for_instance(
            plugin_instance,
            require_live_refresh=True,
        )
        if plugin is None:
            return None
        return _plugin_live_refresh_state(
            plugin,
            plugin_instance.settings or {},
            current_dt,
            plugin_id=plugin_instance.plugin_id,
        )

    def _plugin_background_cache_refresh_disabled(self, plugin_instance):
        plugin_id = str(getattr(plugin_instance, "plugin_id", "") or "").strip()
        if plugin_id != "sports_dashboard":
            return False
        settings = getattr(plugin_instance, "settings", None) or {}
        return not _setting_enabled(settings.get("backgroundCacheRefreshEnabled", False))

    def _plugin_live_refresh_due(self, plugin_instance, current_dt):
        state = self._plugin_live_refresh_state(plugin_instance, current_dt)
        if not state:
            return False
        latest_refresh_dt = plugin_instance.get_latest_refresh_dt()
        if not latest_refresh_dt:
            return True
        latest_refresh_dt = self._align_datetime_tz(latest_refresh_dt, current_dt)
        return (current_dt - latest_refresh_dt) >= timedelta(seconds=state["interval_seconds"])

    def _live_refresh_wait_seconds(self, current_dt):
        try:
            playlist_manager = self.device_config.get_playlist_manager()
            playlist = playlist_manager.determine_active_playlist(current_dt)
        except Exception:
            return None
        if not playlist:
            return None

        waits = []
        for plugin_instance in list(getattr(playlist, "plugins", []) or []):
            state = self._plugin_live_refresh_state(plugin_instance, current_dt)
            if not state:
                continue
            latest_refresh_dt = plugin_instance.get_latest_refresh_dt()
            if not latest_refresh_dt:
                waits.append(0)
                continue
            latest_refresh_dt = self._align_datetime_tz(latest_refresh_dt, current_dt)
            elapsed = (current_dt - latest_refresh_dt).total_seconds()
            waits.append(state["interval_seconds"] - elapsed)
        if not waits:
            return None
        return min(waits)

    def _playlist_has_live_refresh_due(self, playlist, current_dt):
        return self._playlist_live_refresh_due_plugin_id(playlist, current_dt) is not None

    def _playlist_live_refresh_due_plugin_instance(self, playlist, current_dt):
        for plugin_instance in list(getattr(playlist, "plugins", []) or []):
            if self._plugin_live_refresh_due(plugin_instance, current_dt):
                return plugin_instance
        return None

    def _playlist_live_refresh_due_plugin_id(self, playlist, current_dt):
        plugin_instance = self._playlist_live_refresh_due_plugin_instance(playlist, current_dt)
        return None if plugin_instance is None else plugin_instance.plugin_id

    @staticmethod
    def _parse_iso_datetime(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _align_datetime_tz(value, reference):
        if value.tzinfo is None and reference.tzinfo is not None:
            localize = getattr(reference.tzinfo, "localize", None)
            return localize(value) if localize else value.replace(tzinfo=reference.tzinfo)
        if value.tzinfo is not None and reference.tzinfo is not None:
            return value.astimezone(reference.tzinfo)
        if value.tzinfo is not None and reference.tzinfo is None:
            return value.replace(tzinfo=None)
        return value

    def _is_same_plugin_instance(self, plugin_instance, other_plugin_instance):
        if not plugin_instance or not other_plugin_instance:
            return False
        return (
            plugin_instance.plugin_id == other_plugin_instance.plugin_id
            and plugin_instance.name == other_plugin_instance.name
        )

    def _display_target_changed(self, latest_refresh_info, next_refresh_info):
        if not latest_refresh_info:
            return True
        return (
            latest_refresh_info.refresh_type != next_refresh_info.get("refresh_type")
            or latest_refresh_info.plugin_id != next_refresh_info.get("plugin_id")
            or latest_refresh_info.playlist != next_refresh_info.get("playlist")
            or latest_refresh_info.plugin_instance != next_refresh_info.get("plugin_instance")
        )
    
    def log_system_stats(self):
        metrics = {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent,
            'load_avg_1_5_15': os.getloadavg(),
            'swap_percent': psutil.swap_memory().percent,
            'net_io': {
                'bytes_sent': psutil.net_io_counters().bytes_sent,
                'bytes_recv': psutil.net_io_counters().bytes_recv
            }
        }

        logger.info(f"System Stats: {metrics}")

class RefreshAction:
    """Base class for a refresh action. Subclasses should override the methods below."""
    
    def refresh(self, plugin, device_config, current_dt):
        """Perform a refresh operation and return the updated image."""
        raise NotImplementedError("Subclasses must implement the refresh method.")
    
    def get_refresh_info(self):
        """Return refresh metadata as a dictionary."""
        raise NotImplementedError("Subclasses must implement the get_refresh_info method.")
    
    def get_plugin_id(self):
        """Return the plugin ID associated with this refresh."""
        raise NotImplementedError("Subclasses must implement the get_plugin_id method.")

class ManualRefresh(RefreshAction):
    """Performs a manual refresh based on a plugin's ID and its associated settings.
    
    Attributes:
        plugin_id (str): The ID of the plugin to refresh.
        plugin_settings (dict): The settings for the manual refresh.
    """

    def __init__(self, plugin_id: str, plugin_settings: dict):
        self.plugin_id = plugin_id
        self.plugin_settings = plugin_settings

    def execute(self, plugin, device_config, current_dt: datetime):
        """Performs a manual refresh using the stored plugin ID and settings."""
        return plugin.render_themed_image(
            _settings_with_force_refresh(
                self.plugin_settings,
                True,
                display_render=True,
            ),
            device_config,
        )

    def get_refresh_info(self):
        """Return refresh metadata as a dictionary."""
        return {"refresh_type": "Manual Update", "plugin_id": self.plugin_id}

    def get_plugin_id(self):
        """Return the plugin ID associated with this refresh."""
        return self.plugin_id

class PlaylistRefresh(RefreshAction):
    """Performs a refresh using a plugin instance within a playlist context.

    Attributes:
        playlist: The playlist object associated with the refresh.
        plugin_instance: The plugin instance to refresh.
    """

    def __init__(self, playlist, plugin_instance, force=False, display_cached_only=False):
        self.playlist = playlist
        self.plugin_instance = plugin_instance
        self.force = force
        self.display_cached_only = display_cached_only

    def get_refresh_info(self):
        """Return refresh metadata as a dictionary."""
        return {
            "refresh_type": "Playlist",
            "playlist": self.playlist.name,
            "plugin_id": self.plugin_instance.plugin_id,
            "plugin_instance": self.plugin_instance.name
        }

    def get_plugin_id(self):
        """Return the plugin ID associated with this refresh."""
        return self.plugin_instance.plugin_id

    def execute(self, plugin, device_config, current_dt: datetime):
        """Performs a refresh for the specified plugin instance within its playlist context."""
        # Determine the file path for the plugin's image
        plugin_image_path = os.path.join(device_config.plugin_image_dir, self.plugin_instance.get_image_path())
        image_missing = not os.path.exists(plugin_image_path)
        if self.display_cached_only and not self.force:
            if not image_missing:
                logger.info(
                    "Using cached plugin instance image for scheduled display. | "
                    f"plugin_instance: {self.plugin_instance.name}."
                )
                try:
                    return _load_image_copy(plugin_image_path)
                except Exception:
                    logger.exception(
                        "Cached plugin image could not be loaded for scheduled display; using placeholder. | "
                        f"plugin_instance: {self.plugin_instance.name}."
                    )
            logger.warning(
                "Plugin instance image unavailable for scheduled display; using placeholder. | "
                f"plugin_instance: '{self.plugin_instance.name}'"
            )
            return self._placeholder_image(device_config)

        refresh_on_display_hook = getattr(plugin, "wants_refresh_on_display", None)
        refresh_on_display = (
            bool(refresh_on_display_hook(self.plugin_instance.settings or {}))
            if callable(refresh_on_display_hook)
            else False
        )
        live_refresh_due = _plugin_live_refresh_due_for_instance(plugin, self.plugin_instance, current_dt)
        refresh_due = self.plugin_instance.should_refresh(current_dt)
        refresh_due_on_display = refresh_due and self.plugin_instance.plugin_id == "sports_dashboard"

        if self.display_cached_only and not self.force and not refresh_on_display and not live_refresh_due and not refresh_due_on_display:
            if not image_missing:
                logger.info(
                    "Using cached plugin instance image for scheduled display. | "
                    f"plugin_instance: {self.plugin_instance.name}."
                )
                try:
                    return _load_image_copy(plugin_image_path)
                except Exception:
                    logger.exception(
                        "Cached plugin image could not be loaded; refreshing synchronously. | "
                        f"plugin_instance: {self.plugin_instance.name}."
                    )

            try:
                logger.info(
                    "Plugin instance image unavailable for scheduled display; refreshing now. | "
                    f"plugin_instance: '{self.plugin_instance.name}'"
                )
                image = plugin.render_themed_image(
                    _settings_with_force_refresh(
                        self.plugin_instance.settings,
                        self.force,
                        display_render=True,
                    ),
                    device_config,
                )
                if _image_allows_cache(image):
                    _save_image_atomic(image, plugin_image_path)
                    self.plugin_instance.latest_refresh_time = current_dt.isoformat()
                    return image
                logger.warning(
                    "Plugin instance generated a non-cacheable image for scheduled display; using placeholder. | "
                    f"plugin_instance: '{self.plugin_instance.name}'"
                )
                return self._placeholder_image(device_config)
            except Exception:
                logger.exception(
                    "Plugin instance could not refresh for scheduled display; using placeholder. | "
                    f"plugin_instance: '{self.plugin_instance.name}'"
                )
                return self._placeholder_image(device_config)

        # Check if a refresh is needed based on the plugin instance's criteria
        if refresh_due or self.force or image_missing or refresh_on_display or live_refresh_due:
            if image_missing:
                logger.info(f"Plugin instance image missing, refreshing. | plugin_instance: '{self.plugin_instance.name}'")
            if refresh_on_display and not self.force and not image_missing:
                logger.info(f"Refreshing plugin instance on display. | plugin_instance: '{self.plugin_instance.name}'")
            elif live_refresh_due and not self.force and not image_missing:
                logger.info(f"Refreshing live plugin instance on display. | plugin_instance: '{self.plugin_instance.name}'")
            else:
                logger.info(f"Refreshing plugin instance. | plugin_instance: '{self.plugin_instance.name}'")
            # Generate a new image
            image = plugin.render_themed_image(
                _settings_with_force_refresh(
                    self.plugin_instance.settings,
                    self.force,
                    display_render=True,
                ),
                device_config,
            )
            if _image_allows_cache(image):
                _save_image_atomic(image, plugin_image_path)
                self.plugin_instance.latest_refresh_time = current_dt.isoformat()
            else:
                logger.warning(
                    "Plugin instance generated a non-cacheable image; leaving previous cache in place. | "
                    f"plugin_instance: '{self.plugin_instance.name}'"
                )
                if not image_missing and os.path.exists(plugin_image_path):
                    try:
                        return _load_image_copy(plugin_image_path)
                    except Exception:
                        logger.exception(
                            "Previous plugin cache could not be loaded after non-cacheable refresh. | "
                            f"plugin_instance: '{self.plugin_instance.name}'"
                        )
        else:
            logger.info(f"Not time to refresh plugin instance, using latest image. | plugin_instance: {self.plugin_instance.name}.")
            # Load the existing image from disk
            image = _load_image_copy(plugin_image_path)

        return image

    def _placeholder_image(self, device_config):
        dimensions = self._display_dimensions(device_config)
        width, height = dimensions
        image = Image.new("RGB", dimensions, "white")
        draw = ImageDraw.Draw(image)
        border = max(12, min(width, height) // 24)
        draw.rectangle((border, border, width - border, height - border), outline="black", width=3)
        draw.line((border, height // 2, width - border, height // 2), fill=(180, 180, 180), width=2)

        title_font = self._font(max(20, min(width, height) // 12), bold=True)
        subtitle_font = self._font(max(12, min(width, height) // 28))
        title = "CACHE PENDING"
        subtitle = f"{self.plugin_instance.name} will refresh in background"
        subtitle = self._fit_text(draw, subtitle, subtitle_font, width - (border * 3))
        self._draw_centered(draw, title, width // 2, height // 2 - 28, title_font, "black")
        self._draw_centered(draw, subtitle, width // 2, height // 2 + 24, subtitle_font, (70, 70, 70))
        return image

    def _display_dimensions(self, device_config):
        if hasattr(device_config, "get_resolution"):
            try:
                return tuple(int(value) for value in device_config.get_resolution())
            except Exception:
                logger.exception("Could not read display resolution from device config.")

        resolution = None
        if hasattr(device_config, "get_config"):
            resolution = device_config.get_config("resolution", default=None)
        if not resolution:
            resolution = (800, 480)
        return tuple(int(value) for value in resolution)

    def _font(self, size, bold=False):
        return get_base_ui_font(int(size), bold=bool(bold))

    def _draw_centered(self, draw, text, x, y, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((x - (bbox[2] - bbox[0]) // 2, y - (bbox[3] - bbox[1]) // 2), text, font=font, fill=fill)

    def _fit_text(self, draw, text, font, max_width):
        if draw.textlength(text, font=font) <= max_width:
            return text
        candidate = text
        while candidate and draw.textlength(candidate + "...", font=font) > max_width:
            candidate = candidate[:-1].rstrip()
        return f"{candidate}..." if candidate else text[:1]
