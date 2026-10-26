import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageFilter

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


def _bay_map(quakes=(), alerts=(), **options):
    from plugins.bay_alerts import map_view
    from utils.page_canvas import PageCanvas

    palette = render._palette({"_inkypi_theme": {"mode": "day"}})
    canvas = PageCanvas(palette)
    return map_view.render_bay_map((440, 408), palette, canvas.font, quakes=list(quakes), alerts=list(alerts),
                                   home=list(HOME), home_label="家", now=NOW, **options)


def test_recent_quakes_beyond_the_map_get_an_edge_arrow_and_old_unlisted_ones_do_not():
    quakes = sources.normalize_quakes({"features": [
        _quake("gilroy", 3.1, 3, lat=36.95, lon=-121.55),
        _quake("old", 3.4, 120, lat=36.80, lon=-120.90),
        _quake("near", 2.2, 2),
    ]}, HOME, NOW)["items"]

    image = _bay_map(quakes)

    markers = {marker["id"]: marker for marker in image.info["map_markers"]}
    assert markers["near"]["visible"] and not markers["near"]["edge"]
    assert not markers["gilroy"]["visible"] and markers["gilroy"]["edge"]
    assert not markers["old"]["visible"] and not markers["old"]["edge"]
    # A quake the card lists, or the week's strongest, keeps its arrow however old it is.
    listed = _bay_map(quakes, listed=["old"])
    assert {marker["id"]: marker for marker in listed.info["map_markers"]}["old"]["edge"]


def test_quakes_beyond_the_map_in_one_direction_share_an_arrow():
    quakes = sources.normalize_quakes({"features": [
        _quake("a", 2.4, 3, lat=36.95, lon=-121.55), _quake("b", 2.0, 4, lat=36.94, lon=-121.56),
        _quake("north", 2.1, 5, lat=38.45, lon=-122.60),
    ]}, HOME, NOW)["items"]

    from plugins.bay_alerts import map_view
    manifest = map_view.read_manifest(map_view.BAY_MAP)
    groups = map_view._edge_groups(quakes, manifest, (440, 408), lambda quake: (0, -quake["mag"]))

    assert [[quake["id"] for quake in group["quakes"]] for group in groups] == [["a", "b"], ["north"]]
    assert groups[0]["side"] == "bottom" and groups[1]["side"] == "top"


def test_alert_zone_codes_are_kept_for_the_map():
    data = sources.normalize_alerts({"features": [
        {**_alert("Coastal Flood Advisory", ["006075"]),
         "properties": {**_alert("Coastal Flood Advisory", ["006075"])["properties"],
                        "geocode": {"SAME": ["006075"], "UGC": ["CAZ006", "CAZ508", "bad"]}}},
        {**_alert("Wind Advisory", ["006001"]),
         "properties": {**_alert("Wind Advisory", ["006001"])["properties"],
                        "affectedZones": ["https://api.weather.gov/zones/forecast/CAZ510"]}},
    ]}, sources.counties_setting(), NOW)

    assert {item["event"]: item["zones"] for item in data["items"]} == {
        "Coastal Flood Advisory": ["CAZ006", "CAZ508"], "Wind Advisory": ["CAZ510"]}


def _zoned_alert(event, zones, severity="Minor", counties=("San Francisco",)):
    return {"event": event, "name": sources.event_name(event), "kind": sources.hazard_kind(event),
            "severity": severity, "counties": list(counties), "zones": list(zones), "polygon": None}


def test_shore_hazards_mark_only_the_shore_and_others_their_whole_zone():
    from plugins.bay_alerts import map_view

    manifest = map_view.read_manifest(map_view.BAY_MAP)
    water, _faults, _shade = map_view._layers(map_view.BAY_MAP, manifest)
    image = Image.new("RGB", (440, 408))
    palette = render._palette({})
    alerts = [{**_zoned_alert("Coastal Flood Advisory", ["CAZ508"]), "tone": "minor"},
              {**_zoned_alert("Wind Advisory", ["CAZ508"]), "tone": "moderate"}]

    (coastal, coastal_mask), (wind, wind_mask) = map_view._draw_alert_areas(
        image, palette, alerts, water, manifest, (440, 408))

    assert map_view.is_coastal(coastal) and not map_view.is_coastal(wind)
    def area(mask):
        return mask.width * mask.height - mask.histogram()[0]

    assert 0 < area(coastal_mask) < area(wind_mask) / 2
    # Every marked shore pixel is on land within the band's reach of the water.
    near_water = water.filter(ImageFilter.MaxFilter(map_view.SHORE_BAND * 2 + 1))
    outside = ImageChops.lighter(ImageChops.invert(near_water), water)
    assert ImageChops.multiply(coastal_mask, outside).getbbox() is None


def test_alert_tags_number_the_card_rows_and_sit_on_the_map():
    alerts = sources.normalize_alerts({"features": [
        {**_alert("Coastal Flood Advisory", ["006075"], severity="Minor"),
         "properties": {**_alert("Coastal Flood Advisory", ["006075"], severity="Minor")["properties"],
                        "geocode": {"SAME": ["006075"], "UGC": ["CAZ508"]}}},
        # No zone named: the alert's whole county is its area.
        _alert("Heat Advisory", ["006085"]),
    ]}, sources.counties_setting(), NOW)["items"]

    image = render_page((800, 480), _payload(alerts=alerts), {"_inkypi_theme": {"mode": "day"}}, NOW)

    tags = image.info["alert_tags"]
    assert sorted(tag["number"] for tag in tags) == [1, 2]
    for tag in tags:
        left, top, right, bottom = tag["box"]
        assert 0 <= left and right <= 440 and 0 <= top and bottom <= 388
    boxes = [tag["box"] for tag in tags] + [image.info["map_summary"], image.info["map_callout"]]
    from plugins.bay_alerts.map_view import _overlaps
    assert not any(_overlaps(first, second) for index, first in enumerate(boxes) for second in boxes[index + 1:])


def test_nearest_fault_names_the_known_fault_and_gives_up_far_from_any():
    from plugins.bay_alerts.map_view import nearest_fault

    name, km = nearest_fault(37.665, -122.08)
    assert name == "海沃德断层" and km < 1.5
    assert nearest_fault(37.775, -121.983)[0] == "卡拉韦拉斯断层"
    assert nearest_fault(37.0, -123.4) is None
    assert nearest_fault(None, -122.0) is None


def test_the_callout_explains_the_weeks_strongest_quake():
    quakes = sources.normalize_quakes({"features": [
        _quake("small", 2.1, 1), _quake("big", 4.1, 5, lat=37.665, lon=-122.08, place="2 km N of Hayward, CA"),
    ]}, HOME, NOW)["items"]

    focus = render.focus_quake(quakes, NOW)

    assert focus["id"] == "big" and focus["tone"] == "alert" and focus["title"] == "7 天最大"
    assert focus["lines"] == ["5 小时前 · 距家 15 km", "海沃德断层旁"]
    assert render.focus_quake([], NOW) is None
    image = render_page((800, 480), _payload(quakes=quakes), {"_inkypi_theme": {"mode": "day"}}, NOW)
    markers = {marker["id"]: marker for marker in image.info["map_markers"]}
    assert markers["big"]["visible"] and markers["big"]["chip"]
    left, top, right, bottom = image.info["map_callout"]
    assert 0 <= left and right <= 440 and 0 <= top and bottom <= 388


def test_near_home_box_counts_quakes_inside_the_outer_ring():
    quakes = sources.normalize_quakes({"features": [
        _quake("a", 2.4, 2), _quake("b", 1.6, 30), _quake("far", 3.0, 3, lat=36.95, lon=-121.55),
    ]}, HOME, NOW)["items"]

    assert render.near_home_summary(quakes, NOW) == ["50 km 内", "24 小时 1 次", "7 天 2 次", "最大 M2.4"]
    assert render.near_home_summary([], NOW) == ["50 km 内", "24 小时 0 次", "7 天 0 次"]
    image = _bay_map(quakes, summary=render.near_home_summary(quakes, NOW))
    assert image.info["home_rings"] == [10, 25, 50]
    assert image.info["map_summary"] is not None


def test_overlay_assets_are_bounded_and_cover_the_bay():
    from plugins.bay_alerts import map_view

    zones = map_view.read_overlay(map_view.ZONES)["zones"]
    faults = map_view.read_overlay(map_view.FAULTS)["faults"]
    for name in (map_view.ZONES, map_view.FAULTS):
        assert (map_view.ASSETS / f"{name}.json").stat().st_size <= map_view._OVERLAY_BYTES
    assert {"CAZ006", "CAZ508", "CAZ509", "CAZ510", "CAZ515"} <= set(zones)
    assert set(map_view.COUNTY_ZONES.values()) <= set(zones)
    assert {"圣安德烈亚斯断层", "海沃德断层", "卡拉韦拉斯断层"} <= {fault["name"] for fault in faults}


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
    monkeypatch.setattr(sources, "fetch_fires", lambda *_args: {"items": []})
    settings = {"_inkypi_theme": {"mode": "day"}}

    live = plugin.generate_image(settings, Device())
    for name in ("fetch_quakes", "fetch_alerts", "fetch_events", "fetch_fires"):
        monkeypatch.setattr(sources, name, lambda *_args: pytest.fail("cache-only redraw fetched"))
    cached = plugin.generate_image({**settings, "_theme_render_only": True}, Device())

    assert read_source_provenance(live) is SourceProvenance.LIVE
    assert read_source_provenance(cached) is SourceProvenance.FRESH_CACHE


CALFIRE_ROWS = [
    {"Name": "Cooley Fire", "Final": False, "Started": "2026-10-17T00:05:39Z", "County": "Mendocino",
     "AcresBurned": 1365.0, "PercentContained": 90.0, "Longitude": -123.205859, "Latitude": 38.833351,
     "UniqueId": "0c342fc2", "IsActive": True},
    {"Name": "Mission Fire", "Final": False, "Started": "2026-10-21T10:00:00Z", "County": "Alameda",
     "AcresBurned": 40.0, "PercentContained": 10.0, "Longitude": -121.93, "Latitude": 37.53,
     "UniqueId": "m1", "IsActive": True},
    {"Name": "Old Fire", "Final": True, "Started": "2026-09-01T00:00:00Z", "Longitude": -121.9, "Latitude": 37.5,
     "IsActive": False},
    {"Name": "Reno Fire", "Started": "2026-10-20T00:00:00Z", "Longitude": -110.0, "Latitude": 39.5, "IsActive": True},
]


def test_calfire_incidents_become_events_with_acres_and_containment():
    fires = sources.normalize_fires(CALFIRE_ROWS, HOME)["items"]

    assert [fire["title"] for fire in fires] == ["Mission 山火", "Cooley 山火"]
    mission = fires[0]
    assert (mission["kind"], mission["magnitude"], mission["unit"], mission["contained"]) == ("fire", 40.0, "acres", 10)
    assert mission["distance_km"] < 10 and sources.valid_fires({"items": fires})
    with pytest.raises(ValueError):
        sources.normalize_fires({"error": "x"}, HOME)


def test_firms_hotspots_drop_low_confidence_and_hide_the_key_on_failure(monkeypatch):
    table = ("latitude,longitude,bright_ti4,acq_date,acq_time,confidence,frp\n"
             "37.61,-121.85,330.1,2026-10-21,0942,n,4.2\n"
             "37.70,-122.10,301.0,2026-10-21,0942,l,0.8\n")
    spots = sources.normalize_hotspots(table, NOW)["items"]
    assert spots == [{"lat": 37.61, "lon": -121.85, "time": "2026-10-21T09:42:00+00:00", "frp": 4.2}]

    class Failing:
        def request_text(self, method, url, **_kwargs):
            raise OSError(f"could not reach {url}")

    monkeypatch.setattr(sources, "get_http_client", lambda: Failing())

    class Context:
        def raise_if_cancelled(self):
            pass

    with pytest.raises(RuntimeError) as failure:
        sources.fetch_hotspots("secret-map-key", NOW, Context())
    assert "secret-map-key" not in str(failure.value) and failure.value.__cause__ is None


def test_calfire_replaces_eonets_copy_of_the_same_fire_and_marks_the_map():
    eonet = [{"id": "EONET_1", "title": "Mission", "kind": "fire", "date": "2026-10-21T11:00:00+00:00",
              "lat": 37.54, "lon": -121.94, "magnitude": None, "unit": ""},
             {"id": "EONET_2", "title": "Storm", "kind": "storm", "date": "2026-10-20T11:00:00+00:00",
              "lat": 36.5, "lon": -121.5, "magnitude": None, "unit": ""}]
    payload = _payload(events=eonet, quakes=[])
    payload["fires"] = {"state": "live", "data": sources.normalize_fires(CALFIRE_ROWS, HOME)}
    payload["hotspots"] = {"state": "live", "data": {"items": [
        {"lat": 37.61, "lon": -121.85, "time": "2026-10-21T09:42:00+00:00", "frp": 4.2},
        {"lat": 40.0, "lon": -120.0, "time": "2026-10-21T09:42:00+00:00", "frp": 1.0}]}}

    merged = render.with_fires(payload)["events"]["data"]["items"]
    image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW)

    assert [event["id"] for event in merged] == ["calfire-m1", "EONET_2", "calfire-0c342fc2"]
    # Only the incident and the hotspot inside the Bay map are drawn on it.
    assert image.info["fire_marks"] == {"hotspots": 1, "incidents": 1}
    # The nearby fire counts towards the header's attention pill.
    assert attention_items(render.with_fires(payload), NOW) == 1
    assert render.with_fires(_payload(events=eonet)) == _payload(events=eonet)


def test_hotspots_load_only_with_a_firms_key(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(sources, "fetch_quakes", lambda *_args: _payload()["quakes"]["data"])
    monkeypatch.setattr(sources, "fetch_alerts", lambda *_args: {"items": [], "counties": ["006001"]})
    monkeypatch.setattr(sources, "fetch_events", lambda *_args: {"items": []})
    monkeypatch.setattr(sources, "fetch_fires", lambda *_args: {"items": []})
    keys = []
    monkeypatch.setattr(sources, "fetch_hotspots", lambda key, _now, _ctx: keys.append(key) or {"items": []})

    class KeyedDevice(Device):
        def load_env_key(self, key):
            return "firms-test-key" if key == "FIRMS_MAP_KEY" else None

    plugin = BayAlerts({"id": "bay_alerts"})
    plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, Device())
    assert keys == []
    plugin.generate_image({"_inkypi_theme": {"mode": "day"}, "forceRefresh": True}, KeyedDevice())
    assert keys == ["firms-test-key"]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_each_card_names_its_sources_by_their_logos(mode):
    from utils.page_canvas import PageCanvas

    canvas = PageCanvas(render._palette({"_inkypi_theme": {"mode": mode}}))
    drawn = []
    original = canvas.text

    def record(xy, text, *args, **kwargs):
        drawn.append(text)
        return original(xy, text, *args, **kwargs)

    canvas.text = record
    render._card(canvas, (470, 60, 788, 160), "alert", "alert", "气象预警", "2 条", {"state": "live"}, ("NWS", ("nws",)))
    render._card(canvas, (470, 170, 788, 270), "moderate", "fire", "加州自然事件", "共 9 起", {"state": "live"},
                 ("CAL FIRE · EONET", ("calfire", "nasa")))
    render._card(canvas, (470, 280, 788, 380), "home", "quake", "地震", "半径 150 km", {"state": "live"},
                 ("USGS", ("missing",)))

    # A logo stands for the name; a source without its logo file keeps its name in the detail.
    assert {"2 条", "共 9 起", "USGS · 半径 150 km"} <= set(drawn)
    assert not any("NWS" in text or "CAL FIRE" in text for text in drawn)
    header = [canvas.image.getpixel((x, y)) for x in range(640, 760) for y in range(68, 86)]
    assert any(abs(red - blue) > 60 for red, _green, blue in header)  # NWS' blue and red
