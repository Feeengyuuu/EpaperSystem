import json
from datetime import datetime, timedelta, timezone

import pytest
import requests
from PIL import Image

from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeTopMovies, ZGDYPW_REALTIME_URL
from plugins.box_office_top_movies.box_office_top_movies import MAOYAN_SOURCE_LABEL
from plugins.box_office_top_movies.china_source import ChinaFetchBudget, official_metadata
from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from runtime.refresh_contracts import TaskContext, TaskCancelled
from utils.http_client import HttpClient
from tests.test_http_client import FakeSession, FakeResponse
from tests.test_box_office_top_movies import DummyDeviceConfig, EnvDeviceConfig


NOW = datetime(2026, 9, 2, 20, 30, tzinfo=timezone.utc)


def official_page(when=NOW, rows=None):
    state = {
        "dateRange": {"startDate": "2017-01-01", "endDate": "2099-01-01"},
        "boxData": {"updateTimestamp": int(when.timestamp() * 1000), "list": rows if rows is not None else [
            {"code": "film-1", "name": "测试电影", "salesInWanDesc": "123.45",
             "salesRateDesc": "45.67%", "splitSalesInWanDesc": "100.00", "sumSalesDesc": "1.23亿"},
        ]},
    }
    return '<div>今日实时 北京时间 含服务费</div><script>window.__INITIAL_STATE__=' + json.dumps(state) + ';</script>'


def test_official_mode_prefers_official_and_carries_source_time(monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW, raising=False)
    requested = []
    monkeypatch.setattr(plugin, "_fetch_text", lambda url: requested.append(url) or official_page())
    monkeypatch.setattr(plugin, "_fetch_json", lambda *_a: pytest.fail("healthy official source must not hit Maoyan"))

    movies, label = plugin._load_movies({"sourceMode": "official_china"}, 5)

    assert requested == [ZGDYPW_REALTIME_URL]
    assert movies[0].extra["today_box_wan"] == "123.45"
    metadata = movies[0].extra["source_metadata"]
    assert metadata == {
        "statistic_date": "2026-09-03", "source_updated_at": NOW.isoformat(),
        "fetched_at": NOW.isoformat(), "metric_scope": "comprehensive_including_service_fee",
        "source": "zgdypw_realtime", "timezone": "Asia/Shanghai",
    }


@pytest.mark.parametrize("page", ["", "<html>loading</html>", official_page(rows=[]), official_page(NOW-timedelta(days=1)),
                                   official_page(NOW+timedelta(hours=1))])
def test_official_mode_rejects_empty_wrong_day_or_future_data(monkeypatch, page):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW, raising=False)
    monkeypatch.setattr(plugin, "_fetch_text", lambda *_a: page)
    monkeypatch.setattr(plugin, "_fetch_json", lambda *_a: {"movieList": {"data": {"list": []}}})
    with pytest.raises(RuntimeError):
        plugin._load_movies({"sourceMode": "official_china"}, 5)


@pytest.mark.parametrize("stamp", [None, True, 123, int(NOW.timestamp()), float("inf")])
def test_official_timestamp_requires_valid_milliseconds(stamp):
    page = official_page().replace(str(int(NOW.timestamp()*1000)), json.dumps(stamp))
    with pytest.raises(RuntimeError):
        official_metadata(page, NOW)


@pytest.mark.parametrize("field,value", [("salesInWanDesc", ""), ("salesInWanDesc", None), ("salesRateDesc", "101%")])
def test_official_does_not_replace_comprehensive_data_with_split_gross(field, value):
    row = {"name": "电影", "salesInWanDesc": "100.5", "salesRateDesc": "50%", "splitSalesInWanDesc": "90"}
    row[field] = value
    with pytest.raises(RuntimeError):
        official_metadata(official_page(rows=[row]), NOW)


def test_duplicate_titles_cannot_pull_unvalidated_sixth_row_into_chart(monkeypatch):
    valid = {"name": "电影", "salesInWanDesc": "100", "salesRateDesc": "50%"}
    page = official_page(rows=[valid] * 5 + [{"name": "另一部电影", "splitSalesInWanDesc": "100"}])
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW)
    monkeypatch.setattr(plugin, "_fetch_text", lambda *_a: page)
    with pytest.raises(RuntimeError, match="comprehensive"):
        plugin._load_movies({"sourceMode": "official_china"}, 5)


@pytest.mark.parametrize("status,expected", [(403, 1), (503, 2)])
def test_budget_never_immediately_retries_403_and_bounds_transient_retry(status, expected):
    responses = [FakeResponse(status), FakeResponse(status), FakeResponse(200)]
    session = FakeSession(responses)
    parent = TaskContext.never_cancelled(deadline_monotonic=100, clock=lambda: 0)
    with ChinaFetchBudget(client=HttpClient(session=session, max_attempts=1), parent=parent) as budget:
        with pytest.raises(RuntimeError):
            budget.get(ZGDYPW_REALTIME_URL)
    assert len(session.calls) == expected
    assert all(response.closed for response in responses[:expected])


def test_budget_recovers_dns_once_and_shares_remaining_time():
    elapsed = [0]
    class RecoveringSession(FakeSession):
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            elapsed[0] += 4
            if len(self.calls) == 1:
                raise requests.ConnectionError("temporary DNS resolution error")
            return FakeResponse(200, b"ok")
    session = RecoveringSession([])
    parent = TaskContext.never_cancelled(deadline_monotonic=100, clock=lambda: elapsed[0])
    with ChinaFetchBudget(client=HttpClient(session=session, max_attempts=1), parent=parent) as budget:
        assert budget.get(ZGDYPW_REALTIME_URL).text == "ok"
        budget.get("https://example.test/poster", stream=True)
        assert budget.remaining_seconds() == 8
        assert budget.retry_used
    assert len(session.calls) == 3
    assert session.calls[-1][2]["timeout"] == (3, 6)


def test_budget_expiry_preserves_old_cache_bytes_and_times(tmp_path, monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: NOW)
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_a: None)
    monkeypatch.setattr(plugin, "_render_chart", lambda *_a: Image.new("RGB", (32, 16)))
    monkeypatch.setattr(plugin, "_enrich_with_tmdb", lambda *_a, **_k: None)
    monkeypatch.setattr(plugin, "_download_posters", lambda *_a, **_k: None)
    monkeypatch.setattr(plugin, "_fetch_text", lambda *_a: official_page())
    settings = {"sourceMode": "official_china", "forceRefresh": True}
    plugin.generate_image(settings, DummyDeviceConfig())
    path = plugin._cache_path()
    before = path.read_bytes()
    timestamp = path.stat().st_mtime_ns
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW + timedelta(days=1))
    # Cross-day source is rejected, never turned into a new DATA success.
    image = plugin.generate_image(settings, DummyDeviceConfig())
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == timestamp


def test_budget_deadline_becomes_source_failure_but_parent_cancel_propagates():
    elapsed = [0]
    parent = TaskContext.never_cancelled(deadline_monotonic=100, clock=lambda: elapsed[0])
    with pytest.raises(RuntimeError, match="source budget expired"):
        with ChinaFetchBudget(client=HttpClient(session=FakeSession([]), max_attempts=1), parent=parent) as budget:
            elapsed[0] = 21
            budget.get(ZGDYPW_REALTIME_URL)
    with pytest.raises(TaskCancelled):
        with ChinaFetchBudget(client=HttpClient(session=FakeSession([]), max_attempts=1), parent=parent) as budget:
            parent.cancel_event.set()
            budget.get(ZGDYPW_REALTIME_URL)


def test_optional_enrichment_uses_remaining_budget_without_losing_valid_chart(tmp_path, monkeypatch):
    import plugins.box_office_top_movies.china_source as source_module
    elapsed = [0]
    parent = TaskContext.never_cancelled(deadline_monotonic=100, clock=lambda: elapsed[0])
    class SlowEnrichmentSession(FakeSession):
        def close(self):
            pass
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            elapsed[0] += 18 if len(self.calls) == 1 else 3
            return FakeResponse(200, official_page().encode("utf-8") if len(self.calls) == 1 else b'{"results":[]}')
    session = SlowEnrichmentSession([])
    monkeypatch.setattr(source_module, "IsolatedChinaHttpClient", lambda: HttpClient(session=session, max_attempts=1))
    monkeypatch.setattr(source_module, "current_task_context", lambda: parent)
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW)
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_a: None)
    monkeypatch.setattr(plugin, "_render_chart", lambda *_a: Image.new("RGB", (32, 16)))
    monkeypatch.setattr(plugin, "_download_posters", lambda *_a, **_k: pytest.fail("exhausted budget must skip posters"))
    image = plugin.generate_image({"sourceMode": "official_china", "forceRefresh": True}, EnvDeviceConfig({"TMDB_API_KEY": "test-key"}))
    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert len(session.calls) == 2
    assert session.calls[-1][2]["timeout"] == (1, 1)
    saved = json.loads(plugin._cache_path().read_text(encoding="utf-8"))
    assert saved["source_metadata"]["source_updated_at"] == NOW.isoformat()


@pytest.mark.parametrize("drift", [None, "north_america", "source", "label", "settings", "time", "theme_only"])
def test_migrated_first_fetch_preserves_only_matching_mainland_fallback(tmp_path, monkeypatch, drift):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    device = DummyDeviceConfig()
    settings = {"sourceMode": "official_china", "forceRefresh": True}
    legacy_settings = {**settings, "sourceMode": "maoyan_china"}
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW)
    rendered = []
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_a: None)
    monkeypatch.setattr(plugin, "_render_chart", lambda *args: rendered.append(args) or Image.new("RGB", (32, 16)))
    monkeypatch.setattr(plugin, "_load_and_enrich_movies", lambda *_a: (_ for _ in ()).throw(RuntimeError("offline")))
    movies = plugin._parse_zgdypw_realtime(official_page(), ZGDYPW_REALTIME_URL)
    movies[0].extra["source"] = "maoyan"
    saved_time = (NOW - timedelta(days=1)).isoformat()
    if drift == "north_america":
        legacy_settings["sourceMode"] = "the_numbers"
    if drift == "settings":
        legacy_settings["tmdbLanguage"] = "different"
    if drift == "source":
        movies[0].extra["source"] = "the_numbers"
    cache = {
        "version": plugin._cache_state_version(),
        "cache_key": plugin._cache_key(legacy_settings, (800, 480), 5, device),
        "source_label": "Other" if drift == "label" else MAOYAN_SOURCE_LABEL,
        "generated_at": "unknown" if drift == "time" else saved_time,
        "movies": [movie.to_dict() for movie in movies],
    }
    plugin._write_cache(cache)
    path = plugin._cache_path()
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    if drift == "theme_only":
        with pytest.raises(RuntimeError, match="matching cached source"):
            plugin.generate_image({**settings, "_theme_render_only": True}, device)
    else:
        image = plugin.generate_image(settings, device)
        expected = SourceProvenance.STALE_CACHE if drift is None else SourceProvenance.LOCAL_FALLBACK
        assert read_source_provenance(image) is expected
        assert image.info["inkypi_skip_cache"] is True
        if drift is None:
            assert rendered[0][1][0].to_dict() == movies[0].to_dict()
            assert rendered[0][3] == MAOYAN_SOURCE_LABEL
            assert rendered[0][4].isoformat() == saved_time
            assert rendered[0][5] is True
            assert "source_metadata" not in rendered[0][1][0].extra
        else:
            assert not rendered
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime
