"""The Telegram Digest page: one lead story beside a ranked list of the rest.

The lead takes a landscape picture across the top, a portrait one (phone
screenshots) down its left side, or none at all; list rows carry a topic bar,
a headline, one summary line, the source and a small thumbnail. Every
coordinate is laid out for 800x480 and scaled to the panel.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from plugins.telegram_digest.curation import VISIBLE_COUNT, display_headline, display_summary

LIST_ROWS = VISIBLE_COUNT - 1
PORTRAIT_RATIO = 1.05
KIND_LABELS = {"private": "私聊", "group": "群聊", "channel": "频道"}
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
        drawn = []
        if lead:
            self._draw_lead(image, draw, lead, p)
            drawn.append(lead)
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

    def _draw_lead(self, image, draw, item, p):
        x0, y0, x1, y1 = self._x(14), self._y(56), self._x(470), self._y(446)
        picture = self._open_page_media(item)
        colour = self._tag_colour(item, p)
        if picture is not None and picture.height > picture.width * PORTRAIT_RATIO:
            box = (x0, y0, x0 + self._x(176), y1)
            self._paste_media(image, picture, box, p, centering=(0.5, 0.15))
            self._draw_media_badges(draw, item, box, p, colour)
            self._draw_lead_text(draw, item, p, (box[2] + self._x(14), y0, x1, y1), 22, 3, 10)
        elif picture is not None:
            box = (x0, y0, x1, y0 + self._y(214))
            self._paste_media(image, picture, box, p, featured=True)
            self._draw_media_badges(draw, item, box, p, colour)
            self._draw_lead_text(draw, item, p, (x0, box[3] + self._y(10), x1, y1), 24, 2, 3, show_tag=False)
        else:
            self._draw_lead_text(draw, item, p, (x0, y0, x1, y1), 30, 3, 9)

    def _draw_lead_text(self, draw, item, p, box, headline_size, headline_lines, summary_lines, show_tag=True):
        x0, y0, x1, y1 = box
        colour = self._tag_colour(item, p)
        y = y0
        if show_tag:
            self._chip(draw, x0, y + self._y(2), self._tag(item), colour, self._f(12, "bold"))
            y += self._y(30)
        headline_font = self._f(headline_size, "bold")
        lines = self._wrap_text(draw, display_headline(item), headline_font, x1 - x0, headline_lines)
        for line in lines:
            draw.text((x0, y), line, font=headline_font, fill=p["ink"])
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
        meta = self._story_meta(item, with_tag=False)
        meta_font = self._f(12, "bold")
        draw.text((x0, meta_y), self._fit_text(draw, meta, meta_font, x1 - x0 - self._x(44)), font=meta_font, fill=p["muted"])
        self._score_dots(draw, x1 - self._x(32), meta_y + self._y(8), item, colour, p)

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
        x0, y0, x1, y1 = box
        colour = self._tag_colour(item, p)
        draw.rectangle((x0, y0 + self._y(2), x0 + max(2, self._x(3)), y1), fill=colour)
        picture = self._open_page_media(item)
        text_x = x0 + self._x(10)
        text_right = x1
        if picture is not None:
            thumb = (x1 - self._x(64), y0 + self._y(2), x1, y1)
            centering = (0.5, 0.15) if picture.height > picture.width * PORTRAIT_RATIO else (0.5, 0.5)
            self._paste_media(image, picture, thumb, p, centering=centering, radius=5)
            text_right = thumb[0] - self._x(8)
        width = text_right - text_x
        headline_font = self._f(15, "bold")
        draw.text((text_x, y0), self._fit_text(draw, display_headline(item), headline_font, width),
                  font=headline_font, fill=p["ink"])
        summary = display_summary(item)
        if summary:
            body_font = self._f(12)
            draw.text((text_x, y0 + self._y(21)), self._fit_text(draw, summary, body_font, width),
                      font=body_font, fill=p["ink"])
        meta_font = self._f(11, "bold")
        draw.text((text_x, y0 + self._y(39)), self._fit_text(draw, self._story_meta(item), meta_font, width),
                  font=meta_font, fill=colour)

    # --- pieces -------------------------------------------------------------------

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

    def _story_meta(self, item, with_tag=True):
        parts = [self._tag(item)] if with_tag else []
        chat = str(item.get("chat_title") or item.get("chat_username") or "").strip()
        if chat:
            parts.append(chat)
        kind = KIND_LABELS.get(str(item.get("chat_kind") or ""))
        if kind and not with_tag:
            parts.append(kind)
        parts.append(self._relative_time(item.get("date")))
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
