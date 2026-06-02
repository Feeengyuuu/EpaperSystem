import requests
import logging
from datetime import datetime, date, timedelta
from PIL import Image, ImageColor, ImageDraw, ImageFont
from utils.app_utils import get_font

logger = logging.getLogger(__name__)

DEFAULT_COLORS = ['#ebedf0', '#9be9a8', '#40c463', '#30a14e', '#216e39']
COMIC_TOKENS = {
    # Calibrated preview values derived from docs/color-ui-guidelines.md chart IDs.
    # Keep source labels traceable; final production values should be panel-calibrated.
    "paper": {"rgb": (248, 239, 205), "source_label": "25Y PANTONE 100"},
    "panel": {"rgb": (255, 246, 187), "source_label": "50Y PANTONE 101"},
    "ink": {"rgb": (18, 18, 16), "source_label": "PROCESS BLACK"},
    "muted": {"rgb": (94, 82, 64), "source_label": "50Y-50R-25B PANTONE 479"},
    "rule": {"rgb": (18, 18, 16), "source_label": "PROCESS BLACK"},
    "primary_blue": {"rgb": (21, 91, 172), "source_label": "100B-25R PANTONE 285"},
    "accent_yellow": {"rgb": (239, 196, 49), "source_label": "100Y-25R PANTONE 123"},
    "accent_red": {"rgb": (201, 44, 38), "source_label": "100Y-100R PANTONE RED 032"},
    "accent_green": {"rgb": (28, 133, 71), "source_label": "100Y-100B PANTONE 354"},
    "accent_orange": {"rgb": (224, 111, 38), "source_label": "100Y-50R PANTONE ORANGE 021"},
    "inactive": {"rgb": (213, 201, 169), "source_label": "25Y-50R PANTONE 183"},
}
GITHUB_HEATMAP_COLORS = [
    "#ebedf0",  # GitHub inactive
    "#9be9a8",  # GitHub low activity
    "#40c463",  # GitHub medium activity
    "#30a14e",  # GitHub high activity
    "#216e39",  # GitHub peak activity
]

GRAPHQL_QUERY = """
query($username: String!) {
  user(login: $username) {
    login
    name
    bio
    location
    company
    followers { totalCount }
    following { totalCount }
    starredRepositories { totalCount }
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, first: 20, orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes {
        name
        stargazerCount
        forkCount
        primaryLanguage { name color }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            contributionCount
            date
          }
        }
      }
    }
  }
}
"""

def contributions_generate_image(plugin_instance, settings, device_config):
    dimensions = device_config.get_resolution()
    if device_config.get_config("orientation") == "vertical":
        dimensions = dimensions[::-1]

    api_key = device_config.load_env_key("GITHUB_SECRET")
    if not api_key:
        raise RuntimeError("GitHub API Key not configured.")

    colors = normalize_colors(settings.get("contributionColor[]"))
    github_username = settings.get("githubUsername")
    if not github_username:
        raise RuntimeError("GitHub username is required.")

    data = fetch_contributions(github_username, api_key)
    grid, month_positions = parse_contributions(data, colors)
    metrics = calculate_metrics(data)
    profile = extract_profile(data)

    if settings.get("githubRenderer") != "html":
        return render_contributions_fallback(dimensions, github_username, grid, month_positions, metrics, settings, profile)

    template_params = {
        "username": github_username,
        "grid": grid,
        "month_positions": month_positions,
        "metrics": metrics,
        "profile": profile,
        "plugin_settings": settings
    }

    try:
        image = plugin_instance.render_image(
            dimensions,
            "github_contributions.html",
            "github.css",
            template_params
        )
    except Exception as e:
        logger.warning("GitHub HTML render failed, using Pillow fallback: %s", e)
        image = None

    if image is not None:
        return image

    logger.warning("GitHub HTML render returned no image, using Pillow fallback")
    return render_contributions_fallback(dimensions, github_username, grid, month_positions, metrics, settings, profile)

# -------------------------
# Helper functions
# -------------------------

def normalize_colors(colors):
    if isinstance(colors, str):
        colors = [colors]
    if not colors:
        colors = GITHUB_HEATMAP_COLORS

    normalized = []
    for idx, color in enumerate(colors):
        fallback = GITHUB_HEATMAP_COLORS[min(idx, len(GITHUB_HEATMAP_COLORS) - 1)]
        try:
            ImageColor.getrgb(color)
            normalized.append(color)
        except Exception:
            normalized.append(fallback)

    while len(normalized) < len(GITHUB_HEATMAP_COLORS):
        normalized.append(GITHUB_HEATMAP_COLORS[len(normalized)])
    return normalized

def comic_palette(settings=None):
    return {
        "paper": COMIC_TOKENS["paper"]["rgb"],
        "panel": COMIC_TOKENS["panel"]["rgb"],
        "ink": COMIC_TOKENS["ink"]["rgb"],
        "muted": COMIC_TOKENS["muted"]["rgb"],
        "rule": COMIC_TOKENS["rule"]["rgb"],
        "blue": COMIC_TOKENS["primary_blue"]["rgb"],
        "yellow": COMIC_TOKENS["accent_yellow"]["rgb"],
        "red": COMIC_TOKENS["accent_red"]["rgb"],
        "green": COMIC_TOKENS["accent_green"]["rgb"],
        "orange": COMIC_TOKENS["accent_orange"]["rgb"],
        "inactive": COMIC_TOKENS["inactive"]["rgb"],
    }

def render_contributions_fallback(dimensions, username, grid, month_positions, metrics, settings, profile=None):
    profile = profile or {}
    width, height = int(dimensions[0]), int(dimensions[1])
    palette = comic_palette(settings)
    bg_color = palette["paper"]
    text_color = palette["ink"]
    muted_color = palette["muted"]
    line_color = palette["rule"]
    panel_color = palette["panel"]
    accent_color = palette["green"]
    primary_color = palette["blue"]
    yellow_color = palette["yellow"]
    orange_color = palette["orange"]
    red_color = palette["red"]

    image = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(image)

    margin = int_or_default(settings.get("margin"), 5)
    left = max(28, int(width * 0.040)) + int_or_default(settings.get("leftMargin"), margin)
    right = width - max(28, int(width * 0.040)) - int_or_default(settings.get("rightMargin"), margin)
    top = max(20, int(height * 0.045)) + int_or_default(settings.get("topMargin"), margin)
    bottom = height - max(18, int(height * 0.038)) - int_or_default(settings.get("bottomMargin"), margin)

    draw_frame(draw, (left - 12, top - 8, right + 12, bottom + 8), settings, line_color)

    title_font = font("Jost", 38, "bold")
    subtitle_font = font("Jost", 15)
    label_font = font("Jost", 14, "bold")
    small_font = font("Jost", 13)
    tiny_font = font("Jost", 12)
    value_font = font("Jost", 40, "bold")
    chip_value_font = font("Jost", 14, "bold")
    month_font = font("Jost", 13, "bold")

    display_name = profile.get("name") or username
    handle = "@" + (profile.get("login") or username)
    title = f"GitHub / {display_name}"
    header_top = top
    header_bottom = top + 58
    chip_gap = 8
    chip_w = 70
    chip_h = 42
    chip_total_w = chip_w * 3 + chip_gap * 2
    chip_start_x = right - chip_total_w
    title_max_w = chip_start_x - left - 18
    draw.text((left, header_top + 4), fit_text(draw, title, title_font, title_max_w), font=title_font, fill=text_color)
    subtitle_parts = [handle]
    if profile.get("location"):
        subtitle_parts.append(str(profile.get("location")))
    if profile.get("company"):
        subtitle_parts.append(str(profile.get("company")))
    subtitle = "  |  ".join(subtitle_parts)
    draw.text((left, header_top + 45), fit_text(draw, subtitle, subtitle_font, title_max_w), font=subtitle_font, fill=muted_color)

    profile_chip = [
        ("Followers", profile.get("followers", 0)),
        ("Following", profile.get("following", 0)),
        ("Starred", profile.get("starred", 0)),
    ]
    chip_x = chip_start_x
    chip_y = header_top + 2
    for label, value in profile_chip:
        draw_stat_chip(draw, chip_x, chip_y, chip_w, chip_h, label, format_number(value), tiny_font, chip_value_font, text_color, muted_color, panel_color, line_color, yellow_color)
        chip_x += chip_w + chip_gap

    stats_top = header_bottom + 16
    stats_h = 72
    stats = [
        ("Contrib", get_metric_value(metrics, "Contributions")),
        ("Streak", get_metric_value(metrics, "Current Streak")),
        ("Best", get_metric_value(metrics, "Longest Streak")),
        ("Repos", profile.get("repo_total", 0)),
        ("Repo Stars", profile.get("repo_star_total", 0)),
    ]
    stat_accents = [accent_color, primary_color, orange_color, yellow_color, red_color]
    stat_gap = 8
    stat_w = int((right - left - stat_gap * (len(stats) - 1)) / len(stats))
    for idx, (label, value) in enumerate(stats):
        x = left + idx * (stat_w + stat_gap)
        draw.rounded_rectangle((x, stats_top, x + stat_w, stats_top + stats_h), radius=7, fill=panel_color, outline=line_color, width=2)
        label_box = (x + 8, stats_top + 8, x + stat_w - 8, stats_top + 28)
        label_fill = stat_accents[idx % len(stat_accents)]
        draw.rounded_rectangle(label_box, radius=4, fill=label_fill, outline=line_color, width=1)
        draw_centered_text(draw, ((label_box[0] + label_box[2]) // 2, (label_box[1] + label_box[3]) // 2), label, label_font, contrast_text(label_fill))
        draw_centered_text(draw, (x + stat_w // 2, stats_top + 54), format_number(value), value_font, text_color)

    heat_title_y = stats_top + stats_h + 10
    month_y = heat_title_y + 18
    grid_top = month_y + 17
    grid_left = left + 30
    grid_right = right
    grid_bottom = grid_top + 86
    weeks = max(1, len(grid))
    max_days = max((len(week) for week in grid), default=7)
    gap = 2
    cell = int(min(
        (grid_right - grid_left - (weeks - 1) * gap) / weeks,
        (grid_bottom - grid_top - (max_days - 1) * gap) / max_days
    ))
    cell = max(3, cell)

    actual_grid_w = weeks * cell + (weeks - 1) * gap
    x0 = grid_left + max(0, (grid_right - grid_left - actual_grid_w) // 2)
    y0 = grid_top

    draw.text((left, heat_title_y), "Last 12 months", font=label_font, fill=text_color)
    for label in month_positions:
        x = x0 + int(label["index"]) * (cell + gap)
        if x < grid_right - 22:
            draw.text((x, month_y), label["name"], font=month_font, fill=primary_color)

    for week_idx, week in enumerate(grid):
        for day_idx, day in enumerate(week):
            color = get_setting_color({"value": day.get("color")}, "value", GITHUB_HEATMAP_COLORS[0])
            x = x0 + week_idx * (cell + gap)
            y = y0 + day_idx * (cell + gap)
            outline = line_color if day.get("contributionCount", 0) else bg_color
            draw.rounded_rectangle((x, y, x + cell, y + cell), radius=max(1, cell // 5), fill=color, outline=outline)

    lower_top = grid_bottom + 24
    lower_bottom = bottom
    panel_gap = 16
    panel_w = int((right - left - panel_gap) / 2)
    activity_box = (left, lower_top, left + panel_w, lower_bottom)
    repo_box = (left + panel_w + panel_gap, lower_top, right, lower_bottom)
    draw_activity_panel(draw, activity_box, profile, label_font, small_font, tiny_font, text_color, muted_color, panel_color, line_color, accent_color, yellow_color)
    draw_repo_panel(draw, repo_box, profile, label_font, small_font, tiny_font, text_color, muted_color, panel_color, line_color, primary_color, yellow_color)

    return image

def draw_activity_panel(draw, box, profile, label_font, small_font, tiny_font, text_color, muted_color, panel_color, line_color, accent_color, yellow_color):
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=7, fill=panel_color, outline=line_color, width=2)
    draw.rectangle((left, top, right, top + 28), fill=yellow_color, outline=line_color, width=1)
    draw.text((left + 12, top + 8), "Contribution Mix", font=label_font, fill=text_color)
    rows = [
        ("Commits", profile.get("commit_contrib", 0)),
        ("PRs", profile.get("pr_contrib", 0)),
        ("Issues", profile.get("issue_contrib", 0)),
        ("Reviews", profile.get("review_contrib", 0)),
        ("Private", profile.get("restricted_contrib", 0)),
    ]
    max_value = max([value for _, value in rows] + [1])
    y = top + 34
    label_x = left + 12
    bar_left = left + 82
    value_right = right - 14
    bar_right = value_right - 26
    for label, value in rows:
        draw.text((label_x, y - 2), label, font=small_font, fill=muted_color)
        draw.rounded_rectangle((bar_left, y + 2, bar_right, y + 11), radius=4, fill=COMIC_TOKENS["inactive"]["rgb"], outline=line_color, width=1)
        fill_right = bar_left + int((bar_right - bar_left) * (value / max_value))
        if fill_right > bar_left:
            draw.rounded_rectangle((bar_left, y + 2, fill_right, y + 11), radius=4, fill=accent_color)
        draw_right_text(draw, value_right, y - 2, format_number(value), tiny_font, text_color)
        y += 18

def draw_repo_panel(draw, box, profile, label_font, small_font, tiny_font, text_color, muted_color, panel_color, line_color, primary_color, yellow_color):
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=7, fill=panel_color, outline=line_color, width=2)
    draw.rectangle((left, top, right, top + 28), fill=primary_color, outline=line_color, width=1)
    draw.text((left + 12, top + 8), "Top Repositories", font=label_font, fill=contrast_text(primary_color))
    repos = profile.get("top_repos") or []
    if not repos:
        draw.text((left + 12, top + 43), "No public repositories", font=small_font, fill=muted_color)
        return

    name_x = left + 26
    lang_x = right - 128
    stars_right = right - 52
    forks_right = right - 14
    header_y = top + 32
    draw.text((name_x, header_y), "Name", font=tiny_font, fill=text_color)
    draw.text((lang_x, header_y), "Lang", font=tiny_font, fill=text_color)
    draw_right_text(draw, stars_right, header_y, "Stars", tiny_font, text_color)
    draw_right_text(draw, forks_right, header_y, "Forks", tiny_font, text_color)

    y = top + 43
    row_h = 15
    for repo in repos[:5]:
        name = fit_text(draw, repo.get("name") or "-", small_font, lang_x - name_x - 14)
        lang = repo.get("language") or "-"
        stars = format_number(repo.get("stars", 0))
        forks = format_number(repo.get("forks", 0))
        lang_color = repo.get("language_color") or "#999999"
        try:
            lang_rgb = ImageColor.getrgb(lang_color)
        except Exception:
            lang_rgb = muted_color
        draw.ellipse((left + 12, y + 5, left + 20, y + 13), fill=lang_rgb, outline=line_color)
        draw.text((name_x, y), name, font=small_font, fill=text_color)
        draw.text((lang_x, y), fit_text(draw, lang, tiny_font, stars_right - lang_x - 8), font=tiny_font, fill=muted_color)
        draw_right_text(draw, stars_right, y, stars, tiny_font, text_color)
        draw_right_text(draw, forks_right, y, forks, tiny_font, text_color)
        y += row_h

def draw_frame(draw, box, settings, line_color):
    selected = settings.get("selectedFrame")
    if selected == "Rectangle":
        draw.rectangle(box, outline=line_color, width=2)
    elif selected == "Top and Bottom":
        left, top, right, bottom = box
        draw.line((left, top, right, top), fill=line_color, width=2)
        draw.line((left, bottom, right, bottom), fill=line_color, width=2)
    elif selected == "Corner":
        left, top, right, bottom = box
        length = max(18, int((right - left) * 0.06))
        for x0, x1 in ((left, left + length), (right, right - length)):
            draw.line((x0, top, x1, top), fill=line_color, width=2)
            draw.line((x0, bottom, x1, bottom), fill=line_color, width=2)
        for y0, y1 in ((top, top + length), (bottom, bottom - length)):
            draw.line((left, y0, left, y1), fill=line_color, width=2)
            draw.line((right, y0, right, y1), fill=line_color, width=2)

def draw_centered_text(draw, center, text, text_font, fill):
    bbox = draw.textbbox((0, 0), text, font=text_font)
    x = center[0] - (bbox[2] - bbox[0]) / 2 - bbox[0]
    y = center[1] - (bbox[3] - bbox[1]) / 2 - bbox[1]
    draw.text((x, y), text, font=text_font, fill=fill)

def draw_right_text(draw, right, y, text, text_font, fill):
    text = str(text or "")
    bbox = draw.textbbox((0, 0), text, font=text_font)
    draw.text((right - (bbox[2] - bbox[0]) - bbox[0], y), text, font=text_font, fill=fill)

def draw_stat_chip(draw, x, y, w, h, label, value, label_font, value_font, text_color, muted_color, panel_color, line_color, accent_color):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=7, fill=panel_color, outline=line_color, width=2)
    draw.rectangle((x + 2, y + 2, x + w - 2, y + 8), fill=accent_color)
    draw_centered_text(draw, (x + w // 2, y + 14), label, label_font, muted_color)
    draw_centered_text(draw, (x + w // 2, y + 32), value, value_font, text_color)

def contrast_text(fill_color):
    try:
        rgb = ImageColor.getrgb(fill_color)
    except Exception:
        rgb = fill_color
    luminance = rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114
    return COMIC_TOKENS["ink"]["rgb"] if luminance > 150 else COMIC_TOKENS["paper"]["rgb"]

def fit_text(draw, text, text_font, max_width):
    text = str(text or "")
    if draw.textbbox((0, 0), text, font=text_font)[2] <= max_width:
        return text
    suffix = "..."
    for end in range(len(text), 0, -1):
        candidate = text[:end].rstrip() + suffix
        if draw.textbbox((0, 0), candidate, font=text_font)[2] <= max_width:
            return candidate
    return suffix

def format_number(value):
    try:
        value = int(value or 0)
    except Exception:
        value = 0
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k".replace(".0k", "k")
    return str(value)

def get_metric_value(metrics, title):
    for metric in metrics:
        if metric.get("title") == title:
            return metric.get("value", 0)
    return 0

def get_setting_color(settings, key, default):
    try:
        return ImageColor.getrgb(settings.get(key) or default)
    except Exception:
        return ImageColor.getrgb(default)

def blend_color(fg, bg, amount):
    return tuple(int(bg[idx] + (fg[idx] - bg[idx]) * amount) for idx in range(3))

def int_or_default(value, default):
    try:
        if value in (None, ""):
            return int(default)
        return int(value)
    except Exception:
        return int(default)

def font(name, size, weight="normal"):
    return get_font(name, int(size), weight) or ImageFont.load_default()

def fetch_contributions(username, api_key):
    url = "https://api.github.com/graphql"
    headers = {"Authorization": f"Bearer {api_key}"}
    variables = {"username": username}
    resp = requests.post(url, json={"query": GRAPHQL_QUERY, "variables": variables}, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        messages = ", ".join(str(error.get("message", error)) for error in data["errors"])
        raise RuntimeError(f"GitHub API returned errors: {messages}")
    if not data.get("data", {}).get("user"):
        raise RuntimeError(f"GitHub user '{username}' was not found.")
    return data

def extract_profile(data):
    user = data.get("data", {}).get("user") or {}
    contributions = user.get("contributionsCollection") or {}
    repositories = user.get("repositories") or {}
    repo_nodes = repositories.get("nodes") or []
    top_repos = []
    for node in repo_nodes[:5]:
        language = node.get("primaryLanguage") or {}
        top_repos.append({
            "name": node.get("name") or "-",
            "stars": node.get("stargazerCount") or 0,
            "forks": node.get("forkCount") or 0,
            "language": language.get("name") or "-",
            "language_color": language.get("color") or "#999999",
        })

    return {
        "login": user.get("login") or "",
        "name": user.get("name") or "",
        "bio": user.get("bio") or "",
        "location": user.get("location") or "",
        "company": user.get("company") or "",
        "followers": (user.get("followers") or {}).get("totalCount") or 0,
        "following": (user.get("following") or {}).get("totalCount") or 0,
        "starred": (user.get("starredRepositories") or {}).get("totalCount") or 0,
        "repo_total": repositories.get("totalCount") or 0,
        "repo_star_total": sum(repo.get("stargazerCount") or 0 for repo in repo_nodes),
        "top_repos": top_repos,
        "commit_contrib": contributions.get("totalCommitContributions") or 0,
        "issue_contrib": contributions.get("totalIssueContributions") or 0,
        "pr_contrib": contributions.get("totalPullRequestContributions") or 0,
        "review_contrib": contributions.get("totalPullRequestReviewContributions") or 0,
        "restricted_contrib": contributions.get("restrictedContributionsCount") or 0,
    }

def parse_contributions(data, colors):
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]

    grid = [list(week["contributionDays"]) for week in weeks]
    max_contrib = max(day["contributionCount"] for week in grid for day in week)

    def get_color(count):
        if max_contrib == 0 or count == 0:
            return colors[0]
        level = int((count / max_contrib) * (len(colors) - 1))
        return colors[max(1, level)]

    for week in grid:
        for day in week:
            day["color"] = get_color(day["contributionCount"])

    month_positions = []
    seen_months = set()
    for i, week in enumerate(weeks):
        first_day = week["contributionDays"][0]["date"]
        dt = datetime.strptime(first_day, "%Y-%m-%d")
        month_year = f"{dt.strftime('%b')}-{dt.year}"
        if month_year not in seen_months:
            month_positions.append({"name": dt.strftime("%b"), "index": i})
            seen_months.add(month_year)

    if month_positions:
        month_positions.pop(0)

    return grid, month_positions

def calculate_metrics(data):
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [day for week in weeks for day in week["contributionDays"]]
    days = sorted(days, key=lambda d: d["date"])

    total = sum(day["contributionCount"] for day in days)
    streak, longest_streak, current_streak = 0, 0, 0
    today = date.today()
    yesterday = today - timedelta(days=1)
    in_current_streak = False

    for day in days:
        day_date = date.fromisoformat(day["date"])
        if day["contributionCount"] > 0:
            streak += 1
            longest_streak = max(longest_streak, streak)
            if day_date in (today, yesterday) or in_current_streak:
                current_streak = streak
                in_current_streak = True
        else:
            streak = 0
            in_current_streak = False

    return [
        {"title": "Contributions", "value": total},
        {"title": "Current Streak", "value": current_streak},
        {"title": "Longest Streak", "value": longest_streak},
    ]
