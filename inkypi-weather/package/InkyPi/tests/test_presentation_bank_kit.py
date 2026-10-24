"""The building blocks the plugins' presentation banks share."""

import sys
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin import presentation_bank_kit as kit  # noqa: E402


def test_hashes_ids_and_times_are_stable():
    assert kit.json_hash({"b": 1, "a": [1, 2]}) == kit.json_hash({"a": [1, 2], "b": 1})
    assert kit.valid_request_id("a" * 32) and not kit.valid_request_id("A" * 32)
    assert kit.valid_hash("0" * 64) and not kit.valid_hash("0" * 63)
    assert kit.parse_datetime("2026-10-24T01:00:00").utcoffset().total_seconds() == 0
    assert kit.parse_datetime("") is None and kit.parse_datetime(None) is None
    assert kit.bounded_int("x", 5, 1, 3) == 3 and kit.bounded_text("  abc  ", 2) == "ab"


def test_date_buckets_keep_the_newest_by_their_stamp():
    buckets = {f"2026-10-{day:02d}": {"seen": f"2026-10-{day:02d}T00:00:00+00:00"} for day in range(1, 6)}
    buckets["junk"] = "not a bucket"

    kept = kit.bounded_date_buckets(buckets, limit=2, stamp="seen", copy=True)

    assert sorted(kept) == ["2026-10-04", "2026-10-05"]
    assert kept["2026-10-05"] is not buckets["2026-10-05"]
    assert kit.bounded_date_buckets(buckets, limit=10, stamp="seen")["2026-10-01"] is buckets["2026-10-01"]


def test_state_and_media_bounds_name_the_plugin():
    with pytest.raises(RuntimeError, match="Demo state exceeds the size limit"):
        kit.validate_state_payload_size({"x": "y" * 50}, validate_shape=lambda _payload: None, max_bytes=10,
                                        label="Demo state")
    with pytest.raises(RuntimeError, match="Demo media dimensions exceed the safety limit"):
        kit.validate_media_dimensions((5000, 10), max_dimension=4096, max_pixels=10**8, label="Demo media")

    def check(size):
        kit.validate_media_dimensions(size, max_dimension=4096, max_pixels=10**8, label="Demo media")

    image = kit.normalize_media_image(Image.new("L", (4, 3)), validate_dimensions=check, label="Demo media")
    assert image.mode == "RGB" and image.size == (4, 3)
    with pytest.raises(RuntimeError, match="Demo media is not an image"):
        kit.normalize_media_image("nope", validate_dimensions=check, label="Demo media")


def test_selection_bookkeeping_finds_the_pending_and_protected_records():
    profile = {"current_selection": {"record_keys": ["a"]},
               "pending_selection": {"request_id": "r1", "record_keys": ["b"]}}
    book = kit.SelectionBookkeeping()

    assert book.pending_for_request(profile, "r1")["record_keys"] == ["b"]
    assert book.pending_for_request(profile, "r2") is None
    assert book._protected_record_keys(profile) == {"a", "b"}
