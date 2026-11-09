"""The tours' own typefaces for the sports tennis panel: the ATP's Creato Display, the WTA's General Sans.

Like Steam's Motiva Sans (utils/steam_font.py) they never ship in the repository or a release archive:
the owner installs them on the device as private fonts (install/font_dlc.json keys tennis_atp_medium,
tennis_atp_bold, tennis_wta_medium and tennis_wta_semibold) under the data directory's fonts/. Without
them every call returns the panel's own font. Characters a face lacks (Chinese, and the accents the
ATP's web subset leaves out) come from the panel's font glyph by glyph, on one baseline.
"""

import os
from functools import lru_cache
from pathlib import Path

from utils.fonts import load_font

FILES = {
    ("ATP", False): "tennis-atp-medium.ttf",
    ("ATP", True): "tennis-atp-bold.ttf",
    ("WTA", False): "tennis-wta-medium.ttf",
    ("WTA", True): "tennis-wta-semibold.ttf",
}
MAX_BYTES = 2 * 1024 * 1024


def private_path(tour, bold=False):
    """The installed file of the tour's typeface in the weight, with its mtime, or None."""
    name = FILES.get((str(tour or "").strip().upper(), bool(bold)))
    data = os.environ.get("INKYPI_DATA_DIR", "").strip()
    if not name or not data:
        return None
    path = Path(data) / "fonts" / name
    try:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
            return None
        return path, path.stat().st_mtime_ns
    except OSError:
        return None


@lru_cache(maxsize=64)
def _load(path, mtime_ns, size, fallback):
    del mtime_ns  # part of the cache key: a reinstalled font is read again
    from plugins.sports_dashboard.esports_font_runs import EsportsFont

    return EsportsFont(load_font(path, size), fallback)


def tour_font(tour, size, bold, fallback):
    """The tour's typeface at ``size`` in the weight (missing glyphs from ``fallback``), else ``fallback``."""
    found = private_path(tour, bold)
    if found is not None:
        try:
            return _load(str(found[0]), found[1], int(size), fallback)
        except (OSError, ValueError, TypeError):
            pass
    return fallback
