"""Bay Alerts' power outages (Cal OES by county, PG&E's points on the map) and its sea state (the
NWS surf zone forecast's breaker heights, a high-surf headline the alert rows do not list, and
the Bay marine forecast's advisory): optional extras that live on the map and never move the
column or decide the page's health."""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import SourceProvenance  # noqa: E402
from plugins.bay_alerts import map_view, render, sources  # noqa: E402
from plugins.bay_alerts.bay_alerts import BayAlerts  # noqa: E402
from plugins.bay_alerts.render import attention_items, render_page  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402

from tests.test_bay_alerts import HOME, _alert, _payload  # noqa: E402
from tests.test_bay_alerts import NOW as OCTOBER  # noqa: E402

NOW = datetime(2026, 11, 11, 13, 0, tzinfo=sources.PACIFIC)
DAY = {"_inkypi_theme": {"mode": "day"}}

# Cal OES's statistics answer for the Bay counties on 2026-11-11 (its "fields" block left out),
# with one row from outside them.
OUTAGE_STATS = {"objectIdFieldName": "OBJECTID", "features": [{"attributes": dict(zip(
    ("customers", "outages", "County", "UtilityCompany", "OutageType"), row))} for row in (
    (157, 4, "SAN FRANCISCO", "PGE", "Not Planned"), (116, 5, "SANTA CLARA", "PGE", "Planned"),
    (94, 3, "MARIN", "PGE", "Not Planned"), (58, 7, "ALAMEDA", "PGE", "Not Planned"),
    (34, 2, "NAPA", "PGE", "Planned"), (28, 1, "CONTRA COSTA", "PGE", "Planned"),
    (22, 2, "SONOMA", "PGE", "Planned"), (21, 3, "SONOMA", "PGE", "Not Planned"),
    (17, 6, "SANTA CLARA", "PGE", "Not Planned"), (17, 1, "ALAMEDA", "PGE", "Planned"),
    (11, 6, "CONTRA COSTA", "PGE", "Not Planned"), (6, 2, "NAPA", "PGE", "Not Planned"),
    (1, 1, "SAN MATEO", "PGE", "Not Planned"), (1, 1, "SOLANO", "PGE", "Not Planned"),
    (2302, 7, "LOS ANGELES", "SCE", "Not Planned"))]}

# PG&E's outage points inside the map on the same day, largest first.
OUTAGE_POINTS = {"geometryType": "esriGeometryPoint", "exceededTransferLimit": True, "features": [
    {"attributes": {"EST_CUSTOMERS": 147, "OUTAGE_CAUSE": "EMERG REPAIRS", "CREW_CURRENT_STATUS": "Crew On Site",
                    "CURRENT_ETOR_TEXT": "2026-11-11T22:15:00Z", "OUTAGE_START_TEXT": "2026-11-11T16:06:10Z"},
     "geometry": {"x": -122.41650128014624, "y": 37.795133019786505}},
    {"attributes": {"EST_CUSTOMERS": 80, "OUTAGE_CAUSE": "THRD PARTY", "CREW_CURRENT_STATUS": "Awaiting Crew",
                    "CURRENT_ETOR_TEXT": "2026-11-12T01:30:00Z", "OUTAGE_START_TEXT": "2026-11-11T15:57:44Z"},
     "geometry": {"x": -122.53426443470775, "y": 37.97810060305019}},
    {"attributes": {"EST_CUSTOMERS": 28, "OUTAGE_CAUSE": "PLNND SHUTDOWN", "CREW_CURRENT_STATUS": "Crew On Site",
                    "CURRENT_ETOR_TEXT": "2026-11-12T00:00:00Z", "OUTAGE_START_TEXT": "2026-11-11T15:47:40Z"},
     "geometry": {"x": -122.33419962987175, "y": 37.932267107612915}},
    {"attributes": {"EST_CUSTOMERS": 17, "OUTAGE_CAUSE": "PLNND SHUTDOWN", "CREW_CURRENT_STATUS": "Crew On Site",
                    "CURRENT_ETOR_TEXT": "2026-11-12T00:00:00Z", "OUTAGE_START_TEXT": "2026-11-11T17:03:00Z"},
     "geometry": {"x": -122.04831598296306, "y": 37.59413914051434}},
    {"attributes": {"EST_CUSTOMERS": 16, "OUTAGE_CAUSE": None, "CREW_CURRENT_STATUS": "Awaiting T-Man",
                    "CURRENT_ETOR_TEXT": None, "OUTAGE_START_TEXT": "2026-11-11T19:48:21Z"},
     "geometry": {"x": -122.0634414779505, "y": 37.627268564640914}},
    {"attributes": {"EST_CUSTOMERS": 4, "OUTAGE_CAUSE": None, "CREW_CURRENT_STATUS": "Awaiting T-Man",
                    "CURRENT_ETOR_TEXT": None, "OUTAGE_START_TEXT": "2026-11-11T19:40:54Z"},
     "geometry": {"x": -122.17872492210346, "y": 37.73583721242537}},
    {"attributes": {"EST_CUSTOMERS": 40, "OUTAGE_CAUSE": None}, "geometry": {"x": "east", "y": None}},
]}

# The surf zone forecast of 2026-11-11 12:06 PST: its first two zones (the tide lines left out).
SURF_TEXT = """FZUS56 KMTR 112006
SRFMTR

NORTHERN AND CENTRAL CALIFORNIA SURF ZONE FORECAST
NATIONAL WEATHER SERVICE SAN FRANCISCO BAY AREA
1206 PM PST Wed Nov 11 2026

...Please check with your local authorities on potential beach closures...

CAZ505-121000-
SONOMA AND MARIN COUNTY COASTAL AREAS-
1206 PM PST Wed Nov 11 2026

...BEACH HAZARDS STATEMENT THROUGH 6 PM FRIDAY...
...HIGH SURF WARNING FROM 6 PM FRIDAY THROUGH 4 AM SUNDAY...

.REST OF TODAY...
Surf Height...................West Facing Beaches - 10 to 14 feet.
                              South Facing Beaches - 3 to 6 feet.
Thunderstorm Potential........None expected.
Water Temperature.............Missing.
Weather.......................Partly cloudy.
High Temperature..............61-76.
Wind..........................SW at 3 MPH.

.THURSDAY...
Surf Height...................West Facing Beaches - 11 to 15 feet.
                              South Facing Beaches - 3 to 6 feet.
Thunderstorm Potential........None expected.
Weather.......................Mostly cloudy.


$$


CAZ509-121000-
SAN FRANCISCO AND SAN MATEO COUNTY COASTAL AREAS-
1206 PM PST Wed Nov 11 2026

...BEACH HAZARDS STATEMENT THROUGH 6 PM FRIDAY...
...HIGH SURF WARNING FROM 6 PM FRIDAY THROUGH 4 AM SUNDAY...

.REST OF TODAY...
Surf Height...................West Facing Beaches - 9 to 13 feet.
                              South Facing Beaches - 3 to 5 feet.
Thunderstorm Potential........None expected.
Water Temperature.............Around 66 degrees.
Weather.......................Partly cloudy.
High Temperature..............62-76.
Wind..........................NW at 5 MPH becoming NW at 2 MPH.
Tides.........................High 4.6 feet (MLLW) 01:27 AM PST.
                              Low 3.2 feet (MLLW) 05:41 AM PST.

.THURSDAY...
Surf Height...................West Facing Beaches - 10 to 14 feet.
                              South Facing Beaches - 3 to 6 feet.
Thunderstorm Potential........None expected.
Weather.......................Mostly cloudy.
High Temperature..............62-72.
Wind..........................SE at 10 MPH becoming SE at 14 MPH.


$$
"""

# The coastal waters forecast for the Bay north of the Bay Bridge, 2026-11-11 08:36 PST.
MARINE_TEXT = """Expires:202611120530;;645647
FZUS56 KMTR 111636
CWFMTR

Coastal Waters Forecast for California
National Weather Service San Francisco Bay Area
836 AM PST Wed Nov 11 2026

PZZ530-120530-
San Pablo Bay, Suisun Bay, the West Delta and  the San Francisco
Bay north of the Bay Bridge-
836 AM PST Wed Nov 11 2026

...SMALL CRAFT ADVISORY IN EFFECT FROM THURSDAY EVENING THROUGH
FRIDAY MORNING...

.TODAY...NE wind around 5 kt. Patchy dense fog early this
morning.
.TONIGHT...Light and variable winds, becoming S around 5 kt after
midnight.
.THU...SE wind 5 to 10 kt, becoming S 10 to 15 kt in the
afternoon.

$$
"""


@pytest.fixture(autouse=True)
def offline_feeds(monkeypatch):
    """No test here reaches a provider: every feed of the page reads a sample or is empty."""
    monkeypatch.setattr(sources, "fetch_quakes", lambda *_args: _payload()["quakes"]["data"])
    monkeypatch.setattr(sources, "fetch_alerts", lambda *_args: {"items": [], "counties": ["006001"]})
    monkeypatch.setattr(sources, "fetch_events", lambda *_args: {"items": []})
    monkeypatch.setattr(sources, "fetch_fires", lambda *_args: {"items": []})
    for name in ("fetch_key_messages", "fetch_spare_the_air"):
        monkeypatch.setattr(sources, name, lambda _ctx: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(sources, "fetch_flu", lambda _now, _ctx: {})
    monkeypatch.setattr(sources, "fetch_outages",
                        lambda counties, _ctx: sources.normalize_outages(OUTAGE_STATS, counties))
    monkeypatch.setattr(sources, "fetch_outage_points",
                        lambda home, _ctx: sources.normalize_outage_points(OUTAGE_POINTS, home))
    monkeypatch.setattr(sources, "fetch_surf", lambda _ctx: sources.parse_surf(SURF_TEXT))
    monkeypatch.setattr(sources, "fetch_marine", lambda _ctx: sources.parse_marine(MARINE_TEXT))


def _live(data):
    return {"state": "live", "data": data}


def _sea_and_power(stats=OUTAGE_STATS, points=OUTAGE_POINTS):
    counties = sources.counties_setting()
    return {"outages": _live(sources.normalize_outages(stats, counties)),
            "outage_points": _live(sources.normalize_outage_points(points, HOME)),
            "surf": _live(sources.parse_surf(SURF_TEXT)), "marine": _live(sources.parse_marine(MARINE_TEXT))}


def _storm_stats(scale=60):
    """The same day's rows with every unplanned outage ``scale`` times its size."""
    return {"features": [{"attributes": {**row["attributes"], "customers": row["attributes"]["customers"] * (
        scale if row["attributes"]["OutageType"] != "Planned" else 1)}} for row in OUTAGE_STATS["features"]]}


def _record_text(monkeypatch):
    drawn = []
    original = map_view.ImageDraw.ImageDraw.text
    monkeypatch.setattr(map_view.ImageDraw.ImageDraw, "text",
                        lambda self, xy, text, *args, **kwargs: drawn.append(str(text)) or original(
                            self, xy, text, *args, **kwargs))
    return drawn


# --- power outages ------------------------------------------------------------------------------


def test_outages_add_up_the_chosen_counties_with_planned_work_apart():
    data = sources.normalize_outages(OUTAGE_STATS, sources.counties_setting())

    assert (data["customers"], data["outages"], data["planned"]) == (583, 44, 217)
    assert [(row["county"], row["customers"]) for row in data["counties"]] == [
        ("San Francisco", 157), ("Santa Clara", 133), ("Marin", 94), ("Alameda", 75), ("Sonoma", 43), ("Napa", 40),
        ("Contra Costa", 39), ("San Mateo", 1), ("Solano", 1)]
    santa_clara = data["counties"][1]
    assert (santa_clara["outages"], santa_clara["planned"], santa_clara["utilities"]) == (11, 116, ["PG&E"])
    assert sources.valid_outages(data)
    # Only the counties the page covers count.
    only = sources.normalize_outages(OUTAGE_STATS, ("006075",))
    assert (only["customers"], [row["county"] for row in only["counties"]]) == (157, ["San Francisco"])
    # ArcGIS reports a failed query in a 200 answer.
    for broken in ({"error": {"code": 400, "message": "Cannot perform query."}}, [], {"features": "none"}):
        with pytest.raises(ValueError):
            sources.normalize_outages(broken, sources.counties_setting())
    assert not sources.valid_outages({}) and not sources.valid_outages({"customers": -1, "outages": 0, "counties": []})


def test_the_outage_queries_are_small_and_name_only_the_page_s_counties_and_map(monkeypatch):
    calls = []

    class Client:
        def request_json(self, method, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"data": OUTAGE_STATS if "Power_Outages" in url else OUTAGE_POINTS})()

    monkeypatch.undo()
    monkeypatch.setattr(sources, "get_http_client", lambda: Client())
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)

    stats = sources.fetch_outages(("006075", "006001"), context)
    points = sources.fetch_outage_points(HOME, context)

    (stats_url, stats_call), (points_url, points_call) = calls
    assert stats_url == sources.OUTAGE_URL and points_url == sources.PGE_OUTAGE_URL
    assert stats_call["params"]["where"] == "County IN ('SAN FRANCISCO','ALAMEDA')"
    assert stats_call["params"]["groupByFieldsForStatistics"] == "County,UtilityCompany,OutageType"
    assert stats_call["max_bytes"] <= 64 * 1024 and points_call["max_bytes"] <= 64 * 1024
    # The points are asked for the map's own extent, the largest first, a bounded number of them.
    assert points_call["params"]["geometry"] == sources.OUTAGE_MAP_BBOX
    assert points_call["params"]["resultRecordCount"] == sources.MAX_OUTAGE_POINTS
    assert points_call["params"]["where"] == f"EST_CUSTOMERS>={sources.MIN_POINT_CUSTOMERS}"
    assert stats["customers"] == 232 and len(points["items"]) == 5


def test_outage_points_keep_where_how_many_and_whether_planned():
    data = sources.normalize_outage_points(OUTAGE_POINTS, HOME)

    assert [item["customers"] for item in data["items"]] == [147, 80, 28, 17, 16]
    first, planned = data["items"][0], data["items"][2]
    assert (first["lat"], first["lon"], first["planned"], first["restore"]) == (
        37.7951, -122.4165, False, "2026-11-11T22:15:00+00:00")
    assert planned["planned"] is True and data["items"][4]["restore"] is None
    # Union City's is the one near home (Fremont).
    assert data["items"][3]["distance_km"] < 10 < first["distance_km"]
    assert sources.valid_outage_points(data) and not sources.valid_outage_points({"items": [{"lat": 1}]})
    with pytest.raises(ValueError):
        sources.normalize_outage_points({"error": {"code": 500}}, HOME)


def test_a_quiet_day_is_one_line_and_a_storm_names_the_counties():
    quiet = render.outage_summary({**_payload(), **_sea_and_power()}, NOW)

    assert quiet["lines"] == ["停电 583 户", ("家附近", "17 户")] and quiet["significant"] is False
    assert [point["customers"] for point in quiet["points"]] == [147, 80, 28, 17, 16]
    # Without an outage near home it is the one line.
    far = {"features": OUTAGE_POINTS["features"][:3]}
    assert render.outage_summary({**_payload(), **_sea_and_power(points=far)}, NOW)["lines"] == ["停电 583 户"]
    storm = render.outage_summary({**_payload(), **_sea_and_power(stats=_storm_stats(), points=far)}, NOW)
    assert storm["significant"] is True
    # The counties with the most, each a (county, customers) pair for the box's two edges.
    assert storm["lines"] == ["停电 2.2 万户", ("San Francisco", "9,420"), ("Marin", "5,640"), ("Alameda", "3,497")]
    # No outage at all is said in a line too; without either feed there is nothing to draw.
    none = {"features": []}
    assert render.outage_summary({**_payload(), **_sea_and_power(stats=none, points=none)}, NOW)["lines"] == ["暂无停电"]
    assert render.outage_summary(_payload(), NOW) is None
    gone = {name: {"state": "unavailable", "data": {}} for name in ("outages", "outage_points")}
    assert render.outage_summary({**_payload(), **gone}, NOW) is None
    # The points alone still mark the map, with how many of them there are.
    only_points = {**_sea_and_power(), "outages": {"state": "unavailable", "data": {}}}
    assert render.outage_summary({**_payload(), **only_points}, NOW)["lines"][0] == "停电 5 处"


def test_a_major_outage_counts_towards_the_header_s_attention_pill():
    quiet = {**_payload(quakes=[]), **_sea_and_power()}
    storm = {**_payload(quakes=[]), **_sea_and_power(stats=_storm_stats())}

    assert attention_items(quiet, NOW) == 0
    assert attention_items(storm, NOW) == 1


# --- the sea ------------------------------------------------------------------------------------


def test_the_surf_forecast_gives_each_coast_zone_its_breakers_by_day_and_its_headlines():
    data = sources.parse_surf(SURF_TEXT)

    assert data["issued"] == "2026-11-11T12:06:00-08:00"
    assert set(data["zones"]) == {"CAZ505", "CAZ509"}
    coast = data["zones"]["CAZ509"]
    # The most exposed beaches' range each day, in feet as the forecast gives it.
    assert coast["days"] == [{"date": "2026-11-11", "low": 9, "high": 13, "facing": "West"},
                             {"date": "2026-11-12", "low": 10, "high": 14, "facing": "West"}]
    assert coast["headlines"] == [
        {"event": "Beach Hazards Statement", "when": "THROUGH 6 PM FRIDAY"},
        {"event": "High Surf Warning", "when": "FROM 6 PM FRIDAY THROUGH 4 AM SUNDAY"}]
    assert sources.valid_surf(data) and not sources.valid_surf({"zones": {}})
    for text in ("", "FZUS56 KMTR 112006\nSRFMTR\n", SURF_TEXT.replace("Surf Height", "Swell")):
        with pytest.raises(ValueError):
            sources.parse_surf(text)


def test_breaker_wordings_and_period_names_are_read():
    text = SURF_TEXT.replace("West Facing Beaches - 9 to 13 feet.", "Northwest Facing Beaches - Around 2 feet.").replace(
        ".REST OF TODAY...", ".TONIGHT...").replace(
        "CAZ509-121000-", "CAZ006-509-121000-")
    data = sources.parse_surf(text)

    assert set(data["zones"]) == {"CAZ505", "CAZ006", "CAZ509"}
    assert data["zones"]["CAZ509"]["days"][0] == {"date": "2026-11-11", "low": 3, "high": 5, "facing": "South"}
    low = sources.parse_surf(SURF_TEXT.replace("West Facing Beaches - 9 to 13 feet.\n" + " " * 30 +
                                               "South Facing Beaches - 3 to 5 feet.", "1 foot or less."))
    assert low["zones"]["CAZ509"]["days"][0] == {"date": "2026-11-11", "low": 1, "high": 1, "facing": ""}


def test_the_outlook_is_todays_and_tomorrows_breakers_in_feet_for_the_nearest_coast():
    data = sources.parse_surf(SURF_TEXT)

    outlook = sources.surf_outlook(data, NOW)
    assert outlook["zone"] == "CAZ509"
    assert outlook["days"] == [{"label": "今天", "low": 9, "high": 13}, {"label": "明天", "low": 10, "high": 14}]
    assert outlook["alert"] == {"event": "High Surf Warning", "name": "大浪警告", "tone": "alert",
                                "start": "周五 18:00", "end": "周日 04:00"}
    # A day later the forecast's second day is today; once both are past there is no outlook.
    assert sources.surf_outlook(data, NOW + timedelta(days=1))["days"] == [
        {"label": "今天", "low": 10, "high": 14}]
    assert sources.surf_outlook(data, NOW + timedelta(days=2)) is None
    # Without the San Francisco and San Mateo coast, Sonoma and Marin's stands in.
    north = {**data, "zones": {"CAZ505": data["zones"]["CAZ505"]}}
    assert sources.surf_outlook(north, NOW)["days"][0] == {"label": "今天", "low": 10, "high": 14}
    assert sources.surf_outlook({}, NOW) is None


@pytest.mark.parametrize("phrase, span", [
    ("FROM 6 PM FRIDAY THROUGH 4 AM SUNDAY", ("周五 18:00", "周日 04:00")),
    ("THROUGH 6 PM FRIDAY", ("", "周五 18:00")),
    ("IN EFFECT FROM THURSDAY EVENING THROUGH FRIDAY MORNING", ("周四晚", "周五晨")),
    ("IN EFFECT UNTIL 3 AM PST SATURDAY", ("", "周六 03:00")),
    ("REMAINS IN EFFECT UNTIL 9 PM PST THIS EVENING", ("", "今晚 21:00")),
    ("IN EFFECT THROUGH LATE TONIGHT", ("", "今晚深夜")),
    ("IN EFFECT FROM 1230 PM THIS AFTERNOON TO 10 AM PST THURSDAY", ("今天 12:30", "周四 10:00")),
    ("IN EFFECT FROM LATE THURSDAY NIGHT THROUGH SATURDAY AFTERNOON", ("周四深夜", "周六午后")),
    ("IN EFFECT", ("", "")), ("FOR SNEAKER WAVES", ("", "")), ("", ("", "")),
])
def test_a_headline_s_hours_are_said_in_chinese_or_left_out(phrase, span):
    assert sources.headline_span(phrase) == span


def test_the_bay_forecast_gives_its_advisory_until_it_expires():
    data = sources.parse_marine(MARINE_TEXT)

    assert data == {"issued": "2026-11-11T08:36:00-08:00", "expires": "2026-11-12T05:30:00+00:00", "zone": "PZZ530",
                    "headlines": [{"event": "Small Craft Advisory",
                                   "when": "IN EFFECT FROM THURSDAY EVENING THROUGH FRIDAY MORNING"}]}
    assert sources.valid_marine(data)
    notice = sources.marine_notice(data, NOW)
    assert notice == {"event": "Small Craft Advisory", "name": "小船提示", "tone": "moderate", "start": "周四晚",
                      "end": "周五晨"}
    assert sources.marine_notice(data, NOW + timedelta(days=1)) is None  # the forecast has expired
    calm = sources.parse_marine(MARINE_TEXT.replace(
        "...SMALL CRAFT ADVISORY IN EFFECT FROM THURSDAY EVENING THROUGH\nFRIDAY MORNING...\n", ""))
    assert calm["headlines"] == [] and sources.marine_notice(calm, NOW) is None
    gale = sources.parse_marine(MARINE_TEXT.replace("SMALL CRAFT ADVISORY", "GALE WARNING"))
    assert sources.marine_notice(gale, NOW)["name"] == "烈风警告" and sources.marine_notice(gale, NOW)["tone"] == "alert"
    with pytest.raises(ValueError):
        sources.parse_marine("FZUS56 KMTR 111636\nCWFMTR\n")


# --- loading ------------------------------------------------------------------------------------


NEW_SOURCES = ("outages", "outage_points", "surf", "marine")


def test_the_new_feeds_are_cached_extras_that_never_decide_the_page_s_health(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    plugin = BayAlerts({"id": "bay_alerts"})
    task = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)

    first = plugin._payload({}, NOW, allow_network=True, context=task)
    assert [first[name]["state"] for name in NEW_SOURCES] == ["live"] * 4
    assert first["outages"]["data"]["customers"] == 583 and first["provenance"] is SourceProvenance.LIVE

    def offline(*_args):
        raise OSError("offline")

    for name in ("fetch_outages", "fetch_outage_points", "fetch_surf", "fetch_marine"):
        monkeypatch.setattr(sources, name, offline)
    # Within each feed's freshness nothing is asked again (the offline fetchers would fail it).
    soon = plugin._payload({}, NOW + timedelta(minutes=10), allow_network=True, context=task)
    assert [soon[name]["state"] for name in NEW_SOURCES] == ["fresh_cache"] * 4
    # Past it a failed refresh keeps the last good copy: the outages while they are still news,
    # the forecasts for longer; the page's own health never notices.
    later = plugin._payload({}, NOW + timedelta(hours=2, minutes=30), allow_network=True, context=task)
    assert [later[name]["state"] for name in NEW_SOURCES] == ["stale_cache"] * 4
    assert later["outages"]["data"]["customers"] == 583
    assert later["provenance"] in (SourceProvenance.LIVE, SourceProvenance.FRESH_CACHE)
    old = plugin._payload({}, NOW + timedelta(hours=8), allow_network=True, context=task)
    assert [old[name]["state"] for name in NEW_SOURCES] == ["unavailable", "unavailable", "stale_cache", "stale_cache"]
    assert old["provenance"] is not SourceProvenance.LOCAL_FALLBACK
    # A redraw from cache never fetches.
    for name in ("fetch_outages", "fetch_outage_points", "fetch_surf", "fetch_marine"):
        monkeypatch.setattr(sources, name, lambda *_args: pytest.fail("a cache-only redraw fetched"))
    redraw = plugin._payload({}, NOW + timedelta(hours=2, minutes=30), allow_network=False, context=task)
    assert [redraw[name]["state"] for name in NEW_SOURCES] == ["stale_cache"] * 4


def test_a_page_without_the_new_feeds_draws_exactly_as_before(monkeypatch):
    plain = _payload()
    gone = {name: {"state": "unavailable", "data": {}, "error": "OSError"} for name in NEW_SOURCES}

    before = render_page((800, 480), plain, DAY, NOW)
    after = render_page((800, 480), {**plain, **gone}, DAY, NOW)

    assert before.tobytes() == after.tobytes()
    for key in ("outage_box", "surf_box", "marine_tag"):
        assert after.info[key] is None
    assert after.info["outage_marks"] == 0
    # A feed that answers nonsense is left out the same way.
    odd = {**plain, "outages": _live({"customers": "many"}), "outage_points": _live({"items": [{"lat": "x"}]}),
           "surf": _live({"zones": {"CAZ509": {"days": "soon"}}}), "marine": _live({"headlines": [1]})}
    assert render_page((800, 480), odd, DAY, NOW).tobytes() == before.tobytes()


# --- on the page --------------------------------------------------------------------------------


@pytest.fixture(params=("default", "public_noto"))
def map_fonts(request, monkeypatch):
    """Exercise the tracked public fallback even when local YaHei files are installed."""
    if request.param == "public_noto":
        from utils import app_utils, page_canvas

        font = Path(__file__).resolve().parents[1] / "src/static/fonts/NotoSansSC-VF.ttf"
        assert font.is_file()
        monkeypatch.setattr(app_utils, "base_ui_font_candidates", lambda bold=False: (str(font),))
        monkeypatch.setattr(page_canvas, "get_font", lambda _name, size, weight: app_utils.get_base_ui_font(
            size, bold=weight == "bold"))


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_map_carries_the_outages_the_surf_and_the_bay_advisory_and_the_column_stays(monkeypatch, mode, map_fonts):
    settings = {"_inkypi_theme": {"mode": mode}}
    plain = render_page((800, 480), _payload(), settings, NOW)
    drawn = _record_text(monkeypatch)

    image = render_page((800, 480), {**_payload(), **_sea_and_power()}, settings, NOW)

    # The right column is planned exactly as without them.
    assert image.info["column_cards"] == plain.info["column_cards"]
    assert image.crop((460, 56, 800, 480)).tobytes() == plain.crop((460, 56, 800, 480)).tobytes()
    assert image.crop((0, 0, 800, 56)).tobytes() == plain.crop((0, 0, 800, 56)).tobytes()
    # Every outage point inside the map is a bolt on it.
    assert image.info["outage_marks"] == 5
    boxes = [image.info["outage_box"], image.info["surf_box"], image.info["map_summary"]]
    assert all(boxes)
    for left, top, right, bottom in boxes + [image.info["marine_tag"]]:
        assert 0 <= left < right <= 440 and 0 <= top < bottom <= 388
    assert not any(map_view._overlaps(first, second) for index, first in enumerate(boxes) for second in boxes[index + 1:])
    # The boxes stand in the ocean, the Bay advisory in San Pablo Bay.
    assert all(box[2] <= 120 for box in boxes) and image.info["marine_tag"][1] < 70
    assert {"停电 583 户", "家附近", "17 户", "浪高 · 英尺", "今天 9–13", "明天 10–14", "小船提示",
            "周四晚–周五晨"} <= set(drawn)
    # The alert rows do not list the high surf here, so the surf box names it.
    assert "大浪警告" in drawn and "周五 18:00 起" in drawn


def test_a_high_surf_alert_the_card_already_lists_is_not_named_twice(monkeypatch):
    alerts = sources.normalize_alerts({"features": [_alert("High Surf Warning", ["006075"])]},
                                      sources.counties_setting(), OCTOBER)["items"]
    assert alerts and alerts[0]["name"] == "大浪警告"
    drawn = _record_text(monkeypatch)

    image = render_page((800, 480), {**_payload(alerts=alerts), **_sea_and_power()}, DAY, NOW)

    assert image.info["column_cards"]["alerts"]["rows"] == 1
    # Its name is drawn once, by the card's row; the surf box keeps to the breakers.
    assert "今天 9–13" in drawn and drawn.count("大浪警告") == 1 and "周五 18:00 起" not in drawn
    # Hidden below the rows the card shows ("另有 N 条"), it is named by the surf box again.
    assert render.surf_summary({**_payload(alerts=alerts), **_sea_and_power()}, NOW, shown=())["alert"] is not None
    assert render.surf_summary({**_payload(alerts=alerts), **_sea_and_power()}, NOW,
                               shown=("High Surf Warning",))["alert"] is None


def test_a_storm_grows_the_outage_box_and_labels_the_big_outages(monkeypatch, map_fonts):
    big = {"features": [{**feature, "attributes": {**feature["attributes"],
                                                   "EST_CUSTOMERS": feature["attributes"]["EST_CUSTOMERS"] * 40}}
                        for feature in OUTAGE_POINTS["features"][:5]]}
    quiet = render_page((800, 480), {**_payload(), **_sea_and_power()}, DAY, NOW)
    drawn = _record_text(monkeypatch)

    storm = render_page((800, 480), {**_payload(), **_sea_and_power(stats=_storm_stats(), points=big)}, DAY, NOW)

    quiet_box, storm_box = quiet.info["outage_box"], storm.info["outage_box"]
    assert storm_box[3] - storm_box[1] > quiet_box[3] - quiet_box[1]
    assert {"停电 2.2 万户", "San Francisco", "9,420", "Marin", "5,640", "Alameda", "3,497"} <= set(drawn)
    # An outage of a thousand customers or more carries its size beside its bolt.
    assert "5,880 户" in drawn and "3,200 户" in drawn and "1,120 户" in drawn and "640 户" not in drawn
    assert {"家附近", "680 户"} <= set(drawn)
    assert storm.info["column_cards"] == quiet.info["column_cards"]
    # The ocean still holds all three notes, the surf's shortened if it must be.
    boxes = [storm.info["outage_box"], storm.info["surf_box"], storm.info["map_summary"]]
    assert all(boxes)
    assert not any(map_view._overlaps(first, second) for index, first in enumerate(boxes) for second in boxes[index + 1:])


def test_a_note_gives_up_its_later_rows_before_its_alert_when_the_ocean_is_short():
    from PIL import Image, ImageDraw

    from utils.page_canvas import PageCanvas

    palette = render._palette(DAY)
    fonts = PageCanvas(palette).font
    draw = ImageDraw.Draw(Image.new("RGB", (440, 408)))
    note = {"lines": ["浪高 · 英尺", "今天 9–13", "明天 10–14"],
            "alert": {"name": "大浪警告", "tone": "alert", "when": "周五 18:00 起"}}
    frame = (2, 2, 438, 388)

    def placed(room):
        box = map_view._place_note(draw, palette, fonts, note, lambda centre: None,
                                   lambda height: range(100, 100 + room - height + 1, 2), [], frame)
        return box and box[3] - box[1]

    whole = map_view._note_size(draw, fonts, note)[1]
    assert placed(200) == whole == map_view.NOTE_HEAD + 2 * map_view.NOTE_ROW + 17 + map_view.NOTE_ROW
    assert placed(whole - 1) == whole - map_view.NOTE_ROW          # without the alert's hours
    assert placed(whole - map_view.NOTE_ROW - 1) == map_view.NOTE_HEAD + map_view.NOTE_ROW + 17   # one row and the alert
    assert placed(map_view.NOTE_HEAD + 17) == map_view.NOTE_HEAD + map_view.NOTE_ROW   # then rows without it
    assert placed(map_view.NOTE_HEAD) == map_view.NOTE_HEAD and placed(map_view.NOTE_HEAD - 1) is None
