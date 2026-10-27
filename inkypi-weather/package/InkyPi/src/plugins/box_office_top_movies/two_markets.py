"""The two-market page: the China mainland realtime chart beside the North America weekly chart.

Each column keeps its own source, cache and poster repair (the North America one is the
``china_box_office_top_movies`` plugin's); this module only chooses that column's settings,
combines the two freshness verdicts and draws the page.
"""

from __future__ import annotations

from functools import lru_cache
import logging
import math
from pathlib import Path
import re

from PIL import Image, ImageDraw

from plugins.base_plugin.render_provenance import SourceProvenance
from utils.local_wordmark import load_source_logo, paste_local_wordmark

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
# The footer names its sources by their logos (assets/logos, tools/build_source_logos.py):
# source label -> (logo, its height, text kept beside it), each as tall as its lettering reads
# (Maoyan's name in small CJK type stands tallest). The official chart's mark is its site's icon,
# so its name stays beside it; a label without a logo stays in type.
LOGO_DIR = Path(__file__).resolve().parent / "assets" / "logos"
SOURCE_LOGOS = {"中国电影票房": ("zgdypw", 13, "中国电影票房"), "猫眼专业版": ("maoyan", 15, ""),
                "猫眼": ("maoyan", 15, ""), "The Numbers": ("the_numbers", 11, ""), "TMDb": ("tmdb", 10, "")}
SOURCE_LOGO_GAP = "  "

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


def china_today_gross(movie):
    """The official feed's amount is in ten-thousand yuan, not a percentage."""
    raw = str((movie.extra or {}).get("today_box_wan") or "").replace(",", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return None
    value = float(raw)
    if not math.isfinite(value):
        return None
    return f"{value / 10000:.2f}亿" if value >= 10000 else f"{value:,.2f}万"


def theater_average(movie):
    """Use a source-verified cinema count; old heuristic caches remain readable."""
    extra = movie.extra or {}
    if not extra.get("theaters_verified"):
        return None
    count = str(movie.theaters or "").replace(",", "").strip()
    gross = money_value(movie.weekend_gross)
    if not count.isdigit() or int(count) <= 0 or gross is None or not math.isfinite(gross):
        return None
    reported = money_value(extra.get("theater_average"))
    value = reported if reported is not None and math.isfinite(reported) else gross / int(count)
    return f"${value:,.0f}"


def hero_details(market, movie, runner_up=None):
    facts = list(china_chips(movie) if market == "china" else america_chips(movie))
    if market == "china" and runner_up is not None:
        first, second = share_value(movie.weekend_gross), share_value(runner_up.weekend_gross)
        if first is not None and second is not None and 0 <= second < first <= 100:
            facts.append(("bars", "领先#2", f"+{first - second:.2f}pp"))
    elif market == "america":
        average = theater_average(movie)
        if average:
            facts.append(("bars", "单馆", average))
    return [fact for fact in facts if fact[2] not in ("", "—")]


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
    # The page's title is its wordmark (white or ink lettering for the theme), else set in type.
    theme = "night" if colors is PALETTES["night"] else "day"
    title = paste_local_wordmark(page.canvas, ICON_DIR / f"title_wordmark_{theme}.png",
                                 (page.x(12), page.y(3), page.x(320), page.y(37)), align="left")
    if title:
        title_end = title[2] / page.sx
    else:
        red = colors["china"]["banner"]
        page.draw.rounded_rectangle((page.x(12), page.y(7), page.x(46), page.y(37)), radius=page.x(5), fill=red)
        paste_icon(page.canvas, "ticket", page.x(16), page.y(13), page.y(18), BANNER_TEXT, max_width=page.x(26))
        used = page.text((54, 2), "全球票房", 28, colors["text"])
        used += page.text((54 + used + 8, 2), "双城榜", 28, colors["china"]["accent"]) + 8
        title_end = 54 + used
    paste_icon(page.canvas, "camera", page.x(770), page.y(6), page.y(30), colors["camera"], max_width=page.x(26))
    page.text_right(762, 13, _header_meta(page.plugin, china, america), 11, colors["text"])
    page.draw.line((page.x(title_end + 16), page.y(34), page.x(762), page.y(34)), fill=colors["rule"], width=1)


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
    _draw_hero(page, left, right, market, movies[0], movies[1] if len(movies) > 1 else None)
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
    title_end = left + 54 + page.text((left + 54, 51), "北美 · 本期", 18, BANNER_TEXT)
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


def _draw_hero(page, left, right, market, movie, runner_up=None):
    colors = page.colors
    accent = colors[market]["accent"]
    page.poster(movie, left + 8, 88, 102, 146)
    page.draw.rounded_rectangle((page.x(left + 6), page.y(86), page.x(left + 40), page.y(110)), radius=page.x(4),
                                fill=colors["gold"])
    page.text((left + 11, 85), "#1", 18, colors["gold_text"])
    x = left + 120
    width = right - 10 - x
    primary, secondary = page.plugin._display_titles(movie)
    page.text((x, 82), primary, 26, colors["text"], max_width=width, sizes=(24, 22, 20))
    if secondary:
        page.text((x, 114), secondary, 12, _subtitle_color(colors, accent), max_width=width)
    label = "今日占比" if market == "china" else "本期票房"
    today = china_today_gross(movie) if market == "china" else None
    metric_width = 140 if today else width
    page.text((x, 133), label, 11, colors["text"])
    page.text((x, 146), movie.weekend_gross or "—", 30, accent, max_width=metric_width,
              sizes=(28, 26, 24, 22, 20, 18))
    if today:
        amount_x = x + 148
        page.text((amount_x, 133), "今日票房", 11, colors["text"])
        page.text((amount_x, 150), today, 21, colors["text"], max_width=width - 148, sizes=(19, 17, 15))
    _draw_hero_details(page, x, width, hero_details(market, movie, runner_up), accent)


def _draw_hero_details(page, x, width, facts, accent):
    """Two aligned rows use the card's full width without tiny nested boxes."""
    colors = page.colors
    page.draw.line((page.x(x), page.y(183), page.x(x + width), page.y(183)), fill=colors["rule"], width=1)
    cell_width = (width - 12) / 2
    for index, (icon, label, value) in enumerate(facts[:4]):
        left = x + (index % 2) * (cell_width + 12)
        top = 188 + (index // 2) * 25
        paste_icon(page.canvas, icon, page.x(left), page.y(top + 1), page.y(13), accent, max_width=page.x(14))
        label_width = page.text((left + 18, top), label, 10, colors["text"], bold=False)
        value_width = cell_width - 18 - label_width - 5
        font = page.font(13)
        for size in (12, 11):
            if page.draw.textlength(value, font=font) <= page.x(value_width):
                break
            font = page.font(size)
        value = page.plugin._fit_text(page.draw, value, font, page.x(value_width))
        page.draw.text((page.x(left + cell_width) - page.draw.textlength(value, font=font), page.y(top - 1)),
                       value, font=font, fill=colors["text"])


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
    runs = []
    totals = [china_total(china.movies)] + ([america_total(america.movies)] if america is not None else [])
    for total in (total for total in totals if total):
        runs += [total, " · "]
    sources = [china.source_label] + ([america.source_label] if america is not None else [])
    runs += ["数据 "] + _source_runs(page, dict.fromkeys(label for label in sources if label)) + [" · "]
    posters = _poster_sources(china.movies + (america.movies if america is not None else []))
    runs += ["海报 "] + _source_runs(page, posters) if posters else ["海报待补"]
    font = page.font(11, bold=False)
    widths = [page.draw.textlength(run, font=font) / page.sx if isinstance(run, str) else run.width / page.sx
              for run in runs]
    start = (BASE_SIZE[0] - sum(widths)) / 2
    middle = page.y(469)
    x = start
    for run, width in zip(runs, widths):
        if isinstance(run, str):
            page.text((x, 461), run, 11, colors["text"], bold=False)
        else:
            page.canvas.paste(run, (page.x(x), round(middle - run.height / 2)), run)
        x += width
    for reel_x, line in ((10, (34, start - 10)), (772, (x + 10, 766))):
        paste_icon(page.canvas, "reel", page.x(reel_x), page.y(460), page.y(18), colors["camera"])
        if line[1] > line[0]:
            page.draw.line((page.x(line[0]), middle, page.x(line[1]), middle), fill=colors["rule"], width=1)


def _source_runs(page, labels):
    """The sources as runs of the footer: each one's logo (the official chart's mark keeps its
    name beside it), else its name; a little space between logos, " / " beside a name."""
    ink = page.colors["text"] if page.colors is PALETTES["night"] else None
    runs = []
    for label in labels:
        key, height, beside = SOURCE_LOGOS.get(label, (None, 0, ""))
        logo = load_source_logo(LOGO_DIR / f"{key}.png", page.y(height), ink) if key else None
        if runs:
            runs.append(SOURCE_LOGO_GAP if logo is not None and isinstance(runs[-1], Image.Image) else " / ")
        runs += [logo, " " + beside] if logo is not None and beside else [logo if logo is not None else label]
    return runs


def _poster_sources(movies):
    names = {"tmdb": "TMDb", "maoyan": "猫眼", "the_numbers": "The Numbers"}
    sources = []
    for movie in movies:
        if not (movie.poster_url and movie.poster_path):
            continue
        source = str((movie.extra or {}).get("poster_source") or "").strip().lower()
        if not source:
            source = "maoyan" if "pipi.cn" in movie.poster_url else "tmdb"
        sources.append(names.get(source, "TMDb"))
    return list(dict.fromkeys(sources))
