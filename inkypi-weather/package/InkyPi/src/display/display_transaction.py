"""Manifest-backed display commits that never publish intent before hardware."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import threading
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from utils.atomic_file import atomic_write_bytes, atomic_write_image, atomic_write_json
from utils.image_utils import compute_image_hash
from utils.safe_image import safe_open_image


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MANIFEST_NAME = "display_manifest.json"
OBJECTS_DIR_NAME = "objects"
MAX_RETAINED_OBJECTS = 8
_COMMIT_ID = re.compile(r"^[0-9a-f]{32}$")


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
            logger.warning("Ignoring invalid display manifest: %s", self.manifest_path, exc_info=True)
            return None

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

    def _prune_objects(self, *, current_path: Path) -> None:
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

    @staticmethod
    def _instance_uuid(logical_target: Mapping[str, object]) -> str | None:
        value = logical_target.get("instance_uuid")
        return value if isinstance(value, str) and value.strip() else None
