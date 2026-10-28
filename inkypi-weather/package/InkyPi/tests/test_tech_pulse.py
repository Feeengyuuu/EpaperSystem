import ast
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

try:
    import psutil  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault(
        "psutil",
        SimpleNamespace(
            virtual_memory=lambda: SimpleNamespace(total=2 * 1024**3),
            swap_memory=lambda: SimpleNamespace(percent=0.0),
        ),
    )
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.tech_pulse import tech_pulse as tech_pulse_module  # noqa: E402
from plugins.tech_pulse import trends  # noqa: E402
from plugins.ai_ecosystem_pulse.ai_ecosystem_pulse import AiEcosystemPulse  # noqa: E402
from plugins.tech_pulse.tech_pulse import (  # noqa: E402
    CACHE_SCHEMA_VERSION,
    HN_DOCS_URL,
    TITLE_WORDMARK_IMAGE,
    TOP_COMMENT_MAX_CHARS,
    TechPulse,
)
from plugins.base_plugin.presentation import PresentationMode  # noqa: E402
from plugins.base_plugin.render_provenance import read_source_provenance  # noqa: E402
from runtime.refresh_contracts import TaskCancelled  # noqa: E402


def test_background_layer_stays_flat_for_color_epaper():
    plugin = TechPulse({"id": "tech_pulse"})
    background = (12, 17, 28)
    image = Image.new("RGB", (96, 64), background)
    draw = ImageDraw.Draw(image)

    plugin._draw_background_grid(
        draw,
        image.width,
        image.height,
        {"grid": (31, 38, 51), "rule": (64, 70, 84)},
        1.0,
    )

    assert image.getcolors(maxcolors=image.width * image.height) == [
        (image.width * image.height, background)
    ]


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone_name="America/Los_Angeles", orientation="horizontal"):
        self.resolution = resolution
        self.timezone_name = timezone_name
        self.orientation = orientation

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "timezone": self.timezone_name,
            "orientation": self.orientation,
            "theme_mode": "night",
        }
        if key is None:
            return values
        return values.get(key, default)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, by_url):
        self.by_url = by_url
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        for needle, payload in self.by_url.items():
            if needle in url:
                return FakeResponse(payload)
        raise RuntimeError(f"Unexpected URL: {url}")


def _plugin(tmp_path):
    plugin = TechPulse({"id": "tech_pulse"})
    plugin._cache_dir = lambda create=True: tmp_path
    plugin._data_dir = lambda create=True: tmp_path
    return plugin


def canonical_theme(mode):
    palette = {
        "background": (238, 241, 245) if mode == "day" else (12, 17, 28),
        "panel": (255, 255, 255) if mode == "day" else (0, 0, 0),
        "ink": (10, 12, 15) if mode == "day" else (255, 255, 255),
        "muted": (74, 78, 84) if mode == "day" else (194, 196, 202),
        "rule": (185, 188, 194) if mode == "day" else (46, 48, 56),
        "accent": (69, 95, 149) if mode == "day" else (135, 159, 224),
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


def test_default_font_is_yahei_but_explicit_lxgw_is_preserved(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    sentinel = object()
    calls = []

    def fake_get_font(family, size, weight="normal"):
        calls.append((family, size, weight))
        return sentinel

    monkeypatch.setattr(tech_pulse_module, "get_font", fake_get_font)

    assert plugin._load_font(None, 18) is sentinel
    assert plugin._load_font("", 18) is sentinel
    assert plugin._load_font("LXGW WenKai", 18, "bold") is sentinel
    assert calls == [
        ("Microsoft YaHei", 18, "normal"),
        ("Microsoft YaHei", 18, "normal"),
        ("LXGW WenKai", 18, "bold"),
    ]


def _story(story_id, title, score=100, descendants=25, when=1782520200, url="https://example.com/story"):
    return {
        "id": story_id,
        "type": "story",
        "title": title,
        "url": url,
        "by": "alice",
        "time": when,
        "score": score,
        "descendants": descendants,
    }


def test_plugin_info_matches_class_and_id():
    info_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "tech_pulse" / "plugin-info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))

    assert info["id"] == "tech_pulse"
    assert info["class"] == "TechPulse"
    assert "Tech Pulse" in info["display_name"]


def test_settings_defaults_are_declared():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "tech_pulse" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")
    script = " ".join(html.split())
    missing = object()
    native_initial = tech_pulse_module.get_available_font_names(default=tech_pulse_module.DEFAULT_FONT)[0]

    def submitted_font(stored=missing):
        current = native_initial if stored is missing else stored
        has_stored = stored is not missing
        if "const hasStoredFont =" in script:
            assert "&& pluginSettings.fontFamily !== undefined;" in script
            assert "const yahei = [...fontFamily.options].find((option) => option.value === 'Microsoft YaHei');" in script
            assert "if (yahei && (!hasStoredFont || !fontFamily.value)) {" in script
            if not has_stored or not current:
                current = "Microsoft YaHei"
        else:
            assert "if (fontFamily && !fontFamily.value) {" in script
            if not current:
                current = "Microsoft YaHei"
        return current

    assert 'name="feed"' in html
    assert 'value="topstories" selected' in html
    assert 'name="maxStories"' in html
    assert 'value="5"' in html
    assert 'name="refreshMinutes"' in html
    assert 'value="30"' in html
    assert tech_pulse_module.DEFAULT_FONT == "Microsoft YaHei"
    assert native_initial != "Microsoft YaHei"
    assert "fontFamily.value = 'Microsoft YaHei';" in html
    assert "fontFamily.value = 'Jost';" not in html
    assert submitted_font("Jost") == "Jost"
    assert submitted_font("LXGW WenKai") == "LXGW WenKai"
    assert submitted_font("") == "Microsoft YaHei"
    assert submitted_font() == "Microsoft YaHei"


def test_parse_story_item_extracts_hn_fields(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime.fromtimestamp(1782523800, timezone.utc)

    parsed = plugin._parse_story_item(
        _story(42, "Show HN: A debugger &amp; trace viewer", score=321, descendants=77),
        now=now,
    )

    assert parsed["id"] == 42
    assert parsed["title"] == "Show HN: A debugger & trace viewer"
    assert parsed["domain"] == "example.com"
    assert parsed["by"] == "alice"
    assert parsed["score"] == 321
    assert parsed["comments"] == 77
    assert parsed["age_hours"] == 1
    assert parsed["hn_url"].endswith("id=42")


def test_parse_story_item_skips_deleted_dead_and_non_story(tmp_path):
    plugin = _plugin(tmp_path)

    assert plugin._parse_story_item({"deleted": True, "title": "Gone"}) is None
    assert plugin._parse_story_item({"dead": True, "title": "Dead"}) is None
    assert plugin._parse_story_item({"type": "comment", "title": "Comment"}) is None
    assert plugin._parse_story_item({"type": "story", "title": ""}) is None


def test_fetch_live_payload_uses_feed_order_filters_min_score_and_user_agent(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime.fromtimestamp(1782523800, timezone.utc)
    session = FakeSession(
        {
            "topstories.json": [1, 2, 3, 4],
            "/item/1.json": _story(1, "Too small", score=5),
            "/item/2.json": _story(2, "First kept", score=90),
            "/item/3.json": {"id": 3, "type": "comment", "title": "Skip"},
            "/item/4.json": _story(4, "Second kept", score=70, descendants=9, url="https://news.ycombinator.com/item?id=4"),
        }
    )
    monkeypatch.setattr("plugins.tech_pulse.tech_pulse.get_http_session", lambda: session)

    payload = plugin._fetch_live_payload("topstories", max_stories=2, min_score=50, now=now)

    assert [story["title"] for story in payload["stories"]] == ["First kept", "Second kept"]
    assert [story["rank"] for story in payload["stories"]] == [1, 2]
    assert payload["status"]["source_state"] == "live"
    assert HN_DOCS_URL in payload["status"]["source_urls"]
    assert any(call[1]["headers"]["User-Agent"].startswith("InkyPi TechPulse") for call in session.urls)


def test_cancelled_refresh_stops_instead_of_caching_a_partial_feed(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime.fromtimestamp(1782523800, timezone.utc)

    class CancellingSession(FakeSession):
        def get(self, url, **kwargs):
            if "/item/2.json" in url:
                raise TaskCancelled("task was canceled")
            return super().get(url, **kwargs)

    session = CancellingSession({
        "topstories.json": [1, 2, 3],
        "/item/1.json": _story(1, "Fetched before the cancel"),
        "/item/3.json": _story(3, "Never requested"),
    })
    monkeypatch.setattr("plugins.tech_pulse.tech_pulse.get_http_session", lambda: session)

    with pytest.raises(TaskCancelled):
        plugin._payload({"feed": "topstories", "maxStories": "5"}, now)

    assert not (tmp_path / "state.json").exists()
    assert not any("/item/3.json" in url for url, _kwargs in session.urls)


def test_story_preview_url_uses_title_target_and_hn_fallback(tmp_path):
    plugin = _plugin(tmp_path)
    story = {
        "url": "https://github.com/openai/codex/issues/123",
        "hn_url": "https://news.ycombinator.com/item?id=42",
    }
    hn_only = {"url": "", "hn_url": "https://news.ycombinator.com/item?id=43"}

    assert plugin._story_preview_url(story) == "https://github.com/openai/codex/issues/123"
    assert plugin._story_preview_url(hn_only) == "https://news.ycombinator.com/item?id=43"
    assert plugin._story_preview_url({"url": "/relative/path"}) == ""

def test_payload_reuses_fresh_cache_without_live_fetch(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    cache = plugin._build_payload(
        "topstories",
        [plugin._parse_story_item(_story(10, "Cached story"), now=now)],
        "live",
        now,
        [HN_DOCS_URL],
    )
    cache["cache_key"] = plugin._cache_key("topstories", 5, None)
    (tmp_path / "state.json").write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fetch should not run")))

    payload = plugin._payload({"feed": "topstories", "maxStories": "5"}, now)

    assert payload["status"]["source_state"] == "cache"
    assert payload["stories"][0]["title"] == "Cached story"


def test_tech_pulse_theme_only_uses_stale_source_cache_without_network(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    stale_now = now - timedelta(hours=3)
    story = plugin._parse_story_item(_story(10, "Cached theme story"), now=now)
    story["rank"] = 1
    cache = plugin._build_payload(
        "topstories",
        [story],
        "live",
        stale_now,
        [HN_DOCS_URL],
    )
    cache["cache_key"] = plugin._cache_key("topstories", 5, None)
    (tmp_path / "state.json").write_text(json.dumps(cache), encoding="utf-8")
    calls = {"fetch": 0, "session": 0}

    def forbidden_fetch(*_args, **_kwargs):
        calls["fetch"] += 1
        raise AssertionError("theme-only render must not fetch Hacker News")

    def forbidden_session():
        calls["session"] += 1
        raise AssertionError("theme-only render must not open a network session")

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(plugin, "_fetch_live_payload", forbidden_fetch)
    monkeypatch.setattr("plugins.tech_pulse.tech_pulse.get_http_session", forbidden_session)
    monkeypatch.setattr(plugin, "_write_context", lambda *_args: None)

    day_settings = {
        "feed": "topstories",
        "maxStories": "5",
        "themeMode": "paper",
        "_inkypi_theme": canonical_theme("day"),
        "_theme_render_only": True,
    }
    night_settings = {
        **day_settings,
        "_inkypi_theme": canonical_theme("night"),
    }
    day = plugin.generate_image(day_settings, FakeDeviceConfig())
    night = plugin.generate_image(night_settings, FakeDeviceConfig())

    expected_day = plugin._palette(day_settings)
    assert expected_day["background"] == (245, 240, 226)
    assert expected_day["panel"] == (255, 251, 241)
    assert expected_day["orange"] == (255, 102, 0)
    assert plugin._palette({**day_settings, "themeMode": "dark"}) == expected_day
    assert calls == {"fetch": 0, "session": 0}
    assert image_digest(day) != image_digest(night)


def test_payload_uses_stale_cache_when_live_fetch_fails(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    stale_now = now - timedelta(hours=3)
    cache = plugin._build_payload(
        "topstories",
        [plugin._parse_story_item(_story(11, "Stale cached story"), now=now)],
        "live",
        stale_now,
        [HN_DOCS_URL],
    )
    cache["cache_key"] = plugin._cache_key("topstories", 5, None)
    (tmp_path / "state.json").write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network down")))

    payload = plugin._payload({"feed": "topstories", "maxStories": "5"}, now)

    assert payload["status"]["source_state"] == "cache"
    assert payload["status"]["live_error"] == "network down"
    assert payload["stories"][0]["title"] == "Stale cached story"


def test_payload_falls_back_to_local_sample_without_cache(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))

    payload = plugin._payload({"feed": "beststories", "maxStories": "3", "minScore": ""}, now)

    assert payload["status"]["source_state"] == "local_sample"
    assert payload["feed"] == "beststories"
    assert len(payload["stories"]) == 3
    assert payload["status"]["live_error"] == "offline"


def test_render_page_returns_nonblank_800x480_and_draws_labels(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    stories = [
        plugin._parse_story_item(_story(1, "SQLite on the edge: keeping local-first apps boring", score=428, descendants=126, url="https://example.com/sqlite"), now=now),
        plugin._parse_story_item(_story(2, "Open source project trending on GitHub", score=311, descendants=88, url="https://github.com/example/current-hot-repo"), now=now),
        plugin._parse_story_item(_story(3, "A visual guide to transformer KV cache tradeoffs", score=205, descendants=42), now=now),
    ]
    for index, story in enumerate(stories, start=1):
        story["rank"] = index
    payload = plugin._build_payload("topstories", stories, "live", now, [HN_DOCS_URL])
    drawn = []
    story_list_ranks = []
    preview_urls = []
    original = plugin._draw_text
    original_story_list = plugin._draw_story_list
    original_preview = plugin._draw_hn_story_preview

    def capture(draw, xy, text, font, fill, **kwargs):
        drawn.append(str(text))
        return original(draw, xy, text, font, fill, **kwargs)

    def capture_story_list(draw, box, story_list, *args):
        story_list_ranks.extend(story.get("rank") for story in story_list)
        return original_story_list(draw, box, story_list, *args)

    def capture_preview(image_arg, draw_arg, box, story, *args):
        preview_urls.append(story.get("url"))
        return original_preview(image_arg, draw_arg, box, story, *args)

    monkeypatch.setattr(plugin, "_draw_text", capture)
    monkeypatch.setattr(plugin, "_draw_story_list", capture_story_list)
    monkeypatch.setattr(plugin, "_draw_hn_story_preview", capture_preview)

    image = plugin._render_page((800, 480), payload, {}, now, _ai_boards(), {})

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    assert image.getbbox() is not None
    assert len(image.getcolors(maxcolors=1_000_000)) > 8
    assert {"HACKER NEWS", "AGENT SKILLS", "HUGGING FACE", "GITHUB"} <= set(drawn)
    assert {"hyperframes", "acme/tiny-ocr", "morluto/rea"} <= set(drawn)
    assert "+6.5k/24H" in drawn and "NEW" in drawn
    assert story_list_ranks == [2, 3]
    assert preview_urls == ["https://example.com/sqlite"]


def test_tech_pulse_presentation_is_explicit_no_change(monkeypatch, tmp_path):
    plugin = _plugin(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("NO_CHANGE presentation must not render, fetch, or write")

    monkeypatch.setattr(plugin, "generate_image", forbidden)
    monkeypatch.setattr(plugin, "_payload", forbidden)
    monkeypatch.setattr(plugin, "_write_json", forbidden)
    monkeypatch.setattr(plugin, "_write_context", forbidden)

    assert "presentation_mode" in TechPulse.__dict__
    assert plugin.presentation_mode({}) is PresentationMode.NO_CHANGE


def test_tech_pulse_provenance_covers_live_fresh_stale_wrong_key_and_local(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    story = plugin._parse_story_item(_story(88, "Task 6 story"), now=now)
    story["rank"] = 1
    live_payload = plugin._build_payload("topstories", [story], "live", now, [HN_DOCS_URL])
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *_args: dict(live_payload))
    live = plugin._payload({"feed": "topstories", "maxStories": "5"}, now)
    fresh = plugin._payload({"feed": "topstories", "maxStories": "5"}, now)

    cached = plugin._read_json(tmp_path / "state.json", {})
    cached["cache_key"] = "wrong-key"
    cached["status"]["generated_at"] = (now - timedelta(hours=2)).isoformat()
    plugin._write_json(tmp_path / "state.json", cached)
    monkeypatch.setattr(
        plugin,
        "_fetch_live_payload",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    stale = plugin._payload({"feed": "topstories", "maxStories": "5"}, now)

    (tmp_path / "state.json").unlink()
    local = plugin._payload({"feed": "topstories", "maxStories": "5"}, now)

    assert live["_source_provenance"] == "live"
    assert fresh["_source_provenance"] == "fresh_cache"
    assert stale["_source_provenance"] == "stale_cache"
    assert local["_source_provenance"] == "local_fallback"


def test_tech_pulse_theme_provenance_is_read_only_and_context_has_health_label(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    story = plugin._parse_story_item(_story(89, "Cached theme story"), now=now)
    story["rank"] = 1
    cache = plugin._build_payload(
        "topstories",
        [story],
        "live",
        now - timedelta(hours=2),
        [HN_DOCS_URL],
    )
    cache["cache_key"] = plugin._cache_key("topstories", 5, None)
    plugin._write_json(tmp_path / "state.json", cache)
    before = (tmp_path / "state.json").read_bytes()
    captured = []
    monkeypatch.setattr(
        tech_pulse_module,
        "write_context",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    plugin._write_context({**cache, "_source_provenance": "stale_cache"}, now)
    assert captured[0][0][1]["source_provenance"] == "stale_cache"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("theme-only work must not fetch or write")

    monkeypatch.setattr(plugin, "_fetch_live_payload", forbidden)
    monkeypatch.setattr(plugin, "_write_context", forbidden)
    monkeypatch.setattr(plugin, "_write_json", forbidden)
    monkeypatch.setattr(
        plugin,
        "_render_page",
        lambda *_args: Image.new("RGB", (2, 1), "white"),
    )
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)

    image = plugin.generate_image(
        {
            "feed": "topstories",
            "maxStories": "5",
            "_theme_render_only": True,
            "_inkypi_theme": canonical_theme("day"),
        },
        FakeDeviceConfig(),
    )

    assert read_source_provenance(image).value == "stale_cache"
    assert (tmp_path / "state.json").read_bytes() == before


def test_tech_pulse_cold_theme_render_does_not_create_cache_tree(
    monkeypatch,
    tmp_path,
):
    cache_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    plugin = TechPulse({"id": "tech_pulse"})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cold theme-only render must not fetch or write")

    monkeypatch.setattr(plugin, "_fetch_live_payload", forbidden)
    monkeypatch.setattr(plugin, "_write_json", forbidden)
    monkeypatch.setattr(plugin, "_write_context", forbidden)

    with pytest.raises(RuntimeError, match="matching cached source data"):
        plugin.generate_image(
            {
                "_theme_render_only": True,
                "_inkypi_theme": canonical_theme("day"),
            },
            FakeDeviceConfig(),
        )

    assert not cache_root.exists()

    writer = TechPulse({"id": "tech_pulse"})
    state_path = writer._cache_dir() / "state.json"
    writer._write_json(state_path, {"ok": True})
    assert state_path.is_file()


def test_title_wordmark_asset_exists_and_draws(tmp_path):
    plugin = _plugin(tmp_path)
    asset_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "tech_pulse" / TITLE_WORDMARK_IMAGE
    image = Image.new("RGB", (320, 80), (10, 12, 15))

    assert asset_path.is_file()
    with Image.open(asset_path) as asset:
        assert asset.mode == "RGBA"
        assert asset.getchannel("A").getbbox() is not None

    assert plugin._draw_title_wordmark(image, 10, 12, (246, 46)) is True
    crop = image.crop((10, 12, 256, 58))
    assert len(crop.getcolors(maxcolors=1_000_000)) > 8


@pytest.mark.parametrize("mode", ["day", "night"])
def test_header_sources_are_linked_by_a_heartbeat_in_the_live_colour(tmp_path, mode):
    plugin = _plugin(tmp_path)
    palette = plugin._palette({"_inkypi_theme": canonical_theme(mode)})
    image = Image.new("RGB", (800, 60), palette["background"])
    left, right, middle = 360, 590, 30
    plugin._draw_header_sources(image, left, right, middle, palette)

    count = len(tech_pulse_module.HEADER_SOURCE_LOGOS)
    step = (right - left - tech_pulse_module.HEADER_LOGO_SIZE) / (count - 1)
    for index in range(count - 1):
        gap = (round(left + index * step + tech_pulse_module.HEADER_LOGO_SIZE + tech_pulse_module.HEADER_PULSE_GAP),
               middle - 12, round(left + (index + 1) * step - tech_pulse_module.HEADER_PULSE_GAP), middle + 12)
        crop = image.crop(gap)
        # The trace's core is the live colour (within the scale-down's rounding).
        near = [(x, y) for y in range(crop.height) for x in range(crop.width)
                if max(abs(a - b) for a, b in zip(crop.getpixel((x, y)), palette["up"])) <= 16]
        assert len(near) > 20, index
        # It spans the gap, and its spike stands well clear of the baseline.
        xs, ys = [x for x, _y in near], [y for _x, y in near]
        assert max(xs) - min(xs) >= crop.width - 6
        assert max(ys) - min(ys) >= tech_pulse_module.HEADER_PULSE_HEIGHT


def _comment(comment_id, text, by="bob", when=1782520200, **extra):
    return {"id": comment_id, "type": "comment", "by": by, "time": when, "text": text, **extra}


def test_fetch_live_payload_attaches_first_live_comment_to_lead_story(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime.fromtimestamp(1782523800, timezone.utc)
    session = FakeSession(
        {
            "topstories.json": [1, 2],
            "/item/1.json": {**_story(1, "Lead story"), "kids": [101, 102, 103, 104]},
            "/item/2.json": {**_story(2, "Second story"), "kids": [201]},
            "/item/101.json": _comment(101, "", deleted=True),
            "/item/102.json": _comment(102, "flagged", dead=True),
            "/item/103.json": _comment(
                103,
                'It&#x27;s fast.<p>See <a href="https:&#x2F;&#x2F;example.com" rel="nofollow">the docs</a> &gt; README.',
                by="carol",
            ),
            "/item/104.json": _comment(104, "Never fetched"),
        }
    )
    monkeypatch.setattr("plugins.tech_pulse.tech_pulse.get_http_session", lambda: session)

    payload = plugin._fetch_live_payload("topstories", max_stories=2, min_score=None, now=now)

    lead, second = payload["stories"]
    assert lead["top_comment"] == {
        "id": 103,
        "by": "carol",
        "text": "It's fast. See the docs > README.",
        "age_hours": 1,
    }
    assert "top_comment" not in second
    fetched = [url for url, _kwargs in session.urls]
    assert not any(url.endswith(("/item/104.json", "/item/201.json")) for url in fetched)


def test_fetch_live_payload_keeps_stories_when_top_comment_fails(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime.fromtimestamp(1782523800, timezone.utc)
    session = FakeSession(
        {
            "topstories.json": [1],
            "/item/1.json": {**_story(1, "Lead story"), "kids": [101]},
        }
    )
    monkeypatch.setattr("plugins.tech_pulse.tech_pulse.get_http_session", lambda: session)

    payload = plugin._fetch_live_payload("topstories", max_stories=1, min_score=None, now=now)

    assert [story["title"] for story in payload["stories"]] == ["Lead story"]
    assert "top_comment" not in payload["stories"][0]


def test_parse_comment_item_skips_unusable_comments_and_bounds_text(tmp_path):
    plugin = _plugin(tmp_path)

    assert plugin._parse_comment_item(_comment(1, "gone", deleted=True)) is None
    assert plugin._parse_comment_item(_comment(1, "dead", dead=True)) is None
    assert plugin._parse_comment_item(_story(1, "A story")) is None
    assert plugin._parse_comment_item(_comment(1, "<p> <i></i>")) is None
    assert len(plugin._parse_comment_item(_comment(1, "word " * 400))["text"]) <= TOP_COMMENT_MAX_CHARS


def _draw_preview(plugin, monkeypatch, story, box):
    image = Image.new("RGB", (800, 480), (10, 12, 15))
    palette = plugin._palette({"_inkypi_theme": canonical_theme("night")})
    drawn = []
    original = plugin._draw_text

    def capture(draw_arg, xy, text, font, fill):
        drawn.append((xy, str(text), font))
        return original(draw_arg, xy, text, font, fill)

    monkeypatch.setattr(plugin, "_draw_text", capture)
    label_font = plugin._load_font(None, 12, "bold")
    body_font = plugin._load_font(None, 12)
    plugin._draw_hn_story_preview(image, ImageDraw.Draw(image), box, story, palette, 1.0, label_font, body_font)
    return image, drawn


def test_hn_story_preview_draws_top_comment_inside_card(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    box = (42, 212, 350, 318)
    story = {
        "title": "Tesla Roadster",
        "url": "https://www.tesla.com/roadster",
        "comments": 2100,
        "top_comment": {
            "id": 7,
            "by": "jsnell",
            "text": " ".join(["Deposits from 2017 are still waiting for a delivery date."] * 6),
            "age_hours": 20,
        },
    }

    image, drawn = _draw_preview(plugin, monkeypatch, story, box)

    texts = [text for _xy, text, _font in drawn]
    assert texts[:2] == ["TOP COMMENT", "jsnell | 20h ago"]
    assert len(texts[2:]) >= 3
    assert texts[2].startswith("Deposits from 2017")
    assert texts[-1].endswith("...")
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for (x, y), text, font in drawn:
        assert x >= 0
        assert x + plugin._text_width(measure, text, font) <= box[2] - box[0]
        assert y + font.getbbox(text)[3] <= box[3] - box[1]
    assert len(image.crop(box).getcolors(maxcolors=1_000_000)) > 8


def test_hn_story_preview_without_comment_shows_discussion_link(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    story = {"title": "Fallback story", "url": "https://example.com/story", "comments": 2100}

    _image, drawn = _draw_preview(plugin, monkeypatch, story, (42, 212, 350, 318))

    assert [text for _xy, text, _font in drawn] == [
        "HN DISCUSSION",
        "2.1k comments",
        "https://example.com/story",
    ]


def test_tech_pulse_does_not_start_chromium():
    tree = ast.parse(Path(tech_pulse_module.__file__).read_text(encoding="utf-8"))

    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "utils.browser_renderer" not in modules
    assert not attributes & {"render_url", "render_html"}


def test_story_row_text_stays_inside_row_without_overlap(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (800, 480), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    palette = plugin._palette({})
    fonts = {name: plugin._load_font("Jost", size, weight) for name, (size, weight) in tech_pulse_module.FONT_SPECS.items()}
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    story = plugin._parse_story_item(
        _story(
            99,
            "A very long Hacker News headline that used to collide with borders and metadata in the compact row layout",
            score=1234,
            descendants=567,
        ),
        now=now,
    )
    story["rank"] = 5
    boxes = []
    original = plugin._draw_text

    def capture(local_draw, xy, text, font, fill, **kwargs):
        boxes.append((str(text), local_draw.textbbox(xy, str(text), font=font, anchor=kwargs.get("anchor"))))
        return original(local_draw, xy, text, font, fill, **kwargs)

    monkeypatch.setattr(plugin, "_draw_text", capture)
    row_box = (26, 270, 392, 316)

    plugin._story_row(draw, row_box, story, {}, palette, fonts, {}, now)

    assert {"5", "1.2k", "567"} <= {text for text, _bbox in boxes}
    for _, bbox in boxes:
        assert bbox[0] >= row_box[0]
        assert bbox[1] >= row_box[1]
        assert bbox[2] <= row_box[2]
        assert bbox[3] <= row_box[3]


def test_generate_image_uses_payload_and_returns_image(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    story = plugin._parse_story_item(_story(20, "Generated image story"), now=now)
    story["rank"] = 1
    payload = plugin._build_payload("topstories", [story], "local_sample", now, [HN_DOCS_URL])
    monkeypatch.setattr(plugin, "_now_for_device", lambda device: now)
    monkeypatch.setattr(plugin, "_payload", lambda settings, current: payload)
    monkeypatch.setattr(plugin, "_ai_payload", lambda settings, device, current: _ai_boards())
    monkeypatch.setattr(plugin, "_write_context", lambda *_args: None)

    image = plugin.generate_image({}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)


def _ai_boards(states=None):
    return {
        "skills": [
            {"rank": 1, "name": "hyperframes", "source": "heygen-com/hyperframes", "installs": 23700,
             "installs_display": "23.7K"},
            {"rank": 5, "name": "find-skills", "source": "vercel-labs/skills", "installs": 21100,
             "installs_display": "21.1K"},
        ],
        "models": [{"id": "acme/tiny-ocr", "pipeline_tag": "image-text-to-text", "trending_score": 2147,
                    "likes": 2509, "downloads_30d": 57715}],
        "repos": [
            {"id": 1, "full_name": "morluto/rea", "stars": 371228, "language": "TypeScript", "stars_24h": 6530},
            {"id": 2, "full_name": "stablyai/orca", "stars": 103426, "language": "TypeScript", "stars_24h": None},
        ],
        "status": {"aggregate": "LIVE",
                   "sources": states or {"skills": "live", "huggingface": "live", "github": "live"}},
    }


def test_trend_history_keeps_one_point_per_hour_and_measures_a_day():
    now = datetime(2026, 10, 23, 12, 30, tzinfo=timezone.utc)
    points = []
    for hours_ago, score in ((24 * 8, 5), (30, 100), (24, 200), (2, 900), (1.84, 950), (0, 1000), (-1, 9999)):
        points = trends.record(points, {"score": score}, (now - timedelta(hours=hours_ago)).isoformat(), now)

    # Past the week and in the future are dropped; two snapshots in one hour keep the later one.
    assert trends.series(points, "score", now) == [100, 200, 950, 1000]
    assert trends.change(points, "score", now) == (800, 24.0)
    assert trends.change(points[-1:], "score", now) is None
    board = trends.prune({"old": points[:1], "new": points, "gone": []}, {"new"}, now)
    assert set(board) == {"old", "new"}


def test_history_snapshots_each_fetch_once_and_theme_renders_only_read(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    story = plugin._parse_story_item(_story(7, "Tracked story", score=300), now=now)
    story["rank"] = 1
    payload = plugin._build_payload("topstories", [story], "live", now - timedelta(hours=2), [HN_DOCS_URL])
    ai = {**_ai_boards({"skills": "live", "huggingface": "fixture", "github": "live"}),
          "_fetched_at": {"skills": (now - timedelta(hours=3)).isoformat()}}

    state = plugin._history(payload, ai, now)
    again = plugin._history(payload, ai, now)

    assert again["hn"]["7"] == state["hn"]["7"] and len(state["hn"]["7"]) == 1
    assert state["hn"]["7"][0]["v"] == {"score": 300, "comments": 25}
    assert set(state["skills"]) == {"heygen-com/hyperframes/hyperframes", "vercel-labs/skills/find-skills"}
    # Demo rows stand in for a failed source and are never recorded as history.
    assert state["models"] == {}
    before = (tmp_path / "trends.json").read_bytes()
    plugin._write_json = lambda *_args: pytest.fail("a theme render must not write history")
    assert plugin._history(payload, ai, now, record=False)["hn"]["7"] == state["hn"]["7"]
    assert (tmp_path / "trends.json").read_bytes() == before


def test_story_velocity_prefers_snapshots_over_the_average_since_posting(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    story = {"id": 3, "score": 400, "age_hours": 8}
    history = {"3": [{"t": (now - timedelta(hours=2)).isoformat(), "v": {"score": 340}},
                     {"t": now.isoformat(), "v": {"score": 400}}]}

    assert plugin._rate_text(*plugin._velocity(story, {}, now)) == "~50/h"
    assert plugin._rate_text(*plugin._velocity(story, history, now)) == "+30/h"


def test_boards_hide_demo_rows_and_say_they_are_unavailable(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    demo = _ai_boards({"skills": "fixture", "huggingface": "live", "github": "stale_cache"})

    assert plugin._board_rows(demo, "skills", "skills") is None
    assert plugin._board_rows(demo, "github", "repos")[0]["full_name"] == "morluto/rea"
    assert plugin._board_rows(None, "huggingface", "models") is None
    drawn = []
    original = plugin._draw_text

    def capture(draw, xy, text, font, fill, **kwargs):
        drawn.append(str(text))
        return original(draw, xy, text, font, fill, **kwargs)

    monkeypatch.setattr(plugin, "_draw_text", capture)
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    payload = plugin._local_sample_payload("topstories", 5, None, now, "key")
    plugin._render_page((800, 480), payload, {}, now, None, {})
    assert drawn.count("暂时无法获取") == 3


def test_dark_source_marks_turn_light_on_the_night_page(tmp_path):
    plugin = _plugin(tmp_path)
    night, day = (12, 17, 28), (245, 240, 226)

    assert plugin._mark_luminance(plugin._logo("github", 16, night)) > 180
    assert plugin._mark_luminance(plugin._logo("github", 16, day)) < 60
    red, _green, blue = plugin._logo("hn", 16, night).convert("RGB").getpixel((2, 13))
    assert red > 200 and blue < 80


def test_wordmark_lettering_takes_the_ink_on_a_light_page(tmp_path):
    plugin = _plugin(tmp_path)
    source = plugin._load_title_wordmark()
    inked = plugin._ink_pale_lettering(source, (24, 27, 31))

    assert plugin._mark_luminance(source) > 150
    assert plugin._mark_luminance(inked) < 110

    def orange(image):
        return sum(1 for r, g, b, a in image.get_flattened_data() if a > 200 and r > 200 and 90 < g < 190 and b < 60)

    assert orange(inked) == orange(source) > 0


def test_ai_boards_carry_fetch_times_and_star_history(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    plugin = TechPulse({"id": "tech_pulse"})

    assert plugin._ai_payload({"_theme_render_only": True}, FakeDeviceConfig(), now) is None
    assert not (tmp_path / "cache").exists() and not (tmp_path / "data").exists()

    monkeypatch.setattr(AiEcosystemPulse, "_payload", lambda _self, _settings, _device, _now: _ai_boards())
    source = AiEcosystemPulse({"id": "ai_ecosystem_pulse"})
    source._write_json(source._cache_dir() / "skills.json", {"fetched_at": "2026-10-23T10:00:00+00:00"})
    source._write_json(source._data_dir() / "github-stars.json",
                       {"1": [{"captured_at": "2026-10-23T11:00:00+00:00", "stars": 370000}]})
    ai = plugin._ai_payload({}, FakeDeviceConfig(), now)

    assert ai["_fetched_at"] == {"skills": "2026-10-23T10:00:00+00:00", "huggingface": None, "github": None}
    assert ai["_star_history"]["morluto/rea"][0]["stars"] == 370000
    assert ai["_star_history"]["stablyai/orca"] == []

    def broken(*_args):
        raise RuntimeError("down")

    monkeypatch.setattr(AiEcosystemPulse, "_payload", broken)
    assert plugin._ai_payload({}, FakeDeviceConfig(), now) is None
