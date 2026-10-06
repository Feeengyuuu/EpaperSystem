"""Swiss-grid daily image page with measured, complete text and contained media."""
from __future__ import annotations

from datetime import datetime
import math
import re

from PIL import Image, ImageDraw, ImageOps


RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
MIN_BODY_FONT_SIZE = 13


class LayoutOverflowError(ValueError):
    """The complete source content cannot fit at the minimum readable size."""


def _text(value):
    return " ".join(str(value or "").split())


def _source_date(payload, now):
    try:
        return datetime.strptime(str(payload.get("date", "")), "%Y-%m-%d")
    except (TypeError, ValueError):
        return now


def _history_image_index(events, payload):
    year = _text(payload.get("history_image_year"))
    supplied_index = payload.get("history_image_event_index")
    if supplied_index is not None:
        if type(supplied_index) is not int or not 0 <= supplied_index < len(events):
            raise LayoutOverflowError("History image event index is invalid.")
        if not year or _text(events[supplied_index].get("year")) != year:
            raise LayoutOverflowError("History image event index does not match its source year.")
        return supplied_index
    matching = [index for index, event in enumerate(events) if _text(event.get("year")) == year]
    if year and len(matching) == 1:
        return matching[0]
    title = _text(payload.get("history_image_title"))
    titled = [index for index in matching if title and title in _text(events[index].get("text"))]
    if year and len(titled) == 1:
        return titled[0]
    raise LayoutOverflowError("History image has no unambiguous matching event; refusing to attach it to a different event.")


class _Page:
    def __init__(self, plugin, dimensions, payload, settings, now):
        self.plugin = plugin
        self.width, self.height = map(int, dimensions)
        if self.width < 480 or self.height < 280:
            raise LayoutOverflowError("Swiss grid requires at least 480 x 280 pixels.")
        self.scale = min(self.width / 800, self.height / 480)
        self.payload = payload
        self.settings = settings
        self.date = _source_date(payload, now)
        self.cjk = plugin._language_is_cjk(payload.get("language"))
        self.family = plugin._resolved_font_family(settings)
        self.palette = plugin._palette(settings)
        self.image = Image.new("RGB", (self.width, self.height), self.palette["background"])
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}
        self.margin = self.px(18)
        self.rule_y = self.px(70)
        self.top = self.px(83)
        self.bottom = self.height - self.px(16)
        self.rail_right = self.px(120)
        self.photo_left = self.rail_right + self.px(14)
        self.divider = round(self.width * 0.564)
        self.photo_right = self.divider - self.px(13)
        self.history_left = self.divider + self.px(14)
        self.right = self.width - self.margin
        self.minimum_font = max(MIN_BODY_FONT_SIZE, self.px(MIN_BODY_FONT_SIZE))
        self.audit = {
            "dimensions": [self.width, self.height],
            "mode": "night" if sum(self.palette["background"]) < 100 else "day",
            "date": self.date.strftime("%Y-%m-%d"),
            "min_body_font_size": self.minimum_font,
            "text_complete": True,
            "complete": True,
            "media_requested": {"daily_image": False, "history_image": False},
            "media_available": {"daily_image": False, "history_image": False},
            "omitted_events": [],
            "events": [],
            "regions": {
                "date_rail": {"bounds": [self.margin, self.top, self.rail_right, self.bottom]},
                "daily_image_column": {"bounds": [self.photo_left, self.top, self.photo_right, self.bottom]},
                "history": {"bounds": [self.history_left, self.top, self.right, self.bottom]},
            },
            "header": {},
        }

    def px(self, value):
        return max(1, round(value * self.scale))

    def font(self, text, size, bold=False):
        family = "__cjk__" if re.search(r"[\u3400-\u9fff]", text) else self.family
        key = (family, size, bold)
        if key not in self.fonts:
            self.fonts[key] = self.plugin._font(family, size, "bold" if bold else "normal")
        return self.fonts[key]

    def width_of(self, text, font):
        box = self.draw.textbbox((0, 0), text, font=font)
        return max(self.draw.textlength(text, font=font), box[2] - box[0])

    def glyph_height(self, text, font):
        box = self.draw.textbbox((0, 0), text or "Ag", font=font)
        return box[3] - box[1]

    def line_height(self, text, font):
        sample = "国Ag" if re.search(r"[\u3400-\u9fff]", text) else "Ag"
        return max(self.glyph_height(sample, font), getattr(font, "size", 13)) + self.px(2)

    def wrap(self, text, font, width_for_line):
        """Return contiguous slices; joining them reproduces the entire input."""
        lines = []
        cursor = 0
        while cursor < len(text):
            limit = width_for_line(len(lines))
            end = cursor
            while end < len(text) and self.width_of(text[cursor:end + 1], font) <= limit:
                end += 1
            if end == cursor:
                raise LayoutOverflowError("A source character exceeds its available text column.")
            if end < len(text):
                # Prefer a word boundary, but split long tokens rather than overflow.
                space = text.rfind(" ", cursor, end)
                if space > cursor and space - cursor >= (end - cursor) * 0.5:
                    end = space + 1
                token = r"[A-Za-z0-9\u00c0-\u024f_'.:/@%+\-]"
                if re.match(token, text[end]) and re.match(token, text[end - 1]):
                    start = end - 1
                    while start > cursor and re.match(token, text[start - 1]):
                        start -= 1
                    if start > cursor:
                        end = start
                # Keep closing punctuation with its preceding glyph, and an opening
                # mark with what follows. These are break changes, never deletions.
                closing = "，。！？；：、）】》」』”’…,.!?;:%)]}"
                opening = "（【《「『“‘([{"
                while end - cursor > 1 and text[end] in closing:
                    end -= 1
                while end - cursor > 1 and text[end - 1] in opening:
                    end -= 1
            lines.append(text[cursor:end])
            cursor = end
        return lines

    def ink(self, text, x, y, font, color=None):
        box = self.draw.textbbox((0, 0), text, font=font)
        width, height = box[2] - box[0], box[3] - box[1]
        if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise LayoutOverflowError("A measured text glyph would exceed the page bounds.")
        self.draw.text((x - box[0], y - box[1]), text, font=font,
                       fill=color or self.palette["ink"])
        return {"text": text, "bounds": [x, y, x + width, y + height]}

    def contained_image(self, source, box):
        x1, y1, x2, y2 = box
        fitted = ImageOps.contain(source, (x2 - x1, y2 - y1), method=RESAMPLE)
        x = x1 + (x2 - x1 - fitted.width) // 2
        y = y1 + (y2 - y1 - fitted.height) // 2
        if fitted.mode in ("RGBA", "LA") or "transparency" in fitted.info:
            fitted = fitted.convert("RGBA")
            self.image.paste(fitted, (x, y), fitted)
        else:
            self.image.paste(fitted.convert("RGB"), (x, y))
        return {"bounds": [x, y, x + fitted.width, y + fitted.height],
                "slot_bounds": list(box), "source_size": list(source.size), "fit": "contain"}

    def header(self):
        title = "每日图片" if self.cjk else "DAILY IMAGE"
        date_text = self.date.strftime("%Y.%m.%d")
        date_font = self.font(date_text, max(self.minimum_font, self.px(17)), bold=True)
        date_width = math.ceil(self.width_of(date_text, date_font))
        date_x = self.right - date_width
        available = date_x - self.margin - self.px(24)
        for size in range(self.px(48), self.minimum_font - 1, -1):
            title_font = self.font(title, size, bold=True)
            if self.width_of(title, title_font) <= available:
                break
        else:
            raise LayoutOverflowError("The page title and source date cannot fit the header.")
        title_y = self.rule_y - self.px(11) - self.glyph_height(title, title_font)
        self.audit["header"]["title"] = self.ink(title, self.margin, title_y, title_font)
        date_y = self.rule_y - self.px(11) - self.glyph_height(date_text, date_font)
        self.audit["header"]["date"] = self.ink(date_text, date_x, date_y, date_font)
        self.draw.line((self.margin, self.rule_y, self.right, self.rule_y),
                       fill=self.palette["ink"], width=self.px(2))
        for x in (self.rail_right, self.divider):
            self.draw.line((x, self.rule_y, x, self.bottom), fill=self.palette["ink"], width=self.px(1))

    def rail(self):
        left, right = self.margin, self.rail_right - self.px(14)
        rail_width = right - left
        months = self.date.strftime("%m")
        days = self.date.strftime("%d")
        for label, value, top in (("month", months, self.top + self.px(6)),
                                  ("day", days, self.top + self.px(183))):
            font = self.font(value, self.px(130), bold=True)
            bbox = self.draw.textbbox((0, 0), value, font=font)
            mask = Image.new("L", (bbox[2] - bbox[0], bbox[3] - bbox[1]), 0)
            ImageDraw.Draw(mask).text((-bbox[0], -bbox[1]), value, font=font, fill=255)
            # Condensed date numerals are a defining part of the selected Swiss grid.
            mask = mask.resize((rail_width, self.px(100)), RESAMPLE)
            self.image.paste(self.palette["ink"], (left, top), mask)
            self.audit["header"][label] = {"text": value, "bounds": [left, top, right, top + mask.height]}
        slash_top = self.top + self.px(122)
        self.draw.polygon(((left + self.px(38), slash_top), (left + self.px(63), slash_top),
                           (left + self.px(40), slash_top + self.px(48)),
                           (left + self.px(15), slash_top + self.px(48))), fill=self.palette["accent"])
        self.draw.rectangle((left, self.bottom - self.px(47), right - 1, self.bottom - self.px(12)),
                            fill=self.palette["accent"])

    def photograph(self, source):
        text = _text(self.payload.get("image_caption") or self.payload.get("daily_image_title")
                     or self.payload.get("title"))
        source_label = self.plugin._source_label(self.payload)
        width = self.photo_right - self.photo_left
        source_font = self.font(source_label, self.minimum_font)
        source_lines = self.wrap(source_label, source_font, lambda _: width)
        source_step = self.line_height(source_label, source_font)
        footer_y = self.bottom - len(source_lines) * source_step
        minimum_image_height = self.px(200)
        for size in range(max(self.minimum_font, self.px(17)), self.minimum_font - 1, -1):
            font = self.font(text, size)
            lines = self.wrap(text, font, lambda _: width)
            step = self.line_height(text, font)
            caption_height = len(lines) * step
            caption_y = footer_y - self.px(9) - caption_height
            photo_bottom = caption_y - self.px(9)
            if photo_bottom - self.top >= minimum_image_height:
                break
        else:
            raise LayoutOverflowError(
                "Complete daily-image caption cannot fit above the minimum photograph height "
                f"at {self.minimum_font}px; no caption text was omitted."
            )
        photo_box = (self.photo_left, self.top, self.photo_right, photo_bottom)
        if source is not None:
            self.audit["regions"]["daily_image"] = self.contained_image(source, photo_box)
        else:
            self.audit["regions"]["daily_image"] = {"bounds": list(photo_box), "fit": "contain",
                                                     "source_size": None, "available": False}
            label = "图片暂不可用" if self.cjk else "Image unavailable"
            placeholder_font = self.font(label, self.minimum_font)
            self.ink(label, self.photo_left, self.top + self.px(16), placeholder_font,
                     self.palette["muted"])
        caption_lines = [self.ink(line, self.photo_left, caption_y + index * step, font)
                         for index, line in enumerate(lines)]
        self.audit["caption"] = {"source_text": text, "lines": caption_lines, "font_size": size,
                                 "bounds": [self.photo_left, caption_y, self.photo_right, footer_y - self.px(9)],
                                 "complete": "".join(lines) == text}
        source_records = [self.ink(line, self.photo_left, footer_y + index * source_step,
                                  source_font, self.palette["muted"])
                          for index, line in enumerate(source_lines)]
        self.audit["source"] = {"source_text": source_label, "lines": source_records, "complete": True}

    def history(self, source):
        enabled = self.plugin._enabled(self.settings.get("showOnThisDay"), default=True)
        raw_events = (self.payload.get("on_this_day") or []) if enabled else []
        if any(not isinstance(event, dict) for event in raw_events):
            raise LayoutOverflowError("History content must consist of complete event records.")
        title = "历史上的今天" if self.cjk else "ON THIS DAY"
        title_font = self.font(title, self.px(29), bold=True)
        title_x = self.history_left + self.px(36)
        while self.width_of(title, title_font) > self.right - title_x:
            size = getattr(title_font, "size", self.px(29)) - 1
            if size < self.minimum_font:
                raise LayoutOverflowError("The complete history heading cannot fit.")
            title_font = self.font(title, size, bold=True)
        self.draw.rectangle((self.history_left, self.top + self.px(2),
                             self.history_left + self.px(25), self.top + self.px(39)),
                            fill=self.palette["accent"])
        self.audit["header"]["history"] = self.ink(title, title_x, self.top + self.px(7), title_font)
        heading_bottom = self.top + self.px(52)
        if self.audit["media_requested"]["history_image"] and source is None:
            label = "配图暂不可用" if self.cjk else "History image unavailable"
            font = self.font(label, self.minimum_font)
            label_y = heading_bottom - self.px(3) - self.glyph_height(label, font)
            title_bottom = self.audit["header"]["history"]["bounds"][3]
            if label_y >= title_bottom + self.px(2) and self.width_of(label, font) <= self.right - title_x:
                self.audit["regions"]["history_image_status"] = self.ink(
                    label, title_x, label_y, font, self.palette["muted"]
                )
        self.draw.line((self.history_left, heading_bottom, self.right, heading_bottom),
                       fill=self.palette["ink"], width=self.px(1))
        body_top = heading_bottom + self.px(10)
        available = self.bottom - body_top
        if not raw_events:
            label = "暂无历史条目" if enabled and self.cjk else "No history entries" if enabled else ""
            if label:
                self.ink(label, self.history_left, body_top, self.font(label, self.minimum_font),
                         self.palette["muted"])
            return
        image_index = _history_image_index(raw_events, self.payload) if source is not None else None
        selected = None
        needed = 0
        for size in range(max(self.minimum_font, self.px(17)), self.minimum_font - 1, -1):
            for thumbnail_width in (92, 76, 64):
                rows = self.history_rows(raw_events, source, image_index, size, self.px(thumbnail_width))
                needed = sum(row["height"] for row in rows)
                if needed <= available:
                    selected = rows
                    break
            if selected is not None:
                break
        if selected is None:
            raise LayoutOverflowError(
                f"Complete history needs {needed}px but has {available}px at {self.minimum_font}px; "
                f"all {len(raw_events)} events were retained; no image or text was omitted."
            )
        extra, remainder = divmod(available - needed, len(selected))
        y = body_top
        for index, row in enumerate(selected):
            height = row["height"] + extra + (1 if index < remainder else 0)
            top = y + self.px(4)
            year_text = self.ink(row["year"], self.history_left, top, row["year_font"])
            rule_x = row["text_x"] - self.px(9)
            self.draw.line((rule_x, top, rule_x, top + row["content_height"]),
                           fill=self.palette["rule"], width=self.px(1))
            lines = [self.ink(line, row["text_x"], top + number * row["step"], row["font"])
                     for number, line in enumerate(row["lines"])]
            image_record = None
            if row["image_size"] is not None:
                image_w, image_h = row["image_size"]
                image_record = self.contained_image(source, (self.right - image_w, top, self.right, top + image_h))
                image_record["event_year"] = row["year"]
                image_record["event_index"] = index
            self.audit["events"].append({
                "index": index, "year": row["year"], "year_label": year_text,
                "source_text": row["text"], "lines": lines,
                "bounds": [self.history_left, y, self.right, y + height],
                "font_size": size, "image": image_record,
                "complete": "".join(row["lines"]) == row["text"],
            })
            y += height
            if index < len(selected) - 1:
                self.draw.line((self.history_left, y - self.px(1), self.right, y - self.px(1)),
                               fill=self.palette["ink"], width=self.px(1))

    def history_rows(self, events, source, image_index, font_size, thumbnail_width):
        rows = []
        text_x = self.history_left + self.px(75)
        full_width = self.right - text_x
        for index, event in enumerate(events):
            text, year = _text(event.get("text")), _text(event.get("year"))
            font = self.font(text, font_size)
            step = self.line_height(text, font)
            image_size = None
            if index == image_index:
                image_size = ImageOps.contain(source, (thumbnail_width, self.px(74)), method=RESAMPLE).size
            def line_width(line_index, image_size=image_size, step=step):
                if image_size and line_index * step < image_size[1]:
                    return full_width - image_size[0] - self.px(8)
                return full_width
            lines = self.wrap(text, font, line_width)
            year_font_size = self.px(27)
            year_font = self.font(year, year_font_size, bold=True)
            while self.width_of(year, year_font) > self.px(62):
                year_font_size -= 1
                if year_font_size < self.minimum_font:
                    raise LayoutOverflowError("The complete event year cannot fit its label.")
                year_font = self.font(year, year_font_size, bold=True)
            text_height = (len(lines) - 1) * step + self.glyph_height(lines[-1], font) if lines else 0
            content_height = max(text_height, self.glyph_height(year, year_font),
                                 image_size[1] if image_size else 0)
            rows.append({"text": text, "year": year, "font": font, "year_font": year_font,
                         "lines": lines, "step": step, "text_x": text_x, "image_size": image_size,
                         "content_height": content_height, "height": content_height + self.px(12)})
        return rows


def render_page(plugin, dimensions, payload, settings, now):
    """Render without truncation; preserve the existing plugin's media/cache policy."""
    page = _Page(plugin, dimensions, payload, settings, now)
    daily_image = None
    daily_requested = bool(plugin._enabled(settings.get("showImage"), default=True) and payload.get("image_url"))
    history_requested = bool(plugin._enabled(settings.get("showOnThisDay"), default=True)
                             and payload.get("history_image_url"))
    page.audit["media_requested"] = {"daily_image": daily_requested, "history_image": history_requested}
    if daily_requested:
        daily_image = plugin._download_image(payload["image_url"],
                                             (page.photo_right - page.photo_left, page.bottom - page.top), settings)
    history_image = None
    if history_requested:
        history_image = plugin._download_image(payload["history_image_url"], (page.px(120), page.px(100)), settings)
    page.audit["media_available"] = {"daily_image": daily_image is not None, "history_image": history_image is not None}
    page.header()
    page.rail()
    page.photograph(daily_image)
    page.history(history_image)
    page.audit["text_complete"] = page.audit["caption"]["complete"] and all(
        event["complete"] for event in page.audit["events"]
    )
    page.audit["complete"] = page.audit["text_complete"] and all(
        not requested or page.audit["media_available"][role]
        for role, requested in page.audit["media_requested"].items()
    )
    page.image.info["daily_wiki_layout"] = page.audit
    return page.image
