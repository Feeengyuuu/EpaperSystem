"""Artifact preparation and symmetric release activation/rollback engine."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
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
    ):
        self.layout = layout
        self.config_path = Path(config_path)
        self.python_executable = str(python_executable)
        self.run_command = run_command or _run_checked
        self.disk_checker = disk_checker

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
        self.run_command(
            [self.python_executable, "-m", "venv", str(venv)],
            cwd=candidate,
            timeout=180,
        )
        venv_python = venv / "bin" / "python"
        requirements = candidate / "install" / "requirements.txt"
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
        self.run_command(
            [str(venv_python), "-m", "pip", "check"],
            cwd=candidate,
            timeout=120,
        )
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
    link_manager = links or FilesystemLinks()
    preserved = set()
    for link in (layout.current_link, layout.previous_link):
        target = link_manager.read(link)
        if target is not None:
            preserved.add(Path(target).resolve())
    releases = sorted(
        (
            path
            for path in layout.releases_dir.iterdir()
            if path.is_dir() and not path.is_symlink()
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    retained = set(preserved)
    retain_count = max(2, int(keep))
    for release in releases:
        if len(retained) >= retain_count:
            break
        retained.add(release.resolve())
    removed = []
    for release in releases:
        if release.resolve() in retained:
            continue
        _safe_remove_tree(release, layout.releases_dir)
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


def _safe_remove_tree(path, allowed_root) -> None:
    candidate = Path(path).resolve()
    root = Path(allowed_root).resolve()
    if candidate == root or root not in candidate.parents:
        raise ReleaseStateError(f"refusing to remove path outside managed root: {candidate}")
    shutil.rmtree(candidate)
    fsync_directory(candidate.parent)


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
