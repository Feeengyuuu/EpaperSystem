"""Roadgeek sign legends with the page's existing font for unsupported glyphs."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from utils.fonts import load_font


FONT_ROOT = Path(__file__).resolve().parent / "assets" / "fonts"


@lru_cache(maxsize=32)
def _sign_font(series, size):
    try:
        return load_font(FONT_ROOT / f"RG2014{series}.ttf", size)
    except (OSError, ValueError):
        return None


def _cap_height(font):
    box = font.getbbox("H", anchor="ls")
    return box[3] - box[1]


def _fonts(canvas, size, series):
    fallback = canvas.font(size)
    wanted = _cap_height(fallback)
    # Roadgeek's nominal em is larger than its cap height; match visible letters.
    face = None
    for actual_size in range(size, size * 2 + 1):
        face = _sign_font(series, actual_size)
        if face is None or _cap_height(face) >= wanted:
            break
    return face or fallback, fallback


@dataclass
class SignLegend:
    text: str
    runs: list
    width: float
    bounds: tuple

    def draw(self, canvas, left, middle, color):
        baseline = middle - (self.bounds[1] + self.bounds[3]) / 2
        for text, font, offset in self.runs:
            canvas.draw.text((left + offset - self.bounds[0], baseline), text,
                             font=font, fill=color, anchor="ls")


def _layout(canvas, text, size, series):
    face, fallback = _fonts(canvas, size, series)
    groups = []
    for char in text:
        font = face if face is fallback or face.covers(char) else fallback
        if groups and groups[-1][1] is font:
            groups[-1] = (groups[-1][0] + char, font)
        else:
            groups.append((char, font))
    runs, advance, boxes = [], 0.0, []
    for part, font in groups:
        box = canvas.draw.textbbox((advance, 0), part, font=font, anchor="ls")
        boxes.append(box)
        runs.append((part, font, advance))
        advance += canvas.draw.textlength(part, font=font)
    if not boxes:
        return SignLegend("", [], 0, (0, 0, 0, 0))
    bounds = (min(0, min(box[0] for box in boxes)), min(box[1] for box in boxes),
              max(advance, max(box[2] for box in boxes)), max(box[3] for box in boxes))
    return SignLegend(text, runs, bounds[2] - bounds[0], bounds)


def fit_sign(canvas, text, size, max_width, max_height, *, series="EM", min_size=None):
    """Use the same mixed-font layout for bounds, limited shrinking and ellipsis."""
    text = " ".join(str(text or "").split())
    minimum = size if min_size is None else min(size, min_size)
    for chosen in range(size, minimum - 1, -1):
        legend = _layout(canvas, text, chosen, series)
        if legend.width <= max_width and legend.bounds[3] - legend.bounds[1] <= max_height:
            return legend
    while text:
        text = text[:-1].rstrip(" ,，·-")
        legend = _layout(canvas, text + "…", minimum, series)
        if legend.width <= max_width and legend.bounds[3] - legend.bounds[1] <= max_height:
            return legend
    return _layout(canvas, "", minimum, series)
