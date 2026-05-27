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
DEFAULT_FONT_FAMILY = "方正新楷近似"
DEFAULT_LOCAL_FONT = "FandolKai-Regular.otf"
LEGACY_BASE_FONT_FAMILIES = {"LXGW WenKai", "康熙字典体"}
FALLBACK_LOCAL_FONTS = (
    "FandolKai-Regular.otf",
    "LXGWWenKai-Regular.ttf",
    "I.Ming-8.10.ttf",
)
MISSING_GLYPH_PROBE = "\uffff"


def _size_tier(n: int) -> int:
    if n <= 36:
        return 1
    if n <= 64:
        return 2
    if n <= 96:
        return 3
    if n <= 140:
        return 4
    return 5


def _seed_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d-%H%M")


class ChineseLiteratureClock(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = sorted({
            f.get("name") or f.get("font_family")
            for f in get_fonts()
            if f.get("name") or f.get("font_family")
        })
        if DEFAULT_FONT_FAMILY not in params["available_fonts"]:
            params["available_fonts"].append(DEFAULT_FONT_FAMILY)
        return params

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        tz_name = device_config.get_config("timezone") or DEFAULT_TIMEZONE
        now = datetime.now(pytz.timezone(tz_name))
        hhmm = now.strftime("%H:%M")

        csv_path = os.path.join(self.get_plugin_dir("data"), "chinese_litclock.csv")
        try:
            ensure_dataset(csv_path)
        except FileNotFoundError as exc:
            logger.error("Chinese literature clock dataset missing: %s", exc)
            raise RuntimeError("Chinese literature clock dataset unavailable.") from exc

        allow_nsfw = self._enabled(settings.get("allow_nsfw"), default=False)
        rows, _used_time = resolve_with_fallback(csv_path, hhmm, allow_nsfw=allow_nsfw)

        if not rows:
            return self._render_no_quote(dimensions, hhmm, settings)

        strategy = settings.get("quote_selection") or "shortest"
        chosen = pick_quote(rows, strategy=strategy, seed_key=_seed_key(now))

        quote = sanitize(chosen["full_quote"])
        time_human = sanitize(chosen["time_human"])

        attribution = ""
        if self._enabled(settings.get("show_attribution"), default=True):
            book = sanitize(chosen.get("book_title", ""))
            author = sanitize(chosen.get("author_name", ""))
            attribution = f"- 《{book}》，{author}"
            if len(attribution) > ATTRIBUTION_MAX:
                attribution = attribution[: ATTRIBUTION_MAX - 3] + "..."

        return self._render_quote_image(dimensions, quote, time_human, attribution, settings)

    def _render_no_quote(self, dimensions, hhmm, settings):
        return self._render_quote_image(
            dimensions,
            f"现在是 {hhmm}。",
            hhmm,
            "暂无这个时刻的小说句子",
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

        font_family = self._normalize_font_family(settings.get("font_family") or DEFAULT_FONT_FAMILY)
        quote_max_height = max_height - (max(38, height // 9) if attribution else 0)
        quote_font, quote_bold_font, quote_lines, line_height = self._fit_quote(
            draw,
            quote,
            font_family,
            max_width,
            quote_max_height,
        )

        attribution_font = self._load_font_cascade(font_family, max(14, int(self._font_size(quote_font) * 0.42)))
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
            self._draw_text(draw, (width - margin_x - attr_width, y), trimmed, attribution_font, attribution_color)

        return image

    def _fit_quote(self, draw, quote, font_family, max_width, max_height):
        tier = _size_tier(len(quote))
        tier_max = {1: 66, 2: 58, 3: 48, 4: 38, 5: 30}[tier]
        max_size = min(tier_max, max(24, max_width // 9))

        for size in range(max_size, 17, -2):
            font = self._load_font_cascade(font_family, size)
            bold_font = self._load_font_cascade(font_family, size, "bold")
            lines = self._wrap_text(draw, quote, font, max_width)
            line_height = self._line_height(draw, font)
            if lines and line_height * len(lines) <= max_height:
                return font, bold_font, lines, line_height

        font = self._load_font_cascade(font_family, 18)
        bold_font = self._load_font_cascade(font_family, 18, "bold")
        return font, bold_font, self._wrap_text(draw, quote, font, max_width), self._line_height(draw, font)

    def _normalize_font_family(self, font_family):
        if font_family in LEGACY_BASE_FONT_FAMILIES:
            return DEFAULT_FONT_FAMILY
        return font_family

    def _load_font(self, font_family, size, weight="normal"):
        try:
            font = get_font(font_family, size, weight)
            if font:
                return font
        except OSError as exc:
            logger.warning("Could not load font '%s' (%s): %s", font_family, weight, exc)

        local_font = os.path.join(self.get_plugin_dir("fonts"), DEFAULT_LOCAL_FONT)
        if os.path.isfile(local_font):
            try:
                return ImageFont.truetype(local_font, size)
            except OSError as exc:
                logger.warning("Could not load bundled Chinese Kai font: %s", exc)

        return ImageFont.load_default()

    def _load_font_cascade(self, font_family, size, weight="normal"):
        fonts = []
        self._append_font(fonts, self._load_font(font_family, size, weight))

        for filename in FALLBACK_LOCAL_FONTS:
            local_font = os.path.join(self.get_plugin_dir("fonts"), filename)
            if os.path.isfile(local_font):
                try:
                    self._append_font(fonts, ImageFont.truetype(local_font, size))
                except OSError as exc:
                    logger.warning("Could not load bundled fallback font %s: %s", filename, exc)

        self._append_font(fonts, ImageFont.load_default())
        return fonts

    def _append_font(self, fonts, font):
        if not font:
            return
        key = self._font_key(font)
        if key not in {self._font_key(existing) for existing in fonts}:
            fonts.append(font)

    def _is_font_cascade(self, font):
        return isinstance(font, (list, tuple))

    def _font_size(self, font):
        if self._is_font_cascade(font):
            sizes = [getattr(item, "size", None) for item in font]
            return max([size for size in sizes if size] or [20])
        return getattr(font, "size", 20)

    def _font_key(self, font):
        return (
            str(getattr(font, "path", "")),
            getattr(font, "size", None),
            getattr(font, "index", None),
            font.__class__.__name__,
        )

    def _font_supports_char(self, font, char):
        if not char or char.isspace():
            return True

        cache = getattr(self, "_glyph_support_cache", None)
        if cache is None:
            cache = {}
            self._glyph_support_cache = cache

        key = (self._font_key(font), ord(char))
        if key in cache:
            return cache[key]

        try:
            glyph = font.getmask(char)
            missing = self._missing_glyph_signature(font)
            supported = (glyph.size, bytes(glyph)) != missing
        except (AttributeError, UnicodeEncodeError, ValueError):
            supported = False

        cache[key] = supported
        return supported

    def _missing_glyph_signature(self, font):
        cache = getattr(self, "_missing_glyph_cache", None)
        if cache is None:
            cache = {}
            self._missing_glyph_cache = cache

        key = self._font_key(font)
        if key not in cache:
            glyph = font.getmask(MISSING_GLYPH_PROBE)
            cache[key] = (glyph.size, bytes(glyph))
        return cache[key]

    def _font_for_char(self, fonts, char):
        if not self._is_font_cascade(fonts):
            return fonts
        for font in fonts:
            if self._font_supports_char(font, char):
                return font
        return fonts[0]

    def _wrap_text(self, draw, text, font, max_width):
        if " " in text.strip():
            lines = []
            current = ""
            for word in text.split():
                candidate = word if not current else f"{current} {word}"
                if self._text_width(draw, candidate, font) <= max_width or not current:
                    current = candidate
                else:
                    lines.extend(self._wrap_chars(draw, current, font, max_width))
                    current = word
            if current:
                lines.extend(self._wrap_chars(draw, current, font, max_width))
            return lines or [text]

        return self._wrap_chars(draw, text, font, max_width)

    def _wrap_chars(self, draw, text, font, max_width):
        lines = []
        current = ""
        for char in text:
            candidate = current + char
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
                continue

            lines.append(current)
            current = char

        if current:
            lines.append(current)
        return lines or [text]

    def _draw_highlighted_line(self, draw, position, line, time_human, font, bold_font, style, color, text_color):
        x, y = position
        index = line.find(time_human) if time_human else -1

        if index < 0:
            self._draw_text(draw, (x, y), line, font, text_color)
            return

        before = line[:index]
        match = line[index:index + len(time_human)]
        after = line[index + len(time_human):]

        self._draw_text(draw, (x, y), before, font, text_color)
        x += self._text_width(draw, before, font)

        match_font = bold_font if style == "bold" else font
        match_fill = color if style == "color" else text_color
        self._draw_text(draw, (x, y), match, match_font, match_fill)

        if style == "underline":
            underline_y = y + self._text_height(draw, match, match_font) + 2
            draw.line((x, underline_y, x + self._text_width(draw, match, match_font), underline_y), fill=text_color, width=2)

        x += self._text_width(draw, match, match_font)
        self._draw_text(draw, (x, y), after, font, text_color)

    def _fit_single_line(self, draw, text, font, max_width):
        if self._text_width(draw, text, font) <= max_width:
            return text

        suffix = "..."
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return text + suffix if text else suffix

    def _line_height(self, draw, font):
        if self._is_font_cascade(font):
            sample = "国Ag"
            height = max(self._text_height(draw, sample, item) for item in font)
        else:
            height = self._text_height(draw, "国Ag", font)
        return int(height * 1.32)

    def _text_width(self, draw, text, font):
        if self._is_font_cascade(font):
            return sum(self._text_width(draw, char, self._font_for_char(font, char)) for char in text)
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    def _text_height(self, draw, text, font):
        if self._is_font_cascade(font):
            if not text:
                return 0
            return max(self._text_height(draw, char, self._font_for_char(font, char)) for char in text)
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    def _draw_text(self, draw, position, text, font, fill):
        if not self._is_font_cascade(font):
            draw.text(position, text, font=font, fill=fill)
            return

        x, y = position
        for char in text:
            char_font = self._font_for_char(font, char)
            draw.text((x, y), char, font=char_font, fill=fill)
            x += self._text_width(draw, char, char_font)

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

    def _enabled(self, value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("on", "true", "1", "yes")
