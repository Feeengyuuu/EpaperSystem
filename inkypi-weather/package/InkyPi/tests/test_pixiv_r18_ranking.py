import json
import random
import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.pixiv_r18_ranking.pixiv_r18_ranking import PixivR18Ranking  # noqa: E402


TEST_TMP_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "pixiv_r18_ranking_tests"


class DummyDeviceConfig:
    def __init__(self, resolution=(800, 480), token="refresh-token"):
        self.resolution = resolution
        self.token = token

    def get_resolution(self):
        return self.resolution

    def get_config(self, _key, default=None):
        return default

    def load_env_key(self, key):
        if key == "PIXIV_REFRESH_TOKEN":
            return self.token
        return ""


class RecordingLoader:
    def __init__(self):
        self.paths = []

    def from_file(self, path, dimensions, resize=True, focus_crop=False):
        self.paths.append(Path(path))
        with Image.open(path) as image:
            return image.copy().convert("RGB")


def make_test_tmp_dir(name):
    path = TEST_TMP_ROOT / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_tag(name, translated_name=None):
    return SimpleNamespace(name=name, translated_name=translated_name)


def make_illust(illust_id, *, title="Title", tags=None, x_restrict=1, image_url=None, kind="illust"):
    image_url = image_url or f"https://i.pximg.net/img-original/img/2026/06/16/{illust_id}_p0.jpg"
    return SimpleNamespace(
        id=illust_id,
        title=title,
        type=kind,
        x_restrict=x_restrict,
        user=SimpleNamespace(name="Artist"),
        tags=tags or [make_tag("R-18")],
        meta_pages=[],
        meta_single_page=SimpleNamespace(original_image_url=image_url),
        image_urls=SimpleNamespace(large=image_url),
    )


def test_fetch_ranking_uses_pixivpy_refresh_token_and_r18_mode(monkeypatch):
    calls = []
    ranking = [make_illust(101)]

    class FakeAppPixivAPI:
        def auth(self, refresh_token):
            calls.append(("auth", refresh_token))

        def illust_ranking(self, mode):
            calls.append(("ranking", mode))
            return SimpleNamespace(illusts=ranking)

    monkeypatch.setitem(sys.modules, "pixivpy3", types.SimpleNamespace(AppPixivAPI=FakeAppPixivAPI))

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})

    assert plugin._fetch_ranking("secret-refresh", "day_r18") == ranking
    assert calls == [("auth", "secret-refresh"), ("ranking", "day_r18")]


def test_safety_filter_excludes_r18g_guro_and_minor_risk_tags():
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})

    assert plugin._is_safe_ranking_item(make_illust(1, tags=[make_tag("R-18")])) is True
    assert plugin._is_safe_ranking_item(make_illust(2, x_restrict=2)) is False
    assert plugin._is_safe_ranking_item(make_illust(3, tags=[make_tag("R-18G")])) is False
    assert plugin._is_safe_ranking_item(make_illust(4, tags=[make_tag("guro")])) is False
    assert plugin._is_safe_ranking_item(make_illust(5, tags=[make_tag("R-18", "\u30ed\u30ea")])) is False
    assert plugin._is_safe_ranking_item(make_illust(6, kind="ugoira")) is False


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
    source_image = cache_dir / "source.jpg"
    Image.new("RGB", (300, 500), "purple").save(source_image)

    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    plugin.image_loader = RecordingLoader()
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(cache_dir))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(plugin, "_download_to_temp", lambda _url: source_image)

    ranking = [
        make_illust(1, tags=[make_tag("R-18G")]),
        *[make_illust(index, title=f"Safe {index}") for index in range(2, 30)],
    ]
    monkeypatch.setattr(plugin, "_fetch_ranking", lambda token, mode: ranking)

    pool = plugin._refresh_daily_pool({"rankingMode": "day_r18", "poolSize": "20"}, DummyDeviceConfig(), (800, 480))

    assert len(pool) == 20
    assert pool[0]["illust_id"] == "2"
    assert pool[-1]["illust_id"] == "21"
    assert all(Path(item["image_path"]).is_file() for item in pool)


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


def test_missing_filtered_pool_renders_safe_placeholder(monkeypatch):
    plugin = PixivR18Ranking({"id": "pixiv_r18_ranking"})
    monkeypatch.setenv("INKYPI_PIXIV_R18_CACHE", str(make_test_tmp_dir("empty")))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 16, 16, 0, tzinfo=timezone.utc))
    calls = []

    def fake_fetch(token, mode):
        calls.append((token, mode))
        return [make_illust(1, tags=[make_tag("R-18G")])]

    monkeypatch.setattr(plugin, "_fetch_ranking", fake_fetch)

    image = plugin.generate_image({"rankingMode": "day_r18", "poolSize": "20"}, DummyDeviceConfig())
    second_image = plugin.generate_image({"rankingMode": "day_r18", "poolSize": "20"}, DummyDeviceConfig())
    pool_payload = json.loads(plugin._daily_pool_path().read_text(encoding="utf-8"))

    assert image.size == (800, 480)
    assert second_image.size == (800, 480)
    assert pool_payload["items"] == []
    assert calls == [("refresh-token", "day_r18")]
