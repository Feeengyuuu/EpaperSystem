"""The Telegram Digest page: one lead story beside a ranked list of the rest.

The lead takes a landscape picture across the top, a portrait one (phone
screenshots) down its left side, or none at all; list rows open with a line
of topic pill, source and right-aligned age, then carry a headline of up to
two lines (one, with a summary line, when it is short) and a small thumbnail.
Every coordinate is laid out for 800x480 and scaled to the panel.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

from plugins.telegram_digest.curation import (
    VISIBLE_COUNT,
    display_headline,
    display_summary,
    feature_picture,
    original_excerpt,
)
from utils.doodle_backdrop import paint_doodles

LIST_ROWS = VISIBLE_COUNT - 1
PORTRAIT_RATIO = 1.05
# A second story fills the room the lead's text leaves when there is this much of it (page px);
# the lead's summary then keeps to this many lines.
SECOND_MIN_H, SECOND_LEAD_SUMMARY_LINES = 120, 4
# A portrait lead's photo is this wide beside its text; with no second picture to fill the room
# its text leaves, the photo widens (the text column narrowing to as little as TEXT_MIN_W) until
# the text runs down the column to within PORTRAIT_FILL_SLACK of its foot. Text too short for
# even that column lies on a band at the foot of the photo, which then fills the whole lead.
PORTRAIT_PHOTO_W, PORTRAIT_TEXT_MIN_W, PORTRAIT_FILL_SLACK = 176, 168, 28
# A list row's headline starts this far under the row's top, below its topic and source line;
# a row with a picture keeps this width for it beside the headline. A headline's second line
# shorter than this share of the row's width is evened out with the first.
ROW_TEXT_TOP, ROW_THUMB_W, ROW_SHORT_LINE = 18, 96, 0.25
KIND_LABELS = {"private": "私聊", "group": "群聊", "channel": "频道"}
ASSETS = Path(__file__).resolve().parent / "assets"
# Quiet paper-plane and chat doodles in Telegram blue under the lead and the list.
DOODLE_PATH = ASSETS / "doodles" / "messages.png"
# Each story's source line starts with its chat's kind as a generated icon (megaphone for a
# channel, people for a group, a speech bubble for a private chat); a story scored this
# high carries the red 重要 mark before its headline.
IMPORTANT_SCORE = 80
DEFAULT_REFRESH_LABEL = "每次上屏前刷新"
# Saved before this page existed; they now read as the Chinese default.
LEGACY_REFRESH_LABELS = {"refresh on display", "下一次刷新 轮到展示时"}
DAY_TAG_COLOURS = {
    "时事": (196, 40, 40), "财经": (0, 122, 82), "科技": (24, 98, 196), "游戏": (214, 88, 12),
    "体育": (0, 128, 140), "本地": (176, 112, 0), "生活": (150, 72, 150), "娱乐": (200, 50, 120),
    "私聊": (112, 66, 182), "群聊": (0, 128, 140), "其他": (90, 98, 108),
}
NIGHT_TAG_COLOURS = {
    "时事": (255, 120, 110), "财经": (96, 214, 160), "科技": (120, 182, 255), "游戏": (255, 170, 96),
    "体育": (96, 214, 220), "本地": (240, 196, 92), "生活": (226, 150, 226), "娱乐": (255, 140, 190),
    "私聊": (196, 160, 255), "群聊": (96, 214, 220), "其他": (178, 184, 192),
}


@lru_cache(maxsize=12)
def _page_icon(name, size):
    path = ASSETS / f"{name}.png"
    if not path.is_file():
        return None
    try:
        with Image.open(path) as source:
            return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    except OSError:
        return None


@lru_cache(maxsize=32)
def _chat_avatar(path, size):
    """A chat's cached picture as a ``size`` px disc (RGBA), or None when it cannot be read."""
    if not path:
        return None
    try:
        with Image.open(path) as source:
            square = ImageOps.fit(source.convert("RGB"), (size * 4, size * 4), Image.Resampling.LANCZOS)
    except OSError:
        return None
    mask = Image.new("L", square.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, square.width - 1, square.height - 1), fill=255)
    square.putalpha(mask)
    return square.resize((size, size), Image.Resampling.LANCZOS)


class BriefingPageMixin:
    """Draws the page; the plugin supplies palette, fonts, text fitting and media helpers."""

    def _render_page(self, dimensions, payload, settings, now):
        width, height = dimensions
        self._sx, self._sy = width / 800, height / 480
        scale = max(0.72, min(self._sx, self._sy))
        p = self._palette((settings or {}).get("_inkypi_theme"))
        image = Image.new("RGB", dimensions, p["background"])
        draw = ImageDraw.Draw(image)
        self._page_scale = scale
        messages = payload.get("messages") or []
        lead, rest = self._split_page_lead(messages)
        self._draw_doodles(image, p)
        drawn = []
        if lead:
            promoted = self._draw_lead(image, draw, lead, p, rest)
            drawn.append(lead)
            if promoted:
                # The stories promoted under the lead leave the list, which shows as many more instead.
                drawn.extend(promoted)
                rest = [item for item in rest if all(item is not story for story in promoted)]
            drawn.extend(self._draw_story_list(image, draw, rest[:LIST_ROWS], p))
        else:
            self._draw_empty_page(draw, p)
        payload["_rendered_visible_keys"] = self._message_keys_for_items(drawn)
        self._draw_header(image, draw, payload, p, now, len(drawn))
        self._draw_footer(draw, payload, settings or {}, p)
        return image

    # --- geometry -------------------------------------------------------------

    def _x(self, value):
        return int(round(value * self._sx))

    def _y(self, value):
        return int(round(value * self._sy))

    def _f(self, size, weight="normal"):
        return self._font(max(8, int(round(size * self._page_scale))), weight)

    def _draw_doodles(self, image, p):
        """Lay the doodles under the lead and the list before either is drawn."""
        for box in ((self._x(14), self._y(56), self._x(470), self._y(446)),
                    (self._x(486), self._y(50), self._x(786), self._y(446))):
            paint_doodles(image, box, DOODLE_PATH, p["blue"], offset=box[:2])

    # --- header and footer ------------------------------------------------------

    def _draw_header(self, image, draw, payload, p, now, visible_count):
        if not self._draw_title_asset(image, (self._x(14), self._y(4), self._x(344), self._y(40))):
            label = payload.get("channel_label") or "Telegram Digest"
            draw.text((self._x(16), self._y(9)), self._fit_text(draw, label, self._f(25, "bold"), self._x(320)),
                      font=self._f(25, "bold"), fill=p["ink"])
        stats = payload.get("stats") or {}
        chip_font = self._f(12, "bold")
        right = self._x(786)
        chips = [(f"精选 {visible_count}", p["blue"])]
        blocked = int(stats.get("blocked_count") or 0)
        if blocked:
            chips.append((f"拦截广告 {blocked}", p["red"]))
        for text, fill in reversed(chips):
            right = self._chip(draw, right, self._y(12), text, fill, chip_font, align_right=True) - self._x(6)
        clock = self._format_clock((payload.get("status") or {}).get("generated_at"), now)
        clock_font = self._f(13, "bold")
        draw.text((right - self._text_width(draw, clock, clock_font) - self._x(4), self._y(14)), clock,
                  font=clock_font, fill=p["muted"])
        draw.line((self._x(14), self._y(46), self._x(786), self._y(46)), fill=p["ink"], width=max(1, self._y(2)))

    def _draw_footer(self, draw, payload, settings, p):
        stats = payload.get("stats") or {}
        status = payload.get("status") or {}
        draw.line((self._x(14), self._y(455), self._x(786), self._y(455)), fill=p["rule"], width=1)
        parts = [self._curation_label(stats, status)]
        if status.get("account_api") or stats.get("dialog_count"):
            parts.append(f"{int(stats.get('dialog_count') or 0)} 个未读对话")
        if stats.get("scanned_count"):
            parts.append(f"扫描 {int(stats.get('scanned_count') or 0)} 条")
        blocked = int(stats.get("blocked_count") or 0)
        if blocked:
            chats = "、".join(stats.get("blocked_chats") or [])
            parts.append(f"拦截广告 {blocked} 条" + (f"（{chats}…）" if chats else ""))
        font = self._f(11, "bold")
        refresh = str(settings.get("footerRefreshLabel") or "").strip()
        if not refresh or refresh.casefold() in LEGACY_REFRESH_LABELS:
            refresh = DEFAULT_REFRESH_LABEL
        refresh_w = self._text_width(draw, refresh, font)
        draw.text((self._x(786) - refresh_w, self._y(460)), refresh, font=font, fill=p["muted"])
        text = self._fit_text(draw, " · ".join(parts), font, self._x(786) - refresh_w - self._x(30))
        draw.text((self._x(14), self._y(460)), text, font=font, fill=p["muted"])

    @staticmethod
    def _curation_label(stats, status):
        source = str(status.get("source_state") or "")
        if source in {"sample", "missing_token", "missing_account"}:
            return "示例内容"
        if source == "cache":
            return "缓存内容"
        mode = str(stats.get("curation") or "")
        if mode == "ai":
            return "AI 精选"
        if mode == "off":
            return "未过滤"
        return "规则排序" + ("（AI 暂不可用）" if status.get("ai_error") else "")

    # --- lead -------------------------------------------------------------------

    def _draw_lead(self, image, draw, item, p, rest=()):
        """Draw the lead; returns the stories promoted into the room its text leaves."""
        x0, y0, x1, y1 = self._x(14), self._y(56), self._x(470), self._y(446)
        picture = self._open_page_media(item)
        colour = self._tag_colour(item, p)
        if picture is not None and picture.height > picture.width * PORTRAIT_RATIO:
            sizes = (22, 3, 10)
            split = x0 + self._x(PORTRAIT_PHOTO_W)
            text_box = (split + self._x(14), y0, x1, y1)
            second = self._fitting_second(item, p, text_box, rest, sizes)
            if second is None:
                # No picture to put under the text: the photo widens into the room instead.
                split = self._portrait_split(item, p, (x0, y0, x1, y1), sizes)
                if split is None:
                    self._draw_full_bleed_lead(image, draw, item, picture, (x0, y0, x1, y1), p, colour)
                    return []
                text_box = (split + self._x(14), y0, x1, y1)
            box = (x0, y0, split, y1)
            self._paste_media(image, picture, box, p, centering=(0.5, 0.15))
            self._draw_media_badges(draw, item, box, p, colour)
            return self._draw_lead_and_second(image, draw, item, p, text_box, second, sizes)
        if picture is not None:
            box = (x0, y0, x1, y0 + self._y(214))
            self._paste_media(image, picture, box, p, featured=True)
            self._draw_media_badges(draw, item, box, p, colour)
            self._draw_lead_text(image, draw, item, p, (x0, box[3] + self._y(10), x1, y1), 24, 2, 3, show_tag=False)
            return []
        sizes = (30, 3, 9)
        return self._draw_lead_and_second(image, draw, item, p, (x0, y0, x1, y1),
                                          self._fitting_second(item, p, (x0, y0, x1, y1), rest, sizes), sizes)

    def _fitting_second(self, item, p, box, rest, sizes):
        """(the second picture, whether it is the lead's own) when the lead's text in ``box``
        leaves room enough for it, else None."""
        second, same_story = self._second_picture(item, rest)
        if second is None:
            return None
        end = self._lead_text_end(item, p, box, sizes[0], sizes[1], SECOND_LEAD_SUMMARY_LINES)
        return (second, same_story) if box[3] - end >= self._y(SECOND_MIN_H) else None

    def _lead_text_end(self, item, p, box, headline_size, headline_lines, summary_lines):
        scratch = Image.new("RGB", (self._x(800), self._y(480)))
        return self._draw_lead_text(scratch, ImageDraw.Draw(scratch), item, p, box, headline_size, headline_lines,
                                    summary_lines, meta_after=True)

    def _portrait_split(self, item, p, box, sizes):
        """Where a portrait lead's photo ends when nothing fills the room under its text: the
        widest text column whose text runs down to the column's foot, or None when even the
        narrowest column leaves room."""
        x0, y0, x1, y1 = box
        widest = x0 + self._x(PORTRAIT_PHOTO_W)
        narrowest = x1 - self._x(PORTRAIT_TEXT_MIN_W) - self._x(14)
        step = max(1, self._x(8))
        for split in range(widest, narrowest + 1, step):
            end = self._lead_text_end(item, p, (split + self._x(14), y0, x1, y1), *sizes)
            if y1 - end <= self._y(PORTRAIT_FILL_SLACK):
                return split
        return None

    def _draw_full_bleed_lead(self, image, draw, item, picture, box, p, colour):
        """A lead whose text is short: its photo fills the lead, and its tag, headline, summary
        and source line lie on a dark band rising from the photo's foot."""
        x0, y0, x1, y1 = [int(value) for value in box]
        self._paste_media(image, picture, box, p, centering=(0.5, 0.3))
        self._draw_media_badges(draw, item, box, p, colour)
        width = x1 - x0 - self._x(28)
        headline_font, summary_font = self._f(24, "bold"), self._f(15)
        headline = self._wrap_text(draw, display_headline(item), headline_font, width, 2)
        summary = self._wrap_text(draw, display_summary(item), summary_font, width, 3)
        headline_h, summary_h = int(24 * 1.3 * self._page_scale), int(15 * 1.45 * self._page_scale)
        text_h = len(headline) * headline_h + len(summary) * summary_h + self._y(30)
        band = min(y1 - y0, text_h + self._y(56))
        shade = Image.new("L", (1, band), 0)
        for row in range(band):
            shade.putpixel((0, row), round(225 * min(1.0, row / max(1, band - text_h))))
        shade = shade.resize((x1 - x0, band))
        corner = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(corner).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius=self._x(8), fill=255)
        image.paste((10, 14, 20), (x0, y1 - band, x1, y1),
                    ImageChops.multiply(shade, corner.crop((0, y1 - y0 - band, x1 - x0, y1 - y0))))
        x, y = x0 + self._x(14), y1 - text_h
        for line in headline:
            draw.text((x, y), line, font=headline_font, fill=(255, 255, 255))
            y += headline_h
        for line in summary:
            draw.text((x, y + self._y(2)), line, font=summary_font, fill=(236, 240, 244))
            y += summary_h
        meta_y = y1 - self._y(24)
        meta_x = self._paste_marks(image, item, x, meta_y, self._x(16))
        meta_font = self._f(12, "bold")
        draw.text((meta_x, meta_y), self._fit_text(draw, self._story_meta(item), meta_font,
                                                   x1 - self._x(50) - meta_x), font=meta_font, fill=(214, 220, 226))
        self._score_dots(draw, x1 - self._x(46), meta_y + self._y(8), item, colour, p)

    def _draw_lead_and_second(self, image, draw, item, p, box, second, sizes):
        """The lead's text in ``box``; with a ``second`` picture, the lead's source line closes
        its own text and the picture fills the room below: another photo of the lead's own
        album, so the text describes both, or the next picture story, carrying its own headline
        and summary. Returns the stories promoted there."""
        if second is None:
            self._draw_lead_text(image, draw, item, p, box, *sizes)
            return []
        story, same_story = second
        end = self._draw_lead_text(image, draw, item, p, box, sizes[0], sizes[1], SECOND_LEAD_SUMMARY_LINES,
                                   meta_after=True)
        self._draw_second_feature(image, draw, story, (box[0], end + self._y(10), box[2], box[3]), p,
                                  same_story=same_story)
        return [story]

    def _second_picture(self, lead, rest):
        """(the picture for the lead's empty half, whether it is the lead's own) or (None, False):
        a picture chat's best picture first (the owner wants those big), then the lead's album,
        then the next picture story that says something, then any picture story at all."""
        group, chat = str(lead.get("grouped_id") or ""), str(lead.get("chat_id") or "")

        def own(story):
            return bool(group) and str(story.get("grouped_id") or "") == group and str(story.get("chat_id") or "") == chat

        def shown(story):
            return self._open_page_media(story) is not None

        for story in rest:
            if feature_picture(story) and shown(story):
                return story, own(story)
        for story in rest:
            if own(story) and shown(story):
                return story, True
        for story in rest:
            if str(story.get("raw_text") or "").strip() and shown(story):
                return story, False
        for story in rest:
            if shown(story):
                return story, False
        return None, False

    def _draw_second_feature(self, image, draw, item, box, p, same_story=False):
        """The second picture. The lead's own album photo carries only a chip (the text above is
        its story); another story's picture carries its topic, headline, summary and source on a
        dark band along its foot."""
        x0, y0, x1, y1 = [int(value) for value in box]
        picture = self._open_page_media(item)
        if picture is None:
            return
        centering = (0.5, 0.2) if picture.height > picture.width * PORTRAIT_RATIO else (0.5, 0.45)
        self._paste_media(image, picture, box, p, centering=centering)
        if same_story:
            self._chip(draw, x0 + self._x(10), y0 + self._y(10), "同组图片", (24, 28, 34), self._f(12, "bold"))
            return
        summary = display_summary(item)
        band = self._y(66 if summary else 48)
        shade = Image.new("L", (1, band), 0)
        for row in range(band):
            shade.putpixel((0, row), round(215 * min(1.0, row / max(1, band * 0.55))))
        shade = shade.resize((x1 - x0, band))
        corner = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(corner).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius=self._x(8), fill=255)
        mask = ImageChops.multiply(shade, corner.crop((0, y1 - y0 - band, x1 - x0, y1 - y0)))
        image.paste((10, 14, 20), (x0, y1 - band, x1, y1), mask)
        colour = self._tag_colour(item, p)
        self._draw_media_badges(draw, item, box, p, colour)
        headline_font = self._f(15, "bold")
        draw.text((x0 + self._x(10), y1 - self._y(58 if summary else 40)),
                  self._fit_text(draw, display_headline(item), headline_font, x1 - x0 - self._x(20)),
                  font=headline_font, fill=(255, 255, 255))
        if summary:
            summary_font = self._f(12)
            draw.text((x0 + self._x(10), y1 - self._y(37)),
                      self._fit_text(draw, summary, summary_font, x1 - x0 - self._x(20)), font=summary_font,
                      fill=(236, 240, 244))
        meta_font = self._f(11, "bold")
        draw.text((x0 + self._x(10), y1 - self._y(19)),
                  self._fit_text(draw, self._story_meta(item), meta_font, x1 - x0 - self._x(20)),
                  font=meta_font, fill=(214, 220, 226))

    def _draw_lead_text(self, image, draw, item, p, box, headline_size, headline_lines, summary_lines, show_tag=True,
                        meta_after=False):
        """The lead's tag, headline, summary and source line; returns where its text ends.

        The source line closes the box, or follows the summary with ``meta_after``."""
        x0, y0, x1, y1 = box
        colour = self._tag_colour(item, p)
        y = y0
        headline_font = self._f(headline_size, "bold")
        # The tag leads the headline's first line rather than standing on a line of its own,
        # so the room it took goes to the summary.
        tag_font = self._f(12, "bold")
        indent = self._text_width(draw, self._tag(item), tag_font) + self._x(7) * 2 + self._x(8) if show_tag else 0
        lines = self._wrap_text(draw, display_headline(item), headline_font, x1 - x0, headline_lines, first_indent=indent)
        for index, line in enumerate(lines):
            left = x0 + (indent if index == 0 else 0)
            if index == 0 and show_tag:
                top, bottom = draw.textbbox((left, y), line or "国", font=headline_font)[1::2]
                self._chip(draw, x0, round((top + bottom) / 2 - self._y(10)), self._tag(item), colour, tag_font)
            draw.text((left, y), line, font=headline_font, fill=p["ink"])
            y += int(headline_size * 1.3 * self._page_scale)
        body_size = 17 if headline_size >= 30 else 15
        body_font = self._f(body_size, "normal")
        line_h = int(body_size * 1.45 * self._page_scale)
        meta_y = y1 - self._y(16)
        room = max(0, (meta_y - self._y(8) - y) // max(1, line_h))
        extra = max(0, headline_lines - len(lines))
        for line in self._wrap_text(draw, display_summary(item), body_font, x1 - x0,
                                    min(room, summary_lines + extra)):
            draw.text((x0, y + self._y(4)), line, font=body_font, fill=p["ink"])
            y += line_h
        if meta_after:
            meta_y = y + self._y(10)
        else:
            # A short AI summary leaves room above the source line: the message's own words fill it.
            note_font = self._f(body_size - 2, "normal")
            note_h = int((body_size - 2) * 1.5 * self._page_scale)
            left = max(0, (meta_y - self._y(6) - (y + self._y(8))) // max(1, note_h))
            excerpt = self._clean_text(original_excerpt(item))
            for line in self._wrap_text(draw, f"原文 · {excerpt}", note_font, x1 - x0, left) if excerpt and left else ():
                draw.text((x0, y + self._y(8)), line, font=note_font, fill=p["muted"])
                y += note_h
        meta = self._story_meta(item)
        meta_font = self._f(12, "bold")
        meta_x = self._paste_marks(image, item, x0, meta_y, self._x(16))
        draw.text((meta_x, meta_y), self._fit_text(draw, meta, meta_font, x1 - meta_x - self._x(44)), font=meta_font,
                  fill=p["muted"])
        self._score_dots(draw, x1 - self._x(32), meta_y + self._y(8), item, colour, p)
        return meta_y + self._y(18)

    def _draw_media_badges(self, draw, item, box, p, colour):
        self._chip(draw, box[0] + self._x(10), box[1] + self._y(10), self._tag(item), colour, self._f(12, "bold"))
        if item.get("media_kind") == "video" and item.get("duration"):
            label = self._duration_label(item.get("duration"))
            self._chip(draw, box[2] - self._x(10), box[1] + self._y(10), label, (24, 28, 34),
                       self._f(12, "bold"), align_right=True)

    def _draw_empty_page(self, draw, p):
        x, y = self._x(14), self._y(150)
        draw.text((x, y), "没有新的未读消息", font=self._f(30, "bold"), fill=p["ink"])
        draw.text((x, y + self._y(48)), "广告已过滤；下次上屏前会再查一次。", font=self._f(16), fill=p["muted"])

    # --- list -------------------------------------------------------------------

    def _draw_story_list(self, image, draw, items, p):
        x0, x1 = self._x(486), self._x(786)
        draw.text((x0, self._y(52)), "更多要闻", font=self._f(13, "bold"), fill=p["ink"])
        label_w = self._text_width(draw, "更多要闻", self._f(13, "bold"))
        draw.line((x0 + label_w + self._x(8), self._y(61), x1, self._y(61)), fill=p["rule"], width=1)
        if not items:
            draw.text((x0, self._y(80)), "其余未读都已看过或被过滤", font=self._f(12), fill=p["muted"])
            return []
        row_h = self._y(64)
        y = self._y(72)
        drawn = []
        for index, item in enumerate(items):
            if index:
                draw.line((x0, y - self._y(4), x1, y - self._y(4)), fill=p["rule"], width=1)
            self._draw_story_row(image, draw, item, (x0, y, x1, y + row_h - self._y(8)), p)
            drawn.append(item)
            y += row_h
        return drawn

    def _draw_story_row(self, image, draw, item, box, p):
        """One list row. A story's picture comes before its words: it fills the row's right end
        from top to foot, and the words keep to its left. The first line names the story at a
        glance: the topic on a small pill in the topic's colour, the chat's picture and name, and
        the age set flush right against the picture (the row's end without one). The headline
        follows on up to two lines; one that fits on one line leaves the second to the summary."""
        x0, y0, x1, y1 = box
        text_top = y0 + self._y(ROW_TEXT_TOP)
        picture = self._open_page_media(item)
        text_right = x1
        if picture is not None:
            thumb = (x1 - self._x(ROW_THUMB_W), y0, x1, y1)
            centering = (0.5, 0.15) if picture.height > picture.width * PORTRAIT_RATIO else (0.5, 0.5)
            self._paste_media(image, picture, thumb, p, centering=centering, radius=5)
            text_right = thumb[0] - self._x(8)
        headline_font = self._f(15, "bold")
        indent = 0
        if self._important(item):
            icon_w = self._paste_icon(image, "important", x0, text_top + self._y(1), self._x(17))
            indent = icon_w + self._x(5) if icon_w else 0
        lines = self._row_headline(draw, display_headline(item), headline_font, text_right - x0, indent)
        for index, line in enumerate(lines):
            draw.text((x0 + (0 if index else indent), text_top + index * self._y(19)), line, font=headline_font,
                      fill=p["ink"])
        summary = display_summary(item)
        if summary and len(lines) < 2:
            body_font = self._f(12)
            draw.text((x0, text_top + self._y(21)), self._fit_text(draw, summary, body_font, text_right - x0),
                      font=body_font, fill=p["ink"])
        # Small text stays ink: the topic's colour is the pill's fill, the age's weight sets it apart.
        age, age_font = self._relative_time(item.get("date")), self._f(11, "bold")
        age_x = text_right - self._text_width(draw, age, age_font)
        draw.text((age_x, y0 + self._y(1)), age, font=age_font, fill=p["ink"])
        x = self._row_tag(draw, x0, y0, self._tag(item), self._tag_colour(item, p), p) + self._x(7)
        icon_w = self._paste_source_mark(image, item, x, y0 + self._y(1), self._x(14))
        x += icon_w + self._x(4) if icon_w else 0
        name_font = self._f(11)
        draw.text((x, y0 + self._y(1)), self._fit_text(draw, self._chat_name(item), name_font, age_x - self._x(10) - x),
                  font=name_font, fill=p["ink"])

    def _row_headline(self, draw, text, font, width, indent):
        """A row's headline on at most two lines. A second line of only a character or two is
        evened out: the first line gives up characters until the second holds a few."""
        lines = self._wrap_text(draw, text, font, width, 2, first_indent=indent)
        if len(lines) < 2 or self._text_width(draw, lines[1], font) >= width * ROW_SHORT_LINE:
            return lines
        whole = "".join(lines).replace(" ", "")
        step = max(1, int(self._text_width(draw, "国", font)))
        for narrower in range(int(width) - step, int(width * 0.6), -step):
            even = self._wrap_text(draw, text, font, narrower, 2, first_indent=indent)
            if "".join(even).replace(" ", "") != whole:
                break
            lines = even
            if self._text_width(draw, even[1], font) >= width * ROW_SHORT_LINE:
                break
        return lines

    def _row_tag(self, draw, x, y, text, fill, p):
        """A list row's topic on a pill of the topic's colour; returns the pill's right edge.

        The word takes whichever of white and the page's darkest tone reads on the fill: the
        night page's topic colours are light."""
        font = self._f(11, "bold")
        pad, height = self._x(6), self._y(16)
        width = self._text_width(draw, text, font) + pad * 2
        draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=fill)
        light_fill = fill[0] * 299 + fill[1] * 587 + fill[2] * 114 > 150_000
        dark = min(p["ink"], p["background"], key=sum)
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((x + pad, y + (height - (bbox[1] + bbox[3])) / 2), text, font=font,
                  fill=dark if light_fill else (255, 255, 255))
        return x + width

    # --- pieces -------------------------------------------------------------------

    def _paste_icon(self, image, name, x, y, size):
        """Paste a page icon; returns its width (0 when the asset is missing)."""
        icon = _page_icon(name, max(8, int(size)))
        if icon is None:
            return 0
        image.paste(icon, (int(x), int(y)), icon)
        return icon.width

    def _paste_source_mark(self, image, item, x, y, size):
        """The chat's own picture as a disc when it is cached (a group's, a channel's), else its
        kind's icon; returns its width."""
        avatar = _chat_avatar(str(item.get("chat_avatar") or ""), max(8, int(size)))
        if avatar is None:
            return self._paste_icon(image, self._kind_icon(item), x, y, size)
        image.paste(avatar, (int(x), int(y)), avatar)
        return avatar.width

    def _paste_marks(self, image, item, x, y, size):
        """The chat's picture (or kind icon), then the 重要 mark when it applies; returns where the
        text goes."""
        width = self._paste_source_mark(image, item, x, y, size)
        x += width + self._x(4) if width else 0
        if self._important(item):
            width = self._paste_icon(image, "important", x, y, size)
            x += width + self._x(4) if width else 0
        return x

    @staticmethod
    def _kind_icon(item):
        kind = str(item.get("chat_kind") or "")
        return f"kind_{kind}" if kind in KIND_LABELS else "kind_channel"

    @staticmethod
    def _important(item):
        return int((item.get("curation") or {}).get("score") or 0) >= IMPORTANT_SCORE

    def _chip(self, draw, x, y, text, fill, font, align_right=False):
        pad = self._x(7)
        height = self._y(20)
        width = self._text_width(draw, text, font) + pad * 2
        left = x - width if align_right else x
        draw.rounded_rectangle((left, y, left + width, y + height), radius=height // 2, fill=fill)
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((left + pad, y + (height - (bbox[1] + bbox[3])) / 2), text, font=font, fill=(255, 255, 255))
        return left if align_right else left + width

    def _score_dots(self, draw, x, y, item, colour, p):
        score = int((item.get("curation") or {}).get("score") or 0)
        if score <= 0:
            return
        filled = max(1, min(5, round(score / 20)))
        radius = max(2, self._x(2.5))
        for index in range(5):
            cx = x + index * self._x(7)
            draw.ellipse((cx - radius, y - radius, cx + radius, y + radius), fill=colour if index < filled else p["rule"])

    @staticmethod
    def _chat_name(item):
        return str(item.get("chat_title") or item.get("chat_username") or "").strip()

    def _story_meta(self, item):
        """The lead's source line: its chat, the chat's kind and the story's age."""
        parts = [self._chat_name(item), KIND_LABELS.get(str(item.get("chat_kind") or "")),
                 self._relative_time(item.get("date"))]
        return " · ".join(part for part in parts if part)

    @staticmethod
    def _tag(item):
        tag = (item.get("curation") or {}).get("tag")
        if tag:
            return tag
        return KIND_LABELS.get(str(item.get("chat_kind") or ""), "频道")

    def _tag_colour(self, item, p):
        return p["tags"].get(self._tag(item), p["tags"]["其他"])

    def _open_page_media(self, item):
        if not self._media_path_exists(item):
            return None
        try:
            with Image.open(Path(str(item.get("media_path")))) as source:
                return ImageOps.exif_transpose(source).convert("RGB")
        except Exception:  # noqa: BLE001 - a broken cache file just drops the picture.
            return None

    def _paste_media(self, image, picture, box, p, featured=False, centering=(0.5, 0.5), radius=8):
        x0, y0, x1, y1 = [int(value) for value in box]
        size = (max(1, x1 - x0), max(1, y1 - y0))
        if featured:
            tile = self._fit_media_image(picture, size, featured=True)
        else:
            tile = ImageOps.fit(picture, size, method=Image.Resampling.LANCZOS, centering=centering)
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=self._x(radius), fill=255)
        image.paste(tile, (x0, y0), mask)
