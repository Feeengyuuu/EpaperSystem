"""Validate and publish one sanitized Epaper portal release bundle."""

from __future__ import annotations

import argparse
import binascii
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import sys
import time
from typing import Callable
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import zlib


_ASSET_ID = re.compile(r"[0-9a-f]{64}\Z")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_ISO_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?\Z"
)
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_TIMEZONE = re.compile(r"[A-Za-z0-9_+/-]{1,64}\Z")
_MANIFEST_V1_KEYS = {
    "schemaVersion",
    "generation",
    "publishedAt",
    "timezone",
    "playlist",
    "items",
}
_MANIFEST_V2_KEYS = _MANIFEST_V1_KEYS | {"minimumRefreshIntervalSeconds"}
_PLAYLIST_KEYS = {"slug", "title"}
_ITEM_KEYS = {
    "instanceId",
    "pluginId",
    "title",
    "displayTitle",
    "assetId",
    "generatedAt",
    "sourceUpdatedAt",
    "width",
    "height",
}
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_MAX_MANIFEST_BYTES = 128 * 1024
_MINIMUM_REFRESH_INTERVAL_SECONDS = 30
_MAXIMUM_REFRESH_INTERVAL_SECONDS = 7 * 24 * 60 * 60
_MAX_ASSET_BYTES = 1_000_000
_MAX_TOTAL_ASSET_BYTES = 16 * 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class PushPortalError(Exception):
    """Base class for safe, operator-facing publication failures."""


class BundleValidationError(PushPortalError):
    """The local release bundle is not safe to publish."""


class ConfigurationError(PushPortalError):
    """The upload configuration is missing or unsafe."""


class UploadError(PushPortalError):
    """The remote endpoint did not accept a signed object."""


@dataclass(frozen=True, slots=True)
class PushReport:
    asset_count: int
    edition_id: str


@dataclass(frozen=True, slots=True)
class _Asset:
    asset_id: str
    body: bytes


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class _UrllibTransport:
    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def put(self, url: str, *, headers: dict[str, str], body: bytes, timeout: float) -> int:
        request_headers = {
            **headers,
            "Accept": "application/json",
            "User-Agent": "EpaperSystem-Publisher/1.0",
        }
        request = urllib.request.Request(url, data=body, headers=request_headers, method="PUT")
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return int(response.status)
        except urllib.error.HTTPError as exc:
            detail = exc.read(512).decode("utf-8", "replace")
            detail = " ".join(detail.split())[:300]
            raise UploadError(f"remote endpoint returned HTTP {exc.code}: {detail or 'no response detail'}") from exc


class _DuplicateJsonKey(ValueError):
    pass


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def _bounded_text(value: object, maximum: int, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    code_units = len(value.encode("utf-16-le", errors="surrogatepass")) // 2
    return code_units <= maximum and (allow_empty or code_units > 0) and _CONTROL_CHARACTER.search(value) is None


def _identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _iso_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    code_units = len(value.encode("utf-16-le", errors="surrogatepass")) // 2
    if not 20 <= code_units <= 40 or _ISO_TIMESTAMP.fullmatch(value) is None:
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalized)
    except (OverflowError, ValueError):
        return False
    return True


def _validate_manifest(manifest: object) -> dict[str, object]:
    if not isinstance(manifest, dict):
        raise BundleValidationError("manifest.json does not use the release schema")
    schema_version = manifest.get("schemaVersion")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        raise BundleValidationError("manifest schemaVersion is invalid")
    expected_keys = _MANIFEST_V2_KEYS if schema_version == 2 else _MANIFEST_V1_KEYS
    if set(manifest) != expected_keys:
        raise BundleValidationError("manifest.json does not use the release schema")
    if schema_version == 2:
        minimum_refresh_interval = manifest["minimumRefreshIntervalSeconds"]
        if (
            type(minimum_refresh_interval) is not int
            or not _MINIMUM_REFRESH_INTERVAL_SECONDS
            <= minimum_refresh_interval
            <= _MAXIMUM_REFRESH_INTERVAL_SECONDS
        ):
            raise BundleValidationError("manifest minimum refresh interval is invalid")
    generation = manifest["generation"]
    if type(generation) is not int or not 1 <= generation <= _MAX_SAFE_INTEGER:
        raise BundleValidationError("manifest generation is invalid")
    if not _iso_timestamp(manifest["publishedAt"]):
        raise BundleValidationError("manifest publishedAt is invalid")
    timezone = manifest["timezone"]
    if not isinstance(timezone, str) or _TIMEZONE.fullmatch(timezone) is None:
        raise BundleValidationError("manifest timezone is invalid")

    playlist = manifest["playlist"]
    if not isinstance(playlist, dict) or set(playlist) != _PLAYLIST_KEYS:
        raise BundleValidationError("manifest playlist does not use the release schema")
    if not _identifier(playlist["slug"]) or not _bounded_text(playlist["title"], 160):
        raise BundleValidationError("manifest playlist text is invalid")

    items = manifest["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise BundleValidationError("manifest items are invalid")
    instance_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
            raise BundleValidationError("manifest item is invalid")
        for field in ("instanceId", "pluginId"):
            if not _identifier(item[field]):
                raise BundleValidationError(f"manifest item {field} is invalid")
        if item["instanceId"] in instance_ids:
            raise BundleValidationError("manifest instanceId is duplicated")
        for field in ("title", "displayTitle"):
            if not _bounded_text(item[field], 160):
                raise BundleValidationError(f"manifest item {field} is invalid")
        if item["pluginId"] == "weather" and item["displayTitle"] != "当地天气":
            raise BundleValidationError("weather displayTitle must be 当地天气")
        if not _iso_timestamp(item["generatedAt"]) or not _iso_timestamp(item["sourceUpdatedAt"]):
            raise BundleValidationError("manifest item timestamp is invalid")
        if type(item["width"]) is not int or item["width"] != 800:
            raise BundleValidationError("manifest item width is invalid")
        if type(item["height"]) is not int or item["height"] != 480:
            raise BundleValidationError("manifest item height is invalid")
        asset_id = item["assetId"]
        if not isinstance(asset_id, str) or _ASSET_ID.fullmatch(asset_id) is None:
            raise BundleValidationError("manifest assetId is invalid")
        instance_ids.add(item["instanceId"])
    return manifest


def _is_link_or_reparse(file_stat: os.stat_result) -> bool:
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        getattr(file_stat, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _plain_directory(path: Path, label: str) -> None:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise BundleValidationError(f"{label} is unavailable") from exc
    if _is_link_or_reparse(file_stat) or not stat.S_ISDIR(file_stat.st_mode):
        raise BundleValidationError(f"{label} must be a plain directory")


def _directory_names(path: Path, label: str) -> set[str]:
    try:
        with os.scandir(path) as entries:
            return {entry.name for entry in entries}
    except OSError as exc:
        raise BundleValidationError(f"{label} is unavailable") from exc


def _same_file(before: os.stat_result, opened: os.stat_result, after: os.stat_result) -> bool:
    if before.st_size != opened.st_size or opened.st_size != after.st_size:
        return False
    before_identity = (before.st_dev, before.st_ino)
    opened_identity = (opened.st_dev, opened.st_ino)
    after_identity = (after.st_dev, after.st_ino)
    if before.st_ino and opened.st_ino and after.st_ino:
        return before_identity == opened_identity == after_identity
    return before.st_mtime_ns == after.st_mtime_ns


def _read_plain_file(path: Path, label: str, maximum_bytes: int) -> bytes:
    try:
        before = path.lstat()
        if _is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode):
            raise BundleValidationError(f"{label} must be a plain file")
        if before.st_size > maximum_bytes:
            raise BundleValidationError(f"{label} is too large")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise BundleValidationError(f"{label} must be a plain file")
            body = stream.read(maximum_bytes + 1)
        after = path.lstat()
    except BundleValidationError:
        raise
    except OSError as exc:
        raise BundleValidationError(f"{label} is unavailable") from exc
    if _is_link_or_reparse(after) or not _same_file(before, opened, after):
        raise BundleValidationError(f"{label} changed while it was read")
    if len(body) > maximum_bytes or len(body) != opened.st_size:
        raise BundleValidationError(f"{label} is too large or incomplete")
    return body


def _png_scanline_layout(
    width: int,
    height: int,
    bit_depth: int,
    color_type: int,
    interlace: int,
) -> tuple[int, list[int]]:
    samples = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bits_per_pixel = samples * bit_depth
    row_starts: list[int] = []
    offset = 0

    if interlace == 0:
        row_size = (width * bits_per_pixel + 7) // 8
        for _ in range(height):
            row_starts.append(offset)
            offset += 1 + row_size
        return offset, row_starts

    adam7_passes = (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    )
    for x_start, y_start, x_step, y_step in adam7_passes:
        pass_width = 0 if width <= x_start else (width - x_start + x_step - 1) // x_step
        pass_height = 0 if height <= y_start else (height - y_start + y_step - 1) // y_step
        if pass_width == 0 or pass_height == 0:
            continue
        row_size = (pass_width * bits_per_pixel + 7) // 8
        for _ in range(pass_height):
            row_starts.append(offset)
            offset += 1 + row_size
    return offset, row_starts


def _inflate_png_scanlines(compressed: bytes, expected_size: int) -> bytes:
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, expected_size + 1)
        if len(raw) > expected_size or inflater.unconsumed_tail:
            raise BundleValidationError("a portal PNG expands beyond its declared dimensions")
        raw += inflater.flush(expected_size + 1 - len(raw))
    except (zlib.error, ValueError) as exc:
        raise BundleValidationError("a portal PNG has invalid compressed data") from exc
    if len(raw) != expected_size or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise BundleValidationError("a portal PNG has invalid scanline data")
    return raw


def _validate_png(body: bytes) -> None:
    if not body.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BundleValidationError("a portal asset is not a PNG")

    offset = 8
    seen_ihdr = False
    seen_palette = False
    seen_idat = False
    idat_ended = False
    seen_iend = False
    compressed_parts: list[bytes] = []
    width = height = bit_depth = color_type = interlace = 0

    while offset < len(body):
        if seen_iend or len(body) - offset < 12:
            raise BundleValidationError("a portal PNG has trailing or truncated data")
        length = struct.unpack(">I", body[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if length > _MAX_ASSET_BYTES or chunk_end > len(body):
            raise BundleValidationError("a portal PNG chunk is invalid")
        kind = body[offset + 4 : offset + 8]
        payload = body[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", body[offset + 8 + length : chunk_end])[0]
        if len(kind) != 4 or any(not (65 <= character <= 90 or 97 <= character <= 122) for character in kind):
            raise BundleValidationError("a portal PNG chunk type is invalid")
        if binascii.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise BundleValidationError("a portal PNG chunk checksum is invalid")
        offset = chunk_end

        if not seen_ihdr:
            if kind != b"IHDR" or length != 13:
                raise BundleValidationError("a portal PNG must begin with IHDR")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                (width, height) != (800, 480)
                or color_type not in valid_depths
                or bit_depth not in valid_depths[color_type]
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                raise BundleValidationError("portal assets must be valid 800x480 PNG files")
            seen_ihdr = True
            continue

        if kind == b"IHDR":
            raise BundleValidationError("a portal PNG contains multiple IHDR chunks")
        if kind == b"PLTE":
            if seen_palette or seen_idat or length == 0 or length % 3 or length > 768 or color_type in {0, 4}:
                raise BundleValidationError("a portal PNG palette is invalid")
            seen_palette = True
        elif kind == b"IDAT":
            if idat_ended:
                raise BundleValidationError("a portal PNG has nonconsecutive IDAT chunks")
            seen_idat = True
            compressed_parts.append(payload)
        else:
            if seen_idat:
                idat_ended = True
            if kind == b"IEND":
                if length != 0 or not seen_idat:
                    raise BundleValidationError("a portal PNG has an invalid IEND chunk")
                seen_iend = True
            elif kind[0] & 0x20 == 0:
                raise BundleValidationError("a portal PNG contains an unknown critical chunk")

    if not seen_ihdr or not seen_idat or not seen_iend or offset != len(body):
        raise BundleValidationError("a portal PNG is incomplete")
    if color_type == 3 and not seen_palette:
        raise BundleValidationError("an indexed portal PNG is missing its palette")

    expected_size, row_starts = _png_scanline_layout(
        width,
        height,
        bit_depth,
        color_type,
        interlace,
    )
    scanlines = _inflate_png_scanlines(b"".join(compressed_parts), expected_size)
    if any(scanlines[row_start] > 4 for row_start in row_starts):
        raise BundleValidationError("a portal PNG uses an invalid scanline filter")


def _endpoint_origin(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        parsed_port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("endpoint must be a valid HTTPS origin") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in parsed.hostname)
        or (parsed_port is not None and not 1 <= parsed_port <= 65535)
    ):
        raise ConfigurationError("endpoint must be an HTTPS origin without credentials, path, query, or fragment")
    return f"https://{parsed.netloc}"


def _read_bundle(bundle: Path) -> tuple[list[_Asset], bytes]:
    _plain_directory(bundle, "release bundle")
    if _directory_names(bundle, "release bundle") != {"manifest.json", "assets"}:
        raise BundleValidationError("release bundle contains missing or undeclared entries")
    assets_directory = bundle / "assets"
    _plain_directory(assets_directory, "assets directory")

    try:
        manifest_body = _read_plain_file(
            bundle / "manifest.json",
            "manifest.json",
            _MAX_MANIFEST_BYTES,
        )
        manifest = json.loads(
            manifest_body.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise BundleValidationError("manifest.json is unavailable or invalid") from exc
    manifest = _validate_manifest(manifest)

    asset_ids = {item["assetId"] for item in manifest["items"]}
    expected_asset_names = {f"{asset_id}.png" for asset_id in asset_ids}
    if _directory_names(assets_directory, "assets directory") != expected_asset_names:
        raise BundleValidationError("assets directory contains missing or undeclared entries")

    assets: dict[str, _Asset] = {}
    total_asset_bytes = 0
    for item in manifest["items"]:
        asset_id = item["assetId"]
        assert isinstance(asset_id, str)
        if asset_id in assets:
            continue
        body = _read_plain_file(
            assets_directory / f"{asset_id}.png",
            "declared portal asset",
            _MAX_ASSET_BYTES,
        )
        total_asset_bytes += len(body)
        if total_asset_bytes > _MAX_TOTAL_ASSET_BYTES:
            raise BundleValidationError("portal assets exceed the bounded upload budget")
        if hashlib.sha256(body).hexdigest() != asset_id:
            raise BundleValidationError("a portal asset does not match its SHA-256 identifier")
        _validate_png(body)
        assets[asset_id] = _Asset(asset_id, body)

    if (
        _directory_names(bundle, "release bundle") != {"manifest.json", "assets"}
        or _directory_names(assets_directory, "assets directory") != expected_asset_names
    ):
        raise BundleValidationError("release bundle changed while it was validated")
    return [assets[asset_id] for asset_id in sorted(assets)], manifest_body


def _signed_headers(
    method: str,
    path: str,
    content_type: str,
    body: bytes,
    *,
    key: bytes,
    timestamp: str,
    nonce: str,
) -> dict[str, str]:
    body_sha256 = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(
        (
            "v1",
            method,
            path,
            content_type,
            str(len(body)),
            body_sha256,
            timestamp,
            nonce,
        )
    ).encode("utf-8")
    signature = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "X-Epaper-Timestamp": timestamp,
        "X-Epaper-Nonce": nonce,
        "X-Epaper-Content-SHA256": body_sha256,
        "X-Epaper-Signature": signature,
    }


def push_release(
    bundle: str | Path,
    endpoint: str,
    *,
    transport=None,
    clock: Callable[[], float] = time.time,
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    timeout: float = 30.0,
) -> PushReport:
    """Upload all unique assets, then commit the exact manifest bytes."""

    origin = _endpoint_origin(endpoint)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ConfigurationError("timeout must be a positive finite number")
    assets, manifest_body = _read_bundle(Path(bundle))
    key_text = os.environ.get("EPAPER_PUBLISH_KEY")
    if not key_text:
        raise ConfigurationError("EPAPER_PUBLISH_KEY is required")
    key = key_text.encode("utf-8")
    client = transport or _UrllibTransport()

    def upload(path: str, content_type: str, body: bytes) -> None:
        try:
            clock_value = clock()
            if (
                isinstance(clock_value, bool)
                or not isinstance(clock_value, (int, float))
                or not math.isfinite(clock_value)
            ):
                raise ValueError
            timestamp = str(int(clock_value))
        except (OverflowError, TypeError, ValueError) as exc:
            raise ConfigurationError("clock did not provide a valid Unix timestamp") from exc
        try:
            nonce = nonce_factory()
        except Exception as exc:
            raise ConfigurationError("nonce generation failed") from exc
        if not isinstance(nonce, str) or _NONCE.fullmatch(nonce) is None:
            raise ConfigurationError("nonce generator must return 32 lowercase hexadecimal characters")
        headers = _signed_headers(
            "PUT",
            path,
            content_type,
            body,
            key=key,
            timestamp=timestamp,
            nonce=nonce,
        )
        try:
            status = client.put(origin + path, headers=headers, body=body, timeout=float(timeout))
        except (OSError, urllib.error.URLError) as exc:
            raise UploadError(f"network upload failed for {path}") from exc
        if type(status) is not int or not 200 <= status < 300:
            raise UploadError(f"upload was rejected for {path} with HTTP {status}")

    for asset in assets:
        upload(f"/api/v1/assets/{asset.asset_id}", "image/png", asset.body)

    edition_id = hashlib.sha256(manifest_body).hexdigest()
    upload(f"/api/v1/editions/{edition_id}", "application/json", manifest_body)
    return PushReport(asset_count=len(assets), edition_id=edition_id)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a cleaned Epaper portal bundle, upload deduplicated assets, "
            "then atomically publish its manifest. The signing key is read only "
            "from EPAPER_PUBLISH_KEY."
        )
    )
    parser.add_argument("--bundle", required=True, type=Path, help="clean release bundle directory")
    parser.add_argument("--endpoint", required=True, help="portal HTTPS origin")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    try:
        report = push_release(
            arguments.bundle,
            arguments.endpoint,
            timeout=arguments.timeout,
        )
    except PushPortalError as exc:
        print(f"push_portal: {exc}", file=sys.stderr)
        return 2
    print(f"Published edition {report.edition_id} with {report.asset_count} unique asset(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
