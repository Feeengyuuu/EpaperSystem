#!/usr/bin/env python3
"""Serial, privacy-safe live acceptance for every instance in the active playlist.

Run this on the InkyPi host as root.  It intentionally separates provider-backed
DATA_REFRESH from the cache-only display command, then records filesystem,
physical-display-commit, and HTTP image evidence for each exact instance revision.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask
from PIL import Image, UnidentifiedImageError
import requests


EXPECTED_INSTANCE_COUNT = 26
EXPECTED_IMAGE_SIZE = (800, 480)
ORDINARY_TIMEOUT_SECONDS = 240
HEAVY_TIMEOUT_SECONDS = 600
PRESENTATION_TIMEOUT_FLOOR_SECONDS = 420
HTTP_TIMEOUT_SECONDS = 30
STATE_SETTLE_SECONDS = 12
PRESENTATION_START_SETTLE_SECONDS = 30
POLL_INTERVAL_SECONDS = 1.0
DEFAULT_CACHE_ROOT = "/var/cache/inkypi"
DEFAULT_DATA_ROOT = "/var/lib/inkypi/data"
DEFAULT_PLUGIN_ROOT = "/opt/inkypi/current/src/plugins"
HEALTH_RETRY_SECONDS = 120
HEALTH_POLL_INTERVAL_SECONDS = 1.0
HEALTH_EVENT_LIMIT = 128
TRANSIENT_DEGRADED_REASON_CODES = frozenset({"queue_full"})
KNOWN_HEALTH_REASON_CODES = frozenset({
    "cache_lifecycle_disk_hard",
    "cache_lifecycle_disk_soft",
    "config_degraded",
    "config_invalid",
    "development_display_unavailable",
    "disk_hard_limit",
    "disk_low",
    "disk_status_unavailable",
    "display_not_ready",
    "display_unknown",
    "lifecycle_not_running",
    "queue_full",
    "queue_full_stalled",
    "queue_not_accepting",
    "scheduler_not_started",
    "scheduler_stalled",
    "scheduler_starting",
    "startup_degraded",
})
HEAVY_PLUGIN_IDS = frozenset({
    "apod",
    "backtothedate",
    "daily_art",
    "gcd_comic_covers",
    "magazine_covers",
    "newspaper",
    "pixiv_r18_ranking",
    "species_radar",
    "sports_dashboard",
    "steam_daily_art",
    "telegram_digest",
})
ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
SUCCESS_JOB_STATUS = "completed"
SAFE_RESPONSE_HEADERS = (
    "ETag",
    "Last-Modified",
    "Content-Type",
    "Content-Length",
    "Cache-Control",
)
SAFE_JOB_FIELDS = (
    "status",
    "error_code",
    "submitted_at",
    "started_at",
    "completed_at",
    "cancel_requested_at",
)
_SAFE_FILE_TOKEN = re.compile(r"[^a-zA-Z0-9_.-]+")
_MISSING = object()


class AuditFailure(RuntimeError):
    """Base error carrying only a stable code and allowlisted safe details."""

    def __init__(self, code: str, *, safe_details: dict | None = None):
        self.code = str(code)
        self.safe_details = dict(safe_details or {})
        super().__init__(self.code)


class AuditAbort(AuditFailure):
    """Global invariant failure: no additional instance may be submitted."""


class EvidenceFailure(AuditFailure):
    """One instance failed acceptance; the next instance may still be tested."""


@dataclass(frozen=True)
class PreparedCycleIntervalFreeze:
    document: dict
    original_interval_present: bool
    original_interval_value: object
    interval_seconds: int


@dataclass(frozen=True)
class BankProviderEvidenceSpec:
    root_kind: str
    leaf: tuple[str, ...]
    state_filename: str
    attempt_field: str
    override_env: str | None = None
    cache_override_env: str | None = None
    cache_leaf: tuple[str, ...] = ()


BANK_PROVIDER_EVIDENCE_SPECS = {
    "backtothedate": BankProviderEvidenceSpec(
        root_kind="data",
        leaf=(),
        state_filename=".backtothedate_state.json",
        attempt_field="last_provider_attempt_at",
    ),
    "daily_art": BankProviderEvidenceSpec(
        root_kind="cache",
        leaf=(".daily_art_cache",),
        state_filename="presentation-state.json",
        attempt_field="last_provider_attempt_at",
        override_env="INKYPI_DAILY_ART_CACHE",
    ),
    "gcd_comic_covers": BankProviderEvidenceSpec(
        root_kind="cache",
        leaf=(".gcd_comic_covers_cache",),
        state_filename="state.json",
        attempt_field="last_provider_attempt_at",
        override_env="INKYPI_GCD_COMIC_COVERS_CACHE",
    ),
    "magazine_covers": BankProviderEvidenceSpec(
        root_kind="cache",
        leaf=(".magazine_covers_cache",),
        state_filename="presentation-state.json",
        attempt_field="library_last_attempt_at",
        override_env="INKYPI_MAGAZINE_COVERS_CACHE",
    ),
    "newspaper": BankProviderEvidenceSpec(
        root_kind="data",
        leaf=(),
        state_filename=".newspaper_presentation_state.json",
        attempt_field="last_provider_attempt_at",
    ),
    "pixiv_r18_ranking": BankProviderEvidenceSpec(
        root_kind="data",
        leaf=("presentation-bank",),
        state_filename="presentation-state.json",
        attempt_field="last_provider_attempt_at",
        override_env="INKYPI_PIXIV_R18_DATA",
        cache_override_env="INKYPI_PIXIV_R18_CACHE",
        cache_leaf=(".pixiv_r18_ranking_cache",),
    ),
    "species_radar": BankProviderEvidenceSpec(
        root_kind="cache",
        leaf=("cache",),
        state_filename="presentation-state.json",
        attempt_field="last_provider_attempt_at",
        override_env="INKYPI_SPECIES_RADAR_CACHE",
    ),
}


@dataclass(frozen=True)
class InstancePlan:
    index: int
    playlist_name: str
    plugin_id: str
    instance_name: str
    instance_uuid: str
    structural_generation: int
    settings_revision: int
    expects_presentation_refresh: bool

    @property
    def uuid_hash(self) -> str:
        return hash_identifier(self.instance_uuid)

    def safe_identity(self) -> dict:
        return {
            "index": self.index,
            "playlist": self.playlist_name,
            "plugin_id": self.plugin_id,
            "uuid_hash": self.uuid_hash,
            "structural_generation": self.structural_generation,
            "settings_revision": self.settings_revision,
            "presentation_expected": self.expects_presentation_refresh,
        }

    def request_payload(self) -> dict:
        return {
            "playlist_name": self.playlist_name,
            "plugin_id": self.plugin_id,
            "plugin_instance": self.instance_name,
        }


@dataclass(frozen=True)
class ImageEvidence:
    width: int
    height: int
    pixel_hash: str
    png_sha256: str
    byte_count: int

    def to_safe_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "pixel_hash": self.pixel_hash,
            "png_sha256": self.png_sha256,
            "byte_count": self.byte_count,
        }


def hash_identifier(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _non_empty_text(value, *, code, abort=True) -> str:
    if not isinstance(value, str) or not value.strip():
        error_type = AuditAbort if abort else EvidenceFailure
        raise error_type(code)
    return value.strip()


def _positive_revision(value, *, code) -> int:
    if type(value) is not int or value <= 0:
        raise AuditAbort(code)
    return value


def _playlist_is_active(playlist: dict, current_time: str) -> bool:
    start = _non_empty_text(playlist.get("start_time"), code="config_playlist_time")
    end = _non_empty_text(playlist.get("end_time"), code="config_playlist_time")
    _time_minutes(start, allow_24=False)
    _time_minutes(end, allow_24=True)
    if start <= end:
        return start <= current_time < end
    return current_time >= start or current_time < end


def _time_minutes(value: str, *, allow_24: bool) -> int:
    if allow_24 and value == "24:00":
        return 24 * 60
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError) as error:
        raise AuditAbort("config_playlist_time") from error
    return parsed.hour * 60 + parsed.minute


def _playlist_duration(playlist: dict) -> int:
    start = _time_minutes(playlist.get("start_time"), allow_24=False)
    end = _time_minutes(playlist.get("end_time"), allow_24=True)
    if end < start:
        end += 24 * 60
    return end - start


def _current_config_time(config: dict, now: datetime | None) -> datetime:
    zone_name = config.get("timezone") or "UTC"
    try:
        zone = ZoneInfo(str(zone_name))
    except (TypeError, ZoneInfoNotFoundError) as error:
        raise AuditAbort("config_timezone_invalid") from error
    current = now or datetime.now(timezone.utc)
    if not isinstance(current, datetime):
        raise AuditAbort("config_clock_invalid")
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(zone)


def _strict_config_bool(value, *, code) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise AuditAbort(code)


def _presentation_expectation(raw: dict, plugin_id: str, plugin_root) -> bool:
    settings = raw.get("plugin_settings") or {}
    if not isinstance(settings, dict):
        raise AuditAbort("config_plugin_settings")
    explicit = None
    if "refreshOnDisplay" in settings:
        explicit = _strict_config_bool(
            settings["refreshOnDisplay"],
            code="config_refresh_on_display",
        )
        if explicit is False:
            return False

    manifest_path = Path(plugin_root) / plugin_id / "plugin-info.json"
    manifest = _read_json(
        manifest_path,
        code="plugin_manifest_read_failed",
        abort=True,
    )
    if manifest.get("id") != plugin_id:
        raise AuditAbort("plugin_manifest_id_mismatch")
    if explicit is None:
        if "refresh_on_display" in manifest:
            expected = _strict_config_bool(
                manifest["refresh_on_display"],
                code="plugin_manifest_refresh_on_display",
            )
        else:
            expected = bool(
                plugin_id == "newspaper"
                and str(settings.get("mediaRotationMode") or "rotate").lower()
                != "single"
            )
    else:
        expected = explicit
    if not expected:
        return False
    capabilities = manifest.get("capabilities")
    if (
        not isinstance(capabilities, dict)
        or capabilities.get("supports_presentation_refresh") is not True
    ):
        raise AuditAbort("plugin_manifest_presentation_capability")
    return True


def build_acceptance_plan(
    config: dict,
    *,
    now: datetime | None = None,
    plugin_root=DEFAULT_PLUGIN_ROOT,
) -> tuple[InstancePlan, ...]:
    """Resolve the same current priority winner as PlaylistManager, then gate it."""

    if not isinstance(config, dict):
        raise AuditAbort("config_not_object")
    playlist_config = config.get("playlist_config")
    if not isinstance(playlist_config, dict):
        raise AuditAbort("config_playlist_structure")
    playlists = playlist_config.get("playlists")
    if not isinstance(playlists, list) or not playlists:
        raise AuditAbort("config_playlist_structure")
    if any(not isinstance(playlist, dict) for playlist in playlists):
        raise AuditAbort("config_playlist_structure")

    current = _current_config_time(config, now)
    current_time = current.strftime("%H:%M")
    active = [playlist for playlist in playlists if _playlist_is_active(playlist, current_time)]
    if not active:
        raise AuditAbort("config_no_active_playlist")
    playlist = min(active, key=_playlist_duration)
    playlist_name = _non_empty_text(
        playlist.get("name"),
        code="config_playlist_name",
    )
    plugins = playlist.get("plugins")
    if not isinstance(plugins, list):
        raise AuditAbort("config_plugins_structure")
    if len(plugins) != EXPECTED_INSTANCE_COUNT:
        raise AuditAbort(
            "config_instance_count",
            safe_details={"expected": EXPECTED_INSTANCE_COUNT, "actual": len(plugins)},
        )

    plan = []
    seen_uuids = set()
    for index, raw in enumerate(plugins, start=1):
        if not isinstance(raw, dict):
            raise AuditAbort("config_instance_structure", safe_details={"index": index})
        instance_uuid = _non_empty_text(
            raw.get("instance_uuid"),
            code="config_instance_uuid",
        )
        if instance_uuid in seen_uuids:
            raise AuditAbort("config_duplicate_uuid", safe_details={"index": index})
        seen_uuids.add(instance_uuid)
        plugin_id = _non_empty_text(raw.get("plugin_id"), code="config_plugin_id")
        plan.append(InstancePlan(
            index=index,
            playlist_name=playlist_name,
            plugin_id=plugin_id,
            instance_name=_non_empty_text(raw.get("name"), code="config_instance_name"),
            instance_uuid=instance_uuid,
            structural_generation=_positive_revision(
                raw.get("structural_generation"),
                code="config_structural_generation",
            ),
            settings_revision=_positive_revision(
                raw.get("settings_revision"),
                code="config_settings_revision",
            ),
            expects_presentation_refresh=_presentation_expectation(
                raw,
                plugin_id,
                plugin_root,
            ),
        ))
    return tuple(plan)


def plan_fingerprint(plan: tuple[InstancePlan, ...]) -> str:
    payload = [
        (
            item.playlist_name,
            item.plugin_id,
            item.instance_name,
            item.instance_uuid,
            item.structural_generation,
            item.settings_revision,
            item.expects_presentation_refresh,
        )
        for item in plan
    ]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def inspect_png(payload: bytes, *, expected_size=EXPECTED_IMAGE_SIZE) -> ImageEvidence:
    if not isinstance(payload, bytes) or not payload:
        raise EvidenceFailure("image_payload_missing")
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "PNG":
                raise EvidenceFailure("image_format_mismatch")
            image.load()
            width, height = image.size
            rgb_bytes = image.convert("RGB").tobytes()
    except EvidenceFailure:
        raise
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise EvidenceFailure("image_decode_failed") from error
    if (width, height) != tuple(expected_size):
        raise EvidenceFailure(
            "image_dimensions_mismatch",
            safe_details={
                "expected": list(expected_size),
                "actual": [width, height],
            },
        )
    return ImageEvidence(
        width=width,
        height=height,
        pixel_hash=hashlib.sha256(rgb_bytes).hexdigest(),
        png_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
    )


def build_admin_session(
    flask_secret: str,
    *,
    session_factory=requests.Session,
):
    secret = _non_empty_text(flask_secret, code="flask_secret_invalid")
    app = Flask("inkypi_live_acceptance")
    app.secret_key = secret
    csrf_token = secrets.token_urlsafe(32)
    serializer = app.session_interface.get_signing_serializer(app)
    if serializer is None:
        raise AuditAbort("flask_session_serializer_unavailable")
    cookie_value = serializer.dumps({
        "admin_identity": "admin",
        "csrf_token": csrf_token,
        "_permanent": True,
    })
    session = session_factory()
    session.cookies.set(app.config["SESSION_COOKIE_NAME"], cookie_value)
    session.headers.update({
        "X-CSRF-Token": csrf_token,
        "Accept": "application/json",
        "User-Agent": "inkypi-live-acceptance/1",
    })
    if hasattr(session, "trust_env"):
        session.trust_env = False
    return session, csrf_token


def _url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _response_json(response, *, code, abort=False) -> dict:
    error_type = AuditAbort if abort else EvidenceFailure
    if response.status_code < 200 or response.status_code >= 300:
        raise error_type(code, safe_details={"http_status": response.status_code})
    try:
        payload = response.json()
    except (TypeError, ValueError) as error:
        raise error_type(f"{code}_json") from error
    if not isinstance(payload, dict):
        raise error_type(f"{code}_shape")
    return payload


def _safe_health_reason_codes(payload) -> list[str]:
    values = payload.get("error_codes") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return []
    safe = {
        value
        for value in values
        if isinstance(value, str) and value in KNOWN_HEALTH_REASON_CODES
    }
    if len(safe) != len(values):
        safe.add("unknown")
    return sorted(safe)


def safe_job_record(job: dict | None) -> dict | None:
    if not isinstance(job, dict):
        return None
    safe = {}
    if job.get("id") is not None:
        safe["job_id_hash"] = hash_identifier(job["id"])
    elif isinstance(job.get("job_id_hash"), str) and re.fullmatch(
        r"[0-9a-f]{16}",
        job["job_id_hash"],
    ):
        safe["job_id_hash"] = job["job_id_hash"]
    for field in SAFE_JOB_FIELDS:
        value = job.get(field)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value is not None:
                safe[field] = value
    return safe


def _with_job_context(
    error: EvidenceFailure,
    *,
    data_job: dict | None = None,
    display_job: dict | None = None,
) -> EvidenceFailure:
    details = dict(error.safe_details)
    if data_job is not None:
        details["data_job"] = safe_job_record(data_job)
    if display_job is not None:
        details["display_job"] = safe_job_record(display_job)
    return EvidenceFailure(error.code, safe_details=details)


def submit_job(session, base_url: str, endpoint: str, instance: InstancePlan) -> dict:
    try:
        response = session.post(
            _url(base_url, endpoint),
            json=instance.request_payload(),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise AuditAbort("job_submit_transport") from error
    payload = _response_json(response, code="job_submit_rejected")
    job = payload.get("job")
    job_id = payload.get("job_id")
    if not isinstance(job, dict) or not isinstance(job_id, str) or not job_id:
        raise AuditAbort("job_submit_shape")
    if job.get("id") not in {None, job_id}:
        raise AuditAbort("job_submit_identity")
    job = dict(job)
    job["id"] = job_id
    return job


def poll_job(
    session,
    base_url: str,
    job_id: str,
    *,
    timeout_seconds: float,
    monotonic=time.monotonic,
    sleep=time.sleep,
    poll_interval=POLL_INTERVAL_SECONDS,
) -> dict:
    """Poll one job to a terminal state before any next submission is allowed."""

    deadline = monotonic() + float(timeout_seconds)
    last_status = "unknown"
    while True:
        try:
            response = session.get(
                _url(base_url, f"/refresh_job/{job_id}"),
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            if monotonic() >= deadline:
                raise AuditAbort(
                    "outstanding_job_unknown",
                    safe_details={"job_id_hash": hash_identifier(job_id)},
                ) from error
            sleep(min(float(poll_interval), max(0.0, deadline - monotonic())))
            continue
        payload = _response_json(response, code="job_poll_unavailable", abort=True)
        job = payload.get("job")
        if not isinstance(job, dict):
            raise AuditAbort("job_poll_shape")
        returned_id = job.get("id")
        if returned_id not in {None, job_id}:
            raise AuditAbort("job_poll_identity")
        status = job.get("status")
        if not isinstance(status, str) or not status:
            raise AuditAbort("job_poll_status")
        last_status = status
        if status not in ACTIVE_JOB_STATUSES:
            return job
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise AuditAbort(
                "outstanding_job_timeout",
                safe_details={
                    "job_id_hash": hash_identifier(job_id),
                    "last_status": last_status,
                },
            )
        sleep(min(float(poll_interval), remaining))


def _parse_iso(value, *, code, failure_type=EvidenceFailure) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise failure_type(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise failure_type(code) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _instance_runtime(runtime_state: dict, instance: InstancePlan) -> dict:
    if not isinstance(runtime_state, dict):
        raise EvidenceFailure("runtime_state_shape")
    instances = runtime_state.get("instances")
    if not isinstance(instances, dict):
        raise EvidenceFailure("runtime_instances_shape")
    state = instances.get(instance.instance_uuid)
    if not isinstance(state, dict):
        raise EvidenceFailure("runtime_instance_missing")
    return state


def _plugin_storage_directory(
    *,
    root: Path,
    plugin_id: str,
    leaf: tuple[str, ...],
    override_value,
) -> Path:
    plugin_root = root / "plugins" / plugin_id
    if isinstance(override_value, str) and override_value.strip():
        override = Path(override_value.strip()).expanduser()
        return override if override.is_absolute() else plugin_root / override
    return plugin_root.joinpath(*leaf)


def resolve_bank_provider_state_path(
    instance: InstancePlan,
    *,
    cache_root,
    data_root,
    environ=None,
) -> Path | None:
    spec = BANK_PROVIDER_EVIDENCE_SPECS.get(instance.plugin_id)
    if spec is None:
        return None
    environment = os.environ if environ is None else environ
    cache_root = Path(cache_root)
    data_root = Path(data_root)

    if spec.cache_override_env:
        cache_override = environment.get(spec.cache_override_env)
        if isinstance(cache_override, str) and cache_override.strip():
            directory = _plugin_storage_directory(
                root=cache_root,
                plugin_id=instance.plugin_id,
                leaf=spec.cache_leaf,
                override_value=cache_override,
            )
            return directory / spec.state_filename

    root = cache_root if spec.root_kind == "cache" else data_root
    override_value = (
        environment.get(spec.override_env) if spec.override_env is not None else None
    )
    directory = _plugin_storage_directory(
        root=root,
        plugin_id=instance.plugin_id,
        leaf=spec.leaf,
        override_value=override_value,
    )
    return directory / spec.state_filename


def validate_bank_provider_evidence(
    document: dict,
    instance: InstancePlan,
    *,
    started_at: datetime,
) -> dict:
    spec = BANK_PROVIDER_EVIDENCE_SPECS.get(instance.plugin_id)
    if spec is None:
        raise EvidenceFailure("bank_provider_contract_missing")
    if not isinstance(document, dict):
        raise EvidenceFailure("bank_provider_state_shape")
    instance_profiles = document.get("instance_profiles")
    profiles = document.get("profiles")
    if not isinstance(instance_profiles, dict) or not isinstance(profiles, dict):
        raise EvidenceFailure("bank_provider_state_shape")
    profile_key = instance_profiles.get(instance.instance_uuid)
    if not isinstance(profile_key, str) or not profile_key:
        raise EvidenceFailure("bank_provider_instance_profile_missing")
    profile = profiles.get(profile_key)
    if not isinstance(profile, dict):
        raise EvidenceFailure("bank_provider_profile_missing")

    attempted_at = profile.get(spec.attempt_field)
    parsed_attempt = _parse_iso(
        attempted_at,
        code="bank_provider_attempt_timestamp",
    )
    started = started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    started = started.astimezone(timezone.utc)
    if parsed_attempt < started:
        raise EvidenceFailure("bank_provider_attempt_stale")

    status = profile.get("last_provider_status")
    if status == "empty":
        raise EvidenceFailure("bank_provider_empty")
    if status == "error":
        raise EvidenceFailure("bank_provider_error")
    if status != "success":
        raise EvidenceFailure("bank_provider_status_invalid")
    return {
        "attempted_at": attempted_at,
        "status": "success",
    }


def validate_data_evidence(
    runtime_state: dict,
    instance: InstancePlan,
    *,
    started_at: datetime,
) -> dict:
    state = _instance_runtime(runtime_state, instance)
    lanes = state.get("lanes")
    data = lanes.get("data") if isinstance(lanes, dict) else None
    cache = state.get("last_good_cache")
    if not isinstance(data, dict) or not isinstance(cache, dict):
        raise EvidenceFailure("data_evidence_shape")
    started = started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    started = started.astimezone(timezone.utc)
    timestamps = {
        "last_attempt_at": data.get("last_attempt_at"),
        "last_success_at": data.get("last_success_at"),
        "promoted_at": cache.get("promoted_at"),
    }
    parsed = {
        key: _parse_iso(value, code="data_evidence_timestamp")
        for key, value in timestamps.items()
    }
    if any(value < started for value in parsed.values()):
        raise EvidenceFailure("data_evidence_stale")
    if timestamps["last_success_at"] != timestamps["promoted_at"]:
        raise EvidenceFailure("data_cache_success_mismatch")
    if (
        cache.get("structural_generation") != instance.structural_generation
        or cache.get("settings_revision") != instance.settings_revision
    ):
        raise EvidenceFailure("data_cache_revision_mismatch")
    return {
        "last_attempt_at": timestamps["last_attempt_at"],
        "last_success_at": timestamps["last_success_at"],
        "last_good_cache": {
            "theme_mode": cache.get("theme_mode"),
            "structural_generation": cache.get("structural_generation"),
            "settings_revision": cache.get("settings_revision"),
            "promoted_at": timestamps["promoted_at"],
        },
    }


def _normalized_etag(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:]
    return normalized.strip('"')


def _equivalent_hardware_candidate(
    baseline_manifest: dict | None,
    manifest: dict,
    instance: InstancePlan,
) -> bool:
    if not isinstance(baseline_manifest, dict):
        return False
    baseline_target = baseline_manifest.get("logical_target")
    target = manifest.get("logical_target")
    return bool(
        isinstance(baseline_target, dict)
        and isinstance(target, dict)
        and baseline_target.get("instance_uuid") == instance.instance_uuid
        and target.get("instance_uuid") == instance.instance_uuid
        and baseline_manifest.get("pixel_hash") == manifest.get("pixel_hash")
        and baseline_manifest.get("instance_revision")
        == manifest.get("instance_revision")
    )


def validate_display_evidence(
    runtime_state: dict,
    manifest: dict,
    instance: InstancePlan,
    image: ImageEvidence,
    headers,
    *,
    baseline_manifest: dict | None = None,
    display_started_at: datetime | None = None,
) -> dict:
    if not isinstance(runtime_state, dict) or not isinstance(manifest, dict):
        raise EvidenceFailure("display_evidence_shape")
    display = runtime_state.get("display")
    target = manifest.get("logical_target")
    revision = manifest.get("instance_revision")
    commit_id = manifest.get("commit_id")
    if not isinstance(display, dict) or not isinstance(target, dict):
        raise EvidenceFailure("display_evidence_shape")
    if (
        display.get("state") != "committed"
        or display.get("instance_uuid") != instance.instance_uuid
        or display.get("commit_id") != commit_id
    ):
        raise EvidenceFailure("display_runtime_target_mismatch")
    if (
        target.get("kind") != "playlist"
        or target.get("playlist") != instance.playlist_name
        or target.get("plugin_id") != instance.plugin_id
        or target.get("plugin_instance") != instance.instance_name
        or target.get("instance_uuid") != instance.instance_uuid
    ):
        raise EvidenceFailure("display_manifest_target_mismatch")
    expected_revision = [instance.structural_generation, instance.settings_revision]
    if revision != expected_revision:
        raise EvidenceFailure("display_revision_mismatch")
    if not isinstance(commit_id, str) or not commit_id:
        raise EvidenceFailure("display_commit_missing")
    if display_started_at is not None:
        baseline_commit_id = (
            baseline_manifest.get("commit_id")
            if isinstance(baseline_manifest, dict)
            else None
        )
        if baseline_commit_id == commit_id:
            raise EvidenceFailure("display_commit_stale")
        if display_started_at.tzinfo is None:
            display_started_at = display_started_at.replace(tzinfo=timezone.utc)
        display_started_at = display_started_at.astimezone(timezone.utc)
        committed_at = _parse_iso(
            manifest.get("committed_at"),
            code="display_commit_time_invalid",
        )
        if committed_at < display_started_at:
            raise EvidenceFailure("display_commit_precedes_job")
    if manifest.get("pixel_hash") != image.pixel_hash:
        raise EvidenceFailure("display_pixel_hash_mismatch")
    if _normalized_etag(headers.get("ETag")) != commit_id:
        raise EvidenceFailure("display_etag_mismatch")
    last_modified = headers.get("Last-Modified")
    try:
        header_time = parsedate_to_datetime(last_modified).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as error:
        raise EvidenceFailure("display_last_modified_invalid") from error
    committed = _parse_iso(manifest.get("committed_at"), code="display_commit_time_invalid")
    if abs((header_time - committed).total_seconds()) >= 1.1:
        raise EvidenceFailure("display_last_modified_mismatch")
    content_type = headers.get("Content-Type")
    if not isinstance(content_type, str) or not content_type.lower().startswith("image/png"):
        raise EvidenceFailure("display_content_type_mismatch")
    if manifest.get("hardware_written") is not True:
        raise EvidenceFailure(
            "hardware_not_written",
            safe_details={
                "equivalent_candidate": _equivalent_hardware_candidate(
                    baseline_manifest,
                    manifest,
                    instance,
                ),
            },
        )
    return {
        "commit_id_hash": hash_identifier(commit_id),
        "committed_at": manifest.get("committed_at"),
        "pixel_hash": image.pixel_hash,
        "instance_revision": expected_revision,
        "hardware_written": True,
        "headers": safe_headers(headers),
        "image": image.to_safe_dict(),
    }


def safe_headers(headers) -> dict:
    safe = {}
    for key in SAFE_RESPONSE_HEADERS:
        value = headers.get(key)
        if isinstance(value, str) and value:
            safe[key] = value
    return safe


def validate_presentation_completion(
    runtime_state: dict,
    manifest: dict,
    instance: InstancePlan,
    *,
    request_id: str,
) -> dict:
    state = _instance_runtime(runtime_state, instance)
    receipt = state.get("presentation_receipt")
    if state.get("presentation_request") is not None or not isinstance(receipt, dict):
        raise EvidenceFailure("presentation_receipt_not_ready")
    if receipt.get("request_id") != request_id:
        raise EvidenceFailure("presentation_receipt_not_ready")
    if (
        receipt.get("structural_generation") != instance.structural_generation
        or receipt.get("settings_revision") != instance.settings_revision
    ):
        raise EvidenceFailure("presentation_receipt_revision_mismatch")
    display_commit_id = receipt.get("display_commit_id")
    if (
        not isinstance(display_commit_id, str)
        or manifest.get("commit_id") != display_commit_id
        or runtime_state.get("display", {}).get("commit_id") != display_commit_id
    ):
        raise EvidenceFailure("presentation_final_commit_mismatch")
    cache = state.get("last_good_cache")
    if (
        not isinstance(cache, dict)
        or cache.get("structural_generation") != instance.structural_generation
        or cache.get("settings_revision") != instance.settings_revision
        or cache.get("promoted_at") != receipt.get("committed_at")
    ):
        raise EvidenceFailure("presentation_final_cache_mismatch")
    target = manifest.get("logical_target")
    if (
        not isinstance(target, dict)
        or target.get("instance_uuid") != instance.instance_uuid
        or manifest.get("instance_revision")
        != [instance.structural_generation, instance.settings_revision]
    ):
        raise EvidenceFailure("presentation_final_target_mismatch")
    return {
        "request_id_hash": hash_identifier(request_id),
        "display_commit_id_hash": hash_identifier(display_commit_id),
        "committed_at": receipt.get("committed_at"),
        "structural_generation": receipt.get("structural_generation"),
        "settings_revision": receipt.get("settings_revision"),
        "theme_mode": receipt.get("theme_mode"),
    }


def _validated_presentation_request(request, instance: InstancePlan) -> dict:
    if not isinstance(request, dict):
        raise EvidenceFailure("presentation_request_shape")
    if (
        request.get("structural_generation") != instance.structural_generation
        or request.get("settings_revision") != instance.settings_revision
    ):
        raise EvidenceFailure("presentation_request_revision_mismatch")
    for field in ("request_id", "requested_at", "origin_display_commit_id"):
        if not isinstance(request.get(field), str) or not request[field]:
            raise EvidenceFailure("presentation_request_shape")
    _parse_iso(request["requested_at"], code="presentation_request_time_invalid")
    return request


def validate_display_created_presentation_request(
    request: dict,
    instance: InstancePlan,
    manifest: dict,
    *,
    display_started_at: datetime,
) -> None:
    request = _validated_presentation_request(request, instance)
    commit_id = manifest.get("commit_id")
    committed_at = manifest.get("committed_at")
    if (
        not isinstance(commit_id, str)
        or not commit_id
        or request.get("origin_display_commit_id") != commit_id
    ):
        raise EvidenceFailure("presentation_request_origin_mismatch")
    if not isinstance(committed_at, str) or request.get("requested_at") != committed_at:
        raise EvidenceFailure("presentation_request_time_mismatch")
    requested_at = _parse_iso(
        request.get("requested_at"),
        code="presentation_request_time_invalid",
    )
    if display_started_at.tzinfo is None:
        display_started_at = display_started_at.replace(tzinfo=timezone.utc)
    if requested_at < display_started_at.astimezone(timezone.utc):
        raise EvidenceFailure("presentation_request_predates_display")


def validate_presentation_outcome(
    runtime_state: dict,
    manifest: dict,
    instance: InstancePlan,
    *,
    request: dict,
    expected_display_commit_id: str,
) -> dict:
    if not isinstance(expected_display_commit_id, str) or not expected_display_commit_id:
        raise EvidenceFailure("presentation_display_commit_invalid")
    expected = _validated_presentation_request(request, instance)
    state = _instance_runtime(runtime_state, instance)
    current = state.get("presentation_request")
    if current is not None:
        current = _validated_presentation_request(current, instance)
        immutable_fields = (
            "request_id",
            "requested_at",
            "origin_display_commit_id",
            "origin_theme_mode",
            "structural_generation",
            "settings_revision",
        )
        if any(current.get(field) != expected.get(field) for field in immutable_fields):
            raise EvidenceFailure("presentation_request_replaced")
        raise EvidenceFailure("presentation_receipt_not_ready")

    receipt = state.get("presentation_receipt")
    if isinstance(receipt, dict) and receipt.get("request_id") == expected["request_id"]:
        evidence = validate_presentation_completion(
            runtime_state,
            manifest,
            instance,
            request_id=expected["request_id"],
        )
        return {"completion": "changed", **evidence}

    lanes = state.get("lanes")
    presentation = lanes.get("presentation") if isinstance(lanes, dict) else None
    if (
        isinstance(presentation, dict)
        and presentation.get("last_success_at") == expected["requested_at"]
        and presentation.get("next_retry_at") is None
    ):
        display = runtime_state.get("display")
        target = manifest.get("logical_target")
        if (
            manifest.get("commit_id") != expected_display_commit_id
            or not isinstance(display, dict)
            or display.get("commit_id") != expected_display_commit_id
            or not isinstance(target, dict)
            or target.get("instance_uuid") != instance.instance_uuid
            or manifest.get("instance_revision")
            != [instance.structural_generation, instance.settings_revision]
        ):
            raise EvidenceFailure("presentation_no_change_display_drift")
        return {
            "completion": "no_change",
            "request_id_hash": hash_identifier(expected["request_id"]),
            "completed_at": expected["requested_at"],
            "structural_generation": expected["structural_generation"],
            "settings_revision": expected["settings_revision"],
        }
    raise EvidenceFailure("presentation_request_cleared_unproven")


def validate_atomic_presentation_no_change(
    runtime_state: dict,
    baseline_runtime_state: dict,
    manifest: dict,
    instance: InstancePlan,
) -> dict:
    state = _instance_runtime(runtime_state, instance)
    baseline_state = _instance_runtime(baseline_runtime_state, instance)
    if state.get("presentation_request") is not None:
        raise EvidenceFailure("presentation_atomic_request_present")
    if state.get("presentation_receipt") != baseline_state.get("presentation_receipt"):
        raise EvidenceFailure("presentation_atomic_changed_unbindable")
    committed_at = manifest.get("committed_at")
    commit_id = manifest.get("commit_id")
    if not isinstance(committed_at, str) or not committed_at:
        raise EvidenceFailure("presentation_atomic_time_invalid")
    _parse_iso(committed_at, code="presentation_atomic_time_invalid")
    if not isinstance(commit_id, str) or not commit_id:
        raise EvidenceFailure("presentation_display_commit_invalid")
    lanes = state.get("lanes")
    presentation = lanes.get("presentation") if isinstance(lanes, dict) else None
    baseline_lanes = baseline_state.get("lanes")
    baseline_presentation = (
        baseline_lanes.get("presentation")
        if isinstance(baseline_lanes, dict)
        else None
    )
    if (
        not isinstance(presentation, dict)
        or presentation.get("last_success_at") != committed_at
        or presentation.get("next_retry_at") is not None
        or (
            isinstance(baseline_presentation, dict)
            and baseline_presentation.get("last_success_at") == committed_at
        )
    ):
        raise EvidenceFailure("presentation_atomic_no_change_unproven")
    display = runtime_state.get("display")
    target = manifest.get("logical_target")
    if (
        not isinstance(display, dict)
        or display.get("commit_id") != commit_id
        or not isinstance(target, dict)
        or target.get("instance_uuid") != instance.instance_uuid
        or manifest.get("instance_revision")
        != [instance.structural_generation, instance.settings_revision]
    ):
        raise EvidenceFailure("presentation_no_change_display_drift")
    return {
        "completion": "no_change_atomic",
        "success_marker_at": committed_at,
        "structural_generation": instance.structural_generation,
        "settings_revision": instance.settings_revision,
    }


def safe_instance_result(
    instance: InstancePlan,
    *,
    status: str,
    failure_code: str | None = None,
    data_job: dict | None = None,
    display_job: dict | None = None,
    data_evidence: dict | None = None,
    display_evidence: dict | None = None,
    presentation_evidence: dict | None = None,
    artifacts: dict | None = None,
    safe_details: dict | None = None,
) -> dict:
    result = instance.safe_identity()
    result["status"] = status
    if failure_code:
        result["failure_code"] = str(failure_code)
    if data_job is not None:
        result["data_job"] = safe_job_record(data_job)
    if display_job is not None:
        result["display_job"] = safe_job_record(display_job)
    if data_evidence is not None:
        result["data_evidence"] = data_evidence
    if display_evidence is not None:
        result["display_evidence"] = display_evidence
    if presentation_evidence is not None:
        result["presentation_evidence"] = presentation_evidence
    if artifacts:
        result["artifacts"] = dict(artifacts)
    if safe_details:
        result["safe_details"] = dict(safe_details)
    return result


def _secure_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_bytes(payload)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_safe_json(path: Path, payload: dict) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    _secure_write(path, encoded)


def _read_json(path: Path, *, code: str, abort: bool) -> dict:
    error_type = AuditAbort if abort else EvidenceFailure
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise error_type(code) from error
    if not isinstance(payload, dict):
        raise error_type(f"{code}_shape")
    return payload


def prepare_cycle_interval_freeze(
    config: dict,
    *,
    interval_seconds: int,
) -> PreparedCycleIntervalFreeze:
    """Prepare a full config document with only the cycle interval frozen."""

    if not isinstance(config, dict):
        raise AuditAbort("freeze_config_not_object")
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, int)
        or interval_seconds < 1
    ):
        raise AuditAbort("freeze_cycle_interval_invalid")
    original_interval_present = "plugin_cycle_interval_seconds" in config
    original_interval_value = (
        copy.deepcopy(config["plugin_cycle_interval_seconds"])
        if original_interval_present
        else _MISSING
    )
    document = copy.deepcopy(config)
    document["plugin_cycle_interval_seconds"] = interval_seconds
    return PreparedCycleIntervalFreeze(
        document=document,
        original_interval_present=original_interval_present,
        original_interval_value=original_interval_value,
        interval_seconds=interval_seconds,
    )


def restore_cycle_interval(
    current: dict,
    prepared: PreparedCycleIntervalFreeze,
) -> dict:
    """Restore only the pre-test cycle interval onto the latest config."""

    if not isinstance(current, dict):
        raise AuditAbort("freeze_restore_config_not_object")
    document = copy.deepcopy(current)
    if prepared.original_interval_present:
        document["plugin_cycle_interval_seconds"] = copy.deepcopy(
            prepared.original_interval_value,
        )
    else:
        document.pop("plugin_cycle_interval_seconds", None)
    return document


def atomic_write_json(path, document: dict) -> None:
    """Atomically replace JSON while preserving owner and permission bits."""

    target = Path(path)
    try:
        original_stat = target.stat()
    except OSError as error:
        raise AuditAbort("config_stat_failed") from error
    encoded = json.dumps(
        document,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IMODE(original_stat.st_mode),
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), stat.S_IMODE(original_stat.st_mode))
            if hasattr(os, "fchown"):
                try:
                    os.fchown(
                        stream.fileno(),
                        original_stat.st_uid,
                        original_stat.st_gid,
                    )
                except OSError:
                    pass
        os.replace(temporary, target)
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except AuditFailure:
        raise
    except OSError as error:
        raise AuditAbort("config_atomic_write_failed") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class SystemdController:
    def __init__(
        self,
        *,
        service_name="inkypi.service",
        run=subprocess.run,
        timeout_seconds=90,
    ):
        self.service_name = str(service_name)
        self._run = run
        self.timeout_seconds = int(timeout_seconds)

    def _call(self, action: str) -> None:
        try:
            completed = self._run(
                ["systemctl", action, self.service_name],
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AuditAbort(f"service_{action}_failed") from error
        if completed.returncode != 0:
            raise AuditAbort(f"service_{action}_failed")

    def stop(self) -> None:
        self._call("stop")

    def start(self) -> None:
        self._call("start")


class CycleIntervalFreezeAcceptance:
    """Run the acceptance sweep with the plugin cycle interval frozen.

    The service is stopped around each config write so the runtime never sees
    a partially applied document.  If the restore write fails, the service is
    intentionally left stopped: restarting it would keep the frozen interval
    live on the device with nobody watching.
    """

    def __init__(self, *, runner, controller, interval_seconds: int):
        self.runner = runner
        self.controller = controller
        self.interval_seconds = interval_seconds

    def run(self) -> dict:
        config_path = Path(self.runner.config_path)
        original = _read_json(
            config_path,
            code="cycle_freeze_config_read_failed",
            abort=True,
        )
        prepared = prepare_cycle_interval_freeze(
            original,
            interval_seconds=self.interval_seconds,
        )
        self.controller.stop()
        atomic_write_json(config_path, prepared.document)
        self._start_and_wait_ready()
        try:
            summary = self.runner.run()
        finally:
            self._restore(config_path, prepared)
        summary["cycle_interval_freeze_seconds"] = prepared.interval_seconds
        summary["cycle_interval_restored"] = True
        summary["service_ready_restored"] = True
        return summary

    def _start_and_wait_ready(self) -> None:
        self.controller.start()
        self.runner.reset_health_boot_tracking()
        self.runner._ready()

    def _restore(
        self,
        config_path: Path,
        prepared: PreparedCycleIntervalFreeze,
    ) -> None:
        self.controller.stop()
        current = _read_json(
            config_path,
            code="cycle_freeze_restore_read_failed",
            abort=True,
        )
        document = restore_cycle_interval(current, prepared)
        try:
            atomic_write_json(config_path, document)
        except AuditFailure as error:
            write_safe_json(Path(self.runner.output_dir) / "summary.json", {
                "schema_version": 1,
                "status": "aborted",
                "abort_code": "cycle_freeze_restore_config_failed",
                "service_left_stopped": True,
            })
            raise AuditAbort("cycle_freeze_restore_config_failed") from error
        self._start_and_wait_ready()


def timeout_for(instance: InstancePlan) -> int:
    return (
        HEAVY_TIMEOUT_SECONDS
        if instance.plugin_id in HEAVY_PLUGIN_IDS
        else ORDINARY_TIMEOUT_SECONDS
    )


def presentation_timeout_for(instance: InstancePlan) -> int:
    return max(timeout_for(instance), PRESENTATION_TIMEOUT_FLOOR_SECONDS)


class AcceptanceRunner:
    def __init__(
        self,
        *,
        session,
        base_url: str,
        config_path,
        runtime_state_path,
        display_manifest_path,
        output_dir,
        cache_root=DEFAULT_CACHE_ROOT,
        data_root=DEFAULT_DATA_ROOT,
        plugin_root=DEFAULT_PLUGIN_ROOT,
        utcnow=lambda: datetime.now(timezone.utc),
        monotonic=time.monotonic,
        sleep=time.sleep,
    ):
        self.session = session
        self.base_url = str(base_url)
        self.config_path = Path(config_path)
        self.runtime_state_path = Path(runtime_state_path)
        self.display_manifest_path = Path(display_manifest_path)
        self.output_dir = Path(output_dir)
        self.cache_root = Path(cache_root)
        self.data_root = Path(data_root)
        self.plugin_root = Path(plugin_root)
        self.utcnow = utcnow
        self.monotonic = monotonic
        self.sleep = sleep
        self._boot_hash = None
        self._health_events = []

    def reset_health_boot_tracking(self) -> None:
        """Forget the pinned boot id ahead of an intentional service restart."""

        self._boot_hash = None

    def _record_health_event(self, status, reason_codes) -> None:
        event = {
            "observed_at": self._utcnow_iso(),
            "status": status,
            "reason_codes": list(reason_codes),
        }
        if self._health_events:
            previous = self._health_events[-1]
            if (
                previous.get("status") == event["status"]
                and previous.get("reason_codes") == event["reason_codes"]
            ):
                previous["observed_at"] = event["observed_at"]
                previous["observations"] = int(previous.get("observations", 1)) + 1
                return
        if len(self._health_events) < HEALTH_EVENT_LIMIT:
            self._health_events.append(event)

    def _utcnow_iso(self) -> str:
        current = self.utcnow()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat()

    def _ready(self, *, allow_transient_degraded=False) -> dict:
        deadline = self.monotonic() + HEALTH_RETRY_SECONDS
        last_http_status = None
        last_reported_status = "unknown"
        last_reason_codes = []
        transient_reason_codes = set()
        transport_failed = False
        transient_seen = False
        while True:
            try:
                response = self.session.get(
                    _url(self.base_url, "/readyz"),
                    timeout=min(HTTP_TIMEOUT_SECONDS, HEALTH_RETRY_SECONDS),
                )
                last_http_status = response.status_code
                transport_failed = False
                try:
                    payload = response.json()
                except (TypeError, ValueError):
                    payload = None
                if isinstance(payload, dict):
                    reported = payload.get("status")
                    if reported in {"ready", "starting", "not_ready", "degraded"}:
                        last_reported_status = reported
                    else:
                        last_reported_status = "unknown"
                    last_reason_codes = _safe_health_reason_codes(payload)
                    if reported in {"ready", "starting", "not_ready", "degraded"}:
                        boot_id = payload.get("boot_id")
                        boot_hash = None
                        if isinstance(boot_id, str) and boot_id.strip():
                            boot_hash = hash_identifier(boot_id.strip())
                            if self._boot_hash is None:
                                self._boot_hash = boot_hash
                            elif self._boot_hash != boot_hash:
                                raise AuditAbort("health_boot_changed")
                        elif response.status_code == 200 and reported in {
                            "ready",
                            "degraded",
                        }:
                            raise AuditAbort("health_boot_id")
                    if response.status_code == 200 and reported == "ready":
                        if transient_seen:
                            self._record_health_event(
                                "recovered",
                                sorted(transient_reason_codes),
                            )
                        return {
                            "status": "ready",
                            "release_id": str(payload.get("release_id") or "unknown"),
                            "boot_id_hash": boot_hash,
                        }
                    if (
                        response.status_code == 200
                        and reported == "degraded"
                        and allow_transient_degraded
                        and last_reason_codes
                        and set(last_reason_codes) <= TRANSIENT_DEGRADED_REASON_CODES
                    ):
                        self._record_health_event("degraded", last_reason_codes)
                        return {
                            "status": "degraded",
                            "release_id": str(payload.get("release_id") or "unknown"),
                            "boot_id_hash": boot_hash,
                            "reason_codes": last_reason_codes,
                        }
                    transient_reason_codes.update(last_reason_codes)
                    transient_seen = True
            except requests.RequestException:
                transport_failed = True
                transient_seen = True

            remaining = deadline - self.monotonic()
            if remaining <= 0:
                code = "health_unreachable" if transport_failed else "health_not_ready"
                details = {"status": last_reported_status}
                if last_http_status is not None:
                    details["http_status"] = last_http_status
                if last_reason_codes:
                    details["reason_codes"] = last_reason_codes
                raise AuditAbort(code, safe_details=details)
            self.sleep(min(HEALTH_POLL_INTERVAL_SECONDS, remaining))

    def _config_plan(self) -> tuple[InstancePlan, ...]:
        config = _read_json(self.config_path, code="config_read_failed", abort=True)
        return build_acceptance_plan(
            config,
            now=self.utcnow(),
            plugin_root=self.plugin_root,
        )

    def _assert_config_stable(self, expected_fingerprint: str) -> None:
        if plan_fingerprint(self._config_plan()) != expected_fingerprint:
            raise AuditAbort("config_structure_drift")

    def _wait_for_data_evidence(self, instance, started_at):
        deadline = self.monotonic() + STATE_SETTLE_SECONDS
        last_failure = None
        while True:
            try:
                runtime = _read_json(
                    self.runtime_state_path,
                    code="runtime_state_read_failed",
                    abort=False,
                )
                evidence = validate_data_evidence(
                    runtime,
                    instance,
                    started_at=started_at,
                )
                state_path = resolve_bank_provider_state_path(
                    instance,
                    cache_root=self.cache_root,
                    data_root=self.data_root,
                )
                if state_path is not None:
                    state_document = _read_json(
                        state_path,
                        code="bank_provider_state_read_failed",
                        abort=False,
                    )
                    evidence["provider"] = validate_bank_provider_evidence(
                        state_document,
                        instance,
                        started_at=started_at,
                    )
                return runtime, evidence
            except EvidenceFailure as error:
                last_failure = error
                if self.monotonic() >= deadline:
                    raise last_failure
                self.sleep(POLL_INTERVAL_SECONDS)

    def _download_current_image(self):
        try:
            response = self.session.get(
                _url(self.base_url, "/api/current_image"),
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise EvidenceFailure("current_image_transport") from error
        if response.status_code != 200:
            raise EvidenceFailure(
                "current_image_unavailable",
                safe_details={"http_status": response.status_code},
            )
        image = inspect_png(response.content)
        return response.content, image, safe_headers(response.headers)

    def _capture_display(
        self,
        instance,
        *,
        baseline_manifest,
        artifact_suffix,
        display_started_at=None,
    ):
        deadline = self.monotonic() + STATE_SETTLE_SECONDS
        prefix = self._artifact_prefix(instance)
        image_name = f"{prefix}-{artifact_suffix}.png"
        headers_name = f"{prefix}-{artifact_suffix}.headers.json"
        artifacts = {
            "image": image_name,
            "headers": headers_name,
        }
        while True:
            runtime = _read_json(
                self.runtime_state_path,
                code="runtime_state_read_failed",
                abort=False,
            )
            manifest = _read_json(
                self.display_manifest_path,
                code="display_manifest_read_failed",
                abort=False,
            )
            image_bytes, image, headers = self._download_current_image()
            _secure_write(self.output_dir / image_name, image_bytes)
            write_safe_json(self.output_dir / headers_name, headers)
            try:
                evidence = validate_display_evidence(
                    runtime,
                    manifest,
                    instance,
                    image,
                    headers,
                    baseline_manifest=baseline_manifest,
                    display_started_at=display_started_at,
                )
            except EvidenceFailure as error:
                if error.code == "hardware_not_written" or self.monotonic() >= deadline:
                    details = dict(error.safe_details)
                    details["artifacts"] = artifacts
                    raise EvidenceFailure(error.code, safe_details=details) from error
                self.sleep(POLL_INTERVAL_SECONDS)
                continue

            return runtime, manifest, evidence, artifacts

    def _presentation_request(self, runtime, instance):
        state = _instance_runtime(runtime, instance)
        request = state.get("presentation_request")
        if request is None:
            return None
        return _validated_presentation_request(request, instance)

    def _wait_for_presentation(
        self,
        instance,
        request,
        timeout_seconds,
        *,
        expected_display_commit_id,
    ):
        deadline = self.monotonic() + timeout_seconds
        while True:
            self._ready(allow_transient_degraded=True)
            runtime = _read_json(
                self.runtime_state_path,
                code="runtime_state_read_failed",
                abort=False,
            )
            manifest = _read_json(
                self.display_manifest_path,
                code="display_manifest_read_failed",
                abort=False,
            )
            try:
                evidence = validate_presentation_outcome(
                    runtime,
                    manifest,
                    instance,
                    request=request,
                    expected_display_commit_id=expected_display_commit_id,
                )
                return runtime, manifest, evidence
            except EvidenceFailure as error:
                if error.code != "presentation_receipt_not_ready":
                    raise
                if self.monotonic() >= deadline:
                    raise EvidenceFailure("presentation_receipt_timeout")
                self.sleep(POLL_INTERVAL_SECONDS)

    def _wait_for_display_presentation_start(
        self,
        instance,
        baseline_runtime,
        initial_runtime,
        initial_manifest,
        *,
        display_started_at,
    ):
        expected_commit_id = initial_manifest.get("commit_id")
        if not isinstance(expected_commit_id, str) or not expected_commit_id:
            raise EvidenceFailure("presentation_display_commit_invalid")
        deadline = self.monotonic() + PRESENTATION_START_SETTLE_SECONDS
        runtime = initial_runtime
        manifest = initial_manifest
        while True:
            display = runtime.get("display")
            target = manifest.get("logical_target")
            if (
                not isinstance(display, dict)
                or display.get("commit_id") != expected_commit_id
                or manifest.get("commit_id") != expected_commit_id
                or not isinstance(target, dict)
                or target.get("instance_uuid") != instance.instance_uuid
                or manifest.get("instance_revision")
                != [instance.structural_generation, instance.settings_revision]
            ):
                raise EvidenceFailure("presentation_start_display_drift")

            request = self._presentation_request(runtime, instance)
            if request is not None:
                validate_display_created_presentation_request(
                    request,
                    instance,
                    manifest,
                    display_started_at=display_started_at,
                )
                return runtime, manifest, request, None
            try:
                evidence = validate_atomic_presentation_no_change(
                    runtime,
                    baseline_runtime,
                    manifest,
                    instance,
                )
                return runtime, manifest, None, evidence
            except EvidenceFailure:
                pass

            if self.monotonic() >= deadline:
                raise EvidenceFailure("presentation_request_missing")
            self.sleep(POLL_INTERVAL_SECONDS)
            runtime = _read_json(
                self.runtime_state_path,
                code="runtime_state_read_failed",
                abort=False,
            )
            manifest = _read_json(
                self.display_manifest_path,
                code="display_manifest_read_failed",
                abort=False,
            )

    @staticmethod
    def _artifact_prefix(instance):
        plugin = _SAFE_FILE_TOKEN.sub("_", instance.plugin_id).strip("._") or "plugin"
        return f"{instance.index:02d}-{plugin}-{instance.uuid_hash}"

    def _run_instance(self, instance: InstancePlan) -> dict:
        timeout_seconds = timeout_for(instance)
        started_at = self.utcnow()
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        data_job = submit_job(
            self.session,
            self.base_url,
            "/refresh_plugin_instance",
            instance,
        )
        data_job = poll_job(
            self.session,
            self.base_url,
            data_job["id"],
            timeout_seconds=timeout_seconds,
            monotonic=self.monotonic,
            sleep=self.sleep,
        )
        if data_job.get("status") != SUCCESS_JOB_STATUS:
            raise EvidenceFailure(
                "data_job_failed",
                safe_details={"data_job": safe_job_record(data_job)},
            )
        try:
            _runtime_after_data, data_evidence = self._wait_for_data_evidence(
                instance,
                started_at,
            )
        except EvidenceFailure as error:
            raise _with_job_context(error, data_job=data_job) from error

        display_job = None
        try:
            baseline_manifest = None
            if self.display_manifest_path.exists():
                baseline_manifest = _read_json(
                    self.display_manifest_path,
                    code="display_manifest_read_failed",
                    abort=False,
                )
            baseline_runtime = _read_json(
                self.runtime_state_path,
                code="runtime_state_read_failed",
                abort=False,
            )
            baseline_request = self._presentation_request(
                baseline_runtime,
                instance,
            )
            display_started_at = self.utcnow()
            if display_started_at.tzinfo is None:
                display_started_at = display_started_at.replace(tzinfo=timezone.utc)
            display_job = submit_job(
                self.session,
                self.base_url,
                "/display_plugin_instance",
                instance,
            )
            display_job = poll_job(
                self.session,
                self.base_url,
                display_job["id"],
                timeout_seconds=timeout_seconds,
                monotonic=self.monotonic,
                sleep=self.sleep,
            )
            if display_job.get("status") != SUCCESS_JOB_STATUS:
                raise EvidenceFailure("display_job_failed")
            runtime, manifest, display_evidence, artifacts = self._capture_display(
                instance,
                baseline_manifest=baseline_manifest,
                artifact_suffix="display",
                display_started_at=display_started_at,
            )

            presentation_evidence = None
            current_request = self._presentation_request(runtime, instance)
            presentation_request = None
            presentation_origin = None
            if baseline_request is not None:
                presentation_origin = "preexisting"
                if current_request is None:
                    presentation_evidence = validate_presentation_outcome(
                        runtime,
                        manifest,
                        instance,
                        request=baseline_request,
                        expected_display_commit_id=manifest.get("commit_id"),
                    )
                else:
                    presentation_request = baseline_request
            elif current_request is not None:
                if not instance.expects_presentation_refresh:
                    raise EvidenceFailure("presentation_request_unexpected")
                validate_display_created_presentation_request(
                    current_request,
                    instance,
                    manifest,
                    display_started_at=display_started_at,
                )
                presentation_request = current_request
                presentation_origin = "display_created"
            elif instance.expects_presentation_refresh:
                (
                    runtime,
                    manifest,
                    current_request,
                    presentation_evidence,
                ) = self._wait_for_display_presentation_start(
                    instance,
                    baseline_runtime,
                    runtime,
                    manifest,
                    display_started_at=display_started_at,
                )
                if current_request is not None:
                    presentation_request = current_request
                    presentation_origin = "display_created"
                else:
                    presentation_origin = "display_created_atomic"
            else:
                presentation_evidence = {"completion": "not_applicable"}
                presentation_origin = "disabled"

            if presentation_request is not None:
                _runtime, final_manifest, presentation_evidence = (
                    self._wait_for_presentation(
                        instance,
                        presentation_request,
                        presentation_timeout_for(instance),
                        expected_display_commit_id=manifest.get("commit_id"),
                    )
                )
                if presentation_evidence.get("completion") == "changed":
                    _runtime, _manifest, final_display, final_artifacts = (
                        self._capture_display(
                            instance,
                            baseline_manifest=manifest,
                            artifact_suffix="final",
                        )
                    )
                    if final_manifest.get("commit_id") != _manifest.get("commit_id"):
                        raise EvidenceFailure("presentation_capture_commit_changed")
                    display_evidence["final"] = final_display
                    artifacts.update({
                        "final_image": final_artifacts["image"],
                        "final_headers": final_artifacts["headers"],
                    })
            if presentation_evidence is not None and presentation_origin is not None:
                presentation_evidence = {
                    **presentation_evidence,
                    "request_origin": presentation_origin,
                }

            return safe_instance_result(
                instance,
                status="passed",
                data_job=data_job,
                display_job=display_job,
                data_evidence=data_evidence,
                display_evidence=display_evidence,
                presentation_evidence=presentation_evidence,
                artifacts=artifacts,
            )
        except EvidenceFailure as error:
            raise _with_job_context(
                error,
                data_job=data_job,
                display_job=display_job,
            ) from error

    def run(self) -> dict:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.output_dir, 0o700)
        except OSError:
            pass
        health = self._ready()
        plan = self._config_plan()
        fingerprint = plan_fingerprint(plan)
        started_at = self.utcnow().astimezone(timezone.utc).isoformat()
        results = []
        summary = {
            "schema_version": 1,
            "status": "running",
            "started_at": started_at,
            "health": health,
            "health_events": self._health_events,
            "playlist": plan[0].playlist_name,
            "plan_fingerprint": fingerprint,
            "expected_instances": EXPECTED_INSTANCE_COUNT,
            "results": results,
        }
        write_safe_json(self.output_dir / "summary.json", summary)

        try:
            for instance in plan:
                self._ready(allow_transient_degraded=True)
                self._assert_config_stable(fingerprint)
                try:
                    result = self._run_instance(instance)
                except EvidenceFailure as error:
                    details = error.safe_details
                    result = safe_instance_result(
                        instance,
                        status="failed",
                        failure_code=error.code,
                        data_job=(details.get("data_job") if isinstance(details, dict) else None),
                        display_job=(
                            details.get("display_job") if isinstance(details, dict) else None
                        ),
                        artifacts=(
                            details.get("artifacts") if isinstance(details, dict) else None
                        ),
                        safe_details={
                            key: value
                            for key, value in details.items()
                            if key not in {"data_job", "display_job", "artifacts"}
                        },
                    )
                results.append(result)
                item_name = f"{self._artifact_prefix(instance)}-evidence.json"
                write_safe_json(self.output_dir / item_name, result)
                summary["results"] = results
                write_safe_json(self.output_dir / "summary.json", summary)
            self._ready()
            self._assert_config_stable(fingerprint)
        except AuditAbort as error:
            summary.update({
                "status": "aborted",
                "abort_code": error.code,
                "safe_details": error.safe_details,
                "completed_at": self.utcnow().astimezone(timezone.utc).isoformat(),
            })
            write_safe_json(self.output_dir / "summary.json", summary)
            raise

        passed = sum(result.get("status") == "passed" for result in results)
        summary.update({
            "status": "passed" if passed == len(plan) else "failed",
            "completed_at": self.utcnow().astimezone(timezone.utc).isoformat(),
            "passed": passed,
            "failed": len(plan) - passed,
        })
        write_safe_json(self.output_dir / "summary.json", summary)
        return summary


def _default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("/var/lib/inkypi/data/live-acceptance") / stamp


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run exact 26-instance live internet/display acceptance on InkyPi",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1")
    parser.add_argument("--config", default="/var/lib/inkypi/config/device.json")
    parser.add_argument("--runtime-state", default="/var/lib/inkypi/data/runtime_state.json")
    parser.add_argument("--display-manifest", default="/var/lib/inkypi/display/display_manifest.json")
    parser.add_argument("--flask-secret", default="/var/lib/inkypi/config/flask_secret")
    parser.add_argument("--cache-root", default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--data-root", default=DEFAULT_DATA_ROOT)
    parser.add_argument("--plugin-root", default=DEFAULT_PLUGIN_ROOT)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--freeze-cycle-interval-seconds",
        type=int,
        default=None,
        help=(
            "Freeze plugin_cycle_interval_seconds to this value for the run, "
            "then restore the original configuration"
        ),
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print(json.dumps({"status": "aborted", "abort_code": "root_required"}))
        return 2
    try:
        secret = Path(args.flask_secret).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        print(json.dumps({"status": "aborted", "abort_code": "flask_secret_read_failed"}))
        return 2
    try:
        session, _csrf = build_admin_session(secret)
        runner = AcceptanceRunner(
            session=session,
            base_url=args.base_url,
            config_path=args.config,
            runtime_state_path=args.runtime_state,
            display_manifest_path=args.display_manifest,
            output_dir=args.output_dir or _default_output_dir(),
            cache_root=args.cache_root,
            data_root=args.data_root,
            plugin_root=args.plugin_root,
        )
        if args.freeze_cycle_interval_seconds is not None:
            orchestrator = CycleIntervalFreezeAcceptance(
                runner=runner,
                controller=SystemdController(),
                interval_seconds=args.freeze_cycle_interval_seconds,
            )
            summary = orchestrator.run()
        else:
            summary = runner.run()
    except AuditAbort as error:
        print(json.dumps({"status": "aborted", "abort_code": error.code}))
        return 2
    except Exception:
        print(json.dumps({"status": "aborted", "abort_code": "internal_failure"}))
        return 2
    print(json.dumps({
        "status": summary["status"],
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
        "output_dir": str(runner.output_dir),
    }))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
