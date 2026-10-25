from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageChops, ImageDraw, ImageOps
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from runtime.refresh_contracts import TaskCancelled
from utils.cache_manager import CacheBudget
from utils.http_client import get_http_session
from utils.safe_image import safe_open_image
from utils.app_utils import (
    bounded_int,
    coerce_bool,
    get_available_font_names,
    get_base_ui_font,
    get_font,
)

from . import launch_photo
from .sources import _parse_time, _upload_url, _utc, fetch_launches, fetch_market_events


logger = logging.getLogger(__name__)

PLUGIN_ID = "orbital_signal"
DEFAULT_FONT = "Microsoft YaHei"
WORDMARK_ASSET_PATH = Path(__file__).with_name("assets") / "orbital-signal-wordmark.png"
# Polymarket's logo (Wikimedia Commons, public domain) heading the market columns.
POLYMARKET_LOGO_PATH = Path(__file__).with_name("assets") / "polymarket-logo.png"
POLYMARKET_LOGO_HEIGHT = 26
CACHE_SCHEMA = "orbital-signal-v1"
SOURCE_STATES = {"live", "fresh_cache", "stale_cache", "fixture"}
LAUNCH_PHOTO_CACHE_BUDGET = CacheBudget(
    30 * 24 * 60 * 60,
    24,
    48 * 1024 * 1024,
)
# Team logos, league icons and event art for the market columns (each at most 128 px).
MARKET_MARK_CACHE_BUDGET = CacheBudget(30 * 24 * 60 * 60, 96, 8 * 1024 * 1024)
# A matchup's two logos (px), the league icon in its chip and the leader's logo by its name.
MATCHUP_LOGO, LEAGUE_ICON, LEADER_LOGO = 28, 14, 14
# Each market column is themed by its event's art: kept at EVENT_ART_SIDE px, laid ART_H px tall
# across the column's top and fading into the paper from ART_FADE of its height down, the title
# written over its foot. The leading option shows its picture (Polymarket's own, its team's logo,
# or for a person their Wikipedia portrait) in a LEADER_AVATAR px disc by its name.
EVENT_ART_SIDE, ART_H, ART_FADE = 220, 96, 0.34
LEADER_AVATAR = 24
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_USER_AGENT = "InkyPi OrbitalSignal/1.0 (https://github.com/Feeengyuuu/EpaperSystem)"
PORTRAIT_STATE = "leader_portraits.json"
PORTRAIT_DAYS, PORTRAIT_MISS_DAYS = 30, 7
# A Wikipedia summary counts as a person's portrait only with one of these in its description.
PERSON_HINT = re.compile(r"\(born \d{4}\)|footballer|player|politician|president|minister|singer|rapper|actor|actress|"
                         r"businessman|businesswoman|coach|driver|boxer|athlete|candidate|leader", re.IGNORECASE)

# Mission-control layout: the launch side with a photo, flip-clock countdown,
# fact grid and launch timeline; the market side with three gauge columns.
LAUNCH_SPLIT_X = 462
PHOTO_BOX = (16, 72, 124, 272)
FLIP_CARD = (34, 58)
MARKET_COLUMNS_HEIGHT = 300
# The market facts' icons (volume, heat, end) in px.
MARKET_FACT_ICON = 13
# The heat dials (radius in px), drawn this many times larger and then scaled down.
GAUGE_RADIUS, GAUGE_SCALE = 32, 4
MARKET_ART_PATH = Path(__file__).with_name("assets") / "market-console.png"
# A league name too long for its market's tag gives way to its usual short form.
LEAGUE_SHORT_NAMES = {
    "COLLEGE FOOTBALL": "NCAAF", "NCAA FOOTBALL": "NCAAF", "COLLEGE BASKETBALL": "NCAAB",
    "NCAA BASKETBALL": "NCAAB", "PREMIER LEAGUE": "EPL", "ENGLISH PREMIER LEAGUE": "EPL",
    "CHAMPIONS LEAGUE": "UCL", "UEFA CHAMPIONS LEAGUE": "UCL", "EUROPA LEAGUE": "UEL",
    "UEFA EUROPA LEAGUE": "UEL", "MAJOR LEAGUE BASEBALL": "MLB", "MAJOR LEAGUE SOCCER": "MLS",
    "FORMULA 1": "F1", "FORMULA ONE": "F1", "NATIONAL HOCKEY LEAGUE": "NHL",
    "NATIONAL FOOTBALL LEAGUE": "NFL", "NATIONAL BASKETBALL ASSOCIATION": "NBA",
}
CATEGORY_COLORS = {"ECONOMY": "blue", "SPORT": "orange", "MATCH": "orange", "POLITICS": "red",
                   "CRYPTO": "orange", "TECH": "green"}
# Fact-grid art: two-tone icons (black shapes take the ink, orange accents the page orange),
# launch providers' official logos by Launch Library agency id, and pad country flags.
# Logos whose own colours vanish on one paper show there as a one-ink silhouette, as a brand's
# reversed artwork would: dark wordmarks by night, white-lettered ones by day (Launch Library ids).
PROVIDER_LOGO_SILHOUETTE = {
    "night": frozenset({17, 37, 96, 98, 115, 121, 124, 141, 257, 259, 265, 272, 1040}),
    "day": frozenset({147, 1021}),
}
ICON_DIR = Path(__file__).with_name("assets") / "icons"
PROVIDER_LOGO_DIR = Path(__file__).with_name("assets") / "providers"
FLAG_DIR = Path(__file__).with_name("assets") / "flags"
FACT_ICON = 24
FACT_ICONS = {"WINDOW": "clock", "PAD": "tower", "ORBIT": "orbit", "WORLD": "world", "WEATHER": "weather"}
MISSION_ICONS = {
    "communications": "comms", "earth science": "earth", "government/top secret": "classified",
    "human exploration": "human", "tourism": "human", "resupply": "resupply", "test flight": "test",
    "technology": "test", "navigation": "navigation", "astrophysics": "science", "heliophysics": "science",
    "space science": "science", "planetary science": "planetary", "robotic exploration": "planetary",
    "dedicated rideshare": "rideshare",
}
# Mission types too long for a fact cell, in the words a launch broadcast would use.
MISSION_SHORT = {"government/top secret": "CLASSIFIED", "human exploration": "CREWED", "dedicated rideshare": "RIDESHARE",
                 "planetary science": "PLANETARY", "robotic exploration": "ROBOTIC", "communications": "COMMS"}

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


@lru_cache(maxsize=64)
def _two_tone_icon(name, size, ink, accent):
    """The icon at ``size``: black shapes in ``ink``, orange accents in ``accent`` (orange share = red - blue)."""
    try:
        with safe_open_image(ICON_DIR / f"{name}.png") as source:
            icon = source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None
    red, _green, blue, alpha = icon.split()
    tinted = Image.composite(Image.new("RGB", icon.size, accent), Image.new("RGB", icon.size, ink),
                             ImageChops.subtract(red, blue))
    tinted.putalpha(alpha)
    return tinted


@lru_cache(maxsize=32)
def _provider_logo(provider_id):
    """The provider's official logo in its own colours, straight on the page."""
    if type(provider_id) is not int:
        return None
    path = PROVIDER_LOGO_DIR / f"{provider_id}.png"
    if not path.is_file():
        return None
    try:
        with safe_open_image(path) as source:
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=32)
def _flag(country_code):
    code = str(country_code or "").strip().lower()
    if len(code) != 2 or not code.isalpha() or not (FLAG_DIR / f"{code}.png").is_file():
        return None
    try:
        with safe_open_image(FLAG_DIR / f"{code}.png") as source:
            return source.convert("RGB")
    except (OSError, ValueError):
        return None


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

    @staticmethod
    @lru_cache(maxsize=1)
    def _cached_polymarket_logo():
        with safe_open_image(POLYMARKET_LOGO_PATH) as source:
            return source.convert("RGBA")

    def _polymarket_logo(self, height, palette):
        """Polymarket's logo ``height`` px tall in the page's ink (it is a one-colour mark), or None."""
        try:
            logo = self._cached_polymarket_logo()
        except (OSError, ValueError):
            return None
        logo = logo.resize((max(1, round(logo.width * height / logo.height)), int(height)), Image.Resampling.LANCZOS)
        inked = Image.new("RGBA", logo.size, (*palette["ink"][:3], 255))
        inked.putalpha(logo.getchannel("A"))
        return inked

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
                "launch": (0, 58, LAUNCH_SPLIT_X, 448),
                "markets": (LAUNCH_SPLIT_X, 58, 800, 448),
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

    def _mark_namespace(self):
        return self.managed_cache_namespace(self._cache_dir() / "market_marks", MARKET_MARK_CACHE_BUDGET)

    def _hydrate_market_marks(self, market):
        """The market's official marks fetched into the cache: each side's team logo, the league's
        icon and, without a matchup, the event's art. Their cache keys ride along in the row, so a
        theme redraw needs no network."""
        row = dict(market or {})
        namespace = self._mark_namespace()

        def mark(url):
            try:
                return launch_photo.cache_mark(_upload_url(url), namespace, allow_network=True)
            except TaskCancelled:
                raise
            except Exception as exc:
                logger.warning("Orbital Signal market mark unavailable: %s", type(exc).__name__)
                return ""

        teams = [dict(team, mark=mark(team.get("logo")) or mark(team.get("fallback_logo")))
                 for team in row.get("teams") or [] if isinstance(team, dict)]
        row["teams"] = teams
        row["league_mark"] = mark(row.get("league_icon"))
        if not (len(teams) == 2 and all(team["mark"] for team in teams)):
            row["event_mark"] = mark(row.get("event_icon"))
        try:
            row["event_art"] = launch_photo.cache_mark(_upload_url(row.get("event_image") or row.get("event_icon")), namespace,
                                                       allow_network=True, side=EVENT_ART_SIDE)
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Orbital Signal event art unavailable: %s", type(exc).__name__)
        leader = str(row.get("leader") or "")
        is_team = any(leader.casefold() in {str(team.get(key) or "").casefold() for key in ("name", "alias")}
                      for team in teams)
        portrait = row.get("leader_image") or ("" if is_team else self._leader_portrait_url(leader))
        row["leader_mark"] = mark(portrait) if portrait else ""
        return row

    def _leader_portrait_url(self, name):
        """A person's portrait from their English Wikipedia summary ("" for anything that is not a
        person's page), remembered for PORTRAIT_DAYS (a miss for PORTRAIT_MISS_DAYS)."""
        name = " ".join(str(name or "").split())
        if len(name.split()) < 2 or len(name) > 60:
            return ""
        path = self._cache_dir() / PORTRAIT_STATE
        state = self._read_json(path, {})
        now = time.time()
        known = state.get(name) or {}
        if now - float(known.get("ts") or 0) < (PORTRAIT_DAYS if known.get("url") else PORTRAIT_MISS_DAYS) * 86400:
            return known.get("url") or ""
        url = ""
        try:
            response = get_http_session().get(WIKI_SUMMARY_URL.format(title=quote(name.replace(" ", "_"), safe="")),
                                              headers={"User-Agent": WIKI_USER_AGENT, "Accept": "application/json"},
                                              timeout=(4, 12))
            summary = response.json() if response.status_code == 200 else {}
            if (summary.get("type") == "standard" and PERSON_HINT.search(str(summary.get("description") or ""))):
                url = str((summary.get("thumbnail") or {}).get("source") or "")
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Orbital Signal portrait lookup failed: %s", type(exc).__name__)
            return ""
        state[name] = {"url": url, "ts": now}
        self._write_json(path, {key: value for key, value in state.items()
                                if now - float((value or {}).get("ts") or 0) < PORTRAIT_DAYS * 86400})
        return url

    def _market_mark(self, key, size, *, rounded=False):
        """A cached mark contained in ``size`` px (event art cover-cropped to a rounded square)."""
        mark = launch_photo.load_cached_mark(self._mark_namespace(), key) if key else None
        if mark is None:
            return None
        if not rounded:
            box = mark.getchannel("A").getbbox()
            mark = mark.crop(box) if box else mark
            return ImageOps.contain(mark, (size, size), method=Image.Resampling.LANCZOS)
        art = ImageOps.fit(mark.convert("RGB"), (size, size), method=Image.Resampling.LANCZOS).convert("RGBA")
        corner = Image.new("L", (size, size), 0)
        ImageDraw.Draw(corner).rounded_rectangle((0, 0, size - 1, size - 1), radius=max(3, size // 5), fill=255)
        art.putalpha(corner)
        return art

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
            "markets": [self._hydrate_market_marks(row) for row in markets[:3]],
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
        self._draw_markets(draw, boxes["markets"], payload.get("markets") or [], palette, family, image=image)
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

    def _draw_rocket_icon(self, draw, center_x, top, height, color, palette):
        """A flat rocket ``height`` px tall: nose, body, fins and a flame."""
        width = max(4, height // 4)
        left, right = center_x - width // 2, center_x + width // 2
        body_top, body_bottom = top + height // 4, top + height * 3 // 4
        draw.polygon([(center_x, top), (right, body_top), (left, body_top)], fill=color)
        draw.rectangle((left, body_top, right, body_bottom), fill=color)
        fin = max(2, width // 2)
        draw.polygon([(left, body_bottom - fin * 2), (left - fin, body_bottom + 1), (left, body_bottom)], fill=color)
        draw.polygon([(right, body_bottom - fin * 2), (right + fin, body_bottom + 1), (right, body_bottom)], fill=color)
        draw.polygon([(left + 1, body_bottom + 1), (center_x, top + height), (right - 1, body_bottom + 1)],
                     fill=palette["orange"] if color != palette["orange"] else palette["red"])

    def _load_cached_launch_photo(self, launch):
        return launch_photo.load_cached_photo(
            self._photo_namespace(),
            (launch or {}).get("photo_cache_key"),
        )

    def _draw_launch_photo(self, image, draw, primary, palette, family):
        """The next launch's photo in the left column, or a drawn rocket on an orbit arc."""
        left, top, right, bottom = PHOTO_BOX
        photo = self._load_cached_launch_photo(primary)
        fitted = None
        if photo is not None:
            try:
                fitted = launch_photo.rocket_preserving_crop(photo, (right - left, bottom - top))
            except Exception as exc:
                logger.warning("Orbital Signal launch photo crop failed: %s", type(exc).__name__)
            finally:
                photo.close()
        if fitted is None:
            draw.rectangle(PHOTO_BOX, fill=palette["panel"])
            draw.arc((left - 40, top + 30, right + 20, bottom + 60), 200, 320, fill=palette["orange"], width=3)
            self._draw_rocket_icon(draw, (left + right) // 2, top + 34, 132, palette["ink"], palette)
        else:
            image.paste(fitted, (left, top))
            fitted.close()
            credit = str(primary.get("photo_credit") or "").strip()
            if credit:
                credit_font = self._font(8, bold=True, family=family)
                label = self._fit_text(draw, f"PHOTO: {credit.upper()}", credit_font, right - left - 7)
                draw.rectangle((left + 1, bottom - 17, right - 2, bottom - 2), fill=palette["ink"])
                draw.text((left + 3, bottom - 15), label, font=credit_font, fill=palette["paper"])
        draw.rectangle((left, top, right - 1, bottom - 1), outline=palette["rule"], width=1)

    @staticmethod
    def _countdown_units(now, launch_time):
        """(('00', 'H'), ('53', 'M')) inside a day, (('02', 'D'), ('08', 'H')) beyond it."""
        minutes = max(0, int((_utc(launch_time) - _utc(now)).total_seconds())) // 60
        days, remainder = divmod(minutes, 24 * 60)
        hours, mins = divmod(remainder, 60)
        if days:
            return (f"{min(days, 99):02d}", "D"), (f"{hours:02d}", "H")
        return (f"{hours:02d}", "H"), (f"{mins:02d}", "M")

    def _draw_flip_countdown(self, draw, x, y, units, palette, family):
        """'T-' then two flip-card pairs split by a colon, each pair's unit below it."""
        card_w, card_h = FLIP_CARD
        prefix_font = self._font(34, True, family)
        draw.text((x, y + 8), "T-", font=prefix_font, fill=palette["ink"])
        x += draw.textbbox((0, 0), "T-", font=prefix_font)[2] + 8
        digit_font = self._font(44, True, family)
        unit_font = self._font(10, True, family)
        middle = y + card_h // 2
        for index, (digits, unit) in enumerate(units):
            if index:
                for dot_y in (middle - 12, middle + 6):
                    draw.rectangle((x + 2, dot_y, x + 7, dot_y + 5), fill=palette["ink"])
                x += 12
            pair_left = x
            for digit in digits:
                draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=4, fill=palette["ink"])
                bounds = draw.textbbox((0, 0), digit, font=digit_font)
                draw.text((x + (card_w - (bounds[2] - bounds[0])) / 2 - bounds[0],
                           y + (card_h - (bounds[3] - bounds[1])) / 2 - bounds[1]),
                          digit, font=digit_font, fill=palette["paper"])
                # Hinge notches on both sides, as on a split-flap card.
                draw.rectangle((x, middle, x + 2, middle + 1), fill=palette["paper"])
                draw.rectangle((x + card_w - 2, middle, x + card_w, middle + 1), fill=palette["paper"])
                x += card_w + 3
            unit_w = draw.textbbox((0, 0), unit, font=unit_font)[2]
            draw.text((pair_left + (x - 3 - pair_left - unit_w) / 2, y + card_h + 2), unit, font=unit_font,
                      fill=palette["ink"])
        return x

    @staticmethod
    def _short_pad(name):
        """'Rocket Lab Launch Complex 1B' → 'LC-1B', 'Space Launch Complex 4E' → 'SLC-4E'."""
        name = str(name or "").strip()
        for long_form, short_form in (("Space Launch Complex ", "SLC-"), ("Launch Complex ", "LC-"),
                                      ("Launch Pad ", "LP-")):
            if long_form.casefold() in name.casefold():
                return short_form + name[name.casefold().index(long_form.casefold()) + len(long_form):].strip()
        return name

    @staticmethod
    def _launch_site(launch):
        """The place of a pad location: 'Mahia Peninsula', 'Taiyuan', 'Vandenberg SFB'."""
        parts = [part.strip() for part in str(launch.get("location") or "").split(",") if part.strip()]
        place = next((part for part in parts if "launch complex" not in part.casefold()), parts[0] if parts else "")
        for suffix in (" Satellite Launch Center", " Space Launch Center", " Space Launch Site", " Launch Center",
                       " Space Center"):
            if place.casefold().endswith(suffix.casefold()):
                return place[: -len(suffix)].strip()
        return place

    @staticmethod
    def _launch_facts(launch):
        """Label/value pairs for the fact grid under the next launch."""
        start, end = _parse_time(launch.get("window_start")), _parse_time(launch.get("window_end"))
        if start and end and end > start:
            window = f"{start:%H:%M}-{end:%H:%M} UTC"
        elif start:
            window = f"{start:%H:%M} UTC INSTANT"
        else:
            window = "TBD"
        country = str(launch.get("country_code") or "").strip()
        pad = " / ".join(part for part in (OrbitalSignal._short_pad(launch.get("pad")), country) if part) or "TBD"
        provider = str(launch.get("provider") or "").strip()
        if len(provider) > 14 or not provider:
            provider = str(launch.get("provider_abbrev") or provider or "TBD").strip()
        count = launch.get("provider_launches_year")
        if isinstance(count, int) and count > 0:
            provider = f"{provider} / #{count} YTD"
        weather = launch.get("weather_go")
        world = launch.get("orbital_launches_year")
        if isinstance(weather, int):
            last = ("WEATHER", f"{weather}% GO")
        elif isinstance(world, int) and world > 0:
            last = ("WORLD", f"ORBITAL #{world} YTD")
        else:
            last = ("WEATHER", "NO FORECAST")
        mission_type = str(launch.get("mission_type") or "").strip()
        return [
            ("WINDOW", window),
            ("PAD", pad),
            ("ORBIT", str(launch.get("orbit") or "TBD")),
            ("PROVIDER", provider),
            ("MISSION", MISSION_SHORT.get(mission_type.casefold(), mission_type)
             if mission_type and mission_type.casefold() != "unknown" else "TBD"),
            last,
        ]

    @staticmethod
    def _fact_icon(label, launch):
        """The icon for a fact cell; a mission's icon follows its type (none for an unknown type)."""
        if label == "MISSION":
            return MISSION_ICONS.get(str(launch.get("mission_type") or "").strip().casefold())
        return FACT_ICONS.get(label)

    @staticmethod
    def _paste_icon(image, name, x, y, size, palette):
        icon = _two_tone_icon(name, size, tuple(palette["ink"]), tuple(palette["orange"]))
        if icon is not None:
            image.paste(icon, (int(x), int(y)), icon)

    @staticmethod
    def _paste_flag(image, draw, flag, x, y, palette, size=(18, 12)):
        x, y = int(x), int(y)
        image.paste(flag.resize(size, Image.Resampling.LANCZOS), (x, y))
        draw.rectangle((x - 1, y - 1, x + size[0], y + size[1]), outline=palette["rule"], width=1)

    def _draw_provider_cell(self, image, draw, launch, cell, label_font, palette, family):
        """The provider as its official logo, this year's launch count in the label; False without a logo.

        The logo sits straight on the page in its own colours, or as an ink silhouette on the
        paper where those colours would vanish (PROVIDER_LOGO_SILHOUETTE).
        """
        provider_id = launch.get("provider_id")
        logo = _provider_logo(provider_id)
        if logo is None:
            return False
        x, y, width = cell
        count = launch.get("provider_launches_year")
        label = f"PROVIDER  #{count} YTD" if isinstance(count, int) and count > 0 else "PROVIDER"
        draw.text((x, y), label, font=label_font, fill=palette["orange"])
        fitted = logo.copy()
        fitted.thumbnail((int(width), 18), Image.Resampling.LANCZOS)
        paper = "night" if sum(palette["paper"]) < 300 else "day"
        if provider_id in PROVIDER_LOGO_SILHOUETTE[paper]:
            silhouette = Image.new("RGBA", fitted.size, (*palette["ink"], 0))
            silhouette.putalpha(fitted.getchannel("A"))
            fitted = silhouette
        left, top = int(x), int(y + 12)
        image.paste(fitted, (left, top + 1), fitted)
        right = left + fitted.width
        if fitted.width < fitted.height * 2.5:
            # A badge says little alone: the provider's short name follows it.
            name = str(launch.get("provider_abbrev") or launch.get("provider") or "").strip().upper()
            name_font = self._font(12, True, family)
            room = x + width - right - 5
            if name and room > 12:
                draw.text((right + 5, y + 13), self._fit_text(draw, name, name_font, room), font=name_font,
                          fill=palette["ink"])
        return True

    def _draw_when_and_where(self, image, draw, launch, launch_time, now, left, right, top, palette, family):
        """Two plain lines: the local day and time (UTC after it), then the launch site with its flag."""
        local = launch_time.astimezone(now.tzinfo) if now.tzinfo else launch_time
        days = (local.date() - (now.astimezone(now.tzinfo) if now.tzinfo else now).date()).days
        day = "TODAY" if days == 0 else "TOMORROW" if days == 1 else f"{local:%a %d %b}".upper()
        lead = f"{day} {local:%H:%M} {local.tzname() or 'LOCAL'}".upper()
        lead_font = self._font(14, True, family)
        self._paste_icon(image, "clock", left, top, 15, palette)
        draw.text((left + 20, top - 2), lead, font=lead_font, fill=palette["ink"])
        if local.utcoffset():
            utc_left = left + 20 + draw.textbbox((0, 0), lead, font=lead_font)[2] + 8
            utc = f"{launch_time:%H:%M} UTC"
            utc_font = self._font(11, True, family)
            if utc_left + draw.textbbox((0, 0), utc, font=utc_font)[2] <= right:
                draw.text((utc_left, top + 1), utc, font=utc_font, fill=palette["muted"])

        site = (self._launch_site(launch) or str(launch.get("location") or "")).strip().upper()
        if not site:
            return
        place_top = top + 20
        flag = _flag(launch.get("country_code"))
        room = right - left - 20 - (26 if flag is not None else 0)
        site_font = self._fit_font(draw, site, room, 13, 10, family)
        shown = self._fit_text(draw, site, site_font, room)
        self._paste_icon(image, "pin", left, place_top, 15, palette)
        draw.text((left + 20, place_top - 1), shown, font=site_font, fill=palette["ink"])
        if flag is not None:
            self._paste_flag(image, draw, flag, left + 20 + draw.textbbox((0, 0), shown, font=site_font)[2] + 8,
                             place_top + 2, palette)

    def _draw_launch(self, image, draw, box, launches, now, palette, family):
        left, top, right, bottom = box
        draw.rectangle(box, fill=palette["paper"])
        draw.line((right - 2, top, right - 2, bottom), fill=palette["rule"], width=3)
        if not launches:
            draw.text((22, top + 24), "NO LAUNCH DATA", font=self._font(24, True, family), fill=palette["ink"])
            return

        primary = launches[0]
        launch_time = _parse_time(primary.get("net")) or _utc(now)
        self._draw_launch_photo(image, draw, primary, palette, family)
        text_left, text_right = PHOTO_BOX[2] + 12, right - 16

        tab_font = self._font(12, bold=True, family=family)
        tab_w = draw.textbbox((0, 0), "NEXT LAUNCH", font=tab_font)[2] + 18
        draw.rectangle((text_left, PHOTO_BOX[1], text_left + tab_w, PHOTO_BOX[1] + 21), fill=palette["orange"])
        draw.text((text_left + 9, PHOTO_BOX[1] + 3), "NEXT LAUNCH", font=tab_font, fill=palette["white"])
        if primary.get("webcast_live"):
            live_left = text_left + tab_w + 8
            live_w = draw.textbbox((0, 0), "WEBCAST LIVE", font=tab_font)[2] + 24
            draw.rectangle((live_left, PHOTO_BOX[1], live_left + live_w, PHOTO_BOX[1] + 21), outline=palette["red"],
                           width=2)
            draw.ellipse((live_left + 7, PHOTO_BOX[1] + 7, live_left + 14, PHOTO_BOX[1] + 14), fill=palette["red"])
            draw.text((live_left + 18, PHOTO_BOX[1] + 3), "WEBCAST LIVE", font=tab_font, fill=palette["red"])

        clock_top = PHOTO_BOX[1] + 30
        clock_right = self._draw_flip_countdown(
            draw, text_left, clock_top, self._countdown_units(now, launch_time), palette, family
        )

        status = str(primary.get("status") or "TBD").upper()
        status_color = palette["green"] if status in {"GO", "SUCCESS"} else palette["orange"]
        status_font = self._fit_font(draw, status, text_right - clock_right - 30, 26, 12, family)
        status_box = (max(clock_right + 6, text_right - 86), clock_top + 4, text_right, clock_top + FLIP_CARD[1] - 4)
        draw.rounded_rectangle(status_box, radius=6, fill=palette["white"], outline=status_color, width=3)
        bounds = draw.textbbox((0, 0), status, font=status_font)
        draw.text(((status_box[0] + status_box[2] - (bounds[2] - bounds[0])) / 2 - bounds[0],
                   (status_box[1] + status_box[3] - (bounds[3] - bounds[1])) / 2 - bounds[1]),
                  status, font=status_font, fill=status_color)

        width = text_right - text_left
        rocket = str(primary.get("rocket") or "UPCOMING").strip().upper()
        draw.text((text_left, clock_top + FLIP_CARD[1] + 14), rocket,
                  font=self._fit_font(draw, rocket, width, 30, 14, family), fill=palette["ink"])
        mission = str(primary.get("mission") or "MISSION").strip().upper()
        mission_font = self._fit_font(draw, mission, width, 16, 12, family)
        draw.text((text_left, clock_top + FLIP_CARD[1] + 48), self._fit_text(draw, mission, mission_font, width),
                  font=mission_font, fill=palette["ink"])
        self._draw_when_and_where(image, draw, primary, launch_time, now, text_left, text_right,
                                  clock_top + FLIP_CARD[1] + 71, palette, family)

        grid_top = PHOTO_BOX[3] + 8
        draw.line((16, grid_top, right - 18, grid_top), fill=palette["rule"], width=2)
        label_font = self._font(9, True, family)
        cell_w = (right - 18 - 16) / 3
        for index, (label, value) in enumerate(self._launch_facts(primary)):
            cell_x = 16 + (index % 3) * cell_w + (0 if index % 3 == 0 else 8)
            cell_y = grid_top + 6 + (index // 3) * 34
            if label == "PROVIDER" and self._draw_provider_cell(image, draw, primary, (cell_x, cell_y, cell_w - 12),
                                                                label_font, palette, family):
                continue
            icon = self._fact_icon(label, primary)
            text_x = cell_x + (FACT_ICON + 6 if icon else 0)
            text_w = cell_w - 12 - (text_x - cell_x)
            if icon:
                self._paste_icon(image, icon, cell_x, cell_y + 1, FACT_ICON, palette)
            draw.text((text_x, cell_y), label, font=label_font, fill=palette["orange"])
            value = value.upper()
            flag = _flag(primary.get("country_code")) if label == "PAD" else None
            if flag is not None:
                # The pad's country reads from its flag rather than its code.
                value = self._short_pad(primary.get("pad")).upper() or "TBD"
                text_w -= 22
            value_font = self._fit_font(draw, value, text_w, 12, 9, family)
            shown = self._fit_text(draw, value, value_font, text_w)
            draw.text((text_x, cell_y + 12), shown, font=value_font, fill=palette["ink"])
            if flag is not None:
                flag_x = text_x + draw.textbbox((0, 0), shown, font=value_font)[2] + 6
                self._paste_flag(image, draw, flag, flag_x, cell_y + 14, palette)
        for column in (1, 2):
            divider = 16 + column * cell_w
            draw.line((divider, grid_top + 8, divider, grid_top + 68), fill=palette["rule"], width=1)
        self._draw_timeline(draw, (16, grid_top + 76, right - 18, bottom - 6), launches, now, palette, family)

    @staticmethod
    def _gap_label(earlier, later):
        """'+3H 15M', '+2D 4H' or '+45M' between two launch times."""
        minutes = max(0, int((later - earlier).total_seconds() // 60))
        days, remainder = divmod(minutes, 24 * 60)
        hours, mins = divmod(remainder, 60)
        if days:
            return f"+{days}D {hours}H"
        if hours:
            return f"+{hours}H {mins:02d}M"
        return f"+{mins}M"

    def _draw_timeline(self, draw, box, launches, now, palette, family):
        """The coming launches as evenly spaced stops on a line, so close and
        far launches read alike: each stop's local time above it, rocket and
        mission below, and the time to the next stop on the line between."""
        left, top, right, bottom = box
        draw.line((left, top, right, top), fill=palette["rule"], width=2)
        title_font = self._font(10, True, family)
        draw.text((left + 2, top + 5), "NEXT LAUNCHES", font=title_font, fill=palette["ink"])
        zone = f"LOCAL TIME {now.tzname() or 'UTC'}".upper()
        draw.text((left + 10 + draw.textbbox((0, 0), "NEXT LAUNCHES", font=title_font)[2], top + 6), zone,
                  font=self._font(9, True, family), fill=palette["muted"])
        stops = [(launch, when) for launch in launches[:4] if (when := _parse_time(launch.get("net"))) is not None]
        if not stops:
            return
        line_y, inset = top + 38, 50
        if len(stops) > 1:
            xs = [left + inset + (right - left - 2 * inset) * index / (len(stops) - 1) for index in range(len(stops))]
        else:
            xs = [(left + right) / 2]
        draw.line((xs[0], line_y, xs[-1], line_y), fill=palette["ink"], width=2)
        slot = min(150, (right - left) / len(stops) - 6)
        gap_font = self._font(8, True, family)
        for index in range(len(stops) - 1):
            gap = self._gap_label(stops[index][1], stops[index + 1][1])
            gap_w = draw.textbbox((0, 0), gap, font=gap_font)[2]
            middle = (xs[index] + xs[index + 1]) / 2
            draw.rectangle((middle - gap_w / 2 - 3, line_y - 6, middle + gap_w / 2 + 3, line_y + 6), fill=palette["paper"])
            draw.text((middle - gap_w / 2, line_y - 6), gap, font=gap_font, fill=palette["muted"])

        def centered(text, x, y, font, fill):
            text_w = draw.textbbox((0, 0), text, font=font)[2]
            draw.text((min(max(left, x - text_w / 2), right - text_w), y), text, font=font, fill=fill)

        for index, ((launch, when), x) in enumerate(zip(stops, xs)):
            status = str(launch.get("status") or "").upper()
            color = palette["orange"] if index == 0 else palette["green"] if status == "GO" else palette["blue"]
            local = when.astimezone(now.tzinfo) if now.tzinfo else when
            stamp = f"{local:%H:%M}" if local.date() == now.date() else f"{local:%a %H:%M}".upper()
            centered(stamp, x, line_y - 22, self._font(11, True, family), color)
            draw.ellipse((x - 6, line_y - 6, x + 6, line_y + 6), fill=color, outline=palette["ink"], width=1)
            for row, (text, size) in enumerate(((launch.get("rocket"), 10), (launch.get("mission"), 9))):
                text = str(text or "").strip().upper()
                if text:
                    font = self._fit_font(draw, text, slot, size, 8, family)
                    centered(self._fit_text(draw, text, font, slot), x, line_y + 9 + row * 13, font,
                             palette["ink"] if row == 0 else palette["muted"])

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

    @staticmethod
    def _money(value):
        amount = max(0.0, float(value or 0.0))
        for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if amount >= divisor:
                return f"${amount / divisor:.1f}{suffix}"
        return f"${amount:.0f}"

    def _wrap_lines(self, draw, text, font, width, max_lines=2):
        """Greedy word wrap into at most ``max_lines``, the last one ellipsised."""
        words, lines, current = str(text or "").split(), [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= width or not current:
                current = candidate
                continue
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        consumed = sum(len(line.split()) for line in lines)
        if lines and consumed < len(words):
            lines[-1] = self._fit_text(draw, lines[-1] + " ...", font, width)
        return [self._fit_text(draw, line, font, width) for line in lines]

    def _draw_gauge(self, draw, center, radius, probability, color, palette, image=None):
        """A dial filled to ``probability`` in the market's heat colour: a ticked bezel, a track
        tinted in that colour, the value arc with rounded ends and a tapered needle on a hub. The
        three heat colours make a matching set. Drawn GAUGE_SCALE times larger and scaled down
        onto ``image``; without one, the plain dial straight onto ``draw``."""
        cx, cy = center
        end = 180 + 180 * probability
        if image is None:
            bounds = (cx - radius, cy - radius, cx + radius, cy + radius)
            draw.arc(bounds, 180, 360, fill=palette["muted"], width=3)
            draw.arc(bounds, 180, end, fill=color, width=9)
            tip = (cx + (radius - 9) * math.cos(math.radians(end)), cy + (radius - 9) * math.sin(math.radians(end)))
            draw.line((cx, cy, *tip), fill=palette["ink"], width=3)
            draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=palette["ink"])
            return
        k = GAUGE_SCALE
        half_w, height = radius + 8, radius + 10
        layer = Image.new("RGBA", (2 * half_w * k, height * k), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        ox, oy = half_w * k, (radius + 6) * k

        def at(angle, length):
            return ox + length * math.cos(math.radians(angle)), oy + length * math.sin(math.radians(angle))

        bezel = (radius + 4) * k
        pen.arc((ox - bezel, oy - bezel, ox + bezel, oy + bezel), 180, 360, fill=(*palette["muted"], 150), width=k)
        for step in range(21):
            angle = 180 + 9 * step
            major = step % 5 == 0
            pen.line((*at(angle, bezel - (5 if major else 3) * k), *at(angle, bezel)),
                     fill=(*palette["ink" if major else "muted"], 255 if major else 170), width=(2 if major else 1) * k)
        track_w = max(5, round(radius * 0.22)) * k
        track_r = radius * k - track_w / 2
        track = (ox - track_r - track_w / 2, oy - track_r - track_w / 2, ox + track_r + track_w / 2, oy + track_r + track_w / 2)
        tint = self._mix(color, palette["paper"], 0.22)
        pen.arc(track, 180, 360, fill=(*tint, 255), width=track_w)
        for angle, fill in ((360, tint), (180, color)):
            x, y = at(angle, track_r)
            pen.ellipse((x - track_w / 2, y - track_w / 2, x + track_w / 2, y + track_w / 2), fill=(*fill, 255))
        if probability > 0:
            pen.arc(track, 180, end, fill=(*color, 255), width=track_w)
            x, y = at(end, track_r)
            pen.ellipse((x - track_w / 2, y - track_w / 2, x + track_w / 2, y + track_w / 2), fill=(*color, 255))
        tip = at(end, radius * k - track_w - 2 * k)
        left, right = at(end - 90, 2.8 * k), at(end + 90, 2.8 * k)
        tail = at(end + 180, 4 * k)
        pen.polygon((tip, left, tail, right), fill=(*palette["ink"], 255))
        hub = 5 * k
        pen.ellipse((ox - hub, oy - hub, ox + hub, oy + hub), fill=(*palette["ink"], 255))
        dot = 2 * k
        pen.ellipse((ox - dot, oy - dot, ox + dot, oy + dot), fill=(*color, 255))
        small = layer.resize((2 * half_w, height), Image.Resampling.LANCZOS)
        image.paste(small, (round(cx - half_w), round(cy - radius - 6)), small)

    @staticmethod
    def _mix(color, paper, amount):
        """``amount`` of ``color`` over ``paper``."""
        return tuple(round(paper[index] + (color[index] - paper[index]) * amount) for index in range(3))

    def _draw_markets(self, draw, box, markets, palette, family, image=None):
        """MARKET HEAT: three gauge columns, then the console art across the
        room left below them (when ``image`` is given)."""
        left, top, right, bottom = box
        draw.rectangle(box, fill=palette["paper"])
        # Polymarket's own logo heads the markets; the lettered title is the fallback.
        logo = self._polymarket_logo(POLYMARKET_LOGO_HEIGHT, palette) if image is not None else None
        if logo is not None:
            image.paste(logo, (left + 15, top + 12), logo)
            title_right = left + 15 + logo.width
        else:
            title_font = self._font(23, True, family)
            draw.text((left + 15, top + 11), "MARKET HEAT", font=title_font, fill=palette["ink"])
            title_right = draw.textbbox((left + 15, top + 11), "MARKET HEAT", font=title_font)[2]
            # The bulb starts 3 px left of its origin; keep an 8 px optical gap after the title.
            self._draw_thermometer(draw, (title_right + 11, top + 8), palette)
        note_font, note = self._font(9, True, family), "TOP 3 / 24H VOL"
        note_w = draw.textbbox((0, 0), note, font=note_font)[2]
        if right - 12 - note_w > title_right + 32:
            draw.text((right - 12 - note_w, top + 22), note, font=note_font, fill=palette["muted"])

        if not markets:
            draw.text((left + 18, top + 72), "NO MARKET DATA", font=self._font(22, True, family), fill=palette["ink"])
            return

        columns = markets[:3]
        column_w = (right - left - 24) / 3
        columns_bottom = top + MARKET_COLUMNS_HEIGHT
        for index, market in enumerate(columns):
            col_left = left + 12 + index * column_w
            if index:
                draw.line((col_left, top + 48, col_left, columns_bottom), fill=palette["rule"], width=1)
            self._draw_market_column(draw, market, col_left, column_w, top, palette, family, image)
        if image is not None:
            self._paste_market_art(image, (left + 10, columns_bottom + 6, right - 10, bottom - 6))

    def _draw_market_column(self, draw, market, col_left, column_w, top, palette, family, image=None):
        """One market: its event's art across the top fading into the paper with the title over its
        foot (the matchup's two logos on it), the league tag and the day's move, the gauge, the
        leading option with its picture, its probability, then volume, heat and end as icons."""
        cx = col_left + column_w / 2
        width = column_w - 8
        art_box = (round(col_left) + 1, top + 44, round(col_left + column_w) - 1, top + 44 + ART_H)
        if image is not None:
            self._paste_event_art(image, market.get("event_art") or market.get("event_mark"), art_box, palette)
        teams = [team for team in market.get("teams") or [] if isinstance(team, dict)]
        logos = [self._market_mark(team.get("mark"), MATCHUP_LOGO) for team in teams[:2]] if image is not None else []
        title = str(market.get("title") or "MARKET").upper()
        if len(logos) == 2 and all(logos):
            for logo, slot in zip(logos, (cx - 26, cx + 26)):
                image.paste(logo, (round(slot - logo.width / 2), art_box[1] + 8 + (MATCHUP_LOGO - logo.height) // 2), logo)
            vs_font = self._font(10, True, family)
            vs_w = draw.textbbox((0, 0), "VS", font=vs_font)[2]
            draw.text((cx - vs_w / 2, art_box[1] + 16), "VS", font=vs_font, fill=palette["ink"], stroke_width=2,
                      stroke_fill=palette["paper"])
            title = " vs ".join(str(team.get("alias") or team.get("name")) for team in teams[:2]).upper()
        question_font = self._font(10, True, family)
        # Under a matchup's logos the title has two lines; otherwise three, every word kept.
        lines = self._wrap_lines(draw, title, question_font, width, max_lines=2 if title.count(" VS ") else 3)
        y = art_box[3] - 4 - 12 * len(lines)
        for line in lines:
            line_w = draw.textbbox((0, 0), line, font=question_font)[2]
            draw.text((cx - line_w / 2, y), line, font=question_font, fill=palette["ink"], stroke_width=2,
                      stroke_fill=palette["paper"])
            y += 12
        self._draw_league_tag(draw, market, cx, width, top + 144, palette, family, image)

        probability = max(0.0, min(1.0, float(market.get("probability") or 0.0)))
        heat = int(market.get("heat") or 0)
        heat_color = self._heat_color(heat, palette)
        self._draw_gauge(draw, (round(cx), top + 200), GAUGE_RADIUS, probability, heat_color, palette, image)
        self._draw_leader(draw, market, teams, cx, width, top + 210, heat_color, palette, family, image)
        percent = f"{probability * 100:.0f}%"
        percent_font = self._font(28, True, family)
        percent_w = draw.textbbox((0, 0), percent, font=percent_font)[2]
        draw.text((cx - percent_w / 2, top + 234), percent, font=percent_font, fill=palette["ink"])

        # Volume and heat on one line, the end (or the final whistle) under it, each led by its icon.
        fact_font = self._font(10, True, family)
        ends = _parse_time(market.get("end_date"))
        end_fact = (("final", "FINAL") if market.get("final") else
                    ("ends", f"{ends:%d %b}".upper()) if ends is not None else None)
        rows = [[("volume", self._money(market.get("volume_24h")), palette["muted"]),
                 ("heat", f"{heat:02d}", heat_color)]]
        if end_fact:
            rows.append([(end_fact[0], end_fact[1], palette["muted"])])
        for row_index, row in enumerate(rows):
            widths = [MARKET_FACT_ICON + 3 + draw.textbbox((0, 0), text, font=fact_font)[2]
                      for _icon, text, _color in row]
            x = cx - (sum(widths) + 8 * (len(row) - 1)) / 2
            y = top + 270 + row_index * 16
            for (icon, text, color), item_w in zip(row, widths):
                if image is not None:
                    self._paste_icon(image, icon, x, y, MARKET_FACT_ICON, palette)
                draw.text((x + MARKET_FACT_ICON + 3, y + 1), text, font=fact_font, fill=color)
                x += item_w + 8

    def _paste_event_art(self, image, key, box, palette):
        """The event's art cover-cropped to ``box``, cut straight at the column's sides and fading into
        the paper down its lower part, so the column's top takes its theme without a frame."""
        mark = launch_photo.load_cached_mark(self._mark_namespace(), key) if key else None
        if mark is None:
            return
        x0, y0, x1, y1 = box
        size = (x1 - x0, y1 - y0)
        flat = Image.new("RGBA", mark.size, (*palette["paper"], 255))
        flat.alpha_composite(mark)
        art = ImageOps.fit(flat.convert("RGB"), size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.4))
        ramp = Image.new("L", (1, size[1]), 0)
        start = size[1] * ART_FADE
        for y in range(size[1]):
            ramp.putpixel((0, y), round(255 * min(1.0, max(0.0, (y - start) / max(1.0, size[1] - start)) ** 1.2)))
        fade = ramp.resize(size)
        image.paste(Image.composite(Image.new("RGB", size, palette["paper"]), art, fade), (x0, y0))

    def _draw_league_tag(self, draw, market, cx, width, tag_top, palette, family, image):
        """The league (its icon on a paper square, its name on the category colour) and the day's move."""
        category = str(market.get("category") or "MARKET").upper()
        tag_color = palette[CATEGORY_COLORS.get(category, "blue")]
        label = str(market.get("league") or "").upper() or category
        league_icon = self._market_mark(market.get("league_mark"), LEAGUE_ICON) if image is not None else None
        change = float(market.get("change_24h") or 0.0) * 100
        change_text = f"{change:+.1f}%"
        icon_w = 18 if league_icon is not None else 0
        # The tag keeps to its column: a long league name steps down a size or gives way to its
        # short form; failing that the row tightens (a smaller move, slimmer padding), and at
        # last the name ends in "...".
        candidates = tuple(dict.fromkeys((label, LEAGUE_SHORT_NAMES.get(label, label))))
        fitted = None
        for change_size, pad, gap in ((13, 7, 6), (11, 5, 4)):
            change_font = self._font(change_size, True, family)
            change_w = draw.textbbox((0, 0), change_text, font=change_font)[2]
            label_room = max(20, width - icon_w - gap - change_w - 2 * pad)
            for candidate in candidates:
                label_font = self._fit_font(draw, candidate, label_room, 10, 8, family)
                if draw.textbbox((0, 0), candidate, font=label_font)[2] <= label_room:
                    fitted = candidate
                    break
            if fitted:
                break
        label = fitted or self._fit_text(draw, candidates[0], label_font, label_room)
        label_w = draw.textbbox((0, 0), label, font=label_font)[2] + 2 * pad
        x = round(cx - (icon_w + label_w + gap + change_w) / 2)
        if league_icon is not None:
            draw.rectangle((x, tag_top, x + 18, tag_top + 18), fill=palette["white"], outline=tag_color)
            image.paste(league_icon, (x + 9 - league_icon.width // 2, tag_top + 9 - league_icon.height // 2), league_icon)
            x += icon_w
        draw.rectangle((x, tag_top, x + label_w, tag_top + 18), fill=tag_color)
        label_h = draw.textbbox((0, 0), label, font=label_font)
        draw.text((x + pad, tag_top + 9 - (label_h[1] + label_h[3]) / 2), label, font=label_font, fill=palette["white"])
        change_h = draw.textbbox((0, 0), change_text, font=change_font)
        draw.text((x + label_w + gap, tag_top + 9 - (change_h[1] + change_h[3]) / 2), change_text, font=change_font,
                  fill=palette["green"] if change >= 0 else palette["red"])

    def _draw_leader(self, draw, market, teams, cx, width, y, ring, palette, family, image):
        """The leading option's name, led by its picture: a portrait or mark in a disc ringed in the
        heat colour, or its team's logo as it is."""
        leader = str(market.get("leader") or "TBD")
        avatar = None
        if image is not None:
            portrait = launch_photo.load_cached_mark(self._mark_namespace(), market.get("leader_mark"))
            if portrait is not None:
                avatar = self._avatar_disc(portrait, LEADER_AVATAR, ring)
            else:
                team = next((team for team in teams if leader.casefold() in
                             {str(team.get(key) or "").casefold() for key in ("name", "alias")}), None)
                avatar = self._market_mark(team.get("mark"), LEADER_AVATAR - 4) if team is not None else None
                # A team goes by its short name beside its crest.
                leader = str(team.get("alias") or leader) if team is not None else leader
        room = width - (LEADER_AVATAR + 4 if avatar is not None else 0)
        # A name steps down a size or two before it is cut short.
        for size in (11, 10, 9):
            leader_font = self._font(size, True, family)
            lines = self._wrap_lines(draw, leader.upper(), leader_font, room)
            if not any(line.endswith("...") for line in lines):
                break
        text_w = max((draw.textbbox((0, 0), line, font=leader_font)[2] for line in lines), default=0)
        start_x = round(cx - ((LEADER_AVATAR + 4 if avatar is not None else 0) + text_w) / 2)
        text_y = y + (LEADER_AVATAR - 13 * len(lines)) / 2 if avatar is not None else y
        if avatar is not None:
            image.paste(avatar, (start_x + (LEADER_AVATAR - avatar.width) // 2, y + (LEADER_AVATAR - avatar.height) // 2),
                        avatar)
            start_x += LEADER_AVATAR + 4
        for line in lines:
            draw.text((start_x if avatar is not None else cx - draw.textbbox((0, 0), line, font=leader_font)[2] / 2,
                       text_y), line, font=leader_font, fill=palette["ink"])
            text_y += 13

    @staticmethod
    def _avatar_disc(picture, size, ring):
        """``picture`` cover-cropped into a ``size`` px disc (faces sit high) with a 2 px ring."""
        flat = Image.new("RGBA", picture.size, (255, 255, 255, 255))
        flat.alpha_composite(picture.convert("RGBA"))
        scale = 3
        big = size * scale
        art = ImageOps.fit(flat.convert("RGB"), (big, big), method=Image.Resampling.LANCZOS, centering=(0.5, 0.3))
        disc = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).ellipse((2 * scale, 2 * scale, big - 2 * scale - 1, big - 2 * scale - 1), fill=255)
        ring_layer = ImageDraw.Draw(disc)
        ring_layer.ellipse((0, 0, big - 1, big - 1), fill=(*ring[:3], 255))
        disc.paste(art, (0, 0), mask)
        return disc.resize((size, size), Image.Resampling.LANCZOS)

    def _paste_market_art(self, image, box):
        """The console strip centred in the room under the market columns."""
        left, top, right, bottom = box
        if right - left < 80 or bottom - top < 24:
            return
        try:
            with safe_open_image(MARKET_ART_PATH) as source:
                art = source.convert("RGBA")
        except (OSError, ValueError):
            return
        art.thumbnail((right - left, bottom - top), Image.Resampling.LANCZOS)
        image.paste(art, (left + (right - left - art.width) // 2, bottom - art.height), art)

    def _draw_footer(self, draw, box, palette, family):
        left, top, right, bottom = box
        draw.rectangle(box, fill=palette["paper"])
        draw.line((left, top, right, top), fill=palette["rule"], width=3)
        footer_font = self._font(11, True, family)
        draw.text((17, top + 10), "SOURCES  THE SPACE DEVS | POLYMARKET", font=footer_font, fill=palette["ink"])
        right_text = "CROWD PROBABILITY | NOT ADVICE"
        text_width = draw.textbbox((0, 0), right_text, font=footer_font)[2]
        draw.text((right - 17 - text_width, top + 10), right_text, font=footer_font, fill=palette["ink"])
