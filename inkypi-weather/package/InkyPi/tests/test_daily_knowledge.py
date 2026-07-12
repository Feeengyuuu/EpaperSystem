import hashlib
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_knowledge import daily_knowledge as knowledge_module  # noqa: E402
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


def test_default_font_is_yahei_but_explicit_literary_font_is_preserved(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    sentinel = object()
    calls = []

    def fake_get_font(family, size, weight="normal"):
        calls.append((family, size, weight))
        return sentinel

    monkeypatch.setattr(knowledge_module, "get_font", fake_get_font)

    assert plugin._load_font(None, 18) is sentinel
    assert plugin._load_font("", 18) is sentinel
    assert plugin._load_font("方正新楷近似", 18, "bold") is sentinel
    assert calls == [
        ("Microsoft YaHei", 18, "normal"),
        ("Microsoft YaHei", 18, "normal"),
        ("方正新楷近似", 18, "bold"),
    ]


def test_cjk_font_uses_shared_base_ui_resolver(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    sentinel = object()
    calls = []

    def fake_base_ui_font(size, bold=False):
        calls.append((size, bold))
        return sentinel

    monkeypatch.setattr(knowledge_module, "get_base_ui_font", fake_base_ui_font, raising=False)

    assert plugin._load_font("__cjk__", 19, "bold") is sentinel
    assert calls == [(19, True)]


def test_settings_default_font_is_microsoft_yahei():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "daily_knowledge" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")
    script = " ".join(html.split())
    missing = object()
    native_initial = knowledge_module.get_available_font_names(default=knowledge_module.DEFAULT_FONT)[0]

    def submitted_font(stored=missing):
        current = native_initial if stored is missing else stored
        has_stored = stored is not missing
        if "const hasStoredFont =" in script:
            assert "&& pluginSettings.font_family !== undefined;" in script
            assert "const yahei = [...fontFamily.options].find((option) => option.value === 'Microsoft YaHei');" in script
            assert "if (yahei && (!hasStoredFont || !fontFamily.value)) {" in script
            if not has_stored or not current:
                current = "Microsoft YaHei"
        else:
            assert "if (fontFamily && !fontFamily.value) {" in script
            if not current:
                current = "Microsoft YaHei"
        return current

    assert knowledge_module.DEFAULT_FONT == "Microsoft YaHei"
    assert native_initial != "Microsoft YaHei"
    assert "fontFamily.value = 'Microsoft YaHei';" in html
    assert "fontFamily.value = 'Jost';" not in html
    assert submitted_font("Jost") == "Jost"
    assert submitted_font("方正新楷近似") == "方正新楷近似"
    assert submitted_font("") == "Microsoft YaHei"
    assert submitted_font() == "Microsoft YaHei"


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


def test_theme_only_warm_daily_cache_uses_injected_palette_without_provider_calls(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    device = FakeDeviceConfig()
    calls = {"useless": 0, "world": 0}

    def fake_useless(_settings, _language):
        calls["useless"] += 1
        return knowledge_module.KnowledgeFact("Useless Fact", "Cached fact one.", "fixture")

    def fake_world(_settings, _device_config, _language):
        calls["world"] += 1
        return knowledge_module.KnowledgeFact("World Fun Fact", "Cached fact two.", "fixture")

    monkeypatch.setattr(plugin, "_fetch_useless_fact", fake_useless)
    monkeypatch.setattr(plugin, "_fetch_world_fun_fact", fake_world)
    settings = {"language": "en", "themeMode": "night"}
    plugin.generate_image(settings, device)
    warm_calls = dict(calls)

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("theme-only redraw must not call a provider")

    monkeypatch.setattr(plugin, "_fetch_useless_fact", fail_provider)
    monkeypatch.setattr(plugin, "_fetch_world_fun_fact", fail_provider)
    day = _canonical_theme(
        "day",
        background=(240, 235, 224),
        panel=(220, 212, 194),
        ink=(18, 20, 22),
        muted=(74, 76, 80),
        rule=(126, 122, 114),
        accent=(178, 48, 60),
    )
    night = _canonical_theme(
        "night",
        background=(8, 10, 13),
        panel=(24, 28, 34),
        ink=(246, 247, 249),
        muted=(180, 184, 192),
        rule=(60, 66, 74),
        accent=(70, 188, 236),
    )

    day_image = plugin.generate_image({**settings, "_theme_render_only": True, "_inkypi_theme": day}, device)
    night_image = plugin.generate_image({**settings, "_theme_render_only": True, "_inkypi_theme": night}, device)

    assert calls == warm_calls
    assert day_image.getpixel((0, 0)) == day["palette"]["background"]
    assert night_image.getpixel((0, 0)) == night["palette"]["background"]
    assert hashlib.sha256(day_image.tobytes()).digest() != hashlib.sha256(night_image.tobytes()).digest()


def test_theme_only_daily_cache_miss_fails_without_provider_calls(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    calls = {"useless": 0, "world": 0}

    def fake_useless(*_args):
        calls["useless"] += 1
        return None

    def fake_world(*_args):
        calls["world"] += 1
        return None

    monkeypatch.setattr(plugin, "_fetch_useless_fact", fake_useless)
    monkeypatch.setattr(plugin, "_fetch_world_fun_fact", fake_world)

    with pytest.raises(RuntimeError, match="warm .*cache"):
        plugin._daily_payload(
            {"_theme_render_only": True, "language": "en"},
            FakeDeviceConfig(),
            datetime(2026, 7, 11, 9, 0),
        )

    assert calls == {"useless": 0, "world": 0}


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
