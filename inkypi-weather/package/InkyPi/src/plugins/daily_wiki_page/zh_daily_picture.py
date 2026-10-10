"""Chinese captions for the Daily Wiki photograph.

Chinese Wikipedia publishes its own daily picture page (``Wikipedia:每日图片``)
that normally reuses the Commons picture of the day with a hand-written Chinese
caption. These helpers read that page's wikitext and pick a Chinese caption from
the REST feed when the page cannot be used, so a Chinese page never shows the
English Commons description.
"""
from __future__ import annotations

import re

ZH_DAILY_PICTURE_PAGE = "Wikipedia:每日图片/{year}年{month}月{day}日"
GENERIC_ZH_IMAGE_CAPTION = "维基共享资源每日图片"
ZH_CAPTION_LANGUAGES = (
    "zh-cn", "zh-hans", "zh-sg", "zh-my", "zh",
    "zh-hant", "zh-tw", "zh-hk", "zh-mo",
)
_FILE_PREFIX = re.compile(r"^\s*(?:file|image|文件|檔案|图像|圖像)\s*:", re.IGNORECASE)
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_CONVERT_UNITS = {"m": "米", "km": "千米", "ft": "英尺", "mi": "英里", "cm": "厘米"}


def daily_picture_page_title(day):
    return ZH_DAILY_PICTURE_PAGE.format(year=day.year, month=day.month, day=day.day)


def contains_cjk(text):
    return bool(_CJK.search(str(text or "")))


def file_key(name):
    """Comparable Commons file name: no namespace, spaces for underscores, casefolded."""
    name = _FILE_PREFIX.sub("", str(name or ""))
    return re.sub(r"\s+", " ", name.replace("_", " ")).strip().casefold()


def template_params(wikitext):
    """Named parameters of the first template in ``wikitext``.

    Pipes inside nested templates and links belong to them, so
    ``[[A|B]]`` and ``{{lang|en|X}}`` stay inside a parameter value.
    """
    text = re.sub(r"<!--.*?-->", "", str(wikitext or ""), flags=re.DOTALL)
    start = text.find("{{")
    if start < 0:
        return {}
    parts, current = [], []
    curly = square = 0
    index = start + 2
    while index < len(text):
        pair = text[index:index + 2]
        if pair in ("{{", "[["):
            if pair == "{{":
                curly += 1
            else:
                square += 1
            current.append(pair)
            index += 2
            continue
        if pair == "}}" and curly == 0 and square == 0:
            parts.append("".join(current))
            break
        if pair in ("}}", "]]"):
            if pair == "}}":
                curly = max(0, curly - 1)
            else:
                square = max(0, square - 1)
            current.append(pair)
            index += 2
            continue
        if text[index] == "|" and curly == 0 and square == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(text[index])
        index += 1
    else:
        return {}
    params = {}
    for part in parts[1:]:
        key, separator, value = part.partition("=")
        if separator:
            params[key.strip().casefold()] = value.strip()
    return params


def daily_picture_fields(wikitext):
    """``(file name, caption wikitext)`` from a ``Wikipedia:每日图片`` page, or empty strings."""
    params = template_params(wikitext)
    image = params.get("image", "").strip()
    content = params.get("content", "").strip()
    if not image or not content:
        return "", ""
    return _FILE_PREFIX.sub("", image).strip(), content


def without_references(wikitext):
    """Caption wikitext without comments or footnotes (the parser would append a reference list)."""
    text = re.sub(r"<!--.*?-->", "", str(wikitext or ""), flags=re.DOTALL)
    return re.sub(r"<ref\b[^>]*/>|<ref\b[^>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def strip_caption_markup(wikitext):
    """Plain text from caption wikitext, used when the wiki parser is unreachable."""
    text = without_references(wikitext)
    text = re.sub(r"-\{(.*?)\}-", lambda match: _variant_text(match.group(1)), text, flags=re.DOTALL)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\{\{([^{}]*)\}\}", lambda match: _template_text(match.group(1)), text)
    text = re.sub(r"\[\[(?:[^\[\]|]*\|)?([^\[\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[(?:https?:)?//\S+\s+([^\]]*)\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _variant_text(body):
    variants = {}
    for chunk in body.split(";"):
        code, separator, value = chunk.partition(":")
        if separator and re.fullmatch(r"\s*zh(?:-[a-z]+)?\s*", code, re.IGNORECASE):
            variants[code.strip().lower()] = value.strip()
    if not variants:
        return body.strip()
    for code in ZH_CAPTION_LANGUAGES:
        if variants.get(code):
            return variants[code]
    return next(iter(variants.values()))


def _template_text(body):
    params = [part.strip() for part in body.split("|")]
    name = params[0].casefold() if params else ""
    positional = [part for part in params[1:] if "=" not in part]
    if (name == "lang" or name.startswith("lang-")) and positional:
        return positional[-1]
    if name == "convert" and positional:
        unit = positional[1] if len(positional) > 1 else ""
        return positional[0] + _CONVERT_UNITS.get(unit, unit)
    if name in {"le", "link-en", "nowrap", "nobr"} and positional:
        return positional[0]
    if name == "tsl" and len(positional) >= 3:
        return positional[2]
    return ""


def feed_image_chinese_caption(image):
    """A Chinese caption the REST feed already carries for its picture, or ``""``."""
    if not isinstance(image, dict):
        return ""
    description = image.get("description")
    if isinstance(description, dict):
        text = description.get("text") or description.get("html") or ""
        language = str(description.get("lang") or "").lower()
    else:
        text, language = description or "", ""
    if text and (language.startswith("zh") or (not language and contains_cjk(text))):
        return str(text)
    structured = image.get("structured") if isinstance(image.get("structured"), dict) else {}
    captions = structured.get("captions") if isinstance(structured.get("captions"), dict) else {}
    for code in ZH_CAPTION_LANGUAGES:
        caption = captions.get(code)
        if isinstance(caption, str) and contains_cjk(caption):
            return caption
    return ""
