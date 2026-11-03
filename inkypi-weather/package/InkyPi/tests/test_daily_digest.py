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

TOPLISTS = {"code": 200, "list": [
    {"id": 19723756, "name": "飙升榜", "updateFrequency": "每天更新",
     "tracks": [{"first": "Hong Kong (feat. 王菲)", "second": "The Weeknd"}, {"first": "无 意", "second": "加木"},
                {"first": "新生活", "second": "王齐铭"}, {"first": "第四首", "second": "不显示"}]},
    {"id": 3779629, "name": "新歌榜", "updateFrequency": "每天更新", "tracks": [{"first": "一张相片", "second": "连麻"}]},
    {"id": 71384707, "name": "古典榜", "tracks": [{"first": "不读", "second": "x"}]},
    {"id": 3778678, "name": "热歌榜", "updateFrequency": "每天更新", "tracks": []},
]}
SHOWING = {"subject_collection_items": [
    {"title": "生日快乐", "rating": {"value": 8.9}, "year": "2025", "card_subtitle": "2025 / 埃及 / 剧情 / 萨拉 / 朵哈"},
    {"title": f"神探之痕迹{LRM}", "rating": {"value": 6.7}, "year": "2026",
     "card_subtitle": "2026 / 中国大陆 中国香港 / 犯罪 惊悚 悬疑 / 陈思诚 / 张译"},
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


def test_netease_keeps_its_four_charts_top_three_each():
    music = sources.normalize_music(TOPLISTS)

    assert [chart["name"] for chart in music["charts"]] == ["飙升榜", "新歌榜"]  # 热歌榜 listed no tracks today
    assert music["charts"][0]["tracks"] == [{"title": "Hong Kong (feat. 王菲)", "artist": "The Weeknd"},
                                            {"title": "无 意", "artist": "加木"}, {"title": "新生活", "artist": "王齐铭"}]
    # The charts take turns by the hour.
    assert [render.music_chart(music, NOW.replace(hour=hour))["name"] for hour in (16, 17)] == ["飙升榜", "新歌榜"]
    with pytest.raises(ValueError):
        sources.normalize_music({"list": [{"id": 1, "tracks": [{"first": "x"}]}]})


def test_douban_reads_ratings_regions_and_genres_without_format_marks():
    items = sources.normalize_douban(SHOWING)["items"]

    assert items[0] == {"title": "生日快乐", "rating": 8.9, "year": "2025", "region": "埃及", "genres": ["剧情"]}
    assert items[1]["title"] == "神探之痕迹" and items[1]["region"] == "中国大陆" and items[1]["genres"] == ["犯罪", "惊悚"]
    assert items[2]["rating"] is None
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
    monkeypatch.setattr(sources, "fetch_music", fake("music", sources.normalize_music(TOPLISTS)))
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
    _fake_fetchers(monkeypatch, failing={"gas", "music"})

    payload = DailyDigest({"id": "daily_digest"})._payload({}, NOW, allow_network=True, context=_context())

    assert payload["gas"]["state"] == "unavailable" and payload["music"]["state"] == "unavailable"
    assert payload["debt"]["state"] == "live" and payload["news"]["dw"]["state"] == "live"
    assert payload["wordle"]["data"]["solution"] == "VIPER"
    drawn = []
    monkeypatch.setattr(render, "TILE_DRAWERS", tuple((key, lambda *args, key=key: drawn.append(key))
                                                      for key, _draw in render.TILE_DRAWERS))
    monkeypatch.setattr(render, "_music_panel", lambda *args: drawn.append("music"))
    monkeypatch.setattr(render, "_douban_panel", lambda canvas, night, box, items, title: drawn.append(title))
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
        "music": {"data": sources.normalize_music(TOPLISTS)},
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


def _images():
    return {"space": Image.new("RGB", (700, 500), (30, 40, 90)), "sun": Image.new("RGB", (320, 320), (200, 120, 30))}


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_draws_every_card_by_day_and_night(mode):
    page = render.render_page((800, 480), _full_payload(), {"themeMode": mode}, NOW, _images())

    assert page.size == (800, 480)
    paper = (0, 0, 0) if mode == "night" else (255, 255, 255)
    # Each kind's colour band runs along its card's top.
    colours = {kind: (night if mode == "night" else day) for kind, (day, night) in render.KINDS.items()}
    for kind, (x, y) in {"天象": (100, 66), "世界": (360, 66), "数字": (580, 66), "游戏": (720, 66 + 149),
                         "影音": (400, 364)}.items():
        assert page.getpixel((x, y)) == colours[kind], kind
    # The month's picture is feathered into the page: its centre is the picture, its corner the paper.
    assert page.getpixel((107, 125)) == (30, 40, 90)
    assert max(abs(a - b) for a, b in zip(page.getpixel((12, 70)), paper)) <= 2
    assert render.render_page((400, 240), _full_payload(), {"themeMode": mode}, NOW, _images()).size == (400, 240)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_no_text_runs_past_its_card_or_the_page(monkeypatch, mode):
    records, current = [], {"box": None}
    original_card, original_text = render._card, PageCanvas.text

    def card(canvas, box, kind):
        current["box"] = box
        return original_card(canvas, box, kind)

    def text(self, xy, value, size=14, color="ink", *, width=None, bold=True, anchor="la", stroke=None):
        shown = self.fit(value, size, width, bold)
        if shown:
            records.append((current["box"], self.draw.textbbox(xy, shown, font=self.font(size, bold), anchor=anchor), shown))
        return original_text(self, xy, value, size, color, width=width, bold=bold, anchor=anchor, stroke=stroke)

    monkeypatch.setattr(render, "_card", card)
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
