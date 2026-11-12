"""今日黄历 on the calendar: Baidu's almanac read, kept a month, and pinned in the date panel."""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import read_source_provenance  # noqa: E402
from plugins.simple_calendar import cn_almanac, panel_scene  # noqa: E402
from plugins.simple_calendar.cn_almanac import AlmanacProvider  # noqa: E402
from plugins.simple_calendar.panel_scene import Scene  # noqa: E402
from plugins.simple_calendar.simple_calendar import LOCALE_DATA, SimpleCalendar  # noqa: E402

# Four of the 92 days opendata.baidu.com answered for "2026年11月" on 2026-11-11 (the fields the
# page reads, and two it does not), after decoding from GBK.
PAYLOAD = {"status": "0", "data": [{"OriginQuery": "2026年11月", "almanac": [
    {"animal": "马", "avoid": "馀事勿取", "day": "7", "festivalList": "立冬", "gzDate": "乙酉", "gzMonth": "己亥",
     "gzYear": "丙午", "lDate": "廿九", "lMonth": "九", "month": "11", "oDate": "2026-11-06T16:00:00.000Z",
     "suit": "打扫", "term": "立冬", "year": "2026"},
    {"animal": "马", "avoid": "结婚.出行.搬家.搬新房.动土.祈福.栽种.安葬.成人礼.出火.伐木.上梁", "day": "11", "gzDate": "己丑",
     "gzMonth": "己亥", "gzYear": "丙午", "lDate": "初三", "lMonth": "十", "month": "11",
     "oDate": "2026-11-10T16:00:00.000Z", "suit": "会亲友.理发.买衣服.安床.安门.结网.开光", "term": "", "year": "2026"},
    {"animal": "马", "avoid": "买车.安床.作灶", "day": "22", "festivalList": "小雪", "gzDate": "庚子", "gzMonth": "己亥",
     "gzYear": "丙午", "lDate": "十四", "lMonth": "十", "month": "11", "oDate": "2026-11-21T16:00:00.000Z",
     "suit": "结婚.会亲友.出行.打扫.搬家", "term": "小雪", "year": "2026"},
    {"animal": "马", "avoid": "搬家.搬新房", "day": "22", "festivalList": "冬至,一九天", "gzDate": "庚午", "gzMonth": "庚子",
     "gzYear": "丙午", "lDate": "十四", "lMonth": "十一", "month": "12", "oDate": "2026-12-21T16:00:00.000Z",
     "suit": "破屋.治病.馀事勿取.坏垣", "term": "冬至 一九天", "year": "2026"},
    {"day": "31", "month": "11", "year": "2026", "gzDate": "坏"}, "not a day"]}]}
TODAY = date(2026, 11, 11)
NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)


def test_each_day_keeps_only_its_ganzhi_its_lists_and_its_solar_term():
    days = cn_almanac.days_from_payload(PAYLOAD)

    assert sorted(days) == ["2026-11-07", "2026-11-11", "2026-11-22", "2026-12-22"]
    assert days["2026-11-11"] == {"ganzhi": ["丙午", "己亥", "己丑"],
                                  "suit": ["会亲友", "理发", "买衣服", "安床", "安门", "结网", "开光"],
                                  "avoid": ["结婚", "出行", "搬家", "搬新房", "动土", "祈福", "栽种", "安葬", "成人礼", "出火",
                                            "伐木", "上梁"]}
    # Baidu's "term" mixes the 24 solar terms with festivals and the 数九 count: only a solar term is kept.
    assert (days["2026-11-07"]["term"], days["2026-12-22"]["term"]) == ("立冬", "冬至")
    for broken in ({}, {"data": []}, {"data": [{"almanac": "busy"}]}, None):
        with pytest.raises(ValueError):
            cn_almanac.days_from_payload(broken)


def test_the_days_note_names_its_ganzhi_and_the_solar_term_today_or_next():
    days = cn_almanac.days_from_payload(PAYLOAD)

    note = cn_almanac.day_note(days, TODAY)
    assert note["ganzhi"] == "丙午年 己亥月 己丑日"
    assert (note["suit"][:2], note["avoid"][:2]) == (["会亲友", "理发"], ["结婚", "出行"])
    assert note["term"] == "小雪 11/22 还有11天"
    assert cn_almanac.day_note(days, date(2026, 11, 7))["term"] == "今日立冬"
    assert cn_almanac.day_note({**days, "2026-11-21": days["2026-11-11"]}, date(2026, 11, 21))["term"] == "明日小雪"
    # No solar term among the kept days ahead: the note goes without; a day that is not kept has no note.
    assert "term" not in cn_almanac.day_note({"2026-11-11": days["2026-11-11"]}, TODAY)
    assert cn_almanac.day_note(days, date(2026, 11, 12)) is None and cn_almanac.day_note({}, TODAY) is None


def test_the_hosts_line_is_one_thing_to_do_and_one_to_avoid():
    days = cn_almanac.days_from_payload(PAYLOAD)

    assert cn_almanac.balloon_line(cn_almanac.day_note(days, TODAY)) == "宜会亲友 忌结婚"
    # A day whose 忌 is a saying is quoted whole.
    assert cn_almanac.balloon_line(cn_almanac.day_note(days, date(2026, 11, 7))) == "宜打扫 馀事勿取"
    assert cn_almanac.balloon_line({"suit": ["无"], "avoid": ["诸事不宜"]}) == "诸事不宜"
    assert cn_almanac.balloon_line({"suit": [], "avoid": []}) == "" and cn_almanac.balloon_line(None) == ""


def provider(tmp_path, answers):
    asked = []

    def fetch(year, month, context=None):
        asked.append((year, month))
        answer = answers[min(len(asked), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return AlmanacProvider(tmp_path / "almanac", fetch=fetch), asked


def test_the_almanac_is_asked_once_a_month_and_read_from_its_small_state_file(tmp_path):
    almanac, asked = provider(tmp_path, [PAYLOAD])

    days = almanac.refresh(TODAY, now=NOW)

    assert asked == [(2026, 11)] and days["2026-11-11"]["ganzhi"] == ["丙午", "己亥", "己丑"]
    state = json.loads(almanac.path.read_text(encoding="utf-8"))
    assert state["month"] == "2026-11" and almanac.path.stat().st_size < 4096
    # Every later refresh of the month, and every redraw, reads the file.
    assert almanac.refresh(date(2026, 11, 22), now=NOW + timedelta(days=11)) == days
    assert AlmanacProvider(tmp_path / "almanac", fetch=lambda *_a, **_k: pytest.fail("a redraw never asks")).read() == days
    assert asked == [(2026, 11)]
    # The month turns: one more request, for the new month.
    almanac.refresh(date(2026, 12, 1), now=NOW + timedelta(days=20))
    assert asked == [(2026, 11), (2026, 12)]


def test_a_failed_request_keeps_the_last_good_days_and_waits_before_asking_again(tmp_path):
    almanac, asked = provider(tmp_path, [PAYLOAD, RuntimeError("offline"), ValueError("changed shape"), PAYLOAD])
    good = almanac.refresh(TODAY, now=NOW)

    turned = NOW + timedelta(days=20)
    assert almanac.refresh(date(2026, 12, 1), now=turned) == good and len(asked) == 2
    # Not again within six hours of a failure, however often the page refreshes.
    assert almanac.refresh(date(2026, 12, 1), now=turned + timedelta(hours=5)) == good and len(asked) == 2
    assert almanac.refresh(date(2026, 12, 1), now=turned + timedelta(hours=7)) == good and len(asked) == 3
    assert almanac.refresh(date(2026, 12, 2), now=turned + timedelta(hours=14)) == good and len(asked) == 4
    assert json.loads(almanac.path.read_text(encoding="utf-8"))["month"] == "2026-12"


def test_nothing_kept_and_nothing_answered_gives_no_days_and_a_redraw_writes_nothing(tmp_path):
    almanac, asked = provider(tmp_path, [RuntimeError("offline")])

    assert AlmanacProvider(tmp_path / "cold").read() == {} and not (tmp_path / "cold").exists()
    assert almanac.refresh(TODAY, now=NOW) == {} and asked == [(2026, 11)]
    almanac.path.write_text("{not json", encoding="utf-8")
    assert almanac.read() == {}


def test_a_cancelled_task_is_not_swallowed(tmp_path):
    from runtime.refresh_contracts import TaskCancelled

    almanac, _asked = provider(tmp_path, [TaskCancelled("stop")])

    with pytest.raises(TaskCancelled):
        almanac.refresh(TODAY, now=NOW)


def test_the_month_is_asked_from_baidu_in_gbk_with_a_size_cap(monkeypatch):
    asked = {}

    class Client:
        def request_text(self, method, url, **kwargs):
            asked.update(method=method, url=url, **kwargs)
            return type("Result", (), {"data": json.dumps(PAYLOAD, ensure_ascii=False)})()

    monkeypatch.setattr(cn_almanac, "get_http_client", lambda: Client())

    assert cn_almanac.fetch_month(2026, 11)["data"][0]["OriginQuery"] == "2026年11月"
    assert (asked["method"], asked["url"]) == ("GET", "https://opendata.baidu.com/api.php")
    assert asked["params"] == {"tn": "wisetpl", "format": "json", "resource_id": "39043", "query": "2026年11月"}
    assert asked["encoding"] == "gbk" and asked["max_bytes"] <= 300 * 1024 and asked["timeout"] <= 15


def test_the_host_adds_the_almanac_only_to_a_one_line_balloon():
    one = Scene("sky_runner", "sit", None, ("离周末还有 3 天",), "day", "everyday")
    two = Scene("clockwork_robot", "point", "appointment", ("上午 11:45 预约", "还有 2 小时"), "day", "appointment")

    assert panel_scene.with_almanac(one, "宜会亲友 忌结婚").lines == ("离周末还有 3 天", "宜会亲友 忌结婚")
    assert panel_scene.with_almanac(two, "宜会亲友 忌结婚") is two
    assert panel_scene.with_almanac(one, "") is one and panel_scene.with_almanac(None, "宜理发") is None


# --- on the page -------------------------------------------------------------------------------------

NOTE = {"ganzhi": "丙午年 己亥月 己丑日", "suit": ["会亲友", "理发", "买衣服", "安床", "安门", "结网", "开光"],
        "avoid": ["结婚", "出行", "搬家", "搬新房", "动土", "祈福", "栽种", "安葬", "成人礼", "出火", "伐木", "上梁"],
        "term": "小雪 11/22 还有11天"}
NIGHT = {"accent": (105, 185, 197), "background": (11, 21, 24), "panel": (28, 42, 53), "rule": (90, 90, 90),
         "ink": (237, 241, 234), "muted": (198, 208, 213)}
VETERANS = {"date": TODAY, "label": "US", "title": "Veterans Day", "color": (0, 74, 173)}


def render(events=(), almanac=None, scene=None, theme=None, drawn=None, monkeypatch=None):
    if drawn is not None:
        original = ImageDraw.ImageDraw.text
        monkeypatch.setattr(ImageDraw.ImageDraw, "text",
                            lambda self, xy, text, *args, **kwargs: drawn.append((str(text), tuple(xy), kwargs.get("fill")))
                            or original(self, xy, text, *args, **kwargs))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    return plugin._render_calendar(
        (800, 480), TODAY, (46, 46, 46), (163, 13, 13), LOCALE_DATA["en"], "en", "left", list(events), None,
        scene is not None, datetime(2026, 11, 11, 10, 0), theme, scene, almanac=almanac)


@pytest.mark.parametrize("theme", [None, NIGHT])
def test_a_day_without_an_event_pins_the_almanac_slip_where_the_event_card_goes(monkeypatch, theme):
    drawn = []
    image = render(almanac=NOTE, theme=theme, drawn=drawn, monkeypatch=monkeypatch)
    plain = render(theme=theme)

    words = [text for text, _xy, _fill in drawn]
    assert {"黄历", "丙午年 己亥月 己丑日", "宜", "忌", "小雪 11/22 还有11天"} <= set(words)
    # As many whole items as each line holds, in the almanac's order, never a cut one.
    suit = next(text for text in words if text.startswith("会亲友"))
    avoid = next(text for text in words if text.startswith("结婚"))
    assert suit.split(" ") == NOTE["suit"][:len(suit.split(" "))] and len(suit.split(" ")) >= 3
    assert avoid.split(" ") == NOTE["avoid"][:len(avoid.split(" "))] and len(avoid.split(" ")) >= 3
    # Every word of it is in the slip's ink; colour is only on the stamps under 宜 / 忌 / 节.
    ink = next(fill for text, _xy, fill in drawn if text == "丙午年 己亥月 己丑日")
    assert all(fill == ink for text, _xy, fill in drawn if text in (suit, avoid, "小雪 11/22 还有11天"))
    # The slip stays in the panel's top slot: above the weekday, left of the calendar page.
    box = image.info["simple_calendar_almanac"]["bounds"]
    assert 0 < box[0] and box[2] < 304 and 0 <= box[1] and box[3] <= 114
    changed = [(x, y) for x in range(0, 800, 2) for y in range(0, 480, 2) if image.getpixel((x, y)) != plain.getpixel((x, y))]
    assert changed and all(box[0] - 1 <= x <= box[2] + 6 and box[1] - 1 <= y <= box[3] + 6 for x, y in changed)


def test_the_month_grid_and_its_marks_are_untouched_by_the_almanac():
    with_note, without = render(almanac=NOTE), render()

    assert with_note.crop((304, 0, 800, 480)).tobytes() == without.crop((304, 0, 800, 480)).tobytes()
    assert with_note.crop((0, 115, 304, 480)).tobytes() == without.crop((0, 115, 304, 480)).tobytes()


def test_on_an_event_day_the_card_stays_and_the_host_says_the_almanac_in_a_free_line(monkeypatch):
    scene = Scene("sky_runner", "sit", None, ("离周末还有 3 天",), "day", "everyday")
    drawn = []
    image = render([VETERANS], almanac=NOTE, scene=scene, drawn=drawn, monkeypatch=monkeypatch)
    card_only = render([VETERANS], scene=scene)

    words = [text for text, _xy, _fill in drawn]
    assert "Veterans Day" in words and "黄历" not in words and "simple_calendar_almanac" not in image.info
    assert "宜会亲友 忌结婚" in words and "离周末还有 3 天" in words
    # The event's card is drawn exactly as without the almanac.
    assert image.crop((0, 0, 304, 114)).tobytes() == card_only.crop((0, 0, 304, 114)).tobytes()
    # A host with two lines of its own keeps them.
    busy = Scene("clockwork_robot", "point", "appointment", ("上午 11:45 预约", "还有 2 小时"), "day", "appointment")
    drawn.clear()
    render([VETERANS], almanac=NOTE, scene=busy, drawn=drawn, monkeypatch=monkeypatch)
    assert "宜会亲友 忌结婚" not in [text for text, _xy, _fill in drawn]


def test_short_lists_and_a_missing_term_make_a_smaller_slip(monkeypatch):
    drawn = []
    image = render(almanac={"ganzhi": "丙午年 己亥月 乙酉日", "suit": ["打扫"], "avoid": ["馀事勿取"]}, drawn=drawn,
                   monkeypatch=monkeypatch)
    full = render(almanac=NOTE)

    assert {"打扫", "馀事勿取"} <= {text for text, _xy, _fill in drawn}
    small, large = image.info["simple_calendar_almanac"]["bounds"], full.info["simple_calendar_almanac"]["bounds"]
    assert small[3] - small[1] < large[3] - large[1]


class Device:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal", "timezone": "America/Los_Angeles"}
        return values if key is None else values.get(key, default)


SETTINGS = {"customDate": "2026-11-11", "showHolidays": "false", "showPersonalCalendars": "false",
            "weatherPanelBackground": "false", "dateHeroOverlays": "false"}


def test_a_refresh_asks_the_almanac_and_a_redraw_only_reads_it(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    almanac, asked = provider(tmp_path, [PAYLOAD])
    plugin = SimpleCalendar({"id": "simple_calendar"}, almanac_provider=almanac)

    refreshed = plugin.generate_image(dict(SETTINGS), Device())

    assert asked == [(2026, 11)] and "simple_calendar_almanac" in refreshed.info
    # Theme redraws, display redraws and prepared presentations read the kept days.
    for flag in ("_theme_render_only", SimpleCalendar._DISPLAY_RENDER_SETTING):
        redrawn = plugin.generate_image({**SETTINGS, flag: "true"}, Device())
        assert "simple_calendar_almanac" in redrawn.info
    assert asked == [(2026, 11)]


def test_a_failing_almanac_leaves_the_page_and_its_provenance_as_they_were(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    failing, asked = provider(tmp_path, [RuntimeError("offline")])
    never, _ = provider(tmp_path / "other", [PAYLOAD])
    never.refresh = lambda *_args, **_kwargs: {}
    never.read = lambda: {}

    failed = SimpleCalendar({"id": "simple_calendar"}, almanac_provider=failing).generate_image(dict(SETTINGS), Device())
    without = SimpleCalendar({"id": "simple_calendar"}, almanac_provider=never).generate_image(dict(SETTINGS), Device())

    assert asked == [(2026, 11)] and "simple_calendar_almanac" not in failed.info
    assert failed.tobytes() == without.tobytes()
    assert read_source_provenance(failed) == read_source_provenance(without)


def test_the_default_provider_stays_offline_in_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cn_almanac, "get_http_client", lambda: pytest.fail("tests must not reach Baidu"))

    image = SimpleCalendar({"id": "simple_calendar"}).generate_image(dict(SETTINGS), Device())

    assert "simple_calendar_almanac" not in image.info
    assert not (tmp_path / "data" / "plugins" / "simple_calendar" / "cn_almanac").exists()
