"""A small 800x480 drawing surface shared by dashboard-style plugin pages."""

from __future__ import annotations

import re

from PIL import Image, ImageDraw

from utils.app_utils import get_base_ui_font, get_font


def re_tokens(text):
    """Split into breakable units: a space-led Latin word, or one CJK character."""
    return re.findall(r"\s*[぀-ヿ㐀-鿿豈-﫿]|\s*[^\s぀-ヿ㐀-鿿豈-﫿]+",
                      text)


class PageCanvas:
    def __init__(self, palette, *, family="Microsoft YaHei", size=(800, 480)):
        self.palette = palette
        self.image = Image.new("RGB", size, palette["paper"])
        self.draw = ImageDraw.Draw(self.image)
        self.family = str(family or "Microsoft YaHei")
        self._fonts = {}

    def color(self, value):
        return self.palette.get(value, value) if isinstance(value, str) else value

    def font(self, size, bold=True):
        key = (int(size), bool(bold))
        if key not in self._fonts:
            try:
                font = get_font(self.family, key[0], "bold" if bold else "normal")
            except (KeyError, TypeError, ValueError, OSError):
                font = None
            self._fonts[key] = font or get_base_ui_font(key[0], bold=bold)
        return self._fonts[key]

    def fit(self, text, size, width, bold=True):
        """Return ``text`` cut at a character boundary with an ellipsis to fit ``width``."""
        font = self.font(size, bold)
        text = " ".join(str(text or "").split())
        if width is None or self.draw.textlength(text, font=font) <= width:
            return text
        while text and self.draw.textlength(text + "…", font=font) > width:
            text = text[:-1]
        return text.rstrip(" ,，·-") + "…"

    def wrap(self, text, size, width, max_lines, bold=True):
        """Lines that fit ``width``: words break at spaces, CJK at characters; the last line ellipsizes."""
        font = self.font(size, bold)
        tokens = re_tokens(" ".join(str(text or "").split()))
        lines, current, index = [], "", 0
        while index < len(tokens) and len(lines) < max_lines:
            candidate = current + tokens[index] if current else tokens[index].lstrip()
            if not current or self.draw.textlength(candidate, font=font) <= width:
                current = candidate
                index += 1
                continue
            lines.append(current)
            current = ""
        if current and len(lines) < max_lines:
            lines.append(current)
        lines = [self.fit(line, size, width, bold) for line in lines]
        if index < len(tokens) and lines:
            last = lines[-1].rstrip(" …")
            while last and self.draw.textlength(last + "…", font=font) > width:
                last = last[:-1]
            lines[-1] = last.rstrip(" ,，·-:") + "…"
        return lines

    def text(self, xy, text, size=14, color="ink", *, width=None, bold=True, anchor="la", stroke=None):
        text = self.fit(text, size, width, bold)
        kwargs = {}
        if stroke:
            kwargs = {"stroke_width": 2, "stroke_fill": self.color(stroke)}
        self.draw.text(xy, text, font=self.font(size, bold), fill=self.color(color), anchor=anchor, **kwargs)
        return self.draw.textlength(text, font=self.font(size, bold))

    def width(self, text, size=14, bold=True):
        return self.draw.textlength(str(text), font=self.font(size, bold))

    def rule(self, xy, color="rule", width=1):
        self.draw.line(xy, fill=self.color(color), width=width)

    def pill(self, xy, text, size=13, *, fill="ink", color="paper", outline=None, pad=8, height=22):
        x, y = xy
        width = self.width(text, size) + pad * 2
        self.draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2,
                                    fill=self.color(fill), outline=self.color(outline) if outline else None,
                                    width=2 if outline else 0)
        self.text((x + width / 2, y + height / 2), text, size, color, anchor="mm")
        return width
