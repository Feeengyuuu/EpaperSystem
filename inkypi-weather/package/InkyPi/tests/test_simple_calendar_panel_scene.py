"""Who the date panel's host is and what it says (pure scene choice)."""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.simple_calendar import panel_scene  # noqa: E402
from plugins.simple_calendar.panel_scene import choose_scene  # noqa: E402

TZ = timezone(timedelta(hours=-7))
THURSDAY = date(2026, 10, 22)


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def plan(day, hour, minute, title="Dentist appointment"):
    return {"date": day, "title": title, "label": "ME", "kind": "personal", "starts_at": at(day, hour, minute)}


def holiday(day, title, label="US"):
    return {"date": day, "title": title, "label": label, "kind": "holiday"}


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(4, 59, "night"), (5, 0, "dawn"), (8, 59, "dawn"), (9, 0, "day"),
     (16, 59, "day"), (17, 0, "dusk"), (19, 59, "dusk"), (20, 0, "night")],
)
def test_daypart_boundaries(hour, minute, expected):
    assert panel_scene.daypart(at(THURSDAY, hour, minute)) == expected


def test_upcoming_appointment_brings_the_robot_counting_down():
    scene = choose_scene([plan(THURSDAY, 11, 45)], THURSDAY, at(THURSDAY, 9, 40))

    assert scene.host == "clockwork_robot"
    assert scene.costume == "appointment"
    assert scene.lines == ("上午 11:45 预约", "还有 2 小时")
    assert choose_scene([plan(THURSDAY, 11, 45)], THURSDAY, at(THURSDAY, 11, 20)).lines[1] == "还有 25 分钟"


def test_a_past_appointment_leaves_the_stage():
    scene = choose_scene([plan(THURSDAY, 11, 45)], THURSDAY, at(THURSDAY, 12, 30))

    assert scene.occasion != "appointment"
    assert scene.costume is None


def test_festival_brings_its_costume_greeting_and_the_next_plan():
    halloween = date(2026, 10, 31)
    events = [holiday(halloween, "Halloween"), plan(halloween, 15, 0, "Team sync")]

    scene = choose_scene(events, halloween, at(halloween, 10))

    assert (scene.host, scene.costume, scene.occasion) == ("moon_magnet", "halloween", "festival:halloween")
    assert scene.lines == ("万圣节快乐！", "下午 3:00 会议")


def test_chinese_festival_titles_are_recognised():
    day = date(2026, 9, 25)

    scene = choose_scene([holiday(day, "中秋节", "CN")], day, at(day, 19))

    assert scene.costume == "mid_autumn"
    assert scene.lines == ("中秋快乐！",)


def test_launch_day_brings_the_rocket_inventor():
    event = {"date": THURSDAY, "title": "Nintendo Direct", "label": "GAME", "starts_at": at(THURSDAY, 7)}

    scene = choose_scene([event], THURSDAY, at(THURSDAY, 6))

    assert (scene.host, scene.costume) == ("rocket_inventor", "launch")
    assert scene.lines == ("今天有发布会", "上午 7:00 开始")


def test_payday_is_announced():
    scene = choose_scene([{"date": THURSDAY, "title": "Pay Salary/401(k)", "label": "ME"}], THURSDAY,
                         at(THURSDAY, 13))

    assert scene.costume == "payday"
    assert scene.lines == ("今天发薪日！",)


def test_rain_casts_the_umbrella_glider_with_advice():
    scene = choose_scene([], THURSDAY, at(THURSDAY, 13), weather_slug="rain")

    assert (scene.host, scene.pose, scene.costume) == ("umbrella_glider", "sit", None)
    assert scene.lines[-1] == "下雨记得带伞"


def test_night_brings_the_moon_magnet_asleep():
    scene = choose_scene([], THURSDAY, at(THURSDAY, 22))

    assert (scene.host, scene.pose) == ("moon_magnet", "sleep")
    assert "晚安，明天见" in scene.lines


def test_ordinary_day_counts_down_to_the_next_notable_day():
    monday = date(2026, 10, 26)
    events = [holiday(date(2026, 10, 31), "Halloween")]

    assert choose_scene(events, monday, at(monday, 13)).lines[0] == "万圣节还有 5 天"
    tomorrow_plan = plan(monday + timedelta(days=1), 10, 50)
    assert choose_scene(events + [tomorrow_plan], monday, at(monday, 13)).lines[0] == "明天上午10:50有预约"


def test_quiet_days_say_where_the_week_stands():
    assert choose_scene([], date(2026, 10, 26), at(date(2026, 10, 26), 13)).lines == ("离周末还有 5 天",)
    assert choose_scene([], date(2026, 10, 23), at(date(2026, 10, 23), 13)).lines == ("周五啦，周末快到了",)
    saturday = choose_scene([], date(2026, 10, 24), at(date(2026, 10, 24), 13))
    assert saturday.host == "shield_skater"
    assert saturday.lines == ("周末愉快",)


def test_everyday_host_is_stable_within_a_daypart():
    first = choose_scene([], THURSDAY, at(THURSDAY, 10))
    later = choose_scene([], THURSDAY, at(THURSDAY, 15, 30))

    assert first.host == later.host
    assert first.host in panel_scene.EVERYDAY_HOSTS


def test_events_without_real_dates_are_ignored():
    events = [{"date": "2026-10-22", "title": "Halloween"}, None, {"title": "Payday"}]

    scene = choose_scene(events, THURSDAY, at(THURSDAY, 13))

    assert scene.occasion == "everyday"


def test_until_text_rounds_to_what_a_glance_needs():
    now = at(THURSDAY, 9)

    assert panel_scene.until_text(now + timedelta(seconds=30), now) == "马上开始"
    assert panel_scene.until_text(now + timedelta(minutes=59), now) == "还有 59 分钟"
    assert panel_scene.until_text(now + timedelta(minutes=90), now) == "还有 1.5 小时"
    assert panel_scene.until_text(now + timedelta(hours=11), now) == "今天晚些时候"
    assert panel_scene.days_text(2, "春节") == "后天就是春节"
    assert panel_scene.days_text(9, "春节") == "春节还有 9 天"
