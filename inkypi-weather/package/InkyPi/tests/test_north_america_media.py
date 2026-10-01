"""North America poster recovery preserves chart identity and source age."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeMovie
from plugins.box_office_top_movies.china_source import ChinaFetchBudget
import plugins.china_box_office_top_movies.china_box_office_top_movies as plugin_module
from plugins.china_box_office_top_movies.china_box_office_top_movies import ChinaBoxOfficeTopMovies
from plugins.china_box_office_top_movies.north_america_media import (
    canonical_movie_url, page_poster, reuse_posters,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext
from tests.test_china_box_office_top_movies import DummyDeviceConfig, canonical_theme


MOVIE = "https://www.the-numbers.com/movie/Avengers-Endgame-(2019)"
POSTER = "https://media.the-numbers.com/images/movie-posters/Avengers-Endgame-(2019).jpg"
PAGE = f"""<meta property="og:url" content="https://www.the-numbers.com/">
<h1>Avengers: Endgame (2019)</h1>
<img src='{POSTER}' alt='Avengers: Endgame'>
<h1>Cast and crew</h1><img src='https://advertiser.test/ad.jpg' alt='Advertisement'>"""


def test_official_detail_uses_first_movie_heading_and_actual_matching_poster():
    assert page_poster(PAGE, MOVIE + "#tab=summary", MOVIE) == POSTER
    assert canonical_movie_url(MOVIE.replace("(2019)", "%282019%29")) == MOVIE


@pytest.mark.parametrize("page, response_url", [
    (PAGE, MOVIE.replace("2019", "2026")),
    (PAGE.replace("Avengers: Endgame (2019)", "Another Movie (2019)"), MOVIE),
    (f'<link rel="canonical" href="{MOVIE.replace("2019", "2026")}">' + PAGE, MOVIE),
    (PAGE.replace('content="https://www.the-numbers.com/"', f'content="{MOVIE.replace("2019", "2026")}"'), MOVIE),
    ('<link rel="canonical" href="https://www.the-numbers.com/">' + PAGE, MOVIE),
    (PAGE.replace("media.the-numbers.com", "media.the-numbers.com.evil.test"), MOVIE),
    (PAGE.replace("/movie-posters/", "/advertisements/"), MOVIE),
    (PAGE.replace("(2019).jpg", "-placeholder.jpg"), MOVIE),
    (PAGE.replace("alt='Avengers: Endgame'", "alt='Another Movie'"), MOVIE),
    (PAGE.replace("(2019).jpg", "(2019).jpg?redirect=elsewhere"), MOVIE),
])
def test_rejects_other_movie_redirects_ads_placeholders_and_ambiguous_identity(page, response_url):
    assert page_poster(page, MOVIE, response_url) is None


@pytest.fixture
def chart(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INKYPI_CHINA_BOX_OFFICE_CACHE", str(tmp_path / "cache"))
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    now = datetime.now(timezone.utc)
    settings = {"sourceMode": "tmdb_cn_now_playing", "cacheHours": 6, "itemsCount": 5,
                "tmdbLanguage": "zh-CN", "_inkypi_theme": canonical_theme("day")}
    monkeypatch.setattr(plugin, "_source_now", lambda: now)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _: now)
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_: None)
    monkeypatch.setattr(plugin, "_render_chart", lambda *_: Image.new("RGB", (800, 480)))
    device = DummyDeviceConfig()
    movies = [BoxOfficeMovie(rank=1, title="Avengers Endgame: Encore", chart_url=MOVIE,
                             tmdb_id=1785181, weekend_gross="$26,000,000")]
    with Image.new("RGB", (150, 225), (75, 135, 195)) as image:
        encoded = BytesIO()
        image.save(encoded, format="JPEG")
        for number in range(2, 6):
            url = f"https://image.tmdb.org/t/p/w342/{number}.jpg"
            path = plugin._poster_store().save(url, image)
            movies.append(BoxOfficeMovie(rank=number, title=f"Film {number}",
                                         chart_url=f"https://www.the-numbers.com/movie/Film-{number}-(2026)",
                                         poster_url=url, poster_path=str(path), release_year="2026"))
    initial = {"version": plugin_module.STATE_VERSION,
               "cache_key": plugin._cache_key(settings, None, 5, device),
               "generated_at": (now - timedelta(hours=1)).isoformat(),
               "source_label": "The Numbers", "source_metadata": None,
               "movies": [movie.to_dict() for movie in movies],
               "poster_status": {"ready": 4, "total": 5, "attempts": 1,
                                 "settings_key": plugin._poster_repair_key(settings),
                                 "repair_possible": True, "retry_at": (now - timedelta(minutes=5)).isoformat()}}
    plugin._write_cache(initial)
    elapsed = [0.0]
    calls = []
    duration = [1.0]
    cancel = [False]
    parent = TaskContext.never_cancelled(deadline_monotonic=1000, clock=lambda: elapsed[0])

    class Client:
        def request_bytes(self, method, url, *, context, **kwargs):
            assert context.deadline_monotonic <= elapsed[0] + 20
            assert kwargs["allow_redirects"] is False
            calls.append(url)
            elapsed[0] += duration[0]
            if cancel[0]:
                parent.cancel_event.set()
            context.raise_if_cancelled()
            assert url in {MOVIE, POSTER}, "Repair must not fetch a chart or re-search the matched TMDb item"
            return SimpleNamespace(status=200, url=url, data=PAGE.encode() if url == MOVIE else encoded.getvalue(),
                                   headers={"Content-Type": "text/html" if url == MOVIE else "image/jpeg"})

        def close(self):
            pass

    monkeypatch.setattr(plugin_module, "ChinaFetchBudget", lambda: ChinaFetchBudget(client=Client(), parent=parent))
    # Cached repair budgets are created by the base class.
    monkeypatch.setattr("plugins.box_office_top_movies.box_office_top_movies.ChinaFetchBudget",
                        lambda: ChinaFetchBudget(client=Client(), parent=parent))
    return SimpleNamespace(plugin=plugin, settings=settings, device=device, now=now, initial=initial,
                           movies=movies, calls=calls, duration=duration, elapsed=elapsed, cancel=cancel)


def test_live_repair_fills_only_missing_poster_without_advancing_chart_age(chart):
    assert chart.plugin.get_live_refresh_state(chart.settings, chart.now)
    original_files = {Path(m.poster_path): Path(m.poster_path).read_bytes() for m in chart.movies[1:]}
    image = chart.plugin.generate_image({**chart.settings, "_movie_media_only": True}, chart.device)
    after = chart.plugin._read_cache()
    assert chart.calls == [MOVIE, POSTER]
    assert image.info["inkypi_media_ready"] == image.info["inkypi_media_total"] == 5
    assert after["generated_at"] == chart.initial["generated_at"]
    assert after["source_metadata"] == chart.initial["source_metadata"]
    assert [m["weekend_gross"] for m in after["movies"]] == [m["weekend_gross"] for m in chart.initial["movies"]]
    assert after["movies"][0]["poster_url"] == POSTER
    assert after["movies"][0]["extra"]["poster_source"] == "the_numbers"
    assert after["poster_status"]["retry_at"] is None
    assert not chart.plugin.get_live_refresh_state(chart.settings, chart.now + timedelta(minutes=30))
    assert all(path.read_bytes() == original for path, original in original_files.items())
    before_reuse = len(chart.calls)
    chart.plugin.generate_image(chart.settings, chart.device)
    assert len(chart.calls) == before_reuse


def test_same_canonical_film_reuses_verified_poster_despite_rerelease_alias(chart):
    old = chart.movies[1]
    new = BoxOfficeMovie(rank=1, title="Film 2: Encore", chart_url=old.chart_url, weekend_gross="$99")
    reuse_posters(chart.plugin, [new], [old.to_dict()])
    assert new.poster_path == old.poster_path
    assert new.rank == 1 and new.title == "Film 2: Encore" and new.weekend_gross == "$99"
    assert not chart.calls


def test_new_chart_refresh_reuses_poster_without_a_tmdb_request(chart, monkeypatch):
    old = chart.movies[1]
    movie = BoxOfficeMovie(rank=1, title="Film 2: Encore", chart_url=old.chart_url, weekend_gross="$99")
    monkeypatch.setattr(chart.plugin, "_load_movies", lambda *_: ([movie], "The Numbers"))

    def unavailable(*_args, **_kwargs):
        raise AssertionError("A verified same-film poster must not depend on another media request")

    monkeypatch.setattr(chart.plugin, "_enrich_with_tmdb", unavailable)
    monkeypatch.setattr(chart.plugin, "_download_posters", unavailable)
    image = chart.plugin.generate_image({**chart.settings, "forceRefresh": True}, chart.device)
    after = chart.plugin._read_cache()
    assert image.info["inkypi_media_ready"] == 1
    assert not chart.calls
    assert after["movies"][0]["poster_path"] == old.poster_path
    assert after["movies"][0]["weekend_gross"] == "$99"
    assert after["movies"][0]["title"] == "Film 2: Encore"
    assert after["generated_at"] != chart.initial["generated_at"]


@pytest.mark.parametrize("change", ["canonical", "year", "corrupt"])
def test_poster_reuse_rejects_other_identity_and_corrupt_files(chart, change):
    old = chart.movies[1]
    new = BoxOfficeMovie(rank=1, title=old.title, chart_url=old.chart_url)
    if change == "canonical":
        new.chart_url = new.chart_url.replace("Film-2", "Film-3")
    elif change == "year":
        old.release_year = "2025"
    else:
        Path(old.poster_path).write_bytes(b"invalid image")
    reuse_posters(chart.plugin, [new], [old.to_dict()])
    assert not new.poster_path


def test_repair_budget_exhaustion_retries_later_without_changing_source_time(chart):
    chart.duration[0] = 20
    image = chart.plugin.generate_image({**chart.settings, "_movie_media_only": True}, chart.device)
    after = chart.plugin._read_cache()
    assert chart.elapsed[0] == 20 and chart.calls == [MOVIE]
    assert image.info["inkypi_media_ready"] == 4
    assert after["generated_at"] == chart.initial["generated_at"]
    assert after["poster_status"]["attempts"] == 2
    assert after["poster_status"]["retry_at"] is not None


def test_parent_cancellation_does_not_persist_partial_repair(chart):
    before = chart.plugin._cache_path().read_bytes()
    chart.cancel[0] = True
    with pytest.raises(TaskCancelled):
        chart.plugin.generate_image({**chart.settings, "_movie_media_only": True}, chart.device)
    assert chart.plugin._cache_path().read_bytes() == before


def test_expired_or_legacy_chart_cannot_enter_north_america_repair(chart):
    assert chart.plugin.get_live_refresh_state(chart.settings, chart.now + timedelta(hours=7)) is None
    assert not chart.plugin._supports_movie_media_repair({"sourceMode": "legacy_tmdb_cn_now_playing"})
    for field, value in (("source_label", "TMDb Mainland Popular"), ("version", "old")):
        cache = {**chart.initial, field: value}
        assert not chart.plugin._movie_media_source_is_current(cache, chart.settings, chart.now)


def test_tmdb_clip_without_year_cannot_displace_canonical_original_movie(chart):
    clip = {"id": 1785181, "title": "Avengers Endgame: Encore", "release_date": "", "poster_path": None}
    original = {"id": 299534, "title": "Avengers: Endgame", "release_date": "2019-04-24"}
    assert chart.plugin._select_tmdb_search_result(chart.movies[0], [clip, original]) == original
    assert chart.plugin._select_tmdb_search_result(chart.movies[0], [clip]) is None


def test_theme_only_does_not_fetch_or_rewrite_source_for_missing_cover(chart):
    before = chart.plugin._cache_path().read_bytes()
    chart.plugin.generate_image({**chart.settings, "_theme_render_only": True}, chart.device)
    assert not chart.calls
    assert chart.plugin._cache_path().read_bytes() == before


def test_footer_names_both_actual_poster_providers(chart):
    chart.movies[0].poster_url = POSTER
    chart.movies[0].poster_path = "/verified/poster.jpg"
    chart.movies[0].extra["poster_source"] = "the_numbers"
    assert chart.plugin._footer_for_source("The Numbers", chart.movies) == "Data: The Numbers | Posters: TMDb / The Numbers"
