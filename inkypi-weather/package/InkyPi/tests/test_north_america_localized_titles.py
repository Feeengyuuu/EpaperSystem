"""North America films pick up their Simplified Chinese title once TMDB has one."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from plugins.base_plugin.render_provenance import SourceProvenance
from plugins.box_office_top_movies import two_markets
from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeMovie, BoxOfficeTopMovies
from plugins.box_office_top_movies.china_source import ChinaFetchBudget
import plugins.china_box_office_top_movies.china_box_office_top_movies as america_module
from plugins.china_box_office_top_movies.north_america_media import (
    LOCALIZED_TITLE_CHECK, LOCALIZED_TITLE_RECHECK, LOCALIZED_TITLE_RETRY, LOCALIZED_TITLES_PER_PASS,
)
from runtime.refresh_contracts import TaskContext
from tests.test_box_office_top_movies import canonical_theme
from tests.test_box_office_two_markets import SETTINGS, china_movies, snapshot
from tests.test_china_box_office_top_movies import DummyDeviceConfig, EnvDeviceConfig
from utils.http_client import HttpStatusError

TMDB = "https://api.themoviedb.org/3/"
PAGE_SETTINGS = {**SETTINGS, "_inkypi_theme": canonical_theme("day")}
# (title, The Numbers slug, TMDB id, Chinese title already known)
FILMS = [("Godzilla Minus Zero", "Godzilla-Minus-Zero-(2026-Japan)", 101, "哥斯拉-0.0"),
         ("The Cat in the Hat", "Cat-in-the-Hat-The-(2026)", 102, "戴帽子的猫"),
         ("You Can See Everything", "You-Can-See-Everything-(2026)", 103, "你能看到一切"),
         ("Drummer Boy", "Drummer-Boy-(2026)", 104, "鼓手男孩"),
         ("Ramayana: Part 1", "Ramayana-Part-1-(2026-India)", 105, "罗摩衍那：第一部")]
TMDB_KEY_NAMES = ("TMDB_BEARER_TOKEN", "TMDB_READ_ACCESS_TOKEN", "TMDB_ACCESS_TOKEN", "TMDB_Access_Token",
                  "THEMOVIEDB_BEARER_TOKEN", "TMDB_API_KEY", "THEMOVIEDB_API_KEY")


def movie_url(slug):
    return "https://www.the-numbers.com/movie/" + slug


def official_poster(slug):
    return f"https://media.the-numbers.com/images/movie-posters/{slug}.jpg"


class FakeTmdb:
    """TMDB's search, details and alternative titles, plus the publisher's poster files."""

    def __init__(self, jpeg):
        self.jpeg = jpeg
        self.calls = []
        self.titles = {}        # id -> the title TMDB returns for zh-CN
        self.alternatives = {}  # id -> the CN alternative title
        self.search = {}        # query -> results
        self.failing = False

    def request_bytes(self, method, url, *, context, **kwargs):
        context.raise_if_cancelled()
        params = kwargs.get("params") or {}
        if url.startswith(TMDB):
            assert params.get("api_key") == "test-key"
            path = url[len(TMDB):]
            if path == "search/movie":
                self.calls.append("search:" + params["query"])
                body = {"results": self.search.get(params["query"], [])}
            elif path.endswith("/alternative_titles"):
                movie_id = int(path.split("/")[1])
                self.calls.append(f"alternatives:{movie_id}")
                title = self.alternatives.get(movie_id)
                body = {"titles": [{"iso_3166_1": "CN", "title": title}] if title else []}
            else:
                movie_id = int(path.split("/")[1])
                assert params.get("language") == "zh-CN"
                self.calls.append(f"details:{movie_id}")
                body = {"id": movie_id, "title": self.titles.get(movie_id, "Untranslated")}
            if self.failing:
                raise HttpStatusError("GET", url, 404)
            return SimpleNamespace(status=200, url=url, data=json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json"})
        self.calls.append(url)
        return SimpleNamespace(status=200, url=url, data=self.jpeg, headers={"Content-Type": "image/jpeg"})

    def close(self):
        pass


@pytest.fixture
def frame(tmp_path, monkeypatch):
    """The two-market page over a fresh North America chart whose five films all have posters."""
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INKYPI_CHINA_BOX_OFFICE_CACHE", str(tmp_path / "america"))
    monkeypatch.setenv("INKYPI_BOX_OFFICE_CACHE", str(tmp_path / "china"))
    for name in TMDB_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    page = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    america = page._north_america_market()
    started = datetime.now(timezone.utc)
    clock = [started]
    settings = two_markets.north_america_settings(PAGE_SETTINGS)
    for plugin in (page, america):
        monkeypatch.setattr(plugin, "_source_now", lambda: clock[0])
        monkeypatch.setattr(plugin, "_now_for_device", lambda _device: clock[0])
        monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_: None)
    monkeypatch.setattr(page, "_market_snapshot", lambda *_: snapshot(china_movies(), "中国电影票房", ready=5))

    encoded = BytesIO()
    with Image.new("RGB", (150, 225), (75, 135, 195)) as image:
        image.save(encoded, format="JPEG")
        movies = []
        for rank, (title, slug, tmdb_id, chinese) in enumerate(FILMS, start=1):
            url = f"https://image.tmdb.org/t/p/w342/{tmdb_id}.jpg"
            path = america._poster_store().save(url, image)
            movies.append(BoxOfficeMovie(rank=rank, title=title, chart_url=movie_url(slug), tmdb_id=tmdb_id,
                                         poster_url=url, poster_path=str(path), release_year="2026",
                                         localized_title=chinese, localized_language="zh-CN",
                                         weekend_gross=f"${rank},000,000", extra={"poster_source": "tmdb"}))
    tmdb = FakeTmdb(encoded.getvalue())
    elapsed = [0.0]
    parent = TaskContext.never_cancelled(deadline_monotonic=1000, clock=lambda: elapsed[0])
    # A new chart's budget is made by the North America plugin, a cached pass's by the base class.
    for module in ("plugins.china_box_office_top_movies.china_box_office_top_movies",
                   "plugins.box_office_top_movies.box_office_top_movies"):
        monkeypatch.setattr(module + ".ChinaFetchBudget", lambda: ChinaFetchBudget(client=tmdb, parent=parent))

    drawn = []
    original = two_markets.ImageDraw.ImageDraw.text

    def capture(draw, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(two_markets.ImageDraw.ImageDraw, "text", capture)

    def save(device):
        america._write_cache({
            "version": america_module.STATE_VERSION, "cache_key": america._cache_key(settings, None, 5, device),
            "generated_at": (started - timedelta(hours=1)).isoformat(), "source_label": "The Numbers",
            "source_metadata": None, "movies": [movie.to_dict() for movie in movies],
            "poster_status": {"ready": 5, "total": 5, "attempts": 0,
                              "settings_key": america._poster_repair_key(settings),
                              "repair_possible": True, "retry_at": None}})

    def draw(*, media_only=False, device=None, **extra):
        del drawn[:]
        image = page.generate_image({**PAGE_SETTINGS, "_movie_media_only": media_only, **extra},
                                    device or keyed)
        return image, list(drawn)

    def cached():
        return [BoxOfficeMovie.from_dict(row) for row in america._read_cache()["movies"]]

    keyed = EnvDeviceConfig({"TMDB_API_KEY": "test-key"})
    return SimpleNamespace(page=page, america=america, settings=settings, movies=movies, tmdb=tmdb, clock=clock,
                           started=started, save=save, draw=draw, cached=cached, keyed=keyed, elapsed=elapsed)


def untitled(frame, *ranks):
    for rank in ranks:
        frame.movies[rank - 1].localized_title = ""
        frame.movies[rank - 1].localized_language = ""


def test_a_film_that_already_has_its_chinese_title_costs_no_request(frame):
    frame.save(frame.keyed)

    image, drawn = frame.draw()

    assert frame.tmdb.calls == []
    assert image.info["inkypi_media_ready"] == 10
    assert "鼓手男孩" in drawn
    assert frame.america._read_cache()["poster_status"]["retry_at"] is None
    assert frame.page.get_live_refresh_state(PAGE_SETTINGS, frame.started + timedelta(hours=4)) is None


def test_a_cached_media_pass_brings_the_chinese_title_once_tmdb_has_it(frame):
    untitled(frame, 4)
    frame.save(frame.keyed)

    # The first cached refresh asks, TMDB has no Chinese title yet: the film waits one interval.
    _image, drawn = frame.draw()
    assert frame.tmdb.calls == ["details:104", "alternatives:104"]
    assert "Drummer Boy" in drawn and "鼓手男孩" not in drawn
    mark = frame.cached()[3].extra[LOCALIZED_TITLE_CHECK]
    assert mark == {"retry_at": (frame.started + LOCALIZED_TITLE_RECHECK).isoformat()}
    assert frame.america._read_cache()["poster_status"]["retry_at"] == mark["retry_at"]
    assert frame.page.get_live_refresh_state(PAGE_SETTINGS, frame.started + timedelta(hours=2)) is None

    # Refreshes inside the interval do not ask again.
    frame.clock[0] = frame.started + timedelta(hours=2)
    frame.draw()
    frame.draw(media_only=True)
    assert len(frame.tmdb.calls) == 2

    # TMDB gains the title; when the interval has passed the media lane opens and fetches it.
    frame.tmdb.titles[104] = "鼓手男孩"
    frame.clock[0] = frame.started + LOCALIZED_TITLE_RECHECK + timedelta(minutes=1)
    assert frame.page.get_live_refresh_state(PAGE_SETTINGS, frame.clock[0]) == {"active": True,
                                                                               "interval_seconds": 300}
    before = frame.america._read_cache()
    image, drawn = frame.draw(media_only=True)
    after = frame.america._read_cache()

    assert frame.tmdb.calls[2:] == ["details:104"]
    assert "鼓手男孩" in drawn
    assert after["movies"][3]["localized_title"] == "鼓手男孩"
    assert after["movies"][3]["localized_language"] == "zh-CN"
    assert LOCALIZED_TITLE_CHECK not in after["movies"][3]["extra"]
    assert after["generated_at"] == before["generated_at"]
    assert image.info["inkypi_media_ready"] == 10
    # Nothing is left to ask about: the lane closes and later refreshes are free.
    assert after["poster_status"]["retry_at"] is None
    assert frame.page.get_live_refresh_state(PAGE_SETTINGS, frame.clock[0] + timedelta(minutes=30)) is None
    frame.draw()
    assert len(frame.tmdb.calls) == 3


def test_the_title_may_come_from_the_cn_alternative_titles(frame):
    untitled(frame, 5)
    frame.save(frame.keyed)
    frame.tmdb.alternatives[105] = "罗摩衍那：第一部"

    _image, drawn = frame.draw()

    assert frame.tmdb.calls == ["details:105", "alternatives:105"]
    assert frame.cached()[4].localized_title == "罗摩衍那：第一部"
    assert "罗摩衍那：第一部" in drawn


def test_a_new_chart_carries_the_film_over_and_still_picks_up_its_title(frame, monkeypatch):
    untitled(frame, 4)
    waiting = {"retry_at": (frame.started + timedelta(hours=1)).isoformat()}
    frame.movies[3].extra[LOCALIZED_TITLE_CHECK] = waiting
    frame.save(frame.keyed)
    frame.tmdb.titles[104] = "鼓手男孩"

    def new_chart(*_args):
        # The publisher's next chart: the same films with new figures and no media.
        return [BoxOfficeMovie(rank=rank, title=title, chart_url=movie_url(slug), weekend_gross="$9")
                for rank, (title, slug, _id, _zh) in enumerate(FILMS, start=1)], "The Numbers"

    monkeypatch.setattr(frame.america, "_load_movies", new_chart)

    # A film asked about recently keeps its place in the queue on the new chart.
    frame.draw(forceRefresh=True)
    assert frame.tmdb.calls == []
    carried = frame.cached()[3]
    assert (carried.tmdb_id, carried.weekend_gross, carried.localized_title) == (104, "$9", "")
    assert carried.extra[LOCALIZED_TITLE_CHECK] == waiting

    # Once it is due, a full load asks for the title as a cached pass does.
    frame.clock[0] = frame.started + timedelta(hours=1, minutes=1)
    image, drawn = frame.draw(forceRefresh=True)
    assert frame.tmdb.calls == ["details:104"]
    assert frame.cached()[3].localized_title == "鼓手男孩"
    assert frame.cached()[3].weekend_gross == "$9"
    assert "鼓手男孩" in drawn
    assert image.info["inkypi_media_ready"] == 10


def test_films_are_asked_about_a_few_per_pass_and_once_per_interval(frame):
    untitled(frame, 1, 2, 4, 5)
    frame.save(frame.keyed)
    assert LOCALIZED_TITLES_PER_PASS == 2

    frame.draw()
    assert frame.tmdb.calls == ["details:101", "alternatives:101", "details:102", "alternatives:102"]
    # Two films are still due: the media lane takes them without waiting for the interval.
    assert frame.page.get_live_refresh_state(PAGE_SETTINGS, frame.started + timedelta(minutes=5))

    frame.draw(media_only=True)
    assert frame.tmdb.calls[4:] == ["details:104", "alternatives:104", "details:105", "alternatives:105"]
    assert frame.page.get_live_refresh_state(PAGE_SETTINGS, frame.started + timedelta(minutes=5)) is None

    frame.draw()
    frame.draw(media_only=True)
    assert len(frame.tmdb.calls) == 8

    frame.clock[0] = frame.started + LOCALIZED_TITLE_RECHECK + timedelta(minutes=1)
    assert frame.page.get_live_refresh_state(PAGE_SETTINGS, frame.clock[0])
    frame.draw(media_only=True)
    assert frame.tmdb.calls[8:] == ["details:101", "alternatives:101", "details:102", "alternatives:102"]


def test_without_a_tmdb_key_nothing_is_asked(frame):
    untitled(frame, 4, 5)
    device = DummyDeviceConfig()
    frame.save(device)

    _image, drawn = frame.draw(device=device)
    frame.draw(device=device, media_only=True)

    assert frame.tmdb.calls == []
    assert "Drummer Boy" in drawn
    assert LOCALIZED_TITLE_CHECK not in frame.cached()[3].extra
    assert frame.america._read_cache()["poster_status"]["retry_at"] is None


def test_titles_switched_off_are_not_asked_for(frame, monkeypatch):
    untitled(frame, 4)
    monkeypatch.setitem(PAGE_SETTINGS, "showLocalizedTitles", "false")
    frame.settings["showLocalizedTitles"] = "false"
    frame.save(frame.keyed)

    frame.draw()

    assert frame.tmdb.calls == []


def test_a_failing_tmdb_changes_no_title_and_does_not_stop_the_posters(frame):
    untitled(frame, 4)
    slug = FILMS[4][1]
    # The last film's poster file is gone: the same pass must still restore it.
    Path(frame.movies[4].poster_path).unlink()
    frame.movies[4].poster_url = official_poster(slug)
    frame.movies[4].poster_path = ""
    frame.movies[4].extra["poster_source"] = "the_numbers"
    frame.save(frame.keyed)
    frame.tmdb.failing = True
    before = frame.cached()

    image, drawn = frame.draw()
    after = frame.cached()

    assert frame.tmdb.calls == [official_poster(slug), "details:104"]
    assert image.info["inkypi_media_ready"] == 10
    assert Path(after[4].poster_path).is_file()
    assert [(movie.title, movie.localized_title, movie.tmdb_id) for movie in after] == [
        (movie.title, movie.localized_title, movie.tmdb_id) for movie in before]
    assert "Drummer Boy" in drawn and "罗摩衍那：第一部" in drawn
    # The film is asked about again after the short retry, not on every refresh.
    assert after[3].extra[LOCALIZED_TITLE_CHECK] == {"retry_at": (frame.started + LOCALIZED_TITLE_RETRY).isoformat()}
    frame.draw()
    assert len(frame.tmdb.calls) == 2
    frame.tmdb.failing = False
    frame.tmdb.titles[104] = "鼓手男孩"
    frame.clock[0] = frame.started + LOCALIZED_TITLE_RETRY + timedelta(minutes=1)
    frame.draw(media_only=True)
    assert frame.cached()[3].localized_title == "鼓手男孩"


def test_a_film_with_only_the_publishers_poster_is_identified_by_year_and_title_first(frame):
    slug = FILMS[3][1]
    untitled(frame, 4)
    film = frame.movies[3]
    with Image.new("RGB", (150, 225), (20, 40, 60)) as image:
        film.poster_path = str(frame.america._poster_store().save(official_poster(slug), image))
    film.poster_url, film.tmdb_id = official_poster(slug), None
    film.extra["poster_source"] = "the_numbers"
    frame.save(frame.keyed)
    frame.tmdb.titles[904] = "鼓手男孩"

    # A same-named film of another year is not this film: no identity, no title.
    frame.tmdb.search["Drummer Boy"] = [{"id": 777, "title": "Drummer Boy", "release_date": "2019-05-01"}]
    frame.draw()
    assert frame.tmdb.calls == ["search:Drummer Boy"]
    assert (frame.cached()[3].tmdb_id, frame.cached()[3].localized_title) == (None, "")
    assert frame.cached()[3].extra[LOCALIZED_TITLE_CHECK]

    frame.tmdb.search["Drummer Boy"].append({"id": 904, "title": "Drummer Boy", "release_date": "2026-11-06"})
    frame.clock[0] = frame.started + LOCALIZED_TITLE_RECHECK + timedelta(minutes=1)
    _image, drawn = frame.draw(media_only=True)
    found = frame.cached()[3]
    assert frame.tmdb.calls[1:] == ["search:Drummer Boy", "details:904"]
    assert (found.tmdb_id, found.localized_title) == (904, "鼓手男孩")
    # The publisher's poster stays: the title lookup never replaces media.
    assert found.poster_url == official_poster(slug) and found.extra["poster_source"] == "the_numbers"
    assert "鼓手男孩" in drawn


def test_a_record_from_a_clock_that_ran_ahead_is_not_trusted(frame):
    untitled(frame, 4)
    frame.movies[3].extra[LOCALIZED_TITLE_CHECK] = {"retry_at": (frame.started + timedelta(days=30)).isoformat()}
    frame.save(frame.keyed)
    frame.tmdb.titles[104] = "鼓手男孩"

    frame.draw()

    assert frame.cached()[3].localized_title == "鼓手男孩"


def test_the_two_market_snapshot_stays_fresh_cache_for_a_title_pass(frame):
    untitled(frame, 4)
    frame.save(frame.keyed)

    market = frame.america._market_snapshot({**frame.settings, "_inkypi_theme": canonical_theme("day")},
                                            frame.keyed, (800, 480))

    assert market.provenance is SourceProvenance.FRESH_CACHE
    assert market.media_ready == 5
