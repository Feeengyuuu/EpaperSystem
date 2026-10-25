"""What is known about a poster, gathered from three places.

- The poster's own page on chineseposters.net (a Drupal site whose fields carry stable
  ``field--name-field-*`` classes): its English and Chinese titles, pinyin, designers, date,
  publisher, size, call number, collection, notes, themes and tags.
- The designer's page there, for the years of their life and where they were born.
- Chinese Wikipedia, for a short simplified-Chinese account of the poster's theme: a fixed
  article for the themes the plugin samples, else the English article of the theme's name and
  its Chinese counterpart.

Everything is bounded before it is stored with the poster in the presentation bank.
"""

from __future__ import annotations

import base64
import binascii
from html import unescape
from io import BytesIO
import re
from urllib.parse import quote, urljoin, urlparse

from PIL import Image

from utils.safe_image import ImageLimits, safe_open_image

BASE_URL = "https://chineseposters.net"
WIKI_HEADERS = {
    "User-Agent": "InkyPi BacktotheDate/1.0 (+https://github.com/Feeengyuuu/EpaperSystem)",
    "Accept-Language": "zh-cn",
}
ZH_SUMMARY_URL = "https://zh.wikipedia.org/api/rest_v1/page/summary/{}"
EN_API_URL = "https://en.wikipedia.org/w/api.php"
# chineseposters.net theme slug -> its Chinese Wikipedia article.
THEME_ARTICLES = {
    "great-leap-forward": "大跃进",
    "cultural-revolution-campaigns": "文化大革命",
    "cultural-revolution": "文化大革命",
    "monsters-demons": "牛鬼蛇神",
    "revolutionary-networking": "大串联",
    "shanghai-commune": "上海人民公社",
    "revolutionary-committees": "革命委员会",
    "red-sea-movement": "红海洋",
    "may-seven-cadre-schools": "五七干校",
    "up-to-the-mountains": "上山下乡运动",
    "pla-cultural-revolution": "三支两军",
    "mao-cult": "毛泽东崇拜",
    "pla": "中国人民解放军",
    "lei-feng": "雷锋",
    "peoples-communes": "人民公社",
    "red-guards": "红卫兵",
    "four-modernizations": "四个现代化",
    "family-planning": "计划生育",
    "korean-war": "抗美援朝",
}
# Field class (after ``field--name-``) -> the key it is kept under.
TEXT_FIELDS = {
    "field-titlechinese": "title_zh",
    "field-titlepinyin": "pinyin",
    "field-datetxt": "date",
    "field-publisher": "publisher",
    "field-size": "size",
    "field-call-nr-": "call_nr",
    "field-collection": "collection",
    "body": "notes",
}
LIMITS = {"title_en": 200, "title_zh": 80, "pinyin": 160, "date": 40, "publisher": 120, "size": 40, "call_nr": 40,
          "collection": 80, "notes": 400, "theme_zh": 40, "theme_summary": 220, "designer_years": 20,
          "designer_born": 80}
# The theme's Wikipedia photo, kept with the poster as a small JPEG (base64).
PHOTO_SIZE = (240, 170)
PHOTO_MAX_BYTES = 12_000
PHOTO_MAX_CHARS = 16_100
PHOTO_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
PHOTO_LIMITS = ImageLimits(max_bytes=4 * 1024 * 1024)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
MAX_DESIGNERS = 3
MAX_THEMES = 3
MAX_TAGS = 12
_FIELD_RE = re.compile(r'<div[^>]*class="[^"]*\bfield--name-([a-z0-9-]+)(?=[\s"])[^"]*"[^>]*>(.*?)(?=<div[^>]*class="[^"]*\bfield--name-|</article>|<footer|$)',
                       re.S | re.I)
_LINK_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_LABEL_RE = re.compile(r"<label[^>]*>.*?</label>", re.S | re.I)
_TAG_RE = re.compile(r'<a[^>]*href="/tags/[^"]+"[^>]*>(.*?)</a>', re.S | re.I)
_CJK_RE = re.compile(r"[㐀-鿿]")


def clean(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _fields(html_text):
    fields = {}
    for name, body in _FIELD_RE.findall(html_text or ""):
        fields.setdefault(name.lower(), _LABEL_RE.sub(" ", body))
    return fields


def split_chinese(text):
    """'Chen Xinghua (陈兴华)' -> ('Chen Xinghua', '陈兴华'); the Chinese part only when it is Chinese."""
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*(.*)$", text or "")
    if match and _CJK_RE.search(match.group(2)):
        rest = match.group(3).strip(" ,")
        return match.group(1).strip(), match.group(2).strip(), rest
    return (text or "").strip(), "", ""


def parse_poster_page(html_text, title_en=""):
    """The poster's fields from its page; missing ones are left out."""
    fields = _fields(html_text)
    meta = {}
    if title_en:
        meta["title_en"] = title_en
    for field, key in TEXT_FIELDS.items():
        value = clean(fields.get(field, ""))
        if value:
            meta[key] = value
    designers = []
    for href, label in _LINK_RE.findall(fields.get("field-taxonomy-artists", "")):
        name, chinese, _rest = split_chinese(clean(label))
        if name or chinese:
            designers.append({"name": name, "zh": chinese, "url": urljoin(BASE_URL, href)})
    if designers:
        meta["designers"] = designers[:MAX_DESIGNERS]
    themes = []
    for href, label in _LINK_RE.findall(fields.get("field-see-also-these-pages-", "")):
        if urlparse(urljoin(BASE_URL, href)).path.startswith("/themes/"):
            themes.append({"name": clean(label), "slug": urlparse(href).path.rstrip("/").rsplit("/", 1)[-1]})
    if themes:
        meta["themes"] = themes[:MAX_THEMES]
    # The page's tag list (links into /tags/), Chinese and English mixed.
    tags = [clean(label) for label in _TAG_RE.findall(html_text or "")]
    tags = [tag for tag in dict.fromkeys(tags) if tag and tag.lower() not in {"china", "poster", "chinese", "propaganda"}]
    if tags:
        meta["tags"] = tags[:MAX_TAGS]
    return meta


def designer_facts(html_text):
    """(years, born) from a designer's page: '1924-1997' and 'Qingxu, Shanxi Province'."""
    body = clean(_fields(html_text).get("field-body", ""))
    years = ""
    match = re.search(r"\((\d{4})\s*[-–]\s*(\d{4})?\)", body[:300])
    if match:
        years = f"{match.group(1)}–{match.group(2) or ''}"
    born = ""
    match = re.search(r"\bborn in ([^.;]+?)(?:\s+in \d{4})?[.;]", body[:500])
    if match:
        born = match.group(1).strip()
    return years, born


def theme_article(theme, fetch_json):
    """The Chinese Wikipedia article for a theme: the fixed one for known slugs, else the
    English article of its name's Chinese counterpart; None when there is none."""
    article = THEME_ARTICLES.get(str(theme.get("slug") or ""))
    if article:
        return article
    name = str(theme.get("name") or "").strip()
    if not name:
        return None
    payload = fetch_json(EN_API_URL, {"action": "query", "titles": name, "redirects": 1, "prop": "langlinks",
                                      "lllang": "zh", "format": "json"})
    for page in ((payload or {}).get("query") or {}).get("pages", {}).values():
        for link in page.get("langlinks") or []:
            if link.get("*"):
                return link["*"]
    return None


def theme_summary(article, fetch_json):
    """(simplified title, its first one or two sentences, its lead image's URL or "") from
    Chinese Wikipedia."""
    payload = fetch_json(ZH_SUMMARY_URL.format(quote(article, safe="")), None) or {}
    photo = str(((payload.get("thumbnail") or {}).get("source")) or "")
    photo = photo if urlparse(photo).scheme == "https" and urlparse(photo).hostname in PHOTO_HOSTS else ""
    title = clean(payload.get("displaytitle") or "") or str(payload.get("title") or article)
    extract = clean(payload.get("extract") or "")
    sentences = re.findall(r"[^。！？]+[。！？]?", extract)
    summary = ""
    for sentence in sentences:
        if summary and len(summary) + len(sentence) > LIMITS["theme_summary"]:
            break
        summary += sentence
    return title, summary, photo


def encode_photo(data):
    """A downloaded photo as a small base64 JPEG for the poster's record, or "" when it will not
    fit the budget."""
    with safe_open_image(data, limits=PHOTO_LIMITS) as source:
        image = source.convert("RGB")
    image.thumbnail(PHOTO_SIZE, Image.Resampling.LANCZOS)
    for quality in (72, 60, 48, 38):
        output = BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        if output.tell() <= PHOTO_MAX_BYTES:
            return base64.b64encode(output.getvalue()).decode("ascii")
    return ""


def decode_photo(value):
    """The stored photo as an image, or None."""
    if not isinstance(value, str) or not value or len(value) > PHOTO_MAX_CHARS or not _BASE64_RE.match(value):
        return None
    try:
        with safe_open_image(base64.b64decode(value), limits=PHOTO_LIMITS) as source:
            return source.convert("RGB")
    except (binascii.Error, OSError, ValueError):
        return None


def bounded(meta):
    """Meta as stored with a poster: known keys, strings cut to their limits, short lists."""
    if not isinstance(meta, dict):
        return {}
    kept = {}
    for key, limit in LIMITS.items():
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            kept[key] = value.strip()[:limit]
    photo = meta.get("theme_photo")
    if isinstance(photo, str) and len(photo) <= PHOTO_MAX_CHARS and _BASE64_RE.match(photo):
        kept["theme_photo"] = photo
    designers = []
    for item in meta.get("designers") or []:
        if isinstance(item, dict):
            designers.append({field: str(item.get(field) or "")[:120] for field in ("name", "zh", "url")})
    if designers:
        kept["designers"] = designers[:MAX_DESIGNERS]
    themes = []
    for item in meta.get("themes") or []:
        if isinstance(item, dict):
            themes.append({field: str(item.get(field) or "")[:80] for field in ("name", "slug")})
    if themes:
        kept["themes"] = themes[:MAX_THEMES]
    tags = [str(tag)[:40] for tag in meta.get("tags") or [] if isinstance(tag, str) and tag.strip()]
    if tags:
        kept["tags"] = tags[:MAX_TAGS]
    return kept
