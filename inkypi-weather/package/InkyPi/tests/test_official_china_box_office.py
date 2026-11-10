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
    monkeypatch.setattr(plugin, "_enrich_official_with_maoyan", lambda *_a: None)  # stays offline
    monkeypatch.setattr(plugin, "_fetch_text", lambda *_a: official_page())
    settings = {"sourceMode": "official_china", "northAmericaColumn": "false", "forceRefresh": True}
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
    image = plugin.generate_image({"sourceMode": "official_china", "northAmericaColumn": "false", "forceRefresh": True}, EnvDeviceConfig({"TMDB_API_KEY": "test-key"}))
    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert len(session.calls) == 2
    assert session.calls[-1][2]["timeout"] == (1, 1)
    saved = json.loads(plugin._cache_path().read_text(encoding="utf-8"))
    assert saved["source_metadata"]["source_updated_at"] == NOW.isoformat()


@pytest.mark.parametrize("drift", [None, "north_america", "source", "label", "settings", "time", "theme_only"])
def test_migrated_first_fetch_preserves_only_matching_mainland_fallback(tmp_path, monkeypatch, drift):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    device = DummyDeviceConfig()
    settings = {"sourceMode": "official_china", "northAmericaColumn": "false", "forceRefresh": True}
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


# --- national total, seat occupancy, online share and Maoyan scores ---------------------------

def test_the_official_chart_gives_the_national_total_and_each_films_seats_and_online_share():
    rows = [{"code": "film-1", "name": "神探之痕迹", "salesInWanDesc": "20.06", "salesRateDesc": "16.23%",
             "sumSalesDesc": "6.64亿", "seatRateDesc": "1.48%", "onlineSalesRateDesc": "83.74%"}]
    page = official_page(rows=rows).replace('"list"', '"nationalSales": {"salesDesc": {"unit": "万", "value": '
                                                      '"123.66"}, "splitSalesDesc": {"unit": "万", "value": "111.38"}}, "list"')
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})

    metadata = official_metadata(page, NOW)
    movie = plugin._parse_zgdypw_realtime(page, ZGDYPW_REALTIME_URL)[0]

    assert metadata["national_sales"] == "123.66万"
    assert (movie.extra["seat_rate"], movie.extra["online_rate"]) == ("1.48%", "83.74%")
    assert "national_sales" not in official_metadata(official_page(), NOW)
    broken = official_page().replace('"list"', '"nationalSales": {"salesDesc": {"unit": "%", "value": "x"}}, "list"')
    assert "national_sales" not in official_metadata(broken, NOW)


class _MaoyanBudget:
    """Answers Maoyan's now-showing list and detail pages like ChinaFetchBudget.get."""

    def __init__(self, listing, detail=None, fail=None):
        self.listing, self.detail, self.fail, self.calls = listing, detail, fail, []

    def remaining_seconds(self):
        return 10

    def get(self, url, *, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        if self.fail:
            raise self.fail
        response = requests.Response()
        response.status_code = 200
        body = {"movieList": self.listing} if url.endswith("movieOnInfoList") else {"detailMovie": self.detail}
        response._content = json.dumps(body).encode("utf-8")
        return response


def _official_movies(plugin, titles):
    rows = [{"code": f"film-{index}", "name": title, "salesInWanDesc": "10.00", "salesRateDesc": "10.00%",
             "sumSalesDesc": "1.00亿"} for index, title in enumerate(titles)]
    return plugin._parse_zgdypw_realtime(official_page(rows=rows), ZGDYPW_REALTIME_URL)


def test_maoyan_scores_and_the_headliners_facts_join_the_official_chart():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    movies = _official_movies(plugin, ["神探之痕迹", "生化危机：爆发夜", "无人认领"])
    budget = _MaoyanBudget(
        [{"id": 1552906, "nm": "神探之痕迹", "sc": 9.6, "wish": 211664},
         {"id": 1586199, "nm": "生化危机：爆发夜", "sc": 0, "wish": 265115}],
        {"id": 1552906, "nm": "神探之痕迹", "sc": 9.6, "snum": 99404, "wish": 211664, "dur": 140,
         "cat": "犯罪,悬疑", "dir": "陈思诚,某人"})

    plugin._enrich_official_with_maoyan(movies, budget)

    hero, second, third = (movie.extra for movie in movies)
    assert (hero["maoyan_score"], hero["maoyan_votes"], hero["maoyan_wish"]) == (9.6, 99404, 211664)
    assert (hero["genres"], hero["runtime"], hero["director"]) == ("犯罪/悬疑", 140, "陈思诚")
    # Maoyan's "暂无评分" (score 0) is no score; the film keeps its want-to-see count.
    assert "maoyan_score" not in second and second["maoyan_wish"] == 265115 and "genres" not in second
    assert not any(key.startswith("maoyan") for key in third)
    assert [url.rsplit("/", 1)[-1] for url, _params in budget.calls] == ["movieOnInfoList", "detailmovie"]
    assert budget.calls[1][1] == {"movieId": "1552906"}


def test_a_maoyan_detail_for_another_film_or_a_failed_read_adds_nothing():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    movies = _official_movies(plugin, ["神探之痕迹"])
    plugin._enrich_official_with_maoyan(movies, _MaoyanBudget(
        [{"id": 1552906, "nm": "神探之痕迹", "sc": 9.6}], {"id": 1552906, "nm": "别的电影", "dur": 99, "cat": "喜剧"}))
    assert movies[0].extra["maoyan_score"] == 9.6 and "runtime" not in movies[0].extra

    movies = _official_movies(plugin, ["神探之痕迹"])
    plugin._enrich_official_with_maoyan(movies, _MaoyanBudget([], fail=requests.ConnectionError("down")))
    assert not any(key.startswith("maoyan") for key in movies[0].extra)
    with pytest.raises(TaskCancelled):
        plugin._enrich_official_with_maoyan(movies, _MaoyanBudget([], fail=TaskCancelled("stop")))


def test_the_headliners_maoyan_facts_stay_with_the_film_across_refreshes():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    old = _official_movies(plugin, ["神探之痕迹"])
    old[0].extra.update(maoyan_movie_id="1552906", genres="犯罪", runtime=140, director="陈思诚", maoyan_score=9.6)
    new = _official_movies(plugin, ["神探之痕迹"])

    plugin._reuse_official_media(new, [movie.to_dict() for movie in old])

    assert (new[0].extra["genres"], new[0].extra["runtime"], new[0].extra["director"]) == ("犯罪", 140, "陈思诚")
    assert "maoyan_score" not in new[0].extra  # scores are read fresh, never carried over


def _forbidden(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} Client Error", response=response)


def test_maoyans_403_dashboard_hands_over_to_the_official_chart_quietly(monkeypatch, caplog):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW, raising=False)
    monkeypatch.setattr(plugin, "_fetch_json", lambda *_a: (_ for _ in ()).throw(_forbidden(403)))
    monkeypatch.setattr(plugin, "_fetch_text", lambda *_a: official_page())

    with caplog.at_level("INFO"):
        movies, label = plugin._load_movies({"sourceMode": "maoyan_china"}, 5)

    assert label == "中国电影票房" and movies[0].title == "测试电影"
    assert any("403" in record.message and record.levelname == "INFO" for record in caplog.records)
    assert not any(record.levelname == "WARNING" for record in caplog.records)


def test_auto_mode_reads_maoyans_403_as_no_chart_and_other_errors_as_errors(monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_fetch_text", lambda *_a: "<html>no table</html>")
    monkeypatch.setattr(plugin, "_fetch_json", lambda *_a: (_ for _ in ()).throw(_forbidden(403)))
    with pytest.raises(RuntimeError, match="No box office chart source produced movies"):
        plugin._load_movies({"sourceMode": "auto"}, 5)
    monkeypatch.setattr(plugin, "_fetch_json", lambda *_a: (_ for _ in ()).throw(_forbidden(500)))
    with pytest.raises(requests.HTTPError):
        plugin._load_movies({"sourceMode": "auto"}, 5)


def test_a_film_tmdb_left_without_a_poster_takes_maoyans(monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    movies = _official_movies(plugin, ["无可替代的你", "神探之痕迹", "什么意思夫妇"])
    # The second already has its TMDB poster; the third's address is known but not fetched yet.
    movies[1].poster_url, movies[1].poster_path = "https://image.tmdb.org/t/p/w342/a.jpg", "a.jpg"
    movies[1].extra["poster_source"] = "tmdb"
    movies[2].poster_url = "https://image.tmdb.org/t/p/w342/b.jpg"
    fetched = []
    monkeypatch.setattr(plugin, "_download_posters", lambda films, session=None: fetched.extend(
        (film.title, film.poster_url) for film in films))
    poster = "https://p0.pipi.cn/mediaplus/friday_image_fe/e429586e4946e3d8a101e8dac84cb1d3a6591.jpg?imageMogr2/quality/80"
    budget = _MaoyanBudget(
        [{"id": 1365977, "nm": "无可替代的你", "sc": 0, "wish": 34235, "img": poster},
         {"id": 1552906, "nm": "神探之痕迹", "sc": 9.6, "img": poster.replace("6591", "0000")},
         {"id": 1604748, "nm": "什么意思夫妇", "sc": 0, "img": "http://example.com/not-maoyan.jpg"}],
        {"id": 1365977, "nm": "无可替代的你", "dur": 110, "cat": "剧情"})

    plugin._enrich_official_with_maoyan(movies, budget)

    first, second, third = movies
    assert first.poster_url == plugin._maoyan_poster_url(poster) and first.extra["poster_source"] == "maoyan"
    assert fetched == [("无可替代的你", first.poster_url)]
    assert second.poster_url.startswith("https://image.tmdb.org/") and second.extra["poster_source"] == "tmdb"
    assert third.poster_url.startswith("https://image.tmdb.org/") and "poster_source" not in third.extra
    # An address that is not Maoyan's own picture host is not taken.
    plain = _official_movies(plugin, ["什么意思夫妇"])
    plugin._enrich_official_with_maoyan(plain, _MaoyanBudget(
        [{"id": 1604748, "nm": "什么意思夫妇", "img": "http://example.com/not-maoyan.jpg"}], {}))
    assert not plain[0].poster_url and "poster_source" not in plain[0].extra
