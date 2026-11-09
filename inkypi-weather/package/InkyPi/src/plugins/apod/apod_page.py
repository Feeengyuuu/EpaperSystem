"""Fixed 800x480 NASAPics and NOAA space-weather PIL renderer, styled as a mission-control display."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import math
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from plugins.apod.space_weather import SpaceWeatherSnapshot
from utils.space_typography import MixedDraw, space_font
from utils.app_utils import get_base_ui_font
from utils.local_wordmark import load_source_logo
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from plugins.apod.apod import ApodRecord


HEADER_RECT = (0, 0, 800, 65)
WORDMARK_RECT = (20, 8, 452, 54)
WORDMARK_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "nasapics_space_weather_wordmark.png"
)
WORDMARK_FALLBACK_TEXT = "NASAPics × SPACE WEATHER"
# The caption's kicker when the day's APOD is a video shown by its still frame.
VIDEO_STILL_KICKER = "TODAY'S VIDEO | STILL FRAME"
# NASA's insignia at the caption's lower right (px tall).
NASA_INSIGNIA_PATH = Path(__file__).resolve().parent / "nasa_logo.png"
NASA_INSIGNIA_HEIGHT = 46
# Its gap to the caption lines beside it; smaller heights are tried when long lines run under it.
NASA_INSIGNIA_GAP, NASA_INSIGNIA_MIN_HEIGHT = 10, 28
LEFT_RECT = (0, 65, 368, 480)
KP_RECT = (16, 74, 352, 190)
GRS_RECT = (16, 198, 352, 236)
METRICS_RECT = (16, 244, 352, 330)
METRIC_GAP = 8
PROBABILITIES_RECT = (16, 338, 352, 384)
ALERT_RECT = (16, 392, 352, 434)
SOURCE_RECT = (16, 448, 352, 472)
RIGHT_X = 368
# The open readout column: every reading starts PANEL_INSET inside its zone, labels small above
# DM Mono values; no tiles. The G/R/S and chance columns keep GRS_GAP between them.
PANEL_INSET = 4
GRS_GAP = 10
READING_SIZE = 20
ALERT_RULE_WIDTH = 3
# The Kp dial: KP_SEGMENTS bars KP_BAR_LENGTH long, lit in G-scale colours up to the reading and
# in the bar-track navy beyond it; the number and its words start KP_TEXT_LEFT into the zone.
KP_SEGMENTS = 27
KP_BAR_LENGTH = 13
KP_GAUGE_RADIUS = 66
KP_TEXT_LEFT = 160
G_LEVELS = {"G0": "平静", "G1": "弱磁暴", "G2": "中等磁暴", "G3": "强磁暴", "G4": "严重磁暴",
            "G5": "极端磁暴"}
KP_MODES = {"estimated": "估算", "observed": "实测", "unavailable": "暂无读数"}
# With no tiles over it, the photo's faded left edge is covered by the page ground: solid to
# SCRIM_SOLID, clearing by SCRIM_CLEAR, so the readings never sit on the picture.
SCRIM_SOLID = 350
SCRIM_CLEAR = 410
ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"
# JPL's sky watch takes the storm-chance row (those chances move up beside the R and S lamps):
# the NEOs passing within ten lunar distances on a square-root scale from Earth, the Moon marked
# at one; the nearest one named at the right. JPL's logo names the source.
NEO_RECT = PROBABILITIES_RECT
JPL_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logos" / "jpl.png"
NEO_SCALE_LD = 10.0
EARTH_COLOR = (64, 140, 255)
MOON_COLOR = (200, 206, 214)
# Generated sky icons (assets/sky_icons): Earth, the Moon, an asteroid and the nearest one's
# glowing form on the strip, and each planet before its name on tonight's line.
SKY_ICON_DIR = Path(__file__).resolve().parent / "assets" / "sky_icons"
NEO_ICON_SIZES = {"earth": 16, "moon": 11, "asteroid": 9, "asteroid_near": 15}
PLANET_ICONS = {"水星": "mercury", "金星": "venus", "火星": "mars", "木星": "jupiter", "土星": "saturn"}
PLANET_ICON_SIZE = 15
PACIFIC = ZoneInfo("America/Los_Angeles")

PAGE_SIZE = (800, 480)
CAPTION_MIN_TOP = 300
CAPTION_MAX_TOP = 364
PHOTO_TOP = 65
# The photo runs in under the instrument tiles and fades in over PHOTO_FADE pixels, whole by the
# tiles' right edge: the blend shows only between the tiles, and past them the photo is at full
# strength (fading out past the tiles left a dark band beside them).
PHOTO_LEFT = 220
PHOTO_FADE = 150
PHOTO_RECT = (PHOTO_LEFT, PHOTO_TOP, PAGE_SIZE[0], PAGE_SIZE[1])
# The caption sits on the photo, which darkens toward the foot from just above it.
CAPTION_SHADE_LEAD = 36
CAPTION_SHADE_ALPHA = 225
RIGHT_INSET = 12
RIGHT_TEXT_RECT = (RIGHT_X + RIGHT_INSET, 0, 800 - RIGHT_INSET, 480)
KICKER_ICON_ROOM = 18

PAGE_COLOR = (7, 16, 31)
HEADER_COLOR = (9, 21, 40)
# Close to the panel's own blue, so a one-pixel outline stays a line instead of dithering to dots.
TILE_OUTLINE = (0, 60, 230)
# Small type and the line icons are pure white: a tint or grey dithers to speckle on the panel,
# so labels and muted copy differ from values by size and weight, not colour.
TEXT_COLOR = (255, 255, 255)
INK_COLOR = TEXT_COLOR
SUBTITLE_COLOR = TEXT_COLOR
LABEL_COLOR = TEXT_COLOR
MUTED_COLOR = TEXT_COLOR
DIVIDER_COLOR = TILE_OUTLINE
GREEN_COLOR = (62, 214, 120)
AMBER_COLOR = (255, 182, 62)
ORANGE_COLOR = (255, 132, 54)
RED_COLOR = (238, 72, 64)
CYAN_COLOR = (69, 210, 240)
BAR_TRACK_COLOR = (30, 50, 78)
ORBIT_COLOR = (26, 60, 96)
STAR_COLOR = (120, 152, 194)
# The NOAA G scale along the Kp dial: below 5 quiet, then G1 to G5.
KP_ZONES = ((5, GREEN_COLOR), (6, (214, 222, 72)), (7, AMBER_COLOR), (8, ORANGE_COLOR), (9, RED_COLOR),
            (10, (200, 40, 70)))
ORBIT_ARCS = (((470, -230, 1130, 130), 105, 178), ((540, -120, 980, 96), 118, 200), ((-220, -300, 560, 112), 22, 74))
HEADER_STARS = ((488, 14), (523, 40), (571, 22), (612, 51), (655, 12), (702, 33), (741, 52), (768, 15), (452, 58),
                (690, 58))
TEXT_SEPARATOR = " | "


class ApodPageLayoutError(ValueError):
    """Raised when complete bilingual metadata cannot fit the approved page."""


@dataclass(frozen=True)
class CaptionLayout:
    caption_top: int
    caption_height: int
    show_kicker: bool
    kicker_copy: str
    title_en_lines: tuple[str, ...]
    title_zh_lines: tuple[str, ...]
    title_en_font: ImageFont.FreeTypeFont
    title_zh_font: ImageFont.FreeTypeFont | None
    kicker_font: ImageFont.FreeTypeFont
    meta_font: ImageFont.FreeTypeFont
    kicker_y: int | None
    title_zh_y: int | None
    title_en_y: int
    title_bottom: int
    credit_lines: tuple[str, ...]
    credit_y: int
    credit_bottom: int
    date_copy: str
    date_y: int
    date_bottom: int


@dataclass(frozen=True)
class ApodPageMeasurement:
    """Pure measured geometry shared by media admission and final rendering."""

    caption: CaptionLayout
    photo_rect: tuple[int, int, int, int]
    photo_size: tuple[int, int]
    content_signature: tuple[str, ...]


def _measurement_signature(
    *,
    apod: "ApodRecord",
    title_zh: str | None,
    translation_unavailable: bool,
    dimensions: tuple[int, int],
) -> tuple[str, ...]:
    return (
        str(apod.title_en),
        str(title_zh or ""),
        "1" if translation_unavailable else "0",
        str(apod.copyright or ""),
        str(apod.date),
        str(apod.warning or ""),
        "still" if getattr(apod, "video_still", False) else "",
        f"{int(dimensions[0])}x{int(dimensions[1])}",
    )


def fit_photo(source: Image.Image, photo_size: tuple[int, int]) -> Image.Image:
    """Apply EXIF orientation and one centered cover crop with no letterboxing."""

    transposed = ImageOps.exif_transpose(source)
    return ImageOps.fit(
        transposed.convert("RGB"),
        photo_size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    font = get_base_ui_font(size, bold=bold)
    if not isinstance(font, ImageFont.FreeTypeFont):
        raise OSError("A real Microsoft YaHei/Base UI font is required")
    return font


def _load_fonts() -> Mapping[str, ImageFont.FreeTypeFont]:
    return {
        "brand": _font(27, bold=True),
        "header_meta": space_font("mono", 10),
        "label": _font(11),
        "label_small": _font(10),
        "kp_number": space_font("mono", 44),
        "kp_unit": space_font("telemetry_regular", 14),
        "kp_peak": _font(10),
        "dial": space_font("mono", 10),
        "grs_value": space_font("mono", 14),
        "grs_name": _font(10),
        "reading": space_font("mono", READING_SIZE),
        "unit": space_font("telemetry_regular", 11),
        "probability_label": space_font("public_semibold", 10),
        "probability_value": space_font("mono", 13),
        "alert": _font(12, bold=True),
        "source": space_font("public_semibold", 10),
        "caption_kicker": space_font("inter", 10),
        "caption_meta": space_font("public_semibold", 10),
    }


def _text_bbox(draw, xy, text, font):
    return draw.textbbox(xy, str(text), font=font)


def _text_width(draw, text, font) -> int:
    bbox = _text_bbox(draw, (0, 0), text, font)
    return bbox[2] - bbox[0]


def _line_height(draw, font) -> int:
    bbox = _text_bbox(draw, (0, 0), "Ag国", font)
    return max(1, bbox[3] - bbox[1] + 2)


def _lines_extent(draw, lines, font) -> int:
    """Return the measured bottom of a line block drawn from y=0."""

    if not lines:
        return 0
    advance = _line_height(draw, font)
    return max(
        index * advance + _text_bbox(draw, (0, 0), line or " ", font)[3]
        for index, line in enumerate(lines)
    )


def _draw_text(draw, xy, text, *, font, fill=INK_COLOR) -> None:
    draw.text(xy, str(text), font=font, fill=fill)


def _draw_centered(draw, rect, text, *, font, fill=INK_COLOR) -> None:
    x1, _y1, x2, _y2 = rect
    bbox = _text_bbox(draw, (0, 0), text, font)
    width = bbox[2] - bbox[0]
    x = x1 + ((x2 - x1) - width) / 2 - bbox[0]
    _draw_text(draw, (x, rect[1]), text, font=font, fill=fill)


def _draw_right(draw, x_right, y, text, *, font, fill=INK_COLOR) -> None:
    bbox = _text_bbox(draw, (0, 0), text, font)
    x = x_right - (bbox[2] - bbox[0]) - bbox[0]
    _draw_text(draw, (x, y), text, font=font, fill=fill)


def _wrap_complete(draw, text: str, font, max_width: int) -> tuple[str, ...]:
    """Greedily wrap every source character without truncation or ellipsis.

    A line breaks after its last space when the word that follows still fits on the
    next line (the space stays at the end of the line, so the lines join back to the
    text); text without spaces, such as Chinese, breaks between any two characters.
    """

    if text == "":
        return ()
    lines: list[str] = []
    current = ""
    for character in text:
        if character in "\r\n":
            if character == "\r":
                continue
            lines.append(current)
            current = ""
            continue
        candidate = current + character
        if current and _text_width(draw, candidate.rstrip(" "), font) > max_width:
            space = current.rfind(" ")
            carry = current[space + 1:] + character if 0 < space < len(current) - 1 else ""
            if carry and character != " " and _text_width(draw, carry, font) <= max_width:
                lines.append(current[:space + 1])
                current = carry
                continue
            lines.append(current)
            current = character
            if _text_width(draw, current, font) > max_width:
                raise ApodPageLayoutError("one title character exceeds the caption width")
        else:
            current = candidate
    if current or not lines:
        lines.append(current)
    return tuple(lines)


def _normalize_inline_metadata(text: str) -> str:
    """Collapse provider formatting whitespace for one compact display field."""

    return " ".join(str(text).split())


def _complete_line_candidates(
    *,
    draw,
    text: str,
    sizes: tuple[int, ...],
    max_width: int,
    max_lines: int,
    bold: bool,
) -> tuple[tuple[tuple[str, ...], ImageFont.FreeTypeFont], ...]:
    candidates = []
    for size in sizes:
        font = space_font("inter" if bold else "public", size) if text.isascii() else _font(size, bold=bold)
        lines = _wrap_complete(draw, text, font, max_width)
        if len(lines) <= max_lines:
            candidates.append((lines, font))
    if not candidates:
        raise ApodPageLayoutError(
            f"complete title does not fit the {max_lines}-line caption limit"
        )
    return tuple(candidates)


def _layout_caption(
    *,
    draw,
    title_en: str,
    title_zh: str | None,
    translation_unavailable: bool,
    copyright: str | None = None,
    apod_date: str = "",
    warning: str | None = None,
) -> CaptionLayout:
    """Measure complete title strings and choose a bounded caption layout."""

    english = str(title_en or "Astronomy Picture of the Day")
    en_candidates = _complete_line_candidates(
        draw=draw,
        text=english,
        sizes=(14, 13, 12, 11),
        max_width=RIGHT_TEXT_RECT[2] - RIGHT_TEXT_RECT[0],
        max_lines=6,
        bold=True,
    )

    chinese = str(title_zh) if title_zh else ""
    if not chinese and translation_unavailable:
        chinese = "中文翻译暂不可用"
    if chinese:
        zh_candidates = _complete_line_candidates(
            draw=draw,
            text=chinese,
            sizes=(20, 19, 18, 17, 16),
            max_width=RIGHT_TEXT_RECT[2] - RIGHT_TEXT_RECT[0],
            max_lines=3,
            bold=True,
        )
    else:
        zh_candidates = (((), None),)

    kicker_font = _font(10, bold=True)
    meta_font = _font(10, bold=True)
    credit_source = _normalize_inline_metadata(copyright or "NASA / APOD")
    credit_copy = f"CREDIT{TEXT_SEPARATOR}{credit_source}"
    credit_lines = _wrap_complete(
        draw,
        credit_copy,
        meta_font,
        RIGHT_TEXT_RECT[2] - RIGHT_TEXT_RECT[0],
    )
    date_copy = f"NASA APOD{TEXT_SEPARATOR}{str(apod_date or 'date unavailable')}"
    if _text_width(draw, date_copy, meta_font) > RIGHT_TEXT_RECT[2] - RIGHT_TEXT_RECT[0]:
        raise ApodPageLayoutError("complete APOD date does not fit the caption")

    def measure(
        *,
        en_lines,
        en_font,
        zh_lines,
        zh_font,
        show_kicker: bool,
    ):
        cursor = 3 + 8
        kicker_y = None
        if show_kicker:
            kicker_y = cursor
            cursor += _lines_extent(
                draw, ("TODAY'S ASTRONOMY PICTURE",), kicker_font
            )
            cursor += 4
        else:
            # Preserve the approved breathing space where the kicker was removed.
            cursor += 10

        zh_y = cursor if zh_lines else None
        if zh_lines and zh_font is not None:
            cursor += _lines_extent(draw, zh_lines, zh_font)
        if zh_lines and en_lines:
            cursor += 4
        en_y = cursor
        cursor += _lines_extent(draw, en_lines, en_font)
        title_bottom = cursor
        cursor += 8
        credit_y = cursor
        cursor += _lines_extent(draw, credit_lines, meta_font)
        credit_bottom = cursor
        cursor += 3
        date_y = cursor
        cursor += _lines_extent(draw, (date_copy,), meta_font)
        date_bottom = cursor
        cursor += 9
        return {
            "required_height": cursor,
            "kicker_y": kicker_y,
            "zh_y": zh_y,
            "en_y": en_y,
            "title_bottom": title_bottom,
            "credit_y": credit_y,
            "credit_bottom": credit_bottom,
            "date_y": date_y,
            "date_bottom": date_bottom,
        }

    maximum_height = PAGE_SIZE[1] - CAPTION_MIN_TOP
    minimum_height = PAGE_SIZE[1] - CAPTION_MAX_TOP
    warning_copy = (
        str(warning or "")
        .strip()
        .replace("\N{MIDDLE DOT}", "|")
        .replace("\N{BULLET}", "|")
    )
    kicker_copy = warning_copy or "TODAY'S ASTRONOMY PICTURE"
    if _text_width(draw, kicker_copy, kicker_font) > (
        RIGHT_TEXT_RECT[2] - RIGHT_TEXT_RECT[0] - KICKER_ICON_ROOM
    ):
        raise ApodPageLayoutError("complete APOD warning exceeds the caption width")

    selected = None
    for zh_lines, zh_font in zh_candidates:
        for en_lines, en_font in en_candidates:
            for show_kicker in ((True,) if warning_copy else (True, False)):
                measured = measure(
                    en_lines=en_lines,
                    en_font=en_font,
                    zh_lines=zh_lines,
                    zh_font=zh_font,
                    show_kicker=show_kicker,
                )
                if measured["required_height"] <= maximum_height:
                    selected = (
                        en_lines,
                        en_font,
                        zh_lines,
                        zh_font,
                        show_kicker,
                        measured,
                    )
                    break
            if selected is not None:
                break
        if selected is not None:
            break
    if selected is None:
        if warning_copy:
            raise ApodPageLayoutError(
                "complete fallback metadata exceeds the 180px caption limit"
            )
        raise ApodPageLayoutError(
            "complete bilingual metadata exceeds the 180px caption limit"
        )
    en_lines, en_font, zh_lines, zh_font, show_kicker, measured = selected

    caption_height = max(minimum_height, measured["required_height"])
    caption_top = PAGE_SIZE[1] - caption_height

    def absolute(name):
        value = measured[name]
        return None if value is None else caption_top + value

    return CaptionLayout(
        caption_top=caption_top,
        caption_height=caption_height,
        show_kicker=show_kicker,
        kicker_copy=kicker_copy,
        title_en_lines=en_lines,
        title_zh_lines=zh_lines,
        title_en_font=en_font,
        title_zh_font=zh_font,
        kicker_font=kicker_font,
        meta_font=meta_font,
        kicker_y=absolute("kicker_y"),
        title_zh_y=absolute("zh_y"),
        title_en_y=absolute("en_y"),
        title_bottom=absolute("title_bottom"),
        credit_lines=credit_lines,
        credit_y=absolute("credit_y"),
        credit_bottom=absolute("credit_bottom"),
        date_copy=date_copy,
        date_y=absolute("date_y"),
        date_bottom=absolute("date_bottom"),
    )


def measure_apod_page(
    *,
    apod: "ApodRecord",
    title_zh: str | None,
    translation_unavailable: bool,
    dimensions: tuple[int, int] = PAGE_SIZE,
) -> ApodPageMeasurement:
    """Measure the exact caption and final photo rectangle without decoding media."""

    if tuple(dimensions) != PAGE_SIZE:
        raise ValueError("APOD page requires the approved 800x480 dimensions")
    probe = Image.new("RGB", (1, 1), PAGE_COLOR)
    draw = MixedDraw(ImageDraw.Draw(probe))
    caption = _layout_caption(
        draw=draw,
        title_en=apod.title_en,
        title_zh=title_zh,
        translation_unavailable=translation_unavailable,
        copyright=apod.copyright,
        apod_date=apod.date,
        warning=apod.warning or (VIDEO_STILL_KICKER if getattr(apod, "video_still", False) else None),
    )
    photo_rect = PHOTO_RECT
    photo_size = (
        photo_rect[2] - photo_rect[0],
        photo_rect[3] - photo_rect[1],
    )
    return ApodPageMeasurement(
        caption=caption,
        photo_rect=photo_rect,
        photo_size=photo_size,
        content_signature=_measurement_signature(
            apod=apod,
            title_zh=title_zh,
            translation_unavailable=translation_unavailable,
            dimensions=dimensions,
        ),
    )


def _format_number(value, decimals=1, unavailable="—") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return unavailable
    return f"{number:.{decimals}f}"


def _scale_value(scales, letter: str) -> str:
    value = scales.get(letter.lower()) if isinstance(scales, Mapping) else None
    try:
        return f"{letter.upper()}{int(value)}"
    except (TypeError, ValueError):
        return f"{letter.upper()}—"


def _scale_level(scales, letter: str) -> int | None:
    value = scales.get(letter.lower()) if isinstance(scales, Mapping) else None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _severity_color(level) -> tuple[int, int, int]:
    """Quiet in aurora green, minor to moderate in amber, strong and beyond in red."""
    try:
        severity = int(level)
    except (TypeError, ValueError):
        return MUTED_COLOR
    if severity <= 0:
        return GREEN_COLOR
    if severity <= 2:
        return AMBER_COLOR
    return RED_COLOR


@lru_cache(maxsize=64)
def _icon(name: str, size: int) -> Image.Image | None:
    """A generated mission-control line icon, or None when the asset is missing."""
    try:
        with Image.open(ICON_DIR / f"{name}.png") as source:
            icon = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    icon = ImageOps.contain(icon, (size, size), method=Image.Resampling.LANCZOS)
    white = Image.new("RGBA", icon.size, TEXT_COLOR + (255,))
    white.putalpha(icon.getchannel("A"))
    return white


def _paste_icon(canvas, name, x, y, size) -> int:
    """Paste an icon centred in a ``size`` square at (x, y); the width it took (0 when missing)."""
    icon = _icon(name, size)
    if icon is None:
        return 0
    canvas.paste(icon, (int(x + (size - icon.width) // 2), int(y + (size - icon.height) // 2)), icon)
    return size


def _kp_zone_color(kp: float) -> tuple[int, int, int]:
    """The NOAA G scale along the dial: below 5 quiet, then G1 to G5."""
    for threshold, color in KP_ZONES:
        if kp < threshold:
            return color
    return KP_ZONES[-1][1]


def _draw_kp_gauge(canvas, draw, center, radius, kp_value, fonts) -> None:
    """A 0-9 dial of KP_SEGMENTS short bars, lit in G-scale colours up to ``kp_value`` and in the
    bar-track navy beyond it; 5 (the G1 threshold) is named over the rim, 0 and 9 just inside its
    ends. Drawn four times over and reduced, so the bars keep clean edges."""
    try:
        value = max(0.0, min(9.0, float(kp_value)))
    except (TypeError, ValueError):
        value = None
    cx, cy = center
    scale = 4
    reach = radius + 2
    layer = Image.new("RGBA", (2 * reach * scale, (reach + 2) * scale), (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    ox, oy = reach * scale, reach * scale
    outer, inner = radius * scale, (radius - KP_BAR_LENGTH) * scale
    half_gap = math.radians(180 / KP_SEGMENTS) * 0.32
    for index in range(KP_SEGMENTS):
        start = math.radians(180 + 180 * index / KP_SEGMENTS) + half_gap
        end = math.radians(180 + 180 * (index + 1) / KP_SEGMENTS) - half_gap
        middle = 9 * (index + 0.5) / KP_SEGMENTS
        color = BAR_TRACK_COLOR if value is None or middle > value else _kp_zone_color(middle)
        corners = [(ox + math.cos(angle) * reach_, oy + math.sin(angle) * reach_)
                   for angle, reach_ in ((start, inner), (start, outer), (end, outer), (end, inner))]
        pen.polygon(corners, fill=color + (255,))
    layer = layer.resize((layer.width // scale, layer.height // scale), Image.Resampling.LANCZOS)
    canvas.paste(layer, (int(cx - reach), int(cy - reach)), layer)
    font = fonts["dial"]
    angle = math.radians(180 + 20 * 5)
    bbox = _text_bbox(draw, (0, 0), "5", font)
    _draw_text(draw, (cx + math.cos(angle) * (radius + 9) - (bbox[2] + bbox[0]) / 2,
                      cy + math.sin(angle) * (radius + 9) - (bbox[3] + bbox[1]) / 2), "5", font=font, fill=LABEL_COLOR)
    inner_edge = radius - KP_BAR_LENGTH - 4
    _draw_text(draw, (cx - inner_edge, cy - bbox[3]), "0", font=font, fill=LABEL_COLOR)
    _draw_right(draw, cx + inner_edge, cy - bbox[3], "9", font=font, fill=LABEL_COLOR)


def _draw_kp_panel(canvas, draw, rect, snapshot, fonts) -> None:
    """The hero reading: the segmented dial on the left; the Kp number, the storm level in words
    and the 48-hour high beside it, all in the level's colour but the high."""

    x1, y1, x2, y2 = rect
    current = snapshot.current_kp if isinstance(snapshot.current_kp, Mapping) else {}
    scales = snapshot.current_scales if isinstance(snapshot.current_scales, Mapping) else {}
    forecast = snapshot.forecast_48h if isinstance(snapshot.forecast_48h, Mapping) else {}
    color = _severity_color(scales.get("g"))
    kp_value = _format_number(current.get("value"))
    mode = str(current.get("mode") or "unavailable")
    current_g = _scale_value(scales, "G")
    left = x1 + PANEL_INSET

    _draw_text(draw, (left, y1 + 1), "地磁指数", font=fonts["label"], fill=LABEL_COLOR)
    _draw_kp_gauge(canvas, draw, (left + KP_GAUGE_RADIUS + 2, y2 - 6), KP_GAUGE_RADIUS, current.get("value"),
                   fonts)

    right = x1 + KP_TEXT_LEFT
    number_font = fonts["kp_number"]
    _draw_text(draw, (right, y1 + 12), kp_value, font=number_font, fill=color)
    number_right = right + _text_width(draw, kp_value, number_font) + _text_bbox(draw, (0, 0), kp_value, number_font)[0]
    unit_y = y1 + 12 + number_font.getmetrics()[0] - fonts["kp_unit"].getmetrics()[0]
    _draw_text(draw, (number_right + 5, unit_y), "Kp", font=fonts["kp_unit"], fill=color)
    state_copy, state_font = _fit_kp_state(draw, current_g, mode, max_width=x2 - PANEL_INSET - right)
    _draw_text(draw, (right, y1 + 68), state_copy, font=state_font, fill=color)
    _draw_text(draw, (right, y1 + 92), _kp_peak_copy(forecast), font=fonts["kp_peak"], fill=TEXT_COLOR)


def _kp_peak_copy(forecast) -> str:
    """"48 小时最高 Kp 6.3，G2"; the storm level only when one is forecast."""
    peak_g = str(forecast.get("noaa_scale") or "G—")
    copy = f"48 小时最高 Kp {_format_number(forecast.get('max_kp'))}"
    return copy if peak_g in ("G—", "G0") else f"{copy}，{peak_g}"


def _fit_kp_state(draw, current_g, mode, *, max_width):
    """The current G level in words and how Kp was read ("G2 中等磁暴（估算）") on one line
    beside the number, stepping 13 to 10 px."""
    level = G_LEVELS.get(str(current_g), "等级未知")
    reading = KP_MODES.get(str(mode), str(mode))
    for text in (f"{current_g} {level}（{reading}）", f"{current_g} {level}"):
        for size in (13, 12, 11, 10):
            font = _font(size, bold=True)
            if _text_width(draw, text, font) <= max_width:
                return text, font
    raise ApodPageLayoutError("Kp state does not fit beside the number at 10px")


def _draw_grs_panel(draw, snapshot, fonts, *, chances=False) -> None:
    """G, R and S as plain dots in their NOAA level's colour, the level and its name beside each;
    with ``chances`` the R and S columns also carry the day's chance of an R1-R2 blackout and an
    S1+ storm at their right."""
    x1, y1, x2, y2 = GRS_RECT
    scales = snapshot.current_scales if isinstance(snapshot.current_scales, Mapping) else {}
    probabilities = snapshot.probabilities if isinstance(snapshot.probabilities, Mapping) else {}
    odds = {"R": ("R1-2", probabilities.get("r1_r2")), "S": ("S1+", probabilities.get("s1_plus"))}
    left, right = x1 + PANEL_INSET, x2 - PANEL_INSET
    cell_width = (right - left + GRS_GAP) / 3
    for index, (letter, name) in enumerate((("G", "地磁暴"), ("R", "无线电中断"), ("S", "太阳辐射"))):
        cell_left = round(left + index * cell_width)
        cell_right = round(left + (index + 1) * cell_width) - GRS_GAP
        lamp = _severity_color(_scale_level(scales, letter))
        middle = y1 + 11
        draw.ellipse((cell_left, middle - 5, cell_left + 10, middle + 5), fill=lamp)
        level = _scale_value(scales, letter)
        _draw_text(draw, (cell_left + 16, y1 + 2), level, font=fonts["grs_value"], fill=TEXT_COLOR)
        _draw_text(draw, (cell_left + 16, y1 + 21), name, font=fonts["grs_name"], fill=LABEL_COLOR)
        if chances and letter in odds:
            label, value = odds[letter]
            percent = "—" if value is None else f"{value}%"
            _draw_right(draw, cell_right, y1 + 2, percent, font=fonts["probability_value"],
                        fill=_probability_color(value))
            # The chance's name sits under it, when that leaves the level's name clear.
            name_right = cell_left + 16 + _text_width(draw, name, fonts["grs_name"])
            if cell_right - _text_width(draw, label, fonts["probability_label"]) >= name_right + 4:
                _draw_right(draw, cell_right, y1 + 21, label, font=fonts["probability_label"], fill=LABEL_COLOR)


def _draw_reading(draw, origin, max_right, label, value, fonts, *, unit="", color=TEXT_COLOR) -> None:
    """One open reading: a small label above a DM Mono value and its unit, the value stepping down
    from READING_SIZE until value and unit fit before ``max_right``."""
    x, y = origin
    _draw_text(draw, (x, y), label, font=fonts["label"], fill=LABEL_COLOR)
    unit_font = fonts["unit"]
    unit_width = _text_width(draw, unit, unit_font) + 4 if unit and value != "—" else 0
    selected = fonts["reading"]
    for size in range(READING_SIZE, 11, -1):
        selected = space_font("mono", size)
        if x + _text_width(draw, value, selected) + unit_width <= max_right:
            break
    _draw_text(draw, (x, y + 16), value, font=selected, fill=color)
    if unit_width:
        unit_x = x + _text_width(draw, value, selected) + _text_bbox(draw, (0, 0), value, selected)[0] + 4
        unit_y = y + 16 + selected.getmetrics()[0] - unit_font.getmetrics()[0]
        _draw_text(draw, (unit_x, unit_y), unit, font=unit_font, fill=LABEL_COLOR)


def _draw_metric_cell(draw, rect, label, value, fonts, unit="") -> None:
    x1, y1, x2, _y2 = rect
    _draw_reading(draw, (x1 + PANEL_INSET, y1), x2 - PANEL_INSET, label, value, fonts, unit=unit)


def _draw_metrics_panel(canvas, draw, snapshot, fonts) -> None:
    speed = snapshot.solar_wind if isinstance(snapshot.solar_wind, Mapping) else {}
    magnetic = snapshot.magnetic_field if isinstance(snapshot.magnetic_field, Mapping) else {}
    forecast = snapshot.forecast_48h if isinstance(snapshot.forecast_48h, Mapping) else {}
    wind_value = _format_number(speed.get("speed_km_s"), decimals=0)
    bz_number = _format_number(magnetic.get("bz_nt"), decimals=2)
    bz_value = bz_number if bz_number == "—" else f"{str(magnetic.get('direction') or 'unknown')} {bz_number}"
    bt_value = _format_number(magnetic.get("bt_nt"), decimals=1)
    forecast_value = (
        f"{str(forecast.get('noaa_scale') or 'G—')}{TEXT_SEPARATOR}Kp "
        f"{_format_number(forecast.get('max_kp'))}"
    )

    x1, y1, x2, y2 = METRICS_RECT
    mid_x = (x1 + x2) // 2
    mid_y = (y1 + y2) // 2
    gap = METRIC_GAP // 2
    xray = snapshot.xray if isinstance(getattr(snapshot, "xray", None), Mapping) else {}
    chances = (snapshot.flare_probabilities
               if isinstance(getattr(snapshot, "flare_probabilities", None), Mapping) else {})
    if xray or chances:
        # The flares take the whole lower row; Bt and Bz share a reading (the Kp hero already
        # carries the 48-hour high the forecast cell repeated).
        field_value = "—"
        if bt_value != "—" or bz_number != "—":
            field_value = f"{bt_value} / {bz_number}"
        cells = (
            ((x1, y1, mid_x - gap, mid_y - gap), "太阳风", wind_value, "km/s"),
            ((mid_x + gap, y1, x2, mid_y - gap), "磁场 Bt / Bz", field_value, "nT"),
        )
        for rect, label, value, unit in cells:
            _draw_metric_cell(draw, rect, label, value, fonts, unit=unit)
        _draw_flare_cell(canvas, draw, (x1, mid_y + gap, x2, y2), xray, chances, fonts)
        return
    cells = (
        ((x1, y1, mid_x - gap, mid_y - gap), "太阳风", wind_value, "km/s"),
        ((mid_x + gap, y1, x2, mid_y - gap), "磁场 Bz", bz_value, "nT"),
        ((x1, mid_y + gap, mid_x - gap, y2), "磁场强度 Bt", bt_value, "nT"),
        ((mid_x + gap, mid_y + gap, x2, y2), "48 小时预报", forecast_value, ""),
    )
    for rect, label, value, unit in cells:
        _draw_metric_cell(draw, rect, label, value, fonts, unit=unit)


def _flare_colour(flare_class) -> tuple[int, int, int]:
    """A class by its letter: A/B quiet, C amber, M orange (R1-R2), X red (R3+)."""
    letter = str(flare_class or "")[:1].upper()
    return {"C": AMBER_COLOR, "M": ORANGE_COLOR, "X": RED_COLOR}.get(letter, GREEN_COLOR if letter else MUTED_COLOR)


def _draw_flare_cell(canvas, draw, rect, xray, chances, fonts) -> None:
    """The X-ray class now, with the strongest flare of 24 hours after it; SWPC's C/M/X chances
    for the next three days on the right, each class a trio of day bars with day one's figure."""
    x1, y1, x2, y2 = rect
    left = x1 + PANEL_INSET
    _draw_text(draw, (left, y1), "X 射线耀斑", font=fonts["label"], fill=LABEL_COLOR)
    current = str(xray.get("current_class") or "—")
    value_font = fonts["reading"]
    _draw_text(draw, (left, y1 + 16), current, font=value_font, fill=_flare_colour(xray.get("current_class")))
    peak = xray.get("peak_class")
    if peak:
        baseline = y1 + 16 + value_font.getmetrics()[0]
        peak_x = left + _text_width(draw, current, value_font) + 10
        peak_label = "24 小时最高"
        _draw_text(draw, (peak_x, baseline - fonts["label_small"].getmetrics()[0]), peak_label,
                   font=fonts["label_small"], fill=LABEL_COLOR)
        peak_x += _text_width(draw, peak_label, fonts["label_small"]) + 4
        _draw_text(draw, (peak_x, baseline - fonts["probability_value"].getmetrics()[0]), str(peak),
                   font=fonts["probability_value"], fill=_flare_colour(peak))

    rows = [(letter, chances.get(letter.lower())) for letter in ("C", "M", "X")]
    rows = [(letter, values) for letter, values in rows if isinstance(values, (list, tuple)) and len(values) == 3]
    if not rows:
        return
    group_width, right = 56, x2 - PANEL_INSET
    groups_left = right - group_width * len(rows) + 8
    _draw_text(draw, (groups_left, y1), "1-3 天耀斑概率", font=fonts["label"], fill=LABEL_COLOR)
    bar_bottom, bar_height = y2 - 4, 15
    for index, (letter, values) in enumerate(rows):
        gx = groups_left + index * group_width
        _draw_text(draw, (gx, bar_bottom - 14), letter, font=fonts["probability_value"], fill=TEXT_COLOR)
        bars_x = gx + 12
        for day, value in enumerate(values):
            bx = bars_x + day * 5
            draw.rectangle((bx, bar_bottom - bar_height, bx + 2, bar_bottom), fill=BAR_TRACK_COLOR)
            share = max(0.0, min(1.0, float(value) / 100))
            if share > 0:
                top = bar_bottom - max(2, round(bar_height * share))
                draw.rectangle((bx, top, bx + 2, bar_bottom), fill=_probability_color(value))
        _draw_text(draw, (bars_x + 17, bar_bottom - 12), f"{int(values[0])}%", font=fonts["header_meta"],
                   fill=TEXT_COLOR)


def _probability_color(value) -> tuple[int, int, int]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return MUTED_COLOR
    if number < 25:
        return GREEN_COLOR
    if number < 60:
        return AMBER_COLOR
    return RED_COLOR


def _draw_probabilities_panel(canvas, draw, snapshot, fonts) -> None:
    """Today's three storm chances, each its figure over a hairline bar the width of its chance."""
    x1, y1, x2, y2 = PROBABILITIES_RECT
    probabilities = snapshot.probabilities if isinstance(snapshot.probabilities, Mapping) else {}
    values = (
        ("R1–R2", probabilities.get("r1_r2")),
        ("R3–R5", probabilities.get("r3_r5")),
        ("S1+", probabilities.get("s1_plus")),
    )
    left, right = x1 + PANEL_INSET, x2 - PANEL_INSET
    _draw_text(draw, (left, y1), "今日风暴概率", font=fonts["label"], fill=LABEL_COLOR)
    cell_width = (right - left + GRS_GAP) / 3
    for index, (label, value) in enumerate(values):
        cell_left = round(left + index * cell_width)
        cell_right = round(left + (index + 1) * cell_width) - GRS_GAP
        _draw_text(draw, (cell_left, y1 + 17), label, font=fonts["probability_label"], fill=LABEL_COLOR)
        probability = "—" if value is None else f"{value}%"
        _draw_right(draw, cell_right, y1 + 15, probability, font=fonts["probability_value"],
                    fill=_probability_color(value))
        bar_y = y2 - 6
        draw.rectangle((cell_left, bar_y, cell_right, bar_y + 1), fill=BAR_TRACK_COLOR)
        try:
            share = max(0.0, min(1.0, float(value) / 100))
        except (TypeError, ValueError):
            share = 0.0
        if share > 0:
            draw.rectangle((cell_left, bar_y - 1, cell_left + max(3, round((cell_right - cell_left) * share)),
                            bar_y + 2), fill=_probability_color(value))


def planets_copy(sky) -> str:
    """Tonight's planets, brightest first: "今夜可见 木星 东南 02时 · 土星 南 21时"; "" without any."""
    planets = ((sky or {}).get("planets") or {}).get("planets") or []
    parts = []
    for planet in planets[:3]:
        try:
            hour = datetime.fromisoformat(planet["when"]).astimezone(PACIFIC).hour
        except (KeyError, TypeError, ValueError):
            continue
        parts.append(f"{planet['name']} {planet['direction']} {hour:02d}时")
    return "今夜可见 " + " · ".join(parts) if parts else ""


def _alert_copy(snapshot, sky=None) -> tuple[str, tuple[int, int, int]]:
    alert = snapshot.alert if isinstance(snapshot.alert, Mapping) else None
    if alert:
        kind = str(alert.get("kind") or "ALERT")
        severity = str(alert.get("severity") or "").strip()
        headline = str(alert.get("headline") or "").strip()
        parts = [f"NOAA {kind}"]
        if severity:
            parts.append(severity)
        if headline:
            parts.append(headline)
        return TEXT_SEPARATOR.join(parts), AMBER_COLOR

    event = snapshot.donki_event if isinstance(snapshot.donki_event, Mapping) else None
    if event:
        kind = str(event.get("kind") or "EVENT")
        detail = str(
            event.get("class_type")
            or event.get("arrival_time_utc")
            or event.get("event_id")
            or "active"
        )
        return f"NASA DONKI{TEXT_SEPARATOR}{kind} {detail}", CYAN_COLOR
    if snapshot.alert_state == "confirmed_empty":
        # No alert to raise: the row says so briefly and tells what is up in the sky tonight.
        planets = planets_copy(sky)
        if planets:
            return f"NOAA 无告警{TEXT_SEPARATOR}{planets}", GREEN_COLOR
        return f"NOAA ALERTS{TEXT_SEPARATOR}no active items", GREEN_COLOR
    return f"NOAA ALERTS{TEXT_SEPARATOR}temporarily unavailable", TILE_OUTLINE


def _fit_alert_copy(draw, text, *, max_width, required_active=False):
    """The complete alert in at most two lines, stepping the type down from 12 to 10 px."""
    for size in (12, 11, 10):
        font = _font(size, bold=True)
        lines = _wrap_complete(draw, text, font, max_width)
        if len(lines) <= 2:
            return lines, font
    label = "active alert" if required_active else "alert copy"
    raise ApodPageLayoutError(f"complete {label} does not fit the fixed cell at 10px")


def _tonight_planets(snapshot, sky):
    """Tonight's planets for the alert row when there is nothing to raise; [] otherwise."""
    if isinstance(snapshot.alert, Mapping) or isinstance(snapshot.donki_event, Mapping):
        return []
    if snapshot.alert_state != "confirmed_empty":
        return []
    return [planet for planet in ((sky or {}).get("planets") or {}).get("planets") or []
            if planet.get("name") in PLANET_ICONS][:3]


def _alert_rule(draw, color) -> int:
    """The row's colour as a short vertical bar at its left, like the caption's on the photo; the
    x where the row's icon goes."""
    x1, y1, _x2, y2 = ALERT_RECT
    draw.rectangle((x1 + PANEL_INSET, y1 + 3, x1 + PANEL_INSET + ALERT_RULE_WIDTH - 1, y2 - 3), fill=color)
    return x1 + PANEL_INSET + ALERT_RULE_WIDTH + 9


def _draw_planets_row(canvas, draw, planets, fonts) -> None:
    """No alert: a short all-clear, then tonight's planets, each led by its icon, with where and
    when it stands highest."""
    x1, y1, x2, y2 = ALERT_RECT
    x = _alert_rule(draw, GREEN_COLOR)
    _draw_text(draw, (x, y1 + 3), f"NOAA 无告警{TEXT_SEPARATOR}今夜可见", font=_font(11, bold=True), fill=TEXT_COLOR)
    middle = y1 + 30
    font = _font(11, bold=True)
    for planet in planets:
        try:
            hour = datetime.fromisoformat(planet["when"]).astimezone(PACIFIC).hour
        except (KeyError, TypeError, ValueError):
            continue
        label = f"{planet['name']} {planet['direction']} {hour:02d}时"
        width = PLANET_ICON_SIZE + 3 + _text_width(draw, label, font)
        if x + width > x2 - PANEL_INSET:
            break
        _paste_sky_icon(canvas, PLANET_ICONS[planet["name"]], (x + PLANET_ICON_SIZE / 2, middle), PLANET_ICON_SIZE)
        _draw_text(draw, (x + PLANET_ICON_SIZE + 3, middle - 8), label, font=font, fill=TEXT_COLOR)
        x += width + 10


def _draw_alert_panel(canvas, draw, snapshot, fonts, sky=None) -> None:
    planets = _tonight_planets(snapshot, sky)
    if planets:
        _draw_planets_row(canvas, draw, planets, fonts)
        return
    x1, y1, x2, y2 = ALERT_RECT
    text, color = _alert_copy(snapshot, sky)
    icon_x = _alert_rule(draw, color)
    icon = "alert" if isinstance(snapshot.alert, Mapping) or isinstance(snapshot.donki_event, Mapping) else "source"
    taken = _paste_icon(canvas, icon, icon_x, y1 + (y2 - y1 - 20) // 2, 20)
    text_x = icon_x + (taken + 8 if taken else 0)
    lines, font = _fit_alert_copy(
        draw, text, max_width=x2 - PANEL_INSET - text_x, required_active=isinstance(snapshot.alert, Mapping),
    )
    line_height = _line_height(draw, font)
    y = y1 + max(3, ((y2 - y1) - line_height * len(lines)) // 2)
    for line in lines:
        _draw_text(draw, (text_x, y), line, font=font, fill=TEXT_COLOR)
        y += line_height


@lru_cache(maxsize=2)
def _jpl_logo(height: int) -> Image.Image | None:
    return load_source_logo(JPL_LOGO_PATH, height)


@lru_cache(maxsize=24)
def _sky_icon(name: str, size: int) -> Image.Image | None:
    try:
        with Image.open(SKY_ICON_DIR / f"{name}.png") as source:
            return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None


def _paste_sky_icon(canvas, name, centre, size) -> bool:
    icon = _sky_icon(name, size)
    if icon is None:
        return False
    canvas.paste(icon, (round(centre[0] - size / 2), round(centre[1] - size / 2)), icon)
    return True


def _neo_x(left, right, lunar):
    """Where ``lunar`` lunar distances sit on the strip: a square-root scale out to NEO_SCALE_LD."""
    return left + (right - left) * math.sqrt(max(0.0, min(NEO_SCALE_LD, lunar)) / NEO_SCALE_LD)


def neo_copy(neo) -> tuple[str, str]:
    """The nearest NEO's two lines: its name, then "0.32 LD  10/28 18:26  ~7 m"."""
    parts = [f"{neo['lunar']:.2f} LD"]
    try:
        when = datetime.fromisoformat(neo["when"]).astimezone(PACIFIC)
        parts.append(f"{when.month}/{when.day} {when:%H:%M}")
    except (KeyError, TypeError, ValueError):
        pass
    if neo.get("size_m"):
        size = neo["size_m"]
        parts.append(f"~{size:.0f} m" if size < 1000 else f"~{size / 1000:.1f} km")
    return str(neo.get("name") or "—"), "  ".join(parts)


def _draw_neo_panel(canvas, draw, neos, fonts) -> None:
    """The NEOs passing within ten lunar distances in 30 days on a strip from Earth, the Moon at
    one lunar distance, the nearest in red and named at the right; JPL's logo names the source."""
    x1, y1, x2, y2 = NEO_RECT
    objects = neos.get("objects") or []
    left = x1 + PANEL_INSET
    _draw_text(draw, (left, y1), "近地天体 30 天", font=fonts["label"], fill=LABEL_COLOR)
    logo = _jpl_logo(10)
    right = x2 - PANEL_INSET
    if logo is not None:
        canvas.paste(logo, (right - logo.width, y1 + 3), logo)
        right -= logo.width + 6
    _draw_right(draw, right, y1 + 1, f"{neos.get('count', len(objects))} 颗 < 10 LD", font=_font(10, bold=True),
                fill=TEXT_COLOR)
    strip_left, strip_right, strip_y = left + 6, left + 150, y2 - 12
    draw.line((strip_left, strip_y, strip_right, strip_y), fill=TILE_OUTLINE, width=1)
    for lunar in (1, 5, 10):
        x = _neo_x(strip_left, strip_right, lunar)
        draw.line((x, strip_y - 3, x, strip_y + 3), fill=TILE_OUTLINE, width=1)
    if not _paste_sky_icon(canvas, "earth", (strip_left, strip_y), NEO_ICON_SIZES["earth"]):
        draw.ellipse((strip_left - 5, strip_y - 5, strip_left + 5, strip_y + 5), fill=EARTH_COLOR)
    # The farther NEOs first, then the Moon over them, then the nearest one (glowing, larger) on top.
    for neo in reversed(objects):
        nearest = neo is objects[0]
        if nearest:
            moon = _neo_x(strip_left, strip_right, 1)
            if not _paste_sky_icon(canvas, "moon", (moon, strip_y), NEO_ICON_SIZES["moon"]):
                draw.ellipse((moon - 3, strip_y - 3, moon + 3, strip_y + 3), fill=MOON_COLOR)
        x = _neo_x(strip_left, strip_right, neo["lunar"])
        name = "asteroid_near" if nearest else "asteroid"
        if not _paste_sky_icon(canvas, name, (x, strip_y), NEO_ICON_SIZES[name]):
            radius = 3.5 if nearest else 2.2
            draw.ellipse((x - radius, strip_y - radius, x + radius, strip_y + radius),
                         fill=RED_COLOR if nearest else AMBER_COLOR)
    if not objects:
        moon = _neo_x(strip_left, strip_right, 1)
        _paste_sky_icon(canvas, "moon", (moon, strip_y), NEO_ICON_SIZES["moon"])
    text_x = strip_right + 14
    if objects:
        name, detail = neo_copy(objects[0])
        _draw_text(draw, (text_x, y1 + 15), name, font=fonts["probability_value"], fill=RED_COLOR)
        _draw_text(draw, (text_x, y1 + 31), detail, font=_font(10), fill=TEXT_COLOR)
    else:
        _draw_text(draw, (text_x, y1 + 22), "30 天内无近距掠过", font=_font(10, bold=True), fill=TEXT_COLOR)


def _as_utc(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_stamp(value, *, include_date=False) -> str:
    converted = _as_utc(value)
    if converted is None:
        return "unavailable"
    return converted.strftime("%Y-%m-%d %H:%MZ" if include_date else "%H:%MZ")


def _draw_source_panel(canvas, draw, snapshot, fonts) -> None:
    observed = _utc_stamp(snapshot.oldest_core_observed_at_utc)
    cached = _utc_stamp(snapshot.fetched_at_utc)
    text = f"NOAA SWPC{TEXT_SEPARATOR}观测 {observed}{TEXT_SEPARATOR}缓存 {cached}"
    font = fonts["source"]
    x1 = SOURCE_RECT[0] + PANEL_INSET
    taken = _paste_icon(canvas, "source", x1, SOURCE_RECT[1] + 2, 16)
    text_x = x1 + (taken + 6 if taken else 0)
    if _text_width(draw, text, font) > SOURCE_RECT[2] - PANEL_INSET - text_x:
        raise ApodPageLayoutError("complete NOAA source timestamps do not fit")
    _draw_text(draw, (text_x, SOURCE_RECT[1] + 4), text, font=font, fill=MUTED_COLOR)


def _fitting_insignia(draw, layout, right) -> Image.Image | None:
    """The tallest insignia (NASA_INSIGNIA_HEIGHT down to NASA_INSIGNIA_MIN_HEIGHT) whose box clears
    every caption line beside it, or None."""
    x1 = RIGHT_TEXT_RECT[0]
    advance = _line_height(draw, layout.meta_font)
    rows = [(layout.date_y, layout.date_copy)]
    rows += [(layout.credit_y + index * advance, line) for index, line in enumerate(layout.credit_lines)]
    for height in range(NASA_INSIGNIA_HEIGHT, NASA_INSIGNIA_MIN_HEIGHT - 1, -6):
        insignia = _nasa_insignia(height)
        if insignia is None:
            return None
        left, top = right - insignia.width - NASA_INSIGNIA_GAP, layout.date_bottom - insignia.height
        if all(y + advance <= top or x1 + _text_width(draw, line, layout.meta_font) <= left for y, line in rows):
            return insignia
    return None


@lru_cache(maxsize=8)
def _nasa_insignia(height: int) -> Image.Image | None:
    """NASA's insignia (the plugin's nasa_logo.png) ``height`` px tall, or None."""
    try:
        with Image.open(NASA_INSIGNIA_PATH) as source:
            insignia = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    bbox = insignia.getchannel("A").getbbox()
    insignia = insignia.crop(bbox) if bbox else insignia
    width = max(1, round(insignia.width * height / insignia.height))
    return insignia.resize((width, int(height)), Image.Resampling.LANCZOS)


def _load_wordmark() -> Image.Image | None:
    try:
        with Image.open(WORDMARK_PATH) as source:
            wordmark = source.convert("RGBA")
    except (OSError, ValueError):
        return None

    bbox = wordmark.getchannel("A").getbbox()
    return wordmark.crop(bbox) if bbox else None


def _wordmark_on_dark(wordmark: Image.Image) -> Image.Image:
    """The wordmark's dark lettering turned light; its blue swoosh and orange star keep their colour."""
    red, green, blue, alpha = wordmark.split()
    luma = wordmark.convert("L")
    chroma = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(red, green), blue),
                                 ImageChops.darker(ImageChops.darker(red, green), blue))
    lettering = ImageChops.multiply(luma.point(lambda value: 255 if value < 120 else 0),
                                    chroma.point(lambda value: 255 if value < 90 else 0))
    light = Image.new("RGBA", wordmark.size, (*TEXT_COLOR, 255))
    recoloured = Image.composite(light, wordmark, lettering)
    recoloured.putalpha(alpha)
    return recoloured


def _draw_wordmark(canvas: Image.Image) -> bool:
    source = _load_wordmark()
    if source is None:
        return False

    left, top, right, bottom = WORDMARK_RECT
    art = ImageOps.contain(
        _wordmark_on_dark(source),
        (right - left, bottom - top),
        method=Image.Resampling.LANCZOS,
    )
    x = left
    y = top + ((bottom - top - art.height) // 2)
    canvas.paste(art.convert("RGB"), (x, y), art.getchannel("A"))
    return True


def _draw_orbits(canvas) -> None:
    """Faint orbital arcs and a few stars behind the header, as in a mission display, kept inside it."""
    band = Image.new("RGB", (PAGE_SIZE[0], HEADER_RECT[3] - 1), HEADER_COLOR)
    band_draw = MixedDraw(ImageDraw.Draw(band))
    for box, start, end in ORBIT_ARCS:
        band_draw.arc(box, start=start, end=end, fill=ORBIT_COLOR, width=1)
    for x, y in HEADER_STARS:
        band_draw.point((x, y), fill=STAR_COLOR)
    canvas.paste(band, (0, 0))


def _draw_header(canvas, draw, rendered_at_utc, fonts) -> None:
    _draw_orbits(canvas)
    if not _draw_wordmark(canvas):
        _draw_text(
            draw,
            (20, 10),
            WORDMARK_FALLBACK_TEXT,
            font=fonts["brand"],
            fill=TEXT_COLOR,
        )
    _draw_right(
        draw,
        780,
        24,
        _utc_stamp(rendered_at_utc, include_date=True),
        font=fonts["header_meta"],
        fill=TEXT_COLOR,
    )
    draw.line((0, HEADER_RECT[3] - 1, 799, HEADER_RECT[3] - 1), fill=DIVIDER_COLOR)


def _place_photo(canvas, photo, caption_top) -> None:
    """The photo full-bleed on the right, its left edge fading into the panel and its foot darkened for the caption."""
    left, top = PHOTO_LEFT, PHOTO_TOP
    width, height = photo.size
    # Rotated, the gradient runs transparent at the panel to opaque toward the photo.
    fade = Image.linear_gradient("L").rotate(90, expand=True).resize((PHOTO_FADE, height))
    mask = Image.new("L", (width, height), 255)
    mask.paste(fade, (0, 0))
    canvas.paste(photo, (left, top), mask)
    shade_top = max(0, caption_top - top - CAPTION_SHADE_LEAD)
    shade_height = height - shade_top
    if shade_height > 0:
        ramp = Image.linear_gradient("L").resize((width, shade_height)).point(
            lambda value: round(value * CAPTION_SHADE_ALPHA / 255)
        )
        region = canvas.crop((left, top + shade_top, left + width, top + height))
        dark = Image.new("RGB", region.size, PAGE_COLOR)
        canvas.paste(Image.composite(dark, region, ramp), (left, top + shade_top))


def _draw_panel_scrim(canvas) -> None:
    """The page ground over the photo's faded left edge behind the panel: solid to SCRIM_SOLID,
    clearing linearly by SCRIM_CLEAR."""
    left, top, right, bottom = PHOTO_LEFT, PHOTO_TOP, SCRIM_CLEAR, PAGE_SIZE[1]
    ramp = Image.new("L", (right - left, 1))
    for offset in range(right - left):
        x = left + offset
        share = 1.0 if x < SCRIM_SOLID else (SCRIM_CLEAR - x) / (SCRIM_CLEAR - SCRIM_SOLID)
        ramp.putpixel((offset, 0), round(255 * max(0.0, min(1.0, share))))
    mask = ramp.resize((right - left, bottom - top))
    region = canvas.crop((left, top, right, bottom))
    canvas.paste(Image.composite(Image.new("RGB", region.size, PAGE_COLOR), region, mask), (left, top))


def _draw_caption(canvas, draw, layout) -> None:
    x1 = RIGHT_TEXT_RECT[0]
    x2 = RIGHT_TEXT_RECT[2]
    accent_bottom = layout.date_bottom
    accent_top = layout.kicker_y if layout.show_kicker and layout.kicker_y is not None else layout.title_zh_y
    accent_top = layout.title_en_y if accent_top is None else accent_top
    draw.rectangle((x1 - 10, accent_top, x1 - 8, accent_bottom), fill=ORANGE_COLOR)
    if layout.show_kicker and layout.kicker_y is not None:
        taken = _paste_icon(canvas, "telescope", x1, layout.kicker_y - 2, 14)
        _draw_text(
            draw,
            (x1 + (taken + 4 if taken else 0), layout.kicker_y),
            layout.kicker_copy,
            font=layout.kicker_font,
            fill=ORANGE_COLOR,
        )

    if (
        layout.title_zh_lines
        and layout.title_zh_font is not None
        and layout.title_zh_y is not None
    ):
        y = layout.title_zh_y
        zh_advance = _line_height(draw, layout.title_zh_font)
        for line in layout.title_zh_lines:
            _draw_text(draw, (x1, y), line, font=layout.title_zh_font, fill=TEXT_COLOR)
            y += zh_advance
    y = layout.title_en_y
    en_advance = _line_height(draw, layout.title_en_font)
    for line in layout.title_en_lines:
        _draw_text(draw, (x1, y), line, font=layout.title_en_font, fill=SUBTITLE_COLOR)
        y += en_advance

    y = layout.credit_y
    meta_advance = _line_height(draw, layout.meta_font)
    for line in layout.credit_lines:
        _draw_text(draw, (x1, y), line, font=layout.meta_font, fill=MUTED_COLOR)
        y += meta_advance
    _draw_text(
        draw,
        (x1, layout.date_y),
        layout.date_copy,
        font=layout.meta_font,
        fill=MUTED_COLOR,
    )
    # NASA's insignia closes the caption at its lower right, its foot on the date line, as tall as
    # the caption lines beside it leave room for.
    insignia = _fitting_insignia(draw, layout, x2)
    if insignia is not None:
        canvas.paste(insignia, (x2 - insignia.width, layout.date_bottom - insignia.height), insignia)
    else:
        _draw_right(
            draw,
            x2,
            layout.date_y,
            "FULL-BLEED DAILY",
            font=layout.meta_font,
            fill=LABEL_COLOR,
        )


def _mix(colour, other, share):
    return tuple(round(a + (b - a) * share) for a, b in zip(colour, other))


def render_apod_page(
    *,
    apod: "ApodRecord",
    title_zh: str | None,
    translation_unavailable: bool,
    weather: SpaceWeatherSnapshot,
    source_image: Image.Image,
    rendered_at_utc: datetime,
    dimensions: tuple[int, int] = PAGE_SIZE,
    measurement: ApodPageMeasurement | None = None,
    sky: Mapping | None = None,
) -> Image.Image:
    """Render the mission-control page: open instrument readings on the left, the photo full-bleed on the right.
    ``sky`` (JPL's NEOs and tonight's planets) takes the storm-chance row when it has NEOs."""

    if tuple(dimensions) != PAGE_SIZE:
        raise ValueError("APOD page requires the approved 800x480 dimensions")

    measured = measurement or measure_apod_page(
        apod=apod,
        title_zh=title_zh,
        translation_unavailable=translation_unavailable,
        dimensions=dimensions,
    )
    if measured.content_signature != _measurement_signature(
        apod=apod,
        title_zh=title_zh,
        translation_unavailable=translation_unavailable,
        dimensions=dimensions,
    ):
        raise ValueError("APOD page measurement does not match caption content")
    layout = measured.caption
    if measured.photo_rect != PHOTO_RECT:
        raise ValueError("APOD page measurement does not match the photo rectangle")

    canvas = Image.new("RGB", PAGE_SIZE, PAGE_COLOR)
    draw = MixedDraw(ImageDraw.Draw(canvas))
    fonts = _load_fonts()

    _place_photo(canvas, fit_photo(source_image, measured.photo_size), layout.caption_top)
    _draw_panel_scrim(canvas)
    _draw_header(canvas, draw, rendered_at_utc, fonts)
    _draw_kp_panel(canvas, draw, KP_RECT, weather, fonts)
    neos = sky.get("neos") if isinstance(sky, Mapping) else None
    _draw_grs_panel(draw, weather, fonts, chances=bool(neos))
    _draw_metrics_panel(canvas, draw, weather, fonts)
    if neos:
        _draw_neo_panel(canvas, draw, neos, fonts)
    else:
        _draw_probabilities_panel(canvas, draw, weather, fonts)
    _draw_alert_panel(canvas, draw, weather, fonts, sky)
    _draw_source_panel(canvas, draw, weather, fonts)
    _draw_caption(canvas, draw, layout)
    return canvas.convert("RGB")
