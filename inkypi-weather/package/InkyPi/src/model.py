import logging
import random
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping
from uuid import uuid4

from runtime.refresh_contracts import freeze_payload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginInstanceSnapshot:
    """An immutable point-in-time view of a playlist plugin instance."""

    instance_uuid: str
    plugin_id: str
    name: str
    settings: Mapping[str, Any]
    refresh: Mapping[str, Any]
    latest_refresh_time: str | None
    structural_generation: int
    settings_revision: int

class RefreshInfo:
    """Keeps track of refresh metadata.

    Attributes:
        refresh_time (str): ISO-formatted time string of the refresh.
        image_hash (int): SHA-256 hash of the image.
        refresh_type (str): Refresh type ['Manual Update', 'Playlist'].
        plugin_id (str): Plugin id of the refresh.
        playlist (str): Playlist name if refresh_type is 'Playlist'.
        plugin_instance (str): Plugin instance name if refresh_type is 'Playlist'.
    """

    def __init__(self, refresh_type=None, plugin_id=None, refresh_time=None, image_hash=None, playlist=None, plugin_instance=None):
        """Initialize RefreshInfo instance."""
        self.refresh_time = refresh_time
        self.image_hash = image_hash
        self.refresh_type = refresh_type
        self.plugin_id = plugin_id
        self.playlist = playlist
        self.plugin_instance = plugin_instance

    def get_refresh_datetime(self):
        """Returns the refresh time as a datetime object or None if not set."""
        latest_refresh = None
        if self.refresh_time:
            try:
                latest_refresh = datetime.fromisoformat(str(self.refresh_time).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid refresh_time value: %s", self.refresh_time)
        return latest_refresh

    def to_dict(self):
        refresh_dict = {
            "refresh_time": self.refresh_time,
            "image_hash": self.image_hash,
            "refresh_type": self.refresh_type,
            "plugin_id": self.plugin_id,
        }
        if self.playlist:
            refresh_dict["playlist"] = self.playlist
        if self.plugin_instance:
            refresh_dict["plugin_instance"] = self.plugin_instance
        return refresh_dict

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            data = {}
        return cls(
            refresh_time=data.get("refresh_time"),
            image_hash=data.get("image_hash"),
            refresh_type=data.get("refresh_type"),
            plugin_id=data.get("plugin_id"),
            playlist=data.get("playlist"),
            plugin_instance=data.get("plugin_instance")
        )

class PlaylistManager:
    """A class managing multiple time-based playlists.

    Attributes:
        playlists (list): A list of Playlist instances managed by the manager.
        active_playlist (str): Name of the currently active playlist.
    """
    DEFAULT_PLAYLIST_START = "00:00"
    DEFAULT_PLAYLIST_END = "24:00"

    def __init__(self, playlists=None, active_playlist=None):
        """Initialize PlaylistManager with a list of playlists."""
        self.playlists = list(playlists or [])
        self.active_playlist = active_playlist
        # Serializes check-then-act mutations across web threads and the refresh thread
        self._lock = threading.RLock()
        with self._lock:
            self._ensure_unique_instance_uuids()

    def get_playlist_names(self):
        """Returns a list of all playlist names."""
        with self._lock:
            return [p.name for p in self.playlists]

    def add_default_playlist(self):
        """Add a default playlist to the manager, called when no playlists exist."""
        with self._lock:
            return self.playlists.append(
                Playlist("Default", PlaylistManager.DEFAULT_PLAYLIST_START, PlaylistManager.DEFAULT_PLAYLIST_END, []))

    def find_plugin(self, plugin_id, instance):
        """Searches playlists to find a plugin with the given ID and instance."""
        with self._lock:
            for playlist in self.playlists:
                plugin = playlist.find_plugin(plugin_id, instance)
                if plugin:
                    return plugin
            return None

    def snapshot_instance(self, instance_uuid):
        """Return an immutable snapshot for an instance UUID, if it still exists."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            return match[2].snapshot() if match else None

    def update_plugin_instance(
        self,
        instance_uuid,
        *,
        settings=None,
        refresh=None,
        name=None,
        expected_generation=None,
        expected_settings_revision=None,
    ):
        """Atomically update an instance, optionally rejecting stale callers."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return None

            instance = match[2]
            if (
                expected_generation is not None
                and instance.structural_generation != expected_generation
            ):
                return None
            if (
                expected_settings_revision is not None
                and instance.settings_revision != expected_settings_revision
            ):
                return None

            settings_changed = settings is not None and settings != instance.settings
            refresh_changed = refresh is not None and refresh != instance.refresh
            updated_name = str(name) if name is not None else instance.name
            name_changed = name is not None and updated_name != instance.name

            updated_settings = deepcopy(settings) if settings_changed else instance.settings
            updated_refresh = deepcopy(refresh) if refresh_changed else instance.refresh

            if settings_changed or refresh_changed or name_changed:
                instance.settings = updated_settings
                instance.refresh = updated_refresh
                instance.name = updated_name
                instance.settings_revision += 1

            return instance.snapshot()

    def delete_plugin_instance(self, instance_uuid, *, expected_generation=None):
        """Atomically delete an instance, optionally rejecting a stale generation."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return None

            playlist, index, instance = match
            if (
                expected_generation is not None
                and instance.structural_generation != expected_generation
            ):
                return None

            snapshot = instance.snapshot()
            playlist.plugins.pop(index)
            return snapshot

    def determine_active_playlist(self, current_datetime):
        """Determine the active playlist based on the current time."""
        with self._lock:
            current_time = current_datetime.strftime("%H:%M")  # Get current time in "HH:MM" format

            # get active playlists that have plugins
            active_playlists = [p for p in self.playlists if p.is_active(current_time)]
            if not active_playlists:
                return None

            # Sort playlists by priority
            active_playlists.sort(key=lambda p: p.get_priority())
            playlist = active_playlists[0]

            return playlist

    def get_playlist(self, playlist_name):
        """Returns the playlist with the specified name."""
        with self._lock:
            return next((p for p in self.playlists if p.name == playlist_name), None)

    def add_plugin_to_playlist(self, playlist_name, plugin_data):
        """Adds a plugin to a playlist by the specified name. Returns true if successfully added,
        False if playlist doesn't exist"""
        with self._lock:
            playlist = self.get_playlist(playlist_name)
            if playlist:
                if playlist.add_plugin(plugin_data):
                    added_instance = playlist.plugins[-1]
                    other_uuids = {
                        instance.instance_uuid
                        for current_playlist in self.playlists
                        for instance in current_playlist.plugins
                        if instance is not added_instance
                    }
                    if added_instance.instance_uuid in other_uuids:
                        added_instance.instance_uuid = self._new_instance_uuid(other_uuids)
                    return True
            else:
                logger.warning(f"Playlist '{playlist_name}' not found.")
            return False

    def add_playlist(self, name, start_time=None, end_time=None):
        """Creates and adds a new playlist with the given start and end times.
        Returns False if a playlist with the same name already exists."""
        if not start_time:
            start_time = PlaylistManager.DEFAULT_PLAYLIST_START
        if not end_time:
            end_time = PlaylistManager.DEFAULT_PLAYLIST_END
        with self._lock:
            if self.get_playlist(name):
                logger.warning(f"Playlist '{name}' already exists.")
                return False
            self.playlists.append(Playlist(name, start_time, end_time))
            return True

    def update_playlist(self, old_name, new_name, start_time, end_time):
        """Updates an existing playlist's name, start time, and end time.
        Returns False if the playlist is missing or the new name is already taken."""
        with self._lock:
            playlist = self.get_playlist(old_name)
            if not playlist:
                logger.warning(f"Playlist '{old_name}' not found.")
                return False
            if new_name != old_name and self.get_playlist(new_name):
                logger.warning(f"Cannot rename playlist '{old_name}': '{new_name}' already exists.")
                return False
            playlist.name = new_name
            playlist.start_time = start_time
            playlist.end_time = end_time
            return True

    def delete_playlist(self, name):
        """Deletes the playlist with the specified name."""
        with self._lock:
            self.playlists = [p for p in self.playlists if p.name != name]

    def to_dict(self):
        with self._lock:
            return {
                "playlists": [p.to_dict() for p in self.playlists],
                "active_playlist": self.active_playlist
            }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            data = {}
        return cls(
            playlists=[Playlist.from_dict(p) for p in data.get("playlists", [])],
            active_playlist=data.get("active_playlist")
        )

    def _find_instance_by_uuid(self, instance_uuid):
        for playlist in self.playlists:
            for index, instance in enumerate(playlist.plugins):
                if instance.instance_uuid == instance_uuid:
                    return playlist, index, instance
        return None

    def _ensure_unique_instance_uuids(self):
        seen_uuids = set()
        seen_instances = set()
        for playlist in self.playlists:
            for index, instance in enumerate(playlist.plugins):
                if id(instance) in seen_instances:
                    instance = PluginInstance.from_dict(instance.to_dict())
                    playlist.plugins[index] = instance
                seen_instances.add(id(instance))

                instance_uuid = str(instance.instance_uuid) if instance.instance_uuid else ""
                if not instance_uuid or instance_uuid in seen_uuids:
                    instance_uuid = self._new_instance_uuid(seen_uuids)
                instance.instance_uuid = instance_uuid
                seen_uuids.add(instance_uuid)

    @staticmethod
    def _new_instance_uuid(existing_uuids):
        instance_uuid = uuid4().hex
        while instance_uuid in existing_uuids:
            instance_uuid = uuid4().hex
        return instance_uuid

    @staticmethod
    def should_refresh(latest_refresh, interval_seconds, current_time):
        """Determines whether a refresh should occur on the interval and latest refresh time."""
        if not latest_refresh:
            return True  # No previous refresh, so it's time to refresh

        try:
            interval_seconds = float(interval_seconds)
        except (TypeError, ValueError):
            logger.warning("Invalid refresh interval '%s'; refreshing now.", interval_seconds)
            return True
        if interval_seconds <= 0:
            return True

        latest_refresh = PluginInstance.align_datetime_tz(latest_refresh, current_time)
        return (current_time - latest_refresh) >= timedelta(seconds=interval_seconds)

class Playlist:
    """Represents a playlist with a time interval.

    Attributes:
        name (str): Name of the playlist.
        start_time (str): Playlist start time in 'HH:MM'.
        end_time (str): Playlist end time in 'HH:MM'.
        plugins (list): A list of PluginInstance objects within the playlist.
        current_plugin_index (int): Index of the currently active plugin in the playlist.
    """
    RECENT_HISTORY_LIMIT = 8

    def __init__(
        self,
        name,
        start_time,
        end_time,
        plugins=None,
        current_plugin_index=None,
        plugin_rotation_queue=None,
        plugin_rotation_pool=None,
        plugin_rotation_recent_history=None,
    ):
        self.name = name
        self.start_time = start_time
        self.end_time = end_time
        self.plugins = [PluginInstance.from_dict(p) for p in (plugins or [])]
        self.current_plugin_index = current_plugin_index
        self.plugin_rotation_queue = list(plugin_rotation_queue or [])
        self.plugin_rotation_pool = list(plugin_rotation_pool or [])
        self.plugin_rotation_recent_history = list(plugin_rotation_recent_history or [])

    def is_active(self, current_time):
        """Check if the playlist is active at the given time."""
        if self.start_time <= self.end_time:
            # Non-wrapping window (EG: 09:00-15:00)
            return self.start_time <= current_time < self.end_time
        else:
            # Wrapping window across midnight (EG: 21:00-03:00)
            return current_time >= self.start_time or current_time < self.end_time

    def add_plugin(self, plugin_data):
        """Add a new plugin instance to the playlist."""
        if self.find_plugin(plugin_data["plugin_id"], plugin_data["name"]):
            logger.warning(f"Plugin '{plugin_data['plugin_id']}' with instance '{plugin_data['name']}' already exists.")
            return False
        self.plugins.append(PluginInstance.from_dict(plugin_data))
        return True

    def update_plugin(self, plugin_id, instance_name, updated_data):
        """Updates an existing plugin instance in the playlist."""
        plugin = self.find_plugin(plugin_id, instance_name)
        if plugin:
            plugin.update(updated_data)
            return True
        logger.warning(f"Plugin '{plugin_id}' with name '{instance_name}' not found.")
        return False

    def delete_plugin(self, plugin_id, name):
        """Remove a specific plugin instance from the playlist."""
        initial_count = len(self.plugins)
        self.plugins = [p for p in self.plugins if not (p.plugin_id == plugin_id and p.name == name)]
        
        if len(self.plugins) == initial_count:
            logger.warning(f"Plugin '{plugin_id}' with instance '{name}' not found.")
            return False
        return True

    def find_plugin(self, plugin_id, name):
        """Find a plugin instance by its plugin_id and name."""
        return next((p for p in self.plugins if p.plugin_id == plugin_id and p.name == name), None)

    def get_next_plugin(self):
        """Return the next plugin from a shuffled no-repeat rotation bag."""
        if not self.plugins:
            self.current_plugin_index = None
            self.plugin_rotation_queue = []
            self.plugin_rotation_pool = []
            self.plugin_rotation_recent_history = []
            return None

        if len(self.plugins) == 1:
            self.current_plugin_index = 0
            self.plugin_rotation_queue = []
            only_key = self._plugin_rotation_key(self.plugins[0])
            self.plugin_rotation_pool = [only_key]
            self.plugin_rotation_recent_history = [only_key]
            return self.plugins[self.current_plugin_index]

        plugin_keys = [self._plugin_rotation_key(plugin) for plugin in self.plugins]
        current_key = None
        if isinstance(self.current_plugin_index, int) and 0 <= self.current_plugin_index < len(self.plugins):
            current_key = plugin_keys[self.current_plugin_index]

        if self.plugin_rotation_pool != plugin_keys:
            self.plugin_rotation_queue = []
            self.plugin_rotation_pool = list(plugin_keys)

        queue = self._dedupe_rotation_keys(
            key for key in self.plugin_rotation_queue if key in plugin_keys
        )
        started_new_round = False
        if not queue:
            started_new_round = True
            queue = list(plugin_keys)
            random.shuffle(queue)

        recent_history = self._dedupe_rotation_keys(
            key for key in self.plugin_rotation_recent_history if key in plugin_keys
        )
        if started_new_round:
            recent_history = []

        if current_key and queue and queue[0] == current_key:
            replacement_index = next(
                (index for index, key in enumerate(queue[1:], start=1) if key != current_key),
                None,
            )
            if replacement_index is not None:
                queue[0], queue[replacement_index] = queue[replacement_index], queue[0]

        next_key = queue.pop(0)
        self.plugin_rotation_queue = queue
        self.current_plugin_index = plugin_keys.index(next_key)
        self.plugin_rotation_recent_history = self._updated_recent_history(next_key, recent_history, len(plugin_keys))

        return self.plugins[self.current_plugin_index]

    def _plugin_rotation_key(self, plugin):
        return plugin.instance_uuid

    def _recent_history_max_size(self, plugin_count):
        return min(self.RECENT_HISTORY_LIMIT, max(1, plugin_count - 1))

    def _updated_recent_history(self, next_key, recent_history, plugin_count):
        updated = [next_key]
        updated.extend(key for key in recent_history if key != next_key)
        return updated[:self._recent_history_max_size(plugin_count)]

    def _dedupe_rotation_keys(self, keys):
        deduped = []
        seen = set()
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            deduped.append(key)
        return deduped

    def get_priority(self):
        """Determine priority of a playlist, based on the time range"""
        return self.get_time_range_minutes()

    def get_time_range_minutes(self):
        """Calculate the time difference in minutes between start_time and end_time."""
        start = datetime.strptime(self.start_time, "%H:%M")
        # Handle '24:00' by converting it to '00:00' of the next day
        if self.end_time != "24:00":
            end = datetime.strptime(self.end_time, "%H:%M")
        else:
            end = datetime.strptime("00:00", "%H:%M")
            end += timedelta(days=1)

        # If the window wraps past midnight (EG: 21:00 -> 03:00), treat end as next day
        if end < start:
            end += timedelta(days=1)
            
        return int((end - start).total_seconds() // 60)

    def to_dict(self):
        return {
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "plugins": [p.to_dict() for p in self.plugins],
            "current_plugin_index": self.current_plugin_index,
            "plugin_rotation_queue": list(self.plugin_rotation_queue),
            "plugin_rotation_pool": list(self.plugin_rotation_pool),
            "plugin_rotation_recent_history": list(self.plugin_rotation_recent_history),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            name=data["name"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            plugins=data["plugins"],
            current_plugin_index=data.get("current_plugin_index", None),
            plugin_rotation_queue=data.get("plugin_rotation_queue", []),
            plugin_rotation_pool=data.get("plugin_rotation_pool", []),
            plugin_rotation_recent_history=data.get("plugin_rotation_recent_history", []),
        )

class PluginInstance:
    """Represents an individual plugin instance within a playlist.

    Attributes:
        plugin_id (str): Plugin id for this instance.
        name (str): Name of the plugin instance.
        settings (dict): Settings associated with the plugin.
        refresh (dict): Refresh settings, such as interval and scheduled time.
        latest_refresh (str): ISO-formatted string representing the last refresh time.
    """

    def __init__(
        self,
        plugin_id,
        name,
        settings=None,
        refresh=None,
        latest_refresh_time=None,
        instance_uuid=None,
        structural_generation=1,
        settings_revision=1,
    ):
        self.plugin_id = plugin_id
        self.name = name
        self.settings = deepcopy(settings) if settings is not None else {}
        self.refresh = deepcopy(refresh) if refresh is not None else {}
        self.latest_refresh_time = latest_refresh_time
        self.instance_uuid = str(instance_uuid) if instance_uuid else uuid4().hex
        self.structural_generation = self._positive_revision(structural_generation)
        self.settings_revision = self._positive_revision(settings_revision)

    def snapshot(self):
        """Return a deeply immutable copy of the instance's mutable state."""
        return PluginInstanceSnapshot(
            instance_uuid=self.instance_uuid,
            plugin_id=self.plugin_id,
            name=self.name,
            settings=freeze_payload(self.settings),
            refresh=freeze_payload(self.refresh),
            latest_refresh_time=self.latest_refresh_time,
            structural_generation=self.structural_generation,
            settings_revision=self.settings_revision,
        )

    @staticmethod
    def _positive_revision(value):
        try:
            return max(1, int(value or 1))
        except (TypeError, ValueError):
            return 1

    def update(self, updated_data):
        """Update attributes of the class with the dictionary values."""
        for key, value in updated_data.items():
            if key in {"settings", "refresh"}:
                value = deepcopy(value)
            setattr(self, key, value)

    def should_refresh(self, current_time):
        """Checks whether the plugin should be refreshed based on its refresh settings and the current time."""
        latest_refresh_dt = self.get_latest_refresh_dt()
        if not latest_refresh_dt:
            return True
        latest_refresh_dt = self.align_datetime_tz(latest_refresh_dt, current_time)

        # Check for interval-based refresh
        if "interval" in self.refresh:
            try:
                interval = float(self.refresh.get("interval"))
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid refresh interval for plugin '%s' instance '%s': %s",
                    self.plugin_id,
                    self.name,
                    self.refresh.get("interval"),
                )
                interval = None
            if interval and (current_time - latest_refresh_dt) >= timedelta(seconds=interval):
                return True

        if "scheduled" in self.refresh:
            scheduled_time_str = self.refresh.get("scheduled")
            try:
                scheduled_time = datetime.strptime(scheduled_time_str, "%H:%M").time()
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid scheduled refresh for plugin '%s' instance '%s': %s",
                    self.plugin_id,
                    self.name,
                    scheduled_time_str,
                )
                return False
            scheduled_dt = current_time.replace(
                hour=scheduled_time.hour,
                minute=scheduled_time.minute,
                second=0,
                microsecond=0,
            )

            if current_time < scheduled_dt:
                scheduled_dt -= timedelta(days=1)

            if latest_refresh_dt < scheduled_dt <= current_time:
                return True

        return False

    def get_image_path(self):
        """Formats the image path for this plugin instance."""
        return f"{self.plugin_id}_{self.name.replace(' ', '_')}.png"

    def get_latest_refresh_dt(self):
        """Returns the latest refresh time as a datetime object, or None if not set."""
        latest_refresh = None
        if self.latest_refresh_time:
            try:
                latest_refresh = datetime.fromisoformat(str(self.latest_refresh_time).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring invalid latest_refresh_time for plugin '%s' instance '%s': %s",
                    self.plugin_id,
                    self.name,
                    self.latest_refresh_time,
                )
        return latest_refresh

    @staticmethod
    def align_datetime_tz(value, reference):
        if value.tzinfo is None and reference.tzinfo is not None:
            localize = getattr(reference.tzinfo, "localize", None)
            return localize(value) if localize else value.replace(tzinfo=reference.tzinfo)
        if value.tzinfo is not None and reference.tzinfo is not None:
            return value.astimezone(reference.tzinfo)
        if value.tzinfo is not None and reference.tzinfo is None:
            return value.replace(tzinfo=None)
        return value
    
    def to_dict(self):
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "plugin_settings": deepcopy(self.settings),
            "refresh": deepcopy(self.refresh),
            "latest_refresh_time": self.latest_refresh_time,
            "instance_uuid": self.instance_uuid,
            "structural_generation": self.structural_generation,
            "settings_revision": self.settings_revision,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            plugin_id=data["plugin_id"],
            name=data["name"],
            settings=data["plugin_settings"],
            refresh=data["refresh"],
            latest_refresh_time=data.get("latest_refresh_time"),
            instance_uuid=data.get("instance_uuid"),
            structural_generation=data.get("structural_generation", 1),
            settings_revision=data.get("settings_revision", 1),
        )
