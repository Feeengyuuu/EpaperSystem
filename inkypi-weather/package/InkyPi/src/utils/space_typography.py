"""NASA digital typography roles, measured together with the existing Chinese face.

Only requesting renderers opt into these fonts. No global Pillow patch is used.
Font lookup is weak so completed renders do not retain native FreeType objects.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from weakref import WeakValueDictionary
import re

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font

_ROOT = Path(__file__).resolve().parents[1]
_ASSETS = Path(__file__).resolve().parent / "assets/space_fonts"
_INTER = _ROOT / "plugins/flight_radar/assets/fonts"
_PATHS = {
    "inter": _INTER / "Inter-Bold.ttf", "heading": _INTER / "Inter-SemiBold.ttf",
    "public": _ASSETS / "PublicSans-Regular.otf", "body": _ASSETS / "PublicSans-Regular.otf",
    "public_semibold": _ASSETS / "PublicSans-SemiBold.otf", "body_bold": _ASSETS / "PublicSans-SemiBold.otf",
    "mono": _ASSETS / "DMMono-Medium.ttf", "telemetry": _ASSETS / "DMMono-Medium.ttf",
    "telemetry_regular": _ASSETS / "DMMono-Regular.ttf",
}
_FONTS = WeakValueDictionary()
_SCOPE = ContextVar("space_type_scope", default=False)


def space_font(role, size):
    key = (role, int(size))
    found = _FONTS.get(key)
    if found is None:
        try:
            found = load_font(_PATHS[role], int(size))
        except (OSError, ValueError):
            return get_base_ui_font(int(size), bold=role not in {"public", "body", "telemetry_regular"})
        _FONTS[key] = found
    return found


def font_role(selected):
    path = str(getattr(selected, "path", ""))
    return next((role for role, value in _PATHS.items() if str(value) == path), None)


@contextmanager
def space_type_scope():
    token = _SCOPE.set(True)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def scoped_space_font(size, bold=False):
    if _SCOPE.get():
        return space_font("inter" if size >= 16 else "public_semibold" if bold else "public", size)
    return None


class MixedDraw:
    """One measured/drawn run layout, preserving CJK and technical numeric runs."""
    def __init__(self, draw, *, numbers=False, records=None):
        self.draw = draw
        self.numbers = numbers

    def __getattr__(self, name):
        return getattr(self.draw, name)

    def runs(self, text, selected):
        text = str(text)
        if font_role(selected) is None:
            return [(text, selected, 0)]
        pieces = re.findall(r'[\u2e80-\uffff]+|[^\u2e80-\uffff]+', text)
        parts = []
        for piece in pieces:
            if any(ord(char) >= 0x2e80 for char in piece):
                parts.append((piece, get_base_ui_font(selected.size, bold=True)))
            elif self.numbers and font_role(selected) != 'mono' and (selected.size <= 13 or piece.strip().isdigit()):
                for token in re.findall(r'[+−\-]?\d[\d.,:%/\-]*|[^\d]+', piece):
                    parts.append((token, space_font('mono', selected.size) if re.search(r'\d', token) else selected))
            else:
                parts.append((piece, selected))
        ascent = selected.getmetrics()[0]
        return [(text, face, ascent - face.getmetrics()[0]) for text, face in parts]

    def textbbox(self, xy, text, font=None, **kwargs):
        if font is None or font_role(font) is None or kwargs.get('anchor'):
            return self.draw.textbbox(xy, text, font=font, **kwargs)
        x, y = xy
        boxes = []
        for value, face, shift in self.runs(text, font):
            boxes.append(self.draw.textbbox((x, y + shift), value, font=face, **kwargs))
            x += self.draw.textlength(value, font=face)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)) if boxes else (*xy, *xy)

    def textlength(self, text, font=None, **kwargs):
        return sum(self.draw.textlength(value, font=face, **kwargs) for value, face, _ in self.runs(text, font))

    def text(self, xy, text, fill=None, font=None, **kwargs):
        if font is None or font_role(font) is None or kwargs.get('anchor'):
            return self.draw.text(xy, text, fill=fill, font=font, **kwargs)
        x, y = xy
        for value, face, shift in self.runs(text, font):
            self.draw.text((x, y + shift), value, fill=fill, font=face, **kwargs)
            x += self.draw.textlength(value, font=face)


class SpaceCanvas:
    """Mixed-script runs share one measured baseline; CJK uses the existing UI font."""
    def __init__(self, canvas):
        self.base = canvas
    def __getattr__(self, name):
        return getattr(self.base, name)
    def runs(self, text, size, bold=True):
        pieces = re.findall(r"[\x20-\x7e]+|[^\x20-\x7e]+", str(text))
        output = []
        for part in pieces:
            if not part.isascii():
                font = self.base.font(size, bold)
            elif re.search(r"\d", part):
                font = space_font("telemetry" if bold else "telemetry_regular", size)
            elif size >= 14 or part.strip() == "SPACE":
                font = space_font("heading", size)
            else:
                font = space_font("body_bold" if bold else "body", size)
            output.append((part, font))
        return output
    def measure(self, text, size, bold=True):
        return sum(self.draw.textlength(t, font=f) for t, f in self.runs(text, size, bold))
    def width(self, text, size=14, bold=True):
        return self.measure(str(text), int(size), bold)
    def fit(self, text, size, width, bold=True):
        text = " ".join(str(text or "").split())
        if width is None or self.measure(text, int(size), bold) <= width:
            return text
        while text and self.measure(text + "…", int(size), bold) > width:
            text = text[:-1]
        return text.rstrip(" ,，·-") + "…"
    def text(self, xy, text, size=14, color="ink", *, width=None, bold=True, anchor="la", stroke=None):
        text = " ".join(str(text or "").split())
        initial_size = int(size)
        size = initial_size
        numeric = bool(re.search(r"\d", text))
        # Keep complete telemetry within legacy slots before using the original UI fallback.
        if width is not None and numeric:
            while self.measure(text, size, bold) > width and size > 7:
                size -= 1
        if width is not None and self.measure(text, size, bold) > width:
            if numeric:
                return self.base.text(xy, text, size=size, color=color, width=width, bold=bold, anchor=anchor, stroke=stroke)
            text = self.fit(text, size, width, bold)
        runs = self.runs(text, size, bold)
        total = sum(self.draw.textlength(t, font=f) for t, f in runs)
        x, y = xy
        x -= total / 2 if anchor[0] == "m" else total if anchor[0] == "r" else 0
        # Follow the exact original CJK font's anchor, then share its baseline across runs.
        ref = self.base.font(size, bold)
        ascent, descent = ref.getmetrics()
        v = anchor[1]
        baseline = y + ascent if v == "a" else y + (ascent - descent) / 2 if v == "m" else y
        if v == "t":
            baseline = y + ascent - ref.getbbox(text)[1]
        kwargs = {"stroke_width": 2, "stroke_fill": self.color(stroke)} if stroke else {}
        for token, font in runs:
            self.draw.text((x, baseline), token, font=font, fill=self.color(color), anchor="ls", **kwargs)
            x += self.draw.textlength(token, font=font)
        return total
