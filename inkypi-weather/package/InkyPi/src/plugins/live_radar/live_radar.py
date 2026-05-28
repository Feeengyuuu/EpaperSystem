from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from functools import lru_cache
from pathlib import Path
from typing import Any
import hashlib
import json
import logging
import math
import os
import re
import time
import unicodedata

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import get_font
from utils.http_client import get_http_session
from utils.theme_utils import get_theme_context

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://liveradar.pages.dev/api/status/batch"
DEFAULT_ROOMS_TEXT = "\n".join(
    [
        "douyu|6979222|fav",
        "bilibili|545318|fav",
        "twitch|xqc",
        "douyu|60937|fav",
        "twitch|shroud",
        "bilibili|22747736",
        "douyu|12306|fav",
        "twitch|hasanabi",
        "douyu|1126960",
        "douyu|52",
        "douyu|2561707",
        "douyu|57321|fav",
        "douyu|522387",
        "bilibili|7720242",
        "bilibili|1992214",
        "bilibili|6655",
        "bilibili|931522",
        "bilibili|13140424",
        "bilibili|956152",
        "bilibili|473",
        "bilibili|22915949",
        "bilibili|5017134",
        "bilibili|866360",
        "bilibili|11623469",
        "bilibili|23447617",
        "bilibili|139",
        "bilibili|7586498",
        "bilibili|5229|fav",
        "bilibili|25018616",
        "bilibili|21198073",
        "douyu|24422",
        "bilibili|2366002",
        "twitch|ishowspeed",
        "twitch|j_blow",
        "twitch|asmongold",
        "twitch|pokimane",
        "twitch|jinnytty|fav",
        "bilibili|17526",
        "bilibili|5460313",
        "douyu|3935426",
        "douyu|93589",
        "douyu|10639765|fav",
        "douyu|252140",
        "bilibili|24065",
        "bilibili|4417875",
        "douyu|3507497|fav",
        "douyu|110",
        "bilibili|682048",
        "twitch|lululuvely",
        "twitch|emiru",
        "twitch|berticuss",
        "douyu|71415",
        "douyu|48699",
        "douyu|9999",
        "douyu|7718843",
        "douyu|456302",
        "bilibili|733|fav",
        "bilibili|6",
        "bilibili|20984",
        "bilibili|23668205",
        "douyu|8682569",
        "bilibili|30931147",
        "bilibili|382436",
        "twitch|jie_220",
    ]
)
CACHE_SCHEMA_VERSION = "live-radar-card-wall-v1"
BATCH_LIMIT = 10
COVER_MAX_BYTES = 5 * 1024 * 1024
COVER_MAX_SIZE = (960, 540)
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_MAX_SIZE = (256, 256)
AVATAR_CACHE_SECONDS = 24 * 3600
COVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://liveradar.pages.dev/",
}

PLATFORMS = {
    "douyu": {"label": "DOUYU", "short": "DY"},
    "bilibili": {"label": "BILIBILI", "short": "B"},
    "twitch": {"label": "TWITCH", "short": "TW"},
    "kick": {"label": "KICK", "short": "K"},
    "picarto": {"label": "PICARTO", "short": "PA"},
    "soop": {"label": "SOOP", "short": "SO"},
}

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
TITLE_LOGO_FILE = "liveradar_logo.png"
SANS_FONT_PATHS = {
    "normal": (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        os.path.join(PLUGIN_DIR, "fonts", "NotoSansSC-VF.ttf"),
    ),
    "bold": (
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyhbd.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        os.path.join(PLUGIN_DIR, "fonts", "NotoSansSC-VF.ttf"),
    ),
}

STATUS_RANK = {
    "live": 0,
    "replay": 1,
    "offline": 2,
    "error": 3,
}

FAVORITE_PRIORITY = {
    ("douyu", "60937"): 0,  # Zard1991 stays first inside the favorites group.
    ("douyu", "6979222"): 1,
    ("bilibili", "545318"): 2,
    ("douyu", "12306"): 3,
    ("douyu", "57321"): 4,
    ("bilibili", "5229"): 5,
    ("twitch", "jinnytty"): 6,
    ("douyu", "10639765"): 7,
    ("douyu", "3507497"): 8,
    ("bilibili", "733"): 9,
}


class LiveRadar(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["style_settings"] = False
        return template_params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        rooms = self._parse_rooms(settings)
        if not rooms:
            rooms = self._parse_rooms({"roomsText": DEFAULT_ROOMS_TEXT})
        if not rooms:
            raise RuntimeError("LiveRadar needs at least one streamer card.")

        api_url = str(settings.get("apiUrl") or DEFAULT_API_URL).strip() or DEFAULT_API_URL
        cache_seconds = self._int_setting(settings, "cacheSeconds", 60, 20, 3600)
        timeout = self._int_setting(settings, "timeoutSeconds", 20, 5, 45)
        fetch_avatars = self._bool_setting(settings.get("fetchAvatars"), True)
        force_refresh = self._bool_setting(settings.get("forceRefresh"), False)
        show_snapshots = self._bool_setting(settings.get("showSnapshots"), True)
        snapshot_cache_seconds = self._int_setting(settings, "snapshotCacheSeconds", cache_seconds, 30, 1800)
        avatar_cache_seconds = self._int_setting(settings, "avatarCacheSeconds", AVATAR_CACHE_SECONDS, 300, 7 * 24 * 3600)

        cache_key = self._cache_key(rooms, api_url, fetch_avatars)
        cache_entry = self._read_cache(cache_key)
        now_ts = time.time()
        warning = ""
        from_cache = False

        if (
            not force_refresh
            and cache_entry
            and now_ts - float(cache_entry.get("fetched_at") or 0) < cache_seconds
            and isinstance(cache_entry.get("results"), list)
        ):
            results = cache_entry["results"]
            from_cache = True
        else:
            try:
                results = self._fetch_statuses(rooms, api_url, timeout, fetch_avatars)
                self._write_cache(cache_key, {"fetched_at": now_ts, "results": results})
            except Exception as exc:
                logger.warning("LiveRadar status fetch failed: %s", exc)
                if cache_entry and isinstance(cache_entry.get("results"), list):
                    results = cache_entry["results"]
                    from_cache = True
                    warning = "STALE CACHE"
                else:
                    results = [
                        {
                            "ok": False,
                            "platform": room["platform"],
                            "id": room["id"],
                            "error": str(exc),
                            "status": self._default_status(room, is_error=True),
                        }
                        for room in rooms
                    ]
                    warning = "FETCH ERROR"

        cards = self._merge_results(rooms, results)
        generated_at = datetime.now(timezone.utc)
        self._write_context(cards, generated_at, cache_seconds, from_cache, warning)
        theme = self._theme(settings, device_config)
        layout = {
            "max_live_cards": self._int_setting(settings, "maxLiveCards", 3, 1, 3),
            "max_offline_cards": self._int_setting(settings, "maxOfflineCards", 3, 1, 5),
            "show_snapshots": show_snapshots,
            "snapshot_cache_seconds": snapshot_cache_seconds,
            "avatar_cache_seconds": avatar_cache_seconds,
        }
        return self._render_dashboard(cards, dimensions, theme, generated_at, from_cache, warning, layout)

    def _fetch_statuses(self, rooms, api_url, timeout, fetch_avatars):
        session = get_http_session()
        all_results = []
        for start in range(0, len(rooms), BATCH_LIMIT):
            chunk = rooms[start : start + BATCH_LIMIT]
            try:
                all_results.extend(self._post_status_chunk(session, chunk, api_url, timeout, fetch_avatars))
            except Exception as exc:
                logger.warning("LiveRadar batch fetch failed; retrying individually: %s", exc)
                for room in chunk:
                    try:
                        all_results.extend(self._post_status_chunk(session, [room], api_url, timeout, fetch_avatars))
                    except Exception as room_exc:
                        logger.warning("LiveRadar room fetch failed for %s/%s: %s", room["platform"], room["id"], room_exc)
                        all_results.append(
                            {
                                "ok": False,
                                "platform": room["platform"],
                                "id": room["id"],
                                "error": str(room_exc),
                                "status": self._default_status(room, is_error=True),
                            }
                        )
        return all_results

    def _post_status_chunk(self, session, rooms, api_url, timeout, fetch_avatars):
        payload = {
            "rooms": [
                {
                    "platform": room["platform"],
                    "id": room["id"],
                    "fetchAvatar": bool(fetch_avatars),
                }
                for room in rooms
            ]
        }
        response = session.post(
            api_url,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data.get("results"), list):
            return data["results"]
        if data.get("status"):
            return [data]
        raise RuntimeError("LiveRadar API returned no status results.")

    def _merge_results(self, rooms, results):
        cards = []
        for index, room in enumerate(rooms):
            result = results[index] if index < len(results) and isinstance(results[index], dict) else {}
            status = result.get("status") if isinstance(result.get("status"), dict) else {}
            if not result.get("ok", True):
                status = {**self._default_status(room, is_error=True), **status}
            else:
                status = {**self._default_status(room), **status}

            status["platform"] = str(status.get("platform") or room["platform"]).lower()
            status["id"] = str(status.get("id") or room["id"])
            owner = self._clean_text(status.get("owner") or room.get("label") or room["id"])
            title = self._clean_text(status.get("title") or "")

            card = {
                "platform": room["platform"],
                "id": room["id"],
                "label": self._clean_text(room.get("label") or owner or room["id"]),
                "is_fav": bool(room.get("isFav")),
                "owner": owner or self._clean_text(room.get("label") or room["id"]),
                "title": title,
                "heat": self._safe_int(status.get("heatValue"), 0),
                "start_time": status.get("startTime"),
                "cover": status.get("cover") or "",
                "avatar": status.get("avatar") or "",
                "is_error": bool(status.get("isError")),
                "status": self._status_kind(status),
                "favorite_rank": self._favorite_priority(room["platform"], room["id"]),
                "raw": status,
            }
            cards.append(card)
        return cards

    def _render_dashboard(self, cards, dimensions, theme, generated_at, from_cache, warning, layout=None):
        width, height = dimensions
        image = Image.new("RGB", dimensions, theme["bg"])
        draw = ImageDraw.Draw(image)
        layout = layout or {}

        margin = max(14, int(width * 0.02))
        title_font = self._font(max(30, int(height * 0.073)), "bold")
        sub_font = self._font(max(12, int(height * 0.031)), "bold")
        stat_font = self._font(max(14, int(height * 0.035)), "bold")
        small_font = self._font(max(11, int(height * 0.026)), "bold")

        live_cards = self._sort_cards([card for card in cards if card["status"] == "live"])
        replay_cards = self._sort_cards([card for card in cards if card["status"] == "replay"])
        offline_cards = self._sort_cards([card for card in cards if card["status"] == "offline"])
        error_cards = self._sort_cards([card for card in cards if card["status"] == "error"])

        logo_size = max(34, int(height * 0.09))
        title_x = margin
        if self._paste_title_logo(image, margin, 16, logo_size, theme):
            title_x = margin + logo_size + 10
        draw.text((title_x, 13), "LiveRadar", fill=theme["ink"], font=title_font)
        draw.text((title_x + 3, 51), "STREAM CARD WALL", fill=theme["muted"], font=sub_font)
        self._draw_status_totals(
            draw,
            width - margin,
            16,
            [
                ("LIVE", len(live_cards), "live"),
                ("REPLAY", len(replay_cards), "replay"),
                ("OFF", len(offline_cards), "offline"),
            ],
            stat_font,
            theme,
        )
        draw.line((margin, 73, width - margin, 73), fill=theme["line"], width=2)

        max_live = max(1, min(3, int(layout.get("max_live_cards") or 3)))
        max_offline = max(1, min(5, int(layout.get("max_offline_cards") or 3)))
        show_snapshots = bool(layout.get("show_snapshots", True))
        snapshot_cache_seconds = max(30, int(layout.get("snapshot_cache_seconds") or 90))
        avatar_cache_seconds = max(300, int(layout.get("avatar_cache_seconds") or AVATAR_CACHE_SECONDS))

        live_limit = min(max_live, max(1, len(live_cards))) if live_cards else 0
        top_label_y = 84
        top_y = 108
        top_h = 196 if show_snapshots else 158
        bottom_y = 316 if show_snapshots else 292
        footer_y = height - 27
        col_gap = max(12, int(width * 0.018))
        col_w = int((width - 2 * margin - col_gap) / 2)
        live_queue_box = (margin, bottom_y, col_w, footer_y - bottom_y - 8)
        offline_box = (margin + col_w + col_gap, bottom_y, col_w, footer_y - bottom_y - 8)
        live_queue_max = 8
        live_queue_count = self._live_queue_visible_count(live_queue_box, len(live_cards[live_limit:]), live_queue_max)

        if live_cards:
            self._draw_section_title(draw, margin, top_label_y, "LIVE NOW", len(live_cards), theme, sub_font)
            live_visible = live_cards[:live_limit]
            gap = max(8, int(width * 0.012))
            card_w = int((width - 2 * margin - gap * (len(live_visible) - 1)) / len(live_visible))
            for i, card in enumerate(live_visible):
                x = margin + i * (card_w + gap)
                self._draw_card(
                    image,
                    draw,
                    (x, top_y, card_w, top_h),
                    card,
                    theme,
                    large=True,
                    show_snapshot=show_snapshots,
                    snapshot_cache_seconds=snapshot_cache_seconds,
                )
            overflow_cards = self._top_live_overflow_cards(live_cards, len(live_visible), live_queue_count)
            if overflow_cards:
                section_right = (
                    margin
                    + draw.textlength("LIVE NOW", font=sub_font)
                    + draw.textlength(str(len(live_cards)), font=sub_font)
                    + 36
                )
                more_width = max(80, width - margin - section_right)
                more = self._live_overflow_text(overflow_cards, draw, sub_font, more_width)
                self._draw_text_right(draw, more, width - margin, top_label_y, sub_font, theme["muted"])
        else:
            self._draw_quiet_panel(draw, (margin, top_y, width - 2 * margin, top_h), len(cards), theme)

        self._draw_live_queue_section(
            image,
            draw,
            live_queue_box,
            "LIVE TOO",
            live_cards[live_limit:],
            theme,
            max_items=live_queue_max,
            avatar_cache_seconds=avatar_cache_seconds,
        )
        self._draw_compact_section(
            image,
            draw,
            offline_box,
            "OFFLINE",
            offline_cards + error_cards,
            theme,
            max_items=max_offline,
            show_snapshot=False,
            snapshot_cache_seconds=snapshot_cache_seconds,
            avatar_cache_seconds=avatar_cache_seconds,
        )

        footer_parts = [generated_at.astimezone().strftime("%H:%M")]
        if from_cache:
            footer_parts.append("cache")
        if warning:
            footer_parts.append(warning.lower())
        footer = " / ".join(footer_parts)
        draw.line((margin, footer_y - 5, width - margin, footer_y - 5), fill=theme["line"], width=1)
        draw.text((margin, footer_y), "FOR LEARNING & RESEARCH ONLY", fill=theme["muted"], font=small_font)
        self._draw_text_right(draw, footer, width - margin, footer_y, small_font, theme["muted"])
        return image

    def _draw_card(self, image, draw, box, card, theme, large, show_snapshot=True, snapshot_cache_seconds=90):
        x, y, w, h = box
        status = card["status"]
        fill, ink, muted, line = self._card_palette(status, theme)
        self._rounded_rectangle(draw, (x, y, x + w, y + h), radius=8, fill=fill, outline=line, width=2)

        accent_w = 6 if large else 4
        self._rounded_rectangle(draw, (x, y, x + accent_w, y + h), radius=4, fill=ink, outline=ink, width=0)
        snapshot_h = 0
        if show_snapshot and large and status == "live":
            snapshot_h = self._draw_snapshot_header(
                image,
                draw,
                (x + accent_w + 1, y + 2, w - accent_w - 3, h),
                card,
                theme,
                large,
                snapshot_cache_seconds,
            )

        pill_font = self._font(10 if large else 9, "bold")
        body_font = self._font(20 if large else 14, "bold")
        title_font = self._font(13 if large else 10)
        meta_font = self._font(10 if large else 9, "bold")

        pad = 14 if large else 10
        if not large:
            platform_w = 30
            pill_y = y + (5 if snapshot_h else 8)
            self._draw_platform_badge(
                draw,
                (x + pad, pill_y, x + pad + platform_w, pill_y + 19),
                card["platform"],
                fill=ink,
                ink=fill,
                outline=ink,
            )
            status_label = self._status_label(status)
            status_w = 39 if status != "replay" else 52
            self._draw_pill(
                draw,
                (x + w - pad - status_w, pill_y, x + w - pad, pill_y + 19),
                status_label,
                pill_font,
                fill=fill,
                ink=ink,
                outline=ink,
            )
            if snapshot_h:
                text_x = x + pad
                text_w = max(20, w - 2 * pad)
                text_y = y + snapshot_h + 6
            else:
                text_x = x + pad + platform_w + 8
                text_w = max(20, w - (text_x - x) - status_w - pad - 8)
                text_y = y + 7
            owner_text = self._card_display_name(card)
            body_font = self._fit_font(draw, owner_text, text_w, 13 if snapshot_h else 14, 9, "bold")
            owner = self._fit_text(draw, owner_text, body_font, text_w)
            draw.text((text_x, text_y), owner, fill=ink, font=body_font)
            detail_y = text_y + self._line_height(body_font) + 1
            if detail_y + self._line_height(title_font) <= y + h - 3:
                detail = self._fit_text(draw, card["title"] or self._meta_text(card), title_font, w - 2 * pad)
                draw.text((x + pad, detail_y), detail, fill=muted, font=title_font)
            return

        pill_y = y + 10
        self._draw_platform_badge(
            draw,
            (x + pad, pill_y, x + pad + 42, pill_y + 23),
            card["platform"],
            fill=ink,
            ink=fill,
            outline=ink,
        )
        icon_right = x + w - pad
        icon_y = pill_y - 1
        if status == "live":
            icon_right = self._draw_icon_badge(
                draw,
                (icon_right - 23, icon_y, icon_right, icon_y + 23),
                "live",
                fill=fill,
                ink=ink,
                outline=ink,
            ) - 6
        if card.get("is_fav"):
            self._draw_icon_badge(
                draw,
                (icon_right - 23, icon_y, icon_right, icon_y + 23),
                "fav",
                fill=fill,
                ink=ink,
                outline=muted,
            )

        text_w = max(20, w - 2 * pad)
        text_top = y + (snapshot_h + 7 if snapshot_h else 42)
        text_bottom = y + h - pad
        owner_text = self._card_display_name(card)
        body_font = self._fit_font(draw, owner_text, text_w, 16 if snapshot_h else 22, 10, "bold")
        owner = self._fit_text(draw, owner_text, body_font, text_w)
        draw.text((x + pad, text_top), owner, fill=ink, font=body_font)

        meta_text = self._meta_text(card)
        meta_font = self._fit_font(draw, meta_text, text_w, 8 if snapshot_h else 11, 7, "bold")
        meta_h = self._line_height(meta_font)
        meta_y = max(text_top + self._line_height(body_font) + 2, text_bottom - meta_h)
        meta = self._fit_text(draw, meta_text, meta_font, text_w)

        title = card["title"] or self._offline_title(card)
        title_y = text_top + self._line_height(body_font) + (2 if snapshot_h else 5)
        title_space = max(0, meta_y - title_y - 2)
        max_lines = 1 if snapshot_h else 2
        title_font, title_lines = self._fit_wrapped_text(
            draw,
            title,
            text_w,
            title_space,
            max_lines,
            10 if snapshot_h else 14,
            7,
        )
        line_h = self._line_height(title_font) + 1
        for line in title_lines:
            draw.text((x + pad, title_y), line, fill=muted, font=title_font)
            title_y += line_h

        draw.text((x + pad, meta_y), meta, fill=ink, font=meta_font)

    def _draw_snapshot_header(self, image, draw, area, card, theme, large, cache_seconds):
        x, y, w, h = area
        if w <= 8 or h <= 36:
            return 0

        if large:
            snapshot_h = min(max(104, int(h * 0.68)), max(1, h - 58))
        else:
            snapshot_h = min(max(20, int(h * 0.38)), max(1, h - 28))
        if snapshot_h <= 0:
            return 0

        left = int(x)
        top = int(y)
        right = int(x + w)
        bottom = int(y + snapshot_h)
        if right <= left or bottom <= top:
            return 0

        size = (right - left, bottom - top)
        fill, _ink, _muted, _line = self._card_palette(card["status"], theme)
        snapshot = self._load_cover_source(card.get("cover"), cache_seconds)
        if snapshot:
            try:
                draw.rectangle((left, top, right, bottom), fill=fill)
                snapshot = ImageOps.contain(snapshot, size, method=self._resampling_filter())
                snapshot = ImageOps.autocontrast(ImageOps.grayscale(snapshot)).convert("RGB")
                paste_x = left + (size[0] - snapshot.width) // 2
                paste_y = top + (size[1] - snapshot.height) // 2
                image.paste(snapshot, (paste_x, paste_y))
            except Exception as exc:
                logger.warning("LiveRadar cover render failed for %s/%s: %s", card.get("platform"), card.get("id"), exc)
                self._draw_snapshot_placeholder(draw, (left, top, right, bottom), card, theme, large)
        else:
            self._draw_snapshot_placeholder(draw, (left, top, right, bottom), card, theme, large)

        draw.line((left, bottom, right, bottom), fill=theme["line"], width=1)
        return snapshot_h

    def _draw_snapshot_placeholder(self, draw, box, card, theme, large):
        left, top, right, bottom = box
        if theme.get("mode") == "light":
            fill = (222, 222, 222)
            stroke = (180, 180, 180)
            text_fill = (80, 80, 80)
        else:
            fill = (38, 38, 38)
            stroke = (92, 92, 92)
            text_fill = (185, 185, 185)
        draw.rectangle((left, top, right, bottom), fill=fill)
        step = 20 if large else 14
        for offset in range(-int(bottom - top), int(right - left), step):
            draw.line((left + offset, bottom, left + offset + (bottom - top), top), fill=stroke, width=1)
        platform = PLATFORMS.get(card["platform"], {"short": card["platform"][:2].upper()})
        label = platform["short"]
        font = self._font(14 if large else 9, "bold")
        label_w = draw.textlength(label, font=font)
        label_h = self._line_height(font)
        draw.text(
            (left + (right - left - label_w) / 2, top + (bottom - top - label_h) / 2 - 1),
            label,
            fill=text_fill,
            font=font,
        )

    def _draw_compact_section(
        self,
        image,
        draw,
        box,
        title,
        cards,
        theme,
        max_items,
        show_snapshot=True,
        snapshot_cache_seconds=90,
        avatar_cache_seconds=AVATAR_CACHE_SECONDS,
    ):
        x, y, w, h = box
        sub_font = self._font(13, "bold")
        self._draw_section_title(draw, x, y, title, len(cards), theme, sub_font)
        content_y = y + 24
        if not cards:
            self._rounded_rectangle(
                draw,
                (x, content_y, x + w, y + h),
                radius=8,
                fill=theme["panel"],
                outline=theme["line"],
                width=1,
            )
            muted_font = self._font(14, "bold")
            msg = "No cards"
            msg_w = draw.textlength(msg, font=muted_font)
            draw.text((x + (w - msg_w) / 2, content_y + 42), msg, fill=theme["muted"], font=muted_font)
            return

        gap = 6
        available = max(1, h - 22)
        base_row_h = 48 if show_snapshot else 44
        capacity = max(1, int((available + gap) / (base_row_h + gap)))
        visible = cards[: min(max_items, capacity)]
        row_h = max(48 if show_snapshot else 42, int((h - 22 - gap * (len(visible) - 1)) / len(visible)))
        for index, card in enumerate(visible):
            row_y = content_y + index * (row_h + gap)
            if show_snapshot:
                self._draw_card(
                    image,
                    draw,
                    (x, row_y, w, row_h),
                    card,
                    theme,
                    large=False,
                    show_snapshot=show_snapshot,
                    snapshot_cache_seconds=snapshot_cache_seconds,
                )
            else:
                self._draw_compact_card(image, draw, (x, row_y, w, row_h), card, theme, avatar_cache_seconds)
        if len(cards) > len(visible):
            more = f"+{len(cards) - len(visible)}"
            self._draw_text_right(draw, more, x + w, y, sub_font, theme["muted"])

    def _draw_live_queue_section(self, image, draw, box, title, cards, theme, max_items, avatar_cache_seconds=AVATAR_CACHE_SECONDS):
        x, y, w, h = box
        sub_font = self._font(13, "bold")
        self._draw_section_title(draw, x, y, title, len(cards), theme, sub_font)
        content_y = y + 24
        content_h = max(1, h - 24)
        if not cards:
            self._rounded_rectangle(
                draw,
                (x, content_y, x + w, y + h),
                radius=8,
                fill=theme["panel"],
                outline=theme["line"],
                width=1,
            )
            muted_font = self._font(13, "bold")
            msg = "No extra live"
            msg_w = draw.textlength(msg, font=muted_font)
            draw.text((x + (w - msg_w) / 2, content_y + max(14, int((content_h - self._line_height(muted_font)) / 2))), msg, fill=theme["muted"], font=muted_font)
            return 0

        layout = self._live_queue_layout((x, y, w, h), len(cards), max_items)
        visible_count = layout["visible_count"]
        rows_used = layout["rows_used"]
        row_h = layout["row_h"]
        col_w = layout["col_w"]
        columns = layout["columns"]
        gap = layout["gap"]
        col_gap = layout["col_gap"]

        visible = cards[:visible_count]
        for index, card in enumerate(visible):
            column = index // rows_used
            row = index % rows_used
            row_x = x + column * (col_w + col_gap)
            row_y = content_y + row * (row_h + gap)
            self._draw_live_mini_row(image, draw, (row_x, row_y, col_w, row_h), card, theme, avatar_cache_seconds)

        if len(cards) > len(visible):
            self._draw_text_right(draw, f"+{len(cards) - len(visible)}", x + w, y, sub_font, theme["muted"])
        return len(visible)

    def _live_queue_visible_count(self, box, card_count, max_items):
        return self._live_queue_layout(box, card_count, max_items)["visible_count"]

    @staticmethod
    def _top_live_overflow_cards(live_cards, top_count, queue_count):
        return live_cards[int(top_count) + int(queue_count) :]

    @staticmethod
    def _live_queue_layout(box, card_count, max_items):
        _x, _y, w, h = box
        content_h = max(1, h - 24)
        gap = 4
        col_gap = 8
        columns = 2 if w >= 320 and card_count > 4 and max_items > 4 else 1
        min_row_h = 22
        rows_capacity = max(1, int((content_h + gap) / (min_row_h + gap)))
        if columns == 2:
            rows_capacity = min(rows_capacity, max(1, int(math.ceil(max_items / 2))))
        visible_count = min(card_count, max_items, rows_capacity * columns)
        rows_used = max(1, int(math.ceil(max(1, visible_count) / columns)))
        row_h = max(min_row_h, int((content_h - gap * (rows_used - 1)) / rows_used))
        col_w = int((w - col_gap * (columns - 1)) / columns)
        return {
            "visible_count": visible_count,
            "rows_used": rows_used,
            "row_h": row_h,
            "col_w": col_w,
            "columns": columns,
            "gap": gap,
            "col_gap": col_gap,
        }

    def _draw_live_mini_row(self, image, draw, box, card, theme, avatar_cache_seconds=AVATAR_CACHE_SECONDS):
        x, y, w, h = box
        fill = theme["panel"]
        ink = theme["ink"]
        muted = theme["muted"]
        line = theme["line"]
        accent = theme["live_fill"] if theme.get("mode") == "dark" else theme["live_ink"]
        self._rounded_rectangle(draw, (x, y, x + w, y + h), radius=5, fill=fill, outline=line, width=1)
        draw.line((x + 5, y + 5, x + 5, y + h - 5), fill=accent, width=2)

        platform = PLATFORMS.get(card["platform"], {"short": card["platform"][:2].upper()})
        pad = 10
        avatar_size = min(22, max(16, h - 6))
        avatar_x = x + pad + 5
        avatar_y = y + max(3, int((h - avatar_size) / 2))
        self._draw_avatar(image, draw, (avatar_x, avatar_y, avatar_size), card, platform, fill, ink, line, avatar_cache_seconds)

        platform_w = min(20, max(18, int(h * 0.8)))
        platform_h = min(16, max(13, h - 7))
        platform_y = y + max(3, int((h - platform_h) / 2))
        right = x + w - pad
        self._draw_platform_badge(
            draw,
            (right - platform_w, platform_y, right, platform_y + platform_h),
            card["platform"],
            fill=fill,
            ink=ink,
            outline=muted,
        )
        right -= platform_w + 5
        if card.get("is_fav"):
            icon_size = min(15, max(12, h - 7))
            self._draw_icon_badge(
                draw,
                (right - icon_size, platform_y, right, platform_y + icon_size),
                "fav",
                fill=fill,
                ink=ink,
                outline=muted,
            )
            right -= icon_size + 5

        text_x = avatar_x + avatar_size + 8
        text_w = max(24, right - text_x)
        owner_text = self._card_display_name(card)
        name_font = self._fit_font(draw, owner_text, text_w, 11, 8, "bold")
        name_y = y + max(2, int((h - self._line_height(name_font)) / 2) - 1)
        draw.text((text_x, name_y), self._fit_text(draw, owner_text, name_font, text_w), fill=ink, font=name_font)

    def _draw_compact_card(self, image, draw, box, card, theme, avatar_cache_seconds=AVATAR_CACHE_SECONDS):
        x, y, w, h = box
        status = card["status"]
        fill, ink, muted, line = self._card_palette(status, theme)
        self._rounded_rectangle(draw, (x, y, x + w, y + h), radius=6, fill=fill, outline=line, width=1)
        draw.line((x + 5, y + 7, x + 5, y + h - 7), fill=ink, width=2)

        platform = PLATFORMS.get(card["platform"], {"short": card["platform"][:2].upper()})
        pad = 10
        avatar_size = min(32, max(24, h - 14))
        avatar_x = x + pad
        avatar_y = y + max(4, int((h - avatar_size) / 2))
        self._draw_avatar(image, draw, (avatar_x, avatar_y, avatar_size), card, platform, fill, ink, line, avatar_cache_seconds)

        badge_right = x + w - pad
        platform_w = 22
        platform_h = 16
        platform_y = y + max(6, int((h - platform_h) / 2))
        self._draw_platform_badge(
            draw,
            (badge_right - platform_w, platform_y, badge_right, platform_y + platform_h),
            card["platform"],
            fill=fill,
            ink=ink,
            outline=ink,
        )
        badge_right -= platform_w + 5
        if card.get("is_fav"):
            icon_size = 17
            self._draw_icon_badge(
                draw,
                (badge_right - icon_size, platform_y, badge_right, platform_y + icon_size),
                "fav",
                fill=fill,
                ink=ink,
                outline=muted,
            )
            badge_right -= icon_size + 5

        text_x = avatar_x + avatar_size + 10
        text_right = badge_right - 4
        text_w = max(24, text_right - text_x)
        owner_text = self._card_display_name(card)
        name_font = self._fit_font(draw, owner_text, text_w, 12, 9, "bold")
        detail_text = card["title"] or self._meta_text(card)
        detail_font = self._fit_font(draw, detail_text, text_w, 8, 7)

        name_y = y + max(5, int((h - self._line_height(name_font) - self._line_height(detail_font) - 2) / 2))
        detail_y = name_y + self._line_height(name_font) + 2
        draw.text((text_x, name_y), self._fit_text(draw, owner_text, name_font, text_w), fill=ink, font=name_font)
        if detail_y + self._line_height(detail_font) <= y + h - 4:
            draw.text((text_x, detail_y), self._fit_text(draw, detail_text, detail_font, text_w), fill=muted, font=detail_font)

    def _draw_avatar(self, image, draw, avatar_box, card, platform, fill, ink, line, cache_seconds):
        avatar_x, avatar_y, avatar_size = avatar_box
        bounds = (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size)
        avatar = self._load_avatar_source(card.get("avatar"), cache_seconds)
        if avatar:
            try:
                fitted = ImageOps.fit(avatar, (avatar_size, avatar_size), method=self._resampling_filter())
                fitted = ImageOps.autocontrast(ImageOps.grayscale(fitted)).convert("RGB")
                mask = Image.new("L", (avatar_size, avatar_size), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)
                image.paste(fitted, (avatar_x, avatar_y), mask)
            except Exception as exc:
                logger.warning("LiveRadar avatar render failed for %s/%s: %s", card.get("platform"), card.get("id"), exc)
                avatar = None

        if not avatar:
            draw.ellipse(bounds, fill=fill, outline=line, width=1)
            short = platform.get("short", str(card.get("platform") or "")[:2].upper())
            label_font = self._fit_font(draw, short, avatar_size - 6, 9, 6, "bold")
            label_w = draw.textlength(short, font=label_font)
            label_h = self._line_height(label_font)
            draw.text(
                (avatar_x + (avatar_size - label_w) / 2, avatar_y + (avatar_size - label_h) / 2 - 1),
                short,
                fill=ink,
                font=label_font,
            )

        draw.ellipse(bounds, outline=ink, width=1)
        if card.get("is_fav"):
            badge_size = max(10, int(avatar_size * 0.38))
            badge = (avatar_x + avatar_size - badge_size, avatar_y + avatar_size - badge_size, avatar_x + avatar_size + 1, avatar_y + avatar_size + 1)
            self._draw_icon_badge(draw, badge, "fav", fill=ink, ink=fill, outline=ink)

    def _draw_quiet_panel(self, draw, box, tracked_count, theme):
        x, y, w, h = box
        self._rounded_rectangle(draw, (x, y, x + w, y + h), radius=8, fill=theme["panel"], outline=theme["line"], width=2)
        title_font = self._font(34, "bold")
        sub_font = self._font(17, "bold")
        title = "NO LIVE SIGNAL"
        subtitle = f"{tracked_count} streamer cards tracked"
        draw.text((x + 22, y + 36), title, fill=theme["ink"], font=title_font)
        draw.text((x + 24, y + 86), subtitle, fill=theme["muted"], font=sub_font)
        draw.line((x + 24, y + h - 34, x + w - 24, y + h - 34), fill=theme["line"], width=2)
        draw.text((x + 24, y + h - 27), "Waiting for the next broadcast window", fill=theme["muted"], font=self._font(13))

    def _draw_section_title(self, draw, x, y, title, count, theme, font):
        draw.text((x, y), title, fill=theme["ink"], font=font)
        count_text = str(count)
        count_w = draw.textlength(count_text, font=font)
        self._draw_pill(
            draw,
            (x + draw.textlength(title, font=font) + 8, y - 1, x + draw.textlength(title, font=font) + 22 + count_w, y + 18),
            count_text,
            font,
            fill=theme["ink"],
            ink=theme["bg"],
            outline=theme["ink"],
        )

    def _draw_status_totals(self, draw, right_x, y, stats, font, theme):
        x = right_x
        for label, count, kind in reversed(stats):
            text = f"{label} {count}"
            text_w = draw.textlength(text, font=font)
            w = text_w + 18
            x -= w
            fill, ink, _muted, line = self._card_palette(kind, theme)
            self._draw_pill(draw, (x, y, x + w, y + 25), text, font, fill=fill, ink=ink, outline=line)
            x -= 7

    def _live_overflow_text(self, cards, draw, font, max_width):
        names = [self._overflow_live_name(card) for card in cards]
        names = [name for name in names if name]
        if not names:
            return f"+{len(cards)} more live"

        for count in range(len(names), 0, -1):
            shown = names[:count]
            hidden = len(names) - count
            middle = ", ".join(shown)
            if hidden:
                middle = f"{middle}, +{hidden}"
            text = f"...{middle} are live too"
            if draw.textlength(text, font=font) <= max_width:
                return text

        return self._fit_text(draw, f"...{names[0]} are live too", font, max_width)

    def _overflow_live_name(self, card):
        return self._card_display_name(card)

    def _card_display_name(self, card):
        for key in ("owner", "label", "id"):
            value = self._clean_text(card.get(key) or "")
            if value:
                return value
        return ""

    def _paste_title_logo(self, image, x, y, size, theme):
        source = self._load_title_logo()
        if source is None:
            return False
        try:
            logo = ImageOps.contain(source.copy(), (size, size), method=self._resampling_filter())
            logo, mask = self._prepare_title_logo(logo, theme)
            paste_x = int(x + (size - logo.width) / 2)
            paste_y = int(y + (size - logo.height) / 2)
            image.paste(logo.convert("RGB"), (paste_x, paste_y), mask)
            draw = ImageDraw.Draw(image)
            self._rounded_rectangle(draw, (x, y, x + size, y + size), radius=6, fill=None, outline=theme["line"], width=1)
            return True
        except Exception as exc:
            logger.warning("LiveRadar title logo unavailable: %s", exc)
            return False

    @staticmethod
    def _prepare_title_logo(source, theme):
        logo = source.convert("RGBA")
        alpha = logo.getchannel("A")
        luma = ImageOps.grayscale(logo)
        if theme.get("mode") == "light":
            foreground = luma.point(lambda value: 255 if value > 34 else 0)
            mask = ImageChops.multiply(alpha, foreground)
            dark = ImageOps.invert(ImageOps.autocontrast(luma))
            logo = Image.merge("RGBA", (dark, dark, dark, mask))
            return logo, mask

        logo = ImageOps.autocontrast(luma).convert("RGBA")
        if alpha.getextrema() != (255, 255):
            logo.putalpha(alpha)
            return logo, alpha
        return logo, None

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_title_logo():
        path = os.path.join(PLUGIN_DIR, TITLE_LOGO_FILE)
        if not os.path.isfile(path):
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load LiveRadar title logo %s: %s", path, exc)
            return None

    def _draw_pill(self, draw, box, text, font, fill, ink, outline):
        self._rounded_rectangle(draw, box, radius=6, fill=fill, outline=outline, width=1)
        left, top, right, bottom = box
        text_w = draw.textlength(text, font=font)
        text_h = self._line_height(font)
        draw.text(
            (left + (right - left - text_w) / 2, top + (bottom - top - text_h) / 2 - 1),
            text,
            fill=ink,
            font=font,
        )

    def _draw_platform_badge(self, draw, box, platform_key, fill, ink, outline):
        left, top, right, bottom = [int(v) for v in box]
        width = max(1, right - left)
        height = max(1, bottom - top)
        self._rounded_rectangle(draw, (left, top, right, bottom), radius=max(4, min(width, height) // 3), fill=fill, outline=outline, width=1)
        icon_box = (
            left + max(3, width // 6),
            top + max(3, height // 5),
            right - max(3, width // 6),
            bottom - max(3, height // 5),
        )
        key = str(platform_key or "").strip().lower()
        if key == "bilibili":
            self._draw_bilibili_mark(draw, icon_box, ink)
        elif key == "douyu":
            self._draw_douyu_mark(draw, icon_box, ink)
        elif key == "twitch":
            self._draw_twitch_mark(draw, icon_box, ink)
        else:
            self._draw_platform_initials(draw, (left, top, right, bottom), key, ink)
        return left

    def _draw_bilibili_mark(self, draw, box, ink):
        left, top, right, bottom = [int(v) for v in box]
        width = max(1, right - left)
        height = max(1, bottom - top)
        line_w = max(1, min(width, height) // 7)
        body_top = top + max(2, height // 5)
        radius = max(1, min(width, height) // 5)
        self._rounded_rectangle(draw, (left, body_top, right, bottom), radius=radius, fill=None, outline=ink, width=line_w)
        draw.line((left + width * 0.28, body_top, left + width * 0.12, top), fill=ink, width=line_w)
        draw.line((right - width * 0.28, body_top, right - width * 0.12, top), fill=ink, width=line_w)
        eye_r = max(1, min(width, height) // 10)
        cy = body_top + (bottom - body_top) * 0.53
        for cx in (left + width * 0.36, right - width * 0.36):
            draw.ellipse((cx - eye_r, cy - eye_r, cx + eye_r, cy + eye_r), fill=ink)

    def _draw_douyu_mark(self, draw, box, ink):
        left, top, right, bottom = [int(v) for v in box]
        width = max(1, right - left)
        height = max(1, bottom - top)
        line_w = max(1, min(width, height) // 7)
        cy = top + height * 0.5
        tail_w = max(3, int(width * 0.24))
        body = (left + tail_w, top + height * 0.18, right, bottom - height * 0.18)
        draw.polygon(
            [
                (left, cy),
                (left + tail_w + 1, top + height * 0.22),
                (left + tail_w + 1, bottom - height * 0.22),
            ],
            fill=ink,
        )
        draw.ellipse(body, outline=ink, width=line_w)
        eye_r = max(1, min(width, height) // 10)
        eye_x = right - width * 0.22
        eye_y = cy - height * 0.08
        draw.ellipse((eye_x - eye_r, eye_y - eye_r, eye_x + eye_r, eye_y + eye_r), fill=ink)

    def _draw_twitch_mark(self, draw, box, ink):
        left, top, right, bottom = [int(v) for v in box]
        width = max(1, right - left)
        height = max(1, bottom - top)
        line_w = max(1, min(width, height) // 7)
        notch = max(2, width // 5)
        bubble = [
            (left, top),
            (right, top),
            (right, bottom - notch),
            (left + width * 0.58, bottom - notch),
            (left + width * 0.42, bottom),
            (left + width * 0.42, bottom - notch),
            (left, bottom - notch),
            (left, top),
        ]
        draw.line(bubble, fill=ink, width=line_w, joint="curve")
        eye_top = top + height * 0.34
        eye_bottom = top + height * 0.68
        for cx in (left + width * 0.42, left + width * 0.62):
            draw.line((cx, eye_top, cx, eye_bottom), fill=ink, width=line_w)

    def _draw_platform_initials(self, draw, box, platform_key, ink):
        left, top, right, bottom = [int(v) for v in box]
        short = PLATFORMS.get(platform_key, {}).get("short") or str(platform_key or "")[:2].upper()
        font = self._fit_font(draw, short, max(8, right - left - 6), 8, 6, "bold")
        text_w = draw.textlength(short, font=font)
        text_h = self._line_height(font)
        draw.text((left + (right - left - text_w) / 2, top + (bottom - top - text_h) / 2 - 1), short, fill=ink, font=font)

    def _draw_icon_badge(self, draw, box, kind, fill, ink, outline):
        left, top, right, bottom = [int(v) for v in box]
        size = max(1, min(right - left, bottom - top))
        self._rounded_rectangle(draw, (left, top, right, bottom), radius=max(4, size // 4), fill=fill, outline=outline, width=1)
        cx = left + (right - left) / 2
        cy = top + (bottom - top) / 2
        if kind == "live":
            dot_r = max(2, size // 7)
            draw.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r), fill=ink)
            arc_pad = max(3, size // 4)
            arc_box = (left + arc_pad, top + arc_pad - 1, right - arc_pad, bottom - arc_pad + 1)
            draw.arc(arc_box, start=290, end=70, fill=ink, width=max(1, size // 14))
        elif kind == "fav":
            draw.polygon(self._star_points(cx, cy, size * 0.33, size * 0.15), fill=ink)
        return left

    @staticmethod
    def _star_points(cx, cy, outer_radius, inner_radius):
        points = []
        for index in range(10):
            radius = outer_radius if index % 2 == 0 else inner_radius
            angle = -math.pi / 2 + index * math.pi / 5
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
        return points

    def _card_palette(self, status, theme):
        if status == "live":
            return theme["live_fill"], theme["live_ink"], theme["live_muted"], theme["live_line"]
        if status == "replay":
            return theme["replay_fill"], theme["ink"], theme["muted"], theme["line"]
        if status == "error":
            return theme["panel"], theme["ink"], theme["muted"], theme["ink"]
        return theme["panel"], theme["ink"], theme["muted"], theme["line"]

    def _theme(self, settings, device_config):
        requested = str((settings or {}).get("themeMode") or "auto").strip().lower()
        if requested == "auto":
            requested = "dark" if get_theme_context(device_config).get("mode") == "night" else "light"
        if requested in {"light", "day", "paper"}:
            return {
                "mode": "light",
                "bg": (255, 255, 255),
                "ink": (0, 0, 0),
                "muted": (78, 78, 78),
                "line": (0, 0, 0),
                "panel": (255, 255, 255),
                "live_fill": (0, 0, 0),
                "live_ink": (255, 255, 255),
                "live_muted": (210, 210, 210),
                "live_line": (0, 0, 0),
                "replay_fill": (238, 238, 238),
            }
        return {
            "mode": "dark",
            "bg": (0, 0, 0),
            "ink": (255, 255, 255),
            "muted": (172, 172, 172),
            "line": (235, 235, 235),
            "panel": (18, 18, 18),
            "live_fill": (255, 255, 255),
            "live_ink": (0, 0, 0),
            "live_muted": (68, 68, 68),
            "live_line": (255, 255, 255),
            "replay_fill": (36, 36, 36),
        }

    def _parse_rooms(self, settings):
        json_rooms = self._parse_rooms_json(settings.get("roomsJson"))
        text_rooms = self._parse_rooms_text(settings.get("roomsText"))
        rooms = json_rooms or text_rooms
        seen = set()
        unique = []
        for room in rooms:
            platform = str(room.get("platform") or "").strip().lower()
            room_id = str(room.get("id") or room.get("roomId") or room.get("channel") or "").strip()
            if platform not in PLATFORMS or not room_id:
                continue
            key = (platform, room_id)
            if key in seen:
                continue
            seen.add(key)
            unique.append(
                {
                    "platform": platform,
                    "id": room_id,
                    "label": str(room.get("label") or room.get("name") or room.get("owner") or "").strip(),
                    "isFav": self._bool_setting(room.get("isFav") or room.get("favorite") or room.get("fav"), False)
                    or self._favorite_priority(platform, room_id) is not None,
                }
            )
        return unique

    def _parse_rooms_json(self, raw):
        if not raw or not str(raw).strip():
            return []
        try:
            data = json.loads(str(raw))
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in ("rooms", "streamers", "pro_monitored_rooms", "monitoredRooms"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return []

    def _parse_rooms_text(self, raw):
        text = str(raw or DEFAULT_ROOMS_TEXT)
        rooms = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = self._split_room_line(line)
            if len(parts) < 2:
                continue
            platform = parts[0].strip().lower()
            room_id = parts[1].strip()
            label = ""
            fav_parts = parts[2:]
            if len(parts) >= 3 and not self._is_fav_marker(parts[2]):
                label = parts[2].strip()
                fav_parts = parts[3:]
            fav = any(self._is_fav_marker(part) for part in fav_parts)
            rooms.append({"platform": platform, "id": room_id, "label": label, "isFav": fav})
        return rooms

    @staticmethod
    def _split_room_line(line):
        if "|" in line:
            return [part.strip() for part in line.split("|")]
        if "\t" in line:
            return [part.strip() for part in line.split("\t")]
        if "," in line:
            return [part.strip() for part in line.split(",")]
        if ":" in line:
            first, rest = line.split(":", 1)
            return [first.strip(), rest.strip()]
        return line.split()

    def _write_context(self, cards, generated_at, cache_seconds, from_cache, warning):
        live = [card for card in cards if card["status"] == "live"]
        replay = [card for card in cards if card["status"] == "replay"]
        offline = [card for card in cards if card["status"] == "offline"]
        live_names = [card["owner"] for card in live[:5]]
        summary = (
            f"{len(live)} live: {', '.join(live_names)}"
            if live
            else f"No live streamers; {len(offline)} offline and {len(replay)} replay."
        )
        write_context(
            "live_radar",
            {
                "kind": "stream_status",
                "source": "LiveRadar",
                "summary": summary,
                "live": [self._context_card(card) for card in live],
                "replay": [self._context_card(card) for card in replay],
                "offline_count": len(offline),
                "from_cache": bool(from_cache),
                "warning": warning,
            },
            generated_at=generated_at,
            ttl_seconds=max(120, int(cache_seconds) * 3),
        )

    @staticmethod
    def _context_card(card):
        return {
            "platform": card["platform"],
            "id": card["id"],
            "owner": card["owner"],
            "title": card["title"],
            "heat": card["heat"],
            "is_fav": card["is_fav"],
        }

    def _cache_key(self, rooms, api_url, fetch_avatars):
        raw = json.dumps(
            {
                "schema": CACHE_SCHEMA_VERSION,
                "rooms": rooms,
                "api_url": api_url,
                "fetch_avatars": bool(fetch_avatars),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _cache_dir(self):
        path = Path(self.get_plugin_dir("cache"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _cache_path(self, cache_key):
        return self._cache_dir() / f"{cache_key}.json"

    def _cover_cache_path(self, url):
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        return self._cache_dir() / f"cover_{digest}.png"

    def _avatar_cache_path(self, url):
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        return self._cache_dir() / f"avatar_{digest}.png"

    def _load_cover_source(self, url, cache_seconds):
        url = str(url or "").strip()
        if not url:
            return None

        cache_path = self._cover_cache_path(url)
        cached = self._open_cached_cover(cache_path) if cache_path.exists() else None
        if cached and time.time() - cache_path.stat().st_mtime < max(30, int(cache_seconds or 90)):
            return cached

        try:
            session = get_http_session()
            response = session.get(url, timeout=12, headers=self._cover_headers(url))
            response.raise_for_status()
            content = response.content
            if len(content) > COVER_MAX_BYTES:
                raise RuntimeError(f"cover image too large: {len(content)} bytes")

            cover = Image.open(BytesIO(content))
            cover.load()
            cover = ImageOps.exif_transpose(cover).convert("RGB")
            cover.thumbnail(COVER_MAX_SIZE, self._resampling_filter())
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cover.save(cache_path, "PNG")
            return cover.copy()
        except Exception as exc:
            logger.warning("LiveRadar cover unavailable for %s: %s", url, exc)
            return cached

    def _load_avatar_source(self, url, cache_seconds=AVATAR_CACHE_SECONDS):
        url = str(url or "").strip()
        if not url:
            return None

        cache_path = self._avatar_cache_path(url)
        cached = self._open_cached_avatar(cache_path) if cache_path.exists() else None
        if cached and time.time() - cache_path.stat().st_mtime < max(300, int(cache_seconds or AVATAR_CACHE_SECONDS)):
            return cached

        try:
            session = get_http_session()
            response = session.get(url, timeout=12, headers=self._cover_headers(url))
            response.raise_for_status()
            content = response.content
            if len(content) > AVATAR_MAX_BYTES:
                raise RuntimeError(f"avatar image too large: {len(content)} bytes")

            avatar = Image.open(BytesIO(content))
            avatar.load()
            avatar = ImageOps.exif_transpose(avatar).convert("RGB")
            avatar.thumbnail(AVATAR_MAX_SIZE, self._resampling_filter())
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            avatar.save(cache_path, "PNG")
            return avatar.copy()
        except Exception as exc:
            logger.warning("LiveRadar avatar unavailable for %s: %s", url, exc)
            return cached

    @staticmethod
    def _open_cached_cover(path):
        try:
            return Image.open(path).convert("RGB")
        except Exception as exc:
            logger.warning("Could not read LiveRadar cover cache %s: %s", path, exc)
            return None

    @staticmethod
    def _open_cached_avatar(path):
        try:
            return Image.open(path).convert("RGB")
        except Exception as exc:
            logger.warning("Could not read LiveRadar avatar cache %s: %s", path, exc)
            return None

    @staticmethod
    def _cover_headers(url):
        headers = dict(COVER_HEADERS)
        lower = str(url or "").lower()
        if "hdslb.com" in lower or "bilibili" in lower:
            headers["Referer"] = "https://live.bilibili.com/"
        elif "douyucdn" in lower or "douyu" in lower:
            headers["Referer"] = "https://www.douyu.com/"
        elif "ttvnw.net" in lower or "twitch" in lower:
            headers["Referer"] = "https://www.twitch.tv/"
        return headers

    def _read_cache(self, cache_key):
        try:
            path = self._cache_path(cache_key)
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read LiveRadar cache: %s", exc)
        return {}

    def _write_cache(self, cache_key, data):
        path = self._cache_path(cache_key)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _sort_cards(cards):
        return sorted(
            cards,
            key=lambda card: (
                STATUS_RANK.get(card["status"], 9),
                0 if card.get("is_fav") else 1,
                card.get("favorite_rank") if card.get("favorite_rank") is not None else 9999,
                -int(card.get("heat") or 0),
                str(card.get("owner") or "").lower(),
            ),
        )

    @staticmethod
    def _favorite_priority(platform, room_id):
        return FAVORITE_PRIORITY.get((str(platform or "").lower(), str(room_id or "")))

    @staticmethod
    def _status_kind(status):
        if status.get("isError"):
            return "error"
        if status.get("isLive"):
            return "live"
        if status.get("isReplay"):
            return "replay"
        return "offline"

    @staticmethod
    def _status_label(status):
        return {
            "live": "LIVE",
            "replay": "REPLAY",
            "offline": "OFF",
            "error": "ERR",
        }.get(status, "OFF")

    def _default_status(self, room, is_error=False):
        return {
            "isLive": False,
            "isReplay": False,
            "title": "",
            "owner": room.get("label") or room.get("id") or "",
            "cover": "",
            "avatar": "",
            "heatValue": 0,
            "isError": bool(is_error),
            "startTime": None,
            "platform": room.get("platform"),
            "id": room.get("id"),
        }

    def _offline_title(self, card):
        if card["status"] == "error":
            return "Status temporarily unavailable"
        if card["status"] == "replay":
            return "Replay or loop signal detected"
        return "Offline, waiting for signal"

    def _meta_text(self, card):
        parts = []
        if card["heat"]:
            parts.append(self._format_heat(card["heat"]))
        uptime = self._format_uptime(card.get("start_time"))
        if uptime and card["status"] == "live":
            parts.append(uptime)
        if not parts:
            parts.append(card["id"])
        return "  |  ".join(parts)

    @staticmethod
    def _format_heat(value):
        value = int(value or 0)
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M viewers"
        if value >= 10_000:
            return f"{value / 1000:.0f}K viewers"
        if value >= 1000:
            return f"{value / 1000:.1f}K viewers"
        return f"{value} viewers"

    @staticmethod
    def _format_uptime(start_time):
        try:
            raw = float(start_time)
        except (TypeError, ValueError):
            return ""
        if raw <= 0:
            return ""
        if raw < 946684800000:
            raw *= 1000
        seconds = max(0, int(time.time() - raw / 1000))
        if seconds > 72 * 3600:
            return ""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours:
            return f"{hours}h {minutes:02d}m"
        return f"{minutes}m"

    @staticmethod
    def _safe_int(value, default=0):
        try:
            if value is None or (isinstance(value, float) and math.isnan(value)):
                return default
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bool_setting(value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "fav", "favorite"}

    @staticmethod
    def _is_fav_marker(value):
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "fav", "favorite", "star"}

    @staticmethod
    def _int_setting(settings, key, default, minimum, maximum):
        try:
            value = int(settings.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _font(size, weight="normal"):
        for path in SANS_FONT_PATHS["bold" if weight == "bold" else "normal"]:
            try:
                if os.path.isfile(path):
                    return LiveRadar._load_sans_font(path, int(size), weight)
            except Exception:
                continue
        for family in ("Microsoft YaHei", "WenQuanYi Micro Hei", "Noto Sans CJK SC", "LXGW WenKai", "FandolKai", "Jost"):
            try:
                font = get_font(family, int(size), "bold" if weight == "bold" else "normal")
                if font:
                    return font
            except Exception:
                continue
        return ImageFont.load_default()

    @staticmethod
    @lru_cache(maxsize=96)
    def _load_sans_font(path, size, weight="normal"):
        font = ImageFont.truetype(path, int(size))
        if weight == "bold":
            LiveRadar._apply_variation_weight(font, 780)
        return font

    @staticmethod
    def _apply_variation_weight(font, target_weight):
        if not hasattr(font, "get_variation_axes") or not hasattr(font, "set_variation_by_axes"):
            return
        try:
            axes = font.get_variation_axes()
        except Exception:
            return
        if not axes:
            return
        values = []
        changed = False
        for axis in axes:
            if not isinstance(axis, dict):
                return
            name = axis.get("name") or axis.get(b"name") or ""
            if isinstance(name, bytes):
                name = name.decode("ascii", errors="ignore")
            minimum = axis.get("minimum", axis.get(b"minimum", 0))
            maximum = axis.get("maximum", axis.get(b"maximum", 1000))
            default = axis.get("default", axis.get(b"default", minimum))
            value = default
            if "weight" in str(name).lower() or "wght" in str(name).lower():
                value = max(minimum, min(maximum, int(target_weight)))
                changed = True
            values.append(value)
        if changed:
            try:
                font.set_variation_by_axes(values)
            except Exception:
                return

    @staticmethod
    @lru_cache(maxsize=4)
    def _font_source_marker(weight="normal"):
        for path in SANS_FONT_PATHS["bold" if weight == "bold" else "normal"]:
            if os.path.isfile(path):
                return path
        return "app-font-fallback"

    @staticmethod
    def _line_height(font):
        bbox = font.getbbox("Ag") if hasattr(font, "getbbox") else (0, 0, 8, 12)
        return max(1, bbox[3] - bbox[1])

    def _fit_font(self, draw, text, max_width, start_size, min_size, weight="normal"):
        size = int(start_size)
        min_size = int(min_size)
        while size > min_size:
            font = self._font(size, weight)
            if draw.textlength(str(text or ""), font=font) <= max_width:
                return font
            size -= 1
        return self._font(min_size, weight)

    @staticmethod
    def _resampling_filter():
        return getattr(Image, "Resampling", Image).LANCZOS

    @staticmethod
    def _clean_text(value):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        cleaned = []
        for ch in text:
            category = unicodedata.category(ch)
            if category.startswith("C"):
                continue
            if ord(ch) > 0xFFFF:
                if cleaned and cleaned[-1] != " ":
                    cleaned.append(" ")
                continue
            if category.startswith("S") and ch not in {"$", "%", "#", "+", "-", "*"}:
                if cleaned and cleaned[-1] != " ":
                    cleaned.append(" ")
                continue
            cleaned.append(ch)
        return re.sub(r"\s+", " ", "".join(cleaned)).strip()

    def _wrap_text(self, draw, text, font, max_width, max_lines):
        text = self._clean_text(text)
        if not text:
            return []
        tokens = text.split(" ") if " " in text else list(text)
        lines = []
        current = ""
        separator = " " if " " in text else ""
        for token in tokens:
            candidate = token if not current else current + separator + token
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = token
            else:
                lines.append(self._fit_text(draw, token, font, max_width))
                current = ""
            if len(lines) >= max_lines:
                return lines[:max_lines]
        if current and len(lines) < max_lines:
            lines.append(self._fit_text(draw, current, font, max_width))
        return lines[:max_lines]

    def _fit_wrapped_text(self, draw, text, max_width, max_height, max_lines, start_size, min_size, weight="normal"):
        text = self._clean_text(text)
        if not text or max_width <= 0 or max_height <= 0:
            return self._font(min_size, weight), []
        for size in range(int(start_size), int(min_size) - 1, -1):
            font = self._font(size, weight)
            lines = self._wrap_text(draw, text, font, max_width, max_lines)
            if not lines:
                continue
            line_h = self._line_height(font) + 1
            if line_h * len(lines) - 1 <= max_height:
                return font, lines
        font = self._font(min_size, weight)
        if self._line_height(font) <= max_height:
            return font, self._wrap_text(draw, text, font, max_width, 1)
        return font, []

    @staticmethod
    def _fit_text(draw, text, font, max_width):
        text = str(text or "")
        if draw.textlength(text, font=font) <= max_width:
            return text
        output = ""
        for ch in text:
            if draw.textlength(output + ch, font=font) > max_width:
                break
            output += ch
        return output.rstrip()

    @staticmethod
    def _draw_text_right(draw, text, right_x, y, font, fill):
        text_w = draw.textlength(text, font=font)
        draw.text((right_x - text_w, y), text, fill=fill, font=font)

    @staticmethod
    def _rounded_rectangle(draw, box, radius=8, fill=None, outline=None, width=1):
        try:
            draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
        except AttributeError:
            draw.rectangle(box, fill=fill, outline=outline, width=width)
