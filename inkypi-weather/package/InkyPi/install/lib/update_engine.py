"""Artifact preparation and symmetric release activation/rollback engine."""

from __future__ import annotations

import csv
import base64
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
from release_state import (
    RecoveryAction,
    ReleaseLayout,
    ReleaseStateError,
    UpdatePhase,
    atomic_symlink,
    fsync_directory,
    recover_incomplete_update,
)


MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
DEFAULT_DISK_RESERVE_BYTES = 256 * 1024 * 1024


class ArtifactError(RuntimeError):
    pass


class UpdateFailed(RuntimeError):
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
    ):
        self.layout = layout
        self.service = service
        self.links = links or FilesystemLinks()
        self.managed_files = tuple(managed_files)
        self.copy_file = copy_file or atomic_copy_file
        self.fallback_release = (
            Path(fallback_release) if fallback_release is not None else None
        )

    def activate(self, journal, release_path) -> None:
        if journal.phase is not UpdatePhase.PREFLIGHTED:
            raise UpdateFailed("release activation requires a preflighted journal")
        target = Path(release_path).resolve()
        if not target.is_dir():
            raise UpdateFailed(f"prepared release does not exist: {target}")
        releases_root = self.layout.releases_dir.resolve()
        if target.parent != releases_root:
            raise UpdateFailed("prepared release is outside the managed release directory")

        previous = self.links.read(self.layout.current_link)
        if (
            previous is None
            and self.fallback_release is not None
            and self.fallback_release.is_dir()
            and not self.fallback_release.is_symlink()
        ):
            previous = self.fallback_release.resolve()
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
        journal.update_metadata(
            previous_target=str(previous) if previous is not None else None,
            target_path=str(target),
            legacy_source=str(legacy_source) if legacy_source else None,
            service_was_active=service_was_active,
            service_was_enabled=service_was_enabled,
            managed_backups=backups,
        )
        journal.transition(UpdatePhase.SWITCHED)

        try:
            if service_was_active:
                self.service.stop()
            if legacy_source is not None:
                self.links.migrate_legacy_directory(legacy_source, previous)
            if previous is not None:
                self.links.replace(previous, self.layout.previous_link)
            self.links.replace(target, self.layout.current_link)
            self._install_managed_files(target)
            self.service.daemon_reload()
            if not service_was_enabled:
                self.service.enable()
            journal.transition(UpdatePhase.STARTING)
            self.service.start()
            if not self.service.wait_ready(journal.release_id):
                raise UpdateFailed(
                    f"target release {journal.release_id} did not become ready"
                )
            journal.transition(UpdatePhase.HEALTHY)
        except BaseException as error:
            try:
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

    def rollback(self, journal) -> None:
        if journal.phase in {UpdatePhase.SWITCHED, UpdatePhase.STARTING}:
            journal.transition(UpdatePhase.ROLLING_BACK)
        elif journal.phase is not UpdatePhase.ROLLING_BACK:
            raise UpdateFailed(
                f"cannot roll back update in phase {journal.phase.value}"
            )

        try:
            if self.service.is_active():
                self.service.stop()
            metadata = journal.metadata
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
                self.service.start()
            journal.transition(UpdatePhase.ROLLED_BACK)
            self.cleanup_backups(journal)
        except BaseException:
            if journal.phase is UpdatePhase.ROLLING_BACK:
                try:
                    journal.transition(UpdatePhase.ROLLBACK_FAILED)
                except BaseException:
                    pass
            raise

    def recover(self, journal):
        return recover_incomplete_update(
            journal,
            clean_staging=self._clean_staging,
            roll_back=self.rollback,
            finish_commit=lambda candidate: candidate.transition(
                UpdatePhase.COMMITTED
            ),
        )

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

    def _install_managed_files(self, release_path):
        for managed in self.managed_files:
            source = release_path.joinpath(*PurePosixPath(managed.source_relative).parts)
            if not source.is_file() or source.is_symlink():
                raise UpdateFailed(f"managed release file is missing: {source}")
            self.copy_file(source, managed.destination, managed.mode)

    def _restore_managed_files(self, records):
        for record in reversed(tuple(records or ())):
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
    ):
        self.layout = layout
        self.config_path = Path(config_path)
        self.python_executable = str(python_executable)
        self.run_command = run_command or _run_checked
        self.disk_checker = disk_checker
        self.links = links or FilesystemLinks()

    def _try_clone_current_venv(
        self,
        requirements,
        destination,
        *,
        published_destination=None,
    ) -> bool:
        """Clone a compatible current venv without sharing mutable files."""

        source = self.links.read(self.layout.current_link)
        if source is None:
            return False
        candidate_requirements = Path(requirements)
        destination_venv = Path(destination)
        try:
            releases_root = self.layout.releases_dir.resolve(strict=True)
            current_release = Path(source).resolve(strict=True)
        except OSError:
            return False
        if current_release.parent != releases_root:
            return False

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
            return False
        if not source_python.is_file():
            return False
        if source_venv.is_symlink() or not source_venv.is_dir():
            return False
        if not source_site_packages.is_dir() or source_site_packages.is_symlink():
            return False
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
            return False
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
            return False

        locked_versions = _locked_distribution_versions(candidate_requirements)
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
            return False

        source_snapshot = _venv_metadata_digest(source_venv)
        shutil.copytree(
            source_venv,
            destination_venv,
            symlinks=True,
            copy_function=shutil.copy2,
        )
        if _venv_metadata_digest(source_venv) != source_snapshot:
            raise ArtifactError("current virtual environment changed while it was copied")
        if _venv_metadata_digest(destination_venv) != source_snapshot:
            raise ArtifactError("candidate virtual environment copy is incomplete")
        self._finalize_candidate_venv(
            destination_venv,
            candidate_requirements,
            published_destination=published_destination,
            cwd=destination_venv,
        )
        return True

    def _probe_venv_environment(self, venv, locked_versions, *, cwd) -> None:
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
    ) -> None:
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
        self._probe_venv_environment(
            candidate_venv,
            locked_versions,
            cwd=cwd,
        )
        _normalize_venv_python_links(candidate_venv, self.python_executable)
        _relocate_venv_paths(
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
        )
        self._run_isolated_pip_check(candidate_venv, cwd=cwd)

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
        required = max(
            inspection.uncompressed_bytes * 3,
            inspection.uncompressed_bytes + 1024 * 1024 * 1024,
        )
        disk_checker(self.layout.install_root, required)

        staging.mkdir(parents=True, mode=0o755)
        archive_root = safe_extract_zip(inspection, staging / "archive")
        package_root = find_release_root(archive_root)
        candidate = staging / "release"
        os.replace(package_root, candidate)
        if archive_root.exists():
            shutil.rmtree(archive_root)
        if not (candidate / "cli").exists() and (candidate / "install" / "cli").is_dir():
            shutil.copytree(candidate / "install" / "cli", candidate / "cli")
        _atomic_write_text(candidate / ".release-id", f"{release_id}\n", 0o644)
        if os.name != "nt":
            for executable in (
                candidate / "install" / "inkypi",
                candidate / "install" / "inkypi-update",
                candidate / "install" / "preflight.py",
                candidate / "install" / "bootstrap_admin.py",
                candidate / "install" / "update_vendors.sh",
                candidate / "cli" / "inkypi-plugin",
            ):
                if executable.is_file():
                    os.chmod(executable, 0o755)

        vendor_script = candidate / "install" / "update_vendors.sh"
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
            self._finalize_candidate_venv(
                venv,
                requirements,
                published_destination=target / "venv_inkypi",
                cwd=candidate,
            )
        venv_python = venv / "bin" / "python"
        config_source = self.config_path
        if not config_source.is_file():
            config_source = candidate / "install" / "config_base" / "device.json"
        self.run_command(
            [
                str(venv_python),
                str(candidate / "install" / "preflight.py"),
                "--release-root",
                str(candidate),
                "--config",
                str(config_source),
                "--release-id",
                release_id,
            ],
            cwd=candidate,
            timeout=120,
        )

        _fsync_tree(candidate)
        os.replace(candidate, target)
        fsync_directory(self.layout.releases_dir)
        shutil.rmtree(staging)
        fsync_directory(self.layout.staging_dir)
        return target

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


class UpdateLock:
    def __init__(self, path):
        self.path = Path(path)
        self._descriptor = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                os.ftruncate(descriptor, 1)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
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


def _venv_environment_probe(venv, site_packages, expected_abi, locked_versions) -> str:
    return f"""
from importlib import metadata
from pathlib import Path
import base64
import hashlib
import platform
import re
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

documentation_names = {{"copying", "license", "notice"}}
for distribution in distributions:
    files = distribution.files
    assert files is not None
    for item in files:
        if item.hash is None:
            continue
        assert item.hash.mode == "sha256"
        installed_file = Path(distribution.locate_file(item)).resolve(strict=True)
        assert installed_file == venv or venv in installed_file.parents
        if installed_file.parent == site_packages and (
            installed_file.suffix.lower() in {{".md", ".rst"}}
            or installed_file.name.lower() in documentation_names
        ):
            continue
        with installed_file.open("rb") as stream:
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
    """Fingerprint a venv tree without reading package payloads into memory."""

    venv = Path(root)
    digest = hashlib.sha256()
    for path in sorted(venv.rglob("*"), key=lambda item: item.relative_to(venv).as_posix()):
        relative = path.relative_to(venv).as_posix()
        path_stat = os.lstat(path)
        mode = path_stat.st_mode
        if stat.S_ISDIR(mode):
            record = ("d", relative, stat.S_IMODE(mode))
        elif stat.S_ISREG(mode):
            record = (
                "f",
                relative,
                stat.S_IMODE(mode),
                path_stat.st_size,
                path_stat.st_mtime_ns,
            )
        elif stat.S_ISLNK(mode):
            record = ("l", relative, os.readlink(path))
        else:
            raise ArtifactError(
                f"virtual environment contains an unsupported file: {relative}"
            )
        digest.update(json.dumps(record, separators=(",", ":")).encode("utf-8"))
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


def _record_target(record, raw_path, venv) -> Path:
    value = str(raw_path)
    pure_path = PurePosixPath(value)
    if not value or "\\" in value or pure_path.is_absolute():
        raise ArtifactError("virtual environment RECORD contains an unsafe path")
    target = record.parent.parent.joinpath(*pure_path.parts).resolve(strict=False)
    if target != venv and venv not in target.parents:
        raise ArtifactError("virtual environment RECORD escapes the environment")
    return target


def _read_record_rows(record, venv) -> list[list[str]]:
    if record.is_symlink() or not record.is_file() or record.stat().st_size > 16 * 1024 * 1024:
        raise ArtifactError("virtual environment RECORD is unsafe")
    try:
        payload = record.read_text(encoding="utf-8")
        rows = list(csv.reader(io.StringIO(payload, newline="")))
    except (OSError, UnicodeError, csv.Error) as error:
        raise ArtifactError("virtual environment RECORD cannot be parsed") from error
    if not rows or any(len(row) != 3 for row in rows):
        raise ArtifactError("virtual environment RECORD is malformed")
    for row in rows:
        _record_target(record, row[0], venv)
    return rows


def _hashed_record_paths(venv) -> set[Path]:
    hashed_paths = set()
    for record in venv.rglob("*.dist-info/RECORD"):
        for row in _read_record_rows(record, venv):
            if not row[1]:
                continue
            if not re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", row[1]):
                raise ArtifactError("virtual environment RECORD uses an unsafe hash")
            hashed_paths.add(_record_target(record, row[0], venv))
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


def _relocate_venv_paths(root, published_destination, *, install_root) -> None:
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
    for record in venv.rglob("*.dist-info/RECORD"):
        rows = _read_record_rows(record, venv)
        record_documents[record] = rows
        for index, row in enumerate(rows):
            target = _record_target(record, row[0], venv)
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

    for path, relocated in planned.items():
        path_mode = stat.S_IMODE(path.stat().st_mode)
        path.write_bytes(relocated)
        os.chmod(path, path_mode)
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
