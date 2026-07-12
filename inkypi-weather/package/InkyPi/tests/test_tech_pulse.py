import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

sys.modules.setdefault(
    "psutil",
    SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=2 * 1024**3)),
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.tech_pulse import tech_pulse as tech_pulse_module  # noqa: E402
from plugins.tech_pulse.tech_pulse import (  # noqa: E402
    CACHE_SCHEMA_VERSION,
    HN_DOCS_URL,
    HN_HOME_URL,
    STORY_PREVIEW_CAPTURE_SIZE,
    STORY_PREVIEW_TIMEOUT_MS,
    TITLE_WORDMARK_IMAGE,
    TechPulse,
)
from security.ssrf import validate_browser_target  # noqa: E402


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
    plugin._cache_dir = lambda: tmp_path
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
    calls = {"fetch": 0, "preview": 0}

    def forbidden_fetch(*_args, **_kwargs):
        calls["fetch"] += 1
        raise AssertionError("theme-only render must not fetch Hacker News")

    def forbidden_preview(_url):
        calls["preview"] += 1
        return None

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(plugin, "_fetch_live_payload", forbidden_fetch)
    monkeypatch.setattr(plugin, "_capture_story_preview_page", forbidden_preview)
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

    expected_day = canonical_theme("day")["palette"]
    assert plugin._palette(day_settings) == {
        **expected_day,
        "row": expected_day["panel"],
        "metric": expected_day["panel"],
        "chip": expected_day["panel"],
        "grid": expected_day["rule"],
        "dim": expected_day["muted"],
        "orange": expected_day["accent"],
        "amber": expected_day["accent"],
        "cyan": expected_day["accent"],
    }
    assert plugin._palette({**day_settings, "themeMode": "dark"}) == plugin._palette(
        day_settings,
    )
    assert calls == {"fetch": 0, "preview": 0}
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

    def capture(draw, xy, text, font, fill):
        drawn.append(str(text))
        return original(draw, xy, text, font, fill)

    def capture_story_list(draw, box, story_list, *args):
        story_list_ranks.extend(story.get("rank") for story in story_list)
        return original_story_list(draw, box, story_list, *args)

    def capture_preview(image_arg, draw_arg, box, story, *args):
        preview_urls.append(story.get("url"))
        return original_preview(image_arg, draw_arg, box, story, *args)

    monkeypatch.setattr(plugin, "_draw_text", capture)
    monkeypatch.setattr(plugin, "_draw_story_list", capture_story_list)
    monkeypatch.setattr(plugin, "_draw_hn_story_preview", capture_preview)
    monkeypatch.setattr(plugin, "_story_preview_image", lambda story=None: None)

    image = plugin._render_page((800, 480), payload, {}, now)

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    assert image.getbbox() is not None
    assert len(image.getcolors(maxcolors=1_000_000)) > 8
    assert "Hacker News v0 current signal" in drawn
    assert "HN TOP 5" in drawn
    assert story_list_ranks == [2, 3]
    assert preview_urls == ["https://example.com/sqlite"]


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


def test_hn_story_preview_captures_story_target_page_and_caches(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    captured = []
    story_url = "https://github.com/HackerNews/API/issues/1"
    story = {"url": story_url}

    def fake_capture(url):
        captured.append(url)
        img = Image.new("RGB", (1100, 720), (246, 248, 250))
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, 1100, 72), fill=(36, 41, 47))
        draw.rectangle((120, 150, 980, 260), fill=(255, 255, 255), outline=(208, 215, 222))
        draw.text((145, 184), "Target story page", fill=(31, 35, 40))
        return img

    monkeypatch.setattr(plugin, "_capture_story_preview_page", fake_capture)

    first = plugin._story_preview_image(story)
    second = plugin._story_preview_image(story)

    assert captured == [story_url]
    assert first.size == second.size
    assert first.size[0] == 1100
    assert first.getpixel((0, 0)) == (36, 41, 47)
    assert plugin._story_preview_cache_path(story_url).is_file()


def test_story_preview_cache_uses_managed_namespace(tmp_path):
    from utils.cache_manager import CacheNamespace

    plugin = _plugin(tmp_path)

    namespace = plugin._story_preview_namespace()

    assert isinstance(namespace, CacheNamespace)
    assert namespace.root == tmp_path / "story_preview"
    assert namespace.budget.max_files == 256
    assert namespace.budget.max_bytes == 50 * 1024 * 1024


def test_hn_story_preview_falls_back_to_hn_homepage_when_target_fails(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    story_url = "https://example.com/dead"
    captured = []

    def fake_capture(url):
        captured.append(url)
        if url == story_url:
            return None
        img = Image.new("RGB", (1100, 720), (246, 248, 250))
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, 1100, 72), fill=(255, 102, 0))
        draw.text((145, 184), "Hacker News", fill=(31, 35, 40))
        return img

    monkeypatch.setattr(plugin, "_capture_story_preview_page", fake_capture)

    image = plugin._story_preview_image({"url": story_url})

    assert captured == [story_url, HN_HOME_URL]
    assert image is not None
    assert image.getpixel((0, 0)) == (255, 102, 0)
    assert plugin._story_preview_cache_path(HN_HOME_URL).is_file()


def test_story_preview_remote_capture_uses_fail_closed_compatibility_wrapper(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    calls = []

    def fake_take_screenshot(url, dimensions, **kwargs):
        calls.append((url, dimensions, kwargs))
        return None

    monkeypatch.setattr(
        "plugins.tech_pulse.tech_pulse.take_screenshot",
        fake_take_screenshot,
    )

    assert plugin._capture_story_preview_page_direct(HN_HOME_URL) is None
    assert calls == [
        (
            HN_HOME_URL,
            STORY_PREVIEW_CAPTURE_SIZE,
            {
                "timeout_ms": STORY_PREVIEW_TIMEOUT_MS,
                "validator": validate_browser_target,
            },
        )
    ]



def test_hn_story_preview_draws_story_target_screenshot(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (800, 480), (10, 12, 15))
    draw = ImageDraw.Draw(image)
    box = (70, 230, 350, 310)
    preview = Image.new("RGB", (1100, 520), (246, 248, 250))
    preview_draw = ImageDraw.Draw(preview)
    preview_draw.rectangle((0, 0, 1100, 42), fill=(36, 41, 47))
    preview_draw.rectangle((80, 95, 1020, 250), fill=(255, 255, 255), outline=(208, 215, 222))
    monkeypatch.setattr(plugin, "_story_preview_image", lambda story=None: preview)

    plugin._draw_hn_story_preview(image, draw, box, {"title": "Fallback story", "url": "https://example.com/story"}, plugin._palette({}), 1.0)

    crop = image.crop(box)
    assert len(crop.getcolors(maxcolors=1_000_000)) > 8


def test_hn_story_preview_fallback_draws_story_placeholder(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (800, 480), (10, 12, 15))
    draw = ImageDraw.Draw(image)
    box = (70, 230, 350, 305)
    monkeypatch.setattr(plugin, "_story_preview_image", lambda story=None: None)

    plugin._draw_hn_story_preview(image, draw, box, {"title": "Fallback story", "url": "https://example.com/story"}, plugin._palette({}), 1.0)

    crop = image.crop(box)
    assert len(crop.getcolors(maxcolors=1_000_000)) > 8


def test_story_row_text_stays_inside_row_without_overlap(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (800, 480), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    palette = plugin._palette({})
    row_font = plugin._load_font("Jost", 14, "bold")
    small_font = plugin._load_font("Jost", 12)
    label_font = plugin._load_font("Jost", 12, "bold")
    story = plugin._parse_story_item(
        _story(
            99,
            "A very long Hacker News headline that used to collide with borders and metadata in the compact row layout",
            score=1234,
            descendants=567,
        ),
        now=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
    )
    story["rank"] = 5
    boxes = []
    original = plugin._draw_text

    def capture(local_draw, xy, text, font, fill):
        bbox = local_draw.textbbox(xy, str(text), font=font)
        boxes.append((str(text), bbox))
        return original(local_draw, xy, text, font, fill)

    monkeypatch.setattr(plugin, "_draw_text", capture)
    row_box = (390, 180, 760, 227)

    plugin._story_row(draw, row_box, story, {}, palette, row_font, small_font, label_font, 1.0)

    for _, bbox in boxes:
        assert bbox[0] >= row_box[0]
        assert bbox[1] >= row_box[1]
        assert bbox[2] <= row_box[2]
        assert bbox[3] <= row_box[3]


def test_metric_card_labels_and_values_are_centered(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (240, 90), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    palette = plugin._palette({})
    label_font = plugin._load_font("Jost", 12, "bold")
    metric_font = plugin._load_font("Jost", 17, "bold")
    box = (20, 20, 180, 68)
    centers = {}
    original = plugin._draw_text

    def capture(local_draw, xy, text, font, fill):
        bbox = local_draw.textbbox(xy, str(text), font=font)
        centers[str(text)] = (bbox[0] + bbox[2]) / 2
        return original(local_draw, xy, text, font, fill)

    monkeypatch.setattr(plugin, "_draw_text", capture)

    plugin._metric_card(draw, box, "COMMENTS", "742", palette, label_font, metric_font, 1.0)

    expected_center = (box[0] + box[2]) / 2
    assert abs(centers["COMMENTS"] - expected_center) <= 1.5
    assert abs(centers["742"] - expected_center) <= 1.5


def test_generate_image_uses_payload_and_returns_image(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)
    story = plugin._parse_story_item(_story(20, "Generated image story"), now=now)
    story["rank"] = 1
    payload = plugin._build_payload("topstories", [story], "local_sample", now, [HN_DOCS_URL])
    monkeypatch.setattr(plugin, "_now_for_device", lambda device: now)
    monkeypatch.setattr(plugin, "_payload", lambda settings, current: payload)
    monkeypatch.setattr(plugin, "_write_context", lambda payload, current: None)

    image = plugin.generate_image({}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
