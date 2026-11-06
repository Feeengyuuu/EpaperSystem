"""Merriam-Webster's Word of the Day as Daily Word's word: the feed's parser and the page's choice."""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_word_poem import mw_word  # noqa: E402
from plugins.daily_word_poem.daily_word_poem import DailyWordPoem  # noqa: E402

SUMMARY = """<font face="arial, helvetica" size="-1">
  <p><strong><font color="#000066">Merriam-Webster's Word of the Day for November 6, 2026 is:</font></strong></p>
  <p>
    <strong>quondam</strong> &#x2022; \\KWAHN-dum\\&nbsp; &#x2022; <em>adjective</em><br />
    <p><em>Quondam</em> is an adjective that means “former” or “sometime.”</p>
<p>// Many people were surprised to learn that the <em>quondam</em> professor has since become a dancer.</p>
<p><a href="https://www.merriam-webster.com/dictionary/quondam">See the entry ></a></p>
  </p>
  <p><strong>Examples:</strong><br />
    <p>“… <em>quondam</em> soccer legend David Beckham …” — Alex Beam, <em>The Boston Globe</em></p>
  </p>
  <p><strong>Did you know?</strong><br />
    <p>Looking for an unusual and creative way to say “former”? <em>Quondam</em> (which came to English in the
16th century from Latin <em>quondam</em>, meaning “at one time”) certainly fits the bill. Or maybe you’d prefer
<a href="https://www.merriam-webster.com/dictionary/whilom"><em>whilom</em></a>.</p>
  </p>
</font>"""


def _feed(*items):
    entries = "".join(f"""<item><title>{word}</title>
<link>https://www.merriam-webster.com/word-of-the-day/{word}-{day}</link>
<description><![CDATA[{summary}]]></description>
<merriam:shortdef><![CDATA[{shortdef}]]></merriam:shortdef></item>""" for word, day, summary, shortdef in items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:merriam="https://www.merriam-webster.com/word-of-the-day"><channel>
<title>Merriam-Webster's Word of the Day</title>{entries}</channel></rss>"""


FEED = _feed(("quondam", "2026-11-06", SUMMARY, "former or sometime"),
             ("effrontery", "2026-11-05", SUMMARY.replace("quondam", "effrontery"), "bold shamelessness"))


def test_the_feed_gives_each_days_word_with_its_respelling_meaning_example_and_story():
    words = mw_word.parse_feed(FEED)

    assert sorted(words) == ["2026-11-05", "2026-11-06"]
    word = words["2026-11-06"]
    assert word["word"] == "quondam" and word["phonetic"] == "/KWAHN-dum/" and word["part_of_speech"] == "adjective"
    assert word["definition"] == "Quondam is an adjective that means “former” or “sometime.”"
    assert word["example"] == "Many people were surprised to learn that the quondam professor has since become a dancer."
    assert word["did_you_know"].startswith("Looking for an unusual and creative way to say “former”?")
    assert len(word["did_you_know"]) <= mw_word.STORY_MAX_LEN and "<" not in word["did_you_know"]
    assert word["source"] == "Merriam-Webster Word of the Day"
    assert word["source_url"].endswith("/quondam-2026-11-06")
    assert mw_word.parse_feed("<rss><channel></channel></rss>") == {}


class _Device:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"timezone": "America/Los_Angeles", "orientation": "horizontal"}
        return values if key is None else values.get(key, default)


def _plugin(tmp_path, monkeypatch, feed):
    plugin = DailyWordPoem({"id": "daily_word_poem"})
    plugin._cache_dir = lambda: tmp_path
    calls = []
    monkeypatch.setattr(plugin, "_fetch_word_of_the_day_feed", lambda: calls.append("mw") or feed())
    monkeypatch.setattr(plugin, "_fetch_dictionary_entry", lambda word: calls.append(f"dictionary:{word}") or {
        "word": word, "phonetic": "/x/", "part_of_speech": "noun", "definition": "A local word.", "example": "x"})
    monkeypatch.setattr(plugin, "_fetch_wikiquote_quote", lambda date_key: {
        "text": "A quote.", "author": "Q. Author", "source": "Wikiquote QOTD"})
    return plugin, calls


def test_the_days_word_is_merriam_websters_with_its_own_definition(tmp_path, monkeypatch):
    plugin, calls = _plugin(tmp_path, monkeypatch, lambda: FEED)
    now = datetime(2026, 11, 6, 9, 0)

    payload = plugin._daily_payload({}, now)

    assert payload["word"]["word"] == "quondam" and payload["word"]["did_you_know"]
    assert payload["sources"][0] == "Merriam-Webster Word of the Day" and payload["_source_provenance"] == "live"
    # Its definition is Merriam-Webster's: no dictionary lookup for it.
    assert calls == ["mw"]
    # The feed's days are kept: the next refresh and tomorrow's need no new request.
    cached = json.loads((tmp_path / "word_of_the_day.json").read_text(encoding="utf-8"))
    assert sorted(cached["words"]) == ["2026-11-05", "2026-11-06"]
    assert plugin._word_of_the_day("2026-11-05", allow_network=False)["word"] == "effrontery"


def test_without_merriam_webster_the_local_word_and_dictionary_stand_in(tmp_path, monkeypatch):
    def down():
        raise OSError("feed down")

    plugin, calls = _plugin(tmp_path, monkeypatch, down)
    now = datetime(2026, 11, 6, 9, 0)

    payload = plugin._daily_payload({}, now)

    assert payload["word"]["definition"] == "A local word." and calls[0] == "mw" and calls[1].startswith("dictionary:")
    assert "word of the day offline" in payload["warnings"]
    # A failed source keeps the day's page out of the cache, so the next refresh tries again.
    assert payload["_source_provenance"] == "local_fallback" and not (tmp_path / "daily.json").exists()


def test_a_custom_word_list_still_wins(tmp_path, monkeypatch):
    plugin, calls = _plugin(tmp_path, monkeypatch, lambda: FEED)

    payload = plugin._daily_payload({"word_list": "serendipity"}, datetime(2026, 11, 6, 9, 0))

    assert payload["word"]["word"] == "serendipity" and "mw" not in calls


def test_the_word_panel_tells_merriam_websters_story_under_the_example(tmp_path, monkeypatch):
    plugin, _calls = _plugin(tmp_path, monkeypatch, lambda: FEED)
    drawn = []
    import PIL.ImageDraw

    original = PIL.ImageDraw.ImageDraw.text
    monkeypatch.setattr(PIL.ImageDraw.ImageDraw, "text", lambda self, xy, text, *args, **kwargs: drawn.append(text)
                        or original(self, xy, text, *args, **kwargs))
    word = mw_word.parse_feed(FEED)["2026-11-06"]
    payload = {"word": word, "quote": {"text": "A quote.", "author": "Q"}, "sources": [word["source"]]}

    plugin._render((800, 480), {}, payload, datetime(2026, 11, 6, 9, 0))

    assert "Did you know?" in drawn
    assert any(text.startswith("Looking for an unusual") for text in drawn)
