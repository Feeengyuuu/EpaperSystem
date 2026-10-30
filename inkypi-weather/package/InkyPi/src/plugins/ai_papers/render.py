"""800 x 480 AI 论文日报 page.

Left: the most upvoted paper leads with its figure, the organisation behind it (its Hub avatar,
its logo), its title and one-sentence summary, its keywords, and its upvotes, comments, GitHub
stars and arXiv id. The next four stand in a 2 x 2 grid of cards beneath, each with its figure.
Right: the rest of the ranking, each paper's title over its organisation and its upvotes. The
header names the sources by their logos: Hugging Face Papers and arXiv.

Figures sit on the page itself: a diagram drawn on white is blended into the card's ground, a
photo keeps its own frame, and a paper without either shows a generated placeholder. The
organisations' avatars lose their flat tiles. The cards carry a quiet doodle of research themes.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.flat_icons import draw_icon
from utils.local_wordmark import load_source_logo, paste_local_wordmark
from utils.page_canvas import PageCanvas
from utils.picture_grounds import blend_drawing, cut_flat_ground, drawn_on_white, readable_on


ASSETS = Path(__file__).resolve().parent / "assets"
DAY = {"paper": "#ffffff", "ink": "#121a20", "muted": "#3a454d", "rule": "#a6a6a6", "track": "#eceef1",
       "lead": "#fff8dc"}
NIGHT = {"paper": "#000000", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a", "track": "#24282d",
         "lead": "#1c1704"}
# Hugging Face's yellow and orange (its brand), for the solid marks: rank badges and upvote arrows.
HF_YELLOW = (0xFF, 0xD2, 0x1E)
HF_ORANGE = (0xFF, 0x9D, 0x00)
WEEKDAYS = "一二三四五六日"
LEAD = (12, 60, 520, 268)
LEAD_FIGURE = (22, 70, 214, 214)
GRID = (12, 276, 520, 470)
GRID_GAP = 8
RANKING = (528, 60, 788, 470)
RANKING_ROWS = 7
# The cards' figures sit in a box this big, whole (a paper's diagram is not cropped).
CARD_FIGURE = (100, 75)
AVATAR = 18
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


def avatar_mark(canvas, picture, side):
    """An organisation's avatar ``side`` px tall: its mark off its flat tile, its parts too close to
    the page in ink; a photo stays a rounded square."""
    art = cut_flat_ground(picture, side)
    if art is None:
        return _rounded(picture, (side, side), max(2, side // 5))
    art = ImageOps.contain(art, (side, side), Image.Resampling.LANCZOS)
    return readable_on(art, canvas.color("paper"), canvas.color("ink"))


def _avatar(canvas, paper, images, x, middle, side=AVATAR):
    """The organisation's mark before ``x``'s text; its width (0 without one)."""
    picture = images.get(paper.get("org_avatar") or "")
    if picture is None:
        return 0
    mark = avatar_mark(canvas, picture, side)
    canvas.image.paste(mark, (round(x), round(middle - mark.height / 2)), mark)
    return mark.width


def _placeholder():
    try:
        with Image.open(ASSETS / "placeholder.png") as source:
            return source.convert("RGBA")
    except OSError:
        return None


def _figure(canvas, picture, box, ground):
    """The paper's figure whole in ``box``, on the page: a diagram blended into ``ground``, a photo
    with rounded corners, else the placeholder."""
    left, top, right, bottom = box
    size = (right - left, bottom - top)
    canvas.draw.rectangle((left, top, right - 1, bottom - 1), fill=ground)
    if picture is None:
        art = _placeholder()
        if art is None:
            draw_icon(canvas.draw, "paper", ((left + right) / 2 - 14, (top + bottom) / 2 - 14), 28,
                      canvas.color("muted"), ground)
            return
        art = ImageOps.contain(art, (round(size[0] * 0.86), round(size[1] * 0.86)), Image.Resampling.LANCZOS)
        canvas.image.paste(art, (round(left + (size[0] - art.width) / 2), round(top + (size[1] - art.height) / 2)), art)
        return
    art = ImageOps.contain(picture.convert("RGB"), size, Image.Resampling.LANCZOS)
    at = (round(left + (size[0] - art.width) / 2), round(top + (size[1] - art.height) / 2))
    if drawn_on_white(picture):
        blend_drawing(canvas.image, art, at, night=_night(canvas))
    else:
        tile = _rounded(art, art.size, 5)
        canvas.image.paste(tile, at, tile)


def _doodles(canvas, box, ground, offset=(0, 0)):
    mode = "night" if _night(canvas) else "day"
    paint_doodles(canvas.image, box, DOODLES, HF_ORANGE if mode == "day" else HF_YELLOW,
                  strength=DOODLE_STRENGTH[mode], grounds=[ground], radius=6, offset=offset)


def _badge(canvas, x, y, rank, size=20):
    """A Hugging Face yellow disc with the paper's rank."""
    canvas.draw.ellipse((x, y, x + size, y + size), fill=HF_YELLOW, outline=(0x12, 0x1A, 0x20), width=1)
    font = canvas.font(12 if size >= 20 else 10, True)
    canvas.draw.text((x + size / 2, y + size / 2 + 1), str(rank), font=font, fill=(0x12, 0x1A, 0x20), anchor="mm")


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


def _org_line(canvas, paper, images, x, middle, right, size):
    """The organisation (avatar and name), else the first author and the count of the rest."""
    x += _avatar(canvas, paper, images, x, middle, size + 6) + 5 if paper.get("org_avatar") in images else 0
    name = paper.get("organization")
    if not name and paper.get("authors"):
        rest = paper["author_count"] - 1
        name = paper["authors"][0] + (f" 等 {paper['author_count']} 人" if rest > 0 else "")
    if name:
        canvas.text((x, middle), name, size, "muted", anchor="lm", width=right - x)


def _lead(canvas, paper, images, chinese):
    left, top, right, bottom = LEAD
    canvas.draw.rounded_rectangle(LEAD, radius=6, fill=canvas.color("lead"))
    _doodles(canvas, LEAD, canvas.color("lead"))
    canvas.draw.rectangle((left, top + 6, left + 3, bottom - 6), fill=HF_YELLOW)
    _figure(canvas, _picture(paper, images), LEAD_FIGURE, canvas.color("lead"))
    _badge(canvas, LEAD_FIGURE[0] - 4, LEAD_FIGURE[1] - 4, 1, 24)
    x, text_right = LEAD_FIGURE[2] + 12, right - 10
    _org_line(canvas, paper, images, x, top + 18, text_right, 12)
    title, summary, translated = shown_text(paper, chinese)
    y = top + 32
    for line in canvas.wrap(title, 18, text_right - x, 3 if not translated else 2):
        canvas.text((x, y), line, 18, anchor="la")
        y += 24
    if translated:
        canvas.text((x, y + 1), paper["title"], 10, "muted", anchor="la", width=text_right - x, bold=False)
        y += 16
    for line in canvas.wrap(summary, 12, text_right - x, 3 if y < top + 110 else 2):
        canvas.text((x, y + 2), line, 12, anchor="la", bold=False)
        y += 18
    chip_x = x
    for word in paper.get("keywords", [])[:KEYWORDS]:
        width = canvas.width(word, 10, False) + 12
        if chip_x + width > text_right:
            break
        canvas.draw.rounded_rectangle((chip_x, y + 6, chip_x + width, y + 22), radius=8, fill=canvas.color("track"))
        canvas.text((chip_x + width / 2, y + 14), word, 10, anchor="mm", bold=False)
        chip_x += width + 6
    # The figure's caption line: the paper's standing.
    middle = bottom - 22
    sx = LEAD_FIGURE[0]
    sx += _upvote(canvas, sx, middle, paper["upvotes"], 13) + 4
    sx += canvas.text((sx, middle), "赞", 11, "muted", anchor="lm", bold=False) + 12
    sx += _stat(canvas, sx, middle, "comment", f"{paper['comments']} 评论") + 12
    if paper.get("stars"):
        sx += _stat(canvas, sx, middle, "star", f"{count_text(paper['stars'])} GitHub 星") + 12
    sx += _paste_logo(canvas, "arxiv", sx, middle, 13) + 5
    canvas.text((sx, middle), paper["id"], 11, anchor="lm", bold=False)


def _card(canvas, box, rank, paper, images, chinese):
    left, top, right, bottom = box
    canvas.draw.rounded_rectangle(box, radius=6, outline=canvas.color("rule"), width=1)
    _doodles(canvas, (left + 1, top + 1, right - 1, bottom - 1), canvas.color("paper"), offset=(rank * 97, rank * 61))
    figure = (left + 8, top + 8, left + 8 + CARD_FIGURE[0], top + 8 + CARD_FIGURE[1])
    _figure(canvas, _picture(paper, images), figure, canvas.color("paper"))
    _badge(canvas, left + 3, top + 3, rank, 18)
    x, text_right = figure[2] + 8, right - 8
    count_w = _upvote(canvas, text_right, top + 13, paper["upvotes"], 11, anchor="rm")
    _org_line(canvas, paper, images, x, top + 13, text_right - count_w - 6, 10)
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


def _ranking(canvas, papers, images, chinese, first_rank):
    left, top, right, bottom = RANKING
    canvas.draw.rounded_rectangle(RANKING, radius=6, outline=canvas.color("rule"), width=1)
    _doodles(canvas, (left + 1, top + 31, right - 1, bottom - 1), canvas.color("paper"), offset=(211, 37))
    canvas.draw.rectangle((left + 8, top + 9, left + 12, top + 23), fill=HF_YELLOW)
    canvas.text((left + 18, top + 16), "今日榜单", 14, anchor="lm")
    canvas.text((right - 10, top + 16), "按社区点赞", 10, "muted", anchor="rm", bold=False)
    canvas.rule((left + 8, top + 30, right - 8, top + 30))
    row_h = (bottom - top - 34) / RANKING_ROWS
    text_x = left + 34
    for index, paper in enumerate(papers[:RANKING_ROWS]):
        y = top + 34 + index * row_h
        # Each row reads down: its title (two lines at most), then who made it and its upvotes.
        canvas.text((left + 18, y + 13), str(first_rank + index), 16, anchor="mm")
        title, _summary, _translated = shown_text(paper, chinese)
        line_y = y + 5
        for line in canvas.wrap(title, 13, right - 10 - text_x, 2):
            canvas.text((text_x, line_y), line, 13, anchor="la")
            line_y += 17
        meta = y + row_h - 10
        count_w = _upvote(canvas, right - 10, meta, paper["upvotes"], 11, anchor="rm")
        x = text_x
        if paper.get("org_avatar") in images:
            x += _avatar(canvas, paper, images, x, meta, 14) + 4
        name = paper.get("organization") or (paper.get("authors") or [""])[0]
        if name:
            canvas.text((x, meta), name, 10, "muted", anchor="lm", width=right - 16 - count_w - x, bold=False)
        if index < RANKING_ROWS - 1:
            canvas.rule((left + 10, y + row_h, right - 10, y + row_h), "track")


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
    canvas.text((788, 39), "Hugging Face 社区每日精选 · 今昨两日按点赞排", 10, "muted", anchor="rm", bold=False)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    papers = payload.get("papers") or []
    chinese = payload.get("chinese") or {}
    if not papers:
        canvas.text((400, 260), "暂时取不到 Hugging Face 每日论文", 18, "muted", anchor="mm")
    else:
        _lead(canvas, papers[0], images, chinese)
        _grid(canvas, papers[1:5], images, chinese)
        _ranking(canvas, papers[5:], images, chinese, 6)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
