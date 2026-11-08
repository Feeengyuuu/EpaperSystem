"""Remote thumbnails: a picture still on the page outlives newcomers in its bounded cache."""

import io
import itertools
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.refresh_contracts import TaskContext  # noqa: E402
from utils.cache_manager import CacheBudget, cache_namespace_for_directory  # noqa: E402
from utils.remote_thumbnails import _key, load_thumbnails  # noqa: E402


def _png(colour):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), colour).save(buffer, format="PNG")
    return buffer.getvalue()


class _Http:
    def __init__(self):
        self.asked = []

    def request_bytes(self, _method, url, **_kwargs):
        self.asked.append(url)
        return type("Response", (), {"data": _png((len(self.asked) * 40 % 256, 90, 120))})()


def test_a_picture_asked_for_again_is_kept_over_one_no_longer_shown(tmp_path):
    """MakerWorld's lead stayed on the page for hours while new cards came and went: its cover,
    stored first, was the cache's oldest file and the first a new card's cover pushed out, so the
    next cache-only redraw showed its placeholder."""
    budget = CacheBudget(max_age_seconds=86400, max_files=2, max_bytes=1024 * 1024)
    clock = itertools.count(1_000_000)
    namespace = cache_namespace_for_directory(tmp_path, budget)
    namespace.manager._clock = lambda: float(next(clock))
    http = _Http()
    common = {"http": http, "context": TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30),
              "safe_url": lambda url: url, "user_agent": "test", "budget": budget}
    lead, old_card, new_card = (f"https://example.test/{name}.png" for name in ("lead", "old", "new"))

    assert set(load_thumbnails([lead, old_card], tmp_path, cached_only=False, **common)) == {lead, old_card}
    # The next refresh shows the lead again beside a new card: the card no longer shown makes room.
    assert set(load_thumbnails([lead, new_card], tmp_path, cached_only=False, **common)) == {lead, new_card}
    assert http.asked == [lead, old_card, new_card]
    assert {path.name for path in tmp_path.iterdir()} == {_key(lead), _key(new_card)}
    # A later redraw from the cache alone still has both pictures on the page.
    assert set(load_thumbnails([lead, new_card], tmp_path, cached_only=True, **common)) == {lead, new_card}


def test_a_cache_only_read_touches_nothing(tmp_path):
    budget = CacheBudget(max_age_seconds=86400, max_files=4, max_bytes=1024 * 1024)
    http = _Http()
    common = {"http": http, "context": TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30),
              "safe_url": lambda url: url, "user_agent": "test", "budget": budget}
    url = "https://example.test/cover.png"
    load_thumbnails([url], tmp_path, cached_only=False, **common)
    path = tmp_path / _key(url)
    before = path.stat().st_mtime_ns
    assert set(load_thumbnails([url, "https://example.test/missing.png"], tmp_path, cached_only=True, **common)) == {url}
    assert path.stat().st_mtime_ns == before and http.asked == [url]
