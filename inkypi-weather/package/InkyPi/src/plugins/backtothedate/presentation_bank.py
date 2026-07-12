"""Durable, provider-free presentation bank for BacktotheDate."""

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


SCHEMA_VERSION = 1
READY_TARGET = 24
REFILL_THRESHOLD = 8
MAX_PROFILES = 64
MAX_HISTORY_URLS = 4096
MAX_STATE_BYTES = 4 * 1024 * 1024
MEDIA_MAX_AGE_SECONDS = 48 * 60 * 60
MEDIA_MAX_FILES = 64
MEDIA_MAX_BYTES = 96 * 1024 * 1024
MEDIA_MAX_OBJECT_BYTES = 12 * 1024 * 1024
MEDIA_MAX_DIMENSION = 8192
MEDIA_MAX_PIXELS = 32_000_000
MEDIA_BUDGET = CacheBudget(
    max_age_seconds=MEDIA_MAX_AGE_SECONDS,
    max_files=MEDIA_MAX_FILES,
    max_bytes=MEDIA_MAX_BYTES,
)
_HEX = frozenset("0123456789abcdef")
_BANK_KEYS = {
    "profile_fingerprint",
    "settings_fingerprint",
    "settings_key",
    "instance_uuid",
    "records",
    "current_selection",
    "pending_selection",
    "last_applied_origin_commit_id",
    "last_applied_request_id",
    "last_used_at",
}


def settings_key(settings, source_theme_urls, fit_mode):
    """Return the device-independent identity used for receipt reconciliation."""

    settings = settings or {}
    payload = {
        "source_mode": str(settings.get("sourceMode") or "mao_era").strip().lower(),
        "theme_urls": list(source_theme_urls),
        "max_page": settings.get("maxPage"),
        "poster_image_url": str(
            settings.get("posterImageUrl") or settings.get("previewImageUrl") or ""
        ).strip(),
        "poster_page_url": str(settings.get("posterPageUrl") or "").strip(),
        "fit_mode": fit_mode,
        "background_color": str(settings.get("backgroundColor") or "white").strip().lower(),
    }
    return _json_hash(payload)


def settings_fingerprint(settings, source_theme_urls, fit_mode, dimensions):
    """Return the complete source/layout identity for one bank profile."""

    return _json_hash(
        {
            "settings_key": settings_key(settings, source_theme_urls, fit_mode),
            "dimensions": [int(dimensions[0]), int(dimensions[1])],
        }
    )


def instance_profile_fingerprint(base_fingerprint, instance_uuid):
    """Partition otherwise identical settings by trusted playlist instance."""

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


class PosterPresentationBank:
    """Own normalized metadata and bounded media, never authoritative display PNGs."""

    def __init__(
        self,
        state_path,
        legacy_state_path,
        media_dir,
        *,
        fingerprint,
        base_fingerprint,
        profile_settings_key,
        instance_uuid,
        normalize_history_url,
    ):
        self.state_path = Path(state_path)
        self.legacy_state_path = Path(legacy_state_path)
        self.media_dir = Path(media_dir)
        self.fingerprint = fingerprint
        self.base_fingerprint = base_fingerprint
        self.profile_settings_key = profile_settings_key
        self.instance_uuid = instance_uuid
        self.normalize_history_url = normalize_history_url
        self.media = cache_namespace_for_directory(self.media_dir, MEDIA_BUDGET)

    def load_for_data(self):
        document = self._read_document()
        document = self._migrate_document(document)
        profiles = document["profiles"]
        profile = profiles.get(self.fingerprint)
        document["instance_profiles"][self.instance_uuid] = self.fingerprint
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
                ]
            profiles[self.fingerprint] = profile
        else:
            profile = self._normalize_profile(profile)
            profiles[self.fingerprint] = profile
            self._make_profile_room(document, required_slots=0)
        profile["last_used_at"] = _utc_now()
        document["active_fingerprint"] = self.fingerprint
        return document, profile

    def load_warm(self):
        document = self._migrate_document(self._read_document())
        profile = document["profiles"].get(self.fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("BacktotheDate presentation bank is cold for these settings")
        profile = self._normalize_profile(profile)
        if profile["profile_fingerprint"] != self.fingerprint:
            raise RuntimeError("BacktotheDate presentation bank fingerprint does not match")
        profile["last_used_at"] = _utc_now()
        document["profiles"][self.fingerprint] = profile
        document["instance_profiles"][self.instance_uuid] = self.fingerprint
        self._make_profile_room(document, required_slots=0)
        return document, profile

    def save(self, document):
        document["schema_version"] = SCHEMA_VERSION
        validate_state_payload_size(document)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.state_path, document, mode=0o600)

    def ready_records(self, document, profile, *, prune):
        ready = []
        survivors = []
        protected_keys = self._protected_media_keys(profile)
        for record in profile["records"]:
            try:
                self.load_media(record)
            except RuntimeError:
                if record["media_key"] in protected_keys:
                    survivors.append(record)
                continue
            ready.append(record)
            survivors.append(record)

        if prune and len(survivors) != len(profile["records"]):
            profile["records"] = survivors
            valid_keys = {record["media_key"] for record in survivors}
            if not self._selection_is_valid(profile["current_selection"], valid_keys):
                profile["current_selection"] = None
            if not self._selection_is_valid(profile["pending_selection"], valid_keys):
                profile["pending_selection"] = None
            self.save(document)
        return ready

    def missing_protected_records(self, profile, ready):
        ready_keys = {record["media_key"] for record in ready}
        protected_keys = self._protected_media_keys(profile)
        return [
            record
            for record in profile["records"]
            if record["media_key"] in protected_keys
            and record["media_key"] not in ready_keys
        ]

    def ingest(self, profile, poster, image, downloaded_at=None):
        normalized = self.normalize_poster(poster)
        normalized_image = self._normalize_media_image(image)
        media_key = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        output = BytesIO()
        normalized_image.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("BacktotheDate poster media exceeds its object budget")
        self.media.put_bytes(media_key, payload, suffix=".png")
        record = {
            **normalized,
            "media_key": media_key,
            "width": normalized_image.width,
            "height": normalized_image.height,
            "downloaded_at": downloaded_at or datetime.now(timezone.utc).isoformat(),
        }
        records = list(profile["records"])
        for index, item in enumerate(records):
            if item.get("media_key") == media_key:
                records[index] = record
                break
        else:
            records.append(record)
        profile["records"] = records[-READY_TARGET:]
        return record

    def normalize_poster(self, poster):
        if not isinstance(poster, dict):
            raise RuntimeError("BacktotheDate poster metadata is invalid")
        page_url = self._normalize_source_url(poster.get("page_url"), media=False)
        image_url = self._normalize_source_url(poster.get("image_url"), media=True)
        title = str(poster.get("title") or "").strip()[:500]
        return {
            "page_url": page_url,
            "image_url": image_url,
            "title": title,
        }

    def choose_selection(
        self,
        profile,
        ready,
        fit_mode,
        discarded_page_keys,
        discarded_image_keys,
    ):
        if not ready:
            raise RuntimeError("BacktotheDate presentation bank has no decoded media")
        current_keys = set((profile.get("current_selection") or {}).get("media_keys", []))
        unseen = [
            record
            for record in ready
            if record["media_key"] not in current_keys
            and self.normalize_history_url(record["page_url"]) not in discarded_page_keys
            and self.normalize_history_url(record["image_url"]) not in discarded_image_keys
        ]
        candidates = unseen or [
            record for record in ready if record["media_key"] not in current_keys
        ]
        if not candidates:
            candidates = list(ready)
        random.shuffle(candidates)

        if fit_mode in {"triptych", "three_vertical", "three_posters", "gallery"}:
            portraits = []
            for record in candidates:
                if record["width"] >= record["height"]:
                    return {"media_keys": [record["media_key"]], "request_id": None}
                portraits.append(record)
                if len(portraits) == 3:
                    return {
                        "media_keys": [item["media_key"] for item in portraits],
                        "request_id": None,
                    }
            raise RuntimeError("BacktotheDate bank has fewer than three portrait posters")

        return {"media_keys": [candidates[0]["media_key"]], "request_id": None}

    def ensure_current(self, document, profile, ready, fit_mode):
        valid_keys = {record["media_key"] for record in ready}
        current = profile.get("current_selection")
        if self._selection_is_valid(current, valid_keys):
            return current
        discarded_page_keys, discarded_image_keys = self.discarded_keys(document)
        current = self.choose_selection(
            profile,
            ready,
            fit_mode,
            discarded_page_keys,
            discarded_image_keys,
        )
        profile["current_selection"] = current
        self.save(document)
        return current

    def selection_media(self, profile, selection):
        if not isinstance(selection, dict):
            raise RuntimeError("BacktotheDate bank selection is missing")
        records = {record["media_key"]: record for record in profile["records"]}
        selected = []
        for media_key in selection.get("media_keys", []):
            record = records.get(media_key)
            if record is None:
                raise RuntimeError("BacktotheDate selected media metadata is missing")
            selected.append((record, self.load_media(record)))
        if not selected:
            raise RuntimeError("BacktotheDate bank selection is empty")
        return selected

    def apply_trusted_origin(self, document, profile, request):
        if profile["last_applied_origin_commit_id"] == request.origin_display_commit_id:
            return False
        self._commit_selection(
            document,
            profile,
            profile.get("current_selection"),
            request.requested_at,
        )
        profile["last_applied_origin_commit_id"] = request.origin_display_commit_id
        self.save(document)
        return True

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
            "media_keys": list(selection["media_keys"]),
        }
        profile["pending_selection"] = pending
        self.save(document)
        return pending

    @classmethod
    def reconcile_document(
        cls,
        document,
        receipt,
        normalize_history_url,
        instance_uuid,
    ):
        """Commit only a prepared receipt; an origin receipt is trusted in prepare()."""

        if not isinstance(document, dict):
            return False
        validate_state_shape(document)
        profiles = document.get("profiles")
        instance_profiles = document.get("instance_profiles")
        if not isinstance(profiles, dict) or not isinstance(instance_profiles, dict):
            return False
        profile = profiles.get(instance_profiles.get(instance_uuid))
        if not isinstance(profile, dict):
            return False
        pending = profile.get("pending_selection")
        if not isinstance(pending, dict) or pending.get("request_id") != receipt.request_id:
            return False
        if pending.get("origin_display_commit_id") == receipt.display_commit_id:
            return False
        if profile.get("last_applied_request_id") == receipt.request_id:
            return False
        records = {
            record.get("media_key"): record
            for record in profile.get("records", [])
            if isinstance(record, dict)
        }
        selected = [records.get(key) for key in pending.get("media_keys", [])]
        if not selected or any(record is None for record in selected):
            raise RuntimeError("BacktotheDate prepared receipt media is missing")
        _commit_records(
            document,
            selected,
            receipt.committed_at,
            normalize_history_url,
        )
        profile["current_selection"] = {
            "media_keys": list(pending["media_keys"]),
            "request_id": receipt.request_id,
        }
        profile["pending_selection"] = None
        profile["last_applied_request_id"] = receipt.request_id
        return True

    def load_media(self, record):
        downloaded_at = _parse_datetime(record.get("downloaded_at"))
        now = datetime.now(timezone.utc)
        if downloaded_at is None or (now - downloaded_at).total_seconds() > MEDIA_MAX_AGE_SECONDS:
            raise RuntimeError("BacktotheDate poster media is expired")
        target = self.media.path(record["media_key"], suffix=".png")
        try:
            file_info = target.lstat()
        except OSError as exc:
            raise RuntimeError("BacktotheDate poster media is missing") from exc
        if not stat.S_ISREG(file_info.st_mode) or target.is_symlink():
            raise RuntimeError("BacktotheDate poster media is not a regular file")
        if file_info.st_size <= 0 or file_info.st_size > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("BacktotheDate poster media exceeds its object budget")
        payload = self.media.get_bytes(record["media_key"], suffix=".png")
        if payload is None:
            raise RuntimeError("BacktotheDate poster media is missing")
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("BacktotheDate poster media exceeds its object budget")
        try:
            with Image.open(BytesIO(payload)) as source:
                self._validate_media_dimensions(source.size)
                source.load()
                image = self._normalize_media_image(source)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("BacktotheDate poster media could not be decoded") from exc
        return image

    def _read_document(self):
        path = self.state_path
        if not _path_exists_no_follow(path) and self.legacy_state_path != path and _path_exists_no_follow(
            self.legacy_state_path
        ):
            path = self.legacy_state_path
        if not _path_exists_no_follow(path):
            return {}
        try:
            value = read_bounded_json_object(path)
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError("BacktotheDate state could not be read safely") from exc
        return value

    def _migrate_document(self, source):
        document = dict(source)
        document["schema_version"] = SCHEMA_VERSION
        profiles = document.get("profiles")
        document["profiles"] = dict(profiles) if isinstance(profiles, dict) else {}
        for fingerprint, candidate in list(document["profiles"].items()):
            if not isinstance(fingerprint, str):
                document["profiles"].pop(fingerprint, None)
                continue
            if isinstance(candidate, dict) and _parse_datetime(candidate.get("last_used_at")) is None:
                candidate = dict(candidate)
                candidate["last_used_at"] = "1970-01-01T00:00:00+00:00"
                document["profiles"][fingerprint] = candidate
        document.pop("active_profiles", None)
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
            and isinstance(document["profiles"].get(value), dict)
            and document["profiles"][value].get("instance_uuid") == key
        }
        document.setdefault("active_fingerprint", None)
        page_history = _url_list(document.get("discarded_page_urls"))
        page_history.extend(_url_list(document.get("last_page_url")))
        page_history.extend(_url_list(document.get("last_page_urls")))
        image_history = _url_list(document.get("discarded_image_urls"))
        image_history.extend(_url_list(document.get("last_image_url")))
        image_history.extend(_url_list(document.get("last_image_urls")))
        document["discarded_page_urls"] = _append_unique(
            [], page_history, self.normalize_history_url
        )
        document["discarded_image_urls"] = _append_unique(
            [], image_history, self.normalize_history_url
        )
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
            "last_used_at": _utc_now(),
        }

    def _normalize_profile(self, source):
        profile = self._empty_profile()
        for key in _BANK_KEYS:
            if key in source:
                profile[key] = source[key]
        profile["profile_fingerprint"] = self.fingerprint
        profile["settings_fingerprint"] = self.base_fingerprint
        profile["settings_key"] = self.profile_settings_key
        profile["instance_uuid"] = self.instance_uuid
        profile["records"] = [
            record
            for record in (profile.get("records") or [])
            if self._valid_record(record)
        ][-READY_TARGET:]
        valid_keys = {record["media_key"] for record in profile["records"]}
        for name in ("current_selection", "pending_selection"):
            if not self._selection_is_valid(profile.get(name), valid_keys):
                profile[name] = None
        return profile

    def _valid_record(self, record):
        if not isinstance(record, dict):
            return False
        media_key = record.get("media_key")
        structurally_valid = (
            isinstance(media_key, str)
            and len(media_key) == 64
            and all(character in _HEX for character in media_key)
            and isinstance(record.get("page_url"), str)
            and isinstance(record.get("image_url"), str)
            and isinstance(record.get("downloaded_at"), str)
            and isinstance(record.get("width"), int)
            and isinstance(record.get("height"), int)
        )
        if not structurally_valid or _parse_datetime(record.get("downloaded_at")) is None:
            return False
        try:
            self.normalize_poster(record)
            self._validate_media_dimensions((record["width"], record["height"]))
        except RuntimeError:
            return False
        return True

    def _selection_is_valid(self, selection, valid_keys):
        if selection is None:
            return False
        if not isinstance(selection, dict):
            return False
        keys = selection.get("media_keys")
        keys_valid = (
            isinstance(keys, list)
            and bool(keys)
            and all(isinstance(key, str) and key in valid_keys for key in keys)
        )
        if not keys_valid:
            return False
        request_id = selection.get("request_id")
        if request_id is not None and not _valid_request_id(request_id):
            return False
        if "origin_display_commit_id" in selection:
            origin = selection.get("origin_display_commit_id")
            if not isinstance(origin, str) or not origin.strip():
                return False
        return True

    def discarded_keys(self, document):
        return (
            {
                key
                for key in (
                    self.normalize_history_url(url)
                    for url in document.get("discarded_page_urls", [])
                )
                if key
            },
            {
                key
                for key in (
                    self.normalize_history_url(url)
                    for url in document.get("discarded_image_urls", [])
                )
                if key
            },
        )

    def _make_profile_room(self, document, *, required_slots):
        profiles = document["profiles"]
        instance_profiles = document["instance_profiles"]
        while len(profiles) + required_slots > MAX_PROFILES:
            protected = {
                fingerprint
                for fingerprint in instance_profiles.values()
                if isinstance(fingerprint, str)
            }
            active_fingerprint = document.get("active_fingerprint")
            if isinstance(active_fingerprint, str):
                protected.add(active_fingerprint)
            protected.add(self.fingerprint)
            protected.update(
                fingerprint
                for fingerprint, candidate in profiles.items()
                if isinstance(candidate, dict)
                and isinstance(candidate.get("pending_selection"), dict)
            )
            candidates = [
                (
                    str(candidate.get("last_used_at") or "")
                    if isinstance(candidate, dict)
                    else "",
                    fingerprint,
                )
                for fingerprint, candidate in profiles.items()
                if fingerprint not in protected
            ]
            if not candidates:
                raise RuntimeError("BacktotheDate profile capacity is fully active")
            _last_used_at, victim = min(candidates)
            profiles.pop(victim, None)
            for instance_uuid, fingerprint in list(instance_profiles.items()):
                if fingerprint == victim:
                    instance_profiles.pop(instance_uuid, None)
            if document.get("active_fingerprint") == victim:
                document["active_fingerprint"] = None

    def _protected_media_keys(self, profile):
        protected = set()
        for selection_name in ("current_selection", "pending_selection"):
            selection = profile.get(selection_name)
            if isinstance(selection, dict):
                protected.update(selection.get("media_keys", []))
        return protected

    def _commit_selection(self, document, profile, selection, committed_at):
        if not isinstance(selection, dict):
            return False
        records = {record["media_key"]: record for record in profile["records"]}
        selected = [records.get(key) for key in selection.get("media_keys", [])]
        if not selected or any(record is None for record in selected):
            raise RuntimeError("BacktotheDate committed selection media is missing")
        _commit_records(
            document,
            selected,
            committed_at,
            self.normalize_history_url,
        )
        return True

    def _normalize_source_url(self, value, *, media):
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("BacktotheDate poster URL is missing")
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("BacktotheDate poster URL authority is invalid") from exc
        if (
            host != "chineseposters.net"
            or parsed.scheme.lower() not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
        ):
            raise RuntimeError("BacktotheDate poster URL is outside chineseposters.net")
        path = parsed.path
        if media:
            if not path.lower().startswith("/sites/default/files/images/"):
                raise RuntimeError("BacktotheDate media URL is outside the poster image path")
        elif not (
            path.lower().startswith("/posters/")
            or path.lower().startswith("/sites/default/files/images/")
        ):
            raise RuntimeError("BacktotheDate page URL is outside the poster path")
        return urlunparse(("https", "chineseposters.net", path, "", parsed.query, ""))

    def _normalize_media_image(self, image):
        if not isinstance(image, Image.Image):
            raise RuntimeError("BacktotheDate poster media is not an image")
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
            raise RuntimeError("BacktotheDate poster media dimensions exceed the safety limit")


def _commit_records(document, records, committed_at, normalize_history_url):
    page_urls = [record["page_url"] for record in records]
    image_urls = [record["image_url"] for record in records]
    existing_at = _parse_datetime(document.get("last_displayed_at"))
    incoming_at = _parse_datetime(committed_at)
    if incoming_at is None:
        raise RuntimeError("BacktotheDate display receipt timestamp is invalid")
    if existing_at is None or incoming_at >= existing_at:
        first = records[0]
        document["last_page_url"] = first["page_url"]
        document["last_image_url"] = first["image_url"]
        document["last_title"] = first.get("title")
        document["last_page_urls"] = page_urls
        document["last_image_urls"] = image_urls
        document["last_displayed_at"] = incoming_at.isoformat()
    document["discarded_page_urls"] = _append_unique(
        document.get("discarded_page_urls", []),
        page_urls,
        normalize_history_url,
    )
    document["discarded_image_urls"] = _append_unique(
        document.get("discarded_image_urls", []),
        image_urls,
        normalize_history_url,
    )


def _append_unique(existing, additions, normalize):
    result = []
    seen = set()
    for value in _url_list(existing) + _url_list(additions):
        key = normalize(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result[-MAX_HISTORY_URLS:]


def validate_state_payload_size(payload):
    """Reject state that cannot fit the durable JSON budget before opening a writer."""

    validate_state_shape(payload)
    try:
        encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode(
            "utf-8"
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("BacktotheDate state could not be encoded safely") from exc
    if len(encoded) > MAX_STATE_BYTES:
        raise RuntimeError("BacktotheDate state exceeds the size limit")
    return len(encoded)


def validate_state_shape(payload):
    """Prove all durable metadata collections stay within their fixed bounds."""

    if not isinstance(payload, dict):
        raise RuntimeError("BacktotheDate state must be an object")
    profiles = payload.get("profiles")
    if profiles is not None and not isinstance(profiles, dict):
        raise RuntimeError("BacktotheDate state profiles must be an object")
    if isinstance(profiles, dict) and len(profiles) > MAX_PROFILES:
        raise RuntimeError("BacktotheDate profile capacity exceeds the limit")
    instance_profiles = payload.get("instance_profiles")
    if instance_profiles is not None and not isinstance(instance_profiles, dict):
        raise RuntimeError("BacktotheDate instance profiles must be an object")
    if isinstance(instance_profiles, dict):
        if len(instance_profiles) > MAX_PROFILES:
            raise RuntimeError("BacktotheDate instance profile capacity exceeds the limit")
        surviving_profiles = profiles if isinstance(profiles, dict) else {}
        if any(
            not isinstance(instance_uuid, str)
            or not isinstance(fingerprint, str)
            or fingerprint not in surviving_profiles
            for instance_uuid, fingerprint in instance_profiles.items()
        ):
            raise RuntimeError("BacktotheDate instance profiles reference missing profiles")
    for key in ("discarded_page_urls", "discarded_image_urls"):
        if len(_url_list(payload.get(key))) > MAX_HISTORY_URLS:
            raise RuntimeError("BacktotheDate discarded history exceeds the limit")


def read_bounded_json_object(path):
    """Read one regular JSON object only after checking its on-disk byte budget."""

    path = Path(path)
    try:
        file_info = path.lstat()
    except OSError as exc:
        raise RuntimeError("BacktotheDate state path could not be inspected") from exc
    if not stat.S_ISREG(file_info.st_mode):
        raise RuntimeError("BacktotheDate state path is not a regular file")
    if file_info.st_size > MAX_STATE_BYTES:
        raise RuntimeError("BacktotheDate state exceeds the size limit")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise RuntimeError("BacktotheDate state could not be read safely") from exc
    if len(payload) > MAX_STATE_BYTES:
        raise RuntimeError("BacktotheDate state exceeds the size limit")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("BacktotheDate state could not be read safely") from exc
    if not isinstance(value, dict):
        raise RuntimeError("BacktotheDate state must be an object")
    return value


def _path_exists_no_follow(path):
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError("BacktotheDate state path could not be inspected") from exc
    return True


def _url_list(value):
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


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


def _valid_request_id(value):
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in _HEX for character in value)
    )
