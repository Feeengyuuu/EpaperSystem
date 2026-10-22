"""The 整点新闻 page: header, lead band, two news columns and a market strip.

The plugin prepares plain data (lead, sections, quotes, meta); this module only
draws it. Spare column space gets a small globe with that column's sources, the
lead band carries a topic medallion and every story a topic ring from ``news_art``.

The page reads as an ink newspaper with two sparing accents in the panel's solid
inks, which it shows without dithering: red for 国内 and A股, blue for 国际 and 美股.
They mark section bars, counts and story numbers, the lead's kicker, and the tag and
ring glyph of hard-news (red) or business and technology (blue) stories. A market
change is a red (up) or green (down) figure, as Chinese quotes show them, beside the
index's own mark. No colour is ever a filled block except the 头条 seal.
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
# Each story ends in a topic ring; its text stops short of this right-hand slot.
STORY_ICON_SLOT = 40
STORY_ICON_RADIUS = 15
# The panel's solid accents; anything unlisted stays in the theme's ink.
RED = (255, 0, 0)
BLUE = (0, 0, 255)
SECTION_ACCENTS = {"国内": RED, "A股": RED, "国际": BLUE, "美股": BLUE}
TOPIC_ACCENTS = {
    "conflict": RED,
    "climate": RED,
    "health": RED,
    "economy": BLUE,
    "tech": BLUE,
    "aviation": BLUE,
}
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

    def chip(self, x, y, text, colour, size=11, solid=True, ink=None):
        height = size + 8
        width = self.width(text, size) + 14
        box = (x, y, x + width, y + height)
        if solid:
            self.d.rounded_rectangle(box, radius=height // 2, fill=colour)
            ink = self.p._contrast_ink(colour, self.c)
        else:
            self.d.rounded_rectangle(box, radius=height // 2, outline=colour, width=1)
            ink = ink or self.c["ink"]
        self.text((x + 7, y + height / 2), text, size, True, ink, "lm")
        return width

    # -- colour helpers -----------------------------------------------------
    def accent(self, colour):
        """A solid accent, or the theme's ink for none; blue is too dark to read on a night page."""
        if colour is None:
            return self.c["ink"]
        if colour == BLUE and sum(tuple(self.c["panel"])[:3]) < 384:
            return self.c["ink"]
        return colour

    def topic_accent(self, tag, title):
        kind = news_art.topic_kind(tag, title)
        return kind, self.accent(TOPIC_ACCENTS.get(kind))

    def paste_logo(self, key, x, middle):
        """Paste an index mark left-aligned at ``x``, centred on ``middle``; returns its width."""
        logo = self.p._load_market_logo(key) if key else None
        if logo is None:
            return 0
        panel = tuple(self.c["panel"])[:3]
        if panel != (255, 255, 255):
            # The marks' white parts are the page showing through.
            logo = logo.copy()
            pixels = logo.load()
            for py in range(logo.height):
                for px in range(logo.width):
                    if pixels[px, py][:3] == (255, 255, 255):
                        pixels[px, py] = (*panel, pixels[px, py][3])
        self.img.paste(logo.convert("RGB"), (int(x), int(middle - logo.height / 2)), logo.getchannel("A"))
        return logo.width

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
        # A newspaper kicker before the headline: the section in its accent, then the topic.
        title_x = text_x
        kicker_y = top + 20
        section = lead.get("section_label")
        if section:
            self.text((title_x, kicker_y), section, 13, True, self.accent(SECTION_ACCENTS.get(section)), "lm")
            title_x += self.width(section, 13)
        if section and lead.get("tag"):
            self.d.line((title_x + 6, kicker_y - 6, title_x + 6, kicker_y + 6), fill=c["ink"], width=1)
            title_x += 12
        if lead.get("tag"):
            self.text((title_x, kicker_y), lead["tag"], 13, True, c["ink"], "lm")
            title_x += self.width(lead["tag"], 13)
        if title_x > text_x:
            title_x += 12
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
        mark = self.accent(SECTION_ACCENTS.get(label))
        # The bar keeps the section's colour even at night, where blue words fall back to ink.
        self.d.rectangle((x, COLUMN_TOP + 3, x + 3, COLUMN_TOP + 15), fill=SECTION_ACCENTS.get(label, c["ink"]))
        self.text((x + 10, COLUMN_TOP), label, 14, True)
        count_x = x + 14 + self.width(label, 14)
        self.d.line((x, COLUMN_TOP + 22, x + width, COLUMN_TOP + 22), fill=c["rule"], width=1)
        y = COLUMN_TOP + 32
        plan, gap = self.plan_column(items, width - 26 - STORY_ICON_SLOT, COLUMN_BOTTOM - y)
        for number, (item, title_lines) in enumerate(zip(items, plan), 1):
            y += self.item(x, y, width, number, item, title_lines, mark) + gap
        self.text((count_x, COLUMN_TOP + 2), str(len(plan)), 11, True, mark)
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

    def item(self, x, y, width, number, item, title_lines, number_colour=None):
        c = self.c
        self.text((x, y + 1), f"{number:02d}", 13, True, number_colour)
        text_x, text_w = x + 26, width - 26 - STORY_ICON_SLOT
        text_right = x + width - STORY_ICON_SLOT
        lines = self.wrap(item["title"], 15, text_w, title_lines)
        for index, line in enumerate(lines):
            self.text((text_x, y + index * TITLE_LINE), line, 15, True)
        row_y = y + len(lines) * TITLE_LINE + 1
        tag = item.get("tag") or ""
        kind, accent = self.topic_accent(tag, item["title"])
        # The tag sits under the number, so the fact gets the full row after it.
        fact_x = x + self.chip(x, row_y, tag, accent, size=10, solid=False, ink=accent) + 6 if tag else text_x
        why = self.clip(item.get("why"), 12, text_right - fact_x, False)
        if why:
            self.text((fact_x, row_y + 9), why, 12, False, c["ink"], "lm")
        height = len(lines) * TITLE_LINE + ITEM_FOOTER
        news_art.draw_story_icon(self.d, x + width - STORY_ICON_RADIUS - 2, y + height // 2 - 1,
                                 STORY_ICON_RADIUS, kind, c["ink"], accent, c["panel"])
        return height

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
        self.d.rectangle((x, MARKET_TOP + 2, x + 2, MARKET_TOP + 12), fill=SECTION_ACCENTS.get(label, c["ink"]))
        self.text((x + 7, MARKET_TOP), label, 11, True)
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
        middle = top + 13
        mark = None
        change_left = self.change_figure(right - 2, middle, pct)
        if isinstance(pct, (int, float)) and round(pct, 2):
            mark = c["up"] if pct > 0 else c["down"]
        # The index's own mark leads; the short name follows when the row has room for it.
        label_x = left + 1
        logo_w = self.paste_logo(row.get("logo"), label_x, middle)
        label_x += logo_w + 3 if logo_w else 0
        label = row.get("label") or ""
        if label_x + self.width(label, 11) <= change_left - 3 or not logo_w:
            self.text((label_x, middle), label, 11, True, c["ink"], "lm")
        price = row.get("price")
        self.text((left + 2, top + 24), f"{price:,.2f}" if isinstance(price, (int, float)) else "--", 14, True)
        self.sparkline((left + 2, bottom - 15, right - 6, bottom - 6), row.get("closes") or [], mark)

    def change_figure(self, right, middle, pct):
        """The day's change ending at ``right``: a small triangle and the figure, red up and
        green down as Chinese quotes show them, ink when flat. Returns its left edge."""
        if isinstance(pct, (int, float)) and round(pct, 2):
            text, colour = f"{pct:+.2f}%", self.c["up"] if pct > 0 else self.c["down"]
        else:
            text, colour = ("0.00%" if isinstance(pct, (int, float)) else "--"), None
        text_left = right - self.width(text, 12)
        self.text((right, middle), text, 12, True, colour or self.c["ink"], "rm")
        if colour is None:
            return text_left
        cx, half = text_left - 6, 4
        points = ((cx - half, middle + 3), (cx + half, middle + 3), (cx, middle - 4)) if pct > 0 else \
            ((cx - half, middle - 3), (cx + half, middle - 3), (cx, middle + 4))
        self.d.polygon(points, fill=colour)
        return cx - half

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
