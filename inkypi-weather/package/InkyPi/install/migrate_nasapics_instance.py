#!/usr/bin/env python3
"""Debug-only exact NASAPics migration interface.

Production uses the release-bound preflight expectation consumed by Config
inside the service process. This command exists for isolated diagnostics and
tests; it must not be used as the live deployment mutation path.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from config import Config  # noqa: E402
from nasapics_migration import (  # noqa: E402
    ExpectedNasapicsIdentity,
    TARGET_SETTINGS,
    migrate_nasapics_instance,
)
from runtime_paths import RuntimePaths  # noqa: E402


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _runtime_paths(config_file: Path) -> RuntimePaths:
    return RuntimePaths(
        release_id="migration-debug",
        config_file=config_file.resolve(),
        data_dir=Path("/var/lib/inkypi/data"),
        cache_dir=Path("/var/cache/inkypi"),
        env_file=Path("/etc/inkypi/inkypi.env"),
        display_dir=Path("/var/lib/inkypi/display"),
        current_image_file=Path("/var/lib/inkypi/display/current_image.png"),
        plugin_image_dir=Path("/var/lib/inkypi/plugins"),
        flask_secret_file=Path("/var/lib/inkypi/config/flask_secret"),
    )


def _thaw_mapping(value) -> dict:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): (
            _thaw_mapping(item)
            if isinstance(item, Mapping)
            else list(item)
            if isinstance(item, tuple)
            else item
        )
        for key, item in value.items()
    }


def _snapshot_summary(snapshot, *, include_approved_settings: bool) -> dict:
    summary = {
        "instance_uuid": snapshot.instance_uuid,
        "structural_generation": snapshot.structural_generation,
        "settings_revision": snapshot.settings_revision,
        "settings_keys": sorted(str(key) for key in snapshot.settings),
        "refresh": _thaw_mapping(snapshot.refresh),
    }
    if include_approved_settings:
        summary["approved_settings"] = dict(TARGET_SETTINGS)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-uuid", required=True)
    parser.add_argument(
        "--expected-generation",
        type=_positive_integer,
        required=True,
    )
    parser.add_argument(
        "--expected-settings-revision",
        type=_positive_integer,
        required=True,
    )
    args = parser.parse_args(argv)

    try:
        config = Config(runtime_paths=_runtime_paths(args.config))
        result = migrate_nasapics_instance(
            config,
            expected=ExpectedNasapicsIdentity(
                instance_uuid=args.expected_uuid,
                structural_generation=args.expected_generation,
                settings_revision=args.expected_settings_revision,
            ),
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(error).__name__,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "playlist_name": result.playlist_name,
                "before": _snapshot_summary(
                    result.before,
                    include_approved_settings=False,
                ),
                "after": _snapshot_summary(
                    result.after,
                    include_approved_settings=True,
                ),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
