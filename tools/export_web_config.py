#!/usr/bin/env python3
"""Export one InkyPi device configuration for the read-only web portal."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


FORMAT_VERSION = 1
DEFAULT_SECRET_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "inkypi-weather"
    / "package"
    / "InkyPi"
    / "src"
    / "config"
    / "secret_schema.json"
)
DEFAULT_PLUGINS_ROOT = Path(__file__).resolve().parents[1] / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins"
_DEVICE_FIELDS = (
    "image_settings",
    "name",
    "orientation",
    "plugin_cycle_interval_seconds",
    "resolution",
    "time_format",
    "timezone",
)
_PLUGIN_SECRET_FIELDS = {
    ("bambu_monitor", "ACCESS_CODE"): "BAMBU_ACCESS_CODE",
    ("box_office_top_movies", "TMDB_API_KEY"): "TMDB_API_KEY",
    ("box_office_top_movies", "TMDB_BEARER_TOKEN"): "TMDB_BEARER_TOKEN",
    ("dota_profile_dashboard", "API_KEY"): "OPENDOTA_API_KEY",
    ("flight_radar", "GOOGLE_MAPS_API_KEY"): "GOOGLE_MAPS_API_KEY",
    ("lol_info", "API_KEY"): "RIOT_API_KEY",
    ("species_radar", "GOOGLE_MAPS_API_KEY"): "GOOGLE_MAPS_API_KEY",
    ("ticketmaster_events", "API_KEY"): "TICKETMASTER_API_KEY",
    ("wow_profile_dashboard", "ACCESS_TOKEN"): "BLIZZARD_USER_ACCESS_TOKEN",
    ("wow_profile_dashboard", "CLIENT_ID"): "BLIZZARD_CLIENT_ID",
    ("wow_profile_dashboard", "CLIENT_SECRET"): "BLIZZARD_CLIENT_SECRET",
    ("wow_profile_dashboard", "USER_ACCESS_TOKEN"): "BLIZZARD_USER_ACCESS_TOKEN",
}
_SENSITIVE_PARTS = frozenset(
    {
        "access_code",
        "api_hash",
        "api_key",
        "apikey",
        "auth",
        "bearer",
        "client_id",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "key",
        "passwd",
        "password",
        "phpsessid",
        "secret",
        "session",
        "token",
    }
)
_UPLOAD_EXTENSIONS = frozenset({".avif", ".csv", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".pdf", ".png", ".webp"})


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _normalized_env_name(key: object) -> str:
    text = str(key)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    if not text:
        return "UNMAPPED_SECRET"
    if text[0].isdigit():
        text = f"SECRET_{text}"
    return text


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_env_name(key).casefold()
    if normalized in _SENSITIVE_PARTS:
        return True
    return any(normalized.endswith(f"_{part}") or normalized.startswith(f"{part}_") for part in _SENSITIVE_PARTS)


def _secret_lookup(schema_document: dict[str, Any]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for entry in schema_document.get("entries", []):
        canonical = entry["canonical"]
        metadata = {
            "name": canonical,
            "source": "schema",
            "value_type": entry.get("value_type", "secret"),
        }
        for name in (canonical, *entry.get("aliases", [])):
            lookup[str(name).casefold()] = metadata
    return lookup


def _redact_string(
    value: str,
    *,
    lookup: dict[str, dict[str, str]],
    required: dict[str, dict[str, str]],
    location: tuple[str, ...],
) -> str:
    authorization = re.match(r"^\s*(bearer|basic)\s+\S+\s*$", value, re.IGNORECASE)
    if authorization:
        scheme = authorization.group(1).casefold()
        name = "BEARER_TOKEN" if scheme == "bearer" else "BASIC_CREDENTIALS"
        required[name] = {
            "name": name,
            "source": "heuristic",
            "value_type": "secret",
        }
        return f"${{{name}}}"

    parts = urlsplit(value)
    if "@" in parts.netloc:
        userinfo, public_netloc = parts.netloc.rsplit("@", 1)
        if userinfo:
            location_digest = hashlib.sha256("\0".join(location).encode("utf-8")).hexdigest()[:24].upper()
            name = f"URL_USERINFO_{location_digest}"
            required[name] = {
                "name": name,
                "source": "heuristic",
                "value_type": "secret",
            }
            parts = parts._replace(netloc=f"${{{name}}}@{public_netloc}")
            value = urlunsplit(parts)

    if "?" not in value:
        return value
    parts = urlsplit(value)
    if not parts.query:
        return value
    changed = False
    query: list[tuple[str, str]] = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        declared = lookup.get(key.casefold())
        if declared is None and not _is_sensitive_key(key):
            query.append((key, item))
            continue
        metadata = declared or {
            "name": _normalized_env_name(key),
            "source": "heuristic",
            "value_type": "secret",
        }
        required[metadata["name"]] = dict(metadata)
        query.append((key, f"${{{metadata['name']}}}"))
        changed = True
    if not changed:
        return value
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query, doseq=True, quote_via=quote, safe="${}"),
            parts.fragment,
        )
    )


def _redact(
    value: Any,
    *,
    lookup: dict[str, dict[str, str]],
    required: dict[str, dict[str, str]],
    plugin_id: str | None = None,
    location: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, dict):
        active_plugin_id = str(value.get("plugin_id") or plugin_id or "")
        result = {}
        for key, item in value.items():
            env_reference = (
                lookup.get(item.casefold())
                if _normalized_env_name(key).endswith("_ENV") and isinstance(item, str)
                else None
            )
            if env_reference is not None:
                required[env_reference["name"]] = dict(env_reference)
                result[key] = f"${{{env_reference['name']}}}"
                continue
            plugin_secret_name = _PLUGIN_SECRET_FIELDS.get((active_plugin_id, _normalized_env_name(key)))
            declared = (
                lookup.get(plugin_secret_name.casefold())
                if plugin_secret_name is not None
                else lookup.get(str(key).casefold())
            )
            if declared is not None or _is_sensitive_key(key):
                metadata = declared or {
                    "name": _normalized_env_name(key),
                    "source": "heuristic",
                    "value_type": "secret",
                }
                required[metadata["name"]] = dict(metadata)
                result[key] = f"${{{metadata['name']}}}"
            else:
                result[key] = _redact(
                    item,
                    lookup=lookup,
                    required=required,
                    plugin_id=active_plugin_id,
                    location=(*location, str(key)),
                )
        return result
    if isinstance(value, list):
        return [
            _redact(
                item,
                lookup=lookup,
                required=required,
                plugin_id=plugin_id,
                location=(*location, str(index)),
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        return _redact_string(
            value,
            lookup=lookup,
            required=required,
            location=location,
        )
    return value


def _is_resource_setting(key: object) -> bool:
    normalized = _normalized_env_name(key).casefold()
    return any(part in normalized.split("_") for part in ("file", "files", "path", "upload"))


def _record_unavailable(
    *,
    reason: str,
    source_name: str,
    setting: str,
    instance_uuid: str,
    unavailable: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    reference = {"instance_uuid": instance_uuid, "setting": setting}
    unavailable.append(
        {
            "reason": reason,
            "references": [reference],
            "source_name": source_name,
            "status": "unavailable",
        }
    )
    warnings.append(
        {
            "code": "resource_unavailable",
            **reference,
            "reason": reason,
            "source_name": source_name,
        }
    )


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _contains_link(root: Path, relative: Path) -> bool:
    current = root
    if _is_link_or_reparse(current):
        return True
    for part in relative.parts:
        current = current / part
        if _is_link_or_reparse(current):
            return True
    return False


def _package_resource_value(
    value: Any,
    *,
    setting: str,
    instance_uuid: str,
    uploads_root: Path,
    resources_dir: Path,
    available: dict[str, dict[str, Any]],
    unavailable: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> Any:
    if isinstance(value, list):
        return [
            _package_resource_value(
                item,
                setting=setting,
                instance_uuid=instance_uuid,
                uploads_root=uploads_root,
                resources_dir=resources_dir,
                available=available,
                unavailable=unavailable,
                warnings=warnings,
            )
            for item in value
        ]
    if not isinstance(value, str) or not value:
        return value
    if re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*\}", value):
        return value
    source = Path(value)
    source_name = source.name
    if not source.is_absolute() and ".." in source.parts:
        _record_unavailable(
            reason="path_traversal",
            source_name=source_name,
            setting=setting,
            instance_uuid=instance_uuid,
            unavailable=unavailable,
            warnings=warnings,
        )
        return None
    root_lexical = Path(os.path.abspath(uploads_root))
    source_lexical = source if source.is_absolute() else root_lexical / source
    source_lexical = Path(os.path.abspath(source_lexical))
    try:
        relative = source_lexical.relative_to(root_lexical)
    except ValueError:
        relative = None
    if relative is not None and _contains_link(root_lexical, relative):
        _record_unavailable(
            reason="symlink",
            source_name=source_name,
            setting=setting,
            instance_uuid=instance_uuid,
            unavailable=unavailable,
            warnings=warnings,
        )
        return None
    source = source_lexical.resolve()
    root = root_lexical.resolve()
    if source.parent != root and root not in source.parents:
        _record_unavailable(
            reason="outside_uploads_root",
            source_name=source_name,
            setting=setting,
            instance_uuid=instance_uuid,
            unavailable=unavailable,
            warnings=warnings,
        )
        return None
    if not source.exists():
        _record_unavailable(
            reason="missing",
            source_name=source_name,
            setting=setting,
            instance_uuid=instance_uuid,
            unavailable=unavailable,
            warnings=warnings,
        )
        return None
    if not source.is_file():
        _record_unavailable(
            reason="not_regular_file",
            source_name=source_name,
            setting=setting,
            instance_uuid=instance_uuid,
            unavailable=unavailable,
            warnings=warnings,
        )
        return None
    if source.suffix.casefold() not in _UPLOAD_EXTENSIONS:
        _record_unavailable(
            reason="unsupported_extension",
            source_name=source_name,
            setting=setting,
            instance_uuid=instance_uuid,
            unavailable=unavailable,
            warnings=warnings,
        )
        return None
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    relative_path = f"resources/{digest}{source.suffix.casefold()}"
    target = resources_dir / f"{digest}{source.suffix.casefold()}"
    if not target.exists():
        target.write_bytes(payload)
    entry = available.setdefault(
        relative_path,
        {
            "path": relative_path,
            "references": [],
            "sha256": digest,
            "size": len(payload),
            "status": "available",
        },
    )
    reference = {"instance_uuid": instance_uuid, "setting": setting}
    if reference not in entry["references"]:
        entry["references"].append(reference)
    return relative_path


def _package_instance_resources(
    playlists: list[Any],
    *,
    uploads_root: Path,
    resources_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    available: dict[str, dict[str, Any]] = {}
    unavailable: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for playlist in playlists:
        if not isinstance(playlist, dict):
            continue
        for instance in playlist.get("plugins", []):
            if not isinstance(instance, dict):
                continue
            settings = instance.get("plugin_settings")
            if not isinstance(settings, dict):
                continue
            instance_uuid = str(instance.get("instance_uuid") or "")
            for setting, value in list(settings.items()):
                if not _is_resource_setting(setting):
                    continue
                settings[setting] = _package_resource_value(
                    value,
                    setting=str(setting),
                    instance_uuid=instance_uuid,
                    uploads_root=uploads_root,
                    resources_dir=resources_dir,
                    available=available,
                    unavailable=unavailable,
                    warnings=warnings,
                )
    return [available[path] for path in sorted(available)] + unavailable, warnings


def _plugin_catalog(document: dict[str, Any], plugins_root: Path) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for manifest_path in sorted(plugins_root.glob("*/plugin-info.json")):
        manifest = _read_json(manifest_path)
        plugin_id = manifest.get("id")
        class_name = manifest.get("class")
        if not isinstance(plugin_id, str) or not isinstance(class_name, str):
            continue
        by_id[plugin_id] = {
            "class": class_name,
            "disabled": bool(manifest.get("disabled", False)),
            "display_name": str(manifest.get("display_name") or plugin_id),
            "id": plugin_id,
        }
    ordered_ids: list[str] = []
    for plugin_id in document.get("plugin_order", []):
        if plugin_id in by_id and plugin_id not in ordered_ids:
            ordered_ids.append(plugin_id)
    ordered_ids.extend(sorted(plugin_id for plugin_id in by_id if plugin_id not in ordered_ids))
    return [by_id[plugin_id] for plugin_id in ordered_ids]


def _bundle_file_record(output_path: Path, relative_path: str) -> dict[str, Any]:
    payload = (output_path / relative_path).read_bytes()
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def export_config(
    config_path: Path,
    output_path: Path,
    schema_path: Path,
    uploads_root: Path,
    plugins_root: Path,
) -> None:
    document = _read_json(config_path)
    schema_document = _read_json(schema_path)
    lookup = _secret_lookup(schema_document)
    required: dict[str, dict[str, str]] = {}
    playlist_config = document.get("playlist_config") or {}
    playlists = _redact(
        playlist_config.get("playlists", []),
        lookup=lookup,
        required=required,
        location=("playlists",),
    )
    output_path.mkdir(parents=True, exist_ok=True)
    resources_dir = output_path / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    resources, warnings = _package_instance_resources(
        playlists,
        uploads_root=uploads_root,
        resources_dir=resources_dir,
    )
    _write_json(
        output_path / "web_config.json",
        {
            "format_version": FORMAT_VERSION,
            "source": {
                "config_revision": document.get("config_revision"),
                "schema_version": document.get("schema_version"),
            },
            "device": {field: document.get(field) for field in _DEVICE_FIELDS},
            "plugin_catalog": _plugin_catalog(document, plugins_root),
            "timezone": document.get("timezone"),
            "active_playlist": playlist_config.get("active_playlist"),
            "playlists": playlists,
        },
    )
    _write_json(
        output_path / "required_secrets.json",
        {
            "format_version": FORMAT_VERSION,
            "secrets": [required[name] for name in sorted(required)],
        },
    )
    _write_json(
        output_path / "manifest.json",
        {
            "files": [
                _bundle_file_record(output_path, relative_path)
                for relative_path in sorted(
                    [
                        "required_secrets.json",
                        "web_config.json",
                        *[resource["path"] for resource in resources if resource.get("status") == "available"],
                    ]
                )
            ],
            "format_version": FORMAT_VERSION,
            "resources": resources,
            "warnings": warnings,
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a deterministic, redacted InkyPi web import bundle.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--uploads-root", type=Path)
    parser.add_argument(
        "--secret-schema",
        type=Path,
        default=DEFAULT_SECRET_SCHEMA,
    )
    parser.add_argument(
        "--plugins-root",
        type=Path,
        default=DEFAULT_PLUGINS_ROOT,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    uploads_root = args.uploads_root or args.config.resolve().parent.parent / "data" / "uploads"
    export_config(
        args.config,
        args.output,
        args.secret_schema,
        uploads_root,
        args.plugins_root,
    )
    print("Exported redacted web bundle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
