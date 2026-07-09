import pytest
from datetime import datetime, timezone

from src.model import Playlist, PlaylistManager, PluginInstance, RefreshInfo

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
