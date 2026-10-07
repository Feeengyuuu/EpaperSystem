"""Read-only capture of sanitized playlist metadata and exact cached frames."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request, build_opener
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_MAX_HTML_BYTES = 5 * 1024 * 1024
_MAX_RASTER_BYTES = 24 * 1024 * 1024
_VOID_ELEMENTS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_EDIT_WINDOW = re.compile(
    r"openEditModal\(.*?,\s*['\"]([0-9]{2}:[0-9]{2})['\"]\s*,\s*['\"]([0-9]{2}:[0-9]{2})['\"]\s*\)"
)


@dataclass(slots=True)
class _Plugin:
    name: str = ""
    plugin_id: str = ""
    thumbnail: str | None = None
    source_updated_at: str | None = None
    interval: int = 300


@dataclass(slots=True)
class _Playlist:
    name: str = ""
    start: str = "00:00"
    end: str = "24:00"
    active: bool = False
    plugins: list[_Plugin] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DevicePortalCaptureReport:
    bundle_dir: Path
    snapshot_dir: Path
    configured: int
    available: int
    unavailable: int


def _classes(attributes: dict[str, str | None]) -> set[str]:
    return set((attributes.get("class") or "").split())


def _safe_text(value: str, field_name: str, maximum: int) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or any(ord(character) < 32 for character in cleaned):
        raise ValueError(f"captured {field_name} is invalid")
    return cleaned


class _PlaylistParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.playlists: list[_Playlist] = []
        self._playlist: _Playlist | None = None
        self._plugin: _Plugin | None = None
        self._stack: list[tuple[str, str | None]] = []
        self._capture: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {str(key): value for key, value in attrs}
        classes = _classes(attributes)
        marker: str | None = None
        if tag == "div" and "playlist-item" in classes:
            if self._playlist is not None:
                raise ValueError("captured playlists are unexpectedly nested")
            self._playlist = _Playlist(active="active" in classes)
            marker = "playlist"
        elif tag == "div" and "plugin-item" in classes:
            if self._playlist is None or self._plugin is not None:
                raise ValueError("captured plugin item is unexpectedly nested")
            self._plugin = _Plugin()
            marker = "plugin"
        elif tag == "span" and self._playlist is not None and "playlist-title" in classes:
            self._capture = ("playlist_name", [])
        elif tag == "span" and self._plugin is not None and "plugin-instance" in classes:
            self._capture = ("plugin_name", [])
        elif tag == "span" and self._plugin is not None and "latest-refresh" in classes:
            title = attributes.get("title")
            self._plugin.source_updated_at = title.strip() if isinstance(title, str) and title.strip() else None
        elif tag == "button" and self._playlist is not None:
            onclick = attributes.get("onclick")
            if isinstance(onclick, str):
                match = _EDIT_WINDOW.search(onclick)
                if match is not None:
                    self._playlist.start, self._playlist.end = match.groups()
            if self._plugin is not None and "refresh-settings-btn" in classes:
                plugin_id = attributes.get("data-plugin-id")
                instance_name = attributes.get("data-instance")
                if isinstance(plugin_id, str):
                    self._plugin.plugin_id = plugin_id.strip()
                if isinstance(instance_name, str) and instance_name.strip():
                    if self._plugin.name and self._plugin.name != instance_name.strip():
                        raise ValueError("captured instance names disagree")
                    self._plugin.name = instance_name.strip()
                refresh = attributes.get("data-refresh")
                if isinstance(refresh, str):
                    try:
                        refresh_document = json.loads(refresh)
                    except json.JSONDecodeError:
                        refresh_document = {}
                    interval = refresh_document.get("interval") if isinstance(refresh_document, dict) else None
                    if isinstance(interval, int) and not isinstance(interval, bool):
                        self._plugin.interval = max(30, min(7 * 24 * 60 * 60, interval))
        elif tag == "img" and self._plugin is not None and "plugin-thumbnail" in classes:
            source = attributes.get("src")
            if isinstance(source, str) and source.strip():
                self._plugin.thumbnail = source.strip()

        if tag not in _VOID_ELEMENTS:
            self._stack.append((tag, marker))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._capture[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        open_tag, marker = self._stack.pop()
        if open_tag != tag:
            raise ValueError("captured playlist HTML is malformed")
        if tag == "span" and self._capture is not None:
            field_name, parts = self._capture
            value = "".join(parts).strip()
            if field_name == "playlist_name" and self._playlist is not None:
                self._playlist.name = value
            elif field_name == "plugin_name" and self._plugin is not None:
                self._plugin.name = value
            self._capture = None
        if marker == "plugin":
            assert self._playlist is not None and self._plugin is not None
            self._plugin.name = _safe_text(self._plugin.name, "instance name", 160)
            self._plugin.plugin_id = _safe_text(self._plugin.plugin_id, "plugin id", 200)
            self._playlist.plugins.append(self._plugin)
            self._plugin = None
        elif marker == "playlist":
            assert self._playlist is not None
            self._playlist.name = _safe_text(self._playlist.name, "playlist name", 500)
            self.playlists.append(self._playlist)
            self._playlist = None

    def finish(self) -> list[_Playlist]:
        self.close()
        if self._playlist is not None or self._plugin is not None or self._stack:
            raise ValueError("captured playlist HTML ended unexpectedly")
        if not self.playlists:
            raise ValueError("captured playlist HTML contains no playlists")
        return self.playlists


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("device URL must be an HTTP(S) origin")
    return parsed.scheme, parsed.hostname.casefold(), parsed.port


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    _origin(value)
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("device URL must not include a path, query, or fragment")
    return value.rstrip("/")


def _read_response(response, maximum: int, expected_types: frozenset[str], expected_origin) -> bytes:
    if _origin(response.geturl()) != expected_origin:
        raise ValueError("device response redirected outside the configured origin")
    content_type = response.headers.get_content_type().lower()
    if content_type not in expected_types:
        raise ValueError("device response has an unexpected content type")
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise ValueError("device response has an invalid content length") from exc
        if declared_size < 0 or declared_size > maximum:
            raise ValueError("device response is too large")
    body = response.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError("device response is too large")
    return body


def _fetch(opener, url: str, *, maximum: int, expected_types: frozenset[str], expected_origin) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": ", ".join(sorted(expected_types)),
            "User-Agent": "InkyPi-Model-Y-ReadOnly-Capture/1",
        },
        method="GET",
    )
    with opener.open(request, timeout=15) as response:
        return _read_response(response, maximum, expected_types, expected_origin)


def _thumbnail_url(base: str, reference: str) -> str:
    parsed = urlsplit(reference)
    segments = parsed.path.split("/")
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or len(segments) != 5
        or segments[:2] != ["", "plugin_instance_image"]
    ):
        raise ValueError("captured thumbnail URL is outside the supported read-only route")
    for raw_segment in segments[2:]:
        try:
            decoded = unquote(raw_segment, errors="strict")
            decoded_twice = unquote(decoded, errors="strict")
        except UnicodeError as exc:
            raise ValueError("captured thumbnail URL is outside the supported read-only route") from exc
        if (
            not decoded
            or decoded in {".", ".."}
            or decoded_twice != decoded
            or "%" in decoded
            or "/" in decoded
            or "\\" in decoded
            or any(ord(character) < 32 for character in decoded)
        ):
            raise ValueError("captured thumbnail URL is outside the supported read-only route")
    resolved = urljoin(f"{base}/", reference.lstrip("/"))
    if urlsplit(resolved).path != parsed.path:
        raise ValueError("captured thumbnail URL is outside the supported read-only route")
    return resolved


def _source_timestamp(value: str | None, captured_at: datetime) -> str:
    if value:
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(document: object) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _file_record(root: Path, relative_path: str) -> dict:
    body = (root / relative_path).read_bytes()
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body),
    }


def _build_capture_staging(
    staging: Path,
    *,
    base: str,
    expected_origin: tuple[str, str, int | None],
    playlists: list[_Playlist],
    resolution: tuple[int, int],
    timezone_name: str,
    captured_at: datetime,
    opener,
) -> tuple[int, int]:
    """Populate one private staging directory and return configured/available counts."""

    bundle = staging / "bundle"
    snapshot = staging / "snapshot"
    bundle.mkdir(parents=True)
    (snapshot / "frames").mkdir(parents=True)
    web_playlists = []
    snapshot_records = []
    configured = 0
    available = 0
    active_name = next((item.name for item in playlists if item.active), playlists[0].name)
    for playlist in playlists:
        instances = []
        for plugin in playlist.plugins:
            configured += 1
            identity = f"{playlist.name}\0{plugin.plugin_id}\0{plugin.name}"
            instance_uuid = f"device-cache-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"
            instance = {
                "instance_uuid": instance_uuid,
                "latest_refresh_time": plugin.source_updated_at,
                "name": plugin.name,
                "plugin_id": plugin.plugin_id,
                "plugin_settings": {},
                "refresh": {"interval": plugin.interval},
                "settings_revision": 1,
                "structural_generation": 1,
            }
            instances.append(instance)
            common = {
                "instance_uuid": instance_uuid,
                "settings_revision": 1,
                "structural_generation": 1,
            }
            raster_body = None
            if plugin.thumbnail is not None:
                thumbnail_url = _thumbnail_url(base, plugin.thumbnail)
                try:
                    raster_body = _fetch(
                        opener,
                        thumbnail_url,
                        maximum=_MAX_RASTER_BYTES,
                        expected_types=frozenset({"image/png"}),
                        expected_origin=expected_origin,
                    )
                except HTTPError as exc:
                    if exc.code not in {404, 410}:
                        raise
            if raster_body is None:
                snapshot_records.append({**common, "reason": "missing", "status": "unavailable"})
                continue
            frame_path = f"frames/{instance_uuid}.png"
            _write_new(snapshot / frame_path, raster_body)
            snapshot_records.append(
                {
                    **common,
                    "path": frame_path,
                    "source_updated_at": _source_timestamp(plugin.source_updated_at, captured_at),
                    "status": "available",
                }
            )
            available += 1
        web_playlists.append(
            {
                "end_time": playlist.end,
                "name": playlist.name,
                "plugins": instances,
                "start_time": playlist.start,
            }
        )

    web_config = {
        "active_playlist": active_name,
        "device": {
            "image_settings": {},
            "name": "Model Y Device Snapshot",
            "orientation": "horizontal",
            "plugin_cycle_interval_seconds": 300,
            "resolution": list(resolution),
            "time_format": "12h",
            "timezone": timezone_name,
        },
        "format_version": 1,
        "playlists": web_playlists,
        "plugin_catalog": [],
        "source": {"config_revision": 0, "schema_version": 1},
    }
    required_secrets = {"format_version": 1, "secrets": []}
    _write_new(bundle / "web_config.json", _json_bytes(web_config))
    _write_new(bundle / "required_secrets.json", _json_bytes(required_secrets))
    manifest = {
        "files": [
            _file_record(bundle, "required_secrets.json"),
            _file_record(bundle, "web_config.json"),
        ],
        "format_version": 1,
        "resources": [],
        "warnings": [],
    }
    _write_new(bundle / "manifest.json", _json_bytes(manifest))
    snapshot_document = {
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "format_version": 1,
        "rasters": snapshot_records,
    }
    _write_new(snapshot / "snapshot.json", _json_bytes(snapshot_document))
    return configured, available


def capture_device_portal_snapshot(
    device_url: str,
    output_root: str | Path,
    *,
    timezone_name: str,
    resolution: tuple[int, int] = (800, 480),
    clock=lambda: datetime.now(timezone.utc),
    opener=None,
) -> DevicePortalCaptureReport:
    """Capture only sanitized playlist fields and same-origin cached PNGs."""

    base = _base_url(device_url)
    expected_origin = _origin(base)
    try:
        ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("capture timezone is invalid") from exc
    if (
        len(resolution) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in resolution)
    ):
        raise ValueError("capture resolution is invalid")
    captured_at = clock()
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        raise ValueError("capture clock must return an aware datetime")
    captured_at = captured_at.astimezone(timezone.utc)
    opener = opener or build_opener()
    html_body = _fetch(
        opener,
        f"{base}/playlist",
        maximum=_MAX_HTML_BYTES,
        expected_types=frozenset({"text/html"}),
        expected_origin=expected_origin,
    )
    try:
        html = html_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("device playlist is not UTF-8") from exc
    parser = _PlaylistParser()
    parser.feed(html)
    playlists = parser.finish()

    # Reject normalization tricks before creating a staging directory or
    # issuing any request beyond the supported cache-image route.
    for playlist in playlists:
        for plugin in playlist.plugins:
            if plugin.thumbnail is not None:
                _thumbnail_url(base, plugin.thumbnail)

    output = Path(output_root).expanduser()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"capture output already exists: {output}")
    staging = output.parent / f".{output.name}.capture-{uuid4().hex}"
    try:
        configured, available = _build_capture_staging(
            staging,
            base=base,
            expected_origin=expected_origin,
            playlists=playlists,
            resolution=resolution,
            timezone_name=timezone_name,
            captured_at=captured_at,
            opener=opener,
        )
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"capture output already exists: {output}")
        try:
            os.replace(staging, output)
        except OSError as exc:
            if output.exists() or output.is_symlink():
                raise FileExistsError(f"capture output already exists: {output}") from exc
            raise
        return DevicePortalCaptureReport(
            bundle_dir=output / "bundle",
            snapshot_dir=output / "snapshot",
            configured=configured,
            available=available,
            unavailable=configured - available,
        )
    finally:
        if staging.exists() and not staging.is_symlink() and staging.parent == output.parent:
            shutil.rmtree(staging)


__all__ = ["DevicePortalCaptureReport", "capture_device_portal_snapshot"]
