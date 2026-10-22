from utils.resource_cache import cached_resource_image, prune_resource_images
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationPreparation,
    get_presentation_instance_uuid,
)
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.steam_daily_art.presentation_bank import (
    READY_TARGET,
    REFILL_THRESHOLD,
    SteamDailyArtPresentationBank,
    instance_profile_fingerprint,
    read_bounded_json_object,
    settings_key,
    settings_fingerprint,
)
from plugins.base_plugin.theme_presentation import apply_media_theme_chrome
from utils.app_utils import get_font
from utils.safe_image import ImageLimits, safe_open_image
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat
from datetime import datetime
import hashlib
import ipaddress
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from io import BytesIO
from urllib.parse import urlencode, urljoin

from plugins.pixiv_r18_ranking.pixiv_r18_ranking import _PinnedHTTPSResponse
from security.ssrf import get_ssrf_policy

from plugins.context_cache import write_context

logger = logging.getLogger(__name__)

STEAM_FEATURED_CATEGORIES_URL = "https://store.steampowered.com/api/featuredcategories"
STEAM_CDN_APP_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{image_name}.jpg"
STEAM_CDN_ASSET_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{filename}"
# Newer apps keep their store art only under hashed paths; the unhashed CDN
# names above exist for older apps.
STEAM_STORE_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
STEAM_STORE_ASSET_ROOT = "https://shared.akamai.steamstatic.com/store_item_assets/"
STEAM_JSON_HOSTS = ("store.steampowered.com", "api.steampowered.com")
STEAM_DAILY_ART_VERSION = "fresh-frontpage-ranked-live-list-v1"
STEAM_DAILY_ART_RENDER_VERSION = "same-game-lit-scene-v2"
STEAM_ITEM_KINDS = {0: "app", 1: "sub", 2: "bundle"}
MAX_STORE_ITEM_BYTES = 512 * 1024
MAX_ART_GAMES = 3
MAX_SCENE_SCREENSHOTS = 6
# Brightness is the brightest channel, so saturated ink colours count as lit.
# A crop failing either bound prints as an almost black panel on the e-paper.
SCENE_DARK_LEVEL = 48
SCENE_MIN_BRIGHTNESS = 40
SCENE_MAX_DARK_SHARE = 0.8
ART_SCENE = "scene"
ART_TITLED = "titled"
LOGO_MIN_CONTRAST = 64
LOGO_HALO_WIDTH = 3
ASSET_FILENAME_RE = re.compile(r"[0-9A-Za-z_.-]+(?:/[0-9A-Za-z_.-]+)?")
STORE_APP_LINK_RE = re.compile(r"https://store\.steampowered\.com/app/(\d{1,12})(?:[/?#]|$)")
MAX_DATA_NEW_MEDIA = 8
MAX_DATA_ATTEMPTS = 16
MAX_DATA_SECONDS = 45
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_MEDIA_BYTES = 12 * 1024 * 1024
MAX_MEDIA_REDIRECTS = 4
OPTIONAL_MEDIA_NEGATIVE_TTL_SECONDS = 6 * 60 * 60
OPTIONAL_MEDIA_NEGATIVE_CACHE_LIMIT = 256
STEAM_MEDIA_HOST_SUFFIXES = (
    "steamstatic.com",
    "steamcontent.com",
    "steamusercontent.com",
    "akamaihd.net",
)


class _OptionalMediaUnavailable(RuntimeError):
    """Optional media is missing upstream or still inside its retry window."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class _UnusableArtwork(RuntimeError):
    """Every art candidate of a game is missing or too dark; try another game."""


class _ArtSearch:
    """One game's art lookup: its app id, store assets fetched once, failures."""

    def __init__(self, appid, fetch_assets):
        self.appid = appid
        self.download_failed = False
        self._fetch_assets = fetch_assets
        self._assets = None

    def assets(self):
        if self._assets is None:
            self._assets = {}
            if self.appid is not None:
                try:
                    self._assets = self._fetch_assets(self.appid)
                except Exception as exc:
                    self.download_failed = True
                    logger.warning("Steam store art lookup failed for app %s: %s", self.appid, exc)
        return self._assets


class SteamDailyArt(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        return template_params

    def presentation_mode(self, settings):
        mode = str((settings or {}).get("selectionMode") or "current").strip().lower()
        cadence = str((settings or {}).get("rotationCadence") or "hourly").strip().lower()
        if mode == "every_refresh" or cadence == "every_refresh":
            return PresentationMode.PREPARED_BANK
        return PresentationMode.NO_CHANGE

    def prepare_presentation(
        self,
        settings,
        device_config,
        *,
        request,
        resolved_theme_context,
    ):
        if self.presentation_mode(settings) is PresentationMode.NO_CHANGE:
            return PresentationPreparation(
                request_id=request.request_id,
                image=None,
                changed=False,
            )
        dimensions = tuple(int(value) for value in self.get_dimensions(device_config))
        rotation_key = self._bank_rotation_key(device_config, settings)
        bank = self._presentation_bank(settings, dimensions, rotation_key)
        document, profile = bank.load_warm()
        bank.apply_trusted_origin(document, profile, request)
        ready = bank.ready_records(profile, prune=False)
        pending = bank.pending_for_request(profile, request.request_id)
        selection = pending or bank.choose_selection(document, profile, ready)
        image = self._render_bank_selection(bank, profile, selection)
        if resolved_theme_context is not None:
            image = apply_media_theme_chrome(
                image,
                self.get_plugin_id(),
                resolved_theme_context,
                dimensions,
            )
        if pending is None:
            bank.set_pending(document, profile, request, selection)
        return PresentationPreparation(
            request_id=request.request_id,
            image=image,
            changed=True,
        )

    def reconcile_presentation_receipt(self, settings, receipt):
        if receipt is None:
            return None
        instance_uuid = get_presentation_instance_uuid(settings or {})
        if instance_uuid is None:
            raise RuntimeError("Steam receipt reconciliation requires trusted instance identity")
        bank = self._presentation_bank_for_receipt(instance_uuid, receipt.request_id)
        if bank is None:
            return None
        document, profile = bank.load_receipt_profile(receipt.request_id)
        committed = bank.reconcile_receipt(document, profile, receipt)
        if committed:
            self._write_daily_art_context(
                self._context_entry_for_bank_record(committed[-1]),
                settings,
                receipt.committed_at,
            )
        return None

    def _presentation_profile_fingerprint(self, settings, dimensions, rotation_key):
        instance_uuid = get_presentation_instance_uuid(settings or {})
        base = settings_fingerprint(settings, dimensions, rotation_key)
        return instance_profile_fingerprint(base, instance_uuid or "unbound-preview")

    def generate_image(self, settings, device_config):
        settings = settings or {}
        if self.presentation_mode(settings) is PresentationMode.PREPARED_BANK:
            if get_presentation_instance_uuid(settings) is None:
                if settings.get("_theme_render_only") is True:
                    raise RuntimeError("Steam theme-only presentation requires trusted instance identity")
                return self._generate_stateless_preview(settings, device_config)
            if settings.get("_theme_render_only") is True:
                return self._generate_theme_only_banked_image(settings, device_config)
            return self._generate_banked_image(settings, device_config)
        dimensions = self.get_dimensions(device_config)

        rotation_key = self._rotation_key(device_config, settings)
        cache_key = self._cache_key(settings, dimensions, rotation_key)
        cache_entry = self._read_cache()

        force_refresh = self._settings_enabled(settings.get("forceRefresh")) or self._settings_enabled(settings.get("force_refresh"))
        if not force_refresh and cache_entry.get("cache_key") == cache_key:
            cached_image = cache_entry.get("image_path")
            if cached_image and os.path.exists(cached_image):
                logger.info(f"Using cached Steam daily art for {rotation_key}: {cached_image}")
                cached_generated_at = self._cache_generated_at(cache_entry)
                if cached_generated_at is not None:
                    self._write_daily_art_context(
                        cache_entry,
                        settings,
                        cached_generated_at,
                    )
                image = safe_open_image(cached_image).convert("RGB")
                return attach_source_provenance(
                    image,
                    SourceProvenance.FRESH_CACHE,
                )

        item = None
        try:
            skipped = set()
            while True:
                item = self._select_item(settings, rotation_key, exclude=frozenset(skipped))
                try:
                    image_url, logo_url, image = self._render_item(item, settings, dimensions)
                    break
                except _UnusableArtwork as exc:
                    skipped.add(self._item_identity(item))
                    if len(skipped) >= MAX_ART_GAMES:
                        raise
                    logger.info("%s; trying the next Steam game", exc)
            logger.info(f"Selected Steam art: {item.get('name', 'Unknown')} | {image_url}")

            image_path = self._cache_image_path(rotation_key)
            os.makedirs(os.path.dirname(image_path), exist_ok=True)
            image.save(image_path)
            generated_at = self._now_for_device(device_config)
            cache_payload = {
                "cache_key": cache_key,
                "rotation_key": rotation_key,
                "name": item.get("name"),
                "appid": item.get("id") or item.get("appid"),
                "image_url": image_url,
                "logo_url": logo_url,
                "image_path": image_path,
                "generated_at": generated_at.isoformat(),
            }
            self._write_cache(cache_payload)
            self._write_daily_art_context(cache_payload, settings, generated_at)

            return attach_source_provenance(image, SourceProvenance.LIVE)
        except Exception as e:
            logger.error(f"Steam Daily Art failed: {e}")
            stale_image = cache_entry.get("image_path")
            if stale_image and os.path.exists(stale_image):
                logger.warning("Using stale Steam daily art cache.")
                cached_generated_at = self._cache_generated_at(cache_entry)
                if cached_generated_at is not None:
                    self._write_daily_art_context(
                        cache_entry,
                        settings,
                        cached_generated_at,
                    )
                image = safe_open_image(stale_image).convert("RGB")
                image.info["inkypi_skip_cache"] = True
                return attach_source_provenance(
                    image,
                    SourceProvenance.STALE_CACHE,
                )
            if isinstance(item, dict):
                return self._metadata_fallback(dimensions, item)
            raise RuntimeError(f"Steam Daily Art failed: {str(e)}")

    @classmethod
    def _metadata_fallback(cls, dimensions, item):
        """A light title card in pure inks for when no game art can be shown."""
        width, height = (int(value) for value in dimensions)
        image = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        margin = max(24, width // 20)
        band = max(56, height // 7)
        draw.rectangle((0, 0, width, band), fill=(0, 0, 255))
        draw.text(
            (margin, band // 2),
            "STEAM DAILY ART",
            font=cls._card_font(max(18, band * 2 // 5), bold=True),
            fill=(255, 255, 255),
            anchor="lm",
        )
        title = " ".join(str(item.get("name") or "Steam promotion").split())[:120]
        font, lines = cls._fit_card_title(draw, title, width - 2 * margin, max(40, height // 8))
        line_height = int(font.size * 1.18)
        y = band + margin
        for line in lines:
            draw.text((margin, y), line, font=font, fill=(0, 0, 0))
            y += line_height
        draw.rectangle((margin, y + 14, margin + max(48, width // 7), y + 24), fill=(255, 0, 0))
        draw.text(
            (margin, height - margin),
            "Store art is unavailable right now",
            font=cls._card_font(max(16, height // 22)),
            fill=(0, 0, 0),
            anchor="ls",
        )
        image.info["inkypi_skip_cache"] = True
        return attach_source_provenance(image, SourceProvenance.LOCAL_FALLBACK)

    @staticmethod
    def _card_font(size, bold=False):
        font = get_font("Jost", int(size), "bold" if bold else "normal")
        return font if font is not None else ImageFont.load_default(int(size))

    @classmethod
    def _fit_card_title(cls, draw, title, max_width, start_size, max_lines=3):
        """The largest title size that wraps into max_lines; the last size truncates."""
        for size in range(int(start_size), 23, -4):
            font = cls._card_font(size, bold=True)
            lines = cls._wrap_words(draw, title, font, max_width)
            if len(lines) <= max_lines:
                return font, lines
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "...", font=font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "..."
        return font, lines

    @staticmethod
    def _wrap_words(draw, text, font, max_width):
        lines = []
        line = ""
        for word in text.split(" "):
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = word
            # Titles without spaces (CJK, long compounds) break between characters.
            while len(line) > 1 and draw.textlength(line, font=font) > max_width:
                cut = len(line) - 1
                while cut > 1 and draw.textlength(line[:cut], font=font) > max_width:
                    cut -= 1
                lines.append(line[:cut])
                line = line[cut:]
        if line:
            lines.append(line)
        return lines

    def _generate_stateless_preview(self, settings, device_config):
        dimensions = tuple(int(value) for value in self.get_dimensions(device_config))
        for item in self._items_without_state(settings)[:MAX_ART_GAMES]:
            try:
                return self._render_item(item, settings, dimensions)[2]
            except _UnusableArtwork as exc:
                logger.info("%s; trying the next Steam game", exc)
        raise RuntimeError("No Steam promotional art is fit for the display.")

    def _generate_banked_image(self, settings, device_config):
        deadline = time.monotonic() + MAX_DATA_SECONDS
        previous_deadline = getattr(self, "_active_data_deadline", None)
        self._active_data_deadline = deadline
        try:
            return self._generate_banked_image_with_deadline(
                settings,
                device_config,
                deadline,
            )
        finally:
            self._active_data_deadline = previous_deadline

    def _generate_theme_only_banked_image(self, settings, device_config):
        state_path = self._presentation_state_path(create=False)
        if not state_path.exists():
            raise RuntimeError("Steam theme-only presentation bank is cold")
        dimensions = tuple(int(value) for value in self.get_dimensions(device_config))
        rotation_key = self._bank_rotation_key(device_config, settings)
        bank = self._presentation_bank(
            settings,
            dimensions,
            rotation_key,
            read_only=True,
        )
        _document, profile = bank.load_warm()
        selected = bank.selection_records_read_only(
            profile,
            profile.get("current_selection"),
        )
        image = selected[0][1].convert("RGB")
        return attach_source_provenance(image, SourceProvenance.FRESH_CACHE)

    def _generate_banked_image_with_deadline(self, settings, device_config, deadline):
        dimensions = tuple(int(value) for value in self.get_dimensions(device_config))
        rotation_key = self._bank_rotation_key(device_config, settings)
        bank = self._presentation_bank(settings, dimensions, rotation_key)
        document, profile = bank.load_for_data()

        for protected in bank.protected_records(profile):
            try:
                bank.load_media(protected)
            except RuntimeError as media_error:
                try:
                    self._check_data_deadline(deadline)
                    recovered = self._download_image(protected["image_url"])
                    self._check_data_deadline(deadline)
                    bank.recover_media(profile, protected, recovered)
                except Exception as recovery_error:
                    raise RuntimeError("Steam protected media recovery failed") from recovery_error
                logger.info("Recovered protected Steam media after: %s", media_error)

        ready = bank.ready_records(profile, prune=True)
        force_refresh = self._settings_enabled(settings.get("forceRefresh")) or self._settings_enabled(
            settings.get("force_refresh")
        )
        downloaded = 0
        if len(ready) < REFILL_THRESHOLD:
            profile["refill_in_progress"] = True
        if force_refresh or (profile.get("refill_in_progress") is True and len(ready) < READY_TARGET):
            self._check_data_deadline(deadline)
            data = self._fetch_featured_categories(settings)
            self._check_data_deadline(deadline)
            items = self._collect_items(data, settings.get("sourceCategory", "fresh_frontpage"))
            if not items:
                raise RuntimeError("No Steam promotional items found.")
            existing = {record["artwork_id"] for record in profile["records"]}
            attempts = 0
            for item in items:
                if attempts >= MAX_DATA_ATTEMPTS or downloaded >= MAX_DATA_NEW_MEDIA:
                    break
                identity = self._item_identity(item)
                if identity in existing:
                    continue
                attempts += 1
                try:
                    self._check_data_deadline(deadline)
                    image_url, _logo_url, rendered = self._render_item(item, settings, dimensions)
                    self._check_data_deadline(deadline)
                    record = bank.ingest(
                        profile,
                        {**item, "image_url": image_url, "artwork_id": identity},
                        rendered,
                    )
                except Exception as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Steam DATA deadline is exhausted") from exc
                    logger.warning("Steam bank candidate failed for %s: %s", identity, exc)
                    continue
                existing.add(record["artwork_id"])
                ready.append(record)
                downloaded += 1
        profile["refill_in_progress"] = len(ready) < READY_TARGET
        bank.cleanup(document, profile)
        ready = bank.ready_records(profile, prune=True)
        if not ready:
            raise RuntimeError("Steam presentation bank is unavailable")
        preview = {
            "record_keys": [ready[0]["record_key"]],
            "request_id": None,
            "date_key": rotation_key,
            "layout": "single",
            "reset_seen": False,
        }
        image = self._render_bank_selection(bank, profile, preview)
        return attach_source_provenance(
            image,
            SourceProvenance.LIVE if downloaded else SourceProvenance.FRESH_CACHE,
        )

    def _render_item(self, item, settings, dimensions):
        """Compose one game's art; its logo only ever sits on that game's own scene.

        Returns (image_url, logo_url, image). Raises _UnusableArtwork when every
        candidate is missing or too dark, so the caller can move to another game.
        """
        wants_logo = settings.get("logoOverlay", "show") != "hide"
        logo_url, logo = self._download_first_available_logo(item) if wants_logo else (None, None)
        # A bare scene without its logo would not say which game it is, so the
        # store capsule, whose art carries the title, goes first then.
        scenes_first = settings.get("imageMode", "library_hero") not in {"header", "large_capsule"} and (
            logo is not None or not wants_logo
        )
        tiers = (ART_SCENE, ART_TITLED) if scenes_first else (ART_TITLED, ART_SCENE)
        image_url, image, art_kind = self._choose_artwork(item, settings, dimensions, tiers)
        if logo is not None and art_kind == ART_SCENE:
            image = self._overlay_logo(image, logo, settings)
        else:
            logo_url = None
        if settings.get("showCaption") == "true":
            image = self._add_caption(image, item.get("name", "Steam"))
        return image_url, logo_url, image

    def _choose_artwork(self, item, settings, dimensions, tiers):
        """The first lit art of this game, tier by tier: (url, fitted image, tier)."""
        search = _ArtSearch(self._app_id(item), lambda appid: self._fetch_store_assets(appid, settings))
        tried = set()
        for tier in tiers:
            urls = self._scene_urls(search) if tier == ART_SCENE else self._titled_urls(item, settings, search)
            for url in urls:
                if not url or url.split("?", 1)[0] in tried:
                    continue
                tried.add(url.split("?", 1)[0])
                source = self._download_artwork(url, search)
                if source is None:
                    continue
                image = self._fit_artwork(source, dimensions, tier)
                # A scene is judged by the crop it shows; titled art by its own
                # pixels, since a contain canvas would pad it with light backdrop.
                if self._is_lit(self._scene_light(image if tier == ART_SCENE else source, dimensions)):
                    return url, image, tier
                logger.info("Steam art is too dark for the e-paper: %s", url)
            if tier == ART_SCENE:
                screenshot = self._best_screenshot(search, dimensions)
                if screenshot is not None:
                    return (*screenshot, ART_SCENE)
        name = item.get("name") or self._item_identity(item)
        if search.download_failed:
            raise RuntimeError(f"Steam art for {name} could not be downloaded")
        raise _UnusableArtwork(f"No Steam art for {name} is fit for the display")

    def _scene_urls(self, search):
        """Textless key art the logo can sit on; legacy names need no store lookup."""
        if search.appid is None:
            return
        yield self._steam_app_image(search.appid, "library_hero")
        yield search.assets().get("library_hero")

    def _titled_urls(self, item, settings, search):
        """Store capsules and headers, whose art already carries the game's title."""
        headers_first = settings.get("imageMode") == "header"
        if headers_first:
            yield from self._header_urls(item, search)
        if search.appid is not None:
            yield search.assets().get("main_capsule_2x")
        yield item.get("large_capsule_image")
        yield self._steam_app_image(search.appid, "capsule_616x353")
        if not headers_first:
            yield from self._header_urls(item, search)

    def _header_urls(self, item, search):
        yield item.get("header_image")
        yield self._steam_app_image(search.appid, "header")

    def _best_screenshot(self, search, dimensions):
        """The brightest lit screenshot among the store's first few, at full size."""
        ranked = []
        for full_url, thumbnail_url in search.assets().get("screenshots", [])[:MAX_SCENE_SCREENSHOTS]:
            thumbnail = self._download_artwork(thumbnail_url, search)
            if thumbnail is None:
                continue
            light = self._scene_light(thumbnail, dimensions)
            if self._is_lit(light):
                brightness, dark_share = light
                ranked.append((brightness * (1 - dark_share), full_url))
        for _score, url in sorted(ranked, reverse=True):
            source = self._download_artwork(url, search)
            if source is None:
                continue
            image = self._smart_cover(source, dimensions)
            if self._is_lit(self._scene_light(image, dimensions)):
                return url, image
        return None

    def _download_artwork(self, url, search):
        if self._optional_media_negative_hit(url):
            return None
        try:
            return self._download_image(url)
        except Exception as e:
            if self._is_not_found_error(e):
                self._remember_optional_media_negative(url)
                logger.info("Optional Steam image candidate is unavailable: %s", url)
            else:
                search.download_failed = True
                logger.warning(f"Steam image candidate failed: {url} | {e}")
            return None

    def _fit_artwork(self, image, dimensions, tier):
        target_width, target_height = dimensions
        if tier == ART_TITLED and image.width / image.height > target_width / target_height * 1.12:
            # Cropping a wide header to the display would cut its painted title.
            return self._contain_on_canvas(image, dimensions)
        return self._smart_cover(image, dimensions)

    @staticmethod
    def _scene_light(image, dimensions):
        """(mean brightness, dark share) of the image as cropped for the display."""
        width, height = dimensions
        sample = ImageOps.fit(
            image.convert("RGB"),
            (160, max(1, round(160 * height / width))),
            Image.Resampling.BILINEAR,
        )
        red, green, blue = sample.split()
        brightness = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        dark = sum(brightness.histogram()[:SCENE_DARK_LEVEL])
        return ImageStat.Stat(brightness).mean[0], dark / (sample.width * sample.height)

    @staticmethod
    def _is_lit(light):
        brightness, dark_share = light
        return brightness >= SCENE_MIN_BRIGHTNESS and dark_share <= SCENE_MAX_DARK_SHARE

    def _items_without_state(self, settings):
        data = self._fetch_featured_categories(settings)
        items = self._collect_items(data, settings.get("sourceCategory", "fresh_frontpage"))
        if not items:
            raise RuntimeError("No Steam promotional items found.")
        return items

    def _check_data_deadline(self, deadline):
        if time.monotonic() >= deadline:
            raise RuntimeError("Steam DATA deadline is exhausted")

    def _bank_rotation_key(self, device_config, settings):
        now = self._now_for_device(device_config)
        cadence = str((settings or {}).get("rotationCadence") or "hourly").strip().lower()
        if cadence in {"six_hour", "six_hours"}:
            return f"{now.strftime('%Y-%m-%d')}-slot-{now.hour // 6}"
        if cadence == "daily":
            return now.strftime("%Y-%m-%d")
        return now.strftime("%Y-%m-%d-%H")

    def _presentation_state_path(self, *, create=True):
        return Path(self._cache_dir(create=create)) / "presentation-state.json"

    def _presentation_media_dir(self, *, create=True):
        return Path(self._cache_dir(create=create)) / "presentation-media"

    def _presentation_bank(self, settings, dimensions, rotation_key, *, read_only=False):
        instance_uuid = get_presentation_instance_uuid(settings or {})
        if instance_uuid is None:
            raise RuntimeError("Steam presentation bank requires trusted instance identity")
        base_fingerprint = settings_fingerprint(settings, dimensions, rotation_key)
        fingerprint = instance_profile_fingerprint(base_fingerprint, instance_uuid)
        return SteamDailyArtPresentationBank(
            self._presentation_state_path(create=not read_only),
            self._presentation_media_dir(create=not read_only),
            fingerprint=fingerprint,
            base_fingerprint=base_fingerprint,
            profile_settings_key=settings_key(settings),
            instance_uuid=instance_uuid,
            date_key=self._presentation_bucket_key(settings, rotation_key),
            selection_mode=self._effective_selection_mode(settings),
            source_rotation_key=rotation_key,
            read_only=read_only,
        )

    def _presentation_bank_for_receipt(self, instance_uuid, request_id):
        state_path = self._presentation_state_path()
        if not os.path.exists(state_path):
            return None
        document = read_bounded_json_object(state_path)
        for fingerprint, profile in (document.get("profiles") or {}).items():
            if not isinstance(profile, dict) or profile.get("instance_uuid") != instance_uuid:
                continue
            pending = profile.get("pending_selection")
            if not isinstance(pending, dict) or pending.get("request_id") != request_id:
                continue
            return SteamDailyArtPresentationBank(
                state_path,
                self._presentation_media_dir(),
                fingerprint=fingerprint,
                base_fingerprint=profile.get("settings_fingerprint"),
                profile_settings_key=profile.get("settings_key"),
                instance_uuid=instance_uuid,
                date_key=profile.get("date_key") or pending.get("date_key"),
                source_rotation_key=profile.get("date_key") or pending.get("date_key"),
            )
        return None

    @staticmethod
    def _render_bank_selection(bank, profile, selection):
        selected = bank.selection_records(profile, selection, load_media=True)
        image = selected[0][1].convert("RGB")
        return attach_source_provenance(image, SourceProvenance.FRESH_CACHE)

    @staticmethod
    def _cache_generated_at(entry):
        value = entry.get("generated_at") if isinstance(entry, dict) else None
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None

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
    def _context_entry_for_bank_record(record):
        artwork_id = str(record.get("artwork_id") or "")
        appid = artwork_id.removeprefix("app:") if artwork_id.startswith("app:") else None
        if appid is not None and appid.isdigit():
            appid = int(appid)
        return {
            "name": record.get("title") or "Steam promotion",
            "appid": appid,
            "rotation_key": str(
                record.get("source_rotation_key") or record.get("date_key") or ""
            )[:80],
            "image_url": record.get("image_url"),
        }

    @staticmethod
    def _effective_selection_mode(settings):
        mode = str((settings or {}).get("selectionMode") or "daily_rotation").strip().lower()
        if mode in {"", "current", "daily_rotation", "every_refresh"}:
            return "daily_rotation"
        if mode in {"first", "random"}:
            return mode
        return "daily_rotation"

    @staticmethod
    def _presentation_bucket_key(settings, rotation_key):
        cadence = str((settings or {}).get("rotationCadence") or "hourly").strip().lower()
        selection_mode = str((settings or {}).get("selectionMode") or "current").strip().lower()
        if cadence == "every_refresh" or selection_mode == "every_refresh":
            return "every-refresh-pool"
        return str(rotation_key)

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

    def _cache_dir(self, *, create=True):
        return self.cache_dir(leaf=".steam_daily_art_cache", create=create)

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
        selection_mode = str(settings.get("selectionMode") or "current").strip().lower()
        if selection_mode == "daily_rotation":
            selection_mode = "current"
        parts = [
            STEAM_DAILY_ART_VERSION,
            STEAM_DAILY_ART_RENDER_VERSION,
            rotation_key,
            str(dimensions),
            settings.get("sourceCategory", "fresh_frontpage"),
            selection_mode,
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

    @staticmethod
    def _store_locale(settings):
        country_code = (settings.get("countryCode") or "US").strip().upper()[:2]
        language = (settings.get("language") or "english").strip() or "english"
        return country_code, language

    def _fetch_featured_categories(self, settings):
        country_code, language = self._store_locale(settings)
        params = {"cc": country_code, "l": language}
        url = f"{STEAM_FEATURED_CATEGORIES_URL}?{urlencode(params)}"
        return self._fetch_json(url, label="featured", max_bytes=MAX_JSON_BYTES)

    def _fetch_store_assets(self, appid, settings):
        """This app's current store art: hashed hero and capsule paths, screenshots."""
        country_code, language = self._store_locale(settings)
        query = {
            "ids": [{"appid": int(appid)}],
            "context": {"language": language, "country_code": country_code},
            "data_request": {"include_assets": True, "include_screenshots": True},
        }
        params = {"input_json": json.dumps(query, separators=(",", ":"))}
        url = f"{STEAM_STORE_ITEMS_URL}?{urlencode(params)}"
        payload = self._fetch_json(url, label="store item", max_bytes=MAX_STORE_ITEM_BYTES)
        return self._parse_store_assets(payload, appid)

    @staticmethod
    def _parse_store_assets(payload, appid):
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        store_items = response.get("store_items") if isinstance(response.get("store_items"), list) else []
        entry = next(
            (
                item
                for item in store_items
                if isinstance(item, dict) and str(item.get("appid")) == str(appid)
            ),
            None,
        )
        if entry is None or entry.get("success") != 1:
            return {}
        # Every path must name this app, so no scene can come from another game.
        prefix = f"steam/apps/{appid}/"
        found = {}
        assets = entry.get("assets") if isinstance(entry.get("assets"), dict) else {}
        url_format = str(assets.get("asset_url_format") or "")
        if url_format.startswith(prefix) and "${FILENAME}" in url_format and ".." not in url_format:
            for key in ("library_hero", "main_capsule_2x", "header"):
                filename = assets.get(key)
                if isinstance(filename, str) and ASSET_FILENAME_RE.fullmatch(filename) and ".." not in filename:
                    found[key] = STEAM_STORE_ASSET_ROOT + url_format.replace("${FILENAME}", filename)
        screenshots = entry.get("screenshots") if isinstance(entry.get("screenshots"), dict) else {}
        found["screenshots"] = []
        # Mature screenshots sit behind Steam's age gate; only all-ages ones are used.
        shots = screenshots.get("all_ages_screenshots")
        for shot in shots if isinstance(shots, list) else []:
            filename = shot.get("filename") if isinstance(shot, dict) else None
            path, _, query = str(filename or "").partition("?")
            if not path.startswith(prefix) or not path.endswith(".jpg") or ".." in path:
                continue
            suffix = f"?{query}" if query else ""
            found["screenshots"].append((
                STEAM_STORE_ASSET_ROOT + path + suffix,
                STEAM_STORE_ASSET_ROOT + path[:-len(".jpg")] + ".600x338.jpg" + suffix,
            ))
        return found

    def _fetch_json(self, url, *, label, max_bytes):
        deadline = getattr(self, "_active_data_deadline", None)
        approved = get_ssrf_policy().resolve_and_validate(url)
        self._validate_steam_target(approved, kind="json")
        response = self._request_approved_target(
            approved,
            headers={"Accept": "application/json", "User-Agent": "InkyPi SteamDailyArt/1.0"},
            timeout=20,
            deadline=deadline,
        )
        try:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > max_bytes:
                raise RuntimeError(f"Steam {label} response exceeds its object budget")
            payload = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                if deadline is not None:
                    self._check_data_deadline(deadline)
                if not chunk:
                    continue
                if len(payload) + len(chunk) > max_bytes:
                    raise RuntimeError(f"Steam {label} response exceeds its object budget")
                payload.extend(chunk)
        finally:
            response.close()
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Steam {label} response is not JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"Steam {label} response has an invalid shape")
        return value

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
                if not (item.get("id") or item.get("appid")):
                    # Spotlights carry no id, only a link to the game they promote.
                    linked = STORE_APP_LINK_RE.match(str(item.get("url") or ""))
                    if linked:
                        item["id"] = int(linked.group(1))
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
            key = self._item_identity(item)
            if key in seen:
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

    def _select_item(self, settings, today_key, exclude=frozenset()):
        source_category = settings.get("sourceCategory", "fresh_frontpage")
        data = self._fetch_featured_categories(settings)
        items = [
            item
            for item in self._collect_items(data, source_category)
            if self._item_identity(item) not in exclude
        ]
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
        del pool_ids
        if selection_mode in {None, "", "daily_rotation"}:
            selection_mode = "current"
        parts = [
            STEAM_DAILY_ART_VERSION,
            source_category,
            selection_mode,
            settings.get("countryCode", "US"),
            settings.get("language", "english"),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _item_kind(item):
        try:
            item_type = int(item.get("type") or 0)
        except (TypeError, ValueError):
            return "other"
        return STEAM_ITEM_KINDS.get(item_type, "other")

    def _item_identity(self, item):
        appid = item.get("id")
        if appid is None or not str(appid).strip():
            appid = item.get("appid")
        if appid is not None and str(appid).strip():
            return f"{self._item_kind(item)}:{appid}"
        image = item.get("large_capsule_image") or item.get("header_image") or item.get("small_capsule_image")
        if image:
            return f"image:{image}"
        return f"name:{item.get('name', '')}"

    def _app_id(self, item):
        """The Steam app behind an item; bundle and package ids name other apps."""
        if self._item_kind(item) != "app":
            return None
        appid = str(item.get("id") or item.get("appid") or "").strip()
        return appid if appid.isdecimal() else None

    def _steam_app_image(self, appid, image_name):
        if not appid:
            return None
        return STEAM_CDN_APP_URL.format(appid=appid, image_name=image_name)

    def _steam_app_asset(self, appid, filename):
        if not appid:
            return None
        return STEAM_CDN_ASSET_URL.format(appid=appid, filename=filename)

    def _logo_candidate_urls(self, item):
        appid = self._app_id(item)
        return [
            item.get("logo"),
            item.get("logo_url"),
            self._steam_app_asset(appid, "logo.png"),
        ]

    def _download_first_available_logo(self, item):
        for url in self._logo_candidate_urls(item):
            if not url:
                continue
            if self._optional_media_negative_hit(url):
                continue
            try:
                return url, self._download_logo(url)
            except Exception as e:
                if self._is_not_found_error(e):
                    self._remember_optional_media_negative(url)
                    logger.info("Optional Steam logo candidate is unavailable: %s", url)
                elif isinstance(e, _OptionalMediaUnavailable):
                    # The durable retry marker bounds the next attempt, and the
                    # failure that wrote it was reported when it happened.
                    logger.info("Optional Steam logo candidate is in retry backoff: %s", url)
                else:
                    logger.warning(f"Steam logo candidate failed: {url} | {e}")

        logger.info("No Steam logo overlay found for selected item.")
        return None, None

    @staticmethod
    def _is_not_found_error(error):
        status_code = getattr(error, "status_code", None)
        if status_code is not None:
            try:
                return int(status_code) == 404
            except (TypeError, ValueError):
                pass
        return bool(re.search(r"\bstatus\s+404\b", str(error), flags=re.IGNORECASE))

    def _optional_media_negative_hit(self, url):
        now = float(getattr(self, "_optional_media_clock", time.monotonic)())
        cache = getattr(self, "_optional_media_negative_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._optional_media_negative_cache = cache
        expires_at = float(cache.get(str(url), 0.0) or 0.0)
        if expires_at > now:
            return True
        cache.pop(str(url), None)
        return False

    def _remember_optional_media_negative(self, url):
        now = float(getattr(self, "_optional_media_clock", time.monotonic)())
        cache = getattr(self, "_optional_media_negative_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._optional_media_negative_cache = cache
        if len(cache) >= OPTIONAL_MEDIA_NEGATIVE_CACHE_LIMIT:
            oldest = min(cache, key=cache.get)
            cache.pop(oldest, None)
        cache[str(url)] = now + OPTIONAL_MEDIA_NEGATIVE_TTL_SECONDS

    def _download_image(self, url):
        payload = self._download_media_bytes(url, max_bytes=MAX_MEDIA_BYTES, timeout=40)
        return safe_open_image(
            BytesIO(payload),
            limits=ImageLimits(
                max_bytes=MAX_MEDIA_BYTES,
                max_width=8192,
                max_height=8192,
                max_pixels=32_000_000,
            ),
        ).convert("RGB")

    def _download_logo(self, url):
        path = Path(self._cache_dir()) / ("logo_" + hashlib.sha256(url.encode("utf-8")).hexdigest() + ".png")
        # cached_resource_image keeps only a retry marker for a failed fetch;
        # remember the cause so a missing logo is not reported like a fault.
        attempt = {"started": False, "error": None}

        def fetch():
            attempt["started"] = True
            try:
                payload = self._download_media_bytes(url, max_bytes=MAX_MEDIA_BYTES, timeout=30)
                image = safe_open_image(BytesIO(payload), limits=ImageLimits(
                    max_bytes=MAX_MEDIA_BYTES, max_width=8192, max_height=8192,
                    max_pixels=32_000_000,
                )).convert("RGBA")
            except Exception as error:
                attempt["error"] = error
                raise
            image.thumbnail((1600, 900), Image.Resampling.LANCZOS)
            image.info["resource_downloaded_bytes"] = len(payload)
            return image

        image = cached_resource_image(path, fetch, ttl=14 * 24 * 3600, label="steam_logos")
        prune_resource_images(path.parent, prefixes=("logo_",), max_files=128,
                              max_bytes=24 * 1024 * 1024, max_age=60 * 24 * 3600,
                              label="steam_logos", protected=(path,))
        if image is not None:
            return image
        # Drop the stored exception so its traceback (and the payload held by
        # the fetch frame) is not kept alive by this closure state.
        error = attempt.pop("error", None)
        if not attempt["started"]:
            raise _OptionalMediaUnavailable("Optional Steam logo is in retry backoff")
        if error is not None and self._is_not_found_error(error):
            raise _OptionalMediaUnavailable(
                "Optional Steam logo is missing upstream",
                status_code=404,
            ) from error
        detail = f"{type(error).__name__}: {error}" if error is not None else "invalid media"
        raise RuntimeError(f"Optional Steam logo download failed: {detail}") from error

    def _download_media_bytes(self, url, *, max_bytes, timeout):
        policy = get_ssrf_policy()
        current_url = str(url or "").strip()
        deadline = getattr(self, "_active_data_deadline", None)
        for redirect_count in range(MAX_MEDIA_REDIRECTS + 1):
            approved = policy.resolve_and_validate(current_url)
            self._validate_steam_target(approved, kind="media")
            response = self._request_approved_target(
                approved,
                headers={"Referer": "https://store.steampowered.com/", "User-Agent": "InkyPi SteamDailyArt/1.0"},
                timeout=timeout,
                deadline=deadline,
            )
            try:
                status = int(response.status_code)
                if 300 <= status < 400:
                    if redirect_count >= MAX_MEDIA_REDIRECTS:
                        raise RuntimeError("Steam media redirect limit was exceeded")
                    location = str(response.headers.get("Location") or "").strip()
                    if not location:
                        raise RuntimeError("Steam media redirect has no Location")
                    next_url = urljoin(approved.normalized_url, location)
                    next_target = policy.resolve_and_validate(next_url)
                    current_url = self._validate_steam_target(next_target, kind="media")
                    continue
                if not 200 <= status < 300:
                    raise RuntimeError(f"Steam media request failed with status {status}")
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > max_bytes:
                    raise RuntimeError("Steam media response exceeds its object budget")
                payload = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    if deadline is not None:
                        self._check_data_deadline(deadline)
                    if not chunk:
                        continue
                    if len(payload) + len(chunk) > max_bytes:
                        raise RuntimeError("Steam media response exceeds its object budget")
                    payload.extend(chunk)
                if not payload:
                    raise RuntimeError("Steam media response is empty")
                return bytes(payload)
            finally:
                response.close()
        raise RuntimeError("Steam media redirect limit was exceeded")

    def _request_approved_target(self, approved, *, headers, timeout, deadline):
        hostname = str(getattr(approved, "hostname", "") or "").lower().rstrip(".")
        kind = "json" if hostname in STEAM_JSON_HOSTS else "media"
        self._validate_steam_target(approved, kind=kind)
        return _PinnedHTTPSResponse.open(
            approved,
            headers=headers,
            deadline=deadline,
            clock=time.monotonic,
            timeout=timeout,
        )

    @staticmethod
    def _validate_steam_target(approved, *, kind):
        if getattr(approved, "scheme", None) != "https" or getattr(approved, "port", None) != 443:
            raise RuntimeError("Steam target must use HTTPS on port 443")
        hostname = str(getattr(approved, "hostname", "") or "").lower().rstrip(".")
        if kind == "json":
            allowed = hostname in STEAM_JSON_HOSTS
        else:
            allowed = any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in STEAM_MEDIA_HOST_SUFFIXES)
        if not allowed:
            raise RuntimeError("Steam target authority is not allowed")
        addresses = getattr(approved, "addresses", None)
        if not isinstance(addresses, (tuple, list)) or not addresses:
            raise RuntimeError("Steam target has no approved public addresses")
        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(str(raw_address))
            except ValueError as exc:
                raise RuntimeError("Steam target contains an invalid approved address") from exc
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                raise RuntimeError("Steam target IPv4-mapped addresses are not allowed")
            if not address.is_global:
                raise RuntimeError("Steam target address must be public")
        return approved.normalized_url

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

        # A solid black or white mat, matched to the art's edges, prints cleanly;
        # a pale blurred backdrop would dither into noise on the e-paper.
        edge_mask = Image.new("L", fitted.size, 255)
        edge_mask.paste(0, (4, 4, max(4, new_width - 4), max(4, new_height - 4)))
        edge_light = ImageStat.Stat(fitted.convert("L"), mask=edge_mask).mean[0]
        canvas = Image.new("RGB", (target_width, target_height), (0, 0, 0) if edge_light < 128 else (255, 255, 255))

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
        # Share of the canvas area, so square marks are not shrunk like wide
        # wordmarks, then width and height caps.
        scale_map = {
            "compact": (0.045, 0.34, 0.26),
            "normal": (0.07, 0.46, 0.34),
            "large": (0.10, 0.58, 0.42),
        }
        area_share, max_width_factor, max_height_factor = scale_map.get(size_name, scale_map["normal"])

        scale = min(
            (image.width * image.height * area_share / (logo.width * logo.height)) ** 0.5,
            image.width * max_width_factor / logo.width,
            image.height * max_height_factor / logo.height,
            1.0,
        )
        new_size = (max(1, int(logo.width * scale)), max(1, int(logo.height * scale)))
        logo = logo.resize(new_size, Image.Resampling.LANCZOS)

        x, y = self._logo_position(
            image.size,
            logo.size,
            settings.get("logoPosition", "empty_space"),
            image=image,
        )

        composed = image.convert("RGBA")
        alpha = logo.getchannel("A")
        halo_ink = self._logo_halo_ink(image, logo, (x, y))
        if halo_ink is not None:
            # A crisp pure-ink outline keeps a logo readable on art of its own
            # brightness; a soft shadow would dither into noise there.
            pad = LOGO_HALO_WIDTH
            solid = alpha.point(lambda value: 255 if value >= 96 else 0)
            halo_alpha = ImageOps.expand(solid, border=pad, fill=0).filter(ImageFilter.MaxFilter(pad * 2 + 1))
            halo = Image.new("RGBA", halo_alpha.size, halo_ink)
            halo.putalpha(halo_alpha)
            composed.alpha_composite(halo, (x - pad, y - pad))
        else:
            shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
            shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(radius=3))
            shadow.putalpha(shadow_alpha.point(lambda value: int(value * 0.55)))
            composed.alpha_composite(shadow, (x + 3, y + 3))
        composed.alpha_composite(logo, (x, y))

        return composed.convert("RGB")

    @staticmethod
    def _logo_halo_ink(image, logo, position):
        """Black or white outline ink when logo and backdrop share a brightness."""
        x, y = position
        backdrop = image.convert("L").crop((x, y, x + logo.width, y + logo.height))
        mask = logo.getchannel("A").point(lambda value: 255 if value >= 96 else 0)
        if mask.getbbox() is None:
            return None
        logo_light = ImageStat.Stat(logo.convert("L"), mask=mask).mean[0]
        backdrop_light = ImageStat.Stat(backdrop, mask=mask).mean[0]
        if abs(logo_light - backdrop_light) >= LOGO_MIN_CONTRAST:
            return None
        return (0, 0, 0, 255) if backdrop_light >= 128 else (255, 255, 255, 255)

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
