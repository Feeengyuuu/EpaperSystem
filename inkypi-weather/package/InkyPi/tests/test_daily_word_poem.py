import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_word_poem import daily_word_poem as word_module
from plugins.daily_word_poem.daily_word_poem import DailyWordPoem, TITLE_WORDMARK_IMAGE, TITLE_WORDMARK_SIZE
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    read_source_provenance,
)


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone="America/Los_Angeles", orientation="horizontal"):
        self.resolution = resolution
        self.timezone = timezone
        self.orientation = orientation

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "timezone": self.timezone,
            "orientation": self.orientation,
        }
        if key is None:
            return values
        return values.get(key, default)


def _plugin(tmp_path):
    plugin = DailyWordPoem({"id": "daily_word_poem"})
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


def test_default_font_is_original_jost_but_explicit_literary_font_is_preserved(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    sentinel = object()
    calls = []

    def fake_get_font(family, size, weight="normal"):
        calls.append((family, size, weight))
        return sentinel

    monkeypatch.setattr(word_module, "get_font", fake_get_font)

    assert plugin._load_font(None, 18) is sentinel
    assert plugin._load_font("", 18) is sentinel
    assert plugin._load_font("康熙字典体", 18, "bold") is sentinel
    assert calls == [
        ("Jost", 18, "normal"),
        ("Jost", 18, "normal"),
        ("康熙字典体", 18, "bold"),
    ]


def test_settings_default_font_is_original_jost():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "daily_word_poem" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")
    script = " ".join(html.split())
    missing = object()
    native_initial = word_module.get_available_font_names(default=word_module.DEFAULT_FONT)[0]

    def submitted_font(stored=missing):
        current = native_initial if stored is missing else stored
        has_stored = stored is not missing
        if "const hasStoredFont =" in script:
            assert "&& pluginSettings.font_family !== undefined;" in script
            assert "const jost = [...fontFamily.options].find((option) => option.value === 'Jost');" in script
            assert "if (jost && (!hasStoredFont || !fontFamily.value)) {" in script
            if not has_stored or not current:
                current = "Jost"
        else:
            assert "if (fontFamily && !fontFamily.value) {" in script
            if not current:
                current = "Jost"
        return current

    assert word_module.DEFAULT_FONT == "Jost"
    assert "Jost" in word_module.get_available_font_names(default=word_module.DEFAULT_FONT)
    assert "fontFamily.value = 'Jost';" in html
    assert "fontFamily.value = 'Microsoft YaHei';" not in html
    assert submitted_font("Jost") == "Jost"
    assert submitted_font("康熙字典体") == "康熙字典体"
    assert submitted_font("") == "Jost"
    assert submitted_font() == "Jost"


def test_custom_word_list_picks_one_word_per_day(tmp_path):
    plugin = _plugin(tmp_path)
    settings = {"word_list": "Aurora\nNimbus\nAurora\n"}
    day = datetime(2026, 5, 27)

    first = plugin._daily_word(settings, day)
    second = plugin._daily_word(settings, day)
    next_day = plugin._daily_word(settings, datetime(2026, 5, 28))

    assert first == second
    assert first["word"] in {"aurora", "nimbus"}
    assert next_day["word"] in {"aurora", "nimbus"}


def test_parse_dictionary_entry_extracts_core_fields(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._parse_dictionary_entry(
        [
            {
                "word": "luminous",
                "phonetics": [{"text": "/loo-muh-nuhs/"}],
                "meanings": [
                    {
                        "partOfSpeech": "adjective",
                        "definitions": [
                            {
                                "definition": "Full of light.",
                                "example": "A luminous screen glowed quietly.",
                            }
                        ],
                    }
                ],
            }
        ],
        "luminous",
    )

    assert result["word"] == "luminous"
    assert result["phonetic"] == "/loo-muh-nuhs/"
    assert result["part_of_speech"] == "adjective"
    assert result["definition"] == "Full of light."
    assert result["example"] == "A luminous screen glowed quietly."


def test_custom_quote_list_picks_one_quote_per_day(tmp_path):
    plugin = _plugin(tmp_path)
    settings = {
        "quote_list": (
            "Stay curious - Ada Lovelace\n"
            "Ship clarity - Grace Hopper\n"
            "Stay curious - Ada Lovelace\n"
        )
    }
    day = datetime(2026, 5, 27)

    first = plugin._daily_quote(settings, day)
    second = plugin._daily_quote(settings, day)
    next_day = plugin._daily_quote(settings, datetime(2026, 5, 28))

    allowed = {
        ("Stay curious", "Ada Lovelace"),
        ("Ship clarity", "Grace Hopper"),
    }
    assert (first["text"], first["author"]) in allowed
    assert first == second
    assert (next_day["text"], next_day["author"]) in allowed
    assert next_day != first


def test_custom_quote_list_strips_outer_quote_marks(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._daily_quote({"quote_list": '"Stay curious" - Ada Lovelace'}, datetime(2026, 5, 27))

    assert result["text"] == "Stay curious"
    assert result["author"] == "Ada Lovelace"


def test_parse_wikiquote_quote_extracts_fields(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._parse_wikiquote_quote({
        "quote": "\u201cThe truth is rarely pure and never simple.\u201d",
        "author": "Oscar Wilde",
        "featured_date": "2026-05-28",
    })

    assert result["text"] == "The truth is rarely pure and never simple."
    assert result["author"] == "Oscar Wilde"
    assert result["topic"] == "Wikiquote QOTD"
    assert result["source"] == "Wikiquote QOTD"
    assert result["featured_date"] == "2026-05-28"


def test_parse_wikiquote_day_raw_extracts_fields(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._parse_wikiquote_day_raw(
        """{| style="background:{{{color}}};"
| align=center | <p>The time has come when scientific truth must cease to be the property of the few.</p><p> ~ [[Louis Agassiz]] ~ </p>
| align=center | [[File:Plasma lamp touching.jpg|144px|right|]]
{{QoDfooter|Month={{CURRENTMONTHNAME}}|Year=2007}}
|}""",
        "https://en.wikiquote.org/wiki/Wikiquote:Quote_of_the_day/May_28",
        "2026-05-28",
    )

    assert result["text"] == "The time has come when scientific truth must cease to be the property of the few."
    assert result["author"] == "Louis Agassiz"
    assert result["source"] == "Wikiquote QOTD"
    assert result["source_url"].endswith("/May_28")


def test_generate_image_renders_and_writes_daily_cache(tmp_path):
    plugin = _plugin(tmp_path)

    def fake_dictionary(word):
        return {
            "word": word,
            "phonetic": "/test/",
            "part_of_speech": "noun",
            "definition": "A word used by a test.",
            "example": "The test word stayed readable.",
        }

    plugin._fetch_dictionary_entry = fake_dictionary
    plugin._fetch_wikiquote_quote = lambda date_key: {
        "text": "A quote used by a test.",
        "author": "Q. Author",
        "topic": "Wikiquote QOTD",
        "source": "Wikiquote QOTD",
    }

    image = plugin.generate_image({}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert (tmp_path / "daily.json").is_file()
    payload = json.loads((tmp_path / "daily.json").read_text(encoding="utf-8"))
    assert "quote" in payload
    assert "poem" not in payload
    assert payload["quote"]["source"] == "Wikiquote QOTD"


def test_page_palette_switches_between_day_and_midnight(tmp_path):
    plugin = _plugin(tmp_path)

    day = plugin._page_palette({}, {"mode": "day"})
    night = plugin._page_palette({}, {"mode": "night"})

    assert day[0] == (255, 255, 255)
    assert day[-1] == "DAY READING"
    assert night[0] == (0, 0, 0)
    assert night[1] == (255, 255, 255)
    assert night[-1] == "MIDNIGHT READING"


def test_original_day_keeps_legacy_style_colors(tmp_path):
    plugin = _plugin(tmp_path)
    theme = _canonical_theme(
        "day",
        background=(241, 236, 225),
        panel=(221, 213, 196),
        ink=(19, 21, 23),
        muted=(73, 75, 79),
        rule=(128, 124, 116),
        accent=(180, 44, 58),
    )
    settings = {
        "_inkypi_theme": theme,
        "backgroundColor": "#010203",
        "textColor": "#040506",
        "accentColor": "#070809",
    }

    bg, text, accent, muted, faint, label = plugin._page_palette(settings, theme)

    assert (bg, text, accent) == ((1, 2, 3), (4, 5, 6), (7, 8, 9))
    assert muted != theme["palette"]["muted"]
    assert faint != theme["palette"]["rule"]
    assert label == "DAY READING"


def _sample_payload():
    return {
        "word": {
            "word": "radiant",
            "phonetic": "/ray-dee-uhnt/",
            "part_of_speech": "noun",
            "definition": "A point source from which radiation is emitted.",
        },
        "quote": {
            "text": "A compact quote for layout testing.",
            "author": "Tester",
            "topic": "Wikiquote QOTD",
        },
        "sources": ["Free Dictionary API", "Wikiquote QOTD"],
    }


def test_render_quote_panel_does_not_double_wrap_cached_quote(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    draw = ImageDraw.Draw(Image.new("RGB", (400, 300), "white"))
    seen = {}

    def fake_fit_quote_font(draw_obj, rendered_text, font_family, max_width, max_height):
        seen["rendered_text"] = rendered_text
        return ImageFont.load_default()

    monkeypatch.setattr(plugin, "_fit_quote_font", fake_fit_quote_font)

    plugin._render_quote_panel(
        draw,
        {"text": '"Cached quote."', "author": "Tester", "topic": "Wikiquote QOTD"},
        (10, 10, 300, 250),
        "Jost",
        (0, 0, 0),
        (0, 0, 0),
        (80, 80, 80),
    )

    assert seen["rendered_text"] == '"Cached quote."'


class RecordingDraw:
    def __init__(self, draw):
        self.draw = draw
        self.text_calls = []

    def text(self, position, text, *args, **kwargs):
        self.text_calls.append(str(text))
        return self.draw.text(position, text, *args, **kwargs)

    def textbbox(self, *args, **kwargs):
        return self.draw.textbbox(*args, **kwargs)


def test_render_quote_panel_draws_full_long_wikiquote_sentence(tmp_path):
    plugin = _plugin(tmp_path)
    draw = RecordingDraw(ImageDraw.Draw(Image.new("RGB", (800, 480), "white")))
    quote_text = (
        "Never dream of forcing men into the ways of God. Think yourself, and let think. "
        "Use no constraint in matters of religion. Even those who are farthest out of the way "
        "never compel to come in by any other means than reason, truth, and love."
    )

    plugin._render_quote_panel(
        draw,
        {"text": quote_text, "author": "John Wesley", "topic": "Wikiquote QOTD"},
        (445, 79, 326, 454),
        "Jost",
        (0, 0, 0),
        (35, 110, 70),
        (120, 120, 120),
    )

    rendered = " ".join(draw.text_calls)

    assert "reason, truth, and love." in rendered
    assert rendered.count("Never dream") == 1


def test_title_wordmark_asset_is_transparent_measured_strip():
    path = Path(word_module.__file__).with_name(TITLE_WORDMARK_IMAGE)

    image = Image.open(path).convert("RGBA")

    assert image.size == TITLE_WORDMARK_SIZE
    assert image.getchannel("A").getextrema() == (0, 255)
    assert image.getchannel("A").getbbox() is not None
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((image.width - 1, image.height - 1))[3] == 0


def test_render_uses_title_wordmark_when_available(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    fake_wordmark = Image.new("RGBA", TITLE_WORDMARK_SIZE, (12, 34, 56, 255))
    monkeypatch.setattr(plugin, "_load_title_wordmark", lambda: fake_wordmark)

    image = plugin._render((800, 480), {}, _sample_payload(), datetime(2026, 6, 25), {"mode": "day"})

    assert image.getpixel((27, 26)) == (12, 34, 56)


def test_render_falls_back_to_text_title_when_wordmark_missing(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    monkeypatch.setattr(plugin, "_load_title_wordmark", lambda: None)

    image = plugin._render((800, 480), {}, _sample_payload(), datetime(2026, 6, 25), {"mode": "day"})
    header = image.crop((26, 26, 150, 48))

    accent_pixels = 0
    for y in range(header.height):
        for x in range(header.width):
            r, g, b = header.getpixel((x, y))
            if g > r and g > b and g > 70:
                accent_pixels += 1
    assert accent_pixels > 80


def test_display_phonetic_ascii_fallback_for_ipa(tmp_path):
    plugin = _plugin(tmp_path)

    assert plugin._display_phonetic("/ˈnɪmbl/") == "/'nimbl/"
    assert plugin._display_phonetic("/θə ˈsʌn/") == "/thuh 'suhn/"


def test_cached_payload_is_reused_without_network(tmp_path):
    plugin = _plugin(tmp_path)
    calls = {"dictionary": 0, "wikiquote": 0}

    def fake_dictionary(word):
        calls["dictionary"] += 1
        return {"definition": "Network definition."}

    def fake_wikiquote(date_key):
        calls["wikiquote"] += 1
        return {
            "text": "Cached quote.",
            "author": "Wikiquote",
            "topic": "Wikiquote QOTD",
            "source": "Wikiquote QOTD",
        }

    plugin._fetch_dictionary_entry = fake_dictionary
    plugin._fetch_wikiquote_quote = fake_wikiquote
    now = datetime(2026, 5, 27)

    first = plugin._daily_payload({}, now)
    second = plugin._daily_payload({}, now)

    assert first["from_cache"] is False
    assert "quote" in first
    assert second["from_cache"] is True
    assert calls == {"dictionary": 1, "wikiquote": 1}


def test_generate_image_attests_fresh_remote_cache_without_network(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 7, 13, 9, 0)
    monkeypatch.setattr(plugin, "_localized_now", lambda _device: now)
    monkeypatch.setattr(
        plugin,
        "_render",
        lambda *_args, **_kwargs: Image.new("RGB", (32, 16), "white"),
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_dictionary_entry",
        lambda word: {"word": word, "definition": "Remote definition."},
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_wikiquote_quote",
        lambda _date_key: {
            "text": "Remote quote.",
            "author": "Fixture",
            "source": "Wikiquote QOTD",
        },
    )
    plugin.generate_image({}, FakeDeviceConfig())

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("fresh cache must avoid provider calls")

    monkeypatch.setattr(plugin, "_fetch_dictionary_entry", fail_provider)
    monkeypatch.setattr(plugin, "_fetch_wikiquote_quote", fail_provider)
    image = plugin.generate_image({}, FakeDeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE


def test_forced_provider_failure_uses_local_fallback_without_promoting_cache_or_context(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 7, 13, 9, 0)
    context_calls = []
    monkeypatch.setattr(plugin, "_localized_now", lambda _device: now)
    monkeypatch.setattr(
        plugin,
        "_render",
        lambda *_args, **_kwargs: Image.new("RGB", (32, 16), "white"),
    )
    monkeypatch.setattr(
        word_module,
        "write_context",
        lambda *args, **kwargs: context_calls.append((args, kwargs)),
    )

    def fail_provider(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(plugin, "_fetch_dictionary_entry", fail_provider)
    monkeypatch.setattr(plugin, "_fetch_wikiquote_quote", fail_provider)

    image = plugin.generate_image({"forceRefresh": True}, FakeDeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True
    assert not (tmp_path / "daily.json").exists()
    assert context_calls == []


@pytest.mark.parametrize("failed_provider", ["dictionary", "wikiquote"])
def test_partial_provider_success_is_local_and_never_promoted(
    tmp_path,
    monkeypatch,
    failed_provider,
):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 7, 13, 9, 0)
    context_calls = []
    monkeypatch.setattr(plugin, "_localized_now", lambda _device: now)
    monkeypatch.setattr(
        plugin,
        "_render",
        lambda *_args, **_kwargs: Image.new("RGB", (32, 16), "white"),
    )
    monkeypatch.setattr(
        word_module,
        "write_context",
        lambda *args, **kwargs: context_calls.append((args, kwargs)),
    )

    def dictionary(word):
        if failed_provider == "dictionary":
            raise RuntimeError("dictionary unavailable")
        return {"word": word, "definition": "Remote definition."}

    def wikiquote(_date_key):
        if failed_provider == "wikiquote":
            raise RuntimeError("wikiquote unavailable")
        return {
            "text": "Remote quote.",
            "author": "Fixture",
            "source": "Wikiquote QOTD",
        }

    monkeypatch.setattr(plugin, "_fetch_dictionary_entry", dictionary)
    monkeypatch.setattr(plugin, "_fetch_wikiquote_quote", wikiquote)

    image = plugin.generate_image({"force_refresh": True}, FakeDeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True
    assert not (tmp_path / "daily.json").exists()
    assert context_calls == []


def test_forced_provider_failure_uses_stale_remote_cache_without_rewriting_it(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    times = [
        datetime(2026, 7, 13, 9, 0),
        datetime(2026, 7, 13, 9, 5),
    ]
    context_calls = []
    monkeypatch.setattr(plugin, "_localized_now", lambda _device: times[0])
    monkeypatch.setattr(
        plugin,
        "_render",
        lambda *_args, **_kwargs: Image.new("RGB", (32, 16), "white"),
    )
    monkeypatch.setattr(
        word_module,
        "write_context",
        lambda *args, **kwargs: context_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_dictionary_entry",
        lambda word: {"word": word, "definition": "Remote definition."},
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_wikiquote_quote",
        lambda _date_key: {
            "text": "Remote quote.",
            "author": "Fixture",
            "source": "Wikiquote QOTD",
        },
    )
    plugin.generate_image({}, FakeDeviceConfig())
    cache_path = tmp_path / "daily.json"
    original_bytes = cache_path.read_bytes()
    original_generated_at = json.loads(original_bytes)["generated_at"]
    context_calls.clear()
    monkeypatch.setattr(plugin, "_localized_now", lambda _device: times[1])

    def fail_provider(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(plugin, "_fetch_dictionary_entry", fail_provider)
    monkeypatch.setattr(plugin, "_fetch_wikiquote_quote", fail_provider)
    image = plugin.generate_image({"force_refresh": True}, FakeDeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True
    assert cache_path.read_bytes() == original_bytes
    assert len(context_calls) == 1
    assert context_calls[0][1]["generated_at"] == original_generated_at


def test_theme_only_warm_daily_cache_changes_only_palette_without_network(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    device = FakeDeviceConfig()
    plugin._fetch_dictionary_entry = lambda word: {"word": word, "definition": "Cached definition."}
    plugin._fetch_wikiquote_quote = lambda _date_key: {
        "text": "Cached quote.",
        "author": "Fixture",
        "topic": "Wikiquote QOTD",
        "source": "Wikiquote QOTD",
    }
    legacy_style = {
        "themeMode": "night",
        "backgroundColor": "#010203",
        "textColor": "#040506",
        "accentColor": "#070809",
    }
    plugin.generate_image(legacy_style, device)

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("theme-only redraw must not call a provider")

    monkeypatch.setattr(plugin, "_fetch_dictionary_entry", fail_provider)
    monkeypatch.setattr(plugin, "_fetch_wikiquote_quote", fail_provider)
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

    day_image = plugin.generate_image({**legacy_style, "_theme_render_only": True, "_inkypi_theme": day}, device)
    night_image = plugin.generate_image({**legacy_style, "_theme_render_only": True, "_inkypi_theme": night}, device)

    assert day_image.getpixel((0, 0)) == (1, 2, 3)
    assert night_image.getpixel((0, 0)) == night["palette"]["background"]
    assert hashlib.sha256(day_image.tobytes()).digest() != hashlib.sha256(night_image.tobytes()).digest()


def test_theme_only_daily_cache_miss_fails_without_provider_calls(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    calls = {"dictionary": 0, "wikiquote": 0}

    def fake_dictionary(*_args):
        calls["dictionary"] += 1
        return {}

    def fake_wikiquote(*_args):
        calls["wikiquote"] += 1
        return {}

    monkeypatch.setattr(plugin, "_fetch_dictionary_entry", fake_dictionary)
    monkeypatch.setattr(plugin, "_fetch_wikiquote_quote", fake_wikiquote)

    with pytest.raises(RuntimeError, match="warm .*cache"):
        plugin._daily_payload({"_theme_render_only": True}, datetime(2026, 7, 11, 9, 0))

    assert calls == {"dictionary": 0, "wikiquote": 0}
