"""Pillow layout of the ten offers: the day's best value as a large card on the left, the other
nine as a numbered list on the right (the owner's chosen design A, 2026-11-08)."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from utils.doodle_backdrop import paint_doodles
from utils.local_wordmark import load_source_logo, paste_local_wordmark
from utils.steam_font import steam_font

from .facts import STEAM_RATINGS, low_mark
from .price_tag import PriceTags


SIZE = (800, 480)
LOCAL_ZONE = ZoneInfo("America/Los_Angeles")
ASSETS = Path(__file__).with_name("assets")
# The hero card on the left: its Steam header art (460x215, shrunk, never enlarged), then its name,
# its price row (sale price, struck original, discount block), the reviews and the all-time low by
# their official marks, and a line of Steam's verdict.
HERO = (14, 74, 378, 442)
# The art's top meets the list's first row; the name sits on a baseline a clear gap under the art,
# the price row just under the name, and the verdict's line centres on the list's last row, so the
# card and the list start and end together.
HERO_ART = (14, 74, 378, 246)
HERO_TITLE_BASELINE, HERO_PRICE = 284, (292, 362)
HERO_FACTS_MIDDLE, HERO_NOTE_MIDDLE = 390, 422
HERO_BADGE_W, HERO_PRICE_SIZE = 112, 64
# The card's right foot, beside the marks and the verdict: savings, deal rating, when the low was seen.
HERO_ASIDE_LEFT, HERO_ASIDE_Y = 262, 376
# The other nine offers, one row each down the right column: its rank, its capsule, its name with
# its marks under it, its sale price over the struck original, and its discount block.
LIST_LEFT, LIST_RIGHT = 396, 785
LIST_TOP, ROW_PITCH, ROW_HEIGHT = 74, 41, 40
LIST_ROWS = 9
RANK_W, THUMB = 20, (84, 32)
LIST_TEXT_X = LIST_LEFT + RANK_W + 6 + THUMB[0] + 6
LIST_BADGE = (48, 24)
LIST_PRICE_W = 62
# A row's name baseline and its marks' centre, from the row's top.
ROW_TITLE_BASELINE, ROW_MARKS_MIDDLE = 18, 29
# Quiet discount doodles (percent tags, snipped price tags, falling arrows, sale stickers) in the
# badges' green under the hero's text and the list's names and prices; the art keeps the plain page.
DEAL_DOODLES = ASSETS / "doodles" / "deals.png"
# The official marks' heights: Steam's logo in the header, CheapShark's shark and lettering in the
# footer, and the review sources' marks in the hero and the rows.
STEAM_LOGO_HEIGHT, CHEAPSHARK_MARK_HEIGHT, CHEAPSHARK_LETTERS_HEIGHT = 30, 22, 13
HERO_MARK_HEIGHT, ROW_MARK_HEIGHT = 18, 12
# The all-time low on a small solid chip with white words (day hues, then the brighter night ones);
# Metacritic's score and Steam's review share are each its source's mark and the figure in the ink.
CRIMSON = {"day": (200, 30, 50), "night": (226, 54, 72)}
# A sale above its all-time low names that low on a graphite chip instead.
GRAPHITE = {"day": (38, 44, 52), "night": (52, 60, 70)}
LOW_LABELS = {"low": "史低", "near": "近史低", "free": "曾免费"}
DEAL_GREEN = (20, 108, 58)


def _font(size, bold=False):
    # Steam's own Motiva Sans when installed on the device (utils.steam_font), else the UI font.
    return steam_font(size, bold=bold)


def _mark(name, height, ink=None):
    """An official mark from assets/ (tools/build_game_deals_logos.py) ``height`` tall; with
    ``ink``, its shape in that colour (black artwork on a night page, lettering kept as a mask)."""
    try:
        with Image.open(ASSETS / f"{name}.png") as source:
            mark = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    mark = mark.resize((max(1, round(mark.width * height / mark.height)), height), Image.Resampling.LANCZOS)
    if ink is not None:
        tinted = Image.new("RGBA", mark.size, (*ink, 255))
        tinted.putalpha(mark.getchannel("A"))
        mark = tinted
    return mark


def _source_mark(name, height, ink):
    """A source's logo with its colours kept: only its near-black lettering takes ``ink`` (a night page)."""
    return load_source_logo(ASSETS / f"{name}.png", height, ink)


def _paste_mark(image, mark, x, middle):
    """Paste ``mark`` with its left edge at ``x`` and centred on ``middle``; the x after it."""
    image.paste(mark, (round(x), round(middle - mark.height / 2)), mark)
    return x + mark.width


def _fitted_font(text, width, preferred, minimum=14, bold=False):
    for size in range(preferred, minimum - 1, -1):
        font = _font(size, bold)
        if font.getlength(text) <= width:
            return font
    return _font(minimum, bold)


def _one_line(text, width, largest, smallest, bold=True):
    """``text`` on one line: shrink first, then cut at the end with an ellipsis."""
    for size in range(largest, smallest - 1, -1):
        font = _font(size, bold)
        if font.getlength(text) <= width:
            return text, font
    line = text
    while line and font.getlength(line.rstrip() + "…") > width:
        line = line[:-1]
    return line.rstrip() + "…", font


def pick_hero(deals):
    """The index of the day's best value: an offer at its all-time low first, then the critics'
    and players' verdicts, then the deeper discount; the earliest on a tie."""
    def value(item):
        index, deal = item
        low = low_mark(deal)[0]
        return (low in {"low", "free"}, low == "near", int(deal.get("metacritic") or 0),
                int(deal.get("steam_rating_percent") or 0), int(deal.get("discount_percent") or 0), -index)
    return max(enumerate(deals), key=value)[0] if deals else None


def _fit_art(image, cover, box):
    """The complete art centred in ``box``, shrunk to fit but never enlarged; the page shows round it."""
    left, top, right, bottom = box
    if cover is None:
        return None
    fitted = cover.copy()
    fitted.thumbnail((right - left, bottom - top), Image.Resampling.LANCZOS)
    x = left + (right - left - fitted.width) // 2
    y = top + (bottom - top - fitted.height) // 2
    image.paste(fitted, (x, y))
    rect = (x, y, x + fitted.width, y + fitted.height)
    fitted.close()
    return rect


def _placeholder(draw, box, size):
    draw.rectangle(box, fill=(22, 41, 52))
    draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "STEAM", font=_font(size, True), fill="white",
              anchor="mm")


def _chip(draw, x, middle, label, fill, font, height, ink="white"):
    """A small solid chip with ``label`` centred in it, its left edge at ``x``; the x after it."""
    width = round(font.getlength(label)) + 2 * max(4, height // 3)
    top = round(middle - height / 2)
    draw.rounded_rectangle((x, top, x + width, top + height), radius=max(3, height // 4), fill=fill)
    draw.text((x + width / 2, middle), label, font=font, fill=ink, anchor="mm")
    return x + width


def _marks(image, draw, deal, x, middle, right, mode, ink, *, mark_height, font):
    """The offer's all-time low, Metacritic score and Steam review share from ``x`` along ``middle``,
    the review sources named by their official marks; a mark that would pass ``right`` is left off.
    Returns what was drawn, for the layout record."""
    drawn, height = [], mark_height + 2
    gap = max(5, mark_height // 2)
    text_ink = ink or (8, 12, 10)
    items = []
    low, low_price = low_mark(deal)
    if low:
        label = LOW_LABELS.get(low) or f"史低 ${low_price}"
        items.append(("low", label, None, CRIMSON[mode] if low in {"low", "near"} else GRAPHITE[mode]))
    score = int(deal.get("metacritic") or 0)
    if score:
        items.append(("metacritic", str(score), "metacritic_mark", None))
    percent = int(deal.get("steam_rating_percent") or 0)
    if percent and deal.get("steam_rating"):
        items.append(("steam_rating", f"{percent}%", "steam_mark", None))
    for kind, label, logo_name, fill in items:
        # Steam's black mark takes the night page's ink; Metacritic's roundel keeps its own black
        # disc inside its yellow ring, which holds on either page.
        logo = _source_mark(logo_name, mark_height, ink if logo_name == "steam_mark" else None) if logo_name else None
        label_w = round(font.getlength(label))
        needed = (logo.width + 3 if logo is not None else 0) + label_w + (2 * max(4, height // 3) if fill else 0)
        if x + needed > right:
            continue
        if logo is not None:
            x = _paste_mark(image, logo, x, middle) + 3
        if fill:
            # The all-time low stays a solid chip; the scores are their source's mark and the figure alone.
            x = _chip(draw, x, middle, label, fill, font, height) + gap
        else:
            draw.text((x, middle), label, font=font, fill=text_ink, anchor="lm")
            x += label_w + gap
        drawn.append((kind, label))
    return drawn


def _header(image, draw, now, ink, night):
    if not paste_local_wordmark(image, ASSETS / "title_wordmark.png", (14, 8, 197, 58)):
        draw.text((14, 10), "游戏优惠", font=_font(43, True), fill=ink, anchor="lt")
    draw.line((207, 17, 207, 56), fill=ink, width=2)
    # The store by its own logo (black artwork, in the page's ink at night), then the region.
    steam = _mark("steam_logo", STEAM_LOGO_HEIGHT, ink if night else None)
    region_x = _paste_mark(image, steam, 220, 38) + 8 if steam is not None else 220
    draw.text((region_x, 38), "‧ 美国区" if steam is not None else "STEAM ‧ 美国区", font=_font(20, True), fill=ink,
              anchor="lm")
    # The date alone at the right (the owner dropped the currency mark; the footer names USD).
    date = now.strftime("%m.%d")
    date_font = _font(31, True)
    draw.text((785 - date_font.getlength(date), 19), date, font=date_font, fill=ink, anchor="lt")
    draw.line((14, 67, 785, 67), fill=ink, width=2)


def _price_row(image, draw, price_tags, deal, box, *, badge_w, original_size, badge_size, muted, stacked,
               price_size=30):
    """The sale price (greenback green, straight on the page), the struck original and the discount
    block inside ``box``; ``stacked`` sets the original under the sale price. The layout facts."""
    left, top, right, bottom = box
    badge_left = right - badge_w
    sale, original = "$" + deal["sale_price"], "$" + deal["normal_price"]
    discount = f"-{deal['discount_percent']}%"
    draw.rectangle((badge_left, top, right, bottom), fill=DEAL_GREEN)
    badge_font = _fitted_font(discount, badge_w - 6, badge_size, 12, True)
    draw.text((badge_left + badge_w / 2, (top + bottom) / 2), discount, font=badge_font, fill="white", anchor="mm")
    if stacked:
        tag_box = (left, top - 3, badge_left - 6, top + round((bottom - top) * 0.62))
        original_font = _fitted_font(original, badge_left - 6 - left, original_size, 9)
        original_x = badge_left - 6 - original_font.getlength(original)
        baseline = bottom + 3
    else:
        original_font = _font(original_size)
        tag_box = (left, top - 2, badge_left - 10 - original_font.getlength(original) - 8, bottom + 2)
        baseline = None
    tag_box = tuple(map(round, tag_box))
    tag = price_tags.render(deal["sale_price"], (tag_box[2] - tag_box[0] + 1, tag_box[3] - tag_box[1] + 1), price_size)
    image.paste(tag, tag_box[:2], tag)
    if not stacked:
        ink_right = tag_box[0] + tag.info["price_ink_bounds"][2]
        original_x = ink_right + 10
        _, sale_top, _, sale_bottom = original_font.getbbox(original, anchor="ls")
        baseline = (top + bottom) / 2 - (sale_top + sale_bottom) / 2
    draw.text((original_x, baseline), original, font=original_font, fill=muted, anchor="ls")
    strike_y = round(baseline + original_font.getbbox("0", anchor="ls")[1] / 2)
    draw.line((original_x, strike_y, original_x + int(original_font.getlength(original)), strike_y), fill=muted, width=1)
    return {"sale_price": sale, "price_tag_box": tag_box, "price_font_size": tag.info["price_font_size"],
            "prices_right": original_x + original_font.getlength(original), "badge_left": badge_left,
            "price_row": top}


def _verdict(image, draw, deal, x, middle, right, ink, muted):
    """Steam's review summary in its Chinese words and how many reviews it rests on, after Steam's mark."""
    summary = STEAM_RATINGS.get(str(deal.get("steam_rating") or ""))
    count = int(deal.get("steam_rating_count") or 0)
    if not summary or not count:
        return ""
    reviews = f"{count / 10000:.1f} 万" if count >= 10000 else f"{count:,}"
    text = f"{summary} ‧ {reviews} 篇评测"
    logo = _source_mark("steam_mark", 16, ink)
    if logo is not None:
        x = _paste_mark(image, logo, x, middle) + 6
    line, font = _one_line(text, right - x, 15, 12, bold=True)
    draw.text((x, middle), line, font=font, fill=muted, anchor="lm")
    return line


def _aside(image, draw, deal, right, ink, muted):
    """The hero's foot at its right: what the sale saves, CheapShark's deal rating by its mark, and
    when the all-time low was seen (or the release year). The lines drawn, for the layout record."""
    lines = []
    saved = Decimal(deal["normal_price"]) - Decimal(deal["sale_price"])
    if saved > 0:
        lines.append(("saved", f"立省 ${saved:.2f}", _font(17, True), ink, None))
    if deal.get("deal_rating"):
        shark = _mark("cheapshark_mark", 15)
        lines.append(("deal_rating", f"好价 {deal['deal_rating']}/10", _font(14, True), ink, shark))
    when = ""
    if deal.get("lowest_at") and low_mark(deal)[0]:
        when = "史低见于 " + datetime.fromtimestamp(deal["lowest_at"], LOCAL_ZONE).strftime("%Y-%m")
    elif deal.get("release_date"):
        when = datetime.fromtimestamp(deal["release_date"], LOCAL_ZONE).strftime("%Y 年发售")
    if when:
        lines.append(("when", when, _font(13, True), muted, None))
    y = HERO_ASIDE_Y
    for _kind, text, font, colour, mark in lines:
        width = font.getlength(text)
        draw.text((right, y), text, font=font, fill=colour, anchor="rt")
        if mark is not None:
            image.paste(mark, (round(right - width - 4 - mark.width), y + 1), mark)
        y += font.size + 6
    return [(kind, text) for kind, text, *_rest in lines]


def _hero(image, draw, deal, cover, price_tags, mode, ink, muted):
    left, _top, right, _bottom = HERO
    art = _fit_art(image, cover, HERO_ART)
    if art is None:
        art = (HERO_ART[0], HERO_ART[1] + 20, HERO_ART[2], HERO_ART[3] - 20)
        _placeholder(draw, art, 30)
    name = deal.get("title_zh") or deal["title"]
    line, title_font = _one_line(name, right - left, 28, 18)
    draw.text((left, HERO_TITLE_BASELINE), line, font=title_font, fill=ink, anchor="ls")
    facts = _price_row(image, draw, price_tags, deal, (left, HERO_PRICE[0], right, HERO_PRICE[1]),
                       badge_w=HERO_BADGE_W, original_size=20, badge_size=40, muted=muted, stacked=False,
                       price_size=HERO_PRICE_SIZE)
    # The marks and Steam's verdict keep to the card's left; its right foot holds the aside.
    marks = _marks(image, draw, deal, left, HERO_FACTS_MIDDLE, HERO_ASIDE_LEFT - 8, mode,
                   ink if mode == "night" else None, mark_height=HERO_MARK_HEIGHT, font=_font(15, True))
    note = _verdict(image, draw, deal, left, HERO_NOTE_MIDDLE, HERO_ASIDE_LEFT - 8,
                    ink if mode == "night" else None, muted)
    aside = _aside(image, draw, deal, right, ink, muted)
    return {"role": "hero", "title": deal["title"], "lines": [line], "text_width": right - left,
            "title_widths": [title_font.getlength(line)], "box": HERO, "art_rect": art, "marks": marks,
            "note": note, "aside": aside, **facts}


def _row(image, draw, deal, rank, top, cover, price_tags, mode, ink, muted):
    middle = top + ROW_HEIGHT / 2
    # The rank as a bare figure in the ink, no tab behind it.
    draw.text((LIST_LEFT + RANK_W / 2, middle), str(rank), font=_fitted_font(str(rank), RANK_W, 16, 11, True),
              fill=ink, anchor="mm")
    thumb_left = LIST_LEFT + RANK_W + 6
    thumb_box = (thumb_left, round(middle - THUMB[1] / 2), thumb_left + THUMB[0], round(middle + THUMB[1] / 2))
    art = _fit_art(image, cover, thumb_box)
    if art is None:
        art = thumb_box
        _placeholder(draw, thumb_box, 11)
    badge_left = LIST_RIGHT - LIST_BADGE[0]
    price_left = badge_left - 6 - LIST_PRICE_W
    text_right = price_left - 6
    name = deal.get("title_zh") or deal["title"]
    line, title_font = _one_line(name, text_right - LIST_TEXT_X, 15, 12)
    # The name on a baseline and its marks under it, the pair centred in the row clear of its rules.
    draw.text((LIST_TEXT_X, top + ROW_TITLE_BASELINE), line, font=title_font, fill=ink, anchor="ls")
    marks = _marks(image, draw, deal, LIST_TEXT_X, top + ROW_MARKS_MIDDLE, text_right, mode,
                   ink if mode == "night" else None, mark_height=ROW_MARK_HEIGHT, font=_font(10, True))
    badge_top = round(middle - LIST_BADGE[1] / 2)
    facts = _price_row(image, draw, price_tags, deal, (price_left, badge_top, LIST_RIGHT, badge_top + LIST_BADGE[1]),
                       badge_w=LIST_BADGE[0], original_size=12, badge_size=17, muted=muted, stacked=True)
    return {"role": "row", "rank": rank, "title": deal["title"], "lines": [line], "text_width": text_right - LIST_TEXT_X,
            "title_widths": [title_font.getlength(line)], "box": (LIST_LEFT, top, LIST_RIGHT, top + ROW_HEIGHT),
            "art_rect": art, "marks": marks, **facts}


def render_page(snapshot, covers, *, dimensions=SIZE, theme=None, now=None):
    if tuple(dimensions) != SIZE:
        raise ValueError("Game Deals layout requires the device's 800x480 landscape display")
    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_ZONE)
    night = (theme or {}).get("mode") == "night"
    mode = "night" if night else "day"
    background = (9, 16, 13) if night else (250, 248, 239)
    ink = (250, 250, 240) if night else (8, 12, 10)
    muted = (205, 215, 207) if night else (68, 75, 70)
    image = Image.new("RGB", SIZE, background)
    draw = ImageDraw.Draw(image)
    _header(image, draw, now, ink, night)

    layout = []
    deals = list(snapshot.deals[:1 + LIST_ROWS])
    if deals:
        for region in ((HERO[0], HERO_ART[3] + 4, HERO[2], HERO[3]), (LIST_TEXT_X - 4, 70, LIST_RIGHT, 442)):
            paint_doodles(image, region, DEAL_DOODLES, DEAL_GREEN, offset=region[:2])
        price_tags = PriceTags(night)
        hero_index = pick_hero(deals)
        hero = deals[hero_index]
        hero_cover = covers.get(f"hero:{hero['game_id']}") or covers.get(hero["game_id"])
        layout.append(_hero(image, draw, hero, hero_cover, price_tags, mode, ink, muted))
        others = [deal for index, deal in enumerate(deals) if index != hero_index]
        for position, deal in enumerate(others):
            top = LIST_TOP + position * ROW_PITCH
            layout.append(_row(image, draw, deal, position + 2, top, covers.get(deal["game_id"]), price_tags,
                               mode, ink, muted))
        draw.line((388, 72, 388, 440), fill=ink, width=1)
        for position in range(1, len(others)):
            y = LIST_TOP + position * ROW_PITCH - 1
            draw.line((LIST_LEFT, y, LIST_RIGHT, y), fill=muted, width=1)
    else:
        title = "暂时没有符合条件的优惠" if snapshot.state != "unavailable" else "优惠数据暂时不可用"
        detail = "下次更新后自动显示" if snapshot.state != "unavailable" else "等待重新连接 CheapShark"
        draw.text((80, 192), title, font=_font(33, True), fill=ink, anchor="lt")
        draw.text((80, 245), detail, font=_font(23), fill=muted, anchor="lt")

    draw.line((14, 445, 785, 445), fill=ink, width=2)
    # The prices' source by its marks: the CheapShark shark, then its lettering in the page's ink.
    shark, letters = _mark("cheapshark_mark", CHEAPSHARK_MARK_HEIGHT), _mark("cheapshark_letters", CHEAPSHARK_LETTERS_HEIGHT, ink)
    if shark is not None and letters is not None:
        source_x = _paste_mark(image, letters, _paste_mark(image, shark, 14, 463) + 5, 463) + 7
        draw.text((source_x, 463), "‧ Steam 美元报价", font=_font(15, True), fill=ink, anchor="lm")
    else:
        draw.text((14, 455), "CheapShark ‧ Steam 美元报价", font=_font(15, True), fill=ink, anchor="lt")
    stamp = snapshot.fetched_at.astimezone(LOCAL_ZONE).strftime("%m/%d %H:%M") if snapshot.fetched_at else "暂无数据"
    if snapshot.state == "stale":
        status, status_color = f"缓存已过期 ‧ {stamp}", (226, 79, 59) if night else (166, 35, 24)
    elif snapshot.state == "unavailable":
        status, status_color = "连接暂不可用 ‧ 等待更新", (226, 79, 59) if night else (166, 35, 24)
    else:
        status, status_color = f"更新 {stamp} ‧ 每 2 小时", ink
    footer_font = _fitted_font(status, 390, 15, 14, True)
    draw.text((785 - footer_font.getlength(status), 455), status, font=footer_font, fill=status_color, anchor="lt")
    image.info["game_deals_layout"] = layout
    image.info["game_deals_state"] = snapshot.state
    return image
