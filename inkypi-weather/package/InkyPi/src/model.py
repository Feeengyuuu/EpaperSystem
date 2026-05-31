import os
import json
import logging
import random
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

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

    def __init__(self, refresh_type, plugin_id, refresh_time, image_hash, playlist=None, plugin_instance=None):
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
            latest_refresh = datetime.fromisoformat(self.refresh_time)
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

    def __init__(self, playlists=[], active_playlist=None):
        """Initialize PlaylistManager with a list of playlists."""
        self.playlists = playlists
        self.active_playlist = active_playlist

    def get_playlist_names(self):
        """Returns a list of all playlist names."""
        return [p.name for p in self.playlists]

    def add_default_playlist(self):
        """Add a default playlist to the manager, called when no playlists exist."""
        return self.playlists.append(
            Playlist("Default", PlaylistManager.DEFAULT_PLAYLIST_START, PlaylistManager.DEFAULT_PLAYLIST_END, []))

    def find_plugin(self, plugin_id, instance):
        """Searches playlists to find a plugin with the given ID and instance."""
        for playlist in self.playlists:
            plugin = playlist.find_plugin(plugin_id, instance)
            if plugin:
                return plugin
        return None

    def determine_active_playlist(self, current_datetime):
        """Determine the active playlist based on the current time."""
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
        return next((p for p in self.playlists if p.name == playlist_name), None)

    def add_plugin_to_playlist(self, playlist_name, plugin_data):
        """Adds a plugin to a playlist by the specified name. Returns true if successfully added,
        False if playlist doesn't exist"""
        playlist = self.get_playlist(playlist_name)
        if playlist:
            if playlist.add_plugin(plugin_data):
                return True
        else:
            logger.warning(f"Playlist '{playlist_name}' not found.")
        return False

    def add_playlist(self, name, start_time=None, end_time=None):
        """Creates and adds a new playlist with the given start and end times."""
        if not start_time:
            start_time = PlaylistManager.DEFAULT_PLAYLIST_START
        if not end_time:
            end_time = PlaylistManager.DEFAULT_PLAYLIST_END
        self.playlists.append(Playlist(name, start_time, end_time))
        return True

    def update_playlist(self, old_name, new_name, start_time, end_time):
        """Updates an existing playlist's name, start time, and end time."""
        playlist = self.get_playlist(old_name)
        if playlist:
            playlist.name = new_name
            playlist.start_time = start_time
            playlist.end_time = end_time
            return True
        logger.warning(f"Playlist '{old_name}' not found.")
        return False

    def delete_playlist(self, name):
        """Deletes the playlist with the specified name."""
        self.playlists = [p for p in self.playlists if p.name != name]

    def to_dict(self):
        return {
            "playlists": [p.to_dict() for p in self.playlists],
            "active_playlist": self.active_playlist
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            playlists=[Playlist.from_dict(p) for p in data.get("playlists", [])],
            active_playlist=data.get("active_playlist")
        )

    @staticmethod
    def should_refresh(latest_refresh, interval_seconds, current_time):
        """Determines whether a refresh should occur on the interval and latest refresh time."""
        if not latest_refresh:
            return True  # No previous refresh, so it's time to refresh

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
        self.plugin_rotation_queue = plugin_rotation_queue or []
        self.plugin_rotation_pool = plugin_rotation_pool or []
        self.plugin_rotation_recent_history = plugin_rotation_recent_history or []

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
        return json.dumps([plugin.plugin_id, plugin.name], separators=(",", ":"))

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
            "plugin_rotation_queue": self.plugin_rotation_queue,
            "plugin_rotation_pool": self.plugin_rotation_pool,
            "plugin_rotation_recent_history": self.plugin_rotation_recent_history,
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

    def __init__(self, plugin_id, name, settings, refresh, latest_refresh_time=None):
        self.plugin_id = plugin_id
        self.name = name
        self.settings = settings
        self.refresh = refresh
        self.latest_refresh_time = latest_refresh_time

    def update(self, updated_data):
        """Update attributes of the class with the dictionary values."""
        for key, value in updated_data.items():
            setattr(self, key, value)

    def should_refresh(self, current_time):
        """Checks whether the plugin should be refreshed based on its refresh settings and the current time."""
        latest_refresh_dt = self.get_latest_refresh_dt()
        if not latest_refresh_dt:
            return True

        # Check for interval-based refresh
        if "interval" in self.refresh:
            interval = self.refresh.get("interval")
            if interval and (current_time - latest_refresh_dt) >= timedelta(seconds=interval):
                return True

        if "scheduled" in self.refresh:
            scheduled_time_str = self.refresh.get("scheduled")
            scheduled_time = datetime.strptime(scheduled_time_str, "%H:%M").time()
            latest_refresh_dt = latest_refresh_dt.astimezone(current_time.tzinfo) if current_time.tzinfo else latest_refresh_dt
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
            latest_refresh = datetime.fromisoformat(self.latest_refresh_time)
        return latest_refresh
    
    def to_dict(self):
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "plugin_settings": self.settings,
            "refresh": self.refresh,
            "latest_refresh_time": self.latest_refresh_time,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            plugin_id=data["plugin_id"],
            name=data["name"],
            settings=data["plugin_settings"],
            refresh=data["refresh"],
            latest_refresh_time=data.get("latest_refresh_time"),
        )
