"""800 x 480 AI 论文日报 page.

Left: the most upvoted paper leads with its figure down the card's full height, then the
organisation behind it (its Hub avatar, its logo), its title and one-sentence summary, its
keywords, its most liked model on the Hub, and its upvotes, comments, GitHub stars and arXiv id. The
next four stand in a 2 x 2 grid of cards beneath, each with its figure filling the card's left side.
Right: the rest of the ranking, each paper's title over its organisation and its upvotes. A paper
whose authors link its code or a project page carries 有代码 / 项目页 chips. The header names the
sources by their logos: Hugging Face Papers and arXiv.

Figures are shown as large as their boxes allow. A photo fills its box, cropped to it. A diagram
drawn on white loses its empty margins, is cut to its box's shape only so far (most of it stays)
and is blended into the card's ground. A paper without either shows a generated placeholder. The
organisations' avatars lose their flat tiles. The cards carry a quiet doodle of research themes.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.flat_icons import draw_icon
from utils.local_wordmark import load_source_logo, paste_local_wordmark
from utils.page_canvas import PageCanvas
from utils.picture_grounds import (
    blend_drawing, cut_flat_ground, drawn_on_white, light_on_dark, readable_on, trim_white,
)
from utils.theme_utils import contrast_ratio, relative_luminance


ASSETS = Path(__file__).resolve().parent / "assets"
# A paper's links in their own colours, on solid chips: its code cobalt, its project page emerald.
DAY = {"paper": "#ffffff", "ink": "#121a20", "muted": "#3a454d", "rule": "#a6a6a6", "track": "#eceef1",
       "lead": "#fff8dc", "code": "#2554c7", "project": "#059669"}
NIGHT = {"paper": "#000000", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a", "track": "#24282d",
         "lead": "#1c1704", "code": "#7aa2ff", "project": "#34d399"}
LINKS = (("code", "有代码"), ("project", "项目页"))
LINK_MARK = 7
# Hugging Face's yellow and orange (its brand), for the solid marks: rank badges and upvote arrows.
HF_YELLOW = (0xFF, 0xD2, 0x1E)
HF_ORANGE = (0xFF, 0x9D, 0x00)
WEEKDAYS = "一二三四五六日"
LEAD = (12, 60, 520, 268)
# The lead's figure runs the card's full height, as wide as its shape asks within these bounds.
LEAD_FIGURE_WIDTH = (184, 272)
GRID = (12, 276, 520, 470)
GRID_GAP = 8
RANKING = (528, 60, 788, 470)
# Six rows leave each its title's two lines, a clear gap, then its organisation and upvotes.
RANKING_ROWS = 6
RANKING_LINE = 17
RANKING_GAP = 6
RANKING_MARK = 14
# A card's figure fills the card's full height down its left side, this wide.
CARD_FIGURE_WIDTH = 124
# A diagram is cut to its box's shape only so far: at least this share of its width (or height)
# stays, the lead's more than a card's. It keeps this far from its box's edges.
LEAD_DIAGRAM_KEPT = 0.75
CARD_DIAGRAM_KEPT = 0.6
DIAGRAM_INSET = 6
AVATAR = 18
# A mark at least this much wider than tall spells its organisation's name; one may run to this
# many times its height.
WORDMARK_RATIO = 2.2
WORDMARK_WIDTH = 4
AVATAR_CORNER = 4
# By night a mark drawn in a colour no channel of which reaches this (a navy, a dark purple) turns light.
DARK_MARK_VALUE = 160
LEAD_SUMMARY_LINES = 4
KEYWORDS = 3
DOODLES = ASSETS / "doodles" / "papers.png"
DOODLE_STRENGTH = {"day": 0.2, "night": 0.3}


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", (settings or {}).get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def _night(canvas):
    return sum(canvas.color("paper")) < 200


def count_text(value):
    """1520 -> "1.5k", 75 -> "75"."""
    value = int(value or 0)
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def shown_text(paper, chinese):
    """The paper's title and summary as the page prints them: Chinese when translated."""
    translated = (chinese or {}).get(paper["id"]) or {}
    return translated.get("title") or paper["title"], translated.get("summary") or paper["summary"], bool(translated)


def picture_urls(paper):
    """Where a paper's picture may come from, best first: its uploaded still, then its arXiv figures."""
    return [url for url in [paper.get("image"), *(paper.get("figures") or [])] if url]


def _picture(paper, images):
    return next((images[url] for url in picture_urls(paper) if url in images), None)


def _logo(canvas, name, height):
    return load_source_logo(ASSETS / "logos" / f"{name}.png", height, canvas.color("ink") if _night(canvas) else None)


def _paste_logo(canvas, name, x, middle, height):
    logo = _logo(canvas, name, height)
    if logo is None:
        return 0
    canvas.image.paste(logo, (round(x), round(middle - logo.height / 2)), logo)
    return logo.width


def _rounded(picture, size, radius):
    tile = ImageOps.fit(picture.convert("RGBA"), size, Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    tile.putalpha(Image.composite(tile.getchannel("A"), mask, mask))
    return tile


def avatar_mark(canvas, picture, side, max_width=None):
    """An organisation's avatar ``side`` px tall: its mark off its flat tile (a wordmark as wide as
    WORDMARK_WIDTH times that, or ``max_width``), its parts too close to the page in ink; a photo
    stays a rounded square. The avatar's corners do not count: the Hub's copies can carry a rounded
    tile's dark corners, which would hold the mark's crop to the whole square."""
    corners = AVATAR_CORNER if min(picture.size) > 8 * AVATAR_CORNER else 0
    art = cut_flat_ground(picture, side * WORDMARK_WIDTH, corners=corners)
    if art is None:
        return _rounded(picture, (side, side), max(2, side // 5))
    width = min(side * WORDMARK_WIDTH, max_width or side * WORDMARK_WIDTH)
    art = ImageOps.contain(art, (max(side, width), side), Image.Resampling.LANCZOS)
    paper, ink = canvas.color("paper"), canvas.color("ink")
    if _night(canvas) and _dark_mark(art):
        # A dark mark (a navy wordmark) turns light on the black page, its hues kept, as a diagram does.
        return Image.merge("RGBA", (*light_on_dark(art.convert("RGB")).split(), art.getchannel("A")))
    return readable_on(art, paper, ink)


def _dark_mark(art):
    """Whether ``art`` (RGBA) is drawn in a dark colour (a navy, not a bright brand blue): the colour
    a quarter of the way up its drawn pixels by lightness, its white fills and soft rims aside."""
    drawn = sorted(((red, green, blue) for red, green, blue, alpha in art.get_flattened_data()
                    if alpha >= 128 and contrast_ratio((red, green, blue), (255, 255, 255)) >= 1.5),
                   key=relative_luminance)
    return bool(drawn) and max(drawn[len(drawn) // 4]) < DARK_MARK_VALUE


def spells_name(mark):
    """Whether an organisation's mark is a wordmark (its name spelled out), which needs no name beside it."""
    return mark.width >= WORDMARK_RATIO * mark.height


def _avatar(canvas, paper, images, x, middle, side=AVATAR, max_width=None):
    """The organisation's mark before ``x``'s text: its width (0 without one) and whether it
    spells the organisation's name."""
    picture = images.get(paper.get("org_avatar") or "")
    if picture is None:
        return 0, False
    mark = avatar_mark(canvas, picture, side, max_width)
    canvas.image.paste(mark, (round(x), round(middle - mark.height / 2)), mark)
    return mark.width, spells_name(mark)


def _placeholder():
    try:
        with Image.open(ASSETS / "placeholder.png") as source:
            return source.convert("RGBA")
    except OSError:
        return None


def _prepared(picture):
    """The figure to show and whether it is a diagram drawn on white (its empty margins gone)."""
    if picture is None:
        return None, False
    if drawn_on_white(picture):
        return trim_white(picture), True
    return picture, False


def _figure_width(art, drawing, height, bounds):
    """How wide a box ``height`` px tall should be to show ``art`` whole, within ``bounds``."""
    low, high = bounds
    if art is None:
        return low
    inset = 2 * DIAGRAM_INSET if drawing else 0
    return max(low, min(high, round((height - inset) * art.width / art.height) + inset))


def _shown(picture, size, kept):
    """``picture`` as large as it goes in ``size``: cut to the box's shape, but keeping at least
    ``kept`` of its width (or height), then fitted whole."""
    width, height = picture.size
    shape = size[0] / size[1]
    if width / height > shape:
        side = max(width * kept, height * shape)
        crop = ((width - side) / 2, 0, (width + side) / 2, height)
    else:
        side = max(height * kept, width / shape)
        crop = (0, (height - side) / 2, width, (height + side) / 2)
    return ImageOps.contain(picture.crop(tuple(round(value) for value in crop)), size, Image.Resampling.LANCZOS)


def _centred(canvas, art, box, ground):
    """Where ``art`` sits centred in ``box``, the page's doodles cleared under it to ``ground``."""
    left, top, right, bottom = box
    at = (round(left + (right - left - art.width) / 2), round(top + (bottom - top - art.height) / 2))
    canvas.draw.rectangle((at[0], at[1], at[0] + art.width - 1, at[1] + art.height - 1), fill=ground)
    return at


def _figure(canvas, art, drawing, box, ground, *, radius, kept):
    """The paper's figure filling ``box``: a photo cropped to it with rounded corners, a diagram as
    large as it goes (cut to the box's shape down to ``kept`` of it) blended into ``ground``, else
    the placeholder."""
    left, top, right, bottom = box
    size = (right - left, bottom - top)
    if art is None:
        placeholder = _placeholder()
        if placeholder is None:
            draw_icon(canvas.draw, "paper", ((left + right) / 2 - 14, (top + bottom) / 2 - 14), 28,
                      canvas.color("muted"), ground)
            return
        placeholder = ImageOps.contain(placeholder, (round(size[0] * 0.86), round(size[1] * 0.86)),
                                       Image.Resampling.LANCZOS)
        canvas.image.paste(placeholder, _centred(canvas, placeholder, box, ground), placeholder)
        return
    if not drawing:
        tile = _rounded(art, size, radius)
        canvas.image.paste(tile, (left, top), tile)
        return
    shown = _shown(art.convert("RGB"), (size[0] - 2 * DIAGRAM_INSET, size[1] - 2 * DIAGRAM_INSET), kept)
    blend_drawing(canvas.image, shown, _centred(canvas, shown, box, ground), night=_night(canvas))


def _doodles(canvas, box, ground, offset=(0, 0)):
    mode = "night" if _night(canvas) else "day"
    paint_doodles(canvas.image, box, DOODLES, HF_ORANGE if mode == "day" else HF_YELLOW,
                  strength=DOODLE_STRENGTH[mode], grounds=[ground], radius=6, offset=offset)


def _rank(canvas, x, middle, rank, size=12):
    """The paper's rank in its information row, clear of its figure."""
    return canvas.text((x, middle), str(rank), size, anchor="lm") + 7


def _upvote(canvas, x, middle, count, size=11, *, anchor="lm"):
    """The upvote arrow (solid orange) and the count; the pair's width."""
    text = count_text(count)
    width = 9 + 3 + canvas.width(text, size)
    left = x if anchor == "lm" else x - width
    canvas.draw.polygon([(left, middle + 4), (left + 9, middle + 4), (left + 4.5, middle - 4)], fill=HF_ORANGE)
    canvas.text((left + 12, middle), text, size, anchor="lm")
    return width


def _stat(canvas, x, middle, icon, text, size=11):
    draw_icon(canvas.draw, icon, (x, middle - 6), 12, canvas.color("ink"), canvas.color("paper"))
    return 15 + canvas.text((x + 15, middle), text, size, anchor="lm", bold=False)


def _code_logo(canvas, size, solid=False):
    ink = canvas.color("paper") if solid else canvas.color("ink")
    return load_source_logo(ASSETS / "logos" / "github_mark.png", size + 1, ink)


def _links_width(canvas, paper, size=10, gap=6, solid=False):
    widths = []
    for key, label in LINKS:
        if paper.get(key):
            art = _code_logo(canvas, size, solid) if key == "code" else None
            mark = art.width + 3 if art is not None else (0 if solid else LINK_MARK + 3)
            widths.append(canvas.width(label, size) + mark + (8 if solid else 0))
    return sum(widths) + (4 if solid else gap) * max(0, len(widths) - 1)


def _links(canvas, paper, right, middle, size=10, gap=6, solid=False):
    """GitHub's mark for code and a green marker for a project page, followed by their labels.
    Solid chips keep both readable over the small cards' pictures."""
    width = _links_width(canvas, paper, size, gap, solid)
    x = right - width
    for key, label in LINKS:
        if not paper.get(key):
            continue
        art = _code_logo(canvas, size, solid) if key == "code" else None
        if solid:
            mark = art.width + 3 if art is not None else 0
            chip, height = canvas.width(label, size) + mark + 8, size + 6
            canvas.draw.rounded_rectangle((x, middle - height / 2, x + chip, middle + height / 2), radius=height // 2,
                                          fill=canvas.color(key))
            if art is not None:
                canvas.image.paste(art, (round(x + 4), round(middle - art.height / 2)), art)
            canvas.text((x + 4 + mark, middle), label, size, "paper", anchor="lm")
            x += chip + 4
            continue
        if art is not None:
            canvas.image.paste(art, (round(x), round(middle - art.height / 2)), art)
            x += art.width + 3
        else:
            canvas.draw.rounded_rectangle((x, middle - LINK_MARK / 2, x + LINK_MARK, middle + LINK_MARK / 2), radius=2,
                                          fill=canvas.color(key))
            x += LINK_MARK + 3
        x += canvas.text((x, middle), label, size, "ink", anchor="lm") + gap
    return width


def _hub_model(canvas, hub, x, middle, right):
    """The lead's most liked model on the Hub on one line: Hugging Face's mark, the model, its likes,
    and how many more models cite the paper. The model's name comes first: its owner, then the count
    of the others, then its likes give way until it fits."""
    model = (hub or {}).get("model") or {}
    if not model.get("id"):
        return False
    x += _paste_logo(canvas, "huggingface_mark", x, middle, 14) + 5
    others = int((hub or {}).get("models") or 0) - 1
    likes = count_text(model["likes"]) if model.get("likes") else ""
    tail = f"+{others} 个模型" if others > 0 else ""
    names = (model["id"], model["id"].split("/")[-1])
    options = [(name, likes, tail) for name in names] + [(names[1], likes, ""), (names[1], "", "")]
    for name, likes, tail in options:
        need = (canvas.width(name, 11) + (canvas.width(likes, 11) + 20 if likes else 0)
                + (canvas.width(tail, 10, False) + 8 if tail else 0))
        if need <= right - x:
            break
    if tail:
        right -= canvas.text((right, middle), tail, 10, "muted", anchor="rm", bold=False) + 8
    if likes:
        right -= canvas.text((right, middle), likes, 11, anchor="rm") + 14
        draw_icon(canvas.draw, "heart", (right, middle - 5), 11, HF_ORANGE, canvas.color("lead"))
        right -= 6
    canvas.text((x, middle), name, 11, anchor="lm", width=right - x)
    return True


def _org_line(canvas, paper, images, x, middle, right, size, side=None, *, bold=True):
    """The organisation (its mark and name, a wordmark alone), else the first author and the count
    of the rest."""
    width, spelled = _avatar(canvas, paper, images, x, middle, side or size + 6, right - x)
    if spelled:
        return
    x += width + 5 if width else 0
    name = paper.get("organization")
    if not name and paper.get("authors"):
        rest = paper["author_count"] - 1
        name = paper["authors"][0] + (f" 等 {paper['author_count']} 人" if rest > 0 else "")
    if name:
        canvas.text((x, middle), name, size, "muted", anchor="lm", width=right - x, bold=bold)


def _standing(canvas, paper, x, right, middles):
    """The paper's upvotes, comments, GitHub stars and arXiv id, in that order, along the lines
    whose middles are ``middles``: a part that does not fit the rest of a line starts the next."""
    votes = count_text(paper["upvotes"])
    logo = _logo(canvas, "arxiv", 13)

    def upvotes(at, middle):
        at += _upvote(canvas, at, middle, paper["upvotes"], 13) + 4
        canvas.text((at, middle), "赞", 11, "muted", anchor="lm", bold=False)

    def stat(icon, text):
        return 15 + canvas.width(text, 11, False), lambda at, middle: _stat(canvas, at, middle, icon, text)

    def arxiv(at, middle):
        at += _paste_logo(canvas, "arxiv", at, middle, 13) + 5
        canvas.text((at, middle), paper["id"], 11, anchor="lm", bold=False)

    def github(at, middle):
        at += _paste_logo(canvas, "github", at, middle, 12) + 5
        _stat(canvas, at, middle, "star", count_text(paper["stars"]))

    parts = [(12 + canvas.width(votes, 13) + 4 + canvas.width("赞", 11, False), upvotes),
             stat("comment", f"{paper['comments']} 评论")]
    if paper.get("stars"):
        github_logo = _logo(canvas, "github", 12)
        if github_logo is not None:
            parts.append((github_logo.width + 20 + canvas.width(count_text(paper["stars"]), 11, False), github))
        else:
            parts.append(stat("star", f"{count_text(paper['stars'])} GitHub 星"))
    parts.append(((logo.width + 5 if logo else 0) + canvas.width(paper["id"], 11, False), arxiv))
    lines = iter(middles)
    middle, at = next(lines), x
    for width, draw in parts:
        if at > x and at + width > right:
            middle, at = next(lines, None), x
            if middle is None:
                return
        draw(at, middle)
        at += width + 12


def _lead(canvas, paper, images, chinese):
    left, top, right, bottom = LEAD
    canvas.draw.rounded_rectangle(LEAD, radius=6, fill=canvas.color("lead"))
    _doodles(canvas, LEAD, canvas.color("lead"))
    # The figure runs the card's full height down its left side, as wide as its shape asks.
    art, drawing = _prepared(_picture(paper, images))
    figure_width = _figure_width(art, drawing, bottom + 1 - top, LEAD_FIGURE_WIDTH)
    _figure(canvas, art, drawing, (left, top, left + figure_width, bottom + 1), canvas.color("lead"), radius=6,
            kept=LEAD_DIAGRAM_KEPT)
    x, text_right = left + figure_width + 12, right - 10
    links = _links(canvas, paper, text_right, top + 16, 11)
    rank_w = _rank(canvas, x, top + 16, 1)
    _org_line(canvas, paper, images, x + rank_w, top + 16, text_right - (links + 8 if links else 0), 12)
    title, summary, translated = shown_text(paper, chinese)
    y = top + 30
    for line in canvas.wrap(title, 18, text_right - x, 3 if not translated else 2):
        canvas.text((x, y), line, 18, anchor="la")
        y += 24
    if translated:
        canvas.text((x, y + 1), paper["title"], 10, "muted", anchor="la", width=text_right - x, bold=False)
        y += 16
    # The paper's standing closes the column in two lines; the summary and keywords take what is left.
    footer = bottom - 40
    hub = paper.get("hub") if (paper.get("hub") or {}).get("model") else None
    if hub:
        footer -= 22
    for line in canvas.wrap(summary, 12, text_right - x, max(0, min(LEAD_SUMMARY_LINES, (footer - y) // 18))):
        canvas.text((x, y + 2), line, 12, anchor="la", bold=False)
        y += 18
    chip_x, keywords = x, paper.get("keywords", [])[:KEYWORDS] if y + 22 <= footer else []
    for word in keywords:
        width = canvas.width(word, 10, False) + 12
        if chip_x + width > text_right:
            break
        canvas.draw.rounded_rectangle((chip_x, y + 6, chip_x + width, y + 22), radius=8, fill=canvas.color("track"))
        canvas.text((chip_x + width / 2, y + 14), word, 10, anchor="mm", bold=False)
        chip_x += width + 6
    if hub:
        _hub_model(canvas, hub, x, footer + 13, text_right)
    _standing(canvas, paper, x, text_right, (bottom - 31, bottom - 12))


def _card(canvas, box, rank, paper, images, chinese):
    left, top, right, bottom = box
    canvas.draw.rounded_rectangle(box, radius=6, outline=canvas.color("rule"), width=1)
    _doodles(canvas, (left + 1, top + 1, right - 1, bottom - 1), canvas.color("paper"), offset=(rank * 97, rank * 61))
    # The figure fills the card's full height down its left side, inside its border.
    art, drawing = _prepared(_picture(paper, images))
    figure = (left + 1, top + 1, left + 1 + CARD_FIGURE_WIDTH, bottom)
    _figure(canvas, art, drawing, figure, canvas.color("paper"), radius=5, kept=CARD_DIAGRAM_KEPT)
    _links(canvas, paper, figure[0] + 4 + _links_width(canvas, paper, solid=True), bottom - 12, solid=True)
    x, text_right = figure[2] + 8, right - 8
    count_w = _upvote(canvas, text_right, top + 13, paper["upvotes"], 11, anchor="rm")
    rank_w = _rank(canvas, x, top + 13, rank, 10)
    _org_line(canvas, paper, images, x + rank_w, top + 13, text_right - count_w - 6, 10)
    title, summary, _translated = shown_text(paper, chinese)
    y = top + 25
    lines = canvas.wrap(title, 12, text_right - x, 3)
    for line in lines:
        canvas.text((x, y), line, 12, anchor="la")
        y += 16
    # What the paper does, in the lines the title leaves.
    for line in canvas.wrap(summary, 10, text_right - x, max(0, (bottom - 6 - y) // 14)):
        canvas.text((x, y + 1), line, 10, "muted", anchor="la", bold=False)
        y += 14


def _grid(canvas, papers, images, chinese):
    left, top, right, bottom = GRID
    width = (right - left - GRID_GAP) / 2
    height = (bottom - top - GRID_GAP) / 2
    for index, paper in enumerate(papers):
        column, row = index % 2, index // 2
        x0, y0 = left + column * (width + GRID_GAP), top + row * (height + GRID_GAP)
        _card(canvas, (round(x0), round(y0), round(x0 + width), round(y0 + height)), index + 2, paper, images, chinese)


def _ranking(canvas, papers, images, chinese, first_rank, title="今日榜单"):
    left, top, right, bottom = RANKING
    canvas.draw.rounded_rectangle(RANKING, radius=6, outline=canvas.color("rule"), width=1)
    _doodles(canvas, (left + 1, top + 31, right - 1, bottom - 1), canvas.color("paper"), offset=(211, 37))
    canvas.draw.rectangle((left + 8, top + 9, left + 12, top + 23), fill=HF_YELLOW)
    # The papers are arXiv's: its logo follows the heading.
    heading = canvas.text((left + 18, top + 16), title, 14, anchor="lm")
    _paste_logo(canvas, "arxiv", left + 18 + heading + 8, top + 16, 14)
    canvas.text((right - 10, top + 16), "按社区点赞", 10, "muted", anchor="rm", bold=False)
    canvas.rule((left + 8, top + 30, right - 8, top + 30))
    row_h = (bottom - top - 34) / RANKING_ROWS
    text_x = left + 34
    for index, paper in enumerate(papers[:RANKING_ROWS]):
        y = top + 34 + index * row_h
        title, _summary, _translated = shown_text(paper, chinese)
        lines = canvas.wrap(title, 13, right - 10 - text_x, 2)
        # Each row reads down, centred in its band: its title (two lines at most), a clear gap, then
        # who made it and its upvotes.
        line_y = y + (row_h - len(lines) * RANKING_LINE - RANKING_GAP - RANKING_MARK) / 2
        canvas.text((left + 18, line_y + 9), str(first_rank + index), 16, anchor="mm")
        for line in lines:
            canvas.text((text_x, line_y), line, 13, anchor="la")
            line_y += RANKING_LINE
        meta = line_y + RANKING_GAP + RANKING_MARK / 2
        count_w = _upvote(canvas, right - 10, meta, paper["upvotes"], 11, anchor="rm")
        links = _links(canvas, paper, right - 16 - count_w, meta)
        _org_line(canvas, paper, images, text_x, meta, right - 18 - count_w - (links + 6 if links else 0), 10,
                  RANKING_MARK, bold=False)
        if index < RANKING_ROWS - 1:
            canvas.rule((left + 10, y + row_h, right - 10, y + row_h), "track")


def days_label(days, now):
    """How the subtitle names the days ranked: 今昨两日 while they are today and yesterday (the UTC
    dates Hugging Face files papers under), else their dates, as after a weekend ("10.30、10.29 两日")."""
    days = [str(day) for day in days or [] if day]
    today = now.astimezone(timezone.utc).date()
    if not days or days == [(today - timedelta(days=offset)).isoformat() for offset in range(len(days))]:
        return "今昨两日" if len(days) != 1 else "今日"
    dates = "、".join(f"{day[5:7]}.{day[8:10]}" for day in days)
    return f"{dates} 两日" if len(days) == 2 else f"{dates} "


def render_page(dimensions, payload, settings, now, images):
    canvas = PageCanvas(_palette(settings), family=(settings or {}).get("fontFamily") or "Microsoft YaHei")
    placed = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (10, 4, 236, 50), align="left")
    if placed:
        x = placed[2] + 16
    else:
        x = 14 + _paste_logo(canvas, "huggingface_mark", 14, 27, 32) + 8
        x += canvas.text((x, 27), "AI 论文日报", 26, anchor="lm") + 16
    x += _paste_logo(canvas, "huggingface", x, 27, 20) + 10
    canvas.rule((x, 18, x, 36))
    _paste_logo(canvas, "arxiv", x + 10, 27, 17)
    canvas.text((788, 19), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    label = days_label(payload.get("days"), now)
    canvas.text((788, 39), f"Hugging Face 社区每日精选 · {label}按点赞排", 10, "muted", anchor="rm", bold=False)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    papers = payload.get("papers") or []
    chinese = payload.get("chinese") or {}
    if not papers:
        canvas.text((400, 260), "暂时取不到 Hugging Face 每日论文", 18, "muted", anchor="mm")
    else:
        _lead(canvas, papers[0], images, chinese)
        _grid(canvas, papers[1:5], images, chinese)
        # Past a weekend the ranking is the latest days' (Friday's and Thursday's), not today's.
        _ranking(canvas, papers[5:], images, chinese, 6, "今日榜单" if label.startswith("今") else "近日榜单")
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
