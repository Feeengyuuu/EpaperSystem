"""The 整点新闻 page: header, lead band, two news columns and a market strip.

The plugin prepares plain data (lead, sections, quotes, meta); this module only
draws it. Spare column space gets a small globe with that column's sources, and
the lead band carries a topic medallion from ``news_art``.

The page keeps the masthead's two inks: every word in the theme's ink, and the
panel's red only for the lead's 头条 seal, small markers and market gains. Losses
keep the market's green, but only as a small solid mark beside an ink figure.
"""

from __future__ import annotations

from PIL import ImageDraw

from . import news_art

MARGIN = 20
RULE_Y = 56
LEAD_BOX = (64, 146)
LEAD_ART_WIDTH = 170
# A vertical red seal reading 头条 marks the lead story.
SEAL_SIZE = (28, 54)
COLUMN_TOP = 150
COLUMN_GAP = 24
COLUMN_BOTTOM = 390
MARKET_TOP = 396
ITEM_GAP = 8
# The tighter gap a full column uses to fit all five stories or a clipped title's second line.
COMPACT_ITEM_GAP = 4
# A story is its title lines plus a footer row with the tag chip and the one-line fact.
TITLE_LINE = 18
ITEM_FOOTER = 20
# The lead's headline shrinks through these sizes to fit; its two-sentence fact wraps.
LEAD_TITLE_SIZES = (26, 24, 22, 20)
LEAD_FACT_SIZE = 15
LEAD_FACT_LINE = 19
GLOBE_MIN_HEIGHT = 46
WEEKDAYS = "一二三四五六日"


class NewsPage:
    def __init__(self, plugin, image, palette, font_family):
        self.p = plugin
        self.img = image
        self.d = ImageDraw.Draw(image)
        self.c = palette
        self.family = font_family

    # -- text helpers -------------------------------------------------------
    def font(self, size, bold=True):
        return self.p._font(self.family, int(size), "bold" if bold else "normal")

    def width(self, text, size, bold=True):
        return self.p._tw(self.d, str(text), self.font(size, bold))

    def text(self, xy, text, size, bold=True, fill=None, anchor="la"):
        self.d.text(xy, str(text), font=self.font(size, bold), fill=fill or self.c["ink"], anchor=anchor)

    def clip(self, text, size, max_width, bold=True):
        text = str(text or "")
        if self.width(text, size, bold) <= max_width:
            return text
        while text and self.width(text + "…", size, bold) > max_width:
            text = text[:-1]
        return text.rstrip() + "…" if text else ""

    def fit_size(self, text, sizes, max_width, bold=True):
        for size in sizes:
            if self.width(text, size, bold) <= max_width:
                return size
        return sizes[-1]

    def wrap(self, text, size, max_width, max_lines, bold=True):
        text = " ".join(str(text or "").split())
        lines, line = [], ""
        for index, char in enumerate(text):
            if line and self.width(line + char, size, bold) > max_width:
                lines.append(line)
                line = char
                if len(lines) == max_lines:
                    lines[-1] = self.clip(lines[-1] + text[index:], size, max_width, bold)
                    return lines
            else:
                line += char
        if line:
            lines.append(line)
        return lines

    def chip(self, x, y, text, colour, size=11, solid=True):
        height = size + 8
        width = self.width(text, size) + 14
        box = (x, y, x + width, y + height)
        if solid:
            self.d.rounded_rectangle(box, radius=height // 2, fill=colour)
            ink = self.p._contrast_ink(colour, self.c)
        else:
            self.d.rounded_rectangle(box, radius=height // 2, outline=colour, width=1)
            ink = self.c["ink"]
        self.text((x + 7, y + height / 2), text, size, True, ink, "lm")
        return width

    # -- page ---------------------------------------------------------------
    def render(self, *, title, lead, mainland, world, quotes, meta):
        width, height = self.img.size
        self.header(title, meta, width)
        art_left = width - MARGIN - LEAD_ART_WIDTH
        self.lead(lead, width, art_left)
        column_width = (width - 2 * MARGIN - COLUMN_GAP) // 2
        left_x, right_x = MARGIN, MARGIN + column_width + COLUMN_GAP
        self.column(left_x, column_width, "国内", mainland, sources=meta.get("mainland_sources") or [])
        self.column(right_x, column_width, "国际", world, sources=meta.get("world_sources") or [])
        self.market(left_x, column_width, "A股", quotes.get("a_share") or [], height)
        self.market(right_x, column_width, "美股", quotes.get("us_stock") or [], height)

    def header(self, title, meta, width):
        c = self.c
        box = self.p._draw_title_wordmark(self.img, 18, 6, (152, 42), c["ink"])
        if box is None:
            self.text((MARGIN, 9), title, 30, True, c["ink"])
            title_right = MARGIN + self.width(title, 30)
        else:
            title_right = box[2]
        date_text = meta.get("date_text") or ""
        detail = meta.get("detail") or ""
        detail_colour = c["lead"] if meta.get("warning") else c["ink"]
        right = width - MARGIN
        detail = self.clip(detail, 11, right - title_right - 120)
        self.text((right, 9), date_text, 17, True, c["ink"], "ra")
        self.text((right, 33), detail, 11, True, detail_colour, "ra")
        meta_left = right - max(self.width(date_text, 17), self.width(detail, 11))
        self.p._draw_title_background(self.img, (title_right + 12, 4, meta_left - 14, RULE_Y - 4))
        self.d.line((MARGIN, RULE_Y, right, RULE_Y), fill=c["ink"], width=2)

    def lead(self, lead, width, art_left):
        c = self.c
        top, bottom = LEAD_BOX
        if not lead:
            self.text((MARGIN, top + 28), "新闻源暂无更新", 20, True)
            return
        seal_w, seal_h = SEAL_SIZE
        self.d.rounded_rectangle((MARGIN, top + 9, MARGIN + seal_w, top + 9 + seal_h), radius=3, fill=c["lead"])
        seal_ink = self.p._contrast_ink(c["lead"], c)
        for index, char in enumerate("头条"):
            self.text((MARGIN + seal_w / 2, top + 9 + seal_h * (1 + 2 * index) / 4), char, 17, True, seal_ink, "mm")
        text_x = MARGIN + seal_w + 14
        label = " · ".join(part for part in (lead.get("section_label"), lead.get("tag")) if part)
        title_x = text_x + (self.width(label, 12) + 10 if label else 0)
        if label:
            self.text((text_x, top + 19), label, 12, True, c["ink"], "lm")
        title_w = art_left - 12 - title_x
        largest = LEAD_TITLE_SIZES[0]
        size = self.fit_size(lead["title"], LEAD_TITLE_SIZES, title_w)
        self.text((title_x, top + 4 + (largest - size) // 2), self.clip(lead["title"], size, title_w), size, True)
        brief_w = art_left - 12 - text_x
        for index, line in enumerate(self.wrap(lead.get("why"), LEAD_FACT_SIZE, brief_w, 2, False)):
            self.text((text_x, top + 40 + index * LEAD_FACT_LINE), line, LEAD_FACT_SIZE, False)
        kind = news_art.topic_kind(lead.get("tag"), lead["title"])
        news_art.draw_topic_art(self.d, (art_left, top + 4, width - MARGIN - 6, bottom - 4), kind,
                                c["ink"], c["panel"], c["rule"], c["lead"])

    def column(self, x, width, label, items, *, sources):
        c = self.c
        self.d.rectangle((x, COLUMN_TOP + 3, x + 3, COLUMN_TOP + 15), fill=c["ink"])
        self.text((x + 10, COLUMN_TOP), label, 14, True)
        count_x = x + 14 + self.width(label, 14)
        self.d.line((x, COLUMN_TOP + 22, x + width, COLUMN_TOP + 22), fill=c["rule"], width=1)
        y = COLUMN_TOP + 32
        plan, gap = self.plan_column(items, width - 26, COLUMN_BOTTOM - y)
        for number, (item, title_lines) in enumerate(zip(items, plan), 1):
            y += self.item(x, y, width, number, item, title_lines) + gap
        self.text((count_x, COLUMN_TOP + 2), str(len(plan)), 11, True)
        if COLUMN_BOTTOM - y >= GLOBE_MIN_HEIGHT:
            self.globe_filler(x, y, width, COLUMN_BOTTOM, label, sources)

    def plan_column(self, items, text_width, available):
        """Title lines per shown story and the gap between stories.

        As many stories as fit on one line each, then a second line for each clipped
        title in order while the column has room. When the normal gap leaves a clipped
        title on one line, the compact gap is used if it lets more titles show whole.
        """
        clipped = [len(self.wrap(item["title"], 15, text_width, 2)) == 2 for item in items]
        best = None
        for gap in (ITEM_GAP, COMPACT_ITEM_GAP):
            lines, used = [], 0
            for _item in items:
                height = TITLE_LINE + ITEM_FOOTER + (gap if lines else 0)
                if used + height > available:
                    break
                lines.append(1)
                used += height
            for index in range(len(lines)):
                if clipped[index] and used + TITLE_LINE <= available:
                    lines[index] = 2
                    used += TITLE_LINE
            whole = sum(1 for index, count in enumerate(lines) if count == 2 or not clipped[index])
            if best is None or (len(lines), whole) > (len(best[0]), best[2]):
                best = (lines, gap, whole)
        return best[0], best[1]

    def item(self, x, y, width, number, item, title_lines):
        c = self.c
        self.text((x, y + 1), f"{number:02d}", 13, True)
        text_x, text_w = x + 26, width - 26
        lines = self.wrap(item["title"], 15, text_w, title_lines)
        for index, line in enumerate(lines):
            self.text((text_x, y + index * TITLE_LINE), line, 15, True)
        row_y = y + len(lines) * TITLE_LINE + 1
        tag = item.get("tag") or ""
        # The tag sits under the number, so the fact gets the full row after it.
        fact_x = x + self.chip(x, row_y, tag, c["ink"], size=10, solid=False) + 6 if tag else text_x
        why = self.clip(item.get("why"), 12, x + width - fact_x, False)
        if why:
            self.text((fact_x, row_y + 9), why, 12, False, c["ink"], "lm")
        return len(lines) * TITLE_LINE + ITEM_FOOTER

    def globe_filler(self, x, top, width, bottom, label, sources):
        c = self.c
        middle = (top + bottom) // 2
        radius = max(12, min(20, (bottom - top) // 2 - 6))
        news_art.draw_globe(self.d, x + 34, middle, radius, c["ink"], c["panel"], c["lead"])
        text_x = x + 34 + radius + 22
        self.text((text_x, middle - 18), f"{label}信源持续追踪", 12, True)
        names = " · ".join(sources) if sources else "每次刷新重新汇总"
        self.text((text_x, middle + 2), self.clip(names, 11, x + width - text_x), 11, False)

    def market(self, x, width, label, rows, height):
        c = self.c
        self.text((x, MARKET_TOP), label, 11, True)
        as_of = next((row.get("as_of_label") for row in rows if row.get("as_of_label")), "")
        if as_of:
            self.text((x + width, MARKET_TOP), f"截至 {as_of}", 10, True, c["ink"], "ra")
        top, bottom = MARKET_TOP + 14, height - 12
        self.d.line((x, top, x + width, top), fill=c["rule"], width=1)
        tile_w = (width - 16) // 3
        for index in range(3):
            left = x + index * (tile_w + 8)
            box = (left, top, left + tile_w, bottom)
            if index:
                self.d.line((left - 4, top + 6, left - 4, bottom), fill=c["rule"], width=1)
            if index < len(rows):
                self.quote(box, rows[index])
            else:
                self.text(((box[0] + box[2]) // 2, (top + bottom) // 2), "暂无行情", 11, True, c["ink"], "mm")

    def quote(self, box, row):
        c = self.c
        left, top, right, bottom = box
        pct = row.get("change_pct")
        mark = None
        self.text((left + 2, top + 7), row.get("label") or "", 11, True)
        if isinstance(pct, (int, float)) and round(pct, 2):
            mark = c["up"] if pct > 0 else c["down"]
            text = f"{pct:+.2f}%"
            text_left = right - 4 - self.width(text, 12)
            self.text((right - 4, top + 7), text, 12, True, c["ink"], "ra")
            self.change_mark(text_left - 10, top + 14, pct > 0, mark)
        else:
            self.text((right - 4, top + 7), "0.00%" if isinstance(pct, (int, float)) else "--", 12, True, c["ink"], "ra")
        price = row.get("price")
        self.text((left + 2, top + 23), f"{price:,.2f}" if isinstance(price, (int, float)) else "--", 14, True)
        self.sparkline((left + 2, bottom - 15, right - 6, bottom - 6), row.get("closes") or [], mark)

    def change_mark(self, cx, cy, up, colour):
        """A small solid triangle: the only place a market's colour appears."""
        half = 5
        points = ((cx - half, cy + 3), (cx + half, cy + 3), (cx, cy - 5)) if up else \
            ((cx - half, cy - 4), (cx + half, cy - 4), (cx, cy + 4))
        self.d.polygon(points, fill=colour)

    def sparkline(self, box, closes, mark=None):
        """An ink trend line; its end dot carries the day's direction colour when there is one."""
        left, top, right, bottom = box
        values = [value for value in closes if isinstance(value, (int, float))]
        if len(values) < 2:
            middle = (top + bottom) // 2
            for dash in range(left, right, 6):
                self.d.line((dash, middle, min(right, dash + 3), middle), fill=self.c["rule"], width=1)
            return
        low, high = min(values), max(values)
        span = (high - low) or 1
        points = [
            (left + (right - left) * index / (len(values) - 1), bottom - (bottom - top) * (value - low) / span)
            for index, value in enumerate(values)
        ]
        self.d.line(points, fill=self.c["ink"], width=1)
        end_x, end_y = points[-1]
        self.d.ellipse((end_x - 3, end_y - 3, end_x + 3, end_y + 3), fill=mark or self.c["ink"])


def date_text(moment):
    return f"{moment.month}月{moment.day}日 周{WEEKDAYS[moment.weekday()]}"
