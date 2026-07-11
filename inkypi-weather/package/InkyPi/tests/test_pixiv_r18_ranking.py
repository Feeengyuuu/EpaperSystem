import json
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.pixiv_r18_ranking.pixiv_r18_ranking as pixiv_mod  # noqa: E402
from plugins.pixiv_r18_ranking.pixiv_r18_ranking import PixivR18Ranking  # noqa: E402


TEST_TMP_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "pixiv_r18_ranking_tests"


class DummyDeviceConfig:
    def __init__(self, resolution=(800, 480), cookie="session-cookie"):
        self.resolution = resolution
        self.cookie = cookie

    def get_resolution(self):
        return self.resolution

    def get_config(self, _key, default=None):
        return default

    def load_env_key(self, key):
        if key == "PIXIV_PHPSESSID":
            return self.cookie
        return ""


class RecordingLoader:
    def __init__(self):
        self.paths = []

    def from_file(self, path, dimensions, resize=True, focus_crop=False):
        self.paths.append(Path(path))
        with Image.open(path) as image:
            return image.copy().convert("RGB")


class FakeResponse:
    def __init__(self, json_data=None, raise_json=False):
        self._json = json_data
        self._raise_json = raise_json

    def raise_for_status(self):
        return None

    def json(self):
        if self._raise_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json


class FakeSession:
    """Records ranking.php requests and replays queued responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, cookies=None, timeout=None, **kwargs):
        self.calls.append({
            "url": url,
            "params": params or {},
            "headers": headers or {},
            "cookies": cookies,
        })
        return self.responses.pop(0)


def make_test_tmp_dir(name):
    path = TEST_TMP_ROOT / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_ranking_item(
    illust_id,
    *,
    title="Title",
    user_name="Artist",
    rank=1,
    tags=None,
    url=None,
    illust_type="0",
    sexual=2,
    lo=False,
    grotesque=False,
    is_masked=False,
    width=1200,
    height=1800,
):
    """Build a ranking.php ``contents[]`` entry (the public JSON shape).

    Defaults are portrait (1200x1800); pass width>height for a landscape item.
    """
    url = url or (
        "https://i.pximg.net/c/240x480/img-master/img/2026/06/16/00/00/00/"
        f"{illust_id}_p0_master1200.jpg"
    )
    return {
        "illust_id": illust_id,
        "title": title,
        "user_name": user_name,
        "user_id": 999,
        "rank": rank,
        "tags": tags if tags is not None else ["R-18"],
        "url": url,
        "illust_type": illust_type,
        "illust_page_count": "1",
        "width": width,
        "height": height,
        "is_masked": is_masked,
        "illust_content_type": {
            "sexual": sexual,
            "lo": lo,
            "grotesque": grotesque,
            "violent": False,
            "drug": False,
            "antisocial": False,
        },
    }


def test_fetch_ranking_uses_ranking_php_json_with_cookie_for_r18(monkeypatch):
    item = make_ranking_item(101)
    session = FakeSession([FakeResponse({"contents": [item]})])
    monkeypatch.setattr(pixiv_mod, "get_http_session", lambda: session)

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})

    assert plugin._fetch_ranking("day_r18", "sess-123") == [item]
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "https://www.pixiv.net/ranking.php"
    assert call["params"].get("mode") == "daily_r18"
    assert call["params"].get("content") == "illust"
    assert call["params"].get("format") == "json"
    assert call["cookies"] == {"PHPSESSID": "sess-123"}


def test_fetch_ranking_without_cookie_falls_back_to_sfw_mode(monkeypatch):
    item = make_ranking_item(7, sexual=0)
    session = FakeSession([FakeResponse({"contents": [item]})])
    monkeypatch.setattr(pixiv_mod, "get_http_session", lambda: session)

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})

    assert plugin._fetch_ranking("day_r18", "") == [item]
    call = session.calls[0]
    assert call["params"].get("mode") == "daily"  # SFW fallback, not daily_r18
    assert not call["cookies"]  # no cookie sent when none configured


def test_fetch_ranking_falls_back_to_sfw_when_r18_returns_non_json(monkeypatch):
    # An expired/invalid cookie makes pixiv serve the HTML landing page instead of JSON.
    sfw_item = make_ranking_item(9, sexual=0)
    session = FakeSession([
        FakeResponse(raise_json=True),          # daily_r18 with cookie -> HTML, not JSON
        FakeResponse({"contents": [sfw_item]}),  # daily fallback -> JSON
    ])
    monkeypatch.setattr(pixiv_mod, "get_http_session", lambda: session)

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})

    assert plugin._fetch_ranking("day_r18", "expired") == [sfw_item]
    assert [c["params"].get("mode") for c in session.calls] == ["daily_r18", "daily"]


def test_safety_filter_uses_content_type_flags_and_tags():
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})

    assert plugin._is_safe_ranking_item(make_ranking_item(1, tags=["R-18"])) is True
    assert plugin._is_safe_ranking_item(make_ranking_item(2, lo=True)) is False
    assert plugin._is_safe_ranking_item(make_ranking_item(3, grotesque=True)) is False
    assert plugin._is_safe_ranking_item(make_ranking_item(4, tags=["R-18G"])) is False
    assert plugin._is_safe_ranking_item(make_ranking_item(5, tags=["ロリ"])) is False
    assert plugin._is_safe_ranking_item(make_ranking_item(6, illust_type="2")) is False  # ugoira
    assert plugin._is_safe_ranking_item(make_ranking_item(7, is_masked=True)) is False


def test_daily_pool_cache_hit_does_not_call_pixiv(monkeypatch):
    cache_dir = make_test_tmp_dir("cache-hit")
    image_path = cache_dir / "safe.jpg"
    Image.new("RGB", (300, 200), "blue").save(image_path)

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    plugin.image_loader = RecordingLoader()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(plugin, "_fetch_ranking", lambda *_args: (_ for _ in ()).throw(AssertionError("Pixiv called")))

    settings = {"rankingMode": "day_r18", "poolSize": "20"}
    state = {
        "state_version": "pixiv-r18-ranking-v1",
        "day_key": "2026-06-17",
        "ranking_mode": "day_r18",
        "pool_size": 20,
    }
    plugin._write_state(state)
    plugin._write_daily_pool([
        {
            "illust_id": "101",
            "rank": 1,
            "title": "Cached",
            "artist": "Artist",
            "tags": ["R-18"],
            "image_path": str(image_path),
        }
    ])

    image = plugin.generate_image(settings, DummyDeviceConfig())

    assert image.size == (800, 480)
    assert plugin.image_loader.paths == [image_path]


def test_daily_pool_refresh_filters_and_limits_to_top_twenty(monkeypatch):
    cache_dir = make_test_tmp_dir("refresh")

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    plugin.image_loader = RecordingLoader()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))

    def fake_download(_url):
        # Each download must yield its own temp file: the downloader unlinks it afterwards.
        source = cache_dir / f"source-{uuid.uuid4().hex}.jpg"
        Image.new("RGB", (300, 500), "purple").save(source)
        return source

    monkeypatch.setattr(plugin, "_download_to_temp", fake_download)

    ranking = [
        make_ranking_item(1, tags=["R-18G"]),
        *[make_ranking_item(index, title=f"Safe {index}") for index in range(2, 30)],
    ]

    def fake_page(mode, cookie, page=1):
        return ranking if page == 1 else []

    monkeypatch.setattr(plugin, "_fetch_ranking_page", fake_page)

    pool = plugin._refresh_daily_pool({"rankingMode": "day_r18", "poolSize": "20"}, DummyDeviceConfig(), (800, 480))

    assert len(pool) == 20
    assert pool[0]["illust_id"] == "2"
    assert pool[-1]["illust_id"] == "21"
    assert all(Path(item["image_path"]).is_file() for item in pool)


def test_daily_pool_paginates_ranking_until_filled(monkeypatch):
    cache_dir = make_test_tmp_dir("paginate")

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    plugin.image_loader = RecordingLoader()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))

    def fake_download(_url):
        source = cache_dir / f"source-{uuid.uuid4().hex}.jpg"
        Image.new("RGB", (300, 500), "purple").save(source)
        return source

    monkeypatch.setattr(plugin, "_download_to_temp", fake_download)

    # Page 1: only 5 usable (45 filtered out as R-18G). Page 2: 20 usable.
    page1 = [make_ranking_item(i, tags=["R-18G"]) for i in range(1, 46)] + [
        make_ranking_item(i) for i in range(46, 51)
    ]
    page2 = [make_ranking_item(i) for i in range(51, 71)]
    pages = {1: page1, 2: page2}

    def fake_page(mode, cookie, page=1):
        return pages.get(page, [])

    monkeypatch.setattr(plugin, "_fetch_ranking_page", fake_page)

    pool = plugin._refresh_daily_pool({"rankingMode": "day_r18", "poolSize": "20"}, DummyDeviceConfig(), (800, 480))

    assert len(pool) == 20  # 5 from page 1 + 15 from page 2
    ids = [item["illust_id"] for item in pool]
    assert ids[:5] == ["46", "47", "48", "49", "50"]
    assert ids[5] == "51"


def test_daily_pool_uses_available_when_ranking_exhausted(monkeypatch):
    cache_dir = make_test_tmp_dir("exhausted")

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    plugin.image_loader = RecordingLoader()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))

    def fake_download(_url):
        source = cache_dir / f"source-{uuid.uuid4().hex}.jpg"
        Image.new("RGB", (300, 500), "purple").save(source)
        return source

    monkeypatch.setattr(plugin, "_download_to_temp", fake_download)

    def fake_page(mode, cookie, page=1):
        return [make_ranking_item(1), make_ranking_item(2)] if page == 1 else []

    monkeypatch.setattr(plugin, "_fetch_ranking_page", fake_page)

    pool = plugin._refresh_daily_pool({"rankingMode": "day_r18", "poolSize": "20"}, DummyDeviceConfig(), (800, 480))

    assert len(pool) == 2  # ranking ran out; uses what is available, no crash


def test_display_dimensions_is_self_contained_and_swaps_for_vertical():
    # Must not depend on BasePlugin.get_dimensions(): older deployed base_plugin
    # versions lack it, which would raise AttributeError on the device.
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})

    class HorizCfg:
        def get_resolution(self):
            return (800, 480)

        def get_config(self, _key, default=None):
            return default

    class VertCfg:
        def get_resolution(self):
            return (800, 480)

        def get_config(self, key, default=None):
            return "vertical" if key == "orientation" else default

    assert plugin._display_dimensions(HorizCfg()) == (800, 480)
    assert plugin._display_dimensions(VertCfg()) == (480, 800)


def _portrait(illust_id):
    return {"illust_id": illust_id, "rank": int(illust_id) if str(illust_id).isdigit() else 1,
            "width": 800, "height": 1200}


def _landscape(illust_id):
    return {"illust_id": illust_id, "rank": int(illust_id) if str(illust_id).isdigit() else 1,
            "width": 1200, "height": 800}


def test_is_portrait_item_uses_metadata_then_file(monkeypatch, tmp_path):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    assert plugin._is_portrait_item({"width": 800, "height": 1200}) is True
    assert plugin._is_portrait_item({"width": 1200, "height": 800}) is False

    # No dimensions in metadata -> fall back to reading the cached image.
    portrait_file = tmp_path / "p.jpg"
    Image.new("RGB", (300, 500), "purple").save(portrait_file)
    assert plugin._is_portrait_item({"image_path": str(portrait_file)}) is True


def test_select_display_group_groups_next_three_portraits(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(make_test_tmp_dir("group-3")))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)

    pool = [_portrait("p1"), _portrait("p2"), _portrait("p3"), _portrait("p4"), _portrait("p5")]
    group = plugin._select_display_group(pool, {"fitMode": "auto_layout"})

    assert [item["illust_id"] for item in group] == ["p1", "p2", "p3"]
    # The other two portraits remain queued for the next refresh.
    assert plugin._read_state()["queue"] == ["p4", "p5"]


def test_select_display_group_landscape_head_is_single(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(make_test_tmp_dir("group-land")))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)

    pool = [_landscape("L1"), _portrait("p2"), _portrait("p3")]
    group = plugin._select_display_group(pool, {"fitMode": "auto_layout"})

    assert [item["illust_id"] for item in group] == ["L1"]  # landscape shows alone
    assert plugin._read_state()["queue"] == ["p2", "p3"]


def test_select_display_group_handles_leftover_portraits(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(make_test_tmp_dir("group-leftover")))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)

    pool = [_portrait("p1"), _portrait("p2")]  # only two portraits available
    group = plugin._select_display_group(pool, {"fitMode": "auto_layout"})

    assert [item["illust_id"] for item in group] == ["p1", "p2"]


def test_select_display_group_non_auto_layout_is_always_single(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(make_test_tmp_dir("group-cover")))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)

    pool = [_portrait("p1"), _portrait("p2"), _portrait("p3")]
    group = plugin._select_display_group(pool, {"fitMode": "cover"})

    assert [item["illust_id"] for item in group] == ["p1"]  # no triptych outside auto_layout


def test_compose_strip_fills_three_cells_across_width():
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    images = [
        Image.new("RGB", (400, 800), (255, 0, 0)),
        Image.new("RGB", (400, 800), (0, 255, 0)),
        Image.new("RGB", (400, 800), (0, 0, 255)),
    ]

    canvas = plugin._compose_strip(images, (800, 480), {"backgroundColor": "black"})

    assert canvas.size == (800, 480)
    assert canvas.getpixel((130, 240)) == (255, 0, 0)  # left cell red
    assert canvas.getpixel((400, 240)) == (0, 255, 0)  # middle cell green
    assert canvas.getpixel((670, 240)) == (0, 0, 255)  # right cell blue


def test_generate_image_renders_triptych_for_portrait_pool(monkeypatch):
    cache_dir = make_test_tmp_dir("triptych")
    paths = []
    for idx, color in enumerate(["red", "green", "blue", "purple"], start=1):
        p = cache_dir / f"p{idx}.jpg"
        Image.new("RGB", (300, 500), color).save(p)  # portrait
        paths.append(p)

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    plugin.image_loader = RecordingLoader()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)
    monkeypatch.setattr(plugin, "_fetch_ranking_page", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch")))

    state = {
        "state_version": "pixiv-r18-ranking-v1",
        "day_key": "2026-06-17",
        "ranking_mode": "day_r18",
        "pool_size": 20,
    }
    plugin._write_state(state)
    plugin._write_daily_pool([
        {"illust_id": f"p{idx}", "rank": idx, "width": 800, "height": 1200, "image_path": str(p)}
        for idx, p in enumerate(paths, start=1)
    ])

    image = plugin.generate_image({"rankingMode": "day_r18", "poolSize": "20", "fitMode": "auto_layout"}, DummyDeviceConfig())

    assert image.size == (800, 480)
    # Three portraits composed -> three cached images loaded for this render.
    assert len(plugin.image_loader.paths) == 3


def test_random_queue_avoids_repeats_until_pool_is_consumed(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(make_test_tmp_dir("queue")))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)
    pool = [
        {"illust_id": "a", "rank": 1},
        {"illust_id": "b", "rank": 2},
        {"illust_id": "c", "rank": 3},
    ]

    selected = [plugin._select_daily_item(pool)["illust_id"] for _ in range(3)]
    next_round_first = plugin._select_daily_item(pool)["illust_id"]

    assert selected == ["a", "b", "c"]
    assert next_round_first == "a"


def test_random_queue_does_not_start_new_round_with_last_item(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(make_test_tmp_dir("queue-last")))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)
    plugin._write_state({"last_illust_id": "a"})
    pool = [
        {"illust_id": "a", "rank": 1},
        {"illust_id": "b", "rank": 2},
    ]

    assert plugin._select_daily_item(pool)["illust_id"] == "b"


def test_fit_image_handles_landscape_and_portrait_sources():
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    landscape = Image.new("RGB", (500, 250), "red")
    portrait = Image.new("RGB", (240, 500), "green")

    fitted_landscape = plugin._fit_image(
        landscape,
        (800, 480),
        {"fitMode": "auto_blur", "backgroundColor": "black"},
    )
    fitted_portrait = plugin._fit_image(
        portrait,
        (800, 480),
        {"fitMode": "auto_blur", "backgroundColor": "black"},
    )

    assert fitted_landscape.size == (800, 480)
    assert fitted_portrait.size == (800, 480)
    assert fitted_landscape.getpixel((400, 240))[0] > 200
    assert fitted_portrait.getpixel((400, 240))[1] > 100
    assert fitted_landscape.getpixel((0, 240))[0] > 200
    assert fitted_portrait.getpixel((0, 240))[1] > 100


def test_contain_mode_still_preserves_full_image_with_letterbox():
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    square = Image.new("RGB", (640, 640), "blue")

    fitted = plugin._fit_image(
        square,
        (800, 480),
        {"fitMode": "contain", "backgroundColor": "black"},
    )

    assert fitted.size == (800, 480)
    assert fitted.getpixel((400, 240))[2] > 200
    assert fitted.getpixel((0, 240)) == (0, 0, 0)


def test_missing_filtered_pool_renders_safe_placeholder(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(make_test_tmp_dir("empty")))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    calls = []

    def fake_page(mode, cookie, page=1):
        calls.append((mode, cookie, page))
        return [make_ranking_item(1, tags=["R-18G"])] if page == 1 else []

    monkeypatch.setattr(plugin, "_fetch_ranking_page", fake_page)

    image = plugin.generate_image({"rankingMode": "day_r18", "poolSize": "20"}, DummyDeviceConfig())
    second_image = plugin.generate_image({"rankingMode": "day_r18", "poolSize": "20"}, DummyDeviceConfig())
    pool_payload = json.loads(plugin._daily_pool_path().read_text(encoding="utf-8"))

    assert image.size == (800, 480)
    assert second_image.size == (800, 480)
    assert pool_payload["items"] == []
    # R-18 fetched with the cookie (page returned JSON, so no SFW fallback).
    assert calls[0] == ("daily_r18", "session-cookie", 1)


def test_pixiv_font_falls_back_to_noto_when_shared_font_lacks_japanese(
    monkeypatch, tmp_path
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    shared_font = object()
    japanese_font = object()
    noto_path = tmp_path / "NotoSansCJKjp-Regular.otf"
    noto_path.write_bytes(b"test font placeholder")
    calls = []
    checked_text = []
    monkeypatch.setattr(
        pixiv_mod,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or shared_font,
        raising=False,
    )
    monkeypatch.setattr(
        pixiv_mod,
        "JAPANESE_FONT_PATHS",
        (str(noto_path),),
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "_font_supports_text",
        lambda font, text: checked_text.append(text) or font is japanese_font,
        raising=False,
    )
    monkeypatch.setattr(
        pixiv_mod.ImageFont,
        "truetype",
        lambda path, size: japanese_font,
    )

    assert plugin._font(18, bold=True) is japanese_font
    assert calls == [(18, True)]
    assert checked_text
    assert "\u3042" in checked_text[0]


def test_pixiv_japanese_fallback_includes_tracked_noto_font():
    assert any(
        Path(path).name == "NotoSansSC-VF.ttf"
        for path in pixiv_mod.JAPANESE_FONT_PATHS
    )
