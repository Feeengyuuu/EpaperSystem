from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from plugins.box_office_top_movies.box_office_top_movies import (
    BoxOfficeMovie,
    BoxOfficeTopMovies,
    TMDB_IMAGE_BASE,
    _clean_text,
    _contains_cjk,
    _normalize_title,
)
from plugins.context_cache import write_context
from runtime.refresh_contracts import TaskCancelled
from utils.http_client import get_http_session
from utils.local_wordmark import load_source_logo
from utils.safe_image import ImageLimits, safe_open_image, safe_open_image_response

logger = logging.getLogger(__name__)

TMDB_DISCOVER_TV_URL = "https://api.themoviedb.org/3/discover/tv"
TMDB_TRENDING_TV_URL = "https://api.themoviedb.org/3/trending/tv/{time_window}"
# One details call per show with its US streaming services (TMDB's data comes from JustWatch,
# which its terms ask to credit) and its next episode.
TMDB_TV_DETAILS_URL = "https://api.themoviedb.org/3/tv/{tv_id}"
PROVIDER_LOGO_BASE = "https://image.tmdb.org/t/p/w92"
MAX_PROVIDERS = 3
# Douban's American TV collection (about 2 KB a show): each show's Chinese title, rating and
# episode count, matched to TMDB's Chinese name with the season dropped.
DOUBAN_AMERICAN_TV_URL = "https://m.douban.com/rexxar/api/v2/subject_collection/tv_american/items"
DOUBAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; InkyPi UsTvHotShows/1.0; +https://github.com/fatihak/InkyPi/)",
    "Referer": "https://m.douban.com/tv/american",
    "Accept": "application/json",
}
DOUBAN_COUNT = 30
DOUBAN_SEASON = re.compile(r"\s*第[一二三四五六七八九十百零〇两\d]+季\s*$")
DOUBAN_GREEN = (0, 150, 60)
LOGO_DIR = Path(__file__).resolve().parent / "assets" / "logos"
TMDB_LOGO = Path(__file__).resolve().parents[1] / "box_office_top_movies" / "assets" / "logos" / "tmdb.png"
_PROVIDER_LIMITS = ImageLimits(max_bytes=128 * 1024, max_width=512, max_height=512, max_pixels=512 * 512)
STATE_VERSION = "us-tv-hot-shows-v1"
MAX_ITEMS = 5


class UsTvHotShows(BoxOfficeTopMovies):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get(
            "_inkypi_theme"
        ) or self.resolve_theme(settings, device_config)
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
        theme_render_only = bool(settings.get("_theme_render_only"))
        source_cache_ready = (
            cache.get("cache_key") == cache_key and bool(cache.get("shows"))
        )

        if theme_render_only and not source_cache_ready:
            self._device_config_for_source = None
            self._settings_for_source = None
            raise RuntimeError(
                "US TV theme-only render requires matching cached source data."
            )

        if theme_render_only or self._cache_is_fresh(cache, cache_key, cache_hours):
            shows = [BoxOfficeMovie.from_dict(item) for item in cache.get("shows", [])]
            source_label = cache.get("source_label") or source_label
            generated_at = self._local_cached_datetime(cache.get("generated_at"), generated_at) or generated_at
        else:
            try:
                shows, source_label = self._load_shows(settings, items_count)
                self._enrich_show_details(shows, settings)
                self._enrich_with_douban(shows)
                self._download_posters(shows)
                self._download_provider_logos(shows)
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

    def _enrich_show_details(self, shows, settings):
        """Each show's US streaming services and next episode from one TMDB details call."""
        for show in shows:
            if not show.tmdb_id:
                continue
            try:
                details = self._tmdb_source_json(TMDB_TV_DETAILS_URL.format(tv_id=int(show.tmdb_id)), {
                    "language": settings.get("tmdbLanguage") or "zh-CN", "append_to_response": "watch/providers"})
            except TaskCancelled:
                raise
            except Exception as exc:  # optional: the chart stands without it
                logger.info("TMDb details unavailable for %s: %s", show.title, type(exc).__name__)
                continue
            show.extra.update(self._details_extra(details))

    @staticmethod
    def _details_extra(details):
        extra = {}
        upcoming = details.get("next_episode_to_air") if isinstance(details, dict) else None
        if isinstance(upcoming, dict):
            air_date = _clean_text(upcoming.get("air_date"))
            season, episode = upcoming.get("season_number"), upcoming.get("episode_number")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", air_date):
                extra["next_episode_date"] = air_date
                if isinstance(season, int) and isinstance(episode, int):
                    extra["next_episode_code"] = f"S{season}E{episode}"
        region = ((((details or {}).get("watch/providers") or {}).get("results") or {}).get("US") or {}) \
            if isinstance(details, dict) else {}
        providers = []
        for row in sorted((row for row in region.get("flatrate") or [] if isinstance(row, dict)),
                          key=lambda row: row.get("display_priority") if isinstance(row.get("display_priority"), int)
                          else 999):
            logo = _clean_text(row.get("logo_path"))
            if isinstance(row.get("provider_id"), int) and re.fullmatch(r"/[A-Za-z0-9_-]+\.(?:jpg|png)", logo):
                providers.append({"id": row["provider_id"], "name": _clean_text(row.get("provider_name"))[:30],
                                  "logo_path": logo})
            if len(providers) >= MAX_PROVIDERS:
                break
        if providers:
            extra["providers"] = providers
        return extra

    def _enrich_with_douban(self, shows):
        """Douban's Chinese title, rating and episode count for each show its collection lists."""
        try:
            response = get_http_session().get(DOUBAN_AMERICAN_TV_URL, params={"start": 0, "count": DOUBAN_COUNT},
                                              headers=DOUBAN_HEADERS, timeout=12)
            response.raise_for_status()
            items = (response.json() or {}).get("subject_collection_items") or []
        except TaskCancelled:
            raise
        except Exception as exc:  # optional: TMDB's names stand alone
            logger.info("Douban American TV collection unavailable: %s", type(exc).__name__)
            return
        catalogue = {}
        for item in items:
            title = _clean_text((item or {}).get("title")) if isinstance(item, dict) else ""
            if title:
                catalogue.setdefault(_normalize_title(DOUBAN_SEASON.sub("", title)), item)
        for show in shows:
            item = next((catalogue[key] for key in (_normalize_title(show.localized_title), _normalize_title(show.title))
                         if key and key in catalogue), None)
            if item is not None:
                show.extra.update(self._douban_extra(item))
                if not show.localized_title and show.extra.get("douban_title"):
                    show.localized_title = DOUBAN_SEASON.sub("", show.extra["douban_title"])

    @staticmethod
    def _douban_extra(item):
        extra = {"douban_title": _clean_text(item.get("title"))[:40]}
        rating = (item.get("rating") or {}).get("value") if isinstance(item.get("rating"), dict) else None
        if isinstance(rating, (int, float)) and not isinstance(rating, bool) and math.isfinite(rating) and 0 < rating <= 10:
            extra["douban_rating"] = round(float(rating), 1)
        episodes = _clean_text(item.get("episodes_info"))
        if episodes and len(episodes) <= 12:
            extra["douban_episodes"] = episodes
        return extra

    def _provider_logo_path(self, provider_id):
        return self._cache_dir() / "provider_logos" / f"{int(provider_id)}.png"

    def _download_provider_logos(self, shows):
        """Keep each streaming service's TMDB logo (w92) once, by its provider id."""
        wanted = {provider["id"]: provider["logo_path"] for show in shows
                  for provider in show.extra.get("providers") or []}
        for provider_id, logo_path in wanted.items():
            target = self._provider_logo_path(provider_id)
            if target.is_file():
                continue
            try:
                response = get_http_session().get(PROVIDER_LOGO_BASE + logo_path, timeout=12, stream=True)
                with safe_open_image_response(response, limits=_PROVIDER_LIMITS) as decoded:
                    logo = decoded.convert("RGB")
                target.parent.mkdir(parents=True, exist_ok=True)
                logo.save(target)
            except TaskCancelled:
                raise
            except Exception as exc:  # optional: the service's name stays off
                logger.info("Provider logo unavailable for %s: %s", provider_id, type(exc).__name__)

    def _provider_logos(self, show, size, limit=MAX_PROVIDERS):
        """The show's streaming services as ``size`` px rounded app-icon squares, as many as are kept."""
        logos = []
        for provider in (show.extra.get("providers") or [])[:limit]:
            path = self._provider_logo_path(provider["id"])
            try:
                with safe_open_image(path, limits=_PROVIDER_LIMITS) as source:
                    logo = ImageOps.fit(source.convert("RGB"), (size, size), method=Image.Resampling.LANCZOS)
            except (OSError, ValueError, TypeError, KeyError):
                continue
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=max(2, size // 5), fill=255)
            logo.putalpha(mask)
            logos.append(logo)
        return logos

    @staticmethod
    def _paste_provider(image, draw, logo, x, y, colors):
        """A service's icon with a hairline round it in the page's rule colour, so a black (Apple TV+,
        HBO Max) or white (Prime Video) square keeps its shape on a page of its own colour."""
        x, y = round(x), round(y)
        image.paste(logo, (x, y), logo)
        draw.rounded_rectangle((x - 1, y - 1, x + logo.width, y + logo.height), radius=max(2, logo.width // 5) + 1,
                               outline=colors["line"], width=1)

    @staticmethod
    def _next_episode_text(show, today=None):
        """"下集 11/05 S6E3" for an episode from today on, else ""."""
        try:
            day = date.fromisoformat(str(show.extra.get("next_episode_date") or ""))
        except ValueError:
            return ""
        if day < (today or datetime.now().date()):
            return ""
        code = show.extra.get("next_episode_code") or ""
        return f"下集 {day:%m/%d}" + (f" {code}" if code else "")

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
        # Where to stream it, right-aligned on the metric's line (logos are JustWatch's data via TMDB).
        logo_x = width - margin
        for logo in reversed(self._provider_logos(hero, 26)):
            logo_x -= logo.width
            self._paste_provider(image, draw, logo, logo_x, metric_y, colors)
            logo_x -= 5
        facts = [part for part in (hero.extra.get("douban_episodes"), self._next_episode_text(hero),
                                   f"{hero.extra.get('total_label') or '首播'} {hero.total_gross}" if hero.total_gross
                                   else "") if part]
        x = list_x + self._douban_pill(draw, list_x, metric_y + 42, hero, small_font)
        self._draw_facts(draw, x, metric_y + 42, width - margin - x, facts, small_font, colors)

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
            # Douban's rating stands at the title row's right end; the title keeps the rest.
            pill_width = self._douban_pill_width(draw, show, small_font)
            if pill_width:
                self._douban_pill(draw, width - margin - pill_width, y + 13, show, small_font)
            title_room = width - margin - title_x - (pill_width + 8 if pill_width else 0)
            title = self._fit_text(draw, primary_title, primary_font, title_room)
            draw.text((title_x, y + 2), title, fill=colors["ink"], font=primary_font)
            detail_y = y + 31
            if secondary_title:
                secondary_font = row_secondary_cjk_font if _contains_cjk(secondary_title) else row_secondary_latin_font
                secondary_title = self._fit_text(draw, secondary_title, secondary_font, width - margin - title_x)
                draw.text((title_x, y + 25), secondary_title, fill=colors["localized"], font=secondary_font)
                detail_y = y + 43
            metric = f"{show.extra.get('metric_label') or '热度'} {show.weekend_gross or '--'}"
            line_y = detail_y + small_font.size / 2 + 1
            # On the right: the next episode (else the premiere) after the first streaming service's logo.
            right_text = self._next_episode_text(show) or (
                f"{show.extra.get('total_label') or '首播'} {show.total_gross}" if show.total_gross else "")
            right = width - margin
            if right_text:
                right -= draw.textlength(right_text, font=small_font)
                draw.text((right, line_y), right_text, fill=colors["muted"], font=small_font, anchor="lm")
                right -= 6
            for logo in self._provider_logos(show, 14, limit=1):
                right -= logo.width
                self._paste_provider(image, draw, logo, right, line_y - logo.height / 2, colors)
                right -= 6
            self._draw_facts(draw, title_x, line_y, right - title_x, [metric, show.extra.get("douban_episodes")],
                             small_font, colors)

        footer_y = height - margin - footer_h + 8
        if source_label == "Demo Fallback":
            draw.text((margin, footer_y), "Demo fallback: TMDb data unavailable", fill=colors["muted"], font=small_font)
        else:
            self._draw_credits(image, draw, margin, footer_y + small_font.size / 2 + 1, shows, small_font, colors)
        return image

    @staticmethod
    def _douban_pill_width(draw, show, font):
        rating = show.extra.get("douban_rating")
        return draw.textlength(f"豆瓣 {rating:.1f}", font=font) + 12 if isinstance(rating, (int, float)) else 0

    def _douban_pill(self, draw, x, middle, show, font):
        """Douban's rating as a solid green pill at ``x``; the width it took (0 without one)."""
        width = self._douban_pill_width(draw, show, font)
        if not width:
            return 0
        text = f"豆瓣 {show.extra['douban_rating']:.1f}"
        height = font.size + 6
        draw.rounded_rectangle((x, middle - height / 2, x + width, middle + height / 2), radius=height / 2,
                               fill=DOUBAN_GREEN)
        draw.text((x + width / 2, middle), text, fill=(255, 255, 255), font=font, anchor="mm")
        return width + 8

    def _draw_facts(self, draw, x, middle, width, facts, font, colors):
        """``facts`` joined by " · " in the page's muted ink, dropping the last ones that do not fit."""
        facts = [str(fact) for fact in facts if fact]
        while facts:
            text = " · ".join(facts)
            if draw.textlength(text, font=font) <= width:
                draw.text((x, middle), text, fill=colors["muted"], font=font, anchor="lm")
                return
            facts = facts[:-1]

    def _draw_credits(self, image, draw, x, middle, shows, font, colors):
        """The footer names its sources by their logos: TMDB and Douban for the data, JustWatch for
        the streaming services TMDB lists (as TMDB's terms ask)."""
        night = colors["mode"] == "streaming"
        ink = colors["ink"] if night else None
        runs = ["数据 ", load_source_logo(TMDB_LOGO, 10)]
        if any(show.extra.get("douban_title") for show in shows):
            runs += ["  ", load_source_logo(LOGO_DIR / "douban.png", 13)]
        if any(show.extra.get("providers") for show in shows):
            runs += ["  ·  观看平台 ", load_source_logo(LOGO_DIR / "justwatch.png", 11, ink)]
        for run in runs:
            if run is None:
                continue
            if isinstance(run, str):
                draw.text((x, middle), run, fill=colors["muted"], font=font, anchor="lm")
                x += draw.textlength(run, font=font)
            else:
                image.paste(run, (round(x), round(middle - run.height / 2)), run)
                x += run.width

    def _draw_streaming_background(self, image, colors):
        image.paste(colors["paper"], (0, 0, image.width, image.height))

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
        theme = settings.get("_inkypi_theme") or self.resolve_theme(settings, None)
        palette = theme["palette"]
        return {
            "mode": "streaming" if theme["mode"] == "night" else "paper",
            "paper": tuple(palette["background"]),
            "ink": tuple(palette["ink"]),
            "muted": tuple(palette["muted"]),
            "accent": tuple(palette["accent"]),
            "localized": tuple(palette["accent"]),
            "line": tuple(palette["rule"]),
            "outline": tuple(palette["ink"]),
            "shadow": tuple(palette["panel"]),
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

    def _cache_key(self, settings, _dimensions, items_count):
        raw = "|".join([
            STATE_VERSION,
            str(items_count),
            settings.get("sourceMode") or "tmdb_us_on_air",
            str(self._bounded_int(settings.get("activeWindowDays"), 45, 7, 180)),
            settings.get("tmdbLanguage") or "zh-CN",
            "en" if settings.get("withOriginalLanguage") is None else str(settings.get("withOriginalLanguage")),
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
        return self.cache_dir(env_var="INKYPI_US_TV_HOT_SHOWS_CACHE", leaf=".us_tv_hot_shows_cache", create=True)
