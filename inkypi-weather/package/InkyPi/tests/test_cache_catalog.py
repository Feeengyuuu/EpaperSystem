import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from src.runtime import cache_catalog as cache_catalog_module
from src.runtime.cache_catalog import (
    CacheCatalog,
    DisplayCacheCandidate,
    authoritative_cache_path,
)
from src.runtime.runtime_state import InstanceRuntimeState, LastGoodCacheState


def _instance(*, generation=2, revision=5):
    return SimpleNamespace(
        instance_uuid="instance-one",
        structural_generation=generation,
        settings_revision=revision,
    )


def _write_png(path, color="white"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path, format="PNG")


def _last_good(*, mode, generation=2, revision=5):
    return LastGoodCacheState(
        theme_mode=mode,
        structural_generation=generation,
        settings_revision=revision,
        promoted_at="2026-07-09T10:00:00+00:00",
    )


def test_current_theme_exact_revision_wins_over_last_good(tmp_path):
    root = tmp_path / ".refresh-cache"
    instance = _instance()
    current = authoritative_cache_path(root, "instance-one", 2, 5, "night")
    previous = authoritative_cache_path(root, "instance-one", 2, 5, "day")
    _write_png(current, "black")
    _write_png(previous, "white")
    state = InstanceRuntimeState(last_good_cache=_last_good(mode="day"))

    candidate = CacheCatalog(root).resolve(instance, "night", state)

    assert candidate is not None
    assert candidate.theme_mode == "night"
    assert candidate.cache_path == current


def test_last_good_same_revision_is_used_when_current_theme_cache_is_missing(
    tmp_path,
):
    root = tmp_path / ".refresh-cache"
    instance = _instance()
    previous = authoritative_cache_path(root, "instance-one", 2, 5, "day")
    _write_png(previous)
    state = InstanceRuntimeState(last_good_cache=_last_good(mode="day"))

    candidate = CacheCatalog(root).resolve(instance, "night", state)

    assert candidate is not None
    assert candidate.theme_mode == "day"
    assert candidate.promoted_at == "2026-07-09T10:00:00+00:00"
    assert candidate.cache_path == previous


def test_exact_theme_resolution_rejects_opposite_last_good_rollback(tmp_path):
    root = tmp_path / ".refresh-cache"
    instance = _instance()
    previous = authoritative_cache_path(root, "instance-one", 2, 5, "day")
    _write_png(previous)
    state = InstanceRuntimeState(last_good_cache=_last_good(mode="day"))
    catalog = CacheCatalog(root)

    assert catalog.resolve(instance, "night", state).theme_mode == "day"
    assert catalog.resolve_exact(instance, "night", state) is None


def test_exact_theme_resolution_rejects_unsuffixed_migration_rollback(tmp_path):
    root = tmp_path / ".refresh-cache"
    instance = _instance()
    migration = authoritative_cache_path(root, "instance-one", 2, 5, None)
    _write_png(migration)
    catalog = CacheCatalog(root)

    assert catalog.resolve(
        instance,
        "night",
        InstanceRuntimeState(),
    ).theme_mode is None
    assert catalog.resolve_exact(
        instance,
        "night",
        InstanceRuntimeState(),
    ) is None


def test_exact_theme_resolution_returns_only_current_exact_revision(tmp_path):
    root = tmp_path / ".refresh-cache"
    instance = _instance()
    current = authoritative_cache_path(root, "instance-one", 2, 5, "night")
    _write_png(current)
    state = InstanceRuntimeState(last_good_cache=_last_good(mode="night"))

    candidate = CacheCatalog(root).resolve_exact(instance, "night", state)

    assert candidate is not None
    assert candidate.theme_mode == "night"
    assert candidate.cache_path == current
    assert candidate.promoted_at == "2026-07-09T10:00:00+00:00"


def test_old_settings_revision_and_name_alias_are_never_displayable(tmp_path):
    root = tmp_path / ".refresh-cache"
    instance = _instance(revision=5)
    old = authoritative_cache_path(root, "instance-one", 2, 4, "night")
    alias = tmp_path / "weather_My_Weather.png"
    _write_png(old)
    _write_png(alias)
    catalog = CacheCatalog(root)

    assert catalog.resolve(instance, "night", InstanceRuntimeState()) is None
    assert catalog.validate(
        DisplayCacheCandidate(
            instance_uuid="instance-one",
            structural_generation=2,
            settings_revision=5,
            theme_mode="night",
            cache_path=str(alias),
            promoted_at=None,
        )
    ) is False


def test_corrupt_png_is_ineligible_and_validation_cache_invalidates_on_stat_change(
    tmp_path,
):
    root = tmp_path / ".refresh-cache"
    path = authoritative_cache_path(root, "instance-one", 2, 5, None)
    _write_png(path)
    candidate = DisplayCacheCandidate(
        instance_uuid="instance-one",
        structural_generation=2,
        settings_revision=5,
        theme_mode=None,
        cache_path=path,
        promoted_at=None,
    )
    catalog = CacheCatalog(root)

    assert catalog.validate(candidate) is True
    Path(path).write_bytes(b"not-a-png-and-a-different-size")
    os.utime(path, None)
    assert catalog.validate(candidate) is False


def test_non_theme_unsuffixed_authoritative_cache_is_displayable(tmp_path):
    root = tmp_path / ".refresh-cache"
    instance = _instance()
    path = authoritative_cache_path(root, "instance-one", 2, 5)
    _write_png(path)

    candidate = CacheCatalog(root).resolve(
        instance,
        None,
        InstanceRuntimeState(),
    )

    assert candidate is not None
    assert candidate.theme_mode is None
    assert candidate.cache_path == path


def test_candidate_path_cannot_escape_refresh_cache_root(tmp_path):
    root = tmp_path / ".refresh-cache"
    root.mkdir()
    outside = tmp_path / "outside.png"
    _write_png(outside)
    candidate = DisplayCacheCandidate(
        instance_uuid="instance-one",
        structural_generation=2,
        settings_revision=5,
        theme_mode=None,
        cache_path=str(root / ".." / outside.name),
        promoted_at=None,
    )
    catalog = CacheCatalog(root)

    assert catalog.validate(candidate) is False

    symlink_path = Path(
        authoritative_cache_path(root, "instance-one", 2, 5, None)
    )
    try:
        symlink_path.symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    linked = DisplayCacheCandidate(
        instance_uuid="instance-one",
        structural_generation=2,
        settings_revision=5,
        theme_mode=None,
        cache_path=str(symlink_path),
        promoted_at=None,
    )
    assert catalog.validate(linked) is False


def test_validation_rejects_symlink_swap_before_pillow_decode(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / ".refresh-cache"
    inside = Path(
        authoritative_cache_path(root, "instance-one", 2, 5, None)
    )
    outside = tmp_path / "outside.png"
    _write_png(inside, "white")
    outside.write_bytes(inside.read_bytes())
    inside_stat = inside.stat()
    os.utime(
        outside,
        ns=(inside_stat.st_atime_ns, inside_stat.st_mtime_ns),
    )
    candidate = DisplayCacheCandidate(
        instance_uuid="instance-one",
        structural_generation=2,
        settings_revision=5,
        theme_mode=None,
        cache_path=str(inside),
        promoted_at=None,
    )
    real_open = cache_catalog_module.Image.open
    swapped = False

    probe = inside.parent / "symlink-probe"
    try:
        probe.symlink_to(Path("..") / outside.name)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable on this platform: {exc}")
    else:
        probe.unlink()

    def swap_before_decode(target, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            inside.unlink()
            inside.symlink_to(Path("..") / outside.name)
            swapped = True
            assert inside.is_symlink()
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr(cache_catalog_module.Image, "open", swap_before_decode)

    assert CacheCatalog(root).validate(candidate) is False
    assert swapped is True


def test_validation_binds_decode_to_opened_file_descriptor(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / ".refresh-cache"
    inside = Path(
        authoritative_cache_path(root, "instance-one", 2, 5, None)
    )
    replacement = tmp_path / "replacement.png"
    _write_png(inside, "white")
    replacement.write_bytes(inside.read_bytes())
    inside_stat = inside.stat()
    os.utime(
        replacement,
        ns=(inside_stat.st_atime_ns, inside_stat.st_mtime_ns),
    )
    candidate = DisplayCacheCandidate(
        instance_uuid="instance-one",
        structural_generation=2,
        settings_revision=5,
        theme_mode=None,
        cache_path=str(inside),
        promoted_at=None,
    )
    real_image_open = cache_catalog_module.Image.open
    real_os_open = cache_catalog_module.os.open
    real_fstat = cache_catalog_module.os.fstat
    open_calls = []
    fstat_calls = []
    swapped = False

    def observed_os_open(*args, **kwargs):
        open_calls.append((args, kwargs))
        return real_os_open(*args, **kwargs)

    def observed_fstat(fd):
        fstat_calls.append(fd)
        return real_fstat(fd)

    def replace_before_decode(target, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            os.replace(replacement, inside)
            swapped = True
        return real_image_open(target, *args, **kwargs)

    monkeypatch.setattr(cache_catalog_module.os, "open", observed_os_open)
    monkeypatch.setattr(cache_catalog_module.os, "fstat", observed_fstat)
    monkeypatch.setattr(
        cache_catalog_module.Image,
        "open",
        replace_before_decode,
    )

    result = CacheCatalog(root).validate(candidate)

    assert result is False
    assert open_calls
    assert len(fstat_calls) >= 2
    if os.name != "nt":
        assert swapped is True


def test_load_image_returns_a_copy_from_the_validated_bound_descriptor(tmp_path):
    root = tmp_path / ".refresh-cache"
    path = authoritative_cache_path(root, "instance-one", 2, 5, None)
    _write_png(path, "red")
    candidate = DisplayCacheCandidate(
        instance_uuid="instance-one",
        structural_generation=2,
        settings_revision=5,
        theme_mode=None,
        cache_path=path,
        promoted_at=None,
    )

    loader = getattr(CacheCatalog(root), "load_image", None)

    assert callable(loader)
    image = loader(candidate)
    assert image is not None
    try:
        assert image.getpixel((0, 0)) == (255, 0, 0)
    finally:
        image.close()
