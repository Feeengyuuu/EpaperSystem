import logging
import os
from datetime import datetime
import pytz

from PIL import Image, ImageDraw, ImageFont
from utils.app_utils import get_font, get_fonts
from plugins.base_plugin.base_plugin import BasePlugin

from .quote_picker import resolve_with_fallback, sanitize, pick_quote
from .dataset import ensure_dataset

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "US/Eastern"
ATTRIBUTION_MAX = 55
DEFAULT_BACKGROUND_COLOR = (0, 0, 0)
DEFAULT_TEXT_COLOR = (255, 255, 255)
DEFAULT_ATTRIBUTION_COLOR = (210, 210, 210)
DEFAULT_FONT_FAMILY = "LXGW WenKai"


def _size_tier(n: int) -> int:
    if n <= 80:
        return 1
    if n <= 160:
        return 2
    if n <= 260:
        return 3
    if n <= 380:
        return 4
    return 5


def _seed_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d-%H%M")


class LiteratureClock(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = sorted({
            f.get("name") or f.get("font_family")
            for f in get_fonts()
            if f.get("name") or f.get("font_family")
        })
        return params

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        tz_name = device_config.get_config("timezone") or DEFAULT_TIMEZONE
        now = datetime.now(pytz.timezone(tz_name))
        hhmm = now.strftime("%H:%M")

        csv_path = os.path.join(self.get_plugin_dir("data"), "litclock_annotated.csv")
        try:
            ensure_dataset(csv_path)
        except FileNotFoundError as exc:
            logger.error("Literature clock dataset missing: %s", exc)
            raise RuntimeError("Literature clock dataset unavailable.") from exc

        allow_nsfw = settings.get("allow_nsfw") in ("on", "true", True)
        rows, _used_time = resolve_with_fallback(csv_path, hhmm, allow_nsfw=allow_nsfw)

        if not rows:
            return self._render_no_quote(dimensions, hhmm, settings)

        strategy = settings.get("quote_selection") or "shortest"
        chosen = pick_quote(rows, strategy=strategy, seed_key=_seed_key(now))

        quote = sanitize(chosen["full_quote"])
        time_human = sanitize(chosen["time_human"])

        attribution = ""
        if settings.get("show_attribution", "on") in ("on", "true", True):
            book = sanitize(chosen.get("book_title", ""))
            author = sanitize(chosen.get("author_name", ""))
            attribution = f"- {book}, {author}"
            if len(attribution) > ATTRIBUTION_MAX:
                attribution = attribution[: ATTRIBUTION_MAX - 3] + "..."

        return self._render_quote_image(dimensions, quote, time_human, attribution, settings)

    def _render_no_quote(self, dimensions, hhmm, settings):
        return self._render_quote_image(
            dimensions,
            f"It is {hhmm}.",
            hhmm,
            "No quote for this minute",
            settings,
        )

    def _render_quote_image(self, dimensions, quote, time_human, attribution, settings):
        width, height = dimensions
        background_color = self._settings_color(settings, ("backgroundColor", "background_color"), DEFAULT_BACKGROUND_COLOR)
        text_color = self._settings_color(settings, ("textColor", "text_color"), DEFAULT_TEXT_COLOR)
        attribution_color = self._settings_color(settings, ("attributionColor", "attribution_color"), DEFAULT_ATTRIBUTION_COLOR)

        image = Image.new("RGB", dimensions, background_color)
        draw = ImageDraw.Draw(image)

        margin_x = max(24, int(width * 0.06))
        margin_y = max(24, int(height * 0.08))
        max_width = width - margin_x * 2
        max_height = height - margin_y * 2

        font_family = settings.get("font_family") or DEFAULT_FONT_FAMILY
        quote_max_height = max_height - (max(38, height // 9) if attribution else 0)
        quote_font, quote_bold_font, quote_lines, line_height = self._fit_quote(
            draw,
            quote,
            font_family,
            max_width,
            quote_max_height,
        )

        attribution_font = self._load_font(font_family, max(14, int(getattr(quote_font, "size", 20) * 0.42)))
        attribution_height = 0
        if attribution:
            attribution_height = self._text_height(draw, attribution, attribution_font) + max(12, height // 36)

        quote_height = line_height * len(quote_lines)
        total_height = quote_height + attribution_height
        y = max(margin_y, (height - total_height) // 2)

        highlight_style = settings.get("highlight_style") or "bold"
        highlight_color = self._parse_color(settings.get("highlight_color"), text_color)

        for line in quote_lines:
            self._draw_highlighted_line(
                draw,
                (margin_x, y),
                line,
                time_human,
                quote_font,
                quote_bold_font,
                highlight_style,
                highlight_color,
                text_color,
            )
            y += line_height

        if attribution:
            y += max(8, height // 60)
            trimmed = self._fit_single_line(draw, attribution, attribution_font, max_width)
            attr_width = self._text_width(draw, trimmed, attribution_font)
            draw.text((width - margin_x - attr_width, y), trimmed, font=attribution_font, fill=attribution_color)

        return image

    def _fit_quote(self, draw, quote, font_family, max_width, max_height):
        tier = _size_tier(len(quote))
        tier_max = {1: 64, 2: 52, 3: 42, 4: 34, 5: 28}[tier]
        max_size = min(tier_max, max(24, max_width // 9))

        for size in range(max_size, 17, -2):
            font = self._load_font(font_family, size)
            bold_font = self._load_font(font_family, size, "bold")
            lines = self._wrap_text(draw, quote, font, max_width)
            line_height = self._line_height(draw, font)
            if lines and line_height * len(lines) <= max_height:
                return font, bold_font, lines, line_height

        font = self._load_font(font_family, 18)
        bold_font = self._load_font(font_family, 18, "bold")
        return font, bold_font, self._wrap_text(draw, quote, font, max_width), self._line_height(draw, font)

    def _load_font(self, font_family, size, weight="normal"):
        try:
            font = get_font(font_family, size, weight)
            if font:
                return font
        except OSError as exc:
            logger.warning("Could not load font '%s' (%s): %s", font_family, weight, exc)
        return ImageFont.load_default()

    def _wrap_text(self, draw, text, font, max_width):
        words = text.split()
        lines = []
        current = ""

        for word in words:
            candidate = word if not current else f"{current} {word}"
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word

        if current:
            lines.append(current)

        return lines or [text]

    def _draw_highlighted_line(self, draw, position, line, time_human, font, bold_font, style, color, text_color):
        x, y = position
        lower_line = line.lower()
        lower_time = time_human.lower()
        index = lower_line.find(lower_time) if lower_time else -1

        if index < 0:
            draw.text((x, y), line, font=font, fill=text_color)
            return

        before = line[:index]
        match = line[index:index + len(time_human)]
        after = line[index + len(time_human):]

        draw.text((x, y), before, font=font, fill=text_color)
        x += self._text_width(draw, before, font)

        match_font = bold_font if style == "bold" else font
        match_fill = color if style == "color" else text_color
        draw.text((x, y), match, font=match_font, fill=match_fill)

        if style == "underline":
            underline_y = y + self._text_height(draw, match, match_font) + 2
            draw.line((x, underline_y, x + self._text_width(draw, match, match_font), underline_y), fill=text_color, width=2)

        x += self._text_width(draw, match, match_font)
        draw.text((x, y), after, font=font, fill=text_color)

    def _fit_single_line(self, draw, text, font, max_width):
        if self._text_width(draw, text, font) <= max_width:
            return text

        suffix = "..."
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return text + suffix if text else suffix

    def _line_height(self, draw, font):
        return int(self._text_height(draw, "Ag", font) * 1.32)

    def _text_width(self, draw, text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    def _text_height(self, draw, text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    def _settings_color(self, settings, keys, fallback):
        for key in keys:
            if settings.get(key):
                return self._parse_color(settings.get(key), fallback)
        return fallback

    def _parse_color(self, value, fallback):
        value = str(value or "").strip().lstrip("#")
        try:
            if len(value) == 3:
                value = "".join(ch * 2 for ch in value)
            if len(value) == 6:
                return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
        return fallback
