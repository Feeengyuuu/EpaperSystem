"""Recover mainland posters without changing the age or identity of chart data."""

import copy
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.box_office_top_movies.box_office_top_movies as box_module
import plugins.box_office_top_movies.china_source as source_module
from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeMovie, BoxOfficeTopMovies
from runtime.refresh_contracts import TaskCancelled, TaskContext
from tests.test_box_office_top_movies import EnvDeviceConfig, canonical_theme
from tests.test_http_client import FakeResponse
from tests.test_official_china_box_office import NOW, official_page
from utils.http_client import HttpClient


def source_facts(cache):
    """Fields whose age and meaning cannot change when only media improves."""
    return copy.deepcopy({
        "generated_at": cache["generated_at"],
        "source_label": cache["source_label"],
        "source_metadata": cache["source_metadata"],
        "movies": [
            {
                key: movie.get(key)
                for key in ("rank", "title", "weekend_gross", "total_gross", "theaters", "weeks", "chart_url")
            } | {"source_fields": {
                key: movie["extra"].get(key)
                for key in ("source", "zgdypw_movie_code", "official_chinese_title", "today_box_wan", "box_rate", "source_metadata")
            }}
            for movie in cache["movies"]
        ],
    })


@pytest.fixture
def chart(tmp_path, monkeypatch):
    class Chart:
        def __init__(self):
            self.elapsed = 0.0
            self.now = NOW
            self.parent = TaskContext.never_cancelled(deadline_monotonic=10_000, clock=lambda: self.elapsed)
            self.calls = []
            self.durations = {"chart": 1.0, "search": 1.0, "poster": 1.0, "optional": 1.0}
            self.cancel_on = None
            self.result_year = "2026"
            self.rows = [self.row(number) for number in range(1, 6)]
            self.settings = {
                "sourceMode": "official_china", "tmdbLanguage": "zh-CN", "tmdbRegion": "CN",
                "_inkypi_theme": canonical_theme("day"),
            }
            self.device = EnvDeviceConfig({"TMDB_API_KEY": "test-key"})
            self.plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
            self.responses = []
            self.budget_deadlines = []

        @staticmethod
        def row(number):
            return {"code": f"film-{number}", "name": f"缓存电影{number}", "releaseDays": 8,
                    "salesInWanDesc": str(100 - number), "salesRateDesc": f"{30 - number}%",
                    "sumSalesDesc": f"{number}.23亿"}

        def metadata(self, when):
            return {"source": "zgdypw_realtime", "statistic_date": "2026-09-03",
                    "source_updated_at": (when - timedelta(seconds=30)).isoformat(),
                    "fetched_at": when.isoformat(), "metric_scope": "comprehensive_including_service_fee",
                    "timezone": "Asia/Shanghai"}

        def seed(self, count=5):
            when = self.now - timedelta(minutes=2)
            metadata = self.metadata(when)
            movies = []
            for number, row in enumerate(self.rows[:count], 1):
                movie = BoxOfficeMovie(
                    rank=number, title=row["name"], weekend_gross="1%", total_gross="0.01亿",
                    weeks="7", chart_url=box_module.ZGDYPW_REALTIME_URL,
                    tmdb_id=100 + number, release_year="2026",
                    poster_url=f"{box_module.TMDB_IMAGE_BASE}/saved-{number}.png",
                    extra={"source": "zgdypw_realtime", "zgdypw_movie_code": row["code"],
                           "official_chinese_title": row["name"], "today_box_wan": "1", "box_rate": "1%",
                           "source_metadata": copy.deepcopy(metadata), "poster_source": "tmdb",
                           "english_title": f"Saved Film {number}"},
                )
                path = self.plugin._poster_cache_path(movie)
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (90, 135), (180, number * 20, 30)).save(path, format="JPEG")
                movie.poster_path = str(path)
                movies.append(movie.to_dict())
            payload = {"version": self.plugin._cache_state_version(),
                       "cache_key": self.plugin._cache_key(self.settings, (800, 480), 5, self.device),
                       "generated_at": when.isoformat(), "source_label": box_module.ZGDYPW_SOURCE_LABEL,
                       "source_metadata": metadata, "movies": movies}
            self.plugin._write_cache(payload)
            return payload

        def cache(self):
            return json.loads(self.plugin._cache_path().read_text(encoding="utf-8"))

        def generate(self, **settings):
            return self.plugin.generate_image({**self.settings, **settings}, self.device)

        def request(self, method, url, **kwargs):
            budget = source_module.ACTIVE_BUDGET.get()
            assert isinstance(budget, source_module.ChinaFetchBudget)
            self.budget_deadlines.append(budget.context.deadline_monotonic)
            path, params = urlsplit(url).path, kwargs.get("params", {})
            content_type, title = "application/json", None
            if url == box_module.ZGDYPW_REALTIME_URL:
                kind = "chart"
                payload = official_page(when=self.now - timedelta(seconds=30), rows=self.rows).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif path.endswith("/search/movie"):
                kind, title = "search", params["query"]
                number = next(index for index, row in enumerate(self.rows, 1) if row["name"] == title)
                payload = {"results": [{"id": 200 + number, "title": title,
                                        "original_title": f"Current Film {number}",
                                        "release_date": f"{self.result_year}-08-28",
                                        "poster_path": f"/current-{number}.png"}]}
            elif urlsplit(url).hostname == "image.tmdb.org":
                kind = "poster"
                number = int(path.rsplit("-", 1)[-1].split(".")[0])
                buffer = io.BytesIO()
                Image.new("RGB", (90, 135), (20, number * 20, 180)).save(buffer, format="PNG")
                payload, content_type = buffer.getvalue(), "image/png"
            elif path.endswith("/images"):
                kind, payload = "optional", {"posters": []}
            elif path.endswith("/alternative_titles"):
                kind, payload = "optional", {"titles": []}
            elif "/movie/" in path:
                kind, payload = "optional", {"title": "Current English Film", "original_title": "Current English Film"}
            else:
                raise AssertionError(f"Unexpected HTTP request: {url}")
            self.calls.append((kind, title, self.elapsed))
            self.elapsed += min(self.durations[kind], budget.remaining_seconds())
            if self.cancel_on == kind:
                self.parent.cancel_event.set()
            if not isinstance(payload, bytes):
                payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response = FakeResponse(200, payload, headers={"Content-Type": content_type})
            response.url = url
            self.responses.append(response)
            return response

    value = Chart()

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return value.now.astimezone(tz) if tz is not None else value.now.replace(tzinfo=None)

    monkeypatch.setattr(box_module, "datetime", FrozenDatetime)
    monkeypatch.setattr(source_module, "IsolatedChinaHttpClient", lambda: HttpClient(session=value, max_attempts=1))
    monkeypatch.setattr(source_module, "current_task_context", lambda: value.parent)
    monkeypatch.setattr(box_module, "get_http_session", lambda: pytest.fail("Mainland media must use its shared budget"))
    monkeypatch.setattr(value.plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(value.plugin, "_source_now", lambda: value.now)
    monkeypatch.setattr(value.plugin, "_now_for_device", lambda _device: value.now)
    monkeypatch.setattr(value.plugin, "_write_box_office_context", lambda *_args: None)
    return value


def assert_valid_posters(cache):
    for movie in cache["movies"]:
        path = Path(movie.get("poster_path") or "")
        assert path.is_file(), f"Missing poster for {movie['title']}"
        with Image.open(path) as poster:
            poster.load()
            assert poster.size == (90, 135)


def test_new_chart_reuses_same_official_identity_media_and_keeps_new_statistics(chart):
    before = chart.seed()
    chart.now += timedelta(minutes=1)
    image = chart.generate(forceRefresh=True)
    after = chart.cache()

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert after["generated_at"] == chart.now.isoformat()
    assert after["source_metadata"] == chart.metadata(chart.now)
    assert [movie["weekend_gross"] for movie in after["movies"]] == [row["salesRateDesc"] for row in chart.rows]
    assert [movie["total_gross"] for movie in after["movies"]] == [row["sumSalesDesc"] for row in chart.rows]
    assert [movie["poster_path"] for movie in after["movies"]] == [movie["poster_path"] for movie in before["movies"]]
    assert [movie["tmdb_id"] for movie in after["movies"]] == [movie["tmdb_id"] for movie in before["movies"]]
    assert not any(kind in {"search", "poster"} for kind, _title, _time in chart.calls)
    assert_valid_posters(after)


def test_fresh_chart_repairs_missing_posters_without_renewing_data_time(chart):
    before = chart.seed(count=3)
    Path(before["movies"][1]["poster_path"]).unlink()
    before["movies"][2].update(tmdb_id=None, poster_url="", poster_path="")
    chart.plugin._write_cache(before)
    image = chart.generate()
    after = chart.cache()

    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert source_facts(after) == source_facts(before)
    assert_valid_posters(after)
    assert any(kind == "poster" for kind, _title, _time in chart.calls)
    assert not any(kind == "chart" for kind, _title, _time in chart.calls)
    assert not any(kind == "search" and title == before["movies"][0]["title"] for kind, title, _time in chart.calls)
    assert chart.elapsed <= 20


def test_theme_only_missing_posters_never_start_network_or_rewrite_cache(chart):
    before = chart.seed(count=1)
    Path(before["movies"][0]["poster_path"]).unlink()
    path = chart.plugin._cache_path()
    content, modified = path.read_bytes(), path.stat().st_mtime_ns

    image = chart.generate(_theme_render_only=True, _inkypi_theme=canonical_theme("night"))

    assert image.size == (800, 480)
    assert not chart.calls
    assert path.read_bytes() == content
    assert path.stat().st_mtime_ns == modified


@pytest.mark.parametrize("changed", ["code", "title", "year"])
def test_changed_movie_identity_does_not_inherit_cached_media(chart, monkeypatch, changed):
    before = chart.seed(count=1)
    chart.rows = chart.rows[:1]
    if changed == "code":
        chart.rows[0]["code"] = "replacement-film-code"
    elif changed == "title":
        chart.rows[0]["name"] = "另一部同档期电影"
    else:
        # Model an already known release year; current official payloads do not supply one.
        parse = chart.plugin._parse_zgdypw_realtime

        def parse_known_year(*args, **kwargs):
            movies = parse(*args, **kwargs)
            for movie in movies:
                movie.release_year = "2027"
            return movies

        monkeypatch.setattr(chart.plugin, "_parse_zgdypw_realtime", parse_known_year)
        chart.result_year = "2027"

    image = chart.generate(forceRefresh=True)
    after = chart.cache()

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert after["movies"][0]["poster_url"] != before["movies"][0]["poster_url"]
    assert after["movies"][0]["poster_path"] != before["movies"][0]["poster_path"]
    assert after["movies"][0]["tmdb_id"] != before["movies"][0]["tmdb_id"]
    assert any(kind == "search" for kind, _title, _time in chart.calls)
    assert_valid_posters(after)


def test_corrupt_cached_poster_is_downloaded_again_with_original_data_age(chart):
    before = chart.seed(count=1)
    Path(before["movies"][0]["poster_path"]).write_bytes(b"nonempty but not an image")

    image = chart.generate()
    after = chart.cache()

    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert source_facts(after) == source_facts(before)
    assert_valid_posters(after)
    assert any(kind == "poster" for kind, _title, _time in chart.calls)
    assert not any(kind == "chart" for kind, _title, _time in chart.calls)


def test_parent_cancellation_during_poster_work_does_not_commit_chart_cache(chart):
    before = chart.seed(count=1)
    before["movies"][0].update(tmdb_id=None, poster_url="", poster_path="")
    chart.plugin._write_cache(before)
    chart.rows = chart.rows[:1]
    path = chart.plugin._cache_path()
    content, modified = path.read_bytes(), path.stat().st_mtime_ns
    chart.cancel_on = "poster"

    with pytest.raises(TaskCancelled):
        chart.generate(forceRefresh=True)

    assert path.read_bytes() == content
    assert path.stat().st_mtime_ns == modified
    assert all(response.closed for response in chart.responses)


def test_slow_network_resumes_missing_posters_without_researching_completed_movies(chart):
    chart.durations.update(chart=4.0, search=3.5, poster=3.5)
    chart.generate(forceRefresh=True)
    first = chart.cache()
    facts = source_facts(first)
    complete = {movie["title"] for movie in first["movies"] if Path(movie.get("poster_path") or "").is_file()}
    assert 0 < len(complete) < 5
    assert chart.elapsed <= 20

    for _attempt in range(2):
        chart.now += timedelta(minutes=1)
        started, call_start = chart.elapsed, len(chart.calls)
        image = chart.generate()
        after = chart.cache()
        assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
        assert source_facts(after) == facts
        assert chart.elapsed - started <= 20
        assert not any(kind == "search" and title in complete for kind, title, _time in chart.calls[call_start:])
        recovered = {movie["title"] for movie in after["movies"] if Path(movie.get("poster_path") or "").is_file()}
        assert complete < recovered
        complete = recovered

    assert len(complete) == 5
    assert_valid_posters(after)
    assert sum(kind == "chart" for kind, _title, _time in chart.calls) == 1
