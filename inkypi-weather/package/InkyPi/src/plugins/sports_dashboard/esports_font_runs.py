"""Pillow-compatible, baseline-aligned Latin type with per-glyph UI fallback."""

import math

from PIL import Image, ImageDraw


class EsportsFont:
    """Keep unsupported original glyphs in the UI face before normalisation."""

    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback
        self.path, self.size = primary.path, fallback.size

    def covers(self, text):
        return all(self.primary.covers(c) or self.fallback.covers(c) for c in str(text))

    def safe_text(self, text):
        return "".join(c if self.primary.covers(c) else self.fallback.safe_text(c) for c in str(text))

    def _runs(self, text):
        runs = []
        for char in str(text):
            font = self.primary if self.primary.covers(char) else self.fallback
            if runs and runs[-1][0] is font:
                runs[-1] = (font, runs[-1][1] + char)
            else:
                runs.append((font, char))
        return runs

    def getmetrics(self):
        return self.fallback.getmetrics()

    def getlength(self, text, mode="", direction=None, features=None, language=None):
        return sum(font.getlength(run) for font, run in self._runs(text))

    def _layout(self, text, stroke_width=0, anchor=None):
        runs, offset, boxes = [], 0, []
        for font, run in self._runs(text):
            box = font.getbbox(run, stroke_width=stroke_width, anchor="ls")
            boxes.append((offset + box[0], box[1], offset + box[2], box[3]))
            runs.append((font, run, offset))
            offset += font.getlength(run)
        if not boxes:
            return [], (0, 0, 0, 0), (0, 0)
        bounds = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                  max(b[2] for b in boxes), max(b[3] for b in boxes))
        anchor = anchor or "la"
        ascent, descent = self.getmetrics()
        x = {"l": 0, "m": -offset / 2, "r": -offset}.get(anchor[0], 0)
        y = {"a": ascent, "s": 0, "d": -descent, "t": -bounds[1],
             "b": -bounds[3], "m": (ascent - descent) / 2}.get(anchor[1], ascent)
        box = (math.floor(bounds[0] + x), math.floor(bounds[1] + y),
               math.ceil(bounds[2] + x), math.ceil(bounds[3] + y))
        return runs, box, (x, y)

    def getbbox(self, text, mode="", direction=None, features=None, language=None,
                stroke_width=0, anchor=None):
        return self._layout(text, stroke_width, anchor)[1]

    def getmask2(self, text, mode="", direction=None, features=None, language=None,
                 stroke_width=0, anchor=None, ink=0, start=None, *args, **kwargs):
        runs, box, origin = self._layout(text, stroke_width, anchor)
        mask = Image.new("L", (max(0, box[2] - box[0]), max(0, box[3] - box[1])))
        draw = ImageDraw.Draw(mask)
        for font, run, offset in runs:
            draw.text((origin[0] + offset - box[0], origin[1] - box[1]), run,
                      font=font, fill=255, anchor="ls", stroke_width=stroke_width)
        return mask.im, box[:2]

    def getmask(self, text, mode="", *args, **kwargs):
        return self.getmask2(text, mode, *args, **kwargs)[0]
