"""Trusted, release-bound host migrations for the transactional updater."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


HEADLESS_MODE_MIGRATION_ID = "headless_mode_v1"
NASAPICS_MIGRATION_ID = "nasapics_space_weather_v1"
HEADLESS_MODE_UPDATER_CAPABILITY = "headless_mode_v1"
MIGRATION_REQUEST_NAME = ".release-migrations.json"
HEADLESS_MODE_EXPECTATION_NAME = ".headless-mode-v1.expectation.json"
SYSTEMCTL = "/usr/bin/systemctl"
LIGHTDM_SERVICE = "lightdm.service"
SUPPORTED_DEFAULT_TARGETS = frozenset({"graphical.target", "multi-user.target"})
MAX_CONTROL_BYTES = 64 * 1024


class HostMigrationError(RuntimeError):
    pass


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
