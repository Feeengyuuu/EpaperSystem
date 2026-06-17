import logging
import os
from datetime import datetime
from pathlib import Path
import pytz

from PIL import Image, ImageDraw, ImageFont
from utils.app_utils import get_font, bounded_int, get_available_font_names
from plugins.base_plugin.base_plugin import BasePlugin

from .quote_picker import resolve_with_fallback, sanitize, pick_quote
from .dataset import ensure_dataset
from .open_library import lookup_book_metadata

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "US/Eastern"
DEFAULT_BACKGROUND_COLOR = (0, 0, 0)
DEFAULT_TEXT_COLOR = (255, 255, 255)
DEFAULT_ATTRIBUTION_COLOR = (210, 210, 210)
DEFAULT_FONT_FAMILY = "方正新楷近似"
DEFAULT_LOCAL_FONT = "FandolKai-Regular.otf"
OPEN_LIBRARY_CACHE_DAYS = 30
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
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT_FAMILY)
        return params

    def generate_image(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)

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

        strategy = settings.get("quote_selection") or "source_random"
        chosen = pick_quote(rows, strategy=strategy, seed_key=_seed_key(now))

        quote = sanitize(chosen["full_quote"])
        time_human = sanitize(chosen["time_human"])

        source_block = None
        if self._enabled(settings.get("show_attribution"), default=True):
            book = sanitize(chosen.get("book_title", ""))
            author = sanitize(chosen.get("author_name", ""))
            metadata = self._lookup_open_library_metadata(book, author, settings)
            source_block = self._build_source_block(book, author, metadata)

        return self._render_quote_image(dimensions, quote, time_human, source_block, settings)

    def _render_no_quote(self, dimensions, hhmm, settings):
        return self._render_quote_image(
            dimensions,
            f"现在是 {hhmm}。",
            hhmm,
            {"book_line": "暂无这个时刻的小说句子", "meta_line": "本地句库 · 等待下一次报时"},
            settings,
        )

    def _render_quote_image(self, dimensions, quote, time_human, source_block, settings):
        width, height = dimensions
        background_color = self._settings_color(settings, ("backgroundColor", "background_color"), DEFAULT_BACKGROUND_COLOR)
        text_color = self._settings_color(settings, ("textColor", "text_color"), DEFAULT_TEXT_COLOR)
        attribution_color = self._settings_color(settings, ("attributionColor", "attribution_color"), DEFAULT_ATTRIBUTION_COLOR)
        source_meta_color = self._source_meta_color(background_color, attribution_color)

        image = Image.new("RGB", dimensions, background_color)
        draw = ImageDraw.Draw(image)

        margin_x = max(24, int(width * 0.06))
        margin_y = max(24, int(height * 0.08))
        max_width = width - margin_x * 2
        max_height = height - margin_y * 2

        font_family = self._normalize_font_family(settings.get("font_family") or DEFAULT_FONT_FAMILY)
        source_block = self._coerce_source_block(source_block)
        source_fonts = self._source_fonts(font_family, width, height)
        source_gap = max(8, height // 60) if source_block else 0
        source_height = (
            self._source_block_height(draw, source_block, source_fonts, max_width) + source_gap
            if source_block else 0
        )
        quote_max_height = max_height - source_height
        quote_font, quote_bold_font, quote_lines, line_height = self._fit_quote(
            draw,
            quote,
            font_family,
            max_width,
            quote_max_height,
        )

        quote_height = line_height * len(quote_lines)
        total_height = quote_height + source_height
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

        if source_block:
            y += source_gap
            self._draw_source_block(
                draw,
                source_block,
                source_fonts,
                margin_x,
                y,
                max_width,
                width,
                attribution_color,
                source_meta_color,
            )

        return image

    def _lookup_open_library_metadata(self, book, author, settings):
        if not self._enabled(settings.get("open_library_enrichment"), default=True):
            return None
        if not book:
            return None
        cache_days = self._int_setting(settings.get("open_library_cache_days"), OPEN_LIBRARY_CACHE_DAYS, 1, 365)
        try:
            return lookup_book_metadata(
                book,
                author,
                cache_dir=self._open_library_cache_dir(),
                cache_days=cache_days,
                force_refresh=self._enabled(settings.get("force_open_library_refresh"), default=False),
            )
        except Exception as exc:
            logger.warning("Could not enrich Chinese literature source from Open Library: %s", exc)
            return None

    def _open_library_cache_dir(self):
        override = os.getenv("INKYPI_CHINESE_LITCLOCK_OPEN_LIBRARY_CACHE")
        path = Path(override) if override else Path(self.get_plugin_dir(".open_library_cache"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _build_source_block(self, book, author, metadata=None):
        metadata = metadata if isinstance(metadata, dict) else None
        title = book or (metadata or {}).get("title") or ""
        authors = (metadata or {}).get("authors") or []
        author_text = author or ("、".join(authors[:2]) if authors else "")

        if title and author_text:
            book_line = f"《{title}》 · {author_text}"
        elif title:
            book_line = f"《{title}》"
        else:
            book_line = author_text or "未标注书目"

        return {
            "book_line": book_line,
            "meta_line": self._format_metadata_line(metadata),
            "source_label": "Open Library" if metadata else "本地来源",
            "source_url": (metadata or {}).get("open_library_url", ""),
        }

    def _format_metadata_line(self, metadata):
        if not isinstance(metadata, dict):
            return "本地句库 · 原著出处"

        pieces = []
        year = metadata.get("first_publish_year")
        if year:
            pieces.append(f"首版 {year}")
        editions = metadata.get("edition_count")
        if editions:
            pieces.append(f"{editions} 个版本")
        publisher = metadata.get("publisher")
        if publisher:
            pieces.append(self._short_text(str(publisher), 18))
        if not pieces:
            pieces.append("书目信息")

        if metadata.get("from_cache"):
            pieces.append("缓存")
        return "Open Library · " + " · ".join(pieces)

    def _coerce_source_block(self, source_block):
        if not source_block:
            return None
        if isinstance(source_block, str):
            text = source_block.strip()
            if not text:
                return None
            return {"book_line": text, "meta_line": "", "source_label": "本地来源", "source_url": ""}
        if isinstance(source_block, dict):
            book_line = str(source_block.get("book_line") or "").strip()
            meta_line = str(source_block.get("meta_line") or "").strip()
            if not book_line and not meta_line:
                return None
            return {
                "book_line": book_line,
                "meta_line": meta_line,
                "source_label": str(source_block.get("source_label") or "").strip(),
                "source_url": str(source_block.get("source_url") or "").strip(),
            }
        return None

    def _source_fonts(self, font_family, width, height):
        title_size = max(15, min(21, width // 42))
        meta_size = max(12, int(title_size * 0.76))
        label_size = max(11, int(title_size * 0.7))
        return {
            "label": self._load_font_cascade(font_family, label_size),
            "title": self._load_font_cascade(font_family, title_size),
            "meta": self._load_font_cascade(font_family, meta_size),
        }

    def _source_block_height(self, draw, source_block, fonts, max_width):
        if not source_block:
            return 0
        label_h = self._text_height(draw, "来源", fonts["label"])
        title_h = self._text_height(draw, source_block.get("book_line", ""), fonts["title"])
        meta = source_block.get("meta_line", "")
        meta_h = self._text_height(draw, meta, fonts["meta"]) if meta else 0
        return 1 + 9 + label_h + 5 + title_h + (5 + meta_h if meta else 0)

    def _draw_source_block(
        self,
        draw,
        source_block,
        fonts,
        margin_x,
        y,
        max_width,
        width,
        title_color,
        meta_color,
    ):
        line_color = self._mix_color(title_color, meta_color, 0.45)
        draw.line((margin_x, y, margin_x + max_width, y), fill=line_color, width=1)
        y += 9

        label = "来源"
        self._draw_text(draw, (margin_x, y), label, fonts["label"], meta_color)

        badge = "OPEN LIBRARY" if source_block.get("source_label") == "Open Library" else "LOCAL SOURCE"
        badge = self._fit_single_line(draw, badge, fonts["label"], max_width // 2)
        badge_width = self._text_width(draw, badge, fonts["label"])
        self._draw_text(draw, (width - margin_x - badge_width, y), badge, fonts["label"], meta_color)

        y += self._text_height(draw, label, fonts["label"]) + 5
        book_line = self._fit_single_line(draw, source_block.get("book_line", ""), fonts["title"], max_width)
        self._draw_text(draw, (margin_x, y), book_line, fonts["title"], title_color)
        y += self._text_height(draw, book_line, fonts["title"]) + 5

        meta_line = source_block.get("meta_line", "")
        if meta_line:
            meta_line = self._fit_single_line(draw, meta_line, fonts["meta"], max_width)
            self._draw_text(draw, (margin_x, y), meta_line, fonts["meta"], meta_color)

    def _source_meta_color(self, background_color, attribution_color):
        return self._mix_color(attribution_color, background_color, 0.78)

    def _mix_color(self, a, b, weight):
        weight = max(0.0, min(1.0, float(weight)))
        return tuple(int(a[i] * weight + b[i] * (1.0 - weight)) for i in range(3))

    def _short_text(self, text, max_len):
        text = (text or "").strip()
        if len(text) <= max_len:
            return text
        return text[: max(1, max_len - 3)] + "..."

    def _int_setting(self, value, default, minimum, maximum):
        return bounded_int(value, default, minimum, maximum)

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

        plugin_fonts = self.get_plugin_dir("fonts")
        static_fonts = os.path.join(
            os.path.dirname(os.path.dirname(self.get_plugin_dir())), "static", "fonts"
        )
        for filename in FALLBACK_LOCAL_FONTS:
            for font_dir in (plugin_fonts, static_fonts):
                local_font = os.path.join(font_dir, filename)
                if os.path.isfile(local_font):
                    try:
                        self._append_font(fonts, ImageFont.truetype(local_font, size))
                    except OSError as exc:
                        logger.warning("Could not load bundled fallback font %s: %s", filename, exc)
                    break

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
