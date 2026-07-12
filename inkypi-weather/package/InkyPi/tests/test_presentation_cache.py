from dataclasses import replace
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace

from PIL import Image
import pytest

from src.runtime import presentation_cache as presentation_cache_module
from src.runtime.presentation_cache import (
    PreparedPresentationCandidate,
    PresentationCache,
    prepared_presentation_path,
)


INSTANCE_UUID = "123e4567e89b12d3a456426614174000"
REQUEST_ID = "0123456789abcdef0123456789abcdef"
OTHER_REQUEST_ID = "fedcba9876543210fedcba9876543210"
OTHER_INSTANCE_UUID = "223e4567e89b12d3a456426614174000"


def _candidate(
    root,
    *,
    instance_uuid=INSTANCE_UUID,
    generation=2,
    revision=5,
    theme="night",
    request_id=REQUEST_ID,
    cache_path=None,
):
    if cache_path is None:
        cache_path = prepared_presentation_path(
            root,
            instance_uuid,
            generation,
            revision,
            theme,
            request_id,
        )
    return PreparedPresentationCandidate(
        instance_uuid=instance_uuid,
        structural_generation=generation,
        settings_revision=revision,
        theme_mode=theme,
        request_id=request_id,
        cache_path=cache_path,
    )


def _write_png(path, color="white", size=(8, 8)):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, color) as image:
        image.save(path, format="PNG")


def _clone_stat(value, **changes):
    fields = {
        name: getattr(value, name)
        for name in (
            "st_mode",
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
        )
    }
    fields["st_file_attributes"] = getattr(value, "st_file_attributes", 0)
    fields.update(changes)
    return SimpleNamespace(**fields)


def _symlink_or_skip(link, target, *, target_is_directory=False):
    try:
        Path(link).symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")


def test_prepared_path_is_a_deterministic_direct_child(tmp_path):
    root = tmp_path / ".refresh-presentation"

    path = Path(_candidate(root).cache_path)

    digest = hashlib.sha256(INSTANCE_UUID.encode("utf-8")).hexdigest()
    assert path.parent == root
    assert path.name == f"{digest}-2-5-night-{REQUEST_ID}.png"


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("instance_uuid", "not-a-uuid", ValueError),
        ("instance_uuid", f" {INSTANCE_UUID}", ValueError),
        ("instance_uuid", 123, TypeError),
        ("generation", 0, ValueError),
        ("generation", -1, ValueError),
        ("generation", True, TypeError),
        ("revision", 0, ValueError),
        ("revision", "5", TypeError),
        ("theme", "dusk", ValueError),
        ("request_id", "a" * 31, ValueError),
        ("request_id", "A" * 32, ValueError),
        ("request_id", 123, TypeError),
    ],
)
def test_prepared_path_rejects_invalid_identity_fields(
    tmp_path,
    field,
    value,
    error_type,
):
    values = {
        "instance_uuid": INSTANCE_UUID,
        "generation": 2,
        "revision": 5,
        "theme": "night",
        "request_id": REQUEST_ID,
    }
    values[field] = value

    with pytest.raises(error_type):
        prepared_presentation_path(
            tmp_path / ".refresh-presentation",
            values["instance_uuid"],
            values["generation"],
            values["revision"],
            values["theme"],
            values["request_id"],
        )


def test_save_validate_and_load_use_the_authoritative_candidate(tmp_path):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    cache = PresentationCache(root)

    with Image.new("RGB", (8, 8), "red") as source:
        cache.save(candidate, source)

    assert cache.validate(candidate) is True
    loaded = cache.load_image(candidate)
    assert loaded is not None
    try:
        assert loaded.getpixel((0, 0)) == (255, 0, 0)
    finally:
        loaded.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"instance_uuid": OTHER_INSTANCE_UUID},
        {"structural_generation": 0},
        {"settings_revision": 0},
        {"theme_mode": "dusk"},
        {"request_id": "g" * 32},
    ],
)
def test_forged_candidate_fields_fail_closed_without_touching_file(
    tmp_path,
    changes,
):
    root = tmp_path / ".refresh-presentation"
    valid = _candidate(root)
    _write_png(valid.cache_path)
    forged = replace(valid, **changes)
    cache = PresentationCache(root)

    assert cache.validate(forged) is False
    assert cache.load_image(forged) is None
    assert cache.remove(forged) is False
    with Image.new("RGB", (8, 8), "black") as image:
        with pytest.raises((TypeError, ValueError)):
            cache.save(forged, image)
    assert Path(valid.cache_path).exists()


def test_traversal_and_forged_cache_paths_fail_closed(tmp_path):
    root = tmp_path / ".refresh-presentation"
    root.mkdir()
    valid = _candidate(root)
    outside = tmp_path / Path(valid.cache_path).name
    _write_png(outside)
    traversal = replace(valid, cache_path=str(root / ".." / outside.name))
    forged = replace(valid, cache_path=str(outside))
    cache = PresentationCache(root)

    for candidate in (traversal, forged):
        assert cache.validate(candidate) is False
        assert cache.load_image(candidate) is None
        assert cache.remove(candidate) is False
    assert outside.exists()


def test_direct_symlink_is_rejected_by_every_operation(tmp_path):
    root = tmp_path / ".refresh-presentation"
    root.mkdir()
    candidate = _candidate(root)
    outside = tmp_path / "outside.png"
    _write_png(outside)
    _symlink_or_skip(candidate.cache_path, outside)
    cache = PresentationCache(root)

    assert cache.validate(candidate) is False
    assert cache.load_image(candidate) is None
    assert cache.remove(candidate) is False
    with Image.new("RGB", (8, 8), "red") as image:
        with pytest.raises(OSError):
            cache.save(candidate, image)
    assert outside.exists()


def test_root_symlink_is_rejected_by_every_operation(tmp_path):
    real_root = tmp_path / "real-presentation"
    real_root.mkdir()
    linked_root = tmp_path / ".refresh-presentation"
    _symlink_or_skip(linked_root, real_root, target_is_directory=True)
    candidate = _candidate(linked_root)
    _write_png(real_root / Path(candidate.cache_path).name)
    cache = PresentationCache(linked_root)

    assert cache.validate(candidate) is False
    assert cache.load_image(candidate) is None
    assert cache.remove(candidate) is False
    with Image.new("RGB", (8, 8), "red") as image:
        with pytest.raises(OSError):
            cache.save(candidate, image)


@pytest.mark.parametrize("kind", ["corrupt", "jpeg"])
def test_corrupt_and_non_png_files_are_rejected(tmp_path, kind):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    path = Path(candidate.cache_path)
    path.parent.mkdir()
    if kind == "corrupt":
        path.write_bytes(b"not-a-png")
    else:
        with Image.new("RGB", (8, 8), "white") as image:
            image.save(path, format="JPEG")

    cache = PresentationCache(root)

    assert cache.validate(candidate) is False
    assert cache.load_image(candidate) is None


def test_png_over_dimension_and_pixel_limits_is_rejected(tmp_path):
    root = tmp_path / ".refresh-presentation"
    dimension_candidate = _candidate(root)
    _write_png(dimension_candidate.cache_path, size=(8193, 1))
    pixel_candidate = _candidate(root, request_id=OTHER_REQUEST_ID)
    _write_png(pixel_candidate.cache_path, size=(4096, 2000))
    cache = PresentationCache(root)

    assert cache.validate(dimension_candidate) is False
    assert cache.validate(pixel_candidate) is False


def test_png_over_byte_limit_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    _write_png(candidate.cache_path)
    size = Path(candidate.cache_path).stat().st_size
    monkeypatch.setattr(
        presentation_cache_module,
        "MAX_PRESENTATION_FILE_BYTES",
        size - 1,
    )

    assert PresentationCache(root).validate(candidate) is False


def test_expired_file_is_not_valid_or_loadable_but_can_be_removed(tmp_path):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    _write_png(candidate.cache_path)
    expired = 25 * 60 * 60
    old = Path(candidate.cache_path).stat().st_mtime - expired
    os.utime(candidate.cache_path, (old, old))
    cache = PresentationCache(root)

    assert cache.validate(candidate) is False
    assert cache.load_image(candidate) is None
    assert cache.remove(candidate) is True
    assert not Path(candidate.cache_path).exists()


def test_load_revalidates_after_a_prior_successful_validation(tmp_path):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    _write_png(candidate.cache_path)
    cache = PresentationCache(root)
    assert cache.validate(candidate) is True
    Path(candidate.cache_path).write_bytes(b"replacement-is-corrupt")

    assert cache.load_image(candidate) is None


@pytest.mark.skipif(os.name == "nt", reason="Windows does not permit replacing this open descriptor")
def test_load_rejects_path_replacement_during_decode(tmp_path, monkeypatch):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    path = Path(candidate.cache_path)
    replacement = tmp_path / "replacement.png"
    _write_png(path, "red")
    _write_png(replacement, "blue")
    original_stat = path.stat()
    os.utime(
        replacement,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    real_image_open = presentation_cache_module.Image.open
    replaced = False

    def replace_during_decode(source, *args, **kwargs):
        nonlocal replaced
        if not replaced:
            os.replace(replacement, path)
            replaced = True
        return real_image_open(source, *args, **kwargs)

    monkeypatch.setattr(
        presentation_cache_module.Image,
        "open",
        replace_during_decode,
    )

    assert PresentationCache(root).load_image(candidate) is None
    assert replaced is True


def test_load_decodes_from_a_bound_stream_without_reopening_the_path(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    _write_png(candidate.cache_path, "red")
    real_image_open = presentation_cache_module.Image.open
    opened_sources = []

    def observe_image_open(source, *args, **kwargs):
        opened_sources.append(source)
        assert not isinstance(source, (str, os.PathLike))
        return real_image_open(source, *args, **kwargs)

    monkeypatch.setattr(
        presentation_cache_module.Image,
        "open",
        observe_image_open,
    )

    loaded = PresentationCache(root).load_image(candidate)

    assert loaded is not None
    loaded.close()
    assert len(opened_sources) == 2


def test_save_is_atomic_private_and_failure_preserves_old_file(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    cache = PresentationCache(root)
    with Image.new("RGB", (8, 8), "red") as image:
        cache.save(candidate, image)
    path = Path(candidate.cache_path)
    original = path.read_bytes()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    real_replace = presentation_cache_module.os.replace

    def fail_publish(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(presentation_cache_module.os, "replace", fail_publish)
    with Image.new("RGB", (8, 8), "blue") as image:
        with pytest.raises(OSError):
            cache.save(candidate, image)
    monkeypatch.setattr(presentation_cache_module.os, "replace", real_replace)

    assert path.read_bytes() == original
    assert not list(root.glob(f".{path.name}.*.tmp"))


def test_save_refuses_a_third_file_for_one_instance(tmp_path):
    root = tmp_path / ".refresh-presentation"
    cache = PresentationCache(root)
    candidates = [_candidate(root, request_id=f"{index:032x}") for index in (1, 2, 3)]
    with Image.new("RGB", (8, 8), "red") as image:
        cache.save(candidates[0], image)
        cache.save(candidates[1], image)
        with pytest.raises(OSError):
            cache.save(candidates[2], image)

    assert Path(candidates[0].cache_path).exists()
    assert Path(candidates[1].cache_path).exists()
    assert not Path(candidates[2].cache_path).exists()


def test_save_refuses_global_file_and_byte_budget_overflow(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / ".refresh-presentation"
    cache = PresentationCache(root)
    first = _candidate(root)
    second = _candidate(
        root,
        instance_uuid=OTHER_INSTANCE_UUID,
        request_id=OTHER_REQUEST_ID,
    )
    third = _candidate(
        root,
        instance_uuid="323e4567e89b12d3a456426614174000",
        request_id="a" * 32,
    )
    with Image.new("RGB", (8, 8), "red") as image:
        cache.save(first, image)
        first_size = Path(first.cache_path).stat().st_size
        monkeypatch.setattr(presentation_cache_module, "MAX_PRESENTATION_FILES", 1)
        with pytest.raises(OSError):
            cache.save(second, image)
        monkeypatch.setattr(presentation_cache_module, "MAX_PRESENTATION_FILES", 64)
        monkeypatch.setattr(
            presentation_cache_module,
            "MAX_PRESENTATION_TOTAL_BYTES",
            first_size + 1,
        )
        with pytest.raises(OSError):
            cache.save(third, image)

    assert Path(first.cache_path).exists()
    assert not Path(second.cache_path).exists()
    assert not Path(third.cache_path).exists()


def test_remove_deletes_only_the_exact_rederived_child(tmp_path):
    root = tmp_path / ".refresh-presentation"
    cache = PresentationCache(root)
    first = _candidate(root)
    second = _candidate(root, request_id=OTHER_REQUEST_ID)
    with Image.new("RGB", (8, 8), "red") as image:
        cache.save(first, image)
        cache.save(second, image)
    forged = replace(first, cache_path=second.cache_path)

    assert cache.remove(forged) is False
    assert Path(first.cache_path).exists()
    assert Path(second.cache_path).exists()
    assert cache.remove(first) is True
    assert not Path(first.cache_path).exists()
    assert Path(second.cache_path).exists()
    assert cache.remove(first) is False


def test_remove_does_not_delete_a_replacement_file(tmp_path, monkeypatch):
    root = tmp_path / ".refresh-presentation"
    cache = PresentationCache(root)
    candidate = _candidate(root)
    with Image.new("RGB", (8, 8), "red") as image:
        cache.save(candidate, image)
    path = Path(candidate.cache_path)
    outside = tmp_path / "outside.png"
    replacement_link = tmp_path / "replacement-link.png"
    _write_png(outside, "blue")
    try:
        os.link(outside, replacement_link)
    except OSError as error:
        pytest.skip(f"hard links unavailable: {error}")
    real_replace = presentation_cache_module.os.replace
    swapped = False

    def swap_before_quarantine(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            real_replace(replacement_link, path)
            swapped = True
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        presentation_cache_module.os,
        "replace",
        swap_before_quarantine,
    )

    assert cache.remove(candidate) is False
    assert swapped is True
    assert outside.exists()
    assert path.exists()
    with Image.open(path) as remaining:
        assert remaining.getpixel((0, 0)) == (0, 0, 255)


def test_fallback_open_rejects_root_identity_change(tmp_path, monkeypatch):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    _write_png(candidate.cache_path)
    cache = PresentationCache(root)
    path = Path(candidate.cache_path)
    real_lstat = presentation_cache_module.os.lstat
    root_calls = 0

    def changed_root_lstat(target):
        nonlocal root_calls
        result = real_lstat(target)
        if Path(target) == root:
            root_calls += 1
            if root_calls == 2:
                return _clone_stat(result, st_ino=result.st_ino + 1)
        return result

    monkeypatch.setattr(presentation_cache_module.os, "lstat", changed_root_lstat)

    assert cache._open_bound_cache_file_fallback(path) is None
    assert root_calls >= 2


def test_fallback_open_rejects_path_identity_change(tmp_path, monkeypatch):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    _write_png(candidate.cache_path)
    cache = PresentationCache(root)
    path = Path(candidate.cache_path)
    real_lstat = presentation_cache_module.os.lstat
    path_calls = 0

    def changed_path_lstat(target):
        nonlocal path_calls
        result = real_lstat(target)
        if Path(target) == path:
            path_calls += 1
            if path_calls == 2:
                return _clone_stat(result, st_ino=result.st_ino + 1)
        return result

    monkeypatch.setattr(presentation_cache_module.os, "lstat", changed_path_lstat)

    assert cache._open_bound_cache_file_fallback(path) is None
    assert path_calls >= 2


def test_fallback_final_check_rejects_descriptor_path_replacement(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / ".refresh-presentation"
    candidate = _candidate(root)
    _write_png(candidate.cache_path)
    cache = PresentationCache(root)
    path = Path(candidate.cache_path)
    bound = cache._open_bound_cache_file_fallback(path)
    assert bound is not None
    real_lstat = presentation_cache_module.os.lstat

    def changed_path_lstat(target):
        result = real_lstat(target)
        if Path(target) == path:
            return _clone_stat(result, st_ino=result.st_ino + 1)
        return result

    monkeypatch.setattr(presentation_cache_module.os, "lstat", changed_path_lstat)
    try:
        assert cache._descriptor_still_matches_path(path, bound) is False
    finally:
        cache._close_bound_cache_file(bound)
