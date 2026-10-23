"""The Daily Wiki page's accent pairs: one per weekday, festival pairs on their dates."""

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_wiki_page import day_colours  # noqa: E402
from plugins.daily_wiki_page.daily_wiki_page import DailyWikiPage  # noqa: E402


def luma(colour):
    return 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]


def test_each_weekday_has_its_own_pair_and_monday_keeps_klein_blue():
    week = [day_colours.scheme_for(date(2026, 10, 19 + offset)) for offset in range(7)]

    assert [scheme.key for scheme in week] == ["monday", "tuesday", "wednesday", "thursday", "friday",
                                               "saturday", "sunday"]
    assert len({scheme.lead for scheme in week}) == 7
    assert day_colours.scheme_for(None) is day_colours.WEEKDAYS[0]
    assert week[0].colours(night=False)[0] == (0, 82, 255) and week[0].colours(night=True)[0] == (92, 158, 255)


@pytest.mark.parametrize("day, key", [
    (date(2026, 2, 16), "spring_festival"),  # 除夕
    (date(2026, 2, 21), "spring_festival"),  # 初五 is the last day of the span
    (date(2026, 2, 22), "sunday"),
    (date(2026, 3, 3), "lantern"),
    (date(2026, 4, 5), "qingming"),
    (date(2028, 4, 4), "qingming"),
    (date(2026, 5, 10), "mothers_day"),
    (date(2026, 6, 19), "dragon_boat"),
    (date(2026, 8, 19), "qixi"),
    (date(2026, 9, 25), "mid_autumn"),
    (date(2031, 10, 1), "mid_autumn"),  # a single festival day wins over the National Day week
    (date(2026, 10, 7), "national_day"),
    (date(2026, 10, 31), "halloween"),
    (date(2026, 11, 26), "thanksgiving"),
    (date(2026, 12, 24), "christmas"),
    (date(2026, 12, 31), "new_years_eve"),
    (date(2034, 2, 19), "spring_festival"),  # the 2033-problem year follows the published calendar
    (datetime(2027, 1, 1, 9, 30), "new_year"),
])
def test_festivals_take_their_dates(day, key):
    assert day_colours.scheme_for(day).key == key


def test_every_accent_reads_on_its_paper():
    schemes = list(day_colours.WEEKDAYS) + [scheme for scheme, _dates in day_colours.FESTIVALS]
    for scheme in schemes:
        for colour in scheme.colours(night=False):
            assert luma(colour) < 200, scheme.key
        for colour in scheme.colours(night=True):
            assert luma(colour) > 80, scheme.key


def test_the_palette_keeps_pure_paper_and_ink_and_names_the_scheme():
    plugin = DailyWikiPage.__new__(DailyWikiPage)
    friday = plugin._palette({"themeMode": "day", "_inkypi_theme": {"mode": "day"}}, date(2026, 10, 23))
    christmas = plugin._palette({"_inkypi_theme": {"mode": "night"}}, date(2026, 12, 25))

    assert (friday["background"], friday["ink"], friday["scheme"]) == ((255, 255, 255), (0, 0, 0), "friday")
    assert (friday["accent"], friday["accent2"]) == day_colours.WEEKDAYS[4].colours(night=False)
    assert (christmas["background"], christmas["ink"], christmas["scheme"]) == ((0, 0, 0), (255, 255, 255),
                                                                               "christmas")
