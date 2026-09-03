"""Poster completeness must not spend the mainland chart's freshness budget."""

import io
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.box_office_top_movies.china_source as source_module
from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeTopMovies, ZGDYPW_REALTIME_URL
from runtime.refresh_contracts import TaskContext
from tests.test_box_office_top_movies import EnvDeviceConfig, canonical_theme
from tests.test_http_client import FakeResponse
from tests.test_official_china_box_office import NOW, official_page
from utils.http_client import HttpClient


def test_official_five_basic_posters_finish_before_optional_enrichment(tmp_path, monkeypatch):
    """Five searches/downloads fit in 17s; optional details must not crowd them out."""
    elapsed = [0.0]
    parent = TaskContext.never_cancelled(deadline_monotonic=100, clock=lambda: elapsed[0])
    colors = [(180, 30, 40), (20, 170, 70), (35, 80, 190), (200, 120, 25), (130, 50, 170)]
    rows = [
        {
            "code": f"film-{number}",
            "name": f"预算电影{number}",
            "salesInWanDesc": str(100 - number),
            "salesRateDesc": f"{30 - number}%",
            "sumSalesDesc": f"{number}.23亿",
        }
        for number in range(1, 6)
    ]
    poster_bytes = {}
    for number, color in enumerate(colors, 1):
        buffer = io.BytesIO()
        Image.new("RGB", (120, 180), color).save(buffer, format="PNG")
        poster_bytes[f"/{number}.png"] = buffer.getvalue()

    class TimedSession:
        def __init__(self):
            self.calls = []
            self.responses = []
            self.deadlines = []

        def request(self, method, url, **kwargs):
            budget = source_module.ACTIVE_BUDGET.get()
            assert isinstance(budget, source_module.ChinaFetchBudget)
            self.deadlines.append(budget.context.deadline_monotonic)
            params = kwargs.get("params", {})
            path = urlsplit(url).path
            content_type = "application/json"
            if url == ZGDYPW_REALTIME_URL:
                kind, duration = "chart", 2.0
                payload = official_page(rows=rows).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif path.endswith("/search/movie"):
                kind, duration = "search", 1.0
                number = int(params["query"][-1])
                payload = {"results": [{
                    "id": number,
                    "title": rows[number - 1]["name"],
                    "original_title": f"Film {number}",
                    "release_date": "2026-08-28",
                    "poster_path": f"/{number}.png",
                }]}
            elif urlsplit(url).hostname == "image.tmdb.org":
                kind, duration = "poster", 2.0
                payload = poster_bytes["/" + path.rsplit("/", 1)[-1]]
                content_type = "image/png"
            elif path.endswith("/images"):
                kind, duration = "optional_images", 1.0
                payload = {"posters": []}
            elif path.endswith("/alternative_titles"):
                kind, duration = "optional_titles", 1.0
                payload = {"titles": []}
            elif "/movie/" in path:
                kind, duration = "optional_detail", 1.0
                payload = {"title": "English Film", "original_title": "English Film"}
            else:
                raise AssertionError(f"Unexpected HTTP request: {url}")
            self.calls.append((kind, elapsed[0]))
            # HTTP observes the same hard deadline, including a partially completed request.
            elapsed[0] += min(duration, budget.remaining_seconds())
            if not isinstance(payload, bytes):
                payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response = FakeResponse(200, payload, headers={"Content-Type": content_type})
            response.url = url
            self.responses.append(response)
            return response

    session = TimedSession()
    monkeypatch.setattr(source_module, "IsolatedChinaHttpClient", lambda: HttpClient(session=session, max_attempts=1))
    monkeypatch.setattr(source_module, "current_task_context", lambda: parent)
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: NOW)
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_args: None)

    image = plugin.generate_image(
        {
            "sourceMode": "official_china",
            "forceRefresh": True,
            "tmdbLanguage": "zh-CN",
            "tmdbRegion": "CN",
            "_inkypi_theme": canonical_theme("day"),
        },
        EnvDeviceConfig({"TMDB_API_KEY": "test-key"}),
    )

    assert image.size == (800, 480)
    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert elapsed[0] <= 20
    assert set(session.deadlines) == {20.0}
    assert all(response.closed for response in session.responses)
    cache = json.loads(plugin._cache_path().read_text(encoding="utf-8"))
    assert cache["generated_at"] == NOW.isoformat()
    assert cache["source_metadata"] == {
        "statistic_date": "2026-09-03",
        "source_updated_at": NOW.isoformat(),
        "fetched_at": NOW.isoformat(),
        "metric_scope": "comprehensive_including_service_fee",
        "source": "zgdypw_realtime",
        "timezone": "Asia/Shanghai",
    }
    assert [(movie["title"], movie["weekend_gross"], movie["total_gross"]) for movie in cache["movies"]] == [
        (row["name"], row["salesRateDesc"], row["sumSalesDesc"]) for row in rows
    ]
    ready = [movie for movie in cache["movies"] if Path(movie.get("poster_path") or "").is_file()]
    assert len(ready) == 5, f"Only {len(ready)}/5 local posters after requests: {session.calls}"
    for movie, color in zip(cache["movies"], colors):
        with Image.open(movie["poster_path"]) as poster:
            poster.load()
            assert poster.size == (120, 180)
            assert max(abs(a - b) for a, b in zip(poster.convert("RGB").getpixel((60, 90)), color)) <= 3

    # Actual hero and four thumbnail centers must contain the downloaded images.
    for point, color in zip([(120, 258), (261, 223), (261, 283), (261, 343), (261, 403)], colors):
        assert max(abs(a - b) for a, b in zip(image.getpixel(point), color)) <= 3


def test_missing_search_poster_uses_images_after_basic_posters_and_reuses_id(tmp_path, monkeypatch):
    """An identified movie with no search poster can recover in a later bounded repair."""
    elapsed = [0.0]
    parent = TaskContext.never_cancelled(deadline_monotonic=100, clock=lambda: elapsed[0])
    rows = [{
        "code": f"missing-poster-{number}", "name": f"待补封面{number}",
        "salesInWanDesc": str(100 - number), "salesRateDesc": f"{30 - number}%",
        "sumSalesDesc": f"{number}.23亿",
    } for number in range(1, 6)]
    buffer = io.BytesIO()
    Image.new("RGB", (120, 180), (40, 130, 190)).save(buffer, format="PNG")

    class TimedSession:
        def __init__(self):
            self.calls = []
            self.images_available = False
            self.responses = []
            self.deadlines = []

        def request(self, method, url, **kwargs):
            budget = source_module.ACTIVE_BUDGET.get()
            assert isinstance(budget, source_module.ChinaFetchBudget)
            self.deadlines.append(budget.context.deadline_monotonic)
            path = urlsplit(url).path
            content_type = "application/json"
            if url == ZGDYPW_REALTIME_URL:
                kind = "chart"
                payload = official_page(rows=rows).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif path.endswith("/search/movie"):
                number = int(kwargs["params"]["query"][-1])
                kind = f"search-{number}"
                payload = {"results": [{
                    "id": number, "title": rows[number - 1]["name"],
                    "original_title": f"Film {number}", "release_date": "2026-08-28",
                    "poster_path": None if number == 1 else f"/{number}.png",
                }]}
            elif urlsplit(url).hostname == "image.tmdb.org":
                number = int(path.rsplit("/", 1)[-1].split(".")[0])
                kind = f"poster-{number}"
                payload = buffer.getvalue()
                content_type = "image/png"
            elif path.endswith("/movie/1/images"):
                kind = "images-1"
                payload = {"posters": [{
                    "file_path": "/1.png", "iso_639_1": "zh", "width": 120,
                    "height": 180, "vote_count": 3, "vote_average": 7,
                }] if self.images_available else []}
            else:
                raise AssertionError(f"Unexpected HTTP request: {url}")
            self.calls.append(kind)
            elapsed[0] += min(1.0, budget.remaining_seconds())
            if not isinstance(payload, bytes):
                payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response = FakeResponse(200, payload, headers={"Content-Type": content_type})
            response.url = url
            self.responses.append(response)
            return response

    session = TimedSession()
    monkeypatch.setattr(source_module, "IsolatedChinaHttpClient", lambda: HttpClient(session=session, max_attempts=1))
    monkeypatch.setattr(source_module, "current_task_context", lambda: parent)
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: NOW)
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_args: None)
    settings = {
        "sourceMode": "official_china", "forceRefresh": True,
        "tmdbLanguage": "zh-CN", "tmdbRegion": "CN",
        "_inkypi_theme": canonical_theme("day"),
    }
    device = EnvDeviceConfig({"TMDB_API_KEY": "test-key"})

    first_image = plugin.generate_image(settings, device)

    assert read_source_provenance(first_image) is SourceProvenance.LIVE
    assert "images-1" in session.calls, "A matched movie's image collection was never checked"
    assert session.calls.index("images-1") > max(session.calls.index(f"poster-{number}") for number in range(2, 6))
    first_cache = json.loads(plugin._cache_path().read_text(encoding="utf-8"))
    assert first_cache["movies"][0]["tmdb_id"] == 1
    assert not first_cache["movies"][0]["poster_path"]
    assert all(Path(movie["poster_path"]).is_file() for movie in first_cache["movies"][1:])
    assert elapsed[0] <= 20

    # The image collection becomes available without a new chart or identity search.
    elapsed[0] = 0.0
    session.calls.clear()
    session.images_available = True
    monkeypatch.setattr(plugin, "_cache_is_fresh", lambda *_args: True)
    repaired_image = plugin.generate_image({**settings, "forceRefresh": False}, device)

    assert read_source_provenance(repaired_image) is SourceProvenance.FRESH_CACHE
    assert session.calls == ["images-1", "poster-1"]
    assert elapsed[0] <= 20
    assert set(session.deadlines) == {20.0}
    assert all(response.closed for response in session.responses)
    repaired_cache = json.loads(plugin._cache_path().read_text(encoding="utf-8"))
    assert repaired_cache["generated_at"] == first_cache["generated_at"]
    assert repaired_cache["source_metadata"] == first_cache["source_metadata"]
    assert all(Path(movie["poster_path"]).is_file() for movie in repaired_cache["movies"])
    assert repaired_image.getpixel((120, 258)) == (40, 130, 190)


def test_slow_images_lookup_does_not_starve_other_identified_movie(tmp_path, monkeypatch):
    elapsed = [0.0]
    parent = TaskContext.never_cancelled(deadline_monotonic=100, clock=lambda: elapsed[0])
    rows = [{
        "code": f"slow-images-{number}", "name": f"海报等待{number}",
        "salesInWanDesc": "50", "salesRateDesc": "50%", "sumSalesDesc": "1.23亿",
    } for number in (1, 2)]
    buffer = io.BytesIO()
    Image.new("RGB", (120, 180), (90, 140, 70)).save(buffer, format="PNG")

    class TimedSession:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            budget = source_module.ACTIVE_BUDGET.get()
            assert isinstance(budget, source_module.ChinaFetchBudget)
            assert budget.context.deadline_monotonic == 20
            path = urlsplit(url).path
            duration = 1.0
            content_type = "application/json"
            if url == ZGDYPW_REALTIME_URL:
                kind = "chart"
                payload = official_page(rows=rows).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            elif path.endswith("/search/movie"):
                number = int(kwargs["params"]["query"][-1])
                kind = f"search-{number}"
                payload = {"results": [{
                    "id": number, "title": rows[number - 1]["name"],
                    "original_title": f"Film {number}", "release_date": "2026-08-28",
                    "poster_path": None,
                }]}
            elif path.endswith("/movie/1/images"):
                kind = "images-1"
                duration = budget.remaining_seconds()
                payload = {"posters": []}
            elif path.endswith("/movie/2/images"):
                kind = "images-2"
                payload = {"posters": [{
                    "file_path": "/2.png", "iso_639_1": "zh", "width": 120,
                    "height": 180, "vote_count": 3, "vote_average": 7,
                }]}
            elif urlsplit(url).hostname == "image.tmdb.org" and path.endswith("/2.png"):
                kind = "poster-2"
                payload = buffer.getvalue()
                content_type = "image/png"
            else:
                raise AssertionError(f"Unexpected HTTP request: {url}")
            self.calls.append(kind)
            elapsed[0] += min(duration, budget.remaining_seconds())
            if not isinstance(payload, bytes):
                payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response = FakeResponse(200, payload, headers={"Content-Type": content_type})
            response.url = url
            return response

    session = TimedSession()
    monkeypatch.setattr(source_module, "IsolatedChinaHttpClient", lambda: HttpClient(session=session, max_attempts=1))
    monkeypatch.setattr(source_module, "current_task_context", lambda: parent)
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_source_now", lambda: NOW)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: NOW)
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_args: None)
    settings = {
        "sourceMode": "official_china", "itemsCount": 2, "forceRefresh": True,
        "tmdbLanguage": "zh-CN", "tmdbRegion": "CN",
        "_inkypi_theme": canonical_theme("day"),
    }
    device = EnvDeviceConfig({"TMDB_API_KEY": "test-key"})

    first_image = plugin.generate_image(settings, device)

    assert read_source_provenance(first_image) is SourceProvenance.LIVE
    assert session.calls == ["chart", "search-1", "search-2", "images-1"]
    first_cache = json.loads(plugin._cache_path().read_text(encoding="utf-8"))
    assert [movie["tmdb_id"] for movie in first_cache["movies"]] == [1, 2]
    assert all(not movie["poster_path"] for movie in first_cache["movies"])
    assert elapsed[0] == 20

    elapsed[0] = 0.0
    session.calls.clear()
    monkeypatch.setattr(plugin, "_cache_is_fresh", lambda *_args: True)
    repaired_image = plugin.generate_image({**settings, "forceRefresh": False}, device)

    assert session.calls[:2] == ["images-2", "poster-2"], "Slow first images lookup starved the other matched movie again"
    assert read_source_provenance(repaired_image) is SourceProvenance.FRESH_CACHE
    assert not any(call.startswith("search-") for call in session.calls)
    assert elapsed[0] <= 20
    repaired_cache = json.loads(plugin._cache_path().read_text(encoding="utf-8"))
    assert repaired_cache["generated_at"] == first_cache["generated_at"]
    assert repaired_cache["source_metadata"] == first_cache["source_metadata"]
    assert not repaired_cache["movies"][0]["poster_path"]
    with Image.open(repaired_cache["movies"][1]["poster_path"]) as poster:
        poster.load()
        assert poster.size == (120, 180)
