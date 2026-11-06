"""China's official days off and make-up workdays (调休 / 补班) for the calendar, from the keyless
timor.tech holiday API, with the traditional festivals and observances a holiday list leaves out
(除夕, 元宵, 七夕, 中元 from the shared lunar table; 妇女节, 教师节 and the like by date).

Each day becomes one calendar event: {"date", "title", "day_off"}, ``day_off`` "off" for a day off,
"work" for a make-up workday and None for a festival that is not a day off. A run of days off shares
one title ("国庆节 休7天"), so the agenda lists the run once.
"""

from __future__ import annotations

from datetime import date, timedelta

from utils.http_client import get_http_client
from utils.lunar_dates import QIXI, SPRING, lunar_festival

CN_HOLIDAY_URL = "https://timor.tech/api/holiday/year/{year}"
CN_HOLIDAY_MAX_BYTES = 64 * 1024
CN_HOLIDAY_TIMEOUT_SECONDS = 15
DAY_OFF = "off"
WORKDAY = "work"
# The 春节 run's days are named for the day ("除夕", "初一" ...): the run is 春节.
SPRING_DAY_NAMES = frozenset({"春节", "除夕", "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八"})
# Observances that are not days off, by fixed date.
OBSERVANCES = ((3, 8, "妇女节"), (3, 12, "植树节"), (5, 4, "青年节"), (6, 1, "儿童节"), (7, 1, "建党节"),
               (8, 1, "建军节"), (9, 10, "教师节"))


def fetch_year(year, context=None) -> dict:
    """The holiday API's payload for ``year``."""
    response = get_http_client().request_json(
        "GET", CN_HOLIDAY_URL.format(year=int(year)), context=context, timeout=CN_HOLIDAY_TIMEOUT_SECONDS,
        max_bytes=CN_HOLIDAY_MAX_BYTES, headers={"User-Agent": "InkyPi SimpleCalendar/1.0", "Accept": "application/json"})
    return response.data


def _festival(name):
    name = " ".join(str(name or "").split())
    return "春节" if name in SPRING_DAY_NAMES else name


def official_days(payload, year) -> list[dict]:
    """The year's official days off and make-up workdays, in date order."""
    entries = []
    for key, raw in ((payload or {}).get("holiday") or {}).items():
        if not isinstance(raw, dict) or not isinstance(raw.get("holiday"), bool):
            continue
        try:
            day = date.fromisoformat(str(raw.get("date") or f"{int(year)}-{key}"))
        except ValueError:
            continue
        if day.year == int(year):
            entries.append((day, raw))
    entries.sort(key=lambda entry: entry[0])

    events, run = [], []

    def close_run():
        if run:
            title = f"{_festival(run[0][1].get('name'))} 休{len(run)}天"
            events.extend({"date": day, "title": title, "day_off": DAY_OFF} for day, _raw in run)
            run.clear()

    for day, raw in entries:
        if raw["holiday"]:
            if run and (day - run[-1][0] != timedelta(days=1)
                        or _festival(raw.get("name")) != _festival(run[0][1].get("name"))):
                close_run()
            run.append((day, raw))
            continue
        close_run()
        target = _festival(raw.get("target") or str(raw.get("name") or "").replace("补班", "").rstrip("前后"))
        events.append({"date": day, "title": f"补班（{target}）" if target else "补班", "day_off": WORKDAY})
    close_run()
    return sorted(events, key=lambda event: event["date"])


def traditional_days(year) -> list[dict]:
    """除夕, 元宵, 七夕 and 中元 (from the lunar table, 2026-2035) and the fixed-date observances."""
    events = [{"date": date(int(year), month, day), "title": title, "day_off": None}
              for month, day, title in OBSERVANCES]
    spring, qixi = lunar_festival(int(year), SPRING), lunar_festival(int(year), QIXI)
    if spring is not None:
        events += [{"date": spring - timedelta(days=1), "title": "除夕", "day_off": None},
                   {"date": spring + timedelta(days=14), "title": "元宵节", "day_off": None}]
    if qixi is not None:
        events += [{"date": qixi, "title": "七夕", "day_off": None},
                   {"date": qixi + timedelta(days=8), "title": "中元节", "day_off": None}]
    # 除夕 can fall in the year before its 春节.
    next_spring = lunar_festival(int(year) + 1, SPRING)
    if next_spring is not None and (next_spring - timedelta(days=1)).year == int(year):
        events.append({"date": next_spring - timedelta(days=1), "title": "除夕", "day_off": None})
    return sorted((event for event in events if event["date"].year == int(year)), key=lambda event: event["date"])


def month_days(payload, year, month_start, month_end) -> list[dict]:
    """The official and traditional days of ``year`` in [month_start, month_end)."""
    days = sorted(official_days(payload, year) + traditional_days(year), key=lambda event: event["date"])
    return [event for event in days if month_start <= event["date"] < month_end]
