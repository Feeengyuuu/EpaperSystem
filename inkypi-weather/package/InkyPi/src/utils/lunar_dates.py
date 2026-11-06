"""Gregorian dates of the main lunar festivals, 2026-2035 (shared by the wiki page's day colours and
the calendar's Chinese festivals).

The table gives 春节 (1/1), 端午 (5/5), 七夕 (7/7) and 中秋 (8/15), from Meeus new moons in China
time checked against published tables; 2033's 中秋 and 2034's 春节 are the "2033 problem" years
and follow the published calendar. Festivals a fixed number of days from these are derived:
除夕 (the day before 春节), 元宵 (1/15) and 中元 (7/15, eight days after 七夕).
"""

from datetime import date, timedelta

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


def lunar_festival(year, festival):
    """The Gregorian date of ``festival`` (SPRING, DRAGON_BOAT, QIXI, MID_AUTUMN) in ``year``, else None."""
    row = LUNAR.get(year)
    if not row:
        return None
    month, day = map(int, row[festival].split("-"))
    return date(year, month, day)


def lunar_days(year, festival, first=0, days=1):
    """The dates ``first`` days from ``festival`` on, ``days`` of them (an empty set outside the table)."""
    start = lunar_festival(year, festival)
    if start is None:
        return set()
    start += timedelta(days=first)
    return {start + timedelta(days=offset) for offset in range(days)}
