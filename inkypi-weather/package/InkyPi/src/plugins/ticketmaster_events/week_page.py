"""The Local Events page: the featured event first, what is next beside it, the week below.

800 x 480 on plain paper under quiet show doodles (faintest behind the list), the parts kept
apart by hairlines. The header names the page, then
Ticketmaster by its own wordmark, with the week's count, the area and the update time at the
right. The left column is the featured event (the biggest tour starting soon): its poster, when
it starts and its sale state, the headliner over the rest of the bill, its place and distance,
its price and kind; under it the public sales opening this week and the week's mix of kinds as
one stacked bar with a legend of the kinds counted. The right column lists the next seven events
by start time, one row each: the time, the kind's colour as a bar on the poster's edge, the
title, then the kind, place, distance and what to know before buying; under the list the next
seven days, each with its count and a bar of its mix whose length follows the count.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.local_wordmark import load_source_logo

from .team_logos import read_title, team_logo
from .venue_logos import paste_venue_logo, venue_is_wordmark
from utils.theme_utils import contrast_ratio, relative_luminance

WEEKDAYS = "一二三四五六日"
SEGMENT_LABELS = {"music": "音乐", "sports": "体育", "arts & theatre": "戏剧", "film": "电影", "miscellaneous": "其他"}
STATUS_LABELS = {"onsale": "售票中", "offsale": "停售", "postponed": "延期", "rescheduled": "改期"}
# Each kind's Chinese name and sticker (genre_icons/<icon>.png), by Ticketmaster sub-genre or
# genre; a kind not named here takes its segment's.
GENRE_ICON_DIR = Path(__file__).with_name("genre_icons")
# The header: the page's wordmark within HEADER_WORDMARK, then, SOURCE_LOGO_GAP on, Ticketmaster's
# own wordmark (tools/build_source_logos.py) TICKETMASTER_LOGO_HEIGHT tall.
HEADER_WORDMARK_PATH = Path(__file__).with_name("header_wordmark.png")
HEADER_WORDMARK, SOURCE_LOGO_GAP = (250, 44), 18
TICKETMASTER_LOGO_PATH = Path(__file__).with_name("assets") / "ticketmaster.png"
TICKETMASTER_LOGO_HEIGHT = 18
# The keyless sources (local_sources.py) are named the same way, by their own logos from their
# sites (assets/dothebay.png, assets/luma.png): after Ticketmaster's wordmark in the header while
# one of their events is listed (HEADER_SOURCE_HEIGHTS tall, and only where the logo ends
# HEADER_SOURCE_CLEAR before the summary at the right), and at the end of each of their rows'
# second line (SLIP_SOURCE_HEIGHTS tall, SLIP_SOURCE_GAP after the facts). DoTheBay's is an emblem,
# Luma's a wordmark: the emblem stands taller so the two read as one weight.
SOURCE_LOGO_DIR = Path(__file__).with_name("assets")
HEADER_SOURCE_HEIGHTS = {"dothebay": 28, "luma": 15}
SLIP_SOURCE_HEIGHTS = {"dothebay": 15, "luma": 11}
HEADER_SOURCE_CLEAR, SLIP_SOURCE_GAP = 12, 6
# A meetup says how many are going from this many guests up.
MEETUP_GUESTS_SHOWN = 30
# The mix legend under the stacked bar: its type size, the gap between entries, a row's height
# and the most rows it may take (one fewer when sales are listed above it).
LEGEND_SIZE, LEGEND_GAP, LEGEND_ROW_H, LEGEND_ROWS = 11, 9, 15, 3
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
    # Luma's meetups (local_sources.LUMA_KIND).
    "tech meetup": ("科技", "microphone"),
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
    "color": {"pop": (204, 62, 120), "rap": (140, 84, 10), "electronic": (0, 128, 136),
              "alternative": (18, 122, 82), "rock": (200, 84, 24), "comedy": (148, 52, 140)},
    "dark": {"pop": (235, 139, 169), "rap": (229, 188, 88), "electronic": (123, 175, 232),
             "alternative": (123, 194, 148), "rock": (238, 136, 102), "comedy": (190, 153, 218)},
}
# A sticker's dark outline (below ICON_OUTLINE_LUMINANCE) turns to the ink where it would vanish
# into the paper (night).
ICON_MIN_CONTRAST, ICON_OUTLINE_LUMINANCE = 2.0, 0.18
# A tour with at least this many dates is called out as one.
TOUR_CALLOUT_DATES = 10
MARGIN, HEADER_H = 16, 54
# Quiet show doodles in the featured event's kind colour lie on the paper under everything: sparse
# strokes at DOODLE_OPEN of their density in the open areas (the header, the featured column, the
# week), and at DOODLE_LIST behind the list, whose rows of small type they must not disturb.
DOODLE_PATH = Path(__file__).with_name("assets") / "doodles" / "events.png"
DOODLE_OPEN, DOODLE_LIST = 0.15, 0.07
# Two columns under the header's rule, a hairline at COLUMN_RULE between them: the featured event
# (HERO) and, beside it, the list (LIST) over the week (WEEK_BAND).
COLUMN_TOP, COLUMN_BOTTOM, COLUMN_RULE = 64, 474, 324
HERO = (MARGIN, COLUMN_TOP, 316, COLUMN_BOTTOM)
# The featured column, one kind of fact per tier: the poster; the when in the accent colour with
# the sale state at the right; the headliner as the title (TITLE_SIZES, two lines at most) beside
# the kind's colour, the rest of the bill as the subtitle; the place and distance by a pin; the
# facts as chips; then, SECTION_GAP on and ending at the column's foot, the week's sales and mix
# of kinds (SECTION_H when the page gives no other height). The poster takes the room the text
# leaves, POSTER_H to POSTER_MAX tall.
POSTER_H, POSTER_MAX, SECTION_H, SECTION_GAP, SECTION_PAD = 116, 190, 88, 12, 6
# The foot lists SALE_ROWS public sales at most, SALE_ROW_H each, over a MIX_BAR_H stacked bar.
SALE_ROWS, SALE_ROW_H, MIX_BAR_H = 2, 17, 10
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


LIST = (332, COLUMN_TOP, 800 - MARGIN, 420)
# Seven rows under the list's heading, a hairline over each: the time over the kind's name and
# sticker in TIME_COLUMN, the kind's colour as a KIND_BAR on the poster's left edge, the poster (THUMB), and
# two text lines.
ROW_H, LIST_ROWS = 47, 7
SLIP_TOP, SLIP_H = 27, 46
TIME_COLUMN, KIND_BAR, THUMB = 53, 5, (72, 41)
# A venue's official logo leads its place: within these boxes on a list row and in the lead card.
SLIP_VENUE_LOGO, HERO_VENUE_LOGO = (34, 13), (44, 16)
SLIP_VENUE_WORDMARK, HERO_VENUE_WORDMARK = (108, 14), (132, 18)
# A team's (or show's) logo leads its name in a title: as tall as the type plus TEAM_LOGO_RISE, at
# most TEAM_LOGO_WIDE times as wide, TEAM_LOGO_GAP before the name. A list row's title takes the
# first of SLIP_TITLE_SIZES that holds it, the teams by the first of NAME_FORMS that fits: as the
# title writes them, a school by its place ('San Jose State'), then each by its short name.
TEAM_LOGO_RISE, TEAM_LOGO_WIDE, TEAM_LOGO_GAP = 3, 1.5, 4
SLIP_TITLE_SIZES = (15, 14)
NAME_FORMS = ("full", "place", "short")
# A row's sticker is STICKER on its longer side. The row's second line: the place (never under
# SLIP_PLACE_MIN) SLIP_FACT_GAP before the facts, which end at the row's right edge SLIP_CHIP_GAP apart.
STICKER = 18
SLIP_PLACE_MIN, SLIP_FACT_GAP, SLIP_CHIP_GAP = 72, 8, 4
# The week's cells, WEEK_CELL_GAP apart, each over a WEEK_BAR_H bar on the band's foot.
WEEK_BAND = (332, 426, 800 - MARGIN, COLUMN_BOTTOM)
WEEK_CELL_GAP, WEEK_BAR_H = 10, 6


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
def _header_wordmark_width(max_width, max_height):
    """How wide the page's wordmark is drawn within ``max_width`` x ``max_height`` (0 without the file)."""
    try:
        with Image.open(HEADER_WORDMARK_PATH) as source:
            width, height = source.size
    except (OSError, ValueError):
        return 0
    return round(width * min(max_width / width, max_height / height, 1))


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


def source_logo(source, height, ink=None):
    """A keyless source's own logo ``height`` px tall (dark lettering; in ``ink`` on a dark page),
    or None for Ticketmaster, an unknown source or a missing file."""
    if source not in HEADER_SOURCE_HEIGHTS:
        return None
    return load_source_logo(SOURCE_LOGO_DIR / f"{source}.png", height, ink)


def listed_sources(events):
    """The keyless sources with an event among ``events``, in the header's order."""
    used = {(event.extra or {}).get("source") for event in events}
    return tuple(source for source in HEADER_SOURCE_HEIGHTS if source in used)


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
        rule = colors.get("rule", colors["line"])
        self._draw_page_header(image, draw, settings, generated_at, stale, days, colors,
                               sources=listed_sources(events[:1 + LIST_ROWS]))
        draw.line((MARGIN, HEADER_H, width - MARGIN, HEADER_H), fill=rule)
        draw.line((COLUMN_RULE, HERO[1], COLUMN_RULE, HERO[3]), fill=rule)
        section = self._plan_section(draw, events, days, on_sale or [], colors)
        if events:
            self._draw_featured(image, draw, events[0], today, colors, section["height"])
        self._draw_section(draw, section, getattr(generated_at, "tzinfo", None), colors)
        self._draw_next(image, draw, events[1:1 + LIST_ROWS], today, colors,
                        now=generated_at if isinstance(generated_at, datetime) else None)
        self._draw_week_band(draw, days, today, colors, section["keys"])
        if tuple(dimensions) != (width, height):
            image = image.resize(tuple(dimensions), Image.Resampling.LANCZOS)
        return image

    # -- the page under the content ---------------------------------------------------------
    def _draw_backdrop(self, image, featured, colors):
        """The doodle tile over the bare paper, one continuous pattern in the featured event's
        kind colour (the accent with no event): DOODLE_LIST strong behind the list's rows,
        DOODLE_OPEN everywhere else. The paper itself keeps its colour between the strokes."""
        tint = self._event_color(featured, colors) if featured is not None else colors["accent"]
        width, height = image.size
        list_box = (COLUMN_RULE + 1, HEADER_H + 1, width - 1, WEEK_BAND[1] - 1)
        zones = [((0, 0, width - 1, HEADER_H), DOODLE_OPEN),
                 ((0, HEADER_H + 1, COLUMN_RULE, height - 1), DOODLE_OPEN),
                 ((COLUMN_RULE + 1, WEEK_BAND[1], width - 1, height - 1), DOODLE_OPEN),
                 (list_box, DOODLE_LIST)]
        for box, strength in zones:
            # Every zone tiles from the page's corner, so the strokes run on across their borders.
            paint_doodles(image, box, DOODLE_PATH, tint, strength=strength, offset=box[:2])

    # -- header -----------------------------------------------------------------------------
    def _draw_page_header(self, image, draw, settings, generated_at, stale, days, colors, sources=()):
        """The page's wordmark, Ticketmaster's own wordmark after it, then the logos of the keyless
        ``sources`` with an event on the page, and at the right the week's count over the area and
        the update time."""
        right = 800 - MARGIN
        wordmark_bottom = self._draw_header_wordmark(image, draw, MARGIN, 5, *HEADER_WORDMARK, colors)
        if wordmark_bottom is None:
            title_font = self._font(28, bold=True)
            draw.text((MARGIN, 10), "LOCAL EVENTS", fill=colors.get("title", colors["ink"]), font=title_font)
            source_x = MARGIN + draw.textlength("LOCAL EVENTS", font=title_font) + SOURCE_LOGO_GAP
        else:
            source_x = MARGIN + _header_wordmark_width(*HEADER_WORDMARK) + SOURCE_LOGO_GAP
        mark = _ticketmaster_logo(TICKETMASTER_LOGO_HEIGHT, None if colors["mode"] != "dark" else tuple(colors["ink"]))
        if mark is not None:
            image.paste(mark, (round(source_x), HEADER_H // 2 - mark.height // 2), mark)
        counted = [day["count"] for day in days if isinstance(day.get("count"), int)]
        total_font, small = self._font(22, bold=True), self._font(13, bold=True)
        headline = f"本周 {sum(counted):,} 场" if counted else "本地活动"
        draw.text((right - draw.textlength(headline, font=total_font), 3), headline,
                  fill=colors.get("accent", colors["ink"]), font=total_font)
        when = generated_at.strftime("%m/%d %H:%M") if isinstance(generated_at, datetime) else ""
        note = f"{self._place_label(settings)} · {'缓存 ' if stale else ''}{when} 更新"
        draw.text((right - draw.textlength(note, font=small), 32), note, fill=colors["ink"], font=small)
        # The keyless sources' logos follow Ticketmaster's while they clear the summary.
        cursor = source_x + (mark.width if mark is not None else 0)
        limit = right - max(draw.textlength(headline, font=total_font), draw.textlength(note, font=small))
        for source in sources:
            logo = source_logo(source, HEADER_SOURCE_HEIGHTS[source], None if colors["mode"] != "dark" else tuple(colors["ink"]))
            if logo is None or cursor + SOURCE_LOGO_GAP + logo.width > limit - HEADER_SOURCE_CLEAR:
                continue
            image.paste(logo, (round(cursor + SOURCE_LOGO_GAP), HEADER_H // 2 - logo.height // 2), logo)
            cursor += SOURCE_LOGO_GAP + logo.width

    def _place_label(self, settings):
        radius = self._bounded_int(settings.get("radiusMiles"), 50, 1, 250)
        postal = self._postal_code(settings)
        if postal:
            return f"{postal} 周边 {radius} 英里"
        return self._location_label(settings)

    # -- the featured event ---------------------------------------------------------------
    def _draw_featured(self, image, draw, event, today, colors, section_h=SECTION_H):
        """The featured event in tiers, each holding one kind of fact: the poster; the day and time
        with the sale status and the tour's size at the right; the headliner as the title beside
        a bar of the kind's colour, the rest of the bill under it; the place by a pin with the
        distance at the right; the price, kind, age and repeats as chips. ``section_h`` is the room kept under
        it for the column's foot. Returns the y where the text ends."""
        px0, py0, px1, y1 = HERO
        width = px1 - px0
        kind_colour = self._event_color(event, colors)
        # The title's lines are set first: the poster takes the height the text leaves it.
        title, subtitle = title_parts(event.title)
        age = (event.extra or {}).get("age") or ""
        if age_notice(subtitle):
            age, subtitle = age or age_notice(subtitle), ""
        text_x, text_w = px0 + 10, width - 10
        title_font, lines, subtitle = self._featured_title(draw, event, title, subtitle, text_w, colors)
        text_h = 36 + len(lines) * (title_font.size + 4) + (SUBTITLE_SIZE + 6 if subtitle else 0) + 47
        poster_h = max(POSTER_H, min(POSTER_MAX, y1 - section_h - SECTION_GAP - py0 - text_h))
        poster = self._load_event_image(event)
        art = ImageOps.fit((poster or self._placeholder_event_image(event, (width, poster_h), colors)).convert("RGB"),
                           (width, poster_h), method=Image.Resampling.LANCZOS)
        image.paste(art, (px0, py0))
        # When, in the accent colour; the sale status and the tour's size at the right.
        y = py0 + poster_h + 8
        day = _day(event.local_date)
        day_text = day_label(day, today) if day and (day - today).days < 7 else (f"{day:%m/%d}" if day else "待定")
        when_font, chip_font = self._font(20, bold=True), self._font(12, bold=True)
        draw.text((px0, y - 3), f"{day_text} {clock(event.local_time) or '全天'}", fill=colors["accent"], font=when_font)
        tags = []
        tour = (event.extra or {}).get("tour_dates") or 0
        if tour >= 5:
            tags.append((f"巡演 {tour} 场", None))
        sold_out = bool(SOLD_OUT_NOTICE.search(event.title or ""))
        source_status = (event.status or "onsale").lower()
        status = STATUS_LABELS.get(source_status, (event.status or "").upper())
        if sold_out and source_status in {"onsale", "offsale"}:
            status = "售罄"
        if status:
            # A sale that is open is said quietly; one stopped, moved or sold out takes the accent.
            tags.append((status, None if status == STATUS_LABELS["onsale"] else colors["accent"]))
        tag_x = px1
        for text, fill in tags:
            tag_w = draw.textlength(text, font=chip_font) + 14
            self._draw_chip(draw, (tag_x - tag_w, y + 1, tag_x, y + 21), text, chip_font, colors, fill, radius=10)
            tag_x -= tag_w + 6
        y += 28
        # What: the headliner, then the rest of the bill, beside a bar of the kind's colour.
        block_top = y
        for line in lines:
            self._draw_runs(image, draw, line, text_x, y, title_font)
            y += title_font.size + 4
        if subtitle:
            subtitle_font = self._font(SUBTITLE_SIZE, bold=True)
            draw.text((text_x, y + 1), self._fit_text(draw, subtitle, subtitle_font, text_w), fill=colors["ink"],
                      font=subtitle_font)
            y += SUBTITLE_SIZE + 6
        draw.rectangle((px0, block_top + 2, px0 + 3, y - 4), fill=kind_colour)
        # Where: a pin, the place, and the distance at the right.
        y += 5
        meta_font = self._font(13, bold=True)
        distance = event.distance or ""
        distance_w = draw.textlength(distance, font=meta_font) if distance else 0
        if distance:
            draw.text((px1 - distance_w, y), distance, fill=colors["ink"], font=meta_font)
        self._draw_pin(draw, (px0 + 5, y + 8), colors["accent"], colors["paper"])
        place_x = px0 + 14
        wordmark = venue_is_wordmark(event.venue_name)
        logo_box = HERO_VENUE_WORDMARK if wordmark else HERO_VENUE_LOGO
        logo_w = paste_venue_logo(image, event.venue_name, place_x, y + 8, logo_box, colors["ink"], colors["paper"])
        place_x += logo_w + 5 if logo_w else 0
        # The city goes before the venue's name is cut ('Chase Center', not 'Chase Center · San Fran...').
        room = px1 - place_x - (distance_w + 10 if distance else 0)
        place = (event.city or "") if logo_w and wordmark else self._place_line(event)
        if not (logo_w and wordmark) and draw.textlength(place, font=meta_font) > room:
            place = event.venue_name or place
        draw.text((place_x, y), self._fit_text(draw, place, meta_font, room), fill=colors["ink"], font=meta_font)
        y += 22
        # The facts as chips: the price solid, the rest edged by a hairline. Small type stays on
        # paper or the accent: a mid-tone fill would dither under it on the six-colour panel.
        x = px0
        facts = [("" if sold_out else price_label(event.price), colors["accent"]),
                 (self._kind_text(event), None),
                 (age, None),
                 (self._more_text(event), None)]
        drawn = False
        for text, fill in facts:
            if not text:
                continue
            chip = draw.textlength(text, font=chip_font) + 14
            if x + chip > px1:
                break
            self._draw_chip(draw, (x, y, x + chip, y + 20), text, chip_font, colors, fill, radius=10)
            x += chip + 6
            drawn = True
        return y + 20 if drawn else y - 4

    def _draw_chip(self, draw, box, text, font, colors, fill=None, radius=3, drop=2):
        """A chip with its text centred, ``drop`` under its top: solid in ``fill`` with the ink
        that reads on it, or, with no fill, the bare paper (clear of the backdrop's doodles) edged
        by a hairline around the page's ink."""
        x0, y0, x1, y1 = box
        if fill is None:
            draw.rounded_rectangle(box, radius=radius, fill=colors["paper"],
                                   outline=colors.get("rule", colors["line"]), width=1)
            ink = colors["ink"]
        else:
            draw.rounded_rectangle(box, radius=radius, fill=fill)
            ink = chip_ink(fill, colors)
        draw.text(((x0 + x1 - draw.textlength(text, font=font)) / 2, y0 + drop), text, fill=ink, font=font)

    def _featured_title(self, draw, event, title, subtitle, width, colors):
        """(font, lines, subtitle) for the featured card, each of at most two lines a list of runs.
        A game gives each team a line after its logo, 'vs.' ending the first, named in the first of
        NAME_FORMS that fits, and what leads or follows the game is the subtitle; a title a known team or
        show leads hangs beside its logo; any other wraps as it is, at the largest size that keeps
        it to two lines."""
        teams = self._title_teams(event)
        ink = colors.get("title", colors["ink"])
        if teams and teams.is_game:
            subtitle = " · ".join(part for part in (teams.prefix, teams.trailing) if part)
            for form in NAME_FORMS:
                for size in TITLE_SIZES:
                    font = self._font(size, bold=True)
                    lines = self._game_lines(teams, form, font, colors)
                    if all(self._runs_width(draw, line, font) <= width for line in lines):
                        return font, lines, subtitle
            return font, [self._fit_runs(draw, line, font, width) for line in lines], subtitle
        lead = teams.sides[0] if teams else None
        if lead and not subtitle and lead.rest:
            # 'Harlem Globetrotters 2026 World Tour': the show, then the tour.
            title, subtitle = lead.text, lead.rest
        for size in TITLE_SIZES:
            font = self._font(size, bold=True)
            logo = self._title_logo(lead.team, font, colors) if lead else None
            indent = logo.width + TEAM_LOGO_GAP if logo else 0
            if len(self._wrap_full_text(draw, title, font, width - indent)) <= 2:
                break
        lines = []
        for index, text in enumerate(self._wrap_text(draw, title, font, width - indent, 2)):
            # The logo leads the first line; the second hangs under the first's text.
            lead_runs = [("logo", logo, logo.width), ("space", TEAM_LOGO_GAP)] if logo and index == 0 else []
            lines.append((lead_runs or ([("space", indent)] if indent else [])) + [("text", text, ink)])
        return font, lines, subtitle

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
        Ticketmaster gives it; a game's sport as its title says it, '体育 · 女排'); "" when
        Ticketmaster names neither."""
        segment = SEGMENT_LABELS.get((event.segment or "").lower(), event.segment or "")
        label, _icon = kind_of(event)
        teams = self._title_teams(event)
        if teams and teams.sport:
            label = teams.sport
        if kind_key(event) == "comedy" and segment == "音乐":
            return label
        if label == segment:
            genre = event.genre if event.genre and event.genre.lower() != (event.segment or "").lower() else ""
            label = genre if genre and genre.casefold() != "other" else ""
        return " · ".join(part for part in (segment, label) if part)

    # -- titles that name teams: each team's logo before its name ---------------------------
    @staticmethod
    def _title_teams(event):
        return read_title(event.title or "", event.segment or "", event.genre or "", (event.extra or {}).get("subgenre") or "")

    def _title_logo(self, team, font, colors):
        """``team``'s logo as tall as ``font``'s type plus TEAM_LOGO_RISE (None without one)."""
        if team is None:
            return None
        height = font.size + TEAM_LOGO_RISE
        return team_logo(team, (round(height * TEAM_LOGO_WIDE), height), colors["mode"] == "dark",
                         tuple(colors["paper"]), tuple(colors["ink"]))

    def _side_runs(self, side, form, font, colors, slot=0):
        """A side's logo (centred in ``slot`` when given, so both teams' names line up), then its
        name in ``form`` (NAME_FORMS): as the title writes it with what follows, by its place (a
        school; others by their short name), or by its short name."""
        logo = self._title_logo(side.team, font, colors)
        team = side.team
        if form == "full" or team is None:
            name = " ".join(part for part in (side.text, side.rest) if part)
        else:
            name = (team.place if form == "place" else "") or team.short or side.text
        if logo is not None:
            lead = [("logo", logo, max(slot, logo.width)), ("space", TEAM_LOGO_GAP)]
        else:
            lead = [("space", slot + TEAM_LOGO_GAP)] if slot else []
        return lead + [("text", name, colors.get("title", colors["ink"]))]

    def _game_lines(self, teams, form, font, colors):
        """A game in two lines, each team after its logo, the logos in one column: 'vs.' ends the first."""
        logos = [self._title_logo(side.team, font, colors) for side in teams.sides]
        slot = max((logo.width for logo in logos if logo is not None), default=0)
        first, second = (self._side_runs(side, form, font, colors, slot) for side in teams.sides)
        return [first + [("text", f" {teams.joiner}", colors["ink"])], second]

    def _title_runs(self, teams, form, font, colors):
        """A list row's title in one line: what leads the game, each side after its logo, 'vs.'
        between them, what follows; past the ``full`` form only the teams, by shorter names."""
        ink = colors.get("title", colors["ink"])
        short = form != "full"
        runs = [("text", f"{teams.prefix} ", ink)] if teams.prefix and not short else []
        for index, side in enumerate(teams.sides):
            if index:
                runs.append(("text", f" {teams.joiner} ", colors["ink"]))
            runs += self._side_runs(side, form, font, colors)
        if teams.trailing and not short:
            runs.append(("text", f" {teams.trailing}", ink))
        return runs

    def _runs_width(self, draw, runs, font):
        return sum(draw.textlength(run[1], font=font) if run[0] == "text" else run[2] if run[0] == "logo" else run[1]
                   for run in runs)

    def _fit_runs(self, draw, runs, font, width):
        """``runs`` with their last text cut to end within ``width``."""
        *head, (_kind, text, fill) = runs
        room = width - self._runs_width(draw, head, font)
        return [*head, ("text", self._fit_text(draw, text, font, max(room, 10)), fill)]

    @staticmethod
    def _draw_runs(image, draw, runs, x, y, font):
        """Text and logos along a line from (x, y), each logo centred on the type's capitals."""
        _left, top, _right, bottom = draw.textbbox((x, y), "H", font=font)
        middle = (top + bottom) / 2
        for run in runs:
            if run[0] == "text":
                draw.text((x, y), run[1], fill=run[2], font=font)
                x += draw.textlength(run[1], font=font)
            elif run[0] == "logo":
                logo, slot = run[1], run[2]
                image.paste(logo, (round(x + (slot - logo.width) / 2), round(middle - logo.height / 2)), logo)
                x += slot
            else:
                x += run[1]
        return x

    @staticmethod
    def _more_text(event):
        more = (event.extra or {}).get("more_showtimes")
        return f"+{more} 场" if more else ""

    # -- the featured column's foot: sales opening soon and the week's mix of kinds ----------
    def _kind_groups(self, events, days, colors):
        """[(colour, label, keys)]: the kinds that get a colour of their own, the listed events'
        first, then the week's by count. Listed kinds sharing a colour are named together
        ('流行 / R&B'), each keeping its statistics key; a week's kind whose colour already
        means another kind is left to the neutral 'other kinds' bin."""
        groups = {}
        for key in dict.fromkeys(event_category(event) for event in events):
            groups.setdefault(category_color(key, colors), {}).setdefault(category_label(key), []).append(key)
        weekly = {}
        for day in days:
            for key, count in (day.get("kind_counts") or {}).items():
                weekly[key] = weekly.get(key, 0) + count
        named = {key for labels in groups.values() for keys in labels.values() for key in keys}
        for key in sorted(weekly, key=weekly.get, reverse=True):
            colour, label = category_color(key, colors), category_label(key)
            if key in named or key == "|" or (colour in groups and label not in groups[colour]):
                continue
            groups.setdefault(colour, {}).setdefault(label, []).append(key)
        return [(colour, " / ".join(labels), [key for keys in labels.values() for key in keys])
                for colour, labels in groups.items()]

    def _mix_legend(self, draw, events, days, colors, width, max_rows):
        """(keys, rows, counts, sampled, total) for the week's mix: the kinds with their own
        colour, the legend's rows of (colour, text, width) for the kinds counted this week, and
        the bar's counts in the legend's order. Counts stay on the true total: kinds without a
        colour and events never sampled are their own neutral bins. A legend too long for
        ``max_rows`` first drops the named kinds' counts, then the week's rarest kinds, which
        join the 'other kinds' bin; the listed events' kinds always keep their colour."""
        font = self._font(LEGEND_SIZE, bold=True)
        groups = self._kind_groups(events, days, colors)
        listed = {event_category(event) for event in events}
        while True:
            keys = [key for _colour, _label, members in groups for key in members]
            mix, sampled, total = sampled_mix(days, keys)
            for with_counts in (True, False):
                items, counts = [], {}
                for colour, label, members in groups:
                    count = sum(mix.get(key, 0) for key in members)
                    if not count:
                        continue
                    counts.update({key: mix[key] for key in members if mix.get(key)})
                    text = self._fit_text(draw, f"{label} {count}" if with_counts else label, font, width - 12)
                    items.append((colour, text, 12 + draw.textlength(text, font=font)))
                for key in (OTHER_KINDS, "|", UNCOUNTED):
                    if mix.get(key) and key not in counts:
                        counts[key] = mix[key]
                        text = f"{category_label(key)} {mix[key]}"
                        items.append((category_color(key, colors), text, 12 + draw.textlength(text, font=font)))
                rows, used = [[]], 0
                for item in items:
                    if rows[-1] and used + item[2] > width:
                        rows.append([])
                        used = 0
                    rows[-1].append(item)
                    used += item[2] + LEGEND_GAP
                if len(rows) <= max_rows:
                    return keys, rows, counts, sampled, total
            spare = [index for index, (_colour, _label, members) in enumerate(groups) if not listed & set(members)]
            if not spare:
                return keys, rows[:max_rows], counts, sampled, total
            del groups[spare[-1]]

    def _plan_section(self, draw, events, days, on_sale, colors):
        """What the featured column's foot holds, and its height: the public sales opening this
        week (SALE_ROWS at most), then the week's mix of kinds, or, from a cache that kept only
        the main categories, their sampled counts."""
        width = HERO[2] - HERO[0]
        sales = list(on_sale)[:SALE_ROWS]
        has_kinds = any(isinstance(day.get("kind_counts"), dict) for day in days)
        keys, rows, counts, sampled, total = self._mix_legend(
            draw, events, days, colors, width, LEGEND_ROWS - 1 if sales else LEGEND_ROWS)
        plan = {"keys": keys, "sales": sales, "rows": rows, "counts": counts, "sampled": sampled, "total": total,
                "has_kinds": has_kinds, "segments": {}, "height": 0}
        if sales:
            plan["height"] += 22 + len(sales) * SALE_ROW_H
        if has_kinds:
            plan["height"] += 37 + sum(1 for row in rows if row) * LEGEND_ROW_H
        elif total:
            # Earlier caches retained only main-category samples. Keep their counts neutral until
            # the regular refresh supplies kinds; visible events cannot reconstruct the whole week.
            for day in days:
                for key, count in (day.get("segments") or {}).items():
                    plan["segments"][key] = plan["segments"].get(key, 0) + count
            plan["height"] += 22 + (2 * LEGEND_ROW_H if plan["segments"] else 0)
        # The room between the hairline over the section and its first line.
        plan["height"] += SECTION_PAD if plan["height"] else 0
        return plan

    def _draw_section(self, draw, plan, tz, colors):
        """The planned foot, ending at the column's foot, under a hairline."""
        if not plan["height"]:
            return
        x, _y0, right, y1 = HERO
        y = y1 - plan["height"]
        ink = colors["ink"]
        head_font, row_font, small = self._font(13, bold=True), self._font(12, bold=True), self._font(LEGEND_SIZE, bold=True)
        draw.line((x, y, right, y), fill=colors.get("rule", colors["line"]))
        y += SECTION_PAD
        if plan["sales"]:
            draw.text((x, y), "即将开售", fill=ink, font=head_font)
            note = "公开售票开始"
            draw.text((right - draw.textlength(note, font=small), y + 2), note, fill=ink, font=small)
            y += 18
            for item in plan["sales"]:
                sale = self._parse_datetime(item.get("sale_start"))
                sale = sale.astimezone(tz) if sale and tz else sale
                when = f"{sale:%m/%d %H:%M}" if sale else ""
                kind = item.get("kind")
                colour = category_color(kind, colors) if kind in plan["keys"] else colors["muted"]
                draw.rectangle((x, y + 4, x + 7, y + 11), fill=colour)
                when_w = draw.textlength(when, font=row_font)
                draw.text((x + 12, y), when, fill=ink, font=row_font)
                draw.text((x + 18 + when_w, y), self._fit_text(draw, item.get("title") or "", row_font,
                                                               right - x - 18 - when_w), fill=ink, font=row_font)
                y += SALE_ROW_H
            y += 4
        total = plan["total"]
        if plan["has_kinds"]:
            # Coverage is said beside the heading: a sample is never normalised to all events.
            draw.text((x, y), "类型分布", fill=ink, font=head_font)
            note = f"已统计 {plan['sampled']}/{total} 场"
            draw.text((right - draw.textlength(note, font=small), y + 2), note, fill=ink, font=small)
            self._draw_mix_bar(draw, plan["counts"], (x, y + 21, right, y + 21 + MIX_BAR_H - 1), colors, total=total)
            y += 37
            for row in plan["rows"]:
                cursor = x
                for colour, text, item_w in row:
                    draw.rectangle((cursor, y + 3, cursor + 7, y + 10), fill=colour)
                    draw.text((cursor + 12, y), text, fill=ink, font=small)
                    cursor += item_w + LEGEND_GAP
                y += LEGEND_ROW_H
            return
        if not total:
            return
        whole = sum(plan["segments"].values())
        draw.text((x, y), f"大类样本 · {whole}/{total}场", fill=ink, font=head_font)
        waiting = "类型待更新"
        draw.text((right - draw.textlength(waiting, font=small), y + 2), waiting, fill=ink, font=small)
        cursor, y = x, y + 22
        for key, label in SEGMENT_LABELS.items():
            if not plan["segments"].get(key):
                continue
            text = f"{label} {plan['segments'][key]}"
            text_w = draw.textlength(text, font=small)
            if cursor + text_w + 12 > right:
                cursor, y = x, y + LEGEND_ROW_H
            draw.rectangle((cursor, y + 3, cursor + 7, y + 10), fill=colors["muted"])
            draw.text((cursor + 12, y), text, fill=ink, font=small)
            cursor += text_w + 12 + LEGEND_GAP

    # -- the next events: one row per event ---------------------------------------------------
    def _draw_next(self, image, draw, events, today, colors, now=None):
        x0, y0, x1, _y1 = LIST
        ink = colors["ink"]
        draw.text((x0, y0 - 2), "接下来", fill=colors.get("title", ink), font=self._font(16, bold=True))
        tonight = sum(1 for event in events if _day(event.local_date) == today)
        note = (f"今天还有 {tonight} 场 · " if tonight else "") + "按开始时间"
        note_font = self._font(11, bold=True)
        draw.text((x1 - draw.textlength(note, font=note_font), y0 + 3), note, fill=ink, font=note_font)
        previous_day = None
        for index, event in enumerate(events[:LIST_ROWS]):
            top = y0 + SLIP_TOP + index * ROW_H
            draw.line((x0, top - 1, x1, top - 1), fill=colors.get("rule", colors["line"]))
            day = _day(event.local_date)
            label = day_label(day, today) if day and (day - today).days < 7 else (f"{day:%m/%d}" if day else "待定")
            # The day is said once per run of rows on it.
            self._draw_slip(image, draw, event, (x0, top, x1, top + SLIP_H), label if label != previous_day else "",
                            colors, now)
            previous_day = label
        if not events:
            draw.text((x0, y0 + SLIP_TOP + 10), "近期没有更多活动", fill=ink, font=self._font(15, bold=True))

    def _draw_slip(self, image, draw, event, box, day_text, colors, now):
        """One row: the time over the kind's name and sticker; the poster with the kind's colour
        as a bar down its left edge; the title across the row's whole text width; then the place,
        the whole distance, and the facts to know at the right. Where a new day starts, its name
        sits on the hairline over the row."""
        sx0, sy0, sx1, _sy1 = box
        ink = colors["ink"]
        kind_colour = self._event_color(event, colors)
        meta_font = self._font(11, bold=True)
        if day_text:
            label_w = draw.textlength(day_text, font=meta_font)
            draw.rectangle((sx0, sy0 - 2, sx0 + label_w + 5, sy0), fill=colors["paper"])
            draw.text((sx0, sy0 - 9), day_text, fill=ink, font=meta_font)
        draw.text((sx0, sy0 + 5), clock(event.local_time) or "全天", fill=ink, font=self._font(16, bold=True))
        label, icon = kind_of(event)
        teams = self._title_teams(event)
        label = self._fit_text(draw, (teams.sport if teams and teams.sport else label) or "未分类", meta_font,
                               TIME_COLUMN - 4)
        y = sy0 + 27
        draw.text((sx0, y), label, fill=ink, font=meta_font)
        # The kind's sticker follows its name where the column has the room.
        art = _genre_icon(icon, STICKER, tuple(colors["paper"]), tuple(colors["ink"]))
        sticker_x = sx0 + TIME_COLUMN - 4
        if art is not None and draw.textlength(label, font=meta_font) + 3 + art.width <= TIME_COLUMN - 4:
            image.paste(art, (sticker_x - art.width, y + 8 - art.height // 2), art)
        # The kind's colour is the poster's left edge: one solid bar, no frame around the picture.
        bar_x = sx0 + TIME_COLUMN
        thumb_x, thumb_y = bar_x + KIND_BAR, sy0 + 3
        self._paste_event_image(image, event, (thumb_x, thumb_y, *THUMB), colors)
        draw.rectangle((bar_x, thumb_y, thumb_x - 1, thumb_y + THUMB[1] - 1), fill=kind_colour)
        text_x, right = thumb_x + THUMB[0] + 9, sx1
        self._draw_slip_title(image, draw, event, (text_x, sy0 + 3), right - text_x, colors)
        # A keyless source's row ends its second line with the source's own logo, straight on the page.
        source = (event.extra or {}).get("source")
        logo = source_logo(source, SLIP_SOURCE_HEIGHTS.get(source, 0), None if colors["mode"] != "dark" else tuple(ink))
        if logo is not None:
            image.paste(logo, (right - logo.width, y + 7 - logo.height // 2), logo)
            right -= logo.width + SLIP_SOURCE_GAP
        place_x = text_x
        # Reserve the complete distance and important facts before fitting the venue.
        distance = f" · {event.distance}" if event.distance else ""
        distance_w = draw.textlength(distance, font=meta_font)
        wordmark = venue_is_wordmark(event.venue_name)
        logo_box = SLIP_VENUE_WORDMARK if wordmark else SLIP_VENUE_LOGO
        # A wordmark replaces the venue name. Keep its city and distance ahead of optional chips.
        place_min = (logo_box[0] + 4 + draw.textlength(event.city or "", font=meta_font)
                     if wordmark else SLIP_PLACE_MIN)
        facts = self._slip_facts(event, now)
        price = "" if SOLD_OUT_NOTICE.search(event.title or "") else price_label(event.price, lowest=True)
        if price:
            facts.append((price, False))
        if not facts and (event.status or "").lower() == "onsale":
            facts.append(("售票中", False))
        shown, facts_w = [], 0
        for text, alarm in facts:
            fact_w = draw.textlength(text, font=meta_font) + 8
            if place_x - text_x + place_min + distance_w + SLIP_FACT_GAP + facts_w + fact_w > right - text_x:
                break
            shown.append((text, alarm, fact_w))
            facts_w += fact_w + SLIP_CHIP_GAP
        # The facts end at the row's right edge, the most important first.
        x = right - facts_w + SLIP_CHIP_GAP
        facts_left = x
        for text, alarm, fact_w in shown:
            self._draw_chip(draw, (x, y - 1, x + fact_w, y + 13), text, meta_font, colors,
                            colors["accent"] if alarm else None, drop=1)
            x += fact_w + SLIP_CHIP_GAP
        logo_w = paste_venue_logo(image, event.venue_name, place_x, y + 7, logo_box, colors["ink"], colors["paper"])
        place_x += logo_w + 4 if logo_w else 0
        room = (facts_left - SLIP_FACT_GAP if shown else right) - place_x - distance_w
        place = (event.city or "") if logo_w and wordmark else self._place_line(event)
        if not (logo_w and wordmark) and draw.textlength(place, font=meta_font) > room:
            place = event.venue_name or place
        draw.text((place_x, y), self._fit_text(draw, place, meta_font, max(room, 10)) + distance, fill=ink, font=meta_font)

    def _draw_slip_title(self, image, draw, event, origin, width, colors):
        """The title in one line at the first of SLIP_TITLE_SIZES that holds it whole: a game's
        teams after their logos, named in the first of NAME_FORMS that fits; cut only when none does."""
        teams = self._title_teams(event)
        for form in (NAME_FORMS if teams and teams.is_game else NAME_FORMS[:1]):
            for size in SLIP_TITLE_SIZES:
                font = self._font(size, bold=True)
                runs = (self._title_runs(teams, form, font, colors) if teams
                        else [("text", event.title or "", colors["ink"])])
                if self._runs_width(draw, runs, font) <= width:
                    return self._draw_runs(image, draw, runs, *origin, font)
        return self._draw_runs(image, draw, self._fit_runs(draw, runs, font, width), *origin, font)

    def _slip_facts(self, event, now):
        """[(text, alarm)] to know before buying, most important first: a sale stopped or a date
        moved (alarm), the public sale opening later, a meetup that asks for approval, the age
        limit, more showtimes the same day, a big tour, a family show, several days, and how many
        are going to a meetup."""
        extra = event.extra or {}
        facts = []
        # Some public listings retain onsale/$0 metadata after their title is marked SOLD OUT.
        # Only the explicit bracketed marker takes priority; an artist/title word never does.
        # A keyless source says it in a field of its own.
        sold_out = bool(SOLD_OUT_NOTICE.search(event.title or "")) or extra.get("sold_out") is True
        status = (event.status or "").lower()
        if status in STATUS_LABELS and status != "onsale":
            facts.append((STATUS_LABELS[status], True))
        if sold_out:
            facts.append(("售罄", True))
        sale = self._parse_datetime(extra.get("sale_start")) if extra.get("sale_start") else None
        if not sold_out and sale and now and sale > now:
            facts.append((f"{sale.astimezone(now.tzinfo):%m/%d} 开售" if now.tzinfo else f"{sale:%m/%d} 开售", False))
        if extra.get("approval") is True:
            facts.append(("需审批", False))
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
        if isinstance(extra.get("guests"), int) and extra["guests"] >= MEETUP_GUESTS_SHOWN:
            facts.append((f"{extra['guests']} 人", False))
        return facts

    # -- the week under the list ------------------------------------------------------------
    def _draw_week_band(self, draw, days, today, colors, legend_keys=()):
        """Seven day cells under a hairline: the day and date, its count, and on one baseline a
        bar of its mix of kinds as long as its share of the busiest day. Today carries the
        accent colour: a short rule over its cell and its count."""
        x0, y0, x1, y1 = WEEK_BAND
        cell_w = (x1 - x0) / 7
        label_font, count_font = self._font(11, bold=True), self._font(17, bold=True)
        by_date = {day.get("date"): day for day in days}
        max_count = max((day.get("count") or 0 for day in days if isinstance(day.get("count"), int)), default=0)
        draw.line((x0, y0, x1, y0), fill=colors.get("rule", colors["line"]))
        for index in range(7):
            current = today + timedelta(days=index)
            day = by_date.get(current.isoformat()) or {}
            left = round(x0 + index * cell_w)
            right = round(x0 + (index + 1) * cell_w) - WEEK_CELL_GAP
            if index == 0:
                draw.rectangle((left, y0, right, y0 + 1), fill=colors["accent"])
            draw.text((left, y0 + 5), f"{day_label(current, today)} {current:%m/%d}", fill=colors["ink"], font=label_font)
            count = day.get("count")
            figure = f"{count:,}" if isinstance(count, int) else "–"
            draw.text((left, y0 + 17), figure, fill=colors["accent"] if index == 0 else colors["ink"], font=count_font)
            if isinstance(count, int):
                draw.text((left + draw.textlength(figure, font=count_font) + 3, y0 + 23), "场", fill=colors["ink"],
                          font=label_font)
            if not isinstance(count, int) or count <= 0 or not max_count:
                continue
            box = (left, y1 - WEEK_BAR_H, left + max(3, round((right - left) * count / max_count)), y1 - 1)
            if isinstance(day.get("kind_counts"), dict):
                mix, _sampled, total = sampled_mix([day], legend_keys)
                ordered = {key: mix[key] for key in (*legend_keys, OTHER_KINDS, "|", UNCOUNTED) if mix.get(key)}
                self._draw_mix_bar(draw, ordered, box, colors, total=total)
            else:
                # An old cache has no detailed kinds: a neutral quantity bar, not invented shares.
                draw.rectangle(box, fill=colors["muted"])

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
