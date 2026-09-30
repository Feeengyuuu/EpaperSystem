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
from plugins.bay_commute.render import Canvas, render_page
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
