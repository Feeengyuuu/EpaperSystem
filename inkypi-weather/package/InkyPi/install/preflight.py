#!/usr/bin/env python3
"""Validate an extracted InkyPi release without opening ports or display hardware."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from uuid import UUID


NASAPICS_MIGRATION_ID = "nasapics_space_weather_v1"
MIGRATION_REQUEST_NAME = ".release-migrations.json"
NASAPICS_EXPECTATION_NAME = ".nasapics-space-weather-v1.expectation.json"
MAX_MIGRATION_CONTROL_BYTES = 64 * 1024
MAX_DEVICE_CONFIG_BYTES = 16 * 1024 * 1024
REQUIRED_RELEASE_PATHS = (
    "src/inkypi.py",
    "src/templates/inky.html",
    "src/static/styles/main.css",
    "src/static/styles/select2.min.css",
    "src/static/scripts/dark_mode.js",
    "src/static/scripts/i18n.js",
    "src/static/scripts/image_modal.js",
    "src/static/scripts/refresh_settings_manager.js",
    "src/static/scripts/response_modal.js",
    "src/static/scripts/select2.min.js",
    "src/static/scripts/jquery.min.js",
    "src/static/scripts/chart.js",
    "src/static/scripts/calendar.min.js",
    "install/inkypi.service",
    "install/inkypi",
    "install/inkypi-update",
    "install/repair_env_permissions.py",
    "install/bootstrap_admin.py",
    "install/requirements.txt",
    "cli/inkypi-plugin",
    ".release-id",
)


class PreflightError(RuntimeError):
    pass


def _read_bounded_bytes(path: Path, *, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(limit + 1)
    except OSError as error:
        raise PreflightError(f"{label} cannot be read") from error
    if len(payload) > limit:
        raise PreflightError(f"{label} is too large")
    return payload


def _reject_duplicate_json_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _read_strict_json_object(path: Path, *, limit: int, label: str) -> dict:
    payload = _read_bounded_bytes(path, limit=limit, label=label)
    try:
        text = payload.decode("utf-8", errors="strict")
        document = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PreflightError(f"{label} is malformed") from error
    if not isinstance(document, dict):
        raise PreflightError(f"{label} must contain a JSON object")
    return document


def _read_release_id(root: Path) -> str:
    payload = _read_bounded_bytes(
        root / ".release-id",
        limit=128,
        label="release identity",
    )
    try:
        release_id = (
            payload.decode("utf-8", errors="strict")
            .replace("\r", "")
            .replace("\n", "")
        )
    except UnicodeError as error:
        raise PreflightError("release identity is malformed") from error
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", release_id):
        raise PreflightError("release identity file is empty, too long, or malformed")
    return release_id


def validate_release_tree(release_root, *, expected_release_id=None) -> None:
    root = Path(release_root)
    if not root.is_dir() or root.is_symlink():
        raise PreflightError(f"release root is not a regular directory: {root}")
    missing = [relative for relative in REQUIRED_RELEASE_PATHS if not (root / relative).is_file()]
    if missing:
        raise PreflightError(f"release is missing required files: {', '.join(missing)}")
    release_id = _read_release_id(root)
    if expected_release_id is not None and release_id != expected_release_id:
        raise PreflightError("release identity does not match the requested release")


def _load_requested_migrations(release_root: Path) -> tuple[str, ...]:
    request_path = release_root / "install" / MIGRATION_REQUEST_NAME
    if not request_path.exists():
        if request_path.is_symlink():
            raise PreflightError("release migration request cannot be a symlink")
        return ()
    if request_path.is_symlink() or not request_path.is_file():
        raise PreflightError("release migration request must be a regular file")
    request = _read_strict_json_object(
        request_path,
        limit=MAX_MIGRATION_CONTROL_BYTES,
        label="release migration request",
    )
    if set(request) != {"schema_version", "migrations"}:
        raise PreflightError("release migration request has an invalid schema")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise PreflightError("release migration request has an invalid schema")
    migrations = request["migrations"]
    if (
        not isinstance(migrations, list)
        or not migrations
        or any(not isinstance(item, str) or not item for item in migrations)
        or len(set(migrations)) != len(migrations)
    ):
        raise PreflightError("release migration request has invalid migrations")
    unsupported = set(migrations) - {NASAPICS_MIGRATION_ID}
    if unsupported:
        raise PreflightError("release migration request names an unsupported migration")
    return tuple(migrations)


def _positive_identity_integer(value, *, field: str) -> int:
    if type(value) is not int or value < 1:
        raise PreflightError(f"NASAPics target {field} is malformed")
    return value


def _capture_nasapics_identity(config_source: Path) -> dict:
    document = _read_strict_json_object(
        config_source,
        limit=MAX_DEVICE_CONFIG_BYTES,
        label="device configuration",
    )
    playlist_config = document.get("playlist_config")
    playlists = (
        playlist_config.get("playlists")
        if isinstance(playlist_config, dict)
        else None
    )
    if not isinstance(playlists, list):
        raise PreflightError("device configuration has malformed playlists")
    matches = []
    for playlist in playlists:
        if not isinstance(playlist, dict) or playlist.get("name") != "DailyDoseOfDay":
            continue
        plugins = playlist.get("plugins")
        if not isinstance(plugins, list):
            raise PreflightError("device configuration has malformed playlist plugins")
        for instance in plugins:
            if (
                isinstance(instance, dict)
                and instance.get("plugin_id") == "apod"
                and instance.get("name") == "NASAPics"
            ):
                matches.append(instance)
    if len(matches) != 1:
        raise PreflightError(
            "device configuration must contain exactly one DailyDoseOfDay/apod/NASAPics target"
        )
    target = matches[0]
    instance_uuid = target.get("instance_uuid")
    if (
        not isinstance(instance_uuid, str)
        or not instance_uuid
        or instance_uuid != instance_uuid.strip()
        or len(instance_uuid) > 128
        or any(ord(character) < 33 or ord(character) > 126 for character in instance_uuid)
    ):
        raise PreflightError("NASAPics target instance_uuid is malformed")
    try:
        UUID(instance_uuid)
    except (ValueError, AttributeError) as error:
        raise PreflightError("NASAPics target instance_uuid is malformed") from error
    return {
        "playlist_name": "DailyDoseOfDay",
        "plugin_id": "apod",
        "instance_name": "NASAPics",
        "instance_uuid": instance_uuid,
        "structural_generation": _positive_identity_integer(
            target.get("structural_generation"),
            field="structural_generation",
        ),
        "settings_revision": _positive_identity_integer(
            target.get("settings_revision"),
            field="settings_revision",
        ),
    }


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive_read_only_json(path: Path, document: dict) -> None:
    payload = (
        json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o444)
    except FileExistsError as error:
        raise PreflightError("migration expectation was preseeded") from error
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short migration expectation write")
            view = view[written:]
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
    except OSError as error:
        raise PreflightError("migration expectation cannot be persisted") from error
    finally:
        os.close(descriptor)
    try:
        _fsync_directory(path.parent)
    except OSError as error:
        raise PreflightError("migration expectation cannot be persisted") from error


def capture_release_migration_expectations(
    release_root,
    config_source,
    release_id,
) -> tuple[Path, ...]:
    root = Path(release_root)
    expectation_path = root / "install" / NASAPICS_EXPECTATION_NAME
    if os.path.lexists(expectation_path):
        raise PreflightError("migration expectation was preseeded")
    if _read_release_id(root) != release_id:
        raise PreflightError("release identity does not match the requested release")
    migrations = _load_requested_migrations(root)
    if not migrations:
        return ()
    captured = []
    if NASAPICS_MIGRATION_ID in migrations:
        target = _capture_nasapics_identity(Path(config_source))
        _write_exclusive_read_only_json(
            expectation_path,
            {
                "schema_version": 1,
                "migration": NASAPICS_MIGRATION_ID,
                "release_id": release_id,
                "target": target,
            },
        )
        captured.append(expectation_path)
    return tuple(captured)


def prepare_config_copy(source, destination) -> Path:
    source_path = Path(source)
    destination_path = Path(destination)
    try:
        document = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PreflightError(f"device configuration cannot be copied: {error}") from error
    if not isinstance(document, dict):
        raise PreflightError("device configuration must contain a JSON object")
    copied = json.loads(json.dumps(document))
    copied["display_type"] = "mock"
    copied["startup"] = False
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(
        json.dumps(copied, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination_path


def run_no_hardware_probe(release_root, config_source, release_id) -> None:
    root = Path(release_root).resolve()
    source_dir = root / "src"
    with tempfile.TemporaryDirectory(prefix="inkypi-preflight-") as temporary:
        runtime = Path(temporary)
        config_dir = runtime / "config"
        prepare_config_copy(config_source, config_dir / "device_dev.json")
        environment = {
            "INKYPI_DEV_ROOT": str(runtime / "dev"),
            "INKYPI_CONFIG_DIR": str(config_dir),
            "INKYPI_DATA_DIR": str(runtime / "data"),
            "INKYPI_CACHE_DIR": str(runtime / "cache"),
            "INKYPI_ENV_FILE": str(runtime / "inkypi.env"),
            "INKYPI_DISPLAY_DIR": str(runtime / "display"),
            "INKYPI_CURRENT_IMAGE_FILE": str(runtime / "display" / "current.png"),
            "INKYPI_PLUGIN_IMAGE_DIR": str(runtime / "plugins"),
            "INKYPI_FLASK_SECRET_FILE": str(config_dir / "flask_secret"),
            "INKYPI_RELEASE_ID": release_id,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        old_environment = {key: os.environ.get(key) for key in environment}
        old_path = list(sys.path)
        try:
            os.environ.update(environment)
            sys.path.insert(0, str(source_dir))
            from inkypi import build_application

            app = build_application(dev_mode=True)
            response = app.test_client().get("/healthz")
            if response.status_code != 200:
                raise PreflightError("no-hardware application probe failed healthz")
            body = response.get_json(silent=True) or {}
            if body.get("release_id") != release_id:
                raise PreflightError("no-hardware application probe reported wrong release")
        finally:
            sys.path[:] = old_path
            for key, value in old_environment.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument(
        "--skip-app-probe",
        action="store_true",
        help="validate files and config only (intended for packaging diagnostics)",
    )
    args = parser.parse_args(argv)
    try:
        validate_release_tree(
            args.release_root,
            expected_release_id=args.release_id,
        )
        capture_release_migration_expectations(
            args.release_root,
            args.config,
            args.release_id,
        )
        if args.skip_app_probe:
            with tempfile.TemporaryDirectory(prefix="inkypi-config-preflight-") as temp:
                prepare_config_copy(args.config, Path(temp) / "device_dev.json")
        else:
            run_no_hardware_probe(args.release_root, args.config, args.release_id)
    except PreflightError as error:
        print(f"InkyPi release preflight failed: {error}", file=sys.stderr)
        return 1
    print(f"InkyPi release preflight passed: {args.release_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
