"""The information card under each cover of the triptych.

Each card names the publication beside its category's generated icon, then the issue
(its month or year, with how long ago that was, or a "最新" tag for a current issue)
and the category with the collection the scan came from. The covers keep the rest of
their column; both the serial and the staged composition use the same boxes.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from plugins.base_plugin.cover_gallery import Label
from utils.app_utils import get_base_ui_font
from utils.safe_image import ImageLimits, safe_open_image

ASSETS = Path(__file__).with_name("assets")
INFO_HEIGHT = 84
# Clear of the 8 px media inset the theme chrome frames.
PAD = 12
CARD_RADIUS = 10
# How much of the page colour the card's wash lays down, and how far its edge fades out.
CARD_WASH = 0.82
CARD_FEATHER = 8
ICON_SIZE = 28
CATEGORY_LABELS = {
    "art_design": "艺术设计", "sports": "体育", "news_politics": "新闻时政", "fashion_culture": "时尚文化",
    "science_nature": "科学自然", "entertainment_music": "娱乐音乐", "adult": "成人", "general_history": "综合历史",
}
PROVIDER_LABELS = {
    "internet_archive": "Internet Archive", "wikimedia_commons": "Wikimedia Commons",
    "library_of_congress": "美国国会图书馆", "gallica": "Gallica", "publisher_web": "出版方官网",
}
# A historical issue's tag is warm brown; a current issue's is red.
ERA_FILLS = {"historical": (139, 90, 43), "latest": (205, 38, 46)}
_LIMITS = ImageLimits(max_bytes=256 * 1024, max_width=256, max_height=256, max_pixels=256 * 256)
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})")


def cover_box(index, count, dimensions):
    """(left, top, width, height) the index-th cover may fill, above its card."""
    width, height = dimensions
    column = width // count
    left = index * column
    column_width = column if index < count - 1 else width - left
    return left + PAD, PAD, column_width - 2 * PAD, height - INFO_HEIGHT - 2 * PAD


def card_box(index, count, dimensions):
    left, _top, width, _height = cover_box(index, count, dimensions)
    return left, dimensions[1] - INFO_HEIGHT - PAD, left + width, dimensions[1] - PAD


def cover_info(record, this_year):
    """The card's lines for one cover record (bank record or decorated cover)."""
    record = record or {}
    year = record.get("year")
    try:
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    issue_date = str(record.get("issue_date") or "").strip()
    month = _ISO_MONTH.match(issue_date)
    if month:
        year = year or int(month.group(1))
        when = f"{int(month.group(1))} 年 {int(month.group(2))} 月"
    elif year:
        when = f"{year} 年"
    else:
        when = str(record.get("issue") or issue_date or "").strip()[:24]
    era = "historical" if record.get("temporal_class") == "historical" else "latest"
    if era == "historical" and year and this_year - year > 0:
        era_text = f"{this_year - year} 年前"
    else:
        era_text = "最新" if era == "latest" else "往期"
    category = str(record.get("category") or "general_history")
    provider = str(record.get("provider") or "")
    return {
        "publication": str(record.get("publication") or record.get("source_name") or "Magazine").strip(),
        "when": when,
        "era": era,
        "era_text": era_text,
        "category": category if category in CATEGORY_LABELS else "general_history",
        "detail": " · ".join(part for part in (CATEGORY_LABELS.get(category, ""),
                                               PROVIDER_LABELS.get(provider, provider.replace("_", " ").title()))
                             if part),
    }


def provider_label(record):
    provider = str((record or {}).get("provider") or "")
    if provider == "publisher_web":
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", str((record or {}).get("page_url") or "")).split("/")[0])
        return host or PROVIDER_LABELS["publisher_web"]
    return PROVIDER_LABELS.get(provider, provider.replace("_", " ").title())


def gallery_label(record, this_year):
    """The shared gallery's label for one cover: the magazine (its title without the date the
    chip already gives), the issue date with its age or price and its volume and number, then
    the issue's headline, who published or drew it, its category and, when free of copyright,
    that it is in the public domain, after the logo of the collection the scan came from."""
    info = cover_info(record, this_year)
    record = record or {}
    issue_date = str(record.get("issue_date") or "").strip()
    day = re.match(r"^(\d{4})-(\d{2})-(\d{2})", issue_date)
    chip = f"{day.group(1)}.{day.group(2)}.{day.group(3)}" if day else (info["when"] or info["era_text"])
    beside = str(record.get("price") or "").strip()
    if info["era"] == "historical" or not beside:
        beside = info["era_text"] if chip != info["era_text"] else ""
    publication = clean_publication(info["publication"])
    title = str(record.get("title") or "").split("|")[0].strip()
    number = issue_number(record.get("issue"), title)
    publisher = publishers(record.get("attribution"), publication)
    if not re.search(r"[^\W\d_]{3,}", _TRAILING_DATE.sub("", publication)) and publisher:
        # A scan filed under its date ("2026-10-01-ew") is named by its publisher.
        publication, publisher = publisher.split(" · ")[0], " · ".join(publisher.split(" · ")[1:])
    # The headline is the issue's own (a cover line), else its title less what the card already
    # shows: the number, the date, the name.
    issue = str(record.get("issue") or "").strip()
    headline = issue if issue and not number.startswith(issue[:3]) and issue.lower() != publication.lower() \
        else clean_publication(_NUMBER.sub("", title).strip(" ,"))
    if (headline.lower().startswith(("buy ", "best sellers")) or headline.lower() in (publication.lower(), "")
            or not re.search(r"[^\W\d_]{3,}", _TRAILING_DATE.sub("", headline))):
        headline = ""
    category = CATEGORY_LABELS.get(info["category"], "")
    rights = "公有领域" if "public domain" in str(record.get("rights") or "").lower() else ""
    facts = " · ".join(part for part in (publisher, category, rights) if part)
    # Where the scan came from leads the last line by its logo; the headline and facts follow it.
    return Label(title=publication, chip=chip, lines=(" · ".join(part for part in (beside, number) if part),),
                 body=headline, source=facts, source_logo=provider_label(record))


# A date or year the chip already gives, at the end of a scan's file title: "1966-03-24",
# "(1973 04)", "193002", "Feb 17 1933", "-1924".
_TRAILING_DATE = re.compile(
    r"[\s,._-]*(?:\(?\b(?:1[7-9]|20)\d{2}(?:[\s._-]?\d{2}){0,2}\)?|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+(?:1[7-9]|20)\d{2})\s*$",
    re.IGNORECASE)
_NUMBER = re.compile(r"\b(?:vol(?:ume)?\.?\s*(\d+))?[\s,]*(?:issue|no\.?|#)\s*(\d+)\b|\bv(\d+)\s*n(\d+)\b",
                     re.IGNORECASE)


def clean_publication(text):
    """A scan's title without the date or year at its end (the chip shows it), a scanner's
    credit after that date, or the issue number the card shows on its own; as it is when that
    would leave nothing but a date."""
    text = str(text or "").strip()
    cleaned = _SCAN_CREDIT.sub("", text)
    for _ in range(2):
        cleaned = _TRAILING_DATE.sub("", cleaned).strip(" ,-_")
    cleaned = re.sub(r"\s*#\d+$", "", cleaned)
    return cleaned if re.search(r"[^\W\d_]{3,}", cleaned) else text


# A scanner's credit in parentheses after the issue's date: "(1973 04) (LennyS aMouse)".
_SCAN_CREDIT = re.compile(r"(\(\s*(?:1[7-9]|20)\d{2}[^)]*\))(?:\s*\([^)\d]*\))+\s*$")


def issue_number(issue, title=""):
    """"Vol. 1, No. 1" from the record's issue field, else from its title ("vol1 issue1",
    "v01n01", "#30"); "" when neither names one."""
    issue = str(issue or "").strip()
    if re.search(r"\b(?:vol|no)\b", issue, re.IGNORECASE):
        return issue
    match = _NUMBER.search(str(title or ""))
    if not match:
        return ""
    volume, number, short_volume, short_number = match.groups()
    volume, number = volume or short_volume, number or short_number
    return f"Vol. {int(volume)}, No. {int(number)}" if volume else f"No. {int(number)}"


def publishers(attribution, publication=""):
    """Who published or drew the issue, from the record's attribution ("GORDON STEVENSON; Time
    Magazine; Wikimedia Commons"): its names without links, collections or generic placeholders,
    at most two."""
    names = []
    for part in str(attribution or "").split(";"):
        name = part.strip().strip(",")
        lowered = name.lower()
        if (not name or "://" in name or lowered in _NOT_PUBLISHERS or lowered == str(publication or "").lower()
                or len(name) > 48):
            continue
        name = name.title() if name.isupper() else name
        if name not in names:
            names.append(name)
    return " · ".join(names[:2])


_NOT_PUBLISHERS = {"internet archive", "internet archive contributor", "wikimedia commons", "library of congress",
                   "gallica", "bibliothèque nationale de france", "unknown", "anonymous"}


@lru_cache(maxsize=len(CATEGORY_LABELS))
def _category_icon(category):
    path = ASSETS / f"category_{category}.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_LIMITS) as source:
        return source.convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)


def _fit(draw, text, size, width, bold=True, minimum=10):
    for candidate in range(size, minimum - 1, -1):
        font = get_base_ui_font(candidate, bold=bold)
        if draw.textlength(text, font=font) <= width:
            return text, font
    font = get_base_ui_font(minimum, bold=bold)
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…", font


def draw_card(image, box, info, *, dark=False):
    """Draw one cover's card on ``image`` (RGB) inside ``box``."""
    x0, y0, x1, y1 = box
    paper, ink, muted = ((24, 26, 30), (240, 240, 236), (196, 198, 192)) if dark else \
        ((255, 255, 255), (16, 18, 20), (70, 74, 78))
    # No frame: a wash of the page colour with feathered edges, so the card melts into the
    # blurred backdrop and only its text stands out.
    feather = CARD_FEATHER
    outer = (x0 - feather, y0 - feather, x1 + feather, y1 + feather)
    mask = Image.new("L", (outer[2] - outer[0], outer[3] - outer[1]), 0)
    ImageDraw.Draw(mask).rounded_rectangle((feather, feather, feather + x1 - x0, feather + y1 - y0), radius=CARD_RADIUS,
                                           fill=round(255 * CARD_WASH))
    mask = mask.filter(ImageFilter.GaussianBlur(feather / 2))
    region = image.crop(outer)
    image.paste(Image.composite(Image.new("RGB", region.size, paper), region, mask), outer[:2])
    draw = ImageDraw.Draw(image)
    icon = _category_icon(info["category"])
    text_x = x0 + 10
    if icon is not None:
        image.paste(icon, (x0 + 8, y0 + 8), icon)
        text_x = x0 + 8 + ICON_SIZE + 7
    title, font = _fit(draw, info["publication"], 16, x1 - 8 - text_x, minimum=11)
    draw.text((text_x, y0 + 8 + ICON_SIZE / 2), title, font=font, fill=ink, anchor="lm")
    row_y = y0 + 8 + ICON_SIZE + 9
    era_font = get_base_ui_font(12, bold=True)
    era_width = int(draw.textlength(info["era_text"], font=era_font)) + 12
    draw.rounded_rectangle((x0 + 8, row_y - 1, x0 + 8 + era_width, row_y + 17), radius=8, fill=ERA_FILLS[info["era"]])
    draw.text((x0 + 14, row_y + 8), info["era_text"], font=era_font, fill=(255, 255, 255), anchor="lm")
    if info["when"]:
        when, when_font = _fit(draw, info["when"], 13, x1 - 10 - (x0 + 16 + era_width))
        draw.text((x0 + 16 + era_width, row_y + 8), when, font=when_font, fill=ink, anchor="lm")
    if info["detail"]:
        detail, detail_font = _fit(draw, info["detail"], 11, x1 - x0 - 18, bold=True, minimum=9)
        draw.text((x0 + 9, y1 - 9), detail, font=detail_font, fill=muted, anchor="ls")
