import json
import os
from io import BytesIO
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.refresh_contracts import TaskCancelled
from utils import resource_cache as cache


def picture(color="red"):
    return Image.new("RGBA", (12, 8), color)


def seed(path, stamp=100):
    path.parent.mkdir(parents=True, exist_ok=True)
    picture().save(path)
    os.utime(path, (stamp, stamp))
    return path


def test_disk_hit_survives_new_call_and_preserves_freshness_clock(tmp_path, monkeypatch):
    now = [100_000.0]
    monkeypatch.setattr(cache.time, "time", lambda: now[0])
    path = tmp_path / "image.png"
    calls = []

    def fetch():
        calls.append(1)
        return picture()

    cache.cached_resource_image(path, fetch, ttl=3 * 86400, label="test").close()
    first_mtime = path.stat().st_mtime
    now[0] += 86401
    cache.cached_resource_image(path, fetch, ttl=3 * 86400, label="test").close()
    assert calls == [1]
    assert path.stat().st_mtime == first_mtime
    assert path.stat().st_atime == now[0]


def test_expired_media_survives_failure_and_retry_backoff(tmp_path, monkeypatch):
    path = seed(tmp_path / "image.png")
    now = [1000.0]
    monkeypatch.setattr(cache.time, "time", lambda: now[0])
    calls = []

    def fail():
        calls.append(1)
        raise RuntimeError("upstream timeout")

    for _ in range(2):
        image = cache.cached_resource_image(path, fail, ttl=60, label="test")
        assert image.getpixel((0, 0)) == (255, 0, 0, 255)
        assert image.info["resource_cache_state"] == "stale"
        image.close()
    assert len(calls) == 1
    assert path.stat().st_mtime == 100
    now[0] += 301
    image = cache.cached_resource_image(path, lambda: picture("blue"), ttl=60, label="test")
    assert image.getpixel((0, 0)) == (0, 0, 255, 255)
    image.close()
    assert not path.with_suffix(".retry.json").exists()


def test_dynamic_snapshot_has_finite_stale_limit(tmp_path, monkeypatch):
    path = seed(tmp_path / "image.png")
    monkeypatch.setattr(cache.time, "time", lambda: 1000)
    result = cache.cached_resource_image(path, lambda: None, ttl=60, label="live", max_stale=600)
    assert result is None
    assert path.exists()


def test_readonly_miss_does_not_create_directories_or_call_provider(tmp_path):
    path = tmp_path / "missing" / "image.png"
    def fail():
        pytest.fail("readonly cache fetched a provider")
    assert cache.cached_resource_image(path, fail, ttl=1, label="test", read_only=True) is None
    assert not path.parent.exists()


def test_cancellation_does_not_become_media_fallback_or_backoff(tmp_path, monkeypatch):
    path = seed(tmp_path / "image.png")
    monkeypatch.setattr(cache.time, "time", lambda: 1000)
    def cancel():
        raise TaskCancelled("cancel")
    with pytest.raises(TaskCancelled):
        cache.cached_resource_image(path, cancel, ttl=1, label="test")
    assert not path.with_suffix(".retry.json").exists()
    assert path.stat().st_mtime == 100


def test_failed_atomic_publish_preserves_previous_image(tmp_path, monkeypatch):
    path = seed(tmp_path / "image.png")
    original = path.read_bytes()
    monkeypatch.setattr(cache.time, "time", lambda: 1000)
    def fail_replace(*args):
        raise OSError("disk full")
    monkeypatch.setattr(cache.os, "replace", fail_replace)
    image = cache.cached_resource_image(path, lambda: picture("blue"), ttl=1, label="test")
    image.close()
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_cache_is_replaced_only_after_success(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"broken png")
    image = cache.cached_resource_image(path, picture, ttl=1000, label="test")
    assert image.size == (12, 8)
    image.close()
    with Image.open(path) as stored:
        stored.verify()


def test_cmyk_source_is_normalized_and_reused_as_png(tmp_path):
    path = tmp_path / "image.png"
    image = cache.cached_resource_image(path, lambda: Image.new("CMYK", (12, 8)),
                                        ttl=1000, label="test")
    assert image.mode == "RGB"
    image.close()
    def fail():
        pytest.fail("normalized image was not cached")
    cached = cache.cached_resource_image(path, fail, ttl=1000, label="test")
    assert cached.mode == "RGB"
    cached.close()


def test_not_found_backoff_is_persistent_and_expires(tmp_path, monkeypatch):
    path = tmp_path / "logo.png"
    now = [1000.0]
    monkeypatch.setattr(cache.time, "time", lambda: now[0])
    calls = []
    def fail():
        calls.append(1)
        raise RuntimeError("media request failed with status 404")
    assert cache.cached_resource_image(path, fail, ttl=1, label="test") is None
    assert cache.cached_resource_image(path, fail, ttl=1, label="test") is None
    assert len(calls) == 1
    assert json.loads(path.with_suffix(".retry.json").read_text())["retry_after"] == 22600
    now[0] = 22601
    assert cache.cached_resource_image(path, fail, ttl=1, label="test") is None
    assert len(calls) == 2


def test_cleanup_preserves_active_media_and_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cache.time, "time", lambda: 2000)
    files = [seed(tmp_path / f"cover_{i:024x}.png", 100 + i) for i in range(6)]
    unknown = seed(tmp_path / "user-photo.png")
    state = tmp_path / "state.json"
    state.write_text('{"current": "important"}')
    avatar = seed(tmp_path / ("avatar_" + "f" * 24 + ".png"))
    result = cache.prune_resource_images(tmp_path, prefixes=("cover_",), max_files=2,
        max_bytes=1_000_000, max_age=86400, label="test", protected=[files[0]])
    assert result["files"] == 2
    assert files[0].exists() and files[-1].exists()
    assert unknown.exists() and avatar.exists() and state.exists()
    assert result["evicted_files"] == 4


def test_cleanup_byte_budget_and_expired_retry_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cache.time, "time", lambda: 2000)
    files = [seed(tmp_path / f"cover_{i:024x}.png", 100 + i) for i in range(3)]
    retry = tmp_path / ("cover_" + "a" * 24 + ".retry.json")
    retry.write_text('{"retry_after": 1000}')
    result = cache.prune_resource_images(tmp_path, prefixes=("cover_",), max_files=20,
        max_bytes=files[0].stat().st_size, max_age=86400, label="test")
    assert result["files"] == 1
    assert files[-1].exists() and not retry.exists()


def test_cleanup_reports_protected_over_budget_without_removing_it(tmp_path, monkeypatch):
    monkeypatch.setattr(cache.time, "time", lambda: 2000)
    path = seed(tmp_path / ("cover_" + "a" * 24 + ".png"))
    with cache.resource_cache_metrics("test"):
        result = cache.prune_resource_images(tmp_path, prefixes=("cover_",), max_files=1,
            max_bytes=1, max_age=1, label="test", protected=[path])
        assert cache._METRICS.get()["test"]["protected_over_budget"] == 1
    assert path.exists() and result["files"] == 1


def test_metrics_distinguish_actual_network_bytes_and_disk_hits(tmp_path):
    path = tmp_path / "image.png"
    def fetch():
        image = picture()
        image.info["resource_downloaded_bytes"] = 123
        return image
    with cache.resource_cache_metrics("test"):
        cache.cached_resource_image(path, fetch, ttl=1000, label="media").close()
        cache.cached_resource_image(path, fetch, ttl=1000, label="media").close()
        counts = cache._METRICS.get()["media"]
        assert counts["downloads"] == 1 and counts["disk_hits"] == 1
        assert counts["downloaded_bytes"] == 123


def test_steam_capsules_survive_plugin_recreation_and_keep_query_identity(tmp_path, monkeypatch):
    from plugins.steam_charts import steam_charts as module
    payload = BytesIO()
    picture().save(payload, format="PNG")
    calls = []
    class Client:
        def request_bytes(self, method, url, **kwargs):
            calls.append(url)
            return type("Response", (), {"data": payload.getvalue()})()
    monkeypatch.setattr(module, "get_http_client", lambda: Client())
    def plugin():
        p = module.SteamCharts({"id": "steam_charts"})
        p.cache_dir = lambda **kwargs: tmp_path
        return p
    first = plugin()._image_url_to_data_uri("https://example.com/image?v=1")
    second = plugin()._image_url_to_data_uri("https://example.com/image?v=1")
    plugin()._image_url_to_data_uri("https://example.com/image?v=2")
    assert first == second and first.startswith("data:image/png;base64,")
    assert len(calls) == 2


def test_live_cleanup_protects_current_room_urls(tmp_path):
    from plugins.live_radar.live_radar import LiveRadar
    plugin = LiveRadar({"id": "live_radar"})
    plugin._cache_dir = lambda: tmp_path
    url = "https://example.com/active.jpg"
    current = seed(plugin._cover_cache_path(url), 1)
    for i in range(105):
        seed(tmp_path / f"cover_{i:024x}.png", 100 + i)
    plugin._maintain_resource_images([{"cover": url}])
    assert current.exists()
    assert len(list(tmp_path.glob("cover_*.png"))) <= 96
