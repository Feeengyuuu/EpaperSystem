"""The two-market page: the China mainland realtime chart beside the North America weekly chart.

Each column keeps its own source, cache and poster repair (the North America one is the
``china_box_office_top_movies`` plugin's); this module only chooses that column's settings,
combines the two freshness verdicts and draws the page.
"""

from __future__ import annotations

from functools import lru_cache
import logging
from pathlib import Path
import re

from PIL import Image, ImageDraw

from plugins.base_plugin.render_provenance import SourceProvenance

logger = logging.getLogger(__name__)

NORTH_AMERICA_PLUGIN_ID = "china_box_office_top_movies"
NORTH_AMERICA_CACHE_HOURS = "6"
# Settings the North America column takes from the page; the rest are its own defaults.
NORTH_AMERICA_SHARED_KEYS = (
    "itemsCount", "chartUrl", "localizedLanguage", "showLocalizedTitles",
    "tmdbApiKeyEnv", "tmdbBearerTokenEnv", "tmdbApiKey", "tmdbBearerToken", "themeMode",
    "_inkypi_theme", "_theme_render_only", "_movie_media_only", "forceRefresh", "force_refresh",
)
ICON_DIR = Path(__file__).resolve().parent / "assets" / "two_markets"
BASE_SIZE = (800, 480)

# Worst first: the page is as fresh as its staler column.
_PROVENANCE_ORDER = (
    SourceProvenance.LOCAL_FALLBACK,
    SourceProvenance.STALE_CACHE,
    SourceProvenance.FRESH_CACHE,
    SourceProvenance.LIVE,
)

# Small type is pure ink (white by night): on the panel a tinted line of 10-13 px dithers to dots,
# so the column colours stay on banners, bars, icons and the large figures. By day the English
# subtitles keep their colour, which still reads on paper.
PALETTES = {
    "night": {
        "page": (11, 13, 15), "panel": (13, 15, 18), "text": (255, 255, 255), "subtitle": None,
        "rule": (66, 70, 78), "track": (56, 60, 68), "chip": (16, 18, 22), "chip_line": (62, 66, 74),
        "gold": (247, 184, 38), "gold_text": (12, 12, 12), "camera": (104, 108, 116),
        "china": {"accent": (240, 52, 66), "banner": (196, 26, 42), "tile": (142, 16, 30)},
        "america": {"accent": (58, 128, 255), "banner": (30, 92, 232), "tile": (18, 58, 168)},
    },
    "day": {
        "page": (248, 245, 238), "panel": (255, 255, 255), "text": (20, 22, 24), "subtitle": "accent",
        "rule": (196, 188, 174), "track": (222, 214, 200), "chip": (244, 239, 229), "chip_line": (200, 192, 178),
        "gold": (214, 150, 16), "gold_text": (20, 18, 12), "camera": (150, 144, 132),
        "china": {"accent": (196, 24, 40), "banner": (192, 26, 42), "tile": (140, 16, 30)},
        "america": {"accent": (26, 84, 214), "banner": (28, 88, 222), "tile": (18, 56, 160)},
    },
}
BANNER_TEXT = (255, 255, 255)


def north_america_settings(settings):
    """The North America column's settings for a page drawn from ``settings``."""
    picked = {key: settings[key] for key in NORTH_AMERICA_SHARED_KEYS if key in (settings or {})}
    return {**picked, "sourceMode": "the_numbers", "cacheHours": NORTH_AMERICA_CACHE_HOURS, "tmdbLanguage": "zh-CN"}


def cache_only(settings):
    """``settings`` that draw a column from its saved chart without fetching."""
    return {**settings, "_movie_media_only": False, "_theme_render_only": True}


def combined_provenance(china, america):
    """The page's provenance: the staler column's, and stale while North America is missing."""
    verdicts = [china.provenance, SourceProvenance.STALE_CACHE if america is None else america.provenance]
    return min(verdicts, key=_PROVENANCE_ORDER.index)


def share_value(text):
    match = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    return float(match.group(0)) if match else None


def money_value(text):
    match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*([KMB]?)", str(text or ""), re.IGNORECASE)
    if not match:
        return None
    scale = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[match.group(2).upper()]
    return float(match.group(1).replace(",", "")) * scale


def compact_money(text):
    """$30,775,377 as $30.8M; text that is not a dollar figure stays as it is."""
    value = money_value(text)
    if value is None:
        return str(text or "")
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= scale:
            return f"${value / scale:.1f}{suffix}"
    return f"${value:,.0f}"


def thousands(text):
    digits = re.sub(r"\D", "", str(text or ""))
    return f"{int(digits):,}" if digits else ""


def china_chips(movie):
    extra = movie.extra or {}
    days = str(movie.weeks or "").strip()
    return (("bars", "累计", movie.total_gross or "—"),
            ("reel", "排片", extra.get("show_count_rate") or "—"),
            ("calendar", "上映", f"{days}天" if days else "—"))


def america_chips(movie):
    # The Numbers' last column is days in release, kept in ``weeks`` like the China chart's.
    days = str(movie.weeks or "").strip()
    return (("bars", "累计", compact_money(movie.total_gross) or "—"),
            ("seats", "影院", thousands(movie.theaters) or "—"),
            ("calendar", "上映", f"{days}天" if days else "—"))


def china_total(movies):
    shares = [share_value(movie.weekend_gross) for movie in movies]
    return f"TOP{len(movies)} 合计占比 {sum(value for value in shares if value):.1f}%" if any(shares) else ""


def america_total(movies):
    grosses = [money_value(movie.weekend_gross) for movie in movies]
    if not any(grosses):
        return ""
    return f"TOP{len(movies)} 合计 {compact_money('$' + str(sum(value for value in grosses if value)))}"


@lru_cache(maxsize=64)
def _icon_mask(name, height):
    """The alpha of a generated silhouette scaled to ``height``, or None when it is missing."""
    try:
        with Image.open(ICON_DIR / f"{name}.png") as source:
            alpha = source.convert("RGBA").getchannel("A")
    except (OSError, ValueError):
        return None
    width = max(1, round(alpha.width * height / alpha.height))
    return alpha.resize((width, height), Image.Resampling.LANCZOS)


def paste_icon(canvas, name, x, y, height, color, *, max_width=None, anchor="left"):
    """Tint and paste an icon whose left edge, centre or right edge (``anchor``) is ``x``;
    the width it took (0 when missing)."""
    mask = _icon_mask(name, height)
    if mask is None:
        return 0
    if max_width and mask.width > max_width:
        mask = mask.resize((max_width, max(1, round(mask.height * max_width / mask.width))),
                           Image.Resampling.LANCZOS)
    left = x - mask.width / 2 if anchor == "center" else x - mask.width if anchor == "right" else x
    canvas.paste(Image.new("RGB", mask.size, color), (round(left), int(y + (height - mask.height) // 2)), mask)
    return mask.width


class _Page:
    """Coordinates on the 800x480 design, scaled to the panel."""

    def __init__(self, plugin, dimensions, settings):
        self.plugin = plugin
        self.width, self.height = dimensions
        self.sx, self.sy = self.width / BASE_SIZE[0], self.height / BASE_SIZE[1]
        theme = settings.get("_inkypi_theme") or {}
        self.colors = PALETTES["night" if theme.get("mode") == "night" else "day"]
        self.canvas = Image.new("RGB", dimensions, self.colors["page"])
        self.draw = ImageDraw.Draw(self.canvas)

    def x(self, value):
        return round(value * self.sx)

    def y(self, value):
        return round(value * self.sy)

    def font(self, size, bold=True):
        return self.plugin._font(max(9, round(size * min(self.sx, self.sy))), bold=bold, cjk=True)

    def text(self, xy, text, size, fill, *, bold=True, max_width=None, sizes=None):
        """Draw ``text`` at design coordinates, stepping through ``sizes`` before shortening it."""
        font = self.font(size, bold)
        if max_width is not None:
            limit = self.x(max_width)
            for step in sizes or ():
                if self.draw.textlength(text, font=font) <= limit:
                    break
                font = self.font(step, bold)
            text = self.plugin._fit_text(self.draw, text, font, limit)
        self.draw.text((self.x(xy[0]), self.y(xy[1])), text, font=font, fill=fill)
        return self.draw.textlength(text, font=font) / self.sx

    def text_right(self, right, y, text, size, fill, *, bold=True):
        font = self.font(size, bold)
        width = self.draw.textlength(text, font=font)
        self.draw.text((self.x(right) - width, self.y(y)), text, font=font, fill=fill)
        return width / self.sx

    def poster(self, movie, x, y, w, h):
        self.plugin._paste_poster(self.canvas, movie, (self.x(x), self.y(y), self.x(w), self.y(h)),
                                  {"outline": self.colors["rule"], "paper": self.colors["text"]})


def render_two_markets(plugin, dimensions, china, america, settings):
    page = _Page(plugin, dimensions, settings)
    _draw_header(page, china, america)
    _draw_column(page, 8, 394, "china", china)
    _draw_column(page, 406, 792, "america", america)
    page.draw.line((page.x(400), page.y(50), page.x(400), page.y(456)), fill=page.colors["rule"], width=1)
    _draw_footer(page, china, america)
    return page.canvas


def _draw_header(page, china, america):
    colors = page.colors
    red = colors["china"]["banner"]
    page.draw.rounded_rectangle((page.x(12), page.y(7), page.x(46), page.y(37)), radius=page.x(5), fill=red)
    paste_icon(page.canvas, "ticket", page.x(16), page.y(13), page.y(18), BANNER_TEXT, max_width=page.x(26))
    used = page.text((54, 2), "全球票房", 28, colors["text"])
    used += page.text((54 + used + 8, 2), "双城榜", 28, colors["china"]["accent"]) + 8
    paste_icon(page.canvas, "camera", page.x(770), page.y(6), page.y(30), colors["camera"], max_width=page.x(26))
    page.text_right(762, 13, _header_meta(page.plugin, china, america), 11, colors["text"])
    page.draw.line((page.x(54 + used + 16), page.y(34), page.x(762), page.y(34)), fill=colors["rule"], width=1)


def _header_meta(plugin, china, america):
    metadata = (china.movies[0].extra or {}).get("source_metadata") if china.movies else None
    source_time = plugin._parse_datetime((metadata or {}).get("source_updated_at"))
    if source_time is not None:
        from plugins.box_office_top_movies.china_source import SHANGHAI

        china_when = f"北京时间 {source_time.astimezone(SHANGHAI):%m/%d %H:%M}"
    else:
        china_when = f"{china.generated_at:%m/%d %H:%M}"
    parts = [f"{'旧数据 ' if china.stale else ''}内地 {china_when}"]
    if america is not None:
        parts.append(f"{'旧数据 ' if america.stale else ''}北美 {america.source_label} {america.generated_at:%m/%d}")
    return " · ".join(parts)


def _draw_column(page, left, right, market, snapshot):
    colors = page.colors
    roles = colors[market]
    page.draw.rounded_rectangle((page.x(left), page.y(48), page.x(right), page.y(456)), radius=page.x(8),
                                fill=colors["panel"], outline=roles["accent"], width=1)
    _draw_banner(page, left, right, market)
    if snapshot is None or not snapshot.movies:
        page.text((left + 24, 240), "北美数据暂不可用，稍后自动重试", 14, colors["text"])
        return
    movies = snapshot.movies
    _draw_hero(page, left, right, market, movies[0])
    leader = _metric_value(market, movies[0]) or 0
    for index, movie in enumerate(movies[1:5]):
        _draw_row(page, left, right, market, movie, 240 + index * 54, leader, first=index == 0)


def _draw_banner(page, left, right, market):
    roles = page.colors[market]
    page.draw.rounded_rectangle((page.x(left), page.y(48), page.x(right), page.y(80)), radius=page.x(8),
                                fill=roles["banner"])
    page.draw.rounded_rectangle((page.x(left), page.y(48), page.x(left + 44), page.y(80)), radius=page.x(8),
                                fill=roles["tile"])
    # The tile's icon sits on the tile's centre line, whatever its proportions.
    tile_centre = page.x(left + 22)
    if market == "china":
        paste_icon(page.canvas, "pagoda", tile_centre, page.y(52), page.y(25), BANNER_TEXT, max_width=page.x(34),
                   anchor="center")
        title_end = left + 54 + page.text((left + 54, 51), "中国内地 · 实时", 18, BANNER_TEXT)
        _banner_caption(page, title_end + 14, right - 12, "CHINA BOX OFFICE")
        return
    paste_icon(page.canvas, "liberty", tile_centre, page.y(51), page.y(27), BANNER_TEXT, max_width=page.x(24),
               anchor="center")
    title_end = left + 54 + page.text((left + 54, 51), "北美 · 本周", 18, BANNER_TEXT)
    # The skyline ends at the banner's right margin and the caption stops short of it.
    skyline = paste_icon(page.canvas, "skyline", page.x(right - 10), page.y(54), page.y(24), BANNER_TEXT,
                         max_width=page.x(54), anchor="right")
    _banner_caption(page, title_end + 14, right - 10 - (skyline / page.sx + 8 if skyline else 2),
                    "NORTH AMERICA BOX OFFICE")


def _banner_caption(page, start, end, caption):
    """The banner's English name, letter-spaced when it has room, right-aligned to ``end``."""
    font = page.font(9)
    for text in (" ".join(caption), caption):
        if page.draw.textlength(text, font=font) / page.sx <= end - start:
            page.text_right(end, 59, text, 9, BANNER_TEXT)
            return


def _draw_hero(page, left, right, market, movie):
    colors = page.colors
    accent = colors[market]["accent"]
    page.poster(movie, left + 8, 88, 102, 146)
    page.draw.rounded_rectangle((page.x(left + 6), page.y(86), page.x(left + 40), page.y(110)), radius=page.x(4),
                                fill=colors["gold"])
    page.text((left + 11, 85), "#1", 18, colors["gold_text"])
    x = left + 120
    width = right - 10 - x
    primary, secondary = page.plugin._display_titles(movie)
    page.text((x, 86), primary, 22, colors["text"], max_width=width, sizes=(20, 18))
    if secondary:
        page.text((x, 114), secondary, 13, _subtitle_color(colors, accent), max_width=width)
    label = "今日占比" if market == "china" else "本周票房"
    page.text((x, 136), label, 11, colors["text"])
    page.text((x, 148), movie.weekend_gross or "—", 30, accent, max_width=width, sizes=(26, 22))
    chips = china_chips(movie) if market == "china" else america_chips(movie)
    chip_w = (width - 12) / 3
    for index, (icon, chip_label, value) in enumerate(chips):
        _draw_chip(page, x + index * (chip_w + 6), 196, chip_w, icon, chip_label, value, accent)


def _draw_chip(page, x, y, w, icon, label, value, accent):
    colors = page.colors
    page.draw.rounded_rectangle((page.x(x), page.y(y), page.x(x + w), page.y(y + 36)), radius=page.x(4),
                                fill=colors["chip"], outline=colors["chip_line"], width=1)
    taken = paste_icon(page.canvas, icon, page.x(x + 5), page.y(y + 9), page.y(18), accent, max_width=page.x(20))
    text_x = x + 7 + (taken / page.sx if taken else 0) + 4
    page.text((text_x, y + 3), label, 10, colors["text"], bold=False)
    page.text((text_x, y + 16), value, 13, colors["text"], max_width=x + w - 4 - text_x, sizes=(12, 11))


def _subtitle_color(colors, accent):
    return accent if colors["subtitle"] == "accent" else colors["text"]


def _metric_value(market, movie):
    return share_value(movie.weekend_gross) if market == "china" else money_value(movie.weekend_gross)


def _draw_row(page, left, right, market, movie, top, leader, *, first):
    colors = page.colors
    accent = colors[market]["accent"]
    if not first:
        page.draw.line((page.x(left + 8), page.y(top - 3), page.x(right - 8), page.y(top - 3)),
                       fill=colors["rule"], width=1)
    page.text((left + 10, top + 12), f"#{movie.rank}", 19, colors["gold"])
    page.poster(movie, left + 44, top + 1, 34, 48)
    primary, secondary = page.plugin._display_titles(movie)
    # The Chinese title reads at 18 (1.2x the old 15) and takes a little of the figures' room.
    page.text((left + 86, top + 2), primary, 18, colors["text"], max_width=142, sizes=(17, 16, 15))
    if secondary:
        page.text((left + 86, top + 29), secondary, 10, _subtitle_color(colors, accent), max_width=142)
    metric_x = left + 232
    page.text((metric_x, top + 5), movie.weekend_gross or "—", 13, colors["text"], max_width=86)
    value = _metric_value(market, movie)
    share = max(0.0, min(1.0, value / leader)) if value and leader else 0.0
    bar = (page.x(metric_x), page.y(top + 28), page.x(metric_x + 84), page.y(top + 34))
    page.draw.rounded_rectangle(bar, radius=page.y(3), fill=colors["track"])
    if share > 0:
        filled = bar[0] + max(page.y(6), round((bar[2] - bar[0]) * share))
        page.draw.rounded_rectangle((bar[0], bar[1], filled, bar[3]), radius=page.y(3), fill=accent)
    total = movie.total_gross if market == "china" else compact_money(movie.total_gross)
    if total:
        page.text_right(right - 10, top + 17, f"累计 {total}", 10, colors["text"], bold=False)


def _draw_footer(page, china, america):
    colors = page.colors
    parts = [china_total(china.movies)]
    if america is not None:
        parts.append(america_total(america.movies))
    sources = [china.source_label] + ([america.source_label] if america is not None else [])
    parts.append("数据 " + " / ".join(dict.fromkeys(label for label in sources if label)))
    parts.append(_poster_credit(china.movies + (america.movies if america is not None else [])))
    text = " · ".join(part for part in parts if part)
    font = page.font(11, bold=False)
    text_w = page.draw.textlength(text, font=font) / page.sx
    start = (BASE_SIZE[0] - text_w) / 2
    page.text((start, 461), text, 11, colors["text"], bold=False)
    middle = page.y(469)
    for reel_x, line in ((10, (34, start - 10)), (772, (start + text_w + 10, 766))):
        paste_icon(page.canvas, "reel", page.x(reel_x), page.y(460), page.y(18), colors["camera"])
        if line[1] > line[0]:
            page.draw.line((page.x(line[0]), middle, page.x(line[1]), middle), fill=colors["rule"], width=1)


def _poster_credit(movies):
    names = {"tmdb": "TMDb", "maoyan": "猫眼", "the_numbers": "The Numbers"}
    sources = []
    for movie in movies:
        if not (movie.poster_url and movie.poster_path):
            continue
        source = str((movie.extra or {}).get("poster_source") or "").strip().lower()
        if not source:
            source = "maoyan" if "pipi.cn" in movie.poster_url else "tmdb"
        sources.append(names.get(source, "TMDb"))
    return "海报 " + " / ".join(dict.fromkeys(sources)) if sources else "海报待补"
