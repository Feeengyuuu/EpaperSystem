from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

PLUGIN_ID = "pixiv_r18_ranking"
STATE_VERSION = "pixiv-r18-ranking-v1"
DEFAULT_RANKING_MODE = "day_r18"
DEFAULT_POOL_SIZE = 20
MAX_POOL_SIZE = 50
DEFAULT_FIT_MODE = "auto_layout"
MAX_STRIP_CELLS = 3
# Cap on ranking pages walked while filling the pool (~50 entries/page).
MAX_RANKING_PAGES = 5
JST = timezone(timedelta(hours=9))
DOWNLOAD_CHUNK_SIZE = 8192
MAX_PI_SAFE_SOURCE_PIXELS = 900_000
RESAMPLING_FILTER = getattr(Image, "Resampling", Image).BICUBIC

# Public ranking endpoint. ``format=json`` needs no OAuth/API key; only the R-18
# modes require a logged-in session (a ``PHPSESSID`` cookie). When no cookie is
# configured we fall back to the matching safe-for-work ranking so the plugin
# still produces an image.
RANKING_URL = "https://www.pixiv.net/ranking.php"
PIXIV_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Maps the saved setting value to (R-18 ranking.php mode, SFW fallback mode).
RANKING_MODE_MAP = {
    "day_r18": ("daily_r18", "daily"),
    "daily_r18": ("daily_r18", "daily"),
    "day_male_r18": ("male_r18", "male"),
    "male_r18": ("male_r18", "male"),
    "day_female_r18": ("female_r18", "female"),
    "female_r18": ("female_r18", "female"),
    "week_r18": ("weekly_r18", "weekly"),
    "weekly_r18": ("weekly_r18", "weekly"),
}

PIXIV_RANKING_HEADERS = {
    "Referer": "https://www.pixiv.net/",
    "User-Agent": PIXIV_BROWSER_UA,
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}

PIXIV_IMAGE_HEADERS = {
    "Referer": "https://www.pixiv.net/",
    "User-Agent": PIXIV_BROWSER_UA,
}

RISK_TAGS = {
    "r-18g",
    "r18g",
    "guro",
    "gore",
    "grotesque",
    "loli",
    "lolicon",
    "shota",
    "shotacon",
    "\u30ed\u30ea",
    "\u30ed\u30ea\u30b3\u30f3",
    "\u30b7\u30e7\u30bf",
    "\u30b7\u30e7\u30bf\u30b3\u30f3",
    "\u5e7c\u5973",
    "\u5e7c\u5150",
    "\u672a\u6210\u5e74",
    "\u5c11\u5973",
    "\u5c11\u5e74",
}


class PixivR18Ranking(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        try:
            pool = self._daily_pool(settings, device_config, dimensions)
            if not pool:
                logger.warning("Pixiv R-18 ranking daily pool is empty after filtering.")
                return self._fallback_image(dimensions, "Pixiv R-18", "No filtered image available")

            group = self._select_display_group(pool, settings)
            if not group:
                return self._fallback_image(dimensions, "Pixiv R-18", "No cached image available")

            images = []
            for item in group:
                image = self._load_cached_item_image(item, dimensions)
                if image:
                    images.append(image)
            if not images:
                logger.warning("Cached Pixiv ranking image missing for %s", group[0].get("illust_id"))
                return self._fallback_image(dimensions, "Pixiv R-18", "Cached image missing")

            logger.info(
                "Selected Pixiv R-18 ranking. | count: %s | illust_ids: %s",
                len(images),
                [item.get("illust_id") for item in group],
            )
            if len(images) >= 2:
                # Two or three portraits side by side.
                return self._compose_strip(images, dimensions, settings)
            return self._fit_image(images[0], dimensions, settings, group[0])
        except Exception as exc:
            logger.exception("Pixiv R-18 ranking plugin failed: %s", exc)
            return self._fallback_image(dimensions, "Pixiv R-18", "Ranking unavailable")

    def _daily_pool(self, settings, device_config, dimensions):
        if self._daily_pool_needs_refresh(settings):
            self._refresh_daily_pool(settings, device_config, dimensions)

        pool = self._read_daily_pool()
        if not pool:
            return []

        valid_paths = []
        for item in pool:
            image_path = Path(item.get("image_path") or "")
            if image_path.is_file():
                valid_paths.append(item)
        return valid_paths

    def _daily_pool_needs_refresh(self, settings):
        if not _setting_enabled(settings.get("dailyPoolMode", "true")):
            return True

        state = self._read_state()
        expected = {
            "state_version": STATE_VERSION,
            "day_key": self._day_key(),
            "ranking_mode": self._ranking_mode(settings),
            "pool_size": self._pool_size(settings),
        }
        for key, value in expected.items():
            if state.get(key) != value:
                return True

        return self._read_daily_pool_payload() is None

    def _refresh_daily_pool(self, settings, device_config, dimensions):
        ranking_mode = self._ranking_mode(settings)
        pool_size = self._pool_size(settings)
        cookie = self._load_session_cookie(device_config)

        usable = []
        errors = []
        seen = set()
        # Resolve the effective mode (R-18 with cookie, else SFW) and grab page 1.
        mode, eff_cookie, page_items = self._resolve_ranking(ranking_mode, cookie)
        page = 1
        while page_items and len(usable) < pool_size:
            for illust in page_items:
                if len(usable) >= pool_size:
                    break
                illust_id = str(self._illust_id(illust) or "")
                if not illust_id or illust_id in seen:
                    continue
                seen.add(illust_id)
                if not self._is_safe_ranking_item(illust):
                    continue
                try:
                    item = self._ranking_item_metadata(illust, _get_value(illust, "rank", len(usable) + 1))
                    image_path = self._download_ranking_item_image(item, dimensions)
                    if image_path:
                        item["image_path"] = str(image_path)
                        usable.append(item)
                except Exception as exc:
                    errors.append(f"{illust_id}: {exc}")
                    logger.warning("Could not cache Pixiv ranking item %s: %s", illust_id, exc)
            # Keep walking the ranking until the pool is full or pages run out.
            if len(usable) >= pool_size or page >= MAX_RANKING_PAGES:
                break
            page += 1
            try:
                page_items = self._fetch_ranking_page(mode, eff_cookie, page)
            except Exception as exc:
                errors.append(f"page {page} ({mode}): {exc}")
                logger.warning("Pixiv ranking page %s fetch failed: %s", page, exc)
                break

        state = self._write_current_day_pool(usable, settings)
        state["last_refresh_errors"] = errors[-8:]
        self._write_state(state)
        if len(usable) < pool_size:
            logger.warning(
                "Pixiv ranking pool under target. | mode: %s | got: %s | target: %s | pages: %s",
                mode, len(usable), pool_size, page,
            )
        else:
            logger.info(
                "Pixiv R-18 daily ranking pool refreshed. | mode: %s | count: %s | pages: %s",
                mode, len(usable), page,
            )
        return usable

    def _resolve_ranking(self, ranking_mode, cookie):
        """Resolve the effective ranking and fetch page 1.

        Returns (effective_mode, effective_cookie, first_page_items). R-18 modes
        need a login cookie; when it is missing or rejected (the page comes back
        as the HTML landing page, not JSON), fall back to the SFW ranking.
        """
        r18_mode, sfw_mode = self._mode_pair(ranking_mode)
        if r18_mode:
            if cookie:
                try:
                    return r18_mode, cookie, self._fetch_ranking_page(r18_mode, cookie, 1)
                except Exception as exc:
                    logger.warning(
                        "Pixiv R-18 ranking '%s' fetch failed (cookie expired or invalid?); "
                        "falling back to SFW '%s': %s",
                        r18_mode, sfw_mode, exc,
                    )
            else:
                logger.warning(
                    "PIXIV_PHPSESSID is not configured; R-18 ranking requires a login cookie. "
                    "Falling back to SFW ranking '%s'.",
                    sfw_mode,
                )
            return sfw_mode, None, self._fetch_ranking_page(sfw_mode, None, 1)
        eff_cookie = cookie or None
        return sfw_mode, eff_cookie, self._fetch_ranking_page(sfw_mode, eff_cookie, 1)

    def _fetch_ranking(self, ranking_mode, cookie):
        """First ranking page for the mode (R-18 with cookie, else SFW fallback)."""
        return self._resolve_ranking(ranking_mode, cookie)[2]

    def _mode_pair(self, ranking_mode):
        """Returns (r18_mode, sfw_fallback_mode); r18_mode is None for plain modes."""
        return RANKING_MODE_MAP.get(ranking_mode, (None, ranking_mode))

    def _fetch_ranking_page(self, mode, cookie, page=1):
        params = {"mode": mode, "content": "illust", "format": "json", "p": int(page)}
        cookies = {"PHPSESSID": cookie} if cookie else None
        response = get_http_session().get(
            RANKING_URL,
            params=params,
            headers=PIXIV_RANKING_HEADERS,
            cookies=cookies,
            timeout=40,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            # R-18 without a valid cookie returns the HTML landing page, not JSON.
            raise RuntimeError(f"pixiv ranking '{mode}' did not return JSON (auth/cookie issue)") from exc
        if isinstance(payload, dict):
            if payload.get("error"):
                raise RuntimeError(f"pixiv ranking '{mode}' error: {payload.get('message') or 'unknown'}")
            contents = payload.get("contents")
            if isinstance(contents, list):
                return contents
        return []

    def _ranking_item_metadata(self, illust, rank):
        illust_id = str(self._illust_id(illust) or "")
        title = str(_get_value(illust, "title", "") or "").strip()
        artist = str(_get_value(illust, "user_name", "") or "").strip()
        if not artist:
            user = _get_value(illust, "user", {}) or {}
            artist = str(_get_value(user, "name", "") or "").strip()
        tags = self._tag_names(illust)
        image_url = self._image_url(illust)
        if not illust_id or not image_url:
            raise RuntimeError("ranking item missing id or image URL")

        try:
            rank_value = int(_get_value(illust, "rank", rank) or rank)
        except (TypeError, ValueError):
            rank_value = int(rank)

        width, height = 0, 0
        try:
            width = int(_get_value(illust, "width", 0) or 0)
            height = int(_get_value(illust, "height", 0) or 0)
        except (TypeError, ValueError):
            width, height = 0, 0

        return {
            "illust_id": illust_id,
            "rank": rank_value,
            "title": title,
            "artist": artist,
            "tags": tags,
            "width": width,
            "height": height,
            "page_url": f"https://www.pixiv.net/artworks/{illust_id}",
            "image_url": image_url,
            "cached_at": self._now_utc().isoformat(),
        }

    def _download_ranking_item_image(self, item, dimensions):
        tmp_path = None
        resized_path = None
        try:
            tmp_path = self._download_to_temp(item["image_url"])
            image_info = self._source_image_info(tmp_path)
            if image_info and image_info["pixels"] > MAX_PI_SAFE_SOURCE_PIXELS:
                if image_info["format"] == "WEBP":
                    raise RuntimeError("oversized WebP skipped for Pi-safe decode")
                resized_path = self._downsample_to_pi_safe_image(tmp_path)
            load_path = resized_path or tmp_path
            image = self.image_loader.from_file(str(load_path), dimensions, resize=False)
            if not image:
                raise RuntimeError("image load returned empty")
            return self._write_cached_image(item, image)
        finally:
            for path in (tmp_path, resized_path):
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception:
                        pass

    def _download_to_temp(self, url):
        response = get_http_session().get(
            url,
            timeout=40,
            stream=True,
            headers=PIXIV_IMAGE_HEADERS,
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

    def _write_cached_image(self, item, image):
        image_dir = self._cache_dir() / "images" / self._day_key()
        image_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^0-9A-Za-z_-]+", "_", str(item.get("illust_id") or "item"))
        image_path = image_dir / f"{int(item.get('rank') or 0):02d}_{safe_id}.jpg"
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.save(image_path, format="JPEG", quality=90)
        return image_path

    def _write_current_day_pool(self, items, settings):
        state = self._read_state()
        state.update({
            "state_version": STATE_VERSION,
            "day_key": self._day_key(),
            "ranking_mode": self._ranking_mode(settings),
            "pool_size": self._pool_size(settings),
            "refreshed_at": self._now_utc().isoformat(),
            "queue": [],
        })
        self._write_daily_pool(items)
        self._write_state(state)
        return state

    def _read_daily_pool(self):
        payload = self._read_daily_pool_payload()
        if not payload:
            return []
        items = payload.get("items")
        return list(items or []) if isinstance(items, list) else []

    def _read_daily_pool_payload(self):
        try:
            path = self._daily_pool_path()
            if not path.is_file():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("state_version") != STATE_VERSION:
                return None
            if payload.get("day_key") != self._day_key():
                return None
            return payload
        except Exception as exc:
            logger.warning("Could not read Pixiv R-18 daily pool: %s", exc)
            return None

    def _write_daily_pool(self, items):
        payload = {
            "state_version": STATE_VERSION,
            "day_key": self._day_key(),
            "items": list(items or []),
        }
        path = self._daily_pool_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(path, payload)

    def _init_queue(self, pool_by_id, state):
        """Build the rotation queue: reuse the saved one, else reshuffle the pool."""
        queue = [
            str(illust_id)
            for illust_id in state.get("queue", [])
            if str(illust_id) in pool_by_id
        ]
        if not queue:
            queue = list(pool_by_id)
            random.shuffle(queue)
            last_id = str(state.get("last_illust_id") or "")
            if len(queue) > 1 and queue[0] == last_id:
                for index, illust_id in enumerate(queue[1:], start=1):
                    if illust_id != last_id:
                        queue[0], queue[index] = queue[index], queue[0]
                        break
        return queue

    def _select_daily_item(self, pool):
        pool_by_id = {str(item.get("illust_id")): item for item in pool if item.get("illust_id")}
        if not pool_by_id:
            return None

        state = self._read_state()
        queue = self._init_queue(pool_by_id, state)

        selected_id = queue.pop(0)
        state["queue"] = queue
        state["last_illust_id"] = selected_id
        state["last_displayed_at"] = self._now_utc().isoformat()
        self._write_state(state)
        return pool_by_id.get(selected_id)

    def _select_display_group(self, pool, settings):
        """Pick the next 1-3 items to show, advancing the rotation queue.

        In ``auto_layout`` mode a portrait at the queue head pulls the next
        portraits (in queue order) to form a 2-3 wide strip; landscape heads and
        every other fit mode display a single image.
        """
        pool_by_id = {str(item.get("illust_id")): item for item in pool if item.get("illust_id")}
        if not pool_by_id:
            return []

        state = self._read_state()
        queue = self._init_queue(pool_by_id, state)
        if not queue:
            return []

        head_id = queue.pop(0)
        group_ids = [head_id]

        if self._fit_mode(settings) == "auto_layout" and self._is_portrait_item(pool_by_id[head_id]):
            remaining = []
            for illust_id in queue:
                if len(group_ids) < MAX_STRIP_CELLS and self._is_portrait_item(pool_by_id[illust_id]):
                    group_ids.append(illust_id)
                else:
                    remaining.append(illust_id)
            queue = remaining

        state["queue"] = queue
        state["last_illust_id"] = group_ids[-1]
        state["last_displayed_at"] = self._now_utc().isoformat()
        self._write_state(state)
        return [pool_by_id[illust_id] for illust_id in group_ids]

    def _is_portrait_item(self, item):
        try:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        if width > 0 and height > 0:
            return height > width
        path = Path(item.get("image_path") or "")
        if path.is_file():
            try:
                with Image.open(path) as image:
                    return image.height > image.width
            except Exception:
                return False
        return False

    def _load_cached_item_image(self, item, dimensions):
        path = Path(item.get("image_path") or "")
        if not path.is_file():
            return None
        image = self.image_loader.from_file(str(path), dimensions, resize=False)
        if not image:
            return None
        return image.convert("RGB")

    def _fit_mode(self, settings):
        return str(settings.get("fitMode") or DEFAULT_FIT_MODE).strip().lower()

    def _compose_strip(self, images, dimensions, settings):
        """Place 2-3 images side by side, each crop-filled into an equal column."""
        width, height = dimensions
        count = len(images)
        canvas = self._solid_background(dimensions, settings)
        # Column edges that sum exactly to the full width (no seams, no remainder).
        edges = [round(width * index / count) for index in range(count + 1)]
        for index, image in enumerate(images):
            x0, x1 = edges[index], edges[index + 1]
            cell = ImageOps.fit(
                ImageOps.exif_transpose(image).convert("RGB"),
                (max(1, x1 - x0), height),
                method=Image.LANCZOS,
            )
            canvas.paste(cell, (x0, 0))
        return canvas

    def _fit_image(self, image, dimensions, settings, item=None):
        fit_mode = self._fit_mode(settings)
        image = ImageOps.exif_transpose(image).convert("RGB")
        if fit_mode == "contain":
            fitted = ImageOps.contain(image, dimensions, method=Image.LANCZOS)
            canvas = self._background(dimensions, settings, None)
            x = (dimensions[0] - fitted.width) // 2
            y = (dimensions[1] - fitted.height) // 2
            canvas.paste(fitted, (x, y))
        else:
            image = self._rotate_portrait_for_landscape_display(image, dimensions)
            canvas = ImageOps.fit(image, dimensions, method=Image.LANCZOS)

        if _setting_enabled(settings.get("showInfoOverlay", "false")):
            canvas = self._with_info_overlay(canvas, item or {})
        return canvas

    def _rotate_portrait_for_landscape_display(self, image, dimensions):
        if dimensions[0] > dimensions[1] and image.height > image.width:
            return image.rotate(90, expand=True)
        return image

    def _background(self, dimensions, settings, image=None):
        base = self._solid_background(dimensions, settings)
        if image is None:
            return base
        try:
            backdrop = ImageOps.fit(image, dimensions, method=Image.LANCZOS)
            blur_radius = max(4, min(dimensions) // 55)
            backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            backdrop = ImageEnhance.Color(backdrop).enhance(0.45)
            backdrop = ImageEnhance.Contrast(backdrop).enhance(0.85)
            color = str(settings.get("backgroundColor") or "black").lower()
            return Image.blend(backdrop, base, 0.35 if color == "black" else 0.55)
        except Exception as exc:
            logger.warning("Could not render Pixiv ranking blurred background: %s", exc)
            return base

    def _with_info_overlay(self, image, item):
        draw = ImageDraw.Draw(image, "RGBA")
        width, height = image.size
        overlay_height = max(44, height // 8)
        draw.rectangle((0, height - overlay_height, width, height), fill=(0, 0, 0, 150))
        font = self._font(max(14, height // 28), bold=True)
        small_font = self._font(max(11, height // 36))
        title = self._fit_text(draw, str(item.get("title") or "Pixiv ranking"), font, width - 24)
        artist = self._fit_text(draw, f"#{item.get('rank')} {item.get('artist') or ''}".strip(), small_font, width - 24)
        draw.text((12, height - overlay_height + 8), title, fill=(255, 255, 255, 235), font=font)
        draw.text((12, height - overlay_height + 28), artist, fill=(220, 220, 220, 220), font=small_font)
        return image

    def _is_safe_ranking_item(self, illust):
        # ranking.php marks animated works as illust_type "2"; the app-api used "ugoira".
        if str(_get_value(illust, "illust_type", "") or "") == "2":
            return False
        if str(_get_value(illust, "type", "") or "").lower() == "ugoira":
            return False

        # Masked entries cannot be displayed in full; skip them.
        if _get_value(illust, "is_masked", False):
            return False

        # ranking.php exposes per-illust content flags directly; reject minors/gore.
        content_type = _get_value(illust, "illust_content_type", {}) or {}
        if _get_value(content_type, "lo", False):
            return False
        if _get_value(content_type, "grotesque", False):
            return False

        # Defensive guard for the legacy app-api shape (x_restrict >= 2 == R-18G).
        try:
            x_restrict = int(_get_value(illust, "x_restrict", 0) or 0)
        except (TypeError, ValueError):
            x_restrict = 0
        if x_restrict >= 2:
            return False

        normalized_tags = {_normalize_tag(tag) for tag in self._tag_names(illust)}
        return not any(tag in RISK_TAGS for tag in normalized_tags)

    def _tag_names(self, illust):
        names = []
        for tag in _get_value(illust, "tags", []) or []:
            if isinstance(tag, str):
                names.append(tag)
                continue
            for key in ("name", "translated_name"):
                value = _get_value(tag, key, "")
                if value:
                    names.append(str(value))
        return names

    def _image_url(self, illust):
        # ranking.php gives a single sized master URL.
        url = _get_value(illust, "url", "")
        if url:
            return str(url)

        # Defensive fallbacks for the legacy app-api shape.
        meta_pages = _get_value(illust, "meta_pages", []) or []
        if meta_pages:
            image_urls = _get_value(meta_pages[0], "image_urls", {}) or {}
            for key in ("original", "large", "medium", "square_medium"):
                value = _get_value(image_urls, key, "")
                if value:
                    return str(value)

        single_page = _get_value(illust, "meta_single_page", {}) or {}
        value = _get_value(single_page, "original_image_url", "")
        if value:
            return str(value)
        return ""

    def _illust_id(self, illust):
        return _get_value(illust, "illust_id", "") or _get_value(illust, "id", "")

    def _load_session_cookie(self, device_config):
        value = ""
        if device_config is not None and hasattr(device_config, "load_env_key"):
            try:
                value = device_config.load_env_key("PIXIV_PHPSESSID") or ""
            except Exception as exc:
                logger.warning("Could not read PIXIV_PHPSESSID from device config: %s", exc)
        return str(value or os.getenv("PIXIV_PHPSESSID", "") or "").strip()

    def _ranking_mode(self, settings):
        mode = str(settings.get("rankingMode") or DEFAULT_RANKING_MODE).strip()
        return mode or DEFAULT_RANKING_MODE

    def _pool_size(self, settings):
        try:
            size = int(settings.get("poolSize") or DEFAULT_POOL_SIZE)
        except (TypeError, ValueError):
            size = DEFAULT_POOL_SIZE
        return max(1, min(MAX_POOL_SIZE, size))

    def _display_dimensions(self, device_config):
        # Self-contained on purpose: BasePlugin.get_dimensions() is not present on
        # every deployed base_plugin version, so resolve the resolution here.
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        return dimensions

    def _day_key(self):
        return self._now_utc().astimezone(JST).date().isoformat()

    def _daily_pool_path(self):
        return self._cache_dir() / "daily_pool.json"

    def _state_path(self):
        return self._cache_dir() / "state.json"

    def _cache_dir(self):
        path = os.getenv("INKYPI_PIXIV_R18_CACHE")
        if path:
            return Path(path)
        return Path(self.get_plugin_dir(".pixiv_r18_ranking_cache"))

    def _read_state(self):
        path = self._state_path()
        try:
            if path.is_file():
                state = json.loads(path.read_text(encoding="utf-8"))
                return state if isinstance(state, dict) else {}
        except Exception as exc:
            logger.warning("Could not read Pixiv R-18 state %s: %s", path, exc)
        return {}

    def _write_state(self, state):
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(path, state if isinstance(state, dict) else {})

    def _atomic_write_json(self, path, payload):
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(payload, ensure_ascii=True, indent=2)
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            path.write_text(text, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

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
                    "Downsampled oversized Pixiv ranking image for Pi-safe decode: %sx%s -> %sx%s",
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

    def _solid_background(self, dimensions, settings):
        color = str(settings.get("backgroundColor") or "black").lower()
        base_color = (255, 255, 255) if color == "white" else (0, 0, 0)
        return Image.new("RGB", dimensions, base_color)

    def _fallback_image(self, dimensions, title, subtitle):
        image = Image.new("RGB", dimensions, "white")
        draw = ImageDraw.Draw(image)
        width, height = dimensions
        border = max(12, min(width, height) // 24)
        draw.rectangle((border, border, width - border, height - border), outline="black", width=3)
        draw.line((border, height // 2, width - border, height // 2), fill=(180, 180, 180), width=2)

        title_font = self._font(max(28, width // 12), bold=True)
        subtitle_font = self._font(max(18, width // 24))
        self._draw_centered(draw, title, width // 2, height // 2 - 46, title_font, "black")
        self._draw_centered(draw, subtitle, width // 2, height // 2 + 24, subtitle_font, (70, 70, 70))
        return image

    def _font(self, size, bold=False):
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for path in paths:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _draw_centered(self, draw, text, x, y, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((x - (bbox[2] - bbox[0]) // 2, y - (bbox[3] - bbox[1]) // 2), text, font=font, fill=fill)

    def _fit_text(self, draw, text, font, max_width):
        if draw.textlength(text, font=font) <= max_width:
            return text
        candidate = text
        while candidate and draw.textlength(candidate + "...", font=font) > max_width:
            candidate = candidate[:-1].rstrip()
        return f"{candidate}..." if candidate else text[:1]

    def _now_utc(self):
        return datetime.now(timezone.utc)


def _get_value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_tag(value):
    return str(value or "").strip().casefold()


def _setting_enabled(value):
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}
