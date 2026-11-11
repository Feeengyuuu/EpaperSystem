"""Tell a scanned page of text from a magazine cover.

An archive's "cover" is the first page of whatever was scanned: now and then a typed bulletin
or a contents page. Such a page is plain black type on white paper; a cover, even a spare
black-and-white one, carries a picture, a block of colour or lettering large enough to make
dark areas. The check is deliberately narrow, so a cover is not mistaken for text: it
needs all of the signs at once, measured on a small copy of the scan.
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageStat

# The scan is judged on a copy this size (width, height).
SAMPLE_SIZE = (120, 160)
# The dark-area test looks at the sample in cells this many pixels square.
CELL = 10
# A page of text is nearly colourless: its channels differ by less than this on average (0-255)...
MAX_MEAN_CHROMA = 10
# ...at least this share of it is blank paper (every channel at or above WHITE)...
WHITE, MIN_WHITE_SHARE = 200, 0.62
# ...hardly any of it is dark (luminance under DARK): small type greys out at this size...
DARK, MAX_DARK_SHARE = 96, 0.02
# ...and no cell is dark even in part. A masthead's display lettering fills a third of a cell or
# more (so a typographic cover stays), a picture most of one; a line of type a few hundredths.
MAX_CELL_DARK_SHARE = 0.2


def measure(image):
    """What the check goes by: (mean chroma, blank-paper share, dark share, darkest cell's dark share)."""
    sample = image.convert("RGB").resize(SAMPLE_SIZE, Image.Resampling.BOX)
    red, green, blue = sample.split()
    strongest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    weakest = ImageChops.darker(ImageChops.darker(red, green), blue)
    chroma = ImageStat.Stat(ImageChops.difference(strongest, weakest)).mean[0]
    white = weakest.point(lambda value: 255 if value >= WHITE else 0)
    dark = sample.convert("L").point(lambda value: 255 if value < DARK else 0)
    # One pixel per cell: the share of that cell that is dark.
    cells = dark.resize((SAMPLE_SIZE[0] // CELL, SAMPLE_SIZE[1] // CELL), Image.Resampling.BOX)
    return (chroma, ImageStat.Stat(white).mean[0] / 255, ImageStat.Stat(dark).mean[0] / 255,
            cells.getextrema()[1] / 255)


def looks_like_text_page(image):
    """Whether ``image`` is a page of type on white paper rather than a cover.

    False for anything with colour, a dark or mid-toned ground, or a dark area (a
    photograph, an illustration, heavy display lettering), and for anything that cannot be read.
    """
    try:
        chroma, white, dark, darkest_cell = measure(image)
    except (OSError, ValueError, AttributeError):
        return False
    return (chroma <= MAX_MEAN_CHROMA and white >= MIN_WHITE_SHARE and dark <= MAX_DARK_SHARE
            and darkest_cell < MAX_CELL_DARK_SHARE)
