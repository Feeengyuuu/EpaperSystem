"""Bounded, provider-free presentation bank for GCD comic covers."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, urlunparse
import json
import random
import stat

from PIL import Image, ImageOps

from utils.atomic_file import atomic_write_json
from utils.cache_manager import CacheBudget, cache_namespace_for_directory
from utils.safe_image import ImageLimitError, ImageLimits, safe_open_image


SCHEMA_VERSION = 1
READY_TARGET = 18
REFILL_THRESHOLD = 6
MAX_PROFILES = 64
MAX_RECORDS_PER_PROFILE = READY_TARGET
MAX_SEEN_ISSUES = 5000
MAX_DATE_BUCKETS = 366
MAX_STATE_BYTES = 4 * 1024 * 1024
MEDIA_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
MEDIA_MAX_FILES = 128
MEDIA_MAX_BYTES = 128 * 1024 * 1024
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
_HEX = frozenset("0123456789abcdef")
_PROFILE_KEYS = {
    "profile_fingerprint",
    "settings_fingerprint",
    "settings_key",
    "instance_uuid",
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
    "issue_id": 200,
    "match_quality": 32,
    "series_name": 500,
    "issue_label": 500,
    "issue_number": 100,
    "publisher": 300,
    "date_label": 100,
    "on_sale_date": 100,
    "publication_date": 100,
    "key_date": 100,
    "cover_date": 100,
    "store_date": 100,
    "date_added": 100,
    "cover_credits": 1000,
    "country": 100,
}


def settings_key(settings, fit_mode):
    """Return the stable, device-independent source and layout identity."""

    settings = settings or {}
    payload = {
        "source_mode": str(settings.get("sourceMode") or "mixed").strip().lower(),
        "country_codes": str(settings.get("countryCodes") or "us").strip().lower(),
        "start_year": settings.get("startYear"),
        "end_year": settings.get("endYear"),
        "max_years_per_refresh": settings.get("maxYearsPerRefresh"),
        "comic_vine_limit": settings.get("comicVineLimit"),
        "max_cover_attempts": settings.get("maxCoverAttempts"),
        "fit_mode": fit_mode,
        "background_color": str(settings.get("backgroundColor") or "white").strip().lower(),
        "background_style": str(settings.get("backgroundStyle") or "blur").strip().lower(),
        "show_info_label": str(settings.get("showInfoLabel", "true")).strip().lower(),
    }
    return _json_hash(payload)


def settings_fingerprint(settings, fit_mode, dimensions):
    return _json_hash(
        {
            "settings_key": settings_key(settings, fit_mode),
            "dimensions": [int(dimensions[0]), int(dimensions[1])],
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


class GcdPresentationBank:
    """Own normalized records and managed media, partitioned by trusted instance."""

    def __init__(
        self,
        state_path,
        media_dir,
        *,
        fingerprint,
        base_fingerprint,
        profile_settings_key,
        instance_uuid,
        display_date_key,
    ):
        self.state_path = Path(state_path)
        self.media_dir = Path(media_dir)
        self.fingerprint = fingerprint
        self.base_fingerprint = base_fingerprint
        self.profile_settings_key = profile_settings_key
        self.instance_uuid = instance_uuid
        self.display_date_key = display_date_key
        self.media = cache_namespace_for_directory(self.media_dir, MEDIA_BUDGET)

    def load_for_data(self):
        document = self._migrate_document(self._read_document())
        profiles = document["profiles"]
        profile = profiles.get(self.fingerprint)
        if not isinstance(profile, dict):
            self._make_profile_room(document, required_slots=1)
            profile = self._empty_profile()
            donor = next(
                (
                    candidate
                    for candidate in profiles.values()
                    if isinstance(candidate, dict)
                    and candidate.get("settings_fingerprint") == self.base_fingerprint
                    and isinstance(candidate.get("records"), list)
                    and candidate["records"]
                ),
                None,
            )
            if donor is not None:
                profile["records"] = [
                    dict(record)
                    for record in donor["records"]
                    if isinstance(record, dict)
                ][-MAX_RECORDS_PER_PROFILE:]
            profiles[self.fingerprint] = profile
        else:
            profile = self._normalize_profile(profile)
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
            raise RuntimeError("GCD presentation bank is cold for this plugin instance")
        profile = document["profiles"].get(fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("GCD presentation bank is cold for these settings")
        profile = self._normalize_profile(profile)
        if profile["profile_fingerprint"] != self.fingerprint:
            raise RuntimeError("GCD presentation bank fingerprint does not match")
        profile["last_used_at"] = _utc_now()
        document["profiles"][self.fingerprint] = profile
        self._make_profile_room(document, required_slots=0)
        return document, profile

    def save(self, document):
        document["presentation_schema_version"] = SCHEMA_VERSION
        validate_state_payload_size(document)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.state_path, document, mode=0o600)

    def ready_records(self, profile, *, prune):
        ready = []
        survivors = []
        protected = self._protected_record_keys(profile)
        for record in profile["records"]:
            same_day = record.get("display_date_key") == self.display_date_key
            try:
                self._ensure_record_fresh(record)
                if same_day and record.get("media_key"):
                    self.load_media(record)
                elif same_day and record.get("render_kind") != "metadata":
                    raise RuntimeError("GCD bank record has no renderable media")
            except RuntimeError:
                if record["record_key"] in protected:
                    survivors.append(record)
                continue
            if same_day:
                ready.append(record)
                survivors.append(record)
            elif record["record_key"] in protected:
                survivors.append(record)
        if prune and len(survivors) != len(profile["records"]):
            profile["records"] = survivors[-MAX_RECORDS_PER_PROFILE:]
        return ready

    def ingest(self, profile, cover, image=None, *, render_kind="media", downloaded_at=None):
        normalized = self.normalize_cover(cover, render_kind=render_kind)
        media_key = None
        width = 0
        height = 0
        if render_kind == "media":
            normalized_image = self._normalize_media_image(image)
            media_key = sha256(normalized["cover_url"].encode("utf-8")).hexdigest()
            output = BytesIO()
            normalized_image.save(output, format="PNG", optimize=True)
            payload = output.getvalue()
            if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
                raise RuntimeError("GCD cover media exceeds its object budget")
            self.media.put_bytes(media_key, payload, suffix=".png")
            width, height = normalized_image.size
        record_key = sha256(
            f"{normalized['issue_id']}\0{normalized['cover_url']}".encode("utf-8")
        ).hexdigest()
        record = {
            **normalized,
            "record_key": record_key,
            "media_key": media_key,
            "width": width,
            "height": height,
            "downloaded_at": downloaded_at or _utc_now(),
            "display_date_key": self.display_date_key,
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
                raise RuntimeError("GCD protected cover metadata fills the record budget")
            records.pop(victim)
        profile["records"] = records
        return record

    def protected_records(self, profile):
        """Return exact current/pending records or fail on missing metadata."""

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
                    raise RuntimeError("GCD protected cover metadata is missing")
                if record_key not in seen:
                    seen.add(record_key)
                    protected.append(record)
        return protected

    def recover_media(self, profile, record, image, *, downloaded_at=None):
        """Replace only exact saved cover bytes while retaining record identity."""

        if record.get("render_kind") != "media":
            raise RuntimeError("GCD metadata-only records do not require media recovery")
        normalized = self.normalize_cover(record, render_kind="media")
        expected_media_key = sha256(normalized["cover_url"].encode("utf-8")).hexdigest()
        if record.get("media_key") != expected_media_key:
            raise RuntimeError("GCD protected cover media identity does not match its URL")
        normalized_image = self._normalize_media_image(image)
        output = BytesIO()
        normalized_image.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("GCD recovered cover media exceeds its object budget")
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
        raise RuntimeError("GCD protected cover metadata disappeared during recovery")

    def normalize_cover(self, cover, *, render_kind):
        if not isinstance(cover, dict):
            raise RuntimeError("GCD cover metadata is invalid")
        if render_kind not in {"media", "metadata"}:
            raise RuntimeError("GCD cover render kind is invalid")
        issue_id = _bounded_text(cover.get("issue_id"), _TEXT_LIMITS["issue_id"])
        if not issue_id:
            raise RuntimeError("GCD cover issue ID is missing")
        cover_url = self._normalize_source_url(cover.get("cover_url"), required=False)
        if render_kind == "media" and not cover_url:
            raise RuntimeError("GCD cover media URL is missing")
        page_url = self._normalize_source_url(cover.get("page_url"), required=False)
        normalized = {
            "render_kind": render_kind,
            "page_url": page_url,
            "cover_url": cover_url,
        }
        for key, limit in _TEXT_LIMITS.items():
            normalized[key] = _bounded_text(cover.get(key), limit)
        normalized["issue_id"] = issue_id
        quality = normalized.get("match_quality")
        if quality not in {"comicvine_recent", "exact_day", "month_fallback"}:
            quality = (
                "comicvine_recent"
                if normalized.get("source") == "comicvine"
                else "month_fallback"
            )
        normalized["match_quality"] = quality
        return normalized

    def choose_selection(self, document, profile, ready, fit_mode):
        if not ready:
            raise RuntimeError("GCD presentation bank has no ready cover records")
        current_keys = set((profile.get("current_selection") or {}).get("record_keys", []))
        pending_keys = set((profile.get("pending_selection") or {}).get("record_keys", []))
        pending_issue_ids = {
            record["issue_id"]
            for record in profile["records"]
            if record["record_key"] in pending_keys
        }
        bucket = document.get("date_buckets", {}).get(self.display_date_key, {})
        seen_issue_ids = {str(value) for value in bucket.get("seen_issue_ids", [])}
        candidates = [
            record
            for record in ready
            if record["record_key"] not in current_keys
            and record["issue_id"] not in pending_issue_ids
            and record["issue_id"] not in seen_issue_ids
        ]
        reset_seen = False
        if not candidates:
            candidates = [
                record
                for record in ready
                if record["record_key"] not in current_keys
                and record["issue_id"] not in pending_issue_ids
            ]
            reset_seen = bool(candidates)
        if not candidates:
            candidates = list(ready)
            reset_seen = bool(seen_issue_ids)
        ordered = []
        for quality in ("comicvine_recent", "exact_day", "month_fallback"):
            tier = [record for record in candidates if record.get("match_quality") == quality]
            random.shuffle(tier)
            ordered.extend(tier)

        if fit_mode in {"triptych", "three_vertical", "three_covers", "three_posters", "gallery"}:
            media_candidates = [
                record for record in ordered if record.get("render_kind") == "media"
            ]
            regular = [
                record
                for record in media_candidates
                if record.get("width", 0) <= record.get("height", 0) * 1.15
            ]
            wide = [record for record in media_candidates if record not in regular]
            chosen = (regular + wide)[:3]
            if not chosen:
                chosen = [
                    record
                    for record in ordered
                    if record.get("render_kind") == "metadata"
                ][:1]
        else:
            chosen = [
                record for record in ordered if record.get("render_kind") == "media"
            ][:1]
            if not chosen:
                chosen = [
                    record
                    for record in ordered
                    if record.get("render_kind") == "metadata"
                ][:1]
        if not chosen:
            raise RuntimeError("GCD presentation bank could not choose a cover")
        return {
            "record_keys": [record["record_key"] for record in chosen],
            "request_id": None,
            "date_key": self.display_date_key,
            "reset_seen": reset_seen,
        }

    def ensure_current(self, document, profile, ready, fit_mode):
        valid_keys = {record["record_key"] for record in ready}
        current = profile.get("current_selection")
        if self._selection_is_valid(current, valid_keys):
            return current
        current = self.choose_selection(document, profile, ready, fit_mode)
        profile["current_selection"] = current
        self.save(document)
        return current

    def selection_records(self, profile, selection, *, load_media):
        if not isinstance(selection, dict):
            raise RuntimeError("GCD bank selection is missing")
        records = {record["record_key"]: record for record in profile["records"]}
        selected = []
        for record_key in selection.get("record_keys", []):
            record = records.get(record_key)
            if record is None:
                raise RuntimeError("GCD selected cover metadata is missing")
            self._ensure_record_fresh(record)
            image = None
            if record.get("render_kind") == "media" and load_media:
                image = self.load_media(record)
            selected.append((record, image))
        if not selected:
            raise RuntimeError("GCD bank selection is empty")
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
        # Decode the exact prepared bytes again before making any durable mutation.
        selected = self.selection_records(profile, pending, load_media=True)
        records = [record for record, _image in selected]
        _commit_records(document, records, pending, receipt.committed_at)
        profile["current_selection"] = {
            "record_keys": list(pending["record_keys"]),
            "request_id": receipt.request_id,
            "date_key": pending["date_key"],
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
            raise RuntimeError("GCD cover media key is invalid")
        target = self.media.path(media_key, suffix=".png")
        try:
            file_info = target.lstat()
        except OSError as exc:
            raise RuntimeError("GCD cover media is missing") from exc
        if target.is_symlink() or not stat.S_ISREG(file_info.st_mode):
            raise RuntimeError("GCD cover media is not a regular file")
        if file_info.st_size <= 0 or file_info.st_size > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("GCD cover media exceeds its object budget")
        payload = self.media.get_bytes(media_key, suffix=".png")
        if payload is None or not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("GCD cover media is unavailable")
        try:
            source = safe_open_image(payload, limits=MEDIA_IMAGE_LIMITS)
            self._validate_media_dimensions(source.size)
            return self._normalize_media_image(source)
        except ImageLimitError as exc:
            raise RuntimeError(
                "GCD cover media dimensions or safety limits were exceeded"
            ) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("GCD cover media could not be decoded") from exc

    def _read_document(self):
        if not _path_exists_no_follow(self.state_path):
            return {}
        try:
            return read_bounded_json_object(self.state_path)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("GCD cover state could not be read safely") from exc

    def _migrate_document(self, source):
        document = dict(source)
        document.setdefault("version", "gcd-comic-covers-state-v1")
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
        instance_profiles = document.get("instance_profiles")
        document["instance_profiles"] = (
            dict(instance_profiles) if isinstance(instance_profiles, dict) else {}
        )
        document["instance_profiles"] = {
            key: value
            for key, value in document["instance_profiles"].items()
            if isinstance(key, str)
            and isinstance(value, str)
            and value in document["profiles"]
            and document["profiles"][value].get("instance_uuid") == key
        }
        document.setdefault("active_fingerprint", None)
        buckets = document.get("date_buckets")
        document["date_buckets"] = _bounded_date_buckets(buckets)
        return document

    def _empty_profile(self):
        return {
            "profile_fingerprint": self.fingerprint,
            "settings_fingerprint": self.base_fingerprint,
            "settings_key": self.profile_settings_key,
            "instance_uuid": self.instance_uuid,
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

    def _normalize_profile(self, source):
        profile = self._empty_profile()
        for key in _PROFILE_KEYS:
            if key in source:
                profile[key] = source[key]
        profile["profile_fingerprint"] = self.fingerprint
        profile["settings_fingerprint"] = self.base_fingerprint
        profile["settings_key"] = self.profile_settings_key
        profile["instance_uuid"] = self.instance_uuid
        attempted_at = _parse_datetime(profile.get("last_provider_attempt_at"))
        profile["last_provider_attempt_at"] = (
            attempted_at.isoformat() if attempted_at is not None else None
        )
        status = str(profile.get("last_provider_status") or "").strip().lower()
        profile["last_provider_status"] = status if status in {"success", "empty", "error"} else None
        normalized_records = []
        for record in profile.get("records") or []:
            if not self._valid_record(record):
                continue
            normalized_records.append(
                {
                    **record,
                    **self.normalize_cover(
                        record,
                        render_kind=record["render_kind"],
                    ),
                }
            )
        profile["records"] = normalized_records[-MAX_RECORDS_PER_PROFILE:]
        profile["refill_in_progress"] = profile.get("refill_in_progress") is True
        valid_keys = {record["record_key"] for record in profile["records"]}
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if selection is None:
                continue
            if not self._selection_is_valid(selection, valid_keys):
                raise RuntimeError("GCD protected selection metadata is invalid")
        return profile

    def _valid_record(self, record):
        if not isinstance(record, dict):
            return False
        render_kind = record.get("render_kind")
        media_key = record.get("media_key")
        structure = (
            _valid_hash(record.get("record_key"))
            and render_kind in {"media", "metadata"}
            and isinstance(record.get("issue_id"), str)
            and bool(record["issue_id"])
            and isinstance(record.get("display_date_key"), str)
            and isinstance(record.get("downloaded_at"), str)
            and _parse_datetime(record.get("downloaded_at")) is not None
            and isinstance(record.get("width"), int)
            and isinstance(record.get("height"), int)
            and ((render_kind == "media" and _valid_hash(media_key)) or (render_kind == "metadata" and media_key is None))
        )
        if not structure:
            return False
        try:
            normalized = self.normalize_cover(record, render_kind=render_kind)
            if render_kind == "media":
                self._validate_media_dimensions((record["width"], record["height"]))
                expected_media_key = sha256(
                    normalized["cover_url"].encode("utf-8")
                ).hexdigest()
                if media_key != expected_media_key:
                    return False
        except RuntimeError:
            return False
        return True

    def _selection_is_valid(self, selection, valid_keys):
        if not isinstance(selection, dict):
            return False
        keys = selection.get("record_keys")
        if not (
            isinstance(keys, list)
            and bool(keys)
            and len(keys) <= 3
            and all(isinstance(key, str) and key in valid_keys for key in keys)
        ):
            return False
        request_id = selection.get("request_id")
        if request_id is not None and not _valid_request_id(request_id):
            return False
        return isinstance(selection.get("date_key"), str)

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
                raise RuntimeError("GCD presentation profile capacity is fully active")
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
        if record.get("render_kind") == "metadata":
            return
        downloaded_at = _parse_datetime(record.get("downloaded_at"))
        now = datetime.now(timezone.utc)
        if downloaded_at is None or (now - downloaded_at).total_seconds() > MEDIA_MAX_AGE_SECONDS:
            raise RuntimeError("GCD cover record is expired")

    def _commit_selection(self, document, profile, selection, committed_at):
        if not isinstance(selection, dict):
            return None
        selected = self.selection_records(profile, selection, load_media=True)
        records = [record for record, _image in selected]
        _commit_records(document, records, selection, committed_at)
        return records

    def _normalize_source_url(self, value, *, required):
        if value in {None, ""} and not required:
            return ""
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("GCD cover URL is missing")
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("GCD cover URL authority is invalid") from exc
        allowed = (
            host == "comics.org"
            or host.endswith(".comics.org")
            or host == "comicvine.gamespot.com"
            or host.endswith(".comicvine.gamespot.com")
        )
        if (
            not allowed
            or parsed.scheme.lower() not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
        ):
            raise RuntimeError("GCD cover URL is outside approved source authorities")
        return urlunparse(("https", host, parsed.path or "/", "", parsed.query, ""))

    def _normalize_media_image(self, image):
        if not isinstance(image, Image.Image):
            raise RuntimeError("GCD cover media is not an image")
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
            raise RuntimeError("GCD cover media dimensions exceed the safety limit")


def _commit_records(document, records, selection, committed_at):
    incoming_at = _parse_datetime(committed_at)
    if incoming_at is None:
        raise RuntimeError("GCD display receipt timestamp is invalid")
    date_key = selection.get("date_key")
    if not isinstance(date_key, str) or not date_key:
        raise RuntimeError("GCD display receipt date bucket is invalid")
    buckets = document.setdefault("date_buckets", {})
    bucket = buckets.setdefault(date_key, {})
    if selection.get("reset_seen"):
        bucket["seen_issue_ids"] = []
    seen = [str(value) for value in bucket.get("seen_issue_ids", [])]
    for record in records:
        issue_id = record["issue_id"]
        if issue_id not in seen:
            seen.append(issue_id)
    bucket["seen_issue_ids"] = seen[-MAX_SEEN_ISSUES:]
    existing_at = _parse_datetime(bucket.get("last_displayed_at"))
    if existing_at is None or incoming_at >= existing_at:
        bucket["last_issue_id"] = records[-1]["issue_id"]
        bucket["last_displayed_at"] = incoming_at.isoformat()
    document["date_buckets"] = _bounded_date_buckets(buckets)


def validate_state_payload_size(payload):
    validate_state_shape(payload)
    try:
        encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("GCD cover state could not be encoded safely") from exc
    if len(encoded) > MAX_STATE_BYTES:
        raise RuntimeError("GCD cover state exceeds the size limit")
    return len(encoded)


def validate_state_shape(payload):
    if not isinstance(payload, dict):
        raise RuntimeError("GCD cover state must be an object")
    profiles = payload.get("profiles")
    if profiles is not None and not isinstance(profiles, dict):
        raise RuntimeError("GCD cover state profiles must be an object")
    if isinstance(profiles, dict):
        if len(profiles) > MAX_PROFILES:
            raise RuntimeError("GCD cover profile capacity exceeds the limit")
        for profile in profiles.values():
            if not isinstance(profile, dict):
                raise RuntimeError("GCD cover profile must be an object")
            records = profile.get("records", [])
            if not isinstance(records, list) or len(records) > MAX_RECORDS_PER_PROFILE:
                raise RuntimeError("GCD cover record capacity exceeds the limit")
            if len(profile) > len(_PROFILE_KEYS):
                raise RuntimeError("GCD cover profile metadata exceeds the field limit")
            if not all(isinstance(record, dict) and len(record) <= 32 for record in records):
                raise RuntimeError("GCD cover record metadata exceeds the field limit")
            for name in ("current_selection", "pending_selection"):
                selection = profile.get(name)
                if selection is None:
                    continue
                if not isinstance(selection, dict) or len(selection) > 8:
                    raise RuntimeError("GCD cover selection metadata exceeds the field limit")
                record_keys = selection.get("record_keys")
                if not (
                    isinstance(record_keys, list)
                    and 0 < len(record_keys) <= 3
                    and all(_valid_hash(key) for key in record_keys)
                ):
                    raise RuntimeError("GCD cover selection capacity exceeds the limit")
    mappings = payload.get("instance_profiles")
    if mappings is not None and not isinstance(mappings, dict):
        raise RuntimeError("GCD cover instance profiles must be an object")
    if isinstance(mappings, dict):
        if len(mappings) > MAX_PROFILES:
            raise RuntimeError("GCD cover instance profile capacity exceeds the limit")
        surviving = profiles if isinstance(profiles, dict) else {}
        if any(
            not isinstance(instance_uuid, str)
            or not isinstance(fingerprint, str)
            or fingerprint not in surviving
            for instance_uuid, fingerprint in mappings.items()
        ):
            raise RuntimeError("GCD cover instance profile references missing profile")
    buckets = payload.get("date_buckets")
    if buckets is not None and not isinstance(buckets, dict):
        raise RuntimeError("GCD cover date buckets must be an object")
    if isinstance(buckets, dict):
        if len(buckets) > MAX_DATE_BUCKETS:
            raise RuntimeError("GCD cover date bucket capacity exceeds the limit")
        for bucket in buckets.values():
            if not isinstance(bucket, dict):
                raise RuntimeError("GCD cover date bucket must be an object")
            seen = bucket.get("seen_issue_ids", [])
            if not isinstance(seen, list) or len(seen) > MAX_SEEN_ISSUES:
                raise RuntimeError("GCD cover seen history exceeds the limit")
            if any(
                not isinstance(issue_id, str) or len(issue_id) > _TEXT_LIMITS["issue_id"]
                for issue_id in seen
            ):
                raise RuntimeError("GCD cover seen issue metadata exceeds the limit")


def read_bounded_json_object(path):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeError("GCD cover state path could not be inspected") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("GCD cover state path is not a regular file")
    if info.st_size > MAX_STATE_BYTES:
        raise RuntimeError("GCD cover state exceeds the size limit")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise RuntimeError("GCD cover state could not be read safely") from exc
    if len(payload) > MAX_STATE_BYTES:
        raise RuntimeError("GCD cover state exceeds the size limit")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("GCD cover state could not be read safely") from exc
    if not isinstance(value, dict):
        raise RuntimeError("GCD cover state must be an object")
    return value


def _path_exists_no_follow(path):
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError("GCD cover state path could not be inspected") from exc
    return True


def _bounded_text(value, limit):
    return str(value or "").strip()[:limit]


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
            _parse_datetime(candidates[key].get("last_displayed_at")) or minimum,
            key,
        ),
    )
    retained = set(ranked[-MAX_DATE_BUCKETS:])
    return {key: bucket for key, bucket in candidates.items() if key in retained}


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
