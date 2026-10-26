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
RULE_Y = 58
LEAD_BOX = (66, 146)
# The header's wordmark box; the newsroom art stands on the rule beside it.
WORDMARK_BOX = (18, 5, (172, 48))
LEAD_ART_WIDTH = 170
# The brief's outlets as their logos take more of the lead band than the medallion: rows of
# logos sized by their shapes, at most this tall, the lead's outlet first.
LEAD_LOGOS_WIDTH = 250
SOURCE_LOGO_MAX_HEIGHT = 30
SOURCE_LOGO_GAP = (10, 6)
SOURCE_LOGOS_MAX = 12
# A logo that is solid lettering from edge to edge looks larger than its row; it is drawn smaller.
SOURCE_LOGO_SCALES = {"pbs_newshour": 0.68}
# A vertical red seal reading 头条 marks the lead story.
SEAL_SIZE = (28, 54)
COLUMN_TOP = 150
COLUMN_GAP = 24
COLUMN_BOTTOM = 390
MARKET_TOP = 396
ITEM_GAP = 6
# The tighter gap a full column uses to fit all five stories or a clipped title's second line.
COMPACT_ITEM_GAP = 3
# A story is its title lines plus a footer row with the tag chip and the one-line fact.
TITLE_LINE = 18
ITEM_FOOTER = 16
# Each story ends in a topic ring; its text stops short of this right-hand slot.
STORY_ICON_SLOT = 40
STORY_ICON_RADIUS = 15
# Accents are palette roles (the plugin's page colours fill them); anything unlisted stays in ink.
SECTION_ACCENTS = {"国内": "red", "A股": "red", "国际": "blue", "美股": "blue"}
# Each board is coloured the way its own market quotes it: mainland boards show
# gains red and losses green, US boards gains green and losses red.
GAINS_IN_RED = {"A股": True, "美股": False}
TOPIC_ACCENTS = {
    "conflict": "red",
    "climate": "red",
    "health": "red",
    "economy": "blue",
    "tech": "blue",
    "aviation": "blue",
}
# The lead's headline shrinks through these sizes to fit the block's width; its dek (kicker and
# two-sentence fact) wraps under it.
LEAD_TITLE_SIZES = (34, 32, 30, 28, 26, 24)
LEAD_FACT_SIZE = 15
LEAD_FACT_LINE = 19
GLOBE_MIN_HEIGHT = 60
# The most a short column widens each gap between its stories.
JUSTIFY_GAP_MAX = 16
WEEKDAYS = "一二三四五六日"


# How far a market sparkline's direction arrow carries on beyond the line's last close.
TREND_ARROW_LENGTH = 8


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

    def wrap_after(self, text, size, max_width, indent, max_lines, bold=False):
        """Wrap ``text`` whose first line starts ``indent`` px in (after a kicker)."""
        text = " ".join(str(text or "").split())
        if not indent:
            return self.wrap(text, size, max_width, max_lines, bold)
        first = ""
        for char in text:
            if self.width(first + char, size, bold) > max_width - indent:
                break
            first += char
        rest = text[len(first):].lstrip()
        if not rest or max_lines <= 1:
            return [first if not rest else self.clip(text, size, max_width - indent, bold)]
        return [first] + self.wrap(rest, size, max_width, max_lines - 1, bold)

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
    def accent(self, role):
        """The page colour for an accent role, or the theme's ink for none."""
        return self.c[role] if role else self.c["ink"]

    def topic_accent(self, tag, title):
        kind = news_art.topic_kind(tag, title)
        return kind, self.accent(TOPIC_ACCENTS.get(kind))

    def paste_logo(self, key, x, middle):
        """Paste an index mark left-aligned at ``x``, centred on ``middle``; returns its width."""
        logo = self.p._load_market_logo(key) if key else None
        if logo is None:
            return 0
        # Marks keep their owners' colours in both themes; white inside a mark is part of it.
        self.img.paste(logo.convert("RGB"), (int(x), int(middle - logo.height / 2)), logo.getchannel("A"))
        return logo.width

    # -- page ---------------------------------------------------------------
    def render(self, *, title, lead, mainland, world, quotes, meta):
        width, height = self.img.size
        self.header(title, meta, width)
        logos = source_logo_keys(meta.get("source_logos") or [], (lead or {}).get("source_logo"))
        art_left = width - MARGIN - (LEAD_LOGOS_WIDTH if logos else LEAD_ART_WIDTH)
        self.lead(lead, width, art_left, logos)
        column_width = (width - 2 * MARGIN - COLUMN_GAP) // 2
        left_x, right_x = MARGIN, MARGIN + column_width + COLUMN_GAP
        self.column(left_x, column_width, "国内", mainland, sources=meta.get("mainland_sources") or [])
        self.column(right_x, column_width, "国际", world, sources=meta.get("world_sources") or [])
        self.market(left_x, column_width, "A股", quotes.get("a_share") or [], height)
        self.market(right_x, column_width, "美股", quotes.get("us_stock") or [], height)

    def header(self, title, meta, width):
        c = self.c
        x, y, size = WORDMARK_BOX
        box = self.p._draw_title_wordmark(self.img, x, y, size, c["ink"])
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
        self.text((right, 11), date_text, 17, True, c["ink"], "ra")
        self.text((right, 36), detail, 11, True, detail_colour, "ra")
        meta_left = right - max(self.width(date_text, 17), self.width(detail, 11))
        # The newsroom stands on the rule: its objects' feet meet the line drawn over them.
        self.p._draw_title_background(self.img, (title_right + 12, 1, meta_left - 14, RULE_Y + 1), align="bottom")
        self.d.line((MARGIN, RULE_Y, right, RULE_Y), fill=c["ink"], width=2)

    def lead(self, lead, width, art_left, logos=()):
        c = self.c
        top, bottom = LEAD_BOX
        if not lead:
            self.text((MARGIN, top + 28), "新闻源暂无更新", 20, True)
            return
        seal_w, seal_h = SEAL_SIZE
        # The generated 头条 badge is 1:2; drawn as a plain seal when the file is missing.
        if not news_art.paste_badge(self.img, "seal", MARGIN + seal_w / 2, top + 9 + seal_h / 2, seal_h // 2, seal_h):
            self.d.rounded_rectangle((MARGIN, top + 9, MARGIN + seal_w, top + 9 + seal_h), radius=3, fill=c["lead"])
            seal_ink = self.p._contrast_ink(c["lead"], c)
            for index, char in enumerate("头条"):
                self.text((MARGIN + seal_w / 2, top + 9 + seal_h * (1 + 2 * index) / 4), char, 17, True, seal_ink,
                          "mm")
        text_x = MARGIN + seal_w + 14
        brief_w = art_left - 12 - text_x
        # The headline runs the block's full width at the largest size that fits, its ink top on
        # the same line whichever face sets it.
        font, size = self.headline_font(lead["title"], brief_w)
        headline = self.clip_with(lead["title"], font, brief_w)
        ink_top = font.getbbox(headline, anchor="la")[1] if headline else 0
        self.d.text((text_x, top + 4 + (LEAD_TITLE_SIZES[0] - size) // 2 - ink_top), headline, font=font,
                    fill=c["ink"], anchor="la")
        y = top + LEAD_TITLE_SIZES[0] + 6
        # The dek under it opens like a newspaper's: the section in its accent and the topic, then the
        # story's two-sentence fact running on across two lines.
        indent = 0
        section, tag = lead.get("section_label"), lead.get("tag")
        middle = y + LEAD_FACT_LINE / 2 - 1
        if section:
            self.text((text_x, middle), section, 13, True, self.accent(SECTION_ACCENTS.get(section)), "lm")
            indent = self.width(section, 13)
        if section and tag:
            self.d.line((text_x + indent + 6, middle - 6, text_x + indent + 6, middle + 6), fill=c["ink"], width=1)
            indent += 12
        if tag:
            self.text((text_x + indent, middle), tag, 13, True, c["ink"], "lm")
            indent += self.width(tag, 13)
        if indent:
            indent += 10
        fact = self.wrap_after(lead.get("why"), LEAD_FACT_SIZE, brief_w, indent, 2)
        for index, line in enumerate(fact):
            self.text((text_x + (indent if index == 0 else 0), y + index * LEAD_FACT_LINE), line, LEAD_FACT_SIZE, False)
        note = lead.get("source_note")
        if note and len(fact) < 2:
            # A one-sentence fact leaves its second line to the story's source and time.
            note_y = y + len(fact) * LEAD_FACT_LINE + 2
            self.d.line((text_x, note_y + 8, text_x + 14, note_y + 8), fill=c["ink"], width=1)
            self.text((text_x + 20, note_y + 8), self.clip(note, 12, brief_w - 20, False), 12, False, c["ink"], "lm")
        if logos:
            self.source_wall((art_left, top + 2, width - MARGIN, bottom - 2), logos, lead.get("source_logo"))
            return
        kind = news_art.topic_kind(lead.get("tag"), lead["title"])
        news_art.draw_topic_art(self.d, (art_left, top + 4, width - MARGIN - 6, bottom - 4), kind,
                                c["ink"], c["panel"], c["rule"], c["lead"], image=self.img)

    def headline_font(self, title, max_width):
        """(font, size) for the lead's headline: the serif banner face at the largest size that
        fits when it sets every character, else the page's bold."""
        serif = self.p._headline_font(LEAD_TITLE_SIZES[0])
        if serif is not None and serif.covers(title):
            for size in LEAD_TITLE_SIZES:
                font = self.p._headline_font(size)
                if self.p._tw(self.d, title, font) <= max_width:
                    return font, size
            return font, size
        size = self.fit_size(title, LEAD_TITLE_SIZES, max_width)
        return self.font(size), size

    def clip_with(self, text, font, max_width):
        text = str(text or "")
        if self.p._tw(self.d, text, font) <= max_width:
            return text
        while text and self.p._tw(self.d, text + "…", font) > max_width:
            text = text[:-1]
        return text.rstrip() + "…" if text else ""

    def source_wall(self, box, keys, lead_key):
        """The brief's outlets as their official logos in rows packed by shape inside ``box``,
        each logo in its own colours on the bare page; a short red rule marks the lead's."""
        left, top, right, bottom = box
        night = sum(tuple(self.c["panel"])[:3]) < 330
        scales = [SOURCE_LOGO_SCALES.get(key, 1.0) for key in keys]
        rows, height = pack_logo_rows([news_art.source_logo_aspect(key) * scale for key, scale in zip(keys, scales)],
                                      right - left, bottom - top)
        gap_x, gap_y = SOURCE_LOGO_GAP
        y = top + (bottom - top - len(rows) * height - gap_y * (len(rows) - 1)) / 2
        for row in rows:
            logos = [news_art.source_logo(keys[index], max(8, round(height * scales[index])), night, self.c["ink"],
                                          self.c["panel"]) for index in row]
            row_width = sum(logo.width for logo in logos) + gap_x * (len(logos) - 1)
            x = left + (right - left - row_width) / 2
            for index, logo in zip(row, logos):
                self.img.paste(logo, (round(x), round(y + (height - logo.height) / 2)), logo)
                if keys[index] == lead_key:
                    self.d.line((x, y + height + 3, x + min(logo.width, 18), y + height + 3), fill=self.c["lead"],
                                width=2)
                x += logo.width + gap_x
            y += height + gap_y

    def column(self, x, width, label, items, *, sources):
        c = self.c
        mark = self.accent(SECTION_ACCENTS.get(label))
        self.d.rectangle((x, COLUMN_TOP + 3, x + 3, COLUMN_TOP + 15), fill=mark)
        self.text((x + 10, COLUMN_TOP), label, 14, True)
        count_x = x + 14 + self.width(label, 14)
        self.d.line((x, COLUMN_TOP + 22, x + width, COLUMN_TOP + 22), fill=c["rule"], width=1)
        y = COLUMN_TOP + 32
        plan, gap, facts = self.plan_column(items, width - 26 - STORY_ICON_SLOT, COLUMN_BOTTOM - y)
        used = sum(lines * TITLE_LINE + (ITEM_FOOTER if fact else 0) for lines, fact in zip(plan, facts))
        used += gap * max(0, len(plan) - 1)
        spare = COLUMN_BOTTOM - y - used
        if len(plan) > 1 and spare < GLOBE_MIN_HEIGHT:
            # Too little room for the globe: spread the stories down the column, as a
            # newspaper justifies one, instead of leaving a band at its foot.
            gap += min(spare // (len(plan) - 1), JUSTIFY_GAP_MAX)
        for number, (item, title_lines, fact) in enumerate(zip(items, plan, facts), 1):
            height = self.item(x, y, width, number, item, title_lines, mark, with_fact=fact)
            if number < len(plan):
                # A dotted rule in the gap parts one story from the next, as a news digest does.
                rule_y = y + height + max(1, gap // 2)
                for dot_x in range(x + 26, x + width - STORY_ICON_SLOT + 6, 4):
                    self.d.point((dot_x, rule_y), fill=c["rule"])
            y += height + gap
        self.text((count_x, COLUMN_TOP + 2), str(len(plan)), 11, True, mark)
        if COLUMN_BOTTOM - y >= GLOBE_MIN_HEIGHT:
            self.globe_filler(x, y, width, COLUMN_BOTTOM, label, sources)

    def plan_column(self, items, text_width, available):
        """(title lines, gap, fact shown) for each story the column shows.

        As many stories as fit come first: when not all fit with their facts, the later
        ones keep only their title line, so the column carries more news. Facts stay on
        as many leading stories as possible, then clipped titles get a second line in
        order while room is left. The compact gap is used when it shows more.
        """
        clipped = [len(self.wrap(item["title"], 15, text_width, 2)) == 2 for item in items]
        has_fact = [bool(str(item.get("why") or "").strip()) for item in items]

        def height(count, full, gap):
            rows = sum(TITLE_LINE + (ITEM_FOOTER if index < full and has_fact[index] else 0) for index in range(count))
            return rows + gap * max(0, count - 1)

        best = None
        for gap in (ITEM_GAP, COMPACT_ITEM_GAP):
            count, full = 0, 0
            for candidate in range(len(items), 0, -1):
                fitting = next((keep for keep in range(candidate, -1, -1) if height(candidate, keep, gap) <= available),
                               None)
                if fitting is not None:
                    count, full = candidate, fitting
                    break
            used = height(count, full, gap)
            lines = [1] * count
            for index in range(count):
                if clipped[index] and used + TITLE_LINE <= available:
                    lines[index] = 2
                    used += TITLE_LINE
            facts = [index < full and has_fact[index] for index in range(count)]
            whole = sum(1 for index, lines_used in enumerate(lines) if lines_used == 2 or not clipped[index])
            score = (count, sum(facts), whole)
            if best is None or score > best[3]:
                best = (lines, gap, facts, score)
        return best[0], best[1], best[2]

    def item(self, x, y, width, number, item, title_lines, number_colour=None, with_fact=True):
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
        # The topic shows only as the icon at the right; the fact runs under the title.
        why = self.clip(item.get("why"), 11, text_right - text_x, False) if with_fact else ""
        if why:
            self.text((text_x, row_y + 7), why, 11, False, c["ink"], "lm")
        height = len(lines) * TITLE_LINE + (ITEM_FOOTER if why else 0)
        radius = min(STORY_ICON_RADIUS, height // 2 - 1)
        news_art.draw_story_icon(self.d, x + width - STORY_ICON_RADIUS - 2, y + height // 2 - 1,
                                 radius, kind, c["ink"], accent, c["panel"], image=self.img)
        return height

    def globe_filler(self, x, top, width, bottom, label, sources):
        c = self.c
        middle = (top + bottom) // 2
        radius = max(12, min(20, (bottom - top) // 2 - 6))
        news_art.draw_globe(self.d, x + 34, middle, radius, c["ink"], c["panel"], c["lead"], image=self.img)
        text_x = x + 34 + radius + 22
        self.text((text_x, middle - 18), f"{label}信源持续追踪", 12, True)
        names = " · ".join(sources) if sources else "每次刷新重新汇总"
        self.text((text_x, middle + 2), self.clip(names, 11, x + width - text_x), 11, False)

    def market(self, x, width, label, rows, height):
        c = self.c
        self.d.rectangle((x, MARKET_TOP + 2, x + 2, MARKET_TOP + 12), fill=self.accent(SECTION_ACCENTS.get(label)))
        self.text((x + 7, MARKET_TOP), label, 11, True)
        as_of = next((row.get("as_of_label") for row in rows if row.get("as_of_label")), "")
        if as_of:
            self.text((x + width, MARKET_TOP), f"截至 {as_of}", 10, True, c["ink"], "ra")
        top, bottom = MARKET_TOP + 14, height - 12
        self.d.line((x, top, x + width, top), fill=c["rule"], width=1)
        tile_w = (width - 16) // 3
        gains_in_red = GAINS_IN_RED.get(label, True)
        for index in range(3):
            left = x + index * (tile_w + 8)
            box = (left, top, left + tile_w, bottom)
            if index:
                self.d.line((left - 4, top + 6, left - 4, bottom), fill=c["rule"], width=1)
            if index < len(rows):
                self.quote(box, rows[index], gains_in_red)
            else:
                self.text(((box[0] + box[2]) // 2, (top + bottom) // 2), "暂无行情", 11, True, c["ink"], "mm")

    def quote(self, box, row, gains_in_red=True):
        c = self.c
        left, top, right, bottom = box
        pct = row.get("change_pct")
        middle = top + 13
        change_left = self.change_figure(right - 2, middle, pct, gains_in_red)
        mark = self.change_colour(pct, gains_in_red)
        # The index's own mark leads; the short name follows when the row has room for it.
        label_x = left + 1
        logo_w = self.paste_logo(row.get("logo"), label_x, middle)
        label_x += logo_w + 3 if logo_w else 0
        label = row.get("label") or ""
        if label_x + self.width(label, 11) <= change_left - 3 or not logo_w:
            self.text((label_x, middle), label, 11, True, c["ink"], "lm")
        price = row.get("price")
        self.text((left + 2, top + 24), f"{price:,.2f}" if isinstance(price, (int, float)) else "--", 14, True)
        self.sparkline((left + 2, bottom - 15, right - 6, bottom - 6), row.get("closes") or [], mark,
                       rising=pct > 0 if mark is not None else None)

    def change_colour(self, pct, gains_in_red=True):
        """The day's direction colour in the board's convention, or None when flat or unknown."""
        if not isinstance(pct, (int, float)) or not round(pct, 2):
            return None
        return self.c["red"] if (pct > 0) == gains_in_red else self.c["green"]

    def change_figure(self, right, middle, pct, gains_in_red=True):
        """The day's change ending at ``right``: a small triangle and the figure in the
        board's direction colour, ink when flat. Returns its left edge."""
        colour = self.change_colour(pct, gains_in_red)
        if colour is not None:
            text = f"{pct:+.2f}%"
        else:
            text = "0.00%" if isinstance(pct, (int, float)) else "--"
        text_left = right - self.width(text, 12)
        self.text((right, middle), text, 12, True, colour or self.c["ink"], "rm")
        if colour is None:
            return text_left
        cx, half = text_left - 6, 4
        points = ((cx - half, middle + 3), (cx + half, middle + 3), (cx, middle - 4)) if pct > 0 else \
            ((cx - half, middle - 3), (cx + half, middle - 3), (cx, middle + 4))
        self.d.polygon(points, fill=colour)
        return cx - half

    def sparkline(self, box, closes, mark=None, rising=None):
        """An ink trend line ending in a small slanted arrow, up or down in the day's direction
        colour; an ink dot when the day is flat or unknown."""
        left, top, right, bottom = box
        values = [value for value in closes if isinstance(value, (int, float))]
        if len(values) < 2:
            middle = (top + bottom) // 2
            for dash in range(left, right, 6):
                self.d.line((dash, middle, min(right, dash + 3), middle), fill=self.c["rule"], width=1)
            return
        low, high = min(values), max(values)
        span = (high - low) or 1
        # With an arrow to come, the line stops short so the arrow carries on from its end.
        arrow = mark is not None and rising is not None
        line_right = right - TREND_ARROW_LENGTH if arrow else right
        points = [
            (left + (line_right - left) * index / (len(values) - 1), bottom - (bottom - top) * (value - low) / span)
            for index, value in enumerate(values)
        ]
        self.d.line(points, fill=self.c["ink"], width=1)
        end_x, end_y = points[-1]
        if not arrow:
            self.d.ellipse((end_x - 3, end_y - 3, end_x + 3, end_y + 3), fill=mark or self.c["ink"])
            return
        self.trend_arrow((end_x, end_y), rising, mark)

    def trend_arrow(self, tail, rising, colour, length=TREND_ARROW_LENGTH, head=5):
        """An arrow carrying on from the line's end at tail, slanting up-right (rising) or down-right."""
        tail_x, tail_y = tail
        slope = -1 if rising else 1
        tip_x, tip_y = tail_x + length, tail_y + slope * length
        self.d.line(((tail_x, tail_y), (tip_x - 2, tip_y - slope * 2)), fill=colour, width=2)
        self.d.polygon(((tip_x, tip_y), (tip_x - head, tip_y), (tip_x, tip_y - slope * head)), fill=colour)


def date_text(moment):
    return f"{moment.month}月{moment.day}日 周{WEEKDAYS[moment.weekday()]}"


def source_logo_keys(keys, lead_key=""):
    """The outlets to show as logos: those with a logo file, the lead's first, at most SOURCE_LOGOS_MAX."""
    ordered = ([lead_key] if lead_key else []) + [key for key in keys if key != lead_key]
    return [key for key in dict.fromkeys(ordered) if news_art.source_logo_aspect(key)][:SOURCE_LOGOS_MAX]


def pack_logo_rows(aspects, width, height, max_height=SOURCE_LOGO_MAX_HEIGHT, gap=SOURCE_LOGO_GAP):
    """([[index, ...] per row], logo height) for logos of these aspects (width / height) in a
    width x height box: the row count that lets them stand tallest. The widest logos are dealt
    first, each to the row with the least width so far, so rows come out even; the first logo
    (the lead's outlet) heads the top row and every row keeps the logos' order."""
    gap_x, gap_y = gap
    best = None
    for count in range(1, min(4, len(aspects)) + 1):
        rows, widths = [[] for _ in range(count)], [0.0] * count
        for index in sorted(range(len(aspects)), key=lambda item: -aspects[item]):
            row = min(range(count), key=lambda item: widths[item])
            rows[row].append(index)
            widths[row] += aspects[index]
        if any(not row for row in rows):
            continue
        logo_height = min(max_height, (height - gap_y * (count - 1)) / count,
                          *((width - gap_x * (len(row) - 1)) / total for row, total in zip(rows, widths)))
        if best is None or logo_height > best[1] + 0.5:
            rows = sorted((sorted(row) for row in rows), key=lambda row: row[0])
            best = (rows, logo_height)
    return best if best else ([], 0)
