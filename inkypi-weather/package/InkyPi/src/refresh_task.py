import threading
import time
import os
import logging
import ctypes
import gc
import hashlib
import psutil
import pytz
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from plugins.plugin_registry import (
    get_plugin_instance,
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
from utils.theme_utils import get_theme_context, resolve_plugin_theme
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
    thaw_payload,
)
from runtime.cache_catalog import (
    CacheCatalog,
    DisplayCacheCandidate,
    authoritative_cache_path,
)
from runtime.presentation_cache import (
    PreparedPresentationCandidate,
    PresentationCache,
    prepared_presentation_path,
)
from runtime.refresh_queue import QueueEntry, RefreshQueue
from runtime.refresh_policy import (
    AdmissionState,
    DueCandidate,
    DueReason,
    ResourceSample,
    ResourceThresholds,
    ResourceTier,
    classify_resource_tier,
    choose_refresh_candidate,
    evaluate_data_due,
    evaluate_presentation_due,
)
from runtime.long_task_executor import InstanceIdentity, bind_long_task_runtime
from runtime.render_arbiter import RenderArbiter
from runtime.runtime_state import (
    InstanceRuntimeState,
    LastGoodCacheState,
    PresentationCommitReceipt,
    PresentationRequestState,
    RefreshLane,
    RuntimeStateStore,
)
from runtime.scheduler_state import LifecycleController, RetryRegistry, SchedulerState
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)
DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS = 5 * 60
DEFAULT_MANUAL_UPDATE_TIMEOUT_SECONDS = 180
DEFAULT_MANUAL_UPDATE_JOB_RETENTION = 50
DEFAULT_BACKGROUND_CACHE_REFRESH_MAX_PER_PASS = 2
DEFAULT_BACKGROUND_CACHE_REFRESH_MIN_AVAILABLE_MB = 150
DEFAULT_BACKGROUND_CACHE_REFRESH_MAX_SWAP_PERCENT = 70
DEFAULT_MEMORY_MAINTENANCE_INTERVAL_SECONDS = 60
DEFAULT_MEMORY_WATCHDOG_MIN_AVAILABLE_MB = 70
DEFAULT_MEMORY_WATCHDOG_MAX_SWAP_PERCENT = 75
DEFAULT_MEMORY_WATCHDOG_RESTART_MIN_INTERVAL_SECONDS = 30 * 60
DEFAULT_THEME_REFRESH_RETRY_COOLDOWN_SECONDS = 10 * 60
DEFAULT_DISPLAY_REFRESH_MIN_AVAILABLE_MB = 150
DEFAULT_DISPLAY_REFRESH_MAX_SWAP_PERCENT = 30
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


def _setting_enabled(value):
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


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
    ):
        self.device_config = device_config
        self.display_manager = display_manager
        self._clock = clock
        self._wall_clock = wall_clock

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
        self._resource_tier = None
        self._due_counts = {lane.value: 0 for lane in RefreshLane}
        self._oldest_data_overdue_seconds = None
        self._display_transactions_enabled = False
        bind_runtime_state = getattr(display_manager, "bind_runtime_state", None)
        if callable(bind_runtime_state):
            bind_runtime_state(self.runtime_state)
            self._display_transactions_enabled = True

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
        self._libc = None
        self._restart_request = None

    def _config_int(self, key, default, minimum, maximum):
        try:
            value = int(self.device_config.get_config(key, default=default))
        except (TypeError, ValueError, OverflowError):
            value = default
        return max(minimum, min(maximum, value))

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

    def refresh_health_snapshot(self):
        """Return aggregate refresh diagnostics without instance-owned details."""
        tier = getattr(self._resource_tier, "value", self._resource_tier)
        return {
            "resource_tier": "unknown" if tier is None else str(tier),
            "due_counts": dict(self._due_counts),
            "oldest_data_overdue_seconds": self._oldest_data_overdue_seconds,
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
                name="inkypi-refresh-worker",
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
        self._waiting_event.set()
        try:
            self.refresh_queue.wait_for_change(token, timeout=timeout)
        finally:
            self._waiting_event.clear()
        return self.refresh_queue.take(timeout=0)

    def wait_until_waiting(self, timeout=1.0):
        return self._waiting_event.wait(timeout=max(0.0, float(timeout)))

    def _run_one_iteration_for_test(self):
        """Run one non-blocking scheduler/worker turn for deterministic tests."""
        self._schedule_if_due()
        entry = self.refresh_queue.take(timeout=0)
        if entry is not None:
            self._process_queue_entry(entry)
        return entry

    def _schedule_if_due(self):
        now = self._clock()
        scheduler = self.scheduler_state.snapshot()
        if (
            scheduler.next_attempt_monotonic is not None
            and now < scheduler.next_attempt_monotonic
        ):
            return None

        try:
            self.scheduler_state.record_attempt()
            self._attempt_count += 1
            restart_requested = self._memory_watchdog_should_restart()
            current_dt = self._get_current_datetime()
            command = self._select_prepared_display_retry_command(current_dt)
            if command is None:
                command = self._select_cached_display_command(current_dt)
            if command is not None:
                self.refresh_queue.submit(command)
            if restart_requested:
                self._resource_tier = ResourceTier.HARD
            else:
                refresh_command = self._select_independent_refresh_command(current_dt)
                if refresh_command is not None:
                    self.refresh_queue.submit(refresh_command)
            next_delay = 30.0 if restart_requested else self._scheduler_poll_seconds()
            self.scheduler_state.set_next_attempt(now + next_delay)
            return command
        except Exception as error:
            self.scheduler_state.record_failure(error)
            delay = self.retry_registry.mark_failure(RetryRegistry.GLOBAL_KEY, now)
            self.scheduler_state.set_next_attempt(now + max(30.0, delay))
            logger.exception("Scheduled refresh selection failed")
            return None

    def _scheduler_poll_seconds(self):
        interval = self._config_float(
            "plugin_cycle_interval_seconds",
            DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
        )
        if interval <= 0:
            interval = DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS
        return max(1.0, min(30.0, interval))

    def _select_scheduled_command(self, current_dt) -> RefreshCommand | None:
        """Select display work using only immutable PlaylistManager APIs."""
        manager = self.device_config.get_playlist_manager()
        latest_refresh = self.device_config.get_refresh_info()
        theme_context = get_theme_context(self.device_config, now=current_dt)
        if self._has_theme_changed(theme_context, current_dt):
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
                )
            self._persist_active_theme(theme_context, current_dt)
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
        selection = manager.select_next_active_instance(
            current_dt,
            latest_refresh=latest_refresh.get_refresh_datetime(),
            interval_seconds=interval,
        )
        if selection is not None:
            return self._playlist_command(
                selection.playlist_name,
                selection.instance,
                source=CommandSource.SCHEDULER,
                intent=RefreshIntent.DISPLAY_CACHE,
                display_cached_only=True,
                priority=50,
            )

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
                not missing_work,
                latest_timestamp,
                instance.plugin_id,
                instance.name,
                instance,
            ))

        limit = self._background_cache_refresh_max_per_pass()
        selected = sorted(candidates, key=lambda item: item[:4])[:limit]
        return tuple(
            self._playlist_command(
                active.name,
                item[4],
                source=CommandSource.BACKGROUND,
                intent=RefreshIntent.DATA_REFRESH,
                display_cached_only=False,
                priority=10,
                kind=CommandKind.CACHE_REFRESH,
                current_dt=current_dt,
            )
            for item in selected
        )

    def _active_cache_candidates(self, active, theme_context):
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
            candidate = self.cache_catalog.resolve(
                instance,
                theme_mode,
                runtime_instances.get(instance.instance_uuid, InstanceRuntimeState()),
            )
            if candidate is not None:
                candidates[instance.instance_uuid] = candidate
        return candidates

    def _select_cached_display_command(self, current_dt) -> RefreshCommand | None:
        """Select one random eligible cache without loading plugin code."""
        manager = self.device_config.get_playlist_manager()
        active = manager.snapshot_active_playlist(current_dt)
        if active is None:
            return None
        theme_context = get_theme_context(self.device_config, now=current_dt)
        if self._has_theme_changed(theme_context, current_dt):
            # The exact displayed theme refresh owns this transition.  Avoid
            # queueing an opposite-theme DISPLAY_CACHE command that could
            # absorb its cache-only follow-up and lose the pinned context.
            return None
        candidates = self._active_cache_candidates(active, theme_context)
        latest_refresh = self.device_config.get_refresh_info()
        try:
            interval = float(
                self.device_config.get_config(
                    "plugin_cycle_interval_seconds",
                    default=DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
                )
            )
        except (TypeError, ValueError, OverflowError):
            interval = DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS
        selection = manager.select_next_active_instance(
            current_dt,
            latest_refresh=latest_refresh.get_refresh_datetime(),
            interval_seconds=interval,
            eligible_instance_uuids=frozenset(candidates),
        )
        if selection is None:
            return None
        candidate = candidates.get(selection.instance.instance_uuid)
        if candidate is None or (
            candidate.structural_generation
            != selection.instance.structural_generation
            or candidate.settings_revision != selection.instance.settings_revision
        ):
            return None
        return self._playlist_command(
            selection.playlist_name,
            selection.instance,
            source=CommandSource.SCHEDULER,
            intent=RefreshIntent.DISPLAY_CACHE,
            force=False,
            display_cached_only=True,
            priority=50,
            current_dt=current_dt,
            cache_theme_mode=candidate.theme_mode,
        )

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
        if next_retry is None or next_retry > current_dt:
            return None
        plugin_config, _theme_context, theme_mode = self._latest_presentation_theme(
            instance
        )
        if (
            not plugin_supports_presentation_refresh(plugin_config)
            or request.structural_generation != instance.structural_generation
            or request.settings_revision != instance.settings_revision
            or request.prepared_theme_mode != theme_mode
        ):
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
            soft_spacing_seconds=max(
                0.0,
                self._config_float(
                    "independent_refresh_soft_spacing_seconds",
                    60.0,
                ),
            ),
        )

    def _select_independent_refresh_command(
        self,
        current_dt,
    ) -> RefreshCommand | None:
        """Admit at most one ordinary renderer command for this probe."""
        manager = self.device_config.get_playlist_manager()
        active = manager.snapshot_active_playlist(current_dt)
        theme_context = get_theme_context(self.device_config, now=current_dt)
        if active is None:
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
        data_candidates = []
        presentation_candidates = []
        for instance in active.plugins:
            runtime_instance = runtime_instances.get(
                instance.instance_uuid,
                InstanceRuntimeState(),
            )
            evaluation = evaluate_data_due(
                instance,
                runtime_instance,
                instance.instance_uuid in cache_candidates,
                current_dt,
            )
            if evaluation.invalid_fields:
                logger.warning(
                    "Ignoring invalid refresh cadence fields. | plugin_id: %s | fields: %s",
                    instance.plugin_id,
                    ",".join(evaluation.invalid_fields),
                )
            if evaluation.candidate is not None:
                data_candidates.append(evaluation.candidate)
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            if not plugin_supports_presentation_refresh(plugin_config):
                continue
            resolved_theme_context = _resolved_theme_context_for_instance(
                instance,
                plugin_config,
                self.device_config,
                current_dt=current_dt,
            )
            resolved_theme_mode = (
                resolved_theme_context.get("mode") if isinstance(resolved_theme_context, Mapping) else None
            )
            presentation = evaluate_presentation_due(
                instance,
                runtime_instance,
                instance.instance_uuid in cache_candidates,
                resolved_theme_mode,
                current_dt,
            )
            if presentation.candidate is not None:
                presentation_candidates.append(presentation.candidate)

        thresholds = self._resource_thresholds()
        tier = classify_resource_tier(self._resource_sample(), thresholds)
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
        auxiliary_candidates = list(live_candidates)
        auxiliary_candidates.extend(presentation_candidates)
        if theme_candidate is not None:
            auxiliary_candidates.append(theme_candidate)
        decision = choose_refresh_candidate(
            data_candidates,
            auxiliary_candidates,
            tier=tier,
            state=self._admission_state,
            now_monotonic=self._clock(),
            thresholds=thresholds,
        )
        self._admission_state = decision.state
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
        candidate = decision.candidate
        if candidate is None:
            return None
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
                expected_displayed_instance_uuid=candidate.instance.instance_uuid,
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

    def _live_due_candidates(self, active, runtime_instances, current_dt, tier):
        """Return exact-display live candidates admitted only in the healthy tier."""
        if tier is not ResourceTier.HEALTHY:
            return []
        displayed_uuid = self.runtime_state.snapshot().displayed_instance_uuid
        if displayed_uuid is None:
            return []
        candidates = []
        for instance in active.plugins:
            if instance.instance_uuid != displayed_uuid:
                continue
            if self._snapshot_background_cache_disabled(instance):
                continue
            plugin_config = self.device_config.get_plugin(instance.plugin_id)
            if not plugin_supports_live_refresh(plugin_config):
                continue
            live_state = self._snapshot_live_refresh_state(instance, current_dt)
            if not live_state:
                continue
            runtime = runtime_instances.get(
                instance.instance_uuid,
                InstanceRuntimeState(),
            ).live
            next_retry = self._parse_iso_datetime(runtime.next_retry_at)
            if next_retry is not None:
                next_retry = self._align_datetime_tz(next_retry, current_dt)
                if current_dt < next_retry:
                    continue
            last_success = self._parse_iso_datetime(runtime.last_success_at)
            if last_success is None:
                due_since = current_dt
            else:
                last_success = self._align_datetime_tz(last_success, current_dt)
                due_since = last_success + timedelta(
                    seconds=live_state["interval_seconds"]
                )
                if current_dt < due_since:
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
        if not self._has_theme_changed(theme_context, current_dt):
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

        state = runtime_instances.get(
            selection.instance.instance_uuid,
            InstanceRuntimeState(),
        ).theme
        next_retry = self._parse_iso_datetime(state.next_retry_at)
        if next_retry is not None:
            next_retry = self._align_datetime_tz(next_retry, current_dt)
            if current_dt < next_retry:
                return None
        last_attempt = self._parse_iso_datetime(state.last_attempt_at)
        if last_attempt is not None:
            last_attempt = self._align_datetime_tz(last_attempt, current_dt)
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
        if "backgroundCacheRefreshEnabled" not in settings:
            return False
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
        return self._snapshot_cache_path(instance)

    def compatibility_cache_path_for_snapshot(self, instance):
        """Return the old name-based preview path; never use it for scheduling."""
        return os.path.join(
            self.device_config.plugin_image_dir,
            f"{instance.plugin_id}_{instance.name.replace(' ', '_')}.png",
        )

    def _process_queue_entry(self, entry: QueueEntry):
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
            self._record_runtime_attempt(command)
            try:
                identity = InstanceIdentity(
                    command.instance_uuid,
                    command.structural_generation,
                    command.settings_revision,
                )
                with bind_long_task_runtime(context, identity):
                    self._execute_command(command)
            except TaskDeadlineExceeded as error:
                finished = self.refresh_queue.finish(
                    entry.job.id,
                    JobStatus.ABANDONED,
                    error_code="deadline_expired",
                    error=str(error),
                )
            except _CacheUnavailable as error:
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
                abort = self._classify_command_abort(command, context)
                if abort is None:
                    try:
                        self._record_command_failure(command, error)
                    except (TaskDeadlineExceeded, _StaleSelection, TaskCancelled) as abort_error:
                        abort = self._abort_details(abort_error)
                    except Exception:
                        logger.exception("Refresh failure bookkeeping also failed")
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
                    finished = self.refresh_queue.finish(entry.job.id, JobStatus.SUCCEEDED)
                    try:
                        if not bool(
                            getattr(
                                self._execution_local,
                                "degraded_data_result",
                                False,
                            )
                        ):
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
            self._signal_completion(finished.id)
        finally:
            self._cleanup_transient_uploads(entry.job.id, entry.command)
            if busy_lock is not None:
                busy_lock.release()
            self._execution_local.context = None
            self._execution_local.degraded_data_result = False
            self._active_operation = None
            try:
                self._run_memory_maintenance("refresh-command-finally")
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
        error = RuntimeError(
            f"DATA source is display-safe but unhealthy: {provenance.value}"
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
    ):
        if self._display_transactions_enabled:
            return self.display_manager.display_image(
                image,
                image_settings=image_settings,
                task_context=context,
                logical_target=logical_target,
                instance_revision=instance_revision,
            )
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
        context = self._current_task_context(command)
        context.raise_if_cancelled()
        if command.instance_uuid is not None:
            resolved = self._resolve_playlist_command(command)
            if resolved is None:
                raise _StaleSelection("playlist selection is stale")
            if command.intent is RefreshIntent.DISPLAY_CACHE:
                image, prepared_selection = self._load_catalog_display_image(
                    command,
                    resolved,
                )
                self._set_render_metadata(
                    False,
                    False,
                    self.device_config.get_plugin(command.plugin_id),
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
                return self._render_presentation_command(
                    command,
                    resolved,
                    context,
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
        self._set_render_metadata(True, False, getattr(plugin, "config", plugin_config))
        return self._commit_command_result(command, None, image, self._get_current_datetime())

    def _load_catalog_display_image(self, command, resolved):
        """Load prepared or authoritative bytes without plugin execution."""
        instance = None if resolved is None else resolved.instance
        if command.allow_prepared_presentation and instance is not None:
            plugin_config, _theme_context, resolved_theme_mode = self._latest_presentation_theme(instance)
            expected_request_id = command.payload.get("presentation_request_id")
            if not plugin_supports_presentation_refresh(plugin_config):
                if expected_request_id is not None:
                    raise _StaleSelection("presentation capability is no longer enabled")
            else:
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
                        error = RuntimeError("prepared presentation cache is missing or corrupt")
                        cleared_at = self._get_current_datetime().isoformat()
                        if not self.runtime_state.clear_prepared_presentation(
                            instance.instance_uuid,
                            request.request_id,
                            cleared_at,
                        ):
                            raise _StaleSelection("prepared presentation changed during validation")
                        self._record_presentation_failure(
                            command,
                            error,
                            self._get_current_datetime(),
                        )
                        self.presentation_cache.remove(candidate)
                        raise _CacheUnavailable(str(error))
                    return image, _PreparedDisplaySelection(
                        candidate=candidate,
                        request=request,
                        theme_mode=resolved_theme_mode,
                    )
                if expected_request_id is not None:
                    raise _StaleSelection("exact prepared presentation is no longer displayable")

        theme_mode = command.payload.get("cache_theme_mode")
        candidate = DisplayCacheCandidate(
            instance_uuid=command.instance_uuid,
            structural_generation=command.structural_generation,
            settings_revision=command.settings_revision,
            theme_mode=theme_mode,
            cache_path=authoritative_cache_path(
                self.cache_catalog.cache_root,
                command.instance_uuid,
                command.structural_generation,
                command.settings_revision,
                theme_mode,
            ),
            promoted_at=None,
        )
        image = self.cache_catalog.load_image(candidate)
        if image is None:
            self.cache_catalog.invalidate(candidate)
            raise _CacheUnavailable("display cache is unavailable")
        if resolved is None:
            return image
        return image, None

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
        self.scheduler_state.set_next_attempt(self._clock() + self._scheduler_poll_seconds())

    def _render_presentation_command(self, command, resolved, context):
        """Prepare provider-free presentation bytes on the shared worker."""
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
        self.presentation_cache.save(candidate, preparation.image)
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
        plugin = get_plugin_instance(plugin_config)
        settings = thaw_payload(instance.settings)
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
        generated = False
        cacheable = False

        with self.render_arbiter.lease(command.plugin_id, context):
            context.raise_if_cancelled()
            if (
                command.intent is RefreshIntent.DATA_REFRESH
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
                if theme_render_only and image_missing:
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
                    return None

                if theme_render_only and not image_missing:
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

                if not theme_render_only and display_cached_only and not should_generate:
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
        return selection

    def _live_display_target_is_current(self, command):
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
            theme_mode = _resolved_theme_mode(command.payload)
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
                command.intent is RefreshIntent.THEME_REDRAW
                and self._exact_cache_is_valid(instance, theme_mode)
            ):
                promoted_for_intent = True

            if degraded_data_result:
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
                if image_hash != latest_refresh.image_hash or self._display_target_changed(latest_refresh, refresh_info):
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
                    )

            if command.kind is CommandKind.DISPLAY:
                self._require_fresh_selection(command, context)
                if theme_context:
                    self._require_fresh_selection(command, context)

            if command.kind is CommandKind.DISPLAY:
                self._require_fresh_selection(command, context)
                # The final validation is the config commit linearization point.
                # Do not observe cancellation again after shared state is mutated.
                self.device_config.refresh_info = refresh_record
                if thawed_theme_context:
                    self._persist_active_theme(thawed_theme_context, current_dt)
                self._write_device_config()
                commit_id, committed_at = self._display_commit_evidence(
                    display_commit,
                    instance.instance_uuid,
                    current_dt,
                    display_was_invoked=display_was_invoked,
                )
                if command.allow_prepared_presentation:
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
        self._write_device_config()

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

    def _request_presentation_after_display(
        self,
        instance,
        display_commit_id,
        committed_at,
    ):
        """Record one coalesced request using metadata-only trigger resolution."""
        plugin_config, _theme_context, theme_mode = self._latest_presentation_theme(instance)
        if not plugin_supports_presentation_refresh(plugin_config):
            return False
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
            return False
        except Exception:
            logger.exception(
                "Presentation trigger resolution failed closed. | plugin_id: %s",
                instance.plugin_id,
            )
            return False
        if not requested:
            return False
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

    def _enqueue_live_display_followup(
        self,
        command,
        resolved_snapshot,
        current_dt,
        theme_mode,
    ):
        """Queue an exact cache-only display after a successful visible live refresh."""
        if not self._live_display_target_is_current(command):
            return None
        instance = resolved_snapshot.instance
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
        candidate = DisplayCacheCandidate(
            instance_uuid=instance.instance_uuid,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            theme_mode=theme_mode,
            cache_path=authoritative_cache_path(
                self.cache_catalog.cache_root,
                instance.instance_uuid,
                instance.structural_generation,
                instance.settings_revision,
                theme_mode,
            ),
            promoted_at=None,
        )
        return self.cache_catalog.validate(candidate)

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

    def _get_refresh_wait_seconds(self):
        """Return time until the next playlist tick, aligned to the latest refresh time."""
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
        wait_seconds = max(0, min(interval, interval - elapsed))
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
    ):
        now = self._clock()
        if deadline_monotonic is None:
            deadline_monotonic = now + self._manual_update_timeout_seconds()
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
        if theme_render_only or expected_displayed_instance_uuid is not None:
            payload["expected_displayed_instance_uuid"] = instance.instance_uuid
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
        if RefreshIntent(intent) is RefreshIntent.DISPLAY_CACHE:
            payload["cache_theme_mode"] = cache_theme_mode
        if presentation_request_id is not None:
            payload["presentation_request_id"] = str(presentation_request_id)
        normalized_intent = RefreshIntent(intent)
        if allow_prepared_presentation is None:
            allow_prepared_presentation = (
                normalized_intent is RefreshIntent.DISPLAY_CACHE
                and source in {CommandSource.MANUAL, CommandSource.SCHEDULER}
                and coalescing_scope is None
                and expected_displayed_instance_uuid is None
            )
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

    def _persist_active_theme(self, theme_context, current_dt):
        mode = theme_context.get("mode")
        if not mode:
            return
        info = {
            "mode": mode,
            "source": theme_context.get("source"),
            "reason": theme_context.get("reason"),
            "date": theme_context.get("date"),
            "sunrise": theme_context.get("sunrise"),
            "sunset": theme_context.get("sunset"),
            "updated_at": current_dt.isoformat(),
        }
        self._set_config_value("active_theme", mode)
        self._set_config_value("active_theme_info", info)
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
        try:
            return float(raw_value)
        except (TypeError, ValueError):
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

    def _run_memory_maintenance(self, reason, force=False):
        interval_seconds = max(0.0, self._config_float(
            "memory_maintenance_interval_seconds",
            DEFAULT_MEMORY_MAINTENANCE_INTERVAL_SECONDS,
        ))
        if interval_seconds <= 0 and not force:
            return None

        now = time.monotonic()
        if (
            not force
            and self._last_memory_maintenance_monotonic
            and now - self._last_memory_maintenance_monotonic < interval_seconds
        ):
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
        logger.info(
            "Memory maintenance completed. | reason: %s | collected_objects: %s | "
            "malloc_trim: %s | available_mb_before: %s | available_mb_after: %s | "
            "swap_percent_after: %s",
            reason,
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
            return False

        stats = self._read_memory_stats()
        if stats is None:
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
            return False

        now_monotonic = time.monotonic()
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
                self._is_same_plugin_instance(plugin_instance, displayed_plugin_instance)
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
            self._is_same_plugin_instance(plugin_instance, displayed_plugin_instance)
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
        if self.display_cached_only and not self.force and _display_refresh_under_resource_pressure(device_config):
            if not image_missing:
                logger.info(
                    "Using cached plugin instance image for scheduled display under resource pressure. | "
                    f"plugin_instance: {self.plugin_instance.name}."
                )
                try:
                    return _load_image_copy(plugin_image_path)
                except Exception:
                    logger.exception(
                        "Cached plugin image could not be loaded under resource pressure; using placeholder. | "
                        f"plugin_instance: {self.plugin_instance.name}."
                    )
            logger.warning(
                "Plugin instance image unavailable for scheduled display under resource pressure; using placeholder. | "
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
