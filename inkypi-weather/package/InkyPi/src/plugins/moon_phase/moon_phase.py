from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytz
from PIL import Image, ImageDraw, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_font


SYNODIC_MONTH_DAYS = 29.530588853
NEW_MOON_EPOCH_UTC = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
RESAMPLE_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")

PHASES = [
    ("New Moon", 0.0),
    ("First Quarter", 0.25),
    ("Full Moon", 0.5),
    ("Last Quarter", 0.75),
]

CRATERS = [
    (-0.42, -0.33, 0.115, 0.22),
    (-0.22, -0.46, 0.070, 0.16),
    (0.16, -0.38, 0.085, 0.18),
    (0.38, -0.17, 0.075, 0.15),
    (-0.08, -0.12, 0.135, 0.20),
    (-0.38, 0.04, 0.090, 0.18),
    (0.23, 0.02, 0.120, 0.19),
    (0.49, 0.16, 0.060, 0.16),
    (-0.18, 0.24, 0.080, 0.17),
    (0.10, 0.31, 0.105, 0.18),
    (-0.48, 0.36, 0.055, 0.14),
    (0.35, 0.42, 0.075, 0.15),
]


@dataclass(frozen=True)
class MoonInfo:
    now_utc: datetime
    age_days: float
    phase_fraction: float
    illumination: float
    phase_name: str
    direction: str
    next_new_utc: datetime
    next_full_utc: datetime
    next_first_quarter_utc: datetime
    next_last_quarter_utc: datetime


def calculate_moon_info(now: datetime | None = None) -> MoonInfo:
    """Return approximate real-time lunar phase data for the given instant."""
    now_utc = _coerce_utc(now or datetime.now(timezone.utc))
    elapsed_days = (now_utc - NEW_MOON_EPOCH_UTC).total_seconds() / 86400.0
    age = elapsed_days % SYNODIC_MONTH_DAYS
    phase_fraction = age / SYNODIC_MONTH_DAYS
    illumination = (1.0 - math.cos(2.0 * math.pi * phase_fraction)) / 2.0
    direction = "Waxing" if phase_fraction < 0.5 else "Waning"
    phase_name = _phase_name(age)

    return MoonInfo(
        now_utc=now_utc,
        age_days=age,
        phase_fraction=phase_fraction,
        illumination=illumination,
        phase_name=phase_name,
        direction=direction,
        next_new_utc=_next_phase_datetime(now_utc, age, 0.0),
        next_full_utc=_next_phase_datetime(now_utc, age, SYNODIC_MONTH_DAYS / 2.0),
        next_first_quarter_utc=_next_phase_datetime(now_utc, age, SYNODIC_MONTH_DAYS / 4.0),
        next_last_quarter_utc=_next_phase_datetime(now_utc, age, SYNODIC_MONTH_DAYS * 3.0 / 4.0),
    )


class MoonPhase(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["style_settings"] = False
        return template_params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = _display_dimensions(device_config)
        width, height = dimensions
        local_tz = _device_timezone(device_config)
        now_utc = _settings_now(settings) or datetime.now(timezone.utc)
        info = calculate_moon_info(now_utc)
        local_now = info.now_utc.astimezone(local_tz)

        theme = str(settings.get("themeMode") or settings.get("theme_mode") or "midnight").lower()
        palette = _palette(theme)
        img = Image.new("RGB", dimensions, palette["paper"])
        draw = ImageDraw.Draw(img)

        self._draw_stars(draw, width, height, palette)
        self._draw_technical_grid(draw, width, height, palette)
        if width >= height:
            self._draw_landscape(img, draw, info, local_now, local_tz, palette, settings)
        else:
            self._draw_portrait(img, draw, info, local_now, local_tz, palette, settings)

        return img

    def _draw_landscape(self, img, draw, info, local_now, local_tz, palette, settings):
        width, height = img.size
        margin = max(22, int(width * 0.045))
        moon_size = int(min(height * 0.76, width * 0.42))
        moon_x = margin + max(0, int((width * 0.45 - margin - moon_size) / 2))
        moon_y = int((height - moon_size) / 2)
        moon = self._render_moon_disc(moon_size, info.phase_fraction, palette)
        img.paste(moon, (moon_x, moon_y), moon)

        ring_pad = max(3, moon_size // 90)
        draw.ellipse(
            [moon_x - ring_pad, moon_y - ring_pad, moon_x + moon_size + ring_pad, moon_y + moon_size + ring_pad],
            outline=palette["soft_rule"],
            width=max(1, moon_size // 140),
        )
        draw.arc(
            [moon_x - 14, moon_y - 14, moon_x + moon_size + 14, moon_y + moon_size + 14],
            start=-36,
            end=64,
            fill=palette["ink"],
            width=max(1, moon_size // 120),
        )
        draw.line(
            (int(width * 0.465), margin, int(width * 0.465), height - margin),
            fill=palette["rule"],
            width=1,
        )
        self._draw_month_progress(
            draw,
            margin,
            height - margin - 18,
            int(width * 0.38),
            info.phase_fraction,
            palette,
        )

        text_x = int(width * 0.49)
        text_w = width - text_x - margin
        top_y = margin + 2

        small = _font("Jost", max(13, int(height * 0.033)), "bold")
        phase_label = info.phase_name.upper()
        large = _fit_font(draw, phase_label, text_w, "Jost", int(height * 0.112), "bold", 28)
        metric_font = _font("Jost", max(42, int(height * 0.155)), "bold")
        body = _font("Jost", max(19, int(height * 0.047)), "normal")
        label = _font("Jost", max(12, int(height * 0.030)), "bold")

        draw.text((text_x, top_y), "LUNAR TELEMETRY", font=small, fill=palette["muted"])
        updated = "SYNC " + local_now.strftime("%m/%d %H:%M")
        updated_w = _text_size(draw, updated, small)[0]
        draw.text((width - margin - updated_w, top_y), updated, font=small, fill=palette["dim"])

        phase_y = top_y + int(height * 0.095)
        draw.text((text_x, phase_y), phase_label, font=large, fill=palette["ink"])

        metric_y = phase_y + _text_size(draw, phase_label, large)[1] + int(height * 0.025)
        metric_text = f"{round(info.illumination * 100):02d}%"
        draw.text((text_x, metric_y), metric_text, font=metric_font, fill=palette["ink"])
        metric_w = _text_size(draw, metric_text, metric_font)[0]
        draw.text(
            (text_x + metric_w + 12, metric_y + int(height * 0.042)),
            "ILLUMINATED",
            font=label,
            fill=palette["muted"],
        )

        details_y = metric_y + _text_size(draw, metric_text, metric_font)[1] + int(height * 0.055)
        detail_gap = int(height * 0.094)
        detail_rows = [
            ("LUNAR DAY", f"{info.age_days:.1f} days"),
            ("PHASE VECTOR", info.direction.upper()),
            ("NEXT FULL", _format_datetime(info.next_full_utc, local_tz)),
            ("NEXT NEW", _format_datetime(info.next_new_utc, local_tz)),
        ]
        for index, (name, value) in enumerate(detail_rows):
            y = details_y + index * detail_gap
            self._draw_detail_row(draw, text_x, y, text_w, name, value, label, body, palette)

    def _draw_portrait(self, img, draw, info, local_now, local_tz, palette, settings):
        width, height = img.size
        margin = max(18, int(width * 0.06))
        moon_size = int(min(width * 0.76, height * 0.42))
        moon_x = (width - moon_size) // 2
        moon_y = margin + 28
        moon = self._render_moon_disc(moon_size, info.phase_fraction, palette)
        img.paste(moon, (moon_x, moon_y), moon)

        small = _font("Jost", max(12, int(width * 0.035)), "bold")
        phase_label = info.phase_name.upper()
        large = _fit_font(draw, phase_label, width - 2 * margin, "Jost", int(width * 0.105), "bold", 24)
        metric = _font("Jost", max(34, int(width * 0.15)), "bold")
        body = _font("Jost", max(16, int(width * 0.047)), "normal")
        label = _font("Jost", max(11, int(width * 0.032)), "bold")

        draw.text((margin, margin), "LUNAR TELEMETRY", font=small, fill=palette["muted"])
        y = moon_y + moon_size + int(height * 0.045)
        _draw_centered(draw, width / 2, y, phase_label, large, palette["ink"])
        y += _text_size(draw, phase_label, large)[1] + 16
        _draw_centered(draw, width / 2, y, f"{round(info.illumination * 100):02d}%", metric, palette["ink"])
        y += _text_size(draw, "00%", metric)[1] + 22

        rows = [
            ("LUNAR DAY", f"{info.age_days:.1f} days"),
            ("PHASE VECTOR", info.direction.upper()),
            ("NEXT FULL", _format_datetime(info.next_full_utc, local_tz)),
            ("NEXT NEW", _format_datetime(info.next_new_utc, local_tz)),
            ("SYNC", local_now.strftime("%m/%d %H:%M")),
        ]
        for name, value in rows:
            self._draw_detail_row(draw, margin, y, width - 2 * margin, name, value, label, body, palette)
            y += int(height * 0.066)

        self._draw_month_progress(draw, margin, height - margin - 14, width - 2 * margin, info.phase_fraction, palette)

    def _draw_detail_row(self, draw, x, y, width, name, value, label_font, value_font, palette):
        draw.line((x, y - 8, x + width, y - 8), fill=palette["rule"], width=1)
        draw.text((x, y), name, font=label_font, fill=palette["dim"])
        value_font = _fit_font(draw, value, int(width * 0.62), "Jost", getattr(value_font, "size", 18), "normal", 12)
        value_w = _text_size(draw, value, value_font)[0]
        draw.text((x + width - value_w, y - 4), value, font=value_font, fill=palette["ink"])

    def _draw_month_progress(self, draw, x, y, width, phase_fraction, palette):
        dot_count = 30
        dot_gap = width / max(dot_count - 1, 1)
        active = min(dot_count - 1, max(0, round(phase_fraction * (dot_count - 1))))
        for index in range(dot_count):
            cx = x + index * dot_gap
            r = 2 if index != active else 4
            color = palette["ink"] if index <= active else palette["rule"]
            draw.ellipse((cx - r, y - r, cx + r, y + r), fill=color)
        title_font = _font("Jost", 10, "bold")
        draw.text((x, y - 18), "SYNODIC CYCLE", font=title_font, fill=palette["dim"])
        draw.text((x, y + 10), "NEW", font=title_font, fill=palette["dim"])
        full = "FULL"
        full_w = _text_size(draw, full, title_font)[0]
        draw.text((x + width / 2 - full_w / 2, y + 10), full, font=title_font, fill=palette["dim"])
        end = "NEW"
        end_w = _text_size(draw, end, title_font)[0]
        draw.text((x + width - end_w, y + 10), end, font=title_font, fill=palette["dim"])

    def _draw_technical_grid(self, draw, width, height, palette):
        left = int(width * 0.035)
        right = width - left
        top = int(height * 0.055)
        bottom = height - top
        draw.line((left, top, left + 52, top), fill=palette["rule"], width=1)
        draw.line((left, top, left, top + 52), fill=palette["rule"], width=1)
        draw.line((right - 52, top, right, top), fill=palette["rule"], width=1)
        draw.line((right, top, right, top + 52), fill=palette["rule"], width=1)
        draw.line((left, bottom - 52, left, bottom), fill=palette["rule"], width=1)
        draw.line((left, bottom, left + 52, bottom), fill=palette["rule"], width=1)
        draw.line((right, bottom - 52, right, bottom), fill=palette["rule"], width=1)
        draw.line((right - 52, bottom, right, bottom), fill=palette["rule"], width=1)

    def _draw_stars(self, draw, width, height, palette):
        count = max(18, int(width * height / 9500))
        for index in range(count):
            seed = (index * 1103515245 + 12345) & 0x7FFFFFFF
            x = (seed % max(width, 1))
            y = ((seed // 9973) % max(height, 1))
            if width >= height and x < width * 0.46 and height * 0.20 < y < height * 0.82:
                continue
            if width >= height and x > width * 0.47 and height * 0.06 < y < height * 0.84:
                continue
            color = palette["star"] if index % 5 == 0 else palette["rule"]
            draw.point((x, y), fill=color)
            if index % 17 == 0 and 2 < x < width - 3 and 2 < y < height - 3:
                draw.point((x + 1, y), fill=color)
                draw.point((x, y + 1), fill=color)

    def _render_moon_disc(self, diameter, phase_fraction, palette):
        scale = 2
        size = max(96, int(diameter * scale))
        radius = size * 0.475
        center = (size - 1) / 2.0
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        pixels = image.load()
        angle = 2.0 * math.pi * phase_fraction
        sun_x = math.sin(angle)
        sun_z = -math.cos(angle)

        for py in range(size):
            y = (py - center) / radius
            y2 = y * y
            for px in range(size):
                x = (px - center) / radius
                r2 = x * x + y2
                edge = 1.0 - r2
                if edge <= -0.02:
                    continue
                alpha = int(255 * _smoothstep(-0.02, 0.018, edge))
                z = math.sqrt(max(0.0, 1.0 - r2))
                lit = _smoothstep(-0.026, 0.038, x * sun_x + z * sun_z)
                limb = 0.66 + 0.34 * z
                tone = _surface_tone(x, y)
                bright = palette["moon_shadow"] + lit * (
                    palette["moon_light"] * limb * tone - palette["moon_shadow"]
                )
                bright = int(max(0, min(255, bright)))
                pixels[px, py] = (bright, bright, bright, alpha)

        return image.resize((diameter, diameter), RESAMPLE_LANCZOS)


def _phase_name(age):
    if age < 1.0 or age >= SYNODIC_MONTH_DAYS - 1.0:
        return "New Moon"
    if age < SYNODIC_MONTH_DAYS * 0.25 - 1.0:
        return "Waxing Crescent"
    if age < SYNODIC_MONTH_DAYS * 0.25 + 1.0:
        return "First Quarter"
    if age < SYNODIC_MONTH_DAYS * 0.5 - 1.0:
        return "Waxing Gibbous"
    if age < SYNODIC_MONTH_DAYS * 0.5 + 1.0:
        return "Full Moon"
    if age < SYNODIC_MONTH_DAYS * 0.75 - 1.0:
        return "Waning Gibbous"
    if age < SYNODIC_MONTH_DAYS * 0.75 + 1.0:
        return "Last Quarter"
    return "Waning Crescent"


def _next_phase_datetime(now_utc, age, target_age):
    days_until = (target_age - age) % SYNODIC_MONTH_DAYS
    if days_until < 1.0 / 1440.0:
        days_until += SYNODIC_MONTH_DAYS
    return now_utc + timedelta(days=days_until)


def _surface_tone(x, y):
    tone = 1.0
    for cx, cy, radius, depth in CRATERS:
        dx = (x - cx) / radius
        dy = (y - cy) / (radius * 0.82)
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 1.0:
            tone -= depth * (1.0 - dist) ** 2
            if dist > 0.72:
                ring = 1.0 - abs(dist - 0.86) / 0.14
                tone += max(0.0, ring) * depth * 0.32
    maria = 0.055 * math.sin(8.0 * x + 1.7) * math.cos(5.0 * y - 0.4)
    return max(0.62, min(1.12, tone + maria))


def _display_dimensions(device_config):
    dimensions = device_config.get_resolution()
    if device_config.get_config("orientation", default="horizontal") == "vertical":
        return dimensions[::-1]
    return dimensions


def _device_timezone(device_config):
    tz_name = device_config.get_config("timezone", default="UTC") or "UTC"
    try:
        return pytz.timezone(str(tz_name))
    except Exception:
        return pytz.UTC


def _settings_now(settings):
    raw = settings.get("nowUtc") or settings.get("debugNowUtc")
    if not raw:
        return None
    if isinstance(raw, datetime):
        return _coerce_utc(raw)
    try:
        text = str(raw).replace("Z", "+00:00")
        return _coerce_utc(datetime.fromisoformat(text))
    except Exception:
        return None


def _coerce_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_datetime(value_utc, tz):
    return value_utc.astimezone(tz).strftime("%m/%d %H:%M")


def _palette(theme):
    if theme in {"day", "paper", "light"}:
        return {
            "paper": (244, 244, 238),
            "ink": (8, 9, 12),
            "muted": (62, 64, 70),
            "dim": (104, 106, 112),
            "rule": (190, 190, 184),
            "soft_rule": (92, 92, 88),
            "star": (42, 42, 40),
            "moon_light": 228,
            "moon_shadow": 42,
        }
    return {
        "paper": (0, 0, 0),
        "ink": (246, 246, 240),
        "muted": (172, 174, 180),
        "dim": (102, 106, 116),
        "rule": (42, 44, 52),
        "soft_rule": (118, 120, 128),
        "star": (210, 210, 204),
        "moon_light": 238,
        "moon_shadow": 13,
    }


def _font(name, size, weight="normal"):
    try:
        return get_font(name, int(size), font_weight=weight) or ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def _fit_font(draw, text, max_width, name, start_size, weight="normal", min_size=10):
    size = max(int(start_size), min_size)
    while size > min_size:
        font = _font(name, size, weight)
        if _text_size(draw, text, font)[0] <= max_width:
            return font
        size -= 1
    return _font(name, min_size, weight)


def _text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_centered(draw, cx, y, text, font, fill):
    width = _text_size(draw, text, font)[0]
    draw.text((cx - width / 2, y), text, font=font, fill=fill)


def _smoothstep(edge0, edge1, value):
    if edge0 == edge1:
        return 1.0 if value >= edge1 else 0.0
    t = min(max((value - edge0) / (edge1 - edge0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)
