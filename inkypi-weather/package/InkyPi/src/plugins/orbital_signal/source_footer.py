"""Official source marks, the latest space headline and a separate probability notice in the footer."""

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.local_wordmark import load_source_logo
from utils.safe_image import safe_open_image


ASSETS = Path(__file__).with_name("assets")
SOURCE_LOGOS = {"space_devs": "the-space-devs-logo.png", "polymarket": "polymarket-logo.png"}
# The news sites' own marks (assets/news, tools/build_source_logos.py) by the names the Spaceflight
# News API gives them; another site's story is led by the API's own logo and the site's name.
NEWS_SITES = {"NASA": "news-nasa", "ESA": "news-esa", "SpaceNews": "news-spacenews",
              "NASASpaceflight": "news-nasaspaceflight", "Spaceflight Now": "news-spaceflight_now",
              "Arstechnica": "news-arstechnica", "European Spaceflight": "news-european_spaceflight"}
NEWS_MARK_HEIGHT = 14
# Marks whose dark parts (ESA's navy) would vanish on the night ground take its ink there.
NEWS_DARK_PARTS = {"news-esa"}


@lru_cache(maxsize=2)
def _asset(name):
    with safe_open_image(ASSETS / SOURCE_LOGOS[name]) as source:
        image = source.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if not bounds:
        raise ValueError("Source logo is empty")
    return image.crop(bounds)


def source_logo(name, size, ink):
    """Keep TSD's original colors; Polymarket permits monochrome brand marks."""
    try:
        logo = ImageOps.contain(_asset(name), size, Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None
    if name == "polymarket":
        tinted = Image.new("RGBA", logo.size, (*ink[:3], 255))
        tinted.putalpha(logo.getchannel("A"))
        return tinted
    return logo


def _fit(draw, value, face, width):
    if draw.textlength(value, font=face) <= width:
        return value
    while value and draw.textlength(value + "…", font=face) > width:
        value = value[:-1]
    return value.rstrip(" ,.:;-") + "…"


def news_mark(site, ink):
    """The site's mark NEWS_MARK_HEIGHT tall, and whether its name still needs saying."""
    key = NEWS_SITES.get(site)
    logo = load_source_logo(ASSETS / "news" / f"{key or 'snapi'}.png", NEWS_MARK_HEIGHT, ink,
                            dark_parts=ink is not None and key in NEWS_DARK_PARTS)
    return logo, key is None


def draw_footer(image, box, palette, font, label_font, news=()):
    left, top, right, bottom = box
    draw = ImageDraw.Draw(image)
    draw.rectangle(box, fill=palette["paper"])
    draw.line((left, top, right, top), fill=palette["rule"], width=3)
    center_y = (top + 4 + bottom) / 2

    def text(x, value, face=font, color=None):
        bounds = draw.textbbox((0, 0), value, font=face)
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        draw.text((x - bounds[0], round(center_y - height / 2) - bounds[1]), value,
                  font=face, fill=color or palette["ink"])
        return x + width

    cursor = text(left + 17, "SOURCES", label_font, palette["muted"]) + 13
    tsd = source_logo("space_devs", (22, 22), palette["ink"])
    if tsd is not None:
        image.paste(tsd, (cursor, round(center_y - tsd.height / 2)), tsd)
        cursor += tsd.width + 6
    # The official TSD site pairs its round emblem with this plain-text name.
    cursor = text(cursor, "The Space Devs") + 13
    draw.line((cursor, center_y - 6, cursor, center_y + 6), fill=palette["muted"], width=1)
    cursor += 13
    poly = source_logo("polymarket", (120, 16), palette["ink"])
    if poly is not None:
        image.paste(poly, (cursor, round(center_y - poly.height / 2)), poly)
        cursor += poly.width
    else:
        cursor = text(cursor, "Polymarket")

    # This is an interpretation notice, not a third data source or brand.
    first, second = "CROWD PROBABILITY", "NOT ADVICE"
    first_box, second_box = (draw.textbbox((0, 0), value, font=font) for value in (first, second))
    first_width, second_width = first_box[2] - first_box[0], second_box[2] - second_box[0]
    note_x = right - 17 - first_width - second_width - 20
    separator_x = text(note_x, first, color=palette["muted"]) + 10
    draw.line((separator_x, center_y - 5, separator_x, center_y + 5), fill=palette["muted"], width=1)
    text(separator_x + 10, second)

    # Between them, the latest spaceflight story, led by the mark of the site that ran it.
    if news:
        story = news[0]
        cursor += 13
        draw.line((cursor, center_y - 6, cursor, center_y + 6), fill=palette["muted"], width=1)
        cursor += 13
        night = sum(palette["paper"][:3]) < 200
        mark, say_site = news_mark(story["site"], palette["ink"] if night else None)
        if mark is not None:
            image.paste(mark, (cursor, round(center_y - mark.height / 2)), mark)
            cursor += mark.width + 6
        headline = f"{story['site']}: {story['title']}" if say_site else story["title"]
        room = note_x - 16 - cursor
        if room > 40:
            text(cursor, _fit(draw, headline, font, room))
