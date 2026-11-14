"""The tours' own typefaces for the sports tennis panel: the ATP's Creato Display, the WTA's General Sans.

Like Steam's Motiva Sans (utils/steam_font.py) they never ship in the repository or a release archive:
the owner installs them on the device as private fonts (install/font_dlc.json keys tennis_atp_medium,
tennis_atp_bold, tennis_wta_medium and tennis_wta_semibold) under the data directory's fonts/. Without
them every call returns the panel's own font. Characters a face lacks (Chinese, and the accents the
ATP's web subset leaves out) come from the panel's font glyph by glyph, on one baseline.
"""

import os
from functools import lru_cache, partial
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


def _load(size, bold=False, *, path):
    return load_font(path, size)


@lru_cache(maxsize=8)
def _loader(path, mtime_ns):
    del mtime_ns  # Reinstallation selects a new loader, without retaining a face.
    return partial(_load, path=path)


@lru_cache(maxsize=128)
def _matched_size(path, mtime_ns, size, target_height):
    del mtime_ns
    probe = load_font(path, size)
    box = probe.getbbox("8H")
    if not probe.covers("8H") or box[3] <= box[1]:
        raise ValueError("Tennis font has no visible reference glyphs")
    # At small sizes hinting can give adjacent sizes the same cap height. Use
    # the tighter size on that plateau (e.g. General Sans 10 and 11 both have
    # 9px capitals), without making the current capitals smaller. Chinese keeps
    # its own size and baseline. Cache numbers, never the native probe faces.
    target = max(target_height, box[3] - box[1])
    candidates = []
    for actual in range(max(1, size // 2), size * 2 + 2):
        font = load_font(path, actual)
        measured = font.getbbox("8H")
        height = measured[3] - measured[1]
        candidates.append((abs(height - target), height < target, actual))
    return min(candidates)[2]


def tour_font(tour, size, bold, fallback):
    """The tour's typeface at ``size`` in the weight (missing glyphs from ``fallback``), else ``fallback``."""
    from plugins.sports_dashboard.esports_font_runs import EsportsFont
    from plugins.sports_dashboard.render_fonts import render_font

    found = private_path(tour, bold)
    if found is not None:
        try:
            target = fallback.getbbox("8H")
            actual = _matched_size(str(found[0]), found[1], int(size), target[3] - target[1])
            primary = render_font(_loader(str(found[0]), found[1]), actual, bool(bold))
            return EsportsFont(primary, fallback)
        except (OSError, ValueError, TypeError):
            pass
    return fallback
