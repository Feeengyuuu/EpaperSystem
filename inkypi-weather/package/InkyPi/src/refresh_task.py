import threading
import time
import os
import logging
import psutil
import pytz
from datetime import datetime, timezone
from plugins.plugin_registry import get_plugin_instance
from utils.image_utils import compute_image_hash
from utils.theme_utils import get_theme_context
from model import RefreshInfo, PlaylistManager
from PIL import Image

logger = logging.getLogger(__name__)
DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS = 5 * 60


def _setting_enabled(value):
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _refresh_on_display(plugin_instance):
    settings = plugin_instance.settings or {}
    if "refreshOnDisplay" in settings:
        return _setting_enabled(settings.get("refreshOnDisplay"))

    if plugin_instance.plugin_id == "newspaper":
        return str(settings.get("mediaRotationMode") or "rotate").lower() != "single"

    if plugin_instance.plugin_id == "backtothedate":
        return True

    return False


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
            try:
                with self.condition:
                    sleep_time = self.device_config.get_config(
                        "plugin_cycle_interval_seconds",
                        default=DEFAULT_PLUGIN_CYCLE_INTERVAL_SECONDS,
                    )

                    # Wait for sleep_time or until notified
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
                    background_cache_refresh_force = False
                    theme_context_to_persist = None
                    if self.manual_update_request:
                        # handle immediate update request
                        logger.info("Manual update requested")
                        refresh_action = self.manual_update_request
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
                            if refresh_action is None:
                                refresh_action = PlaylistRefresh(playlist, plugin_instance)
                                background_cache_refresh = (playlist, plugin_instance)

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
                        # check if image is the same as current image
                        if image_hash != latest_refresh.image_hash:
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
                                skip_plugin_instance=displayed_plugin_instance,
                                force=background_cache_refresh_force,
                            )

            except Exception as e:
                logger.exception('Exception during refresh')
                self.refresh_result["exception"] = e  # Capture exception
            finally:
                self.refresh_event.set()

    def manual_update(self, refresh_action):
        """Manually triggers an update for the specified plugin id and plugin settings by notifying the background process."""
        if self.running:
            with self.condition:
                self.manual_update_request = refresh_action
                self.refresh_result = {}
                self.refresh_event.clear()

                self.condition.notify_all()  # Wake the thread to process manual update

            self.refresh_event.wait()
            if self.refresh_result.get("exception"):
                raise self.refresh_result.get("exception")
        else:
            logger.warning("Background refresh task is not running, unable to do a manual update")

    def signal_config_change(self):
        """Notify the background thread that config has changed (e.g., interval updated)."""
        if self.running:
            with self.condition:
                self.condition.notify_all()

    def _get_current_datetime(self):
        """Retrieves the current datetime based on the device's configured timezone."""
        tz_str = self.device_config.get_config("timezone", default="UTC")
        return datetime.now(pytz.timezone(tz_str))

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

    def _start_due_plugin_cache_refresh(self, playlist, current_dt, skip_plugin_instance=None, force=False):
        """Start a non-blocking cache refresh for due non-displayed plugins."""
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
                    force=force,
                )
            finally:
                self.cache_refresh_lock.release()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _refresh_due_plugin_instances(self, playlist, current_dt, skip_plugin_instance=None, force=False):
        """Refresh cached images for due plugin instances in the active playlist.

        This is intended for the non-blocking background cache pass. Display
        rotation remains random, and the displayed plugin is refreshed
        synchronously before this pass starts.
        """
        updated = False
        for plugin_instance in list(playlist.plugins):
            if self._is_same_plugin_instance(plugin_instance, skip_plugin_instance):
                continue

            plugin_image_path = os.path.join(
                self.device_config.plugin_image_dir,
                plugin_instance.get_image_path(),
            )
            image_missing = not os.path.exists(plugin_image_path)
            if not force and not image_missing and not plugin_instance.should_refresh(current_dt):
                continue

            try:
                if image_missing:
                    logger.info(
                        "Plugin instance image missing during cache refresh. | "
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
                image = plugin.generate_image(plugin_instance.settings, self.device_config)
                os.makedirs(os.path.dirname(plugin_image_path), exist_ok=True)
                image.save(plugin_image_path)
                plugin_instance.latest_refresh_time = current_dt.isoformat()
                updated = True
            except Exception:
                logger.exception(
                    "Exception during due plugin instance cache refresh. | "
                    f"plugin_instance: '{plugin_instance.name}'"
                )

        if updated:
            self._write_device_config()

    def _is_same_plugin_instance(self, plugin_instance, other_plugin_instance):
        if not plugin_instance or not other_plugin_instance:
            return False
        return (
            plugin_instance.plugin_id == other_plugin_instance.plugin_id
            and plugin_instance.name == other_plugin_instance.name
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
        return plugin.generate_image(self.plugin_settings, device_config)

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

    def __init__(self, playlist, plugin_instance, force=False):
        self.playlist = playlist
        self.plugin_instance = plugin_instance
        self.force = force

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

        # Check if a refresh is needed based on the plugin instance's criteria
        refresh_on_display = _refresh_on_display(self.plugin_instance)

        if self.plugin_instance.should_refresh(current_dt) or self.force or image_missing or refresh_on_display:
            if image_missing:
                logger.info(f"Plugin instance image missing, refreshing. | plugin_instance: '{self.plugin_instance.name}'")
            if refresh_on_display and not self.force and not image_missing:
                logger.info(f"Refreshing plugin instance on display. | plugin_instance: '{self.plugin_instance.name}'")
            else:
                logger.info(f"Refreshing plugin instance. | plugin_instance: '{self.plugin_instance.name}'")
            # Generate a new image
            image = plugin.generate_image(self.plugin_instance.settings, device_config)
            image.save(plugin_image_path)
            self.plugin_instance.latest_refresh_time = current_dt.isoformat()
        else:
            logger.info(f"Not time to refresh plugin instance, using latest image. | plugin_instance: {self.plugin_instance.name}.")
            # Load the existing image from disk
            with Image.open(plugin_image_path) as img:
                image = img.copy()

        return image
