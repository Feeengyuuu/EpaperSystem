"""Optional UFC Sans for the fight panel, with its normal font for missing glyphs.

Original Condensed Medium/Bold WOFF2 files are private device data, never source
assets. The loader keeps only a bounded set of loader functions; font instances
are shared weakly within the sports render's existing scope.
"""

import os
from functools import lru_cache, partial
from pathlib import Path

from utils.fonts import load_font


FILES = {False: "ufc-sans-condensed-medium.woff2", True: "ufc-sans-condensed-bold.woff2"}
MAX_BYTES = 2 * 1024 * 1024


def private_path(bold=False):
    """Return the original installed font and its modification time, or None."""
    data = os.environ.get("INKYPI_DATA_DIR", "").strip()
    if not data:
        return None
    path = Path(data) / "fonts" / FILES[bool(bold)]
    try:
        if path.is_symlink() or not path.is_file():
            return None
        info = path.stat()
        return (path, info.st_mtime_ns) if 0 < info.st_size <= MAX_BYTES else None
    except OSError:
        return None


def _load(size, bold=False, *, path):
    return load_font(path, size)


@lru_cache(maxsize=8)
def _loader(path, mtime_ns):
    del mtime_ns  # New file bytes select a new loader without retaining a font.
    return partial(_load, path=path)


@lru_cache(maxsize=64)
def _matched_size(path, mtime_ns, size, target_height):
    del mtime_ns
    probe = load_font(path, size)
    box = probe.getbbox("8H")
    height = box[3] - box[1]
    if height <= 0:
        raise ValueError("UFC font has no visible reference glyphs")
    # UFC's compact em box would otherwise make the larger labels look smaller.
    # Cache only the numeric result, never the native probe font.
    return max(1, round(size * target_height / height))


def ufc_font(size, bold, fallback):
    """Use UFC Sans for supported glyphs and ``fallback`` on the same baseline."""
    from plugins.sports_dashboard.esports_font_runs import EsportsFont
    from plugins.sports_dashboard.render_fonts import render_font

    found = private_path(bold)
    if found is not None:
        try:
            target = fallback.getbbox("8H")
            actual = _matched_size(str(found[0]), found[1], int(size), target[3] - target[1])
            primary = render_font(_loader(str(found[0]), found[1]), actual, bool(bold))
            return EsportsFont(primary, fallback)
        except (OSError, ValueError, TypeError):
            pass
    return fallback
