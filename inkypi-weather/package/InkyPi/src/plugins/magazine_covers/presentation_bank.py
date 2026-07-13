"""Bounded provider-free presentation bank for Magazine Covers."""

from __future__ import annotations

import json
import ipaddress
import os
import random
import secrets
import shutil
import stat
import tempfile
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from PIL import Image, ImageOps

from plugins.daily_art.presentation_bank import (
    _atomic_write_bounded_json,
    _is_link_like,
    _path_exists_no_follow,
    _validate_fallback_directory_chain,
    read_bounded_json_object,
)
from utils.atomic_file import atomic_write_bytes, fsync_directory
from utils.safe_image import ImageLimitError, ImageLimits, safe_open_image


SCHEMA_VERSION = 1
READY_TARGET = 18
REFILL_THRESHOLD = 6
MAX_PROFILES = 64
MAX_RECORDS_PER_PROFILE = READY_TARGET
MAX_SEEN_SOURCES = 5000
MAX_DATE_BUCKETS = 366
MAX_STATE_BYTES = 4 * 1024 * 1024
MEDIA_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
MEDIA_MAX_FILES = 48
MEDIA_MAX_BYTES = 128 * 1024 * 1024
GLOBAL_MEDIA_ROOT_MAX_BYTES = 512 * 1024 * 1024
MEDIA_MAX_OBJECT_BYTES = 16 * 1024 * 1024
MEDIA_MAX_DIMENSION = 8192
MEDIA_MAX_PIXELS = 32_000_000
MEDIA_IMAGE_LIMITS = ImageLimits(
    max_bytes=MEDIA_MAX_OBJECT_BYTES,
    max_width=MEDIA_MAX_DIMENSION,
    max_height=MEDIA_MAX_DIMENSION,
    max_pixels=MEDIA_MAX_PIXELS,
    allowed_formats=frozenset({"PNG"}),
)
COVER_FRESH_SECONDS = 20 * 60 * 60
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
    "refill_in_progress",
    "hydration_cursor",
    "library_refreshed_at",
    "library_last_attempt_at",
    "library_pool_key",
    "library_scan_source_ids",
    "library_scan_started_at",
    "last_used_at",
}


class ProtectedMediaStore:
    """Bounded media namespace whose protected objects are never LRU victims."""

    def __init__(self, root, *, protected_keys_provider, global_root):
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.global_root = Path(os.path.abspath(os.fspath(global_root)))
        self._protected_keys_provider = protected_keys_provider

    def path(self, key, *, suffix=".png"):
        self._validate_identity(key, suffix)
        return self.root / f"{key}{suffix}"

    def get_bytes(self, key, *, suffix=".png"):
        target = self.path(key, suffix=suffix)
        try:
            _validate_fallback_directory_chain(self.root, create=False)
            info = target.lstat()
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError) as exc:
            raise RuntimeError("Magazine media path could not be inspected safely") from exc
        if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Magazine media path is unsafe")
        if info.st_size <= 0 or info.st_size > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Magazine media exceeds its object budget")
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise RuntimeError("Magazine media could not be read safely") from exc
        if len(payload) != info.st_size or not payload:
            raise RuntimeError("Magazine media changed during read")
        return payload

    def put_bytes(self, key, payload, *, suffix=".png"):
        target = self.path(key, suffix=suffix)
        if not isinstance(payload, bytes) or not payload:
            raise RuntimeError("Magazine media payload is empty")
        if len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Magazine media exceeds its object budget")
        try:
            _validate_fallback_directory_chain(self.root, create=True)
            _validate_fallback_directory_chain(self.global_root, create=False)
            existing = self._namespace_entries()
            target_entry = next((entry for entry in existing if entry["path"] == target), None)
            old_size = target_entry["size"] if target_entry is not None else 0
            victims = self._admission_victims(existing, key, old_size, len(payload))
            self._transactional_publish(target, payload, victims, target_entry)
        except RuntimeError:
            raise
        except OSError as exc:
            raise RuntimeError("Magazine media could not be admitted safely") from exc
        return target

    def _admission_victims(self, entries, target_key, old_size, incoming_size):
        protected = set(self._protected_keys_provider() or ())
        candidates = sorted(
            (
                entry
                for entry in entries
                if entry["media_key"] not in protected and entry["media_key"] != target_key
            ),
            key=lambda entry: (entry["mtime"], entry["path"].name),
        )
        namespace_count = len(entries) + (0 if old_size else 1)
        namespace_bytes = sum(entry["size"] for entry in entries) - old_size + incoming_size
        global_bytes = self._global_regular_file_bytes() - old_size + incoming_size
        now = datetime.now(timezone.utc).timestamp()
        victims = []

        for entry in candidates:
            expired = now - entry["mtime"] > MEDIA_MAX_AGE_SECONDS
            over_budget = (
                namespace_count > MEDIA_MAX_FILES
                or namespace_bytes > MEDIA_MAX_BYTES
                or global_bytes > GLOBAL_MEDIA_ROOT_MAX_BYTES
            )
            if not expired and not over_budget:
                continue
            victims.append(entry)
            namespace_count -= 1
            namespace_bytes -= entry["size"]
            global_bytes -= entry["size"]

        if (
            namespace_count > MEDIA_MAX_FILES
            or namespace_bytes > MEDIA_MAX_BYTES
            or global_bytes > GLOBAL_MEDIA_ROOT_MAX_BYTES
        ):
            raise RuntimeError("Magazine protected media leaves no bounded admission capacity")
        return victims

    def _transactional_publish(self, target, payload, victims, target_entry):
        quarantine = self.global_root / f".magazine-admission-{secrets.token_hex(12)}"
        stage = quarantine / "incoming.bin"
        archive = quarantine / "rollback.zip"
        deleted = []
        published = False
        try:
            quarantine.mkdir(mode=0o700)
            atomic_write_bytes(stage, payload, mode=0o600)
            self._write_rollback_archive(archive, victims, target_entry)
            for entry in victims:
                self._require_unchanged_regular(entry)
                entry["path"].unlink()
                deleted.append(entry)
            os.replace(stage, target)
            published = True
            fsync_directory(self.root)
            archive.unlink()
        except Exception as exc:
            rollback_errors = []
            if published:
                try:
                    if target_entry is None:
                        target.unlink(missing_ok=True)
                    else:
                        self._restore_archive_entry(archive, "target", target_entry)
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            for index, entry in enumerate(victims):
                if entry in deleted:
                    try:
                        self._restore_archive_entry(archive, f"victim-{index}", entry)
                    except Exception as rollback_error:
                        rollback_errors.append(rollback_error)
            self._cleanup_quarantine(quarantine, rollback_errors)
            failure = RuntimeError("Magazine media admission could not complete atomically")
            for rollback_error in rollback_errors:
                failure.add_note(f"rollback failed: {type(rollback_error).__name__}")
            raise failure from exc
        self._cleanup_quarantine(quarantine, [])

    def _write_rollback_archive(self, archive, victims, target_entry):
        with zipfile.ZipFile(archive, mode="x", compression=zipfile.ZIP_STORED) as bundle:
            for index, entry in enumerate(victims):
                self._require_unchanged_regular(entry)
                bundle.write(entry["path"], arcname=f"victim-{index}")
            if target_entry is not None:
                self._require_unchanged_regular(target_entry)
                bundle.write(target_entry["path"], arcname="target")
        with archive.open("ab") as stream:
            os.fsync(stream.fileno())

    def _restore_archive_entry(self, archive, archive_name, entry):
        temp_path = None
        with zipfile.ZipFile(archive, mode="r") as bundle:
            with bundle.open(archive_name, mode="r") as source:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    delete=False,
                    dir=self.root,
                    prefix=".magazine-restore-",
                    suffix=".tmp",
                ) as destination:
                    temp_path = Path(destination.name)
                    shutil.copyfileobj(source, destination, length=64 * 1024)
                    destination.flush()
                    os.fsync(destination.fileno())
        try:
            os.chmod(temp_path, entry["mode"])
            os.replace(temp_path, entry["path"])
            temp_path = None
            os.utime(
                entry["path"],
                ns=(entry["atime_ns"], entry["mtime_ns"]),
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _require_unchanged_regular(self, entry):
        try:
            current = entry["path"].lstat()
        except OSError as exc:
            raise RuntimeError("Magazine media victim disappeared before admission") from exc
        if (
            _is_link_like(current)
            or not stat.S_ISREG(current.st_mode)
            or current.st_dev != entry["dev"]
            or current.st_ino != entry["ino"]
            or current.st_size != entry["size"]
            or current.st_mtime_ns != entry["mtime_ns"]
        ):
            raise RuntimeError("Magazine media victim changed before admission")

    @staticmethod
    def _cleanup_quarantine(quarantine, errors):
        try:
            shutil.rmtree(quarantine)
        except FileNotFoundError:
            return
        except OSError as exc:
            errors.append(exc)

    def _namespace_entries(self):
        entries = []
        try:
            children = list(self.root.iterdir())
        except OSError as exc:
            raise RuntimeError("Magazine media namespace could not be enumerated") from exc
        for child in children:
            try:
                info = child.lstat()
            except FileNotFoundError:
                continue
            if _is_link_like(info):
                raise RuntimeError("Magazine media namespace contains an unsafe link")
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("Magazine media namespace contains a special entry")
            key = child.stem if child.suffix == ".png" and _valid_hash(child.stem) else None
            entries.append(
                {
                    "media_key": key,
                    "path": child,
                    "size": info.st_size,
                    "mtime": info.st_mtime,
                    "mtime_ns": info.st_mtime_ns,
                    "atime_ns": info.st_atime_ns,
                    "mode": stat.S_IMODE(info.st_mode),
                    "dev": info.st_dev,
                    "ino": info.st_ino,
                }
            )
        return entries

    def _global_regular_file_bytes(self):
        total = 0
        pending = [self.global_root]
        while pending:
            directory = pending.pop()
            try:
                children = list(directory.iterdir())
            except OSError as exc:
                raise RuntimeError("Magazine global media root could not be enumerated") from exc
            for child in children:
                try:
                    info = child.lstat()
                except FileNotFoundError:
                    continue
                if _is_link_like(info):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append(child)
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if total > GLOBAL_MEDIA_ROOT_MAX_BYTES + MEDIA_MAX_OBJECT_BYTES:
                        return total
        return total

    @staticmethod
    def _validate_identity(key, suffix):
        if not _valid_hash(key) or suffix != ".png":
            raise RuntimeError("Magazine media identity is invalid")


def settings_key(settings, sources):
    settings = settings or {}
    payload = {
        "sources": [
            {
                "name": str(source.get("name") or "").strip(),
                "url": _normalize_public_url(source.get("url")),
            }
            for source in sources
        ],
        "rotation_mode": str(settings.get("rotationMode") or "random").strip().lower(),
        "fit_mode": str(settings.get("fitMode") or "triptych").strip().lower(),
        "background_color": str(settings.get("backgroundColor") or "white").strip().lower(),
        "background_style": str(settings.get("backgroundStyle") or "blur").strip().lower(),
        "show_source_label": _enabled(settings.get("showSourceLabel"), default=True),
        "daily_library_mode": _enabled(settings.get("dailyLibraryMode"), default=True),
        "library_refresh_hours": _library_refresh_hours(settings.get("libraryRefreshHours")),
    }
    return _json_hash(payload)


def settings_fingerprint(settings, sources, dimensions, date_key):
    return _json_hash(
        {
            "settings_key": settings_key(settings, sources),
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


class MagazinePresentationBank:
    """Own normalized covers and media partitioned by trusted instance."""

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
        self._loaded_document = None
        self.media = ProtectedMediaStore(
            self.media_dir,
            protected_keys_provider=self._protected_media_keys,
            global_root=self.media_dir.parent,
        )

    @classmethod
    def from_profile(cls, state_path, media_dir, fingerprint, profile):
        return cls(
            state_path,
            media_dir,
            fingerprint=fingerprint,
            base_fingerprint=profile.get("settings_fingerprint"),
            profile_settings_key=profile.get("settings_key"),
            instance_uuid=profile.get("instance_uuid"),
            date_key=profile.get("date_key"),
        )

    def load_for_data(self):
        document = self._migrate_document(self._read_document())
        profiles = document["profiles"]
        profile = profiles.get(self.fingerprint)
        if not isinstance(profile, dict):
            self._make_profile_room(document, required_slots=1)
            profile = self._empty_profile(document.get("date_buckets"))
            profiles[self.fingerprint] = profile
        else:
            profile = self._normalize_profile(profile, document.get("date_buckets"))
            profiles[self.fingerprint] = profile
            self._make_profile_room(document, required_slots=0)
        document["instance_profiles"][self.instance_uuid] = self.fingerprint
        document["active_fingerprint"] = self.fingerprint
        profile["last_used_at"] = _utc_now()
        self._loaded_document = document
        return document, profile

    def load_warm(self):
        document = self._migrate_document(self._read_document())
        fingerprint = document["instance_profiles"].get(self.instance_uuid)
        if fingerprint != self.fingerprint:
            raise RuntimeError("Magazine presentation bank is cold for this plugin instance")
        profile = document["profiles"].get(fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("Magazine presentation bank is cold for these settings")
        profile = self._normalize_profile(profile, document.get("date_buckets"))
        if profile["profile_fingerprint"] != self.fingerprint:
            raise RuntimeError("Magazine presentation bank fingerprint does not match")
        profile["last_used_at"] = _utc_now()
        document["profiles"][self.fingerprint] = profile
        self._make_profile_room(document, required_slots=0)
        self._loaded_document = document
        return document, profile

    def load_receipt_profile(self, request_id):
        document = self._migrate_document(self._read_document())
        profile = document["profiles"].get(self.fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("Magazine receipt profile is unavailable")
        profile = self._normalize_profile(profile, document.get("date_buckets"))
        pending = profile.get("pending_selection")
        if not isinstance(pending, dict) or pending.get("request_id") != request_id:
            raise RuntimeError("Magazine receipt no longer matches a pending selection")
        document["profiles"][self.fingerprint] = profile
        self._loaded_document = document
        return document, profile

    def save(self, document):
        document["presentation_schema_version"] = SCHEMA_VERSION
        validate_state_payload_size(document)
        _atomic_write_bounded_json(self.state_path, document)
        self._loaded_document = document

    def ready_records(self, profile, *, prune, now=None):
        ready = []
        survivors = []
        protected = self._protected_record_keys(profile)
        now = _coerce_datetime(now) or datetime.now(timezone.utc)
        for record in profile["records"]:
            try:
                self._ensure_record_retained(record, now=now)
                self.load_media(record, now=now)
            except RuntimeError:
                if record["record_key"] in protected:
                    survivors.append(record)
                continue
            survivors.append(record)
            if record.get("date_key") == self.date_key and self.record_provenance(record, now=now) != "stale_cache":
                ready.append(record)
        if prune and len(survivors) != len(profile["records"]):
            profile["records"] = survivors[-MAX_RECORDS_PER_PROFILE:]
        return ready

    def stale_current_records(self, profile, *, now=None):
        current = profile.get("current_selection")
        if not isinstance(current, dict):
            return []
        try:
            selected = self.selection_records(profile, current, load_media=True, now=now)
        except RuntimeError:
            return []
        if any(self.record_provenance(record, now=now) == "stale_cache" for record, _image in selected):
            return selected
        return []

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
                    raise RuntimeError("Magazine protected cover metadata is missing")
                if record_key not in seen:
                    seen.add(record_key)
                    protected.append(record)
        return protected

    def ingest(self, profile, source, cover, image, *, fetched_at=None):
        normalized = self.normalize_cover(source, cover)
        normalized_image = self._normalize_media_image(image)
        media_key = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        output = BytesIO()
        normalized_image.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Magazine cover media exceeds its object budget")
        self.media.put_bytes(media_key, payload, suffix=".png")
        source_id = normalized["source_id"]
        record_key = sha256(f"{source_id}\0{normalized['image_url']}".encode("utf-8")).hexdigest()
        record = {
            **normalized,
            "record_key": record_key,
            "media_key": media_key,
            "width": normalized_image.width,
            "height": normalized_image.height,
            "fetched_at": _iso_datetime(fetched_at),
            "date_key": self.date_key,
        }
        records = list(profile["records"])
        replaced = False
        for index, candidate in enumerate(records):
            if candidate.get("record_key") == record_key or candidate.get("source_id") == source_id:
                if candidate.get("record_key") in self._protected_record_keys(profile):
                    break
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        protected = self._protected_record_keys(profile)
        while len(records) > MAX_RECORDS_PER_PROFILE:
            victim = next(
                (
                    index
                    for index, candidate in enumerate(records)
                    if candidate.get("record_key") not in protected
                    and candidate.get("record_key") != record_key
                ),
                None,
            )
            if victim is None:
                raise RuntimeError("Magazine protected cover metadata fills the record budget")
            records.pop(victim)
        profile["records"] = records
        return record

    def recover_media(self, profile, record, image, *, recovered_at=None):
        normalized = self.normalize_cover(record, record)
        expected_media_key = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        if record.get("media_key") != expected_media_key:
            raise RuntimeError("Magazine protected media identity does not match its URL")
        normalized_image = self._normalize_media_image(image)
        output = BytesIO()
        normalized_image.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Magazine recovered media exceeds its object budget")
        self.media.put_bytes(expected_media_key, payload, suffix=".png")
        updated = {
            **record,
            "width": normalized_image.width,
            "height": normalized_image.height,
            "media_recovered_at": _iso_datetime(recovered_at),
        }
        for index, candidate in enumerate(profile["records"]):
            if candidate.get("record_key") == record.get("record_key"):
                profile["records"][index] = updated
                return updated
        raise RuntimeError("Magazine protected cover metadata disappeared during recovery")

    def normalize_cover(self, source, cover):
        if not isinstance(source, dict) or not isinstance(cover, dict):
            raise RuntimeError("Magazine cover metadata is invalid")
        source_name = str(source.get("name") or cover.get("source_name") or "").strip()[:200]
        source_url = _normalize_public_url(source.get("url") or cover.get("source_url"))
        image_url = _normalize_public_url(cover.get("image_url"))
        page_url = _normalize_public_url(cover.get("page_url") or source_url)
        if not source_name or not source_url or not image_url:
            raise RuntimeError("Magazine cover source metadata is incomplete")
        source_id = f"{source_name}|{source_url}"
        return {
            "source_id": source_id,
            "source_name": source_name,
            "source_url": source_url,
            "image_url": image_url,
            "page_url": page_url,
            "title": str(cover.get("title") or source_name).strip()[:600],
        }

    def choose_selection(self, profile, ready, fit_mode, rotation_mode="random"):
        if not ready:
            raise RuntimeError("Magazine presentation bank has no fresh cover records")
        current_keys = set((profile.get("current_selection") or {}).get("record_keys", []))
        pending_keys = set((profile.get("pending_selection") or {}).get("record_keys", []))
        bucket = profile.get("date_buckets", {}).get(self.date_key, {})
        seen_ids = {str(value) for value in bucket.get("seen_source_ids", [])}
        candidates = [
            record
            for record in ready
            if record["record_key"] not in current_keys
            and record["record_key"] not in pending_keys
            and record["source_id"] not in seen_ids
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
        if str(rotation_mode).strip().lower() not in {"rotate", "sequential", "single"}:
            random.shuffle(candidates)
        count = 3 if str(fit_mode).lower() in {"triptych", "three_covers", "gallery"} else 1
        chosen = candidates[:count]
        if not chosen:
            raise RuntimeError("Magazine presentation bank could not choose a cover")
        return {
            "record_keys": [record["record_key"] for record in chosen],
            "request_id": None,
            "date_key": self.date_key,
            "layout": "triptych" if len(chosen) > 1 else "single",
            "reset_seen": reset_seen,
        }

    def ensure_current(self, document, profile, ready, fit_mode, rotation_mode="random"):
        valid_keys = {record["record_key"] for record in profile["records"]}
        current = profile.get("current_selection")
        if self._selection_is_valid(current, valid_keys):
            return current
        current = self.choose_selection(profile, ready, fit_mode, rotation_mode)
        profile["current_selection"] = current
        self.save(document)
        return current

    def selection_records(self, profile, selection, *, load_media, now=None):
        if not isinstance(selection, dict):
            raise RuntimeError("Magazine bank selection is missing")
        records = {record["record_key"]: record for record in profile["records"]}
        selected = []
        for record_key in selection.get("record_keys", []):
            record = records.get(record_key)
            if record is None:
                raise RuntimeError("Magazine selected cover metadata is missing")
            self._ensure_record_retained(record, now=now)
            image = self.load_media(record, now=now) if load_media else None
            selected.append((record, image))
        if not selected:
            raise RuntimeError("Magazine bank selection is empty")
        return selected

    def apply_trusted_origin(self, document, profile, request):
        if profile["last_applied_origin_commit_id"] == request.origin_display_commit_id:
            return None
        committed = self._commit_selection(profile, profile.get("current_selection"), request.requested_at)
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
        selected = self.selection_records(
            profile,
            pending,
            load_media=True,
            now=receipt.committed_at,
        )
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

    def record_provenance(self, record, *, now=None):
        now = _coerce_datetime(now) or datetime.now(timezone.utc)
        fetched_at = _coerce_datetime(record.get("fetched_at"))
        if fetched_at is None or (now - fetched_at).total_seconds() > COVER_FRESH_SECONDS:
            return "stale_cache"
        return "fresh_cache"

    def load_media(self, record, *, now=None):
        self._ensure_record_retained(record, now=now)
        media_key = record.get("media_key")
        if not _valid_hash(media_key):
            raise RuntimeError("Magazine cover media key is invalid")
        target = self.media.path(media_key, suffix=".png")
        try:
            info = target.lstat()
        except OSError as exc:
            raise RuntimeError("Magazine cover media is missing") from exc
        if target.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Magazine cover media is not a regular file")
        if info.st_size <= 0 or info.st_size > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Magazine cover media exceeds its object budget")
        payload = self.media.get_bytes(media_key, suffix=".png")
        if payload is None or not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Magazine cover media is unavailable")
        try:
            source = safe_open_image(payload, limits=MEDIA_IMAGE_LIMITS)
            self._validate_media_dimensions(source.size)
            return self._normalize_media_image(source)
        except ImageLimitError as exc:
            raise RuntimeError(
                "Magazine cover media dimensions or safety limits were exceeded"
            ) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Magazine cover media could not be decoded") from exc

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
            if _coerce_datetime(candidate.get("last_used_at")) is None:
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
            "refill_in_progress": True,
            "hydration_cursor": 0,
            "library_refreshed_at": None,
            "library_last_attempt_at": None,
            "library_pool_key": None,
            "library_scan_source_ids": [],
            "library_scan_started_at": None,
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
        profile["date_buckets"] = deepcopy(_bounded_date_buckets(source.get("date_buckets")))
        normalized_records = []
        for record in profile.get("records") or []:
            if self._valid_record(record):
                normalized_records.append({**record, **self.normalize_cover(record, record)})
        profile["records"] = normalized_records[-MAX_RECORDS_PER_PROFILE:]
        profile["refill_in_progress"] = profile.get("refill_in_progress") is True
        try:
            profile["hydration_cursor"] = max(0, int(profile.get("hydration_cursor") or 0))
        except (TypeError, ValueError):
            profile["hydration_cursor"] = 0
        scan_source_ids = profile.get("library_scan_source_ids")
        if not isinstance(scan_source_ids, list):
            scan_source_ids = []
        profile["library_scan_source_ids"] = [
            value[:1000]
            for value in scan_source_ids[:MAX_SEEN_SOURCES]
            if isinstance(value, str) and value
        ]
        if _coerce_datetime(profile.get("library_scan_started_at")) is None:
            profile["library_scan_started_at"] = None
        valid_keys = {record["record_key"] for record in profile["records"]}
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if selection is not None and not self._selection_is_valid(selection, valid_keys):
                raise RuntimeError("Magazine protected selection metadata is invalid")
        return profile

    def _valid_record(self, record):
        if not isinstance(record, dict):
            return False
        structure = (
            _valid_hash(record.get("record_key"))
            and _valid_hash(record.get("media_key"))
            and isinstance(record.get("source_id"), str)
            and bool(record["source_id"])
            and isinstance(record.get("date_key"), str)
            and _coerce_datetime(record.get("fetched_at")) is not None
            and isinstance(record.get("width"), int)
            and isinstance(record.get("height"), int)
        )
        if not structure:
            return False
        try:
            normalized = self.normalize_cover(record, record)
            self._validate_media_dimensions((record["width"], record["height"]))
        except RuntimeError:
            return False
        expected_media_key = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        expected_record_key = sha256(
            f"{normalized['source_id']}\0{normalized['image_url']}".encode("utf-8")
        ).hexdigest()
        return record["media_key"] == expected_media_key and record["record_key"] == expected_record_key

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
        return isinstance(selection.get("date_key"), str) and selection.get("layout") in {"single", "triptych"}

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
                raise RuntimeError("Magazine presentation profile capacity is fully active")
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

    def _protected_media_keys(self):
        protected = set()
        document = self._loaded_document or {}
        for profile in (document.get("profiles") or {}).values():
            if not isinstance(profile, dict):
                continue
            record_by_key = {
                record.get("record_key"): record
                for record in profile.get("records") or []
                if isinstance(record, dict)
            }
            for name in ("current_selection", "pending_selection"):
                selection = profile.get(name)
                if not isinstance(selection, dict):
                    continue
                for record_key in selection.get("record_keys", []):
                    media_key = (record_by_key.get(record_key) or {}).get("media_key")
                    if _valid_hash(media_key):
                        protected.add(media_key)
        return protected

    def _ensure_record_retained(self, record, *, now=None):
        now = _coerce_datetime(now) or datetime.now(timezone.utc)
        fetched_at = _coerce_datetime(record.get("fetched_at"))
        if fetched_at is None or (now - fetched_at).total_seconds() > MEDIA_MAX_AGE_SECONDS:
            raise RuntimeError("Magazine cover record is expired")

    def _commit_selection(self, profile, selection, committed_at):
        if not isinstance(selection, dict):
            return None
        selected = self.selection_records(
            profile,
            selection,
            load_media=True,
            now=committed_at,
        )
        records = [record for record, _image in selected]
        _commit_records(profile, records, selection, committed_at)
        return records

    def _normalize_media_image(self, image):
        if not isinstance(image, Image.Image):
            raise RuntimeError("Magazine cover media is not an image")
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
            raise RuntimeError("Magazine cover media dimensions exceed the safety limit")


def _commit_records(profile, records, selection, committed_at):
    incoming_at = _coerce_datetime(committed_at)
    if incoming_at is None:
        raise RuntimeError("Magazine display receipt timestamp is invalid")
    date_key = selection.get("date_key")
    if not isinstance(date_key, str) or not date_key:
        raise RuntimeError("Magazine display receipt date bucket is invalid")
    buckets = profile.setdefault("date_buckets", {})
    bucket = buckets.setdefault(date_key, {})
    if selection.get("reset_seen"):
        bucket["seen_source_ids"] = []
    seen = [str(value) for value in bucket.get("seen_source_ids", []) if value]
    for record in records:
        source_id = record["source_id"]
        if source_id not in seen:
            seen.append(source_id)
    bucket["seen_source_ids"] = seen[-MAX_SEEN_SOURCES:]
    existing_at = _coerce_datetime(bucket.get("committed_at"))
    if existing_at is None or incoming_at >= existing_at:
        bucket["last_source_id"] = records[-1]["source_id"]
        bucket["committed_at"] = incoming_at.isoformat()
    profile["date_buckets"] = _bounded_date_buckets(buckets)


def validate_state_payload_size(payload):
    validate_state_shape(payload)
    try:
        encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Magazine state could not be encoded safely") from exc
    if len(encoded) > MAX_STATE_BYTES:
        raise RuntimeError("Magazine state exceeds the size limit")
    return len(encoded)


def validate_state_shape(payload):
    if not isinstance(payload, dict):
        raise RuntimeError("Magazine state must be an object")
    profiles = payload.get("profiles")
    if profiles is not None and not isinstance(profiles, dict):
        raise RuntimeError("Magazine profiles must be an object")
    if isinstance(profiles, dict):
        if len(profiles) > MAX_PROFILES:
            raise RuntimeError("Magazine profile capacity exceeds the limit")
        for profile in profiles.values():
            if not isinstance(profile, dict):
                raise RuntimeError("Magazine profile must be an object")
            records = profile.get("records", [])
            if not isinstance(records, list) or len(records) > MAX_RECORDS_PER_PROFILE:
                raise RuntimeError("Magazine record capacity exceeds the limit")
            if len(profile) > len(_PROFILE_KEYS):
                raise RuntimeError("Magazine profile metadata exceeds the field limit")
            if not all(isinstance(record, dict) and len(record) <= 24 for record in records):
                raise RuntimeError("Magazine record metadata exceeds the field limit")
            for name in ("current_selection", "pending_selection"):
                selection = profile.get(name)
                if selection is None:
                    continue
                if not isinstance(selection, dict) or len(selection) > 9:
                    raise RuntimeError("Magazine selection metadata exceeds the field limit")
                keys = selection.get("record_keys")
                if not (
                    isinstance(keys, list)
                    and 0 < len(keys) <= 3
                    and all(_valid_hash(key) for key in keys)
                ):
                    raise RuntimeError("Magazine selection capacity exceeds the limit")
            _validate_date_buckets(profile.get("date_buckets"))
    mappings = payload.get("instance_profiles")
    if mappings is not None and not isinstance(mappings, dict):
        raise RuntimeError("Magazine instance profiles must be an object")
    if isinstance(mappings, dict):
        if len(mappings) > MAX_PROFILES:
            raise RuntimeError("Magazine instance profile capacity exceeds the limit")
        surviving = profiles if isinstance(profiles, dict) else {}
        if any(
            not isinstance(instance_uuid, str)
            or not isinstance(fingerprint, str)
            or fingerprint not in surviving
            for instance_uuid, fingerprint in mappings.items()
        ):
            raise RuntimeError("Magazine instance profile references missing profile")
    _validate_date_buckets(payload.get("date_buckets"))


def _validate_date_buckets(buckets):
    if buckets is not None and not isinstance(buckets, dict):
        raise RuntimeError("Magazine date buckets must be an object")
    if not isinstance(buckets, dict):
        return
    if len(buckets) > MAX_DATE_BUCKETS:
        raise RuntimeError("Magazine date bucket capacity exceeds the limit")
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            raise RuntimeError("Magazine date bucket must be an object")
        seen = bucket.get("seen_source_ids", [])
        if not isinstance(seen, list) or len(seen) > MAX_SEEN_SOURCES:
            raise RuntimeError("Magazine seen history exceeds the limit")
        if any(not isinstance(value, str) or len(value) > 800 for value in seen):
            raise RuntimeError("Magazine seen source metadata exceeds the limit")


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
            _coerce_datetime(candidates[key].get("committed_at")) or minimum,
            key,
        ),
    )
    retained = set(ranked[-MAX_DATE_BUCKETS:])
    return {key: bucket for key, bucket in candidates.items() if key in retained}


def _normalize_public_url(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Magazine URL authority is invalid") from exc
    try:
        literal_address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal_address = None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or host == "localhost"
        or host.endswith(".localhost")
        or host.endswith(".local")
        or (literal_address is not None and not literal_address.is_global)
    ):
        raise RuntimeError("Magazine URL is outside approved network authorities")
    return urlunparse(("https", host, parsed.path or "/", "", parsed.query, ""))


def _library_refresh_hours(value):
    try:
        hours = float(value or 0)
    except (TypeError, ValueError):
        hours = 0
    if hours <= 0 or hours == 12:
        return 6.0
    return max(1.0, hours)


def _enabled(value, *, default):
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "show"}


def _coerce_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_datetime(value=None):
    return (_coerce_datetime(value) or datetime.now(timezone.utc)).isoformat()


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(character in _HEX for character in value)


def _valid_request_id(value):
    return isinstance(value, str) and len(value) == 32 and all(character in _HEX for character in value)
