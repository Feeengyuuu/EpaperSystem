import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.bay_alerts import sources  # noqa: E402
from plugins.bay_alerts.bay_alerts import BayAlerts  # noqa: E402
from plugins.bay_alerts import render  # noqa: E402
from plugins.bay_alerts.render import attention_items, listed_quakes, plan_column, render_page  # noqa: E402
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


@pytest.mark.parametrize(
    "counts",
    [
        {"alerts": 0, "quakes": 27, "events": 8},
        {"alerts": 2, "quakes": 27, "events": 8},
        {"alerts": 5, "quakes": 27, "events": 8},
        {"alerts": 30, "quakes": 300, "events": 40},
        {"alerts": 0, "quakes": 0, "events": 0},
        {"alerts": 1, "quakes": 1, "events": 1},
    ],
)
def test_column_cards_always_fill_the_column_and_never_list_more_than_they_have(counts):
    rows, heights = plan_column(counts)

    assert sum(heights.values()) == render.COLUMN_SPACE
    for name, shown in rows.items():
        assert 0 <= shown <= counts[name]


def test_an_all_clear_alert_card_shrinks_so_quakes_and_events_show_more():
    rows, heights = plan_column({"alerts": 0, "quakes": 27, "events": 8})

    assert heights["alerts"] == 80
    # The fixed layout listed two quakes and two events.
    assert rows["quakes"] > 2 and rows["events"] > 2


def test_alerts_take_the_space_first_and_the_cards_below_shrink():
    quiet, _ = plan_column({"alerts": 0, "quakes": 27, "events": 8})
    two, _ = plan_column({"alerts": 2, "quakes": 27, "events": 8})
    busy, heights = plan_column({"alerts": 5, "quakes": 27, "events": 8})

    assert two["alerts"] == 2
    assert two["quakes"] + two["events"] < quiet["quakes"] + quiet["events"]
    # A busy warning day: the cards below keep only their smallest useful size.
    assert (busy["quakes"], busy["events"]) == (1, 2)
    assert busy["alerts"] == 3 and heights["alerts"] > heights["quakes"]


def test_listed_quakes_prefer_m2_but_fill_with_smaller_ones_newest_first():
    items = [{"id": "a", "mag": 1.2}, {"id": "b", "mag": 2.5}, {"id": "c", "mag": 1.8}, {"id": "d", "mag": 3.1}]

    assert [item["id"] for item in listed_quakes(items, 1)] == ["b"]
    assert [item["id"] for item in listed_quakes(items, 3)] == ["a", "b", "d"]
    assert [item["id"] for item in listed_quakes(items, 9)] == ["a", "b", "c", "d"]


def test_page_lays_the_cards_out_top_to_bottom_without_overlap():
    alerts = sources.normalize_alerts({"features": [_alert("Wind Advisory", ["006001"])]},
                                      sources.counties_setting(), NOW)["items"]

    image = render_page((800, 480), _payload(alerts=alerts), {"_inkypi_theme": {"mode": "day"}}, NOW)

    cards = image.info["column_cards"]
    boxes = [cards[name]["box"] for name in ("alerts", "quakes", "events")]
    assert boxes[0][1] == render.COLUMN_TOP and boxes[-1][3] == render.COLUMN_BOTTOM
    assert all(upper[3] + render.CARD_GAP == lower[1] for upper, lower in zip(boxes, boxes[1:]))
    assert cards["alerts"]["rows"] == 1


def test_zoomed_map_asset_matches_its_manifest_and_frames_the_inner_bay():
    import hashlib

    from plugins.bay_alerts import map_view

    manifest = map_view.read_manifest(map_view.BAY_MAP)
    asset = map_view.ASSETS / f"{map_view.BAY_MAP}.png"
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == manifest["asset_sha256"]
    size = tuple(manifest["image_size"])
    for name in ("瓦列霍", "旧金山", "圣何塞", "半月湾", "利弗莫尔"):
        lat, lon = next((lat, lon) for city, lat, lon, _size in map_view.CITIES if city == name)
        assert map_view.project(lat, lon, manifest, size) is not None, name
    # Napa and Santa Cruz are outside the zoomed frame now.
    assert map_view.project(38.2975, -122.2869, manifest, size) is None
    assert map_view.project(36.9741, -122.0308, manifest, size) is None
    assert map_view.project(36.9741, -122.0308, manifest, size, clip=False)[1] > size[1]


def test_bridges_airports_and_fault_names_land_on_the_map():
    from plugins.bay_alerts import map_view

    manifest = map_view.read_manifest(map_view.BAY_MAP)
    size = tuple(manifest["image_size"])
    water, faults, _shade = map_view._layers(map_view.BAY_MAP, manifest)
    for name, points in map_view.BRIDGES:
        pixels = [map_view.project(lat, lon, manifest, size) for lat, lon in points]
        assert all(pixels), name
        samples = [
            water.getpixel((int(x0 + (x1 - x0) * step / 20), int(y0 + (y1 - y0) * step / 20)))
            for (x0, y0), (x1, y1) in zip(pixels, pixels[1:]) for step in range(21)
        ]
        # Most of a deck is over water (the Bay Bridge crosses Yerba Buena Island midway).
        assert sum(1 for value in samples if value) / len(samples) >= 0.5, name
    for code, lat, lon in map_view.AIRPORTS:
        point = map_view.project(lat, lon, manifest, size)
        assert point and not water.getpixel((int(point[0]), int(point[1]))), code
    for name, lat, lon, _side in map_view.FAULT_LABELS:
        anchor = map_view.project(lat, lon, manifest, size)
        assert map_view._fault_frame(faults, anchor, size) is not None, name


def test_recent_quakes_beyond_the_map_get_an_edge_arrow_and_old_ones_do_not():
    quakes = sources.normalize_quakes({"features": [
        _quake("gilroy", 3.1, 3, lat=36.95, lon=-121.55),
        _quake("old", 3.4, 120, lat=36.80, lon=-121.40),
        _quake("near", 2.2, 2),
    ]}, HOME, NOW)["items"]

    image = render_page((800, 480), _payload(quakes=quakes), {"_inkypi_theme": {"mode": "day"}}, NOW)

    markers = {marker["id"]: marker for marker in image.info["map_markers"]}
    assert markers["near"]["visible"] and not markers["near"]["edge"]
    assert not markers["gilroy"]["visible"] and markers["gilroy"]["edge"]
    assert not markers["old"]["visible"] and not markers["old"]["edge"]


def _events(count):
    return sources.normalize_events({"events": [
        {"id": f"E{index}", "title": f"Wildfire F{index}, Placer, California", "categories": [{"id": "wildfires"}],
         "geometry": [{"type": "Point", "date": "2026-10-16T00:00:00Z", "coordinates": [-121.0, 38.9 - index * 0.3]}]}
        for index in range(count)
    ]}, NOW)["items"]


def test_event_minimap_grows_with_its_card_and_stays_centred():
    alerts = sources.normalize_alerts({"features": [
        _alert(f"Wind Advisory {index}", ["006001"]) for index in range(5)
    ]}, sources.counties_setting(), NOW)["items"]
    settings = {"_inkypi_theme": {"mode": "day"}}

    roomy = render_page((800, 480), _payload(events=_events(6)), settings, NOW)
    tight = render_page((800, 480), _payload(alerts=alerts, events=_events(6)), settings, NOW)

    for image in (roomy, tight):
        left, top, width, height = image.info["event_map_box"]
        card = image.info["column_cards"]["events"]["box"]
        content_top, content_bottom = card[1] + 32, card[3] - 4
        assert content_top <= top and top + height <= content_bottom
        assert abs((top - content_top) - (content_bottom - top - height)) <= 1
    assert roomy.info["event_map_box"][2] == render.EVENT_MAP_MAX_WIDTH
    assert tight.info["event_map_box"][3] < roomy.info["event_map_box"][3]


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
