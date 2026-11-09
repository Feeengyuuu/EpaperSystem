import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.time_almanac import render, sources  # noqa: E402
from plugins.time_almanac.time_almanac import TimeAlmanac  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402
from utils.page_canvas import PageCanvas  # noqa: E402
from utils.rich_text import draw_rich, fit_box, rich_lines  # noqa: E402

NOW = datetime(2026, 11, 9, 11, 57, tzinfo=sources.PACIFIC)
SKY = {"date": "2026-11-09", "moonrise": "07:18", "moonset": "17:00", "sunrise": "06:42", "sunset": "17:02"}


def _context(seconds=30):
    return TaskContext(threading.Event(), time.monotonic() + seconds, time.monotonic)


class _Device:
    def __init__(self, home=True):
        self.home = home

    def get_config(self, key=None, default=None):
        plugins = [{"plugin_id": "weather", "plugin_settings": {"latitude": "37.5485", "longitude": "-121.9886"}}]
        full = {"timezone": "America/Los_Angeles", "resolution": [800, 480],
                "playlist_config": {"playlists": [{"plugins": plugins if self.home else []}]}}
        return full if key is None else full.get(key, default)

    def get_resolution(self):
        return (800, 480)


def _payload(now=NOW, settings=None, sky=SKY):
    return {"sky": {"data": dict(sky or {}), "state": "live"}, "clocks": sources.clock_lines(now),
            "moon": sources.moon(now), "progress": sources.progress(now),
            "countdowns": sources.countdowns(now.date(), settings)}


def test_both_clocks_name_this_minute_and_the_same_minute_picks_the_same_lines():
    lines = sources.clock_lines(NOW.replace(hour=12, minute=0))

    for line in lines.values():
        assert line["quote"] and line["time"]
        assert sources.highlight_span(line["quote"], line["time"]) is not None
        assert "  " not in line["quote"]
    assert sources.clock_lines(NOW.replace(hour=12, minute=0)) == lines
    # A minute no Chinese novel names falls back to its 时辰 or watch, so the line is never empty.
    assert sources.clock_lines(NOW.replace(hour=3, minute=17))["zh"]["quote"]


def test_the_highlight_is_found_whatever_its_case():
    assert sources.highlight_span("It was Noon, and late.", "noon") == (7, 11)
    assert sources.highlight_span("no time here", "noon") is None
    assert sources.highlight_span("", "noon") is None


def test_the_moon_reads_in_chinese_with_its_next_phases_in_local_time():
    full = sources.moon(datetime(2026, 11, 24, 7, 0, tzinfo=sources.PACIFIC))
    assert full["name"] == "满月" and full["illumination"] > 0.99
    young = sources.moon(NOW)
    assert young["name"] == "娥眉月" and young["waxing"]
    names = [name for _moment, name in young["next"]]
    assert names == ["上弦", "满月", "下弦", "新月"]
    assert all(moment.tzinfo == NOW.tzinfo for moment, _name in young["next"])


def test_progress_counts_the_day_week_month_and_year():
    rows = sources.progress(NOW)
    assert [label for label, _share, _note in rows] == ["今日", "本周", "本月", "今年"]
    assert rows[0][2] == "还剩 12 小时 03 分"
    assert rows[1][2] == "周一" and rows[2][2] == "第 9 / 30 天" and rows[3][2] == "第 313 / 365 天"
    assert all(0 <= share <= 1 for _label, share, _note in rows)


def test_holidays_come_in_order_with_thanksgiving_and_the_lunar_festivals():
    days = dict((name, day) for day, name in sources.holidays(2027))
    assert days["感恩节"] == date(2027, 11, 25) and days["春节"] == date(2027, 2, 6)
    assert days["中秋"] == date(2027, 9, 15)
    rows = sources.countdowns(date(2026, 11, 9))
    assert [(name, days) for _day, name, days, _own in rows] == [
        ("感恩节", 17), ("圣诞节", 46), ("元旦", 53), ("春节", 89)]
    assert sources.countdowns(date(2026, 11, 26))[0][1:3] == ("感恩节", 0)


def test_the_owners_own_date_leads_even_once_passed():
    rows = sources.countdowns(date(2026, 11, 9), {"countdownTitle": "  旅行   出发  ", "countdownDate": "2026-12-20"})
    assert rows[0] == (date(2026, 12, 20), "旅行 出发", 41, True) and len(rows) == sources.COUNTDOWN_ROWS
    passed = sources.countdowns(date(2026, 11, 9), {"countdownDate": "2026-11-01"})
    assert passed[0][1:] == ("我的日子", -8, True)
    assert sources.countdowns(date(2026, 11, 9), {"countdownDate": "next week"})[0][1] == "感恩节"


def test_rich_lines_keep_each_lines_place_in_the_text_and_hang_closing_punctuation():
    canvas = PageCanvas({"paper": (255, 255, 255), "ink": (0, 0, 0)})
    text = "行者念咒語，驚動太子，把旗捲起，卻早見日光正午。"
    lines = rich_lines(canvas, text, 20, 200, 6)
    assert "".join(line for line, _start in lines) == text
    for line, start in lines:
        assert text[start:start + len(line)] == line
        assert line[0] not in "，。"
    assert rich_lines(canvas, text, 20, 60, 2) is None
    size, kept, line_height = fit_box(canvas, text, (30, 20), 200, 60)
    assert size == 20 and len(kept) * line_height <= 60


def test_the_time_is_drawn_in_the_cards_colour():
    canvas = PageCanvas({"paper": (255, 255, 255), "ink": (0, 0, 0), "accent": (200, 0, 0)})
    text = "It was noon exactly."
    lines = rich_lines(canvas, text, 20, 400, 2)
    draw_rich(canvas, 0, 0, lines, 20, 26, sources.highlight_span(text, "noon"), "accent")
    colours = {canvas.image.getpixel((x, y)) for x in range(0, 200) for y in range(0, 26)}
    assert (200, 0, 0) in colours


def test_a_long_english_line_gives_way_to_the_chinese_one():
    canvas = PageCanvas(render.DAY)
    zh = {"quote": "正午。" * 4, "time": "正午", "book": "西游记", "author": "吴承恩"}
    en = {"quote": "word " * 400, "time": "noon", "book": "Long", "author": "Writer"}
    zh_block, en_block = render.clock_layout(canvas, zh, en, 430, 230)
    assert zh_block is not None and en_block is None
    zh_block, en_block = render.clock_layout(canvas, zh, dict(en, quote="It was noon."), 430, 230)
    assert en_block is not None and en_block[0] < zh_block[0]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_draws_every_card_by_day_and_night(mode):
    page = render.render_page((800, 480), _payload(settings={"countdownDate": "2026-12-20"}),
                              {"_inkypi_theme": {"mode": mode}}, NOW)
    assert page.size == (800, 480)
    paper = page.getpixel((400, 470))
    assert paper == ((0, 0, 0) if mode == "night" else (255, 255, 255))
    colours = {page.getpixel((x, 66)) for x in range(20, 460, 10)}
    assert render.KINDS["报时"][mode == "night"] in colours


def test_without_rise_and_set_times_the_moon_card_still_draws_and_other_sizes_scale():
    page = render.render_page((1600, 960), _payload(sky={}), {}, NOW)
    assert page.size == (1600, 960)
    empty = {"sky": {}, "clocks": {}, "moon": sources.moon(NOW), "progress": [], "countdowns": []}
    assert render.render_page((800, 480), empty, {}, NOW).size == (800, 480)


def test_the_rise_and_set_times_are_fetched_once_a_day_and_a_redraw_never_asks(monkeypatch, tmp_path):
    monkeypatch.setenv("TIME_ALMANAC_CACHE_DIR", str(tmp_path))
    calls = []

    def fetch(home, day, offset, context):
        calls.append((home, day, offset))
        return dict(SKY, date=day.isoformat())

    monkeypatch.setattr(sources, "fetch_sky_times", fetch)
    plugin = TimeAlmanac({"id": "time_almanac"})
    sky = plugin._sky({}, _Device(), NOW, allow_network=True, context=_context())
    assert sky["state"] == "live" and sky["data"]["moonrise"] == "07:18"
    assert calls == [((37.5485, -121.9886), NOW.date(), -8.0)]

    again = plugin._sky({}, _Device(), NOW.replace(hour=18), allow_network=False, context=_context())
    assert again["state"] == "fresh_cache" and len(calls) == 1
    # No page names a place: the moon card goes without rise and set times, and nothing is asked.
    alone = plugin._sky({}, _Device(home=False), NOW, allow_network=True, context=_context())
    assert alone["state"] == "disabled" and len(calls) == 1


def test_generate_image_redraws_from_the_cache_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("TIME_ALMANAC_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sources, "fetch_sky_times", lambda *args: pytest.fail("a cached redraw asked USNO"))
    image = TimeAlmanac({"id": "time_almanac"}).generate_image(
        {"_theme_render_only": True, "_inkypi_theme": {"mode": "day"}}, _Device())
    assert image.size == (800, 480)
