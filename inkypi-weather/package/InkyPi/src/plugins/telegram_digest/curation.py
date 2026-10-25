"""Which unread Telegram messages reach the page.

Local rules drop ads first (and whatever the owner blocks); an AI then ranks what
is left, writes a short Chinese headline and summary and names a topic. Verdicts
are cached per message, the AI is called at most ``aiDailyLimit`` times a day and
any AI failure falls back to the rule score, so a refresh never waits on it to
show the page. Private chats are never judged as ads by the keyword rules.
"""

from __future__ import annotations

import json
import logging
import re
import time

logger = logging.getLogger(__name__)

TAGS = ("时事", "财经", "科技", "游戏", "体育", "本地", "生活", "娱乐", "私聊", "群聊", "其他")
KIND_TAGS = {"private": "私聊", "group": "群聊"}
DEFAULT_AI_MODEL = "gpt-5-mini"
DEFAULT_AI_DAILY_LIMIT = 12
AI_TIMEOUT_SECONDS = 45
# What a refresh keeps after the AI call for media and the reply.
AI_REPLY_RESERVE_SECONDS = 15
AI_MAX_ITEMS = 40
AI_TEXT_CHARS = 320
AI_MAX_OUTPUT_TOKENS = 6000
AI_CACHE_LIMIT = 300
FILTERED_KEY_LIMIT = 1000
HEADLINE_MAX_CHARS = 22
SUMMARY_MAX_CHARS = 60
# The page shows one lead and this many list rows; at most two come from one chat.
VISIBLE_COUNT = 7
PER_CHAT_VISIBLE = 2
# Focus dialogs (the owner's must-read chats) score higher by weight and get one more row,
# while the others keep at least two rows of the page.
DEFAULT_FOCUS_CHATS = "吃瓜大佬*2, 吃瓜群众*2"
FOCUS_DEFAULT_WEIGHT = 2
FOCUS_MAX_WEIGHT = 3
FOCUS_SCORE_STEP = 10
FOCUS_EXTRA_ROWS = 1
FOCUS_VISIBLE_LIMIT = VISIBLE_COUNT - 2
# A picture leads when it scores within this much of the best story.
MEDIA_LEAD_MARGIN = 10

AD_KEYWORDS = re.compile(
    "|".join((
        r"广告", r"推广", r"赞助", r"商务合作", r"商务洽谈", r"互推", r"频道推荐", r"优质频道",
        r"优惠券", r"领券", r"返利", r"返现", r"红包码", r"下单立减", r"限时秒杀", r"代购", r"招代理",
        r"代理加盟", r"招商加盟", r"博彩", r"菠菜", r"娱乐城", r"体育平台", r"百家乐", r"棋牌", r"彩票",
        r"首充", r"充值送", r"返水", r"USDT", r"免费领", r"点击领取", r"扫码", r"加群", r"进群", r"入群",
        r"私聊咨询", r"联系客服", r"在线客服", r"科学上网", r"翻墙", r"机场订阅", r"梯子",
        r"\bsponsored\b", r"\bpromo\s*code\b", r"\bairdrop\b", r"\bgiveaway\b", r"#ad\b",
    )),
    re.IGNORECASE,
)
# Click-bait that sells a link: "click on the link to watch now", "VIP zone", leaked videos.
CLICK_BAIT = re.compile(
    "|".join((
        r"click\s+(?:on\s+)?(?:the\s+)?link", r"watch\s+(?:now|link|full)", r"tap\s+now", r"vip\s+zone",
        r"exclusive\s+leaks?", r"leak(?:ed|s)?\s+(?:video|link)", r"full\s+video", r"link\s+in\s+bio",
        r"\bonly\s*fans\b", r"免费观看", r"完整版", r"看完整", r"福利视频", r"资源合集", r"点击观看",
        r"链接在评论", r"戳链接",
    )),
    re.IGNORECASE,
)
# Link shorteners and click-through pages that spam posts hide their targets behind.
SHORT_LINK = re.compile(
    r"https?://(?:www\.)?(?:lockr\.so|fast-links\.org|bit\.ly|cutt\.ly|tinyurl\.com|shorturl\.at|t\.ly|is\.gd|"
    r"ouo\.io|shrinkme\.io|adf\.ly|exe\.io|linktr\.ee|lnk\.bio|rebrand\.ly|bitly\.ws)/\S*",
    re.IGNORECASE,
)
INVITE_LINK = re.compile(r"(?:t\.me|telegram\.me)/(?:\+|joinchat/)", re.IGNORECASE)
LINK = re.compile(r"https?://\S+|(?<![\w.])t\.me/\S+", re.IGNORECASE)
MENTION = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,}")
TERM_SPLIT = re.compile(r"[,，、;；\n]+")
FOCUS_TERM = re.compile(r"^(.*?)\s*[*×xX:：]\s*(\d)$")

SYSTEM_PROMPT = (
    "你是用户的 Telegram 未读消息编辑。用户会把每条消息的对话名、对话类型（频道/群聊/私聊）、"
    "距今分钟数、是否带图和正文交给你。逐条判断：是否广告或推广（ad），对用户的重要度 score "
    "（0-100），话题 tag，并写简体中文 headline 和 summary。\n"
    "重要度：需要用户回复或行动的私聊最高；突发和重大新闻、与用户生活相关的本地通知其次；"
    "普通资讯中等；闲聊、表情、梗图、重复转发低；广告、推广、互推、引流、博彩、带货一律 ad=true、score=0。\n"
    "headline 不超过 18 个汉字，概括事实，不加引号和书名号以外的标点堆砌；summary 不超过 48 个汉字，"
    "补充最关键的细节。只根据正文写，不编造；正文为空只带图时 headline 写“<对话名>发来图片”，"
    "summary 写空字符串。私聊 tag 固定为私聊；群聊没有更合适的话题时用群聊。外文内容翻译成中文。"
)

AI_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "ad", "score", "tag", "headline", "summary"],
                "properties": {
                    "id": {"type": "string"},
                    "ad": {"type": "boolean"},
                    "score": {"type": "integer"},
                    "tag": {"type": "string", "enum": list(TAGS)},
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
    },
}


def split_terms(value):
    """Terms from a comma, 、 or newline separated setting, case-folded, blanks dropped."""
    return [term.strip().casefold() for term in TERM_SPLIT.split(str(value or "")) if term.strip()]


def focus_terms(value):
    """(name, weight) pairs from a focus setting such as "吃瓜大佬*3, 吃瓜群众"; weight 1-3."""
    terms = []
    for raw in TERM_SPLIT.split(str(value or "")):
        match = FOCUS_TERM.match(raw.strip())
        name, weight = (match.group(1), int(match.group(2))) if match else (raw, FOCUS_DEFAULT_WEIGHT)
        name = name.strip().casefold()
        if name:
            terms.append((name, max(1, min(FOCUS_MAX_WEIGHT, weight))))
    return terms


class Rules:
    """The owner's terms and chats, plus the built-in ad rules."""

    def __init__(self, settings):
        settings = settings or {}
        self.block_terms = split_terms(settings.get("blockKeywords"))
        self.boost_terms = split_terms(settings.get("boostKeywords"))
        self.block_chats = set(split_terms(settings.get("blockChats")))
        self.pin_chats = set(split_terms(settings.get("pinChats")))
        focus = settings.get("focusChats")
        self.focus_chats = focus_terms(DEFAULT_FOCUS_CHATS if focus is None else focus)

    @staticmethod
    def chat_names(item):
        username = str(item.get("chat_username") or "").strip().lstrip("@").casefold()
        names = {
            str(item.get("chat_title") or "").strip().casefold(),
            str(item.get("chat_id") or "").strip().casefold(),
            username,
            "@" + username if username else "",
        }
        names.discard("")
        return names

    def blocked_chat(self, item):
        return bool(self.block_chats & self.chat_names(item))

    def pinned(self, item):
        return bool(self.pin_chats & self.chat_names(item))

    def focus_weight(self, item):
        """The weight of the focus term found in the chat's title or username, 0 when none."""
        names = self.chat_names(item)
        return max((weight for term, weight in self.focus_chats if any(term in name for name in names)),
                   default=0)

    def ad_reason(self, item):
        """Why ``item`` is dropped before ranking, or "" when it stays."""
        text = message_text(item)
        folded = text.casefold()
        if self.blocked_chat(item):
            return "屏蔽对话"
        if any(term in folded for term in self.block_terms):
            return "屏蔽词"
        if item.get("chat_kind") == "private":
            return ""
        if INVITE_LINK.search(text):
            return "邀请链接"
        if AD_KEYWORDS.search(text):
            return "广告"
        if CLICK_BAIT.search(text):
            return "诱导点击"
        if len(SHORT_LINK.findall(text)) >= 2:
            return "推广链接"
        if len(LINK.findall(text)) >= 3 or len(MENTION.findall(text)) >= 4:
            return "链接过多"
        if item.get("has_buttons") and (LINK.search(text) or MENTION.search(text)):
            return "按钮推广"
        return ""

    def score(self, item, now_ts):
        """A rule-only importance: who sent it, the owner's terms, length, picture and age."""
        kind = item.get("chat_kind")
        value = 70 if kind == "private" else 50 if kind == "group" else 45
        text = message_text(item)
        folded = text.casefold()
        if any(term in folded for term in self.boost_terms):
            value += 15
        if self.pinned(item):
            value += 20
        value += FOCUS_SCORE_STEP * self.focus_weight(item)
        value += min(10, len(text) // 30)
        if str(item.get("media_kind") or "") in {"photo", "video"}:
            value += 5
        age_hours = max(0, now_ts - int(item.get("date") or now_ts)) // 3600
        value -= min(20, age_hours)
        return max(1, min(100, value))


def message_text(item):
    return " ".join(str(item.get("raw_text") or "").split())


def default_tag(item):
    return KIND_TAGS.get(str(item.get("chat_kind") or ""), "其他")


def _today(now):
    return now.date().isoformat()


class Curator:
    """Ranks one refresh's candidates and carries the AI cache and daily counter between refreshes."""

    def __init__(self, settings, previous_state, now, api_key="", ai_client_factory=None):
        settings = settings or {}
        previous = previous_state if isinstance(previous_state, dict) else {}
        self.mode = str(settings.get("curationMode") or "ai").strip().casefold()
        if self.mode not in {"ai", "rules", "off"}:
            self.mode = "ai"
        self.rules = Rules(settings)
        self.now = now
        self.now_ts = int(now.timestamp())
        self.api_key = str(api_key or "").strip()
        self.model = str(settings.get("aiModel") or DEFAULT_AI_MODEL).strip() or DEFAULT_AI_MODEL
        try:
            self.daily_limit = max(0, min(48, int(settings.get("aiDailyLimit") or DEFAULT_AI_DAILY_LIMIT)))
        except (TypeError, ValueError):
            self.daily_limit = DEFAULT_AI_DAILY_LIMIT
        self.ai_cache = dict(previous.get("ai") or {})
        calls = previous.get("ai_calls") if isinstance(previous.get("ai_calls"), dict) else {}
        self.ai_calls = {"date": _today(now), "count": 0}
        if calls.get("date") == self.ai_calls["date"]:
            self.ai_calls["count"] = int(calls.get("count") or 0)
        self.filtered_keys = [str(key) for key in previous.get("filtered_keys") or [] if key]
        self._filtered_set = set(self.filtered_keys)
        self.ai_client_factory = ai_client_factory
        # time.monotonic() by which curation must be done; None means no limit.
        self.deadline = None
        self.ai_used = False
        self.ai_error = ""
        self.blocked = []

    def is_known_filtered(self, key):
        return str(key) in self._filtered_set

    def note_known_filtered(self, item_or_key):
        """Count an ad the scan skipped because an earlier refresh already dropped it."""
        self.blocked.append(item_or_key if isinstance(item_or_key, dict) else {"key": str(item_or_key)})

    def curate(self, candidates):
        """Candidates without ads, best first, each with ``curation`` filled in."""
        kept = []
        for item in candidates:
            reason = "" if self.mode == "off" else self.rules.ad_reason(item)
            if reason:
                self._drop(item, reason)
                continue
            kept.append(item)
        if self.mode == "ai":
            self._apply_ai(kept)
        for item in kept:
            verdict = self.ai_cache.get(str(item.get("key"))) if self.mode == "ai" else None
            if verdict and verdict.get("ad"):
                self._drop(item, "AI 判定广告")
                continue
            item["curation"] = self._curation_fields(item, verdict)
        ranked = [item for item in kept if item.get("curation")]
        return order_for_page(ranked)

    def state(self, candidates):
        """What the next refresh needs: verdicts for these candidates first, newest others after."""
        keys = [str(item.get("key")) for item in candidates]
        kept = {key: self.ai_cache[key] for key in keys if key in self.ai_cache}
        for key, verdict in reversed(list(self.ai_cache.items())):
            if len(kept) >= AI_CACHE_LIMIT:
                break
            kept.setdefault(key, verdict)
        return {
            "mode": self.mode,
            "ai": kept,
            "ai_calls": dict(self.ai_calls),
            "ai_used": self.ai_used,
            "ai_error": self.ai_error,
            "filtered_keys": self.filtered_keys[-FILTERED_KEY_LIMIT:],
        }

    def stats(self):
        chats = []
        for item in self.blocked:
            title = str(item.get("chat_title") or "").strip()
            if title and title not in chats:
                chats.append(title)
        return {"blocked_count": len(self.blocked), "blocked_chats": chats[:3],
                "curation": "ai" if self.ai_used else self.mode}

    def _drop(self, item, reason):
        item["filtered"] = reason
        self.blocked.append(item)
        key = str(item.get("key") or "")
        if key and key not in self._filtered_set:
            self._filtered_set.add(key)
            self.filtered_keys.append(key)

    def _curation_fields(self, item, verdict):
        if verdict:
            headline = clean_line(verdict.get("headline"), HEADLINE_MAX_CHARS)
            summary = clean_line(verdict.get("summary"), SUMMARY_MAX_CHARS)
            tag = verdict.get("tag") if verdict.get("tag") in TAGS else default_tag(item)
            score = max(0, min(100, int(verdict.get("score") or 0)))
            source = "ai"
        else:
            headline = summary = ""
            tag = default_tag(item)
            score = self.rules.score(item, self.now_ts)
            source = "rules"
        if item.get("chat_kind") == "private":
            tag = "私聊"
        if self.rules.pinned(item):
            score = min(100, score + 20)
        focus = self.rules.focus_weight(item)
        if focus and source == "ai":
            score = min(100, score + FOCUS_SCORE_STEP * focus)
        return {"score": score, "tag": tag, "headline": headline, "summary": summary,
                "source": source, "pinned": self.rules.pinned(item), "focus": focus}

    def _apply_ai(self, items):
        pending = [item for item in items if str(item.get("key")) not in self.ai_cache][:AI_MAX_ITEMS]
        if not pending:
            self.ai_used = any(str(item.get("key")) in self.ai_cache for item in items)
            return
        if not self.api_key:
            self.ai_error = "未配置 OpenAI 密钥"
            return
        if self.ai_calls["count"] >= self.daily_limit:
            self.ai_error = "今日 AI 次数已用完"
            self.ai_used = any(str(item.get("key")) in self.ai_cache for item in items)
            return
        if self.deadline is not None and self.deadline - time.monotonic() < AI_TIMEOUT_SECONDS + AI_REPLY_RESERVE_SECONDS:
            # No time left for a full AI call this refresh: rules now, the AI on a later one.
            self.ai_error = "本次时间不够，先按规则排序"
            self.ai_used = any(str(item.get("key")) in self.ai_cache for item in items)
            logger.info("Telegram Digest AI ranking skipped: too little time left in this refresh.")
            return
        self.ai_calls["count"] += 1
        started = time.monotonic()
        try:
            verdicts = self._ask_ai(pending)
        except Exception as exc:  # noqa: BLE001 - any AI failure falls back to the rules.
            self.ai_error = str(exc)[:160] or type(exc).__name__
            logger.warning("Telegram Digest AI ranking failed; using rule scores: %s", self.ai_error)
            return
        for item in pending:
            verdict = verdicts.get(str(item.get("key")))
            if verdict:
                self.ai_cache[str(item.get("key"))] = verdict
        self.ai_used = True
        logger.info("Telegram Digest AI ranked %d messages in %.1fs.", len(verdicts), time.monotonic() - started)

    def _ask_ai(self, items):
        ids = {str(index): str(item.get("key")) for index, item in enumerate(items, 1)}
        request = {"items": [ai_item(index, item, self.now_ts) for index, item in zip(ids, items)]}
        client = self._client()
        response = client.responses.create(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=json.dumps(request, ensure_ascii=False),
            max_output_tokens=AI_MAX_OUTPUT_TOKENS,
            reasoning={"effort": "low"},
            text={"format": {"type": "json_schema", "name": "telegram_digest", "strict": True,
                             "schema": AI_SCHEMA}, "verbosity": "low"},
        )
        content = (getattr(response, "output_text", "") or "").strip()
        if not content:
            raise RuntimeError("AI returned no ranking")
        return checked_verdicts(content, ids)

    def _client(self):
        if self.ai_client_factory is not None:
            return self.ai_client_factory(self.api_key)
        from openai import OpenAI

        return OpenAI(api_key=self.api_key, timeout=AI_TIMEOUT_SECONDS, max_retries=0)


def ai_item(index, item, now_ts):
    kind = {"private": "私聊", "group": "群聊"}.get(str(item.get("chat_kind") or ""), "频道")
    return {
        "id": index,
        "chat": str(item.get("chat_title") or item.get("chat_username") or "")[:40],
        "type": kind,
        "minutes_ago": max(0, now_ts - int(item.get("date") or now_ts)) // 60,
        "media": str(item.get("media_kind") or "text") if item.get("media_kind") in {"photo", "video"} else "none",
        "text": message_text(item)[:AI_TEXT_CHARS],
    }


def checked_verdicts(content, ids):
    """The AI's verdicts by message key; ids it invented or skipped are left out."""
    data = json.loads(content)
    verdicts = {}
    for entry in data.get("items") or []:
        if not isinstance(entry, dict):
            continue
        key = ids.get(str(entry.get("id") or ""))
        if not key:
            continue
        tag = entry.get("tag") if entry.get("tag") in TAGS else "其他"
        try:
            score = int(entry.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        verdicts[key] = {
            "ad": bool(entry.get("ad")),
            "score": max(0, min(100, score)),
            "tag": tag,
            "headline": clean_line(entry.get("headline"), HEADLINE_MAX_CHARS),
            "summary": clean_line(entry.get("summary"), SUMMARY_MAX_CHARS),
        }
    return verdicts


def clean_line(value, limit):
    text = " ".join(str(value or "").split())
    return text[:limit]


def order_for_page(items):
    """Best first; the first VISIBLE_COUNT take at most PER_CHAT_VISIBLE from one chat.

    A focus chat may take one row more, and focus chats together leave two rows to the rest.
    """
    ranked = sorted(
        items,
        key=lambda item: (
            bool(item["curation"].get("pinned")),
            int(item["curation"].get("score") or 0),
            int(item.get("date") or 0),
        ),
        reverse=True,
    )
    visible, overflow, per_chat, focus_rows = [], [], {}, 0
    for item in ranked:
        chat = str(item.get("chat_id") or item.get("chat_title") or "")
        focus = bool(item["curation"].get("focus"))
        rows = PER_CHAT_VISIBLE + (FOCUS_EXTRA_ROWS if focus else 0)
        if (len(visible) < VISIBLE_COUNT and per_chat.get(chat, 0) < rows
                and (not focus or focus_rows < FOCUS_VISIBLE_LIMIT)):
            visible.append(item)
            per_chat[chat] = per_chat.get(chat, 0) + 1
            focus_rows += focus
        else:
            overflow.append(item)
    return visible + overflow


def split_lead(items, has_media):
    """The lead story and the rest in page order: a picture leads when it is nearly the best."""
    items = list(items or [])
    if not items:
        return {}, []
    best = int((items[0].get("curation") or {}).get("score") or 0)
    for index, item in enumerate(items[:3]):
        score = int((item.get("curation") or {}).get("score") or 0)
        if has_media(item) and score >= best - MEDIA_LEAD_MARGIN:
            return item, items[:index] + items[index + 1:]
    return items[0], items[1:]


def display_headline(item):
    curation = item.get("curation") or {}
    if curation.get("headline"):
        return curation["headline"]
    title = " ".join(str(item.get("title") or "").split())
    if title and message_text(item):
        return title
    return f"{item.get('chat_title') or '未知对话'}发来{'视频' if item.get('media_kind') == 'video' else '图片'}"


def original_excerpt(item):
    """The message's own words, links dropped, when an AI summary stands in for them; else ""."""
    if not (item.get("curation") or {}).get("headline"):
        return ""
    text = " ".join(LINK.sub(" ", message_text(item)).split())
    return "" if not text or text == display_summary(item) else text


def display_summary(item):
    curation = item.get("curation") or {}
    if curation.get("headline"):
        return curation.get("summary") or ""
    headline = display_headline(item)
    text = message_text(item)
    if text.startswith(headline):
        text = text[len(headline):].strip(" ：:，,-")
    return text
