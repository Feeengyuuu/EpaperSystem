import logging
import math
import random
import threading
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping
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


@dataclass(frozen=True)
class ActivePlaylistSnapshot:
    """An immutable view of the playlist selected by current priority."""

    name: str
    start_time: str
    end_time: str
    plugins: tuple[PluginInstanceSnapshot, ...]


@dataclass(frozen=True)
class PlaylistSelectionSnapshot:
    """An immutable selected instance plus its exact playlist membership."""

    playlist_name: str
    instance: PluginInstanceSnapshot


@dataclass(frozen=True)
class PluginInstanceMutationResult:
    """Detached before/after views produced by one atomic mutation."""

    playlist_name: str
    old_snapshot: PluginInstanceSnapshot
    new_snapshot: PluginInstanceSnapshot | None


@dataclass(frozen=True)
class PlaylistDeletionResult:
    """Detached playlist contents removed by one atomic deletion."""

    name: str
    start_time: str
    end_time: str
    removed_instances: tuple[PluginInstanceSnapshot, ...]


@dataclass(frozen=True)
class PlaylistRotationAcknowledgement:
    """Rollback token for one persisted automatic display acknowledgement."""

    playlist_name: str
    instance_uuid: str
    before_state: tuple
    after_state: tuple


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
        # Extends instance create/delete serialization across external cleanup work.
        # This must stay separate from ``_lock`` because plugin cleanup can be slow.
        self._instance_lifecycle_lock = threading.RLock()
        with self._lock:
            self._ensure_unique_instance_uuids()

    @contextmanager
    def instance_lifecycle_guard(self):
        """Serialize instance ownership changes with opaque resource cleanup."""
        with self._instance_lifecycle_lock:
            yield

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

    def snapshot_all_instances(self) -> tuple[PluginInstanceSnapshot, ...]:
        """Return every configured instance in stable playlist order."""

        with self._lock:
            return tuple(
                instance.snapshot()
                for playlist in self.playlists
                for instance in playlist.plugins
            )

    def resolve_plugin_instance_snapshot(
        self,
        playlist_name,
        plugin_id,
        instance_name,
    ) -> PlaylistSelectionSnapshot | None:
        """Resolve a legacy web identity to one detached immutable selection."""
        with self._lock:
            playlists = self.playlists
            if playlist_name is not None:
                playlists = [
                    item for item in self.playlists if item.name == playlist_name
                ]
            for playlist in playlists:
                instance = next(
                    (
                        item
                        for item in playlist.plugins
                        if item.plugin_id == plugin_id
                        and item.name == instance_name
                    ),
                    None,
                )
                if instance is not None:
                    return PlaylistSelectionSnapshot(
                        playlist.name,
                        instance.snapshot(),
                    )
            return None

    def snapshot_active_playlist(
        self,
        current_datetime,
    ) -> ActivePlaylistSnapshot | None:
        """Return a pure immutable snapshot of the current priority winner."""
        with self._lock:
            playlist = self._determine_active_playlist_locked(current_datetime)
            return self._snapshot_playlist(playlist) if playlist else None

    def select_next_active_instance(
        self,
        current_datetime,
        *,
        latest_refresh,
        interval_seconds,
        eligible_instance_uuids=None,
    ) -> PlaylistSelectionSnapshot | None:
        """Atomically choose and rotate the next due active instance."""
        eligible_instance_uuids = self._normalize_eligible_instance_uuids(
            eligible_instance_uuids
        )
        if latest_refresh is not None and not isinstance(latest_refresh, datetime):
            raise ValueError("latest_refresh must be a datetime or None")
        try:
            normalized_interval = float(interval_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("interval_seconds must be finite") from exc
        if not math.isfinite(normalized_interval):
            raise ValueError("interval_seconds must be finite")

        with self._lock:
            playlist = self._determine_active_playlist_locked(current_datetime)
            if playlist is None:
                self.active_playlist = None
                return None

            self.active_playlist = playlist.name
            if not playlist.plugins:
                return None
            if not self.should_refresh(
                latest_refresh,
                normalized_interval,
                current_datetime,
            ):
                return None

            instance = playlist.get_next_plugin(eligible_instance_uuids)
            if instance is None:
                return None
            return PlaylistSelectionSnapshot(playlist.name, instance.snapshot())

    def reserve_next_active_instance(
        self,
        current_datetime,
        *,
        latest_refresh,
        interval_seconds,
        eligible_instance_uuids=None,
    ) -> PlaylistSelectionSnapshot | None:
        """Reserve, but do not consume, one due automatic display candidate."""
        eligible_instance_uuids = self._normalize_eligible_instance_uuids(
            eligible_instance_uuids
        )
        if latest_refresh is not None and not isinstance(latest_refresh, datetime):
            raise ValueError("latest_refresh must be a datetime or None")
        try:
            normalized_interval = float(interval_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("interval_seconds must be finite") from exc
        if not math.isfinite(normalized_interval):
            raise ValueError("interval_seconds must be finite")

        with self._lock:
            playlist = self._determine_active_playlist_locked(current_datetime)
            if playlist is None:
                self.active_playlist = None
                return None

            self.active_playlist = playlist.name
            if not playlist.plugins:
                return None
            if not self.should_refresh(
                latest_refresh,
                normalized_interval,
                current_datetime,
            ):
                return None

            instance = playlist.reserve_next_plugin(eligible_instance_uuids)
            if instance is None:
                starved_since = self._parse_rotation_timestamp(
                    playlist.plugin_rotation_starved_since,
                    current_datetime,
                )
                if starved_since is None:
                    playlist.plugin_rotation_starved_since = (
                        current_datetime.isoformat()
                    )
                    return None
                starved_seconds = (
                    current_datetime - starved_since
                ).total_seconds()
                if starved_seconds < max(3 * normalized_interval, 300.0):
                    return None
                instance = playlist.reserve_next_plugin(
                    eligible_instance_uuids,
                    allow_round_concession=True,
                )
                if instance is None:
                    return None
            playlist.plugin_rotation_starved_since = None
            return PlaylistSelectionSnapshot(playlist.name, instance.snapshot())

    @staticmethod
    def _parse_rotation_timestamp(value, reference):
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return PluginInstance.align_datetime_tz(parsed, reference)

    def validate_rotation_reservation(
        self,
        instance_uuid,
        *,
        expected_playlist_name,
    ) -> bool:
        """Return whether an automatic display still owns this exact reservation."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return False
            playlist = match[0]
            return (
                playlist.name == expected_playlist_name
                and playlist.is_rotation_reservation_current(instance_uuid)
            )

    def acknowledge_rotation_display(
        self,
        instance_uuid,
        *,
        expected_playlist_name,
    ) -> PlaylistRotationAcknowledgement | None:
        """Consume one reserved member only after its playlist display commits."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return None
            playlist = match[0]
            if playlist.name != expected_playlist_name:
                return None
            return playlist.acknowledge_rotation_display(instance_uuid)

    def defer_rotation_reservation(
        self,
        instance_uuid,
        *,
        expected_playlist_name,
    ) -> bool:
        """Move a failed reservation to the round tail without consuming it."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return False
            playlist = match[0]
            if playlist.name != expected_playlist_name:
                return False
            return playlist.defer_rotation_reservation(instance_uuid)

    def rollback_rotation_acknowledgement(
        self,
        acknowledgement: PlaylistRotationAcknowledgement,
    ) -> bool:
        """Restore an acknowledgement only if no later rotation mutation won."""
        if not isinstance(acknowledgement, PlaylistRotationAcknowledgement):
            return False
        with self._lock:
            playlist = next(
                (
                    item
                    for item in self.playlists
                    if item.name == acknowledgement.playlist_name
                ),
                None,
            )
            if playlist is None:
                return False
            return playlist.rollback_rotation_acknowledgement(acknowledgement)

    @staticmethod
    def _normalize_eligible_instance_uuids(values):
        if values is None:
            return None
        if isinstance(values, (str, bytes)):
            raise TypeError("eligible_instance_uuids must be a collection of strings")
        try:
            normalized = frozenset(values)
        except TypeError as exc:
            raise TypeError(
                "eligible_instance_uuids must be a collection of strings"
            ) from exc
        if any(not isinstance(value, str) or not value for value in normalized):
            raise ValueError("eligible_instance_uuids must contain non-empty strings")
        return normalized

    def select_theme_instance(
        self,
        current_datetime,
        *,
        displayed_instance_uuid=None,
        displayed_playlist=None,
        displayed_plugin_id=None,
        displayed_name=None,
        is_eligible: Callable[[PluginInstanceSnapshot], bool] | None = None,
        allow_fallback: bool = True,
    ) -> PlaylistSelectionSnapshot | None:
        """Select eligible theme input, preferring an exact displayed UUID.

        The three legacy display fields are consulted only when UUID is absent.
        That compatibility path is inherently ABA-unsafe and is retained only
        until all callers persist and provide instance UUIDs. Eligibility is
        evaluated against immutable snapshots. The callback runs under the manager
        lock and must be pure and non-blocking. Callback exceptions propagate; any
        fallback rotation performed before an exception is rolled back. Rotation
        fallback can be disabled.
        """
        with self._lock:
            playlist = self._determine_active_playlist_locked(current_datetime)
            if playlist is None:
                self.active_playlist = None
                return None

            self.active_playlist = playlist.name
            considered_instance_uuids = set()
            if displayed_instance_uuid is not None:
                displayed = next(
                    (
                        instance
                        for instance in playlist.plugins
                        if instance.instance_uuid == displayed_instance_uuid
                    ),
                    None,
                )
                if displayed is not None:
                    displayed_snapshot = displayed.snapshot()
                    if is_eligible is None or is_eligible(displayed_snapshot):
                        return PlaylistSelectionSnapshot(
                            playlist.name,
                            displayed_snapshot,
                        )
                    considered_instance_uuids.add(displayed_snapshot.instance_uuid)
            elif (
                displayed_playlist == playlist.name
                and displayed_plugin_id is not None
                and displayed_name is not None
            ):
                displayed = next(
                    (
                        instance
                        for instance in playlist.plugins
                        if instance.plugin_id == displayed_plugin_id
                        and instance.name == displayed_name
                    ),
                    None,
                )
                if displayed is not None:
                    displayed_snapshot = displayed.snapshot()
                    if is_eligible is None or is_eligible(displayed_snapshot):
                        return PlaylistSelectionSnapshot(
                            playlist.name,
                            displayed_snapshot,
                        )
                    considered_instance_uuids.add(displayed_snapshot.instance_uuid)

            if not allow_fallback:
                return None
            if not playlist.plugins:
                return None
            rotation_state = (
                playlist.current_plugin_index,
                list(playlist.plugin_rotation_queue),
                list(playlist.plugin_rotation_pool),
                list(playlist.plugin_rotation_recent_history),
            )
            unique_candidate_count = len({
                instance.instance_uuid for instance in playlist.plugins
            })
            for _ in range(2 * len(playlist.plugins)):
                fallback = playlist.get_next_plugin()
                if fallback is None:
                    return None
                if fallback.instance_uuid in considered_instance_uuids:
                    if len(considered_instance_uuids) == unique_candidate_count:
                        return None
                    continue
                fallback_snapshot = fallback.snapshot()
                considered_instance_uuids.add(fallback_snapshot.instance_uuid)
                try:
                    fallback_is_eligible = (
                        is_eligible is None or is_eligible(fallback_snapshot)
                    )
                except BaseException:
                    (
                        playlist.current_plugin_index,
                        playlist.plugin_rotation_queue,
                        playlist.plugin_rotation_pool,
                        playlist.plugin_rotation_recent_history,
                    ) = rotation_state
                    raise
                if fallback_is_eligible:
                    return PlaylistSelectionSnapshot(
                        playlist.name,
                        fallback_snapshot,
                    )
                if len(considered_instance_uuids) == unique_candidate_count:
                    return None
            return None

    def validate_instance_revision(
        self,
        instance_uuid,
        *,
        expected_generation,
        expected_settings_revision,
    ) -> PluginInstanceSnapshot | None:
        """Return a snapshot only for an exact UUID and revision match."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return None
            instance = match[2]
            if (
                instance.structural_generation != expected_generation
                or instance.settings_revision != expected_settings_revision
            ):
                return None
            return instance.snapshot()

    def validate_selection(
        self,
        instance_uuid,
        *,
        expected_playlist_name,
        expected_generation,
        expected_settings_revision,
        current_datetime,
        require_active=True,
    ) -> PlaylistSelectionSnapshot | None:
        """Validate exact revision, membership, and commit-time priority."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return None
            playlist, _index, instance = match
            if playlist.name != expected_playlist_name:
                return None
            if (
                instance.structural_generation != expected_generation
                or instance.settings_revision != expected_settings_revision
            ):
                return None
            if require_active:
                active = self._determine_active_playlist_locked(current_datetime)
                if active is not playlist:
                    return None
            return PlaylistSelectionSnapshot(playlist.name, instance.snapshot())

    def record_instance_refresh(
        self,
        instance_uuid,
        *,
        expected_generation,
        expected_settings_revision,
        expected_latest_refresh_time,
        latest_refresh_time,
    ) -> PluginInstanceSnapshot | None:
        """CAS-update only the latest refresh timestamp for an exact instance."""
        self._validate_refresh_timestamp(latest_refresh_time)
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return None
            instance = match[2]
            if (
                instance.structural_generation != expected_generation
                or instance.settings_revision != expected_settings_revision
                or instance.latest_refresh_time != expected_latest_refresh_time
            ):
                return None
            instance.latest_refresh_time = latest_refresh_time
            return instance.snapshot()

    def first_instance_uuid(self) -> str | None:
        """Return the first UUID in deterministic playlist/plugin order."""
        with self._lock:
            for playlist in self.playlists:
                if playlist.plugins:
                    return playlist.plugins[0].instance_uuid
            return None

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

    def update_plugin_instance_atomic(
        self,
        instance_uuid,
        *,
        settings=None,
        refresh=None,
        name=None,
        expected_generation,
        expected_settings_revision,
    ) -> PluginInstanceMutationResult | None:
        """CAS-update an instance and return immutable before/after snapshots."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return None

            playlist, _index, instance = match
            if (
                instance.structural_generation != expected_generation
                or instance.settings_revision != expected_settings_revision
            ):
                return None

            old_snapshot = instance.snapshot()
            settings_changed = settings is not None and settings != instance.settings
            refresh_changed = refresh is not None and refresh != instance.refresh
            updated_name = str(name) if name is not None else instance.name
            name_changed = name is not None and updated_name != instance.name

            if settings_changed:
                instance.settings = deepcopy(settings)
            if refresh_changed:
                instance.refresh = deepcopy(refresh)
            if name_changed:
                instance.name = updated_name
            if settings_changed or refresh_changed or name_changed:
                instance.settings_revision += 1

            return PluginInstanceMutationResult(
                playlist_name=playlist.name,
                old_snapshot=old_snapshot,
                new_snapshot=instance.snapshot(),
            )

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

    def delete_plugin_instance_atomic(
        self,
        instance_uuid,
        *,
        expected_generation,
        expected_settings_revision,
    ) -> PluginInstanceMutationResult | None:
        """CAS-delete an instance and return its detached prior snapshot."""
        with self._lock:
            match = self._find_instance_by_uuid(instance_uuid)
            if not match:
                return None

            playlist, index, instance = match
            if (
                instance.structural_generation != expected_generation
                or instance.settings_revision != expected_settings_revision
            ):
                return None

            old_snapshot = instance.snapshot()
            playlist.plugins.pop(index)
            return PluginInstanceMutationResult(
                playlist_name=playlist.name,
                old_snapshot=old_snapshot,
                new_snapshot=None,
            )

    def determine_active_playlist(self, current_datetime):
        """Determine the active playlist based on the current time."""
        with self._lock:
            return self._determine_active_playlist_locked(current_datetime)

    def get_playlist(self, playlist_name):
        """Returns the playlist with the specified name."""
        with self._lock:
            return next((p for p in self.playlists if p.name == playlist_name), None)

    def add_plugin_to_playlist(self, playlist_name, plugin_data):
        """Adds a plugin to a playlist by the specified name. Returns true if successfully added,
        False if playlist doesn't exist"""
        with self.instance_lifecycle_guard():
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
                        # Runtime creation is a new identity boundary. Never trust a
                        # caller-provided UUID, even when it is not currently live:
                        # accepting it could reopen a delete/recreate ABA window.
                        forbidden_uuids = set(other_uuids)
                        forbidden_uuids.add(str(added_instance.instance_uuid))
                        added_instance.instance_uuid = self._new_instance_uuid(
                            forbidden_uuids
                        )
                        return True
                else:
                    logger.warning(f"Playlist '{playlist_name}' not found.")
                return False

    def add_plugin_to_playlist_snapshot(
        self,
        playlist_name,
        plugin_data,
    ) -> PlaylistSelectionSnapshot | None:
        """Add an instance and return its detached runtime identity."""
        with self.instance_lifecycle_guard():
            with self._lock:
                playlist = next(
                    (item for item in self.playlists if item.name == playlist_name),
                    None,
                )
                if playlist is None:
                    logger.warning("Playlist '%s' not found.", playlist_name)
                    return None
                if any(
                    instance.plugin_id == plugin_data.get("plugin_id")
                    and instance.name == plugin_data.get("name")
                    for current_playlist in self.playlists
                    for instance in current_playlist.plugins
                ):
                    logger.warning(
                        "Plugin '%s' with instance '%s' already exists.",
                        plugin_data.get("plugin_id"),
                        plugin_data.get("name"),
                    )
                    return None
                if not playlist.add_plugin(plugin_data):
                    return None

                added_instance = playlist.plugins[-1]
                other_uuids = {
                    instance.instance_uuid
                    for current_playlist in self.playlists
                    for instance in current_playlist.plugins
                    if instance is not added_instance
                }
                forbidden_uuids = set(other_uuids)
                forbidden_uuids.add(str(added_instance.instance_uuid))
                added_instance.instance_uuid = self._new_instance_uuid(forbidden_uuids)
                return PlaylistSelectionSnapshot(
                    playlist.name,
                    added_instance.snapshot(),
                )

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

    def delete_playlist_atomic(self, name) -> PlaylistDeletionResult | None:
        """Delete one playlist and return every removed instance snapshot."""
        with self._lock:
            for index, playlist in enumerate(self.playlists):
                if playlist.name != name:
                    continue
                removed = PlaylistDeletionResult(
                    name=playlist.name,
                    start_time=playlist.start_time,
                    end_time=playlist.end_time,
                    removed_instances=tuple(
                        instance.snapshot() for instance in playlist.plugins
                    ),
                )
                self.playlists.pop(index)
                return removed
            return None

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

    def _determine_active_playlist_locked(self, current_datetime):
        current_time = current_datetime.strftime("%H:%M")
        active_playlists = [
            playlist
            for playlist in self.playlists
            if playlist.is_active(current_time)
        ]
        if not active_playlists:
            return None
        return min(active_playlists, key=lambda playlist: playlist.get_priority())

    @staticmethod
    def _snapshot_playlist(playlist):
        return ActivePlaylistSnapshot(
            name=playlist.name,
            start_time=playlist.start_time,
            end_time=playlist.end_time,
            plugins=tuple(instance.snapshot() for instance in playlist.plugins),
        )

    @staticmethod
    def _validate_refresh_timestamp(latest_refresh_time):
        if not isinstance(latest_refresh_time, str) or not latest_refresh_time:
            raise ValueError("latest_refresh_time must be an ISO datetime string")
        try:
            datetime.fromisoformat(latest_refresh_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "latest_refresh_time must be an ISO datetime string"
            ) from exc

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
        try:
            interval_seconds = float(interval_seconds)
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                "Invalid refresh interval '%s'; refreshing now.",
                interval_seconds,
            )
            return True
        if not math.isfinite(interval_seconds):
            logger.warning(
                "Invalid refresh interval '%s'; refreshing now.",
                interval_seconds,
            )
            return True
        if interval_seconds <= 0:
            return True
        if not latest_refresh:
            return True  # No previous refresh, so it's time to refresh

        latest_refresh = PluginInstance.align_datetime_tz(latest_refresh, current_time)
        elapsed_seconds = (current_time - latest_refresh).total_seconds()
        return elapsed_seconds >= interval_seconds

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
        plugin_rotation_starved_since=None,
    ):
        self.name = name
        self.start_time = start_time
        self.end_time = end_time
        self.plugins = [PluginInstance.from_dict(p) for p in (plugins or [])]
        self.current_plugin_index = current_plugin_index
        self.plugin_rotation_queue = list(plugin_rotation_queue or [])
        self.plugin_rotation_pool = list(plugin_rotation_pool or [])
        self.plugin_rotation_recent_history = list(plugin_rotation_recent_history or [])
        self.plugin_rotation_starved_since = plugin_rotation_starved_since
        # Reservations are process-local. The persisted queue remains unchanged
        # until a successful playlist DISPLAY commit acknowledges the member.
        self._plugin_rotation_reserved_key = None

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

    def get_next_plugin(self, eligible_instance_uuids=None):
        """Return the next plugin from a shuffled no-repeat rotation bag."""
        # This legacy API consumes immediately. Any outstanding automatic
        # reservation must become stale before that separate mutation proceeds.
        self._plugin_rotation_reserved_key = None
        if eligible_instance_uuids is not None:
            eligible_instance_uuids = frozenset(eligible_instance_uuids)
            if not eligible_instance_uuids:
                return None
        if not self.plugins:
            self.current_plugin_index = None
            self.plugin_rotation_queue = []
            self.plugin_rotation_pool = []
            self.plugin_rotation_recent_history = []
            return None

        if eligible_instance_uuids is None:
            eligible_plugins = self.plugins
        else:
            eligible_plugins = [
                plugin
                for plugin in self.plugins
                if plugin.instance_uuid in eligible_instance_uuids
            ]
            if not eligible_plugins:
                return None

        if len(eligible_plugins) == 1:
            only_plugin = eligible_plugins[0]
            self.current_plugin_index = self.plugins.index(only_plugin)
            self.plugin_rotation_queue = []
            only_key = self._plugin_rotation_key(only_plugin)
            self.plugin_rotation_pool = [only_key]
            self.plugin_rotation_recent_history = [only_key]
            return self.plugins[self.current_plugin_index]

        plugin_keys = [
            self._plugin_rotation_key(plugin) for plugin in eligible_plugins
        ]
        current_key = None
        if isinstance(self.current_plugin_index, int) and 0 <= self.current_plugin_index < len(self.plugins):
            indexed_key = self._plugin_rotation_key(
                self.plugins[self.current_plugin_index]
            )
            if indexed_key in plugin_keys:
                current_key = indexed_key

        started_new_round = False
        if self.plugin_rotation_pool != plugin_keys:
            if eligible_instance_uuids is None:
                self.plugin_rotation_queue = []
            else:
                previous_pool = set(self.plugin_rotation_pool)
                queue = self._dedupe_rotation_keys(
                    key
                    for key in self.plugin_rotation_queue
                    if key in plugin_keys
                )
                newly_eligible = [
                    key
                    for key in plugin_keys
                    if key not in previous_pool and key not in queue
                ]
                random.shuffle(newly_eligible)
                queue.extend(newly_eligible)
                self.plugin_rotation_queue = queue
                started_new_round = not previous_pool
            self.plugin_rotation_pool = list(plugin_keys)

        queue = self._dedupe_rotation_keys(
            key for key in self.plugin_rotation_queue if key in plugin_keys
        )
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
        self.current_plugin_index = next(
            index
            for index, plugin in enumerate(self.plugins)
            if self._plugin_rotation_key(plugin) == next_key
        )
        self.plugin_rotation_recent_history = self._updated_recent_history(next_key, recent_history, len(plugin_keys))

        return self.plugins[self.current_plugin_index]

    def reserve_next_plugin(
        self,
        eligible_instance_uuids=None,
        *,
        allow_round_concession=False,
    ):
        """Reserve an eligible member from the full persisted shuffle bag.

        Eligibility is only an admission filter: ineligible configured members
        remain in the current round. Selection does not remove the reservation
        from ``plugin_rotation_queue``; acknowledgement after display commit does.

        ``allow_round_concession`` ends a round early when every remaining
        member is ineligible: a round that can never finish is a rotation
        deadlock, not fairness, so the caller may bound how long it waits.
        """
        if eligible_instance_uuids is not None:
            eligible_instance_uuids = frozenset(eligible_instance_uuids)
            if not eligible_instance_uuids:
                return None
        if not self.plugins:
            self.current_plugin_index = None
            self.plugin_rotation_queue = []
            self.plugin_rotation_pool = []
            self.plugin_rotation_recent_history = []
            self._plugin_rotation_reserved_key = None
            return None

        plugin_keys = [self._plugin_rotation_key(plugin) for plugin in self.plugins]
        if eligible_instance_uuids is not None and not any(
            key in eligible_instance_uuids for key in plugin_keys
        ):
            return None

        self._reconcile_automatic_rotation_bag(plugin_keys)
        if not self.plugin_rotation_queue:
            self.plugin_rotation_queue = list(plugin_keys)
            random.shuffle(self.plugin_rotation_queue)
            self._avoid_automatic_round_boundary_repeat()

        reserved_key = self._plugin_rotation_reserved_key
        if (
            reserved_key not in self.plugin_rotation_queue
            or (
                eligible_instance_uuids is not None
                and reserved_key not in eligible_instance_uuids
            )
        ):
            reserved_key = None
            self._plugin_rotation_reserved_key = None

        if reserved_key is None:
            reserved_key = next(
                (
                    key
                    for key in self.plugin_rotation_queue
                    if eligible_instance_uuids is None
                    or key in eligible_instance_uuids
                ),
                None,
            )
            if reserved_key is None:
                if not allow_round_concession:
                    # The round still contains configured members, but none
                    # currently has a valid cache. Do not refill and do not
                    # discard them.
                    return None
                # Bounded starvation concession: complete the blocked round and
                # start a fresh one so eligible members keep rotating.
                self.plugin_rotation_queue = list(plugin_keys)
                random.shuffle(self.plugin_rotation_queue)
                self._avoid_automatic_round_boundary_repeat()
                reserved_key = next(
                    (
                        key
                        for key in self.plugin_rotation_queue
                        if eligible_instance_uuids is None
                        or key in eligible_instance_uuids
                    ),
                    None,
                )
                if reserved_key is None:
                    return None
            self._plugin_rotation_reserved_key = reserved_key

        return next(
            plugin
            for plugin in self.plugins
            if self._plugin_rotation_key(plugin) == reserved_key
        )

    def is_rotation_reservation_current(self, instance_uuid):
        return (
            self._plugin_rotation_reserved_key == instance_uuid
            and instance_uuid in self.plugin_rotation_queue
            and any(
                self._plugin_rotation_key(plugin) == instance_uuid
                for plugin in self.plugins
            )
        )

    def acknowledge_rotation_display(self, instance_uuid):
        """Remove exactly one current reservation and return a rollback token."""
        before_state = self._automatic_rotation_state()
        plugin_keys = [self._plugin_rotation_key(plugin) for plugin in self.plugins]
        self._reconcile_automatic_rotation_bag(plugin_keys)
        if not self.is_rotation_reservation_current(instance_uuid):
            self._restore_automatic_rotation_state(before_state)
            return None

        self.plugin_rotation_queue.remove(instance_uuid)
        self.current_plugin_index = next(
            index
            for index, plugin in enumerate(self.plugins)
            if self._plugin_rotation_key(plugin) == instance_uuid
        )
        recent_history = self._dedupe_rotation_keys(
            key
            for key in self.plugin_rotation_recent_history
            if key in plugin_keys
        )
        self.plugin_rotation_recent_history = self._updated_recent_history(
            instance_uuid,
            recent_history,
            len(plugin_keys),
        )
        self._plugin_rotation_reserved_key = None
        after_state = self._automatic_rotation_state()
        return PlaylistRotationAcknowledgement(
            playlist_name=self.name,
            instance_uuid=instance_uuid,
            before_state=before_state,
            after_state=after_state,
        )

    def defer_rotation_reservation(self, instance_uuid):
        """Retry a failed member after the other members in this shuffle round."""
        plugin_keys = [self._plugin_rotation_key(plugin) for plugin in self.plugins]
        self._reconcile_automatic_rotation_bag(plugin_keys)
        if not self.is_rotation_reservation_current(instance_uuid):
            return False
        self.plugin_rotation_queue.remove(instance_uuid)
        self.plugin_rotation_queue.append(instance_uuid)
        self._plugin_rotation_reserved_key = None
        return True

    def rollback_rotation_acknowledgement(self, acknowledgement):
        if (
            acknowledgement.playlist_name != self.name
            or self._automatic_rotation_state() != acknowledgement.after_state
        ):
            return False
        self._restore_automatic_rotation_state(acknowledgement.before_state)
        return True

    def _reconcile_automatic_rotation_bag(self, plugin_keys):
        configured = set(plugin_keys)
        previous_pool = self._dedupe_rotation_keys(
            key for key in self.plugin_rotation_pool if key in configured
        )
        initialized_new_round = not previous_pool
        remaining = self._dedupe_rotation_keys(
            key for key in self.plugin_rotation_queue if key in configured
        )
        newly_configured = [
            key for key in plugin_keys if key not in set(previous_pool)
        ]
        if not previous_pool:
            remaining = list(plugin_keys)
            random.shuffle(remaining)
        elif newly_configured:
            random.shuffle(newly_configured)
            remaining.extend(
                key for key in newly_configured if key not in remaining
            )

        self.plugin_rotation_pool = list(plugin_keys)
        self.plugin_rotation_queue = remaining
        self.plugin_rotation_recent_history = self._dedupe_rotation_keys(
            key for key in self.plugin_rotation_recent_history if key in configured
        )[: self._recent_history_max_size(len(plugin_keys))]
        if initialized_new_round:
            self._avoid_automatic_round_boundary_repeat()
        if self._plugin_rotation_reserved_key not in remaining:
            self._plugin_rotation_reserved_key = None

    def _avoid_automatic_round_boundary_repeat(self):
        if len(self.plugin_rotation_queue) < 2:
            return
        current_key = None
        if (
            isinstance(self.current_plugin_index, int)
            and 0 <= self.current_plugin_index < len(self.plugins)
        ):
            current_key = self._plugin_rotation_key(
                self.plugins[self.current_plugin_index]
            )
        if current_key is None and self.plugin_rotation_recent_history:
            current_key = self.plugin_rotation_recent_history[0]
        if current_key is None or self.plugin_rotation_queue[0] != current_key:
            return
        replacement_index = next(
            (
                index
                for index, key in enumerate(self.plugin_rotation_queue[1:], start=1)
                if key != current_key
            ),
            None,
        )
        if replacement_index is not None:
            self.plugin_rotation_queue[0], self.plugin_rotation_queue[replacement_index] = (
                self.plugin_rotation_queue[replacement_index],
                self.plugin_rotation_queue[0],
            )

    def _automatic_rotation_state(self):
        return (
            self.current_plugin_index,
            tuple(self.plugin_rotation_queue),
            tuple(self.plugin_rotation_pool),
            tuple(self.plugin_rotation_recent_history),
            self._plugin_rotation_reserved_key,
        )

    def _restore_automatic_rotation_state(self, state):
        (
            self.current_plugin_index,
            queue,
            pool,
            recent_history,
            self._plugin_rotation_reserved_key,
        ) = state
        self.plugin_rotation_queue = list(queue)
        self.plugin_rotation_pool = list(pool)
        self.plugin_rotation_recent_history = list(recent_history)

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
            "plugin_rotation_starved_since": self.plugin_rotation_starved_since,
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
            plugin_rotation_starved_since=data.get(
                "plugin_rotation_starved_since",
                None,
            ),
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
        refresh = deepcopy(data["refresh"])
        interval = refresh.get("interval") if isinstance(refresh, dict) else None
        if interval is not None and not isinstance(interval, bool):
            try:
                normalized_interval = float(interval)
            except (TypeError, ValueError, OverflowError):
                normalized_interval = None
            if (
                normalized_interval is not None
                and normalized_interval <= 0
            ):
                logger.warning(
                    "Legacy non-positive refresh interval for plugin '%s' "
                    "instance '%s' normalized to 60 seconds: %r",
                    data.get("plugin_id"),
                    data.get("name"),
                    interval,
                )
                refresh["interval"] = 60
        return cls(
            plugin_id=data["plugin_id"],
            name=data["name"],
            settings=data["plugin_settings"],
            refresh=refresh,
            latest_refresh_time=data.get("latest_refresh_time"),
            instance_uuid=data.get("instance_uuid"),
            structural_generation=data.get("structural_generation", 1),
            settings_revision=data.get("settings_revision", 1),
        )
