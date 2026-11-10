"""Remote thumbnails: a picture still on the page outlives newcomers in its bounded cache."""

import io
import itertools
import sys
import time
from pathlib import Path

from PIL import Image
import pytest

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


SVG_URL = "https://example.test/icons/Mark.svg"
SVG_MARK = (b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="20">'
            b'<rect x="2" y="4" width="6" height="12" fill="#102030"/></svg>')


class _Pages:
    """Answers each address with the bytes given for it."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def request_bytes(self, _method, url, **_kwargs):
        self.asked.append(url)
        return type("Response", (), {"data": self.pages[url]})()


def _asking(http, **extra):
    return {"http": http, "context": TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30),
            "safe_url": lambda url: url, "user_agent": "test", "cached_only": False, "keep_alpha": True, **extra}


def test_an_svg_logo_is_drawn_and_kept_as_a_picture_when_the_caller_asks(tmp_path):
    http = _Pages({SVG_URL: SVG_MARK})

    images = load_thumbnails([SVG_URL], tmp_path, size=(64, 64), svg_side=64, **_asking(http))

    mark = images[SVG_URL]
    # Drawn 64 px on its longer side with its own transparency: 32 x 64, clear around the bar.
    assert mark.mode == "RGBA" and mark.size == (32, 64)
    assert mark.getpixel((16, 32)) == (16, 32, 48, 255) and mark.getpixel((1, 1))[3] == 0
    # It is kept as a PNG: a redraw reads it without asking again, and without the SVG.
    with Image.open(tmp_path / _key(SVG_URL, True)) as kept:
        assert kept.format == "PNG" and kept.size == (32, 64)
    again = load_thumbnails([SVG_URL], tmp_path, size=(64, 64), **{**_asking(http), "cached_only": True})
    assert again[SVG_URL].getpixel((16, 32)) == (16, 32, 48, 255) and http.asked == [SVG_URL]


def test_a_drawn_svg_is_made_to_fit_the_thumbnails_size(tmp_path):
    images = load_thumbnails([SVG_URL], tmp_path, size=(24, 24), svg_side=96, **_asking(_Pages({SVG_URL: SVG_MARK})))

    assert images[SVG_URL].size == (12, 24)


def test_an_svg_address_is_left_alone_unless_the_caller_asks_for_it(tmp_path):
    assert load_thumbnails([SVG_URL], tmp_path, **_asking(_Pages({SVG_URL: SVG_MARK}))) == {}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("body", [
    b"<!doctype html><html><body>Not found</body></html>",
    b"",
    b"\x89PNG\r\n\x1a\n not an svg",
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><path d="M0 0L10',
])
def test_an_svg_address_that_serves_nothing_drawable_gives_no_picture(tmp_path, body):
    assert load_thumbnails([SVG_URL], tmp_path, svg_side=64, **_asking(_Pages({SVG_URL: body}))) == {}
    assert list(tmp_path.iterdir()) == []


def test_a_picture_address_is_still_read_as_a_picture_when_svgs_are_drawn(tmp_path):
    url = "https://example.test/icons/Mark.png"

    images = load_thumbnails([url], tmp_path, size=(64, 64), svg_side=64, **_asking(_Pages({url: _png((9, 8, 7))})))

    assert images[url].getpixel((5, 5)) == (9, 8, 7, 255)
