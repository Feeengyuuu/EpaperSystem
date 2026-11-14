"""LiveRadar's readable Latin/CJK pairing, owned only by the current render."""

from pathlib import Path

from plugins.sports_dashboard.esports_font_runs import EsportsFont
from plugins.sports_dashboard.render_fonts import render_font
from utils.app_utils import get_base_ui_font
from utils.fonts import load_font


SRC = Path(__file__).resolve().parents[2]
CJK_PATH = SRC / "static/fonts/NotoSansSC-VF.ttf"
LATIN_PATHS = {
    False: SRC / "plugins/flight_radar/assets/fonts/Inter-SemiBold.ttf",
    True: SRC / "plugins/flight_radar/assets/fonts/Inter-Bold.ttf",
}


def _load(size, bold=False):
    try:
        cjk = load_font(CJK_PATH, size)
        cjk.set_variation_by_axes([600 if bold else 500])
    except (OSError, ValueError, TypeError):
        cjk = get_base_ui_font(size, bold=bold)
    try:
        latin = load_font(LATIN_PATHS[bold], size)
    except (OSError, ValueError, TypeError):
        return cjk
    return EsportsFont(latin, cjk) if hasattr(cjk, "covers") else cjk


def readable_font(size, weight="normal"):
    """Match the accepted preview: only bold labels at 14 px or more use heading weights."""
    size = int(size)
    heading = weight == "bold" and size >= 14
    return render_font(_load, size, heading)
