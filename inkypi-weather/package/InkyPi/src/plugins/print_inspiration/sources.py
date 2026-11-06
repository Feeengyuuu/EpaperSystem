"""Printables' public GraphQL lists for 打印灵感 (no key): the week's most liked models, the week's
most printed (most "makes") and the all-time classics, each model with its picture, maker,
category, counts and rating.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from utils.http_client import get_http_client

GRAPHQL_URL = "https://api.printables.com/graphql/"
MEDIA_URL = "https://media.printables.com/"
USER_AGENT = "Mozilla/5.0 (compatible; InkyPi PrintInspiration/1.0)"
REQUEST_TIMEOUT = 15
LIST_MAX_BYTES = 512 * 1024
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
# list key -> (Printables ordering, how many)
LISTS = {
    "week": ("-likes_count_7_days", 7),
    "makes": ("-makes_count_7_days", 5),
    "classics": ("-likes_count", 3),
}
FIELDS = ("id name slug likesCount downloadCount makesCount ratingAvg ratingCount commentCount displayCount "
          "datePublished summary category { path { name } } user { publicUsername avatarFilePath } "
          "image { filePath } images { filePath } gcodes { id }")
# A model's pictures tried in order (its cover, then its next ones): an old upload may have no thumbnail.
PICTURE_CANDIDATES = 3
QUERY = 'query { morePrints(ordering: "%s", limit: %d) { items { %s } } }'
# Printables' categories in Chinese (top level and the common sub-categories); others keep their name.
CATEGORIES = {
    "3D Printers": "3D 打印机", "Art & Design": "艺术设计", "Costumes & Accessories": "服饰配件",
    "Fashion": "时尚", "Gadgets": "小玩意", "Healthcare": "医疗健康", "Hobby & Makers": "爱好创客",
    "Household": "家居", "Learning": "学习", "Outdoor & Garden": "户外园艺", "Props": "道具",
    "Sports & Outdoor": "运动户外", "Tools": "工具", "Toys & Games": "玩具游戏", "World & Scans": "世界扫描",
    "Organizers": "收纳", "Decorations": "装饰", "Kitchen": "厨房", "Bathroom": "浴室", "Office": "办公",
    "Electronics": "电子", "Cosplay": "Cosplay", "Figurines": "手办", "Board Games": "桌游", "Puzzles": "益智",
    "Hand Tools": "手工具", "Power Tools": "电动工具", "Workshop": "工作间", "Garden": "花园", "Pets": "宠物",
    "Upgrades": "升级件", "Spare Parts": "备件", "Sculptures": "雕塑", "Wall Art": "墙饰", "Masks": "面具",
    "Jewelry": "饰品", "Vehicles": "载具", "Outdoor": "户外", "Gaming": "游戏", "Photography": "摄影",
    "Music": "音乐", "Education": "教育", "Mechanical": "机械", "Seasonal designs": "节日",
    "Halloween": "万圣节", "Christmas": "圣诞", "Phone & Tablet": "手机平板", "Computers": "电脑",
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


def media_url(path, size=None):
    """A Printables media file's URL; a picture's ``size`` thumbnail when given ("640x480" or
    "320x240", the sizes Printables serves; its original can run to several megabytes). A
    thumbnail keeps its original's format (a PNG's is a PNG)."""
    path = str(path or "").strip().lstrip("/")
    if not path or ".." in path or not re.fullmatch(r"media/[\w./ -]+\.(?:jpe?g|png|webp)", path, re.IGNORECASE):
        return None
    if size:
        head, _sep, name = path.rpartition("/")
        extension = name.rsplit(".", 1)[1].lower()
        path = f"{head}/thumbs/inside/{size}/{'jpg' if extension in ('jpg', 'jpeg') else extension}/{name}"
    return MEDIA_URL + quote(path)


def safe_image_url(url):
    """Only Printables' own media host serves the page's pictures."""
    parts = urlsplit(str(url or ""))
    return url if parts.scheme == "https" and parts.hostname == "media.printables.com" else None


def category_name(path):
    """The model's category in Chinese where known ("Hobby & Makers / Organizers" -> "爱好创客 · 收纳")."""
    names = [_text(item.get("name"), 40) for item in path or () if isinstance(item, dict) and item.get("name")]
    return " · ".join(CATEGORIES.get(name, name) for name in names[-2:])


def normalize_model(raw):
    """One listed model, or None without an id, a name and a picture."""
    if not isinstance(raw, dict):
        return None
    model_id, name = str(raw.get("id") or "").strip(), _text(raw.get("name"), 120)
    paths = []
    for item in [raw.get("image"), *(raw.get("images") or [])[:PICTURE_CANDIDATES]]:
        path = (item or {}).get("filePath") if isinstance(item, dict) else None
        if path and path not in paths:
            paths.append(path)
    pictures = [url for url in (media_url(path, "640x480") for path in paths[:PICTURE_CANDIDATES]) if url]
    small = [url for url in (media_url(path, "320x240") for path in paths[:PICTURE_CANDIDATES]) if url]
    picture = pictures[0] if pictures else None
    if not model_id.isdigit() or not name or not picture:
        return None
    try:
        rating = round(float(raw.get("ratingAvg") or 0), 1)
    except (TypeError, ValueError):
        rating = 0.0
    published = None
    try:
        published = datetime.fromisoformat(str(raw.get("datePublished"))).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    user = raw.get("user") or {}
    return {
        "id": model_id, "name": name, "summary": _text(raw.get("summary"), 300),
        "likes": _int(raw.get("likesCount")), "downloads": _int(raw.get("downloadCount")),
        "makes": _int(raw.get("makesCount")), "comments": _int(raw.get("commentCount")),
        "views": _int(raw.get("displayCount")), "rating": rating, "ratings": _int(raw.get("ratingCount")),
        "category": category_name((raw.get("category") or {}).get("path")),
        "maker": _text(user.get("publicUsername"), 40), "avatar": media_url(user.get("avatarFilePath")),
        "image": picture, "image_small": small[0] if small else None, "pictures": pictures, "pictures_small": small,
        "gcode": bool(raw.get("gcodes")),
        "published": published,
    }


def fetch_list(key, context):
    """The ``key`` list (LISTS) as {"models": [...]}."""
    ordering, limit = LISTS[key]
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "POST", GRAPHQL_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=LIST_MAX_BYTES,
        json={"query": QUERY % (ordering, limit, FIELDS)},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"})
    payload = response.data if isinstance(response.data, dict) else {}
    if payload.get("errors"):
        raise ValueError("Printables refused the list query")
    items = ((payload.get("data") or {}).get("morePrints") or {}).get("items") or []
    models = [model for model in (normalize_model(raw) for raw in items) if model is not None]
    return {"models": models}


def valid_list(data):
    return isinstance(data, dict) and isinstance(data.get("models"), list) and bool(data["models"])
