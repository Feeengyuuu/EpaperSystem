"""The live overview page: Steam-wide totals over two boards of five games each.

Drawn natively (no browser): a header with the wordmark and Steam's users online and in
game, then trending games (24-hour movers) and the most played, each row with the store
header art, names, genres and developer, price or release, the all-time peak and 30-day
average, and the player count over its own trend line.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.flat_icons import draw_icon

ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"
HEADER_H = 56
SECTION_TOP = 62
ROWS_TOP = 90
COVER = (112, 52)
COVER_RADIUS = 6
METRIC_W = 112
NAME_SIZES = (17, 16, 15, 14, 13, 12, 11)
# The top three ranks wear medals; the rest an ink disc.
MEDALS = ((240, 185, 40), (196, 204, 216), (205, 127, 60))
RANK_DISC = 20
FIRST_WEEK_DAYS, NEW_RELEASE_DAYS = 7, 30
# The Steam logo (x, y, size), then the wordmark from WORDMARK_LEFT to WORDMARK_RIGHT: its lettering
# drawn larger than its own pixels and its skyline grown into what is left, both standing on the
# header rule so their underline and the rule are one line.
LOGO_BOX = (10, 4, 48)
WORDMARK_LEFT, WORDMARK_RIGHT, WORDMARK_BASELINE = 64, 420, HEADER_H + 2
LETTERING_SCALE = 1.45
# Where the wordmark's pieces sit in its own 320x72 pixels: the skyline's first building,
# the end of the lettering, the row below "STEAM CHARTS" and the underline's first row.
SKYLINE_X, LETTERING_END, LETTERING_BOTTOM, UNDERLINE_TOP = 116, 158, 58, 70
# Refined accents rather than the panel's pure inks; night lifts each tone.
ACCENTS = {
    "day": {"up": (5, 150, 105), "down": (200, 30, 50), "blue": (27, 111, 168), "gold": (196, 140, 0)},
    "night": {"up": (52, 211, 153), "down": (255, 107, 120), "blue": (102, 192, 244), "gold": (251, 191, 36)},
}


def _release_date(text):
    """A store release date ("2026 年 10 月 16 日" or "Oct 16, 2026"), or None."""
    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    try:
        if match:
            return date(*(int(part) for part in match.groups()))
        for pattern in ("%b %d, %Y", "%d %b, %Y", "%B %d, %Y", "%d %B, %Y"):
            try:
                return datetime.strptime(text.strip(), pattern).date()
            except ValueError:
                continue
    except ValueError:
        return None
    return None


def _blend(colour, other, amount):
    return tuple(round(a * amount + b * (1 - amount)) for a, b in zip(colour, other))


def _split_wordmark(source):
    """(lettering, skyline) of the title wordmark, in its own pixel grid.

    The skyline is everything right of the lettering plus the low buildings under its end;
    the underline beneath those buildings stays in both, so the two halves join on it.
    """
    width, height = source.size
    skyline_mask = Image.new("L", source.size, 0)
    mask_draw = ImageDraw.Draw(skyline_mask)
    mask_draw.rectangle((LETTERING_END, 0, width, height), fill=255)
    mask_draw.rectangle((SKYLINE_X, LETTERING_BOTTOM, width, height), fill=255)
    skyline = Image.new("RGBA", source.size, (0, 0, 0, 0))
    skyline.paste(source, (0, 0), skyline_mask)
    lettering_mask = skyline_mask.copy()
    ImageDraw.Draw(lettering_mask).rectangle((SKYLINE_X, UNDERLINE_TOP, LETTERING_END - 1, height), fill=0)
    lettering = source.copy()
    lettering.paste((0, 0, 0, 0), (0, 0, width, height), lettering_mask)
    return lettering, skyline


def _shown(part):
    """The part cropped to what shows: the art carries near-clear specks across its whole canvas."""
    box = part.getchannel("A").point(lambda alpha: 255 if alpha > 24 else 0).getbbox()
    return part.crop(box) if box else None


@lru_cache(maxsize=32)
def _icon(name, size):
    """The badge at this size, decoded once per process (the rows reuse three of them)."""
    path = ICON_DIR / f"{name}.png"
    if not path.is_file():
        return None
    try:
        with Image.open(path) as source:
            return ImageOps.contain(source.convert("RGBA"), (size, size), method=Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None


def wan(value):
    """A count in 万/亿 for the header totals."""
    if not isinstance(value, (int, float)):
        return "--"
    if value >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if value >= 1e4:
        return f"{value / 1e4:.0f}万" if value >= 1e7 else f"{value / 1e4:.1f}万"
    return f"{int(value):,}"


class LiveOverviewMixin:
    def _render_live_overview(self, dimensions, chart_groups, totals, theme_context, updated_at_text):
        night = isinstance(theme_context, dict) and theme_context.get("mode") == "night"
        colors = dict(self._theme_colors(theme_context))
        colors.update(ACCENTS["night" if night else "day"])
        colors["muted"] = _blend(colors["ink"], colors["paper"], 0.58)
        colors["rule"] = _blend(colors["ink"], colors["paper"], 0.22)
        image = Image.new("RGB", (800, 480), colors["paper"])
        draw = ImageDraw.Draw(image)

        self._overview_header(image, draw, totals or {}, colors, updated_at_text)
        columns = ((12, 394), (406, 788))
        for (left, right), group in zip(columns, chart_groups):
            self._overview_board(image, draw, (left, right), group, colors)
        if tuple(dimensions) != (800, 480):
            image = image.resize(tuple(dimensions), Image.Resampling.LANCZOS)
        return image

    # -- pieces ----------------------------------------------------------------------
    def _overview_icon(self, image, draw, name, fallback, xy, size, colour, colors):
        """A generated icon from assets/icons when present, else the flat procedural one."""
        icon = _icon(name, size)
        if icon is not None:
            image.paste(icon, (int(xy[0]), int(xy[1])), icon)
            return
        draw_icon(draw, fallback, xy, size, colour, colors["paper"])

    def _overview_wordmark(self, image, colors):
        """The wordmark's lettering enlarged, then its skyline lifted out and grown up to WORDMARK_RIGHT."""
        source = self._theme_title_wordmark(colors)
        if source is None:
            return
        lettering, skyline = (_shown(part) for part in _split_wordmark(source))
        if lettering is None:
            return
        lettering = lettering.resize((round(lettering.width * LETTERING_SCALE), round(lettering.height * LETTERING_SCALE)),
                                     Image.Resampling.LANCZOS)
        image.paste(lettering, (WORDMARK_LEFT, WORDMARK_BASELINE - lettering.height), lettering)
        if skyline is None:
            return
        # It starts where the lettering's underline ends and stands on the same line.
        left = WORDMARK_LEFT + lettering.width
        grow = min((WORDMARK_RIGHT - left) / skyline.width, (WORDMARK_BASELINE - 2) / skyline.height)
        skyline = skyline.resize((round(skyline.width * grow), round(skyline.height * grow)), Image.Resampling.LANCZOS)
        image.paste(skyline, (left, WORDMARK_BASELINE - skyline.height), skyline)

    def _overview_header(self, image, draw, totals, colors, updated_at_text):
        """Logo and wordmark standing on the header rule, the 48-hour line, then the totals by the source."""
        self._paste_steam_logo(image, *LOGO_BOX, colors)
        self._overview_wordmark(image, colors)
        site = "steamcharts.com"
        draw.text((788 - draw.textlength(site, font=self._font(9)), 12), site, font=self._font(9), fill=colors["muted"])
        text_left = 788 - draw.textlength(site, font=self._font(9))
        if updated_at_text:
            width = draw.textlength(updated_at_text, font=self._font(11))
            draw.text((788 - width, 38), updated_at_text, font=self._font(11), fill=colors["muted"])
            text_left = min(text_left, 788 - width)

        number_font = self._font(19, "bold")
        blocks = [(key, label, icon, fallback, 24 + max(draw.textlength(wan(totals[key]), font=number_font), 70))
                  for key, label, icon, fallback in (("online", "在线", "users", "users"),
                                                     ("ingame", "游戏中", "ingame", "gamepad")) if key in totals]
        # The totals sit just left of the source and refresh time; the 48-hour line takes the space before them.
        x = round(text_left - 14 - sum(block[-1] for block in blocks) - 18 * max(0, len(blocks) - 1))
        spark = totals.get("spark") or []
        if len(spark) > 1 and x - 16 - (WORDMARK_RIGHT + 12) > 60:
            # The filled line stands on the header rule like the skyline; its label lies over it, haloed,
            # and a dashed cursor drops from the latest point as if it were being hovered.
            left = WORDMARK_RIGHT + 12
            end_x, end_y = self._overview_spark(draw, (left, 8, x - 16, HEADER_H - 1), spark, colors["blue"], colors)
            for y in range(round(end_y) + 5, HEADER_H - 1, 4):
                draw.line((end_x, y, end_x, min(y + 1, HEADER_H - 2)), fill=colors["blue"], width=1)
            draw.text((left + 4, HEADER_H - 18), "48 小时", font=self._font(11, "bold"), fill=colors["ink"],
                      stroke_width=2, stroke_fill=colors["paper"])
        for key, label, icon, fallback, width in blocks:
            self._overview_icon(image, draw, icon, fallback, (x, 10), 18, colors["blue"], colors)
            draw.text((x + 24, 7), label, font=self._font(11), fill=colors["muted"])
            draw.text((x + 24, 21), wan(totals[key]), font=number_font, fill=colors["ink"])
            peak = f"峰值 {wan(totals.get(key + '_peak'))}"
            draw.text((x + 24, 42), peak, font=self._font(10), fill=colors["muted"])
            x += width + 18
        draw.line((12, HEADER_H, 788, HEADER_H), fill=colors["ink"], width=2)

    def _overview_board(self, image, draw, columns, group, colors):
        left, right = columns
        trending = group.get("table_variant") == "trending"
        title = "热门飙升 ‧ 24H" if trending else "在线人数 TOP 5"
        icon, fallback, colour = ("trending", "fire", colors["up"]) if trending else ("live", "pulse", colors["blue"])
        self._overview_icon(image, draw, icon, fallback, (left, SECTION_TOP + 2), 20, colour, colors)
        draw.text((left + 26, SECTION_TOP), title, font=self._font(16, "bold"), fill=colors["ink"])
        note = "24 小时涨幅" if trending else "当前 / 24h 峰值"
        draw.text((right - draw.textlength(note, font=self._font(10)), SECTION_TOP + 5), note,
                  font=self._font(10), fill=colors["muted"])
        games = (group.get("games") or [])[:5]
        row_h = (472 - ROWS_TOP) / 5
        for index, game in enumerate(games):
            top = ROWS_TOP + index * row_h
            self._overview_row(image, draw, (left + 4, top, right, top + row_h), game, trending, colors)
            if index < len(games) - 1:
                for x in range(left, right, 4):
                    draw.point((x, int(top + row_h - 2)), fill=colors["rule"])

    def _overview_row(self, image, draw, box, game, trending, colors):
        left, top, right, bottom = box
        top = int(top)
        cover_xy = (left, top + 5)
        self._overview_cover(image, draw, cover_xy, game, colors)
        self._overview_badges(draw, cover_xy, game, colors)

        text_x, text_right = left + COVER[0] + 10, right - METRIC_W - 8
        hero_w = self._overview_metric(draw, box, game, trending, colors)
        store = game.get("store") or {}
        name = str(game.get("name") or "")
        # Line two is the English name or the developer, then the genres; line three the price,
        # or the record and the month's average. Without line two the others centre on the art.
        secondary = str(game.get("secondary_name") or "")
        developer = str(store.get("developer") or "")
        genres = " / ".join(store.get("genres") or [])
        facts = " · ".join(part for part in ((secondary, genres, developer) if secondary else (developer, genres)) if part)
        name_y, detail_y = (top + 3, top + 41) if facts else (top + 11, top + 35)
        # The name runs on to the figure beside it, smaller before it is cut short.
        name_width = right - hero_w - 10 - text_x
        name_font = next((self._font(size, "bold") for size in NAME_SIZES
                          if draw.textlength(name, font=self._font(size, "bold")) <= name_width),
                         self._font(NAME_SIZES[-1], "bold"))
        draw.text((text_x, name_y + (17 - name_font.size) // 2), self._overview_clip(draw, name, name_font, name_width),
                  font=name_font, fill=colors["ink"])
        if facts:
            draw.text((text_x, top + 24), self._overview_clip(draw, facts, self._font(10), text_right - text_x),
                      font=self._font(10), fill=colors["muted"])
        self._overview_detail_line(image, draw, (text_x, detail_y, text_right), game, store, trending, colors)

    def _overview_cover(self, image, draw, xy, game, colors):
        """The store art with rounded corners on a hairline shadow."""
        x, y = xy
        box = (x, y, x + COVER[0], y + COVER[1])
        draw.rounded_rectangle((x + 2, y + 2, box[2] + 2, box[3] + 2), radius=COVER_RADIUS, fill=colors["rule"])
        cover = self._decode_data_image(game.get("image"))
        if cover is None:
            draw.rounded_rectangle(box, radius=COVER_RADIUS, fill=colors["rule"])
            return
        fitted = ImageOps.fit(cover.convert("RGB"), COVER, method=Image.Resampling.LANCZOS)
        mask = Image.new("L", COVER, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, COVER[0] - 1, COVER[1] - 1), radius=COVER_RADIUS, fill=255)
        image.paste(fitted, (x, y), mask)

    def _overview_badges(self, draw, xy, game, colors):
        """The rank as a medal on the art's corner (gold, silver, bronze, then ink), its move since
        the board last changed along the art's foot, and a new release's tag beside it."""
        x, y = xy
        rank = int(game.get("rank") or 0)
        if rank:
            fill = MEDALS[rank - 1] if rank <= len(MEDALS) else colors["ink"]
            ink = (20, 20, 20) if rank <= len(MEDALS) else colors["paper"]
            disc = (x - 5, y - 4, x - 5 + RANK_DISC, y - 4 + RANK_DISC)
            draw.ellipse(disc, fill=fill, outline=colors["paper"], width=2)
            font = self._font(11, "bold")
            label = str(rank)
            draw.text(((disc[0] + disc[2] - draw.textlength(label, font=font)) / 2, disc[1] + 3), label, font=font,
                      fill=ink)
        foot = y + COVER[1] - 3
        move = game.get("rank_move")
        chip_x = x + 3
        if move == "new" or (isinstance(move, int) and move):
            font = self._font(9, "bold")
            if move == "new":
                label, fill, arrow = "NEW", colors["gold"], None
            else:
                label, fill, arrow = str(abs(move)), colors["up"] if move > 0 else colors["down"], move > 0
            width = draw.textlength(label, font=font) + (17 if arrow is not None else 10)
            draw.rounded_rectangle((chip_x, foot - 13, chip_x + width, foot), radius=4, fill=fill)
            text_x = chip_x + 5
            if arrow is not None:
                tip, base = (foot - 11, foot - 3) if arrow else (foot - 3, foot - 11)
                draw.polygon(((text_x, base), (text_x + 7, base), (text_x + 3.5, tip)), fill=colors["paper"])
                text_x += 9
            draw.text((text_x, foot - 13), label, font=font, fill=colors["paper"])
            chip_x += width + 4
        tag = self._overview_release_tag(game)
        if tag:
            font = self._font(9, "bold")
            width = draw.textlength(tag, font=font) + 10
            right = x + COVER[0] - 3
            draw.rounded_rectangle((right - width, foot - 13, right, foot), radius=4, fill=colors["blue"])
            draw.text((right - width + 5, foot - 13), tag, font=font, fill=colors["paper"])

    @staticmethod
    def _overview_release_tag(game, today=None):
        """首周 in a game's first week out, 新品 in its first month; nothing before release or after."""
        released = _release_date(str(((game.get("store") or {}).get("release")) or ""))
        if released is None:
            return ""
        age = ((today or datetime.now(timezone.utc).date()) - released).days
        if 0 <= age < FIRST_WEEK_DAYS:
            return "首周"
        if 0 <= age < NEW_RELEASE_DAYS:
            return "新品"
        return ""

    def _overview_metric(self, draw, box, game, trending, colors):
        """The row's figure and its trend line; returns how wide the figure stands at the top.

        A mover's figure is its 24-hour gain (on a solid chip), its players below; a top game's is
        its players now, with how far that is up today's peak as a bar and its 30-day line."""
        left, top, right, bottom = box
        top = int(top)
        current = game.get("current_players")
        number = f"{current:,}" if isinstance(current, (int, float)) else str(game.get("current_players_fmt") or "--")
        change = self._overview_change(game) if trending else None
        spark = game.get("spark") or []
        if trending and isinstance(change, (int, float)):
            text = f"{'+' if change >= 0 else ''}{change:,.0f}%" if abs(change) >= 100 else f"{change:+.1f}%"
            chip_font = self._font(14, "bold")
            chip_w = draw.textlength(text, font=chip_font) + 14
            colour = colors["up"] if change >= 0 else colors["down"]
            draw.rounded_rectangle((right - chip_w, top + 2, right, top + 23), radius=6, fill=colour)
            draw.text((right - chip_w + 7, top + 3), text, font=chip_font, fill=colors["paper"])
            players = f"{number} 在线"
            draw.text((right - draw.textlength(players, font=self._font(10)), top + 26), players, font=self._font(10),
                      fill=colors["ink"])
            if len(spark) > 1:
                base = game.get("spark_base")
                rising = spark[-1] >= (base if isinstance(base, (int, float)) else spark[0])
                self._overview_spark(draw, (right - METRIC_W, top + 42, right, bottom - 6), spark,
                                     colors["up"] if rising else colors["down"], colors,
                                     base=base if isinstance(base, (int, float)) else None)
            return chip_w
        number_font = self._font(18, "bold")
        width = draw.textlength(number, font=number_font)
        draw.text((right - width, top + 1), number, font=number_font, fill=colors["ink"])
        peak = game.get("peak_24h")
        if not trending and isinstance(peak, (int, float)) and peak > 0:
            label = f"峰值 {peak:,}"
            draw.text((right - draw.textlength(label, font=self._font(10)), top + 30), label, font=self._font(10),
                      fill=colors["muted"])
            # How far today's peak the players now are, right under the count.
            share = max(0.0, min(1.0, current / peak)) if isinstance(current, (int, float)) else 0.0
            bar = (right - METRIC_W, top + 24, right, top + 28)
            draw.rounded_rectangle(bar, radius=2, fill=colors["rule"])
            if share > 0:
                draw.rounded_rectangle((bar[0], bar[1], bar[0] + max(4, (bar[2] - bar[0]) * share), bar[3]), radius=2,
                                       fill=colors["blue"])
        if len(spark) > 1:
            # 48 hours of a mover in green or red; 30 days of daily peaks of a top game in blue.
            colour = (colors["up"] if spark[-1] >= spark[0] else colors["down"]) if trending else colors["blue"]
            self._overview_spark(draw, (right - METRIC_W, top + 45, right, bottom - 6), spark, colour, colors,
                                 mark_peak=not trending)
        return width

    def _overview_detail_line(self, image, draw, box, game, store, trending, colors):
        """A mover's price (Steam's own discount block) and reviews; a top game's all-time peak
        and 30-day average."""
        x, y, right = box
        font = self._font(10)
        if trending:
            price = str(store.get("price") or "")
            discount = store.get("discount")
            if discount:
                label = f"-{discount}%"
                block_font = self._font(10, "bold")
                width = draw.textlength(label, font=block_font) + 8
                draw.rectangle((x, y, x + width, y + 15), fill=colors["up"])
                draw.text((x + 4, y + 1), label, font=block_font, fill=colors["paper"])
                x += width + 5
            if price:
                price_font = self._font(11, "bold")
                draw.text((x, y), price, font=price_font, fill=colors["ink"])
                x += draw.textlength(price, font=price_font) + 10
            reviews = store.get("recommendations")
            parts = [("reviews", "ok", f"{wan(reviews)} 评测", colors["ink"])] if isinstance(reviews, int) and reviews else []
        else:
            parts = []
            peak = game.get("peak_all")
            average = game.get("avg_30d")
            if isinstance(peak, (int, float)):
                when = datetime.fromtimestamp(game["peak_all_ts"] / 1000, timezone.utc).strftime("%y/%m") \
                    if game.get("peak_all_ts") else ""
                record = f"{wan(peak)} {when}".strip()
                # The record's month gives way before the month's average does.
                if isinstance(average, (int, float)) and \
                        x + 15 + draw.textlength(f"{record}  月均 {wan(average)}", font=font) + 10 > right:
                    record = wan(peak)
                parts.append(("peak", "trophy", record, colors["gold"]))
            if isinstance(average, (int, float)):
                parts.append((None, None, f"月均 {wan(average)}", colors["ink"]))
        for name, fallback, text, colour in parts:
            text_w = draw.textlength(text, font=font) + (15 if name else 0)
            if x + text_w > right:
                break
            if name:
                self._overview_icon(image, draw, name, fallback, (x, y + 2), 12, colour, colors)
                x += 15
            draw.text((x, y + 1), text, font=font, fill=colour)
            x += text_w - (15 if name else 0) + 10

    def _overview_spark(self, draw, box, values, colour, colors, base=None, mark_peak=False):
        """A filled trend line ending on a dot; ``base`` adds a dashed line at that value (24 hours
        back) and ``mark_peak`` a small pointer on the highest point."""
        left, top, right, bottom = box
        if mark_peak:
            top += 7  # room for the pointer over the highest point
        scale = list(values) + ([base] if base is not None else [])
        low, high = min(scale), max(scale)
        span = (high - low) or 1

        def y_of(value):
            return bottom - (bottom - top) * (value - low) / span

        points = [(left + (right - left) * index / (len(values) - 1), y_of(value)) for index, value in enumerate(values)]
        draw.polygon(points + [(right, bottom), (left, bottom)], fill=_blend(colour, colors["paper"], 0.22))
        if base is not None:
            y = round(y_of(base))
            for x in range(int(left), int(right), 6):
                draw.line((x, y, min(x + 2, right), y), fill=colors["muted"], width=1)
        draw.line(points, fill=colour, width=2, joint="curve")
        if mark_peak:
            peak_x, peak_y = max(points, key=lambda point: -point[1])
            draw.polygon(((peak_x - 3.5, peak_y - 8), (peak_x + 3.5, peak_y - 8), (peak_x, peak_y - 3)),
                         fill=colors["gold"])
        end_x, end_y = points[-1]
        draw.ellipse((end_x - 2.5, end_y - 2.5, end_x + 2.5, end_y + 2.5), fill=colour)
        return end_x, end_y

    @staticmethod
    def _overview_change(game):
        text = str(game.get("change_24h_fmt") or "").replace(",", "").replace("%", "").strip()
        try:
            return float(text)
        except ValueError:
            change = game.get("change_24h")
            return change if isinstance(change, (int, float)) else None

    @staticmethod
    def _overview_clip(draw, text, font, width):
        text = str(text or "")
        if draw.textlength(text, font=font) <= width:
            return text
        while text and draw.textlength(text + "…", font=font) > width:
            text = text[:-1]
        return text.rstrip() + "…" if text else ""
