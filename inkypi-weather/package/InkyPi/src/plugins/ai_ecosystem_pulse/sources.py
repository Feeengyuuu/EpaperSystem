from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Iterable

from utils.http_client import get_http_session


SKILLS_URL = "https://www.skills.sh/trending"
# Only the fields the boards read (about 3.5 KB for three models, against 8 KB with full=true and its
# file lists): the metrics, each model's parameter count (safetensors) and the inference providers
# serving it.
HF_MODEL_FIELDS = ("trendingScore", "likes", "downloads", "pipeline_tag", "safetensors", "inferenceProviderMapping",
                   "author", "createdAt", "lastModified", "gated")
HF_MODELS_URL = "https://huggingface.co/api/models?sort=trendingScore&limit=3" + "".join(
    f"&expand%5B%5D={field}" for field in HF_MODEL_FIELDS)
# The trending Spaces (the apps people run on the Hub), with their cards' titles and descriptions; a
# dozen, as some are left out (about 5 KB).
HF_SPACES_URL = ("https://huggingface.co/api/spaces?sort=trendingScore&limit=12"
                 "&expand%5B%5D=cardData&expand%5B%5D=likes&expand%5B%5D=trendingScore")
# Spaces that are not for a wall display: NSFW or "uncensored" generators, face and head swaps,
# deepfakes and undressing tools, and crude names.
UNSUITABLE_SPACE = re.compile(
    r"nsfw|uncensor|unfilter|face[\s_-]*swap|head[\s_-]*swap|deep[\s_-]*fake|nudi|nude|undress|porn|hentai|"
    r"erotic|lewd|(?<![a-z0-9])(?:xxx|18[+]|r-?18)(?![a-z0-9])|onlyfans|wank|adult",
    re.IGNORECASE,
)
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
            "params": model_parameters(raw.get("safetensors")),
            "providers": live_providers(raw.get("inferenceProviderMapping")),
        })
        if len(rows) >= int(limit):
            break
    if not rows:
        raise RuntimeError("Hugging Face returned no usable trending models")
    return rows


def model_parameters(safetensors):
    """A model's parameter count from its safetensors summary, or None when it has none."""
    if not isinstance(safetensors, dict):
        return None
    total = _required_integer(safetensors.get("total"))
    return total or None


# A model's task in a few characters, so its size and providers fit beside it.
PIPELINE_TAGS = {
    "text-generation": "文本生成", "image-text-to-text": "图文理解", "automatic-speech-recognition": "语音识别",
    "text-to-image": "文生图", "image-to-image": "图像编辑", "text-to-speech": "语音合成",
    "text-to-video": "文生视频", "image-to-video": "图生视频", "feature-extraction": "向量嵌入",
    "sentence-similarity": "句向量", "any-to-any": "全模态", "image-to-3d": "图生 3D", "text-to-3d": "文生 3D",
    "audio-text-to-text": "音频理解", "video-text-to-text": "视频理解", "image-classification": "图像分类",
    "object-detection": "目标检测", "image-segmentation": "图像分割", "translation": "翻译",
    "summarization": "摘要", "text-classification": "文本分类", "token-classification": "序列标注",
    "question-answering": "问答", "depth-estimation": "深度估计", "text-ranking": "重排序",
    "reinforcement-learning": "强化学习", "robotics": "机器人", "audio-to-audio": "音频处理",
    "visual-document-retrieval": "文档检索", "zero-shot-image-classification": "零样本分类",
}


def pipeline_label(tag):
    """A model's task as the boards print it: in Chinese when it is a common one."""
    tag = str(tag or "").strip() or "model"
    return PIPELINE_TAGS.get(tag, tag)


def parameter_text(count):
    """A parameter count as people say it: 1728801536 -> "1.7B", 763205315794 -> "763B", 350000000 -> "350M"."""
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        return ""
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if count >= scale:
            value = count / scale
            return f"{value:.1f}".rstrip("0").rstrip(".") + suffix if value < 100 else f"{value:.0f}{suffix}"
    return ""


def model_facts(model):
    """A model's size and how many inference providers serve it: "1.7B · 4 家推理"."""
    model = model if isinstance(model, dict) else {}
    providers = model.get("providers")
    parts = [parameter_text(model.get("params")),
             f"{len(providers)} 家推理" if isinstance(providers, list) and providers else ""]
    return " · ".join(part for part in parts if part)


def live_providers(mapping):
    """The inference providers serving a model live (a list of mappings, or the older dict by provider
    name), or None when the Hub did not say."""
    if isinstance(mapping, dict):
        mapping = [{"provider": name, **(value if isinstance(value, dict) else {})} for name, value in mapping.items()]
    if not isinstance(mapping, list):
        return None
    names = []
    for entry in mapping:
        if isinstance(entry, dict) and entry.get("status", "live") == "live":
            name = str(entry.get("provider") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def normalize_hf_spaces(payload, limit=3):
    """The trending Spaces fit for the page (``UNSUITABLE_SPACE`` ones left out), each with its card's
    title and one-line description."""
    rows = []
    for raw in payload if isinstance(payload, list) else []:
        if not isinstance(raw, dict):
            continue
        space_id = str(raw.get("id") or "").strip()
        likes = _required_integer(raw.get("likes"))
        if "/" not in space_id or likes is None:
            continue
        card = raw.get("cardData") if isinstance(raw.get("cardData"), dict) else {}
        title = " ".join(str(card.get("title") or space_id.split("/", 1)[1]).split())[:60]
        description = " ".join(str(card.get("short_description") or "").split())[:120]
        tags = card.get("tags") if isinstance(card.get("tags"), list) else []
        if UNSUITABLE_SPACE.search(" ".join([space_id, title, description, *map(str, tags)])):
            continue
        rows.append({
            "id": space_id,
            "title": title,
            "description": description,
            "likes": likes,
            "trending_score": _required_integer(raw.get("trendingScore")) or 0,
            "url": f"https://huggingface.co/spaces/{space_id}",
        })
        if len(rows) >= int(limit):
            break
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


def fetch_spaces(session=None):
    session = session or get_http_session()
    response = session.get(HF_SPACES_URL, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    rows = normalize_hf_spaces(response.json(), limit=3)
    if not rows:
        raise RuntimeError("Hugging Face returned no suitable trending Spaces")
    return rows


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
