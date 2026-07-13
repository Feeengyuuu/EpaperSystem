"""Stable identities and bounded presentation state for Steam Daily Art."""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from plugins.daily_art.presentation_bank import (
    DailyArtPresentationBank,
    MEDIA_MAX_AGE_SECONDS,
    MEDIA_MAX_BYTES,
    MEDIA_MAX_FILES,
    MEDIA_IMAGE_LIMITS,
    MEDIA_MAX_OBJECT_BYTES,
    READY_TARGET,
    REFILL_THRESHOLD,
    read_bounded_json_object,
)
from utils.safe_image import ImageLimitError, safe_open_image


STEAM_MEDIA_HOST_SUFFIXES = (
    "steamstatic.com",
    "steamcontent.com",
    "steamusercontent.com",
    "akamaihd.net",
)


def settings_key(settings):
    """Return the pixel/source identity without runtime controls or secrets."""

    settings = settings or {}
    selection_mode = str(settings.get("selectionMode") or "current").strip().lower()
    rotation_cadence = str(settings.get("rotationCadence") or "hourly").strip().lower()
    if rotation_cadence == "every_refresh":
        if selection_mode in {"", "current", "daily_rotation", "every_refresh"}:
            selection_mode = "daily_rotation"
    elif selection_mode == "daily_rotation":
        selection_mode = "current"
    payload = {
        "source_category": str(settings.get("sourceCategory") or "fresh_frontpage").strip().lower(),
        "selection_mode": selection_mode,
        "rotation_cadence": rotation_cadence,
        "image_mode": str(settings.get("imageMode") or "library_hero").strip().lower(),
        "logo_overlay": str(settings.get("logoOverlay") or "show").strip().lower(),
        "logo_position": str(settings.get("logoPosition") or "empty_space").strip().lower(),
        "logo_size": str(settings.get("logoSize") or "normal").strip().lower(),
        "country_code": str(settings.get("countryCode") or "US").strip().upper()[:2],
        "language": str(settings.get("language") or "english").strip().lower(),
        "show_caption": str(settings.get("showCaption") or "false").strip().lower(),
    }
    return _json_hash(payload)


def settings_fingerprint(settings, dimensions, rotation_key):
    settings = settings or {}
    cadence = str(settings.get("rotationCadence") or "hourly").strip().lower()
    selection_mode = str(settings.get("selectionMode") or "current").strip().lower()
    profile_rotation_key = (
        "every-refresh-pool"
        if cadence == "every_refresh" or selection_mode == "every_refresh"
        else str(rotation_key)
    )
    return _json_hash(
        {
            "settings_key": settings_key(settings),
            "dimensions": [int(dimensions[0]), int(dimensions[1])],
            "rotation_key": profile_rotation_key,
        }
    )


def instance_profile_fingerprint(base_fingerprint, instance_uuid):
    return _json_hash(
        {
            "settings_fingerprint": str(base_fingerprint),
            "instance_uuid": str(instance_uuid),
        }
    )


def _json_hash(payload):
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SteamDailyArtPresentationBank(DailyArtPresentationBank):
    """Steam single-image specialization of the approved DailyArt bank."""

    def __init__(
        self,
        *args,
        selection_mode="daily_rotation",
        source_rotation_key=None,
        read_only=False,
        **kwargs,
    ):
        self.selection_mode = self._normalize_selection_mode(selection_mode)
        self.source_rotation_key = str(source_rotation_key or kwargs.get("date_key") or "")
        self.read_only = bool(read_only)
        if not self.read_only:
            super().__init__(*args, **kwargs)
            return

        if len(args) < 2:
            raise TypeError("Steam read-only bank requires state and media paths")
        self.state_path = Path(args[0])
        self.media_dir = Path(args[1])
        self.fingerprint = kwargs["fingerprint"]
        self.base_fingerprint = kwargs["base_fingerprint"]
        self.profile_settings_key = kwargs["profile_settings_key"]
        self.instance_uuid = kwargs["instance_uuid"]
        self.date_key = kwargs["date_key"]
        self.media = _ReadOnlyMediaNamespace(self.media_dir)

    @staticmethod
    def _normalize_selection_mode(value):
        mode = str(value or "daily_rotation").strip().lower()
        if mode in {"", "current", "daily_rotation", "every_refresh"}:
            return "daily_rotation"
        if mode in {"first", "random"}:
            return mode
        return "daily_rotation"

    def normalize_candidate(self, candidate):
        if not isinstance(candidate, dict):
            raise RuntimeError("Steam Daily Art metadata is invalid")
        appid = str(candidate.get("id") or candidate.get("appid") or "").strip()
        image_url = self._normalize_source_url(candidate.get("image_url"), required=True)
        hostname = (urlparse(image_url).hostname or "").lower().rstrip(".")
        if not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in STEAM_MEDIA_HOST_SUFFIXES):
            raise RuntimeError("Steam media URL authority is not allowed")
        stored_artwork_id = str(candidate.get("artwork_id") or "").strip()
        artwork_id = (
            stored_artwork_id
            or (f"app:{appid}" if appid else f"image:{hashlib.sha256(image_url.encode()).hexdigest()}")
        )
        title = str(candidate.get("name") or candidate.get("title") or "Steam").strip()[:600]
        page_url = candidate.get("page_url")
        if not page_url and appid:
            page_url = f"https://store.steampowered.com/app/{appid}/"
        return {
            "source": "steam",
            "source_label": "Steam Store",
            "artwork_id": artwork_id[:240],
            "title": title,
            "artist": str(candidate.get("_category_name") or "Steam").strip()[:400],
            "date": "",
            "medium": "Game promotional art",
            "museum": "Steam",
            "rights": "Steam promotional media",
            "culture": "",
            "image_url": image_url,
            "page_url": self._normalize_source_url(page_url, required=False),
            "source_rotation_key": str(candidate.get("source_rotation_key") or "")[:80],
        }

    def ingest(self, profile, candidate, image, *, downloaded_at=None):
        record = super().ingest(
            profile,
            candidate,
            image,
            downloaded_at=downloaded_at,
        )
        record["source_rotation_key"] = self.source_rotation_key
        return record

    def choose_selection(self, document, profile, ready, layout_mode="single", gallery_count=1):
        del layout_mode, gallery_count
        if self.selection_mode == "random":
            return super().choose_selection(document, profile, ready, "single", 1)
        if not ready:
            raise RuntimeError("Steam presentation bank has no ready artwork records")
        if self.selection_mode == "first":
            chosen = ready[0]
            reset_seen = False
        else:
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
            chosen = candidates[0]
        return {
            "record_keys": [chosen["record_key"]],
            "request_id": None,
            "date_key": self.date_key,
            "layout": "single",
            "reset_seen": reset_seen,
        }

    def ensure_current(self, document, profile, ready, layout_mode="single", gallery_count=1):
        del layout_mode, gallery_count
        return super().ensure_current(document, profile, ready, "single", 1)

    def selection_records_read_only(self, profile, selection):
        if not self.read_only:
            raise RuntimeError("Steam read-only media access requires a read-only bank")
        if not isinstance(selection, dict):
            raise RuntimeError("Steam theme-only bank has no committed selection")
        records = {record["record_key"]: record for record in profile["records"]}
        selected = []
        for record_key in selection.get("record_keys", []):
            record = records.get(record_key)
            if record is None:
                raise RuntimeError("Steam theme-only selection metadata is missing")
            self._ensure_record_fresh(record)
            selected.append((record, self._load_media_read_only(record)))
        if not selected:
            raise RuntimeError("Steam theme-only selection is empty")
        return selected

    def _load_media_read_only(self, record):
        target = self.media.path(record.get("media_key"), suffix=".png")
        try:
            info = target.lstat()
        except OSError as exc:
            raise RuntimeError("Steam theme-only media is missing") from exc
        if target.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Steam theme-only media is unsafe")
        if info.st_size <= 0 or info.st_size > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Steam theme-only media exceeds its object budget")
        try:
            payload = target.read_bytes()
            source = safe_open_image(payload, limits=MEDIA_IMAGE_LIMITS)
            self._validate_media_dimensions(source.size)
            return self._normalize_media_image(source)
        except ImageLimitError as exc:
            raise RuntimeError(
                "Steam theme-only media dimensions or safety limits were exceeded"
            ) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Steam theme-only media could not be decoded") from exc

    def cleanup(self, document, profile):
        self._loaded_document = document
        now = datetime.now(timezone.utc)
        active_fingerprints = {
            value
            for value in (document.get("instance_profiles") or {}).values()
            if isinstance(value, str)
        }
        protected_profile_fingerprints = set(active_fingerprints)
        protected_profile_fingerprints.update(
            fingerprint
            for fingerprint, candidate_profile in (document.get("profiles") or {}).items()
            if isinstance(candidate_profile, dict)
            and isinstance(candidate_profile.get("pending_selection"), dict)
        )
        protected_profile_fingerprints.update(_latest_receipt_profiles(document))
        protected_by_fingerprint = {}
        for fingerprint, candidate_profile in (document.get("profiles") or {}).items():
            if not isinstance(candidate_profile, dict):
                continue
            protected_keys = _protected_keys_for_cleanup(
                candidate_profile,
                protect_current=fingerprint in protected_profile_fingerprints,
            )
            protected_by_fingerprint[fingerprint] = protected_keys
            retained = []
            for record in candidate_profile.get("records", []):
                if record.get("record_key") in protected_keys:
                    retained.append(record)
                    continue
                downloaded_at = _parse_datetime(record.get("downloaded_at"))
                if downloaded_at is None or (now - downloaded_at).total_seconds() > MEDIA_MAX_AGE_SECONDS:
                    continue
                retained.append(record)
            candidate_profile["records"] = retained
            _clear_inactive_dangling_current(
                candidate_profile,
                protect_current=fingerprint in protected_profile_fingerprints,
            )

        referenced = {
            record.get("media_key")
            for candidate_profile in (document.get("profiles") or {}).values()
            if isinstance(candidate_profile, dict)
            for record in (candidate_profile.get("records") or [])
            if isinstance(record, dict)
        }
        protected_media = {
            record.get("media_key")
            for fingerprint, candidate_profile in (document.get("profiles") or {}).items()
            if isinstance(candidate_profile, dict)
            for record in (candidate_profile.get("records") or [])
            if isinstance(record, dict)
            and record.get("record_key") in protected_by_fingerprint.get(fingerprint, set())
        }
        root = self.media.root
        if root.exists():
            if root.is_symlink():
                raise RuntimeError("Steam media root is unsafe")
            files = []
            for path in root.iterdir():
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError("Steam media root contains an unsafe entry")
                if path.suffix == ".png" and path.stem not in referenced:
                    self.media.remove(path.stem, suffix=".png")
                    continue
                if path.suffix == ".png":
                    files.append((path, path.stat()))
            total = sum(info.st_size for _path, info in files)
            candidates = sorted(
                (
                    (info.st_mtime_ns, path, info.st_size)
                    for path, info in files
                    if path.stem not in protected_media
                ),
                key=lambda value: (value[0], value[1].name),
            )
            count = len(files)
            while (count > MEDIA_MAX_FILES or total > MEDIA_MAX_BYTES) and candidates:
                _mtime, path, size = candidates.pop(0)
                self.media.remove(path.stem, suffix=".png")
                for candidate_profile in (document.get("profiles") or {}).values():
                    if isinstance(candidate_profile, dict):
                        candidate_profile["records"] = [
                            record
                            for record in candidate_profile.get("records", [])
                            if record.get("media_key") != path.stem
                        ]
                count -= 1
                total -= size
            if count > MEDIA_MAX_FILES or total > MEDIA_MAX_BYTES:
                raise RuntimeError("Steam protected media fills the bank budget")
        for fingerprint, candidate_profile in (document.get("profiles") or {}).items():
            if isinstance(candidate_profile, dict):
                _clear_inactive_dangling_current(
                    candidate_profile,
                    protect_current=fingerprint in protected_profile_fingerprints,
                )
        self.save(document)


class _ReadOnlyMediaNamespace:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, key, *, suffix=""):
        value = str(key or "")
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise RuntimeError("Steam theme-only media key is invalid")
        if suffix != ".png":
            raise RuntimeError("Steam theme-only media suffix is invalid")
        return self.root / f"{value}{suffix}"


def _parse_datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _protected_keys_for_cleanup(profile, *, protect_current):
    protected = set()
    names = (
        ("current_selection", "pending_selection")
        if protect_current
        else ("pending_selection",)
    )
    for name in names:
        selection = profile.get(name)
        if not isinstance(selection, dict):
            continue
        protected.update(
            value
            for value in selection.get("record_keys", [])
            if isinstance(value, str)
        )
    return protected


def _clear_inactive_dangling_current(profile, *, protect_current):
    if protect_current:
        return
    current = profile.get("current_selection")
    if not isinstance(current, dict):
        return
    valid_keys = {
        record.get("record_key")
        for record in profile.get("records", [])
        if isinstance(record, dict)
    }
    current_keys = current.get("record_keys")
    if (
        not isinstance(current_keys, list)
        or not current_keys
        or not set(current_keys).issubset(valid_keys)
    ):
        profile["current_selection"] = None


def _latest_receipt_profiles(document):
    latest = {}
    for fingerprint, profile in (document.get("profiles") or {}).items():
        if not isinstance(fingerprint, str) or not isinstance(profile, dict):
            continue
        instance_uuid = profile.get("instance_uuid")
        current = profile.get("current_selection")
        if not isinstance(instance_uuid, str) or not isinstance(current, dict):
            continue
        date_key = current.get("date_key")
        bucket = (profile.get("date_buckets") or {}).get(date_key, {})
        committed_at = _parse_datetime(bucket.get("committed_at"))
        if committed_at is None:
            continue
        previous = latest.get(instance_uuid)
        candidate = (committed_at, fingerprint)
        if previous is None or candidate > previous:
            latest[instance_uuid] = candidate
    return {fingerprint for _committed_at, fingerprint in latest.values()}
