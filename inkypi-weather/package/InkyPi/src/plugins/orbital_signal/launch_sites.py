"""Offline launch-site identities, distinct from the rocket provider's logo.

The range/centre's own mark takes priority. Where it has no verified compact
mark, a documented site operator identifies it; unknown places keep the pin.
Asset provenance and the scope of each operator mark live in assets/sites/source.json.
"""

from functools import lru_cache
from pathlib import Path
import re
import unicodedata

from PIL import Image, ImageOps

from utils.picture_grounds import readable_on
from utils.safe_image import safe_open_image


ASSET_DIR = Path(__file__).with_name("assets") / "sites"
MARK_HEIGHT, MARK_MAX_WIDTH, MARK_GAP = 24, 48, 6
# Match the location supplied by Launch Library, never the rocket provider.
# Country codes guard ambiguous names; a pad can only narrow a matched location.
SITE_IDENTITIES = (
    ("vandenberg", ("US",), r"\bvandenberg\b"),
    ("cape_canaveral", ("US",), r"\bcape canaveral\b"),
    ("nasa", ("US",), r"\b(?:kennedy space cent(?:er|re)|wallops(?: island| flight facility)?)\b"),
    ("rocketlab", ("NZ",), r"\b(?:mahia(?: peninsula)?|rocket lab launch complex 1)\b"),
    ("csg", ("GF", "FR"), r"\b(?:kourou|guiana space cent(?:er|re)|centre spatial guyanais)\b"),
    ("isro", ("IN",), r"\b(?:satish dhawan|sriharikota|sdsc shar)\b"),
    ("jaxa", ("JP",), r"\b(?:tanegashima|uchinoura)\b"),
    ("tsenki", ("KZ",), r"\bbaikonur\b"),
    ("tsenki", ("RU",), r"\bvostochn(?:y|yy)\b"),
    ("pla_asf", ("CN",), r"\b(?:taiyuan|jiuquan|xichang|wenchang)\b|"
     r"(?:太原|酒泉|西昌|文昌)(?:(?:卫星|航天)?发射(?:中心|场))?(?:[,，\s]|$)"),
)
_MATCHERS = tuple((key, countries, re.compile(pattern)) for key, countries, pattern in SITE_IDENTITIES)
_ADAPTIVE_MARKS = frozenset({"rocketlab", "isro", "jaxa"})
_WENCHANG = re.compile(r"\bwenchang\b|文昌")
_WENCHANG_STATE_PAD = re.compile(r"(?:(?:lc|launch\s+(?:complex|pad))[\s-]*)?(?:101|201|301)", re.I)


def site_mark_key(launch):
    """The verified site/operator identity, or None for an unknown location."""
    location = unicodedata.normalize("NFKD", str(launch.get("location") or ""))
    location = " ".join("".join(char for char in location if not unicodedata.combining(char)).casefold().split())
    country = str(launch.get("country_code") or "").strip().upper()
    for key, countries, pattern in _MATCHERS:
        if (not country or country in countries) and pattern.search(location):
            # Launch Library groups the separate Hainan commercial launch site
            # under the same Wenchang location. Only positively identified state
            # pads may carry the military operator mark; unknown pads keep the pin.
            if key == "pla_asf" and _WENCHANG.search(location):
                pad = str(launch.get("pad") or "").strip()
                if "commercial" in location or "商业" in location or not _WENCHANG_STATE_PAD.fullmatch(pad):
                    return None
            return key
    return None


@lru_cache(maxsize=16)
def _source_mark(key):
    # Only registry keys can name a file; upstream strings never become paths.
    if key not in {record[0] for record in SITE_IDENTITIES}:
        return None
    path = ASSET_DIR.parent / "providers" / "31.png" if key == "isro" else ASSET_DIR / f"{key}.png"
    try:
        with safe_open_image(path) as source:
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


def site_mark(launch, palette):
    """A small fitted copy ready to paste in the location row; missing art falls back."""
    key = site_mark_key(launch)
    source = _source_mark(key) if key else None
    if source is None:
        return None
    fitted = ImageOps.contain(source, (MARK_MAX_WIDTH, MARK_HEIGHT), Image.Resampling.LANCZOS)
    # Preserve shields and full-colour insignia. Adapt only low-contrast strokes
    # in the simpler marks (e.g. Rocket Lab's black R on the night background).
    return readable_on(fitted, palette["paper"], palette["ink"]) if key in _ADAPTIVE_MARKS else fitted
