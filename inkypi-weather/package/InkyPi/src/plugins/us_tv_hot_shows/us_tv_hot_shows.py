from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from plugins.box_office_top_movies.box_office_top_movies import (
    BoxOfficeMovie,
    BoxOfficeTopMovies,
    TMDB_IMAGE_BASE,
    _clean_text,
    _contains_cjk,
)
from plugins.context_cache import write_context
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

TMDB_DISCOVER_TV_URL = "https://api.themoviedb.org/3/discover/tv"
TMDB_TRENDING_TV_URL = "https://api.themoviedb.org/3/trending/tv/{time_window}"
STATE_VERSION = "us-tv-hot-shows-v1"
MAX_ITEMS = 5


class UsTvHotShows(BoxOfficeTopMovies):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        self._device_config_for_source = device_config
        self._settings_for_source = settings
        dimensions = self._display_dimensions(device_config)
        items_count = self._bounded_int(settings.get("itemsCount"), 5, 1, MAX_ITEMS)
        cache_hours = self._bounded_int(settings.get("cacheHours"), 6, 1, 48)
        cache = self._read_cache()

        cache_key = self._cache_key(settings, dimensions, items_count)
        shows = []
        source_label = "TMDb US On Air"
        generated_at = self._now_for_device(device_config)
        stale = False

        if self._cache_is_fresh(cache, cache_key, cache_hours):
            shows = [BoxOfficeMovie.from_dict(item) for item in cache.get("shows", [])]
            source_label = cache.get("source_label") or source_label
            generated_at = self._local_cached_datetime(cache.get("generated_at"), generated_at) or generated_at
        else:
            try:
                shows, source_label = self._load_shows(settings, items_count)
                self._download_posters(shows)
                generated_at = self._now_for_device(device_config)
                self._write_cache({
                    "version": STATE_VERSION,
                    "cache_key": cache_key,
                    "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
                    "source_label": source_label,
                    "shows": [show.to_dict() for show in shows],
                })
            except Exception as exc:
                logger.warning("US TV hot-shows refresh failed: %s", exc)
                shows = [BoxOfficeMovie.from_dict(item) for item in cache.get("shows", [])]
                source_label = cache.get("source_label") or source_label
                generated_at = self._local_cached_datetime(cache.get("generated_at"), generated_at) or generated_at
                stale = True

        self._device_config_for_source = None
        self._settings_for_source = None
        if not shows:
            shows = self._sample_shows()[:items_count]
            source_label = "Demo Fallback"

        shows = shows[:items_count]
        self._write_us_tv_context(shows, source_label, generated_at, stale)
        return self._render_chart(dimensions, shows, settings, source_label, generated_at, stale)

    def _load_shows(self, settings, items_count):
        source_mode = (settings.get("sourceMode") or "tmdb_us_on_air").lower()

        if source_mode == "tmdb_us_trending_week":
            shows = self._load_trending_us(settings, items_count, "week")
            if shows:
                return shows[:items_count], "TMDb US TV Trending Week"
            shows = self._load_discover_us_popular(settings, items_count)
            if shows:
                return shows[:items_count], "TMDb US TV Popular"

        if source_mode == "tmdb_us_popular":
            shows = self._load_discover_us_popular(settings, items_count)
            if shows:
                return shows[:items_count], "TMDb US TV Popular"

        shows = self._load_discover_us_on_air(settings, items_count)
        if shows:
            return shows[:items_count], "TMDb US TV On Air"

        shows = self._load_discover_us_popular(settings, items_count)
        if shows:
            return shows[:items_count], "TMDb US TV Popular"

        raise RuntimeError("No TMDb US TV source produced shows.")

    def _local_cached_datetime(self, value, local_now):
        parsed = self._parse_datetime(value)
        if not parsed:
            return None
        if getattr(local_now, "tzinfo", None):
            return parsed.astimezone(local_now.tzinfo)
        return parsed

    def _load_discover_us_on_air(self, settings, items_count):
        today = datetime.now(timezone.utc).date()
        window_days = self._bounded_int(settings.get("activeWindowDays"), 45, 7, 180)
        params = self._base_discover_params(settings)
        params.update({
            "sort_by": "popularity.desc",
            "air_date.gte": (today - timedelta(days=window_days)).isoformat(),
            "air_date.lte": today.isoformat(),
            "page": "1",
        })
        data = self._tmdb_source_json(TMDB_DISCOVER_TV_URL, params)
        return self._shows_from_tmdb_results(
            data.get("results") or [],
            items_count,
            metric_label="TMDb 热度",
            source_mode="on_air",
        )

    def _load_discover_us_popular(self, settings, items_count):
        today = datetime.now(timezone.utc).date()
        params = self._base_discover_params(settings)
        params.update({
            "sort_by": "popularity.desc",
            "first_air_date.lte": today.isoformat(),
            "page": "1",
        })
        data = self._tmdb_source_json(TMDB_DISCOVER_TV_URL, params)
        return self._shows_from_tmdb_results(
            data.get("results") or [],
            items_count,
            metric_label="TMDb 热度",
            source_mode="popular",
        )

    def _load_trending_us(self, settings, items_count, time_window):
        data = self._tmdb_source_json(
            TMDB_TRENDING_TV_URL.format(time_window=time_window),
            {"language": settings.get("tmdbLanguage") or "zh-CN"},
        )
        us_results = []
        for item in data.get("results") or []:
            countries = [str(value).upper() for value in (item.get("origin_country") or [])]
            if "US" in countries:
                us_results.append(item)
            if len(us_results) >= items_count:
                break
        return self._shows_from_tmdb_results(
            us_results,
            items_count,
            metric_label="TMDb 趋势",
            source_mode="trending",
        )

    def _base_discover_params(self, settings):
        params = {
            "include_adult": "false",
            "include_null_first_air_dates": "false",
            "language": settings.get("tmdbLanguage") or "zh-CN",
            "timezone": "America/New_York",
            "with_origin_country": "US",
        }
        raw_original_language = settings.get("withOriginalLanguage")
        original_language = _clean_text("en" if raw_original_language is None else raw_original_language)
        if original_language:
            params["with_original_language"] = original_language
        return params

    def _tmdb_source_json(self, url, params):
        auth = self._tmdb_auth(
            getattr(self, "_settings_for_source", {}) or {},
            getattr(self, "_device_config_for_source", None),
        )
        if not auth:
            raise RuntimeError("TMDb credentials are not configured.")
        return self._tmdb_get_json(get_http_session(), url, auth, params)

    def _shows_from_tmdb_results(self, results, items_count, metric_label, source_mode):
        shows = []
        seen = set()
        for item in results:
            localized = _clean_text(item.get("name") or "")
            original = _clean_text(item.get("original_name") or localized)
            key = str(item.get("id") or original or localized)
            if key in seen:
                continue
            seen.add(key)
            if not localized and not original:
                continue

            primary_title = original if original and original != localized else localized
            localized_title = localized if _contains_cjk(localized) and localized != primary_title else ""
            first_air_date = _clean_text(item.get("first_air_date") or "")
            show = BoxOfficeMovie(
                rank=len(shows) + 1,
                title=primary_title,
                localized_title=localized_title,
                weekend_gross=self._popularity_text(item.get("popularity")),
                total_gross=first_air_date,
                tmdb_id=item.get("id"),
                poster_path=item.get("poster_path") or "",
                poster_url=(TMDB_IMAGE_BASE + item.get("poster_path")) if item.get("poster_path") else "",
                release_year=first_air_date[:4],
                overview=item.get("overview") or "",
                extra={
                    "metric_label": metric_label,
                    "total_label": "首播",
                    "source_mode": source_mode,
                    "vote_average": self._rating_text(item.get("vote_average")),
                    "origin_country": ",".join(item.get("origin_country") or []),
                },
            )
            shows.append(show)
            if len(shows) >= items_count:
                break
        return shows

    def _popularity_text(self, value):
        try:
            return f"{float(value):.1f}"
        except (TypeError, ValueError):
            return "--"

    def _rating_text(self, value):
        try:
            score = float(value)
        except (TypeError, ValueError):
            return ""
        return f"{score:.1f}"

    def _sample_shows(self):
        samples = [
            ("Sample US Show", "样张美剧一", "128.4", "2026-05-01"),
            ("Network Drama", "样张美剧二", "92.7", "2026-04-18"),
            ("Streaming Thriller", "样张美剧三", "88.1", "2026-03-22"),
            ("Late Night Mystery", "样张美剧四", "76.5", "2026-02-14"),
            ("Campus Comedy", "样张美剧五", "61.9", "2026-01-09"),
        ]
        return [
            BoxOfficeMovie(
                rank=index,
                title=title,
                localized_title=localized,
                weekend_gross=heat,
                total_gross=date,
                extra={"metric_label": "样张热度", "total_label": "首播"},
            )
            for index, (title, localized, heat, date) in enumerate(samples, start=1)
        ]

    def _render_chart(self, dimensions, shows, settings, source_label, generated_at, stale=False):
        width, height = dimensions
        colors = self._palette(settings)
        image = Image.new("RGB", dimensions, colors["paper"])
        draw = ImageDraw.Draw(image)

        self._draw_streaming_background(image, colors)
        margin = max(14, width // 44)
        header_h = max(54, height // 8)
        footer_h = max(24, height // 20)
        accent = colors["accent"]

        title_font = self._font(max(24, height // 14), bold=True, cjk=True)
        subtitle_font = self._font(max(12, height // 34), cjk=True)
        rank_font = self._font(max(22, height // 12), bold=True)
        latin_title_font = self._font(max(18, height // 23), bold=True)
        primary_cjk_font = self._font(max(22, height // 20), bold=True, cjk=True)
        secondary_latin_font = self._font(max(14, height // 32), bold=True)
        secondary_cjk_font = self._font(max(14, height // 32), bold=True, cjk=True)
        row_primary_cjk_font = self._font(max(19, height // 24), bold=True, cjk=True)
        row_secondary_latin_font = self._font(max(12, height // 38), bold=True)
        row_secondary_cjk_font = self._font(max(12, height // 38), bold=True, cjk=True)
        small_font = self._font(max(11, height // 42), cjk=True)
        metric_font = self._font(max(16, height // 27), bold=True, cjk=True)

        draw.text((margin, margin - 2), self._title_for_source(source_label), fill=colors["ink"], font=title_font)
        subtitle = self._subtitle_for_source(source_label, len(shows))
        draw.text((margin, margin + int(header_h * 0.58)), subtitle, fill=accent, font=subtitle_font)

        meta = self._updated_text(generated_at, source_label, stale)
        meta_w = draw.textlength(meta, font=small_font)
        draw.text((width - margin - meta_w, margin + int(header_h * 0.62)), meta, fill=colors["muted"], font=small_font)

        top = margin + header_h
        bottom = height - margin - footer_h
        hero = shows[0]
        poster_w = max(156, int(width * 0.255))
        poster_h = min(bottom - top, int(poster_w * 1.48))
        poster_x = margin
        poster_y = top + max(0, (bottom - top - poster_h) // 2)
        self._paste_poster(image, hero, (poster_x, poster_y, poster_w, poster_h), colors)

        badge_size = max(42, height // 9)
        draw.rounded_rectangle(
            (poster_x - 1, poster_y - 1, poster_x + badge_size + 10, poster_y + badge_size),
            radius=4,
            fill=accent,
        )
        draw.text((poster_x + 9, poster_y + 4), "#1", fill=colors["paper"], font=rank_font)

        list_x = poster_x + poster_w + max(18, width // 38)
        list_w = width - list_x - margin
        hero_primary, hero_secondary = self._display_titles(hero)
        hero_primary_font = primary_cjk_font if _contains_cjk(hero_primary) else latin_title_font
        hero_title = self._fit_text(draw, hero_primary, hero_primary_font, list_w)
        draw.text((list_x, top + 4), hero_title, fill=colors["ink"], font=hero_primary_font)
        metric_y = top + 34
        if hero_secondary:
            secondary_font = secondary_cjk_font if _contains_cjk(hero_secondary) else secondary_latin_font
            hero_secondary = self._fit_text(draw, hero_secondary, secondary_font, list_w)
            draw.text((list_x, top + 36), hero_secondary, fill=colors["localized"], font=secondary_font)
            metric_y = top + 64
        metric_label = hero.extra.get("metric_label") or "热度"
        draw.text((list_x, metric_y + 5), metric_label, fill=colors["muted"], font=small_font)
        label_w = draw.textlength(metric_label, font=small_font)
        draw.text((list_x + label_w + 16, metric_y), hero.weekend_gross or "--", fill=accent, font=metric_font)
        if hero.total_gross:
            total_label = hero.extra.get("total_label") or "首播"
            draw.text((list_x, metric_y + 34), f"{total_label} {hero.total_gross}", fill=colors["muted"], font=small_font)

        row_top = top + max(112, height // 4)
        remaining = shows[1:]
        row_count = max(1, len(remaining))
        row_h = max(58, int((bottom - row_top) / row_count))
        mini_w = max(36, int(row_h * 0.46))
        mini_h = max(50, min(row_h - 10, int(mini_w * 1.48)))

        for index, show in enumerate(remaining):
            y = row_top + index * row_h
            if index:
                draw.line((list_x, y - 5, width - margin, y - 5), fill=colors["line"], width=1)
            self._paste_poster(image, show, (list_x, y, mini_w, mini_h), colors)
            draw.text((list_x + mini_w + 10, y + 2), f"#{show.rank}", fill=accent, font=metric_font)
            title_x = list_x + mini_w + 58
            primary_title, secondary_title = self._display_titles(show)
            primary_font = row_primary_cjk_font if _contains_cjk(primary_title) else latin_title_font
            title = self._fit_text(draw, primary_title, primary_font, width - margin - title_x)
            draw.text((title_x, y + 2), title, fill=colors["ink"], font=primary_font)
            detail_y = y + 31
            if secondary_title:
                secondary_font = row_secondary_cjk_font if _contains_cjk(secondary_title) else row_secondary_latin_font
                secondary_title = self._fit_text(draw, secondary_title, secondary_font, width - margin - title_x)
                draw.text((title_x, y + 25), secondary_title, fill=colors["localized"], font=secondary_font)
                detail_y = y + 43
            metric = f"{show.extra.get('metric_label') or '热度'} {show.weekend_gross or '--'}"
            draw.text((title_x, detail_y), metric, fill=colors["muted"], font=small_font)
            if show.total_gross:
                total = f"{show.extra.get('total_label') or '首播'} {show.total_gross}"
                total_w = draw.textlength(total, font=small_font)
                draw.text((width - margin - total_w, detail_y), total, fill=colors["muted"], font=small_font)

        footer = "Data: TMDb TV | Posters: TMDb"
        if source_label == "Demo Fallback":
            footer = "Demo fallback: TMDb data unavailable"
        draw.text((margin, height - margin - footer_h + 8), footer, fill=colors["muted"], font=small_font)
        return image

    def _draw_streaming_background(self, image, colors):
        width, height = image.size
        draw = ImageDraw.Draw(image)
        if colors["mode"] == "paper":
            for y in range(0, height, 18):
                draw.line((0, y, width, y), fill=colors["line"], width=1)
            return
        for y in range(height):
            ratio = y / max(1, height - 1)
            shade = tuple(int(colors["paper"][i] * (1 - ratio * 0.13)) for i in range(3))
            draw.line((0, y, width, y), fill=shade)
        for x in range(0, width, max(22, width // 34)):
            draw.rectangle((x, 0, x + 2, height), fill=colors["shadow"])

    def _title_for_source(self, source_label):
        if source_label == "Demo Fallback":
            return "美剧热播榜样张"
        return "美剧热播榜"

    def _subtitle_for_source(self, source_label, count):
        if source_label == "TMDb US TV Trending Week":
            return f"TMDb 周趋势 TOP {count}"
        if source_label == "TMDb US TV Popular":
            return f"美国剧集热度 TOP {count}"
        if source_label == "Demo Fallback":
            return f"视觉样张 TOP {count}"
        return f"近期在播 TOP {count}"

    def _palette(self, settings):
        mode = (settings.get("themeMode") or "auto").lower()
        if mode == "paper":
            return {
                "mode": "paper",
                "paper": (239, 235, 224),
                "ink": (31, 34, 38),
                "muted": (88, 88, 80),
                "accent": (33, 123, 190),
                "localized": (126, 81, 51),
                "line": (209, 200, 184),
                "outline": (38, 40, 42),
                "shadow": (224, 217, 202),
            }
        return {
            "mode": "streaming",
            "paper": (15, 18, 20),
            "ink": (239, 235, 223),
            "muted": (170, 172, 163),
            "accent": (42, 138, 210),
            "localized": (232, 181, 90),
            "line": (55, 62, 64),
            "outline": (220, 224, 214),
            "shadow": (10, 12, 14),
        }

    def _write_us_tv_context(self, shows, source_label, generated_at, stale):
        write_context(
            "us_tv_hot_shows",
            {
                "kind": "us_tv_hot_shows",
                "source": source_label,
                "summary": "US TV hot shows: " + ", ".join(self._context_movie_name(show) for show in shows[:3]),
                "facts": [
                    {"label": "source", "value": source_label},
                    {"label": "stale", "value": str(bool(stale)).lower()},
                ],
                "items": [show.to_dict() for show in shows],
            },
            generated_at=generated_at,
            ttl_seconds=8 * 60 * 60,
        )

    def _cache_key(self, settings, dimensions, items_count):
        raw = "|".join([
            STATE_VERSION,
            str(dimensions),
            str(items_count),
            settings.get("sourceMode") or "tmdb_us_on_air",
            str(self._bounded_int(settings.get("activeWindowDays"), 45, 7, 180)),
            settings.get("tmdbLanguage") or "zh-CN",
            "en" if settings.get("withOriginalLanguage") is None else str(settings.get("withOriginalLanguage")),
            settings.get("themeMode") or "auto",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read_cache(self):
        path = self._cache_path()
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read US TV hot-shows cache: %s", exc)
        return {}

    def _write_cache(self, payload):
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.replace(tmp, path)
        except PermissionError:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _cache_is_fresh(self, cache, cache_key, cache_hours):
        if cache.get("version") != STATE_VERSION or cache.get("cache_key") != cache_key:
            return False
        generated = self._parse_datetime(cache.get("generated_at"))
        if not generated or not cache.get("shows"):
            return False
        return datetime.now(timezone.utc) - generated.astimezone(timezone.utc) < timedelta(hours=cache_hours)

    def _cache_path(self):
        return self._cache_dir() / "us_tv_hot_shows_cache.json"

    def _cache_dir(self):
        override = os.getenv("INKYPI_US_TV_HOT_SHOWS_CACHE")
        if override:
            path = Path(override)
        else:
            path = Path(self.get_plugin_dir(".us_tv_hot_shows_cache"))
        path.mkdir(parents=True, exist_ok=True)
        return path
