"""One-shot import of exact device cache rasters into the publication ledger."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from PIL import Image

from publication import (
    AdapterRegistry,
    AssetInput,
    PlaylistEntry,
    PlaylistSpec,
    ProducerBatch,
    ProductionFailure,
    PublicationDraft,
    PublicationModule,
    RasterAssetLegacyAdapter,
    TimeWindow,
    WorkBudget,
)


_FORMAT_VERSION = 1
_MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_MAX_RASTER_BYTES = 24 * 1024 * 1024
_MAX_IMAGE_PIXELS = 24 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RASTER_PATH = re.compile(r"rasters/([0-9a-f]{64})\.png\Z")
_SNAPSHOT_RASTER_PATH = re.compile(r"frames/[A-Za-z0-9][A-Za-z0-9._-]{0,190}\.png\Z")
_MAX_SQLITE_INTEGER = 2**63 - 1


@dataclass(frozen=True, slots=True)
class RasterAttachmentReport:
    configured: int
    available: int
    unavailable: int


class _StaticProducer:
    def __init__(self, batch: ProducerBatch) -> None:
        self._batch = batch

    def collect_due(self, _budget: WorkBudget) -> ProducerBatch:
        return self._batch


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_SQLITE_INTEGER:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _safe_text(value: object, field_name: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = value.strip()
    if (not allow_empty and not cleaned) or len(cleaned) > maximum:
        raise ValueError(f"{field_name} is invalid")
    if any(ord(character) < 32 for character in cleaned):
        raise ValueError(f"{field_name} is invalid")
    return cleaned


def _link_like(value: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(value, "st_file_attributes", 0)
    return stat.S_ISLNK(value.st_mode) or bool(reparse_flag and attributes & reparse_flag)


def _same_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _validated_parts(relative_path: str) -> tuple[str, ...]:
    if (
        not isinstance(relative_path, str)
        or "\\" in relative_path
        or ":" in relative_path
        or "\x00" in relative_path
    ):
        raise ValueError("bundle path is invalid")
    pure = PurePosixPath(relative_path)
    if (
        pure.is_absolute()
        or not pure.parts
        or pure.as_posix() != relative_path
        or any(
            part in {"", ".", ".."} or part.endswith((" ", "."))
            for part in pure.parts
        )
    ):
        raise ValueError("bundle path is invalid")
    return pure.parts


def _read_bound_file(root: Path, relative_path: str, maximum: int) -> bytes:
    parts = _validated_parts(relative_path)
    root = Path(os.path.abspath(os.fspath(root)))
    path = root.joinpath(*parts)
    descriptors: list[tuple[Path, os.stat_result]] = []
    file_descriptor: int | None = None
    try:
        root_stat = os.lstat(root)
        if not stat.S_ISDIR(root_stat.st_mode) or _link_like(root_stat):
            raise ValueError("bundle root is not a safe directory")
        descriptors.append((root, root_stat))
        parent = root
        for part in parts[:-1]:
            parent /= part
            parent_stat = os.lstat(parent)
            if not stat.S_ISDIR(parent_stat.st_mode) or _link_like(parent_stat):
                raise ValueError("bundle path crosses an unsafe directory")
            descriptors.append((parent, parent_stat))

        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or _link_like(before):
            raise ValueError("bundle file is not a safe regular file")
        if before.st_size < 0 or before.st_size > maximum:
            raise ValueError("bundle file is too large")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(path, flags)
        opened = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened.st_mode) or _link_like(opened) or not _same_snapshot(before, opened):
            raise ValueError("bundle file changed while opening")
        with os.fdopen(os.dup(file_descriptor), "rb") as handle:
            body = handle.read(maximum + 1)
        if len(body) > maximum or len(body) != opened.st_size:
            raise ValueError("bundle file size changed while reading")
        after = os.lstat(path)
        if not _same_snapshot(opened, after):
            raise ValueError("bundle file changed while reading")
        for directory, expected in descriptors:
            current = os.lstat(directory)
            if not stat.S_ISDIR(current.st_mode) or _link_like(current) or not _same_snapshot(expected, current):
                raise ValueError("bundle directory changed while reading")
        return body
    except OSError as exc:
        raise ValueError(f"bundle file is unavailable: {relative_path}") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)


def _load_json(root: Path, relative_path: str) -> dict:
    body = _read_bound_file(root, relative_path, _MAX_DOCUMENT_BYTES)
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{relative_path} is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or document.get("format_version") != _FORMAT_VERSION:
        raise ValueError(f"unsupported {relative_path} format")
    return document


def _file_records(manifest: Mapping[str, object]) -> dict[str, dict]:
    records = manifest.get("files")
    if not isinstance(records, list):
        raise ValueError("manifest files must be an array")
    by_path: dict[str, dict] = {}
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ValueError("manifest file record must be an object")
        path = raw.get("path")
        digest = raw.get("sha256")
        size = raw.get("size")
        if not isinstance(path, str) or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError("manifest file record is invalid")
        _validated_parts(path)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("manifest file record is invalid")
        if path in by_path:
            raise ValueError("manifest file paths must be unique")
        by_path[path] = dict(raw)
    return by_path


def _verified_file(
    root: Path,
    file_records: Mapping[str, Mapping[str, object]],
    path: str,
    maximum: int,
) -> bytes:
    record = file_records.get(path)
    if record is None:
        raise ValueError(f"manifest does not cover {path}")
    body = _read_bound_file(root, path, maximum)
    if len(body) != record["size"] or hashlib.sha256(body).hexdigest() != record["sha256"]:
        raise ValueError(f"bundle integrity check failed: {path}")
    return body


def _atomic_write(root: Path, relative_path: str, body: bytes) -> None:
    parts = _validated_parts(relative_path)
    root = Path(os.path.abspath(os.fspath(root)))
    root_stat = os.lstat(root)
    if not stat.S_ISDIR(root_stat.st_mode) or _link_like(root_stat):
        raise ValueError("bundle root is not a safe directory")
    parent = root.joinpath(*parts[:-1]) if len(parts) > 1 else root
    parent.mkdir(parents=True, exist_ok=True)
    parent_stat = os.lstat(parent)
    if not stat.S_ISDIR(parent_stat.st_mode) or _link_like(parent_stat):
        raise ValueError("bundle destination is not a safe directory")
    destination = root.joinpath(*parts)
    temporary = parent / f".{parts[-1]}.{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_content_addressed_raster(root: Path, digest: str, body: bytes) -> str:
    relative_path = f"rasters/{digest}.png"
    try:
        existing = _read_bound_file(root, relative_path, _MAX_RASTER_BYTES)
    except ValueError as exc:
        destination = root / "rasters" / f"{digest}.png"
        if destination.exists() or destination.is_symlink():
            raise ValueError("existing raster destination is unsafe or corrupt") from exc
        _atomic_write(root, relative_path, body)
    else:
        if hashlib.sha256(existing).hexdigest() != digest or existing != body:
            raise ValueError("existing content-addressed raster is corrupt")
    return relative_path


def _configured_snapshot_targets(document: Mapping[str, object]) -> dict[str, dict]:
    raw_playlists = document.get("playlists")
    if not isinstance(raw_playlists, list):
        raise ValueError("publication playlists must be an array")
    configured: dict[str, dict] = {}
    playlist_names: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for raw_playlist in raw_playlists:
        if not isinstance(raw_playlist, Mapping):
            raise ValueError("publication playlist must be an object")
        playlist_name = _safe_text(raw_playlist.get("name"), "playlist name", 500)
        if playlist_name in playlist_names:
            raise ValueError("playlist names must be unique")
        playlist_names.add(playlist_name)
        raw_instances = raw_playlist.get("plugins")
        if not isinstance(raw_instances, list):
            raise ValueError("playlist plugins must be an array")
        for raw_instance in raw_instances:
            if not isinstance(raw_instance, Mapping):
                raise ValueError("playlist instance must be an object")
            instance_uuid = _safe_text(raw_instance.get("instance_uuid"), "instance_uuid", 200)
            if instance_uuid in configured:
                raise ValueError("instance_uuid values must be globally unique")
            plugin_id = _safe_text(raw_instance.get("plugin_id"), "plugin_id", 200)
            title = _safe_text(raw_instance.get("name") or plugin_id, "instance name", 160)
            identity = (plugin_id, title)
            if identity in identities:
                raise ValueError("plugin instance names must be globally unique")
            identities.add(identity)
            configured[instance_uuid] = {
                "settings_revision": _safe_positive_int(
                    raw_instance.get("settings_revision", 1),
                    "settings_revision",
                ),
                "structural_generation": _safe_positive_int(
                    raw_instance.get("structural_generation", 1),
                    "structural_generation",
                ),
            }
    active_playlist = document.get("active_playlist")
    if active_playlist is not None and (
        not isinstance(active_playlist, str)
        or (active_playlist.strip() and active_playlist.strip() not in playlist_names)
    ):
        raise ValueError("active playlist does not exist")
    return configured


def attach_cached_raster_snapshot(
    bundle_dir: str | Path,
    snapshot_dir: str | Path,
) -> RasterAttachmentReport:
    """Attach a selected device-cache snapshot to a redacted export bundle."""

    bundle_root = Path(bundle_dir).expanduser()
    snapshot_root = Path(snapshot_dir).expanduser()
    manifest = _load_json(bundle_root, "manifest.json")
    file_records = _file_records(manifest)
    config_body = _verified_file(bundle_root, file_records, "web_config.json", _MAX_DOCUMENT_BYTES)
    try:
        document = json.loads(config_body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("web_config.json is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or document.get("format_version") != _FORMAT_VERSION:
        raise ValueError("unsupported web_config.json format")
    expected_resolution = _resolution(document)
    configured = _configured_snapshot_targets(document)

    snapshot = _load_json(snapshot_root, "snapshot.json")
    captured_at = _parse_timestamp(snapshot.get("captured_at"), "captured_at")
    raw_rasters = snapshot.get("rasters")
    if not isinstance(raw_rasters, list):
        raise ValueError("snapshot rasters must be an array")
    selected: dict[str, dict] = {}
    bodies: dict[str, bytes] = {}
    for raw in raw_rasters:
        if not isinstance(raw, Mapping):
            raise ValueError("snapshot raster record must be an object")
        instance_uuid = _safe_text(raw.get("instance_uuid"), "raster instance_uuid", 200)
        if instance_uuid in selected:
            raise ValueError("raster instance_uuid values must be unique")
        target = configured.get(instance_uuid)
        if target is None:
            raise ValueError("snapshot contains an unconfigured instance_uuid")
        status_value = raw.get("status")
        structural_generation = _safe_positive_int(
            raw.get("structural_generation"),
            "raster structural_generation",
        )
        settings_revision = _safe_positive_int(
            raw.get("settings_revision"),
            "raster settings_revision",
        )
        if (
            structural_generation != target["structural_generation"]
            or settings_revision != target["settings_revision"]
        ):
            raise ValueError("raster revision does not match web_config.json")
        common = {
            "instance_uuid": instance_uuid,
            "settings_revision": settings_revision,
            "status": status_value,
            "structural_generation": structural_generation,
        }
        if status_value == "unavailable":
            if raw.get("reason") != "missing":
                raise ValueError("unavailable raster reason is invalid")
            selected[instance_uuid] = {**common, "reason": "missing"}
            continue
        if status_value != "available":
            raise ValueError("raster status is invalid")
        source_path = raw.get("path")
        if not isinstance(source_path, str) or _SNAPSHOT_RASTER_PATH.fullmatch(source_path) is None:
            raise ValueError("snapshot raster path is invalid")
        source_updated_at = _parse_timestamp(raw.get("source_updated_at"), "raster source_updated_at")
        body = _read_bound_file(snapshot_root, source_path, _MAX_RASTER_BYTES)
        _decode_png_size(body, expected_resolution)
        digest = hashlib.sha256(body).hexdigest()
        bodies[digest] = body
        selected[instance_uuid] = {
            **common,
            "path": f"rasters/{digest}.png",
            "sha256": digest,
            "size": len(body),
            "source_updated_at": _iso(source_updated_at),
        }

    if set(selected) != set(configured):
        raise ValueError("snapshot must declare exactly one raster state per configured instance")

    for digest, body in sorted(bodies.items()):
        _persist_content_addressed_raster(bundle_root, digest, body)
    retained_files = {
        path: dict(record)
        for path, record in file_records.items()
        if not path.startswith("rasters/")
    }
    for digest, body in bodies.items():
        path = f"rasters/{digest}.png"
        retained_files[path] = {
            "path": path,
            "sha256": digest,
            "size": len(body),
        }
    updated_manifest = dict(manifest)
    updated_manifest["captured_at"] = _iso(captured_at)
    updated_manifest["files"] = [retained_files[path] for path in sorted(retained_files)]
    updated_manifest["rasters"] = [selected[instance_uuid] for instance_uuid in configured]
    manifest_body = (
        json.dumps(updated_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write(bundle_root, "manifest.json", manifest_body)
    available = sum(item["status"] == "available" for item in selected.values())
    return RasterAttachmentReport(
        configured=len(configured),
        available=available,
        unavailable=len(configured) - available,
    )


def _decode_png_size(body: bytes, expected_resolution: tuple[int, int]) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(body)) as source:
            if source.format != "PNG":
                raise ValueError("raster is not PNG")
            width, height = source.size
            source.verify()
    except Exception as exc:
        raise ValueError("raster PNG is invalid") from exc
    if width <= 0 or height <= 0 or width * height > _MAX_IMAGE_PIXELS:
        raise ValueError("raster dimensions are invalid")
    if (width, height) != expected_resolution:
        raise ValueError("raster dimensions do not match the exported device")
    return width, height


def _config_revision(document: Mapping[str, object]) -> int:
    source = document.get("source")
    value = source.get("config_revision") if isinstance(source, Mapping) else None
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_SQLITE_INTEGER:
        return value
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return int(hashlib.sha256(canonical).hexdigest()[:15], 16)


def _playlist_slug(name: str, order: int) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"playlist-{order + 1}-{digest}"


def _resolution(document: Mapping[str, object]) -> tuple[int, int]:
    device = document.get("device")
    value = device.get("resolution") if isinstance(device, Mapping) else None
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value)
    ):
        raise ValueError("exported device resolution is invalid")
    width, height = value
    if width * height > _MAX_IMAGE_PIXELS:
        raise ValueError("exported device resolution is too large")
    return width, height


def _refresh_interval(instance: Mapping[str, object], document: Mapping[str, object]) -> int:
    refresh = instance.get("refresh")
    value = refresh.get("interval") if isinstance(refresh, Mapping) else None
    if value is None:
        device = document.get("device")
        value = device.get("plugin_cycle_interval_seconds") if isinstance(device, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int):
        return 300
    return max(30, min(7 * 24 * 60 * 60, value))


def _batch_from_bundle(root: Path) -> tuple[ProducerBatch, str, int, datetime]:
    manifest = _load_json(root, "manifest.json")
    file_records = _file_records(manifest)
    config_body = _verified_file(root, file_records, "web_config.json", _MAX_DOCUMENT_BYTES)
    try:
        document = json.loads(config_body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("web_config.json is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or document.get("format_version") != _FORMAT_VERSION:
        raise ValueError("unsupported web_config.json format")
    captured_at = _parse_timestamp(manifest.get("captured_at"), "captured_at")
    expected_resolution = _resolution(document)

    raw_rasters = manifest.get("rasters")
    if not isinstance(raw_rasters, list):
        raise ValueError("manifest rasters must be an array")
    raster_records: dict[str, dict] = {}
    for raw in raw_rasters:
        if not isinstance(raw, Mapping):
            raise ValueError("raster record must be an object")
        instance_uuid = _safe_text(raw.get("instance_uuid"), "raster instance_uuid", 200)
        if instance_uuid in raster_records:
            raise ValueError("raster instance_uuid values must be unique")
        status_value = raw.get("status")
        if status_value not in {"available", "unavailable"}:
            raise ValueError("raster status is invalid")
        structural_generation = _safe_positive_int(
            raw.get("structural_generation"),
            "raster structural_generation",
        )
        settings_revision = _safe_positive_int(
            raw.get("settings_revision"),
            "raster settings_revision",
        )
        if status_value == "unavailable":
            if raw.get("reason") != "missing":
                raise ValueError("unavailable raster reason is invalid")
            raster_records[instance_uuid] = {
                "settings_revision": settings_revision,
                "status": status_value,
                "structural_generation": structural_generation,
            }
            continue
        path = raw.get("path")
        digest = raw.get("sha256")
        size = raw.get("size")
        match = _RASTER_PATH.fullmatch(path) if isinstance(path, str) else None
        if (
            match is None
            or not isinstance(digest, str)
            or match.group(1) != digest
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 < size <= _MAX_RASTER_BYTES
        ):
            raise ValueError("raster record is invalid")
        file_record = file_records.get(path)
        if file_record is None or file_record.get("sha256") != digest or file_record.get("size") != size:
            raise ValueError("raster record does not match the file manifest")
        source_updated_at = _parse_timestamp(raw.get("source_updated_at"), "raster source_updated_at")
        source_revision = int(source_updated_at.timestamp() * 1_000_000)
        _safe_positive_int(source_revision, "raster source_revision")
        body = _verified_file(root, file_records, path, _MAX_RASTER_BYTES)
        width, height = _decode_png_size(body, expected_resolution)
        raster_records[instance_uuid] = {
            "body": body,
            "height": height,
            "settings_revision": settings_revision,
            "source_revision": source_revision,
            "source_updated_at": source_updated_at,
            "status": status_value,
            "structural_generation": structural_generation,
            "width": width,
        }

    raw_playlists = document.get("playlists")
    if not isinstance(raw_playlists, list):
        raise ValueError("publication playlists must be an array")
    playlists: list[PlaylistSpec] = []
    configured: dict[str, dict] = {}
    configured_identities: set[tuple[str, str]] = set()
    playlist_names: set[str] = set()
    active_playlist: str | None = None
    configured_active = document.get("active_playlist")
    if configured_active is not None and not isinstance(configured_active, str):
        raise ValueError("active playlist is invalid")
    for playlist_order, raw_playlist in enumerate(raw_playlists):
        if not isinstance(raw_playlist, Mapping):
            raise ValueError("publication playlist must be an object")
        name = _safe_text(raw_playlist.get("name"), "playlist name", 500)
        if name in playlist_names:
            raise ValueError("playlist names must be unique")
        playlist_names.add(name)
        start = _safe_text(raw_playlist.get("start_time", "00:00"), "playlist start_time", 5)
        end = _safe_text(raw_playlist.get("end_time", "24:00"), "playlist end_time", 5)
        raw_instances = raw_playlist.get("plugins")
        if not isinstance(raw_instances, list):
            raise ValueError("playlist plugins must be an array")
        entries: list[PlaylistEntry] = []
        playlist_instances: set[str] = set()
        for instance_order, raw_instance in enumerate(raw_instances):
            if not isinstance(raw_instance, Mapping):
                raise ValueError("playlist instance must be an object")
            instance_uuid = _safe_text(raw_instance.get("instance_uuid"), "instance_uuid", 200)
            plugin_id = _safe_text(raw_instance.get("plugin_id"), "plugin_id", 200)
            title = _safe_text(raw_instance.get("name") or plugin_id, "instance name", 160)
            if instance_uuid in configured:
                raise ValueError("instance_uuid values must be globally unique")
            identity = (plugin_id, title)
            if identity in configured_identities:
                raise ValueError("plugin instance names must be globally unique")
            configured_identities.add(identity)
            settings_revision = _safe_positive_int(raw_instance.get("settings_revision", 1), "settings_revision")
            structural_generation = _safe_positive_int(
                raw_instance.get("structural_generation", 1),
                "structural_generation",
            )
            if instance_uuid in playlist_instances:
                raise ValueError("playlist instance_uuid values must be unique")
            playlist_instances.add(instance_uuid)
            metadata = {
                "instance_uuid": instance_uuid,
                "plugin_id": plugin_id,
                "title": title,
                "settings_revision": settings_revision,
                "structural_generation": structural_generation,
                "interval": _refresh_interval(raw_instance, document),
            }
            configured[instance_uuid] = metadata
            entries.append(PlaylistEntry(instance_uuid, title, plugin_id, instance_order))
        slug = _playlist_slug(name, playlist_order)
        playlists.append(
            PlaylistSpec(
                slug=slug,
                title=name,
                order=playlist_order,
                window=TimeWindow(start, end),
                entries=tuple(entries),
            )
        )
        if isinstance(configured_active, str) and configured_active.strip() in {name, slug}:
            active_playlist = slug

    if isinstance(configured_active, str) and configured_active.strip() and active_playlist is None:
        raise ValueError("active playlist does not exist")

    if set(raster_records) != set(configured):
        raise ValueError("manifest must declare exactly one raster state per configured instance")
    for instance_uuid, metadata in configured.items():
        raster = raster_records[instance_uuid]
        if (
            raster["settings_revision"] != metadata["settings_revision"]
            or raster["structural_generation"] != metadata["structural_generation"]
        ):
            raise ValueError("raster revision does not match web_config.json")
    fallback_revision = int(captured_at.timestamp() * 1_000_000)
    drafts: list[PublicationDraft] = []
    failures: list[ProductionFailure] = []
    for instance_uuid, metadata in configured.items():
        raster = raster_records[instance_uuid]
        if raster["status"] == "unavailable":
            failures.append(
                ProductionFailure(
                    instance_uuid=instance_uuid,
                    plugin_id=metadata["plugin_id"],
                    title=metadata["title"],
                    settings_revision=metadata["settings_revision"],
                    source_revision=fallback_revision,
                    attempted_at=captured_at,
                    reason_code="source_unavailable",
                )
            )
            continue
        generated_at = raster["source_updated_at"]
        interval = metadata["interval"]
        fresh_until = generated_at + timedelta(seconds=interval)
        stale_until = fresh_until + max(timedelta(hours=24), timedelta(seconds=interval * 4))
        title = metadata["title"]
        drafts.append(
            PublicationDraft(
                instance_uuid=instance_uuid,
                plugin_id=metadata["plugin_id"],
                title=title,
                settings_revision=metadata["settings_revision"],
                source_revision=raster["source_revision"],
                generated_at=generated_at,
                fresh_until=fresh_until,
                stale_until=stale_until,
                snapshot={
                    "rasterMetadata": {
                        "alt": f"{title} original plugin frame",
                        "data_mode": "cache",
                        "height": raster["height"],
                        "source_label": title,
                        "source_updated_at": _iso(generated_at),
                        "width": raster["width"],
                    }
                },
                source_assets=(AssetInput("raster", "image/png", raster["body"]),),
            )
        )

    device = document.get("device")
    timezone_name = device.get("timezone") if isinstance(device, Mapping) else None
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        timezone_name = "UTC"
    return (
        ProducerBatch(
            catalog_revision=_config_revision(document),
            drafts=tuple(drafts),
            playlists=tuple(playlists),
            failures=tuple(failures),
            active_playlist=active_playlist,
        ),
        timezone_name,
        len(configured),
        captured_at,
    )


def publish_cached_raster_bundle(
    bundle_dir: str | Path,
    publication_root: str | Path,
    *,
    clock: Callable[[], datetime] = _utc_now,
):
    """Validate one offline bundle and install a complete, new bootstrap ledger."""

    bundle_root = Path(bundle_dir).expanduser()
    batch, timezone_name, instance_count, captured_at = _batch_from_bundle(bundle_root)
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("bootstrap clock must return an aware datetime")
    now = now.astimezone(timezone.utc)
    if captured_at > now + timedelta(minutes=5):
        raise ValueError("bundle capture time is too far in the future")
    if any(draft.generated_at > captured_at for draft in batch.drafts):
        raise ValueError("raster source time is newer than the bundle capture")

    target = Path(os.path.abspath(os.fspath(Path(publication_root).expanduser())))
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"bootstrap target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    parent_stat = os.lstat(target.parent)
    if not stat.S_ISDIR(parent_stat.st_mode) or _link_like(parent_stat):
        raise ValueError("bootstrap target parent is not a safe directory")
    staging = target.parent / f".{target.name}.bootstrap-{uuid4().hex}"
    registry = AdapterRegistry()
    registry.register_default_legacy(RasterAssetLegacyAdapter())
    try:
        module = PublicationModule(
            staging,
            producer=_StaticProducer(batch),
            adapters=registry,
            clock=lambda: now,
            timezone_name=timezone_name,
        )
        report = module.publish_due(WorkBudget(max_items=max(1, instance_count)))
        if (
            report.attempted != instance_count
            or report.skipped
            or report.published + report.unavailable != instance_count
        ):
            raise RuntimeError("bootstrap publication did not reach a complete terminal state")
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"bootstrap target already exists: {target}")
        try:
            os.replace(staging, target)
        except OSError as exc:
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"bootstrap target already exists: {target}") from exc
            raise
        return report
    finally:
        if staging.exists() and not staging.is_symlink() and staging.parent == target.parent:
            shutil.rmtree(staging)


__all__ = [
    "RasterAttachmentReport",
    "attach_cached_raster_snapshot",
    "publish_cached_raster_bundle",
]
