import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import SourceProvenance  # noqa: E402
from plugins.base_plugin.source_book import (  # noqa: E402
    DISABLED, FRESH_CACHE, LIVE, STALE_CACHE, UNAVAILABLE, Source, SourceBook, combined_provenance,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded  # noqa: E402
from utils.cache_manager import CacheBudget  # noqa: E402
from utils.flat_icons import ICONS, draw_icon  # noqa: E402
from utils.page_canvas import PageCanvas  # noqa: E402

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)


def _context(seconds=30):
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + seconds)


def _book(tmp_path):
    return SourceBook(tmp_path / "sources", CacheBudget(86400, 8, 1024 * 1024), schema=1)


def _source(fetch, **kwargs):
    return Source("feed", {"key": "value"}, fetch, fresh_seconds=600, usable_seconds=3600, **kwargs)


def test_live_fetch_is_cached_then_served_fresh(tmp_path):
    book = _book(tmp_path)
    calls = []

    def fetch(_context):
        calls.append(1)
        return {"value": 1}

    first = book.load(_source(fetch), NOW, allow_network=True, context=_context())
    second = book.load(_source(fetch), NOW + timedelta(minutes=5), allow_network=True, context=_context())

    assert (first["state"], second["state"]) == (LIVE, FRESH_CACHE)
    assert second["data"] == {"value": 1}
    assert len(calls) == 1


def test_provider_failure_falls_back_to_usable_cache_then_unavailable(tmp_path):
    book = _book(tmp_path)
    book.load(_source(lambda _ctx: {"value": 1}), NOW, allow_network=True, context=_context())

    def broken(_context):
        raise RuntimeError("provider down")

    stale = book.load(_source(broken), NOW + timedelta(minutes=20), allow_network=True, context=_context())
    expired = book.load(_source(broken), NOW + timedelta(hours=2), allow_network=True, context=_context())

    assert (stale["state"], stale["error"], stale["data"]) == (STALE_CACHE, "RuntimeError", {"value": 1})
    assert (expired["state"], expired["data"]) == (UNAVAILABLE, {})


def test_cache_only_reads_never_fetch(tmp_path):
    book = _book(tmp_path)
    book.load(_source(lambda _ctx: {"value": 1}), NOW, allow_network=True, context=_context())

    def forbidden(_context):
        pytest.fail("cache-only load fetched")

    result = book.load(_source(forbidden), NOW + timedelta(minutes=30), allow_network=False, context=_context())

    assert (result["state"], result["data"]) == (STALE_CACHE, {"value": 1})


def test_unconfigured_source_is_disabled_and_ignored_by_provenance(tmp_path):
    result = _book(tmp_path).load(_source(None), NOW, allow_network=True, context=_context())

    assert result["state"] == DISABLED
    assert combined_provenance([result, {"state": LIVE}]) is SourceProvenance.LIVE


def test_slow_source_fails_alone_on_its_child_deadline(tmp_path):
    book = _book(tmp_path)

    def slow(child):
        assert child.deadline_monotonic < time.monotonic() + 5
        raise TaskDeadlineExceeded("child deadline")

    result = book.load(_source(slow, timeout_seconds=2), NOW, allow_network=True, context=_context())

    assert (result["state"], result["error"]) == (UNAVAILABLE, "source_deadline")


def test_parent_cancellation_still_propagates(tmp_path):
    book = _book(tmp_path)
    context = _context()

    def cancelled(_child):
        context.cancel_event.set()
        raise TaskCancelled("cancelled")

    with pytest.raises(TaskCancelled):
        book.load(_source(cancelled), NOW, allow_network=True, context=context)


def test_sources_are_skipped_when_too_little_time_remains(tmp_path):
    result = _book(tmp_path).load(
        _source(lambda _ctx: pytest.fail("fetched without budget"), min_remaining_seconds=20),
        NOW, allow_network=True, context=_context(5),
    )

    assert (result["state"], result["error"]) == (UNAVAILABLE, "deadline_budget")


def test_invalid_or_foreign_cache_files_are_ignored(tmp_path):
    book = _book(tmp_path)
    spec = _source(lambda _ctx: {"value": 1}, validate=lambda data: data.get("value") == 1)
    book.load(spec, NOW, allow_network=True, context=_context())
    path = next((tmp_path / "sources").glob("feed-*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**payload, "data": {"value": 2}}), encoding="utf-8")

    assert book.read(spec) is None


def test_combined_provenance_marks_any_degraded_source():
    assert combined_provenance([{"state": FRESH_CACHE}, {"state": FRESH_CACHE}]) is SourceProvenance.FRESH_CACHE
    assert combined_provenance([{"state": LIVE}, {"state": STALE_CACHE}]) is SourceProvenance.STALE_CACHE
    assert combined_provenance([{"state": LIVE}, {"state": UNAVAILABLE}]) is SourceProvenance.STALE_CACHE
    assert combined_provenance([{"state": UNAVAILABLE}]) is SourceProvenance.LOCAL_FALLBACK
    assert combined_provenance([{"state": DISABLED}]) is SourceProvenance.LOCAL_FALLBACK


def test_every_flat_icon_stays_inside_its_square():
    from PIL import Image, ImageDraw

    for kind in ICONS:
        canvas = Image.new("RGB", (60, 60), "white")
        draw_icon(ImageDraw.Draw(canvas), kind, (10, 10), 40, (0, 0, 0))
        box = canvas.convert("L").point(lambda value: 255 if value < 128 else 0).getbbox()
        assert box is not None, kind
        assert box[0] >= 8 and box[1] >= 8 and box[2] <= 52 and box[3] <= 52, (kind, box)


def test_page_canvas_wraps_words_and_characters_with_an_ellipsis():
    canvas = PageCanvas({"paper": (255, 255, 255), "ink": (0, 0, 0)})
    latin = canvas.wrap("Joints, Camera, Action! Co-Generating 3D Motion and Video for Arbitrary Scenes", 13, 160, 2)
    chinese = canvas.wrap("湾区暂无生效预警覆盖十个县天气局实时数据", 13, 80, 2)

    assert len(latin) == 2 and latin[-1].endswith("…")
    assert latin[0].startswith("Joints, Camera") and not latin[0].endswith(" ")
    assert all(canvas.width(line, 13) <= 160 for line in latin)
    assert len(chinese) == 2 and chinese[-1].endswith("…")
    assert canvas.wrap("short", 13, 160, 2) == ["short"]
