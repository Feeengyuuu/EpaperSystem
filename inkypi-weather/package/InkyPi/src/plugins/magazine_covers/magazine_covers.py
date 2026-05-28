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

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

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

DEFAULT_SOURCES = """TIME|https://magazineshop.us/collections/time
Rolling Stone|https://magazineshop.us/collections/rolling-stone
Billboard|https://magazineshop.us/collections/billboard
Vanity Fair|https://www.vanityfair.com/magazine
The Atlantic|https://www.theatlantic.com/magazine/
WIRED Japan|https://wired.jp/magazine/
Variety|https://magazineshop.us/collections/variety
The Hollywood Reporter|https://magazineshop.us/collections/the-hollywood-reporter
Us Weekly|https://magazineshop.us/collections/us-weekly
Sports Illustrated|https://magazineshop.us/collections/sports-illustrated
Robb Report|https://magazineshop.us/collections/robb-report
Reader's Digest|https://magazineshop.us/collections/readers-digest
Taste of Home|https://magazineshop.us/collections/taste-of-home
TV Guide|https://magazineshop.us/collections/tv-guide-tv"""

ROTATION_STATE_VERSION = "magazine-covers-rotation-v1"
IMAGE_CACHE_TTL = timedelta(hours=18)
MAX_PI_SAFE_SOURCE_PIXELS = 900_000
DOWNLOAD_CHUNK_SIZE = 8192
RESAMPLING_FILTER = getattr(Image, "Resampling", Image).BICUBIC


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
        sources = self._parse_sources(settings.get("sources") or DEFAULT_SOURCES)
        if not sources:
            raise RuntimeError("No magazine cover sources configured.")

        rotation_mode = (settings.get("rotationMode") or "random").lower()
        if rotation_mode == "single":
            ordered_sources = sources[:1]
        elif rotation_mode in {"rotate", "sequential"}:
            ordered_sources = self._rotation_order(sources)
        else:
            ordered_sources = self._random_order(sources)

        errors = []
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
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        return dimensions

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

    def _load_cover(self, source, dimensions):
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

            image = self.image_loader.from_file(str(decode_path), dimensions, resize=True)
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
        fit_mode = (settings.get("fitMode") or "rotate_full").lower()
        image = ImageOps.exif_transpose(image).convert("RGB")

        if fit_mode == "cover":
            fitted = self._fit_cover_crop(image, dimensions)
            return self._with_source_label(fitted, source, settings)

        should_rotate = fit_mode in {"rotate_full", "rotate", "auto"} and image.height > image.width
        if should_rotate:
            image = image.rotate(90, expand=True)

        background = self._background(dimensions, settings, image)
        fitted = ImageOps.contain(image, dimensions, method=Image.LANCZOS)
        x = (dimensions[0] - fitted.width) // 2
        y = (dimensions[1] - fitted.height) // 2
        background.paste(fitted, (x, y))
        return self._with_source_label(background, source, settings)

    def _fit_cover_crop(self, image, dimensions):
        target_width, target_height = dimensions
        target_ratio = target_width / target_height
        image_ratio = image.width / image.height

        if image_ratio > target_ratio:
            crop_width = max(1, min(image.width, int(round(image.height * target_ratio))))
            x = max(0, (image.width - crop_width) // 2)
            crop_box = (x, 0, x + crop_width, image.height)
        else:
            crop_height = max(1, min(image.height, int(round(image.width / target_ratio))))
            y = self._masthead_crop_offset(image.height, crop_height)
            crop_box = (0, y, image.width, y + crop_height)

        cropped = image.crop(crop_box)
        return cropped.resize((target_width, target_height), Image.LANCZOS)

    def _masthead_crop_offset(self, image_height, crop_height):
        max_offset = max(0, image_height - crop_height)
        if max_offset == 0:
            return 0
        return 0

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

    def _background(self, dimensions, settings, image):
        color = (settings.get("backgroundColor") or "white").lower()
        base_color = (0, 0, 0) if color == "black" else (255, 255, 255)

        style = (settings.get("backgroundStyle") or "blur").lower()
        if style in {"plain", "solid"}:
            return Image.new("RGB", dimensions, base_color)

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
            return Image.new("RGB", dimensions, base_color)

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
            loaded = self.image_loader.from_file(str(image_path), dimensions, resize=True)
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
        state = self._read_state()
        source_id = self._source_id(source)
        state["last_source"] = source.get("name")
        state["last_source_id"] = source_id
        state["last_page_url"] = cover.get("page_url")
        state["last_image_url"] = cover.get("image_url")
        state["last_title"] = cover.get("title")
        state["last_displayed_at"] = datetime.now(timezone.utc).isoformat()
        if isinstance(state.get("random_queue"), list):
            state["random_queue"] = [
                queued_id for queued_id in state["random_queue"] if queued_id != source_id
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
            f"{source['name']}|{source['url']}|{dimensions[0]}x{dimensions[1]}".encode("utf-8")
        ).hexdigest()[:20]
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", source["name"]).strip("_") or "source"
        return self._cache_dir() / "covers" / f"{safe_name}_{key}.json"

    def _cache_dir(self):
        path = os.getenv("INKYPI_MAGAZINE_COVERS_CACHE")
        if path:
            return Path(path)
        return Path(self.get_plugin_dir(".magazine_covers_cache"))

    def _read_state(self):
        path = self._state_path()
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read Magazine Covers state %s: %s", path, exc)
        return {}

    def _write_state(self, state):
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

    def _pool_key(self, sources):
        raw = "|".join([ROTATION_STATE_VERSION] + [self._source_id(source) for source in sources])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _source_id(self, source):
        return f"{source['name']}|{source['url']}"

    def _safe_int(self, value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


def _clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()
