import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.epaper_pet.epaper_pet import DEFAULT_CONTEXT_PLUGIN_IDS, EpaperPet


def _plugin():
    return EpaperPet({"id": "epaper_pet"})


def test_default_context_plugins_include_new_daily_sources():
    plugin = _plugin()

    assert plugin._context_plugin_ids({}) == DEFAULT_CONTEXT_PLUGIN_IDS
    for plugin_id in (
        "steam_charts",
        "live_radar",
        "daily_word_poem",
        "apod",
        "natgeo_photo_of_the_day",
        "magazine_covers",
        "comic",
        "wpotd",
    ):
        assert plugin_id in DEFAULT_CONTEXT_PLUGIN_IDS


def test_context_items_merge_new_payload_collections_and_fields():
    plugin = _plugin()
    payload = {
        "items": [{"title": "Daily word", "word": "lucid", "definition": "Clear."}],
        "live": [{"platform": "twitch", "owner": "streamer", "title": "Live now"}],
        "games": [{"rank": 1, "name": "Counter-Strike 2", "current_players": "1,234"}],
    }

    items = [plugin._context_item(item) for item in plugin._context_items_from_payload(payload)]

    assert {"title": "Daily word", "word": "lucid", "definition": "Clear."} in items
    assert {"title": "Live now", "platform": "twitch", "owner": "streamer"} in items
    assert {"rank": "1", "name": "Counter-Strike 2", "current_players": "1,234"} in items


def test_hungry_pet_hunts_and_eats_its_catch():
    plugin = _plugin()
    now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    settings = {"autonomous_care": "on", "language": "zh-Hans"}
    state = {
        "pet_id": "test-hunter",
        "name": "Loki",
        "born_at": now.isoformat(),
        "last_tick_at": now.isoformat(),
        "event_index": 3,
        "message": "Quiet heartbeat.",
        "activity": "quiet watch",
        "mood": "hungry",
        "stats": {
            "food": 12,
            "happiness": 50,
            "energy": 70,
            "cleanliness": 80,
            "health": 88,
            "xp": 0,
            "level": 1,
            "age_days": 0,
        },
    }

    assert plugin._apply_autonomous_care(state, settings, now)

    assert state["activity"] == "hunting"
    assert state["message"] == "Autonomy: hunted a small meal and ate it."
    assert state["stats"]["food"] >= 40
    assert state["stats"]["energy"] < 70
    assert state["last_hunt"]["food"]

    plugin._finalize_state(state, settings, now)
    summary = plugin._state_summary(state, settings)

    assert summary["mood_id"] == "hunting"
    assert summary["activity"] == "\u51fa\u53bb\u72e9\u730e"
    assert "\u72e9\u730e" in summary["message"]
