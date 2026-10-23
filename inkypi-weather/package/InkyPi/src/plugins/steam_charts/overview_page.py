"""The live overview page: Steam-wide totals over two boards of five games each.

Drawn natively (no browser): a header with the wordmark and Steam's users online and in
game, then trending games (24-hour movers) and the most played, each row with the store
header art, names, genres and developer, price or release, the all-time peak and 30-day
average, and the player count over its own trend line.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.flat_icons import draw_icon

ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"
HEADER_H = 56
SECTION_TOP = 62
ROWS_TOP = 90
COVER = (112, 52)
METRIC_W = 112
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
            self._overview_row(image, draw, (left, top, right, top + row_h), game, trending, colors)
            if index < len(games) - 1:
                for x in range(left, right, 4):
                    draw.point((x, int(top + row_h - 2)), fill=colors["rule"])

    def _overview_row(self, image, draw, box, game, trending, colors):
        left, top, right, bottom = box
        cover_box = (left, int(top + 4), left + COVER[0], int(top + 4) + COVER[1])
        cover = self._decode_data_image(game.get("image"))
        if cover is not None:
            fitted = ImageOps.fit(cover.convert("RGB"), COVER, method=Image.Resampling.LANCZOS)
            image.paste(fitted, cover_box[:2])
        else:
            draw.rectangle(cover_box, fill=colors["rule"])
        rank = str(game.get("rank") or "")
        tab = (left, cover_box[1], left + 9 + 8 * len(rank), cover_box[1] + 17)
        draw.rectangle(tab, fill=colors["ink"])
        draw.text((left + 4, cover_box[1]), rank, font=self._font(12, "bold"), fill=colors["paper"])

        text_x, text_right = left + COVER[0] + 8, right - METRIC_W - 8
        width = text_right - text_x
        store = game.get("store") or {}
        name = str(game.get("name") or "")
        current = game.get("current_players")
        number = f"{current:,}" if isinstance(current, (int, float)) else str(game.get("current_players_fmt") or "--")
        number_font = self._font(18, "bold")
        # The name runs on to the player count beside it, smaller before it is cut short.
        name_width = right - draw.textlength(number, font=number_font) - 10 - text_x
        name_font = next((self._font(size, "bold") for size in (15, 14, 13)
                          if draw.textlength(name, font=self._font(size, "bold")) <= name_width), self._font(12, "bold"))
        draw.text((text_x, top + 2 + (15 - name_font.size) // 2), self._overview_clip(draw, name, name_font, name_width),
                  font=name_font, fill=colors["ink"])
        # Line two is the English name, or the developer when there is none; line three the genres.
        secondary = str(game.get("secondary_name") or "")
        developer = str(store.get("developer") or "")
        genres = " / ".join(store.get("genres") or [])
        if secondary:
            facts = " · ".join(part for part in (genres, developer) if part)
        else:
            secondary, facts = developer, genres
        average = game.get("avg_30d")
        if not trending and isinstance(average, (int, float)):
            facts = " · ".join(part for part in (facts, f"月均 {wan(average)}") if part)
        if secondary:
            draw.text((text_x, top + 21), self._overview_clip(draw, secondary, self._font(10), width),
                      font=self._font(10), fill=colors["muted"])
        draw.text((text_x, top + 35), self._overview_clip(draw, facts, self._font(10), width),
                  font=self._font(10), fill=colors["ink"])
        self._overview_detail_line(image, draw, (text_x, top + 50, text_right), game, store, trending, colors)

        draw.text((right - draw.textlength(number, font=number_font), top + 1), number, font=number_font,
                  fill=colors["ink"])
        # The trending board ranks by steamcharts' own 24-hour gain, so its figure is shown as given.
        change = self._overview_change(game)
        if trending and isinstance(change, (int, float)):
            text = f"{'+' if change >= 0 else ''}{change:,.0f}%" if abs(change) >= 100 else f"{change:+.1f}%"
            chip_font = self._font(11, "bold")
            chip_w = draw.textlength(text, font=chip_font) + 12
            colour = colors["up"] if change >= 0 else colors["down"]
            draw.rounded_rectangle((right - chip_w, top + 25, right, top + 41), radius=8, fill=colour)
            draw.text((right - chip_w + 6, top + 26), text, font=chip_font, fill=colors["paper"])
        else:
            peak = game.get("peak_24h")
            text = f"峰值 {peak:,}" if isinstance(peak, (int, float)) else f"峰值 {game.get('peak_players_fmt') or '--'}"
            draw.text((right - draw.textlength(text, font=self._font(10)), top + 27), text, font=self._font(10),
                      fill=colors["muted"])
        spark = game.get("spark") or []
        if len(spark) > 1:
            # 48 hours of a mover in green or red; 30 days of daily peaks of a top game in blue.
            colour = (colors["up"] if spark[-1] >= spark[0] else colors["down"]) if trending else colors["blue"]
            self._overview_spark(draw, (right - METRIC_W, top + 46, right, bottom - 6), spark, colour, colors)

    def _overview_detail_line(self, image, draw, box, game, store, trending, colors):
        """Price and release for a trending game; all-time peak and 30-day average for a top game."""
        x, y, right = box
        parts = []
        if trending:
            price = store.get("price")
            if price:
                discount = store.get("discount")
                parts.append(("price", "tag", f"{price}{f' -{discount}%' if discount else ''}",
                              colors["down"] if discount else colors["ink"]))
            if store.get("release"):
                parts.append(("release", "calendar", str(store["release"]), colors["ink"]))
        else:
            peak = game.get("peak_all")
            if isinstance(peak, (int, float)):
                when = datetime.fromtimestamp(game["peak_all_ts"] / 1000, timezone.utc).strftime("%y/%m") \
                    if game.get("peak_all_ts") else ""
                parts.append(("peak", "trophy", f"{wan(peak)} {when}".strip(), colors["gold"]))
        reviews = store.get("recommendations")
        if trending and isinstance(reviews, int) and reviews:
            parts.append(("reviews", "ok", f"{wan(reviews)} 评测", colors["ink"]))
        font = self._font(10)
        for name, fallback, text, colour in parts:
            text_w = draw.textlength(text, font=font)
            if x + 15 + text_w > right:
                break
            self._overview_icon(image, draw, name, fallback, (x, y + 1), 12, colour, colors)
            draw.text((x + 15, y), text, font=font, fill=colour)
            x += 15 + text_w + 10

    def _overview_spark(self, draw, box, values, colour, colors):
        left, top, right, bottom = box
        low, high = min(values), max(values)
        span = (high - low) or 1
        points = [(left + (right - left) * index / (len(values) - 1),
                   bottom - (bottom - top) * (value - low) / span) for index, value in enumerate(values)]
        draw.polygon(points + [(right, bottom), (left, bottom)], fill=_blend(colour, colors["paper"], 0.22))
        draw.line(points, fill=colour, width=2, joint="curve")
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
