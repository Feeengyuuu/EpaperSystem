"""Build release ZIPs while excluding device-owned runtime font files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePath
import sys
import zipfile


NASAPICS_MIGRATION_ID = "nasapics_space_weather_v1"
SUPPORTED_MIGRATIONS = frozenset({NASAPICS_MIGRATION_ID})
MIGRATION_REQUEST_MEMBER = "install/.release-migrations.json"
NASAPICS_EXPECTATION_MEMBER = (
    "install/.nasapics-space-weather-v1.expectation.json"
)
MIGRATION_CONTROL_MEMBERS = frozenset(
    {MIGRATION_REQUEST_MEMBER, NASAPICS_EXPECTATION_MEMBER}
)
MIGRATION_CONTROL_NAMES = frozenset(
    PurePath(member).name for member in MIGRATION_CONTROL_MEMBERS
)
EXCLUDED_NAMES = {
    ".env",
    ".git",
    ".pc-packages",
    ".pytest_cache",
    ".tmp",
    ".venv",
    ".venv-test",
    ".venv-codex",
    ".venv-local",
    "__pycache__",
    "tmp",
}
YAHEI_SUFFIXES = {".ttc", ".ttf"}


def is_device_owned_yahei_font(path: PurePath) -> bool:
    """Return whether a release member is a device-owned YaHei binary."""

    return (
        path.name.casefold().startswith("msyh")
        and path.suffix.casefold() in YAHEI_SUFFIXES
    )


def is_runtime_plugin_cache(path: PurePath) -> bool:
    """Return whether a member belongs to a plugin-owned runtime cache."""

    parts = tuple(part.casefold() for part in path.parts)
    return (
        len(parts) >= 3
        and parts[:2] == ("src", "plugins")
        and any(
            part == "cache"
            or (part.startswith(".") and part.endswith("_cache"))
            for part in parts[2:-1]
        )
    )


def is_runtime_display_image(path: PurePath) -> bool:
    """Return whether a member is the mutable last-displayed frame."""

    return tuple(part.casefold() for part in path.parts) == (
        "src",
        "static",
        "images",
        "current_image.png",
    )


def build_release_archive(
    source_root: Path,
    artifact: Path,
    *,
    migrations=(),
) -> Path:
    root = Path(source_root).resolve()
    output = Path(artifact)
    requested_migrations = tuple(dict.fromkeys(migrations))
    unsupported = set(requested_migrations) - SUPPORTED_MIGRATIONS
    if unsupported:
        raise ValueError(f"unsupported release migration: {sorted(unsupported)[0]}")
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if any(part in EXCLUDED_NAMES for part in relative.parts):
                continue
            if (
                relative.as_posix() in MIGRATION_CONTROL_MEMBERS
                or relative.name in MIGRATION_CONTROL_NAMES
            ):
                continue
            if (
                is_device_owned_yahei_font(relative)
                or is_runtime_plugin_cache(relative)
                or is_runtime_display_image(relative)
            ):
                continue
            if path.is_symlink() or not path.is_file() or path.suffix == ".pyc":
                continue
            archive.write(path, relative.as_posix())
        if requested_migrations:
            request = {
                "schema_version": 1,
                "migrations": list(requested_migrations),
            }
            archive.writestr(
                MIGRATION_REQUEST_MEMBER,
                json.dumps(
                    request,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                + "\n",
            )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migration",
        action="append",
        choices=sorted(SUPPORTED_MIGRATIONS),
        default=[],
    )
    parser.add_argument("source_root", type=Path)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    build_release_archive(
        args.source_root,
        args.artifact,
        migrations=args.migration,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
