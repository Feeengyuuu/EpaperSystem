"""Publisher identity follows RSS URLs, with bounded optional work and offline redraws."""

import io
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.planet_now import planet_now, publisher_icons as icons, render, sources  # noqa: E402
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded  # noqa: E402
from utils.remote_thumbnails import _key  # noqa: E402

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
ARTICLE = {"source": "A newly published newspaper", "source_url": "https://newspaper.example/news/1"}


def _context(seconds=30):
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + seconds)


def _png():
    buffer = io.BytesIO()
    Image.new("RGBA", (32, 32), (40, 110, 190, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class Http:
    def __init__(self, response=None):
        self.calls = []
        self.response = response

    def request_bytes(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.response:
            return self.response(kwargs["context"])
        return SimpleNamespace(data=_png())


@pytest.mark.parametrize("url,host", [
    ("https://sports.yahoo.com/article?x=1", "sports.yahoo.com"),
    ("http://WWW.BBC.COM:80/sport", "www.bbc.com"),
    ("https://newspaper.example/story", "newspaper.example"),
    ("https://xn--bcher-kva.de/story", "xn--bcher-kva.de"),
    ("https://bbc.com@127.0.0.1/", ""), ("https://user:pass@bbc.com/", ""),
    ("https://127.0.0.1/", ""), ("https://[::1]/", ""), ("https://[broken/", ""),
    ("http://localhost/", ""), ("https://device.local/", ""), ("https://news.internal/", ""),
    ("https://example.com:8443/", ""), ("https://example.com:bad/", ""),
    ("file:///tmp/secret", ""), ("javascript:alert(1)", ""),
])
def test_article_identity_uses_only_an_ordinary_dns_host(url, host):
    assert icons.article_host(url) == host


def test_only_the_exact_fixed_favicon_service_request_is_allowed():
    url = icons.favicon_url("newspaper.example")
    assert icons.safe_icon_url(url) == url
    for altered in (url + "&redirect=https://evil.example", url + "#extra",
                    url.replace("t3.gstatic.com", "t3.gstatic.com.evil.example"),
                    url.replace("https://t3", "http://t3"),
                    url.replace("url=https://newspaper.example", "url=https://localhost"),
                    "https://newspaper.example/favicon.ico"):
        assert icons.safe_icon_url(altered) is None


def test_article_url_survives_rss_parsing_and_takes_precedence_over_legacy_name(monkeypatch):
    xml = '''<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item>
    <title>Search</title><ht:approx_traffic>1000+</ht:approx_traffic><ht:news_item>
    <ht:news_item_source>Legacy name</ht:news_item_source>
    <ht:news_item_url>https://newspaper.example/a?x=1&amp;y=2</ht:news_item_url>
    </ht:news_item></item></channel></rss>'''
    item = sources.parse_trends(xml)[0]
    assert item["source_url"] == "https://newspaper.example/a?x=1&y=2"
    monkeypatch.setattr(icons, "_legacy_hosts", lambda: {"legacy name": {"host": "old.example"}})
    assert icons.publisher_host(item) == "newspaper.example"
    assert icons.publisher_host({"source": "Legacy name"}) == "old.example"
    assert icons.publisher_host({"source": "Legacy name", "source_url": "file:///bad"}) == ""
    assert icons.publisher_host({"source": "Legacy name..."}) == ""


def test_new_publisher_is_cached_and_a_redraw_neither_networks_nor_writes(tmp_path):
    http = Http()
    loaded = icons.load([ARTICLE], tmp_path, http=http, context=_context(), allow_network=True, now=NOW)
    assert list(loaded) == ["publisher:newspaper.example"]
    assert loaded["publisher:newspaper.example"].getpixel((16, 16)) == (40, 110, 190, 255)
    method, url, options = http.calls[0]
    assert method == "GET" and url.startswith("https://t3.gstatic.com/faviconV2?")
    assert options["allow_redirects"] is False and options["max_bytes"] == icons.ICON_BYTES
    assert 0 < options["context"].remaining_seconds() <= icons.ICON_SECONDS
    files = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    again = icons.load([ARTICLE, {"source_url": "https://another.example/"}], tmp_path,
                       http=http, context=_context(), allow_network=False, now=NOW)
    assert list(again) == ["publisher:newspaper.example"] and len(http.calls) == 1
    assert files == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}


def test_a_failed_icon_rests_then_retries_without_failing_news(tmp_path):
    def unavailable(_context):
        raise OSError("service unavailable")

    http = Http(unavailable)
    arguments = dict(http=http, context=_context(), allow_network=True)
    assert icons.load([ARTICLE], tmp_path, now=NOW, **arguments) == {}
    assert len(http.calls) == 1
    assert icons.load([ARTICLE], tmp_path, now=NOW + timedelta(minutes=30), **arguments) == {}
    assert len(http.calls) == 1
    assert icons.load([ARTICLE], tmp_path, now=NOW + timedelta(hours=3), **arguments) == {}
    assert len(http.calls) == 2
    assert len(json.loads((tmp_path / "missed.json").read_text())) == 1


def test_offline_or_short_remaining_budget_never_creates_a_cache(tmp_path):
    http = Http()
    for online in (False, True):
        directory = tmp_path / str(online)
        assert icons.load([ARTICLE], directory, http=http, context=_context(5),
                          allow_network=online, now=NOW) == {}
        assert not directory.exists()
    assert not http.calls


def test_child_deadline_preserves_parent_render_time(tmp_path):
    clock = [100.0]
    parent = TaskContext.never_cancelled(deadline_monotonic=130, clock=lambda: clock[0])

    def slow(child):
        assert child.deadline_monotonic == 106
        clock[0] = 106.1
        child.raise_if_cancelled()

    assert icons.load([ARTICLE], tmp_path, http=Http(slow), context=parent,
                      allow_network=True, now=NOW) == {}
    assert parent.remaining_seconds() > icons.RENDER_RESERVE
    parent.raise_if_cancelled()


def test_real_parent_cancellation_is_not_swallowed(tmp_path):
    parent = _context()

    def cancel(child):
        parent.cancel_event.set()
        child.raise_if_cancelled()

    with pytest.raises(TaskCancelled):
        icons.load([ARTICLE], tmp_path, http=Http(cancel), context=parent, allow_network=True, now=NOW)


def test_existing_official_wordmarks_need_no_favicon_request(tmp_path):
    http = Http()
    assert icons.load([{"source": "BBC", "source_url": "https://www.bbc.com/news/a"}], tmp_path,
                      http=http, context=_context(), allow_network=True, now=NOW) == {}
    assert not http.calls and not list(tmp_path.iterdir())


@pytest.mark.parametrize("mode", ["day", "night"])
def test_favicon_replaces_the_source_name_and_gives_its_space_to_the_headline(monkeypatch, mode):
    canvas = render.PageCanvas(render._palette({"themeMode": mode}))
    item = {**ARTICLE, "title": "A search", "news": "The original headline", "traffic": 1000}
    payload = {"trends": {"data": {"items": [item]}}}
    calls = []
    text = canvas.text

    def record(xy, value, *args, **kwargs):
        calls.append((xy, str(value), args, kwargs))
        return text(xy, value, *args, **kwargs)

    monkeypatch.setattr(canvas, "text", record)
    image = Image.open(io.BytesIO(_png())).convert("RGBA")
    render._trends(canvas, payload, {icons.icon_key(item): image})
    headline = next(call for call in calls if call[1] == "The original headline")
    assert not any(call[1].startswith("A newly") for call in calls)
    assert headline[0][0] == 52 + 11 + 4
    assert headline[3]["width"] > 180
    assert canvas.image.getpixel((57, 324))[:3] == (40, 110, 190)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_bundled_symbols_replace_names_but_unknown_sources_still_use_text(monkeypatch, mode):
    canvas = render.PageCanvas(render._palette({"themeMode": mode}))
    names = ["KTLA", "LoL Esports", "Yahoo", "IMDb", "Unknown publisher"]
    items = [{"source": name, "title": f"Search {index}", "news": f"Headline {index}"}
             for index, name in enumerate(names)]
    text = canvas.text
    calls = []

    def record(xy, value, *args, **kwargs):
        calls.append((xy, str(value)))
        return text(xy, value, *args, **kwargs)

    monkeypatch.setattr(canvas, "text", record)
    render._trends(canvas, {"trends": {"data": {"items": items}}}, {})
    for index, name in enumerate(names[:-1]):
        assert not any(value == name for _xy, value in calls)
        assert next(xy for xy, value in calls if value == f"Headline {index}")[0] == 52 + 11 + 4
    assert any(value == canvas.fit(names[-1], 9, 72) for _xy, value in calls)
    assert next(xy for xy, value in calls if value == "Headline 4")[0] > 52 + 11 + 4


def test_bundled_mark_still_wins_when_cached_favicon_exists(monkeypatch):
    canvas = render.PageCanvas(render._palette({}))
    item = {"source": "BBC", "source_url": "https://www.bbc.com/a", "title": "News"}
    used = []
    monkeypatch.setattr(icons, "on_page", lambda *_args: used.append(True))
    render._trends(canvas, {"trends": {"data": {"items": [item]}}},
                   {icons.icon_key(item): Image.new("RGBA", (16, 16))})
    assert used == []


def test_known_placeholder_cache_is_never_presented_as_a_publisher(monkeypatch, tmp_path):
    # Pixel identity is checked after decoding, independent of the PNG container's metadata.
    image = Image.new("RGBA", (16, 16), (100, 120, 140, 255))
    monkeypatch.setattr(icons, "PLACEHOLDER_PIXELS", icons.sha256(image.tobytes()).hexdigest())
    path = tmp_path / _key(icons.favicon_url("newspaper.example"), True)
    image.save(path)
    assert icons.load([ARTICLE], tmp_path, http=Http(), context=_context(), allow_network=False, now=NOW) == {}
    assert path.exists()


@pytest.mark.parametrize("seeded_cache", [False, True])
def test_default_globe_is_never_persisted_and_can_be_replaced_after_retry(monkeypatch, tmp_path, seeded_cache):
    globe = Image.new("RGBA", (16, 16), (100, 120, 140, 255))
    monkeypatch.setattr(icons, "PLACEHOLDER_PIXELS", icons.sha256(globe.tobytes()).hexdigest())
    buffer = io.BytesIO()
    globe.save(buffer, format="PNG")
    http = Http(lambda _context: SimpleNamespace(data=buffer.getvalue()))
    key = _key(icons.favicon_url("newspaper.example"), True)
    if seeded_cache:
        (tmp_path / key).write_bytes(buffer.getvalue())
    assert icons.load([ARTICLE], tmp_path, http=http, context=_context(), allow_network=True, now=NOW) == {}
    assert not (tmp_path / key).exists() and len(http.calls) == 1
    http.response = None
    assert icons.load([ARTICLE], tmp_path, http=http, context=_context(), allow_network=True,
                      now=NOW + timedelta(minutes=30)) == {}
    assert len(http.calls) == 1
    assert icons.load([ARTICLE], tmp_path, http=http, context=_context(), allow_network=True,
                      now=NOW + timedelta(hours=3))
    assert len(http.calls) == 2 and (tmp_path / key).is_file()


def test_budget_skipped_publisher_is_not_negative_cached(tmp_path):
    clock = [100.0]
    parent = TaskContext.never_cancelled(deadline_monotonic=130, clock=lambda: clock[0])

    def slow_failure(_child):
        clock[0] += 2.5  # Less than the 4 seconds needed to start the second picture remains.
        raise OSError("failed first icon")

    http = Http(slow_failure)
    assert icons.load([ARTICLE, {"source_url": "https://second.example/news"}], tmp_path,
                      http=http, context=parent, allow_network=True, now=NOW) == {}
    assert len(http.calls) == 1
    assert list(json.loads((tmp_path / "missed.json").read_text())) == [icons.favicon_url("newspaper.example")]


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_child_timeout_with_parent_stop_closes_previously_read_icons(monkeypatch, tmp_path, stop):
    clock = [100.0]
    parent = TaskContext.never_cancelled(deadline_monotonic=130, clock=lambda: clock[0])
    cached = Image.new("RGBA", (16, 16))
    original = icons.load_thumbnails
    cached_url = icons.favicon_url("newspaper.example")

    def read(urls, *args, **kwargs):
        if kwargs["cached_only"]:
            return {cached_url: cached}
        return original(urls, *args, **kwargs)

    def stop_parent(child):
        clock[0] = 106.1 if stop == "cancel" else 130.1
        if stop == "cancel":
            parent.cancel_event.set()
        child.raise_if_cancelled()

    monkeypatch.setattr(icons, "load_thumbnails", read)
    with pytest.raises(TaskCancelled if stop == "cancel" else TaskDeadlineExceeded):
        icons.load([ARTICLE, {"source_url": "https://second.example/"}], tmp_path,
                   http=Http(stop_parent), context=parent, allow_network=True, now=NOW)
    with pytest.raises(ValueError):
        cached.getpixel((0, 0))


def test_cached_only_still_honors_parent_cancellation(tmp_path):
    parent = _context()
    parent.cancel_event.set()
    with pytest.raises(TaskCancelled):
        icons.load([ARTICLE], tmp_path, http=Http(), context=parent, allow_network=False, now=NOW)


def test_optional_icon_storage_failure_keeps_main_provenance_and_cache_isolation(monkeypatch, tmp_path):
    from plugins.base_plugin.source_book import combined_provenance
    from utils import remote_thumbnails

    plugin = planet_now.PlanetNow({"id": "planet_now"})
    feeds = {"trends": {"items": [ARTICLE]}}
    book = SimpleNamespace(load=lambda source, *_a, **_kw: {"data": feeds.get(source.name, {}), "state": "live"})
    monkeypatch.setattr(plugin, "_book", lambda: book)
    monkeypatch.setattr(planet_now, "load_thumbnails", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(planet_now, "get_http_client", lambda: Http())
    monkeypatch.setenv("PLANET_NOW_CACHE_DIR", str(tmp_path / "override"))
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "runtime"))
    original = icons.load
    paths = []

    def observe(items, directory, **kwargs):
        paths.append(directory)
        assert directory == tmp_path / "override" / "publisher-icons"
        assert not directory.exists()  # The refresh coordinator must not mkdir optional storage.
        return original(items, directory, **kwargs)

    def denied(*_args, **_kwargs):
        raise OSError("icon cache cannot be created")

    monkeypatch.setattr(icons, "load", observe)
    monkeypatch.setattr(remote_thumbnails, "cache_namespace_for_directory", denied)
    monkeypatch.setattr(icons, "atomic_write_json", denied)
    payload, images = plugin._refresh({}, NOW, allow_network=True, context=_context())
    assert paths and images == {} and payload["trends"]["data"]["items"] == [ARTICLE]
    assert payload["provenance"] == combined_provenance([{"state": "live"}] * 3)


@pytest.mark.parametrize("cancelling", [False, True])
def test_optional_placeholder_invalidation_handles_cache_error_but_keeps_cancellation(monkeypatch, tmp_path, cancelling):
    globe = Image.new("RGBA", (16, 16), (100, 120, 140, 255))
    monkeypatch.setattr(icons, "PLACEHOLDER_PIXELS", icons.sha256(globe.tobytes()).hexdigest())
    path = tmp_path / _key(icons.favicon_url("newspaper.example"), True)
    globe.save(path)

    def fail(_key):
        raise TaskCancelled("cancelled cleanup") if cancelling else icons.CacheError("cache removal refused")

    monkeypatch.setattr(icons, "cache_namespace_for_directory", lambda *_a: SimpleNamespace(remove=fail))
    if cancelling:
        with pytest.raises(TaskCancelled):
            icons.load([ARTICLE], tmp_path, http=Http(), context=_context(), allow_network=True, now=NOW)
    else:
        assert icons.load([ARTICLE], tmp_path, http=Http(), context=_context(), allow_network=True, now=NOW) == {}


def test_verified_legacy_registry_preserves_all_observed_source_identities():
    registry = json.loads((icons.ASSETS / "publisher_hosts.json").read_text(encoding="utf-8"))
    assert len(registry["publishers"]) == registry["publisher_count"]
    for name, record in registry["publishers"].items():
        assert name == record["name"].strip().casefold()
        assert icons.publisher_host({"source": record["name"]}) == record["host"]
        assert icons.article_host(record["evidence_url"]) and record["basis"]
        if record["article_url_verified"]:
            assert icons.article_host(record["evidence_url"]) == record["host"]


def test_hbcu_official_colors_keep_their_white_field_in_the_night_source_line():
    canvas = render.PageCanvas(render._palette({"themeMode": "night"}))
    item = {"source": "HBCU Sports", "title": "Search", "news": "Original report"}
    render._trends(canvas, {"trends": {"data": {"items": [item]}}}, {})
    mark = render.load_source_logo(icons.ASSETS / "hbcu_sports.png", 11)
    expected = Image.new("RGB", mark.size, "white")
    expected.paste(mark, (0, 0), mark)
    assert canvas.image.crop((52, 320, 52 + mark.width, 331)).tobytes() == expected.tobytes()


def test_all_observed_publishers_have_a_bundled_identity_with_an_empty_offline_cache(tmp_path):
    registry = icons._legacy_hosts()
    http = Http()
    cold_cache = tmp_path / "never-created"
    for name, record in registry.items():
        item = {"source": record["name"]}
        assert icons.load([item], cold_cache, http=http, context=_context(), allow_network=False, now=NOW) == {}
        wordmark = icons.NEWS_OUTLET_LOGOS.get(name) or icons.LOCAL_ICONS.get(name)
        if wordmark:
            assert render.load_source_logo(icons.ASSETS / f"{wordmark}.png", 11) is not None
        else:
            image = icons.bundled_icon(item)
            assert image is not None and not icons._placeholder(image)
            assert max(image.size) <= 64
            image.close()
    assert len(registry) == 96 and not cold_cache.exists() and http.calls == []


def test_public_site_icon_bundle_matches_its_provenance_and_needs_no_refresh(tmp_path):
    manifest = json.loads((icons.PUBLISHER_ASSETS / "publishers.source.json").read_text(encoding="utf-8"))
    http = Http()
    for host, record in manifest["publishers"].items():
        path = icons.PUBLISHER_ASSETS / record["file"]
        assert path.name == f"{host}.png"
        assert icons.sha256(path.read_bytes()).hexdigest() == record["sha256"]
        assert icons.safe_icon_url(record["source"]) and icons.article_host(f"https://{host}") == host
        assert icons.load([{"source_url": f"https://{host}/news/new"}], tmp_path, http=http,
                          context=_context(), allow_network=True, now=NOW) == {}
    assert len(manifest["publishers"]) == 88 and http.calls == [] and not list(tmp_path.iterdir())
