"""Bounded, provider-free presentation bank for DailyArt every-refresh mode."""

from __future__ import annotations

import ipaddress
import json
import os
import random
import secrets
import stat
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from PIL import Image, ImageOps

from utils.atomic_file import atomic_write_json
from utils.cache_manager import CacheBudget, cache_namespace_for_directory
from utils.safe_image import ImageLimitError, ImageLimits, safe_open_image


SCHEMA_VERSION = 1
READY_TARGET = 16
REFILL_THRESHOLD = 6
MAX_PROFILES = 64
MAX_RECORDS_PER_PROFILE = READY_TARGET
MAX_SEEN_ARTWORKS = 5000
MAX_DATE_BUCKETS = 366
MAX_STATE_BYTES = 4 * 1024 * 1024
MEDIA_MAX_AGE_SECONDS = 48 * 60 * 60
MEDIA_MAX_FILES = 48
MEDIA_MAX_BYTES = 96 * 1024 * 1024
MEDIA_MAX_OBJECT_BYTES = 12 * 1024 * 1024
MEDIA_MAX_DIMENSION = 8192
MEDIA_MAX_PIXELS = 32_000_000
MEDIA_IMAGE_LIMITS = ImageLimits(
    max_bytes=MEDIA_MAX_OBJECT_BYTES,
    max_width=MEDIA_MAX_DIMENSION,
    max_height=MEDIA_MAX_DIMENSION,
    max_pixels=MEDIA_MAX_PIXELS,
    allowed_formats=frozenset({"PNG"}),
)
MEDIA_BUDGET = CacheBudget(
    max_age_seconds=MEDIA_MAX_AGE_SECONDS,
    max_files=MEDIA_MAX_FILES,
    max_bytes=MEDIA_MAX_BYTES,
)
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
DEFAULT_FONT_FAMILY = "Microsoft YaHei"

_HEX = frozenset("0123456789abcdef")
_PROFILE_KEYS = {
    "profile_fingerprint",
    "settings_fingerprint",
    "settings_key",
    "instance_uuid",
    "date_key",
    "date_buckets",
    "records",
    "current_selection",
    "pending_selection",
    "last_applied_origin_commit_id",
    "last_applied_request_id",
    "last_provider_attempt_at",
    "last_provider_status",
    "refill_in_progress",
    "last_used_at",
}
_TEXT_LIMITS = {
    "source": 32,
    "source_label": 200,
    "artwork_id": 240,
    "title": 600,
    "artist": 400,
    "date": 120,
    "medium": 500,
    "museum": 400,
    "rights": 500,
    "culture": 300,
}
_PROVIDER_HOST_SUFFIXES = {
    "met": ("metmuseum.org",),
    "artic": ("artic.edu",),
    "harvard": ("harvard.edu",),
}
_FEDERATED_PUBLIC_SOURCES = frozenset({"europeana"})


def settings_key(settings, enabled_sources):
    """Return the stable content/layout identity, excluding runtime and secrets."""

    settings = settings or {}
    payload = {
        "source_mode": str(settings.get("sourceMode") or "all").strip().lower(),
        "enabled_sources": sorted({str(value).strip().lower() for value in enabled_sources if value}),
        "query_terms": _normalized_query_terms(settings.get("queryTerms")),
        "source_limit": _bounded_int(settings.get("sourceLimit"), 12, 3, 50),
        "max_attempts": _bounded_int(settings.get("maxAttempts"), 10, 1, 40),
        "layout_mode": _layout_mode(settings),
        "gallery_count": _bounded_int(settings.get("galleryCount"), 3, 1, 4),
        "fit_mode": str(settings.get("fitMode") or "contain").strip().lower(),
        "background_style": str(settings.get("backgroundStyle") or "blur").strip().lower(),
        "background_color": str(settings.get("backgroundColor") or "warm").strip().lower(),
        "show_caption": _enabled(settings.get("showCaption"), default=False),
        "font_family": str(settings.get("fontFamily") or DEFAULT_FONT_FAMILY).strip(),
        "iiif_width": _bounded_int(settings.get("iiifWidth"), 1200, 600, 2400),
        "max_image_bytes": _bounded_int(
            settings.get("maxImageBytes"),
            12_000_000,
            1_000_000,
            25_000_000,
        ),
        "image_timeout_seconds": _bounded_int(
            settings.get("imageTimeoutSeconds"),
            14,
            4,
            40,
        ),
    }
    return _json_hash(payload)


def settings_fingerprint(settings, dimensions, date_key, enabled_sources):
    return _json_hash(
        {
            "settings_key": settings_key(settings, enabled_sources),
            "dimensions": [int(dimensions[0]), int(dimensions[1])],
            "date_key": str(date_key),
        }
    )


def instance_profile_fingerprint(base_fingerprint, instance_uuid):
    return _json_hash(
        {
            "settings_fingerprint": base_fingerprint,
            "instance_uuid": instance_uuid,
        }
    )


def _json_hash(payload):
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class DailyArtPresentationBank:
    """Own normalized artwork/media records partitioned by trusted instance."""

    def __init__(
        self,
        state_path,
        media_dir,
        *,
        fingerprint,
        base_fingerprint,
        profile_settings_key,
        instance_uuid,
        date_key,
    ):
        self.state_path = Path(state_path)
        self.media_dir = Path(media_dir)
        self.fingerprint = fingerprint
        self.base_fingerprint = base_fingerprint
        self.profile_settings_key = profile_settings_key
        self.instance_uuid = instance_uuid
        self.date_key = date_key
        self.media = cache_namespace_for_directory(self.media_dir, MEDIA_BUDGET)

    def load_for_data(self):
        document = self._migrate_document(self._read_document())
        profiles = document["profiles"]
        profile = profiles.get(self.fingerprint)
        if not isinstance(profile, dict):
            self._make_profile_room(document, required_slots=1)
            profile = self._empty_profile(document["date_buckets"])
            profiles[self.fingerprint] = profile
        else:
            profile = self._normalize_profile(profile, document["date_buckets"])
            profiles[self.fingerprint] = profile
            self._make_profile_room(document, required_slots=0)
        document["instance_profiles"][self.instance_uuid] = self.fingerprint
        document["active_fingerprint"] = self.fingerprint
        profile["last_used_at"] = _utc_now()
        return document, profile

    def load_warm(self):
        document = self._migrate_document(self._read_document())
        fingerprint = document["instance_profiles"].get(self.instance_uuid)
        if fingerprint != self.fingerprint:
            raise RuntimeError("DailyArt presentation bank is cold for this plugin instance")
        profile = document["profiles"].get(fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("DailyArt presentation bank is cold for these settings")
        profile = self._normalize_profile(profile, document["date_buckets"])
        if profile["profile_fingerprint"] != self.fingerprint:
            raise RuntimeError("DailyArt presentation bank fingerprint does not match")
        profile["last_used_at"] = _utc_now()
        document["profiles"][self.fingerprint] = profile
        self._make_profile_room(document, required_slots=0)
        return document, profile

    def load_receipt_profile(self, request_id):
        document = self._migrate_document(self._read_document())
        profile = document["profiles"].get(self.fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("DailyArt receipt profile is unavailable")
        profile = self._normalize_profile(profile, document["date_buckets"])
        pending = profile.get("pending_selection")
        if not isinstance(pending, dict) or pending.get("request_id") != request_id:
            raise RuntimeError("DailyArt receipt no longer matches a pending selection")
        document["profiles"][self.fingerprint] = profile
        return document, profile

    def save(self, document):
        document["presentation_schema_version"] = SCHEMA_VERSION
        validate_state_payload_size(document)
        _atomic_write_bounded_json(self.state_path, document)

    def ready_records(self, profile, *, prune):
        ready = []
        survivors = []
        protected = self._protected_record_keys(profile)
        for record in profile["records"]:
            try:
                self._ensure_record_fresh(record)
                self.load_media(record)
            except RuntimeError:
                if record["record_key"] in protected:
                    survivors.append(record)
                continue
            if record.get("date_key") == self.date_key:
                ready.append(record)
                survivors.append(record)
            elif record["record_key"] in protected:
                survivors.append(record)
        if prune and len(survivors) != len(profile["records"]):
            profile["records"] = survivors[-MAX_RECORDS_PER_PROFILE:]
        return ready

    def protected_records(self, profile):
        records = {record["record_key"]: record for record in profile["records"]}
        protected = []
        seen = set()
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if not isinstance(selection, dict):
                continue
            for record_key in selection.get("record_keys", []):
                record = records.get(record_key)
                if record is None:
                    raise RuntimeError("DailyArt protected artwork metadata is missing")
                if record_key not in seen:
                    seen.add(record_key)
                    protected.append(record)
        return protected

    def ingest(self, profile, candidate, image, *, downloaded_at=None):
        normalized = self.normalize_candidate(candidate)
        normalized_image = self._normalize_media_image(image)
        media_key = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        output = BytesIO()
        normalized_image.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("DailyArt media exceeds its object budget")
        self.media.put_bytes(media_key, payload, suffix=".png")
        record_key = sha256(
            f"{normalized['artwork_id']}\0{normalized['image_url']}".encode("utf-8")
        ).hexdigest()
        record = {
            **normalized,
            "record_key": record_key,
            "media_key": media_key,
            "width": normalized_image.width,
            "height": normalized_image.height,
            "downloaded_at": downloaded_at or _utc_now(),
            "date_key": self.date_key,
        }
        records = list(profile["records"])
        for index, item in enumerate(records):
            if item.get("record_key") == record_key:
                records[index] = record
                break
        else:
            records.append(record)
        protected = self._protected_record_keys(profile)
        while len(records) > MAX_RECORDS_PER_PROFILE:
            victim = next(
                (
                    index
                    for index, item in enumerate(records)
                    if item.get("record_key") not in protected
                    and item.get("record_key") != record_key
                ),
                None,
            )
            if victim is None:
                raise RuntimeError("DailyArt protected metadata fills the record budget")
            records.pop(victim)
        profile["records"] = records
        return record

    def recover_media(self, profile, record, image, *, downloaded_at=None):
        normalized = self.normalize_candidate(record)
        expected_media_key = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        if record.get("media_key") != expected_media_key:
            raise RuntimeError("DailyArt protected media identity does not match its URL")
        normalized_image = self._normalize_media_image(image)
        output = BytesIO()
        normalized_image.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("DailyArt recovered media exceeds its object budget")
        self.media.put_bytes(expected_media_key, payload, suffix=".png")
        updated = {
            **record,
            "width": normalized_image.width,
            "height": normalized_image.height,
            "downloaded_at": downloaded_at or _utc_now(),
        }
        for index, candidate in enumerate(profile["records"]):
            if candidate.get("record_key") == record.get("record_key"):
                profile["records"][index] = updated
                return updated
        raise RuntimeError("DailyArt protected metadata disappeared during recovery")

    def normalize_candidate(self, candidate):
        if not isinstance(candidate, dict):
            raise RuntimeError("DailyArt artwork metadata is invalid")
        artwork_id = _bounded_text(candidate.get("artwork_id"), _TEXT_LIMITS["artwork_id"])
        if not artwork_id:
            raise RuntimeError("DailyArt artwork ID is missing")
        normalized = {
            "image_url": self._normalize_source_url(candidate.get("image_url"), required=True),
            "page_url": self._normalize_source_url(candidate.get("page_url"), required=False),
        }
        for key, limit in _TEXT_LIMITS.items():
            normalized[key] = _bounded_text(candidate.get(key), limit)
        normalized["artwork_id"] = artwork_id
        image_host = (urlparse(normalized["image_url"]).hostname or "").lower()
        if not provider_media_host_allowed(normalized["source"], image_host):
            raise RuntimeError("DailyArt media URL authority is not allowed for its provider")
        return normalized

    def choose_selection(self, document, profile, ready, layout_mode, gallery_count):
        if not ready:
            raise RuntimeError("DailyArt presentation bank has no ready artwork records")
        current_keys = set((profile.get("current_selection") or {}).get("record_keys", []))
        pending_keys = set((profile.get("pending_selection") or {}).get("record_keys", []))
        bucket = profile.get("date_buckets", {}).get(self.date_key, {})
        seen_ids = {str(value) for value in bucket.get("seen_artwork_ids", [])}
        candidates = [
            record
            for record in ready
            if record["record_key"] not in current_keys
            and record["record_key"] not in pending_keys
            and record["artwork_id"] not in seen_ids
        ]
        reset_seen = False
        if not candidates:
            candidates = [
                record
                for record in ready
                if record["record_key"] not in current_keys
                and record["record_key"] not in pending_keys
            ]
            reset_seen = bool(candidates)
        if not candidates:
            candidates = list(ready)
            reset_seen = bool(seen_ids)
        random.shuffle(candidates)

        if layout_mode == "single":
            chosen = candidates[:1]
            layout = "single"
        elif layout_mode == "gallery":
            chosen = candidates[:gallery_count]
            layout = "gallery"
        else:
            portraits = [
                record
                for record in candidates
                if int(record.get("height", 0)) >= int(record.get("width", 0)) * 1.08
            ]
            if portraits:
                chosen = portraits[:gallery_count]
                layout = "gallery"
            else:
                chosen = candidates[:1]
                layout = "single"
        if not chosen:
            raise RuntimeError("DailyArt presentation bank could not choose artwork")
        return {
            "record_keys": [record["record_key"] for record in chosen],
            "request_id": None,
            "date_key": self.date_key,
            "layout": layout,
            "reset_seen": reset_seen,
        }

    def ensure_current(self, document, profile, ready, layout_mode, gallery_count):
        valid_keys = {record["record_key"] for record in ready}
        current = profile.get("current_selection")
        if self._selection_is_valid(current, valid_keys):
            return current
        current = self.choose_selection(document, profile, ready, layout_mode, gallery_count)
        profile["current_selection"] = current
        self.save(document)
        return current

    def selection_records(self, profile, selection, *, load_media):
        if not isinstance(selection, dict):
            raise RuntimeError("DailyArt bank selection is missing")
        records = {record["record_key"]: record for record in profile["records"]}
        selected = []
        for record_key in selection.get("record_keys", []):
            record = records.get(record_key)
            if record is None:
                raise RuntimeError("DailyArt selected artwork metadata is missing")
            self._ensure_record_fresh(record)
            image = self.load_media(record) if load_media else None
            selected.append((record, image))
        if not selected:
            raise RuntimeError("DailyArt bank selection is empty")
        return selected

    def apply_trusted_origin(self, document, profile, request):
        if profile["last_applied_origin_commit_id"] == request.origin_display_commit_id:
            return None
        committed = self._commit_selection(
            document,
            profile,
            profile.get("current_selection"),
            request.requested_at,
        )
        profile["last_applied_origin_commit_id"] = request.origin_display_commit_id
        self.save(document)
        return committed

    def pending_for_request(self, profile, request_id):
        pending = profile.get("pending_selection")
        if isinstance(pending, dict) and pending.get("request_id") == request_id:
            return pending
        return None

    def set_pending(self, document, profile, request, selection):
        pending = {
            "request_id": request.request_id,
            "origin_display_commit_id": request.origin_display_commit_id,
            "requested_at": request.requested_at,
            "record_keys": list(selection["record_keys"]),
            "date_key": selection["date_key"],
            "layout": selection["layout"],
            "reset_seen": bool(selection.get("reset_seen")),
        }
        profile["pending_selection"] = pending
        self.save(document)
        return pending

    def reconcile_receipt(self, document, profile, receipt):
        pending = profile.get("pending_selection")
        if not isinstance(pending, dict) or pending.get("request_id") != receipt.request_id:
            return None
        if pending.get("origin_display_commit_id") == receipt.display_commit_id:
            return None
        if profile.get("last_applied_request_id") == receipt.request_id:
            return None
        selected = self.selection_records(profile, pending, load_media=True)
        records = [record for record, _image in selected]
        _commit_records(profile, records, pending, receipt.committed_at)
        profile["current_selection"] = {
            "record_keys": list(pending["record_keys"]),
            "request_id": receipt.request_id,
            "date_key": pending["date_key"],
            "layout": pending["layout"],
            "reset_seen": False,
        }
        profile["pending_selection"] = None
        profile["last_applied_request_id"] = receipt.request_id
        self.save(document)
        return records

    def load_media(self, record):
        self._ensure_record_fresh(record)
        media_key = record.get("media_key")
        if not _valid_hash(media_key):
            raise RuntimeError("DailyArt media key is invalid")
        target = self.media.path(media_key, suffix=".png")
        try:
            info = target.lstat()
        except OSError as exc:
            raise RuntimeError("DailyArt media is missing") from exc
        if target.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("DailyArt media is not a regular file")
        if info.st_size <= 0 or info.st_size > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("DailyArt media exceeds its object budget")
        payload = self.media.get_bytes(media_key, suffix=".png")
        if payload is None or not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("DailyArt media is unavailable")
        try:
            source = safe_open_image(payload, limits=MEDIA_IMAGE_LIMITS)
            self._validate_media_dimensions(source.size)
            return self._normalize_media_image(source)
        except ImageLimitError as exc:
            raise RuntimeError(
                "DailyArt media dimensions or safety limits were exceeded"
            ) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("DailyArt media could not be decoded") from exc

    def _read_document(self):
        if not _path_exists_no_follow(self.state_path):
            return {}
        return read_bounded_json_object(self.state_path)

    def _migrate_document(self, source):
        document = dict(source)
        document["presentation_schema_version"] = SCHEMA_VERSION
        profiles = document.get("profiles")
        document["profiles"] = dict(profiles) if isinstance(profiles, dict) else {}
        for fingerprint, candidate in list(document["profiles"].items()):
            if not isinstance(fingerprint, str) or not isinstance(candidate, dict):
                document["profiles"].pop(fingerprint, None)
                continue
            if _parse_datetime(candidate.get("last_used_at")) is None:
                candidate = dict(candidate)
                candidate["last_used_at"] = "1970-01-01T00:00:00+00:00"
                document["profiles"][fingerprint] = candidate
        mappings = document.get("instance_profiles")
        document["instance_profiles"] = dict(mappings) if isinstance(mappings, dict) else {}
        document["instance_profiles"] = {
            key: value
            for key, value in document["instance_profiles"].items()
            if isinstance(key, str)
            and isinstance(value, str)
            and value in document["profiles"]
            and document["profiles"][value].get("instance_uuid") == key
        }
        document.setdefault("active_fingerprint", None)
        document["date_buckets"] = _bounded_date_buckets(document.get("date_buckets"))
        return document

    def _empty_profile(self, legacy_date_buckets=None):
        return {
            "profile_fingerprint": self.fingerprint,
            "settings_fingerprint": self.base_fingerprint,
            "settings_key": self.profile_settings_key,
            "instance_uuid": self.instance_uuid,
            "date_key": self.date_key,
            "date_buckets": deepcopy(_bounded_date_buckets(legacy_date_buckets)),
            "records": [],
            "current_selection": None,
            "pending_selection": None,
            "last_applied_origin_commit_id": None,
            "last_applied_request_id": None,
            "last_provider_attempt_at": None,
            "last_provider_status": None,
            "refill_in_progress": False,
            "last_used_at": _utc_now(),
        }

    def _normalize_profile(self, source, legacy_date_buckets=None):
        profile = self._empty_profile(legacy_date_buckets)
        for key in _PROFILE_KEYS:
            if key in source:
                profile[key] = source[key]
        profile["profile_fingerprint"] = self.fingerprint
        profile["settings_fingerprint"] = self.base_fingerprint
        profile["settings_key"] = self.profile_settings_key
        profile["instance_uuid"] = self.instance_uuid
        profile["date_key"] = str(source.get("date_key") or self.date_key)
        attempted_at = _parse_datetime(profile.get("last_provider_attempt_at"))
        profile["last_provider_attempt_at"] = (
            attempted_at.isoformat() if attempted_at is not None else None
        )
        status = str(profile.get("last_provider_status") or "").strip().lower()
        profile["last_provider_status"] = status if status in {"success", "empty", "error"} else None
        source_buckets = source.get("date_buckets")
        if isinstance(source_buckets, dict):
            profile["date_buckets"] = deepcopy(_bounded_date_buckets(source_buckets))
        normalized_records = []
        for record in profile.get("records") or []:
            if not self._valid_record(record):
                continue
            normalized_records.append({**record, **self.normalize_candidate(record)})
        profile["records"] = normalized_records[-MAX_RECORDS_PER_PROFILE:]
        profile["refill_in_progress"] = profile.get("refill_in_progress") is True
        valid_keys = {record["record_key"] for record in profile["records"]}
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if selection is None:
                continue
            if not self._selection_is_valid(selection, valid_keys):
                raise RuntimeError("DailyArt protected selection metadata is invalid")
        return profile

    def _valid_record(self, record):
        if not isinstance(record, dict):
            return False
        structure = (
            _valid_hash(record.get("record_key"))
            and _valid_hash(record.get("media_key"))
            and isinstance(record.get("artwork_id"), str)
            and bool(record["artwork_id"])
            and isinstance(record.get("date_key"), str)
            and isinstance(record.get("downloaded_at"), str)
            and _parse_datetime(record.get("downloaded_at")) is not None
            and isinstance(record.get("width"), int)
            and isinstance(record.get("height"), int)
        )
        if not structure:
            return False
        try:
            normalized = self.normalize_candidate(record)
            self._validate_media_dimensions((record["width"], record["height"]))
        except RuntimeError:
            return False
        expected_media_key = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        expected_record_key = sha256(
            f"{normalized['artwork_id']}\0{normalized['image_url']}".encode("utf-8")
        ).hexdigest()
        return record["media_key"] == expected_media_key and record["record_key"] == expected_record_key

    def _selection_is_valid(self, selection, valid_keys):
        if not isinstance(selection, dict):
            return False
        keys = selection.get("record_keys")
        if not (
            isinstance(keys, list)
            and bool(keys)
            and len(keys) <= 4
            and all(isinstance(key, str) and key in valid_keys for key in keys)
        ):
            return False
        request_id = selection.get("request_id")
        if request_id is not None and not _valid_request_id(request_id):
            return False
        return (
            isinstance(selection.get("date_key"), str)
            and selection.get("layout") in {"single", "gallery"}
        )

    def _make_profile_room(self, document, *, required_slots):
        profiles = document["profiles"]
        mappings = document["instance_profiles"]
        while len(profiles) + required_slots > MAX_PROFILES:
            protected = set(mappings.values())
            protected.add(self.fingerprint)
            protected.update(
                fingerprint
                for fingerprint, profile in profiles.items()
                if isinstance(profile, dict) and isinstance(profile.get("pending_selection"), dict)
            )
            candidates = [
                (str(profile.get("last_used_at") or ""), fingerprint)
                for fingerprint, profile in profiles.items()
                if fingerprint not in protected and isinstance(profile, dict)
            ]
            if not candidates:
                raise RuntimeError("DailyArt presentation profile capacity is fully active")
            _last_used_at, victim = min(candidates)
            profiles.pop(victim, None)
            for instance_uuid, fingerprint in list(mappings.items()):
                if fingerprint == victim:
                    mappings.pop(instance_uuid, None)

    def _protected_record_keys(self, profile):
        protected = set()
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if isinstance(selection, dict):
                protected.update(selection.get("record_keys", []))
        return protected

    def _ensure_record_fresh(self, record):
        downloaded_at = _parse_datetime(record.get("downloaded_at"))
        now = datetime.now(timezone.utc)
        if downloaded_at is None or (now - downloaded_at).total_seconds() > MEDIA_MAX_AGE_SECONDS:
            raise RuntimeError("DailyArt media record is expired")

    def _commit_selection(self, document, profile, selection, committed_at):
        if not isinstance(selection, dict):
            return None
        selected = self.selection_records(profile, selection, load_media=True)
        records = [record for record, _image in selected]
        _commit_records(profile, records, selection, committed_at)
        return records

    def _normalize_source_url(self, value, *, required):
        if value in {None, ""} and not required:
            return ""
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("DailyArt source URL is missing")
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("DailyArt source URL authority is invalid") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or _unsafe_host(host)
        ):
            raise RuntimeError("DailyArt source URL is outside approved network authorities")
        return urlunparse(("https", host, parsed.path or "/", "", parsed.query, ""))

    def _normalize_media_image(self, image):
        if not isinstance(image, Image.Image):
            raise RuntimeError("DailyArt media is not an image")
        self._validate_media_dimensions(image.size)
        image = ImageOps.exif_transpose(image)
        self._validate_media_dimensions(image.size)
        return image.convert("RGB")

    def _validate_media_dimensions(self, size):
        width, height = size
        if (
            width <= 0
            or height <= 0
            or width > MEDIA_MAX_DIMENSION
            or height > MEDIA_MAX_DIMENSION
            or width * height > MEDIA_MAX_PIXELS
        ):
            raise RuntimeError("DailyArt media dimensions exceed the safety limit")


def _commit_records(profile, records, selection, committed_at):
    incoming_at = _parse_datetime(committed_at)
    if incoming_at is None:
        raise RuntimeError("DailyArt display receipt timestamp is invalid")
    date_key = selection.get("date_key")
    if not isinstance(date_key, str) or not date_key:
        raise RuntimeError("DailyArt display receipt date bucket is invalid")
    buckets = profile.setdefault("date_buckets", {})
    bucket = buckets.setdefault(date_key, {})
    if selection.get("reset_seen"):
        bucket["seen_artwork_ids"] = []
    seen = [str(value) for value in bucket.get("seen_artwork_ids", []) if value]
    for record in records:
        artwork_id = record["artwork_id"]
        if artwork_id not in seen:
            seen.append(artwork_id)
    bucket["seen_artwork_ids"] = seen[-MAX_SEEN_ARTWORKS:]
    existing_at = _parse_datetime(bucket.get("committed_at"))
    if existing_at is None or incoming_at >= existing_at:
        bucket["last_artwork_id"] = records[-1]["artwork_id"]
        bucket["committed_at"] = incoming_at.isoformat()
        bucket["updated_at"] = incoming_at.isoformat()
    profile["date_buckets"] = _bounded_date_buckets(buckets)


def validate_state_payload_size(payload):
    validate_state_shape(payload)
    try:
        encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("DailyArt state could not be encoded safely") from exc
    if len(encoded) > MAX_STATE_BYTES:
        raise RuntimeError("DailyArt state exceeds the size limit")
    return len(encoded)


def validate_state_shape(payload):
    if not isinstance(payload, dict):
        raise RuntimeError("DailyArt state must be an object")
    profiles = payload.get("profiles")
    if profiles is not None and not isinstance(profiles, dict):
        raise RuntimeError("DailyArt state profiles must be an object")
    if isinstance(profiles, dict):
        if len(profiles) > MAX_PROFILES:
            raise RuntimeError("DailyArt profile capacity exceeds the limit")
        for profile in profiles.values():
            if not isinstance(profile, dict):
                raise RuntimeError("DailyArt profile must be an object")
            records = profile.get("records", [])
            if not isinstance(records, list) or len(records) > MAX_RECORDS_PER_PROFILE:
                raise RuntimeError("DailyArt record capacity exceeds the limit")
            if len(profile) > len(_PROFILE_KEYS):
                raise RuntimeError("DailyArt profile metadata exceeds the field limit")
            if not all(isinstance(record, dict) and len(record) <= 28 for record in records):
                raise RuntimeError("DailyArt record metadata exceeds the field limit")
            for name in ("current_selection", "pending_selection"):
                selection = profile.get(name)
                if selection is None:
                    continue
                if not isinstance(selection, dict) or len(selection) > 9:
                    raise RuntimeError("DailyArt selection metadata exceeds the field limit")
                keys = selection.get("record_keys")
                if not (
                    isinstance(keys, list)
                    and 0 < len(keys) <= 4
                    and all(_valid_hash(key) for key in keys)
                ):
                    raise RuntimeError("DailyArt selection capacity exceeds the limit")
            _validate_date_buckets(profile.get("date_buckets"), label="profile")
    mappings = payload.get("instance_profiles")
    if mappings is not None and not isinstance(mappings, dict):
        raise RuntimeError("DailyArt instance profiles must be an object")
    if isinstance(mappings, dict):
        if len(mappings) > MAX_PROFILES:
            raise RuntimeError("DailyArt instance profile capacity exceeds the limit")
        surviving = profiles if isinstance(profiles, dict) else {}
        if any(
            not isinstance(instance_uuid, str)
            or not isinstance(fingerprint, str)
            or fingerprint not in surviving
            for instance_uuid, fingerprint in mappings.items()
        ):
            raise RuntimeError("DailyArt instance profile references missing profile")
    _validate_date_buckets(payload.get("date_buckets"), label="legacy")


def _validate_date_buckets(buckets, *, label):
    if buckets is not None and not isinstance(buckets, dict):
        raise RuntimeError(f"DailyArt {label} date buckets must be an object")
    if not isinstance(buckets, dict):
        return
    if len(buckets) > MAX_DATE_BUCKETS:
        raise RuntimeError(f"DailyArt {label} date bucket capacity exceeds the limit")
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            raise RuntimeError(f"DailyArt {label} date bucket must be an object")
        seen = bucket.get("seen_artwork_ids", [])
        if not isinstance(seen, list) or len(seen) > MAX_SEEN_ARTWORKS:
            raise RuntimeError("DailyArt seen history exceeds the limit")
        if any(
            not isinstance(value, str) or len(value) > _TEXT_LIMITS["artwork_id"]
            for value in seen
        ):
            raise RuntimeError("DailyArt seen metadata exceeds the limit")


def read_bounded_json_object(path):
    path = Path(path)
    if os.name == "posix":
        payload = _read_bounded_json_posix(path)
    else:
        payload = _read_bounded_json_fallback(path)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("DailyArt state could not be read safely") from exc
    if not isinstance(value, dict):
        raise RuntimeError("DailyArt state must be an object")
    return value


def _read_bounded_json_posix(path):
    root_fd = None
    file_fd = None
    try:
        root_fd, root_stat = _open_posix_directory_chain(path.parent, create=False)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
        )
        file_fd = os.open(path.name, flags, dir_fd=root_fd)
        file_before = os.fstat(file_fd)
        path_before = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
        _validate_state_file_stat(file_before)
        _validate_state_file_stat(path_before)
        if not _same_file_snapshot(file_before, path_before):
            raise RuntimeError("DailyArt state identity changed before read")
        payload = _read_fd_bounded(file_fd)
        file_after = os.fstat(file_fd)
        path_after = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
        if (
            not _same_file_snapshot(file_before, file_after)
            or not _same_file_snapshot(file_before, path_after)
            or not _bound_root_still_matches(path.parent, root_stat)
        ):
            raise RuntimeError("DailyArt state identity changed during read")
        return payload
    except RuntimeError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise RuntimeError("DailyArt state could not be read safely") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if root_fd is not None:
            os.close(root_fd)


def _read_bounded_json_fallback(path):
    file_fd = None
    try:
        root_before = _validate_fallback_directory_chain(path.parent, create=False)
        path_before = os.lstat(path)
        _validate_state_file_stat(path_before)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        file_fd = os.open(path, flags)
        file_before = os.fstat(file_fd)
        _validate_state_file_stat(file_before)
        if not _same_file_snapshot(file_before, path_before):
            raise RuntimeError("DailyArt state identity changed before read")
        payload = _read_fd_bounded(file_fd)
        file_after = os.fstat(file_fd)
        path_after = os.lstat(path)
        root_after = _validate_fallback_directory_chain(path.parent, create=False)
        if (
            not _same_file_snapshot(file_before, file_after)
            or not _same_file_snapshot(file_before, path_after)
            or not _same_identity(root_before, root_after)
        ):
            raise RuntimeError("DailyArt state identity changed during read")
        return payload
    except RuntimeError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise RuntimeError("DailyArt state could not be read safely") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)


def _atomic_write_bounded_json(path, document):
    path = Path(os.path.abspath(os.fspath(path)))
    if path.name in {"", ".", ".."}:
        raise RuntimeError("DailyArt state target name is unsafe")
    try:
        payload = (json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("DailyArt state could not be encoded safely") from exc
    if len(payload) > MAX_STATE_BYTES:
        raise RuntimeError("DailyArt state exceeds the size limit")
    if os.name == "posix":
        _atomic_write_bounded_json_posix(path, payload)
    else:
        _atomic_write_bounded_json_fallback(path, document)


def _atomic_write_bounded_json_posix(path, payload):
    root_fd = None
    temp_fd = None
    temp_name = None
    try:
        root_fd, root_stat = _open_posix_directory_chain(path.parent, create=True)
        try:
            target_stat = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None:
            _validate_state_file_stat(target_stat)
        temp_name = f".{path.name}.{secrets.token_hex(12)}.tmp"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
        )
        temp_fd = os.open(temp_name, flags, 0o600, dir_fd=root_fd)
        os.fchmod(temp_fd, 0o600)
        _write_all(temp_fd, payload)
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = None
        if not _bound_root_still_matches(path.parent, root_stat):
            raise RuntimeError("DailyArt state root identity changed before publish")
        os.replace(
            temp_name,
            path.name,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        temp_name = None
        os.fsync(root_fd)
        if not _bound_root_still_matches(path.parent, root_stat):
            raise RuntimeError("DailyArt state root identity changed during publish")
    except RuntimeError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise RuntimeError("DailyArt state could not be written safely") from exc
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_name is not None and root_fd is not None:
            try:
                os.unlink(temp_name, dir_fd=root_fd)
            except OSError:
                pass
        if root_fd is not None:
            os.close(root_fd)


def _atomic_write_bounded_json_fallback(path, document):
    try:
        root_before = _validate_fallback_directory_chain(path.parent, create=True)
        try:
            target_before = os.lstat(path)
        except FileNotFoundError:
            target_before = None
        if target_before is not None:
            _validate_state_file_stat(target_before)
        atomic_write_json(path, document, mode=0o600)
        root_after = _validate_fallback_directory_chain(path.parent, create=False)
        target_after = os.lstat(path)
        _validate_state_file_stat(target_after)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("DailyArt state root identity changed during publish")
    except RuntimeError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise RuntimeError("DailyArt state could not be written safely") from exc


def _open_posix_directory_chain(directory, *, create):
    absolute = Path(os.path.abspath(os.fspath(directory)))
    anchor = absolute.anchor
    if not anchor:
        raise RuntimeError("DailyArt state root must be absolute")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    current_fd = os.open(anchor, flags)
    try:
        for part in absolute.parts[1:]:
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=current_fd)
            next_stat = os.fstat(next_fd)
            if not _safe_directory_stat(next_stat):
                os.close(next_fd)
                raise RuntimeError("DailyArt state directory chain is unsafe")
            os.close(current_fd)
            current_fd = next_fd
        root_stat = os.fstat(current_fd)
        if not _safe_directory_stat(root_stat):
            raise RuntimeError("DailyArt state root is unsafe")
        result_fd = current_fd
        current_fd = None
        return result_fd, root_stat
    finally:
        if current_fd is not None:
            os.close(current_fd)


def _validate_fallback_directory_chain(directory, *, create):
    absolute = Path(os.path.abspath(os.fspath(directory)))
    if not absolute.anchor:
        raise RuntimeError("DailyArt state root must be absolute")
    current = Path(absolute.anchor)
    root_stat = os.lstat(current)
    if not _safe_directory_stat(root_stat):
        raise RuntimeError("DailyArt state directory anchor is unsafe")
    for part in absolute.parts[1:]:
        current = current / part
        try:
            current_stat = os.lstat(current)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(current, mode=0o700)
            except FileExistsError:
                pass
            current_stat = os.lstat(current)
        if not _safe_directory_stat(current_stat):
            raise RuntimeError("DailyArt state directory chain contains a reparse point")
        root_stat = current_stat
    confirm = os.lstat(absolute)
    if not _safe_directory_stat(confirm) or not _same_identity(root_stat, confirm):
        raise RuntimeError("DailyArt state root identity is unsafe")
    return root_stat


def _bound_root_still_matches(directory, expected):
    root_fd = None
    try:
        root_fd, current = _open_posix_directory_chain(directory, create=False)
        return _same_identity(expected, current)
    except (OSError, RuntimeError, TypeError, NotImplementedError):
        return False
    finally:
        if root_fd is not None:
            os.close(root_fd)


def _read_fd_bounded(fd):
    payload = bytearray()
    while len(payload) <= MAX_STATE_BYTES:
        chunk = os.read(fd, min(64 * 1024, MAX_STATE_BYTES + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
    if len(payload) > MAX_STATE_BYTES:
        raise RuntimeError("DailyArt state exceeds the size limit")
    return bytes(payload)


def _write_all(fd, payload):
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise RuntimeError("DailyArt state write was incomplete")
        offset += written


def _validate_state_file_stat(value):
    if not _safe_regular_file_stat(value):
        raise RuntimeError("DailyArt state path is not a safe regular file")
    if value.st_size < 0 or value.st_size > MAX_STATE_BYTES:
        raise RuntimeError("DailyArt state exceeds the size limit")


def _safe_regular_file_stat(value):
    return stat.S_ISREG(value.st_mode) and not _is_link_like(value)


def _safe_directory_stat(value):
    return stat.S_ISDIR(value.st_mode) and not _is_link_like(value)


def _is_link_like(value):
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(value, "st_file_attributes", 0)
    return stat.S_ISLNK(value.st_mode) or bool(reparse_flag and attributes & reparse_flag)


def _same_identity(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _same_file_snapshot(left, right):
    return (
        _same_identity(left, right)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _path_exists_no_follow(path):
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError("DailyArt state path could not be inspected") from exc
    return True


def _bounded_date_buckets(value):
    if not isinstance(value, dict):
        return {}
    candidates = {
        str(key): bucket
        for key, bucket in value.items()
        if isinstance(key, str) and isinstance(bucket, dict)
    }
    if len(candidates) <= MAX_DATE_BUCKETS:
        return candidates
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    ranked = sorted(
        candidates,
        key=lambda key: (
            _parse_datetime(candidates[key].get("committed_at")) or minimum,
            key,
        ),
    )
    retained = set(ranked[-MAX_DATE_BUCKETS:])
    return {key: bucket for key, bucket in candidates.items() if key in retained}


def _unsafe_host(host):
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return not address.is_global


def provider_media_host_allowed(source, hostname):
    """Apply source-specific authority rules after DNS/public-address validation."""

    source = str(source or "").strip().lower()
    hostname = str(hostname or "").strip().rstrip(".").lower()
    if not hostname:
        return False
    if source in _FEDERATED_PUBLIC_SOURCES:
        # Europeana intentionally returns media hosted by member museums worldwide.
        return True
    suffixes = _PROVIDER_HOST_SUFFIXES.get(source, ())
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes)


def validate_provider_media_target(approved, source):
    """Validate one SSRF-approved media hop against HTTPS/provider authority rules."""

    if (
        getattr(approved, "scheme", None) != "https"
        or getattr(approved, "port", None) != 443
    ):
        raise RuntimeError("DailyArt media target must use HTTPS on the default port")
    hostname = getattr(approved, "hostname", "")
    if not provider_media_host_allowed(source, hostname):
        raise RuntimeError("DailyArt media target authority is not allowed for its provider")
    addresses = tuple(getattr(approved, "addresses", ()) or ())
    if not addresses:
        raise RuntimeError("DailyArt media target has no approved public address")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise RuntimeError("DailyArt media target resolved to an invalid address") from exc
        if (
            not address.is_global
            or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped)
        ):
            raise RuntimeError("DailyArt media target resolved to a non-public address")
    return approved.normalized_url


def _normalized_query_terms(value):
    text = str(value or "").replace(";", ",").replace("\n", ",")
    terms = [part.strip() for part in text.split(",") if part.strip()]
    return terms or list(DEFAULT_QUERY_TERMS)


def _layout_mode(settings):
    raw = str((settings or {}).get("layoutMode") or "auto_gallery").strip().lower()
    aliases = {
        "auto": "auto_gallery",
        "portrait": "auto_gallery",
        "portrait_gallery": "auto_gallery",
        "triptych": "gallery",
        "three": "gallery",
        "three_artworks": "gallery",
    }
    mode = aliases.get(raw, raw)
    return mode if mode in {"single", "gallery", "auto_gallery"} else "auto_gallery"


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


def _bounded_text(value, limit):
    return str(value or "").strip()[:limit]


def _parse_datetime(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(character in _HEX for character in value)


def _valid_request_id(value):
    return isinstance(value, str) and len(value) == 32 and all(character in _HEX for character in value)
