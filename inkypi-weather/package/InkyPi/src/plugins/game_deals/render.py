"""Pillow recreation of the approved six-offer, two-column design."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from utils.app_utils import get_base_ui_font


SIZE = (800, 480)
LOCAL_ZONE = ZoneInfo("America/Los_Angeles")
CARD_RECTS = tuple((14 + col * 399, 80 + row * 122, 386 + col * 399, 193 + row * 122)
                   for row in range(3) for col in range(2))


def _font(size, bold=False):
    return get_base_ui_font(size, bold=bold)


def _title_lines(text, width, *, max_lines=3):
    font = _font(18, True)
    lines, line = [], ""
    # Break at words when possible; exceptionally long tokens are split safely.
    for word in text.split():
        trial = f"{line} {word}".strip()
        if font.getlength(trial) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = ""
        for char in word:
            if line and font.getlength(line + char) > width:
                lines.append(line)
                line = ""
            line += char
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and font.getlength(last + "…") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines, font


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
    draw.text((14, 10), "游戏优惠", font=_font(43, True), fill=ink, anchor="lt")
    draw.line((207, 17, 207, 56), fill=ink, width=2)
    draw.text((220, 30), "STEAM ‧ 美国区", font=_font(20, True), fill=ink, anchor="lt")
    draw.rectangle((619, 20, 680, 52), outline=ink, width=2)
    draw.text((626, 25), "USD", font=_font(24, True), fill=ink, anchor="lt")
    draw.text((694, 19), now.strftime("%m.%d"), font=_font(31, True), fill=ink, anchor="lt")
    draw.line((14, 67, 785, 67), fill=ink, width=2)

    layout = []
    for index, deal in enumerate(snapshot.deals[:6]):
        x0, y0, x1, y1 = CARD_RECTS[index]
        art_box = (x0, y0 + 3, x0 + 150, y0 + 104)
        cover = covers.get(deal["game_id"])
        if cover is not None:
            # Preserve the complete capsule and never enlarge a small source.
            # Unused space is the page itself: no frame or added black padding.
            fitted = cover.copy()
            fitted.thumbnail((150, 101), Image.Resampling.LANCZOS)
            image.paste(fitted, (x0 + (150 - fitted.width) // 2,
                                 y0 + 3 + (101 - fitted.height) // 2))
            fitted.close()
        else:
            draw.rectangle(art_box, fill=(22, 41, 52))
            draw.text((x0 + 29, y0 + 43), "STEAM", font=_font(22, True), fill="white", anchor="lt")
        text_x = x0 + 161
        available = x1 - text_x
        lines, title_font = _title_lines(deal["title"], available)
        for number, line in enumerate(lines):
            draw.text((text_x, y0 + 2 + number * 20), line, font=title_font, fill=ink, anchor="lt")
        sale = "$" + deal["sale_price"]
        price_font = _fitted_font(sale, available - 67, 29, 17, True)
        draw.text((text_x, y0 + 65), sale, font=price_font, fill=ink, anchor="lt")
        original = "$" + deal["normal_price"]
        original_font = _fitted_font(original, available - 67, 16)
        draw.text((text_x, y0 + 96), original, font=original_font, fill=muted, anchor="lt")
        draw.line((text_x, y0 + 103, text_x + int(original_font.getlength(original)), y0 + 103),
                  fill=muted, width=1)
        badge = (x1 - 62, y0 + 72, x1, y0 + 105)
        draw.rectangle(badge, fill=green)
        discount = f"-{deal['discount_percent']}%"
        badge_font = _fitted_font(discount, 56, 22, 16, True)
        draw.text((badge[0] + (62 - badge_font.getlength(discount)) / 2, y0 + 79),
                  discount, font=badge_font, fill="white", anchor="lt")
        layout.append({"title": deal["title"], "lines": lines, "text_width": available,
                       "title_widths": [title_font.getlength(line) for line in lines]})
    draw.line((399, 80, 399, 435), fill=ink, width=1)
    for y in (197, 319):
        draw.line((14, y, 785, y), fill=ink, width=1)
    if not snapshot.deals:
        title = "暂时没有符合条件的优惠" if snapshot.state != "unavailable" else "优惠数据暂时不可用"
        detail = "下次更新后自动显示" if snapshot.state != "unavailable" else "等待重新连接 CheapShark"
        draw.rectangle((14, 78, 785, 438), fill=background)
        draw.text((80, 192), title, font=_font(33, True), fill=ink, anchor="lt")
        draw.text((80, 245), detail, font=_font(23), fill=muted, anchor="lt")

    draw.line((14, 445, 785, 445), fill=ink, width=2)
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
