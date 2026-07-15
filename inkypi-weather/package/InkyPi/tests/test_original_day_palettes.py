from plugins.bambu_monitor.bambu_monitor import (
    ACCENT_BLUE,
    ACCENT_GOLD,
    ACCENT_ORANGE,
    CINNABAR,
    INK,
    MALACHITE,
    PANEL,
    PAPER,
    _render_colors,
)
from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeTopMovies
from plugins.china_box_office_top_movies.china_box_office_top_movies import (
    ChinaBoxOfficeTopMovies,
)
from plugins.daily_ai_news.daily_ai_news import DailyAINews
from plugins.daily_wiki_page.daily_wiki_page import DailyWikiPage
from plugins.daily_word_poem.daily_word_poem import DailyWordPoem
from plugins.live_radar.live_radar import LiveRadar
from plugins.lol_info.lol_info import LoLInfo
from plugins.simple_calendar.simple_calendar import SimpleCalendar
from plugins.species_radar.species_radar import (
    COMIC_BLUE,
    COMIC_INK,
    COMIC_PANEL,
    COMIC_PAPER,
    SpeciesRadar,
)
from plugins.sports_dashboard.common import DAY_COLORS, SportsDashboardCommonMixin
from plugins.steam_profile_dashboard.steam_profile_dashboard import (
    SteamProfileDashboard,
)
from plugins.tech_pulse.tech_pulse import TechPulse
from plugins.telegram_digest.telegram_digest import TelegramDigest
from plugins.ticketmaster_events.ticketmaster_events import TicketmasterEvents
from plugins.weather.weather import Weather
from utils.theme_utils import get_theme_palette


def _theme(mode="day"):
    palette = {
        "background": (211, 212, 213),
        "panel": (221, 222, 223),
        "ink": (31, 32, 33),
        "muted": (91, 92, 93),
        "rule": (151, 152, 153),
        "accent": (41, 122, 173),
    }
    return {"mode": mode, "palette": palette, "css": {}}


def test_bambu_day_uses_pre_theme_color_constants():
    colors = _render_colors(_theme())
    assert colors["paper"] == PAPER
    assert colors["panel"] == PANEL
    assert colors["ink"] == INK
    assert colors["accent_blue"] == ACCENT_BLUE
    assert colors["accent_gold"] == ACCENT_GOLD
    assert colors["accent_orange"] == ACCENT_ORANGE
    assert colors["cinnabar"] == CINNABAR
    assert colors["malachite"] == MALACHITE


def test_box_office_day_uses_original_paper_palette():
    colors = BoxOfficeTopMovies.__new__(BoxOfficeTopMovies)._palette(
        {"_inkypi_theme": _theme()}
    )
    assert colors == {
        "mode": "paper",
        "paper": (239, 233, 215),
        "ink": (32, 35, 36),
        "muted": (91, 85, 74),
        "accent": (176, 41, 45),
        "localized": (115, 72, 58),
        "line": (208, 198, 178),
        "outline": (40, 40, 38),
        "shadow": (224, 216, 196),
    }


def test_china_box_office_day_uses_original_paper_palette():
    colors = ChinaBoxOfficeTopMovies.__new__(ChinaBoxOfficeTopMovies)._palette(
        {"_inkypi_theme": _theme()}
    )
    assert colors["paper"] == (240, 235, 222)
    assert colors["accent"] == (184, 39, 48)
    assert colors["localized"] == (124, 72, 55)


def test_daily_ai_news_day_uses_original_global_day_palette():
    assert DailyAINews._render_palette(_theme()) == get_theme_palette("day")


def test_daily_wiki_day_uses_original_paper_palette():
    colors = DailyWikiPage.__new__(DailyWikiPage)._palette(
        {"_inkypi_theme": _theme()}
    )
    assert colors["background"] == (232, 226, 214)
    assert colors["panel"] == (222, 215, 200)
    assert colors["accent"] == (102, 56, 24)


def test_daily_word_day_ignores_canonical_override():
    plugin = DailyWordPoem.__new__(DailyWordPoem)
    colors = plugin._page_palette({"_inkypi_theme": _theme()}, _theme())
    original = get_theme_palette("day")
    assert colors[0] == original["background"]
    assert colors[1] == (18, 18, 16)
    assert colors[2] == original["green"]


def test_live_radar_day_uses_original_black_and_white_palette():
    colors = LiveRadar.__new__(LiveRadar)._theme(
        {"_inkypi_theme": _theme()}, None
    )
    assert colors["bg"] == (255, 255, 255)
    assert colors["ink"] == (0, 0, 0)
    assert colors["line"] == (0, 0, 0)


def test_lol_day_preserves_original_dark_dashboard_palette():
    colors = LoLInfo.__new__(LoLInfo)._render_colors(_theme())
    assert colors["background"] == (5, 7, 12)
    assert colors["panel"] == (18, 22, 35)
    assert colors["gold"] == (255, 205, 54)
    assert colors["cyan"] == (107, 204, 255)


def test_simple_calendar_day_uses_original_user_colors():
    assert SimpleCalendar._theme_palette_for_render(_theme()) is None
    assert SimpleCalendar._theme_palette_for_render(_theme("night")) is not None


def test_species_day_uses_original_comic_palette():
    colors = SpeciesRadar.__new__(SpeciesRadar)._palette(
        {"_inkypi_theme": _theme()}
    )
    assert colors["paper"] == COMIC_PAPER
    assert colors["panel"] == COMIC_PANEL
    assert colors["ink"] == COMIC_INK
    assert colors["accent"] == COMIC_BLUE


def test_sports_day_uses_original_dashboard_palette():
    assert SportsDashboardCommonMixin._sports_dashboard_colors(_theme()) == DAY_COLORS


def test_steam_profile_day_uses_original_global_day_palette():
    assert SteamProfileDashboard._render_palette(_theme()) == get_theme_palette("day")


def test_tech_pulse_day_uses_original_paper_palette():
    colors = TechPulse.__new__(TechPulse)._palette({"_inkypi_theme": _theme()})
    assert colors["background"] == (245, 240, 226)
    assert colors["panel"] == (255, 251, 241)
    assert colors["orange"] == (255, 102, 0)


def test_telegram_day_uses_original_digest_palette():
    colors = TelegramDigest.__new__(TelegramDigest)._palette(_theme())
    assert colors["background"] == (246, 242, 232)
    assert colors["panel"] == (255, 252, 242)
    assert colors["cyan"] == (0, 135, 170)
    assert colors["amber"] == (188, 116, 32)


def test_ticketmaster_day_uses_original_color_palette():
    plugin = TicketmasterEvents.__new__(TicketmasterEvents)
    expected = plugin._palette({"themeMode": "color"})
    actual = plugin._palette({"themeMode": "color", "_inkypi_theme": _theme()})
    assert actual == expected


def test_weather_day_keeps_original_plugin_background_settings():
    settings = {"backgroundOption": "image", "backgroundColor": "#123456"}
    assert Weather._settings_for_theme(settings, _theme()) == settings
    assert Weather._settings_for_theme(settings, _theme("night")) != settings
