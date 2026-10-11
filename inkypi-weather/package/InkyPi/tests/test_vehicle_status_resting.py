"""A bridge-confirmed asleep/offline vehicle is current data, not a failing source.

The Epaper Vehicle Bridge never wakes a resting vehicle. It answers with its last
snapshot marked ``stale_cache`` plus the connectivity it just observed, so only
an ``unavailable``/unknown fallback means the upstream source is unhealthy.
"""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)
from plugins.vehicle_status import vehicle_status as vehicle_module  # noqa: E402
from tests.test_vehicle_status import (  # noqa: E402
    DeviceConfig,
    FakeHttpClient,
    _null_v2_summary,
    _plugin,
    _summary,
)


NOW = datetime.fromisoformat("2026-08-05T20:00:00+00:00").timestamp()


def _epoch(value):
    return datetime.fromisoformat(value).timestamp()


def _capture_rendered(monkeypatch, plugin):
    rendered = []
    render_summary = plugin._render_summary

    def capture(summary, *args, **kwargs):
        rendered.append(deepcopy(summary))
        return render_summary(summary, *args, **kwargs)

    monkeypatch.setattr(plugin, "_render_summary", capture)
    return rendered


def _forbid_render(monkeypatch, plugin):
    monkeypatch.setattr(
        plugin,
        "_render_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("expired vehicle values rendered")),
    )


def _empty_bridge_snapshot(captured_at, served_at, connectivity="asleep"):
    """Mirror the Worker's emptyVehicleSnapshot after it drops an expired cache."""
    payload = _null_v2_summary()
    payload["schema_version"] = 3
    payload["location"] = None
    payload["served_at"] = served_at
    payload["snapshot"].update(
        captured_at=captured_at,
        freshness="stale_cache",
        age_seconds=0,
        vehicle_connectivity=connectivity,
    )
    return payload


@pytest.mark.parametrize("connectivity", ["asleep", "offline"])
def test_bridge_confirmed_resting_vehicle_is_healthy_cacheable_data(tmp_path, monkeypatch, connectivity):
    plugin = _plugin(tmp_path)
    payload = _summary(freshness="stale_cache", connectivity=connectivity)
    payload["snapshot"].update(captured_at="2026-08-05T14:00:00Z", age_seconds=21_600)
    client = FakeHttpClient(payload)
    monkeypatch.setattr(vehicle_module.time, "time", lambda: NOW)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    rendered = _capture_rendered(monkeypatch, plugin)

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert len(client.calls) == 1
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert image.info.get("inkypi_skip_cache") is not True
    assert rendered[-1]["snapshot"] == {
        "captured_at": "2026-08-05T14:00:00Z",
        "freshness": "fresh_cache",
        "age_seconds": 21_600,
        "vehicle_connectivity": connectivity,
    }
    cached = plugin._read_cache()
    assert cached["fetched_at"] == NOW
    # The local cache keeps the bridge contract; only the attestation changes.
    assert cached["summary"]["snapshot"]["freshness"] == "stale_cache"
    assert cached["summary"]["snapshot"]["vehicle_connectivity"] == connectivity

    client.error = AssertionError("network called")
    again = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert len(client.calls) == 1
    assert read_source_provenance(again) is SourceProvenance.FRESH_CACHE
    assert again.info.get("inkypi_skip_cache") is not True


@pytest.mark.parametrize(
    ("reported", "connectivity"),
    [
        ("unavailable", "unavailable"),
        ("unknown", "unknown"),
        ("in_service", "in_service"),
        ("online", "online"),
        ("parked", "unknown"),
    ],
)
def test_bridge_stale_snapshot_without_resting_vehicle_stays_degraded(
    tmp_path, monkeypatch, reported, connectivity
):
    plugin = _plugin(tmp_path)
    client = FakeHttpClient(_summary(freshness="stale_cache", connectivity=reported))
    monkeypatch.setattr(vehicle_module.time, "time", lambda: NOW)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    rendered = _capture_rendered(monkeypatch, plugin)

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert len(client.calls) == 1
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True
    assert rendered[-1]["snapshot"]["freshness"] == "stale_cache"
    assert rendered[-1]["snapshot"]["vehicle_connectivity"] == connectivity


@pytest.mark.parametrize("token", ["bridge-token", None])
def test_bridge_failure_or_missing_token_keeps_resting_cache_degraded(tmp_path, monkeypatch, token):
    plugin = _plugin(tmp_path)
    plugin._write_cache(
        {
            "fetched_at": NOW - 901,
            "summary": _summary(freshness="stale_cache", connectivity="asleep"),
        }
    )
    original = plugin._cache_file().read_bytes()
    client = FakeHttpClient(error=TimeoutError("bridge unavailable"))
    monkeypatch.setattr(vehicle_module.time, "time", lambda: NOW)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    rendered = _capture_rendered(monkeypatch, plugin)

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig(token=token))

    assert len(client.calls) == (1 if token else 0)
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True
    assert rendered[-1]["snapshot"]["freshness"] == "stale_cache"
    assert rendered[-1]["snapshot"]["vehicle_connectivity"] == "asleep"
    assert plugin._cache_file().read_bytes() == original


@pytest.mark.parametrize(
    ("connectivity", "expected"),
    [
        ("asleep", SourceProvenance.FRESH_CACHE),
        ("offline", SourceProvenance.FRESH_CACHE),
        ("unavailable", SourceProvenance.STALE_CACHE),
        ("unknown", SourceProvenance.STALE_CACHE),
    ],
)
def test_local_cache_redraw_follows_cached_connectivity(tmp_path, monkeypatch, connectivity, expected):
    plugin = _plugin(tmp_path)
    plugin._write_cache(
        {
            "fetched_at": NOW - 300,
            "summary": _summary(freshness="stale_cache", connectivity=connectivity),
        }
    )
    original = plugin._cache_file().read_bytes()
    client = FakeHttpClient(error=AssertionError("network called"))
    monkeypatch.setattr(vehicle_module.time, "time", lambda: NOW)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    rendered = _capture_rendered(monkeypatch, plugin)
    device = DeviceConfig(token=None)

    redraw = plugin.generate_image({"cacheSeconds": 900, "_theme_render_only": True}, device)
    data = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert device.loaded == []
    assert client.calls == []
    assert plugin._cache_file().read_bytes() == original
    for image, summary in zip((redraw, data), rendered, strict=True):
        assert read_source_provenance(image) is expected
        assert summary["snapshot"]["freshness"] == expected.value
        assert summary["snapshot"]["vehicle_connectivity"] == connectivity
        assert (image.info.get("inkypi_skip_cache") is True) is (expected is SourceProvenance.STALE_CACHE)


def test_resting_cache_past_its_poll_window_redraws_as_stale(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    plugin._write_cache(
        {
            "fetched_at": NOW - 901,
            "summary": _summary(freshness="stale_cache", connectivity="asleep"),
        }
    )
    monkeypatch.setattr(vehicle_module.time, "time", lambda: NOW)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: FakeHttpClient(error=AssertionError("network")))

    image = plugin.generate_image({"cacheSeconds": 900, "_theme_render_only": True}, DeviceConfig(token=None))

    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True


def test_resting_status_for_same_snapshot_renews_the_poll_deadline(tmp_path, monkeypatch):
    now = _epoch("2026-08-05T20:15:00+00:00")
    plugin = _plugin(tmp_path)
    original = _summary(freshness="stale_cache", connectivity="unavailable")
    plugin._write_cache({"fetched_at": now - 901, "summary": original})
    updated = deepcopy(original)
    updated["served_at"] = "2026-08-05T20:15:00Z"
    updated["snapshot"].update(age_seconds=905, vehicle_connectivity="asleep")
    client = FakeHttpClient(updated)
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    rendered = _capture_rendered(monkeypatch, plugin)

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert image.info.get("inkypi_skip_cache") is not True
    # The same vehicle values never become younger than the local view.
    assert rendered[-1]["snapshot"]["age_seconds"] == 906
    cached = plugin._read_cache()
    assert cached["fetched_at"] == now
    assert cached["summary"]["snapshot"]["captured_at"] == "2026-08-05T19:59:55Z"
    assert cached["summary"]["snapshot"]["age_seconds"] == 906
    assert cached["summary"]["snapshot"]["vehicle_connectivity"] == "asleep"

    redraw = plugin.generate_image({"cacheSeconds": 900, "_theme_render_only": True}, DeviceConfig(token=None))

    assert len(client.calls) == 1
    assert read_source_provenance(redraw) is SourceProvenance.FRESH_CACHE
    assert rendered[-1]["snapshot"]["age_seconds"] == 906


@pytest.mark.parametrize(
    ("captured_at", "served_at"),
    [
        # The last full snapshot already aged out locally (weekend parking).
        ("2026-08-07T00:00:00+00:00", "2026-08-07T17:00:00+00:00"),
        # The bridge dropped its cache while the local snapshot was still usable.
        ("2026-08-08T10:00:00+00:00", "2026-08-08T19:00:00+00:00"),
    ],
)
def test_bridge_empty_snapshot_after_long_sleep_replaces_local_values(
    tmp_path, monkeypatch, captured_at, served_at
):
    now = _epoch("2026-08-08T20:00:00+00:00")
    plugin = _plugin(tmp_path)
    full = _summary(freshness="stale_cache", connectivity="asleep")
    full["served_at"] = served_at
    full["snapshot"].update(
        captured_at=captured_at,
        age_seconds=int(_epoch(served_at) - _epoch(captured_at)),
    )
    plugin._write_cache({"fetched_at": _epoch(served_at), "summary": full})
    empty = _empty_bridge_snapshot("2026-08-08T20:00:00Z", "2026-08-08T20:00:00Z")
    client = FakeHttpClient(empty)
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    rendered = _capture_rendered(monkeypatch, plugin)

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert len(client.calls) == 1
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert rendered[-1]["snapshot"] == {
        "captured_at": "2026-08-08T20:00:00Z",
        "freshness": "fresh_cache",
        "age_seconds": 0,
        "vehicle_connectivity": "asleep",
    }
    assert rendered[-1]["battery"]["level_percent"] is None
    cached = plugin._read_cache()
    assert cached["fetched_at"] == now
    assert cached["summary"]["snapshot"]["captured_at"] == "2026-08-08T20:00:00Z"
    assert cached["summary"]["battery"]["level_percent"] is None


@pytest.mark.parametrize("connectivity", ["asleep", "offline"])
def test_resting_vehicle_never_revives_values_older_than_24_hours(tmp_path, monkeypatch, connectivity):
    now = _epoch("2026-08-06T20:00:00+00:00")
    plugin = _plugin(tmp_path)
    original = _summary(freshness="stale_cache", connectivity=connectivity)
    plugin._write_cache({"fetched_at": now - 86_400, "summary": original})
    original_bytes = plugin._cache_file().read_bytes()
    # The bridge still serves its snapshot at exactly its own 24 hour limit, but
    # the local view of the same values is already a few seconds older.
    replay = deepcopy(original)
    replay["served_at"] = "2026-08-06T20:00:00Z"
    replay["snapshot"]["age_seconds"] = 86_400
    client = FakeHttpClient(replay)
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    _forbid_render(monkeypatch, plugin)

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert len(client.calls) == 1
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True
    assert plugin._cache_file().read_bytes() == original_bytes
