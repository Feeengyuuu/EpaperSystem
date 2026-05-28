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


def test_parse_poem_response_limits_nonblank_lines(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._parse_poem_response(
        [
            {
                "title": "Test Poem",
                "author": "Example Poet",
                "lines": ["First line", "", "Second line", "Third line"],
            }
        ],
        2,
    )

    assert result == {
        "title": "Test Poem",
        "author": "Example Poet",
        "lines": ["First line", "Second line"],
    }


def test_parse_poem_response_skips_headings_and_foreign_epigraph(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._parse_poem_response(
        [
            {
                "title": "The Corsair.",
                "author": "George Gordon, Lord Byron",
                "lines": [
                    "CANTO THE FIRST.",
                    '"-nessun maggior dolore,',
                    "Che ricordarsi del tempo felice",
                    "Nella miseria,-\"",
                    "O'er the glad waters of the dark blue sea,",
                    "Our thoughts as boundless, and our soul's as free",
                    "Far as the breeze can bear, the billows foam,",
                ],
            }
        ],
        2,
    )

    assert result["lines"] == [
        "O'er the glad waters of the dark blue sea,",
        "Our thoughts as boundless, and our soul's as free",
    ]


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

    def fake_poem(limit):
        return {
            "title": "Small Song",
            "author": "A. Poet",
            "lines": ["One clear line", "Another measured line"][:limit],
        }

    plugin._fetch_dictionary_entry = fake_dictionary
    plugin._fetch_poem = fake_poem

    image = plugin.generate_image({}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    assert (tmp_path / "daily.json").is_file()


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
    calls = {"dictionary": 0, "poem": 0}

    def fake_dictionary(word):
        calls["dictionary"] += 1
        return {"definition": "Network definition."}

    def fake_poem(limit):
        calls["poem"] += 1
        return {"title": "Poem", "author": "Poet", "lines": ["Line"]}

    plugin._fetch_dictionary_entry = fake_dictionary
    plugin._fetch_poem = fake_poem
    now = datetime(2026, 5, 27)

    first = plugin._daily_payload({}, now)
    second = plugin._daily_payload({}, now)

    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert calls == {"dictionary": 1, "poem": 1}
