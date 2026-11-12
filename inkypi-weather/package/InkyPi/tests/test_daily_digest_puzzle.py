"""每日一题 on 万象速览: the chess.com puzzle, today's xkcd or the Connections board, in the Wordle tile's turn."""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_digest import daily_digest as module, render, sources  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402

# The three sources as they answered on 2026-11-11 (Connections cut to two of its four groups' shape).
CHESS = {"title": "Eviction Notice", "url": "https://www.chess.com/daily/2026-11-11", "publish_time": 1794384000,
         "fen": "6k1/4rb2/3b1R1Q/p6p/2pq3r/P2n2NP/1P4P1/5R1K w - - 0 1",
         "pgn": "[White \"David_Goldfarb\"]\n\n1. Rg6+ Bxg6 2. Rf8# *",
         "image": "https://www.chess.com/dynboard?fen=6k1/4rb2/3b1R1Q&size=2"}
XKCD = {"month": "11", "num": 3323, "year": "2026", "safe_title": "Meteor Fear", "alt": "Don't worry",
        "img": "https://imgs.xkcd.com/comics/meteor_fear.png", "title": "Meteor Fear", "day": "11"}
CONNECTIONS = {"status": "OK", "id": 1354, "print_date": "2026-11-11", "editor": "Wyna Liu", "categories": [
    {"title": "KINDS OF MEDICINE", "cards": [{"content": "FOLK", "position": 5}, {"content": "HERBAL", "position": 1},
                                             {"content": "HOLISTIC", "position": 13}, {"content": "TRADITIONAL", "position": 10}]},
    {"title": "PARTS OF A GOLF COURSE", "cards": [{"content": "GREEN", "position": 4}, {"content": "ROUGH", "position": 7},
                                                  {"content": "SAND TRAP", "position": 12}, {"content": "TEE BOX", "position": 15}]},
    {"title": "THINGS FILLED WITH AIR", "cards": [{"content": "BALLOON", "position": 0}, {"content": "BUBBLE", "position": 9},
                                                  {"content": "LUNG", "position": 2}, {"content": "TIRE", "position": 14}]},
    {"title": "___ BOX", "cards": [{"content": "JUKE", "position": 3}, {"content": "LITTER", "position": 6},
                                   {"content": "MAIL", "position": 8}, {"content": "SOAP", "position": 11}]}]}
NOW = datetime(2026, 11, 11, 13, 0, tzinfo=sources.PACIFIC)


def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)


def test_the_chess_puzzle_keeps_its_title_and_position_never_its_solution():
    puzzle = sources.normalize_chess(CHESS)

    assert puzzle == {"kind": "chess", "title": "Eviction Notice", "fen": "6k1/4rb2/3b1R1Q/p6p/2pq3r/P2n2NP/1P4P1/5R1K w"}
    board = sources.fen_board(puzzle["fen"])
    assert board[0] == [None] * 6 + ["k", None] and board[7][7] == "K" and len(board) == 8
    assert sources.fen_side(puzzle["fen"]) == "w"
    for broken in ({}, {"fen": "8/8/8 w"}, {"fen": "9/8/8/8/8/8/8/8 w"}, {"fen": "xxxxxxxx/8/8/8/8/8/8/8 w"}, None):
        with pytest.raises(ValueError):
            sources.normalize_chess(broken)


def test_xkcd_keeps_its_number_title_and_a_picture_only_from_its_own_host():
    assert sources.normalize_xkcd(XKCD) == {"kind": "xkcd", "num": 3323, "title": "Meteor Fear",
                                           "image": "https://imgs.xkcd.com/comics/meteor_fear.png"}
    with pytest.raises(ValueError):
        sources.normalize_xkcd({**XKCD, "img": "https://example.com/x.png"})
    assert sources.safe_xkcd_image("http://imgs.xkcd.com/comics/a.png") is None


def test_connections_keeps_the_sixteen_words_in_the_boards_own_shuffle_and_nothing_of_the_groups():
    board = sources.normalize_connections(CONNECTIONS, "2026-11-11")

    assert board["kind"] == "connections" and board["number"] == 1354 and len(board["words"]) == 16
    assert board["words"][:6] == ["BALLOON", "HERBAL", "LUNG", "JUKE", "GREEN", "FOLK"]
    # No group title, and no two neighbours that give a group away by order.
    assert set(board) == {"kind", "day", "number", "words"}
    assert "MEDICINE" not in str(board) and "GOLF" not in str(board)
    with pytest.raises(ValueError):
        sources.normalize_connections(CONNECTIONS, "2026-11-12")
    with pytest.raises(ValueError):
        sources.normalize_connections({**CONNECTIONS, "categories": CONNECTIONS["categories"][:3]}, "2026-11-11")


def test_the_day_chooses_one_puzzle_and_each_is_asked_at_its_own_address(monkeypatch):
    kinds = [sources.puzzle_kind(NOW + timedelta(days=offset)) for offset in range(6)]
    assert sorted(kinds[:3]) == sorted(sources.PUZZLES) and kinds[3:] == kinds[:3]
    assert sources.puzzle_kind(NOW.replace(hour=0)) == sources.puzzle_kind(NOW.replace(hour=23))

    asked = []
    answers = {"chess": CHESS, "xkcd": XKCD, "connections": CONNECTIONS}
    monkeypatch.setattr(sources, "_get", lambda url, context, **kwargs: asked.append(url) or next(
        answer for key, answer in answers.items() if key in url or (key == "chess" and "chess.com" in url)))
    for kind in sources.PUZZLES:
        assert sources.fetch_puzzle(kind, NOW, _context())["kind"] == kind
    assert asked == ["https://api.chess.com/pub/puzzle", "https://xkcd.com/info.0.json",
                     "https://www.nytimes.com/svc/connections/v2/2026-11-11.json"]


def _plugin(monkeypatch, tmp_path, puzzle):
    monkeypatch.setenv("DAILY_DIGEST_CACHE_DIR", str(tmp_path))
    names = ("fetch_poem", "fetch_gas", "fetch_debt", "fetch_fear_greed", "fetch_billboard")
    for name in names:
        monkeypatch.setattr(sources, name, lambda ctx: (_ for _ in ()).throw(RuntimeError("off")))
    for name in ("fetch_news", "fetch_chart", "fetch_douban", "fetch_space", "fetch_wordle"):
        monkeypatch.setattr(sources, name, lambda key, ctx: (_ for _ in ()).throw(RuntimeError("off")))
    monkeypatch.setattr(sources, "fetch_puzzle", puzzle)
    return module.DailyDigest({"id": "daily_digest"})


def test_the_puzzle_is_one_request_kept_six_hours_and_never_counts_against_the_page(monkeypatch, tmp_path):
    asked = []
    state = {"fail": False}

    def puzzle(kind, now, context):
        asked.append(kind)
        if state["fail"]:
            raise RuntimeError("down")
        return sources.normalize_chess(CHESS)

    plugin = _plugin(monkeypatch, tmp_path, puzzle)

    first = plugin._payload({}, NOW, allow_network=True, context=_context())
    assert asked == [sources.puzzle_kind(NOW)] and first["puzzle"]["data"]["title"] == "Eviction Notice"
    again = plugin._payload({}, NOW + timedelta(hours=5), allow_network=True, context=_context())
    assert len(asked) == 1 and again["puzzle"]["data"]["kind"] == "chess"
    # A redraw from cache never asks; a failed refresh keeps the last good puzzle of that kind.
    plugin._payload({}, NOW + timedelta(hours=9), allow_network=False, context=_context())
    assert len(asked) == 1
    state["fail"] = True
    late = plugin._payload({}, NOW + timedelta(hours=9), allow_network=True, context=_context())
    assert len(asked) == 2 and late["puzzle"]["data"]["kind"] == "chess"
    # The page's provenance is the same with the puzzle failing as with it answering.
    assert late["provenance"] == first["provenance"]


def _page(puzzle, hour, mode="day", wordle=True, images=None, drawn=None, monkeypatch=None):
    payload = {"debt": {"data": {"total": 3.9e13, "date": "2026-11-10"}},
               "fear_greed": {"data": {"days": [{"value": 40, "name": "恐惧"}]}},
               "gas": {"data": {"weeks": [{"week": "2026-11-02", "price": 4.6}, {"week": "2026-11-09", "price": 4.5}]}},
               "wordle": {"data": {"day": "2026-11-10", "solution": "VIPER", "number": 1970}} if wordle else {},
               "puzzle": {"data": puzzle} if puzzle else {}}
    if drawn is not None:
        original = render.PageCanvas.text
        monkeypatch.setattr(render.PageCanvas, "text", lambda self, xy, text, *args, **kwargs: drawn.append(str(text))
                            or original(self, xy, text, *args, **kwargs))
    return render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW.replace(hour=hour), images or {})


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_puzzle_takes_the_wordle_tiles_turn_on_odd_hours(monkeypatch, mode):
    drawn = []
    odd = _page(sources.normalize_chess(CHESS), 13, mode, drawn=drawn, monkeypatch=monkeypatch)
    assert "每日棋题" in drawn and "白先" in drawn and "Wordle 昨日" not in drawn
    drawn.clear()
    even = _page(sources.normalize_chess(CHESS), 14, mode, drawn=drawn, monkeypatch=monkeypatch)
    assert "Wordle 昨日" in drawn and "每日棋题" not in drawn
    # Only the fourth tile differs between the two hours.
    box = tuple(odd.info["daily_digest_puzzle"]["box"])
    assert odd.info["daily_digest_puzzle"]["kind"] == "chess" and "daily_digest_puzzle" not in even.info
    # Under the header (its clock shows the hour) only that tile differs between the two hours.
    below = (0, render.BODY_TOP, 800, 480)
    changed = ImageChops.difference(odd.crop(below), even.crop(below)).getbbox()
    assert changed and box[0] <= changed[0] and box[1] <= changed[1] + render.BODY_TOP
    assert changed[2] <= box[2] + 1 and changed[3] + render.BODY_TOP <= box[3] + 1
    # Without a puzzle the page is the one it was, at every hour; without Wordle the puzzle holds the tile.
    plain = _page(None, 13, mode)
    assert plain.crop(below).tobytes() == _page(None, 14, mode).crop(below).tobytes()
    assert plain.crop(below).tobytes() == even.crop(below).tobytes()
    drawn.clear()
    _page(sources.normalize_chess(CHESS), 14, mode, wordle=False, drawn=drawn, monkeypatch=monkeypatch)
    assert "每日棋题" in drawn


def test_the_board_is_drawn_from_the_position_with_the_side_to_move_at_the_bottom():
    image = _page(sources.normalize_chess(CHESS), 13)
    box = tuple(image.info["daily_digest_puzzle"]["box"])
    squares = render.chess_squares(box)
    ink, paper = (18, 26, 32), (255, 255, 255)

    def has(square, colour):
        left, top, right, bottom = squares[square]
        return any(image.getpixel((x, y)) == colour for x in range(left + 2, right - 1) for y in range(top + 2, bottom - 1))

    # White to move: rank 8 on top. The black king on g8 is a solid ink piece, the white king on h1 an outlined one.
    assert has((0, 6), ink) and has((7, 7), ink) and has((7, 7), paper)
    left, top, right, bottom = squares[(0, 0)]
    assert {image.getpixel((x, y)) for x in range(left + 1, right) for y in range(top + 1, bottom)} == {paper}
    # Black to move turns the board round.
    flipped = _page({**sources.normalize_chess(CHESS), "fen": CHESS["fen"].split(" ")[0] + " b"}, 13)
    assert flipped.crop(box).tobytes() != image.crop(box).tobytes()


def test_xkcd_is_scaled_whole_into_the_tile_and_a_wide_strip_shows_its_first_panel(monkeypatch):
    comic = sources.normalize_xkcd(XKCD)
    drawn = []
    tall = Image.new("RGB", (400, 500), (250, 250, 250))
    page = _page(comic, 13, images={"puzzle": tall}, drawn=drawn, monkeypatch=monkeypatch)
    assert "xkcd" in drawn and any(text.startswith("#3323") for text in drawn)
    assert render.comic_view(tall, (116, 66)).size[1] == 66 and render.comic_view(tall, (116, 66)).size[0] <= 116
    # A strip three times as wide as the room is cut to its first panel instead of shrinking to a ribbon.
    strip = Image.new("RGB", (900, 250), (250, 250, 250))
    view = render.comic_view(strip, (116, 66))
    assert view.size[0] <= 116 and view.size[1] >= 60
    # No picture at hand (a first failure): the tile gives its turn back to Wordle.
    drawn.clear()
    _page(comic, 13, drawn=drawn, monkeypatch=monkeypatch)
    assert "Wordle 昨日" in drawn and page.size == (800, 480)


def test_connections_prints_its_sixteen_words_whole_and_no_group(monkeypatch):
    board = sources.normalize_connections(CONNECTIONS, "2026-11-11")
    drawn = []
    for mode in ("day", "night"):
        _page(board, 13, mode, drawn=drawn, monkeypatch=monkeypatch)

    assert "Connections" in drawn
    lines = [text for text in drawn if "BALLOON" in text or "SAND TRAP" in text or "TEE BOX" in text]
    printed = "  ".join(text for text in drawn if text.isupper() and text not in ("VIPER",))
    assert lines and all(word in printed for word in board["words"])
    assert not any("MEDICINE" in text or "GOLF" in text for text in drawn)
