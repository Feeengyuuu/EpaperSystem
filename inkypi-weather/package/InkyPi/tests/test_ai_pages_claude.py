"""The AI news brief and Telegram curation ask Claude Opus 5.5 first when an Anthropic key is set.

The owner chose this on 2026-11-07 (Console credit); OpenAI and Groq stay as the backups.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from plugins.daily_ai_news import news_brief
from plugins.daily_ai_news.daily_ai_news import DailyAINews
from plugins.telegram_digest import curation
from plugins.telegram_digest.curation import Curator
from utils import claude_http

NOW = datetime(2026, 11, 7, 7, 52)
EMPTY_BRIEF = '{"lead":null,"mainland":[],"world":[],"sources":[]}'


def test_the_news_brief_asks_claude_first_and_keeps_openai_and_groq_behind_it(monkeypatch):
    plugin = DailyAINews({"id": "daily_ai_news"})
    asked = []

    def claude(api_key, model, settings, items, market, now, recent_titles=None):
        asked.append(("claude", model))
        raise TimeoutError("timed out")

    def openai(api_key, model, settings, items, market, now, recent_titles=None, provider="openai", base_url=None):
        asked.append((provider, model))
        return {"lead": None, "mainland": [], "world": []}

    monkeypatch.setattr(plugin, "_summarize_with_claude", claude)
    monkeypatch.setattr(plugin, "_summarize_with_openai", openai)

    brief = plugin._summarize_with_ai("sk", "gsk", "gpt-6.1-sol", {}, [], {}, NOW, claude_key="sk-ant")
    assert asked == [("claude", "claude-opus-5-5"), ("openai", "gpt-6.1-sol")]
    assert brief["_model"] == "gpt-6.1-sol"

    asked.clear()
    monkeypatch.setattr(plugin, "_summarize_with_claude",
                        lambda *args, **kwargs: asked.append("claude") or {"lead": None})
    brief = plugin._summarize_with_ai("sk", "gsk", "gpt-6.1-sol", {}, [], {}, NOW, claude_key="sk-ant")
    assert asked == ["claude"] and brief["_model"] == "claude:claude-opus-5-5" and brief["_used_ai"]

    # Without the Anthropic key nothing changes.
    asked.clear()
    plugin._summarize_with_ai("sk", "", "gpt-6.1-sol", {}, [], {}, NOW)
    assert asked == [("openai", "gpt-6.1-sol")]


def test_the_claude_brief_sends_the_brief_schema_at_medium_effort_and_keeps_its_tokens(monkeypatch, caplog):
    plugin = DailyAINews({"id": "daily_ai_news"})
    sent = {}

    def ask_json(api_key, **kwargs):
        sent.update(kwargs, api_key=api_key)
        return SimpleNamespace(text=EMPTY_BRIEF, model="claude-opus-5-5", usage={"input": 5900, "output": 2100})

    monkeypatch.setattr(claude_http, "ask_json", ask_json)
    with caplog.at_level("INFO", logger=news_brief.logger.name):
        brief = plugin._summarize_with_claude("sk-ant", "claude-opus-5-5", {}, [], {}, NOW)

    assert sent["api_key"] == "sk-ant" and sent["model"] == "claude-opus-5-5"
    assert sent["schema"] == news_brief.NEWS_JSON_SCHEMA and sent["effort"] == "medium"
    assert sent["system"] == news_brief.NEWS_SYSTEM_PROMPT
    assert json.loads(sent["prompt"])["output_schema"] == news_brief.NEWS_OUTPUT_SCHEMA
    assert brief["_usage"] == {"input": 5900, "output": 2100}
    assert "claude-opus-5-5 used 5900 input + 2100 output tokens" in caplog.text


def test_the_page_reads_the_anthropic_key(monkeypatch, tmp_path):
    plugin = DailyAINews({"id": "daily_ai_news"})
    passed = {}

    class Device:
        def load_env_key(self, key):
            return {"ANTHROPIC_API_KEY": "sk-ant"}.get(key, "")

    def summarize(openai_key, groq_key, model, settings, items, market, now, recent_titles=None, claude_key=""):
        passed.update(openai=openai_key, groq=groq_key, claude=claude_key)
        return {"lead": None, "mainland": [], "world": [], "sources": [], "_used_ai": True}

    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_fetch_items", lambda *_args: [
        {"source": "BBC中文", "title": "新闻一", "summary": "", "published": "", "link": ""}])
    monkeypatch.setattr(plugin, "_fetch_market_snapshot", lambda *_args: {})
    monkeypatch.setattr(plugin, "_allow_api_call", lambda *_args: True)
    monkeypatch.setattr(plugin, "_summarize_with_ai", summarize)

    plugin._get_brief({"feed_urls": "", "max_items": "6"}, Device(), NOW)

    assert passed == {"openai": "", "groq": "", "claude": "sk-ant"}


class FakeClaude:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, api_key, **kwargs):
        self.calls.append({"api_key": api_key, **kwargs})
        if self.error:
            raise self.error
        entries = json.loads(kwargs["prompt"])["items"]
        items = [{"id": entry["id"], "ad": False, "score": 77, "tag": "时事", "headline": "Claude 标题",
                  "summary": "Claude 摘要"} for entry in entries]
        return SimpleNamespace(text=json.dumps({"items": items}, ensure_ascii=False), usage={})


def telegram_message(key, text):
    stamp = int(datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc).timestamp()) - 600
    return {"key": key, "chat_id": "c", "chat_title": "快讯", "chat_kind": "channel", "raw_text": text,
            "title": text, "media_kind": "text", "date": stamp}


def test_telegram_curation_asks_claude_at_low_effort_with_its_schema(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(claude_http, "ask_json", fake)
    openai_used = []
    now = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
    curator = Curator({}, None, now, api_key="sk", claude_key="sk-ant",
                      ai_client_factory=lambda _key: openai_used.append(1))

    ranked = curator.curate([telegram_message("fed", "美联储维持利率不变")])

    assert ranked[0]["curation"]["headline"] == "Claude 标题" and ranked[0]["curation"]["source"] == "ai"
    call = fake.calls[0]
    assert call["api_key"] == "sk-ant" and call["model"] == "claude-opus-5-5" and call["effort"] == "low"
    assert call["schema"] == curation.AI_SCHEMA and call["system"] == curation.SYSTEM_PROMPT
    assert call["timeout"] == curation.CLAUDE_TIMEOUT_SECONDS
    assert openai_used == []


def test_telegram_falls_back_to_openai_only_when_time_is_left(monkeypatch):
    monkeypatch.setattr(claude_http, "ask_json", FakeClaude(error=TimeoutError("timed out")))
    now = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)

    class OpenAIReplies:
        def __init__(self):
            self.calls = 0
            self.responses = self

        def create(self, **kwargs):
            self.calls += 1
            entries = json.loads(kwargs["input"])["items"]
            items = [{"id": entry["id"], "ad": False, "score": 66, "tag": "时事", "headline": "OpenAI 标题",
                      "summary": "摘要"} for entry in entries]
            return SimpleNamespace(output_text=json.dumps({"items": items}, ensure_ascii=False))

    openai = OpenAIReplies()
    curator = Curator({}, None, now, api_key="sk", claude_key="sk-ant", ai_client_factory=lambda _key: openai)
    ranked = curator.curate([telegram_message("fed", "美联储维持利率不变")])
    assert openai.calls == 1 and ranked[0]["curation"]["headline"] == "OpenAI 标题"

    # Claude used up the time: no OpenAI call, the rules rank the messages this refresh.
    clock = [1000.0]
    monkeypatch.setattr(curation.time, "monotonic", lambda: clock[0])

    def slow(api_key, **kwargs):
        clock[0] += curation.CLAUDE_TIMEOUT_SECONDS
        raise TimeoutError("timed out")

    monkeypatch.setattr(claude_http, "ask_json", slow)
    openai = OpenAIReplies()
    curator = Curator({}, None, now, api_key="sk", claude_key="sk-ant", ai_client_factory=lambda _key: openai)
    curator.deadline = clock[0] + curation.CLAUDE_TIMEOUT_SECONDS + curation.AI_REPLY_RESERVE_SECONDS + 5
    ranked = curator.curate([telegram_message("fed", "美联储维持利率不变")])
    assert openai.calls == 0 and ranked[0]["curation"]["source"] == "rules"
    assert curator.ai_error


@pytest.mark.parametrize(("openai_key", "claude_key", "asks"), [("", "sk-ant", True), ("", "", False)])
def test_telegram_needs_either_key(monkeypatch, openai_key, claude_key, asks):
    fake = FakeClaude()
    monkeypatch.setattr(claude_http, "ask_json", fake)
    now = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
    curator = Curator({}, None, now, api_key=openai_key, claude_key=claude_key)

    curator.curate([telegram_message("fed", "美联储维持利率不变")])

    assert bool(fake.calls) is asks
    assert (curator.ai_error == "未配置 AI 密钥") is (not asks)


def test_the_telegram_page_passes_the_anthropic_key_to_its_account_worker():
    from plugins.telegram_digest.telegram_digest import TelegramDigest

    plugin = TelegramDigest({"id": "telegram_digest"})

    class Device:
        def load_env_key(self, key):
            return {"ANTHROPIC_API_KEY": "sk-ant"}.get(key, "")

    assert plugin._claude_key({}, Device()) == "sk-ant"
    assert plugin._claude_key({"curationMode": "rules"}, Device()) == ""
