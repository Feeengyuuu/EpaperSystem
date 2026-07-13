"""Bounded, provider-free presentation bank for Pixiv ranking media."""

from __future__ import annotations

import json
import ipaddress
import os
import random
import secrets
import stat
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from PIL import Image, ImageOps

# Reuse the already-reviewed no-follow, root-identity, relative atomic+fsync
# implementation.  These helpers do not contain DailyArt state semantics.
from plugins.daily_art.presentation_bank import (
    _atomic_write_bounded_json as _secure_atomic_write_json,
    _bound_root_still_matches,
    _is_link_like,
    _open_posix_directory_chain,
    _read_bounded_json_fallback,
    _read_bounded_json_posix,
    _same_file_snapshot,
    _same_identity,
    _validate_state_file_stat,
    _validate_fallback_directory_chain,
    read_bounded_json_object as _secure_read_json,
)
from utils.atomic_file import atomic_write_bytes, fsync_directory


SCHEMA_VERSION = 1
READY_TARGET = 24
REFILL_THRESHOLD = 8
MAX_PROFILES = 64
MAX_RECORDS_PER_PROFILE = 50
MAX_SEEN_ILLUSTS = 5000
MAX_DATE_BUCKETS = 366
MAX_STATE_BYTES = 4 * 1024 * 1024
MEDIA_MAX_AGE_SECONDS = 48 * 60 * 60
MEDIA_MAX_FILES = 64
MEDIA_MAX_BYTES = 128 * 1024 * 1024
MEDIA_MAX_OBJECT_BYTES = 12 * 1024 * 1024
MEDIA_MAX_DIMENSION = 8192
MEDIA_MAX_PIXELS = 32_000_000
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
    "source_provenance",
    "last_used_at",
}
_TEXT_LIMITS = {
    "illust_id": 160,
    "title": 600,
    "artist": 400,
    "requested_mode": 40,
    "effective_mode": 40,
    "content_rating": 16,
    "source_status": 24,
}
_PIXIV_HOST_SUFFIXES = ("pixiv.net", "pximg.net")


def settings_key(settings):
    """Canonical effective defaults; excludes secrets and runtime-only keys."""

    settings = settings or {}
    payload = {
        "ranking_mode": str(settings.get("rankingMode") or "day_r18").strip(),
        "pool_size": _bounded_int(settings.get("poolSize"), 20, 1, 50),
        "fit_mode": str(settings.get("fitMode") or "auto_layout").strip().lower(),
        "background_color": str(settings.get("backgroundColor") or "black").strip().lower(),
        "show_info_overlay": _enabled(settings.get("showInfoOverlay"), False),
        "daily_pool_mode": _enabled(settings.get("dailyPoolMode"), True),
    }
    return _json_hash(payload)


def settings_fingerprint(
    settings,
    dimensions,
    date_key,
    *,
    effective_mode,
    content_rating,
):
    return _json_hash(
        {
            "settings_key": settings_key(settings),
            "dimensions": [int(dimensions[0]), int(dimensions[1])],
            "date_key": str(date_key),
            "effective_mode": str(effective_mode),
            "content_rating": str(content_rating),
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


class _PixivMediaStore:
    """Path-safe atomic storage without generic cache-manager eviction."""

    def __init__(self, root):
        self.root = Path(root)

    def path(self, key, *, suffix=""):
        if not _valid_hash(key) or suffix != ".png":
            raise RuntimeError("Pixiv media path is invalid")
        return self.root / f"{key}{suffix}"

    def put_bytes(self, key, payload, *, suffix=""):
        if not isinstance(payload, bytes):
            raise RuntimeError("Pixiv media payload is invalid")
        target = self.path(key, suffix=suffix)
        try:
            _validate_fallback_directory_chain(self.root, create=True)
            atomic_write_bytes(target, payload, mode=0o600)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Pixiv media could not be written safely") from exc
        return target

    def stage_bytes(self, key, payload, *, suffix=""):
        if not isinstance(payload, bytes):
            raise RuntimeError("Pixiv media payload is invalid")
        target = self.path(key, suffix=suffix)
        descriptor = None
        stage = None
        try:
            _validate_fallback_directory_chain(self.root, create=True)
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=self.root,
            )
            stage = Path(raw_path)
            os.chmod(stage, 0o600)
            _write_all_bytes(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            return stage
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Pixiv media could not be staged safely") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if stage is not None and descriptor is not None:
                try:
                    stage.unlink(missing_ok=True)
                except OSError:
                    pass

    def publish_stage(self, stage, key, *, suffix=""):
        target = self.path(key, suffix=suffix)
        stage = Path(stage)
        try:
            root_before = _validate_fallback_directory_chain(self.root, create=False)
            stage_info = stage.lstat()
            if _is_link_like(stage_info) or not stat.S_ISREG(stage_info.st_mode):
                raise RuntimeError("Pixiv staged media path is unsafe")
            try:
                target_info = target.lstat()
            except FileNotFoundError:
                target_info = None
            if target_info is not None and (
                _is_link_like(target_info) or not stat.S_ISREG(target_info.st_mode)
            ):
                raise RuntimeError("Pixiv media target is unsafe")
            root_after = _validate_fallback_directory_chain(self.root, create=False)
            if not _same_identity(root_before, root_after):
                raise RuntimeError("Pixiv media root identity changed before publish")
            os.replace(stage, target)
            fsync_directory(self.root)
            return target
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Pixiv media could not be published safely") from exc

    @staticmethod
    def discard_stage(stage):
        if stage is None:
            return
        try:
            Path(stage).unlink(missing_ok=True)
        except OSError:
            pass


class PixivPresentationBank:
    """Normalized Pixiv records partitioned by a trusted playlist identity."""

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
        self.date_key = str(date_key)
        self.media = _PixivMediaStore(self.media_dir)
        self._loaded_document = None

    def load_for_data(self):
        document = self._migrate_document(self._read_document())
        profiles = document["profiles"]
        profile = profiles.get(self.fingerprint)
        if not isinstance(profile, dict):
            self._make_profile_room(document, required_slots=1)
            profile = self._empty_profile()
            profiles[self.fingerprint] = profile
        else:
            profile = self._normalize_profile(profile)
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
            raise RuntimeError("Pixiv presentation bank is cold for this instance")
        profile = document["profiles"].get(fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("Pixiv presentation bank is unavailable")
        profile = self._normalize_profile(profile)
        document["profiles"][fingerprint] = profile
        self._make_profile_room(document, required_slots=0)
        self._loaded_document = document
        return document, profile

    def load_receipt_profile(self, request_id):
        document = self._migrate_document(self._read_document())
        profile = document["profiles"].get(self.fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("Pixiv receipt profile is unavailable")
        profile = self._normalize_profile(profile)
        pending = profile.get("pending_selection")
        if not isinstance(pending, dict) or pending.get("request_id") != request_id:
            raise RuntimeError("Pixiv receipt no longer matches a pending selection")
        document["profiles"][self.fingerprint] = profile
        self._loaded_document = document
        return document, profile

    def save(self, document, *, before_commit=None):
        candidate = deepcopy(document)
        candidate["presentation_schema_version"] = SCHEMA_VERSION
        validate_state_payload_size(candidate)
        if before_commit is None:
            _secure_atomic_write_json(self.state_path, candidate)
        else:
            _atomic_write_json_before_commit(
                self.state_path,
                candidate,
                before_commit,
            )

    def ready_records(self, profile, *, prune):
        ready = []
        survivors = []
        protected = self._protected_record_keys(profile)
        for record in profile["records"]:
            same_day = record.get("date_key") == self.date_key
            try:
                if same_day:
                    self.load_media(record, allow_stale=True)
                    downloaded = _parse_datetime(record.get("downloaded_at"))
                    if (
                        downloaded is not None
                        and (datetime.now(timezone.utc) - downloaded).total_seconds()
                        > MEDIA_MAX_AGE_SECONDS
                    ):
                        record["source_status"] = "stale"
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

    def protected_records(self, profile):
        records = {record["record_key"]: record for record in profile["records"]}
        result = []
        seen = set()
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if not isinstance(selection, dict):
                continue
            for key in selection.get("record_keys", []):
                record = records.get(key)
                if record is None:
                    raise RuntimeError("Pixiv protected metadata is missing")
                if key not in seen:
                    seen.add(key)
                    result.append(record)
        return result

    def ingest(
        self,
        profile,
        candidate,
        image,
        *,
        downloaded_at=None,
        before_commit=None,
    ):
        normalized = self.normalize_candidate(candidate)
        normalized_image = self._normalize_media_image(image)
        media_key = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        output = BytesIO()
        normalized_image.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Pixiv media exceeds its object budget")
        victims = self._plan_media_budget(profile, media_key, len(payload))
        record_key = sha256(
            f"{normalized['illust_id']}\0{normalized['image_url']}".encode("utf-8")
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
        for index, existing in enumerate(records):
            if existing.get("record_key") == record_key:
                records[index] = record
                break
        else:
            records.append(record)
        protected = self._protected_record_keys(profile)
        while len(records) > MAX_RECORDS_PER_PROFILE:
            victim = next(
                (
                    index
                    for index, existing in enumerate(records)
                    if existing.get("record_key") not in protected
                    and existing.get("record_key") != record_key
                ),
                None,
            )
            if victim is None:
                raise RuntimeError("Pixiv protected metadata fills the record budget")
            records.pop(victim)
        self._commit_media_transaction(
            profile,
            records,
            media_key,
            payload,
            victims,
            before_commit,
        )
        return record

    def recover_media(
        self,
        profile,
        record,
        image,
        *,
        downloaded_at=None,
        before_commit=None,
    ):
        normalized = self.normalize_candidate(record)
        expected_media = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        if record.get("media_key") != expected_media:
            raise RuntimeError("Pixiv protected media identity does not match")
        normalized_image = self._normalize_media_image(image)
        output = BytesIO()
        normalized_image.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Pixiv recovered media exceeds its object budget")
        victims = self._plan_media_budget(profile, expected_media, len(payload))
        updated = {
            **record,
            "width": normalized_image.width,
            "height": normalized_image.height,
            "downloaded_at": downloaded_at or _utc_now(),
        }
        records = list(profile["records"])
        target_index = None
        for index, existing in enumerate(records):
            if existing.get("record_key") == record.get("record_key"):
                target_index = index
                break
        if target_index is None:
            raise RuntimeError("Pixiv protected metadata disappeared during recovery")
        records[target_index] = updated
        self._commit_media_transaction(
            profile,
            records,
            expected_media,
            payload,
            victims,
            before_commit,
        )
        return updated

    def normalize_candidate(self, candidate):
        if not isinstance(candidate, dict):
            raise RuntimeError("Pixiv ranking metadata is invalid")
        illust_id = _bounded_text(candidate.get("illust_id"), _TEXT_LIMITS["illust_id"])
        if not illust_id:
            raise RuntimeError("Pixiv illustration ID is missing")
        normalized = {
            "illust_id": illust_id,
            "rank": _bounded_int(candidate.get("rank"), 0, 0, 100000),
            "title": _bounded_text(candidate.get("title"), _TEXT_LIMITS["title"]),
            "artist": _bounded_text(candidate.get("artist"), _TEXT_LIMITS["artist"]),
            "tags": [
                _bounded_text(value, 120)
                for value in (candidate.get("tags") or [])[:100]
                if _bounded_text(value, 120)
            ],
            "image_url": self._normalize_source_url(candidate.get("image_url"), required=True),
            "page_url": self._normalize_source_url(candidate.get("page_url"), required=False),
            "requested_mode": _bounded_text(candidate.get("requested_mode"), 40),
            "effective_mode": _bounded_text(candidate.get("effective_mode"), 40),
            "content_rating": _bounded_text(candidate.get("content_rating"), 16),
            "authenticated": candidate.get("authenticated") is True,
            "source_status": _bounded_text(candidate.get("source_status") or "fresh", 24),
        }
        if normalized["content_rating"] not in {"r18", "sfw"}:
            raise RuntimeError("Pixiv content provenance is missing")
        if normalized["content_rating"] == "r18" and not normalized["authenticated"]:
            raise RuntimeError("Pixiv unauthenticated data cannot be marked healthy R-18")
        return normalized

    def choose_selection(self, document, profile, ready, fit_mode):
        if not ready:
            raise RuntimeError("Pixiv presentation bank has no ready records")
        current_keys = set((profile.get("current_selection") or {}).get("record_keys", []))
        pending_keys = set((profile.get("pending_selection") or {}).get("record_keys", []))
        bucket = profile.get("date_buckets", {}).get(self.date_key, {})
        seen_ids = {str(value) for value in bucket.get("seen_illust_ids", [])}
        candidates = [
            record
            for record in ready
            if record["record_key"] not in current_keys
            and record["record_key"] not in pending_keys
            and record["illust_id"] not in seen_ids
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
        head = candidates[0]
        if str(fit_mode).lower() == "auto_layout" and head["height"] > head["width"]:
            chosen = [item for item in candidates if item["height"] > item["width"]][:3]
        else:
            chosen = [head]
        return {
            "record_keys": [record["record_key"] for record in chosen],
            "request_id": None,
            "date_key": self.date_key,
            "layout": "strip" if len(chosen) > 1 else "single",
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
            raise RuntimeError("Pixiv selection is missing")
        records = {record["record_key"]: record for record in profile["records"]}
        selected = []
        for key in selection.get("record_keys", []):
            record = records.get(key)
            if record is None:
                raise RuntimeError("Pixiv selected metadata is missing")
            image = self.load_media(record, allow_stale=True) if load_media else None
            selected.append((record, image))
        if not selected:
            raise RuntimeError("Pixiv selection is empty")
        return selected

    def apply_trusted_origin(self, document, profile, request):
        if profile.get("last_applied_origin_commit_id") == request.origin_display_commit_id:
            return None
        committed = self._commit_selection(
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
        records = [
            record
            for record, _image in self.selection_records(profile, pending, load_media=True)
        ]
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

    def load_media(self, record, *, allow_stale=False):
        downloaded = _parse_datetime(record.get("downloaded_at"))
        age = None if downloaded is None else (datetime.now(timezone.utc) - downloaded).total_seconds()
        if downloaded is None or (age > MEDIA_MAX_AGE_SECONDS and not allow_stale):
            raise RuntimeError("Pixiv media is expired")
        media_key = record.get("media_key")
        if not _valid_hash(media_key):
            raise RuntimeError("Pixiv media key is invalid")
        target = self.media.path(media_key, suffix=".png")
        payload = self._read_media_payload(target)
        try:
            with Image.open(BytesIO(payload)) as source:
                self._validate_media_dimensions(source.size)
                source.load()
                image = self._normalize_media_image(source)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Pixiv media could not be decoded") from exc
        return image

    def cleanup(self, document, profile, *, before_save=None):
        protected = self._protected_record_keys(profile)
        retained = []
        for record in profile["records"]:
            path = self.media.path(record["media_key"], suffix=".png")
            if record["record_key"] in protected:
                retained.append(record)
                continue
            downloaded = _parse_datetime(record.get("downloaded_at"))
            expired = (
                downloaded is None
                or (datetime.now(timezone.utc) - downloaded).total_seconds()
                > MEDIA_MAX_AGE_SECONDS
                or record.get("date_key") != self.date_key
            )
            if expired:
                try:
                    self._unlink_unprotected_media(path)
                except FileNotFoundError:
                    pass
            else:
                retained.append(record)
        profile["records"] = retained[-MAX_RECORDS_PER_PROFILE:]
        self._enforce_media_budget(document)
        self.save(document, before_commit=before_save)

    def _plan_media_budget(self, profile, incoming_key, incoming_bytes):
        self.media_dir.mkdir(parents=True, exist_ok=True)
        target = self.media.path(incoming_key, suffix=".png")
        protected_media_keys = self._all_protected_media_keys(profile)
        files = []
        for path in self.media_dir.glob("*.png"):
            try:
                info = path.lstat()
            except OSError:
                continue
            if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
                continue
            files.append((path, info))
        existing = next((info for path, info in files if path == target), None)
        count = len(files) - (1 if existing is not None else 0)
        total = sum(info.st_size for path, info in files if path != target)
        candidates = sorted(
            (
                (info.st_mtime_ns, path, info.st_size)
                for path, info in files
                if path != target and path.stem not in protected_media_keys
            ),
            key=lambda item: (item[0], item[1].name),
        )
        victims = []
        while (
            count + 1 > MEDIA_MAX_FILES
            or total + incoming_bytes > MEDIA_MAX_BYTES
        ) and candidates:
            _mtime, victim, size = candidates.pop(0)
            victims.append((victim, size))
            count -= 1
            total -= size
        if count + 1 > MEDIA_MAX_FILES or total + incoming_bytes > MEDIA_MAX_BYTES:
            raise RuntimeError("Pixiv protected media fills the bank budget")
        return victims

    def _commit_media_transaction(
        self,
        profile,
        records,
        media_key,
        payload,
        victims,
        before_commit,
    ):
        target = self.media.path(media_key, suffix=".png")
        profile_snapshot = deepcopy(dict(profile))
        media_snapshot = self._snapshot_media_paths(
            [target, *(path for path, _size in victims)]
        )
        stage = self.media.stage_bytes(media_key, payload, suffix=".png")
        transaction_started = False
        try:
            if before_commit is not None:
                before_commit()
            transaction_started = True
            self.media.publish_stage(stage, media_key, suffix=".png")
            stage = None
            if before_commit is not None:
                before_commit()
            for victim, expected_size in victims:
                self._unlink_unprotected_media(victim, expected_size=expected_size)
                if before_commit is not None:
                    before_commit()
            fsync_directory(self.media_dir)
            if before_commit is not None:
                before_commit()
            profile["records"] = records
            if before_commit is not None:
                before_commit()
        except Exception:
            if transaction_started:
                self._rollback_media_transaction(
                    profile,
                    profile_snapshot,
                    media_snapshot,
                )
            raise
        finally:
            self.media.discard_stage(stage)

    def _snapshot_media_paths(self, paths):
        snapshot = {}
        for path in paths:
            path = Path(path)
            if path in snapshot:
                continue
            try:
                path.lstat()
            except FileNotFoundError:
                snapshot[path] = None
                continue
            except OSError as exc:
                raise RuntimeError("Pixiv media could not be snapshotted safely") from exc
            snapshot[path] = self._read_media_payload(path)
        return snapshot

    def _rollback_media_transaction(self, profile, profile_snapshot, media_snapshot):
        rollback_error = None
        try:
            self._restore_media_snapshot(media_snapshot)
        except Exception as exc:  # pragma: no cover - defensive rollback escalation
            rollback_error = exc
        try:
            dict.clear(profile)
            dict.update(profile, deepcopy(profile_snapshot))
        except Exception as exc:  # pragma: no cover - defensive rollback escalation
            if rollback_error is None:
                rollback_error = exc
        if rollback_error is not None:
            raise RuntimeError("Pixiv media transaction rollback failed") from rollback_error

    def _restore_media_snapshot(self, snapshot):
        try:
            _validate_fallback_directory_chain(self.media_dir, create=True)
            for path, old_payload in snapshot.items():
                if old_payload is None:
                    try:
                        info = path.lstat()
                    except FileNotFoundError:
                        continue
                    if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
                        raise RuntimeError("Pixiv rollback media path is unsafe")
                    path.unlink()
                else:
                    atomic_write_bytes(path, old_payload, mode=0o600)
            fsync_directory(self.media_dir)
            _validate_fallback_directory_chain(self.media_dir, create=False)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Pixiv media could not be restored safely") from exc

    def _all_protected_media_keys(self, active_profile=None):
        profiles = []
        document = self._loaded_document
        if isinstance(document, dict):
            profiles.extend(
                profile
                for profile in (document.get("profiles") or {}).values()
                if isinstance(profile, dict)
            )
        if isinstance(active_profile, dict) and all(profile is not active_profile for profile in profiles):
            profiles.append(active_profile)
        protected = set()
        for profile in profiles:
            protected_records = self._protected_record_keys(profile)
            for record in profile.get("records", []):
                if record.get("record_key") in protected_records and _valid_hash(record.get("media_key")):
                    protected.add(record["media_key"])
        return protected

    def _enforce_media_budget(self, document):
        self._loaded_document = document
        if not self.media_dir.exists():
            return
        protected = self._all_protected_media_keys()
        files = []
        for path in self.media_dir.glob("*.png"):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RuntimeError("Pixiv media could not be inspected safely") from exc
            if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
                continue
            files.append((path, info))
        now = datetime.now(timezone.utc).timestamp()
        for path, info in tuple(files):
            if path.stem in protected or now - info.st_mtime <= MEDIA_MAX_AGE_SECONDS:
                continue
            self._unlink_unprotected_media(path, expected_size=info.st_size)
            files.remove((path, info))
        total = sum(info.st_size for _path, info in files)
        candidates = sorted(
            (
                (info.st_mtime_ns, path, info.st_size)
                for path, info in files
                if path.stem not in protected
            ),
            key=lambda item: (item[0], item[1].name),
        )
        count = len(files)
        while (count > MEDIA_MAX_FILES or total > MEDIA_MAX_BYTES) and candidates:
            _mtime, victim, size = candidates.pop(0)
            self._unlink_unprotected_media(victim, expected_size=size)
            count -= 1
            total -= size
        if count > MEDIA_MAX_FILES or total > MEDIA_MAX_BYTES:
            raise RuntimeError("Pixiv protected media fills the bank budget")

    def _unlink_unprotected_media(self, path, *, expected_size=None):
        try:
            info = Path(path).lstat()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise RuntimeError("Pixiv media could not be inspected safely") from exc
        if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Pixiv media path is unsafe")
        if expected_size is not None and info.st_size != expected_size:
            raise RuntimeError("Pixiv media identity changed before cleanup")
        try:
            Path(path).unlink()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise RuntimeError("Pixiv media could not be removed safely") from exc

    def _read_media_payload(self, target):
        if os.name == "posix":
            return self._read_media_payload_posix(target)
        return self._read_media_payload_fallback(target)

    def _read_media_payload_posix(self, target):
        root_fd = None
        file_fd = None
        try:
            root_fd, root_stat = _open_posix_directory_chain(self.media_dir, create=False)
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_BINARY", 0)
            )
            file_fd = os.open(target.name, flags, dir_fd=root_fd)
            file_before = os.fstat(file_fd)
            path_before = os.stat(target.name, dir_fd=root_fd, follow_symlinks=False)
            self._validate_media_file_stat(file_before)
            self._validate_media_file_stat(path_before)
            if not _same_file_snapshot(file_before, path_before):
                raise RuntimeError("Pixiv media identity changed before read")
            payload = self._read_media_fd(file_fd)
            file_after = os.fstat(file_fd)
            path_after = os.stat(target.name, dir_fd=root_fd, follow_symlinks=False)
            if (
                not _same_file_snapshot(file_before, file_after)
                or not _same_file_snapshot(file_before, path_after)
                or not _bound_root_still_matches(self.media_dir, root_stat)
            ):
                raise RuntimeError("Pixiv media identity changed during read")
            return payload
        except RuntimeError:
            raise
        except (OSError, TypeError, NotImplementedError) as exc:
            raise RuntimeError("Pixiv media could not be read safely") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if root_fd is not None:
                os.close(root_fd)

    def _read_media_payload_fallback(self, target):
        file_fd = None
        try:
            root_before = _validate_fallback_directory_chain(self.media_dir, create=False)
            path_before = os.lstat(target)
            self._validate_media_file_stat(path_before)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            file_fd = os.open(target, flags)
            file_before = os.fstat(file_fd)
            self._validate_media_file_stat(file_before)
            if not _same_file_snapshot(file_before, path_before):
                raise RuntimeError("Pixiv media identity changed before read")
            payload = self._read_media_fd(file_fd)
            file_after = os.fstat(file_fd)
            path_after = os.lstat(target)
            root_after = _validate_fallback_directory_chain(self.media_dir, create=False)
            if (
                not _same_file_snapshot(file_before, file_after)
                or not _same_file_snapshot(file_before, path_after)
                or not _same_identity(root_before, root_after)
            ):
                raise RuntimeError("Pixiv media identity changed during read")
            return payload
        except RuntimeError:
            raise
        except (OSError, TypeError, NotImplementedError) as exc:
            raise RuntimeError("Pixiv media could not be read safely") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)

    @staticmethod
    def _validate_media_file_stat(info):
        if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Pixiv media is not a regular file")
        if info.st_size <= 0 or info.st_size > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Pixiv media exceeds its object budget")

    @staticmethod
    def _read_media_fd(file_fd):
        payload = bytearray()
        while True:
            chunk = os.read(file_fd, min(64 * 1024, MEDIA_MAX_OBJECT_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > MEDIA_MAX_OBJECT_BYTES:
                raise RuntimeError("Pixiv media exceeds its object budget")
        if not payload:
            raise RuntimeError("Pixiv media is unavailable")
        return bytes(payload)

    def _read_document(self):
        try:
            self.state_path.lstat()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise RuntimeError("Pixiv state path could not be inspected") from exc
        return _secure_read_json(self.state_path)

    def _migrate_document(self, source):
        document = dict(source)
        document["presentation_schema_version"] = SCHEMA_VERSION
        profiles = document.get("profiles")
        document["profiles"] = dict(profiles) if isinstance(profiles, dict) else {}
        mappings = document.get("instance_profiles")
        document["instance_profiles"] = dict(mappings) if isinstance(mappings, dict) else {}
        for fingerprint, candidate in list(document["profiles"].items()):
            if not isinstance(fingerprint, str) or not isinstance(candidate, dict):
                document["profiles"].pop(fingerprint, None)
        document["instance_profiles"] = {
            instance: fingerprint
            for instance, fingerprint in document["instance_profiles"].items()
            if isinstance(instance, str)
            and isinstance(fingerprint, str)
            and fingerprint in document["profiles"]
            and document["profiles"][fingerprint].get("instance_uuid") == instance
        }
        document.setdefault("active_fingerprint", None)
        return document

    def _empty_profile(self):
        return {
            "profile_fingerprint": self.fingerprint,
            "settings_fingerprint": self.base_fingerprint,
            "settings_key": self.profile_settings_key,
            "instance_uuid": self.instance_uuid,
            "date_key": self.date_key,
            "date_buckets": {},
            "records": [],
            "current_selection": None,
            "pending_selection": None,
            "last_applied_origin_commit_id": None,
            "last_applied_request_id": None,
            "refill_in_progress": False,
            "source_provenance": {},
            "last_used_at": _utc_now(),
        }

    def _normalize_profile(self, source):
        profile = self._empty_profile()
        for key in _PROFILE_KEYS:
            if key in source:
                profile[key] = deepcopy(source[key])
        profile["profile_fingerprint"] = self.fingerprint
        profile["settings_fingerprint"] = self.base_fingerprint
        profile["settings_key"] = self.profile_settings_key
        profile["instance_uuid"] = self.instance_uuid
        profile["date_buckets"] = _bounded_date_buckets(profile.get("date_buckets"))
        normalized = []
        for record in profile.get("records") or []:
            if self._valid_record(record):
                normalized.append({**record, **self.normalize_candidate(record)})
        profile["records"] = normalized[-MAX_RECORDS_PER_PROFILE:]
        profile["refill_in_progress"] = profile.get("refill_in_progress") is True
        valid_keys = {record["record_key"] for record in profile["records"]}
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if selection is not None and not self._selection_is_valid(selection, valid_keys):
                raise RuntimeError("Pixiv protected selection metadata is invalid")
        return profile

    def _valid_record(self, record):
        if not isinstance(record, dict):
            return False
        structure = (
            _valid_hash(record.get("record_key"))
            and _valid_hash(record.get("media_key"))
            and isinstance(record.get("date_key"), str)
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
        expected_media = sha256(normalized["image_url"].encode("utf-8")).hexdigest()
        expected_record = sha256(
            f"{normalized['illust_id']}\0{normalized['image_url']}".encode("utf-8")
        ).hexdigest()
        return record["media_key"] == expected_media and record["record_key"] == expected_record

    def _selection_is_valid(self, selection, valid_keys):
        if not isinstance(selection, dict):
            return False
        keys = selection.get("record_keys")
        if not (
            isinstance(keys, list)
            and 0 < len(keys) <= 3
            and all(isinstance(key, str) and key in valid_keys for key in keys)
        ):
            return False
        request_id = selection.get("request_id")
        return (
            (request_id is None or _valid_request_id(request_id))
            and isinstance(selection.get("date_key"), str)
            and selection.get("layout") in {"single", "strip"}
        )

    def _make_profile_room(self, document, *, required_slots):
        profiles = document["profiles"]
        mappings = document["instance_profiles"]
        while len(profiles) + required_slots > MAX_PROFILES:
            protected = set(mappings.values()) | {self.fingerprint}
            protected.update(
                fingerprint
                for fingerprint, profile in profiles.items()
                if isinstance(profile, dict)
                and isinstance(profile.get("pending_selection"), dict)
            )
            candidates = [
                (str(profile.get("last_used_at") or ""), fingerprint)
                for fingerprint, profile in profiles.items()
                if fingerprint not in protected and isinstance(profile, dict)
            ]
            if not candidates:
                raise RuntimeError("Pixiv profile capacity is fully active")
            _last_used, victim = min(candidates)
            profiles.pop(victim, None)
            for instance, fingerprint in list(mappings.items()):
                if fingerprint == victim:
                    mappings.pop(instance, None)

    def _protected_record_keys(self, profile):
        protected = set()
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if isinstance(selection, dict):
                protected.update(selection.get("record_keys", []))
        return protected

    def _commit_selection(self, profile, selection, committed_at):
        if not isinstance(selection, dict):
            return None
        records = [
            record
            for record, _image in self.selection_records(profile, selection, load_media=True)
        ]
        _commit_records(profile, records, selection, committed_at)
        return records

    def _normalize_source_url(self, value, *, required):
        if value in {None, ""} and not required:
            return ""
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("Pixiv source URL is missing")
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").rstrip(".").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("Pixiv source URL authority is invalid") from exc
        allowed = any(host == suffix or host.endswith(f".{suffix}") for suffix in _PIXIV_HOST_SUFFIXES)
        if (
            not allowed
            or parsed.scheme.lower() not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
        ):
            raise RuntimeError("Pixiv source URL is outside approved authorities")
        return urlunparse(("https", host, parsed.path or "/", "", parsed.query, ""))

    def _normalize_media_image(self, image):
        if not isinstance(image, Image.Image):
            raise RuntimeError("Pixiv media is not an image")
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
            raise RuntimeError("Pixiv media dimensions exceed the safety limit")


def _commit_records(profile, records, selection, committed_at):
    incoming = _parse_datetime(committed_at)
    if incoming is None:
        raise RuntimeError("Pixiv receipt timestamp is invalid")
    date_key = selection.get("date_key")
    if not isinstance(date_key, str) or not date_key:
        raise RuntimeError("Pixiv receipt date bucket is invalid")
    buckets = profile.setdefault("date_buckets", {})
    bucket = buckets.setdefault(date_key, {})
    if selection.get("reset_seen"):
        bucket["seen_illust_ids"] = []
    seen = [str(value) for value in bucket.get("seen_illust_ids", []) if value]
    for record in records:
        if record["illust_id"] not in seen:
            seen.append(record["illust_id"])
    bucket["seen_illust_ids"] = seen[-MAX_SEEN_ILLUSTS:]
    existing = _parse_datetime(bucket.get("committed_at"))
    if existing is None or incoming >= existing:
        bucket["last_illust_id"] = records[-1]["illust_id"]
        bucket["committed_at"] = incoming.isoformat()
    profile["date_buckets"] = _bounded_date_buckets(buckets)


def _atomic_write_json_before_commit(path, document, before_commit):
    try:
        payload = (json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Pixiv state could not be encoded safely") from exc
    if len(payload) > MAX_STATE_BYTES:
        raise RuntimeError("Pixiv state exceeds the size limit")
    if os.name == "posix":
        _atomic_write_json_before_commit_posix(Path(path), payload, before_commit)
    else:
        _atomic_write_json_before_commit_fallback(Path(path), payload, before_commit)


def _atomic_write_json_before_commit_posix(path, payload, before_commit):
    root_fd = None
    stage_fd = None
    stage_name = None
    published = False
    old_payload = None
    try:
        root_fd, root_stat = _open_posix_directory_chain(path.parent, create=True)
        try:
            target_stat = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None:
            _validate_state_file_stat(target_stat)
            old_payload = _read_bounded_json_posix(path)
        stage_name = f".{path.name}.{secrets.token_hex(12)}.tmp"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
        )
        stage_fd = os.open(stage_name, flags, 0o600, dir_fd=root_fd)
        os.fchmod(stage_fd, 0o600)
        _write_all_bytes(stage_fd, payload)
        os.fsync(stage_fd)
        os.close(stage_fd)
        stage_fd = None
        before_commit()
        if not _bound_root_still_matches(path.parent, root_stat):
            raise RuntimeError("Pixiv state root identity changed before publish")
        try:
            target_current = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_current = None
        if (target_stat is None) != (target_current is None):
            raise RuntimeError("Pixiv state identity changed before publish")
        if target_stat is not None and not _same_file_snapshot(target_stat, target_current):
            raise RuntimeError("Pixiv state identity changed before publish")
        os.replace(
            stage_name,
            path.name,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        stage_name = None
        published = True
        os.fsync(root_fd)
        before_commit()
    except RuntimeError:
        if published:
            _rollback_state_publish_posix(root_fd, path.name, old_payload)
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        if published:
            _rollback_state_publish_posix(root_fd, path.name, old_payload)
        raise RuntimeError("Pixiv state could not be written safely") from exc
    except Exception:
        if published:
            _rollback_state_publish_posix(root_fd, path.name, old_payload)
        raise
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
        if stage_name is not None and root_fd is not None:
            try:
                os.unlink(stage_name, dir_fd=root_fd)
            except OSError:
                pass
        if root_fd is not None:
            os.close(root_fd)


def _atomic_write_json_before_commit_fallback(path, payload, before_commit):
    stage_fd = None
    stage_path = None
    published = False
    old_payload = None
    try:
        root_before = _validate_fallback_directory_chain(path.parent, create=True)
        try:
            target_before = os.lstat(path)
        except FileNotFoundError:
            target_before = None
        if target_before is not None:
            _validate_state_file_stat(target_before)
            old_payload = _read_bounded_json_fallback(path)
        stage_fd, raw_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        stage_path = Path(raw_path)
        os.chmod(stage_path, 0o600)
        _write_all_bytes(stage_fd, payload)
        os.fsync(stage_fd)
        os.close(stage_fd)
        stage_fd = None
        stage_stat = os.lstat(stage_path)
        _validate_state_file_stat(stage_stat)
        before_commit()
        root_current = _validate_fallback_directory_chain(path.parent, create=False)
        if not _same_identity(root_before, root_current):
            raise RuntimeError("Pixiv state root identity changed before publish")
        try:
            target_current = os.lstat(path)
        except FileNotFoundError:
            target_current = None
        if (target_before is None) != (target_current is None):
            raise RuntimeError("Pixiv state identity changed before publish")
        if target_before is not None and not _same_file_snapshot(target_before, target_current):
            raise RuntimeError("Pixiv state identity changed before publish")
        os.replace(stage_path, path)
        stage_path = None
        published = True
        fsync_directory(path.parent)
        target_after = os.lstat(path)
        _validate_state_file_stat(target_after)
        root_after = _validate_fallback_directory_chain(path.parent, create=False)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("Pixiv state root identity changed during publish")
        before_commit()
    except RuntimeError:
        if published:
            _rollback_state_publish(path, old_payload)
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        if published:
            _rollback_state_publish(path, old_payload)
        raise RuntimeError("Pixiv state could not be written safely") from exc
    except Exception:
        if published:
            _rollback_state_publish(path, old_payload)
        raise
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
        if stage_path is not None:
            try:
                stage_path.unlink(missing_ok=True)
            except OSError:
                pass


def _rollback_state_publish(path, old_payload):
    path = Path(path)
    try:
        root_before = _validate_fallback_directory_chain(path.parent, create=False)
        if old_payload is None:
            try:
                target = os.lstat(path)
            except FileNotFoundError:
                target = None
            if target is not None:
                _validate_state_file_stat(target)
                path.unlink()
        else:
            atomic_write_bytes(path, old_payload, mode=0o600)
            _validate_state_file_stat(os.lstat(path))
        fsync_directory(path.parent)
        root_after = _validate_fallback_directory_chain(path.parent, create=False)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("Pixiv state root identity changed during rollback")
    except Exception as exc:
        raise RuntimeError("Pixiv state rollback failed") from exc


def _rollback_state_publish_posix(root_fd, target_name, old_payload):
    rollback_fd = None
    rollback_name = None
    try:
        if old_payload is None:
            try:
                target_stat = os.stat(target_name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                target_stat = None
            if target_stat is not None:
                _validate_state_file_stat(target_stat)
                os.unlink(target_name, dir_fd=root_fd)
        else:
            rollback_name = f".{target_name}.{secrets.token_hex(12)}.rollback.tmp"
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_BINARY", 0)
            )
            rollback_fd = os.open(rollback_name, flags, 0o600, dir_fd=root_fd)
            os.fchmod(rollback_fd, 0o600)
            _write_all_bytes(rollback_fd, old_payload)
            os.fsync(rollback_fd)
            os.close(rollback_fd)
            rollback_fd = None
            os.replace(
                rollback_name,
                target_name,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
            )
            rollback_name = None
        os.fsync(root_fd)
    except Exception as exc:
        raise RuntimeError("Pixiv state rollback failed") from exc
    finally:
        if rollback_fd is not None:
            os.close(rollback_fd)
        if rollback_name is not None:
            try:
                os.unlink(rollback_name, dir_fd=root_fd)
            except OSError:
                pass


def _write_all_bytes(descriptor, payload):
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise RuntimeError("Pixiv staged write was incomplete")
        offset += written


def validate_state_payload_size(payload):
    validate_state_shape(payload)
    try:
        encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Pixiv state could not be encoded safely") from exc
    if len(encoded) > MAX_STATE_BYTES:
        raise RuntimeError("Pixiv state exceeds the size limit")
    return len(encoded)


def validate_state_shape(payload):
    if not isinstance(payload, dict):
        raise RuntimeError("Pixiv state must be an object")
    profiles = payload.get("profiles", {})
    if not isinstance(profiles, dict) or len(profiles) > MAX_PROFILES:
        raise RuntimeError("Pixiv profile capacity exceeds the limit")
    for profile in profiles.values():
        if not isinstance(profile, dict) or len(profile) > len(_PROFILE_KEYS):
            raise RuntimeError("Pixiv profile metadata exceeds the limit")
        records = profile.get("records", [])
        if not isinstance(records, list) or len(records) > MAX_RECORDS_PER_PROFILE:
            raise RuntimeError("Pixiv record capacity exceeds the limit")
        if any(not isinstance(record, dict) or len(record) > 28 for record in records):
            raise RuntimeError("Pixiv record metadata exceeds the limit")
        _validate_date_buckets(profile.get("date_buckets"))
    mappings = payload.get("instance_profiles", {})
    if not isinstance(mappings, dict) or len(mappings) > MAX_PROFILES:
        raise RuntimeError("Pixiv instance profile capacity exceeds the limit")


def read_bounded_json_object(path):
    """Read through the reviewed no-follow bounded JSON implementation."""

    return _secure_read_json(path)


def atomic_write_bounded_json(path, payload):
    """Publish JSON with the reviewed relative, no-follow, fsync sequence."""

    validate_state_payload_size(payload)
    _secure_atomic_write_json(Path(path), payload)


def _validate_date_buckets(buckets):
    if buckets is None:
        return
    if not isinstance(buckets, dict) or len(buckets) > MAX_DATE_BUCKETS:
        raise RuntimeError("Pixiv date bucket capacity exceeds the limit")
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            raise RuntimeError("Pixiv date bucket must be an object")
        seen = bucket.get("seen_illust_ids", [])
        if not isinstance(seen, list) or len(seen) > MAX_SEEN_ILLUSTS:
            raise RuntimeError("Pixiv seen history exceeds the limit")


def _bounded_date_buckets(value):
    if not isinstance(value, dict):
        return {}
    candidates = {
        str(key): deepcopy(bucket)
        for key, bucket in value.items()
        if isinstance(key, str) and isinstance(bucket, dict)
    }
    if len(candidates) <= MAX_DATE_BUCKETS:
        return candidates
    ranked = sorted(candidates, key=lambda key: (_parse_datetime(candidates[key].get("committed_at")) or datetime.min.replace(tzinfo=timezone.utc), key))
    retained = set(ranked[-MAX_DATE_BUCKETS:])
    return {key: bucket for key, bucket in candidates.items() if key in retained}


def pixiv_host_allowed(hostname):
    hostname = str(hostname or "").strip().rstrip(".").lower()
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in _PIXIV_HOST_SUFFIXES)


def validate_pixiv_media_target(approved):
    if getattr(approved, "scheme", None) != "https" or getattr(approved, "port", None) != 443:
        raise RuntimeError("Pixiv media target must use HTTPS on the default port")
    if not pixiv_host_allowed(getattr(approved, "hostname", "")):
        raise RuntimeError("Pixiv media target authority is not allowed")
    addresses = tuple(getattr(approved, "addresses", ()) or ())
    if not addresses:
        raise RuntimeError("Pixiv media target has no approved public address")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise RuntimeError("Pixiv media target resolved to an invalid address") from exc
        if (
            not address.is_global
            or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped)
        ):
            raise RuntimeError("Pixiv media target resolved to a non-public address")
    return approved.normalized_url


def _bounded_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return max(int(minimum), min(int(maximum), number))


def _bounded_text(value, limit):
    return str(value or "").strip()[:limit]


def _enabled(value, default=False):
    if value is None:
        return bool(default)
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


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
    return isinstance(value, str) and len(value) == 64 and all(char in _HEX for char in value)


def _valid_request_id(value):
    return isinstance(value, str) and len(value) == 32 and all(char in _HEX for char in value)
