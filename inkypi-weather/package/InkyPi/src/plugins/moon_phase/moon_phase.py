from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytz
from PIL import Image, ImageDraw, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.source_book import Source, SourceBook
from runtime.long_task_executor import task_context_or_default
from runtime.refresh_contracts import TaskCancelled, TaskContext
from utils.app_utils import coerce_bool, get_base_ui_font, get_font, resolve_dimensions
from utils.cache_manager import CacheBudget

from . import lunar_phases, sky_times
from .lunar_phases import FIRST_QUARTER, FULL_MOON, LAST_QUARTER, NEW_MOON


logger = logging.getLogger(__name__)
# A true new moon (2000-01-06 18:14 UT), the start of Meeus' lunation numbering.
NEW_MOON_EPOCH_UTC = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
RESAMPLE_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
# A principal phase is an instant; the page names it for the 24 hours centred on it.
PRINCIPAL_WINDOW = timedelta(hours=12)
SKY_CACHE_BUDGET = CacheBudget(4 * 86400, 8, 64 * 1024)
TASK_SECONDS = 20

PHASES = [
    ("New Moon", NEW_MOON),
    ("First Quarter", FIRST_QUARTER),
    ("Full Moon", FULL_MOON),
    ("Last Quarter", LAST_QUARTER),
]
PHASE_NAMES = {fraction: name for name, fraction in PHASES}
BETWEEN_NAMES = {
    NEW_MOON: "Waxing Crescent",
    FIRST_QUARTER: "Waxing Gibbous",
    FULL_MOON: "Waning Gibbous",
    LAST_QUARTER: "Waning Crescent",
}
SHORT_NAMES = {NEW_MOON: "NEW", FIRST_QUARTER: "FIRST Q", FULL_MOON: "FULL", LAST_QUARTER: "LAST Q"}

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
    previous_new_utc: datetime
    upcoming: tuple = ()


def calculate_moon_info(now: datetime | None = None) -> MoonInfo:
    """Lunar phase data for the given instant, from the true (Meeus ch. 49) phase times."""
    now_utc = _coerce_utc(now or datetime.now(timezone.utc))
    phases = lunar_phases.principal_phases_around(now_utc, before=1, after=2)
    previous = max((item for item in phases if item[0] <= now_utc), key=lambda item: item[0])
    upcoming = tuple(item for item in phases if item[0] > now_utc)[:4]
    following = upcoming[0]
    previous_new = max(when for when, kind in phases if kind == NEW_MOON and when <= now_utc)
    next_of = {kind: when for when, kind in reversed(upcoming)}

    if now_utc - previous[0] < PRINCIPAL_WINDOW:
        phase_name = PHASE_NAMES[previous[1]]
    elif following[0] - now_utc <= PRINCIPAL_WINDOW:
        phase_name = PHASE_NAMES[following[1]]
    else:
        phase_name = BETWEEN_NAMES[previous[1]]
    phase_fraction = lunar_phases.elongation_fraction(now_utc)
    # Keep the drawn disc on the same side of new moon as the phase times.
    if previous[1] == NEW_MOON and phase_fraction > 0.5:
        phase_fraction = 0.0
    elif following[1] == NEW_MOON and phase_fraction < 0.5:
        phase_fraction = 0.9999

    return MoonInfo(
        now_utc=now_utc,
        age_days=(now_utc - previous_new).total_seconds() / 86400.0,
        phase_fraction=phase_fraction,
        illumination=lunar_phases.illuminated_fraction(now_utc),
        phase_name=phase_name,
        direction="Waxing" if previous[1] in (NEW_MOON, FIRST_QUARTER) else "Waning",
        next_new_utc=next_of[NEW_MOON],
        next_full_utc=next_of[FULL_MOON],
        next_first_quarter_utc=next_of[FIRST_QUARTER],
        next_last_quarter_utc=next_of[LAST_QUARTER],
        previous_new_utc=previous_new,
        upcoming=upcoming,
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
        sky = self._sky_times(settings, device_config, local_now)

        theme = str(settings.get("themeMode") or settings.get("theme_mode") or "midnight").lower()
        palette = _palette(theme)
        img = Image.new("RGB", dimensions, palette["paper"])
        draw = ImageDraw.Draw(img)

        self._draw_stars(draw, width, height, palette)
        self._draw_technical_grid(draw, width, height, palette)
        if width >= height:
            self._draw_landscape(img, draw, info, local_now, local_tz, palette, sky)
        else:
            self._draw_portrait(img, draw, info, local_now, local_tz, palette, sky)

        return img

    def _sky_times(self, settings, device_config, local_now):
        """Today's moonrise/moonset/twilight table for home, or {} (those rows then stay hidden)."""
        if "skyTimes" in settings:  # previews and tests hand the table in directly
            data = settings.get("skyTimes")
            return data if sky_times.valid_sky_times(data) else {}
        home = sky_times.find_home(settings, device_config)
        if home is None:
            return {}
        day = local_now.date()
        offset = sky_times.utc_offset_hours(local_now.replace(hour=12, minute=0, second=0, microsecond=0))
        cache_only = any(coerce_bool(settings.get(key), default=False)
                         for key in ("_theme_render_only", "_cached_render_only", "cached_render_only"))
        parent = task_context_or_default(timeout_seconds=TASK_SECONDS)
        context = TaskContext(parent.cancel_event,
                              min(parent.deadline_monotonic, parent.clock() + TASK_SECONDS), parent.clock)
        try:
            directory = self.cache_dir(env_var="MOON_PHASE_CACHE_DIR", leaf="cache", create=True, strip=True)
            book = SourceBook(directory, SKY_CACHE_BUDGET, schema=1, max_bytes=16 * 1024)
            source = Source("sky", {"date": day.isoformat(), "home": list(home), "tz": offset},
                            lambda ctx: sky_times.fetch_sky_times(home, day, offset, ctx),
                            fresh_seconds=86400, usable_seconds=36 * 3600,
                            validate=sky_times.valid_sky_times, timeout_seconds=10)
            result = book.load(source, datetime.now(timezone.utc), allow_network=not cache_only, context=context)
        except TaskCancelled:
            raise
        except (OSError, ValueError) as exc:
            logger.warning("Moon rise/set table unavailable: %s", type(exc).__name__)
            return {}
        data = result.get("data") or {}
        return data if data.get("date") == day.isoformat() else {}

    def _draw_landscape(self, img, draw, info, local_now, local_tz, palette, sky):
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
        large = _fit_font(draw, phase_label, text_w, "Jost", int(height * 0.105), "bold", 28)
        metric_font = _font("Jost", max(42, int(height * 0.14)), "bold")
        body = _font("Jost", max(19, int(height * 0.045)), "normal")
        label = _font("Jost", max(12, int(height * 0.029)), "bold")

        draw.text((text_x, top_y), "LUNAR TELEMETRY", font=small, fill=palette["muted"])
        updated = "SYNC " + local_now.strftime("%m/%d %H:%M")
        updated_w = _text_size(draw, updated, small)[0]
        draw.text((width - margin - updated_w, top_y), updated, font=small, fill=palette["dim"])

        phase_y = top_y + int(height * 0.08)
        draw.text((text_x, phase_y), phase_label, font=large, fill=palette["ink"])

        metric_y = phase_y + _text_size(draw, phase_label, large)[1] + int(height * 0.022)
        metric_h = self._draw_metric(draw, text_x, metric_y, info, metric_font, label, palette)

        strip_h = int(height * 0.165)
        strip_y = height - margin - strip_h + 4
        rows_y = metric_y + metric_h + int(height * 0.05)
        rows_gap = max(34, min(int(height * 0.092), (strip_y - rows_y - 8) // 3))
        self._draw_rows(draw, text_x, rows_y, text_w, rows_gap, info, sky, label, body, palette)
        self._draw_phase_strip(img, draw, text_x, strip_y, text_w, strip_h, info, local_tz, palette)

    def _draw_portrait(self, img, draw, info, local_now, local_tz, palette, sky):
        width, height = img.size
        margin = max(18, int(width * 0.06))
        moon_size = int(min(width * 0.62, height * 0.36))
        moon_x = (width - moon_size) // 2
        moon_y = margin + 34
        moon = self._render_moon_disc(moon_size, info.phase_fraction, palette)
        img.paste(moon, (moon_x, moon_y), moon)

        small = _font("Jost", max(12, int(width * 0.035)), "bold")
        phase_label = info.phase_name.upper()
        large = _fit_font(draw, phase_label, width - 2 * margin, "Jost", int(width * 0.1), "bold", 24)
        metric = _font("Jost", max(34, int(width * 0.13)), "bold")
        body = _font("Jost", max(16, int(width * 0.042)), "normal")
        label = _font("Jost", max(11, int(width * 0.029)), "bold")

        draw.text((margin, margin), "LUNAR TELEMETRY", font=small, fill=palette["muted"])
        updated = "SYNC " + local_now.strftime("%m/%d %H:%M")
        draw.text((width - margin - _text_size(draw, updated, small)[0], margin), updated, font=small,
                  fill=palette["dim"])
        y = moon_y + moon_size + int(height * 0.025)
        _draw_centered(draw, width / 2, y, phase_label, large, palette["ink"])
        y += _text_size(draw, phase_label, large)[1] + 16
        metric_text = f"{round(info.illumination * 100):02d}%"
        group_w = _text_size(draw, metric_text, metric)[0] + 12 + _text_size(draw, "ILLUMINATED", label)[0]
        metric_h = self._draw_metric(draw, int((width - group_w) / 2), y, info, metric, label, palette)
        y += metric_h + int(height * 0.04)

        progress_y = height - margin - 14
        strip_h = int(height * 0.1)
        strip_y = progress_y - 42 - strip_h
        rows_gap = max(34, min(int(height * 0.058), (strip_y - y - 8) // 3))
        self._draw_rows(draw, margin, y, width - 2 * margin, rows_gap, info, sky, label, body, palette)
        self._draw_phase_strip(img, draw, margin, strip_y, width - 2 * margin, strip_h, info, local_tz, palette)
        self._draw_month_progress(draw, margin, progress_y, width - 2 * margin, info.phase_fraction, palette)

    def _draw_metric(self, draw, x, y, info, metric_font, label_font, palette):
        """The lit percentage, its caption and the waxing/waning marker beside it; returns its height."""
        metric_text = f"{round(info.illumination * 100):02d}%"
        draw.text((x, y), metric_text, font=metric_font, fill=palette["ink"])
        bbox = draw.textbbox((x, y), metric_text, font=metric_font)
        label_x = bbox[2] + 12
        label_h = _text_size(draw, "ILLUMINATED", label_font)[1]
        caption_y = bbox[1] + max(0, (bbox[3] - bbox[1]) // 2 - label_h - 6)
        draw.text((label_x, caption_y), "ILLUMINATED", font=label_font, fill=palette["muted"])
        vector_y = caption_y + label_h + 12
        size = max(8, label_h)
        cy = vector_y + label_h / 2 + 2
        _draw_marker(draw, label_x, cy, size, info.direction == "Waxing", palette["accent"])
        draw.text((label_x + size + 6, vector_y), info.direction.upper(), font=label_font, fill=palette["ink"])
        return bbox[3] - y

    def _draw_rows(self, draw, x, y, width, gap, info, sky, label_font, body_font, palette):
        self._draw_detail_row(draw, x, y, width, "LUNAR DAY", f"{info.age_days:.1f} days", label_font, body_font,
                              palette)
        y += gap
        if not sky:
            following = info.upcoming[0]
            hours = max(0, int((following[0] - info.now_utc).total_seconds() // 3600))
            value = f"{SHORT_NAMES[following[1]]} IN {hours // 24}D {hours % 24}H"
            self._draw_detail_row(draw, x, y, width, "NEXT PHASE", value, label_font, body_font, palette)
            return
        if sky.get("moonrise") or sky.get("moonset"):
            self._draw_rise_set_row(draw, x, y, width, sky, label_font, body_font, palette)
            y += gap
        if sky.get("civil_begin") and sky.get("civil_end"):
            value = f"{sky['civil_begin']} – {sky['civil_end']}"
            self._draw_detail_row(draw, x, y, width, "CIVIL TWILIGHT", value, label_font, body_font, palette)

    def _draw_rise_set_row(self, draw, x, y, width, sky, label_font, body_font, palette):
        """MOONRISE and MOONSET side by side, each behind a small solid up/down marker."""
        draw.line((x, y - 8, x + width, y - 8), fill=palette["rule"], width=1)
        half = width // 2
        label_h = _text_size(draw, "MOON", label_font)[1]
        size = max(8, label_h)
        for index, (name, key, up) in enumerate((("MOONRISE", "moonrise", True), ("MOONSET", "moonset", False))):
            cell_x = x + index * (half + 8)
            _draw_marker(draw, cell_x, y + label_h / 2 + 2, size, up, palette["rise"] if up else palette["set"])
            draw.text((cell_x + size + 6, y), name, font=label_font, fill=palette["dim"])
            value = sky.get(key) or "--:--"
            value_w = _text_size(draw, value, body_font)[0]
            right = x + half - 10 if index == 0 else x + width
            draw.text((right - value_w, y - 4), value, font=body_font, fill=palette["ink"])
        draw.line((x + half, y - 2, x + half, y + label_h + 6), fill=palette["rule"], width=1)

    def _draw_phase_strip(self, img, draw, x, y, width, height, info, local_tz, palette):
        """The next four principal phases in time order: icon, name, local date and time."""
        cells = info.upcoming[:4]
        if not cells:
            return
        gap = 10
        cell_w = (width - gap * (len(cells) - 1)) / len(cells)
        icon = max(16, int(height * 0.27))
        name_font = _font("Jost", max(11, int(height * 0.16)), "bold")
        date_font = _font("Jost", max(16, int(height * 0.25)), "bold")
        time_font = _font("Jost", max(13, int(height * 0.2)), "normal")
        name_h = _text_size(draw, "NEW", name_font)[1]
        for index, (when, kind) in enumerate(cells):
            cell_x = int(x + index * (cell_w + gap))
            first = index == 0
            draw.line((cell_x, y, int(cell_x + cell_w), y), fill=palette["ink"] if first else palette["soft_rule"],
                      width=3 if first else 1)
            disc = self._render_moon_disc(icon, kind, palette, icon=True)
            img.paste(disc, (cell_x, y + 9), disc)
            draw.text((cell_x + icon + 6, y + 9 + (icon - name_h) / 2 - 2), SHORT_NAMES[kind], font=name_font,
                      fill=palette["ink"])
            # Phase tables (USNO's included) give the instant to the nearest minute.
            local = (when + timedelta(seconds=30)).replace(second=0, microsecond=0).astimezone(local_tz)
            date_text, time_text = local.strftime("%m/%d"), local.strftime("%H:%M")
            date_y = y + 9 + icon + 6
            draw.text((cell_x, date_y), date_text, font=date_font, fill=palette["ink"])
            date_box = draw.textbbox((cell_x, date_y), date_text, font=date_font)
            time_w = _text_size(draw, time_text, time_font)[0]
            if date_box[2] + 6 + time_w <= cell_x + cell_w:
                time_box = draw.textbbox((0, 0), time_text, font=time_font)
                draw.text((date_box[2] + 6, date_box[3] - time_box[3]), time_text, font=time_font,
                          fill=palette["ink"])
            else:
                draw.text((cell_x, date_box[3] + 4), time_text, font=time_font, fill=palette["ink"])

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
            if width >= height and x > width * 0.47 and height * 0.06 < y:
                continue
            if width < height and height * 0.5 < y:
                continue
            color = palette["star"] if index % 5 == 0 else palette["rule"]
            draw.point((x, y), fill=color)
            if index % 17 == 0 and 2 < x < width - 3 and 2 < y < height - 3:
                draw.point((x + 1, y), fill=color)
                draw.point((x, y + 1), fill=color)

    def _render_moon_disc(self, diameter, phase_fraction, palette, icon=False):
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
                tone = 0.96 if icon else _surface_tone(x, y)
                bright = palette["moon_shadow"] + lit * (
                    palette["moon_light"] * limb * tone - palette["moon_shadow"]
                )
                bright = int(max(0, min(255, bright)))
                pixels[px, py] = (bright, bright, bright, alpha)

        if icon:
            # Small phase icons: a hairline rim keeps the dark side readable on both themes.
            ImageDraw.Draw(image).ellipse(
                (center - radius, center - radius, center + radius, center + radius),
                outline=palette["soft_rule"] + (255,), width=max(3, size // 24),
            )
        return image.resize((diameter, diameter), RESAMPLE_LANCZOS)


def _draw_marker(draw, x, cy, size, up, fill):
    half = size / 2
    if up:
        points = [(x, cy + half), (x + size, cy + half), (x + half, cy - half)]
    else:
        points = [(x, cy - half), (x + size, cy - half), (x + half, cy + half)]
    draw.polygon(points, fill=fill)


def _surface_tone(x, y):
    tone = 1.0
    for crater in CRATERS:
        cx = crater[0]
        cy = crater[1]
        radius = crater[2]
        depth = crater[3]
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
    return resolve_dimensions(device_config)


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
            "accent": (37, 84, 199),
            "rise": (5, 150, 105),
            "set": (200, 30, 50),
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
        "accent": (96, 140, 255),
        "rise": (52, 211, 153),
        "set": (244, 86, 104),
        "moon_light": 238,
        "moon_shadow": 13,
    }


def _font(name, size, weight="normal"):
    if str(name or "").strip().casefold() in {
        "jost",
        "lxgw wenkai",
        "microsoft yahei",
        "微软雅黑",
    }:
        return get_base_ui_font(int(size), bold=weight == "bold")
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
