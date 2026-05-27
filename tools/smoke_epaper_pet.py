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
from plugins.epaper_pet.epaper_pet import EpaperPet  # noqa: E402
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
