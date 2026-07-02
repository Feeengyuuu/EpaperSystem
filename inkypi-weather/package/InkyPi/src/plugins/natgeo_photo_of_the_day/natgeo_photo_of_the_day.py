from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import random
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

PAGE_URL = "https://www.nationalgeographic.com/photo-of-the-day"
DISCOVERY_PAGE_URL = "https://www.discovery.com/exploration/all-exploration-photos-pictures"
DISCOVERY_GALLERY_PAGES = [
    "https://www.discovery.com/exploration/all-exploration-photos-pictures",
    "https://www.discovery.com/shows/deadliest-catch/photos--a-new-era-for-deadliest-catch-captains-pictures",
    "https://www.discovery.com/shows/deadliest-catch/photos--deadliest-catch-season-17-pictures",
    "https://www.discovery.com/shows/expedition-unknown/articles/expedition-unknown--egypt-live-photo-gallery",
]
PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; InkyPi NatGeoPhotoOfTheDay/1.0; "
        "+https://github.com/fatihak/InkyPi/)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
IMAGE_HEADERS = {
    "User-Agent": PAGE_HEADERS["User-Agent"],
    "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
    "Referer": PAGE_URL,
}
DISCOVERY_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; InkyPi DiscoveryGalleryPhotos/1.0; "
        "+https://github.com/fatihak/InkyPi/)"
    ),
    "Accept": "text/plain,text/markdown,*/*;q=0.8",
}
DISCOVERY_IMAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
    "Referer": "https://www.discovery.com/",
}
CACHE_TTL = timedelta(hours=12)
SOURCE_IDS = ["natgeo", "discovery"]


class _NatGeoParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.meta_images = []
        self.images = []
        self._in_title = False
        self._title_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
            self._title_text = []
            return

        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
                self._add(self.meta_images, attrs.get("content"), key)
            return

        if tag == "img":
            label = " ".join([attrs.get("alt") or "", attrs.get("class") or "", attrs.get("id") or ""])
            for url in self._urls_from_image_attrs(attrs):
                self._add(self.images, url, label)
            return

        if tag == "source":
            for attr in ["srcset", "data-srcset"]:
                if attrs.get(attr):
                    self._add(self.images, self._best_srcset_url(attrs[attr]), attrs.get("media") or attr)

    def handle_data(self, data):
        if self._in_title:
            self._title_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "title" and self._in_title:
            self.title = re.sub(r"\s+", " ", " ".join(self._title_text)).strip()
            self._in_title = False

    def _add(self, bucket, url, label):
        if not url:
            return
        bucket.append({"url": urljoin(self.base_url, html.unescape(url)), "label": label or ""})

    def _urls_from_image_attrs(self, attrs):
        urls = []
        for attr in ["src", "data-src", "data-original"]:
            if attrs.get(attr):
                urls.append(attrs[attr])
        for attr in ["srcset", "data-srcset"]:
            if attrs.get(attr):
                urls.append(self._best_srcset_url(attrs[attr]))
        return [url for url in urls if url]

    def _best_srcset_url(self, srcset):
        best_url = ""
        best_score = -1
        for part in srcset.split(","):
            bits = part.strip().split()
            if not bits:
                continue
            score = 0
            if len(bits) > 1:
                match = re.search(r"(\d+)(?:w|x)?$", bits[-1])
                if match:
                    score = int(match.group(1))
            if score >= best_score:
                best_score = score
                best_url = bits[0]
        return best_url


class NatGeoPhotoOfTheDay(BasePlugin):
    SOURCE_QUEUE_KEY = "daily_photo_source_queue"
    SOURCE_POOL_KEY = "daily_photo_source_pool"
    SOURCE_LAST_KEY = "daily_photo_source_last"
    DISCOVERY_QUEUE_KEY = "discovery_image_queue"
    DISCOVERY_POOL_KEY = "discovery_image_pool"
    DISCOVERY_LAST_KEY = "discovery_image_last"

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        logger.info("=== NatGeo + Discovery Daily Photos: Starting image generation ===")
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)

        selected_source = self._select_no_repeat_value(
            settings,
            SOURCE_IDS,
            self.SOURCE_QUEUE_KEY,
            self.SOURCE_POOL_KEY,
            self.SOURCE_LAST_KEY,
        )
        source_order = [selected_source] + [source for source in SOURCE_IDS if source != selected_source]

        errors = []
        photo = None
        for source in source_order:
            try:
                photo = self._photo_for_source(source, dimensions, settings)
                break
            except Exception as exc:
                logger.warning("Could not fetch %s daily photo: %s", source, exc)
                errors.append(f"{source}: {exc}")

        if not photo:
            raise RuntimeError(f"No daily photo source succeeded. {'; '.join(errors)}")

        image = self._compose(photo, dimensions, settings)
        self._write_photo_context(photo)
        logger.info(
            "Selected %s daily photo: %s | %s",
            photo.get("source"),
            photo.get("title") or "Untitled",
            photo.get("image_url"),
        )
        logger.info("=== NatGeo + Discovery Daily Photos: Image generation complete ===")
        return image

    def _write_photo_context(self, photo):
        source_id = str(photo.get("source") or "daily_photo").strip()
        source_name = "Discovery" if source_id == "discovery" else "National Geographic"
        title = str(photo.get("title") or f"{source_name} daily photo").strip()
        write_context(
            "natgeo_photo_of_the_day",
            {
                "kind": "daily_photo",
                "source": source_name,
                "summary": f"{source_name} daily photo: {title}"[:180],
                "facts": [
                    {"label": "source", "value": source_name},
                    {"label": "title", "value": title[:100]},
                ],
                "items": [{
                    "title": title[:120],
                    "source": source_name,
                    "page_url": photo.get("page_url"),
                    "image_url": photo.get("image_url"),
                }],
            },
            generated_at=datetime.now(timezone.utc),
            ttl_seconds=int(CACHE_TTL.total_seconds()),
        )

    def _display_dimensions(self, device_config):
        return self.get_dimensions(device_config)

    def _photo_for_source(self, source, dimensions, settings):
        if source == "discovery":
            return self._fetch_discovery_photo(dimensions, settings)

        photo = self._read_cache("natgeo", dimensions)
        if not photo:
            photo = self._fetch_natgeo_photo(dimensions)
            self._write_cache("natgeo", dimensions, photo)
        return photo

    def _fetch_natgeo_photo(self, dimensions):
        response = get_http_session().get(PAGE_URL, headers=PAGE_HEADERS)
        response.raise_for_status()
        if not response.encoding:
            response.encoding = "utf-8"

        parser = _NatGeoParser(PAGE_URL)
        parser.feed(response.text or "")
        candidates = self._rank_candidates(parser, response.text or "")
        errors = []
        for candidate in candidates[:20]:
            try:
                image = self._download_image(candidate["url"], dimensions)
                if self._looks_usable(image):
                    return {
                        "source": "natgeo",
                        "image": image,
                        "image_url": candidate["url"],
                        "page_url": PAGE_URL,
                        "title": parser.title,
                    }
            except Exception as exc:
                errors.append(f"{candidate['url']}: {exc}")

        detail = "; ".join(errors[-3:])
        raise RuntimeError(f"No usable NatGeo Photo of the Day image found. {detail}")

    def _fetch_discovery_photo(self, dimensions, settings):
        candidates = self._discovery_gallery_candidates()
        if not candidates:
            raise RuntimeError("No Discovery gallery images found.")

        selected_id = self._select_no_repeat_value(
            settings,
            candidates[:60],
            self.DISCOVERY_QUEUE_KEY,
            self.DISCOVERY_POOL_KEY,
            self.DISCOVERY_LAST_KEY,
        )
        ordered_candidates = [selected_id] + [url for url in candidates[:60] if url != selected_id]
        errors = []

        for candidate_url in ordered_candidates[:16]:
            for image_url in self._discovery_image_variants(candidate_url):
                try:
                    image = self._download_image(image_url, dimensions, headers=DISCOVERY_IMAGE_HEADERS)
                    if self._looks_usable(image):
                        return {
                            "source": "discovery",
                            "image": image,
                            "image_url": image_url,
                            "page_url": DISCOVERY_PAGE_URL,
                            "title": "Discovery photo gallery",
                        }
                except Exception as exc:
                    errors.append(f"{image_url}: {exc}")

        detail = "; ".join(errors[-3:])
        raise RuntimeError(f"No usable Discovery image found. {detail}")

    def _discovery_gallery_candidates(self):
        candidates = []
        for page_url in DISCOVERY_GALLERY_PAGES:
            reader_url = f"https://r.jina.ai/http://{page_url}"
            try:
                response = get_http_session().get(reader_url, timeout=35, headers=DISCOVERY_PAGE_HEADERS)
                response.raise_for_status()
                if not response.encoding:
                    response.encoding = "utf-8"
                candidates.extend(self._discovery_image_urls(response.text or ""))
            except Exception as exc:
                logger.warning("Could not read Discovery gallery page %s: %s", page_url, exc)

        deduped = []
        for url in candidates:
            if url not in deduped:
                deduped.append(url)
        random.shuffle(deduped)
        return deduped

    def _discovery_image_urls(self, markdown_text):
        urls = []
        for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", markdown_text):
            urls.append(self._clean_url(match.group(1)))
        for match in re.finditer(r"https?://[^\"'<>\s)]+", markdown_text):
            urls.append(self._clean_url(match.group(0)))

        return [url for url in urls if self._usable_discovery_url(url)]

    def _usable_discovery_url(self, url):
        lower = (url or "").lower()
        if not lower.startswith(("http://", "https://")):
            return False
        if "sndimg.com/content/dam/images" not in lower:
            return False
        if not any(token in lower for token in [".jpg", ".jpeg", ".png", ".webp"]):
            return False
        reject = [
            "disco-plus",
            "logo",
            "breadcrumb",
            "ytimg.com",
            ".rend.hgtvcom.196.196.",
            ".rend.hgtvcom.161.161.",
            "cover_art",
            "social_3000x3000",
            "_social_",
            "_la_",
            "bleed",
            "nott",
        ]
        return not any(token in lower for token in reject)

    def _discovery_image_variants(self, url):
        variants = []
        if re.search(r"\.rend\.hgtvcom\.\d+\.\d+\.suffix/", url):
            for size in ["1280.720", "616.347", "406.406"]:
                variants.append(re.sub(r"\.rend\.hgtvcom\.\d+\.\d+\.suffix/", f".rend.hgtvcom.{size}.suffix/", url))
        variants.append(url)

        deduped = []
        for item in variants:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _rank_candidates(self, parser, html_text):
        raw = []
        for source, items in [("meta", parser.meta_images), ("img", parser.images)]:
            for item in items:
                raw.append({"url": self._clean_url(item["url"]), "label": item["label"], "source": source})

        for url in re.findall(r"https?:\\?/\\?/[^\"'<>\\s]+", html_text):
            cleaned = self._clean_url(url)
            if "natgeofe.com" in cleaned or "nationalgeographic.com" in cleaned:
                raw.append({"url": cleaned, "label": "raw-html", "source": "raw"})

        deduped = {}
        for candidate in raw:
            url = candidate["url"]
            if not self._usable_url(url):
                continue
            candidate["score"] = self._score_candidate(candidate)
            existing = deduped.get(url)
            if not existing or candidate["score"] > existing["score"]:
                deduped[url] = candidate
        return sorted(deduped.values(), key=lambda item: item["score"], reverse=True)

    def _clean_url(self, url):
        url = html.unescape(url or "")
        url = url.replace("\\/", "/").replace("\\u002F", "/").replace("\\u0026", "&")
        return url.strip().strip('"').strip("'")

    def _usable_url(self, url):
        lower = (url or "").lower()
        if not lower.startswith(("http://", "https://")):
            return False
        reject = ["logo", "favicon", "sprite", "avatar", "placeholder", "transparent", ".svg", ".gif"]
        if any(token in lower for token in reject):
            return False
        return "natgeofe.com" in lower or "nationalgeographic.com" in lower

    def _score_candidate(self, candidate):
        haystack = f"{candidate['url']} {candidate.get('label', '')} {candidate.get('source', '')}".lower()
        score = 0
        if "i.natgeofe.com" in haystack:
            score += 120
        if "photo-of-the-day" in haystack:
            score += 80
        if candidate["source"] == "meta":
            score += 60
        if candidate["source"] == "img":
            score += 35
        for token in ["image", "photo", "potd", "nationalgeographic"]:
            if token in haystack:
                score += 15
        for token in ["logo", "icon", "newsletter", "disney"]:
            if token in haystack:
                score -= 80
        return score

    def _download_image(self, url, dimensions, headers=IMAGE_HEADERS):
        response = get_http_session().get(url, timeout=35, headers=headers)
        response.raise_for_status()
        with Image.open(BytesIO(response.content)) as image:
            loaded = ImageOps.exif_transpose(image).convert("RGB")
        return loaded

    def _looks_usable(self, image):
        width, height = image.size
        return width >= 300 and height >= 200 and width * height >= 160_000

    def _compose(self, photo, dimensions, settings):
        image = photo["image"]
        image = ImageOps.exif_transpose(image).convert("RGB")
        if (settings.get("fitMode") or "contain").lower() == "cover":
            canvas = ImageOps.fit(image, dimensions, method=Image.LANCZOS)
        else:
            canvas = self._background(image, dimensions, settings)
            fitted = ImageOps.contain(image, dimensions, method=Image.LANCZOS)
            x = (dimensions[0] - fitted.width) // 2
            y = (dimensions[1] - fitted.height) // 2
            canvas.paste(fitted, (x, y))

        if str(settings.get("showLogo") or "true").lower() in {"1", "true", "on", "yes"}:
            self._paste_logo(canvas, photo.get("source") or "natgeo")
        return canvas

    def _background(self, image, dimensions, settings):
        if (settings.get("backgroundStyle") or "blur").lower() == "plain":
            return Image.new("RGB", dimensions, (0, 0, 0))
        background = ImageOps.fit(image, dimensions, method=Image.LANCZOS)
        background = background.filter(ImageFilter.GaussianBlur(radius=max(6, min(dimensions) // 60)))
        background = ImageEnhance.Color(background).enhance(0.45)
        background = ImageEnhance.Contrast(background).enhance(0.88)
        return background

    def _paste_logo(self, canvas, source):
        logo_config = {
            "natgeo": {
                "file": "natgeo_logo.png",
                "box": (218, 64),
                "crop": False,
                "position": "bottom-left",
                "halo": False,
            },
            "discovery": {
                "file": "discovery_logo.png",
                "box": (190, 45),
                "crop": True,
                "position": "top-left",
                "halo": True,
            },
        }.get(source)
        if not logo_config:
            return

        logo_path = Path(self.get_plugin_dir(logo_config["file"]))
        if not logo_path.is_file():
            logger.warning("%s logo asset missing: %s", source, logo_path)
            return
        try:
            with Image.open(logo_path) as logo:
                logo = ImageOps.exif_transpose(logo).convert("RGBA")
            if logo_config.get("crop"):
                bbox = logo.getbbox()
                if bbox:
                    logo = logo.crop(bbox)
            logo = ImageOps.contain(logo, logo_config["box"], method=Image.LANCZOS)
            if logo_config["position"] == "top-left":
                x = 22
                y = 22
            else:
                x = 22
                y = canvas.height - 22 - logo.height
            if logo_config.get("halo"):
                alpha = logo.getchannel("A").filter(ImageFilter.GaussianBlur(radius=1.3))
                halo = Image.new("RGBA", logo.size, (255, 255, 255, 115))
                halo.putalpha(alpha.point(lambda value: min(170, int(value * 0.68))))
                canvas.paste(halo, (x, y), halo)
            canvas.paste(logo, (x, y), logo)
        except Exception as exc:
            logger.warning("Could not paste %s logo: %s", source, exc)

    def _select_no_repeat_value(self, settings, pool, queue_key, pool_key, last_key):
        pool = [str(value) for value in pool if value]
        if not pool:
            raise RuntimeError("No values available for random selection.")

        previous_pool = self._normalize_setting_list(settings.get(pool_key))
        queue = [value for value in self._normalize_setting_list(settings.get(queue_key)) if value in pool]
        last_selected = settings.get(last_key)

        if previous_pool != pool:
            queue = []

        if not queue:
            queue = list(pool)
            random.shuffle(queue)
            self._avoid_immediate_repeat(queue, last_selected)

        selected = queue.pop(0)
        settings[pool_key] = pool
        settings[queue_key] = queue
        settings[last_key] = selected
        return selected

    def _normalize_setting_list(self, value):
        if not value:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed if item]
            except Exception:
                pass
            return [item.strip() for item in value.split(",") if item.strip()]
        return []

    def _avoid_immediate_repeat(self, queue, last_selected):
        if not last_selected or len(queue) < 2 or queue[0] != last_selected:
            return
        for index, value in enumerate(queue[1:], start=1):
            if value != last_selected:
                queue[0], queue[index] = queue[index], queue[0]
                return

    def _read_cache(self, source, dimensions):
        meta_path = self._cache_meta_path(source, dimensions)
        try:
            if not meta_path.is_file():
                return None
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(meta.get("fetched_at"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched_at > CACHE_TTL:
                return None
            image_path = Path(meta.get("image_path") or "")
            if not image_path.is_file():
                return None
            with Image.open(image_path) as image:
                loaded = ImageOps.exif_transpose(image).convert("RGB")
            return {
                "source": meta.get("source") or source,
                "image": loaded,
                "image_url": meta.get("image_url"),
                "page_url": meta.get("page_url"),
                "title": meta.get("title"),
            }
        except Exception as exc:
            logger.warning("Could not read %s daily photo cache: %s", source, exc)
            return None

    def _write_cache(self, source, dimensions, photo):
        meta_path = self._cache_meta_path(source, dimensions)
        image_path = meta_path.with_suffix(".jpg")
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            photo["image"].save(image_path, quality=92)
            meta = {
                "source": photo.get("source") or source,
                "image_url": photo.get("image_url"),
                "page_url": photo.get("page_url"),
                "title": photo.get("title"),
                "image_path": str(image_path),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=True, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not write %s daily photo cache: %s", source, exc)

    def _cache_meta_path(self, source, dimensions):
        key = hashlib.sha256(f"{source}|{dimensions[0]}x{dimensions[1]}".encode("utf-8")).hexdigest()[:20]
        return self._cache_dir() / f"{source}_{key}.json"

    def _cache_dir(self):
        return self.cache_dir(env_var="INKYPI_NATGEO_POTD_CACHE", leaf=".natgeo_potd_cache", create=False)
