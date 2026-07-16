"""Bounded, provider-free presentation bank for Species Radar."""

from __future__ import annotations

import json
import ipaddress
import os
import random
import stat
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from PIL import Image, ImageOps

from plugins.daily_art.presentation_bank import (
    _atomic_write_bounded_json as _secure_atomic_write_json,
    _bound_root_still_matches,
    _is_link_like,
    _open_posix_directory_chain,
    _same_file_snapshot,
    _same_identity,
    _validate_fallback_directory_chain,
    read_bounded_json_object as _secure_read_json,
)
from utils.atomic_file import atomic_write_bytes
from utils.safe_image import ImageLimitError, ImageLimits, safe_open_image


SCHEMA_VERSION = 1
READY_TARGET = 12
REFILL_THRESHOLD = 4
MAX_PROFILES = 64
MAX_RECORDS_PER_PROFILE = 24
MAX_SEEN_IDS = 5000
MAX_RELATED_OBSERVATIONS = 5
MAX_STATE_BYTES = 4 * 1024 * 1024
SOURCE_FRESH_SECONDS = 6 * 60 * 60
PHOTO_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
PHOTO_MAX_FILES = 256
PHOTO_MAX_BYTES = 64 * 1024 * 1024
MAP_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
MAP_MAX_FILES = 64
MAP_MAX_BYTES = 64 * 1024 * 1024
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
_HEX = frozenset("0123456789abcdef")
_ALLOWED_PHOTO_SUFFIXES = (
    "inaturalist.org",
    "inaturalist-open-data.s3.amazonaws.com",
    "static.inaturalist.org",
    "gbif.org",
    "gbifusercontent.org",
)
_SOURCE_HOSTS = {
    "gbif": ("api.gbif.org",),
    "wikidata": ("query.wikidata.org",),
    "photo": _ALLOWED_PHOTO_SUFFIXES,
    "map": ("maps.googleapis.com",),
    "inaturalist": ("inaturalist.org", "www.inaturalist.org"),
}
_OBS_TEXT_LIMITS = {
    "gbif_key": 80,
    "taxon_key": 80,
    "species_key": 80,
    "scientific_name": 300,
    "species": 300,
    "display_name": 300,
    "common_name_zh": 300,
    "common_name_en": 300,
    "category_label": 120,
    "taxonomy_path": 800,
    "event_date": 80,
    "location": 500,
    "radar_location_name": 500,
    "radar_location_label": 500,
    "radar_location_id": 80,
    "image_url": 2048,
    "photo_creator": 300,
    "photo_license": 200,
    "photo_references": 2048,
    "source_bucket": 80,
}


def settings_key(settings):
    canonical = _canonical_settings(settings)
    return _json_hash(canonical)


def settings_fingerprint(settings, dimensions, bucket_key, location):
    width, height = (int(dimensions[0]), int(dimensions[1]))
    normalized_location = {
        "latitude": round(float((location or {}).get("latitude", 37.5485)), 5),
        "longitude": round(float((location or {}).get("longitude", -121.9886)), 5),
        "name": _text((location or {}).get("name") or "Fremont, CA", 500),
    }
    return _json_hash(
        {
            "settings": _canonical_settings(settings),
            "dimensions": [width, height],
            "bucket_key": str(bucket_key),
            "location": normalized_location,
        }
    )


def instance_profile_fingerprint(base_fingerprint, instance_uuid):
    return sha256(f"{base_fingerprint}\0{instance_uuid}".encode("utf-8")).hexdigest()


def _canonical_settings(settings):
    source = settings or {}
    radius_km = _integer(source.get("radiusKm", source.get("radius_km")), 25, 1, 100)
    lookback_days = _integer(source.get("lookbackDays", source.get("lookback_days")), 365, 7, 1825)
    limit = _integer(source.get("limit"), 50, 1, 100)
    return {
        "location_source": str(source.get("locationSource") or "weather").strip().lower(),
        "latitude": _optional_float(source.get("latitude")),
        "longitude": _optional_float(source.get("longitude")),
        "location_name": _text(source.get("locationName"), 500),
        "include_fremont": _bool(source.get("includeFremont", source.get("include_fremont")), True),
        "include_luoyang": _bool(source.get("includeLuoyang", source.get("include_luoyang")), True),
        "radius_km": radius_km,
        "lookback_days": lookback_days,
        "limit": limit,
        "luoyang_radius_km": _integer(source.get("luoyangRadiusKm", source.get("luoyang_radius_km")), radius_km, 1, 100),
        "luoyang_lookback_days": _integer(source.get("luoyangLookbackDays", source.get("luoyang_lookback_days")), 730, 7, 1825),
        "luoyang_limit": _integer(source.get("luoyangLimit", source.get("luoyang_limit")), limit, 1, 100),
        "refresh_hours": _integer(source.get("refreshHours", source.get("refresh_hours")), 6, 1, 24),
        "show_map": _bool(source.get("showObservationMap"), True),
        "map_zoom": _integer(source.get("observationMapZoom"), 12, 8, 17),
        "map_type": str(source.get("googleMapType") or "terrain").strip().lower(),
        "language": str(source.get("language") or "zh-CN").strip(),
        "layout": str(source.get("layout") or "default").strip(),
    }


def _json_hash(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256(encoded).hexdigest()


class _SpeciesMediaStore:
    """Atomic path-safe PNG storage without generic cache-manager eviction."""

    def __init__(self, root):
        self.root = Path(root)

    def path(self, key, suffix="", **kwargs):
        if "suffix" in kwargs:
            suffix = kwargs["suffix"]
        if not _valid_hash(key) or suffix != ".png":
            raise RuntimeError("Species media path is invalid")
        return self.root / f"{key}{suffix}"

    def put_bytes(self, key, payload, *, suffix=""):
        if not isinstance(payload, bytes):
            raise RuntimeError("Species media payload is invalid")
        target = self.path(key, suffix=suffix)
        try:
            _validate_fallback_directory_chain(self.root, create=True)
            atomic_write_bytes(target, payload, mode=0o600)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Species media could not be written safely") from exc
        return target


class SpeciesPresentationBank:
    def __init__(
        self,
        state_path,
        photo_dir,
        map_dir,
        *,
        fingerprint,
        base_fingerprint,
        profile_settings_key,
        instance_uuid,
        bucket_key,
    ):
        self.state_path = Path(state_path)
        self.photo_dir = Path(photo_dir)
        self.map_dir = Path(map_dir)
        self.fingerprint = str(fingerprint)
        self.base_fingerprint = str(base_fingerprint)
        self.profile_settings_key = str(profile_settings_key)
        self.instance_uuid = str(instance_uuid)
        self.bucket_key = str(bucket_key)
        self._loaded_document = None
        self._pending_ingest_undos = []
        self.photos = _SpeciesMediaStore(self.photo_dir)
        self.maps = _SpeciesMediaStore(self.map_dir)

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
        profile["last_used_at"] = _utc_now()
        self._loaded_document = document
        return document, profile

    def load_warm(self):
        document = self._document()
        fingerprint = document["instance_profiles"].get(self.instance_uuid)
        if fingerprint != self.fingerprint:
            raise RuntimeError("Species presentation bank is cold for this instance")
        profile = document["profiles"].get(fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("Species presentation bank is unavailable")
        profile = self._normalize_profile(profile)
        document["profiles"][fingerprint] = profile
        self._loaded_document = document
        return document, profile

    def load_receipt_profile(self, request_id):
        document = self._document()
        profile = document["profiles"].get(self.fingerprint)
        if not isinstance(profile, dict):
            raise RuntimeError("Species receipt profile is unavailable")
        profile = self._normalize_profile(profile)
        pending = profile.get("pending_selection")
        if not isinstance(pending, dict) or pending.get("request_id") != request_id:
            raise RuntimeError("Species receipt no longer matches a pending selection")
        document["profiles"][self.fingerprint] = profile
        self._loaded_document = document
        return document, profile

    def save(self, document, *, deadline_check=None):
        check = deadline_check or (lambda: None)
        check()
        try:
            self.state_path.lstat()
        except FileNotFoundError:
            state_before = None
        else:
            state_before = _secure_read_json(self.state_path)
        document["presentation_schema_version"] = SCHEMA_VERSION
        _validate_state(document)
        _secure_atomic_write_json(self.state_path, document)
        try:
            check()
        except Exception:
            if state_before is None:
                self._safe_unlink_state()
            else:
                _secure_atomic_write_json(self.state_path, state_before)
            self.rollback_pending_ingests()
            raise
        self._pending_ingest_undos.clear()

    def _safe_unlink_state(self):
        parent = self.state_path.parent
        if os.name == "posix":
            root_fd = None
            try:
                root_fd, root_stat = _open_posix_directory_chain(parent, create=False)
                info = os.stat(self.state_path.name, dir_fd=root_fd, follow_symlinks=False)
                if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
                    raise RuntimeError("Species state rollback target is unsafe")
                os.unlink(self.state_path.name, dir_fd=root_fd)
                if not _bound_root_still_matches(parent, root_stat):
                    raise RuntimeError("Species state root changed during rollback")
                return
            finally:
                if root_fd is not None:
                    os.close(root_fd)
        root_before = _validate_fallback_directory_chain(parent, create=False)
        info = os.lstat(self.state_path)
        if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Species state rollback target is unsafe")
        self.state_path.unlink()
        root_after = _validate_fallback_directory_chain(parent, create=False)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("Species state root changed during rollback")

    def ingest(
        self,
        profile,
        observation,
        photo,
        map_image=None,
        *,
        fetched_at=None,
        deadline_check=None,
    ):
        check = deadline_check or (lambda: None)
        check()
        normalized = self.normalize_observation(observation)
        if photo is None:
            raise RuntimeError("Species ready observation requires photo media")
        observation_id = _observation_id(normalized)
        photo_payload, photo_size = self._encode_image(photo)
        check()
        photo_key = sha256(f"photo\0{normalized['image_url']}".encode("utf-8")).hexdigest()
        map_payload = None
        map_size = None
        map_key = None
        if map_image is not None:
            map_payload, map_size = self._encode_image(map_image)
            check()
            map_key = sha256(f"map\0{observation_id}\0{self.bucket_key}".encode("utf-8")).hexdigest()

        profile_before = deepcopy(profile)
        roots_existed = {
            self.photo_dir: self.photo_dir.exists(),
            self.map_dir: self.map_dir.exists(),
        }
        affected = {}
        backups = {}
        photo_victims = []
        map_victims = []
        try:
            photo_victims = self._reserve(
                self.photo_dir,
                profile,
                "photo_key",
                photo_key,
                len(photo_payload),
                PHOTO_MAX_FILES,
                PHOTO_MAX_BYTES,
            )
            check()
            if map_payload is not None:
                map_victims = self._reserve(
                    self.map_dir,
                    profile,
                    "map_key",
                    map_key,
                    len(map_payload),
                    MAP_MAX_FILES,
                    MAP_MAX_BYTES,
                )
                check()
            affected = {
                self.photos.path(photo_key, suffix=".png"): self.photo_dir,
            }
            if map_key is not None:
                affected[self.maps.path(map_key, suffix=".png")] = self.map_dir
            for victim in photo_victims:
                affected[victim] = self.photo_dir
            for victim in map_victims:
                affected[victim] = self.map_dir
            backups = self._backup_paths(affected)
            check()
        except Exception:
            self._remove_new_roots(roots_existed)
            raise

        try:
            check()
            for victim in photo_victims:
                check()
                self._safe_unlink(victim, self.photo_dir)
                check()
            for victim in map_victims:
                check()
                self._safe_unlink(victim, self.map_dir)
                check()
            self.photos.put_bytes(photo_key, photo_payload, suffix=".png")
            check()
            if map_payload is not None:
                self.maps.put_bytes(map_key, map_payload, suffix=".png")
                check()

            record_key = sha256(f"{observation_id}\0{self.bucket_key}".encode("utf-8")).hexdigest()
            record = {
                "record_key": record_key,
                "observation_id": observation_id,
                "observation": normalized,
                "photo_key": photo_key,
                "map_key": map_key,
                "photo_size": list(photo_size),
                "map_size": list(map_size) if map_size else None,
                "fetched_at": fetched_at or _utc_now(),
                "bucket_key": self.bucket_key,
                "provenance": "live",
            }
            records = list(profile.get("records") or [])
            for index, existing in enumerate(records):
                if existing.get("record_key") == record_key:
                    records[index] = record
                    break
            else:
                records.append(record)
            protected = self._protected_keys(profile)
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
                    raise RuntimeError("Species protected metadata fills the record budget")
                records.pop(victim)
            profile["records"] = records
            check()
            self._pending_ingest_undos.append(
                (profile, profile_before, affected, backups, roots_existed)
            )
            return record
        except Exception:
            profile.clear()
            profile.update(profile_before)
            self._restore_paths(affected, backups, roots_existed)
            raise

    def rollback_pending_ingests(self):
        while self._pending_ingest_undos:
            profile, profile_before, affected, backups, roots_existed = (
                self._pending_ingest_undos.pop()
            )
            profile.clear()
            profile.update(profile_before)
            self._restore_paths(affected, backups, roots_existed)

    def recover_media(
        self,
        profile,
        record,
        photo,
        map_image=None,
        *,
        fetched_at=None,
        deadline_check=None,
    ):
        observation = record.get("observation")
        recovered = self.ingest(
            profile,
            observation,
            photo,
            map_image,
            fetched_at=fetched_at or record.get("fetched_at"),
            deadline_check=deadline_check,
        )
        if recovered["record_key"] != record.get("record_key"):
            raise RuntimeError("Species protected recovery identity changed")
        return recovered

    def normalize_observation(self, value):
        if not isinstance(value, dict):
            raise RuntimeError("Species observation metadata is invalid")
        normalized = {key: _text(value.get(key), limit) for key, limit in _OBS_TEXT_LIMITS.items()}
        normalized["image_url"] = _normalize_photo_url(normalized["image_url"])
        for key in ("latitude", "longitude", "distance_km", "coordinate_uncertainty_m"):
            normalized[key] = _optional_float(value.get(key))
        if not normalized["gbif_key"] and not normalized["scientific_name"]:
            raise RuntimeError("Species observation identity is missing")
        return normalized

    def ready_records(self, profile, *, prune):
        ready = []
        survivors = []
        protected = self._protected_keys(profile)
        now = datetime.now(timezone.utc)
        for record in profile.get("records") or []:
            try:
                if not record.get("observation", {}).get("image_url") or not record.get("photo_key"):
                    raise RuntimeError("Species ready record has no photo media")
                self.load_photo(record)
                if record.get("map_key"):
                    self.load_map(record)
            except RuntimeError:
                if record.get("record_key") in protected:
                    survivors.append(record)
                continue
            fetched = _parse_datetime(record.get("fetched_at"))
            same_bucket = record.get("bucket_key") == self.bucket_key
            fresh = same_bucket and fetched is not None and (now - fetched).total_seconds() <= SOURCE_FRESH_SECONDS
            record["provenance"] = "fresh_cache" if fresh else "stale_cache"
            if same_bucket:
                ready.append(record)
                survivors.append(record)
            elif record.get("record_key") in protected:
                survivors.append(record)
        if prune:
            profile["records"] = survivors[-MAX_RECORDS_PER_PROFILE:]
        return ready

    def set_related_observations(self, profile, observations):
        profile["related_observations"] = self._normalize_related_observations(
            observations,
        )

    def protected_records(self, profile):
        by_key = {item["record_key"]: item for item in profile.get("records") or []}
        result = []
        for key in self._protected_keys(profile):
            record = by_key.get(key)
            if record is None:
                raise RuntimeError("Species protected metadata is missing")
            result.append(record)
        return result

    def choose_selection(self, document, profile, ready):
        del document
        if not ready:
            raise RuntimeError("Species presentation bank has no ready records")
        current = (profile.get("current_selection") or {}).get("record_key")
        pending = (profile.get("pending_selection") or {}).get("record_key")
        seen = set(profile.get("seen_ids") or [])
        choices = [item for item in ready if item["record_key"] not in {current, pending} and item["observation_id"] not in seen]
        reset_seen = False
        if not choices:
            choices = [item for item in ready if item["record_key"] not in {current, pending}]
            reset_seen = bool(choices)
        if not choices:
            choices = list(ready)
            reset_seen = bool(seen)
        random.shuffle(choices)
        return {"record_key": choices[0]["record_key"], "request_id": None, "bucket_key": self.bucket_key, "reset_seen": reset_seen}

    def ensure_current(
        self,
        document,
        profile,
        ready,
        *,
        deadline_check=None,
        persist=True,
    ):
        check = deadline_check or (lambda: None)
        check()
        keys = {item["record_key"] for item in ready}
        current = profile.get("current_selection")
        if isinstance(current, dict) and current.get("record_key") in keys:
            check()
            return current
        current = self.choose_selection(document, profile, ready)
        check()
        if not persist:
            return current
        profile_before = deepcopy(profile)
        try:
            profile["current_selection"] = current
            check()
            self.save(document, deadline_check=check)
            check()
        except Exception:
            profile.clear()
            profile.update(profile_before)
            raise
        return current

    def selection_record(
        self,
        profile,
        selection,
        *,
        load_media,
        deadline_check=None,
    ):
        check = deadline_check or (lambda: None)
        check()
        if not isinstance(selection, dict):
            raise RuntimeError("Species selection is missing")
        record = next((item for item in profile.get("records") or [] if item.get("record_key") == selection.get("record_key")), None)
        if record is None:
            raise RuntimeError("Species selected metadata is missing")
        check()
        photo = (
            self.load_photo(record, deadline_check=check)
            if load_media and record.get("photo_key")
            else None
        )
        check()
        map_image = (
            self.load_map(record, deadline_check=check)
            if load_media and record.get("map_key")
            else None
        )
        check()
        return record, photo, map_image

    def apply_trusted_origin(self, document, profile, request):
        if profile.get("last_applied_origin_commit_id") == request.origin_display_commit_id:
            return None
        current = profile.get("current_selection")
        if isinstance(current, dict):
            record, _photo, _map = self.selection_record(profile, current, load_media=True)
            self._commit(profile, record, current, request.requested_at)
        profile["last_applied_origin_commit_id"] = request.origin_display_commit_id
        self.save(document)
        return current

    def pending_for_request(self, profile, request_id):
        pending = profile.get("pending_selection")
        return pending if isinstance(pending, dict) and pending.get("request_id") == request_id else None

    def set_pending(self, document, profile, request, selection):
        pending = {
            "record_key": selection["record_key"],
            "request_id": request.request_id,
            "origin_display_commit_id": request.origin_display_commit_id,
            "requested_at": request.requested_at,
            "bucket_key": selection["bucket_key"],
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
        incoming = _parse_datetime(receipt.committed_at)
        requested = _parse_datetime(pending.get("requested_at"))
        if incoming is None or requested is None or incoming < requested:
            return None
        record, _photo, _map = self.selection_record(profile, pending, load_media=True)
        self._commit(profile, record, pending, receipt.committed_at)
        profile["current_selection"] = {
            "record_key": pending["record_key"],
            "request_id": receipt.request_id,
            "bucket_key": pending["bucket_key"],
            "reset_seen": False,
        }
        profile["pending_selection"] = None
        profile["last_applied_request_id"] = receipt.request_id
        self.save(document)
        return record

    def load_photo(self, record, *, deadline_check=None):
        return self._load_media(
            self.photos,
            record.get("photo_key"),
            "photo",
            deadline_check=deadline_check,
        )

    def load_map(self, record, *, deadline_check=None):
        return self._load_media(
            self.maps,
            record.get("map_key"),
            "map",
            deadline_check=deadline_check,
        )

    def photo_path(self, record):
        return self.photos.path(record["photo_key"], suffix=".png")

    def cleanup(self, document, profile, *, deadline_check=None):
        check = deadline_check or (lambda: None)
        check()
        profile_before = deepcopy(profile)
        document_before = deepcopy(document)
        try:
            self.state_path.lstat()
        except FileNotFoundError:
            state_before = None
        else:
            state_before = _secure_read_json(self.state_path)
        protected = self._protected_keys(profile)
        retained = []
        affected = {}
        now = datetime.now(timezone.utc)
        for record in profile.get("records") or []:
            if record.get("record_key") in protected:
                retained.append(record)
                continue
            fetched = _parse_datetime(record.get("fetched_at"))
            expired = fetched is None or (now - fetched).total_seconds() > PHOTO_MAX_AGE_SECONDS
            if expired:
                for namespace, field in (
                    (self.photos, "photo_key"),
                    (self.maps, "map_key"),
                ):
                    key = record.get(field)
                    if (
                        _valid_hash(key)
                        and key not in self._protected_media_keys(field)
                    ):
                        affected[namespace.path(key, suffix=".png")] = namespace.root
            else:
                retained.append(record)
        roots_existed = {
            self.photo_dir: self.photo_dir.exists(),
            self.map_dir: self.map_dir.exists(),
        }
        backups = self._backup_paths(affected)
        check()
        try:
            for path, root in affected.items():
                check()
                self._safe_unlink(path, root)
                check()
            profile["records"] = retained[-MAX_RECORDS_PER_PROFILE:]
            check()
            self.save(document, deadline_check=check)
            check()
        except Exception:
            profile.clear()
            profile.update(profile_before)
            document.clear()
            document.update(document_before)
            document.setdefault("profiles", {})[self.fingerprint] = profile
            self._restore_paths(affected, backups, roots_existed)
            if state_before is None:
                try:
                    self.state_path.lstat()
                except FileNotFoundError:
                    pass
                else:
                    self._safe_unlink_state()
            else:
                _secure_atomic_write_json(self.state_path, state_before)
            raise

    def _commit(self, profile, record, selection, committed_at):
        if _parse_datetime(committed_at) is None:
            raise RuntimeError("Species receipt timestamp is invalid")
        if selection.get("reset_seen"):
            profile["seen_ids"] = []
        seen = [str(value) for value in profile.get("seen_ids") or [] if value]
        if record["observation_id"] not in seen:
            seen.append(record["observation_id"])
        profile["seen_ids"] = seen[-MAX_SEEN_IDS:]
        profile["last_committed_at"] = committed_at
        observation = record["observation"]
        profile["displayed_context"] = {
            "observation_id": record["observation_id"],
            "scientific_name": observation.get("scientific_name"),
            "display_name": observation.get("display_name"),
            "location": observation.get("radar_location_name") or observation.get("location"),
            "record_key": record["record_key"],
            "committed_at": committed_at,
        }

    def _encode_image(self, image):
        if not isinstance(image, Image.Image):
            raise RuntimeError("Species media is not an image")
        image = ImageOps.exif_transpose(image)
        _validate_dimensions(image.size)
        normalized = image.convert("RGB")
        output = BytesIO()
        normalized.save(output, "PNG", optimize=True)
        payload = output.getvalue()
        if not payload or len(payload) > MEDIA_MAX_OBJECT_BYTES:
            raise RuntimeError("Species media exceeds its object budget")
        return payload, normalized.size

    def _load_media(self, namespace, key, kind, *, deadline_check=None):
        check = deadline_check or (lambda: None)
        check()
        if not _valid_hash(key):
            raise RuntimeError(f"Species {kind} key is invalid")
        path = namespace.path(key, suffix=".png")
        check()
        payload = self._read_media_payload(
            path,
            namespace.root,
            kind,
            deadline_check=check,
        )
        check()
        try:
            check()
            source = safe_open_image(payload, limits=MEDIA_IMAGE_LIMITS)
            check()
            _validate_dimensions(source.size)
            check()
            image = source.convert("RGB")
            check()
            return image
        except ImageLimitError as exc:
            raise RuntimeError(
                f"Species {kind} media dimensions or safety limits were exceeded"
            ) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Species {kind} media could not be decoded") from exc

    def _read_media_payload(
        self,
        target,
        root,
        kind,
        max_bytes=MEDIA_MAX_OBJECT_BYTES,
        deadline_check=None,
    ):
        check = deadline_check or (lambda: None)
        check()
        if os.name == "posix":
            return self._read_media_payload_posix(
                target,
                root,
                kind,
                max_bytes,
                check,
            )
        return self._read_media_payload_fallback(
            target,
            root,
            kind,
            max_bytes,
            check,
        )

    def _read_media_payload_posix(self, target, root, kind, max_bytes, check):
        root_fd = None
        file_fd = None
        try:
            root_fd, root_stat = _open_posix_directory_chain(root, create=False)
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_BINARY", 0)
            )
            file_fd = os.open(target.name, flags, dir_fd=root_fd)
            file_before = os.fstat(file_fd)
            path_before = os.stat(target.name, dir_fd=root_fd, follow_symlinks=False)
            self._validate_media_stat(file_before, kind, max_bytes)
            self._validate_media_stat(path_before, kind, max_bytes)
            if not _same_file_snapshot(file_before, path_before):
                raise RuntimeError(f"Species {kind} media identity changed before read")
            check()
            payload = self._read_media_fd(file_fd, kind, max_bytes, check)
            check()
            file_after = os.fstat(file_fd)
            path_after = os.stat(target.name, dir_fd=root_fd, follow_symlinks=False)
            if (
                not _same_file_snapshot(file_before, file_after)
                or not _same_file_snapshot(file_before, path_after)
                or not _bound_root_still_matches(root, root_stat)
            ):
                raise RuntimeError(f"Species {kind} media identity changed during read")
            return payload
        except RuntimeError:
            raise
        except (OSError, TypeError, NotImplementedError) as exc:
            raise RuntimeError(f"Species {kind} media could not be read safely") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if root_fd is not None:
                os.close(root_fd)

    def _read_media_payload_fallback(self, target, root, kind, max_bytes, check):
        file_fd = None
        try:
            root_before = _validate_fallback_directory_chain(root, create=False)
            path_before = os.lstat(target)
            self._validate_media_stat(path_before, kind, max_bytes)
            file_fd = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            file_before = os.fstat(file_fd)
            self._validate_media_stat(file_before, kind, max_bytes)
            if not _same_file_snapshot(file_before, path_before):
                raise RuntimeError(f"Species {kind} media identity changed before read")
            check()
            payload = self._read_media_fd(file_fd, kind, max_bytes, check)
            check()
            file_after = os.fstat(file_fd)
            path_after = os.lstat(target)
            root_after = _validate_fallback_directory_chain(root, create=False)
            if (
                not _same_file_snapshot(file_before, file_after)
                or not _same_file_snapshot(file_before, path_after)
                or not _same_identity(root_before, root_after)
            ):
                raise RuntimeError(f"Species {kind} media identity changed during read")
            return payload
        except RuntimeError:
            raise
        except (OSError, TypeError, NotImplementedError) as exc:
            raise RuntimeError(f"Species {kind} media could not be read safely") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)

    @staticmethod
    def _validate_media_stat(info, kind, max_bytes):
        if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"Species {kind} media is not a regular file")
        if info.st_size <= 0 or info.st_size > max_bytes:
            raise RuntimeError(f"Species {kind} media exceeds its object budget")

    @staticmethod
    def _read_media_fd(file_fd, kind, max_bytes, check):
        payload = bytearray()
        while True:
            check()
            chunk = os.read(file_fd, min(64 * 1024, max_bytes + 1 - len(payload)))
            check()
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise RuntimeError(f"Species {kind} media exceeds its object budget")
        if not payload:
            raise RuntimeError(f"Species {kind} media is unavailable")
        return bytes(payload)

    def _reserve(self, directory, profile, field, incoming_key, incoming_bytes, max_files, max_bytes):
        protected_media = self._protected_media_keys(field)
        target = directory / f"{incoming_key}.png"
        files = self._scan_media_root(directory, create=True)
        count = sum(1 for path, _info in files if path != target)
        total = sum(info.st_size for path, info in files if path != target)
        candidates = sorted(
            (info.st_mtime_ns, path, info.st_size)
            for path, info in files
            if path != target and path.stem not in protected_media
        )
        victims = []
        while (count + 1 > max_files or total + incoming_bytes > max_bytes) and candidates:
            _mtime, victim, size = candidates.pop(0)
            count -= 1
            total -= size
            victims.append(victim)
        if count + 1 > max_files or total + incoming_bytes > max_bytes:
            raise RuntimeError("Species protected media fills the cache budget")
        return victims

    def _scan_media_root(self, directory, *, create):
        directory = Path(directory)
        if os.name == "posix":
            root_fd = None
            try:
                root_fd, root_stat = _open_posix_directory_chain(directory, create=create)
                files = []
                for name in os.listdir(root_fd):
                    try:
                        info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                    except OSError as exc:
                        raise RuntimeError("Species media entry could not be inspected safely") from exc
                    if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
                        raise RuntimeError("Species media root contains an unsafe entry")
                    files.append((directory / name, info))
                if not _bound_root_still_matches(directory, root_stat):
                    raise RuntimeError("Species media root identity changed during enumeration")
                return files
            except RuntimeError:
                raise
            except (OSError, TypeError, NotImplementedError) as exc:
                raise RuntimeError("Species media root could not be enumerated safely") from exc
            finally:
                if root_fd is not None:
                    os.close(root_fd)
        root_before = _validate_fallback_directory_chain(directory, create=create)
        files = []
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise RuntimeError("Species media root could not be enumerated safely") from exc
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError("Species media entry could not be inspected safely") from exc
            if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
                raise RuntimeError("Species media root contains an unsafe entry")
            files.append((Path(entry.path), info))
        root_after = _validate_fallback_directory_chain(directory, create=False)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("Species media root identity changed during enumeration")
        return files

    def _backup_paths(self, affected):
        backups = {}
        for path, root in affected.items():
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RuntimeError("Species media transaction could not inspect a path") from exc
            backups[path] = self._read_media_payload(
                path,
                root,
                "transaction",
                max(PHOTO_MAX_BYTES, MAP_MAX_BYTES),
            )
        return backups

    def _safe_unlink(self, path, root):
        path = Path(path)
        root = Path(root)
        if os.name == "posix":
            root_fd = None
            try:
                root_fd, root_stat = _open_posix_directory_chain(root, create=False)
                try:
                    info = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
                except FileNotFoundError:
                    return
                if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
                    raise RuntimeError("Species media unlink target is unsafe")
                os.unlink(path.name, dir_fd=root_fd)
                if not _bound_root_still_matches(root, root_stat):
                    raise RuntimeError("Species media root identity changed during unlink")
                return
            except RuntimeError:
                raise
            except (OSError, TypeError, NotImplementedError) as exc:
                raise RuntimeError("Species media could not be unlinked safely") from exc
            finally:
                if root_fd is not None:
                    os.close(root_fd)
        root_before = _validate_fallback_directory_chain(root, create=False)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError("Species media unlink target could not be inspected") from exc
        if _is_link_like(info) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Species media unlink target is unsafe")
        try:
            path.unlink()
        except OSError as exc:
            raise RuntimeError("Species media could not be unlinked safely") from exc
        root_after = _validate_fallback_directory_chain(root, create=False)
        if not _same_identity(root_before, root_after):
            raise RuntimeError("Species media root identity changed during unlink")

    def _restore_paths(self, affected, backups, roots_existed):
        errors = []
        for path, root in affected.items():
            try:
                if path in backups:
                    _validate_fallback_directory_chain(root, create=True)
                    atomic_write_bytes(path, backups[path], mode=0o600)
                else:
                    try:
                        path.lstat()
                    except FileNotFoundError:
                        continue
                    self._safe_unlink(path, root)
            except Exception as exc:
                errors.append(exc)
        try:
            self._remove_new_roots(roots_existed)
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise RuntimeError("Species media transaction rollback failed") from errors[0]

    @staticmethod
    def _remove_new_roots(roots_existed):
        for root, existed in roots_existed.items():
            if existed:
                continue
            try:
                root.lstat()
            except FileNotFoundError:
                continue
            _validate_fallback_directory_chain(root, create=False)
            try:
                root.rmdir()
            except OSError as exc:
                raise RuntimeError("Species media transaction could not remove a new root") from exc

    def _protected_keys(self, profile):
        result = set()
        for name in ("current_selection", "pending_selection"):
            value = profile.get(name)
            if isinstance(value, dict) and isinstance(value.get("record_key"), str):
                result.add(value["record_key"])
        return result

    def _protected_media_keys(self, field):
        protected = set()
        document = self._loaded_document or {}
        for candidate in (document.get("profiles") or {}).values():
            if not isinstance(candidate, dict):
                continue
            record_keys = self._protected_keys(candidate)
            protected.update(
                record.get(field)
                for record in candidate.get("records") or []
                if record.get("record_key") in record_keys and record.get(field)
            )
        return protected

    def _unlink_record_media(self, record):
        for namespace, field in ((self.photos, "photo_key"), (self.maps, "map_key")):
            key = record.get(field)
            if not _valid_hash(key) or key in self._protected_media_keys(field):
                continue
            path = namespace.path(key, suffix=".png")
            try:
                self._safe_unlink(path, namespace.root)
            except FileNotFoundError:
                pass

    def _read(self):
        try:
            self.state_path.lstat()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise RuntimeError("Species state path could not be inspected") from exc
        return _secure_read_json(self.state_path)

    def _document(self):
        source = self._read()
        document = dict(source)
        profiles = document.get("profiles")
        mappings = document.get("instance_profiles")
        document["profiles"] = dict(profiles) if isinstance(profiles, dict) else {}
        document["instance_profiles"] = dict(mappings) if isinstance(mappings, dict) else {}
        for key, profile in list(document["profiles"].items()):
            if not isinstance(key, str) or not isinstance(profile, dict):
                document["profiles"].pop(key, None)
        document["instance_profiles"] = {
            instance: fingerprint
            for instance, fingerprint in document["instance_profiles"].items()
            if isinstance(instance, str)
            and isinstance(fingerprint, str)
            and fingerprint in document["profiles"]
            and document["profiles"][fingerprint].get("instance_uuid") == instance
        }
        document["presentation_schema_version"] = SCHEMA_VERSION
        return document

    def _empty_profile(self):
        return {
            "profile_fingerprint": self.fingerprint,
            "settings_fingerprint": self.base_fingerprint,
            "settings_key": self.profile_settings_key,
            "instance_uuid": self.instance_uuid,
            "bucket_key": self.bucket_key,
            "records": [],
            "related_observations": [],
            "seen_ids": [],
            "current_selection": None,
            "pending_selection": None,
            "last_applied_origin_commit_id": None,
            "last_applied_request_id": None,
            "last_committed_at": None,
            "last_provider_attempt_at": None,
            "last_provider_status": None,
            "displayed_context": None,
            "refill_cursor": 0,
            "last_used_at": _utc_now(),
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
        records = [item for item in profile.get("records") or [] if self._valid_record(item)]
        profile["records"] = records[-MAX_RECORDS_PER_PROFILE:]
        profile["related_observations"] = self._normalize_related_observations(
            profile.get("related_observations") or [],
        )
        profile["seen_ids"] = [str(value) for value in profile.get("seen_ids") or [] if value][-MAX_SEEN_IDS:]
        valid = {item["record_key"] for item in records}
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if selection is not None and not self._valid_selection(selection, valid):
                raise RuntimeError("Species protected selection metadata is invalid")
        return profile

    def _normalize_related_observations(self, observations):
        normalized = []
        identities = set()
        for observation in observations or []:
            try:
                candidate = self.normalize_observation(observation)
            except RuntimeError:
                continue
            identity = _observation_id(candidate)
            if not identity or identity in identities:
                continue
            identities.add(identity)
            normalized.append(candidate)
            if len(normalized) >= MAX_RELATED_OBSERVATIONS:
                break
        return normalized

    def _valid_record(self, record):
        if not isinstance(record, dict) or not _valid_hash(record.get("record_key")):
            return False
        if not isinstance(record.get("observation"), dict) or _parse_datetime(record.get("fetched_at")) is None:
            return False
        try:
            normalized = self.normalize_observation(record["observation"])
        except RuntimeError:
            return False
        expected = sha256(f"{_observation_id(normalized)}\0{record.get('bucket_key')}".encode("utf-8")).hexdigest()
        return (
            expected == record["record_key"]
            and _valid_hash(record.get("photo_key"))
            and (record.get("map_key") is None or _valid_hash(record.get("map_key")))
        )

    @staticmethod
    def _valid_selection(selection, valid):
        if not isinstance(selection, dict) or selection.get("record_key") not in valid:
            return False
        request = selection.get("request_id")
        return request is None or _valid_request_id(request)

    def _make_profile_room(self, document, required):
        while len(document["profiles"]) + required > MAX_PROFILES:
            protected = set(document["instance_profiles"].values()) | {self.fingerprint}
            protected.update(key for key, profile in document["profiles"].items() if isinstance(profile.get("pending_selection"), dict))
            choices = [(str(profile.get("last_used_at") or ""), key) for key, profile in document["profiles"].items() if key not in protected]
            if not choices:
                raise RuntimeError("Species profile capacity is fully active")
            _used, victim = min(choices)
            document["profiles"].pop(victim, None)
            for instance, fingerprint in list(document["instance_profiles"].items()):
                if fingerprint == victim:
                    document["instance_profiles"].pop(instance, None)


def read_bounded_json_object(path):
    return _secure_read_json(path)


def atomic_write_bounded_json(path, payload):
    _validate_state_size_only(payload)
    _secure_atomic_write_json(Path(path), payload)


def validate_species_target(approved, source):
    """Bind an SSRF-approved DNS result to one expected provider authority."""

    allowed = _SOURCE_HOSTS.get(str(source or "").strip().lower())
    if not allowed:
        raise RuntimeError("Species provider source is not approved")
    host = str(getattr(approved, "hostname", "") or "").rstrip(".").lower()
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in allowed):
        raise RuntimeError("Species provider authority does not match its source")
    if getattr(approved, "scheme", "") != "https" or int(getattr(approved, "port", 0)) != 443:
        raise RuntimeError("Species provider target must use HTTPS")
    addresses = tuple(getattr(approved, "addresses", ()) or ())
    if not addresses:
        raise RuntimeError("Species provider target has no approved public address")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise RuntimeError("Species provider target has an invalid address") from exc
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            raise RuntimeError("Species provider target has a non-public address")
        if not address.is_global:
            raise RuntimeError("Species provider target has a non-public address")
    return approved


def _validate_state(document):
    if not isinstance(document, dict):
        raise RuntimeError("Species state must be an object")
    profiles = document.get("profiles", {})
    mappings = document.get("instance_profiles", {})
    if not isinstance(profiles, dict) or len(profiles) > MAX_PROFILES:
        raise RuntimeError("Species profile capacity exceeds the limit")
    if not isinstance(mappings, dict) or len(mappings) > MAX_PROFILES:
        raise RuntimeError("Species instance capacity exceeds the limit")
    for profile in profiles.values():
        if not isinstance(profile, dict) or len(profile.get("records", [])) > MAX_RECORDS_PER_PROFILE:
            raise RuntimeError("Species record capacity exceeds the limit")
        if len(profile.get("seen_ids", [])) > MAX_SEEN_IDS:
            raise RuntimeError("Species seen history exceeds the limit")
    _validate_state_size_only(document)


def _validate_state_size_only(document):
    try:
        encoded = (json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Species state could not be encoded safely") from exc
    if len(encoded) > MAX_STATE_BYTES:
        raise RuntimeError("Species state exceeds the size limit")


def _observation_id(observation):
    return str(observation.get("gbif_key") or observation.get("species_key") or observation.get("taxon_key") or observation.get("scientific_name"))


def _normalize_photo_url(value):
    if not value:
        raise RuntimeError("Species photo URL is missing")
    parsed = urlparse(value)
    host = (parsed.hostname or "").rstrip(".").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Species photo URL authority is invalid") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_PHOTO_SUFFIXES)
    ):
        raise RuntimeError("Species photo URL is outside approved authorities")
    return urlunparse(("https", host, parsed.path or "/", "", parsed.query, ""))


def _validate_dimensions(size):
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0 or width > MEDIA_MAX_DIMENSION or height > MEDIA_MAX_DIMENSION or width * height > MEDIA_MAX_PIXELS:
        raise RuntimeError("Species media dimensions exceed the safety limit")


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


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(character in _HEX for character in value)


def _valid_request_id(value):
    return isinstance(value, str) and len(value) == 32 and all(character in _HEX for character in value)


def _text(value, limit):
    return str(value or "").strip()[:limit]


def _integer(value, default, minimum, maximum):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _optional_float(value):
    if value in {None, ""}:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _bool(value, default):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _is_reparse_or_link(path, info):
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(reparse_flag and attributes & reparse_flag)
