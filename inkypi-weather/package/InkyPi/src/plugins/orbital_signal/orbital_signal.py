from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from utils.cache_manager import CacheBudget
from utils.safe_image import safe_open_image
from utils.app_utils import (
    bounded_int,
    coerce_bool,
    get_available_font_names,
    get_base_ui_font,
    get_font,
)

from . import launch_photo
from .sources import fetch_launches, fetch_market_events


logger = logging.getLogger(__name__)

PLUGIN_ID = "orbital_signal"
DEFAULT_FONT = "Microsoft YaHei"
WORDMARK_ASSET_PATH = Path(__file__).with_name("assets") / "orbital-signal-wordmark.png"
CACHE_SCHEMA = "orbital-signal-v1"
SOURCE_STATES = {"live", "fresh_cache", "stale_cache", "fixture"}
LAUNCH_PHOTO_CACHE_BUDGET = CacheBudget(
    30 * 24 * 60 * 60,
    24,
    48 * 1024 * 1024,
)

DAY_PALETTE = {
    "paper": (247, 244, 236),
    "panel": (255, 252, 244),
    "ink": (8, 30, 61),
    "muted": (63, 76, 91),
    "rule": (8, 30, 61),
    "orange": (236, 78, 17),
    "orange_tint": (250, 224, 199),
    "red": (224, 35, 43),
    "blue": (25, 83, 177),
    "green": (18, 123, 67),
    "white": (255, 252, 244),
}

NIGHT_PALETTE = {
    "paper": (12, 23, 40),
    "panel": (23, 37, 58),
    "ink": (247, 241, 221),
    "muted": (202, 210, 219),
    "rule": (214, 220, 226),
    "orange": (255, 133, 50),
    "orange_tint": (92, 50, 32),
    "red": (255, 86, 82),
    "blue": (93, 148, 244),
    "green": (85, 188, 120),
    "white": (26, 42, 64),
}


def _utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _utc(parsed)


class OrbitalSignal(BasePlugin):
    CACHE_SCHEMA = CACHE_SCHEMA

    @staticmethod
    @lru_cache(maxsize=1)
    def _cached_wordmark_source():
        with safe_open_image(WORDMARK_ASSET_PATH) as source:
            wordmark = source.convert("RGBA")
        bounds = wordmark.getchannel("A").getbbox()
        if bounds is None:
            raise ValueError("Orbital Signal wordmark has no visible pixels")
        return wordmark.crop(bounds)

    def _prepare_header_wordmark(self, max_size, palette):
        wordmark = self._cached_wordmark_source().copy()
        wordmark.thumbnail(
            (max(1, int(max_size[0])), max(1, int(max_size[1]))),
            Image.Resampling.LANCZOS,
        )
        recolored = []
        for red, green, blue, alpha in wordmark.get_flattened_data():
            if alpha == 0:
                recolored.append((0, 0, 0, 0))
            elif red > 120 and red > green * 1.2 and red > blue * 1.5:
                recolored.append((*palette["orange"], alpha))
            else:
                recolored.append((*palette["ink"], alpha))
        wordmark.putdata(recolored)
        return wordmark

    def presentation_mode(self, settings):
        return PresentationMode.NO_CHANGE

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    @staticmethod
    def _panel_boxes(width, height):
        if (width, height) == (800, 480):
            return {
                "header": (0, 0, 800, 58),
                "launch": (0, 58, 440, 448),
                "markets": (440, 58, 800, 448),
                "footer": (0, 448, 800, 480),
            }
        scale_x = width / 800
        scale_y = height / 480
        return {
            name: tuple(
                round(value * (scale_x if index % 2 == 0 else scale_y))
                for index, value in enumerate(box)
            )
            for name, box in OrbitalSignal._panel_boxes(800, 480).items()
        }

    @staticmethod
    def _palette(settings):
        mode = str((settings or {}).get("themeMode") or "day").casefold()
        resolved = (settings or {}).get("_inkypi_theme") or {}
        mode = str(resolved.get("mode") or mode).casefold()
        return NIGHT_PALETTE if mode == "night" else DAY_PALETTE

    def _font(self, size, bold=False, family=None):
        selected = str(family or "").strip() or DEFAULT_FONT
        weight = "bold" if bold else "normal"
        try:
            font = get_font(selected, int(size), weight)
        except (KeyError, OSError, TypeError, ValueError):
            font = None
        if font is None and selected != DEFAULT_FONT:
            try:
                font = get_font(DEFAULT_FONT, int(size), weight)
            except (KeyError, OSError, TypeError, ValueError):
                font = None
        return font or get_base_ui_font(int(size), bold=bold)

    @staticmethod
    def _fit_text(draw, text, font, max_width):
        value = str(text or "").strip()
        if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
            return value
        ellipsis = "..."
        while value and draw.textbbox((0, 0), value + ellipsis, font=font)[2] > max_width:
            value = value[:-1]
        return value.rstrip() + ellipsis if value else ellipsis

    def _fit_font(self, draw, text, max_width, max_size, min_size, family, bold=True):
        value = str(text or "").strip()
        for size in range(int(max_size), int(min_size) - 1, -1):
            font = self._font(size, bold=bold, family=family)
            box = draw.textbbox((0, 0), value, font=font)
            if box[2] - box[0] <= max_width:
                return font
        return self._font(min_size, bold=bold, family=family)

    def _cache_dir(self, create=True):
        return self.cache_dir(
            env_var="ORBITAL_SIGNAL_CACHE_DIR",
            leaf="cache",
            create=create,
            strip=True,
        )

    def _photo_namespace(self):
        return self.managed_cache_namespace(
            self._cache_dir() / "launch_photos",
            LAUNCH_PHOTO_CACHE_BUDGET,
        )

    def _hydrate_primary_launch_photo(self, launch):
        row = dict(launch or {})
        try:
            cached = launch_photo.load_or_acquire_photo(
                row,
                self._photo_namespace(),
                allow_network=True,
            )
            if cached is None:
                return row
            row.update(
                {
                    "photo_cache_key": cached.cache_key,
                    "photo_credit": cached.credit,
                    "photo_license": cached.license,
                    "photo_source": cached.source,
                }
            )
            cached.image.close()
        except Exception as exc:
            logger.warning(
                "Orbital Signal primary launch photo unavailable: %s",
                type(exc).__name__,
            )
        return row

    @staticmethod
    def _read_json(path, default):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return default

    @staticmethod
    def _write_json(path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _cache_fresh(payload, now, ttl_minutes):
        if not isinstance(payload, dict):
            return False
        fetched = _parse_time(payload.get("fetched_at"))
        if fetched is None:
            return False
        age = (_utc(now) - fetched).total_seconds()
        return 0 <= age <= int(ttl_minutes) * 60

    @staticmethod
    def _valid_launch(row):
        return (
            isinstance(row, dict)
            and bool(str(row.get("id") or "").strip())
            and bool(str(row.get("name") or "").strip())
            and _parse_time(row.get("net")) is not None
        )

    @staticmethod
    def _valid_market(row):
        if not isinstance(row, dict):
            return False
        try:
            probability = float(row.get("probability"))
            change = float(row.get("change_24h"))
            heat = int(row.get("heat"))
        except (TypeError, ValueError):
            return False
        return (
            bool(str(row.get("id") or "").strip())
            and bool(str(row.get("title") or "").strip())
            and bool(str(row.get("leader") or "").strip())
            and 0 <= probability <= 1
            and -1 <= change <= 1
            and 0 <= heat <= 100
        )

    @classmethod
    def _valid_items(cls, name, items):
        validator = cls._valid_launch if name == "launches" else cls._valid_market
        if not isinstance(items, list):
            return []
        return [dict(row) for row in items if validator(row)]

    def _resolve_source(self, name, now, force, fetcher, fixture, ttl_minutes):
        cache_file = self._cache_dir() / f"{name}.json"
        cached = self._read_json(cache_file, {})
        cached_items = cached.get("items") if isinstance(cached, dict) else None
        valid_cached = self._valid_items(name, cached_items)
        cache_valid = (
            isinstance(cached, dict)
            and cached.get("schema") == CACHE_SCHEMA
            and isinstance(cached_items, list)
            and bool(valid_cached)
            and len(valid_cached) == len(cached_items)
        )
        if not force and cache_valid and self._cache_fresh(cached, now, ttl_minutes):
            return valid_cached, "fresh_cache", ""

        try:
            live_items = self._valid_items(name, fetcher())
            if not live_items:
                raise ValueError("source returned no usable rows")
            self._write_json(
                cache_file,
                {
                    "schema": CACHE_SCHEMA,
                    "fetched_at": _utc(now).isoformat(),
                    "items": live_items,
                },
            )
            return live_items, "live", ""
        except Exception as exc:
            logger.warning("Orbital Signal %s source unavailable: %s", name, type(exc).__name__)
            if cache_valid:
                return valid_cached, "stale_cache", type(exc).__name__
            return list(fixture), "fixture", type(exc).__name__

    @staticmethod
    def _fixture_payload(now):
        now_utc = _utc(now)
        launches = [
            {
                "id": "fixture-vikram",
                "name": "Vikram-I | Demo Flight",
                "net": (now_utc + timedelta(hours=8, minutes=40)).isoformat(),
                "status": "GO",
                "provider": "Skyroot Aerospace",
                "rocket": "Vikram-I",
                "mission": "Demo Flight",
                "orbit": "LEO",
                "pad": "First Launch Pad",
                "location": "Satish Dhawan Space Centre, India",
                "webcast_live": False,
            },
            {
                "id": "fixture-falcon",
                "name": "Falcon 9 | Starlink 17-39",
                "net": (now_utc + timedelta(days=2, hours=16, minutes=40)).isoformat(),
                "status": "GO",
                "provider": "SpaceX",
                "rocket": "Falcon 9",
                "mission": "Starlink 17-39",
                "orbit": "LEO",
                "pad": "SLC-4E",
                "location": "Vandenberg SFB, USA",
                "webcast_live": False,
            },
            {
                "id": "fixture-starship",
                "name": "Starship | Flight 13",
                "net": (now_utc + timedelta(days=3, hours=1, minutes=25)).isoformat(),
                "status": "GO",
                "provider": "SpaceX",
                "rocket": "Starship",
                "mission": "Flight 13",
                "orbit": "SUB",
                "pad": "Orbital Launch Pad 2",
                "location": "Starbase, TX, USA",
                "webcast_live": False,
            },
        ]
        markets = [
            {
                "id": "fixture-world-cup",
                "title": "WORLD CUP WINNER",
                "category": "SPORT",
                "leader": "SPAIN",
                "probability": 0.59,
                "change_24h": 0.008,
                "volume_24h": 4_610_850,
                "liquidity": 18_218_089,
                "heat": 82,
                "heat_label": "HOT",
                "end_date": (now_utc + timedelta(days=3)).isoformat(),
                "question": "Will Spain win the World Cup?",
            },
            {
                "id": "fixture-france-england",
                "title": "FRANCE vs ENGLAND",
                "category": "MATCH",
                "leader": "FRANCE",
                "probability": 0.52,
                "change_24h": 0.02,
                "volume_24h": 1_191_752,
                "liquidity": 2_052_124,
                "heat": 91,
                "heat_label": "HOT",
                "end_date": (now_utc + timedelta(days=1)).isoformat(),
                "question": "Will France win?",
            },
            {
                "id": "fixture-prime-minister",
                "title": "NEXT PRIME MINISTER",
                "category": "POLITICS",
                "leader": "LEADING PICK",
                "probability": 0.41,
                "change_24h": -0.012,
                "volume_24h": 875_000,
                "liquidity": 193_000,
                "heat": 58,
                "heat_label": "WARM",
                "end_date": (now_utc + timedelta(days=15)).isoformat(),
                "question": "Who will be the next prime minister?",
            },
        ]
        return {
            "schema": CACHE_SCHEMA,
            "launches": launches,
            "markets": markets,
            "status": {
                "aggregate": "fixture",
                "sources": {"launches": "fixture", "markets": "fixture"},
                "errors": {"launches": "", "markets": ""},
                "updated_at": now_utc.isoformat(),
            },
            "_source_provenance": SourceProvenance.LOCAL_FALLBACK.value,
        }

    @classmethod
    def _valid_aggregate_payload(cls, payload):
        if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA:
            return False
        if not cls._valid_items("launches", payload.get("launches")):
            return False
        if not cls._valid_items("markets", payload.get("markets")):
            return False
        status = payload.get("status")
        sources = status.get("sources") if isinstance(status, dict) else None
        return (
            isinstance(sources, dict)
            and set(sources) == {"launches", "markets"}
            and all(value in SOURCE_STATES for value in sources.values())
        )

    @staticmethod
    def _aggregate_status(states):
        values = set(states.values())
        if "fixture" in values:
            return "partial"
        if "stale_cache" in values:
            return "stale"
        if values == {"fresh_cache"}:
            return "cached"
        return "live"

    @staticmethod
    def _provenance(states):
        values = set(states.values())
        if "fixture" in values:
            return SourceProvenance.LOCAL_FALLBACK.value
        if "stale_cache" in values:
            return SourceProvenance.STALE_CACHE.value
        if values == {"fresh_cache"}:
            return SourceProvenance.FRESH_CACHE.value
        return SourceProvenance.LIVE.value

    def _payload(self, settings, device_config, now):
        aggregate_file = self._cache_dir() / "aggregate.json"
        if (settings or {}).get("_theme_render_only"):
            cached = self._read_json(aggregate_file, {})
            if self._valid_aggregate_payload(cached):
                return cached
            return self._fixture_payload(now)

        fixture = self._fixture_payload(now)
        force = coerce_bool((settings or {}).get("forceRefresh"), default=False)
        ttl_minutes = bounded_int((settings or {}).get("refreshMinutes"), 60, 15, 720)
        launches, launch_state, launch_error = self._resolve_source(
            "launches",
            now,
            force,
            lambda: fetch_launches(now=now),
            fixture["launches"],
            ttl_minutes,
        )
        markets, market_state, market_error = self._resolve_source(
            "markets",
            now,
            force,
            lambda: fetch_market_events(now=now),
            fixture["markets"],
            ttl_minutes,
        )
        launches = [dict(row) for row in launches[:4]]
        if launches:
            launches[0] = self._hydrate_primary_launch_photo(launches[0])
        states = {"launches": launch_state, "markets": market_state}
        payload = {
            "schema": CACHE_SCHEMA,
            "launches": launches,
            "markets": markets[:3],
            "status": {
                "aggregate": self._aggregate_status(states),
                "sources": states,
                "errors": {"launches": launch_error, "markets": market_error},
                "updated_at": _utc(now).isoformat(),
            },
            "_source_provenance": self._provenance(states),
        }
        self._write_json(aggregate_file, payload)
        return payload

    @staticmethod
    def _format_countdown(now, launch_time):
        seconds = max(0, int((_utc(launch_time) - _utc(now)).total_seconds()))
        total_minutes = seconds // 60
        days, remainder = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(remainder, 60)
        if days:
            return f"T- {days:02d}D {hours:02d}H"
        return f"T- {hours:02d}H {minutes:02d}M"

    def _now_for_device(self, device_config):
        timezone_name = "America/Los_Angeles"
        try:
            timezone_name = device_config.get_config("timezone") or timezone_name
        except Exception:
            pass
        for candidate in (timezone_name, "America/Los_Angeles", "UTC"):
            try:
                return datetime.now(ZoneInfo(candidate))
            except (KeyError, TypeError, ZoneInfoNotFoundError):
                continue
        return datetime.now(timezone.utc)

    def generate_image(self, settings, device_config):
        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get("_inkypi_theme") or self.resolve_theme(
            settings, device_config
        )
        dimensions = self.get_dimensions(device_config)
        now = self._now_for_device(device_config)
        payload = self._payload(settings, device_config, now)
        image = self._render_page(dimensions, payload, settings, now)
        return attach_source_provenance(
            image,
            payload.get("_source_provenance", SourceProvenance.LOCAL_FALLBACK.value),
            detail=PLUGIN_ID,
        )

    def _render_page(self, dimensions, payload, settings, now):
        if tuple(dimensions) != (800, 480):
            base = self._render_page((800, 480), payload, settings, now)
            return base.resize(tuple(dimensions), Image.Resampling.LANCZOS)

        palette = self._palette(settings)
        family = str((settings or {}).get("fontFamily") or "").strip() or DEFAULT_FONT
        image = Image.new("RGB", (800, 480), palette["paper"])
        draw = ImageDraw.Draw(image)
        boxes = self._panel_boxes(800, 480)
        self._draw_header(image, draw, boxes["header"], payload, now, palette, family)
        self._draw_launch(
            image,
            draw,
            boxes["launch"],
            payload.get("launches") or [],
            now,
            palette,
            family,
        )
        self._draw_markets(draw, boxes["markets"], payload.get("markets") or [], palette, family)
        self._draw_footer(draw, boxes["footer"], palette, family)
        return image

    def _draw_header(self, image, draw, box, payload, now, palette, family):
        left, top, right, bottom = box
        draw.rectangle(box, fill=palette["paper"])
        draw.line((left, bottom - 2, right, bottom - 2), fill=palette["rule"], width=3)
        subtitle_font = self._font(12, bold=True, family=family)
        meta_font = self._font(11, bold=True, family=family)
        try:
            wordmark = self._prepare_header_wordmark((247, 34), palette)
            wordmark_y = top + max(0, ((bottom - top) - wordmark.height) // 2 - 1)
            image.paste(wordmark, (left + 16, wordmark_y), wordmark)
        except (OSError, ValueError):
            title_font = self._font(29, bold=True, family=family)
            draw.text((16, 10), "ORBITAL SIGNAL", font=title_font, fill=palette["ink"])
        draw.text((279, 22), "GLOBAL LAUNCHES / CROWD ODDS", font=subtitle_font, fill=palette["ink"])

        aggregate = str((payload.get("status") or {}).get("aggregate") or "fixture").upper()
        chip_text = "LIVE DATA" if aggregate == "LIVE" else "CACHED" if aggregate == "CACHED" else aggregate
        chip_width = 82 if chip_text == "LIVE DATA" else 70
        chip_left = 620 - chip_width
        draw.rounded_rectangle(
            (chip_left, 12, 620, 45),
            radius=4,
            fill=palette["orange"] if aggregate == "LIVE" else palette["blue"],
        )
        chip_font = self._font(13, bold=True, family=family)
        text_box = draw.textbbox((0, 0), chip_text, font=chip_font)
        draw.text(
            (chip_left + (chip_width - (text_box[2] - text_box[0])) / 2, 20),
            chip_text,
            font=chip_font,
            fill=palette["white"],
        )
        updated = _parse_time((payload.get("status") or {}).get("updated_at")) or _utc(now)
        draw.text((638, 21), updated.strftime("%d %b / %H:%M UTC").upper(), font=meta_font, fill=palette["ink"])

    def _draw_rocket(self, draw, origin, palette):
        x, y = origin
        draw.arc((x - 34, y + 6, x + 78, y + 228), 102, 262, fill=palette["orange"], width=4)
        draw.ellipse((x + 66, y + 49, x + 77, y + 60), fill=palette["orange"])
        draw.polygon(
            [(x + 23, y), (x + 13, y + 31), (x + 13, y + 154), (x + 33, y + 154), (x + 33, y + 31)],
            fill=palette["white"],
            outline=palette["ink"],
        )
        draw.line((x + 23, y, x + 23, y + 154), fill=palette["ink"], width=2)
        draw.rectangle((x + 13, y + 54, x + 33, y + 66), fill=palette["ink"])
        draw.line((x + 13, y + 52, x + 33, y + 52), fill=palette["orange"], width=3)
        draw.polygon([(x + 13, y + 128), (x - 3, y + 157), (x + 13, y + 151)], fill=palette["ink"])
        draw.polygon([(x + 33, y + 128), (x + 49, y + 157), (x + 33, y + 151)], fill=palette["ink"])
        draw.polygon([(x + 17, y + 155), (x + 23, y + 188), (x + 29, y + 155)], fill=palette["orange"])
        draw.ellipse((x + 6, y + 177, x + 40, y + 210), fill=palette["orange"])
        draw.ellipse((x - 7, y + 195, x + 55, y + 226), fill=palette["orange"])
        draw.ellipse((x + 13, y + 179, x + 33, y + 226), fill=palette["white"])

    def _load_cached_launch_photo(self, launch):
        return launch_photo.load_cached_photo(
            self._photo_namespace(),
            (launch or {}).get("photo_cache_key"),
        )

    def _draw_launch_photo(self, image, draw, primary, top, palette, family):
        photo = self._load_cached_launch_photo(primary)
        if photo is None:
            return False
        try:
            fitted = launch_photo.rocket_preserving_crop(photo, (113, 247))
        except Exception as exc:
            logger.warning(
                "Orbital Signal launch photo crop failed: %s",
                type(exc).__name__,
            )
            return False
        finally:
            photo.close()

        photo_left = 16
        photo_top = top + 18
        photo_right = photo_left + fitted.width
        photo_bottom = photo_top + fitted.height
        image.paste(fitted, (photo_left, photo_top))
        fitted.close()

        credit = str(primary.get("photo_credit") or "").strip()
        if credit:
            credit_font = self._font(8, bold=True, family=family)
            label = self._fit_text(
                draw,
                f"PHOTO: {credit.upper()}",
                credit_font,
                photo_right - photo_left - 7,
            )
            strip_top = photo_bottom - 18
            draw.rectangle(
                (photo_left + 1, strip_top, photo_right - 2, photo_bottom - 2),
                fill=palette["ink"],
            )
            draw.text(
                (photo_left + 3, strip_top + 3),
                label,
                font=credit_font,
                fill=palette["paper"],
            )
        draw.rectangle(
            (photo_left, photo_top, photo_right - 1, photo_bottom - 1),
            outline=palette["rule"],
            width=1,
        )
        return True

    def _draw_launch(self, image, draw, box, launches, now, palette, family):
        left, top, right, bottom = box
        draw.rectangle(box, fill=palette["paper"])
        draw.line((right - 2, top, right - 2, bottom), fill=palette["rule"], width=3)
        if not launches:
            draw.text((22, top + 24), "NO LAUNCH DATA", font=self._font(24, True, family), fill=palette["ink"])
            return

        primary = launches[0]
        launch_time = _parse_time(primary.get("net")) or _utc(now)
        if not self._draw_launch_photo(image, draw, primary, top, palette, family):
            self._draw_rocket(draw, (49, top + 21), palette)
        draw.line((139, top + 18, 139, top + 265), fill=palette["rule"], width=2)

        tab_font = self._font(13, bold=True, family=family)
        draw.rectangle((153, top + 18, 282, top + 49), fill=palette["orange"])
        draw.text((165, top + 25), "NEXT LAUNCH", font=tab_font, fill=palette["white"])
        countdown = self._format_countdown(now, launch_time)
        draw.text((151, top + 57), countdown, font=self._font(39, True, family), fill=palette["ink"])

        rocket = str(primary.get("rocket") or "UPCOMING").strip().upper()
        rocket_font = self._fit_font(draw, rocket, 270, 31, 12, family)
        mission = self._fit_text(draw, str(primary.get("mission") or "MISSION"), self._font(22, True, family), 270)
        draw.text((151, top + 111), rocket, font=rocket_font, fill=palette["ink"])
        draw.text((153, top + 150), mission.upper(), font=self._font(22, True, family), fill=palette["ink"])

        status = str(primary.get("status") or "TBD").upper()
        status_color = palette["green"] if status in {"GO", "SUCCESS"} else palette["orange"]
        status_font = self._font(22, True, family)
        status_width = max(62, draw.textbbox((0, 0), status, font=status_font)[2] + 24)
        draw.rounded_rectangle(
            (153, top + 184, 153 + status_width, top + 226),
            radius=6,
            fill=palette["white"],
            outline=status_color,
            width=3,
        )
        draw.text((165, top + 191), status, font=status_font, fill=status_color)

        meta_font = self._font(15, True, family)
        date_line = launch_time.strftime("%d %b / %H:%M UTC").upper()
        location = self._fit_text(draw, str(primary.get("location") or "LOCATION TBD").upper(), meta_font, 270)
        provider_line = f"{primary.get('provider') or 'PROVIDER TBD'} / {primary.get('orbit') or 'TBD'}".upper()
        provider_line = self._fit_text(draw, provider_line, meta_font, 270)
        draw.text((153, top + 235), date_line, font=meta_font, fill=palette["ink"])
        draw.text((153, top + 258), location, font=meta_font, fill=palette["ink"])
        draw.text((153, top + 281), provider_line, font=meta_font, fill=palette["ink"])

        up_top = top + 310
        draw.line((16, up_top, right - 18, up_top), fill=palette["rule"], width=2)
        draw.text((20, up_top + 5), "UP NEXT", font=self._font(17, True, family), fill=palette["ink"])
        draw.line((16, up_top + 30, right - 18, up_top + 30), fill=palette["rule"], width=2)
        row_font = self._font(12, True, family)
        for index, launch in enumerate(launches[1:3]):
            y = up_top + 34 + index * 23
            when = _parse_time(launch.get("net"))
            date = when.strftime("%d %b") if when else "TBD"
            rocket_name = str(launch.get("rocket") or "ROCKET").upper()
            mission_name = str(launch.get("mission") or "MISSION").upper()
            rocket_font = self._fit_font(draw, rocket_name, 115, 12, 8, family)
            mission_font = self._fit_font(draw, mission_name, 185, 12, 8, family)
            draw.text((20, y), date.upper(), font=row_font, fill=palette["ink"])
            draw.rectangle((87, y + 5, 91, y + 14), fill=palette["orange"])
            draw.text((100, y), rocket_name, font=rocket_font, fill=palette["ink"])
            draw.rectangle((225, y + 5, 229, y + 14), fill=palette["orange"])
            draw.text((237, y), mission_name, font=mission_font, fill=palette["ink"])
            if index == 0:
                draw.line((16, y + 21, right - 18, y + 21), fill=palette["rule"], width=1)

    def _heat_color(self, heat, palette):
        if heat >= 80:
            return palette["red"]
        if heat >= 60:
            return palette["orange"]
        return palette["blue"]

    def _draw_thermometer(self, draw, origin, palette):
        x, y = origin
        draw.rounded_rectangle((x, y, x + 12, y + 28), radius=6, outline=palette["ink"], width=2)
        draw.ellipse((x - 3, y + 22, x + 15, y + 40), fill=palette["white"], outline=palette["ink"], width=2)
        draw.rectangle((x + 4, y + 10, x + 8, y + 30), fill=palette["red"])
        draw.ellipse((x + 1, y + 26, x + 11, y + 36), fill=palette["red"])

    def _draw_markets(self, draw, box, markets, palette, family):
        left, top, right, bottom = box
        draw.rectangle(box, fill=palette["paper"])
        draw.text((left + 15, top + 11), "MARKET HEAT", font=self._font(23, True, family), fill=palette["ink"])
        self._draw_thermometer(draw, (left + 197, top + 8), palette)
        draw.text((left + 226, top + 22), "VOLUME + 24H MOVE", font=self._font(10, True, family), fill=palette["ink"])

        if not markets:
            draw.text((left + 18, top + 72), "NO MARKET DATA", font=self._font(22, True, family), fill=palette["ink"])
            return

        card_top = top + 58
        card_height = 104
        gap = 7
        for index, market in enumerate(markets[:3]):
            y = card_top + index * (card_height + gap)
            heat = int(market.get("heat") or 0)
            color = self._heat_color(heat, palette)
            draw.rounded_rectangle(
                (left + 12, y, right - 12, y + card_height),
                radius=7,
                fill=palette["panel"],
                outline=color,
                width=3,
            )
            category = str(market.get("category") or "MARKET").upper()
            category_font = self._font(10, True, family)
            category_width = draw.textbbox((0, 0), category, font=category_font)[2] + 18
            draw.rounded_rectangle(
                (left + 23, y + 10, left + 23 + category_width, y + 32),
                radius=3,
                fill=color,
            )
            draw.text((left + 32, y + 14), category, font=category_font, fill=palette["white"])
            draw.text(
                (left + 31 + category_width, y + 15),
                f"HEAT {heat:02d}",
                font=self._font(9, True, family),
                fill=color,
            )

            title_font = self._font(13, True, family)
            title_width = right - left - 46
            title = self._fit_text(
                draw,
                str(market.get("title") or "MARKET").upper(),
                title_font,
                title_width,
            )
            draw.text((left + 23, y + 38), title, font=title_font, fill=palette["ink"])

            probability = max(0.0, min(1.0, float(market.get("probability") or 0.0)))
            percent = f"{probability * 100:.0f}%"
            percent_font = self._font(28, True, family)
            percent_width = draw.textbbox((0, 0), percent, font=percent_font)[2]
            percent_x = right - 24 - percent_width

            leader_font = self._font(18, True, family)
            leader_width = max(80, percent_x - (left + 23) - 10)
            leader = self._fit_text(
                draw,
                str(market.get("leader") or "TBD").upper(),
                leader_font,
                leader_width,
            )
            draw.text((left + 23, y + 59), leader, font=leader_font, fill=palette["ink"])
            draw.text((percent_x, y + 47), percent, font=percent_font, fill=color)

            bar_left, bar_right = left + 23, right - 23
            bar_top = y + 89
            draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_top + 9), radius=4, fill=palette["ink"])
            fill_right = bar_left + max(5, round((bar_right - bar_left) * probability))
            draw.rounded_rectangle((bar_left, bar_top, fill_right, bar_top + 9), radius=4, fill=color)
            split = max(bar_left + 3, min(bar_right - 3, fill_right))
            draw.line((split, bar_top, split, bar_top + 9), fill=palette["white"], width=2)
            change = float(market.get("change_24h") or 0.0) * 100
            change_text = f"{change:+.1f}%"
            change_font = self._font(11, True, family)
            change_width = draw.textbbox((0, 0), change_text, font=change_font)[2]
            draw.text((right - 23 - change_width, y + 13), change_text, font=change_font, fill=color)

    def _draw_footer(self, draw, box, palette, family):
        left, top, right, bottom = box
        draw.rectangle(box, fill=palette["paper"])
        draw.line((left, top, right, top), fill=palette["rule"], width=3)
        footer_font = self._font(11, True, family)
        draw.text((17, top + 10), "SOURCES  THE SPACE DEVS | POLYMARKET", font=footer_font, fill=palette["ink"])
        right_text = "CROWD PROBABILITY | NOT ADVICE"
        text_width = draw.textbbox((0, 0), right_text, font=footer_font)[2]
        draw.text((right - 17 - text_width, top + 10), right_text, font=footer_font, fill=palette["ink"])
