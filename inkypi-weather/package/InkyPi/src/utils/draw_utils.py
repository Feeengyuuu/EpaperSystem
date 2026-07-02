from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


def text_width(draw, text, font):
    value = str(text or "")
    if hasattr(draw, "textlength"):
        return draw.textlength(value, font=font)
    box = draw.textbbox((0, 0), value, font=font)
    return box[2] - box[0]


def fit_text(draw, text, font, max_width, *, suffix="..."):
    value = str(text or "")
    if text_width(draw, value, font) <= max_width:
        return value
    available = max(0, max_width - text_width(draw, suffix, font))
    clipped = value
    while clipped and text_width(draw, clipped, font) > available:
        clipped = clipped[:-1]
    return f"{clipped.rstrip()}{suffix}" if clipped else suffix


def draw_centered(draw, text, x, y, width, font, fill, **kwargs):
    value = str(text or "")
    draw.text((x + (width - text_width(draw, value, font)) / 2, y), value, font=font, fill=fill, **kwargs)


@lru_cache(maxsize=128)
def load_font_from_paths(size, paths=(), fallback=True):
    for path in paths or ():
        try:
            target = Path(path)
            if target.is_file():
                return ImageFont.truetype(str(target), int(size))
        except Exception:
            continue
    if fallback:
        return ImageFont.load_default()
    raise OSError("No configured font path could be loaded")