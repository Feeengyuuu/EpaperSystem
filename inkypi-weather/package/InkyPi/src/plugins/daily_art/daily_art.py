from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import get_available_font_names, get_font
from utils.image_utils import text_width
from utils.safe_image import ImageLimits, safe_open_image

logger = logging.getLogger(__name__)

PLUGIN_ID = "daily_art"
CACHE_SCHEMA_VERSION = "daily-art-cache-v1"
STATE_SCHEMA_VERSION = "daily-art-state-v1"
DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_FONT = "Jost"
DEFAULT_LAYOUT_MODE = "auto_gallery"
DEFAULT_GALLERY_COUNT = 3
GALLERY_LAYOUT_MODES = {"auto_gallery", "gallery"}
DEFAULT_SOURCES = ("met", "artic", "europeana", "harvard")
DEFAULT_QUERY_TERMS = (
    "painting",
    "portrait",
    "landscape",
    "still life",
    "impressionism",
    "japanese print",
    "watercolor",
    "drawing",
    "flowers",
    "night",
    "river",
    "garden",
    "rembrandt",
    "vermeer",
    "monet",
    "hokusai",
)

MET_SEARCH_URL = "https://collectionapi.metmuseum.org/public/collection/v1/search"
MET_OBJECT_URL = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{object_id}"
ARTIC_SEARCH_URL = "https://api.artic.edu/api/v1/artworks/search"
ARTIC_IIIF_BASE_URL = "https://www.artic.edu/iiif/2"
EUROPEANA_SEARCH_URL = "https://api.europeana.eu/record/v2/search.json"
HARVARD_OBJECT_URL = "https://api.harvardartmuseums.org/object"

REQUEST_HEADERS = {
    "User-Agent": "InkyPi DailyArt/1.0",
    "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
}
IMAGE_HEADERS = {
    "User-Agent": "InkyPi DailyArt/1.0",
    "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8,*/*;q=0.5",
}
ARTIC_HEADERS = {
    **REQUEST_HEADERS,
    "AIC-User-Agent": "InkyPi DailyArt",
}

EUROPEANA_ENV_KEYS = (
    "EUROPEANA_API_KEY",
    "EUROPEANA_KEY",
    "EUROPEANA_WSKEY",
    "EUROPEANA_APIKEY",
    "EUROPEANA_PERSONAL_API_KEY",
    "Europeana_API_Key",
    "Europeana",
    "Europeana_Key",
    "EuropeanaApiKey",
    "EUROPEANA",
)
HARVARD_ENV_KEYS = (
    "HARVARD_ART_MUSEUMS_API_KEY",
    "HARVARD_ART_MUSEUM_API_KEY",
    "HARVARD_ART_MUSEUMS_KEY",
    "HARVARD_ART_MUSEUM_KEY",
    "HARVARD_ART_API_KEY",
    "HARVARD_API_KEY",
    "Harvard_Art_Museums_Key",
    "Harvard_Art_Museum_Key",
    "Harvard_Art_Museums_API_Key",
    "Harvard_Art_Museum_API_Key",
    "Harvard_Art_Key",
    "HarvardArtMuseums",
    "HAM_API_KEY",
    "Harverd_Key",
    "Harvard",
    "HARVARD",
)

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS


@dataclass
class ArtworkCandidate:
    source: str
    source_label: str
    artwork_id: str
    title: str
    artist: str = ""
    date: str = ""
    medium: str = ""
    museum: str = ""
    rights: str = ""
    image_url: str = ""
    page_url: str = ""
    culture: str = ""


class DailyArt(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["default_query_terms"] = ", ".join(DEFAULT_QUERY_TERMS)
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        now = self._now_for_device(device_config)
        rotation_key = self._rotation_key(now, settings)
        cache_key = self._cache_key(settings, dimensions, rotation_key)
        cache = self._read_daily_cache()

        force_refresh = _enabled(settings.get("forceRefresh"), default=False)
        if (
            cache.get("schema") == CACHE_SCHEMA_VERSION
            and cache.get("cache_key") == cache_key
            and not force_refresh
        ):
            image_path = cache.get("image_path")
            if image_path and Path(image_path).is_file():
                logger.info("Using cached DailyArt image for %s: %s", rotation_key, image_path)
                self._write_art_context(cache.get("artworks") or cache.get("artwork") or {}, now, settings)
                return safe_open_image(image_path).convert("RGB")

        self._prune_cache_files()
        state = self._read_state()
        candidates = self._candidate_pool(settings, device_config, now)
        ordered = self._candidate_order(candidates, state, rotation_key)
        attempt_limit = _bounded_int(settings.get("maxAttempts"), 10, 1, 40)
        layout_mode = self._layout_mode(settings)
        gallery_count = self._gallery_count(settings)
        gallery_selection = []
        landscape_fallback = None
        errors = []

        for candidate in ordered[:attempt_limit]:
            try:
                source_image = self._download_image_preview(candidate.image_url, dimensions, settings)
                if not source_image:
                    raise RuntimeError("image could not be downloaded")

                if layout_mode == "single":
                    return self._save_artwork_render(
                        [(candidate, source_image)],
                        "single",
                        dimensions,
                        settings,
                        state,
                        rotation_key,
                        cache_key,
                        now,
                    )

                if layout_mode == "gallery" or self._is_portrait_art(source_image):
                    gallery_selection.append((candidate, source_image))
                    if len(gallery_selection) >= gallery_count:
                        return self._save_artwork_render(
                            gallery_selection,
                            "gallery",
                            dimensions,
                            settings,
                            state,
                            rotation_key,
                            cache_key,
                            now,
                        )
                    continue

                if landscape_fallback is None:
                    landscape_fallback = (candidate, source_image)
            except Exception as exc:
                errors.append(f"{candidate.artwork_id}: {exc}")
                logger.warning("DailyArt candidate failed for %s: %s", candidate.artwork_id, exc)

        if gallery_selection:
            return self._save_artwork_render(
                gallery_selection,
                "gallery",
                dimensions,
                settings,
                state,
                rotation_key,
                cache_key,
                now,
            )

        if landscape_fallback:
            return self._save_artwork_render(
                [landscape_fallback],
                "single",
                dimensions,
                settings,
                state,
                rotation_key,
                cache_key,
                now,
            )

        stale_image = cache.get("image_path")
        if stale_image and Path(stale_image).is_file():
            logger.warning("DailyArt using stale cached image after candidate failures: %s", "; ".join(errors[-4:]))
            self._write_art_context(cache.get("artworks") or cache.get("artwork") or {}, now, settings)
            return safe_open_image(stale_image).convert("RGB")

        logger.warning("DailyArt failed without usable stale cache: %s", "; ".join(errors[-4:]))
        return self._fallback_image(dimensions, "Daily Art", "No museum scan available")

    def _display_dimensions(self, device_config):
        dimensions = self.get_dimensions(device_config)
        return tuple(int(value) for value in dimensions)

    def _now_for_device(self, device_config):
        timezone_name = ""
        try:
            timezone_name = device_config.get_config("timezone", default="") or DEFAULT_TIMEZONE
            import pytz

            return datetime.now(pytz.timezone(timezone_name))
        except Exception:
            return datetime.now(timezone.utc)

    def _rotation_key(self, now, settings):
        cadence = str(settings.get("rotationCadence") or "daily").strip().lower()
        if cadence == "every_refresh":
            return now.strftime("%Y-%m-%d-%H-%M-%S-%f")
        if cadence == "hourly":
            return now.strftime("%Y-%m-%d-%H")
        return now.strftime("%Y-%m-%d")

    def _cache_key(self, settings, dimensions, rotation_key):
        parts = [
            CACHE_SCHEMA_VERSION,
            rotation_key,
            str(dimensions),
            self._source_mode(settings),
            ",".join(self._query_terms(settings)),
            str(settings.get("fitMode") or "contain"),
            self._layout_mode(settings),
            str(self._gallery_count(settings)),
            str(settings.get("showCaption") or "true"),
            str(settings.get("backgroundColor") or "warm"),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def _layout_mode(self, settings):
        raw = str(settings.get("layoutMode") or DEFAULT_LAYOUT_MODE).strip().lower()
        aliases = {
            "auto": "auto_gallery",
            "portrait": "auto_gallery",
            "portrait_gallery": "auto_gallery",
            "triptych": "gallery",
            "three": "gallery",
            "three_artworks": "gallery",
        }
        mode = aliases.get(raw, raw)
        if mode in {"single", "gallery", "auto_gallery"}:
            return mode
        return DEFAULT_LAYOUT_MODE

    def _gallery_count(self, settings):
        return _bounded_int(settings.get("galleryCount"), DEFAULT_GALLERY_COUNT, 1, 4)

    def _is_portrait_art(self, image):
        if not image:
            return False
        return int(image.height) >= int(image.width) * 1.08

    def _candidate_pool(self, settings, device_config, now):
        sources = self._enabled_sources(settings, device_config)
        if not sources:
            logger.warning("DailyArt has no enabled sources with available credentials.")
            return []

        terms = self._query_terms(settings)
        rng = random.Random(self._stable_seed(now.strftime("%Y-%m-%d"), self._source_mode(settings), ",".join(terms)))
        source_order = list(sources)
        rng.shuffle(source_order)
        term_order = list(terms)
        rng.shuffle(term_order)
        limit = _bounded_int(settings.get("sourceLimit"), 12, 3, 50)

        candidates = []
        for index, source in enumerate(source_order):
            term = term_order[index % len(term_order)] if term_order else "painting"
            try:
                if source == "met":
                    candidates.extend(self._fetch_met_candidates(term, limit, rng))
                elif source == "artic":
                    candidates.extend(self._fetch_artic_candidates(term, limit, settings, rng))
                elif source == "europeana":
                    key = self._env_value(device_config, EUROPEANA_ENV_KEYS, settings, ("europeanaApiKey",))
                    if key:
                        candidates.extend(self._fetch_europeana_candidates(term, limit, key, rng))
                elif source == "harvard":
                    key = self._env_value(device_config, HARVARD_ENV_KEYS, settings, ("harvardApiKey",))
                    if key:
                        candidates.extend(self._fetch_harvard_candidates(term, limit, key, rng))
            except Exception as exc:
                logger.warning("DailyArt source %s failed: %s", source, exc)

        return self._dedupe_candidates(candidates)

    def _fetch_met_candidates(self, term, limit, rng):
        payload = self._get_json(MET_SEARCH_URL, {"hasImages": "true", "q": term}, headers=REQUEST_HEADERS)
        object_ids = payload.get("objectIDs") if isinstance(payload, dict) else []
        if not isinstance(object_ids, list):
            return []
        rng.shuffle(object_ids)

        candidates = []
        for object_id in object_ids[: max(limit * 2, 8)]:
            if len(candidates) >= limit:
                break
            try:
                detail = self._get_json(MET_OBJECT_URL.format(object_id=object_id), {}, headers=REQUEST_HEADERS)
                if not isinstance(detail, dict):
                    continue
                image_url = _first_text(detail, ["primaryImageSmall", "primaryImage"])
                if not image_url:
                    continue
                if detail.get("isPublicDomain") is False:
                    continue
                candidates.append(ArtworkCandidate(
                    source="met",
                    source_label="The Met",
                    artwork_id=f"met:{detail.get('objectID') or object_id}",
                    title=_first_text(detail, ["title"]) or "Untitled",
                    artist=_first_text(detail, ["artistDisplayName"]),
                    date=_first_text(detail, ["objectDate"]),
                    medium=_first_text(detail, ["medium"]),
                    museum="The Metropolitan Museum of Art",
                    rights="Open Access / Public Domain",
                    image_url=image_url,
                    page_url=_first_text(detail, ["objectURL"]),
                    culture=_first_text(detail, ["culture", "department"]),
                ))
            except Exception as exc:
                logger.debug("Met object %s skipped: %s", object_id, exc)
        return candidates

    def _fetch_artic_candidates(self, term, limit, settings, rng):
        fields = ",".join([
            "id",
            "title",
            "artist_display",
            "artist_title",
            "date_display",
            "image_id",
            "medium_display",
            "classification_title",
            "place_of_origin",
            "is_public_domain",
        ])
        params = {
            "q": term,
            "query[term][is_public_domain]": "true",
            "limit": int(limit),
            "fields": fields,
        }
        payload = self._get_json(ARTIC_SEARCH_URL, params, headers=ARTIC_HEADERS)
        records = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(records, list):
            return []
        rng.shuffle(records)
        iiif_base = _first_text(payload.get("config") if isinstance(payload, dict) else {}, ["iiif_url"]) or ARTIC_IIIF_BASE_URL
        iiif_width = _bounded_int(settings.get("iiifWidth"), 1200, 600, 2400)

        candidates = []
        for record in records:
            if not isinstance(record, dict):
                continue
            image_id = _first_text(record, ["image_id"])
            if not image_id:
                continue
            image_url = f"{iiif_base.rstrip('/')}/{image_id}/full/{iiif_width},/0/default.jpg"
            artist = _first_text(record, ["artist_title"]) or _first_text(record, ["artist_display"])
            candidates.append(ArtworkCandidate(
                source="artic",
                source_label="Art Institute of Chicago",
                artwork_id=f"artic:{record.get('id')}",
                title=_first_text(record, ["title"]) or "Untitled",
                artist=artist,
                date=_first_text(record, ["date_display"]),
                medium=_first_text(record, ["medium_display", "classification_title"]),
                museum="Art Institute of Chicago",
                rights="Public Domain",
                image_url=image_url,
                page_url=f"https://www.artic.edu/artworks/{record.get('id')}",
                culture=_first_text(record, ["place_of_origin"]),
            ))
        return candidates

    def _fetch_europeana_candidates(self, term, limit, api_key, rng):
        params = {
            "wskey": api_key,
            "query": term,
            "rows": int(limit),
            "media": "true",
            "thumbnail": "true",
            "reusability": "open",
            "profile": "rich",
            "qf": "TYPE:IMAGE",
        }
        payload = self._get_json(EUROPEANA_SEARCH_URL, params, headers=REQUEST_HEADERS)
        records = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(records, list):
            return []
        rng.shuffle(records)

        candidates = []
        for record in records:
            if not isinstance(record, dict):
                continue
            image_url = _first_list_text(record.get("edmIsShownBy")) or _first_list_text(record.get("edmPreview"))
            if not image_url:
                continue
            title = _first_list_text(record.get("title")) or _first_text(record, ["title"])
            creator = _first_list_text(record.get("dcCreator")) or _first_list_text(record.get("edmAgentLabel"))
            provider = _first_list_text(record.get("dataProvider")) or _first_list_text(record.get("provider"))
            rights = _first_list_text(record.get("rights"))
            candidates.append(ArtworkCandidate(
                source="europeana",
                source_label="Europeana",
                artwork_id=f"europeana:{_clean_text(str(record.get('id') or record.get('guid') or title))}",
                title=title or "Untitled",
                artist=creator,
                date=_first_list_text(record.get("year")),
                medium=_first_list_text(record.get("type")),
                museum=provider or "Europeana",
                rights=rights or "Open reuse",
                image_url=image_url,
                page_url=_first_text(record, ["guid", "link"]),
                culture=_first_list_text(record.get("country")),
            ))
        return candidates

    def _fetch_harvard_candidates(self, term, limit, api_key, rng):
        page = rng.randint(1, 80)
        params = {
            "apikey": api_key,
            "classification": "Paintings",
            "hasimage": "1",
            "size": int(limit),
            "page": page,
            "fields": ",".join([
                "id",
                "objectnumber",
                "title",
                "dated",
                "people",
                "images",
                "primaryimageurl",
                "url",
                "classification",
                "medium",
                "culture",
                "creditline",
            ]),
        }
        if term and term not in {"painting", "paintings"}:
            params["title"] = term
        records = self._harvard_records(params)
        if not records and "title" in params:
            params.pop("title", None)
            records = self._harvard_records(params)
        rng.shuffle(records)

        candidates = []
        for record in records:
            if not isinstance(record, dict):
                continue
            image_url = self._harvard_image_url(record)
            if not image_url:
                continue
            people = record.get("people") if isinstance(record.get("people"), list) else []
            artist = ""
            if people:
                artist = _first_text(people[0], ["displayname", "name", "persondisplayname"])
            candidates.append(ArtworkCandidate(
                source="harvard",
                source_label="Harvard Art Museums",
                artwork_id=f"harvard:{record.get('id') or record.get('objectnumber')}",
                title=_first_text(record, ["title"]) or "Untitled",
                artist=artist,
                date=_first_text(record, ["dated"]),
                medium=_first_text(record, ["medium", "classification"]),
                museum="Harvard Art Museums",
                rights=_first_text(record, ["creditline"]) or "Harvard Art Museums",
                image_url=image_url,
                page_url=_first_text(record, ["url"]) or f"https://www.harvardartmuseums.org/collections/object/{record.get('id')}",
                culture=_first_text(record, ["culture"]),
            ))
        return candidates

    def _harvard_records(self, params):
        payload = self._get_json(HARVARD_OBJECT_URL, params, headers=REQUEST_HEADERS)
        records = payload.get("records") if isinstance(payload, dict) else []
        return records if isinstance(records, list) else []

    def _harvard_image_url(self, record):
        images = record.get("images") if isinstance(record.get("images"), list) else []
        for image in images:
            if not isinstance(image, dict):
                continue
            base = _first_text(image, ["baseimageurl", "iiifbaseuri"])
            if base:
                return f"{base.rstrip('/')}/full/1200,/0/default.jpg"
            url = _first_text(image, ["primaryimageurl", "imageurl"])
            if url:
                return url
        return _first_text(record, ["primaryimageurl"])

    def _get_json(self, url, params, headers=None):
        response = requests.get(
            url,
            params=params,
            headers=headers or REQUEST_HEADERS,
            timeout=(5, 12),
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"response from {url} was not JSON") from exc

    def _download_image_preview(self, image_url, dimensions, settings):
        if not image_url:
            return None
        max_bytes = _bounded_int(settings.get("maxImageBytes"), 12_000_000, 1_000_000, 25_000_000)
        timeout = _bounded_int(settings.get("imageTimeoutSeconds"), 14, 4, 40)
        cache_dir = self._cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(image_url).encode("utf-8")).hexdigest()[:16]
        tmp_path = cache_dir / f"download-{digest}.img"

        try:
            with requests.get(image_url, headers=IMAGE_HEADERS, timeout=(5, timeout), stream=True) as response:
                response.raise_for_status()
                downloaded = 0
                with tmp_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise RuntimeError(f"image exceeded {max_bytes} bytes")
                        handle.write(chunk)

            image = safe_open_image(
                tmp_path,
                limits=ImageLimits(max_bytes=max_bytes),
                draft_size=(dimensions[0] * 3, dimensions[1] * 3),
            ).convert("RGB")
            image.thumbnail((dimensions[0] * 3, dimensions[1] * 3), RESAMPLE)
            return image
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    def _save_artwork_render(self, selected, layout, dimensions, settings, state, rotation_key, cache_key, now):
        selected = [
            (candidate, ImageOps.exif_transpose(source_image).convert("RGB"))
            for candidate, source_image in selected
            if candidate and source_image
        ]
        if not selected:
            return self._fallback_image(dimensions, "Daily Art", "No museum scan available")

        candidates = [candidate for candidate, _image in selected]
        source_images = [image for _candidate, image in selected]
        if layout == "gallery":
            image = self._render_artwork_gallery(source_images, candidates, dimensions, settings)
        else:
            image = self._render_artwork(source_images[0], candidates[0], dimensions, settings)

        image_path = self._cache_image_path(rotation_key)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(image_path)

        artworks = [asdict(candidate) for candidate in candidates]
        payload = {
            "schema": CACHE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "rotation_key": rotation_key,
            "generated_at": now.isoformat(),
            "layout": layout,
            "artwork": artworks[0],
            "artworks": artworks,
            "image_path": str(image_path),
        }
        self._write_daily_cache(payload)
        for candidate in candidates:
            self._mark_seen(state, rotation_key, candidate)
        self._write_state(state)
        self._write_art_context(artworks, now, settings)

        logger.info(
            "Selected DailyArt %s: %s",
            layout,
            " || ".join(
                f"{candidate.source_label} | {candidate.title} | {candidate.image_url}"
                for candidate in candidates
            ),
        )
        return image

    def _render_artwork(self, image, artwork, dimensions, settings):
        width, height = dimensions
        image = ImageOps.exif_transpose(image).convert("RGB")
        canvas = self._art_backdrop(image, dimensions, settings)
        fit_mode = str(settings.get("fitMode") or "contain").strip().lower()

        if fit_mode == "cover":
            fitted = ImageOps.fit(image, dimensions, method=RESAMPLE)
            canvas.paste(fitted, (0, 0))
        else:
            fitted = ImageOps.contain(image, dimensions, method=RESAMPLE)
            x = (width - fitted.width) // 2
            y = (height - fitted.height) // 2
            canvas.paste(fitted, (x, y))

        if _enabled(settings.get("showCaption"), default=False):
            canvas = self._with_caption(canvas, artwork, settings)
        return canvas

    def _render_artwork_gallery(self, images, artworks, dimensions, settings):
        width, height = dimensions
        images = [ImageOps.exif_transpose(image).convert("RGB") for image in images if image]
        if not images:
            return self._fallback_image(dimensions, "Daily Art", "No usable artwork image")

        canvas = self._art_backdrop(images[0], dimensions, settings)
        visible_count = min(len(images), self._gallery_count(settings))
        column_width = width // visible_count
        fit_mode = str(settings.get("fitMode") or "contain").strip().lower()

        for index, image in enumerate(images[:visible_count]):
            x0 = index * column_width
            target_width = column_width if index < visible_count - 1 else width - x0
            if fit_mode == "cover":
                fitted = ImageOps.fit(image, (target_width, height), method=RESAMPLE)
            else:
                fitted = ImageOps.contain(image, (target_width, height), method=RESAMPLE)
            x = x0 + (target_width - fitted.width) // 2
            y = (height - fitted.height) // 2
            canvas.paste(fitted, (x, y))

        if _enabled(settings.get("showCaption"), default=False) and artworks:
            canvas = self._with_caption(canvas, artworks[0], settings)
        return canvas

    def _art_backdrop(self, image, dimensions, settings):
        color = str(settings.get("backgroundColor") or "warm").strip().lower()
        base = {
            "black": (15, 14, 13),
            "white": (248, 247, 244),
            "gray": (226, 224, 218),
        }.get(color, (241, 237, 229))
        background = Image.new("RGB", dimensions, base)
        if str(settings.get("backgroundStyle") or "blur").strip().lower() != "blur":
            return background
        try:
            backdrop = ImageOps.fit(image, dimensions, method=RESAMPLE)
            blur_radius = max(10, min(dimensions) // 22)
            backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            backdrop = ImageEnhance.Color(backdrop).enhance(0.42)
            backdrop = ImageEnhance.Contrast(backdrop).enhance(0.76)
            return Image.blend(backdrop, background, 0.64)
        except Exception as exc:
            logger.warning("DailyArt backdrop failed: %s", exc)
            return background

    def _with_caption(self, image, artwork, settings):
        width, height = image.size
        draw = ImageDraw.Draw(image)
        font_family = str(settings.get("fontFamily") or DEFAULT_FONT)
        title_font = _font(font_family, max(22, width // 27), "bold")
        meta_font = _font(font_family, max(16, width // 44), "normal")
        label_font = _font(font_family, max(13, width // 60), "bold")
        title_size = _font_size(title_font, max(22, width // 27))
        meta_size = _font_size(meta_font, max(16, width // 44))
        label_size = _font_size(label_font, max(13, width // 60))

        title = _clean_text(artwork.title or "Untitled")
        artist = _clean_text(artwork.artist or "Unknown artist")
        date = _clean_text(artwork.date or "")
        source = _clean_text(artwork.source_label or artwork.museum or "Museum")
        medium = _clean_text(artwork.medium or artwork.culture or "")
        meta_bits = [bit for bit in [artist, date, medium] if bit]
        meta = " | ".join(meta_bits)

        margin = max(16, width // 38)
        panel_w = min(width - margin * 2, max(width // 2, 560))
        max_title_width = panel_w - margin * 2
        title_lines = self._wrap_text(draw, title, title_font, max_title_width, 2)
        meta_lines = self._wrap_text(draw, meta, meta_font, max_title_width, 2) if meta else []
        panel_h = margin + len(title_lines) * (title_size + 4) + len(meta_lines) * (meta_size + 3) + label_size + 16
        panel_h = min(max(panel_h, 86), height // 3)
        x0 = margin
        y0 = height - panel_h - margin
        x1 = x0 + panel_w
        y1 = y0 + panel_h

        fill = (246, 244, 238)
        outline = (32, 31, 29)
        draw.rectangle((x0, y0, x1, y1), fill=fill, outline=outline, width=2)
        y = y0 + margin // 2 + 2
        draw.text((x0 + margin, y), source.upper()[:48], fill=(104, 76, 42), font=label_font)
        y += label_size + 7
        for line in title_lines:
            draw.text((x0 + margin, y), line, fill=(24, 24, 22), font=title_font)
            y += title_size + 4
        for line in meta_lines:
            draw.text((x0 + margin, y), line, fill=(62, 60, 56), font=meta_font)
            y += meta_size + 3
        return image

    def _fallback_image(self, dimensions, title, subtitle):
        width, height = dimensions
        image = Image.new("RGB", dimensions, (242, 239, 231))
        draw = ImageDraw.Draw(image)
        title_font = _font(DEFAULT_FONT, max(36, width // 14), "bold")
        body_font = _font(DEFAULT_FONT, max(18, width // 38), "normal")
        draw.rectangle((24, 24, width - 24, height - 24), outline=(50, 45, 38), width=3)
        self._draw_centered(draw, title, width // 2, height // 2 - 34, title_font, (26, 25, 23))
        self._draw_centered(draw, subtitle, width // 2, height // 2 + 28, body_font, (82, 75, 66))
        return image

    def _write_art_context(self, artwork, now, settings):
        if isinstance(artwork, list):
            artworks = [item for item in artwork if isinstance(item, dict) and item]
        elif isinstance(artwork, dict) and artwork:
            artworks = [artwork]
        else:
            artworks = []
        if not artworks:
            return
        first = artworks[0]
        title = _clean_text(first.get("title") or "Untitled")
        artist = _clean_text(first.get("artist") or "")
        source = _clean_text(first.get("source_label") or first.get("museum") or "Daily Art")
        if len(artworks) > 1:
            titles = "; ".join(_clean_text(item.get("title") or "Untitled") for item in artworks)
            summary = f"Daily Art gallery: {titles}"
            context_kind = "museum_artwork_gallery"
        else:
            summary = f"{source}: {title}"
            if artist:
                summary += f" by {artist}"
            context_kind = "museum_artwork"
        write_context(
            PLUGIN_ID,
            {
                "kind": context_kind,
                "source": source,
                "summary": summary[:220],
                "facts": [
                    {"label": "count", "value": str(len(artworks))},
                    {"label": "title", "value": title[:120]},
                    {"label": "artist", "value": artist[:120]},
                    {"label": "date", "value": _clean_text(first.get("date") or "")[:80]},
                    {"label": "museum", "value": _clean_text(first.get("museum") or source)[:120]},
                ],
                "items": [
                    {
                        "artwork_id": item.get("artwork_id"),
                        "source": item.get("source"),
                        "title": _clean_text(item.get("title") or "Untitled"),
                        "artist": _clean_text(item.get("artist") or ""),
                        "date": item.get("date"),
                        "image_url": item.get("image_url"),
                        "page_url": item.get("page_url"),
                        "rights": item.get("rights"),
                    }
                    for item in artworks
                ],
            },
            generated_at=now,
            ttl_seconds=self._context_ttl_seconds(settings),
        )

    def _context_ttl_seconds(self, settings):
        cadence = str(settings.get("rotationCadence") or "daily").strip().lower()
        if cadence == "every_refresh":
            return 45 * 60
        if cadence == "hourly":
            return 2 * 60 * 60
        return 26 * 60 * 60

    def _candidate_order(self, candidates, state, rotation_key):
        candidates = list(candidates)
        seed = self._stable_seed(rotation_key, "candidate-order")
        rng = random.Random(seed)
        rng.shuffle(candidates)
        bucket_key = self._state_bucket_key(rotation_key)
        bucket = state.setdefault("buckets", {}).setdefault(bucket_key, {})
        seen = {str(value) for value in bucket.get("seen_artwork_ids", [])}
        unseen = [candidate for candidate in candidates if candidate.artwork_id not in seen]
        seen_candidates = [candidate for candidate in candidates if candidate.artwork_id in seen]
        if not unseen and candidates:
            bucket["seen_artwork_ids"] = []
            unseen = candidates
            seen_candidates = []
        return unseen + seen_candidates

    def _mark_seen(self, state, rotation_key, candidate):
        bucket_key = self._state_bucket_key(rotation_key)
        bucket = state.setdefault("buckets", {}).setdefault(bucket_key, {})
        seen = [str(value) for value in bucket.get("seen_artwork_ids", []) if value]
        if candidate.artwork_id in seen:
            seen.remove(candidate.artwork_id)
        seen.append(candidate.artwork_id)
        bucket["seen_artwork_ids"] = seen[-220:]
        bucket["last_artwork_id"] = candidate.artwork_id
        bucket["updated_at"] = datetime.now(timezone.utc).isoformat()

    def _state_bucket_key(self, rotation_key):
        match = re.match(r"^\d{4}-\d{2}-\d{2}", str(rotation_key))
        return match.group(0) if match else str(rotation_key)

    def _enabled_sources(self, settings, device_config):
        mode = self._source_mode(settings)
        if mode == "open":
            source_names = ["met", "artic"]
        elif mode == "keyed":
            source_names = ["europeana", "harvard"]
        elif mode in {"met", "artic", "europeana", "harvard"}:
            source_names = [mode]
        else:
            raw = str(settings.get("sources") or "").strip()
            source_names = _source_tokens(raw) if raw else list(DEFAULT_SOURCES)

        enabled = []
        for source in source_names:
            if source == "europeana" and not self._env_value(device_config, EUROPEANA_ENV_KEYS, settings, ("europeanaApiKey",)):
                continue
            if source == "harvard" and not self._env_value(device_config, HARVARD_ENV_KEYS, settings, ("harvardApiKey",)):
                continue
            if source in DEFAULT_SOURCES and source not in enabled:
                enabled.append(source)
        return enabled

    def _source_mode(self, settings):
        return str(settings.get("sourceMode") or "all").strip().lower()

    def _query_terms(self, settings):
        raw = str(settings.get("queryTerms") or "").strip()
        if not raw:
            return list(DEFAULT_QUERY_TERMS)
        terms = [_clean_text(part) for part in re.split(r"[,;\n]+", raw) if _clean_text(part)]
        return terms or list(DEFAULT_QUERY_TERMS)

    def _env_value(self, device_config, names, settings=None, setting_keys=()):
        settings = settings or {}
        for key in setting_keys:
            value = str(settings.get(key) or "").strip()
            if value:
                return value
        for name in names:
            value = ""
            if device_config is not None and hasattr(device_config, "load_env_key"):
                try:
                    value = device_config.load_env_key(name) or ""
                except Exception as exc:
                    logger.warning("Could not read DailyArt env key %s: %s", name, exc)
            if not value:
                value = os.getenv(name, "")
            value = str(value or "").strip()
            if value:
                return value
        return ""

    def _read_daily_cache(self):
        return self._read_json(self._daily_cache_path(), {})

    def _write_daily_cache(self, payload):
        self._write_json(self._daily_cache_path(), payload)

    def _read_state(self):
        data = self._read_json(self._state_path(), {})
        if data.get("schema") != STATE_SCHEMA_VERSION:
            return {"schema": STATE_SCHEMA_VERSION, "buckets": {}}
        return data

    def _write_state(self, state):
        state["schema"] = STATE_SCHEMA_VERSION
        self._write_json(self._state_path(), state)

    def _read_json(self, path, default):
        try:
            path = Path(path)
            if not path.is_file():
                return default
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read DailyArt JSON %s: %s", path, exc)
            return default

    def _write_json(self, path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=True, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            tmp.replace(path)
        except PermissionError:
            logger.warning("Atomic DailyArt JSON write denied for %s; using direct write fallback.", path)
            path.write_text(text, encoding="utf-8")
            try:
                tmp.unlink()
            except Exception:
                pass

    def _cache_dir(self):
        return self.cache_dir(env_var="INKYPI_DAILY_ART_CACHE", leaf=".daily_art_cache", create=False)

    def _daily_cache_path(self):
        return self._cache_dir() / "daily.json"

    def _state_path(self):
        return self._cache_dir() / "state.json"

    def _cache_image_path(self, rotation_key):
        safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(rotation_key)).strip("-") or "daily"
        return self._cache_dir() / f"{safe_key}.png"

    def _prune_cache_files(self):
        cutoff = time.time() - timedelta(days=10).total_seconds()
        try:
            for path in self._cache_dir().glob("*.png"):
                if path.stat().st_mtime < cutoff:
                    path.unlink()
        except Exception as exc:
            logger.debug("DailyArt cache prune skipped: %s", exc)

    def _dedupe_candidates(self, candidates):
        deduped = {}
        for candidate in candidates:
            if not isinstance(candidate, ArtworkCandidate):
                continue
            key = candidate.artwork_id or hashlib.sha256(candidate.image_url.encode("utf-8")).hexdigest()
            if key and key not in deduped:
                deduped[key] = candidate
        return list(deduped.values())

    def _stable_seed(self, *parts):
        digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
        return int(digest[:16], 16)

    def _wrap_text(self, draw, text, font, max_width, max_lines=2):
        words = str(text or "").split()
        if not words:
            return []
        lines = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if _text_width(draw, candidate, font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if lines and _text_width(draw, lines[-1], font) > max_width:
            lines[-1] = self._ellipsize(draw, lines[-1], font, max_width)
        return lines

    def _ellipsize(self, draw, text, font, max_width):
        text = str(text or "")
        if _text_width(draw, text, font) <= max_width:
            return text
        suffix = "..."
        while text and _text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return (text + suffix) if text else suffix

    def _draw_centered(self, draw, text, x, y, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((x - (bbox[2] - bbox[0]) / 2, y - (bbox[3] - bbox[1]) / 2), text, font=font, fill=fill)


def _font(family, size, weight="normal"):
    font = get_font(family, int(size), weight)
    if font:
        return font
    try:
        return ImageFont.truetype("DejaVuSans.ttf", int(size))
    except Exception:
        return ImageFont.load_default()


def _font_size(font, fallback):
    return int(getattr(font, "size", fallback) or fallback)


def _bounded_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return max(int(minimum), min(int(maximum), number))


def _enabled(value, default=False):
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "show"}


def _clean_text(value):
    if isinstance(value, list):
        value = " ".join(str(item) for item in value if item)
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text


def _first_text(data, keys):
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            value = _first_list_text(value)
        value = _clean_text(value)
        if value:
            return value
    return ""


def _first_list_text(value):
    if isinstance(value, list):
        for item in value:
            text = _clean_text(item)
            if text:
                return text
        return ""
    return _clean_text(value)


def _source_tokens(value):
    aliases = {
        "aic": "artic",
        "chicago": "artic",
        "art-institute": "artic",
        "artinstitute": "artic",
        "themet": "met",
        "metmuseum": "met",
        "metropolitan": "met",
        "europeana": "europeana",
        "harvard": "harvard",
    }
    tokens = []
    for raw in re.split(r"[,;\s]+", str(value or "").strip().lower()):
        if not raw:
            continue
        token = aliases.get(raw, raw)
        if token in DEFAULT_SOURCES and token not in tokens:
            tokens.append(token)
    return tokens


def _text_width(draw, text, font):
    return text_width(draw, str(text), font)
