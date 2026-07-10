"""Versioned, immutable device configuration persistence with LKG recovery.

The store deliberately owns only persistence and publication.  It does not
mutate the live ``Config`` facade or model objects; callers build a detached
candidate and publish their higher-level view only after :meth:`commit`
returns successfully.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias
from uuid import UUID

try:  # Support both ``src.config_store`` tests and the installed flat module.
    from .utils.atomic_file import (
        AtomicCommitUncertainError,
        AtomicWriteError,
        atomic_write_json,
        fsync_directory,
    )
except ImportError:  # pragma: no cover - exercised by the installed runtime
    from utils.atomic_file import (
        AtomicCommitUncertainError,
        AtomicWriteError,
        atomic_write_json,
        fsync_directory,
    )


Pathish: TypeAlias = str | os.PathLike[str]
FrozenJSON: TypeAlias = None | bool | int | float | str | tuple["FrozenJSON", ...] | Mapping[str, "FrozenJSON"]

_SCHEDULED_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_IMAGE_ADJUSTMENTS = frozenset(
    {"saturation", "brightness", "sharpness", "contrast"}
)
_ROTATION_FIELDS = (
    "plugin_rotation_queue",
    "plugin_rotation_pool",
    "plugin_rotation_recent_history",
)


def _is_finite_number(value: object) -> bool:
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)


class ConfigStoreError(RuntimeError):
    """Base class for public configuration-store failures."""


class ConfigValidationError(ConfigStoreError, ValueError):
    """A candidate cannot be represented as a supported device config."""


class ConfigConflictError(ConfigStoreError):
    """The expected global configuration revision is stale."""

    def __init__(self, expected_version: int, actual_version: int) -> None:
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"config revision conflict: expected {expected_version}, actual {actual_version}"
        )


class ConfigPersistenceError(ConfigStoreError):
    """The primary atomic write failed before replacement."""

    def __init__(self, target: Path, stage: str) -> None:
        self.target = target
        self.stage = stage
        super().__init__(f"config persistence failed during {stage} for {target}")


class ConfigCommitUncertainError(ConfigStoreError):
    """The primary was replaced but directory-entry durability is unknown."""

    def __init__(self, target: Path) -> None:
        self.target = target
        super().__init__(f"config commit durability is uncertain for {target}")


class ConfigStoreFencedError(ConfigStoreError):
    """Writes are disabled until an explicit load reconciles persistence."""


# Descriptive aliases keep the exception vocabulary friendly to integrations.
ConfigCASConflictError = ConfigConflictError
ConfigPersistError = ConfigPersistenceError
ConfigPersistenceUncertainError = ConfigCommitUncertainError


@dataclass(frozen=True)
class ConfigSnapshot:
    """One complete, detached and deeply immutable configuration revision."""

    version: int
    data: Mapping[str, FrozenJSON]


@dataclass(frozen=True)
class ConfigStatus:
    """Operational status published alongside a configuration snapshot."""

    valid: bool
    writable: bool
    source: str
    version: int
    degraded_reason: str | None = None


@dataclass(frozen=True)
class ConfigState:
    """The single-reference, lock-free read boundary."""

    snapshot: ConfigSnapshot | None
    status: ConfigStatus


@dataclass(frozen=True)
class _ReadResult:
    path: Path
    source: str
    exists: bool
    snapshot: ConfigSnapshot | None
    payload: dict[str, Any] | None
    error_kind: str | None

    @property
    def valid(self) -> bool:
        return self.snapshot is not None


@dataclass(frozen=True)
class _PreparedCandidate:
    persisted_base: dict[str, Any]
    frozen_base: Mapping[str, FrozenJSON]


class ConfigStore:
    """Serialize writes while publishing immutable snapshots to lock-free readers."""

    def __init__(self, config_path: Pathish | object) -> None:
        path_value = getattr(config_path, "config_file", config_path)
        self.config_path = Path(path_value)
        self.lkg_paths = (
            self.config_path.with_name(
                f"{self.config_path.stem}.lkg.1{self.config_path.suffix}"
            ),
            self.config_path.with_name(
                f"{self.config_path.stem}.lkg.2{self.config_path.suffix}"
            ),
        )
        self._writer_lock = threading.Lock()
        self._revision_floor = 0
        self._fenced = True
        self._fence_reason: str | None = "not_loaded"
        self._published = ConfigState(
            snapshot=None,
            status=ConfigStatus(
                valid=False,
                writable=False,
                source="unloaded",
                version=0,
                degraded_reason="not_loaded",
            ),
        )

    def current(self) -> ConfigState:
        """Return the paired immutable state without acquiring the writer lock."""

        return self._published

    def snapshot(self) -> ConfigSnapshot | None:
        """Return the current immutable snapshot without taking a lock."""

        return self._published.snapshot

    def status(self) -> ConfigStatus:
        """Return the status paired with the current published snapshot."""

        return self._published.status

    def load(self) -> ConfigState:
        """Load or explicitly reconcile the primary and two LKG files."""

        with self._writer_lock:
            reconciling_uncertain = (
                self._fenced and self._fence_reason == "persistence_uncertain"
            )
            primary = self._read_path(self.config_path, "primary")
            if reconciling_uncertain:
                try:
                    fsync_directory(self.config_path.parent)
                except OSError:
                    self._publish_existing_status(
                        writable=False,
                        degraded_reason="persistence_uncertain",
                    )
                    return self._published
                self._fenced = False
                self._fence_reason = None

            if primary.error_kind == "read_failed":
                self._fenced = True
                self._fence_reason = "read_failed"
                self._publish_existing_status(
                    writable=False,
                    degraded_reason="read_failed",
                )
                return self._published

            lkg1 = self._read_path(self.lkg_paths[0], "lkg1")
            lkg2 = self._read_path(self.lkg_paths[1], "lkg2")
            all_results = (primary, lkg1, lkg2)
            observed_floor = max(
                (item.snapshot.version for item in all_results if item.valid),
                default=0,
            )
            self._revision_floor = max(self._revision_floor, observed_floor)

            if primary.valid:
                lkg_read_failed = any(
                    result.error_kind == "read_failed"
                    for result in (lkg1, lkg2)
                )
                if lkg_read_failed:
                    self._fenced = True
                    self._fence_reason = "lkg_read_failed"
                    self._published = self._state_for(
                        primary.snapshot,
                        source="primary",
                        writable=False,
                        degraded_reason="lkg_read_failed",
                    )
                    return self._published

                self._fenced = False
                self._fence_reason = None
                self._published = self._state_for(
                    primary.snapshot,
                    source="primary",
                    writable=True,
                )
                repaired_lkgs = self._repair_corrupt_lkgs(
                    primary.payload,
                    (lkg1, lkg2),
                )
                if not repaired_lkgs:
                    self._publish_existing_status(degraded_reason="lkg_update_failed")
                return self._published

            valid_lkgs = [item for item in (lkg1, lkg2) if item.valid]
            if valid_lkgs:
                chosen = max(
                    valid_lkgs,
                    key=lambda item: (
                        item.snapshot.version,
                        1 if item.source == "lkg1" else 0,
                    ),
                )
                return self._recover_primary(primary, chosen)

            self._fenced = primary.exists
            self._fence_reason = primary.error_kind if primary.exists else None
            if not primary.exists and not lkg1.exists and not lkg2.exists:
                self._published = ConfigState(
                    snapshot=None,
                    status=ConfigStatus(
                        valid=False,
                        writable=True,
                        source="missing",
                        version=0,
                        degraded_reason="missing",
                    ),
                )
            else:
                reason = primary.error_kind or "missing"
                self._published = ConfigState(
                    snapshot=None,
                    status=ConfigStatus(
                        valid=False,
                        writable=False,
                        source="invalid",
                        version=0,
                        degraded_reason=reason,
                    ),
                )
            return self._published

    def commit(self, expected_version: int, candidate: object) -> ConfigSnapshot:
        """CAS-persist a strict candidate and publish it with the next revision."""

        prepared = self._prepare_candidate(candidate)
        if type(expected_version) is not int or expected_version < 0:
            raise ConfigValidationError("expected_version must be a non-negative integer")

        with self._writer_lock:
            if self._fenced or not self._published.status.writable:
                raise ConfigStoreFencedError(
                    "config store is fenced; call load() to reconcile persistence"
                )

            old_state = self._published
            actual_version = (
                old_state.snapshot.version if old_state.snapshot is not None else 0
            )
            if expected_version != actual_version:
                raise ConfigConflictError(expected_version, actual_version)

            old_lkg1 = self._read_path(self.lkg_paths[0], "lkg1")
            old_lkg2 = self._read_path(self.lkg_paths[1], "lkg2")
            if any(
                result.error_kind == "read_failed"
                for result in (old_lkg1, old_lkg2)
            ):
                self._fenced = True
                self._fence_reason = "lkg_read_failed"
                self._publish_existing_status(
                    writable=False,
                    degraded_reason="lkg_read_failed",
                )
                raise ConfigStoreFencedError(
                    "config backup history is unreadable; call load() to reconcile persistence"
                )
            observed_lkg_floor = max(
                (
                    result.snapshot.version
                    for result in (old_lkg1, old_lkg2)
                    if result.valid
                ),
                default=0,
            )
            self._revision_floor = max(self._revision_floor, observed_lkg_floor)
            next_version = max(self._revision_floor, actual_version) + 1
            persisted = dict(prepared.persisted_base)
            persisted["schema_version"] = 1
            persisted["config_revision"] = next_version
            snapshot_values = dict(prepared.frozen_base)
            snapshot_values["schema_version"] = 1
            snapshot_values["config_revision"] = next_version
            new_snapshot = ConfigSnapshot(
                version=next_version,
                data=MappingProxyType(snapshot_values),
            )

            try:
                atomic_write_json(self.config_path, persisted, mode=0o600)
            except AtomicCommitUncertainError as error:
                self._fenced = True
                self._fence_reason = "persistence_uncertain"
                self._published = ConfigState(
                    snapshot=old_state.snapshot,
                    status=replace(
                        old_state.status,
                        writable=False,
                        degraded_reason="persistence_uncertain",
                    ),
                )
                raise ConfigCommitUncertainError(self.config_path) from error
            except AtomicWriteError as error:
                raise ConfigPersistenceError(self.config_path, error.stage) from error
            except OSError as error:
                raise ConfigPersistenceError(self.config_path, "prepare") from error

            self._revision_floor = next_version
            self._published = self._state_for(
                new_snapshot,
                source="primary",
                writable=True,
            )

            if not self._maintain_lkgs(
                old_state.snapshot,
                old_lkg1,
                old_lkg2,
                persisted,
            ):
                self._publish_existing_status(degraded_reason="lkg_update_failed")
            return new_snapshot

    def _prepare_candidate(self, candidate: object) -> _PreparedCandidate:
        frozen = _freeze_json(candidate)
        if not isinstance(frozen, Mapping):
            raise ConfigValidationError("config root must be an object")
        prepared = _thaw_json(frozen)
        if type(prepared) is not dict:  # defensive; mappings thaw to dictionaries
            raise ConfigValidationError("config root must be an object")

        supplied_schema = prepared.pop("schema_version", None)
        supplied_revision = prepared.pop("config_revision", None)
        if supplied_schema is not None and (
            type(supplied_schema) is not int or supplied_schema != 1
        ):
            raise ConfigValidationError("schema_version must be 1")
        if supplied_revision is not None and (
            type(supplied_revision) is not int or supplied_revision < 0
        ):
            raise ConfigValidationError("config_revision must be a non-negative integer")

        self._validate_device_config(prepared, legacy=False)
        frozen_prepared = _freeze_json(prepared)
        if not isinstance(frozen_prepared, Mapping):  # defensive
            raise ConfigValidationError("config root must be an object")
        return _PreparedCandidate(
            persisted_base=prepared,
            frozen_base=frozen_prepared,
        )

    def _read_path(self, path: Path, source: str) -> _ReadResult:
        try:
            with path.open("r", encoding="utf-8-sig") as stream:
                payload = json.load(
                    stream,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"non-finite JSON number: {value}")
                    ),
                )
        except FileNotFoundError:
            return _ReadResult(path, source, False, None, None, "missing")
        except OSError:
            return _ReadResult(path, source, True, None, None, "read_failed")
        except (UnicodeError, json.JSONDecodeError, ValueError):
            return _ReadResult(path, source, True, None, None, "syntax")

        if type(payload) is not dict:
            return _ReadResult(path, source, True, None, None, "root_type")

        has_schema = "schema_version" in payload
        has_revision = "config_revision" in payload
        if has_schema != has_revision:
            return _ReadResult(path, source, True, None, None, "schema")

        if not has_schema:
            version = 0
            legacy = True
        else:
            schema = payload.get("schema_version")
            revision = payload.get("config_revision")
            if (
                type(schema) is not int
                or schema != 1
                or type(revision) is not int
                or revision < 0
            ):
                return _ReadResult(path, source, True, None, None, "schema")
            version = revision
            legacy = False

        try:
            _freeze_json(payload)
            self._validate_device_config(payload, legacy=legacy)
            snapshot = self._snapshot_from_payload(payload, version)
        except ConfigValidationError:
            return _ReadResult(path, source, True, None, None, "schema")
        return _ReadResult(path, source, True, snapshot, payload, None)

    def _validate_device_config(self, payload: dict[str, Any], *, legacy: bool) -> None:
        if "resolution" in payload:
            resolution = payload["resolution"]
            if type(resolution) not in {list, tuple} or len(resolution) != 2:
                raise ConfigValidationError(
                    "resolution must contain exactly two positive integers"
                )
            if any(type(item) is not int or item <= 0 for item in resolution):
                raise ConfigValidationError(
                    "resolution must contain exactly two positive integers"
                )

        if "display_type" in payload:
            display_type = payload["display_type"]
            if type(display_type) is not str or not display_type.strip():
                raise ConfigValidationError("display_type must be a non-empty string")

        if "orientation" in payload:
            orientation = payload["orientation"]
            if type(orientation) is not str or orientation not in {
                "horizontal",
                "vertical",
            }:
                raise ConfigValidationError(
                    "orientation must be horizontal or vertical"
                )

        for field_name in (
            "scheduler_sleep_time",
            "plugin_cycle_interval_seconds",
        ):
            if field_name not in payload:
                continue
            value = payload[field_name]
            if not _is_finite_number(value) or value <= 0:
                raise ConfigValidationError(
                    f"{field_name} must be positive and finite"
                )

        if "image_settings" in payload:
            image_settings = payload["image_settings"]
            if type(image_settings) is not dict:
                raise ConfigValidationError("image_settings must be an object")
            for setting_name in _IMAGE_ADJUSTMENTS:
                if setting_name not in image_settings:
                    continue
                if not _is_finite_number(image_settings[setting_name]):
                    raise ConfigValidationError(
                        f"image_settings.{setting_name} must be finite and numeric"
                    )

        if "playlist_config" not in payload:
            return

        playlist_config = payload["playlist_config"]
        if type(playlist_config) is not dict:
            raise ConfigValidationError("playlist_config must be an object")
        playlists = playlist_config.get("playlists")
        if type(playlists) not in {list, tuple}:
            raise ConfigValidationError("playlist_config.playlists must be an array")

        playlist_names: set[str] = set()
        seen_uuids: set[str] = set()
        seen_identities: set[tuple[str, str]] = set()
        for playlist in playlists:
            if type(playlist) is not dict:
                raise ConfigValidationError("each playlist must be an object")

            playlist_name = playlist.get("name")
            if type(playlist_name) is not str or not playlist_name.strip():
                raise ConfigValidationError("playlist name must be a non-empty string")
            if playlist_name in playlist_names:
                raise ConfigValidationError("playlist names must be unique")
            playlist_names.add(playlist_name)

            start_time = playlist.get("start_time")
            if type(start_time) is not str or not _SCHEDULED_TIME.fullmatch(
                start_time
            ):
                raise ConfigValidationError(
                    "playlist start_time must use 24-hour HH:MM format"
                )
            end_time = playlist.get("end_time")
            if type(end_time) is not str or not (
                end_time == "24:00" or _SCHEDULED_TIME.fullmatch(end_time)
            ):
                raise ConfigValidationError(
                    "playlist end_time must use HH:MM or 24:00"
                )

            current_plugin_index = playlist.get("current_plugin_index")
            if (
                current_plugin_index is not None
                and type(current_plugin_index) is not int
            ):
                raise ConfigValidationError(
                    "playlist current_plugin_index must be an integer or null"
                )
            for field_name in _ROTATION_FIELDS:
                if field_name in playlist and type(playlist[field_name]) not in {
                    list,
                    tuple,
                }:
                    raise ConfigValidationError(
                        f"playlist {field_name} must be an array"
                    )

            plugins = playlist.get("plugins")
            if type(plugins) not in {list, tuple}:
                raise ConfigValidationError("playlist plugins must be an array")
            for instance in plugins:
                self._validate_instance(
                    instance,
                    seen_uuids,
                    seen_identities,
                    legacy=legacy,
                )

        active_playlist = playlist_config.get("active_playlist")
        if active_playlist is not None:
            if type(active_playlist) is not str or not active_playlist.strip():
                raise ConfigValidationError(
                    "active_playlist must be null or a non-empty string"
                )
            if not legacy and active_playlist not in playlist_names:
                raise ConfigValidationError(
                    "active_playlist must name an existing playlist"
                )

    @staticmethod
    def _validate_instance(
        instance: object,
        seen_uuids: set[str],
        seen_identities: set[tuple[str, str]],
        *,
        legacy: bool,
    ) -> None:
        if type(instance) is not dict:
            raise ConfigValidationError("each plugin instance must be an object")

        plugin_id = instance.get("plugin_id")
        instance_name = instance.get("name")
        if type(plugin_id) is not str or not plugin_id.strip():
            raise ConfigValidationError("plugin_id must be a non-empty string")
        if type(instance_name) is not str or not instance_name.strip():
            raise ConfigValidationError("plugin name must be a non-empty string")
        identity = (plugin_id, instance_name)
        if not legacy and identity in seen_identities:
            raise ConfigValidationError(
                "plugin (plugin_id, name) identity must be globally unique"
            )
        seen_identities.add(identity)

        if type(instance.get("plugin_settings")) is not dict:
            raise ConfigValidationError("plugin_settings must be an object")

        instance_uuid = instance.get("instance_uuid")
        if not (legacy and instance_uuid is None):
            if type(instance_uuid) is not str:
                raise ConfigValidationError(
                    "plugin instance_uuid must be a UUID string"
                )
            try:
                normalized_uuid = UUID(instance_uuid).hex
            except (ValueError, AttributeError):
                raise ConfigValidationError(
                    "plugin instance_uuid must be a UUID string"
                ) from None
            if normalized_uuid in seen_uuids:
                raise ConfigValidationError(
                    "plugin instance UUIDs must be globally unique"
                )
            seen_uuids.add(normalized_uuid)

        for field_name in ("structural_generation", "settings_revision"):
            if legacy and field_name not in instance:
                continue
            value = instance.get(field_name)
            if type(value) is not int or value <= 0:
                raise ConfigValidationError(
                    f"plugin {field_name} must be a positive integer"
                )

        refresh = instance.get("refresh")
        if type(refresh) is not dict:
            raise ConfigValidationError("plugin refresh must be an object")
        if "interval" in refresh:
            interval = refresh["interval"]
            if not _is_finite_number(interval) or (
                not legacy and interval <= 0
            ):
                raise ConfigValidationError(
                    "plugin refresh interval must be finite"
                    + ("" if legacy else " and positive")
                )
        if "scheduled" in refresh:
            scheduled = refresh["scheduled"]
            if type(scheduled) is not str or not _SCHEDULED_TIME.fullmatch(scheduled):
                raise ConfigValidationError(
                    "plugin scheduled refresh must use 24-hour HH:MM format"
                )

    def _recover_primary(
        self,
        primary: _ReadResult,
        chosen: _ReadResult,
    ) -> ConfigState:
        assert chosen.snapshot is not None
        assert chosen.payload is not None

        if primary.exists:
            quarantine_path = self._quarantine_path()
            try:
                self._quarantine_replace(self.config_path, quarantine_path)
            except OSError:
                self._fenced = True
                self._fence_reason = "quarantine_failed"
                self._published = self._state_for(
                    chosen.snapshot,
                    source=chosen.source,
                    writable=False,
                    degraded_reason="quarantine_failed",
                )
                return self._published
            try:
                fsync_directory(self.config_path.parent)
            except OSError:
                self._fenced = True
                self._fence_reason = "persistence_uncertain"
                self._published = self._state_for(
                    chosen.snapshot,
                    source=chosen.source,
                    writable=False,
                    degraded_reason="persistence_uncertain",
                )
                return self._published

        try:
            atomic_write_json(self.config_path, chosen.payload, mode=0o600)
        except AtomicCommitUncertainError:
            self._fenced = True
            self._fence_reason = "persistence_uncertain"
            self._published = self._state_for(
                chosen.snapshot,
                source=chosen.source,
                writable=False,
                degraded_reason="persistence_uncertain",
            )
            return self._published
        except (AtomicWriteError, OSError):
            self._fenced = True
            self._fence_reason = "restore_failed"
            self._published = self._state_for(
                chosen.snapshot,
                source=chosen.source,
                writable=False,
                degraded_reason="restore_failed",
            )
            return self._published

        self._fenced = False
        self._fence_reason = None
        self._published = self._state_for(
            chosen.snapshot,
            source=chosen.source,
            writable=True,
            degraded_reason="primary_recovered",
        )
        return self._published

    def _repair_corrupt_lkgs(
        self,
        primary_payload: dict[str, Any] | None,
        lkg_results: tuple[_ReadResult, _ReadResult],
    ) -> bool:
        if primary_payload is None:
            return False
        repaired = True
        for result in lkg_results:
            if not result.exists or result.valid:
                continue
            if result.error_kind == "read_failed":
                repaired = False
                continue
            try:
                atomic_write_json(result.path, primary_payload, mode=0o600)
            except Exception:
                repaired = False
        return repaired

    def _maintain_lkgs(
        self,
        old_primary: ConfigSnapshot | None,
        old_lkg1: _ReadResult,
        old_lkg2: _ReadResult,
        new_payload: dict[str, Any],
    ) -> bool:
        history: list[tuple[int, int, dict[str, Any]]] = []
        if old_primary is not None:
            history.append(
                (old_primary.version, 3, _thaw_json(old_primary.data))
            )
        if old_lkg1.valid:
            history.append((old_lkg1.snapshot.version, 2, old_lkg1.payload))
        if old_lkg2.valid:
            history.append((old_lkg2.snapshot.version, 1, old_lkg2.payload))

        if history:
            old_payload = max(history, key=lambda item: (item[0], item[1]))[2]
            try:
                atomic_write_json(self.lkg_paths[1], old_payload, mode=0o600)
            except Exception:
                if old_lkg1.valid:
                    return False
                try:
                    atomic_write_json(self.lkg_paths[0], new_payload, mode=0o600)
                except Exception:
                    pass
                return False

        try:
            atomic_write_json(self.lkg_paths[0], new_payload, mode=0o600)
        except Exception:
            return False
        return True

    def _state_for(
        self,
        snapshot: ConfigSnapshot,
        *,
        source: str,
        writable: bool,
        degraded_reason: str | None = None,
    ) -> ConfigState:
        return ConfigState(
            snapshot=snapshot,
            status=ConfigStatus(
                valid=True,
                writable=writable,
                source=source,
                version=snapshot.version,
                degraded_reason=degraded_reason,
            ),
        )

    def _publish_existing_status(
        self,
        *,
        writable: bool | None = None,
        degraded_reason: str | None = None,
    ) -> None:
        current = self._published
        status = current.status
        self._published = ConfigState(
            snapshot=current.snapshot,
            status=replace(
                status,
                writable=status.writable if writable is None else writable,
                degraded_reason=degraded_reason,
            ),
        )

    @staticmethod
    def _snapshot_from_payload(
        payload: dict[str, Any],
        version: int,
    ) -> ConfigSnapshot:
        frozen = _freeze_json(payload)
        if not isinstance(frozen, Mapping):  # defensive
            raise ConfigValidationError("config root must be an object")
        return ConfigSnapshot(version=version, data=frozen)

    def _quarantine_path(self) -> Path:
        stamp = self._utc_now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        token = self._random_token()
        return self.config_path.with_name(
            f"{self.config_path.stem}.corrupt.{stamp}.{token}{self.config_path.suffix}"
        )

    @staticmethod
    def _quarantine_replace(source: Path, destination: Path) -> None:
        os.replace(source, destination)

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _random_token() -> str:
        return secrets.token_hex(6)


def _freeze_json(value: object, active: set[int] | None = None) -> FrozenJSON:
    """Validate, detach and deeply freeze the exact supported JSON domain."""

    if active is None:
        active = set()
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is str:
        _require_utf8(value)
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ConfigValidationError("JSON numbers must be finite")
        return value
    if type(value) is dict:
        identity = id(value)
        if identity in active:
            raise ConfigValidationError("JSON values must not contain a cycle")
        active.add(identity)
        try:
            frozen: dict[str, FrozenJSON] = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise ConfigValidationError("JSON object keys must be strings")
                _require_utf8(key)
                frozen[key] = _freeze_json(item, active)
            return MappingProxyType(frozen)
        finally:
            active.remove(identity)
    if type(value) in {list, tuple}:
        identity = id(value)
        if identity in active:
            raise ConfigValidationError("JSON values must not contain a cycle")
        active.add(identity)
        try:
            return tuple(_freeze_json(item, active) for item in value)
        finally:
            active.remove(identity)
    raise ConfigValidationError(
        f"unsupported JSON value type: {type(value).__name__}"
    )


def _thaw_json(value: FrozenJSON) -> Any:
    """Create a plain JSON-encodable copy from an immutable snapshot value."""

    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_utf8(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ConfigValidationError(
            "JSON strings and object keys must contain valid UTF-8 scalars"
        ) from None
