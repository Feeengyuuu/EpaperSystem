import threading
import time
import os
import logging
import json
import psutil
import pytz
from datetime import datetime, timedelta, timezone
from plugins.plugin_registry import get_plugin_instance
from utils.image_utils import compute_image_hash
from utils.theme_utils import get_theme_context
from model import RefreshInfo, PlaylistManager
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)
DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS = 5 * 60
DEFAULT_MANUAL_UPDATE_TIMEOUT_SECONDS = 180
SPORTS_DASHBOARD_PLUGIN_ID = "sports_dashboard"
SPORTS_DASHBOARD_WORLD_CUP_LIVE_STATE_VERSION = "sports-dashboard-worldcup-live-v1"
SPORTS_DASHBOARD_LPL_LIVE_STATE_VERSION = "sports-dashboard-lpl-live-v1"
SPORTS_DASHBOARD_NBA_LIVE_STATE_VERSION = "sports-dashboard-nba-live-v1"
DEFAULT_SPORTS_DASHBOARD_WORLD_CUP_LIVE_REFRESH_SECONDS = 60
DEFAULT_SPORTS_DASHBOARD_LPL_LIVE_REFRESH_SECONDS = 60
DEFAULT_SPORTS_DASHBOARD_NBA_LIVE_REFRESH_SECONDS = 60
REFRESH_ON_DISPLAY_PLUGIN_IDS = {"backtothedate", "lol_info"}


def _setting_enabled(value):
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _refresh_on_display(plugin_instance):
    settings = plugin_instance.settings or {}
    if "refreshOnDisplay" in settings:
        return _setting_enabled(settings.get("refreshOnDisplay"))

    if plugin_instance.plugin_id == "newspaper":
        return str(settings.get("mediaRotationMode") or "rotate").lower() != "single"

    if plugin_instance.plugin_id in REFRESH_ON_DISPLAY_PLUGIN_IDS:
        return True

    return False


def _settings_with_force_refresh(settings, force=False):
    merged = dict(settings or {})
    if force:
        merged["forceRefresh"] = True
        merged["force_refresh"] = True
    return merged


def _save_image_atomic(image, path):
    """Write a PNG/JPEG cache image without exposing a partially-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    root, ext = os.path.splitext(path)
    tmp_path = f"{root}.tmp-{os.getpid()}-{threading.get_ident()}{ext or '.png'}"
    try:
        image.save(tmp_path)
        os.replace(tmp_path, path)
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


class RefreshTask:
    """Handles the logic for refreshing the display using a background thread."""

    def __init__(self, device_config, display_manager):
        self.device_config = device_config
        self.display_manager = display_manager

        self.thread = None
        self.cache_refresh_lock = threading.Lock()
        self.config_write_lock = threading.Lock()
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.running = False
        self.manual_update_request = ()

        self.refresh_event = threading.Event()
        self.refresh_event.set()
        self.refresh_result = {}

    def start(self):
        """Starts the background thread for refreshing the display."""
        if not self.thread or not self.thread.is_alive():
            logger.info("Starting refresh task")
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.running = True
            self.thread.start()

    def stop(self):
        """Stops the refresh task by notifying the background thread to exit."""
        with self.condition:
            self.running = False
            self.condition.notify_all()  # Wake the thread to let it exit
        if self.thread:
            logger.info("Stopping refresh task")
            self.thread.join()

    def _run(self):
        """Background task that manages the periodic refresh of the display.

        This function runs in a loop, sleeping for a configured duration (`plugin_cycle_interval_seconds`) or until
        manually triggered via `manual_update()`. Determines the next plugin to refresh based on active playlists and
        updates the display accordingly.

        Workflow:
        1. Waits for the configured sleep duration or until notified of a manual update.
        2. Checks if a manual update has been requested:
        - If so, refreshes the specified plugin immediately.
        3. Otherwise, determines the next plugin to refresh based on the active playlist and generates an image.
        4. Compares the image hash with the last displayed image hash.
        - If the image has changed, updates the display.
        - If the image is the same, skips the refresh.
        5. Updates the refresh metadata in the device configuration.
        6. Repeats the process until `stop()` is called.

        Handles any exceptions that occur during the refresh process and ensures the refresh event is set 
        to indicate completion.

        Exceptions:
        - Captures and logs any unexpected errors during execution to prevent the thread from exiting.
        """
        while True:
            active_manual_request = None
            try:
                with self.condition:
                    if not self.manual_update_request:
                        sleep_time = self._get_refresh_wait_seconds()
                        # Wait for sleep_time or until notified.
                        self.condition.wait(timeout=sleep_time)
                    self.refresh_result = {}
                    self.refresh_event.clear()

                    # Exit if `stop()` is called
                    if not self.running:
                        break

                    playlist_manager = self.device_config.get_playlist_manager()
                    latest_refresh = self.device_config.get_refresh_info()
                    current_dt = self._get_current_datetime()

                    refresh_action = None
                    background_cache_refresh = None
                    background_cache_refresh_only_plugin_id = None
                    background_cache_refresh_force = False
                    theme_context_to_persist = None
                    if self.manual_update_request:
                        # handle immediate update request
                        logger.info("Manual update requested")
                        active_manual_request = self.manual_update_request
                        refresh_action = active_manual_request["action"]
                        self.manual_update_request = ()
                    else:

                        if self.device_config.get_config("log_system_stats"):
                            self.log_system_stats()

                        # handle refresh based on playlists
                        logger.info(f"Running interval refresh check. | current_time: {current_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                        theme_context = get_theme_context(self.device_config, now=current_dt)
                        if self._has_theme_changed(theme_context):
                            playlist, plugin_instance = self._determine_theme_refresh_plugin(playlist_manager, latest_refresh, current_dt)
                            if plugin_instance:
                                logger.info(
                                    "Theme changed; forcing display refresh. | "
                                    f"active_theme: {theme_context.get('mode')} | "
                                    f"source: {theme_context.get('source')}"
                                )
                                refresh_action = PlaylistRefresh(playlist, plugin_instance, force=True)
                                background_cache_refresh = (playlist, plugin_instance)
                                background_cache_refresh_force = True
                                theme_context_to_persist = theme_context
                        else:
                            playlist, plugin_instance = self._determine_next_plugin(playlist_manager, latest_refresh, current_dt)
                            if plugin_instance:
                                refresh_action = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True)
                                background_cache_refresh = (playlist, plugin_instance)
                            else:
                                playlist = playlist_manager.determine_active_playlist(current_dt)
                                if playlist and self._playlist_has_sports_dashboard_live_refresh_due(playlist, current_dt):
                                    logger.info("SportsDashboard live cache refresh due before playlist display tick.")
                                    background_cache_refresh = (playlist, None)
                                    background_cache_refresh_only_plugin_id = SPORTS_DASHBOARD_PLUGIN_ID

                    if refresh_action:
                        plugin_config = self.device_config.get_plugin(refresh_action.get_plugin_id())
                        if plugin_config is None:
                            logger.error(f"Plugin config not found for '{refresh_action.get_plugin_id()}'.")
                            continue
                        plugin = get_plugin_instance(plugin_config)
                        image = refresh_action.execute(plugin, self.device_config, current_dt)
                        image_hash = compute_image_hash(image)

                        refresh_info = refresh_action.get_refresh_info()
                        refresh_info.update({"refresh_time": current_dt.isoformat(), "image_hash": image_hash})
                        display_target_changed = self._display_target_changed(latest_refresh, refresh_info)
                        # check if image is the same as current image
                        if image_hash != latest_refresh.image_hash or display_target_changed:
                            logger.info(f"Updating display. | refresh_info: {refresh_info}")
                            self.display_manager.display_image(image, image_settings=plugin.config.get("image_settings", []))
                        else:
                            logger.info(f"Image already displayed, skipping refresh. | refresh_info: {refresh_info}")

                        # update latest refresh data in the device config
                        self.device_config.refresh_info = RefreshInfo(**refresh_info)
                        if theme_context_to_persist:
                            self._persist_active_theme(theme_context_to_persist, current_dt)
                        self._write_device_config()

                        if background_cache_refresh:
                            playlist, displayed_plugin_instance = background_cache_refresh
                            self._start_due_plugin_cache_refresh(
                                playlist,
                                current_dt,
                                skip_plugin_instance=displayed_plugin_instance if background_cache_refresh_force else None,
                                displayed_plugin_instance=displayed_plugin_instance,
                                force=background_cache_refresh_force,
                                only_plugin_id=background_cache_refresh_only_plugin_id,
                            )
                    elif background_cache_refresh:
                        playlist, displayed_plugin_instance = background_cache_refresh
                        self._start_due_plugin_cache_refresh(
                            playlist,
                            current_dt,
                            skip_plugin_instance=None,
                            displayed_plugin_instance=displayed_plugin_instance,
                            force=False,
                            only_plugin_id=background_cache_refresh_only_plugin_id,
                        )

            except Exception as e:
                logger.exception('Exception during refresh')
                if active_manual_request:
                    active_manual_request["result"]["exception"] = e
                else:
                    self.refresh_result["exception"] = e  # Capture exception
            finally:
                if active_manual_request:
                    active_manual_request["event"].set()
                else:
                    self.refresh_event.set()

    def manual_update(self, refresh_action):
        """Manually triggers an update for the specified plugin id and plugin settings by notifying the background process."""
        if self.running:
            request = {
                "action": refresh_action,
                "event": threading.Event(),
                "result": {},
            }
            with self.condition:
                self.manual_update_request = request
                self.refresh_result = {}
                self.condition.notify_all()  # Wake the thread to process manual update

            timeout = self._manual_update_timeout_seconds()
            completed = request["event"].wait(timeout=timeout)
            if not completed:
                with self.condition:
                    if self.manual_update_request is request:
                        self.manual_update_request = ()
                raise TimeoutError(f"Manual update timed out after {timeout:.0f} seconds")
            if request["result"].get("exception"):
                raise request["result"].get("exception")
        else:
            logger.warning("Background refresh task is not running, unable to do a manual update")

    def _manual_update_timeout_seconds(self):
        raw_value = self.device_config.get_config(
            "manual_update_timeout_seconds",
            default=DEFAULT_MANUAL_UPDATE_TIMEOUT_SECONDS,
        )
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = DEFAULT_MANUAL_UPDATE_TIMEOUT_SECONDS
        return max(0.01, min(600.0, value))

    def signal_config_change(self):
        """Notify the background thread that config has changed (e.g., interval updated)."""
        if self.running:
            with self.condition:
                self.condition.notify_all()

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
        live_wait_seconds = self._sports_dashboard_live_refresh_wait_seconds(current_dt)
        if live_wait_seconds is not None:
            if live_wait_seconds <= 0 < wait_seconds:
                wait_seconds = min(wait_seconds, 5.0)
            else:
                wait_seconds = min(wait_seconds, max(0, live_wait_seconds))
        return wait_seconds

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

    def _has_theme_changed(self, theme_context):
        current_mode = (theme_context or {}).get("mode")
        previous_mode = self._get_config_value("active_theme", None)
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
        """Start a non-blocking cache refresh for due plugin instances."""
        if not self.running:
            return
        if not self.cache_refresh_lock.acquire(blocking=False):
            logger.info("Due plugin cache refresh already running, skipping this tick.")
            return

        def worker():
            try:
                self._refresh_due_plugin_instances(
                    playlist,
                    current_dt,
                    skip_plugin_instance=skip_plugin_instance,
                    displayed_plugin_instance=displayed_plugin_instance,
                    force=force,
                    only_plugin_id=only_plugin_id,
                )
            finally:
                self.cache_refresh_lock.release()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _refresh_due_plugin_instances(self, playlist, current_dt, skip_plugin_instance=None, displayed_plugin_instance=None, force=False, only_plugin_id=None):
        """Refresh cached images for due plugin instances in the active playlist.

        This is intended for the non-blocking background cache pass. Display
        rotation uses the latest cached image first, then this pass updates
        stale caches without blocking the next visible playlist tick.
        """
        updated = False
        for plugin_instance in list(playlist.plugins):
            if only_plugin_id and plugin_instance.plugin_id != only_plugin_id:
                continue
            if self._is_same_plugin_instance(plugin_instance, skip_plugin_instance):
                continue

            plugin_image_path = os.path.join(
                self.device_config.plugin_image_dir,
                plugin_instance.get_image_path(),
            )
            image_missing = not os.path.exists(plugin_image_path)
            refresh_on_display = (
                self._is_same_plugin_instance(plugin_instance, displayed_plugin_instance)
                and _refresh_on_display(plugin_instance)
            )
            live_refresh_due = self._sports_dashboard_live_refresh_due(plugin_instance, current_dt)
            if not force and not image_missing and not plugin_instance.should_refresh(current_dt) and not refresh_on_display and not live_refresh_due:
                continue

            try:
                if image_missing:
                    logger.info(
                        "Plugin instance image missing during cache refresh. | "
                        f"plugin_instance: '{plugin_instance.name}'"
                    )
                if live_refresh_due and not force and not image_missing:
                    logger.info(
                        "SportsDashboard live cache refresh due. | "
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
                image = plugin.generate_image(_settings_with_force_refresh(plugin_instance.settings, force), self.device_config)
                _save_image_atomic(image, plugin_image_path)
                plugin_instance.latest_refresh_time = current_dt.isoformat()
                updated = True
            except Exception:
                logger.exception(
                    "Exception during due plugin instance cache refresh. | "
                    f"plugin_instance: '{plugin_instance.name}'"
                )

        if updated:
            self._write_device_config()

    def _playlist_has_sports_dashboard_live_refresh_due(self, playlist, current_dt):
        return any(
            self._sports_dashboard_live_refresh_due(plugin_instance, current_dt)
            for plugin_instance in list(getattr(playlist, "plugins", []) or [])
        )

    def _sports_dashboard_live_refresh_due(self, plugin_instance, current_dt):
        if getattr(plugin_instance, "plugin_id", None) != SPORTS_DASHBOARD_PLUGIN_ID:
            return False
        active_sources = self._sports_dashboard_enabled_live_sources(plugin_instance, current_dt)
        if not active_sources:
            return False
        latest_refresh_dt = plugin_instance.get_latest_refresh_dt()
        if not latest_refresh_dt:
            return True
        latest_refresh_dt = self._align_datetime_tz(latest_refresh_dt, current_dt)
        interval = min(
            self._sports_dashboard_live_refresh_interval(plugin_instance, source)
            for source in active_sources
        )
        return (current_dt - latest_refresh_dt) >= timedelta(seconds=interval)

    def _sports_dashboard_live_refresh_wait_seconds(self, current_dt):
        try:
            playlist_manager = self.device_config.get_playlist_manager()
            playlist = playlist_manager.determine_active_playlist(current_dt)
        except Exception:
            return None
        if not playlist or not self._sports_dashboard_active_live_sources(current_dt):
            return None

        waits = []
        for plugin_instance in list(getattr(playlist, "plugins", []) or []):
            if getattr(plugin_instance, "plugin_id", None) != SPORTS_DASHBOARD_PLUGIN_ID:
                continue
            active_sources = self._sports_dashboard_enabled_live_sources(plugin_instance, current_dt)
            if not active_sources:
                continue
            latest_refresh_dt = plugin_instance.get_latest_refresh_dt()
            if not latest_refresh_dt:
                waits.append(0)
                continue
            latest_refresh_dt = self._align_datetime_tz(latest_refresh_dt, current_dt)
            elapsed = (current_dt - latest_refresh_dt).total_seconds()
            interval = min(
                self._sports_dashboard_live_refresh_interval(plugin_instance, source)
                for source in active_sources
            )
            waits.append(interval - elapsed)
        if not waits:
            return None
        return min(waits)

    def _sports_dashboard_enabled_live_sources(self, plugin_instance, current_dt):
        return [
            source
            for source in self._sports_dashboard_active_live_sources(current_dt)
            if self._sports_dashboard_live_refresh_enabled(plugin_instance, source)
        ]

    def _sports_dashboard_active_live_sources(self, current_dt):
        sources = []
        if self._sports_dashboard_worldcup_live_state_active(current_dt):
            sources.append("worldcup")
        if self._sports_dashboard_lpl_live_state_active(current_dt):
            sources.append("lpl")
        if self._sports_dashboard_nba_live_state_active(current_dt):
            sources.append("nba")
        return sources

    def _sports_dashboard_live_refresh_enabled(self, plugin_instance, source):
        if source == "nba":
            settings = getattr(plugin_instance, "settings", None) or {}
            if "nbaLiveRefreshEnabled" not in settings:
                return True
            return _setting_enabled(settings.get("nbaLiveRefreshEnabled"))
        if source == "worldcup":
            settings = getattr(plugin_instance, "settings", None) or {}
            if "worldCupLiveRefreshEnabled" not in settings:
                return True
            return _setting_enabled(settings.get("worldCupLiveRefreshEnabled"))
        return self._sports_dashboard_lpl_live_refresh_enabled(plugin_instance)

    def _sports_dashboard_live_refresh_interval(self, plugin_instance, source):
        if source == "nba":
            settings = getattr(plugin_instance, "settings", None) or {}
            try:
                value = int(settings.get(
                    "nbaLiveRefreshIntervalSeconds",
                    DEFAULT_SPORTS_DASHBOARD_NBA_LIVE_REFRESH_SECONDS,
                ))
            except (TypeError, ValueError):
                value = DEFAULT_SPORTS_DASHBOARD_NBA_LIVE_REFRESH_SECONDS
            return max(60, min(900, value))
        if source == "worldcup":
            settings = getattr(plugin_instance, "settings", None) or {}
            try:
                value = int(settings.get(
                    "worldCupLiveRefreshIntervalSeconds",
                    DEFAULT_SPORTS_DASHBOARD_WORLD_CUP_LIVE_REFRESH_SECONDS,
                ))
            except (TypeError, ValueError):
                value = DEFAULT_SPORTS_DASHBOARD_WORLD_CUP_LIVE_REFRESH_SECONDS
            return max(60, min(900, value))
        return self._sports_dashboard_lpl_live_refresh_interval(plugin_instance)

    def _sports_dashboard_lpl_live_refresh_enabled(self, plugin_instance):
        settings = getattr(plugin_instance, "settings", None) or {}
        if "lplLiveRefreshEnabled" not in settings:
            return True
        return _setting_enabled(settings.get("lplLiveRefreshEnabled"))

    def _sports_dashboard_lpl_live_refresh_interval(self, plugin_instance):
        settings = getattr(plugin_instance, "settings", None) or {}
        try:
            value = int(settings.get(
                "lplLiveRefreshIntervalSeconds",
                DEFAULT_SPORTS_DASHBOARD_LPL_LIVE_REFRESH_SECONDS,
            ))
        except (TypeError, ValueError):
            value = DEFAULT_SPORTS_DASHBOARD_LPL_LIVE_REFRESH_SECONDS
        return max(60, min(900, value))

    def _sports_dashboard_lpl_live_state_active(self, current_dt):
        return self._sports_dashboard_live_state_active(
            current_dt,
            self._sports_dashboard_lpl_live_state_path(),
            SPORTS_DASHBOARD_LPL_LIVE_STATE_VERSION,
        )

    def _sports_dashboard_worldcup_live_state_active(self, current_dt):
        return self._sports_dashboard_live_state_active(
            current_dt,
            self._sports_dashboard_worldcup_live_state_path(),
            SPORTS_DASHBOARD_WORLD_CUP_LIVE_STATE_VERSION,
        )

    def _sports_dashboard_nba_live_state_active(self, current_dt):
        return self._sports_dashboard_live_state_active(
            current_dt,
            self._sports_dashboard_nba_live_state_path(),
            SPORTS_DASHBOARD_NBA_LIVE_STATE_VERSION,
        )

    def _sports_dashboard_live_state_active(self, current_dt, path, version):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (FileNotFoundError, OSError, ValueError):
            return False
        if not isinstance(state, dict):
            return False
        if state.get("version") != version:
            return False
        if not state.get("has_live"):
            return False
        live_until = self._parse_iso_datetime(state.get("live_until"))
        if not live_until:
            return True
        return current_dt <= self._align_datetime_tz(live_until, current_dt)

    def _sports_dashboard_lpl_live_state_path(self):
        return os.path.join(
            os.path.dirname(__file__),
            "plugins",
            SPORTS_DASHBOARD_PLUGIN_ID,
            "cache",
            "lpl_live_state.json",
        )

    def _sports_dashboard_worldcup_live_state_path(self):
        return os.path.join(
            os.path.dirname(__file__),
            "plugins",
            SPORTS_DASHBOARD_PLUGIN_ID,
            "cache",
            "worldcup_live_state.json",
        )

    def _sports_dashboard_nba_live_state_path(self):
        return os.path.join(
            os.path.dirname(__file__),
            "plugins",
            SPORTS_DASHBOARD_PLUGIN_ID,
            "cache",
            "nba_live_state.json",
        )

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
        return plugin.generate_image(_settings_with_force_refresh(self.plugin_settings, True), device_config)

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

        refresh_on_display = _refresh_on_display(self.plugin_instance)

        if self.display_cached_only and not self.force and not refresh_on_display:
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
                image = plugin.generate_image(_settings_with_force_refresh(self.plugin_instance.settings, self.force), device_config)
                _save_image_atomic(image, plugin_image_path)
                self.plugin_instance.latest_refresh_time = current_dt.isoformat()
                return image
            except Exception:
                logger.exception(
                    "Plugin instance could not refresh for scheduled display; using placeholder. | "
                    f"plugin_instance: '{self.plugin_instance.name}'"
                )
                return self._placeholder_image(device_config)

        # Check if a refresh is needed based on the plugin instance's criteria
        if self.plugin_instance.should_refresh(current_dt) or self.force or image_missing or refresh_on_display:
            if image_missing:
                logger.info(f"Plugin instance image missing, refreshing. | plugin_instance: '{self.plugin_instance.name}'")
            if refresh_on_display and not self.force and not image_missing:
                logger.info(f"Refreshing plugin instance on display. | plugin_instance: '{self.plugin_instance.name}'")
            else:
                logger.info(f"Refreshing plugin instance. | plugin_instance: '{self.plugin_instance.name}'")
            # Generate a new image
            image = plugin.generate_image(_settings_with_force_refresh(self.plugin_instance.settings, self.force), device_config)
            _save_image_atomic(image, plugin_image_path)
            self.plugin_instance.latest_refresh_time = current_dt.isoformat()
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
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        ]
        for path in paths:
            try:
                if os.path.exists(path):
                    return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

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
