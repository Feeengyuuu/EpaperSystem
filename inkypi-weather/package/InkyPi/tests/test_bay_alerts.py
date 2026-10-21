import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.bay_alerts import sources  # noqa: E402
from plugins.bay_alerts.bay_alerts import BayAlerts  # noqa: E402
from plugins.bay_alerts.render import attention_items, render_page  # noqa: E402
from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance  # noqa: E402

NOW = datetime(2026, 10, 21, 13, 0, tzinfo=sources.PACIFIC)
HOME = sources.DEFAULT_HOME


class Device:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal", "timezone": "America/Los_Angeles"}
        return values if key is None else values.get(key, default)

    def load_env_key(self, _key):
        return None


def _quake(id_, mag, hours_ago, lat=37.70, lon=-121.90, kind="earthquake", place="5 km NW of San Ramon, CA"):
    stamp = (NOW - timedelta(hours=hours_ago)).astimezone(timezone.utc)
    return {"id": id_, "properties": {"mag": mag, "time": stamp.timestamp() * 1000, "place": place, "type": kind},
            "geometry": {"type": "Point", "coordinates": [lon, lat, 8.2]}}


def test_quakes_drop_blasts_measure_distance_and_translate_places():
    data = sources.normalize_quakes({"features": [
        _quake("a", 2.4, 2), _quake("b", 1.9, 30), _quake("c", 2.0, 1, kind="quarry blast"),
    ]}, HOME, NOW)

    assert [item["id"] for item in data["items"]] == ["a", "b"]
    assert data["items"][0]["place"] == "San Ramon 西北 5 公里"
    assert 15 < data["items"][0]["distance_km"] < 25
    assert sources.valid_quakes(data)


def _alert(event, same, severity="Moderate", ends_in_hours=6, message="Alert"):
    return {"properties": {
        "id": event, "status": "Actual", "messageType": message, "event": event, "severity": severity,
        "geocode": {"SAME": same}, "headline": event,
        "onset": NOW.isoformat(), "ends": (NOW + timedelta(hours=ends_in_hours)).isoformat()},
        "geometry": None}


def test_alerts_keep_bay_counties_sorted_by_severity_and_translated():
    data = sources.normalize_alerts({"features": [
        _alert("Wind Advisory", ["006001"]),
        _alert("Red Flag Warning", ["006085", "006019"], severity="Severe"),
        _alert("Flood Watch", ["006037"]),
        _alert("Heat Advisory", ["006075"], ends_in_hours=-1),
        _alert("Dense Fog Advisory", ["006081"], message="Cancel"),
    ]}, sources.counties_setting(), NOW)

    assert [item["event"] for item in data["items"]] == ["Red Flag Warning", "Wind Advisory"]
    assert data["items"][0]["name"] == "红旗火险警告"
    assert data["items"][0]["kind"] == "fire"
    assert data["items"][0]["counties"] == ["Santa Clara"]


def test_eonet_events_are_cleaned_and_bounded_to_california():
    data = sources.normalize_events({"events": [
        {"id": "E1", "title": "Wildfire COOLEY, Mendocino, California", "categories": [{"id": "wildfires"}],
         "geometry": [{"type": "Point", "date": "2026-10-16T00:00:00Z", "coordinates": [-123.4, 39.5],
                       "magnitudeValue": 1365, "magnitudeUnit": "acres"}]},
        {"id": "E2", "title": "Wildfire - Far Away", "categories": [{"id": "wildfires"}],
         "geometry": [{"type": "Point", "date": "2026-10-16T00:00:00Z", "coordinates": [-100.0, 30.0]}]},
    ]}, NOW)

    assert [item["title"] for item in data["items"]] == ["COOLEY, Mendocino"]
    assert data["items"][0]["kind"] == "fire" and data["items"][0]["magnitude"] == 1365


def test_settings_validate_home_and_counties():
    assert sources.home_setting() == HOME
    assert sources.home_setting("37.4", "-122.1") == (37.4, -122.1)
    with pytest.raises(ValueError):
        sources.home_setting("40.7", "-74.0")
    assert sources.counties_setting("006075,006001,999999") == ("006075", "006001")
    with pytest.raises(ValueError):
        sources.counties_setting("999999")


def _payload(alerts=(), quakes=None, events=()):
    quakes = quakes if quakes is not None else sources.normalize_quakes(
        {"features": [_quake("a", 3.8, 2), _quake("b", 2.1, 40)]}, HOME, NOW)["items"]
    return {
        "quakes": {"state": "live", "data": {"items": quakes, "radius_km": 150}},
        "alerts": {"state": "live", "data": {"items": list(alerts), "counties": list(sources.BAY_COUNTIES)}},
        "events": {"state": "fresh_cache", "data": {"items": list(events)}},
        "home": list(HOME), "home_label": "家", "radius_km": 150, "counties": list(sources.BAY_COUNTIES),
    }


def test_attention_counts_alerts_and_strong_recent_quakes():
    alert = sources.normalize_alerts({"features": [_alert("Wind Advisory", ["006001"])]},
                                     sources.counties_setting(), NOW)["items"]

    assert attention_items(_payload(), NOW) == 1  # the M3.8 two hours ago
    assert attention_items(_payload(alerts=alert, quakes=[]), NOW) == 1
    assert attention_items(_payload(quakes=[]), NOW) == 0


@pytest.mark.parametrize("mode", ["day", "night"])
def test_page_renders_map_markers_offline(mode):
    image = render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, NOW)

    assert image.size == (800, 480)
    assert [marker["visible"] for marker in image.info["map_markers"]] == [True, True]


def test_generate_image_cache_only_redraw_uses_seeded_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    plugin = BayAlerts({"id": "bay_alerts"})
    monkeypatch.setattr(sources, "fetch_quakes", lambda *_args: _payload()["quakes"]["data"])
    monkeypatch.setattr(sources, "fetch_alerts", lambda *_args: {"items": [], "counties": ["006001"]})
    monkeypatch.setattr(sources, "fetch_events", lambda *_args: {"items": []})
    settings = {"_inkypi_theme": {"mode": "day"}}

    live = plugin.generate_image(settings, Device())
    for name in ("fetch_quakes", "fetch_alerts", "fetch_events"):
        monkeypatch.setattr(sources, name, lambda *_args: pytest.fail("cache-only redraw fetched"))
    cached = plugin.generate_image({**settings, "_theme_render_only": True}, Device())

    assert read_source_provenance(live) is SourceProvenance.LIVE
    assert read_source_provenance(cached) is SourceProvenance.FRESH_CACHE
