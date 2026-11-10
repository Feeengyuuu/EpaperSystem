import json
import sys
import threading
import time
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_digest import daily_digest, render, sources  # noqa: E402
from plugins.daily_digest.daily_digest import DailyDigest  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402
from utils.page_canvas import PageCanvas  # noqa: E402

NOW = datetime(2026, 11, 2, 16, 30, tzinfo=sources.PACIFIC)
LRM = chr(0x200E)

COVER = "http://p2.music.126.net/ucdEaYKU_dL0U2ae-wSRCQ==/109951174184824841.jpg"
SOARING = {"code": 200, "playlist": {"id": 19723756, "name": "飙升榜", "updateTime": 1793666229302, "tracks": [
    {"id": 306675, "name": "明年依然好时节", "ar": [{"name": "许飞"}], "al": {"picUrl": COVER}},
    {"id": 3448738711, "name": "无 意", "ar": [{"name": "加木"}, {"name": "Demxntia"}],
     "al": {"picUrl": "https://evil.example/cover.jpg"}},
    {"id": 3451003254, "name": "宝船", "ar": [{"name": "DOUDOU"}], "al": {}},
    {"id": 1, "name": "第四首", "ar": [], "al": {"picUrl": COVER}},
]}}
POSTER = "https://img9.doubanio.com/view/photo/m_ratio_poster/public/p2935834896.jpg"
SHOWING = {"subject_collection_items": [
    {"title": "生日快乐", "rating": {"value": 8.9}, "year": "2025", "card_subtitle": "2025 / 埃及 / 剧情 / 萨拉 / 朵哈",
     "cover": {"url": POSTER}},
    {"title": f"神探之痕迹{LRM}", "rating": {"value": 6.7}, "year": "2026",
     "card_subtitle": "2026 / 中国大陆 中国香港 / 犯罪 惊悚 悬疑 / 陈思诚 / 张译",
     "pic": {"normal": "https://evil.example/poster.jpg"}},
    {"title": "三星堆：未来往事", "rating": None, "year": "2026", "card_subtitle": "2026 / 中国大陆 / 科幻 动画 / 越明"},
]}
RSS = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>纽约时报中文网</title>
<item><title><![CDATA[较早的一条]]></title><pubDate>Mon, 02 Nov 2026 08:00:00 +0800</pubDate></item>
<item><title><![CDATA[人工智能时代，美中关系将迎来重大变革]]></title><pubDate>Tue, 03 Nov 2026 07:27:48 +0800</pubDate></item>
<item><title><![CDATA[较早的一条]]></title><pubDate>Mon, 02 Nov 2026 07:00:00 +0800</pubDate></item>
</channel></rss>""".encode("utf-8")
RDF = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
 <channel rdf:about="https://rss.dw.com/rdf/rss-chi-all"><title>DW</title><link>https://www.dw.com/zh/</link></channel>
 <item rdf:about="https://www.dw.com/zh/a-1"><title>德國總理與美國國會</title><link>https://www.dw.com/zh/a-1</link>
  <dc:date>2026-11-02T15:45:00Z</dc:date></item>
</rdf:RDF>""".encode("utf-8")
# The table page's shape: a stub cell holding a nested table, then one cell per week, then history.
EIA_PAGE = """<table><tr><th class="Series5">09/21/26</th><th class="Series5">09/28/26</th>
<th class="Series5">10/05/26</th><th class="Series5">10/12/26</th><th class="Series5">10/19/26</th>
<th class="Series5">10/26/26</th><th class="Cross">View<br>History</th></tr>
<tr class="DataRow"><td class="DataStub"><table class="data2"><tr><td></td><td class="DataStub1">Gasoline - All Grades</td>
</tr></table></td><td class="DataB">6.112</td><td class="DataB">6.300</td><td class="DataB">6.335</td>
<td class="DataB">6.216</td><td class="DataB">6.145</td><td class="Current2">6.003</td>
<td class="DataHist"><a href="./hist/LeafHandler.ashx?n=PET&s=EMM_EPM0_PTE_SCA_DPG&f=W">2000-2026</a></td></tr>
<tr class="DataRow"><td class="DataStub"><table class="data2"><tr><td></td><td class="DataStub1">Regular</td>
</tr></table></td><td class="DataB">6.003</td><td class="DataB">6.189</td><td class="DataB">6.227</td>
<td class="DataB">6.104</td><td class="DataB">NA</td><td class="Current2">5.888</td>
<td class="DataHist"><a href="./hist/LeafHandler.ashx?n=PET&s=EMM_EPMR_PTE_SCA_DPG&f=W">2000-2026</a></td></tr>
</table>"""
WEBB_PAGE = """<div class="row news-list"><div class="col-lg-4"><a href="/images/potm2610a/">
<div class="news-image"><img src="https://cdn.esawebb.org/archives/images/thumb350x/potm2610a.jpg"></div>
<div class="news-title h6 text-capitalize text-primary mx-3 mt-2 fw-bold" style="height: 40px;">Stars brewing in a nebulous cauldron
</div><div class="news-teaser-potm small"><strong>22 October 2026</strong><br>In this Picture of the Month</div></a></div>
<div class="col-lg-4"><a href="/images/potm2609a/"><div class="news-title h6">Galaxies in a cosmic house of mirrors</div>
<strong>29 September 2026</strong></a></div></div>"""
HUBBLE_PAGE = """<a href="/images/potm2609a/"><div class="news-wrapper"><img class="img-responsive" alt="x">
<div class="news-title">A galaxy spinning out of sync</div><div class="news-teaser"><strong>18 September 2026</strong>:
Though the spiral galaxy</div></div></a>"""
DEBT = {"data": [
    {"record_date": "2026-10-28", "tot_pub_debt_out_amt": "40551054852362.41", "debt_held_public_amt": "32671924859186.91",
     "intragov_hold_amt": "7879129993175.50"},
    {"record_date": "2026-10-29", "tot_pub_debt_out_amt": "40593637262883.48", "debt_held_public_amt": "32712190190967.42",
     "intragov_hold_amt": "7881447071916.06"}]}
FEAR_GREED = {"data": [{"value": "26", "value_classification": "Fear", "timestamp": "1793664000"},
                       {"value": "51", "value_classification": "Neutral", "timestamp": "1793232000"},
                       {"value": "abc", "timestamp": "1793145600"}]}


def _context(seconds=30):
    return TaskContext(threading.Event(), time.monotonic() + seconds, time.monotonic)


def test_a_poem_reads_in_simplified_chinese_with_its_work_and_poet():
    poem = sources.normalize_poem({"hitokoto": " 對岸流莺闲不住，\n長歌戴月到江東。", "from": "浪淘沙·飞云湖月夜",
                                   "from_who": "徐清", "uuid": "u-1"})

    assert poem == {"text": "对岸流莺闲不住，长歌戴月到江东。", "work": "浪淘沙·飞云湖月夜", "author": "徐清", "id": "u-1"}
    assert render.poem_credit(poem) == "—— 徐清《浪淘沙·飞云湖月夜》"
    anonymous = sources.normalize_poem({"hitokoto": "执子之手，与子偕老。", "from": "击鼓", "from_who": "佚名"})
    assert anonymous["author"] == "" and render.poem_credit(anonymous) == "—— 《击鼓》"
    with pytest.raises(ValueError):
        sources.normalize_poem({"hitokoto": "  "})


def test_a_netease_chart_keeps_its_top_three_with_their_album_covers():
    chart = sources.normalize_chart(SOARING, 19723756)

    assert (chart["id"], chart["name"]) == (19723756, "飙升榜")
    assert chart["updated"].startswith("2026-11-03T")
    # Covers come over https at their 200 px size; one off NetEase's image CDN is dropped, the song kept.
    # Without trackIds the chart keeps no record of its last edition, so no song carries a move.
    blank = {"album": "", "published": "", "seconds": None}
    assert chart["tracks"] == [
        {"title": "明年依然好时节", "artist": "许飞",
         "cover": "https://p2.music.126.net/ucdEaYKU_dL0U2ae-wSRCQ==/109951174184824841.jpg?param=200y200", **blank},
        {"title": "无 意", "artist": "加木/Demxntia", "cover": None, **blank},
        {"title": "宝船", "artist": "DOUDOU", "cover": None, **blank}]
    assert sources.safe_music_image("https://p1.music.126.net.evil.example/x.jpg") is None
    with pytest.raises(ValueError):
        sources.normalize_chart({"code": 200, "playlist": {"tracks": []}}, 19723756)


def test_a_chart_song_carries_its_album_release_length_and_move():
    hot = {"code": 200, "playlist": {"id": 3778678, "name": "热歌榜", "tracks": [
        {"id": 11, "name": "Hong Kong (feat. 王菲季候风)", "ar": [{"name": "The Weeknd"}],
         "al": {"name": "Hong Kong (feat. 王菲季候风)"}, "dt": 216993, "publishTime": 1793376000000},
        {"id": 12, "name": "我不难过", "ar": [{"name": "孙燕姿"}], "al": {"name": "未完成"}, "dt": 320000,
         "publishTime": 1042128000000},
        {"id": 13, "name": "海屿你", "ar": [{"name": "马也_Crabbit"}], "al": {"name": "海屿你"}, "dt": 295000,
         "publishTime": 1660924800000},
        {"id": 14, "name": "无 意", "ar": [], "al": {"name": "无 意"}, "dt": 176000, "publishTime": 0},
    ], "trackIds": [{"id": 11, "lr": 2}, {"id": 12, "lr": 1}, {"id": 13}, {"id": 14, "lr": 3}]}}
    soaring = {"code": 200, "playlist": {"tracks": SOARING["playlist"]["tracks"][:2],
                                         "trackIds": [{"id": 306675, "lr": 18, "ratio": 446}, {"id": 3448738711}]}}

    tracks = sources.normalize_chart(hot, 3778678)["tracks"]
    # NetEase's last ranks count from 0; a song without one is new to the chart. Release days are
    # Beijing's; a publishTime of 0 means NetEase does not know.
    assert [(track["last_rank"], track["soaring"]) for track in tracks] == [(3, None), (2, None), (None, None)]
    assert [(track["album"], track["published"], track["seconds"]) for track in tracks] == [
        ("Hong Kong (feat. 王菲季候风)", "2026-10-31", 216), ("未完成", "2003-01-10", 320),
        ("海屿你", "2022-08-20", 295)]
    assert [render.song_move(track, rank) for rank, track in enumerate(tracks, 1)] == [
        ("up", "2"), None, ("new", "NEW")]
    assert render.song_move({**tracks[1], "last_rank": 1}, 2) == ("down", "1")
    # The soaring chart reads its rise in plays instead of its places.
    moved = sources.normalize_chart(soaring, 19723756)["tracks"]
    assert [render.song_move(track, rank) for rank, track in enumerate(moved, 1)] == [("up", "446%"), ("new", "NEW")]
    # A chart cached before the moves were read shows none.
    assert render.song_move({"title": "x"}, 1) is None

    # The album when the song is not its own single, else its release (the day while it is new).
    assert [render.song_note(track, NOW) for track in tracks] == [
        "10.31 发行 · 3:36", "《未完成》 · 5:20", "2022 发行 · 4:55"]
    assert render.song_note({"title": "x", "seconds": 61}, NOW) == "1:01"
    assert render.song_note({"title": "x"}, NOW) == ""


def test_the_charts_take_turns_by_the_hour_and_a_failed_one_gives_way():
    soaring = {"data": sources.normalize_chart(SOARING, 19723756)}
    new = {"data": {**soaring["data"], "id": 3779629, "name": "新歌榜"}}
    music = {19723756: soaring, 3779629: new, 2884035: {"data": {}}, 3778678: {"data": {}}}

    assert [render.music_chart(music, NOW.replace(hour=hour))["name"] for hour in (16, 17, 18)] == [
        "飙升榜", "新歌榜", "飙升榜"]
    assert render.music_chart({}, NOW) is None


def test_netease_reads_each_chart_cut_to_three_songs(monkeypatch):
    seen = {}

    class Client:
        def request_json(self, method, url, **kwargs):
            seen.update(url=url, **kwargs)
            return SimpleNamespace(data=SOARING)

    monkeypatch.setattr(sources, "get_http_client", Client)

    assert len(sources.fetch_chart(19723756, _context())["tracks"]) == 3
    assert seen["url"] == "https://music.163.com/api/v6/playlist/detail"
    assert seen["params"] == {"id": 19723756, "n": 3, "s": 0}


def test_douban_reads_ratings_regions_and_genres_without_format_marks():
    items = sources.normalize_douban(SHOWING)["items"]

    assert items[0] == {"title": "生日快乐", "rating": 8.9, "year": "2025", "region": "埃及", "genres": ["剧情"],
                        "poster": "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2935834896.jpg"}
    assert items[1]["title"] == "神探之痕迹" and items[1]["region"] == "中国大陆" and items[1]["genres"] == ["犯罪", "惊悚"]
    assert items[2]["rating"] is None
    # A poster off Douban's image CDN, or none at all, leaves the title without one.
    assert items[1]["poster"] is None and items[2]["poster"] is None
    with pytest.raises(ValueError):
        sources.normalize_douban({"subject_collection_items": []})


def test_douban_asks_with_its_mobile_page_as_referer(monkeypatch):
    seen = {}

    class Client:
        def request_json(self, method, url, **kwargs):
            seen.update(url=url, **kwargs)
            return SimpleNamespace(data=SHOWING)

    monkeypatch.setattr(sources, "get_http_client", Client)

    assert sources.fetch_douban("us_tv", _context())["items"]
    assert seen["url"].endswith("/subject_collection/tv_american/items")
    assert seen["headers"]["Referer"] == "https://m.douban.com/movie/"
    assert seen["params"] == {"start": 0, "count": 10}


def test_headlines_come_newest_first_once_each_from_rss_and_rdf():
    nyt = sources.normalize_news(RSS)["items"]
    dw = sources.normalize_news(RDF)["items"]

    assert [item["title"] for item in nyt] == ["人工智能时代，美中关系将迎来重大变革", "较早的一条"]
    assert nyt[0]["time"].startswith("2026-11-02T23:27:48")
    assert dw == [{"title": "德国总理与美国国会", "time": "2026-11-02T15:45:00+00:00"}]  # in Simplified Chinese
    with pytest.raises(ValueError):
        sources.normalize_news(b"<rss><channel></channel></rss>")


def test_eia_table_gives_california_regular_gasoline_by_week():
    weeks = sources.normalize_gas(EIA_PAGE)["weeks"]

    # The all-grades row above is not regular gasoline; a week EIA marks NA is skipped.
    assert weeks == [{"week": "2026-09-21", "price": 6.003}, {"week": "2026-09-28", "price": 6.189},
                     {"week": "2026-10-05", "price": 6.227}, {"week": "2026-10-12", "price": 6.104},
                     {"week": "2026-10-26", "price": 5.888}]
    assert sources.valid_gas({"weeks": weeks}) and not sources.valid_gas({"weeks": weeks[:1]})
    with pytest.raises(ValueError):
        sources.normalize_gas(EIA_PAGE.replace("EMM_EPMR_PTE_SCA_DPG", "OTHER"))


def test_the_pictures_of_the_month_come_from_either_sites_page_and_take_turns():
    webb = sources.normalize_space(WEBB_PAGE, "webb")
    hubble = sources.normalize_space(HUBBLE_PAGE, "hubble")

    assert webb["items"][0] == {"title": "Stars brewing in a nebulous cauldron", "code": "potm2610a", "time": "2026-10-22",
                                "image": "https://cdn.esawebb.org/archives/images/thumb700x/potm2610a.jpg"}
    assert [item["code"] for item in webb["items"]] == ["potm2610a", "potm2609a"]
    assert hubble["items"][0]["image"] == "https://cdn.esahubble.org/archives/images/thumb700x/potm2609a.jpg"
    assert hubble["items"][0]["time"] == "2026-09-18"
    even, odd = date(2026, 11, 2), date(2026, 11, 3)
    assert {even.toordinal() % 2, odd.toordinal() % 2} == {0, 1}
    picks = {sources.space_pick(webb, hubble, day)[0] for day in (even, odd)}
    assert picks == {"webb", "hubble"}
    # One whose page failed gives way to the other.
    assert sources.space_pick({}, hubble, even)[0] == "hubble" and sources.space_pick(webb, None, odd)[0] == "webb"
    assert sources.space_pick({}, {}, even) == (None, None)
    assert sources.safe_space_image("https://evil.example/x.jpg") is None
    with pytest.raises(ValueError):
        sources.normalize_space("<html></html>", "webb")


def test_the_debt_reads_its_total_its_day_change_and_who_holds_it():
    debt = sources.normalize_debt(DEBT)

    assert debt["date"] == "2026-10-29" and debt["previous_date"] == "2026-10-28"
    assert round(debt["total"] - debt["previous_total"]) == 42582410521
    assert render.money_text(debt["total"]) == "$40.59 万亿"
    assert render.money_text(debt["total"] - debt["previous_total"]) == "$425.8 亿"
    assert round(debt["public"] + debt["intragov"]) == round(debt["total"])
    with pytest.raises(ValueError):
        sources.normalize_debt({"data": [{"record_date": "2026-10-29", "tot_pub_debt_out_amt": "n/a"}]})


def test_fear_and_greed_keeps_its_days_newest_first_with_chinese_names():
    days = sources.normalize_fear_greed(FEAR_GREED)["days"]

    assert days == [{"value": 26, "name": "恐惧", "day": "2026-11-03"}, {"value": 51, "name": "中性", "day": "2026-10-29"}]
    assert [render.fear_greed_band(value) for value in (0, 24, 25, 46, 47, 54, 55, 75, 76, 100)] == [
        0, 0, 1, 1, 2, 2, 3, 3, 4, 4]


def test_wordle_is_yesterdays_answer_never_todays(monkeypatch):
    late = datetime(2026, 11, 2, 23, 59, tzinfo=sources.PACIFIC)
    assert sources.wordle_day(late) == "2026-11-01"
    assert sources.wordle_day(datetime(2026, 11, 3, 0, 1, tzinfo=sources.PACIFIC)) == "2026-11-02"
    asked = []

    class Client:
        def request_json(self, method, url, **kwargs):
            asked.append(url)
            return SimpleNamespace(data={"solution": "viper", "print_date": "2026-11-01", "days_since_launch": 1961})

    monkeypatch.setattr(sources, "get_http_client", Client)

    assert sources.fetch_wordle("2026-11-01", _context()) == {"day": "2026-11-01", "solution": "VIPER", "number": 1961}
    assert asked == ["https://www.nytimes.com/svc/wordle/v2/2026-11-01.json"]
    with pytest.raises(ValueError):
        sources.fetch_wordle("../today", _context())
    # An answer for another day than the one asked for is refused.
    with pytest.raises(ValueError):
        sources.normalize_wordle({"solution": "viper", "print_date": "2026-11-02"}, "2026-11-01")


def _fake_fetchers(monkeypatch, failing=()):
    calls = []

    def fake(name, value):
        def fetch(*args):
            calls.append(name)
            if name in failing:
                raise RuntimeError(f"{name} is down")
            return value
        return fetch

    news = {"items": [{"title": "头条", "time": "2026-11-02T22:00:00+00:00"}]}
    monkeypatch.setattr(sources, "fetch_poem", fake("poem", {"text": "水是眼波横，山是眉峰聚。", "work": "卜算子", "author": "王观"}))
    monkeypatch.setattr(sources, "fetch_news", fake("news", news))
    monkeypatch.setattr(sources, "fetch_chart", lambda chart_id, ctx: fake("chart", {
        **sources.normalize_chart(SOARING, 19723756), "id": chart_id, "name": sources.NETEASE_CHARTS[chart_id]})())
    monkeypatch.setattr(sources, "fetch_billboard", fake("chart", sources.normalize_billboard(BILLBOARD)))
    monkeypatch.setattr(sources, "fetch_douban", fake("douban", sources.normalize_douban(SHOWING)))
    monkeypatch.setattr(sources, "fetch_gas", fake("gas", sources.normalize_gas(EIA_PAGE)))
    monkeypatch.setattr(sources, "fetch_space", lambda name, ctx: fake("space", sources.normalize_space(
        WEBB_PAGE if name == "webb" else HUBBLE_PAGE, name))())
    monkeypatch.setattr(sources, "fetch_debt", fake("debt", sources.normalize_debt(DEBT)))
    monkeypatch.setattr(sources, "fetch_fear_greed", fake("fear_greed", sources.normalize_fear_greed(FEAR_GREED)))
    monkeypatch.setattr(sources, "fetch_wordle", fake("wordle", {"day": "2026-11-01", "solution": "VIPER", "number": 1961}))
    return calls


def test_a_failing_source_only_loses_its_own_card(monkeypatch, tmp_path):
    monkeypatch.setenv("DAILY_DIGEST_CACHE_DIR", str(tmp_path))
    _fake_fetchers(monkeypatch, failing={"gas", "chart"})

    payload = DailyDigest({"id": "daily_digest"})._payload({}, NOW, allow_network=True, context=_context())

    assert payload["gas"]["state"] == "unavailable"
    assert {result["state"] for result in payload["music"].values()} == {"unavailable"}
    assert payload["debt"]["state"] == "live" and payload["news"]["dw"]["state"] == "live"
    assert payload["wordle"]["data"]["solution"] == "VIPER"
    drawn = []
    monkeypatch.setattr(render, "TILE_DRAWERS", tuple((key, lambda *args, key=key: drawn.append(key))
                                                      for key, _draw in render.TILE_DRAWERS))
    monkeypatch.setattr(render, "_music_panel", lambda *args: drawn.append("music"))
    monkeypatch.setattr(render, "_douban_panel", lambda canvas, night, box, items, title, images: drawn.append(title))
    page = render.render_page((800, 480), payload, {}, NOW, {})
    assert page.size == (800, 480)
    # Gasoline's tile and NetEase's panel are left out; the rest close up.
    assert drawn == ["debt", "fear_greed", "wordle", "正在热映", "热门美剧"]


def test_a_cache_only_redraw_never_touches_the_network(monkeypatch, tmp_path):
    monkeypatch.setenv("DAILY_DIGEST_CACHE_DIR", str(tmp_path))
    calls = _fake_fetchers(monkeypatch)
    plugin = DailyDigest({"id": "daily_digest"})
    plugin._payload({}, NOW, allow_network=True, context=_context())
    calls.clear()

    later = plugin._payload({}, NOW.replace(hour=20), allow_network=False, context=_context())

    assert calls == []
    assert later["news"]["nyt"]["state"] == "stale_cache"  # past its hour, still shown
    assert later["gas"]["state"] == "fresh_cache"


class _PictureClient:
    def __init__(self):
        self.urls = []

    def request_bytes(self, method, url, **kwargs):
        self.urls.append(url)
        buffer = BytesIO()
        Image.new("RGB", (512, 512), (200, 120, 30)).save(buffer, format="JPEG")
        return SimpleNamespace(data=buffer.getvalue())


def test_the_sun_is_fetched_once_a_slot_and_an_earlier_copy_stands_in(monkeypatch, tmp_path):
    monkeypatch.setenv("DAILY_DIGEST_CACHE_DIR", str(tmp_path))
    client = _PictureClient()
    monkeypatch.setattr(daily_digest, "get_http_client", lambda: client)
    plugin = DailyDigest({"id": "daily_digest"})
    options = {"http": client, "context": _context(), "user_agent": sources.USER_AGENT}
    epoch = 1793665800 + 60

    picture, slot = plugin._sun(epoch, allow_network=True, context=options["context"], options=options)
    assert picture is not None and slot == 1793665800
    assert client.urls == [sources.sun_url(1793665800)]
    # Half an hour on, a redraw without the network keeps the earlier slot's sun.
    picture, slot = plugin._sun(epoch + 1800, allow_network=False, context=options["context"], options=options)
    assert slot == 1793665800 and len(client.urls) == 1
    # Past SUN_USABLE_SLOTS the sun is left out.
    late = epoch + daily_digest.SUN_SLOT_SECONDS * daily_digest.SUN_USABLE_SLOTS
    assert plugin._sun(late, allow_network=False, context=options["context"], options=options) == (None, None)
    assert sources.safe_sun_image("https://example.com/latest_512_0193.jpg") is None


class _HeaderClient(_PictureClient):
    def __init__(self):
        super().__init__()
        self.headers = {}

    def request_bytes(self, method, url, **kwargs):
        self.headers[url] = kwargs.get("headers") or {}
        return super().request_bytes(method, url, **kwargs)


def test_the_foot_fetches_covers_and_posters_douban_with_its_referer(monkeypatch, tmp_path):
    monkeypatch.setenv("DAILY_DIGEST_CACHE_DIR", str(tmp_path))
    client = _HeaderClient()
    plugin = DailyDigest({"id": "daily_digest"})
    chart = sources.normalize_chart(SOARING, 19723756)
    payload = {"music": {19723756: {"data": chart}}, "showing": {"data": sources.normalize_douban(SHOWING)},
               "us_tv": {"data": {}}}
    options = {"http": client, "context": _context(), "user_agent": sources.USER_AGENT}

    art = plugin._art(payload, NOW, allow_network=True, options=options)

    cover, poster = chart["tracks"][0]["cover"], sources.douban_poster(POSTER)
    assert set(art) == {cover, poster} and client.urls == [cover, poster]
    assert client.headers[poster]["Referer"] == "https://m.douban.com/movie/"
    assert "Referer" not in client.headers[cover]
    # A redraw without the network finds them cached.
    assert set(plugin._art(payload, NOW, allow_network=False, options=options)) == {cover, poster}
    assert len(client.urls) == 2


def _full_payload():
    news = {name: {"data": {"items": [
        {"title": f"{label}的第{index}条新闻标题，写得相当长以便测试换行与省略号的处理是否越界", "time": "2026-11-02T22:00:00+00:00"}
        for index in range(1, 7)]}} for name, label in (("dw", "德国之声"), ("nyt", "纽约时报"), ("sspai", "少数派"))}
    long_douban = {"items": [{"title": "一部名字非常非常长的电影：续集之再续集之终章", "rating": 9.1, "year": "2026",
                              "region": "中国大陆", "genres": ["剧情", "历史"]}] * 3}
    return {
        "poem": {"data": {"text": "对岸流莺闲不住，长歌戴月到江东，还要再长一点看看会不会越过日期", "work": "浪淘沙·飞云湖月夜·加长的出处名字",
                          "author": "徐清"}},
        "news": news,
        "music": {19723756: {"data": sources.normalize_chart(SOARING, 19723756)}},
        "showing": {"data": long_douban},
        "us_tv": {"data": sources.normalize_douban(SHOWING)},
        "gas": {"data": sources.normalize_gas(EIA_PAGE)},
        "debt": {"data": sources.normalize_debt(DEBT)},
        "fear_greed": {"data": sources.normalize_fear_greed(FEAR_GREED)},
        "wordle": {"data": {"day": "2026-11-01", "solution": "VIPER", "number": 1961}},
        "space": {"name": "webb", "title": "Stars brewing in a nebulous cauldron and a much longer title than usual",
                  "image": "https://cdn.esawebb.org/archives/images/thumb700x/potm2610a.jpg", "time": "2026-10-22"},
        "sun_slot": 1793665800,
    }


COVER_URL = "https://p2.music.126.net/ucdEaYKU_dL0U2ae-wSRCQ==/109951174184824841.jpg?param=200y200"
POSTER_URL = "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2935834896.jpg"


def _images():
    return {"space": Image.new("RGB", (700, 500), (30, 40, 90)), "sun": Image.new("RGB", (320, 320), (200, 120, 30)),
            COVER_URL: Image.new("RGB", (160, 160), (10, 200, 220)), POSTER_URL: Image.new("RGB", (160, 240), (240, 0, 160))}


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_draws_every_card_by_day_and_night(mode):
    page = render.render_page((800, 480), _full_payload(), {"themeMode": mode}, NOW, _images())

    assert page.size == (800, 480)
    paper = (0, 0, 0) if mode == "night" else (255, 255, 255)
    # Each kind's colour band runs along its card's top.
    colours = {kind: (night if mode == "night" else day) for kind, (day, night) in render.KINDS.items()}
    tile_height = (render.GRID_BOTTOM - render.BODY_TOP - render.TILE_GAP) // 2
    for kind, (x, y) in {"天象": (100, 66), "世界": (360, 66), "数字": (580, 66),
                         "游戏": (720, render.BODY_TOP + tile_height + render.TILE_GAP + 2),
                         "影音": (400, render.MEDIA[1] + 2)}.items():
        assert page.getpixel((x, y)) == colours[kind], kind
    # The month's picture is feathered into the page: its centre is the picture, its corner the paper.
    assert page.getpixel((107, 125)) == (30, 40, 90)
    assert max(abs(a - b) for a, b in zip(page.getpixel((12, 70)), paper)) <= 2
    # The foot shows the album cover and the poster big enough to make out, with no frame round them.
    cover = sum(1 for pixel in page.crop(render.MEDIA).get_flattened_data() if pixel == (10, 200, 220))
    poster = sum(1 for pixel in page.crop(render.MEDIA).get_flattened_data() if pixel == (240, 0, 160))
    assert cover > 60 * 60 and poster > 55 * 85
    assert render.render_page((400, 240), _full_payload(), {"themeMode": mode}, NOW, _images()).size == (400, 240)


def test_a_missing_picture_keeps_its_song_or_title(monkeypatch):
    shown = []
    original = PageCanvas.text

    def text(self, xy, value, *args, **kwargs):
        shown.append(value)
        return original(self, xy, value, *args, **kwargs)

    monkeypatch.setattr(PageCanvas, "text", text)
    ratings = []
    original_rating = render._rating_mark
    monkeypatch.setattr(render, "_rating_mark", lambda canvas, right, bottom, rating: (
        ratings.append(rating), original_rating(canvas, right, bottom, rating)))
    images = {key: picture for key, picture in _images().items() if key not in (COVER_URL, POSTER_URL)}

    render.render_page((800, 480), _full_payload(), {}, NOW, images)

    assert "明年依然好时节" in shown and "生日快乐" in shown and 8.9 in ratings


@pytest.mark.parametrize("mode", ["day", "night"])
def test_a_posters_rating_sits_on_the_picture_without_a_plate(monkeypatch, mode):
    placed = []
    original = render._rating_mark
    monkeypatch.setattr(render, "_rating_mark", lambda canvas, right, bottom, rating: (
        placed.append((right, bottom)), original(canvas, right, bottom, rating)))
    payload = _full_payload()
    payload["showing"] = {"data": sources.normalize_douban(SHOWING)}

    page = render.render_page((800, 480), payload, {"themeMode": mode}, NOW, _images())

    right, bottom = placed[0]
    # Left of the star the poster shows through where the old ink chip stood; the score is white,
    # haloed in dark ink.
    assert page.getpixel((round(right) - 44, round(bottom) - 6)) == (240, 0, 160)
    corner = page.crop((round(right) - 40, round(bottom) - 18, round(right), round(bottom))).convert("RGB")
    colours = set(corner.get_flattened_data())
    assert (255, 255, 255) in colours and render.RATING_HALO_INK in colours


@pytest.mark.parametrize("mode", ["day", "night"])
def test_no_text_runs_past_its_card_or_the_page(monkeypatch, mode):
    records, current = [], {"box": None}
    original_card, original_panel, original_text = render._card, render._brand_panel, PageCanvas.text

    def card(canvas, box, kind):
        current["box"] = box
        return original_card(canvas, box, kind)

    def panel(canvas, night, box, colour):
        current["box"] = box
        return original_panel(canvas, night, box, colour)

    def text(self, xy, value, size=14, color="ink", *, width=None, bold=True, anchor="la", stroke=None):
        shown = self.fit(value, size, width, bold)
        if shown:
            records.append((current["box"], self.draw.textbbox(xy, shown, font=self.font(size, bold), anchor=anchor), shown))
        return original_text(self, xy, value, size, color, width=width, bold=bold, anchor=anchor, stroke=stroke)

    monkeypatch.setattr(render, "_card", card)
    monkeypatch.setattr(render, "_brand_panel", panel)
    monkeypatch.setattr(PageCanvas, "text", text)

    render.render_page((800, 480), _full_payload(), {"themeMode": mode}, NOW, _images())

    assert len(records) > 40
    for box, (left, top, right, bottom), shown in records:
        assert 0 <= left and right <= 800 and 0 <= top and bottom <= 480, shown
        if box is None:
            assert bottom <= render.HEADER_RULE, shown
            continue
        assert box[0] <= left and right <= box[2] and box[1] <= top and bottom <= box[3], (shown, box)
    # The header's line and its credit stop short of the date and the colour key.
    header = [(bbox, shown) for box, bbox, shown in records if box is None]
    date_left = min(bbox[0] for bbox, shown in header if "周一" in shown)
    poem = next(bbox for bbox, shown in header if shown.startswith("对岸"))
    assert poem[2] < date_left


@pytest.mark.parametrize("mode", ["day", "night"])
def test_each_outlet_reads_in_a_block_lined_in_its_own_brand_colour(monkeypatch, mode):
    panels, texts = [], []
    original_panel, original_text = render._brand_panel, PageCanvas.text

    def panel(canvas, night, box, colour):
        panels.append((box, colour))
        return original_panel(canvas, night, box, colour)

    def text(self, xy, value, size=14, color="ink", **kwargs):
        texts.append((value, size, color, kwargs.get("stroke")))
        return original_text(self, xy, value, size, color, **kwargs)

    monkeypatch.setattr(render, "_brand_panel", panel)
    monkeypatch.setattr(PageCanvas, "text", text)
    monkeypatch.setattr(render, "paint_doodles", lambda *args, **kwargs: False)  # the page's ground alone
    page = render.render_page((800, 480), _full_payload(), {"themeMode": mode}, NOW, _images())

    # DW Blue, the Times' black and 少数派 red, top to bottom, a small gap apart, filling the column.
    assert [colour for _box, colour in panels] == [(0x00, 0x2D, 0x5A), (0, 0, 0), (0xD7, 0x1A, 0x1B)]
    boxes = [box for box, _colour in panels]
    assert all(after[1] - before[3] == render.NEWS_GAP for before, after in zip(boxes, boxes[1:]))
    assert boxes[-1][3] >= render.GRID_BOTTOM - 2
    paper = (0, 0, 0) if mode == "night" else (255, 255, 255)
    for (left, top, right, bottom), colour in panels:
        middle = (top + bottom) // 2
        # The brand colour is only the line round the block; inside it the page shows.
        edge = render.brand_edge(colour, paper)
        assert page.getpixel((left, middle)) == edge and page.getpixel((right, middle)) == edge
        assert page.getpixel((left + render.NEWS_EDGE + 2, middle)) == paper
        assert page.getpixel((right - 4, middle)) == paper
    # Headlines keep their size, in the page's ink with a halo of the page that clears the doodles.
    headlines = [(size, color, stroke) for value, size, color, stroke in texts if "新闻标题" in value]
    assert headlines and set(headlines) == {(render.NEWS_SIZE, "ink", "paper")}
    # Each block's logo keeps its own colours: 少数派's disc is still red.
    for (left, top, _right, _bottom), _colour in panels:
        mark = page.crop((left + 10, top + 3, left + 40, top + render.NEWS_HEAD))
        assert any(pixel != paper for pixel in mark.convert("RGB").get_flattened_data())
    left, top = boxes[2][0], boxes[2][1]
    disc = page.crop((left + 10, top + 3, left + 30, top + render.NEWS_HEAD)).convert("RGB")
    assert any(red > 180 and green < 80 and blue < 80 for red, green, blue in disc.get_flattened_data())


def test_a_brand_colour_that_would_not_show_on_the_page_is_lightened_for_its_line():
    dw, nyt, sspai = (render.NEWS_BRANDS[name] for name in ("dw", "nyt", "sspai"))
    white, black = (255, 255, 255), (0, 0, 0)

    # By day every brand colour shows on the white page as it is.
    assert [render.brand_edge(colour, white) for colour in (dw, nyt, sspai)] == [dw, nyt, sspai]
    # By night DW's navy turns to a light blue and the Times' black to white; 少数派 red stays.
    night_dw = render.brand_edge(dw, black)
    assert night_dw[2] == max(night_dw) and night_dw[0] < night_dw[1] < night_dw[2] and min(night_dw) >= 120
    assert render.brand_edge(nyt, black) == white
    assert render.brand_edge(sspai, black) == sspai


def test_each_block_carries_its_own_quiet_doodles_off_the_page(monkeypatch):
    laid = []
    original = render.paint_doodles
    monkeypatch.setattr(render, "paint_doodles", lambda image, box, pattern, tint, **kwargs: (
        laid.append((Path(pattern).name, tint, kwargs["strength"])), original(image, box, pattern, tint, **kwargs))[1])

    page = render.render_page((800, 480), _full_payload(), {}, NOW, _images())

    assert [name for name, _tint, _strength in laid] == ["dw.png", "nyt.png", "sspai.png"]
    # In the page's ink, faint.
    assert all(tint == render.DAY["ink"] and strength <= 0.2 for _name, tint, strength in laid)
    for name in ("dw", "nyt", "sspai"):
        assert (render.ASSETS / "doodles" / f"{name}.png").is_file()
    # Against the same page without them, the blocks' grounds carry strokes only a shade off the page.
    monkeypatch.setattr(render, "paint_doodles", lambda *args, **kwargs: False)
    plain = render.render_page((800, 480), _full_payload(), {}, NOW, _images())
    column = (212, render.BODY_TOP, 510, render.GRID_BOTTOM)
    changed = [max(abs(a - b) for a, b in zip(before, after))
               for before, after in zip(plain.crop(column).get_flattened_data(), page.crop(column).get_flattened_data())
               if before != after]
    assert len(changed) > 500
    # Quiet: a stroke is only a shade off the page, nothing like the type on it.
    assert max(changed) <= 60


def test_an_empty_payload_still_draws_a_page():
    page = render.render_page((800, 480), {}, {}, NOW, {})

    assert page.size == (800, 480)


def test_every_logo_the_page_names_is_built_with_its_provenance():
    names = {"hitokoto", "netease_music", "douban", "sspai", "dw", "nyt", "eia", "sdo", "esa_webb", "esa_hubble",
             "treasury", "alternative_me", "wordle"}
    record = json.loads((render.LOGOS / "logos.source.json").read_text(encoding="utf-8"))["logos"]

    for name in names:
        assert (render.LOGOS / f"{name}.png").is_file(), name
        assert record[name]["source"] and record[name]["licence"], name
        assert render.logo(name, 16, True, (237, 241, 234)) is not None
    assert render.TITLE.is_file()


def test_a_slow_hubble_site_does_not_degrade_the_page_while_webb_is_fresh():
    webb, hubble = {"state": "live", "data": {"items": [{}]}}, {"state": "stale_cache", "data": {}}
    assert daily_digest.space_result(webb, hubble) is webb
    assert daily_digest.space_result({"state": "unavailable", "data": {}}, hubble) is hubble
    assert daily_digest.combined_provenance([daily_digest.space_result(webb, hubble)]).value == "live"


def test_one_late_news_feed_does_not_degrade_the_page_while_the_others_are_fresh():
    fresh, late = {"state": "live", "data": {"items": [{}]}}, {"state": "stale_cache", "data": {"items": [{}]}}
    assert daily_digest.healthiest_result([late, fresh, late]) is fresh
    assert daily_digest.healthiest_result([late, late])["state"] == "stale_cache"
    assert daily_digest.combined_provenance([daily_digest.healthiest_result([late, fresh])]).value == "live"


BILLBOARD = {"date": "2026-11-07", "data": [
    {"song": "Choosin' Texas", "artist": "Ella Langley", "this_week": 1, "last_week": 1, "peak_position": 1,
     "weeks_on_chart": 54},
    {"song": "Housewife", "artist": "Tate McRae", "this_week": 2, "last_week": None, "peak_position": 2,
     "weeks_on_chart": 1},
    {"song": "Boston", "artist": "Stella Lefty", "this_week": 3, "last_week": None, "peak_position": 2,
     "weeks_on_chart": 30},
    {"song": "Been By Now", "artist": "Morgan Wallen", "this_week": 4, "last_week": 7, "peak_position": 2,
     "weeks_on_chart": 14}]}
ARTWORK = "https://is1-ssl.mzstatic.com/image/thumb/Music221/v4/69/64/e4/196874840146.jpg/100x100bb.jpg"
ITUNES = {"resultCount": 1, "results": [{"kind": "song", "trackName": "Housewife", "artistName": "Tate McRae",
                                         "collectionName": "Housewife - Single", "artworkUrl100": ARTWORK,
                                         "trackTimeMillis": 175000, "releaseDate": "2026-10-24T07:00:00Z"}]}


def test_billboards_hot_100_reads_its_top_songs_moves_and_weeks():
    chart = sources.normalize_billboard(BILLBOARD)

    assert (chart["id"], chart["name"], chart["updated"]) == ("billboard", "Hot 100", "2026-11-07")
    tracks = chart["tracks"]
    assert [track["title"] for track in tracks] == ["Choosin' Texas", "Housewife", "Boston"]
    assert [(track["last_rank"], track["weeks"], track["peak"]) for track in tracks] == [
        (1, 54, 1), (None, 1, 2), (None, 30, 2)]
    # A song back on the chart after weeks away is a re-entry, not a debut.
    assert [render.song_move(track, rank) for rank, track in enumerate(tracks, 1)] == [
        None, ("new", "NEW"), ("new", "RE")]
    with pytest.raises(ValueError):
        sources.normalize_billboard({"date": "2026-11-07", "data": []})


def test_each_billboard_song_finds_its_cover_album_and_length_on_itunes(monkeypatch):
    asked = []

    def get(url, context, **kwargs):
        if url == sources.BILLBOARD_URL:
            return BILLBOARD
        asked.append(kwargs["params"]["term"])
        return ITUNES if "Housewife" in kwargs["params"]["term"] else {"resultCount": 0, "results": []}

    monkeypatch.setattr(sources, "_get", get)
    chart = sources.fetch_billboard(_context())

    assert asked == ["Choosin' Texas Ella Langley", "Housewife Tate McRae", "Boston Stella Lefty"]
    song = chart["tracks"][1]
    assert song["cover"] == ARTWORK.replace("100x100bb", "200x200bb") and sources.safe_music_image(song["cover"])
    assert (song["album"], song["seconds"], song["published"]) == ("Housewife", 175, "2026-10-24")
    assert chart["tracks"][0]["cover"] == ""


def test_billboard_takes_its_turn_after_the_netease_charts_under_its_own_logo(monkeypatch):
    soaring = {"data": sources.normalize_chart(SOARING, 19723756)}
    billboard = {"data": sources.normalize_billboard(BILLBOARD)}
    music = {19723756: soaring, "billboard": billboard}
    assert [chart["name"] for chart in render.music_charts(music)] == ["飙升榜", "Hot 100"]

    canvas = render.PageCanvas(render._palette({"themeMode": "day"})[0], family="Microsoft YaHei")
    drawn, logos = [], []
    original_text, original_logo = canvas.text, render._paste_logo
    monkeypatch.setattr(canvas, "text", lambda xy, text, *args, **kwargs: drawn.append(text) or original_text(
        xy, text, *args, **kwargs))
    monkeypatch.setattr(render, "_paste_logo", lambda canvas, night, name, *args, **kwargs: logos.append(name) or
                        original_logo(canvas, night, name, *args, **kwargs))
    render._music_panel(canvas, False, (20, 330, 280, 470), billboard["data"], {}, sources.PACIFIC, NOW)

    assert logos == ["billboard"] and "Hot 100" in drawn and "11.07 榜单" in drawn
    # A song below its peak names the peak, which a narrow slot drops before the weeks.
    assert "上榜 54 周" in drawn and "首周上榜" in drawn and "上榜 30 周" in drawn
    tracks = billboard["data"]["tracks"]
    assert [render.song_note(track, NOW, rank) for rank, track in enumerate(tracks, 1)] == [
        "上榜 54 周", "首周上榜", "上榜 30 周 · 最高第 2"]


def test_the_billboard_logo_is_built_with_its_provenance():
    record = json.loads((render.LOGOS / "logos.source.json").read_text(encoding="utf-8"))["logos"]
    assert (render.LOGOS / "billboard.png").is_file() and record["billboard"]["source"]
    assert render.logo("billboard", 16, True, (237, 241, 234)) is not None
