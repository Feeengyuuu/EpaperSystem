#!/usr/bin/env python3
"""Privacy-safe physical acceptance for one complete automatic shuffle round.

The live orchestrator is intentionally kept in this standalone tool so the
production scheduler remains the system under test.  It resets only the active
playlist's persisted rotation bag and a temporary cycle interval while the
service is stopped; the normal InkyPi worker performs every selection, hardware
display commit, acknowledgement, and round refill.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import re
import secrets
import stat
import subprocess
import sys
import time
from urllib.parse import urljoin

import requests


def _load_shared_acceptance():
    """Load the sibling verifier both from the repo and a /var/tmp deployment."""

    try:
        import live_all_instances_acceptance as shared

        return shared
    except ModuleNotFoundError:
        from importlib.util import module_from_spec, spec_from_file_location

        path = Path(__file__).resolve().with_name("live_all_instances_acceptance.py")
        spec = spec_from_file_location("live_all_instances_acceptance", path)
        if spec is None or spec.loader is None:
            raise
        shared = module_from_spec(spec)
        sys.modules[spec.name] = shared
        spec.loader.exec_module(shared)
        return shared


_shared = _load_shared_acceptance()
AuditAbort = _shared.AuditAbort
AuditFailure = _shared.AuditFailure
EvidenceFailure = _shared.EvidenceFailure
InstancePlan = _shared.InstancePlan
EXPECTED_INSTANCE_COUNT = _shared.EXPECTED_INSTANCE_COUNT
build_acceptance_plan = _shared.build_acceptance_plan
hash_identifier = _shared.hash_identifier
inspect_png = _shared.inspect_png
plan_fingerprint = _shared.plan_fingerprint
safe_headers = _shared.safe_headers
validate_display_evidence = _shared.validate_display_evidence
write_safe_json = _shared.write_safe_json
_read_json = _shared._read_json
_secure_write = _shared._secure_write


DEFAULT_CONFIG_PATH = "/var/lib/inkypi/config/device.json"
DEFAULT_RUNTIME_STATE_PATH = "/var/lib/inkypi/data/runtime_state.json"
DEFAULT_DISPLAY_MANIFEST_PATH = "/var/lib/inkypi/display/display_manifest.json"
DEFAULT_BASE_URL = "http://127.0.0.1"
DEFAULT_TEST_INTERVAL_SECONDS = 1
DEFAULT_STARTUP_WINDOW_SECONDS = 45
DEFAULT_ROUND_TIMEOUT_SECONDS = 2700
DEFAULT_READY_TIMEOUT_SECONDS = 120
DEFAULT_POLL_INTERVAL_SECONDS = 0.25
HTTP_TIMEOUT_SECONDS = 20


_MISSING = object()


@dataclass(frozen=True)
class PreparedRotation:
    document: dict
    plan: tuple[InstancePlan, ...]
    playlist_name: str
    configured_uuids: tuple[str, ...]
    initial_queue: tuple[str, ...]
    current_uuid: str | None
    marker_refresh_time: str
    marker_refresh_info: dict
    original_interval_present: bool
    original_interval_value: object
    original_refresh_info: object


@dataclass(frozen=True)
class RotationAck:
    round_number: int
    round_index: int
    slot: int
    instance_uuid: str
    commit_id: str
    queue_remaining: int


def _active_playlist(document: dict, playlist_name: str) -> dict:
    playlist_config = document.get("playlist_config")
    playlists = (
        playlist_config.get("playlists")
        if isinstance(playlist_config, dict)
        else None
    )
    if not isinstance(playlists, list):
        raise AuditAbort("config_playlist_structure")
    matches = [
        playlist
        for playlist in playlists
        if isinstance(playlist, dict) and playlist.get("name") == playlist_name
    ]
    if len(matches) != 1:
        raise AuditAbort("config_playlist_identity")
    return matches[0]


def _manifest_current_uuid(
    manifest: dict | None,
    *,
    playlist_name: str,
    configured_uuids: tuple[str, ...],
) -> str | None:
    target = manifest.get("logical_target") if isinstance(manifest, dict) else None
    if not isinstance(target, dict):
        return None
    value = target.get("instance_uuid")
    if (
        target.get("kind") == "playlist"
        and target.get("playlist") == playlist_name
        and isinstance(value, str)
        and value in configured_uuids
    ):
        return value
    return None


def prepare_rotation_config(
    config: dict,
    *,
    current_manifest: dict | None,
    now: datetime,
    test_interval_seconds: int,
    startup_window_seconds: int,
    shuffle=None,
) -> PreparedRotation:
    """Build, but do not write, a full UUID rotation bag and future start gate."""

    if not isinstance(config, dict):
        raise AuditAbort("config_not_object")
    if not isinstance(test_interval_seconds, int) or test_interval_seconds < 1:
        raise AuditAbort("test_interval_invalid")
    if not isinstance(startup_window_seconds, int) or startup_window_seconds < 1:
        raise AuditAbort("startup_window_invalid")
    if not isinstance(now, datetime):
        raise AuditAbort("clock_invalid")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    document = copy.deepcopy(config)
    plan = build_acceptance_plan(document, now=now)
    playlist_name = plan[0].playlist_name
    playlist = _active_playlist(document, playlist_name)
    configured_uuids = tuple(item.instance_uuid for item in plan)
    current_uuid = _manifest_current_uuid(
        current_manifest,
        playlist_name=playlist_name,
        configured_uuids=configured_uuids,
    )

    queue = list(configured_uuids)
    (shuffle or random.SystemRandom().shuffle)(queue)
    if current_uuid is not None and len(queue) > 1 and queue[0] == current_uuid:
        replacement = next(
            index for index, value in enumerate(queue[1:], start=1)
            if value != current_uuid
        )
        queue[0], queue[replacement] = queue[replacement], queue[0]

    playlist["plugin_rotation_pool"] = list(configured_uuids)
    playlist["plugin_rotation_queue"] = list(queue)
    playlist["plugin_rotation_recent_history"] = (
        [current_uuid] if current_uuid is not None else []
    )
    if current_uuid is not None:
        playlist["current_plugin_index"] = configured_uuids.index(current_uuid)

    original_interval_present = "plugin_cycle_interval_seconds" in config
    original_interval_value = (
        copy.deepcopy(config["plugin_cycle_interval_seconds"])
        if original_interval_present
        else _MISSING
    )
    original_refresh_info = (
        copy.deepcopy(config["refresh_info"])
        if "refresh_info" in config
        else _MISSING
    )
    marker = (now.astimezone(timezone.utc) + timedelta(
        seconds=startup_window_seconds,
    )).isoformat()
    refresh_info = document.get("refresh_info")
    if not isinstance(refresh_info, dict):
        refresh_info = {}
        document["refresh_info"] = refresh_info
    refresh_info["refresh_time"] = marker
    marker_refresh_info = copy.deepcopy(refresh_info)
    document["plugin_cycle_interval_seconds"] = test_interval_seconds

    return PreparedRotation(
        document=document,
        plan=plan,
        playlist_name=playlist_name,
        configured_uuids=configured_uuids,
        initial_queue=tuple(queue),
        current_uuid=current_uuid,
        marker_refresh_time=marker,
        marker_refresh_info=marker_refresh_info,
        original_interval_present=original_interval_present,
        original_interval_value=original_interval_value,
        original_refresh_info=original_refresh_info,
    )


def restore_runtime_config(current: dict, prepared: PreparedRotation) -> dict:
    """Restore temporary controls without discarding real runtime acknowledgements."""

    if not isinstance(current, dict):
        raise AuditAbort("restore_config_not_object")
    document = copy.deepcopy(current)
    if prepared.original_interval_present:
        document["plugin_cycle_interval_seconds"] = copy.deepcopy(
            prepared.original_interval_value
        )
    else:
        document.pop("plugin_cycle_interval_seconds", None)

    refresh_info = document.get("refresh_info")
    marker_is_still_active = refresh_info == prepared.marker_refresh_info
    if marker_is_still_active:
        if prepared.original_refresh_info is _MISSING:
            document.pop("refresh_info", None)
        else:
            document["refresh_info"] = copy.deepcopy(
                prepared.original_refresh_info
            )
    return document


def safe_filename_token(value) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._-")
    return token or "plugin"


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
    temporary = target.with_name(
        f".{target.name}.{secrets.token_hex(8)}.tmp"
    )
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


class ShuffleRoundTracker:
    """Recognize only persisted, one-member automatic bag acknowledgements."""

    def __init__(
        self,
        *,
        configured_uuids,
        initial_queue,
        initial_pool,
        baseline_commit_id,
        current_uuid,
    ):
        self.configured_uuids = tuple(configured_uuids)
        self._configured_set = set(self.configured_uuids)
        if (
            len(self.configured_uuids) != EXPECTED_INSTANCE_COUNT
            or len(self._configured_set) != EXPECTED_INSTANCE_COUNT
        ):
            raise AuditAbort("config_instance_count")
        self._validate_pool(initial_pool)
        initial_queue = tuple(initial_queue)
        if (
            len(initial_queue) != EXPECTED_INSTANCE_COUNT
            or set(initial_queue) != self._configured_set
            or len(set(initial_queue)) != len(initial_queue)
        ):
            raise AuditAbort("rotation_initial_queue_invalid")
        if current_uuid is not None and initial_queue[0] == current_uuid:
            raise AuditAbort("rotation_initial_boundary_repeat")

        self._expected_queue = initial_queue
        self._baseline_commit_id = baseline_commit_id
        self._seen_commit_ids = {
            baseline_commit_id
        } if isinstance(baseline_commit_id, str) and baseline_commit_id else set()
        self._last_observed_commit_id = baseline_commit_id
        self._last_ack_uuid = None
        self._first_round_uuids = []
        self._boundary_ack = None
        self.ignored_same_uuid_followups = 0

    @property
    def first_round_count(self) -> int:
        return len(self._first_round_uuids)

    @property
    def complete(self) -> bool:
        return self._boundary_ack is not None

    @property
    def expected_queue(self) -> tuple[str, ...]:
        return self._expected_queue

    @property
    def last_ack_uuid(self) -> str | None:
        return self._last_ack_uuid

    def _validate_pool(self, pool) -> None:
        values = tuple(pool) if isinstance(pool, (tuple, list)) else ()
        if values != self.configured_uuids:
            raise EvidenceFailure("rotation_pool_drift")

    @staticmethod
    def _single_removed(before: tuple[str, ...], after: tuple[str, ...]):
        if len(after) != len(before) - 1:
            return None
        missing = [value for value in before if value not in set(after)]
        if len(missing) != 1:
            return None
        removed = missing[0]
        if tuple(value for value in before if value != removed) != after:
            return None
        return removed

    def _require_new_commit(self, commit_id) -> str:
        if not isinstance(commit_id, str) or not commit_id:
            raise EvidenceFailure("display_commit_missing")
        if commit_id in self._seen_commit_ids:
            raise EvidenceFailure("ack_display_commit_not_new")
        return commit_id

    def expected_ack_uuid(self, queue) -> str | None:
        """Return the sole member a valid next persisted transition removes."""

        queue = tuple(queue) if isinstance(queue, (tuple, list)) else ()
        if queue == self._expected_queue:
            return None
        if self.first_round_count < EXPECTED_INSTANCE_COUNT:
            return self._single_removed(self._expected_queue, queue)
        if self._expected_queue or len(queue) != EXPECTED_INSTANCE_COUNT - 1:
            return None
        if len(set(queue)) != len(queue) or not set(queue) < self._configured_set:
            return None
        missing = self._configured_set - set(queue)
        return next(iter(missing)) if len(missing) == 1 else None

    def observe(self, *, queue, pool, manifest_uuid, commit_id):
        self._validate_pool(pool)
        queue = tuple(queue) if isinstance(queue, (tuple, list)) else ()

        if queue == self._expected_queue:
            if (
                isinstance(commit_id, str)
                and commit_id
                and commit_id != self._last_observed_commit_id
                and manifest_uuid == self._last_ack_uuid
            ):
                self.ignored_same_uuid_followups += 1
                self._last_observed_commit_id = commit_id
            return None

        if self.complete:
            raise EvidenceFailure("rotation_changed_after_acceptance")

        if self.first_round_count < EXPECTED_INSTANCE_COUNT:
            removed = self._single_removed(self._expected_queue, queue)
            if removed is None:
                raise EvidenceFailure("rotation_queue_not_single_ack")
            if manifest_uuid != removed:
                raise EvidenceFailure("ack_manifest_target_mismatch")
            commit_id = self._require_new_commit(commit_id)
            if removed in self._first_round_uuids:
                raise EvidenceFailure("rotation_first_round_repeat")
            self._first_round_uuids.append(removed)
            self._expected_queue = queue
            self._seen_commit_ids.add(commit_id)
            self._last_observed_commit_id = commit_id
            self._last_ack_uuid = removed
            return RotationAck(
                round_number=1,
                round_index=self.first_round_count,
                slot=self.first_round_count,
                instance_uuid=removed,
                commit_id=commit_id,
                queue_remaining=len(queue),
            )

        if self._expected_queue:
            raise EvidenceFailure("rotation_first_round_not_empty")
        if (
            len(queue) != EXPECTED_INSTANCE_COUNT - 1
            or len(set(queue)) != len(queue)
            or not set(queue) < self._configured_set
        ):
            raise EvidenceFailure("rotation_second_round_refill_invalid")
        missing = self._configured_set - set(queue)
        if len(missing) != 1:
            raise EvidenceFailure("rotation_second_round_refill_invalid")
        removed = next(iter(missing))
        if manifest_uuid != removed:
            raise EvidenceFailure("ack_manifest_target_mismatch")
        if removed == self._first_round_uuids[-1]:
            raise EvidenceFailure("rotation_round_boundary_repeat")
        commit_id = self._require_new_commit(commit_id)
        self._expected_queue = queue
        self._seen_commit_ids.add(commit_id)
        self._last_observed_commit_id = commit_id
        self._last_ack_uuid = removed
        self._boundary_ack = RotationAck(
            round_number=2,
            round_index=1,
            slot=EXPECTED_INSTANCE_COUNT + 1,
            instance_uuid=removed,
            commit_id=commit_id,
            queue_remaining=len(queue),
        )
        return self._boundary_ack


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


class ShuffleRoundAcceptance:
    """Safely orchestrate the real scheduler and capture 27 physical commits."""

    def __init__(
        self,
        *,
        session,
        controller,
        base_url,
        config_path,
        runtime_state_path,
        display_manifest_path,
        output_dir,
        test_interval_seconds=DEFAULT_TEST_INTERVAL_SECONDS,
        startup_window_seconds=DEFAULT_STARTUP_WINDOW_SECONDS,
        round_timeout_seconds=DEFAULT_ROUND_TIMEOUT_SECONDS,
        ready_timeout_seconds=DEFAULT_READY_TIMEOUT_SECONDS,
        poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
        utcnow=lambda: datetime.now(timezone.utc),
        monotonic=time.monotonic,
        sleep=time.sleep,
        shuffle=None,
    ):
        self.session = session
        self.controller = controller
        self.base_url = str(base_url).rstrip("/") + "/"
        self.config_path = Path(config_path)
        self.runtime_state_path = Path(runtime_state_path)
        self.display_manifest_path = Path(display_manifest_path)
        self.output_dir = Path(output_dir)
        self.test_interval_seconds = int(test_interval_seconds)
        self.startup_window_seconds = int(startup_window_seconds)
        self.round_timeout_seconds = int(round_timeout_seconds)
        self.ready_timeout_seconds = int(ready_timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.utcnow = utcnow
        self.monotonic = monotonic
        self.sleep = sleep
        self.shuffle = shuffle

    def _now(self) -> datetime:
        value = self.utcnow()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _prepare_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.output_dir, 0o700)
        except OSError:
            pass

    def _wait_ready(self) -> dict:
        deadline = self.monotonic() + self.ready_timeout_seconds
        last_http_status = None
        while True:
            try:
                response = self.session.get(
                    urljoin(self.base_url, "readyz"),
                    timeout=min(HTTP_TIMEOUT_SECONDS, self.ready_timeout_seconds),
                )
                last_http_status = response.status_code
                try:
                    payload = response.json()
                except (TypeError, ValueError):
                    payload = None
                if (
                    response.status_code == 200
                    and isinstance(payload, dict)
                    and payload.get("status") == "ready"
                ):
                    boot_id = payload.get("boot_id")
                    return {
                        "status": "ready",
                        "release_id": str(payload.get("release_id") or "unknown"),
                        "boot_id_hash": (
                            hash_identifier(boot_id)
                            if isinstance(boot_id, str) and boot_id
                            else None
                        ),
                    }
            except requests.RequestException:
                pass
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                details = {}
                if last_http_status is not None:
                    details["http_status"] = last_http_status
                raise AuditAbort("service_not_ready", safe_details=details)
            self.sleep(min(self.poll_interval_seconds, remaining))

    def _rotation_state(
        self,
        document: dict,
        prepared: PreparedRotation,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        current_plan = build_acceptance_plan(document, now=self._now())
        if plan_fingerprint(current_plan) != plan_fingerprint(prepared.plan):
            raise AuditAbort("config_structure_drift")
        playlist = _active_playlist(document, prepared.playlist_name)
        queue = playlist.get("plugin_rotation_queue")
        pool = playlist.get("plugin_rotation_pool")
        if (
            not isinstance(queue, list)
            or not isinstance(pool, list)
            or any(not isinstance(value, str) or not value for value in queue)
            or any(not isinstance(value, str) or not value for value in pool)
        ):
            raise EvidenceFailure("rotation_state_shape")
        return tuple(queue), tuple(pool)

    @staticmethod
    def _manifest_identity(manifest: dict) -> tuple[str | None, str | None]:
        target = manifest.get("logical_target") if isinstance(manifest, dict) else None
        instance_uuid = target.get("instance_uuid") if isinstance(target, dict) else None
        commit_id = manifest.get("commit_id") if isinstance(manifest, dict) else None
        return (
            instance_uuid if isinstance(instance_uuid, str) and instance_uuid else None,
            commit_id if isinstance(commit_id, str) and commit_id else None,
        )

    def _download_current_image(self):
        try:
            response = self.session.get(
                urljoin(self.base_url, "api/current_image"),
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

    def _stable_candidate(
        self,
        *,
        prepared: PreparedRotation,
        queue: tuple[str, ...],
        pool: tuple[str, ...],
    ):
        manifest = _read_json(
            self.display_manifest_path,
            code="display_manifest_read_failed",
            abort=False,
        )
        runtime = _read_json(
            self.runtime_state_path,
            code="runtime_state_read_failed",
            abort=False,
        )
        image_bytes, image, headers = self._download_current_image()

        confirm_config = _read_json(
            self.config_path,
            code="config_read_failed",
            abort=True,
        )
        confirm_queue, confirm_pool = self._rotation_state(confirm_config, prepared)
        confirm_manifest = _read_json(
            self.display_manifest_path,
            code="display_manifest_read_failed",
            abort=False,
        )
        if confirm_queue != queue or confirm_pool != pool:
            return None
        _uuid, commit_id = self._manifest_identity(manifest)
        _confirm_uuid, confirm_commit_id = self._manifest_identity(confirm_manifest)
        if commit_id != confirm_commit_id:
            return None
        return manifest, runtime, image_bytes, image, headers

    @staticmethod
    def _committed_after(manifest: dict, started_at: datetime) -> None:
        value = manifest.get("committed_at")
        try:
            committed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if committed.tzinfo is None:
                committed = committed.replace(tzinfo=timezone.utc)
            committed = committed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError) as error:
            raise EvidenceFailure("display_commit_time_invalid") from error
        if committed < started_at:
            raise EvidenceFailure("display_commit_precedes_acceptance")

    def _safe_ack_record(
        self,
        *,
        ack: RotationAck,
        instance: InstancePlan,
        display_evidence: dict,
        screenshot_name: str,
    ) -> dict:
        image = display_evidence.get("image") or {}
        return {
            "round": ack.round_number,
            "round_index": ack.round_index,
            "slot": ack.slot,
            "plugin_id": instance.plugin_id,
            "uuid_hash": instance.uuid_hash,
            "commit_id_hash": hash_identifier(ack.commit_id),
            "queue_remaining": ack.queue_remaining,
            "committed_at": display_evidence.get("committed_at"),
            "hardware_written": display_evidence.get("hardware_written") is True,
            "pixel_hash": display_evidence.get("pixel_hash"),
            "image": {
                "width": image.get("width"),
                "height": image.get("height"),
                "png_sha256": image.get("png_sha256"),
                "byte_count": image.get("byte_count"),
            },
            "headers": display_evidence.get("headers") or {},
            "screenshot": screenshot_name,
        }

    def _monitor_round(
        self,
        prepared: PreparedRotation,
        baseline_manifest: dict,
        started_at: datetime,
    ) -> dict:
        baseline_uuid, baseline_commit_id = self._manifest_identity(baseline_manifest)
        tracker = ShuffleRoundTracker(
            configured_uuids=prepared.configured_uuids,
            initial_queue=prepared.initial_queue,
            initial_pool=prepared.configured_uuids,
            baseline_commit_id=baseline_commit_id,
            current_uuid=prepared.current_uuid or baseline_uuid,
        )
        instances = {item.instance_uuid: item for item in prepared.plan}
        evidence_records = []
        previous_manifest = baseline_manifest
        pending_by_uuid = {}
        pending_commit_ids = set()
        deadline = self.monotonic() + self.round_timeout_seconds

        while not tracker.complete:
            if self.monotonic() >= deadline:
                raise EvidenceFailure(
                    "rotation_round_timeout",
                    safe_details={
                        "accepted_slots": len(evidence_records),
                        "first_round_count": tracker.first_round_count,
                    },
                )
            document = _read_json(
                self.config_path,
                code="config_read_failed",
                abort=True,
            )
            queue, pool = self._rotation_state(document, prepared)
            if queue == tracker.expected_queue:
                manifest = _read_json(
                    self.display_manifest_path,
                    code="display_manifest_read_failed",
                    abort=False,
                )
                manifest_uuid, commit_id = self._manifest_identity(manifest)
                # DisplayTransaction publishes the hardware-backed manifest
                # before RefreshTask persists the bag acknowledgement. Capture
                # that exact evidence while the queue is still unchanged so a
                # same-target refreshOnDisplay follow-up cannot overwrite it.
                if (
                    manifest_uuid in instances
                    and manifest_uuid != tracker.last_ack_uuid
                    and isinstance(commit_id, str)
                    and commit_id
                    and commit_id not in pending_commit_ids
                    and commit_id != baseline_commit_id
                ):
                    pending = self._stable_candidate(
                        prepared=prepared,
                        queue=queue,
                        pool=pool,
                    )
                    if pending is not None:
                        pending_manifest = pending[0]
                        pending_uuid, pending_commit = self._manifest_identity(
                            pending_manifest
                        )
                        if pending_uuid in instances and pending_commit:
                            pending_by_uuid[pending_uuid] = pending
                            pending_commit_ids.add(pending_commit)
                tracker.observe(
                    queue=queue,
                    pool=pool,
                    manifest_uuid=manifest_uuid,
                    commit_id=commit_id,
                )
                self.sleep(self.poll_interval_seconds)
                continue

            expected_uuid = tracker.expected_ack_uuid(queue)
            candidate = pending_by_uuid.get(expected_uuid)
            if candidate is not None:
                confirm_config = _read_json(
                    self.config_path,
                    code="config_read_failed",
                    abort=True,
                )
                confirm_queue, confirm_pool = self._rotation_state(
                    confirm_config,
                    prepared,
                )
                if confirm_queue != queue or confirm_pool != pool:
                    candidate = None
                    self.sleep(self.poll_interval_seconds)
                    continue
            else:
                candidate = self._stable_candidate(
                    prepared=prepared,
                    queue=queue,
                    pool=pool,
                )
            if candidate is None:
                self.sleep(self.poll_interval_seconds)
                continue
            manifest, runtime, image_bytes, image, headers = candidate
            manifest_uuid, commit_id = self._manifest_identity(manifest)
            ack = tracker.observe(
                queue=queue,
                pool=pool,
                manifest_uuid=manifest_uuid,
                commit_id=commit_id,
            )
            if ack is None:
                self.sleep(self.poll_interval_seconds)
                continue
            instance = instances.get(ack.instance_uuid)
            if instance is None:
                raise EvidenceFailure("ack_instance_not_configured")
            self._committed_after(manifest, started_at)
            display_evidence = validate_display_evidence(
                runtime,
                manifest,
                instance,
                image,
                headers,
                baseline_manifest=previous_manifest,
            )
            screenshot_name = (
                f"slot-{ack.slot:02d}-{safe_filename_token(instance.plugin_id)}-"
                f"{instance.uuid_hash}.png"
            )
            _secure_write(self.output_dir / screenshot_name, image_bytes)
            evidence_records.append(self._safe_ack_record(
                ack=ack,
                instance=instance,
                display_evidence=display_evidence,
                screenshot_name=screenshot_name,
            ))
            previous_manifest = manifest
            pending_by_uuid.clear()

        first_round = [record for record in evidence_records if record["round"] == 1]
        if (
            len(first_round) != EXPECTED_INSTANCE_COUNT
            or len({record["uuid_hash"] for record in first_round})
            != EXPECTED_INSTANCE_COUNT
            or len(evidence_records) != EXPECTED_INSTANCE_COUNT + 1
        ):
            raise EvidenceFailure("rotation_evidence_count")
        if any(record["hardware_written"] is not True for record in evidence_records):
            raise EvidenceFailure("hardware_not_written")
        return {
            "accepted_slots": len(evidence_records),
            "first_round_unique": len({record["uuid_hash"] for record in first_round}),
            "boundary_no_repeat": (
                evidence_records[-1]["uuid_hash"]
                != evidence_records[-2]["uuid_hash"]
            ),
            "ignored_same_uuid_followups": tracker.ignored_same_uuid_followups,
            "events": evidence_records,
        }

    def run(self) -> dict:
        self._prepare_output_dir()
        summary = {
            "schema_version": 1,
            "status": "failed",
            "expected_instances": EXPECTED_INSTANCE_COUNT,
            "expected_slots": EXPECTED_INSTANCE_COUNT + 1,
            "accepted_slots": 0,
            "service_ready_restored": False,
            "service_left_stopped": False,
        }
        prepared = None
        monitor_succeeded = False
        primary_error_code = None
        started_at = self._now()

        try:
            self.controller.stop()
            original_config = _read_json(
                self.config_path,
                code="config_read_failed",
                abort=True,
            )
            baseline_manifest = _read_json(
                self.display_manifest_path,
                code="display_manifest_read_failed",
                abort=True,
            )
            prepared = prepare_rotation_config(
                original_config,
                current_manifest=baseline_manifest,
                now=self._now(),
                test_interval_seconds=self.test_interval_seconds,
                startup_window_seconds=self.startup_window_seconds,
                shuffle=self.shuffle,
            )
            atomic_write_json(self.config_path, prepared.document)
            summary.update({
                "playlist_hash": hash_identifier(prepared.playlist_name),
                "configured_instances": len(prepared.plan),
                "test_cycle_interval_seconds": self.test_interval_seconds,
                "startup_window_seconds": self.startup_window_seconds,
            })
            started_at = self._now()
            self.controller.start()
            summary["test_service_ready"] = self._wait_ready()
            monitor_result = self._monitor_round(
                prepared,
                baseline_manifest,
                started_at,
            )
            summary.update(monitor_result)
            monitor_succeeded = True
        except AuditFailure as error:
            primary_error_code = error.code
            summary["error_code"] = error.code
            if error.safe_details:
                summary["safe_error_details"] = error.safe_details
        except Exception:
            primary_error_code = "internal_failure"
            summary["error_code"] = primary_error_code
        finally:
            restore_error_code = None
            try:
                self.controller.stop()
                summary["service_left_stopped"] = True
            except AuditFailure:
                restore_error_code = "restore_service_stop_failed"

            if prepared is not None and restore_error_code is None:
                try:
                    current_config = _read_json(
                        self.config_path,
                        code="restore_config_read_failed",
                        abort=True,
                    )
                    restored_config = restore_runtime_config(current_config, prepared)
                    atomic_write_json(self.config_path, restored_config)
                    restored_value = restored_config.get(
                        "plugin_cycle_interval_seconds",
                        "default",
                    )
                    summary["restored_cycle_interval_seconds"] = restored_value
                except AuditFailure:
                    restore_error_code = "restore_config_failed"

            # Never restart with the accelerated test interval if restoration
            # did not commit durably. A stopped service is the fail-safe state.
            if restore_error_code is None:
                try:
                    self.controller.start()
                    summary["service_left_stopped"] = False
                except AuditFailure:
                    restore_error_code = "restore_service_start_failed"

            if restore_error_code is None:
                try:
                    summary["final_service_ready"] = self._wait_ready()
                    summary["service_ready_restored"] = True
                except AuditFailure:
                    restore_error_code = "restore_service_not_ready"

            if restore_error_code is not None:
                summary["restore_error_code"] = restore_error_code
                if primary_error_code is None:
                    summary["error_code"] = restore_error_code
            if monitor_succeeded and restore_error_code is None:
                summary.pop("error_code", None)
                summary["status"] = "passed"
            else:
                summary["status"] = "failed"
            write_safe_json(self.output_dir / "summary.json", summary)
        return summary


def _default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("/var/lib/inkypi/data/live-shuffle-acceptance") / stamp


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prove one real 26-instance automatic shuffle round plus the next "
            "round's first physical display"
        ),
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--runtime-state", default=DEFAULT_RUNTIME_STATE_PATH)
    parser.add_argument("--display-manifest", default=DEFAULT_DISPLAY_MANIFEST_PATH)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--service", default="inkypi.service")
    parser.add_argument(
        "--test-interval-seconds",
        type=int,
        default=DEFAULT_TEST_INTERVAL_SECONDS,
    )
    parser.add_argument(
        "--startup-window-seconds",
        type=int,
        default=DEFAULT_STARTUP_WINDOW_SECONDS,
    )
    parser.add_argument(
        "--round-timeout-seconds",
        type=int,
        default=DEFAULT_ROUND_TIMEOUT_SECONDS,
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print(json.dumps({"status": "aborted", "error_code": "root_required"}))
        return 2
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    try:
        runner = ShuffleRoundAcceptance(
            session=requests.Session(),
            controller=SystemdController(service_name=args.service),
            base_url=args.base_url,
            config_path=args.config,
            runtime_state_path=args.runtime_state,
            display_manifest_path=args.display_manifest,
            output_dir=output_dir,
            test_interval_seconds=args.test_interval_seconds,
            startup_window_seconds=args.startup_window_seconds,
            round_timeout_seconds=args.round_timeout_seconds,
        )
        summary = runner.run()
    except Exception:
        print(json.dumps({
            "status": "aborted",
            "error_code": "orchestrator_start_failed",
            "output_dir": str(output_dir),
        }))
        return 2
    print(json.dumps({
        "status": summary.get("status"),
        "accepted_slots": summary.get("accepted_slots", 0),
        "service_ready_restored": summary.get("service_ready_restored", False),
        "output_dir": str(output_dir),
    }))
    return 0 if summary.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
