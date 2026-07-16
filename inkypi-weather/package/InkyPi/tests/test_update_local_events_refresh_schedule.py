from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = REPO_ROOT / "tools" / "update_local_events_refresh_schedule.py"


def _load_tool():
    assert TOOL_PATH.is_file(), "Local Events refresh migration tool is missing"
    spec = importlib.util.spec_from_file_location(
        "update_local_events_refresh_schedule",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(refresh):
    return {
        "playlist_config": {
            "playlists": [
                {
                    "name": "DailyDoseOfDay",
                    "plugins": [
                        {
                            "plugin_id": "ticketmaster_events",
                            "name": "DailyShow",
                            "refresh": refresh,
                        },
                        {
                            "plugin_id": "stocktracker",
                            "name": "Money",
                            "refresh": {"scheduled": "13:10"},
                        },
                    ],
                }
            ]
        }
    }


def test_update_local_events_refresh_schedule_replaces_stale_daily_rule_only():
    tool = _load_tool()
    config = _config({"scheduled": "00:00"})

    updated = tool.update_local_events_refresh_schedule(config)

    plugins = config["playlist_config"]["playlists"][0]["plugins"]
    assert updated == [
        "DailyDoseOfDay/ticketmaster_events/DailyShow: "
        "{'scheduled': '00:00'} -> {'interval': 10800}"
    ]
    assert plugins[0]["refresh"] == {"interval": 10_800}
    assert plugins[1]["refresh"] == {"scheduled": "13:10"}


def test_update_local_events_refresh_schedule_is_idempotent():
    tool = _load_tool()
    config = _config({"interval": 10_800})

    assert tool.update_local_events_refresh_schedule(config) == [
        "ticketmaster_events/DailyShow already refreshes every 10800 seconds"
    ]
