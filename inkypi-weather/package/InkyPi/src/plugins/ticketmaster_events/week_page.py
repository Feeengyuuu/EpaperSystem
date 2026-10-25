"""The Local Events page: the featured event first, what is next beside it, the week below.

800 x 480, by importance: the featured event (the biggest tour starting soon) largest, its
title on its poster, its place, price and kind under it with the public sales opening this
week; beside it the next seven events by start time with their time, venue, distance and
price; along the foot the next seven days' counts and mix of kinds. The page lies on the
featured poster, blurred and washed into the paper, so its colour follows what is on, and
the cards are frosted over it so their text keeps its contrast.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PIL import Image, ImageDraw, ImageFilter, ImageOps

WEEKDAYS = "一二三四五六日"
SEGMENT_LABELS = {"music": "音乐", "sports": "体育", "arts & theatre": "戏剧", "film": "电影", "miscellaneous": "其他"}
STATUS_LABELS = {"onsale": "售票中", "offsale": "停售", "postponed": "延期", "rescheduled": "改期"}
MARGIN, HEADER_H = 16, 54
CARD_TOP, CARD_BOTTOM = 64, 420
HERO = (MARGIN, CARD_TOP, 324, CARD_BOTTOM)
POSTER_H = 188
LIST = (336, CARD_TOP, 800 - MARGIN, CARD_BOTTOM)
ROW_H, LIST_ROWS = 47, 7
THUMB = (72, 42)
WEEK_BAND = (MARGIN, 428, 800 - MARGIN, 474)
# The page backdrop: the featured poster blurred at a small size, laid over the paper at this
# strength (day, night); the cards frost it back by CARD_FROST.
BACKDROP_MIX = {"color": 0.42, "dark": 0.48, "paper": 0.0}
CARD_FROST = 0.8


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def day_label(day, today):
    """今天 / 明天 / 周X for a date within the week ahead."""
    if day is None:
        return "待定"
    offset = (day - today).days
    return "今天" if offset == 0 else "明天" if offset == 1 else f"周{WEEKDAYS[day.weekday()]}"


def clock(value):
    """'19:30' from Ticketmaster's '19:30:00' ("" when the event has no set time)."""
    return str(value or "")[:5]


class WeekPageMixin:
    def _render_events(self, dimensions, events, settings, source_label, generated_at, stale=False, week=None,
                       on_sale=None):
        width, height = 800, 480
        colors = self._palette(settings)
        image = Image.new("RGB", (width, height), colors["paper"])
        self._draw_backdrop(image, events[0] if events else None, colors)
        draw = ImageDraw.Draw(image)
        today = generated_at.date() if isinstance(generated_at, datetime) else date.today()
        days = (week or {}).get("days") or []
        self._draw_page_header(image, draw, settings, generated_at, stale, days, colors)
        for box in (HERO, LIST, WEEK_BAND):
            self._frost(image, box, colors)
        if events:
            self._draw_featured(image, draw, events[0], today, colors)
        self._draw_on_sale(draw, on_sale or [], days, getattr(generated_at, "tzinfo", None), colors)
        self._draw_next(image, draw, events[1:1 + LIST_ROWS], today, colors)
        self._draw_week_band(draw, days, today, colors)
        if tuple(dimensions) != (width, height):
            image = image.resize(tuple(dimensions), Image.Resampling.LANCZOS)
        return image

    # -- the page under the cards ---------------------------------------------------------
    def _draw_backdrop(self, image, featured, colors):
        """The featured poster, blurred and washed into the paper, under everything below the
        header band; the plain background (header band and edge stripe) when there is no poster."""
        draw = ImageDraw.Draw(image)
        self._draw_background(draw, image.size, colors)
        strength = BACKDROP_MIX.get(colors["mode"], 0.0)
        poster = self._load_event_image(featured) if featured is not None and strength else None
        if poster is None:
            return
        small = ImageOps.fit(poster.convert("RGB"), (100, 60), method=Image.Resampling.BILINEAR)
        wash = small.filter(ImageFilter.GaussianBlur(5)).resize(image.size, Image.Resampling.BICUBIC)
        top = HEADER_H + 3
        region = image.crop((0, top, image.width, image.height))
        image.paste(Image.blend(region, wash.crop((0, top, image.width, image.height)), strength), (0, top))
        if colors["mode"] == "color":
            draw.rectangle((0, 0, 6, image.height), fill=colors["accent"])

    def _frost(self, image, box, colors, amount=CARD_FROST):
        """Frost the backdrop under a card with the paper colour, and edge the card."""
        x0, y0, x1, y1 = box
        region = image.crop((x0, y0, x1, y1))
        image.paste(Image.blend(region, Image.new("RGB", region.size, colors["paper"]), amount), (x0, y0))
        ImageDraw.Draw(image).rounded_rectangle(box, radius=6, outline=colors["line"], width=1)

    # -- header -----------------------------------------------------------------------------
    def _draw_page_header(self, image, draw, settings, generated_at, stale, days, colors):
        right = 800 - MARGIN
        wordmark_bottom = self._draw_header_wordmark(image, draw, MARGIN + 2, 4, 250, 44, colors)
        if wordmark_bottom is None:
            draw.text((MARGIN, 10), "LOCAL EVENTS", fill=colors.get("title", colors["ink"]), font=self._font(28, bold=True))
        counted = [day["count"] for day in days if isinstance(day.get("count"), int)]
        total_font, small = self._font(22, bold=True), self._font(13, bold=True)
        headline = f"本周 {sum(counted):,} 场" if counted else "本地活动"
        draw.text((right - draw.textlength(headline, font=total_font), 3), headline,
                  fill=colors.get("accent", colors["ink"]), font=total_font)
        when = generated_at.strftime("%m/%d %H:%M") if isinstance(generated_at, datetime) else ""
        note = f"{self._place_label(settings)} · {'缓存 ' if stale else ''}{when} 更新"
        note_w = draw.textlength(note, font=small)
        draw.text((right - note_w, 32), note, fill=colors.get("header_muted", colors["muted"]), font=small)
        # The kinds' colours, as the list and the week band use them.
        legend_font = self._font(12, bold=True)
        x = 282
        for key, label in SEGMENT_LABELS.items():
            if x + 52 > right - note_w - 10:
                break
            draw.ellipse((x, 37, x + 9, 46), fill=self._segment_color(key, colors))
            draw.text((x + 12, 33), label, fill=colors.get("header_muted", colors["muted"]), font=legend_font)
            x += 14 + draw.textlength(label, font=legend_font) + 10

    def _place_label(self, settings):
        radius = self._bounded_int(settings.get("radiusMiles"), 50, 1, 250)
        postal = self._postal_code(settings)
        if postal:
            return f"{postal} 周边 {radius} 英里"
        return self._location_label(settings)

    # -- the featured event ---------------------------------------------------------------
    def _draw_featured(self, image, draw, event, today, colors):
        """The poster with the title over its foot, the day and time on a gold chip, the tour's
        size and the sale status; under it the place and the price, kind, distance and repeats."""
        x0, y0, x1, _y1 = HERO
        pad = 8
        px0, py0, px1 = x0 + pad, y0 + pad, x1 - pad
        width = px1 - px0
        poster = self._load_event_image(event)
        art = ImageOps.fit((poster or self._placeholder_event_image(event, (width, POSTER_H), colors)).convert("RGB"),
                           (width, POSTER_H), method=Image.Resampling.LANCZOS)
        # A dark shade rises from the poster's foot for the title to sit on.
        title_font = self._font(21, bold=True)
        for size in (21, 19, 17):
            title_font = self._font(size, bold=True)
            if len(self._wrap_full_text(draw, event.title, title_font, width - 20)) <= 2:
                break
        lines = self._wrap_text(draw, event.title, title_font, width - 20, 2)
        shade_h = 18 + len(lines) * (title_font.size + 4)
        ramp = Image.linear_gradient("L").resize((1, shade_h + 30)).resize((width, shade_h + 30))
        shade = Image.new("RGB", (width, shade_h + 30), (12, 14, 18))
        foot = art.crop((0, POSTER_H - shade_h - 30, width, POSTER_H))
        art.paste(Image.composite(shade, foot, ramp.point(lambda value: int(value * 0.86))), (0, POSTER_H - shade_h - 30))
        image.paste(art, (px0, py0))
        text_y = py0 + POSTER_H - 10 - len(lines) * (title_font.size + 4)
        for line in lines:
            draw.text((px0 + 10, text_y), line, fill=(250, 248, 240), font=title_font)
            text_y += title_font.size + 4
        draw.rectangle((px0, py0 + POSTER_H, px1, py0 + POSTER_H + 4), fill=self._event_color(event, colors))
        # When, on the gold chip.
        day = _day(event.local_date)
        top_text = day_label(day, today) if day and (day - today).days < 7 else (f"{day:%m/%d}" if day else "待定")
        bottom_text = clock(event.local_time) or "全天"
        chip_font, time_font = self._font(12, bold=True), self._font(18, bold=True)
        chip_w = max(draw.textlength(top_text, font=chip_font), draw.textlength(bottom_text, font=time_font)) + 18
        draw.rectangle((px0 + 8, py0 + 8, px0 + 8 + chip_w, py0 + 52), fill=colors.get("date_chip", colors["chip"]),
                       outline=colors["outline"])
        ink = colors.get("date_ink", colors["chip_ink"])
        draw.text((px0 + 8 + (chip_w - draw.textlength(top_text, font=chip_font)) / 2, py0 + 11), top_text, fill=ink,
                  font=chip_font)
        draw.text((px0 + 8 + (chip_w - draw.textlength(bottom_text, font=time_font)) / 2, py0 + 26), bottom_text,
                  fill=ink, font=time_font)
        tags = []
        tour = (event.extra or {}).get("tour_dates") or 0
        if tour >= 5:
            tags.append((f"巡演 {tour} 场", colors["outline"], colors["paper"]))
        status = STATUS_LABELS.get((event.status or "onsale").lower(), (event.status or "").upper())
        if status:
            tags.append((status, colors.get("status_chip", colors["chip"]), colors.get("status_ink", colors["chip_ink"])))
        tag_x = px1 - 8
        for text, fill, color in tags:
            tag_w = draw.textlength(text, font=chip_font) + 14
            draw.rectangle((tag_x - tag_w, py0 + 8, tag_x, py0 + 28), fill=fill)
            draw.text((tag_x - tag_w + 7, py0 + 10), text, fill=color, font=chip_font)
            tag_x -= tag_w + 6
        # Where, then the facts as chips.
        y = py0 + POSTER_H + 10
        place_font = self._font(15, bold=True)
        draw.text((px0, y), self._fit_text(draw, self._place_line(event), place_font, width), fill=colors["ink"],
                  font=place_font)
        y += 24
        fact_font = self._font(12, bold=True)
        x = px0
        facts = [(event.price.replace("From ", "") if event.price else "", colors["accent"], True),
                 (self._kind_text(event), self._event_color(event, colors), False),
                 (event.distance, colors["muted"], False), (self._more_text(event), colors["muted"], False)]
        for text, colour, solid in facts:
            if not text:
                continue
            text_w = draw.textlength(text, font=fact_font) + 14
            if x + text_w > px1:
                break
            if solid:
                draw.rounded_rectangle((x, y, x + text_w, y + 20), radius=10, fill=colour)
                draw.text((x + 7, y + 2), text, fill=colors["paper"], font=fact_font)
            else:
                draw.rounded_rectangle((x, y, x + text_w, y + 20), radius=10, outline=colour, width=2)
                draw.text((x + 7, y + 2), text, fill=colors["ink"], font=fact_font)
            x += text_w + 6

    @staticmethod
    def _place_line(event):
        """'Venue · City' (the state is left out: everything is within driving range)."""
        return " · ".join(part for part in (event.venue_name or "地点待定", event.city) if part)

    def _kind_text(self, event):
        segment = SEGMENT_LABELS.get((event.segment or "").lower(), event.segment or "")
        genre = event.genre if event.genre and event.genre.lower() != (event.segment or "").lower() else ""
        return " / ".join(part for part in (segment, genre) if part)

    @staticmethod
    def _more_text(event):
        more = (event.extra or {}).get("more_showtimes")
        return f"+{more} 场" if more else ""

    # -- public sales opening soon (or the week's mix), in the featured card's foot ----------
    def _draw_on_sale(self, draw, on_sale, days, tz, colors):
        x0, _y0, x1, y1 = HERO
        x, right = x0 + 8, x1 - 8
        top = HERO[1] + 8 + POSTER_H + 66
        head_font, row_font = self._font(13, bold=True), self._font(12, bold=True)
        for dot in range(x, right, 5):
            draw.point((dot, top - 6), fill=colors["line"])
        if on_sale:
            draw.text((x, top), "即将开售", fill=colors["accent"], font=head_font)
            draw.text((x + 62, top + 2), "公开售票开始", fill=colors["muted"], font=self._font(11, bold=True))
            y = top + 21
            for item in on_sale[:4]:
                if y + 16 > y1 - 4:
                    break
                sale = self._parse_datetime(item.get("sale_start"))
                sale = sale.astimezone(tz) if sale and tz else sale
                when = f"{sale:%m/%d %H:%M}" if sale else ""
                draw.ellipse((x, y + 5, x + 7, y + 12), fill=self._segment_color(item.get("segment"), colors))
                when_w = draw.textlength(when, font=row_font)
                draw.text((x + 11, y), when, fill=colors["ink"], font=row_font)
                draw.text((x + 17 + when_w, y), self._fit_text(draw, item.get("title") or "", row_font,
                                                               right - x - 17 - when_w),
                          fill=colors["muted"], font=row_font)
                y += 19
            return
        # No public sale opens this week: the week's mix of kinds instead.
        totals = {}
        for day in days:
            for key, count in (day.get("segments") or {}).items():
                totals[key] = totals.get(key, 0) + count
        draw.text((x, top), "本周类别", fill=colors["accent"], font=head_font)
        whole = sum(totals.values())
        if not whole:
            return
        self._draw_mix_bar(draw, totals, (x, top + 22, right, top + 30), colors)
        cursor, y = x, top + 38
        for key, label in SEGMENT_LABELS.items():
            if not totals.get(key):
                continue
            text = f"{label} {totals[key] * 100 // whole}%"
            text_w = draw.textlength(text, font=row_font)
            if cursor + text_w + 14 > right:
                cursor, y = x, y + 18
            draw.ellipse((cursor, y + 5, cursor + 7, y + 12), fill=self._segment_color(key, colors))
            draw.text((cursor + 10, y), text, fill=colors["muted"], font=row_font)
            cursor += text_w + 22

    # -- the next events -----------------------------------------------------------------
    def _draw_next(self, image, draw, events, today, colors):
        x0, y0, x1, _y1 = LIST
        x0, x1 = x0 + 10, x1 - 10
        head_font = self._font(16, bold=True)
        draw.text((x0, y0 + 6), "接下来", fill=colors.get("title", colors["ink"]), font=head_font)
        tonight = sum(1 for event in events if _day(event.local_date) == today)
        note = (f"今天还有 {tonight} 场 · " if tonight else "") + "按开始时间"
        note_font = self._font(11, bold=True)
        draw.text((x1 - draw.textlength(note, font=note_font), y0 + 10), note, fill=colors["muted"], font=note_font)
        day_font, time_font = self._font(11, bold=True), self._font(17, bold=True)
        title_font, meta_font, price_font = self._font(15, bold=True), self._font(11, bold=True), self._font(13, bold=True)
        previous_day = None
        for index, event in enumerate(events[:LIST_ROWS]):
            y = y0 + 32 + index * ROW_H
            if index:
                for dot in range(x0, x1, 5):
                    draw.point((dot, y - 4), fill=colors["line"])
            draw.rectangle((x0, y + 1, x0 + 3, y + ROW_H - 8), fill=self._event_color(event, colors))
            day = _day(event.local_date)
            label = day_label(day, today) if day and (day - today).days < 7 else (f"{day:%m/%d}" if day else "待定")
            # The day is said once per run of rows on it.
            if label != previous_day:
                draw.text((x0 + 9, y), label, fill=colors["accent"] if label != "今天" else colors["muted"], font=day_font)
            previous_day = label
            draw.text((x0 + 9, y + 14), clock(event.local_time) or "全天", fill=colors["ink"], font=time_font)
            thumb_x = x0 + 60
            self._paste_event_image(image, event, (thumb_x, y, THUMB[0], THUMB[1]), colors)
            text_x = thumb_x + THUMB[0] + 9
            price = event.price.replace("From ", "") if event.price else ""
            price_w = draw.textlength(price, font=price_font) if price else 0
            if price:
                draw.text((x1 - price_w, y + 1), price, fill=colors["accent"], font=price_font)
            draw.text((text_x, y - 1), self._fit_text(draw, event.title, title_font, x1 - text_x - price_w - 8),
                      fill=colors["ink"], font=title_font)
            more = self._more_text(event)
            more_w = draw.textlength(more, font=meta_font) + 12 if more else 0
            meta = " · ".join(part for part in (self._place_line(event), event.distance) if part)
            draw.text((text_x, y + 21), self._fit_text(draw, meta, meta_font, x1 - text_x - more_w - 4),
                      fill=colors["muted"], font=meta_font)
            if more:
                draw.rounded_rectangle((x1 - more_w, y + 20, x1, y + 35), radius=7, outline=colors["outline"])
                draw.text((x1 - more_w + 6, y + 21), more, fill=colors["ink"], font=meta_font)
        if not events:
            draw.text((x0, y0 + 40), "近期没有更多活动", fill=colors["muted"], font=title_font)

    # -- the week along the foot ----------------------------------------------------------
    def _draw_week_band(self, draw, days, today, colors):
        """Seven day cells: the day and date, its count, and a bar of its mix of kinds; today
        outlined, the weekend's names in the accent colour."""
        x0, y0, x1, y1 = WEEK_BAND
        cell_w = (x1 - x0 - 16) / 7
        label_font, count_font = self._font(11, bold=True), self._font(17, bold=True)
        by_date = {day.get("date"): day for day in days}
        for index in range(7):
            current = today + timedelta(days=index)
            day = by_date.get(current.isoformat()) or {}
            left = round(x0 + 8 + index * cell_w)
            right = round(left + cell_w - 6)
            if index == 0:
                draw.rounded_rectangle((left - 4, y0 + 3, right + 2, y1 - 3), radius=5, outline=colors["accent"], width=2)
            label = f"{day_label(current, today)} {current:%m/%d}"
            draw.text((left, y0 + 5), label, fill=colors["accent"] if current.weekday() >= 5 else colors["muted"],
                      font=label_font)
            count = day.get("count")
            text = f"{count:,} 场" if isinstance(count, int) else "–"
            draw.text((right - draw.textlength(text, font=count_font), y0 + 16), text, fill=colors["ink"],
                      font=count_font)
            self._draw_mix_bar(draw, day.get("segments") or {}, (left, y1 - 9, right - 50, y1 - 6), colors)

    def _draw_mix_bar(self, draw, segments, box, colors):
        x0, y0, x1, y1 = box
        total = sum(segments.values())
        if not total or x1 <= x0:
            draw.rectangle(box, fill=colors["line"])
            return
        x = x0
        for key in SEGMENT_LABELS:
            share = segments.get(key, 0) / total
            if share <= 0:
                continue
            right = x1 if key == list(SEGMENT_LABELS)[-1] else x + (x1 - x0) * share
            draw.rectangle((round(x), y0, max(round(x), round(right) - 1), y1), fill=self._segment_color(key, colors))
            x = right

    def _segment_color(self, key, colors):
        return (colors.get("segment_colors") or {}).get(key, colors.get("accent", colors["line"]))
