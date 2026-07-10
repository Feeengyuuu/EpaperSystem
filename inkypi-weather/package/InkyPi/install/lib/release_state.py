"""Durable release layout, update journal, and power-loss recovery decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time


JOURNAL_VERSION = 1
MAX_JOURNAL_BYTES = 1024 * 1024
RELEASE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class ReleaseStateError(RuntimeError):
    pass


class InvalidTransition(ReleaseStateError):
    pass


class UpdatePhase(str, Enum):
    CREATED = "created"
    DOWNLOADED = "downloaded"
    PREFLIGHTED = "preflighted"
    SWITCHED = "switched"
    STARTING = "starting"
    HEALTHY = "healthy"
    COMMITTED = "committed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"


class RecoveryAction(str, Enum):
    CLEAN_STAGING = "clean_staging"
    ROLL_BACK = "roll_back"
    FINISH_COMMIT = "finish_commit"
    NONE = "none"
    MANUAL_INTERVENTION = "manual_intervention"


_TRANSITIONS = {
    UpdatePhase.CREATED: frozenset({UpdatePhase.DOWNLOADED}),
    UpdatePhase.DOWNLOADED: frozenset({UpdatePhase.PREFLIGHTED}),
    UpdatePhase.PREFLIGHTED: frozenset({UpdatePhase.SWITCHED}),
    UpdatePhase.SWITCHED: frozenset(
        {UpdatePhase.STARTING, UpdatePhase.ROLLING_BACK}
    ),
    UpdatePhase.STARTING: frozenset(
        {UpdatePhase.HEALTHY, UpdatePhase.ROLLING_BACK}
    ),
    UpdatePhase.HEALTHY: frozenset({UpdatePhase.COMMITTED}),
    UpdatePhase.ROLLING_BACK: frozenset(
        {UpdatePhase.ROLLED_BACK, UpdatePhase.ROLLBACK_FAILED}
    ),
    UpdatePhase.COMMITTED: frozenset(),
    UpdatePhase.ROLLED_BACK: frozenset(),
    UpdatePhase.ROLLBACK_FAILED: frozenset(),
}


def validate_release_id(value) -> str:
    if not isinstance(value, str) or not RELEASE_ID_PATTERN.fullmatch(value):
        raise ValueError("release_id must contain only 1-64 safe characters")
    return value


@dataclass(frozen=True)
class ReleaseLayout:
    install_root: Path
    state_root: Path

    def __init__(self, install_root, state_root):
        object.__setattr__(self, "install_root", Path(install_root))
        object.__setattr__(self, "state_root", Path(state_root))

    @property
    def releases_dir(self) -> Path:
        return self.install_root / "releases"

    @property
    def staging_dir(self) -> Path:
        return self.install_root / "staging"

    @property
    def current_link(self) -> Path:
        return self.install_root / "current"

    @property
    def previous_link(self) -> Path:
        return self.install_root / "previous"

    @property
    def journal_path(self) -> Path:
        return self.state_root / "update-state.json"

    @property
    def backup_dir(self) -> Path:
        return self.state_root / "backups"

    @property
    def history_dir(self) -> Path:
        return self.state_root / "history"

    @property
    def lock_path(self) -> Path:
        return self.state_root / "update.lock"

    def release_path(self, release_id) -> Path:
        return self.releases_dir / validate_release_id(release_id)

    def staging_path(self, release_id) -> Path:
        return self.staging_dir / validate_release_id(release_id)

    def ensure(self) -> None:
        for directory, mode in (
            (self.install_root, 0o755),
            (self.releases_dir, 0o755),
            (self.staging_dir, 0o755),
            (self.state_root, 0o700),
            (self.backup_dir, 0o700),
            (self.history_dir, 0o700),
        ):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(directory, mode)
            except OSError:
                if os.name != "nt":
                    raise


class UpdateJournal:
    def __init__(self, path, document, *, clock=time.time):
        self.path = Path(path)
        self._document = document
        self._clock = clock
        self._lock = threading.RLock()

    @classmethod
    def create(
        cls,
        path,
        *,
        release_id,
        metadata=None,
        clock=time.time,
    ):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise ReleaseStateError(f"update journal already exists: {target}")
        now = _finite_timestamp(clock())
        document = {
            "version": JOURNAL_VERSION,
            "release_id": validate_release_id(release_id),
            "phase": UpdatePhase.CREATED.value,
            "created_at": now,
            "updated_at": now,
            "metadata": _json_mapping(metadata or {}),
            "history": [
                {
                    "phase": UpdatePhase.CREATED.value,
                    "at": now,
                }
            ],
        }
        _atomic_write_json(target, document)
        return cls(target, document, clock=clock)

    @classmethod
    def load(cls, path, *, clock=time.time):
        target = Path(path)
        if target.is_symlink():
            raise ReleaseStateError("update journal cannot be a symlink")
        try:
            if target.stat().st_size > MAX_JOURNAL_BYTES:
                raise ReleaseStateError("update journal is too large")
            document = json.loads(
                target.read_text(encoding="utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant {value}")
                ),
            )
        except ReleaseStateError:
            raise
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise ReleaseStateError("update journal is unreadable") from error
        cls._validate_document(document)
        return cls(target, document, clock=clock)

    @staticmethod
    def _validate_document(document) -> None:
        if not isinstance(document, dict) or document.get("version") != JOURNAL_VERSION:
            raise ReleaseStateError("update journal has an unsupported format")
        try:
            validate_release_id(document.get("release_id"))
            UpdatePhase(document.get("phase"))
            _finite_timestamp(document.get("created_at"))
            _finite_timestamp(document.get("updated_at"))
            _json_mapping(document.get("metadata"))
        except (TypeError, ValueError) as error:
            raise ReleaseStateError("update journal has invalid fields") from error
        history = document.get("history")
        if not isinstance(history, list) or not history or len(history) > 64:
            raise ReleaseStateError("update journal history is invalid")
        for entry in history:
            if not isinstance(entry, dict):
                raise ReleaseStateError("update journal history is invalid")
            try:
                UpdatePhase(entry.get("phase"))
                _finite_timestamp(entry.get("at"))
            except (TypeError, ValueError) as error:
                raise ReleaseStateError("update journal history is invalid") from error

    @property
    def release_id(self) -> str:
        return self._document["release_id"]

    @property
    def phase(self) -> UpdatePhase:
        return UpdatePhase(self._document["phase"])

    @property
    def metadata(self) -> dict:
        return json.loads(json.dumps(self._document["metadata"]))

    def update_metadata(self, **updates) -> None:
        with self._lock:
            metadata = dict(self._document["metadata"])
            metadata.update(_json_mapping(updates))
            updated = dict(self._document)
            updated["metadata"] = metadata
            updated["updated_at"] = _finite_timestamp(self._clock())
            _atomic_write_json(self.path, updated)
            self._document = updated

    def transition(self, phase, **metadata_updates) -> None:
        destination = UpdatePhase(phase)
        with self._lock:
            source = self.phase
            if destination not in _TRANSITIONS[source]:
                raise InvalidTransition(
                    f"update phase cannot move from {source.value} to {destination.value}"
                )
            now = _finite_timestamp(self._clock())
            updated = dict(self._document)
            metadata = dict(updated["metadata"])
            metadata.update(_json_mapping(metadata_updates))
            history = list(updated["history"])
            history.append({"phase": destination.value, "at": now})
            updated.update(
                phase=destination.value,
                updated_at=now,
                metadata=metadata,
                history=history,
            )
            _atomic_write_json(self.path, updated)
            self._document = updated

    def recovery_action(self) -> RecoveryAction:
        phase = self.phase
        if phase in {
            UpdatePhase.CREATED,
            UpdatePhase.DOWNLOADED,
            UpdatePhase.PREFLIGHTED,
        }:
            return RecoveryAction.CLEAN_STAGING
        if phase in {
            UpdatePhase.SWITCHED,
            UpdatePhase.STARTING,
            UpdatePhase.ROLLING_BACK,
        }:
            return RecoveryAction.ROLL_BACK
        if phase is UpdatePhase.HEALTHY:
            return RecoveryAction.FINISH_COMMIT
        if phase is UpdatePhase.ROLLBACK_FAILED:
            return RecoveryAction.MANUAL_INTERVENTION
        return RecoveryAction.NONE


def recover_incomplete_update(
    journal,
    *,
    clean_staging,
    roll_back,
    finish_commit,
) -> RecoveryAction:
    action = journal.recovery_action()
    if action is RecoveryAction.CLEAN_STAGING:
        clean_staging(journal)
    elif action is RecoveryAction.ROLL_BACK:
        roll_back(journal)
    elif action is RecoveryAction.FINISH_COMMIT:
        finish_commit(journal)
    elif action is RecoveryAction.MANUAL_INTERVENTION:
        raise ReleaseStateError(
            "the previous update rollback failed; manual intervention is required"
        )
    return action


def atomic_symlink(target, link) -> None:
    target_path = Path(target)
    link_path = Path(link)
    link_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = link_path.parent / (
        f".{link_path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    )
    try:
        os.symlink(target_path, temporary, target_is_directory=True)
        os.replace(temporary, link_path)
        fsync_directory(link_path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def fsync_directory(directory) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(Path(directory), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path, document) -> None:
    payload = (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        fsync_directory(target.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _finite_timestamp(value) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("timestamp must be finite and non-negative")
    return number


def _json_mapping(value) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError("journal metadata must be a string-keyed mapping")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise TypeError("journal metadata must be JSON serializable") from error
    if not isinstance(decoded, dict):
        raise TypeError("journal metadata must be a mapping")
    return decoded
