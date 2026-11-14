"""Local generated team-livery schematics, not official photographs or rider portraits."""

from functools import lru_cache
import json
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFilter, ImageOps


ASSETS = Path(__file__).resolve().parent / "assets" / "motogp_bikes"


def _key(value):
    return re.sub(r"[^\w]", "", str(value or "").casefold())


@lru_cache(maxsize=4)
def _read_manifest(path, modified):
    try:
        value = Path(path).read_text(encoding="utf-8")
        result = json.loads(value)
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) and result.get("kind") == "generated_livery_schematic" else {}


def _manifest():
    path = ASSETS / "sources.json"
    try:
        return _read_manifest(str(path), path.stat().st_mtime_ns)
    except OSError:
        return {}


def _asset(entry):
    filename = entry.get("file") if isinstance(entry, dict) else entry
    if not isinstance(filename, str) or Path(filename).name != filename or Path(filename).suffix.lower() != ".png":
        return None
    try:
        with Image.open(ASSETS / filename) as source:
            if "A" not in source.getbands():
                return None
            art = source.convert("RGBA")
            if art.getchannel("A").getextrema()[0] != 0:
                return None
            bounds = art.getchannel("A").getbbox()
            return art.crop(bounds) if bounds else None
    except (OSError, ValueError):
        return None


def _neutral():
    """A colour-neutral side silhouette remains available even if local artwork is absent."""
    art = Image.new("RGBA", (64, 28))
    draw = ImageDraw.Draw(art)
    for x in (9, 53):
        draw.ellipse((x - 7, 13, x + 7, 27), fill=(74, 74, 74, 255))
        draw.ellipse((x - 3, 17, x + 3, 23), fill=(170, 170, 170, 255))
    draw.polygon(((9, 18), (18, 10), (28, 6), (39, 8), (46, 3), (50, 5),
                  (44, 11), (51, 17), (54, 20), (33, 22), (17, 20)), fill=(132, 132, 132, 255))
    draw.line((13, 8, 24, 9), fill=(84, 84, 84, 255), width=3)
    return art


def load_bike(row, season, size, panel):
    """The season's exact team/rider mapping, otherwise a neutral bike; never guess a team.

    Rider overrides use that season's bike number. Sponsor variants such as the two LCR
    liveries stay distinct. No season match means no inherited previous-season colours.
    """
    manifest = _manifest()
    seasons = manifest.get("seasons") or {}
    year = seasons.get(str(season), {}) if isinstance(seasons, dict) and type(season) is int else {}
    year = year if isinstance(year, dict) else {}
    overrides = year.get("riders") or {}
    entry = overrides.get(str(row.get("number"))) if isinstance(overrides, dict) else None
    art = _asset(entry)
    if art is None:
        teams = year.get("teams") or {}
        team = _key(row.get("team"))
        if team and isinstance(teams, dict):
            for key, value in teams.items():
                if not isinstance(value, dict):
                    continue
                aliases = value.get("aliases") or []
                if isinstance(aliases, list) and team in {_key(name) for name in [key, *aliases]}:
                    art = _asset(value)
                    break
    if art is None:
        art = _asset(manifest.get("fallback", "neutral.png")) or _neutral()
    width, height = (max(3, int(value)) for value in size)
    art = ImageOps.contain(art, (width - 2, height - 2), Image.Resampling.LANCZOS)
    framed = Image.new("RGBA", (art.width + 2, art.height + 2))
    framed.paste(art, (1, 1))
    # A subtle edge keeps tyres visible on a dark page and pale fairings on a light one.
    ink = (195, 195, 195) if sum(panel[:3]) < 384 else (55, 55, 55)
    edge = Image.new("RGBA", framed.size, (*ink, 0))
    edge.putalpha(framed.getchannel("A").filter(ImageFilter.MaxFilter(3)).point(lambda value: round(value * 0.6)))
    return Image.alpha_composite(edge, framed)
