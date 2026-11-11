"""Bay Alerts read at a glance: alerts of one name share a heading, each alert's hours sit on a
small track, the quake card shows the week as a time axis, places are never cut mid-name, the map
keeps only the 500 m contours and a headline quake beyond the map is one chip at its edge."""

import sys
from datetime import timedelta
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.bay_alerts import map_view, render, sources  # noqa: E402
from plugins.bay_alerts.render import plan_column, render_page  # noqa: E402
from utils.page_canvas import PageCanvas  # noqa: E402

from tests.test_bay_alerts import HOME, NOW, _alert, _bay_map, _payload, _quake  # noqa: E402


def _alerts(*features):
    return sources.normalize_alerts({"features": list(features)}, sources.counties_setting(), NOW)["items"]


def _record_text(monkeypatch):
    drawn = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda self, xy, text, *args, **kwargs: drawn.append(str(text)) or original(
        self, xy, text, *args, **kwargs))
    return drawn


def test_alerts_of_one_name_share_a_heading_and_the_card_shrinks_by_the_repeat(monkeypatch):
    flood = {**_alert("Coastal Flood Advisory", ["006041"], severity="Minor"),
             "properties": {**_alert("Coastal Flood Advisory", ["006041"], severity="Minor")["properties"], "id": "one"}}
    later = _alert("Coastal Flood Advisory", ["006075"], severity="Minor", ends_in_hours=80)
    alerts = _alerts(flood, later, _alert("Wind Advisory", ["006001"], severity="Minor"))

    heights = render.alert_row_heights(alerts)

    assert [alert["name"] for alert in alerts] == ["沿海洪水提示", "沿海洪水提示", alerts[2]["name"]]
    assert heights == [render.ALERT_ROW, render.ALERT_FOLLOW_ROW, render.ALERT_ROW]
    # Same name but another severity is its own heading.
    assert render.alert_row_heights([alerts[0], {**alerts[1], "severity": "Severe"}]) == [render.ALERT_ROW] * 2
    counts = {"alerts": 2, "quakes": 27, "events": 8}
    _rows, plain = plan_column(counts)
    rows, merged = plan_column({**counts, "alert_heights": heights[:2]})
    assert rows["alerts"] == 2
    assert plain["alerts"] - merged["alerts"] == render.ALERT_ROW - render.ALERT_FOLLOW_ROW
    assert sum(merged.values()) == render.COLUMN_SPACE
    drawn = _record_text(monkeypatch)
    image = render_page((800, 480), _payload(alerts=alerts[:2]), {"_inkypi_theme": {"mode": "day"}}, NOW)
    assert drawn.count("沿海洪水提示") == 1
    assert image.info["column_cards"]["alerts"]["rows"] == 2
    assert sorted(tag["number"] for tag in image.info["alert_tags"]) == [1, 2]


def test_an_alerts_hours_sit_on_a_three_day_track():
    def alert(onset_hours, end_hours):
        return {"onset": (NOW + timedelta(hours=onset_hours)).isoformat() if onset_hours is not None else None,
                "ends": (NOW + timedelta(hours=end_hours)).isoformat() if end_hours is not None else None}

    assert render.alert_span(alert(-5, 18), NOW) == (0.0, 0.25, False)
    # One that has not started yet begins part-way along; one past the track runs off its end.
    assert render.alert_span(alert(36, 54), NOW) == (0.5, 0.75, False)
    assert render.alert_span(alert(None, 100), NOW) == (0.0, 1.0, True)
    assert render.alert_span(alert(0, None), NOW) is None
    assert render.alert_span(alert(80, 90), NOW) is None
    canvas = PageCanvas(render._palette({"_inkypi_theme": {"mode": "day"}}))
    render._alert_span(canvas, (100, 100), (0.0, 0.5, False), "minor", NOW)
    blue = canvas.color("minor")
    assert canvas.image.getpixel((105, 100)) == blue and canvas.image.getpixel((100 + render.ALERT_SPAN_WIDTH - 3, 100)) != blue


def test_an_alert_that_starts_in_the_tracks_last_pixels_and_runs_on_still_draws():
    # A watch issued 66 h ahead for a storm lasting past the track: it starts 36.7 px along the
    # 40 px track, closer to the end than the pointed tip is long. The bar was then drawn right
    # edge first, which failed the whole page until the start came within 63 h.
    canvas = PageCanvas(render._palette({"_inkypi_theme": {"mode": "day"}}))
    watch = {"onset": (NOW + timedelta(hours=66)).isoformat(), "ends": (NOW + timedelta(hours=120)).isoformat()}

    render._alert_span(canvas, (100, 100), render.alert_span(watch, NOW), "minor", NOW)

    blue = canvas.color("minor")
    assert canvas.image.getpixel((138, 100)) == blue
    # Nothing of it is drawn before its own start.
    assert blue not in {canvas.image.getpixel((x, y)) for x in range(100, 136) for y in range(94, 107)}


def test_the_end_day_is_named_by_weekday_within_the_week():
    assert render.until(NOW + timedelta(hours=3), NOW) == "至 今天 16:00"
    assert render.until(NOW + timedelta(days=1), NOW) == "至 明天 13:00"
    assert render.until(NOW + timedelta(days=3), NOW) == "至 周六 13:00"
    assert render.until(NOW + timedelta(days=9), NOW) == "至 10/30 周五 13:00"


def test_counties_are_listed_whole_or_counted_never_cut():
    canvas = PageCanvas(render._palette({"_inkypi_theme": {"mode": "day"}}))
    counties = ["Marin", "Sonoma", "San Mateo", "San Francisco"]

    assert render.county_text(canvas, counties, 400) == "Marin、Sonoma、San Mateo、San Francisco"
    assert render.county_text(canvas, counties, 130) == "Marin、Sonoma 等4县"
    assert render.county_text(canvas, counties, 80) == "Marin 等4县"
    assert render.county_text(canvas, counties, 20) == "4 县"


def test_a_place_is_its_town_then_an_arrow_and_the_kilometres(monkeypatch):
    assert render.place_parts("San Ramon 西北 5 公里") == ("San Ramon", 315.0, "5")
    assert render.place_parts("Pleasant Hill 南偏西 3.5 公里") == ("Pleasant Hill", 202.5, "3.5")
    assert render.place_parts("The Geysers") is None and render.place_parts(None) is None
    drawn = _record_text(monkeypatch)
    arrows = []
    original = render._bearing_arrow
    monkeypatch.setattr(render, "_bearing_arrow", lambda draw, centre, bearing, *args: arrows.append(bearing) or original(
        draw, centre, bearing, *args))
    quakes = sources.normalize_quakes({"features": [_quake("a", 2.4, 2), _quake("b", 2.1, 40)]}, HOME, NOW)["items"]

    render_page((800, 480), _payload(quakes=quakes), {"_inkypi_theme": {"mode": "day"}}, NOW)

    # The town is written in full, the offset after it as an arrow and a figure.
    assert drawn.count("San Ramon") == 2 and drawn.count("5 km") == 2 and arrows == [315.0, 315.0]
    assert not any("公里" in text and "San Ramon" in text for text in drawn[drawn.index("San Ramon"):])


def test_the_quake_card_shows_the_week_as_a_time_axis(monkeypatch):
    assert render.quake_stick(1.0) == 5 and render.quake_stick(4.0) == render.QUAKE_WEEK_STICK
    assert render.quake_stick(0.2) == 5 and render.quake_stick(6.5) == render.QUAKE_WEEK_STICK
    assert 5 < render.quake_stick(2.5) < render.QUAKE_WEEK_STICK
    quakes = sources.normalize_quakes({"features": [
        _quake("today", 2.2, 2), _quake("big", 3.9, 50), _quake("old", 2.0, 150)]}, HOME, NOW)["items"]
    drawn = _record_text(monkeypatch)

    image = render_page((800, 480), _payload(quakes=quakes), {"_inkypi_theme": {"mode": "day"}}, NOW)

    week = image.info["quake_week"]
    left, top, right, base = week["box"]
    assert week["sticks"] == 3 and render.COLUMN < left < right <= render.RIGHT
    palette = render._palette({"_inkypi_theme": {"mode": "day"}})
    seconds = render.QUAKE_WEEK_DAYS * 86400

    def stick(hours):
        return image.getpixel((round(right - hours * 3600 / seconds * (right - left)), base - 2))

    # Each quake stands where it happened on the axis, in its age's colour (older ones in ink).
    assert stick(2) == palette["quake_day"] and stick(50) == palette["quake_recent"] and stick(150) == palette["ink"]
    assert "3 次" in drawn and "24 小时 1 次" in drawn and "M3.9" in drawn
    assert "周二" in drawn and "周一" in drawn and "周四" in drawn
    # The list starts under the axis and its day names.
    card_top = image.info["column_cards"]["quakes"]["box"][1]
    assert base + 14 < card_top + render.QUAKE_LIST_TOP - 8


def test_the_map_keeps_only_the_index_contours(monkeypatch):
    seen = []
    original = map_view._draw_contours
    monkeypatch.setattr(map_view, "_draw_contours", lambda image, palette, size, **options: seen.append(options) or original(
        image, palette, size, **options))

    _bay_map()

    assert seen == [{"minor": False}]
    colours = {key: map_view.ImageColor.getrgb(value) for key, value in render.DAY.items() if isinstance(value, str)}
    image = Image.new("RGB", (440, 408), colours["paper"])
    map_view._draw_contours(image, colours, image.size, minor=False)
    pixels = set(image.get_flattened_data())
    assert colours["contour_index"] in pixels and colours["contour"] not in pixels


def test_a_headline_quake_beyond_the_map_is_one_chip_at_its_edge(monkeypatch):
    quakes = sources.normalize_quakes({"features": [
        _quake("far", 3.1, 90, lat=36.85, lon=-121.40, place="6 km SSW of Tres Pinos, CA"),
        _quake("near", 2.2, 2)]}, HOME, NOW)["items"]
    lines, labels = [], []
    monkeypatch.setattr(map_view, "_dashed", lambda *args, **options: lines.append(args))
    monkeypatch.setattr(map_view, "_draw_distance", lambda *args: labels.append(args))
    focus = render.focus_quake(quakes, NOW)

    image = _bay_map(quakes, focus=focus, listed=["far", "near"])

    markers = {marker["id"]: marker for marker in image.info["map_markers"]}
    assert focus["id"] == "far" and not markers["far"]["visible"] and markers["far"]["edge"]
    # No callout box over the map and no line across it: the card lists the quake.
    assert image.info["map_callout"] is None and lines == [] and labels == []
    # On the map the headline quake still gets its callout and its line from home.
    inside = sources.normalize_quakes({"features": [
        _quake("big", 4.1, 5, lat=37.665, lon=-122.08, place="2 km N of Hayward, CA")]}, HOME, NOW)["items"]
    shown = _bay_map(inside, focus=render.focus_quake(inside, NOW))
    assert shown.info["map_callout"] is not None and len(lines) == 1
