import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.cn_hot_search import sources  # noqa: E402
from plugins.cn_hot_search.cn_hot_search import list_moves  # noqa: E402
from plugins.cn_hot_search.render import (  # noqa: E402
    _slots, ago_text, heat_text, lead_facts, lead_moves, render_page, row_count, row_note, span_text, story_since,
)

NOW = datetime(2026, 10, 28, 21, 30, tzinfo=sources.PACIFIC)
# A ten-minute boundary at the start of all two-/three-/four-/five-card cycles.
TURN_START = datetime(2026, 10, 28, 17, 0, tzinfo=sources.PACIFIC)


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
    assert rows[0]["since"] == 0
    timed = sources.normalize_weibo({"data": {"realtime": [{"word": "哀悼", "onboard_time": 1793260000}]}})["rows"]
    assert timed[0]["since"] == 1793260000


def test_the_lead_says_how_long_it_has_run_how_it_moved_and_what_the_platforms_add():
    started = int(NOW.timestamp()) - 8 * 3600
    story = {"title": "发布会", "ranks": {"douyin": 1, "zhihu": 3, "weibo": 2},
             "rows": {"douyin": {"title": "发布会", "videos": 7, "since": started},
                      "zhihu": {"title": "如何看待发布会", "answers": 148},
                      "weibo": {"title": "发布会", "category": "社会", "since": started + 3600}}}

    assert story_since(story) == started
    assert lead_facts(story, NOW) == "发酵 8小时 · 抖音 7 个视频 · 知乎 148 回答 · 微博 · 社会"
    assert lead_facts(story, NOW, since=False) == "抖音 7 个视频 · 知乎 148 回答 · 微博 · 社会"
    assert (span_text(0, NOW), ago_text(started, NOW)) == ("", "8小时前")
    payload = {"lists": {"douyin": [{"title": "发布会", "rank": 1}], "zhihu": [{"title": "如何看待发布会", "rank": 3}]},
               "history": {"douyin": {"previous": [{"title": "发布会", "rank": 4}]},
                           "zhihu": {"previous": [{"title": "别的", "rank": 1}]}}}
    assert lead_moves(payload, story) == {"douyin": 3, "zhihu": "new"}


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
    # Zhihu's sentence leads; its answer count follows it with the heat and tags.
    assert row_note("zhihu", {"answers": 198, "summary": "近日"}, NOW) == "近日"
    assert row_note("zhihu", {"answers": 198}, NOW) == "198回答"
    assert (row_count("zhihu", {"answers": 198, "summary": "近日"}), row_count("zhihu", {"answers": 198})) == ("198回答", "")
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


def test_an_entry_without_a_picture_shows_an_illustration_of_its_topic():
    from plugins.cn_hot_search import topic_art

    assert topic_art.topic("A股磨底阶段如何应对", "社会") == "finance"
    assert topic_art.topic("国防部回应美日近期军演") == "military"
    assert topic_art.topic("中国U17女足首进世界杯8强") == "sports"
    assert topic_art.topic("养猫后明白了辱追心理") == "health"
    assert topic_art.topic("某地发生一件事", "娱乐") == "entertainment"
    assert topic_art.topic("某地发生一件事") == topic_art.DEFAULT_TOPIC
    keys = {key for key, _words in topic_art.TITLE_TOPICS} | set(topic_art.CATEGORY_TOPICS.values())
    assert topic_art.picture("", "") is not None
    assert {path.stem for path in topic_art.TOPIC_DIR.glob("*.jpg")} == keys | {topic_art.DEFAULT_TOPIC}


def test_a_row_title_steps_down_to_fit_and_keeps_the_move_room_only_for_a_move():
    from plugins.cn_hot_search import render

    assert render._has_move("new") and render._has_move(-2) and not render._has_move(0) and not render._has_move(None)
    lists = {platform: [{"rank": rank, "title": "中共中央30日上午举行新闻发布会", "heat": 1_000_000, "image": None}
                        for rank in range(1, 4)] for platform in sources.PLATFORMS}
    lists = {"weibo": lists["douyin"], **lists}
    payload = {"lists": lists, "topics": sources.cross_topics(lists), "history": {}}
    drawn = []
    from utils.page_canvas import PageCanvas

    original = PageCanvas.text

    def capture(self, xy, text, size=14, color="ink", **kwargs):
        drawn.append((str(text), size))
        return original(self, xy, text, size, color, **kwargs)

    PageCanvas.text = capture
    try:
        render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})
    finally:
        PageCanvas.text = original
    # In the narrow 3-column cards the whole title fits at a smaller size instead of being cut.
    assert ("中共中央30日上午举行新闻发布会", 12) in drawn or ("中共中央30日上午举行新闻发布会", 13) in drawn


def test_a_rows_sentence_comes_before_its_heat_and_tags():
    lists = {platform: [{"rank": rank, "title": "某话题", "heat": 7_810_000, "tag": "新",
                         "summary": "10月29日，重庆知识产权局发布消息", "image": None} for rank in range(1, 4)]
             for platform in sources.PLATFORMS}
    payload = {"lists": lists, "topics": [], "history": {}}
    drawn = []
    from utils.page_canvas import PageCanvas

    original = PageCanvas.text

    def capture(self, xy, text, size=14, color="ink", **kwargs):
        drawn.append((str(text), xy[0], xy[1]))
        return original(self, xy, text, size, color, **kwargs)

    PageCanvas.text = capture
    try:
        render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})
    finally:
        PageCanvas.text = original
    # On every row the sentence starts left of the heat figure on its line.
    pairs = [(note, heat) for note in drawn if note[0].startswith("10月29日")
             for heat in drawn if heat[0] == "781万" and abs(heat[2] - note[2]) < 1 and (heat[1] < 400) == (note[1] < 400)]
    assert len(pairs) >= 4 and all(note[1] < heat[1] for note, heat in pairs)


def test_a_row_without_a_sentence_keeps_its_heat_and_tags_first():
    lists = {platform: [{"rank": rank, "title": "某话题", "heat": 12_150_000, "tag": "热", "videos": 7, "image": None}
                        for rank in range(1, 4)] for platform in sources.PLATFORMS}
    drawn = []
    from utils.page_canvas import PageCanvas

    original = PageCanvas.text

    def capture(self, xy, text, size=14, color="ink", **kwargs):
        drawn.append((str(text), xy[0], xy[1]))
        return original(self, xy, text, size, color, **kwargs)

    PageCanvas.text = capture
    try:
        render_page((800, 480), {"lists": lists, "topics": [], "history": {}}, {"_inkypi_theme": {"mode": "day"}}, NOW, {})
    finally:
        PageCanvas.text = original
    # Douyin's line is its videos: the heat leads it.
    pairs = [(heat, note) for note in drawn if note[0].startswith("7个视频")
             for heat in drawn if heat[0] == "1215万" and abs(heat[2] - note[2]) < 1 and (heat[1] < 400) == (note[1] < 400)]
    assert pairs and all(heat[1] < note[1] for heat, note in pairs)


def _board(*rows):
    return {"data": {"cards": [{"content": list(rows)}]}}


def test_baidus_other_boards_give_each_entry_the_fact_that_names_it():
    teleplay = sources.normalize_baidu_board(_board(
        {"word": "九门", "hotScore": "95471", "show": ["类型：民国、传奇", "演员：陈伟霆\xa0、曾舜晞\xa0、陈瑶"]},
        {"word": "非份之罪", "hotScore": "93247", "hotChange": "up", "show": ["类型：剧情、悬疑"]}), "teleplay")
    car = sources.normalize_baidu_board(_board(
        {"word": "捷达VS8", "hotScore": "6121997", "hotChange": "up", "show": ["价格：11.99-13.79万", "级别：中型SUV"]}), "car")
    novel = sources.normalize_baidu_board(_board({"word": "绍宋", "show": ["作者：榴弹怕水", "类型：历史"]}), "novel")

    assert teleplay[0]["facts"] == {"类型": "民国、传奇", "演员": "陈伟霆、曾舜晞、陈瑶"}
    # A drama by its lead actor (its genre when no actor is given), a car by its starting price.
    assert [row["detail"] for row in teleplay] == ["陈伟霆", "剧情"]
    assert (teleplay[1]["change"], car[0]["detail"], novel[0]["detail"]) == ("up", "11.99万起", "榴弹怕水")
    with pytest.raises(ValueError):
        sources.normalize_baidu_board(_board(), "movie")


def test_a_board_that_fails_leaves_only_itself_out(monkeypatch):
    asked = []

    def fake_get(url, context, *, max_bytes, params=None, headers=None):
        asked.append(params["tab"])
        if params["tab"] == "novel":
            raise RuntimeError("refused")
        return _board({"word": f"{params['tab']} 第一", "show": []})

    monkeypatch.setattr(sources, "_get", fake_get)
    boards = sources.fetch_baidu_boards(None)["boards"]

    assert asked == list(sources.BAIDU_BOARDS)
    assert set(boards) == {"teleplay", "movie", "game", "car"} and sources.valid_boards({"boards": boards})
    monkeypatch.setattr(sources, "_get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(ValueError):
        sources.fetch_baidu_boards(None)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_boards_strip_sits_under_the_lead_and_the_grid_moves_down(mode, monkeypatch):
    from PIL import ImageDraw

    from plugins.cn_hot_search import render

    lists = {platform: [{"rank": 1, "title": f"{platform}话题", "heat": 1000, "image": None}] for platform in sources.PLATFORMS}
    boards = {"teleplay": [{"title": "九门", "detail": "陈伟霆", "change": None}],
              "car": [{"title": "捷达VS8", "detail": "11.99万起", "change": "up"}]}
    drawn, boxes = [], []
    original = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text",
                        lambda self, xy, text, *a, **k: drawn.append(str(text)) or original(self, xy, text, *a, **k))
    real_boxes = render._card_boxes
    monkeypatch.setattr(render, "_card_boxes", lambda count, top=render.GRID_TOP: boxes.append(top) or real_boxes(count, top))

    payload = {"lists": lists, "topics": [], "history": {}}
    render_page((800, 480), {**payload, "boards": boards}, {"_inkypi_theme": {"mode": mode}}, NOW, {})
    assert {"分榜", "剧", "九门", "陈伟霆", "车", "捷达VS8", "11.99万起"} <= set(drawn)
    assert "影" not in drawn and boxes == [render.STRIP_GRID_TOP]
    # Without boards the page keeps its old grid.
    render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW, {})
    assert boxes[-1] == render.GRID_TOP


THEPAPER = {"resultCode": 1, "data": {"hotNews": [
    {"contId": "34422015", "name": "霍志静任北京人艺院长，冯远征不再担任", "praiseTimes": "47", "interactionNum": "166",
     "pic": "https://imgpai.thepaper.cn/newpai/image/1793966581311_k5DwND.jpg", "pubTimeLong": "1793966911741",
     "nodeInfo": {"nodeId": 25423, "name": "人事风向"}},
    {"contId": "34418620", "name": "国家卫健委原党组成员主动投案", "praiseTimes": "284", "interactionNum": "62",
     "pic": "https://example.com/x.jpg", "pubTimeLong": "1793930484087", "nodeInfo": {"name": "打虎记"}},
    {"contId": "1", "name": ""}]}}
BLUESKY = {"trends": [
    {"topic": "godzilla", "displayName": "Godzilla Minus Zero opens", "postCount": 2050, "status": "hot",
     "category": "entertainment", "startedAt": "2026-11-03T02:04:25+00:00"},
    {"topic": "tesla", "displayName": "Tesla halts Cybertruck production", "postCount": 5517, "status": "cooling",
     "category": "business"}]}
MASTODON = [
    {"title": "Global stocks tumble as AI panic grips markets", "provider_name": "the Guardian",
     "image": "https://files.mastodon.social/cache/preview_cards/images/213/705/536/original/43e.jpg",
     "history": [{"day": "1793923200", "uses": "120", "accounts": "100"}, {"day": "1793836800", "uses": "58"}]},
    {"title": "Protesters break into Microsoft AI data centre site", "provider_name": "Al Jazeera", "image": None,
     "history": [{"day": "1793923200", "uses": "112"}]}]


def test_the_papers_hot_list_reads_its_likes_column_and_picture():
    rows = sources.normalize_thepaper(THEPAPER)["rows"]

    assert [(row["rank"], row["title"]) for row in rows] == [
        (1, "霍志静任北京人艺院长，冯远征不再担任"), (2, "国家卫健委原党组成员主动投案")]
    assert (rows[0]["heat"], rows[0]["comments"], rows[0]["summary"], rows[0]["since"]) == (47, 166, "人事风向", 1793966911)
    assert rows[0]["image"].startswith("https://imgpai.thepaper.cn/") and rows[1]["image"] is None
    assert sources.valid_rows({"rows": rows})


def test_the_overseas_talk_takes_turns_between_bluesky_and_mastodon():
    rows = sources.normalize_overseas(BLUESKY, MASTODON)["rows"]

    assert [(row["source"], row["title"]) for row in rows] == [
        ("bluesky", "Godzilla Minus Zero opens"), ("mastodon", "Global stocks tumble as AI panic grips markets"),
        ("bluesky", "Tesla halts Cybertruck production"), ("mastodon", "Protesters break into Microsoft AI data centre site")]
    assert [row["rank"] for row in rows] == [1, 2, 3, 4]
    # Bluesky's posts, its trend's state and its category in Chinese; Mastodon's shares and outlet.
    assert (rows[0]["heat"], rows[0]["tag"], rows[0]["summary"]) == (2050, "热", "娱乐")
    assert (rows[2]["tag"], rows[2]["summary"]) == (None, "商业")
    assert (rows[1]["heat"], rows[1]["summary"]) == (178, "the Guardian")
    assert rows[1]["image"].startswith("https://files.mastodon.social/") and rows[3]["image"] is None
    assert sources.normalize_overseas({}, MASTODON)["rows"][0]["source"] == "mastodon"
    with pytest.raises(ValueError):
        sources.normalize_overseas({}, [])


def test_the_spare_card_takes_ten_minute_turns_with_the_papers_list_and_the_overseas_talk():
    lists = {platform: [] for platform in ("weibo",) + sources.PLATFORMS}
    extras = {"thepaper": sources.normalize_thepaper(THEPAPER)["rows"],
              "overseas": sources.normalize_overseas(BLUESKY, MASTODON)["rows"]}
    payload = {"lists": lists, "extras": extras}

    assert [_slots(payload, TURN_START + timedelta(minutes=minute))[-1] for minute in (0, 10, 20)] == ["shared", "thepaper", "overseas"]
    # A card with nothing at hand gives its turns to the rest.
    only = {"lists": lists, "extras": {"overseas": extras["overseas"]}}
    assert [_slots(only, TURN_START + timedelta(minutes=minute))[-1] for minute in (0, 10)] == ["shared", "overseas"]
    # Without Weibo's list the two spare places of a 3 x 2 grid go to two of them in turn.
    four = {"lists": {platform: [] for platform in sources.PLATFORMS}, "extras": extras}
    assert _slots(four, TURN_START + timedelta(minutes=10)) == [*sources.PLATFORMS, "thepaper", "overseas"]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_turn_cards_name_their_sources_and_list_their_stories(monkeypatch, mode):
    from plugins.cn_hot_search import render

    drawn, logos = [], []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda self, xy, text, *args, **kwargs: drawn.append(text) or original(
        self, xy, text, *args, **kwargs))
    original_logo = render._paste_logo
    monkeypatch.setattr(render, "_paste_logo", lambda canvas, platform, *args, **kwargs: logos.append(platform) or
                        original_logo(canvas, platform, *args, **kwargs))
    lists = {platform: [{"rank": 1, "title": f"{platform}的话题", "heat": 100, "image": None}]
             for platform in sources.PLATFORMS}
    payload = {"lists": lists, "topics": [], "history": {},
               "extras": {"thepaper": sources.normalize_thepaper(THEPAPER)["rows"],
                          "overseas": sources.normalize_overseas(BLUESKY, MASTODON)["rows"]}}

    render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, TURN_START + timedelta(minutes=10), {})

    assert {"thepaper", "bluesky", "mastodon"} <= set(logos)
    assert {"澎湃热榜", "海外热议", "霍志静任北京人艺院长，冯远征不再担任", "Godzilla Minus Zero opens", "人事风向"} <= set(drawn)


def test_the_turn_cards_are_extras_outside_the_pages_provenance(monkeypatch, tmp_path):
    from plugins.cn_hot_search import cn_hot_search as module
    from runtime.refresh_contracts import TaskContext
    import time

    monkeypatch.setenv("CN_HOT_SEARCH_CACHE_DIR", str(tmp_path))
    rows = {"rows": [{"rank": 1, "title": "话题", "heat": 1, "image": None}]}
    monkeypatch.setattr(sources, "FETCHERS", {platform: (lambda ctx: rows) for platform in sources.PLATFORMS})
    monkeypatch.setattr(sources, "fetch_baidu_boards", lambda ctx: {"boards": {}})
    monkeypatch.setattr(sources, "EXTRA_FETCHERS", {
        "thepaper": lambda ctx: sources.normalize_thepaper(THEPAPER),
        "overseas": lambda ctx: (_ for _ in ()).throw(RuntimeError("down"))})
    plugin = module.CnHotSearch({"id": "cn_hot_search"})

    payload = plugin._payload({}, NOW, allow_network=True,
                              context=TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30))

    assert payload["extras"]["thepaper"][0]["title"].startswith("霍志静") and payload["extras"]["overseas"] == []
    assert "thepaper" not in payload["lists"] and payload["provenance"] == "live"


def test_the_overseas_titles_read_in_chinese_once_translated(monkeypatch, tmp_path):
    from plugins.cn_hot_search import cn_hot_search as module
    from plugins.cn_hot_search import render
    from runtime.refresh_contracts import TaskContext
    import time

    monkeypatch.setenv("CN_HOT_SEARCH_CACHE_DIR", str(tmp_path))
    asked = []
    monkeypatch.setattr(module.translate, "translate", lambda items, key, **kwargs: asked.append(
        (key, [item["title"] for item in items])) or [{"id": item["id"], "title": f"译：{item['title'][:6]}",
                                                        "summary": ""} for item in items])

    class Device:
        def load_env_key(self, key):
            return "sk-test" if key == "OPEN_AI_SECRET" else None

    plugin = module.CnHotSearch({"id": "cn_hot_search"})
    rows = sources.normalize_overseas(BLUESKY, MASTODON)["rows"]
    payload = {"extras": {"overseas": rows}}
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 60)

    plugin._translate_overseas(payload, Device(), allow_network=True, context=context)

    assert asked[0][0] == "sk-test" and asked[0][1][0] == "Godzilla Minus Zero opens"
    assert rows[0]["title_zh"] == "译：Godzil"
    # Kept: a cache-only redraw and the next refresh read the same Chinese title without asking.
    fresh = sources.normalize_overseas(BLUESKY, MASTODON)["rows"]
    plugin._translate_overseas({"extras": {"overseas": fresh}}, Device(), allow_network=False, context=context)
    assert fresh[0]["title_zh"] == "译：Godzil" and len(asked) == 1
    assert render.row_title(fresh[0]) == "译：Godzil" and render.row_title({"title": "x"}) == "x"


ZHWIKI = {"mostread": {"date": "2026-11-04Z", "articles": [
    {"title": "Wikipedia:首页", "normalizedtitle": "Wikipedia:首页", "views": 900000, "rank": 1, "namespace": {"id": 4}},
    {"title": "美國中期選舉", "normalizedtitle": "美國中期選舉", "views": 42723, "rank": 2, "namespace": {"id": 0},
     "description": "美國定期選舉之一",
     "thumbnail": {"source": "https://thumb.wikimedia.org/wikipedia/commons/thumb/5/5b/Arms.svg/330px-Arms.svg.png"}},
    {"title": "嫁金钗", "normalizedtitle": "嫁金钗", "views": 42618, "rank": 3, "namespace": {"id": 0},
     "description": "2026年中国电视剧"}]}}


def test_chinese_wikipedias_most_read_reads_in_simplified_chinese_with_its_views():
    rows = sources.normalize_zhwiki(ZHWIKI)["rows"]

    # Only articles: the main page and other namespaces are left out.
    assert [(row["rank"], row["title"]) for row in rows] == [(1, "美国中期选举"), (2, "嫁金钗")]
    assert (rows[0]["heat"], rows[0]["summary"]) == (42723, "美国定期选举之一")
    assert rows[0]["image"].startswith("https://thumb.wikimedia.org/") and rows[1]["image"] is None
    with pytest.raises(ValueError):
        sources.normalize_zhwiki({"tfa": {}})


def test_the_most_read_card_takes_its_turn_under_the_wikipedia_mark(monkeypatch):
    from plugins.cn_hot_search import render

    lists = {platform: [] for platform in ("weibo",) + sources.PLATFORMS}
    extras = {"zhwiki": sources.normalize_zhwiki(ZHWIKI)["rows"]}
    hours = {render._slots({"lists": lists, "extras": extras}, TURN_START + timedelta(minutes=10 * turn))[-1]
             for turn in range(4)}
    assert hours == {"shared", "zhwiki"}

    drawn, logos = [], []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda self, xy, text, *args, **kwargs: drawn.append(text) or original(
        self, xy, text, *args, **kwargs))
    original_logo = render._paste_logo
    monkeypatch.setattr(render, "_paste_logo", lambda canvas, platform, *args, **kwargs: logos.append(platform) or
                        original_logo(canvas, platform, *args, **kwargs))
    four = {platform: [{"rank": 1, "title": f"{platform}的话题", "heat": 100, "image": None}]
            for platform in sources.PLATFORMS}
    render_page((800, 480), {"lists": four, "topics": [], "history": {}, "extras": extras},
                {"_inkypi_theme": {"mode": "day"}}, NOW, {})

    assert "wikipedia_zh" in logos and {"维基热读", "美国中期选举", "4.3万次浏览"} <= set(drawn)


def test_a_platforms_own_label_is_a_smaller_chip_than_the_shared_one():
    from plugins.cn_hot_search import render

    canvas = render.PageCanvas(render._palette({"_inkypi_theme": {"mode": "day"}}))
    paper = canvas.image.getpixel((0, 0))
    solid = render._chip(canvas, 20, 30, "热", (224, 35, 35))
    shared = render._chip(canvas, 80, 30, "全网2", render.SHARED, outline=True)

    # 热 / 新 / 升: 8 px type on an 11 px chip; the outlined "on N lists" chip keeps its 14 px.
    assert solid == canvas.width("热", 8) + 6 and shared == canvas.width("全网2", 9) + 8
    column = [canvas.image.getpixel((22, y)) for y in range(15, 45)]
    assert sum(pixel != paper for pixel in column) == 12
    outline = [canvas.image.getpixel((80 + shared // 2, y)) for y in range(15, 45)]
    assert outline.index(next(p for p in outline if p != paper)) == 30 - 7 - 15


# Taken from tieba.baidu.com/hottopic/browse/topicList on 2026-11-11 (two topics of its thirty).
TIEBA = {"errno": 0, "errmsg": "success", "data": {"bang_topic": {"module_title": "", "topic_list": [
    {"topic_id": 28369747, "topic_name": "吧友连续14年盖楼支持诺基亚",
     "topic_desc": "诺记吧老哥自2012年起盖楼打卡支持诺基亚，每天一句“我觉得，对吧，我还是支持诺基亚”，帖子历经14年仍在更新。",
     "topic_pic": "https://tiebapic.baidu.com/forum/pic/item/c8177f3e6709c93d77a64affd93df8dcd1005465.jpg"
                  "?tbpicau=2026-11-23-05_c6378c7d8e6a8e7362bdb2fb972d2931",
     "tag": 2, "discuss_num": 2754030, "idx_num": 1, "create_time": 1794363945, "content_num": 0,
     "topic_avatar": "https://tiebapic.baidu.com/forum/whfpf%3D84%2C88%2C40%3Bq%3D90/sign=538e47e9692eb938ec3829b2b35fbd01"
                     "/c8177f3e6709c93d77a64affd93df8dcd1005465.jpg?tbpicau=2026-11-23-05_17a9e87131842c8189109d281b78f628",
     "is_video_topic": "0",
     "topic_url": "https://tieba.baidu.com/hottopic/browse/hottopic?topic_id=28369747&amp;topic_name=%E5%90%A7",
     "topic_default_avatar": "https://tb1.bdstatic.com/tb/%E8%AF%9D%E9%A2%98%E6%89%93%E5%BA%95%E5%9B%BE.PNG"},
    {"topic_id": 28369762, "topic_name": "樊振东祝贺LPL进决赛", "topic_desc": "樊振东发文祝贺LPL闯入纽约决赛，一起为LPL加油吧！",
     "topic_pic": "https://example.com/elsewhere.jpg", "tag": 0, "discuss_num": 822302, "idx_num": 5,
     "create_time": 1794379809, "topic_avatar": "https://example.com/elsewhere-small.jpg"},
    {"topic_id": 1, "topic_name": " ", "discuss_num": 5}]}, "timestamp": 1794430190759}}


def test_tiebas_hot_topics_read_their_discussions_their_sentence_and_their_small_picture():
    rows = sources.normalize_tieba(TIEBA)["rows"]

    assert [(row["rank"], row["title"]) for row in rows] == [(1, "吧友连续14年盖楼支持诺基亚"), (2, "樊振东祝贺LPL进决赛")]
    assert (rows[0]["heat"], rows[0]["since"], rows[0]["tag"]) == (2754030, 1794363945, None)
    assert rows[0]["summary"].startswith("诺记吧老哥自2012年起盖楼打卡支持诺基亚")
    # The 84 x 88 cover the hot list itself shows (4 KB), not the full picture; only from Tieba's host.
    assert rows[0]["image"].startswith("https://tiebapic.baidu.com/forum/whfpf%3D84%2C88%2C40")
    assert rows[0]["image"].endswith("tbpicau=2026-11-23-05_17a9e87131842c8189109d281b78f628")
    assert rows[1]["image"] is None
    assert sources.valid_rows({"rows": rows})
    for broken in ({}, {"data": []}, {"data": {"bang_topic": {"topic_list": "busy"}}}, {"errno": 110, "data": None}):
        with pytest.raises(ValueError):
            sources.normalize_tieba(broken)


def test_tiebas_entities_are_read_as_text():
    payload = {"data": {"bang_topic": {"topic_list": [
        {"topic_name": "A&amp;B 联名", "topic_desc": "吧友说&quot;真香&quot;", "discuss_num": "12,345"}]}}}
    row = sources.normalize_tieba(payload)["rows"][0]

    assert (row["title"], row["summary"], row["heat"]) == ("A&B 联名", "吧友说\"真香\"", 12345)


def test_tieba_takes_its_turn_in_the_spare_card_under_its_own_logo(monkeypatch):
    from plugins.cn_hot_search import render

    lists = {platform: [] for platform in ("weibo",) + sources.PLATFORMS}
    extras = {"thepaper": sources.normalize_thepaper(THEPAPER)["rows"],
              "overseas": sources.normalize_overseas(BLUESKY, MASTODON)["rows"],
              "zhwiki": sources.normalize_zhwiki(ZHWIKI)["rows"], "tieba": sources.normalize_tieba(TIEBA)["rows"]}
    # Ten minutes in fifty with every turn card at hand; the others keep their order.
    turns = [render._slots({"lists": lists, "extras": extras}, TURN_START + timedelta(minutes=10 * turn))[-1] for turn in range(5)]
    assert turns == ["shared", "thepaper", "overseas", "zhwiki", "tieba"]
    # Without Tieba's list the page turns exactly as before.
    without = {key: value for key, value in extras.items() if key != "tieba"}
    assert [render._slots({"lists": lists, "extras": without}, TURN_START + timedelta(minutes=10 * turn))[-1] for turn in range(4)] == [
        "shared", "thepaper", "overseas", "zhwiki"]

    drawn, logos = [], []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda self, xy, text, *args, **kwargs: drawn.append(text) or original(
        self, xy, text, *args, **kwargs))
    original_logo = render._paste_logo
    monkeypatch.setattr(render, "_paste_logo", lambda canvas, platform, *args, **kwargs: logos.append(platform) or
                        original_logo(canvas, platform, *args, **kwargs))
    filled = {platform: [{"rank": 1, "title": f"{platform}的话题", "heat": 100, "image": None}]
              for platform in ("weibo",) + sources.PLATFORMS}
    for mode in ("day", "night"):
        image = render_page((800, 480), {"lists": filled, "topics": [], "history": {}, "extras": {"tieba": extras["tieba"]}},
                            {"_inkypi_theme": {"mode": mode}}, TURN_START + timedelta(minutes=10), {})
        assert image.size == (800, 480)

    assert "tieba" in logos and render._logo_art("tieba", render.LOGO_HEIGHTS["tieba"], False) is not None
    assert {"热议榜", "吧友连续14年盖楼支持诺基亚", "275万讨论", "樊振东祝贺LPL进决赛", "82.2万讨论"} <= set(drawn)
    # Each topic's sentence follows its discussion count on the row's second line.
    assert any(str(text).startswith("诺记吧老哥自2012年起") for text in drawn)
    # Tieba is a turn card: it never joins the header's key or the lead's grid of lists.
    assert "tieba" not in sources.ORDER and logos.count("tieba") == 2


def test_tiebas_list_is_kept_an_hour_survives_a_failed_refresh_and_never_fails_the_page(monkeypatch, tmp_path):
    from datetime import timedelta

    from plugins.cn_hot_search import cn_hot_search as module
    from runtime.refresh_contracts import TaskContext
    import time

    monkeypatch.setenv("CN_HOT_SEARCH_CACHE_DIR", str(tmp_path))
    rows = {"rows": [{"rank": 1, "title": "话题", "heat": 1, "image": None}]}
    monkeypatch.setattr(sources, "FETCHERS", {platform: (lambda ctx: rows) for platform in sources.PLATFORMS})
    monkeypatch.setattr(sources, "fetch_baidu_boards", lambda ctx: {"boards": {}})
    asked = []
    state = {"fail": False}

    def fetch_tieba(context):
        asked.append(1)
        if state["fail"]:
            raise RuntimeError("tieba is down")
        return sources.normalize_tieba(TIEBA)

    monkeypatch.setattr(sources, "EXTRA_FETCHERS", {"tieba": fetch_tieba})
    plugin = module.CnHotSearch({"id": "cn_hot_search"})

    def payload(now, allow_network=True):
        return plugin._payload({}, now, allow_network=allow_network,
                               context=TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30))

    first = payload(NOW)
    assert first["extras"]["tieba"][0]["title"] == "吧友连续14年盖楼支持诺基亚" and len(asked) == 1
    # Within the hour the kept list is read; a redraw from cache never asks, however old the list.
    for minute in (10, 20, 30, 40, 50):
        assert payload(NOW + timedelta(minutes=minute), allow_network=False)["extras"]["tieba"]
        assert len(asked) == 1
    assert payload(NOW + timedelta(minutes=40))["extras"]["tieba"] and len(asked) == 1
    assert payload(NOW + timedelta(hours=5), allow_network=False)["extras"]["tieba"] and len(asked) == 1
    # After the hour it is asked once more; a failure keeps the last good list and the page's health.
    state["fail"] = True
    later = payload(NOW + timedelta(hours=2))
    assert len(asked) == 2 and later["extras"]["tieba"][0]["heat"] == 2754030
    assert later["provenance"] == first["provenance"] == "live" and "tieba" not in later["lists"]
    # With nothing kept (a first failure) the card is simply not there.
    monkeypatch.setenv("CN_HOT_SEARCH_CACHE_DIR", str(tmp_path / "empty"))
    assert payload(NOW)["extras"]["tieba"] == []


def test_tieba_asks_its_hot_list_once(monkeypatch):
    asked = []
    monkeypatch.setattr(sources, "_get", lambda url, context, **kwargs: asked.append((url, kwargs)) or TIEBA)

    assert sources.fetch_tieba(None)["rows"][0]["heat"] == 2754030
    assert [url for url, _kwargs in asked] == ["https://tieba.baidu.com/hottopic/browse/topicList"]
    assert asked[0][1]["max_bytes"] <= 300 * 1024
    assert sources.EXTRA_FETCHERS["tieba"] is sources.fetch_tieba


def test_ten_minute_turns_hold_until_the_boundary_and_continue_across_midnight():
    payload = {"lists": {platform: [] for platform in ("weibo",) + sources.PLATFORMS},
               "extras": {name: [{"title": name}] for name in ("thepaper", "overseas", "zhwiki", "tieba")}}
    assert _slots(payload, TURN_START)[-1] == "shared"
    assert _slots(payload, TURN_START + timedelta(minutes=9, seconds=59))[-1] == "shared"
    assert _slots(payload, TURN_START + timedelta(minutes=10))[-1] == "thepaper"
    assert _slots(payload, TURN_START + timedelta(minutes=50))[-1] == "shared"
    midnight = datetime(2026, 11, 12, 0, 0, tzinfo=sources.PACIFIC)
    assert [_slots(payload, midnight + timedelta(minutes=minute))[-1] for minute in (-10, 0, 10)] == [
        "zhwiki", "tieba", "shared"]
