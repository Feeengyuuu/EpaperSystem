"""LiveRadar's original base UI face, weakly shared only during the current render."""

from plugins.sports_dashboard.render_fonts import render_font
from utils.app_utils import get_base_ui_font


def readable_font(size, weight="normal"):
    """Restore the pre-23662e14 YaHei regular/bold chain, including bold small labels."""
    return render_font(get_base_ui_font, int(size), weight == "bold")
