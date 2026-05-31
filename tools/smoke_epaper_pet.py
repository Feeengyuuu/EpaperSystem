from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "inkypi-weather" / "package" / "InkyPi" / "src"
LOCAL_PACKAGES = ROOT / "inkypi-weather" / "package" / "InkyPi" / ".pc-packages"

# The project-local package folder can contain platform-specific Pillow wheels.
# Load the working user-site Pillow first, then use .pc-packages for Flask.
from PIL import Image  # noqa: E402

if LOCAL_PACKAGES.exists():
    sys.path.insert(0, str(LOCAL_PACKAGES))
sys.path.insert(0, str(SRC))

from config import Config  # noqa: E402
from plugins.context_cache import write_context  # noqa: E402
from plugins.epaper_pet.epaper_pet import EpaperPet, FACE_MAP, HUNTED_FOODS  # noqa: E402
from plugins.plugin_registry import get_plugin_instance, load_plugins  # noqa: E402


AI_LINE = "\u6211\u521a\u8bfb\u5b8c\u5929\u6c14\u548c\u65b0\u95fb\uff0c\u50cf\u7d20\u4e5f\u5f00\u59cb\u4f1a\u51b7\u7b11\u4e86\u3002"
MIXED_LINE = "\u4e0b\u5348\u7684\u98ce\u5f88pleasant\u3002"


class DeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
        }
        if key is None:
            return values
        return values.get(key, default)


class AIDeviceConfig(DeviceConfig):
    def load_env_key(self, key):
        return "fake-key" if key in {"GROQ_API_KEY", "OPEN_AI_SECRET", "OPENAI_API_KEY"} else None


class FakeRateLimitError(Exception):
    status_code = 429


def main() -> int:
    preview_path = ROOT / ".tmp" / "epaper_pet_smoke.png"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")

    settings = {
        "pet_name": "Mochi",
        "pet_id": f"smoke-mochi-{run_id}",
        "language": "zh-Hans",
        "personality": "quiet, curious, low-refresh e-paper companion",
        "tick_minutes": "15",
        "care_profile": "normal",
        "event_density": "expressive",
        "autonomous_care": "on",
        "show_journal": "on",
    }

    state_dir = ROOT / ".tmp" / "epaper_pet_smoke_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    context_dir = state_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    os.environ["INKYPI_CONTEXT_CACHE_DIR"] = str(context_dir)

    plugin = EpaperPet({"id": "epaper_pet"})
    plugin._cache_dir = lambda: state_dir
    image = plugin.generate_image(settings, DeviceConfig())

    if image.size != (800, 480):
        raise AssertionError(f"Unexpected image size: {image.size}")

    colors = image.convert("RGB").getcolors(maxcolors=1_000_000) or []
    palette = {color for _count, color in colors}
    if (0, 0, 0) not in palette or (255, 255, 255) not in palette:
        raise AssertionError("Expected pure black and pure white in rendered output.")

    image.save(preview_path)

    state_path = state_dir / f"smoke-mochi-{run_id}.json"
    first_state = json.loads(state_path.read_text(encoding="utf-8"))
    first_event = first_state.get("last_event_key")
    first_message = first_state.get("message")

    first_tick = datetime.fromisoformat(first_state["last_tick_at"])
    first_state["last_tick_at"] = (first_tick - timedelta(minutes=20)).isoformat()
    state_path.write_text(json.dumps(first_state, ensure_ascii=True, indent=2), encoding="utf-8")
    plugin.generate_image(settings, DeviceConfig())
    second_state = json.loads(state_path.read_text(encoding="utf-8"))
    if second_state.get("last_event_key") == first_event and second_state.get("message") == first_message:
        raise AssertionError("Expected autonomous event to change after an elapsed heartbeat.")

    summary = plugin._state_summary(second_state, settings)
    if not any("\u4e00" <= char <= "\u9fff" for char in summary["message"] + summary["activity"] + summary["mood"]):
        raise AssertionError("Expected Simplified Chinese text in localized pet summary.")
    daily_life = second_state.get("daily_life") or {}
    if not daily_life.get("theme") or not daily_life.get("goal") or not daily_life.get("favorite"):
        raise AssertionError(f"Expected daily life plan in pet state, got {daily_life}")
    if "belly" not in FACE_MAP or "zoomies" not in FACE_MAP:
        raise AssertionError("Expected active belly/zoomies moods in the random expression pool.")
    if FACE_MAP["belly"][0] == "( \u3002 )( \u3002 )":
        raise AssertionError("Expected non-sexual belly expression, not the rejected explicit form.")
    if len(HUNTED_FOODS) < 20:
        raise AssertionError("Expected a broad ecology-based hunting prey table.")
    prey_sizes = {food.get("size") for food in HUNTED_FOODS}
    if not {"tiny", "small", "medium", "large", "huge"}.issubset(prey_sizes):
        raise AssertionError(f"Expected prey sizes from tiny to huge, got {prey_sizes}")
    if not all(food.get("prey_group") and food.get("food_zh") and food.get("prey_mass_g") for food in HUNTED_FOODS):
        raise AssertionError("Expected every prey entry to include ecological group, Chinese name, and mass.")
    prey_life = plugin._life_context(second_state, settings, datetime.now(), second_state.get("message", ""))
    prey_ecology = prey_life.get("prey_ecology") or {}
    catalog_entries = [
        prey
        for tier in prey_ecology.get("catalog", [])
        for prey in tier.get("prey", [])
    ]
    if len(catalog_entries) != len(HUNTED_FOODS):
        raise AssertionError("Expected AI prey ecology context to include the complete prey catalog.")
    if not prey_ecology.get("available_now") or not prey_ecology.get("next_locked_prey"):
        raise AssertionError(f"Expected AI prey ecology context to include available and locked prey, got {prey_ecology}")
    if not all("prey_mass_g" in prey and "reserve_gain" in prey and "xp_gain" in prey for prey in catalog_entries):
        raise AssertionError("Expected AI prey catalog to expose mass, reserve gain, and XP gain.")

    midday = datetime.now().astimezone().replace(hour=12, minute=15, second=0, microsecond=0)
    midday_events = plugin._event_catalog(settings, midday, second_state)
    midday_ids = {event.get("id") for event in midday_events}
    if not {"crumb_audit", "warm_screen_listen", "safe_belly_sprawl"}.issubset(midday_ids):
        raise AssertionError(f"Expected midday routine events, got {midday_ids}")

    original_daily_gate = plugin._daily_gate
    try:
        plugin._daily_gate = lambda *_args, **_kwargs: True
        instinct_state = {
            "pet_id": f"instinct-mochi-{run_id}",
            "name": "Mochi",
            "activity": "quiet watch",
            "mood": "calm",
            "message": "Quiet heartbeat.",
            "event_index": 0,
            "born_at": midday.isoformat(),
            "last_tick_at": midday.isoformat(),
            "stats": {
                "food": 36,
                "happiness": 70,
                "energy": 68,
                "cleanliness": 80,
                "health": 90,
                "level": 1,
                "xp": 0,
                "age_days": 0,
            },
        }
        plugin._ensure_daily_life(instinct_state, midday)
        if not plugin._apply_daily_instinct(instinct_state, settings, midday, DeviceConfig()):
            raise AssertionError("Expected daily survival instinct to trigger.")
        if instinct_state.get("activity") != "foraging" or instinct_state["stats"]["food"] <= 36:
            raise AssertionError(f"Expected self-foraging to improve food, got {instinct_state}")
    finally:
        plugin._daily_gate = original_daily_gate

    low_hunt_state = {
        "pet_id": f"low-hunt-mochi-{run_id}",
        "name": "Mochi",
        "event_index": 0,
        "last_tick_at": midday.isoformat(),
        "activity": "quiet watch",
        "mood": "calm",
        "message": "Quiet heartbeat.",
        "stats": {
            "food": 10,
            "food_reserve": 0,
            "happiness": 70,
            "energy": 80,
            "cleanliness": 80,
            "health": 90,
            "level": 1,
            "xp": 0,
            "age_days": 0,
        },
    }
    plugin._apply_hunting_meal(low_hunt_state, midday)
    if low_hunt_state["last_hunt"].get("size") != "tiny":
        raise AssertionError(f"Expected level 1 pet to hunt tiny prey, got {low_hunt_state['last_hunt']}")
    if not low_hunt_state["last_hunt"].get("prey_group") or not low_hunt_state["last_hunt"].get("food_zh"):
        raise AssertionError(f"Expected hunt details to include ecology metadata, got {low_hunt_state['last_hunt']}")
    if low_hunt_state["stats"].get("food_reserve", 0) <= 0:
        raise AssertionError(f"Expected hunting leftovers to create food reserve, got {low_hunt_state}")

    high_hunt_state = json.loads(json.dumps(low_hunt_state))
    high_hunt_state["pet_id"] = f"high-hunt-mochi-{run_id}"
    high_hunt_state["stats"].update({"food": 8, "food_reserve": 0, "xp": 1000, "energy": 100})
    high_level = plugin._level_info(high_hunt_state, settings)
    if high_level["level"] < 10 or high_level["prey_size"] != "large" or high_level["reserve_cap"] < 540:
        raise AssertionError(f"Expected high-level large-prey tier, got {high_level}")
    if not high_level.get("next_prey_unlock") or high_level["next_prey_unlock"].get("prey_size") != "huge":
        raise AssertionError(f"Expected next huge-prey unlock, got {high_level}")
    high_pool_sizes = {food.get("size") for food in plugin._available_hunted_foods(high_level["level"])}
    if "tiny" in high_pool_sizes or not {"medium", "large"}.issubset(high_pool_sizes):
        raise AssertionError(f"Expected high-level focused pool to favor medium/large prey, got {high_pool_sizes}")
    large_seen = False
    for event_index in range(32):
        high_hunt_state["event_index"] = event_index
        prey = plugin._select_hunted_food(high_hunt_state, midday)
        if prey.get("size") in {"large", "medium"}:
            large_seen = True
            break
    if not large_seen:
        raise AssertionError("Expected high-level hunting pool to include larger prey.")

    apex_state = json.loads(json.dumps(high_hunt_state))
    apex_state["stats"].update({"xp": 1800})
    apex_level = plugin._level_info(apex_state, settings)
    apex_pool_sizes = {food.get("size") for food in plugin._available_hunted_foods(apex_level["level"])}
    if "huge" not in apex_pool_sizes:
        raise AssertionError(f"Expected apex-level hunting pool to include huge prey, got {apex_pool_sizes}")

    reserve_state = json.loads(json.dumps(high_hunt_state))
    reserve_state["stats"].update({"food": 12, "food_reserve": 80})
    plugin._eat_from_reserve(reserve_state, midday)
    if reserve_state["stats"]["food"] <= 12 or reserve_state["stats"]["food_reserve"] >= 80:
        raise AssertionError(f"Expected stash meal to feed the pet from reserve, got {reserve_state}")

    ai_settings = dict(settings)
    ai_settings.update({
        "pet_id": f"ai-smoke-mochi-{run_id}",
        "ai_dialogue": "on",
        "ai_each_render": "on",
        "ai_provider": "groq",
        "ai_text_model": "gpt-4o-mini",
        "ai_groq_model": "llama-3.3-70b-versatile",
        "ai_daily_limit": "24",
        "ai_use_plugin_context": "on",
        "ai_chat_style": "wry",
        "ai_context_max_age_hours": "24",
    })

    context_now = datetime.now().astimezone()
    prompt_context = plugin._ai_prompt_context(
        high_hunt_state,
        ai_settings,
        context_now,
        high_hunt_state.get("message", ""),
        {
            "available": True,
            "sources": [
                {
                    "plugin": "weather",
                    "kind": "weather",
                    "source": "Prompt Smoke Weather",
                    "age_minutes": 2,
                    "summary": "current 68F; clear enough for hunting metaphors",
                }
            ],
        },
    )
    variation = prompt_context.get("variation") or {}
    if not variation.get("primary_angle") or not variation.get("line_shape") or not variation.get("detail_lens"):
        raise AssertionError(f"Expected AI variation controls, got {variation}")
    if "prey_ecology" not in variation.get("must_consider", []):
        raise AssertionError(f"Expected AI variation to require prey ecology coverage, got {variation}")
    if not variation.get("prey_focus", {}).get("prey_mass_g"):
        raise AssertionError(f"Expected AI variation to select a concrete prey focus, got {variation}")
    prompt_prey_catalog = [
        prey
        for tier in prompt_context["life"]["prey_ecology"]["catalog"]
        for prey in tier.get("prey", [])
    ]
    if len(prompt_prey_catalog) != len(HUNTED_FOODS):
        raise AssertionError("Expected AI prompt context to carry the complete prey catalog.")
    system_rules = plugin._ai_system_content("zh-Hans")
    if "prey_ecology contains the full prey catalog" not in system_rules or "variation.primary_angle" not in system_rules:
        raise AssertionError("Expected AI system prompt to require prey ecology and variation controls.")

    write_context(
        "weather",
        {
            "kind": "weather",
            "source": "Smoke Weather",
            "summary": "current 68F; feels like 66F; today high 72 low 55",
            "facts": [{"label": "Air Quality", "value": "Good"}],
        },
        generated_at=context_now,
        ttl_seconds=3600,
    )
    write_context(
        "daily_ai_news",
        {
            "kind": "news",
            "source": "Smoke News",
            "summary": "A tiny policy story made the afternoon feel expensive.",
            "items": [{"title": "Test headline reaches the e-paper pet", "why": "Smoke cache proves context works"}],
        },
        generated_at=context_now,
        ttl_seconds=3600,
    )
    write_context(
        "steam_daily_art",
        {
            "kind": "game_promo",
            "source": "Smoke Steam",
            "summary": "Steam promotion: Tiny Strategy Sale",
            "items": [{"name": "Tiny Strategy Sale"}],
        },
        generated_at=context_now,
        ttl_seconds=3600,
    )

    captured_context = {}

    def fake_ai_message(_provider, _api_key, _model, _state, _settings, _now, _base_message, ambient_context=None):
        captured_context["ambient"] = ambient_context or {}
        return AI_LINE

    plugin._request_ai_message = fake_ai_message
    plugin.generate_image(ai_settings, AIDeviceConfig())
    ai_state = json.loads((state_dir / f"ai-smoke-mochi-{run_id}.json").read_text(encoding="utf-8"))
    if ai_state.get("message") != AI_LINE:
        raise AssertionError("Expected mocked AI message to replace the local pet line.")
    if ai_state.get("ai_message_status") != "generated":
        raise AssertionError("Expected AI message status to be generated.")
    ai_telemetry = plugin._ai_telemetry_text(ai_settings, ai_state)
    if "Groq" not in ai_telemetry or "1/24" not in ai_telemetry:
        raise AssertionError(f"Expected Groq usage telemetry, got {ai_telemetry!r}.")

    ambient = captured_context.get("ambient") or {}
    if not ambient.get("available"):
        raise AssertionError("Expected live plugin context to be available to the AI prompt.")
    plugins = {source.get("plugin") for source in ambient.get("sources", [])}
    if not {"weather", "daily_ai_news", "steam_daily_art"}.issubset(plugins):
        raise AssertionError(f"Expected weather, news, and Steam context, got {plugins}")
    if not ai_state.get("ai_context_snapshot", {}).get("available"):
        raise AssertionError("Expected pet state to keep an AI context snapshot.")

    if plugin._clean_ai_message(MIXED_LINE, settings) != MIXED_LINE:
        raise AssertionError("Expected Simplified Chinese cleaner to allow mixed English.")

    fallback_settings = dict(ai_settings)
    fallback_settings.update({
        "pet_id": f"fallback-smoke-mochi-{run_id}",
        "ai_provider": "free_auto",
        "ai_openai_after_free": "on",
    })
    fallback_calls = []

    def fake_fallback_ai(provider, _api_key, _model, _state, _settings, _now, _base_message, ambient_context=None):
        fallback_calls.append(provider)
        if provider == "groq":
            raise FakeRateLimitError("Groq rate limit exceeded for the free tier.")
        return "\u514d\u8d39\u989d\u5ea6\u7761\u4e86\uff0cOpenAI\u624d\u4e0a\u591c\u73ed\u3002"

    plugin._request_ai_message = fake_fallback_ai
    plugin.generate_image(fallback_settings, AIDeviceConfig())
    fallback_state = json.loads((state_dir / f"fallback-smoke-mochi-{run_id}.json").read_text(encoding="utf-8"))
    if fallback_calls != ["groq", "openai"]:
        raise AssertionError(f"Expected Groq then OpenAI fallback, got {fallback_calls}")
    if fallback_state.get("ai_message_provider") != "openai":
        raise AssertionError("Expected OpenAI to be recorded as the paid fallback provider.")
    if fallback_state.get("ai_message_fallback_from") != "groq":
        raise AssertionError("Expected fallback source to be recorded as Groq.")
    usage = fallback_state.get("ai_provider_usage") or {}
    if usage.get("openai_requests") != 1:
        raise AssertionError(f"Expected one recorded OpenAI request, got {usage}")
    fallback_telemetry = plugin._ai_telemetry_text(fallback_settings, fallback_state)
    if "OpenAI" not in fallback_telemetry or "<- Groq" not in fallback_telemetry:
        raise AssertionError(f"Expected OpenAI fallback telemetry, got {fallback_telemetry!r}.")

    low_need_state = {
        "name": "Mochi",
        "activity": "grooming",
        "mood": "calm",
        "message": "Combed the pixel fur into tidy rows.",
        "stats": {
            "food": 12,
            "happiness": 70,
            "energy": 18,
            "cleanliness": 82,
            "health": 90,
            "level": 1,
            "age_days": 0,
        },
    }
    life = plugin._life_context(low_need_state, settings, datetime.now(), low_need_state["message"])
    if life["top_priority"]["metric"] not in {"food", "energy"}:
        raise AssertionError(f"Expected food or energy to be the top priority, got {life['top_priority']}")
    if life["top_priority"]["severity"] < 4:
        raise AssertionError("Expected urgent pet state to have severity >= 4.")
    if not life["state_notes"]:
        raise AssertionError("Expected state notes for AI prompt grounding.")

    Config.config_file = str(SRC / "config" / "device_dev.json")
    config = Config()
    plugin_config = config.get_plugin("epaper_pet")
    if not plugin_config:
        raise AssertionError("epaper_pet was not discovered by InkyPi config.")

    load_plugins([plugin_config])
    plugin_instance = get_plugin_instance(plugin_config)
    if not isinstance(plugin_instance, EpaperPet):
        raise AssertionError("epaper_pet was not registered as an EpaperPet instance.")

    print(f"epaper_pet smoke ok: {preview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
