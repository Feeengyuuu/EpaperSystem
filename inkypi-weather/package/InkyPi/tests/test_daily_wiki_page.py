import hashlib
import os
import sys
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_wiki_page import daily_wiki_page as wiki_module  # noqa: E402
from plugins.daily_wiki_page.daily_wiki_page import DailyWikiPage, DEFAULT_FONT  # noqa: E402
from plugins.base_plugin.presentation import PresentationMode  # noqa: E402
from plugins.base_plugin.render_provenance import read_source_provenance  # noqa: E402
from utils import cache_manager  # noqa: E402
from utils.cache_manager import CacheBudget, CachePathError  # noqa: E402


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone="America/Los_Angeles", orientation="horizontal"):
        self.resolution = resolution
        self.timezone = timezone
        self.orientation = orientation

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"timezone": self.timezone, "orientation": self.orientation}
        if key is None:
            return values
        return values.get(key, default)


def make_plugin(tmp_path):
    plugin = DailyWikiPage({"id": "daily_wiki_page"})
    plugin._cache_dir = lambda create=True: tmp_path
    return plugin


def luma(color):
    red, green, blue = color[:3]
    return (red * 299 + green * 587 + blue * 114) / 1000


def canonical_theme(mode):
    palette = {
        "background": (244, 240, 230) if mode == "day" else (16, 21, 27),
        "panel": (255, 255, 255) if mode == "day" else (0, 0, 0),
        "ink": (10, 12, 15) if mode == "day" else (255, 255, 255),
        "muted": (74, 78, 84) if mode == "day" else (194, 196, 202),
        "rule": (185, 188, 194) if mode == "day" else (46, 48, 56),
        "accent": (56, 95, 143) if mode == "day" else (121, 174, 230),
    }
    return {
        "mode": mode,
        "requested_mode": "auto",
        "palette": palette,
        "css": {
            role: "#{:02x}{:02x}{:02x}".format(*color)
            for role, color in palette.items()
        },
    }


def image_digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


DAILY_MEDIA_URL = "https://media.example.test/daily.png"
HISTORY_MEDIA_URL = "https://media.example.test/history.png"


class FakeImageResponse:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {"Content-Length": str(len(payload))}
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        for offset in range(0, len(self._payload), chunk_size):
            yield self._payload[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class RecordingImageSession:
    def __init__(self, payloads=None, *, forbidden=False):
        self.payloads = payloads or {}
        self.forbidden = forbidden
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.forbidden:
            raise AssertionError(f"unexpected HTTP GET: {url}")
        return FakeImageResponse(self.payloads[url])


def png_bytes(color):
    output = BytesIO()
    Image.new("RGB", (96, 72), color).save(output, format="PNG")
    return output.getvalue()


def media_payload(plugin, current, language, settings):
    payload = plugin._payload_from_feed(sample_feed(), language, settings)
    payload["date"] = current.strftime("%Y-%m-%d")
    payload["image_url"] = DAILY_MEDIA_URL
    payload["history_image_url"] = HISTORY_MEDIA_URL
    return payload


def media_cache_path(tmp_path, url):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return tmp_path / "media" / f"{key}.png"


def sample_feed():
    return {
        "tfa": {
            "titles": {"normalized": "Knowledge graph"},
            "description": "A network of entities and relationships",
            "extract": "A knowledge graph organizes information as entities and relationships. It can connect people, places, concepts, and events in a structure that supports search and discovery.",
            "thumbnail": {"source": "https://example.com/article.jpg"},
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Knowledge_graph"}},
        },
        "image": {
            "titles": {"normalized": "Daily image"},
            "thumbnail": {"source": "https://example.com/image.jpg"},
            "description": {"text": "A connected diagram"},
            "artist": {"text": "Example credit"},
        },
        "onthisday": [
            {"year": 1991, "text": "The first public web page was announced.", "pages": [{"titles": {"normalized": "1991年"}}, {"titles": {"normalized": "Flag page"}, "thumbnail": {"source": "https://example.com/flag.svg.png"}}, {"titles": {"normalized": "Map page"}, "thumbnail": {"source": "https://example.com/map.jpg"}}]},
            {"year": 2001, "text": "An encyclopedia project reached a wider audience."},
            {"year": 2005, "text": "A collaborative knowledge project grew."},
            {"year": 2010, "text": "A public digital archive expanded."},
            {"year": 2016, "text": "A research milestone was published."},
            {"year": 2020, "text": "This sixth event should not be included."},
        ],
        "mostread": {
            "articles": [
                {"titles": {"normalized": "Knowledge graph"}, "views": 1000},
                {"titles": {"normalized": "Wikipedia"}, "views": 900},
            ]
        },
    }


def test_fetch_feed_uses_official_per_wiki_rest_endpoint(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    captured = {}

    def fake_get_json(url, params=None):
        captured["url"] = url
        captured["params"] = params
        return {"tfa": {}}

    monkeypatch.setattr(plugin, "_get_json", fake_get_json)

    plugin._fetch_feed(
        datetime(2026, 8, 28, 1, 19),
        "zh",
    )

    assert captured == {
        "url": "https://zh.wikipedia.org/api/rest_v1/feed/featured/2026/08/28",
        "params": None,
    }


def test_daily_wiki_settings_refresh_on_display_default_enabled():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "daily_wiki_page" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")

    assert 'name="refreshOnDisplay"' in html
    assert 'value="true"' in html


def test_daily_wiki_font_defaults_to_microsoft_yahei_and_preserves_explicit_choice(tmp_path):
    plugin = make_plugin(tmp_path)
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "daily_wiki_page" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")

    assert DEFAULT_FONT == "Microsoft YaHei"
    assert plugin._resolved_font_family({}) == "Microsoft YaHei"
    assert plugin._resolved_font_family({"fontFamily": ""}) == "Microsoft YaHei"
    assert plugin._resolved_font_family({"fontFamily": "Jost"}) == "Jost"
    assert plugin._resolved_font_family({"fontFamily": "LXGW WenKai"}) == "LXGW WenKai"
    assert "fontFamily.value = 'Microsoft YaHei';" in html
    assert "fontFamily.value = 'Jost';" not in html
    assert "fontFamily.value === 'Jost'" not in html


def test_daily_wiki_font_default_and_explicit_choice(monkeypatch, tmp_path):
    plugin = make_plugin(tmp_path)
    sentinel = object()
    calls = []

    def fake_get_font(family, size, weight="normal"):
        calls.append((family, size, weight))
        return sentinel

    monkeypatch.setattr(wiki_module, "get_font", fake_get_font)

    assert plugin._font(None, 18) is sentinel
    assert plugin._font("", 18) is sentinel
    assert plugin._font("Jost", 18, "bold") is sentinel
    assert calls == [
        ("Microsoft YaHei", 18, "normal"),
        ("Microsoft YaHei", 18, "normal"),
        ("Jost", 18, "bold"),
    ]


def test_daily_wiki_cjk_font_uses_shared_base_ui_resolver(monkeypatch, tmp_path):
    plugin = make_plugin(tmp_path)
    sentinel = object()
    calls = []

    def fake_base_ui_font(size, bold=False):
        calls.append((size, bold))
        return sentinel

    monkeypatch.setattr(wiki_module, "get_base_ui_font", fake_base_ui_font, raising=False)

    assert plugin._font("__cjk__", 21, "bold") is sentinel
    assert calls == [(21, True)]


def test_daily_wiki_cjk_font_prefers_microsoft_yahei_static_file(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    plugin_dir = tmp_path / "src" / "plugins" / "daily_wiki_page"
    static_fonts_dir = tmp_path / "src" / "static" / "fonts"
    plugin_dir.mkdir(parents=True)
    static_fonts_dir.mkdir(parents=True)
    yahei_path = static_fonts_dir / "msyh.ttf"
    noto_path = static_fonts_dir / "NotoSansSC-VF.ttf"
    yahei_path.touch()
    noto_path.touch()
    monkeypatch.setattr(plugin, "get_plugin_dir", lambda: plugin_dir)

    selected = plugin._cjk_font_path()

    assert selected.resolve() == yahei_path.resolve()


def test_daily_wiki_cjk_font_uses_tracked_noto_fallback_without_microsoft_yahei(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    tracked_noto_path = (
        Path(__file__).resolve().parents[1] / "src" / "static" / "fonts" / "NotoSansSC-VF.ttf"
    )
    original_is_file = Path.is_file

    def is_file_without_microsoft_yahei(path):
        if path.name in {"msyh.ttf", "msyh.ttc"}:
            return False
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", is_file_without_microsoft_yahei)

    selected = plugin._cjk_font_path()

    assert tracked_noto_path.is_file()
    assert selected.resolve() == tracked_noto_path.resolve()

def test_payload_uses_daily_image_and_history_only(tmp_path):
    plugin = make_plugin(tmp_path)

    payload = plugin._payload_from_feed(sample_feed(), "en", {})

    assert payload["title"] == "Knowledge graph"
    assert payload["article_source"] == "featured article"
    assert payload["page_url"] == "https://en.wikipedia.org/wiki/Knowledge_graph"
    assert payload["image_url"] == "https://example.com/image.jpg"
    assert payload["daily_image_title"] == "Daily image"
    assert payload["image_caption"] == "A connected diagram"
    assert payload["image_source"] == "daily_image"
    assert payload["history_image_url"] == "https://example.com/map.jpg"
    assert payload["history_image_title"] == "Map page"
    assert payload["history_image_year"] == "1991"
    assert [item["year"] for item in payload["on_this_day"]] == ["1991", "2001", "2005", "2010", "2016"]
    assert all("topics_text" not in item for item in payload["on_this_day"])
    assert payload["most_read"] == []


def test_on_this_day_deduplicates_normalized_event_when_pages_change(tmp_path):
    plugin = make_plugin(tmp_path)
    feed = sample_feed()
    galileo_pages = [
        {"titles": {"normalized": "Galileo (spacecraft)"}},
        {"titles": {"normalized": "243 Ida"}},
        {"titles": {"normalized": "Minor-planet moon"}},
    ]
    feed["onthisday"] = [
        {
            "year": 2021,
            "text": "The second phase of a rapid-transit line opened.",
            "pages": [{"titles": {"normalized": "Thomson-East Coast Line"}}],
        },
        {
            "year": 1993,
            "text": "The NASA spacecraft Galileo flew by 243 Ida (both pictured).",
            "pages": galileo_pages,
        },
        {
            "year": 1993,
            "text": "THE NASA SPACECRAFT GALILEO—FLEW BY 243 IDA!",
            "pages": [
                {"titles": {"normalized": "Galileo (spacecraft)"}},
                {"titles": {"normalized": "Dactyl (moon)"}},
            ],
        },
        {
            "year": 1987,
            "text": "Construction began on the Ryugyong Hotel.",
            "pages": [{"titles": {"normalized": "Ryugyong Hotel"}}],
        },
        {
            "year": 1973,
            "text": "The Norrmalmstorg robbery ended.",
            "pages": [{"titles": {"normalized": "Norrmalmstorg robbery"}}],
        },
        {
            "year": 1963,
            "text": "Martin Luther King Jr. delivered the I Have a Dream speech.",
            "pages": [{"titles": {"normalized": "I Have a Dream"}}],
        },
    ]

    payload = plugin._payload_from_feed(feed, "en", {})

    assert [item["year"] for item in payload["on_this_day"]] == [
        "2021",
        "1993",
        "1987",
        "1973",
        "1963",
    ]
    assert payload["on_this_day"][1]["text"] == (
        "The NASA spacecraft Galileo flew by 243 Ida (both pictured)."
    )


def test_on_this_day_keeps_same_year_same_pages_when_event_text_differs(tmp_path):
    plugin = make_plugin(tmp_path)
    feed = sample_feed()
    shared_pages = [
        {"titles": {"normalized": "Galileo (spacecraft)"}},
        {"titles": {"normalized": "1993"}},
    ]
    feed["onthisday"] = [
        {
            "year": 1993,
            "text": "Galileo photographed the asteroid 243 Ida.",
            "pages": shared_pages,
        },
        {
            "year": 1993,
            "text": "The Oslo Accords were signed.",
            "pages": list(reversed(shared_pages)),
        },
    ]

    payload = plugin._payload_from_feed(feed, "en", {})

    assert payload["on_this_day"] == [
        {"year": "1993", "text": "Galileo photographed the asteroid 243 Ida."},
        {"year": "1993", "text": "The Oslo Accords were signed."},
    ]


@pytest.mark.parametrize("annotation", ["(pictured)", "(both pictured)", "（图）", "（圖）"])
def test_history_event_signature_ignores_pictorial_annotations(tmp_path, annotation):
    plugin = make_plugin(tmp_path)

    signature = plugin._history_event_signature(
        "1993",
        f"The NASA spacecraft Galileo flew by 243 Ida {annotation}.",
    )

    assert signature == plugin._history_event_signature(
        "1993",
        "THE NASA SPACECRAFT GALILEO—FLEW BY 243 IDA!",
    )


def test_history_image_tracks_displayed_events(tmp_path):
    plugin = make_plugin(tmp_path)
    feed = sample_feed()
    feed["onthisday"][0]["text"] = ""
    feed["onthisday"][0]["pages"] = [{"titles": {"normalized": "Skipped event"}, "thumbnail": {"source": "https://example.com/skipped.jpg"}}]
    feed["onthisday"][1]["pages"] = [{"titles": {"normalized": "Selected event"}, "thumbnail": {"source": "https://example.com/selected.jpg"}}]

    selected = plugin._on_this_day_items(feed, {}, date_page_events=[])
    history_image = plugin._history_image_from_feed(feed, selected)

    assert selected[0]["year"] == "2001"
    assert history_image["url"] == "https://example.com/selected.jpg"
    assert history_image["title"] == "Selected event"
    assert history_image["year"] == "2001"


def test_history_image_scans_beyond_six_events_for_selected_item(tmp_path):
    plugin = make_plugin(tmp_path)
    feed = sample_feed()
    feed["onthisday"] = [
        {"year": 1900 + index, "text": "", "pages": []}
        for index in range(6)
    ]
    feed["onthisday"].append(
        {
            "year": 2007,
            "text": "The seventh feed item is the first displayable event.",
            "pages": [
                {
                    "titles": {"normalized": "Seventh selected event"},
                    "thumbnail": {"source": "https://example.com/seventh.jpg"},
                }
            ],
        }
    )

    selected = plugin._on_this_day_items(feed, {}, date_page_events=[])
    history_image = plugin._history_image_from_feed(feed, selected)

    assert selected == [
        {"year": "2007", "text": "The seventh feed item is the first displayable event."}
    ]
    assert history_image == {
        "url": "https://example.com/seventh.jpg",
        "title": "Seventh selected event",
        "year": "2007",
        "event_index": 0,
    }


def test_simplified_chinese_keeps_daily_image_with_its_caption(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    feed = sample_feed()

    def fake_get_json(url, params=None):
        if params["action"] == "query":
            return {
                "query": {
                    "pages": [{
                        "pageid": 100,
                        "title": "Knowledge graph",
                        "extract": "Knowledge graph article extract.",
                        "thumbnail": {"source": "https://example.com/article-zh.jpg"},
                    }]
                }
            }
        if params["action"] == "parse":
            return {"parse": {"displaytitle": "Knowledge graph"}}
        raise AssertionError(params)

    monkeypatch.setattr(plugin, "_get_json", fake_get_json)
    monkeypatch.setattr(plugin, "_convert_zh_cn_texts", lambda values: values)

    payload = plugin._payload_from_feed(feed, "zh-cn", {})

    assert payload["image_url"] == "https://example.com/image.jpg"
    assert payload["image_caption"] == "A connected diagram"
    assert payload["daily_image_title"] == "Daily image"
    assert payload["image_source"] == "daily_image"


def test_image_caption_collapses_duplicate_chinese_sentence_punctuation(tmp_path):
    plugin = make_plugin(tmp_path)
    feed = sample_feed()
    feed["image"]["description"] = {"text": "\u8fd9\u662f\u4e00\u6bb5\u56fe\u7247\u8bf4\u660e\u3002\u3002"}

    payload = plugin._payload_from_feed(feed, "zh-cn", {})

    assert payload["image_caption"] == "\u8fd9\u662f\u4e00\u6bb5\u56fe\u7247\u8bf4\u660e\u3002"


def test_daily_payload_fetches_once_then_uses_cache(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    calls = {"fetch": 0}

    def fake_fetch(now, language, fallback_language, settings):
        calls["fetch"] += 1
        payload = plugin._payload_from_feed(sample_feed(), language, settings)
        payload["date"] = now.strftime("%Y-%m-%d")
        return payload

    monkeypatch.setattr(plugin, "_fetch_live_payload", fake_fetch)

    now = datetime(2026, 6, 25, 10, 0)
    first = plugin._daily_payload({"language": "en"}, now)
    second = plugin._daily_payload({"language": "en"}, now)

    assert first["source_state"] == "live"
    assert second["source_state"] == "cache"
    assert calls["fetch"] == 1


def test_daily_payload_ignores_v6_cache_and_fetches_current_source(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 8, 28, 1, 19)
    settings = {
        "language": "zh-cn",
        "fallbackLanguage": "en",
        "showImage": True,
        "showOnThisDay": True,
    }
    plugin._write_cache(
        {
            "schema": "daily-wiki-page-v6",
            "cache_key": "7a19ac743fc6ac9e52dedacc752db098f8202579",
            "generated_at": "2026-08-28T01:19:00-07:00",
            "payload": {
                "date": "2026-08-28",
                "language": "en",
                "source": "Wikimedia",
                "title": "Cached English fallback",
                "extract": "Cached English content.",
                "source_state": "live",
            },
        }
    )
    calls = {"fetch": 0}

    def fake_fetch(current, language, fallback_language, current_settings):
        calls["fetch"] += 1
        assert language == "zh-cn"
        assert fallback_language == "en"
        assert current_settings == settings
        return {
            "date": current.strftime("%Y-%m-%d"),
            "language": "zh-cn",
            "source": "Wikimedia",
            "title": "Fresh Chinese source",
            "extract": "Fresh Chinese content.",
        }

    monkeypatch.setattr(plugin, "_fetch_live_payload", fake_fetch)

    payload = plugin._daily_payload(settings, now)

    assert calls["fetch"] == 1
    assert payload["language"] == "zh-cn"
    assert payload["title"] == "Fresh Chinese source"
    assert payload["source_state"] == "live"
    assert plugin._read_cache()["schema"] == "daily-wiki-page-v7"


def test_daily_payload_rejects_v6_cache_when_live_fetch_fails(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 8, 28, 1, 19)
    settings = {
        "language": "zh-cn",
        "fallbackLanguage": "en",
        "showImage": True,
        "showOnThisDay": True,
        "forceRefresh": True,
    }
    plugin._write_cache(
        {
            "schema": "daily-wiki-page-v6",
            "cache_key": "7a19ac743fc6ac9e52dedacc752db098f8202579",
            "payload": {
                "date": "2026-08-28",
                "language": "en",
                "source": "Wikimedia",
                "title": "Cached English fallback",
                "extract": "Cached English content.",
            },
        }
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_live_payload",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("private upstream detail")),
    )

    payload = plugin._daily_payload(settings, now)

    assert payload["source_state"] == "local"
    assert payload["_source_provenance"] == "local_fallback"
    assert payload["source"] == "Local Encyclopedia"
    assert payload["language"] == "zh-cn"
    assert payload["title"] != "Cached English fallback"


def test_daily_wiki_theme_only_opposite_palette_reuses_warm_source_cache(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 25, 10, 0)
    fetch_calls = 0

    def fake_fetch(current, language, _fallback_language, settings):
        nonlocal fetch_calls
        fetch_calls += 1
        return media_payload(plugin, current, language, settings)

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(plugin, "_fetch_live_payload", fake_fetch)
    monkeypatch.setattr(plugin, "_write_context", lambda *_args: None)
    warm_session = RecordingImageSession(
        {
            DAILY_MEDIA_URL: png_bytes((30, 90, 160)),
            HISTORY_MEDIA_URL: png_bytes((180, 120, 45)),
        }
    )
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: warm_session)

    day_settings = {
        "language": "en",
        "theme": "dark",
        "_inkypi_theme": canonical_theme("day"),
    }
    night_settings = {
        "language": "en",
        "theme": "paper",
        "_inkypi_theme": canonical_theme("night"),
        "_theme_render_only": True,
    }
    day = plugin.generate_image(day_settings, FakeDeviceConfig())

    assert [url for url, _kwargs in warm_session.calls] == [
        DAILY_MEDIA_URL,
        HISTORY_MEDIA_URL,
    ]
    assert all(call[1]["stream"] is True for call in warm_session.calls)
    assert all(call[1]["timeout"] == (5, 12) for call in warm_session.calls)
    cached_media = [
        media_cache_path(tmp_path, DAILY_MEDIA_URL),
        media_cache_path(tmp_path, HISTORY_MEDIA_URL),
    ]

    forbidden_session = RecordingImageSession(forbidden=True)
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: forbidden_session)
    night = plugin.generate_image(night_settings, FakeDeviceConfig())

    assert fetch_calls == 1
    assert forbidden_session.calls == []
    assert all(path.is_file() for path in cached_media)
    assert all(len(path.stem) == 64 and path.stem.isalnum() for path in cached_media)
    assert plugin._cache_key("2026-06-25", day_settings, "en", "") == plugin._cache_key(
        "2026-06-25",
        night_settings,
        "en",
        "",
    )
    assert image_digest(day) != image_digest(night)
    assert plugin._palette({**day_settings, "theme": "paper"}) == plugin._palette(
        day_settings,
    )


def test_daily_wiki_normal_render_reuses_cached_media_without_http(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 25, 10, 0)

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(
        plugin,
        "_fetch_live_payload",
        lambda current, language, _fallback, settings: media_payload(
            plugin,
            current,
            language,
            settings,
        ),
    )
    monkeypatch.setattr(plugin, "_write_context", lambda *_args: None)
    warm_session = RecordingImageSession(
        {
            DAILY_MEDIA_URL: png_bytes((30, 90, 160)),
            HISTORY_MEDIA_URL: png_bytes((180, 120, 45)),
        }
    )
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: warm_session)
    settings = {"language": "en", "_inkypi_theme": canonical_theme("day")}

    plugin.generate_image(settings, FakeDeviceConfig())
    forbidden_session = RecordingImageSession(forbidden=True)
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: forbidden_session)
    plugin.generate_image(settings, FakeDeviceConfig())

    assert len(warm_session.calls) == 2
    assert forbidden_session.calls == []


def test_daily_wiki_normal_render_refreshes_stale_cached_media(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    cache_path = media_cache_path(tmp_path, DAILY_MEDIA_URL)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(png_bytes((12, 34, 56)))
    stale_timestamp = time.time() - (2 * 60 * 60)
    os.utime(cache_path, (stale_timestamp, stale_timestamp))
    session = RecordingImageSession(
        {DAILY_MEDIA_URL: png_bytes((210, 120, 30))},
    )
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: session)

    loaded = plugin._download_image(
        DAILY_MEDIA_URL,
        (96, 72),
        {"imageCacheHours": 1},
    )

    assert loaded.getpixel((0, 0)) == (210, 120, 30)
    assert [url for url, _kwargs in session.calls] == [DAILY_MEDIA_URL]
    with Image.open(cache_path) as refreshed:
        assert refreshed.convert("RGB").getpixel((0, 0)) == (210, 120, 30)


def test_daily_wiki_fresh_media_read_does_not_extend_download_age(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    cache_path = media_cache_path(tmp_path, DAILY_MEDIA_URL)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(png_bytes((18, 52, 86)))
    downloaded_at = time.time() - (30 * 60)
    os.utime(cache_path, (downloaded_at, downloaded_at))
    forbidden_session = RecordingImageSession(forbidden=True)
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: forbidden_session)

    loaded = plugin._download_image(
        DAILY_MEDIA_URL,
        (96, 72),
        {"imageCacheHours": 1},
    )

    assert loaded.getpixel((0, 0)) == (18, 52, 86)
    assert cache_path.stat().st_mtime == pytest.approx(
        downloaded_at,
        abs=0.01,
        rel=0,
    )
    assert forbidden_session.calls == []


def test_daily_wiki_theme_only_reuses_stale_media_without_http(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    cache_path = media_cache_path(tmp_path, DAILY_MEDIA_URL)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(png_bytes((24, 68, 112)))
    stale_timestamp = time.time() - (2 * 60 * 60)
    os.utime(cache_path, (stale_timestamp, stale_timestamp))
    forbidden_session = RecordingImageSession(forbidden=True)
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: forbidden_session)

    loaded = plugin._download_image(
        DAILY_MEDIA_URL,
        (96, 72),
        {"imageCacheHours": 1, "_theme_render_only": True},
    )

    assert loaded.getpixel((0, 0)) == (24, 68, 112)
    assert forbidden_session.calls == []


def test_daily_wiki_media_url_change_uses_a_new_cache_object(tmp_path):
    plugin = make_plugin(tmp_path)
    first = plugin._media_cache_path(DAILY_MEDIA_URL)
    second = plugin._media_cache_path(f"{DAILY_MEDIA_URL}?revision=2")

    plugin._write_cached_media(first, Image.new("RGB", (8, 8), (10, 20, 30)))
    plugin._write_cached_media(second, Image.new("RGB", (8, 8), (40, 50, 60)))

    assert first != second
    assert first.is_file()
    assert second.is_file()
    assert len(first.stem) == 64
    assert len(second.stem) == 64


def test_daily_wiki_media_cache_uses_managed_budget_namespace(tmp_path):
    plugin = make_plugin(tmp_path)

    namespace = plugin._media_cache_namespace()

    assert namespace.root == tmp_path / "media"
    assert namespace.budget == CacheBudget(
        max_age_seconds=30 * 24 * 60 * 60,
        max_files=256,
        max_bytes=50 * 1024 * 1024,
    )
    assert wiki_module.MEDIA_CACHE_BUDGET == namespace.budget


def test_daily_wiki_atomic_cache_replace_failure_preserves_existing_file(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    cache_path = plugin._media_cache_path(DAILY_MEDIA_URL)
    plugin._write_cached_media(
        cache_path,
        Image.new("RGB", (8, 8), (10, 20, 30)),
    )
    original = cache_path.read_bytes()

    def fail_replace(_source, _target):
        raise PermissionError("simulated atomic replace failure")

    monkeypatch.setattr(cache_manager.os, "replace", fail_replace)
    plugin._write_cached_media(
        cache_path,
        Image.new("RGB", (8, 8), (200, 210, 220)),
    )

    assert cache_path.read_bytes() == original
    assert not list(cache_path.parent.glob("*.tmp"))


def test_daily_wiki_media_cache_rejects_symlink_root_without_os_privileges(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    media_root = tmp_path / "media"
    media_root.mkdir()
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == media_root or original(path),
    )

    with pytest.raises(CachePathError):
        plugin._media_cache_path(DAILY_MEDIA_URL)


def test_daily_wiki_theme_only_reads_stale_cached_media_without_http(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 25, 10, 0)

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(
        plugin,
        "_fetch_live_payload",
        lambda current, language, _fallback, settings: media_payload(
            plugin,
            current,
            language,
            settings,
        ),
    )
    monkeypatch.setattr(plugin, "_write_context", lambda *_args: None)
    plugin._daily_payload({"language": "en"}, now)
    for url, payload in (
        (DAILY_MEDIA_URL, png_bytes((30, 90, 160))),
        (HISTORY_MEDIA_URL, png_bytes((180, 120, 45))),
    ):
        path = media_cache_path(tmp_path, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        os.utime(path, (1, 1))

    forbidden_session = RecordingImageSession(forbidden=True)
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: forbidden_session)
    image = plugin.generate_image(
        {
            "language": "en",
            "_inkypi_theme": canonical_theme("night"),
            "_theme_render_only": True,
        },
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert forbidden_session.calls == []


def test_daily_wiki_theme_only_with_warm_source_and_cold_media_stays_offline(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 25, 10, 0)
    fetch_calls = 0

    def fake_fetch(current, language, _fallback, settings):
        nonlocal fetch_calls
        fetch_calls += 1
        return media_payload(plugin, current, language, settings)

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(plugin, "_fetch_live_payload", fake_fetch)
    monkeypatch.setattr(plugin, "_write_context", lambda *_args: None)
    plugin._daily_payload({"language": "en"}, now)
    assert not (tmp_path / "media").exists()

    forbidden_session = RecordingImageSession(forbidden=True)
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: forbidden_session)
    image = plugin.generate_image(
        {
            "language": "en",
            "_inkypi_theme": canonical_theme("night"),
            "_theme_render_only": True,
        },
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert fetch_calls == 1
    assert forbidden_session.calls == []


def test_daily_wiki_theme_only_with_corrupt_media_stays_offline(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 25, 10, 0)

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(
        plugin,
        "_fetch_live_payload",
        lambda current, language, _fallback, settings: media_payload(
            plugin,
            current,
            language,
            settings,
        ),
    )
    monkeypatch.setattr(plugin, "_write_context", lambda *_args: None)
    plugin._daily_payload({"language": "en"}, now)
    for url in (DAILY_MEDIA_URL, HISTORY_MEDIA_URL):
        path = media_cache_path(tmp_path, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a valid image")

    forbidden_session = RecordingImageSession(forbidden=True)
    monkeypatch.setattr(wiki_module, "get_http_session", lambda: forbidden_session)
    image = plugin.generate_image(
        {
            "language": "en",
            "_inkypi_theme": canonical_theme("night"),
            "_theme_render_only": True,
        },
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert forbidden_session.calls == []


def test_render_page_returns_display_image(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "en", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (320, 240), (80, 120, 160)))

    image = plugin._render_page((800, 480), payload, {}, datetime(2026, 6, 25, 10, 0))

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)


def test_render_page_downloads_selected_history_image_with_positive_target(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "zh-cn", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"
    calls = []

    def fake_download(url, target_size, _settings):
        calls.append((url, target_size))
        return Image.new("RGB", (max(1, target_size[0]), max(1, target_size[1])), (80, 120, 160))

    monkeypatch.setattr(plugin, "_download_image", fake_download)

    plugin._render_page((800, 480), payload, {"language": "zh-cn"}, datetime(2026, 6, 25, 10, 0))

    history_targets = [target for url, target in calls if url == payload["history_image_url"]]
    assert history_targets
    assert all(width > 0 and height > 0 for width, height in history_targets)


def test_zh_date_page_events_keep_link_text(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    html = """
    <h2 id="\u5927\u4e8b\u8bb0">\u5927\u4e8b\u8bb0</h2>
    <ul><li><a href="/wiki/1991\u5e74">1991\u5e74</a>\uff1a\u514b\u7f57\u5730\u4e9a\u548c<a href="/wiki/\u65af\u6d1b\u6587\u5c3c\u4e9a">\u65af\u6d1b\u6587\u5c3c\u4e9a</a>\u5404\u81ea\u5ba3\u5e03\u8131\u79bb\u5357\u65af\u62c9\u592b\u72ec\u7acb\u3002</li></ul>
    <h2 id="\u51fa\u751f">\u51fa\u751f</h2>
    <ul><li>1991\u5e74\uff1a\u4e0d\u5e94\u8be5\u8bfb\u5230\u51fa\u751f\u533a\u5757</li></ul>
    """
    monkeypatch.setattr(plugin, "_get_json", lambda *_args, **_kwargs: {"parse": {"text": html}})

    events = plugin._fetch_zh_date_page_events(datetime(2026, 6, 25, 10, 0))

    assert events == [{"year": "1991", "text": "\u514b\u7f57\u5730\u4e9a\u548c\u65af\u6d1b\u6587\u5c3c\u4e9a\u5404\u81ea\u5ba3\u5e03\u8131\u79bb\u5357\u65af\u62c9\u592b\u72ec\u7acb\u3002"}]


def test_zh_date_page_text_restores_missing_link_text(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    feed = sample_feed()
    feed["onthisday"][0] = {
        "year": 1991,
        "text": "\u548c\u5404\u81ea\u5ba3\u5e03\u8131\u79bb\u5357\u65af\u62c9\u592b\u72ec\u7acb\uff0c\u968f\u540e\u5f15\u53d1\u957f\u671f\u7684\u5357\u65af\u62c9\u592b\u5185\u6218\u3002",
        "pages": [
            {"titles": {"normalized": "1991\u5e74"}},
            {"titles": {"normalized": "\u514b\u7f57\u5730\u4e9a"}},
            {"titles": {"normalized": "\u65af\u6d1b\u6587\u5c3c\u4e9a"}},
            {"titles": {"normalized": "\u5357\u65af\u62c9\u592b\u5185\u6218"}},
        ],
    }
    full_text = "\u514b\u7f57\u5730\u4e9a\u548c\u65af\u6d1b\u6587\u5c3c\u4e9a\u5404\u81ea\u5ba3\u5e03\u8131\u79bb\u5357\u65af\u62c9\u592b\u72ec\u7acb\uff0c\u968f\u540e\u5f15\u53d1\u957f\u671f\u7684\u5357\u65af\u62c9\u592b\u5185\u6218\u3002"
    monkeypatch.setattr(plugin, "_fetch_zh_date_page_events", lambda _now: [{"year": "1991", "text": full_text}])
    monkeypatch.setattr(plugin, "_apply_simplified_chinese_variant", lambda payload, _article: payload)

    payload = plugin._payload_from_feed(feed, "zh-cn", {}, now=datetime(2026, 6, 25, 10, 0))

    assert payload["on_this_day"][0]["text"] == full_text


def test_local_fallback_uses_requested_language_when_available(tmp_path):
    plugin = make_plugin(tmp_path)

    payload = plugin._local_fallback_payload("zh-cn", "2026-06-25")

    assert payload["language"] == "zh-cn"
    assert payload["title"]
    assert payload["extract"]


def test_empty_fallback_language_disables_fallback(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._fallback_language({"fallbackLanguage": ""}, "zh-cn") == ""


def test_live_payload_logs_primary_language_failure_when_fallback_succeeds(
    tmp_path,
    monkeypatch,
    caplog,
):
    plugin = make_plugin(tmp_path)

    def fake_fetch_feed(_now, language):
        if language == "zh":
            raise TimeoutError("private upstream detail")
        return sample_feed()

    monkeypatch.setattr(plugin, "_fetch_feed", fake_fetch_feed)
    caplog.set_level(30, logger=wiki_module.__name__)

    payload = plugin._fetch_live_payload(
        datetime(2026, 8, 28, 1, 19),
        "zh-cn",
        "en",
        {},
    )

    assert payload["language"] == "en"
    assert caplog.messages == [
        "DailyWikiPage primary language zh-cn failed; using fallback language en (TimeoutError)"
    ]
    assert "private upstream detail" not in caplog.text


def test_live_payload_combined_failures_only_include_safe_error_types(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)

    def fake_fetch_feed(_now, language):
        if language == "zh":
            raise TimeoutError("primary credential=do-not-log")
        raise ConnectionError("fallback token=do-not-log")

    monkeypatch.setattr(plugin, "_fetch_feed", fake_fetch_feed)

    with pytest.raises(RuntimeError) as exc_info:
        plugin._fetch_live_payload(
            datetime(2026, 8, 28, 1, 19),
            "zh-cn",
            "en",
            {},
        )

    message = str(exc_info.value)
    assert message == "zh-cn: TimeoutError; en: ConnectionError"
    assert "credential" not in message
    assert "token" not in message


def test_simplified_chinese_payload_uses_zh_cn_variant(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    traditional_title = "\u4fe0\u76dc\u7375\u8eca\u624bV\u7372\u734e\u8207\u63d0\u540d\u5217\u8868"
    simplified_title = "\u4fa0\u76d7\u730e\u8f66\u624bV\u83b7\u5956\u4e0e\u63d0\u540d\u5217\u8868"
    feed = {
        "tfa": {
            "pageid": 5997202,
            "titles": {"normalized": traditional_title},
            "description": "\u7dad\u57fa\u5a92\u9ad4\u5217\u8868\u689d\u76ee",
            "extract": "\u300a\u4fe0\u76dc\u7375\u8eca\u624bV\u300b\u662f\u4e00\u6b3e\u958b\u653e\u4e16\u754c\u52a8\u4f5c\u5192\u9669\u6e38\u620f\u3002",
            "content_urls": {"desktop": {"page": "https://zh.wikipedia.org/wiki/original"}},
        },
        "onthisday": [{"year": 2001, "text": "\u7dad\u57fa\u767e\u79d1\u4e0a\u7ebf\u3002"}],
        "mostread": {"articles": []},
    }

    def fake_get_json(url, params=None):
        assert params["variant"] == "zh-cn"
        if params["action"] == "query":
            return {
                "query": {
                    "pages": [{
                        "pageid": 5997202,
                        "title": traditional_title,
                        "extract": "\u300a\u4fa0\u76d7\u730e\u8f66\u624bV\u300b\u662f\u4e00\u6b3e\u5f00\u653e\u4e16\u754c\u52a8\u4f5c\u5192\u9669\u6e38\u620f\u3002",
                        "fullurl": "https://zh.wikipedia.org/wiki/original",
                        "thumbnail": {"source": "https://example.com/zh-cn.jpg"},
                    }]
                }
            }
        if params["action"] == "parse":
            return {"parse": {"displaytitle": f"<span>{simplified_title}</span>"}}
        raise AssertionError(params)

    monkeypatch.setattr(plugin, "_get_json", fake_get_json)
    monkeypatch.setattr(plugin, "_convert_zh_cn_texts", lambda values: [plugin._to_simplified_cn(value) for value in values])

    payload = plugin._payload_from_feed(feed, "zh-cn", {})

    assert payload["language"] == "zh-cn"
    assert payload["title"] == simplified_title
    assert payload["description"] == "\u7ef4\u57fa\u5a92\u4f53\u5217\u8868\u6761\u76ee"
    assert payload["extract"].startswith("\u300a\u4fa0\u76d7\u730e\u8f66\u624bV\u300b")
    assert "\u4fe0" not in payload["title"]
    assert payload["image_url"] == "https://example.com/zh-cn.jpg"
    assert payload["page_url"].startswith("https://zh.wikipedia.org/zh-cn/")
    assert payload["most_read"] == []


def test_swiss_day_and_canonical_night_use_epaper_readable_contrast(tmp_path):
    plugin = make_plugin(tmp_path)
    day = plugin._palette({"theme": "dark", "_inkypi_theme": canonical_theme("day")})
    night = plugin._palette({"theme": "paper", "_inkypi_theme": canonical_theme("night")})

    assert day["background"] == day["panel"] == (255, 255, 255)
    assert day["ink"] == day["rule"] == (0, 0, 0)
    assert day["accent"] == (0, 82, 255)
    assert night["background"] == night["panel"] == (0, 0, 0)
    assert night["ink"] == night["rule"] == (255, 255, 255)
    assert night["accent"] == (92, 158, 255)
    assert luma(day["background"]) - luma(day["muted"]) >= 140
    assert luma(night["muted"]) - luma(night["background"]) >= 140


def test_daily_wiki_presentation_is_explicit_no_change(monkeypatch, tmp_path):
    plugin = make_plugin(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("NO_CHANGE presentation must not render, fetch, or write")

    monkeypatch.setattr(plugin, "generate_image", forbidden)
    monkeypatch.setattr(plugin, "_daily_payload", forbidden)
    monkeypatch.setattr(plugin, "_write_cache", forbidden)
    monkeypatch.setattr(plugin, "_write_context", forbidden)

    assert "presentation_mode" in DailyWikiPage.__dict__
    assert plugin.presentation_mode({}) is PresentationMode.NO_CHANGE


def test_daily_wiki_provenance_covers_live_fresh_stale_and_local(monkeypatch, tmp_path):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 7, 12, 10, 0)
    settings = {"language": "en"}
    monkeypatch.setattr(
        plugin,
        "_fetch_live_payload",
        lambda current, language, _fallback, local_settings: media_payload(
            plugin,
            current,
            language,
            local_settings,
        ),
    )
    live = plugin._daily_payload(settings, now)
    fresh = plugin._daily_payload(settings, now)

    cache = plugin._read_cache()
    cache["cache_key"] = "wrong-key"
    plugin._write_cache(cache)
    monkeypatch.setattr(
        plugin,
        "_fetch_live_payload",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    stale = plugin._daily_payload({**settings, "forceRefresh": True}, now)

    plugin._cache_path().unlink()
    local = plugin._daily_payload({**settings, "forceRefresh": True}, now)

    assert live["_source_provenance"] == "live"
    assert fresh["_source_provenance"] == "fresh_cache"
    assert stale["_source_provenance"] == "stale_cache"
    assert local["_source_provenance"] == "local_fallback"


def test_daily_wiki_theme_provenance_is_read_only_and_context_has_health_label(
    monkeypatch,
    tmp_path,
):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 7, 12, 10, 0)
    settings = {
        "language": "en",
        "_theme_render_only": True,
        "_inkypi_theme": canonical_theme("day"),
    }
    payload = plugin._local_fallback_payload("en", "2026-07-12")
    key = plugin._cache_key("2026-07-12", settings, "en", "")
    plugin._write_cache(
        {
            "schema": wiki_module.CACHE_SCHEMA_VERSION,
            "cache_key": key,
            "payload": payload,
        }
    )
    before = plugin._cache_path().read_bytes()
    captured = []
    monkeypatch.setattr(
        wiki_module,
        "write_context",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    plugin._write_context({**payload, "_source_provenance": "fresh_cache"}, now)
    assert captured[0][0][1]["source_provenance"] == "fresh_cache"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("theme-only work must not fetch or write")

    monkeypatch.setattr(plugin, "_fetch_live_payload", forbidden)
    monkeypatch.setattr(plugin, "_write_context", forbidden)
    monkeypatch.setattr(plugin, "_write_cache", forbidden)
    monkeypatch.setattr(
        plugin,
        "_render_page",
        lambda *_args: Image.new("RGB", (2, 1), "white"),
    )
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)

    image = plugin.generate_image(settings, FakeDeviceConfig())

    assert read_source_provenance(image).value == "fresh_cache"
    assert plugin._cache_path().read_bytes() == before


def test_daily_wiki_cold_theme_render_does_not_create_cache_tree(
    monkeypatch,
    tmp_path,
):
    cache_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    plugin = DailyWikiPage({"id": "daily_wiki_page"})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cold theme-only render must not fetch or write")

    monkeypatch.setattr(plugin, "_fetch_live_payload", forbidden)
    monkeypatch.setattr(plugin, "_write_cache", forbidden)
    monkeypatch.setattr(plugin, "_write_context", forbidden)

    with pytest.raises(RuntimeError, match="matching cached source data"):
        plugin.generate_image(
            {
                "language": "en",
                "_theme_render_only": True,
                "_inkypi_theme": canonical_theme("day"),
            },
            FakeDeviceConfig(),
        )

    assert not cache_root.exists()

    writer = DailyWikiPage({"id": "daily_wiki_page"})
    writer._write_cache({"schema": wiki_module.CACHE_SCHEMA_VERSION})
    assert (
        cache_root / "plugins" / "daily_wiki_page" / "cache" / "daily.json"
    ).is_file()
