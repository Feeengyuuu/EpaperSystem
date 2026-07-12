import calendar
import hashlib
import logging
import os
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import icalendar
import pytz
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import read_contexts
from utils.app_utils import get_base_ui_font, get_font
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

LEGACY_CALENDAR_DIR = Path("/usr/local/inkypi/src/static/calendar")
DEFAULT_DATA_DIR = Path("/var/lib/inkypi/data")
DURABLE_CALENDAR_SUBDIR = Path("plugins/simple_calendar/calendars")

DEFAULT_HOLIDAY_CALENDARS = [
    {
        "label": "US",
        "url": "https://calendar.google.com/calendar/ical/en.usa%23holiday%40group.v.calendar.google.com/public/basic.ics",
        "color": "#345995",
    },
    {
        "label": "CN",
        "url": "https://calendar.google.com/calendar/ical/china__zh_cn%40holiday.calendar.google.com/public/basic.ics",
        "color": "#c62828",
    },
]

HOLIDAY_LABEL_COUNTRY_COLORS = {
    "CN": (222, 41, 16),
    "US": (0, 74, 173),
}

WEATHER_PANEL_BACKGROUND_DEFAULT = "cloudy"
WEATHER_PANEL_BACKGROUND_BLEND = 0.72
WEATHER_PANEL_BACKGROUND_STYLE_DEFAULT = "img2_original_heroes_mixed"
WEATHER_PANEL_CONTEXT_MAX_AGE_SECONDS = 3 * 60 * 60
WEATHER_PANEL_BACKGROUND_STYLES = {
    "classic": (),
    "img2_original_heroes_weather": ("img2_original_heroes_weather",),
    "img2_original_heroes_nyc_weather": ("img2_original_heroes_nyc_weather",),
    "img2_original_heroes_local_top_weather": ("img2_original_heroes_local_top_weather",),
    "img2_original_heroes_mixed": (
        "img2_original_heroes",
        "img2_original_heroes_weather",
        "img2_original_heroes_nyc_weather",
        "img2_original_heroes_local_top_weather",
    ),
}
OPEN_METEO_CURRENT_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={long}"
    "&current=weather_code,is_day"
    "&timezone=auto&models=best_match&forecast_days=1"
)
WEATHER_BACKGROUND_BY_ICON = {
    "01d": "clear_day",
    "01n": "clear_night",
    "022d": "clear_day",
    "022n": "clear_night",
    "02d": "cloudy",
    "02n": "cloudy",
    "03d": "cloudy",
    "03n": "cloudy",
    "04d": "cloudy",
    "04n": "cloudy",
    "09d": "rain",
    "09n": "rain",
    "10d": "rain",
    "10n": "rain",
    "11d": "thunderstorm",
    "11n": "thunderstorm",
    "13d": "snow",
    "13n": "snow",
    "48d": "fog",
    "48n": "fog",
    "50d": "fog",
    "50n": "fog",
    "51d": "rain",
    "51n": "rain",
    "53d": "rain",
    "53n": "rain",
    "56d": "rain",
    "56n": "rain",
    "57d": "rain",
    "57n": "rain",
    "71d": "snow",
    "71n": "snow",
    "73d": "snow",
    "73n": "snow",
    "77d": "snow",
    "77n": "snow",
}
WEATHER_BACKGROUND_SLUGS = {
    "clear_day",
    "clear_night",
    "cloudy",
    "fog",
    "rain",
    "snow",
    "thunderstorm",
}
ICAL_WEEKDAY_INDEX = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}
RECURRENCE_ITERATION_LIMIT = 20000
DATE_HERO_CUTOUT_DIR = "date_hero_cutouts"
DATE_HERO_PLACEMENTS = (
    (-0.20, -0.12, 0.38),
    (0.20, -0.12, 0.38),
    (-0.18, 0.10, 0.36),
    (0.18, 0.10, 0.36),
    (-0.03, -0.18, 0.35),
    (-0.21, 0.00, 0.36),
    (0.21, 0.00, 0.36),
)

LOCALE_DATA = {
    "de": {
        "weekday_abbrev": ["MO", "DI", "MI", "DO", "FR", "SA", "SO"],
        "headers": ["S", "M", "D", "M", "D", "F", "S"],
        "months": ["JANUAR", "FEBRUAR", "MÄRZ", "APRIL", "MAI", "JUNI", "JULI", "AUGUST", "SEPTEMBER", "OKTOBER", "NOVEMBER", "DEZEMBER"],
    },
    "en": {
        "weekday_abbrev": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
        "headers": ["S", "M", "T", "W", "T", "F", "S"],
        "months": ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"],
    },
    "es": {
        "weekday_abbrev": ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"],
        "headers": ["D", "L", "M", "M", "J", "V", "S"],
        "months": ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"],
    },
    "fr": {
        "weekday_abbrev": ["LUN", "MAR", "MER", "JEU", "VEN", "SAM", "DIM"],
        "headers": ["D", "L", "M", "M", "J", "V", "S"],
        "months": ["JANVIER", "FÉVRIER", "MARS", "AVRIL", "MAI", "JUIN", "JUILLET", "AOÛT", "SEPTEMBRE", "OCTOBRE", "NOVEMBRE", "DÉCEMBRE"],
    },
    "id": {
        "weekday_abbrev": ["SEN", "SEL", "RAB", "KAM", "JUM", "SAB", "MIN"],
        "headers": ["M", "S", "S", "R", "K", "J", "S"],
        "months": ["JANUARI", "FEBRUARI", "MARET", "APRIL", "MEI", "JUNI", "JULI", "AGUSTUS", "SEPTEMBER", "OKTOBER", "NOVEMBER", "DESEMBER"],
    },
    "it": {
        "weekday_abbrev": ["LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"],
        "headers": ["D", "L", "M", "M", "G", "V", "S"],
        "months": ["GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO", "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"],
    },
    "nl": {
        "weekday_abbrev": ["MAA", "DIN", "WOE", "DON", "VRI", "ZAT", "ZON"],
        "headers": ["Z", "M", "D", "W", "D", "V", "Z"],
        "months": ["JANUARI", "FEBRUARI", "MAART", "APRIL", "MEI", "JUNI", "JULI", "AUGUSTUS", "SEPTEMBER", "OKTOBER", "NOVEMBER", "DECEMBER"],
    },
    "pt": {
        "weekday_abbrev": ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"],
        "headers": ["D", "S", "T", "Q", "Q", "S", "S"],
        "months": ["JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"],
    },
}

# ---------------------------------------------------------------------------
# Dot-matrix glyph definitions (5 wide × 7 tall for digits, variable for letters)
# Each glyph is a list of (row, col) positions where a dot should be drawn.
# ---------------------------------------------------------------------------

_DIGIT_W, _DIGIT_H = 5, 7

_DIGIT_PATTERNS = {
    "0": [
        "01110",
        "10001",
        "10011",
        "10101",
        "11001",
        "10001",
        "01110",
    ],
    "1": [
        "00100",
        "01100",
        "00100",
        "00100",
        "00100",
        "00100",
        "01110",
    ],
    "2": [
        "01110",
        "10001",
        "00001",
        "00110",
        "01000",
        "10000",
        "11111",
    ],
    "3": [
        "01110",
        "10001",
        "00001",
        "00110",
        "00001",
        "10001",
        "01110",
    ],
    "4": [
        "00010",
        "00110",
        "01010",
        "10010",
        "11111",
        "00010",
        "00010",
    ],
    "5": [
        "11111",
        "10000",
        "11110",
        "00001",
        "00001",
        "10001",
        "01110",
    ],
    "6": [
        "01110",
        "10001",
        "10000",
        "11110",
        "10001",
        "10001",
        "01110",
    ],
    "7": [
        "11111",
        "00001",
        "00010",
        "00100",
        "01000",
        "01000",
        "01000",
    ],
    "8": [
        "01110",
        "10001",
        "10001",
        "01110",
        "10001",
        "10001",
        "01110",
    ],
    "9": [
        "01110",
        "10001",
        "10001",
        "01111",
        "00001",
        "10001",
        "01110",
    ],
}

# 5×7 dot-matrix letter patterns for weekday abbreviations
_LETTER_W, _LETTER_H = 5, 7

_LETTER_PATTERNS = {
    "S": [
        "01110",
        "10001",
        "10000",
        "01110",
        "00001",
        "10001",
        "01110",
    ],
    "A": [
        "01110",
        "10001",
        "10001",
        "11111",
        "10001",
        "10001",
        "10001",
    ],
    "T": [
        "11111",
        "00100",
        "00100",
        "00100",
        "00100",
        "00100",
        "00100",
    ],
    "M": [
        "10001",
        "11011",
        "10101",
        "10101",
        "10001",
        "10001",
        "10001",
    ],
    "O": [
        "01110",
        "10001",
        "10001",
        "10001",
        "10001",
        "10001",
        "01110",
    ],
    "N": [
        "10001",
        "11001",
        "10101",
        "10011",
        "10001",
        "10001",
        "10001",
    ],
    "U": [
        "10001",
        "10001",
        "10001",
        "10001",
        "10001",
        "10001",
        "01110",
    ],
    "E": [
        "11111",
        "10000",
        "10000",
        "11110",
        "10000",
        "10000",
        "11111",
    ],
    "W": [
        "10001",
        "10001",
        "10001",
        "10101",
        "10101",
        "11011",
        "10001",
    ],
    "D": [
        "11100",
        "10010",
        "10001",
        "10001",
        "10001",
        "10010",
        "11100",
    ],
    "H": [
        "10001",
        "10001",
        "10001",
        "11111",
        "10001",
        "10001",
        "10001",
    ],
    "F": [
        "11111",
        "10000",
        "10000",
        "11110",
        "10000",
        "10000",
        "10000",
    ],
    "R": [
        "11110",
        "10001",
        "10001",
        "11110",
        "10010",
        "10001",
        "10001",
    ],
    "I": [
        "01110",
        "00100",
        "00100",
        "00100",
        "00100",
        "00100",
        "01110",
    ],
    "P": [
        "11110",
        "10001",
        "10001",
        "11110",
        "10000",
        "10000",
        "10000",
    ],
    "Q": [
        "01110",
        "10001",
        "10001",
        "10001",
        "10001",
        "10011",
        "01111",
    ],
    "B": [
        "11110",
        "10001",
        "10001",
        "11110",
        "10001",
        "10001",
        "11110",
    ],
    "G": [
        "01110",
        "10001",
        "10000",
        "10111",
        "10001",
        "10001",
        "01110",
    ],
    "J": [
        "00111",
        "00010",
        "00010",
        "00010",
        "00010",
        "10010",
        "01100",
    ],
    "K": [
        "10001",
        "10010",
        "10100",
        "11000",
        "10100",
        "10010",
        "10001",
    ],
    "L": [
        "10000",
        "10000",
        "10000",
        "10000",
        "10000",
        "10000",
        "11111",
    ],
    "V": [
        "10001",
        "10001",
        "10001",
        "10001",
        "10001",
        "01010",
        "00100",
    ],
    "X": [
        "10001",
        "10001",
        "01010",
        "00100",
        "01010",
        "10001",
        "10001",
    ],
    "Z": [
        "11111",
        "00001",
        "00010",
        "00100",
        "01000",
        "10000",
        "11111",
    ],
}

def _get_dot_positions(char, reserve_accent_row=False):
    """Return list of (row, col) positions for a character glyph."""
    accent_positions = []
    base_char = char.upper()

    if base_char == "Á":
        base_char = "A"
        accent_positions = [(0, 3)]
    elif base_char == "É":
        base_char = "E"
        accent_positions = [(0, 3)]

    patterns = _DIGIT_PATTERNS if base_char.isdigit() else _LETTER_PATTERNS
    rows = patterns.get(base_char.upper(), [])
    positions = []
    for r, row_str in enumerate(rows):
        for c, ch in enumerate(row_str):
            if ch == "1":
                positions.append((r + (1 if reserve_accent_row else 0), c))

    positions.extend(accent_positions)

    return positions


def _draw_dotmatrix_text(draw, text, center_x, center_y, dot_radius, dot_spacing,
                         fill, glyph_w=5, glyph_h=7, char_gap_dots=1.5):
    """Draw a string of dot-matrix characters centred at (center_x, center_y)."""

    cell = dot_radius * 2 + dot_spacing
    char_width_px = glyph_w * cell
    gap_px = char_gap_dots * cell
    total_width = len(text) * char_width_px + (len(text) - 1) * gap_px
    accent_row = any(ch.upper() in {"Á", "É"} for ch in text)
    total_height = (glyph_h + (1 if accent_row else 0)) * cell

    start_x = center_x - total_width / 2
    start_y = center_y - total_height / 2

    for i, ch in enumerate(text):
        ox = start_x + i * (char_width_px + gap_px)
        oy = start_y
        for r, c in _get_dot_positions(ch, reserve_accent_row=accent_row):
            cx = ox + c * cell + dot_radius
            cy = oy + r * cell + dot_radius
            draw.ellipse(
                [cx - dot_radius, cy - dot_radius,
                 cx + dot_radius, cy + dot_radius],
                fill=fill,
            )


class SimpleCalendar(BasePlugin):

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)

        timezone_name = device_config.get_config("timezone", default="America/New_York")
        tz = pytz.timezone(timezone_name)
        selected_date = self._get_selected_date(settings, tz)
        reference_dt = None if settings.get("customDate") else datetime.now(tz)
        language = self._get_locale_key(settings.get("language") or settings.get("locale", "en"))
        locale_data = LOCALE_DATA.get(language)

        primary_color = self._parse_color(settings.get("primaryColor"), (230, 26, 26))
        highlight_color = self._parse_color(settings.get("highlightColor"), (163, 13, 13))
        layout_position = settings.get("layoutPosition", "left").lower()
        theme_render_only = self._setting_enabled(settings.get("_theme_render_only"))
        holiday_events = self._get_calendar_events(
            settings,
            selected_date,
            tz,
            allow_remote=not theme_render_only,
        )
        weather_panel_background_path = self._get_weather_panel_background_path(settings, device_config, selected_date)
        date_hero_overlay_enabled = self._date_hero_overlay_enabled(settings)

        return self._render_calendar(
            dimensions,
            selected_date,
            primary_color,
            highlight_color,
            locale_data,
            language,
            layout_position,
            holiday_events,
            weather_panel_background_path,
            date_hero_overlay_enabled,
            reference_dt,
        )

    # ------------------------------------------------------------------
    # Core rendering
    # ------------------------------------------------------------------

    def _render_calendar(
        self,
        dimensions,
        selected_date,
        primary_color,
        highlight_color,
        locale_data,
        language,
        layout_position="left",
        holiday_events=None,
        weather_panel_background_path=None,
        date_hero_overlay_enabled=False,
        reference_dt=None,
    ):
        W, H = dimensions
        holiday_events = holiday_events or []
        current_month_events = self._events_for_selected_month(holiday_events, selected_date)
        holiday_events_by_day = self._group_holiday_events_by_day(current_month_events)
        upcoming_event_rows = self._upcoming_event_rows(
            holiday_events,
            selected_date,
            reference_dt=reference_dt,
            limit=3,
        )

        # Colours
        accent = primary_color
        surface = (255, 255, 255)
        panel_bg = (248, 248, 247)
        divider = (220, 220, 220)
        white = (255, 255, 255)
        text_color = (0, 0, 0)
        muted_text = (132, 132, 132)

        img = Image.new("RGB", (W, H), surface)
        draw = ImageDraw.Draw(img)

        # Edge-to-edge surface for the e-paper canvas.
        card_left = 0
        card_top = 0
        card_right = W
        card_bottom = H
        card_w = card_right - card_left
        card_h = card_bottom - card_top
        # --- Panel and calendar bounds based on layout_position ---
        aspect = card_w / max(card_h, 1)

        panel_ratio = 0.38 if aspect >= 1.0 else 0.30
        panel_px = int(card_w * panel_ratio)
        if layout_position == "left":
            p_left, p_top, p_right, p_bottom = card_left, card_top, card_left + panel_px, card_bottom
            c_left, c_top, c_right, c_bottom = p_right, card_top, card_right, card_bottom
        else:  # right
            p_left, p_top, p_right, p_bottom = card_right - panel_px, card_top, card_right, card_bottom
            c_left, c_top, c_right, c_bottom = card_left, card_top, p_left, card_bottom

        cal_w = c_right - c_left
        cal_h = c_bottom - c_top
        cal_cx = c_left + cal_w // 2

        # --- Draw a quiet focus panel instead of the old dot-matrix block. ---
        self._draw_focus_panel_background(
            img,
            p_left,
            p_top,
            p_right,
            p_bottom,
            panel_bg,
            weather_panel_background_path,
        )
        if layout_position == "left":
            draw.rectangle([p_left, p_top, p_left + max(int(panel_px * 0.018), 4), p_bottom], fill=accent)
            draw.line(
                [(p_right, p_top + int(card_h * 0.08)), (p_right, p_bottom - int(card_h * 0.08))],
                fill=divider,
                width=max(int(W * 0.0025), 1),
            )
        else:  # right
            draw.rectangle([p_right - max(int(panel_px * 0.018), 4), p_top, p_right, p_bottom], fill=accent)
            draw.line(
                [(p_left, p_top + int(card_h * 0.08)), (p_left, p_bottom - int(card_h * 0.08))],
                fill=divider,
                width=max(int(W * 0.0025), 1),
            )

        # === DATE FOCUS CONTENT (inside the quiet panel) ===
        panel_w = p_right - p_left
        panel_h = p_bottom - p_top
        panel_cx = p_left + panel_w // 2
        panel_cy = p_top + panel_h // 2

        day_str = str(selected_date.day)
        weekday_str = self._get_weekday_abbrev(selected_date, locale_data, language)

        # Solid typography keeps the panel clear on e-paper.
        month_name = self._get_month_name(selected_date, locale_data, language)
        focus_max_w = panel_w * 0.78
        clean_day_font_size = max(int(min(panel_w * 0.52, panel_h * 0.34)), 58)
        clean_day_font = self._get_calendar_ui_font(clean_day_font_size, bold=True)
        while clean_day_font_size > 48:
            bbox = draw.textbbox((0, 0), day_str, font=clean_day_font)
            if bbox[2] - bbox[0] <= focus_max_w:
                break
            clean_day_font_size -= 2
            clean_day_font = self._get_calendar_ui_font(
                clean_day_font_size, bold=True
            )

        clean_weekday_font = self._get_calendar_ui_font(
            max(int(panel_w * 0.105), 18), bold=True
        )
        clean_date_font = self._get_calendar_ui_font(
            max(int(panel_w * 0.072), 13)
        )

        weekday_y = panel_cy - int(panel_h * 0.22)
        day_y = panel_cy + int(panel_h * 0.01)
        rule_y = panel_cy + int(panel_h * 0.19)
        date_y = panel_cy + int(panel_h * 0.28)
        rule_w = int(panel_w * 0.30)

        self._draw_focus_holiday(
            draw,
            self._events_for_focus_day(holiday_events_by_day.get(selected_date.day, []), selected_date, reference_dt),
            panel_cx,
            p_top + int(panel_h * 0.12),
            int(panel_w * 0.84),
            text_color,
            muted_text,
        )
        draw.text((panel_cx, weekday_y), weekday_str, fill=muted_text, font=clean_weekday_font, anchor="mm")
        draw.text((panel_cx, day_y), day_str, fill=text_color, font=clean_day_font, anchor="mm")
        if date_hero_overlay_enabled:
            self._draw_focus_date_hero(
                img,
                selected_date,
                p_left,
                p_top,
                p_right,
                p_bottom,
                panel_cx,
                day_y,
                panel_w,
                panel_h,
            )
        draw.line(
            [(panel_cx - rule_w // 2, rule_y), (panel_cx + rule_w // 2, rule_y)],
            fill=accent,
            width=max(int(W * 0.004), 2),
        )
        draw.text((panel_cx, date_y), f"{month_name} {selected_date.year}", fill=muted_text, font=clean_date_font, anchor="mm")

        # === CALENDAR CONTENT (inside light area) ===
        grid_side_pad = int(cal_w * 0.04)
        grid_left = c_left + grid_side_pad
        grid_right_edge = c_right - grid_side_pad
        grid_w = grid_right_edge - grid_left
        col_w = grid_w / 7

        month_font_size = max(int(col_w * 0.76), 12)
        year_font_size = max(int(col_w * 0.52), 9)
        header_font_size = max(int(col_w * 0.40), 9)
        day_font_size = max(int(col_w * 0.56), 10)

        month_font = self._get_calendar_ui_font(month_font_size, bold=True)
        year_font = self._get_calendar_ui_font(year_font_size)
        header_font = self._get_calendar_ui_font(header_font_size)
        day_font = self._get_calendar_ui_font(day_font_size)

        top_pad = int(cal_h * 0.045)
        month_y = c_top + top_pad

        # Month and year
        month_name = self._get_month_name(selected_date, locale_data, language)
        year_text = str(selected_date.year)
        month_bbox = draw.textbbox((0, 0), month_name, font=month_font)
        year_bbox = draw.textbbox((0, 0), year_text, font=year_font)
        month_width = month_bbox[2] - month_bbox[0]
        header_gap = max(int(col_w * 0.4), 8)
        total_width = month_width + header_gap + (year_bbox[2] - year_bbox[0])
        header_left = cal_cx - total_width / 2

        baseline_y = month_y + month_font.getmetrics()[0]
        title_lift = max(int(month_font_size * 0.30), 8)

        draw.text(
            (header_left, baseline_y - title_lift), month_name,
            fill=text_color, font=month_font, anchor="ls",
        )
        year_y = baseline_y - int(month_font_size * 0.15) - title_lift
        draw.text(
            (header_left + month_width + header_gap, year_y),
            year_text,
            fill=muted_text, font=year_font, anchor="ls",
        )

        # Weekday header row
        header_labels = self._get_weekday_headers(locale_data, language)
        header_y = month_y + int(month_font_size * 1.4)

        for i, label in enumerate(header_labels):
            x = grid_left + col_w * i + col_w / 2
            draw.text(
                (x, header_y), label,
                fill=muted_text, font=header_font, anchor="mt",
            )

        # Month day grid
        grid_top_y = header_y + int(header_font_size * 1.4)
        draw.line(
            [(grid_left, grid_top_y - int(header_font_size * 0.40)), (grid_right_edge, grid_top_y - int(header_font_size * 0.40))],
            fill=(232, 232, 232),
            width=max(int(W * 0.0018), 1),
        )
        event_list_h = int(cal_h * 0.19) if upcoming_event_rows else 0
        available_grid_h = c_bottom - grid_top_y - int(cal_h * 0.015) - event_list_h

        cal_grid = calendar.Calendar(firstweekday=6).monthdayscalendar(selected_date.year, selected_date.month)
        num_weeks = len(cal_grid)
        row_h = available_grid_h / num_weeks

        today_circle_r = int(min(col_w, row_h) * 0.46)

        for week_idx, week in enumerate(cal_grid):
            row_cy = grid_top_y + row_h * week_idx + row_h / 2
            for dow, day in enumerate(week):
                if day == 0:
                    continue
                col_cx = grid_left + col_w * dow + col_w / 2

                if day == selected_date.day:
                    draw.ellipse(
                        [col_cx - today_circle_r, row_cy - today_circle_r,
                         col_cx + today_circle_r, row_cy + today_circle_r],
                        fill=highlight_color,
                    )
                    draw.text(
                        (col_cx, row_cy), str(day),
                        fill=white, font=day_font, anchor="mm",
                    )
                else:
                    draw.text(
                        (col_cx, row_cy), str(day),
                        fill=text_color, font=day_font, anchor="mm",
                    )
                if day in holiday_events_by_day:
                    self._draw_holiday_markers(
                        draw,
                        holiday_events_by_day[day],
                        col_cx,
                        row_cy + today_circle_r * 0.62 + 8,
                        min(col_w, row_h),
                        selected=day == selected_date.day,
                    )

        self._draw_holiday_list(
            draw,
            holiday_events,
            selected_date,
            upcoming_event_rows,
            c_left,
            c_bottom - event_list_h,
            c_right,
            c_bottom,
            text_color,
            muted_text,
            divider,
        )
        return img

    def _draw_focus_panel_background(self, img, left, top, right, bottom, panel_bg, weather_panel_background_path):
        panel_w = right - left
        panel_h = bottom - top
        if panel_w <= 0 or panel_h <= 0:
            return

        panel = Image.new("RGB", (panel_w, panel_h), panel_bg)
        if weather_panel_background_path:
            try:
                background = Image.open(weather_panel_background_path).convert("RGB")
                background = ImageOps.fit(background, (panel_w, panel_h), method=Image.LANCZOS)
                if self._is_color_weather_panel_background(weather_panel_background_path):
                    panel = background
                else:
                    background = ImageOps.autocontrast(ImageOps.grayscale(background), cutoff=1).convert("RGB")
                    panel = Image.blend(panel, background, WEATHER_PANEL_BACKGROUND_BLEND)
            except Exception as exc:
                logger.debug("Could not load Simple Calendar weather panel background %s: %s", weather_panel_background_path, exc)

        img.paste(panel, (left, top))

    def _is_color_weather_panel_background(self, weather_panel_background_path):
        try:
            return "weather_panel_backgrounds_color" in Path(weather_panel_background_path).parts
        except TypeError:
            return False

    def _draw_focus_date_hero(self, img, selected_date, left, top, right, bottom, panel_cx, day_y, panel_w, panel_h):
        hero_path = self._date_hero_cutout_path(selected_date)
        if not hero_path:
            return

        try:
            hero = Image.open(hero_path).convert("RGBA")
            placement = DATE_HERO_PLACEMENTS[selected_date.toordinal() % len(DATE_HERO_PLACEMENTS)]
            x_factor, y_factor, scale = placement
            target_side = max(56, int(panel_w * scale))
            target_side = min(target_side, int(panel_h * 0.34), int(panel_w * 0.50))
            hero.thumbnail((target_side, target_side), Image.LANCZOS)
            if hero.width <= 0 or hero.height <= 0:
                return

            margin = max(int(panel_w * 0.025), 4)
            center_x = panel_cx + panel_w * x_factor
            center_y = day_y + panel_h * y_factor
            max_x = max(left + margin, right - margin - hero.width)
            max_y = max(top + margin, bottom - margin - hero.height)
            x = int(max(left + margin, min(center_x - hero.width / 2, max_x)))
            y = int(max(top + margin, min(center_y - hero.height / 2, max_y)))
            img.paste(hero, (x, y), hero)
        except Exception as exc:
            logger.debug("Could not draw Simple Calendar date hero %s: %s", hero_path, exc)

    def _date_hero_overlay_enabled(self, settings):
        settings = settings or {}
        if "dateHeroOverlays" in settings:
            return self._setting_enabled(settings.get("dateHeroOverlays"))
        if "dateHeroOverlay" in settings:
            return self._setting_enabled(settings.get("dateHeroOverlay"))
        return self._weather_panel_background_style(settings) != "classic"

    def _date_hero_cutout_path(self, selected_date):
        paths = self._date_hero_cutout_paths()
        if not paths:
            return None
        try:
            date_number = selected_date.toordinal()
        except AttributeError:
            date_number = date.today().toordinal()
        return paths[date_number % len(paths)]

    def _date_hero_cutout_paths(self):
        directory = Path(__file__).resolve().parent / DATE_HERO_CUTOUT_DIR
        if not directory.is_dir():
            return []
        return sorted(path for path in directory.glob("*.png") if path.is_file())

    def _get_weather_panel_background_path(self, settings, device_config, selected_date=None):
        if not self._weather_panel_background_enabled(settings):
            return None

        theme_render_only = self._setting_enabled(settings.get("_theme_render_only"))
        source = "context"
        slug = self._read_weather_context_background_slug(
            include_stale=theme_render_only
        )
        if not slug and not theme_render_only:
            source = "open-meteo"
            weather_settings = self._find_weather_source_settings(settings, device_config)
            slug = self._fetch_current_weather_background_slug(weather_settings) if weather_settings else None

        if not slug:
            source = "fallback"
            slug = settings.get("weatherPanelFallback") or WEATHER_PANEL_BACKGROUND_DEFAULT

        background_path = self._weather_background_path(slug, settings, selected_date)
        if background_path:
            logger.info("Simple Calendar weather panel background: %s (%s)", slug, source)
        return background_path

    def _weather_panel_background_enabled(self, settings):
        if "weatherPanelBackground" not in settings:
            return True
        return self._setting_enabled(settings.get("weatherPanelBackground"))

    def _read_weather_context_background_slug(self, *, include_stale=False):
        try:
            entries = read_contexts(
                ["weather"],
                max_age_seconds=WEATHER_PANEL_CONTEXT_MAX_AGE_SECONDS,
                include_stale=include_stale,
            )
        except Exception as exc:
            logger.debug("Could not read weather context for Simple Calendar: %s", exc)
            return None

        for entry in entries:
            payload = entry.get("payload") if isinstance(entry, dict) else {}
            if not isinstance(payload, dict):
                continue
            slug = self._normalize_weather_background_slug(
                payload.get("background_slug")
            )
            if slug:
                return slug
            slug = self._weather_icon_to_background_slug(payload.get("icon_code"))
            if slug:
                return slug
            for key in ("weather_background_slug", "weather_background_path", "weather_background_file"):
                slug = self._normalize_weather_background_slug(payload.get(key))
                if slug:
                    return slug
            slug = self._weather_icon_to_background_slug(payload.get("current_day_icon"))
            if slug:
                return slug
        return None

    def _find_weather_source_settings(self, settings, device_config):
        latitude = settings.get("weatherLatitude") or settings.get("latitude")
        longitude = settings.get("weatherLongitude") or settings.get("longitude")
        if latitude not in (None, "") and longitude not in (None, ""):
            return {"latitude": latitude, "longitude": longitude}

        config = self._device_config_dict(device_config)
        playlist_config = config.get("playlist_config") if isinstance(config, dict) else {}
        playlists = playlist_config.get("playlists") if isinstance(playlist_config, dict) else []
        for playlist in playlists or []:
            for plugin in playlist.get("plugins", []) or []:
                plugin_id = plugin.get("plugin_id") or plugin.get("id")
                if plugin_id not in {"mini_weather", "weather"}:
                    continue
                plugin_settings = plugin.get("plugin_settings") or plugin.get("settings") or {}
                if plugin_settings.get("latitude") not in (None, "") and plugin_settings.get("longitude") not in (None, ""):
                    return plugin_settings
        return None

    def _fetch_current_weather_background_slug(self, weather_settings):
        latitude = self._coerce_float((weather_settings or {}).get("latitude"))
        longitude = self._coerce_float((weather_settings or {}).get("longitude"))
        if latitude is None or longitude is None:
            return None

        url = OPEN_METEO_CURRENT_URL.format(lat=latitude, long=longitude)
        try:
            response = get_http_session().get(
                url,
                timeout=12,
                headers={"User-Agent": "InkyPi SimpleCalendar/1.0"},
            )
            response.raise_for_status()
            current = response.json().get("current") or {}
        except Exception as exc:
            logger.warning("Could not fetch current weather for Simple Calendar panel: %s", exc)
            return None

        slug = self._weather_code_to_background_slug(
            current.get("weather_code"),
            current.get("is_day", 1),
        )
        logger.debug(
            "Simple Calendar Open-Meteo weather_code=%s is_day=%s background=%s",
            current.get("weather_code"),
            current.get("is_day", 1),
            slug,
        )
        return slug

    def _weather_code_to_background_slug(self, weather_code, is_day=1):
        try:
            code = int(weather_code)
        except (TypeError, ValueError):
            return WEATHER_PANEL_BACKGROUND_DEFAULT

        day = self._setting_enabled(is_day)
        if code in {0, 1}:
            return "clear_day" if day else "clear_night"
        if code in {2, 3}:
            return "cloudy"
        if code in {45, 48}:
            return "fog"
        if code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
            return "rain"
        if code in {71, 73, 75, 77, 85, 86}:
            return "snow"
        if code in {95, 96, 99}:
            return "thunderstorm"
        return WEATHER_PANEL_BACKGROUND_DEFAULT

    def _weather_icon_to_background_slug(self, current_icon_path):
        icon_name = Path(str(current_icon_path or "")).stem.lower()
        slug = WEATHER_BACKGROUND_BY_ICON.get(icon_name)

        if not slug and icon_name:
            code = icon_name[:2]
            suffix = icon_name[-1] if icon_name[-1:] in ("d", "n") else "d"
            if code == "01":
                slug = "clear_night" if suffix == "n" else "clear_day"
            elif code in ("02", "03", "04"):
                slug = "cloudy"
            elif code in ("09", "10", "51", "53", "56", "57"):
                slug = "rain"
            elif code == "11":
                slug = "thunderstorm"
            elif code in ("13", "71", "73", "77"):
                slug = "snow"
            elif code in ("48", "50"):
                slug = "fog"

        return self._normalize_weather_background_slug(slug)

    def _weather_background_path(self, slug, settings=None, selected_date=None):
        normalized = self._normalize_weather_background_slug(slug)
        if not normalized:
            return None
        style = self._weather_panel_background_style(settings)
        candidates = self._weather_background_candidates(normalized, style)
        if not candidates:
            logger.debug("Simple Calendar weather panel background missing: %s (%s)", normalized, style)
            return None
        path = candidates[self._stable_weather_background_index(normalized, style, selected_date, len(candidates))]
        return str(path)

    def _weather_panel_background_style(self, settings):
        style = str((settings or {}).get("weatherPanelBackgroundStyle") or WEATHER_PANEL_BACKGROUND_STYLE_DEFAULT).strip()
        if style in WEATHER_PANEL_BACKGROUND_STYLES:
            return style
        return WEATHER_PANEL_BACKGROUND_STYLE_DEFAULT

    def _weather_background_candidates(self, slug, style):
        plugin_dir = Path(__file__).resolve().parent
        if style == "classic":
            return self._weather_background_candidates_from_dir(plugin_dir / "weather_panel_backgrounds", slug)

        candidates = []
        color_dir = plugin_dir / "weather_panel_backgrounds_color"
        for style_name in WEATHER_PANEL_BACKGROUND_STYLES.get(style, ()):
            candidates.extend(self._weather_background_candidates_from_dir(color_dir / style_name, slug))
        if candidates:
            return sorted(dict.fromkeys(candidates))
        return self._weather_background_candidates_from_dir(plugin_dir / "weather_panel_backgrounds", slug)

    def _weather_background_candidates_from_dir(self, directory, slug):
        candidates = []
        for pattern in (f"{slug}.png", f"{slug}_*.png", f"*_{slug}.png"):
            candidates.extend(path for path in directory.glob(pattern) if path.is_file())
        variant_dir = directory / slug
        if variant_dir.is_dir():
            candidates.extend(path for path in variant_dir.glob("*.png") if path.is_file())
        return sorted(dict.fromkeys(candidates))

    def _stable_weather_background_index(self, slug, style, selected_date, count):
        if count <= 1:
            return 0
        if hasattr(selected_date, "isoformat"):
            date_number = selected_date.toordinal()
        else:
            date_number = date.today().toordinal()
        digest = hashlib.sha256(f"{slug}|{style}".encode("utf-8")).hexdigest()
        offset = int(digest[:12], 16)
        return (date_number + offset) % count

    def _normalize_weather_background_slug(self, value):
        text = str(value or "").strip().lower()
        if not text:
            return None
        stem = Path(text).stem
        if stem in WEATHER_BACKGROUND_SLUGS:
            return stem
        for slug in WEATHER_BACKGROUND_SLUGS:
            if slug in text:
                return slug
        return None

    def _device_config_dict(self, device_config):
        if isinstance(device_config, dict):
            return device_config
        if hasattr(device_config, "get_config"):
            try:
                value = device_config.get_config()
            except TypeError:
                value = None
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _coerce_float(value):
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _get_calendar_events(
        self,
        settings,
        selected_date,
        tz,
        *,
        allow_remote=True,
    ):
        events = []
        events.extend(
            self._get_holiday_events(
                settings,
                selected_date,
                tz,
                allow_remote=allow_remote,
            )
        )
        events.extend(
            self._get_personal_calendar_events(
                settings,
                selected_date,
                tz,
                allow_remote=allow_remote,
            )
        )
        return self._dedupe_holiday_events(events)

    def _get_holiday_events(
        self,
        settings,
        selected_date,
        tz,
        *,
        allow_remote=True,
    ):
        if not self._holidays_enabled(settings):
            return []

        sources = self._get_holiday_sources(settings)
        if not sources:
            return []

        events = []
        for source in sources:
            if not allow_remote and self._calendar_source_requires_network(source):
                continue
            try:
                events.extend(self._fetch_holiday_events(source, selected_date, tz))
            except Exception:
                logger.exception("Failed to fetch holiday calendar: %s", source.get("label") or source.get("url"))

        return self._dedupe_holiday_events(events)

    def _holidays_enabled(self, settings):
        if str(settings.get("holidayPreset") or "").strip().lower() == "off":
            return False
        if "showHolidays" in settings:
            return self._setting_enabled(settings.get("showHolidays"))
        return bool(settings.get("holidayCalendarURLs[]") or settings.get("holidayPreset"))

    def _get_holiday_sources(self, settings):
        preset = str(settings.get("holidayPreset") or "us_cn").lower()
        urls = self._as_list(settings.get("holidayCalendarURLs[]"))
        labels = self._as_list(settings.get("holidayCalendarLabels[]"))
        colors = self._as_list(settings.get("holidayCalendarColors[]"))

        if preset == "us_cn" and not urls:
            return [
                {
                    "url": source["url"],
                    "label": source["label"],
                    "color": self._parse_color(source["color"], (80, 80, 80)),
                    "kind": "holiday",
                }
                for source in DEFAULT_HOLIDAY_CALENDARS
            ]

        sources = []
        for index, url in enumerate(urls):
            url = str(url or "").strip()
            if not url:
                continue
            fallback = DEFAULT_HOLIDAY_CALENDARS[index % len(DEFAULT_HOLIDAY_CALENDARS)]
            label = str(labels[index] if index < len(labels) and labels[index] else fallback["label"]).strip()
            color = colors[index] if index < len(colors) else fallback["color"]
            sources.append({
                "url": url,
                "label": label[:6] or fallback["label"],
                "color": self._parse_color(color, self._parse_color(fallback["color"], (80, 80, 80))),
                "kind": "holiday",
            })
        return sources

    def _get_personal_calendar_events(
        self,
        settings,
        selected_date,
        tz,
        *,
        allow_remote=True,
    ):
        if not self._personal_calendars_enabled(settings):
            return []

        events = []
        for source in self._get_personal_calendar_sources(settings):
            if not allow_remote and self._calendar_source_requires_network(source):
                continue
            try:
                events.extend(self._fetch_holiday_events(source, selected_date, tz))
            except Exception:
                logger.exception("Failed to fetch personal calendar: %s", source.get("label") or source.get("url"))
        return events

    def _personal_calendars_enabled(self, settings):
        urls = self._as_list(settings.get("personalCalendarURLs[]"))
        if not any(str(url or "").strip() for url in urls):
            return False
        if "showPersonalCalendars" in settings:
            return self._setting_enabled(settings.get("showPersonalCalendars"))
        return True

    def _get_personal_calendar_sources(self, settings):
        urls = self._as_list(settings.get("personalCalendarURLs[]"))
        labels = self._as_list(settings.get("personalCalendarLabels[]"))
        colors = self._as_list(settings.get("personalCalendarColors[]"))
        sources = []
        for index, url in enumerate(urls):
            url = str(url or "").strip()
            if not url:
                continue
            label = str(labels[index] if index < len(labels) and labels[index] else "CAL").strip()
            color = colors[index] if index < len(colors) else "#2e7d32"
            sources.append({
                "url": url,
                "label": label[:8] or "CAL",
                "color": self._parse_color(color, (46, 125, 50)),
                "kind": "personal",
            })
        return sources

    @staticmethod
    def _calendar_source_requires_network(source):
        url = str((source or {}).get("url") or "").strip()
        parsed = urlparse(url)
        is_local_path = parsed.scheme == "file" or (
            not parsed.scheme and url.startswith("/")
        )
        return not is_local_path

    def _fetch_holiday_events(self, source, selected_date, tz):
        content = self._read_calendar_source(source["url"])
        cal = icalendar.Calendar.from_ical(content)
        return self._extract_holiday_events(cal, source, selected_date, tz)

    def _read_calendar_source(self, url):
        url = str(url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme == "file":
            path_text = unquote(parsed.path)
            if parsed.netloc:
                path_text = f"//{parsed.netloc}{path_text}"
            if os.name == "nt" and path_text.startswith("/") and len(path_text) > 2 and path_text[2] == ":":
                path_text = path_text[1:]
            return self._read_local_calendar_path(Path(path_text))
        if not parsed.scheme and url.startswith("/"):
            return self._read_local_calendar_path(Path(url))
        response = get_http_session().get(
            url,
            timeout=20,
            headers={"User-Agent": "InkyPi SimpleCalendar/1.0"},
        )
        response.raise_for_status()
        return response.content

    def _read_local_calendar_path(self, path):
        try:
            relative = path.relative_to(LEGACY_CALENDAR_DIR)
        except ValueError:
            return path.read_bytes()

        if len(relative.parts) != 1 or relative.name in {"", ".", ".."}:
            return path.read_bytes()

        data_dir = Path(os.environ.get("INKYPI_DATA_DIR") or DEFAULT_DATA_DIR)
        durable_root = data_dir / DURABLE_CALENDAR_SUBDIR
        durable_path = durable_root / relative
        if not durable_path.is_relative_to(durable_root):
            return path.read_bytes()
        if durable_path.is_file():
            return durable_path.read_bytes()
        return path.read_bytes()

    def _extract_holiday_events(self, cal, source, selected_date, tz):
        month_start = date(selected_date.year, selected_date.month, 1)
        if selected_date.month == 12:
            month_end = date(selected_date.year + 1, 1, 1)
        else:
            month_end = date(selected_date.year, selected_date.month + 1, 1)

        events = []
        recurrence_overrides = self._recurrence_override_keys(cal, tz)
        for component in cal.walk("VEVENT"):
            if self._component_is_cancelled(component):
                continue
            title = self._clean_event_title(str(component.get("summary") or "Holiday"))
            uid = self._component_uid(component)
            recurrence_id = self._component_value(component, "recurrence-id", tz)
            occurrence_starts, duration = self._component_occurrence_starts(
                component,
                selected_date,
                tz,
                excluded_keys=recurrence_overrides.get(uid, set()),
                force_single=bool(recurrence_id),
            )
            for occurrence_start in occurrence_starts:
                events.extend(
                    self._events_for_occurrence(
                        occurrence_start,
                        duration,
                        month_start,
                        month_end,
                        title,
                        source,
                        tz,
                    )
                )
        return events

    def _recurrence_override_keys(self, cal, tz):
        overrides = {}
        for component in cal.walk("VEVENT"):
            recurrence_id = self._component_value(component, "recurrence-id", tz)
            if not recurrence_id:
                continue
            uid = self._component_uid(component)
            if not uid:
                continue
            overrides.setdefault(uid, set()).add(self._date_value_key(recurrence_id, tz))
        return overrides

    def _component_occurrence_starts(self, component, selected_date, tz, excluded_keys=None, force_single=False):
        start_value, duration = self._component_start_and_duration(component, tz)
        if not start_value:
            return [], timedelta(days=1)

        candidates = []
        recur = component.get("rrule")
        if recur and not force_single:
            candidates.extend(self._rrule_occurrence_starts(start_value, duration, recur, selected_date, tz))
        else:
            candidates.append(start_value)
        candidates.extend(self._component_date_values(component, "rdate", tz))

        excluded = set(excluded_keys or set())
        excluded.update(self._component_date_value_keys(component, "exdate", tz))
        month_start, month_end = self._selected_month_bounds(selected_date)

        unique = {}
        for candidate in candidates:
            key = self._date_value_key(candidate, tz)
            if key in excluded:
                continue
            if not self._occurrence_overlaps_month(candidate, duration, month_start, month_end, tz):
                continue
            unique[key] = candidate

        return sorted(unique.values(), key=self._date_value_sort_key), duration

    def _rrule_occurrence_starts(self, start_value, duration, recur, selected_date, tz):
        start_date = self._date_from_value(start_value, tz)
        if not start_date:
            return []

        month_start, month_end = self._selected_month_bounds(selected_date)
        until_value = self._rrule_until(recur, tz)
        count = self._rrule_int(recur, "COUNT")
        occurrences = []
        generated = 0
        cursor = start_date
        iterations = 0

        while cursor < month_end and iterations < RECURRENCE_ITERATION_LIMIT:
            iterations += 1
            if self._rrule_date_matches(cursor, start_date, recur):
                occurrence = self._same_kind_value_on_date(start_value, cursor, tz)
                if self._date_value_sort_key(occurrence) >= self._date_value_sort_key(start_value):
                    if until_value and self._occurrence_after_until(occurrence, until_value, tz):
                        break
                    generated += 1
                    if not count or generated <= count:
                        if self._occurrence_overlaps_month(occurrence, duration, month_start, month_end, tz):
                            occurrences.append(occurrence)
                    if count and generated >= count:
                        break
            cursor += timedelta(days=1)

        return occurrences

    def _rrule_date_matches(self, current, start_date, recur):
        freq = str(self._rrule_first(recur, "FREQ", "")).upper()
        interval = max(self._rrule_int(recur, "INTERVAL") or 1, 1)
        bymonth = self._rrule_int_values(recur, "BYMONTH")
        bymonthday = self._rrule_int_values(recur, "BYMONTHDAY")
        byday = [str(value).upper() for value in self._rrule_values(recur, "BYDAY")]

        if bymonth and current.month not in bymonth:
            return False
        if bymonthday and not self._monthday_matches(current, bymonthday):
            return False
        if byday and not self._byday_matches(current, byday, freq):
            return False

        if freq == "DAILY":
            return (current - start_date).days % interval == 0
        if freq == "WEEKLY":
            week_index = (current - start_date).days // 7
            if week_index % interval != 0:
                return False
            if byday:
                return True
            return current.weekday() == start_date.weekday()
        if freq == "MONTHLY":
            month_index = (current.year - start_date.year) * 12 + current.month - start_date.month
            if month_index % interval != 0:
                return False
            if bymonthday or byday:
                return True
            return current.day == start_date.day
        if freq == "YEARLY":
            year_index = current.year - start_date.year
            if year_index % interval != 0:
                return False
            if not bymonth and current.month != start_date.month:
                return False
            if bymonthday or byday:
                return True
            return current.day == start_date.day

        return current == start_date

    def _monthday_matches(self, current, monthdays):
        last_day = calendar.monthrange(current.year, current.month)[1]
        for monthday in monthdays:
            expected = monthday if monthday > 0 else last_day + monthday + 1
            if current.day == expected:
                return True
        return False

    def _byday_matches(self, current, byday_values, freq):
        for raw_value in byday_values:
            weekday, ordinal = self._parse_byday(raw_value)
            if weekday is None or current.weekday() != weekday:
                continue
            if ordinal is None or freq not in {"MONTHLY", "YEARLY"}:
                return True
            if ordinal > 0 and ((current.day - 1) // 7) + 1 == ordinal:
                return True
            if ordinal < 0:
                last_day = calendar.monthrange(current.year, current.month)[1]
                if ((last_day - current.day) // 7) + 1 == abs(ordinal):
                    return True
        return False

    def _parse_byday(self, raw_value):
        value = str(raw_value).upper()
        weekday = ICAL_WEEKDAY_INDEX.get(value[-2:])
        if weekday is None:
            return None, None
        ordinal_text = value[:-2]
        if not ordinal_text:
            return weekday, None
        try:
            return weekday, int(ordinal_text)
        except ValueError:
            return weekday, None

    def _events_for_occurrence(self, occurrence_start, duration, month_start, month_end, title, source, tz):
        start_date = self._date_from_value(occurrence_start, tz)
        occurrence_end = occurrence_start + duration
        end_date = self._date_from_value(occurrence_end, tz)
        if not start_date or not end_date:
            return []
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)

        time_label = self._time_label_from_value(occurrence_start, tz) if source.get("kind") == "personal" else ""
        starts_at = self._datetime_from_value(occurrence_start, tz) if source.get("kind") == "personal" else None
        events = []
        current = max(start_date, month_start)
        last = min(end_date, month_end)
        while current < last:
            event = {
                "date": current,
                "title": title,
                "label": source.get("label") or "",
                "color": source.get("color") or (80, 80, 80),
                "kind": source.get("kind") or "holiday",
                "time": time_label,
            }
            if starts_at and current == starts_at.date():
                event["starts_at"] = starts_at
            events.append(event)
            current += timedelta(days=1)
        return events

    def _component_start_and_duration(self, component, tz):
        start = self._component_value(component, "dtstart", tz)
        if not start:
            return None, timedelta(days=1)
        end = self._component_value(component, "dtend", tz)
        if not end:
            end = start + timedelta(days=1)
        try:
            duration = end - start
        except TypeError:
            duration = timedelta(days=1)
        if duration <= timedelta(0):
            duration = timedelta(days=1)
        return start, duration

    def _component_value(self, component, key, tz):
        try:
            value = component.decoded(key)
        except Exception:
            return None
        return self._normalize_date_value(value, tz)

    def _component_date_values(self, component, key, tz):
        raw_values = component.get(key)
        if not raw_values:
            return []
        if not isinstance(raw_values, list):
            raw_values = [raw_values]

        values = []
        for raw_value in raw_values:
            if hasattr(raw_value, "dts"):
                candidates = [item.dt for item in raw_value.dts]
            elif hasattr(raw_value, "dt"):
                candidates = [raw_value.dt]
            else:
                candidates = [raw_value]
            for candidate in candidates:
                normalized = self._normalize_date_value(candidate, tz)
                if normalized:
                    values.append(normalized)
        return values

    def _component_date_value_keys(self, component, key, tz):
        return {self._date_value_key(value, tz) for value in self._component_date_values(component, key, tz)}

    def _normalize_date_value(self, value, tz):
        if isinstance(value, datetime):
            if value.tzinfo:
                return value.astimezone(tz)
            return tz.localize(value)
        if isinstance(value, date):
            return value
        return None

    def _date_from_value(self, value, tz):
        normalized = self._normalize_date_value(value, tz)
        if isinstance(normalized, datetime):
            return normalized.date()
        return normalized

    def _datetime_from_value(self, value, tz):
        normalized = self._normalize_date_value(value, tz)
        if isinstance(normalized, datetime):
            return normalized
        return None

    def _same_kind_value_on_date(self, template_value, occurrence_date, tz):
        if isinstance(template_value, datetime):
            template_value = self._normalize_date_value(template_value, tz)
            return tz.localize(datetime.combine(occurrence_date, template_value.timetz().replace(tzinfo=None)))
        return occurrence_date

    def _occurrence_overlaps_month(self, occurrence_start, duration, month_start, month_end, tz):
        start_date = self._date_from_value(occurrence_start, tz)
        if not start_date:
            return False
        end_date = self._date_from_value(occurrence_start + duration, tz)
        if not end_date or end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        return start_date < month_end and end_date > month_start

    def _occurrence_after_until(self, occurrence, until_value, tz):
        occurrence = self._normalize_date_value(occurrence, tz)
        until_value = self._normalize_date_value(until_value, tz)
        if isinstance(occurrence, datetime) and isinstance(until_value, datetime):
            return occurrence > until_value
        return self._date_from_value(occurrence, tz) > self._date_from_value(until_value, tz)

    def _date_value_key(self, value, tz):
        normalized = self._normalize_date_value(value, tz)
        if isinstance(normalized, datetime):
            return ("datetime", normalized.isoformat())
        if isinstance(normalized, date):
            return ("date", normalized.isoformat())
        return ("none", "")

    def _date_value_sort_key(self, value):
        if isinstance(value, datetime):
            return (value.date().isoformat(), value.timetz().isoformat())
        if isinstance(value, date):
            return (value.isoformat(), "")
        return ("", "")

    def _time_label_from_value(self, value, tz):
        value = self._datetime_from_value(value, tz)
        if not value:
            return ""
        hour = value.hour % 12 or 12
        minute = value.minute
        suffix = "a" if value.hour < 12 else "p"
        if minute:
            return f"{hour}:{minute:02d}{suffix}"
        return f"{hour}{suffix}"

    def _rrule_values(self, recur, key):
        if not recur:
            return []
        values = recur.get(key) or recur.get(key.lower())
        if values is None:
            return []
        if not isinstance(values, (list, tuple)):
            values = [values]
        return [value.dt if hasattr(value, "dt") else value for value in values]

    def _rrule_first(self, recur, key, default=None):
        values = self._rrule_values(recur, key)
        return values[0] if values else default

    def _rrule_int(self, recur, key):
        value = self._rrule_first(recur, key)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _rrule_int_values(self, recur, key):
        values = []
        for value in self._rrule_values(recur, key):
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                continue
        return values

    def _rrule_until(self, recur, tz):
        value = self._rrule_first(recur, "UNTIL")
        return self._normalize_date_value(value, tz)

    def _component_uid(self, component):
        return str(component.get("uid") or "")

    def _component_is_cancelled(self, component):
        return str(component.get("status") or "").strip().upper() == "CANCELLED"

    def _selected_month_bounds(self, selected_date):
        month_start = date(selected_date.year, selected_date.month, 1)
        if selected_date.month == 12:
            return month_start, date(selected_date.year + 1, 1, 1)
        return month_start, date(selected_date.year, selected_date.month + 1, 1)

    def _component_datetime(self, component, key, tz):
        try:
            value = component.decoded(key)
        except Exception:
            return None
        if not isinstance(value, datetime):
            return None
        if value.tzinfo:
            return value.astimezone(tz)
        return tz.localize(value)

    def _component_date(self, component, key, tz):
        try:
            value = component.decoded(key)
        except Exception:
            return None
        if isinstance(value, datetime):
            if value.tzinfo:
                value = value.astimezone(tz)
            return value.date()
        if isinstance(value, date):
            return value
        return None

    def _component_time_label(self, component, key, tz):
        try:
            value = component.decoded(key)
        except Exception:
            return ""
        if not isinstance(value, datetime):
            return ""
        if value.tzinfo:
            value = value.astimezone(tz)
        hour = value.hour % 12 or 12
        minute = value.minute
        suffix = "a" if value.hour < 12 else "p"
        if minute:
            return f"{hour}:{minute:02d}{suffix}"
        return f"{hour}{suffix}"

    def _dedupe_holiday_events(self, events):
        deduped = []
        seen = set()
        for event in sorted(events, key=lambda item: (item["date"], item["label"], item["title"])):
            key = (event["date"].isoformat(), event["label"], event["title"], event.get("time", ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        return deduped

    def _group_holiday_events_by_day(self, holiday_events):
        grouped = {}
        for event in holiday_events:
            grouped.setdefault(event["date"].day, []).append(event)
        return grouped

    def _events_for_selected_month(self, events, selected_date):
        return [
            event
            for event in events
            if event.get("date")
            and event["date"].year == selected_date.year
            and event["date"].month == selected_date.month
        ]

    def _event_is_upcoming(self, event, selected_date, reference_dt=None):
        event_date = event.get("date")
        if not event_date:
            return False
        if event_date.year != selected_date.year or event_date.month != selected_date.month:
            return False
        starts_at = event.get("starts_at")
        if starts_at and reference_dt:
            if starts_at < reference_dt:
                return False
            return starts_at.date() >= selected_date
        return event_date >= selected_date

    def _events_for_focus_day(self, events, selected_date, reference_dt=None):
        return [
            event
            for event in events
            if event.get("date") == selected_date and self._event_is_upcoming(event, selected_date, reference_dt)
        ]

    def _upcoming_event_rows(self, events, selected_date, reference_dt=None, limit=3):
        upcoming = [
            event
            for event in events
            if self._event_is_upcoming(event, selected_date, reference_dt)
        ]
        return self._merge_same_day_events(upcoming)[:limit]

    def _draw_focus_holiday(self, draw, events, x, y, max_width, text_color, muted_text):
        if not events:
            return

        label_font = self._get_calendar_ui_font(10, bold=True)
        text_font = self._get_holiday_title_font(14, bold=True)
        event = (self._merge_same_day_events(events) or events)[0]
        card_w = int(max_width * 0.98)
        content_left_pad = 31
        content_right_pad = 24
        label = self._fit_text(draw, event.get("label") or "", label_font, card_w * 0.30)
        title_lines = self._wrap_text_lines(
            draw,
            event.get("title") or "",
            text_font,
            card_w - content_left_pad - content_right_pad,
            max_lines=2,
        )
        card_h = 52 + max(len(title_lines), 1) * 16
        left = int(x - card_w / 2)
        top = int(y - card_h / 2)
        right = left + card_w
        bottom = top + card_h

        shadow = (25, 38, 45)
        paper = (238, 218, 158)
        paper_light = (255, 239, 183)
        border = (43, 37, 30)
        ink = (39, 31, 22)
        rail = (156, 92, 43)
        red = (166, 31, 36)
        red_dark = (101, 26, 31)
        gold = (239, 195, 95)

        draw.rounded_rectangle([left + 5, top + 5, right + 5, bottom + 5], radius=8, fill=shadow)
        draw.rounded_rectangle([left, top, right, bottom], radius=8, fill=paper, outline=border, width=2)
        draw.rounded_rectangle([left + 4, top + 4, right - 4, bottom - 4], radius=6, outline=paper_light, width=1)
        draw.rectangle([left + 8, top + 8, left + 14, bottom - 8], fill=red)
        draw.line([(left + 20, top + 30), (right - 22, top + 30)], fill=rail, width=1)
        draw.line([(left + 20, bottom - 11), (right - 22, bottom - 11)], fill=(111, 74, 39), width=1)
        draw.polygon([(right - 18, top), (right, top), (right, top + 18)], fill=(217, 177, 90), outline=border)
        for dot_y in (top + 18, bottom - 18):
            draw.ellipse([right - 17, dot_y - 2, right - 13, dot_y + 2], fill=gold, outline=border)

        if label:
            label_bbox = draw.textbbox((0, 0), label, font=label_font)
            label_w = min(max(label_bbox[2] - label_bbox[0] + 16, 42), int(card_w * 0.34))
            chip_left = left + 24
            chip_top = top + 9
            draw.rounded_rectangle(
                [chip_left, chip_top, chip_left + label_w, chip_top + 16],
                radius=4,
                fill=paper_light,
                outline=red_dark,
                width=1,
            )
            self._draw_source_label(
                draw,
                label,
                chip_left + label_w / 2,
                chip_top + 8,
                label_font,
                ink,
                separator_color=ink,
                anchor="mm",
            )

        title_cx = left + content_left_pad + (card_w - content_left_pad - content_right_pad) / 2
        first_title_y = top + (47 if label else 34)
        for line_index, title_line in enumerate(title_lines or [""]):
            draw.text((title_cx, first_title_y + line_index * 16), title_line, fill=ink, font=text_font, anchor="mm")

    def _draw_holiday_markers(self, draw, events, x, y, cell_size, selected=False):
        radius = max(int(cell_size * 0.055), 3)
        gap = radius * 3
        shown = events[:3]
        start_x = x - gap * (len(shown) - 1) / 2
        for index, event in enumerate(shown):
            color = (255, 255, 255) if selected else event.get("color", (80, 80, 80))
            cx = start_x + gap * index
            draw.ellipse([cx - radius, y - radius, cx + radius, y + radius], fill=color)

    def _draw_holiday_list(self, draw, events, selected_date, upcoming_event_rows, left, top, right, bottom, text_color, muted_text, divider):
        if not upcoming_event_rows or bottom <= top:
            return

        width = right - left
        line_y = top + 2
        draw.line([(left + int(width * 0.04), line_y), (right - int(width * 0.04), line_y)], fill=divider, width=1)

        grouped = upcoming_event_rows

        date_font_size = max(int(width * 0.032), 14)
        label_font_size = max(int(width * 0.025), 11)
        title_font_size = max(int(width * 0.032), 14)
        date_font = self._get_calendar_ui_font(date_font_size, bold=True)
        label_font = self._get_calendar_ui_font(label_font_size, bold=True)
        title_font = self._get_holiday_title_font(title_font_size)
        row_h = (bottom - top - 12) / 3
        x0 = left + int(width * 0.055)
        for index, event in enumerate(grouped):
            row_y = top + 12 + row_h * index + row_h / 2
            date_text = f"{event['date'].month}/{event['date'].day}"
            draw.text((x0, row_y), date_text, fill=text_color, font=date_font, anchor="lm")
            label_x = x0 + int(width * 0.145)
            self._draw_source_label(
                draw,
                event["label"],
                label_x,
                row_y,
                label_font,
                event.get("color", muted_text),
                separator_color=muted_text,
                anchor="lm",
            )
            title_x = label_x + int(width * 0.105)
            title = self._fit_text(draw, event["title"], title_font, right - title_x - int(width * 0.05))
            draw.text(
                (title_x, row_y),
                title,
                fill=text_color,
                font=title_font,
                anchor="lm",
            )

    def _draw_source_label(self, draw, label, x, y, font, default_color, separator_color=None, anchor="lm"):
        parts = self._source_label_parts(label)
        if not parts:
            return 0

        widths = [self._text_width(draw, part, font) for part in parts]
        total_width = sum(widths)
        cursor_x = x - total_width / 2 if anchor == "mm" else x
        separator_color = separator_color or default_color
        draw_anchor = "mm" if anchor == "mm" else "lm"
        for part, width in zip(parts, widths):
            fill = separator_color if part == "/" else self._source_label_color(part, default_color)
            draw_x = cursor_x + width / 2 if anchor == "mm" else cursor_x
            draw.text((draw_x, y), part, fill=fill, font=font, anchor=draw_anchor)
            if part != "/" and fill != default_color:
                draw.text((draw_x, y), part, fill=fill, font=font, anchor=draw_anchor)
            cursor_x += width
        return total_width

    def _source_label_parts(self, label):
        text = str(label or "").strip()
        if not text:
            return []
        parts = []
        token = ""
        for char in text:
            if char == "/":
                if token:
                    parts.append(token)
                    token = ""
                parts.append(char)
            else:
                token += char
        if token:
            parts.append(token)
        return parts

    def _source_label_color(self, label_part, default_color):
        key = str(label_part or "").strip().upper()
        return HOLIDAY_LABEL_COUNTRY_COLORS.get(key, default_color)

    def _text_width(self, draw, text, font):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        return bbox[2] - bbox[0]

    def _get_calendar_ui_font(self, font_size, bold=False):
        weight = "bold" if bold else "normal"
        return get_font("Jost", int(font_size), weight)

    def _get_holiday_title_font(self, font_size, bold=False):
        return get_base_ui_font(int(font_size), bold=bool(bold))

    def _merge_same_day_events(self, events):
        merged = []
        by_day = {}
        for event in events:
            bucket = by_day.setdefault(event["date"], [])
            bucket.append(event)
        for event_date in sorted(by_day):
            day_events = by_day[event_date]
            labels = "/".join(dict.fromkeys(event["label"] for event in day_events if event.get("label")))
            title = " / ".join(dict.fromkeys(self._event_display_title(event) for event in day_events if event.get("title")))
            merged.append({
                "date": event_date,
                "label": labels,
                "title": title,
                "color": day_events[0].get("color", (80, 80, 80)),
            })
        return merged

    def _event_display_title(self, event):
        title = str(event.get("title") or "").strip()
        time_label = str(event.get("time") or "").strip()
        if time_label and title:
            return f"{time_label} {title}"
        return title

    def _fit_text(self, draw, text, font, max_width):
        text = str(text or "").strip()
        if not text or draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return text
        ellipsis = "..."
        while text:
            candidate = text[:-1].rstrip() + ellipsis
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                return candidate
            text = text[:-1].rstrip()
        return ellipsis

    def _wrap_text_lines(self, draw, text, font, max_width, max_lines=2):
        words = str(text or "").strip().split()
        if not words or max_lines <= 0:
            return []

        lines = []
        current = ""
        for index, word in enumerate(words):
            candidate = f"{current} {word}".strip()
            if not current or draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
                continue

            lines.append(current)
            current = word
            if len(lines) == max_lines:
                tail = " ".join([current] + words[index + 1:])
                lines[-1] = self._fit_text(draw, f"{lines[-1]} {tail}", font, max_width)
                break

        if current and len(lines) < max_lines:
            lines.append(current)

        return [self._fit_text(draw, line, font, max_width) for line in lines[:max_lines]]

    def _clean_event_title(self, title):
        title = " ".join(str(title or "").replace("\n", " ").split())
        title = "".join(character for character in title if not self._is_calendar_symbol_noise(character))
        return " ".join(title.split())

    def _is_calendar_symbol_noise(self, character):
        if "\ufe00" <= character <= "\ufe0f":
            return True
        return unicodedata.category(character) == "So"

    @staticmethod
    def _get_selected_date(settings, tz):
        custom_date = settings.get("customDate")
        if custom_date:
            return datetime.strptime(custom_date, "%Y-%m-%d").date()

        return datetime.now(tz).date()

    @staticmethod
    def _get_locale_key(language):
        language = str(language or "en").strip().lower()
        return language if language in LOCALE_DATA else "en"

    @staticmethod
    def _strip_accents(text):
        normalized = unicodedata.normalize("NFKD", text)
        return "".join(char for char in normalized if not unicodedata.combining(char))

    def _get_weekday_abbrev(self, selected_date, locale_data, language):
        if locale_data:
            return locale_data["weekday_abbrev"][selected_date.weekday()].upper()
        return selected_date.strftime("%a").upper()[:3]

    def _get_month_name(self, selected_date, locale_data, language):
        if locale_data:
            return locale_data["months"][selected_date.month - 1]
        return self._strip_accents(selected_date.strftime("%B").upper())

    def _get_weekday_headers(self, locale_data, language):
        if locale_data:
            return locale_data["headers"]
        return ["S", "M", "T", "W", "T", "F", "S"]

    @staticmethod
    def _parse_color(value, fallback):
        if not value:
            return fallback

        try:
            return ImageColor.getrgb(value)
        except Exception:
            return fallback

    @staticmethod
    def _setting_enabled(value):
        return value is True or str(value).strip().lower() in {"1", "true", "on", "yes"}

    @staticmethod
    def _as_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]
