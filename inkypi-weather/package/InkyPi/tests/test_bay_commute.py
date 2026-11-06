import csv
import io
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.bay_commute import sources
from plugins.bay_commute.bay_commute import BayCommute
from plugins.bay_commute.command_render import Page as Canvas
from plugins.bay_commute.render import render_page
from runtime.refresh_contracts import TaskCancelled, TaskContext


NOW = datetime(2026, 9, 29, 22, 30, tzinfo=sources.PACIFIC)
SETTINGS = {"routes": "I-880, I-680, SR-84", "tideStation": "9414523", "windowDays": 7}


def context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic()+30)


def csv_row(route="I-880", **overrides):
    row = {"index": "closure-1", "beginRoute": route, "closureStartDate": "2026-09-29",
           "closureStartTime": "20:00:00", "closureEndDate": "2026-09-30", "closureEndTime": "05:00:00",
           "typeOfClosure": "Lane", "isCode1097": "true", "isCode1098": "false", "isCode1022": "false",
           "recordEpoch": str(NOW.timestamp()), "travelFlowDirection": "North",
           "beginLocationName": "Adeline St", "endLocationName": "Union St", "beginNearbyPlace": "Oakland",
           "facility": "Mainline", "typeOfWork": "Electrical Work"}
    row.update(overrides)
    return row


def csv_text(rows):
    output = io.StringIO()
    headers = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(output, headers)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def road_data(now=NOW):
    row = csv_row(recordEpoch=str(now.timestamp()), closureStartDate=now.date().isoformat(),
                  closureEndDate=(now.date()+timedelta(days=1)).isoformat())
    return sources.parse_roads_csv(csv_text([row]), sources.DEFAULT_ROUTES, now)


def tide_data(now=NOW):
    begin = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = begin+timedelta(days=2)
    cursor = begin.astimezone(timezone.utc)
    curve = []
    while cursor < end:
        curve.append({"time": cursor.isoformat(), "height": 3.5})
        cursor += timedelta(minutes=15)
    extremes = []
    for day in (0,1):
        for hour, kind in ((6,"H"),(12,"L"),(18,"H"),(23,"L")):
            stamp = begin+timedelta(days=day,hours=hour)
            extremes.append({"time": stamp.astimezone(timezone.utc).isoformat(),
                             "height": 7.1 if kind == "H" else 0.9, "type": kind})
    return {"station": "9414523", "prediction_date": now.date().isoformat(),
            "end_date": (now.date()+timedelta(days=1)).isoformat(), "extremes": extremes, "curve": curve}


BUOY_PAGE = """Station 46026
37\xb0 45.0' N  122\xb0 50.3' W

8:00 am PDT
1500 GMT 10/25/26
Wind: NE (40\xb0), 3.9 kt
Gust: 5.8 kt
Pres: 30.05 rising
Water Temp: 64.0 \xb0F

Wave Summary
7:40 am PDT
1400 GMT 10/25/26
Swell: 3.0 ft
Period: 8.3 sec
Direction: NW
Wind Wave: 1.0 ft
Period: 3.6 sec
Direction: WNW
"""


@pytest.fixture(autouse=True)
def quiet_buoy(monkeypatch):
    """No test reaches NDBC: the buoy reads the page above unless a test says otherwise."""
    def buoy(task):
        task.raise_if_cancelled()
        return sources.parse_buoy(BUOY_PAGE)
    monkeypatch.setattr(sources, "fetch_buoy", buoy)


BART_PAYLOAD = {"root": {"station": [{"name": "Warm Springs/South Fremont", "abbr": "WARM", "etd": [
    {"destination": "Berryessa", "abbreviation": "BERY", "estimate": [
        {"minutes": "13", "direction": "South", "color": "ORANGE", "hexcolor": "#ff9933", "cancelflag": "0"},
        {"minutes": "33", "direction": "South", "hexcolor": "#ff9933", "cancelflag": "0"}]},
    {"destination": "Richmond", "abbreviation": "RICH", "estimate": [
        {"minutes": "Leaving", "direction": "North", "color": "ORANGE", "hexcolor": "#ff9933", "cancelflag": "0"},
        {"minutes": "28", "direction": "North", "hexcolor": "#ff9933", "cancelflag": "1"}]},
    {"destination": "Daly City", "abbreviation": "DALY", "estimate": [
        {"minutes": "5", "direction": "North", "color": "GREEN", "hexcolor": "#339933", "cancelflag": "0"}]}]}]}}


@pytest.fixture(autouse=True)
def quiet_bart(monkeypatch):
    """No test reaches BART: the network reads the departures above unless a test says otherwise."""
    def bart(now, task):
        task.raise_if_cancelled()
        return sources.parse_bart_network(BART_PAYLOAD, now)
    monkeypatch.setattr(sources, "fetch_bart_network", bart)


BSA_DELAY = {"root": {"bsa": [{"@id": "560", "station": "BART", "type": "DELAY", "description": {
    "#cdata-section": "Expect 30-minute delays starting at 9pm each night on Sunday-Thursday for riders traveling "
                      "between Millbrae, SFO, and Daly City stations due to planned train control modernization "
                      "construction work. "},
    "sms_text": {"#cdata-section": "Expect 30 min delays starting at 9pm each night between MLBR, SFO, and DALY."},
    "posted": "Mon Nov 02 2026 06:00 AM PST", "expires": "No time provided."}], "message": ""}}
BSA_QUIET = {"root": {"bsa": [{"station": "", "description": {"#cdata-section": "No delays reported."},
                               "sms_text": {"#cdata-section": "No delays reported."}}]}}
ELEVATORS_FINE = {"root": {"bsa": [{"station": "ALL", "description": {
    "#cdata-section": "Attention passengers: All elevators are in service. Thank You."}}]}}
ELEVATORS_OUT = {"root": {"bsa": [{"station": "BART", "description": {
    "#cdata-section": "There are 2 elevators out of service at this time: Civic Center Elevator, "
                      "and Lake Merritt Elevator. Thank you."}}]}}
TRAIN_COUNT = {"root": {"traincount": "56", "message": ""}}


def cms_entry(index, route, direction, lat, lon, lines, *, place="Union City", clock="22:24:39", in_service="true",
              phase2=("", "", "")):
    return {"cms": {"index": str(index), "recordTimestamp": {"recordDate": "2026-09-29", "recordTime": "23:31:00"},
                    "location": {"route": route, "direction": direction, "nearbyPlace": place,
                                 "latitude": str(lat), "longitude": str(lon)},
                    "inService": in_service,
                    "message": {"messageTimestamp": {"messageDate": "2026-09-29", "messageTime": clock},
                                "display": "1 Page (Normal)",
                                "phase1": {f"phase1Line{n}": line for n, line in enumerate(lines, 1)},
                                "phase2": {f"phase2Line{n}": line for n, line in enumerate(phase2, 1)}}}}


CMS_PAYLOAD = {"data": [
    cms_entry(1, "I-880", "South", 37.594, -122.05, ("MILPITAS  17 MIN", "SJ ARPT   25 MIN", "MENLO PRK 13 MIN")),
    cms_entry(2, "I-680", "North", 37.573, -121.886, ("DUBLIN    14 MIN", "WALNUT CR 32 MIN", "ANTIOCH   66 MIN"),
              place="Sunol", clock="22:26:10"),
    cms_entry(3, "I-880", "North", 37.65, -122.1, ("S101 FULL CLSURE", "AT SF JCT 80/101", "NOV 6- NOV 9")),
    cms_entry(4, "US-101", "South", 37.44, -122.14, ("SJ ARPT   22 MIN", "MILPITAS  27 MIN", "LOS GATOS 26 MIN")),
    cms_entry(5, "SR-84", "East", 37.49, -122.14, ("", "", ""), phase2=("FREMONT   13 MIN", "HAYWARD   28 MIN",
                                                                         "MILPITAS  23 MIN")),
    cms_entry(6, "I-680", "South", 37.45, -121.9, ("Not Reported",) * 3, in_service="false"),
]}


@pytest.fixture(autouse=True)
def quiet_extras(monkeypatch):
    """No test reaches BART's advisories, Caltrans' signs or NOAA's observed level: they read the
    samples above (advisory, elevators fine, 56 trains; the signs; the water 1.1 ft above the
    prediction) unless a test says otherwise."""
    def status(now, task):
        task.raise_if_cancelled()
        return {"time": now.isoformat(), "advisories": sources.parse_bart_advisories(BSA_DELAY),
                "elevators": sources.parse_bart_elevators(ELEVATORS_FINE), "trains": 56}

    def signs(routes, task):
        task.raise_if_cancelled()
        return sources.parse_signs(CMS_PAYLOAD, routes)

    def water(station, task):
        task.raise_if_cancelled()
        stamp = NOW - timedelta(minutes=6)
        return {"time": stamp.astimezone(timezone.utc).isoformat(), "height": 4.6, "quality": "p"}

    monkeypatch.setattr(sources, "fetch_bart_status", status)
    monkeypatch.setattr(sources, "fetch_signs", signs)
    monkeypatch.setattr(sources, "fetch_water_level", water)


FREMONT = {"name": "Fremont", "abbr": "FRMT", "etd": [
    {"destination": "Richmond", "estimate": [
        {"minutes": "4", "platform": "2", "length": "6", "color": "ORANGE", "hexcolor": "#ff9933", "delay": "143",
         "cancelflag": "0"},
        {"minutes": "19", "platform": "2", "length": "5", "color": "ORANGE", "hexcolor": "#ff9933", "delay": "0",
         "cancelflag": "0"}]}]}
NETWORK_PAYLOAD = {"root": {"station": [BART_PAYLOAD["root"]["station"][0], FREMONT]}}


def test_the_network_reading_keeps_each_trains_delay_and_the_next_trains_cars():
    network = sources.parse_bart_network(NETWORK_PAYLOAD, NOW)

    assert sources.valid_bart_network(network) and [station["code"] for station in network["stations"]] == ["WARM", "FRMT"]
    richmond = sources.pick_bart_station(network, "FRMT")["lines"][0]
    assert richmond["delays"] == [143, 0] and richmond["cars"] == 6 and richmond["platform"] == "2"
    # Once the late train has gone its delay goes with it.
    later = sources.coming_trains({"lines": [richmond]}, NOW + timedelta(minutes=10))
    assert later[0]["delays"] == [0] and len(later[0]["departures"]) == 1
    # Drawn at random among the stations with trains, or the one named.
    assert sources.pick_bart_station(network, None, choose=lambda stations: stations[-1])["code"] == "FRMT"
    assert sources.pick_bart_station(network, "WARM", choose=lambda stations: stations[-1])["code"] == "WARM"
    assert sources.pick_bart_station({"stations": []}) is None


def test_the_page_draws_a_bart_station_at_random_unless_one_is_fixed(plugin, monkeypatch):
    mock_sources(monkeypatch, [])
    monkeypatch.setattr(sources, "fetch_bart_network", lambda now, task: sources.parse_bart_network(NETWORK_PAYLOAD, now))
    monkeypatch.setattr(sources.random, "choice", lambda stations: stations[-1])

    drawn = plugin._payload(SETTINGS, NOW, allow_network=True, context=context())
    fixed = plugin._payload({**SETTINGS, "bartMode": "fixed", "bartStation": "warm"}, NOW, allow_network=False,
                            context=context())

    assert drawn["bart"]["data"]["code"] == "FRMT" and fixed["bart"]["data"]["code"] == "WARM"
    assert drawn["station"] == "9414523"


def test_bart_departures_become_clock_times_and_drop_cancelled_and_departed_trains():
    data = sources.parse_bart(BART_PAYLOAD, NOW)

    assert data["station"] == "Warm Springs/South Fremont" and sources.valid_bart(data)
    assert [len(line["departures"]) for line in data["lines"]] == [2, 1, 1]  # the cancelled train is gone
    assert [line["line"] for line in data["lines"]] == ["orange", "orange", "green"]
    from plugins.bay_commute.map_view import source_logo
    assert all(source_logo(f"bart-{name}", 12) is not None for name in sources.BART_LINES)
    lines = sources.coming_trains(data, NOW + timedelta(minutes=6))
    assert [(line["destination"], [stamp.strftime("%H:%M") for stamp in line["departures"]]) for line in lines] == [
        ("Berryessa", ["22:43", "23:03"])]
    assert sources.bart_station_setting("frmt") == "FRMT" and sources.bart_station_setting("../x") == "WARM"
    with pytest.raises(ValueError):
        sources.parse_bart({"root": {"station": []}}, NOW)


def test_buoy_page_gives_waves_period_swell_and_water_temperature():
    data = sources.parse_buoy(BUOY_PAGE)

    assert data == {"time": "2026-10-25T15:00:00+00:00", "wave_ft": 3.0, "period_s": 8.3, "swell_dir": "NW",
                    "water_f": 64.0, "wind_kt": 3.9, "wind_dir": "NE"}
    # A significant wave height and dominant period win over the swell's when reported.
    measured = sources.parse_buoy(BUOY_PAGE.replace("Wave Summary", "Wave Height: 4.6 ft\nDominant Wave Period: 11 sec"))
    assert (measured["wave_ft"], measured["period_s"]) == (4.6, 11.0)
    assert sources.valid_buoy(data) and not sources.valid_buoy({"time": data["time"]})
    with pytest.raises(ValueError):
        sources.parse_buoy("Station 46026\nno observation")


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("BAY_COMMUTE_CACHE_DIR", str(tmp_path / "bay-cache"))
    return BayCommute({"id": "bay_commute"})


def mock_sources(monkeypatch, calls):
    def roads(routes, now, task, **kwargs):
        task.raise_if_cancelled()
        calls.append("roads")
        return road_data(now)
    def tides(station, now, task):
        task.raise_if_cancelled()
        calls.append("tides")
        return tide_data(now)
    monkeypatch.setattr(sources, "fetch_roads", roads)
    monkeypatch.setattr(sources, "fetch_tides", tides)


def test_csv_filters_finished_cancelled_dates_and_preserves_current_vs_planned():
    rows = [csv_row(), csv_row("I-680", index="plan", isCode1097="false"),
            csv_row("SR-84", index="cancelled", isCode1022="true"),
            csv_row(index="finished", isCode1098="true"),
            csv_row(index="expired", closureEndDate="2026-09-29", closureEndTime="21:00:00"),
            csv_row("SR-1", index="other")]
    result = sources.parse_roads_csv(csv_text(rows), sources.DEFAULT_ROUTES, NOW)
    assert [row["id"] for row in result["items"]] == ["closure-1", "plan"]
    assert sources.road_state(result["items"][0], NOW) == "active"
    assert sources.road_state(result["items"][1], NOW) == "scheduled_now"
    assert sources.parse_time(result["source_updated_at"]) == NOW


def test_valid_empty_filter_is_success_but_malformed_selected_row_is_failure():
    assert sources.parse_roads_csv(csv_text([csv_row("SR-1")]), ("I-880",), NOW)["items"] == []
    with pytest.raises(ValueError, match="schema"):
        sources.parse_roads_csv("<html>maintenance</html>", sources.DEFAULT_ROUTES, NOW)
    with pytest.raises(ValueError, match="invalid dates"):
        sources.parse_roads_csv(csv_text([csv_row(closureStartDate="bad")]), sources.DEFAULT_ROUTES, NOW)


def test_epoch_times_override_ambiguous_local_strings():
    row = csv_row(closureStartDate="invalid", closureStartEpoch=str(NOW.timestamp()-3600))
    result = sources.parse_roads_csv(csv_text([row]), sources.DEFAULT_ROUTES, NOW)
    assert sources.parse_time(result["items"][0]["start"]) == NOW-timedelta(hours=1)


def test_indefinite_official_sentinel_is_not_a_real_end_time():
    raw = csv_row(isClosureEndIndefinite="true", closureEndDate="2999-12-31",
                  closureEndTime="23:59:00", closureEndEpoch="32503708740")
    row = sources.parse_roads_csv(csv_text([raw]), sources.DEFAULT_ROUTES, NOW)["items"][0]
    assert row["end"] is None
    assert row["indefinite"] is True
    assert sources.road_state(row, NOW+timedelta(days=10)) == "active"


def test_caltrans_coordinates_preserved_as_real_numeric_positions():
    raw = csv_row(beginLatitude="37.589684", beginLongitude="-121.957626",
                  endLatitude="37.594945", endLongitude="-121.930611")
    row = sources.parse_roads_csv(csv_text([raw]), sources.DEFAULT_ROUTES, NOW)["items"][0]
    assert (row["begin_lat"],row["begin_lon"]) == (37.589684,-121.957626)
    assert (row["end_lat"],row["end_lon"]) == (37.594945,-121.930611)


@pytest.mark.parametrize("lat,lon", [("nan","-121.9"),("37.5","inf"),("91","-121.9"),
                                     ("37.5","-181"),("0","0"),("","-121.9"),
                                     ("Not Reported","Not Reported"),("-121.9","37.5")])
def test_invalid_coordinates_do_not_invent_a_map_position_or_hide_closure(lat,lon):
    raw = csv_row(beginLatitude=lat,beginLongitude=lon,endLatitude="37.6",endLongitude="-122.0")
    rows = sources.parse_roads_csv(csv_text([raw]),sources.DEFAULT_ROUTES,NOW)["items"]
    assert len(rows) == 1
    assert rows[0]["begin_lat"] is rows[0]["begin_lon"] is None
    assert (rows[0]["end_lat"],rows[0]["end_lon"]) == (37.6,-122.0)


def test_cache_schema_invalidates_pre_coordinate_payload(plugin,monkeypatch):
    mock_sources(monkeypatch,[])
    plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    identity={"routes":list(sources.DEFAULT_ROUTES),"window_days":7}
    cache_path=plugin._cache_dir()/plugin._cache_key("roads",identity)
    payload=json.loads(cache_path.read_text(encoding="utf-8"))
    payload["schema"]=1
    cache_path.write_text(json.dumps(payload),encoding="utf-8")
    assert plugin._read_cache("roads",identity) is None


def test_window_and_route_selection_keep_high_impact_current_closures_first():
    rows = [csv_row(index="lane"), csv_row("I-680", index="full", typeOfClosure="Full"),
            csv_row("SR-84", index="future", closureStartDate="2026-10-02", closureEndDate="2026-10-03"),
            csv_row(index="another", typeOfClosure="Full")]
    parsed = sources.parse_roads_csv(csv_text(rows), sources.DEFAULT_ROUTES, NOW)
    chosen = sources.select_roads(parsed["items"], NOW)
    assert chosen[0]["kind"] == "Full"
    assert {row["route"] for row in chosen} == set(sources.DEFAULT_ROUTES)
    limited = sources.parse_roads_csv(csv_text(rows), sources.DEFAULT_ROUTES, NOW, window_days=1)
    assert "future" not in {row["id"] for row in limited["items"]}


@pytest.mark.parametrize("value,expected", [(None,sources.DEFAULT_ROUTES), ("880,680,84",sources.DEFAULT_ROUTES),
                                           ("I880 SR84 I880",("I-880","SR-84"))])
def test_route_settings(value, expected):
    assert sources.routes_setting(value) == expected


@pytest.mark.parametrize("value", ["../../file", "I-880<svg>", "not a road"])
def test_invalid_routes_rejected(value):
    with pytest.raises(ValueError):
        sources.routes_setting(value)


class FakeClient:
    def __init__(self, now):
        self.now, self.calls = now, []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        params = kwargs["params"]
        data = tide_data(self.now)
        key = "extremes" if params["interval"] == "hilo" else "curve"
        rows = [{"t": sources.parse_time(row["time"]).strftime("%Y-%m-%d %H:%M"),
                 "v": str(row["height"]), **({"type":row["type"]} if key == "extremes" else {})}
                for row in data[key]]
        return SimpleNamespace(data={"predictions":rows})


@pytest.mark.parametrize("now,points", [(NOW,192),
    (datetime(2026,10,31,20,tzinfo=sources.PACIFIC),196),
    (datetime(2026,3,7,20,tzinfo=sources.PACIFIC),188)])
def test_tides_request_local_two_day_range_via_gmt_and_validate_dst(now, points, monkeypatch):
    client = FakeClient(now)
    monkeypatch.setattr(sources, "get_http_client", lambda: client)
    task = context()
    result = sources.fetch_tides("9414523",now,task)
    assert len(result["curve"]) == points
    assert result["prediction_date"] == now.date().isoformat()
    assert len(client.calls) == 2
    for _,_,kwargs in client.calls:
        assert kwargs["context"] is task
        assert kwargs["max_bytes"] == 128*1024
        assert kwargs["params"]["time_zone"] == "gmt"
        assert kwargs["params"]["datum"] == "MLLW"


def test_noaa_rejects_empty_errors_partial_curve_and_nonfinite_values():
    begin = NOW.replace(hour=0,minute=0)
    end = begin+timedelta(days=2)
    for payload in ({"error":{"message":"no data"}}, {"predictions":[]},
                    {"predictions":[{"t":"2026-09-29 07:00","v":"nan"}]},
                    {"predictions":[{"t":"2026-09-29 07:00","v":"1.1"}]}):
        with pytest.raises(ValueError):
            sources.normalize_tides(payload,interval="15",begin=begin,end=end)


def test_independent_hourly_daily_cache_and_next_event_recomputed(plugin, monkeypatch):
    calls=[]
    mock_sources(monkeypatch,calls)
    first=plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    assert first["provenance"] == SourceProvenance.LIVE
    second=plugin._payload(SETTINGS,NOW+timedelta(minutes=30),allow_network=True,context=context())
    assert calls == ["roads","tides"]
    assert second["provenance"] == SourceProvenance.FRESH_CACHE
    plugin._payload(SETTINGS,NOW+timedelta(hours=1),allow_network=True,context=context())
    assert calls == ["roads","tides","roads"]
    plugin._payload(SETTINGS,NOW+timedelta(hours=2),allow_network=True,context=context())
    assert calls == ["roads","tides","roads","roads","tides"]
    before=sources.next_tides(first["tides"]["data"],NOW)["L"]["time"]
    after=sources.next_tides(first["tides"]["data"],NOW+timedelta(hours=1))["L"]["time"]
    assert before != after


def test_force_road_refresh_reuses_same_day_tides(plugin,monkeypatch):
    calls=[]
    mock_sources(monkeypatch,calls)
    plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    plugin._payload({**SETTINGS,"forceRefresh":True},NOW+timedelta(minutes=5),allow_network=True,context=context())
    assert calls == ["roads","tides","roads"]


def test_failure_retains_original_fetch_time_and_marks_stale_independently(plugin,monkeypatch):
    calls=[]
    mock_sources(monkeypatch,calls)
    first=plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    def fail(*args,**kwargs):
        raise OSError("offline")
    monkeypatch.setattr(sources,"fetch_roads",fail)
    result=plugin._payload(SETTINGS,NOW+timedelta(hours=1),allow_network=True,context=context())
    assert result["roads"]["state"] == "stale_cache"
    assert result["roads"]["fetched_at"] == first["roads"]["fetched_at"]
    assert result["tides"]["state"] == "fresh_cache"
    assert result["provenance"] == SourceProvenance.STALE_CACHE


def test_no_cache_failure_is_truthfully_unavailable_while_other_provider_succeeds(plugin,monkeypatch):
    mock_sources(monkeypatch,[])
    monkeypatch.setattr(sources,"fetch_roads",lambda *a,**k: (_ for _ in ()).throw(OSError("offline")))
    payload=plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    assert payload["roads"]["state"] == "unavailable"
    assert payload["tides"]["state"] == "live"
    assert payload["provenance"] == SourceProvenance.LOCAL_FALLBACK


def test_cache_only_makes_no_network_or_filesystem_mutations(plugin,monkeypatch):
    calls=[]
    mock_sources(monkeypatch,calls)
    empty=plugin._payload(SETTINGS,NOW,allow_network=False,context=context())
    assert not plugin._cache_dir().exists()
    assert empty["roads"]["state"] == "unavailable"
    plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    snapshot={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in plugin._cache_dir().iterdir()}
    plugin._payload({**SETTINGS,"forceRefresh":True},NOW+timedelta(minutes=1),allow_network=False,context=context())
    assert calls == ["roads","tides"]
    assert snapshot == {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in plugin._cache_dir().iterdir()}


def test_cancellation_propagates_and_never_publishes_a_fallback_cache(plugin,monkeypatch):
    calls=[]
    mock_sources(monkeypatch,calls)
    task=context()
    task.cancel_event.set()
    with pytest.raises(TaskCancelled):
        plugin._payload(SETTINGS,NOW,allow_network=True,context=task)
    assert calls == []
    assert not plugin._cache_dir().exists()


def test_old_source_snapshot_is_stale_even_after_http_success(plugin,monkeypatch):
    mock_sources(monkeypatch,[])
    old=road_data()
    old["source_updated_at"]=(NOW-timedelta(hours=4)).isoformat()
    monkeypatch.setattr(sources,"fetch_roads",lambda *a,**k:old)
    payload=plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    assert payload["roads"]["state"] == "stale_cache"
    assert payload["provenance"] == SourceProvenance.STALE_CACHE


def test_changed_route_settings_never_show_another_route_cache(plugin,monkeypatch):
    mock_sources(monkeypatch,[])
    plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    payload=plugin._payload({**SETTINGS,"routes":"I-80"},NOW,allow_network=False,context=context())
    assert payload["roads"]["state"] == "unavailable"
    assert payload["tides"]["state"] == "fresh_cache"


def test_empty_road_filter_attests_success_and_avoids_fake_closures(plugin,monkeypatch):
    mock_sources(monkeypatch,[])
    monkeypatch.setattr(sources,"fetch_roads",lambda *a,**k:{"items":[],"source_updated_at":NOW.isoformat()})
    payload=plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    assert payload["roads"]["state"] == "live"
    assert payload["provenance"] == SourceProvenance.LIVE
    recorded=[]
    original=Canvas.text
    def capture(self,xy,text,*args,**kwargs):
        recorded.append(str(text))
        return original(self,xy,text,*args,**kwargs)
    monkeypatch.setattr(Canvas,"text",capture)
    image=render_page((800,480),payload,{},NOW)
    assert image.size == (800,480)
    assert "所选路线暂无封闭记录" in recorded


def test_pillow_day_night_render_and_cache_display_provenance(plugin,monkeypatch):
    mock_sources(monkeypatch,[])
    monkeypatch.setattr(sources,"local_now",lambda now=None:now or NOW)
    device=SimpleNamespace(get_resolution=lambda:(800,480),
                           get_config=lambda key=None,default=None:({"orientation":"horizontal","timezone":"America/Los_Angeles"}.get(key,default)))
    payload=plugin._payload(SETTINGS,NOW,allow_network=True,context=context())
    day=render_page((800,480),payload,{"_inkypi_theme":{"mode":"day"}},NOW)
    night=render_page((800,480),payload,{"_inkypi_theme":{"mode":"night"}},NOW)
    assert day.getpixel((0,0)) != night.getpixel((0,0))
    assert day.mode == night.mode == "RGB"
    result=plugin.generate_image({**SETTINGS,"_theme_render_only":True,"_inkypi_theme":{"mode":"day"}},device)
    assert read_source_provenance(result) == SourceProvenance.FRESH_CACHE


def test_buoy_is_drawn_beside_the_tides_and_never_decides_provenance(plugin, monkeypatch):
    mock_sources(monkeypatch, [])
    payload = plugin._payload(SETTINGS, NOW, allow_network=True, context=context())
    assert payload["buoy"]["state"] == "live" and payload["provenance"] == SourceProvenance.LIVE
    with_buoy = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW)
    without = render_page((800, 480), {**payload, "buoy": {"state": "unavailable", "data": {}}},
                          {"_inkypi_theme": {"mode": "day"}}, NOW)
    from plugins.bay_commute.command_render import BUOY_Y
    row = (510, BUOY_Y - 4, 790, BUOY_Y + 12)
    assert with_buoy.crop(row).tobytes() != without.crop(row).tobytes()
    assert with_buoy.crop((0, 100, 460, 480)).tobytes() == without.crop((0, 100, 460, 480)).tobytes()

    monkeypatch.setattr(sources, "fetch_buoy", lambda task: (_ for _ in ()).throw(OSError("offline")))
    later = plugin._payload({**SETTINGS, "forceRefresh": True}, NOW + timedelta(hours=7), allow_network=True,
                            context=context())
    assert later["buoy"]["state"] == "unavailable"
    assert later["provenance"] != SourceProvenance.LOCAL_FALLBACK


def test_the_tide_readouts_are_set_in_space_grotesk_with_inter_standing_in(monkeypatch, tmp_path):
    from plugins.bay_commute import command_render

    assert command_render.font(32, "display").getname()[0] == "Space Grotesk"
    monkeypatch.setattr(command_render, "DISPLAY_FACE", (tmp_path / "missing.ttf", None))
    command_render.font.cache_clear()
    try:
        assert command_render.font(32, "display").getname()[0] == command_render.font(32, "number").getname()[0]
    finally:
        command_render.font.cache_clear()


def test_bart_departures_sit_under_the_buoy_and_never_decide_provenance(plugin, monkeypatch):
    mock_sources(monkeypatch, [])
    payload = plugin._payload(SETTINGS, NOW, allow_network=True, context=context())
    assert payload["bart"]["state"] == "live" and payload["provenance"] == SourceProvenance.LIVE
    recorded = []
    original = Canvas.text

    def capture(self, xy, text, *args, **kwargs):
        recorded.append(str(text))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(Canvas, "text", capture)
    with_bart = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW)
    assert {"Warm Springs", "Daly City", "Berryessa", "22:35"} <= set(recorded)
    without = render_page((800, 480), {**payload, "bart": {"state": "unavailable", "data": {}}},
                          {"_inkypi_theme": {"mode": "day"}}, NOW)
    assert with_bart.crop((510, 395, 790, 444)).tobytes() != without.crop((510, 395, 790, 444)).tobytes()
    assert with_bart.crop((0, 100, 460, 480)).tobytes() == without.crop((0, 100, 460, 480)).tobytes()


def test_bart_rows_lead_from_destination_to_times_along_the_line_level_with_the_bullet(monkeypatch):
    payload = {"roads": {"state": "live", "data": {"items": []}, "fetched_at": NOW.isoformat()},
               "tides": {"state": "live", "data": tide_data(NOW), "fetched_at": NOW.isoformat()},
               "buoy": {"state": "live", "data": sources.parse_buoy(BUOY_PAGE)},
               "bart": {"state": "live", "data": sources.parse_bart(BART_PAYLOAD, NOW)},
               "routes": [], "station": "9414523", "window_days": 7, "nearby_cameras": {}}
    rows = {}
    original = Canvas.text

    def capture(self, xy, text, *args, **kwargs):
        width = original(self, xy, text, *args, **kwargs)
        rows[str(text)] = (round(xy[0]), round(xy[1]), width)
        return width

    monkeypatch.setattr(Canvas, "text", capture)
    image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW)
    x, y, width = rows["Daly City"]
    green = (0x33, 0x99, 0x33)
    # The leader runs in the line's green from after the name, level with the capitals' middle.
    leader = [row for row in range(y - 4, y + 14) if image.getpixel((x + round(width) + 20, row)) == green]
    assert leader and min(leader) <= y + 4 <= max(leader)
    # The lettered bullet is centred on that same middle.
    bullet = [row for row in range(y - 6, y + 16)
              if any(image.getpixel((column, row)) != image.getpixel((505, row)) for column in range(512, 524))]
    assert abs((min(bullet) + max(bullet)) / 2 - (y + 4)) <= 1
    # The station's stop ring before its name shares its outline between the lines calling there.
    name_x, name_y, _width = rows["Warm Springs"]
    ring = image.crop((name_x - 16, name_y - 4, name_x - 2, name_y + 13)).get_flattened_data()

    def near(colour):
        return any(sum(abs(a - b) for a, b in zip(pixel, colour)) < 30 for pixel in ring)

    assert near((0xff, 0x99, 0x33)) and near(green)


def test_bart_rows_give_the_next_trains_cars_and_mark_late_trains_red(monkeypatch):
    fremont = sources.pick_bart_station(sources.parse_bart_network(NETWORK_PAYLOAD, NOW), "FRMT")
    payload = {"roads": {"state": "live", "data": {"items": []}, "fetched_at": NOW.isoformat()},
               "tides": {"state": "live", "data": tide_data(NOW), "fetched_at": NOW.isoformat()},
               "buoy": {"state": "live", "data": sources.parse_buoy(BUOY_PAGE)},
               "bart": {"state": "live", "data": fremont},
               "routes": [], "station": "9414523", "window_days": 7, "nearby_cameras": {}}
    recorded = []
    original = Canvas.text

    def capture(self, xy, text, size=13, color="ink", *args, **kwargs):
        recorded.append((str(text), color))
        return original(self, xy, text, size, color, *args, **kwargs)

    monkeypatch.setattr(Canvas, "text", capture)
    render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW)
    late, on_time = [(NOW + timedelta(minutes=wait)).astimezone(sources.PACIFIC).strftime("%H:%M") for wait in (4, 19)]
    assert {("1 班晚点", "red"), ("6节", "muted"), (late, "red"), (on_time, "blue")} <= set(recorded)


def test_bart_keeps_each_trains_length_and_line_and_the_row_follows_the_next_train_to_come():
    richmond = sources.pick_bart_station(sources.parse_bart_network(NETWORK_PAYLOAD, NOW), "FRMT")["lines"][0]
    assert richmond["lengths"] == [6, 5] and richmond["cars"] == 6 and richmond["train_lines"] == ["orange", "orange"]
    # Once the six-car train has gone, the row's train is the five-car one after it.
    later = sources.coming_trains({"lines": [richmond]}, NOW + timedelta(minutes=10))[0]
    assert later["cars"] == 5 and later["lengths"] == [5] and later["next_line"] == "orange"
    # A reading kept from before lengths and lines were kept still gives its first train's cars.
    kept = {key: value for key, value in richmond.items() if key not in ("lengths", "train_lines")}
    kept_later = sources.coming_trains({"lines": [kept]}, NOW + timedelta(minutes=10))[0]
    assert kept_later["cars"] == 6 and "next_line" not in kept_later
    # Trains BART gives no length read as None, and two leaving together never compare lengths.
    assert [line["lengths"] for line in sources.parse_bart(BART_PAYLOAD, NOW)["lines"]] == [[None, None], [None], [None]]
    together = {"root": {"station": [{"name": "Fremont", "abbr": "FRMT", "etd": [{"destination": "Richmond", "estimate": [
        {"minutes": "4", "length": "6", "hexcolor": "#ff9933"}, {"minutes": "4", "hexcolor": "#ff9933"}]}]}]}}
    assert sources.parse_bart(together, NOW)["lines"][0]["lengths"] == [6, None]
    # A destination two lines serve: each train keeps its own line, the row the next one's.
    mixed = sources.parse_bart({"root": {"station": [{"name": "12th St. Oakland City Center", "etd": [
        {"destination": "Richmond", "estimate": [{"minutes": "2", "length": "5", "color": "ORANGE"},
                                                 {"minutes": "9", "length": "6", "color": "RED"}]}]}]}}, NOW)
    assert mixed["lines"][0]["line"] == "orange" and mixed["lines"][0]["train_lines"] == ["orange", "red"]
    next_red = sources.coming_trains(mixed, NOW + timedelta(minutes=4))[0]
    assert next_red["next_line"] == "red" and next_red["cars"] == 6


def bart_rows_page(monkeypatch, etds, mode="day", at=NOW, status=None):
    """The page for one station whose destinations are ``etds`` (BART's own shape) read at NOW and
    drawn ``at``, with where each text went: {text: (x, y, width)}."""
    reading = {"root": {"station": [{"name": "Fremont", "abbr": "FRMT", "etd": etds}]}}
    payload = {"roads": {"state": "live", "data": {"items": []}, "fetched_at": NOW.isoformat()},
               "tides": {"state": "live", "data": tide_data(NOW), "fetched_at": NOW.isoformat()},
               "buoy": {"state": "live", "data": sources.parse_buoy(BUOY_PAGE)},
               "bart": {"state": "live", "data": sources.parse_bart(reading, NOW)},
               "bart_status": status or {"state": "unavailable"},
               "routes": [], "station": "9414523", "window_days": 7, "nearby_cameras": {}}
    rows = {}
    original = Canvas.text

    def capture(self, xy, text, *args, **kwargs):
        width = original(self, xy, text, *args, **kwargs)
        rows[str(text)] = (round(xy[0]), round(xy[1]), width)
        return width

    monkeypatch.setattr(Canvas, "text", capture)
    return render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, at), rows


def bart_etd(destination, line, hexcolor, minutes, length):
    return {"destination": destination, "estimate": [
        {"minutes": str(wait), "length": str(length), "color": line, "hexcolor": hexcolor, "cancelflag": "0"}
        for wait in minutes]}


def stripe_runs(image, row, colour, left=529, right=784):
    """The runs of ``colour`` along ``row`` between ``left`` and ``right``, as (first x, last x)."""
    runs = []
    for x in range(left, right):
        if image.getpixel((x, row)) == colour:
            if runs and runs[-1][1] == x - 1:
                runs[-1] = (runs[-1][0], x)
            else:
                runs.append((x, x))
    return runs


def stripe_rows(image, x, colour, top, bottom):
    return [y for y in range(top, bottom) if image.getpixel((x, y)) == colour]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_bart_rows_draw_the_next_train_as_long_as_it_runs_on_one_scale(monkeypatch, mode):
    from PIL import ImageColor

    from plugins.bay_commute.command_render import BART_LINE_COLOURS, NIGHT

    image, rows = bart_rows_page(monkeypatch, [
        bart_etd("Millbrae", "RED", "#ff0000", (4, 25, 45), 5),
        bart_etd("SFO/Millbrae", "YELLOW", "#ffff33", (9, 29, 49), 10),
        bart_etd("Dublin/Pleasanton", "BLUE", "#0099cc", (14, 34), 3)], mode)
    # Ten cars beside three times take the room the whole name would; three cars leave it.
    assert "SFO" in rows and "SFO/Millbrae" not in rows and "Dublin/Pleasanton" in rows
    assert {"5节", "10节", "3节"} <= set(rows)
    ink = (0, 0, 0) if mode == "day" else ImageColor.getrgb(NIGHT["ink"])
    trains = {}
    for name, line, cars in (("Millbrae", "red", 5), ("SFO", "yellow", 10), ("Dublin/Pleasanton", "blue", 3)):
        _x, y, _width = rows[name]
        colour = ImageColor.getrgb(BART_LINE_COLOURS[line])
        # One run of the line's own colour (BART's, not the feed's) per car along its stripe.
        runs = stripe_runs(image, y + 5, colour)
        assert len(runs) == cars
        first_time = min(x for text, (x, row_y, _w) in rows.items() if row_y == y and ":" in text)
        assert runs[-1][1] < first_time - 3
        # The stripe at least 4 px deep, and under the train a 2 px rail from past its nose back to
        # the cars label.
        assert len(stripe_rows(image, runs[0][0] + 2, colour, y - 6, y + 16)) >= 4
        rail = [row for row in range(y, y + 16) if image.getpixel((runs[-1][1] + 1, row)) == ink]
        assert len(rail) == 2 and rail[1] == rail[0] + 1
        label_x, _label_y, label_w = rows[f"{cars}节"]
        assert all(image.getpixel((label_x + round(label_w) + 6, row)) == ink for row in rail)
        trains[cars] = runs
    # One scale for the page: every car as wide (7 to 12 px), the same pitch, the same nose.
    widths = {run[1] - run[0] + 1 for runs in trains.values() for run in runs[:-1]}
    pitches = {later[0] - earlier[0] for runs in trains.values() for earlier, later in zip(runs, runs[1:])}
    noses = {runs[-1][1] - runs[-1][0] + 1 for runs in trains.values()}
    assert len(widths) == 1 and 7 <= min(widths) <= 12 and len(pitches) == 1 and len(noses) == 1
    span = {cars: runs[-1][1] - runs[0][0] + 1 for cars, runs in trains.items()}
    assert span[3] < span[5] < span[10]


def test_bart_trains_take_the_widest_cars_the_tightest_row_allows(monkeypatch):
    from plugins.bay_commute.command_render import car_rows, row_plan, train_shape_for

    # Two trains with room to spare: the widest cars, the tallest body.
    image, rows = bart_rows_page(monkeypatch, [bart_etd("Millbrae", "RED", "#ff0000", (4, 25, 45), 5),
                                               bart_etd("Berryessa", "ORANGE", "#ff9933", (17, 37), 6)])
    _x, y, _width = rows["Millbrae"]
    runs = stripe_runs(image, y + 5, (0xed, 0x1c, 0x24))
    assert len(runs) == 5 and runs[0][1] - runs[0][0] + 1 == 12
    # Under an advisory and elevator notes the rows close up, and the body with them, rail and all
    # clear of the next row; its stripe keeps 4 px.
    busy = {"state": "live", "data": {"time": NOW.isoformat(), "advisories": sources.parse_bart_advisories(BSA_DELAY),
                                      "elevators": sources.parse_bart_elevators(ELEVATORS_OUT), "trains": 56}}
    image, rows = bart_rows_page(monkeypatch, [
        bart_etd("Millbrae", "RED", "#ff0000", (4, 25, 45), 5),
        bart_etd("Antioch", "YELLOW", "#ffff33", (9, 29, 49), 9),
        bart_etd("Berryessa", "ORANGE", "#ff9933", (17, 37), 6)], status=busy)
    first, second = rows["Millbrae"][1], rows["Antioch"][1]
    assert second - first <= 16
    runs = stripe_runs(image, first + 5, (0xed, 0x1c, 0x24))
    assert len(runs) == 5 and len(stripe_rows(image, runs[0][0] + 2, (0xed, 0x1c, 0x24), first - 6, second)) >= 4
    page = Canvas({"_inkypi_theme": {"mode": "day"}})
    lines = sources.coming_trains(sources.parse_bart({"root": {"station": [{"name": "Fremont", "etd": [
        bart_etd("Antioch", "YELLOW", "#ffff33", (9, 29, 49), 9)]}]}}, NOW), NOW)
    shape = train_shape_for([row_plan(page, line) for line in lines], 15.3)
    assert len(shape[2]) + 3 <= 15 - 2
    # Every body height keeps its parts: the stripe 4 px from 10 px tall, the ink 2 px from 12.
    for height in range(8, 15):
        parts = car_rows(height)
        assert len(parts) == height and parts[0] == "roof" and parts[-1] == "frame"
        assert parts.count("stripe") >= (4 if height >= 10 else 3)
        if height >= 12:
            assert min(parts.count(part) for part in ("roof", "window", "belt", "frame")) >= 2


def test_bart_trains_stripe_follows_the_line_the_next_train_runs_on(monkeypatch):
    # A destination two lines serve: an orange train first, then a red one; once the orange has gone
    # the train drawn is the red one, six cars in BART's red.
    image, rows = bart_rows_page(monkeypatch, [{"destination": "Richmond", "estimate": [
        {"minutes": "2", "length": "5", "color": "ORANGE", "hexcolor": "#ff9933", "cancelflag": "0"},
        {"minutes": "9", "length": "6", "color": "RED", "hexcolor": "#ff0000", "cancelflag": "0"}]}],
        at=NOW + timedelta(minutes=4))
    _x, y, _width = rows["Richmond"]
    assert "6节" in rows
    assert len(stripe_runs(image, y + 5, (0xed, 0x1c, 0x24))) == 6
    assert not stripe_runs(image, y + 5, (0xfa, 0xa6, 0x1a))


def test_bart_rows_without_a_length_bart_runs_keep_the_leader(monkeypatch):
    image, rows = bart_rows_page(monkeypatch, [bart_etd("Berryessa", "ORANGE", "#ff9933", (4, 19), 14)])
    _x, y, _width = rows["Berryessa"]
    # No train and no "14节": the destination leads along its line in the feed's orange, as before.
    assert "14节" not in rows
    assert stripe_runs(image, y + 4, (0xff, 0x99, 0x33)) and not stripe_runs(image, y + 5, (0xfa, 0xa6, 0x1a))


def test_bart_rows_are_set_in_bart_typeface():
    from plugins.bay_commute.command_render import ASSETS, font

    assert font(12, "bart").getname()[0] == "Hind"
    assert font(12, "bart", bold=False).getname() != font(12, "bart").getname()
    record = json.loads((ASSETS / "fonts" / "Hind.source.json").read_text(encoding="utf-8"))
    assert record["licence"].startswith("SIL Open Font License") and (ASSETS / "fonts" / "Hind-OFL.txt").is_file()


def test_the_tide_readout_holds_past_midnight_and_falls_back_to_the_day_range(monkeypatch):
    from plugins.bay_commute import command_render

    late = NOW.replace(hour=23, minute=55)
    recorded = []
    original = Canvas.text

    def capture(self, xy, text, *args, **kwargs):
        recorded.append((round(xy[1]), str(text)))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(Canvas, "text", capture)
    page = Canvas({})
    # Today's samples end at 23:45; the prediction runs on into tomorrow, so the readout stays.
    command_render.tide_chart(page, tide_data(late), late)
    assert (command_render.READOUT_Y, "预测此刻") in recorded
    recorded.clear()
    morning_only = {**tide_data(late), "curve": [row for row in tide_data(late)["curve"]
                                                 if sources.parse_time(row["time"]) < late.replace(hour=12)]}
    command_render.tide_chart(Canvas({}), morning_only, late)
    assert (command_render.READOUT_Y, "今日潮位") in recorded
    assert command_render.READOUT_Y < command_render.CHART_HEADER_Y < command_render.CHART[1]


def test_bart_status_reads_advisories_elevators_and_trains_in_service():
    advisories = sources.parse_bart_advisories(BSA_DELAY)
    assert [(item["type"], item["station"]) for item in advisories] == [("DELAY", "BART")]
    assert advisories[0]["text"].startswith("Expect 30-minute delays") and advisories[0]["sms"]
    assert sources.parse_bart_advisories(BSA_QUIET) == []
    assert sources.parse_bart_elevators(ELEVATORS_FINE) == {"out": 0, "stations": []}
    assert sources.parse_bart_elevators(ELEVATORS_OUT) == {"out": 2, "stations": ["Civic Center", "Lake Merritt"]}
    one = {"root": {"bsa": [{"description": {"#cdata-section":
                                             "There is one elevator out of service at this time: Balboa Park "
                                             "Elevator. Thank you."}}]}}
    assert sources.parse_bart_elevators(one) == {"out": 1, "stations": ["Balboa Park"]}
    assert sources.parse_bart_count(TRAIN_COUNT) == 56
    for broken in ({}, {"root": {}}, {"root": {"traincount": "many"}}):
        with pytest.raises(ValueError):
            sources.parse_bart_count(broken)
    with pytest.raises(ValueError):
        sources.parse_bart_advisories({"root": {"message": ""}})


def test_bart_status_keeps_the_advisories_when_elevators_or_the_count_fail(monkeypatch):
    replies = {"bsa": BSA_DELAY, "elev": {"root": {"bsa": []}}, "count": TRAIN_COUNT}
    calls = []

    class Client:
        def request_json(self, method, url, **kwargs):
            calls.append(kwargs["params"]["cmd"])
            assert kwargs["params"]["key"] == sources.BART_PUBLIC_KEY and kwargs["max_bytes"] <= 64 * 1024
            return SimpleNamespace(data=replies[kwargs["params"]["cmd"]])

    monkeypatch.undo()
    monkeypatch.setattr(sources, "get_http_client", lambda: Client())
    status = sources.fetch_bart_status(NOW, context())
    assert calls == ["bsa", "elev", "count"]
    assert status["elevators"] is None and status["trains"] == 56 and len(status["advisories"]) == 1
    assert sources.valid_bart_status(status)
    assert not sources.valid_bart_status({**status, "trains": "56"})


def test_signs_keep_travel_times_on_the_pages_routes_nearest_home_first():
    data = sources.parse_signs(CMS_PAYLOAD, sources.DEFAULT_ROUTES)

    assert sources.valid_signs(data)
    # The closure message, the other route and the sign out of service are left out; SR-84's
    # travel times are on its second page.
    assert [(sign["route"], sign["direction"]) for sign in data["signs"]] == [
        ("I-880", "South"), ("I-680", "North"), ("SR-84", "East")]
    first = data["signs"][0]
    assert first["lines"] == [{"to": "MILPITAS", "minutes": 17}, {"to": "SJ ARPT", "minutes": 25},
                              {"to": "MENLO PRK", "minutes": 13}]
    assert first["distance_km"] < data["signs"][1]["distance_km"] < data["signs"][2]["distance_km"]
    # The reading's age is its newest message (the feed's own record time ran an hour ahead).
    assert data["source_updated_at"] == NOW.replace(hour=22, minute=26, second=10).isoformat()
    assert sources.pick_sign(data, NOW, choose=lambda signs: signs[-1])["route"] == "SR-84"
    assert sources.pick_sign(data, NOW + timedelta(hours=1)) is None
    assert sources.pick_sign({"signs": []}, NOW) is None
    with pytest.raises(ValueError):
        sources.parse_signs({"data": None}, sources.DEFAULT_ROUTES)


def test_observed_water_is_set_against_the_prediction_for_its_moment():
    observed = sources.parse_water_level({"metadata": {"id": "9414523"}, "data": [
        {"t": "2026-09-30 05:24", "v": "4.612", "s": "0.016", "f": "0,0,0,0", "q": "p"}]})
    assert observed == {"time": "2026-09-30T05:24:00+00:00", "height": 4.612, "quality": "p"}
    compared = sources.water_versus_prediction(observed, tide_data(NOW), NOW)
    assert compared["delta"] == pytest.approx(1.11) and compared["height"] == 4.612
    # Too old a reading, or none to compare it with, says nothing.
    assert sources.water_versus_prediction(observed, tide_data(NOW), NOW + timedelta(hours=2)) is None
    assert sources.water_versus_prediction(observed, {"curve": []}, NOW) is None
    for broken in ({"error": {"message": "No data was found"}}, {"data": [{"t": "x", "v": "1"}]},
                   {"data": [{"t": "2026-09-30 05:24", "v": "nan"}]}):
        with pytest.raises(ValueError):
            sources.parse_water_level(broken)


def test_the_extras_never_decide_provenance_and_a_failure_only_hides_them(plugin, monkeypatch):
    mock_sources(monkeypatch, [])
    payload = plugin._payload(SETTINGS, NOW, allow_network=True, context=context())
    assert payload["provenance"] == SourceProvenance.LIVE
    assert payload["bart_status"]["state"] == "live" and payload["bart_status"]["data"]["trains"] == 56
    assert payload["sign"]["route"] in {"I-880", "I-680", "SR-84"}
    assert payload["water"]["delta"] == pytest.approx(1.1)

    def offline(*args, **kwargs):
        raise OSError("offline")

    for name in ("fetch_bart_status", "fetch_signs", "fetch_water_level"):
        monkeypatch.setattr(sources, name, offline)
    later = plugin._payload({**SETTINGS, "forceRefresh": True}, NOW + timedelta(hours=3), allow_network=True,
                            context=context())
    assert later["bart_status"]["state"] == "unavailable" and later["sign"] is None and later["water"] is None
    assert later["provenance"] != SourceProvenance.LOCAL_FALLBACK


def test_the_header_shows_a_travel_time_sign_and_the_readout_the_measured_water(plugin, monkeypatch):
    from plugins.bay_commute.command_render import SIGN_BOX

    mock_sources(monkeypatch, [])
    payload = plugin._payload(SETTINGS, NOW, allow_network=True, context=context())
    recorded = []
    original = Canvas.text

    def capture(self, xy, text, size=13, color="ink", *args, **kwargs):
        recorded.append((str(text), color))
        return original(self, xy, text, size, color, *args, **kwargs)

    monkeypatch.setattr(Canvas, "text", capture)
    signed = render_page((800, 480), {**payload, "sign": sources.parse_signs(CMS_PAYLOAD, ["I-880"])["signs"][0]},
                         {"_inkypi_theme": {"mode": "day"}}, NOW)
    texts = {text for text, _color in recorded}
    assert {"MILPITAS", "17 MIN", "南向", "实测此刻", "4.6 ft", "+1.1 ft 高于预测"} <= texts
    assert ("延误", "paper") in recorded and "在线 56 列 ·" in texts
    assert any(text.startswith("Expect 30-min delays") for text in texts)
    plain = render_page((800, 480), {**payload, "sign": None, "water": None, "bart_status": {"state": "unavailable"}},
                        {"_inkypi_theme": {"mode": "night"}}, NOW)
    assert signed.crop(SIGN_BOX).getcolors(4096) and len(signed.crop(SIGN_BOX).getcolors(4096)) > 2
    assert len(plain.crop(SIGN_BOX).getcolors(4096)) == 1
    recorded.clear()
    quiet = {**payload, "bart_status": {"state": "live", "data": {
        "time": NOW.isoformat(), "advisories": [], "elevators": {"out": 0, "stations": []}, "trains": None}}}
    render_page((800, 480), quiet, {"_inkypi_theme": {"mode": "day"}}, NOW)
    assert "全线无延误公告 · 电梯全部运行" in {text for text, _color in recorded}
    assert "预测此刻" not in {text for text, _color in recorded}


BIKE_INFO = {"data": {"stations": [
    {"station_id": "pn", "name": "Powell St BART North", "lat": 37.78, "lon": -122.41, "capacity": 31},
    {"station_id": "ps", "name": "Powell St BART South", "lat": 37.78, "lon": -122.41, "capacity": 23},
    {"station_id": "gp", "name": "Glen Park BART", "lat": 37.73, "lon": -122.43, "capacity": 19},
    {"station_id": "be", "name": "Berryessa BART", "lat": 37.37, "lon": -121.87, "capacity": 15},
    {"station_id": "x", "name": "Market St at 10th St", "lat": 37.77, "lon": -122.42, "capacity": 27}]}}
BIKE_STATUS = {"data": {"stations": [
    {"station_id": "pn", "num_bikes_available": 10, "num_ebikes_available": 6, "num_docks_available": 17,
     "is_renting": 1},
    {"station_id": "ps", "num_bikes_available": 3, "num_ebikes_available": 1, "num_docks_available": 20,
     "is_renting": 1},
    {"station_id": "gp", "num_bikes_available": 0, "num_ebikes_available": 0, "num_docks_available": 19,
     "is_renting": 0},
    {"station_id": "x", "num_bikes_available": 5, "num_ebikes_available": 5, "num_docks_available": 2}]}}


def test_bay_wheels_docks_beside_a_bart_station_are_found_by_name_and_counted():
    stations = sources.parse_bike_stations(BIKE_INFO)
    assert [station["name"] for station in stations["stations"]] == [
        "Powell St BART North", "Powell St BART South", "Glen Park BART", "Berryessa BART"]
    status = sources.parse_bike_status(BIKE_STATUS, {station["id"] for station in stations["stations"]})
    assert set(status["stations"]) == {"pn", "ps", "gp"}
    # Powell's two docks add up: e-bikes apart from the classic bikes, and the free docks.
    assert sources.bikes_at(stations, status, "Powell St.") == {
        "name": "Powell St BART", "docks_count": 2, "bikes": 6, "ebikes": 7, "free": 37}
    # BART's own names with a qualifier still find their docks; a closed dock counts nothing.
    assert sources.bikes_at(stations, status, "Glen Park (SF)")["bikes"] == 0
    assert sources.bikes_at(stations, status, "Berryessa/North San Jose") is None  # no status for it
    assert sources.bikes_at(stations, status, "Fremont") is None
    assert sources.valid_bike_stations(stations) and sources.valid_bike_status(status)


def test_bart_notes_add_the_bikes_at_the_station_and_keep_three_trains():
    from plugins.bay_commute.command_render import bart_notes

    page = Canvas({"_inkypi_theme": {"mode": "day"}})
    status = {"state": "live", "data": {"time": NOW.isoformat(), "advisories": sources.parse_bart_advisories(BSA_DELAY),
                                        "elevators": {"out": 0, "stations": []}, "trains": 56}}
    bikes = {"name": "Powell St BART", "docks_count": 2, "bikes": 6, "ebikes": 7, "free": 37}
    notes = bart_notes(page, {"bart_status": status, "bikes": bikes})
    labels = [label for label, _colour, _lines in notes]
    assert labels[-1] == "单车"
    assert "6 辆普通" in notes[-1][2][0] and "7 辆电助力" in notes[-1][2][0] and "37 个空桩" in notes[-1][2][0]
    # The advisory gives up its second line to the bikes, so the trains keep their rows.
    assert len(notes[0][2]) == 1
    assert sum(len(lines) for _label, _colour, lines in notes) <= 3


def test_no_advisories_issued_reads_as_the_quiet_line():
    payload = {"root": {"bsa": [{"type": "", "description": {"#cdata-section": "No advisories issued."}}]}}
    assert sources.parse_bart_advisories(payload) == []
