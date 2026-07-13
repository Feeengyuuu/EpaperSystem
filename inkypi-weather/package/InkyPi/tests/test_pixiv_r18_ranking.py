import json
import hashlib
import importlib
from copy import deepcopy
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.pixiv_r18_ranking.pixiv_r18_ranking as pixiv_mod  # noqa: E402
from plugins.pixiv_r18_ranking.pixiv_r18_ranking import PixivR18Ranking  # noqa: E402
from plugins.base_plugin.presentation import (  # noqa: E402
    PresentationMode,
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)
from runtime.runtime_state import PresentationCommitReceipt  # noqa: E402


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
        self.headers = {}
        self.closed = False
        self.body_read = False
        self._payload = (
            b"<html>not json</html>"
            if raise_json
            else json.dumps(json_data).encode("utf-8")
        )

    def raise_for_status(self):
        return None

    def json(self):
        if self._raise_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json

    def iter_content(self, chunk_size):
        self.body_read = True
        for offset in range(0, len(self._payload), chunk_size):
            yield self._payload[offset:offset + chunk_size]

    def close(self):
        self.closed = True


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


def install_fake_ranking_transport(monkeypatch, plugin, session):
    def request(url, *, headers, **_kwargs):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        cookie = headers.get("Cookie")
        session.calls.append({
            "url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
            "params": {key: values[-1] for key, values in query.items()},
            "headers": headers,
            "cookies": {"PHPSESSID": cookie.split("=", 1)[1]} if cookie else None,
        })
        return session.responses.pop(0)

    monkeypatch.setattr(plugin, "_request_ranking_target", request)


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
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    install_fake_ranking_transport(monkeypatch, plugin, session)

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
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    install_fake_ranking_transport(monkeypatch, plugin, session)

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
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    install_fake_ranking_transport(monkeypatch, plugin, session)

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

    assert image.size == (800, 480)
    assert second_image.size == (800, 480)
    assert not plugin._daily_pool_path().exists()
    assert not plugin._state_path().exists()
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


# Presentation-bank contract -------------------------------------------------


def bound_settings(instance_uuid="pixiv-instance", **overrides):
    return bind_presentation_instance_identity(
        {
            "rankingMode": "day_r18",
            "poolSize": "20",
            "fitMode": "auto_layout",
            "dailyPoolMode": "true",
            **overrides,
        },
        instance_uuid,
    )


def presentation_request(request_id, *, origin="origin-display"):
    return PresentationRequestContext(
        request_id=request_id,
        requested_at="2026-07-12T10:00:00+00:00",
        origin_display_commit_id=origin,
        last_receipt=None,
    )


def presentation_receipt(
    request_id,
    *,
    display="prepared-display",
    committed_at="2026-07-12T10:01:00+00:00",
):
    return PresentationCommitReceipt(
        request_id=request_id,
        committed_at=committed_at,
        display_commit_id=display,
        structural_generation=1,
        settings_revision=1,
        theme_mode="day",
    )


def presentation_theme(mode):
    return {
        "mode": mode,
        "palette": {
            "background": (27, 11, 19) if mode == "night" else (255, 240, 245),
            "accent": (255, 136, 185) if mode == "night" else (223, 79, 143),
        },
    }


def bank_candidate(index, *, content_rating="r18", effective_mode="daily_r18"):
    return {
        "illust_id": str(index),
        "rank": index,
        "title": f"Title {index}",
        "artist": f"Artist {index}",
        "tags": ["R-18"] if content_rating == "r18" else [],
        "width": 800,
        "height": 1200,
        "page_url": f"https://www.pixiv.net/artworks/{index}",
        "image_url": (
            "https://i.pximg.net/img-master/img/2026/07/12/00/00/00/"
            f"{index}_p0_master1200.jpg"
        ),
        "requested_mode": "day_r18",
        "effective_mode": effective_mode,
        "content_rating": content_rating,
        "authenticated": content_rating == "r18",
        "source_status": "fresh",
    }


def make_presentation_bank(tmp_path, *, instance_uuid="pixiv-instance", date_key="2026-07-12"):
    from plugins.pixiv_r18_ranking.presentation_bank import (
        PixivPresentationBank,
        instance_profile_fingerprint,
        settings_fingerprint,
        settings_key,
    )

    settings = bound_settings(instance_uuid=instance_uuid)
    base = settings_fingerprint(
        settings,
        (800, 480),
        date_key,
        effective_mode="daily_r18",
        content_rating="r18",
    )
    fingerprint = instance_profile_fingerprint(base, instance_uuid)
    return PixivPresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint=fingerprint,
        base_fingerprint=base,
        profile_settings_key=settings_key(settings),
        instance_uuid=instance_uuid,
        date_key=date_key,
    )


def warm_bank(tmp_path, *, count=24, instance_uuid="pixiv-instance", date_key="2026-07-12"):
    bank = make_presentation_bank(
        tmp_path,
        instance_uuid=instance_uuid,
        date_key=date_key,
    )
    document, profile = bank.load_for_data()
    for index in range(1, count + 1):
        bank.ingest(
            profile,
            bank_candidate(index),
            Image.new("RGB", (240, 420), (index % 255, 40, 90)),
            downloaded_at="2026-07-12T08:00:00+00:00",
        )
    ready = bank.ready_records(profile, prune=True)
    current = bank.ensure_current(document, profile, ready, "auto_layout")
    bank.save(document)
    return bank, document, profile, current


def profile_for_instance(document, instance_uuid="pixiv-instance"):
    fingerprint = document["instance_profiles"][instance_uuid]
    return document["profiles"][fingerprint]


def test_pixiv_manifest_declares_prepared_presentation_capability():
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "pixiv_r18_ranking"
        / "plugin-info.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["capabilities"]["supports_presentation_refresh"] is True
    assert PixivR18Ranking({"id": "pixiv_r18_ranking"}).presentation_mode({}) is PresentationMode.PREPARED_BANK


def test_pixiv_fingerprint_canonicalizes_defaults_and_tracks_pixel_semantics():
    from plugins.pixiv_r18_ranking.presentation_bank import settings_fingerprint

    omitted = {}
    explicit = {
        "rankingMode": "day_r18",
        "poolSize": 20,
        "fitMode": "auto_layout",
        "backgroundColor": "black",
        "showInfoOverlay": "false",
        "dailyPoolMode": "true",
    }
    first = settings_fingerprint(
        omitted,
        (800, 480),
        "2026-07-12",
        effective_mode="daily_r18",
        content_rating="r18",
    )
    second = settings_fingerprint(
        explicit,
        (800, 480),
        "2026-07-12",
        effective_mode="daily_r18",
        content_rating="r18",
    )

    assert first == second
    assert first != settings_fingerprint(
        {**explicit, "fitMode": "contain"},
        (800, 480),
        "2026-07-12",
        effective_mode="daily_r18",
        content_rating="r18",
    )
    assert first != settings_fingerprint(
        explicit,
        (800, 480),
        "2026-07-13",
        effective_mode="daily_r18",
        content_rating="r18",
    )
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    source = Image.new("RGB", (240, 420), "purple")
    assert plugin._fit_image(source, (800, 480), omitted).tobytes() == plugin._fit_image(
        source,
        (800, 480),
        explicit,
    ).tobytes()


def test_missing_cookie_records_sfw_provenance_not_healthy_r18(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    item = make_ranking_item(7, sexual=0)
    monkeypatch.setattr(plugin, "_fetch_ranking_page", lambda mode, cookie, page=1: [item])

    resolution = plugin._resolve_ranking_with_provenance("day_r18", "")

    assert resolution["requested_mode"] == "day_r18"
    assert resolution["effective_mode"] == "daily"
    assert resolution["content_rating"] == "sfw"
    assert resolution["authenticated"] is False
    assert resolution["healthy_r18"] is False


def test_bank_limits_and_defaults_match_frozen_contract():
    from plugins.pixiv_r18_ranking import presentation_bank

    assert presentation_bank.READY_TARGET == 24
    assert presentation_bank.REFILL_THRESHOLD == 8
    assert presentation_bank.MAX_RECORDS_PER_PROFILE == 50
    assert presentation_bank.MEDIA_MAX_AGE_SECONDS == 48 * 60 * 60
    assert presentation_bank.MEDIA_MAX_FILES == 64
    assert presentation_bank.MEDIA_MAX_BYTES == 128 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_OBJECT_BYTES == 12 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_DIMENSION == 8192
    assert presentation_bank.MEDIA_MAX_PIXELS == 32_000_000
    assert presentation_bank.MAX_STATE_BYTES == 4 * 1024 * 1024
    assert presentation_bank.MAX_PROFILES == 64
    assert presentation_bank.MAX_DATE_BUCKETS == 366
    assert presentation_bank.MAX_SEEN_ILLUSTS == 5000


def test_data_bank_hydration_preserves_current_pending_and_seen_history(tmp_path):
    bank, document, profile, current = warm_bank(tmp_path)
    ready = bank.ready_records(profile, prune=True)
    pending = bank.choose_selection(document, profile, ready, "auto_layout")
    pending["request_id"] = "a" * 32
    profile["pending_selection"] = pending
    profile["date_buckets"]["2026-07-12"] = {
        "seen_illust_ids": ["already-seen"],
        "committed_at": "2026-07-12T07:00:00+00:00",
    }
    bank.save(document)
    before = json.loads(bank.state_path.read_text(encoding="utf-8"))

    reloaded, reloaded_profile = bank.load_for_data()
    bank.ready_records(reloaded_profile, prune=True)
    bank.save(reloaded)
    after = json.loads(bank.state_path.read_text(encoding="utf-8"))

    before_profile = profile_for_instance(before)
    after_profile = profile_for_instance(after)
    assert after_profile["current_selection"] == before_profile["current_selection"] == current
    assert after_profile["pending_selection"] == before_profile["pending_selection"]
    assert after_profile["date_buckets"] == before_profile["date_buckets"]


def test_warm_prepare_is_zero_provider_and_pending_is_not_seen(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))
    warm_bank(tmp_path)
    monkeypatch.setattr(plugin, "_fetch_ranking_page", lambda *_args, **_kwargs: pytest.fail("prepare used provider"))
    monkeypatch.setattr(plugin, "_download_to_temp", lambda *_args, **_kwargs: pytest.fail("prepare downloaded media"))
    monkeypatch.setattr(
        plugin,
        "_request_ranking_target",
        lambda *_args, **_kwargs: pytest.fail("prepare opened HTTP"),
    )

    prepared = plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=presentation_request("b" * 32),
        resolved_theme_context=presentation_theme("night"),
    )
    state = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    profile = profile_for_instance(state)
    pending = profile["pending_selection"]
    seen = profile.get("date_buckets", {}).get("2026-07-12", {}).get("seen_illust_ids", [])

    assert prepared.changed is True
    assert prepared.image.size == (800, 480)
    assert prepared.image.info["inkypi_theme_mode"] == "night"
    assert pending["request_id"] == "b" * 32
    pending_ids = {
        record["illust_id"]
        for record in profile["records"]
        if record["record_key"] in pending["record_keys"]
    }
    assert not pending_ids.intersection(seen)


def test_receipt_commits_exact_group_once_and_foreign_canceled_late_are_noops(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))
    warm_bank(tmp_path)
    plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=presentation_request("c" * 32),
        resolved_theme_context=None,
    )
    state_path = tmp_path / "presentation-state.json"
    pending_state = json.loads(state_path.read_text(encoding="utf-8"))
    pending = profile_for_instance(pending_state)["pending_selection"]
    pending_ids = [
        record["illust_id"]
        for key in pending["record_keys"]
        for record in profile_for_instance(pending_state)["records"]
        if record["record_key"] == key
    ]
    baseline = state_path.read_bytes()

    plugin.reconcile_presentation_receipt(
        bound_settings(instance_uuid="wrong-instance"),
        presentation_receipt("c" * 32),
    )
    plugin.reconcile_presentation_receipt(settings, presentation_receipt("d" * 32))
    plugin.reconcile_presentation_receipt(
        settings,
        presentation_receipt("c" * 32, display="origin-display"),
    )
    assert state_path.read_bytes() == baseline

    plugin.reconcile_presentation_receipt(settings, presentation_receipt("c" * 32))
    committed = state_path.read_bytes()
    plugin.reconcile_presentation_receipt(settings, presentation_receipt("c" * 32))
    plugin.reconcile_presentation_receipt(
        settings,
        presentation_receipt("c" * 32, committed_at="2026-07-12T09:00:00+00:00"),
    )
    assert state_path.read_bytes() == committed
    final = json.loads(committed.decode("utf-8"))
    profile = profile_for_instance(final)
    assert profile["pending_selection"] is None
    assert profile["date_buckets"]["2026-07-12"]["seen_illust_ids"][-len(pending_ids):] == pending_ids


def test_pending_survives_restart_theme_and_jst_rollover(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    now = {"value": datetime(2026, 7, 12, 14, 59, tzinfo=timezone.utc)}
    monkeypatch.setattr(plugin, "_now_utc", lambda: now["value"])
    warm_bank(tmp_path, date_key="2026-07-12")
    first = plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=presentation_request("e" * 32),
        resolved_theme_context=presentation_theme("day"),
    )
    first_state = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    pending = profile_for_instance(first_state)["pending_selection"]

    restarted = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    now["value"] = datetime(2026, 7, 12, 15, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(restarted, "_now_utc", lambda: now["value"])
    monkeypatch.setattr(restarted, "_fetch_ranking_page", lambda *_args, **_kwargs: pytest.fail("restart used provider"))
    second = restarted.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=presentation_request("e" * 32),
        resolved_theme_context=presentation_theme("night"),
    )

    second_state = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    assert profile_for_instance(second_state)["pending_selection"] == pending
    assert first.image.info["inkypi_theme_mode"] == "day"
    assert second.image.info["inkypi_theme_mode"] == "night"


def test_identical_instances_are_isolated_and_raw_json_cannot_spoof(tmp_path, monkeypatch):
    first = make_presentation_bank(tmp_path, instance_uuid="first")
    second = make_presentation_bank(tmp_path, instance_uuid="second")
    first_document, first_profile = first.load_for_data()
    first.ingest(first_profile, bank_candidate(1), Image.new("RGB", (200, 300), "red"))
    first.save(first_document)
    second_document, second_profile = second.load_for_data()
    second.ingest(second_profile, bank_candidate(2), Image.new("RGB", (200, 300), "blue"))
    second.save(second_document)
    state = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))

    assert state["instance_profiles"]["first"] != state["instance_profiles"]["second"]
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    before = {
        path.relative_to(tmp_path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    spoof = {
        "_inkypi_presentation_instance_identity": {"instance_uuid": "first"},
        "rankingMode": "day_r18",
    }
    monkeypatch.setattr(plugin, "_fetch_ranking_page", lambda *_args, **_kwargs: [])
    plugin.generate_image(spoof, DummyDeviceConfig())
    after = {
        path.relative_to(tmp_path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_bank_rejects_oversize_decompression_and_symlink_media(tmp_path):
    from plugins.pixiv_r18_ranking import presentation_bank

    bank = make_presentation_bank(tmp_path)
    document, profile = bank.load_for_data()
    with pytest.raises(RuntimeError, match="dimensions"):
        bank.ingest(
            profile,
            bank_candidate(1),
            Image.new("RGB", (presentation_bank.MEDIA_MAX_DIMENSION + 1, 1), "red"),
        )
    bank.ingest(profile, bank_candidate(2), Image.new("RGB", (200, 300), "blue"))
    bank.save(document)
    record = profile["records"][0]
    media = bank.media.path(record["media_key"], suffix=".png")
    media.unlink()
    target = tmp_path / "outside.png"
    target.write_bytes(b"not an image")
    try:
        media.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")
    with pytest.raises(RuntimeError, match="regular|media"):
        bank.load_media(record)


def test_state_budget_symlink_and_atomic_write_fail_closed(tmp_path):
    from plugins.pixiv_r18_ranking import presentation_bank

    state_path = tmp_path / "presentation-state.json"
    outside = tmp_path / "outside.json"
    outside.write_text('{"sentinel":true}', encoding="utf-8")
    try:
        state_path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")
    bank = make_presentation_bank(tmp_path)
    with pytest.raises(RuntimeError, match="safe|regular|state"):
        bank.load_for_data()
    assert outside.read_text(encoding="utf-8") == '{"sentinel":true}'
    state_path.unlink()
    state_path.write_bytes(b"{" + b"x" * presentation_bank.MAX_STATE_BYTES + b"}")
    with pytest.raises(RuntimeError, match="size"):
        bank.load_for_data()


def test_legacy_pool_json_is_bounded_before_decode(tmp_path, monkeypatch):
    from plugins.pixiv_r18_ranking import presentation_bank

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    oversized = {
        "state_version": pixiv_mod.STATE_VERSION,
        "day_key": plugin._day_key(),
        "items": [],
        "padding": "x" * presentation_bank.MAX_STATE_BYTES,
    }
    plugin._daily_pool_path().write_text(json.dumps(oversized), encoding="utf-8")

    with pytest.raises(RuntimeError, match="size"):
        plugin._read_daily_pool_payload()


def test_cleanup_preserves_current_pending_and_removes_only_old_unprotected(tmp_path):
    bank, document, profile, current = warm_bank(tmp_path, count=10)
    ready = bank.ready_records(profile, prune=False)
    pending = bank.choose_selection(document, profile, ready, "auto_layout")
    pending["request_id"] = "f" * 32
    profile["pending_selection"] = pending
    bank.save(document)
    protected = set(current["record_keys"]) | set(pending["record_keys"])
    old = (datetime.now(timezone.utc) - timedelta(hours=49)).timestamp()
    for record in profile["records"]:
        path = bank.media.path(record["media_key"], suffix=".png")
        os.utime(path, (old, old))

    bank.cleanup(document, profile)

    for record in profile["records"]:
        path = bank.media.path(record["media_key"], suffix=".png")
        if record["record_key"] in protected:
            assert path.is_file()


def test_media_count_budget_evicts_unprotected_before_current_or_pending(tmp_path):
    from plugins.pixiv_r18_ranking import presentation_bank

    bank, document, profile, current = warm_bank(tmp_path, count=3)
    ready = bank.ready_records(profile, prune=False)
    pending = bank.choose_selection(document, profile, ready, "auto_layout")
    pending["request_id"] = "9" * 32
    profile["pending_selection"] = pending
    bank.save(document)
    protected_keys = set(current["record_keys"]) | set(pending["record_keys"])
    protected_media = {
        bank.media.path(record["media_key"], suffix=".png")
        for record in profile["records"]
        if record["record_key"] in protected_keys
    }
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    for path in protected_media:
        os.utime(path, (old, old))
    for index in range(presentation_bank.MEDIA_MAX_FILES - len(profile["records"])):
        (bank.media_dir / f"unprotected-{index:03d}.png").write_bytes(b"x")

    bank.ingest(
        profile,
        bank_candidate(99),
        Image.new("RGB", (240, 420), "green"),
    )

    assert all(path.is_file() for path in protected_media)
    assert len([path for path in bank.media_dir.glob("*.png") if path.is_file()]) <= (
        presentation_bank.MEDIA_MAX_FILES
    )


def test_stateless_preview_and_theme_chrome_do_not_mutate_bank(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    bank, _document, _profile, _current = warm_bank(tmp_path)
    state_before = bank.state_path.read_bytes()
    media_before = sorted(path.read_bytes() for path in bank.media_dir.glob("*.png"))
    monkeypatch.setattr(plugin, "_fetch_ranking_page", lambda *_args, **_kwargs: [])

    preview = plugin.generate_image({"rankingMode": "day_r18"}, DummyDeviceConfig())

    assert preview.size == (800, 480)
    assert bank.state_path.read_bytes() == state_before
    assert sorted(path.read_bytes() for path in bank.media_dir.glob("*.png")) == media_before


def test_data_refill_adds_at_most_twelve_per_run_and_continues_to_target(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))
    items = [make_ranking_item(index) for index in range(1, 41)]
    resolution = {
        "requested_mode": "day_r18",
        "effective_mode": "daily_r18",
        "content_rating": "r18",
        "authenticated": True,
        "healthy_r18": True,
        "source_status": "fresh",
        "cookie": "session-cookie",
        "items": items,
    }
    downloads = []
    monkeypatch.setattr(
        plugin,
        "_resolve_ranking_with_provenance",
        lambda *_args, **_kwargs: dict(resolution),
    )
    monkeypatch.setattr(plugin, "_fetch_ranking_page", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        plugin,
        "_download_ranking_item_source_image",
        lambda item, _dimensions, **_kwargs: downloads.append(item["illust_id"])
        or Image.new("RGB", (240, 420), "purple"),
    )

    plugin.generate_image(settings, DummyDeviceConfig())
    first = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    first_profile = profile_for_instance(first)
    assert len(downloads) == 12
    assert len(first_profile["records"]) == 12
    assert first_profile["refill_in_progress"] is True
    assert first_profile.get("date_buckets", {}).get("2026-07-12", {}).get("seen_illust_ids", []) == []

    plugin.generate_image(settings, DummyDeviceConfig())
    second = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    second_profile = profile_for_instance(second)
    assert len(downloads) == 24
    assert len(second_profile["records"]) == 24
    assert second_profile["refill_in_progress"] is False


def test_full_same_day_bank_keeps_daily_source_pool_without_provider_or_selection_advance(
    tmp_path,
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))
    bank, _document, _profile, current = warm_bank(tmp_path)
    monkeypatch.setattr(
        plugin,
        "_resolve_ranking_with_provenance",
        lambda *_args, **_kwargs: pytest.fail("full same-day bank refetched ranking"),
    )
    monkeypatch.setattr(
        plugin,
        "_download_ranking_item_source_image",
        lambda *_args, **_kwargs: pytest.fail("full same-day bank downloaded media"),
    )

    image = plugin.generate_image(settings, DummyDeviceConfig())
    after = json.loads(bank.state_path.read_text(encoding="utf-8"))
    profile = profile_for_instance(after)

    assert image.size == (800, 480)
    assert profile["current_selection"] == current
    assert profile["pending_selection"] is None
    assert profile.get("date_buckets", {}).get("2026-07-12", {}).get("seen_illust_ids", []) == []


@pytest.mark.parametrize("force_key", ["forceRefresh", "force_refresh"])
def test_pixiv_force_refresh_attempts_r18_provider_for_full_bank_without_consuming_selection(
    tmp_path,
    monkeypatch,
    force_key,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))
    bank, _document, _profile, current = warm_bank(tmp_path)
    calls = []
    item = make_ranking_item(99)
    resolution = {
        "requested_mode": "day_r18",
        "effective_mode": "daily_r18",
        "content_rating": "r18",
        "authenticated": True,
        "healthy_r18": True,
        "source_status": "fresh",
        "cookie": "session-cookie",
        "items": [item],
    }
    monkeypatch.setattr(
        plugin,
        "_resolve_ranking_with_provenance",
        lambda *_args, **_kwargs: calls.append("provider") or dict(resolution),
    )
    monkeypatch.setattr(plugin, "_fetch_ranking_page", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        plugin,
        "_download_ranking_item_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (240, 420), "purple"),
    )

    image = plugin.generate_image({**settings, force_key: "true"}, DummyDeviceConfig())

    state = json.loads(bank.state_path.read_text(encoding="utf-8"))
    profile = profile_for_instance(state)
    assert calls == ["provider"]
    assert profile["last_provider_status"] == "success"
    assert datetime.fromisoformat(profile["last_provider_attempt_at"]).tzinfo is not None
    assert profile["current_selection"] == current
    assert profile["pending_selection"] is None
    assert any(record["illust_id"] == "99" for record in profile["records"])
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE


def test_pixiv_force_refresh_provider_exception_marks_warm_bank_stale_and_skips_cache(
    tmp_path,
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(
        plugin,
        "_now_utc",
        lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )
    bank, _document, _profile, _current = warm_bank(tmp_path)
    monkeypatch.setattr(
        plugin,
        "_resolve_ranking_with_provenance",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider offline")),
    )

    image = plugin.generate_image(
        {**settings, "forceRefresh": "true"},
        DummyDeviceConfig(),
    )

    state = json.loads(bank.state_path.read_text(encoding="utf-8"))
    profile = profile_for_instance(state)
    assert profile["last_provider_status"] == "error"
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True


def test_pixiv_forced_r18_sfw_fallback_is_persisted_as_error_and_fails(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings(forceRefresh="true")
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))
    bank, _document, _profile, current = warm_bank(tmp_path)
    fallback = {
        "requested_mode": "day_r18",
        "effective_mode": "daily",
        "content_rating": "sfw",
        "authenticated": False,
        "healthy_r18": False,
        "source_status": "fresh_sfw_fallback",
        "cookie": None,
        "items": [make_ranking_item(100, sexual=0, tags=[])],
    }
    monkeypatch.setattr(
        plugin,
        "_resolve_ranking_with_provenance",
        lambda *_args, **_kwargs: dict(fallback),
    )

    with pytest.raises(RuntimeError, match="R-18|SFW|fallback"):
        plugin.generate_image(settings, DummyDeviceConfig())

    state = json.loads(bank.state_path.read_text(encoding="utf-8"))
    profile = profile_for_instance(state)
    assert profile["last_provider_status"] == "error"
    assert datetime.fromisoformat(profile["last_provider_attempt_at"]).tzinfo is not None
    assert profile["current_selection"] == current
    assert all(record["content_rating"] == "r18" for record in profile["records"])


def test_data_recovers_exact_protected_media_or_fails_without_state_change(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))
    bank, document, profile, current = warm_bank(tmp_path)
    record = next(item for item in profile["records"] if item["record_key"] == current["record_keys"][0])
    media = bank.media.path(record["media_key"], suffix=".png")
    media.unlink()
    recovered_urls = []
    resolution = {
        "requested_mode": "day_r18",
        "effective_mode": "daily_r18",
        "content_rating": "r18",
        "authenticated": True,
        "healthy_r18": True,
        "source_status": "fresh",
        "cookie": "session-cookie",
        "items": [],
    }
    monkeypatch.setattr(
        plugin,
        "_resolve_ranking_with_provenance",
        lambda *_args, **_kwargs: dict(resolution),
    )
    monkeypatch.setattr(
        plugin,
        "_download_ranking_item_source_image",
        lambda item, _dimensions, **_kwargs: recovered_urls.append(item["image_url"])
        or Image.new("RGB", (240, 420), "blue"),
    )

    plugin.generate_image(settings, DummyDeviceConfig())
    recovered = json.loads(bank.state_path.read_text(encoding="utf-8"))
    assert profile_for_instance(recovered)["current_selection"] == current
    assert recovered_urls == [record["image_url"]]

    media.unlink()
    baseline = bank.state_path.read_bytes()
    monkeypatch.setattr(
        plugin,
        "_download_ranking_item_source_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    with pytest.raises(RuntimeError, match="protected|recover"):
        plugin.generate_image(settings, DummyDeviceConfig())
    assert bank.state_path.read_bytes() == baseline


def test_sfw_fallback_provenance_is_persisted_without_healthy_r18(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(
        plugin,
        "_fetch_ranking_page",
        lambda mode, _cookie, page=1, **_kwargs: [make_ranking_item(1, sexual=0)] if page == 1 else [],
    )
    monkeypatch.setattr(
        plugin,
        "_download_ranking_item_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (240, 420), "purple"),
    )

    plugin.generate_image(settings, DummyDeviceConfig(cookie=""))
    state = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    profile = profile_for_instance(state)

    assert profile["source_provenance"]["content_rating"] == "sfw"
    assert profile["source_provenance"]["healthy_r18"] is False
    assert profile["source_provenance"]["authenticated"] is False
    assert all(record["content_rating"] == "sfw" for record in profile["records"])


def test_full_triptych_round_is_committed_only_by_exact_receipts(tmp_path, monkeypatch):
    from plugins.pixiv_r18_ranking import presentation_bank

    monkeypatch.setattr(presentation_bank.random, "shuffle", lambda _values: None)
    bank, document, profile, current = warm_bank(tmp_path)
    committed = [
        record["illust_id"]
        for key in current["record_keys"]
        for record in profile["records"]
        if record["record_key"] == key
    ]
    for index in range(7):
        request_id = f"{index + 1:032x}"
        req = presentation_request(request_id, origin=f"origin-{index}")
        bank.apply_trusted_origin(document, profile, req)
        ready = bank.ready_records(profile, prune=False)
        selection = bank.choose_selection(document, profile, ready, "auto_layout")
        bank.set_pending(document, profile, req, selection)
        before = list(profile["date_buckets"]["2026-07-12"]["seen_illust_ids"])
        selected_ids = [
            record["illust_id"]
            for key in selection["record_keys"]
            for record in profile["records"]
            if record["record_key"] == key
        ]
        assert not set(selected_ids).issubset(before)
        bank.reconcile_receipt(document, profile, presentation_receipt(request_id))
        committed.extend(selected_ids)

    assert len(committed) == 24
    assert len(set(committed)) == 24


def test_same_jst_day_expired_media_is_usable_but_marked_stale_and_cross_day_is_not_fresh(
    tmp_path,
):
    bank = make_presentation_bank(tmp_path, date_key="2026-07-12")
    document, profile = bank.load_for_data()
    record = bank.ingest(
        profile,
        bank_candidate(1),
        Image.new("RGB", (240, 420), "purple"),
        downloaded_at=(datetime.now(timezone.utc) - timedelta(hours=49)).isoformat(),
    )
    bank.save(document)

    ready = bank.ready_records(profile, prune=False)

    assert [item["illust_id"] for item in ready] == ["1"]
    assert record["source_status"] == "stale"

    next_day = make_presentation_bank(tmp_path, date_key="2026-07-13")
    next_document, next_profile = next_day.load_for_data()
    assert next_day.ready_records(next_profile, prune=False) == []
    assert profile["records"][0]["date_key"] == "2026-07-12"


def test_theme_only_uses_current_media_without_provider_or_state_change(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    settings = bound_settings(_theme_render_only=True)
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc))
    bank, _document, _profile, _current = warm_bank(tmp_path)
    baseline = bank.state_path.read_bytes()
    monkeypatch.setattr(plugin, "_fetch_ranking_page", lambda *_args, **_kwargs: pytest.fail("theme used provider"))
    monkeypatch.setattr(plugin, "_download_to_temp", lambda *_args, **_kwargs: pytest.fail("theme downloaded"))

    image = plugin.generate_image(settings, DummyDeviceConfig())

    assert image.size == (800, 480)
    assert bank.state_path.read_bytes() == baseline


class ApprovedTarget:
    def __init__(self, url, *, host="i.pximg.net", address="93.184.216.34"):
        self.normalized_url = url
        self.scheme = urlparse(url).scheme
        self.hostname = host
        self.port = 443 if self.scheme == "https" else 80
        self.addresses = (address,)

    @property
    def authority(self):
        return self.hostname


class RedirectResponse:
    def __init__(self, url, status, *, location=None, payload=b"image"):
        self.url = url
        self.status_code = status
        self.headers = {} if location is None else {"Location": location}
        self.payload = payload
        self.body_read = False

    def iter_content(self, chunk_size):
        self.body_read = True
        yield self.payload

    def close(self):
        return None


def test_media_redirect_to_private_is_rejected_before_second_request(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    first_url = "https://i.pximg.net/start.jpg"
    response = RedirectResponse(first_url, 302, location="http://127.0.0.1/private.jpg")
    requests = []

    class Policy:
        def resolve_and_validate(self, url):
            if "127.0.0.1" in url:
                raise RuntimeError("private address")
            return ApprovedTarget(url)

    def request_approved(approved, **kwargs):
        requests.append((approved, kwargs))
        return response

    monkeypatch.setattr(pixiv_mod, "get_ssrf_policy", lambda: Policy())
    monkeypatch.setattr(plugin, "_request_approved_target", request_approved)

    with pytest.raises(RuntimeError, match="private"):
        plugin._download_media_bytes(first_url, max_bytes=1024, timeout=5)
    assert len(requests) == 1
    assert response.body_read is False


def test_media_unexpected_private_final_url_is_rejected_before_body(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    first_url = "https://i.pximg.net/start.jpg"
    response = RedirectResponse("http://127.0.0.1/final.jpg", 200)

    class Policy:
        def resolve_and_validate(self, url):
            if "127.0.0.1" in url:
                raise RuntimeError("private final")
            return ApprovedTarget(url)

    monkeypatch.setattr(pixiv_mod, "get_ssrf_policy", lambda: Policy())
    monkeypatch.setattr(
        plugin,
        "_request_approved_target",
        lambda _approved, **_kwargs: response,
    )

    with pytest.raises(RuntimeError, match="private final"):
        plugin._download_media_bytes(first_url, max_bytes=1024, timeout=5)
    assert response.body_read is False


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "::ffff:127.0.0.1"])
def test_pixiv_media_target_rejects_non_public_dns_answers(address):
    from plugins.pixiv_r18_ranking.presentation_bank import validate_pixiv_media_target

    approved = ApprovedTarget(
        "https://i.pximg.net/image.jpg",
        address=address,
    )
    with pytest.raises(RuntimeError, match="public|address"):
        validate_pixiv_media_target(approved)


def test_legacy_old_day_cleanup_is_bounded_and_never_follows_symlinks(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(tmp_path))
    old_day = tmp_path / "images" / "2026-07-10"
    current_day = tmp_path / "images" / "2026-07-12"
    old_day.mkdir(parents=True)
    current_day.mkdir(parents=True)
    (old_day / "old.jpg").write_bytes(b"old")
    (current_day / "current.jpg").write_bytes(b"current")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    link = old_day / "link.jpg"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        link = None

    removed = plugin._cleanup_legacy_image_days("2026-07-12")

    assert removed == 1
    assert not (old_day / "old.jpg").exists()
    assert (current_day / "current.jpg").read_bytes() == b"current"
    assert outside.read_bytes() == b"outside"
    if link is not None:
        assert link.is_symlink()


def test_restart_with_65_media_files_keeps_oldest_current_and_pending_offline_renderable(
    tmp_path,
):
    from plugins.pixiv_r18_ranking import presentation_bank
    from utils import cache_manager

    bank, document, profile, current = warm_bank(tmp_path, count=6)
    pending = bank.choose_selection(
        document,
        profile,
        bank.ready_records(profile, prune=False),
        "auto_layout",
    )
    pending["request_id"] = "8" * 32
    profile["pending_selection"] = pending
    bank.save(document)
    protected_keys = set(current["record_keys"]) | set(pending["record_keys"])
    protected_paths = {
        bank.media.path(record["media_key"], suffix=".png")
        for record in profile["records"]
        if record["record_key"] in protected_keys
    }
    oldest = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    for path in protected_paths:
        os.utime(path, (oldest, oldest))
    filler = 0
    while len(list(bank.media_dir.glob("*.png"))) < presentation_bank.MEDIA_MAX_FILES + 1:
        (bank.media_dir / f"restart-unprotected-{filler:03d}.png").write_bytes(b"x")
        filler += 1

    with cache_manager._GLOBAL_MANAGER_LOCK:
        cache_manager._AUXILIARY_MANAGERS.clear()
    restarted = make_presentation_bank(tmp_path)
    restarted_document, restarted_profile = restarted.load_for_data()
    restarted.cleanup(restarted_document, restarted_profile)

    assert all(path.is_file() for path in protected_paths)
    assert restarted.selection_records(
        restarted_profile,
        restarted_profile["current_selection"],
        load_media=True,
    )
    assert restarted.selection_records(
        restarted_profile,
        restarted_profile["pending_selection"],
        load_media=True,
    )
    assert len(list(bank.media_dir.glob("*.png"))) <= presentation_bank.MEDIA_MAX_FILES


def test_cross_profile_byte_admission_fails_closed_without_deleting_protected_media(
    tmp_path,
    monkeypatch,
):
    from plugins.pixiv_r18_ranking import presentation_bank

    first = make_presentation_bank(tmp_path, instance_uuid="first")
    first_document, first_profile = first.load_for_data()
    first_record = first.ingest(
        first_profile,
        bank_candidate(1),
        Image.new("RGB", (240, 420), "red"),
    )
    first.ensure_current(first_document, first_profile, [first_record], "contain")
    first.save(first_document)

    second = make_presentation_bank(tmp_path, instance_uuid="second")
    second_document, second_profile = second.load_for_data()
    second_record = second.ingest(
        second_profile,
        bank_candidate(2),
        Image.new("RGB", (240, 420), "blue"),
    )
    second.ensure_current(second_document, second_profile, [second_record], "contain")
    second.save(second_document)
    first_path = second.media.path(first_record["media_key"], suffix=".png")
    second_path = second.media.path(second_record["media_key"], suffix=".png")
    current_bytes = first_path.stat().st_size + second_path.stat().st_size
    monkeypatch.setattr(presentation_bank, "MEDIA_MAX_BYTES", current_bytes + 1)

    with pytest.raises(RuntimeError, match="protected|budget"):
        second.ingest(
            second_profile,
            bank_candidate(3),
            Image.new("RGB", (240, 420), "green"),
        )

    assert first_path.is_file()
    assert second_path.is_file()


def test_presentation_bank_uses_durable_data_root_outside_global_cache(
    tmp_path,
    monkeypatch,
):
    cache_root = tmp_path / "cache"
    data_root = tmp_path / "data"
    monkeypatch.delenv("INKYPI_PIXIV_R18_CACHE", raising=False)
    monkeypatch.delenv("INKYPI_PIXIV_R18_DATA", raising=False)
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(data_root))
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})

    assert plugin._presentation_state_path().is_relative_to(data_root)
    assert plugin._presentation_media_dir().is_relative_to(data_root)
    assert not plugin._presentation_state_path().is_relative_to(cache_root)
    assert not plugin._presentation_media_dir().is_relative_to(cache_root)


def test_data_deadline_is_shared_by_recovery_refill_pages_and_media_streams(
    tmp_path,
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 0.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"], raising=False)
    bank, document, profile, current = warm_bank(tmp_path, count=4)
    record = next(
        item for item in profile["records"]
        if item["record_key"] == current["record_keys"][0]
    )
    bank.media.path(record["media_key"], suffix=".png").unlink()
    recovery_deadlines = []

    def recover(_item, _dimensions, *, deadline=None):
        recovery_deadlines.append(deadline)
        return Image.new("RGB", (240, 420), "purple")

    monkeypatch.setattr(plugin, "_download_ranking_item_source_image", recover)
    plugin._recover_protected_media(
        bank,
        document,
        profile,
        (800, 480),
        deadline=30.0,
    )
    assert recovery_deadlines == [30.0]

    calls = []

    def bounded_download(_item, _dimensions, *, deadline=None):
        assert deadline == 30.0
        remaining = deadline - clock["value"]
        assert remaining > 0
        spent = min(12.0, remaining)
        clock["value"] += spent
        calls.append(spent)
        if clock["value"] >= deadline:
            raise RuntimeError("deadline exhausted")
        return Image.new("RGB", (240, 420), "purple")

    monkeypatch.setattr(plugin, "_download_ranking_item_source_image", bounded_download)
    resolution = {
        "requested_mode": "day_r18",
        "effective_mode": "daily_r18",
        "content_rating": "r18",
        "authenticated": True,
        "healthy_r18": True,
        "source_status": "fresh",
        "cookie": "session-cookie",
        "items": [make_ranking_item(index) for index in range(100, 120)],
    }
    plugin._refill_presentation_bank(
        bank,
        profile,
        resolution,
        (800, 480),
        deadline=30.0,
    )

    assert clock["value"] <= 30.0
    assert calls == [12.0, 12.0, 6.0]


@pytest.mark.parametrize("chunked", [False, True])
def test_ranking_json_is_bounded_before_decode_and_never_calls_response_json(
    monkeypatch,
    chunked,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    limit = 4 * 1024 * 1024

    class OversizeRankingResponse:
        status_code = 200

        def __init__(self):
            self.headers = {} if chunked else {"Content-Length": str(limit + 1)}
            self.json_called = False
            self.body_read = False

        def raise_for_status(self):
            return None

        def json(self):
            self.json_called = True
            raise AssertionError("response.json must not be called")

        def iter_content(self, chunk_size):
            self.body_read = True
            remaining = limit + 1
            while remaining:
                chunk = b"x" * min(chunk_size, remaining)
                remaining -= len(chunk)
                yield chunk

        def close(self):
            return None

    response = OversizeRankingResponse()
    monkeypatch.setattr(
        plugin,
        "_request_ranking_target",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(RuntimeError, match="budget|large|size"):
        plugin._fetch_ranking_page("daily", None)

    assert response.json_called is False
    assert response.body_read is chunked


def test_media_transport_consumes_each_approved_target_without_hostname_reresolution(
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    approved_targets = []

    class Policy:
        def resolve_and_validate(self, url):
            parsed = urlparse(url)
            return ApprovedTarget(
                url,
                host=parsed.hostname,
                address="93.184.216.34",
            )

    class Response:
        status_code = 200
        headers = {}

        def __init__(self, url):
            self.url = url

        def iter_content(self, chunk_size):
            yield b"image"

        def close(self):
            return None

    def request_approved(approved, **_kwargs):
        approved_targets.append(approved)
        return Response(approved.normalized_url)

    monkeypatch.setattr(pixiv_mod, "get_ssrf_policy", lambda: Policy())
    assert not hasattr(pixiv_mod, "get_http_client")
    monkeypatch.setattr(
        plugin,
        "_request_approved_target",
        request_approved,
        raising=False,
    )

    for url in (
        "https://www.pixiv.net/image.jpg",
        "https://i.pximg.net/image.jpg",
    ):
        assert plugin._download_media_bytes(url, max_bytes=1024, timeout=5) == b"image"

    assert [target.hostname for target in approved_targets] == [
        "www.pixiv.net",
        "i.pximg.net",
    ]


def test_media_descriptor_read_fails_closed_when_path_is_deleted_after_open(
    tmp_path,
    monkeypatch,
):
    from plugins.pixiv_r18_ranking import presentation_bank

    bank = make_presentation_bank(tmp_path)
    document, profile = bank.load_for_data()
    record = bank.ingest(
        profile,
        bank_candidate(1),
        Image.new("RGB", (240, 420), "purple"),
    )
    bank.save(document)
    media_path = bank.media.path(record["media_key"], suffix=".png")
    replacement_path = tmp_path / "replacement.png"
    replacement_path.write_bytes(media_path.read_bytes())
    original_open = os.open
    removed = {"value": False}

    def deleting_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        raw = os.fspath(path)
        if not removed["value"] and (
            Path(raw).name == media_path.name
        ):
            os.close(descriptor)
            media_path.unlink()
            descriptor = original_open(replacement_path, flags)
            removed["value"] = True
        return descriptor

    monkeypatch.setattr(os, "open", deleting_open)

    with pytest.raises(RuntimeError, match="identity|changed|media|missing") as caught:
        bank.load_media(record, allow_stale=True)

    assert removed["value"] is True, str(caught.value)


def test_virtual_clock_hard_deadline_caps_ranking_redirect_and_stream_requests(
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 0.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])

    class TimedRankingResponse(FakeResponse):
        def iter_content(self, chunk_size):
            clock["value"] += 20.0
            yield self._payload

    ranking_calls = []

    def ranking_request(_url, *, timeout, deadline, **_kwargs):
        ranking_calls.append((timeout, deadline))
        clock["value"] += 10.0
        return TimedRankingResponse({"contents": [make_ranking_item(1, sexual=0)]})

    monkeypatch.setattr(plugin, "_request_ranking_target", ranking_request)
    with pytest.raises(RuntimeError, match="deadline"):
        plugin._fetch_ranking_page("daily", None, deadline=30.0)
    assert clock["value"] == 30.0
    assert ranking_calls == [(30.0, 30.0)]
    with pytest.raises(RuntimeError, match="deadline"):
        plugin._fetch_ranking_page("daily", None, deadline=30.0)
    assert len(ranking_calls) == 1

    clock["value"] = 0.0
    first_url = "https://i.pximg.net/start.jpg"
    final_url = "https://i.pximg.net/final.jpg"
    media_calls = []

    class Policy:
        def resolve_and_validate(self, url):
            return ApprovedTarget(url, host="i.pximg.net")

    class FinalResponse(RedirectResponse):
        def iter_content(self, chunk_size):
            clock["value"] += 18.0
            yield b"image"

    responses = [
        RedirectResponse(first_url, 302, location=final_url),
        FinalResponse(final_url, 200),
    ]

    def media_request(_approved, *, timeout, deadline, **_kwargs):
        media_calls.append((timeout, deadline))
        if len(media_calls) == 1:
            clock["value"] += 12.0
        return responses.pop(0)

    monkeypatch.setattr(pixiv_mod, "get_ssrf_policy", lambda: Policy())
    monkeypatch.setattr(plugin, "_request_approved_target", media_request)
    with pytest.raises(RuntimeError, match="deadline"):
        plugin._download_media_bytes(
            first_url,
            max_bytes=1024,
            timeout=40,
            deadline=30.0,
        )
    assert clock["value"] == 30.0
    assert media_calls == [(30.0, 30.0), (18.0, 30.0)]


@pytest.mark.parametrize("hostname", ["www.pixiv.net", "i.pximg.net"])
def test_pinned_https_transport_uses_approved_ip_with_original_host_and_sni(
    monkeypatch,
    hostname,
):
    raw_sockets = []
    wrapped = []

    class FakeSocket:
        def __init__(self):
            self.connected = None
            self.timeouts = []
            self.sent = b""
            self.closed = False

        def settimeout(self, value):
            self.timeouts.append(value)

        def connect(self, endpoint):
            self.connected = endpoint

        def sendall(self, payload):
            self.sent += payload

        def close(self):
            self.closed = True

    class FakeContext:
        def wrap_socket(self, raw, *, server_hostname):
            wrapped.append((raw, server_hostname))
            return raw

    class FakeHTTPResponse:
        status = 200
        headers = {}

        def __init__(self, connection):
            self.connection = connection
            self.reads = 0

        def begin(self):
            return None

        def read(self, _size):
            self.reads += 1
            return b"ok" if self.reads == 1 else b""

        def close(self):
            return None

    def socket_factory(_family, _kind):
        created = FakeSocket()
        raw_sockets.append(created)
        return created

    monkeypatch.setattr(pixiv_mod.socket, "socket", socket_factory)
    monkeypatch.setattr(pixiv_mod.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(pixiv_mod.http.client, "HTTPResponse", FakeHTTPResponse)
    approved = ApprovedTarget(
        f"https://{hostname}/folder/image.jpg?size=large",
        host=hostname,
        address="93.184.216.34",
    )

    response = pixiv_mod._PinnedHTTPSResponse.open(
        approved,
        headers={"User-Agent": "test"},
        deadline=30.0,
        clock=lambda: 0.0,
        timeout=5.0,
    )
    assert b"".join(response.iter_content(64)) == b"ok"
    response.close()

    assert raw_sockets[0].connected == ("93.184.216.34", 443)
    assert wrapped == [(raw_sockets[0], hostname)]
    request = raw_sockets[0].sent.decode("latin-1")
    assert "GET /folder/image.jpg?size=large HTTP/1.1" in request
    assert f"Host: {hostname}" in request


def test_download_chunk_size_compatibility_export_keeps_reddit_plugin_importable():
    assert pixiv_mod.DOWNLOAD_CHUNK_SIZE == 8192
    sys.modules.pop("plugins.reddit_rule34_hot.reddit_rule34_hot", None)

    imported = importlib.import_module("plugins.reddit_rule34_hot.reddit_rule34_hot")

    assert imported.DOWNLOAD_CHUNK_SIZE == 8192


def test_ranking_json_cpu_overrun_at_29_to_31_seconds_returns_no_payload(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    response = FakeResponse({"contents": [make_ranking_item(1, sexual=0)]})
    monkeypatch.setattr(
        plugin,
        "_request_ranking_target",
        lambda *_args, **_kwargs: response,
    )
    real_loads = json.loads

    def slow_loads(payload):
        value = real_loads(payload)
        clock["value"] = 31.0
        return value

    monkeypatch.setattr(pixiv_mod.json, "loads", slow_loads)

    with pytest.raises(RuntimeError, match="deadline"):
        plugin._fetch_ranking_page("daily", None, deadline=30.0)

    assert clock["value"] == 31.0
    assert response.closed is True


@pytest.mark.parametrize("stage", ["source_info", "downsample", "decode", "thumbnail"])
def test_media_cpu_stage_overrun_fails_closed_and_cleans_temporary_files(
    tmp_path,
    monkeypatch,
    stage,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    source_path = tmp_path / "source.png"
    resized_path = tmp_path / "resized.png"
    Image.new("RGB", (40, 60), "purple").save(source_path)
    Image.new("RGB", (40, 60), "purple").save(resized_path)
    monkeypatch.setattr(
        plugin,
        "_download_to_temp",
        lambda *_args, **_kwargs: source_path,
    )

    def source_info(_path):
        if stage == "source_info":
            clock["value"] = 31.0
        return {
            "pixels": 1_000_000 if stage == "downsample" else 2400,
            "format": "PNG",
            "width": 40,
            "height": 60,
        }

    monkeypatch.setattr(plugin, "_source_image_info", source_info)

    def downsample(_path):
        if stage == "downsample":
            clock["value"] = 31.0
        return resized_path

    monkeypatch.setattr(plugin, "_downsample_to_pi_safe_image", downsample)
    real_safe_open = pixiv_mod.safe_open_image

    class ThumbnailImage:
        def convert(self, _mode):
            return self

        def thumbnail(self, _size, _filter):
            clock["value"] = 31.0

    def open_image(path, **kwargs):
        if stage == "decode":
            clock["value"] = 31.0
        if stage == "thumbnail":
            return ThumbnailImage()
        return real_safe_open(path, **kwargs)

    monkeypatch.setattr(pixiv_mod, "safe_open_image", open_image)

    with pytest.raises(RuntimeError, match="deadline"):
        plugin._download_ranking_item_source_image(
            bank_candidate(1),
            (800, 480),
            deadline=30.0,
        )

    assert not source_path.exists()
    if stage == "downsample":
        assert not resized_path.exists()
    else:
        assert resized_path.exists()


def test_download_return_overrun_creates_no_temporary_file(tmp_path, monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])

    def download(*_args, **_kwargs):
        clock["value"] = 31.0
        return b"image-bytes"

    monkeypatch.setattr(plugin, "_download_media_bytes", download)
    original_named_temp = pixiv_mod.tempfile.NamedTemporaryFile

    def named_temp(*args, **kwargs):
        kwargs["dir"] = tmp_path
        return original_named_temp(*args, **kwargs)

    monkeypatch.setattr(pixiv_mod.tempfile, "NamedTemporaryFile", named_temp)

    with pytest.raises(RuntimeError, match="deadline"):
        plugin._download_to_temp(
            "https://i.pximg.net/image.jpg",
            deadline=30.0,
        )

    assert list(tmp_path.iterdir()) == []


def test_post_network_deadline_prevents_recover_ingest_and_save_transactions(
    tmp_path,
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    bank, document, profile, current = warm_bank(tmp_path, count=4)
    protected = next(
        record for record in profile["records"]
        if record["record_key"] == current["record_keys"][0]
    )
    protected_path = bank.media.path(protected["media_key"], suffix=".png")
    protected_path.unlink()
    state_before = bank.state_path.read_bytes()
    transactions = []

    def late_image(*_args, **_kwargs):
        clock["value"] = 31.0
        return Image.new("RGB", (240, 420), "purple")

    monkeypatch.setattr(plugin, "_download_ranking_item_source_image", late_image)
    monkeypatch.setattr(
        bank,
        "recover_media",
        lambda *_args, **_kwargs: transactions.append("recover"),
    )
    monkeypatch.setattr(
        bank,
        "save",
        lambda *_args, **_kwargs: transactions.append("save"),
    )

    with pytest.raises(RuntimeError, match="deadline|recover"):
        plugin._recover_protected_media(
            bank,
            document,
            profile,
            (800, 480),
            deadline=30.0,
        )

    assert transactions == []
    assert bank.state_path.read_bytes() == state_before
    assert not protected_path.exists()

    clock["value"] = 29.0
    ingested = []
    monkeypatch.setattr(
        bank,
        "ingest",
        lambda *_args, **_kwargs: ingested.append("ingest") or {},
    )
    resolution = {
        "requested_mode": "day_r18",
        "effective_mode": "daily_r18",
        "content_rating": "r18",
        "authenticated": True,
        "healthy_r18": True,
        "source_status": "fresh",
        "cookie": "session-cookie",
        "items": [make_ranking_item(99)],
    }
    plugin._refill_presentation_bank(
        bank,
        profile,
        resolution,
        (800, 480),
        deadline=30.0,
    )
    assert ingested == []


def test_deadline_crossing_inside_recovery_blocks_followup_state_save(
    tmp_path,
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    bank, document, profile, current = warm_bank(tmp_path, count=4)
    protected = next(
        record for record in profile["records"]
        if record["record_key"] == current["record_keys"][0]
    )
    bank.media.path(protected["media_key"], suffix=".png").unlink()
    saved = []
    monkeypatch.setattr(
        plugin,
        "_download_ranking_item_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (240, 420), "purple"),
    )

    def recover(*_args, **_kwargs):
        clock["value"] = 31.0
        return protected

    monkeypatch.setattr(bank, "recover_media", recover)
    monkeypatch.setattr(bank, "save", lambda *_args, **_kwargs: saved.append(True))

    with pytest.raises(RuntimeError, match="deadline"):
        plugin._recover_protected_media(
            bank,
            document,
            profile,
            (800, 480),
            deadline=30.0,
        )

    assert saved == []


def _pixiv_media_snapshot(bank):
    return {
        path.name: path.read_bytes()
        for path in bank.media_dir.iterdir()
        if path.is_file()
    }


def _deadline_crossing_commit_hook(plugin, clock):
    def before_commit():
        clock["value"] = 31.0
        plugin._remaining_data_timeout(30.0, pixiv_mod.MAX_DATA_SECONDS)

    return before_commit


def _deadline_check(plugin, clock):
    return lambda: plugin._remaining_data_timeout(
        30.0,
        pixiv_mod.MAX_DATA_SECONDS,
    )


def test_ingest_internal_deadline_crossing_leaves_media_profile_document_and_state_byte_stable(
    tmp_path,
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    bank, document, profile, _current = warm_bank(tmp_path, count=4)
    state_before = bank.state_path.read_bytes()
    media_before = _pixiv_media_snapshot(bank)
    document_before = deepcopy(document)
    profile_before = deepcopy(profile)

    with pytest.raises(RuntimeError, match="deadline"):
        bank.ingest(
            profile,
            bank_candidate(99),
            Image.new("RGB", (240, 420), "green"),
            before_commit=_deadline_crossing_commit_hook(plugin, clock),
        )

    assert _pixiv_media_snapshot(bank) == media_before
    assert profile == profile_before
    assert document == document_before
    assert bank.state_path.read_bytes() == state_before


def test_recover_internal_deadline_crossing_restores_old_media_and_all_state_bytes(
    tmp_path,
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    bank, document, profile, current = warm_bank(tmp_path, count=4)
    record = next(
        item for item in profile["records"]
        if item["record_key"] == current["record_keys"][0]
    )
    state_before = bank.state_path.read_bytes()
    media_before = _pixiv_media_snapshot(bank)
    document_before = deepcopy(document)
    profile_before = deepcopy(profile)

    with pytest.raises(RuntimeError, match="deadline"):
        bank.recover_media(
            profile,
            record,
            Image.new("RGB", (240, 420), "orange"),
            before_commit=_deadline_crossing_commit_hook(plugin, clock),
        )

    assert _pixiv_media_snapshot(bank) == media_before
    assert profile == profile_before
    assert document == document_before
    assert bank.state_path.read_bytes() == state_before


def test_save_internal_deadline_crossing_never_replaces_old_state_or_mutates_document(
    tmp_path,
    monkeypatch,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    bank, document, profile, _current = warm_bank(tmp_path, count=4)
    profile["source_provenance"] = {
        "content_rating": "r18",
        "source_status": "changed-but-uncommitted",
    }
    state_before = bank.state_path.read_bytes()
    media_before = _pixiv_media_snapshot(bank)
    document_before = deepcopy(document)
    profile_before = deepcopy(profile)

    with pytest.raises(RuntimeError, match="deadline"):
        bank.save(
            document,
            before_commit=_deadline_crossing_commit_hook(plugin, clock),
        )

    assert _pixiv_media_snapshot(bank) == media_before
    assert profile == profile_before
    assert document == document_before
    assert bank.state_path.read_bytes() == state_before
    assert not any(path.name.endswith(".tmp") for path in bank.state_path.parent.iterdir())


@pytest.mark.parametrize("operation", ["ingest", "recover"])
def test_media_actual_publish_crossing_deadline_rolls_back_every_byte(
    tmp_path,
    monkeypatch,
    operation,
):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    bank, document, profile, current = warm_bank(tmp_path, count=4)
    state_before = bank.state_path.read_bytes()
    media_before = _pixiv_media_snapshot(bank)
    profile_before = deepcopy(profile)
    document_before = deepcopy(document)
    original_publish = bank.media.publish_stage

    def publish_then_expire(*args, **kwargs):
        result = original_publish(*args, **kwargs)
        clock["value"] = 31.0
        return result

    monkeypatch.setattr(bank.media, "publish_stage", publish_then_expire)
    check = _deadline_check(plugin, clock)

    with pytest.raises(RuntimeError, match="deadline"):
        if operation == "ingest":
            bank.ingest(
                profile,
                bank_candidate(99),
                Image.new("RGB", (240, 420), "green"),
                before_commit=check,
            )
        else:
            record = next(
                item for item in profile["records"]
                if item["record_key"] == current["record_keys"][0]
            )
            bank.recover_media(
                profile,
                record,
                Image.new("RGB", (240, 420), "orange"),
                before_commit=check,
            )

    assert _pixiv_media_snapshot(bank) == media_before
    assert profile == profile_before
    assert document == document_before
    assert bank.state_path.read_bytes() == state_before


@pytest.mark.parametrize("crossing", ["victim", "profile", "fsync"])
def test_ingest_post_publish_step_crossing_rolls_back_target_victims_and_profile(
    tmp_path,
    monkeypatch,
    crossing,
):
    from plugins.pixiv_r18_ranking import presentation_bank

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    bank, document, profile, _current = warm_bank(tmp_path, count=4)
    if crossing == "profile":
        class ExpiringProfile(dict):
            armed = False

            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                if self.armed and key == "records":
                    clock["value"] = 31.0

        profile = ExpiringProfile(profile)
        document["profiles"][bank.fingerprint] = profile
        bank._loaded_document = document
    elif crossing == "victim":
        victim = bank.media_dir / "unprotected-victim.png"
        victim.write_bytes(b"victim-bytes")
        monkeypatch.setattr(
            presentation_bank,
            "MEDIA_MAX_FILES",
            len(list(bank.media_dir.glob("*.png"))),
        )
        original_unlink = bank._unlink_unprotected_media

        def unlink_then_expire(*args, **kwargs):
            result = original_unlink(*args, **kwargs)
            clock["value"] = 31.0
            return result

        monkeypatch.setattr(bank, "_unlink_unprotected_media", unlink_then_expire)
    else:
        original_fsync = presentation_bank.fsync_directory

        def fsync_then_expire(directory):
            result = original_fsync(directory)
            if Path(directory) == bank.media_dir:
                clock["value"] = 31.0
            return result

        monkeypatch.setattr(presentation_bank, "fsync_directory", fsync_then_expire)

    state_before = bank.state_path.read_bytes()
    media_before = _pixiv_media_snapshot(bank)
    profile_before = deepcopy(profile)
    document_before = deepcopy(document)
    if crossing == "profile":
        profile.armed = True

    with pytest.raises(RuntimeError, match="deadline"):
        bank.ingest(
            profile,
            bank_candidate(99),
            Image.new("RGB", (240, 420), "green"),
            before_commit=_deadline_check(plugin, clock),
        )

    assert _pixiv_media_snapshot(bank) == media_before
    assert dict(profile) == dict(profile_before)
    assert document == document_before
    assert bank.state_path.read_bytes() == state_before


def test_state_actual_replace_crossing_deadline_restores_old_bytes_and_document(
    tmp_path,
    monkeypatch,
):
    from plugins.pixiv_r18_ranking import presentation_bank

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    clock = {"value": 29.0}
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    bank, document, profile, _current = warm_bank(tmp_path, count=4)
    profile["source_provenance"] = {"source_status": "uncommitted"}
    state_before = bank.state_path.read_bytes()
    media_before = _pixiv_media_snapshot(bank)
    document_before = deepcopy(document)
    original_replace = presentation_bank.os.replace

    def replace_then_expire(source, target, *args, **kwargs):
        result = original_replace(source, target, *args, **kwargs)
        target_path = Path(target)
        if target_path.name == bank.state_path.name:
            clock["value"] = 31.0
        return result

    monkeypatch.setattr(presentation_bank.os, "replace", replace_then_expire)

    with pytest.raises(RuntimeError, match="deadline"):
        bank.save(
            document,
            before_commit=_deadline_check(plugin, clock),
        )

    assert bank.state_path.read_bytes() == state_before
    assert _pixiv_media_snapshot(bank) == media_before
    assert document == document_before
    assert not any(path.name.endswith(".tmp") for path in bank.state_path.parent.iterdir())
