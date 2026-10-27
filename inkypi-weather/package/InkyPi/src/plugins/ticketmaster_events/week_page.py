"""The Local Events page: the featured event first, what is next beside it, the week below.

800 x 480, by importance: the featured event (the biggest tour starting soon) largest, its
poster over its headliner and the rest of the bill, its place, price and kind under them with
the public sales opening this week; beside it a notice board of the next seven events by start time, one slip each with its
kind's colour and sticker, time, poster, title, price tag, place, distance and what to know
before buying; along the foot the next seven days' counts and mix of kinds. The page lies on
the featured poster, blurred and washed into the paper, so its colour follows what is on, and
the cards are frosted over it so their text keeps its contrast.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.theme_utils import contrast_ratio, relative_luminance

WEEKDAYS = "一二三四五六日"
SEGMENT_LABELS = {"music": "音乐", "sports": "体育", "arts & theatre": "戏剧", "film": "电影", "miscellaneous": "其他"}
STATUS_LABELS = {"onsale": "售票中", "offsale": "停售", "postponed": "延期", "rescheduled": "改期"}
# Each kind's Chinese name and sticker (genre_icons/<icon>.png), by Ticketmaster sub-genre or
# genre; a kind not named here takes its segment's.
GENRE_ICON_DIR = Path(__file__).with_name("genre_icons")
# Ticketmaster's wordmark (tools/build_source_logos.py), this tall before the header's weekly count.
TICKETMASTER_LOGO_PATH = Path(__file__).with_name("assets") / "ticketmaster.png"
TICKETMASTER_LOGO_HEIGHT = 18
# Quiet show doodles in the featured event's kind colour under the featured card.
DOODLE_PATH = Path(__file__).with_name("assets") / "doodles" / "events.png"
# The header's kind legend uses at most two balanced rows between the wordmark and source.
# Visible kinds sharing a colour are named together; every member keeps its statistics key.
LEGEND_SIZE, LEGEND_LEFT, LEGEND_GAP = 13, 276, 8
OTHER_KINDS, UNCOUNTED = "__other_kinds__", "__uncounted__"
GENRE_KINDS = {
    "rock": ("摇滚", "guitar"), "metal": ("金属", "guitar"), "alternative": ("另类", "guitar"), "punk": ("朋克", "guitar"),
    "pop": ("流行", "microphone"), "r&b": ("R&B", "microphone"), "ballads/romantic": ("情歌", "microphone"),
    "religious": ("宗教", "microphone"), "lecture/seminar": ("讲座", "microphone"),
    "hip-hop/rap": ("说唱", "turntable"), "dance/electronic": ("电子", "turntable"),
    "jazz": ("爵士", "saxophone"), "blues": ("布鲁斯", "saxophone"),
    "classical": ("古典", "violin"), "opera": ("歌剧", "violin"), "new age": ("新世纪", "violin"),
    "country": ("乡村", "country"), "folk": ("民谣", "country"), "rodeo": ("牛仔竞技", "country"),
    "latin": ("拉丁", "maracas"), "world": ("世界音乐", "maracas"), "reggae": ("雷鬼", "maracas"),
    "children's music": ("儿童", "balloons"), "children's theatre": ("儿童剧", "balloons"),
    "family": ("亲子", "balloons"), "holiday": ("节日", "balloons"), "puppetry": ("木偶", "balloons"),
    "basketball": ("篮球", "basketball"), "hockey": ("冰球", "hockey"), "football": ("橄榄球", "football"),
    "rugby": ("橄榄球", "football"), "baseball": ("棒球", "baseball"), "softball": ("垒球", "baseball"),
    "soccer": ("足球", "soccer"), "boxing": ("拳击", "boxing"), "wrestling": ("摔角", "boxing"),
    "martial arts": ("格斗", "boxing"), "mixed martial arts": ("综合格斗", "boxing"),
    "motorsports/racing": ("赛车", "racing"), "ice shows": ("冰演", "skate"),
    "theatre": ("戏剧", "masks"), "musical": ("音乐剧", "masks"), "miscellaneous theatre": ("戏剧", "masks"),
    "cultural": ("文化", "masks"), "performance art": ("表演艺术", "masks"),
    "comedy": ("喜剧", "comedy"), "dance": ("舞蹈", "ballet"), "ballet": ("芭蕾", "ballet"),
    "circus & specialty acts": ("马戏", "circus"), "spectacular": ("大秀", "circus"), "variety": ("综艺", "circus"),
    "magic & illusion": ("魔术", "magic"), "fairs & festivals": ("节庆", "ferris"),
    "hobby/special interest expos": ("展会", "ferris"), "food & drink": ("美食", "ferris"),
    "tennis": ("网球", "ticket"), "golf": ("高尔夫", "ticket"), "volleyball": ("排球", "ticket"),
    "esports": ("电竞", "ticket"),
}
SEGMENT_KINDS = {"music": ("音乐", "microphone"), "sports": ("体育", "ticket"), "arts & theatre": ("戏剧", "masks"),
                 "film": ("电影", "film"), "miscellaneous": ("其他", "ticket")}
# Specific musical kinds need their own visual identity even when every row is Music.
# These colours belong to named genres, never to words guessed from an artist's title.
KIND_COLOR_KEYS = {
    "pop": "pop", "r&b": "pop", "ballads/romantic": "pop",
    "hip-hop/rap": "rap", "dance/electronic": "electronic", "alternative": "alternative",
    "rock": "rock", "metal": "rock", "punk": "rock", "comedy": "comedy",
}
KIND_COLORS = {
    "color": {"pop": (160, 48, 80), "rap": (125, 77, 9), "electronic": (39, 87, 152),
              "alternative": (40, 102, 67), "rock": (155, 61, 40), "comedy": (120, 65, 137)},
    "dark": {"pop": (235, 139, 169), "rap": (229, 188, 88), "electronic": (123, 175, 232),
             "alternative": (123, 194, 148), "rock": (238, 136, 102), "comedy": (190, 153, 218)},
}
# A sticker's dark outline (below ICON_OUTLINE_LUMINANCE) turns to the ink where it would vanish
# into the paper (night).
ICON_MIN_CONTRAST, ICON_OUTLINE_LUMINANCE = 2.0, 0.18
# A tour with at least this many dates is called out as one.
TOUR_CALLOUT_DATES = 10
MARGIN, HEADER_H = 16, 54
CARD_TOP, CARD_BOTTOM = 64, 420
HERO = (MARGIN, CARD_TOP, 324, CARD_BOTTOM)
# The featured card, one kind of fact per tier: the poster with the when and the sale on it;
# the headliner as the title (TITLE_SIZES, two lines at most) beside the kind's colour, the rest
# of the bill as the subtitle; the place and distance by a pin; the facts as chips; then, past
# a dotted rule, the week's sales or mix of kinds, kept SECTION_H. The poster takes the room
# the text leaves, POSTER_H to POSTER_MAX tall.
POSTER_H, POSTER_MAX, SECTION_H = 140, 172, 60
TITLE_SIZES, SUBTITLE_SIZE = (21, 19, 17), 14
# A title's headliner and the rest of the bill part at ' - ', ': ', '|', 'presents', or before
# 'w/' / 'with' / 'feat.' (which stay on the subtitle).
TITLE_BREAK = re.compile(r"\s+[-–—|]\s+|:\s+|\s+presents\s+|\s+(?=(?:w/|with|feat\.?|ft\.?)\s)", re.IGNORECASE)
# A bill that goes on to an age limit ('Ages 14+ (No children under 5…)', '21+', 'All Ages')
# has no subtitle: the limit is a fact for the chips.
AGE_NOTICE = re.compile(r"(?:ages?\s*(\d+)\s*\+|(\d+)\s*\+|(all\s+ages))(?:\W|$)", re.IGNORECASE)
SOLD_OUT_NOTICE = re.compile(r"(?:\(\s*SOLD\s+OUT\s*\)|\[\s*SOLD\s+OUT\s*\])", re.IGNORECASE)


def age_notice(text):
    """'14+' / '全龄' when ``text`` opens with an age limit, else ""."""
    match = AGE_NOTICE.match(str(text or "").strip())
    if not match:
        return ""
    return "全龄" if match.group(3) else f"{match.group(1) or match.group(2)}+"
LIST = (336, CARD_TOP, 800 - MARGIN, CARD_BOTTOM)
# Seven rows keep a consistent time column, a poster/type tile, and two spacious text lines.
ROW_H, LIST_ROWS = 47, 7
SLIP_TOP, SLIP_H = 28, 45
THUMB = (52, 29)
STICKER = 21
SLIP_PLACE_MIN, SLIP_FACT_GAP = 72, 8
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


def kind_key(event):
    """Known sub-genre, genre, then segment, with film kept in its own category."""
    return kind_key_from_names(event.segment, event.genre, (event.extra or {}).get("subgenre"))


def kind_key_from_names(segment, genre="", subgenre=""):
    segment = (segment or "").strip().casefold()
    if segment == "film":
        return segment
    for name in (subgenre, genre):
        key = (name or "").strip().casefold()
        if key in GENRE_KINDS:
            return key
    return segment


def category_key(segment, genre="", subgenre=""):
    """Persist the source segment with the resolved kind, so fallback colours stay identical."""
    return f"{(segment or '').strip().casefold()}|{kind_key_from_names(segment, genre, subgenre)}"


def event_category(event):
    return category_key(event.segment, event.genre, (event.extra or {}).get("subgenre"))


def category_label(key):
    if key in {OTHER_KINDS, UNCOUNTED}:
        return "其他类型" if key == OTHER_KINDS else "未统计"
    _segment, _separator, kind = key.partition("|")
    return GENRE_KINDS.get(kind, SEGMENT_KINDS.get(kind, ("未分类", "ticket")))[0]


def category_color(key, colors):
    if key == OTHER_KINDS:
        return colors["muted"]
    if key == UNCOUNTED:
        return colors["line"]
    if key == "|":
        return tuple(round((line + ink) / 2) for line, ink in zip(colors["line"], colors["muted"]))
    segment, _separator, kind = key.partition("|")
    specific = KIND_COLORS.get(colors["mode"], {}).get(KIND_COLOR_KEYS.get(kind))
    return specific or (colors.get("segment_colors") or {}).get(segment, colors.get("accent", colors["line"]))


def sampled_mix(days, legend_keys):
    """Counts on the true total: unlisted kinds and unobserved events remain explicit bins."""
    total = sum(day["count"] for day in days if isinstance(day.get("count"), int))
    sampled, counts = 0, {}
    for day in days:
        kinds = day.get("kind_counts")
        if not isinstance(kinds, dict):
            continue
        sampled += min(day.get("sample_count", sum(kinds.values())), day.get("count") or 0)
        for key, count in kinds.items():
            shown = key if key in legend_keys or key == "|" else OTHER_KINDS
            counts[shown] = counts.get(shown, 0) + count
    if total > sampled:
        counts[UNCOUNTED] = total - sampled
    return counts, sampled, total


def kind_of(event):
    """Chinese name and sticker from the same classification used by its colour."""
    key = kind_key(event)
    return GENRE_KINDS.get(key, SEGMENT_KINDS.get(key, ("", "ticket")))


def chip_ink(fill, colors):
    """Keep small type readable even on a mid-tone category colour."""
    ink = max((colors["paper"], colors["ink"]), key=lambda candidate: contrast_ratio(candidate, fill))
    if contrast_ratio(ink, fill) < 4.5:
        ink = max(((0, 0, 0), (255, 255, 255)), key=lambda candidate: contrast_ratio(candidate, fill))
    return ink


def title_parts(title):
    """(title, subtitle): the headliner and the rest of the bill ('Lenny Pearce', 'Toddler Rave
    World Tour'), or the whole title and "" when it names nothing more."""
    text = str(title or "").strip()
    match = TITLE_BREAK.search(text)
    if match and match.start() >= 2 and len(text) - match.end() >= 3:
        return text[:match.start()].strip(), text[match.end():].strip()
    return text, ""


def price_label(price, lowest=False):
    """'$45 起', '$45–129' or '免费' from the stored price ('From $45', '$45-129'); "" for none.
    ``lowest`` gives a range as its lowest price ('$45 起')."""
    text = str(price or "").strip()
    starting = text.startswith("From ")
    text = text.removeprefix("From ").strip()
    if not text:
        return ""
    figures = re.findall(r"\d+(?:\.\d+)?", text)
    if figures and all(float(figure) == 0 for figure in figures):
        return "免费"
    if lowest and "-" in text:
        return f"{text.split('-', 1)[0]} 起"
    return f"{text} 起" if starting else text.replace("-", "–")


@lru_cache(maxsize=4)
def _ticketmaster_logo(height, ink=None):
    """Ticketmaster's wordmark (assets/ticketmaster.png, black) ``height`` tall, in ``ink`` when
    given (a dark page); None without the file."""
    try:
        with Image.open(TICKETMASTER_LOGO_PATH) as source:
            mark = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    mark = mark.resize((max(1, round(mark.width * height / mark.height)), height), Image.Resampling.LANCZOS)
    if ink is not None:
        tinted = Image.new("RGBA", mark.size, (*ink, 255))
        tinted.putalpha(mark.getchannel("A"))
        mark = tinted
    return mark


@lru_cache(maxsize=64)
def _genre_icon(name, size, ground, ink):
    """A kind's sticker ``size`` px on its longer side, its dark outlines in ``ink`` where they
    would vanish on ``ground`` (its pale fills keep their colour); None when the file is missing."""
    try:
        with Image.open(GENRE_ICON_DIR / f"{name}.png") as source:
            art = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    art.thumbnail((size, size), Image.Resampling.LANCZOS)
    pixels = art.load()
    for y in range(art.height):
        for x in range(art.width):
            red, green, blue, alpha = pixels[x, y]
            colour = (red, green, blue)
            if (alpha >= 8 and relative_luminance(colour) < ICON_OUTLINE_LUMINANCE
                    and contrast_ratio(colour, ground) < ICON_MIN_CONTRAST):
                pixels[x, y] = (*ink, alpha)
    return art


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
        legend_keys = self._draw_page_header(image, draw, settings, generated_at, stale, days, colors, events)
        for box in (HERO, LIST, WEEK_BAND):
            self._frost(image, box, colors)
        tint = self._event_color(events[0], colors) if events else colors["accent"]
        paint_doodles(image, (HERO[0] + 1, HERO[1] + 1, HERO[2] - 1, HERO[3] - 1), DOODLE_PATH, tint, radius=5)
        foot = self._draw_featured(image, draw, events[0], today, colors) if events else HERO[1] + 8 + POSTER_H
        self._draw_on_sale(draw, on_sale or [], days, getattr(generated_at, "tzinfo", None), colors, foot + 16, legend_keys)
        self._draw_next(image, draw, events[1:1 + LIST_ROWS], today, colors,
                        now=generated_at if isinstance(generated_at, datetime) else None)
        self._draw_week_band(draw, days, today, colors, legend_keys)
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
    def _draw_page_header(self, image, draw, settings, generated_at, stale, days, colors, events=()):
        right = 800 - MARGIN
        wordmark_bottom = self._draw_header_wordmark(image, draw, MARGIN + 2, 4, 250, 44, colors)
        if wordmark_bottom is None:
            draw.text((MARGIN, 10), "LOCAL EVENTS", fill=colors.get("title", colors["ink"]), font=self._font(28, bold=True))
        counted = [day["count"] for day in days if isinstance(day.get("count"), int)]
        total_font, small = self._font(22, bold=True), self._font(13, bold=True)
        headline = f"本周 {sum(counted):,} 场" if counted else "本地活动"
        headline_left = right - draw.textlength(headline, font=total_font)
        draw.text((headline_left, 3), headline, fill=colors.get("accent", colors["ink"]), font=total_font)
        # The listings' source by Ticketmaster's own wordmark, before the week's count.
        mark = _ticketmaster_logo(TICKETMASTER_LOGO_HEIGHT, None if colors["mode"] != "dark" else tuple(colors["ink"]))
        block_left = headline_left
        if mark is not None:
            block_left = round(headline_left) - 14 - mark.width
            image.paste(mark, (block_left, 17 - mark.height // 2), mark)
        when = generated_at.strftime("%m/%d %H:%M") if isinstance(generated_at, datetime) else ""
        note = f"{self._place_label(settings)} · {'缓存 ' if stale else ''}{when} 更新"
        note_w = draw.textlength(note, font=small)
        draw.text((right - note_w, 32), note, fill=colors.get("header_muted", colors["muted"]), font=small)
        block_left = min(block_left, right - note_w)
        # The legend names the exact kinds shown below. Weekly kinds take any remaining room;
        # unlisted weekly kinds are grouped neutrally and labelled beside the distribution.
        visible = list(dict.fromkeys(event_category(event) for event in events))
        weekly = {}
        for day in days:
            for key, count in (day.get("kind_counts") or {}).items():
                weekly[key] = weekly.get(key, 0) + count
        keys = list(visible)
        groups = {}
        for key in visible:
            labels = groups.setdefault(category_color(key, colors), {})
            labels.setdefault(category_label(key), []).append(key)
        for key in sorted(weekly, key=weekly.get, reverse=True):
            if key in keys:
                continue
            colour, label = category_color(key, colors), category_label(key)
            # A fallback main-category colour must not acquire a second meaning in the legend.
            if colour in groups and label not in groups[colour]:
                continue
            keys.append(key)
            groups.setdefault(colour, {}).setdefault(label, []).append(key)
        left, room = LEGEND_LEFT, block_left - LEGEND_GAP - LEGEND_LEFT
        legend_font = self._font(LEGEND_SIZE, bold=True)
        for size in (LEGEND_SIZE, 12, 11):
            legend_font = self._font(size, bold=True)
            items = []
            for colour, labels in groups.items():
                words, members = [], []
                for label, label_keys in labels.items():
                    joined = " / ".join([*words, label])
                    if words and 12 + draw.textlength(joined, font=legend_font) > room:
                        text = " / ".join(words)
                        items.append((members, text, colour, 12 + draw.textlength(text, font=legend_font)))
                        words, members = [], []
                    words.append(label)
                    members.extend(label_keys)
                text = " / ".join(words)
                items.append((members, text, colour, 12 + draw.textlength(text, font=legend_font)))
            rows, used = [[]], 0
            for item in items:
                width = item[3]
                if used + width > room and rows[-1]:
                    if len(rows) == 2:
                        break
                    rows.append([])
                    used = 0
                rows[-1].append(item)
                used += width + 7
            included = {key for row in rows for members, _text, _colour, _width in row for key in members}
            shown = [key for key in keys if key in included]
            if all(key in shown for key in visible):
                break
        if len(rows) == 2:
            items = [item for row in rows for item in row]
            def row_width(items):
                return sum(item[3] for item in items) + max(0, len(items) - 1) * 7
            splits = [index for index in range(1, len(items))
                      if max(row_width(items[:index]), row_width(items[index:])) <= room]
            if splits:
                split = min(splits, key=lambda index: abs(row_width(items[:index]) - row_width(items[index:])))
                rows = [items[:split], items[split:]]
        for index, row in enumerate(rows):
            x, middle = left, 16 + index * 23 if len(rows) > 1 else 27
            for _members, text, colour, width in row:
                draw.ellipse((x, middle - 4, x + 8, middle + 4), fill=colour)
                draw.text((x + 12, middle), text, fill=colors.get("header_muted", colors["muted"]),
                          font=legend_font, anchor="lm")
                x += width + 7
        return shown

    def _place_label(self, settings):
        radius = self._bounded_int(settings.get("radiusMiles"), 50, 1, 250)
        postal = self._postal_code(settings)
        if postal:
            return f"{postal} 周边 {radius} 英里"
        return self._location_label(settings)

    # -- the featured event ---------------------------------------------------------------
    def _draw_featured(self, image, draw, event, today, colors):
        """The featured event in tiers, each holding one kind of fact: the poster with the day and
        time on a gold chip and the tour's size and sale status on its top right; the headliner as
        the title beside the kind's colour, the rest of the bill under it; the place by a pin with
        the distance at the right; the price, kind, age and repeats as chips. Returns the y where
        the card's text ends."""
        x0, y0, x1, _y1 = HERO
        pad = 8
        px0, py0, px1 = x0 + pad, y0 + pad, x1 - pad
        width = px1 - px0
        kind_colour = self._event_color(event, colors)
        # The title's lines are set first: the poster takes the height the text leaves it.
        title, subtitle = title_parts(event.title)
        age = (event.extra or {}).get("age") or ""
        if age_notice(subtitle):
            age, subtitle = age or age_notice(subtitle), ""
        text_x, text_w = px0 + 10, width - 10
        title_font = self._font(TITLE_SIZES[-1], bold=True)
        for size in TITLE_SIZES:
            title_font = self._font(size, bold=True)
            if len(self._wrap_full_text(draw, title, title_font, text_w)) <= 2:
                break
        lines = self._wrap_text(draw, title, title_font, text_w, 2)
        text_h = 10 + len(lines) * (title_font.size + 4) + (SUBTITLE_SIZE + 6 if subtitle else 0) + 47
        poster_h = max(POSTER_H, min(POSTER_MAX, HERO[3] - 8 - SECTION_H - 16 - py0 - text_h))
        poster = self._load_event_image(event)
        art = ImageOps.fit((poster or self._placeholder_event_image(event, (width, poster_h), colors)).convert("RGB"),
                           (width, poster_h), method=Image.Resampling.LANCZOS)
        image.paste(art, (px0, py0))
        draw.rectangle((px0, py0 + poster_h, px1, py0 + poster_h + 3), fill=kind_colour)
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
        sold_out = bool(SOLD_OUT_NOTICE.search(event.title or ""))
        source_status = (event.status or "onsale").lower()
        status = STATUS_LABELS.get(source_status, (event.status or "").upper())
        if sold_out and source_status in {"onsale", "offsale"}:
            status = "售罄"
        if status:
            tags.append((status, colors.get("status_chip", colors["chip"]), colors.get("status_ink", colors["chip_ink"])))
        tag_x = px1 - 8
        for text, fill, color in tags:
            tag_w = draw.textlength(text, font=chip_font) + 14
            draw.rectangle((tag_x - tag_w, py0 + 8, tag_x, py0 + 28), fill=fill)
            draw.text((tag_x - tag_w + 7, py0 + 10), text, fill=color, font=chip_font)
            tag_x -= tag_w + 6
        # What: the headliner, then the rest of the bill, beside a bar of the kind's colour.
        block_top = y = py0 + poster_h + 10
        for line in lines:
            draw.text((text_x, y), line, fill=colors.get("title", colors["ink"]), font=title_font)
            y += title_font.size + 4
        if subtitle:
            subtitle_font = self._font(SUBTITLE_SIZE, bold=True)
            draw.text((text_x, y + 1), self._fit_text(draw, subtitle, subtitle_font, text_w), fill=colors["muted"],
                      font=subtitle_font)
            y += SUBTITLE_SIZE + 6
        draw.rectangle((px0, block_top + 2, px0 + 3, y - 4), fill=kind_colour)
        # Where: a pin, the place, and the distance at the right.
        y += 5
        meta_font = self._font(13, bold=True)
        distance = event.distance or ""
        distance_w = draw.textlength(distance, font=meta_font) if distance else 0
        if distance:
            draw.text((px1 - distance_w, y), distance, fill=colors["muted"], font=meta_font)
        self._draw_pin(draw, (px0 + 5, y + 8), colors["accent"], colors["paper"])
        place_x = px0 + 14
        draw.text((place_x, y), self._fit_text(draw, self._place_line(event), meta_font,
                                                px1 - place_x - (distance_w + 10 if distance else 0)),
                  fill=colors["ink"], font=meta_font)
        y += 22
        # The facts as chips: the price solid, the kind edged in its colour.
        fact_font = self._font(12, bold=True)
        x = px0
        facts = [("" if sold_out else price_label(event.price), colors["accent"], True),
                 (self._kind_text(event), kind_colour, False),
                 (age, colors["muted"], False),
                 (self._more_text(event), colors["muted"], False)]
        drawn = False
        for text, colour, solid in facts:
            if not text:
                continue
            chip = draw.textlength(text, font=fact_font) + 14
            if x + chip > px1:
                break
            if solid:
                draw.rounded_rectangle((x, y, x + chip, y + 20), radius=10, fill=colour)
                draw.text((x + 7, y + 2), text, fill=colors["paper"], font=fact_font)
            else:
                draw.rounded_rectangle((x, y, x + chip, y + 20), radius=10, outline=colour, width=2)
                draw.text((x + 7, y + 2), text, fill=colors["ink"], font=fact_font)
            x += chip + 6
            drawn = True
        return y + 20 if drawn else y - 4

    @staticmethod
    def _draw_pin(draw, tip, fill, hole):
        """A map pin ``tip`` its point: a round head over a point, with a hole."""
        x, y = tip
        draw.polygon(((x - 4, y - 5), (x + 4, y - 5), (x, y + 2)), fill=fill)
        draw.ellipse((x - 5, y - 13, x + 5, y - 3), fill=fill)
        draw.ellipse((x - 2, y - 10, x + 2, y - 6), fill=hole)

    @staticmethod
    def _place_line(event):
        """'Venue · City' (the state is left out: everything is within driving range); a venue
        named after its city says it once ('Fox Theater - Oakland' in Oakland: 'Fox Theater · Oakland')."""
        venue, city = event.venue_name or "地点待定", event.city or ""
        if city:
            short = re.sub(rf"\s*(?:[-–—,·]|\bat\b|\bin\b)\s*{re.escape(city)}$", "", venue, flags=re.IGNORECASE)
            venue = short if short and short != venue else venue
        return " · ".join(part for part in (venue, city) if part)

    def _kind_text(self, event):
        """'音乐 · 摇滚': the segment and the kind in Chinese (a genre with no Chinese name as
        Ticketmaster gives it); "" when Ticketmaster names neither."""
        segment = SEGMENT_LABELS.get((event.segment or "").lower(), event.segment or "")
        label, _icon = kind_of(event)
        if kind_key(event) == "comedy" and segment == "音乐":
            return label
        if label == segment:
            genre = event.genre if event.genre and event.genre.lower() != (event.segment or "").lower() else ""
            label = genre if genre and genre.casefold() != "other" else ""
        return " · ".join(part for part in (segment, label) if part)

    @staticmethod
    def _more_text(event):
        more = (event.extra or {}).get("more_showtimes")
        return f"+{more} 场" if more else ""

    # -- public sales opening soon (or the week's mix), in the featured card's foot ----------
    def _draw_on_sale(self, draw, on_sale, days, tz, colors, top, legend_keys=()):
        """Under a dotted rule from ``top``: the public sales opening this week, else the week's
        mix of kinds as a bar beside its heading with each kind's share under it."""
        x0, _y0, x1, y1 = HERO
        x, right = x0 + 8, x1 - 8
        head_font, row_font = self._font(13, bold=True), self._font(12, bold=True)
        for dot in range(x, right, 5):
            draw.point((dot, top - 8), fill=colors["line"])
        has_kinds = any(isinstance(day.get("kind_counts"), dict) for day in days)
        mix, sampled, total = sampled_mix(days, legend_keys)
        if on_sale:
            draw.text((x, top), "即将开售", fill=colors["accent"], font=head_font)
            if has_kinds:
                self._draw_mix_notes(draw, mix, (x + 65, top + 2, right), colors, show_counts=False)
            else:
                draw.text((x + 62, top + 2), "公开售票开始", fill=colors["muted"], font=self._font(11, bold=True))
            y = top + 21
            for item in on_sale[:4]:
                if y + 16 > y1 - 4:
                    break
                sale = self._parse_datetime(item.get("sale_start"))
                sale = sale.astimezone(tz) if sale and tz else sale
                when = f"{sale:%m/%d %H:%M}" if sale else ""
                kind = item.get("kind")
                colour = category_color(kind, colors) if kind in legend_keys else colors["muted"]
                draw.ellipse((x, y + 5, x + 7, y + 12), fill=colour)
                when_w = draw.textlength(when, font=row_font)
                draw.text((x + 11, y), when, fill=colors["ink"], font=row_font)
                draw.text((x + 17 + when_w, y), self._fit_text(draw, item.get("title") or "", row_font,
                                                               right - x - 17 - when_w),
                          fill=colors["muted"], font=row_font)
                y += 19
            return
        # No public sale opens this week: show coverage, never normalize a sample to all events.
        if has_kinds:
            draw.text((x, top), f"类型分布 · {sampled}/{total}场", fill=colors["accent"], font=head_font)
            self._draw_mix_bar(draw, mix, (x, top + 22, right, top + 29), colors, total=total)
            self._draw_mix_notes(draw, mix, (x, top + 37, right), colors)
            return
        # Earlier caches retained only main-category samples. Keep their counts neutral until
        # the regular refresh supplies kinds; visible events cannot reconstruct the whole week.
        totals = {}
        for day in days:
            for key, count in (day.get("segments") or {}).items():
                totals[key] = totals.get(key, 0) + count
        whole = sum(totals.values())
        draw.text((x, top), f"大类样本 · {whole}/{total}场", fill=colors["accent"], font=head_font)
        waiting = "类型待更新"
        waiting_font = self._font(11, bold=True)
        draw.text((right - draw.textlength(waiting, font=waiting_font), top + 2), waiting,
                  fill=colors["muted"], font=waiting_font)
        if not whole:
            return
        cursor, y = x, top + 22
        for key, label in SEGMENT_LABELS.items():
            if not totals.get(key):
                continue
            text = f"{label} {totals[key]}"
            text_w = draw.textlength(text, font=row_font)
            if cursor + text_w + 14 > right:
                cursor, y = x, y + 18
            draw.ellipse((cursor, y + 5, cursor + 7, y + 12), fill=colors["muted"])
            draw.text((cursor + 10, y), text, fill=colors["muted"], font=row_font)
            cursor += text_w + 22

    def _draw_mix_notes(self, draw, counts, box, colors, show_counts=True):
        """Label neutral bins locally even when the header is full of named event kinds."""
        x, y, right = box
        font = self._font(11, bold=True)
        for key in (OTHER_KINDS, "|", UNCOUNTED):
            if not counts.get(key):
                continue
            text = category_label(key) + (f" {counts[key]}" if show_counts else "")
            width = draw.textlength(text, font=font) + 13
            if x + width > right:
                x, y = box[0], y + 16
            draw.rectangle((x, y + 4, x + 7, y + 11), fill=category_color(key, colors))
            draw.text((x + 11, y), text, fill=colors["muted"], font=font)
            x += width + 10

    # -- the next events: a notice board, one slip per event --------------------------------
    def _draw_next(self, image, draw, events, today, colors, now=None):
        x0, y0, x1, _y1 = LIST
        x0, x1 = x0 + 8, x1 - 8
        head_font = self._font(16, bold=True)
        draw.text((x0 + 2, y0 + 5), "接下来", fill=colors.get("title", colors["ink"]), font=head_font)
        tonight = sum(1 for event in events if _day(event.local_date) == today)
        note = (f"今天还有 {tonight} 场 · " if tonight else "") + "按开始时间"
        note_font = self._font(11, bold=True)
        draw.text((x1 - 2 - draw.textlength(note, font=note_font), y0 + 9), note, fill=colors["muted"], font=note_font)
        previous_day = None
        for index, event in enumerate(events[:LIST_ROWS]):
            top = y0 + SLIP_TOP + index * ROW_H
            day = _day(event.local_date)
            label = day_label(day, today) if day and (day - today).days < 7 else (f"{day:%m/%d}" if day else "待定")
            # The day is said once per run of slips on it.
            self._draw_slip(image, draw, event, (x0, top, x1, top + SLIP_H), label if label != previous_day else "",
                            colors, now)
            previous_day = label
        if not events:
            draw.text((x0, y0 + 40), "近期没有更多活动", fill=colors["muted"], font=self._font(15, bold=True))

    def _draw_slip(self, image, draw, event, box, day_text, colors, now):
        """Time and type beside the title; location, full distance and facts on the second line."""
        sx0, sy0, sx1, sy1 = box
        kind_colour = self._event_color(event, colors)
        draw.rectangle(box, fill=colors["paper"])
        draw.line((sx0, sy1, sx1, sy1), fill=colors["line"])
        time_ink = colors["ink"]
        # The day appears once per chronological group, with a full rule at the boundary.
        if day_text:
            draw.line((sx0, sy0, sx1, sy0), fill=colors["muted"])
            draw.text((sx0 + 2, sy0 + 3), day_text, fill=time_ink, font=self._font(11, bold=True))
        draw.text((sx0 + 2, sy0 + 20), clock(event.local_time) or "全天", fill=time_ink, font=self._font(16, bold=True))
        # Colour, label and sticker all express the same provider classification.
        thumb_x, thumb_y = sx0 + 53, sy0 + 2
        tile_right = thumb_x + THUMB[0]
        draw.rectangle((thumb_x, thumb_y, tile_right + 4, sy0 + 43), fill=kind_colour)
        self._paste_event_image(image, event, (thumb_x, thumb_y, THUMB[0], THUMB[1]), colors)
        label, icon = kind_of(event)
        label = label or "未分类"
        label_font = self._font(11, bold=True)
        label = self._fit_text(draw, label, label_font, THUMB[0] - 4)
        draw.text((thumb_x + (THUMB[0] - draw.textlength(label, font=label_font)) / 2, sy0 + 31),
                  label, fill=chip_ink(kind_colour, colors), font=label_font)
        art = _genre_icon(icon, STICKER, tuple(colors["paper"]), tuple(colors["ink"]))
        if art is not None:
            image.paste(art, (tile_right - art.width + 4, sy0 + 10), art)
        # One category-colour contour encloses the neutral time and coloured poster/type tile.
        # Draw it last so neither the poster nor its sticker interrupts the outline.
        draw.rectangle((sx0, sy0 + 2, tile_right + 4, sy0 + 43), outline=kind_colour, width=1)
        text_x, right = tile_right + 9, sx1 - 3
        # The title has the full text-column width; prices no longer shorten the artist name.
        title_font = self._font(15, bold=True)
        draw.text((text_x, sy0 + 3), self._fit_text(draw, event.title, title_font, right - text_x),
                  fill=colors["ink"], font=title_font)
        # Reserve the complete distance and important facts before fitting the venue.
        meta_font = self._font(11, bold=True)
        distance = f" · {event.distance}" if event.distance else ""
        distance_w = draw.textlength(distance, font=meta_font)
        facts = self._slip_facts(event, now)
        price = "" if SOLD_OUT_NOTICE.search(event.title or "") else price_label(event.price, lowest=True)
        if price:
            facts.append((price, False))
        if not facts and (event.status or "").lower() == "onsale":
            facts.append(("售票中", False))
        shown, facts_w = [], 0
        for text, alarm in facts:
            fact_w = draw.textlength(text, font=meta_font) + 8
            if distance_w + SLIP_PLACE_MIN + facts_w + SLIP_FACT_GAP + fact_w > right - text_x:
                break
            shown.append((text, alarm, fact_w))
            facts_w += SLIP_FACT_GAP + fact_w
        room = right - text_x - distance_w - facts_w
        place = self._place_line(event)
        if draw.textlength(place, font=meta_font) > room:
            place = event.venue_name or place
        location = self._fit_text(draw, place, meta_font, max(room, 10)) + distance
        y = sy0 + 25
        draw.text((text_x, y), location, fill=colors["muted"], font=meta_font)
        x = text_x + draw.textlength(location, font=meta_font) + SLIP_FACT_GAP
        for text, alarm, fact_w in shown:
            fill = colors["accent"] if alarm else self._blend(colors["paper"], kind_colour, 0.12)
            fact_ink = chip_ink(fill, colors)
            draw.rounded_rectangle((x, y - 1, x + fact_w, y + 12), radius=2, fill=fill)
            draw.text((x + 4, y), text, fill=fact_ink, font=meta_font)
            x += fact_w + SLIP_FACT_GAP

    def _slip_facts(self, event, now):
        """[(text, alarm)] to know before buying, most important first: a sale stopped or a date
        moved (alarm), the public sale opening later, the age limit, more showtimes the same
        day, a big tour, a family show, several days."""
        extra = event.extra or {}
        facts = []
        # Some public listings retain onsale/$0 metadata after their title is marked SOLD OUT.
        # Only the explicit bracketed marker takes priority; an artist/title word never does.
        sold_out = bool(SOLD_OUT_NOTICE.search(event.title or ""))
        status = (event.status or "").lower()
        if status in STATUS_LABELS and status != "onsale":
            facts.append((STATUS_LABELS[status], True))
        if sold_out:
            facts.append(("售罄", True))
        sale = self._parse_datetime(extra.get("sale_start")) if extra.get("sale_start") else None
        if not sold_out and sale and now and sale > now:
            facts.append((f"{sale.astimezone(now.tzinfo):%m/%d} 开售" if now.tzinfo else f"{sale:%m/%d} 开售", False))
        if extra.get("age"):
            facts.append((extra["age"], False))
        if self._more_text(event):
            facts.append((self._more_text(event), False))
        if (extra.get("tour_dates") or 0) >= TOUR_CALLOUT_DATES:
            facts.append(("巡演", False))
        if extra.get("family") and kind_of(event)[0] not in {"亲子", "儿童", "儿童剧"}:
            facts.append(("亲子", False))
        if extra.get("multi_day"):
            facts.append(("多日", False))
        return facts

    # -- the week along the foot ----------------------------------------------------------
    def _draw_week_band(self, draw, days, today, colors, legend_keys=()):
        """Seven day cells: the day and date, its count, and a bar of its mix of kinds; today
        outlined, the weekend's names in the accent colour."""
        x0, y0, x1, y1 = WEEK_BAND
        cell_w = (x1 - x0 - 16) / 7
        label_font, count_font = self._font(11, bold=True), self._font(17, bold=True)
        by_date = {day.get("date"): day for day in days}
        max_count = max((day.get("count") or 0 for day in days), default=0)
        for index in range(7):
            current = today + timedelta(days=index)
            day = by_date.get(current.isoformat()) or {}
            left = round(x0 + 8 + index * cell_w)
            right = round(left + cell_w - 6)
            if index == 0:
                draw.rounded_rectangle((left - 4, y0 + 3, right + 2, y1 - 3), radius=5, outline=colors["accent"], width=1)
            label = f"{day_label(current, today)} {current:%m/%d}"
            draw.text((left, y0 + 5), label, fill=colors["accent"] if current.weekday() >= 5 else colors["muted"],
                      font=label_font)
            count = day.get("count")
            text = f"{count:,} 场" if isinstance(count, int) else "–"
            draw.text((right - draw.textlength(text, font=count_font), y0 + 16), text, fill=colors["ink"],
                      font=count_font)
            box = (left, y1 - 9, right - 50, y1 - 6)
            if isinstance(day.get("kind_counts"), dict):
                mix, _sampled, total = sampled_mix([day], legend_keys)
                self._draw_mix_bar(draw, mix, box, colors, total=total)
            else:
                # An old cache has no detailed kinds: a neutral quantity bar, not invented shares.
                draw.rectangle(box, fill=colors["line"])
                if isinstance(count, int) and count > 0 and max_count:
                    end = box[0] + (box[2] - box[0]) * count / max_count
                    draw.rectangle((box[0], box[1], end, box[3]), fill=colors["muted"])

    def _draw_mix_bar(self, draw, counts, box, colors, total=None):
        x0, y0, x1, y1 = box
        total = sum(counts.values()) if total is None else total
        if not total or x1 <= x0:
            draw.rectangle(box, fill=colors["line"])
            return
        x = x0
        for key, count in counts.items():
            share = count / total
            if share <= 0:
                continue
            right = min(x1, x + (x1 - x0) * share)
            draw.rectangle((round(x), y0, max(round(x), round(right) - 1), y1), fill=category_color(key, colors))
            x = right
