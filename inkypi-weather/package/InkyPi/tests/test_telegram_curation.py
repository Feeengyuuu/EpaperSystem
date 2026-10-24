import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.telegram_digest.curation import (  # noqa: E402
    PER_CHAT_VISIBLE,
    VISIBLE_COUNT,
    Curator,
    Rules,
    display_headline,
    order_for_page,
)
from plugins.telegram_digest.telegram_digest import TelegramDigest  # noqa: E402

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)


def message(key, text, kind="channel", chat="Daily Signal", age_minutes=10, **extra):
    return {
        "key": key,
        "chat_id": extra.pop("chat_id", chat),
        "chat_title": chat,
        "chat_kind": kind,
        "raw_text": text,
        "title": text.split(" ")[0],
        "media_kind": extra.pop("media_kind", "text"),
        "date": int(NOW.timestamp()) - age_minutes * 60,
        **extra,
    }


class FakeAI:
    """Replies with a verdict per id from ``answers`` (keyed by message text) and records calls."""

    def __init__(self, answers=None, error=None):
        self.answers = answers or {}
        self.error = error
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        items = []
        for entry in json.loads(kwargs["input"])["items"]:
            answer = self.answers.get(entry["text"], {})
            items.append({"id": entry["id"], "ad": answer.get("ad", False), "score": answer.get("score", 50),
                          "tag": answer.get("tag", "时事"), "headline": answer.get("headline", "AI 标题"),
                          "summary": answer.get("summary", "AI 摘要")})
        return SimpleNamespace(output_text=json.dumps({"items": items}, ensure_ascii=False))


def curator(settings=None, previous=None, ai=None, api_key="key"):
    return Curator(settings or {}, previous, NOW, api_key=api_key,
                   ai_client_factory=(lambda _key: ai) if ai else None)


def test_rules_drop_channel_ads_but_never_judge_private_chats():
    rules = Rules({})

    assert rules.ad_reason(message("a", "进群领福利 t.me/+AbCd")) == "邀请链接"
    assert rules.ad_reason(message("b", "限时秒杀，优惠券点击领取")) == "广告"
    assert rules.ad_reason(message("c", "看看 @alpha_one @beta_two @gamma_three @delta_four")) == "链接过多"
    assert rules.ad_reason(message("d", "订阅 https://x.example", has_buttons=True)) == "按钮推广"
    assert rules.ad_reason(message("e", "地震预警：本地震感明显")) == ""
    assert rules.ad_reason(message("f", "给你一张优惠券，记得用", kind="private", chat="妈妈")) == ""


def test_owner_terms_and_chats_block_even_private_messages():
    rules = Rules({"blockKeywords": "抽奖，直播带货", "blockChats": "@noisy_channel, 吵闹群"})

    assert rules.ad_reason(message("a", "今晚抽奖", kind="private")) == "屏蔽词"
    assert rules.ad_reason(message("b", "普通消息", chat="吵闹群", kind="group")) == "屏蔽对话"
    assert rules.ad_reason(message("c", "普通消息", chat="Other", chat_username="noisy_channel")) == "屏蔽对话"


def test_ai_verdicts_rank_messages_and_drop_ai_flagged_ads():
    ai = FakeAI({
        "美联储维持利率不变": {"score": 88, "tag": "财经", "headline": "美联储按兵不动"},
        "周末一起吃饭吗": {"score": 92, "tag": "私聊", "headline": "周末一起吃饭吗？"},
        "新品上市欢迎选购": {"ad": True, "score": 0},
    })
    items = [
        message("fed", "美联储维持利率不变", chat="财经快讯"),
        message("mom", "周末一起吃饭吗", kind="private", chat="妈妈"),
        message("shop", "新品上市欢迎选购", chat="商城"),
    ]

    ranked = curator(ai=ai).curate(items)

    assert [item["key"] for item in ranked] == ["mom", "fed"]
    assert ranked[1]["curation"]["headline"] == "美联储按兵不动"
    assert ranked[1]["curation"]["tag"] == "财经"
    assert ranked[0]["curation"]["source"] == "ai"
    assert len(ai.calls) == 1
    assert ai.calls[0]["text"]["format"]["type"] == "json_schema"


def test_cached_verdicts_are_reused_without_a_new_ai_call():
    ai = FakeAI({"美联储维持利率不变": {"score": 88}})
    first = curator(ai=ai)
    first.curate([message("fed", "美联储维持利率不变")])
    state = first.state([message("fed", "美联储维持利率不变")])

    second_ai = FakeAI()
    second = curator(previous=state, ai=second_ai)
    ranked = second.curate([message("fed", "美联储维持利率不变")])

    assert second_ai.calls == []
    assert ranked[0]["curation"]["score"] == 88
    assert second.stats()["curation"] == "ai"


def test_daily_limit_and_ai_failure_fall_back_to_rule_scores():
    spent = {"ai_calls": {"date": NOW.date().isoformat(), "count": 2}}
    ai = FakeAI()
    limited = curator({"aiDailyLimit": "2"}, previous=spent, ai=ai)
    ranked = limited.curate([message("a", "普通资讯")])

    assert ai.calls == []
    assert ranked[0]["curation"]["source"] == "rules"
    assert "今日" in limited.ai_error

    broken = curator(ai=FakeAI(error=RuntimeError("timeout")))
    ranked = broken.curate([message("a", "普通资讯")])

    assert ranked[0]["curation"]["source"] == "rules"
    assert broken.ai_error == "timeout"
    assert broken.state([])["ai_calls"]["count"] == 1


def test_missing_key_or_rules_mode_never_calls_the_ai():
    ai = FakeAI()

    no_key = curator(ai=ai, api_key="")
    no_key.curate([message("a", "普通资讯")])
    rules_only = curator({"curationMode": "rules"}, ai=ai)
    rules_only.curate([message("a", "普通资讯")])

    assert ai.calls == []
    assert no_key.ai_error


def test_dropped_ads_are_remembered_and_counted_when_seen_again():
    first = curator({"curationMode": "rules"})
    first.curate([message("ad", "点击进群领红包", chat="福利群", kind="group")])
    state = first.state([])

    second = curator({"curationMode": "rules"}, previous=state)

    assert second.is_known_filtered("ad")
    second.note_known_filtered({"key": "ad", "chat_title": "福利群"})
    assert second.stats()["blocked_count"] == 1
    assert second.stats()["blocked_chats"] == ["福利群"]


def test_page_order_caps_each_chat_in_the_visible_set():
    busy = [
        dict(message(f"busy-{index}", "资讯", chat="Busy"), curation={"score": 90 - index})
        for index in range(6)
    ]
    others = [
        dict(message(f"other-{index}", "资讯", chat=f"Other {index}"), curation={"score": 50 - index})
        for index in range(6)
    ]

    ordered = order_for_page(busy + others)
    visible = ordered[:VISIBLE_COUNT]

    assert sum(1 for item in visible if item["chat_title"] == "Busy") == PER_CHAT_VISIBLE
    assert [item["key"] for item in visible[:2]] == ["busy-0", "busy-1"]
    assert len(ordered) == 12


def test_pinned_chats_rank_first():
    ranked = curator({"curationMode": "rules", "pinChats": "家人群"}).curate([
        message("news", "重大新闻 " * 20, chat="新闻"),
        message("family", "到家了", chat="家人群", kind="group"),
    ])

    assert ranked[0]["key"] == "family"
    assert ranked[0]["curation"]["pinned"] is True


def test_media_only_headline_names_the_sender():
    item = message("p", "", chat="湾区生活群", media_kind="photo")
    item["title"] = "图片无配字"

    assert display_headline(item) == "湾区生活群发来图片"


def test_wrap_never_starts_a_line_with_closing_punctuation(tmp_path):
    plugin = TelegramDigest({"id": "telegram_digest"})
    draw = ImageDraw.Draw(Image.new("RGB", (400, 100), "white"))
    font = plugin._font(15, "normal")
    text = "探测器以 960 公里距离掠过，传回首批高清图像，确认其为双叶结构，任务团队称一切按计划推进。"

    for width in range(120, 460, 7):
        lines = plugin._wrap_text(draw, text, font, width, 8)
        assert all(line[:1] not in "，。、" for line in lines[1:]), (width, lines)
        assert all(plugin._text_width(draw, line, font) <= width for line in lines)
        assert "".join(lines).replace(" ", "") == text.replace(" ", "")


def test_click_bait_and_shortened_links_count_as_ads_but_one_plain_link_does_not():
    rules = Rules({})
    spam = ("Click On The Link To Watch Now Watch Link 1 https://lockr.so/nFQD2Zho8 Watch Link 2 "
            "https://fast-links.org/s?reM8RSw6 VIP Zone - Tap Now for Exclusive Leaks")

    assert rules.ad_reason(message("a", spam)) == "诱导点击"
    assert rules.ad_reason(message("b", "new drop https://bit.ly/a and https://cutt.ly/b")) == "推广链接"
    assert rules.ad_reason(message("c", "完整版点击观看")) == "诱导点击"
    assert rules.ad_reason(message("d", "泡泡 v1.5.1 更新！详见 https://example.com/release")) == ""
    assert rules.ad_reason(message("e", "commonlyfansubbed anime season")) == ""
    assert rules.ad_reason(message("f", "click on the link I sent you", kind="private", chat="妈妈")) == ""


def test_rows_name_the_chat_kind_by_icon_and_mark_important_stories(tmp_path):
    plugin = TelegramDigest({"id": "telegram_digest"})
    image = Image.new("RGB", (800, 480), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    plugin._sx = plugin._sy = plugin._page_scale = 1
    palette = plugin._palette({"mode": "day"})
    pasted = []
    original = plugin._paste_icon

    def record(image, name, x, y, size):
        pasted.append(name)
        return original(image, name, x, y, size)

    plugin._paste_icon = record
    for kind, score in (("private", 90), ("group", 40), ("channel", 85)):
        item = {**message(kind, "正文", kind=kind), "curation": {"score": score, "tag": "时事"}}
        plugin._draw_story_row(image, draw, item, (486, 72, 786, 128), palette)

    assert pasted == ["important", "kind_private", "kind_group", "important", "kind_channel"]
