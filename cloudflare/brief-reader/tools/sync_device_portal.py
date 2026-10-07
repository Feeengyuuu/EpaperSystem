"""Publish the web-safe subset of the active InkyPi playlist from cached images.

This sidecar deliberately has no provider, renderer, refresh, display, or admin
write integration.  It reads the loopback playlist and the already-generated
PNG thumbnails, validates a complete edition, then uses ``push_portal`` to
atomically switch the private Cloudflare reader to that edition.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import tempfile
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import push_portal


_MAX_HTML_BYTES = 5 * 1024 * 1024
_MAX_ASSET_BYTES = 1_000_000
_MAX_TOTAL_ASSET_BYTES = 16 * 1024 * 1024
_DEFAULT_MINIMUM_REFRESH_INTERVAL_SECONDS = 300
_MINIMUM_REFRESH_INTERVAL_SECONDS = 30
_MAXIMUM_REFRESH_INTERVAL_SECONDS = 7 * 24 * 60 * 60
_EXCLUDED_WEB_PLUGIN_IDS = frozenset({"pixiv_r18_ranking"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class SyncPortalError(Exception):
    """A bounded, secret-safe device synchronization failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message[:160])
        self.code = code


@dataclass(slots=True)
class _Plugin:
    name: str = ""
    plugin_id: str = ""
    thumbnail: str | None = None
    source_updated_at: str | None = None
    refresh_interval_seconds: int | None = None


@dataclass(slots=True)
class _Playlist:
    name: str = ""
    active: bool = False
    plugins: list[_Plugin] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _CapturedItem:
    document: dict[str, object]
    body: bytes


@dataclass(frozen=True, slots=True)
class SyncReport:
    outcome: str
    item_count: int
    fingerprint: str | None
    generation: int | None
    edition_id: str | None


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _classes(attributes: dict[str, str | None]) -> set[str]:
    return set((attributes.get("class") or "").split())


def _safe_text(value: str, label: str, maximum: int = 160) -> str:
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in cleaned)
    ):
        raise SyncPortalError("capture_invalid", f"device {label} is invalid")
    return cleaned


def _refresh_interval(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        refresh_document = json.loads(value)
    except json.JSONDecodeError:
        return None
    interval = refresh_document.get("interval") if isinstance(refresh_document, dict) else None
    if isinstance(interval, int) and not isinstance(interval, bool) and interval > 0:
        return max(_MINIMUM_REFRESH_INTERVAL_SECONDS, min(_MAXIMUM_REFRESH_INTERVAL_SECONDS, interval))
    return None


class _PlaylistParser(HTMLParser):
    """Read the device /playlist page.

    Two layouts are accepted: the legacy list (``div.playlist-item`` /
    ``div.plugin-item``) and the 2026-10 Now Playing page, whose playlist
    panels and instance cards carry the same facts as ``data-*`` attributes.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.playlists: list[_Playlist] = []
        self._playlist: _Playlist | None = None
        self._plugin: _Plugin | None = None
        self._stack: list[tuple[str, str | None]] = []
        self._capture: tuple[str, list[str]] | None = None
        self._tab: str | None = None
        self._live_tabs: set[str] = set()
        self._card_image: str | None = None

    def _start_card_markup(self, tag: str, attributes: dict[str, str | None], classes: set[str]) -> str | None:
        """Handle the Now Playing layout; return a stack marker or None."""
        if tag == "button" and "data-playlist-tab" in attributes:
            self._tab = attributes.get("data-playlist-tab") or ""
            return "tab"
        if tag == "span" and self._tab is not None and "live-dot" in classes:
            self._live_tabs.add(self._tab)
            return None
        if tag == "div" and "data-playlist-panel" in attributes:
            if self._playlist is not None:
                raise SyncPortalError("capture_invalid", "device playlists are malformed")
            name = attributes.get("data-playlist-panel") or ""
            active = attributes.get("data-playlist-active") == "true" or name in self._live_tabs
            self._playlist = _Playlist(name=name, active=active)
            return "playlist"
        if tag == "article" and "data-instance-card" in attributes:
            if self._playlist is None or self._plugin is not None:
                raise SyncPortalError("capture_invalid", "device plugins are malformed")
            if attributes.get("data-playlist") != self._playlist.name:
                raise SyncPortalError("capture_invalid", "device card playlist disagrees")
            self._plugin = _Plugin(
                name=(attributes.get("data-instance") or "").strip(),
                plugin_id=(attributes.get("data-plugin-id") or "").strip(),
                refresh_interval_seconds=_refresh_interval(attributes.get("data-refresh")),
            )
            image = attributes.get("data-image-url")
            self._card_image = image.strip() if isinstance(image, str) and image.strip() else None
            return "plugin"
        if tag == "span" and self._plugin is not None and "data-relative-time" in attributes:
            # Like the legacy page, only an instance that has rendered offers its thumbnail.
            value = attributes.get("data-relative-time")
            if isinstance(value, str) and value.strip():
                self._plugin.source_updated_at = value.strip()
                self._plugin.thumbnail = self._card_image
        return None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {str(key): value for key, value in attrs}
        classes = _classes(attributes)
        marker: str | None = self._start_card_markup(tag, attributes, classes)
        if marker is not None:
            pass
        elif tag == "div" and "playlist-item" in classes:
            if self._playlist is not None:
                raise SyncPortalError("capture_invalid", "device playlists are malformed")
            self._playlist = _Playlist(active="active" in classes)
            marker = "playlist"
        elif tag == "div" and "plugin-item" in classes:
            if self._playlist is None or self._plugin is not None:
                raise SyncPortalError("capture_invalid", "device plugins are malformed")
            self._plugin = _Plugin()
            marker = "plugin"
        elif tag == "span" and self._playlist is not None and "playlist-title" in classes:
            self._capture = ("playlist", [])
        elif tag == "span" and self._plugin is not None and "plugin-instance" in classes:
            self._capture = ("plugin", [])
        elif tag == "span" and self._plugin is not None and "latest-refresh" in classes:
            value = attributes.get("title")
            self._plugin.source_updated_at = value.strip() if isinstance(value, str) and value.strip() else None
        elif tag == "button" and self._plugin is not None and "refresh-settings-btn" in classes:
            plugin_id = attributes.get("data-plugin-id")
            instance_name = attributes.get("data-instance")
            if isinstance(plugin_id, str):
                self._plugin.plugin_id = plugin_id.strip()
            if isinstance(instance_name, str) and instance_name.strip():
                instance_name = instance_name.strip()
                if self._plugin.name and self._plugin.name != instance_name:
                    raise SyncPortalError("capture_invalid", "device instance names disagree")
                self._plugin.name = instance_name
            interval = _refresh_interval(attributes.get("data-refresh"))
            if interval is not None:
                self._plugin.refresh_interval_seconds = interval
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
            raise SyncPortalError("capture_invalid", "device playlist HTML is malformed")
        if tag == "span" and self._capture is not None:
            target, parts = self._capture
            value = "".join(parts).strip()
            if target == "playlist" and self._playlist is not None:
                self._playlist.name = value
            elif target == "plugin" and self._plugin is not None:
                self._plugin.name = value
            self._capture = None
        if marker == "tab":
            self._tab = None
        elif marker == "plugin":
            assert self._playlist is not None and self._plugin is not None
            self._plugin.name = _safe_text(self._plugin.name, "instance name")
            self._plugin.plugin_id = _safe_text(self._plugin.plugin_id, "plugin id", 128)
            if _IDENTIFIER.fullmatch(self._plugin.plugin_id) is None:
                raise SyncPortalError("capture_invalid", "device plugin identifier is invalid")
            self._playlist.plugins.append(self._plugin)
            self._plugin = None
        elif marker == "playlist":
            assert self._playlist is not None
            self._playlist.name = _safe_text(self._playlist.name, "playlist title")
            self.playlists.append(self._playlist)
            self._playlist = None

    def finish(self) -> list[_Playlist]:
        self.close()
        if self._playlist is not None or self._plugin is not None or self._stack:
            raise SyncPortalError("capture_invalid", "device playlist HTML ended unexpectedly")
        active = [playlist for playlist in self.playlists if playlist.active]
        if len(active) != 1 or not 1 <= len(active[0].plugins) <= 100:
            raise SyncPortalError("capture_invalid", "device must have one complete active playlist")
        return active


def _origin(value: str) -> tuple[str, str, int | None]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SyncPortalError("configuration_invalid", "device origin is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname.casefold() not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise SyncPortalError("configuration_invalid", "device origin must be loopback HTTP(S)")
    return parsed.scheme, parsed.hostname.casefold(), port


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    _origin(value)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise SyncPortalError("configuration_invalid", "device URL must be an origin without extra fields")
    return value.rstrip("/")


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
        raise SyncPortalError("capture_invalid", "device thumbnail route is not read-only")
    for raw_segment in segments[2:]:
        try:
            decoded = unquote(raw_segment, errors="strict")
            decoded_twice = unquote(decoded, errors="strict")
        except UnicodeError as exc:
            raise SyncPortalError("capture_invalid", "device thumbnail route is invalid") from exc
        if (
            not decoded
            or decoded in {".", ".."}
            or decoded_twice != decoded
            or "%" in decoded
            or "/" in decoded
            or "\\" in decoded
            or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
        ):
            raise SyncPortalError("capture_invalid", "device thumbnail route is invalid")
    return f"{base}{parsed.path}"


def _read_response(response, maximum: int, expected_type: str, expected_origin) -> bytes:
    if _origin(response.geturl()) != expected_origin:
        raise SyncPortalError("capture_invalid", "device response left the configured origin")
    if response.headers.get_content_type().lower() != expected_type:
        raise SyncPortalError("capture_invalid", "device response has an unexpected content type")
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise SyncPortalError("capture_invalid", "device response length is invalid") from exc
        if declared_size < 0 or declared_size > maximum:
            raise SyncPortalError("capture_invalid", "device response exceeds its limit")
    body = response.read(maximum + 1)
    if len(body) > maximum:
        raise SyncPortalError("capture_invalid", "device response exceeds its limit")
    return body


def _fetch(opener, url: str, *, maximum: int, expected_type: str, expected_origin) -> bytes:
    request = Request(
        url,
        headers={"Accept": expected_type, "User-Agent": "InkyPi-Cloud-Sync/1.0"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=15) as response:
            return _read_response(response, maximum, expected_type, expected_origin)
    except SyncPortalError:
        raise
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise SyncPortalError("device_unavailable", "device cache is temporarily unavailable") from exc


def _timestamp(value: str | None) -> str:
    if not isinstance(value, str):
        raise SyncPortalError("capture_invalid", "device source timestamp is missing")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SyncPortalError("capture_invalid", "device source timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SyncPortalError("capture_invalid", "device source timestamp needs a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SyncPortalError("configuration_invalid", "clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _capture(
    base: str,
    timezone_name: str,
    opener,
) -> tuple[dict[str, str], int, list[_CapturedItem]]:
    expected_origin = _origin(base)
    html_body = _fetch(
        opener,
        f"{base}/playlist",
        maximum=_MAX_HTML_BYTES,
        expected_type="text/html",
        expected_origin=expected_origin,
    )
    try:
        html = html_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SyncPortalError("capture_invalid", "device playlist is not UTF-8") from exc
    parser = _PlaylistParser()
    try:
        parser.feed(html)
        playlists = parser.finish()
    except SyncPortalError:
        raise
    except Exception as exc:
        raise SyncPortalError("capture_invalid", "device playlist could not be parsed") from exc
    playlist = playlists[0]
    publishable_plugins = [
        plugin
        for plugin in playlist.plugins
        if plugin.plugin_id not in _EXCLUDED_WEB_PLUGIN_IDS
    ]
    if not publishable_plugins:
        raise SyncPortalError("capture_invalid", "active playlist has no web-publishable items")
    minimum_refresh_interval_seconds = min(
        (
            plugin.refresh_interval_seconds
            for plugin in publishable_plugins
            if plugin.refresh_interval_seconds is not None
        ),
        default=_DEFAULT_MINIMUM_REFRESH_INTERVAL_SECONDS,
    )

    # Validate every route before making any thumbnail request.
    thumbnail_urls: list[str] = []
    for plugin in publishable_plugins:
        if plugin.thumbnail is None:
            raise SyncPortalError("capture_invalid", "every active item needs a cached thumbnail")
        thumbnail_urls.append(_thumbnail_url(base, plugin.thumbnail))

    items: list[_CapturedItem] = []
    instance_ids: set[str] = set()
    total_asset_bytes = 0
    for plugin, thumbnail_url in zip(publishable_plugins, thumbnail_urls, strict=True):
        body = _fetch(
            opener,
            thumbnail_url,
            maximum=_MAX_ASSET_BYTES,
            expected_type="image/png",
            expected_origin=expected_origin,
        )
        total_asset_bytes += len(body)
        if total_asset_bytes > _MAX_TOTAL_ASSET_BYTES:
            raise SyncPortalError(
                "capture_invalid",
                "device active assets exceed the bounded publication budget",
            )
        try:
            push_portal._validate_png(body)
        except push_portal.BundleValidationError as exc:
            raise SyncPortalError("capture_invalid", "device returned an invalid 800x480 PNG") from exc
        asset_id = hashlib.sha256(body).hexdigest()
        identity = f"{playlist.name}\0{plugin.plugin_id}\0{plugin.name}"
        instance_id = f"device-cache-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"
        if instance_id in instance_ids:
            raise SyncPortalError("capture_invalid", "device active items are not unique")
        instance_ids.add(instance_id)
        source_time = _timestamp(plugin.source_updated_at)
        document: dict[str, object] = {
            "instanceId": instance_id,
            "pluginId": plugin.plugin_id,
            "title": plugin.name,
            "displayTitle": "当地天气" if plugin.plugin_id == "weather" else plugin.name,
            "assetId": asset_id,
            "generatedAt": source_time,
            "sourceUpdatedAt": source_time,
            "width": 800,
            "height": 480,
        }
        items.append(_CapturedItem(document=document, body=body))

    slug_digest = hashlib.sha256(playlist.name.encode("utf-8")).hexdigest()[:24]
    return (
        {"slug": f"device-{slug_digest}", "title": playlist.name},
        minimum_refresh_interval_seconds,
        items,
    )


def _default_state() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "reservedGeneration": 0,
        "lastGood": None,
        "status": {"outcome": "new"},
    }


def _read_state(state_path: Path) -> dict[str, object]:
    if not state_path.exists():
        return _default_state()
    try:
        file_stat = state_path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > 64 * 1024:
            raise ValueError
        document = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SyncPortalError("state_invalid", "local synchronization state is invalid") from exc
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != 1
        or type(document.get("reservedGeneration")) is not int
        or not 0 <= document["reservedGeneration"] <= 9_007_199_254_740_991
        or "lastGood" not in document
        or not isinstance(document.get("status"), dict)
    ):
        raise SyncPortalError("state_invalid", "local synchronization state is invalid")
    last_good = document["lastGood"]
    if last_good is not None and (
        not isinstance(last_good, dict)
        or not isinstance(last_good.get("fingerprint"), str)
        or type(last_good.get("generation")) is not int
    ):
        raise SyncPortalError("state_invalid", "local synchronization state is invalid")
    return document


def _atomic_state_write(path: Path, document: dict[str, object]) -> None:
    body = (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_descriptor = None
        if directory_descriptor is not None:
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class _ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self._stream = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._stream = self.path.open("a+b")
        self._stream.seek(0, os.SEEK_END)
        if self._stream.tell() == 0:
            self._stream.write(b"\0")
            self._stream.flush()
        self._stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self._stream.close()
            self._stream = None
            return False
        return True

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None

    def __enter__(self):
        return self if self.acquire() else None

    def __exit__(self, *_args):
        self.release()


def _prepare_state_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    file_stat = path.lstat()
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISDIR(file_stat.st_mode):
        raise SyncPortalError("state_invalid", "state directory must be a plain directory")
    if os.name != "nt":
        os.chmod(path, 0o700)


def _status(outcome: str, attempted_at: str, **fields: object) -> dict[str, object]:
    return {"outcome": outcome, "attemptedAt": attempted_at, **fields}


def sync_device_portal(
    *,
    endpoint: str,
    state_dir: str | Path,
    timezone_name: str,
    device_url: str = "http://127.0.0.1",
    opener=None,
    push: Callable[..., push_portal.PushReport] = push_portal.push_release,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    timeout: float = 30.0,
) -> SyncReport:
    """Publish the active playlist's web-safe subset while preserving last-good."""

    if timezone_name != "America/Los_Angeles":
        raise SyncPortalError("configuration_invalid", "synchronization timezone is invalid")
    base = _base_url(device_url)
    try:
        push_portal._endpoint_origin(endpoint)
    except push_portal.PushPortalError as exc:
        raise SyncPortalError("configuration_invalid", "publication endpoint is invalid") from exc
    try:
        now = clock()
        attempted_at = _utc_timestamp(now)
        now_generation = int(now.timestamp() * 1000)
    except (OverflowError, OSError, ValueError, SyncPortalError) as exc:
        if isinstance(exc, SyncPortalError):
            raise
        raise SyncPortalError("configuration_invalid", "synchronization clock is invalid") from exc
    if not 1 <= now_generation <= 9_007_199_254_740_991:
        raise SyncPortalError("configuration_invalid", "synchronization clock is outside its range")

    state_root = Path(state_dir).expanduser()
    _prepare_state_directory(state_root)
    lock = _ProcessLock(state_root / "sync.lock")
    if not lock.acquire():
        return SyncReport("busy", 0, None, None, None)
    state_path = state_root / "state.json"
    try:
        state = _read_state(state_path)
        try:
            playlist, minimum_refresh_interval_seconds, captured_items = _capture(
                base,
                timezone_name,
                opener or build_opener(_NoRedirectHandler()),
            )
            item_documents = [item.document for item in captured_items]
            fingerprint_document = {
                "timezone": timezone_name,
                "playlist": playlist,
                "minimumRefreshIntervalSeconds": minimum_refresh_interval_seconds,
                "items": item_documents,
            }
            fingerprint = hashlib.sha256(
                json.dumps(
                    fingerprint_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            last_good = state.get("lastGood")
            if isinstance(last_good, dict) and last_good.get("fingerprint") == fingerprint:
                state["status"] = _status(
                    "unchanged",
                    attempted_at,
                    itemCount=len(captured_items),
                    fingerprint=fingerprint,
                )
                _atomic_state_write(state_path, state)
                return SyncReport(
                    "unchanged",
                    len(captured_items),
                    fingerprint,
                    int(last_good["generation"]),
                    str(last_good.get("editionId") or "") or None,
                )

            previous_generation = 0
            if isinstance(last_good, dict) and type(last_good.get("generation")) is int:
                previous_generation = int(last_good["generation"])
            reserved_generation = max(
                now_generation,
                int(state.get("reservedGeneration", 0)) + 1,
                previous_generation + 1,
            )
            if reserved_generation > 9_007_199_254_740_991:
                raise SyncPortalError("generation_exhausted", "publication generation is exhausted")
            state["reservedGeneration"] = reserved_generation
            state["status"] = _status(
                "publishing",
                attempted_at,
                itemCount=len(captured_items),
                fingerprint=fingerprint,
                generation=reserved_generation,
            )
            _atomic_state_write(state_path, state)

            manifest = {
                "schemaVersion": 2,
                "generation": reserved_generation,
                "publishedAt": attempted_at,
                "timezone": timezone_name,
                "playlist": playlist,
                "minimumRefreshIntervalSeconds": minimum_refresh_interval_seconds,
                "items": item_documents,
            }
            with tempfile.TemporaryDirectory(prefix=".release-", dir=state_root) as temporary:
                bundle = Path(temporary)
                assets = bundle / "assets"
                assets.mkdir(mode=0o700)
                written: set[str] = set()
                for item in captured_items:
                    asset_id = str(item.document["assetId"])
                    if asset_id in written:
                        continue
                    written.add(asset_id)
                    (assets / f"{asset_id}.png").write_bytes(item.body)
                manifest_body = json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                (bundle / "manifest.json").write_bytes(manifest_body)
                try:
                    push_report = push(bundle, endpoint, timeout=timeout)
                except push_portal.PushPortalError as exc:
                    raise SyncPortalError("publish_failed", "cloud publication was not accepted") from exc
                except (OSError, URLError, TimeoutError) as exc:
                    raise SyncPortalError("publish_failed", "cloud publication is temporarily unavailable") from exc

            published_state = dict(state)
            published_state["lastGood"] = {
                "fingerprint": fingerprint,
                "generation": reserved_generation,
                "editionId": push_report.edition_id,
                "publishedAt": attempted_at,
                "assetIds": sorted(written),
            }
            published_state["status"] = _status(
                "published",
                attempted_at,
                itemCount=len(captured_items),
                fingerprint=fingerprint,
                generation=reserved_generation,
                editionId=push_report.edition_id,
            )
            _atomic_state_write(state_path, published_state)
            state = published_state
            return SyncReport(
                "published",
                len(captured_items),
                fingerprint,
                reserved_generation,
                push_report.edition_id,
            )
        except SyncPortalError as exc:
            state["status"] = _status("failed", attempted_at, errorCode=exc.code)
            try:
                _atomic_state_write(state_path, state)
            except OSError:
                pass
            raise
        except Exception as exc:
            state["status"] = _status("failed", attempted_at, errorCode="internal_error")
            try:
                _atomic_state_write(state_path, state)
            except OSError:
                pass
            raise SyncPortalError("internal_error", "synchronization failed safely") from exc
    finally:
        lock.release()


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish the web-safe active InkyPi items from existing cached 800x480 PNG files."
    )
    parser.add_argument("--endpoint", required=True, help="private portal HTTPS origin")
    parser.add_argument("--state-dir", required=True, type=Path, help="private persistent state directory")
    parser.add_argument("--device-url", default="http://127.0.0.1", help="loopback InkyPi HTTP origin")
    parser.add_argument("--timezone", default="America/Los_Angeles", dest="timezone_name")
    parser.add_argument("--timeout", default=30.0, type=float, help="per-upload timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    try:
        report = sync_device_portal(
            endpoint=arguments.endpoint,
            state_dir=arguments.state_dir,
            timezone_name=arguments.timezone_name,
            device_url=arguments.device_url,
            timeout=arguments.timeout,
        )
    except SyncPortalError as exc:
        print(f"sync_device_portal: {exc.code}: {str(exc)[:160]}", file=sys.stderr)
        return 2
    if report.outcome == "published":
        print(f"Published {report.item_count} cached frame(s) as edition {report.edition_id}.")
    elif report.outcome == "unchanged":
        print(f"No change across {report.item_count} cached frame(s).")
    else:
        print("Synchronization already running; skipped.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
