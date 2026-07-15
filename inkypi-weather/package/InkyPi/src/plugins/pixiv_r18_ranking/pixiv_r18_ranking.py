from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import logging
import os
import random
import re
import socket
import ssl
import stat
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse, urlsplit

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationPreparation,
    get_presentation_instance_uuid,
)
from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from plugins.base_plugin.theme_presentation import apply_media_theme_chrome
from plugins.pixiv_r18_ranking.presentation_bank import (
    READY_TARGET,
    REFILL_THRESHOLD,
    PixivPresentationBank,
    atomic_write_bounded_json,
    instance_profile_fingerprint,
    read_bounded_json_object,
    settings_fingerprint,
    settings_key,
    validate_pixiv_media_target,
)
from security.ssrf import get_ssrf_policy
from utils.app_utils import get_base_ui_font
from utils.safe_image import ImageLimits, safe_open_image

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
MAX_DATA_NEW_MEDIA = 12
MAX_DATA_ATTEMPTS = 36
MAX_DATA_SECONDS = 30
MAX_MEDIA_REDIRECTS = 4
MAX_RANKING_JSON_BYTES = 4 * 1024 * 1024
# Compatibility export consumed by reddit_rule34_hot and older plugin modules.
DOWNLOAD_CHUNK_SIZE = 8192
JST = timezone(timedelta(hours=9))
MAX_PI_SAFE_SOURCE_PIXELS = 900_000
RESAMPLING_FILTER = getattr(Image, "Resampling", Image).BICUBIC
JAPANESE_FONT_SAMPLE = "\u65e5\u672c\u8a9e\u3042\u30a2"
JAPANESE_FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    Path(__file__).resolve().parents[2] / "static" / "fonts" / "NotoSansSC-VF.ttf",
)

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


class _PinnedHTTPSResponse:
    """Minimal HTTPS response bound to an SSRF-approved address set."""

    def __init__(self, response, connection, url, *, deadline, clock, read_timeout):
        self._response = response
        self._connection = connection
        self.url = url
        self.status_code = int(response.status)
        self.headers = response.headers
        self._deadline = deadline
        self._clock = clock
        self._read_timeout = float(read_timeout)

    @classmethod
    def open(cls, approved, *, headers, deadline, clock, timeout):
        last_error = None
        for address in approved.addresses:
            raw_socket = None
            tls_socket = None
            try:
                connect_timeout = _remaining_timeout(deadline, clock, min(5.0, timeout))
                parsed_address = ipaddress.ip_address(address)
                family = socket.AF_INET6 if parsed_address.version == 6 else socket.AF_INET
                raw_socket = socket.socket(family, socket.SOCK_STREAM)
                raw_socket.settimeout(connect_timeout)
                endpoint = (
                    (address, approved.port, 0, 0)
                    if parsed_address.version == 6
                    else (address, approved.port)
                )
                raw_socket.connect(endpoint)
                raw_socket.settimeout(_remaining_timeout(deadline, clock, timeout))
                tls_socket = ssl.create_default_context().wrap_socket(
                    raw_socket,
                    server_hostname=approved.hostname,
                )
                raw_socket = None
                tls_socket.settimeout(_remaining_timeout(deadline, clock, timeout))
                parsed = urlsplit(approved.normalized_url)
                request_target = parsed.path or "/"
                if parsed.query:
                    request_target = f"{request_target}?{parsed.query}"
                authority = getattr(approved, "authority", approved.hostname)
                lines = [f"GET {request_target} HTTP/1.1", f"Host: {authority}"]
                for name, value in headers.items():
                    name = str(name)
                    value = str(value)
                    if name.lower() in {"host", "connection"}:
                        continue
                    if not name or "\r" in name or "\n" in name or "\r" in value or "\n" in value:
                        raise RuntimeError("Pixiv request headers are invalid")
                    lines.append(f"{name}: {value}")
                lines.append("Connection: close")
                tls_socket.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
                response = http.client.HTTPResponse(tls_socket)
                response.begin()
                return cls(
                    response,
                    tls_socket,
                    approved.normalized_url,
                    deadline=deadline,
                    clock=clock,
                    read_timeout=timeout,
                )
            except RuntimeError:
                if tls_socket is not None:
                    tls_socket.close()
                elif raw_socket is not None:
                    raw_socket.close()
                raise
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                if tls_socket is not None:
                    tls_socket.close()
                elif raw_socket is not None:
                    raw_socket.close()
                last_error = exc
        raise RuntimeError("Pixiv approved target could not be reached") from last_error

    def iter_content(self, chunk_size):
        while True:
            timeout = _remaining_timeout(
                self._deadline,
                self._clock,
                self._read_timeout,
            )
            self._connection.settimeout(timeout)
            chunk = self._response.read(chunk_size)
            if not chunk:
                return
            yield chunk

    def raise_for_status(self):
        if not 200 <= self.status_code < 300:
            raise RuntimeError(f"Pixiv provider request failed with status {self.status_code}")

    def close(self):
        try:
            self._response.close()
        finally:
            self._connection.close()


def _remaining_timeout(deadline, clock, configured):
    configured = max(0.001, float(configured))
    if deadline is None:
        return configured
    remaining = float(deadline) - float(clock())
    if remaining <= 0:
        raise RuntimeError("Pixiv DATA deadline is exhausted")
    return max(0.001, min(configured, remaining))


class PixivR18Ranking(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        if get_presentation_instance_uuid(settings) is not None:
            if settings.get("_theme_render_only") is True:
                return self._generate_theme_only(settings, device_config)
            return self._generate_banked_image(settings, device_config)
        return self._generate_stateless_preview(settings, device_config)

    def _generate_stateless_preview(self, settings, device_config):
        """Render an unsaved preview without touching provider or bank state."""

        dimensions = self._display_dimensions(device_config)
        try:
            pool = self._read_daily_pool()
            if not pool:
                cookie = self._load_session_cookie(device_config)
                resolution = self._resolve_ranking_with_provenance(
                    self._ranking_mode(settings),
                    cookie,
                )
                pool = []
                for rank, raw in enumerate(resolution["items"], start=1):
                    if not self._is_safe_ranking_item(raw):
                        continue
                    item = self._ranking_item_metadata(raw, rank)
                    item.update(_resolution_provenance(resolution))
                    source = self._download_ranking_item_source_image(item, dimensions)
                    if source is not None:
                        item["_preview_image"] = source
                        pool.append(item)
                    if len(pool) >= MAX_STRIP_CELLS:
                        break
            if not pool:
                logger.warning("Pixiv R-18 ranking daily pool is empty after filtering.")
                return self._fallback_image(dimensions, "Pixiv R-18", "No filtered image available")

            group = self._preview_display_group(pool, settings)
            if not group:
                return self._fallback_image(dimensions, "Pixiv R-18", "No cached image available")

            images = []
            for item in group:
                image = item.get("_preview_image") or self._load_cached_item_image(item, dimensions)
                if image:
                    images.append(image.convert("RGB"))
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

    def _preview_display_group(self, pool, settings):
        candidates = list(pool)
        if not candidates:
            return []
        head = candidates[0]
        if self._fit_mode(settings) == "auto_layout" and self._is_portrait_item(head):
            return [item for item in candidates if self._is_portrait_item(item)][:MAX_STRIP_CELLS]
        return [head]

    def presentation_mode(self, settings):
        return PresentationMode.PREPARED_BANK

    def _generate_banked_image(self, settings, device_config):
        deadline = self._monotonic() + MAX_DATA_SECONDS
        dimensions = self._display_dimensions(device_config)
        date_key = self._day_key()
        ranking_mode = self._ranking_mode(settings)
        cookie = self._load_session_cookie(device_config)
        force_refresh = _setting_enabled(settings.get("forceRefresh")) or _setting_enabled(
            settings.get("force_refresh")
        )
        requested_r18 = self._mode_pair(ranking_mode)[0] is not None
        resolution = None
        provider_error = None
        provider_attempted = False
        provider_attempted_at = None
        provider_status = None
        live_record_keys = set()
        instance_uuid = get_presentation_instance_uuid(settings)
        provenance = self._saved_provenance_for_instance(instance_uuid)
        if provenance is None:
            provider_attempted = True
            provider_attempted_at = self._now_utc().isoformat()
            try:
                resolution = self._resolve_ranking_with_provenance(
                    ranking_mode,
                    cookie,
                    deadline=deadline,
                )
            except Exception as exc:
                raise RuntimeError("Pixiv ranking source is unavailable") from exc
            provenance = _resolution_provenance(resolution)
            provider_status = (
                "error"
                if requested_r18 and resolution.get("healthy_r18") is not True
                else ("success" if resolution.get("items") else "empty")
            )
        bank = self._presentation_bank(settings, dimensions, date_key, provenance)
        document, profile = bank.load_for_data()
        profile["source_provenance"] = dict(provenance)
        if provider_attempted:
            profile["last_provider_attempt_at"] = provider_attempted_at
            profile["last_provider_status"] = provider_status
        if force_refresh and provider_status == "error":
            bank.save(document)
            raise RuntimeError("Pixiv forced R-18 refresh resolved only an SFW fallback")

        self._recover_protected_media(
            bank,
            document,
            profile,
            dimensions,
            deadline=deadline,
        )
        ready = bank.ready_records(profile, prune=True)
        if len(ready) < REFILL_THRESHOLD:
            profile["refill_in_progress"] = True
        if force_refresh or (
            profile.get("refill_in_progress") is True and len(ready) < READY_TARGET
        ):
            if resolution is None:
                provider_attempted = True
                provider_attempted_at = self._now_utc().isoformat()
                try:
                    resolution = self._resolve_ranking_with_provenance(
                        ranking_mode,
                        cookie,
                        deadline=deadline,
                    )
                except Exception as exc:
                    provider_error = exc
                    provider_status = "error"
            if resolution is not None:
                provider_status = (
                    "error"
                    if requested_r18 and resolution.get("healthy_r18") is not True
                    else ("success" if resolution.get("items") else "empty")
                )
                if force_refresh and provider_status == "error":
                    profile["last_provider_attempt_at"] = provider_attempted_at
                    profile["last_provider_status"] = "error"
                    bank.save(document)
                    raise RuntimeError("Pixiv forced R-18 refresh resolved only an SFW fallback")
                live_provenance = _resolution_provenance(resolution)
                if _provenance_identity(live_provenance) != _provenance_identity(provenance):
                    provenance = live_provenance
                    bank = self._presentation_bank(settings, dimensions, date_key, provenance)
                    document, profile = bank.load_for_data()
                    profile["source_provenance"] = dict(provenance)
                    self._recover_protected_media(
                        bank,
                        document,
                        profile,
                        dimensions,
                        deadline=deadline,
                    )
                    ready = bank.ready_records(profile, prune=True)
                    if len(ready) < REFILL_THRESHOLD:
                        profile["refill_in_progress"] = True
                else:
                    profile["source_provenance"] = dict(live_provenance)
                live_record_keys.update(self._refill_presentation_bank(
                    bank,
                    profile,
                    resolution,
                    dimensions,
                    deadline=deadline,
                    force_refresh=force_refresh,
                ))
                ready = bank.ready_records(profile, prune=True)
            elif not ready:
                profile["last_provider_attempt_at"] = provider_attempted_at
                profile["last_provider_status"] = "error"
                bank.save(document)
                raise RuntimeError("Pixiv ranking source is unavailable") from provider_error
            else:
                profile["source_provenance"]["source_status"] = "stale"
        if provider_attempted:
            profile["last_provider_attempt_at"] = provider_attempted_at
            profile["last_provider_status"] = provider_status or "empty"
        if profile.get("refill_in_progress") is True:
            profile["refill_in_progress"] = len(ready) < READY_TARGET

        bank.cleanup(
            document,
            profile,
            before_save=lambda: self._remaining_data_timeout(
                deadline,
                MAX_DATA_SECONDS,
            ),
        )
        self._cleanup_legacy_image_days(date_key)
        ready = bank.ready_records(profile, prune=True)
        if not ready:
            raise RuntimeError("Pixiv presentation bank is unavailable") from provider_error
        current = profile.get("current_selection")
        if current is None:
            current = bank.ensure_current(document, profile, ready, self._fit_mode(settings))
        else:
            bank.selection_records(profile, current, load_media=True)
        image = self._render_bank_selection(bank, profile, current, dimensions, settings)
        if requested_r18 and str(provenance.get("content_rating") or "").lower() != "r18":
            source_provenance = SourceProvenance.LOCAL_FALLBACK
        elif set(current.get("record_keys") or []).intersection(live_record_keys):
            source_provenance = SourceProvenance.LIVE
        else:
            source_provenance = SourceProvenance.FRESH_CACHE
        if force_refresh and provider_status == "error":
            source_provenance = SourceProvenance.STALE_CACHE
            image.info["inkypi_skip_cache"] = True
        return attach_source_provenance(image, source_provenance)

    def _recover_protected_media(
        self,
        bank,
        document,
        profile,
        dimensions,
        *,
        deadline=None,
    ):
        protected_changed = False
        for record in bank.protected_records(profile):
            try:
                bank.load_media(record, allow_stale=True)
            except RuntimeError as media_error:
                try:
                    self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
                    recovered = self._download_ranking_item_source_image(
                        record,
                        dimensions,
                        deadline=deadline,
                    )
                    self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
                    if recovered is None:
                        raise RuntimeError("Pixiv exact media recovery returned no image")
                    self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
                    bank.recover_media(
                        profile,
                        record,
                        recovered,
                        before_commit=lambda: self._remaining_data_timeout(
                            deadline,
                            MAX_DATA_SECONDS,
                        ),
                    )
                    protected_changed = True
                except Exception as recovery_error:
                    raise RuntimeError("Pixiv protected media recovery failed") from recovery_error
                logger.info(
                    "Recovered exact protected Pixiv media for %s after: %s",
                    record.get("illust_id"),
                    media_error,
                )
        if protected_changed:
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            bank.save(
                document,
                before_commit=lambda: self._remaining_data_timeout(
                    deadline,
                    MAX_DATA_SECONDS,
                ),
            )

    def _refill_presentation_bank(
        self,
        bank,
        profile,
        resolution,
        dimensions,
        *,
        deadline=None,
        force_refresh=False,
    ):
        existing_ids = {record["illust_id"] for record in profile["records"]}
        existing_urls = {record["image_url"] for record in profile["records"]}
        ready_count = len(bank.ready_records(profile, prune=False))
        attempts = 0
        downloaded = 0
        ingested_keys = set()
        full_force_refresh = force_refresh and ready_count >= READY_TARGET
        page = 1
        items = list(resolution.get("items") or [])
        provenance = _resolution_provenance(resolution)
        while page <= MAX_RANKING_PAGES and items:
            for raw in items:
                if (
                    (ready_count >= READY_TARGET and not full_force_refresh)
                    or attempts >= MAX_DATA_ATTEMPTS
                    or downloaded >= MAX_DATA_NEW_MEDIA
                    or (deadline is not None and self._monotonic() >= deadline)
                ):
                    return ingested_keys
                if full_force_refresh and downloaded >= 1:
                    return ingested_keys
                if not self._is_safe_ranking_item(raw):
                    continue
                item = self._ranking_item_metadata(raw, _get_value(raw, "rank", ready_count + 1))
                if item["illust_id"] in existing_ids or item["image_url"] in existing_urls:
                    continue
                attempts += 1
                try:
                    self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
                    source = self._download_ranking_item_source_image(
                        item,
                        dimensions,
                        deadline=deadline,
                    )
                    self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
                    if source is None:
                        continue
                    self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
                    record = bank.ingest(
                        profile,
                        {**item, **provenance},
                        source,
                        before_commit=lambda: self._remaining_data_timeout(
                            deadline,
                            MAX_DATA_SECONDS,
                        ),
                    )
                except Exception as exc:
                    logger.warning("Pixiv bank candidate failed for %s: %s", item["illust_id"], exc)
                    continue
                existing_ids.add(record["illust_id"])
                existing_urls.add(record["image_url"])
                ready_count += 1
                downloaded += 1
                ingested_keys.add(record["record_key"])
            page += 1
            if page > MAX_RANKING_PAGES:
                break
            try:
                items = self._fetch_ranking_page_with_deadline(
                    resolution["effective_mode"],
                    resolution.get("cookie"),
                    page,
                    deadline=deadline,
                )
            except Exception as exc:
                logger.warning("Pixiv ranking refill page %s failed: %s", page, exc)
                break
        return ingested_keys

    def prepare_presentation(
        self,
        settings,
        device_config,
        *,
        request,
        resolved_theme_context,
    ):
        settings = settings or {}
        instance_uuid = get_presentation_instance_uuid(settings)
        if instance_uuid is None:
            raise RuntimeError("Pixiv presentation requires trusted instance identity")
        dimensions = self._display_dimensions(device_config)
        bank = self._presentation_bank_for_request(instance_uuid, request.request_id)
        if bank is None:
            provenance = self._saved_provenance_for_instance(instance_uuid)
            if provenance is None:
                raise RuntimeError("Pixiv presentation bank is cold")
            bank = self._presentation_bank(
                settings,
                dimensions,
                self._day_key(),
                provenance,
            )
        document, profile = bank.load_warm()
        bank.apply_trusted_origin(document, profile, request)
        pending = bank.pending_for_request(profile, request.request_id)
        if pending is None:
            ready = bank.ready_records(profile, prune=False)
            selection = bank.choose_selection(
                document,
                profile,
                ready,
                self._fit_mode(settings),
            )
        else:
            selection = pending
        image = self._render_bank_selection(bank, profile, selection, dimensions, settings)
        if resolved_theme_context is not None:
            image = apply_media_theme_chrome(
                image,
                self.get_plugin_id(),
                resolved_theme_context,
                dimensions,
            )
            mode = resolved_theme_context.get("mode")
            if mode in {"day", "night"}:
                image.info["inkypi_theme_mode"] = mode
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
            raise RuntimeError("Pixiv receipt requires trusted instance identity")
        bank = self._presentation_bank_for_request(instance_uuid, receipt.request_id)
        if bank is None:
            return None
        document, profile = bank.load_receipt_profile(receipt.request_id)
        bank.reconcile_receipt(document, profile, receipt)
        return None

    def _generate_theme_only(self, settings, device_config):
        instance_uuid = get_presentation_instance_uuid(settings)
        dimensions = self._display_dimensions(device_config)
        provenance = self._saved_provenance_for_instance(instance_uuid)
        if provenance is None:
            raise RuntimeError("Pixiv theme redraw requires a warm bank")
        bank = self._presentation_bank(settings, dimensions, self._day_key(), provenance)
        document, profile = bank.load_warm()
        current = profile.get("current_selection")
        if current is None:
            raise RuntimeError("Pixiv theme redraw has no current selection")
        return self._render_bank_selection(bank, profile, current, dimensions, settings)

    def _presentation_bank(self, settings, dimensions, date_key, provenance):
        instance_uuid = get_presentation_instance_uuid(settings)
        if instance_uuid is None:
            raise RuntimeError("Pixiv bank requires trusted instance identity")
        base = settings_fingerprint(
            settings,
            dimensions,
            date_key,
            effective_mode=provenance["effective_mode"],
            content_rating=provenance["content_rating"],
        )
        fingerprint = instance_profile_fingerprint(base, instance_uuid)
        return PixivPresentationBank(
            self._presentation_state_path(),
            self._presentation_media_dir(),
            fingerprint=fingerprint,
            base_fingerprint=base,
            profile_settings_key=settings_key(settings),
            instance_uuid=instance_uuid,
            date_key=date_key,
        )

    def _presentation_bank_for_request(self, instance_uuid, request_id):
        path = self._presentation_state_path()
        if not path.exists():
            return None
        document = read_bounded_json_object(path)
        profiles = document.get("profiles")
        if not isinstance(profiles, dict):
            return None
        for fingerprint, profile in profiles.items():
            if not isinstance(profile, dict) or profile.get("instance_uuid") != instance_uuid:
                continue
            pending = profile.get("pending_selection")
            if not isinstance(pending, dict) or pending.get("request_id") != request_id:
                continue
            return PixivPresentationBank(
                path,
                self._presentation_media_dir(),
                fingerprint=fingerprint,
                base_fingerprint=profile.get("settings_fingerprint"),
                profile_settings_key=profile.get("settings_key"),
                instance_uuid=instance_uuid,
                date_key=pending.get("date_key"),
            )
        return None

    def _saved_provenance_for_instance(self, instance_uuid):
        path = self._presentation_state_path()
        if not path.exists():
            return None
        document = read_bounded_json_object(path)
        fingerprint = (document.get("instance_profiles") or {}).get(instance_uuid)
        profile = (document.get("profiles") or {}).get(fingerprint)
        if not isinstance(profile, dict):
            return None
        provenance = profile.get("source_provenance")
        if isinstance(provenance, dict) and provenance.get("effective_mode"):
            return dict(provenance)
        records = profile.get("records")
        if isinstance(records, list) and records and isinstance(records[0], dict):
            recovered = _resolution_provenance(records[0])
            if recovered.get("effective_mode"):
                return recovered
        return None

    def _render_bank_selection(self, bank, profile, selection, dimensions, settings):
        selected = bank.selection_records(profile, selection, load_media=True)
        images = [image for _record, image in selected]
        records = [record for record, _image in selected]
        if len(images) > 1:
            return self._compose_strip(images, dimensions, settings)
        return self._fit_image(images[0], dimensions, settings, records[0])

    def _presentation_state_path(self):
        return self._presentation_root_dir() / "presentation-state.json"

    def _presentation_media_dir(self):
        return self._presentation_root_dir() / "presentation-media"

    def _presentation_root_dir(self):
        if os.getenv("INKYPI_PIXIV_R18_CACHE"):
            return self._cache_dir()
        return self.data_dir(
            env_var="INKYPI_PIXIV_R18_DATA",
            leaf="presentation-bank",
            legacy_leaf=".pixiv_r18_ranking_cache",
            create=False,
            strip=True,
        )

    def _cleanup_legacy_image_days(self, current_day, *, max_files=64):
        """Bound one legacy cleanup pass without following links or reparses."""

        root = self._cache_dir() / "images"
        try:
            root_info = root.lstat()
        except FileNotFoundError:
            return 0
        if root.is_symlink() or not root.is_dir() or _stat_is_reparse(root_info):
            return 0
        removed = 0
        for day_dir in sorted(root.iterdir(), key=lambda item: item.name):
            if removed >= max_files or day_dir.name == str(current_day):
                continue
            try:
                day_info = day_dir.lstat()
            except OSError:
                continue
            if day_dir.is_symlink() or not day_dir.is_dir() or _stat_is_reparse(day_info):
                continue
            for target in sorted(day_dir.iterdir(), key=lambda item: item.name):
                if removed >= max_files:
                    break
                try:
                    info = target.lstat()
                except OSError:
                    continue
                if target.is_symlink() or _stat_is_reparse(info):
                    continue
                if target.is_file():
                    try:
                        target.unlink()
                    except OSError:
                        continue
                    removed += 1
            try:
                day_dir.rmdir()
            except OSError:
                pass
        return removed

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
        resolution = self._resolve_ranking_with_provenance(ranking_mode, cookie)
        return (
            resolution["effective_mode"],
            resolution.get("cookie"),
            resolution["items"],
        )

    def _resolve_ranking_with_provenance(self, ranking_mode, cookie, *, deadline=None):
        """Resolve the effective source without ever labelling SFW as healthy R-18."""

        r18_mode, sfw_mode = self._mode_pair(ranking_mode)
        if r18_mode:
            if cookie:
                try:
                    return {
                        "requested_mode": ranking_mode,
                        "effective_mode": r18_mode,
                        "content_rating": "r18",
                        "authenticated": True,
                        "healthy_r18": True,
                        "source_status": "fresh",
                        "cookie": cookie,
                        "items": self._fetch_ranking_page_with_deadline(
                            r18_mode,
                            cookie,
                            1,
                            deadline=deadline,
                        ),
                    }
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
            return {
                "requested_mode": ranking_mode,
                "effective_mode": sfw_mode,
                "content_rating": "sfw",
                "authenticated": False,
                "healthy_r18": False,
                "source_status": "fresh_sfw_fallback",
                "cookie": None,
                "items": self._fetch_ranking_page_with_deadline(
                    sfw_mode,
                    None,
                    1,
                    deadline=deadline,
                ),
            }
        eff_cookie = cookie or None
        return {
            "requested_mode": ranking_mode,
            "effective_mode": sfw_mode,
            "content_rating": "sfw",
            "authenticated": bool(eff_cookie),
            "healthy_r18": False,
            "source_status": "fresh",
            "cookie": eff_cookie,
            "items": self._fetch_ranking_page_with_deadline(
                sfw_mode,
                eff_cookie,
                1,
                deadline=deadline,
            ),
        }

    def _fetch_ranking(self, ranking_mode, cookie):
        """First ranking page for the mode (R-18 with cookie, else SFW fallback)."""
        return self._resolve_ranking(ranking_mode, cookie)[2]

    def _mode_pair(self, ranking_mode):
        """Returns (r18_mode, sfw_fallback_mode); r18_mode is None for plain modes."""
        return RANKING_MODE_MAP.get(ranking_mode, (None, ranking_mode))

    def _fetch_ranking_page(self, mode, cookie, page=1, *, deadline=None):
        params = {"mode": mode, "content": "illust", "format": "json", "p": int(page)}
        timeout = self._remaining_data_timeout(deadline, 40)
        request_url = f"{RANKING_URL}?{urlencode(params)}"
        headers = dict(PIXIV_RANKING_HEADERS)
        if cookie:
            headers["Cookie"] = f"PHPSESSID={cookie}"
        response = self._request_ranking_target(
            request_url,
            headers=headers,
            timeout=timeout,
            deadline=deadline,
        )
        try:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > MAX_RANKING_JSON_BYTES:
                        raise RuntimeError("Pixiv ranking response exceeds its object budget")
                except ValueError:
                    pass
            payload_bytes = bytearray()
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                self._remaining_data_timeout(deadline, 40)
                if not chunk:
                    continue
                if len(payload_bytes) + len(chunk) > MAX_RANKING_JSON_BYTES:
                    raise RuntimeError("Pixiv ranking response exceeds its object budget")
                payload_bytes.extend(chunk)
            if not payload_bytes:
                raise RuntimeError("Pixiv ranking response is empty")
        finally:
            response.close()
        self._remaining_data_timeout(deadline, 40)
        try:
            self._remaining_data_timeout(deadline, 40)
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # R-18 without a valid cookie returns the HTML landing page, not JSON.
            raise RuntimeError(f"pixiv ranking '{mode}' did not return JSON (auth/cookie issue)") from exc
        self._remaining_data_timeout(deadline, 40)
        if isinstance(payload, dict):
            if payload.get("error"):
                raise RuntimeError(f"pixiv ranking '{mode}' error: {payload.get('message') or 'unknown'}")
            contents = payload.get("contents")
            if isinstance(contents, list):
                return contents
        return []

    def _request_ranking_target(self, url, *, headers, timeout, deadline):
        approved = get_ssrf_policy().resolve_and_validate(url)
        validate_pixiv_media_target(approved)
        return self._request_approved_target(
            approved,
            headers=headers,
            timeout=timeout,
            deadline=deadline,
        )

    def _fetch_ranking_page_with_deadline(self, mode, cookie, page, *, deadline):
        if deadline is None:
            return self._fetch_ranking_page(mode, cookie, page)
        return self._fetch_ranking_page(mode, cookie, page, deadline=deadline)

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

    def _download_ranking_item_image(self, item, dimensions, *, deadline=None):
        tmp_path = None
        resized_path = None
        try:
            tmp_path = (
                self._download_to_temp(item["image_url"])
                if deadline is None
                else self._download_to_temp(item["image_url"], deadline=deadline)
            )
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            image_info = self._source_image_info(tmp_path)
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            if image_info and image_info["pixels"] > MAX_PI_SAFE_SOURCE_PIXELS:
                if image_info["format"] == "WEBP":
                    raise RuntimeError("oversized WebP skipped for Pi-safe decode")
                self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
                resized_path = self._downsample_to_pi_safe_image(tmp_path)
                self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            load_path = resized_path or tmp_path
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            image = self.image_loader.from_file(str(load_path), dimensions, resize=False)
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            if not image:
                raise RuntimeError("image load returned empty")
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            return self._write_cached_image(item, image)
        finally:
            for path in (tmp_path, resized_path):
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception:
                        pass

    def _download_ranking_item_source_image(self, item, dimensions, *, deadline=None):
        tmp_path = None
        resized_path = None
        try:
            tmp_path = (
                self._download_to_temp(item["image_url"])
                if deadline is None
                else self._download_to_temp(item["image_url"], deadline=deadline)
            )
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            info = self._source_image_info(tmp_path)
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            if info and info["pixels"] > 32_000_000:
                raise RuntimeError("Pixiv media dimensions exceed the safety limit")
            if info and info["pixels"] > MAX_PI_SAFE_SOURCE_PIXELS:
                if info["format"] == "WEBP":
                    raise RuntimeError("oversized WebP skipped for Pi-safe decode")
                self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
                resized_path = self._downsample_to_pi_safe_image(tmp_path)
                self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            load_path = resized_path or tmp_path
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            image = safe_open_image(
                load_path,
                limits=ImageLimits(max_bytes=12 * 1024 * 1024),
                draft_size=(dimensions[0] * 3, dimensions[1] * 3),
            ).convert("RGB")
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            image.thumbnail((dimensions[0] * 3, dimensions[1] * 3), RESAMPLING_FILTER)
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            return image
        finally:
            for path in (tmp_path, resized_path):
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception:
                        pass

    def _download_to_temp(self, url, *, deadline=None):
        payload = self._download_media_bytes(
            url,
            max_bytes=12 * 1024 * 1024,
            timeout=40,
            deadline=deadline,
        )
        self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
        suffix = Path(urlparse(url).path).suffix or ".img"
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = Path(temp_file.name)
        try:
            with temp_file:
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            os.chmod(tmp_path, 0o600)
            self._remaining_data_timeout(deadline, MAX_DATA_SECONDS)
            return tmp_path
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _download_media_bytes(self, url, *, max_bytes, timeout, deadline=None):
        policy = get_ssrf_policy()
        current_url = str(url or "").strip()
        for redirect_count in range(MAX_MEDIA_REDIRECTS + 1):
            approved = policy.resolve_and_validate(current_url)
            validate_pixiv_media_target(approved)
            remaining = self._remaining_data_timeout(deadline, timeout)
            response = self._request_approved_target(
                approved,
                headers=PIXIV_IMAGE_HEADERS,
                timeout=remaining,
                deadline=deadline,
            )
            try:
                response_url = str(
                    getattr(response, "url", approved.normalized_url)
                    or approved.normalized_url
                )
                final_hop = policy.resolve_and_validate(response_url)
                validate_pixiv_media_target(final_hop)
                status = int(response.status_code)
                if 300 <= status < 400:
                    if redirect_count >= MAX_MEDIA_REDIRECTS:
                        raise RuntimeError("Pixiv media redirect limit was exceeded")
                    location = str(response.headers.get("Location") or "").strip()
                    if not location:
                        raise RuntimeError("Pixiv media redirect has no Location")
                    next_url = urljoin(final_hop.normalized_url, location)
                    next_hop = policy.resolve_and_validate(next_url)
                    validate_pixiv_media_target(next_hop)
                    current_url = next_hop.normalized_url
                    continue
                if not 200 <= status < 300:
                    raise RuntimeError(f"Pixiv media request failed with status {status}")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        if int(content_length) > max_bytes:
                            raise RuntimeError("Pixiv media response exceeds its object budget")
                    except ValueError:
                        pass
                payload = bytearray()
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    self._remaining_data_timeout(deadline, timeout)
                    if not chunk:
                        continue
                    if len(payload) + len(chunk) > max_bytes:
                        raise RuntimeError("Pixiv media response exceeds its object budget")
                    payload.extend(chunk)
                if not payload:
                    raise RuntimeError("Pixiv media response is empty")
                self._remaining_data_timeout(deadline, timeout)
                return bytes(payload)
            finally:
                response.close()
        raise RuntimeError("Pixiv media redirect limit was exceeded")

    def _request_approved_target(self, approved, *, headers, timeout, deadline):
        return _PinnedHTTPSResponse.open(
            approved,
            headers=headers,
            deadline=deadline,
            clock=self._monotonic,
            timeout=timeout,
        )

    def _remaining_data_timeout(self, deadline, configured):
        return _remaining_timeout(deadline, self._monotonic, configured)

    def _monotonic(self):
        return time.monotonic()

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
            payload = read_bounded_json_object(path)
            if payload.get("state_version") != STATE_VERSION:
                return None
            if payload.get("day_key") != self._day_key():
                return None
            return payload
        except RuntimeError:
            raise
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
        value = str(os.getenv("PIXIV_PHPSESSID", "") or "").strip()
        if value:
            return value
        if device_config is not None and hasattr(device_config, "load_env_key"):
            try:
                value = device_config.load_env_key("PIXIV_PHPSESSID") or ""
            except Exception as exc:
                logger.warning("Could not read PIXIV_PHPSESSID from device config: %s", exc)
        return str(value or "").strip()

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
        return self.cache_dir(
            env_var="INKYPI_PIXIV_R18_CACHE",
            leaf=".pixiv_r18_ranking_cache",
            create=False,
        )

    def _read_state(self):
        path = self._state_path()
        try:
            if path.is_file():
                state = read_bounded_json_object(path)
                return state if isinstance(state, dict) else {}
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning("Could not read Pixiv R-18 state %s: %s", path, exc)
        return {}

    def _write_state(self, state):
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(path, state if isinstance(state, dict) else {})

    def _atomic_write_json(self, path, payload):
        atomic_write_bounded_json(path, payload)

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
        font = get_base_ui_font(int(size), bold=bool(bold))
        if self._font_supports_text(font, JAPANESE_FONT_SAMPLE):
            return font

        paths = []
        if bold:
            paths.extend(
                [
                    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
                    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                ]
            )
        paths.extend(JAPANESE_FONT_PATHS)
        for path in paths:
            try:
                candidate = ImageFont.truetype(path, size=int(size))
                if self._font_supports_text(candidate, JAPANESE_FONT_SAMPLE):
                    return candidate
            except Exception:
                continue
        return font

    @staticmethod
    def _font_supports_text(font, text):
        if font is None or not hasattr(font, "getmask"):
            return False
        try:
            replacement = font.getmask("\ufffd")
            replacement_signature = (replacement.size, bytes(replacement))
            for char in str(text or ""):
                if char.isspace():
                    continue
                glyph = font.getmask(char)
                if glyph.getbbox() is None:
                    return False
                if (glyph.size, bytes(glyph)) == replacement_signature:
                    return False
        except Exception:
            return False
        return True

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


def _resolution_provenance(resolution):
    resolution = resolution or {}
    return {
        "requested_mode": str(resolution.get("requested_mode") or ""),
        "effective_mode": str(resolution.get("effective_mode") or ""),
        "content_rating": str(resolution.get("content_rating") or "sfw"),
        "authenticated": resolution.get("authenticated") is True,
        "healthy_r18": resolution.get("healthy_r18") is True,
        "source_status": str(resolution.get("source_status") or "unavailable"),
    }


def _provenance_identity(provenance):
    return (
        str((provenance or {}).get("effective_mode") or ""),
        str((provenance or {}).get("content_rating") or ""),
        bool((provenance or {}).get("authenticated")),
    )


def _stat_is_reparse(value):
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(value, "st_file_attributes", 0)
    return stat.S_ISLNK(value.st_mode) or bool(flag and attributes & flag)
