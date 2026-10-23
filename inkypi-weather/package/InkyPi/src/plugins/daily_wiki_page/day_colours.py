"""The page's two accent colours: a pair for each weekday, and festival pairs on their dates.

Only the accents change. Paper, ink, rules and grey text stay pure black and white for
the e-paper. ``lead`` colours the wordmark slash, the date slash and the history marker;
``second`` the header ornament's shapes and the rail block. Night lifts each toward white
so it still reads on black.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import NamedTuple

NIGHT_LIFT = 0.36


class Scheme(NamedTuple):
    key: str
    label: str
    lead: tuple
    second: tuple
    night_lead: tuple | None = None
    night_second: tuple | None = None

    def colours(self, night):
        if not night:
            return self.lead, self.second
        return self.night_lead or _lift(self.lead), self.night_second or _lift(self.second)


def _lift(colour):
    return tuple(round(channel + (255 - channel) * NIGHT_LIFT) for channel in colour)


# Monday first. Monday keeps the page's original Klein blue.
WEEKDAYS = (
    Scheme("monday", "克莱因蓝", (0, 82, 255), (0, 172, 210), (92, 158, 255), (64, 210, 240)),
    Scheme("tuesday", "朱砂", (226, 52, 30), (247, 168, 0)),
    Scheme("wednesday", "翡翠", (0, 150, 100), (132, 190, 0)),
    Scheme("thursday", "紫罗兰", (109, 40, 217), (245, 158, 11)),
    Scheme("friday", "橘焰", (255, 98, 0), (29, 78, 216)),
    Scheme("saturday", "品红", (219, 0, 120), (0, 166, 166)),
    Scheme("sunday", "金阳", (240, 168, 0), (220, 38, 38)),
)
DEFAULT = WEEKDAYS[0]

# Gregorian dates of 春节 (1/1), 端午 (5/5), 七夕 (7/7) and 中秋 (8/15), from Meeus new moons in
# China time checked against published tables; 2033's 中秋 and 2034's 春节 are the "2033
# problem" years and follow the published calendar.
LUNAR = {
    2026: ("02-17", "06-19", "08-19", "09-25"),
    2027: ("02-06", "06-09", "08-08", "09-15"),
    2028: ("01-26", "05-28", "08-26", "10-03"),
    2029: ("02-13", "06-16", "08-16", "09-22"),
    2030: ("02-03", "06-05", "08-05", "09-12"),
    2031: ("01-23", "06-24", "08-24", "10-01"),
    2032: ("02-11", "06-12", "08-12", "09-19"),
    2033: ("01-31", "06-01", "08-01", "09-08"),
    2034: ("02-19", "06-20", "08-20", "09-27"),
    2035: ("02-08", "06-10", "08-10", "09-16"),
}
SPRING, DRAGON_BOAT, QIXI, MID_AUTUMN = range(4)


def _lunar(year, festival, first=0, days=1):
    row = LUNAR.get(year)
    if not row:
        return set()
    month, day = map(int, row[festival].split("-"))
    start = date(year, month, day) + timedelta(days=first)
    return {start + timedelta(days=offset) for offset in range(days)}


def _fixed(month, day, days=1):
    return lambda year: {date(year, month, day) + timedelta(days=offset) for offset in range(days)}


def _nth_weekday(month, weekday, n):
    def dates(year):
        first = date(year, month, 1)
        return {first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))}
    return dates


def _qingming(year):
    # The 21st-century solar-term formula: 清明 falls on April 4 or 5.
    short = year % 100
    return {date(year, 4, math.floor(short * 0.2422 + 4.81) - short // 4)}


# The first festival that claims a date wins, so single days come before the longer spans.
FESTIVALS = (
    # 除夕 to 初五.
    (Scheme("spring_festival", "春节", (214, 0, 28), (232, 168, 0)), lambda y: _lunar(y, SPRING, -1, 6)),
    (Scheme("lantern", "元宵", (226, 35, 26), (255, 128, 0)), lambda y: _lunar(y, SPRING, 14)),
    (Scheme("qingming", "清明", (0, 133, 119), (122, 184, 0)), _qingming),
    (Scheme("dragon_boat", "端午", (0, 122, 61), (214, 48, 39)), lambda y: _lunar(y, DRAGON_BOAT)),
    (Scheme("qixi", "七夕", (124, 58, 237), (236, 72, 153)), lambda y: _lunar(y, QIXI)),
    (Scheme("mid_autumn", "中秋", (217, 154, 0), (30, 58, 138)), lambda y: _lunar(y, MID_AUTUMN)),
    (Scheme("new_year", "元旦", (196, 150, 20), (23, 37, 120)), _fixed(1, 1)),
    (Scheme("valentine", "情人节", (225, 29, 72), (251, 113, 133)), _fixed(2, 14)),
    (Scheme("earth_day", "地球日", (2, 106, 200), (22, 163, 74)), _fixed(4, 22)),
    (Scheme("labour_day", "劳动节", (220, 38, 38), (71, 85, 105)), _fixed(5, 1)),
    (Scheme("mothers_day", "母亲节", (219, 39, 119), (101, 163, 13)), _nth_weekday(5, 6, 2)),
    (Scheme("childrens_day", "儿童节", (250, 176, 0), (14, 165, 233)), _fixed(6, 1)),
    (Scheme("fathers_day", "父亲节", (30, 64, 175), (180, 83, 9)), _nth_weekday(6, 6, 3)),
    (Scheme("independence_day", "独立日", (200, 16, 46), (10, 49, 151)), _fixed(7, 4)),
    (Scheme("halloween", "万圣节", (249, 115, 22), (109, 40, 217)), _fixed(10, 31)),
    (Scheme("thanksgiving", "感恩节", (180, 70, 20), (202, 138, 4)), _nth_weekday(11, 3, 4)),
    (Scheme("christmas", "圣诞", (200, 16, 46), (0, 122, 61)), _fixed(12, 24, 2)),
    (Scheme("new_years_eve", "跨年夜", (202, 160, 40), (88, 28, 135)), _fixed(12, 31)),
    (Scheme("national_day", "国庆", (214, 0, 28), (245, 185, 0)), _fixed(10, 1, 7)),
)


def scheme_for(day=None):
    """The festival's scheme on its dates, else the weekday's; the default without a date."""
    if day is None:
        return DEFAULT
    day = day.date() if hasattr(day, "date") and callable(day.date) else day
    for scheme, dates in FESTIVALS:
        if day in dates(day.year):
            return scheme
    return WEEKDAYS[day.weekday()]
