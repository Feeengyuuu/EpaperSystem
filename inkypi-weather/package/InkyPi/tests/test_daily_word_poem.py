import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_word_poem.daily_word_poem import DailyWordPoem


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


def test_parse_wikiquote_quote_extracts_fields(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._parse_wikiquote_quote({
        "quote": "The truth is rarely pure and never simple.",
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
