"""Opt-in typography for dense information pages, without changing the UI default.

Native font pairs are weakly shared; a page canvas may own its active sizes only
until that page is released. The existing glyph-safe loader keeps punctuation
and unsupported characters on the same baseline as the Latin face.
"""

from functools import lru_cache
from pathlib import Path
from threading import RLock
from weakref import WeakValueDictionary

from plugins.sports_dashboard.esports_font_runs import EsportsFont
from utils.app_utils import base_ui_font_candidates, get_base_ui_font
from utils.fonts import load_font
from utils.page_canvas import PageCanvas
from utils.steam_font import private_path as steam_private_path


SRC = Path(__file__).resolve().parents[1]
CJK_PATH = SRC / "static/fonts/NotoSansSC-VF.ttf"
INTER = SRC / "plugins/flight_radar/assets/fonts"
PUBLIC = SRC / "utils/assets/space_fonts"
LATIN_PATHS = {
    "news": (INTER / "Inter-SemiBold.ttf", INTER / "Inter-Bold.ttf"),
    "science": (PUBLIC / "PublicSans-Regular.otf", PUBLIC / "PublicSans-SemiBold.otf"),
    "instrument": (SRC / "static/fonts/SpaceGrotesk-VF.ttf",) * 2,
}
_PAIRS = WeakValueDictionary()
_LOCK = RLock()


def _identity(path):
    try:
        info = Path(path).stat()
        return str(path), info.st_mtime_ns, info.st_size
    except OSError:
        return str(path), None, None


@lru_cache(maxsize=128)
def _reference_height(identity, size, bold):
    del identity  # Reinstalled files / a different resolver invalidate scalar metrics.
    font = get_base_ui_font(size, bold=bold)
    box = font.getbbox("8H")
    return box[3] - box[1]


def _latin_face(path, size, heading, profile):
    font = load_font(path, size)
    if profile == "instrument":
        font.set_variation_by_axes([700 if heading else 600])
    return font


@lru_cache(maxsize=256)
def _matched_size(identity, size, heading, profile, target_height):
    font = _latin_face(identity[0], size, heading, profile)
    # Preserve the original visible number/capital height, including large
    # instrument readings. Nominal em sizes differ between these families.
    box = font.getbbox("8H")
    height = box[3] - box[1]
    if height <= 0:
        raise ValueError("font has no visible reference glyphs")
    return max(1, round(size * target_height / height))


def content_font(size, bold=False, *, profile="news"):
    """Pair readable Chinese with news, scientific, instrument or Steam Latin.

    Small labels use Noto 500, and headings requested bold at >=14 px use 600.
    An installed private Motiva Sans remains first choice for Steam; missing or
    unreadable files fall back through bundled Latin, CJK, then the shared UI.
    """
    size, bold = int(size), bool(bold)
    heading = bold and size >= 14
    found = steam_private_path(bold) if profile == "steam" else None
    family = "news" if profile == "steam" else profile
    paths = LATIN_PATHS[family]
    reference_id = (id(get_base_ui_font), tuple(_identity(path) for path in base_ui_font_candidates(bold)))
    latin_ids = tuple(_identity(path) for path in paths)
    key = (size, bold, family, _identity(CJK_PATH), latin_ids, found, reference_id)
    with _LOCK:
        cached = _PAIRS.get(key)
        if cached is not None:
            return cached
    try:
        cjk = load_font(CJK_PATH, size)
        cjk.set_variation_by_axes([600 if heading else 500])
    except (OSError, ValueError, TypeError):
        cjk = get_base_ui_font(size, bold=bold)
    font = cjk
    if hasattr(cjk, "covers"):
        choices = [(_identity(found[0]), "steam")] if found else []
        choices.append((latin_ids[heading], family))
        for identity, face_profile in choices:
            try:
                height = _reference_height(reference_id, size, bold)
                actual = _matched_size(identity, size, heading, face_profile, height)
                latin = _latin_face(identity[0], actual, heading, face_profile)
                font = EsportsFont(latin, cjk)
                break
            except (OSError, ValueError, TypeError):
                continue
    with _LOCK:
        if len(_PAIRS) < 128:
            try:
                _PAIRS[key] = font
            except TypeError:
                pass  # Alternate UI implementations may not support weak references.
    return font


class ContentCanvas(PageCanvas):
    """Use a page's selected content role while respecting a custom font family."""

    def __init__(self, palette, *, family="Microsoft YaHei", size=(800, 480), profile="news"):
        super().__init__(palette, family=family, size=size)
        self.profile = profile

    def font(self, size, bold=True):
        if self.family != "Microsoft YaHei":
            return super().font(size, bold)
        key = (int(size), bool(bold))
        if key not in self._fonts:
            self._fonts[key] = content_font(*key, profile=self.profile)
        return self._fonts[key]
