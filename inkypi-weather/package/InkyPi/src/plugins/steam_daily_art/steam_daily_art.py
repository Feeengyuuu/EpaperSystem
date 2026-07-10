from plugins.base_plugin.base_plugin import BasePlugin
from utils.http_client import get_http_session
from utils.safe_image import safe_open_image, safe_open_image_response
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat
from datetime import datetime
import hashlib
import json
import logging
import os
import random

from plugins.context_cache import write_context

logger = logging.getLogger(__name__)

STEAM_FEATURED_CATEGORIES_URL = "https://store.steampowered.com/api/featuredcategories"
STEAM_CDN_APP_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{image_name}.jpg"
STEAM_CDN_ASSET_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{filename}"
STEAM_DAILY_ART_VERSION = "fresh-frontpage-ranked-live-list-v1"


class SteamDailyArt(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)

        rotation_key = self._rotation_key(device_config, settings)
        cache_key = self._cache_key(settings, dimensions, rotation_key)
        cache_entry = self._read_cache()

        force_refresh = self._settings_enabled(settings.get("forceRefresh")) or self._settings_enabled(settings.get("force_refresh"))
        if not force_refresh and cache_entry.get("cache_key") == cache_key:
            cached_image = cache_entry.get("image_path")
            if cached_image and os.path.exists(cached_image):
                logger.info(f"Using cached Steam daily art for {rotation_key}: {cached_image}")
                self._write_daily_art_context(cache_entry, settings, self._now_for_device(device_config))
                return safe_open_image(cached_image).convert("RGB")

        try:
            item = self._select_item(settings, rotation_key)
            image_url, image = self._download_first_available_image(item, settings)
            logger.info(f"Selected Steam art: {item.get('name', 'Unknown')} | {image_url}")
            image = self._smart_cover(image, dimensions)

            logo_url = None
            if settings.get("logoOverlay", "show") != "hide":
                logo_url, logo = self._download_first_available_logo(item)
                if logo:
                    image = self._overlay_logo(image, logo, settings)

            if settings.get("showCaption") == "true":
                image = self._add_caption(image, item.get("name", "Steam"))

            image_path = self._cache_image_path(rotation_key)
            os.makedirs(os.path.dirname(image_path), exist_ok=True)
            image.save(image_path)
            cache_payload = {
                "cache_key": cache_key,
                "rotation_key": rotation_key,
                "name": item.get("name"),
                "appid": item.get("id") or item.get("appid"),
                "image_url": image_url,
                "logo_url": logo_url,
                "image_path": image_path,
            }
            self._write_cache(cache_payload)
            self._write_daily_art_context(cache_payload, settings, self._now_for_device(device_config))

            return image
        except Exception as e:
            logger.error(f"Steam Daily Art failed: {e}")
            stale_image = cache_entry.get("image_path")
            if stale_image and os.path.exists(stale_image):
                logger.warning("Using stale Steam daily art cache.")
                self._write_daily_art_context(cache_entry, settings, self._now_for_device(device_config))
                return safe_open_image(stale_image).convert("RGB")
            raise RuntimeError(f"Steam Daily Art failed: {str(e)}")

    def _write_daily_art_context(self, entry, settings, generated_at):
        if not isinstance(entry, dict):
            return
        name = str(entry.get("name") or "Steam promotion").strip()
        summary = f"Steam promotion: {name}"
        if entry.get("appid"):
            summary += f" (app {entry.get('appid')})"
        write_context(
            "steam_daily_art",
            {
                "kind": "game_promo",
                "source": "Steam Daily Art",
                "summary": summary,
                "items": [{
                    "name": name,
                    "appid": entry.get("appid"),
                    "rotation_key": entry.get("rotation_key"),
                    "image_url": entry.get("image_url"),
                }],
            },
            generated_at=generated_at,
            ttl_seconds=self._context_ttl_seconds(settings),
        )

    @staticmethod
    def _settings_enabled(value):
        return value is True or str(value).strip().lower() in {"1", "true", "on", "yes"}

    def _context_ttl_seconds(self, settings):
        cadence = settings.get("rotationCadence", "hourly")
        if cadence == "every_refresh":
            return 30 * 60
        if cadence == "hourly":
            return 2 * 60 * 60
        if cadence == "six_hours":
            return 7 * 60 * 60
        return 26 * 60 * 60

    def _today_key(self, device_config):
        return self._now_for_device(device_config).strftime("%Y-%m-%d")

    def _now_for_device(self, device_config):
        timezone_name = device_config.get_config("timezone", default="")
        try:
            import pytz
            tz = pytz.timezone(timezone_name)
            return datetime.now(tz)
        except Exception:
            return datetime.now()

    def _rotation_key(self, device_config, settings):
        now = self._now_for_device(device_config)
        cadence = settings.get("rotationCadence", "hourly")
        if cadence == "every_refresh":
            return now.strftime("%Y-%m-%d-%H-%M-%S-%f")
        if cadence == "hourly":
            return now.strftime("%Y-%m-%d-%H")
        if cadence == "six_hours":
            return f"{now.strftime('%Y-%m-%d')}-slot-{now.hour // 6}"
        return now.strftime("%Y-%m-%d")

    def _cache_dir(self):
        return self.cache_dir(leaf=".steam_daily_art_cache", create=True)

    def _cache_path(self):
        return os.path.join(self._cache_dir(), "cache.json")

    def _selection_state_path(self):
        return os.path.join(self._cache_dir(), "selection_state.json")

    def _cache_image_path(self, rotation_key):
        safe_key = "".join(char if char.isalnum() or char in "-_" else "-" for char in str(rotation_key))
        return os.path.join(self._cache_dir(), f"{safe_key}.png")

    def _read_cache(self):
        try:
            with open(self._cache_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write_cache(self, data):
        with open(self._cache_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _read_selection_state(self):
        try:
            with open(self._selection_state_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write_selection_state(self, state):
        with open(self._selection_state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _cache_key(self, settings, dimensions, rotation_key):
        parts = [
            STEAM_DAILY_ART_VERSION,
            rotation_key,
            str(dimensions),
            settings.get("sourceCategory", "fresh_frontpage"),
            settings.get("selectionMode", "daily_rotation"),
            settings.get("rotationCadence", "hourly"),
            settings.get("imageMode", "library_hero"),
            settings.get("logoOverlay", "show"),
            settings.get("logoPosition", "empty_space"),
            settings.get("logoSize", "normal"),
            settings.get("countryCode", "US"),
            settings.get("language", "english"),
            settings.get("showCaption", "false"),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def _fetch_featured_categories(self, settings):
        country_code = (settings.get("countryCode") or "US").strip().upper()[:2]
        language = (settings.get("language") or "english").strip() or "english"
        params = {"cc": country_code, "l": language}

        session = get_http_session()
        response = session.get(STEAM_FEATURED_CATEGORIES_URL, params=params)
        response.raise_for_status()
        return response.json()

    def _collect_items(self, data, source_category):
        matched = []
        fallback = []

        for category_key, category_data in data.items():
            if not isinstance(category_data, dict):
                continue

            items = category_data.get("items")
            if not isinstance(items, list):
                continue

            category_id = str(category_data.get("id") or category_key).lower()
            category_name = str(category_data.get("name") or category_key).lower()
            category_tags = self._category_tags(str(category_key), category_id, category_name)

            for item in items:
                if not isinstance(item, dict):
                    continue
                if not self._has_usable_image(item):
                    continue

                item = dict(item)
                item["_category_key"] = category_key
                item["_category_id"] = category_id
                item["_category_name"] = category_name
                item["_category_tags"] = sorted(category_tags)
                fallback.append(item)

                if self._category_matches_source(source_category, category_tags):
                    matched.append(item)

        items = matched or fallback
        return self._dedupe_items(items)

    def _category_tags(self, category_key, category_id, category_name):
        text = " ".join([category_key, category_id, category_name]).lower()
        tags = set()
        if "spotlight" in text:
            tags.add("spotlights")
        if "daily" in text:
            tags.add("daily_deals")
        if "special" in text:
            tags.add("specials")
        if "top" in text and "seller" in text:
            tags.add("top_sellers")
        if "new" in text and "release" in text:
            tags.add("new_releases")
        if "coming" in text:
            tags.add("coming_soon")
        return tags

    def _category_matches_source(self, source_category, category_tags):
        source_category = source_category or "fresh_frontpage"
        if source_category == "featured":
            return True
        if source_category == "fresh_frontpage":
            return bool(category_tags & {"spotlights", "daily_deals", "specials", "top_sellers", "new_releases"})
        if source_category == "specials":
            return bool(category_tags & {"specials", "daily_deals"})
        return source_category in category_tags

    def _dedupe_items(self, items):
        seen = set()
        unique = []
        for item in items:
            appid = str(item.get("id") or item.get("appid") or "")
            image = item.get("large_capsule_image") or item.get("header_image") or ""
            key = appid or image
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _has_usable_image(self, item):
        return bool(
            item.get("large_capsule_image")
            or item.get("header_image")
            or item.get("small_capsule_image")
            or item.get("id")
            or item.get("appid")
        )

    def _select_item(self, settings, today_key):
        source_category = settings.get("sourceCategory", "fresh_frontpage")
        data = self._fetch_featured_categories(settings)
        items = self._collect_items(data, source_category)
        if not items:
            raise RuntimeError("No Steam promotional items found.")

        selection_mode = settings.get("selectionMode", "daily_rotation")
        if selection_mode == "first":
            return items[0]

        return self._select_no_repeat_item(items, settings, today_key, source_category, selection_mode)

    def _select_no_repeat_item(self, items, settings, rotation_key, source_category, selection_mode):
        pool_ids = [self._item_identity(item) for item in items]
        item_by_id = {item_id: item for item_id, item in zip(pool_ids, items)}
        pool_key = self._selection_pool_key(settings, source_category, selection_mode, pool_ids)
        state = self._read_selection_state()
        pool_state = state.get(pool_key, {})
        used = list(dict.fromkeys(pool_state.get("used", [])))
        used_set = set(used)

        if selection_mode == "random":
            remaining = [
                item_id
                for item_id in pool_state.get("remaining", [])
                if item_id in item_by_id
            ]
            new_ids = [item_id for item_id in pool_ids if item_id not in used_set and item_id not in remaining]
            remaining.extend(new_ids)
            seed = f"{rotation_key}|{source_category}|{settings.get('countryCode', 'US')}|{settings.get('language', 'english')}|{len(used)}"
            if not remaining:
                remaining = list(pool_ids)
                random.Random(seed).shuffle(remaining)
                used = []
            elif not pool_state or new_ids:
                random.Random(seed).shuffle(remaining)
            selected_id = remaining.pop(0)
        else:
            remaining = [item_id for item_id in pool_ids if item_id not in used_set]
            if not remaining:
                used = []
                used_set = set()
                remaining = list(pool_ids)
            selected_id = remaining.pop(0)

        if selection_mode == "random":
            next_remaining = remaining
        else:
            next_used = set(used)
            next_used.add(selected_id)
            next_remaining = [item_id for item_id in pool_ids if item_id not in next_used]

        used.append(selected_id)
        history_limit = max(250, len(pool_ids) * 4)
        used = used[-history_limit:]

        state[pool_key] = {
            "remaining": next_remaining,
            "used": used,
            "last_selected": selected_id,
            "last_rotation_key": rotation_key,
            "pool_size": len(pool_ids),
            "last_pool_ids": pool_ids,
        }
        self._write_selection_state(state)
        return item_by_id[selected_id]

    def _selection_pool_key(self, settings, source_category, selection_mode, pool_ids):
        parts = [
            STEAM_DAILY_ART_VERSION,
            source_category,
            selection_mode,
            settings.get("countryCode", "US"),
            settings.get("language", "english"),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def _item_identity(self, item):
        appid = item.get("id")
        if appid is None or not str(appid).strip():
            appid = item.get("appid")
        if appid is not None and str(appid).strip():
            return f"app:{appid}"
        image = item.get("large_capsule_image") or item.get("header_image") or item.get("small_capsule_image")
        if image:
            return f"image:{image}"
        return f"name:{item.get('name', '')}"

    def _candidate_image_urls(self, item, settings):
        image_mode = settings.get("imageMode", "library_hero")
        appid = item.get("id") or item.get("appid")

        if image_mode == "header":
            return [
                item.get("header_image"),
                self._steam_app_image(appid, "header"),
                self._steam_app_image(appid, "library_hero"),
                item.get("large_capsule_image"),
            ]
        if image_mode == "large_capsule":
            return [
                item.get("large_capsule_image"),
                self._steam_app_image(appid, "capsule_616x353"),
                self._steam_app_image(appid, "library_hero"),
                item.get("header_image"),
            ]
        if image_mode == "auto":
            return [
                self._steam_app_image(appid, "library_hero"),
                item.get("large_capsule_image"),
                item.get("header_image"),
                item.get("small_capsule_image"),
                self._steam_app_image(appid, "capsule_616x353"),
            ]

        return [
            self._steam_app_image(appid, "library_hero"),
            item.get("large_capsule_image"),
            item.get("header_image"),
            self._steam_app_image(appid, "capsule_616x353"),
        ]

    def _steam_app_image(self, appid, image_name):
        if not appid:
            return None
        return STEAM_CDN_APP_URL.format(appid=appid, image_name=image_name)

    def _steam_app_asset(self, appid, filename):
        if not appid:
            return None
        return STEAM_CDN_ASSET_URL.format(appid=appid, filename=filename)

    def _download_first_available_image(self, item, settings):
        errors = []
        for url in self._candidate_image_urls(item, settings):
            if not url:
                continue
            try:
                return url, self._download_image(url)
            except Exception as e:
                errors.append(f"{url}: {type(e).__name__}")
                logger.warning(f"Steam image candidate failed: {url} | {e}")

        raise RuntimeError("No usable Steam image found. " + "; ".join(errors[:3]))

    def _logo_candidate_urls(self, item):
        appid = item.get("id") or item.get("appid")
        return [
            item.get("logo"),
            item.get("logo_url"),
            self._steam_app_asset(appid, "logo.png"),
        ]

    def _download_first_available_logo(self, item):
        for url in self._logo_candidate_urls(item):
            if not url:
                continue
            try:
                return url, self._download_logo(url)
            except Exception as e:
                logger.warning(f"Steam logo candidate failed: {url} | {e}")

        logger.info("No Steam logo overlay found for selected item.")
        return None, None

    def _download_image(self, url):
        session = get_http_session()
        response = session.get(url, timeout=40, stream=True)
        return safe_open_image_response(response).convert("RGB")

    def _download_logo(self, url):
        session = get_http_session()
        response = session.get(url, stream=True)
        return safe_open_image_response(response).convert("RGBA")

    def _smart_cover(self, image, dimensions):
        target_width, target_height = dimensions
        target_ratio = target_width / target_height
        image_ratio = image.width / image.height

        if image_ratio < target_ratio * 0.82:
            return self._contain_on_canvas(image, dimensions)

        if abs(image_ratio - target_ratio) < 0.01:
            cropped = image
        elif image_ratio > target_ratio:
            crop_width = int(image.height * target_ratio)
            x = self._best_crop_offset(image, crop_width, horizontal=True)
            cropped = image.crop((x, 0, x + crop_width, image.height))
        else:
            crop_height = int(image.width / target_ratio)
            y = self._best_crop_offset(image, crop_height, horizontal=False)
            cropped = image.crop((0, y, image.width, y + crop_height))

        return cropped.resize((target_width, target_height), Image.Resampling.LANCZOS)

    def _contain_on_canvas(self, image, dimensions):
        target_width, target_height = dimensions
        image_ratio = image.width / image.height
        target_ratio = target_width / target_height

        if image_ratio > target_ratio:
            new_width = target_width
            new_height = max(1, int(target_width / image_ratio))
        else:
            new_height = target_height
            new_width = max(1, int(target_height * image_ratio))

        fitted = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

        canvas = image.resize((target_width, target_height), Image.Resampling.BILINEAR)
        canvas = ImageOps.fit(canvas, (target_width, target_height), method=Image.Resampling.BILINEAR, centering=(0.5, 0.5))
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=18))
        canvas = Image.blend(canvas, Image.new("RGB", dimensions, (245, 245, 245)), 0.45)

        x = (target_width - new_width) // 2
        y = (target_height - new_height) // 2
        canvas.paste(fitted, (x, y))
        return canvas

    def _best_crop_offset(self, image, crop_size, horizontal=True):
        full_size = image.width if horizontal else image.height
        max_offset = full_size - crop_size
        if max_offset <= 0:
            return 0

        # Steam promotional images usually keep the key art near center. Use a light
        # edge-density pass only to avoid cutting off high-detail logo/art regions.
        sample = image.convert("L").resize((160, 90), Image.Resampling.BILINEAR)
        edges = sample.filter(ImageFilter.FIND_EDGES)

        sample_full = sample.width if horizontal else sample.height
        sample_crop = max(1, int(crop_size * sample_full / full_size))
        sample_max = sample_full - sample_crop
        if sample_max <= 0:
            return max_offset // 2

        steps = min(24, sample_max + 1)
        best_score = None
        best_sample_offset = sample_max // 2

        for i in range(steps):
            offset = round(i * sample_max / max(1, steps - 1))
            if horizontal:
                region = edges.crop((offset, 0, offset + sample_crop, edges.height))
            else:
                region = edges.crop((0, offset, edges.width, offset + sample_crop))

            edge_score = sum(region.histogram()[128:])
            center = offset + sample_crop / 2
            center_bias = 1 - abs(center - sample_full / 2) / (sample_full / 2)
            score = edge_score + center_bias * 500

            if best_score is None or score > best_score:
                best_score = score
                best_sample_offset = offset

        return int(best_sample_offset * max_offset / sample_max)

    def _overlay_logo(self, image, logo, settings):
        logo = self._trim_transparent_edges(logo)
        if not logo or logo.width < 2 or logo.height < 2:
            return image

        size_name = settings.get("logoSize", "normal")
        scale_map = {
            "compact": (0.34, 0.14),
            "normal": (0.46, 0.18),
            "large": (0.58, 0.24),
        }
        max_width_factor, max_height_factor = scale_map.get(size_name, scale_map["normal"])

        max_width = int(image.width * max_width_factor)
        max_height = int(image.height * max_height_factor)
        scale = min(max_width / logo.width, max_height / logo.height, 1.0)
        new_size = (max(1, int(logo.width * scale)), max(1, int(logo.height * scale)))
        logo = logo.resize(new_size, Image.Resampling.LANCZOS)

        x, y = self._logo_position(
            image.size,
            logo.size,
            settings.get("logoPosition", "empty_space"),
            image=image,
        )

        composed = image.convert("RGBA")

        shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
        alpha = logo.getchannel("A")
        shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(radius=3))
        shadow.putalpha(shadow_alpha.point(lambda value: int(value * 0.55)))
        composed.alpha_composite(shadow, (x + 3, y + 3))
        composed.alpha_composite(logo, (x, y))

        return composed.convert("RGB")

    def _trim_transparent_edges(self, logo):
        if logo.mode != "RGBA":
            logo = logo.convert("RGBA")
        bbox = logo.getchannel("A").getbbox()
        if not bbox:
            return None
        return logo.crop(bbox)

    def _logo_position(self, canvas_size, logo_size, position, image=None):
        canvas_width, canvas_height = canvas_size
        logo_width, logo_height = logo_size
        margin_x = max(18, canvas_width // 32)
        margin_y = max(18, canvas_height // 24)

        if position == "empty_space" and image is not None:
            return self._find_empty_logo_position(image, logo_size, margin_x, margin_y)
        if position == "center":
            return ((canvas_width - logo_width) // 2, (canvas_height - logo_height) // 2)
        if position == "golden_left":
            anchor_x = int(canvas_width * 0.38)
            anchor_y = int(canvas_height * 0.42)
            x = anchor_x - logo_width // 2
            y = anchor_y - logo_height // 2
            x = max(margin_x, min(x, canvas_width - logo_width - margin_x))
            y = max(margin_y, min(y, canvas_height - logo_height - margin_y))
            return (x, y)
        if position == "bottom_left":
            return (margin_x, canvas_height - logo_height - margin_y)
        if position == "bottom_center":
            return ((canvas_width - logo_width) // 2, canvas_height - logo_height - margin_y)

        return ((canvas_width - logo_width) // 2, margin_y)

    def _find_empty_logo_position(self, image, logo_size, margin_x, margin_y):
        canvas_width, canvas_height = image.size
        logo_width, logo_height = logo_size
        max_x = canvas_width - logo_width - margin_x
        max_y = canvas_height - logo_height - margin_y

        if max_x <= margin_x or max_y <= margin_y:
            return self._logo_position(image.size, logo_size, "golden_left")

        candidates = self._logo_position_candidates(
            canvas_width,
            canvas_height,
            logo_width,
            logo_height,
            margin_x,
            margin_y,
        )
        gray = image.convert("L")
        edges = gray.filter(ImageFilter.FIND_EDGES)

        best = None
        best_score = None
        preferred_x = canvas_width * 0.38
        preferred_y = canvas_height * 0.42

        for x, y in candidates:
            box = (x, y, x + logo_width, y + logo_height)
            edge_region = edges.crop(box)
            gray_region = gray.crop(box)
            edge_mean = ImageStat.Stat(edge_region).mean[0]
            luminance_std = ImageStat.Stat(gray_region).stddev[0]

            center_x = x + logo_width / 2
            center_y = y + logo_height / 2
            distance_x = abs(center_x - preferred_x) / canvas_width
            distance_y = abs(center_y - preferred_y) / canvas_height
            library_bias = (distance_x * 0.8 + distance_y * 0.35) * 35

            # Lower score means the logo sits on a flatter, less detailed area.
            score = edge_mean * 1.5 + luminance_std * 0.65 + library_bias
            if best_score is None or score < best_score:
                best_score = score
                best = (x, y)

        return best or self._logo_position(image.size, logo_size, "golden_left")

    def _logo_position_candidates(self, canvas_width, canvas_height, logo_width, logo_height, margin_x, margin_y):
        max_x = canvas_width - logo_width - margin_x
        max_y = canvas_height - logo_height - margin_y
        positions = set()

        anchor_points = [
            (0.24, 0.28), (0.38, 0.28), (0.52, 0.28), (0.70, 0.28),
            (0.24, 0.42), (0.38, 0.42), (0.52, 0.42), (0.70, 0.42),
            (0.24, 0.58), (0.38, 0.58), (0.52, 0.58), (0.70, 0.58),
            (0.24, 0.73), (0.38, 0.73), (0.52, 0.73), (0.70, 0.73),
        ]

        for ax, ay in anchor_points:
            x = int(canvas_width * ax - logo_width / 2)
            y = int(canvas_height * ay - logo_height / 2)
            x = max(margin_x, min(x, max_x))
            y = max(margin_y, min(y, max_y))
            positions.add((x, y))

        step_x = max(32, logo_width // 3)
        step_y = max(28, logo_height // 2)
        x = margin_x
        while x <= max_x:
            y = margin_y
            while y <= max_y:
                positions.add((x, y))
                y += step_y
            x += step_x

        return sorted(positions)

    def _add_caption(self, image, title):
        image = image.copy()
        draw = ImageDraw.Draw(image)
        caption = str(title or "Steam")
        bar_height = max(42, image.height // 10)

        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle((0, image.height - bar_height, image.width, image.height), fill=(0, 0, 0, 160))
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

        draw = ImageDraw.Draw(image)
        text = caption[:80]
        draw.text((18, image.height - bar_height + 12), text, fill=(255, 255, 255))
        return image
