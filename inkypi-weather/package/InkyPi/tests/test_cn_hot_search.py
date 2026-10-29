import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.cn_hot_search import sources  # noqa: E402
from plugins.cn_hot_search.cn_hot_search import list_moves  # noqa: E402
from plugins.cn_hot_search.render import _slots, ago_text, heat_text, render_page, row_note  # noqa: E402

NOW = datetime(2026, 10, 28, 21, 30, tzinfo=sources.PACIFIC)


def test_baidu_skips_the_pinned_row_and_keeps_heat_summary_picture_and_move():
    payload = {"data": {"cards": [{"content": [
        {"isTop": True, "word": "置顶"},
        {"word": "GDP十强城市出炉", "hotScore": "7808034", "hotTag": "1", "desc": "前三季度数据",
         "img": "https://fyb-2.cdn.bcebos.com/hotboard_image/abc", "hotChange": "up"},
        {"query": "三丽鸥致歉", "hotScore": "7713610", "hotTag": "0", "img": "https://example.com/x.jpg"},
    ]}]}}
    rows = sources.normalize_baidu(payload)["rows"]

    assert [(row["rank"], row["title"]) for row in rows] == [(1, "GDP十强城市出炉"), (2, "三丽鸥致歉")]
    assert (rows[0]["heat"], rows[0]["tag"], rows[0]["change"], rows[0]["summary"]) == (7808034, "新", "up", "前三季度数据")
    assert rows[0]["image"].startswith("https://fyb-2.cdn.bcebos.com/")
    # A picture off the platforms' own hosts is never fetched.
    assert rows[1]["image"] is None


def test_toutiao_zhihu_and_douyin_read_their_heat_labels_and_pictures():
    toutiao = sources.normalize_toutiao({"data": [{"Title": "地铁环线开通", "HotValue": "25794006", "Label": "refuteRumors",
                                                   "Image": {"url": "https://p3-sign.toutiaoimg.com/a.jpeg"}}]})
    zhihu = sources.normalize_zhihu({"data": [{"target": {"title": "姆巴佩赔偿？", "excerpt": "近日…", "answer_count": 198},
                                               "detail_text": "1521 万热度", "debut": True,
                                               "children": [{"thumbnail": "https://pic1.zhimg.com/v2-49fa514cbeff.png"}]}]})
    douyin = sources.normalize_douyin({"data": {"word_list": [
        {"word": "白鹤滩水电站", "hot_value": 11696321, "label": 8, "video_count": 3, "event_time": 1793181653,
         "word_cover": {"url_list": ["https://p3-sign.douyinpic.com/cover.jpeg"]}}]}})
    billboard = sources.normalize_douyin({"word_list": [{"word": "秋日山野漫游", "hot_value": 12005162, "label": 3}]})

    assert (toutiao["rows"][0]["heat"], toutiao["rows"][0]["tag"]) == (25794006, "辟谣")
    row = zhihu["rows"][0]
    assert (row["heat"], row["tag"], row["answers"]) == (15210000, "新", 198)
    # Zhihu's original picture can be a 1.5 MB PNG: the page asks for its small size.
    assert row["image"] == "https://pic1.zhimg.com/v2-49fa514cbeff_xl.jpg"
    assert (douyin["rows"][0]["tag"], douyin["rows"][0]["videos"]) == ("独家", 3)
    assert douyin["rows"][0]["image"] == "https://p3-sign.douyinpic.com/cover.jpeg"
    assert (billboard["rows"][0]["tag"], billboard["rows"][0]["image"]) == ("热", None)
    with pytest.raises(ValueError):
        sources.normalize_toutiao({"data": []})


def test_weibo_needs_the_owners_cookie_and_skips_ads():
    assert sources.cookie_header("abc123") == "SUB=abc123"
    assert sources.cookie_header("SUB=abc; SUBP=xyz") == "SUB=abc; SUBP=xyz"
    rows = sources.normalize_weibo({"data": {"realtime": [
        {"word": "广告", "is_ad": 1}, {"word": "西班牙全国哀悼", "num": 861234, "label_name": "沸", "category": "社会"}]}})["rows"]

    assert [(row["rank"], row["title"], row["tag"]) for row in rows] == [(1, "西班牙全国哀悼", "沸")]


def test_the_same_story_on_several_lists_is_found_once():
    lists = {
        "baidu": [{"rank": 2, "title": "三丽鸥致歉"}, {"rank": 4, "title": "西班牙全国哀悼三天"}],
        "toutiao": [{"rank": 4, "title": "西班牙宣布全国哀悼3天"}, {"rank": 1, "title": "国内最长地铁环线全线开通"}],
        "zhihu": [{"rank": 6, "title": "如何看待三丽鸥致歉"}],
        "douyin": [{"rank": 6, "title": "三丽鸥致歉"}],
    }
    topics = sources.cross_topics(lists)

    assert [(topic["title"], topic["ranks"]) for topic in topics] == [
        ("三丽鸥致歉", {"baidu": 2, "zhihu": 6, "douyin": 6}), ("西班牙全国哀悼三天", {"baidu": 4, "toutiao": 4})]
    assert not sources.same_topic("西班牙高铁事故已致68死", "西班牙全国哀悼三天")


def test_list_moves_match_a_reworded_entry_and_mark_new_ones():
    previous = [{"title": "西班牙全国哀悼三天", "rank": 4}, {"title": "三丽鸥致歉", "rank": 1}]
    current = [{"title": "三丽鸥致歉", "rank": 2}, {"title": "西班牙宣布全国哀悼3天", "rank": 1}, {"title": "新话题", "rank": 3}]

    assert list_moves(current, previous) == {"三丽鸥致歉": -1, "西班牙宣布全国哀悼3天": 3, "新话题": "new"}


def test_heat_notes_and_layout_slots():
    assert (heat_text(7808034), heat_text(52300), heat_text(123456789)) == ("781万", "5.2万", "1.2亿")
    assert ago_text(int(NOW.timestamp()) - 3 * 3600, NOW) == "3小时前"
    assert row_note("zhihu", {"answers": 198, "summary": "近日"}, NOW) == "198回答 · 近日"
    assert row_note("douyin", {"videos": 3}, NOW) == "3个视频"
    four = {"lists": {platform: [] for platform in sources.PLATFORMS}}
    assert _slots(four) == list(sources.PLATFORMS)
    assert _slots({"lists": {"weibo": [], **four["lists"]}}) == ["weibo", *sources.PLATFORMS, "shared"]


@pytest.mark.parametrize("mode", ["day", "night"])
@pytest.mark.parametrize("weibo", [False, True])
def test_page_renders_without_pictures_in_both_grids(mode, weibo):
    def rows(prefix):
        return [{"rank": rank, "title": f"{prefix}热门话题第{rank}条，标题可能很长很长很长", "heat": 1_000_000 * rank,
                 "tag": "热" if rank == 1 else None, "summary": "平台给出的摘要", "image": None} for rank in range(1, 6)]
    lists = {platform: rows(platform) for platform in sources.PLATFORMS}
    if weibo:
        lists = {"weibo": rows("weibo"), **lists}
    payload = {"lists": lists, "topics": sources.cross_topics(lists), "history": {}}
    image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW, {})

    assert image.size == (800, 480)
    assert image.getbbox() is not None
