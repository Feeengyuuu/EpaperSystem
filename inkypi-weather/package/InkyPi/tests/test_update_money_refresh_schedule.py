import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = REPO_ROOT / "tools" / "update_money_refresh_schedule.py"
spec = importlib.util.spec_from_file_location("update_money_refresh_schedule", TOOL_PATH)
money_schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(money_schedule)


def test_update_money_refresh_schedule_moves_only_money_to_market_close():
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "DailyDoseOfDay",
                    "plugins": [
                        {
                            "plugin_id": "stocktracker",
                            "name": "Money",
                            "refresh": {"scheduled": "00:00"},
                            "plugin_settings": {"tickers": "AAPL"},
                        },
                        {
                            "plugin_id": "github",
                            "name": "GitHub",
                            "refresh": {"scheduled": "00:00"},
                            "plugin_settings": {},
                        },
                    ],
                }
            ]
        }
    }

    updated = money_schedule.update_money_refresh_schedule(config)

    assert updated == [
        "DailyDoseOfDay/stocktracker/Money: {'scheduled': '00:00'} -> {'scheduled': '13:10'}"
    ]
    assert config["playlist_config"]["playlists"][0]["plugins"][0]["refresh"] == {"scheduled": "13:10"}
    assert config["playlist_config"]["playlists"][0]["plugins"][1]["refresh"] == {"scheduled": "00:00"}


def test_update_money_refresh_schedule_requires_money_instance():
    with pytest.raises(ValueError):
        money_schedule.update_money_refresh_schedule({"playlist_config": {"playlists": []}})


def test_update_money_refresh_schedule_is_idempotent():
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "DailyDoseOfDay",
                    "plugins": [
                        {
                            "plugin_id": "stocktracker",
                            "name": "Money",
                            "refresh": {"scheduled": "13:10"},
                            "plugin_settings": {"tickers": "AAPL"},
                        },
                    ],
                }
            ]
        }
    }

    assert money_schedule.update_money_refresh_schedule(config) == [
        "stocktracker/Money already scheduled at 13:10"
    ]
