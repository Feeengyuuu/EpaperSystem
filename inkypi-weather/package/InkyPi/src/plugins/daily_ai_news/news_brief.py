"""The AI brief for 整点新闻: prompt, parsing, checks against the RSS items, visible-text
cleanup and the RSS-only fallback."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from openai import OpenAI

from . import news_common as common
from .news_common import MAINLAND_NEWS_MAX, WORLD_NEWS_MAX
from .news_text import (
    ENGLISH_NEWS_TERM_REPLACEMENTS,
    TRADITIONAL_PHRASE_REPLACEMENTS,
    TRADITIONAL_TO_SIMPLIFIED,
    VISIBLE_ENGLISH_ALLOWLIST,
    VISIBLE_ENGLISH_WORD_RE,
)

logger = logging.getLogger(__name__)

# OpenAI's current small model, in the small-model group of the free daily tokens for
# shared traffic (as gpt-5-mini was).
DEFAULT_MODEL = "gpt-6-luna"
# Earlier defaults; a setting saved with one of them follows the current default.
LEGACY_DEFAULT_MODELS = ("gpt-5-nano", "gpt-5-mini")
# Asked once more when OpenAI refuses the default (an account it is not open to yet).
PREVIOUS_MODEL = "gpt-5-mini"
# How long each model family thinks: gpt-6 at its default, gpt-5 briefly.
REASONING_EFFORTS = {"gpt-6": "medium", "gpt-5": "low"}
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
# Groq shut llama-3.3-70b-versatile down for free use on 2026-08-16; a saved one follows the default.
LEGACY_GROQ_MODELS = ("llama-3.3-70b-versatile", "llama-3.1-8b-instant")
# Groq's free tier allows 8K tokens a minute, prompt and reply together: fewer items, a shorter reply.
GROQ_MAX_ITEMS, GROQ_MAX_COMPLETION_TOKENS = 12, 2000
# The models reason before answering.
DEFAULT_AI_TIMEOUT_SECONDS = 90
AI_MAX_OUTPUT_TOKENS = 6000
# Each story is a short concrete title, a one-sentence fact and a 2-4 character tag.
NEWS_TITLE_MAX_CHARS = 30
NEWS_FACT_MAX_CHARS = 44
NEWS_TAG_MAX_CHARS = 4
# The lead's fact is two sentences: it fills the two summary lines of the lead band.
LEAD_FACT_MAX_CHARS = 64
# The fixed tag vocabulary the AI picks from, with the meaning the prompt gives it.
NEWS_TAG_DEFINITIONS = (
    ("时政", "国内政策、政府与人事、重要会议"),
    ("经济", "宏观数据、消费、贸易、物价、口岸"),
    ("产业", "工业、制造、企业经营、产量"),
    ("金融", "银行、央行、货币、股市、债市"),
    ("科技", "科研、人工智能、芯片、航天、互联网"),
    ("社会", "民生、教育、乡村、社会事件"),
    ("两岸", "台湾与两岸关系"),
    ("外交", "国家间往来、会谈、特使、峰会"),
    ("冲突", "战争、袭击、军事行动、停火"),
    ("公卫", "疫情、疾病、医疗卫生"),
    ("灾害", "地震、台风、洪水、火灾等灾害事故"),
    ("司法", "法院、执法、反腐审查、案件"),
    ("航空", "民航、机场、飞机与航班"),
    ("能源", "石油、天然气、电力、煤炭、新能源"),
    ("体育", "赛事与运动员"),
    ("环境", "气候、生态、污染、碳排放"),
)
NEWS_TAGS = tuple(tag for tag, _meaning in NEWS_TAG_DEFINITIONS)
FALLBACK_NEWS_TAG = "要闻"
# Title keywords that place an out-of-vocabulary tag, most specific first.
TAG_TITLE_KEYWORDS = (
    ("两岸", ("两岸", "台湾", "台当局", "国台办", "台胞", "陆生", "民进党")),
    ("公卫", ("疫情", "病毒", "埃博拉", "病例", "公共卫生", "疫苗", "医疗", "流感")),
    ("冲突", ("冲突", "袭击", "空袭", "战争", "导弹", "武装", "停火", "交火", "战机", "以军", "胡塞", "军事")),
    ("灾害", ("地震", "台风", "洪水", "暴雨", "火灾", "山火", "干旱", "灾害", "泥石流", "事故")),
    ("航空", ("航空", "机场", "客机", "航班", "波音", "空客", "飞机", "FAA")),
    ("能源", ("能源", "石油", "原油", "天然气", "电力", "煤电", "煤炭", "核电", "光伏", "电池", "储能", "油价")),
    ("司法", ("法院", "法官", "司法", "判决", "审判", "起诉", "检察", "逮捕", "查处", "查获", "违纪", "审查调查", "警方")),
    ("外交", ("外交", "会谈", "会晤", "对话", "访问", "特使", "大使", "峰会", "外长")),
    ("金融", ("金融", "银行", "央行", "人民币", "利率", "股市", "证券", "汇率", "债券")),
    ("科技", ("科技", "人工智能", "芯片", "航天", "卫星", "火箭", "发射", "半导体", "互联网")),
    ("产业", ("产业", "制造", "钢", "汽车", "工业", "企业", "产量", "工信部")),
    ("环境", ("气候", "环境", "生态", "污染", "排放", "碳")),
    ("经济", ("经济", "消费", "投资", "贸易", "外贸", "出口", "进口", "物价", "价格", "批发价", "就业", "口岸", "GDP")),
    ("体育", ("体育", "比赛", "奥运", "足球", "篮球", "冠军", "联赛")),
    ("社会", ("社会", "乡村", "教育", "民生", "养老", "医保", "学校")),
    ("时政", ("国务院", "中央", "政府", "政策", "人大", "政协", "选举", "总统", "首相", "议会")),
)
# Title keywords for these tags are specific enough to correct even a vocabulary tag the AI
# picked (埃博拉 is 公卫 whatever the AI says); broad ones (时政 经济 产业 社会 环境) are not.
DECISIVE_TITLE_TAGS = frozenset({"两岸", "公卫", "冲突", "灾害", "航空", "能源", "司法", "外交", "金融", "科技", "体育"})
# Out-of-vocabulary tags the AI writes for a vocabulary tag, used when the title says nothing.
TAG_SYNONYMS = {"公共卫生": "公卫", "卫生": "公卫", "医疗": "公卫", "政治": "时政", "财经": "经济", "军事": "冲突",
                "战争": "冲突", "灾难": "灾害", "法治": "司法", "反腐": "司法", "民生": "社会"}
# Events whose headlines vary a lot: titles naming one group are the same story at a
# lower character overlap. A group lists the ways one event is written.
HOT_NEWS_TERM_GROUPS = (
    ("伊朗",), ("空袭",), ("煤矿",), ("太空人",), ("黎巴嫩",), ("基辅",), ("教宗",),
    ("巴以", "以巴", "加沙", "巴勒斯坦", "约旦河西岸"),
)
# Tag for an RSS-only card, from its chinanews.com.cn desk.
SOURCE_DESK_TAGS = {"要闻": "要闻", "国内": "时政", "财经": "经济", "社会": "社会", "即时": "要闻"}
# The AI offers a few more world stories than the column shows, so one dropped by the
# ref and vagueness checks below leaves the next candidate instead of a gap.
WORLD_CANDIDATES_MAX = WORLD_NEWS_MAX + 2
# Topic summaries the AI writes in place of a story when it abstracts the names away
# ("加剧冲突风险的国际局势", "能源与地缘冲突报道增多"); such a world story is dropped.
VAGUE_NEWS_PHRASES = (
    "国际局势", "地区局势", "局势再度", "局势持续", "报道增多", "多方报道", "持续变化",
    "动向持续", "再度波动", "争议扩散", "引发关注", "引关注", "成为焦点",
)

# --- The prompt --------------------------------------------------------------------------
# Changing what the brief asks for starts here. The rules are grouped by what they govern;
# news_request assembles them with the numbered RSS items into the user message.
NEWS_SYSTEM_PROMPT = (
    "你是中文新闻编辑。只根据用户提供的 RSS 条目写简体中文整点新闻。"
    "所有中文必须使用简体中文，不得使用繁体中文或港澳台繁体词形。"
    "所有用户可见文字必须是简体中文；常见英文地名、人名、组织名必须译成通用简体中文。"
    "mainland 新闻只能来自 section=mainland 的 RSS 条目，world 新闻只能来自 section=world 的 RSS 条目。"
    "优先选择 recent_titles 中没有出现过的新标题；候选不足时才复用近几天出现过的事件。"
    "新闻必须强调今天或最近一次更新的具体变化，标题要具体，避免宏观空话。"
    "输出必须是一个 JSON object，不要 Markdown。"
)
NEWS_OUTPUT_SCHEMA = {
    "lead": {"ref": "所依据条目的 id", "section": "mainland 或 world", "tag": "固定分类之一",
             "title": "头条标题，不超过18字", "why": "两句话事实，共40到56字"},
    "mainland": [{"ref": "所依据条目的 id", "tag": "固定分类之一", "title": "大陆新闻标题，不超过18字",
                  "why": "一句话事实，不超过30字"}],
    "world": [{"ref": "所依据条目的 id", "tag": "固定分类之一", "title": "世界新闻标题，不超过20字",
               "why": "一句话事实，不超过30字"}],
    "sources": ["来源名"],
}
NEWS_RULES = {
    # Every story names the one RSS item it comes from; checked by _grounded_world_stories.
    "grounding": (
        "lead、mainland、world 的每一条都用 ref 写出它所依据的那一条 RSS 条目的 id；一条新闻只写一个条目，同一个 ref 只能用一次，不得把几条合并或概括成一条",
        "如果某个分区素材不足，宁可少写，不得跨区挪用，也不得编造新闻",
        "mainland 和 world 不允许使用 market_snapshot、股票指数或你自己的背景知识生成新闻",
        "不得写 RSS items 中不存在的人名、机构、政策或市场事件",
        "sources 只能从 items 里已有的 source 字段原样选择，最多5个，不得自造来源名",
    ),
    "language": (
        "所有输出字段只使用简体中文；如果素材是繁体中文，必须转换为简体中文再写入 JSON",
        "不要留下英文单词或英文地名，例如 Moscow 必须写成莫斯科，United States 必须写成美国，Iran 必须写成伊朗",
        "人名、地名、机构名用通用中文译名；拿不准译名时写成身份或类别（例如“美国一亲以色列游说团体”“沙特一机场”），不要因此删掉事件本身，也不要把英文原词放进 title、why 或 lede",
        "中文词内部不要加空格，例如写“美伊谈判”，不要写“美 伊谈判”",
    ),
    "selection": (
        "lead 是两个分区中今天最重要的一条新变化，必须来自 RSS 条目，section 写它来自 mainland 还是 world；lead 不得再出现在 mainland 或 world 中",
        "lead 选重大硬新闻（重大政策、冲突、灾害、关键经济数据、外交），不要选人物表彰、企业宣传、活动启幕、纪念活动这类软新闻",
        "section=mainland 的素材里如果是外国新闻（例如乌克兰、美国、中东的事件），不要放进 mainland；mainland 只写中国大陆的事件",
        "表彰、荣誉、先进个人评选和企业宣传不进 mainland，除非没有其他素材",
        "mainland 给 5 到 7 条，只能选择 section=mainland 的 RSS 条目，优先大陆时政、经济、社会、公共安全和重大政策更新",
        "world 按重要性给 6 到 8 条（页面显示通过核对的前 7 条），只能选择 section=world 的 RSS 条目，优先国际冲突、外交、政策、事故、市场、法律和重大科技治理",
        "同一事件只能出现一次，不要用不同标题重复同一军事行动、谈判或事故",
        "优先避开 recent_titles 中的近几天已展示标题，除非该分区没有足够新素材",
        "不要把人生、心理健康、生活方式、科普解释稿、旧背景稿放进新闻列表，除非它们是当天重大政策或公共事件",
    ),
    "writing": (
        "world 的 title 是所依据英文标题的中文译写：保留主体、地点、动作、结果和数字，例如 Earthquake kills 12 in central Chile 写成“智利中部地震致12人死亡”；why 从同一条的 summary 里取标题以外的一个事实",
        "禁止把新闻写成话题概括，例如“国际局势”“地区局势”“相关报道增多”“多方报道显示”“持续变化”“再度波动”；每条都必须是一件具体的事",
        "title 中大陆新闻和 lead 不超过18字，世界新闻不超过20字，写清主体和动作，不要标点堆砌",
        "title 必须包含具体人物/机构/地点/事件动作/结果，禁止只写宏观分类",
        "避免使用“引发讨论”“风险升级”“议题焦点”“全球媒体聚焦”这类宽泛标题，除非同时写清具体事件",
        "why 写一句话事实：谁、做了什么、关键数字或结果，不超过30字；不要写意义、影响、体现、凸显、彰显之类的评价，也不要写“成为关注焦点”“引发关注”“引发讨论”“持续发酵”“局势紧张”这类空话；why 要补充标题以外的信息，不能复述标题",
        "lead 的 why 写两句话，共40到56字：第一句写发生了什么，第二句写关键数字、原因、各方回应或下一步；两句都只取所依据条目 summary 里的事实",
        "尽量保留素材里的数字、地点、人物、机构和动作",
        "tag 只能从以下分类中选一个：" + "；".join(f"{tag}（{meaning}）" for tag, meaning in NEWS_TAG_DEFINITIONS)
        + "；都不合适时写 要闻",
    ),
}


def _story_schema(*, lead=False):
    properties = {
        "ref": {"type": "string", "description": "所依据 RSS 条目的 id"},
        "tag": {"type": "string", "enum": list(dict.fromkeys((*NEWS_TAGS, FALLBACK_NEWS_TAG)))},
        "title": {"type": "string"},
        "why": {"type": "string", "description": "两句话事实" if lead else "一句话事实"},
    }
    if lead:
        properties["section"] = {"type": "string", "enum": ["mainland", "world"]}
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


# The reply's shape, enforced by the OpenAI Responses API (Structured Outputs), so the
# brief always parses and every story carries its ref, a vocabulary tag and its section.
NEWS_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lead": _story_schema(lead=True),
        "mainland": {"type": "array", "items": _story_schema()},
        "world": {"type": "array", "items": _story_schema()},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["lead", "mainland", "world", "sources"],
}


def summary_model(value: Any) -> str:
    """The summary model a setting asks for; a saved earlier default follows the current one."""
    model = str(value or "").strip()
    return DEFAULT_MODEL if not model or model in LEGACY_DEFAULT_MODELS else model


def groq_model(value: Any) -> str:
    """The Groq model a setting asks for; a saved model Groq has shut down follows the default."""
    model = str(value or "").strip()
    return DEFAULT_GROQ_MODEL if not model or model in LEGACY_GROQ_MODELS else model


def reasoning_effort(model: str) -> str | None:
    """The reasoning effort for an OpenAI model family, None for a model without it."""
    return next((effort for family, effort in REASONING_EFFORTS.items() if str(model).startswith(family)), None)


def model_unavailable(exc: Exception) -> bool:
    """Whether OpenAI refused the model itself (unknown or not open to this account)."""
    status = getattr(exc, "status_code", None)
    text = str(exc).casefold()
    return status in (403, 404) or "model_not_found" in text or "does not have access to model" in text


def news_request(items: list[dict[str, str]], recent_titles: list[str] | None, now: datetime,
                 style: Any = None) -> dict[str, Any]:
    """The user message: date, recent titles to avoid, the output shape, every rule and the
    RSS items, numbered so each story can name the one it comes from."""
    return {
        "date": now.strftime("%Y-%m-%d"),
        "style": style or "china_global",
        "recent_titles": list(recent_titles or [])[:24],
        "output_schema": NEWS_OUTPUT_SCHEMA,
        "rules": [rule for group in NEWS_RULES.values() for rule in group],
        "items": [{"id": str(index), **item} for index, item in enumerate(items, 1)],
    }


class NewsBriefMixin:
    """AI summary, checks against the RSS items, visible-text cleanup and the RSS fallback."""

    def _summarize_with_ai(
        self,
        openai_key: str,
        groq_key: str,
        model: str,
        settings,
        items: list[dict[str, str]],
        market_snapshot: dict[str, Any],
        now: datetime,
        recent_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        backends = []
        if openai_key:
            backends.append({
                "provider": "openai",
                "api_key": openai_key,
                "model": model,
                "base_url": None,
            })
            if model != PREVIOUS_MODEL:
                backends.append({
                    "provider": "openai",
                    "api_key": openai_key,
                    "model": PREVIOUS_MODEL,
                    "base_url": None,
                    "only_after_refusal": True,
                })
        if groq_key:
            backends.append({
                "provider": "groq",
                "api_key": groq_key,
                "model": groq_model(settings.get("groq_model") or settings.get("ai_groq_model")),
                "base_url": "https://api.groq.com/openai/v1",
            })

        errors = []
        refused = False
        for backend in backends:
            if backend.get("only_after_refusal") and not refused:
                continue
            try:
                brief = self._summarize_with_openai(
                    backend["api_key"],
                    backend["model"],
                    settings,
                    items,
                    market_snapshot,
                    now,
                    recent_titles=recent_titles,
                    provider=backend["provider"],
                    base_url=backend["base_url"],
                )
                brief["_model"] = backend["model"] if backend["provider"] == "openai" else f"groq:{backend['model']}"
                brief["_used_ai"] = True
                return brief
            except Exception as exc:
                refused = backend["provider"] == "openai" and model_unavailable(exc)
                reason = self._ai_error_reason(exc)
                errors.append(f"{backend['provider']}: {reason}")
                logger.warning("Daily AI news provider failed: %s", errors[-1])

        raise RuntimeError("; ".join(errors) or "No AI provider is configured.")

    def _summarize_with_openai(
        self,
        api_key: str,
        model: str,
        settings,
        items: list[dict[str, str]],
        market_snapshot: dict[str, Any],
        now: datetime,
        recent_titles: list[str] | None = None,
        provider: str = "openai",
        base_url: str | None = None,
    ) -> dict[str, Any]:
        client_kwargs = {
            "api_key": api_key,
            "timeout": DEFAULT_AI_TIMEOUT_SECONDS,
            "max_retries": 0,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        if provider == "groq":
            items = list(items)[:GROQ_MAX_ITEMS]
        system = NEWS_SYSTEM_PROMPT
        user_text = json.dumps(news_request(items, recent_titles, now, settings.get("region_focus")),
                               ensure_ascii=False)
        try:
            response_kwargs = {
                "model": model,
                "instructions": system,
                "input": user_text,
                "max_output_tokens": AI_MAX_OUTPUT_TOKENS,
                "text": {"format": {"type": "json_schema", "name": "news_brief", "strict": True,
                                    "schema": NEWS_JSON_SCHEMA}},
            }
            effort = reasoning_effort(model)
            if effort:
                # Never minimal: a think noticeably improves the lead choice, the
                # two-sentence lead fact and the translations.
                response_kwargs["reasoning"] = {"effort": effort}
                response_kwargs["text"]["verbosity"] = "low"
            if provider == "openai":
                response = client.responses.create(**response_kwargs)
                content = (getattr(response, "output_text", "") or "").strip()
            else:
                raise RuntimeError(f"{provider} does not support Responses API in this plugin")
        except Exception as exc:
            logger.warning("Responses API unavailable for %s/%s, falling back to chat completions: %s", provider, model, exc)
            chat_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text},
                ],
            }
            if provider == "groq":
                # gpt-oss thinks briefly and answers in plain JSON within the free tier's budget.
                chat_kwargs["response_format"] = {"type": "json_object"}
                if "gpt-oss" in model:
                    chat_kwargs["reasoning_effort"] = "low"
            limit = GROQ_MAX_COMPLETION_TOKENS if provider == "groq" else 3000
            try:
                response = client.chat.completions.create(max_completion_tokens=limit, **chat_kwargs)
            except TypeError:
                response = client.chat.completions.create(max_tokens=limit, **chat_kwargs)
            content = (response.choices[0].message.content or "").strip()

        if not content:
            raise RuntimeError("OpenAI returned an empty summary.")
        return self._checked_brief(content, items, recent_titles)

    def _checked_brief(self, content: str, items: list[dict[str, str]],
                       recent_titles: list[str] | None) -> dict[str, Any]:
        """The AI's reply as a page brief, step by step: parse the JSON, check every story
        against the RSS items, keep only real source names, clean the visible text."""
        brief = self._parse_brief_json(content)
        brief = self._postprocess_brief_news(brief, items, recent_titles)
        brief["sources"] = self._clean_brief_sources(brief.get("sources"), items)
        brief = self._sanitize_brief_visible_text(brief)
        if not str(brief.get("lede") or "").strip():
            brief["lede"] = self._fallback_lede(brief["top"])
        return self._simplify_chinese_payload(brief)

    def _parse_brief_json(self, content: str) -> dict[str, Any]:
        data = self._load_brief_json_object(content)
        top = self._as_list(data.get("top"), MAINLAND_NEWS_MAX + WORLD_NEWS_MAX)
        mainland = self._as_list(
            data.get("mainland") or data.get("mainland_news") or data.get("china") or data.get("domestic"),
            MAINLAND_NEWS_MAX,
        )
        world = self._as_list(
            data.get("world") or data.get("world_news") or data.get("quick") or data.get("international"),
            WORLD_CANDIDATES_MAX,
        )
        lead = data.get("lead") if isinstance(data.get("lead"), dict) else None
        lead = lead if lead and str(lead.get("title") or "").strip() else None
        lede = str((lead or {}).get("title") or data.get("lede") or "").strip()
        top_items = self._merge_section_news(mainland, world) or top
        if not lede or lede == "今日新闻简报已生成。":
            lede = self._fallback_lede(top_items)
        return self._simplify_chinese_payload({
            "lede": lede[:48],
            "lead": lead,
            "mainland": mainland,
            "world": world,
            "top": top_items,
            "sources": self._as_list(data.get("sources"), 5),
        })

    def _load_brief_json_object(self, content: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for candidate in self._json_object_candidates(content):
            repaired = self._strip_trailing_json_commas(candidate)
            for payload in (candidate, repaired):
                try:
                    # strict=False: models sometimes leave a raw newline or tab inside a string.
                    data = json.loads(payload, strict=False)
                except json.JSONDecodeError as exc:
                    last_error = exc
                    continue
                if isinstance(data, dict):
                    return data
                last_error = ValueError("AI summary JSON was not an object.")
        if last_error:
            raise last_error
        raise ValueError("AI summary JSON was empty.")

    @staticmethod
    def _json_object_candidates(content: str) -> list[str]:
        text = str(content or "").strip()
        candidates = [text] if text else []
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            extracted = match.group(0).strip()
            if extracted and extracted not in candidates:
                candidates.append(extracted)
        return candidates

    @staticmethod
    def _strip_trailing_json_commas(text: str) -> str:
        output = []
        in_string = False
        escaped = False
        length = len(text)
        for index, char in enumerate(text):
            if in_string:
                output.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                output.append(char)
                continue
            if char == ",":
                next_index = index + 1
                while next_index < length and text[next_index] in " \t\r\n":
                    next_index += 1
                if next_index < length and text[next_index] in "}]":
                    continue
            output.append(char)
        return "".join(output)

    def _as_list(self, value: Any, limit: int) -> list[Any]:
        if isinstance(value, list):
            return value[:limit]
        if value:
            return [value]
        return []

    def _postprocess_brief_news(
        self,
        brief: dict[str, Any],
        items: list[dict[str, str]],
        recent_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = dict(brief)
        mainland_sources = [item for item in items if self._item_section(item) == "mainland"]
        world_sources = [item for item in items if self._item_section(item) == "world"]
        mainland = self._as_list(payload.get("mainland"), MAINLAND_NEWS_MAX)
        world = self._as_list(payload.get("world"), WORLD_CANDIDATES_MAX)

        if not mainland and not world:
            mainland, world = self._split_legacy_top_sections(payload.get("top"))

        world = self._grounded_world_stories(world, items)
        mainland = self._dedupe_top_items(
            mainland,
            mainland_sources,
            MAINLAND_NEWS_MAX,
            require_source_match=True,
            recent_titles=recent_titles,
        )
        world = self._dedupe_top_items(
            world,
            world_sources,
            WORLD_NEWS_MAX,
            recent_titles=recent_titles,
        )
        top = self._merge_section_news(mainland, world)
        if not top:
            top = self._dedupe_top_items(
                payload.get("top") or [],
                items,
                MAINLAND_NEWS_MAX + WORLD_NEWS_MAX,
                recent_titles=recent_titles,
            )
            mainland, world = self._split_legacy_top_sections(top)

        mainland = self._without_world_copies(mainland, world)
        top = self._merge_section_news(mainland, world) or top
        lead = self._checked_lead(payload.get("lead"), mainland_sources, items)
        if not lead:
            # Without a checked lead the page promotes the first de-duplicated story.
            payload["lede"] = ""
        if lead:
            lead_ref = self._story_ref(payload.get("lead"))
            mainland = [item for item in mainland if not self._similar_news_title(self._news_text(item)[0], lead["title"])]
            world = [item for item in world if not self._similar_news_title(self._news_text(item)[0], lead["title"])
                     and not (lead_ref and self._story_ref(item) == lead_ref)]
            top = self._merge_section_news(mainland, world)
            payload["lede"] = lead["title"]

        payload["lead"] = lead
        payload["mainland"] = [self._trimmed_news_item(item) for item in mainland]
        payload["world"] = [self._trimmed_news_item(item) for item in world]
        payload["top"] = [self._trimmed_news_item(item) for item in top]
        if not str(payload.get("lede") or "").strip():
            payload["lede"] = self._fallback_lede(top)
        return payload

    def _without_world_copies(self, mainland: list[Any], world: list[Any]) -> list[Any]:
        """Mainland stories minus those a world story already covers.

        The mixed 即时 desk carries foreign news too; a copy of a dedicated world-feed
        story is international news, so it stays in 国际 and leaves 国内.
        """
        world_titles = [self._news_text(item)[0] for item in world if item]
        return [
            item
            for item in mainland
            if item and not any(self._similar_news_title(self._news_text(item)[0], title) for title in world_titles)
        ]

    def _checked_lead(self, lead: Any, mainland_sources: list[dict[str, str]],
                      items: list[dict[str, str]] = ()) -> dict[str, str] | None:
        """The AI's lead story, kept only when it is one of the RSS items.

        A mainland lead must match a mainland RSS title, as mainland items do; a world
        lead is a translation of an English item, so like world items it is checked by
        its ref (when it gives one) and must name a specific story.
        """
        if not isinstance(lead, dict):
            return None
        ref = self._story_ref(lead)
        lead = self._trimmed_news_item(lead, fact_max=LEAD_FACT_MAX_CHARS)
        if not lead.get("title"):
            return None
        source_titles = [str(source.get("title") or "") for source in mainland_sources]
        matches_mainland = self._matches_source_title(lead["title"], source_titles)
        section = str(lead.get("section") or "").strip().lower()
        if section == "mainland" and not matches_mainland:
            return None
        if matches_mainland:
            source = next((item for item in mainland_sources
                           if self._matches_source_title(lead["title"], [str(item.get("title") or "")])), None)
        else:
            source = self._referenced_item(ref, items)
            if (ref and (source is None or self._item_section(source) != "world")) or self._is_vague_story(lead):
                return None
        lead["section"] = "mainland" if matches_mainland else "world"
        # Where the story came from, so the page can show the outlet's logo beside it.
        if isinstance(source, dict):
            lead["source"] = str(source.get("source") or "")[:24]
            lead["link"] = str(source.get("link") or "")[:220]
        return lead

    def _grounded_world_stories(self, world: list[Any], items: list[dict[str, str]]) -> list[Any]:
        """World stories that each name one world RSS item by ref, once, as a specific story.

        A world story is a translation of an English headline, so its title cannot be
        matched against the source; the ref is the check instead. A brief without any
        refs comes from before they were asked for and keeps its stories.
        """
        if not any(self._story_ref(story) for story in world):
            return [story for story in world if not self._is_vague_story(story)]
        used, grounded = set(), []
        for story in world:
            ref = self._story_ref(story)
            source = self._referenced_item(ref, items)
            if source is None or self._item_section(source) != "world" or ref in used or self._is_vague_story(story):
                continue
            used.add(ref)
            grounded.append(story)
        return grounded

    @staticmethod
    def _story_ref(story: Any) -> str:
        return NewsBriefMixin._field_text(story.get("ref")) if isinstance(story, dict) else ""

    @staticmethod
    def _referenced_item(ref: str, items: list[dict[str, str]]) -> dict[str, str] | None:
        """The RSS item a story's ref names; refs are the 1-based ids the prompt numbered."""
        if not ref.isdigit() or not 1 <= int(ref) <= len(items):
            return None
        return items[int(ref) - 1]

    def _is_vague_story(self, story: Any) -> bool:
        title = self._news_text(story)[0]
        return any(phrase in title for phrase in VAGUE_NEWS_PHRASES)

    @staticmethod
    def _trimmed_news_item(item: Any, fact_max: int = NEWS_FACT_MAX_CHARS) -> Any:
        """Bound a story's visible fields: short title, one-sentence fact (the lead's is two),
        2-4 character tag."""
        if not isinstance(item, dict):
            return item
        trimmed = dict(item)
        # The ref only checks a story against its source; it is not shown or cached.
        trimmed.pop("ref", None)
        if "tag" in trimmed:
            # Mapped from the AI's full tag, before any truncation.
            trimmed["tag"] = NewsBriefMixin._normalized_tag(trimmed["tag"], trimmed.get("title"))
        limits = {"title": NEWS_TITLE_MAX_CHARS, "why": fact_max, "tag": NEWS_TAG_MAX_CHARS}
        for key, limit in limits.items():
            if key in trimmed:
                trimmed[key] = NewsBriefMixin._field_text(trimmed.get(key))[:limit]
        return trimmed

    @staticmethod
    def _normalized_tag(tag: Any, title: Any) -> str:
        """A tag from the fixed vocabulary.

        A vocabulary tag stays unless the title has a decisive keyword for another tag.
        Anything else is placed by the title's keywords, then by a vocabulary tag or known
        synonym inside the AI's tag, and finally falls back to 要闻.
        """
        tag = NewsBriefMixin._field_text(tag)
        title = str(title or "")
        guess = next((vocabulary_tag for vocabulary_tag, words in TAG_TITLE_KEYWORDS
                      if any(word in title for word in words)), "")
        if tag in NEWS_TAGS:
            return guess if guess in DECISIVE_TITLE_TAGS and guess != tag else tag
        if guess:
            # 要闻 means the AI was unsure, so any title keyword places the story.
            return guess
        for vocabulary_tag in NEWS_TAGS:
            if vocabulary_tag in tag:
                return vocabulary_tag
        for synonym, vocabulary_tag in TAG_SYNONYMS.items():
            if synonym in tag:
                return vocabulary_tag
        return FALLBACK_NEWS_TAG

    @staticmethod
    def _field_text(value: Any) -> str:
        """A visible text field from AI output: a string, a number, or the first string of a list."""
        if isinstance(value, (list, tuple)):
            value = next(
                (
                    entry
                    for entry in value
                    if isinstance(entry, (str, int, float)) and not isinstance(entry, bool) and str(entry).strip()
                ),
                "",
            )
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return ""
        return str(value).strip()

    @staticmethod
    def _summary_fact(summary: Any) -> str:
        """The first sentence of an RSS summary, without its dateline or 据…消息 attribution."""
        text = re.sub(r"\s+", " ", str(summary or "")).strip()
        text = re.sub(r"^\S{0,24}?\d{1,2}月\d{1,2}日电\s*(?:[（(][^）)]{0,24}[）)])?\s*", "", text)
        text = re.sub(r"^(?:\d{1,2}月)?\d{1,2}日[，,]\s*", "", text)
        text = re.sub(r"^据[^，,。]{0,24}消息[，,]\s*", "", text)
        return re.split(r"[。！？]", text, maxsplit=1)[0].strip()[:36]

    def _source_tag(self, source: Any) -> str:
        """An RSS-only card's tag: its chinanews desk, else a generic 要闻."""
        desk = re.sub(r"^(中国新闻网|中新网)", "", str(source or "").strip())
        return SOURCE_DESK_TAGS.get(desk, "要闻")

    def _split_legacy_top_sections(self, top: Any) -> tuple[list[Any], list[Any]]:
        top_items = self._as_list(top, MAINLAND_NEWS_MAX + WORLD_NEWS_MAX)
        left_count = min(3, len(top_items))
        return top_items[:left_count], top_items[left_count:left_count + WORLD_NEWS_MAX]

    @staticmethod
    def _merge_section_news(mainland: list[Any], world: list[Any]) -> list[Any]:
        return list(mainland or [])[:MAINLAND_NEWS_MAX] + list(world or [])[:WORLD_NEWS_MAX]

    def _clean_brief_sources(self, sources: Any, items: list[dict[str, str]]) -> list[str]:
        allowed = []
        for item in items:
            source = self._simplify_chinese_text(str(item.get("source") or "").strip())
            if source and source not in allowed:
                allowed.append(source)
        if not allowed:
            return [self._simplify_chinese_text(str(source).strip()) for source in self._as_list(sources, 5) if str(source).strip()]

        normalized_allowed = {self._normalize_source_label(source): source for source in allowed}
        cleaned = []
        for source in self._as_list(sources, 5):
            source_text = self._simplify_chinese_text(str(source or "").strip())
            if not source_text:
                continue
            allowed_source = normalized_allowed.get(self._normalize_source_label(source_text))
            if allowed_source and allowed_source not in cleaned:
                cleaned.append(allowed_source)
        return cleaned[:5] if cleaned else allowed[:5]

    def _normalize_source_label(self, source: str) -> str:
        return re.sub(r"[\W_]+", "", source, flags=re.UNICODE).casefold()

    def _sanitize_brief_visible_text(self, brief: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(brief)
        sanitized["lede"] = self._strip_untranslated_english_terms(str(sanitized.get("lede") or ""))

        def cleaned_story(item: dict[str, Any]) -> dict[str, Any]:
            cleaned_item = dict(item)
            for key in ("title", "why", "tag"):
                if key in cleaned_item or key != "tag":
                    cleaned_item[key] = self._strip_untranslated_english_terms(str(cleaned_item.get(key) or ""))
            return cleaned_item

        for list_name, limit in (
            ("mainland", MAINLAND_NEWS_MAX),
            ("world", WORLD_NEWS_MAX),
            ("top", MAINLAND_NEWS_MAX + WORLD_NEWS_MAX),
        ):
            cleaned_items = []
            for item in self._as_list(sanitized.get(list_name), limit):
                if isinstance(item, dict):
                    cleaned_items.append(cleaned_story(item))
                else:
                    cleaned_items.append(self._strip_untranslated_english_terms(str(item or "")))
            sanitized[list_name] = cleaned_items
        if isinstance(sanitized.get("lead"), dict):
            sanitized["lead"] = cleaned_story(sanitized["lead"])

        return sanitized

    def _strip_untranslated_english_terms(self, text: str) -> str:
        cleaned = self._simplify_chinese_text(text)

        def replace_match(match: re.Match[str]) -> str:
            term = match.group(0)
            canonical = term.replace(".", "").replace("-", "").upper()
            if canonical in VISIBLE_ENGLISH_ALLOWLIST:
                return term
            # Brand and agency names stay as Chinese media print them: camel case
            # (SpaceX, OpenAI, iPhone) or a short all-caps acronym (FAA, NASA).
            if any(char.isupper() for char in term[1:]) and any(char.islower() for char in term):
                return term
            letters = term.replace(".", "").replace("-", "")
            if letters.isalpha() and letters.isupper() and 2 <= len(letters) <= 6:
                return term
            # A word between numbers is a model designation, e.g. 737 MAX 10.
            before = match.string[: match.start()].rstrip()[-1:]
            after = match.string[match.end():].lstrip()[:1]
            if before.isdigit() and after.isdigit():
                return term
            return ""

        cleaned = VISIBLE_ENGLISH_WORD_RE.sub(replace_match, cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
        cleaned = re.sub(r"\s+([，。；：、])", r"\1", cleaned)
        cleaned = re.sub(r"([（(])\s+", r"\1", cleaned)
        cleaned = re.sub(r"\s+([）)])", r"\1", cleaned)
        return self._tidy_visible_text(cleaned)

    @staticmethod
    def _tidy_visible_text(text: str) -> str:
        """Drop the space between Latin text and Chinese characters, and any separator a
        removed word left at either end, e.g. "/特斯拉". Safe on already-clean text."""
        text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])\s+(?=[\u4e00-\u9fff])", "", str(text or ""))
        # A 中新网 channel prefix such as "中新健康：" names the desk, not the story.
        text = re.sub(r"^中新[^\s:：|｜]{0,3}\s*[:：|｜]\s*", "", text)
        return re.sub(r"^[\s/／、，,·]+|[\s/／、，,·]+$", "", text).strip()

    def _rss_news_cards(
        self,
        items: list[dict[str, str]],
        limit: int,
        existing: list[Any] | None = None,
        recent_titles: list[str] | None = None,
    ) -> list[Any]:
        result = list(existing or [])[:limit]
        delayed_recent = []
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title or not self._is_chinese_title(title):
                continue
            if any(self._similar_news_title(title, self._news_text(existing_item)[0]) for existing_item in result):
                continue
            source = str(item.get("source") or "").strip()
            summary = str(item.get("summary") or "").strip()
            why = self._summary_fact(summary) or source[:36] or "来自 RSS 源。"
            card = {"title": title[:32], "why": why, "tag": self._source_tag(source)}
            if self._matches_recent_news_title(title, recent_titles):
                delayed_recent.append(card)
                continue
            result.append(card)
            if len(result) >= limit:
                break
        for card in delayed_recent:
            if len(result) >= limit:
                break
            if any(self._similar_news_title(card["title"], self._news_text(existing_item)[0]) for existing_item in result):
                continue
            result.append(card)
        return result[:limit]

    def _rss_only_brief(
        self,
        items: list[dict[str, str]],
        warning: str,
        recent_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        mainland_sources = [item for item in items if self._item_section(item) == "mainland"]
        world_sources = [item for item in items if self._item_section(item) == "world"]
        mainland = self._rss_news_cards(mainland_sources, MAINLAND_NEWS_MAX, recent_titles=recent_titles)
        world = self._rss_news_cards(world_sources, WORLD_NEWS_MAX, recent_titles=recent_titles)
        if not mainland and not world:
            mainland, world = self._split_legacy_top_sections(self._rss_news_cards(items, MAINLAND_NEWS_MAX + WORLD_NEWS_MAX))
        top = self._merge_section_news(mainland, world)

        sources = []
        for item in mainland_sources + world_sources:
            source = str(item.get("source") or "").strip()
            if source and source not in sources:
                sources.append(source)

        return {
            "lede": self._fallback_lede(top),
            "mainland": mainland,
            "world": world,
            "top": top,
            "lead": None,
            "sources": sources[:5],
            "_model": "rss-fallback",
            "_used_ai": False,
            "_fallback_warning": warning,
        }

    def _fallback_lede(self, top: list[Any]) -> str:
        if top:
            headline, _why = self._news_text(top[0])
            if headline:
                return headline[:32]
        return "今日硬新闻更新"

    def _dedupe_top_items(
        self,
        top: list[Any],
        source_items: list[dict[str, str]],
        limit: int = 7,
        *,
        require_source_match: bool = False,
        recent_titles: list[str] | None = None,
    ) -> list[Any]:
        result = []
        delayed_recent = []
        recent_titles = list(recent_titles or [])
        source_titles = [
            str(source.get("title") or "").strip()
            for source in source_items
            if str(source.get("title") or "").strip()
        ]

        def append_candidate(candidate: Any, headline: str) -> bool:
            if any(self._similar_news_title(headline, self._news_text(existing)[0]) for existing in result):
                return False
            if self._matches_recent_news_title(headline, recent_titles):
                delayed_recent.append(candidate)
                return False
            result.append(candidate)
            return True

        for item in top:
            headline, _why = self._news_text(item)
            if not headline:
                continue
            if require_source_match and source_titles and not self._matches_source_title(headline, source_titles):
                continue
            append_candidate(item, headline)
            if len(result) >= limit:
                return result

        for source in source_items:
            title = str(source.get("title") or "").strip()
            if not title or not self._is_chinese_title(title):
                continue
            summary = self._summary_fact(source.get("summary")) or str(source.get("source") or "").strip()[:36]
            card = {"title": title[:32], "why": summary, "tag": self._source_tag(source.get("source"))}
            append_candidate(card, title)
            if len(result) >= limit:
                break

        for item in delayed_recent:
            if len(result) >= limit:
                break
            headline, _why = self._news_text(item)
            if not headline:
                continue
            if any(self._similar_news_title(headline, self._news_text(existing)[0]) for existing in result):
                continue
            result.append(item)
        return result

    def _matches_source_title(self, headline: str, source_titles: list[str]) -> bool:
        return any(self._similar_news_title(headline, title) for title in source_titles)

    def _ai_error_reason(self, exc: Exception) -> str:
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        text = re.sub(r"\s+", " ", str(exc or "")).strip()
        if len(text) > 180:
            text = text[:180].rstrip()
        return f"HTTP {status}: {text}" if status else (text or type(exc).__name__)

    def _similar_news_title(self, left: str, right: str) -> bool:
        left_chars = {char for char in left if "\u4e00" <= char <= "\u9fff"}
        right_chars = {char for char in right if "\u4e00" <= char <= "\u9fff"}
        if not left_chars or not right_chars:
            return False
        overlap = len(left_chars & right_chars) / max(1, min(len(left_chars), len(right_chars)))
        if overlap >= 0.56:
            return True
        for group in HOT_NEWS_TERM_GROUPS:
            left_named = [term for term in group if term in left]
            right_named = [term for term in group if term in right]
            if not left_named or not right_named:
                continue
            # Both name the same hot event; the words beyond its name must still overlap,
            # so two different stories about one place are not merged.
            named = set("".join(left_named + right_named))
            rest_left = left_chars - named
            rest_right = right_chars - named
            if rest_left and rest_right and len(rest_left & rest_right) / min(len(rest_left), len(rest_right)) >= 0.38:
                return True
        return False

    def _fallback_brief(self, settings, now: datetime, error: str) -> dict[str, Any]:
        return {
            "date": now.strftime("%Y-%m-%d"),
            "generated_at": now.isoformat(),
            "model": summary_model(settings.get("model")),
            "brief": {
                "lede": "AI新闻暂不可用，等待下次刷新。",
                "mainland": [{"title": "新闻简报生成失败", "why": error[:48]}],
                "world": [],
                "top": [{"title": "新闻简报生成失败", "why": error[:48]}],
                "sources": [],
            },
            "from_cache": False,
            "warning": error,
        }

    def _simplify_chinese_payload(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._simplify_chinese_text(value)
        if isinstance(value, list):
            return [self._simplify_chinese_payload(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._simplify_chinese_payload(item) for item in value)
        if isinstance(value, dict):
            return {key: self._simplify_chinese_payload(item) for key, item in value.items()}
        return value

    def _simplify_chinese_text(self, text: str) -> str:
        simplified = str(text)
        if simplified.startswith(("http://", "https://")):
            return simplified
        for traditional, replacement in TRADITIONAL_PHRASE_REPLACEMENTS:
            simplified = simplified.replace(traditional, replacement)
        for pattern, replacement in ENGLISH_NEWS_TERM_REPLACEMENTS:
            simplified = pattern.sub(replacement, simplified)
        simplified = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", simplified)
        return simplified.translate(TRADITIONAL_TO_SIMPLIFIED)

    def _allow_api_call(self, settings, date_key: str) -> bool:
        limit = common._parse_int(settings.get("daily_api_limit"), 1, 1, 20)
        state = common._safe_json_load(self._cache_dir() / "state.json", {})
        if state.get("date") != date_key:
            return True
        return int(state.get("calls") or 0) < limit

    def _record_api_call(self, date_key: str) -> None:
        state_file = self._cache_dir() / "state.json"
        state = common._safe_json_load(state_file, {})
        if state.get("date") != date_key:
            state = {"date": date_key, "calls": 0}
        state["calls"] = int(state.get("calls") or 0) + 1
        common._safe_json_write(state_file, state)
