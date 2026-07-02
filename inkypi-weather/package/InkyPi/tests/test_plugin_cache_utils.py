from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from utils.draw_utils import fit_text, text_width
from utils.plugin_cache import CachedState, MemoryTTLCache, is_fresh, read_json, write_json


def test_read_write_json_round_trip(tmp_path):
    path = tmp_path / "state.json"

    write_json(path, {"b": 2, "a": "雪"}, ensure_ascii=False, indent=2)

    assert read_json(path, default={}) == {"b": 2, "a": "雪"}
    assert read_json(tmp_path / "missing.json", default={"ok": True}) == {"ok": True}


def test_cached_state_version_ttl_and_daily_counter(tmp_path):
    state = CachedState(tmp_path / "state.json", version="v1", ttl_seconds=60)
    state.write({"fetched_at": "2026-07-01T12:00:00+00:00"})

    assert state.read(default={})["version"] == "v1"
    assert state.is_fresh(datetime(2026, 7, 1, 12, 0, 30, tzinfo=timezone.utc)) is True
    assert state.is_fresh(datetime(2026, 7, 1, 12, 2, 0, tzinfo=timezone.utc)) is False
    assert state.daily_calls_left(2, date_key="2026-07-01") == 2
    assert state.record_daily_call(date_key="2026-07-01") == 1
    assert state.daily_calls_left(2, date_key="2026-07-01") == 1


def test_memory_ttl_cache_expires_success_and_failure_entries():
    cache = MemoryTTLCache(time_func=lambda: 100.0)
    cache.set_entry("a", {"title": "Paris"}, now=100.0)
    cache.set_entry("b", {"failed": True}, now=100.0)

    assert cache.get_entry("a", success_ttl=10, failure_ttl=1, now=109.0)["title"] == "Paris"
    assert cache.get_entry("a", success_ttl=10, failure_ttl=1, now=111.0) is None
    assert cache.get_entry("b", success_ttl=10, failure_ttl=1, now=101.5) is None


def test_fit_text_clips_to_available_width():
    image = Image.new("RGB", (200, 40), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    clipped = fit_text(draw, "A very long title", font, 40)

    assert clipped.endswith("...")
    assert text_width(draw, clipped, font) <= text_width(draw, "A very long title", font)