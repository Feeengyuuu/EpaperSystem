#!/usr/bin/env python3
"""Serial, privacy-safe live acceptance for every instance in the active playlist.

Run this on the InkyPi host as root.  It intentionally separates provider-backed
DATA_REFRESH from the cache-only display command, then records filesystem,
physical-display-commit, and HTTP image evidence for each exact instance revision.
"""

from __future__ import annotations

import argparse
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
HTTP_TIMEOUT_SECONDS = 30
STATE_SETTLE_SECONDS = 12
POLL_INTERVAL_SECONDS = 1.0
DEFAULT_CACHE_ROOT = "/var/cache/inkypi"
DEFAULT_DATA_ROOT = "/var/lib/inkypi/data"
HEALTH_RETRY_SECONDS = 20
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


def build_acceptance_plan(config: dict, *, now: datetime | None = None) -> tuple[InstancePlan, ...]:
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
        plan.append(InstancePlan(
            index=index,
            playlist_name=playlist_name,
            plugin_id=_non_empty_text(raw.get("plugin_id"), code="config_plugin_id"),
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


def timeout_for(instance: InstancePlan) -> int:
    return (
        HEAVY_TIMEOUT_SECONDS
        if instance.plugin_id in HEAVY_PLUGIN_IDS
        else ORDINARY_TIMEOUT_SECONDS
    )


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
        self.utcnow = utcnow
        self.monotonic = monotonic
        self.sleep = sleep
        self._boot_hash = None
        self._health_events = []

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
        return build_acceptance_plan(config, now=self.utcnow())

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

    def _presentation_request_id(self, runtime, instance):
        state = _instance_runtime(runtime, instance)
        request = state.get("presentation_request")
        if request is None:
            return None
        if not isinstance(request, dict):
            raise EvidenceFailure("presentation_request_shape")
        if (
            request.get("structural_generation") != instance.structural_generation
            or request.get("settings_revision") != instance.settings_revision
        ):
            raise EvidenceFailure("presentation_request_revision_mismatch")
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise EvidenceFailure("presentation_request_shape")
        return request_id

    def _wait_for_presentation(self, instance, request_id, timeout_seconds):
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
                evidence = validate_presentation_completion(
                    runtime,
                    manifest,
                    instance,
                    request_id=request_id,
                )
                return runtime, manifest, evidence
            except EvidenceFailure as error:
                if error.code != "presentation_receipt_not_ready":
                    raise
                if self.monotonic() >= deadline:
                    raise EvidenceFailure("presentation_receipt_timeout")
                self.sleep(POLL_INTERVAL_SECONDS)

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
            request_id = self._presentation_request_id(runtime, instance)
            if request_id is not None:
                _runtime, final_manifest, presentation_evidence = (
                    self._wait_for_presentation(
                        instance,
                        request_id,
                        timeout_seconds,
                    )
                )
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
    parser.add_argument("--output-dir", default=None)
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
        )
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
