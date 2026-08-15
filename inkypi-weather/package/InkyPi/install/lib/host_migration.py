"""Trusted, release-bound host migrations for the transactional updater."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


HEADLESS_MODE_MIGRATION_ID = "headless_mode_v1"
NASAPICS_MIGRATION_ID = "nasapics_space_weather_v1"
HEADLESS_MODE_UPDATER_CAPABILITY = "headless_mode_v1"
MIGRATION_REQUEST_NAME = ".release-migrations.json"
HEADLESS_MODE_EXPECTATION_NAME = ".headless-mode-v1.expectation.json"
SYSTEMCTL = "/usr/bin/systemctl"
LIGHTDM_SERVICE = "lightdm.service"
SUPPORTED_DEFAULT_TARGETS = frozenset({"graphical.target", "multi-user.target"})
MAX_CONTROL_BYTES = 64 * 1024
GPU_MEMORY_CANARY_ID = "gpu_memory_32_tryboot_v1"
GPU_MEMORY_CANARY_STATE_NAME = "gpu-memory-canary-v1.json"
GPU_MEMORY_MIB = 32
REBOOT = "/usr/sbin/reboot"
ZERO_2_W_MODEL = re.compile(r"Raspberry Pi Zero 2 W Rev 1\.[0-9]+")
SAFE_RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
MAX_CANARY_STATE_BYTES = 16 * 1024


class HostMigrationError(RuntimeError):
    pass


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        if mode is not None and os.name != "nt":
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read_regular_file(path: Path, *, label: str, limit: int) -> bytes:
    if path.is_symlink() or not path.exists():
        raise HostMigrationError(f"{label} must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise HostMigrationError(f"{label} must be a regular file") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise HostMigrationError(f"{label} must be a regular file")
        chunks = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(4096, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise HostMigrationError(f"{label} is too large")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


class TrustedGpuMemoryCanary:
    """Run the fixed Zero 2 W gpu_mem=32 experiment via one-shot tryboot."""

    def __init__(
        self,
        *,
        identity,
        state_root=Path("/var/lib/inkypi/update"),
        boot_root=Path("/boot/firmware"),
        board_model_path=Path("/proc/device-tree/model"),
        boot_id_path=Path("/proc/sys/kernel/random/boot_id"),
        tryboot_flag_path=Path(
            "/proc/device-tree/chosen/bootloader/tryboot"
        ),
        mount_validator=os.path.ismount,
        runner=subprocess.run,
    ):
        self.state_root = Path(state_root)
        self.boot_root = Path(boot_root)
        self.board_model_path = Path(board_model_path)
        self.boot_id_path = Path(boot_id_path)
        self.tryboot_flag_path = Path(tryboot_flag_path)
        self.identity = dict(identity) if isinstance(identity, dict) else identity
        self._mount_validator = mount_validator
        self._runner = runner

    @property
    def state_path(self) -> Path:
        return self.state_root / GPU_MEMORY_CANARY_STATE_NAME

    @property
    def config_path(self) -> Path:
        return self.boot_root / "config.txt"

    @property
    def tryboot_path(self) -> Path:
        return self.boot_root / "tryboot.txt"

    def start(self) -> dict:
        identity = self._validated_identity()
        self._validate_host()
        self._ensure_state_root()
        if os.path.lexists(self.state_path):
            return self._resume_start(self._validated_state())
        if os.path.lexists(self.tryboot_path):
            raise HostMigrationError("tryboot.txt is not owned by this canary")

        config = _read_regular_file(
            self.config_path,
            label="boot config",
            limit=1024 * 1024,
        )
        config_sha256 = hashlib.sha256(config).hexdigest()
        boot_id = self._read_boot_id()
        tryboot = self._tryboot_payload(identity, config_sha256)
        tryboot_sha256 = hashlib.sha256(tryboot).hexdigest()
        state = {
            "schema_version": 1,
            "canary": GPU_MEMORY_CANARY_ID,
            "phase": "preparing",
            "release_id": identity["release_id"],
            "updater_sha256": identity["updater_sha256"],
            "config_sha256": config_sha256,
            "tryboot_sha256": tryboot_sha256,
            "start_boot_id": boot_id,
            "phase_boot_id": boot_id,
        }
        self._persist_state(state)
        return self._resume_start(state)

    def _resume_start(self, state) -> dict:
        boot_id = self._read_boot_id()
        if state["phase"] == "preparing":
            if boot_id != state["start_boot_id"]:
                state = dict(state)
                state["start_boot_id"] = boot_id
                state["phase_boot_id"] = boot_id
                self._persist_state(state)
            expected = self._tryboot_payload(
                {
                    "release_id": state["release_id"],
                    "updater_sha256": state["updater_sha256"],
                },
                state["config_sha256"],
            )
            if not os.path.lexists(self.tryboot_path):
                _atomic_write(self.tryboot_path, expected)
            # A previous call may have completed the FAT rename but failed its
            # directory fsync.  Re-establish that durability on every resume
            # before the state is allowed to advance to armed.
            _fsync_directory(self.boot_root)
            # Re-validate the immutable inputs and exact owned payload before
            # making the reboot-requested state durable.
            self._validated_state()
            state = dict(state)
            state["phase"] = "armed"
            state["phase_boot_id"] = boot_id
            self._persist_state(state)
            self._validated_state()
        elif state["phase"] == "armed":
            if boot_id != state["start_boot_id"]:
                status = "testing" if self._tryboot_is_active() else "auto_rolled_back"
                return {"canary": GPU_MEMORY_CANARY_ID, "status": status}
        else:
            raise HostMigrationError("GPU memory canary rollback is incomplete")
        self._fixed_reboot(tryboot=True)
        return {"canary": GPU_MEMORY_CANARY_ID, "status": "reboot_requested"}

    def status(self) -> dict:
        self._validated_identity()
        self._validate_host()
        self._require_existing_state_root()
        if not os.path.lexists(self.state_path):
            if os.path.lexists(self.tryboot_path):
                raise HostMigrationError("tryboot.txt exists without canary state")
            status = "inactive"
        else:
            state = self._validated_state()
            boot_id = self._read_boot_id()
            if state["phase"] == "preparing":
                status = "preparing"
            elif state["phase"] == "armed":
                if boot_id == state["start_boot_id"]:
                    status = "armed"
                elif self._tryboot_is_active():
                    status = "testing"
                else:
                    status = "auto_rolled_back"
            elif state["phase"] == "rolling_back":
                status = "rollback_in_progress"
            elif boot_id == state["phase_boot_id"]:
                status = "rollback_pending_reboot"
            else:
                status = "rollback_complete_pending_finalize"
        return {"canary": GPU_MEMORY_CANARY_ID, "status": status}

    def rollback(self) -> dict:
        self._validated_identity()
        self._validate_host()
        self._require_existing_state_root()
        if not os.path.lexists(self.state_path):
            if os.path.lexists(self.tryboot_path):
                raise HostMigrationError("tryboot.txt exists without canary state")
            return {"canary": GPU_MEMORY_CANARY_ID, "status": "inactive"}

        state = self._validated_state()
        boot_id = self._read_boot_id()
        if state["phase"] == "rollback_pending_reboot":
            if boot_id != state["phase_boot_id"]:
                self._remove_state()
                return {"canary": GPU_MEMORY_CANARY_ID, "status": "inactive"}
            self._fixed_reboot(tryboot=False)
            return {"canary": GPU_MEMORY_CANARY_ID, "status": "reboot_requested"}

        if state["phase"] in {"preparing", "armed"}:
            state = dict(state)
            state["phase"] = "rolling_back"
            state["phase_boot_id"] = boot_id
            self._persist_state(state)

        if os.path.lexists(self.tryboot_path):
            expected = self._tryboot_payload(
                {
                    "release_id": state["release_id"],
                    "updater_sha256": state["updater_sha256"],
                },
                state["config_sha256"],
            )
            actual = _read_regular_file(
                self.tryboot_path,
                label="canary tryboot file",
                limit=MAX_CANARY_STATE_BYTES,
            )
            if not hmac.compare_digest(actual, expected):
                raise HostMigrationError("tryboot.txt is not owned by this canary")
            self.tryboot_path.unlink()
        # A previous rollback may have removed the FAT entry but failed the
        # directory fsync.  Missing is not evidence of durable removal.
        _fsync_directory(self.boot_root)

        state = dict(state)
        state["phase"] = "rollback_pending_reboot"
        state["phase_boot_id"] = boot_id
        self._persist_state(state)
        self._fixed_reboot(tryboot=False)
        return {"canary": GPU_MEMORY_CANARY_ID, "status": "reboot_requested"}

    def _validated_identity(self) -> dict:
        if not isinstance(self.identity, dict) or set(self.identity) != {
            "release_id",
            "updater_sha256",
        }:
            raise HostMigrationError("installed updater identity is invalid")
        release_id = self.identity.get("release_id")
        updater_sha256 = self.identity.get("updater_sha256")
        if not isinstance(release_id, str) or not SAFE_RELEASE_ID.fullmatch(release_id):
            raise HostMigrationError("installed updater release is invalid")
        if (
            not isinstance(updater_sha256, str)
            or not SHA256_HEX.fullmatch(updater_sha256)
        ):
            raise HostMigrationError("installed updater hash is invalid")
        return {"release_id": release_id, "updater_sha256": updater_sha256}

    def _validate_host(self) -> None:
        if (
            self.boot_root.is_symlink()
            or not self.boot_root.is_dir()
            or not self._mount_validator(self.boot_root)
        ):
            raise HostMigrationError("boot firmware path is not the trusted mount")
        if os.path.lexists(self.boot_root / "autoboot.txt"):
            raise HostMigrationError(
                "alternate tryboot configuration is unsupported"
            )
        model = _read_regular_file(
            self.board_model_path,
            label="board model",
            limit=256,
        ).rstrip(b"\x00")
        try:
            model_text = model.decode("ascii", errors="strict")
        except UnicodeError as error:
            raise HostMigrationError("board model is malformed") from error
        if not ZERO_2_W_MODEL.fullmatch(model_text):
            raise HostMigrationError("GPU memory canary requires Raspberry Pi Zero 2 W")

    def _read_boot_id(self) -> str:
        raw = _read_regular_file(
            self.boot_id_path,
            label="kernel boot identity",
            limit=128,
        )
        try:
            value = raw.decode("ascii", errors="strict").strip().lower()
        except UnicodeError as error:
            raise HostMigrationError("kernel boot identity is malformed") from error
        if not BOOT_ID.fullmatch(value):
            raise HostMigrationError("kernel boot identity is malformed")
        return value

    @staticmethod
    def _tryboot_payload(identity, config_sha256) -> bytes:
        return (
            f"# Managed by InkyPi {GPU_MEMORY_CANARY_ID}\n"
            f"# release_id={identity['release_id']}\n"
            f"# updater_sha256={identity['updater_sha256']}\n"
            f"# config_sha256={config_sha256}\n"
            "[all]\n"
            "include config.txt\n"
            "[all]\n"
            f"gpu_mem={GPU_MEMORY_MIB}\n"
        ).encode("ascii")

    def _read_state(self) -> dict:
        raw = _read_regular_file(
            self.state_path,
            label="GPU memory canary state",
            limit=MAX_CANARY_STATE_BYTES,
        )
        try:
            document = json.loads(
                raw.decode("ascii", errors="strict"),
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise HostMigrationError("GPU memory canary state is malformed") from error
        if not isinstance(document, dict):
            raise HostMigrationError("GPU memory canary state is malformed")
        return document

    def _validated_state(self) -> dict:
        state = self._read_state()
        expected_keys = {
            "schema_version",
            "canary",
            "phase",
            "release_id",
            "updater_sha256",
            "config_sha256",
            "tryboot_sha256",
            "start_boot_id",
            "phase_boot_id",
        }
        if set(state) != expected_keys:
            raise HostMigrationError("GPU memory canary state has an invalid schema")
        if (
            type(state.get("schema_version")) is not int
            or state["schema_version"] != 1
            or state.get("canary") != GPU_MEMORY_CANARY_ID
            or state.get("phase")
            not in {"preparing", "armed", "rolling_back", "rollback_pending_reboot"}
            or not isinstance(state.get("release_id"), str)
            or not SAFE_RELEASE_ID.fullmatch(state["release_id"])
            or not isinstance(state.get("updater_sha256"), str)
            or not SHA256_HEX.fullmatch(state["updater_sha256"])
            or not isinstance(state.get("config_sha256"), str)
            or not SHA256_HEX.fullmatch(state["config_sha256"])
            or not isinstance(state.get("tryboot_sha256"), str)
            or not SHA256_HEX.fullmatch(state["tryboot_sha256"])
            or not isinstance(state.get("start_boot_id"), str)
            or not BOOT_ID.fullmatch(state["start_boot_id"])
            or not isinstance(state.get("phase_boot_id"), str)
            or not BOOT_ID.fullmatch(state["phase_boot_id"])
        ):
            raise HostMigrationError("GPU memory canary state has an invalid schema")
        identity = self._validated_identity()
        if (
            state["release_id"] != identity["release_id"]
            or state["updater_sha256"] != identity["updater_sha256"]
        ):
            raise HostMigrationError("GPU memory canary identity no longer matches")
        config_sha256 = hashlib.sha256(
            _read_regular_file(
                self.config_path,
                label="boot config",
                limit=1024 * 1024,
            )
        ).hexdigest()
        if config_sha256 != state["config_sha256"]:
            raise HostMigrationError("boot config no longer matches canary state")
        expected_tryboot = self._tryboot_payload(identity, config_sha256)
        if hashlib.sha256(expected_tryboot).hexdigest() != state["tryboot_sha256"]:
            raise HostMigrationError("canary tryboot hash is invalid")
        tryboot_exists = os.path.lexists(self.tryboot_path)
        if tryboot_exists:
            actual_tryboot = _read_regular_file(
                self.tryboot_path,
                label="canary tryboot file",
                limit=MAX_CANARY_STATE_BYTES,
            )
            if not hmac.compare_digest(actual_tryboot, expected_tryboot):
                raise HostMigrationError("tryboot.txt is not owned by this canary")
            if state["phase"] == "rollback_pending_reboot":
                raise HostMigrationError("rolled back canary still has tryboot.txt")
        elif state["phase"] == "armed":
            raise HostMigrationError("armed canary tryboot.txt is missing")
        elif state["phase"] == "preparing":
            # Durable preparing state is deliberately written before the FAT
            # rename, so a missing file is a resumable power-loss boundary.
            pass
        elif state["phase"] == "rolling_back":
            # A power loss after the owned file was removed is recoverable.
            pass
        elif state["phase"] == "rollback_pending_reboot":
            pass
        return state

    def _persist_state(self, state) -> None:
        _atomic_write(
            self.state_path,
            (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "ascii"
            ),
            mode=0o600,
        )
        if self._read_state() != state:
            raise HostMigrationError("GPU memory canary state is not durable")

    def _ensure_state_root(self) -> None:
        if os.path.lexists(self.state_root):
            if self.state_root.is_symlink() or not self.state_root.is_dir():
                raise HostMigrationError("canary state root must be a directory")
            return
        self.state_root.mkdir(parents=True, mode=0o700)
        if self.state_root.is_symlink() or not self.state_root.is_dir():
            raise HostMigrationError("canary state root must be a directory")
        _fsync_directory(self.state_root.parent)

    def _require_existing_state_root(self) -> None:
        if self.state_root.is_symlink() or not self.state_root.is_dir():
            raise HostMigrationError("canary state root must be a directory")

    def _remove_state(self) -> None:
        if self.state_path.is_symlink() or not self.state_path.is_file():
            raise HostMigrationError("GPU memory canary state must be a regular file")
        self.state_path.unlink()
        _fsync_directory(self.state_root)

    def _tryboot_is_active(self) -> bool:
        if not os.path.lexists(self.tryboot_flag_path):
            return False
        raw = _read_regular_file(
            self.tryboot_flag_path,
            label="tryboot boot flag",
            limit=8,
        )
        if len(raw) != 4:
            raise HostMigrationError("tryboot boot flag is malformed")
        value = int.from_bytes(raw, byteorder="big", signed=False)
        if value not in {0, 1}:
            raise HostMigrationError("tryboot boot flag is malformed")
        return value == 1

    def _fixed_reboot(self, *, tryboot: bool) -> None:
        command = [REBOOT]
        if tryboot:
            command.append("0 tryboot")
        self._runner(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _reject_duplicate_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _read_control(path: Path, *, label: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise HostMigrationError(f"{label} must be a regular file")
    try:
        if path.stat().st_size > MAX_CONTROL_BYTES:
            raise HostMigrationError(f"{label} is too large")
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except HostMigrationError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise HostMigrationError(f"{label} is malformed") from error
    if not isinstance(document, dict):
        raise HostMigrationError(f"{label} must contain a JSON object")
    return document


class TrustedHostMigrator:
    """Execute only the built-in headless migration with fixed systemctl argv."""

    def __init__(self, *, runner=subprocess.run):
        self._runner = runner

    def requested_migration(self, release_path, release_id) -> str | None:
        release = Path(release_path)
        request_path = release / "install" / MIGRATION_REQUEST_NAME
        expectation_path = release / "install" / HEADLESS_MODE_EXPECTATION_NAME
        if not os.path.lexists(request_path):
            if os.path.lexists(expectation_path):
                raise HostMigrationError(
                    "headless migration expectation exists without a request"
                )
            return None

        request = _read_control(request_path, label="release migration request")
        if set(request) != {"schema_version", "migrations"}:
            raise HostMigrationError("release migration request has an invalid schema")
        migrations = request.get("migrations")
        if (
            type(request.get("schema_version")) is not int
            or request["schema_version"] != 1
            or not isinstance(migrations, list)
            or any(not isinstance(item, str) or not item for item in migrations)
            or len(set(migrations)) != len(migrations)
        ):
            raise HostMigrationError("release migration request has an invalid schema")
        unsupported = set(migrations) - {
            HEADLESS_MODE_MIGRATION_ID,
            NASAPICS_MIGRATION_ID,
        }
        if unsupported:
            raise HostMigrationError(
                "release migration request names an unsupported migration"
            )
        if HEADLESS_MODE_MIGRATION_ID not in migrations:
            if os.path.lexists(expectation_path):
                raise HostMigrationError(
                    "headless migration expectation exists without a request"
                )
            return None
        if not os.path.lexists(expectation_path):
            raise HostMigrationError(
                "headless migration request lacks the updater capability handshake"
            )
        expectation = _read_control(
            expectation_path,
            label="headless migration expectation",
        )
        expected = {
            "schema_version": 1,
            "migration": HEADLESS_MODE_MIGRATION_ID,
            "release_id": str(release_id),
            "updater_capability": HEADLESS_MODE_UPDATER_CAPABILITY,
        }
        if expectation != expected:
            raise HostMigrationError(
                "headless migration expectation does not match this updater release"
            )
        return HEADLESS_MODE_MIGRATION_ID

    def capture_snapshot(self, migration) -> dict:
        self._require_supported_migration(migration)
        snapshot = {
            "default_target": self._query("get-default"),
            "lightdm_enabled": self._query("is-enabled", LIGHTDM_SERVICE),
            "lightdm_active": self._query("is-active", LIGHTDM_SERVICE),
        }
        self._validate_snapshot(snapshot)
        return snapshot

    def apply(self, migration, snapshot) -> None:
        self._require_supported_migration(migration)
        self._validate_snapshot(snapshot)
        self._mutate("set-default", "multi-user.target")
        self._mutate("disable", LIGHTDM_SERVICE)
        self._mutate("stop", LIGHTDM_SERVICE)

    def restore(self, migration, snapshot) -> None:
        self._require_supported_migration(migration)
        self._validate_snapshot(snapshot)
        self._mutate("set-default", snapshot["default_target"])
        self._mutate(
            "enable" if snapshot["lightdm_enabled"] == "enabled" else "disable",
            LIGHTDM_SERVICE,
        )
        self._mutate(
            "start" if snapshot["lightdm_active"] == "active" else "stop",
            LIGHTDM_SERVICE,
        )

    def restore_for_boot(self, migration, snapshot) -> None:
        """Restore persisted state without blocking on a Before-ordered unit."""

        self._require_supported_migration(migration)
        self._validate_snapshot(snapshot)
        self._mutate("set-default", snapshot["default_target"])
        self._mutate(
            "enable" if snapshot["lightdm_enabled"] == "enabled" else "disable",
            LIGHTDM_SERVICE,
        )
        if snapshot["lightdm_active"] == "active":
            # The recovery oneshot is ordered Before=lightdm.service.  Queueing
            # the start lets systemd honor that order after this process exits;
            # a blocking start here would wait on the unit that is calling us.
            self._mutate("--no-block", "start", LIGHTDM_SERVICE)
        else:
            self._mutate("stop", LIGHTDM_SERVICE)

    def snapshot_matches(self, migration, snapshot) -> bool:
        self._require_supported_migration(migration)
        self._validate_snapshot(snapshot)
        return self.capture_snapshot(migration) == snapshot

    @staticmethod
    def _require_supported_migration(migration) -> None:
        if migration != HEADLESS_MODE_MIGRATION_ID:
            raise HostMigrationError("unsupported host migration")

    @staticmethod
    def _validate_snapshot(snapshot) -> None:
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "default_target",
            "lightdm_enabled",
            "lightdm_active",
        }:
            raise HostMigrationError("headless host snapshot has an invalid schema")
        if snapshot["default_target"] not in SUPPORTED_DEFAULT_TARGETS:
            raise HostMigrationError("default systemd target is unsupported")
        if snapshot["lightdm_enabled"] not in {"enabled", "disabled"}:
            raise HostMigrationError("LightDM enabled state is unsupported")
        if snapshot["lightdm_active"] not in {"active", "inactive"}:
            raise HostMigrationError("LightDM active state is unsupported")

    def _query(self, *arguments) -> str:
        result = self._runner(
            [SYSTEMCTL, *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        raw = getattr(result, "stdout", b"")
        if isinstance(raw, bytes):
            try:
                value = raw.decode("utf-8", errors="strict").strip()
            except UnicodeError as error:
                raise HostMigrationError("systemctl returned malformed state") from error
        else:
            value = str(raw).strip()
        if not value:
            raise HostMigrationError("systemctl did not report host state")
        return value

    def _mutate(self, *arguments) -> None:
        self._runner(
            [SYSTEMCTL, *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
