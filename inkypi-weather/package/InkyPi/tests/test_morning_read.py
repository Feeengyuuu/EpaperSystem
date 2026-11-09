import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.comic import comic_parser  # noqa: E402
from plugins.daily_knowledge.daily_knowledge import DailyKnowledge  # noqa: E402
from plugins.daily_word_poem.daily_word_poem import DailyWordPoem  # noqa: E402
from plugins.morning_read import morning_read, render, sources  # noqa: E402
from plugins.morning_read.morning_read import MorningRead  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402

NOW = datetime(2026, 11, 9, 7, 30, tzinfo=sources.PACIFIC)
WORDS = {"word": {"word": "wabi-sabi", "phonetic": "/WAH-bee-SAH-bee/", "part_of_speech": "noun",
                  "definition": "Wabi-sabi refers to a philosophy of Japanese origin that emphasizes the aesthetic "
                                "value in imperfect, impermanent, and simple things. This word can also refer to the "
                                "qualities of imperfection, impermanence, and simplicity considered as beautiful.",
                  "example": "The gardener learned to embrace wabi-sabi.", "source": "Merriam-Webster Word of the Day"},
         "quote": {"text": "We speak for Earth.", "author": "Carl Sagan", "source": "Wikiquote QOTD"}}
FACTS = {"facts": [{"text": "Octopuses have three hearts.", "source": "uselessfacts", "language": "en"},
                   {"text": "章鱼的神经元有相当一部分分布在腕足中。", "source": "本地知识", "language": "zh"}]}
COMIC = {"name": "XKCD", "title": "Simulation", "caption": "Mashing planets together.",
         "image": "https://imgs.xkcd.com/comics/simulation.png"}


def _context(seconds=30):
    return TaskContext(threading.Event(), time.monotonic() + seconds, time.monotonic)


class _Device:
    def get_config(self, key=None, default=None):
        full = {"timezone": "America/Los_Angeles", "resolution": [800, 480]}
        return full if key is None else full.get(key, default)

    def get_resolution(self):
        return (800, 480)


def _payload(words=WORDS, facts=FACTS, comic=COMIC):
    return {"words": {"data": words or {}, "state": "live"}, "facts": {"data": facts or {}, "state": "live"},
            "comic": {"data": comic or {}, "state": "live"}}


def test_word_and_quote_come_from_daily_word_poem(monkeypatch):
    seen = []

    def payload(self, settings, now):
        seen.append((self.get_plugin_id(), settings, now))
        return {"word": {**WORDS["word"], "did_you_know": "dropped"}, "quote": {**WORDS["quote"], "topic": "x"}}

    monkeypatch.setattr(DailyWordPoem, "_daily_payload", payload)
    data = sources.fetch_word_and_quote(NOW)
    assert seen == [("daily_word_poem", {}, NOW)]
    assert data == WORDS and sources.valid_word_and_quote(data)
    assert not sources.valid_word_and_quote({"word": {"word": ""}, "quote": {"text": "x"}})


def test_two_facts_come_from_daily_knowledge_spaces_collapsed(monkeypatch):
    monkeypatch.setattr(DailyKnowledge, "_daily_payload", lambda self, settings, device, now: {"facts": [
        {"text": "  Octopuses   have three hearts. ", "source": "uselessfacts", "language": "en"},
        {"text": "", "source": "empty"}, FACTS["facts"][1], {"text": "third", "source": "x"}]})
    data = sources.fetch_facts(_Device(), NOW)
    assert [fact["text"] for fact in data["facts"]] == ["Octopuses have three hearts.", FACTS["facts"][1]["text"]]
    assert sources.valid_facts(data) and not sources.valid_facts({"facts": []})


def test_comic_pictures_must_live_on_the_strips_own_hosts_over_https():
    assert sources.safe_comic_image("https://imgs.xkcd.com/comics/a.png") == "https://imgs.xkcd.com/comics/a.png"
    assert sources.safe_comic_image("http://www.qwantz.com/comics/c.png") == "https://www.qwantz.com/comics/c.png"
    assert sources.safe_comic_image("https://64.media.tumblr.com/x/s640/a.png")
    assert sources.safe_comic_image("https://evil.example/a.png") is None
    assert sources.safe_comic_image("ftp://imgs.xkcd.com/a.png") is None
    assert sources.safe_comic_image("") is None


def test_the_strips_take_turns_by_the_day_and_a_dead_feed_gives_way():
    names = list(comic_parser.COMICS)
    assert sources.comic_order(date(2026, 11, 9))[0] != sources.comic_order(date(2026, 11, 10))[0]
    assert sorted(sources.comic_order(date(2026, 11, 9))) == sorted(names)

    order = sources.comic_order(NOW.date())
    panels = {order[0]: {"placeholder": True, "image_url": None},
              order[1]: {"image_url": "https://evil.example/a.png", "title": "bad host"},
              order[2]: {"image_url": "https://imgs.xkcd.com/comics/b.png", "title": " Good  one ", "caption": "alt"}}
    asked = []

    def get_panel(name):
        asked.append(name)
        return panels.get(name, {})

    comic = sources.fetch_comic(NOW, get_panel=get_panel)
    assert asked == order[:3]
    assert comic == {"name": order[2], "title": "Good one", "caption": "alt",
                     "image": "https://imgs.xkcd.com/comics/b.png"}
    with pytest.raises(RuntimeError):
        sources.fetch_comic(NOW, get_panel=lambda name: {"placeholder": True})


def test_first_sentence_keeps_the_text_up_to_its_full_stop():
    assert render.first_sentence("One thing. Another.") == "One thing."
    assert render.first_sentence("No stop at all") == "No stop at all"
    assert render.first_sentence("e.g.value and more. Next.") == "e.g.value and more."


def test_a_strip_fits_its_area_whole_and_centred():
    assert render.comic_box((800, 400), (0, 0, 400, 400)) == (0, 100, 400, 200)
    assert render.comic_box((300, 900), (10, 10, 310, 310)) == (110, 10, 100, 300)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_draws_every_card_by_day_and_night(mode):
    strip = Image.new("RGB", (600, 400), (10, 200, 30))
    page = render.render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, NOW, strip)
    assert page.size == (800, 480)
    assert page.getpixel((598, 300)) == (10, 200, 30)
    colours = {page.getpixel((x, 66)) for x in range(20, 780, 10)}
    assert {render.KINDS["单词"][mode == "night"], render.KINDS["漫画"][mode == "night"]} <= colours


def test_without_a_strip_the_reading_takes_the_width_and_empty_cards_give_way():
    page = render.render_page((800, 480), _payload(comic={}), {}, NOW, None)
    assert page.getpixel((600, 66)) == render.KINDS["单词"][0]
    only_quote = render.render_page((800, 480), _payload(words={"quote": WORDS["quote"]}, facts={}), {}, NOW, None)
    assert only_quote.getpixel((400, 66)) == render.KINDS["名言"][0]
    assert render.render_page((1600, 960), _payload(words={}, facts={}, comic={}), {}, NOW, None).size == (1600, 960)


def test_a_failing_feed_only_loses_its_card_and_a_redraw_never_asks(monkeypatch, tmp_path):
    monkeypatch.setenv("MORNING_READ_CACHE_DIR", str(tmp_path))
    calls = []

    def fake(name, value):
        def fetch(*args, **kwargs):
            calls.append(name)
            if value is None:
                raise RuntimeError(f"{name} is down")
            return value
        return fetch

    monkeypatch.setattr(sources, "fetch_word_and_quote", fake("words", WORDS))
    monkeypatch.setattr(sources, "fetch_facts", fake("facts", None))
    monkeypatch.setattr(sources, "fetch_comic", fake("comic", COMIC))
    plugin = MorningRead({"id": "morning_read"})
    payload = plugin._payload({}, _Device(), NOW, allow_network=True, context=_context())
    assert {name: result["state"] for name, result in payload.items()} == {
        "words": "live", "facts": "unavailable", "comic": "live"}
    assert calls == ["words", "facts", "comic"]

    calls.clear()
    later = plugin._payload({}, _Device(), NOW.replace(hour=20), allow_network=False, context=_context())
    assert calls == [] and later["words"]["state"] == "stale_cache" and later["comic"]["state"] == "stale_cache"


def test_generate_image_fetches_the_strip_once_and_redraws_from_the_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("MORNING_READ_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sources, "fetch_word_and_quote", lambda now: WORDS)
    monkeypatch.setattr(sources, "fetch_facts", lambda device, now: FACTS)
    monkeypatch.setattr(sources, "fetch_comic", lambda now: COMIC)
    loads = []

    def thumbnails(urls, directory, *, cached_only, **kwargs):
        loads.append((tuple(urls), cached_only, kwargs["safe_url"] is sources.safe_comic_image))
        return {url: Image.new("RGB", (500, 300), (10, 200, 30)) for url in urls}

    monkeypatch.setattr(morning_read, "load_thumbnails", thumbnails)
    plugin = MorningRead({"id": "morning_read"})
    assert plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, _Device()).size == (800, 480)
    monkeypatch.setattr(sources, "fetch_comic", lambda now: pytest.fail("a cached redraw asked the feed"))
    assert plugin.generate_image({"_theme_render_only": True, "_inkypi_theme": {"mode": "night"}},
                                 _Device()).size == (800, 480)
    assert loads == [((COMIC["image"],), False, True), ((COMIC["image"],), True, True)]
