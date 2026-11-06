"""Bounded keyless hot lists for 全网热搜: Baidu, Toutiao, Zhihu and Douyin.

These are the JSON feeds the platforms' own hot-list pages read (none is a documented API, so
each normaliser is strict and the plugin draws whatever is cached when one changes or fails).
Every fetcher returns a small dict of ranked rows: title, heat, a label and, where the platform
gives one, a summary and a picture.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from runtime.refresh_contracts import TaskCancelled
from utils.http_client import HttpClientError, get_http_client


logger = logging.getLogger(__name__)
PACIFIC = ZoneInfo("America/Los_Angeles")
BEIJING = ZoneInfo("Asia/Shanghai")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
REQUEST_TIMEOUT = (3, 9)
BAIDU_URL = "https://top.baidu.com/api/board"
TOUTIAO_URL = "https://www.toutiao.com/hot-event/hot-board/"
ZHIHU_URL = "https://api.zhihu.com/topstory/hot-lists/total"
# Douyin's web hot list (with each topic's cover, video count and start); the short billboard is
# the stand-in when it refuses.
DOUYIN_URL = "https://www.douyin.com/aweme/v1/web/hot/search/list/"
DOUYIN_PARAMS = {"device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web", "detail_list": "1"}
DOUYIN_BILLBOARD_URL = "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"
PLATFORMS = ("baidu", "toutiao", "zhihu", "douyin")
# Weibo's hot search answers only a signed-in session: it joins the page (first) when the owner
# has saved their weibo.com login cookie as WEIBO_COOKIE.
WEIBO_URL = "https://weibo.com/ajax/side/hotSearch"
COOKIE_KEY = "WEIBO_COOKIE"
ORDER = ("weibo",) + PLATFORMS
MAX_ROWS = 20
# The platforms' picture hosts (each serves from numbered mirrors): a picture is fetched only from these.
IMAGE_HOST_SUFFIXES = (".cdn.bcebos.com", ".toutiaoimg.com", ".zhimg.com", ".douyinpic.com", ".thepaper.cn",
                       ".mastodon.social")
# A Zhihu picture's path names its size after the hash (v2-<hash>_1440w.png); "_xl" is a small one.
ZHIHU_SIZED = re.compile(r"(/v2-[0-9a-f]+)(?:_[0-9a-z]+)?\.(?:png|jpe?g|webp)$")
# Each platform's labels, as the page names them (anything else is left unmarked).
BAIDU_TAGS = {"1": "新", "3": "热"}
TOUTIAO_TAGS = {"hot": "热", "new": "新", "boom": "爆", "depth": "深度", "refuteRumors": "辟谣",
                "interpretation": "解读", "exclusive": "独家", "first": "首发"}
DOUYIN_TAGS = {1: "新", 3: "热", 8: "独家"}
WEIBO_TAGS = {"新": "新", "热": "热", "沸": "沸", "爆": "爆"}
# 两个榜单的标题算同一话题：去掉标点后共有至少这么多的二字组（按较短的一边算）。
SAME_TOPIC_SHARE = 0.5
SAME_TOPIC_MIN_BIGRAMS = 3
_PUNCTUATION = re.compile(r"[\s\W_“”‘’《》「」『』【】（）()，。、：；！？!?,.:;·…—-]+", re.UNICODE)


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _count(value):
    try:
        return max(0, int(float(str(value).replace(",", ""))))
    except (TypeError, ValueError):
        return 0


def _zhihu_heat(text):
    """"1521 万热度" -> 15210000."""
    match = re.search(r"([\d.]+)\s*(万|亿)?", str(text or ""))
    if not match:
        return 0
    value = float(match.group(1))
    return int(value * {"万": 10_000, "亿": 100_000_000}.get(match.group(2), 1))


def safe_image_url(value):
    """The picture's https link when it is on one of the platforms' image hosts, else None. A Zhihu
    picture is asked for at its small size: the original can be a 1.5 MB PNG."""
    url = str(value or "").strip()
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    parts = urlsplit(url)
    if parts.scheme != "https" or not str(parts.hostname or "").endswith(IMAGE_HOST_SUFFIXES):
        return None
    if parts.hostname.endswith(".zhimg.com"):
        url = ZHIHU_SIZED.sub(r"\1_xl.jpg", url)
    return url


def _get(url, context, *, max_bytes, params=None, headers=None):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", url, context=context, timeout=REQUEST_TIMEOUT, max_bytes=max_bytes, params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    context.raise_if_cancelled()
    return response.data


def _rows(rows, platform):
    if not rows:
        raise ValueError(f"{platform} returned no hot topics")
    for rank, row in enumerate(rows[:MAX_ROWS], start=1):
        row["rank"] = rank
    return {"rows": rows[:MAX_ROWS]}


# --- Baidu: 百度热搜 实时榜 ------------------------------------------------------------------------

def normalize_baidu(payload):
    cards = (((payload or {}).get("data") or {}).get("cards")) or []
    content = (cards[0] or {}).get("content") if cards and isinstance(cards[0], dict) else []
    # The wise board nests its rows one level down; the pc board lists them directly.
    if content and isinstance(content[0], dict) and "content" in content[0] and "word" not in content[0]:
        content = content[0]["content"]
    rows = []
    for row in content or []:
        if not isinstance(row, dict) or row.get("isTop"):
            continue
        title = _clean(row.get("word") or row.get("query"), 40)
        if not title:
            continue
        change = str(row.get("hotChange") or "")
        rows.append({"title": title, "heat": _count(row.get("hotScore")),
                     "tag": BAIDU_TAGS.get(str(row.get("hotTag") or "")),
                     "summary": _clean(row.get("desc"), 120), "image": safe_image_url(row.get("img")),
                     "change": change if change in ("up", "down", "same") else None})
    return _rows(rows, "Baidu")


def fetch_baidu(context):
    return normalize_baidu(_get(BAIDU_URL, context, max_bytes=512 * 1024,
                                params={"platform": "pc", "tab": "realtime"}))


# --- Baidu's other boards: 电视剧 / 电影 / 小说 / 游戏 / 汽车 (分榜) ----------------------------------------

# Each board's tab, and which of its "类型：…" facts names the entry best (a drama's lead actor, a
# novel's author, a car's price), the next one when it lacks that.
BAIDU_BOARDS = {
    "teleplay": ("演员", "类型"),
    "movie": ("演员", "类型"),
    "novel": ("作者", "类型"),
    "game": ("类型",),
    "car": ("价格", "级别"),
}
BOARD_ROWS = 3


def board_facts(show):
    """Baidu's ["类型：民国、传奇", "演员：陈伟霆 、曾舜晞"] as {"类型": "民国、传奇", "演员": "陈伟霆、曾舜晞"}."""
    facts = {}
    for item in show if isinstance(show, list) else []:
        label, sep, value = str(item).replace("\xa0", " ").partition("：")
        value = re.sub(r"\s*、\s*", "、", " ".join(value.split()))
        if sep and label.strip() and value:
            facts[label.strip()[:6]] = value[:40]
    return facts


def board_detail(tab, facts):
    """The one fact the 分榜 shows for an entry: the first of its board's preferred facts, its
    first item only ("陈伟霆、曾舜晞" -> "陈伟霆", "单机游戏"), "" without one."""
    for label in BAIDU_BOARDS.get(tab, ()):
        value = facts.get(label)
        if value and label == "价格":
            # "11.99-13.79万" -> "11.99万起": the strip has room for a car's starting price.
            low = re.match(r"([\d.]+)\s*-\s*[\d.]+\s*万", value)
            return f"{low.group(1)}万起" if low else value
        if value:
            return value.split("、")[0].strip()
    return ""


def normalize_baidu_board(payload, tab):
    """A board's first BOARD_ROWS entries: title, heat, move and its facts (type, actors, price…)."""
    cards = (((payload or {}).get("data") or {}).get("cards")) or []
    content = (cards[0] or {}).get("content") if cards and isinstance(cards[0], dict) else []
    rows = []
    for row in content or []:
        if not isinstance(row, dict) or row.get("isTop"):
            continue
        title = _clean(row.get("word") or row.get("query"), 24)
        if not title:
            continue
        change = str(row.get("hotChange") or "")
        facts = board_facts(row.get("show"))
        rows.append({"title": title, "heat": _count(row.get("hotScore")), "facts": facts,
                     "detail": board_detail(tab, facts), "change": change if change in ("up", "down", "same") else None})
        if len(rows) == BOARD_ROWS:
            break
    if not rows:
        raise ValueError(f"Baidu {tab} board returned no entries")
    return rows


def fetch_baidu_boards(context):
    """The 分榜: each board's leading entries ({tab: rows}); a board that fails is left out."""
    boards = {}
    for tab in BAIDU_BOARDS:
        try:
            boards[tab] = normalize_baidu_board(_get(BAIDU_URL, context, max_bytes=256 * 1024,
                                                     params={"platform": "pc", "tab": tab}), tab)
        except TaskCancelled:
            raise
        except Exception:  # noqa: BLE001 - one board's failure leaves only that board out.
            continue
    if not boards:
        raise ValueError("Baidu returned none of its boards")
    return {"boards": boards}


def valid_boards(data):
    return bool((data or {}).get("boards"))


# --- Toutiao: 今日头条 热榜 -----------------------------------------------------------------------

def normalize_toutiao(payload):
    rows = []
    for row in (payload or {}).get("data") or []:
        if not isinstance(row, dict):
            continue
        title = _clean(row.get("Title"), 40)
        if not title:
            continue
        image = row.get("Image") if isinstance(row.get("Image"), dict) else {}
        rows.append({"title": title, "heat": _count(row.get("HotValue")),
                     "tag": TOUTIAO_TAGS.get(str(row.get("Label") or "")), "summary": "",
                     "image": safe_image_url(image.get("url"))})
    return _rows(rows, "Toutiao")


def fetch_toutiao(context):
    return normalize_toutiao(_get(TOUTIAO_URL, context, max_bytes=512 * 1024, params={"origin": "toutiao_pc"}))


# --- Zhihu: 知乎热榜 -------------------------------------------------------------------------------

def normalize_zhihu(payload):
    rows = []
    for row in (payload or {}).get("data") or []:
        if not isinstance(row, dict):
            continue
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        title = _clean(target.get("title"), 60)
        if not title:
            continue
        children = row.get("children") if isinstance(row.get("children"), list) else []
        thumb = (children[0] or {}).get("thumbnail") if children and isinstance(children[0], dict) else None
        rows.append({"title": title, "heat": _zhihu_heat(row.get("detail_text")),
                     "tag": "新" if row.get("debut") else None, "summary": _clean(target.get("excerpt"), 120),
                     "image": safe_image_url(thumb), "answers": _count(target.get("answer_count"))})
    return _rows(rows, "Zhihu")


def fetch_zhihu(context):
    return normalize_zhihu(_get(ZHIHU_URL, context, max_bytes=768 * 1024, params={"limit": MAX_ROWS}))


# --- Douyin: 抖音热榜 ------------------------------------------------------------------------------

def normalize_douyin(payload):
    """The web list (``data.word_list``, with covers) or the billboard (``word_list``, words only)."""
    data = (payload or {}).get("data") if isinstance((payload or {}).get("data"), dict) else payload or {}
    rows = []
    for row in data.get("word_list") or []:
        if not isinstance(row, dict):
            continue
        title = _clean(row.get("word"), 40)
        if not title:
            continue
        cover = row.get("word_cover") if isinstance(row.get("word_cover"), dict) else {}
        urls = cover.get("url_list") if isinstance(cover.get("url_list"), list) else []
        rows.append({"title": title, "heat": _count(row.get("hot_value")),
                     "tag": DOUYIN_TAGS.get(row.get("label")), "summary": "",
                     "image": next((safe_image_url(url) for url in urls if safe_image_url(url)), None),
                     "videos": _count(row.get("video_count")), "since": _count(row.get("event_time"))})
    return _rows(rows, "Douyin")


def fetch_douyin(context):
    try:
        # The web list answers an empty body to a request that does not come from its own site.
        return normalize_douyin(_get(DOUYIN_URL, context, max_bytes=512 * 1024, params=DOUYIN_PARAMS,
                                     headers={"Referer": "https://www.douyin.com/"}))
    except TaskCancelled:
        raise
    except Exception:  # noqa: BLE001 - any refusal falls back to the billboard's words.
        return normalize_douyin(_get(DOUYIN_BILLBOARD_URL, context, max_bytes=256 * 1024))


# --- Weibo: 微博热搜 (with the owner's login cookie) ---------------------------------------------

def cookie_header(value):
    """The Cookie header for the saved value: a whole cookie string as it is, a bare SUB value named."""
    text = " ".join(str(value or "").split())
    return text if "=" in text else (f"SUB={text}" if text else "")


def normalize_weibo(payload):
    rows = []
    for row in (((payload or {}).get("data") or {}).get("realtime")) or []:
        if not isinstance(row, dict) or row.get("is_ad"):
            continue
        title = _clean(row.get("word") or row.get("note"), 40)
        if not title:
            continue
        rows.append({"title": title, "heat": _count(row.get("num")),
                     "tag": WEIBO_TAGS.get(str(row.get("label_name") or row.get("icon_desc") or "")),
                     "summary": "", "image": None, "category": _clean(row.get("category"), 12),
                     "since": _count(row.get("onboard_time"))})
    return _rows(rows, "Weibo")


def fetch_weibo(cookie, context):
    return normalize_weibo(_get(WEIBO_URL, context, max_bytes=512 * 1024,
                                headers={"Cookie": cookie_header(cookie), "Referer": "https://weibo.com/"}))


FETCHERS = {"baidu": fetch_baidu, "toutiao": fetch_toutiao, "zhihu": fetch_zhihu, "douyin": fetch_douyin}


# --- the turn cards: 澎湃's hot list and the overseas talk ---------------------------------------

THEPAPER_URL = "https://cache.thepaper.cn/contentapi/wwwIndex/rightSidebar"
BLUESKY_TRENDS_URL = "https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrends"
MASTODON_LINKS_URL = "https://mastodon.social/api/v1/trends/links"
OVERSEAS_EACH = 6
# Bluesky's trend states and categories, in Chinese ("hot" is its hottest; cooling ones are left plain).
BLUESKY_TAGS = {"hot": "热", "trending": "升"}
BLUESKY_CATEGORIES = {"politics": "政治", "entertainment": "娱乐", "sports": "体育", "business": "商业",
                      "science-tech": "科技", "science": "科学", "tech": "科技", "culture": "文化", "news": "新闻",
                      "other": "其他", "video-games": "游戏", "pop-culture": "流行文化"}


def normalize_thepaper(payload):
    """澎湃新闻's hot list: each story with its likes, comments, column and picture."""
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    rows = []
    for item in (data or {}).get("hotNews") or [] if isinstance(data, dict) else []:
        if not isinstance(item, dict) or not _clean(item.get("name"), 80):
            continue
        node = item.get("nodeInfo") if isinstance(item.get("nodeInfo"), dict) else {}
        since = _count(item.get("pubTimeLong")) // 1000
        rows.append({"title": _clean(item.get("name"), 80), "heat": _count(item.get("praiseTimes")),
                     "comments": _count(item.get("interactionNum")), "summary": _clean(node.get("name"), 12),
                     "image": safe_image_url(item.get("pic") or item.get("smallPic")), "since": since or None,
                     "tag": None})
    return _rows(rows, "thepaper")


def fetch_thepaper(context):
    return normalize_thepaper(_get(THEPAPER_URL, context, max_bytes=512 * 1024))


def _bluesky_rows(payload):
    rows = []
    for trend in (payload or {}).get("trends") or [] if isinstance(payload, dict) else []:
        title = _clean((trend or {}).get("displayName") or (trend or {}).get("topic"), 80) if isinstance(trend, dict) else ""
        if title:
            rows.append({"title": title, "heat": _count(trend.get("postCount")), "source": "bluesky",
                         "tag": BLUESKY_TAGS.get(str(trend.get("status") or "")),
                         "summary": BLUESKY_CATEGORIES.get(str(trend.get("category") or ""), ""), "image": None})
    return rows[:OVERSEAS_EACH]


def _mastodon_rows(payload):
    rows = []
    for link in payload if isinstance(payload, list) else []:
        title = _clean((link or {}).get("title"), 100) if isinstance(link, dict) else ""
        if title:
            uses = sum(_count(day.get("uses")) for day in link.get("history") or [] if isinstance(day, dict))
            rows.append({"title": title, "heat": uses, "source": "mastodon", "tag": None,
                         "summary": _clean(link.get("provider_name"), 24), "image": safe_image_url(link.get("image"))})
    return rows[:OVERSEAS_EACH]


def normalize_overseas(bluesky, mastodon):
    """What the English-speaking social web talks about: Bluesky's trending topics (posts, state,
    category) and the links most shared on mastodon.social (shares over its days, outlet), taking
    turns."""
    sky, toots = _bluesky_rows(bluesky), _mastodon_rows(mastodon)
    rows = [row for pair in zip(sky, toots) for row in pair]
    rows += sky[len(toots):] + toots[len(sky):]
    return _rows(rows, "overseas")


def fetch_overseas(context):
    readings = []
    for url, params in ((BLUESKY_TRENDS_URL, {"limit": OVERSEAS_EACH}), (MASTODON_LINKS_URL, {"limit": OVERSEAS_EACH})):
        try:
            readings.append(_get(url, context, max_bytes=256 * 1024, params=params))
        except (HttpClientError, ValueError) as error:
            # One network's trends alone still fill the card.
            logger.info("Overseas trends unavailable from %s: %s", url.split("/")[2], type(error).__name__)
            readings.append(None)
    return normalize_overseas(*readings)


EXTRA_FETCHERS = {"thepaper": fetch_thepaper, "overseas": fetch_overseas}


def valid_rows(data):
    return bool((data or {}).get("rows"))


# --- the same story on several lists ---------------------------------------------------------------

def _bigrams(title):
    text = _PUNCTUATION.sub("", str(title or "")).lower()
    return {text[index:index + 2] for index in range(len(text) - 1)}


def same_topic(first, second):
    """Two titles name the same story when they share most of the shorter one's two-character runs."""
    a, b = _bigrams(first), _bigrams(second)
    shared = len(a & b)
    return shared >= SAME_TOPIC_MIN_BIGRAMS and shared >= SAME_TOPIC_SHARE * min(len(a), len(b))


def cross_topics(lists, depth=MAX_ROWS):
    """Stories that are on more than one platform's list, most widely and highly placed first.

    ``lists`` maps a platform to its rows. Each topic is {"title", "ranks": {platform: rank},
    "rows": {platform: row}}, its title the highest-placed one."""
    topics = []
    for platform in ORDER:
        for row in (lists.get(platform) or [])[:depth]:
            home = next((topic for topic in topics if platform not in topic["ranks"]
                         and any(same_topic(row["title"], other["title"]) for other in topic["rows"].values())), None)
            if home is None:
                topics.append({"ranks": {platform: row["rank"]}, "rows": {platform: row}})
            else:
                home["ranks"][platform] = row["rank"]
                home["rows"][platform] = row
    shared = [topic for topic in topics if len(topic["ranks"]) > 1]
    for topic in shared:
        best = min(topic["ranks"], key=topic["ranks"].get)
        topic["title"] = topic["rows"][best]["title"]
        topic["score"] = sum(1 / rank for rank in topic["ranks"].values()) + len(topic["ranks"])
    return sorted(shared, key=lambda topic: topic["score"], reverse=True)
