"""Pillow recreation of the ten-offer design: two columns of five rows."""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from utils.app_utils import get_base_ui_font
from utils.doodle_backdrop import paint_doodles
from utils.local_wordmark import paste_local_wordmark

from .price_tag import PriceTags


SIZE = (800, 480)
LOCAL_ZONE = ZoneInfo("America/Los_Angeles")
ROWS = 5
ROW_PITCH = 74
CARD_HEIGHT = 68
CARD_RECTS = tuple((14 + col * 399, 73 + row * ROW_PITCH, 386 + col * 399, 73 + row * ROW_PITCH + CARD_HEIGHT)
                   for row in range(ROWS) for col in range(2))
ART_SIZE = (150, 62)
BADGE_SIZE = (58, 28)
# Room between the emboldened dollar sign and the digits after it.
DOLLAR_GAP = 2
# Preserve the accepted sale/original/discount column positions while the
# engraved type fits independently inside the sale-price slot.
PRICE_TAG_PAD = (13, 6)
# Quiet discount doodles (percent tags, snipped price tags, falling arrows, sale stickers) in the
# badges' green under each column's names and prices; the capsule art keeps the plain page around it.
DEAL_DOODLES = Path(__file__).with_name("assets") / "doodles" / "deals.png"
# The official marks' heights: Steam's logo in the header, CheapShark's shark and lettering in the footer.
STEAM_LOGO_HEIGHT, CHEAPSHARK_MARK_HEIGHT, CHEAPSHARK_LETTERS_HEIGHT = 30, 22, 13


def _font(size, bold=False):
    return get_base_ui_font(size, bold=bold)


def _mark(name, height, ink=None):
    """An official mark from assets/ (tools/build_game_deals_logos.py) ``height`` tall; with
    ``ink``, its shape in that colour (black artwork on a night page, lettering kept as a mask)."""
    try:
        with Image.open(Path(__file__).with_name("assets") / f"{name}.png") as source:
            mark = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    mark = mark.resize((max(1, round(mark.width * height / mark.height)), height), Image.Resampling.LANCZOS)
    if ink is not None:
        tinted = Image.new("RGBA", mark.size, (*ink, 255))
        tinted.putalpha(mark.getchannel("A"))
        mark = tinted
    return mark


def _paste_mark(image, mark, x, middle):
    """Paste ``mark`` with its left edge at ``x`` and centred on ``middle``; the x after it."""
    image.paste(mark, (round(x), round(middle - mark.height / 2)), mark)
    return x + mark.width


def _title_line(text, width):
    # One line per offer: shrink first, then cut at the end with an ellipsis.
    for size in range(18, 14, -1):
        font = _font(size, True)
        if font.getlength(text) <= width:
            return text, font
    line = text
    while line and font.getlength(line.rstrip() + "…") > width:
        line = line[:-1]
    return line.rstrip() + "…", font


def _fitted_font(text, width, preferred, minimum=14, bold=False):
    for size in range(preferred, minimum - 1, -1):
        font = _font(size, bold)
        if font.getlength(text) <= width:
            return font
    return _font(minimum, bold)


def render_page(snapshot, covers, *, dimensions=SIZE, theme=None, now=None):
    if tuple(dimensions) != SIZE:
        raise ValueError("Game Deals layout requires the device's 800x480 landscape display")
    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_ZONE)
    night = (theme or {}).get("mode") == "night"
    background = (9, 16, 13) if night else (250, 248, 239)
    ink = (250, 250, 240) if night else (8, 12, 10)
    muted = (205, 215, 207) if night else (68, 75, 70)
    green = (20, 108, 58)
    image = Image.new("RGB", SIZE, background)
    draw = ImageDraw.Draw(image)
    assets = Path(__file__).with_name("assets")
    if not paste_local_wordmark(image, assets / "title_wordmark.png", (14, 8, 197, 58)):
        draw.text((14, 10), "游戏优惠", font=_font(43, True), fill=ink, anchor="lt")
    draw.line((207, 17, 207, 56), fill=ink, width=2)
    # The store by its own logo (black artwork, in the page's ink at night), then the region.
    steam = _mark("steam_logo", STEAM_LOGO_HEIGHT, ink if night else None)
    region_x = _paste_mark(image, steam, 220, 38) + 8 if steam is not None else 220
    draw.text((region_x, 38), "‧ 美国区" if steam is not None else "STEAM ‧ 美国区", font=_font(20, True), fill=ink,
              anchor="lm")
    if not paste_local_wordmark(image, assets / "usd_wordmark.png", (587, 18, 680, 54)):
        draw.rectangle((619, 20, 680, 52), outline=ink, width=2)
        draw.text((626, 25), "USD", font=_font(24, True), fill=ink, anchor="lt")
    draw.text((694, 19), now.strftime("%m.%d"), font=_font(31, True), fill=ink, anchor="lt")
    draw.line((14, 67, 785, 67), fill=ink, width=2)
    for column in range(2):
        x0, _y0, x1, _y1 = CARD_RECTS[column]
        prices = (x0 + ART_SIZE[0] + 4, 70, x1 + (10 if column == 0 else 3), CARD_RECTS[-1][3] + 3)
        paint_doodles(image, prices, DEAL_DOODLES, green, offset=prices[:2])

    layout = []
    price_tags = PriceTags() if snapshot.deals else None
    art_w, art_h = ART_SIZE
    badge_w, badge_h = BADGE_SIZE
    for index, deal in enumerate(snapshot.deals[:len(CARD_RECTS)]):
        x0, y0, x1, y1 = CARD_RECTS[index]
        art_y = y0 + (CARD_HEIGHT - art_h) // 2
        cover = covers.get(deal["game_id"])
        if cover is not None:
            # Preserve the complete capsule and never enlarge a small source.
            # Unused space is the page itself: no frame or added black padding.
            fitted = cover.copy()
            fitted.thumbnail(ART_SIZE, Image.Resampling.LANCZOS)
            image.paste(fitted, (x0 + (art_w - fitted.width) // 2,
                                 art_y + (art_h - fitted.height) // 2))
            fitted.close()
        else:
            # Same footprint as a 231x87 Steam capsule shrunk into the column.
            placeholder = (x0, art_y + 3, x0 + art_w, art_y + art_h - 3)
            draw.rectangle(placeholder, fill=(22, 41, 52))
            draw.text(((placeholder[0] + placeholder[2]) / 2, (placeholder[1] + placeholder[3]) / 2),
                      "STEAM", font=_font(20, True), fill="white", anchor="mm")
        text_x = x0 + 160
        available = x1 - text_x
        # Only the name changes: Steam's Simplified Chinese name when it has one.
        line, title_font = _title_line(deal.get("title_zh") or deal["title"], available)

        # The sale price and struck original share one line beside the discount badge.
        badge_left = x1 - badge_w
        prices_limit = badge_left - 4 - text_x
        sale = "$" + deal["sale_price"]
        original = "$" + deal["normal_price"]
        original_font = _font(15)
        tag_pad = sum(PRICE_TAG_PAD)
        price_font = _fitted_font(sale, prices_limit - 8 - DOLLAR_GAP - tag_pad - original_font.getlength(original),
                                  26, 17, True)
        if price_font.getlength(sale) + DOLLAR_GAP + tag_pad + 8 + original_font.getlength(original) > prices_limit:
            original_font = _fitted_font(original, prices_limit - 8 - DOLLAR_GAP - tag_pad - price_font.getlength(sale),
                                         15, 11)
        sale_w = price_font.getlength(sale) + DOLLAR_GAP + tag_pad
        # Exceptional long amounts may use a smaller engraved font, never consume
        # the original-price or discount columns or omit any amount characters.
        sale_w = min(sale_w, prices_limit - 8 - original_font.getlength(original))
        original_x = text_x + sale_w + 8

        title_height = max(title_font.getbbox("Ag", anchor="lt")[3], title_font.getbbox(line, anchor="lt")[3])
        group_top = y0 + (CARD_HEIGHT - (title_height + 5 + badge_h)) // 2
        price_row = group_top + title_height + 5
        draw.text((text_x, group_top), line, font=title_font, fill=ink, anchor="lt")
        _, sale_top, _, sale_bottom = price_font.getbbox(sale, anchor="ls")
        baseline = price_row + badge_h / 2 - (sale_top + sale_bottom) / 2
        tag_box = tuple(map(round, (text_x, price_row - 1, text_x + sale_w, price_row + badge_h + 1)))
        tag = price_tags.render(deal["sale_price"], (tag_box[2] - tag_box[0] + 1, tag_box[3] - tag_box[1] + 1))
        image.paste(tag, tag_box[:2], tag)
        draw.text((original_x, baseline), original, font=original_font, fill=muted, anchor="ls")
        strike_y = round(baseline + original_font.getbbox("0", anchor="ls")[1] / 2)
        draw.line((original_x, strike_y, original_x + int(original_font.getlength(original)), strike_y),
                  fill=muted, width=1)
        draw.rectangle((badge_left, price_row, x1, price_row + badge_h), fill=green)
        discount = f"-{deal['discount_percent']}%"
        badge_font = _fitted_font(discount, badge_w - 6, 20, 14, True)
        draw.text((badge_left + badge_w / 2, price_row + badge_h / 2), discount,
                  font=badge_font, fill="white", anchor="mm")
        layout.append({"title": deal["title"], "lines": [line], "text_width": available,
                       "title_widths": [title_font.getlength(line)], "box": (x0, y0, x1, y1),
                       "prices_right": original_x + original_font.getlength(original),
                       "badge_left": badge_left, "price_row": price_row,
                       "sale_price": sale, "price_tag_box": tag_box, "price_font_size": tag.info["price_font_size"]})
    draw.line((399, 72, 399, 440), fill=ink, width=1)
    for row in range(1, ROWS):
        y = CARD_RECTS[row * 2][1] - (ROW_PITCH - CARD_HEIGHT) // 2
        draw.line((14, y, 785, y), fill=ink, width=1)
    if not snapshot.deals:
        title = "暂时没有符合条件的优惠" if snapshot.state != "unavailable" else "优惠数据暂时不可用"
        detail = "下次更新后自动显示" if snapshot.state != "unavailable" else "等待重新连接 CheapShark"
        draw.rectangle((14, 70, 785, 442), fill=background)
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
