"""GitHub's repository page: the repository's stars large, then its forks, watchers, open issues and
pull requests and when it was last pushed, and its latest commit.

Everything but the commit comes from the one ``/repos/{owner}/{repo}`` response; the commit
(``/commits?per_page=1``, about 5 KB) is fetched only when the repository was pushed since the
last one was read. The last good reading stands in when GitHub cannot be reached.
"""

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from runtime.refresh_contracts import TaskCancelled
from utils.app_utils import get_base_ui_font, resolve_dimensions
from utils.flat_icons import draw_icon
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

API = "https://api.github.com/repos/{repository}"
REQUEST_TIMEOUT = (4, 12)
USER_AGENT = "InkyPi GitHub/1.0"
CACHE_FILE = "stars.json"
MARK = Path(__file__).resolve().parent / "assets" / "github-mark.png"
PAGE = (800, 480)
DAY = {"paper": (242, 243, 245), "panel": (255, 255, 255), "ink": (31, 35, 40), "muted": (89, 99, 110),
       "rule": (208, 215, 222)}
NIGHT = {"paper": (13, 17, 23), "panel": (22, 27, 34), "ink": (230, 237, 243), "muted": (166, 173, 182),
         "rule": (48, 54, 61)}
# Each figure's own colour, on its tile's band: stars gold, forks cobalt, watchers emerald, open
# issues crimson, the last push violet (brighter by night).
ACCENTS = {
    "day": {"star": (227, 179, 65), "fork": (37, 84, 199), "watch": (5, 150, 105), "issue": (200, 30, 50),
            "push": (130, 80, 223)},
    "night": {"star": (240, 196, 80), "fork": (122, 162, 255), "watch": (52, 211, 153), "issue": (255, 107, 120),
              "push": (180, 140, 255)},
}
# GitHub's own language colours (github-linguist), for the language dot.
LANGUAGE_COLOURS = {
    "Python": (53, 114, 165), "TypeScript": (49, 120, 198), "JavaScript": (241, 224, 90), "Go": (0, 173, 216),
    "Rust": (222, 165, 132), "Java": (176, 114, 25), "C++": (243, 75, 125), "C": (85, 85, 85),
    "Shell": (137, 224, 81), "HTML": (227, 76, 38), "Swift": (240, 81, 56), "Kotlin": (169, 123, 255),
}


def stars_generate_image(plugin_instance, settings, device_config):
    username = str(settings.get('githubUsername') or "").strip()
    repository = str(settings.get('githubRepository') or "").strip()
    if not username or not repository:
        raise RuntimeError("GitHub repository is required.")
    github_repository = f"{username}/{repository}"
    dimensions = resolve_dimensions(device_config)
    token = _token(device_config)
    cache_path = _cache_path(plugin_instance)
    cached = _read_json(cache_path)
    cached = cached if isinstance(cached, dict) and cached.get("repository") == github_repository else {}
    try:
        data = fetch_repository(github_repository, token, cached)
        _write_json(cache_path, data)
    except TaskCancelled:
        raise
    except Exception as e:
        logger.error(f"GitHub repository request failed: {str(e)}")
        if not cached.get("repo"):
            raise RuntimeError("GitHub request failure, please check logs")
        data = cached

    if settings.get("githubRenderer") == "html":
        repo = data["repo"]
        return plugin_instance.render_image(dimensions, "github_stars.html", "github.css", {
            "repository": github_repository,
            "stars": repo.get("stars", 0),
            "metrics": [{"title": title, "value": value}
                        for title, value in _figures(repo, datetime.now(timezone.utc))],
            "plugin_settings": settings,
        })
    return render_repository(dimensions, data, settings, datetime.now(timezone.utc))


def fetch_repository(github_repository, token="", cached=None):
    """The repository's figures and its latest commit; the cached commit stands while the
    repository has not been pushed since it was read."""
    cached = cached or {}
    session = get_http_session()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = API.format(repository=github_repository)
    response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    repo = normalize_repo(response.json())
    commit = (cached.get("commit") if cached.get("repo", {}).get("pushed_at") == repo["pushed_at"] else None)
    if commit is None:
        try:
            response = session.get(f"{url}/commits", params={"per_page": 1}, headers=headers,
                                   timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            commit = normalize_commit(response.json())
        except TaskCancelled:
            raise
        except Exception as exc:  # the figures stand without the commit
            logger.warning("GitHub latest commit unavailable: %s", exc)
            commit = cached.get("commit")
    return {"repository": github_repository, "repo": repo, "commit": commit,
            "fetched_at": datetime.now(timezone.utc).isoformat()}


def _count(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def normalize_repo(raw):
    if not isinstance(raw, dict) or "stargazers_count" not in raw:
        raise ValueError("GitHub returned no repository")
    licence = raw.get("license") if isinstance(raw.get("license"), dict) else {}
    return {
        "full_name": str(raw.get("full_name") or ""),
        "description": " ".join(str(raw.get("description") or "").split())[:200],
        "language": str(raw.get("language") or ""),
        "licence": str(licence.get("spdx_id") or "") if licence.get("spdx_id") != "NOASSERTION" else "",
        "stars": _count(raw.get("stargazers_count")),
        "forks": _count(raw.get("forks_count")),
        "watchers": _count(raw.get("subscribers_count")),
        "open_issues": _count(raw.get("open_issues_count")),
        "pushed_at": str(raw.get("pushed_at") or ""),
        "default_branch": str(raw.get("default_branch") or ""),
    }


def normalize_commit(raw):
    """The latest commit's subject, author, time and short sha, or None."""
    item = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], dict) else None
    if item is None:
        return None
    commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
    author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
    subject = str(commit.get("message") or "").strip().splitlines()
    if not subject:
        return None
    login = (item.get("author") or {}).get("login") if isinstance(item.get("author"), dict) else None
    return {"sha": str(item.get("sha") or "")[:7], "subject": " ".join(subject[0].split())[:200],
            "author": str(author.get("name") or login or ""), "date": str(author.get("date") or "")}


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def ago(value, now):
    """'5 分钟前', '2 小时前', '3 天前'; '' without a time."""
    stamp = _parse_time(value)
    if stamp is None:
        return ""
    seconds = max(0, (now - stamp).total_seconds())
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def compact(value):
    value = _count(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if value >= 10_000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return f"{value:,}"


def _figures(repo, now):
    """The tiles under the stars: (label, value)."""
    pushed = ago(repo.get("pushed_at"), now)
    figures = [("Fork", compact(repo.get("forks"))), ("关注", compact(repo.get("watchers"))),
               ("Issue + PR", compact(repo.get("open_issues")))]
    if pushed:
        figures.append(("推送", pushed))
    return figures


def _token(device_config):
    load = getattr(device_config, "load_env_key", None)
    value = load("GITHUB_SECRET") if callable(load) else ""
    return str(value or os.getenv("GITHUB_SECRET", "") or "").strip()


def _cache_path(plugin_instance):
    try:
        return Path(plugin_instance.cache_dir(env_var="GITHUB_CACHE_DIR", leaf="cache", create=True, strip=True)) / CACHE_FILE
    except (AttributeError, OSError, TypeError):
        return None


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    if path is None:
        return
    try:
        temporary = Path(path).with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        logger.debug("Could not cache the GitHub repository: %s", exc)


# --- Page ---------------------------------------------------------------------------------------------

def _mode(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    return "night" if theme.get("mode", (settings or {}).get("themeMode")) == "night" else "day"


def _font(size, bold=False):
    return get_base_ui_font(int(size), bold=bold)


def _fit(draw, text, font, width):
    text = str(text or "")
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "..."


def _wrap(draw, text, font, width, lines):
    """``text`` in at most ``lines`` lines of ``width`` (by word, else by character), the last cut short."""
    words = str(text or "").split()
    out, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not current:
            current = candidate
            continue
        out.append(current)
        current = word
    if current:
        out.append(current)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = _fit(draw, out[-1] + " ...", font, width)
    return [_fit(draw, line, font, width) for line in out]


def _mark(height, ink):
    """GitHub's mark ``height`` px tall in ``ink`` (it is one colour)."""
    try:
        with Image.open(MARK) as source:
            mark = ImageOps.contain(source.convert("RGBA"), (height * 2, height), Image.Resampling.LANCZOS)
    except OSError:
        return None
    tinted = Image.new("RGBA", mark.size, (*ink, 255))
    tinted.putalpha(mark.getchannel("A"))
    return tinted


def _star(draw, centre, radius, fill):
    """A solid five-pointed star."""
    cx, cy = centre
    points = []
    for index in range(10):
        angle = -math.pi / 2 + index * math.pi / 5
        r = radius if index % 2 == 0 else radius * 0.45
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=fill)


def _icon(draw, kind, xy, size, colour, ground):
    """A tile's glyph: GitHub's fork (three nodes, two joined), its open issue (a ring round a dot),
    else the shared flat icon (watchers' people, the push's clock)."""
    x, y = xy
    if kind == "fork":
        r = max(1.5, size * 0.15)
        nodes = ((x + size * 0.22, y + size * 0.18), (x + size * 0.78, y + size * 0.18), (x + size * 0.5, y + size * 0.84))
        draw.line((nodes[0], (x + size * 0.22, y + size * 0.42), (x + size * 0.5, y + size * 0.6), nodes[2]),
                  fill=colour, width=2, joint="curve")
        draw.line((nodes[1], (x + size * 0.78, y + size * 0.42), (x + size * 0.5, y + size * 0.6)), fill=colour,
                  width=2, joint="curve")
        for cx, cy in nodes:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=colour)
        return
    if kind == "issue":
        draw.ellipse((x, y, x + size, y + size), outline=colour, width=max(2, round(size / 7)))
        draw.ellipse((x + size * 0.36, y + size * 0.36, x + size * 0.64, y + size * 0.64), fill=colour)
        return
    draw_icon(draw, {"watch": "users", "push": "clock"}.get(kind, "info"), xy, size, colour, ground)


def render_repository(dimensions, data, settings, now):
    mode = _mode(settings)
    palette, accents = (NIGHT if mode == "night" else DAY), ACCENTS[mode]
    image = Image.new("RGB", PAGE, palette["paper"])
    draw = ImageDraw.Draw(image)
    repo, commit = data.get("repo") or {}, data.get("commit")
    width = PAGE[0]

    # The title as before: GitHub's mark and owner/repository, centred.
    title_font = _font(30, True)
    title = _fit(draw, f"GitHub/{repo.get('full_name') or data.get('repository') or ''}", title_font, width - 120)
    mark = _mark(32, palette["ink"])
    title_w = draw.textlength(title, font=title_font) + (mark.width + 12 if mark else 0)
    x = (width - title_w) / 2
    if mark is not None:
        image.paste(mark, (round(x), 30), mark)
        x += mark.width + 12
    draw.text((x, 46), title, font=title_font, fill=palette["ink"], anchor="lm")

    # What it is: its description, then its language and licence.
    caption = [part for part in (repo.get("language"), repo.get("licence"), repo.get("default_branch")) if part]
    small = _font(15)
    if repo.get("description"):
        draw.text((width / 2, 84), _fit(draw, repo["description"], small, width - 120), font=small,
                  fill=palette["muted"], anchor="mm")
    if caption:
        text = " · ".join(caption)
        text_w = draw.textlength(text, font=small)
        left = (width - text_w - 16) / 2
        colour = LANGUAGE_COLOURS.get(repo.get("language"), palette["muted"])
        if repo.get("language"):
            draw.ellipse((left, 106, left + 10, 116), fill=colour)
        draw.text((left + (16 if repo.get("language") else 8), 111), text, font=small, fill=palette["muted"],
                  anchor="lm")

    # The stars, large.
    star_font = _font(84, True)
    stars = f"{repo.get('stars', 0):,}"
    stars_w = draw.textlength(stars, font=star_font)
    left = (width - stars_w - 96) / 2
    _star(draw, (left + 38, 190), 40, accents["star"])
    draw.text((left + 96, 190), stars, font=star_font, fill=palette["ink"], anchor="lm")

    # Forks, watchers, open issues and pull requests, and the last push, each on its colour's band.
    figures = _figures(repo, now)
    keys = ("fork", "watch", "issue", "push")
    gap, tile_h, top = 12, 74, 262
    tile_w = (width - 96 - gap * (len(figures) - 1)) / len(figures)
    value_font, label_font = _font(26, True), _font(14)
    for index, (label, value) in enumerate(figures):
        left = 48 + index * (tile_w + gap)
        box = (left, top, left + tile_w, top + tile_h)
        colour = accents[keys[index]]
        draw.rounded_rectangle(box, radius=10, fill=palette["panel"], outline=palette["rule"], width=1)
        draw.rounded_rectangle((box[0], box[1], box[2], box[1] + 8), radius=10, fill=colour,
                               corners=(True, True, False, False))
        draw.rectangle((box[0], box[1] + 5, box[2], box[1] + 8), fill=colour)
        draw.text(((box[0] + box[2]) / 2, top + 36), _fit(draw, value, value_font, tile_w - 16), font=value_font,
                  fill=palette["ink"], anchor="mm")
        label_w = draw.textlength(label, font=label_font)
        icon_x = (box[0] + box[2]) / 2 - (label_w + 18) / 2
        _icon(draw, keys[index], (icon_x, top + 54), 13, colour, palette["panel"])
        draw.text((icon_x + 18, top + 61), label, font=label_font, fill=palette["ink"], anchor="lm")

    # The latest commit: its subject, then who made it, when, and its short sha.
    if isinstance(commit, dict) and commit.get("subject"):
        subject_font = _font(18, True)
        lines = _wrap(draw, commit["subject"], subject_font, width - 136, 2)
        box = (48, 356, width - 48, 356 + 44 + 25 * len(lines))
        draw.rounded_rectangle(box, radius=10, fill=palette["panel"], outline=palette["rule"], width=1)
        draw.rounded_rectangle((box[0], box[1], box[0] + 7, box[3]), radius=4, fill=accents["push"])
        label_font = _font(13, True)
        draw.text((box[0] + 20, box[1] + 18), "最新提交", font=label_font, fill=palette["ink"], anchor="lm")
        meta = " · ".join(part for part in (commit.get("author"), ago(commit.get("date"), now), commit.get("sha"))
                          if part)
        draw.text((box[2] - 16, box[1] + 18), _fit(draw, meta, _font(13), box[2] - box[0] - 140), font=_font(13),
                  fill=palette["muted"], anchor="rm")
        for line_index, line in enumerate(lines):
            draw.text((box[0] + 20, box[1] + 46 + line_index * 25), line, font=subject_font, fill=palette["ink"],
                      anchor="lm")

    if tuple(dimensions) != PAGE:
        image = image.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return image
