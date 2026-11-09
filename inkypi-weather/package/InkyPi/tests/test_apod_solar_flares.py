import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.apod import apod_page, solar_flares  # noqa: E402
from plugins.apod.space_weather import SpaceWeatherRepository, refresh_space_weather  # noqa: E402
from runtime.refresh_contracts import TaskCancelled  # noqa: E402

NOW = datetime(2026, 11, 3, 0, 30, tzinfo=timezone.utc)
LATEST = [{"time_tag": "2026-11-03T00:27:00Z", "satellite": 18, "current_class": "B3.2",
           "begin_time": "2026-11-02T21:59:00Z", "max_time": "2026-11-02T22:08:00Z", "max_class": "B8.0",
           "end_time": "2026-11-02T22:17:00Z"}]
WEEK = [
    {"max_time": "2026-10-31T10:00:00Z", "max_class": "M1.0"},  # older than 48 h: dropped
    {"max_time": "2026-11-01T23:00:00Z", "max_class": "C2.1"},  # within 48 h, outside 24 h
    {"max_time": "2026-11-02T16:44:00Z", "max_class": "B4.2"},
    {"max_time": "2026-11-02T22:08:00Z", "max_class": "B8.0"},
]
PROBABILITIES = [
    {"date": "2026-11-01T00:00:00", "c_class_1_day": 25, "c_class_2_day": 25, "c_class_3_day": 25,
     "m_class_1_day": 1, "m_class_2_day": 1, "m_class_3_day": 1, "x_class_1_day": 1, "x_class_2_day": 1,
     "x_class_3_day": 1, "10mev_protons_1_day": 1, "10mev_protons_2_day": 1, "10mev_protons_3_day": 1},
    {"date": "2026-11-02T00:00:00", "c_class_1_day": 30, "c_class_2_day": 35, "c_class_3_day": 40,
     "m_class_1_day": 5, "m_class_2_day": 10, "m_class_3_day": 10, "x_class_1_day": 1, "x_class_2_day": 1,
     "x_class_3_day": 2, "10mev_protons_1_day": 1, "10mev_protons_2_day": 1, "10mev_protons_3_day": 1,
     "polar_cap_absorption": "green"},
]


class FakeHttp:
    def __init__(self, responses):
        self.responses = {endpoint: list(values) for endpoint, values in responses.items()}
        self.calls = []

    def request_bytes(self, method, endpoint, **kwargs):
        self.calls.append(endpoint)
        queue = self.responses.get(endpoint)
        if not queue:
            raise RuntimeError(f"offline: {endpoint}")
        value = queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(data=json.dumps(value).encode("utf-8"))


def _flare_http(**overrides):
    responses = {solar_flares.XRAY_FLARES_ENDPOINT: [LATEST], solar_flares.XRAY_FLARES_WEEK_ENDPOINT: [WEEK],
                 solar_flares.SOLAR_PROBABILITIES_ENDPOINT: [PROBABILITIES]}
    responses.update(overrides)
    return FakeHttp(responses)


@pytest.mark.parametrize(("value", "expected"), [("B3.2", "B3.2"), ("x1", "X1.0"), (" m2.45 ", "M2.5"),
                                                 ("Z1.0", None), ("", None), (None, None)])
def test_flare_classes_normalise(value, expected):
    assert solar_flares.flare_class(value) == expected


def test_class_flux_orders_classes_across_letters():
    assert solar_flares.class_flux("B9.9") < solar_flares.class_flux("C1.0") < solar_flares.class_flux("X1.0")


def test_xray_keeps_the_class_now_and_two_days_of_flares():
    payload = solar_flares.normalize_xray(LATEST, WEEK, now_utc=NOW)

    assert payload["current_class"] == "B3.2"
    assert payload["observed_at_utc"] == "2026-11-03T00:27:00Z"
    assert [flare["class"] for flare in payload["flares"]] == ["C2.1", "B4.2", "B8.0"]
    projected = solar_flares.project_xray(payload, now_utc=NOW)
    # C2.1 is 25.5 h old: the 24-hour peak is the B8.0.
    assert projected == {"current_class": "B3.2", "time_tag": "2026-11-03T00:27:00Z", "peak_class": "B8.0",
                         "peak_time": "2026-11-02T22:08:00Z"}
    # Without the week list the latest flare alone still gives the peak.
    alone = solar_flares.normalize_xray(LATEST, None, now_utc=NOW)
    assert solar_flares.project_xray(alone, now_utc=NOW)["peak_class"] == "B8.0"
    with pytest.raises(ValueError):
        solar_flares.normalize_xray([{"time_tag": "2026-11-03T00:27:00Z"}], WEEK, now_utc=NOW)


def test_flare_probabilities_take_the_newest_issue():
    payload = solar_flares.normalize_flare_probabilities(PROBABILITIES, now_utc=NOW)

    assert payload == {"issued_date": "2026-11-02", "c": [30, 35, 40], "m": [5, 10, 10], "x": [1, 1, 2],
                       "protons": [1, 1, 1]}
    with pytest.raises(ValueError):
        solar_flares.normalize_flare_probabilities([{"date": "2026-11-02T00:00:00", "c_class_1_day": 130}],
                                                   now_utc=NOW)


def test_repository_caches_each_flare_product_and_falls_back_to_it(tmp_path):
    repository = SpaceWeatherRepository(cache_dir=tmp_path, http=_flare_http())
    xray, chances = repository.refresh_flares(now_utc=NOW, context=None)

    assert (xray.state, chances.state) == ("live", "live")
    assert (tmp_path / "xray.json").is_file() and (tmp_path / "flare_probabilities.json").is_file()

    repository.http = FakeHttp({})
    xray, chances = repository.refresh_flares(now_utc=NOW + timedelta(minutes=10), context=None)
    assert (xray.state, chances.state) == ("fresh_cache", "fresh_cache")
    assert xray.envelope.payload["current_class"] == "B3.2"
    xray, chances = repository.refresh_flares(now_utc=NOW + timedelta(hours=3), context=None)
    # The class is too old after two hours; the day's chances last a day.
    assert (xray.state, chances.state) == ("unavailable", "fresh_cache")


def test_week_list_failure_keeps_the_xray_source(tmp_path):
    http = _flare_http(**{solar_flares.XRAY_FLARES_WEEK_ENDPOINT: [RuntimeError("week offline")]})
    xray, _chances = SpaceWeatherRepository(cache_dir=tmp_path, http=http).refresh_flares(now_utc=NOW, context=None)

    assert xray.state == "live"
    assert [flare["class"] for flare in xray.envelope.payload["flares"]] == ["B8.0"]


def test_cancellation_is_never_swallowed_by_the_flare_sources(tmp_path):
    signal = TaskCancelled("stop")
    http = _flare_http(**{solar_flares.XRAY_FLARES_WEEK_ENDPOINT: [signal]})
    with pytest.raises(TaskCancelled):
        SpaceWeatherRepository(cache_dir=tmp_path, http=http).refresh_flares(now_utc=NOW, context=None)
    assert not (tmp_path / "xray.json").exists()


class _Repository:
    """Core and optional NOAA sources from fixtures; flares from the fake HTTP client."""

    def __init__(self, tmp_path, http):
        self.flares = SpaceWeatherRepository(cache_dir=tmp_path, http=http)
        self.cache_dir = None

    def refresh_core(self, **_kwargs):
        from plugins.apod.space_weather import SourceResult, _build_envelope

        scales = _build_envelope(endpoint="scales", fetched_at_utc=NOW, raw_digest="0" * 64, freshness_kind="scales",
                                 payload={"observed_at_utc": "2026-11-03T00:20:00Z", "current": {"g": 0, "r": 0, "s": 0},
                                          "forecast_g": [], "probabilities": {}})
        kp = _build_envelope(endpoint="kp", fetched_at_utc=NOW, raw_digest="0" * 64, freshness_kind="kp",
                             payload={"observed_at_utc": "2026-11-03T00:00:00Z",
                                      "current": {"kp": 0.33, "observed": "estimated", "noaa_scale": None,
                                                  "time_tag": "2026-11-03T00:00:00Z"}, "predicted_peak": {}})
        return (SourceResult(name="scales", state="live", envelope=scales),
                SourceResult(name="kp", state="live", envelope=kp))

    def refresh_wind(self, **_kwargs):
        from plugins.apod.space_weather import SourceResult

        return (SourceResult(name="wind_speed", state="unavailable", envelope=None),
                SourceResult(name="wind_mag", state="unavailable", envelope=None))

    def refresh_alerts(self, **_kwargs):
        from plugins.apod.space_weather import SourceResult

        return SourceResult(name="alerts", state="unavailable", envelope=None), "unavailable", None

    def refresh_donki(self, **_kwargs):
        from plugins.apod.space_weather import SourceResult

        return SourceResult(name="donki", state="unavailable", envelope=None), None

    def refresh_flares(self, **kwargs):
        return self.flares.refresh_flares(**kwargs)


def test_snapshot_carries_the_flares_and_the_page_draws_them(tmp_path, monkeypatch):
    snapshot = refresh_space_weather(_Repository(tmp_path, _flare_http()), nasa_api_key="k", now_utc=NOW,
                                     context=None)
    assert snapshot.xray["current_class"] == "B3.2" and snapshot.xray["peak_class"] == "B8.0"
    assert list(snapshot.flare_probabilities["m"]) == [5, 10, 10]
    assert snapshot.sources["xray"].state == "live"

    drawn = []
    original = ImageDraw.ImageDraw.text

    def capture(draw, xy, text, *args, **kwargs):
        drawn.append(str(text))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    canvas = Image.new("RGB", apod_page.PAGE_SIZE, apod_page.PAGE_COLOR)
    draw = apod_page.MixedDraw(ImageDraw.Draw(canvas))
    apod_page._draw_metrics_panel(canvas, draw, snapshot, apod_page._load_fonts())
    text = " ".join(drawn)
    for expected in ("B3.2", "24 小时最高", "B8.0", "耀斑概率", "30%", "5%", "1%", "Bt"):
        assert expected in text, expected
    assert "48 小时预报" not in text


def test_without_flare_data_the_metrics_keep_their_four_cells(tmp_path, monkeypatch):
    snapshot = refresh_space_weather(_Repository(tmp_path, FakeHttp({})), nasa_api_key="k", now_utc=NOW,
                                     context=None)
    assert dict(snapshot.xray) == {} and dict(snapshot.flare_probabilities) == {}

    drawn = []
    original = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text",
                        lambda draw, xy, text, *a, **k: (drawn.append(str(text)), original(draw, xy, text, *a, **k)))
    canvas = Image.new("RGB", apod_page.PAGE_SIZE, apod_page.PAGE_COLOR)
    apod_page._draw_metrics_panel(canvas, apod_page.MixedDraw(ImageDraw.Draw(canvas)), snapshot,
                                  apod_page._load_fonts())
    assert "48 小时预报" in drawn and not any("耀斑" in text for text in drawn)
