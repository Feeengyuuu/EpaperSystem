import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_pop import sources  # noqa: E402
from plugins.daily_pop.daily_pop import chart_moves  # noqa: E402
from plugins.daily_pop.render import earth_disc, render_page  # noqa: E402

NOW = datetime(2026, 10, 24, 10, 30, tzinfo=sources.PACIFIC)


def test_bilibili_keeps_titles_counts_and_https_covers():
    payload = {"data": {"list": [
        {"title": "  一个  视频 ", "owner": {"name": "UP"}, "tname": "知识", "pic": "http://i0.hdslb.com/a.jpg",
         "stat": {"view": 1234567, "like": "890"}, "rcmd_reason": {"content": "百万播放"}},
        {"title": ""},
    ]}}
    video = sources.normalize_bilibili(payload)["videos"][0]

    assert (video["title"], video["owner"], video["views"], video["likes"]) == ("一个 视频", "UP", 1234567, 890)
    assert video["cover"] == "https://i0.hdslb.com/a.jpg"
    assert sources.safe_image_url(video["cover"]) == video["cover"]
    assert sources.safe_image_url("https://example.com/a.jpg") is None
    # A video cover is fetched scaled to the page's size, never as a 2560 px original.
    cover = "http://i1.hdslb.com/bfs/archive/58b1.jpg"
    assert sources.safe_image_url(cover) == "https://i1.hdslb.com/bfs/archive/58b1.jpg@640w_360h_1c.jpg"
    assert sources.safe_image_url(cover + "@320w_180h.jpg") == "https://i1.hdslb.com/bfs/archive/58b1.jpg@320w_180h.jpg"
    with pytest.raises(ValueError):
        sources.normalize_bilibili({"data": {"list": []}})


def test_bangumi_takes_today_and_puts_the_most_watched_first():
    payload = [{"weekday": {"id": 6}, "items": [
        {"name": "Quiet", "rating": {"score": 9.1}, "collection": {"doing": 10}},
        {"name": "Loud", "name_cn": "热门", "rating": {"score": 7.0}, "collection": {"doing": 900}},
    ]}, {"weekday": {"id": 5}, "items": [{"name": "Friday"}]}]
    data = sources.normalize_bangumi(payload, 6)

    assert [show["name"] for show in data["shows"]] == ["热门", "Quiet"]
    assert data["count"] == 2


def test_most_read_skips_special_pages_and_puzzle_counts_the_mate():
    articles = sources.normalize_most_read({"mostread": {"articles": [
        {"title": "Special:Search", "views": 9}, {"title": "首页"}, {"normalizedtitle": "地球", "views": 50}]}})
    puzzle = sources.normalize_puzzle({"title": "Mate", "fen": "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1",
                                       "pgn": '[Event "x"]\n1. Rd8# 1-0', "image": "https://www.chess.com/x.png"})

    assert [row["title"] for row in articles["articles"]] == ["地球"]
    assert (puzzle["to_move"], puzzle["solution"], puzzle["mate_in"]) == ("w", "1. Rd8#", 1)
    with pytest.raises(ValueError):
        sources.normalize_puzzle({"fen": "not a position"})


def test_epic_frames_sort_and_the_globe_turns_with_the_time_of_day():
    rows = [{"image": f"epic_1b_20261023{hour:02d}0000", "date": f"2026-10-23 {hour:02d}:00:00",
             "centroid_coordinates": {"lat": 5, "lon": -15 * hour}} for hour in (20, 2, 14)]
    epic = sources.normalize_epic(rows)

    assert [frame["taken"][11:13] for frame in epic["frames"]] == ["02", "14", "20"]
    assert epic["frames"][0]["image"].endswith("/2026/10/23/jpg/epic_1b_20261023020000.jpg")
    # 10:30 Pacific is 17:30 UTC: the 20:00 frame is nearest by time of day.
    assert sources.epic_frame_index(epic, NOW) == 2
    assert sources.facing(5, -100).endswith("上空")
    assert sources.epic_frames_ahead(epic, NOW, 2) == [2, 0]
    assert sources.epic_frames_by_nearness(epic, NOW) == [2, 1, 0]


def test_a_cached_render_shows_the_nearest_cached_earth_not_an_empty_circle(tmp_path, monkeypatch):
    from plugins.daily_pop import daily_pop as module

    rows = [{"image": f"epic_1b_20261023{hour:02d}0000", "date": f"2026-10-23 {hour:02d}:00:00",
             "centroid_coordinates": {"lat": 0, "lon": 0}} for hour in (2, 14, 20)]
    epic = sources.normalize_epic(rows)
    cached = {epic["frames"][1]["image"]}  # only the 14:00 frame was fetched at the last refresh
    fetched = []

    def load(urls, _directory, *, cached_only, **_options):
        if not cached_only:
            fetched.extend(urls)
            cached.update(urls)
        return {url: Image.new("RGB", (4, 4)) for url in urls if url in cached}

    monkeypatch.setattr(module, "load_thumbnails", load)
    monkeypatch.setattr(module, "EPIC_PREFETCH", 2)
    plugin = module.DailyPop({"id": "daily_pop"})
    monkeypatch.setattr(plugin, "_cache_subdir", lambda name: tmp_path / name)

    # Now wants the 20:00 frame, which the cache lacks: the nearest cached one (14:00) stands in.
    index, url, picture = plugin._earth(epic, NOW, False, {})
    assert (index, url) == (1, epic["frames"][1]["image"]) and picture is not None
    # A refresh fetches the frame for now and the ones the clock turns to next.
    index, url, _picture = plugin._earth(epic, NOW, True, {})
    assert index == 2 and fetched == [epic["frames"][i]["image"] for i in (2, 0)]


def test_chart_moves_mark_new_entries_and_places_gained():
    previous = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    current = [{"name": "C"}, {"name": "A"}, {"name": "D"}]

    assert chart_moves(current, previous, "name") == {"C": 2, "A": -1, "D": "new"}


def test_earth_disc_cuts_the_globe_out_of_black_space():
    picture = Image.new("RGB", (200, 200), (0, 0, 0))
    picture.paste((40, 90, 160), (50, 50, 150, 150))
    disc = earth_disc(picture, 64)

    assert disc.size == (64, 64) and disc.mode == "RGBA"
    assert disc.getpixel((0, 0))[3] == 0 and disc.getpixel((32, 32))[3] == 255


def _payload():
    def ok(data):
        return {"data": data, "state": "live"}

    return {
        "bilibili": ok({"videos": [{"title": f"视频 {index} " * 4, "owner": "UP", "section": "知识", "views": 12345 * index,
                                    "likes": 10, "cover": "", "reason": ""} for index in range(1, 7)]}),
        "bangumi": ok({"weekday": 6, "count": 3, "shows": [{"name": "很长很长的新番名字" * 3, "score": 8.1, "votes": 10,
                                                           "watching": 100, "cover": ""}] * 3}),
        "songs": ok({"songs": [{"name": "Song " * 8, "artist": "Artist " * 6, "artwork": ""}] * 5}),
        "wiki": ok({"articles": [{"title": "条目" * 10, "views": 99999, "description": "说明" * 10}] * 6}),
        "puzzle": ok({"title": "Daily", "fen": "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", "to_move": "w",
                      "image": "", "solution": "1. Rd8#", "mate_in": 1}),
        "epic": ok({"frames": []}),
        "history": {"puzzle": {"previous": {"title": "Yesterday", "solution": "1. Qh7+ Kf8 2. Qh8# " * 3}}},
        "provenance": None,
    }


@pytest.mark.parametrize("mode", ["day", "night"])
def test_page_renders_long_text_without_any_images(mode):
    image = render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, NOW, {})

    assert image.size == (800, 480)
    assert len(image.getcolors(800 * 480)) > 4


def test_wiki_globe_is_a_lit_sphere_with_a_glow():
    from plugins.daily_pop.render import WIKI_GLOW_PAD, wiki_globe

    sphere = wiki_globe(94, False)

    assert sphere.size == (94 + 2 * WIKI_GLOW_PAD,) * 2
    lit, shaded = sphere.getpixel((40, 40)), sphere.getpixel((80, 84))
    # Light from the upper left: the near side brighter than the far side.
    assert sum(lit[:3]) > sum(shaded[:3])
    # The blue glow just outside the disc, on its right.
    assert sphere.getpixel((94 + WIKI_GLOW_PAD + 1, 53))[3] > 0
