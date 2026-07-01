from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import get_font
from utils.http_client import get_http_session
from utils.draw_utils import fit_text as fit_text_to_width
from utils.theme_utils import get_theme_context, get_theme_palette, rgb_to_hex
import base64
import concurrent.futures
from io import BytesIO
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import html
import os
import re
import subprocess
import threading
import time

logger = logging.getLogger(__name__)


SPARKLINE_INK = (6, 78, 59)
LINE_SPARKLINE_AMPLIFICATION = 1.55
LINE_SPARKLINE_EDGE_PADDING = 2.0
SKIP_CACHE_IMAGE_INFO_KEY = "inkypi_skip_cache"
BOLD_SAFE_MIDDLE_DOT = "\u2027"
MIDDLE_DOT_DISPLAY_TRANSLATION = str.maketrans({
    "\u00b7": BOLD_SAFE_MIDDLE_DOT,
    "\u2219": BOLD_SAFE_MIDDLE_DOT,
    "\u0387": BOLD_SAFE_MIDDLE_DOT,
})

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(os.path.dirname(PLUGIN_DIR))
STATIC_FONT_DIR = os.path.join(SRC_DIR, "static", "fonts")
STATIC_YAHEI_FONT_PATH = os.path.join(STATIC_FONT_DIR, "msyh.ttc")
STATIC_YAHEI_BOLD_FONT_PATH = os.path.join(STATIC_FONT_DIR, "msyhbd.ttc")
STATIC_NOTO_SANS_SC_PATH = os.path.join(STATIC_FONT_DIR, "NotoSansSC-VF.ttf")
STEAM_LOGO_PATH = os.path.join(PLUGIN_DIR, "assets", "steam_logo.png")
STEAM_TITLE_WORDMARK_PATH = os.path.join(PLUGIN_DIR, "assets", "steam_charts_title_wordmark.png")
STEAM_PIXEL_KAIJU_PATH = os.path.join(PLUGIN_DIR, "assets", "steam_charts_pixel_kaiju.png")
STEAM_HEADER_BAR_PATH = os.path.join(PLUGIN_DIR, "assets", "steam_header_pixel_bar.png")
STEAM_HEADER_SCENE_PATH = os.path.join(PLUGIN_DIR, "assets", "steam_header_pixel_level.png")
STEAMCHARTS_HOME_URL = "https://steamcharts.com"
STEAMCHARTS_CHART_URL = "https://steamcharts.com/app/{appid}/chart-data.json"
STEAM_STORE_APPDETAILS = "https://store.steampowered.com/api/appdetails"
STEAM_CAPSULE_URL = "https://cdn.akamai.steamstatic.com/steam/apps/{appid}/capsule_sm_120.jpg"
STEAM_CAPSULE_TIMEOUT = 15
STEAM_CAPSULE_CACHE_SIZE = 128
STEAM_STORE_TIMEOUT = 20
STEAM_PRIMARY_GAME_LANGUAGE = "schinese"
STEAM_SECONDARY_GAME_LANGUAGE = "english"
STEAMCHARTS_CHART_TIMEOUT = 30
STEAMCHARTS_REQUESTS_PER_SECOND = 2
SANS_FONT_FAMILIES = (
    "Microsoft YaHei",
    "\u5fae\u8f6f\u96c5\u9ed1",
    "Noto Sans SC",
    "Noto Sans CJK SC",
    "WenQuanYi Micro Hei",
    "Source Han Sans SC",
)
ACCEPTED_SANS_FONT_MATCHES = (
    "microsoft yahei",
    "noto sans sc",
    "noto sans cjk",
    "source han sans",
    "wenquanyi micro hei",
    "dengxian",
    "simhei",
)
SANS_FONT_PATHS = {
    "normal": (
        STATIC_YAHEI_FONT_PATH,
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
        r"C:\Windows\Fonts\Deng.ttf",
        STATIC_NOTO_SANS_SC_PATH,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttf",
    ),
    "bold": (
        STATIC_YAHEI_BOLD_FONT_PATH,
        STATIC_YAHEI_FONT_PATH,
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyhbd.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
        r"C:\Windows\Fonts\Dengb.ttf",
        STATIC_NOTO_SANS_SC_PATH,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttf",
    ),
}

LEGACY_MODE_ALIASES = {
    "top_sellers": "most_played",
}

CHART_MODES = {
    "new_trending": {
        "label": "Trending",
        "source": "steamcharts_trending",
        "table_variant": "trending",
    },
    "most_played": {
        "label": "Most Played",
        "source": "steamcharts_top_games",
        "table_variant": "top_games",
    },
    "top_records": {
        "label": "Top Records",
        "source": "steamcharts_top_records",
        "table_variant": "top_records",
    },
    "live_overview": {
        "label": "Live Overview",
        "source": "combined_live_overview",
        "table_variant": "combined",
    },
}

COMBINED_CHART_GROUPS = (
    {
        "key": "trending",
        "title": "Trending Top 5",
        "subtitle": "24h movers",
        "source": "steamcharts_trending",
        "table_variant": "trending",
    },
    {
        "key": "player_count",
        "title": "Player Count Top 5",
        "subtitle": "live now",
        "source": "steamcharts_top_games",
        "table_variant": "top_games",
    },
)

MAX_ITEMS = 5


class _RateLimiter:
    def __init__(self, requests_per_second):
        self._interval = 1 / requests_per_second
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            if now < self._next_request_at:
                time.sleep(self._next_request_at - now)
                now = self._next_request_at
            self._next_request_at = now + self._interval


steamcharts_rate_limiter = _RateLimiter(STEAMCHARTS_REQUESTS_PER_SECOND)


class SteamCharts(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["chart_modes"] = CHART_MODES
        return template_params

    def generate_image(self, settings, device_config):
        mode = settings.get("mode", "new_trending")
        mode = LEGACY_MODE_ALIASES.get(mode, mode)
        raw_items_count = settings.get("itemsCount", MAX_ITEMS)
        try:
            items_count = int(raw_items_count)
        except (TypeError, ValueError):
            items_count = MAX_ITEMS
        items_count = max(1, min(items_count, MAX_ITEMS))
        show_images = str(settings.get("showImages", "true")).lower() == "true"

        mode_config = CHART_MODES.get(mode)
        if not mode_config:
            raise RuntimeError(f"Unknown chart mode: {mode}")
        if mode_config.get("table_variant") == "combined":
            items_count = MAX_ITEMS

        theme_context = self._resolve_theme_context(settings, device_config)
        theme_colors = self._theme_colors(theme_context)
        updated_at_text = self._format_updated_at(device_config)
        dimensions = self.get_dimensions(device_config)
        table_variant = mode_config["table_variant"]
        games = []
        chart_groups = []

        if table_variant == "combined":
            chart_groups = self._fetch_combined_chart_groups(items_count, show_images)
            self._write_combined_context(mode_config, chart_groups, updated_at_text)
        else:
            games = self._fetch_games(mode_config["source"], items_count)
            self._apply_store_metadata(games, include_images=show_images)
            games = self._prepare_table_games(table_variant, games)
            self._write_charts_context(mode_config, games, updated_at_text)

        template_params = {
            "title": "STEAM CHARTS",
            "subtitle": mode_config["label"],
            "layout_variant": "combined" if table_variant == "combined" else "single",
            "table_variant": table_variant,
            "games": games,
            "chart_groups": chart_groups,
            "show_images": show_images,
            "theme_mode": theme_context.get("mode", "day"),
            "theme_ink": rgb_to_hex(theme_colors["ink"]),
            "theme_paper": rgb_to_hex(theme_colors["paper"]),
            "theme_chart_ink": rgb_to_hex(SPARKLINE_INK),
            "steam_logo_uri": self._steam_logo_data_uri(theme_colors),
            "title_wordmark_uri": self._title_wordmark_data_uri(theme_colors),
            "pixel_kaiju_uri": self._pixel_kaiju_data_uri(),
            "yahei_font_uri": self._font_file_uri("normal"),
            "yahei_bold_font_uri": self._font_file_uri("bold"),
            "updated_at_text": updated_at_text,
            "plugin_settings": settings,
        }

        render_settings = dict(settings)
        render_settings["backgroundOption"] = "color"
        render_settings["backgroundColor"] = rgb_to_hex(theme_colors["paper"])
        render_settings["textColor"] = rgb_to_hex(theme_colors["ink"])
        render_settings["selectedFrame"] = "None"
        for margin_key in ("margin", "topMargin", "bottomMargin", "leftMargin", "rightMargin"):
            render_settings[margin_key] = 0
        template_params["plugin_settings"] = render_settings

        image = None
        html_render_failed = False
        prefer_pil_first = self._prefer_pil_fallback_first(settings)
        if not prefer_pil_first:
            image = self.render_image(
                dimensions, "steam_charts.html", "steam_charts.css", template_params
            )
            if image is not None:
                return image

            html_render_failed = True
            logger.warning("Steam Charts HTML render failed; using PIL fallback renderer.")
        else:
            logger.info("Steam Charts using PIL fallback renderer first on constrained display runtime.")

        if table_variant == "combined":
            fallback_image = self._render_combined_fallback_image(
                dimensions,
                mode_config["label"],
                chart_groups,
                theme_context,
                updated_at_text,
            )
        else:
            fallback_image = self._render_fallback_image(
                dimensions,
                mode_config["label"],
                table_variant,
                games,
                show_images,
                theme_context,
                updated_at_text,
            )

        if html_render_failed:
            fallback_image.info[SKIP_CACHE_IMAGE_INFO_KEY] = True
        return fallback_image

    def _fetch_combined_chart_groups(self, items_count, show_images=True):
        groups = []
        for group_config in COMBINED_CHART_GROUPS:
            games = self._fetch_games(group_config["source"], items_count)
            self._apply_store_metadata(games, include_images=show_images)
            groups.append({
                "key": group_config["key"],
                "title": group_config["title"],
                "subtitle": group_config["subtitle"],
                "source": group_config["source"],
                "table_variant": group_config["table_variant"],
                "games": self._prepare_compact_games(group_config["table_variant"], games),
            })
        SteamCharts._align_compact_metric_font_scales(groups)
        return groups

    @staticmethod
    def _align_compact_metric_font_scales(groups):
        metric_scales = []
        for group in groups:
            for game in group.get("games") or []:
                metric_scales.append(
                    SteamCharts._coerce_font_scale(game.get("metric_font_scale"), 1.0)
                )
        if not metric_scales:
            return groups

        shared_scale = SteamCharts._css_scale(min(metric_scales))
        for group in groups:
            for game in group.get("games") or []:
                game["metric_font_scale"] = shared_scale
        return groups

    @staticmethod
    def _prepare_compact_games(table_variant, games):
        compact_games = []
        for index, game in enumerate(games[:MAX_ITEMS], start=1):
            compact = dict(game)
            compact["rank"] = game.get("rank") or index
            compact["primary_metric"] = SteamCharts._primary_metric(table_variant, game)
            compact["secondary_metric"] = SteamCharts._secondary_text(table_variant, game)
            SteamCharts._apply_display_names(compact)
            SteamCharts._apply_layout_font_scales(compact)
            compact_games.append(compact)
        return compact_games

    @staticmethod
    def _prepare_table_games(table_variant, games):
        prepared = []
        for game in games:
            row = dict(game)
            row["primary_metric"] = SteamCharts._primary_metric(table_variant, game)
            row["secondary_metric"] = SteamCharts._secondary_text(table_variant, game)
            SteamCharts._apply_display_names(row)
            SteamCharts._apply_layout_font_scales(row)
            prepared.append(row)
        return prepared

    @staticmethod
    def _apply_display_names(game):
        game["display_name"] = SteamCharts._display_safe_name(game.get("name", ""))
        game["display_secondary_name"] = SteamCharts._display_safe_name(game.get("secondary_name", ""))
        return game

    @staticmethod
    def _display_safe_name(value):
        return str(value or "").translate(MIDDLE_DOT_DISPLAY_TRANSLATION)

    @staticmethod
    def _apply_layout_font_scales(game):
        game["name_font_scale"] = SteamCharts._css_scale(
            SteamCharts._name_font_scale(
                game.get("name", ""),
                game.get("secondary_name", ""),
            )
        )
        game["metric_font_scale"] = SteamCharts._css_scale(
            SteamCharts._metric_font_scale(
                game.get("primary_metric", "")
            )
        )
        return game

    @staticmethod
    def _name_font_scale(name, secondary_name=""):
        primary_units = SteamCharts._text_visual_units(name)
        secondary_units = SteamCharts._text_visual_units(secondary_name) * 0.65
        units = max(primary_units, secondary_units)
        return SteamCharts._linear_font_scale(
            units,
            full_size_units=7.0,
            min_size_units=34.0,
            floor=0.62,
            ceiling=1.08,
        )

    @staticmethod
    def _metric_font_scale(metric):
        units = SteamCharts._text_visual_units(metric)
        return SteamCharts._linear_font_scale(
            units,
            full_size_units=3.5,
            min_size_units=13.0,
            floor=0.78,
            ceiling=1.08,
        )

    @staticmethod
    def _linear_font_scale(units, full_size_units, min_size_units, floor, ceiling):
        if units <= full_size_units:
            return ceiling
        if units >= min_size_units:
            return floor
        progress = (units - full_size_units) / (min_size_units - full_size_units)
        return ceiling - progress * (ceiling - floor)

    @staticmethod
    def _text_visual_units(value):
        units = 0.0
        for char in str(value or ""):
            codepoint = ord(char)
            if char.isspace():
                units += 0.35
            elif char.isdigit():
                units += 0.55
            elif codepoint < 128:
                units += 0.65 if char.isupper() else 0.58
            elif 0x4E00 <= codepoint <= 0x9FFF:
                units += 1.0
            else:
                units += 0.8
        return units

    @staticmethod
    def _css_scale(value):
        return f"{value:.3f}"

    def _write_combined_context(self, mode_config, chart_groups, updated_at_text):
        label = str(mode_config.get("label") or "Steam Charts").strip()
        context_groups = []
        leaders = []
        for group in chart_groups:
            items = []
            for index, game in enumerate(group.get("games", [])[:MAX_ITEMS], start=1):
                name = game.get("name")
                if len(leaders) < 4 and name:
                    leaders.append(str(name))
                items.append({
                    "rank": game.get("rank") or index,
                    "name": name,
                    "secondary_name": game.get("secondary_name"),
                    "appid": game.get("app_id"),
                    "current_players": game.get("current_players_fmt"),
                    "peak_players": game.get("peak_players_fmt"),
                    "change_24h": game.get("change_24h_fmt"),
                    "primary_metric": game.get("primary_metric"),
                    "secondary_metric": game.get("secondary_metric"),
                })
            context_groups.append({
                "key": group.get("key"),
                "title": group.get("title"),
                "source": group.get("source"),
                "table_variant": group.get("table_variant"),
                "items": items,
            })

        summary = f"Steam Charts {label}"
        if leaders:
            summary += f": {', '.join(leaders)}"

        write_context(
            "steam_charts",
            {
                "kind": "game_chart_overview",
                "source": "Steam Charts",
                "summary": summary[:180],
                "facts": [
                    {"label": "mode", "value": label},
                    {"label": "updated", "value": updated_at_text},
                ],
                "groups": context_groups,
                "table_variant": "combined",
            },
            generated_at=datetime.now(),
            ttl_seconds=2 * 60 * 60,
        )

    def _write_charts_context(self, mode_config, games, updated_at_text):
        label = str(mode_config.get("label") or "Steam Charts").strip()
        table_variant = str(mode_config.get("table_variant") or "").strip()
        items = []
        for index, game in enumerate(games[:MAX_ITEMS], start=1):
            items.append({
                "rank": game.get("rank") or index,
                "name": game.get("name"),
                "secondary_name": game.get("secondary_name"),
                "appid": game.get("app_id"),
                "current_players": game.get("current_players_fmt"),
                "peak_players": game.get("peak_players_fmt"),
                "change_24h": game.get("change_24h_fmt"),
                "peak_time": game.get("peak_time_fmt"),
            })

        leaders = ", ".join(str(item.get("name") or "") for item in items[:3] if item.get("name"))
        summary = f"Steam Charts {label}"
        if leaders:
            summary += f": {leaders}"

        write_context(
            "steam_charts",
            {
                "kind": "game_chart",
                "source": "Steam Charts",
                "summary": summary[:180],
                "facts": [
                    {"label": "mode", "value": label},
                    {"label": "updated", "value": updated_at_text},
                ],
                "items": items,
                "table_variant": table_variant,
            },
            generated_at=datetime.now(),
            ttl_seconds=2 * 60 * 60,
        )

    def _render_fallback_image(self, dimensions, subtitle, table_variant, games, show_images=True, theme_context=None, updated_at_text=""):
        width, height = dimensions
        theme_colors = self._theme_colors(theme_context)
        ink = theme_colors["ink"]
        paper = theme_colors["paper"]
        image = Image.new("RGB", dimensions, paper)
        draw = ImageDraw.Draw(image)

        margin = max(14, int(width * 0.028))
        title_font = self._font(max(28, int(height * 0.092)), "bold")
        subtitle_font = self._font(max(15, int(height * 0.04)), "normal")
        meta_font = self._font(max(12, int(height * 0.032)), "normal")
        rank_font = self._font(max(18, int(height * 0.048)), "bold")
        name_font_size = max(19, int(height * 0.046))
        english_font_size = max(11, int(height * 0.027))
        metric_font_size = max(15, int(height * 0.036))
        small_font_size = max(9, int(height * 0.024))

        title_y = margin
        logo_size = max(44, int(height * 0.1131))
        logo_y = title_y + max(1, int(height * 0.006))
        header_text_x = margin + logo_size + max(8, int(width * 0.012))
        subtitle_y = title_y + int(height * 0.09)
        header_art_width = max(76, int(width * 0.115))



        logo_slot_x = margin + int(width * 0.008)
        wordmark_x = margin + logo_size + max(8, int(width * 0.012))

        wordmark_y = max(0, title_y - int(height * 0.006))
        wordmark_size = (max(220, int(width * 0.39)), max(48, int(height * 0.12)))
        title_wordmark_drawn = self._paste_title_wordmark(
            image,
            wordmark_x,
            wordmark_y,
            wordmark_size,
            theme_colors,
        )
        if title_wordmark_drawn:
            self._paste_steam_logo(
                image,
                logo_slot_x,
                self._centered_logo_y(wordmark_y, wordmark_size, logo_size),
                logo_size,
                theme_colors,
            )
        else:
            self._paste_steam_logo(image, margin, logo_y, logo_size, theme_colors)
            draw.text((header_text_x, title_y), "STEAM CHARTS", fill=ink, font=title_font)
            draw.text((header_text_x, subtitle_y), subtitle.upper(), fill=ink, font=subtitle_font)

        if updated_at_text:
            updated_bbox = draw.textbbox((0, 0), updated_at_text, font=meta_font)
            updated_width = updated_bbox[2] - updated_bbox[0]
            updated_height = updated_bbox[3] - updated_bbox[1]
            updated_pad_x = max(4, int(width * 0.006))
            updated_pad_y = max(2, int(height * 0.004))
            updated_gap = max(8, int(width * 0.012))
            art_left = width - margin - header_art_width
            updated_x = art_left - updated_gap - updated_width - updated_pad_x * 2
            updated_x = max(header_text_x, int(updated_x))
            updated_y = subtitle_y
            draw.rectangle(
                (
                    updated_x,
                    updated_y - updated_pad_y,
                    updated_x + updated_width + updated_pad_x * 2,
                    updated_y + updated_height + updated_pad_y,
                ),
                fill=paper,
            )
            draw.text(
                (updated_x + updated_pad_x, updated_y),
                updated_at_text,
                fill=ink,
                font=meta_font,
            )

        top = subtitle_y + max(22, int(height * 0.066))
        row_gap = max(7, int(height * 0.016))
        row_height = max(44, int((height - top - margin) / max(1, len(games))))
        rank_x = margin
        name_x = margin + int(width * 0.052)
        metric_x = width - margin
        cover_width = int(width * 0.1885) if show_images else 0
        cover_height = max(28, int(cover_width * 3 / 8)) if show_images else 0
        cover_gap = max(40, int(width * 0.056)) if show_images else 0
        title_x = name_x + cover_width + cover_gap
        metric_max_width = max(78, int(width * 0.25))
        name_max_width = max(120, width - title_x - metric_max_width - int(width * 0.035))
        separator_width = max(70, int(width * 0.14))
        separator_line_width = max(1, int(height * 0.0035))

        for index, game in enumerate(games):
            y = top + index * row_height

            rank = str(game.get("rank", index + 1))
            draw.text((rank_x, y + row_height * 0.24), rank, fill=ink, font=rank_font)

            if show_images:
                cover = self._decode_data_image(game.get("image"))
                cover_x = name_x
                cover_y = y + max(3, int((row_height - row_gap - cover_height) / 2))
                if cover:
                    cover = ImageOps.fit(cover.convert("RGB"), (cover_width, cover_height), method=Image.Resampling.LANCZOS)
                    image.paste(cover, (cover_x, cover_y))

            name_scale = self._coerce_font_scale(game.get("name_font_scale", 1))
            metric_scale = self._coerce_font_scale(game.get("metric_font_scale", 1))
            name_text = str(game.get("name", "Unknown"))
            row_name_font = self._font_to_fit(
                draw,
                name_text,
                name_max_width,
                self._scaled_font_size(name_font_size, name_scale, 13),
                13,
                "bold",
            )
            name = self._fit_text(draw, name_text, row_name_font, name_max_width)
            draw.text((title_x, y + row_height * 0.08), name, fill=ink, font=row_name_font)

            english_text = str(game.get("secondary_name", ""))
            row_english_font = self._font_to_fit(
                draw,
                english_text,
                name_max_width,
                self._scaled_font_size(english_font_size, name_scale, 8),
                8,
                "normal",
            )
            english_name = self._fit_text(draw, english_text, row_english_font, name_max_width)
            if english_name:
                draw.text((title_x, y + row_height * 0.43), english_name, fill=ink, font=row_english_font)

            metric = self._primary_metric(table_variant, game)
            row_metric_font = self._font_to_fit(
                draw,
                metric,
                metric_max_width,
                self._scaled_font_size(metric_font_size, metric_scale, 8),
                8,
                "bold",
            )
            metric_width = draw.textlength(metric, font=row_metric_font)
            draw.text((metric_x - metric_width, y + row_height * 0.18), metric, fill=ink, font=row_metric_font)
            secondary = self._secondary_text(table_variant, game)
            if secondary:
                row_small_font = self._font_to_fit(
                    draw,
                    secondary,
                    metric_max_width,
                    self._scaled_font_size(small_font_size, metric_scale, 7),
                    7,
                    "normal",
                )
                secondary_width = draw.textlength(secondary, font=row_small_font)
                draw.text((metric_x - secondary_width, y + row_height * 0.56), secondary, fill=ink, font=row_small_font)

            if index < len(games) - 1:
                separator_y = int(y + row_height - max(4, row_gap * 0.55))
                separator_start_x = name_x
                separator_end_x = min(
                    title_x + separator_width,
                    metric_x - int(width * 0.2),
                )
                draw.line(
                    (
                        separator_start_x,
                        separator_y,
                        separator_end_x,
                        separator_y,
                    ),
                    fill=ink,
                    width=separator_line_width,
                )

        return image

    def _render_combined_fallback_image(self, dimensions, subtitle, chart_groups, theme_context=None, updated_at_text=""):
        width, height = dimensions
        theme_colors = self._theme_colors(theme_context)
        ink = theme_colors["ink"]
        paper = theme_colors["paper"]
        chart_ink = SPARKLINE_INK
        image = Image.new("RGB", dimensions, paper)
        draw = ImageDraw.Draw(image)

        margin = max(14, int(width * 0.026))
        title_font = self._font(max(24, int(height * 0.072)), "bold")
        subtitle_font = self._font(max(13, int(height * 0.032)), "normal")
        meta_font = self._font(max(11, int(height * 0.028)), "normal")
        panel_font = self._font(max(17, int(height * 0.041)), "bold")
        panel_sub_font = self._font(max(11, int(height * 0.027)), "normal")
        rank_font = self._font(max(16, int(height * 0.039)), "bold")
        name_font_size = max(16, int(height * 0.037))
        secondary_font_size = max(9, int(height * 0.023))
        metric_font_size = max(17, int(height * 0.0395))
        small_font_size = max(10, int(height * 0.025))

        title_y = margin
        logo_size = max(36, int(height * 0.0884))
        logo_y = title_y + max(1, int(height * 0.004))
        header_text_x = margin + logo_size + max(8, int(width * 0.012))
        subtitle_y = title_y + int(height * 0.066)

        logo_slot_x = margin + int(width * 0.008)
        wordmark_x = margin + logo_size + max(8, int(width * 0.012))

        wordmark_y = max(0, title_y - int(height * 0.006))
        wordmark_size = (max(210, int(width * 0.36)), max(44, int(height * 0.108)))
        title_wordmark_drawn = self._paste_title_wordmark(
            image,
            wordmark_x,
            wordmark_y,
            wordmark_size,
            theme_colors,
        )
        if title_wordmark_drawn:
            self._paste_steam_logo(
                image,
                logo_slot_x,
                self._centered_logo_y(wordmark_y, wordmark_size, logo_size),
                logo_size,
                theme_colors,
            )
        else:
            self._paste_steam_logo(image, margin, logo_y, logo_size, theme_colors)
            draw.text((header_text_x, title_y), "STEAM CHARTS", fill=ink, font=title_font)
            draw.text((header_text_x, subtitle_y), subtitle.upper(), fill=ink, font=subtitle_font)

        if updated_at_text:
            updated_bbox = draw.textbbox((0, 0), updated_at_text, font=meta_font)
            updated_width = updated_bbox[2] - updated_bbox[0]
            updated_x = width - margin - updated_width
            draw.rectangle(
                (updated_x - 4, subtitle_y - 2, width - margin + 4, subtitle_y + updated_bbox[3] - updated_bbox[1] + 2),
                fill=paper,
            )
            draw.text((updated_x, subtitle_y), updated_at_text, fill=ink, font=meta_font)

        top = subtitle_y + max(22, int(height * 0.055))
        group_count = max(1, len(chart_groups))
        col_gap = max(14, int(width * 0.018))
        col_width = int((width - margin * 2 - col_gap * (group_count - 1)) / group_count)
        line_width = max(1, int(height * 0.003))
        panel_header_height = max(36, int(height * 0.078))
        bottom = height - margin

        for group_index, group in enumerate(chart_groups):
            left = margin + group_index * (col_width + col_gap)
            right = left + col_width
            draw.line((left, top, right, top), fill=ink, width=line_width)
            title = str(group.get("title") or "Chart").upper()
            subtitle_text = str(group.get("subtitle") or "")
            draw.text((left, top + max(5, int(height * 0.01))), title, fill=ink, font=panel_font)
            if subtitle_text:
                sub_width = draw.textlength(subtitle_text, font=panel_sub_font)
                draw.text((right - sub_width, top + max(9, int(height * 0.014))), subtitle_text, fill=ink, font=panel_sub_font)

            games = group.get("games") or []
            rows_top = top + panel_header_height
            row_height = max(38, int((bottom - rows_top) / max(1, len(games))))
            rank_x = left
            cover_width = max(104, int(col_width * 0.31))
            cover_height = max(29, int(cover_width * 3 / 8))
            cover_gap = max(6, int(width * 0.007))
            metric_x = right
            title_x = left + max(22, int(col_width * 0.06))
            if any(game.get("image") for game in games):
                title_x += cover_width + cover_gap
            metric_max_width = max(108, int(col_width * 0.30))
            name_max_width = max(70, metric_x - title_x - metric_max_width - int(col_width * 0.025))

            for row_index, game in enumerate(games):
                y = rows_top + row_index * row_height
                draw.text((rank_x, y + int(row_height * 0.2)), str(game.get("rank") or row_index + 1), fill=ink, font=rank_font)

                cover = self._decode_data_image(game.get("image"))
                if cover:
                    cover_x = left + max(24, int(col_width * 0.075))
                    cover_y = y + max(4, int((row_height - cover_height) / 2))
                    cover = ImageOps.fit(cover.convert("RGB"), (cover_width, cover_height), method=Image.Resampling.LANCZOS)
                    image.paste(cover, (cover_x, cover_y))

                name_scale = self._coerce_font_scale(game.get("name_font_scale", 1))
                metric_scale = self._coerce_font_scale(game.get("metric_font_scale", 1))

                name_text = str(game.get("name") or "Unknown")
                row_name_font = self._font_to_fit(
                    draw,
                    name_text,
                    name_max_width,
                    self._scaled_font_size(name_font_size, name_scale, 14),
                    14,
                    "bold",
                )
                name = self._fit_text(draw, name_text, row_name_font, name_max_width)
                draw.text((title_x, y + int(row_height * 0.05)), name, fill=ink, font=row_name_font)
                secondary_text = str(game.get("secondary_name") or "")
                row_secondary_font = self._font_to_fit(
                    draw,
                    secondary_text,
                    name_max_width,
                    self._scaled_font_size(secondary_font_size, name_scale, 9),
                    9,
                    "normal",
                )
                secondary = self._fit_text(draw, secondary_text, row_secondary_font, name_max_width)
                if secondary:
                    draw.text((title_x, y + int(row_height * 0.54)), secondary, fill=ink, font=row_secondary_font)

                metric = str(game.get("primary_metric") or "--")
                row_metric_font = self._font_to_fit(
                    draw,
                    metric,
                    metric_max_width,
                    self._scaled_font_size(metric_font_size, metric_scale, 12),
                    12,
                    "bold",
                )
                metric_width = draw.textlength(metric, font=row_metric_font)
                draw.text((metric_x - metric_width, y + int(row_height * 0.10)), metric, fill=ink, font=row_metric_font)
                secondary_metric = str(game.get("secondary_metric") or "")
                row_small_font = self._font_to_fit(
                    draw,
                    secondary_metric,
                    metric_max_width,
                    self._scaled_font_size(small_font_size, metric_scale, 9),
                    9,
                    "normal",
                )
                if secondary_metric:
                    secondary_width = draw.textlength(secondary_metric, font=row_small_font)
                    draw.text((metric_x - secondary_width, y + int(row_height * 0.54)), secondary_metric, fill=ink, font=row_small_font)

                sparkline_height = max(7, int(row_height * 0.16))
                sparkline_y_offset = self._compact_sparkline_y_offset(group.get("table_variant"))
                sparkline_bottom_gap = max(3, int(height * 0.006))
                sparkline_y = min(
                    y + int(row_height * 0.74) + sparkline_y_offset,
                    y + row_height - sparkline_height - sparkline_bottom_gap,
                )
                sparkline_svg = game.get("sparkline_svg")
                sparkline_width = max(
                    1,
                    int(
                        metric_max_width
                        * self._compact_sparkline_width_ratio(group.get("table_variant"), sparkline_svg)
                    ),
                )
                self._draw_sparkline_svg(
                    draw,
                    sparkline_svg,
                    (metric_x - sparkline_width, sparkline_y, metric_x, sparkline_y + sparkline_height),
                    chart_ink,
                    max(1, line_width),
                )

                if row_index < len(games) - 1:
                    separator_y = y + row_height - max(4, int(height * 0.008))
                    draw.line((title_x, separator_y, min(title_x + int(col_width * 0.34), metric_x - int(col_width * 0.22)), separator_y), fill=ink, width=line_width)

        return image

    @staticmethod
    def _draw_header_pixel_gradient(target, margin, bottom_y, theme_colors, area_width=None):
        if SteamCharts._paste_header_bar(target, margin, bottom_y, theme_colors, area_width):
            return

        width, height = target.size
        draw = ImageDraw.Draw(target)
        ink = theme_colors["ink"]
        pixel = max(4, int(height * 0.011))
        gap = max(3, int(pixel * 0.85))
        if area_width is None:
            area_width = max(126, int(width * 0.2))
        else:
            area_width = max(1, int(area_width))
        area_height = max(40, int(height * 0.13))
        left = width - margin - area_width
        top = max(margin - pixel, 0)
        right = width - margin
        bottom = min(top + area_height, max(top + pixel, int(bottom_y - pixel * 2)))
        if bottom <= top:
            return

        cols = max(1, (right - left) // (pixel + gap))
        rows = max(1, (bottom - top) // (pixel + gap))
        for row in range(rows):
            for col in range(cols):
                x_ratio = (col + 1) / cols
                y_ratio = 1 - (row / rows)
                density = (x_ratio ** 1.7) * (y_ratio ** 0.9)
                threshold = int(1000 * density)
                value = SteamCharts._stable_pixel_value(row, col)
                if value > threshold:
                    continue
                x = right - (cols - col) * (pixel + gap)
                y = top + row * (pixel + gap)
                if x < left or y < top:
                    continue
                elongate = pixel if SteamCharts._stable_pixel_value(col, row, salt=17) < 360 else 0
                height_boost = pixel if SteamCharts._stable_pixel_value(row, col, salt=29) < 160 else 0
                draw.rectangle(
                    (x, y, min(x + pixel + elongate, right), min(y + pixel + height_boost, bottom)),
                    fill=ink,
                )

    @staticmethod
    def _paste_header_bar(target, margin, bottom_y, theme_colors, area_width=None):
        source = SteamCharts._load_header_bar_source()
        if source is None:
            return False

        width, height = target.size
        if area_width is None:
            area_width = max(126, int(width * 0.2))
        else:
            area_width = max(1, int(area_width))
        area_height = max(40, min(int(height * 0.13), max(1, int(bottom_y - margin))))

        bar = ImageOps.contain(
            source.convert("RGBA"),
            (area_width, area_height),
            method=Image.Resampling.LANCZOS,
        )
        alpha = bar.getchannel("A")
        ink = theme_colors["ink"]
        themed = Image.new("RGBA", bar.size, (ink[0], ink[1], ink[2], 0))
        themed.putalpha(alpha)

        x = width - margin - area_width + (area_width - bar.width) // 2
        y = max(0, margin - max(2, int(height * 0.006))) + (area_height - bar.height) // 2
        target.paste(themed, (x, y), themed)
        return True

    @staticmethod
    def _stable_pixel_value(row, col, salt=0):
        value = (row * 1103515245 + col * 12345 + salt * 2654435761) & 0xFFFFFFFF
        value ^= value >> 16
        return value % 1000

    @staticmethod
    def _format_updated_at(device_config, now=None):
        current = SteamCharts._device_local_datetime(device_config, now)
        return f"\u5237\u65b0\u65f6\u95f4 {current.strftime('%m/%d %H:%M')}"

    @staticmethod
    def _centered_logo_y(wordmark_y, wordmark_size, logo_size):
        wordmark_height = max(1, int(wordmark_size[1]))
        return max(0, int(round(wordmark_y + (wordmark_height - int(logo_size)) / 2)))

    @staticmethod
    def _prefer_pil_fallback_first(settings):
        setting = str((settings or {}).get("preferPilFallback", "")).strip().lower()
        if setting in {"1", "true", "yes", "on"}:
            return True
        if setting in {"0", "false", "no", "off"}:
            return False
        env_value = os.environ.get("INKYPI_STEAM_CHARTS_PIL_FIRST", "").strip().lower()
        if env_value in {"1", "true", "yes", "on"}:
            return True
        if env_value in {"0", "false", "no", "off"}:
            return False
        return False

    @staticmethod
    def _device_local_datetime(device_config, now=None):
        tz = None
        tz_name = None
        if device_config is not None and hasattr(device_config, "get_config"):
            try:
                tz_name = device_config.get_config("timezone", default=None)
            except TypeError:
                tz_name = device_config.get_config("timezone")
        if tz_name:
            try:
                tz = ZoneInfo(str(tz_name))
            except Exception:
                tz = None

        if now is None:
            return datetime.now(tz) if tz else datetime.now().astimezone()
        if now.tzinfo is None:
            return now.replace(tzinfo=tz) if tz else now
        return now.astimezone(tz) if tz else now.astimezone()

    @staticmethod
    def _paste_steam_logo(target, x, y, size, theme_colors):
        icon = SteamCharts._theme_steam_logo(size, theme_colors)
        if icon is None:
            draw = ImageDraw.Draw(target)
            draw.ellipse((x, y, x + size, y + size), fill=theme_colors["ink"])
            return
        target.paste(icon, (x, y), icon)

    @staticmethod
    def _steam_logo_data_uri(theme_colors, size=96):
        icon = SteamCharts._theme_steam_logo(size, theme_colors)
        if icon is None:
            return ""
        buffer = BytesIO()
        icon.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _title_wordmark_data_uri(theme_colors):
        wordmark = SteamCharts._theme_title_wordmark(theme_colors)
        if wordmark is None:
            return ""
        buffer = BytesIO()
        wordmark.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _pixel_kaiju_data_uri():
        try:
            with open(STEAM_PIXEL_KAIJU_PATH, "rb") as fh:
                encoded = base64.b64encode(fh.read()).decode("ascii")
            return f"data:image/png;base64,{encoded}"
        except OSError:
            return ""

    @staticmethod
    def _header_bar_data_uri():
        try:
            with open(STEAM_HEADER_BAR_PATH, "rb") as fh:
                encoded = base64.b64encode(fh.read()).decode("ascii")
            return f"data:image/png;base64,{encoded}"
        except OSError:
            return ""

    @staticmethod
    def _header_scene_data_uri():
        try:
            with open(STEAM_HEADER_SCENE_PATH, "rb") as fh:
                encoded = base64.b64encode(fh.read()).decode("ascii")
            return f"data:image/png;base64,{encoded}"
        except OSError:
            return ""

    @staticmethod
    def _theme_steam_logo(size, theme_colors):
        source = SteamCharts._load_steam_logo_source()
        if source is None:
            return None

        icon = ImageOps.fit(source, (size, size), method=Image.Resampling.LANCZOS)
        themed = Image.new("RGBA", icon.size, (0, 0, 0, 0))
        ink = theme_colors["ink"]
        paper = theme_colors["paper"]
        source_pixels = icon.load()
        target_pixels = themed.load()
        for y in range(size):
            for x in range(size):
                r, g, b, a = source_pixels[x, y]
                if a <= 3:
                    continue
                luma = 0.299 * r + 0.587 * g + 0.114 * b
                color = paper if luma >= 170 else ink
                target_pixels[x, y] = (color[0], color[1], color[2], a)
        return themed

    @staticmethod
    def _paste_title_wordmark(target, x, y, max_size, theme_colors):
        source = SteamCharts._theme_title_wordmark(theme_colors)
        if source is None:
            return False
        wordmark = ImageOps.contain(
            source,
            (max(1, int(max_size[0])), max(1, int(max_size[1]))),
            method=Image.Resampling.LANCZOS,
        )
        target.paste(wordmark, (int(x), int(y)), wordmark)
        return True

    @staticmethod
    def _paste_pixel_kaiju(target, x, y, max_size):
        source = SteamCharts._load_pixel_kaiju_source()
        if source is None:
            return False
        kaiju = ImageOps.contain(
            source,
            (max(1, int(max_size[0])), max(1, int(max_size[1]))),
            method=Image.Resampling.NEAREST,
        )
        target.paste(kaiju, (int(x), int(y)), kaiju)
        return True


    @staticmethod
    def _theme_title_wordmark(theme_colors):
        source = SteamCharts._load_title_wordmark_source()
        if source is None:
            return None

        wordmark = source.copy()
        ink = theme_colors["ink"]
        paper = theme_colors["paper"]
        if sum(ink) <= sum(paper):
            return wordmark

        pixels = wordmark.load()
        width, height = wordmark.size
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                if a <= 0:
                    continue
                luma = 0.299 * r + 0.587 * g + 0.114 * b
                saturation = max(r, g, b) - min(r, g, b)
                if luma < 150 or saturation < 24:
                    pixels[x, y] = (ink[0], ink[1], ink[2], a)
                elif luma < 190:
                    pixels[x, y] = (
                        min(255, int(r * 1.25)),
                        min(255, int(g * 1.25)),
                        min(255, int(b * 1.25)),
                        a,
                    )
        return wordmark

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_pixel_kaiju_source():
        if not os.path.exists(STEAM_PIXEL_KAIJU_PATH):
            return None
        try:
            with Image.open(STEAM_PIXEL_KAIJU_PATH) as kaiju:
                return kaiju.convert("RGBA")
        except Exception:
            logger.warning("Could not load Steam pixel kaiju asset: %s", STEAM_PIXEL_KAIJU_PATH)
            return None

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_header_bar_source():
        if not os.path.exists(STEAM_HEADER_BAR_PATH):
            return None
        try:
            with Image.open(STEAM_HEADER_BAR_PATH) as bar:
                return bar.convert("RGBA")
        except Exception:
            logger.warning("Could not load Steam header bar asset: %s", STEAM_HEADER_BAR_PATH)
            return None

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_steam_logo_source():
        if not os.path.exists(STEAM_LOGO_PATH):
            return None
        try:
            with Image.open(STEAM_LOGO_PATH) as logo:
                return logo.convert("RGBA")
        except Exception:
            logger.warning("Could not load Steam logo asset: %s", STEAM_LOGO_PATH)
            return None

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_title_wordmark_source():
        if not os.path.exists(STEAM_TITLE_WORDMARK_PATH):
            return None
        try:
            with Image.open(STEAM_TITLE_WORDMARK_PATH) as wordmark:
                return wordmark.convert("RGBA")
        except Exception:
            logger.warning("Could not load Steam title wordmark asset: %s", STEAM_TITLE_WORDMARK_PATH)
            return None

    @staticmethod
    def _font_file_uri(weight="normal"):
        for font_path in SteamCharts._preferred_sans_font_paths(weight):
            if not SteamCharts._is_yahei_font_path(font_path):
                continue
            try:
                return Path(font_path).resolve().as_uri()
            except (OSError, ValueError):
                return ""
        return ""

    @staticmethod
    def _is_yahei_font_path(font_path):
        name = os.path.basename(str(font_path or "")).lower()
        return name.startswith("msyh") or "yahei" in name

    @staticmethod
    def _font(size, weight="normal"):
        for font_path in SteamCharts._preferred_sans_font_paths(weight):
            try:
                return SteamCharts._load_sans_font(font_path, size, weight)
            except Exception:
                continue
        return get_font("LXGW WenKai", size, weight) or get_font("Jost", size, weight) or ImageFont.load_default()

    @staticmethod
    def _load_sans_font(font_path, size, weight="normal"):
        font = ImageFont.truetype(font_path, size)
        if hasattr(font, "get_variation_axes") and hasattr(font, "set_variation_by_axes"):
            try:
                target_weight = 780 if weight == "bold" else 430
                values = []
                changed = False
                for axis in font.get_variation_axes():
                    axis_name = axis.get("name", b"")
                    if isinstance(axis_name, bytes):
                        axis_name = axis_name.decode("utf-8", errors="ignore")
                    default = axis.get("default")
                    if "weight" in str(axis_name).lower():
                        minimum = axis.get("minimum", target_weight)
                        maximum = axis.get("maximum", target_weight)
                        values.append(max(minimum, min(maximum, target_weight)))
                        changed = True
                    else:
                        values.append(default)
                if changed:
                    font.set_variation_by_axes(values)
            except Exception:
                pass
        return font

    @staticmethod
    @lru_cache(maxsize=4)
    def _preferred_sans_font_paths(weight="normal"):
        paths = []
        requested_weight = "bold" if weight == "bold" else "normal"
        for path in SANS_FONT_PATHS[requested_weight]:
            if os.path.exists(path):
                paths.append(path)

        try:
            for family in SANS_FONT_FAMILIES:
                output = subprocess.check_output(
                    ["fc-match", "-f", "%{file}\t%{family}", family],
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                ).decode("utf-8", errors="ignore").strip()
                if not output:
                    continue
                font_path, _, matched_family = output.partition("\t")
                if (
                    font_path
                    and os.path.exists(font_path)
                    and font_path not in paths
                    and SteamCharts._is_accepted_sans_match(family, matched_family)
                ):
                    paths.append(font_path)
        except Exception:
            pass

        return tuple(paths)

    @staticmethod
    def _is_accepted_sans_match(requested_family, matched_family):
        requested = str(requested_family or "").lower()
        matched = str(matched_family or "").lower()
        if requested and requested in matched:
            return True
        return any(name in matched for name in ACCEPTED_SANS_FONT_MATCHES)

    @staticmethod
    def _resolve_theme_context(settings, device_config):
        requested = str((settings or {}).get("themeMode") or "auto").strip().lower()
        if requested in {"day", "light", "white"}:
            return {"mode": "day", "palette": get_theme_palette("day")}
        if requested in {"night", "dark", "midnight"}:
            return {"mode": "night", "palette": get_theme_palette("night")}
        return get_theme_context(device_config)

    @staticmethod
    def _theme_colors(theme_context):
        mode = "night" if isinstance(theme_context, dict) and theme_context.get("mode") == "night" else "day"
        if mode == "night":
            return {
                "ink": (255, 255, 255),
                "paper": (0, 0, 0),
            }
        return {
            "ink": (0, 0, 0),
            "paper": (255, 255, 255),
        }

    @staticmethod
    def _decode_data_image(data_uri):
        if not data_uri or not str(data_uri).startswith("data:image/"):
            return None
        try:
            _prefix, encoded = str(data_uri).split(",", 1)
            return Image.open(BytesIO(base64.b64decode(encoded)))
        except Exception:
            return None

    @staticmethod
    def _coerce_font_scale(value, default=1.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _scaled_font_size(base_size, scale, floor):
        return max(floor, int(base_size * scale))

    @staticmethod
    def _font_to_fit(draw, text, max_width, start_size, min_size, weight="normal"):
        text = str(text or "")
        size = max(int(min_size), int(start_size))
        min_size = max(1, int(min_size))
        while size > min_size:
            font = SteamCharts._font(size, weight)
            if draw.textlength(text, font=font) <= max_width:
                return font
            size -= 1
        return SteamCharts._font(min_size, weight)

    @staticmethod
    def _fit_text(draw, text, font, max_width):
        return fit_text_to_width(draw, text, font, max_width)

    @staticmethod
    def _draw_sparkline_svg(draw, sparkline_svg, box, ink, line_width=1):
        svg = str(sparkline_svg or "")
        if not svg:
            return False

        left, top, right, bottom = [int(v) for v in box]
        chart_width = max(1, right - left)
        chart_height = max(1, bottom - top)

        rects = re.findall(
            r'<rect[^>]*\sx="([^"]+)"[^>]*\sy="([^"]+)"[^>]*\swidth="([^"]+)"[^>]*\sheight="([^"]+)"[^>]*/?>',
            svg,
        )
        if rects:
            for x, y, width, height in rects:
                try:
                    x1 = left + (float(x) / 120) * chart_width
                    y1 = top + (float(y) / 30) * chart_height
                    x2 = x1 + max(1, (float(width) / 120) * chart_width)
                    y2 = y1 + max(1, (float(height) / 30) * chart_height)
                except ValueError:
                    continue
                draw.rectangle((int(x1), int(y1), int(x2), int(y2)), fill=ink)
            return True

        match = re.search(r'points="([^"]+)"', svg)
        if not match:
            return False

        points = []
        for pair in match.group(1).split():
            try:
                raw_x, raw_y = pair.split(",", 1)
                x = left + (float(raw_x) / 120) * chart_width
                y = top + (float(raw_y) / 30) * chart_height
            except ValueError:
                continue
            points.append((int(x), int(y)))

        if len(points) < 2:
            return False
        draw.line(points, fill=ink, width=max(1, int(line_width)))
        return True

    @staticmethod
    def _compact_sparkline_width_ratio(table_variant, sparkline_svg=""):
        return 0.64

    @staticmethod
    def _compact_sparkline_y_offset(table_variant):
        return 5 if table_variant == "top_games" else 0

    @staticmethod
    def _primary_metric(table_variant, game):
        if table_variant == "top_records":
            return str(game.get("peak_players_fmt", "--"))
        return str(game.get("current_players_fmt", game.get("peak_players_fmt", "--")))

    @staticmethod
    def _secondary_text(table_variant, game):
        if table_variant == "trending":
            return f"24h {game.get('change_24h_fmt', '--')}"
        if table_variant == "top_games":
            return f"Peak {game.get('peak_players_fmt', '--')}"
        if table_variant == "top_records":
            return str(game.get("peak_time_fmt", "--"))
        return ""

    def _fetch_games(self, source, count):
        """Fetch a homepage section and enrich it with chart data when needed."""
        if source == "steamcharts_trending":
            games = self._scrape_steamcharts_trending(count)
            chart_data = self._fetch_chart_data_batch(
                [g["app_id"] for g in games], sparkline_hours=48, include_change=True
            )
        elif source == "steamcharts_top_games":
            games = self._scrape_steamcharts_top_games(count)
            chart_data = self._fetch_chart_data_batch(
                [g["app_id"] for g in games],
                sparkline_hours=30 * 24,
                sparkline_style="bars",
            )
        elif source == "steamcharts_top_records":
            games = self._scrape_steamcharts_top_records(count)
            chart_data = self._fetch_chart_data_batch(
                [g["app_id"] for g in games], sparkline_hours=48
            )
        else:
            raise RuntimeError(f"Unknown chart source: {source}")

        for game in games:
            app_id = game["app_id"]
            stats = chart_data.get(app_id, {})
            game["sparkline_svg"] = stats.get("sparkline_svg")
            if source == "steamcharts_trending" and "change_24h_fmt" not in game:
                game["change_24h_fmt"] = self._format_change(stats.get("change_24h"))
            if source in {"steamcharts_trending", "steamcharts_top_games"} and "current_players_fmt" not in game:
                game["current_players_fmt"] = self._format_count(
                    stats.get("current_players")
                )

        return games

    def _apply_store_metadata(self, games, include_images=True):
        for game in games:
            app_id = game.get("app_id")
            if app_id is None:
                continue

            primary = self._fetch_store_appdetails(app_id, STEAM_PRIMARY_GAME_LANGUAGE)
            secondary = self._fetch_store_appdetails(app_id, STEAM_SECONDARY_GAME_LANGUAGE)

            localized_name = self._clean_game_name(primary.get("name"))
            english_name = self._clean_game_name(secondary.get("name"))
            fallback_name = self._clean_game_name(game.get("name"))
            display_name = localized_name or english_name or fallback_name or f"应用 {app_id}"
            game["name"] = display_name
            if english_name and english_name != display_name:
                game["secondary_name"] = english_name
            elif fallback_name and fallback_name != display_name:
                game["secondary_name"] = fallback_name
            else:
                game["secondary_name"] = ""

            if not include_images:
                continue

            image_url = (
                primary.get("capsule_image")
                or secondary.get("capsule_image")
                or primary.get("header_image")
                or secondary.get("header_image")
                or STEAM_CAPSULE_URL.format(appid=app_id)
            )
            try:
                game["image"] = self._image_url_to_data_uri(image_url)
            except Exception as e:
                logger.warning(f"Failed to cache cover image for app {app_id}: {e}")
                game["image"] = ""

    def _apply_cached_images(self, games):
        for game in games:
            app_id = game.get("app_id")
            if app_id is None:
                continue
            try:
                game["image"] = self._get_cached_capsule_image(app_id)
            except Exception as e:
                logger.warning(f"Failed to cache capsule image for app {app_id}: {e}")
                # Clear the image field so templates do not leave a remote CDN URL.
                # This prevents Chromium (used by `take_screenshot`) from
                # performing uncontrolled network fetches outside our timeouts
                # and rate limits which can hang or slow rendering.
                game["image"] = ""

    @staticmethod
    @lru_cache(maxsize=STEAM_CAPSULE_CACHE_SIZE)
    def _fetch_store_appdetails(app_id, language):
        appid = str(app_id or "").strip()
        if not appid.isdigit():
            return {}
        try:
            session = get_http_session()
            response = session.get(
                STEAM_STORE_APPDETAILS,
                params={
                    "appids": appid,
                    "l": language,
                },
                timeout=STEAM_STORE_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            entry = payload.get(appid, {}) if isinstance(payload, dict) else {}
            data = entry.get("data") if entry.get("success") else None
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Steam store appdetails unavailable for {appid} ({language}): {e}")
            return {}

    def _fetch_homepage(self, failure_message):
        """Return SteamCharts homepage HTML or raise a descriptive runtime error."""
        try:
            steamcharts_rate_limiter.wait()
            session = get_http_session()
            resp = session.get(STEAMCHARTS_HOME_URL, timeout=15)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.error(f"Failed to fetch SteamCharts homepage: {e}")
            raise RuntimeError(f"{failure_message}: {e}") from e

    @staticmethod
    def _extract_table_rows(page_html, table_id, missing_message):
        """Extract table rows from a specific homepage table id."""
        table_match = re.search(
            rf'<table[^>]*id="{re.escape(table_id)}"[^>]*>.*?</table>',
            page_html,
            re.DOTALL,
        )
        if not table_match:
            raise RuntimeError(missing_message)
        return re.findall(r"<tr[^>]*>.*?</tr>", table_match.group(0), re.DOTALL)

    @staticmethod
    def _extract_app_id(row):
        appid_match = re.search(r"/app/(\d+)", row)
        if not appid_match:
            return None
        return int(appid_match.group(1))

    @staticmethod
    def _clean_cells(row):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        return [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", td)).strip()
            for td in tds
        ]

    def _scrape_steamcharts_trending(self, count):
        """Scrape the Trending section from steamcharts.com homepage."""
        homepage_html = self._fetch_homepage(
            "Unable to fetch Steam trending data. Please try again later."
        )
        rows = self._extract_table_rows(
            homepage_html,
            "trending-recent",
            "Trending section not found on steamcharts.com.",
        )

        games = []
        for row in rows:
            app_id = self._extract_app_id(row)
            if app_id is None:
                continue
            tds_clean = self._clean_cells(row)
            if len(tds_clean) < 4:
                continue

            name = html.unescape(tds_clean[0])
            change_fmt = html.unescape(tds_clean[1])
            players_raw = tds_clean[3]

            try:
                players_int = int(players_raw.replace(",", ""))
                players_fmt = self._format_count(players_int)
            except ValueError:
                players_fmt = "--"

            games.append({
                "rank": len(games) + 1,
                "app_id": app_id,
                "name": name,
                "image": STEAM_CAPSULE_URL.format(appid=app_id),
                "change_24h_fmt": change_fmt,
                "current_players_fmt": players_fmt,
            })
            if len(games) >= count:
                break

        if not games:
            raise RuntimeError("No trending games found on steamcharts.com.")

        return games

    def _scrape_steamcharts_top_games(self, count):
        """Scrape the Top Games By Current Players section from the homepage."""
        homepage_html = self._fetch_homepage(
            "Unable to fetch Steam top games data. Please try again later."
        )
        rows = self._extract_table_rows(
            homepage_html,
            "top-games",
            "Top games section not found on steamcharts.com.",
        )

        games = []
        for row in rows:
            app_id = self._extract_app_id(row)
            if app_id is None:
                continue
            tds_clean = self._clean_cells(row)
            if len(tds_clean) < 6:
                continue

            name = html.unescape(tds_clean[1])
            try:
                players_int = int(tds_clean[2].replace(",", ""))
                players_fmt = self._format_count(players_int)
            except ValueError:
                players_fmt = "--"

            try:
                peak_players_int = int(tds_clean[4].replace(",", ""))
                peak_players_fmt = self._format_count(peak_players_int)
            except ValueError:
                peak_players_fmt = "--"

            games.append({
                "rank": len(games) + 1,
                "app_id": app_id,
                "name": name,
                "image": STEAM_CAPSULE_URL.format(appid=app_id),
                "current_players_fmt": players_fmt,
                "peak_players_fmt": peak_players_fmt,
            })
            if len(games) >= count:
                break

        if not games:
            raise RuntimeError("No top games found on steamcharts.com.")

        return games

    def _scrape_steamcharts_top_records(self, count):
        """Scrape the Top Records section from the homepage."""
        homepage_html = self._fetch_homepage(
            "Unable to fetch Steam top records data. Please try again later."
        )
        rows = self._extract_table_rows(
            homepage_html,
            "toppeaks",
            "Top records section not found on steamcharts.com.",
        )

        games = []
        for row in rows:
            app_id = self._extract_app_id(row)
            if app_id is None:
                continue
            tds_clean = self._clean_cells(row)
            if len(tds_clean) < 4:
                continue

            name = html.unescape(tds_clean[0])
            try:
                peak_players_int = int(tds_clean[1].replace(",", ""))
                peak_players_fmt = self._format_count(peak_players_int)
            except ValueError:
                peak_players_fmt = "--"

            games.append({
                "rank": len(games) + 1,
                "app_id": app_id,
                "name": name,
                "image": STEAM_CAPSULE_URL.format(appid=app_id),
                "peak_players_fmt": peak_players_fmt,
                "peak_time_fmt": self._format_peak_time(tds_clean[2]),
            })
            if len(games) >= count:
                break

        if not games:
            raise RuntimeError("No top records found on steamcharts.com.")

        return games

    def _fetch_chart_data_batch(self, app_ids, sparkline_hours=48, include_change=False, sparkline_style="line"):
        """Fetch chart data for multiple games in parallel with a mode-specific window."""
        results = {}

        def fetch_one(app_id):
            return app_id, self._fetch_chart_stats(app_id, sparkline_hours, include_change, sparkline_style)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_one, aid): aid for aid in app_ids}
            for future in concurrent.futures.as_completed(futures):
                try:
                    aid, stats = future.result()
                    results[aid] = stats
                except Exception as e:
                    aid = futures[future]
                    logger.warning(f"Chart data fetch failed for app {aid}: {e}")
                    results[aid] = {}

        return results

    @staticmethod
    @lru_cache(maxsize=STEAM_CAPSULE_CACHE_SIZE)
    def _get_cached_capsule_image(app_id):
        return SteamCharts._image_url_to_data_uri(STEAM_CAPSULE_URL.format(appid=app_id))

    @staticmethod
    @lru_cache(maxsize=STEAM_CAPSULE_CACHE_SIZE)
    def _image_url_to_data_uri(image_url):
        if not image_url:
            return ""
        session = get_http_session()
        resp = session.get(image_url, timeout=STEAM_CAPSULE_TIMEOUT)
        resp.raise_for_status()
        encoded_image = base64.b64encode(resp.content).decode("ascii")
        return f"data:image/jpeg;base64,{encoded_image}"

    @staticmethod
    def _clean_game_name(name):
        return " ".join(str(name or "").split())

    def _fetch_chart_stats(self, app_id, sparkline_hours=48, include_change=True, sparkline_style="line"):
        """Fetch chart data and compute a sparkline window plus optional 24h change."""
        try:
            url = STEAMCHARTS_CHART_URL.format(appid=app_id)
            steamcharts_rate_limiter.wait()
            # Use a per-call session to avoid sharing a requests.Session() across threads.
            # Shared sessions from `get_http_session()` are a global singleton and
            # may not be safe to reuse concurrently from multiple worker threads.
            # To retain retry and connection-pool characteristics, configure the
            # per-call session with the same HTTPAdapter settings used by
            # `get_http_session()` so transient failures are retried.
            with requests.Session() as session:
                session.headers.update({
                    'User-Agent': 'InkyPi/1.0 (https://github.com/fatihak/InkyPi/)'
                })
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=10,
                    pool_maxsize=10,
                    max_retries=3,
                    pool_block=False,
                )
                session.mount('http://', adapter)
                session.mount('https://', adapter)
                resp = session.get(url, timeout=STEAMCHARTS_CHART_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"Failed chart data for app {app_id}: {e}")
            return {}

        if not data:
            return {}

        # Anchor calculations to the newest datapoint timestamp instead of wall-clock
        # time. SteamCharts data timestamps can lag behind wall-clock time which
        # would skew the sparkline window and 24h comparison.
        latest_ts = data[-1][0]
        cutoff_window_ms = latest_ts - sparkline_hours * 3600 * 1000

        recent_window = [p for p in data if p[0] >= cutoff_window_ms]

        current_players = recent_window[-1][1] if recent_window else data[-1][1]

        change_24h = None
        if include_change and len(data) >= 2:
            cutoff_24h_ms = latest_ts - 24 * 3600 * 1000
            target_24h = min(data, key=lambda p: abs(p[0] - cutoff_24h_ms))
            if target_24h[1] > 0:
                change_24h = ((current_players - target_24h[1]) / target_24h[1]) * 100

        sparkline_svg = self._generate_sparkline_svg(recent_window, chart_style=sparkline_style)

        return {
            "current_players": current_players,
            "change_24h": change_24h,
            "sparkline_svg": sparkline_svg,
        }

    @staticmethod
    def _generate_sparkline_svg(data_points, width=120, height=30, chart_style="line"):
        """
        Generate inline SVG content from [[timestamp_ms, count]] pairs.
        `line` is used for 48-hour movement; `bars` mirrors SteamCharts' 30-day volume strips.
        """
        if not data_points or len(data_points) < 2:
            return None

        if chart_style == "bars":
            return SteamCharts._generate_bar_sparkline_svg(data_points, width, height)

        data_points = SteamCharts._downsample_chart_points(data_points, target_points=24)
        counts = [p[1] for p in data_points]

        if len(counts) > 5:
            smoothed = []
            for i in range(len(counts)):
                window = counts[max(0, i - 2):min(len(counts), i + 3)]
                smoothed.append(sum(window) / len(window))
            counts = smoothed

        min_c, max_c = min(counts), max(counts)
        if max_c == min_c or (max_c - min_c) < (max_c * 0.001):
            y = height / 2
            return f'<polyline points="0,{y} {width},{y}" />'

        range_c = max_c - min_c
        margin = range_c * 0.15
        plot_min = min_c - margin
        plot_max = max_c + margin
        plot_range = plot_max - plot_min

        points = []
        for i, c in enumerate(counts):
            x = (i / (len(counts) - 1)) * width
            y = (height - 2) - ((c - plot_min) / plot_range) * (height - 4) + 1
            y = SteamCharts._amplify_sparkline_y(y, height)
            points.append(f"{x:.1f},{y:.1f}")

        return '<polyline points="{}" />'.format(" ".join(points))

    @staticmethod
    def _amplify_sparkline_y(y, height):
        center = height / 2
        amplified = center + (y - center) * LINE_SPARKLINE_AMPLIFICATION
        lower = LINE_SPARKLINE_EDGE_PADDING
        upper = height - LINE_SPARKLINE_EDGE_PADDING
        return min(upper, max(lower, amplified))

    @staticmethod
    def _generate_bar_sparkline_svg(data_points, width=120, height=30):
        data_points = SteamCharts._downsample_chart_points(data_points, target_points=30)
        counts = [p[1] for p in data_points]
        if not counts:
            return None

        min_c, max_c = min(counts), max(counts)
        usable_height = max(1, height - 2)
        step = width / len(counts)
        bar_width = max(1.0, step * 0.72)
        rects = []

        for i, count in enumerate(counts):
            if max_c == min_c or (max_c - min_c) < (max_c * 0.001):
                ratio = 0.58
            else:
                ratio = (count - min_c) / (max_c - min_c)
                ratio = 0.18 + ratio * 0.82
            bar_height = max(1.0, ratio * usable_height)
            x = i * step + (step - bar_width) / 2
            y = height - 1 - bar_height
            rects.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" />'
            )

        return "".join(rects)

    @staticmethod
    def _downsample_chart_points(data_points, target_points):
        if len(data_points) <= target_points:
            return data_points
        indices = [
            int(i * (len(data_points) - 1) / (target_points - 1))
            for i in range(target_points)
        ]
        return [data_points[i] for i in indices]

    @staticmethod
    def _format_count(count):
        """Format player count with thousands separator."""
        if count is None:
            return "--"
        return f"{count:,}"

    @staticmethod
    def _format_change(change):
        """Format 24h change as signed percentage."""
        if change is None:
            return "--"
        sign = "+" if change >= 0 else ""
        return f"{sign}{change:.1f}%"

    @staticmethod
    def _format_peak_time(raw_value):
        """Format SteamCharts Top Records timestamps like 'Aug 2024'."""
        if not raw_value:
            return "--"
        try:
            return datetime.strptime(raw_value, "%Y-%m-%dT%H:%M:%SZ").strftime("%b %Y")
        except ValueError:
            return raw_value
