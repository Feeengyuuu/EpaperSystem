from __future__ import annotations

import binascii
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from email.message import Message
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
from urllib.error import HTTPError
import zlib


TOOLS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_ROOT))

import push_portal  # noqa: E402
import sync_device_portal  # noqa: E402


NOW = datetime(2026, 8, 3, 3, 30, 0, 123000, tzinfo=timezone.utc)
DEVICE_ORIGIN = "http://127.0.0.1"
IMAGE_PATH = "/plugin_instance_image/Drive/weather/Fremont%20Weather"
IMAGE_URL = DEVICE_ORIGIN + IMAGE_PATH
PIXIV_IMAGE_PATH = "/plugin_instance_image/Drive/pixiv_r18_ranking/Pixiv"
PIXIV_IMAGE_URL = DEVICE_ORIGIN + PIXIV_IMAGE_PATH
CALENDAR_IMAGE_PATH = "/plugin_instance_image/Drive/calendar/Calendar"
CALENDAR_IMAGE_URL = DEVICE_ORIGIN + CALENDAR_IMAGE_PATH
TEST_KEY = "test-device-sync-key-never-persist"


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", binascii.crc32(kind + body) & 0xFFFFFFFF)


def _zlib_stored(body: bytes) -> bytes:
    encoded = bytearray(b"\x78\x01")
    for offset in range(0, len(body), 65_535):
        block = body[offset : offset + 65_535]
        encoded.append(1 if offset + len(block) == len(body) else 0)
        encoded.extend(struct.pack("<HH", len(block), len(block) ^ 0xFFFF))
        encoded.extend(block)
    encoded.extend(struct.pack(">I", zlib.adler32(body) & 0xFFFFFFFF))
    return bytes(encoded)


def _png(width: int = 800, height: int = 480, shade: int = 0) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    row = b"\x00" + bytes([shade]) * ((width + 7) // 8)
    scanlines = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", _zlib_stored(scanlines))
        + _png_chunk(b"IEND", b"")
    )


def _playlist_html(
    *,
    image_source: str | None = IMAGE_PATH,
    source_updated_at: str = "2026-08-03T03:28:00Z",
    plugin_id: str = "weather",
    instance_name: str = "Fremont Weather",
    active_class: str = "active",
    refresh_interval: int | None = 120,
    second_refresh_interval: int | None = None,
) -> bytes:
    image = (
        ""
        if image_source is None
        else f'<img class="plugin-thumbnail" src="{image_source}">'
    )
    refresh = (
        'data-refresh=\'{"scheduled":"08:00"}\''
        if refresh_interval is None
        else f'data-refresh=\'{{"interval":{refresh_interval}}}\''
    )
    second_plugin = ""
    if second_refresh_interval is not None:
        second_plugin = f"""
        <div class="plugin-item">
          <span class="plugin-instance">Second Item</span>
          {image}
          <span class="latest-refresh" title="{source_updated_at}"></span>
          <button class="refresh-settings-btn" data-plugin-id="calendar"
                  data-instance="Second Item"
                  data-refresh='{{"interval":{second_refresh_interval}}}'></button>
        </div>
        """
    return f"""
    <!doctype html><html><body>
      <input name="csrf_token" value="csrf-value-must-not-persist">
      <div class="playlist-item">
        <span class="playlist-title">Night</span>
        <div class="plugin-item">
          <span class="plugin-instance">Inactive Item</span>
          <button class="refresh-settings-btn" data-plugin-id="calendar"
                  data-instance="Inactive Item"
                  data-refresh='{{"interval":30}}'></button>
        </div>
      </div>
      <div class="playlist-item {active_class}">
        <span class="playlist-title">Drive</span>
        <div class="plugin-item">
          <span class="plugin-instance">{instance_name}</span>
          {image}
          <span class="latest-refresh" title="{source_updated_at}"></span>
          <button class="refresh-settings-btn" data-plugin-id="{plugin_id}"
                  data-instance="{instance_name}" {refresh}></button>
        </div>
        {second_plugin}
      </div>
    </body></html>
    """.encode("utf-8")


def _playlist_html_with_pixiv() -> bytes:
    return f"""
    <!doctype html><html><body>
      <input name="csrf_token" value="csrf-value-must-not-persist">
      <div class="playlist-item active">
        <span class="playlist-title">Drive</span>
        <div class="plugin-item">
          <span class="plugin-instance">Fremont Weather</span>
          <img class="plugin-thumbnail" src="{IMAGE_PATH}">
          <span class="latest-refresh" title="2026-08-03T03:28:00Z"></span>
          <button class="refresh-settings-btn" data-plugin-id="weather"
                  data-instance="Fremont Weather" data-refresh='{{"interval":900}}'></button>
        </div>
        <div class="plugin-item">
          <span class="plugin-instance">Pixiv Ranking</span>
          <img class="plugin-thumbnail" src="{PIXIV_IMAGE_PATH}">
          <span class="latest-refresh" title="2026-08-03T03:29:00Z"></span>
          <button class="refresh-settings-btn" data-plugin-id="pixiv_r18_ranking"
                  data-instance="Pixiv Ranking" data-refresh='{{"interval":30}}'></button>
        </div>
        <div class="plugin-item">
          <span class="plugin-instance">Calendar</span>
          <img class="plugin-thumbnail" src="{CALENDAR_IMAGE_PATH}">
          <span class="latest-refresh" title="2026-08-03T03:27:00Z"></span>
          <button class="refresh-settings-btn" data-plugin-id="calendar"
                  data-instance="Calendar" data-refresh='{{"interval":120}}'></button>
        </div>
      </div>
    </body></html>
    """.encode("utf-8")


class _Response:
    def __init__(
        self,
        body: bytes,
        url: str,
        content_type: str,
        *,
        headers: dict[str, str] | None = None,
    ):
        self._stream = io.BytesIO(body)
        self._url = url
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))
        for name, value in (headers or {}).items():
            self.headers[name] = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self._stream.close()


class _Opener:
    def __init__(
        self,
        html: bytes,
        image: bytes | Exception | None = None,
        *,
        images: dict[str, bytes | Exception] | None = None,
    ):
        self.html = html
        self.image = image
        self.images = images or {}
        self.requests: list[dict[str, object]] = []

    def open(self, request, timeout):
        self.requests.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "timeout": timeout,
                "headers": dict(request.header_items()),
            }
        )
        if request.full_url == f"{DEVICE_ORIGIN}/playlist":
            return _Response(self.html, request.full_url, "text/html; charset=utf-8")
        if request.full_url in self.images:
            image = self.images[request.full_url]
            if isinstance(image, Exception):
                raise image
            return _Response(image, request.full_url, "image/png")
        if request.full_url == IMAGE_URL:
            if isinstance(self.image, Exception):
                raise self.image
            if isinstance(self.image, bytes):
                return _Response(self.image, request.full_url, "image/png")
        raise AssertionError(f"unexpected request: {request.full_url}")


class _PushRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.failure: Exception | None = None

    def __call__(self, bundle, endpoint, *, timeout=30.0):
        bundle = Path(bundle)
        snapshot = {
            path.relative_to(bundle).as_posix(): path.read_bytes() for path in bundle.rglob("*") if path.is_file()
        }
        self.calls.append(
            {
                "bundle": snapshot,
                "endpoint": endpoint,
                "timeout": timeout,
            }
        )
        if self.failure is not None:
            raise self.failure
        manifest_body = snapshot["manifest.json"]
        return push_portal.PushReport(
            asset_count=len([name for name in snapshot if name.startswith("assets/")]),
            edition_id=hashlib.sha256(manifest_body).hexdigest(),
        )


def _sync(state_dir: Path, opener: _Opener, push: _PushRecorder, *, clock=lambda: NOW):
    return sync_device_portal.sync_device_portal(
        endpoint="https://portal.example",
        state_dir=state_dir,
        timezone_name="America/Los_Angeles",
        device_url=DEVICE_ORIGIN,
        opener=opener,
        push=push,
        clock=clock,
        timeout=12.5,
    )


def _state(state_dir: Path) -> dict[str, object]:
    return json.loads((state_dir / "state.json").read_text(encoding="utf-8"))


class SyncDevicePortalTests(unittest.TestCase):
    def test_publishes_exact_active_playlist_bundle_using_only_read_only_gets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            image = _png()
            opener = _Opener(_playlist_html(), image)
            push = _PushRecorder()

            with mock.patch.dict(os.environ, {"EPAPER_PUBLISH_KEY": TEST_KEY}, clear=True):
                report = _sync(state_dir, opener, push)

            state_document = _state(state_dir)
            persisted = b"\n".join(path.read_bytes() for path in state_dir.rglob("*") if path.is_file())
            remaining_names = {path.name for path in state_dir.iterdir()}

        self.assertEqual(report.outcome, "published")
        self.assertEqual(report.item_count, 1)
        self.assertEqual(
            [(request["method"], request["url"]) for request in opener.requests],
            [
                ("GET", f"{DEVICE_ORIGIN}/playlist"),
                ("GET", IMAGE_URL),
            ],
        )
        self.assertEqual(len(push.calls), 1)
        self.assertEqual(push.calls[0]["endpoint"], "https://portal.example")
        self.assertEqual(push.calls[0]["timeout"], 12.5)

        bundle = push.calls[0]["bundle"]
        asset_id = hashlib.sha256(image).hexdigest()
        self.assertEqual(set(bundle), {"manifest.json", f"assets/{asset_id}.png"})
        self.assertEqual(bundle[f"assets/{asset_id}.png"], image)
        manifest = json.loads(bundle["manifest.json"].decode("utf-8"))
        self.assertEqual(
            set(manifest),
            {
                "schemaVersion",
                "generation",
                "publishedAt",
                "timezone",
                "playlist",
                "minimumRefreshIntervalSeconds",
                "items",
            },
        )
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual(manifest["minimumRefreshIntervalSeconds"], 120)
        self.assertEqual(manifest["generation"], int(NOW.timestamp() * 1000))
        self.assertEqual(manifest["publishedAt"], "2026-08-03T03:30:00.123Z")
        self.assertEqual(manifest["timezone"], "America/Los_Angeles")
        self.assertEqual(manifest["playlist"]["title"], "Drive")
        self.assertRegex(manifest["playlist"]["slug"], r"\A[A-Za-z0-9._:-]{1,128}\Z")
        self.assertEqual(len(manifest["items"]), 1)
        item = manifest["items"][0]
        self.assertEqual(
            set(item),
            {
                "instanceId",
                "pluginId",
                "title",
                "displayTitle",
                "assetId",
                "generatedAt",
                "sourceUpdatedAt",
                "width",
                "height",
            },
        )
        self.assertRegex(item["instanceId"], r"\A[A-Za-z0-9._:-]{1,128}\Z")
        self.assertEqual(item["pluginId"], "weather")
        self.assertEqual(item["title"], "Fremont Weather")
        self.assertEqual(item["displayTitle"], "当地天气")
        self.assertEqual(item["assetId"], asset_id)
        self.assertEqual(item["generatedAt"], "2026-08-03T03:28:00.000Z")
        self.assertEqual(item["sourceUpdatedAt"], "2026-08-03T03:28:00.000Z")
        self.assertEqual((item["width"], item["height"]), (800, 480))

        self.assertEqual(state_document["schemaVersion"], 1)
        self.assertEqual(state_document["status"]["outcome"], "published")
        self.assertEqual(state_document["lastGood"]["fingerprint"], report.fingerprint)
        self.assertEqual(state_document["lastGood"]["generation"], report.generation)
        self.assertNotIn(TEST_KEY.encode("utf-8"), persisted)
        self.assertNotIn(b"csrf-value-must-not-persist", persisted)
        self.assertEqual(remaining_names, {"state.json", "sync.lock"})

    def test_unchanged_fingerprint_is_a_publish_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()
            first = _sync(state_dir, _Opener(_playlist_html(), _png()), push)
            first_good = _state(state_dir)["lastGood"]
            second = _sync(
                state_dir,
                _Opener(_playlist_html(), _png()),
                push,
                clock=lambda: datetime(2026, 8, 3, 4, 30, tzinfo=timezone.utc),
            )
            second_state = _state(state_dir)

        self.assertEqual(first.outcome, "published")
        self.assertEqual(second.outcome, "unchanged")
        self.assertEqual(len(push.calls), 1)
        self.assertEqual(second_state["lastGood"], first_good)
        self.assertEqual(second_state["status"]["outcome"], "unchanged")

    def test_uses_only_the_active_playlists_shortest_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()
            report = _sync(
                state_dir,
                _Opener(
                    _playlist_html(refresh_interval=900, second_refresh_interval=120),
                    _png(),
                ),
                push,
            )
            manifest = json.loads(push.calls[0]["bundle"]["manifest.json"].decode("utf-8"))

        self.assertEqual(report.outcome, "published")
        self.assertEqual(report.item_count, 2)
        self.assertEqual(manifest["minimumRefreshIntervalSeconds"], 120)

    def test_web_publication_excludes_pixiv_before_fetch_and_preserves_remaining_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            weather_image = _png(shade=0)
            pixiv_image = _png(shade=127)
            calendar_image = _png(shade=255)
            opener = _Opener(
                _playlist_html_with_pixiv(),
                images={
                    IMAGE_URL: weather_image,
                    PIXIV_IMAGE_URL: pixiv_image,
                    CALENDAR_IMAGE_URL: calendar_image,
                },
            )
            push = _PushRecorder()

            report = _sync(state_dir, opener, push)
            bundle = push.calls[0]["bundle"]
            manifest = json.loads(bundle["manifest.json"].decode("utf-8"))

        self.assertEqual(report.item_count, 2)
        self.assertEqual(
            [item["pluginId"] for item in manifest["items"]],
            ["weather", "calendar"],
        )
        self.assertEqual(manifest["minimumRefreshIntervalSeconds"], 120)
        self.assertEqual(
            [request["url"] for request in opener.requests],
            [f"{DEVICE_ORIGIN}/playlist", IMAGE_URL, CALENDAR_IMAGE_URL],
        )
        self.assertNotIn(f"assets/{hashlib.sha256(pixiv_image).hexdigest()}.png", bundle)

    def test_web_publication_rejects_an_active_playlist_with_only_pixiv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            opener = _Opener(
                _playlist_html(
                    image_source=PIXIV_IMAGE_PATH,
                    plugin_id="pixiv_r18_ranking",
                    instance_name="Pixiv Ranking",
                    refresh_interval=30,
                ),
                images={PIXIV_IMAGE_URL: _png()},
            )
            push = _PushRecorder()

            with self.assertRaises(sync_device_portal.SyncPortalError) as raised:
                _sync(state_dir, opener, push)

        self.assertEqual(raised.exception.code, "capture_invalid")
        self.assertEqual(
            [request["url"] for request in opener.requests],
            [f"{DEVICE_ORIGIN}/playlist"],
        )
        self.assertEqual(push.calls, [])

    def test_web_publication_exclusion_matches_only_the_exact_pixiv_plugin_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()

            report = _sync(
                state_dir,
                _Opener(
                    _playlist_html(plugin_id="pixiv_r18_ranking_preview"),
                    _png(),
                ),
                push,
            )
            manifest = json.loads(push.calls[0]["bundle"]["manifest.json"].decode("utf-8"))

        self.assertEqual(report.item_count, 1)
        self.assertEqual(manifest["items"][0]["pluginId"], "pixiv_r18_ranking_preview")

    def test_pixiv_only_changes_are_a_web_publication_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()
            images = {
                IMAGE_URL: _png(shade=0),
                CALENDAR_IMAGE_URL: _png(shade=255),
            }
            first = _sync(
                state_dir,
                _Opener(_playlist_html_with_pixiv(), images=images),
                push,
            )
            changed_html = (
                _playlist_html_with_pixiv()
                .replace(b"2026-08-03T03:29:00Z", b"2026-08-03T04:29:00Z")
                .replace(b'{"interval":30}', b'{"interval":45}')
                .replace(PIXIV_IMAGE_PATH.encode("ascii"), b"/plugin_instance_image/Drive/pixiv_r18_ranking/Pixiv2")
            )
            second_opener = _Opener(changed_html, images=images)
            second = _sync(
                state_dir,
                second_opener,
                push,
                clock=lambda: datetime(2026, 8, 3, 4, 30, tzinfo=timezone.utc),
            )

        self.assertEqual(first.outcome, "published")
        self.assertEqual(second.outcome, "unchanged")
        self.assertEqual(second.fingerprint, first.fingerprint)
        self.assertEqual(len(push.calls), 1)
        self.assertEqual(
            [request["url"] for request in second_opener.requests],
            [f"{DEVICE_ORIGIN}/playlist", IMAGE_URL, CALENDAR_IMAGE_URL],
        )

    def test_scheduled_only_playlist_uses_the_safe_default_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()
            _sync(
                state_dir,
                _Opener(_playlist_html(refresh_interval=None), _png()),
                push,
            )
            manifest = json.loads(push.calls[0]["bundle"]["manifest.json"].decode("utf-8"))

        self.assertEqual(manifest["minimumRefreshIntervalSeconds"], 300)

    def test_invalid_or_non_integer_refresh_values_use_the_safe_default_without_leaking(self) -> None:
        refresh_values = (
            b'{"interval":true}',
            b'{"interval":1.5}',
            b'{"interval":"1","providerSecret":"must-not-leak"}',
            b'{"interval":0}',
            b'{"interval":-1}',
            b'{broken-json}',
        )
        for refresh_value in refresh_values:
            with self.subTest(refresh_value=refresh_value), tempfile.TemporaryDirectory() as temporary:
                state_dir = Path(temporary) / "state"
                html = _playlist_html().replace(b'{"interval":120}', refresh_value, 1)
                push = _PushRecorder()
                _sync(state_dir, _Opener(html, _png()), push)
                manifest_body = push.calls[0]["bundle"]["manifest.json"]
                manifest = json.loads(manifest_body.decode("utf-8"))
                persisted = b"\n".join(
                    path.read_bytes() for path in state_dir.rglob("*") if path.is_file()
                )

                self.assertEqual(manifest["minimumRefreshIntervalSeconds"], 300)
                self.assertNotIn(b"must-not-leak", manifest_body)
                self.assertNotIn(b"must-not-leak", persisted)

    def test_interval_change_publishes_a_new_manifest_even_when_images_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()
            first = _sync(
                state_dir,
                _Opener(_playlist_html(refresh_interval=120), _png()),
                push,
            )
            second = _sync(
                state_dir,
                _Opener(_playlist_html(refresh_interval=300), _png()),
                push,
                clock=lambda: datetime(2026, 8, 3, 4, 30, tzinfo=timezone.utc),
            )
            second_manifest = json.loads(push.calls[1]["bundle"]["manifest.json"].decode("utf-8"))

        self.assertEqual(first.outcome, "published")
        self.assertEqual(second.outcome, "published")
        self.assertEqual(second_manifest["minimumRefreshIntervalSeconds"], 300)

    def test_changed_content_uses_a_monotonic_millisecond_generation(self) -> None:
        later = datetime(2026, 8, 3, 3, 30, 0, 123000, tzinfo=timezone.utc)
        earlier = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()
            first = _sync(state_dir, _Opener(_playlist_html(), _png(shade=0)), push, clock=lambda: later)
            second = _sync(state_dir, _Opener(_playlist_html(), _png(shade=255)), push, clock=lambda: earlier)
            second_manifest = json.loads(push.calls[1]["bundle"]["manifest.json"].decode("utf-8"))

        self.assertEqual(first.outcome, "published")
        self.assertEqual(second.outcome, "published")
        self.assertEqual(second.generation, first.generation + 1)
        self.assertEqual(second_manifest["generation"], first.generation + 1)

    def test_publish_failure_preserves_last_good_fingerprint_and_redacts_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()
            first = _sync(state_dir, _Opener(_playlist_html(), _png()), push)
            previous_good = _state(state_dir)["lastGood"]
            push.failure = push_portal.UploadError("network failure containing should-not-be-copied details")

            with mock.patch.dict(os.environ, {"EPAPER_PUBLISH_KEY": TEST_KEY}, clear=True):
                with self.assertRaises(sync_device_portal.SyncPortalError) as raised:
                    _sync(state_dir, _Opener(_playlist_html(), _png(shade=255)), push)

            failed_state = _state(state_dir)
            persisted = b"\n".join(path.read_bytes() for path in state_dir.rglob("*") if path.is_file())

        self.assertEqual(first.fingerprint, previous_good["fingerprint"])
        self.assertEqual(failed_state["lastGood"], previous_good)
        self.assertEqual(failed_state["status"]["outcome"], "failed")
        self.assertEqual(failed_state["status"]["errorCode"], "publish_failed")
        self.assertNotIn("should-not-be-copied", str(raised.exception))
        self.assertNotIn(b"should-not-be-copied", persisted)
        self.assertNotIn(TEST_KEY.encode("utf-8"), persisted)

    def test_failed_upload_reserves_generation_before_the_next_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()
            first = _sync(state_dir, _Opener(_playlist_html(), _png()), push)
            push.failure = push_portal.UploadError("simulated rejection")
            with self.assertRaises(sync_device_portal.SyncPortalError):
                _sync(state_dir, _Opener(_playlist_html(), _png(shade=255)), push)
            reserved_after_failure = _state(state_dir)["reservedGeneration"]

            push.failure = None
            recovered = _sync(state_dir, _Opener(_playlist_html(), _png(shade=255)), push)

        self.assertGreater(reserved_after_failure, first.generation)
        self.assertEqual(recovered.generation, reserved_after_failure + 1)

    def test_final_state_write_failure_never_advances_local_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            push = _PushRecorder()
            original_write = sync_device_portal._atomic_state_write
            write_count = 0

            def fail_only_final_write(path, state):
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise OSError("simulated final state fsync failure")
                return original_write(path, state)

            with (
                mock.patch.dict(os.environ, {"EPAPER_PUBLISH_KEY": TEST_KEY}, clear=True),
                mock.patch.object(
                    sync_device_portal,
                    "_atomic_state_write",
                    side_effect=fail_only_final_write,
                ),
                self.assertRaises(sync_device_portal.SyncPortalError) as raised,
            ):
                _sync(state_dir, _Opener(_playlist_html(), _png()), push)

            persisted = _state(state_dir)

        self.assertEqual(raised.exception.code, "internal_error")
        self.assertEqual(len(push.calls), 1)
        self.assertIsNone(persisted["lastGood"])
        self.assertEqual(persisted["status"]["outcome"], "failed")

    def test_missing_or_unsafe_thumbnail_is_rejected_without_following_it(self) -> None:
        unsafe_sources = (
            None,
            "https://evil.example/plugin_instance_image/Drive/weather/Fremont%20Weather",
            "/plugin_instance_image/../display_plugin_instance",
            "/plugin_instance_image/Drive/weather/%252e%252e",
            "/refresh_plugin_instance",
        )
        for source in unsafe_sources:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                state_dir = Path(temporary) / "state"
                opener = _Opener(_playlist_html(image_source=source))
                push = _PushRecorder()

                with self.assertRaises(sync_device_portal.SyncPortalError):
                    _sync(state_dir, opener, push)

                self.assertEqual([request["url"] for request in opener.requests], [f"{DEVICE_ORIGIN}/playlist"])
                self.assertEqual(push.calls, [])

    def test_every_active_item_requires_a_complete_valid_png_within_worker_limit(self) -> None:
        valid = _png()
        bad_crc = bytearray(valid)
        bad_crc[-1] ^= 1
        invalid_images = {
            "wrong dimensions": _png(799, 480),
            "bad CRC": bytes(bad_crc),
            "trailing data": valid + b"x",
            "oversized": valid + bytes(1_000_001 - len(valid)),
            "missing": HTTPError(IMAGE_URL, 404, "missing", {}, None),
        }
        for label, image in invalid_images.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                state_dir = Path(temporary) / "state"
                opener = _Opener(_playlist_html(), image)
                push = _PushRecorder()

                with self.assertRaises(sync_device_portal.SyncPortalError):
                    _sync(state_dir, opener, push)

                self.assertEqual(push.calls, [])
                state_document = _state(state_dir)
                self.assertIsNone(state_document["lastGood"])
                self.assertEqual(state_document["status"]["outcome"], "failed")

    def test_capture_rejects_an_aggregate_asset_set_above_the_memory_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            image = _png()
            opener = _Opener(_playlist_html(), image)
            push = _PushRecorder()

            with (
                mock.patch.object(
                    sync_device_portal,
                    "_MAX_TOTAL_ASSET_BYTES",
                    len(image) - 1,
                ),
                self.assertRaises(sync_device_portal.SyncPortalError) as raised,
            ):
                _sync(state_dir, opener, push)

        self.assertEqual(raised.exception.code, "capture_invalid")
        self.assertEqual(push.calls, [])

    def test_nonblocking_process_lock_returns_busy_before_device_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            state_dir.mkdir()
            held_lock = sync_device_portal._ProcessLock(state_dir / "sync.lock")
            self.assertTrue(held_lock.acquire())
            try:
                opener = _Opener(_playlist_html(), _png())
                push = _PushRecorder()
                report = _sync(state_dir, opener, push)
            finally:
                held_lock.release()

        self.assertEqual(report.outcome, "busy")
        self.assertEqual(opener.requests, [])
        self.assertEqual(push.calls, [])

    def test_cli_returns_retryable_status_when_synchronization_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            state_dir.mkdir()
            held_lock = sync_device_portal._ProcessLock(state_dir / "sync.lock")
            self.assertTrue(held_lock.acquire())
            stdout = io.StringIO()
            stderr = io.StringIO()
            try:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = sync_device_portal.main(
                        [
                            "--endpoint",
                            "https://portal.example",
                            "--state-dir",
                            str(state_dir),
                        ]
                    )
            finally:
                held_lock.release()

        self.assertEqual(exit_code, 3)
        self.assertIn("already running", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_has_no_key_argument_and_configuration_errors_are_bounded(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                sync_device_portal.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertNotIn("--key", stdout.getvalue())

        secret = "query-secret-must-not-be-printed"
        with tempfile.TemporaryDirectory() as temporary:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {"EPAPER_PUBLISH_KEY": TEST_KEY}, clear=True):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = sync_device_portal.main(
                        [
                            "--endpoint",
                            "https://portal.example",
                            "--state-dir",
                            str(Path(temporary) / "state"),
                            "--device-url",
                            f"http://127.0.0.1/playlist?token={secret}",
                        ]
                    )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertLessEqual(len(stderr.getvalue()), 240)
        self.assertNotIn(secret, stderr.getvalue())
        self.assertNotIn(TEST_KEY, stderr.getvalue())

    def test_event_watcher_uses_kernel_file_events_and_never_probes_current_png(self) -> None:
        watcher = Path(__file__).with_name("watch_device_portal.sh").read_text(encoding="ascii")

        self.assertIn("/usr/bin/inotifywait", watcher)
        self.assertIn("display_revision", watcher)
        self.assertIn("/usr/bin/flock", watcher)
        self.assertIn("publish_with_retry", watcher)
        self.assertIn('exec 3<"$event_dir/events"', watcher)
        self.assertIn("--event delete_self", watcher)
        self.assertIn("--event move_self", watcher)
        self.assertIn("DELETE_SELF", watcher)
        self.assertIn("MOVE_SELF", watcher)
        self.assertNotIn('exec 3<>"$event_dir/events"', watcher)
        self.assertNotIn("/api/current_image", watcher)
        self.assertNotIn("EPAPER_PUBLISH_KEY=\"$(cat", watcher)

    def test_cli_has_no_polling_watch_or_secret_arguments(self) -> None:
        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(stdout):
            sync_device_portal.main(["--help"])

        help_text = stdout.getvalue()
        self.assertEqual(raised.exception.code, 0)
        self.assertNotIn("--watch", help_text)
        self.assertNotIn("--poll-interval", help_text)
        self.assertNotIn("--reconcile-interval", help_text)
        self.assertNotIn("publish-key", help_text)


if __name__ == "__main__":
    unittest.main()
