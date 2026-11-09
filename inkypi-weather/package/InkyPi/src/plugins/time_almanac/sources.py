"""时光书签's pieces, each read through the plugin that owns it.

Everything but the moon's rise and set is worked out at render time from bundled data, so a cached
redraw at display time shows the minute it is shown:

- the literature clocks: a classical Chinese novel's sentence for this minute (else its 时辰 or
  watch) from chinese_literature_clock's table, and an English book's line for this minute from
  literature_clock's table;
- the moon from moon_phase's Meeus calculation, with the day's moon and sun rise and set from
  USNO through moon_phase's own reader (the one network feed, cached per day);
- the day, week, month and year as flow_progress counts them;
- days until the next holidays, Chinese (utils.lunar_dates) and American, and an optional date of
  the owner's own.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plugins.chinese_literature_clock import quote_picker as zh_clock
from plugins.flow_progress.flow_progress import (
    calc_day_progress,
    calc_month_progress,
    calc_week_progress,
    calc_year_progress,
)
from plugins.literature_clock import quote_picker as en_clock
from plugins.moon_phase.moon_phase import calculate_moon_info
from plugins.moon_phase.sky_times import fetch_sky_times, find_home, utc_offset_hours, valid_sky_times
from utils.lunar_dates import DRAGON_BOAT, MID_AUTUMN, QIXI, SPRING, lunar_festival

PACIFIC = ZoneInfo("America/Los_Angeles")
PLUGINS = Path(__file__).resolve().parents[1]
EN_CLOCK_CSV = PLUGINS / "literature_clock" / "data" / "litclock_annotated.csv"
ZH_CLOCK_CSV = PLUGINS / "chinese_literature_clock" / "data" / "chinese_litclock.csv"
# An English line longer than this rarely fits beside the Chinese one; the shorter ones go first.
EN_QUOTE_MAX = 230
WEEKDAYS = "一二三四五六日"
PHASE_NAMES_ZH = {
    "New Moon": "新月", "Waxing Crescent": "娥眉月", "First Quarter": "上弦月", "Waxing Gibbous": "盈凸月",
    "Full Moon": "满月", "Waning Gibbous": "亏凸月", "Last Quarter": "下弦月", "Waning Crescent": "残月",
}
PRINCIPAL_ZH = {0.0: "新月", 0.25: "上弦", 0.5: "满月", 0.75: "下弦"}
# Holidays by month and day; Thanksgiving and the lunar festivals are worked out per year.
FIXED_DAYS = ((1, 1, "元旦"), (2, 14, "情人节"), (7, 4, "独立日"), (10, 31, "万圣节"), (12, 25, "圣诞节"))
LUNAR_DAYS = ((SPRING, "春节"), (DRAGON_BOAT, "端午"), (QIXI, "七夕"), (MID_AUTUMN, "中秋"))
COUNTDOWN_ROWS = 4
CUSTOM_TITLE_MAX = 12


def device_zone(name):
    """The device's configured time zone, Pacific when it names none the system knows."""
    try:
        return ZoneInfo(str(name)) if name else PACIFIC
    except (ZoneInfoNotFoundError, ValueError):
        return PACIFIC


def local_now(now=None, zone=PACIFIC):
    value = now or datetime.now(zone)
    return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)


# --- the literature clocks ---------------------------------------------------------------------

def _clock_line(picker, path, now, *, chinese):
    """{quote, time, book, author} for ``now`` from one clock table, or None when it has no line
    near this minute (nor, for the Chinese one, for this 时辰). The same minute picks the same line."""
    try:
        rows, _key = picker.resolve_with_fallback(Path(path), now.strftime("%H:%M"), allow_nsfw=False)
    except (OSError, ValueError, KeyError):
        return None
    if not rows:
        return None
    seed = now.strftime("%Y-%m-%d %H:%M")
    if chinese:
        row = picker.pick_quote(rows, "source_daily", seed)
    else:
        short = [row for row in rows if len(row.get("full_quote") or "") <= EN_QUOTE_MAX]
        row = random.Random(seed).choice(short or rows)
    # Spaces collapsed here, as the page wraps them, so the time's place in the line stays true.
    quote = " ".join(picker.sanitize(row.get("full_quote") or "").split())
    if not quote:
        return None
    return {"quote": quote, "time": " ".join(picker.sanitize(row.get("time_human") or "").split()),
            "book": picker.sanitize(row.get("book_title") or ""), "author": picker.sanitize(row.get("author_name") or "")}


def clock_lines(now):
    return {"zh": _clock_line(zh_clock, ZH_CLOCK_CSV, now, chinese=True),
            "en": _clock_line(en_clock, EN_CLOCK_CSV, now, chinese=False)}


def highlight_span(quote, phrase):
    """(start, end) of ``phrase`` in ``quote`` (case-insensitive), or None."""
    if not quote or not phrase:
        return None
    index = quote.lower().find(phrase.lower())
    return (index, index + len(phrase)) if index >= 0 else None


# --- the moon ------------------------------------------------------------------------------------

def moon(now):
    """The moon at ``now``: its phase (0 new, 0.5 full), lit share, age, Chinese name and the next
    four principal phases as local datetimes."""
    info = calculate_moon_info(now)
    zone = now.tzinfo
    return {
        "fraction": info.phase_fraction,
        "illumination": info.illumination,
        "age": info.age_days,
        "name": PHASE_NAMES_ZH.get(info.phase_name, info.phase_name),
        "waxing": info.direction == "Waxing",
        "next": [(moment.astimezone(zone), PRINCIPAL_ZH.get(fraction, "")) for moment, fraction in info.upcoming],
    }


def sky_identity(home, day, offset):
    return {"home": [round(home[0], 3), round(home[1], 3)], "day": day.isoformat(), "offset": offset}


def sky_fetcher(home, day, offset):
    return lambda context: fetch_sky_times(home, day, offset, context)


def home_and_offset(settings, device_config, now):
    """(home, UTC offset in hours that day) or (None, None) when no page names a place."""
    home = find_home(settings or {}, device_config)
    if not home:
        return None, None
    noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    return home, utc_offset_hours(noon)


valid_sky = valid_sky_times


# --- the day, week, month and year ------------------------------------------------------------------

def progress(now):
    """[(label, share done, note)] for the day, week, month and year, as flow_progress counts them."""
    days_in_year = (date(now.year + 1, 1, 1) - date(now.year, 1, 1)).days
    days_in_month = (date(now.year + (now.month == 12), now.month % 12 + 1, 1) - date(now.year, now.month, 1)).days
    left = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), now.tzinfo) - now
    hours, minutes = divmod(int(left.total_seconds()) // 60, 60)
    return [
        ("今日", calc_day_progress(now), f"还剩 {hours} 小时 {minutes:02d} 分"),
        ("本周", calc_week_progress(now), f"周{WEEKDAYS[now.weekday()]}"),
        ("本月", calc_month_progress(now), f"第 {now.day} / {days_in_month} 天"),
        ("今年", calc_year_progress(now), f"第 {now.timetuple().tm_yday} / {days_in_year} 天"),
    ]


# --- days until --------------------------------------------------------------------------------------

def _thanksgiving(year):
    first = date(year, 11, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 21)


def holidays(year):
    days = [(date(year, month, day), name) for month, day, name in FIXED_DAYS]
    days.append((_thanksgiving(year), "感恩节"))
    for festival, name in LUNAR_DAYS:
        day = lunar_festival(year, festival)
        if day:
            days.append((day, name))
    return days


def custom_day(settings):
    """(date, title) of the owner's own countdown, or None when it is unset or malformed."""
    settings = settings or {}
    raw = str(settings.get("countdownDate") or "").strip()
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return None
    title = " ".join(str(settings.get("countdownTitle") or "").split())[:CUSTOM_TITLE_MAX] or "我的日子"
    return day, title


def countdowns(today, settings=None, rows=COUNTDOWN_ROWS):
    """[(date, name, days from today, own)]: the owner's own date first (even once passed, as days
    since), then the next holidays from today on."""
    picked = []
    own = custom_day(settings)
    if own:
        picked.append((own[0], own[1], (own[0] - today).days, True))
    upcoming = sorted(day for year in (today.year, today.year + 1) for day in holidays(year) if day[0] >= today)
    for day, name in upcoming:
        if len(picked) >= rows:
            break
        picked.append((day, name, (day - today).days, False))
    return picked
