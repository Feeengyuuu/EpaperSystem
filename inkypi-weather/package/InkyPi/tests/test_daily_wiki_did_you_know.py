"""你知道吗: Chinese Wikipedia's front-page questions, parsed, kept and refreshed for Daily Wiki."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_wiki_page import did_you_know  # noqa: E402
from plugins.daily_wiki_page.daily_wiki_page import DailyWikiPage  # noqa: E402


# Template:Dyk as zh.wikipedia.org's parser returned it on 2026-11-11 (variant zh-cn): the block,
# its picture and three of its six questions, then the start of the template's documentation.
DYK_HTML = (
    '<div class="mw-content-ltr mw-parser-output" lang="zh-Hans-CN" dir="ltr"><div id="column-dyk" class="knoblock">\n'
    '<figure class="mw-halign-right" typeof="mw:File"><a href="/wiki/File:Siegfried_Kasche.JPG" '
    'class="mw-file-description"><img src="//thumb.wikimedia.org/wikipedia/commons/thumb/b/b9/Siegfried_Kasche.JPG/'
    '120px-Siegfried_Kasche.JPG?utm_source=zh.wikipedia.org&amp;utm_campaign=parser&amp;utm_content=thumbnail" '
    'decoding="async" loading="lazy" width="116" height="140" class="mw-file-element" srcset="//thumb.wikimedia.org/'
    'wikipedia/commons/thumb/b/b9/Siegfried_Kasche.JPG/250px-Siegfried_Kasche.JPG?utm_source=zh.wikipedia.org&amp;'
    'utm_campaign=parser&amp;utm_content=thumbnail 2x" data-file-width="486" data-file-height="586" /></a>'
    '<figcaption></figcaption></figure>\n'
    '<ul><li><b><a href="/wiki/U-267%E5%8F%B7%E6%BD%9C%E8%89%87" title="U-267号潜艇">哪艘潜艇</a></b>是'
    '<a href="/wiki/%E4%BA%8C%E6%88%98" class="mw-redirect" title="二战">二战</a>中最后一艘撤离法国'
    '<a href="/wiki/%E5%9C%A3%E7%BA%B3%E6%B3%BD%E5%B0%94" title="圣纳泽尔">圣纳泽尔</a>基地的纳粹'
    '<a href="/wiki/U%E8%89%87" class="mw-redirect" title="U艇">U艇</a>？</li>\n'
    '<li>日本女国脚<a href="/wiki/%E5%B3%B6%E7%94%B0%E8%8A%BD%E4%BE%9D" title="岛田芽依">岛田芽依</a>自2026-27赛季起'
    '效力于<b><a href="/wiki/%E4%BA%91%E8%BE%BE" title="云达不来梅女子足球队">哪支德甲球队</a></b>？</li>\n'
    '<li>1941年，希特勒选定<b><a href="/wiki/%E9%BD%90%E6%A0%BC" title="齐格弗里德·卡舍">哪位德国驻克罗地亚公使</a></b>'
    '担任计划中的<a href="/wiki/%E8%8E%AB%E6%96%AF%E7%A7%91" class="mw-redirect" title="莫斯科专员辖区">莫斯科专员辖区</a>'
    '总督？<small>（图）</small></li></ul>\n'
    '</div>\n'
    '<div style="text-align: right;" class="noprint"><b><a href="/wiki/Wikipedia:DYKN" title="Wikipedia:新条目推荐/候选">'
    '候选</a></b></div>\n'
    '<div class="documentation"><ul><li><b><a href="/wiki/Template:Dyk/doc" title="Template:Dyk/doc">文档里的列表</a></b>'
    '不是提问</li></ul></div></div>'
)


def html_text(fragment):
    return DailyWikiPage({"id": "daily_wiki_page"})._html_text(fragment)


def test_each_question_is_plain_text_with_the_article_that_answers_it_and_the_blocks_picture():
    found = did_you_know.facts_from_html(DYK_HTML, html_text)

    assert found["facts"] == [
        {"question": "哪艘潜艇是二战中最后一艘撤离法国圣纳泽尔基地的纳粹U艇？", "answer": "U-267号潜艇", "pictured": False},
        {"question": "日本女国脚岛田芽依自2026-27赛季起效力于哪支德甲球队？", "answer": "云达不来梅女子足球队",
         "pictured": False},
        # The picture's question says so with （图）: the mark is read, not shown.
        {"question": "1941年，希特勒选定哪位德国驻克罗地亚公使担任计划中的莫斯科专员辖区总督？", "answer": "齐格弗里德·卡舍",
         "pictured": True},
    ]
    # The larger of the two sizes the page names, over https and without the tracking query.
    assert found["image_url"] == ("https://thumb.wikimedia.org/wikipedia/commons/thumb/b/b9/Siegfried_Kasche.JPG/"
                                  "250px-Siegfried_Kasche.JPG")


def test_an_answer_drops_its_disambiguation_and_a_question_without_a_bold_link_keeps_its_words():
    page = ('<div id="column-dyk"><ul>'
            '<li><b><a href="/wiki/x" title="玫瑰战争 (无线电视剧)">哪部电视剧</a></b>于2026年首播？</li>'
            '<li><a href="/wiki/y" title="A&amp;B"><b>哪家公司</b></a>以“&amp;”为名？<small>(左图)</small></li>'
            '<li>哪座城市没有加粗的链接？</li><li> </li></ul></div>')
    facts = did_you_know.facts_from_html(page, html_text)["facts"]

    assert [(fact["question"], fact["answer"], fact["pictured"]) for fact in facts] == [
        ("哪部电视剧于2026年首播？", "玫瑰战争", False), ('哪家公司以"&"为名？', "A&B", True),
        ("哪座城市没有加粗的链接？", "", False)]


@pytest.mark.parametrize("page", ["", "<div><ul><li><b><a title='x'>别处的列表</a></b>？</li></ul></div>",
                                  '<div id="column-dyk">今天没有列表</div>', None])
def test_a_page_without_the_block_or_its_list_gives_no_facts(page):
    with pytest.raises(ValueError):
        did_you_know.facts_from_html(page, html_text)


def test_a_picture_is_taken_only_from_wikimedia():
    page = DYK_HTML.replace("//thumb.wikimedia.org/", "//images.example.com/")

    assert did_you_know.facts_from_html(page, html_text)["image_url"] == ""
    assert did_you_know.picture_url('<img src="//upload.wikimedia.org/a/120px-A.jpg">') == (
        "https://upload.wikimedia.org/a/120px-A.jpg")


def make_plugin(tmp_path, monkeypatch, answers):
    plugin = DailyWikiPage({"id": "daily_wiki_page"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: tmp_path)
    asked = []

    def get_json(url, params=None):
        asked.append((url, dict(params or {})))
        answer = answers[min(len(asked), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(plugin, "_get_json", get_json)
    return plugin, asked


NOW = datetime(2026, 11, 11, 9, 0)
ZH = {"language": "zh-cn"}


def test_the_facts_are_asked_once_in_six_hours_from_the_parse_api(tmp_path, monkeypatch):
    plugin, asked = make_plugin(tmp_path, monkeypatch, [{"parse": {"text": DYK_HTML}}])

    first = plugin._did_you_know(ZH, NOW, allow_network=True)
    again = plugin._did_you_know(ZH, NOW + timedelta(hours=5, minutes=59), allow_network=True)

    assert [fact["answer"] for fact in first["facts"]] == ["U-267号潜艇", "云达不来梅女子足球队", "齐格弗里德·卡舍"]
    assert again == first and first["image_url"].endswith("250px-Siegfried_Kasche.JPG")
    assert asked == [("https://zh.wikipedia.org/w/api.php", {
        "action": "parse", "page": "Template:Dyk", "prop": "text", "format": "json", "formatversion": "2",
        "variant": "zh-cn", "disablelimitreport": "1", "disableeditsection": "1"})]
    # After six hours the page asks again.
    plugin._did_you_know(ZH, NOW + timedelta(hours=6, minutes=1), allow_network=True)
    assert len(asked) == 2


def test_a_failed_refresh_keeps_the_last_good_facts_for_three_days_then_shows_none(tmp_path, monkeypatch):
    plugin, asked = make_plugin(tmp_path, monkeypatch, [
        {"parse": {"text": DYK_HTML}}, RuntimeError("offline"), {"parse": {"text": "<p>changed shape</p>"}},
        RuntimeError("offline")])
    good = plugin._did_you_know(ZH, NOW, allow_network=True)

    assert plugin._did_you_know(ZH, NOW + timedelta(hours=7), allow_network=True) == good
    # A page that changed shape is a failed refresh too: the kept facts stay.
    assert plugin._did_you_know(ZH, NOW + timedelta(days=2), allow_network=True) == good
    assert plugin._did_you_know(ZH, NOW + timedelta(days=3, hours=1), allow_network=True) == {}
    assert len(asked) == 4


def test_a_first_failure_and_another_language_show_nothing_and_a_redraw_never_asks(tmp_path, monkeypatch):
    plugin, asked = make_plugin(tmp_path, monkeypatch, [RuntimeError("offline"), {"parse": {"text": DYK_HTML}}])

    assert plugin._did_you_know(ZH, NOW, allow_network=True) == {} and len(asked) == 1
    # An English page has no 你知道吗; a redraw from cache reads what is kept and never asks.
    assert plugin._did_you_know({"language": "en"}, NOW, allow_network=True) == {}
    assert plugin._did_you_know(ZH, NOW, allow_network=False) == {}
    assert len(asked) == 1
    kept = plugin._did_you_know(ZH, NOW, allow_network=True)
    assert plugin._did_you_know(ZH, NOW + timedelta(days=1), allow_network=False) == kept and len(asked) == 2


def test_a_redraw_reads_the_kept_facts_without_creating_or_changing_any_file(tmp_path, monkeypatch):
    cold = tmp_path / "cold"
    plugin = DailyWikiPage({"id": "daily_wiki_page"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: cold)
    monkeypatch.setattr(plugin, "_get_json", lambda *args, **kwargs: pytest.fail("a redraw must not fetch"))

    assert plugin._did_you_know(ZH, NOW, allow_network=False) == {} and not cold.exists()
    cold.mkdir()
    (cold / did_you_know.CACHE_FILE).write_text("{not json", encoding="utf-8")
    assert plugin._did_you_know(ZH, NOW, allow_network=False) == {}
    assert sorted(path.name for path in cold.iterdir()) == [did_you_know.CACHE_FILE]


def test_a_task_that_is_cancelled_is_not_swallowed(tmp_path, monkeypatch):
    from runtime.refresh_contracts import TaskCancelled

    plugin, _asked = make_plugin(tmp_path, monkeypatch, [TaskCancelled("stop")])

    with pytest.raises(TaskCancelled):
        plugin._did_you_know(ZH, NOW, allow_network=True)


def test_the_page_carries_the_facts_beside_the_days_payload_not_inside_its_cache(tmp_path, monkeypatch):
    plugin, asked = make_plugin(tmp_path, monkeypatch, [{"parse": {"text": DYK_HTML}}])
    payload = {"language": "zh-cn", "title": "条目", "date": "2026-11-11", "_source_provenance": "fresh_cache"}

    shown = plugin._with_did_you_know(payload, ZH, NOW, allow_network=True)

    assert [fact["answer"] for fact in shown["did_you_know"]["facts"]][0] == "U-267号潜艇"
    assert "did_you_know" not in payload and shown["_source_provenance"] == "fresh_cache"
    # A page that fell back to English shows none, and asks for none.
    english = plugin._with_did_you_know({**payload, "language": "en"}, ZH, NOW + timedelta(days=1), allow_network=True)
    assert "did_you_know" not in english and len(asked) == 1
