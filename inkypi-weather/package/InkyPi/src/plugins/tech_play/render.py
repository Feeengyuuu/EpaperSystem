"""800 x 480 Tech & Play page, laid out as outlined cards with coloured accent bars.

Left: service status tiles and numbered arXiv papers. Right: Epic giveaways, upcoming
releases, and per-game speedrun world records with box art, trophies and runner flags.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageOps

from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_local_wordmark
from utils.page_canvas import PageCanvas
from utils.safe_image import ImageLimits, safe_open_image

from .sources import epic_end_label, flag_url, local_now, parse_time, run_time


ASSETS = Path(__file__).resolve().parent / "assets"
_LIMITS = ImageLimits(max_bytes=256 * 1024, max_width=512, max_height=512, max_pixels=512 * 512)
DAY = {
    "paper": "#fbfaf5", "panel": "#ebe8de", "ink": "#121a20", "muted": "#222c34", "rule": "#8d969c",
    "ok": "#00a83a", "minor": "#ffde00", "major": "#d42020", "maintenance": "#1f5fbf", "unknown": "#8a949b",
    "accent": "#1f5fbf", "gold": "#ffde00", "badge_ink": "#ffffff", "band": "#121a20",
    "status_bar": "#00a83a", "papers_bar": "#d42020", "epic_bar": "#121a20", "upcoming_bar": "#1f5fbf",
    "speed_bar": "#ffde00",
}
NIGHT = {
    "paper": "#0e171f", "panel": "#1c2a35", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#55666f",
    "ok": "#6fd38f", "minor": "#ffd84a", "major": "#ff6b6b", "maintenance": "#7fb8ff", "unknown": "#7d8b94",
    "accent": "#7fb8ff", "gold": "#ffd84a", "badge_ink": "#0e171f", "band": "#05090c",
    "status_bar": "#6fd38f", "papers_bar": "#ff6b6b", "epic_bar": "#edf1ea", "upcoming_bar": "#7fb8ff",
    "speed_bar": "#ffd84a",
}
STATUS_TEXT = {"none": "正常", "minor": "轻微", "major": "故障", "critical": "严重", "maintenance": "维护",
               "unknown": "未知"}
STATUS_ICON = {"none": "ok", "minor": "alert", "major": "outage", "critical": "outage",
               "maintenance": "maintenance", "unknown": "info"}
WEEKDAYS = "一二三四五六日"
LEFT_CARD = (12, 58, 268, 244)
PAPER_CARD = (12, 250, 268, 468)
EPIC_CARD = (280, 58, 788, 196)
UPCOMING_CARD = (280, 202, 788, 314)
SPEED_CARD = (280, 320, 788, 468)
HEADER = 28


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = settings.get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", settings.get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def _logo(name, height, max_width=None):
    path = ASSETS / f"logo-{name}.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_LIMITS) as source:
        image = source.convert("RGBA")
    return ImageOps.contain(image, (max_width or height * 3, height), Image.Resampling.LANCZOS)


def _paste(canvas, image, xy):
    if image is None:
        return 0
    if image.mode == "RGBA":
        canvas.image.paste(image, (round(xy[0]), round(xy[1])), image)
    else:
        canvas.image.paste(image, (round(xy[0]), round(xy[1])))
    return image.width


def ago(stamp, now):
    seconds = max(0, (now - stamp).total_seconds())
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def _card(canvas, box, bar, title, *, logo=None, icon=None, detail="", chip=None):
    """Outlined card with a coloured accent bar, a logo or icon, a title and a right-hand detail."""
    left, top, right, bottom = box
    canvas.draw.rounded_rectangle(box, radius=6, outline=canvas.color("rule"), width=1)
    canvas.draw.rectangle((left + 8, top + 8, left + 12, top + 22), fill=canvas.color(bar))
    cursor = left + 18
    if logo is not None:
        cursor += _paste(canvas, logo, (cursor, top + 15 - logo.height / 2)) + 6
    elif icon:
        draw_icon(canvas.draw, icon, (cursor, top + 7), 16, canvas.color("ink"), canvas.color("paper"))
        cursor += 22
    cursor += canvas.text((cursor, top + 15), title, 15, anchor="lm") + 8
    if chip:
        width = canvas.width(chip, 11) + 12
        canvas.draw.rounded_rectangle((cursor, top + 7, cursor + width, top + 23), radius=8,
                                      outline=canvas.color("ink"), width=1)
        canvas.text((cursor + width / 2, top + 15), chip, 11, anchor="mm")
        cursor += width + 8
    if detail:
        canvas.text((right - 8, top + 15), detail, 11, "muted", anchor="rm", bold=False, width=right - 8 - cursor)
    canvas.rule((left + 8, top + HEADER, right - 8, top + HEADER))


# --- Left column -----------------------------------------------------------------------------------

def _status(canvas, payload):
    source = payload["status"]
    services = source["data"].get("services", [])
    healthy = sum(1 for service in services if service.get("indicator") == "none")
    detail = f"{healthy}/{len(services)} 正常" if services else "暂不可用"
    if source["state"] == "stale_cache":
        detail = "缓存 · " + detail
    _card(canvas, LEFT_CARD, "status_bar", "服务状态", icon="pulse", detail=detail)
    left, right = LEFT_CARD[0] + 8, LEFT_CARD[2] - 8
    if not services:
        canvas.text((left, 120), "状态页暂不可用", 14, "muted", anchor="lm")
        return
    for index, service in enumerate(services[:5]):
        top = LEFT_CARD[1] + HEADER + 4 + index * 31
        indicator = service.get("indicator") or "unknown"
        tone = "major" if indicator == "critical" else indicator if indicator in STATUS_TEXT else "unknown"
        tone = "ok" if tone == "none" else tone
        canvas.draw.rounded_rectangle((left, top, left + 4, top + 26), radius=2, fill=canvas.color(tone))
        logo = _logo(service["id"], 18, max_width=22)
        if logo is not None:
            _paste(canvas, logo, (left + 19 - logo.width / 2, top + 13 - logo.height / 2))
        label = STATUS_TEXT.get(indicator, "未知")
        label_width = canvas.text((right, top + 9), label, 12, anchor="rm")
        draw_icon(canvas.draw, STATUS_ICON.get(indicator, "info"), (right - label_width - 17, top + 2), 14,
                  canvas.color(tone), canvas.color("paper"))
        name_width = right - label_width - 24 - (left + 34)
        incident = service.get("incident")
        canvas.text((left + 34, top + (8 if incident else 13)), service["name"], 13, anchor="lm", width=name_width)
        if incident:
            canvas.text((left + 34, top + 21), incident, 11, "muted", anchor="lm", bold=False, width=right - left - 34)
        if index < len(services[:5]) - 1:
            canvas.rule((left + 34, top + 29, right, top + 29))


def _papers(canvas, payload, now):
    source = payload["arxiv"]
    papers = source["data"].get("papers", [])
    _card(canvas, PAPER_CARD, "papers_bar", "新论文", logo=_logo("arxiv", 18), chip=payload["arxiv_category"],
          detail=f"最新 {len(papers)} 篇" if papers else "")
    left, right = PAPER_CARD[0] + 8, PAPER_CARD[2] - 8
    if not papers:
        canvas.text((left, 300), "论文列表暂不可用", 14, "muted", anchor="lm")
        return
    for index, paper in enumerate(papers[:3]):
        top = PAPER_CARD[1] + HEADER + 6 + index * 61
        canvas.draw.rounded_rectangle((left, top + 1, left + 18, top + 19), radius=3, fill=canvas.color("ink"))
        canvas.text((left + 9, top + 10), str(index + 1), 12, "paper", anchor="mm")
        for line_index, line in enumerate(canvas.wrap(paper["title"], 13, right - left - 26, 2)):
            canvas.text((left + 26, top + line_index * 16), line, 13, anchor="la")
        chip = paper.get("id") or ""
        chip_width = canvas.width(chip, 10, False) + 10 if chip else 0
        if chip:
            canvas.draw.rounded_rectangle((right - chip_width, top + 36, right, top + 50), radius=3,
                                          outline=canvas.color("rule"), width=1)
            canvas.text((right - chip_width / 2, top + 43), chip, 10, anchor="mm", bold=False)
        authors = paper.get("authors") or []
        who = authors[0] + (" 等" if paper.get("author_count", 1) > 1 else "") if authors else ""
        stamp = parse_time(paper.get("time"))
        canvas.text((left + 26, top + 43), f"{who} · {ago(stamp, now) if stamp else ''}", 11, "muted",
                    anchor="lm", bold=False, width=right - left - 32 - chip_width)
        if index < 2:
            for x in range(left, right, 6):
                canvas.draw.line((x, top + 57, x + 2, top + 57), fill=canvas.color("rule"), width=1)


# --- Right column ----------------------------------------------------------------------------------

def _cover(image, size):
    return ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=(0.5, 0.4))


def _epic(canvas, payload, images, now):
    data = payload["epic"]["data"]
    current, upcoming = data.get("current", []), data.get("upcoming", [])
    later = "、".join(item["title"] for item in upcoming[:2])
    _card(canvas, EPIC_CARD, "epic_bar", "本周免费", logo=_logo("epic", 20),
          detail=f"下周：{later}" if later else "")
    cards = [(item, False) for item in current[:2]] + [(item, True) for item in upcoming[:2 - len(current[:2])]]
    left, right = EPIC_CARD[0] + 8, EPIC_CARD[2] - 8
    if not cards:
        canvas.text((left, 140), "本周暂无免费游戏" if payload["epic"]["state"] != "unavailable"
                    else "Epic 数据暂不可用", 15, "muted", anchor="lm")
        return
    top, bottom = EPIC_CARD[1] + HEADER + 4, EPIC_CARD[3] - 8
    width = (right - left - 10) // 2
    for index, (item, is_next) in enumerate(cards):
        x = left + index * (width + 10)
        image = images.get(item.get("image"))
        if image is not None:
            canvas.image.paste(_cover(image, (width, bottom - top)), (x, top))
        else:
            canvas.draw.rectangle((x, top, x + width, bottom), fill=canvas.color("panel"))
            draw_icon(canvas.draw, "gift", (x + width / 2 - 18, top + 18), 36, canvas.color("ink"),
                      canvas.color("panel"))
        canvas.draw.rectangle((x, bottom - 22, x + width, bottom), fill=canvas.color("band"))
        end, start = parse_time(item.get("end")), parse_time(item.get("start"))
        meta = (f"{start.astimezone(now.tzinfo):%m/%d} 开始" if is_next and start else
                epic_end_label(end, now) if end else "")
        meta_width = canvas.text((x + width - 6, bottom - 11), meta, 11, (255, 255, 255), anchor="rm", bold=False)
        canvas.text((x + 6, bottom - 11), item["title"], 13, (255, 255, 255), anchor="lm",
                    width=width - meta_width - 16)
        tag = "下周" if is_next else "免费领"
        tag_width = canvas.width(tag, 12) + 12
        canvas.draw.rectangle((x, top, x + tag_width, top + 18),
                              fill=canvas.color("maintenance" if is_next else "major"))
        canvas.text((x + tag_width / 2, top + 9), tag, 12, "badge_ink", anchor="mm")
        canvas.draw.rectangle((x, top, x + width, bottom), outline=canvas.color("ink"), width=1)


def _upcoming(canvas, payload, images, now):
    data = payload["upcoming"]["data"]
    items = data.get("items", [])
    source_name = data.get("source") or ""
    logo = _logo("igdb" if source_name == "IGDB" else "steam", 18)
    detail = "IGDB 期待度排行" if source_name == "IGDB" else "Steam 热门即将推出" if source_name else ""
    _card(canvas, UPCOMING_CARD, "upcoming_bar", "即将发售", logo=logo, detail=detail)
    left, right = UPCOMING_CARD[0] + 8, UPCOMING_CARD[2] - 8
    if not items:
        canvas.text((left, 260), "新作列表暂不可用", 14, "muted", anchor="lm")
        return
    count, gap = 4, 8
    width = (right - left - gap * (count - 1)) // count
    top = UPCOMING_CARD[1] + HEADER + 4
    for index, item in enumerate(items[:count]):
        x = left + index * (width + gap)
        box = (x, top, x + width, top + 46)
        image = images.get(item.get("image"))
        if image is not None:
            canvas.image.paste(_cover(image, (width, 46)), (x, top))
        else:
            canvas.draw.rectangle(box, fill=canvas.color("panel"))
            draw_icon(canvas.draw, "rocket", (x + width / 2 - 12, top + 11), 24, canvas.color("ink"),
                      canvas.color("panel"))
        canvas.draw.rectangle(box, outline=canvas.color("ink"), width=1)
        canvas.text((x, top + 56), item["name"], 12, anchor="lm", width=width)
        stamp = parse_time(item.get("date"))
        when = (f"{stamp.astimezone(now.tzinfo):%m/%d} 周{WEEKDAYS[stamp.astimezone(now.tzinfo).weekday()]}"
                if stamp else (item.get("date_text") or "日期待定"))
        draw_icon(canvas.draw, "calendar", (x, top + 64), 11, canvas.color("ink"), canvas.color("paper"))
        platforms = "/".join(item.get("platforms") or [])
        canvas.text((x + 15, top + 70), f"{when}  {platforms}".strip(), 11, "muted", anchor="lm", bold=False,
                    width=width - 15)


def _initials(name):
    """'DOP 4: Draw One Part' -> 'DOP4'; 'Super Mario 64' -> 'SM64'."""
    words = re.findall(r"[A-Za-z0-9]+", str(name or "").split(":")[0])
    if len(words) == 1:
        return words[0][:4].upper()
    return "".join(word if word.isdigit() or word.isupper() else word[0] for word in words)[:4].upper() or "?"


def coverage_label(data, now):
    """'本周' once a full week of runs is counted, otherwise the honest '近 N 小时/天'."""
    since = parse_time(data.get("since"))
    if since is None:
        return ""
    span = (now - since).total_seconds()
    if span >= 7 * 86400 - 3600:
        return "本周"
    if span >= 2 * 86400:
        return f"近 {int(span // 86400)} 天"
    return f"近 {max(1, int(span // 3600))} 小时"


def _speedruns(canvas, payload, images, now):
    data = payload["speedrun"]["data"]
    games = data.get("top", [])
    window = coverage_label(data, now)
    detail = f"speedrun.com · {window}新验证 {data.get('total', 0):,} 次" if games else "speedrun.com"
    _card(canvas, SPEED_CARD, "speed_bar", "速通周榜", logo=_logo("speedrun", 18), detail=detail)
    left, right = SPEED_CARD[0] + 8, SPEED_CARD[2] - 8
    if not games:
        canvas.text((left, 400), "正在统计 speedrun.com 新验证的成绩", 13, "muted", anchor="lm")
        return
    gap = 10
    width = (right - left - gap * 2) // 3
    top = SPEED_CARD[1] + HEADER + 4
    for index, game in enumerate(games[:3]):
        x = left + index * (width + gap)
        if index:
            canvas.rule((x - gap / 2, top, x - gap / 2, SPEED_CARD[3] - 8))
        cover = images.get(game.get("cover"))
        box = (x, top, x + 36, top + 48)
        if cover is not None:
            canvas.image.paste(_cover(cover, (36, 48)), (x, top))
        else:
            # No box art anywhere (mobile-only games): a lettered tile in the cover slot.
            canvas.draw.rectangle(box, fill=canvas.color("band"))
            canvas.text((x + 18, top + 20), _initials(game.get("name")), 11, (255, 255, 255), anchor="mm")
            draw_icon(canvas.draw, "trophy", (x + 12, top + 30), 12, canvas.color("gold"), canvas.color("band"))
        canvas.draw.rectangle(box, outline=canvas.color("ink"), width=1)
        badge_fill = canvas.color("gold" if index == 0 else "ink")
        canvas.draw.ellipse((x - 5, top - 5, x + 13, top + 13), fill=badge_fill, outline=canvas.color("paper"))
        canvas.text((x + 4, top + 4), str(index + 1), 11, (20, 20, 20) if index == 0 else "paper", anchor="mm")
        for line_index, line in enumerate(canvas.wrap(game.get("name") or "", 12, width - 44, 2)):
            canvas.text((x + 44, top + 1 + line_index * 15), line, 12, anchor="la")
        meta = " · ".join(part for part in (f"{game.get('week_runs', 0)} 次验证", str(game.get("released") or ""))
                          if part)
        canvas.text((x + 44, top + 40), meta, 11, "muted", anchor="lm", bold=False, width=width - 44)
        for run_index, run in enumerate(game.get("runs", [])[:2]):
            y = top + 58 + run_index * 30
            canvas.draw.ellipse((x, y + 3, x + 8, y + 11), fill=canvas.color("speed_bar"), outline=canvas.color("ink"))
            time_text = run_time(run["seconds"])
            time_width = canvas.text((x + width, y + 7), time_text, 12, anchor="rm")
            canvas.text((x + 13, y + 7), run.get("category") or "", 11, anchor="lm", width=width - time_width - 19)
            flag = images.get(flag_url(run.get("country")))
            runner_x = x + 13
            if flag is not None:
                small = ImageOps.contain(flag, (16, 11), Image.Resampling.LANCZOS)
                canvas.image.paste(small, (runner_x, y + 15))
                runner_x += small.width + 4
            verified = parse_time(run.get("verified"))
            when = ago(verified, now) if verified else ""
            when_width = canvas.text((x + width, y + 20), when, 10, "muted", anchor="rm", bold=False)
            canvas.text((runner_x, y + 20), run.get("runner") or "", 11, "muted", anchor="lm", bold=False,
                        width=x + width - when_width - 6 - runner_x)


def render_page(dimensions, payload, settings, now, images):
    now = local_now(now)
    canvas = PageCanvas(_palette(settings), family=settings.get("fontFamily") or "Microsoft YaHei")
    pasted = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (12, 6, 212, 50), align="left")
    if pasted:
        title_right = pasted[2]
    else:
        title_right = 16 + canvas.text((16, 27), "科技与游戏", 30, anchor="lm")
    canvas.text((title_right + 14, 30), "服务状态 · 免费游戏 · 新作 · 论文 · 速通", 14, "muted", anchor="lm", bold=False)
    canvas.text((788, 27), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _status(canvas, payload)
    _papers(canvas, payload, now)
    _epic(canvas, payload, images, now)
    _upcoming(canvas, payload, images, now)
    _speedruns(canvas, payload, images, now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
