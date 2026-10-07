from __future__ import annotations

import io
import sys
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

from PIL import Image
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device_portal_capture import capture_device_portal_snapshot  # noqa: E402
from publication import PublicationModule  # noqa: E402
from publication_bootstrap import (  # noqa: E402
    attach_cached_raster_snapshot,
    publish_cached_raster_bundle,
)
import device_portal_capture as capture_module  # noqa: E402
from web_portal.factories import LedgerPublicationSource  # noqa: E402


NOW = datetime(2026, 8, 2, 21, 0, tzinfo=timezone.utc)
CSRF_CANARY = "csrf-canary-must-never-be-persisted"


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (800, 480), "#182b3f").save(output, format="PNG")
    return output.getvalue()


class _Response:
    def __init__(self, body: bytes, url: str, content_type: str):
        self._stream = io.BytesIO(body)
        self._url = url
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))

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
    def __init__(self, html: bytes, raster: bytes):
        self._html = html
        self._raster = raster
        self.requested: list[str] = []

    def open(self, request, timeout):
        del timeout
        url = request.full_url
        self.requested.append(url)
        if url == "http://device.local/playlist":
            return _Response(self._html, url, "text/html; charset=utf-8")
        if url == "http://device.local/plugin_instance_image/Drive/weather/Fremont%20Weather":
            return _Response(self._raster, url, "image/png")
        raise HTTPError(url, 404, "missing", {}, None)


def test_capture_sanitizes_playlist_and_publishes_exact_device_cache(tmp_path):
    html = f"""
    <html><body>
      <input name="csrf_token" value="{CSRF_CANARY}">
      <div class="playlist-list">
        <div class="playlist-item active">
          <div class="playlist-header">
            <span class="playlist-title">Drive</span>
            <button onclick="openEditModal('Drive', '06:00','23:30')"></button>
          </div>
          <div class="plugin-list">
            <div class="plugin-item">
              <span class="plugin-instance">Fremont Weather</span>
              <img class="plugin-thumbnail"
                   src="/plugin_instance_image/Drive/weather/Fremont%20Weather">
              <span class="latest-refresh" title="2026-08-02T20:30:00+00:00"></span>
              <button class="refresh-settings-btn" data-playlist="Drive"
                      data-plugin-id="weather" data-instance="Fremont Weather"
                      data-refresh='{{"interval": 600}}'></button>
            </div>
            <div class="plugin-item">
              <span class="plugin-instance">Sports</span>
              <button class="refresh-settings-btn" data-playlist="Drive"
                      data-plugin-id="sports_dashboard" data-instance="Sports"
                      data-refresh='{{"interval": 900}}'></button>
            </div>
          </div>
        </div>
      </div>
    </body></html>
    """.encode("utf-8")
    raster = _png()
    opener = _Opener(html, raster)
    capture_root = tmp_path / "capture"

    capture = capture_device_portal_snapshot(
        "http://device.local",
        capture_root,
        timezone_name="America/Los_Angeles",
        clock=lambda: NOW,
        opener=opener,
    )

    assert capture.configured == 2
    assert capture.available == 1
    assert capture.unavailable == 1
    assert opener.requested == [
        "http://device.local/playlist",
        "http://device.local/plugin_instance_image/Drive/weather/Fremont%20Weather",
    ]
    persisted = b"\n".join(
        path.read_bytes()
        for path in capture_root.rglob("*")
        if path.is_file()
    )
    assert CSRF_CANARY.encode("utf-8") not in persisted

    attachment = attach_cached_raster_snapshot(capture.bundle_dir, capture.snapshot_dir)
    assert attachment.available == 1
    ledger = tmp_path / "ledger"
    report = publish_cached_raster_bundle(capture.bundle_dir, ledger, clock=lambda: NOW)
    assert report.attempted == 2
    assert report.published == 1
    assert report.unavailable == 1

    source = LedgerPublicationSource(
        PublicationModule(
            ledger,
            read_only=True,
            timezone_name="America/Los_Angeles",
            clock=lambda: NOW,
        )
    )
    catalog = source.list_publications()
    assert catalog["playlists"][0]["name"] == "Drive"
    assert [item["title"] for item in catalog["publications"]] == [
        "Fremont Weather",
        "Sports",
    ]
    publication = source.get_publication(catalog["publications"][0]["slug"])
    assert source.get_asset(publication["asset_id"])["body"] == raster


@pytest.mark.parametrize(
    "thumbnail",
    [
        "/plugin_instance_image/../display_plugin_instance",
        "/plugin_instance_image/%2e%2e/display_plugin_instance",
        "/plugin_instance_image/Drive/weather/%252e%252e",
        "/plugin_instance_image/Drive/weather%2f..%2fdisplay_plugin_instance",
    ],
)
def test_capture_rejects_thumbnail_route_normalization_without_leaving_staging(tmp_path, thumbnail):
    html = f"""
    <html><body>
      <div class="playlist-item active">
        <span class="playlist-title">Drive</span>
        <div class="plugin-item">
          <span class="plugin-instance">Weather</span>
          <img class="plugin-thumbnail" src="{thumbnail}">
          <button class="refresh-settings-btn" data-plugin-id="weather"
                  data-instance="Weather" data-refresh='{{"interval": 600}}'></button>
        </div>
      </div>
    </body></html>
    """.encode("utf-8")
    opener = _Opener(html, _png())
    capture_root = tmp_path / "capture"

    with pytest.raises(ValueError, match="thumbnail URL"):
        capture_device_portal_snapshot(
            "http://device.local",
            capture_root,
            timezone_name="America/Los_Angeles",
            clock=lambda: NOW,
            opener=opener,
        )

    assert opener.requested == ["http://device.local/playlist"]
    assert not capture_root.exists()
    assert list(tmp_path.iterdir()) == []


def test_capture_network_failure_removes_private_staging_and_never_exposes_partial_output(tmp_path):
    html = b"""
    <html><body>
      <div class="playlist-item active">
        <span class="playlist-title">Drive</span>
        <div class="plugin-item">
          <span class="plugin-instance">Weather</span>
          <img class="plugin-thumbnail"
               src="/plugin_instance_image/Drive/weather/Fremont%20Weather">
          <button class="refresh-settings-btn" data-plugin-id="weather"
                  data-instance="Weather" data-refresh='{"interval": 600}'></button>
        </div>
        <div class="plugin-item">
          <span class="plugin-instance">Sports</span>
          <img class="plugin-thumbnail"
               src="/plugin_instance_image/Drive/sports_dashboard/Sports">
          <button class="refresh-settings-btn" data-plugin-id="sports_dashboard"
                  data-instance="Sports" data-refresh='{"interval": 600}'></button>
        </div>
      </div>
    </body></html>
    """

    class FailingSecondRaster(_Opener):
        def open(self, request, timeout):
            if request.full_url.endswith("/sports_dashboard/Sports"):
                self.requested.append(request.full_url)
                raise HTTPError(request.full_url, 503, "provider unavailable", {}, None)
            return super().open(request, timeout)

    capture_root = tmp_path / "capture"
    opener = FailingSecondRaster(html, _png())

    with pytest.raises(HTTPError) as error:
        capture_device_portal_snapshot(
            "http://device.local",
            capture_root,
            timezone_name="America/Los_Angeles",
            clock=lambda: NOW,
            opener=opener,
        )

    assert error.value.code == 503
    assert not capture_root.exists()
    assert list(tmp_path.iterdir()) == []


def test_capture_operator_interruption_removes_staging_without_masking_base_exception(tmp_path):
    html = b"""
    <html><body><div class="playlist-item active">
      <span class="playlist-title">Drive</span>
      <div class="plugin-item">
        <span class="plugin-instance">Weather</span>
        <img class="plugin-thumbnail"
             src="/plugin_instance_image/Drive/weather/Fremont%20Weather">
        <button class="refresh-settings-btn" data-plugin-id="weather"
                data-instance="Weather" data-refresh='{"interval": 600}'></button>
      </div>
    </div></body></html>
    """

    class InterruptedOpener(_Opener):
        def open(self, request, timeout):
            if request.full_url.endswith("Fremont%20Weather"):
                raise KeyboardInterrupt("simulated operator interruption")
            return super().open(request, timeout)

    capture_root = tmp_path / "capture"
    with pytest.raises(KeyboardInterrupt, match="operator interruption"):
        capture_device_portal_snapshot(
            "http://device.local",
            capture_root,
            timezone_name="America/Los_Angeles",
            clock=lambda: NOW,
            opener=InterruptedOpener(html, _png()),
        )

    assert not capture_root.exists()
    assert list(tmp_path.iterdir()) == []


def test_capture_commit_race_preserves_competing_target_and_removes_loser_staging(tmp_path, monkeypatch):
    html = b"""
    <html><body><div class="playlist-item active">
      <span class="playlist-title">Drive</span>
      <div class="plugin-item">
        <span class="plugin-instance">Weather</span>
        <img class="plugin-thumbnail"
             src="/plugin_instance_image/Drive/weather/Fremont%20Weather">
        <button class="refresh-settings-btn" data-plugin-id="weather"
                data-instance="Weather" data-refresh='{"interval": 600}'></button>
      </div>
    </div></body></html>
    """
    capture_root = tmp_path / "capture"

    def competing_install(_source, destination):
        destination = Path(destination)
        destination.mkdir()
        (destination / "winner.txt").write_text("other writer", encoding="utf-8")
        raise FileExistsError("competing capture committed first")

    monkeypatch.setattr(capture_module.os, "replace", competing_install)

    with pytest.raises(FileExistsError, match="capture output already exists"):
        capture_device_portal_snapshot(
            "http://device.local",
            capture_root,
            timezone_name="America/Los_Angeles",
            clock=lambda: NOW,
            opener=_Opener(html, _png()),
        )

    assert (capture_root / "winner.txt").read_text(encoding="utf-8") == "other writer"
    assert [path for path in tmp_path.iterdir() if ".capture-" in path.name] == []
