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
from plugins.steam_daily_art import shelf as info_shelf
from runtime.refresh_contracts import TaskCancelled
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
# steamcommunity.com serves a studio's (creator clan's) profile, whose avatar is its logo.
STEAM_JSON_HOSTS = ("store.steampowered.com", "api.steampowered.com", "steamcommunity.com")
STEAM_DAILY_ART_VERSION = "fresh-frontpage-ranked-live-list-v1"
STEAM_DAILY_ART_RENDER_VERSION = "info-shelf-personal-v3"
# The info shelf and the owner's Steam account (wishlist, library): Chinese store facts,
# each cached for its own while.
STEAM_WISHLIST_URL = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"
STEAM_OWNED_GAMES_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
STEAM_TAG_LIST_URL = "https://api.steampowered.com/IStoreService/GetTagList/v1/"
SHELF_LANGUAGE = "schinese"
# Store facts cached under an older shape are fetched again.
SHELF_DETAILS_VERSION = 2
# A studio's logo: its creator clan's Steam avatar, from the clan's public profile.
STEAM_CLAN_XML_URL = "https://steamcommunity.com/gid/{gid}/memberslistxml/?xml=1"
CLAN_STEAMID_BASE = 103582791429521408
STUDIO_AVATAR_RE = re.compile(
    r"<avatarFull>\s*(?:<!\[CDATA\[)?\s*(https://avatars\.(?:[a-z]+\.)?steamstatic\.com/[0-9a-f]{40}_full\.jpg)"
)
STUDIO_LOGO_TTL = 30 * 24 * 3600
STUDIO_LOGO_SIZE = 48
MAX_STUDIO_PAGE_BYTES = 512 * 1024
MAX_STUDIOS = 300
MAX_NEW_STUDIO_LOGOS = 4
# Steam Deck compatibility categories worth a word.
DECK_LABELS = {3: "Steam Deck 已验证", 2: "Steam Deck 可玩"}
SHELF_DETAILS_TTL = 6 * 3600
WISHLIST_TTL = 6 * 3600
WISHLIST_DEALS_TTL = 3 * 3600
OWNED_TTL = 24 * 3600
TAG_NAMES_TTL = 30 * 24 * 3600
STORE_ITEMS_BATCH = 100
MAX_WISHLIST_ITEMS = 1000
MAX_WISHLIST_DEALS = 24
MAX_SHELF_DETAILS = 400
MAX_BLURBS = 400
MAX_SHELF_CACHE_BYTES = 2 * 1024 * 1024
NEW_RELEASE_DAYS = 14
# Steam's English blurbs become one Chinese line with the model the news brief uses.
BLURB_MODEL = "gpt-5-mini"
BLURB_TIMEOUT_SECONDS = 30
BLURB_MAX_CHARS = 36
BLURB_INSTRUCTIONS = (
    "输入是 Steam 游戏的英文简介列表。把每条改写成一句简体中文介绍：说清楚这是什么游戏、怎么玩，"
    f"不超过 {BLURB_MAX_CHARS - 4} 个汉字，不要引号、书名号和句末标点。"
    "按 JSON 返回：lines 里每条一项，appid 原样照抄，line 是这句中文。"
)
BLURB_MAX_OUTPUT_TOKENS = 3000
BLURB_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"appid": {"type": "string"}, "line": {"type": "string"}},
                "required": ["appid", "line"],
            },
        },
    },
    "required": ["lines"],
}
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


def _has_cjk(text):
    """Whether the text has Chinese characters (the CJK unified ideographs)."""
    return any(0x4E00 <= ord(char) <= 0x9FFF for char in str(text or ""))


def _first_sentence(text, limit=90):
    """The description's first sentence (when it is not too short), cut to ``limit`` characters,
    an English one at a word."""
    text = " ".join(str(text or "").split())
    ends = [index for index in (text.find(mark) for mark in ("。", "！", "？", ". ", "! ", "? ")) if index >= 8]
    if ends:
        text = text[:min(ends)]
    if len(text) > limit:
        cut = text[:limit]
        text = cut if _has_cjk(cut) or " " not in cut else cut.rsplit(" ", 1)[0]
    return text.rstrip(".。")


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
        # The shelf's keys (Steam, OpenAI) and the device's clock come from its config.
        previous = getattr(self, "_active_device_config", None)
        self._active_device_config = device_config
        try:
            return self._generate_image(settings, device_config)
        finally:
            self._active_device_config = previous

    def _generate_image(self, settings, device_config):
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
            items = self._personalize(self._collect_items(data, settings.get("sourceCategory", "fresh_frontpage")), settings)
            if not items:
                raise RuntimeError("No Steam promotional items found.")
            existing = {record["artwork_id"] for record in profile["records"]}
            # The shelf's facts for every candidate in one store lookup (and one AI call).
            self._prepare_shelf(
                [item for item in items if self._item_identity(item) not in existing][:MAX_DATA_ATTEMPTS],
                settings,
            )
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
        info = self._shelf_info(item, settings)
        band = None
        if info is not None:
            # A flat block too short for every row leaves the shelf to the band fading in over
            # the art's foot; the art fills the page either way.
            band = info_shelf.band_fits(image.height, info_shelf.find_flat_band(image), info)
        if logo is not None and art_kind == ART_SCENE:
            if info is not None:
                # The logo finds its place above the shelf.
                top = info_shelf.shelf_top(image.height, band)
                upper = self._overlay_logo(image.crop((0, 0, image.width, top)), logo, settings)
                image = image.copy()
                image.paste(upper, (0, 0))
            else:
                image = self._overlay_logo(image, logo, settings)
        else:
            logo_url = None
        if info is not None:
            image = info_shelf.draw_shelf(image, info, band)
        elif settings.get("showCaption") == "true":
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
                image = self._fit_artwork(source, dimensions)
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

    def _fit_artwork(self, image, dimensions):
        """The art cropped to fill the page (a wide header loses its sides, not the page its
        edges); only art far taller than the page is kept whole."""
        target_width, target_height = dimensions
        resample = Image.Resampling.LANCZOS
        grid = info_shelf.pixel_grid(image)
        if grid is not None:
            # Pixel art at its own grid, enlarged without smoothing so its pixels stay square.
            image, resample = info_shelf.to_native(image, grid), Image.Resampling.NEAREST
        elif max(target_width / image.width, target_height / image.height) > 1 and info_shelf.is_hard_edged(image):
            # Hard-edged art enlarged by an uneven factor still keeps its edges sharp.
            resample = Image.Resampling.NEAREST
        return self._smart_cover(image, dimensions, resample)

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
        items = self._personalize(self._collect_items(data, settings.get("sourceCategory", "fresh_frontpage")), settings)
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
            settings.get("infoShelf", "on"),
            settings.get("personalize", "on"),
            settings.get("steamId", ""),
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

    # The info shelf and the owner's Steam account ------------------------------------

    @staticmethod
    def _setting_on(settings, key):
        return str((settings or {}).get(key) or "on").strip().lower() not in {"off", "false", "hide", "0"}

    def _env_key(self, name):
        device_config = getattr(self, "_active_device_config", None)
        if device_config is not None and hasattr(device_config, "load_env_key"):
            return str(device_config.load_env_key(name) or "").strip()
        return os.getenv(name, "").strip()

    def _steam_id(self, settings):
        """The owner's 64-bit Steam id: the plugin's setting, else the Steam profile page's."""
        value = str((settings or {}).get("steamId") or "").strip()
        if not value:
            from plugins.steam_profile_dashboard.steam_profile_dashboard import DEFAULT_STEAM_ID

            value = DEFAULT_STEAM_ID
        return value if value.isdecimal() and len(value) == 17 else ""

    def _shelf_cache_path(self):
        return Path(self._cache_dir()) / "shelf-cache.json"

    def _read_shelf_cache(self):
        path = self._shelf_cache_path()
        try:
            if path.stat().st_size > MAX_SHELF_CACHE_BYTES:
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_shelf_cache(self, data):
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_SHELF_CACHE_BYTES:
            # The store facts are the bulk and the cheapest to fetch again.
            text = json.dumps({**data, "details": {}}, ensure_ascii=False, separators=(",", ":"))
        path = self._shelf_cache_path()
        temp = path.with_name(path.name + ".tmp")
        try:
            temp.write_text(text, encoding="utf-8")
            os.replace(temp, path)
        except OSError as exc:
            logger.warning("Steam shelf cache could not be written: %s", exc)

    def _cached_lookup(self, name, key, ttl, fetch):
        """A lookup kept in the shelf cache: the stored value while fresh for ``key``, else
        ``fetch()``'s; when that fails, the stored value (however old) or None."""
        entry = self._read_shelf_cache().get(name)
        entry = entry if isinstance(entry, dict) and entry.get("key") == key else {}
        if entry and time.time() - float(entry.get("at") or 0) < ttl:
            return entry.get("value")
        try:
            value = fetch()
        except TaskCancelled:
            raise
        except Exception as exc:
            # Only the kind of failure: the owned-games URL carries the Steam key.
            logger.warning("Steam %s lookup failed: %s", name, type(exc).__name__)
            return entry.get("value")
        cache = self._read_shelf_cache()
        cache[name] = {"key": key, "at": time.time(), "value": value}
        self._write_shelf_cache(cache)
        return value

    def _fetch_profile_json(self, url, label):
        """Every shelf and account lookup goes through here (or the page fetch below)."""
        return self._fetch_json(url, label=label, max_bytes=MAX_JSON_BYTES)

    def _fetch_profile_text(self, url, label):
        return self._fetch_bytes(url, label=label, max_bytes=MAX_STUDIO_PAGE_BYTES).decode("utf-8", "replace")

    def _store_items(self, appids, country, data_request):
        """GetItems for these apps with the store's simplified Chinese text."""
        query = {
            "ids": [{"appid": int(appid)} for appid in appids],
            "context": {"language": SHELF_LANGUAGE, "country_code": country},
            "data_request": data_request,
        }
        url = f"{STEAM_STORE_ITEMS_URL}?{urlencode({'input_json': json.dumps(query, separators=(',', ':'))})}"
        response = self._fetch_profile_json(url, "store items").get("response")
        items = response.get("store_items") if isinstance(response, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def _store_details(self, appids, settings):
        """The shelf's store facts per app id, those not fetched within SHELF_DETAILS_TTL asked
        for in one batch; older facts stand when the lookup fails."""
        country, _language = self._store_locale(settings)
        wanted = list(dict.fromkeys(str(appid) for appid in appids if str(appid or "").isdecimal()))
        cache = self._read_shelf_cache()
        details = cache.get("details") if isinstance(cache.get("details"), dict) else {}
        now = time.time()

        def fresh(appid):
            entry = details.get(appid)
            return (
                isinstance(entry, dict)
                and entry.get("country") == country
                and entry.get("v") == SHELF_DETAILS_VERSION
                and now - float(entry.get("at") or 0) < SHELF_DETAILS_TTL
            )

        stale = [appid for appid in wanted if not fresh(appid)]
        if stale:
            try:
                entries = self._store_items(
                    stale[:STORE_ITEMS_BATCH],
                    country,
                    {"include_basic_info": True, "include_tag_count": 6, "include_reviews": True, "include_release": True,
                     "include_platforms": True},
                )
            except TaskCancelled:
                raise
            except Exception as exc:
                logger.warning("Steam store details lookup failed: %s", type(exc).__name__)
            else:
                for entry in entries:
                    parsed = self._parse_store_details(entry)
                    if parsed is not None:
                        details[str(entry.get("appid"))] = {**parsed, "at": now, "country": country, "v": SHELF_DETAILS_VERSION}
                details = dict(sorted(details.items(), key=lambda pair: float(pair[1].get("at") or 0))[-MAX_SHELF_DETAILS:])
                cache = self._read_shelf_cache()
                cache["details"] = details
                self._write_shelf_cache(cache)
        return {appid: details[appid] for appid in wanted if isinstance(details.get(appid), dict)}

    @staticmethod
    def _parse_store_details(entry):
        if not isinstance(entry, dict) or entry.get("success") != 1:
            return None

        def number(value):
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        basic = entry.get("basic_info") if isinstance(entry.get("basic_info"), dict) else {}
        reviews = (entry.get("reviews") or {}).get("summary_filtered") if isinstance(entry.get("reviews"), dict) else None
        reviews = reviews if isinstance(reviews, dict) else {}
        release = entry.get("release") if isinstance(entry.get("release"), dict) else {}
        option = entry.get("best_purchase_option") if isinstance(entry.get("best_purchase_option"), dict) else {}
        developers = [item for item in basic.get("developers") or [] if isinstance(item, dict) and item.get("name")]
        publishers = [item for item in basic.get("publishers") or [] if isinstance(item, dict) and item.get("name")]
        made_by = {str(item["name"]).strip() for item in developers}
        tags = entry.get("tags") if isinstance(entry.get("tags"), list) else []
        platforms = entry.get("platforms") if isinstance(entry.get("platforms"), dict) else {}
        review_count = number(reviews.get("review_count"))
        return {
            "name": " ".join(str(entry.get("name") or "").split())[:120],
            "description": " ".join(str(basic.get("short_description") or "").split())[:400],
            "developer": (str(developers[0]["name"]).strip() if developers else "")[:60],
            "developer_clan": str(developers[0].get("creator_clan_account_id") or "") if developers else "",
            "publisher": next(
                (str(item["name"]).strip() for item in publishers if str(item["name"]).strip() not in made_by), ""
            )[:60],
            "deck": number(platforms.get("steam_deck_compat_category")),
            "tags": [str(tag["tagid"]) for tag in tags if isinstance(tag, dict) and tag.get("tagid")][:6],
            "review_label": str(reviews.get("review_score_label") or "")[:20] if review_count else "",
            "review_percent": number(reviews.get("percent_positive")),
            "review_count": review_count,
            "release": number(release.get("steam_release_date")),
            "coming_soon": bool(release.get("is_coming_soon")),
            "free": bool(entry.get("is_free")),
            "discount": 0 if option.get("hide_discount_pct_for_compliance") else number(option.get("discount_pct")),
            "final": str(option.get("formatted_final_price") or "")[:24],
            "original": str(option.get("formatted_original_price") or "")[:24],
        }

    def _tag_names(self):
        """Steam's tag names in simplified Chinese by tag id."""

        def fetch():
            payload = self._fetch_profile_json(f"{STEAM_TAG_LIST_URL}?{urlencode({'language': SHELF_LANGUAGE})}", "tag list")
            response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
            tags = response.get("tags") if isinstance(response.get("tags"), list) else []
            return {
                str(tag["tagid"]): str(tag["name"])[:16]
                for tag in tags
                if isinstance(tag, dict) and tag.get("tagid") and tag.get("name")
            }

        names = self._cached_lookup("tag_names", SHELF_LANGUAGE, TAG_NAMES_TTL, fetch)
        return names if isinstance(names, dict) else {}

    def _wishlist(self, settings):
        """The owner's public wishlist: {app id: priority}."""
        steam_id = self._steam_id(settings)
        if not steam_id:
            return {}

        def fetch():
            payload = self._fetch_profile_json(f"{STEAM_WISHLIST_URL}?{urlencode({'steamid': steam_id})}", "wishlist")
            items = (payload.get("response") or {}).get("items") if isinstance(payload.get("response"), dict) else None
            wishlist = {}
            for item in (items if isinstance(items, list) else [])[:MAX_WISHLIST_ITEMS]:
                appid = str(item.get("appid") or "") if isinstance(item, dict) else ""
                if appid.isdecimal():
                    wishlist[appid] = int(item.get("priority") or 0)
            return wishlist

        wishlist = self._cached_lookup("wishlist", steam_id, WISHLIST_TTL, fetch)
        return wishlist if isinstance(wishlist, dict) else {}

    def _wishlist_deals(self, settings, wishlist):
        """The wishlist's games on sale now, deepest cut first, as pool items."""
        if not wishlist:
            return []
        country, _language = self._store_locale(settings)

        def fetch():
            deals = []
            ids = list(wishlist)
            for start in range(0, len(ids), STORE_ITEMS_BATCH):
                for entry in self._store_items(ids[start:start + STORE_ITEMS_BATCH], country, {}):
                    facts = self._parse_store_details(entry)
                    if facts and facts["discount"] > 0:
                        deals.append({
                            "id": int(entry["appid"]),
                            "type": 0,
                            "name": facts["name"],
                            "discounted": True,
                            "discount_percent": facts["discount"],
                            "_wishlist": True,
                            "_category_key": "wishlist",
                            "_category_tags": ["wishlist"],
                        })
            deals.sort(key=lambda deal: (-deal["discount_percent"], wishlist.get(str(deal["id"]), 0)))
            return deals[:MAX_WISHLIST_DEALS]

        key = f"{self._steam_id(settings)}|{country}|{len(wishlist)}"
        deals = self._cached_lookup("wishlist_deals", key, WISHLIST_DEALS_TTL, fetch)
        return [dict(deal) for deal in deals if isinstance(deal, dict)] if isinstance(deals, list) else []

    def _owned_appids(self, settings):
        """The owner's library (needs STEAM_API_KEY); empty without it."""
        steam_id = self._steam_id(settings)
        api_key = self._env_key("STEAM_API_KEY")
        if not steam_id or not api_key:
            return set()

        def fetch():
            query = urlencode({"key": api_key, "steamid": steam_id, "include_played_free_games": 1})
            payload = self._fetch_profile_json(f"{STEAM_OWNED_GAMES_URL}?{query}", "owned games")
            games = (payload.get("response") or {}).get("games") if isinstance(payload.get("response"), dict) else None
            return sorted(
                str(game["appid"]) for game in (games if isinstance(games, list) else [])
                if isinstance(game, dict) and str(game.get("appid") or "").isdecimal()
            )

        owned = self._cached_lookup("owned", steam_id, OWNED_TTL, fetch)
        return set(owned) if isinstance(owned, list) else set()

    def _personalize(self, items, settings):
        """The pool made the owner's own: games from the wishlist (and its deals the front page
        lacks) take every other place, deepest discount first; games already owned go last."""
        if not self._setting_on(settings, "personalize"):
            return items
        wishlist = self._wishlist(settings)
        owned = self._owned_appids(settings)
        mine, rest, held = [], [], []
        present = set()
        for item in items:
            appid = self._app_id(item)
            item = dict(item)
            if appid:
                present.add(appid)
            if appid and appid in wishlist:
                item["_wishlist"] = True
                mine.append(item)
            elif appid and appid in owned:
                item["_owned"] = True
                held.append(item)
            else:
                rest.append(item)
        mine.extend(deal for deal in self._wishlist_deals(settings, wishlist) if str(deal["id"]) not in present)
        mine.sort(key=lambda item: -int(item.get("discount_percent") or 0))
        merged = []
        for index in range(max(len(mine), len(rest))):
            merged.extend(group[index] for group in (mine, rest) if index < len(group))
        return merged + held

    def _prepare_shelf(self, items, settings):
        """The candidates' store facts in one batch and their Chinese lines in one AI call."""
        if not self._setting_on(settings, "infoShelf"):
            return
        appids = [appid for appid in (self._app_id(item) for item in items) if appid]
        if appids:
            details = self._store_details(appids, settings)
            self._blurbs(details)
            self._tag_names()
            # A few new studios' logos per refill; the rest come with later ones.
            fetched = 0
            for facts in details.values():
                deadline = getattr(self, "_active_data_deadline", None)
                if fetched >= MAX_NEW_STUDIO_LOGOS or (deadline is not None and deadline - time.monotonic() < 15):
                    break
                if facts.get("developer_clan") and self._studio_logo(facts["developer_clan"], fetch=False) is None:
                    fetched += 1
                    self._studio_logo(facts["developer_clan"])

    def _shelf_info(self, item, settings):
        """What the shelf says about this game, or None without its store facts."""
        appid = self._app_id(item)
        if not appid or not self._setting_on(settings, "infoShelf"):
            return None
        facts = self._store_details([appid], settings).get(appid)
        if not facts:
            return None
        names = self._tag_names()
        now = self._shelf_now()
        return {
            # The Steam Deck's verdict, with Steam's own mark, follows the reasons while there is room.
            "reasons": self._shelf_reasons(item, facts, now)
            + ([("steam_icon", DECK_LABELS[facts["deck"]])] if facts.get("deck") in DECK_LABELS else []),
            "price": self._shelf_price(facts),
            "line": self._blurbs({appid: facts}).get(appid) or facts.get("name") or str(item.get("name") or ""),
            "tags": [names[tag] for tag in facts.get("tags") or [] if tag in names][:4],
            # In reading order, each with how readily it gives way when the row is short.
            "credit": [
                (text, give)
                for text, give in (
                    (facts.get("developer"), 0),
                    (f"{facts['publisher']} 发行" if facts.get("publisher") else "", 3),
                    (self._release_label(facts, now), 2),
                    (self._review_label(facts), 1),
                )
                if text
            ],
            "studio_logo": self._studio_logo(facts.get("developer_clan")) if facts.get("developer") else None,
        }

    def _studio_logo_path(self, clan):
        return Path(self._cache_dir()) / "studio-logos" / f"{int(clan)}.png"

    def _open_studio_logo(self, path):
        try:
            return safe_open_image(str(path)).convert("RGB")
        except (OSError, ValueError):
            return None

    def _studio_logo(self, clan_id, fetch=True):
        """The studio's logo (its Steam creator clan's avatar), kept for STUDIO_LOGO_TTL; None
        when it has none. ``fetch`` False answers from what is kept alone."""
        clan = str(clan_id or "").strip()
        if not clan.isdecimal() or not int(clan):
            return None
        path = self._studio_logo_path(clan)
        studios = self._read_shelf_cache().get("studios")
        entry = studios.get(clan) if isinstance(studios, dict) and isinstance(studios.get(clan), dict) else {}
        if entry and time.time() - float(entry.get("at") or 0) < STUDIO_LOGO_TTL:
            return self._open_studio_logo(path) if entry.get("url") and path.is_file() else None
        if not fetch:
            return self._open_studio_logo(path) if path.is_file() else None
        try:
            page = self._fetch_profile_text(STEAM_CLAN_XML_URL.format(gid=CLAN_STEAMID_BASE + int(clan)), "studio page")
            match = STUDIO_AVATAR_RE.search(page)
            url = match.group(1) if match else ""
            if url:
                logo = self._download_image(url).resize((STUDIO_LOGO_SIZE, STUDIO_LOGO_SIZE), Image.Resampling.LANCZOS)
                path.parent.mkdir(parents=True, exist_ok=True)
                logo.save(path)
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Steam studio logo lookup failed: %s", type(exc).__name__)
            return self._open_studio_logo(path) if path.is_file() else None
        cache = self._read_shelf_cache()
        studios = cache.get("studios") if isinstance(cache.get("studios"), dict) else {}
        studios[clan] = {"at": time.time(), "url": url}
        cache["studios"] = dict(sorted(studios.items(), key=lambda pair: float(pair[1].get("at") or 0))[-MAX_STUDIOS:])
        self._write_shelf_cache(cache)
        return self._open_studio_logo(path) if url else None

    def _shelf_now(self):
        device_config = getattr(self, "_active_device_config", None)
        now = self._now_for_device(device_config) if device_config is not None else datetime.now()
        return now if now.tzinfo is not None else now.astimezone()

    @staticmethod
    def _release_age_days(facts, now):
        released = facts.get("release")
        if not released:
            return None
        return (now - datetime.fromtimestamp(int(released), tz=now.tzinfo)).total_seconds() / 86400

    def _shelf_reasons(self, item, facts, now):
        """Why the game is here, the owner's own reasons first; at most three."""
        tags = set(item.get("_category_tags") or [])
        age = self._release_age_days(facts, now)
        reasons = []
        if item.get("_wishlist"):
            reasons.append(("wishlist", "愿望单特惠" if facts.get("discount") else "在你的愿望单"))
        if "daily_deals" in tags:
            reasons.append(("daily_deal", "今日特惠"))
        elif "specials" in tags and not item.get("_wishlist"):
            reasons.append(("deal", "特惠"))
        if age is not None and 0 <= age < 1:
            reasons.append(("new", "今日发售"))
        elif "new_releases" in tags or (age is not None and 0 <= age < NEW_RELEASE_DAYS):
            reasons.append(("new", "新品"))
        if "top_sellers" in tags:
            reasons.append(("top_seller", "热销"))
        if "spotlights" in tags:
            reasons.append(("spotlight", "首页推荐"))
        if "coming_soon" in tags or facts.get("coming_soon"):
            reasons.append(("coming_soon", "即将推出"))
        if item.get("_owned"):
            reasons.append(("owned", "已在你的库中"))
        if item.get("_owned") and len(reasons) > 3:
            reasons = [*reasons[:2], reasons[-1]]
        return reasons[:3]

    @staticmethod
    def _shelf_price(facts):
        if facts.get("free"):
            return {"label": "免费开玩"}
        if facts.get("discount") and facts.get("final"):
            return {"discount": facts["discount"], "final": facts["final"], "original": facts.get("original") or ""}
        if facts.get("final"):
            return {"final": facts["final"]}
        if facts.get("coming_soon"):
            return {"label": "即将推出"}
        return None

    def _release_label(self, facts, now):
        released = facts.get("release")
        if not released:
            return ""
        day = datetime.fromtimestamp(int(released), tz=now.tzinfo)
        age = (now - day).total_seconds() / 86400
        if age < 0:
            return f"{day:%m/%d} 发售"
        if age < 1:
            return "今日发售"
        if age < NEW_RELEASE_DAYS:
            return f"{day:%m/%d} 发售"
        return f"{day.year} 年发售"

    @staticmethod
    def _review_label(facts):
        if not facts.get("review_label") or not facts.get("review_count"):
            return ""
        return f"{facts['review_label']} {facts['review_percent']}%"

    def _blurbs(self, details):
        """One line in Chinese per app: Steam's own Chinese description when it has one, else
        an AI line from the English one (cached against that text), else the English cut."""
        cache = self._read_shelf_cache()
        known = cache.get("blurbs") if isinstance(cache.get("blurbs"), dict) else {}
        lines, missing = {}, {}
        for appid, facts in details.items():
            text = str(facts.get("description") or "")
            if not text:
                continue
            if _has_cjk(text):
                lines[appid] = _first_sentence(text)
                continue
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            entry = known.get(appid)
            if isinstance(entry, dict) and entry.get("source") == digest and entry.get("text"):
                lines[appid] = str(entry["text"])
            else:
                missing[appid] = (digest, text)
        if missing:
            translated = self._translate_blurbs({appid: text for appid, (_digest, text) in missing.items()})
            if translated:
                cache = self._read_shelf_cache()
                known = cache.get("blurbs") if isinstance(cache.get("blurbs"), dict) else {}
                for appid, line in translated.items():
                    known[appid] = {"source": missing[appid][0], "text": line, "at": time.time()}
                cache["blurbs"] = dict(sorted(known.items(), key=lambda pair: float(pair[1].get("at") or 0))[-MAX_BLURBS:])
                self._write_shelf_cache(cache)
                lines.update(translated)
        for appid, (_digest, text) in missing.items():
            lines.setdefault(appid, _first_sentence(text))
        return lines

    def _translate_blurbs(self, texts):
        """{app id: one Chinese line} from the English blurbs in one model call; {} without a
        key, with too little of the data deadline left, or when the call fails."""
        api_key = self._env_key("OPEN_AI_SECRET") or self._env_key("OPENAI_API_KEY")
        if not api_key or not texts:
            return {}
        deadline = getattr(self, "_active_data_deadline", None)
        if deadline is not None and deadline - time.monotonic() < BLURB_TIMEOUT_SECONDS + 5:
            return {}
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=BLURB_TIMEOUT_SECONDS, max_retries=0)
            # The same Responses API shape as the news brief: a strict JSON schema, a short think.
            response = client.responses.create(
                model=BLURB_MODEL,
                instructions=BLURB_INSTRUCTIONS,
                input=json.dumps([{"appid": appid, "text": text} for appid, text in texts.items()], ensure_ascii=False),
                max_output_tokens=BLURB_MAX_OUTPUT_TOKENS,
                reasoning={"effort": "low"},
                text={
                    "format": {"type": "json_schema", "name": "steam_lines", "strict": True, "schema": BLURB_JSON_SCHEMA},
                    "verbosity": "low",
                },
            )
            reply = json.loads(getattr(response, "output_text", "") or "{}")
        except TaskCancelled:
            raise
        except Exception as exc:
            # The provider's message names what it refused; it never carries the key.
            logger.warning("Steam blurb translation failed: %s: %s", type(exc).__name__, str(exc)[:200])
            return {}
        rows = reply.get("lines") if isinstance(reply, dict) else None
        lines = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            appid = str(row.get("appid") or "")
            line = " ".join(str(row.get("line") or "").split()).strip("。.!！\"'“”「」《》")
            if appid in texts and _has_cjk(line):
                lines[appid] = line[:BLURB_MAX_CHARS]
        return lines

    def _fetch_json(self, url, *, label, max_bytes):
        payload = self._fetch_bytes(url, label=label, max_bytes=max_bytes)
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Steam {label} response is not JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"Steam {label} response has an invalid shape")
        return value

    def _fetch_bytes(self, url, *, label, max_bytes):
        """A Steam store, API or community page's body within ``max_bytes``."""
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
        return bytes(payload)

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
            for item in self._personalize(self._collect_items(data, source_category), settings)
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

    def _smart_cover(self, image, dimensions, resample=Image.Resampling.LANCZOS):
        target_width, target_height = dimensions
        target_ratio = target_width / target_height
        image_ratio = image.width / image.height

        if image_ratio < target_ratio * 0.82:
            return self._contain_on_canvas(image, dimensions, resample)

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

        return cropped.resize((target_width, target_height), resample)

    def _contain_on_canvas(self, image, dimensions, resample=Image.Resampling.LANCZOS):
        target_width, target_height = dimensions
        image_ratio = image.width / image.height
        target_ratio = target_width / target_height

        if image_ratio > target_ratio:
            new_width = target_width
            new_height = max(1, int(target_width / image_ratio))
        else:
            new_height = target_height
            new_width = max(1, int(target_height * image_ratio))

        fitted = image.resize((new_width, new_height), resample)

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
