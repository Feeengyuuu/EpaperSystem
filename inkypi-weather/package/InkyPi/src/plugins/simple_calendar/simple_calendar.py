import calendar
import hashlib
import ipaddress
import json
import logging
import os
import re
import stat
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import icalendar
import pytz
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationPreparation,
)
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.context_cache import read_contexts
from plugins.simple_calendar import panel_scene
from plugins.simple_calendar.panel_stage import PanelStageMixin
from plugins.simple_calendar.game_events import GameEventProvider, enabled as game_events_enabled
from plugins.simple_calendar.apple_events import AppleEventProvider
from plugins.simple_calendar.game_event_sources import SERIES as GAME_EVENT_SERIES
from utils.app_utils import get_base_ui_font, get_font
from utils.atomic_file import atomic_write_json
from utils.http_client import get_http_session
from utils.theme_utils import contrast_ratio, relative_luminance

logger = logging.getLogger(__name__)

# The only local raster inputs are one weather background and one optional
# host drawing.  They live under independent roots and are each consumed once,
# so forming separate one-item batches would add staging/IPC without parallel
# work.  Keep this plugin on its existing parent-serial render path.
PARALLEL_IMAGE_STAGE_ELIGIBLE = False
PARALLEL_IMAGE_STAGE_RATIONALE = (
    "weather background and host drawing are single assets under independent roots"
)

DEFAULT_DATA_DIR = Path("/var/lib/inkypi/data")
EVENT_SNAPSHOT_SUBDIR = Path("plugins/simple_calendar/event_snapshots")
EVENT_SNAPSHOT_VERSION = 1
EVENT_SNAPSHOT_MAX_BYTES = 256 * 1024
EVENT_SNAPSHOT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
EVENT_SNAPSHOT_MAX_EVENTS = 512
EVENT_SNAPSHOT_RETENTION_SECONDS = 62 * 24 * 60 * 60
EVENT_SNAPSHOT_TITLE_MAX_CHARS = 256
EVENT_SNAPSHOT_LABEL_MAX_CHARS = 16
EVENT_SNAPSHOT_TIME_MAX_CHARS = 16
EVENT_SNAPSHOT_FILENAME_RE = re.compile(r"[0-9a-f]{64}\.json\Z")
CALENDAR_SOURCE_MAX_BYTES = 8 * 1024 * 1024
CALENDAR_SOURCE_MAX_REDIRECTS = 4

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


# 5×7 dot-matrix letter patterns for weekday abbreviations
_LETTER_W, _LETTER_H = 5, 7



# The month's wall-calendar page (calendar_page/): inset, room above for the binder rings,
# hard shadow, ring size and spacing; four finger-painted crosses strike off the days gone by and
# a drop of blood lands on today (calendar_page/marks.source.json).
CALENDAR_PAGE_DIR = Path(__file__).with_name("calendar_page")
CALENDAR_PAGE_INSET = 3
CALENDAR_PAGE_TOP = 8
CALENDAR_PAGE_SHADOW = 3
# The page hugs the month's own margins: the rings sit in the space above the title and the
# content keeps its full box, so the agenda below loses no row.
CALENDAR_PAGE_CONTENT_TOP = 0
CALENDAR_RING_HEIGHT = 24
CALENDAR_RING_PITCH = 42
CALENDAR_RING_MARGIN = 22
CALENDAR_CROSS_VARIANTS = 4
# The month table: a dot every CALENDAR_GRID_DOT_STEP px between its cells, stopping this far short
# of the table's edges (px).
CALENDAR_GRID_DOT_STEP, CALENDAR_GRID_INSET = 3, 6
# How strongly the month's border illustration is printed on the page (0-1).
CALENDAR_BACKDROP_SHARE = 0.42
# It keeps that strength within this many pixels of the page's edges and falls to this share
# of it in the middle; the agenda's band at the foot is printed at half again.
CALENDAR_BACKDROP_EDGE = 46
CALENDAR_BACKDROP_INNER = 0.32
CALENDAR_AGENDA_BAND = 70
# A cross spans a little more than the today circle and lets the struck-off number show through.
CALENDAR_CROSS_SCALE = 1.45
CALENDAR_CROSS_OPACITY = 0.88
# Today's blood drop: its splat's radius as a share of the art (marks.source.json), drawn this
# much larger than the old today circle; the falling drop and drips reach past the cell.
CALENDAR_BLOOD_SPLAT = 0.2585
CALENDAR_BLOOD_SCALE = 1.16
# A dark outline keeps today's number crisp on any part of the drop.
CALENDAR_BLOOD_INK = (60, 0, 6)


@lru_cache(maxsize=4)
def _backdrop_edge_mask(size, box):
    """255 along the page's edges, falling to CALENDAR_BACKDROP_INNER inside it, and halved
    again over the agenda band at the foot (corners excepted)."""
    width, height = size
    left, top, right, bottom = box
    inner = round(255 * CALENDAR_BACKDROP_INNER)

    def ramp(distance):
        return round(inner + (255 - inner) * max(0.0, 1 - distance / CALENDAR_BACKDROP_EDGE))

    across = Image.new("L", (width, 1))
    across.putdata([ramp(min(abs(x - left), abs(right - x))) for x in range(width)])
    down = Image.new("L", (1, height))
    down.putdata([ramp(min(abs(y - top), abs(bottom - y))) for y in range(height)])
    mask = ImageChops.lighter(across.resize(size), down.resize(size))
    agenda = Image.new("L", size, 255)
    ImageDraw.Draw(agenda).rectangle((left + CALENDAR_BACKDROP_EDGE // 2, bottom - CALENDAR_AGENDA_BAND,
                                      right - CALENDAR_BACKDROP_EDGE // 2, bottom), fill=128)
    return ImageChops.multiply(mask, agenda)


@lru_cache(maxsize=8)
def _calendar_page_art(name):
    path = CALENDAR_PAGE_DIR / name
    if not path.is_file():
        return None
    try:
        with Image.open(path) as source:
            return source.convert("RGBA")
    except OSError:
        return None


class SimpleCalendar(PanelStageMixin, BasePlugin):

    _CACHED_EVENTS_SETTING = "_inkypi_simple_calendar_cached_events"
    _SOURCE_PROVENANCE_SETTING = "_inkypi_simple_calendar_source_provenance"
    _DISPLAY_RENDER_SETTING = "_inkypiDisplayRender"
    # The moment a prepared presentation is for, so the panel's host speaks to
    # the time it will be on screen even though the date is pinned.
    _SCENE_TIME_SETTING = "_inkypi_simple_calendar_scene_time"

    def __init__(self, config, **dependencies):
        self.game_events = dependencies.pop("game_event_provider", None) or GameEventProvider()
        self.apple_events = dependencies.pop("apple_event_provider", None) or AppleEventProvider()
        super().__init__(config, **dependencies)

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        template_params['game_event_series'] = GAME_EVENT_SERIES
        template_params['game_event_status'] = self.game_events.read
        template_params['apple_event_status'] = self.apple_events.read
        return template_params

    def presentation_mode(self, settings):
        del settings
        return PresentationMode.PREPARED_BANK

    def prepare_presentation(
        self,
        settings,
        device_config,
        *,
        request,
        resolved_theme_context,
    ):
        timezone_name = device_config.get_config(
            "timezone", default="America/New_York"
        )
        tz = pytz.timezone(timezone_name)
        requested_at = datetime.fromisoformat(request.requested_at)
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=timezone.utc)
        selected_date = (
            self._get_selected_date(settings, tz)
            if (settings or {}).get("customDate")
            else requested_at.astimezone(tz).date()
        )
        events, provenance = self._load_calendar_event_snapshots(
            settings,
            selected_date,
            tz,
            require_current=True,
        )
        render_settings = dict(settings or {})
        render_settings["customDate"] = selected_date.isoformat()
        render_settings[self._SCENE_TIME_SETTING] = requested_at.astimezone(tz).isoformat()
        render_settings["_theme_render_only"] = True
        render_settings["_inkypi_theme"] = resolved_theme_context
        render_settings[self._CACHED_EVENTS_SETTING] = events
        render_settings[self._SOURCE_PROVENANCE_SETTING] = provenance.value
        image = self.generate_image(render_settings, device_config)
        preparation = PresentationPreparation(
            request_id=request.request_id,
            image=image,
            changed=True,
        )
        # generate_image already attests the merged base + optional event data.
        return preparation

    def generate_image(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)

        timezone_name = device_config.get_config("timezone", default="America/New_York")
        tz = pytz.timezone(timezone_name)
        selected_date = self._get_selected_date(settings, tz)
        reference_dt = None if settings.get("customDate") else datetime.now(tz)
        language = self._get_locale_key(settings.get("language") or settings.get("locale", "en"))
        locale_data = LOCALE_DATA.get(language)

        theme_palette = self._theme_palette_for_render(settings.get("_inkypi_theme"))
        primary_color = self._parse_color(settings.get("primaryColor"), (230, 26, 26))
        highlight_color = self._parse_color(settings.get("highlightColor"), (163, 13, 13))
        if theme_palette:
            primary_color = theme_palette["accent"]
            highlight_color = theme_palette["accent"]
        layout_position = settings.get("layoutPosition", "left").lower()
        theme_render_only = self._setting_enabled(settings.get("_theme_render_only"))
        display_render = self._setting_enabled(
            settings.get(self._DISPLAY_RENDER_SETTING)
        )
        cached_events = settings.get(self._CACHED_EVENTS_SETTING)
        provenance = settings.get(self._SOURCE_PROVENANCE_SETTING)
        if cached_events is not None:
            holiday_events = list(cached_events)
        elif theme_render_only or display_render:
            holiday_events, provenance = self._load_calendar_event_snapshots(
                settings,
                selected_date,
                tz,
                require_current=True,
            )
        else:
            holiday_events, provenance = self._refresh_calendar_event_snapshots(
                settings,
                selected_date,
                tz,
            )
        for enabled_key, provider in (("showGameEvents", self.game_events), ("showAppleEvents", self.apple_events)):
            if not game_events_enabled(settings.get(enabled_key)):
                continue
            event_result = (
                provider.read(settings)
                if cached_events is not None or theme_render_only or display_render
                else provider.refresh(settings)
            )
            _current_month, next_month = self._current_and_next_month(selected_date)
            calendar_events = [
                event
                for month_date in (selected_date, next_month)
                for event in provider.calendar_events(event_result, month_date, tz)
            ]
            if theme_palette:
                for event in calendar_events:
                    event["color"] = theme_palette["accent"]
            holiday_events = list(holiday_events) + calendar_events
            provenance = self._worst_source_provenance([
                provenance or SourceProvenance.LIVE, event_result.provenance
            ])
        # The panel's art and host follow the moment it is shown: the background
        # turns over with the time of day, and the host reacts to today's plans.
        weather_slug = self._weather_panel_slug(settings, device_config)
        date_hero_overlay_enabled = self._date_hero_overlay_enabled(settings)
        scene_time = self._scene_time(settings, selected_date, tz, reference_dt)
        scene = (
            panel_scene.choose_scene(holiday_events, selected_date, scene_time, weather_slug)
            if date_hero_overlay_enabled
            else None
        )
        weather_panel_background_path = self._weather_panel_background_for(
            weather_slug,
            settings,
            selected_date,
            daypart=panel_scene.daypart(scene_time),
            free_stage=bool(scene and scene.costume),
        )

        image = self._render_calendar(
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
            theme_palette,
            scene,
        )
        if provenance is not None:
            image = attach_source_provenance(image, provenance)
        return image

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
        theme_palette=None,
        scene=None,
    ):
        W, H = dimensions
        holiday_events = holiday_events or []
        current_month_events = self._events_for_selected_month(holiday_events, selected_date)
        holiday_events_by_day = self._group_holiday_events_by_day(current_month_events)
        upcoming_event_rows = self._upcoming_event_rows(
            holiday_events,
            selected_date,
            reference_dt=reference_dt,
            limit=12,
        )
        _current_month, next_month = self._current_and_next_month(selected_date)
        next_month_event_rows = self._upcoming_event_rows(holiday_events, next_month, limit=6)

        # Colours
        accent = primary_color
        surface = (255, 255, 255)
        panel_bg = (248, 248, 247)
        divider = (220, 220, 220)
        selected_text = (255, 255, 255)
        text_color = (0, 0, 0)
        muted_text = (132, 132, 132)
        if theme_palette:
            accent = theme_palette["accent"]
            surface = theme_palette["background"]
            panel_bg = theme_palette["panel"]
            divider = theme_palette["rule"]
            text_color = theme_palette["ink"]
            muted_text = theme_palette["muted"]
            selected_text = self._highest_contrast_color(
                highlight_color,
                theme_palette["background"],
                theme_palette["ink"],
            )

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
            theme_palette,
        )
        # Over a picture the date is lettered in ink with a surface halo and a thin
        # ink edge, so it reads on bright and dark parts of the art alike.
        outlined = bool(weather_panel_background_path)
        focus_muted = text_color if outlined else muted_text
        self._draw_focus_text(
            draw, (panel_cx, weekday_y), weekday_str, clean_weekday_font, focus_muted,
            surface if outlined else None, text_color,
        )
        self._draw_focus_text(
            draw, (panel_cx, day_y), day_str, clean_day_font, text_color,
            surface if outlined else None, text_color,
        )
        rule_width = max(int(W * 0.004), 2)
        rule_points = [(panel_cx - rule_w // 2, rule_y), (panel_cx + rule_w // 2, rule_y)]
        if outlined:
            halo = max(rule_width // 2, 2)
            draw.rectangle(
                [rule_points[0][0] - halo - 1, rule_y - rule_width // 2 - halo - 1,
                 rule_points[1][0] + halo + 1, rule_y + rule_width // 2 + halo + 1],
                fill=surface,
                outline=text_color,
                width=1,
            )
        draw.line(rule_points, fill=accent, width=rule_width)
        month_year = f"{month_name} {selected_date.year}"
        self._draw_focus_text(
            draw, (panel_cx, date_y), month_year, clean_date_font, focus_muted,
            surface if outlined else None, text_color,
        )
        if date_hero_overlay_enabled and scene is not None:
            # The host stands under the month line and never covers the date.
            def text_box(xy, text, font):
                left, top, right, bottom = draw.textbbox(xy, text, font=font, anchor="mm")
                return (left - 6, top - 6, right + 6, bottom + 6)

            month_box = text_box((panel_cx, date_y), month_year, clean_date_font)
            stripe = max(int(panel_px * 0.018), 4)
            stage_left = p_left + (stripe if layout_position == "left" else 0) + 6
            stage_right = p_right - (0 if layout_position == "left" else stripe) - 6
            self._draw_panel_stage(
                img,
                scene,
                (stage_left, month_box[3], stage_right, p_bottom - 5),
                [
                    text_box((panel_cx, weekday_y), weekday_str, clean_weekday_font),
                    text_box((panel_cx, day_y), day_str, clean_day_font),
                    (rule_points[0][0] - 6, rule_y - 8, rule_points[1][0] + 6, rule_y + 8),
                    month_box,
                ],
                weather_panel_background_path,
                self._stage_colors(theme_palette, surface, text_color, muted_text),
                side_seed=self._stage_side_seed(selected_date, scene),
            )

        # === CALENDAR CONTENT (inside light area) ===
        # The month hangs as a wall-calendar page: rings along its top, ink edge, hard shadow.
        c_left, c_top, c_right, c_bottom = self._draw_wall_calendar_page(
            img, (c_left, c_top, c_right, c_bottom), surface, text_color, month=selected_date.month)
        cal_w = c_right - c_left
        cal_h = c_bottom - c_top
        cal_cx = c_left + cal_w // 2
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
            fill=divider,
            width=max(int(W * 0.0018), 1),
        )
        cal_grid = calendar.Calendar(firstweekday=6).monthdayscalendar(selected_date.year, selected_date.month)
        num_weeks = len(cal_grid)
        # Spend vertical whitespace on individual events without shrinking any
        # font. Six-week months keep enough space for dates and their markers.
        agenda_row_h = max(int(cal_w * 0.032), 14) + 4
        grid_gap = int(cal_h * 0.015)
        agenda_budget = max(0, c_bottom - grid_top_y - grid_gap - num_weeks * (day_font_size + 7))
        agenda_capacity = max(0, int((agenda_budget - 12) // agenda_row_h))
        agenda_capacity = min(6, agenda_capacity)
        following_event_rows = upcoming_event_rows[agenda_capacity:]
        upcoming_event_rows = upcoming_event_rows[:agenda_capacity]
        agenda_rows = len(upcoming_event_rows)
        if next_month_event_rows:
            agenda_rows = agenda_capacity
        event_list_h = 12 + agenda_row_h * agenda_rows if agenda_rows else 0
        available_grid_h = c_bottom - grid_top_y - grid_gap - event_list_h
        row_h = available_grid_h / num_weeks

        today_circle_r = int(min(col_w, row_h) * 0.46)
        self._draw_month_grid(draw, (grid_left, grid_top_y - int(header_font_size * 0.40), grid_right_edge,
                                     grid_top_y + row_h * num_weeks), grid_top_y, col_w, row_h, num_weeks, text_color)

        # Today's blood drop goes down first: its splashes and drips sit under every other day's
        # number and cross, and today's number is printed on top of it.
        bled = False
        for week_idx, week in enumerate(cal_grid):
            if selected_date.day in week:
                bled = self._bleed_on_day(
                    img,
                    (grid_left + col_w * week.index(selected_date.day) + col_w / 2,
                     grid_top_y + row_h * week_idx + row_h / 2),
                    today_circle_r,
                )
        for week_idx, week in enumerate(cal_grid):
            row_cy = grid_top_y + row_h * week_idx + row_h / 2
            for dow, day in enumerate(week):
                if day == 0:
                    continue
                col_cx = grid_left + col_w * dow + col_w / 2

                if day == selected_date.day:
                    # Today: a drop of blood has just landed on it (the old circle if the art is missing).
                    if not bled:
                        draw.ellipse(
                            [col_cx - today_circle_r, row_cy - today_circle_r,
                             col_cx + today_circle_r, row_cy + today_circle_r],
                            fill=highlight_color,
                        )
                    draw.text(
                        (col_cx, row_cy), str(day),
                        fill=selected_text, font=day_font, anchor="mm",
                        stroke_width=2 if bled else 0, stroke_fill=CALENDAR_BLOOD_INK,
                    )
                elif day < selected_date.day:
                    # A day already lived: its number fades and a finger-painted red cross strikes it off.
                    draw.text(
                        (col_cx, row_cy), str(day),
                        fill=muted_text, font=day_font, anchor="mm",
                    )
                    self._cross_off_day(img, selected_date.replace(day=day), (col_cx, row_cy),
                                        today_circle_r * CALENDAR_CROSS_SCALE)
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
                        selected_color=selected_text,
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
            following_event_rows=following_event_rows,
            next_month_event_rows=next_month_event_rows,
        )
        return img

    def _draw_wall_calendar_page(self, img, area, surface, ink, month=None):
        """Draw the month's page inside ``area`` and return the box its content goes in.

        The page is inset with a hard comic shadow, an ink edge and binder rings spaced
        along its top, and runs on past the bottom edge; the month's own illustration
        (calendar_page/backdrop_MM.png) is printed lightly on it. Without the art the
        plain page still frames the month.
        """
        left, top, right, bottom = area
        # The page runs on past the panel's bottom edge, so the agenda's last line is never cut.
        page = (left + CALENDAR_PAGE_INSET, top + CALENDAR_PAGE_TOP, right - CALENDAR_PAGE_INSET - CALENDAR_PAGE_SHADOW,
                bottom + 20)
        draw = ImageDraw.Draw(img)
        shadow = tuple(page[i] + CALENDAR_PAGE_SHADOW for i in range(4))
        draw.rounded_rectangle(shadow, radius=10, fill=ink)
        draw.rounded_rectangle(page, radius=10, fill=surface, outline=ink, width=2)
        if month:
            self._print_month_backdrop(img, page, surface, month, bottom)
            draw.rounded_rectangle(page, radius=10, outline=ink, width=2)
        ring = _calendar_page_art("binder_ring.png")
        if ring is not None:
            height = CALENDAR_RING_HEIGHT
            ring = ring.resize((max(1, round(ring.width * height / ring.height)), height), Image.LANCZOS)
            usable = page[2] - page[0] - 2 * CALENDAR_RING_MARGIN
            count = max(2, usable // CALENDAR_RING_PITCH + 1)
            step = usable / (count - 1)
            for index in range(count):
                x = round(page[0] + CALENDAR_RING_MARGIN + index * step - ring.width / 2)
                img.paste(ring, (x, page[1] - round(height * 0.62)), ring)
        return (left, top + CALENDAR_PAGE_CONTENT_TOP, right, bottom)

    @staticmethod
    def _print_month_backdrop(img, page, surface, month, visible_bottom):
        """The month's border illustration, printed lightly on the page: as wide as the page,
        its foot on the panel's bottom edge, clipped to the page and faded toward the paper."""
        art = _calendar_page_art(f"backdrop_{month:02d}.png")
        if art is None:
            return
        left, top, right, _bottom = page
        width = right - left - 4
        scaled = art.resize((width, max(1, round(art.height * width / art.width))), Image.LANCZOS)
        origin = (left + 2, visible_bottom - scaled.height)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        layer.paste(scaled, origin)
        # Printed full strength along the page's edges and faded toward its middle, and fainter
        # again behind the agenda, so the month's numbers and its events read on top of it.
        alpha = layer.getchannel("A").point(lambda value: round(value * CALENDAR_BACKDROP_SHARE))
        alpha = ImageChops.multiply(alpha, _backdrop_edge_mask(img.size, (left, top, right, visible_bottom)))
        clip = Image.new("L", img.size, 0)
        ImageDraw.Draw(clip).rounded_rectangle((left + 2, top + 2, right - 2, visible_bottom), radius=9, fill=255)
        layer.putalpha(ImageChops.multiply(alpha, clip))
        base = img.convert("RGBA")
        base.alpha_composite(layer)
        img.paste(base.convert("RGB"))

    @staticmethod
    def _draw_month_grid(draw, frame, rows_top, col_w, row_h, weeks, ink):
        """The month as a planner table, drawn soft: no frame, only fine dotted rules between its
        cells (dots of solid ink, which stay crisp where a light grey line would dither into noise
        on the panel), one column a weekday and one row a week."""
        left, top, right, bottom = frame
        inset = CALENDAR_GRID_INSET
        for column in range(1, 7):
            x = round(left + col_w * column)
            for y in range(round(top) + inset, round(bottom) - inset, CALENDAR_GRID_DOT_STEP):
                draw.point((x, y), fill=ink)
        for week in range(1, weeks):
            y = round(rows_top + row_h * week)
            for x in range(round(left) + inset, round(right) - inset, CALENDAR_GRID_DOT_STEP):
                draw.point((x, y), fill=ink)

    @staticmethod
    def _bleed_on_day(img, centre, radius):
        """Paste the blood drop with its splat centred on today, sized off the today circle's
        radius; False when the art is missing."""
        drop = _calendar_page_art("blood_today.png")
        if drop is None:
            return False
        side = max(16, round(radius * CALENDAR_BLOOD_SCALE / CALENDAR_BLOOD_SPLAT))
        drop = drop.resize((side, side), Image.LANCZOS)
        img.paste(drop, (round(centre[0] - side / 2), round(centre[1] - side / 2)), drop)
        return True

    @staticmethod
    def _cross_off_day(img, day, centre, size):
        """Paste one of the finger-painted crosses over a past day, the same one for that date every time."""
        variant = 1 + (day.toordinal() * 7 + day.day) % CALENDAR_CROSS_VARIANTS
        mark = _calendar_page_art(f"cross_{variant}.png")
        if mark is None:
            return
        side = max(8, int(size))
        mark = mark.resize((side, side), Image.LANCZOS)
        alpha = mark.getchannel("A").point(lambda value: round(value * CALENDAR_CROSS_OPACITY))
        img.paste(mark, (round(centre[0] - side / 2), round(centre[1] - side / 2)), alpha)

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

    def _date_hero_overlay_enabled(self, settings):
        settings = settings or {}
        if "dateHeroOverlays" in settings:
            return self._setting_enabled(settings.get("dateHeroOverlays"))
        if "dateHeroOverlay" in settings:
            return self._setting_enabled(settings.get("dateHeroOverlay"))
        return self._weather_panel_background_style(settings) != "classic"

    def _scene_time(self, settings, selected_date, tz, reference_dt=None):
        """The moment the panel's host speaks to, on the selected date."""
        moment = None
        raw = (settings or {}).get(self._SCENE_TIME_SETTING)
        if raw:
            try:
                moment = datetime.fromisoformat(str(raw))
            except ValueError:
                moment = None
            if moment is not None and moment.tzinfo is None:
                moment = tz.localize(moment)
        if moment is None:
            moment = reference_dt
        if moment is None:
            # A pinned date without a moment (previews, tests) reads as midday.
            return tz.localize(datetime(selected_date.year, selected_date.month, selected_date.day, 12))
        moment = moment.astimezone(tz)
        if moment.date() != selected_date:
            moment = tz.localize(datetime.combine(selected_date, moment.time()))
        return moment

    def _get_weather_panel_background_path(self, settings, device_config, selected_date=None):
        return self._weather_panel_background_for(
            self._weather_panel_slug(settings, device_config), settings, selected_date
        )

    def _weather_panel_background_for(self, slug, settings, selected_date=None, daypart=None, free_stage=False):
        if not slug:
            return None
        return self._weather_background_path(
            slug, settings, selected_date, daypart=daypart, free_stage=free_stage
        )

    def _weather_panel_slug(self, settings, device_config):
        """The weather the panel shows, or None when its background is turned off."""
        if not self._weather_panel_background_enabled(settings):
            return None

        theme_render_only = self._setting_enabled(settings.get("_theme_render_only"))
        display_render = self._setting_enabled(
            settings.get(self._DISPLAY_RENDER_SETTING)
        )
        provider_free_render = theme_render_only or display_render
        source = "context"
        slug = self._read_weather_context_background_slug(
            include_stale=provider_free_render
        )
        if not slug and not provider_free_render:
            source = "open-meteo"
            weather_settings = self._find_weather_source_settings(settings, device_config)
            slug = self._fetch_current_weather_background_slug(weather_settings) if weather_settings else None

        if not slug:
            source = "fallback"
            slug = settings.get("weatherPanelFallback") or WEATHER_PANEL_BACKGROUND_DEFAULT

        logger.info("Simple Calendar weather panel background: %s (%s)", slug, source)
        return slug

    def _weather_panel_background_enabled(self, settings):
        if "weatherPanelBackground" not in settings:
            return True
        return self._setting_enabled(settings.get("weatherPanelBackground"))

    def _read_weather_context_background_slug(self, *, include_stale=False):
        if include_stale and not self._weather_context_storage_is_readable():
            return None
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

    @staticmethod
    def _weather_context_storage_is_readable():
        raw = os.getenv("INKYPI_CONTEXT_CACHE_DIR", "").strip()
        runtime_raw = os.getenv("INKYPI_CACHE_DIR", "").strip()
        runtime_root = Path(runtime_raw).expanduser() if runtime_raw else None
        if raw:
            directory = Path(raw).expanduser()
            if not directory.is_absolute():
                directory = (
                    runtime_root / "context" / directory
                    if runtime_root is not None
                    else Path(__file__).resolve().parents[1] / directory
                )
        elif runtime_root is not None:
            directory = runtime_root / "context"
        else:
            directory = Path(__file__).resolve().parents[1] / ".context_cache"

        try:
            info = os.lstat(directory)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return False
            for candidate in directory.glob("*.json"):
                candidate_info = os.lstat(candidate)
                if stat.S_ISLNK(candidate_info.st_mode) or not stat.S_ISREG(
                    candidate_info.st_mode
                ):
                    return False
        except OSError:
            return False
        return True

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

    def _weather_background_path(self, slug, settings=None, selected_date=None, daypart=None, free_stage=False):
        """One background for the weather, stable for the date and time of day.

        With a daypart the default pool also takes the clean plates drawn for
        that time of day, and each daypart shows a different picture. A
        free_stage asks for a background whose bottom corners are clear for the
        host, when the pool has one.
        """
        normalized = self._normalize_weather_background_slug(slug)
        if not normalized:
            return None
        style = self._weather_panel_background_style(settings)
        candidates = self._weather_background_candidates(normalized, style)
        if daypart and style == WEATHER_PANEL_BACKGROUND_STYLE_DEFAULT:
            candidates = list(dict.fromkeys(candidates + self._clean_plate_candidates(normalized, daypart)))
        if free_stage and style != "classic":
            candidates = [path for path in candidates if not self._occupied_corners(path)] or candidates
        if not candidates:
            logger.debug("Simple Calendar weather panel background missing: %s (%s)", normalized, style)
            return None
        index = self._stable_weather_background_index(normalized, style, selected_date, len(candidates), daypart)
        return str(candidates[index])

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

    def _stable_weather_background_index(self, slug, style, selected_date, count, daypart=None):
        if count <= 1:
            return 0
        if hasattr(selected_date, "isoformat"):
            date_number = selected_date.toordinal()
        else:
            date_number = date.today().toordinal()
        digest = hashlib.sha256(f"{slug}|{style}".encode("utf-8")).hexdigest()
        offset = int(digest[:12], 16)
        if daypart:
            # Each daypart starts a quarter of the pool further on, so a day
            # shows different pictures while every daypart still steps through
            # the whole pool one picture per day.
            offset += panel_scene.daypart_index(daypart) * max(count // len(panel_scene.DAYPARTS), 1)
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

    def _configured_calendar_sources(self, settings):
        sources = []
        if self._holidays_enabled(settings):
            sources.extend(self._get_holiday_sources(settings))
        if self._personal_calendars_enabled(settings):
            sources.extend(self._get_personal_calendar_sources(settings))
        return sources

    @staticmethod
    def _current_and_next_month(selected_date):
        current = date(selected_date.year, selected_date.month, 1)
        if current.month == 12:
            following = date(current.year + 1, 1, 1)
        else:
            following = date(current.year, current.month + 1, 1)
        return current, following

    @staticmethod
    def _worst_source_provenance(values):
        ranking = {
            SourceProvenance.LIVE: 0,
            SourceProvenance.FRESH_CACHE: 1,
            SourceProvenance.LOCAL_FALLBACK: 2,
            SourceProvenance.STALE_CACHE: 3,
        }
        return max(values, key=lambda value: ranking[SourceProvenance(value)])

    def _refresh_calendar_event_snapshots(self, settings, selected_date, tz):
        """DATA-only provider refresh for the visible and following month."""

        sources = self._configured_calendar_sources(settings)
        if not sources:
            return [], SourceProvenance.LIVE

        current_month, next_month = self._current_and_next_month(selected_date)
        month_results = []
        for month_date in (selected_date, next_month):
            events, failures = self._fetch_calendar_sources(
                sources,
                month_date,
                tz,
            )
            if failures:
                cached, _cached_provenance = self._read_event_snapshot_with_provenance(
                    sources,
                    month_date,
                    tz,
                )
                month_results.append(
                    (month_date, cached, SourceProvenance.STALE_CACHE, False)
                )
            else:
                month_results.append(
                    (
                        month_date,
                        self._normalize_snapshot_events(events),
                        SourceProvenance.LIVE,
                        True,
                    )
                )

        protected_paths = {
            self._event_snapshot_path(
                self._event_snapshot_fingerprint(sources, month_date, tz),
                create=False,
            )
            for month_date in (selected_date, next_month)
        }
        for month_date, events, _provenance, should_write in month_results:
            if should_write:
                self._write_event_snapshot(
                    sources,
                    month_date,
                    tz,
                    events,
                    data_month=current_month,
                    protected_paths=protected_paths,
                )

        visible_events = [event for result in month_results for event in result[1]]
        provenance = self._worst_source_provenance(
            [result[2] for result in month_results]
        )
        return visible_events, provenance

    def _load_calendar_event_snapshots(
        self,
        settings,
        selected_date,
        tz,
        *,
        require_current,
    ):
        """Provider-free presentation/theme load of validated snapshot data."""

        sources = self._configured_calendar_sources(settings)
        if not sources:
            return [], SourceProvenance.FRESH_CACHE

        _current_month, next_month = self._current_and_next_month(selected_date)
        try:
            current_events, current_provenance = (
                self._read_event_snapshot_with_provenance(
                    sources,
                    selected_date,
                    tz,
                )
            )
        except RuntimeError as exc:
            if require_current:
                raise RuntimeError(
                    "Simple Calendar current-month snapshot is unavailable; "
                    "event snapshot required for provider-free redraw; "
                    "refusing provider-free redraw"
                ) from exc
            current_events = []
            current_provenance = SourceProvenance.LOCAL_FALLBACK

        provenances = [current_provenance]
        next_fingerprint = self._event_snapshot_fingerprint(
            sources,
            next_month,
            tz,
        )
        next_path = self._event_snapshot_path(next_fingerprint, create=False)
        if os.path.lexists(next_path):
            next_events, next_provenance = self._read_event_snapshot_with_provenance(
                sources,
                next_month,
                tz,
            )
            provenances.append(next_provenance)
            current_events = list(current_events) + next_events
        return current_events, self._worst_source_provenance(provenances)

    def _get_calendar_events(
        self,
        settings,
        selected_date,
        tz,
        *,
        allow_remote=True,
    ):
        sources = []
        if self._holidays_enabled(settings):
            sources.extend(self._get_holiday_sources(settings))
        if self._personal_calendars_enabled(settings):
            sources.extend(self._get_personal_calendar_sources(settings))

        local_sources = [
            source
            for source in sources
            if not self._calendar_source_requires_network(source)
        ]
        remote_sources = [
            source
            for source in sources
            if self._calendar_source_requires_network(source)
        ]
        local_events, _local_failures = self._fetch_calendar_sources(
            local_sources,
            selected_date,
            tz,
        )
        local_events = self._normalize_snapshot_events(local_events)

        if allow_remote:
            remote_events, remote_failures = self._fetch_calendar_sources(
                remote_sources,
                selected_date,
                tz,
            )
            if remote_failures:
                remote_events = self._read_event_snapshot(
                    remote_sources,
                    selected_date,
                    tz,
                )
            else:
                remote_events = self._normalize_snapshot_events(remote_events)
                if remote_sources:
                    remote_events = self._write_event_snapshot(
                        remote_sources,
                        selected_date,
                        tz,
                        remote_events,
                    )
        elif remote_sources:
            remote_events = self._read_event_snapshot(
                remote_sources,
                selected_date,
                tz,
            )
        else:
            remote_events = []

        return self._dedupe_holiday_events(local_events + remote_events)

    def _fetch_calendar_sources(self, sources, selected_date, tz):
        events = []
        failures = []
        for source in sources:
            try:
                events.extend(self._fetch_holiday_events(source, selected_date, tz))
            except Exception as exc:
                failures.append(source)
                self._log_calendar_source_failure(source, exc)
        return events, failures

    @staticmethod
    def _log_calendar_source_failure(source, exc):
        label = " ".join(str((source or {}).get("label") or "configured").split())
        label = label[:32] or "configured"
        kind = str((source or {}).get("kind") or "configured").strip().lower()
        if kind not in {"holiday", "personal"}:
            kind = "configured"
        logger.warning(
            "Calendar source unavailable label=%s kind=%s error_type=%s status=unavailable",
            label,
            kind,
            type(exc).__name__,
        )

    def _write_event_snapshot(
        self,
        sources,
        selected_date,
        tz,
        events,
        *,
        data_month=None,
        protected_paths=(),
    ):
        fingerprint = self._event_snapshot_fingerprint(sources, selected_date, tz)
        snapshot_path = self._event_snapshot_path(fingerprint, create=True)
        normalized = self._normalize_snapshot_events(events)
        serialized = [self._serialize_snapshot_event(event) for event in normalized]
        payload = self._event_snapshot_payload(
            fingerprint,
            selected_date,
            tz,
            serialized,
            data_month=data_month,
        )
        while serialized and self._event_snapshot_size(payload) > EVENT_SNAPSHOT_MAX_BYTES:
            serialized.pop()
            payload = self._event_snapshot_payload(
                fingerprint,
                selected_date,
                tz,
                serialized,
                data_month=data_month,
            )
        if self._event_snapshot_size(payload) > EVENT_SNAPSHOT_MAX_BYTES:
            raise RuntimeError("Remote calendar event snapshot exceeds its size limit")

        protected_paths = {Path(path).absolute() for path in protected_paths}
        protected_paths.add(snapshot_path.absolute())
        self._prune_event_snapshots(
            snapshot_path.parent,
            protected_paths=protected_paths,
        )
        stored_bytes = self._event_snapshot_storage_bytes(
            snapshot_path.parent,
            exclude=snapshot_path,
        )
        if stored_bytes + self._event_snapshot_size(payload) > EVENT_SNAPSHOT_MAX_TOTAL_BYTES:
            raise RuntimeError("Remote calendar event snapshot byte budget is exhausted")
        try:
            atomic_write_json(snapshot_path, payload, mode=0o600)
        except OSError as exc:
            raise RuntimeError(
                "Remote calendar event snapshot could not be written"
            ) from exc
        self._assert_snapshot_regular_file(snapshot_path)
        self._prune_event_snapshots(
            snapshot_path.parent,
            protected_paths=protected_paths,
        )
        return normalized[: len(serialized)]

    def _read_event_snapshot(self, sources, selected_date, tz):
        events, _provenance = self._read_event_snapshot_with_provenance(
            sources,
            selected_date,
            tz,
        )
        return events

    def _read_event_snapshot_with_provenance(self, sources, selected_date, tz):
        fingerprint = self._event_snapshot_fingerprint(sources, selected_date, tz)
        snapshot_path = self._event_snapshot_path(fingerprint, create=False)
        raw = self._read_event_snapshot_bytes(snapshot_path)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(
                "Remote calendar event snapshot is corrupt; refusing theme-only redraw"
            ) from exc

        expected_month = selected_date.strftime("%Y-%m")
        expected_timezone = getattr(tz, "zone", None) or str(tz)
        if (
            not isinstance(payload, dict)
            or payload.get("version") != EVENT_SNAPSHOT_VERSION
            or payload.get("source_fingerprint") != fingerprint
            or payload.get("month") != expected_month
            or payload.get("timezone") != expected_timezone
        ):
            raise RuntimeError(
                "Remote calendar event snapshot does not match this calendar; "
                "refusing theme-only redraw"
            )
        serialized = payload.get("events")
        if not isinstance(serialized, list) or len(serialized) > EVENT_SNAPSHOT_MAX_EVENTS:
            raise RuntimeError(
                "Remote calendar event snapshot has invalid bounds; "
                "refusing theme-only redraw"
            )
        try:
            events = [self._deserialize_snapshot_event(event) for event in serialized]
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Remote calendar event snapshot has invalid events; "
                "refusing theme-only redraw"
            ) from exc
        data_month = str(payload.get("data_month") or expected_month)
        provenance = (
            SourceProvenance.STALE_CACHE
            if data_month != expected_month
            else SourceProvenance.FRESH_CACHE
        )
        return self._dedupe_holiday_events(events), provenance

    @staticmethod
    def _event_snapshot_payload(
        fingerprint,
        selected_date,
        tz,
        serialized,
        *,
        data_month=None,
    ):
        source_month = data_month or selected_date
        if isinstance(source_month, str):
            data_month_text = source_month
        else:
            data_month_text = source_month.strftime("%Y-%m")
        return {
            "version": EVENT_SNAPSHOT_VERSION,
            "source_fingerprint": fingerprint,
            "month": selected_date.strftime("%Y-%m"),
            "data_month": data_month_text,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "timezone": getattr(tz, "zone", None) or str(tz),
            "events": serialized,
        }

    @staticmethod
    def _event_snapshot_size(payload):
        return len(
            (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode(
                "utf-8"
            )
        )

    @staticmethod
    def _event_snapshot_fingerprint(sources, selected_date, tz):
        descriptors = []
        for source in sources:
            descriptors.append(
                {
                    "url": str(source.get("url") or "").strip(),
                    "label": str(source.get("label") or "").strip(),
                    "color": list(source.get("color") or ()),
                    "kind": str(source.get("kind") or "").strip(),
                }
            )
        seed = {
            "month": selected_date.strftime("%Y-%m"),
            "timezone": getattr(tz, "zone", None) or str(tz),
            "sources": descriptors,
        }
        encoded = json.dumps(
            seed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _event_snapshot_path(cls, fingerprint, *, create):
        filename = f"{fingerprint}.json"
        if not EVENT_SNAPSHOT_FILENAME_RE.fullmatch(filename):
            raise RuntimeError("Calendar event snapshot fingerprint is unsafe")

        data_root = Path(
            os.environ.get("INKYPI_DATA_DIR") or DEFAULT_DATA_DIR
        ).expanduser().absolute()
        directory = (data_root / EVENT_SNAPSHOT_SUBDIR).absolute()
        try:
            if create:
                data_root.mkdir(parents=True, exist_ok=True)
            cls._assert_snapshot_path(data_root, directory)
            if create:
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            cls._assert_snapshot_directory(
                data_root,
                directory,
                required=create,
            )
            if create and os.name != "nt":
                os.chmod(directory, 0o700)
            target = directory / filename
            cls._assert_snapshot_path(data_root, target)
            if os.path.lexists(target):
                cls._assert_snapshot_regular_file(target)
        except RuntimeError:
            raise
        except OSError as exc:
            raise RuntimeError("Calendar event snapshot storage is unsafe") from exc
        return target

    @staticmethod
    def _assert_snapshot_path(root, target):
        root = root.absolute()
        target = target.absolute()
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("Calendar event snapshot path escaped its root") from exc

        current = root
        candidates = [root]
        for part in relative.parts:
            current = current / part
            candidates.append(current)
        for candidate in candidates:
            if not os.path.lexists(candidate):
                continue
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError("Calendar event snapshot paths cannot use symlinks")

        if os.path.lexists(root):
            root_info = os.lstat(root)
            if not stat.S_ISDIR(root_info.st_mode):
                raise RuntimeError("Calendar event snapshot root is not a directory")
            resolved_root = root.resolve(strict=True)
            resolved_target = target.resolve(strict=False)
            try:
                resolved_target.relative_to(resolved_root)
            except ValueError as exc:
                raise RuntimeError(
                    "Calendar event snapshot path escaped its resolved root"
                ) from exc

    @classmethod
    def _assert_snapshot_directory(cls, root, directory, *, required):
        cls._assert_snapshot_path(root, directory)
        if not os.path.lexists(directory):
            if required:
                raise RuntimeError("Calendar event snapshot directory is missing")
            return
        info = os.lstat(directory)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Calendar event snapshot directory is unsafe")

    @staticmethod
    def _assert_snapshot_regular_file(path):
        try:
            info = os.lstat(path)
        except FileNotFoundError as exc:
            raise RuntimeError("Calendar event snapshot file is missing") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Calendar event snapshot file is unsafe")

    @classmethod
    def _read_event_snapshot_bytes(cls, snapshot_path):
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        try:
            descriptor = os.open(snapshot_path, flags)
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Remote calendar event snapshot is missing; refusing theme-only redraw"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                "Remote calendar event snapshot is unsafe; refusing theme-only redraw"
            ) from exc

        try:
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = None
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise RuntimeError(
                        "Remote calendar event snapshot is unsafe; "
                        "refusing theme-only redraw"
                    )
                raw = handle.read(EVENT_SNAPSHOT_MAX_BYTES + 1)
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(raw) > EVENT_SNAPSHOT_MAX_BYTES:
            raise RuntimeError(
                "Remote calendar event snapshot is oversized; refusing theme-only redraw"
            )
        return raw

    @staticmethod
    def _prune_event_snapshots(directory, *, protected_paths=()):
        protected = {Path(path).absolute() for path in protected_paths}
        try:
            directory_info = os.lstat(directory)
            if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(
                directory_info.st_mode
            ):
                return
            candidates = list(directory.iterdir())
        except OSError:
            return

        now = time.time()
        for candidate in candidates:
            if candidate.absolute() in protected:
                continue
            if not EVENT_SNAPSHOT_FILENAME_RE.fullmatch(candidate.name):
                continue
            try:
                info = os.lstat(candidate)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                continue
            if now - info.st_mtime <= EVENT_SNAPSHOT_RETENTION_SECONDS:
                continue
            try:
                current = os.lstat(candidate)
                if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(
                    current.st_mode
                ):
                    continue
                candidate.unlink()
            except OSError as exc:
                logger.warning(
                    "Calendar event snapshot cleanup failed "
                    "error_type=%s status=retained",
                    type(exc).__name__,
                )

    @staticmethod
    def _event_snapshot_storage_bytes(directory, *, exclude=None):
        try:
            directory_info = os.lstat(directory)
            if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(
                directory_info.st_mode
            ):
                return 0
            candidates = list(directory.iterdir())
        except OSError:
            return 0

        total = 0
        for candidate in candidates:
            if candidate == exclude or not EVENT_SNAPSHOT_FILENAME_RE.fullmatch(
                candidate.name
            ):
                continue
            try:
                info = os.lstat(candidate)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                continue
            total += max(0, int(info.st_size))
        return total

    def _normalize_snapshot_events(self, events):
        normalized = []
        for event in events or []:
            item = self._normalize_snapshot_event(event)
            if item is not None:
                normalized.append(item)
        return self._dedupe_holiday_events(normalized)[:EVENT_SNAPSHOT_MAX_EVENTS]

    def _normalize_snapshot_event(self, event):
        if not isinstance(event, dict):
            return None
        event_date = event.get("date")
        if isinstance(event_date, datetime):
            event_date = event_date.date()
        if not isinstance(event_date, date):
            return None

        title = self._clean_event_title(event.get("title"))[
            :EVENT_SNAPSHOT_TITLE_MAX_CHARS
        ]
        label = " ".join(str(event.get("label") or "").split())[
            :EVENT_SNAPSHOT_LABEL_MAX_CHARS
        ]
        time_label = " ".join(str(event.get("time") or "").split())[
            :EVENT_SNAPSHOT_TIME_MAX_CHARS
        ]
        kind = str(event.get("kind") or "holiday").strip().lower()
        if kind not in {"holiday", "personal"}:
            kind = "holiday"
        try:
            color = tuple(int(channel) for channel in event.get("color") or ())
        except (TypeError, ValueError):
            color = ()
        if len(color) != 3 or any(channel < 0 or channel > 255 for channel in color):
            color = (80, 80, 80)

        normalized = {
            "date": event_date,
            "title": title,
            "label": label,
            "color": color,
            "kind": kind,
            "time": time_label,
        }
        starts_at = event.get("starts_at")
        if isinstance(starts_at, datetime):
            normalized["starts_at"] = starts_at
        return normalized

    @staticmethod
    def _serialize_snapshot_event(event):
        serialized = {
            "date": event["date"].isoformat(),
            "title": event["title"],
            "label": event["label"],
            "color": list(event["color"]),
            "kind": event["kind"],
            "time": event.get("time", ""),
        }
        if event.get("starts_at"):
            serialized["starts_at"] = event["starts_at"].isoformat()
        return serialized

    @staticmethod
    def _deserialize_snapshot_event(event):
        if not isinstance(event, dict):
            raise TypeError("event must be an object")
        title = event.get("title")
        label = event.get("label")
        time_label = event.get("time", "")
        kind = event.get("kind")
        color = event.get("color")
        if (
            not isinstance(title, str)
            or len(title) > EVENT_SNAPSHOT_TITLE_MAX_CHARS
            or not isinstance(label, str)
            or len(label) > EVENT_SNAPSHOT_LABEL_MAX_CHARS
            or not isinstance(time_label, str)
            or len(time_label) > EVENT_SNAPSHOT_TIME_MAX_CHARS
            or kind not in {"holiday", "personal"}
            or not isinstance(color, list)
            or len(color) != 3
            or any(
                not isinstance(channel, int) or not 0 <= channel <= 255
                for channel in color
            )
        ):
            raise ValueError("invalid event fields")
        event_date = date.fromisoformat(str(event.get("date")))
        restored = {
            "date": event_date,
            "title": title,
            "label": label,
            "color": tuple(color),
            "kind": kind,
            "time": time_label,
        }
        if event.get("starts_at") is not None:
            starts_at = datetime.fromisoformat(str(event["starts_at"]))
            if starts_at.tzinfo is None:
                raise ValueError("starts_at must include a timezone")
            restored["starts_at"] = starts_at
        return restored

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
            except Exception as exc:
                self._log_calendar_source_failure(source, exc)

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
        if parsed.scheme == "file":
            if parsed.netloc.lower() not in {"", "localhost"}:
                return True
            decoded_path = unquote(parsed.path or "").replace("\\", "/")
            return decoded_path.startswith("//")
        if url.startswith("\\\\") or (not parsed.scheme and parsed.netloc):
            return True
        return not (not parsed.scheme and Path(url).is_absolute())

    def _fetch_holiday_events(self, source, selected_date, tz):
        content = self._read_calendar_source(source["url"])
        cal = icalendar.Calendar.from_ical(content)
        return self._extract_holiday_events(cal, source, selected_date, tz)

    def _read_calendar_source(self, url):
        url = str(url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme == "file":
            path_text = unquote(parsed.path)
            if parsed.netloc and parsed.netloc.lower() != "localhost":
                path_text = f"//{parsed.netloc}{path_text}"
            if os.name == "nt" and path_text.startswith("/") and len(path_text) > 2 and path_text[2] == ":":
                path_text = path_text[1:]
            return Path(path_text).read_bytes()
        if url.startswith("\\\\") or (not parsed.scheme and Path(url).is_absolute()):
            return Path(url).read_bytes()
        session = get_http_session()
        current_url = self._validated_remote_calendar_url(url, redirect=False)
        for redirect_count in range(CALENDAR_SOURCE_MAX_REDIRECTS + 1):
            response = session.get(
                current_url,
                timeout=20,
                headers={"User-Agent": "InkyPi SimpleCalendar/1.0"},
                allow_redirects=False,
            )
            try:
                status_code = int(getattr(response, "status_code", 200))
                if status_code in {301, 302, 303, 307, 308}:
                    location = str(
                        (getattr(response, "headers", {}) or {}).get("Location")
                        or ""
                    ).strip()
                    if not location or redirect_count >= CALENDAR_SOURCE_MAX_REDIRECTS:
                        raise RuntimeError(
                            "Calendar source redirect chain is invalid or too long"
                        )
                    current_url = self._validated_remote_calendar_url(
                        urljoin(current_url, location),
                        redirect=True,
                    )
                    continue
                response.raise_for_status()
                content = bytes(response.content)
                if len(content) > CALENDAR_SOURCE_MAX_BYTES:
                    raise RuntimeError("Calendar source response exceeds its size limit")
                return content
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        raise RuntimeError("Calendar source redirect chain is too long")

    @staticmethod
    def _validated_remote_calendar_url(url, *, redirect):
        parsed = urlparse(str(url or "").strip())
        label = "redirect target" if redirect else "URL"
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError(f"Calendar source {label} has an invalid port") from exc
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
        ):
            raise RuntimeError(
                f"Calendar source {label} must use public HTTPS without credentials"
            )

        hostname = parsed.hostname.rstrip(".").lower()
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
            (".local", ".localhost", ".internal")
        ):
            raise RuntimeError(f"Calendar source {label} cannot target a private host")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise RuntimeError(f"Calendar source {label} cannot target a private host")
        return parsed.geturl()

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

    def _upcoming_event_rows(self, events, selected_date, reference_dt=None, limit=6):
        upcoming = [
            event
            for event in events
            if self._event_is_upcoming(event, selected_date, reference_dt)
        ]
        ordered = sorted(upcoming, key=lambda event: (event["date"], self._same_day_event_sort_key(event)))
        return [{**event, "title": self._event_display_title(event)} for event in ordered[:limit]]

    def _draw_focus_holiday(
        self,
        draw,
        events,
        x,
        y,
        max_width,
        text_color,
        muted_text,
        theme_palette=None,
    ):
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
        lower_rail = (111, 74, 39)
        corner = (217, 177, 90)
        if theme_palette:
            shadow = muted_text
            paper = theme_palette["panel"]
            paper_light = theme_palette["background"]
            border = theme_palette["rule"]
            ink = text_color
            rail = theme_palette["rule"]
            red = theme_palette["accent"]
            red_dark = text_color
            gold = theme_palette["accent"]
            lower_rail = theme_palette["rule"]
            corner = theme_palette["accent"]

        draw.rounded_rectangle([left + 5, top + 5, right + 5, bottom + 5], radius=8, fill=shadow)
        draw.rounded_rectangle([left, top, right, bottom], radius=8, fill=paper, outline=border, width=2)
        draw.rounded_rectangle([left + 4, top + 4, right - 4, bottom - 4], radius=6, outline=paper_light, width=1)
        draw.rectangle([left + 8, top + 8, left + 14, bottom - 8], fill=red)
        draw.line([(left + 20, top + 30), (right - 22, top + 30)], fill=rail, width=1)
        draw.line([(left + 20, bottom - 11), (right - 22, bottom - 11)], fill=lower_rail, width=1)
        draw.polygon([(right - 18, top), (right, top), (right, top + 18)], fill=corner, outline=border)
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

    def _draw_holiday_markers(
        self,
        draw,
        events,
        x,
        y,
        cell_size,
        selected=False,
        selected_color=(255, 255, 255),
    ):
        radius = max(int(cell_size * 0.055), 3)
        gap = radius * 3
        shown = events[:3]
        start_x = x - gap * (len(shown) - 1) / 2
        for index, event in enumerate(shown):
            color = selected_color if selected else event.get("color", (80, 80, 80))
            cx = start_x + gap * index
            draw.ellipse([cx - radius, y - radius, cx + radius, y + radius], fill=color)

    def _draw_holiday_list(self, draw, events, selected_date, upcoming_event_rows, left, top, right, bottom, text_color, muted_text, divider, following_event_rows=None, next_month_event_rows=None):
        if not (upcoming_event_rows or next_month_event_rows) or bottom <= top:
            return

        width = right - left
        line_y = top + 2
        draw.line([(left + int(width * 0.04), line_y), (right - int(width * 0.04), line_y)], fill=divider, width=1)

        date_font_size = max(int(width * 0.032), 14)
        label_font_size = max(int(width * 0.025), 11)
        title_font_size = max(int(width * 0.032), 14)
        date_font = self._get_calendar_ui_font(date_font_size, bold=True)
        label_font = self._get_calendar_ui_font(label_font_size, bold=True)
        title_font = self._get_holiday_title_font(title_font_size)
        row_h = title_font_size + 4
        x0 = left + int(width * 0.055)
        label_x = x0 + max(self._text_width(draw, "12/31", date_font) + 14, width * 0.12)
        label_w = max((self._text_width(draw, event.get("label", ""), label_font) for event in upcoming_event_rows), default=0)
        # Cap unusually long custom labels so the content column remains useful.
        label_w = min(label_w, width * 0.20)
        title_x = label_x + label_w + 10
        split_x = left + width * 0.58
        # Align the second column at the top when titles fit. Only long titles
        # need full width and push the preview into the unused agenda tail.
        preview_start = 0
        for index, event in enumerate(upcoming_event_rows):
            if title_x + self._text_width(draw, event["title"], title_font) > split_x - 12:
                preview_start = index + 1
        preview_slots = max(0, int((bottom - top - 10) // row_h) - preview_start)
        preview_rows = []
        for heading, candidates in (("NEXT", following_event_rows), ("NEXT MONTH", next_month_event_rows)):
            available = preview_slots - len(preview_rows) - 1
            if candidates and available > 0:
                preview_rows.append({"heading": heading})
                preview_rows.extend(candidates[:available])
        if width < 440:
            preview_rows = []
        last_date = None
        for index, event in enumerate(upcoming_event_rows):
            row_right = split_x - 12 if preview_rows and index >= preview_start else right - int(width * 0.04)
            row_y = top + 10 + row_h * index + row_h / 2
            if row_y + row_h / 2 > bottom:
                break
            date_text = f"{event['date'].month}/{event['date'].day}"
            if event["date"] != last_date:
                if last_date is not None:
                    draw.line([(x0, row_y - row_h / 2 - 1), (row_right, row_y - row_h / 2 - 1)], fill=divider, width=1)
                draw.text((x0, row_y), date_text, fill=text_color, font=date_font, anchor="lm")
                last_date = event["date"]
            self._draw_source_label(
                draw,
                self._fit_text(draw, event.get("label", ""), label_font, label_w),
                label_x,
                row_y,
                label_font,
                event.get("color", muted_text),
                separator_color=muted_text,
                anchor="lm",
            )
            title = self._fit_text(draw, event["title"], title_font, right - title_x - int(width * 0.05))
            draw.text(
                (title_x, row_y),
                title,
                fill=text_color,
                font=title_font,
                anchor="lm",
            )
        if preview_rows:
            preview_top = top + 10 + preview_start * row_h
            preview_right = right - int(width * 0.04)
            preview_x = split_x + 12
            draw.line([(split_x, preview_top), (split_x, bottom - 4)], fill=divider, width=1)
            dated_rows = [event for event in preview_rows if "heading" not in event]
            next_date_w = max(self._text_width(draw, f"{e['date'].month}/{e['date'].day}", date_font) for e in dated_rows)
            next_label_x = preview_x + next_date_w + 7
            next_label_w = min(max(self._text_width(draw, e.get("label", ""), label_font) for e in dated_rows), width * 0.08)
            next_title_x = next_label_x + next_label_w + 7
            last_date = None
            for index, event in enumerate(preview_rows):
                row_y = preview_top + row_h * (index + 0.5)
                if "heading" in event:
                    draw.text((preview_x, row_y), event["heading"], font=label_font, fill=muted_text, anchor="lm")
                    last_date = None
                    continue
                if event["date"] != last_date:
                    draw.text((preview_x, row_y), f"{event['date'].month}/{event['date'].day}", font=date_font, fill=text_color, anchor="lm")
                    last_date = event["date"]
                self._draw_source_label(draw, self._fit_text(draw, event.get("label", ""), label_font, next_label_w),
                                        next_label_x, row_y, label_font, event.get("color", muted_text),
                                        separator_color=muted_text, anchor="lm")
                title = self._fit_text(draw, event["title"], title_font, preview_right - next_title_x)
                draw.text((next_title_x, row_y), title, font=title_font, fill=text_color, anchor="lm")

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

    @staticmethod
    def _draw_focus_text(draw, xy, text, font, fill, halo=None, edge=None):
        """Centered focus-panel text; with a halo it gets a surface ring and a thin edge around it."""
        if halo is not None:
            ring = max(int(round(getattr(font, "size", 20) * 0.045)), 2)
            draw.text(xy, text, fill=edge, font=font, anchor="mm", stroke_width=ring + max(ring // 4, 1), stroke_fill=edge)
            draw.text(xy, text, fill=fill, font=font, anchor="mm", stroke_width=ring, stroke_fill=halo)
            return
        draw.text(xy, text, fill=fill, font=font, anchor="mm")

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
            day_events = sorted(
                by_day[event_date],
                key=self._same_day_event_sort_key,
            )
            labels = "/".join(dict.fromkeys(event["label"] for event in day_events if event.get("label")))
            title = " / ".join(dict.fromkeys(self._event_display_title(event) for event in day_events if event.get("title")))
            merged.append({
                "date": event_date,
                "label": labels,
                "title": title,
                "color": day_events[0].get("color", (80, 80, 80)),
            })
        return merged

    @staticmethod
    def _same_day_event_sort_key(event):
        starts_at = event.get("starts_at")
        if isinstance(starts_at, datetime):
            return (
                1,
                starts_at.hour,
                starts_at.minute,
                starts_at.second,
                starts_at.microsecond,
            )
        time_match = re.fullmatch(
            r"([1-9]|1[0-2])(?::([0-5][0-9]))?([ap])",
            str(event.get("time") or "").strip().lower(),
        )
        if time_match is None:
            return (0, 0, 0, 0, 0)
        hour = int(time_match.group(1)) % 12
        if time_match.group(3) == "p":
            hour += 12
        return (1, hour, int(time_match.group(2) or 0), 0, 0)

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

    @classmethod
    def _canonical_theme_palette(cls, theme):
        roles = theme.get("palette") if isinstance(theme, dict) else None
        if not isinstance(roles, dict):
            return None
        fallbacks = {
            "background": (255, 255, 255),
            "panel": (248, 248, 247),
            "ink": (0, 0, 0),
            "muted": (132, 132, 132),
            "rule": (220, 220, 220),
            "accent": (230, 26, 26),
        }
        return {
            role: cls._coerce_palette_color(roles.get(role), fallback)
            for role, fallback in fallbacks.items()
        }

    @classmethod
    def _theme_palette_for_render(cls, theme):
        if not isinstance(theme, dict) or theme.get("mode") != "night":
            return None
        return cls._canonical_theme_palette(theme)

    @staticmethod
    def _coerce_palette_color(value, fallback):
        if isinstance(value, (list, tuple)) and len(value) == 3:
            try:
                channels = tuple(int(channel) for channel in value)
            except (TypeError, ValueError):
                return fallback
            if all(0 <= channel <= 255 for channel in channels):
                return channels
        try:
            return ImageColor.getrgb(str(value))
        except Exception:
            return fallback

    @classmethod
    def _highest_contrast_color(cls, background, first, second):
        return max(
            (first, second),
            key=lambda candidate: cls._contrast_ratio(background, candidate),
        )

    _contrast_ratio = staticmethod(contrast_ratio)

    _relative_luminance = staticmethod(relative_luminance)

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
