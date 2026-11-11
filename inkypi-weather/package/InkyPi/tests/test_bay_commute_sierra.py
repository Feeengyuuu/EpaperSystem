"""Bay Commute's Sierra road conditions: Caltrans' text for I-80, US-50 and SR-120, its chain
controls in force and Palisades Tahoe's status; one source read per refresh, a quiet footer line
when nothing is restricted and the header's sign when chains are required or a pass is shut."""

from datetime import datetime, timedelta

import pytest

from plugins.base_plugin.render_provenance import SourceProvenance
from plugins.bay_commute import sierra, sources
from plugins.bay_commute.bay_commute import BayCommute
from plugins.bay_commute.command_render import SIGN_BOX, Page
from plugins.bay_commute.render import render_page
from tests.test_bay_commute import (
    BART_PAYLOAD, BUOY_PAGE, CMS_PAYLOAD, SETTINGS, context, road_data, tide_data,
)


NOW = datetime(2026, 11, 11, 13, 0, tzinfo=sources.PACIFIC)

# roads.dot.ca.gov/roadscell.php?roadnumber=80,50,120 as it answered on 2026-11-11 (its report
# from the "latest reported" line to the closing rule, verbatim).
ROADS_PAGE = """<div>
            <hr>
<p><em>This highway information is the latest reported as of Wednesday, November 11th, 2026 at 12:51 PM.</em></p>
<h3>US 50</h3>
<p>
<strong>[IN THE SACRAMENTO VALLEY & THE LAKE TAHOE BASIN]</strong><br />
The westbound connector to southbound SR 99 /in Sacramento/ (Sacramento Co)
is closed from 2300 hrs each night to 0500 hrs each morning Monday thru Friday
thru 11/13/26 - Due to construction - Motorists are advised to use an
alternate route
</p>
<p>
1-way controlled traffic at various locations from 2.1 mi east of Riverton
to 2.5 mi west of Kyburz /at 30 Milestone Tract/ (El Dorado Co) from 1900 hrs
each night to 0600 hrs each morning Sunday thru Saturday thru 11/14/26 - Due to
construction
</p>
<p>
<strong>[CAPITAL CITY FREEWAY]</strong><br />
The southbound connector to westbound US 50 /in Sacramento/ (Sacramento Co)
is closed from 2300 hrs each night to 0500 hrs each morning Monday thru Friday
thru 11/13/26 - Due to construction - Motorists are advised to use an
alternate route
</p>
<h3>I 80</h3>
<p>
<strong>[IN THE SAN FRANCISCO BAY AREA - SOLANO CO]</strong><br />
There is a dense fog advisory in effect at the San Francisco-Oakland Bay
Bridge /in San Francisco/ (San Francisco,Alameda Co) - Motorists are advised tO
drive with caution
</p>
<p>
<strong>[IN THE NORTHERN CALIFORNIA AREA & SIERRA NEVADA]</strong><br />
All westbound trucks are required to stop at the brake check area
0.5 mi west of Nyack (Placer Co) - To reduced the risk of brake fires
</p>
<p>
<strong>[BUSINESS 80 - CAPITAL CITY FREEWAY]</strong><br />
The southbound connector to westbound US 50 /in Sacramento/ (Sacramento Co)
is closed from 2300 hrs each night to 0500 hrs each morning Monday thru Friday
thru 11/13/26 - Due to construction - Motorists are advised to use an
alternate route
</p>
<h3>SR 120</h3>
<p>
<strong>[IN THE CENTRAL CALIFORNIA AREA & SIERRA NEVADA]</strong><br />
Chains or snow tires are required from Sand Flat Cut Off Rd to Benton
Crossing Rd (Mono CO)
</p>
<p>
Please research chain control locations as Caltrans is currently working to
update chain control descriptions for consistency with quickmap.dot.ca.gov
</p>
<p>
<strong>[YOSEMITE NAT'L PARK]</strong><br />
For Yosenite Nat'l Park road information call 209-372-0200
</p>
<hr />
            </div>"""

# The same page in a storm, written here in the wording Caltrans uses for chain controls and
# closures (not a recorded response: none was in force on the day the page was built).
STORM_PAGE = """<p><em>This highway information is the latest reported as of Saturday, January 9th, 2027 at 6:05 AM.</em></p>
<h3>US 50</h3>
<p>
<strong>[IN THE SACRAMENTO VALLEY & THE LAKE TAHOE BASIN]</strong><br />
Is closed from 3 mi east of Twin Bridges to Meyers /Echo Summit/ (El Dorado Co) - Due to
snow - Motorists are advised to use an alternate route
</p>
<h3>I 80</h3>
<p>
<strong>[IN THE NORTHERN CALIFORNIA AREA & SIERRA NEVADA]</strong><br />
Chains are required on all vehicles except 4-wheel-drive vehicles with snow tires on all 4
wheels from Kingvale (Placer Co) to Truckee (Nevada Co)
</p>
<p>
Chains or snow tires are required from 7.5 mi east of Emigrant Gap to Kingvale (Placer Co)
</p>
<p>
Eastbound trucks are being screened at Applegate (Placer Co) - Chains are required on all
commercial vehicles
</p>
<h3>SR 120</h3>
<p>
<strong>[IN THE CENTRAL CALIFORNIA AREA & SIERRA NEVADA]</strong><br />
Is closed from Crane Flat to 5 mi west of the Jct of US 395 /Tioga Pass/ (Tuolumne, Mono Co)
- For the winter - Motorists are advised to use an alternate route
</p>
<hr />"""

# quickmap.dot.ca.gov/data/cc.kml on 2026-11-11: no chain control in force anywhere in the state.
CHAINS_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<kml xsi:schemaLocation="http://www.opengis.net/kml/2.2 http://schemas.opengis.net/kml/2.2.0/ogckml22.xsd" xmlns="http://www.opengis.net/kml/2.2" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <Document>
        <name>Caltrans Chain Controls</name>
        <Style id="notclosed">
            <IconStyle>
                <colorMode>normal</colorMode>
                <Icon>
                    <href>https://quickmap.dot.ca.gov/img/cc32x32.png</href>
                </Icon>
                <hotSpot x="0.5" y="0.5" xunits="fraction" yunits="fraction"/>
            </IconStyle>
            <LabelStyle>
                <scale>0</scale>
            </LabelStyle>
        </Style>
        <Style id="full-closure">
            <IconStyle>
                <colorMode>normal</colorMode>
                <Icon>
                    <href>https://quickmap.dot.ca.gov/img/full-closure-32x32.png</href>
                </Icon>
                <hotSpot x="0.5" y="0.5" xunits="fraction" yunits="fraction"/>
            </IconStyle>
            <LabelStyle>
                <scale>0</scale>
            </LabelStyle>
        </Style>
    </Document>
</kml>"""
# With controls in force the file was never seen: these placemarks are an assumption (the two
# styles the real file declares, a route and an R level in the text).
CHAINS_ACTIVE = CHAINS_EMPTY.replace("    </Document>", """        <Placemark>
            <name>I-80 Kingvale</name>
            <description><![CDATA[<b>R-2</b>: Chains are required on all vehicles except 4-wheel-drive]]></description>
            <styleUrl>#notclosed</styleUrl>
            <Point><coordinates>-120.4337,39.3181,0</coordinates></Point>
        </Placemark>
        <Placemark>
            <name>SR-88 Carson Spur</name>
            <description><![CDATA[Closed due to avalanche control]]></description>
            <styleUrl>#full-closure</styleUrl>
            <Point><coordinates>-120.0891,38.7012,0</coordinates></Point>
        </Placemark>
    </Document>""")

# mtnpowder.com/feed?resortId=61 on 2026-11-11, cut down to the fields the page reads (the whole
# feed is 177 KB).
RESORT_FEED = {
    "Name": "Palisades Tahoe", "LastUpdate": "2026-11-11T12:47:28-0800", "OperatingStatus": "Closed",
    "SnowReport": {
        "LastUpdate": "2026-11-11T11:40:13-0800",
        "Alert": "<div><a href=\"https://www.palisadestahoe.com/events-and-activities/events-calendar/opening-day\">"
                 "Opening Day</a> is scheduled for November 25, 2026 weather and conditions permitting.</div>",
        "TotalOpenTrails": 0, "TotalTrails": 296, "TotalOpenLifts": 0, "TotalLifts": 39,
        "BaseArea": {"Name": "Palisades Base, el. 6200'", "BaseIn": "0", "BaseCm": "0", "Last24HoursIn": "0",
                     "Last24HoursCm": "0"},
        "SummitArea": {"Name": "Palisades Siberia Crest, el. 8700'", "BaseIn": "--", "BaseCm": "0",
                       "Last24HoursIn": "--", "Last24HoursCm": "0"}},
    "CurrentConditions": {"Base": {"Name": "Palisades Base, el. 6200'", "TemperatureF": "62", "TemperatureC": "17",
                                   "Skies": "Sunny"}},
}
RESORT_OPEN = {**RESORT_FEED, "OperatingStatus": "Open", "SnowReport": {
    **RESORT_FEED["SnowReport"], "Alert": "--", "TotalOpenLifts": 22, "TotalOpenTrails": 141,
    "BaseArea": {"BaseCm": "112", "Last24HoursCm": "20"}},
    "CurrentConditions": {"Base": {"TemperatureF": "27", "TemperatureC": "-3"}}}


def test_road_conditions_read_each_sierra_route_and_leave_out_the_valley_and_the_east_side():
    data = sierra.parse_road_conditions(ROADS_PAGE)

    assert sierra.valid_roads(data)
    assert data["reported_at"] == "2026-11-11T12:51:00-08:00"
    # Sacramento's connector closed for construction is no Sierra closure; the chains east of
    # US 395 in Mono County are not on the way to Yosemite.
    assert data["routes"] == [{"route": "I-80", "level": "clear", "from": "", "to": ""},
                              {"route": "US-50", "level": "clear", "from": "", "to": ""},
                              {"route": "SR-120", "level": "clear", "from": "", "to": ""}]
    with pytest.raises(ValueError):
        sierra.parse_road_conditions("<html><body>Service unavailable</body></html>")


def test_storm_wording_gives_chain_levels_closures_and_winter_closures():
    data = sierra.parse_road_conditions(STORM_PAGE)

    assert sierra.valid_roads(data) and data["reported_at"] == "2027-01-09T06:05:00-08:00"
    # The worst of a route's notes stands for it; a truck-only chain rule is not one for cars.
    assert data["routes"] == [
        {"route": "I-80", "level": "r2", "from": "Kingvale", "to": "Truckee"},
        {"route": "US-50", "level": "closed", "from": "Twin Bridges", "to": "Meyers"},
        {"route": "SR-120", "level": "seasonal", "from": "Crane Flat", "to": "Jct of US 395"}]


def test_chain_controls_count_what_is_in_force_and_name_its_routes():
    quiet = sierra.parse_chain_controls(CHAINS_EMPTY)
    active = sierra.parse_chain_controls(CHAINS_ACTIVE)

    assert quiet == {"count": 0, "closures": 0, "routes": {}} and sierra.valid_chains(quiet)
    assert active == {"count": 2, "closures": 1, "routes": {"I-80": "r2"}} and sierra.valid_chains(active)
    with pytest.raises(ValueError):
        sierra.parse_chain_controls("<html>maintenance</html>")


def test_resort_feed_gives_status_lifts_base_temperature_and_the_announced_opening_day():
    closed = sierra.parse_resort(RESORT_FEED, NOW)
    opened = sierra.parse_resort(RESORT_OPEN, NOW)

    assert closed == {"name": "Palisades Tahoe", "open": False, "lifts_open": 0, "lifts_total": 39,
                      "trails_open": 0, "trails_total": 296, "base_temp_c": 17, "base_cm": 0, "new_cm": 0,
                      "opening": "2026-11-25", "updated": "2026-11-11T12:47:28-08:00"}
    assert sierra.valid_resort(closed) and sierra.valid_resort(opened)
    assert (opened["open"], opened["lifts_open"], opened["base_temp_c"], opened["new_cm"], opened["opening"]) == (
        True, 22, -3, 20, None)
    # An opening day named without its year is the next one to come.
    undated = {**RESORT_FEED, "SnowReport": {**RESORT_FEED["SnowReport"],
                                              "Alert": "Opening Day is scheduled for January 8th."}}
    assert sierra.parse_resort(undated, NOW)["opening"] == "2027-01-08"
    with pytest.raises(ValueError):
        sierra.parse_resort({"error": "not found"}, NOW)


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("BAY_COMMUTE_CACHE_DIR", str(tmp_path / "bay-cache"))
    return BayCommute({"id": "bay_commute"})


@pytest.fixture
def offline_page(monkeypatch):
    """The page's other sources from their samples; the Sierra's three record each read in ``calls``."""
    calls = []

    def reader(name, value):
        def read(*args):
            calls.append(name)
            return value() if callable(value) else value
        return read

    monkeypatch.setattr(sources, "fetch_roads", lambda routes, now, task, **kwargs: road_data(now))
    monkeypatch.setattr(sources, "fetch_tides", lambda station, now, task: tide_data(now))
    monkeypatch.setattr(sources, "fetch_buoy", lambda task: sources.parse_buoy(BUOY_PAGE))
    monkeypatch.setattr(sources, "fetch_bart_network", lambda now, task: sources.parse_bart_network(BART_PAYLOAD, now))
    monkeypatch.setattr(sources, "fetch_bart_status",
                        lambda now, task: {"time": now.isoformat(), "advisories": [], "elevators": None, "trains": 56})
    monkeypatch.setattr(sources, "fetch_signs", lambda routes, task: sources.parse_signs(CMS_PAYLOAD, routes))
    monkeypatch.setattr(sources, "fetch_water_level", lambda station, task: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(sources, "fetch_bike_stations", lambda task: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(sierra, "fetch_road_conditions", reader("roads", lambda: sierra.parse_road_conditions(ROADS_PAGE)))
    monkeypatch.setattr(sierra, "fetch_chain_controls", reader("chains", lambda: sierra.parse_chain_controls(CHAINS_EMPTY)))
    monkeypatch.setattr(sierra, "fetch_resort", reader("resort", lambda: sierra.parse_resort(RESORT_FEED, NOW)))
    return calls


def test_one_sierra_source_is_read_per_refresh_each_resting_on_its_own_ttl(plugin, offline_page):
    calls = offline_page
    first = plugin._payload(SETTINGS, NOW, allow_network=True, context=context())
    # The roads come first; the page already says what it knows.
    assert calls == ["roads"] and first["sierra"]["routes"][0] == {"route": "I-80", "level": "clear", "from": "", "to": ""}
    assert first["sierra"]["resort"] is None and first["provenance"] == SourceProvenance.LIVE
    plugin._payload(SETTINGS, NOW + timedelta(minutes=10), allow_network=True, context=context())
    third = plugin._payload(SETTINGS, NOW + timedelta(minutes=20), allow_network=True, context=context())
    assert calls == ["roads", "chains", "resort"]
    assert third["sierra"]["resort"]["opening"] == "2026-11-25" and third["sierra"]["alert"] is False
    # All three rest now: nothing is read until one is due again, and then only the most overdue.
    plugin._payload(SETTINGS, NOW + timedelta(minutes=50), allow_network=True, context=context())
    assert calls == ["roads", "chains", "resort"]
    plugin._payload(SETTINGS, NOW + timedelta(hours=2, minutes=5), allow_network=True, context=context())
    plugin._payload(SETTINGS, NOW + timedelta(hours=2, minutes=6), allow_network=True, context=context())
    # (The chain controls rest one hour in the season, the roads two: the controls are further past.)
    assert calls == ["roads", "chains", "resort", "chains", "roads"]


def test_a_storm_brings_the_roads_and_chain_controls_back_sooner(plugin, offline_page, monkeypatch):
    calls = offline_page
    monkeypatch.setattr(sierra, "fetch_road_conditions",
                        lambda *args: calls.append("roads") or sierra.parse_road_conditions(STORM_PAGE))
    storm = NOW.replace(year=2027, month=1, day=9, hour=6, minute=10)
    for minutes in (0, 5, 10):
        plugin._payload(SETTINGS, storm + timedelta(minutes=minutes), allow_network=True, context=context())
    assert calls == ["roads", "chains", "resort"]
    # Thirty-five minutes on (in calm weather the roads would rest two hours).
    later = plugin._payload(SETTINGS, storm + timedelta(minutes=35), allow_network=True, context=context())
    assert calls[-1] == "roads" and len(calls) == 4 and later["sierra"]["alert"] is True


def test_a_failed_sierra_source_keeps_its_last_reading_and_never_marks_the_page(plugin, offline_page, monkeypatch):
    calls = offline_page
    for minutes in (0, 10, 20):
        plugin._payload(SETTINGS, NOW + timedelta(minutes=minutes), allow_network=True, context=context())

    def offline(*args):
        calls.append("failed")
        raise OSError("offline")

    for name in ("fetch_road_conditions", "fetch_chain_controls", "fetch_resort"):
        monkeypatch.setattr(sierra, name, offline)
    later = plugin._payload(SETTINGS, NOW + timedelta(hours=3), allow_network=True, context=context())
    assert calls[3:] == ["failed"]
    assert later["sierra"]["routes"] and later["sierra"]["resort"]["lifts_total"] == 39
    assert later["provenance"] in {SourceProvenance.LIVE, SourceProvenance.FRESH_CACHE}
    # Past their keep the old readings are no longer shown, and the page is still not marked.
    stale = plugin._payload(SETTINGS, NOW + timedelta(days=3), allow_network=True, context=context())
    assert stale["sierra"] is None and stale["provenance"] != SourceProvenance.LOCAL_FALLBACK
    # Nothing but a cache: never a request, never a write.
    before = sorted(path.name for path in plugin._cache_dir().iterdir())
    count = len(calls)
    plugin._payload(SETTINGS, NOW + timedelta(days=3, minutes=1), allow_network=False, context=context())
    assert len(calls) == count and before == sorted(path.name for path in plugin._cache_dir().iterdir())


def test_a_cache_only_redraw_reads_no_sierra_source(plugin, offline_page):
    payload = plugin._payload(SETTINGS, NOW, allow_network=False, context=context())

    assert offline_page == [] and payload["sierra"] is None


def reading(data, at=NOW):
    return {"state": "live", "data": data, "fetched_at": at.isoformat()}


def test_the_summary_is_quiet_out_of_season_and_loud_for_chains_or_a_closed_pass():
    clear = reading(sierra.parse_road_conditions(ROADS_PAGE))
    resort = reading(sierra.parse_resort(RESORT_FEED, NOW))
    quiet = sierra.summary(clear, reading(sierra.parse_chain_controls(CHAINS_EMPTY)), resort, NOW)
    assert quiet["alert"] is False and quiet["resort"]["opening"] == "2026-11-25"
    # July, nothing restricted, the lifts shut with no opening day: nothing at all.
    july = NOW.replace(year=2027, month=7)
    shut = reading({**sierra.parse_resort(RESORT_FEED, july), "opening": None}, july)
    assert sierra.summary(reading(clear["data"], july), {"state": "unavailable", "data": {}}, shut, july) is None
    # A closure in July (a fire, a slide) is still an alert.
    storm = sierra.parse_road_conditions(STORM_PAGE)
    assert sierra.summary(reading(storm, july), {"state": "unavailable"}, {"state": "unavailable"}, july)["alert"] is True
    # A winter closure alone is not an alert; the chain controls in force raise a route the text
    # has not caught up with; a reading too old to trust is left out.
    tioga = {**storm, "routes": [row for row in storm["routes"] if row["route"] == "SR-120"]}
    assert sierra.summary(reading(tioga), {"state": "unavailable"}, {"state": "unavailable"}, NOW)["alert"] is False
    raised = sierra.summary(clear, reading(sierra.parse_chain_controls(CHAINS_ACTIVE)), resort, NOW)
    assert raised["alert"] is True and raised["routes"][0] == {"route": "I-80", "level": "r2", "from": "", "to": ""}
    old = sierra.summary(reading(storm, NOW - timedelta(hours=13)), {"state": "unavailable"}, resort, NOW)
    assert old["routes"] == [] and old["alert"] is False


def page_payload(sierra_summary, sign=True):
    payload = {"roads": {"state": "live", "data": {"items": []}, "fetched_at": NOW.isoformat()},
               "tides": {"state": "live", "data": tide_data(NOW), "fetched_at": NOW.isoformat()},
               "buoy": {"state": "live", "data": sources.parse_buoy(BUOY_PAGE)},
               "bart": {"state": "live", "data": sources.parse_bart(BART_PAYLOAD, NOW)},
               "bart_status": {"state": "unavailable"},
               "sign": sources.parse_signs(CMS_PAYLOAD, ["I-880"])["signs"][0] if sign else None,
               "routes": [], "station": "9414523", "window_days": 7, "nearby_cameras": {}}
    if sierra_summary != "absent":
        payload["sierra"] = sierra_summary
    return payload


def draw(monkeypatch, payload, mode="day"):
    """The page and where each text went: {text: (x, y, width)}."""
    placed = {}
    original = Page.text

    def capture(self, xy, text, *args, **kwargs):
        width = original(self, xy, text, *args, **kwargs)
        placed[str(text)] = (round(xy[0]), round(xy[1]), width)
        return width

    monkeypatch.setattr(Page, "text", capture)
    try:
        return render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW), placed
    finally:
        monkeypatch.setattr(Page, "text", original)


def quiet_summary():
    return sierra.summary(reading(sierra.parse_road_conditions(ROADS_PAGE)),
                          reading(sierra.parse_chain_controls(CHAINS_EMPTY)),
                          reading(sierra.parse_resort(RESORT_FEED, NOW)), NOW)


def storm_summary():
    return sierra.summary(reading(sierra.parse_road_conditions(STORM_PAGE)),
                          reading(sierra.parse_chain_controls(CHAINS_EMPTY)),
                          reading(sierra.parse_resort(RESORT_OPEN, NOW)), NOW)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_nothing_restricted_takes_one_quiet_footer_line_and_leaves_the_rest_alone(monkeypatch, mode):
    plain, _ = draw(monkeypatch, page_payload("absent"), mode)
    none, _ = draw(monkeypatch, page_payload(None), mode)
    quiet, placed = draw(monkeypatch, page_payload(quiet_summary()), mode)

    assert plain.tobytes() == none.tobytes()
    line = next(text for text in placed if text.startswith("雪山 "))
    assert "I-80 US-50 SR-120 无管制" in line and "Palisades 11/25 开放" in line and "山脚 17°C" in line
    # In the footer, between the roads' stamp and the tides', clear of both.
    x, y, width = placed[line]
    left = next(value for text, value in placed.items() if text.startswith("道路"))
    right = next(value for text, value in placed.items() if text.startswith("NOAA"))
    assert y == left[1] == right[1] and x >= left[0] + left[2] + 12 and x + width <= right[0] - right[2] - 12
    # The travel-time sign keeps the header and nothing above the footer's rule moves.
    assert "MILPITAS" in placed and quiet.crop((0, 0, 800, 456)).tobytes() == plain.crop((0, 0, 800, 456)).tobytes()
    assert quiet.crop((0, 456, 800, 480)).tobytes() != plain.crop((0, 456, 800, 480)).tobytes()


@pytest.mark.parametrize("mode", ["day", "night"])
def test_chains_or_a_closed_pass_take_the_headers_sign(monkeypatch, mode):
    from plugins.bay_commute.command_render import DAY, NIGHT
    from PIL import ImageColor

    plain, _ = draw(monkeypatch, page_payload("absent"), mode)
    storm, placed = draw(monkeypatch, page_payload(storm_summary()), mode)

    # The three routes with what holds on each, in the sign's place; the travel times give way.
    assert "MILPITAS" not in placed and "雪山路况" in placed
    assert {"I-80", "US-50", "SR-120", "R2 两驱装链", "封闭", "冬季封闭", "Kingvale–Truckee"} <= set(placed)
    for text in ("I-80", "封闭", "Kingvale–Truckee"):
        x, y, width = placed[text]
        assert SIGN_BOX[0] <= x and x + width <= SIGN_BOX[2] and SIGN_BOX[1] <= y <= SIGN_BOX[3] - 10
    # Each state's colour is a solid mark beside ink text: red for the closed pass, orange for chains.
    palette = NIGHT if mode == "night" else DAY
    colours = {colour for _count, colour in storm.crop(SIGN_BOX).getcolors(8192)}
    assert {ImageColor.getrgb(palette["crimson"]), ImageColor.getrgb(palette["orange"])} <= colours
    # The footer's line turns to the resort; the columns under the header are as they were.
    line = next(text for text in placed if text.startswith("雪山 "))
    assert "无管制" not in line and "Palisades 开放" in line and "缆车 22/39" in line and "山脚 -3°C" in line
    assert storm.crop((0, 66, 800, 456)).tobytes() == plain.crop((0, 66, 800, 456)).tobytes()


def test_a_long_stretch_is_cut_short_inside_the_sign(monkeypatch):
    summary = storm_summary()
    summary["routes"][0].update({"from": "7.5 mi east of the Emigrant Gap Overcrossing", "to": "Donner Lake Interchange"})
    image, _placed = draw(monkeypatch, page_payload(summary))

    cut, box = next((text, box) for text, box in image.info["bay_text_bounds"] if text.startswith("7.5 mi"))
    assert cut.endswith("...") and SIGN_BOX[0] < box[0] and box[2] <= SIGN_BOX[2]
