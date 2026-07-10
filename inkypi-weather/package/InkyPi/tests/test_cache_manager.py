import os
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.cache_manager import (
    CacheBudget,
    CacheManager,
    CacheObjectTooLarge,
    CachePathError,
    ImageLRUCache,
    _ImageCachePool,
)


class Clock:
    def __init__(self, value=1_000_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class Publisher:
    def __init__(self):
        self.components = []

    def publish_component(self, name, value):
        self.components.append((name, value))


def test_namespace_prunes_lru_before_write_and_stays_under_budget(tmp_path):
    manager = CacheManager(tmp_path / "managed")
    namespace = manager.namespace("ticketmaster", CacheBudget(3600, 2, 10))

    namespace.put_bytes("one", b"12345", suffix=".jpg")
    namespace.put_bytes("two", b"12345", suffix=".jpg")
    namespace.put_bytes("three", b"12345", suffix=".jpg")

    status = namespace.status()
    assert status.files <= 2
    assert status.bytes <= 10
    assert not namespace.path("one", ".jpg").exists()
    assert namespace.path("two", ".jpg").exists()
    assert namespace.path("three", ".jpg").exists()


def test_read_updates_lru_so_recent_object_survives_next_write(tmp_path):
    clock = Clock()
    namespace = CacheManager(tmp_path / "managed", clock=clock).namespace(
        "previews",
        CacheBudget(3600, 2, 10),
    )
    namespace.put_bytes("one", b"11111")
    clock.advance(1)
    namespace.put_bytes("two", b"22222")
    clock.advance(1)

    assert namespace.get_bytes("one") == b"11111"
    clock.advance(1)
    namespace.put_bytes("three", b"33333")

    assert namespace.path("one").exists()
    assert not namespace.path("two").exists()
    assert namespace.path("three").exists()


def test_namespace_rejects_traversal_absolute_paths_and_unsafe_suffixes(tmp_path):
    namespace = CacheManager(tmp_path / "managed").namespace(
        "sports",
        CacheBudget(3600, 10, 1000),
    )

    for key, suffix in [
        ("../secret", ".png"),
        (str(tmp_path / "absolute"), ".png"),
        ("safe", "/escape.png"),
        ("safe", "../escape"),
    ]:
        with pytest.raises(CachePathError):
            namespace.path(key, suffix)

    with pytest.raises(CachePathError):
        CacheManager(tmp_path / "managed").namespace(
            "../outside",
            CacheBudget(3600, 10, 1000),
        )


def test_namespace_rejects_symlink_escape(tmp_path):
    namespace = CacheManager(tmp_path / "managed").namespace(
        "sports",
        CacheBudget(3600, 10, 1000),
    )
    escape = namespace.root / "escape"
    try:
        os.symlink(tmp_path.parent, escape, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(CachePathError):
        namespace.path("escape/secret", ".png")
    with pytest.raises(CachePathError):
        namespace.put_bytes("escape/secret", b"blocked", suffix=".png")


def test_namespace_rejects_detected_symlink_component_without_os_privileges(
    tmp_path,
    monkeypatch,
):
    namespace = CacheManager(tmp_path / "managed").namespace(
        "sports",
        CacheBudget(3600, 10, 1000),
    )
    escape = namespace.root / "escape"
    escape.mkdir()
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == escape or original(path),
    )

    with pytest.raises(CachePathError):
        namespace.path("escape/secret", ".png")


def test_oversize_object_is_rejected_without_removing_existing_value(tmp_path):
    namespace = CacheManager(tmp_path / "managed").namespace(
        "sports",
        CacheBudget(3600, 10, 5),
    )
    namespace.put_bytes("logo", b"old")

    with pytest.raises(CacheObjectTooLarge):
        namespace.put_bytes("logo", b"123456")

    assert namespace.get_bytes("logo") == b"old"
    assert list(namespace.root.rglob("*.tmp")) == []


def test_failed_atomic_replace_keeps_old_value_and_removes_temp(tmp_path, monkeypatch):
    from utils import cache_manager as module

    namespace = CacheManager(tmp_path / "managed").namespace(
        "atomic",
        CacheBudget(3600, 10, 100),
    )
    namespace.put_bytes("state", b"old")

    monkeypatch.setattr(
        module.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        namespace.put_bytes("state", b"new")

    assert namespace.get_bytes("state") == b"old"
    assert list(namespace.root.rglob("*.tmp")) == []


def test_startup_and_daily_maintenance_remove_only_old_managed_temp_files(tmp_path):
    clock = Clock()
    manager = CacheManager(tmp_path / "managed", clock=clock)
    namespace = manager.namespace("temp", CacheBudget(10_000, 10, 1000))
    old = namespace.root / "abandoned.tmp"
    fresh = namespace.root / "active.tmp"
    outside = tmp_path / "outside.tmp"
    for path in (old, fresh, outside):
        path.write_bytes(b"x")
    os.utime(old, (clock() - 3601, clock() - 3601))
    os.utime(fresh, (clock(), clock()))
    os.utime(outside, (clock() - 7200, clock() - 7200))

    manager.maintenance(force=True)

    assert not old.exists()
    assert fresh.exists()
    assert outside.exists()


def test_age_budget_removes_expired_objects(tmp_path):
    clock = Clock()
    namespace = CacheManager(tmp_path / "managed", clock=clock).namespace(
        "aged",
        CacheBudget(10, 10, 1000),
    )
    namespace.put_bytes("old", b"value")
    clock.advance(11)

    namespace.maintenance()

    assert namespace.get_bytes("old") is None
    assert namespace.status().files == 0


def test_global_budget_evicts_oldest_across_namespaces(tmp_path):
    clock = Clock()
    manager = CacheManager(
        tmp_path / "managed",
        global_max_bytes=10,
        clock=clock,
    )
    first = manager.namespace("first", CacheBudget(3600, 10, 100))
    second = manager.namespace("second", CacheBudget(3600, 10, 100))
    first.put_bytes("one", b"111111")
    clock.advance(1)

    second.put_bytes("two", b"222222")

    assert first.get_bytes("one") is None
    assert second.get_bytes("two") == b"222222"
    assert manager.status().bytes <= 10


def test_runtime_paths_root_and_health_publisher_are_supported(tmp_path):
    publisher = Publisher()
    runtime_paths = SimpleNamespace(cache_dir=tmp_path / "runtime-cache")
    manager = CacheManager(runtime_paths, health_publisher=publisher)
    namespace = manager.namespace("plugin/images", CacheBudget(3600, 10, 100))

    namespace.put_bytes("one", b"123")

    assert namespace.root == tmp_path / "runtime-cache" / "plugins" / "plugin" / "images"
    assert publisher.components
    name, value = publisher.components[-1]
    assert name == "cache"
    assert value["bytes"] == 3
    assert value["global_max_bytes"] > value["bytes"]


def test_image_lru_cache_enforces_entry_and_byte_limits():
    pool = _ImageCachePool(max_bytes=1000)
    cache = ImageLRUCache(max_entries=2, max_bytes=24, pool=pool)
    first = Image.new("RGB", (2, 2), "red")
    second = Image.new("RGB", (2, 2), "green")
    third = Image.new("RGB", (2, 2), "blue")

    cache["first"] = first
    cache["second"] = second
    assert cache["first"] is first
    cache["third"] = third

    assert "first" in cache
    assert "second" not in cache
    assert "third" in cache
    assert len(cache) == 2
    assert cache.bytes <= 24


def test_image_lru_cache_global_pool_evicts_across_individual_caches():
    pool = _ImageCachePool(max_bytes=18)
    first = ImageLRUCache(max_entries=10, max_bytes=100, pool=pool)
    second = ImageLRUCache(max_entries=10, max_bytes=100, pool=pool)
    first["old"] = Image.new("RGB", (2, 2), "red")
    second["new"] = Image.new("RGB", (2, 2), "blue")

    assert "old" not in first
    assert "new" in second
    assert pool.bytes <= 18


def test_image_lru_negative_cache_stays_bounded_after_one_thousand_keys():
    cache = ImageLRUCache(
        max_entries=128,
        max_bytes=20 * 1024 * 1024,
        pool=_ImageCachePool(max_bytes=32 * 1024 * 1024),
    )

    for index in range(1000):
        cache[f"missing-{index}"] = None

    assert len(cache) == 128
    assert cache.bytes == 0
    cache.clear()
    assert len(cache) == 0


def test_disk_namespace_stays_bounded_after_one_thousand_keys(tmp_path):
    namespace = CacheManager(tmp_path / "managed").namespace(
        "stress",
        CacheBudget(3600, 256, 1024),
    )

    for index in range(1000):
        namespace.put_bytes(f"item-{index:04d}", b"x")

    status = namespace.status()
    assert status.files == 256
    assert status.bytes == 256
