from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from PIL import Image, ImageDraw, ImageOps

from plugins.box_office_top_movies.box_office_top_movies import (
    BoxOfficeMovie,
    BoxOfficeTopMovies,
    DEFAULT_CHART_URL,
    TMDB_IMAGE_BASE,
    _TableParser,
    _clean_text,
    _contains_cjk,
)
from plugins.context_cache import write_context
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

DEFAULT_REPORTS_URL = "https://www.zgdypw.cn/sc/sjbg/"
TMDB_NOW_PLAYING_URL = "https://api.themoviedb.org/3/movie/now_playing"
TMDB_DISCOVER_MOVIE_URL = "https://api.themoviedb.org/3/discover/movie"
STATE_VERSION = "north-america-weekly-box-office-v2"
MAX_ITEMS = 5
CHINA_PLUGIN_DIR = Path(__file__).resolve().parent
MAINLAND_PLACEHOLDER_FILE = "china_boxoffice_mainland_placeholder.png"
MAINLAND_PLACEHOLDER_SIZE = (320, 84)
LEGACY_CHINA_SOURCE_MAP = {
    "legacy_auto": "auto",
    "legacy_zgdypw_weekly": "zgdypw_weekly",
    "legacy_tmdb_cn_now_playing": "tmdb_cn_now_playing",
    "legacy_tmdb_cn_popular": "tmdb_cn_popular",
}

CHINA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; InkyPi ChinaBoxOfficeTopMovies/1.0; "
        "+https://github.com/fatihak/InkyPi/)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}


class _ReportLinkParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links = []
        self._href = ""
        self._text = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attrs = dict(attrs)
        self._href = attrs.get("href") or ""
        self._text = []

    def handle_data(self, data):
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self._text is None:
            return
        text = _clean_text(" ".join(self._text))
        href = self._href
        if href and "全国电影票房周报" in text:
            self.links.append((text, urljoin(self.base_url, href)))
        self._href = ""
        self._text = None


class ChinaBoxOfficeTopMovies(BoxOfficeTopMovies):
    def generate_image(self, settings, device_config):
        self._device_config_for_source = device_config
        try:
            return super().generate_image(settings, device_config)
        finally:
            self._device_config_for_source = None

    def _load_movies(self, settings, items_count):
        settings = settings or {}
        source_mode = (settings.get("sourceMode") or "the_numbers").strip().lower()
        errors = []
        legacy_source_mode = LEGACY_CHINA_SOURCE_MAP.get(source_mode)

        if legacy_source_mode is None:
            try:
                north_america_settings = dict(settings)
                north_america_settings["sourceMode"] = "the_numbers"
                movies, label = super()._load_movies(north_america_settings, items_count)
                if movies:
                    self._normalize_north_america_movies(movies)
                    return movies[:items_count], label
            except Exception as exc:
                errors.append(f"North America weekly box office: {exc}")
                logger.warning("North America weekly box office refresh failed: %s", exc)
            raise RuntimeError("; ".join(errors) or "No North America weekly box office data.")

        source_mode = legacy_source_mode

        if source_mode in {"auto", "zgdypw_weekly"}:
            try:
                movies, label = self._load_zgdypw_weekly(settings, items_count)
                if movies:
                    return movies[:items_count], label
            except Exception as exc:
                errors.append(f"official weekly report: {exc}")
                logger.warning("Official China movie chart refresh failed: %s", exc)
            if source_mode == "zgdypw_weekly":
                raise RuntimeError("; ".join(errors) or "No official China chart data.")

        if source_mode in {"auto", "tmdb_cn_now_playing"}:
            try:
                movies = self._load_tmdb_now_playing(settings, items_count)
                if movies:
                    return movies[:items_count], "TMDb China Now Playing"
            except Exception as exc:
                errors.append(f"TMDb now playing: {exc}")
                logger.warning("TMDb China now-playing refresh failed: %s", exc)
            if source_mode == "tmdb_cn_now_playing":
                raise RuntimeError("; ".join(errors) or "No TMDb China now-playing data.")

        if source_mode in {"auto", "tmdb_cn_popular"}:
            try:
                movies = self._load_tmdb_cn_popular(settings, items_count)
                if movies:
                    return movies[:items_count], "TMDb Mainland Popular"
            except Exception as exc:
                errors.append(f"TMDb popular: {exc}")
                logger.warning("TMDb Mainland popular refresh failed: %s", exc)
            if source_mode == "tmdb_cn_popular":
                raise RuntimeError("; ".join(errors) or "No TMDb Mainland popular data.")

        if source_mode == "auto":
            return self._sample_movies()[:items_count], "Demo Fallback"
        raise RuntimeError("; ".join(errors) or "No Mainland China movie chart source produced movies.")

    def _normalize_north_america_movies(self, movies):
        for movie in movies:
            movie.extra.setdefault("metric_label", "本周票房")
            movie.extra.setdefault("total_label", "累计票房")

    def _enrich_with_tmdb(self, movies, settings, device_config=None):
        settings = settings or {}
        source_mode = (settings.get("sourceMode") or "the_numbers").strip().lower()
        if LEGACY_CHINA_SOURCE_MAP.get(source_mode) is None:
            north_america_settings = dict(settings)
            north_america_settings["tmdbLanguage"] = "en-US"
            north_america_settings["tmdbRegion"] = "US"
            north_america_settings.setdefault("localizedLanguage", "zh-CN")
            return super()._enrich_with_tmdb(movies, north_america_settings, device_config)
        return super()._enrich_with_tmdb(movies, settings, device_config)

    def _load_zgdypw_weekly(self, settings, items_count):
        reports_url = settings.get("reportsUrl") or DEFAULT_REPORTS_URL
        index_html = self._fetch_china_text(reports_url)
        links = self._parse_report_links(index_html, reports_url)
        if not links:
            raise RuntimeError("No weekly report links found.")

        report_title, report_url = links[0]
        report_html = self._fetch_china_text(report_url)
        movies = self._parse_zgdypw_report(report_html, report_url)
        for movie in movies:
            movie.extra["period"] = self._period_from_title(report_title)
            movie.extra["metric_label"] = "本周票房"
            movie.extra["total_label"] = "累计"
        return movies[:items_count], self._source_label_from_report(report_title)

    def _fetch_china_text(self, url):
        response = get_http_session().get(url, timeout=20, headers=CHINA_HEADERS)
        response.raise_for_status()
        content = response.content
        candidates = [
            response.encoding,
            getattr(response, "apparent_encoding", None),
            "utf-8",
            "gb18030",
        ]
        seen = set()
        fallback = ""
        for encoding in candidates:
            if not encoding:
                continue
            normalized = str(encoding).lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                text = content.decode(encoding, errors="replace")
            except (LookupError, AttributeError):
                continue
            if not fallback:
                fallback = text
            if "全国电影票房周报" in text or "中国电影数据信息网" in text:
                return text
        return fallback or response.text

    def _parse_report_links(self, html_text, base_url=DEFAULT_REPORTS_URL):
        parser = _ReportLinkParser(base_url)
        parser.feed(html_text or "")
        return parser.links

    def _parse_zgdypw_report(self, html_text, report_url=DEFAULT_REPORTS_URL):
        parser = _TableParser(report_url)
        parser.feed(html_text or "")
        rows = parser.rows
        movies = self._movies_from_report_rows(rows, report_url)
        if movies:
            return movies
        return self._movies_from_report_text(html_text, report_url)

    def _movies_from_report_rows(self, rows, report_url):
        movies = []
        seen = set()
        for row in rows:
            cells = [_clean_text(cell.get("text", "")) for cell in row]
            if len(cells) < 2 or self._is_header_row(cells):
                continue

            rank = self._rank_from_cells(cells)
            if not rank:
                continue

            title, title_link = self._title_from_china_cells(row, cells)
            if not title:
                continue

            normalized = re.sub(r"\s+", "", title)
            if normalized in seen:
                continue
            seen.add(normalized)

            metrics = self._china_metrics_from_cells(cells)
            weekend_gross = metrics[0] if metrics else ""
            total_gross = metrics[1] if len(metrics) > 1 else ""

            movies.append(BoxOfficeMovie(
                rank=rank,
                title=title,
                localized_title=title if _contains_cjk(title) else "",
                weekend_gross=weekend_gross,
                total_gross=total_gross,
                chart_url=title_link or report_url,
            ))

        movies.sort(key=lambda movie: movie.rank)
        return movies

    def _movies_from_report_text(self, html_text, report_url):
        text = _clean_text(re.sub(r"<[^>]+>", " ", html.unescape(html_text or "")))
        movies = []
        for match in re.finditer(r"(?:第\s*)?([1-9]\d?)\s*(?:名|位)[：:\s、《]+([^，。；;《》]{2,28})", text):
            title = self._clean_china_title(match.group(2))
            if not title:
                continue
            movies.append(BoxOfficeMovie(
                rank=int(match.group(1)),
                title=title,
                localized_title=title if _contains_cjk(title) else "",
                chart_url=report_url,
                extra={"metric_label": "本周票房", "total_label": "累计"},
            ))
            if len(movies) >= MAX_ITEMS:
                break
        return movies

    def _is_header_row(self, cells):
        joined = " ".join(cells)
        return any(token in joined for token in ("影片名称", "电影名称", "片名", "本周票房", "累计票房", "排名"))

    def _rank_from_cells(self, cells):
        for cell in cells[:2]:
            match = re.search(r"\d+", cell)
            if not match:
                continue
            rank = int(match.group(0))
            if 1 <= rank <= 100:
                return rank
        return None

    def _title_from_china_cells(self, row, cells):
        for index, cell in enumerate(cells[1:6], start=1):
            title = self._clean_china_title(cell)
            if not title:
                continue
            link = row[index].get("link") if index < len(row) else ""
            return title, link or ""
        return "", ""

    def _clean_china_title(self, value):
        text = _clean_text(value)
        text = text.replace("《", "").replace("》", "")
        text = re.sub(r"\s*\(\d{4}\)\s*$", "", text)
        text = re.sub(r"^(?:影片|电影|片名|名称)[:：]\s*", "", text)
        text = text.strip(" -\u3000")
        if len(text) < 2 or len(text) > 40:
            return ""
        if any(token in text for token in ("票房", "人次", "场次", "排名", "累计", "日期", "周报")):
            return ""
        if self._china_metric_text(text):
            return ""
        if re.fullmatch(r"[\d.,%+\-\s]+", text):
            return ""
        return text if (_contains_cjk(text) or any(char.isalpha() for char in text)) else ""

    def _china_metrics_from_cells(self, cells):
        metrics = []
        for cell in cells:
            metric = self._china_metric_text(cell)
            if metric and metric not in metrics:
                metrics.append(metric)
        return metrics

    def _china_metric_text(self, value):
        text = _clean_text(value)
        if not text:
            return ""
        if re.search(r"[￥¥]\s*[\d,.]+", text):
            return re.search(r"[￥¥]\s*[\d,.]+(?:\s*[万亿]?元?)?", text).group(0).replace(" ", "")
        match = re.search(r"[\d,.]+(?:\.\d+)?\s*(?:万|亿)?\s*元", text)
        if match:
            return match.group(0).replace(" ", "")
        match = re.search(r"[\d,.]+(?:\.\d+)?\s*(?:万|亿)", text)
        if match and not re.search(r"^\d{4}[.-]\d{1,2}", text):
            return match.group(0).replace(" ", "")
        return ""

    def _period_from_title(self, title):
        match = re.search(r"（([^）]+)）", title or "")
        return match.group(1) if match else ""

    def _source_label_from_report(self, title):
        period = self._period_from_title(title)
        return f"中国电影数据信息网 {period}".strip()

    def _load_tmdb_now_playing(self, settings, items_count):
        data = self._tmdb_source_json(settings, TMDB_NOW_PLAYING_URL, {
            "language": settings.get("tmdbLanguage") or "zh-CN",
            "region": "CN",
            "page": "1",
        })
        return self._movies_from_tmdb_results(data.get("results") or [], items_count, "TMDb 热度")

    def _load_tmdb_cn_popular(self, settings, items_count):
        today = datetime.now(timezone.utc).date()
        data = self._tmdb_source_json(settings, TMDB_DISCOVER_MOVIE_URL, {
            "include_adult": "false",
            "include_video": "false",
            "language": settings.get("tmdbLanguage") or "zh-CN",
            "region": "CN",
            "sort_by": "popularity.desc",
            "with_origin_country": "CN",
            "primary_release_date.lte": today.isoformat(),
            "primary_release_date.gte": (today - timedelta(days=540)).isoformat(),
            "page": "1",
        })
        return self._movies_from_tmdb_results(data.get("results") or [], items_count, "TMDb 热度")

    def _tmdb_source_json(self, settings, url, params):
        auth = self._tmdb_auth(settings, getattr(self, "_device_config_for_source", None))
        if not auth:
            raise RuntimeError("TMDb credentials are not configured.")
        return self._tmdb_get_json(get_http_session(), url, auth, params)

    def _movies_from_tmdb_results(self, results, items_count, metric_label):
        movies = []
        for index, item in enumerate(results[: max(items_count, MAX_ITEMS)], start=1):
            title = _clean_text(item.get("title") or item.get("name") or item.get("original_title") or "")
            original_title = _clean_text(item.get("original_title") or "")
            if not title:
                continue
            movie = BoxOfficeMovie(
                rank=index,
                title=original_title if original_title and original_title != title else title,
                localized_title=title if _contains_cjk(title) else "",
                weekend_gross=self._tmdb_popularity_text(item.get("popularity")),
                total_gross=self._tmdb_secondary_text(item),
                tmdb_id=item.get("id"),
                poster_path=item.get("poster_path") or "",
                poster_url=(TMDB_IMAGE_BASE + item.get("poster_path")) if item.get("poster_path") else "",
                release_year=str(item.get("release_date") or "")[:4],
                overview=item.get("overview") or "",
                extra={
                    "metric_label": metric_label,
                    "total_label": "上映",
                    "release_date": item.get("release_date") or "",
                },
            )
            movies.append(movie)
        return movies

    def _tmdb_popularity_text(self, value):
        try:
            return f"{float(value):.1f}"
        except (TypeError, ValueError):
            return "--"

    def _tmdb_secondary_text(self, item):
        release = _clean_text(item.get("release_date") or "")
        if release:
            return release
        vote = item.get("vote_average")
        try:
            score = float(vote)
        except (TypeError, ValueError):
            return ""
        return f"{score:.1f} 分"

    def _sample_movies(self):
        titles = [
            ("样张影片一", "3287.4万", "2.1亿"),
            ("样张影片二", "2110.8万", "6.8亿"),
            ("样张影片三", "980.5万", "1.3亿"),
            ("样张影片四", "721.2万", "7212万"),
            ("样张影片五", "510.0万", "3.4亿"),
        ]
        return [
            BoxOfficeMovie(
                rank=index,
                title=title,
                localized_title=title,
                weekend_gross=weekly,
                total_gross=total,
                extra={"metric_label": "样张票房", "total_label": "累计"},
            )
            for index, (title, weekly, total) in enumerate(titles, start=1)
        ]

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

        title_font = self._font(max(23, height // 15), bold=True, cjk=True)
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
        subtitle = self._subtitle_for_source(source_label, len(movies))
        draw.text((margin, margin + int(header_h * 0.58)), subtitle, fill=accent, font=subtitle_font)

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
        hero_primary_font = primary_cjk_font if _contains_cjk(hero_primary) else latin_title_font
        hero_title = self._fit_text(draw, hero_primary, hero_primary_font, list_w)
        draw.text((list_x, top + 4), hero_title, fill=colors["ink"], font=hero_primary_font)
        metric_y = top + 34
        if hero_secondary:
            secondary_font = secondary_cjk_font if _contains_cjk(hero_secondary) else secondary_latin_font
            hero_secondary = self._fit_text(draw, hero_secondary, secondary_font, list_w)
            draw.text((list_x, top + 36), hero_secondary, fill=colors["localized"], font=secondary_font)
            metric_y = top + 64
        metric_label = hero.extra.get("metric_label") or "本周"
        draw.text((list_x, metric_y + 5), metric_label, fill=colors["muted"], font=small_font)
        label_w = draw.textlength(metric_label, font=small_font)
        draw.text((list_x + label_w + 16, metric_y), hero.weekend_gross or "--", fill=accent, font=metric_font)
        if hero.total_gross:
            total_label = hero.extra.get("total_label") or "累计"
            draw.text((list_x, metric_y + 34), f"{total_label} {hero.total_gross}", fill=colors["muted"], font=small_font)

        row_top = top + max(112, height // 4)
        self._draw_mainland_placeholder(
            image,
            self._mainland_placeholder_box(width, height, margin, top),
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
            primary_font = row_primary_cjk_font if _contains_cjk(primary_title) else latin_title_font
            title = self._fit_text(draw, primary_title, primary_font, width - margin - title_x)
            draw.text((title_x, y + 2), title, fill=colors["ink"], font=primary_font)
            detail_y = y + 31
            if secondary_title:
                secondary_font = row_secondary_cjk_font if _contains_cjk(secondary_title) else row_secondary_latin_font
                secondary_title = self._fit_text(draw, secondary_title, secondary_font, width - margin - title_x)
                draw.text((title_x, y + 25), secondary_title, fill=colors["localized"], font=secondary_font)
                detail_y = y + 43
            metric = movie.weekend_gross or "--"
            metric_label = movie.extra.get("metric_label") or "本周"
            draw.text((title_x, detail_y), f"{metric_label} {metric}", fill=colors["muted"], font=small_font)
            if movie.total_gross:
                total_label = movie.extra.get("total_label") or "累计"
                total = f"{total_label} {movie.total_gross}"
                total_w = draw.textlength(total, font=small_font)
                draw.text((width - margin - total_w, detail_y), total, fill=colors["muted"], font=small_font)

        footer = self._footer_for_source(source_label, movies)
        draw.text((margin, height - margin - footer_h + 8), footer, fill=colors["muted"], font=small_font)
        return image

    def _mainland_placeholder_box(self, width, height, margin, top):
        target_w, target_h = MAINLAND_PLACEHOLDER_SIZE
        draw_w = min(target_w, max(220, int(width * 0.4)))
        draw_h = min(target_h, max(54, int(height * 0.175)))
        x = width - margin - draw_w - max(24, width // 28)
        y = top + max(8, height // 60)
        return (int(x), int(y), int(draw_w), int(draw_h))

    def _draw_mainland_placeholder(self, image, box):
        asset = self._load_mainland_placeholder_asset()
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
            logger.warning("Mainland cinema placeholder asset unavailable: %s", exc)

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_mainland_placeholder_asset():
        path = CHINA_PLUGIN_DIR / MAINLAND_PLACEHOLDER_FILE
        if not path.is_file():
            return None
        try:
            with Image.open(path) as image:
                image.load()
                return ImageOps.exif_transpose(image).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load mainland cinema placeholder asset %s: %s", path, exc)
            return None

    def _subtitle_for_source(self, source_label, count):
        if source_label == "The Numbers":
            return f"北美本周票房 TOP {count}"
        if source_label.startswith("中国电影数据信息网"):
            return f"官方周报 TOP {count}"
        if source_label == "Demo Fallback":
            return f"视觉样张 TOP {count}"
        return f"中国区热映 TOP {count}"

    def _title_for_source(self, source_label):
        if source_label == "The Numbers":
            return "北美本周票房榜"
        if source_label.startswith("中国电影数据信息网"):
            return "中国内地票房榜"
        if source_label == "TMDb Mainland Popular":
            return "大陆电影热度榜"
        if source_label == "Demo Fallback":
            return "中国电影榜样张"
        return "中国区热映榜"

    def _footer_for_source(self, source_label, movies):
        if source_label == "Demo Fallback":
            return "Demo fallback: upstream data unavailable"
        if source_label == "The Numbers":
            if any(movie.poster_url for movie in movies):
                return "Data: The Numbers | Posters: TMDb"
            return "Data: The Numbers | Posters pending TMDb"
        if any(movie.poster_url for movie in movies):
            return "Data: official/TMDb | Posters: TMDb"
        return "Data: official/TMDb | Posters pending TMDb"

    def _palette(self, settings):
        mode = (settings.get("themeMode") or "auto").lower()
        if mode == "paper":
            return {
                "mode": "paper",
                "paper": (240, 235, 222),
                "ink": (34, 34, 31),
                "muted": (95, 87, 74),
                "accent": (184, 39, 48),
                "localized": (124, 72, 55),
                "line": (210, 199, 181),
                "outline": (39, 39, 36),
                "shadow": (224, 216, 198),
            }
        return {
            "mode": "cinema",
            "paper": (18, 20, 22),
            "ink": (240, 234, 220),
            "muted": (178, 169, 150),
            "accent": (224, 56, 54),
            "localized": (232, 190, 118),
            "line": (66, 62, 56),
            "outline": (236, 222, 188),
            "shadow": (12, 14, 15),
        }

    def _write_box_office_context(self, movies, source_label, generated_at, stale):
        if source_label == "The Numbers":
            kind = "north_america_weekly_box_office"
            summary = "North America weekly box office: " + ", ".join(self._context_movie_name(movie) for movie in movies[:3])
        else:
            kind = "mainland_china_movie_chart"
            summary = "Mainland China movie chart: " + ", ".join(self._context_movie_name(movie) for movie in movies[:3])
        write_context(
            "china_box_office_top_movies",
            {
                "kind": kind,
                "source": source_label,
                "summary": summary,
                "facts": [
                    {"label": "source", "value": source_label},
                    {"label": "stale", "value": str(bool(stale)).lower()},
                ],
                "items": [movie.to_dict() for movie in movies],
            },
            generated_at=generated_at,
            ttl_seconds=8 * 60 * 60,
        )

    def _cache_key(self, settings, dimensions, items_count):
        raw = "|".join([
            STATE_VERSION,
            str(dimensions),
            str(items_count),
            settings.get("sourceMode") or "the_numbers",
            settings.get("chartUrl") or DEFAULT_CHART_URL,
            settings.get("reportsUrl") or DEFAULT_REPORTS_URL,
            settings.get("tmdbLanguage") or "en-US",
            settings.get("tmdbRegion") or "US",
            settings.get("localizedLanguage") or "zh-CN",
            settings.get("themeMode") or "auto",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read_cache(self):
        path = self._cache_path()
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read China movie chart cache: %s", exc)
        return {}

    def _write_cache(self, payload):
        payload = dict(payload or {})
        payload["version"] = STATE_VERSION
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
        if not generated or not cache.get("movies"):
            return False
        return datetime.now(timezone.utc) - generated.astimezone(timezone.utc) < timedelta(hours=cache_hours)

    def _cache_path(self):
        return self._cache_dir() / "china_box_office_cache.json"

    def _cache_dir(self):
        return self.cache_dir(env_var="INKYPI_CHINA_BOX_OFFICE_CACHE", leaf=".china_box_office_top_movies_cache", create=True)
