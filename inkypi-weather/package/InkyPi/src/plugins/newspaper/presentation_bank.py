"""Durable, provider-free presentation bank for Newspaper."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import os
import random
import stat
import uuid

from PIL import Image, ImageOps

from plugins.daily_art.presentation_bank import (
    _atomic_write_bounded_json as _secure_write_json,
    _is_link_like,
    _same_file_snapshot,
    _same_identity,
    _validate_fallback_directory_chain,
    read_bounded_json_object as _secure_read_json,
)
from utils.atomic_file import atomic_write_bytes
from utils.safe_image import ImageLimitError, ImageLimits, safe_open_image


SCHEMA_VERSION = 1
READY_TARGET = 6
REFILL_THRESHOLD = 3
MAX_PROFILES = 64
MAX_RECORDS_PER_PROFILE = 32
MAX_SEEN_IDS = 5000
MAX_STATE_BYTES = 4 * 1024 * 1024
FRESH_SECONDS = 48 * 60 * 60
RECEIPT_MAX_AGE_SECONDS = 2 * 60 * 60
MEDIA_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
MEDIA_MAX_FILES = 32
MEDIA_MAX_BYTES = 192 * 1024 * 1024
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
_HEX = frozenset("0123456789abcdef")


def settings_key(settings, sources):
    source = settings or {}
    canonical_sources = [
        {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or "")[:500],
            "type": str(item.get("type") or ""),
            "value": str(item.get("value") or "")[:8192],
        }
        for item in sources
    ]
    return _json_hash(
        {
            "media_rotation_mode": str(source.get("mediaRotationMode") or "rotate").lower(),
            "newspaper_slug": str(source.get("newspaperSlug") or "").upper(),
            "sources": canonical_sources,
        }
    )


def settings_fingerprint(settings, sources, dimensions):
    return _json_hash(
        {
            "settings_key": settings_key(settings, sources),
            "dimensions": [int(dimensions[0]), int(dimensions[1])],
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
    return sha256(encoded).hexdigest()


class _MediaTransaction:
    def __init__(self, bank):
        self.bank = bank
        self.root_existed = bank.media_dir.exists()
        self.created = []
        self.quarantined = []
        self.closed = False
        self.document = bank._loaded_document
        self.document_before = deepcopy(self.document) if isinstance(self.document, dict) else None
        self.profile = None
        self.profile_before = None
        if isinstance(self.document, dict):
            candidate = (self.document.get("profiles") or {}).get(bank.fingerprint)
            if isinstance(candidate, dict):
                self.profile = candidate
                self.profile_before = deepcopy(candidate)

    def add_created(self, path):
        self.created.append(Path(path))

    def quarantine(self, path, *, deadline_check=None):
        check = deadline_check or (lambda: None)
        check()
        path = Path(path)
        quarantine = path.with_name(f".{path.name}.{uuid.uuid4().hex}.quarantine")
        payload = self.bank._read_regular_payload(path, MEDIA_MAX_BYTES)
        check()
        self.bank._safe_replace(path, quarantine)
        self.quarantined.append((path, quarantine, payload))
        check()

    def rollback(self):
        if self.closed:
            return
        errors = []
        for path in reversed(self.created):
            try:
                self.bank._safe_unlink(path)
            except Exception as exc:
                errors.append(exc)
        for original, quarantine, payload in reversed(self.quarantined):
            try:
                if quarantine.exists():
                    self.bank._safe_replace(quarantine, original)
                else:
                    self.bank._write_media(original, payload)
            except Exception as exc:
                errors.append(exc)
        if not self.root_existed:
            try:
                self.bank._remove_empty_media_root()
            except Exception as exc:
                errors.append(exc)
        self._restore_memory()
        self.closed = True
        if errors:
            raise RuntimeError("Newspaper media transaction rollback failed") from errors[0]

    def commit(self, *, deadline_check=None):
        if self.closed:
            return
        check = deadline_check or (lambda: None)
        for _original, quarantine, _payload in self.quarantined:
            check()
            self.bank._safe_unlink(quarantine)
            check()
        check()
        self.closed = True

    def _restore_memory(self):
        if self.profile is not None and self.profile_before is not None:
            self.profile.clear()
            self.profile.update(deepcopy(self.profile_before))
        if self.document is not None and self.document_before is not None:
            self.document.clear()
            self.document.update(deepcopy(self.document_before))


class NewspaperPresentationBank:
    def __init__(
        self,
        state_path,
        media_dir,
        *,
        fingerprint,
        base_fingerprint,
        profile_settings_key,
        instance_uuid,
        now,
    ):
        self.state_path = Path(state_path)
        self.media_dir = Path(media_dir)
        self.fingerprint = str(fingerprint)
        self.base_fingerprint = str(base_fingerprint)
        self.profile_settings_key = str(profile_settings_key)
        self.instance_uuid = str(instance_uuid)
        self.now = now
        self._loaded_document = None

    def load_for_data(self):
        document = self._document()
        profile = document["profiles"].get(self.fingerprint)
        if not isinstance(profile, dict):
            self._make_profile_room(document, 1)
            profile = self._empty_profile()
        else:
            profile = self._normalize_profile(profile)
            self._make_profile_room(document, 0)
        document["profiles"][self.fingerprint] = profile
        document["instance_profiles"][self.instance_uuid] = self.fingerprint
        profile["last_used_at"] = self.now().isoformat()
        self._loaded_document = document
        return document, profile

    def load_warm(self):
        document = self._document()
        profile = document["profiles"].get(self.fingerprint)
        if not isinstance(profile, dict) or profile.get("instance_uuid") != self.instance_uuid:
            raise RuntimeError("Newspaper presentation bank is unavailable")
        profile = self._normalize_profile(profile)
        document["profiles"][self.fingerprint] = profile
        document["instance_profiles"][self.instance_uuid] = self.fingerprint
        self._loaded_document = document
        return document, profile

    def transaction(self):
        return _MediaTransaction(self)

    def save(self, document, *, deadline_check=None, transaction=None):
        check = deadline_check or (lambda: None)
        document_before = deepcopy(document)
        check()
        before = None
        try:
            self.state_path.lstat()
        except FileNotFoundError:
            pass
        else:
            before = self._read_state_bytes()
            check()
        document["schema_version"] = SCHEMA_VERSION
        _validate_state(document)
        try:
            _secure_write_json(self.state_path, document)
            check()
        except Exception:
            document.clear()
            document.update(document_before)
            if before is None:
                self._safe_unlink_state()
            else:
                _validate_fallback_directory_chain(self.state_path.parent, create=True)
                atomic_write_bytes(self.state_path, before, mode=0o600)
            if transaction is not None:
                transaction.rollback()
            raise
        if transaction is not None:
            try:
                transaction.commit(deadline_check=check)
            except Exception:
                document.clear()
                document.update(document_before)
                if before is None:
                    self._safe_unlink_state()
                else:
                    _validate_fallback_directory_chain(
                        self.state_path.parent,
                        create=True,
                    )
                    atomic_write_bytes(self.state_path, before, mode=0o600)
                transaction.rollback()
                raise

    def ready_records(self, profile, *, prune):
        ready = []
        survivors = []
        protected = self._protected_keys(profile)
        now = self.now()
        for record in profile.get("records") or []:
            try:
                self.load_media(record)
            except RuntimeError:
                if record.get("record_key") in protected:
                    survivors.append(record)
                continue
            downloaded = _parse_datetime(record.get("downloaded_at"))
            age = None if downloaded is None else (now - downloaded).total_seconds()
            render_record = dict(record)
            render_record["provenance"] = (
                "fresh_cache" if age is not None and 0 <= age <= FRESH_SECONDS else "stale_cache"
            )
            ready.append(render_record)
            survivors.append(record)
        if prune:
            profile["records"] = survivors[-MAX_RECORDS_PER_PROFILE:]
        return ready

    def ingest(
        self,
        profile,
        source,
        image,
        *,
        transaction,
        downloaded_at=None,
        deadline_check=None,
    ):
        check = deadline_check or (lambda: None)
        check()
        normalized = self.normalize_source(source)
        payload, size = self._encode_image(image)
        check()
        content_key = sha256(payload).hexdigest()
        record_key = sha256(f"{normalized['id']}\0{content_key}".encode("utf-8")).hexdigest()
        target = self.media_dir / f"{content_key}.png"
        self._admit(
            target,
            len(payload),
            transaction,
            deadline_check=check,
        )
        if not target.exists():
            self._write_media(target, payload)
            transaction.add_created(target)
            check()
        record = {
            "record_key": record_key,
            "media_key": content_key,
            "source": normalized,
            "width": size[0],
            "height": size[1],
            "downloaded_at": downloaded_at or self.now().isoformat(),
        }
        protected = self._protected_keys(profile)
        records = []
        replaced_identical = False
        for item in profile.get("records") or []:
            if item.get("record_key") == record_key:
                if not replaced_identical:
                    records.append(record)
                    replaced_identical = True
                continue
            if item.get("source", {}).get("id") == normalized["id"] and item.get("record_key") not in protected:
                continue
            records.append(item)
        if not replaced_identical:
            records.append(record)
        profile["records"] = records[-MAX_RECORDS_PER_PROFILE:]
        return record

    def cleanup(self, document, profile, *, transaction, deadline_check=None):
        check = deadline_check or (lambda: None)
        check()
        now = self.now()
        protected = self._protected_keys(profile)
        retained = []
        media_references = {}
        for candidate in (document.get("profiles") or {}).values():
            if not isinstance(candidate, dict):
                continue
            for record in candidate.get("records") or []:
                media_key = record.get("media_key")
                if _valid_hash(media_key):
                    media_references[media_key] = media_references.get(media_key, 0) + 1
        for record in profile.get("records") or []:
            downloaded = _parse_datetime(record.get("downloaded_at"))
            expired = downloaded is None or (now - downloaded).total_seconds() > MEDIA_MAX_AGE_SECONDS
            if expired and record.get("record_key") not in protected:
                media_key = record.get("media_key")
                media_references[media_key] = max(
                    0,
                    media_references.get(media_key, 0) - 1,
                )
                if media_references[media_key] == 0:
                    path = self.media_dir / f"{media_key}.png"
                    if path.exists():
                        transaction.quarantine(path, deadline_check=check)
            else:
                retained.append(record)
            check()
        profile["records"] = retained[-MAX_RECORDS_PER_PROFILE:]

    def ensure_current(self, profile, ready):
        keys = {item["record_key"] for item in ready}
        current = profile.get("current_selection")
        if isinstance(current, dict) and current.get("record_key") in keys:
            return current
        selection = self.choose_selection(profile, ready)
        profile["current_selection"] = selection
        return selection

    def choose_selection(self, profile, ready):
        if not ready:
            raise RuntimeError("Newspaper presentation bank has no ready media")
        current_key = (profile.get("current_selection") or {}).get("record_key")
        seen = set(profile.get("seen_ids") or [])
        choices = [item for item in ready if item["record_key"] != current_key and item["source"]["id"] not in seen]
        reset_seen = False
        if not choices:
            choices = [item for item in ready if item["record_key"] != current_key]
            reset_seen = bool(choices)
        if not choices:
            choices = list(ready)
            reset_seen = bool(seen)
        random.shuffle(choices)
        return {
            "record_key": choices[0]["record_key"],
            "request_id": None,
            "reset_seen": reset_seen,
        }

    def selection_media(self, profile, selection):
        if not isinstance(selection, dict):
            raise RuntimeError("Newspaper selection is missing")
        record = next(
            (item for item in profile.get("records") or [] if item.get("record_key") == selection.get("record_key")),
            None,
        )
        if record is None:
            raise RuntimeError("Newspaper selected metadata is missing")
        return record, self.load_media(record)

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
            "record_key": selection["record_key"],
            "reset_seen": bool(selection.get("reset_seen")),
            "profile_fingerprint": self.fingerprint,
            "instance_uuid": self.instance_uuid,
        }
        profile["pending_selection"] = pending
        self.save(document)
        return pending

    @staticmethod
    def reconcile_document(document, receipt, instance_uuid):
        if not isinstance(document, dict):
            return False
        _validate_state(document)
        matches = [
            (fingerprint, profile)
            for fingerprint, profile in (document.get("profiles") or {}).items()
            if isinstance(profile, dict)
            and profile.get("instance_uuid") == instance_uuid
            and isinstance(profile.get("pending_selection"), dict)
            and profile["pending_selection"].get("request_id") == receipt.request_id
        ]
        if len(matches) != 1:
            return False
        fingerprint, profile = matches[0]
        pending = profile.get("pending_selection")
        requested_at = _parse_datetime(pending.get("requested_at") if isinstance(pending, dict) else None)
        committed_at = _parse_datetime(receipt.committed_at)
        if (
            not isinstance(pending, dict)
            or pending.get("request_id") != receipt.request_id
            or pending.get("instance_uuid") != instance_uuid
            or pending.get("profile_fingerprint") != fingerprint
            or pending.get("origin_display_commit_id") == receipt.display_commit_id
            or profile.get("last_applied_request_id") == receipt.request_id
            or requested_at is None
            or committed_at is None
            or committed_at < requested_at
            or (committed_at - requested_at).total_seconds() > RECEIPT_MAX_AGE_SECONDS
        ):
            return False
        record = next(
            (item for item in profile.get("records") or [] if item.get("record_key") == pending.get("record_key")),
            None,
        )
        if record is None:
            raise RuntimeError("Newspaper receipt media is missing")
        if pending.get("reset_seen"):
            profile["seen_ids"] = []
        seen = [str(value) for value in profile.get("seen_ids") or [] if value]
        source_id = record["source"]["id"]
        if source_id not in seen:
            seen.append(source_id)
        profile["seen_ids"] = seen[-MAX_SEEN_IDS:]
        profile["current_selection"] = {
            "record_key": record["record_key"],
            "request_id": receipt.request_id,
        }
        profile["pending_selection"] = None
        profile["last_applied_request_id"] = receipt.request_id
        profile["last_committed_at"] = receipt.committed_at
        return True

    def media_exists(self, record):
        media_key = record.get("media_key")
        if not _valid_hash(media_key):
            raise RuntimeError("Newspaper media key is invalid")
        path = self.media_dir / f"{media_key}.png"
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return False
        if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Newspaper media is not a regular file")
        return True

    def rehydrate_missing_media(
        self,
        record,
        image,
        *,
        transaction,
        deadline_check=None,
    ):
        check = deadline_check or (lambda: None)
        check()
        if self.media_exists(record):
            raise RuntimeError("Newspaper missing media changed during recovery")
        payload, _size = self._encode_image(image)
        check()
        target = self.media_dir / f"{record['media_key']}.png"
        self._admit(
            target,
            len(payload),
            transaction,
            deadline_check=check,
        )
        if self.media_exists(record):
            raise RuntimeError("Newspaper missing media changed during recovery")
        self._write_media(target, payload)
        transaction.add_created(target)
        check()
        self.load_media(record)

    def load_media(self, record):
        downloaded = _parse_datetime(record.get("downloaded_at"))
        if downloaded is None or (self.now() - downloaded).total_seconds() > MEDIA_MAX_AGE_SECONDS:
            raise RuntimeError("Newspaper media is expired")
        media_key = record.get("media_key")
        if not _valid_hash(media_key):
            raise RuntimeError("Newspaper media key is invalid")
        path = self.media_dir / f"{media_key}.png"
        payload = self._read_media(path)
        try:
            source = safe_open_image(payload, limits=MEDIA_IMAGE_LIMITS)
            _validate_dimensions(source.size)
            return source.convert("RGB")
        except ImageLimitError as exc:
            raise RuntimeError(
                "Newspaper media dimensions or safety limits were exceeded"
            ) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Newspaper media could not be decoded") from exc

    def normalize_source(self, source):
        if not isinstance(source, dict):
            raise RuntimeError("Newspaper source metadata is invalid")
        source_type = str(source.get("type") or "")
        if source_type not in {"url", "headlines", "lywb", "newspaper"}:
            raise RuntimeError("Newspaper source type is invalid")
        source_id = str(source.get("id") or "").strip()[:8192]
        value = str(source.get("value") or "").strip()[:8192]
        if not source_id or not value:
            raise RuntimeError("Newspaper source identity is missing")
        return {
            "id": source_id,
            "name": str(source.get("name") or value).strip()[:500],
            "type": source_type,
            "value": value,
        }

    def _document(self):
        try:
            self.state_path.lstat()
        except FileNotFoundError:
            source = {}
        else:
            source = _secure_read_json(self.state_path)
        document = dict(source)
        profiles = document.get("profiles")
        mappings = document.get("instance_profiles")
        document["profiles"] = dict(profiles) if isinstance(profiles, dict) else {}
        document["instance_profiles"] = dict(mappings) if isinstance(mappings, dict) else {}
        document["instance_profiles"] = {
            key: value
            for key, value in document["instance_profiles"].items()
            if isinstance(key, str)
            and isinstance(value, str)
            and value in document["profiles"]
            and document["profiles"][value].get("instance_uuid") == key
        }
        document["schema_version"] = SCHEMA_VERSION
        return document

    def _empty_profile(self):
        return {
            "profile_fingerprint": self.fingerprint,
            "settings_fingerprint": self.base_fingerprint,
            "settings_key": self.profile_settings_key,
            "instance_uuid": self.instance_uuid,
            "records": [],
            "seen_ids": [],
            "current_selection": None,
            "pending_selection": None,
            "last_applied_request_id": None,
            "last_committed_at": None,
            "last_provider_attempt_at": None,
            "last_provider_status": None,
            "refill_cursor": 0,
            "refill_in_progress": False,
            "last_used_at": self.now().isoformat(),
        }

    def _normalize_profile(self, source):
        profile = self._empty_profile()
        for key in profile:
            if key in source:
                profile[key] = deepcopy(source[key])
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
        profile["records"] = [item for item in profile.get("records") or [] if self._valid_record(item)][
            -MAX_RECORDS_PER_PROFILE:
        ]
        profile["seen_ids"] = [str(value) for value in profile.get("seen_ids") or [] if value][-MAX_SEEN_IDS:]
        valid = {item["record_key"] for item in profile["records"]}
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if selection is not None and (not isinstance(selection, dict) or selection.get("record_key") not in valid):
                raise RuntimeError("Newspaper protected selection metadata is invalid")
        return profile

    def _valid_record(self, record):
        if not isinstance(record, dict):
            return False
        try:
            self.normalize_source(record.get("source"))
            _validate_dimensions((record.get("width"), record.get("height")))
        except (RuntimeError, TypeError, ValueError):
            return False
        return (
            _valid_hash(record.get("record_key"))
            and _valid_hash(record.get("media_key"))
            and _parse_datetime(record.get("downloaded_at")) is not None
        )

    def _make_profile_room(self, document, required):
        while len(document["profiles"]) + required > MAX_PROFILES:
            protected = set(document["instance_profiles"].values()) | {self.fingerprint}
            protected.update(
                key
                for key, profile in document["profiles"].items()
                if isinstance(profile.get("pending_selection"), dict)
            )
            choices = [
                (str(profile.get("last_used_at") or ""), key)
                for key, profile in document["profiles"].items()
                if key not in protected
            ]
            if not choices:
                raise RuntimeError("Newspaper profile capacity is fully active")
            _used, victim = min(choices)
            document["profiles"].pop(victim, None)
            for instance, fingerprint in list(document["instance_profiles"].items()):
                if fingerprint == victim:
                    document["instance_profiles"].pop(instance, None)

    def _protected_keys(self, profile):
        result = set()
        for name in ("current_selection", "pending_selection"):
            value = profile.get(name)
            if isinstance(value, dict) and isinstance(value.get("record_key"), str):
                result.add(value["record_key"])
        return result

    def _protected_media(self):
        protected = set()
        for profile in (self._loaded_document or {}).get("profiles", {}).values():
            if not isinstance(profile, dict):
                continue
            protected.update(
                record.get("media_key")
                for record in profile.get("records") or []
                if _valid_hash(record.get("media_key"))
            )
        return protected

    def _encode_image(self, image):
        if not isinstance(image, Image.Image):
            raise RuntimeError("Newspaper capture is not an image")
        image = ImageOps.exif_transpose(image)
        _validate_dimensions(image.size)
        normalized = image.convert("RGB")
        output = BytesIO()
        normalized.save(output, "PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Newspaper PNG exceeds its object budget")
        return payload, normalized.size

    def _admit(
        self,
        target,
        incoming_bytes,
        transaction,
        *,
        deadline_check=None,
    ):
        check = deadline_check or (lambda: None)
        check()
        files = self._scan_media_root(create=True)
        check()
        protected = self._protected_media()
        existing = [(path, info) for path, info in files if path != target]
        count = len(existing)
        total = sum(info.st_size for _path, info in existing)
        candidates = sorted(
            (info.st_mtime_ns, path, info.st_size) for path, info in existing if path.stem not in protected
        )
        while (count + 1 > MEDIA_MAX_FILES or total + incoming_bytes > MEDIA_MAX_BYTES) and candidates:
            _mtime, victim, size = candidates.pop(0)
            transaction.quarantine(victim, deadline_check=check)
            count -= 1
            total -= size
        if count + 1 > MEDIA_MAX_FILES or total + incoming_bytes > MEDIA_MAX_BYTES:
            raise RuntimeError("Newspaper protected media fills the cache budget")

    def _scan_media_root(self, *, create):
        root_before = _validate_fallback_directory_chain(self.media_dir, create=create)
        try:
            entries = list(os.scandir(self.media_dir))
        except OSError as exc:
            raise RuntimeError("Newspaper media root could not be enumerated") from exc
        files = []
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError("Newspaper media entry could not be inspected") from exc
            if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
                raise RuntimeError("Newspaper media root contains an unsafe entry")
            files.append((Path(entry.path), info))
        root_after = _validate_fallback_directory_chain(self.media_dir, create=False)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("Newspaper media root identity changed")
        return files

    def _write_media(self, target, payload):
        root_before = _validate_fallback_directory_chain(self.media_dir, create=True)
        try:
            info = os.lstat(target)
        except FileNotFoundError:
            info = None
        if info is not None and (_is_link_like(info) or not stat.S_ISREG(info.st_mode)):
            raise RuntimeError("Newspaper media target is unsafe")
        atomic_write_bytes(target, payload, mode=0o600)
        root_after = _validate_fallback_directory_chain(self.media_dir, create=False)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("Newspaper media root changed during write")

    def _read_media(self, path):
        return self._read_regular_payload(path, MEDIA_MAX_OBJECT_BYTES)

    def _read_regular_payload(self, path, max_bytes):
        root_before = _validate_fallback_directory_chain(self.media_dir, create=False)
        fd = None
        try:
            path_before = os.lstat(path)
            if _is_link_like(path_before) or not stat.S_ISREG(path_before.st_mode):
                raise RuntimeError("Newspaper media is not a regular file")
            if path_before.st_size <= 0 or path_before.st_size > max_bytes:
                raise RuntimeError("Newspaper PNG exceeds its object budget")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            file_before = os.fstat(fd)
            if not _same_file_snapshot(path_before, file_before):
                raise RuntimeError("Newspaper media identity changed before read")
            payload = bytearray()
            while len(payload) <= max_bytes:
                chunk = os.read(fd, min(64 * 1024, max_bytes + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            file_after = os.fstat(fd)
            path_after = os.lstat(path)
            root_after = _validate_fallback_directory_chain(self.media_dir, create=False)
            if (
                len(payload) > max_bytes
                or not _same_file_snapshot(file_before, file_after)
                or not _same_file_snapshot(file_before, path_after)
                or not _same_identity(root_before, root_after)
            ):
                raise RuntimeError("Newspaper media changed during read")
            return bytes(payload)
        except RuntimeError:
            raise
        except OSError as exc:
            raise RuntimeError("Newspaper media could not be read safely") from exc
        finally:
            if fd is not None:
                os.close(fd)

    def _read_state_bytes(self):
        root_before = _validate_fallback_directory_chain(
            self.state_path.parent,
            create=False,
        )
        fd = None
        try:
            path_before = os.lstat(self.state_path)
            if _is_link_like(path_before) or not stat.S_ISREG(path_before.st_mode):
                raise RuntimeError("Newspaper state is not a regular file")
            if path_before.st_size > MAX_STATE_BYTES:
                raise RuntimeError("Newspaper state exceeds the size limit")
            fd = os.open(
                self.state_path,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            file_before = os.fstat(fd)
            if not _same_file_snapshot(path_before, file_before):
                raise RuntimeError("Newspaper state identity changed before read")
            payload = os.read(fd, MAX_STATE_BYTES + 1)
            file_after = os.fstat(fd)
            path_after = os.lstat(self.state_path)
            root_after = _validate_fallback_directory_chain(
                self.state_path.parent,
                create=False,
            )
            if (
                len(payload) > MAX_STATE_BYTES
                or not _same_file_snapshot(file_before, file_after)
                or not _same_file_snapshot(file_before, path_after)
                or not _same_identity(root_before, root_after)
            ):
                raise RuntimeError("Newspaper state changed during read")
            return payload
        except RuntimeError:
            raise
        except OSError as exc:
            raise RuntimeError("Newspaper state could not be read safely") from exc
        finally:
            if fd is not None:
                os.close(fd)

    def _safe_replace(self, source, target):
        root_before = _validate_fallback_directory_chain(self.media_dir, create=False)
        source_info = os.lstat(source)
        if _is_link_like(source_info) or not stat.S_ISREG(source_info.st_mode):
            raise RuntimeError("Newspaper media transaction target is unsafe")
        try:
            target_info = os.lstat(target)
        except FileNotFoundError:
            target_info = None
        if target_info is not None:
            raise RuntimeError("Newspaper media quarantine collision")
        os.replace(source, target)
        root_after = _validate_fallback_directory_chain(self.media_dir, create=False)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("Newspaper media root changed during quarantine")

    def _safe_unlink(self, path):
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        root_before = _validate_fallback_directory_chain(Path(path).parent, create=False)
        if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Newspaper unlink target is unsafe")
        Path(path).unlink()
        root_after = _validate_fallback_directory_chain(Path(path).parent, create=False)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("Newspaper root changed during unlink")

    def _safe_unlink_state(self):
        try:
            self.state_path.lstat()
        except FileNotFoundError:
            return
        self._safe_unlink(self.state_path)

    def _remove_empty_media_root(self):
        try:
            self.media_dir.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError("Newspaper media transaction left a non-empty root") from exc


def read_state(path):
    return _secure_read_json(Path(path))


def write_state(path, document):
    _validate_state(document)
    _secure_write_json(Path(path), document)


def _validate_state(document):
    if not isinstance(document, dict):
        raise RuntimeError("Newspaper state must be an object")
    profiles = document.get("profiles", {})
    mappings = document.get("instance_profiles", {})
    if not isinstance(profiles, dict) or len(profiles) > MAX_PROFILES:
        raise RuntimeError("Newspaper profile capacity exceeds the limit")
    if not isinstance(mappings, dict) or len(mappings) > MAX_PROFILES:
        raise RuntimeError("Newspaper instance capacity exceeds the limit")
    for profile in profiles.values():
        if not isinstance(profile, dict):
            raise RuntimeError("Newspaper profile is invalid")
        if len(profile.get("records") or []) > MAX_RECORDS_PER_PROFILE:
            raise RuntimeError("Newspaper record capacity exceeds the limit")
        if len(profile.get("seen_ids") or []) > MAX_SEEN_IDS:
            raise RuntimeError("Newspaper seen history exceeds the limit")
    try:
        payload = (json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Newspaper state could not be encoded") from exc
    if len(payload) > MAX_STATE_BYTES:
        raise RuntimeError("Newspaper state exceeds the size limit")


def _validate_dimensions(size):
    width, height = int(size[0]), int(size[1])
    if (
        width <= 0
        or height <= 0
        or width > MEDIA_MAX_DIMENSION
        or height > MEDIA_MAX_DIMENSION
        or width * height > MEDIA_MAX_PIXELS
    ):
        raise RuntimeError("Newspaper image dimensions exceed the safety limit")


def _parse_datetime(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(character in _HEX for character in value)
