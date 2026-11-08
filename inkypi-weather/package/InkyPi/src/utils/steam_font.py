"""Steam's own typeface, Motiva Sans, for the pages about Steam (Game Deals, the Steam profile).

The fonts are Valve's brand type: they never ship in the repository or a release archive. The owner
installs them on the device as private fonts (install/font_dlc.json keys steam_regular and
steam_bold) under the data directory's fonts/; without them every call falls back to the page's UI
font. Characters Motiva Sans lacks (Chinese) come from the UI font glyph by glyph, on one baseline
(the pairing the esports pages already use).
"""

import os
from functools import lru_cache
from pathlib import Path

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font

FILES = {False: "steam-motiva-regular.ttf", True: "steam-motiva-bold.ttf"}
MAX_BYTES = 2 * 1024 * 1024


def private_path(bold=False):
    """The installed Motiva Sans file for the weight, or None."""
    data = os.environ.get("INKYPI_DATA_DIR", "").strip()
    if not data:
        return None
    path = Path(data) / "fonts" / FILES[bool(bold)]
    try:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
            return None
        return path, path.stat().st_mtime_ns
    except OSError:
        return None


@lru_cache(maxsize=64)
def _load(path, mtime_ns, size, bold):
    del mtime_ns  # part of the cache key: a reinstalled font is read again
    from plugins.sports_dashboard.esports_font_runs import EsportsFont

    return EsportsFont(load_font(path, size), get_base_ui_font(size, bold=bold))


def steam_font(size, bold=False):
    """Motiva Sans at ``size`` in the weight (Chinese from the UI font), else the UI font."""
    found = private_path(bold)
    if found is not None:
        try:
            return _load(str(found[0]), found[1], int(size), bool(bold))
        except (OSError, ValueError):
            pass
    return get_base_ui_font(size, bold=bold)
