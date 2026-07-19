from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Iterable

from utils.http_client import get_http_session


SKILLS_URL = "https://www.skills.sh/trending"
HF_MODELS_URL = "https://huggingface.co/api/models?sort=trendingScore&limit=3&full=true"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_TOPICS = ("agent-skills", "ai-agents", "mcp")
REQUEST_TIMEOUT = (4, 15)
USER_AGENT = "InkyPi AiEcosystemPulse/1.0"
_ANCHOR_RE = re.compile(
    r'<a[^>]+href="(?P<path>/[^"?#]+/[^"?#]+/[^"?#]+)"[^>]*>(?P<body>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_INSTALL_RE = re.compile(r"^(?P<number>\d+(?:\.\d+)?)(?P<suffix>[KMB]?)$", re.IGNORECASE)


def _integer(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _required_integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[+]?[0-9]+", value.strip()):
        parsed = int(value.strip())
    else:
        return None
    return parsed if parsed >= 0 else None


def compact_count_to_int(value):
    text = str(value or "").strip().upper().replace(",", "")
    match = _INSTALL_RE.fullmatch(text)
    if not match:
        return 0
    scale = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group("suffix")]
    return int(float(match.group("number")) * scale)


def parse_skills_html(html, limit=6):
    rows = []
    for match in _ANCHOR_RE.finditer(str(html or "")):
        text = unescape(_TAG_RE.sub(" ", match.group("body")))
        parts = re.sub(r"\s+", " ", text).strip().split(" ")
        if len(parts) < 4 or not parts[0].isdigit() or not _INSTALL_RE.fullmatch(parts[-1]):
            continue
        source = parts[-2]
        name = " ".join(parts[1:-2]).strip()
        if not name or "/" not in source:
            continue
        rows.append({
            "rank": int(parts[0]),
            "name": name,
            "source": source,
            "installs": compact_count_to_int(parts[-1]),
            "installs_display": parts[-1],
            "url": f"https://www.skills.sh{match.group('path')}",
        })
        if len(rows) >= int(limit):
            break
    if not rows:
        raise RuntimeError("skills.sh leaderboard contained no usable rows")
    return rows


def normalize_hf_models(payload, limit=3):
    rows = []
    for raw in payload if isinstance(payload, list) else []:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get("id") or "").strip()
        if not model_id:
            continue
        metrics = [_required_integer(raw.get(key)) for key in ("trendingScore", "likes", "downloads")]
        if any(value is None for value in metrics):
            continue
        author = raw.get("author")
        tags = raw.get("tags")
        rows.append({
            "id": model_id,
            "author": author.strip() if isinstance(author, str) and author.strip() else model_id.split("/", 1)[0],
            "pipeline_tag": (
                raw["pipeline_tag"].strip()
                if isinstance(raw.get("pipeline_tag"), str) and raw["pipeline_tag"].strip()
                else "model"
            ),
            "trending_score": metrics[0],
            "likes": metrics[1],
            "downloads_30d": metrics[2],
            "created_at": raw.get("createdAt"),
            "last_modified": raw.get("lastModified"),
            "gated": raw.get("gated", False),
            "tags": [str(tag) for tag in tags[:3]] if isinstance(tags, list) else [],
            "url": f"https://huggingface.co/{model_id}",
        })
        if len(rows) >= int(limit):
            break
    if not rows:
        raise RuntimeError("Hugging Face returned no usable trending models")
    return rows


def _normalize_github_repo(raw):
    if not isinstance(raw, dict):
        return None
    full_name = raw.get("full_name")
    if not isinstance(full_name, str) or not full_name.strip():
        return None
    stars = _required_integer(raw.get("stargazers_count"))
    if stars is None:
        return None
    if "forks_count" in raw:
        forks = _required_integer(raw.get("forks_count"))
        if forks is None:
            return None
    else:
        forks = 0
    repo_id = raw.get("id")
    if repo_id is not None:
        repo_id = _required_integer(repo_id)
        if repo_id is None:
            return None
    owner = raw.get("owner")
    owner = owner if isinstance(owner, dict) else {}
    topics = raw.get("topics")
    topics = topics if isinstance(topics, list) else []

    def text_field(name, default=""):
        value = raw.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else default

    return {
        "id": repo_id,
        "full_name": full_name.strip(),
        "description": text_field("description"),
        "url": text_field("html_url"),
        "stars": stars,
        "forks": forks,
        "language": text_field("language", "Other"),
        "topics": [str(topic) for topic in topics if isinstance(topic, (str, int, float))],
        "owner_avatar_url": (
            owner["avatar_url"].strip()
            if isinstance(owner.get("avatar_url"), str)
            else ""
        ),
        "created_at": raw.get("created_at"),
        "pushed_at": raw.get("pushed_at"),
        "updated_at": raw.get("updated_at"),
    }


def merge_github_candidates(groups: Iterable[list[dict]]):
    merged = {}
    for group in groups:
        for raw in group or []:
            row = _normalize_github_repo(raw)
            if row is None:
                continue
            key = f"id:{row['id']}" if row["id"] is not None else f"name:{row['full_name'].casefold()}"
            previous = merged.get(key)
            if previous is None or len(row["description"]) + len(row["topics"]) > len(previous["description"]) + len(previous["topics"]):
                merged[key] = row
    if not merged:
        raise RuntimeError("GitHub returned no usable repository rows")
    return list(merged.values())


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def stars_24h(history, current_stars, now):
    now_utc = now.astimezone(timezone.utc)
    candidates = []
    for point in history or []:
        captured = _parse_time(point.get("captured_at"))
        if captured is None:
            continue
        age_hours = (now_utc - captured).total_seconds() / 3600
        if 20 <= age_hours <= 32:
            candidates.append((abs(age_hours - 24), _integer(point.get("stars"))))
    if not candidates:
        return None
    baseline = min(candidates, key=lambda item: item[0])[1]
    return max(0, _integer(current_stars) - baseline)


def record_star_snapshot(history, current_stars, now):
    now_utc = now.astimezone(timezone.utc)
    cutoff = now_utc - timedelta(days=8)
    points = {}
    for point in history or []:
        captured = _parse_time(point.get("captured_at"))
        if captured is not None and cutoff <= captured <= now_utc:
            points[captured] = {"captured_at": captured.isoformat(), "stars": _integer(point.get("stars"))}
    points[now_utc] = {"captured_at": now_utc.isoformat(), "stars": _integer(current_stars)}

    recent_by_hour = {}
    older_by_day = {}
    for captured, point in sorted(points.items()):
        if now_utc - captured <= timedelta(hours=32):
            recent_by_hour[captured.replace(minute=0, second=0, microsecond=0)] = point
        else:
            older_by_day[captured.date()] = point
    return sorted(
        [*older_by_day.values(), *recent_by_hour.values()],
        key=lambda point: point["captured_at"],
    )


def fetch_skills(session=None):
    session = session or get_http_session()
    response = session.get(SKILLS_URL, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return parse_skills_html(response.text, limit=6)


def fetch_huggingface(session=None):
    session = session or get_http_session()
    response = session.get(HF_MODELS_URL, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return normalize_hf_models(response.json(), limit=3)


def fetch_github(token="", session=None):
    session = session or get_http_session()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    groups = []
    for topic in GITHUB_TOPICS:
        response = session.get(
            GITHUB_SEARCH_URL,
            params={
                "q": f"topic:{topic} archived:false fork:false",
                "sort": "updated",
                "order": "desc",
                "per_page": 20,
            },
            timeout=REQUEST_TIMEOUT,
            headers=headers,
        )
        response.raise_for_status()
        groups.append((response.json() or {}).get("items") or [])
    return merge_github_candidates(groups)
