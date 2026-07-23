"""Fixed 800x480 NASAPics and NOAA space-weather PIL renderer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.apod.space_weather import SpaceWeatherSnapshot
from utils.app_utils import get_base_ui_font

if TYPE_CHECKING:
    from plugins.apod.apod import ApodRecord


HEADER_RECT = (0, 0, 800, 65)
LEFT_RECT = (0, 65, 368, 480)
KP_RECT = (20, 77, 354, 180)
GRS_RECT = (20, 190, 354, 218)
METRICS_RECT = (20, 228, 354, 318)
PROBABILITIES_RECT = (20, 326, 354, 364)
ALERT_RECT = (20, 374, 354, 414)
SOURCE_RECT = (20, 456, 354, 476)
RIGHT_X = 368

PAGE_SIZE = (800, 480)
CAPTION_MIN_TOP = 300
CAPTION_MAX_TOP = 364
PHOTO_TOP = 65
RIGHT_INSET = 12
RIGHT_TEXT_RECT = (RIGHT_X + RIGHT_INSET, 0, 800 - RIGHT_INSET, 480)

PAGE_COLOR = (249, 249, 246)
HEADER_COLOR = (252, 253, 252)
LEFT_COLOR = (246, 241, 232)
CAPTION_COLOR = (252, 252, 249)
INK_COLOR = (22, 35, 47)
MUTED_COLOR = (57, 69, 78)
RULE_COLOR = (123, 133, 139)
DIVIDER_COLOR = (24, 39, 52)
GREEN_COLOR = (28, 158, 72)
ORANGE_COLOR = (224, 92, 36)
RED_COLOR = (197, 49, 41)
BLUE_COLOR = (20, 96, 156)


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
        "kp_state": _font(16, bold=True),
        "kp_peak": _font(10, bold=True),
        "grs_value": _font(14, bold=True),
        "metric_label": _font(12, bold=True),
        "metric_value": _font(17, bold=True),
        "probability_label": _font(12, bold=True),
        "probability_value": _font(14, bold=True),
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


def _fit_complete_lines(
    *,
    draw,
    text: str,
    sizes: tuple[int, ...],
    max_width: int,
    max_lines: int,
    bold: bool,
) -> tuple[tuple[str, ...], ImageFont.FreeTypeFont]:
    for size in sizes:
        font = _font(size, bold=bold)
        lines = _wrap_complete(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return lines, font
    raise ApodPageLayoutError(
        f"complete title does not fit the {max_lines}-line caption limit"
    )


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
    en_lines, en_font = _fit_complete_lines(
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
        zh_lines, zh_font = _fit_complete_lines(
            draw=draw,
            text=chinese,
            sizes=(20, 19, 18, 17, 16),
            max_width=RIGHT_TEXT_RECT[2] - RIGHT_TEXT_RECT[0],
            max_lines=3,
            bold=True,
        )
    else:
        zh_lines = ()
        zh_font = None

    kicker_font = _font(10, bold=True)
    meta_font = _font(10, bold=True)
    credit_copy = f"CREDIT · {str(copyright or 'NASA / APOD')}"
    credit_lines = _wrap_complete(
        draw,
        credit_copy,
        meta_font,
        RIGHT_TEXT_RECT[2] - RIGHT_TEXT_RECT[0],
    )
    if len(credit_lines) > 2:
        raise ApodPageLayoutError("complete APOD credit exceeds two metadata lines")
    date_copy = f"NASA APOD · {str(apod_date or 'date unavailable')}"
    if _text_width(draw, date_copy, meta_font) > RIGHT_TEXT_RECT[2] - RIGHT_TEXT_RECT[0]:
        raise ApodPageLayoutError("complete APOD date does not fit the caption")

    def measure(show_kicker: bool):
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
    warning_copy = str(warning or "").strip()
    kicker_copy = warning_copy or "TODAY'S ASTRONOMY PICTURE"
    if _text_width(draw, kicker_copy, kicker_font) > (
        RIGHT_TEXT_RECT[2] - RIGHT_TEXT_RECT[0]
    ):
        raise ApodPageLayoutError("complete APOD warning exceeds the caption width")

    show_kicker = True
    measured = measure(show_kicker=True)
    if measured["required_height"] > maximum_height:
        if warning_copy:
            raise ApodPageLayoutError(
                "complete fallback metadata exceeds the 180px caption limit"
            )
        show_kicker = False
        measured = measure(show_kicker=False)
    if measured["required_height"] > maximum_height:
        raise ApodPageLayoutError(
            "complete bilingual metadata exceeds the 180px caption limit"
        )

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
    photo_rect = (RIGHT_X, PHOTO_TOP, PAGE_SIZE[0], caption.caption_top)
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


def _severity_color(g_value) -> tuple[int, int, int]:
    try:
        severity = int(g_value)
    except (TypeError, ValueError):
        return MUTED_COLOR
    if severity <= 0:
        return GREEN_COLOR
    if severity <= 2:
        return ORANGE_COLOR
    return RED_COLOR


def _draw_kp_panel(draw, rect, snapshot, fonts) -> None:
    """Draw the fixed Kp hero cell directly inside ``rect``."""

    x1, y1, x2, y2 = rect
    current = snapshot.current_kp if isinstance(snapshot.current_kp, Mapping) else {}
    scales = (
        snapshot.current_scales
        if isinstance(snapshot.current_scales, Mapping)
        else {}
    )
    forecast = (
        snapshot.forecast_48h
        if isinstance(snapshot.forecast_48h, Mapping)
        else {}
    )
    color = _severity_color(scales.get("g"))
    kp_value = _format_number(current.get("value"))
    mode = str(current.get("mode") or "unavailable")
    current_g = _scale_value(scales, "G")
    peak_kp = _format_number(forecast.get("max_kp"))
    peak_g = str(forecast.get("noaa_scale") or "G—")

    draw.rectangle((x1, y1, x2 - 1, y2 - 1), outline=RULE_COLOR, width=1)
    _draw_centered(
        draw,
        (x1 + 3, y1 + 2, x2 - 3, y1 + 18),
        "当前地磁指数 · CURRENT KP",
        font=fonts["kp_label"],
        fill=BLUE_COLOR,
    )
    _draw_centered(
        draw,
        (x1 + 48, y1 + 12, x2 - 48, y1 + 58),
        kp_value,
        font=fonts["kp_number"],
        fill=color,
    )
    _draw_text(
        draw, (x1 + 216, y1 + 42), "Kp", font=fonts["metric_label"], fill=color
    )
    _draw_centered(
        draw,
        (x1 + 4, y1 + 61, x2 - 4, y1 + 79),
        f"CURRENT {current_g} · MODE {mode}",
        font=fonts["kp_state"],
        fill=color,
    )
    _draw_centered(
        draw,
        (x1 + 4, y1 + 81, x2 - 4, y2 - 3),
        f"48H PEAK · Kp {peak_kp} / {peak_g}",
        font=fonts["kp_peak"],
        fill=INK_COLOR,
    )


def _draw_grs_panel(draw, snapshot, fonts) -> None:
    x1, y1, x2, y2 = GRS_RECT
    scales = (
        snapshot.current_scales
        if isinstance(snapshot.current_scales, Mapping)
        else {}
    )
    cell_width = (x2 - x1) / 3
    for index, letter in enumerate(("G", "R", "S")):
        left = round(x1 + index * cell_width)
        right = round(x1 + (index + 1) * cell_width)
        draw.rectangle((left, y1, right - 1, y2 - 1), outline=RULE_COLOR, width=1)
        _draw_centered(
            draw,
            (left + 2, y1 + 7, right - 2, y2 - 2),
            _scale_value(scales, letter),
            font=fonts["grs_value"],
            fill=INK_COLOR,
        )


def _draw_metric_cell(draw, rect, label, value, fonts) -> None:
    x1, y1, x2, y2 = rect
    draw.rectangle((x1, y1, x2 - 1, y2 - 1), outline=RULE_COLOR, width=1)
    _draw_text(
        draw,
        (x1 + 6, y1 + 2),
        label,
        font=fonts["metric_label"],
        fill=MUTED_COLOR,
    )
    _draw_text(
        draw,
        (x1 + 6, y1 + 18),
        value,
        font=fonts["metric_value"],
        fill=INK_COLOR,
    )


def _draw_metrics_panel(draw, snapshot, fonts) -> None:
    speed = snapshot.solar_wind if isinstance(snapshot.solar_wind, Mapping) else {}
    magnetic = (
        snapshot.magnetic_field
        if isinstance(snapshot.magnetic_field, Mapping)
        else {}
    )
    forecast = (
        snapshot.forecast_48h
        if isinstance(snapshot.forecast_48h, Mapping)
        else {}
    )
    wind_value = _format_number(speed.get("speed_km_s"), decimals=0)
    if wind_value != "—":
        wind_value += " km/s"
    bz_value = _format_number(magnetic.get("bz_gsm_nt"), decimals=2)
    if bz_value != "—":
        bz_value = f"{str(magnetic.get('bz_direction') or 'unknown')} {bz_value} nT"
    bt_value = _format_number(magnetic.get("bt_nt"), decimals=1)
    if bt_value != "—":
        bt_value += " nT"
    forecast_value = (
        f"{str(forecast.get('noaa_scale') or 'G—')} · Kp "
        f"{_format_number(forecast.get('max_kp'))}"
    )

    x1, y1, x2, y2 = METRICS_RECT
    mid_x = (x1 + x2) // 2
    mid_y = (y1 + y2) // 2
    _draw_metric_cell(
        draw, (x1, y1, mid_x, mid_y), "太阳风 · WIND", wind_value, fonts
    )
    _draw_metric_cell(draw, (mid_x, y1, x2, mid_y), "磁场 Bz", bz_value, fonts)
    _draw_metric_cell(draw, (x1, mid_y, mid_x, y2), "磁场强度 Bt", bt_value, fonts)
    _draw_metric_cell(
        draw, (mid_x, mid_y, x2, y2), "48H FORECAST", forecast_value, fonts
    )


def _draw_probabilities_panel(draw, snapshot, fonts) -> None:
    x1, y1, x2, y2 = PROBABILITIES_RECT
    probabilities = (
        snapshot.probabilities
        if isinstance(snapshot.probabilities, Mapping)
        else {}
    )
    values = (
        ("R1–R2", probabilities.get("r1_r2")),
        ("R3–R5", probabilities.get("r3_r5")),
        ("S1+", probabilities.get("s1_plus")),
    )
    cell_width = (x2 - x1) / 3
    for index, (label, value) in enumerate(values):
        left = round(x1 + index * cell_width)
        right = round(x1 + (index + 1) * cell_width)
        draw.rectangle((left, y1, right - 1, y2 - 1), outline=RULE_COLOR, width=1)
        _draw_centered(
            draw,
            (left + 2, y1 + 4, right - 2, y1 + 14),
            label,
            font=fonts["probability_label"],
            fill=MUTED_COLOR,
        )
        probability = "—" if value is None else f"{value}%"
        _draw_centered(
            draw,
            (left + 2, y1 + 17, right - 2, y2 - 2),
            probability,
            font=fonts["probability_value"],
            fill=INK_COLOR,
        )


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
        return " · ".join(parts), ORANGE_COLOR

    event = snapshot.donki_event if isinstance(snapshot.donki_event, Mapping) else None
    if event:
        kind = str(event.get("kind") or "EVENT")
        detail = str(
            event.get("class_type")
            or event.get("arrival_time_utc")
            or event.get("event_id")
            or "active"
        )
        return f"NASA DONKI · {kind} {detail}", BLUE_COLOR
    if snapshot.alert_state == "confirmed_empty":
        return "NOAA ALERTS · no active items", GREEN_COLOR
    return "NOAA ALERTS · temporarily unavailable", MUTED_COLOR


def _fit_box_copy(draw, text, *, font, max_width, required_active=False):
    lines = _wrap_complete(draw, text, font, max_width)
    if len(lines) <= 2:
        return lines
    label = "active alert" if required_active else "alert copy"
    raise ApodPageLayoutError(
        f"complete {label} does not fit the fixed cell at 12px"
    )


def _draw_alert_panel(draw, snapshot, fonts) -> None:
    x1, y1, x2, y2 = ALERT_RECT
    text, color = _alert_copy(snapshot)
    draw.rectangle((x1, y1, x2 - 1, y2 - 1), fill=CAPTION_COLOR)
    draw.rectangle((x1, y1, x1 + 2, y2 - 1), fill=color)
    font = fonts["alert"]
    lines = _fit_box_copy(
        draw,
        text,
        font=font,
        max_width=x2 - x1 - 16,
        required_active=isinstance(snapshot.alert, Mapping),
    )
    line_height = _line_height(draw, font)
    y = y1 + max(3, ((y2 - y1) - line_height * len(lines)) // 2)
    for line in lines:
        _draw_text(draw, (x1 + 10, y), line, font=font, fill=INK_COLOR)
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


def _draw_source_panel(draw, snapshot, fonts) -> None:
    observed = _utc_stamp(snapshot.oldest_core_observed_at_utc)
    cached = _utc_stamp(snapshot.fetched_at_utc)
    text = f"NOAA SWPC · OBS {observed} · CACHE {cached}"
    font = fonts["source"]
    if _text_width(draw, text, font) > SOURCE_RECT[2] - SOURCE_RECT[0]:
        raise ApodPageLayoutError("complete NOAA source timestamps do not fit")
    _draw_text(
        draw,
        (SOURCE_RECT[0], SOURCE_RECT[1] + 3),
        text,
        font=font,
        fill=MUTED_COLOR,
    )


def _draw_header(draw, rendered_at_utc, fonts) -> None:
    _draw_text(
        draw,
        (20, 10),
        "NASAPics × SPACE WEATHER",
        font=fonts["brand"],
        fill=INK_COLOR,
    )
    _draw_right(
        draw,
        780,
        24,
        _utc_stamp(rendered_at_utc, include_date=True),
        font=fonts["header_meta"],
        fill=INK_COLOR,
    )
    draw.line((0, HEADER_RECT[3] - 1, 799, HEADER_RECT[3] - 1), fill=DIVIDER_COLOR)


def _draw_caption(draw, layout) -> None:
    x1 = RIGHT_TEXT_RECT[0]
    x2 = RIGHT_TEXT_RECT[2]
    draw.rectangle((RIGHT_X, layout.caption_top, 799, 479), fill=CAPTION_COLOR)
    draw.rectangle((RIGHT_X, layout.caption_top, 799, layout.caption_top + 2), fill=ORANGE_COLOR)
    if layout.show_kicker and layout.kicker_y is not None:
        _draw_text(
            draw,
            (x1, layout.kicker_y),
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
            _draw_text(draw, (x1, y), line, font=layout.title_zh_font, fill=INK_COLOR)
            y += zh_advance
    y = layout.title_en_y
    en_advance = _line_height(draw, layout.title_en_font)
    for line in layout.title_en_lines:
        _draw_text(draw, (x1, y), line, font=layout.title_en_font, fill=MUTED_COLOR)
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
        fill=MUTED_COLOR,
    )


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
    """Render the approved fixed mirrored page with one full-bleed cover crop."""

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
    expected_rect = (RIGHT_X, PHOTO_TOP, PAGE_SIZE[0], layout.caption_top)
    if measured.photo_rect != expected_rect:
        raise ValueError("APOD page measurement does not match the caption boundary")

    canvas = Image.new("RGB", PAGE_SIZE, PAGE_COLOR)
    draw = ImageDraw.Draw(canvas)
    fonts = _load_fonts()

    draw.rectangle((0, 0, 799, HEADER_RECT[3] - 1), fill=HEADER_COLOR)
    draw.rectangle((0, LEFT_RECT[1], LEFT_RECT[2] - 1, 479), fill=LEFT_COLOR)
    canvas.paste(fit_photo(source_image, measured.photo_size), (RIGHT_X, PHOTO_TOP))

    _draw_header(draw, rendered_at_utc, fonts)
    _draw_kp_panel(draw, KP_RECT, weather, fonts)
    _draw_grs_panel(draw, weather, fonts)
    _draw_metrics_panel(draw, weather, fonts)
    _draw_probabilities_panel(draw, weather, fonts)
    _draw_alert_panel(draw, weather, fonts)
    _draw_source_panel(draw, weather, fonts)
    _draw_caption(draw, layout)
    draw.rectangle(
        (RIGHT_X - 2, PHOTO_TOP, RIGHT_X - 1, 479), fill=DIVIDER_COLOR
    )
    return canvas.convert("RGB")
