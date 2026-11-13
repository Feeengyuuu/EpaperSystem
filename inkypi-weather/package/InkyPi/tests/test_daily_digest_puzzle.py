"""万象速览's game tile: today's xkcd in the Wordle tile's turn, every day.

The chess.com puzzle that used to take every other day's turn is on 今日热榜; one the page kept in its
cache while its tile drew it is never read again, and never drawn.
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_digest import daily_digest as module, render, sources  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402

# xkcd as it answered on 2026-11-11, and the reading the page keeps of it.
XKCD = {"month": "11", "num": 3323, "year": "2026", "safe_title": "Meteor Fear", "alt": "Don't worry",
        "img": "https://imgs.xkcd.com/comics/meteor_fear.png", "title": "Meteor Fear", "day": "11"}
COMIC = {"kind": "xkcd", "num": 3323, "title": "Meteor Fear", "image": "https://imgs.xkcd.com/comics/meteor_fear.png"}
# chess.com's puzzle of that day, as the page kept it while its tile drew one.
CHESS = {"kind": "chess", "title": "Eviction Notice", "fen": "6k1/4rb2/3b1R1Q/p6p/2pq3r/P2n2NP/1P4P1/5R1K w"}
# The two files a device's cache held that day, as the release that drew the chess puzzle wrote them.
XKCD_FILE, CHESS_FILE = "puzzle-xkcd-39ab2864e166d5c0.json", "puzzle-chess-1cf1e65ce2f3aa7d.json"
WRITTEN = datetime(2026, 11, 11, 21, 13, 34, tzinfo=timezone.utc)
KEPT = {XKCD_FILE: {"schema": 2, "identity": {"kind": "xkcd"}, "fetched_at": WRITTEN.isoformat(), "data": COMIC},
        CHESS_FILE: {"schema": 2, "identity": {"kind": "chess"}, "fetched_at": WRITTEN.isoformat(), "data": CHESS}}
NOW = datetime(2026, 11, 11, 13, 0, tzinfo=sources.PACIFIC)
BELOW_HEADER = (0, render.BODY_TOP, 800, 480)
BLUE = (40, 90, 200)
CHESS_WORDS = {"每日棋题", "白先", "黑先", CHESS["title"]}


def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)


def _off(*_args):
    raise RuntimeError("off")


def _plugin(monkeypatch, tmp_path, xkcd, wordle=_off):
    """The plugin with every feed off but xkcd (and Wordle, when it is given one)."""
    monkeypatch.setenv("DAILY_DIGEST_CACHE_DIR", str(tmp_path))
    for name in ("fetch_poem", "fetch_gas", "fetch_debt", "fetch_fear_greed", "fetch_billboard", "fetch_news",
                 "fetch_chart", "fetch_douban", "fetch_space"):
        monkeypatch.setattr(sources, name, _off)
    monkeypatch.setattr(sources, "fetch_wordle", wordle)
    monkeypatch.setattr(sources, "fetch_xkcd", xkcd)
    return module.DailyDigest({"id": "daily_digest"})


def _reads(monkeypatch):
    """The name of every source whose cache file the page looks for from here on."""
    names = []
    original = module.SourceBook.read
    monkeypatch.setattr(module.SourceBook, "read",
                        lambda self, source: names.append(source.name) or original(self, source))
    return names


@pytest.fixture
def drawn(monkeypatch):
    """Every text a page draws while the test runs, in order."""
    texts = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text",
                        lambda self, xy, text, *args, **kwargs: texts.append(str(text)) or original(
                            self, xy, text, *args, **kwargs))
    return texts


def _picture(size=(400, 500)):
    return Image.new("RGB", size, BLUE)


def _page(reading, hour, mode="day", wordle=True, picture=None, key="xkcd"):
    """The page with its three 数字 tiles, Wordle unless told otherwise, and ``reading`` under ``key``."""
    payload = {"debt": {"data": {"total": 3.9e13, "date": "2026-11-10"}},
               "fear_greed": {"data": {"days": [{"value": 40, "name": "恐惧"}]}},
               "gas": {"data": {"weeks": [{"week": "2026-11-02", "price": 4.6}, {"week": "2026-11-09", "price": 4.5}]}},
               "wordle": {"data": {"day": "2026-11-10", "solution": "VIPER", "number": 1970}} if wordle else {},
               key: {"data": reading} if reading else {}}
    images = {} if picture is None else {"xkcd": picture}
    return render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW.replace(hour=hour), images)


def _comic_middle(box):
    """A point in the middle of the comic's room in the tile ``box``."""
    left, top, right, bottom = box
    return (left + right) // 2, (top + render.COMIC_BODY_TOP + bottom - 20) // 2


def test_xkcd_keeps_its_number_title_and_a_picture_only_from_its_own_host():
    assert sources.normalize_xkcd(XKCD) == COMIC
    with pytest.raises(ValueError):
        sources.normalize_xkcd({**XKCD, "img": "https://example.com/x.png"})
    assert sources.safe_xkcd_image("http://imgs.xkcd.com/comics/a.png") is None


def test_xkcd_is_asked_at_its_own_address_and_only_a_comic_with_its_picture_is_kept(monkeypatch):
    asked = []
    monkeypatch.setattr(sources, "_get", lambda url, context, **kwargs: asked.append(url) or XKCD)

    assert sources.fetch_xkcd(_context()) == COMIC
    assert asked == ["https://xkcd.com/info.0.json"]
    assert sources.valid_xkcd(COMIC) and not sources.valid_xkcd({**COMIC, "image": ""})
    # A chess puzzle or a Connections board kept from the days the tile had one is not xkcd's comic,
    # whatever else it carries.
    assert not sources.valid_xkcd(CHESS) and not sources.valid_xkcd({**COMIC, "kind": "chess"})
    assert not sources.valid_xkcd({"kind": "connections", "words": ["A"] * 16})


def test_xkcd_is_one_request_kept_six_hours_and_never_counts_against_the_page(monkeypatch, tmp_path):
    asked = []
    state = {"fail": False}

    def xkcd(context):
        asked.append("xkcd")
        if state["fail"]:
            raise RuntimeError("down")
        return sources.normalize_xkcd(XKCD)

    plugin = _plugin(monkeypatch, tmp_path, xkcd)

    first = plugin._payload({}, NOW, allow_network=True, context=_context())
    assert asked == ["xkcd"] and first["xkcd"]["data"] == COMIC
    again = plugin._payload({}, NOW + timedelta(hours=5), allow_network=True, context=_context())
    assert len(asked) == 1 and again["xkcd"]["data"] == COMIC
    # A redraw from cache never asks; a failed refresh keeps the last good comic.
    plugin._payload({}, NOW + timedelta(hours=9), allow_network=False, context=_context())
    assert len(asked) == 1
    state["fail"] = True
    late = plugin._payload({}, NOW + timedelta(hours=9), allow_network=True, context=_context())
    assert len(asked) == 2 and late["xkcd"]["data"] == COMIC
    # The page's provenance is the same with xkcd failing as with it answering.
    assert late["provenance"] == first["provenance"]


def test_the_comic_a_device_holds_stays_in_use_and_its_kept_chess_puzzle_is_never_read(monkeypatch, tmp_path):
    asked = []
    plugin = _plugin(monkeypatch, tmp_path, lambda context: asked.append("xkcd") or _off())
    folder = plugin._cache_subdir("sources")
    for name, kept in KEPT.items():
        (folder / name).write_text(json.dumps(kept), encoding="utf-8")
    read = _reads(monkeypatch)

    # Within its six hours the kept comic is the tile's reading without a request, on a redraw and a refresh.
    for network in (False, True):
        payload = plugin._payload({}, WRITTEN + timedelta(hours=1), allow_network=network, context=_context())
        assert payload["xkcd"]["state"] == "fresh_cache" and payload["xkcd"]["data"] == COMIC
    assert asked == []
    # The next day, which used to be the chess puzzle's, xkcd is asked for; while it is down the kept
    # comic still stands. The chess puzzle's file is never opened and nothing of it reaches the page.
    late = plugin._payload({}, WRITTEN + timedelta(hours=20), allow_network=True, context=_context())
    assert asked == ["xkcd"] and late["xkcd"]["state"] == "stale_cache" and late["xkcd"]["data"] == COMIC
    assert "puzzle-xkcd" in read and "puzzle-chess" not in read
    assert "puzzle" not in late and "chess" not in json.dumps(late, default=str)


class _PictureClient:
    """Answers every picture the page asks for with one small blue JPEG."""

    def __init__(self):
        self.urls = []

    def request_bytes(self, method, url, **kwargs):
        self.urls.append(url)
        buffer = BytesIO()
        _picture().save(buffer, format="JPEG")
        return SimpleNamespace(data=buffer.getvalue())


def _refresh(plugin, now):
    """One refresh with the network on, as generate_image runs it: (the page, its payload)."""
    payload = plugin._payload({}, now, allow_network=True, context=_context())
    images = plugin._images(payload, now, now.timestamp(), allow_network=True, context=_context())
    return render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, now, images), payload


def test_the_game_tile_is_xkcd_on_every_days_odd_hours_and_never_a_chess_board(monkeypatch, tmp_path, drawn):
    asked = []
    client = _PictureClient()
    monkeypatch.setattr(module, "get_http_client", lambda: client)
    plugin = _plugin(monkeypatch, tmp_path, lambda context: asked.append("xkcd") or dict(COMIC),
                     wordle=lambda day, context: {"day": day, "solution": "VIPER", "number": 1970})
    # The device still holds the chess puzzle its tile drew every other day.
    (plugin._cache_subdir("sources") / CHESS_FILE).write_text(json.dumps(KEPT[CHESS_FILE]), encoding="utf-8")
    read = _reads(monkeypatch)

    tiles = []
    for offset in range(4):
        odd = NOW + timedelta(days=offset)
        page, payload = _refresh(plugin, odd)
        assert payload["xkcd"]["data"] == COMIC
        box = tuple(page.info["daily_digest_xkcd"]["box"])
        # The comic itself is on the tile (its picture came with the refresh).
        assert max(abs(a - b) for a, b in zip(page.getpixel(_comic_middle(box)), BLUE)) <= 8
        tiles.append(page.crop(box).tobytes())
        assert "daily_digest_xkcd" not in _refresh(plugin, odd.replace(hour=14))[0].info
    # One request and the same tile each day; Wordle on the even hours; no chess board on any.
    assert asked == ["xkcd"] * 4 and len(set(tiles)) == 1
    assert {"xkcd", "今日漫画", "#3323 Meteor Fear", "Wordle 昨日"} <= set(drawn) and not CHESS_WORDS & set(drawn)
    assert "puzzle-xkcd" in read and "puzzle-chess" not in read and client.urls.count(COMIC["image"]) == 1


@pytest.mark.parametrize("mode", ["day", "night"])
def test_xkcd_takes_the_wordle_tiles_turn_on_odd_hours(drawn, mode):
    odd = _page(COMIC, 13, mode, picture=_picture())
    assert "xkcd" in drawn and "今日漫画" in drawn and "Wordle 昨日" not in drawn
    drawn.clear()
    even = _page(COMIC, 14, mode, picture=_picture())
    assert "Wordle 昨日" in drawn and "xkcd" not in drawn
    box = tuple(odd.info["daily_digest_xkcd"]["box"])
    assert "daily_digest_xkcd" not in even.info
    # Under the header (its clock shows the hour) only that tile differs between the two hours.
    changed = ImageChops.difference(odd.crop(BELOW_HEADER), even.crop(BELOW_HEADER)).getbbox()
    assert changed and box[0] <= changed[0] and box[1] <= changed[1] + render.BODY_TOP
    assert changed[2] <= box[2] + 1 and changed[3] + render.BODY_TOP <= box[3] + 1
    # Every odd hour is xkcd's and every even hour Wordle's.
    for hour in (1, 23):
        assert _page(COMIC, hour, mode, picture=_picture()).crop(box).tobytes() == odd.crop(box).tobytes()
    for hour in (0, 22):
        assert _page(COMIC, hour, mode, picture=_picture()).crop(box).tobytes() == even.crop(box).tobytes()
    # Without the comic the page is the one it was, at every hour; without Wordle xkcd holds the tile.
    plain = _page(None, 13, mode)
    assert plain.crop(BELOW_HEADER).tobytes() == _page(None, 14, mode).crop(BELOW_HEADER).tobytes()
    assert plain.crop(BELOW_HEADER).tobytes() == even.crop(BELOW_HEADER).tobytes()
    drawn.clear()
    alone = _page(COMIC, 14, mode, wordle=False, picture=_picture())
    assert "xkcd" in drawn and "daily_digest_xkcd" in alone.info


@pytest.mark.parametrize("key", ["xkcd", "puzzle"])
def test_a_chess_puzzle_in_the_tiles_reading_is_never_drawn(drawn, key):
    """Whether it sits where the tile's reading is now or under the name the page kept it by."""
    for mode in ("day", "night"):
        # On the tile's odd hour, a picture at hand: the tile is Wordle's, as on a page with no reading,
        # even for a puzzle that also carries a comic's number and picture address.
        for puzzle in (CHESS, {**COMIC, **CHESS}):
            page = _page(puzzle, 13, mode, picture=_picture(), key=key)
            assert "daily_digest_xkcd" not in page.info
            assert page.crop(BELOW_HEADER).tobytes() == _page(None, 13, mode).crop(BELOW_HEADER).tobytes()
    assert "Wordle 昨日" in drawn and not (CHESS_WORDS | {"xkcd", "今日漫画"}) & set(drawn)
    # Without Wordle the game tile is left out and the three 数字 tiles stand alone.
    drawn.clear()
    bare = _page(CHESS, 13, wordle=False, picture=_picture(), key=key)
    assert "daily_digest_xkcd" not in bare.info and "美国国债" in drawn
    assert not (CHESS_WORDS | {"xkcd", "今日漫画", "Wordle 昨日"}) & set(drawn)


def test_xkcd_is_scaled_whole_into_the_tile_and_a_wide_strip_shows_its_first_panel(drawn):
    tall = _picture((400, 500))
    page = _page(COMIC, 13, picture=tall)
    assert "xkcd" in drawn and any(text.startswith("#3323") for text in drawn)
    # By day the comic is pasted as it is, in the middle of its room.
    assert page.getpixel(_comic_middle(tuple(page.info["daily_digest_xkcd"]["box"]))) == BLUE
    assert render.comic_view(tall, (116, 66)).size[1] == 66 and render.comic_view(tall, (116, 66)).size[0] <= 116
    # A strip three times as wide as the room is cut to its first panel instead of shrinking to a ribbon.
    strip = _picture((900, 250))
    view = render.comic_view(strip, (116, 66))
    assert view.size[0] <= 116 and view.size[1] >= 60
    # No picture at hand (a first failure): the tile gives its turn back to Wordle.
    drawn.clear()
    _page(COMIC, 13)
    assert "Wordle 昨日" in drawn and "xkcd" not in drawn and page.size == (800, 480)
