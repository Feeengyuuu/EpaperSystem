import json
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageChops, ImageDraw
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from utils.app_utils import (
    bounded_int,
    coerce_bool,
    get_available_font_names,
    get_base_ui_font,
    get_font,
)
from utils.safe_image import safe_open_image

from .sources import (
    fetch_github,
    fetch_huggingface,
    fetch_skills,
    record_star_snapshot,
    stars_24h,
)


logger = logging.getLogger(__name__)

PLUGIN_ID = "ai_ecosystem_pulse"
DEFAULT_FONT = "Microsoft YaHei"
LOGO_ASSET_PATH = Path(__file__).with_name("assets") / "ai-ecosystem-pulse-logo.png"
CACHE_SCHEMA = "ai-ecosystem-pulse-v1"
GITHUB_CANDIDATE_SCHEMA = "ai-ecosystem-pulse-github-candidates-v1"
SOURCE_TTLS = {"skills": 360, "huggingface": 60, "github": 60}
MAX_GITHUB_HISTORY_REPOS = 256
SOURCE_STATES = {"live", "fresh_cache", "stale_cache", "fixture"}

DAY_PALETTE = {
    "paper": (243, 239, 228),
    "panel": (251, 248, 240),
    "ink": (19, 34, 56),
    "muted": (70, 86, 107),
    "rule": (36, 54, 74),
    "amber": (228, 165, 43),
    "blue": (38, 88, 207),
    "blue_tint": (227, 235, 251),
    "green": (58, 134, 84),
    "green_tint": (228, 239, 229),
    "rust": (177, 93, 59),
    "white": (255, 252, 244),
    "on_accent": (17, 31, 49),
}

NIGHT_PALETTE = {
    "paper": (16, 24, 39),
    "panel": (25, 36, 54),
    "ink": (244, 239, 224),
    "muted": (190, 199, 210),
    "rule": (139, 157, 181),
    "amber": (239, 185, 67),
    "blue": (105, 151, 247),
    "blue_tint": (35, 55, 87),
    "green": (91, 166, 112),
    "green_tint": (35, 61, 53),
    "rust": (221, 132, 91),
    "white": (30, 42, 61),
    "on_accent": (17, 31, 49),
}


def _utc(value):
    return value.astimezone(timezone.utc)


class AiEcosystemPulse(BasePlugin):
    @staticmethod
    @lru_cache(maxsize=1)
    def _cached_logo_source():
        with safe_open_image(LOGO_ASSET_PATH) as source:
            logo = source.convert("RGBA")
        bounds = logo.getchannel("A").getbbox()
        if bounds is None:
            raise ValueError("AI Ecosystem Pulse logo has no visible pixels")
        return logo.crop(bounds)

    def _logo_source(self):
        return self._cached_logo_source().copy()

    def _prepare_header_logo(self, max_size, palette):
        logo = self._logo_source()
        logo.thumbnail(
            (max(1, int(max_size[0])), max(1, int(max_size[1]))),
            Image.Resampling.LANCZOS,
        )
        if palette["paper"] == NIGHT_PALETTE["paper"]:
            red, green, blue, alpha = logo.split()
            brightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
            dark_wordmark = brightest.point(lambda value: 255 if value < 110 else 0)
            night_ink = Image.new("RGBA", logo.size, (*palette["ink"], 255))
            night_ink.putalpha(alpha)
            logo.paste(night_ink, (0, 0), dark_wordmark)
        return logo

    @staticmethod
    def _panel_boxes(width, height):
        if (width, height) == (800, 480):
            return {
                "header": (18, 16, 782, 64),
                "skills": (18, 76, 438, 446),
                "huggingface": (450, 76, 782, 254),
                "github": (450, 266, 782, 446),
            }
        scale_x = width / 800
        scale_y = height / 480
        base = AiEcosystemPulse._panel_boxes(800, 480)
        return {
            name: tuple(round(value * (scale_x if index % 2 == 0 else scale_y)) for index, value in enumerate(box))
            for name, box in base.items()
        }

    def _font(self, size, bold=False, family=None):
        selected_family = str(family or "").strip() or DEFAULT_FONT
        weight = "bold" if bold else "normal"
        try:
            font = get_font(selected_family, int(size), weight)
        except (KeyError, OSError, TypeError, ValueError):
            font = None
        if font is None and selected_family != DEFAULT_FONT:
            try:
                font = get_font(DEFAULT_FONT, int(size), weight)
            except (KeyError, OSError, TypeError, ValueError):
                font = None
        return font or get_base_ui_font(int(size), bold=bold)

    def _safe_complete_font(
        self,
        draw,
        text,
        preferred_size,
        minimum_size,
        max_width,
        family,
        bold=True,
    ):
        """Choose a font that draws a fixed label/value without truncation."""
        value = str(text or "")
        preferred_size = int(preferred_size)
        minimum_size = int(minimum_size)
        if preferred_size < minimum_size:
            raise ValueError("preferred font size must meet its minimum")

        selected_family = str(family or "").strip() or DEFAULT_FONT
        candidates = []
        for candidate_family in (
            selected_family,
            DEFAULT_FONT,
            "LXGW WenKai",
            "Napoli",
            "DS-Digital",
            *get_available_font_names(),
        ):
            if candidate_family and candidate_family not in candidates:
                candidates.append(candidate_family)
        for candidate_family in candidates:
            for size in range(preferred_size, minimum_size - 1, -1):
                font = self._font(size, bold=bold, family=candidate_family)
                bounds = draw.textbbox((0, 0), value, font=font)
                glyphs_visible = all(
                    character.isspace() or font.getmask(character).getbbox() is not None for character in set(value)
                )
                if glyphs_visible and bounds[2] - bounds[0] <= max_width:
                    return font
        raise ValueError(f"fixed text cannot fit its layout slot: {value!r}")

    @staticmethod
    def _palette(settings):
        mode = str((settings or {}).get("themeMode") or "day").casefold()
        resolved = (settings or {}).get("_inkypi_theme") or {}
        mode = str(resolved.get("mode") or mode).casefold()
        return NIGHT_PALETTE if mode == "night" else DAY_PALETTE

    @staticmethod
    def _fit_text(draw, text, font, max_width):
        value = str(text or "")

        def width(candidate):
            bounds = draw.textbbox((0, 0), candidate, font=font)
            return bounds[2] - bounds[0]

        if width(value) <= max_width:
            return value
        ellipsis = "…"
        for end in range(len(value), 0, -1):
            candidate = value[:end].rstrip() + ellipsis
            if width(candidate) <= max_width:
                return candidate
        return ellipsis if width(ellipsis) <= max_width else ""

    @staticmethod
    def _compact_number(value):
        number = int(value or 0)
        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M".replace(".0M", "M")
        if number >= 1_000:
            return f"{number / 1_000:.1f}K".replace(".0K", "K")
        return str(number)

    @staticmethod
    def _format_integer(value):
        try:
            return f"{int(value or 0):,}"
        except (TypeError, ValueError):
            return "0"

    @staticmethod
    def _github_badge(repo):
        delta = repo.get("stars_24h")
        return "NEW" if delta is None else f"{int(delta):+d} / 24H"

    @staticmethod
    def _skills_row_edges(table, row_count):
        top = table[1]
        bottom = table[3]
        if row_count <= 1:
            return [top, bottom]
        hero_bottom = min(top + 70, bottom)
        compact_count = row_count - 1
        compact_space = bottom - hero_bottom
        return [
            top,
            hero_bottom,
            *(hero_bottom + round(compact_space * index / compact_count) for index in range(1, compact_count)),
            bottom,
        ]

    @staticmethod
    def _right_text(draw, right, y, text, font, fill):
        value = str(text or "")
        bounds = draw.textbbox((0, 0), value, font=font)
        draw.text(
            (right - (bounds[2] - bounds[0]), y),
            value,
            font=font,
            fill=fill,
        )

    @staticmethod
    def _center_text(draw, box, text, font, fill):
        value = str(text or "")
        bounds = draw.textbbox((0, 0), value, font=font)
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]
        x = box[0] + (box[2] - box[0] - text_width) / 2 - bounds[0]
        y = box[1] + (box[3] - box[1] - text_height) / 2 - bounds[1]
        draw.text((x, y), value, font=font, fill=fill)

    @staticmethod
    def _panel(draw, box, palette):
        draw.rounded_rectangle(
            box,
            radius=8,
            fill=palette["panel"],
            outline=palette["rule"],
            width=2,
        )

    def presentation_mode(self, settings):
        return PresentationMode.NO_CHANGE

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_dir(self, create=True):
        return self.cache_dir(
            env_var="AI_ECOSYSTEM_PULSE_CACHE_DIR",
            leaf="cache",
            create=create,
            strip=True,
        )

    def _data_dir(self, create=True):
        return self.data_dir(
            env_var="AI_ECOSYSTEM_PULSE_DATA_DIR",
            leaf="state",
            create=create,
            strip=True,
            legacy_leaf="data",
        )

    @staticmethod
    def _read_json(path, default):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
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
        try:
            fetched = datetime.fromisoformat(payload["fetched_at"]).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            return False
        age = (_utc(now) - fetched).total_seconds()
        return 0 <= age <= ttl_minutes * 60

    @staticmethod
    def _valid_source_item(name, item):
        if not isinstance(item, dict):
            return False
        if name == "skills":
            try:
                int(item.get("rank"))
            except (TypeError, ValueError):
                return False
            if not str(item.get("name") or "").strip() or not str(item.get("source") or "").strip():
                return False
            try:
                int(item.get("installs"))
                return True
            except (TypeError, ValueError):
                return bool(str(item.get("installs_display") or "").strip())
        if name == "huggingface":
            if not bool(str(item.get("id") or "").strip()):
                return False
            try:
                for key in ("trending_score", "likes", "downloads_30d"):
                    int(item.get(key))
            except (TypeError, ValueError):
                return False
            return True
        if name == "github":
            if not str(item.get("full_name") or "").strip():
                return False
            try:
                int(item.get("stars"))
            except (TypeError, ValueError):
                return False
            try:
                if item.get("stars_24h") is not None:
                    int(item.get("stars_24h"))
            except (TypeError, ValueError):
                return False
            return True
        return False

    @classmethod
    def _valid_source_items(cls, name, items):
        if not isinstance(items, list):
            return []
        return [dict(item) for item in items if cls._valid_source_item(name, item)]

    @classmethod
    def _fetch_live_source(cls, name, fetcher):
        raw_items = fetcher()
        items = cls._valid_source_items(name, raw_items)
        if not items:
            raise ValueError("source returned no usable rows")
        return items

    def _resolve_source(self, name, now, force, fetcher, fixture, ttl_minutes):
        cache_file = self._cache_dir() / f"{name}.json"
        cached = self._read_json(cache_file, {})
        cached_items = cached.get("items") if isinstance(cached, dict) else None
        valid_cached_items = self._valid_source_items(name, cached_items)
        cache_is_valid = (
            isinstance(cached, dict)
            and cached.get("schema") == CACHE_SCHEMA
            and isinstance(cached_items, list)
            and bool(valid_cached_items)
            and len(valid_cached_items) == len(cached_items)
        )
        if not force and cache_is_valid and self._cache_fresh(cached, now, ttl_minutes):
            return valid_cached_items, "fresh_cache", ""
        try:
            items = self._fetch_live_source(name, fetcher)
            self._write_json(
                cache_file,
                {
                    "schema": CACHE_SCHEMA,
                    "fetched_at": _utc(now).isoformat(),
                    "items": items,
                },
            )
            return items, "live", ""
        except Exception as exc:
            logger.warning(
                "AI Ecosystem Pulse %s source unavailable: %s",
                name,
                type(exc).__name__,
            )
            if cache_is_valid and cached_items:
                return valid_cached_items, "stale_cache", type(exc).__name__
            return list(fixture), "fixture", type(exc).__name__

    @staticmethod
    def _github_token(device_config):
        if hasattr(device_config, "load_env_key"):
            token = device_config.load_env_key("GITHUB_SECRET") or ""
            if token:
                return token
        return os.getenv("GITHUB_SECRET", "") or os.getenv("GITHUB_TOKEN", "")

    @staticmethod
    def _normalise_star_history(history, now):
        """Keep only valid, bounded snapshots without inventing a new one."""
        if not isinstance(history, list):
            return []
        now_utc = _utc(now)
        cutoff = now_utc - timedelta(days=8)
        points = {}
        for point in history:
            if not isinstance(point, dict):
                continue
            try:
                captured = datetime.fromisoformat(str(point.get("captured_at")).replace("Z", "+00:00")).astimezone(
                    timezone.utc
                )
                stars = int(point.get("stars"))
            except (TypeError, ValueError):
                continue
            if cutoff <= captured <= now_utc:
                points[captured] = {
                    "captured_at": captured.isoformat(),
                    "stars": stars,
                }

        recent_by_hour = {}
        older_by_day = {}
        for captured, point in sorted(points.items()):
            if now_utc - captured <= timedelta(hours=32):
                recent_by_hour[captured.replace(minute=0, second=0, microsecond=0)] = point
            else:
                older_by_day[captured.date()] = point
        return sorted(
            [*older_by_day.values(), *recent_by_hour.values()],
            key=lambda point: point["captured_at"],
        )

    @classmethod
    def _normalise_star_state(cls, state, now):
        if not isinstance(state, dict):
            return {}
        cleaned = {}
        for key, history in state.items():
            normalised = cls._normalise_star_history(history, now)
            if normalised:
                cleaned[str(key)] = normalised
        return cleaned

    @staticmethod
    def _cap_star_state(state, current_keys):
        if len(state) <= MAX_GITHUB_HISTORY_REPOS:
            return state
        protected = set(current_keys)
        removable = sorted(
            (key for key in state if key not in protected),
            key=lambda key: state[key][-1]["captured_at"],
        )
        while len(state) > MAX_GITHUB_HISTORY_REPOS and removable:
            state.pop(removable.pop(0), None)
        return state

    @staticmethod
    def _github_candidate_key(row):
        repo_id = row.get("id")
        if repo_id is not None:
            return str(repo_id)
        full_name = str(row.get("full_name") or "").strip().casefold()
        return f"name:{full_name}" if full_name else ""

    @staticmethod
    def _github_candidate_metadata(row):
        if not isinstance(row, dict):
            return None
        full_name = row.get("full_name")
        if not isinstance(full_name, str) or not full_name.strip():
            return None

        def nonnegative_integer(value, *, missing=None):
            if value is None:
                return missing
            if isinstance(value, bool):
                return None
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            if isinstance(value, float) and not value.is_integer():
                return None
            if isinstance(value, str) and str(parsed) != value.strip().lstrip("+"):
                return None
            return parsed if parsed >= 0 else None

        stars = nonnegative_integer(row.get("stars"))
        if stars is None:
            return None
        repo_id = nonnegative_integer(row.get("id"), missing=None)
        if row.get("id") is not None and repo_id is None:
            return None
        forks = nonnegative_integer(row.get("forks", 0))
        if forks is None:
            return None
        topics = row.get("topics")
        topics = topics if isinstance(topics, list) else []

        def text_field(name, default=""):
            value = row.get(name)
            return value.strip() if isinstance(value, str) and value.strip() else default

        return {
            "id": repo_id,
            "full_name": full_name.strip(),
            "description": text_field("description"),
            "url": text_field("url"),
            "stars": stars,
            "forks": forks,
            "language": text_field("language", "Other"),
            "topics": [str(topic) for topic in topics if isinstance(topic, (str, int, float))],
            "owner_avatar_url": text_field("owner_avatar_url"),
            "created_at": row.get("created_at"),
            "pushed_at": row.get("pushed_at"),
            "updated_at": row.get("updated_at"),
        }

    @classmethod
    def _normalise_candidate_catalog(cls, payload, now):
        if not isinstance(payload, dict) or payload.get("schema") != GITHUB_CANDIDATE_SCHEMA:
            return {}
        items = payload.get("items")
        if not isinstance(items, list):
            return {}
        now_utc = _utc(now)
        cutoff = now_utc - timedelta(days=8)
        catalog = {}
        for raw in items:
            metadata = cls._github_candidate_metadata(raw)
            if metadata is None:
                continue
            try:
                last_seen = datetime.fromisoformat(
                    str(raw.get("last_seen")).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            if not cutoff <= last_seen <= now_utc:
                continue
            key = cls._github_candidate_key(metadata)
            if not key:
                continue
            candidate = {**metadata, "last_seen": last_seen.isoformat()}
            previous = catalog.get(key)
            if previous is None or candidate["last_seen"] > previous["last_seen"]:
                catalog[key] = candidate
        return catalog

    @classmethod
    def _merge_github_candidate_catalog(cls, payload, current_rows, now):
        catalog = cls._normalise_candidate_catalog(payload, now)
        current_keys = set()
        observed_at = _utc(now).isoformat()
        for raw in current_rows:
            metadata = cls._github_candidate_metadata(raw)
            if metadata is None:
                continue
            key = cls._github_candidate_key(metadata)
            if not key:
                continue
            current_keys.add(key)
            catalog[key] = {**metadata, "last_seen": observed_at}
        retained = sorted(
            catalog.items(),
            key=lambda item: (item[1]["last_seen"], item[0]),
            reverse=True,
        )[:MAX_GITHUB_HISTORY_REPOS]
        return [item for _key, item in retained], current_keys

    def _fetch_github_with_snapshots(self, device_config, now):
        rows = fetch_github(token=self._github_token(device_config))
        candidate_file = self._data_dir() / "github-candidates.json"
        candidates, current_keys = self._merge_github_candidate_catalog(
            self._read_json(candidate_file, {}), rows, now
        )
        if not current_keys:
            raise ValueError("GitHub returned no usable current candidates")
        self._write_json(
            candidate_file,
            {"schema": GITHUB_CANDIDATE_SCHEMA, "items": candidates},
        )
        state_file = self._data_dir() / "github-stars.json"
        next_state = self._normalise_star_state(self._read_json(state_file, {}), now)
        enriched = []
        for candidate in candidates:
            item = {key: value for key, value in candidate.items() if key != "last_seen"}
            key = self._github_candidate_key(item)
            history = list(next_state.get(key) or [])
            if key in current_keys:
                item["stars_24h"] = stars_24h(history, item["stars"], now)
                next_state[key] = record_star_snapshot(history, item["stars"], now)
            else:
                item["stars_24h"] = None
            enriched.append(item)
        self._cap_star_state(next_state, current_keys)
        self._write_json(state_file, next_state)
        enriched.sort(
            key=lambda row: (
                row.get("stars_24h") is not None,
                row.get("stars_24h") or -1,
                row.get("stars") or 0,
                row.get("pushed_at") or "",
            ),
            reverse=True,
        )
        return enriched[:3]

    @staticmethod
    def _fixture_payload(now):
        return {
            "schema": CACHE_SCHEMA,
            "skills": [
                {
                    "rank": 1,
                    "name": "ai-video-generation",
                    "source": "101-skills/skills",
                    "installs": 21900,
                    "installs_display": "21.9K",
                },
                {
                    "rank": 6,
                    "name": "find-skills",
                    "source": "vercel-labs/skills",
                    "installs": 16900,
                    "installs_display": "16.9K",
                },
                {
                    "rank": 7,
                    "name": "media-use",
                    "source": "heygen-com/hyperframes",
                    "installs": 11200,
                    "installs_display": "11.2K",
                },
                {
                    "rank": 8,
                    "name": "hyperframes-core",
                    "source": "heygen-com/hyperframes",
                    "installs": 11100,
                    "installs_display": "11.1K",
                },
                {
                    "rank": 9,
                    "name": "hyperframes-cli",
                    "source": "heygen-com/hyperframes",
                    "installs": 10900,
                    "installs_display": "10.9K",
                },
                {
                    "rank": 10,
                    "name": "design-guide",
                    "source": "getpaperclipai/paperclip",
                    "installs": 10800,
                    "installs_display": "10.8K",
                },
            ],
            "models": [
                {
                    "id": "thinkingmachines/Inkling",
                    "pipeline_tag": "image-text-to-text",
                    "trending_score": 922,
                    "likes": 942,
                    "downloads_30d": 7870,
                },
                {
                    "id": "prism-ml/Ternary-Bonsai-27B-gguf",
                    "pipeline_tag": "text-generation",
                    "trending_score": 642,
                    "likes": 670,
                    "downloads_30d": 200774,
                },
                {
                    "id": "prism-ml/Bonsai-27B-gguf",
                    "pipeline_tag": "text-generation",
                    "trending_score": 381,
                    "likes": 386,
                    "downloads_30d": 1045182,
                },
            ],
            "repos": [
                {
                    "id": 1,
                    "full_name": "anthropics/skills",
                    "stars": 162108,
                    "language": "Python",
                    "stars_24h": None,
                },
                {
                    "id": 2,
                    "full_name": "modelcontextprotocol/servers",
                    "stars": 80000,
                    "language": "TypeScript",
                    "stars_24h": None,
                },
                {
                    "id": 3,
                    "full_name": "openai/openai-agents-python",
                    "stars": 18000,
                    "language": "Python",
                    "stars_24h": None,
                },
            ],
            "status": {
                "aggregate": "DEMO",
                "sources": {
                    "skills": "fixture",
                    "huggingface": "fixture",
                    "github": "fixture",
                },
                "updated_at": _utc(now).isoformat(),
            },
            "_source_provenance": SourceProvenance.LOCAL_FALLBACK.value,
        }

    @staticmethod
    def _aggregate_status(states):
        values = list(states.values())
        current = {"live", "fresh_cache"}
        if all(value in current for value in values):
            return "LIVE"
        if all(value == "stale_cache" for value in values):
            return "CACHED"
        if all(value == "fixture" for value in values):
            return "DEMO"
        return "PARTIAL"

    @staticmethod
    def _provenance_for_states(states):
        if "fixture" in states.values():
            return SourceProvenance.LOCAL_FALLBACK
        if "stale_cache" in states.values():
            return SourceProvenance.STALE_CACHE
        if all(value == "fresh_cache" for value in states.values()):
            return SourceProvenance.FRESH_CACHE
        return SourceProvenance.LIVE

    @classmethod
    def _valid_aggregate_payload(cls, payload):
        if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA:
            return False
        for name, key in (("skills", "skills"), ("huggingface", "models"), ("github", "repos")):
            rows = payload.get(key)
            if not isinstance(rows, list) or not rows:
                return False
            if len(cls._valid_source_items(name, rows)) != len(rows):
                return False
        status = payload.get("status")
        if not isinstance(status, dict) or status.get("aggregate") not in {
            "LIVE",
            "PARTIAL",
            "CACHED",
            "DEMO",
        }:
            return False
        sources = status.get("sources")
        if not isinstance(sources, dict) or set(sources) != {
            "skills",
            "huggingface",
            "github",
        }:
            return False
        if any(value not in SOURCE_STATES for value in sources.values()):
            return False
        if status.get("aggregate") != cls._aggregate_status(sources):
            return False
        if payload.get("_source_provenance") != cls._provenance_for_states(sources).value:
            return False
        return True

    def _payload(self, settings, device_config, now):
        aggregate_file = self._cache_dir() / "aggregate.json"
        if settings.get("_theme_render_only"):
            cached = self._read_json(aggregate_file, {})
            if self._valid_aggregate_payload(cached):
                return cached
            return self._fixture_payload(now)

        fixture = self._fixture_payload(now)
        force = coerce_bool(settings.get("forceRefresh"), default=False)
        refresh_minutes = bounded_int(settings.get("refreshMinutes"), 60, 15, 720)
        skills_ttl = max(SOURCE_TTLS["skills"], refresh_minutes)
        skills, skills_state, skills_error = self._resolve_source(
            "skills", now, force, fetch_skills, fixture["skills"], skills_ttl
        )
        models, huggingface_state, huggingface_error = self._resolve_source(
            "huggingface",
            now,
            force,
            fetch_huggingface,
            fixture["models"],
            refresh_minutes,
        )
        repos, github_state, github_error = self._resolve_source(
            "github",
            now,
            force,
            lambda: self._fetch_github_with_snapshots(device_config, now),
            fixture["repos"],
            refresh_minutes,
        )
        states = {
            "skills": skills_state,
            "huggingface": huggingface_state,
            "github": github_state,
        }
        provenance = self._provenance_for_states(states)

        payload = {
            "schema": CACHE_SCHEMA,
            "skills": skills[:6],
            "models": models[:3],
            "repos": repos[:3],
            "status": {
                "aggregate": self._aggregate_status(states),
                "sources": states,
                "errors": {
                    "skills": skills_error,
                    "huggingface": huggingface_error,
                    "github": github_error,
                },
                "updated_at": _utc(now).isoformat(),
            },
            "_source_provenance": provenance.value,
        }
        self._write_json(aggregate_file, payload)
        return payload

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
        settings["_inkypi_theme"] = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
        dimensions = self.get_dimensions(device_config)
        now = self._now_for_device(device_config)
        payload = self._payload(settings, device_config, now)
        image = self._render_page(dimensions, payload, settings, now)
        return attach_source_provenance(
            image,
            payload.get(
                "_source_provenance",
                SourceProvenance.LOCAL_FALLBACK.value,
            ),
            detail=PLUGIN_ID,
        )

    def _render_page(self, dimensions, payload, settings, now):
        palette = self._palette(settings)
        font_family = str((settings or {}).get("fontFamily") or "").strip() or DEFAULT_FONT
        image = Image.new("RGB", dimensions, palette["paper"])
        draw = ImageDraw.Draw(image)
        boxes = self._panel_boxes(*dimensions)
        self._draw_header(
            image,
            draw,
            boxes["header"],
            payload,
            now,
            palette,
            font_family,
        )
        self._draw_skills(
            draw,
            boxes["skills"],
            payload.get("skills") or [],
            palette,
            font_family,
        )
        self._draw_huggingface(
            draw,
            boxes["huggingface"],
            payload.get("models") or [],
            palette,
            font_family,
        )
        self._draw_github(
            draw,
            boxes["github"],
            payload.get("repos") or [],
            palette,
            font_family,
        )
        return image

    def _draw_header(
        self,
        image,
        draw,
        box,
        payload,
        now,
        palette,
        font_family=DEFAULT_FONT,
    ):
        self._panel(draw, box, palette)
        x0, y0, x1, y1 = box
        title_x = x0 + 14
        divider_x = x0 + 270
        title_slot = (divider_x - title_x - 12, y1 - y0 - 16)
        try:
            logo = self._prepare_header_logo(title_slot, palette)
        except (OSError, ValueError):
            title = "AI ECOSYSTEM PULSE"
            title_font = self._safe_complete_font(
                draw,
                title,
                preferred_size=22,
                minimum_size=18,
                max_width=title_slot[0],
                family=font_family,
            )
            draw.text((title_x, y0 + 10), title, font=title_font, fill=palette["ink"])
        else:
            logo_y = y0 + (y1 - y0 - logo.height) // 2
            image.paste(logo, (title_x, logo_y), logo)
        draw.line((divider_x, y0 + 8, divider_x, y1 - 8), fill=palette["rule"], width=2)

        chip = (x1 - 132, y0 + 9, x1 - 72, y1 - 9)
        section = "SKILLS / MODELS / REPOS"
        section_x = divider_x + 14
        section_font = self._safe_complete_font(
            draw,
            section,
            preferred_size=11,
            minimum_size=9,
            max_width=chip[0] - section_x - 12,
            family=font_family,
        )
        draw.text(
            (section_x, y0 + 17),
            section,
            font=section_font,
            fill=palette["ink"],
        )

        status = str((payload.get("status") or {}).get("aggregate") or "DEMO")
        if status == "LIVE":
            chip_fill = palette["green"]
        elif status in {"PARTIAL", "CACHED"}:
            chip_fill = palette["rust"]
        else:
            chip_fill = palette["amber"]
        draw.rounded_rectangle(
            chip,
            radius=6,
            fill=chip_fill,
            outline=palette["rule"],
            width=1,
        )
        chip_text = palette["on_accent"] if status == "DEMO" else (255, 252, 244)
        status_font = self._safe_complete_font(
            draw,
            status,
            preferred_size=9,
            minimum_size=9,
            max_width=chip[2] - chip[0] - 8,
            family=font_family,
        )
        self._center_text(draw, chip, status, status_font, chip_text)
        time_text = now.strftime("%H:%M")
        time_font = self._safe_complete_font(
            draw,
            time_text,
            preferred_size=12,
            minimum_size=9,
            max_width=x1 - 10 - (chip[2] + 8),
            family=font_family,
        )
        self._right_text(
            draw,
            x1 - 10,
            y0 + 16,
            time_text,
            time_font,
            palette["ink"],
        )

    def _draw_skills(
        self,
        draw,
        box,
        skills,
        palette,
        font_family=DEFAULT_FONT,
    ):
        self._panel(draw, box, palette)
        x0, y0, x1, y1 = box
        rows = list(skills[:6])
        name_font = self._font(15, bold=True, family=font_family)
        hero_name_font = self._font(18, bold=True, family=font_family)
        source_font = self._font(10, bold=True, family=font_family)
        table = (x0 + 12, y0 + 48, x1 - 12, y1 - 12)
        rank_right = table[0] + 51
        metric_left = table[2] - 81

        label = "AGENT SKILLS / 24H"
        label_font = self._safe_complete_font(
            draw,
            label,
            preferred_size=15,
            minimum_size=10,
            max_width=x1 - x0 - 24,
            family=font_family,
        )
        draw.text((x0 + 12, y0 + 7), label, font=label_font, fill=palette["ink"])

        rank_label = "RANK"
        rank_label_font = self._safe_complete_font(
            draw,
            rank_label,
            preferred_size=8,
            minimum_size=8,
            max_width=rank_right - table[0] - 6,
            family=font_family,
        )
        draw.text(
            (table[0] + 3, y0 + 31),
            rank_label,
            font=rank_label_font,
            fill=palette["muted"],
        )
        name_label = "NAME / SOURCE"
        name_label_font = self._safe_complete_font(
            draw,
            name_label,
            preferred_size=8,
            minimum_size=8,
            max_width=metric_left - rank_right - 16,
            family=font_family,
        )
        draw.text(
            (rank_right + 8, y0 + 31),
            name_label,
            font=name_label_font,
            fill=palette["muted"],
        )
        installs_label = "INSTALLS"
        installs_label_font = self._safe_complete_font(
            draw,
            installs_label,
            preferred_size=8,
            minimum_size=8,
            max_width=table[2] - metric_left - 8,
            family=font_family,
        )
        self._right_text(
            draw,
            table[2] - 4,
            y0 + 31,
            installs_label,
            installs_label_font,
            palette["muted"],
        )
        draw.rounded_rectangle(
            table,
            radius=7,
            fill=palette["white"],
            outline=palette["rule"],
            width=2,
        )
        if not rows:
            empty_label = "NO SKILL DATA"
            empty_font = self._safe_complete_font(
                draw,
                empty_label,
                preferred_size=13,
                minimum_size=10,
                max_width=table[2] - table[0] - 24,
                family=font_family,
            )
            draw.text(
                (table[0] + 12, table[1] + 18),
                empty_label,
                font=empty_font,
                fill=palette["rust"],
            )
            return

        row_edges = self._skills_row_edges(table, len(rows))
        draw.rectangle(
            (table[0] + 2, table[1] + 2, table[2] - 2, row_edges[1]),
            fill=palette["amber"],
        )
        for edge in row_edges[1:-1]:
            draw.line((table[0], edge, table[2], edge), fill=palette["rule"], width=2)
        draw.line((rank_right, table[1], rank_right, table[3]), fill=palette["rule"], width=2)
        draw.line((metric_left, table[1], metric_left, table[3]), fill=palette["rule"], width=2)

        for index, skill in enumerate(rows):
            top = row_edges[index]
            bottom = row_edges[index + 1]
            rank_box = (table[0], top, rank_right, bottom)
            rank = str(skill.get("rank", "-"))
            rank_font = self._safe_complete_font(
                draw,
                rank,
                preferred_size=20,
                minimum_size=12,
                max_width=rank_right - table[0] - 8,
                family=font_family,
            )
            self._center_text(
                draw,
                rank_box,
                rank,
                rank_font,
                palette["on_accent"] if index == 0 else palette["ink"],
            )
            active_name_font = hero_name_font if index == 0 else name_font
            text_color = palette["on_accent"] if index == 0 else palette["ink"]
            name_x = rank_right + 10
            name_width = metric_left - name_x - 8
            name_y = top + (10 if index == 0 else 6)
            source_y = top + (39 if index == 0 else 27)
            draw.text(
                (name_x, name_y),
                self._fit_text(draw, skill.get("name"), active_name_font, name_width),
                font=active_name_font,
                fill=text_color,
            )
            draw.text(
                (name_x, source_y),
                self._fit_text(draw, skill.get("source"), source_font, name_width),
                font=source_font,
                fill=text_color if index == 0 else palette["muted"],
            )
            metric = skill.get("installs_display") or self._compact_number(skill.get("installs"))
            active_metric_font = self._safe_complete_font(
                draw,
                metric,
                preferred_size=18 if index == 0 else 14,
                minimum_size=10,
                max_width=table[2] - metric_left - 8,
                family=font_family,
            )
            self._center_text(
                draw,
                (metric_left, top, table[2], bottom),
                metric,
                active_metric_font,
                text_color,
            )

    def _draw_huggingface(
        self,
        draw,
        box,
        models,
        palette,
        font_family=DEFAULT_FONT,
    ):
        self._panel(draw, box, palette)
        x0, y0, x1, y1 = box
        rows = list(models[:3])
        name_font = self._font(12, bold=True, family=font_family)
        meta_font = self._font(9, bold=True, family=font_family)
        label = "HUGGING FACE / TRENDING"
        label_font = self._safe_complete_font(
            draw,
            label,
            preferred_size=14,
            minimum_size=10,
            max_width=x1 - x0 - 20,
            family=font_family,
        )
        draw.text(
            (x0 + 10, y0 + 7),
            label,
            font=label_font,
            fill=palette["ink"],
        )
        table = (x0 + 10, y0 + 39, x1 - 10, y1 - 10)
        rank_right = table[0] + 34
        name_right = rank_right + 116
        trend_right = name_right + 44
        likes_right = trend_right + 42
        columns = (rank_right, name_right, trend_right, likes_right)
        for right, label in zip(
            (trend_right, likes_right, table[2]),
            ("TREND", "LIKES", "DL 30D"),
        ):
            left = name_right if label == "TREND" else trend_right if label == "LIKES" else likes_right
            column_font = self._safe_complete_font(
                draw,
                label,
                preferred_size=9,
                minimum_size=9,
                max_width=right - left - 4,
                family=font_family,
            )
            self._center_text(
                draw,
                (left, y0 + 22, right, y0 + 37),
                label,
                column_font,
                palette["blue"],
            )
        draw.rounded_rectangle(
            table,
            radius=7,
            fill=palette["white"],
            outline=palette["blue"],
            width=2,
        )
        if not rows:
            empty_label = "NO MODEL DATA"
            empty_font = self._safe_complete_font(
                draw,
                empty_label,
                preferred_size=10,
                minimum_size=9,
                max_width=table[2] - table[0] - 20,
                family=font_family,
            )
            draw.text(
                (table[0] + 10, table[1] + 14),
                empty_label,
                font=empty_font,
                fill=palette["rust"],
            )
            return

        row_height = (table[3] - table[1]) // 3
        row_edges = [table[1], table[1] + row_height, table[1] + row_height * 2, table[3]]
        draw.rectangle(
            (table[0] + 2, table[1] + 2, table[2] - 2, row_edges[1]),
            fill=palette["blue_tint"],
        )
        for edge in row_edges[1:-1]:
            draw.line((table[0], edge, table[2], edge), fill=palette["blue"], width=1)
        for column in columns:
            draw.line((column, table[1], column, table[3]), fill=palette["rule"], width=1)

        for index, model in enumerate(rows):
            top = row_edges[index]
            bottom = row_edges[index + 1]
            rank = str(index + 1)
            rank_font = self._safe_complete_font(
                draw,
                rank,
                preferred_size=17,
                minimum_size=10,
                max_width=rank_right - table[0] - 4,
                family=font_family,
            )
            self._center_text(
                draw,
                (table[0], top, rank_right, bottom),
                rank,
                rank_font,
                palette["blue"],
            )
            name_width = name_right - rank_right - 12
            draw.text(
                (rank_right + 6, top + 5),
                self._fit_text(draw, model.get("id"), name_font, name_width),
                font=name_font,
                fill=palette["blue"] if index == 0 else palette["ink"],
            )
            draw.text(
                (rank_right + 6, top + 23),
                self._fit_text(
                    draw,
                    model.get("pipeline_tag") or "model",
                    meta_font,
                    name_width,
                ),
                font=meta_font,
                fill=palette["blue"] if index == 0 else palette["muted"],
            )
            values = (
                self._format_integer(model.get("trending_score")),
                self._format_integer(model.get("likes")),
                self._format_integer(model.get("downloads_30d")),
            )
            for left, right, value in zip(
                (name_right, trend_right, likes_right),
                (trend_right, likes_right, table[2]),
                values,
            ):
                metric_font = self._safe_complete_font(
                    draw,
                    value,
                    preferred_size=10,
                    minimum_size=9,
                    max_width=right - left - 4,
                    family=font_family,
                )
                self._center_text(
                    draw,
                    (left, top, right, bottom),
                    value,
                    metric_font,
                    palette["blue"] if index == 0 else palette["ink"],
                )

    def _draw_github(
        self,
        draw,
        box,
        repos,
        palette,
        font_family=DEFAULT_FONT,
    ):
        self._panel(draw, box, palette)
        x0, y0, x1, y1 = box
        rows = list(repos[:3])
        name_font = self._font(12, bold=True, family=font_family)
        metric_font = self._font(9, bold=True, family=font_family)
        label = "GITHUB / AI RISING"
        label_font = self._safe_complete_font(
            draw,
            label,
            preferred_size=14,
            minimum_size=10,
            max_width=x1 - x0 - 20,
            family=font_family,
        )
        draw.text(
            (x0 + 10, y0 + 7),
            label,
            font=label_font,
            fill=palette["ink"],
        )
        table = (x0 + 10, y0 + 40, x1 - 10, y1 - 10)
        rank_right = table[0] + 34
        name_right = rank_right + 108
        stars_right = name_right + 60
        language_right = stars_right + 48
        columns = (rank_right, name_right, stars_right, language_right)
        for left, right, label in (
            (name_right, stars_right, "STARS"),
            (stars_right, language_right, "LANG"),
            (language_right, table[2], "STATUS"),
        ):
            column_font = self._safe_complete_font(
                draw,
                label,
                preferred_size=9,
                minimum_size=9,
                max_width=right - left - 4,
                family=font_family,
            )
            self._center_text(
                draw,
                (left, y0 + 23, right, y0 + 38),
                label,
                column_font,
                palette["green"],
            )
        draw.rounded_rectangle(
            table,
            radius=7,
            fill=palette["white"],
            outline=palette["green"],
            width=2,
        )
        if not rows:
            empty_label = "NO REPOSITORY DATA"
            empty_font = self._safe_complete_font(
                draw,
                empty_label,
                preferred_size=10,
                minimum_size=9,
                max_width=table[2] - table[0] - 20,
                family=font_family,
            )
            draw.text(
                (table[0] + 10, table[1] + 14),
                empty_label,
                font=empty_font,
                fill=palette["rust"],
            )
            return

        row_height = (table[3] - table[1]) // 3
        row_edges = [table[1], table[1] + row_height, table[1] + row_height * 2, table[3]]
        for edge in row_edges[1:-1]:
            draw.line((table[0], edge, table[2], edge), fill=palette["green"], width=1)
        for column in columns:
            draw.line((column, table[1], column, table[3]), fill=palette["rule"], width=1)

        for index, repo in enumerate(rows):
            top = row_edges[index]
            bottom = row_edges[index + 1]
            rank = str(index + 1)
            rank_font = self._safe_complete_font(
                draw,
                rank,
                preferred_size=17,
                minimum_size=10,
                max_width=rank_right - table[0] - 4,
                family=font_family,
            )
            self._center_text(
                draw,
                (table[0], top, rank_right, bottom),
                rank,
                rank_font,
                palette["green"],
            )
            draw.text(
                (rank_right + 6, top + 15),
                self._fit_text(
                    draw,
                    repo.get("full_name"),
                    name_font,
                    name_right - rank_right - 12,
                ),
                font=name_font,
                fill=palette["ink"],
            )
            star_value = f"★{self._format_integer(repo.get('stars'))}"
            star_font = self._safe_complete_font(
                draw,
                star_value,
                preferred_size=9,
                minimum_size=9,
                max_width=stars_right - name_right - 4,
                family=font_family,
            )
            self._center_text(
                draw,
                (name_right, top, stars_right, bottom),
                star_value,
                star_font,
                palette["green"],
            )
            language = self._fit_text(
                draw,
                repo.get("language") or "Other",
                metric_font,
                language_right - stars_right - 4,
            )
            self._center_text(
                draw,
                (stars_right, top, language_right, bottom),
                language,
                metric_font,
                palette["green"],
            )
            badge_box = (
                language_right + 4,
                top + 8,
                table[2] - 4,
                bottom - 8,
            )
            draw.rounded_rectangle(
                badge_box,
                radius=5,
                fill=palette["green"],
                outline=palette["rule"],
                width=1,
            )
            badge = self._github_badge(repo)
            badge_font = self._safe_complete_font(
                draw,
                badge,
                preferred_size=9,
                minimum_size=9,
                max_width=badge_box[2] - badge_box[0] - 4,
                family=font_family,
            )
            self._center_text(draw, badge_box, badge, badge_font, (255, 252, 244))
