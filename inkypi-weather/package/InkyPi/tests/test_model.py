import threading
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.model import Playlist, PlaylistManager, PluginInstance, RefreshInfo


class MutableSettingsLeaf:
    def __init__(self, values):
        self.values = list(values)

class TestPlaylist:

    @pytest.mark.parametrize(
        "start,end,current,expected,priority",
        [
            # --- Non-wrapping cases 09:00 <-> 15:00 ---
            ("09:00", "15:00", "08:59", False, 360),  # just before start
            ("09:00", "15:00", "09:00", True, 360),   # exactly at start
            ("09:00", "15:00", "12:00", True, 360),   # during
            ("09:00", "15:00", "14:59", True, 360),   # just before end
            ("09:00", "15:00", "15:00", False, 360),  # exactly at end
            ("09:00", "15:00", "23:00", False, 360),  # way after
    
            # --- Wrapping cases (crossing midnight) 21:00 <-> 03:00 ---
            ("21:00", "03:00", "20:59", False, 360),  # just before start
            ("21:00", "03:00", "21:00", True, 360),   # exactly at start
            ("21:00", "03:00", "23:59", True, 360),   # before midnight
            ("21:00", "03:00", "00:00", True, 360),   # after midnight, inside
            ("21:00", "03:00", "02:59", True, 360),   # just before end
            ("21:00", "03:00", "03:00", False, 360),  # exactly at end
            ("21:00", "03:00", "11:00", False, 360),  # way after
    
            # --- Equal start and end 12:00 <-> 12:00 ---
            ("12:00", "12:00", "11:59", False, 0),
            ("12:00", "12:00", "12:00", False, 0),
            ("12:00", "12:00", "12:01", False, 0),
    
            # --- Midnight boundaries 18:00 <-> 00:00 ---
            ("18:00", "00:00", "17:59", False, 360),  # before start
            ("18:00", "00:00", "23:59", True, 360),   # before end
            ("18:00", "00:00", "00:00", False, 360),  # exactly at end
    
            # --- Midnight boundaries 00:00 <-> 06:00 ---
            ("00:00", "06:00", "00:00", True, 360),   # start at midnight
            ("00:00", "06:00", "05:59", True, 360),   # before end
            ("00:00", "06:00", "06:00", False, 360),  # exactly at end

            # --- All day 00:00 <-> 24:00 ---
            ("00:00", "24:00", "00:00", True, 1440),   # exactly at start
            ("00:00", "24:00", "10:00", True, 1440),   # during
            ("00:00", "24:00", "24:00", False, 1440),  # exactly at end
        ]
    )
    def test_is_active_and_priority(self, start, end, current, expected, priority):
        playlist = Playlist("Test Playlist", start, end)
        assert playlist.is_active(current) == expected
        assert playlist.get_priority() == priority

    def test_get_next_plugin_shuffled_queue_without_immediate_repeat(self, monkeypatch):
        playlist = Playlist(
            "Test Playlist",
            "00:00",
            "24:00",
            plugins=[
                {"plugin_id": "one", "name": "One", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "two", "name": "Two", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "three", "name": "Three", "plugin_settings": {}, "refresh": {"interval": 300}},
            ],
            current_plugin_index=1,
        )

        def fake_shuffle(items):
            items[:] = [items[1], items[2], items[0]]

        monkeypatch.setattr("src.model.random.shuffle", fake_shuffle)

        plugin = playlist.get_next_plugin()

        assert plugin.name == "Three"
        assert playlist.current_plugin_index == 2
        assert playlist.plugin_rotation_queue == [
            playlist._plugin_rotation_key(playlist.plugins[1]),
            playlist._plugin_rotation_key(playlist.plugins[0]),
        ]

    def test_get_next_plugin_single_item(self):
        playlist = Playlist(
            "Test Playlist",
            "00:00",
            "24:00",
            plugins=[
                {"plugin_id": "one", "name": "One", "plugin_settings": {}, "refresh": {"interval": 300}},
            ],
            current_plugin_index=0,
        )

        assert playlist.get_next_plugin().name == "One"
        assert playlist.current_plugin_index == 0
        assert playlist.plugin_rotation_recent_history == [
            playlist._plugin_rotation_key(playlist.plugins[0])
        ]

    def test_get_next_plugin_uses_every_plugin_before_repeating(self, monkeypatch):
        playlist = Playlist(
            "Test Playlist",
            "00:00",
            "24:00",
            plugins=[
                {"plugin_id": "one", "name": "One", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "two", "name": "Two", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "three", "name": "Three", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "four", "name": "Four", "plugin_settings": {}, "refresh": {"interval": 300}},
            ],
        )

        monkeypatch.setattr("src.model.random.shuffle", lambda items: items.reverse())

        first_round = [playlist.get_next_plugin().name for _ in range(4)]
        second_round_first = playlist.get_next_plugin().name

        assert len(first_round) == len(set(first_round))
        assert set(first_round) == {"One", "Two", "Three", "Four"}
        assert second_round_first != first_round[-1]

    def test_get_next_plugin_persists_queue_through_dict_roundtrip(self, monkeypatch):
        playlist = Playlist(
            "Test Playlist",
            "00:00",
            "24:00",
            plugins=[
                {"plugin_id": "one", "name": "One", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "two", "name": "Two", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "three", "name": "Three", "plugin_settings": {}, "refresh": {"interval": 300}},
            ],
        )

        monkeypatch.setattr("src.model.random.shuffle", lambda items: items.reverse())

        selected_before_write = playlist.get_next_plugin().name
        restored = Playlist.from_dict(playlist.to_dict())
        remaining_after_write = [restored.get_next_plugin().name for _ in range(2)]

        assert selected_before_write == "Three"
        assert remaining_after_write == ["Two", "One"]

    def test_get_next_plugin_uses_remaining_random_pool_before_refill(self, monkeypatch):
        playlist = Playlist(
            "Test Playlist",
            "00:00",
            "24:00",
            plugins=[
                {"plugin_id": "one", "name": "One", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "two", "name": "Two", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "three", "name": "Three", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "four", "name": "Four", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "five", "name": "Five", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "six", "name": "Six", "plugin_settings": {}, "refresh": {"interval": 300}},
            ],
            current_plugin_index=4,
        )
        playlist.plugin_rotation_pool = [
            playlist._plugin_rotation_key(plugin) for plugin in playlist.plugins
        ]
        playlist.plugin_rotation_queue = [
            playlist._plugin_rotation_key(playlist.plugins[2]),
            playlist._plugin_rotation_key(playlist.plugins[5]),
        ]

        monkeypatch.setattr("src.model.random.shuffle", lambda items: None)

        plugin = playlist.get_next_plugin()

        assert plugin.name == "Three"
        assert playlist.plugin_rotation_queue == [
            playlist._plugin_rotation_key(playlist.plugins[5]),
        ]
        assert playlist.plugin_rotation_recent_history == [
            playlist._plugin_rotation_key(playlist.plugins[2]),
        ]

    def test_get_next_plugin_refills_random_pool_after_all_plugins_displayed(self, monkeypatch):
        playlist = Playlist(
            "Test Playlist",
            "00:00",
            "24:00",
            plugins=[
                {"plugin_id": "one", "name": "One", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "two", "name": "Two", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "three", "name": "Three", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "four", "name": "Four", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "five", "name": "Five", "plugin_settings": {}, "refresh": {"interval": 300}},
                {"plugin_id": "six", "name": "Six", "plugin_settings": {}, "refresh": {"interval": 300}},
            ],
            current_plugin_index=5,
            plugin_rotation_queue=[],
            plugin_rotation_recent_history=[
                '["six","Six"]',
                '["five","Five"]',
                '["four","Four"]',
                '["three","Three"]',
                '["two","Two"]',
            ],
        )

        def fake_shuffle(items):
            items[:] = [
                playlist._plugin_rotation_key(playlist.plugins[5]),
                playlist._plugin_rotation_key(playlist.plugins[4]),
                playlist._plugin_rotation_key(playlist.plugins[3]),
                playlist._plugin_rotation_key(playlist.plugins[2]),
                playlist._plugin_rotation_key(playlist.plugins[1]),
                playlist._plugin_rotation_key(playlist.plugins[0]),
            ]

        monkeypatch.setattr("src.model.random.shuffle", fake_shuffle)

        plugin = playlist.get_next_plugin()

        assert plugin.name == "Five"
        assert playlist.plugin_rotation_queue == [
            playlist._plugin_rotation_key(playlist.plugins[5]),
            playlist._plugin_rotation_key(playlist.plugins[3]),
            playlist._plugin_rotation_key(playlist.plugins[2]),
            playlist._plugin_rotation_key(playlist.plugins[1]),
            playlist._plugin_rotation_key(playlist.plugins[0]),
        ]
        assert playlist.plugin_rotation_recent_history == [
            playlist._plugin_rotation_key(playlist.plugins[4])
        ]


class TestPlaylistManager:

    def test_from_dict_handles_missing_config(self):
        manager = PlaylistManager.from_dict(None)

        assert manager.playlists == []
        assert manager.active_playlist is None

    def test_should_refresh_accepts_string_interval_and_aligns_timezone(self):
        latest_refresh = datetime(2026, 5, 26, 7, 0)
        current_time = datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc)

        assert PlaylistManager.should_refresh(latest_refresh, "300", current_time)

    @pytest.mark.parametrize(
        "interval_seconds",
        [None, "invalid", float("nan"), float("inf"), float("-inf")],
    )
    def test_should_refresh_legacy_invalid_or_nonfinite_interval_is_due(
        self,
        interval_seconds,
    ):
        now = datetime(2026, 7, 9, 12, 0)

        assert PlaylistManager.should_refresh(
            now - timedelta(seconds=1),
            interval_seconds,
            now,
        )


class TestPlaylistManagerIdentity:

    @staticmethod
    def _manager_with_home_plugin(settings=None):
        return PlaylistManager.from_dict({
            "playlists": [{
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [{
                    "plugin_id": "weather",
                    "name": "Home",
                    "plugin_settings": settings or {},
                    "refresh": {"interval": 300},
                }],
            }],
        })

    def test_delete_and_same_name_recreate_cannot_match_old_snapshot(self):
        manager = self._manager_with_home_plugin()
        old = manager.find_plugin("weather", "Home")
        old_snapshot = manager.snapshot_instance(old.instance_uuid)

        removed = manager.delete_plugin_instance(old.instance_uuid)
        assert manager.add_plugin_to_playlist("Default", {
            "plugin_id": "weather",
            "name": "Home",
            "plugin_settings": {},
            "refresh": {"interval": 300},
        })
        new = manager.find_plugin("weather", "Home")

        assert removed.instance_uuid == old_snapshot.instance_uuid
        assert new.instance_uuid != old_snapshot.instance_uuid
        assert manager.snapshot_instance(old_snapshot.instance_uuid) is None

    def test_snapshot_is_deeply_immutable_and_isolated_from_updates(self):
        manager = self._manager_with_home_plugin({
            "appearance": {"theme": "dark", "accents": ["red", "blue"]},
        })
        instance = manager.find_plugin("weather", "Home")
        snapshot = manager.snapshot_instance(instance.instance_uuid)

        with pytest.raises(TypeError):
            snapshot.settings["appearance"]["theme"] = "light"
        with pytest.raises(TypeError):
            snapshot.settings["appearance"]["accents"][0] = "green"
        with pytest.raises(TypeError):
            snapshot.refresh["interval"] = 600

        updated = manager.update_plugin_instance(
            instance.instance_uuid,
            settings={"appearance": {"theme": "light", "accents": ["green"]}},
        )

        assert updated.settings["appearance"]["theme"] == "light"
        assert snapshot.settings["appearance"]["theme"] == "dark"
        assert snapshot.settings["appearance"]["accents"] == ("red", "blue")

    def test_snapshot_update_rejects_stale_generation(self):
        manager = self._manager_with_home_plugin({"units": "metric"})
        instance = manager.find_plugin("weather", "Home")
        before = manager.snapshot_instance(instance.instance_uuid)

        result = manager.update_plugin_instance(
            instance.instance_uuid,
            settings={"units": "imperial"},
            expected_generation=before.structural_generation + 1,
        )

        assert result is None
        assert manager.snapshot_instance(instance.instance_uuid) == before

    def test_snapshot_update_rejects_stale_settings_revision(self):
        manager = self._manager_with_home_plugin({"units": "metric"})
        instance = manager.find_plugin("weather", "Home")
        before = manager.snapshot_instance(instance.instance_uuid)

        result = manager.update_plugin_instance(
            instance.instance_uuid,
            settings={"units": "imperial"},
            expected_settings_revision=before.settings_revision + 1,
        )

        assert result is None
        assert manager.snapshot_instance(instance.instance_uuid) == before

    def test_snapshot_delete_rejects_stale_generation(self):
        manager = self._manager_with_home_plugin()
        instance = manager.find_plugin("weather", "Home")
        before = manager.snapshot_instance(instance.instance_uuid)

        result = manager.delete_plugin_instance(
            instance.instance_uuid,
            expected_generation=before.structural_generation + 1,
        )

        assert result is None
        assert manager.snapshot_instance(instance.instance_uuid) == before

    def test_snapshot_noop_update_does_not_advance_settings_revision(self):
        manager = self._manager_with_home_plugin({"units": "metric"})
        instance = manager.find_plugin("weather", "Home")
        before = manager.snapshot_instance(instance.instance_uuid)

        after = manager.update_plugin_instance(
            instance.instance_uuid,
            settings={"units": "metric"},
            refresh={"interval": 300},
            name="Home",
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
        )

        assert after.settings_revision == before.settings_revision
        assert after.structural_generation == before.structural_generation

    @pytest.mark.parametrize(
        "changes",
        [
            {"settings": {"units": "imperial"}},
            {"refresh": {"interval": 600}},
            {"name": "Away"},
        ],
    )
    def test_snapshot_effective_update_advances_settings_revision_once(self, changes):
        manager = self._manager_with_home_plugin({"units": "metric"})
        instance = manager.find_plugin("weather", "Home")
        before = manager.snapshot_instance(instance.instance_uuid)

        after = manager.update_plugin_instance(instance.instance_uuid, **changes)

        assert after.settings_revision == before.settings_revision + 1
        assert after.structural_generation == before.structural_generation

    def test_manager_replaces_duplicate_serialized_uuids(self):
        manager = PlaylistManager.from_dict({
            "playlists": [
                {
                    "name": "Morning",
                    "start_time": "00:00",
                    "end_time": "12:00",
                    "plugins": [{
                        "plugin_id": "weather",
                        "name": "Home",
                        "plugin_settings": {},
                        "refresh": {"interval": 300},
                        "instance_uuid": "duplicate-instance-uuid",
                    }],
                },
                {
                    "name": "Evening",
                    "start_time": "12:00",
                    "end_time": "24:00",
                    "plugins": [{
                        "plugin_id": "clock",
                        "name": "Clock",
                        "plugin_settings": {},
                        "refresh": {"interval": 60},
                        "instance_uuid": "duplicate-instance-uuid",
                    }],
                },
            ],
        })

        instances = [plugin for playlist in manager.playlists for plugin in playlist.plugins]
        uuids = [plugin.instance_uuid for plugin in instances]
        restored = PlaylistManager.from_dict(manager.to_dict())
        restored_uuids = [
            plugin.instance_uuid
            for playlist in restored.playlists
            for plugin in playlist.plugins
        ]

        assert uuids[0] == "duplicate-instance-uuid"
        assert len(set(uuids)) == 2
        assert restored_uuids == uuids

    def test_uuid_rotation_resets_legacy_identity_bag(self, monkeypatch):
        legacy_keys = ['["one","One"]', '["two","Two"]']
        playlist = Playlist.from_dict({
            "name": "Default",
            "start_time": "00:00",
            "end_time": "24:00",
            "plugins": [
                {"plugin_id": "one", "name": "One", "plugin_settings": {}, "refresh": {}},
                {"plugin_id": "two", "name": "Two", "plugin_settings": {}, "refresh": {}},
            ],
            "plugin_rotation_queue": list(legacy_keys),
            "plugin_rotation_pool": list(legacy_keys),
            "plugin_rotation_recent_history": list(reversed(legacy_keys)),
        })
        monkeypatch.setattr("src.model.random.shuffle", lambda items: None)

        selected = playlist.get_next_plugin()
        uuid_keys = [plugin.instance_uuid for plugin in playlist.plugins]

        assert selected.instance_uuid == uuid_keys[0]
        assert playlist.plugin_rotation_pool == uuid_keys
        assert playlist.plugin_rotation_queue == uuid_keys[1:]
        assert playlist.plugin_rotation_recent_history == [uuid_keys[0]]

    def test_from_dict_detaches_snapshot_and_rotation_state_from_input(self):
        source = {
            "playlists": [{
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [{
                    "plugin_id": "weather",
                    "name": "Home",
                    "plugin_settings": {"appearance": {"theme": "dark"}},
                    "refresh": {"interval": 300, "days": ["monday"]},
                    "instance_uuid": "home-instance",
                }],
                "plugin_rotation_queue": ["home-instance"],
                "plugin_rotation_pool": ["home-instance"],
                "plugin_rotation_recent_history": ["home-instance"],
            }],
        }
        manager = PlaylistManager.from_dict(source)
        instance = manager.find_plugin("weather", "Home")
        before = manager.snapshot_instance(instance.instance_uuid)

        source_playlist = source["playlists"][0]
        source_plugin = source_playlist["plugins"][0]
        source_plugin["plugin_settings"]["appearance"]["theme"] = "light"
        source_plugin["refresh"]["days"].append("tuesday")
        source_playlist["plugin_rotation_queue"].append("external")
        source_playlist["plugin_rotation_pool"].append("external")
        source_playlist["plugin_rotation_recent_history"].append("external")

        after = manager.snapshot_instance(instance.instance_uuid)
        live_playlist = manager.get_playlist("Default")
        assert after.settings_revision == before.settings_revision
        assert after == before
        assert live_playlist.plugin_rotation_queue == ["home-instance"]
        assert live_playlist.plugin_rotation_pool == ["home-instance"]
        assert live_playlist.plugin_rotation_recent_history == ["home-instance"]

    def test_add_plugin_detaches_snapshot_from_plugin_data(self):
        manager = PlaylistManager.from_dict({
            "playlists": [{
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [],
            }],
        })
        plugin_data = {
            "plugin_id": "weather",
            "name": "Home",
            "plugin_settings": {"appearance": {"theme": "dark"}},
            "refresh": {"interval": 300, "days": ["monday"]},
        }
        assert manager.add_plugin_to_playlist("Default", plugin_data)
        instance = manager.find_plugin("weather", "Home")
        before = manager.snapshot_instance(instance.instance_uuid)

        plugin_data["plugin_settings"]["appearance"]["theme"] = "light"
        plugin_data["refresh"]["days"].append("tuesday")

        after = manager.snapshot_instance(instance.instance_uuid)
        assert after.settings_revision == before.settings_revision
        assert after == before

    def test_to_dict_detaches_snapshot_and_rotation_state_from_result(self):
        manager = self._manager_with_home_plugin({
            "appearance": {"theme": "dark"},
        })
        instance = manager.find_plugin("weather", "Home")
        playlist = manager.get_playlist("Default")
        playlist.plugin_rotation_queue = [instance.instance_uuid]
        playlist.plugin_rotation_pool = [instance.instance_uuid]
        playlist.plugin_rotation_recent_history = [instance.instance_uuid]
        before = manager.snapshot_instance(instance.instance_uuid)

        candidate = manager.to_dict()
        candidate_playlist = candidate["playlists"][0]
        candidate_plugin = candidate_playlist["plugins"][0]
        candidate_plugin["plugin_settings"]["appearance"]["theme"] = "light"
        candidate_plugin["refresh"]["interval"] = 600
        candidate_playlist["plugin_rotation_queue"].append("external")
        candidate_playlist["plugin_rotation_pool"].append("external")
        candidate_playlist["plugin_rotation_recent_history"].append("external")

        after = manager.snapshot_instance(instance.instance_uuid)
        assert after.settings_revision == before.settings_revision
        assert after == before
        assert playlist.plugin_rotation_queue == [instance.instance_uuid]
        assert playlist.plugin_rotation_pool == [instance.instance_uuid]
        assert playlist.plugin_rotation_recent_history == [instance.instance_uuid]

    def test_duplicate_uuid_on_add_rotates_new_identity_and_roundtrip_is_stable(self):
        manager = PlaylistManager.from_dict({
            "playlists": [{
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [{
                    "plugin_id": "weather",
                    "name": "Home",
                    "plugin_settings": {},
                    "refresh": {"interval": 300},
                    "instance_uuid": "shared-instance",
                }],
            }],
        })
        plugin_data = {
            "plugin_id": "clock",
            "name": "Clock",
            "plugin_settings": {},
            "refresh": {"interval": 60},
            "instance_uuid": "shared-instance",
        }

        assert manager.add_plugin_to_playlist("Default", plugin_data)
        existing = manager.find_plugin("weather", "Home")
        added = manager.find_plugin("clock", "Clock")
        current_uuids = [existing.instance_uuid, added.instance_uuid]
        serialized = manager.to_dict()
        restored = PlaylistManager.from_dict(serialized)
        restored_uuids = [
            plugin.instance_uuid
            for plugin in restored.get_playlist("Default").plugins
        ]

        assert existing.instance_uuid == "shared-instance"
        assert added.instance_uuid != existing.instance_uuid
        assert restored_uuids == current_uuids
        assert restored.to_dict() == serialized

    def test_compatibility_update_detaches_snapshot_from_updated_data(self):
        playlist = Playlist(
            "Default",
            "00:00",
            "24:00",
            plugins=[{
                "plugin_id": "weather",
                "name": "Home",
                "plugin_settings": {},
                "refresh": {},
            }],
        )
        updated_data = {
            "settings": {"appearance": {"theme": "dark"}},
            "refresh": {"interval": 300, "days": ["monday"]},
        }
        assert playlist.update_plugin("weather", "Home", updated_data)
        instance = playlist.find_plugin("weather", "Home")
        before = instance.snapshot()

        updated_data["settings"]["appearance"]["theme"] = "light"
        updated_data["refresh"]["days"].append("tuesday")

        assert instance.snapshot() == before


class TestRefreshInfo:

    def test_from_dict_handles_missing_config(self):
        refresh_info = RefreshInfo.from_dict(None)

        assert refresh_info.refresh_time is None
        assert refresh_info.image_hash is None

    def test_get_refresh_datetime_ignores_invalid_timestamp(self):
        refresh_info = RefreshInfo.from_dict({"refresh_time": "not-a-time"})

        assert refresh_info.get_refresh_datetime() is None


class TestPluginInstance:

    def test_legacy_plugin_instance_receives_stable_uuid_and_revisions(self):
        instance = PluginInstance.from_dict({
            "plugin_id": "weather",
            "name": "Home",
            "plugin_settings": {},
            "refresh": {"interval": 300},
        })

        serialized = instance.to_dict()
        restored = PluginInstance.from_dict(serialized)

        assert serialized["instance_uuid"] == instance.instance_uuid
        assert restored.instance_uuid == instance.instance_uuid
        assert restored.structural_generation == 1
        assert restored.settings_revision == 1

    @pytest.mark.parametrize("field_name", ["settings", "refresh"])
    def test_snapshot_rejects_unsupported_custom_mutable_leaf_stably(
        self,
        field_name,
    ):
        leaf = MutableSettingsLeaf(["original"])
        values = {"leaf": leaf}
        instance = PluginInstance(
            "weather",
            "Home",
            settings=values if field_name == "settings" else {},
            refresh=values if field_name == "refresh" else {},
        )

        with pytest.raises(TypeError):
            instance.snapshot()
        with pytest.raises(TypeError):
            instance.snapshot()

        assert leaf.values == ["original"]

    def test_scheduled_refresh_waits_until_scheduled_time_today(self):
        plugin = PluginInstance(
            "daily_ai_news",
            "Daily AI News",
            settings={},
            refresh={"scheduled": "07:30"},
            latest_refresh_time="2026-05-26T01:50:00+00:00",
        )

        assert not plugin.should_refresh(datetime(2026, 5, 26, 2, 0, tzinfo=timezone.utc))

    def test_scheduled_refresh_runs_after_scheduled_time_today(self):
        plugin = PluginInstance(
            "daily_ai_news",
            "Daily AI News",
            settings={},
            refresh={"scheduled": "07:30"},
            latest_refresh_time="2026-05-26T01:50:00+00:00",
        )

        assert plugin.should_refresh(datetime(2026, 5, 26, 7, 31, tzinfo=timezone.utc))

    def test_scheduled_refresh_does_not_repeat_after_today_schedule(self):
        plugin = PluginInstance(
            "daily_ai_news",
            "Daily AI News",
            settings={},
            refresh={"scheduled": "07:30"},
            latest_refresh_time="2026-05-26T07:31:00+00:00",
        )

        assert not plugin.should_refresh(datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc))

    def test_interval_refresh_uses_plugin_interval(self):
        plugin = PluginInstance(
            "clock",
            "Clock",
            settings={},
            refresh={"interval": 300},
            latest_refresh_time="2026-05-26T07:00:00+00:00",
        )

        assert plugin.should_refresh(datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc))

    def test_invalid_latest_refresh_time_refreshes_instead_of_crashing(self):
        plugin = PluginInstance(
            "clock",
            "Clock",
            settings={},
            refresh={"interval": 300},
            latest_refresh_time="not-a-time",
        )

        assert plugin.should_refresh(datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc))

    def test_invalid_scheduled_refresh_is_ignored(self):
        plugin = PluginInstance(
            "clock",
            "Clock",
            settings={},
            refresh={"scheduled": "bad"},
            latest_refresh_time="2026-05-26T07:00:00+00:00",
        )

        assert not plugin.should_refresh(datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc))
        

class TestPlaylistManagerConcurrency:

    def _plugin_dict(self, name="Instance"):
        return {"plugin_id": "clock", "name": name, "plugin_settings": {}, "refresh": {"interval": 300}}

    def test_add_playlist_rejects_duplicate_name(self):
        manager = PlaylistManager()
        assert manager.add_playlist("Morning") is True
        assert manager.add_playlist("Morning") is False
        assert manager.get_playlist_names() == ["Morning"]

    def test_update_playlist_rejects_rename_collision(self):
        manager = PlaylistManager()
        manager.add_playlist("Morning")
        manager.add_playlist("Evening")
        assert manager.update_playlist("Morning", "Evening", "06:00", "12:00") is False
        assert manager.get_playlist("Morning") is not None
        # renaming to the same name is still allowed (time-only update)
        assert manager.update_playlist("Morning", "Morning", "06:00", "12:00") is True

    def test_concurrent_add_plugin_creates_single_instance(self):
        import threading

        manager = PlaylistManager()
        manager.add_playlist("Default")
        barrier = threading.Barrier(8)
        results = []

        def worker():
            barrier.wait()
            results.append(manager.add_plugin_to_playlist("Default", self._plugin_dict()))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert results.count(True) == 1
        assert len(manager.get_playlist("Default").plugins) == 1

    def test_concurrent_add_playlist_creates_single_playlist(self):
        import threading

        manager = PlaylistManager()
        barrier = threading.Barrier(8)
        results = []

        def worker():
            barrier.wait()
            results.append(manager.add_playlist("Race"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert results.count(True) == 1
        assert manager.get_playlist_names() == ["Race"]


class TestPlaylistManagerSchedulerSnapshots:

    @staticmethod
    def _plugin(
        name,
        *,
        plugin_id="clock",
        instance_uuid=None,
        settings=None,
        refresh=None,
        latest_refresh_time=None,
    ):
        data = {
            "plugin_id": plugin_id,
            "name": name,
            "plugin_settings": settings or {},
            "refresh": {"interval": 300} if refresh is None else refresh,
            "latest_refresh_time": latest_refresh_time,
        }
        if instance_uuid is not None:
            data["instance_uuid"] = instance_uuid
        return data

    @staticmethod
    def _playlist(
        name,
        start="00:00",
        end="24:00",
        plugins=None,
        **rotation,
    ):
        return {
            "name": name,
            "start_time": start,
            "end_time": end,
            "plugins": list(plugins or []),
            **rotation,
        }

    @classmethod
    def _manager(cls, *playlists, active_playlist=None):
        return PlaylistManager.from_dict(
            {
                "playlists": list(playlists),
                "active_playlist": active_playlist,
            }
        )

    @staticmethod
    def _rotation_state(manager):
        return tuple(
            (
                playlist.current_plugin_index,
                tuple(playlist.plugin_rotation_queue),
                tuple(playlist.plugin_rotation_pool),
                tuple(playlist.plugin_rotation_recent_history),
            )
            for playlist in manager.playlists
        )

    def test_active_playlist_snapshot_is_deeply_immutable_detached_and_pure(self):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin(
                        "Home",
                        plugin_id="weather",
                        instance_uuid="home-uuid",
                        settings={
                            "appearance": {
                                "theme": "dark",
                                "accents": ["red", "blue"],
                            }
                        },
                        refresh={"interval": 300, "days": ["monday"]},
                    )
                ],
                current_plugin_index=0,
                plugin_rotation_queue=["home-uuid"],
                plugin_rotation_pool=["home-uuid"],
                plugin_rotation_recent_history=["home-uuid"],
            ),
            active_playlist="unchanged-sentinel",
        )
        before_rotation = self._rotation_state(manager)

        snapshot = manager.snapshot_active_playlist(datetime(2026, 7, 9, 12, 0))

        assert snapshot.name == "Default"
        assert isinstance(snapshot.plugins, tuple)
        assert snapshot.plugins[0].instance_uuid == "home-uuid"
        with pytest.raises(FrozenInstanceError):
            snapshot.name = "Mutated"
        with pytest.raises(TypeError):
            snapshot.plugins[0].settings["appearance"]["theme"] = "light"
        with pytest.raises(TypeError):
            snapshot.plugins[0].refresh["days"][0] = "tuesday"

        manager.update_plugin_instance(
            "home-uuid",
            settings={"appearance": {"theme": "light", "accents": ["green"]}},
        )
        assert snapshot.plugins[0].settings["appearance"]["theme"] == "dark"
        assert snapshot.plugins[0].settings["appearance"]["accents"] == (
            "red",
            "blue",
        )
        assert manager.active_playlist == "unchanged-sentinel"
        assert self._rotation_state(manager) == before_rotation

    def test_active_playlist_snapshot_uses_cross_midnight_priority_and_insertion_order(
        self,
    ):
        manager = self._manager(
            self._playlist("All Day", plugins=[self._plugin("All Day")]),
            self._playlist(
                "First Night",
                "21:00",
                "03:00",
                [self._plugin("First Night")],
            ),
            self._playlist(
                "Second Night",
                "22:00",
                "04:00",
                [self._plugin("Second Night")],
            ),
        )

        snapshot = manager.snapshot_active_playlist(datetime(2026, 7, 10, 0, 30))

        assert snapshot.name == "First Night"
        assert snapshot.plugins[0].name == "First Night"

    def test_select_next_active_no_active_clears_active_without_rotation(self):
        manager = self._manager(
            self._playlist(
                "Morning",
                "06:00",
                "07:00",
                [self._plugin("Clock", instance_uuid="morning-uuid")],
                current_plugin_index=0,
                plugin_rotation_queue=["morning-uuid"],
                plugin_rotation_pool=["morning-uuid"],
                plugin_rotation_recent_history=["morning-uuid"],
            ),
            active_playlist="Morning",
        )
        before_rotation = self._rotation_state(manager)

        selected = manager.select_next_active_instance(
            datetime(2026, 7, 9, 8, 0),
            latest_refresh=None,
            interval_seconds=300,
        )

        assert selected is None
        assert manager.active_playlist is None
        assert self._rotation_state(manager) == before_rotation

    def test_select_next_active_empty_records_active_without_rotation(self):
        manager = self._manager(
            self._playlist(
                "Empty",
                plugins=[],
                current_plugin_index=7,
                plugin_rotation_queue=["stale-queue"],
                plugin_rotation_pool=["stale-pool"],
                plugin_rotation_recent_history=["stale-history"],
            ),
            active_playlist="old-name",
        )
        before_rotation = self._rotation_state(manager)

        selected = manager.select_next_active_instance(
            datetime(2026, 7, 9, 12, 0),
            latest_refresh=None,
            interval_seconds=300,
        )

        assert selected is None
        assert manager.active_playlist == "Empty"
        assert self._rotation_state(manager) == before_rotation

    def test_select_next_active_not_due_records_active_without_rotation(self):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[self._plugin("Clock", instance_uuid="clock-uuid")],
                current_plugin_index=0,
                plugin_rotation_queue=["clock-uuid"],
                plugin_rotation_pool=["clock-uuid"],
                plugin_rotation_recent_history=["clock-uuid"],
            ),
            active_playlist="old-name",
        )
        before_rotation = self._rotation_state(manager)
        now = datetime(2026, 7, 9, 12, 0)

        selected = manager.select_next_active_instance(
            now,
            latest_refresh=now - timedelta(seconds=10),
            interval_seconds=300,
        )

        assert selected is None
        assert manager.active_playlist == "Default"
        assert self._rotation_state(manager) == before_rotation

    def test_select_next_active_huge_finite_interval_is_not_due_without_rotation(
        self,
    ):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[self._plugin("Clock", instance_uuid="clock-uuid")],
                current_plugin_index=0,
                plugin_rotation_queue=["clock-uuid"],
                plugin_rotation_pool=["clock-uuid"],
                plugin_rotation_recent_history=["clock-uuid"],
            ),
            active_playlist="old-name",
        )
        before_rotation = self._rotation_state(manager)
        now = datetime(2026, 7, 9, 12, 0)

        selected = manager.select_next_active_instance(
            now,
            latest_refresh=now - timedelta(days=1),
            interval_seconds=1e308,
        )

        assert selected is None
        assert manager.active_playlist == "Default"
        assert self._rotation_state(manager) == before_rotation

    @pytest.mark.parametrize(
        "interval_seconds",
        [None, "invalid", float("nan"), float("inf"), float("-inf")],
    )
    def test_select_next_active_rejects_invalid_or_nonfinite_interval(
        self,
        interval_seconds,
    ):
        manager = self._manager(
            self._playlist("Default", plugins=[self._plugin("Clock")]),
            active_playlist="sentinel",
        )
        before_rotation = self._rotation_state(manager)

        with pytest.raises(ValueError):
            manager.select_next_active_instance(
                datetime(2026, 7, 9, 12, 0),
                latest_refresh=None,
                interval_seconds=interval_seconds,
            )

        assert manager.active_playlist == "sentinel"
        assert self._rotation_state(manager) == before_rotation

    def test_select_next_active_requires_datetime_or_none_latest_refresh(self):
        manager = self._manager(
            self._playlist("Default", plugins=[self._plugin("Clock")])
        )
        before_rotation = self._rotation_state(manager)

        with pytest.raises(ValueError):
            manager.select_next_active_instance(
                datetime(2026, 7, 9, 12, 0),
                latest_refresh="2026-07-09T11:00:00",
                interval_seconds=300,
            )

        assert self._rotation_state(manager) == before_rotation

    @pytest.mark.parametrize("interval_seconds", [0, -1, -300.5])
    def test_select_next_active_zero_or_negative_interval_is_due(
        self,
        interval_seconds,
    ):
        manager = self._manager(
            self._playlist("Default", plugins=[self._plugin("Clock")])
        )
        now = datetime(2026, 7, 9, 12, 0)

        selected = manager.select_next_active_instance(
            now,
            latest_refresh=now,
            interval_seconds=interval_seconds,
        )

        assert selected.playlist_name == "Default"
        assert selected.instance.name == "Clock"

    def test_select_next_active_uses_cross_midnight_priority(self, monkeypatch):
        manager = self._manager(
            self._playlist("All Day", plugins=[self._plugin("All Day")]),
            self._playlist(
                "Night",
                "21:00",
                "03:00",
                [self._plugin("Night")],
            ),
        )
        monkeypatch.setattr("src.model.random.shuffle", lambda items: None)

        selected = manager.select_next_active_instance(
            datetime(2026, 7, 10, 0, 30),
            latest_refresh=None,
            interval_seconds=300,
        )

        assert selected.playlist_name == "Night"
        assert selected.instance.name == "Night"
        assert manager.active_playlist == "Night"

    def test_select_next_active_concurrent_due_calls_choose_different_uuids(
        self,
        monkeypatch,
    ):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin("One", instance_uuid="one-uuid"),
                    self._plugin("Two", instance_uuid="two-uuid"),
                ],
            )
        )
        monkeypatch.setattr("src.model.random.shuffle", lambda items: None)
        barrier = threading.Barrier(3)
        results = []
        failures = []

        def select_due():
            try:
                barrier.wait()
                results.append(
                    manager.select_next_active_instance(
                        datetime(2026, 7, 9, 12, 0),
                        latest_refresh=None,
                        interval_seconds=300,
                    )
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        threads = [threading.Thread(target=select_due) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=1.0)

        assert not failures
        assert all(not thread.is_alive() for thread in threads)
        assert {result.instance.instance_uuid for result in results} == {
            "one-uuid",
            "two-uuid",
        }

    def test_select_theme_exact_uuid_is_primary_and_does_not_rotate(self):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin("One", instance_uuid="one-uuid"),
                    self._plugin("Two", instance_uuid="two-uuid"),
                ],
                current_plugin_index=0,
                plugin_rotation_queue=["two-uuid"],
                plugin_rotation_pool=["one-uuid", "two-uuid"],
                plugin_rotation_recent_history=["one-uuid"],
            )
        )
        before_rotation = self._rotation_state(manager)

        selected = manager.select_theme_instance(
            datetime(2026, 7, 9, 12, 0),
            displayed_instance_uuid="two-uuid",
            displayed_playlist="wrong-playlist",
            displayed_plugin_id="wrong-plugin",
            displayed_name="wrong-name",
        )

        assert selected.instance.instance_uuid == "two-uuid"
        assert selected.playlist_name == "Default"
        assert self._rotation_state(manager) == before_rotation

    def test_select_theme_stale_uuid_recreate_falls_back_exactly_once(self):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin(
                        "Home",
                        plugin_id="weather",
                        instance_uuid="old-home-uuid",
                    )
                ],
            )
        )
        old = manager.snapshot_instance("old-home-uuid")
        assert manager.delete_plugin_instance(old.instance_uuid) is not None
        assert manager.add_plugin_to_playlist(
            "Default",
            self._plugin("Home", plugin_id="weather"),
        )
        recreated = manager.find_plugin("weather", "Home")
        assert recreated.instance_uuid != old.instance_uuid
        playlist = manager.get_playlist("Default")
        original_get_next = playlist.get_next_plugin
        calls = []

        def counted_get_next():
            calls.append(None)
            return original_get_next()

        playlist.get_next_plugin = counted_get_next

        selected = manager.select_theme_instance(
            datetime(2026, 7, 9, 12, 0),
            displayed_instance_uuid=old.instance_uuid,
            displayed_playlist="Default",
            displayed_plugin_id="weather",
            displayed_name="Home",
        )

        assert selected.instance.instance_uuid == recreated.instance_uuid
        assert len(calls) == 1
        assert playlist.plugin_rotation_recent_history == [recreated.instance_uuid]

    def test_explicit_old_uuid_recreate_cannot_reopen_theme_validation_or_record_aba(
        self,
        monkeypatch,
    ):
        old_uuid = "stable-explicit-uuid"
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin(
                        "Home",
                        plugin_id="weather",
                        instance_uuid=old_uuid,
                    )
                ],
                current_plugin_index=0,
                plugin_rotation_queue=[],
                plugin_rotation_pool=[old_uuid],
                plugin_rotation_recent_history=[old_uuid],
            )
        )
        old = manager.snapshot_instance(old_uuid)
        assert manager.delete_plugin_instance(old_uuid) is not None
        generated_uuids = iter((old_uuid, "fresh-manager-uuid"))
        monkeypatch.setattr(
            "src.model.uuid4",
            lambda: SimpleNamespace(hex=next(generated_uuids)),
        )
        explicit_recreate = self._plugin(
            "Home",
            plugin_id="weather",
            instance_uuid=old_uuid,
        )
        explicit_recreate["structural_generation"] = old.structural_generation
        explicit_recreate["settings_revision"] = old.settings_revision
        assert manager.add_plugin_to_playlist("Default", explicit_recreate)
        recreated = manager.find_plugin("weather", "Home")

        assert recreated.instance_uuid != old_uuid
        themed = manager.select_theme_instance(
            datetime(2026, 7, 9, 12, 0),
            displayed_instance_uuid=old_uuid,
            displayed_playlist="Default",
            displayed_plugin_id="weather",
            displayed_name="Home",
        )
        assert themed.instance.instance_uuid == recreated.instance_uuid
        playlist = manager.get_playlist("Default")
        assert playlist.plugin_rotation_pool == [recreated.instance_uuid]
        assert playlist.plugin_rotation_recent_history == [recreated.instance_uuid]
        assert manager.validate_instance_revision(
            old_uuid,
            expected_generation=old.structural_generation,
            expected_settings_revision=old.settings_revision,
        ) is None
        assert manager.validate_selection(
            old_uuid,
            expected_playlist_name="Default",
            expected_generation=old.structural_generation,
            expected_settings_revision=old.settings_revision,
            current_datetime=datetime(2026, 7, 9, 12, 0),
        ) is None
        assert manager.record_instance_refresh(
            old_uuid,
            expected_generation=old.structural_generation,
            expected_settings_revision=old.settings_revision,
            expected_latest_refresh_time=None,
            latest_refresh_time="2026-07-09T12:00:00+00:00",
        ) is None
        assert manager.snapshot_instance(recreated.instance_uuid).latest_refresh_time is None

    def test_select_theme_uuid_from_inactive_playlist_falls_back_once(self):
        manager = self._manager(
            self._playlist(
                "Morning",
                "06:00",
                "12:00",
                [self._plugin("Morning", instance_uuid="morning-uuid")],
            ),
            self._playlist(
                "Evening",
                "12:00",
                "18:00",
                [self._plugin("Evening", instance_uuid="evening-uuid")],
            ),
        )
        evening = manager.get_playlist("Evening")
        original_get_next = evening.get_next_plugin
        calls = []

        def counted_get_next():
            calls.append(None)
            return original_get_next()

        evening.get_next_plugin = counted_get_next

        selected = manager.select_theme_instance(
            datetime(2026, 7, 9, 13, 0),
            displayed_instance_uuid="morning-uuid",
            displayed_playlist="Morning",
            displayed_plugin_id="clock",
            displayed_name="Morning",
        )

        assert selected.playlist_name == "Evening"
        assert selected.instance.instance_uuid == "evening-uuid"
        assert len(calls) == 1

    def test_select_theme_legacy_identity_reuses_only_when_uuid_is_absent(self):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin(
                        "Home",
                        plugin_id="weather",
                        instance_uuid="home-uuid",
                    )
                ],
                current_plugin_index=None,
                plugin_rotation_queue=["home-uuid"],
                plugin_rotation_pool=["home-uuid"],
                plugin_rotation_recent_history=[],
            )
        )
        before_rotation = self._rotation_state(manager)

        selected = manager.select_theme_instance(
            datetime(2026, 7, 9, 12, 0),
            displayed_playlist="Default",
            displayed_plugin_id="weather",
            displayed_name="Home",
        )

        assert selected.instance.instance_uuid == "home-uuid"
        assert self._rotation_state(manager) == before_rotation

    def test_validate_instance_revision_requires_exact_nonwildcard_tokens(self):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[self._plugin("Clock", instance_uuid="clock-uuid")],
            )
        )
        snapshot = manager.snapshot_instance("clock-uuid")

        exact = manager.validate_instance_revision(
            snapshot.instance_uuid,
            expected_generation=snapshot.structural_generation,
            expected_settings_revision=snapshot.settings_revision,
        )

        assert exact == snapshot
        assert manager.validate_instance_revision(
            snapshot.instance_uuid,
            expected_generation=None,
            expected_settings_revision=snapshot.settings_revision,
        ) is None
        assert manager.validate_instance_revision(
            snapshot.instance_uuid,
            expected_generation=snapshot.structural_generation + 1,
            expected_settings_revision=snapshot.settings_revision,
        ) is None
        assert manager.validate_instance_revision(
            snapshot.instance_uuid,
            expected_generation=snapshot.structural_generation,
            expected_settings_revision=snapshot.settings_revision + 1,
        ) is None

    def test_validate_selection_accepts_exact_then_rejects_playlist_rename(self):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[self._plugin("Clock", instance_uuid="clock-uuid")],
            )
        )
        now = datetime(2026, 7, 9, 12, 0)
        selected = manager.select_next_active_instance(
            now,
            latest_refresh=None,
            interval_seconds=300,
        )
        instance = selected.instance

        exact = manager.validate_selection(
            instance.instance_uuid,
            expected_playlist_name=selected.playlist_name,
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
            current_datetime=now,
        )
        assert exact == selected
        assert manager.validate_selection(
            instance.instance_uuid,
            expected_playlist_name=selected.playlist_name,
            expected_generation=None,
            expected_settings_revision=instance.settings_revision,
            current_datetime=now,
        ) is None

        assert manager.update_playlist("Default", "Renamed", "00:00", "24:00")
        assert manager.validate_selection(
            instance.instance_uuid,
            expected_playlist_name="Default",
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
            current_datetime=now,
        ) is None

    def test_validate_selection_rejects_move_even_when_active_not_required(self):
        manager = self._manager(
            self._playlist(
                "First",
                plugins=[self._plugin("Clock", instance_uuid="clock-uuid")],
            ),
            self._playlist("Second", plugins=[]),
        )
        snapshot = manager.snapshot_instance("clock-uuid")
        with manager._lock:
            moved = manager.playlists[0].plugins.pop(0)
            manager.playlists[1].plugins.append(moved)

        assert manager.validate_selection(
            snapshot.instance_uuid,
            expected_playlist_name="First",
            expected_generation=snapshot.structural_generation,
            expected_settings_revision=snapshot.settings_revision,
            current_datetime=datetime(2026, 7, 9, 12, 0),
            require_active=False,
        ) is None

    def test_validate_selection_rechecks_commit_time_priority_and_active_window(self):
        manager = self._manager(
            self._playlist(
                "All Day",
                plugins=[self._plugin("Clock", instance_uuid="clock-uuid")],
            )
        )
        noon = datetime(2026, 7, 9, 12, 0)
        selected = manager.select_next_active_instance(
            noon,
            latest_refresh=None,
            interval_seconds=300,
        )
        instance = selected.instance
        assert manager.add_playlist("Priority", "11:00", "13:00")
        assert manager.add_plugin_to_playlist("Priority", self._plugin("Priority"))

        assert manager.validate_selection(
            instance.instance_uuid,
            expected_playlist_name="All Day",
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
            current_datetime=noon,
        ) is None
        assert manager.validate_selection(
            instance.instance_uuid,
            expected_playlist_name="All Day",
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
            current_datetime=datetime(2026, 7, 10, 3, 0),
            require_active=False,
        ).playlist_name == "All Day"

    def test_validate_selection_rejects_no_longer_active_playlist_at_commit_time(
        self,
    ):
        manager = self._manager(
            self._playlist(
                "Morning",
                "06:00",
                "12:00",
                [self._plugin("Clock", instance_uuid="clock-uuid")],
            )
        )
        selected = manager.select_next_active_instance(
            datetime(2026, 7, 9, 10, 0),
            latest_refresh=None,
            interval_seconds=300,
        )
        instance = selected.instance

        assert manager.validate_selection(
            instance.instance_uuid,
            expected_playlist_name="Morning",
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
            current_datetime=datetime(2026, 7, 9, 13, 0),
        ) is None
        assert manager.validate_selection(
            instance.instance_uuid,
            expected_playlist_name="Morning",
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
            current_datetime=datetime(2026, 7, 9, 13, 0),
            require_active=False,
        ).playlist_name == "Morning"

    def test_record_instance_refresh_is_strict_timestamp_cas_and_changes_only_timestamp(
        self,
    ):
        old_timestamp = "2026-07-09T11:00:00+00:00"
        new_timestamp = "2026-07-09T12:00:00+00:00"
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin(
                        "Clock",
                        instance_uuid="clock-uuid",
                        settings={"nested": {"value": 1}},
                        latest_refresh_time=old_timestamp,
                    )
                ],
                current_plugin_index=0,
                plugin_rotation_queue=["clock-uuid"],
                plugin_rotation_pool=["clock-uuid"],
                plugin_rotation_recent_history=["clock-uuid"],
            ),
            active_playlist="Default",
        )
        before = manager.snapshot_instance("clock-uuid")
        before_rotation = self._rotation_state(manager)

        after = manager.record_instance_refresh(
            before.instance_uuid,
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
            expected_latest_refresh_time=old_timestamp,
            latest_refresh_time=new_timestamp,
        )

        assert after.latest_refresh_time == new_timestamp
        assert replace(after, latest_refresh_time=old_timestamp) == before
        assert self._rotation_state(manager) == before_rotation
        assert manager.active_playlist == "Default"
        assert manager.record_instance_refresh(
            before.instance_uuid,
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
            expected_latest_refresh_time=old_timestamp,
            latest_refresh_time="2026-07-09T12:01:00+00:00",
        ) is None
        assert manager.snapshot_instance("clock-uuid") == after

    @pytest.mark.parametrize(
        "invalid_timestamp",
        [None, datetime(2026, 7, 9, 12, 0), "", "not-a-timestamp"],
    )
    def test_record_instance_refresh_rejects_nonserializable_or_invalid_timestamp(
        self,
        invalid_timestamp,
    ):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[self._plugin("Clock", instance_uuid="clock-uuid")],
            )
        )
        before = manager.snapshot_instance("clock-uuid")

        with pytest.raises(ValueError):
            manager.record_instance_refresh(
                before.instance_uuid,
                expected_generation=before.structural_generation,
                expected_settings_revision=before.settings_revision,
                expected_latest_refresh_time=None,
                latest_refresh_time=invalid_timestamp,
            )

        assert manager.snapshot_instance("clock-uuid") == before

    def test_record_instance_refresh_rejects_delete_same_name_recreate_aba(self):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin(
                        "Home",
                        plugin_id="weather",
                        instance_uuid="old-home-uuid",
                    )
                ],
            )
        )
        before = manager.snapshot_instance("old-home-uuid")
        assert manager.delete_plugin_instance(before.instance_uuid) is not None
        assert manager.add_plugin_to_playlist(
            "Default",
            self._plugin("Home", plugin_id="weather"),
        )
        recreated = manager.find_plugin("weather", "Home").snapshot()

        result = manager.record_instance_refresh(
            before.instance_uuid,
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
            expected_latest_refresh_time=None,
            latest_refresh_time="2026-07-09T12:00:00+00:00",
        )

        assert result is None
        assert recreated.instance_uuid != before.instance_uuid
        assert manager.snapshot_instance(recreated.instance_uuid).latest_refresh_time is None

    @pytest.mark.parametrize(
        ("generation_delta", "revision_delta"),
        [(1, 0), (0, 1), (None, 0), (0, None)],
    )
    def test_record_instance_refresh_rejects_stale_or_wildcard_revision_tokens(
        self,
        generation_delta,
        revision_delta,
    ):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[self._plugin("Clock", instance_uuid="clock-uuid")],
            )
        )
        before = manager.snapshot_instance("clock-uuid")
        expected_generation = (
            None
            if generation_delta is None
            else before.structural_generation + generation_delta
        )
        expected_revision = (
            None
            if revision_delta is None
            else before.settings_revision + revision_delta
        )

        assert manager.record_instance_refresh(
            before.instance_uuid,
            expected_generation=expected_generation,
            expected_settings_revision=expected_revision,
            expected_latest_refresh_time=None,
            latest_refresh_time="2026-07-09T12:00:00+00:00",
        ) is None
        assert manager.snapshot_instance("clock-uuid") == before

    def test_record_instance_refresh_concurrent_cas_allows_one_winner(self):
        old_timestamp = "2026-07-09T11:00:00+00:00"
        candidates = {
            "2026-07-09T12:00:00+00:00",
            "2026-07-09T12:01:00+00:00",
        }
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin(
                        "Clock",
                        instance_uuid="clock-uuid",
                        latest_refresh_time=old_timestamp,
                    )
                ],
            )
        )
        before = manager.snapshot_instance("clock-uuid")
        barrier = threading.Barrier(3)
        results = []
        failures = []

        def record(timestamp):
            try:
                barrier.wait()
                results.append(
                    manager.record_instance_refresh(
                        before.instance_uuid,
                        expected_generation=before.structural_generation,
                        expected_settings_revision=before.settings_revision,
                        expected_latest_refresh_time=old_timestamp,
                        latest_refresh_time=timestamp,
                    )
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        threads = [
            threading.Thread(target=record, args=(timestamp,))
            for timestamp in candidates
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=1.0)

        assert not failures
        assert all(not thread.is_alive() for thread in threads)
        assert sum(result is not None for result in results) == 1
        assert manager.snapshot_instance("clock-uuid").latest_refresh_time in candidates

    def test_first_instance_uuid_is_deterministic_skips_empty_and_is_pure(self):
        manager = self._manager(
            self._playlist(
                "Empty",
                plugins=[],
                current_plugin_index=9,
                plugin_rotation_queue=["stale"],
            ),
            self._playlist(
                "Second",
                plugins=[
                    self._plugin("First", instance_uuid="first-uuid"),
                    self._plugin("Second", instance_uuid="second-uuid"),
                ],
            ),
            active_playlist="sentinel",
        )
        before_rotation = self._rotation_state(manager)

        assert manager.first_instance_uuid() == "first-uuid"
        assert manager.first_instance_uuid() == "first-uuid"
        assert manager.active_playlist == "sentinel"
        assert self._rotation_state(manager) == before_rotation
        assert PlaylistManager().first_instance_uuid() is None

    def test_scheduler_snapshot_selection_and_update_concurrency_has_no_deadlock(
        self,
        monkeypatch,
    ):
        manager = self._manager(
            self._playlist(
                "Default",
                plugins=[
                    self._plugin("One", instance_uuid="one-uuid"),
                    self._plugin("Two", instance_uuid="two-uuid"),
                ],
            )
        )
        monkeypatch.setattr("src.model.random.shuffle", lambda items: None)
        before = manager.snapshot_instance("one-uuid")
        barrier = threading.Barrier(4)
        completed = [threading.Event() for _ in range(3)]
        results = {}
        failures = []

        def guarded(name, index, action):
            try:
                barrier.wait()
                results[name] = action()
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                completed[index].set()

        actions = [
            (
                "selection",
                lambda: manager.select_next_active_instance(
                    datetime(2026, 7, 9, 12, 0),
                    latest_refresh=None,
                    interval_seconds=300,
                ),
            ),
            (
                "snapshot",
                lambda: manager.snapshot_active_playlist(
                    datetime(2026, 7, 9, 12, 0)
                ),
            ),
            (
                "update",
                lambda: manager.update_plugin_instance(
                    before.instance_uuid,
                    settings={"updated": True},
                    expected_generation=before.structural_generation,
                    expected_settings_revision=before.settings_revision,
                ),
            ),
        ]
        threads = [
            threading.Thread(
                target=guarded,
                args=(name, index, action),
            )
            for index, (name, action) in enumerate(actions)
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        assert all(event.wait(1.0) for event in completed)
        for thread in threads:
            thread.join(timeout=1.0)

        assert not failures
        assert all(not thread.is_alive() for thread in threads)
        assert results["selection"].instance.instance_uuid in {
            "one-uuid",
            "two-uuid",
        }
        assert results["snapshot"].name == "Default"
        assert results["update"].settings["updated"] is True


class TestPlaylistManagerAtomicWebMutations:
    @staticmethod
    def _manager():
        return PlaylistManager.from_dict({
            "playlists": [{
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [
                    {
                        "plugin_id": "weather",
                        "name": "Home",
                        "plugin_settings": {"units": "metric"},
                        "refresh": {"interval": 300},
                        "instance_uuid": "home-uuid",
                    },
                    {
                        "plugin_id": "clock",
                        "name": "Office",
                        "plugin_settings": {},
                        "refresh": {"scheduled": "08:00"},
                        "instance_uuid": "office-uuid",
                    },
                ],
            }],
        })

    def test_legacy_identity_resolver_returns_detached_immutable_snapshot(self):
        manager = self._manager()

        resolved = manager.resolve_plugin_instance_snapshot(
            "Default",
            "weather",
            "Home",
        )
        manager.update_plugin_instance(
            "home-uuid",
            settings={"units": "imperial"},
        )

        assert resolved.playlist_name == "Default"
        assert resolved.instance.instance_uuid == "home-uuid"
        assert resolved.instance.settings["units"] == "metric"
        with pytest.raises(TypeError):
            resolved.instance.settings["units"] = "mutated"

    def test_legacy_identity_resolver_supports_existing_global_request_shape(self):
        manager = self._manager()

        resolved = manager.resolve_plugin_instance_snapshot(
            None,
            "weather",
            "Home",
        )

        assert resolved.playlist_name == "Default"
        assert resolved.instance.instance_uuid == "home-uuid"

    def test_atomic_update_returns_old_and_new_snapshots_with_one_revision_step(self):
        manager = self._manager()
        before = manager.snapshot_instance("home-uuid")

        mutation = manager.update_plugin_instance_atomic(
            before.instance_uuid,
            settings={"units": "imperial"},
            refresh={"interval": 600},
            name="Home",
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
        )

        assert mutation.playlist_name == "Default"
        assert mutation.old_snapshot == before
        assert mutation.new_snapshot.settings["units"] == "imperial"
        assert mutation.new_snapshot.refresh["interval"] == 600
        assert mutation.new_snapshot.settings_revision == before.settings_revision + 1
        assert manager.snapshot_instance(before.instance_uuid) == mutation.new_snapshot

    def test_atomic_update_rejects_stale_revision_without_mutation(self):
        manager = self._manager()
        before = manager.snapshot_instance("home-uuid")

        result = manager.update_plugin_instance_atomic(
            before.instance_uuid,
            settings={"units": "imperial"},
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision + 1,
        )

        assert result is None
        assert manager.snapshot_instance(before.instance_uuid) == before

    def test_atomic_delete_requires_both_tokens_and_returns_old_snapshot(self):
        manager = self._manager()
        before = manager.snapshot_instance("home-uuid")

        assert manager.delete_plugin_instance_atomic(
            before.instance_uuid,
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision + 1,
        ) is None
        mutation = manager.delete_plugin_instance_atomic(
            before.instance_uuid,
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
        )

        assert mutation.playlist_name == "Default"
        assert mutation.old_snapshot == before
        assert mutation.new_snapshot is None
        assert manager.snapshot_instance(before.instance_uuid) is None

    def test_atomic_playlist_delete_returns_every_removed_snapshot(self):
        manager = self._manager()

        deleted = manager.delete_playlist_atomic("Default")

        assert deleted.name == "Default"
        assert {item.instance_uuid for item in deleted.removed_instances} == {
            "home-uuid",
            "office-uuid",
        }
        assert manager.get_playlist_names() == []
        assert manager.delete_playlist_atomic("Default") is None

    def test_snapshot_add_returns_new_identity_without_exposing_live_instance(self):
        manager = self._manager()
        source = {
            "plugin_id": "news",
            "name": "Headlines",
            "plugin_settings": {"region": "us"},
            "refresh": {"interval": 900},
        }

        added = manager.add_plugin_to_playlist_snapshot("Default", source)
        source["plugin_settings"]["region"] = "mutated"

        assert added.playlist_name == "Default"
        assert added.instance.plugin_id == "news"
        assert added.instance.settings["region"] == "us"
        assert manager.snapshot_instance(added.instance.instance_uuid) == added.instance

    def test_snapshot_add_preserves_global_legacy_identity_uniqueness(self):
        manager = self._manager()
        assert manager.add_playlist("Other")

        result = manager.add_plugin_to_playlist_snapshot("Other", {
            "plugin_id": "weather",
            "name": "Home",
            "plugin_settings": {"units": "replacement"},
            "refresh": {"interval": 300},
        })

        assert result is None
        assert manager.resolve_plugin_instance_snapshot(
            "Other",
            "weather",
            "Home",
        ) is None


@pytest.mark.parametrize(
    "legacy_interval",
    [0, -1, "-5", float("-inf"), "-Infinity"],
)
def test_from_dict_normalizes_legacy_nonpositive_interval_once(
    legacy_interval,
    caplog,
):
    caplog.set_level("WARNING", logger="src.model")

    plugin = PluginInstance.from_dict({
        "plugin_id": "legacy",
        "name": "Legacy",
        "plugin_settings": {},
        "refresh": {"interval": legacy_interval},
        "latest_refresh_time": "2026-07-09T12:00:00+00:00",
    })

    assert plugin.refresh == {"interval": 60}
    assert not plugin.should_refresh(datetime(2026, 7, 9, 12, 0, 30, tzinfo=timezone.utc))
    diagnostics = [
        record
        for record in caplog.records
        if "legacy non-positive refresh interval" in record.getMessage().lower()
    ]
    assert len(diagnostics) == 1
