"""Manifest-backed display commits that never publish intent before hardware."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

try:
    from ..runtime.cache_lifecycle import (
        CleanupBudget,
        LifecycleAggregate,
        LifecycleAllowance,
        LifecycleBudget,
    )
except ImportError:  # pragma: no cover - production imports modules from src/
    from runtime.cache_lifecycle import (
        CleanupBudget,
        LifecycleAggregate,
        LifecycleAllowance,
        LifecycleBudget,
    )
from utils.atomic_file import atomic_write_bytes, atomic_write_image, atomic_write_json
from utils.image_utils import compute_image_hash
from utils.safe_image import safe_open_image


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MANIFEST_NAME = "display_manifest.json"
OBJECTS_DIR_NAME = "objects"
MAX_RETAINED_OBJECTS = 8
_COMMIT_ID = re.compile(r"^[0-9a-f]{32}$")
_ATOMIC_TEMP_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")
_OBJECT_ATOMIC_TEMP = re.compile(
    r"^\.[0-9a-f]{32}\.png\.[A-Za-z0-9_-]+\.tmp$"
)


def _display_lifecycle_allowance(*, budget, allowance, aggregate, clock):
    if allowance is not None:
        if not isinstance(allowance, LifecycleAllowance):
            raise TypeError("allowance must be a LifecycleAllowance")
        if aggregate is not None and allowance.aggregate is not aggregate:
            raise ValueError("allowance and aggregate must share the same counters")
        return allowance
    if isinstance(budget, CleanupBudget):
        budget = budget.start(clock())
    if not isinstance(budget, LifecycleBudget):
        raise TypeError("budget must be a CleanupBudget or LifecycleBudget")
    if aggregate is None:
        aggregate = LifecycleAggregate()
    elif not isinstance(aggregate, LifecycleAggregate):
        raise TypeError("aggregate must be a LifecycleAggregate")
    return LifecycleAllowance(budget, aggregate, clock=clock)


def _is_reparse_point(info):
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    marker = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & marker)


def _stat_token(info):
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
    )


def _is_atomic_temp_for(name, target_name):
    prefix = f".{target_name}."
    suffix = ".tmp"
    if not name.startswith(prefix) or not name.endswith(suffix):
        return False
    token = name[len(prefix) : -len(suffix)]
    return bool(token and _ATOMIC_TEMP_TOKEN.fullmatch(token))


class DisplayCommitUnknownError(RuntimeError):
    """Hardware changed, but the authoritative manifest commit is uncertain."""

    def __init__(self, commit_id: str):
        self.commit_id = commit_id
        super().__init__(f"display commit state is unknown: {commit_id}")


def _json_detach(value):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    return json.loads(encoded)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _frozen_mapping(value) -> Mapping[str, object]:
    detached = _json_detach(value or {})
    if not isinstance(detached, dict):
        raise TypeError("logical_target must be a mapping")
    return _freeze(detached)


def _revision(value) -> tuple[int, int] | None:
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise TypeError("instance_revision must be a pair of integers")
    normalized = tuple(value)
    if any(type(item) is not int or item < 0 for item in normalized):
        raise ValueError("instance_revision values must be non-negative integers")
    return normalized


@dataclass(frozen=True)
class PreparedDisplay:
    commit_id: str
    image_path: Path
    pixel_hash: str
    hardware_fingerprint: str
    logical_target: Mapping[str, object]
    instance_revision: tuple[int, int] | None
    image_settings: tuple[object, ...]


@dataclass(frozen=True)
class DisplayCommit:
    commit_id: str
    image_path: Path
    pixel_hash: str
    hardware_fingerprint: str
    logical_target: Mapping[str, object]
    instance_revision: tuple[int, int] | None
    image_settings: tuple[object, ...]
    hardware_written: bool
    committed_at: str

    def to_manifest(self, *, display_dir: Path) -> dict:
        relative_image = self.image_path.relative_to(display_dir).as_posix()
        return {
            "schema_version": SCHEMA_VERSION,
            "commit_id": self.commit_id,
            "image": relative_image,
            "pixel_hash": self.pixel_hash,
            "hardware_fingerprint": self.hardware_fingerprint,
            "logical_target": _thaw(self.logical_target),
            "instance_revision": (
                list(self.instance_revision)
                if self.instance_revision is not None
                else None
            ),
            "image_settings": _thaw(self.image_settings),
            "hardware_written": self.hardware_written,
            "committed_at": self.committed_at,
        }


class DisplayTransaction:
    """Prepare immutable image objects, write hardware, then publish a manifest."""

    def __init__(
        self,
        manager,
        *,
        display_dir,
        compatibility_image_path,
        runtime_state_store,
    ):
        self.manager = manager
        self.display_dir = Path(display_dir)
        self.objects_dir = self.display_dir / OBJECTS_DIR_NAME
        self.manifest_path = self.display_dir / MANIFEST_NAME
        self.compatibility_image_path = Path(compatibility_image_path)
        self.runtime_state = runtime_state_store
        self._lock = threading.RLock()
        self.display_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.compatibility_image_path.parent.mkdir(parents=True, exist_ok=True)

    def prepare(
        self,
        image,
        *,
        image_settings=(),
        logical_target=None,
        instance_revision=None,
    ) -> PreparedDisplay:
        settings = _freeze(_json_detach(list(image_settings or ())))
        final = self.manager.prepare_image(image, image_settings=settings)
        commit_id = uuid4().hex
        object_path = self.objects_dir / f"{commit_id}.png"
        atomic_write_image(object_path, final)
        return PreparedDisplay(
            commit_id=commit_id,
            image_path=object_path,
            pixel_hash=compute_image_hash(final),
            hardware_fingerprint=self.manager.hardware_fingerprint(settings),
            logical_target=_frozen_mapping(logical_target),
            instance_revision=_revision(instance_revision),
            image_settings=settings,
        )

    def commit(self, prepared: PreparedDisplay, *, task_context) -> DisplayCommit:
        if not isinstance(prepared, PreparedDisplay):
            raise TypeError("prepared must be a PreparedDisplay")
        task_context.raise_if_cancelled()
        with self._lock:
            self._validate_prepared(prepared)
            previous = self.current()
            hardware_needed = previous is None or (
                previous.pixel_hash != prepared.pixel_hash
                or previous.hardware_fingerprint != prepared.hardware_fingerprint
            )
            if hardware_needed:
                self.manager.write_hardware_path(
                    prepared.image_path,
                    image_settings=prepared.image_settings,
                    task_context=task_context,
                )

            commit = DisplayCommit(
                commit_id=prepared.commit_id,
                image_path=prepared.image_path,
                pixel_hash=prepared.pixel_hash,
                hardware_fingerprint=prepared.hardware_fingerprint,
                logical_target=prepared.logical_target,
                instance_revision=prepared.instance_revision,
                image_settings=prepared.image_settings,
                hardware_written=hardware_needed,
                committed_at=datetime.now(timezone.utc).isoformat(),
            )
            manifest = commit.to_manifest(display_dir=self.display_dir)
            try:
                atomic_write_json(self.manifest_path, manifest, mode=0o600)
            except OSError as error:
                self.runtime_state.set_display_state(
                    "display_unknown",
                    prepared.commit_id,
                    instance_uuid=self._instance_uuid(prepared.logical_target),
                )
                raise DisplayCommitUnknownError(prepared.commit_id) from error

            self.runtime_state.set_display_state(
                "committed",
                prepared.commit_id,
                instance_uuid=self._instance_uuid(prepared.logical_target),
                changed_at=commit.committed_at,
            )
            self._publish_compatibility_image(prepared.image_path)
            self._prune_objects(current_path=prepared.image_path)
            return commit

    def current(self) -> DisplayCommit | None:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            commit = self._commit_from_manifest(payload)
            image = safe_open_image(commit.image_path)
            if compute_image_hash(image) != commit.pixel_hash:
                raise ValueError("display object hash does not match manifest")
            return commit
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Ignoring invalid display manifest")
            return None

    def maintenance(
        self,
        *,
        now_epoch,
        stale_seconds,
        budget=None,
        dry_run=False,
        allowance=None,
        aggregate=None,
    ):
        """Recover owned atomic residue while preserving display authority."""

        allowance = _display_lifecycle_allowance(
            budget=budget,
            allowance=allowance,
            aggregate=aggregate,
            clock=time.monotonic,
        )
        aggregate = allowance.aggregate
        try:
            now_epoch = float(now_epoch)
            stale_seconds = float(stale_seconds)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("maintenance times must be finite numbers") from None
        if not math.isfinite(now_epoch) or not math.isfinite(stale_seconds):
            raise ValueError("maintenance times must be finite numbers")
        stale_seconds = max(0.0, stale_seconds)

        with self._lock:
            planning = {
                "baseline_entries": aggregate.deleted_entries,
                "baseline_bytes": aggregate.deleted_bytes,
                "entries": 0,
                "bytes": 0,
            }
            current = self.current()
            if not allowance.can_delete(0):
                return aggregate
            if current is not None:
                aggregate.retained_current += 1
                stopped = self._prune_objects(
                    current_path=current.image_path,
                    allowance=allowance,
                    dry_run=dry_run,
                    planning=planning,
                )
                if stopped:
                    return aggregate

            display_root = Path(os.path.abspath(self.display_dir))
            objects_root = Path(os.path.abspath(self.objects_dir))
            compatibility_path = Path(os.path.abspath(self.compatibility_image_path))
            display_targets = {self.manifest_path.name}
            roots = []
            if compatibility_path.parent == display_root:
                display_targets.add(compatibility_path.name)
            roots.append((display_root, frozenset(display_targets), False))
            roots.append((objects_root, frozenset(), True))
            for configured_root, target_names, object_temps in roots:
                try:
                    root_info = os.lstat(configured_root)
                    if (
                        not stat.S_ISDIR(root_info.st_mode)
                        or stat.S_ISLNK(root_info.st_mode)
                        or _is_reparse_point(root_info)
                    ):
                        aggregate.skipped_unsafe += 1
                        allowance.mark_backlog()
                        return aggregate
                    root = configured_root.resolve(strict=True)
                except FileNotFoundError:
                    continue
                except OSError:
                    aggregate.error_count += 1
                    allowance.mark_backlog()
                    return aggregate

                names = []
                try:
                    with os.scandir(root) as iterator:
                        while True:
                            try:
                                entry = next(iterator)
                            except StopIteration:
                                break
                            if not allowance.consume_scan():
                                return aggregate
                            names.append(entry.name)
                except OSError:
                    aggregate.error_count += 1
                    allowance.mark_backlog()
                    return aggregate

                for name in sorted(names):
                    if not allowance.can_delete(0):
                        return aggregate
                    reserved = (
                        bool(_OBJECT_ATOMIC_TEMP.fullmatch(name))
                        if object_temps
                        else any(
                            _is_atomic_temp_for(name, target_name)
                            for target_name in target_names
                        )
                    )
                    if not reserved:
                        continue

                    candidate = root / name
                    try:
                        info = os.lstat(candidate)
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or stat.S_ISLNK(info.st_mode)
                            or _is_reparse_point(info)
                            or candidate.resolve(strict=True).parent != root
                        ):
                            aggregate.skipped_unsafe += 1
                            continue
                    except FileNotFoundError:
                        continue
                    except OSError:
                        aggregate.error_count += 1
                        continue

                    if now_epoch - float(info.st_mtime) <= stale_seconds:
                        aggregate.retained_recent += 1
                        continue
                    size = max(0, int(info.st_size))
                    if (
                        planning["baseline_entries"] + planning["entries"]
                        >= allowance.budget.max_deleted
                        or planning["baseline_bytes"] + planning["bytes"] + size
                        > allowance.budget.max_deleted_bytes
                    ):
                        allowance.mark_backlog()
                        return aggregate

                    planning["entries"] += 1
                    planning["bytes"] += size
                    aggregate.candidate_entries += 1
                    if dry_run:
                        continue

                    expected_token = _stat_token(info)
                    try:
                        current_info = os.lstat(candidate)
                        if (
                            _stat_token(current_info) != expected_token
                            or not stat.S_ISREG(current_info.st_mode)
                            or stat.S_ISLNK(current_info.st_mode)
                            or _is_reparse_point(current_info)
                            or candidate.resolve(strict=True).parent != root
                        ):
                            aggregate.skipped_unsafe += 1
                            allowance.mark_backlog()
                            continue
                        candidate.unlink()
                    except FileNotFoundError:
                        continue
                    except OSError:
                        aggregate.error_count += 1
                        allowance.mark_backlog()
                        continue
                    allowance.consume_delete(size)
            return aggregate

    def recover(self, *, task_context) -> DisplayCommit | None:
        task_context.raise_if_cancelled()
        with self._lock:
            current = self.current()
            if current is None:
                self.runtime_state.set_display_state("not_ready", None, instance_uuid=None)
                return None

            state = self.runtime_state.snapshot()
            needs_hardware = (
                state.display_state in {"unknown", "display_unknown", "not_ready"}
                or self._has_newer_orphan(current.image_path)
            )
            if needs_hardware:
                self.manager.write_hardware_path(
                    current.image_path,
                    image_settings=current.image_settings,
                    task_context=task_context,
                )
            self.runtime_state.set_display_state(
                "committed",
                current.commit_id,
                instance_uuid=self._instance_uuid(current.logical_target),
            )
            self._prune_objects(current_path=current.image_path)
            self._publish_compatibility_image(current.image_path)
            return current

    def _commit_from_manifest(self, payload) -> DisplayCommit:
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported display manifest")
        commit_id = payload.get("commit_id")
        if not isinstance(commit_id, str) or not _COMMIT_ID.fullmatch(commit_id):
            raise ValueError("invalid display commit id")
        relative_image = payload.get("image")
        if not isinstance(relative_image, str):
            raise ValueError("display image path must be a string")
        image_path = (self.display_dir / relative_image).resolve()
        objects_root = self.objects_dir.resolve()
        if image_path.parent != objects_root or image_path.name != f"{commit_id}.png":
            raise ValueError("display image path escapes the object directory")
        pixel_hash = payload.get("pixel_hash")
        fingerprint = payload.get("hardware_fingerprint")
        committed_at = payload.get("committed_at")
        if not all(isinstance(value, str) and value for value in (pixel_hash, fingerprint, committed_at)):
            raise ValueError("display manifest metadata is incomplete")
        return DisplayCommit(
            commit_id=commit_id,
            image_path=image_path,
            pixel_hash=pixel_hash,
            hardware_fingerprint=fingerprint,
            logical_target=_frozen_mapping(payload.get("logical_target")),
            instance_revision=_revision(payload.get("instance_revision")),
            image_settings=_freeze(_json_detach(payload.get("image_settings", []))),
            hardware_written=bool(payload.get("hardware_written")),
            committed_at=committed_at,
        )

    def _validate_prepared(self, prepared: PreparedDisplay) -> None:
        expected = (self.objects_dir / f"{prepared.commit_id}.png").resolve()
        if prepared.image_path.resolve() != expected or not expected.is_file():
            raise ValueError("prepared display object is missing or outside the object directory")
        image = safe_open_image(expected)
        if compute_image_hash(image) != prepared.pixel_hash:
            raise ValueError("prepared display object hash changed before commit")

    def _has_newer_orphan(self, current_path: Path) -> bool:
        try:
            manifest_mtime = self.manifest_path.stat().st_mtime_ns
        except OSError:
            return True
        for candidate in self.objects_dir.glob("*.png"):
            if candidate == current_path:
                continue
            try:
                if candidate.stat().st_mtime_ns > manifest_mtime:
                    return True
            except OSError:
                return True
        return False

    def _publish_compatibility_image(self, image_path: Path) -> None:
        try:
            atomic_write_bytes(
                self.compatibility_image_path,
                image_path.read_bytes(),
                mode=0o600,
            )
        except OSError:
            logger.exception(
                "Display manifest committed, but compatibility image publication failed: %s",
                self.compatibility_image_path,
            )

    def _prune_objects(
        self,
        *,
        current_path: Path,
        allowance=None,
        dry_run=False,
        planning=None,
    ):
        if allowance is not None:
            if planning is None:
                planning = {
                    "baseline_entries": allowance.aggregate.deleted_entries,
                    "baseline_bytes": allowance.aggregate.deleted_bytes,
                    "entries": 0,
                    "bytes": 0,
                }
            return self._prune_objects_bounded(
                current_path=current_path,
                allowance=allowance,
                dry_run=dry_run,
                planning=planning,
            )

        candidates = []
        for path in self.objects_dir.glob("*.png"):
            try:
                candidates.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
        keep = {current_path}
        keep.update(path for _mtime, path in sorted(candidates, reverse=True)[:MAX_RETAINED_OBJECTS])
        for _mtime, path in candidates:
            if path in keep:
                continue
            try:
                path.unlink()
            except OSError:
                logger.warning("Could not prune display object: %s", path)
        return False

    def _prune_objects_bounded(
        self,
        *,
        current_path,
        allowance,
        dry_run,
        planning,
    ):
        aggregate = allowance.aggregate
        configured_root = Path(os.path.abspath(self.objects_dir))
        try:
            root_info = os.lstat(configured_root)
            if (
                not stat.S_ISDIR(root_info.st_mode)
                or stat.S_ISLNK(root_info.st_mode)
                or _is_reparse_point(root_info)
            ):
                aggregate.skipped_unsafe += 1
                allowance.mark_backlog()
                return True
            root = configured_root.resolve(strict=True)
        except FileNotFoundError:
            return False
        except OSError:
            aggregate.error_count += 1
            allowance.mark_backlog()
            return True

        candidates = []
        try:
            with os.scandir(root) as iterator:
                for entry in iterator:
                    if not allowance.consume_scan():
                        return True
                    path = root / entry.name
                    if path.suffix != ".png" or not _COMMIT_ID.fullmatch(path.stem):
                        continue
                    try:
                        info = os.lstat(path)
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or stat.S_ISLNK(info.st_mode)
                            or _is_reparse_point(info)
                            or path.resolve(strict=True).parent != root
                        ):
                            aggregate.skipped_unsafe += 1
                            continue
                    except FileNotFoundError:
                        continue
                    except OSError:
                        aggregate.error_count += 1
                        continue
                    candidates.append((int(info.st_mtime_ns), path.name, path, info))
        except OSError:
            aggregate.error_count += 1
            allowance.mark_backlog()
            return True

        normalized_current = Path(os.path.abspath(current_path))
        newest = sorted(candidates, reverse=True)[:MAX_RETAINED_OBJECTS]
        keep = {normalized_current}
        keep.update(path for _mtime, _name, path, _info in newest)
        removable = sorted(
            (candidate for candidate in candidates if candidate[2] not in keep),
            key=lambda candidate: (candidate[0], candidate[1]),
        )
        for _mtime, _name, path, info in removable:
            if not allowance.can_delete(0):
                return True
            size = max(0, int(info.st_size))
            if (
                planning["baseline_entries"] + planning["entries"]
                >= allowance.budget.max_deleted
                or planning["baseline_bytes"] + planning["bytes"] + size
                > allowance.budget.max_deleted_bytes
            ):
                allowance.mark_backlog()
                return True
            planning["entries"] += 1
            planning["bytes"] += size
            aggregate.candidate_entries += 1
            if dry_run:
                continue

            expected_token = _stat_token(info)
            try:
                current_info = os.lstat(path)
                if (
                    _stat_token(current_info) != expected_token
                    or not stat.S_ISREG(current_info.st_mode)
                    or stat.S_ISLNK(current_info.st_mode)
                    or _is_reparse_point(current_info)
                    or path.resolve(strict=True).parent != root
                ):
                    aggregate.skipped_unsafe += 1
                    allowance.mark_backlog()
                    continue
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                aggregate.error_count += 1
                allowance.mark_backlog()
                continue
            allowance.consume_delete(size)
        return False

    @staticmethod
    def _instance_uuid(logical_target: Mapping[str, object]) -> str | None:
        value = logical_target.get("instance_uuid")
        return value if isinstance(value, str) and value.strip() else None
