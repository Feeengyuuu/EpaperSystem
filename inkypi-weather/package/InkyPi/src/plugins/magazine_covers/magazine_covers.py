from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import random
import re
import tempfile
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; InkyPi MagazineCovers/1.0; "
        "+https://github.com/fatihak/InkyPi/)"
    )
}

def _magazineshop_pages(label, slug, first_page, last_page):
    lines = []
    for page in range(first_page, last_page + 1):
        name = label if page == 1 else f"{label} Page {page}"
        if page == 1:
            url = f"https://magazineshop.us/collections/{slug}"
        else:
            url = f"https://magazineshop.us/collections/{slug}?page={page}"
        lines.append(f"{name}|{url}")
    return "\n".join(lines)


CORE_DEFAULT_SOURCES = """TIME|https://magazineshop.us/collections/time
Rolling Stone|https://magazineshop.us/collections/rolling-stone
Billboard|https://magazineshop.us/collections/billboard
Vanity Fair|https://www.vanityfair.com/magazine
The Atlantic|https://www.theatlantic.com/magazine/
Variety|https://magazineshop.us/collections/variety
The Hollywood Reporter|https://magazineshop.us/collections/the-hollywood-reporter
Us Weekly|https://magazineshop.us/collections/us-weekly
Sports Illustrated|https://magazineshop.us/collections/sports-illustrated
Robb Report|https://magazineshop.us/collections/robb-report
Reader's Digest|https://magazineshop.us/collections/readers-digest
Taste of Home|https://magazineshop.us/collections/taste-of-home
TV Guide|https://magazineshop.us/collections/tv-guide-tv"""

ADDITIONAL_DEFAULT_SOURCES = """Newest Releases|https://magazineshop.us/collections/new-releases
Newest Releases Page 2|https://magazineshop.us/collections/new-releases?page=2
Newest Releases Page 3|https://magazineshop.us/collections/new-releases?page=3
Best Sellers|https://magazineshop.us/collections/best-sellers
Digital Magazines|https://magazineshop.us/collections/digital-magazines
Digital Magazines Page 2|https://magazineshop.us/collections/digital-magazines?page=2
People Magazine|https://magazineshop.us/collections/people-magazine
People Special Editions|https://magazineshop.us/collections/people-special-editions
Newsweek|https://magazineshop.us/collections/newsweek
Men's Journal|https://magazineshop.us/collections/mens-journal
Athlon Sports|https://magazineshop.us/collections/athlon-sports
Surfer|https://magazineshop.us/collections/surfer
Powder|https://magazineshop.us/collections/powder-magazine
First for Women|https://magazineshop.us/collections/first-for-women-magazine
Woman's World Specials|https://magazineshop.us/collections/womans-world-special
Health Food & Wellness|https://magazineshop.us/collections/health-food-and-wellness
Entertainment & Celebrity|https://magazineshop.us/collections/entertainment
Food & Recipes|https://magazineshop.us/collections/food-and-recipes
Football|https://magazineshop.us/collections/football
Politics|https://magazineshop.us/collections/politics"""

LEGACY_PRE_ART_DEFAULT_SOURCES = f"{CORE_DEFAULT_SOURCES}\n{ADDITIONAL_DEFAULT_SOURCES}"

FRESH_COLLECTION_SOURCES = "\n".join([
    _magazineshop_pages("Newest Releases", "new-releases", 4, 20),
    _magazineshop_pages("All In Stock", "all-in-stock-products", 1, 20),
    _magazineshop_pages("All Magazines", "all", 1, 20),
    _magazineshop_pages("Best Sellers", "best-sellers", 2, 10),
    _magazineshop_pages("Digital Magazines", "digital-magazines", 3, 10),
])

EXPANDED_CATEGORY_SOURCES = """Archie Comics|https://magazineshop.us/collections/archie-comics
DC Comics|https://magazineshop.us/collections/dc-comics
Celebrate with Woman's World|https://magazineshop.us/collections/celebrate-with-womans-world
Closer Weekly|https://magazineshop.us/collections/closer-weekly-1
Harvard Health|https://magazineshop.us/collections/harvard-health
Hoffman Media|https://magazineshop.us/collections/hoffman
Penny Press|https://magazineshop.us/collections/penny-press
Sur La Table|https://magazineshop.us/collections/sur-la-table
VegNews|https://magazineshop.us/collections/vegnews
Woman's World|https://magazineshop.us/collections/womans-world-magazine
Coloring Books|https://magazineshop.us/collections/coloring-books
Fitness and Active Living|https://magazineshop.us/collections/fitness-and-active-living
Gift Guide|https://magazineshop.us/collections/gift-guide
Men's Interest|https://magazineshop.us/collections/mens-interest
Music|https://magazineshop.us/collections/music
Special Interest|https://magazineshop.us/collections/special-interest
Taylor Swift|https://magazineshop.us/collections/taylor-swift
Women's Interest|https://magazineshop.us/collections/womens-interest"""

PRE_ART_DEFAULT_SOURCES = (
    f"{LEGACY_PRE_ART_DEFAULT_SOURCES}\n"
    f"{FRESH_COLLECTION_SOURCES}\n"
    f"{EXPANDED_CATEGORY_SOURCES}"
)

ART_DEFAULT_SOURCES = """Art in America|https://magazineshop.us/collections/art-in-america
Artforum|https://magazineshop.us/collections/artforum
Aspire Design and Home|https://magazineshop.us/collections/aspire-design-and-home
Decorator|https://magazineshop.us/collections/decorator
Home Design|https://magazineshop.us/collections/home-design"""

LEGACY_PRE_MATURE_DEFAULT_SOURCES = f"{LEGACY_PRE_ART_DEFAULT_SOURCES}\n{ART_DEFAULT_SOURCES}"
PRE_MATURE_DEFAULT_SOURCES = f"{PRE_ART_DEFAULT_SOURCES}\n{ART_DEFAULT_SOURCES}"

LEGACY_MATURE_DEFAULT_SOURCES = """Playboy|https://magazineshop.us/collections/playboy"""
LEGACY_DEFAULT_SOURCES = f"{LEGACY_PRE_MATURE_DEFAULT_SOURCES}\n{LEGACY_MATURE_DEFAULT_SOURCES}"

MATURE_DEFAULT_SOURCES = """Playboy|https://magazineshop.us/collections/playboy
Playboy Page 2|https://magazineshop.us/collections/playboy?page=2
Playboy Magazine|https://www.playboy.com/magazine
Penthouse Magazine|https://penthousemagazine.com/
Hustler Magazine|https://hustlermagazine.com/
Maxim|https://www.maxim.com/"""

DEFAULT_SOURCES = f"{PRE_MATURE_DEFAULT_SOURCES}\n{MATURE_DEFAULT_SOURCES}"

ROTATION_STATE_VERSION = "magazine-covers-rotation-v1"
COVER_CACHE_VERSION = "magazine-covers-cache-v2-title-crop"
IMAGE_CACHE_TTL = timedelta(hours=20)
COVER_CACHE_FILE_RETENTION = timedelta(days=7)
DAILY_LIBRARY_STATE_VERSION = "magazine-covers-daily-library-v1"
DAILY_LIBRARY_REFRESH_INTERVAL = timedelta(hours=6)
LEGACY_DAILY_LIBRARY_REFRESH_HOURS = 12
RANDOM_COVER_POOL_TTL = timedelta(days=7)
MAX_PI_SAFE_SOURCE_PIXELS = 900_000
DOWNLOAD_CHUNK_SIZE = 8192
RESAMPLING_FILTER = getattr(Image, "Resampling", Image).BICUBIC
DEFAULT_FIT_MODE = "triptych"
TRIPTYCH_COVER_COUNT = 3


class _ImageCandidateParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.meta_images = []
        self.images = []
        self._in_title = False
        self._title_text = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
            self._title_text = []
            return

        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
                self._add_meta_image(attrs.get("content"), key)
            return

        if tag != "img":
            return

        for raw_url in self._image_urls_from_attrs(attrs):
            self.images.append({
                "url": urljoin(self.base_url, raw_url),
                "alt": attrs.get("alt") or "",
                "width": attrs.get("width") or "",
                "height": attrs.get("height") or "",
                "class": attrs.get("class") or "",
                "id": attrs.get("id") or "",
            })

    def handle_data(self, data):
        if self._in_title:
            self._title_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "title" and self._in_title:
            self.title = _clean_text(" ".join(self._title_text))
            self._in_title = False

    def _add_meta_image(self, url, key):
        if not url:
            return
        self.meta_images.append({
            "url": urljoin(self.base_url, url),
            "alt": key,
            "width": "",
            "height": "",
            "class": "",
            "id": "",
        })

    def _image_urls_from_attrs(self, attrs):
        urls = []
        src = attrs.get("src") or attrs.get("data-src") or attrs.get("data-original")
        if src:
            urls.append(src)

        for attr in ["srcset", "data-srcset"]:
            srcset = attrs.get(attr)
            if not srcset:
                continue
            urls.extend(self._srcset_urls(srcset))
        return urls

    def _srcset_urls(self, srcset):
        candidates = []
        for part in srcset.split(","):
            bits = part.strip().split()
            if not bits:
                continue
            url = bits[0]
            score = 0
            if len(bits) > 1:
                match = re.search(r"(\d+)(?:w|x)?$", bits[-1])
                if match:
                    score = int(match.group(1))
            candidates.append((score, url))
        return [url for _score, url in sorted(candidates)]


class MagazineCovers(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["default_sources"] = DEFAULT_SOURCES
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        sources = self._sources_from_settings(settings)
        if not sources:
            raise RuntimeError("No magazine cover sources configured.")

        rotation_mode = (settings.get("rotationMode") or "random").lower()
        if self._daily_library_enabled(settings):
            image = self._generate_from_daily_library(sources, dimensions, settings, device_config, rotation_mode)
            if image:
                return image
            logger.warning("Daily magazine cover library unavailable; falling back to direct source fetch.")

        if rotation_mode == "single":
            ordered_sources = sources[:1]
        elif rotation_mode in {"rotate", "sequential"}:
            ordered_sources = self._rotation_order(sources)
        else:
            ordered_sources = self._random_order(sources)

        errors = []
        if self._fit_mode(settings) in {"triptych", "three_covers", "gallery"}:
            source_covers = []
            for source in ordered_sources:
                try:
                    cover = self._load_cover(source, dimensions)
                    source_covers.append((source, cover))
                    if len(source_covers) >= TRIPTYCH_COVER_COUNT:
                        break
                except Exception as exc:
                    logger.warning("Magazine cover failed for %s: %s", source["name"], exc)
                    errors.append(f"{source['name']}: {exc}")
                    if rotation_mode == "random":
                        self._remember_failure(source)

            if len(source_covers) >= TRIPTYCH_COVER_COUNT:
                image = self._fit_cover_triptych(source_covers, dimensions, settings)
                self._remember_successes(source_covers)
                self._write_cover_context(source_covers[0][0], source_covers[0][1])
                logger.info(
                    "Selected magazine cover triptych: %s",
                    " | ".join(source["name"] for source, _cover in source_covers),
                )
                return image

            if source_covers:
                logger.warning("Only %s covers loaded for triptych; falling back to first cover.", len(source_covers))
                source, cover = source_covers[0]
                image = self._fit_cover(cover["image"], dimensions, settings, source)
                self._remember_success(source, cover)
                self._write_cover_context(source, cover)
                return image

        for source in ordered_sources:
            try:
                cover = self._load_cover(source, dimensions)
                image = self._fit_cover(cover["image"], dimensions, settings, source)
                self._remember_success(source, cover)
                self._write_cover_context(source, cover)
                logger.info("Selected magazine cover: %s | %s", source["name"], cover["image_url"])
                return image
            except Exception as exc:
                logger.warning("Magazine cover failed for %s: %s", source["name"], exc)
                errors.append(f"{source['name']}: {exc}")
                if rotation_mode == "random":
                    self._remember_failure(source)

        detail = "; ".join(errors[-4:])
        logger.warning("No Pi-safe magazine cover could be fetched. %s", detail)
        return self._fallback_image(dimensions, "Magazine Covers", "No Pi-safe cover image")

    def _daily_library_enabled(self, settings):
        return _setting_enabled(settings.get("dailyLibraryMode", "true"))

    def _generate_from_daily_library(self, sources, dimensions, settings, device_config, rotation_mode):
        if self._daily_library_needs_refresh(sources, dimensions, settings):
            self._refresh_daily_library(sources, dimensions, settings)

        fit_mode = self._fit_mode(settings)
        display_count = TRIPTYCH_COVER_COUNT if fit_mode in {"triptych", "three_covers", "gallery"} else 1
        ordered_sources = self._daily_library_order(sources, dimensions, rotation_mode, display_count)
        if fit_mode in {"triptych", "three_covers", "gallery"}:
            triptych = self._triptych_from_cached_sources(ordered_sources, dimensions, settings)
            if triptych:
                return triptych

        for source in ordered_sources:
            cover = self._read_cached_cover(source, dimensions)
            if not cover:
                logger.warning("Daily magazine library cover missing for %s", source["name"])
                continue

            image = self._fit_cover(cover["image"], dimensions, settings, source)
            self._remember_success(source, cover)
            self._write_cover_context(source, cover)
            logger.info(
                "Selected magazine cover from daily library: %s | %s",
                source["name"],
                cover["image_url"],
            )
            return image
        return None

    def _triptych_from_cached_sources(self, ordered_sources, dimensions, settings):
        source_covers = []
        for source in ordered_sources:
            cover = self._read_cached_cover(source, dimensions)
            if not cover:
                logger.warning("Daily magazine library triptych cover missing for %s", source["name"])
                continue
            source_covers.append((source, cover))
            if len(source_covers) >= TRIPTYCH_COVER_COUNT:
                break

        if len(source_covers) < TRIPTYCH_COVER_COUNT:
            logger.warning(
                "Daily magazine cover library has only %s usable covers for triptych.",
                len(source_covers),
            )
            return None

        image = self._fit_cover_triptych(source_covers, dimensions, settings)
        self._remember_successes(source_covers)
        self._write_cover_context(source_covers[0][0], source_covers[0][1])
        logger.info(
            "Selected magazine cover triptych from daily library: %s",
            " | ".join(source["name"] for source, _cover in source_covers),
        )
        return image

    def _daily_library_needs_refresh(self, sources, dimensions, settings):
        state = self._read_state()
        if state.get("daily_library_version") != DAILY_LIBRARY_STATE_VERSION:
            return True
        if state.get("daily_library_pool_key") != self._pool_key(sources):
            return True
        if state.get("daily_library_dimensions") != self._dimensions_key(dimensions):
            return True
        if state.get("daily_library_day_key") != self._daily_library_day_key():
            return True
        if not state.get("daily_library_source_ids"):
            return True

        refreshed_at = self._parse_datetime(state.get("daily_library_refreshed_at"))
        if not refreshed_at:
            return True

        return self._now_utc() - refreshed_at >= self._daily_library_refresh_interval(settings)

    def _refresh_daily_library(self, sources, dimensions, settings):
        state = self._read_state()
        refreshed_source_ids = []
        errors = []

        for source in sources:
            try:
                cover = self._load_cover(source, dimensions, force_refresh=True)
                refreshed_source_ids.append(self._source_id(source))
                logger.info(
                    "Refreshed magazine cover library item: %s | %s",
                    source["name"],
                    cover.get("image_url"),
                )
            except Exception as exc:
                cached = self._read_cached_cover(source, dimensions)
                if cached:
                    refreshed_source_ids.append(self._source_id(source))
                    logger.warning(
                        "Magazine library refresh failed for %s, keeping cached cover: %s",
                        source["name"],
                        exc,
                    )
                else:
                    logger.warning("Magazine library refresh failed for %s: %s", source["name"], exc)
                    errors.append(f"{source['name']}: {exc}")

        now = self._now_utc().isoformat()
        state["daily_library_last_attempt_at"] = now
        state["daily_library_errors"] = errors[-8:]

        if refreshed_source_ids:
            state["daily_library_version"] = DAILY_LIBRARY_STATE_VERSION
            state["daily_library_pool_key"] = self._pool_key(sources)
            state["daily_library_dimensions"] = self._dimensions_key(dimensions)
            state["daily_library_day_key"] = self._daily_library_day_key()
            state["daily_library_refreshed_at"] = now
            state["daily_library_source_ids"] = refreshed_source_ids
            state["daily_library_queue"] = []
            state["daily_library_next_index"] = 0
            logger.info("Magazine cover library refreshed. | count: %s", len(refreshed_source_ids))
        else:
            logger.warning("Magazine cover library refresh produced no usable covers.")

        self._write_state(state)
        return bool(refreshed_source_ids)

    def _daily_library_order(self, sources, dimensions, rotation_mode, display_count=1):
        source_by_id = {self._source_id(source): source for source in sources}
        state = self._read_state()
        source_ids = [
            source_id
            for source_id in state.get("daily_library_source_ids", [])
            if source_id in source_by_id
        ]
        if not source_ids:
            return []
        display_count = max(1, min(int(display_count or 1), len(source_ids)))

        if rotation_mode == "single":
            return [source_by_id[source_ids[0]]]

        if rotation_mode in {"rotate", "sequential"}:
            next_index = int(state.get("daily_library_next_index") or 0) % len(source_ids)
            ordered_ids = source_ids[next_index:] + source_ids[:next_index]
            state["daily_library_next_index"] = (next_index + display_count) % len(source_ids)
            self._write_state(state)
            return [source_by_id[source_id] for source_id in ordered_ids]

        queue = [
            source_id
            for source_id in state.get("daily_library_queue", [])
            if source_id in source_by_id and source_id in source_ids
        ]
        if len(queue) < display_count:
            existing_ids = set(queue)
            refill = [
                source_id
                for source_id in self._new_daily_library_queue(source_ids, state, display_count)
                if source_id not in existing_ids
            ]
            queue.extend(refill)

        selected_ids = []
        while queue and len(selected_ids) < display_count:
            source_id = queue.pop(0)
            if source_id not in selected_ids:
                selected_ids.append(source_id)

        state["daily_library_queue"] = queue
        self._write_state(state)

        selected_id_set = set(selected_ids)
        remaining_ids = [source_id for source_id in queue if source_id not in selected_id_set]
        fallback_ids = [source_id for source_id in source_ids if source_id not in selected_id_set and source_id not in remaining_ids]
        ordered_ids = selected_ids + remaining_ids + fallback_ids
        return [source_by_id[source_id] for source_id in ordered_ids]

    def _new_daily_library_queue(self, source_ids, state, display_count):
        queue = list(source_ids)
        random.shuffle(queue)
        last_ids = set(state.get("last_source_ids") or [])
        last_source_id = state.get("last_source_id")
        if last_source_id:
            last_ids.add(last_source_id)

        fresh_first = [source_id for source_id in queue if source_id not in last_ids]
        delayed_last = [source_id for source_id in queue if source_id in last_ids]
        if len(fresh_first) >= min(display_count, len(source_ids)):
            return fresh_first + delayed_last
        return queue

    def _write_cover_context(self, source, cover):
        publication = str(source.get("name") or "Magazine").strip()
        title = str(cover.get("title") or publication).strip()
        write_context(
            "magazine_covers",
            {
                "kind": "magazine_cover",
                "source": "Magazine Covers",
                "summary": f"Magazine cover: {publication} - {title}"[:180],
                "facts": [
                    {"label": "publication", "value": publication[:80]},
                    {"label": "title", "value": title[:100]},
                ],
                "items": [{
                    "publication": publication[:80],
                    "title": title[:120],
                    "page_url": cover.get("page_url"),
                    "image_url": cover.get("image_url"),
                }],
            },
            generated_at=datetime.now(timezone.utc),
            ttl_seconds=int(IMAGE_CACHE_TTL.total_seconds()),
        )

    def _display_dimensions(self, device_config):
        return self.get_dimensions(device_config)

    def _parse_sources(self, sources_text):
        sources = []
        seen = set()
        for line in (sources_text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split("|", 1)]
            if len(parts) == 2:
                name, url = parts
            else:
                url = parts[0]
                name = urlparse(url).netloc or url

            if not url.startswith(("http://", "https://")):
                logger.warning("Ignoring magazine source with invalid URL: %s", line)
                continue

            source_id = f"{name}|{url}"
            if source_id in seen:
                continue
            seen.add(source_id)
            sources.append({"name": name or urlparse(url).netloc or url, "url": url})
        return sources

    def _sources_from_settings(self, settings):
        configured_text = settings.get("sources") or ""
        configured = self._parse_sources(configured_text)
        defaults = self._parse_sources(DEFAULT_SOURCES)
        if not configured:
            return defaults

        legacy_default_texts = [
            CORE_DEFAULT_SOURCES,
            LEGACY_PRE_ART_DEFAULT_SOURCES,
            PRE_ART_DEFAULT_SOURCES,
            LEGACY_PRE_MATURE_DEFAULT_SOURCES,
            PRE_MATURE_DEFAULT_SOURCES,
            LEGACY_DEFAULT_SOURCES,
        ]
        legacy_default_id_sets = [
            {self._source_id(source) for source in self._parse_sources(sources_text)}
            for sources_text in legacy_default_texts
        ]
        configured_ids = {self._source_id(source) for source in configured}
        if any(configured_ids == legacy_ids for legacy_ids in legacy_default_id_sets):
            merged = list(configured)
            merged_ids = set(configured_ids)
            for source in defaults:
                source_id = self._source_id(source)
                if source_id not in merged_ids:
                    merged.append(source)
                    merged_ids.add(source_id)
            return merged

        return configured

    def _rotation_order(self, sources):
        if len(sources) <= 1:
            return sources

        state = self._read_state()
        pool_key = self._pool_key(sources)
        pool_state = state.get(pool_key, {})
        next_index = int(pool_state.get("next_index") or 0) % len(sources)

        ordered = sources[next_index:] + sources[:next_index]
        state[pool_key] = {
            "next_index": (next_index + 1) % len(sources),
            "source_ids": [self._source_id(source) for source in sources],
        }
        self._write_state(state)
        return ordered

    def _random_order(self, sources):
        if len(sources) <= 1:
            return sources

        source_by_id = {self._source_id(source): source for source in sources}
        state = self._read_state()
        queue = [
            source_id
            for source_id in state.get("random_queue", [])
            if source_id in source_by_id
        ]

        if not queue:
            queue = list(source_by_id.keys())
            random.shuffle(queue)
            last_source_id = state.get("last_source_id")
            if len(queue) > 1 and queue[0] == last_source_id:
                for index, source_id in enumerate(queue[1:], start=1):
                    if source_id != last_source_id:
                        queue[0], queue[index] = queue[index], queue[0]
                        break
            state["random_queue"] = queue
            state["random_source_ids"] = list(source_by_id.keys())
            state["random_pool_saved_at"] = self._now_utc().isoformat()
            self._write_state(state)

        ordered_ids = list(queue)
        if len(ordered_ids) < len(source_by_id):
            retry_ids = [
                source_id
                for source_id in source_by_id
                if source_id not in set(ordered_ids)
            ]
            random.shuffle(retry_ids)
            ordered_ids.extend(retry_ids)

        return [source_by_id[source_id] for source_id in ordered_ids]

    def _load_cover(self, source, dimensions, force_refresh=False):
        if not force_refresh:
            cached = self._read_cached_cover(source, dimensions)
            if cached:
                return cached

        html_text = self._fetch_text(source["url"])
        parser = _ImageCandidateParser(source["url"])
        parser.feed(html_text or "")

        candidates = self._rank_candidates(source, parser)
        errors = []
        for candidate in candidates[:12]:
            try:
                image = self._download_candidate_image(candidate, dimensions)
                if image and self._looks_like_cover(image, candidate):
                    cover = {
                        "image": image,
                        "image_url": candidate["url"],
                        "page_url": source["url"],
                        "title": parser.title or source["name"],
                    }
                    self._write_cached_cover(source, dimensions, cover)
                    return cover
            except Exception as exc:
                errors.append(f"{candidate['url']}: {exc}")

        detail = "; ".join(errors[-3:])
        raise RuntimeError(f"no usable cover image found. {detail}")

    def _fetch_text(self, url):
        response = get_http_session().get(url, timeout=25, headers=REQUEST_HEADERS)
        response.raise_for_status()
        if not response.encoding:
            response.encoding = "utf-8"
        return response.text

    def _rank_candidates(self, source, parser):
        candidates = []
        for candidate in parser.meta_images + parser.images:
            url = candidate.get("url") or ""
            if not self._usable_image_url(url):
                continue
            candidate = dict(candidate)
            candidate["score"] = self._candidate_score(source, candidate)
            candidates.append(candidate)

        deduped = {}
        for candidate in candidates:
            existing = deduped.get(candidate["url"])
            if not existing or candidate["score"] > existing["score"]:
                deduped[candidate["url"]] = candidate
        return sorted(deduped.values(), key=lambda item: item["score"], reverse=True)

    def _usable_image_url(self, url):
        lower = (url or "").lower()
        if not lower.startswith(("http://", "https://")):
            return False
        reject = ["logo", "icon", "sprite", "avatar", "favicon", "newsletter", "adchoices"]
        if any(token in lower for token in reject):
            return False
        if lower.endswith((".svg", ".gif", ".ico")):
            return False
        return True

    def _candidate_score(self, source, candidate):
        haystack = " ".join([
            candidate.get("url", ""),
            candidate.get("alt", ""),
            candidate.get("class", ""),
            candidate.get("id", ""),
            source.get("name", ""),
        ]).lower()

        score = 0
        for token, weight in [
            ("cover", 80),
            ("magazine", 35),
            ("issue", 28),
            ("current", 20),
            ("new-yorker", 20),
            ("nationalgeographic", 20),
            ("natgeo", 20),
            ("vogue", 20),
            ("vanityfair", 20),
            ("wired", 20),
            ("time", 16),
            ("rolling", 16),
            ("stone", 16),
            ("billboard", 16),
            ("atlantic", 16),
            ("variety", 16),
            ("hollywood", 16),
            ("reporter", 16),
            ("weekly", 16),
            ("sports", 16),
            ("illustrated", 16),
            ("robb", 16),
            ("digest", 16),
            ("taste", 16),
            ("tv-guide", 16),
            ("newest", 14),
            ("best", 14),
            ("digital", 14),
            ("people", 14),
            ("newsweek", 14),
            ("mens", 14),
            ("journal", 14),
            ("athlon", 14),
            ("surfer", 14),
            ("powder", 14),
            ("first", 14),
            ("woman", 14),
            ("health", 14),
            ("wellness", 14),
            ("entertainment", 14),
            ("food", 14),
            ("football", 14),
            ("politics", 14),
            ("artforum", 16),
            ("art", 14),
            ("design", 14),
            ("decorator", 14),
            ("aspire", 14),
            ("home-design", 14),
            ("all-in-stock", 14),
            ("archie", 14),
            ("comics", 14),
            ("harvard", 14),
            ("hoffman", 14),
            ("penny", 14),
            ("sur-la-table", 14),
            ("vegnews", 14),
            ("coloring", 12),
            ("fitness", 14),
            ("gift", 12),
            ("music", 12),
            ("special-interest", 12),
            ("taylor", 12),
            ("swift", 12),
            ("playboy", 16),
            ("penthouse", 16),
            ("hustler", 16),
            ("maxim", 14),
        ]:
            if token in haystack:
                score += weight

        for token in ["logo", "newsletter", "avatar", "icon", "promo", "ad-"]:
            if token in haystack:
                score -= 60

        width = self._safe_int(candidate.get("width"), 0)
        height = self._safe_int(candidate.get("height"), 0)
        if width >= 250 and height >= 250:
            score += 20
        if height > width:
            score += 18
        if width >= 700 or height >= 700:
            score += 14
        return score

    def _download_candidate_image(self, candidate, dimensions):
        tmp_path = self._download_candidate_to_temp(candidate["url"])
        decode_path = tmp_path
        resized_path = None
        try:
            image_info = self._source_image_info(tmp_path)
            if image_info and image_info["pixels"] > MAX_PI_SAFE_SOURCE_PIXELS:
                if image_info["format"] == "WEBP":
                    raise RuntimeError("oversized WebP source cannot be safely downsampled on Pi")
                resized_path = self._downsample_to_pi_safe_image(tmp_path)
                decode_path = resized_path

            image = self.image_loader.from_file(str(decode_path), dimensions, resize=False)
            if not image:
                raise RuntimeError("image load returned empty")
            return image.convert("RGB")
        finally:
            for path in [tmp_path, resized_path]:
                if not path:
                    continue
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _download_candidate_to_temp(self, url):
        response = get_http_session().get(
            url,
            timeout=35,
            stream=True,
            headers=REQUEST_HEADERS,
        )
        response.raise_for_status()

        suffix = Path(urlparse(url).path).suffix or ".img"
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = Path(temp_file.name)
        try:
            with temp_file:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        temp_file.write(chunk)
            return tmp_path
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _image_exceeds_pi_safe_size(self, image_path):
        image_info = self._source_image_info(image_path)
        if not image_info:
            return False

        if image_info["pixels"] <= MAX_PI_SAFE_SOURCE_PIXELS:
            return False

        logger.info(
            "Skipping oversized magazine cover candidate for Pi-safe decode: %sx%s",
            image_info["width"],
            image_info["height"],
        )
        return True

    def _source_image_info(self, image_path):
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                image_format = (image.format or "").upper()
        except Exception:
            return None

        return {
            "width": width,
            "height": height,
            "pixels": width * height,
            "format": image_format,
        }

    def _downsample_to_pi_safe_image(self, image_path):
        with Image.open(image_path) as image:
            original_size = image.size
            target_size = self._pi_safe_downsample_size(original_size)
            image.draft("RGB", target_size)
            image = ImageOps.exif_transpose(image)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.thumbnail(target_size, RESAMPLING_FILTER)

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            resized_path = Path(temp_file.name)
            try:
                with temp_file:
                    image.save(temp_file, format="JPEG", quality=88)
                logger.info(
                    "Downsampled oversized magazine cover for Pi-safe decode: %sx%s -> %sx%s",
                    original_size[0],
                    original_size[1],
                    image.size[0],
                    image.size[1],
                )
                return resized_path
            except Exception:
                resized_path.unlink(missing_ok=True)
                raise

    def _pi_safe_downsample_size(self, size):
        width, height = max(1, int(size[0])), max(1, int(size[1]))
        pixels = width * height
        if pixels <= MAX_PI_SAFE_SOURCE_PIXELS:
            return width, height
        scale = (MAX_PI_SAFE_SOURCE_PIXELS / pixels) ** 0.5
        return max(1, int(width * scale)), max(1, int(height * scale))

    def _looks_like_cover(self, image, candidate):
        width, height = image.size
        if width < 160 or height < 160:
            return False
        ratio = max(width, height) / max(1, min(width, height))
        score = candidate.get("score", 0)
        if ratio >= 1.15 and max(width, height) >= 420:
            return True
        return score >= 80 and max(width, height) >= 300

    def _fit_cover(self, image, dimensions, settings, source=None):
        fit_mode = self._fit_mode(settings)
        image = ImageOps.exif_transpose(image).convert("RGB")

        if fit_mode == "cover":
            fitted = self._fit_cover_crop(image, dimensions)
            return self._with_source_label(fitted, source, settings)

        should_rotate = fit_mode in {"rotate_full", "rotate", "auto"} and image.height > image.width
        if should_rotate:
            image = image.rotate(90, expand=True)

        fitted = ImageOps.contain(image, dimensions, method=Image.LANCZOS)
        background = self._solid_background(dimensions, settings)
        x = (dimensions[0] - fitted.width) // 2
        y = (dimensions[1] - fitted.height) // 2
        background.paste(fitted, (x, y))
        return self._with_source_label(background, source, settings)

    def _fit_mode(self, settings):
        return str(settings.get("fitMode") or DEFAULT_FIT_MODE).strip().lower()

    def _fit_cover_triptych(self, source_covers, dimensions, settings):
        cover_images = [
            ImageOps.exif_transpose(cover["image"]).convert("RGB")
            for _source, cover in source_covers[:TRIPTYCH_COVER_COUNT]
        ]
        canvas = self._triptych_background(cover_images, dimensions, settings)
        width, height = dimensions
        column_width = width // TRIPTYCH_COVER_COUNT

        for index, image in enumerate(cover_images):
            x0 = index * column_width
            target_width = column_width if index < TRIPTYCH_COVER_COUNT - 1 else width - x0
            fitted = ImageOps.contain(image, (target_width, height), method=Image.LANCZOS)
            x = x0 + (target_width - fitted.width) // 2
            y = (height - fitted.height) // 2
            canvas.paste(fitted, (x, y))

        return canvas

    def _triptych_background(self, cover_images, dimensions, settings):
        background = self._solid_background(dimensions, settings)
        if not cover_images:
            return background
        return self._background(dimensions, settings, cover_images[0])

    def _fit_cover_crop(self, image, dimensions):
        target_width, target_height = dimensions
        target_ratio = target_width / target_height
        image_ratio = image.width / image.height

        if image_ratio > target_ratio:
            crop_width = max(1, min(image.width, int(round(image.height * target_ratio))))
            title_focus = self._title_focus_region(image)
            if title_focus:
                x = self._crop_offset_for_focus(title_focus["center_x"], image.width, crop_width)
            else:
                x = max(0, (image.width - crop_width) // 2)
            crop_box = (x, 0, x + crop_width, image.height)
        else:
            crop_height = max(1, min(image.height, int(round(image.width / target_ratio))))
            y = self._masthead_crop_offset(image, crop_height)
            crop_box = (0, y, image.width, y + crop_height)

        cropped = image.crop(crop_box)
        return cropped.resize((target_width, target_height), Image.LANCZOS)

    def _masthead_crop_offset(self, image, crop_height):
        max_offset = max(0, image.height - crop_height)
        if max_offset == 0:
            return 0

        title_focus = self._title_focus_region(image)
        if title_focus:
            if title_focus["center_y"] <= crop_height * 0.45:
                return 0
            offset = int(round(title_focus["center_y"] - crop_height * 0.15))
            return max(0, min(max_offset, offset))

        return 0

    def _title_focus_region(self, image):
        sample = image.convert("L")
        sample.thumbnail((320, 320), Image.BILINEAR)
        if sample.width < 80 or sample.height < 80:
            return None

        edges = sample.filter(ImageFilter.FIND_EDGES)
        scan_height = max(1, int(sample.height * 0.68))
        window_height = max(18, min(scan_height, sample.height // 6))
        if window_height >= scan_height:
            return None

        step = max(4, window_height // 4)
        best = None
        best_score = 0.0

        for y in range(0, scan_height - window_height + 1, step):
            box = (0, y, sample.width, y + window_height)
            gray_region = sample.crop(box)
            edge_region = edges.crop(box)
            score = self._title_region_score(gray_region, edge_region, y, scan_height)
            if score > best_score:
                best_score = score
                best = {
                    "center_x": sample.width / 2,
                    "center_y": y + window_height / 2,
                    "score": score,
                }

        if not best or best_score < 42:
            return None

        return {
            "center_x": best["center_x"] * image.width / sample.width,
            "center_y": best["center_y"] * image.height / sample.height,
            "score": best_score,
        }

    def _title_region_score(self, gray_region, edge_region, y, scan_height):
        area = max(1, gray_region.width * gray_region.height)
        gray_hist = gray_region.histogram()
        edge_hist = edge_region.histogram()
        dark_ratio = sum(gray_hist[:90]) / area
        light_ratio = sum(gray_hist[200:]) / area
        edge_ratio = sum(edge_hist[32:]) / area
        edge_mean = ImageStat.Stat(edge_region).mean[0]
        contrast = ImageStat.Stat(gray_region).stddev[0]
        coverage = self._title_region_horizontal_coverage(gray_region, edge_region)
        top_bias = 1 - min(1.0, y / max(1, scan_height))

        score = (
            edge_mean * 1.35
            + contrast * 0.65
            + min(42.0, dark_ratio * 120)
            + min(30.0, edge_ratio * 260)
            + coverage * 34
            + top_bias * 12
        )

        if dark_ratio > 0.92 or light_ratio > 0.98:
            score *= 0.45
        if dark_ratio < 0.025 and edge_ratio < 0.035:
            score *= 0.55
        return score

    def _title_region_horizontal_coverage(self, gray_region, edge_region):
        segments = 8
        active = 0
        for index in range(segments):
            left = int(round(index * gray_region.width / segments))
            right = int(round((index + 1) * gray_region.width / segments))
            if right <= left:
                continue
            box = (left, 0, right, gray_region.height)
            gray_slice = gray_region.crop(box)
            edge_slice = edge_region.crop(box)
            area = max(1, gray_slice.width * gray_slice.height)
            dark_ratio = sum(gray_slice.histogram()[:90]) / area
            edge_ratio = sum(edge_slice.histogram()[32:]) / area
            if dark_ratio > 0.055 or edge_ratio > 0.04:
                active += 1
        return active / segments

    def _crop_offset_for_focus(self, focus_coord, full_size, crop_size):
        max_offset = max(0, full_size - crop_size)
        if max_offset == 0:
            return 0
        offset = int(round(focus_coord - crop_size / 2))
        return max(0, min(max_offset, offset))

    def _with_source_label(self, image, source, settings):
        if str(settings.get("showSourceLabel", "true")).lower() in {"false", "0", "off", "no"}:
            return image

        label = str((source or {}).get("name") or "").strip()
        if not label:
            return image

        image = image.copy()
        draw = ImageDraw.Draw(image)
        width, height = image.size
        max_label_width = max(120, int(width * 0.58))
        font_size = max(16, min(width, height) // 22)
        font = self._fallback_font(font_size, bold=True)
        while font_size > 12 and draw.textlength(label.upper(), font=font) > max_label_width:
            font_size -= 1
            font = self._fallback_font(font_size, bold=True)
        text = self._fit_text(draw, label.upper(), font, max_label_width)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        pad_x = max(8, width // 80)
        pad_y = max(5, height // 96)
        x = max(8, width // 70)
        y = height - text_h - pad_y * 2 - max(8, height // 70)
        box = (x, y, x + text_w + pad_x * 2, y + text_h + pad_y * 2)
        draw.rectangle(box, fill="white", outline="black", width=1)
        draw.text((x + pad_x, y + pad_y - bbox[1]), text, fill="black", font=font)
        return image

    def _fallback_image(self, dimensions, title, subtitle):
        image = Image.new("RGB", dimensions, "white")
        draw = ImageDraw.Draw(image)
        width, height = dimensions
        border = max(12, min(width, height) // 24)
        draw.rectangle((border, border, width - border, height - border), outline="black", width=3)
        draw.line((border, height // 2, width - border, height // 2), fill=(180, 180, 180), width=2)

        title_font = self._fallback_font(max(28, width // 12), bold=True)
        subtitle_font = self._fallback_font(max(18, width // 24))
        self._draw_centered(draw, title, width // 2, height // 2 - 46, title_font, "black")
        self._draw_centered(draw, subtitle, width // 2, height // 2 + 24, subtitle_font, (70, 70, 70))
        return image

    def _fallback_font(self, size, bold=False):
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for path in paths:
            try:
                if Path(path).is_file():
                    return ImageFont.truetype(path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    def _draw_centered(self, draw, text, x, y, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(
            (x - (bbox[2] - bbox[0]) // 2, y - (bbox[3] - bbox[1]) // 2),
            text,
            font=font,
            fill=fill,
        )

    def _fit_text(self, draw, text, font, max_width):
        if draw.textlength(text, font=font) <= max_width:
            return text

        candidate = text
        while candidate and draw.textlength(candidate, font=font) > max_width:
            candidate = candidate[:-1].rstrip()
        return candidate or text[:1]

    def _solid_background(self, dimensions, settings):
        color = (settings.get("backgroundColor") or "white").lower()
        base_color = (0, 0, 0) if color == "black" else (255, 255, 255)
        return Image.new("RGB", dimensions, base_color)

    def _background(self, dimensions, settings, image):
        color = (settings.get("backgroundColor") or "white").lower()
        base_color = (0, 0, 0) if color == "black" else (255, 255, 255)

        style = (settings.get("backgroundStyle") or "blur").lower()
        if style in {"plain", "solid"}:
            return self._solid_background(dimensions, settings)

        try:
            backdrop = ImageOps.fit(image, dimensions, method=Image.LANCZOS)
            blur_radius = max(4, min(dimensions) // 60)
            backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            backdrop = ImageEnhance.Color(backdrop).enhance(0.35)
            backdrop = ImageEnhance.Contrast(backdrop).enhance(0.82)
            wash = Image.new("RGB", dimensions, base_color)
            return Image.blend(backdrop, wash, 0.5 if color != "black" else 0.35)
        except Exception as exc:
            logger.warning("Could not render blurred magazine background: %s", exc)
            return self._solid_background(dimensions, settings)

    def _read_cached_cover(self, source, dimensions):
        meta_path = self._cache_meta_path(source, dimensions)
        try:
            if not meta_path.is_file():
                return None
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(meta.get("fetched_at"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched_at > IMAGE_CACHE_TTL:
                return None
            image_path = Path(meta.get("image_path") or "")
            if not image_path.is_file():
                return None
            if self._image_exceeds_pi_safe_size(image_path):
                return None
            loaded = self.image_loader.from_file(str(image_path), dimensions, resize=False)
            if not loaded:
                return None
            return {
                "image": loaded,
                "image_url": meta.get("image_url"),
                "page_url": meta.get("page_url"),
                "title": meta.get("title"),
            }
        except Exception as exc:
            logger.warning("Could not read cached magazine cover for %s: %s", source["name"], exc)
            return None

    def _write_cached_cover(self, source, dimensions, cover):
        meta_path = self._cache_meta_path(source, dimensions)
        image_path = meta_path.with_suffix(".jpg")
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cover["image"].save(image_path, quality=92)
            meta = {
                "source": source,
                "image_url": cover.get("image_url"),
                "page_url": cover.get("page_url"),
                "title": cover.get("title"),
                "image_path": str(image_path),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=True, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not cache magazine cover for %s: %s", source["name"], exc)

    def _remember_success(self, source, cover):
        self._remember_successes([(source, cover)])

    def _remember_successes(self, source_covers):
        source_covers = list(source_covers or [])
        if not source_covers:
            return

        first_source, first_cover = source_covers[0]
        state = self._read_state()
        source_ids = [self._source_id(source) for source, _cover in source_covers]
        state["last_source"] = first_source.get("name")
        state["last_source_id"] = source_ids[0]
        state["last_sources"] = [source.get("name") for source, _cover in source_covers]
        state["last_source_ids"] = source_ids
        state["last_page_url"] = first_cover.get("page_url")
        state["last_image_url"] = first_cover.get("image_url")
        state["last_title"] = first_cover.get("title")
        state["last_page_urls"] = [cover.get("page_url") for _source, cover in source_covers if cover.get("page_url")]
        state["last_image_urls"] = [cover.get("image_url") for _source, cover in source_covers if cover.get("image_url")]
        state["last_displayed_at"] = datetime.now(timezone.utc).isoformat()
        if isinstance(state.get("random_queue"), list):
            source_id_set = set(source_ids)
            state["random_queue"] = [
                queued_id for queued_id in state["random_queue"] if queued_id not in source_id_set
            ]
        self._write_state(state)

    def _remember_failure(self, source):
        state = self._read_state()
        queue = state.get("random_queue")
        if not isinstance(queue, list):
            return

        source_id = self._source_id(source)
        updated_queue = [queued for queued in queue if queued != source_id]
        if updated_queue == queue:
            return

        state["random_queue"] = updated_queue
        self._write_state(state)

    def _state_path(self):
        return self._cache_dir() / "rotation_state.json"

    def _cache_meta_path(self, source, dimensions):
        key = hashlib.sha256(
            f"{COVER_CACHE_VERSION}|{source['name']}|{source['url']}|{dimensions[0]}x{dimensions[1]}".encode("utf-8")
        ).hexdigest()[:20]
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", source["name"]).strip("_") or "source"
        return self._cache_dir() / "covers" / f"{safe_name}_{key}.json"

    def _cache_dir(self):
        return self.cache_dir(env_var="INKYPI_MAGAZINE_COVERS_CACHE", leaf=".magazine_covers_cache", create=False)

    def _prune_stale_cover_cache_files(self):
        covers_dir = self._cache_dir() / "covers"
        if not covers_dir.is_dir():
            return 0

        cutoff = self._now_utc() - COVER_CACHE_FILE_RETENTION
        removed = 0
        try:
            resolved_covers_dir = covers_dir.resolve()
        except Exception:
            resolved_covers_dir = covers_dir

        for meta_path in covers_dir.glob("*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                fetched_at = self._parse_datetime(meta.get("fetched_at"))
                if not fetched_at:
                    fetched_at = datetime.fromtimestamp(meta_path.stat().st_mtime, timezone.utc)
                if fetched_at >= cutoff:
                    continue

                image_path = Path(meta.get("image_path") or meta_path.with_suffix(".jpg"))
                try:
                    image_parent = image_path.resolve().parent
                except Exception:
                    image_parent = image_path.parent
                if image_parent != resolved_covers_dir:
                    image_path = meta_path.with_suffix(".jpg")

                for cache_path in (meta_path, image_path):
                    if cache_path.is_file():
                        cache_path.unlink()
                        removed += 1
            except Exception as exc:
                logger.warning("Could not prune stale magazine cover cache %s: %s", meta_path, exc)
        return removed

    def _read_state(self):
        self._prune_stale_cover_cache_files()
        path = self._state_path()
        try:
            if path.is_file():
                state = json.loads(path.read_text(encoding="utf-8"))
                legacy_saved_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                return self._prune_stale_cover_pool_state(state, legacy_saved_at=legacy_saved_at)
        except Exception as exc:
            logger.warning("Could not read Magazine Covers state %s: %s", path, exc)
        return {}

    def _write_state(self, state):
        state = self._prune_stale_cover_pool_state(state)
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(state, ensure_ascii=True, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            path.write_text(text, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _prune_stale_cover_pool_state(self, state, legacy_saved_at=None):
        if not isinstance(state, dict):
            return {}

        pruned = dict(state)
        if self._pool_timestamp_is_stale(pruned.get("random_pool_saved_at") or legacy_saved_at):
            for key in ("random_queue", "random_source_ids", "random_pool_saved_at"):
                pruned.pop(key, None)

        if self._pool_timestamp_is_stale(pruned.get("daily_library_refreshed_at") or legacy_saved_at):
            for key in (
                "daily_library_source_ids",
                "daily_library_queue",
                "daily_library_next_index",
                "daily_library_refreshed_at",
                "daily_library_day_key",
                "daily_library_dimensions",
                "daily_library_pool_key",
                "daily_library_version",
            ):
                pruned.pop(key, None)

        return pruned

    def _pool_timestamp_is_stale(self, value):
        if isinstance(value, datetime):
            saved_at = value
        else:
            saved_at = self._parse_datetime(value)

        if not saved_at:
            return False
        if saved_at.tzinfo is None:
            saved_at = saved_at.replace(tzinfo=timezone.utc)

        return self._now_utc() - saved_at.astimezone(timezone.utc) > RANDOM_COVER_POOL_TTL

    def _pool_key(self, sources):
        raw = "|".join([ROTATION_STATE_VERSION] + [self._source_id(source) for source in sources])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _source_id(self, source):
        return f"{source['name']}|{source['url']}"

    def _dimensions_key(self, dimensions):
        return f"{dimensions[0]}x{dimensions[1]}"

    def _daily_library_day_key(self):
        return self._now_utc().astimezone().date().isoformat()

    def _daily_library_refresh_interval(self, settings):
        try:
            hours = float(settings.get("libraryRefreshHours") or 0)
        except (TypeError, ValueError):
            hours = 0
        if hours <= 0 or hours == LEGACY_DAILY_LIBRARY_REFRESH_HOURS:
            return DAILY_LIBRARY_REFRESH_INTERVAL
        return timedelta(hours=max(1.0, hours))

    def _parse_datetime(self, value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _now_utc(self):
        return datetime.now(timezone.utc)

    def _safe_int(self, value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


def _setting_enabled(value):
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()
