import hashlib
import os
import sys
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_wiki_page import daily_wiki_page as wiki_module  # noqa: E402
from plugins.daily_wiki_page.daily_wiki_page import DailyWikiPage, DAILY_IMAGE_TITLE_PATH, DAILY_HEADER_FILLER_PATH, HISTORY_TITLE_WORDMARK_PATH, DAILY_CAPTION_GAP, DAILY_CAPTION_LINE_SPACING, EPAPER_RULE_WIDTH, HISTORY_BODY_Y_OFFSET, HISTORY_FLOAT_MIN_TEXT_WIDTH, HISTORY_IMAGE_GAP, HISTORY_IMAGE_HEIGHT, HISTORY_IMAGE_WIDTH, HISTORY_LINE_SPACING, HISTORY_MIN_EVENT_FONT_SIZE, HISTORY_TEXT_INDENT, HISTORY_TITLE_RULE_GAP, HISTORY_TITLE_Y_OFFSET, HISTORY_TOPIC_PLACEHOLDER_TOP_OFFSET, YEAR_LABEL_Y_OFFSET, TOPIC_PLACEHOLDER_PATH, DEFAULT_FONT  # noqa: E402
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


def test_render_page_downloads_history_image_for_panel_size(tmp_path, monkeypatch):
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
    assert history_targets[0][0] > HISTORY_IMAGE_WIDTH
    assert history_targets[0][1] > HISTORY_IMAGE_HEIGHT


def test_render_page_extends_history_panel_to_left_visual_bottom(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "zh-cn", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"
    captured = {}

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: None)

    def fake_history_panel(_draw, _events, _palette, _x, y, _width, panel_h, *_args, **_kwargs):
        captured["bottom"] = y + panel_h

    monkeypatch.setattr(plugin, "_draw_on_this_day_panel", fake_history_panel)

    plugin._render_page((800, 480), payload, {"language": "zh-cn"}, datetime(2026, 6, 25, 10, 0))

    assert captured["bottom"] == 480 - max(20, min(800, 480) // 18)


def test_history_image_floats_at_history_text_top_right(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (800, 480), (244, 240, 230))
    draw = ImageDraw.Draw(canvas)
    palette = plugin._palette({"theme": "paper"})
    events = [
        {"year": "1991", "text": "First history event text for the wrapped panel."},
        {"year": "1950", "text": "Second history event text for the wrapped panel."},
        {"year": "1938", "text": "Third history event text for the wrapped panel."},
    ]
    captured = {}

    monkeypatch.setattr(plugin, "_load_history_title_wordmark", lambda: None)
    monkeypatch.setattr(plugin, "_draw_history_image", lambda _image, _history_image, x, y, w, h: captured.setdefault("box", (x, y, w, h)))

    plugin._draw_on_this_day_panel(
        draw,
        events,
        palette,
        460,
        80,
        300,
        360,
        plugin._font("Jost", 24, "bold"),
        plugin._font("Jost", 20, "bold"),
        plugin._font("__cjk__", 15),
        plugin._font("Jost", 10),
        True,
        date_key="2026-06-25",
        history_image=Image.new("RGB", (240, 120), (80, 130, 170)),
        target_image=canvas,
    )

    title_font = plugin._font_for_text("历史上的今天", plugin._font("Jost", 24, "bold"))
    title_h = plugin._text_height(draw, "历史上的今天", title_font)
    body_y = 80 + HISTORY_TITLE_Y_OFFSET + title_h + HISTORY_TITLE_RULE_GAP + max(9, 360 // 42) + HISTORY_BODY_Y_OFFSET
    image_x, image_y, image_w, image_h = captured["box"]

    assert image_x == 460 + 300 - image_w
    assert image_y == body_y
    expected_w = 300 - HISTORY_TEXT_INDENT - HISTORY_FLOAT_MIN_TEXT_WIDTH - HISTORY_IMAGE_GAP
    assert image_w == expected_w
    assert image_h == max(42, round(expected_w / 2))


def test_history_float_wraps_overlapping_lines_then_restores_full_width(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (800, 480), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    event_font = plugin._font("__cjk__", 15)
    events = [{"year": "1991", "text": "This historical entry is intentionally long enough to cross several lines before and after the floating image area in the right panel."}]

    rows = plugin._event_rows_for_height(
        draw,
        events,
        260,
        220,
        plugin._font("Jost", 20, "bold"),
        event_font,
        date_key="2026-06-25",
        cjk=True,
        float_width_px=100,
        float_height_px=64,
    )

    assert rows
    line_widths = rows[0]["line_widths"]
    assert line_widths[0] == max(HISTORY_FLOAT_MIN_TEXT_WIDTH, 260 - 100)
    assert line_widths[0] < 260
    assert line_widths[-1] == 260
    assert rows[0]["height"] <= 220


def test_history_rows_stretch_to_bottom_edge(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (800, 480), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    events = [
        {"year": str(1990 + index), "text": "Short event"}
        for index in range(5)
    ]

    rows = plugin._event_rows_for_height(
        draw,
        events,
        260,
        240,
        plugin._font("Jost", 18, "bold"),
        plugin._font("Jost", 13),
        date_key="2026-06-25",
    )

    assert len(rows) == 5
    assert sum(row["height"] for row in rows) == 240
    last_top = sum(row["height"] for row in rows[:-1])
    assert last_top + rows[-1]["text_offset_y"] + rows[-1]["ink_height"] == 240
    assert rows[0]["height"] > rows[-1]["height"]


def test_render_page_allocates_space_for_full_daily_image_caption(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "zh-cn", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"
    long_caption = "\u5b8c\u6574\u56fe\u7247\u8bf4\u660e\u6d4b\u8bd5"
    caption_lines = [
        "\u56fe\u7247\u4fe1\u606f\u7b2c\u4e00\u884c",
        "\u56fe\u7247\u4fe1\u606f\u7b2c\u4e8c\u884c",
        "\u56fe\u7247\u4fe1\u606f\u7b2c\u4e09\u884c",
        "\u56fe\u7247\u4fe1\u606f\u7b2c\u56db\u884c",
        "\u56fe\u7247\u4fe1\u606f\u7b2c\u4e94\u884c",
        "\u56fe\u7247\u4fe1\u606f\u7b2c\u516d\u884c",
    ]
    payload["image_caption"] = long_caption
    drawn_text = []
    captured = {}

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (320, 240), (80, 120, 160)))
    monkeypatch.setattr(plugin, "_draw_on_this_day_panel", lambda *_args, **_kwargs: None)

    def fake_draw_article_image(_draw, _image, _article_image, _palette, _x, _y, _width, height):
        captured["image_h"] = height

    original_wrap_all = plugin._wrap_all

    def fake_wrap_all(draw, text, font, max_width):
        if text == long_caption:
            return caption_lines
        return original_wrap_all(draw, text, font, max_width)

    original_text = ImageDraw.ImageDraw.text

    def fake_text(self, xy, text, font=None, fill=None, *args, **kwargs):
        drawn_text.append(text)
        return original_text(self, xy, text, font=font, fill=fill, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_article_image", fake_draw_article_image)
    monkeypatch.setattr(plugin, "_wrap_all", fake_wrap_all)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", fake_text)

    plugin._render_page((800, 480), payload, {"language": "zh-cn"}, datetime(2026, 6, 25, 10, 0))

    assert all(line in drawn_text for line in caption_lines)
    assert captured["image_h"] < int(480 * 0.61)


def test_daily_image_caption_is_close_to_image_and_readable(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "zh-cn", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"
    caption = "\u56fe\u7247\u4fe1\u606f\u5e94\u8be5\u8d34\u8fd1\u4e3b\u56fe\u5e76\u6e05\u6670\u53ef\u8bfb"
    payload["image_caption"] = caption
    ink = plugin._palette({"theme": "paper"})["ink"]
    calls = []
    captured = {}

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (320, 240), (80, 120, 160)))
    monkeypatch.setattr(plugin, "_draw_on_this_day_panel", lambda *_args, **_kwargs: None)

    def fake_draw_article_image(_draw, _image, _article_image, _palette, _x, y, _width, height):
        captured["image_bottom"] = y + height

    original_text = ImageDraw.ImageDraw.text

    def fake_text(self, xy, text, font=None, fill=None, *args, **kwargs):
        calls.append((xy, text, getattr(font, "size", 0), fill))
        return original_text(self, xy, text, font=font, fill=fill, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_article_image", fake_draw_article_image)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", fake_text)

    plugin._render_page((800, 480), payload, {"language": "zh-cn", "theme": "paper"}, datetime(2026, 6, 25, 10, 0))

    caption_calls = [(xy, size, fill) for xy, text, size, fill in calls if text == caption]
    assert caption_calls
    caption_xy, caption_size, caption_fill = caption_calls[0]
    assert DAILY_CAPTION_GAP == 4
    assert DAILY_CAPTION_LINE_SPACING <= 1.12
    assert caption_xy[1] - captured["image_bottom"] == DAILY_CAPTION_GAP
    assert caption_size == 18
    assert caption_fill == ink


def test_right_history_fonts_use_next_larger_size_step(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "zh-cn", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"
    captured = {}

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: None)

    def fake_history_panel(_draw, _events, _palette, _x, _y, _width, _height, title_font, year_font, event_font, _small_font, *_args, **_kwargs):
        captured["title_size"] = getattr(title_font, "size", 0)
        captured["year_size"] = getattr(year_font, "size", 0)
        captured["event_size"] = getattr(event_font, "size", 0)

    monkeypatch.setattr(plugin, "_draw_on_this_day_panel", fake_history_panel)

    plugin._render_page((800, 480), payload, {"language": "zh-cn", "theme": "paper"}, datetime(2026, 6, 25, 10, 0))

    assert captured["title_size"] >= 29
    assert captured["year_size"] >= 23
    assert captured["event_size"] >= 20





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
def test_daily_image_title_asset_has_transparent_background():
    asset = Image.open(DAILY_IMAGE_TITLE_PATH).convert("RGBA")
    alpha = asset.getchannel("A")

    assert asset.width > asset.height
    assert alpha.getbbox() is not None
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((asset.width - 1, 0)) == 0
    assert alpha.getpixel((0, asset.height - 1)) == 0
    assert alpha.getpixel((asset.width - 1, asset.height - 1)) == 0


def test_daily_header_filler_asset_has_exact_transparent_size():
    asset = Image.open(DAILY_HEADER_FILLER_PATH).convert("RGBA")
    alpha = asset.getchannel("A")

    assert asset.size == (424, 52)
    assert alpha.getbbox() is not None
    assert alpha.getextrema() == (0, 255)
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((asset.width - 1, 0)) == 0
    assert alpha.getpixel((0, asset.height - 1)) == 0
    assert alpha.getpixel((asset.width - 1, asset.height - 1)) == 0


def test_history_title_wordmark_asset_has_transparent_background():
    asset = Image.open(HISTORY_TITLE_WORDMARK_PATH).convert("RGBA")
    alpha = asset.getchannel("A")

    assert asset.width > asset.height
    assert alpha.getbbox() is not None
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((asset.width - 1, 0)) == 0
    assert alpha.getpixel((0, asset.height - 1)) == 0
    assert alpha.getpixel((asset.width - 1, asset.height - 1)) == 0

def test_daily_image_header_draws_pixel_filler_between_title_and_date(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "zh-cn", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"
    captured = {}

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_load_daily_image_title", lambda: Image.new("RGBA", (620, 165), (96, 64, 42, 255)))
    monkeypatch.setattr(plugin, "_load_daily_header_filler", lambda: Image.new("RGBA", (424, 52), (10, 20, 30, 255)))
    monkeypatch.setattr(plugin, "_draw_daily_image_title", lambda *_args, **_kwargs: None)

    def fake_draw_filler(_image, filler, x1, x2, y, rule_y):
        captured["filler"] = (x1, x2, y, rule_y, filler.size)
        return (x1, rule_y - filler.height - 2, filler.width, filler.height)

    monkeypatch.setattr(plugin, "_draw_daily_header_filler", fake_draw_filler)

    plugin._render_page((800, 480), payload, {"language": "zh-cn"}, datetime(2026, 6, 25, 10, 0))

    x1, x2, y, rule_y, filler_size = captured["filler"]
    assert filler_size == (424, 52)
    assert 250 <= x1 <= 285
    assert 685 <= x2 <= 710
    assert x2 - x1 >= 424
    assert y >= 0
    assert rule_y == 64


def test_daily_image_header_uses_title_image_asset(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "zh-cn", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"
    captured = {}

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_load_daily_image_title", lambda: Image.new("RGBA", (320, 88), (96, 64, 42, 255)))

    def fake_draw_title(_image, _title_image, x, y, max_width, max_height, rule_y=None):
        captured["box"] = (x, y, max_width, max_height)
        captured["rule_y"] = rule_y

    monkeypatch.setattr(plugin, "_draw_daily_image_title", fake_draw_title)

    plugin._render_page((800, 480), payload, {"language": "zh-cn"}, datetime(2026, 6, 25, 10, 0))

    assert captured["box"][0] > 0
    assert captured["box"][1] >= 0
    assert captured["box"][2] >= 190
    assert 28 <= captured["box"][3] <= 58
    assert captured["rule_y"] is not None


def test_daily_header_filler_keeps_native_size_when_slot_matches(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGBA", (520, 90), (0, 0, 0, 0))
    filler = Image.new("RGBA", (424, 52), (10, 20, 30, 255))

    box = plugin._draw_daily_header_filler(canvas, filler, 48, 472, 10, 64)

    assert box == (48, 10, 424, 52)
    assert canvas.getpixel((48, 10)) == (10, 20, 30, 255)
    assert canvas.getpixel((471, 61)) == (10, 20, 30, 255)


def test_daily_image_title_rule_aligns_with_header_rule(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (180, 90), (255, 255, 255))
    title = Image.new("RGBA", (120, 50), (0, 0, 0, 0))
    draw = ImageDraw.Draw(title)
    draw.line((8, 38, 112, 38), fill=(96, 64, 42, 255), width=2)

    plugin._draw_daily_image_title(canvas, title, 12, 4, 120, 50, rule_y=60)

    assert canvas.getpixel((20, 60)) != (255, 255, 255)
    assert canvas.getpixel((20, 52)) == (255, 255, 255)

def test_daily_image_header_falls_back_to_text_when_title_asset_missing(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "zh-cn", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"
    captured = []

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_load_daily_image_title", lambda: None)

    original_font_for_text = plugin._font_for_text

    def fake_font_for_text(text, fallback_font):
        if text == "\u6bcf\u65e5\u56fe\u7247":
            captured.append(getattr(fallback_font, "size", 0))
        return original_font_for_text(text, fallback_font)

    monkeypatch.setattr(plugin, "_font_for_text", fake_font_for_text)

    plugin._render_page((800, 480), payload, {"language": "zh-cn"}, datetime(2026, 6, 25, 10, 0))

    assert captured and captured[0] >= 22

def test_render_page_passes_payload_date_to_history_panel(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = plugin._payload_from_feed(sample_feed(), "zh-cn", {})
    payload["source_state"] = "live"
    payload["date"] = "2026-06-25"
    captured = {}

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: None)

    def fake_panel(*args, **kwargs):
        captured["date_key"] = kwargs.get("date_key")

    monkeypatch.setattr(plugin, "_draw_on_this_day_panel", fake_panel)

    plugin._render_page((800, 480), payload, {"language": "zh-cn"}, datetime(2026, 6, 25, 10, 0))

    assert captured["date_key"] == "2026-06-25"

def test_local_fallback_uses_requested_language_when_available(tmp_path):
    plugin = make_plugin(tmp_path)

    payload = plugin._local_fallback_payload("zh-cn", "2026-06-25")

    assert payload["language"] == "zh-cn"
    assert payload["title"]
    assert payload["extract"]


def test_empty_fallback_language_disables_fallback(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._fallback_language({"fallbackLanguage": ""}, "zh-cn") == ""


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



def test_article_image_is_contained_instead_of_cropped(tmp_path):
    plugin = make_plugin(tmp_path)
    target = Image.new("RGB", (240, 130), (240, 240, 240))
    draw = ImageDraw.Draw(target)
    source = Image.new("RGB", (60, 180), (20, 160, 20))
    source_draw = ImageDraw.Draw(source)
    source_draw.rectangle((0, 0, 59, 35), fill=(250, 0, 0))
    source_draw.rectangle((0, 145, 59, 179), fill=(0, 0, 250))
    palette = {"panel": (230, 230, 230), "rule": (100, 100, 100)}

    plugin._draw_article_image(draw, target, source, palette, 10, 10, 200, 100)

    assert target.getpixel((110, 13))[0] > 200
    assert target.getpixel((110, 107))[2] > 200
    assert target.getpixel((10, 10)) == (240, 240, 240)
    assert target.getpixel((209, 109)) == (240, 240, 240)
def test_on_this_day_rows_fit_available_height(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (800, 480), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    year_font = plugin._font("Jost", 18, "bold")
    event_font = plugin._font("Jost", 13)
    long_text = "\u8fd9\u662f\u4e00\u4e2a\u5f88\u957f\u7684\u5386\u53f2\u4e8b\u4ef6\u63cf\u8ff0\uff0c\u7528\u6765\u786e\u8ba4\u53f3\u4fa7\u65f6\u95f4\u7ebf\u5728\u6709\u9650\u9ad8\u5ea6\u91cc\u4e0d\u4f1a\u4e92\u76f8\u91cd\u53e0\uff0c\u4e5f\u4e0d\u4f1a\u88ab\u5e95\u90e8\u88c1\u6389\u3002"
    long_events = []
    for index in range(5):
        long_events.append({
            "year": str(1900 + index),
            "topics_text": "\u514b\u7f57\u5730\u4e9a\u3001\u65af\u6d1b\u6587\u5c3c\u4e9a\u3001\u5357\u65af\u62c9\u592b\u89e3\u4f53",
            "text": long_text,
        })

    rows = plugin._event_rows_for_height(draw, long_events, 280, 310, year_font, event_font, date_key="2026-06-25", cjk=True)

    assert rows
    assert len(rows) <= 5
    assert rows[0]["date_label"] == "1900\u5e746\u670825\u65e5"
    assert rows[0]["topic_lines"] == []
    assert rows[0]["source_text"] == long_text
    assert "\u5173\u952e\u8bcd" not in "".join(rows[0]["body_lines"])
    assert "\u4e8b\u4ef6\uff1a" not in "".join(rows[0]["body_lines"])
    assert "..." not in "".join(rows[0]["body_lines"])
    assert rows[0]["line_h"] >= int(plugin._text_height(draw, "Ag", event_font) * HISTORY_LINE_SPACING)
    assert rows[0]["section_gap_h"] == 0
    assert rows[0]["body_gap_h"] > 0
    assert sum(row["height"] for row in rows) <= 310
    assert all(row["body_lines"] for row in rows)

def test_full_wrapped_history_rows_drop_overflow_instead_of_ellipsizing(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (800, 480), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    year_font = plugin._font("Jost", 18, "bold")
    event_font = plugin._font("Jost", 13)
    events = [
        {"year": "1991", "text": "\u7b2c\u4e00\u6761\u5386\u53f2\u4e8b\u4ef6\u539f\u6587\u5e94\u8be5\u5b8c\u6574\u663e\u793a\u3002"},
        {"year": "1992", "text": "\u7b2c\u4e8c\u6761\u662f\u4e00\u6bb5\u975e\u5e38\u975e\u5e38\u975e\u5e38\u957f\u7684\u6587\u5b57\uff0c\u5728\u7a7a\u95f4\u4e0d\u8db3\u65f6\u4e0d\u5e94\u8be5\u88ab\u7701\u7565\u53f7\u622a\u65ad\u663e\u793a\u3002"},
    ]

    rows = plugin._event_rows_for_height(draw, events, 160, 56, year_font, event_font, date_key="2026-06-25", cjk=True)

    assert len(rows) == 1
    assert rows[0]["source_text"] == events[0]["text"]
    assert "..." not in "".join(rows[0]["body_lines"])
def test_event_date_label_uses_feed_month_day(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._event_date_label("1991", "2026-06-25", cjk=True) == "1991\u5e746\u670825\u65e5"
    assert plugin._event_date_label("1991", "2026-06-25", cjk=False) == "1991-06-25"
    assert plugin._event_date_label("1991", "bad-date", cjk=True) == "1991"


def test_history_title_is_drawn_at_offset_and_rule_below(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    text_calls = []
    line_calls = []

    class FakeDraw:
        def text(self, xy, text, font=None, fill=None):
            text_calls.append((xy, text, font))

        def line(self, xy, fill=None, width=1):
            line_calls.append((xy, fill, width))

    monkeypatch.setattr(plugin, "_text_height", lambda *_args, **_kwargs: 18)
    monkeypatch.setattr(plugin, "_event_rows_for_height", lambda *_args, **_kwargs: [])

    plugin._draw_on_this_day_panel(
        FakeDraw(),
        [{"year": "1991", "text": "Event"}],
        {"ink": (0, 0, 0), "rule": (0, 0, 0), "accent": (0, 0, 0), "dim": (0, 0, 0)},
        10,
        20,
        220,
        180,
        plugin._font("Jost", 18, "bold"),
        plugin._font("Jost", 16, "bold"),
        plugin._font("Jost", 12),
        plugin._font("Jost", 10),
        True,
        "2026-06-25",
    )

    expected_title_y = 20 + HISTORY_TITLE_Y_OFFSET
    expected_line_y = expected_title_y + 18 + HISTORY_TITLE_RULE_GAP
    assert text_calls[0][:2] == ((10, expected_title_y), "\u5386\u53f2\u4e0a\u7684\u4eca\u5929")
    assert line_calls[0][0] == (10, expected_line_y, 230, expected_line_y)


def test_history_title_wordmark_used_for_wide_cjk_panel(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (360, 180), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    title_font = plugin._font("Jost", 28, "bold")
    year_font = plugin._font("Jost", 16, "bold")
    event_font = plugin._font("Jost", 12)
    small_font = plugin._font("Jost", 10)
    captured = {}

    monkeypatch.setattr(plugin, "_load_history_title_wordmark", lambda: Image.new("RGBA", (360, 90), (80, 60, 40, 255)))

    def fake_draw_history_title(_image, _title_image, x, y, width, height):
        captured["box"] = (x, y, width, height)
        return 34

    monkeypatch.setattr(plugin, "_draw_history_title_wordmark", fake_draw_history_title)
    monkeypatch.setattr(plugin, "_event_rows_for_height", lambda *_args, **_kwargs: [])

    plugin._draw_on_this_day_panel(
        draw,
        [{"year": "1991", "text": "Event"}],
        {"ink": (0, 0, 0), "rule": (0, 0, 0), "accent": (0, 0, 0), "dim": (0, 0, 0)},
        10,
        20,
        320,
        130,
        title_font,
        year_font,
        event_font,
        small_font,
        True,
        "2026-06-25",
        target_image=canvas,
    )

    assert captured["box"][0] == 10
    assert captured["box"][1] == 18
    assert 180 <= captured["box"][2] <= 320
    assert 28 <= captured["box"][3] <= 38

def test_history_title_wordmark_rule_overlaps_panel_rule(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (360, 180), (255, 255, 255))
    real_draw = ImageDraw.Draw(canvas)
    line_calls = []
    title = Image.new("RGBA", (180, 60), (0, 0, 0, 0))
    title_draw = ImageDraw.Draw(title)
    title_draw.rectangle((16, 10, 96, 24), fill=(40, 40, 40, 255))
    title_draw.line((12, 44, 168, 44), fill=(40, 40, 40, 255), width=2)

    class DrawSpy:
        def __getattr__(self, name):
            return getattr(real_draw, name)

        def line(self, xy, fill=None, width=1):
            line_calls.append(xy)
            return real_draw.line(xy, fill=fill, width=width)

    monkeypatch.setattr(plugin, "_load_history_title_wordmark", lambda: title)
    monkeypatch.setattr(plugin, "_event_rows_for_height", lambda *_args, **_kwargs: [])

    plugin._draw_on_this_day_panel(
        DrawSpy(),
        [{"year": "1991", "text": "Event"}],
        {"ink": (0, 0, 0), "rule": (0, 0, 0), "accent": (0, 0, 0), "dim": (0, 0, 0)},
        10,
        20,
        320,
        130,
        plugin._font("Jost", 28, "bold"),
        plugin._font("Jost", 16, "bold"),
        plugin._font("Jost", 12),
        plugin._font("Jost", 10),
        True,
        "2026-06-25",
        target_image=canvas,
    )

    assert line_calls
    rule_y = line_calls[0][1]
    assert canvas.getpixel((24, rule_y)) != (255, 255, 255)
    assert all(value > 245 for value in canvas.getpixel((300, rule_y - 8)))

def test_history_body_starts_near_title_rule(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (320, 240), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    title_font = plugin._font("Jost", 18, "bold")
    year_font = plugin._font("Jost", 16, "bold")
    event_font = plugin._font("Jost", 12)
    small_font = plugin._font("Jost", 10)
    captured = {}

    def fake_rows(_draw, _events, _text_width_px, available_h, _year_font, _event_font, **_kwargs):
        captured["available_h"] = available_h
        return []

    monkeypatch.setattr(plugin, "_event_rows_for_height", fake_rows)
    plugin._draw_on_this_day_panel(
        draw,
        [{"year": "1991", "text": "Event"}],
        {"ink": (0, 0, 0), "rule": (0, 0, 0), "accent": (0, 0, 0), "dim": (0, 0, 0)},
        10,
        20,
        220,
        180,
        title_font,
        year_font,
        event_font,
        small_font,
        True,
        "2026-06-25",
    )

    title_draw_font = plugin._font_for_text("\u5386\u53f2\u4e0a\u7684\u4eca\u5929", title_font)
    title_h = plugin._text_height(draw, "\u5386\u53f2\u4e0a\u7684\u4eca\u5929", title_draw_font)
    expected_start_offset = HISTORY_TITLE_Y_OFFSET + title_h + HISTORY_TITLE_RULE_GAP + max(9, 180 // 42) + HISTORY_BODY_Y_OFFSET
    assert captured["available_h"] == 180 - expected_start_offset
    assert HISTORY_BODY_Y_OFFSET <= 10


def test_topic_placeholder_asset_has_transparent_background():
    asset = Image.open(TOPIC_PLACEHOLDER_PATH).convert("RGBA")
    alpha = asset.getchannel("A")

    assert asset.width > asset.height
    assert alpha.getbbox() is not None
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((asset.width - 1, 0)) == 0
    assert alpha.getpixel((0, asset.height - 1)) == 0
    assert alpha.getpixel((asset.width - 1, asset.height - 1)) == 0


def test_history_topic_placeholder_draws_in_remaining_space(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (320, 240), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    title_font = plugin._font("Jost", 18, "bold")
    year_font = plugin._font("Jost", 16, "bold")
    event_font = plugin._font("Jost", 12)
    small_font = plugin._font("Jost", 10)
    captured = {}

    def fake_rows(_draw, _events, _text_width_px, available_h, _year_font, _event_font, **_kwargs):
        captured["available_h"] = available_h
        return [{
            "height": 36,
            "date_label": "1991\u5e746\u670825\u65e5",
            "year_h": 0,
            "topic_lines": [],
            "section_gap_h": 0,
            "body_gap_h": 0,
            "body_lines": ["1991\u5e746\u670825\u65e5 Event"],
            "line_h": 15,
        }]

    def fake_placeholder(_image, _placeholder, x, y, width, height):
        captured["placeholder_box"] = (x, y, width, height)

    monkeypatch.setattr(plugin, "_event_rows_for_height", fake_rows)
    monkeypatch.setattr(plugin, "_load_topic_placeholder", lambda: Image.new("RGBA", (120, 32), (80, 60, 40, 255)))
    monkeypatch.setattr(plugin, "_draw_topic_placeholder", fake_placeholder)

    plugin._draw_on_this_day_panel(
        draw,
        [{"year": "1991", "text": "Event"}],
        {"ink": (0, 0, 0), "rule": (0, 0, 0), "accent": (0, 0, 0), "dim": (0, 0, 0)},
        10,
        20,
        220,
        180,
        title_font,
        year_font,
        event_font,
        small_font,
        True,
        "2026-06-25",
        target_image=canvas,
    )

    title_draw_font = plugin._font_for_text("\u5386\u53f2\u4e0a\u7684\u4eca\u5929", title_font)
    title_h = plugin._text_height(draw, "\u5386\u53f2\u4e0a\u7684\u4eca\u5929", title_draw_font)
    body_y = 20 + HISTORY_TITLE_Y_OFFSET + title_h + HISTORY_TITLE_RULE_GAP + max(9, 180 // 42) + HISTORY_BODY_Y_OFFSET

    assert captured["placeholder_box"][0] == 10 + HISTORY_TEXT_INDENT
    assert captured["placeholder_box"][1] == body_y + 36 + HISTORY_TOPIC_PLACEHOLDER_TOP_OFFSET
    assert captured["placeholder_box"][2] == 220 - HISTORY_TEXT_INDENT
    assert captured["placeholder_box"][3] >= 28

def test_history_image_cover_fills_float_box(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (160, 100), (255, 255, 255))
    portrait = Image.new("RGB", (40, 120), (20, 80, 120))

    plugin._draw_history_image(canvas, portrait, 10, 20, 120, 50)

    assert canvas.getpixel((10, 20)) == (20, 80, 120)
    assert canvas.getpixel((129, 69)) == (20, 80, 120)

def test_history_image_float_passes_wrap_dimensions_to_rows(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (320, 240), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    title_font = plugin._font("Jost", 18, "bold")
    year_font = plugin._font("Jost", 16, "bold")
    event_font = plugin._font("Jost", 12)
    small_font = plugin._font("Jost", 10)
    captured = {}
    row_height = 54

    def fake_rows(_draw, _events, _text_width_px, available_h, _year_font, _event_font, **kwargs):
        captured["available_h"] = available_h
        captured["float_width_px"] = kwargs.get("float_width_px")
        captured["float_height_px"] = kwargs.get("float_height_px")
        return [{
            "height": row_height,
            "date_label": "1991年6月25日",
            "year_h": 0,
            "topic_lines": [],
            "section_gap_h": 0,
            "body_gap_h": 0,
            "body_lines": ["1991年6月25日 Event"],
            "line_h": 15,
        }]

    def fake_history_image(_image, _history_image, x, y, width, height):
        captured["image_box"] = (x, y, width, height)

    monkeypatch.setattr(plugin, "_event_rows_for_height", fake_rows)
    monkeypatch.setattr(plugin, "_draw_history_image", fake_history_image)

    plugin._draw_on_this_day_panel(
        draw,
        [{"year": "1991", "text": "Event"}],
        {"ink": (0, 0, 0), "rule": (0, 0, 0), "accent": (0, 0, 0), "dim": (0, 0, 0)},
        10,
        20,
        220,
        180,
        title_font,
        year_font,
        event_font,
        small_font,
        True,
        "2026-06-25",
        history_image=Image.new("RGB", (120, 80), (20, 80, 120)),
        target_image=canvas,
    )

    title_draw_font = plugin._font_for_text("历史上的今天", title_font)
    title_h = plugin._text_height(draw, "历史上的今天", title_draw_font)
    expected_start_offset = HISTORY_TITLE_Y_OFFSET + title_h + HISTORY_TITLE_RULE_GAP + max(9, 180 // 42) + HISTORY_BODY_Y_OFFSET
    body_y = 20 + expected_start_offset

    assert captured["available_h"] == 180 - expected_start_offset
    assert captured["float_width_px"] == captured["image_box"][2] + HISTORY_IMAGE_GAP
    assert captured["float_height_px"] == captured["image_box"][3] + HISTORY_IMAGE_GAP
    image_x, image_y, image_w, image_h = captured["image_box"]
    assert image_x == 10 + 220 - image_w
    assert image_y == body_y
    assert image_w >= 70
    assert image_h >= 42

def test_history_date_label_uses_cjk_font_when_drawing(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (320, 240), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    title_font = plugin._font("Jost", 18, "bold")
    year_font = plugin._font("Jost", 16, "bold")
    event_font = plugin._font("Jost", 12)
    small_font = plugin._font("Jost", 10)
    seen = []
    original_font_for_text = plugin._font_for_text

    def fake_font_for_text(text, fallback_font):
        if text == "1991\u5e746\u670825\u65e5":
            seen.append(text)
        return original_font_for_text(text, fallback_font)

    monkeypatch.setattr(plugin, "_font_for_text", fake_font_for_text)

    plugin._draw_on_this_day_panel(
        draw,
        [{"year": "1991", "text": "\u514b\u7f57\u5730\u4e9a\u548c\u65af\u6d1b\u6587\u5c3c\u4e9a\u5404\u81ea\u5ba3\u5e03\u72ec\u7acb\u3002"}],
        {"ink": (0, 0, 0), "rule": (0, 0, 0), "accent": (0, 0, 0), "dim": (0, 0, 0)},
        10,
        20,
        220,
        180,
        title_font,
        year_font,
        event_font,
        small_font,
        True,
        "2026-06-25",
    )

    assert seen


def test_history_body_draws_with_ink_color_for_readability(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (320, 240), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    title_font = plugin._font("Jost", 18, "bold")
    year_font = plugin._font("Jost", 16, "bold")
    event_font = plugin._font("Jost", 12)
    small_font = plugin._font("Jost", 10)
    body_text = "1991\u5e746\u670825\u65e5 Event"
    date_label = "1991\u5e746\u670825\u65e5"
    ink = (12, 13, 14)
    dim = (150, 150, 150)
    calls = []

    def fake_rows(_draw, _events, _text_width_px, _available_h, _year_font, _event_font, **_kwargs):
        return [{
            "height": 24,
            "date_label": date_label,
            "year_h": 0,
            "topic_lines": [],
            "section_gap_h": 0,
            "body_gap_h": 0,
            "body_lines": [body_text],
            "line_h": 15,
        }]

    original_text = ImageDraw.ImageDraw.text

    def fake_text(self, xy, text, font=None, fill=None, *args, **kwargs):
        calls.append((text, fill))
        return original_text(self, xy, text, font=font, fill=fill, *args, **kwargs)

    monkeypatch.setattr(plugin, "_event_rows_for_height", fake_rows)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", fake_text)

    plugin._draw_on_this_day_panel(
        draw,
        [{"year": "1991", "text": "Event"}],
        {"ink": ink, "rule": (0, 0, 0), "accent": (80, 50, 30), "dim": dim},
        10,
        20,
        220,
        180,
        title_font,
        year_font,
        event_font,
        small_font,
        True,
        "2026-06-25",
    )

    body_fills = [fill for text, fill in calls if text == body_text]
    assert ink in body_fills
    assert dim not in body_fills


def test_history_line_spacing_is_relaxed_for_readability():
    assert 1.06 <= HISTORY_LINE_SPACING <= 1.10


def test_history_current_length_events_fit_five_rows(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (800, 480), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    events = [
        {"year": "2015", "text": "\u7f8e\u56fd\u6700\u9ad8\u6cd5\u9662\u5728\u5965\u8d1d\u683c\u8d39\u5c14\u8bc9\u970d\u5947\u65af\u6848\u88c1\u5b9a\u540c\u6027\u5a5a\u59fb\u53d7\u5230\u300a\u7f8e\u56fd\u5baa\u6cd5\u300b\u4fee\u6b63\u6848\u7684\u4fdd\u969c\u3002"},
        {"year": "1976", "text": "\u52a0\u62ff\u5927\u591a\u4f26\u591a\u7684\u52a0\u62ff\u5927\u56fd\u5bb6\u7535\u89c6\u5854\u9996\u6b21\u5411\u516c\u4f17\u5f00\u653e\uff0c\u4e3a\u5f53\u65f6\u4e16\u754c\u4e0a\u6700\u9ad8\u7684\u81ea\u7acb\u5f0f\u5efa\u7b51\u3002"},
        {"year": "1963", "text": "\u5728\u82cf\u8054\u4e0e\u4e1c\u5fb7\u5efa\u7acb\u67cf\u6797\u5899\u540e\uff0c\u7f8e\u56fd\u603b\u7edf\u7ea6\u7ff0\u00b7\u80af\u5c3c\u8fea\u53d1\u8868\u652f\u6301\u897f\u5fb7\u7684\"\u6211\u662f\u67cf\u6797\u4eba\"\u6f14\u8bb2\u3002"},
        {"year": "1945", "text": "\u8054\u5408\u56fd\u56fd\u9645\u7ec4\u7ec7\u4f1a\u8bae\u7684\u4f1a\u5458\u56fd\u4ee3\u8868\u5728\u7f8e\u56fd\u65e7\u91d1\u5c71\u7b7e\u7f72\u300a\u8054\u5408\u56fd\u5baa\u7ae0\u300b\uff0c\u6b63\u5f0f\u5efa\u7acb\u8054\u5408\u56fd\u3002"},
        {"year": "363", "text": "\u7f57\u9a6c\u5e1d\u56fd\u7687\u5e1d\u5c24\u5229\u5b89\u5728\u7387\u9886\u519b\u961f\u8fdc\u5f81\u6ce2\u65af\u8428\u73ca\u738b\u671d\u65f6\u9635\u4ea1\uff0c\u519b\u961f\u63a8\u7acb\u7ea6\u7ef4\u5b89\u4e3a\u65b0\u4efb\u7687\u5e1d\u3002"},
    ]

    fitted_font, rows = plugin._fit_history_event_rows(
        draw,
        events,
        320,
        310,
        plugin._font("Jost", 23, "bold"),
        plugin._font("Jost", 20),
        date_key="2026-06-26",
        cjk=True,
    )

    assert len(rows) == 5
    assert getattr(fitted_font, "size", 0) >= 15
    assert sum(row["height"] for row in rows) <= 310

def test_history_event_font_adapts_to_keep_five_rows(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (320, 240), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    events = [{"year": str(1990 + index), "text": "Event text"} for index in range(5)]
    attempted_sizes = []

    def fake_rows(_draw, _events, _text_width_px, _available_h, _year_font, event_font, **_kwargs):
        size = getattr(event_font, "size", 0)
        attempted_sizes.append(size)
        count = 3 if size > HISTORY_MIN_EVENT_FONT_SIZE else 5
        return [
            {"height": 12, "date_label": "", "body_lines": ["Event"], "line_h": 12}
            for _index in range(count)
        ]

    monkeypatch.setattr(plugin, "_event_rows_for_height", fake_rows)

    fitted_font, rows = plugin._fit_history_event_rows(
        draw,
        events,
        220,
        160,
        plugin._font("Jost", 18, "bold"),
        plugin._font("Jost", 20),
        date_key="2026-06-25",
        cjk=True,
    )

    assert attempted_sizes[0] == 20
    assert getattr(fitted_font, "size", 0) == HISTORY_MIN_EVENT_FONT_SIZE
    assert len(rows) == 5


def test_history_title_offset_moves_header_up_from_previous_tuning():
    assert HISTORY_TITLE_Y_OFFSET == 0


def test_year_label_offset_moves_years_up_by_ten_pixels():
    assert YEAR_LABEL_Y_OFFSET == -10

def test_canonical_palettes_use_epaper_readable_contrast(tmp_path):
    plugin = make_plugin(tmp_path)
    day_theme = canonical_theme("day")
    night_theme = canonical_theme("night")
    day = plugin._palette({"theme": "dark", "_inkypi_theme": day_theme})
    night = plugin._palette({"theme": "paper", "_inkypi_theme": night_theme})

    assert day == {**day_theme["palette"], "dim": day_theme["palette"]["muted"]}
    assert night == {
        **night_theme["palette"],
        "dim": night_theme["palette"]["muted"],
    }
    assert luma(day["background"]) - luma(day["ink"]) >= 200
    assert luma(day["background"]) - luma(day["muted"]) >= 140
    assert luma(night["ink"]) - luma(night["background"]) >= 200
    assert luma(night["muted"]) - luma(night["background"]) >= 140
    assert EPAPER_RULE_WIDTH >= 2


def test_title_wordmark_is_darkened_for_epaper(tmp_path):
    plugin = make_plugin(tmp_path)
    original = Image.new("RGBA", (4, 4), (184, 148, 108, 160))

    boosted = plugin._epaper_wordmark_image(original)

    original_pixel = original.getpixel((1, 1))
    boosted_pixel = boosted.getpixel((1, 1))
    assert luma(boosted_pixel) <= luma(original_pixel) - 35
    assert boosted_pixel[3] > original_pixel[3]


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
