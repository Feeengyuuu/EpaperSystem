import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.epaper_pet.epaper_pet as pet_module
from plugins.epaper_pet.epaper_pet import DEFAULT_CONTEXT_PLUGIN_IDS, EpaperPet


def _plugin():
    return EpaperPet({"id": "epaper_pet"})


class _FakeDeviceConfig:
    def __init__(self, keys=None, resolution=(800, 480), theme_mode="day"):
        self.keys = keys or {}
        self.resolution = resolution
        self.theme_mode = theme_mode

    def load_env_key(self, key):
        return self.keys.get(key, "")

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "orientation": "horizontal",
            "timezone": "UTC",
            "theme_mode": self.theme_mode,
        }
        if key is None:
            return values
        return values.get(key, default)


def _canonical_theme(mode, *, background, panel, ink, muted, rule, accent):
    palette = {
        "background": background,
        "panel": panel,
        "ink": ink,
        "muted": muted,
        "rule": rule,
        "accent": accent,
    }
    return {"mode": mode, "palette": palette, "css": {}}


def _state(now):
    return {
        "pet_id": "robot-test",
        "name": "Loki",
        "born_at": now.isoformat(),
        "last_tick_at": now.isoformat(),
        "event_index": 4,
        "message": "Quiet heartbeat.",
        "activity": "quiet watch",
        "mood": "calm",
        "stats": {
            "food": 60,
            "happiness": 80,
            "energy": 42,
            "cleanliness": 70,
            "health": 95,
            "xp": 25,
            "level": 3,
            "age_days": 2,
            "food_reserve": 12,
        },
    }


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
    assert state["message"] == "Autonomy: hunted a meal and stored the leftovers."
    assert state["stats"]["food"] > 12
    assert state["stats"]["energy"] < 70
    assert state["last_hunt"]["food"]

    plugin._finalize_state(state, settings, now)
    summary = plugin._state_summary(state, settings)

    assert summary["mood_id"] == "hunting"
    assert summary["activity"] == "\u51fa\u53bb\u72e9\u730e"
    assert "\u72e9\u730e" in summary["message"]


def test_ai_context_includes_daily_visual_pose_library():
    plugin = _plugin()
    now = datetime(2026, 5, 30, 15, 0, tzinfo=timezone.utc)
    settings = {"language": "zh-Hans", "_inkypi_theme": {"mode": "night"}}
    state = {
        "pet_id": "visual-mochi",
        "name": "Mochi",
        "born_at": now.isoformat(),
        "last_tick_at": now.isoformat(),
        "event_index": 8,
        "message": "Had six seconds of brave little chaos.",
        "activity": "tiny zoomies",
        "mood": "zoomies",
        "mood_hint": "zoomies",
        "stats": {
            "food": 70,
            "happiness": 88,
            "energy": 80,
            "cleanliness": 82,
            "health": 95,
            "xp": 42,
            "level": 2,
            "age_days": 1,
        },
        "daily_life": {"date": now.strftime("%Y-%m-%d"), "theme": "mischief"},
    }

    life = plugin._life_context(state, settings, now, state["message"])
    visual = life["visual_state"]

    assert life["daily_life"]["motion_theme"]
    assert life["daily_life"]["body_focus"]
    assert life["daily_life"]["visual_motif"]
    assert visual["identity"]["front_legs"] == "short stubby front legs with compact paws"
    assert visual["current_pose"]["key"] == "zoomies"
    assert visual["current_pose"]["source"] == "activity"
    assert visual["render_style"]["mode"] == "night"
    assert len(visual["pose_library"]) >= 20

    variation = plugin._dialogue_variation(state, now, life, {"available": False, "sources": []}, [])
    assert "visual_state" in variation["must_consider"]
    assert variation["pose_focus"]
    assert variation["daily_motion_theme"]


def test_pet_palette_anchors_all_structural_roles_to_injected_context():
    theme = _canonical_theme(
        "day",
        background=(241, 236, 225),
        panel=(221, 213, 196),
        ink=(19, 21, 23),
        muted=(73, 75, 79),
        rule=(128, 124, 116),
        accent=(180, 44, 58),
    )

    palette = pet_module._pet_palette("happy", theme)

    for role in ("background", "panel", "ink", "muted", "rule", "accent"):
        assert palette[role] == theme["palette"][role]
    assert palette["border"] == theme["palette"]["ink"]
    assert set(palette["bar_colors"]) == {"food", "happiness", "energy", "cleanliness", "health"}


def test_theme_only_redraw_keeps_pet_state_bytes_and_skips_lifecycle(tmp_path, monkeypatch):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    now = datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now", lambda _device_config: now)
    settings = {
        "pet_id": "robot-test",
        "pet_name": "Loki",
        "language": "en",
        "autonomous_care": "on",
        "ai_dialogue": "on",
        "ai_each_render": "on",
        "_theme_render_only": True,
    }
    state = _state(now)
    state["last_event_key"] = "quiet-watch"
    state_file = tmp_path / "robot-test.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    before = state_file.read_bytes()
    calls = {name: 0 for name in ("elapsed", "care", "event", "ai", "finalize", "save")}

    def recorder(name, result=None):
        def record(*_args, **_kwargs):
            calls[name] += 1
            return result

        return record

    monkeypatch.setattr(plugin, "_apply_elapsed", recorder("elapsed", False))
    monkeypatch.setattr(plugin, "_apply_autonomous_care", recorder("care", False))
    monkeypatch.setattr(plugin, "_apply_autonomous_event", recorder("event"))
    monkeypatch.setattr(plugin, "_maybe_generate_ai_message", recorder("ai", False))
    monkeypatch.setattr(plugin, "_finalize_state", recorder("finalize"))
    monkeypatch.setattr(plugin, "_save_state", recorder("save"))
    day = _canonical_theme(
        "day",
        background=(241, 236, 225),
        panel=(221, 213, 196),
        ink=(19, 21, 23),
        muted=(73, 75, 79),
        rule=(128, 124, 116),
        accent=(180, 44, 58),
    )
    night = _canonical_theme(
        "night",
        background=(9, 11, 14),
        panel=(25, 29, 35),
        ink=(244, 246, 248),
        muted=(179, 183, 191),
        rule=(61, 67, 75),
        accent=(72, 186, 234),
    )

    day_image = plugin.generate_image({**settings, "_inkypi_theme": day}, _FakeDeviceConfig(theme_mode="night"))
    night_image = plugin.generate_image({**settings, "_inkypi_theme": night}, _FakeDeviceConfig(theme_mode="day"))

    assert calls == {"elapsed": 0, "care": 0, "event": 0, "ai": 0, "finalize": 0, "save": 0}
    assert state_file.read_bytes() == before
    assert day_image.getpixel((0, 0)) == day["palette"]["background"]
    assert night_image.getpixel((0, 0)) == night["palette"]["background"]
    assert hashlib.sha256(day_image.tobytes()).digest() != hashlib.sha256(night_image.tobytes()).digest()


def test_free_auto_uses_groq_then_local_without_openai_paid_fallback():
    plugin = _plugin()
    settings = {"ai_provider": "free_auto", "ai_openai_after_free": "on"}
    device_config = _FakeDeviceConfig({
        "GROQ_API_KEY": "groq-key",
        "OPEN_AI_SECRET": "openai-key",
    })

    backends = plugin._resolve_ai_backends(settings, device_config)

    assert [backend["provider"] for backend in backends] == ["groq", "local"]
    assert backends[1]["model"] == "local-rules-v1"


def test_missing_groq_key_generates_local_line_with_groq_fallback_marker():
    plugin = _plugin()
    now = datetime(2026, 6, 4, 15, 30, tzinfo=timezone.utc)
    settings = {
        "ai_dialogue": "on",
        "ai_provider": "free_auto",
        "ai_use_plugin_context": "off",
        "language": "en",
    }
    state = _state(now)

    assert plugin._maybe_generate_ai_message(state, settings, now, _FakeDeviceConfig({}))

    assert state["ai_message_provider"] == "local"
    assert state["ai_message_model"] == "local-rules-v1"
    assert state["ai_message_fallback_from"] == "groq"
    assert state["ai_message_fallback_reason"] == "missing_groq_key"
    assert state["message"] != "Quiet heartbeat."
    assert state.get("ai_usage") is None


def test_groq_daily_limit_falls_back_to_local_without_incrementing_usage():
    plugin = _plugin()
    now = datetime(2026, 6, 4, 15, 30, tzinfo=timezone.utc)
    settings = {
        "ai_dialogue": "on",
        "ai_provider": "free_auto",
        "ai_daily_limit": "1",
        "ai_use_plugin_context": "off",
        "language": "en",
    }
    state = _state(now)
    state["ai_usage"] = {"date": "2026-06-04", "requests": 1}

    assert plugin._maybe_generate_ai_message(state, settings, now, _FakeDeviceConfig({"GROQ_API_KEY": "groq-key"}))

    assert state["ai_message_provider"] == "local"
    assert state["ai_message_fallback_from"] == "groq"
    assert state["ai_message_fallback_reason"] == "daily_limit_reached"
    assert state["ai_usage"]["requests"] == 1
    assert state["ai_message_attempts"][0]["status"] == "skipped"


def test_groq_limit_error_falls_back_to_local_line():
    class _LimitError(Exception):
        status_code = 429

    class _FallbackPet(EpaperPet):
        def _request_ai_message(self, provider, api_key, model, state, settings, now, base_message, ambient_context=None):
            if provider == "groq":
                raise _LimitError("rate limit exceeded")
            return super()._request_ai_message(provider, api_key, model, state, settings, now, base_message, ambient_context)

    plugin = _FallbackPet({"id": "epaper_pet"})
    now = datetime(2026, 6, 4, 15, 30, tzinfo=timezone.utc)
    settings = {
        "ai_dialogue": "on",
        "ai_provider": "free_auto",
        "ai_daily_limit": "24",
        "ai_use_plugin_context": "off",
        "language": "en",
    }
    state = _state(now)

    assert plugin._maybe_generate_ai_message(state, settings, now, _FakeDeviceConfig({"GROQ_API_KEY": "groq-key"}))

    assert state["ai_message_provider"] == "local"
    assert state["ai_message_fallback_from"] == "groq"
    assert "rate limit" in state["ai_message_fallback_reason"]
    assert state["ai_usage"]["requests"] == 1
    assert [attempt["status"] for attempt in state["ai_message_attempts"]] == ["failed", "response"]


def test_local_chinese_fallback_does_not_expose_english_pose_labels():
    plugin = _plugin()
    life = {
        "stats": {"food_reserve": 8, "level": 2},
        "top_priority": {"metric": "stable", "severity": 0},
        "last_hunt": {},
        "prey_ecology": {"available_now": []},
        "visual_state": {"current_pose": {"key": "unwell", "label": "unwell rest"}},
        "activity": "\u4f11\u606f",
        "time_band": "night",
    }

    options = plugin._local_ai_message_options_zh(life, {})

    assert all("unwell" not in option and "rest" not in option for option in options)
    assert any("\u4f4e\u4f4e\u4f11\u606f" in option for option in options)


def test_local_fallback_telemetry_marks_groq_source():
    plugin = _plugin()
    text = plugin._ai_telemetry_text(
        {"ai_daily_limit": "24"},
        {
            "ai_usage": {"date": "2026-06-04", "requests": 0},
            "ai_message_provider": "local",
            "ai_message_fallback_from": "groq",
        },
    )

    assert text == "AI Local 0/24 <- Groq"
