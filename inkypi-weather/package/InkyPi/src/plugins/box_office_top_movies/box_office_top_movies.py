from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import bounded_int, get_base_ui_font
from utils.http_client import get_http_session
from utils.safe_image import safe_open_image_response

logger = logging.getLogger(__name__)

DEFAULT_CHART_URL = "https://www.the-numbers.com/weekend-box-office-chart"
MAOYAN_DASHBOARD_URL = "https://piaofang.maoyan.com/dashboard-ajax"
MAOYAN_SOURCE_LABEL = "\u732b\u773c\u4e13\u4e1a\u7248"
CHINA_SOURCE_MODES = {"maoyan", "maoyan_china", "china", "china_mainland", "mainland_china"}
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_MOVIE_URL = "https://api.themoviedb.org/3/movie/{movie_id}"
TMDB_ALT_TITLES_URL = "https://api.themoviedb.org/3/movie/{movie_id}/alternative_titles"
TMDB_MOVIE_IMAGES_URL = "https://api.themoviedb.org/3/movie/{movie_id}/images"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w342"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; InkyPi BoxOfficeTopMovies/1.0; "
        "+https://github.com/fatihak/InkyPi/)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
IMAGE_HEADERS = {
    "User-Agent": REQUEST_HEADERS["User-Agent"],
    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.5",
}
MAOYAN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://piaofang.maoyan.com/dashboard",
}
STATE_VERSION = "box-office-top-movies-v5"
MAX_ITEMS = 5
PLUGIN_DIR = Path(__file__).resolve().parent
PLUGIN_FONT_DIR = PLUGIN_DIR / "fonts"
SRC_DIR = PLUGIN_DIR.parent.parent
STATIC_FONT_DIR = SRC_DIR / "static" / "fonts"
CINEMA_PLACEHOLDER_FILE = "boxoffice_cinema_placeholder.png"
CINEMA_PLACEHOLDER_SIZE = (300, 90)
CHINA_TITLE_WORDMARK_FILE = "boxoffice_china_title_wordmark.png"
CHINA_TITLE_WORDMARK_SIZE = (384, 86)
CHINA_TITLE_WORDMARK_DISPLAY_SIZE = (269, 60)
NORTH_AMERICA_TITLE_WORDMARK_FILE = "boxoffice_na_bauhaus_title_wordmark.png"
NORTH_AMERICA_TITLE_WORDMARK_SIZE = (384, 86)
NORTH_AMERICA_TITLE_WORDMARK_DISPLAY_SIZE = (323, 72)
CJK_FONT_SAMPLE = "\u6d4b\u8bd5\u7535\u5f71\u63ed\u79d8\u65e5"


@dataclass
class BoxOfficeMovie:
    rank: int
    title: str
    weekend_gross: str = ""
    total_gross: str = ""
    theaters: str = ""
    weeks: str = ""
    distributor: str = ""
    chart_url: str = ""
    tmdb_id: int | None = None
    poster_url: str = ""
    poster_path: str = ""
    release_year: str = ""
    overview: str = ""
    localized_title: str = ""
    localized_language: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "rank": self.rank,
            "title": self.title,
            "weekend_gross": self.weekend_gross,
            "total_gross": self.total_gross,
            "theaters": self.theaters,
            "weeks": self.weeks,
            "distributor": self.distributor,
            "chart_url": self.chart_url,
            "tmdb_id": self.tmdb_id,
            "poster_url": self.poster_url,
            "poster_path": self.poster_path,
            "release_year": self.release_year,
            "overview": self.overview,
            "localized_title": self.localized_title,
            "localized_language": self.localized_language,
            "extra": dict(self.extra or {}),
        }

    @classmethod
    def from_dict(cls, data):
        payload = dict(data or {})
        payload["rank"] = int(payload.get("rank") or 0)
        return cls(**{key: payload.get(key) for key in cls.__dataclass_fields__})


class _TableParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.rows = []
        self._row = None
        self._cell = None
        self._cell_link = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "tr":
            self._row = []
            return
        if self._row is not None and tag in {"td", "th"}:
            self._cell = []
            self._cell_link = ""
            return
        if self._cell is not None and tag == "a":
            href = attrs.get("href") or ""
            if href and not self._cell_link:
                self._cell_link = urljoin(self.base_url, href)

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append({
                "text": _clean_text(" ".join(self._cell)),
                "link": self._cell_link,
            })
            self._cell = None
            self._cell_link = ""
            return
        if tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


class BoxOfficeTopMovies(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        items_count = self._bounded_int(settings.get("itemsCount"), 5, 1, MAX_ITEMS)
        cache_hours = self._bounded_int(settings.get("cacheHours"), 6, 1, 48)
        cache = self._read_cache()

        cache_key = self._cache_key(settings, dimensions, items_count)
        movies = []
        source_label = "The Numbers"
        generated_at = self._now_for_device(device_config)
        stale = False

        if self._cache_is_fresh(cache, cache_key, cache_hours):
            movies = [BoxOfficeMovie.from_dict(item) for item in cache.get("movies", [])]
            source_label = cache.get("source_label") or source_label
            generated_at = self._parse_datetime(cache.get("generated_at")) or generated_at
        else:
            try:
                movies, source_label = self._load_movies(settings, items_count)
                self._enrich_with_tmdb(movies, settings, device_config)
                self._download_posters(movies)
                generated_at = self._now_for_device(device_config)
                self._write_cache({
                    "version": STATE_VERSION,
                    "cache_key": cache_key,
                    "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
                    "source_label": source_label,
                    "movies": [movie.to_dict() for movie in movies],
                })
            except Exception as exc:
                logger.warning("Box office refresh failed: %s", exc)
                movies = [BoxOfficeMovie.from_dict(item) for item in cache.get("movies", [])]
                source_label = cache.get("source_label") or source_label
                generated_at = self._parse_datetime(cache.get("generated_at")) or generated_at
                stale = True

        if not movies:
            return self._fallback_image(dimensions, "Box Office", "No chart data")

        movies = movies[:items_count]
        self._write_box_office_context(movies, source_label, generated_at, stale)
        return self._render_chart(dimensions, movies, settings, source_label, generated_at, stale)

    def _load_movies(self, settings, items_count):
        source_mode = (settings.get("sourceMode") or "the_numbers").strip().lower()
        chart_url = settings.get("chartUrl") or DEFAULT_CHART_URL

        if source_mode in CHINA_SOURCE_MODES:
            data = self._fetch_json(settings.get("maoyanUrl") or MAOYAN_DASHBOARD_URL, MAOYAN_HEADERS)
            movies = self._parse_maoyan_dashboard(data)
            if movies:
                return movies[:items_count], MAOYAN_SOURCE_LABEL
            raise RuntimeError("Maoyan mainland China chart did not produce movies.")

        if source_mode in {"the_numbers", "auto"}:
            html_text = self._fetch_text(chart_url)
            movies = self._parse_the_numbers_chart(html_text, chart_url)
            if movies:
                return movies[:items_count], "The Numbers"

        if source_mode == "auto":
            data = self._fetch_json(settings.get("maoyanUrl") or MAOYAN_DASHBOARD_URL, MAOYAN_HEADERS)
            movies = self._parse_maoyan_dashboard(data)
            if movies:
                return movies[:items_count], MAOYAN_SOURCE_LABEL

        raise RuntimeError("No box office chart source produced movies.")

    def _fetch_text(self, url):
        response = get_http_session().get(url, timeout=20, headers=REQUEST_HEADERS)
        response.raise_for_status()
        if not response.encoding:
            response.encoding = "utf-8"
        return response.text

    def _fetch_json(self, url, headers=None):
        response = get_http_session().get(url, timeout=16, headers=headers or REQUEST_HEADERS)
        response.raise_for_status()
        if not response.encoding:
            response.encoding = "utf-8"
        return response.json()

    def _parse_maoyan_dashboard(self, data):
        if isinstance(data, str):
            data = json.loads(data)
        rows = (((data or {}).get("movieList") or {}).get("data") or {}).get("list") or []
        movies = []
        seen_titles = set()

        for row in rows:
            info = (row or {}).get("movieInfo") or {}
            title = _clean_text(info.get("movieName"))
            if not title:
                continue
            normalized_title = _normalize_title(title)
            if normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)

            release_info = _clean_text(info.get("releaseInfo"))
            box_rate = _clean_text(row.get("boxRate"))
            total_box = _clean_text(row.get("sumBoxDesc"))
            show_count = str(row.get("showCount") or "")
            show_count_rate = _clean_text(row.get("showCountRate"))

            movies.append(BoxOfficeMovie(
                rank=len(movies) + 1,
                title=title,
                weekend_gross=box_rate,
                total_gross=total_box,
                theaters=show_count,
                weeks=self._days_from_release_info(release_info),
                chart_url=MAOYAN_DASHBOARD_URL,
                localized_language="zh-CN",
                extra={
                    "source": "maoyan",
                    "official_chinese_title": title,
                    "release_info": release_info,
                    "box_rate": box_rate,
                    "show_count_rate": show_count_rate,
                },
            ))

        return movies

    def _days_from_release_info(self, value):
        match = re.search(r"\d+", str(value or ""))
        return match.group(0) if match else ""

    def _parse_the_numbers_chart(self, html_text, chart_url=DEFAULT_CHART_URL):
        parser = _TableParser(chart_url)
        parser.feed(html_text or "")
        movies = []
        seen_titles = set()

        for row in parser.rows:
            cells = [cell.get("text", "") for cell in row]
            if len(cells) < 3:
                continue
            rank = self._rank_from_cell(cells[0])
            if not rank:
                continue

            title, title_link = self._movie_title_from_row(row)
            if not title:
                continue
            normalized_title = _normalize_title(title)
            if normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)

            money_values = [value for value in (self._money_text(cell) for cell in cells) if value]
            weekend_gross = money_values[0] if money_values else ""
            total_gross = money_values[-1] if len(money_values) > 1 else ""
            theaters = self._theater_count_from_cells(cells)
            weeks = self._last_small_int(cells)

            movies.append(BoxOfficeMovie(
                rank=rank,
                title=title,
                weekend_gross=weekend_gross,
                total_gross=total_gross,
                theaters=theaters,
                weeks=weeks,
                chart_url=title_link or chart_url,
            ))

        movies.sort(key=lambda movie: movie.rank)
        return movies

    def _movie_title_from_row(self, row):
        for cell in row[1:5]:
            text = cell.get("text", "")
            lower_link = (cell.get("link") or "").lower()
            if "/movie/" in lower_link and self._looks_like_title(text):
                return self._clean_movie_title(text), cell.get("link") or ""

        for cell in row[1:6]:
            text = cell.get("text", "")
            if self._looks_like_title(text):
                return self._clean_movie_title(text), cell.get("link") or ""
        return "", ""

    def _looks_like_title(self, text):
        value = _clean_text(text)
        if len(value) < 2 or "$" in value or "%" in value:
            return False
        if value.lower() in {"movie", "distributor", "gross", "theaters", "total"}:
            return False
        if re.fullmatch(r"[\d,.-]+", value):
            return False
        return any(char.isalpha() for char in value)

    def _clean_movie_title(self, text):
        text = _clean_text(text)
        text = re.sub(r"\s*\(\d{4}\)\s*$", "", text)
        return text.strip()

    def _rank_from_cell(self, value):
        match = re.search(r"\d+", str(value or ""))
        if not match:
            return None
        rank = int(match.group(0))
        return rank if 1 <= rank <= 200 else None

    def _money_text(self, value):
        match = re.search(r"\$[\d,]+(?:\.\d+)?\s*[KMB]?", str(value or ""), re.IGNORECASE)
        if not match:
            return ""
        return match.group(0).replace(" ", "")

    def _theater_count_from_cells(self, cells):
        candidates = []
        for cell in cells:
            raw = str(cell or "").replace(",", "").strip()
            if not raw.isdigit():
                continue
            value = int(raw)
            if 10 <= value <= 10000:
                candidates.append(str(value))
        return candidates[0] if candidates else ""

    def _last_small_int(self, cells):
        last = ""
        for cell in cells:
            raw = str(cell or "").strip()
            if re.fullmatch(r"\d{1,2}", raw):
                last = raw
        return last

    def _enrich_with_tmdb(self, movies, settings, device_config=None):
        auth = self._tmdb_auth(settings, device_config)
        if not auth:
            logger.info("TMDb credentials not configured; rendering poster placeholders.")
            return

        has_china_movies = any(self._is_maoyan_movie(movie) for movie in movies)
        default_language = "zh-CN" if has_china_movies else "en-US"
        default_region = "CN" if has_china_movies else "US"
        language = (settings.get("tmdbLanguage") or default_language).strip() or default_language
        localized_language = (settings.get("localizedLanguage") or "zh-CN").strip() or "zh-CN"
        show_localized = self._truthy(settings.get("showLocalizedTitles"), True)
        region = (settings.get("tmdbRegion") or default_region).strip().upper()[:2] or default_region
        session = get_http_session()
        for movie in movies:
            try:
                params = {
                    "query": movie.title,
                    "include_adult": "false",
                    "language": language,
                    "region": region,
                }
                results = self._tmdb_get_json(session, TMDB_SEARCH_URL, auth, params).get("results") or []
                if not results:
                    continue
                item = results[0]
                movie.tmdb_id = item.get("id")
                poster_path, poster_language = self._best_tmdb_poster(item, session, auth, language)
                movie.poster_path = poster_path
                movie.release_year = str(item.get("release_date") or "")[:4]
                movie.overview = item.get("overview") or ""
                if movie.poster_path:
                    movie.poster_url = TMDB_IMAGE_BASE + movie.poster_path
                    if poster_language:
                        movie.extra["poster_language"] = poster_language
                    movie.extra["poster_market"] = region
                if self._is_maoyan_movie(movie):
                    english_title = self._tmdb_english_title(movie, item, session, auth)
                    if english_title:
                        movie.extra["english_title"] = english_title
                    movie.localized_title = ""
                elif show_localized and movie.tmdb_id:
                    self._enrich_localized_title(movie, session, auth, localized_language)
            except Exception as exc:
                logger.warning("TMDb lookup failed for %s: %s", movie.title, exc)

    def _best_tmdb_poster(self, item, session, auth, language):
        fallback_path = item.get("poster_path") or ""
        movie_id = item.get("id")
        poster_language = self._tmdb_poster_language(language)
        if not movie_id or not poster_language:
            return fallback_path, ""
        try:
            data = self._tmdb_get_json(
                session,
                TMDB_MOVIE_IMAGES_URL.format(movie_id=movie_id),
                auth,
                {"include_image_language": f"{poster_language},null"},
            )
            selected_path, selected_language = self._select_tmdb_poster(data.get("posters") or [], poster_language)
            if selected_path:
                return selected_path, selected_language
        except Exception as exc:
            title = item.get("title") or item.get("name") or movie_id
            logger.warning("TMDb poster image lookup failed for %s: %s", title, exc)
        return fallback_path, ""

    def _tmdb_poster_language(self, language):
        raw = str(language or "en-US").strip().lower()
        if not raw:
            return "en"
        return raw.replace("_", "-").split("-", 1)[0] or "en"

    def _select_tmdb_poster(self, posters, poster_language):
        desired = str(poster_language or "").lower()
        candidates = []
        for poster in posters:
            path = poster.get("file_path") or ""
            if not path:
                continue
            language = str(poster.get("iso_639_1") or "").lower()
            if language == desired:
                language_rank = 0
                selected_language = language
            elif not language:
                language_rank = 1
                selected_language = "und"
            else:
                language_rank = 2
                selected_language = language
            try:
                aspect_delta = abs(float(poster.get("aspect_ratio") or 0) - (2 / 3))
            except (TypeError, ValueError):
                aspect_delta = 9
            try:
                vote_average = float(poster.get("vote_average") or 0)
            except (TypeError, ValueError):
                vote_average = 0
            try:
                vote_count = int(poster.get("vote_count") or 0)
            except (TypeError, ValueError):
                vote_count = 0
            try:
                width = int(poster.get("width") or 0)
            except (TypeError, ValueError):
                width = 0
            candidates.append((language_rank, aspect_delta, -vote_average, -vote_count, -width, path, selected_language))
        if not candidates:
            return "", ""
        candidates.sort()
        return candidates[0][5], candidates[0][6]

    def _tmdb_english_title(self, movie, item, session, auth):
        candidates = []
        try:
            detail = self._tmdb_get_json(
                session,
                TMDB_MOVIE_URL.format(movie_id=movie.tmdb_id),
                auth,
                {"language": "en-US"},
            )
            candidates.extend([detail.get("title"), detail.get("original_title")])
        except Exception as exc:
            logger.warning("TMDb English title lookup failed for %s: %s", movie.title, exc)
        candidates.extend([item.get("title"), item.get("original_title"), item.get("name"), item.get("original_name")])
        for candidate in candidates:
            english = self._usable_english_title(candidate, movie.title)
            if english:
                return english
        return self._english_title_from_alternatives(movie, session, auth)

    def _english_title_from_alternatives(self, movie, session, auth):
        for country in ("US", "GB", "CA", "AU", "WW"):
            try:
                data = self._tmdb_get_json(
                    session,
                    TMDB_ALT_TITLES_URL.format(movie_id=movie.tmdb_id),
                    auth,
                    {"country": country},
                )
            except Exception as exc:
                logger.warning("TMDb English alternative title lookup failed for %s/%s: %s", movie.title, country, exc)
                continue
            for item in data.get("titles") or []:
                english = self._usable_english_title(item.get("title"), movie.title)
                if english:
                    return english
        return ""

    def _usable_english_title(self, value, chinese_title):
        title = _clean_text(value)
        if not title or _contains_cjk(title):
            return ""
        if title.casefold() == str(chinese_title or "").strip().casefold():
            return ""
        return title

    def _enrich_localized_title(self, movie, session, auth, language):
        try:
            detail = self._tmdb_get_json(
                session,
                TMDB_MOVIE_URL.format(movie_id=movie.tmdb_id),
                auth,
                {"language": language},
            )
            localized = self._usable_localized_title(detail.get("title"), movie.title)
            if not localized:
                localized = self._localized_title_from_alternatives(movie, session, auth, language)
            if localized:
                movie.localized_title = localized
                movie.localized_language = language
        except Exception as exc:
            logger.warning("TMDb localized title lookup failed for %s: %s", movie.title, exc)

    def _localized_title_from_alternatives(self, movie, session, auth, language):
        region = (language.split("-")[-1] if "-" in language else "CN").upper()
        data = self._tmdb_get_json(
            session,
            TMDB_ALT_TITLES_URL.format(movie_id=movie.tmdb_id),
            auth,
            {"country": region},
        )
        for item in data.get("titles") or []:
            if str(item.get("iso_3166_1") or "").upper() != region:
                continue
            localized = self._usable_localized_title(item.get("title"), movie.title)
            if localized:
                return localized
        return ""

    def _tmdb_get_json(self, session, url, auth, params=None):
        request_params = dict(params or {})
        headers = {}
        if auth["type"] == "bearer":
            headers["Authorization"] = f"Bearer {auth['value']}"
        else:
            request_params["api_key"] = auth["value"]

        response = session.get(url, params=request_params, headers=headers, timeout=12)
        response.raise_for_status()
        return response.json()

    def _usable_localized_title(self, value, english_title):
        title = _clean_text(value)
        if not title or not _contains_cjk(title):
            return ""
        if title.casefold() == str(english_title or "").strip().casefold():
            return ""
        return title

    def _tmdb_auth(self, settings, device_config=None):
        bearer = (
            self._setting_secret(settings, "tmdbBearerToken")
            or self._env_secret(settings.get("tmdbBearerTokenEnv"), [
                "TMDB_BEARER_TOKEN",
                "TMDB_READ_ACCESS_TOKEN",
                "TMDB_ACCESS_TOKEN",
                "TMDB_Access_Token",
                "THEMOVIEDB_BEARER_TOKEN",
            ], device_config)
        )
        if bearer:
            return {"type": "bearer", "value": bearer}

        api_key = (
            self._setting_secret(settings, "tmdbApiKey")
            or self._env_secret(settings.get("tmdbApiKeyEnv"), [
                "TMDB_API_KEY",
                "THEMOVIEDB_API_KEY",
            ], device_config)
        )
        if api_key:
            return {"type": "api_key", "value": api_key}
        return None

    def _truthy(self, value, default=False):
        if value is None:
            return bool(default)
        return str(value).strip().lower() not in {"0", "false", "off", "no", "none"}

    def _setting_secret(self, settings, key):
        value = str(settings.get(key) or "").strip()
        return value or ""

    def _env_secret(self, preferred_name, fallback_names, device_config=None):
        names = []
        preferred = str(preferred_name or "").strip()
        if preferred:
            names.append(preferred)
        names.extend(name for name in fallback_names if name not in names)
        for name in names:
            value = str(os.getenv(name) or "").strip()
            if value:
                return value
            if device_config and hasattr(device_config, "load_env_key"):
                try:
                    value = str(device_config.load_env_key(name) or "").strip()
                except Exception:
                    value = ""
                if value:
                    return value
        return ""

    def _download_posters(self, movies):
        for movie in movies:
            if not movie.poster_url:
                continue
            try:
                path = self._poster_cache_path(movie)
                if path.is_file() and path.stat().st_size > 0:
                    movie.poster_path = str(path)
                    continue
                response = get_http_session().get(
                    movie.poster_url,
                    timeout=18,
                    headers=IMAGE_HEADERS,
                    stream=True,
                )
                image = safe_open_image_response(response).convert("RGB")
                path.parent.mkdir(parents=True, exist_ok=True)
                image.save(path, format="JPEG", quality=88)
                movie.poster_path = str(path)
            except Exception as exc:
                logger.warning("Poster download failed for %s: %s", movie.title, exc)

    def _render_chart(self, dimensions, movies, settings, source_label, generated_at, stale=False):
        width, height = dimensions
        colors = self._palette(settings)
        image = Image.new("RGB", dimensions, colors["paper"])
        draw = ImageDraw.Draw(image)

        self._draw_cinema_background(image, colors)
        margin = max(14, width // 44)
        header_h = max(54, height // 8)
        footer_h = max(24, height // 20)
        accent = colors["accent"]

        title_font = self._font(max(24, height // 14), bold=True)
        subtitle_font = self._font(max(12, height // 34))
        rank_font = self._font(max(22, height // 12), bold=True)
        movie_font = self._font(max(18, height // 23), bold=True)
        primary_cjk_font = self._font(max(22, height // 20), bold=True, cjk=True)
        secondary_latin_font = self._font(max(14, height // 32), bold=True)
        row_primary_cjk_font = self._font(max(19, height // 24), bold=True, cjk=True)
        row_secondary_latin_font = self._font(max(12, height // 38), bold=True)
        small_font = self._font(max(11, height // 42))
        metric_font = self._font(max(16, height // 27), bold=True)
        copy = self._chart_copy(settings, source_label, len(movies))

        title_drawn = False
        if self._is_china_chart(settings, source_label):
            title_drawn = self._draw_china_title_wordmark(
                image,
                self._china_title_wordmark_box(width, height, margin),
            )
        else:
            title_drawn = self._draw_north_america_title_wordmark(
                image,
                self._north_america_title_wordmark_box(width, height, margin),
            )
        if not title_drawn:
            draw.text((margin, margin - 2), copy["title"], fill=colors["ink"], font=title_font)
            draw.text((margin, margin + int(header_h * 0.58)), copy["subtitle"], fill=accent, font=subtitle_font)

        meta = self._updated_text(generated_at, source_label, stale)
        meta_w = draw.textlength(meta, font=small_font)
        draw.text((width - margin - meta_w, margin + int(header_h * 0.62)), meta, fill=colors["muted"], font=small_font)

        top = margin + header_h
        bottom = height - margin - footer_h
        hero = movies[0]
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
        hero_primary_font = primary_cjk_font if hero.localized_title else movie_font
        hero_title = self._fit_text(draw, hero_primary, hero_primary_font, list_w)
        draw.text((list_x, top + 4), hero_title, fill=colors["ink"], font=hero_primary_font)
        metric_y = top + 31
        if hero_secondary:
            hero_secondary = self._fit_text(draw, hero_secondary, secondary_latin_font, list_w)
            draw.text((list_x, top + 35), hero_secondary, fill=colors["localized"], font=secondary_latin_font)
            metric_y = top + 62
        metric_label = copy["primary_metric_label"]
        metric_label_w = int(draw.textlength(metric_label, font=small_font))
        draw.text((list_x, metric_y + 5), metric_label, fill=colors["muted"], font=small_font)
        draw.text((list_x + metric_label_w + 16, metric_y), hero.weekend_gross or "--", fill=accent, font=metric_font)
        if hero.total_gross:
            draw.text((list_x, metric_y + 33), f"{copy['total_prefix']} {hero.total_gross}", fill=colors["muted"], font=small_font)

        row_top = top + max(112, height // 4)
        self._draw_cinema_placeholder(
            image,
            self._cinema_placeholder_box(width, height, margin, top, row_top),
        )
        remaining = movies[1:]
        row_count = max(1, len(remaining))
        row_h = max(58, int((bottom - row_top) / row_count))
        mini_w = max(36, int(row_h * 0.46))
        mini_h = max(50, min(row_h - 10, int(mini_w * 1.48)))

        for index, movie in enumerate(remaining):
            y = row_top + index * row_h
            if index:
                draw.line((list_x, y - 5, width - margin, y - 5), fill=colors["line"], width=1)
            self._paste_poster(image, movie, (list_x, y, mini_w, mini_h), colors)
            rank = f"#{movie.rank}"
            draw.text((list_x + mini_w + 10, y + 2), rank, fill=accent, font=metric_font)
            title_x = list_x + mini_w + 58
            primary_title, secondary_title = self._display_titles(movie)
            primary_font = row_primary_cjk_font if movie.localized_title else movie_font
            title = self._fit_text(draw, primary_title, primary_font, width - margin - title_x)
            draw.text((title_x, y + 2), title, fill=colors["ink"], font=primary_font)
            detail_y = y + 31
            if secondary_title:
                secondary_title = self._fit_text(draw, secondary_title, row_secondary_latin_font, width - margin - title_x)
                draw.text((title_x, y + 25), secondary_title, fill=colors["localized"], font=row_secondary_latin_font)
                detail_y = y + 43
            draw.text((title_x, detail_y), movie.weekend_gross or "--", fill=colors["muted"], font=small_font)
            if movie.total_gross:
                total = f"{copy['total_prefix']} {movie.total_gross}"
                total_w = draw.textlength(total, font=small_font)
                draw.text((width - margin - total_w, detail_y), total, fill=colors["muted"], font=small_font)

        footer = copy["footer_tmdb"] if any(movie.poster_url for movie in movies) else copy["footer_placeholder"]
        draw.text((margin, height - margin - footer_h + 8), footer, fill=colors["muted"], font=small_font)
        return image

    def _paste_poster(self, target, movie, box, colors):
        x, y, w, h = box
        poster = self._load_poster(movie, (w, h))
        if poster is None:
            poster = self._placeholder_poster(movie, (w, h), colors)
        poster = ImageOps.fit(poster.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)
        target.paste(poster, (x, y))
        draw = ImageDraw.Draw(target)
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=colors["outline"], width=1)

    def _load_poster(self, movie, size):
        path = movie.poster_path or ""
        if not path or not Path(path).is_file():
            return None
        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except Exception:
            return None

    def _placeholder_poster(self, movie, size, colors):
        w, h = size
        key = hashlib.sha256(movie.title.encode("utf-8")).digest()
        base = (55 + key[0] // 3, 38 + key[1] // 4, 48 + key[2] // 5)
        image = Image.new("RGB", size, base)
        draw = ImageDraw.Draw(image)
        for i in range(0, h, max(8, h // 10)):
            color = tuple(max(0, min(255, channel + ((i // 8) % 5) * 10)) for channel in base)
            draw.rectangle((0, i, w, min(h, i + max(5, h // 18))), fill=color)
        font = self._font(max(12, w // 7), bold=True)
        words = movie.title.upper().split()
        lines = self._wrap_words(draw, words, font, w - 14, max_lines=4)
        total_h = len(lines) * (font.size + 2)
        start_y = max(8, (h - total_h) // 2)
        for line in lines:
            line_w = draw.textlength(line, font=font)
            draw.text(((w - line_w) / 2, start_y), line, fill=colors["paper"], font=font)
            start_y += font.size + 2
        return image

    def _draw_cinema_background(self, image, colors):
        width, height = image.size
        draw = ImageDraw.Draw(image)
        if colors["mode"] != "cinema":
            for y in range(0, height, 16):
                draw.line((0, y, width, y), fill=colors["line"], width=1)
            return

        for y in range(height):
            ratio = y / max(1, height - 1)
            shade = tuple(int(colors["paper"][i] * (1 - ratio * 0.16)) for i in range(3))
            draw.line((0, y, width, y), fill=shade)
        for x in range(0, width, max(18, width // 36)):
            draw.rectangle((x, 0, x + 3, height), fill=colors["shadow"])

    def _cinema_placeholder_box(self, width, height, margin, top, row_top):
        target_w, target_h = CINEMA_PLACEHOLDER_SIZE
        draw_w = min(target_w, max(180, int(width * 0.375)))
        draw_h = min(target_h, max(58, int(height * 0.1875)))
        x = width - margin - draw_w
        y = max(top + 24, row_top - draw_h)
        return (int(x), int(y), int(draw_w), int(draw_h))

    def _draw_cinema_placeholder(self, image, box):
        asset = self._load_cinema_placeholder_asset()
        if asset is None:
            return
        x, y, w, h = [int(value) for value in box]
        if w <= 0 or h <= 0:
            return
        try:
            fitted = ImageOps.contain(asset, (w, h), method=Image.Resampling.LANCZOS)
            paste_x = x + (w - fitted.width) // 2
            paste_y = y + (h - fitted.height) // 2
            image.paste(fitted.convert("RGB"), (paste_x, paste_y), fitted.getchannel("A"))
        except Exception as exc:
            logger.warning("Cinema placeholder asset unavailable: %s", exc)

    def _china_title_wordmark_box(self, width, height, margin):
        target_w, target_h = CHINA_TITLE_WORDMARK_DISPLAY_SIZE
        draw_w = min(target_w, max(196, round(width * 0.336)))
        draw_h = min(target_h, max(43, round(height * 0.126)))
        x = max(0, margin - 3)
        y = max(0, margin - max(8, height // 70))
        return (int(x), int(y), int(draw_w), int(draw_h))

    def _north_america_title_wordmark_box(self, width, height, margin):
        target_w, target_h = NORTH_AMERICA_TITLE_WORDMARK_DISPLAY_SIZE
        draw_w = min(target_w, max(236, round(width * 0.404)))
        draw_h = min(target_h, max(52, round(height * 0.151)))
        x = margin
        y = max(0, margin - max(8, height // 70))
        return (int(x), int(y), int(draw_w), int(draw_h))

    def _draw_north_america_title_wordmark(self, image, box):
        asset = self._load_north_america_title_wordmark_asset()
        if asset is None:
            return False
        x, y, w, h = [int(value) for value in box]
        if w <= 0 or h <= 0:
            return False
        try:
            fitted = ImageOps.contain(asset, (w, h), method=Image.Resampling.LANCZOS)
            visible_bbox = fitted.getchannel("A").getbbox()
            visible_left = visible_bbox[0] if visible_bbox else 0
            paste_x = x - visible_left - 2
            paste_y = y + (h - fitted.height) // 2
            image.paste(fitted.convert("RGB"), (paste_x, paste_y), fitted.getchannel("A"))
            return True
        except Exception as exc:
            logger.warning("North America title wordmark asset unavailable: %s", exc)
            return False

    def _draw_china_title_wordmark(self, image, box):
        asset = self._load_china_title_wordmark_asset()
        if asset is None:
            return False
        x, y, w, h = [int(value) for value in box]
        if w <= 0 or h <= 0:
            return False
        try:
            fitted = ImageOps.contain(asset, (w, h), method=Image.Resampling.LANCZOS)
            paste_x = x
            paste_y = y + (h - fitted.height) // 2
            image.paste(fitted.convert("RGB"), (paste_x, paste_y), fitted.getchannel("A"))
            return True
        except Exception as exc:
            logger.warning("China title wordmark asset unavailable: %s", exc)
            return False

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_china_title_wordmark_asset():
        path = PLUGIN_DIR / CHINA_TITLE_WORDMARK_FILE
        if not path.is_file():
            return None
        try:
            with Image.open(path) as image:
                image.load()
                return ImageOps.exif_transpose(image).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load China title wordmark asset %s: %s", path, exc)
            return None

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_north_america_title_wordmark_asset():
        path = PLUGIN_DIR / NORTH_AMERICA_TITLE_WORDMARK_FILE
        if not path.is_file():
            return None
        try:
            with Image.open(path) as image:
                image.load()
                return ImageOps.exif_transpose(image).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load North America title wordmark asset %s: %s", path, exc)
            return None
    @staticmethod
    @lru_cache(maxsize=1)
    def _load_cinema_placeholder_asset():
        path = PLUGIN_DIR / CINEMA_PLACEHOLDER_FILE
        if not path.is_file():
            return None
        try:
            with Image.open(path) as image:
                image.load()
                return ImageOps.exif_transpose(image).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load cinema placeholder asset %s: %s", path, exc)
            return None

    def _chart_copy(self, settings, source_label, count):
        if self._is_china_chart(settings, source_label):
            return {
                "title": "\u4e2d\u56fd\u5927\u9646\u7535\u5f71\u7968\u623f",
                "subtitle": f"\u5b9e\u65f6\u699c TOP {count}",
                "primary_metric_label": "\u4eca\u65e5\u5360\u6bd4",
                "total_prefix": "\u7d2f\u8ba1",
                "footer_tmdb": "\u6d77\u62a5: TMDb",
                "footer_placeholder": "\u6d77\u62a5: \u672c\u5730\u5360\u4f4d\u56fe / TMDb \u672a\u914d\u7f6e",
            }
        return {
            "title": "NORTH AMERICA BOX OFFICE",
            "subtitle": f"WEEKEND TOP {count}",
            "primary_metric_label": "WEEKEND",
            "total_prefix": "Total",
            "footer_tmdb": "Posters: TMDb",
            "footer_placeholder": "Posters: local placeholders until TMDb is configured",
        }

    def _is_china_chart(self, settings, source_label):
        source_mode = str((settings or {}).get("sourceMode") or "").strip().lower()
        return source_mode in CHINA_SOURCE_MODES or str(source_label or "") == MAOYAN_SOURCE_LABEL

    def _palette(self, settings):
        mode = (settings.get("themeMode") or "auto").lower()
        if mode == "paper":
            return {
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
        return {
            "mode": "cinema",
            "paper": (18, 21, 24),
            "ink": (239, 233, 218),
            "muted": (177, 169, 151),
            "accent": (222, 61, 56),
            "localized": (232, 188, 120),
            "line": (65, 63, 59),
            "outline": (236, 222, 188),
            "shadow": (12, 14, 16),
        }

    def _write_box_office_context(self, movies, source_label, generated_at, stale):
        write_context(
            "box_office_top_movies",
            {
                "kind": "box_office_chart",
                "source": source_label,
                "summary": self._context_summary(source_label, movies),
                "facts": [
                    {"label": "source", "value": source_label},
                    {"label": "stale", "value": str(bool(stale)).lower()},
                ],
                "items": [movie.to_dict() for movie in movies],
            },
            generated_at=generated_at,
            ttl_seconds=8 * 60 * 60,
        )

    def _display_dimensions(self, device_config):
        return self.get_dimensions(device_config)

    def _cache_key(self, settings, dimensions, items_count):
        raw = "|".join([
            STATE_VERSION,
            str(dimensions),
            str(items_count),
            settings.get("sourceMode") or "the_numbers",
            settings.get("chartUrl") or DEFAULT_CHART_URL,
            settings.get("maoyanUrl") or MAOYAN_DASHBOARD_URL,
            settings.get("tmdbLanguage") or "en-US",
            settings.get("tmdbRegion") or "US",
            settings.get("localizedLanguage") or "zh-CN",
            str(self._truthy(settings.get("showLocalizedTitles"), True)),
            settings.get("themeMode") or "auto",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read_cache(self):
        path = self._cache_path()
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read box office cache: %s", exc)
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
        if not generated:
            return False
        if not cache.get("movies"):
            return False
        return datetime.now(timezone.utc) - generated.astimezone(timezone.utc) < timedelta(hours=cache_hours)

    def _cache_path(self):
        return self._cache_dir() / "box_office_cache.json"

    def _cache_dir(self):
        return self.cache_dir(env_var="INKYPI_BOX_OFFICE_CACHE", leaf=".box_office_top_movies_cache", create=True)

    def _poster_cache_path(self, movie):
        key = hashlib.sha256((movie.poster_url or movie.title).encode("utf-8")).hexdigest()[:18]
        return self._cache_dir() / "posters" / f"{key}.jpg"

    def _updated_text(self, generated_at, source_label, stale):
        when = generated_at.strftime("%m/%d %H:%M") if isinstance(generated_at, datetime) else ""
        prefix = "\u65e7\u6570\u636e " if stale and self._is_china_chart({}, source_label) else ("STALE " if stale else "")
        return f"{prefix}{source_label} {when}".strip()

    def _context_summary(self, source_label, movies):
        prefix = "\u4e2d\u56fd\u5927\u9646\u7535\u5f71\u7968\u623f: " if self._is_china_chart({}, source_label) else "North America box office: "
        return prefix + ", ".join(self._context_movie_name(movie) for movie in movies[:3])

    def _parse_datetime(self, value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except Exception:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _now_for_device(self, device_config):
        try:
            import pytz
            timezone_name = device_config.get_config("timezone", default="UTC")
            return datetime.now(pytz.timezone(timezone_name))
        except Exception:
            return datetime.now(timezone.utc)

    def _fallback_image(self, dimensions, title, subtitle):
        image = Image.new("RGB", dimensions, (239, 233, 215))
        draw = ImageDraw.Draw(image)
        width, height = dimensions
        title_font = self._font(max(30, width // 12), bold=True)
        subtitle_font = self._font(max(18, width // 28))
        self._draw_centered(draw, title, width // 2, height // 2 - 26, title_font, (32, 35, 36))
        self._draw_centered(draw, subtitle, width // 2, height // 2 + 28, subtitle_font, (91, 85, 74))
        return image

    def _draw_centered(self, draw, text, x, y, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((x - (bbox[2] - bbox[0]) // 2, y - (bbox[3] - bbox[1]) // 2), text, font=font, fill=fill)

    def _fit_text(self, draw, text, font, max_width):
        value = str(text or "").strip()
        if draw.textlength(value, font=font) <= max_width:
            return value
        suffix = "..."
        while value and draw.textlength(value + suffix, font=font) > max_width:
            value = value[:-1].rstrip()
        return value + suffix if value else str(text or "")[:1]

    def _wrap_words(self, draw, words, font, max_width, max_lines=4):
        lines = []
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if line and draw.textlength(candidate, font=font) > max_width:
                lines.append(line)
                line = word
                if len(lines) >= max_lines - 1:
                    break
            else:
                line = candidate
        if line and len(lines) < max_lines:
            lines.append(self._fit_text(draw, line, font, max_width))
        return lines or [""]

    def _font(self, size, bold=False, cjk=False):
        font = get_base_ui_font(int(size), bold=bool(bold))
        font_path = getattr(font, "path", None)
        if font_path:
            try:
                font = self._load_font(str(font_path), int(size), bool(bold))
            except Exception:
                pass
        if not cjk or self._font_has_cjk_glyphs(font):
            return font

        cjk_paths = [
            PLUGIN_FONT_DIR / "NotoSansSC-VF.ttf",
            STATIC_FONT_DIR / "NotoSansSC-VF.ttf",
            STATIC_FONT_DIR / "LXGWWenKai-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]
        for path in cjk_paths:
            candidate = self._font_from_path(path, size, bold)
            if candidate is not None and self._font_has_cjk_glyphs(candidate):
                return candidate
        logger.warning(
            "Shared base font lacks CJK glyphs for BoxOfficeTopMovies; "
            "using the shared fallback."
        )
        return font

    def _default_yahei_font(self, size, bold=False):
        return get_base_ui_font(int(size), bold=bool(bold))

    def _font_from_path(self, path, size, bold=False):
        try:
            path = Path(path)
            if path.is_file():
                return self._load_font(str(path), size, bold)
        except Exception:
            return None
        return None

    def _load_font(self, path, size, bold=False):
        font = ImageFont.truetype(path, size)
        if bold and hasattr(font, "get_variation_axes") and hasattr(font, "set_variation_by_axes"):
            try:
                values = []
                changed = False
                for axis in font.get_variation_axes():
                    axis_name = axis.get("name", b"")
                    if isinstance(axis_name, bytes):
                        axis_name = axis_name.decode("utf-8", errors="ignore")
                    default = axis.get("default")
                    if "weight" in str(axis_name).lower():
                        values.append(780)
                        changed = True
                    else:
                        values.append(default)
                if changed:
                    font.set_variation_by_axes(values)
            except Exception:
                pass
        return font

    @staticmethod
    def _font_has_cjk_glyphs(font):
        signatures = set()
        for char in CJK_FONT_SAMPLE:
            try:
                bbox = font.getbbox(char)
            except Exception:
                return False
            if not bbox:
                return False
            width = max(1, bbox[2] - bbox[0] + 8)
            height = max(1, bbox[3] - bbox[1] + 8)
            glyph = Image.new("L", (width, height), 0)
            glyph_draw = ImageDraw.Draw(glyph)
            glyph_draw.text((4 - bbox[0], 4 - bbox[1]), char, font=font, fill=255)
            if glyph.getbbox() is None:
                return False
            signatures.add(hashlib.sha1(glyph.tobytes()).hexdigest())
        return len(signatures) >= min(3, len(CJK_FONT_SAMPLE))

    def _bounded_int(self, value, default, minimum, maximum):
        return bounded_int(value, default, minimum, maximum)

    def _context_movie_name(self, movie):
        if self._is_maoyan_movie(movie):
            chinese_title = movie.extra.get("official_chinese_title") or movie.title
            english_title = movie.extra.get("english_title") or ""
            return f"{chinese_title} ({english_title})" if english_title else chinese_title
        if movie.localized_title:
            return f"{movie.localized_title} ({movie.title})"
        return movie.title

    def _display_titles(self, movie):
        if self._is_maoyan_movie(movie):
            return movie.extra.get("official_chinese_title") or movie.title, movie.extra.get("english_title") or ""
        if movie.localized_title:
            return movie.localized_title, movie.title
        return movie.title, ""

    def _is_maoyan_movie(self, movie):
        return str((movie.extra or {}).get("source") or "").lower() == "maoyan"


def _clean_text(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_title(value):
    return "".join(char.casefold() for char in str(value or "") if char.isalnum())


def _contains_cjk(value):
    return any("\u4e00" <= char <= "\u9fff" for char in str(value or ""))
