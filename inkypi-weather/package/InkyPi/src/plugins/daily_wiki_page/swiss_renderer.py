"""Swiss-grid daily image page with measured, complete text and contained media."""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageFilter, ImageOps
from plugins.daily_wiki_page.almanac import short_role
from plugins.daily_wiki_page.holiday_places import MAX_PLACES
from utils.doodle_backdrop import paint_doodles
from utils.fonts import load_font


RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
MIN_BODY_FONT_SIZE = 13
MIN_SHORT_PHOTOGRAPH_HEIGHT = 120
# The wash behind the photograph: how much of the picture shows through the page colour,
# how soft it is and how far its edge fades into the page.
PHOTO_GLOW_STRENGTH = 0.6
PHOTO_GLOW_BLUR = 14
PHOTO_GLOW_FEATHER = 22
# History thumbnails keep this box's proportions as the ladder narrows them.
HISTORY_THUMBNAIL_BOX = (92, 74)
# Row padding above and below each history event, normal and compact.
HISTORY_ROW_PADDING = ((4, 8), (2, 4))
# Last resort for text that cannot fit even at the minimum size: cut it at a
# sentence end, else a clause break, and mark the cut with an ellipsis.
ELLIPSIS = "…"
SENTENCE_END = re.compile(r"[。！？!?]|\.(?=\s)")
CLAUSE_END = re.compile(r"[，；：、,;:]")
SWISS_FONT_PATH = Path(__file__).resolve().parents[2] / "static" / "fonts" / "NotoSansSC-VF.ttf"
TITLE_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "daily_image_wordmark.png"
# Shape strip between the title and the date; its end rules are drawn here so they stay crisp,
# and the asset is cropped symmetrically about the rules' axis.
ORNAMENT_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "daily_image_header_ornament.png"
# Wikimedia's logo, crediting a Wikimedia picture at the head of its source line.
WIKIMEDIA_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "wikimedia_logo.png"
# Wikipedia's puzzle globe: with Wikimedia's logo it names the page's sources after the title
# (the day's feed is Wikipedia's, its picture Wikimedia Commons'), each HEADER_LOGO_HEIGHT tall.
WIKIPEDIA_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "wikipedia_logo.png"
HEADER_LOGO_HEIGHT = 34
HEADER_LOGO_GAP = 12
# The logo stands this much taller than the source line it heads.
WIKIMEDIA_LOGO_SCALE = 1.3
# Quiet doodles in the day's accent under the photograph's column and the history column.
DOODLE_DIR = Path(__file__).resolve().parent / "assets" / "doodles"
# 今日人物与节日 at the foot of the history column: each row's list, the icon that leads it (a
# birthday cake, a candle, a lantern) and the palette role it is filled with (the day's lead
# accent, ink, the day's second accent).
ALMANAC_ROWS = (("births", "cake", "accent"), ("deaths", "candle", "ink"), ("holidays", "lantern", "accent2"))
# The gap under the block's rule, between its rows, and after a row's icon slot; the slot's width
# and the room kept between one row's icon and the next.
ALMANAC_TOP_GAP, ALMANAC_ROW_GAP, ALMANAC_CHIP_GAP = 5, 4, 7
ALMANAC_ICON_SLOT, ALMANAC_ICON_SPACING = 30, 1
ALMANAC_SEPARATOR = (" · ", False)
# The icons are drawn on an 80 x 64 grid (a cake and a lantern are wider than tall), as tall as a row
# with its gap less ALMANAC_ICON_SPACING, ALMANAC_ICON_SUPERSAMPLE times that size, then scaled down so
# their solid shapes stay crisp.
ALMANAC_ICON_GRID = (80, 64)
ALMANAC_ICON_SUPERSAMPLE = 4
# A holiday leads with where it belongs: each place's flag, as tall as a Chinese glyph at its own
# proportions and at most twice as wide (a wider flag keeps that width and stands shorter), as the
# World Cup panels size theirs, ringed by a hairline in the rule colour as Orbital Signal's pad
# flags are; then the place's name and the holiday ("[flag] 英国 篝火之夜"). Its row may take
# ALMANAC_HOLIDAY_LINES lines when the history above keeps its type without the room.
ALMANAC_FLAG_GAP = 3
ALMANAC_HOLIDAY_LINES = 2
FLAG = "flag"


class FlagMark(NamedTuple):
    """A flag run in an almanac entry: a flag key ("gb", "un") or a Commons picture's URL."""

    key: str
    url: str


class LayoutOverflowError(ValueError):
    """The complete source content cannot fit at the minimum readable size."""


def _text(value):
    return " ".join(str(value or "").split())


def _source_date(payload, now):
    try:
        return datetime.strptime(str(payload.get("date", "")), "%Y-%m-%d")
    except (TypeError, ValueError):
        return now


def _from_wikimedia(payload):
    return str(payload.get("source") or "Wikimedia").strip().casefold() == "wikimedia"


def _logo(path, height):
    """The logo at ``path`` ``height`` tall in its own colours, or None without the file."""
    try:
        with Image.open(path) as source:
            logo = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    return logo.resize((max(1, round(logo.width * height / logo.height)), height), Image.Resampling.LANCZOS)


def _source_logo(payload, height):
    """Wikimedia's logo ``height`` tall when the picture comes from Wikimedia, else None."""
    if not _from_wikimedia(payload) or height < 6:
        return None
    return _logo(WIKIMEDIA_LOGO_PATH, height)


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


def _almanac_entry(key, item, full=True):
    """One almanac entry as (text, bold) runs: a person's name, year and (when ``full``) what
    they were ("乔治·布尔 1815 英国数学家"), or a holiday's name led by its places' flags
    (FlagMark runs) and names, never without them; None when it has no name."""
    name = _text(item.get("name"))
    if not name:
        return None
    if key == "holidays":
        places = [place for place in item.get("places") or [] if isinstance(place, dict) and _text(place.get("name"))]
        if not places:
            return [(name, True)]
        shown = places[:MAX_PLACES]
        label = "、".join(_text(place["name"]) for place in shown) + ("等" if len(places) > len(shown) else "")
        flags = [(FlagMark(_text(place.get("flag")).lower(), _text(place.get("flag_url"))), FLAG) for place in shown]
        return [*flags, (label, False), (f" {name}", True)]
    role = short_role(item.get("role")) if full else ""
    year = _text(item.get("year"))
    return [(name, True), *([(f" {year}", False)] if year else []), *([(f" {role}", False)] if role else [])]


def _almanac_forms(key, items):
    """Each entry's forms, fullest first: a person with, then without, what they were; a run of
    holidays in the same places as one entry naming them all ("[flag] 中国 光棍节、双十一购物狂欢节"),
    then naming fewer of them."""
    if key != "holidays":
        for item in items:
            if isinstance(item, dict):
                yield [_almanac_entry(key, item, full) for full in (True, False)]
        return
    runs = []
    for item in items:
        if not isinstance(item, dict) or not _text(item.get("name")):
            continue
        if runs and item.get("places") and item.get("places") == runs[-1][0].get("places"):
            runs[-1][1].append(_text(item.get("name")))
        else:
            runs.append((item, [_text(item.get("name"))]))
    for item, names in runs:
        yield [_almanac_entry(key, {**item, "name": "、".join(names[:count])}) for count in range(len(names), 0, -1)]


def almanac_icon(kind, height):
    """The ``kind`` icon ("cake", "candle", "lantern") ``height`` px tall (and 5/4 as wide) as an
    alpha mask of solid shapes: what is drawn 255, the gaps that shape it cut back to 0."""
    grid_w, grid_h = ALMANAC_ICON_GRID
    width = round(height * grid_w / grid_h)
    scale = height * ALMANAC_ICON_SUPERSAMPLE / grid_h
    mask = Image.new("L", (width * ALMANAC_ICON_SUPERSAMPLE, height * ALMANAC_ICON_SUPERSAMPLE), 0)
    pen = ImageDraw.Draw(mask)

    def box(x0, y0, x1, y1):
        return (round(x0 * scale), round(y0 * scale), round(x1 * scale), round(y1 * scale))

    def point(x, y):
        return (round(x * scale), round(y * scale))

    def flame(x0, y0, x1, y1):
        """A teardrop: round below, pointed above."""
        across = x1 - x0
        pen.ellipse(box(x0, y1 - across, x1, y1), fill=255)
        pen.polygon([point((x0 + x1) / 2, y0), point(x0 + 0.6, y1 - across / 2), point(x1 - 0.6, y1 - across / 2)],
                    fill=255)

    if kind == "cake":
        # One wide tier with a wave of icing cut across it, a candle lit on top.
        flame(34, 0, 46, 17)
        pen.rectangle(box(37, 18, 43, 29), fill=255)
        pen.rounded_rectangle(box(6, 29, 74, 63), radius=round(7 * scale), fill=255)
        wave = [point(x, 44 + 4 * math.sin((x - 6) / 68 * 3 * math.tau)) for x in range(4, 77, 2)]
        pen.line(wave, fill=0, width=max(1, round(5 * scale)), joint="curve")
    elif kind == "candle":
        # A pillar candle with a drip of wax, its flame clear of the wick, in a dish.
        flame(33, 0, 47, 23)
        pen.rounded_rectangle(box(30, 28, 50, 58), radius=round(3 * scale), fill=255)
        pen.rounded_rectangle(box(45, 27, 54, 42), radius=round(4 * scale), fill=255)
        pen.rounded_rectangle(box(14, 56, 66, 63), radius=round(3 * scale), fill=255)
    elif kind == "lantern":
        # A round paper lantern: hanger, caps, ribs cut through its body, and a tassel.
        pen.rectangle(box(37.5, 0, 42.5, 6), fill=255)
        pen.rounded_rectangle(box(26, 5, 54, 13), radius=round(2 * scale), fill=255)
        pen.ellipse(box(6, 9, 74, 52), fill=255)
        pen.arc(box(22, 11, 58, 50), start=0, end=360, fill=0, width=max(1, round(4 * scale)))
        pen.rectangle(box(38, 12, 42, 49), fill=0)
        pen.rounded_rectangle(box(26, 48, 54, 56), radius=round(2 * scale), fill=255)
        pen.polygon([point(35, 56), point(45, 56), point(49, 63.9), point(31, 63.9)], fill=255)
    return mask.resize((width, height), RESAMPLE)


def fit_flag(source, height):
    """``source`` cropped to its visible pixels and contained in a ``height`` tall, twice as wide
    box: as tall as ``height`` at its own proportions up to 2:1, a wider flag that wide and
    shorter. Never stretched."""
    flag = source.convert("RGBA")
    bounds = flag.getchannel("A").getbbox()
    if bounds:
        flag = flag.crop(bounds)
    return ImageOps.contain(flag, (2 * height, height), method=RESAMPLE)


def _joined_runs(entries):
    runs = []
    for index, entry in enumerate(entries):
        runs += ([ALMANAC_SEPARATOR] if index else []) + list(entry)
    return runs


def _without_one_event(events, image_index):
    """``events`` whole, then without their last event that carries no picture: each as
    (events, the picture's index among them, the omitted events' audit records)."""
    yield events, image_index, []
    drop = next((index for index in range(len(events) - 1, -1, -1) if index != image_index), None)
    if drop is None or len(events) < 2:
        return
    kept = events[:drop] + events[drop + 1:]
    index = image_index - 1 if image_index is not None and image_index > drop else image_index
    yield kept, index, [{"year": _text(events[drop].get("year")), "text": _text(events[drop].get("text")),
                         "reason": "almanac"}]


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
        self.palette = plugin._palette(settings, self.date)
        self.image = Image.new("RGB", (self.width, self.height), self.palette["background"])
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}
        self.flags = {}
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
            "colour_scheme": self.palette["scheme"],
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
            "typography": {
                "family": "Noto Sans SC",
                "font_file": SWISS_FONT_PATH.name,
                "weights": {"heading": 900, "body": 500, "year": 900, "date_rail": 900, "date": 800},
                "year_horizontal_scale": 0.77,
                "fallback_used": False,
            },
        }

    def px(self, value):
        return max(1, round(value * self.scale))

    def font(self, text, size, bold=False, weight=None):
        weight = weight if weight is not None else 900 if bold else 500
        key = (size, weight)
        if key not in self.fonts:
            try:
                font = load_font(str(SWISS_FONT_PATH), size)
                axes = font.get_variation_axes()
                values = [weight if axis["name"] == b"Weight" else axis["default"] for axis in axes]
                font.set_variation_by_axes(values)
                self.fonts[key] = font
            except (OSError, ValueError, AttributeError) as exc:
                family = "__cjk__" if re.search(r"[\u3400-\u9fff]", text) else self.family
                self.fonts[key] = self.plugin._font(family, size, "bold" if weight >= 700 else "normal")
                self.audit["typography"]["fallback_used"] = True
                self.audit["typography"]["fallback_reason"] = type(exc).__name__
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

    def wrap(self, text, font, width_for_line, max_lines=None):
        """Return contiguous slices; joining them reproduces the entire input.

        With ``max_lines`` the wrap stops once it has one line too many, which is
        enough to tell that ``text`` does not fit.
        """
        lines = []
        cursor = 0
        while cursor < len(text):
            if max_lines is not None and len(lines) > max_lines:
                break
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
                # A straight '"' closes a quote when an odd number precede it.
                while end - cursor > 1 and (
                    text[end] in closing or (text[end] == '"' and text.count('"', 0, end) % 2 == 1)
                ):
                    end -= 1
                while end - cursor > 1 and (
                    text[end - 1] in opening or (text[end - 1] == '"' and text.count('"', 0, end - 1) % 2 == 0)
                ):
                    end -= 1
            lines.append(text[cursor:end])
            cursor = end
        return lines

    def shortened(self, text, font, width_for_line, max_lines):
        """Return the longest cut of ``text`` that wraps into ``max_lines`` with an ellipsis."""
        def marked(prefix):
            prefix = prefix.rstrip(" ，、,；;：:")
            return prefix + (" " if prefix[-1:] in ".!?" else "") + ELLIPSIS

        def fits(prefix):
            return len(self.wrap(marked(prefix), font, width_for_line, max_lines)) <= max_lines

        def longest_fitting(cuts):
            # Fit only shrinks as a cut moves later, so binary search the cuts.
            low, high, best = 0, len(cuts) - 1, None
            while low <= high:
                middle = (low + high) // 2
                if fits(text[:cuts[middle]]):
                    best, low = cuts[middle], middle + 1
                else:
                    high = middle - 1
            return best

        for pattern in (SENTENCE_END, CLAUSE_END):
            cut = longest_fitting([match.end() for match in pattern.finditer(text)
                                   if match.end() < len(text)])
            if cut is not None:
                return marked(text[:cut])
        return marked(text[:longest_fitting(list(range(1, len(text)))) or 0])

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

    def photo_glow(self, source, box):
        """Fill the photograph's slot with a soft, washed-out wash of the photograph itself,
        fading into the page at the slot's edges, so the picture melts into the page.

        The photograph is then drawn whole on top: its four corners stay sharp.
        """
        x1, y1, x2, y2 = box
        size = (x2 - x1, y2 - y1)
        if min(size) < 8:
            return
        wash = ImageOps.fit(source.convert("RGB"), size, method=RESAMPLE)
        wash = wash.filter(ImageFilter.GaussianBlur(self.px(PHOTO_GLOW_BLUR)))
        wash = Image.blend(Image.new("RGB", size, self.palette["background"]), wash, PHOTO_GLOW_STRENGTH)
        feather = self.px(PHOTO_GLOW_FEATHER)
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rectangle((feather, feather, size[0] - 1 - feather, size[1] - 1 - feather), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(feather / 2))
        region = self.image.crop(box)
        self.image.paste(Image.composite(wash, region, mask), (x1, y1))

    def two_tone_artwork(self, path, size, crop=True, accent="accent"):
        """Fit black + accent-blue artwork into ``size`` and recolour it with this palette.

        The asset's blue channel is the accent share (black ink is 0, the accent is 255),
        so day and night keep identical shapes with their own ink and ``accent`` role.
        """
        with Image.open(path) as original:
            source_size = list(original.size)
            artwork = original.convert("RGBA")
        bbox = artwork.getchannel("A").getbbox()
        if bbox is None:
            return None
        if crop:
            artwork = artwork.crop(bbox)
        artwork = ImageOps.contain(artwork, size, method=RESAMPLE)
        layer = Image.composite(Image.new("RGB", artwork.size, self.palette[accent]),
                                Image.new("RGB", artwork.size, self.palette["ink"]), artwork.getchannel("B"))
        return layer, artwork.getchannel("A"), source_size, list(bbox)

    def title_asset(self, title, available):
        if not self.cjk:
            return None
        try:
            artwork = self.two_tone_artwork(TITLE_ASSET_PATH,
                                            (min(self.px(248), available), self.rule_y - self.px(12)))
            if artwork is None:
                return None
            layer, mask, source_size, bbox = artwork
            y = self.rule_y - self.px(6) - mask.height
            self.image.paste(layer, (self.margin, y), mask)
            return {"text": title, "bounds": [self.margin, y, self.margin + mask.width, y + mask.height],
                    "asset": TITLE_ASSET_PATH.name, "asset_used": True, "fit": "contain",
                    "source_size": source_size, "alpha_source_bounds": bbox}
        except (OSError, ValueError):
            return None

    def header_sources(self, left, right, axis):
        """Wikipedia's and Wikimedia's logos from ``left`` on ``axis``, in their own colours straight
        on the page; None when the page is not from Wikimedia, a logo is missing or they do not fit."""
        if not _from_wikimedia(self.payload):
            return None
        height = self.px(HEADER_LOGO_HEIGHT)
        logos = [_logo(path, height) for path in (WIKIPEDIA_LOGO_PATH, WIKIMEDIA_LOGO_PATH)]
        gap = self.px(HEADER_LOGO_GAP)
        if any(logo is None for logo in logos) or left + sum(logo.width for logo in logos) + gap > right:
            return None
        records, x = [], left
        for logo, path in zip(logos, (WIKIPEDIA_LOGO_PATH, WIKIMEDIA_LOGO_PATH)):
            y = axis - logo.height // 2
            self.image.paste(logo, (x, y), logo)
            records.append({"asset": path.name, "bounds": [x, y, x + logo.width, y + logo.height]})
            x += logo.width + gap
        return records

    def header_ornament(self, left, right, axis):
        """Fill the header gap with the shape strip, ruled out to ``left`` and ``right`` on ``axis``:
        the stand-in when the sources' logos cannot be shown."""
        gap = self.px(12)
        if right - left < self.px(160):
            return None
        try:
            artwork = self.two_tone_artwork(ORNAMENT_ASSET_PATH, (right - left - self.px(64), self.px(48)),
                                            crop=False, accent="accent2")
        except (OSError, ValueError):
            return None
        if artwork is None:
            return None
        layer, mask, source_size, _bbox = artwork
        x = (left + right - mask.width) // 2
        y = axis - mask.height // 2
        self.image.paste(layer, (x, y), mask)
        rules = [[x1, axis, x2, axis] for x1, x2 in ((left, x - gap), (x + mask.width + gap, right))]
        for rule in rules:
            self.draw.line(rule, fill=self.palette["ink"], width=self.px(2))
        return {"asset": ORNAMENT_ASSET_PATH.name, "bounds": [x, y, x + mask.width, y + mask.height],
                "rules": rules, "source_size": source_size}

    def year_ink(self, text, x, y, font):
        if not re.fullmatch(r"-?\d+", text):
            return self.ink(text, x, y, font)
        bbox = self.draw.textbbox((0, 0), text, font=font)
        mask = Image.new("L", (bbox[2] - bbox[0], bbox[3] - bbox[1]), 0)
        ImageDraw.Draw(mask).text((-bbox[0], -bbox[1]), text, font=font, fill=255)
        width = min(self.px(62), max(1, round(mask.width * 0.77)))
        mask = mask.resize((width, mask.height), RESAMPLE)
        self.image.paste(self.palette["ink"], (x, y), mask)
        return {"text": text, "bounds": [x, y, x + mask.width, y + mask.height],
                "font_size": getattr(font, "size", 34), "font_weight": 900, "condensed": True}

    def header(self):
        title = "每日图片" if self.cjk else "DAILY IMAGE"
        date_text = self.date.strftime("%Y.%m.%d")
        date_font = self.font(date_text, max(self.minimum_font, self.px(17)), weight=800)
        date_width = math.ceil(self.width_of(date_text, date_font))
        date_x = self.right - date_width
        available = date_x - self.margin - self.px(24)
        title_record = self.title_asset(title, available)
        if title_record is None:
            for size in range(self.px(48), self.minimum_font - 1, -1):
                title_font = self.font(title, size, bold=True)
                if self.width_of(title, title_font) <= available:
                    break
            else:
                raise LayoutOverflowError("The page title and source date cannot fit the header.")
            title_y = self.rule_y - self.px(11) - self.glyph_height(title, title_font)
            title_record = self.ink(title, self.margin, title_y, title_font)
            title_record.update(asset_used=False, font_weight=900)
        self.audit["header"]["title"] = title_record
        date_y = self.rule_y - self.px(11) - self.glyph_height(date_text, date_font)
        self.audit["header"]["date"] = self.ink(date_text, date_x, date_y, date_font)
        title_bounds = title_record["bounds"]
        gap = (title_bounds[2] + self.px(16), date_x - self.px(16), (title_bounds[1] + title_bounds[3]) // 2)
        # The title, then the sources by their official logos, then the date.
        self.audit["header"]["sources"] = self.header_sources(*gap)
        self.audit["header"]["ornament"] = None if self.audit["header"]["sources"] else self.header_ornament(*gap)
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
                            fill=self.palette["accent2"])

    def doodles(self):
        """Lay the photograph column's camera doodles and the history column's keepsakes
        in the day's accent, before either column draws its content."""
        for name, box in (("photo", (self.photo_left, self.top, self.photo_right, self.bottom)),
                          ("history", (self.history_left, self.top, self.right, self.bottom))):
            paint_doodles(self.image, box, DOODLE_DIR / f"{name}.png", self.palette["accent"], offset=box[:2])

    def photograph(self, source):
        text = _text(self.payload.get("image_caption") or self.payload.get("daily_image_title")
                     or self.payload.get("title"))
        width = self.photo_right - self.photo_left
        # A Wikimedia picture is credited by Wikimedia's logo before the rest of the source line.
        source_label = self.plugin._source_label(self.payload)
        source_font = self.font(source_label, self.minimum_font)
        source_step = self.line_height(source_label, source_font)
        logo = _source_logo(self.payload, round(source_step * WIKIMEDIA_LOGO_SCALE))
        indent = logo.width + self.px(6) if logo is not None else 0
        if logo is not None:
            source_label = self.plugin._source_label(self.payload, named=False)
        source_lines = self.wrap(source_label, source_font, lambda index: width - (indent if index == 0 else 0))
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
            # A long caption keeps every word at the minimum size; the photograph
            # gives up height down to a smaller floor. Only a caption that would
            # push it below that floor is cut, visibly, at a sentence or clause.
            floor = self.px(MIN_SHORT_PHOTOGRAPH_HEIGHT)
            if photo_bottom - self.top < floor:
                max_lines = (footer_y - self.px(18) - self.top - floor) // step
                if max_lines < 1:
                    raise LayoutOverflowError(
                        "The daily-image caption has no room above the minimum photograph height."
                    )
                lines = self.wrap(self.shortened(text, font, lambda _: width, max_lines),
                                  font, lambda _: width)
                caption_y = footer_y - self.px(9) - len(lines) * step
                photo_bottom = caption_y - self.px(9)
        photo_box = (self.photo_left, self.top, self.photo_right, photo_bottom)
        if source is not None:
            self.photo_glow(source, photo_box)
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
                                 "complete": "".join(lines) == text,
                                 "shortened": "".join(lines) != text}
        if logo is not None:
            self.image.paste(logo, (self.photo_left, footer_y + (source_step - logo.height) // 2), logo)
        source_records = [self.ink(line, self.photo_left + (indent if index == 0 else 0), footer_y + index * source_step,
                                  source_font, self.palette["muted"])
                          for index, line in enumerate(source_lines)]
        self.audit["source"] = {"source_text": source_label, "lines": source_records, "complete": True}

    def history(self, source):
        enabled = self.plugin._enabled(self.settings.get("showOnThisDay"), default=True)
        raw_events = (self.payload.get("on_this_day") or []) if enabled else []
        if any(not isinstance(event, dict) for event in raw_events):
            raise LayoutOverflowError("History content must consist of complete event records.")
        title = "历史上的今天" if self.cjk else "ON THIS DAY"
        title_font = self.font(title, self.px(35), bold=True)
        title_x = self.history_left + self.px(36)
        while self.width_of(title, title_font) > self.right - title_x:
            size = getattr(title_font, "size", self.px(35)) - 1
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
            if self.width_of(label, font) <= self.right - title_x:
                label_y = max(label_y, title_bottom + self.px(2))
                heading_bottom = max(heading_bottom, label_y + self.glyph_height(label, font) + self.px(3))
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
        # 今日人物与节日 takes the column's foot only while the events above it still fit
        # whole: all of them, else all but one (never the one with the picture); else it stays off.
        # Its holidays take a second line only from room all the events leave unused.
        almanac = self.almanac_rows() if enabled else []
        selected = None
        if almanac:
            room = available - self.almanac_height(almanac)
            for events, index, omitted in _without_one_event(raw_events, image_index):
                selected, size = self.fit_history_rows(events, source, index, room)
                if selected is not None:
                    roomier = None if omitted else self.roomier_almanac(events, source, index, available, almanac)
                    if roomier is not None:
                        almanac, room, selected = roomier
                    raw_events, image_index, available = events, index, room
                    self.audit["omitted_events"].extend(omitted)
                    break
            if selected is None:
                almanac = []
        if selected is None:
            selected, size = self.fit_history_rows(raw_events, source, image_index, available)
        needed = 0 if selected is not None else sum(
            row["height"] for row in self.history_rows(raw_events, source, image_index, self.minimum_font,
                                                       self.px(64), compact=True))
        if selected is None:
            selected = self.shortened_history_rows(raw_events, source, image_index, available)
        if selected is None:
            raise LayoutOverflowError(
                f"Complete history needs {needed}px but has {available}px at {self.minimum_font}px; "
                f"all {len(raw_events)} events were retained; no image or text was omitted."
            )
        needed = sum(row["height"] for row in selected)
        extra, remainder = divmod(available - needed, len(selected))
        y = body_top
        for index, row in enumerate(selected):
            height = row["height"] + extra + (1 if index < remainder else 0)
            top = y + row["inset"]
            year_text = self.year_ink(row["year"], self.history_left, top, row["year_font"])
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
            source_text = row.get("source_text", row["text"])
            self.audit["events"].append({
                "index": index, "year": row["year"], "year_label": year_text,
                "source_text": source_text, "lines": lines,
                "bounds": [self.history_left, y, self.right, y + height],
                "font_size": size, "image": image_record,
                "complete": "".join(row["lines"]) == source_text,
                "shortened": row["text"] != source_text,
            })
            y += height
            if index < len(selected) - 1:
                self.draw.line((self.history_left, y - self.px(1), self.right, y - self.px(1)),
                               fill=self.palette["ink"], width=self.px(1))
        if almanac:
            self.draw_almanac(almanac, body_top + available)

    def fit_history_rows(self, events, source, image_index, available):
        """The events' rows at the largest size that fits ``available`` whole, and that size;
        (None, the minimum size) when none does."""
        # Days that fit with normal spacing render exactly as before; only a day
        # that would otherwise fail tightens row padding at the minimum size.
        attempts = [(size, False) for size in range(max(self.minimum_font, self.px(17)),
                                                    self.minimum_font - 1, -1)]
        attempts.append((self.minimum_font, True))
        for size, compact in attempts:
            for thumbnail_width in (92, 76, 64):
                rows = self.history_rows(events, source, image_index, size,
                                         self.px(thumbnail_width), compact=compact)
                if sum(row["height"] for row in rows) <= available:
                    self.audit["history_fit"] = {"font_size": size, "compact": compact,
                                                 "thumbnail_width": self.px(thumbnail_width)}
                    return rows, size
        return None, self.minimum_font

    def almanac_rows(self, holiday_lines=1):
        """今日人物与节日 as rows {key, icon, colour, lines}, each line a list of entries and each
        entry a list of (text, bold) runs (FlagMark runs lead a placed holiday); a row keeps the
        entries that fit its line whole (the holidays' up to ``holiday_lines`` lines, each entry
        on the first with room), and a row with none is left out."""
        almanac = self.payload.get("almanac") if self.cjk else None
        if not isinstance(almanac, dict):
            return []
        width = self.right - self.history_left - self.px(ALMANAC_ICON_SLOT) - self.px(ALMANAC_CHIP_GAP)
        rows = []
        for key, icon, colour in ALMANAC_ROWS:
            lines = [[] for _line in range(max(1, holiday_lines) if key == "holidays" else 1)]
            # A person whose role does not fit is still named, by name and year.
            for forms in _almanac_forms(key, almanac.get(key) or []):
                placement = next(((line, entry) for entry in forms if entry for line in lines
                                  if self.runs_width(_joined_runs([*line, entry])) <= width), None)
                if placement is not None:
                    placement[0].append(placement[1])
            lines = [line for line in lines if line]
            if lines:
                rows.append({"key": key, "icon": icon, "colour": colour, "lines": lines})
        return rows

    def roomier_almanac(self, events, source, image_index, available, rows):
        """(the almanac with its holidays over more lines, the history's room under it, the history
        rows) when the holidays left some out and the events keep the same type in the smaller
        room (size, spacing and thumbnail); else None."""
        if ALMANAC_HOLIDAY_LINES < 2 or not any(row["key"] == "holidays" for row in rows):
            return None
        wider = self.almanac_rows(ALMANAC_HOLIDAY_LINES)
        if sum(len(row["lines"]) for row in wider) <= sum(len(row["lines"]) for row in rows):
            return None
        before = self.audit.get("history_fit")
        room = available - self.almanac_height(wider)
        selected, _size = self.fit_history_rows(events, source, image_index, room)
        if selected is not None and self.audit.get("history_fit") == before:
            return wider, room, selected
        self.audit["history_fit"] = before
        return None

    def almanac_fonts(self):
        return {bold: self.font("国", self.minimum_font, weight=800 if bold else 500) for bold in (False, True)}

    def almanac_flag(self, mark):
        """The flag a FlagMark names, sized for an almanac line (fit_flag), or None."""
        if mark not in self.flags:
            flag = None
            getter = getattr(self.plugin, "_holiday_flag", None)
            source = getter(mark.key, mark.url, self.settings) if callable(getter) else None
            if source is not None:
                flag = fit_flag(source, self.glyph_height("国", self.almanac_fonts()[False]))
            self.flags[mark] = flag
        return self.flags[mark]

    def runs_width(self, runs):
        """How far ``runs`` reach as draw_almanac lays them (a flag starts on a whole pixel, with
        its hairline ring and gap; one that cannot load takes no room)."""
        fonts, x = self.almanac_fonts(), 0
        for text, bold in runs:
            if bold != FLAG:
                x += self.draw.textlength(text, font=fonts[bold])
            elif (flag := self.almanac_flag(text)) is not None:
                x = round(x) + flag.width + 2 + self.px(ALMANAC_FLAG_GAP)
        return x

    def almanac_line_height(self):
        return self.line_height("国", self.almanac_fonts()[False])

    def almanac_height(self, rows):
        lines = sum(len(row["lines"]) for row in rows)
        return self.px(ALMANAC_TOP_GAP) + lines * (self.almanac_line_height() + self.px(ALMANAC_ROW_GAP))

    def draw_almanac(self, rows, top):
        """The rows from ``top`` (a rule over them) to the column's foot: each one's icon, solid in
        its category's colour and centred in its slot by its first line, then its entries in ink on
        one baseline per line; a flag sits on the Chinese glyphs' middle, ringed in the rule colour."""
        self.draw.line((self.history_left, top, self.right, top), fill=self.palette["ink"], width=self.px(2))
        fonts = self.almanac_fonts()
        slot, line_h = self.px(ALMANAC_ICON_SLOT), self.almanac_line_height()
        height = line_h + self.px(ALMANAC_ROW_GAP) - self.px(ALMANAC_ICON_SPACING)
        ascent = fonts[False].getmetrics()[0] if hasattr(fonts[False], "getmetrics") else line_h
        y = top + self.px(ALMANAC_TOP_GAP)
        records = []
        for row in rows:
            icon = almanac_icon(row["icon"], height)
            icon_box = [self.history_left + (slot - icon.width) // 2, y + (line_h - height) // 2]
            icon_box += [icon_box[0] + icon.width, icon_box[1] + height]
            self.image.paste(self.palette[row["colour"]], tuple(icon_box), icon)
            row_top, texts, flags, right = y, [], [], self.history_left
            for line in row["lines"]:
                x = self.history_left + slot + self.px(ALMANAC_CHIP_GAP)
                baseline = y + (line_h - self.px(2) - ascent) // 2 + ascent
                glyph = self.draw.textbbox((x, baseline), "国", font=fonts[False], anchor="ls")
                runs = _joined_runs(line)
                for text, bold in runs:
                    if bold == FLAG:
                        flag = self.almanac_flag(text)
                        if flag is None:
                            continue
                        left, flag_y = round(x), (glyph[1] + glyph[3] - flag.height) // 2
                        self.image.paste(flag, (left + 1, flag_y), flag)
                        self.draw.rectangle((left, flag_y - 1, left + flag.width + 1, flag_y + flag.height),
                                            outline=self.palette["rule"], width=1)
                        flags.append({"key": text.key, "url": text.url,
                                      "bounds": [left, flag_y - 1, left + flag.width + 2, flag_y + flag.height + 1]})
                        x = left + flag.width + 2 + self.px(ALMANAC_FLAG_GAP)
                        continue
                    self.draw.text((x, baseline), text, font=fonts[bold], fill=self.palette["ink"], anchor="ls")
                    x += self.draw.textlength(text, font=fonts[bold])
                if x > self.right + 1:
                    raise LayoutOverflowError("An almanac line would exceed the history column.")
                texts.append("".join(text for text, bold in runs if bold != FLAG))
                right = max(right, round(x))
                y += line_h + self.px(ALMANAC_ROW_GAP)
            records.append({"key": row["key"], "icon": row["icon"], "icon_bounds": icon_box,
                            "text": ALMANAC_SEPARATOR[0].join(texts), "lines": texts, "flags": flags,
                            "bounds": [self.history_left, row_top, right, y - self.px(ALMANAC_ROW_GAP)]})
        self.audit["almanac"] = {"rows": records, "bounds": [self.history_left, top, self.right, self.bottom]}

    def shortened_history_rows(self, events, source, image_index, available):
        """Cap the longest events' line count, at the tightest layout, until all fit.

        Every event keeps its row and the opening of its text; a cut entry ends
        with an ellipsis and the audit keeps the complete source text.
        """
        thumbnail_width = self.px(64)
        originals = [_text(event.get("text")) for event in events]
        full_rows = self.history_rows(events, source, image_index, self.minimum_font,
                                      thumbnail_width, compact=True)
        # No event can use more lines than the whole column holds.
        column_lines = available // min(row["step"] for row in full_rows)
        for cap in range(min(max(len(row["lines"]) for row in full_rows) - 1, column_lines), 0, -1):
            capped = [dict(event, text=original if len(row["lines"]) <= cap
                           else self.shortened(original, row["font"], row["line_width"], cap))
                      for event, original, row in zip(events, originals, full_rows)]
            rows = self.history_rows(capped, source, image_index, self.minimum_font,
                                     thumbnail_width, compact=True)
            if sum(row["height"] for row in rows) <= available:
                for row, original in zip(rows, originals):
                    row["source_text"] = original
                self.audit["history_fit"] = {
                    "font_size": self.minimum_font, "compact": True, "thumbnail_width": thumbnail_width,
                    "shortened": sum(row["text"] != original for row, original in zip(rows, originals)),
                }
                return rows
        return None

    def history_rows(self, events, source, image_index, font_size, thumbnail_width, compact=False):
        rows = []
        inset, below = (self.px(value) for value in HISTORY_ROW_PADDING[1 if compact else 0])
        box_width, box_height = HISTORY_THUMBNAIL_BOX
        thumbnail_box = (thumbnail_width, max(1, round(thumbnail_width * box_height / box_width)))
        text_x = self.history_left + self.px(75)
        full_width = self.right - text_x
        for index, event in enumerate(events):
            text, year = _text(event.get("text")), _text(event.get("year"))
            font = self.font(text, font_size)
            step = self.line_height(text, font)
            image_size = None
            if index == image_index:
                image_size = ImageOps.contain(source, thumbnail_box, method=RESAMPLE).size
            def line_width(line_index, image_size=image_size, step=step):
                if image_size and line_index * step < image_size[1]:
                    return full_width - image_size[0] - self.px(8)
                return full_width
            lines = self.wrap(text, font, line_width)
            year_font_size = self.px(34)
            year_font = self.font(year, year_font_size, bold=True)
            while not re.fullmatch(r"-?\d+", year) and self.width_of(year, year_font) > self.px(62):
                year_font_size -= 1
                if year_font_size < self.minimum_font:
                    raise LayoutOverflowError("The complete event year cannot fit its label.")
                year_font = self.font(year, year_font_size, bold=True)
            text_height = (len(lines) - 1) * step + self.glyph_height(lines[-1], font) if lines else 0
            content_height = max(text_height, self.glyph_height(year, year_font),
                                 image_size[1] if image_size else 0)
            rows.append({"text": text, "year": year, "font": font, "year_font": year_font,
                         "lines": lines, "step": step, "text_x": text_x, "image_size": image_size,
                         "content_height": content_height, "inset": inset, "line_width": line_width,
                         "height": content_height + inset + below})
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
    page.doodles()
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
