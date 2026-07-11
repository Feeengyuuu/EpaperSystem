#!/usr/bin/env python3
"""Validate an extracted InkyPi release without opening ports or display hardware."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


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
    "install/bootstrap_admin.py",
    "install/requirements.txt",
    "cli/inkypi-plugin",
    ".release-id",
)


class PreflightError(RuntimeError):
    pass


def validate_release_tree(release_root) -> None:
    root = Path(release_root)
    if not root.is_dir() or root.is_symlink():
        raise PreflightError(f"release root is not a regular directory: {root}")
    missing = [relative for relative in REQUIRED_RELEASE_PATHS if not (root / relative).is_file()]
    if missing:
        raise PreflightError(f"release is missing required files: {', '.join(missing)}")
    release_id = (root / ".release-id").read_text(encoding="utf-8").strip()
    if not release_id or len(release_id) > 64:
        raise PreflightError("release identity file is empty or too long")


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
        validate_release_tree(args.release_root)
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
