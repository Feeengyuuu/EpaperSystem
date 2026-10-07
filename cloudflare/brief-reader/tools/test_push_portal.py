from __future__ import annotations

import binascii
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib


TOOLS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_ROOT))

import push_portal  # noqa: E402


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", binascii.crc32(kind + body) & 0xFFFFFFFF)


def _zlib_stored(body: bytes) -> bytes:
    """Build a deterministic RFC 1950 stream from uncompressed DEFLATE blocks."""

    encoded = bytearray(b"\x78\x01")
    for offset in range(0, len(body), 65_535):
        block = body[offset : offset + 65_535]
        final = offset + len(block) == len(body)
        encoded.append(1 if final else 0)
        encoded.extend(struct.pack("<HH", len(block), len(block) ^ 0xFFFF))
        encoded.extend(block)
    encoded.extend(struct.pack(">I", zlib.adler32(body) & 0xFFFFFFFF))
    return bytes(encoded)


def _png(width: int = 800, height: int = 480) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    row = b"\x00" + bytes((width + 7) // 8)
    scanlines = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", _zlib_stored(scanlines))
        + _png_chunk(b"IEND", b"")
    )


def _manifest(asset_id: str) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "generation": 7,
        "publishedAt": "2026-08-03T03:30:00Z",
        "timezone": "America/Los_Angeles",
        "playlist": {"slug": "model-y", "title": "Model Y"},
        "minimumRefreshIntervalSeconds": 120,
        "items": [
            {
                "instanceId": "weather-home",
                "pluginId": "weather",
                "title": "Fremont, California",
                "displayTitle": "当地天气",
                "assetId": asset_id,
                "generatedAt": "2026-08-03T03:29:00Z",
                "sourceUpdatedAt": "2026-08-03T03:28:00Z",
                "width": 800,
                "height": 480,
            }
        ],
    }


def _write_bundle(root: Path) -> tuple[bytes, bytes, str]:
    asset_body = _png()
    asset_id = hashlib.sha256(asset_body).hexdigest()
    assets = root / "assets"
    assets.mkdir(parents=True)
    (assets / f"{asset_id}.png").write_bytes(asset_body)
    manifest_body = json.dumps(
        _manifest(asset_id),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    (root / "manifest.json").write_bytes(manifest_body)
    return asset_body, manifest_body, asset_id


class _RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def put(self, url: str, *, headers: dict[str, str], body: bytes, timeout: float) -> int:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        return 201


class _FailingTransport(_RecordingTransport):
    def put(self, url: str, *, headers: dict[str, str], body: bytes, timeout: float) -> int:
        super().put(url, headers=headers, body=body, timeout=timeout)
        raise OSError("simulated connection failure")


def _rewrite_manifest(bundle: Path, manifest: dict[str, object]) -> bytes:
    manifest_body = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    (bundle / "manifest.json").write_bytes(manifest_body)
    return manifest_body


def _replace_asset(bundle: Path, old_asset_id: str, body: bytes) -> str:
    new_asset_id = hashlib.sha256(body).hexdigest()
    old_path = bundle / "assets" / f"{old_asset_id}.png"
    old_path.unlink()
    (bundle / "assets" / f"{new_asset_id}.png").write_bytes(body)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"][0]["assetId"] = new_asset_id
    _rewrite_manifest(bundle, manifest)
    return new_asset_id


def _bundle_snapshot(bundle: Path) -> dict[str, bytes]:
    return {path.relative_to(bundle).as_posix(): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}


class PushPortalTests(unittest.TestCase):
    def test_aggregate_asset_bytes_are_bounded_before_any_network_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            asset_body, _manifest_body, _asset_id = _write_bundle(bundle)
            transport = _RecordingTransport()

            with (
                mock.patch.object(
                    push_portal,
                    "_MAX_TOTAL_ASSET_BYTES",
                    len(asset_body) - 1,
                ),
                mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ),
                self.assertRaises(push_portal.BundleValidationError),
            ):
                push_portal.push_release(
                    bundle,
                    "https://portal.example",
                    transport=transport,
                )

        self.assertEqual(transport.requests, [])

    def test_valid_bundle_uploads_deduplicated_assets_before_signed_manifest_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            asset_body, manifest_body, asset_id = _write_bundle(bundle)
            transport = _RecordingTransport()
            nonces = iter(("0" * 32, "1" * 32))
            key = "test-publish-key-32-bytes-minimum"

            with mock.patch.dict(os.environ, {"EPAPER_PUBLISH_KEY": key}, clear=True):
                report = push_portal.push_release(
                    bundle,
                    "https://portal.example",
                    transport=transport,
                    clock=lambda: 1_786_249_800,
                    nonce_factory=lambda: next(nonces),
                    timeout=12.5,
                )

        manifest_id = hashlib.sha256(manifest_body).hexdigest()
        self.assertEqual(report.asset_count, 1)
        self.assertEqual(report.edition_id, manifest_id)
        self.assertEqual(
            [request["url"] for request in transport.requests],
            [
                f"https://portal.example/api/v1/assets/{asset_id}",
                f"https://portal.example/api/v1/editions/{manifest_id}",
            ],
        )
        self.assertEqual([request["body"] for request in transport.requests], [asset_body, manifest_body])
        self.assertEqual([request["timeout"] for request in transport.requests], [12.5, 12.5])

        asset_headers = transport.requests[0]["headers"]
        self.assertEqual(asset_headers["Content-Type"], "image/png")
        self.assertEqual(asset_headers["Content-Length"], str(len(asset_body)))
        self.assertEqual(asset_headers["X-Epaper-Timestamp"], "1786249800")
        self.assertEqual(asset_headers["X-Epaper-Nonce"], "0" * 32)
        self.assertEqual(asset_headers["X-Epaper-Content-SHA256"], asset_id)
        self.assertEqual(
            asset_headers["X-Epaper-Signature"],
            "a76d899c8c3a5dca82a58493407480bdcf4151ef80e708ee6474fbf6429ccbb1",
        )

        manifest_headers = transport.requests[1]["headers"]
        self.assertEqual(manifest_headers["Content-Type"], "application/json")
        self.assertEqual(manifest_headers["Content-Length"], str(len(manifest_body)))
        self.assertEqual(manifest_headers["X-Epaper-Content-SHA256"], manifest_id)
        self.assertEqual(manifest_headers["X-Epaper-Nonce"], "1" * 32)

    def test_each_put_uses_a_fresh_timestamp_for_its_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            transport = _RecordingTransport()
            clock_values = iter((1_786_249_800, 1_786_250_101))
            key = "test-publish-key-32-bytes-minimum"

            with mock.patch.dict(os.environ, {"EPAPER_PUBLISH_KEY": key}, clear=True):
                push_portal.push_release(
                    bundle,
                    "https://portal.example",
                    transport=transport,
                    clock=lambda: next(clock_values),
                    nonce_factory=lambda: "a" * 32,
                )

        self.assertEqual(
            [request["headers"]["X-Epaper-Timestamp"] for request in transport.requests],
            ["1786249800", "1786250101"],
        )
        self.assertEqual(
            [request["headers"]["X-Epaper-Signature"] for request in transport.requests],
            [
                "424a5d31c9c3531d9dc7b429b6343d1d0c3b2b7100db91584c5316d9b8c9ef78",
                "f62a358a4cff1810d3c54437b25c6d17aae280a121adcdb9fb4e1079f852f46d",
            ],
        )

    def test_duplicate_asset_references_upload_the_asset_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _, manifest_body, asset_id = _write_bundle(bundle)
            manifest = json.loads(manifest_body.decode("utf-8"))
            second_item = dict(manifest["items"][0])
            second_item.update(
                {
                    "instanceId": "weather-secondary",
                    "pluginId": "calendar",
                    "title": "Calendar",
                    "displayTitle": "日历",
                }
            )
            manifest["items"].append(second_item)
            _rewrite_manifest(bundle, manifest)
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                report = push_portal.push_release(
                    bundle,
                    "https://portal.example",
                    transport=transport,
                )

        self.assertEqual(report.asset_count, 1)
        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(transport.requests[0]["url"].endswith(f"/assets/{asset_id}"))

    def test_manifest_with_unknown_top_level_field_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["providerSecret"] = "must-not-leave-this-machine"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                with self.assertRaises(push_portal.BundleValidationError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)

        self.assertEqual(transport.requests, [])

    def test_legacy_v1_manifest_without_refresh_metadata_remains_uploadable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schemaVersion"] = 1
            del manifest["minimumRefreshIntervalSeconds"]
            _rewrite_manifest(bundle, manifest)
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                report = push_portal.push_release(
                    bundle,
                    "https://portal.example",
                    transport=transport,
                )

        self.assertEqual(report.asset_count, 1)
        self.assertEqual(len(transport.requests), 2)

    def test_schema_versions_require_their_exact_refresh_metadata_shape(self) -> None:
        mutations = (
            (
                "v1 with v2 metadata",
                lambda document: document.__setitem__("schemaVersion", 1),
            ),
            (
                "v2 without metadata",
                lambda document: document.pop("minimumRefreshIntervalSeconds"),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "release"
                _write_bundle(bundle)
                manifest_path = bundle / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest)
                _rewrite_manifest(bundle, manifest)
                transport = _RecordingTransport()

                with mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ):
                    with self.assertRaises(push_portal.BundleValidationError):
                        push_portal.push_release(
                            bundle,
                            "https://portal.example",
                            transport=transport,
                        )

                self.assertEqual(transport.requests, [])

    def test_manifest_with_unknown_item_field_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["items"][0]["settings"] = {"apiKey": "must-not-leak"}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                with self.assertRaises(push_portal.BundleValidationError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)

        self.assertEqual(transport.requests, [])

    def test_invalid_release_schema_values_are_rejected_before_network(self) -> None:
        mutations = {
            "schema version": lambda document: document.__setitem__("schemaVersion", 3),
            "generation": lambda document: document.__setitem__("generation", 0),
            "naive published time": lambda document: document.__setitem__("publishedAt", "2026-08-03T03:30:00"),
            "timezone": lambda document: document.__setitem__("timezone", "America/Los Angeles"),
            "playlist slug": lambda document: document["playlist"].__setitem__("slug", ""),
            "empty items": lambda document: document.__setitem__("items", []),
            "display title": lambda document: document["items"][0].__setitem__("displayTitle", "Los Angeles Weather"),
            "source time": lambda document: document["items"][0].__setitem__("sourceUpdatedAt", None),
            "width": lambda document: document["items"][0].__setitem__("width", 801),
            "minimum refresh interval below bound": lambda document: document.__setitem__(
                "minimumRefreshIntervalSeconds", 29
            ),
            "minimum refresh interval above bound": lambda document: document.__setitem__(
                "minimumRefreshIntervalSeconds", 604801
            ),
        }

        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "release"
                _write_bundle(bundle)
                manifest_path = bundle / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                transport = _RecordingTransport()

                with mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ):
                    with self.assertRaises(push_portal.BundleValidationError):
                        push_portal.push_release(bundle, "https://portal.example", transport=transport)

                self.assertEqual(transport.requests, [])

    def test_manifest_identifiers_match_the_worker_contract_before_network(self) -> None:
        mutations = {
            "playlist slug punctuation": lambda document: document["playlist"].__setitem__("slug", "model/y"),
            "playlist slug length": lambda document: document["playlist"].__setitem__("slug", "a" * 129),
            "instance identifier punctuation": lambda document: document["items"][0].__setitem__(
                "instanceId", "weather/home"
            ),
            "plugin identifier punctuation": lambda document: document["items"][0].__setitem__("pluginId", "weather?"),
        }

        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "release"
                _write_bundle(bundle)
                manifest_path = bundle / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest)
                _rewrite_manifest(bundle, manifest)
                transport = _RecordingTransport()

                with mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ):
                    with self.assertRaises(push_portal.BundleValidationError):
                        push_portal.push_release(bundle, "https://portal.example", transport=transport)

                self.assertEqual(transport.requests, [])

    def test_nonempty_space_text_matches_the_worker_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["playlist"]["title"] = " "
            manifest["items"][0]["title"] = " "
            _rewrite_manifest(bundle, manifest)
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                report = push_portal.push_release(
                    bundle,
                    "https://portal.example",
                    transport=transport,
                )

        self.assertEqual(report.asset_count, 1)
        self.assertEqual(len(transport.requests), 2)

    def test_manifest_text_bounds_and_controls_match_the_worker_contract_before_network(self) -> None:
        def set_non_weather_display_title(document: dict[str, object], value: str) -> None:
            document["items"][0]["pluginId"] = "calendar"
            document["items"][0]["displayTitle"] = value

        mutations = {
            "playlist title length": lambda document: document["playlist"].__setitem__("title", "a" * 161),
            "playlist title control": lambda document: document["playlist"].__setitem__("title", "Model\nY"),
            "item title length": lambda document: document["items"][0].__setitem__("title", "a" * 161),
            "item title UTF-16 length": lambda document: document["items"][0].__setitem__("title", "😀" * 81),
            "display title control": lambda document: set_non_weather_display_title(document, "Calendar\x7f"),
        }

        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "release"
                _write_bundle(bundle)
                manifest_path = bundle / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest)
                _rewrite_manifest(bundle, manifest)
                transport = _RecordingTransport()

                with mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ):
                    with self.assertRaises(push_portal.BundleValidationError):
                        push_portal.push_release(bundle, "https://portal.example", transport=transport)

                self.assertEqual(transport.requests, [])

    def test_manifest_timezone_matches_the_worker_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["timezone"] = "UTC"
            _rewrite_manifest(bundle, manifest)
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                report = push_portal.push_release(
                    bundle,
                    "https://portal.example",
                    transport=transport,
                )

            self.assertEqual(report.asset_count, 1)
            self.assertEqual(len(transport.requests), 2)

        for timezone in ("", "America/Los Angeles", "America/Los.Angeles", "a" * 65):
            with self.subTest(timezone=timezone), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "release"
                _write_bundle(bundle)
                manifest_path = bundle / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["timezone"] = timezone
                _rewrite_manifest(bundle, manifest)
                transport = _RecordingTransport()

                with mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ):
                    with self.assertRaises(push_portal.BundleValidationError):
                        push_portal.push_release(bundle, "https://portal.example", transport=transport)

                self.assertEqual(transport.requests, [])

    def test_manifest_rejects_more_than_one_hundred_items_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            template = dict(manifest["items"][0])
            template.update(
                {
                    "pluginId": "calendar",
                    "title": "Calendar",
                    "displayTitle": "日历",
                }
            )
            manifest["items"] = [{**template, "instanceId": f"calendar-{index}"} for index in range(101)]
            _rewrite_manifest(bundle, manifest)
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                with self.assertRaises(push_portal.BundleValidationError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)

        self.assertEqual(transport.requests, [])

    def test_manifest_accepts_worker_upper_bound_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            template = dict(manifest["items"][0])
            template.update(
                {
                    "pluginId": "calendar",
                    "title": "Calendar",
                    "displayTitle": "日历",
                }
            )
            manifest["playlist"] = {"slug": "s" * 128, "title": "t" * 160}
            manifest["timezone"] = "A" * 64
            manifest["items"] = [{**template, "instanceId": f"calendar-{index}"} for index in range(100)]
            maximum_timestamp = "2026-08-03T03:30:00." + "1" * 19 + "Z"
            manifest["publishedAt"] = maximum_timestamp
            manifest["items"][0].update(
                {
                    "instanceId": "i" * 128,
                    "pluginId": "p" * 128,
                    "title": "😀" * 80,
                    "displayTitle": "d" * 160,
                    "generatedAt": maximum_timestamp,
                    "sourceUpdatedAt": maximum_timestamp,
                }
            )
            _rewrite_manifest(bundle, manifest)
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                report = push_portal.push_release(
                    bundle,
                    "https://portal.example",
                    transport=transport,
                )

        self.assertEqual(report.asset_count, 1)
        self.assertEqual(len(transport.requests), 2)

    def test_manifest_rejects_duplicate_instance_identifiers_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            duplicate = dict(manifest["items"][0])
            duplicate.update(
                {
                    "pluginId": "calendar",
                    "title": "Calendar",
                    "displayTitle": "日历",
                }
            )
            manifest["items"].append(duplicate)
            _rewrite_manifest(bundle, manifest)
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                with self.assertRaises(push_portal.BundleValidationError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)

        self.assertEqual(transport.requests, [])

    def test_manifest_timestamps_match_the_worker_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            timestamp = "2026-08-03T03:30:00.0"
            manifest["publishedAt"] = timestamp
            manifest["items"][0]["generatedAt"] = timestamp
            manifest["items"][0]["sourceUpdatedAt"] = timestamp
            _rewrite_manifest(bundle, manifest)
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                report = push_portal.push_release(
                    bundle,
                    "https://portal.example",
                    transport=transport,
                )

            self.assertEqual(report.asset_count, 1)
            self.assertEqual(len(transport.requests), 2)

        invalid_timestamps = (
            "2026-08-03T03:30Z",
            "2026-08-03 03:30:00Z",
            "2026-W32-1T03:30:00Z",
            "2026-08-03T03:30:00,123Z",
            "2026-08-03T03:30:00+00:00:00",
            "2026-08-03T03:30:00.12345678901234567890+00:00",
        )
        for timestamp in invalid_timestamps:
            with self.subTest(timestamp=timestamp), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "release"
                _write_bundle(bundle)
                manifest_path = bundle / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["publishedAt"] = timestamp
                _rewrite_manifest(bundle, manifest)
                transport = _RecordingTransport()

                with mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ):
                    with self.assertRaises(push_portal.BundleValidationError):
                        push_portal.push_release(bundle, "https://portal.example", transport=transport)

                self.assertEqual(transport.requests, [])

    def test_duplicate_json_object_keys_are_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest_body = manifest_path.read_bytes()
            manifest_path.write_bytes(
                manifest_body.replace(
                    b'"schemaVersion":2',
                    b'"schemaVersion":2,"schemaVersion":2',
                    1,
                )
            )
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                with self.assertRaises(push_portal.BundleValidationError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)

        self.assertEqual(transport.requests, [])

    def test_undeclared_bundle_files_are_rejected_before_network(self) -> None:
        extras = (
            ("root file", Path("provider.json")),
            ("asset file", Path("assets") / "undeclared.png"),
            ("nested directory", Path("assets") / "cache" / "copy.png"),
        )
        for label, relative_path in extras:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "release"
                _write_bundle(bundle)
                extra_path = bundle / relative_path
                extra_path.parent.mkdir(parents=True, exist_ok=True)
                extra_path.write_bytes(b"must-not-be-uploaded")
                transport = _RecordingTransport()

                with mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ):
                    with self.assertRaises(push_portal.BundleValidationError):
                        push_portal.push_release(bundle, "https://portal.example", transport=transport)

                self.assertEqual(transport.requests, [])

    def test_asset_hash_mismatch_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _, _, asset_id = _write_bundle(bundle)
            (bundle / "assets" / f"{asset_id}.png").write_bytes(b"tampered")
            transport = _RecordingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                with self.assertRaises(push_portal.BundleValidationError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)

        self.assertEqual(transport.requests, [])

    def test_structurally_invalid_png_is_rejected_before_network(self) -> None:
        original = _png()
        corrupt_crc = bytearray(original)
        corrupt_crc[-5] ^= 0x01
        malformed_images = {
            "bad CRC": bytes(corrupt_crc),
            "trailing bytes": original + b"trailing",
            "truncated data": original[:-8],
            "wrong dimensions": _png(799, 480),
        }
        for label, malformed in malformed_images.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "release"
                _, _, old_asset_id = _write_bundle(bundle)
                _replace_asset(bundle, old_asset_id, malformed)
                transport = _RecordingTransport()

                with mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ):
                    with self.assertRaises(push_portal.BundleValidationError):
                        push_portal.push_release(bundle, "https://portal.example", transport=transport)

                self.assertEqual(transport.requests, [])

    def test_worker_body_size_limits_are_enforced_before_network(self) -> None:
        with self.subTest(body="manifest"), tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            manifest_path = bundle / "manifest.json"
            manifest_body = manifest_path.read_bytes()
            manifest_path.write_bytes(manifest_body + b" " * (128 * 1024 + 1 - len(manifest_body)))
            transport = _RecordingTransport()
            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                with self.assertRaises(push_portal.BundleValidationError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)
            self.assertEqual(transport.requests, [])

        with self.subTest(body="asset"), tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            asset_body, _, old_asset_id = _write_bundle(bundle)
            oversized = asset_body + bytes(1_000_001 - len(asset_body))
            _replace_asset(bundle, old_asset_id, oversized)
            transport = _RecordingTransport()
            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                with self.assertRaises(push_portal.BundleValidationError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)
            self.assertEqual(transport.requests, [])

    def test_network_failure_never_commits_manifest_or_mutates_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _, _, asset_id = _write_bundle(bundle)
            before = _bundle_snapshot(bundle)
            transport = _FailingTransport()

            with mock.patch.dict(
                os.environ,
                {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                clear=True,
            ):
                with self.assertRaises(push_portal.UploadError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)

            after = _bundle_snapshot(bundle)

        self.assertEqual(before, after)
        self.assertEqual(len(transport.requests), 1)
        self.assertTrue(transport.requests[0]["url"].endswith(f"/assets/{asset_id}"))

    def test_endpoint_and_signing_configuration_are_validated_before_network(self) -> None:
        invalid_endpoints = (
            "http://portal.example",
            "https://user@portal.example",
            "https://portal.example/path",
            "https://portal.example?debug=1",
        )
        for endpoint in invalid_endpoints:
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "release"
                _write_bundle(bundle)
                transport = _RecordingTransport()
                with mock.patch.dict(
                    os.environ,
                    {"EPAPER_PUBLISH_KEY": "test-publish-key-32-bytes-minimum"},
                    clear=True,
                ):
                    with self.assertRaises(push_portal.ConfigurationError):
                        push_portal.push_release(bundle, endpoint, transport=transport)
                self.assertEqual(transport.requests, [])

        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            transport = _RecordingTransport()
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(push_portal.ConfigurationError):
                    push_portal.push_release(bundle, "https://portal.example", transport=transport)
            self.assertEqual(transport.requests, [])

    def test_cli_help_has_no_key_argument_and_missing_key_is_safe(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                push_portal.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--bundle", stdout.getvalue())
        self.assertIn("--endpoint", stdout.getvalue())
        self.assertNotIn("--key", stdout.getvalue())

        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "release"
            _write_bundle(bundle)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = push_portal.main(
                        [
                            "--bundle",
                            str(bundle),
                            "--endpoint",
                            "https://portal.example",
                        ]
                    )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("EPAPER_PUBLISH_KEY", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
