"""Vehicle Status's California Supercharger news (supercharge.info): the newest sites opened and
the sites under construction nearest downtown San Francisco, in turn with the vehicle strip."""

import json
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from plugins.vehicle_status import superchargers
from plugins.vehicle_status import vehicle_status as vehicle_module
from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from tests.test_vehicle_status import _summary_v3


def _change(day, status, previous, stalls, power, name, latitude, longitude, kind="UPDATE"):
    return {"date": day, "changeType": kind, "siteStatus": status, "prevStatus": previous, "stallCount": stalls,
            "powerKilowatt": power, "siteName": name, "state": "CA",
            "site": {"name": name, "status": status, "gps": {"latitude": latitude, "longitude": longitude},
                     "stallCount": stalls, "powerKilowatt": power}}


# supercharge.info/service/supercharge/changes?regionId=100&countryId=100&state=CA&start=0&length=5
# on 2026-11-11 (its five results, the fields the page reads).
CHANGES = {"pageSize": 5, "recordCount": 2377, "results": [
    _change("2026-11-03", "OPEN", "CONSTRUCTION", 12, 325, "Castaic, CA - 31783 Castaic Rd", 34.495778, -118.622176),
    _change("2026-10-30", "OPEN", "CONSTRUCTION", 16, 500, "Santa Ana, CA - N Grand Ave", 33.771032, -117.851484),
    _change("2026-10-26", "OPEN", "CONSTRUCTION", 16, 500, "Simi Valley, CA - Cochran St", 34.279326, -118.689532),
    _change("2026-10-20", "OPEN", "CONSTRUCTION", 8, 500, "Novato, CA - San Marin Dr", 38.122295, -122.604236),
    _change("2026-10-15", "PLAN", None, 20, 325, "Yucaipa, CA", 34.018967, -117.092715, kind="ADD")]}


def _site(name, latitude, longitude, stalls, power, days):
    return {"name": name, "status": "CONSTRUCTION", "gps": {"latitude": latitude, "longitude": longitude},
            "stallCount": stalls, "powerKilowatt": power, "statusDays": days}


# .../sites?regionId=100&countryId=100&state=CA&status=CONSTRUCTION on 2026-11-11: seven of its 39.
SITES = {"recordCount": 39, "results": [
    _site("Antioch, CA - Contra Loma Blvd", 37.990697, -121.822785, 24, 325, 155),
    _site("Barstow, CA - E Main St", 34.891512, -117.001854, 16, 325, 106),
    _site("Brisbane, CA", 37.673544, -122.384757, 16, 325, 116),
    _site("Colma, CA", 37.677465, -122.467644, 16, 500, 119),
    _site("Daly City, CA - Serramonte Center", 37.669677, -122.471231, 0, 325, 99),
    _site("Millbrae, CA", 37.602643, -122.39162, 24, 500, 308),
    _site("San Francisco, CA - Cambon Dr", 37.717154, -122.474143, 16, 500, 250)]}
NOW = datetime(2026, 11, 11, 13, 30).timestamp()


def test_changes_give_the_newest_openings_and_sites_the_nearest_under_construction():
    opened = superchargers.parse_opened(CHANGES)
    building = superchargers.parse_building(SITES)

    assert [(row["name"], row["date"], row["stalls"], row["kw"]) for row in opened] == [
        ("Castaic", "2026-11-03", 12, 325), ("Santa Ana", "2026-10-30", 16, 500),
        ("Simi Valley", "2026-10-26", 16, 500), ("Novato", "2026-10-20", 8, 500)]
    # Nearest downtown San Francisco first (a fixed point, never the car's or the owner's place).
    assert [(row["name"], row["days"], row["km"]) for row in building] == [
        ("San Francisco", 250, 8), ("Colma", 119, 12), ("Brisbane", 116, 12), ("Daly City", 99, 13)]
    assert building[3]["stalls"] is None  # a site that does not say how many yet
    for broken in ({}, {"results": "x"}, []):
        with pytest.raises(ValueError):
            superchargers.parse_opened(broken)
        with pytest.raises(ValueError):
            superchargers.parse_building(broken)


class Client:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def request_json(self, method, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        if self.fail:
            raise OSError("offline")
        return SimpleNamespace(data=CHANGES if url.endswith("/changes") else SITES)


@pytest.mark.parametrize("error", [TaskCancelled, TaskDeadlineExceeded])
def test_optional_charger_requests_do_not_swallow_refresh_cancellation(tmp_path, monkeypatch, error):
    def stop(*_args, **_kwargs):
        raise error("refresh stopped")

    monkeypatch.setattr(superchargers, "get_http_client", lambda: SimpleNamespace(request_json=stop))
    with pytest.raises(error, match="refresh stopped"):
        superchargers.refresh(tmp_path, NOW)
    assert not (tmp_path / superchargers.CACHE_NAME).exists()


def test_news_is_cached_twelve_hours_keeps_its_last_copy_and_asks_for_no_place(tmp_path, monkeypatch):
    client = Client()
    monkeypatch.setattr(superchargers, "get_http_client", lambda: client)
    superchargers.refresh(tmp_path, NOW)
    superchargers.refresh(tmp_path, NOW + 11 * 3600)

    assert [url.rsplit("/", 1)[1] for url, _params in client.calls] == ["changes", "sites"]
    # Filtered, paged requests only, and no coordinate in either.
    assert all(params["state"] == "CA" and params["length"] <= 60 and not {"lat", "lng", "latitude"} & set(params)
               for _url, params in client.calls)
    assert "all" not in "".join(url for url, _params in client.calls).lower()
    news = superchargers.read(tmp_path, NOW + 11 * 3600)
    assert [row["name"] for row in news["opened"]][:2] == ["Castaic", "Santa Ana"] and len(news["building"]) == 4
    # Half a day on a failed refresh keeps the copy; a week on it is no longer shown.
    failing = Client(fail=True)
    monkeypatch.setattr(superchargers, "get_http_client", lambda: failing)
    superchargers.refresh(tmp_path, NOW + 13 * 3600)
    assert len(failing.calls) == 2 and superchargers.read(tmp_path, NOW + 13 * 3600) == news
    assert superchargers.read(tmp_path, NOW + 8 * 86400) is None
    assert superchargers.read(tmp_path / "missing", NOW) is None
    (tmp_path / superchargers.CACHE_NAME).write_text("{broken", encoding="utf-8")
    assert superchargers.read(tmp_path, NOW) is None


def test_the_news_has_the_odd_hours_and_gives_way_to_a_pending_update(tmp_path, monkeypatch):
    monkeypatch.setattr(superchargers, "get_http_client", lambda: Client())
    superchargers.refresh(tmp_path, NOW)

    assert superchargers.for_display(tmp_path, NOW)["opened"]  # 13:30
    assert superchargers.for_display(tmp_path, NOW + 3600) is None  # 14:30: the vehicle strip's hour


def _render(news, mode="day", language="zh-CN", update=None):
    plugin = vehicle_module.VehicleStatus({"id": "vehicle_status"})
    payload = deepcopy(_summary_v3())
    payload["software_update"] = update or dict.fromkeys(payload["software_update"])
    summary = vehicle_module.sanitize_summary(payload)
    return plugin._render_summary(summary, (800, 480), {"mode": mode}, {"language": language},
                                  supercharger_news=news)


def _news():
    return {"opened": superchargers.parse_opened(CHANGES), "building": superchargers.parse_building(SITES)}


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_news_takes_the_vehicle_strip_and_nothing_else(monkeypatch, mode):
    drawn = []
    original = vehicle_module._ellipsize_text
    monkeypatch.setattr(vehicle_module, "_ellipsize_text",
                        lambda draw, text, font, room: drawn.append(text) or original(draw, text, font, room))
    plain = _render(None, mode)
    drawn.clear()
    page = _render(_news(), mode)

    assert {"Castaic", "Santa Ana", "San Francisco · 8 km", "Colma · 12 km"} <= set(drawn)
    assert {"11/03 开放 · 12 桩 · 325 kW", "在建 250 天 · 16 桩 · 500 kW"} <= set(drawn)
    strip = (20, vehicle_module.STRIP_RULE_Y + 1, 780, vehicle_module.FOOTER_Y - 4)
    assert page.crop(strip).tobytes() != plain.crop(strip).tobytes()
    for box in ((0, 0, 800, vehicle_module.STRIP_RULE_Y + 1), (0, vehicle_module.FOOTER_Y - 4, 800, 480)):
        assert page.crop(box).tobytes() == plain.crop(box).tobytes()
    # In English too; an update waiting to install keeps the vehicle strip (its progress is there).
    drawn.clear()
    _render(_news(), mode, language="en")
    assert "Opened 11/03 · 12 stalls · 325 kW" in drawn and "Building 250 d · 16 stalls · 500 kW" in drawn
    pending = {"version": "2026.40.1", "download_percent": 100, "install_percent": 10, "expected_duration_minutes": 25}
    assert _render(_news(), mode, update=pending).tobytes() == _render(None, mode, update=pending).tobytes()
    assert _render({"opened": [], "building": []}, mode).tobytes() == plain.tobytes()


def test_a_theme_redraw_reads_the_cache_and_a_refresh_reads_the_news_once(tmp_path, monkeypatch):
    from tests.test_vehicle_status import DeviceConfig, FakeHttpClient

    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    bridge, news = FakeHttpClient(_summary_v3()), Client()
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: bridge)
    monkeypatch.setattr(superchargers, "get_http_client", lambda: news)
    plugin = vehicle_module.VehicleStatus({"id": "vehicle_status"})

    plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, DeviceConfig())
    assert len(news.calls) == 2 and len(bridge.calls) == 1
    cached = json.loads((plugin.cache_dir(leaf="cache", create=False) / superchargers.CACHE_NAME).read_text("utf-8"))
    assert cached["opened"]["items"][0]["name"] == "Castaic"
    plugin.generate_image({"_inkypi_theme": {"mode": "night"}, "_theme_render_only": True}, DeviceConfig())
    assert len(news.calls) == 2 and len(bridge.calls) == 1
