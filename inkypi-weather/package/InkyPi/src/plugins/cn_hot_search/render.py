"""800 x 480 全网热搜 page: the story on the most lists across the top, then each platform's list
in a card under its official logo and colour.

The lead story names its place on every list (its picture fading into the page, a platform's
summary under its title); under it a strip of Baidu's 分榜 gives the first drama, film, novel,
game and car on its other boards. Each card shows a list's first three entries, each with its picture,
its heat, the platform's own label (新 / 热 / 辟谣 …), whether it is on other lists too, a line of
what the platform says about it, and its move since the previous refresh. Baidu, Toutiao, Zhihu
and Douyin fill a 2 x 2 grid; with the owner's Weibo login it leads a 3 x 2 grid whose last card
takes turns by the hour between the other stories that are on several lists, 澎湃's hot list and
the overseas talk (Bluesky's trends and Mastodon's most shared links, in Chinese once translated),
Chinese Wikipedia's most-read articles and 百度贴吧's 热议榜 (each topic's discussion count and the
sentence Tieba writes for it).
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.flat_icons import draw_icon
from utils.local_wordmark import load_source_logo, paste_local_wordmark
from utils.page_canvas import PageCanvas

from . import sources, topic_art
from .cn_hot_search import ROWS_SHOWN, list_moves


ASSETS = Path(__file__).resolve().parent / "assets"
DAY = {"paper": "#ffffff", "ink": "#121a20", "muted": "#3a454d", "rule": "#a6a6a6", "up": "#0a8f3c",
       "down": "#d42020", "new": "#d42020", "badge_ink": "#ffffff", "shade": "#121a20", "lead": "#fff4e0"}
NIGHT = {"paper": "#000000", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a", "up": "#6fd38f",
         "down": "#ff6b6b", "new": "#ff6b6b", "badge_ink": "#000000", "shade": "#000000", "lead": "#2a1a06"}
# Each platform's own colour (its logo's), as the bar along its card.
BRANDS = {"weibo": (0xE6, 0x16, 0x2D), "baidu": (0x29, 0x32, 0xE1), "toutiao": (0xF0, 0x41, 0x42),
          "zhihu": (0x05, 0x6D, 0xE8), "douyin": (0xFE, 0x2C, 0x55)}
NAMES = {"weibo": "微博", "baidu": "百度", "toutiao": "头条", "zhihu": "知乎", "douyin": "抖音"}
NOTES = {"weibo": "微博热搜", "baidu": "实时热搜", "toutiao": "头条热榜", "zhihu": "知乎热榜", "douyin": "抖音热榜"}
LOGO_HEIGHTS = {"weibo": 19, "baidu": 20, "toutiao": 15, "zhihu": 17, "douyin": 17, "thepaper": 18, "bluesky": 15,
                "mastodon": 16, "wikipedia_zh": 18, "tieba": 20}
# The cards that take turns in the grid's spare places, hour by hour: the stories on several lists,
# 澎湃's hot list, the overseas talk (Bluesky's trends and Mastodon's most shared links), Chinese
# Wikipedia's most read and 百度贴吧's 热议榜. Each turn card wears its source's colour: 澎湃's
# ink-black mark, Bluesky's butterfly blue, the Baidu blue of Tieba's paw.
TURN_CARDS = ("shared", "thepaper", "overseas", "zhwiki", "tieba")
TURN_SECONDS = 10 * 60
EXTRA_TINTS = {"overseas": (0x00, 0x85, 0xFF), "tieba": BRANDS["baidu"]}
# Tieba's logo already reads 贴吧: its card's title is only the list's name.
EXTRA_TITLES = {"thepaper": "澎湃热榜", "overseas": "海外热议", "zhwiki": "维基热读", "tieba": "热议榜"}
EXTRA_NOTES = {"thepaper": "澎湃新闻", "overseas": "Bluesky · Mastodon", "zhwiki": "昨日浏览最多", "tieba": "吧友实时讨论"}
EXTRA_LOGOS = {"overseas": ("bluesky", "mastodon"), "zhwiki": ("wikipedia_zh",)}
# The header's row of logos: Baidu's paw stands above its letters, so it is set taller.
KEY_LOGO_HEIGHTS = {"weibo": 22, "baidu": 24, "toutiao": 18, "zhihu": 20, "douyin": 20}
KEY_GAP = 18
# The platforms' labels, each a solid chip of its own colour.
TAG_COLORS = {"新": (0xF0, 0x7A, 0x00), "热": (0xE0, 0x23, 0x23), "沸": (0xF0, 0x55, 0x00),
              "爆": (0xB0, 0x00, 0x2A), "深度": (0x5B, 0x4B, 0xD6), "解读": (0x5B, 0x4B, 0xD6),
              "辟谣": (0x0E, 0x8F, 0x7E), "独家": (0xB8, 0x86, 0x0B), "首发": (0xF0, 0x7A, 0x00)}
# A story that is on other lists too wears this gold: its chip, and the card that gathers them.
SHARED = (0xD9, 0x9A, 0x06)
WEEKDAYS = "一二三四五六日"
BAR = 6
LEAD = (12, 58, 788, 156)
LEAD_PICTURE_W = 178
LEAD_FADE = 0.5
GRID_TOP, GRID_BOTTOM, GRID_GAP = 164, 470, 8
# Baidu's 分榜 strip between the lead and the cards (when its boards are at hand): each board's
# first entry behind a pictogram of its own colour (day, night), the grid starting under it.
STRIP = (12, 160, 788, 181)
STRIP_GRID_TOP = 186
BOARD_STYLES = {
    "teleplay": ("television", (200, 30, 50), (240, 86, 102)),
    "movie": ("clapperboard", (37, 84, 199), (96, 140, 245)),
    "novel": ("book", (5, 150, 105), (52, 211, 153)),
    "game": ("gamepad", (124, 58, 237), (167, 139, 250)),
    "car": ("car", (217, 119, 6), (251, 176, 59)),
}
# A card row: a narrow rank column, then the full thumbnail; the title on the
# first line, the heat, labels and the platform's own words on the second.
THUMB_RATIO = 1.3
RANK_WIDTH = 16
MOVE_ROOM = 28
# A row's title takes the largest of these sizes it fits whole at (the narrow cards start a size
# down), clipped only at the smallest.
TITLE_SIZES = (14, 13, 12)
# A row's sentence is shown only with at least this much room before its heat and tags.
NOTE_MIN_WIDTH = 30
# Each card's doodles (its platform's own theme: chatter, search, news, questions, video, the shared
# card's network) show this much of their strokes, under the rows.
DOODLE_STRENGTH = 0.26
LEAD_DOODLE_STRENGTH = 0.18


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", (settings or {}).get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def heat_text(value):
    """7808034 -> "781万", 52300 -> "5.2万", 123456789 -> "1.2亿"."""
    value = int(value or 0)
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.0f}万" if value >= 1_000_000 else f"{value / 10_000:.1f}万"
    return str(value) if value else ""


def span_text(epoch, now):
    """How long since ``epoch``: "35分钟", "3小时", "2天"; "" without one."""
    if not epoch:
        return ""
    minutes = max(0, int((now.astimezone(timezone.utc) - datetime.fromtimestamp(epoch, timezone.utc)).total_seconds() // 60))
    if minutes < 60:
        return f"{max(1, minutes)}分钟"
    if minutes < 48 * 60:
        return f"{minutes // 60}小时"
    return f"{minutes // 1440}天"


def ago_text(epoch, now):
    """How long ago a Douyin topic started: "35分钟前", "3小时前", "2天前"."""
    span = span_text(epoch, now)
    return f"{span}前" if span else ""


def row_note(platform, row, now):
    """The line of what the platform says about an entry, under its title (Zhihu's answer count
    only when it has no sentence; row_count gives it otherwise)."""
    if platform == "zhihu":
        return row.get("summary") or (f"{row['answers']}回答" if row.get("answers") else "")
    if platform == "douyin":
        videos = f"{row['videos']}个视频" if row.get("videos") else ""
        return " · ".join(part for part in (videos, ago_text(row.get("since"), now)) if part)
    if platform == "weibo":
        return row.get("category") or ""
    return row.get("summary") or ""


def row_count(platform, row):
    """The count that follows an entry's sentence, with its heat and tags: Zhihu's answers."""
    if platform == "zhihu" and row.get("summary") and row.get("answers"):
        return f"{row['answers']}回答"
    return ""


def _night(canvas):
    return sum(canvas.color("paper")) < 200


@lru_cache(maxsize=24)
def _logo_art(platform, height, night):
    ink = (237, 241, 234) if night else None
    return load_source_logo(ASSETS / "logos" / f"{platform}.png", height, ink)


def _paste_logo(canvas, platform, x, middle, height=None):
    """The platform's official logo, its left end at ``x``; returns its width."""
    art = _logo_art(platform, height or LOGO_HEIGHTS[platform], _night(canvas))
    if art is None:
        return canvas.text((x, middle), NAMES[platform], 13, anchor="lm")
    canvas.image.paste(art, (round(x), round(middle - art.height / 2)), art)
    return art.width


def _fade_right(canvas, picture, box, *, fade_from, radius=6):
    """``picture`` fitted to ``box``, rounded on the left, fading into the page across its right side."""
    x0, y0, x1, y1 = [int(value) for value in box]
    size = (max(1, x1 - x0), max(1, y1 - y0))
    tile = ImageOps.fit(picture.convert("RGB"), size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.4))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1 + radius, size[1] - 1), radius=radius, fill=255)
    start = round(size[0] * fade_from)
    ramp = Image.new("L", (size[0], 1), 255)
    for index in range(start, size[0]):
        t = (index - start) / max(1, size[0] - 1 - start)
        ramp.putpixel((index, 0), round(255 * (1 - t * t * (3 - 2 * t))))
    canvas.image.paste(tile, (x0, y0), ImageChops.multiply(mask, ramp.resize(size)))


def _thumb(canvas, picture, box, tint):
    """The entry's picture in a rounded box, or a quiet tile in its platform's colour."""
    x0, y0, x1, y1 = [int(value) for value in box]
    size = (max(1, x1 - x0), max(1, y1 - y0))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=4, fill=255)
    if picture is not None:
        tile = ImageOps.fit(picture.convert("RGB"), size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.35))
        canvas.image.paste(tile, (x0, y0), mask)
        return True
    paper = canvas.color("paper")
    quiet = tuple(round(paper[index] * 0.8 + tint[index] * 0.2) for index in range(3))
    canvas.image.paste(quiet, (x0, y0, x1, y1), mask)
    return False


def _glass(canvas, box, tint, *, fill_alpha, edge_alpha, radius=6):
    """A frosted tile over what is already drawn: ``tint`` laid on at ``fill_alpha``, a one-pixel
    edge of it at ``edge_alpha`` and a faint light line along the top, like glass catching light."""
    x0, y0, x1, y1 = [round(value) for value in box]
    size = (x1 - x0 + 1, y1 - y0 + 1)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    pen.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(*tint, round(255 * fill_alpha)),
                          outline=(*tint, round(255 * edge_alpha)), width=1)
    pen.line((radius, 1, size[0] - 1 - radius, 1), fill=(255, 255, 255, 46 if _night(canvas) else 140), width=1)
    canvas.image.paste(layer, (x0, y0), layer)


def _chip(canvas, x, middle, text, fill, *, outline=False):
    """A small label chip from ``x``; returns its width. A platform's own label (热, 新, 升...) is a
    solid chip a size smaller than the outlined "on N lists" one, so it sits quietly by the heat."""
    if outline:
        width = canvas.width(text, 9) + 8
        canvas.draw.rounded_rectangle((x, middle - 7, x + width, middle + 7), radius=7, outline=fill, width=1)
        canvas.text((x + width / 2, middle), text, 9, fill, anchor="mm")
    else:
        width = canvas.width(text, 8) + 6
        canvas.draw.rounded_rectangle((x, middle - 6, x + width, middle + 5), radius=3, fill=fill)
        canvas.text((x + width / 2, middle), text, 8, (255, 255, 255), anchor="mm")
    return width


def _has_move(move):
    return move == "new" or (isinstance(move, int) and move != 0)


def _move(canvas, x, middle, move):
    """NEW, or a small triangle and the places gained or lost, ending at ``x``."""
    if move == "new":
        width = canvas.width("NEW", 8) + 8
        canvas.draw.rounded_rectangle((x - width, middle - 6, x, middle + 6), radius=6, fill=canvas.color("new"))
        canvas.text((x - width / 2, middle), "NEW", 8, "badge_ink", anchor="mm")
        return
    if not isinstance(move, int) or move == 0:
        return
    tone = "up" if move > 0 else "down"
    label = str(abs(move)) if abs(move) < 100 else ""
    left = x - 9 - (canvas.width(label, 10) if label else 0)
    points = ((left, middle + 3), (left + 7, middle + 3), (left + 3.5, middle - 4)) if move > 0 else \
        ((left, middle - 3), (left + 7, middle - 3), (left + 3.5, middle + 4))
    canvas.draw.polygon(points, fill=canvas.color(tone))
    if label:
        canvas.text((left + 9, middle), label, 10, tone, anchor="lm")


# --- the lead story ------------------------------------------------------------------------------

def _lead_story(payload):
    """The story on the most lists, else the first list's first entry; None without either."""
    topics = payload.get("topics") or []
    if topics:
        return topics[0]
    for platform in sources.ORDER:
        rows = (payload.get("lists") or {}).get(platform) or []
        if rows:
            return {"title": rows[0]["title"], "ranks": {platform: rows[0]["rank"]}, "rows": {platform: rows[0]}}
    return None


def story_since(story):
    """When the story began, as the platforms that say date it (Douyin's event, Weibo's time on
    its list): the earliest, or 0."""
    times = [row.get("since") for row in story["rows"].values() if row.get("since")]
    return min(times) if times else 0


def lead_facts(story, now, *, since=True):
    """The lead's line of what the platforms say about it: how long it has run (unless ``since`` is
    False, when the grid shows it), Douyin's videos, Zhihu's answers, Weibo's category."""
    rows = story["rows"]
    facts = []
    if since and story_since(story):
        facts.append(f"发酵 {span_text(story_since(story), now)}")
    if (rows.get("douyin") or {}).get("videos"):
        facts.append(f"抖音 {rows['douyin']['videos']} 个视频")
    if (rows.get("zhihu") or {}).get("answers"):
        facts.append(f"知乎 {rows['zhihu']['answers']} 回答")
    if (rows.get("weibo") or {}).get("category"):
        facts.append(f"微博 · {rows['weibo']['category']}")
    return " · ".join(facts)


def lead_moves(payload, story):
    """{platform: move} of the lead story on each list it is on, against that list's previous reading."""
    moves = {}
    for platform, row in story["rows"].items():
        history = ((payload.get("history") or {}).get(platform) or {})
        if history.get("previous"):
            moves[platform] = list_moves((payload.get("lists") or {}).get(platform), history["previous"]).get(row["title"])
    return moves


def _picture_for(images, rows):
    """The first of ``rows``' pictures that is at hand."""
    for row in rows:
        picture = images.get(sources.safe_image_url(row.get("image")))
        if picture is not None:
            return picture
    return None


def _lead(canvas, payload, images, platforms, now):
    left, top, right, bottom = LEAD
    canvas.draw.rounded_rectangle(LEAD, radius=8, fill=canvas.color("lead"))
    columns = 2 if len(platforms) <= 4 else 3
    cell_w = 92 if columns == 2 else 84
    # (Baidu's logo height, the others', the rank's size): the 3-column grid's cells are narrower.
    logo_h, rank_size = ((15, 13), 16) if columns == 2 else ((12, 10), 13)
    grid_left = right - 8 - columns * cell_w - (columns - 1) * 6
    # Headline doodles, fainter than the cards', under the story's words (the picture fades over them).
    paint_doodles(canvas.image, (left + 1, top + 1, grid_left - 6, bottom - 1), ASSETS / "doodles" / "lead.png",
                  SHARED, strength=LEAD_DOODLE_STRENGTH, radius=7, offset=(left, top))
    story = _lead_story(payload)
    if story is None:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "热搜榜单暂不可用", 16, "muted", anchor="mm")
        return
    rows = story["rows"]
    picture = _picture_for(images, rows.values())
    x = left + 14
    if picture is not None:
        _fade_right(canvas, picture, (left, top, left + LEAD_PICTURE_W, bottom), fade_from=LEAD_FADE, radius=8)
        x = left + LEAD_PICTURE_W - 4
    width = grid_left - 12 - x
    # The chip says how widely the story runs: on how many of the lists.
    shared = len(story["ranks"])
    chip = f"全网 {shared}/{len(platforms)} 同上" if shared > 1 else f"{NAMES[next(iter(rows))]}热搜第一"
    chip_w = canvas.width(chip, 10) + 14
    canvas.draw.rounded_rectangle((x, top + 8, x + chip_w, top + 24), radius=8, fill=SHARED)
    canvas.text((x + chip_w / 2, top + 16), chip, 10, (255, 255, 255), anchor="mm")
    canvas.text((x + chip_w + 8, top + 16), "全网头条", 11, "muted", anchor="lm")
    for size in (21, 19, 17):
        lines = canvas.wrap(story["title"], size, width, 2)
        # Whole, and with no lone character or two left on a second line ("…8 / 强").
        if not lines[-1].endswith("…") and (len(lines) == 1 or len(lines[-1]) > 2):
            break
    y = top + 30
    for line in lines:
        canvas.text((x, y), line, size, stroke="lead")
        y += size + 5
    summary = next((row.get("summary") for row in rows.values() if row.get("summary")), "")
    for line in canvas.wrap(summary, 11, width, 2 if len(lines) == 1 else 1, bold=False) if summary else []:
        canvas.text((x, y + 1), line, 11, "muted", bold=False, stroke="lead")
        y += 15
    # A grid with a cell to spare shows how long the story has run there; the facts line otherwise.
    spare = len(platforms) % columns and story_since(story)
    facts = lead_facts(story, now, since=not spare)
    if facts and y <= bottom - 16:
        canvas.text((x, bottom - 11), facts, 10, "ink", anchor="lm", bold=False, width=width, stroke="lead")
    # Where the story stands on each list: the platform's logo and its place, its heat there and
    # how it moved since the list's previous reading.
    moves = lead_moves(payload, story)
    cell_h = (bottom - top - 14) / 2
    for index, platform in enumerate(platforms + (["since"] if spare else [])):
        cx = grid_left + (index % columns) * (cell_w + 6)
        cy = top + 7 + (index // columns) * cell_h
        box = (cx, cy + 1, cx + cell_w, cy + cell_h - 3)
        upper, lower = box[1] + 11, box[3] - 9
        if platform == "since":
            _glass(canvas, box, SHARED, fill_alpha=0.2 if _night(canvas) else 0.12, edge_alpha=0.85)
            canvas.text((cx + 6, upper), "已发酵", 10, "muted", anchor="lm", bold=False)
            canvas.text((box[2] - 5, lower - 2), span_text(story_since(story), now), rank_size, "ink", anchor="rm")
            continue
        rank = story["ranks"].get(platform)
        # Glass tiles: a list the story is on is tinted its platform's colour, the others barely there.
        if rank:
            _glass(canvas, box, BRANDS[platform], fill_alpha=0.24 if _night(canvas) else 0.13, edge_alpha=0.85)
        else:
            _glass(canvas, box, canvas.color("muted"), fill_alpha=0.08 if _night(canvas) else 0.05, edge_alpha=0.45)
        _paste_logo(canvas, platform, cx + 5, upper, logo_h[0] if platform == "baidu" else logo_h[1])
        if rank:
            canvas.text((box[2] - 5, upper + 1), f"#{rank}", rank_size, BRANDS[platform], anchor="rm")
            heat = heat_text(rows[platform].get("heat"))
            if heat:
                canvas.text((cx + 6, lower), heat, 9, "ink", anchor="lm", bold=False)
            _move(canvas, box[2] - 5, lower, moves.get(platform))
        else:
            canvas.text((box[2] - 8, upper), "—", 13, "rule", anchor="rm")
            canvas.text((cx + 6, lower), "未上榜", 9, "muted", anchor="lm", bold=False)


# --- the cards ------------------------------------------------------------------------------------

def _slots(payload, now=None):
    """The cards in order: Weibo when the owner signed in, the four lists, then the turn cards at
    hand (TURN_CARDS, each ten-minute slot choosing which comes first) in the grid's spare places; the
    grid stays even, so the four lists alone keep a 2 x 2 grid when nothing else is at hand."""
    lists = payload.get("lists") or {}
    slots = (["weibo"] if "weibo" in lists else []) + list(sources.PLATFORMS)
    extras = payload.get("extras") or {}
    candidates = [name for name in TURN_CARDS if name == "shared" or extras.get(name)]
    # Absolute slots keep the cycle continuous across midnight and DST changes.
    # This only selects already-cached rows; source refresh TTLs are independent.
    turn = (int(now.timestamp()) // TURN_SECONDS if now else 0) % len(candidates)
    chosen = (candidates[turn:] + candidates[:turn])[:max(0, 6 - len(slots))]
    if (len(slots) + len(chosen)) % 2:
        chosen = chosen[:-1]
    return slots + chosen


def _card_boxes(count, grid_top=GRID_TOP):
    columns = 2 if count <= 4 else 3
    width = (LEAD[2] - LEAD[0] - (columns - 1) * GRID_GAP) / columns
    height = (GRID_BOTTOM - grid_top - GRID_GAP) / 2
    return [(round(LEAD[0] + (index % columns) * (width + GRID_GAP)), round(grid_top + (index // columns) * (height + GRID_GAP)),
             round(LEAD[0] + (index % columns) * (width + GRID_GAP) + width),
             round(grid_top + (index // columns) * (height + GRID_GAP) + height)) for index in range(count)]


def _arrow(canvas, x, middle, change):
    """A small up or down triangle from ``x`` for a board entry that rose or fell; its width."""
    if change not in ("up", "down"):
        return 0
    tone = "up" if change == "up" else "down"
    points = ((x, middle + 3), (x + 7, middle + 3), (x + 3.5, middle - 4)) if change == "up" else         ((x, middle - 3), (x + 7, middle - 3), (x + 3.5, middle + 4))
    canvas.draw.polygon(points, fill=canvas.color(tone))
    return 7


def _boards_strip(canvas, boards, box):
    """百度分榜: each board's first entry in a cell of the strip -- its board's chip, its title, how
    it moved and the fact that names it (a drama's lead actor, a novel's author, a car's price)."""
    left, top, right, bottom = box
    middle = (top + bottom) / 2
    night = _night(canvas)
    _glass(canvas, box, BRANDS["baidu"], fill_alpha=0.16 if night else 0.07, edge_alpha=0.55)
    x = left + 8
    x += _paste_logo(canvas, "baidu", x, middle, 14) + 4
    x += canvas.text((x, middle), "分榜", 11, anchor="lm") + 8
    tabs = [tab for tab in sources.BAIDU_BOARDS if boards.get(tab)]
    cell_w = (right - 4 - x) / max(1, len(tabs))
    for index, tab in enumerate(tabs):
        row = boards[tab][0]
        cx, end = x + index * cell_w, x + (index + 1) * cell_w - 6
        if index:
            canvas.rule((cx - 4, top + 5, cx - 4, bottom - 5))
        icon, day_ink, night_ink = BOARD_STYLES[tab]
        ink = night_ink if night else day_ink
        # Transparent cutouts preserve the strip beneath the coloured pictogram.
        art = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
        draw_icon(ImageDraw.Draw(art), icon, (0, 0), 11, (*ink, 255), (0, 0, 0, 0))
        canvas.image.paste(art, (round(cx + 2), round(middle - 6)), art)
        tx = cx + 20
        tx += canvas.text((tx, middle), row["title"], 12, anchor="lm", width=end - tx) + 3
        tx += _arrow(canvas, tx, middle, row.get("change"))
        detail = row.get("detail") or ""
        if detail and end - tx - 4 >= canvas.width(detail[:2], 10, False):
            canvas.text((tx + 4, middle + 1), detail, 10, "muted", anchor="lm", bold=False, width=end - tx - 4)


def _shared_marks(payload):
    """{platform: {rank: topic}} for the entries that are on other lists too."""
    marks = {platform: {} for platform in sources.ORDER}
    for topic in payload.get("topics") or []:
        for platform, rank in topic["ranks"].items():
            marks[platform][rank] = topic
    return marks


def _frame(canvas, box, tint, title_drawer, pattern):
    """A card's frame under its colour bar, its own doodles (``pattern``, assets/doodles) laid
    quietly in its colour across the body; returns the first row's top and the rows' height."""
    left, top, right, bottom = box
    canvas.draw.rounded_rectangle(box, radius=6, width=1, outline=canvas.color("rule"))
    canvas.draw.rounded_rectangle((left, top, right, top + BAR), radius=6, fill=tint, corners=(True, True, False, False))
    paint_doodles(canvas.image, (left + 2, top + BAR + 2, right - 2, bottom - 2), ASSETS / "doodles" / f"{pattern}.png",
                  tint, strength=DOODLE_STRENGTH, radius=4, offset=(left, top))
    title_drawer(top + BAR + 11)
    row_top = top + BAR + 23
    return row_top, (bottom - 3 - row_top) / ROWS_SHOWN


def _row(canvas, box, y0, row_h, *, picture, tint, rank, title, move, meta):
    """One entry: rank beside the picture, title (its move at the line's end) and the meta line
    ``meta`` draws from a left x and a middle y."""
    left, _top, right, _bottom = box
    thumb_h = int(row_h - 5)
    thumb_left = left + 6 + RANK_WIDTH
    thumb = (thumb_left, int(y0 + 2), thumb_left + round(thumb_h * THUMB_RATIO), int(y0 + 2) + thumb_h)
    _thumb(canvas, picture, thumb, tint)
    rank_ink = tuple(round(channel * 0.7 + 255 * 0.3) for channel in tint) if _night(canvas) else tint
    canvas.text((left + 6 + (RANK_WIDTH - 3) / 2, y0 + row_h / 2), str(rank), 12 if rank < 10 else 11,
                rank_ink if rank <= 3 else "muted", anchor="mm", width=RANK_WIDTH - 3)
    text_x = thumb[2] + 6
    # The move's room is kept only when there is a move to show.
    room = right - 8 - (MOVE_ROOM if _has_move(move) else 0) - text_x
    sizes = TITLE_SIZES if right - left > 300 else TITLE_SIZES[1:]
    size = next((size for size in sizes if canvas.width(title, size) <= room), sizes[-1])
    title_middle = y0 + 2 + sizes[0] / 2 + 1
    canvas.text((text_x, title_middle), title, size, anchor="lm", width=room)
    _move(canvas, right - 8, title_middle, move)
    meta(text_x, y0 + row_h - 12, right - 8)


def _platform_card(canvas, box, platform, payload, images, marks, now):
    left, top, right, bottom = box
    rows = ((payload.get("lists") or {}).get(platform) or [])[:ROWS_SHOWN]
    history = ((payload.get("history") or {}).get(platform) or {})
    moves = list_moves(rows, history.get("previous")) if history.get("previous") else {}

    def title(middle):
        _paste_logo(canvas, platform, left + 9, middle)
        canvas.text((right - 9, middle), NOTES[platform], 10, "muted", anchor="rm", bold=False)

    row_top, row_h = _frame(canvas, box, BRANDS[platform], title, platform)
    if not rows:
        canvas.text(((left + right) / 2, (row_top + bottom) / 2), f"{NAMES[platform]}榜单暂不可用", 12, "muted", anchor="mm")
        return
    for number, row in enumerate(rows):
        y0 = row_top + number * row_h
        if number:
            canvas.rule((left + 8, y0 - 1, right - 8, y0 - 1))
        rank = row.get("rank") or number + 1
        topic = marks.get(rank)
        # An entry without a picture borrows the one another list gives the same story, else shows
        # an illustration of its topic.
        picture = _picture_for(images, [row] + (list(topic["rows"].values()) if topic else []))             or topic_art.picture(row.get("title"), row.get("category"))
        move = moves.get(row.get("title"))
        if move is None and platform == "baidu":
            move = {"up": 1, "down": -1}.get(row.get("change"))

        def meta(x, middle, end, row=row, topic=topic):
            heat = heat_text(row.get("heat"))
            tags = [(row["tag"], TAG_COLORS.get(row["tag"], SHARED), False)] if row.get("tag") else []
            if topic:
                tags.append((f"全网{len(topic['ranks'])}", SHARED, True))
            count = row_count(platform, row)
            note = row_note(platform, row, now)

            def marks(x):
                if count:
                    x += canvas.text((x, middle), count, 10, "muted", anchor="lm", bold=False) + 6
                if heat:
                    x += canvas.text((x, middle), heat, 10, "ink", anchor="lm") + 5
                for text, fill, outline in tags:
                    x += _chip(canvas, x, middle, text, fill, outline=outline) + 4
                return x

            if not row.get("summary"):
                # No sentence (Douyin's videos and age, Weibo's category): the heat and tags lead.
                x = marks(x)
                if note and end - x > NOTE_MIN_WIDTH:
                    canvas.text((x + 1, middle), note, 10, "muted", anchor="lm", bold=False, width=end - x - 1)
                return
            # The platform's sentence leads, clipped to leave room for its counts, heat and tags.
            marks_w = (canvas.width(count, 10, False) + 6 if count else 0) \
                + (canvas.width(heat, 10) + 5 if heat else 0) + sum(canvas.width(text, 9) + 12 for text, _f, _o in tags)
            if end - x - marks_w > NOTE_MIN_WIDTH:
                x += canvas.text((x, middle), note, 10, "muted", anchor="lm", bold=False, width=end - x - marks_w) + 6
            marks(x)

        _row(canvas, box, y0, row_h, picture=picture, tint=BRANDS[platform], rank=rank, title=row.get("title"),
             move=move, meta=meta)


def _shared_card(canvas, box, payload, images):
    """The other stories that are on several lists, each with where it stands on them."""
    left, top, right, bottom = box
    topics = (payload.get("topics") or [])[1:1 + ROWS_SHOWN]

    def title(middle):
        canvas.text((left + 9, middle), "全网同上", 14, anchor="lm")
        canvas.text((right - 9, middle), "多个榜单同时在榜", 10, "muted", anchor="rm", bold=False)

    row_top, row_h = _frame(canvas, box, SHARED, title, "shared")
    if not topics:
        canvas.text(((left + right) / 2, (row_top + bottom) / 2), "暂无多榜同上的话题", 12, "muted", anchor="mm")
        return
    for number, topic in enumerate(topics):
        y0 = row_top + number * row_h
        if number:
            canvas.rule((left + 8, y0 - 1, right - 8, y0 - 1))

        def meta(x, middle, end, topic=topic):
            for platform in sources.ORDER:
                rank = topic["ranks"].get(platform)
                if rank is None:
                    continue
                art = _logo_art(platform, 11 if platform != "baidu" else 13, _night(canvas))
                needed = (art.width if art is not None else 24) + canvas.width(f"#{rank}", 10) + 10
                if x + needed > end:
                    break
                x += _paste_logo(canvas, platform, x, middle, 11 if platform != "baidu" else 13) + 2
                x += canvas.text((x, middle), f"#{rank}", 10, BRANDS[platform], anchor="lm") + 8

        picture = _picture_for(images, topic["rows"].values()) or topic_art.picture(
            topic["title"], next((row.get("category") for row in topic["rows"].values() if row.get("category")), ""))
        _row(canvas, box, y0, row_h, picture=picture, tint=SHARED,
             rank=number + 2, title=topic["title"], move=None, meta=meta)


def row_title(row):
    """A turn card row's title: the overseas talk's in Chinese once translated."""
    return row.get("title_zh") or row.get("title")


def _extra_meta(canvas, name, row, now):
    """A turn card row's second line: 澎湃's column, likes, comments and age; the overseas talk's
    network mark, Bluesky's category or the outlet, its posts or shares and its trend's state;
    Tieba's running discussion count, then the sentence it writes for the topic."""
    def meta(x, middle, end):
        if name == "tieba":
            if row.get("heat"):
                x += canvas.text((x, middle), f"{heat_text(row['heat'])}讨论", 10, "ink", anchor="lm") + 6
            if row.get("summary") and end - x > NOTE_MIN_WIDTH:
                canvas.text((x, middle), row["summary"], 10, "muted", anchor="lm", bold=False, width=end - x)
            return
        if name == "overseas":
            x += _paste_logo(canvas, row["source"], x, middle, 11) + 5
        parts = []
        if name == "thepaper":
            parts = [f"{heat_text(row.get('heat'))}赞" if row.get("heat") else "",
                     f"{row['comments']}评" if row.get("comments") else "", ago_text(row.get("since"), now)]
        elif name == "zhwiki" and row.get("heat"):
            parts = [f"{heat_text(row['heat'])}次浏览"]
        elif row.get("heat"):
            parts = [f"{heat_text(row['heat'])}{'帖' if row['source'] == 'bluesky' else '次分享'}"]
        if row.get("tag"):
            x += _chip(canvas, x, middle, row["tag"], TAG_COLORS.get(row["tag"], SHARED)) + 5
        if row.get("summary") and end - x > NOTE_MIN_WIDTH:
            x += canvas.text((x, middle), row["summary"], 10, "ink", anchor="lm", width=min(90, end - x)) + 6
        text = " · ".join(part for part in parts if part)
        if text and end - x > NOTE_MIN_WIDTH:
            canvas.text((x, middle), text, 10, "muted", anchor="lm", bold=False, width=end - x)
    return meta


def _extra_card(canvas, box, name, payload, images, now):
    """A turn card: 澎湃's hot list, the overseas talk, Wikipedia's most read or Tieba's topics,
    under its sources' official logos."""
    left, top, right, bottom = box
    rows = ((payload.get("extras") or {}).get(name) or [])[:ROWS_SHOWN]
    tint = EXTRA_TINTS.get(name) or canvas.color("ink")

    def title(middle):
        x = left + 9
        for logo in EXTRA_LOGOS.get(name, (name,)):
            x += _paste_logo(canvas, logo, x, middle) + 5
        canvas.text((x + 1, middle), EXTRA_TITLES[name], 14, anchor="lm")
        canvas.text((right - 9, middle), EXTRA_NOTES[name], 10, "muted", anchor="rm", bold=False)

    row_top, row_h = _frame(canvas, box, tint, title, "shared")
    for number, row in enumerate(rows):
        y0 = row_top + number * row_h
        if number:
            canvas.rule((left + 8, y0 - 1, right - 8, y0 - 1))
        picture = _picture_for(images, [row]) or topic_art.picture(row.get("title"), row.get("summary"))
        _row(canvas, box, y0, row_h, picture=picture, tint=tint, rank=row.get("rank") or number + 1,
             title=row_title(row), move=None, meta=_extra_meta(canvas, name, row, now))


def _key(canvas, platforms, left, right):
    """The header's key: the platforms' official logos in a row, divided by thin rules, as far as
    there is room."""
    x = left
    for index, platform in enumerate(platforms):
        art = _logo_art(platform, KEY_LOGO_HEIGHTS[platform], _night(canvas))
        width = art.width if art is not None else canvas.width(NAMES[platform], 12)
        if x + width > right:
            break
        if index:
            canvas.rule((x - KEY_GAP / 2, 18, x - KEY_GAP / 2, 36))
        _paste_logo(canvas, platform, x, 27, KEY_LOGO_HEIGHTS[platform])
        x += width + KEY_GAP


def render_page(dimensions, payload, settings, now, images):
    canvas = PageCanvas(_palette(settings), family=(settings or {}).get("fontFamily") or "Microsoft YaHei")
    placed = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (10, 2, 230, 50), align="left")
    title_right = placed[2] if placed else 14 + canvas.text((14, 26), "全网热搜", 28, anchor="lm")
    beijing = now.astimezone(sources.BEIJING)
    stamp_left = 788 - canvas.text((788, 19), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    canvas.text((788, 39), f"北京时间 {beijing:%m.%d %H:%M}", 10, "muted", anchor="rm", bold=False)
    slots = _slots(payload, now)
    platforms = [slot for slot in slots if slot in sources.ORDER]
    _key(canvas, platforms, title_right + 16, stamp_left - 14)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _lead(canvas, payload, images, platforms, now)
    boards = payload.get("boards") or {}
    grid_top = GRID_TOP
    if any(boards.get(tab) for tab in sources.BAIDU_BOARDS):
        _boards_strip(canvas, boards, STRIP)
        grid_top = STRIP_GRID_TOP
    marks = _shared_marks(payload)
    for slot, box in zip(slots, _card_boxes(len(slots), grid_top)):
        if slot == "shared":
            _shared_card(canvas, box, payload, images)
        elif slot in EXTRA_TITLES:
            _extra_card(canvas, box, slot, payload, images, now)
        else:
            _platform_card(canvas, box, slot, payload, images, marks[slot], now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
