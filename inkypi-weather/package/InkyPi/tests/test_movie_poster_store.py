"""Retained movie media survives temporary-cache pressure and ages by use."""
import os

from PIL import Image
import pytest

from plugins.box_office_top_movies.poster_store import PosterStore
from utils.cache_manager import CacheManager


def test_retained_cover_survives_temporary_cache_eviction_and_store_restart(tmp_path):
    temporary = tmp_path / 'cache'
    temporary.mkdir()
    legacy = temporary / 'legacy.jpg'
    with Image.new('RGB', (40, 60), 'red') as image:
        image.save(legacy)
    root = tmp_path / 'data' / 'posters'
    store = PosterStore(root, clock=lambda: 1000)
    path = store.resolve('https://image.example/current.jpg', legacy=legacy)
    assert path and path.is_relative_to(root)
    CacheManager(temporary, global_max_bytes=1)
    assert not legacy.exists()
    restarted = PosterStore(root, clock=lambda: 1001)
    assert restarted.resolve('https://image.example/current.jpg') == path
    with Image.open(path) as restored:
        assert restored.size == (40, 60)


def test_cleanup_preserves_current_and_recently_reused_covers(tmp_path):
    now = [1000.0]
    store = PosterStore(tmp_path, clock=lambda: now[0], max_age_seconds=100, max_files=3)
    urls = ['https://image.example/' + name for name in ('current', 'reused', 'old')]
    with Image.new('RGB', (40, 60), 'red') as image:
        paths = [store.save(url, image, protected_urls=urls) for url in urls]
    now[0] = 1060
    store.resolve(urls[1])
    now[0] = 1110
    store.cleanup(protected_urls=[urls[0]])
    assert paths[0].exists()
    assert paths[1].exists()
    assert not paths[2].exists()


def test_capacity_evicts_oldest_unused_cover_and_rejects_overwriting_protected_budget(tmp_path):
    now = [1000.0]
    store = PosterStore(tmp_path, clock=lambda: now[0], max_files=2)
    with Image.new('RGB', (40, 60), 'red') as image:
        current = store.save('current', image)
        now[0] += 1
        oldest_unused = store.save('unused', image)
        now[0] += 1
        new = store.save('new', image, protected_urls=['current'])
        assert current.exists() and new.exists() and not oldest_unused.exists()
        with pytest.raises(ValueError, match='Active posters'):
            store.save('one-too-many', image, protected_urls=['current', 'new'])
    assert current.exists() and new.exists()
    assert not store.path('one-too-many').exists()


def test_byte_budget_and_read_only_lookup(tmp_path):
    now = [1000.0]
    store = PosterStore(tmp_path, clock=lambda: now[0], max_bytes=1000)
    with Image.new('RGB', (40, 60), 'red') as image:
        old = store.save('old', image)
        now[0] += 1
        new = store.save('new', image)
    assert not old.exists() and new.exists()
    assert sum(p.stat().st_size for p in tmp_path.glob('*.jpg')) <= 1000
    modified = new.stat().st_mtime_ns
    now[0] += 2 * 86400
    assert store.resolve('new', touch=False) == new
    assert new.stat().st_mtime_ns == modified


def test_parent_symlink_cannot_be_read_or_cleaned(tmp_path):
    outside = tmp_path / 'outside'
    store = PosterStore(outside / 'posters', clock=lambda: 1000)
    with Image.new('RGB', (40, 60), 'red') as image:
        original = store.save('current', image)
    link = tmp_path / 'link'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Creating directory symlinks is unavailable')
    alias = PosterStore(link / 'posters', clock=lambda: 1000 + 40 * 86400)
    assert alias.resolve('current') is None
    alias.cleanup()
    assert original.exists()


def test_failed_access_touch_does_not_hide_decodable_cover(tmp_path, monkeypatch):
    now = [1000.0]
    store = PosterStore(tmp_path, clock=lambda: now[0])
    with Image.new('RGB', (40, 60), 'red') as image:
        path = store.save('current', image)
    now[0] += 2 * 86400
    monkeypatch.setattr(os, 'utime', lambda *_: (_ for _ in ()).throw(PermissionError('fixture')))
    assert store.resolve('current') == path
