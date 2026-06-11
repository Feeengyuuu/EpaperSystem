import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_knowledge.daily_knowledge import DailyKnowledge, LOCAL_FALLBACK_FACTS  # noqa: E402


class FakeDeviceConfig:
    def __init__(self, env=None, resolution=(800, 480), timezone="America/Los_Angeles", orientation="horizontal"):
        self.env = env or {}
        self.resolution = resolution
        self.timezone = timezone
        self.orientation = orientation

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "timezone": self.timezone,
            "orientation": self.orientation,
            "theme_mode": "night",
        }
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return self.env.get(key)


def _plugin(tmp_path):
    plugin = DailyKnowledge({"id": "daily_knowledge"})
    plugin._cache_dir = lambda: tmp_path
    return plugin


def test_extract_fact_text_accepts_common_response_shapes(tmp_path):
    plugin = _plugin(tmp_path)

    assert plugin._extract_fact_text({"text": "A useful fact."}) == "A useful fact."
    assert plugin._extract_fact_text({"data": {"fact": "Nested fact."}}) == "Nested fact."
    assert plugin._extract_fact_text([{"content": "List fact."}]) == "List fact."


def test_rapidapi_key_prefers_fun_fact_env_name(tmp_path):
    plugin = _plugin(tmp_path)
    device = FakeDeviceConfig(env={"Fun_Fact": "secret-value"})

    assert plugin._rapidapi_key({}, device) == "secret-value"


def test_daily_payload_fetches_once_then_reuses_cache(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    calls = {"useless": 0, "world": 0}

    def fake_useless(settings, language):
        calls["useless"] += 1
        return plugin._fallback_fact("en", "2026-06-03", 0)

    def fake_world(settings, device_config, language):
        calls["world"] += 1
        return plugin._fallback_fact("en", "2026-06-03", 1)

    monkeypatch.setattr(plugin, "_fetch_useless_fact", fake_useless)
    monkeypatch.setattr(plugin, "_fetch_world_fun_fact", fake_world)

    now = datetime(2026, 6, 3, 9, 30)
    settings = {"language": "en"}
    first = plugin._daily_payload(settings, FakeDeviceConfig(), now)
    second = plugin._daily_payload(settings, FakeDeviceConfig(), now)

    assert len(first["facts"]) == 2
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert calls == {"useless": 1, "world": 1}


def test_chinese_fallback_uses_two_fresh_sentences_per_day(tmp_path):
    plugin = _plugin(tmp_path)

    first = plugin._fallback_fact("zh", "2026-06-07", 0)
    second = plugin._fallback_fact("zh", "2026-06-07", 1)
    repeated_first = plugin._fallback_fact("zh", "2026-06-07", 0)
    repeated_second = plugin._fallback_fact("zh", "2026-06-07", 1)

    assert first.text != second.text
    assert repeated_first.text == first.text
    assert repeated_second.text == second.text


def test_chinese_fallback_does_not_repeat_until_pool_is_exhausted(tmp_path):
    plugin = _plugin(tmp_path)
    chinese_pool_size = sum(1 for item in LOCAL_FALLBACK_FACTS if item["language"] == "zh")
    seen = set()

    for day in range(1, chinese_pool_size + 1):
        fact = plugin._fallback_fact("zh", f"2026-07-{day:02d}", 0)
        assert fact.text not in seen
        seen.add(fact.text)

    assert len(seen) == chinese_pool_size


def test_daily_payload_rotates_chinese_local_sentences_across_dates(tmp_path):
    plugin = _plugin(tmp_path)
    device = FakeDeviceConfig()
    settings = {"language": "zh", "use_useless_facts": False, "use_world_fun_facts": False}

    first = plugin._daily_payload(settings, device, datetime(2026, 6, 7, 9, 30))
    second = plugin._daily_payload(settings, device, datetime(2026, 6, 8, 9, 30))

    first_texts = {fact["text"] for fact in first["facts"]}
    second_texts = {fact["text"] for fact in second["facts"]}
    assert first_texts.isdisjoint(second_texts)


def test_render_page_returns_image(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 3, 9, 30)
    payload = {
        "date": "2026-06-03",
        "language": "en",
        "facts": [
            {
                "title": "Useless Fact",
                "text": "Octopuses have three hearts.",
                "source": "uselessfacts",
                "language": "en",
                "source_state": "live",
            },
            {
                "title": "World Fun Fact",
                "text": "A day on Venus is longer than a year on Venus.",
                "source": "World Fun Facts",
                "language": "en",
                "source_state": "live",
            },
        ],
    }
    palette = {
        "background": (0, 0, 0),
        "ink": (255, 255, 255),
        "dim": (112, 117, 130),
        "muted": (194, 196, 202),
        "rule": (46, 48, 56),
        "cyan": (107, 204, 255),
        "gold": (255, 196, 92),
        "accent": (107, 204, 255),
    }

    image = plugin._render_page((800, 480), payload, {}, now, palette)

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
