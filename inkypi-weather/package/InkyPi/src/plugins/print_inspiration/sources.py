"""MakerWorld's public lists for 打印灵感 (Bambu Lab's model site; its search API needs no key): the
trending models of the moment (its hot ranking, newest designs rising), the most printed of them, and
the all-time classics (most liked); the pages of the lead and the four cards add their summary, category,
pictures and default print profile (time, filament, layer height, AMS colours) and the maker's level and
followers. Pictures come resized by MakerWorld's own image service (a cover's original is a PNG of
several megabytes).
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from utils.http_client import get_http_client

SEARCH_URL = "https://makerworld.com/api/v1/search-service/select/design2"
DESIGN_URL = "https://makerworld.com/api/v1/design-service/design/{id}"
USER_AGENT = "Mozilla/5.0 (compatible; InkyPi PrintInspiration/1.0)"
REQUEST_TIMEOUT = 15
LIST_MAX_BYTES = 768 * 1024
DESIGN_MAX_BYTES = 1024 * 1024
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
IMAGE_HOST_SUFFIX = ".bblmw.com"
# The trending pool (MakerWorld's hot ranking): its first models lead the page and the most printed
# of the pool fill the right column; the classics are the all-time most liked.
POOL = ("hotScore", 30)
CLASSICS = ("likeCount", 30)
# The page shows CLASSICS_SHOWN of them at a time, the next ones every CLASSICS_TURN_SECONDS.
CLASSICS_SHOWN = 3
CLASSICS_TURN_SECONDS = 3 * 3600
WEEK = 7
# The lead and the four cards under it read their own design pages (print profile, rating).
DETAILED = 5
MAKES = 5
# The pictures after the cover the lead shows beside it.
GALLERY = 3
TAGS = 6
PICTURE_WIDTH, SMALL_WIDTH, AVATAR_WIDTH = 480, 200, 64
# MakerWorld's category tree (api/v1/design-service/design/category) in Chinese by id: a top
# category's id is a multiple of 100 and its sub-categories number after it ("Decor" 401 is under
# "Household" 400). A category added later keeps its English name.
CATEGORIES = {
    900: "3D 打印机", 901: "打印机配件", 902: "打印机零件", 903: "测试模型",
    100: "艺术", 101: "平面艺术", 102: "标牌标志", 103: "硬币徽章", 104: "雕塑",
    500: "教育", 501: "工程", 502: "数学", 503: "生物", 504: "物理天文", 505: "化学", 506: "地理",
    200: "时尚", 201: "包袋", 202: "服装", 203: "眼镜", 204: "鞋履", 205: "戒指", 206: "耳饰", 208: "首饰",
    300: "爱好 DIY", 301: "电子", 302: "载具", 303: "音乐", 304: "遥控模型", 305: "机器人", 306: "运动户外",
    400: "家居", 401: "装饰", 402: "园艺", 403: "节庆", 404: "办公", 405: "宠物",
    600: "微缩模型", 601: "动物", 602: "建筑", 603: "奇幻生物", 604: "人物",
    1000: "道具 Cosplay", 1001: "面具头盔", 1002: "道具武器", 1003: "服饰",
    700: "工具", 701: "收纳", 702: "测量工具", 703: "手动工具", 704: "机床工具", 705: "小工具", 707: "医疗工具",
    800: "玩具游戏", 801: "角色", 802: "桌游", 803: "户外玩具", 804: "益智拼图", 806: "拼装套件",
    2000: "生成模型", 2001: "透光浮雕", 2002: "定制标牌", 2003: "定制花瓶", 2004: "像素拼图", 2005: "浮雕生成",
    2006: "AI 扫描", 2007: "图片钥匙扣", 2008: "定制收纳", 2009: "PrintMon", 2010: "雕像生成",
    2011: "圣诞挂饰", 2012: "定制灯笼",
}


def local_now():
    return datetime.now(LOCAL_TZ)


def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def resized(url, width):
    """A MakerWorld picture ``width`` px wide from its image service; None off MakerWorld's hosts."""
    url = safe_image_url(str(url or "").split("?")[0])
    return f"{url}?x-oss-process=image/resize,w_{int(width)}" if url else None


def safe_image_url(url):
    """Only MakerWorld's own picture hosts (*.bblmw.com) serve the page's pictures."""
    parts = urlsplit(str(url or ""))
    host = parts.hostname or ""
    return url if parts.scheme == "https" and host.endswith(IMAGE_HOST_SUFFIX) else None


def category_name(categories):
    """The model's category in Chinese, its top category first ("Decor, Household" -> "家居 · 装饰");
    an "Other ..." sub-category adds nothing to its parent and is left out."""
    named = []
    for item in categories or ():
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name, category_id = _text(item.get("name"), 40), _int(item.get("id"))
        if not name.lower().startswith("other "):
            named.append((category_id % 100 != 0, category_id, CATEGORIES.get(category_id, name)))
    return " · ".join(dict.fromkeys(name for *_, name in sorted(named)[:2]))


def license_label(name):
    """A licence's short name: "CC BY-NC-SA" (MakerWorld writes it "BY-NC-SA"), "CC0", "标准授权"
    (its standard file licence), "独家授权" (a MakerWorld exclusive)."""
    text = " ".join(str(name or "").split())
    lowered = text.lower()
    if not text:
        return ""
    if "standard digital file" in lowered:
        return "标准授权"
    if "exclusive" in lowered:
        return "独家授权"
    if "public domain" in lowered or "cc0" in lowered:
        return "CC0"
    if "gnu" in lowered or "gpl" in lowered:
        return "GPL"
    match = re.match(r"(?i)^(?:cc[\s-]*)?(by(?:[\s-]+(?:nc|nd|sa))*)\b", text)
    if match:
        return "CC " + "-".join(part.upper() for part in re.split(r"[\s-]+", match.group(1)))
    return text[:16]


def summary_text(value, limit=300):
    """A design's summary as plain text: its "Boost me" block and markup dropped."""
    text = re.sub(r"(?is)<boostme>.*?</boostme>", " ", str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>|</p>", " ", text)
    return _text(html.unescape(re.sub(r"<[^>]+>", " ", text)), limit)


def print_settings(instance):
    """The default print profile's settings: {material, colors, minutes, weight, layer, ams}, only
    those it gives. MakerWorld names a profile like "0.2mm layer, 2 walls, 15% infill" and gives its
    print time in seconds."""
    if not isinstance(instance, dict):
        return {}
    filaments = [item for item in instance.get("instanceFilaments") or [] if isinstance(item, dict)]
    types = []
    for item in filaments:
        kind = _text(item.get("type"), 12)
        if kind and kind not in types:
            types.append(kind)
    settings = {"material": "/".join(types[:2]), "colors": len(filaments) if len(filaments) > 1 else 0,
                "minutes": round(_int(instance.get("prediction")) / 60), "weight": _int(instance.get("weight"))}
    layer = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*layer", str(instance.get("title") or ""), re.IGNORECASE)
    if layer:
        settings["layer"] = layer.group(1)
    if instance.get("needAms"):
        settings["ams"] = True
    return {key: value for key, value in settings.items() if value}


def normalize_hit(raw):
    """One listed design, or None without an id, a title and a cover."""
    if not isinstance(raw, dict):
        return None
    model_id, name = str(raw.get("id") or "").strip(), _text(raw.get("title"), 120)
    picture, small = resized(raw.get("cover"), PICTURE_WIDTH), resized(raw.get("cover"), SMALL_WIDTH)
    if not model_id.isdigit() or not name or not picture:
        return None
    published = None
    try:
        published = datetime.fromisoformat(str(raw.get("createTime")).replace("Z", "+00:00")).astimezone(
            timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    creator = raw.get("designCreator") if isinstance(raw.get("designCreator"), dict) else {}
    tags = [_text(tag, 24) for tag in raw.get("tags") or [] if isinstance(tag, str) and tag.strip()]
    return {
        "id": model_id, "name": name, "summary": "",
        "likes": _int(raw.get("likeCount")), "downloads": _int(raw.get("downloadCount")),
        "makes": _int(raw.get("printCount")), "comments": _int(raw.get("commentCount")),
        "collections": _int(raw.get("collectionCount")), "views": _int(raw.get("readCount")),
        "rating": 0.0, "ratings": 0, "category": "",
        "maker": _text(creator.get("name"), 40), "avatar": resized(creator.get("avatar"), AVATAR_WIDTH),
        "image": picture, "image_small": small, "pictures": [picture], "pictures_small": [small],
        "gcode": False, "published": published, "print": {}, "files": 0,
        "license": license_label(raw.get("license")), "tags": tags[:TAGS], "remixes": 0,
        "multi_material": False, "maker_level": 0, "maker_followers": 0, "gallery": [],
        "staff_pick": bool(raw.get("isStaffPicked")),
    }


def design_details(payload):
    """What the lead adds from its design page: summary, category, pictures, print profile, rating,
    print profiles count, multi-colour and its maker's level and followers ({} for an empty page)."""
    if not isinstance(payload, dict) or not payload.get("id"):
        return {}
    instances = [item for item in payload.get("instances") or [] if isinstance(item, dict)]
    default = next((item for item in instances if item.get("id") == payload.get("defaultInstanceId")),
                   instances[0] if instances else {})
    pictures = ((payload.get("designExtension") or {}).get("design_pictures") or []) if isinstance(
        payload.get("designExtension"), dict) else []
    cover = str(payload.get("coverUrl") or "").split("?")[0]
    gallery = [resized(item.get("url"), SMALL_WIDTH) for item in pictures
               if isinstance(item, dict) and str(item.get("url") or "").split("?")[0] != cover]
    ratings = sum(_int(item.get("ratingCount")) for item in instances)
    total = sum(_int(item.get("ratingScoreTotal")) for item in instances)
    creator = payload.get("designCreator") if isinstance(payload.get("designCreator"), dict) else {}
    settings = print_settings(default)
    return {
        "summary": summary_text(payload.get("summary")), "category": category_name(payload.get("categories")),
        "gallery": [url for url in gallery if url][:GALLERY], "print": settings, "files": len(instances),
        "rating": round(total / ratings, 1) if ratings else 0.0, "ratings": ratings,
        "multi_material": bool(settings.get("ams") or settings.get("colors")),
        "maker_level": _int(creator.get("level")), "maker_followers": _int(creator.get("fanCount")),
    }


def _search(order, limit, context):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", SEARCH_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=LIST_MAX_BYTES,
        params={"keyword": "", "orderBy": order, "limit": limit, "offset": 0},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    payload = response.data if isinstance(response.data, dict) else {}
    models, seen = [], set()
    for raw in payload.get("hits") or []:
        model = normalize_hit(raw)
        if model is not None and model["id"] not in seen:
            seen.add(model["id"])
            models.append(model)
    return {"models": models}


def fetch_pool(context):
    """MakerWorld's trending designs (its hot ranking) as {"models": [...]}."""
    return _search(*POOL, context)


def fetch_classics(context):
    """The all-time most liked designs as {"models": [...]}."""
    return _search(*CLASSICS, context)


def fetch_details(model_id, context):
    """A shown design's page as {"details": {...}}."""
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", DESIGN_URL.format(id=int(model_id)), context=context, timeout=REQUEST_TIMEOUT,
        max_bytes=DESIGN_MAX_BYTES, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    return {"details": design_details(response.data)}


def lists(pool, classics, details=None):
    """The page's lists from the trending pool: "week" (its first WEEK, each with its page's details
    when ``details`` ({id: details}) has them: the lead and the cards), "makes" (the most printed of
    the rest of the pool) and "classics"."""
    week = [dict(model) for model in pool[:WEEK]]
    for model in week:
        found = (details or {}).get(model["id"]) or {}
        model.update({key: value for key, value in found.items() if value not in ("", None, [], {}, 0, 0.0)})
    shown = {model["id"] for model in week[:DETAILED]}
    makes = sorted((model for model in pool if model["id"] not in shown), key=lambda model: model["makes"], reverse=True)
    return {"week": week, "makes": makes[:MAKES], "classics": list(classics)}


def classics_turn(classics, now):
    """The classics on show at ``now``: CLASSICS_SHOWN in a row of the all-time list, each with its
    place in it ("rank") and the list's length ("of"), moving on every CLASSICS_TURN_SECONDS and
    starting again after the last full turn."""
    ranked = [{**model, "rank": index + 1, "of": len(classics)} for index, model in enumerate(classics)]
    turns = max(1, len(ranked) // CLASSICS_SHOWN)
    first = (int(now.timestamp() // CLASSICS_TURN_SECONDS) % turns) * CLASSICS_SHOWN
    return ranked[first:first + CLASSICS_SHOWN]


def valid_list(data):
    return isinstance(data, dict) and isinstance(data.get("models"), list) and bool(data["models"])


def valid_details(data):
    return isinstance(data, dict) and isinstance(data.get("details"), dict) and bool(data["details"])
