import pytest
from datetime import datetime, timezone

from src.model import Playlist, PluginInstance

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


class TestPluginInstance:

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
        
