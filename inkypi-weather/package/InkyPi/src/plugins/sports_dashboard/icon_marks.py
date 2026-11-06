"""Icon marks for the sports page's leaderboards: a place as a medal-style disc (gold, silver and
bronze for the first three, ink on the paper below them, a tie marked with a small "T") and a
nationality as its flag, framed by a hairline so a white flag still reads on the paper.
"""

from PIL import ImageChops

from .common import *

SportsDashboard = None

ESPN_FLAG_URL = "https://a.espncdn.com/i/teamlogos/countries/500/{code}.png"
FLAGCDN_URL = "https://flagcdn.com/w160/{iso}.png"
# Metallic medal fills: gold, silver, bronze (refined tones, not the panel's pure inks).
MEDAL_FILLS = {1: (212, 160, 23), 2: (168, 176, 186), 3: (176, 112, 60)}


def espn_flag_url(code):
    """ESPN's flag for a three-letter country code (USA, CAN, IRL...), or ""."""
    code = str(code or "").strip().lower()
    return ESPN_FLAG_URL.format(code=code) if len(code) == 3 and code.isalpha() else ""


def iso2_flag_url(iso):
    """A flag for a two-letter ISO code (ES, IT...), or ""."""
    iso = str(iso or "").strip().lower()
    return FLAGCDN_URL.format(iso=iso) if len(iso) == 2 and iso.isalpha() else ""


def night_inked(mark):
    """A logo for the current ground: on a dark page its dark strokes (black, navy, deep purple)
    take the page's light ink, its coloured parts stay as printed."""
    if sum(COLORS["panel"]) >= 384:
        return mark
    mark = mark.convert("RGBA")
    alpha = mark.getchannel("A")
    dark = ImageChops.multiply(mark.convert("L").point(lambda value: 255 if value < 90 else 0),
                               alpha.point(lambda value: 255 if value else 0))
    ink = Image.new("RGBA", mark.size, (*COLORS["text"], 255))
    ink.putalpha(alpha)
    return Image.composite(ink, mark, dark)


def place_parts(label, position=None):
    """(place number, tied) from a leaderboard label ("T3", "P1", "3") or a position."""
    text = str(label or "").strip().upper()
    tied = text.startswith("T")
    digits = "".join(char for char in text if char.isdigit())
    number = int(digits) if digits else (position if isinstance(position, int) else None)
    return number, tied


class IconMarksMixin:
    @staticmethod
    def _night_inked_mark(mark):
        return night_inked(mark)

    def _draw_rank_medal(self, draw, box, number, tied=False, medal=True):
        """A place as a disc in ``box`` (square): medal colours for 1-3, else ink outline on the
        panel; a tie adds a small "T" before the number."""
        x1, y1, x2, y2 = [int(value) for value in box]
        size = min(x2 - x1, y2 - y1)
        left, top = x1 + (x2 - x1 - size) // 2, y1 + (y2 - y1 - size) // 2
        disc = (left, top, left + size, top + size)
        fill = MEDAL_FILLS.get(number) if medal and isinstance(number, int) else None
        draw.ellipse(disc, fill=fill or COLORS["panel"], outline=COLORS["border"], width=2 if fill else 1)
        if fill:
            # A thin inner ring gives the disc its struck-medal edge.
            inset = max(2, size // 7)
            draw.ellipse((disc[0] + inset, disc[1] + inset, disc[2] - inset, disc[3] - inset),
                         outline=self._blend(fill, (255, 255, 255), 0.45), width=1)
        text = f"T{number}" if tied and number is not None else (str(number) if number is not None else "-")
        font_size = max(7, int(size * (0.5 if len(text) <= 1 else 0.42 if len(text) == 2 else 0.34)))
        self._draw_centered_in_box(draw, disc, text, self._font(font_size, True), (8, 8, 8) if fill else COLORS["text"])

    def _load_icon_flag(self, url, size):
        """The flag at ``url`` trimmed and contained in ``size``, its file kept in the team-logo disk
        cache (white fields stay white, unlike a logo's), so a later process reads it from disk."""
        size = (max(1, int(size[0])), max(1, int(size[1])))
        key = (url, size, "icon-flag-v1")
        if key in FLAG_IMAGE_CACHE:
            return FLAG_IMAGE_CACHE[key]
        flag = None
        try:
            path = SportsDashboard._team_logo_disk_cache_path(self._team_logo_disk_cache_dir(), f"flag:{url}")
            data = SportsDashboard._read_team_logo_disk_cache(path, validate_image=False)
            if data is None:
                data = SportsDashboard._fetch_remote_image_bytes(url, 4)
                SportsDashboard._write_team_logo_disk_cache(path, data)
            source = safe_open_image(data, limits=TEAM_LOGO_IMAGE_LIMITS)
            try:
                flag = ImageOps.contain(SportsDashboard._trim_transparent_flag(source.convert("RGBA")), size, Image.LANCZOS)
            finally:
                source.close()
        except Exception as exc:  # noqa: BLE001 - a missing flag leaves its country code instead.
            logger.warning("Flag icon unavailable %s: %s", url, type(exc).__name__)
        FLAG_IMAGE_CACHE[key] = flag
        return flag

    def _draw_flag_icon(self, image, draw, url, box, fallback=""):
        """The flag at ``url`` contained in ``box`` with a hairline frame; the code as small text
        when there is no flag. Returns the drawn box, or None."""
        x1, y1, x2, y2 = [int(value) for value in box]
        flag = self._load_icon_flag(url, (x2 - x1, y2 - y1)) if url else None
        if flag is None:
            if fallback:
                text, font = self._fit_text(draw, str(fallback)[:3].upper(), x2 - x1, 8, bold=True, min_size=6)
                self._draw_centered_in_box(draw, (x1, y1, x2, y2), text, font, COLORS["muted"])
            return None
        left = x1 + (x2 - x1 - flag.width) // 2
        top = y1 + (y2 - y1 - flag.height) // 2
        image.paste(flag, (left, top), flag)
        draw.rectangle((left - 1, top - 1, left + flag.width, top + flag.height), outline=COLORS["line"], width=1)
        return (left, top, left + flag.width, top + flag.height)
