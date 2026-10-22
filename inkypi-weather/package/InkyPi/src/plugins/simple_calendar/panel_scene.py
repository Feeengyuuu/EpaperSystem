"""What the date panel's host does right now: who appears, in what pose or
costume, and the one or two lines it says in its speech bubble.

The host is cast from the day itself instead of the weekday: a festival brings
its costume and greeting, a timed appointment brings the clockwork robot
counting down to it, a launch brings the rocket inventor, and an ordinary day
is read from the weather and the time of day. With nothing pressing, the
bubble counts down to the next notable day (a festival, payday, a birthday, a
launch, tomorrow's appointment) or gives the weekend or weather line.

Pure functions only (no images, files or clocks): the caller passes the
events and the moment, so every scene is reproducible in tests.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

HOSTS = (
    "sky_runner", "shield_skater", "rocket_inventor", "umbrella_glider",
    "clockwork_robot", "lightning_messenger", "moon_magnet",
)
DAYPARTS = ("dawn", "day", "dusk", "night")
POSES = ("point", "wave", "sit", "sleep")
COUNTDOWN_DAYS = 14
EVERYDAY_HOSTS = ("sky_runner", "shield_skater", "rocket_inventor", "lightning_messenger")
WEATHER_HOSTS = {
    "thunderstorm": "lightning_messenger",
    "rain": "umbrella_glider",
    "fog": "umbrella_glider",
    "snow": "shield_skater",
}
WEATHER_TIPS = {
    "rain": "下雨记得带伞",
    "thunderstorm": "雷雨天少出门",
    "snow": "下雪了，注意保暖",
    "fog": "起雾了，慢慢走",
}
DAYPART_GREETINGS = {"dawn": "早上好！", "dusk": "傍晚好，辛苦了", "night": "晚安，明天见"}
WEEKDAY_NAMES = "一二三四五六日"

# (key, title patterns, Chinese name, greeting, host)
FESTIVALS = (
    ("halloween", ("halloween", "万圣"), "万圣节", "万圣节快乐！", "moon_magnet"),
    ("christmas", ("christmas", "圣诞"), "圣诞节", "圣诞快乐！", "sky_runner"),
    ("new_year", ("new year's day", "new years day", "元旦"), "元旦", "新年快乐！", "sky_runner"),
    ("spring_festival", ("lunar new year", "chinese new year", "spring festival", "春节", "除夕"),
     "春节", "过年好！", "lightning_messenger"),
    ("lantern", ("lantern festival", "元宵"), "元宵节", "元宵节快乐！", "umbrella_glider"),
    ("mid_autumn", ("mid-autumn", "mid autumn", "中秋"), "中秋节", "中秋快乐！", "moon_magnet"),
    ("dragon_boat", ("dragon boat", "端午"), "端午节", "端午安康！", "shield_skater"),
    ("thanksgiving", ("thanksgiving", "感恩节"), "感恩节", "感恩节快乐！", "clockwork_robot"),
    ("valentines", ("valentine", "情人节"), "情人节", "情人节快乐！", "umbrella_glider"),
    ("independence_day", ("independence day",), "独立日", "独立日快乐！", "sky_runner"),
    ("mothers_day", ("mother's day", "mothers day", "母亲节"), "母亲节", "母亲节快乐！", "umbrella_glider"),
    ("fathers_day", ("father's day", "fathers day", "父亲节"), "父亲节", "父亲节快乐！", "clockwork_robot"),
    ("national_day", ("国庆",), "国庆节", "国庆快乐！", "sky_runner"),
    ("labor_day", ("labor day", "labour day", "劳动节"), "劳动节", "劳动节快乐！", "shield_skater"),
    ("qingming", ("qingming", "清明"), "清明节", "清明安康", "umbrella_glider"),
)
OCCASION_HOSTS = {
    "birthday": "sky_runner",
    "appointment": "clockwork_robot",
    "payday": "lightning_messenger",
    "launch": "rocket_inventor",
}
_PAYDAY = re.compile(r"\bpay\s?day\b|\bpay salary\b|\bsalary\b|\bpaycheck\b|工资|发薪|薪水", re.I)
_BIRTHDAY = re.compile(r"birthday|生日", re.I)
_KINDS_OF_PLAN = (
    (re.compile(r"appointment|visit|doctor|dentist|clinic|therapy|门诊|看病|复诊|预约|就诊", re.I), "预约"),
    (re.compile(r"interview|面试", re.I), "面试"),
    (re.compile(r"flight|航班|起飞", re.I), "航班"),
    (re.compile(r"meeting|call|sync|standup|1:1|review|会议|开会", re.I), "会议"),
    (re.compile(r"class|lesson|course|课", re.I), "上课"),
)


@dataclass(frozen=True)
class Scene:
    host: str
    pose: str | None          # an optional pose drawing; None keeps the host's own drawing
    costume: str | None       # an occasion drawing that replaces host and pose when present
    lines: tuple[str, ...]    # one or two bubble lines
    daypart: str
    occasion: str             # "festival:halloween", "appointment", "weather", ...


def daypart(moment):
    hour = moment.hour
    if 5 <= hour < 9:
        return "dawn"
    if 9 <= hour < 17:
        return "day"
    if 17 <= hour < 20:
        return "dusk"
    return "night"


def daypart_index(name):
    return DAYPARTS.index(name) if name in DAYPARTS else 1


# Classifying events ------------------------------------------------------------

def _title(event):
    return " ".join(str((event or {}).get("title") or "").split())


def festival_of(event):
    """(key, name, greeting, host) of a festival event, or None."""
    title = _title(event).lower()
    if not title:
        return None
    for key, patterns, name, greeting, host in FESTIVALS:
        if any(pattern in title for pattern in patterns):
            return key, name, greeting, host
    return None


def _is_launch(event):
    return str((event or {}).get("label") or "").upper() in {"GAME", "APPLE"}


def _is_timed_personal(event):
    return str((event or {}).get("kind") or "") == "personal" and isinstance((event or {}).get("starts_at"), datetime)


def plan_kind(event):
    """Short Chinese word for a timed personal event: 预约, 会议, 面试 ..."""
    title = _title(event)
    for pattern, word in _KINDS_OF_PLAN:
        if pattern.search(title):
            return word
    return "日程"


def clock_text(moment):
    """'上午 11:45' / '下午 2:00' for a time today."""
    hour, minute = moment.hour, moment.minute
    half = "上午" if hour < 12 else "下午"
    shown = hour if 1 <= hour <= 12 else (12 if hour == 0 else hour - 12)
    return f"{half} {shown}:{minute:02d}"


def until_text(start, now):
    minutes = int((start - now).total_seconds() // 60)
    if minutes < 1:
        return "马上开始"
    if minutes < 60:
        return f"还有 {minutes} 分钟"
    hours = minutes / 60
    if hours < 10:
        rounded = round(hours * 2) / 2
        shown = f"{rounded:g}"
        return f"还有 {shown} 小时"
    return "今天晚些时候"


def days_text(days, name):
    if days <= 0:
        return f"今天是{name}"
    if days == 1:
        return f"明天就是{name}"
    if days == 2:
        return f"后天就是{name}"
    return f"{name}还有 {days} 天"


# Choosing the scene -------------------------------------------------------------

def choose_scene(events, today, now, weather_slug=None):
    """Scene for the date panel at `now` on `today`.

    `events` are calendar events (dicts with date, title, label, kind, and
    starts_at for timed personal events) covering today and the coming weeks.
    """
    part = daypart(now)
    events = [event for event in events or [] if isinstance((event or {}).get("date"), date)]
    todays = [event for event in events if event["date"] == today]
    upcoming_today = sorted(
        (event for event in todays if _is_timed_personal(event) and event["starts_at"] >= now),
        key=lambda event: event["starts_at"],
    )
    next_plan = (f"{clock_text(upcoming_today[0]['starts_at'])} {plan_kind(upcoming_today[0])}"
                 if upcoming_today else None)

    for event in todays:
        festival = festival_of(event)
        if festival:
            key, _name, greeting, host = festival
            return Scene(host, "wave", key, _lines(greeting, next_plan), part, f"festival:{key}")
    if any(_BIRTHDAY.search(_title(event)) for event in todays):
        return Scene(OCCASION_HOSTS["birthday"], "wave", "birthday", _lines("生日快乐！", next_plan),
                     part, "birthday")

    if upcoming_today:
        return Scene(OCCASION_HOSTS["appointment"], "point", "appointment",
                     (next_plan, until_text(upcoming_today[0]["starts_at"], now)), part, "appointment")
    if any(_PAYDAY.search(_title(event)) for event in todays):
        return Scene(OCCASION_HOSTS["payday"], "wave", "payday", ("今天发薪日！",), part, "payday")
    launches = [event for event in todays if _is_launch(event)]
    if launches:
        timed = sorted((event for event in launches if isinstance(event.get("starts_at"), datetime)),
                       key=lambda event: event["starts_at"])
        second = f"{clock_text(timed[0]['starts_at'])} 开始" if timed else "记得看直播"
        return Scene(OCCASION_HOSTS["launch"], "point", "launch", ("今天有发布会", second), part, "launch")

    return _everyday_scene(events, today, now, part, weather_slug)


def _everyday_scene(events, today, now, part, weather_slug):
    weekend = today.weekday() >= 5
    if weather_slug in WEATHER_HOSTS:
        host = WEATHER_HOSTS[weather_slug]
    elif part == "night":
        host = "moon_magnet"
    elif weekend:
        host = "shield_skater"
    else:
        host = _date_pick(EVERYDAY_HOSTS, today, part)
    pose = {"night": "sleep", "dawn": "wave", "dusk": "wave"}.get(part, "sit")
    # First the nearest thing to look forward to, else a greeting for the hour;
    # then the weather advice, else where the week stands.
    first = _next_notable(events, today) or DAYPART_GREETINGS.get(part) or _week_line(today)
    second = WEATHER_TIPS.get(weather_slug)
    if not second:
        second = next((line for line in (_week_line(today), DAYPART_GREETINGS.get(part))
                       if line and line != first), None)
    occasion = "weather" if weather_slug in WEATHER_TIPS else "everyday"
    return Scene(host, pose, None, _lines(first, second), part, occasion)


def _lines(first, second=None):
    return (first, second) if second and second != first else (first,)


def _next_notable(events, today):
    """The nearest notable day within COUNTDOWN_DAYS, as one bubble line."""
    tomorrow = today + timedelta(days=1)
    tomorrow_plans = sorted(
        (event for event in events if event["date"] == tomorrow and _is_timed_personal(event)),
        key=lambda event: event["starts_at"],
    )
    candidates = []
    for event in events:
        days = (event["date"] - today).days
        if not 1 <= days <= COUNTDOWN_DAYS:
            continue
        festival = festival_of(event)
        if festival:
            candidates.append((days, 0, days_text(days, festival[1])))
        elif _BIRTHDAY.search(_title(event)):
            candidates.append((days, 1, days_text(days, "生日")))
        elif _PAYDAY.search(_title(event)):
            candidates.append((days, 2, days_text(days, "发薪日")))
        elif _is_launch(event):
            candidates.append((days, 3, days_text(days, "发布会")))
    if tomorrow_plans:
        event = tomorrow_plans[0]
        clock = clock_text(event["starts_at"]).replace(" ", "")
        candidates.append((1, -1, f"明天{clock}有{plan_kind(event)}"))
    if not candidates:
        return None
    return min(candidates)[2]


def _week_line(today):
    weekday = today.weekday()
    if weekday <= 3:
        return f"离周末还有 {5 - weekday} 天"
    if weekday == 4:
        return "周五啦，周末快到了"
    return "周末愉快"


def _date_pick(options, today, part):
    digest = hashlib.sha256(f"{today.isoformat()}|{part}".encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]
