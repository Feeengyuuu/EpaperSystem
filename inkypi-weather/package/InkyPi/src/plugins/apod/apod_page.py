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
from utils.app_utils import get_base_ui_font

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
LEFT_RECT = (0, 65, 368, 480)
KP_RECT = (16, 74, 352, 190)
GRS_RECT = (16, 198, 352, 236)
METRICS_RECT = (16, 244, 352, 330)
METRIC_GAP = 8
PROBABILITIES_RECT = (16, 338, 352, 384)
ALERT_RECT = (16, 392, 352, 434)
SOURCE_RECT = (16, 448, 352, 472)
RIGHT_X = 368
ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"

PAGE_SIZE = (800, 480)
CAPTION_MIN_TOP = 300
CAPTION_MAX_TOP = 364
PHOTO_TOP = 65
# The photo runs under the panel's right edge and fades into it over PHOTO_FADE pixels.
PHOTO_LEFT = 340
PHOTO_FADE = 64
PHOTO_RECT = (PHOTO_LEFT, PHOTO_TOP, PAGE_SIZE[0], PAGE_SIZE[1])
# The caption sits on the photo, which darkens toward the foot from just above it.
CAPTION_SHADE_LEAD = 36
CAPTION_SHADE_ALPHA = 225
RIGHT_INSET = 12
RIGHT_TEXT_RECT = (RIGHT_X + RIGHT_INSET, 0, 800 - RIGHT_INSET, 480)
KICKER_ICON_ROOM = 18

PAGE_COLOR = (7, 16, 31)
HEADER_COLOR = (9, 21, 40)
TILE_COLOR = (12, 27, 49)
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
        "header_meta": _font(10, bold=True),
        "kp_label": _font(10, bold=True),
        "kp_number": _font(40, bold=True),
        "kp_peak": _font(10, bold=True),
        "dial": _font(10, bold=True),
        "grs_value": _font(13, bold=True),
        "grs_name": _font(10, bold=True),
        "metric_label": _font(10, bold=True),
        "metric_value": _font(16, bold=True),
        "probability_label": _font(10, bold=True),
        "probability_value": _font(13, bold=True),
        "alert": _font(12, bold=True),
        "source": _font(10, bold=True),
        "caption_kicker": _font(10, bold=True),
        "caption_meta": _font(10, bold=True),
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
    """Greedily wrap every source character without truncation or ellipsis."""

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
        if current and _text_width(draw, candidate, font) > max_width:
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
        font = _font(size, bold=bold)
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
    draw = ImageDraw.Draw(probe)
    caption = _layout_caption(
        draw=draw,
        title_en=apod.title_en,
        title_zh=title_zh,
        translation_unavailable=translation_unavailable,
        copyright=apod.copyright,
        apod_date=apod.date,
        warning=apod.warning,
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


def _tile(draw, rect, *, outline=TILE_OUTLINE, fill=TILE_COLOR, radius=8) -> None:
    x1, y1, x2, y2 = rect
    draw.rounded_rectangle((x1, y1, x2 - 1, y2 - 1), radius=radius, fill=fill, outline=outline, width=1)


def _kp_zone_color(kp: float) -> tuple[int, int, int]:
    """The NOAA G scale along the dial: below 5 quiet, then G1 to G5."""
    for threshold, color in KP_ZONES:
        if kp < threshold:
            return color
    return KP_ZONES[-1][1]


def _draw_kp_gauge(draw, center, radius, kp_value, fonts) -> None:
    """A semicircular 0-9 dial in G-scale colours with a needle at ``kp_value``."""
    cx, cy = center
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    steps = 54
    for step in range(steps):
        start = 180 + 180 * step / steps
        end = 180 + 180 * (step + 1) / steps
        draw.arc(box, start=start, end=end + 0.6, fill=_kp_zone_color(9 * (step + 0.5) / steps), width=7)
    for tick in range(10):
        angle = math.radians(180 + 20 * tick)
        cos, sin = math.cos(angle), math.sin(angle)
        draw.line((cx + cos * (radius - 11), cy + sin * (radius - 11), cx + cos * (radius - 6), cy + sin * (radius - 6)),
                  fill=TEXT_COLOR, width=1)
        label = str(tick)
        bbox = _text_bbox(draw, (0, 0), label, fonts["dial"])
        lx = cx + cos * (radius + 9) - (bbox[2] + bbox[0]) / 2
        ly = cy + sin * (radius + 9) - (bbox[3] + bbox[1]) / 2
        _draw_text(draw, (lx, ly), label, font=fonts["dial"], fill=LABEL_COLOR)
    try:
        value = max(0.0, min(9.0, float(kp_value)))
    except (TypeError, ValueError):
        value = None
    if value is not None:
        angle = math.radians(180 + 20 * value)
        tip = (cx + math.cos(angle) * (radius - 14), cy + math.sin(angle) * (radius - 14))
        draw.line((cx, cy, *tip), fill=TEXT_COLOR, width=3)
    draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=TEXT_COLOR, outline=TILE_OUTLINE)


def _draw_kp_panel(canvas, draw, rect, snapshot, fonts) -> None:
    """The Kp hero tile: the dial on the left, the reading and its context on the right."""

    x1, y1, x2, y2 = rect
    current = snapshot.current_kp if isinstance(snapshot.current_kp, Mapping) else {}
    scales = snapshot.current_scales if isinstance(snapshot.current_scales, Mapping) else {}
    forecast = snapshot.forecast_48h if isinstance(snapshot.forecast_48h, Mapping) else {}
    color = _severity_color(scales.get("g"))
    kp_value = _format_number(current.get("value"))
    mode = str(current.get("mode") or "unavailable")
    current_g = _scale_value(scales, "G")
    peak_kp = _format_number(forecast.get("max_kp"))
    peak_g = str(forecast.get("noaa_scale") or "G—")

    _tile(draw, rect)
    _draw_text(draw, (x1 + 12, y1 + 7), f"当前地磁指数{TEXT_SEPARATOR}CURRENT GEOMAGNETIC INDEX",
               font=fonts["kp_label"], fill=LABEL_COLOR)
    _draw_kp_gauge(draw, (x1 + 84, y2 - 16), 58, current.get("value"), fonts)

    right = x1 + 162
    number_font = fonts["kp_number"]
    _draw_text(draw, (right, y1 + 24), kp_value, font=number_font, fill=color)
    number_right = right + _text_width(draw, kp_value, number_font) + _text_bbox(draw, (0, 0), kp_value, number_font)[0]
    _draw_text(draw, (number_right + 6, y1 + 50), "Kp", font=fonts["metric_label"], fill=color)
    state_copy, state_font = _fit_kp_state(draw, current_g, mode, max_width=x2 - 12 - right)
    _draw_text(draw, (right, y1 + 76), state_copy, font=state_font, fill=color)
    _draw_text(draw, (right, y1 + 94), f"48H PEAK{TEXT_SEPARATOR}Kp {peak_kp} / {peak_g}",
               font=fonts["kp_peak"], fill=MUTED_COLOR)


def _fit_kp_state(draw, current_g, mode, *, max_width):
    """The current G level and Kp mode on one line beside the number, stepping 12 to 10 px."""
    for text in (f"CURRENT {current_g}{TEXT_SEPARATOR}{mode}", f"{current_g}{TEXT_SEPARATOR}{mode}"):
        for size in (12, 11, 10):
            font = _font(size, bold=True)
            if _text_width(draw, text, font) <= max_width:
                return text, font
    raise ApodPageLayoutError("Kp state does not fit beside the number at 10px")


def _draw_grs_panel(draw, snapshot, fonts) -> None:
    """G, R and S as status lamps coloured by their NOAA level."""
    x1, y1, x2, y2 = GRS_RECT
    scales = snapshot.current_scales if isinstance(snapshot.current_scales, Mapping) else {}
    _tile(draw, GRS_RECT)
    cell_width = (x2 - x1) / 3
    middle = (y1 + y2) / 2
    for index, (letter, name) in enumerate((("G", "地磁暴"), ("R", "无线电中断"), ("S", "太阳辐射"))):
        left = round(x1 + index * cell_width)
        if index:
            draw.line((left, y1 + 8, left, y2 - 9), fill=TILE_OUTLINE, width=1)
        lamp = _severity_color(_scale_level(scales, letter))
        cx = left + 20
        draw.ellipse((cx - 11, middle - 11, cx + 11, middle + 11), fill=_mix(lamp, TILE_COLOR, 0.55))
        draw.ellipse((cx - 7, middle - 7, cx + 7, middle + 7), fill=lamp, outline=_mix(lamp, (255, 255, 255), 0.4))
        _draw_text(draw, (left + 38, y1 + 4), _scale_value(scales, letter), font=fonts["grs_value"], fill=TEXT_COLOR)
        _draw_text(draw, (left + 38, y1 + 22), name, font=fonts["grs_name"], fill=LABEL_COLOR)


def _draw_metric_cell(canvas, draw, rect, icon, label, value, fonts) -> None:
    x1, y1, x2, y2 = rect
    _tile(draw, rect)
    taken = _paste_icon(canvas, icon, x1 + 8, y1 + (y2 - y1 - 24) // 2, 24)
    text_x = x1 + 10 + (taken + 6 if taken else 0)
    _draw_text(draw, (text_x, y1 + 4), label, font=fonts["metric_label"], fill=LABEL_COLOR)
    _draw_text(draw, (text_x, y1 + 18), value, font=fonts["metric_value"], fill=TEXT_COLOR)


def _draw_metrics_panel(canvas, draw, snapshot, fonts) -> None:
    speed = snapshot.solar_wind if isinstance(snapshot.solar_wind, Mapping) else {}
    magnetic = snapshot.magnetic_field if isinstance(snapshot.magnetic_field, Mapping) else {}
    forecast = snapshot.forecast_48h if isinstance(snapshot.forecast_48h, Mapping) else {}
    wind_value = _format_number(speed.get("speed_km_s"), decimals=0)
    if wind_value != "—":
        wind_value += " km/s"
    bz_value = _format_number(magnetic.get("bz_nt"), decimals=2)
    if bz_value != "—":
        bz_value = f"{str(magnetic.get('direction') or 'unknown')} {bz_value} nT"
    bt_value = _format_number(magnetic.get("bt_nt"), decimals=1)
    if bt_value != "—":
        bt_value += " nT"
    forecast_value = (
        f"{str(forecast.get('noaa_scale') or 'G—')}{TEXT_SEPARATOR}Kp "
        f"{_format_number(forecast.get('max_kp'))}"
    )

    x1, y1, x2, y2 = METRICS_RECT
    mid_x = (x1 + x2) // 2
    mid_y = (y1 + y2) // 2
    gap = METRIC_GAP // 2
    cells = (
        ((x1, y1, mid_x - gap, mid_y - gap), "solar_wind", f"太阳风{TEXT_SEPARATOR}WIND", wind_value),
        ((mid_x + gap, y1, x2, mid_y - gap), "magnet", "磁场 Bz", bz_value),
        ((x1, mid_y + gap, mid_x - gap, y2), "compass", "磁场强度 Bt", bt_value),
        ((mid_x + gap, mid_y + gap, x2, y2), "forecast", "48H FORECAST", forecast_value),
    )
    for rect, icon, label, value in cells:
        _draw_metric_cell(canvas, draw, rect, icon, label, value, fonts)


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
    """Three storm chances, each with a bar the width of its probability."""
    x1, y1, x2, y2 = PROBABILITIES_RECT
    probabilities = snapshot.probabilities if isinstance(snapshot.probabilities, Mapping) else {}
    values = (
        ("R1–R2", probabilities.get("r1_r2")),
        ("R3–R5", probabilities.get("r3_r5")),
        ("S1+", probabilities.get("s1_plus")),
    )
    _tile(draw, PROBABILITIES_RECT)
    taken = _paste_icon(canvas, "probability", x1 + 8, y1 + (y2 - y1 - 22) // 2, 22)
    cells_left = x1 + 10 + (taken + 4 if taken else 0)
    cell_width = (x2 - 8 - cells_left) / 3
    for index, (label, value) in enumerate(values):
        left = round(cells_left + index * cell_width)
        right = round(cells_left + (index + 1) * cell_width) - 8
        _draw_text(draw, (left, y1 + 6), label, font=fonts["probability_label"], fill=LABEL_COLOR)
        probability = "—" if value is None else f"{value}%"
        _draw_right(draw, right, y1 + 4, probability, font=fonts["probability_value"], fill=TEXT_COLOR)
        bar_y = y2 - 12
        draw.rounded_rectangle((left, bar_y, right, bar_y + 4), radius=2, fill=BAR_TRACK_COLOR)
        try:
            share = max(0.0, min(1.0, float(value) / 100))
        except (TypeError, ValueError):
            share = 0.0
        if share > 0:
            draw.rounded_rectangle((left, bar_y, left + max(4, round((right - left) * share)), bar_y + 4), radius=2,
                                   fill=_probability_color(value))


def _alert_copy(snapshot) -> tuple[str, tuple[int, int, int]]:
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


def _draw_alert_panel(canvas, draw, snapshot, fonts) -> None:
    x1, y1, x2, y2 = ALERT_RECT
    text, color = _alert_copy(snapshot)
    _tile(draw, ALERT_RECT, outline=color, fill=_mix(color, PAGE_COLOR, 0.82))
    icon = "alert" if isinstance(snapshot.alert, Mapping) or isinstance(snapshot.donki_event, Mapping) else "source"
    taken = _paste_icon(canvas, icon, x1 + 9, y1 + (y2 - y1 - 22) // 2, 22)
    text_x = x1 + 12 + (taken + 6 if taken else 0)
    lines, font = _fit_alert_copy(
        draw, text, max_width=x2 - 10 - text_x, required_active=isinstance(snapshot.alert, Mapping),
    )
    line_height = _line_height(draw, font)
    y = y1 + max(3, ((y2 - y1) - line_height * len(lines)) // 2)
    for line in lines:
        _draw_text(draw, (text_x, y), line, font=font, fill=TEXT_COLOR)
        y += line_height


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
    text = (
        f"NOAA SWPC{TEXT_SEPARATOR}OBS {observed}"
        f"{TEXT_SEPARATOR}CACHE {cached}"
    )
    font = fonts["source"]
    taken = _paste_icon(canvas, "source", SOURCE_RECT[0], SOURCE_RECT[1] + 2, 16)
    text_x = SOURCE_RECT[0] + (taken + 6 if taken else 0)
    if _text_width(draw, text, font) > SOURCE_RECT[2] - text_x:
        raise ApodPageLayoutError("complete NOAA source timestamps do not fit")
    _draw_text(draw, (text_x, SOURCE_RECT[1] + 4), text, font=font, fill=MUTED_COLOR)


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
    band_draw = ImageDraw.Draw(band)
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
) -> Image.Image:
    """Render the mission-control page: instrument tiles on the left, the photo full-bleed on the right."""

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
    draw = ImageDraw.Draw(canvas)
    fonts = _load_fonts()

    _place_photo(canvas, fit_photo(source_image, measured.photo_size), layout.caption_top)
    _draw_header(canvas, draw, rendered_at_utc, fonts)
    _draw_kp_panel(canvas, draw, KP_RECT, weather, fonts)
    _draw_grs_panel(draw, weather, fonts)
    _draw_metrics_panel(canvas, draw, weather, fonts)
    _draw_probabilities_panel(canvas, draw, weather, fonts)
    _draw_alert_panel(canvas, draw, weather, fonts)
    _draw_source_panel(canvas, draw, weather, fonts)
    _draw_caption(canvas, draw, layout)
    return canvas.convert("RGB")
