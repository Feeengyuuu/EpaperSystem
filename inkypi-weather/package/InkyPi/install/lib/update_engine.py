"""Artifact preparation and symmetric release activation/rollback engine."""

from __future__ import annotations

import csv
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
import zipfile

from release_archive import is_device_owned_yahei_font
from host_migration import TrustedHostMigrator
from release_state import (
    RecoveryAction,
    ReleaseLayout,
    ReleaseStateError,
    UpdatePhase,
    atomic_symlink,
    fsync_directory,
    recover_incomplete_update,
    validate_release_id,
)


MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
DEFAULT_DISK_RESERVE_BYTES = 256 * 1024 * 1024
BOOT_RECOVERY_UNIT_NAME = "inkypi-update-recover.service"
# The fixed boot recovery capability: (release-relative source, host destination, mode, whether it
# is a systemd unit or drop-in, so that changing it needs a daemon-reload).
BOOT_RECOVERY_CAPABILITY_FILES = (
    ("install/inkypi-update-recover.service", "/etc/systemd/system/inkypi-update-recover.service", 0o644, True),
    ("install/inkypi-update-finalize.service", "/etc/systemd/system/inkypi-update-finalize.service", 0o644, True),
    ("install/systemd/inkypi.service.d/10-update-recovery.conf",
     "/etc/systemd/system/inkypi.service.d/10-update-recovery.conf", 0o644, True),
    ("install/systemd/lightdm.service.d/10-inkypi-update-recovery.conf",
     "/etc/systemd/system/lightdm.service.d/10-inkypi-update-recovery.conf", 0o644, True),
    ("install/lib/release_state.py", "/usr/local/lib/inkypi-update/release_state.py", 0o644, False),
    ("install/lib/release_archive.py", "/usr/local/lib/inkypi-update/release_archive.py", 0o644, False),
    ("install/lib/host_migration.py", "/usr/local/lib/inkypi-update/host_migration.py", 0o644, False),
    ("install/lib/update_engine.py", "/usr/local/lib/inkypi-update/update_engine.py", 0o644, False),
)

# Reported by ``inkypi-update --print-capabilities``.  The delta code lives in
# this module on purpose: the installed library directory only ever receives
# this fixed set of files, so a new sibling module would not exist there for
# the first rounds after the engine that needs it was installed.
UPDATER_FEATURES = ("delta_v1", "timing_v1")
FULL_ARTIFACT_FORMAT = "full"
DELTA_ARTIFACT_FORMAT = "delta_v1"
DELTA_FORMAT_V1 = "inkypi-delta-v1"
DELTA_MANIFEST_MEMBER = "inkypi-delta.json"
# Two levels deep, so an older engine's release-root search never mistakes a
# delta payload for a complete package and refuses the artifact instead.
DELTA_PAYLOAD_PREFIX = "delta/files/"
MAX_DELTA_MANIFEST_BYTES = 16 * 1024 * 1024
# Hard links cost no data blocks.  New bytes come from the payload, the few
# copies whose mode differs, relocated venv scripts and RECORDs, vendor assets,
# preflight bytecode and directory blocks; a changed lock needs a fresh venv.
DELTA_OVERHEAD_BYTES = 128 * 1024 * 1024
FRESH_VENV_RESERVE_BYTES = 1024 * 1024 * 1024
DELTA_GENERATED_TOP_LEVEL = frozenset({"venv_inkypi", ".release-id", "cli"})
# Kept literally (not imported) so a half-installed library cannot fail to
# import; tests pin it to release_archive.MIGRATION_CONTROL_NAMES.
DELTA_MIGRATION_CONTROL_NAMES = frozenset(
    {
        ".release-migrations.json",
        ".nasapics-space-weather-v1.expectation.json",
        ".headless-mode-v1.expectation.json",
    }
)
DELTA_MODES = {"0644": 0o644, "0755": 0o755}
RELEASE_EXECUTABLES = (
    "install/inkypi",
    "install/inkypi-update",
    "install/preflight.py",
    "install/bootstrap_admin.py",
    "install/update_vendors.sh",
    "cli/inkypi-plugin",
)
# Windows test hosts have no POSIX modes; tests patch these three seams.
_MODES_ENFORCED = os.name != "nt"
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_VENDOR_PIN_PATTERN = re.compile(
    r'^\s*"[^"|]+\|https://[^"|\s]+\|([0-9a-f]{64})\|([A-Za-z0-9_./-]+)"\s*$'
)


class ArtifactError(RuntimeError):
    pass


class DeltaRefused(ArtifactError):
    """A delta artifact cannot be applied; nothing outside staging changed."""

    def __init__(self, message):
        super().__init__(f"delta_v1 refused: {message}")


class UpdateFailed(RuntimeError):
    pass


def print_timing(line) -> None:
    # A dropped SSH session must never turn a progress line into a failure.
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass


class PhaseTimer:
    """Emit one short ``timing: <phase> <seconds>s [key=value ...]`` line per phase."""

    def __init__(self, emit=None, clock=None):
        self.emit = emit or print_timing
        self.clock = clock or time.monotonic

    @contextmanager
    def phase(self, name):
        started = self.clock()
        note = {}
        outcome = " failed"
        try:
            yield note
            outcome = ""
        finally:
            details = "".join(f" {key}={value}" for key, value in note.items())
            elapsed = max(0.0, self.clock() - started)
            self.line(f"timing: {name} {elapsed:.1f}s{details}{outcome}")

    def line(self, text) -> None:
        try:
            self.emit(text)
        except Exception:
            pass


@dataclass(frozen=True)
class ArtifactInspection:
    path: Path
    sha256: str
    file_count: int
    uncompressed_bytes: int


@dataclass(frozen=True)
class ManagedFile:
    source_relative: str
    destination: Path
    mode: int

    def __init__(self, source_relative, destination, mode):
        relative = PurePosixPath(str(source_relative))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("managed file source must be release-relative")
        normalized_mode = int(mode)
        if not 0 <= normalized_mode <= 0o777:
            raise ValueError("managed file mode is invalid")
        object.__setattr__(self, "source_relative", relative.as_posix())
        object.__setattr__(self, "destination", Path(destination))
        object.__setattr__(self, "mode", normalized_mode)


@dataclass(frozen=True)
class DeltaPlan:
    """A validated delta manifest: the complete target package plus its payload."""

    base_commit: str
    base_release: str
    target_commit: str
    hashes: dict
    modes: dict
    removed: tuple
    changed: tuple
    payload_bytes: int


class FilesystemLinks:
    def read(self, link):
        path = Path(link)
        if path.is_symlink():
            target = Path(os.readlink(path))
            return target if target.is_absolute() else (path.parent / target).resolve()
        if path.exists():
            return path.resolve()
        return None

    def replace(self, target, link):
        path = Path(link)
        if path.exists() and not path.is_symlink():
            raise UpdateFailed(f"cannot replace non-symlink release path: {path}")
        atomic_symlink(Path(target), path)

    def remove(self, link):
        path = Path(link)
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            fsync_directory(path.parent)
        elif path.exists():
            raise UpdateFailed(f"refusing to remove release directory as a link: {path}")

    def is_legacy_directory(self, link) -> bool:
        path = Path(link)
        return path.exists() and path.is_dir() and not path.is_symlink()

    def migrate_legacy_directory(self, source, destination) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if not self.is_legacy_directory(source_path):
            return
        if destination_path.exists() or destination_path.is_symlink():
            raise UpdateFailed(f"legacy release destination already exists: {destination_path}")
        os.replace(source_path, destination_path)
        fsync_directory(source_path.parent)
        if destination_path.parent != source_path.parent:
            fsync_directory(destination_path.parent)


class UpdateCoordinator:
    """Switch one prepared release and restore every changed pointer on failure."""

    def __init__(
        self,
        layout: ReleaseLayout,
        service,
        *,
        links=None,
        managed_files=(),
        copy_file=None,
        fallback_release=None,
        host_migrator=None,
        progress=None,
    ):
        self.layout = layout
        self.service = service
        self.links = links or FilesystemLinks()
        self.managed_files = tuple(managed_files)
        self.copy_file = copy_file or atomic_copy_file
        self.timer = PhaseTimer(progress)
        # fallback_release is accepted and ignored.  Installed N-1 updaters
        # still pass the retired /usr/local/inkypi root, and boot recovery can
        # pair such a script with this library while managed files are half
        # installed or half restored.
        self.host_migrator = host_migrator or TrustedHostMigrator()

    def activate(self, journal, release_path) -> None:
        if journal.phase is not UpdatePhase.PREFLIGHTED:
            raise UpdateFailed("release activation requires a preflighted journal")
        target = Path(release_path).resolve()
        if not target.is_dir():
            raise UpdateFailed(f"prepared release does not exist: {target}")
        releases_root = self.layout.releases_dir.resolve()
        if target.parent != releases_root:
            raise UpdateFailed("prepared release is outside the managed release directory")
        host_migration = self.host_migrator.requested_migration(
            target,
            journal.release_id,
        )
        host_migration_snapshot = (
            self.host_migrator.capture_snapshot(host_migration)
            if host_migration is not None
            else None
        )

        previous = self.links.read(self.layout.current_link)
        legacy_source = None
        if getattr(self.links, "is_legacy_directory", lambda _path: False)(
            self.layout.current_link
        ):
            legacy_source = self.layout.current_link
            legacy_id = f"legacy-{journal.release_id[:40]}-{int(time.time())}"
            previous = self.layout.release_path(legacy_id)

        backups = self._capture_managed_file_backups(journal)
        service_was_active = bool(self.service.is_active())
        service_was_enabled = bool(
            getattr(self.service, "is_enabled", lambda: True)()
        )
        activation_metadata = dict(
            previous_target=str(previous) if previous is not None else None,
            target_path=str(target),
            legacy_source=str(legacy_source) if legacy_source else None,
            service_was_active=service_was_active,
            service_was_enabled=service_was_enabled,
            managed_backups=backups,
        )
        if host_migration is not None:
            activation_metadata["host_migration"] = {
                "migration": host_migration,
                "snapshot": host_migration_snapshot,
            }
        journal.update_metadata(**activation_metadata)
        journal.transition(UpdatePhase.SWITCHED)
        timer = self.timer
        activation_started = timer.clock()

        try:
            if service_was_active:
                with timer.phase("stop"):
                    self.service.stop()
            if host_migration is not None:
                journal.transition(UpdatePhase.APPLYING_HOST_MIGRATION)
                with timer.phase("host-migration"):
                    self.host_migrator.apply(
                        host_migration,
                        host_migration_snapshot,
                    )
            with timer.phase("switch"):
                if legacy_source is not None:
                    self.links.migrate_legacy_directory(legacy_source, previous)
                if previous is not None:
                    self.links.replace(previous, self.layout.previous_link)
                self.links.replace(target, self.layout.current_link)
                # systemd is reloaded (about 12 s on the device) only when a managed file changed.
                if self._install_managed_files(target):
                    self.service.daemon_reload()
                if not service_was_enabled:
                    self.service.enable()
            journal.transition(UpdatePhase.STARTING)
            with timer.phase("start"):
                self.service.start()
            with timer.phase("wait-ready") as note:
                ready = self.service.wait_ready(journal.release_id)
                note["ready"] = "yes" if ready else "no"
            if not ready:
                raise UpdateFailed(
                    f"target release {journal.release_id} did not become ready"
                )
            journal.transition(UpdatePhase.HEALTHY)
        except BaseException as error:
            try:
                with timer.phase("rollback"):
                    self.rollback(journal)
            except BaseException as rollback_error:
                raise UpdateFailed(
                    f"release activation failed and rollback failed: {rollback_error}"
                ) from error
            raise UpdateFailed(
                f"release activation failed and was rolled back: {error}"
            ) from error

        try:
            journal.transition(UpdatePhase.COMMITTED)
        except BaseException as error:
            raise UpdateFailed(
                "target release is healthy but commit recording is pending recovery"
            ) from error
        self.cleanup_backups(journal)
        elapsed = max(0.0, timer.clock() - activation_started)
        timer.line(f"timing: activation {elapsed:.1f}s")

    def rollback(self, journal, *, defer_service_starts=False) -> None:
        if journal.phase in {
            UpdatePhase.SWITCHED,
            UpdatePhase.STARTING,
            UpdatePhase.APPLYING_HOST_MIGRATION,
            UpdatePhase.HEALTHY,
        }:
            journal.transition(UpdatePhase.ROLLING_BACK)
        elif journal.phase is UpdatePhase.ROLLBACK_PENDING_SERVICES:
            journal.transition(UpdatePhase.ROLLING_BACK)
        elif journal.phase is not UpdatePhase.ROLLING_BACK:
            raise UpdateFailed(
                f"cannot roll back update in phase {journal.phase.value}"
            )

        try:
            metadata = journal.metadata
            # A candidate may already be running when readiness or commit work
            # fails.  Stop it before restoring the desktop so the constrained
            # host never runs both memory-heavy stacks during rollback.
            if defer_service_starts or self.service.is_active():
                self.service.stop()
            host_migration = metadata.get("host_migration")
            if host_migration is not None:
                if not isinstance(host_migration, dict) or set(host_migration) != {
                    "migration",
                    "snapshot",
                }:
                    raise UpdateFailed("host migration rollback metadata is invalid")
                if defer_service_starts:
                    self.host_migrator.restore_for_boot(
                        host_migration["migration"],
                        host_migration["snapshot"],
                    )
                else:
                    self.host_migrator.restore(
                        host_migration["migration"],
                        host_migration["snapshot"],
                    )
            previous_raw = metadata.get("previous_target")
            previous = Path(previous_raw) if previous_raw else None
            legacy_source = metadata.get("legacy_source")
            if previous is not None and previous.exists():
                self.links.replace(previous, self.layout.current_link)
            elif previous is not None and not legacy_source:
                # Test and virtual link implementations do not require a real path.
                self.links.replace(previous, self.layout.current_link)
            elif previous is None:
                self.links.remove(self.layout.current_link)
            elif not Path(legacy_source).exists():
                raise UpdateFailed("previous release is unavailable during rollback")
            if not metadata.get("service_was_enabled", True):
                disable = getattr(self.service, "disable", None)
                if callable(disable):
                    disable()
            self._restore_managed_files(metadata.get("managed_backups", []))
            self.service.daemon_reload()
            if metadata.get("service_was_enabled", True):
                enable = getattr(self.service, "enable", None)
                if callable(enable):
                    enable()
            if metadata.get("service_was_active"):
                if defer_service_starts:
                    self.service.start_deferred()
                else:
                    self.service.start()
                    self._wait_for_previous_release(metadata)
            if defer_service_starts:
                journal.transition(UpdatePhase.ROLLBACK_PENDING_SERVICES)
            else:
                journal.transition(UpdatePhase.ROLLED_BACK)
                self.cleanup_backups(journal)
        except BaseException as error:
            if journal.phase is UpdatePhase.ROLLING_BACK:
                # Host restoration is idempotent.  A transient systemd or I/O
                # failure must remain boot-retryable instead of freezing a
                # partially restored host in manual-intervention state.
                try:
                    metadata = journal.metadata
                    journal.update_metadata(
                        rollback_failure_count=(
                            int(metadata.get("rollback_failure_count", 0)) + 1
                        ),
                        rollback_failure_type=type(error).__name__[:128],
                        rollback_failure_message=str(error)[:512],
                    )
                except BaseException:
                    pass
            raise

    def _wait_for_previous_release(self, metadata) -> None:
        previous_raw = metadata.get("previous_target")
        if not previous_raw:
            raise UpdateFailed("restored release identity is unavailable")
        previous = Path(previous_raw)
        legacy_source = metadata.get("legacy_source")
        if not previous.exists() and legacy_source:
            previous = Path(legacy_source)
        try:
            with (previous / ".release-id").open(encoding="utf-8") as identity:
                release_id = validate_release_id(identity.readline(66).rstrip("\r\n"))
        except (OSError, UnicodeError, ValueError) as error:
            raise UpdateFailed("restored release identity is unavailable or invalid") from error
        if not self.service.wait_ready(release_id):
            raise UpdateFailed("restored release did not become ready")

    def recover(self, journal, *, defer_service_starts=False):
        # Readiness alone is not the commit point for a host mutation.  If the
        # power fails after HEALTHY was persisted but before COMMITTED, boot
        # must restore the recorded graphical/LightDM snapshot exactly.
        if (
            journal.phase is UpdatePhase.HEALTHY
            and journal.metadata.get("host_migration") is not None
        ):
            self.rollback(
                journal,
                defer_service_starts=defer_service_starts,
            )
            return RecoveryAction.ROLL_BACK
        return recover_incomplete_update(
            journal,
            clean_staging=self._clean_staging,
            roll_back=lambda candidate: self.rollback(
                candidate,
                defer_service_starts=defer_service_starts,
            ),
            finish_commit=lambda candidate: candidate.transition(
                UpdatePhase.COMMITTED
            ),
        )

    def finalize_boot_recovery(self, journal) -> None:
        """Commit rollback only after all deferred service states are exact."""

        if journal.phase is not UpdatePhase.ROLLBACK_PENDING_SERVICES:
            raise UpdateFailed("boot recovery is not awaiting service finalization")
        metadata = journal.metadata
        host_migration = metadata.get("host_migration")
        if host_migration is not None:
            if not isinstance(host_migration, dict) or set(host_migration) != {
                "migration",
                "snapshot",
            }:
                raise UpdateFailed("host migration rollback metadata is invalid")
            if not self.host_migrator.snapshot_matches(
                host_migration["migration"],
                host_migration["snapshot"],
            ):
                raise UpdateFailed("restored host state does not match its snapshot")
        if bool(self.service.is_enabled()) != bool(
            metadata.get("service_was_enabled", True)
        ):
            raise UpdateFailed("restored InkyPi enabled state does not match")
        if bool(self.service.is_active()) != bool(
            metadata.get("service_was_active", False)
        ):
            raise UpdateFailed("restored InkyPi active state does not match")
        if metadata.get("service_was_active"):
            self._wait_for_previous_release(metadata)
        journal.transition(UpdatePhase.ROLLED_BACK)
        self.cleanup_backups(journal)

    def _capture_managed_file_backups(self, journal):
        backup_root = self.layout.backup_dir / journal.release_id
        if backup_root.exists():
            _safe_remove_tree(backup_root, self.layout.backup_dir)
        backup_root.mkdir(parents=True, mode=0o700)
        records = []
        for index, managed in enumerate(self.managed_files):
            destination = managed.destination
            if destination.is_symlink():
                raise UpdateFailed(f"managed destination cannot be a symlink: {destination}")
            backup = backup_root / f"{index:03d}.bak"
            existed = destination.is_file()
            if existed:
                atomic_copy_file(destination, backup, 0o600)
            records.append(
                {
                    "destination": str(destination),
                    "backup": str(backup) if existed else None,
                    "existed": existed,
                    "mode": (
                        stat.S_IMODE(destination.stat().st_mode)
                        if existed
                        else managed.mode
                    ),
                }
            )
        fsync_directory(backup_root)
        return records

    def cleanup_backups(self, journal) -> bool:
        backup_root = self.layout.backup_dir / journal.release_id
        try:
            if backup_root.exists():
                _safe_remove_tree(backup_root, self.layout.backup_dir)
        except (OSError, ReleaseStateError):
            return False
        return True

    def cleanup_candidate(self, journal) -> None:
        self._clean_staging(journal)

    def _install_managed_files(self, release_path) -> bool:
        """Install the release's managed host files; True when any had to be rewritten (one already
        matching byte for byte, mode included, is left alone)."""
        changed = False
        for managed in self.managed_files:
            source = release_path.joinpath(*PurePosixPath(managed.source_relative).parts)
            if not source.is_file() or source.is_symlink():
                raise UpdateFailed(f"managed release file is missing: {source}")
            if files_identical(source, managed.destination, managed.mode):
                continue
            self.copy_file(source, managed.destination, managed.mode)
            changed = True
        return changed

    def _restore_managed_files(self, records):
        records = tuple(records or ())
        allowed_destinations = {
            _absolute_without_resolving(managed.destination)
            for managed in self.managed_files
        }
        observed_destinations = set()
        backup_root = _absolute_without_resolving(self.layout.backup_dir)
        for record in records:
            if not isinstance(record, dict):
                raise UpdateFailed("managed file backup record is invalid")
            if type(record.get("existed")) is not bool:
                raise UpdateFailed("managed file backup existence flag is invalid")
            try:
                mode = int(record.get("mode"))
            except (TypeError, ValueError) as error:
                raise UpdateFailed("managed file backup mode is invalid") from error
            if not 0 <= mode <= 0o777:
                raise UpdateFailed("managed file backup mode is invalid")
            destination = _absolute_without_resolving(record.get("destination", ""))
            if (
                destination not in allowed_destinations
                or destination in observed_destinations
            ):
                raise UpdateFailed("managed file backup destination is not allowlisted")
            observed_destinations.add(destination)
            if record.get("existed"):
                backup = _absolute_without_resolving(record.get("backup", ""))
                try:
                    backup.relative_to(backup_root)
                except ValueError as error:
                    raise UpdateFailed(
                        "managed file backup is outside the update state root"
                    ) from error
                if backup == backup_root:
                    raise UpdateFailed("managed file backup path is invalid")
        for record in reversed(records):
            destination = Path(record["destination"])
            if record.get("existed"):
                backup = Path(record["backup"])
                if not backup.is_file():
                    raise UpdateFailed(f"managed file backup is missing: {backup}")
                self.copy_file(backup, destination, int(record["mode"]))
            elif destination.exists() or destination.is_symlink():
                if destination.is_dir() and not destination.is_symlink():
                    raise UpdateFailed(
                        f"refusing to remove managed directory: {destination}"
                    )
                destination.unlink()
                fsync_directory(destination.parent)

    def _clean_staging(self, journal):
        metadata = journal.metadata
        for key, allowed_root in (
            ("staging_path", self.layout.staging_dir),
            ("target_path", self.layout.releases_dir),
        ):
            raw = metadata.get(key)
            if not raw:
                continue
            candidate = Path(raw)
            current = self.links.read(self.layout.current_link)
            if current is not None and candidate.resolve() == Path(current).resolve():
                continue
            if candidate.exists():
                _safe_remove_tree(candidate, allowed_root)
        self.cleanup_backups(journal)


class ArtifactPreparer:
    """Build and preflight a candidate release without touching live pointers."""

    def __init__(
        self,
        layout,
        *,
        config_path="/var/lib/inkypi/config/device.json",
        python_executable=sys.executable,
        run_command=None,
        disk_checker=None,
        links=None,
        updater_capabilities=(),
        artifact_format=FULL_ARTIFACT_FORMAT,
        progress=None,
    ):
        self.layout = layout
        self.config_path = Path(config_path)
        self.python_executable = str(python_executable)
        self.run_command = run_command or _run_checked
        self.disk_checker = disk_checker
        self.links = links or FilesystemLinks()
        self.updater_capabilities = tuple(
            capability
            for capability in updater_capabilities
            if capability == "headless_mode_v1"
        )
        if artifact_format not in {FULL_ARTIFACT_FORMAT, DELTA_ARTIFACT_FORMAT}:
            raise ArtifactError(f"unsupported artifact format: {artifact_format}")
        self.artifact_format = artifact_format
        self._timer = PhaseTimer(progress)

    def _try_clone_current_venv(
        self,
        requirements,
        destination,
        *,
        published_destination=None,
    ) -> bool:
        """Clone a compatible current venv as hard links, copy-on-write.

        Every file keeps the current release's inode until something must
        change it; relocation then writes a new file and renames it into
        place, so the live and previous releases are never modified.

        The payload is hashed once, in venv-check.  The finalize probe skips
        the inodes the clone still shares with that verified venv and hashes
        only what relocation wrote, so the live venv's metadata must stay
        unchanged from before that hash until finalize ends.
        """

        with self._timer.phase("venv-check") as note:
            reusable = self._reusable_current_venv(requirements, destination)
            note["reuse"] = "no" if reusable is None else "yes"
        if reusable is None:
            return False
        source_venv, source_snapshot = reusable
        destination_venv = Path(destination)
        with self._timer.phase("venv-clone") as note:
            note.update(_link_tree(source_venv, destination_venv))
        if _venv_metadata_digest(source_venv) != source_snapshot:
            raise ArtifactError("current virtual environment changed while it was checked or copied")
        if _venv_metadata_digest(destination_venv) != source_snapshot:
            raise ArtifactError("candidate virtual environment copy is incomplete")
        with self._timer.phase("venv-finalize") as note:
            note["relocated"] = self._finalize_candidate_venv(
                destination_venv,
                Path(requirements),
                published_destination=published_destination,
                cwd=destination_venv,
                environment_prevalidated=True,
                verified_source=source_venv,
            )
            if _venv_metadata_digest(source_venv) != source_snapshot:
                raise ArtifactError("current virtual environment changed while the clone was verified")
        return True

    def _reusable_current_venv(self, requirements, destination):
        """Return ``(venv, metadata digest)`` of the current release when it may be reused.

        The digest is taken before the payload is hashed, so any later change
        to the venv shows up when the caller compares it again.
        """

        source = self.links.read(self.layout.current_link)
        if source is None:
            return None
        candidate_requirements = Path(requirements)
        destination_venv = Path(destination)
        try:
            releases_root = self.layout.releases_dir.resolve(strict=True)
            current_release = Path(source).resolve(strict=True)
        except OSError:
            return None
        if current_release.parent != releases_root:
            return None

        source_requirements = current_release / "install" / "requirements.txt"
        source_venv = current_release / "venv_inkypi"
        source_python = source_venv / "bin" / "python"
        source_site_packages = (
            source_venv
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        regular_inputs = (source_requirements, candidate_requirements)
        if any(path.is_symlink() or not path.is_file() for path in regular_inputs):
            return None
        if not source_python.is_file():
            return None
        if source_venv.is_symlink() or not source_venv.is_dir():
            return None
        if not source_site_packages.is_dir() or source_site_packages.is_symlink():
            return None
        if destination_venv.exists() or destination_venv.is_symlink():
            raise ArtifactError("candidate release already contains a virtual environment")
        try:
            source_stat = source_requirements.stat()
            candidate_stat = candidate_requirements.stat()
            locks_match = source_stat.st_size == candidate_stat.st_size and hmac.compare_digest(
                _sha256_file(source_requirements),
                _sha256_file(candidate_requirements),
            )
        except OSError:
            return None
        if (
            not locks_match
            or not _venv_uses_trusted_python(source_venv, self.python_executable)
            or _venv_has_unsafe_symlink(
                source_venv,
                self.layout.install_root,
                self.python_executable,
            )
            or _venv_has_unsafe_path_file(source_venv)
        ):
            return None

        locked_versions = _locked_distribution_versions(candidate_requirements)
        snapshot = _venv_metadata_digest(source_venv)
        try:
            self._probe_venv_environment(
                source_venv,
                locked_versions,
                cwd=current_release,
            )
            self._run_isolated_pip_check(
                source_venv,
                cwd=current_release,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        return source_venv, snapshot

    def _probe_venv_environment(
        self,
        venv,
        locked_versions,
        *,
        cwd,
        verified_source=None,
    ) -> None:
        candidate_venv = Path(venv)
        python = candidate_venv / "bin" / "python"
        site_packages = (
            candidate_venv
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        expected_abi = (
            sys.implementation.name,
            sys.implementation.cache_tag,
            sys.version_info[:2],
            platform.machine(),
            sysconfig.get_config_var("SOABI"),
        )
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.run_command(
            [
                str(python),
                "-I",
                "-S",
                "-c",
                _venv_environment_probe(
                    candidate_venv,
                    site_packages,
                    expected_abi,
                    locked_versions,
                    verified_source=verified_source,
                ),
            ],
            cwd=cwd,
            timeout=300,
            env=environment,
        )

    def _run_isolated_pip_check(self, venv, *, cwd) -> None:
        candidate_venv = Path(venv)
        python = candidate_venv / "bin" / "python"
        site_packages = (
            candidate_venv
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.run_command(
            [
                str(python),
                "-I",
                "-S",
                "-c",
                _isolated_pip_check_probe(site_packages),
            ],
            cwd=cwd,
            timeout=120,
            env=environment,
        )

    def _finalize_candidate_venv(
        self,
        venv,
        requirements,
        *,
        published_destination=None,
        cwd,
        environment_prevalidated=False,
        verified_source=None,
    ) -> int:
        candidate_venv = Path(venv)
        published_venv = (
            candidate_venv
            if published_destination is None
            else Path(published_destination)
        )
        if (
            not _venv_uses_trusted_python(candidate_venv, self.python_executable)
            or _venv_has_unsafe_symlink(
                candidate_venv,
                self.layout.install_root,
                self.python_executable,
            )
            or _venv_has_unsafe_path_file(candidate_venv)
        ):
            raise ArtifactError("candidate virtual environment is not self-contained")

        locked_versions = _locked_distribution_versions(requirements)
        if not environment_prevalidated:
            self._probe_venv_environment(
                candidate_venv,
                locked_versions,
                cwd=cwd,
            )
        _normalize_venv_python_links(candidate_venv, self.python_executable)
        relocated = _relocate_venv_paths(
            candidate_venv,
            published_venv,
            install_root=self.layout.install_root,
        )
        if (
            not _venv_uses_trusted_python(candidate_venv, self.python_executable)
            or _venv_has_unsafe_symlink(
                candidate_venv,
                self.layout.install_root,
                self.python_executable,
            )
            or _venv_has_unsafe_path_file(candidate_venv)
        ):
            raise ArtifactError("candidate virtual environment relocation is unsafe")
        self._probe_venv_environment(
            candidate_venv,
            locked_versions,
            cwd=cwd,
            verified_source=verified_source,
        )
        self._run_isolated_pip_check(candidate_venv, cwd=cwd)
        return relocated

    def prepare(self, inspection, release_id, journal):
        # Resolve the default lazily because ensure_disk_reserve is defined below.
        disk_checker = self.disk_checker or ensure_disk_reserve
        self.layout.ensure()
        staging = self.layout.staging_path(release_id)
        target = self.layout.release_path(release_id)
        if target.exists() or target.is_symlink():
            raise ArtifactError(f"target release already exists: {target}")
        if staging.exists() or staging.is_symlink():
            _safe_remove_tree(staging, self.layout.staging_dir)
        journal.update_metadata(staging_path=str(staging), target_path=str(target))
        candidate = staging / "release"
        if self.artifact_format == DELTA_ARTIFACT_FORMAT:
            plan = read_delta_manifest(inspection)
            base = self._delta_base_release(plan)
            journal.update_metadata(
                artifact_format=DELTA_ARTIFACT_FORMAT,
                delta_base_release=plan.base_release,
                delta_changed_files=len(plan.changed),
                delta_removed_files=len(plan.removed),
            )
            disk_checker(self.layout.install_root, delta_disk_requirement(plan, base))
            staging.mkdir(parents=True, mode=0o755)
            # Every check of the delta happens here, before any other step
            # runs against the candidate and long before a pointer changes.
            with self._timer.phase("delta-apply") as note:
                note.update(apply_delta(inspection, plan, base, candidate))
            with self._timer.phase("delta-verify") as note:
                trusted = load_verified_files(self.layout, plan.base_release)
                record = {}
                note["files"] = verify_package_tree(candidate, plan, trusted=trusted, record=record)
                note["trusted"] = sum(1 for name, known in record.items() if trusted.get(name) == known)
            save_verified_files(self.layout, release_id, record)
        else:
            if _zip_has_member(inspection.path, DELTA_MANIFEST_MEMBER):
                raise ArtifactError("a delta artifact needs --artifact-format delta_v1")
            required = max(
                inspection.uncompressed_bytes * 3,
                inspection.uncompressed_bytes + 1024 * 1024 * 1024,
            )
            disk_checker(self.layout.install_root, required)
            staging.mkdir(parents=True, mode=0o755)
            with self._timer.phase("extract") as note:
                archive_root = safe_extract_zip(inspection, staging / "archive")
                package_root = find_release_root(archive_root)
                os.replace(package_root, candidate)
                if archive_root.exists():
                    shutil.rmtree(archive_root)
                note["files"] = inspection.file_count
        self._complete_candidate(candidate, staging, target, release_id)
        with self._timer.phase("publish"):
            os.replace(candidate, target)
            fsync_directory(self.layout.releases_dir)
            shutil.rmtree(staging)
            fsync_directory(self.layout.staging_dir)
        return target

    def _complete_candidate(self, candidate, staging, target, release_id) -> None:
        """Generated files, vendors, venv and preflight; identical for both formats.

        Delta candidates share unchanged inodes with the live release, so no
        step here may modify an existing file in place: every write creates a
        new file and renames it over the old name.
        """

        if not (candidate / "cli").exists() and (candidate / "install" / "cli").is_dir():
            shutil.copytree(candidate / "install" / "cli", candidate / "cli")
        _atomic_write_text(candidate / ".release-id", f"{release_id}\n", 0o644)
        for relative in RELEASE_EXECUTABLES:
            executable = candidate.joinpath(*PurePosixPath(relative).parts)
            if executable.is_file():
                _ensure_mode_copy_on_write(executable, 0o755)

        vendor_script = candidate / "install" / "update_vendors.sh"
        with self._timer.phase("vendors") as note:
            note["seeded"] = self._seed_vendor_assets(candidate)
            self.run_command(
                ["bash", str(vendor_script)],
                cwd=candidate,
                timeout=180,
            )
        venv = candidate / "venv_inkypi"
        requirements = candidate / "install" / "requirements.txt"
        reused_venv = self._try_clone_current_venv(
            requirements,
            venv,
            published_destination=target / "venv_inkypi",
        )
        if not reused_venv:
            with self._timer.phase("venv-pip"):
                self.run_command(
                    [self.python_executable, "-m", "venv", str(venv)],
                    cwd=candidate,
                    timeout=180,
                )
                venv_python = venv / "bin" / "python"
                with tempfile.TemporaryDirectory(prefix="pip-", dir=staging) as pip_tmpdir:
                    pip_environment = os.environ.copy()
                    pip_environment["TMPDIR"] = pip_tmpdir
                    self.run_command(
                        [
                            str(venv_python),
                            "-m",
                            "pip",
                            "install",
                            "--require-hashes",
                            "--no-deps",
                            "--no-compile",
                            "--disable-pip-version-check",
                            "-r",
                            str(requirements),
                        ],
                        cwd=candidate,
                        timeout=1200,
                        env=pip_environment,
                    )
            with self._timer.phase("venv-finalize") as note:
                note["relocated"] = self._finalize_candidate_venv(
                    venv,
                    requirements,
                    published_destination=target / "venv_inkypi",
                    cwd=candidate,
                )
        venv_python = venv / "bin" / "python"
        config_source = self.config_path
        if not config_source.is_file():
            config_source = candidate / "install" / "config_base" / "device.json"
        preflight_command = [
                str(venv_python),
                str(candidate / "install" / "preflight.py"),
                "--release-root",
                str(candidate),
                "--config",
                str(config_source),
                "--release-id",
                release_id,
            ]
        for capability in self.updater_capabilities:
            preflight_command.extend(("--updater-capability", capability))
        with self._timer.phase("preflight"):
            self.run_command(
                preflight_command,
                cwd=candidate,
                timeout=600,
            )

        with self._timer.phase("fsync"):
            _fsync_tree(candidate)

    def _current_release_root(self):
        source = self.links.read(self.layout.current_link)
        if source is None:
            return None
        try:
            releases_root = self.layout.releases_dir.resolve(strict=True)
            current_release = Path(source).resolve(strict=True)
        except OSError:
            return None
        if current_release.parent != releases_root or not current_release.is_dir():
            return None
        return current_release

    def _delta_base_release(self, plan):
        """The active release, which must be exactly the delta's base release."""

        base = self._current_release_root()
        if base is None:
            raise DeltaRefused("no active release to apply the delta to")
        if base.name != plan.base_release:
            raise DeltaRefused(
                f"active release {base.name} is not the delta base {plan.base_release}"
            )
        identity = base / ".release-id"
        try:
            if identity.is_symlink() or not identity.is_file():
                raise DeltaRefused("active release has no release identity")
            with identity.open(encoding="utf-8") as stream:
                recorded = stream.readline(66).rstrip("\r\n")
        except (OSError, UnicodeError) as error:
            raise DeltaRefused("active release identity cannot be read") from error
        if recorded != plan.base_release:
            raise DeltaRefused("active release identity does not match the delta base")
        return base

    def _seed_vendor_assets(self, candidate) -> int:
        """Reuse pinned vendor files the live release already holds.

        update_vendors.sh then verifies their pinned hashes instead of
        downloading; any file that is absent or differs is still downloaded.
        """

        source = self._current_release_root()
        if source is None:
            return 0
        seeded = 0
        for relative, expected in _vendor_pins(candidate / "install" / "update_vendors.sh"):
            parts = PurePosixPath(relative).parts
            destination = candidate.joinpath(*parts)
            created = False
            try:
                if os.path.lexists(destination) or not _real_directory_chain(candidate, parts[:-1]):
                    continue
                origin = _regular_file_below(source, parts)
                if origin is None or not hmac.compare_digest(_sha256_file(origin), expected):
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                _link_or_copy(origin, destination)
                created = True
                _ensure_mode_copy_on_write(destination, 0o644)
            except (OSError, ArtifactError):
                # Seeding only saves a download; the vendor script still fetches it.
                if created:
                    destination.unlink(missing_ok=True)
                continue
            seeded += 1
        return seeded

    def ensure_bootstrap_token(self, release_path) -> None:
        release = Path(release_path)
        self.run_command(
            [
                str(release / "venv_inkypi" / "bin" / "python"),
                str(release / "install" / "bootstrap_admin.py"),
                "ensure-bootstrap",
            ],
            cwd=release,
            timeout=30,
        )


class SystemdService:
    def __init__(
        self,
        *,
        service_name="inkypi.service",
        systemctl="/usr/bin/systemctl",
        health_url="http://127.0.0.1/readyz",
        health_timeout_seconds=120,
        runner=subprocess.run,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.service_name = service_name
        self.systemctl = systemctl
        self.health_url = health_url
        self.health_timeout_seconds = max(1.0, float(health_timeout_seconds))
        self._runner = runner
        self._clock = clock
        self._sleep = sleep
        self._opener = build_opener(ProxyHandler({}))

    def is_active(self) -> bool:
        result = self._runner(
            [self.systemctl, "is-active", "--quiet", self.service_name],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    def is_enabled(self) -> bool:
        result = self._runner(
            [self.systemctl, "is-enabled", "--quiet", self.service_name],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    def stop(self) -> None:
        self._systemctl("stop", self.service_name)

    def start(self) -> None:
        self._systemctl("start", self.service_name)

    def start_deferred(self) -> None:
        self._systemctl("--no-block", "start", self.service_name)

    def enable(self) -> None:
        self._systemctl("enable", self.service_name)

    def disable(self) -> None:
        self._systemctl("disable", self.service_name)

    def daemon_reload(self) -> None:
        self._systemctl("daemon-reload")

    def wait_ready(self, release_id) -> bool:
        deadline = self._clock() + self.health_timeout_seconds
        while self._clock() < deadline:
            try:
                with self._opener.open(self.health_url, timeout=3) as response:
                    payload = response.read(64 * 1024 + 1)
                    if len(payload) > 64 * 1024:
                        raise ValueError("readyz response is too large")
                    body = json.loads(payload.decode("utf-8"))
                    if (
                        response.status == 200
                        and body.get("release_id") == release_id
                        and body.get("status") in {"ready", "degraded"}
                    ):
                        return True
            except (HTTPError, URLError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
                pass
            self._sleep(min(2.0, max(0.05, deadline - self._clock())))
        return False

    def _systemctl(self, *arguments) -> None:
        self._runner(
            [self.systemctl, *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )


class BootRecoveryUnitInstaller:
    """Atomically install and enable the fixed boot recovery oneshot."""

    def __init__(
        self,
        *,
        unit_target="/etc/systemd/system/inkypi-update-recover.service",
        systemctl="/usr/bin/systemctl",
        runner=None,
        copy_file=None,
        capability_files=None,
    ):
        self.unit_target = Path(unit_target)
        self.systemctl = str(systemctl)
        self._runner = runner or subprocess.run
        self._copy_file = copy_file or atomic_copy_file
        # (release-relative source, destination, mode, is a systemd unit or drop-in); the
        # recovery unit itself goes to ``unit_target``.
        self.capability_files = tuple(
            (relative, Path(destination), mode, unit)
            for relative, destination, mode, unit in (capability_files or tuple(
                (relative, self.unit_target if relative == "install/" + BOOT_RECOVERY_UNIT_NAME else destination,
                 mode, unit)
                for relative, destination, mode, unit in BOOT_RECOVERY_CAPABILITY_FILES))
        )

    def install(self, source) -> None:
        self._copy_file(source, self.unit_target, 0o644)
        self._systemctl("daemon-reload")
        # `enable` is intentionally unconditional: it is idempotent and makes
        # a partially completed bootstrap converge on every update attempt.
        self._systemctl("enable", BOOT_RECOVERY_UNIT_NAME)
        self._systemctl("--no-block", "start", BOOT_RECOVERY_UNIT_NAME)

    def install_capability(self, release_root) -> None:
        """Install every fixed recovery file before any host migration.

        A file already in place byte for byte (and mode) is left alone, and systemd is reloaded
        only when a unit or drop-in changed: each reload takes about 12 s on a Pi Zero 2 W. The
        `enable` calls never reload again (``--no-reload``); a new unit was loaded just before."""

        root = Path(release_root)
        units_changed = False
        for relative, destination, mode, unit in self.capability_files:
            source = root.joinpath(*PurePosixPath(relative).parts)
            if not source.is_file() or source.is_symlink():
                raise UpdateFailed(f"boot recovery capability file is missing: {source}")
            if files_identical(source, destination, mode):
                continue
            self._copy_file(source, destination, mode)
            units_changed = units_changed or unit
        if units_changed:
            self._systemctl("daemon-reload")
        self._systemctl("--no-reload", "enable", BOOT_RECOVERY_UNIT_NAME)
        # Queue recovery while the updater still owns the shared lock.  The
        # oneshot itself waits for that lock, and the caller releases it before
        # joining the systemd job with wait_ready().
        self._systemctl("--no-block", "start", BOOT_RECOVERY_UNIT_NAME)
        self._systemctl("--no-reload", "enable", "inkypi-update-finalize.service")

    def wait_ready(self) -> None:
        """Join the queued oneshot and propagate a fail-closed unit failure."""

        self._systemctl("start", BOOT_RECOVERY_UNIT_NAME)

    def _systemctl(self, *arguments) -> None:
        self._runner(
            [self.systemctl, *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )


class UpdateLock:
    def __init__(self, path, *, blocking=False):
        self.path = Path(path)
        self.blocking = bool(blocking)
        self._descriptor = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                os.ftruncate(descriptor, 1)
                os.lseek(descriptor, 0, os.SEEK_SET)
                operation = msvcrt.LK_LOCK if self.blocking else msvcrt.LK_NBLCK
                msvcrt.locking(descriptor, operation, 1)
            else:
                import fcntl

                operation = fcntl.LOCK_EX
                if not self.blocking:
                    operation |= fcntl.LOCK_NB
                fcntl.flock(descriptor, operation)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            raise UpdateFailed("another InkyPi update is already running") from None
        self._descriptor = descriptor
        return self

    def __exit__(self, *_args):
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def archive_journal(layout, journal, *, keep=20) -> Path:
    layout.history_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.time_ns()
    destination = layout.history_dir / (
        f"{stamp}-{journal.release_id}-{journal.phase.value}.json"
    )
    os.replace(journal.path, destination)
    fsync_directory(layout.history_dir)
    histories = sorted(
        layout.history_dir.glob("*.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in histories[max(1, int(keep)) :]:
        stale.unlink(missing_ok=True)
    fsync_directory(layout.history_dir)
    return destination


def prune_releases(layout, links=None, *, keep=2) -> tuple[Path, ...]:
    _ensure_no_active_journal(layout)
    install_root, _install_identity = _validated_directory_chain(
        layout.install_root,
        label="install root",
    )
    releases_root, _releases_identity = _validated_descendant_directory(
        layout.releases_dir,
        install_root,
        label="releases root",
    )
    link_manager = links or FilesystemLinks()
    preserved = set()
    for index, link in enumerate((layout.current_link, layout.previous_link)):
        target = link_manager.read(link)
        if target is None:
            if index == 0:
                raise ReleaseStateError("current release link target is missing")
            continue
        validated_target, _target_identity = _validated_descendant_directory(
            target,
            releases_root,
            label="release link target",
        )
        if validated_target.parent != releases_root:
            raise ReleaseStateError(
                f"release link target must be a direct child: {validated_target}"
            )
        preserved.add(validated_target)

    releases_with_mtime = []
    try:
        entries = tuple(releases_root.iterdir())
    except OSError as error:
        raise ReleaseStateError("releases root cannot be enumerated safely") from error
    for path in entries:
        try:
            path_stat = os.lstat(path)
        except OSError as error:
            raise ReleaseStateError(f"release child cannot be inspected: {path}") from error
        if stat.S_ISLNK(path_stat.st_mode):
            raise ReleaseStateError(f"release child cannot be a symlink: {path}")
        if stat.S_ISDIR(path_stat.st_mode):
            releases_with_mtime.append((path, path_stat.st_mtime_ns))
    releases = tuple(
        path
        for path, _mtime in sorted(
            releases_with_mtime,
            key=lambda item: item[1],
            reverse=True,
        )
    )
    retained = set(preserved)
    retain_count = max(2, int(keep))
    for release in releases:
        if len(retained) >= retain_count:
            break
        retained.add(release)
    removed = []
    for release in releases:
        if release in retained:
            continue
        _ensure_no_active_journal(layout)
        _safe_remove_tree(release, releases_root)
        removed.append(release)
    return tuple(removed)


def inspect_artifact(path, expected_sha256) -> ArtifactInspection:
    artifact = Path(path)
    expected = str(expected_sha256 or "").strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise ArtifactError("expected SHA256 must contain 64 hexadecimal characters")
    if not artifact.is_file() or artifact.is_symlink():
        raise ArtifactError(f"release artifact is not a regular file: {artifact}")
    if artifact.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ArtifactError("compressed release artifact is too large")
    actual = _sha256_file(artifact)
    if not hmac.compare_digest(expected, actual):
        raise ArtifactError("release artifact SHA256 does not match")

    total = 0
    count = 0
    try:
        with zipfile.ZipFile(artifact) as archive:
            for info in archive.infolist():
                relative = _validated_archive_path(info)
                if is_device_owned_yahei_font(relative):
                    raise ArtifactError(
                        "release archive cannot contain device-owned YaHei font binaries"
                    )
                count += 1
                if count > MAX_ARCHIVE_FILES:
                    raise ArtifactError("release archive contains too many files")
                if info.file_size > MAX_ARCHIVE_FILE_BYTES:
                    raise ArtifactError("release archive contains an oversized file")
                total += info.file_size
                if total > MAX_ARCHIVE_BYTES:
                    raise ArtifactError("release archive is too large when extracted")
                compressed = max(1, info.compress_size)
                if info.file_size > 1024 * 1024 and info.file_size / compressed > MAX_COMPRESSION_RATIO:
                    raise ArtifactError("release archive has an unsafe compression ratio")
    except ArtifactError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise ArtifactError("release artifact is not a readable ZIP archive") from error
    return ArtifactInspection(artifact, actual, count, total)


def ensure_disk_reserve(
    directory,
    required_bytes,
    *,
    reserve_bytes=DEFAULT_DISK_RESERVE_BYTES,
    disk_usage=shutil.disk_usage,
) -> None:
    available = int(disk_usage(Path(directory)).free)
    required = int(required_bytes) + int(reserve_bytes)
    if available < required:
        raise ArtifactError(
            f"insufficient disk space: require {required} bytes, have {available}"
        )


def safe_extract_zip(inspection, destination) -> Path:
    destination_path = Path(destination)
    if destination_path.exists() or destination_path.is_symlink():
        raise ArtifactError(f"release extraction destination already exists: {destination_path}")
    # Re-read the artifact immediately before extraction to close inspection TOCTOU.
    current = _sha256_file(inspection.path)
    if not hmac.compare_digest(current, inspection.sha256):
        raise ArtifactError("release artifact changed after SHA256 verification")
    destination_path.mkdir(parents=True, mode=0o755)
    try:
        with zipfile.ZipFile(inspection.path) as archive:
            for info in archive.infolist():
                relative = _validated_archive_path(info)
                target = destination_path.joinpath(*relative.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("xb") as output:
                    copied = 0
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > info.file_size or copied > MAX_ARCHIVE_FILE_BYTES:
                            raise ArtifactError("archive member exceeded its declared size")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if copied != info.file_size:
                    raise ArtifactError("archive member size did not match its declaration")
                executable = bool((info.external_attr >> 16) & 0o111)
                try:
                    os.chmod(target, 0o755 if executable else 0o644)
                except OSError:
                    if os.name != "nt":
                        raise
        fsync_directory(destination_path)
        return destination_path
    except BaseException:
        shutil.rmtree(destination_path, ignore_errors=True)
        raise


def find_release_root(extracted_root) -> Path:
    root = Path(extracted_root)
    candidates = []
    for candidate in (
        root,
        root / "inkypi-weather" / "package" / "InkyPi",
    ):
        if _is_release_root(candidate):
            candidates.append(candidate)
    if not candidates:
        for entry in root.iterdir():
            if entry.is_dir() and _is_release_root(entry):
                candidates.append(entry)
    unique = tuple(dict.fromkeys(candidate.resolve() for candidate in candidates))
    if len(unique) != 1:
        raise ArtifactError("release archive must contain exactly one InkyPi package root")
    return unique[0]


_DELTA_REQUIRED_KEYS = frozenset(
    {
        "format",
        "base_commit",
        "base_release",
        "target_commit",
        "hashes",
        "modes",
        "removed",
        "changed",
    }
)
_DELTA_OPTIONAL_KEYS = frozenset({"full_artifact_sha256"})


def read_delta_manifest(inspection) -> DeltaPlan:
    """Read and validate the manifest of an inspected delta artifact.

    The manifest is covered by the artifact SHA256 the caller verified, and
    it lists the complete target package, so the candidate can be proven
    file by file against it after it is built.
    """

    current = _sha256_file(inspection.path)
    if not hmac.compare_digest(current, inspection.sha256):
        raise ArtifactError("release artifact changed after SHA256 verification")
    try:
        with zipfile.ZipFile(inspection.path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(set(names)) != len(names):
                raise DeltaRefused("the artifact repeats an archive member")
            manifests = [info for info in infos if info.filename == DELTA_MANIFEST_MEMBER]
            if len(manifests) != 1:
                raise DeltaRefused("the artifact has no delta manifest")
            if manifests[0].file_size > MAX_DELTA_MANIFEST_BYTES:
                raise DeltaRefused("the delta manifest is too large")
            with archive.open(manifests[0]) as stream:
                raw = stream.read(MAX_DELTA_MANIFEST_BYTES + 1)
            payload = {}
            for info in infos:
                if info.filename == DELTA_MANIFEST_MEMBER:
                    continue
                if info.is_dir() or not info.filename.startswith(DELTA_PAYLOAD_PREFIX):
                    raise DeltaRefused(f"unexpected archive member {info.filename}")
                payload[info.filename[len(DELTA_PAYLOAD_PREFIX):]] = info.file_size
    except ArtifactError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise DeltaRefused("the artifact is not a readable ZIP archive") from error
    if len(raw) > MAX_DELTA_MANIFEST_BYTES:
        raise DeltaRefused("the delta manifest is too large")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_json_object)
    except (UnicodeError, ValueError) as error:
        raise DeltaRefused("the delta manifest is not strict JSON") from error
    return _validated_delta_plan(document, payload)


def _validated_delta_plan(document, payload) -> DeltaPlan:
    if not isinstance(document, dict):
        raise DeltaRefused("the delta manifest must be a JSON object")
    keys = set(document)
    if not _DELTA_REQUIRED_KEYS <= keys or keys - _DELTA_REQUIRED_KEYS - _DELTA_OPTIONAL_KEYS:
        raise DeltaRefused("the delta manifest has missing or unexpected keys")
    if document["format"] != DELTA_FORMAT_V1:
        raise DeltaRefused("unsupported delta format")
    for key in ("base_commit", "target_commit"):
        if not isinstance(document[key], str) or not _COMMIT_PATTERN.fullmatch(document[key]):
            raise DeltaRefused(f"{key} is not a full commit id")
    try:
        base_release = validate_release_id(document["base_release"])
    except ValueError as error:
        raise DeltaRefused("base_release is not a release id") from error
    full_sha256 = document.get("full_artifact_sha256")
    if full_sha256 is not None and (
        not isinstance(full_sha256, str) or not _SHA256_PATTERN.fullmatch(full_sha256)
    ):
        raise DeltaRefused("full_artifact_sha256 is malformed")
    hashes = document["hashes"]
    if not isinstance(hashes, dict) or not hashes or len(hashes) > MAX_ARCHIVE_FILES:
        raise DeltaRefused("the target hash map is empty or too large")
    for name, digest in hashes.items():
        _validated_delta_path(name)
        if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
            raise DeltaRefused(f"invalid sha256 for {name}")
    directories = {
        parent.as_posix()
        for name in hashes
        for parent in PurePosixPath(name).parents
        if parent.as_posix() != "."
    }
    clashes = directories & set(hashes)
    if clashes:
        raise DeltaRefused(f"{sorted(clashes)[0]} is both a file and a directory")
    modes = document["modes"]
    if (
        not isinstance(modes, dict)
        or set(modes) != set(hashes)
        or any(not isinstance(value, str) or value not in DELTA_MODES for value in modes.values())
    ):
        raise DeltaRefused("the mode map must give 0644 or 0755 for every target file")
    removed = _validated_delta_path_list(document["removed"], "removed")
    if set(removed) & set(hashes):
        raise DeltaRefused("a removed path is still part of the target")
    changed = _validated_delta_path_list(document["changed"], "changed")
    if not set(changed) <= set(hashes):
        raise DeltaRefused("a changed path is not part of the target")
    if set(changed) != set(payload):
        raise DeltaRefused("the delta payload does not match its manifest")
    return DeltaPlan(
        base_commit=document["base_commit"],
        base_release=base_release,
        target_commit=document["target_commit"],
        hashes=dict(hashes),
        modes={name: DELTA_MODES[value] for name, value in modes.items()},
        removed=tuple(removed),
        changed=tuple(changed),
        payload_bytes=sum(payload.values()),
    )


def _validated_delta_path_list(value, label) -> list:
    if not isinstance(value, list) or len(value) > MAX_ARCHIVE_FILES:
        raise DeltaRefused(f"{label} must be a bounded list of paths")
    for name in value:
        _validated_delta_path(name)
    if len(set(value)) != len(value):
        raise DeltaRefused(f"{label} repeats a path")
    return value


def _validated_delta_path(name) -> PurePosixPath:
    """A package-relative POSIX path a delta may name (never a generated file)."""

    if (
        not isinstance(name, str)
        or not name
        or len(name) > 1024
        or "\\" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise DeltaRefused("unsafe package path")
    parts = name.split("/")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise DeltaRefused(f"unsafe package path {name!r}")
    if (
        parts[0] in DELTA_GENERATED_TOP_LEVEL
        or "__pycache__" in parts
        or path.suffix == ".pyc"
        or path.name in DELTA_MIGRATION_CONTROL_NAMES
        or is_device_owned_yahei_font(path)
    ):
        raise DeltaRefused(f"{name} is not a package file a delta may carry")
    return path


def delta_disk_requirement(plan, base) -> int:
    """Bytes a delta install writes: payload and overhead, plus a venv if the lock changed."""

    required = plan.payload_bytes * 2 + DELTA_OVERHEAD_BYTES
    lock = Path(base) / "install" / "requirements.txt"
    try:
        lock_matches = (
            not lock.is_symlink()
            and lock.is_file()
            and hmac.compare_digest(
                _sha256_file(lock),
                plan.hashes.get("install/requirements.txt", ""),
            )
        )
    except OSError:
        lock_matches = False
    if not lock_matches:
        required += FRESH_VENV_RESERVE_BYTES
    return required


def apply_delta(inspection, plan, base, candidate) -> dict:
    """Build the candidate package from the base release plus the delta payload.

    Unchanged files become hard links to the base release's inodes (a copy
    when linking is impossible or the mode differs); changed files are new
    files written from the payload; removed files are simply not created.
    Nothing outside ``candidate`` is written and no existing entry is ever
    followed or replaced: every destination is created exclusively.
    """

    current = _sha256_file(inspection.path)
    if not hmac.compare_digest(current, inspection.sha256):
        raise ArtifactError("release artifact changed after SHA256 verification")
    root = Path(candidate)
    root.mkdir(mode=0o755)
    counts = {"linked": 0, "written": 0, "copied": 0, "removed": len(plan.removed)}
    changed = set(plan.changed)
    checked_directories = set()
    try:
        with zipfile.ZipFile(inspection.path) as archive:
            payload = {
                info.filename[len(DELTA_PAYLOAD_PREFIX):]: info
                for info in archive.infolist()
                if info.filename.startswith(DELTA_PAYLOAD_PREFIX)
            }
            if set(payload) != changed:
                raise DeltaRefused("the delta payload does not match its manifest")
            for name in sorted(plan.hashes):
                parts = PurePosixPath(name).parts
                destination = root.joinpath(*parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                mode = plan.modes[name]
                if name in changed:
                    _write_delta_member(archive, payload[name], destination, plan.hashes[name])
                    _set_mode(destination, mode)
                    counts["written"] += 1
                    continue
                source = _base_package_file(base, parts, checked_directories)
                if _MODES_ENFORCED and _file_mode(source) != mode:
                    _copy_to_new_file(source, destination, mode)
                    counts["copied"] += 1
                elif _link_or_copy(source, destination) == "linked":
                    counts["linked"] += 1
                else:
                    _set_mode(destination, mode)
                    counts["copied"] += 1
        fsync_directory(root)
    except ArtifactError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise DeltaRefused(f"the candidate cannot be built: {error}") from error
    return counts


def verify_package_tree(root, plan, *, trusted=None, record=None) -> int:
    """Prove the candidate holds exactly the target package: names, hashes and modes.

    ``trusted`` is the base release's record from its own verification (load_verified_files): a
    file whose inode, size and mtime match that record, and whose recorded hash is the target's,
    is the very file verified then (a hard link to it) and is not read again; any change to it
    since moves its mtime. Every other file is hashed. ``record`` (a dict) receives each
    verified file's [dev, ino, size, mtime_ns, sha256] for the next round."""

    root_path = Path(root)
    seen = set()
    try:
        for current, directories, files in os.walk(root_path, onerror=_raise_walk_error):
            current_path = Path(current)
            for name in directories:
                if os.path.islink(current_path / name):
                    raise DeltaRefused(f"symlink {name} in the candidate")
            for name in files:
                path = current_path / name
                relative = path.relative_to(root_path).as_posix()
                expected = plan.hashes.get(relative)
                if expected is None:
                    raise DeltaRefused(f"unlisted file {relative} in the candidate")
                status = os.lstat(path)
                if not stat.S_ISREG(status.st_mode):
                    raise DeltaRefused(f"{relative} is not a regular file")
                fingerprint = [status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns]
                known = (trusted or {}).get(relative)
                if not (isinstance(known, list) and known[:4] == fingerprint and len(known) == 5
                        and hmac.compare_digest(str(known[4]), expected)):
                    if not hmac.compare_digest(_sha256_file(path), expected):
                        raise DeltaRefused(f"{relative} does not match its target sha256")
                if record is not None:
                    record[relative] = [*fingerprint, expected]
                if _MODES_ENFORCED and _file_mode(path) != plan.modes[relative]:
                    raise DeltaRefused(f"{relative} does not have its target mode")
                seen.add(relative)
    except ArtifactError:
        raise
    except OSError as error:
        raise DeltaRefused(f"the candidate cannot be verified: {error}") from error
    missing = set(plan.hashes) - seen
    if missing:
        raise DeltaRefused(f"{len(missing)} target files are missing, e.g. {sorted(missing)[0]}")
    if not _is_release_root(root_path):
        raise DeltaRefused("the candidate is not a complete InkyPi package")
    return len(seen)


def _verified_files_dir(layout) -> Path:
    return Path(layout.state_root) / "verified"


def save_verified_files(layout, release_id, record) -> None:
    """Keep ``release_id``'s verification record (verify_package_tree's ``record``) for the next
    delta, and drop the records of releases no longer on disk."""
    directory = _verified_files_dir(layout)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{validate_release_id(release_id)}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, target)
    on_disk = {entry.name for entry in Path(layout.releases_dir).iterdir()} if Path(layout.releases_dir).is_dir() else set()
    for stale in directory.glob("*.json"):
        if stale.stem not in on_disk and stale != target:
            stale.unlink(missing_ok=True)


def load_verified_files(layout, release_id) -> dict:
    """``release_id``'s verification record, or {} (none kept, or unreadable)."""
    try:
        target = _verified_files_dir(layout) / f"{validate_release_id(release_id)}.json"
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, ArtifactError, RuntimeError):
        return {}
    return record if isinstance(record, dict) else {}


def _write_delta_member(archive, info, destination, expected_sha256) -> None:
    digest = hashlib.sha256()
    copied = 0
    with archive.open(info, "r") as source, Path(destination).open("xb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > info.file_size or copied > MAX_ARCHIVE_FILE_BYTES:
                raise DeltaRefused(f"{info.filename} exceeded its declared size")
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if copied != info.file_size:
        raise DeltaRefused(f"{info.filename} does not match its declared size")
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        raise DeltaRefused(f"{info.filename} does not match its target sha256")


def _base_package_file(base, parts, checked_directories) -> Path:
    """A regular file of the base release reached through real directories only."""

    name = "/".join(parts)
    current = Path(base)
    for index, part in enumerate(parts[:-1]):
        current = current / part
        key = parts[: index + 1]
        if key in checked_directories:
            continue
        try:
            info = os.lstat(current)
        except FileNotFoundError as error:
            raise DeltaRefused(f"the base release lacks {name}") from error
        if not stat.S_ISDIR(info.st_mode):
            raise DeltaRefused(f"{'/'.join(key)} in the base release is not a directory")
        checked_directories.add(key)
    source = current / parts[-1]
    try:
        info = os.lstat(source)
    except FileNotFoundError as error:
        raise DeltaRefused(f"the base release lacks {name}") from error
    if not stat.S_ISREG(info.st_mode):
        raise DeltaRefused(f"{name} in the base release is not a regular file")
    return source


def _regular_file_below(root, parts):
    try:
        return _base_package_file(root, parts, set())
    except ArtifactError:
        return None


def _real_directory_chain(root, parts) -> bool:
    current = Path(root)
    for part in parts:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return True
        if not stat.S_ISDIR(info.st_mode):
            return False
    return True


def _vendor_pins(script) -> tuple:
    """(relative path, sha256) of every pinned asset in an update_vendors.sh."""

    path = Path(script)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
            return ()
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ()
    pins = []
    for line in lines:
        match = _VENDOR_PIN_PATTERN.match(line)
        if match is None:
            continue
        relative = match.group(2)
        if relative.startswith("/") or any(part in {"", ".", ".."} for part in relative.split("/")):
            continue
        if relative.split("/")[0] in DELTA_GENERATED_TOP_LEVEL:
            continue
        pins.append((relative, match.group(1)))
    return tuple(pins)


def _link_tree(source, destination) -> dict:
    """Recreate ``source`` at ``destination``; regular files share their inodes.

    Symlinks are recreated as symlinks (never followed), directories are new
    and get the source's modes, and any other file type is refused.
    """

    source_root = Path(source)
    destination_root = Path(destination)
    counts = {"linked": 0, "copied": 0}
    destination_root.mkdir(mode=0o700)
    directory_modes = [(destination_root, stat.S_IMODE(os.lstat(source_root).st_mode))]
    for current, directories, files in os.walk(source_root, onerror=_raise_walk_error):
        current_path = Path(current)
        target_directory = destination_root.joinpath(*current_path.relative_to(source_root).parts)
        descend = []
        for name in sorted(directories):
            origin = current_path / name
            info = os.lstat(origin)
            if stat.S_ISLNK(info.st_mode):
                os.symlink(os.readlink(origin), target_directory / name, target_is_directory=True)
            elif stat.S_ISDIR(info.st_mode):
                (target_directory / name).mkdir(mode=0o700)
                directory_modes.append((target_directory / name, stat.S_IMODE(info.st_mode)))
                descend.append(name)
            else:
                raise ArtifactError(f"virtual environment contains an unsupported entry: {origin}")
        directories[:] = descend
        for name in sorted(files):
            origin = current_path / name
            info = os.lstat(origin)
            if stat.S_ISLNK(info.st_mode):
                os.symlink(os.readlink(origin), target_directory / name)
            elif stat.S_ISREG(info.st_mode):
                counts[_link_or_copy(origin, target_directory / name)] += 1
            else:
                raise ArtifactError(f"virtual environment contains an unsupported file: {origin}")
    if os.name != "nt":
        for path, mode in reversed(directory_modes):
            os.chmod(path, mode)
    return counts


def _link_or_copy(source, destination) -> str:
    """Hard-link a regular file, or copy it to a new file when linking fails."""

    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        _copy_to_new_file(source, destination, stat.S_IMODE(os.lstat(source).st_mode))
        return "copied"
    return "linked"


def _zip_has_member(path, name) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return any(info.filename == name for info in archive.infolist())
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise ArtifactError("release artifact is not a readable ZIP archive") from error


def _raise_walk_error(error) -> None:
    raise error


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def files_identical(source, destination, mode) -> bool:
    """True when ``destination`` is already a regular file with ``source``'s bytes (and ``mode``)."""
    target = Path(destination)
    try:
        if target.is_symlink() or not target.is_file():
            return False
        if os.name != "nt" and stat.S_IMODE(target.stat().st_mode) != int(mode):
            return False
        if target.stat().st_size != Path(source).stat().st_size:
            return False
        with Path(source).open("rb") as left, target.open("rb") as right:
            while True:
                chunk = left.read(1024 * 1024)
                if chunk != right.read(1024 * 1024):
                    return False
                if not chunk:
                    return True
    except OSError:
        return False


def atomic_copy_file(source, destination, mode) -> None:
    source_path = Path(source)
    target = Path(destination)
    if not source_path.is_file() or source_path.is_symlink():
        raise UpdateFailed(f"managed source is not a regular file: {source_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, int(mode))
        with source_path.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            shutil.copyfileobj(input_stream, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        if os.name != "nt":
            os.chmod(target, int(mode))
        fsync_directory(target.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validated_archive_path(info) -> PurePosixPath:
    name = info.filename
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or "\x00" in name
        or any(ord(character) < 32 for character in name)
    ):
        raise ArtifactError("unsafe archive path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
        raise ArtifactError("unsafe archive path")
    file_type = (info.external_attr >> 16) & stat.S_IFMT(0o170000)
    if file_type == stat.S_IFLNK:
        raise ArtifactError("release archive cannot contain symbolic links")
    if info.flag_bits & 0x1:
        raise ArtifactError("release archive cannot contain encrypted files")
    return path


def _is_release_root(path) -> bool:
    candidate = Path(path)
    return all(
        required.is_file()
        for required in (
            candidate / "src" / "inkypi.py",
            candidate / "install" / "inkypi.service",
            candidate / "install" / "inkypi",
            candidate / "install" / "inkypi-update",
        )
    )


def _ensure_no_active_journal(layout) -> None:
    if os.path.lexists(layout.journal_path):
        raise ReleaseStateError(
            f"refusing to prune releases while update journal is active: "
            f"{layout.journal_path}"
        )


def _absolute_without_resolving(path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _directory_identity(path_stat) -> tuple[int, int, int]:
    return (
        int(path_stat.st_dev),
        int(path_stat.st_ino),
        stat.S_IFMT(path_stat.st_mode),
    )


def _validated_directory_chain(path, *, label) -> tuple[Path, tuple]:
    candidate = _absolute_without_resolving(path)
    chain = [candidate]
    while chain[-1].parent != chain[-1]:
        chain.append(chain[-1].parent)
    identities = []
    for member in reversed(chain):
        try:
            member_stat = os.lstat(member)
        except OSError as error:
            raise ReleaseStateError(f"{label} cannot be inspected: {member}") from error
        if stat.S_ISLNK(member_stat.st_mode):
            raise ReleaseStateError(f"{label} cannot contain a symlink: {member}")
        if not stat.S_ISDIR(member_stat.st_mode):
            raise ReleaseStateError(f"{label} must be a directory: {member}")
        identities.append((member, _directory_identity(member_stat)))
    return candidate, tuple(identities)


def _validated_descendant_directory(path, root, *, label) -> tuple[Path, tuple]:
    candidate = _absolute_without_resolving(path)
    managed_root = _absolute_without_resolving(root)
    try:
        relative = candidate.relative_to(managed_root)
    except ValueError as error:
        raise ReleaseStateError(
            f"{label} is outside managed root: {candidate}"
        ) from error
    if not relative.parts:
        raise ReleaseStateError(f"{label} cannot be the managed root: {candidate}")

    _root, root_identities = _validated_directory_chain(
        managed_root,
        label="managed root",
    )
    identities = list(root_identities)
    member = managed_root
    for part in relative.parts:
        member /= part
        try:
            member_stat = os.lstat(member)
        except OSError as error:
            raise ReleaseStateError(f"{label} cannot be inspected: {member}") from error
        if stat.S_ISLNK(member_stat.st_mode):
            raise ReleaseStateError(f"{label} cannot contain a symlink: {member}")
        if not stat.S_ISDIR(member_stat.st_mode):
            raise ReleaseStateError(f"{label} must be a directory: {member}")
        identities.append((member, _directory_identity(member_stat)))

    try:
        resolved_root = managed_root.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise ReleaseStateError(f"{label} cannot be resolved safely: {candidate}") from error
    if resolved_candidate == resolved_root or resolved_root not in resolved_candidate.parents:
        raise ReleaseStateError(f"{label} is outside managed root: {candidate}")
    return candidate, tuple(identities)


def _safe_remove_tree(path, allowed_root) -> None:
    candidate, identities = _validated_descendant_directory(
        path,
        allowed_root,
        label="removal candidate",
    )
    rechecked_candidate, rechecked_identities = _validated_descendant_directory(
        candidate,
        allowed_root,
        label="removal candidate",
    )
    if rechecked_candidate != candidate or rechecked_identities != identities:
        raise ReleaseStateError(
            f"refusing to remove path changed during validation: {candidate}"
        )
    shutil.rmtree(candidate)
    fsync_directory(candidate.parent)


def _locked_distribution_versions(requirements) -> dict[str, str]:
    pins = {}
    try:
        lines = Path(requirements).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ArtifactError("dependency lock cannot be read") from error
    current_pin = None
    pin_pattern = re.compile(
        r"^([A-Za-z0-9_.-]+)==([^ \\;]+)(?:\s*\\)?\s*$"
    )
    hash_pattern = re.compile(r"^--hash=sha256:[0-9a-f]{64}(?:\s*\\)?$")
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        match = pin_pattern.fullmatch(value)
        if match is None:
            if current_pin is not None and hash_pattern.fullmatch(value):
                continue
            raise ArtifactError("dependency lock uses unsupported syntax")
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        version = match.group(2)
        if name in pins:
            raise ArtifactError(f"dependency lock contains a duplicate pin: {name}")
        pins[name] = version
        current_pin = name
    if not pins:
        raise ArtifactError("dependency lock contains no pinned distributions")
    return pins


def _venv_environment_probe(
    venv,
    site_packages,
    expected_abi,
    locked_versions,
    *,
    verified_source=None,
) -> str:
    """Source of the probe that checks the venv's ABI, pins and RECORD hashes.

    With ``verified_source`` (a venv this run already hashed in full), a file
    that is the very same inode as the file at its path in that venv is not
    read again; everything else is hashed.  The caller must prove the source
    stayed unchanged from before that hash until after this probe.
    """

    source = None if verified_source is None else str(Path(verified_source))
    return f"""
from importlib import metadata
from pathlib import Path
import base64
import hashlib
import os
import platform
import re
import stat
import sys
import sysconfig

expected_abi = {expected_abi!r}
actual_abi = (
    sys.implementation.name,
    sys.implementation.cache_tag,
    sys.version_info[:2],
    platform.machine(),
    sysconfig.get_config_var("SOABI"),
)
assert actual_abi == expected_abi, (actual_abi, expected_abi)

venv = Path({str(venv)!r}).resolve(strict=True)
site_packages = Path({str(site_packages)!r}).resolve(strict=True)
assert site_packages == venv or venv in site_packages.parents
normalize = lambda value: re.sub(r"[-_.]+", "-", value).lower()
distributions = tuple(metadata.distributions(path=[str(site_packages)]))
pairs = tuple(
    (normalize(item.metadata.get("Name") or ""), item.version)
    for item in distributions
)
assert all(name for name, _version in pairs)
installed = dict(pairs)
assert len(installed) == len(pairs)
locked = {locked_versions!r}
assert all(installed.get(name) == version for name, version in locked.items())
assert set(installed) - set(locked) <= {{"pip"}}

verified_source = {source!r}
real_directories = {{}}


def resolve_strict(path):
    # Path.resolve(strict=True), resolving each directory only once.
    parent, name = os.path.split(path)
    if name in {{"", ".", ".."}}:
        return os.path.realpath(path, strict=True)
    real_parent = real_directories.get(parent)
    if real_parent is None:
        real_parent = real_directories[parent] = os.path.realpath(parent, strict=True)
    candidate = os.path.join(real_parent, name)
    if os.path.islink(candidate):
        return os.path.realpath(candidate, strict=True)
    os.lstat(candidate)
    return candidate


def shares_verified_inode(installed_file):
    if verified_source is None:
        return False
    try:
        reference = os.lstat(os.path.join(verified_source, installed_file[len(venv_prefix):]))
    except FileNotFoundError:
        return False
    current = os.lstat(installed_file)
    return stat.S_ISREG(reference.st_mode) and (reference.st_dev, reference.st_ino) == (
        current.st_dev,
        current.st_ino,
    )


# Plain strings: on the device pathlib's parents/relative_to cost more than the I/O.
venv_text = os.fspath(venv)
venv_prefix = os.path.join(venv_text, "")
site_packages_text = os.fspath(site_packages)
documentation_names = {{"copying", "license", "notice"}}
for distribution in distributions:
    files = distribution.files
    assert files is not None
    for item in files:
        if item.hash is None:
            continue
        assert item.hash.mode == "sha256"
        installed_file = resolve_strict(os.fspath(distribution.locate_file(item)))
        assert installed_file == venv_text or installed_file.startswith(venv_prefix)
        directory, name = os.path.split(installed_file)
        if directory == site_packages_text and (
            os.path.splitext(name)[1].lower() in {{".md", ".rst"}}
            or name.lower() in documentation_names
        ):
            continue
        if shares_verified_inode(installed_file):
            continue
        with open(installed_file, "rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").digest()
        actual_hash = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert actual_hash == item.hash.value
"""


def _isolated_pip_check_probe(site_packages) -> str:
    return f"""
from importlib import metadata
import re
import sys

sys.path.insert(0, {str(site_packages)!r})
from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement

normalize = lambda value: re.sub(r"[-_.]+", "-", value).lower()
distributions = tuple(metadata.distributions(path=[{str(site_packages)!r}]))
installed = {{
    normalize(item.metadata["Name"]): item.version
    for item in distributions
}}
environment = default_environment()
environment["extra"] = ""
dependency_errors = []
for distribution in distributions:
    for raw_requirement in distribution.requires or ():
        requirement = Requirement(raw_requirement)
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        installed_version = installed.get(normalize(requirement.name))
        if installed_version is None:
            dependency_errors.append(f"{{requirement.name}} is missing")
        elif requirement.specifier and installed_version not in requirement.specifier:
            dependency_errors.append(
                f"{{requirement.name}} {{installed_version}} does not satisfy "
                f"{{requirement.specifier}}"
            )

if dependency_errors:
    print("\\n".join(dependency_errors), file=sys.stderr)
    raise SystemExit(1)
"""


def _venv_metadata_digest(root) -> str:
    """Fingerprint a venv tree's names, modes, sizes, mtimes and link targets.

    Only compared within one run, never stored.  Built from plain strings and
    one ``lstat`` per entry: the pathlib/JSON version took ~10 s per call on
    the device for a venv of about eleven thousand entries.
    """

    base = os.fspath(root)
    lines = []
    pending = [""]
    while pending:
        relative_directory = pending.pop()
        directory = os.path.join(base, relative_directory) if relative_directory else base
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = (
                    f"{relative_directory}/{entry.name}" if relative_directory else entry.name
                )
                path_stat = entry.stat(follow_symlinks=False)
                mode = path_stat.st_mode
                if stat.S_ISDIR(mode):
                    fields = ("d", relative, stat.S_IMODE(mode))
                    pending.append(relative)
                elif stat.S_ISREG(mode):
                    fields = (
                        "f",
                        relative,
                        stat.S_IMODE(mode),
                        path_stat.st_size,
                        path_stat.st_mtime_ns,
                    )
                elif stat.S_ISLNK(mode):
                    fields = ("l", relative, os.readlink(entry.path))
                else:
                    raise ArtifactError(
                        f"virtual environment contains an unsupported file: {relative}"
                    )
                lines.append((relative, "\0".join(map(str, fields))))
    digest = hashlib.sha256()
    for _relative, line in sorted(lines):
        digest.update(line.encode("utf-8", "surrogateescape"))
        digest.update(b"\n")
    return digest.hexdigest()


def _canonical_python_names() -> set[str]:
    major, minor = sys.version_info[:2]
    return {"python", f"python{major}", f"python{major}.{minor}"}


def _venv_uses_trusted_python(root, trusted_python) -> bool:
    try:
        venv = Path(root).resolve(strict=True)
        trusted = Path(trusted_python).resolve(strict=True)
    except OSError:
        return False
    python = venv / "bin" / "python"
    if not python.exists() and not python.is_symlink():
        return False
    for name in _canonical_python_names():
        candidate = venv / "bin" / name
        if not candidate.exists() and not candidate.is_symlink():
            continue
        try:
            if candidate.is_symlink():
                if not os.path.samefile(candidate.resolve(strict=True), trusted):
                    return False
            elif not candidate.is_file():
                return False
            elif candidate.stat().st_size != trusted.stat().st_size or not hmac.compare_digest(
                _sha256_file(candidate),
                _sha256_file(trusted),
            ):
                return False
        except OSError:
            return False
    return True


def _venv_has_unsafe_symlink(root, install_root, trusted_python) -> bool:
    try:
        venv = Path(root).resolve(strict=True)
        managed_root = Path(install_root).resolve(strict=True)
        trusted = Path(trusted_python).resolve(strict=True)
    except OSError:
        return True
    if trusted == managed_root or managed_root in trusted.parents:
        return True
    for path in venv.rglob("*"):
        if not path.is_symlink():
            continue
        raw_target = os.readlink(path)
        try:
            resolved_target = path.resolve(strict=True)
        except OSError:
            return True
        relative = path.relative_to(venv)
        is_system_python = (
            len(relative.parts) == 2
            and relative.parts[0] == "bin"
            and relative.name in _canonical_python_names()
            and resolved_target.is_file()
            and os.path.samefile(resolved_target, trusted)
        )
        if is_system_python:
            continue
        if not os.path.isabs(raw_target):
            if resolved_target == venv or venv in resolved_target.parents:
                continue
            return True
        return True
    return False


class _RecordTargetResolver:
    """Resolve RECORD rows exactly like ``Path.resolve(strict=False)``, faster.

    A venv's RECORD files name about ten thousand paths in a few hundred
    directories.  Resolving each from the root with pathlib (one ``lstat``
    per component, plus ``venv in target.parents`` building a Path for every
    ancestor) took ~17 s per pass on the device.  ``realpath(a/b)`` equals
    ``realpath(realpath(a)/b)``, so each directory is resolved once, only the
    last component of a row is looked at again, and containment is a prefix
    test on the normalized string ``realpath`` returns.
    """

    def __init__(self, venv):
        self.venv = Path(venv)
        self._venv_text = os.fspath(self.venv)
        self._venv_prefix = os.path.join(self._venv_text, "")
        self._bases = {}
        self._directories = {}
        self._targets = {}

    def target(self, record, raw_path) -> Path:
        value = str(raw_path)
        if not value or "\\" in value or value.startswith("/"):
            raise ArtifactError("virtual environment RECORD contains an unsafe path")
        base = self._bases.get(record)
        if base is None:
            base = self._bases[record] = os.fspath(record.parent.parent)
        joined = os.path.join(base, *value.split("/"))
        target = self._targets.get(joined)
        if target is None:
            resolved = self._realpath(joined)
            if resolved != self._venv_text and not resolved.startswith(self._venv_prefix):
                raise ArtifactError("virtual environment RECORD escapes the environment")
            target = self._targets[joined] = Path(resolved)
        return target

    def _realpath(self, path) -> str:
        parent, name = os.path.split(path)
        if name in {"", ".", ".."}:
            return os.path.realpath(path)
        real_parent = self._directories.get(parent)
        if real_parent is None:
            real_parent = self._directories[parent] = os.path.realpath(parent)
        candidate = os.path.join(real_parent, name)
        if os.path.islink(candidate):
            return os.path.realpath(candidate)
        return candidate


def _read_record_rows(record, venv, resolver=None) -> list[list[str]]:
    if record.is_symlink() or not record.is_file() or record.stat().st_size > 16 * 1024 * 1024:
        raise ArtifactError("virtual environment RECORD is unsafe")
    try:
        payload = record.read_text(encoding="utf-8")
        rows = list(csv.reader(io.StringIO(payload, newline="")))
    except (OSError, UnicodeError, csv.Error) as error:
        raise ArtifactError("virtual environment RECORD cannot be parsed") from error
    if not rows or any(len(row) != 3 for row in rows):
        raise ArtifactError("virtual environment RECORD is malformed")
    resolver = resolver or _RecordTargetResolver(venv)
    for row in rows:
        resolver.target(record, row[0])
    return rows


def _hashed_record_paths(venv) -> set[Path]:
    hashed_paths = set()
    resolver = _RecordTargetResolver(venv)
    for record in venv.rglob("*.dist-info/RECORD"):
        for row in _read_record_rows(record, venv, resolver):
            if not row[1]:
                continue
            if not re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", row[1]):
                raise ArtifactError("virtual environment RECORD uses an unsafe hash")
            hashed_paths.add(resolver.target(record, row[0]))
    return hashed_paths


def _venv_has_unsafe_path_file(root) -> bool:
    try:
        venv = Path(root).resolve(strict=True)
        hashed_paths = _hashed_record_paths(venv)
    except (OSError, ArtifactError):
        return True
    candidates = tuple(venv.rglob("*.pth")) + tuple(venv.rglob("*.egg-link"))
    for path in candidates:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            return True
        if path.resolve() not in hashed_paths:
            return True
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return True
        for line in lines:
            value = line.strip()
            if (
                not value
                or value.startswith("#")
                or value == "import"
                or value.startswith("import ")
                or value.startswith("import\t")
            ):
                continue
            referenced = Path(value)
            if not referenced.is_absolute():
                referenced = path.parent / referenced
            resolved_reference = referenced.resolve(strict=False)
            if resolved_reference != venv and venv not in resolved_reference.parents:
                return True
    return False


def _normalize_venv_python_links(root, trusted_python) -> None:
    venv = Path(root)
    trusted = Path(trusted_python).resolve(strict=True)
    for name in _canonical_python_names():
        candidate = venv / "bin" / name
        if not candidate.is_symlink():
            continue
        candidate.unlink()
        candidate.symlink_to(trusted)


def _record_digest(payload) -> str:
    digest = hashlib.sha256(payload).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _render_record_rows(rows) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue()


def _relocate_venv_paths(root, published_destination, *, install_root) -> int:
    venv = Path(root).resolve(strict=True)
    final_venv = Path(published_destination).resolve(strict=False)
    managed_root = Path(install_root).resolve(strict=True)
    final_bytes = os.fsencode(str(final_venv))
    source_bytes = os.fsencode(str(venv))
    separator = rb"[\\/]"
    segment = rb"[^\\/\s'\"\x00]+"
    managed_pattern = re.compile(
        re.escape(os.fsencode(str(managed_root)))
        + rb"(?:"
        + separator
        + segment
        + rb")*?"
        + separator
        + rb"venv_inkypi"
    )
    source_pattern = re.compile(re.escape(source_bytes))
    target_files = [venv / "pyvenv.cfg"]
    bin_dir = venv / "bin"
    if bin_dir.is_dir():
        target_files.extend(path for path in bin_dir.rglob("*") if path.is_file())

    planned = {}
    for path in target_files:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        payload = path.read_bytes()
        if b"\0" in payload:
            continue
        relocated = managed_pattern.sub(lambda _match: final_bytes, payload)
        relocated = source_pattern.sub(lambda _match: final_bytes, relocated)
        if relocated != payload:
            planned[path.resolve()] = relocated

    record_documents = {}
    owners = {}
    resolver = _RecordTargetResolver(venv)
    for record in venv.rglob("*.dist-info/RECORD"):
        rows = _read_record_rows(record, venv, resolver)
        record_documents[record] = rows
        for index, row in enumerate(rows):
            target = resolver.target(record, row[0])
            if target in planned:
                owners.setdefault(target, []).append((record, index))

    record_updates = {}
    for path, relocated in planned.items():
        entries = owners.get(path, [])
        if len(entries) > 1:
            raise ArtifactError("multiple distributions own a relocated environment file")
        if not entries:
            continue
        record, index = entries[0]
        row = record_documents[record][index]
        if not re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", row[1]):
            raise ArtifactError("relocated environment file lacks a trusted RECORD hash")
        payload = path.read_bytes()
        if not hmac.compare_digest(row[1], f"sha256={_record_digest(payload)}"):
            raise ArtifactError("relocated environment file fails its RECORD hash")
        row[1] = f"sha256={_record_digest(relocated)}"
        row[2] = str(len(relocated))
        record_updates[record] = _render_record_rows(record_documents[record])

    # A cloned venv shares inodes with the live and previous releases: write
    # each relocated file as a new inode instead of modifying it in place.
    for path, relocated in planned.items():
        _atomic_write_bytes(path, relocated, _file_mode(path))
    for record, payload in record_updates.items():
        _atomic_write_text(record, payload, stat.S_IMODE(record.stat().st_mode))

    for path in target_files:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        payload = path.read_bytes()
        if b"\0" in payload:
            continue
        stale_matches = (
            match.group(0)
            for match in managed_pattern.finditer(payload)
            if match.group(0) != final_bytes
        )
        has_staging_source = source_bytes != final_bytes and source_pattern.search(payload)
        if next(stale_matches, None) is not None or has_staging_source:
            raise ArtifactError("virtual environment still references a staging release")
    return len(planned)


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_tree(root) -> None:
    if os.name == "nt":
        return
    directories = []
    for current_root, child_directories, filenames in os.walk(root):
        current = Path(current_root)
        directories.append(current)
        child_directories[:] = [
            name for name in child_directories if not (current / name).is_symlink()
        ]
        for name in filenames:
            path = current / name
            if path.is_symlink():
                continue
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for directory in reversed(directories):
        fsync_directory(directory)


def _run_checked(command, *, cwd=None, timeout=None, env=None) -> None:
    subprocess.run(
        [str(argument) for argument in command],
        cwd=str(cwd) if cwd is not None else None,
        check=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )


def _atomic_write_text(path, text, mode) -> None:
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
            os.fchmod(descriptor, int(mode))
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(text)
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


def _file_mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _set_mode(path, mode) -> None:
    if _MODES_ENFORCED:
        os.chmod(path, int(mode))


def _ensure_mode_copy_on_write(path, mode) -> bool:
    """Give a regular file ``mode`` without changing an inode another release shares.

    chmod changes the inode, so on a hard link it would also change the live
    and previous releases; a shared file is replaced by a private copy.
    """

    target = Path(path)
    info = os.lstat(target)
    if not stat.S_ISREG(info.st_mode):
        raise ArtifactError(f"refusing to change the mode of a non-regular file: {target}")
    if not _MODES_ENFORCED or _file_mode(target) == int(mode):
        return False
    if info.st_nlink > 1:
        _replace_with_private_copy(target, mode)
    else:
        _set_mode(target, mode)
    return True


def _copy_to_new_file(source, destination, mode) -> None:
    """Copy into a destination that must not exist yet (never follows an entry)."""

    info = os.lstat(source)
    with Path(source).open("rb") as input_stream, Path(destination).open("xb") as output:
        shutil.copyfileobj(input_stream, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    os.utime(destination, ns=(info.st_atime_ns, info.st_mtime_ns))
    _set_mode(destination, mode)


def _replace_with_private_copy(path, mode) -> None:
    target = Path(path)
    info = os.lstat(target)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with target.open("rb") as source, os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.utime(temporary, ns=(info.st_atime_ns, info.st_mtime_ns))
        _set_mode(temporary, mode)
        os.replace(temporary, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _atomic_write_bytes(path, payload, mode) -> None:
    """Write a new inode and rename it over ``path``; the old inode is never modified."""

    target = Path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _set_mode(temporary, mode)
        os.replace(temporary, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
