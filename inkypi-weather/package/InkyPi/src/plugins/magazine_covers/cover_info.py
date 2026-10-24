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

from PIL import Image, ImageDraw

from utils.app_utils import get_base_ui_font
from utils.safe_image import ImageLimits, safe_open_image

ASSETS = Path(__file__).with_name("assets")
INFO_HEIGHT = 84
# Clear of the 8 px media inset the theme chrome frames.
PAD = 12
CARD_RADIUS = 10
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
    region = image.crop(box)
    image.paste(Image.blend(region, Image.new("RGB", region.size, paper), 0.9), box[:2])
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius=CARD_RADIUS, outline=ink, width=2)
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
