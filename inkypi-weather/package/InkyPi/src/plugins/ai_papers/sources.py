"""Hugging Face's daily papers for AI 论文日报: the papers the community picked today and yesterday,
ranked by their upvotes.

Hugging Face Papers (huggingface.co/papers) lists each day's arXiv papers people submitted, with
the community's upvotes, comments, the paper's GitHub stars, the organisation behind it (its name
and the avatar it shows on the Hub, its logo), a one-sentence AI summary with keywords, and the
teaser images the authors uploaded. A paper without an uploaded still gets its first figure from
arXiv's HTML version. Every fetcher returns a small normalised dict.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from utils.http_client import get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi AIPapers/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (3, 12)
DAILY_PAPERS = "https://huggingface.co/api/daily_papers"
ARXIV_HTML = "https://arxiv.org/html/{id}v1"
# A day's list runs to about 50 papers (340 KB); arXiv's HTML version of a paper to about 750 KB.
LIST_MAX_BYTES = 1536 * 1024
ARXIV_MAX_BYTES = 2048 * 1024
# The days the page ranks together: today's list fills through the day, yesterday's has its votes.
DAYS = 2
IMAGE_HOSTS = ("cdn-uploads.huggingface.co", "cdn-avatars.huggingface.co", "arxiv.org")
STILL = re.compile(r"\.(?:png|jpe?g|gif|webp)$", re.I)
ARXIV_ID = re.compile(r"^\d{4}\.\d{4,6}$")
# arXiv's figures are the paper's numbered figures (a lab's logo in the title block is not one).
FIGURE = re.compile(r'<figure[^>]*class="ltx_figure[^"]*"[^>]*>(.*?)</figure>', re.I | re.S)
FIGURE_IMAGE = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
NOT_A_FIGURE = re.compile(r"logo|icon|badge", re.I)
# A figure over the thumbnail loader's size cap is skipped, so the next ones stand by.
FIGURE_CANDIDATES = 3


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def safe_image_url(value):
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in IMAGE_HOSTS:
        return None
    return url


def list_days(now):
    """The UTC dates Hugging Face files the papers under, today's first."""
    today = now.astimezone(timezone.utc).date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(DAYS)]


def normalize_paper(raw, day):
    """One listed paper, or None without an arXiv id and a title."""
    paper = raw.get("paper") if isinstance(raw, dict) else None
    if not isinstance(paper, dict):
        return None
    paper_id = str(paper.get("id") or "").strip()
    title = _clean(paper.get("title") or raw.get("title"), 300)
    if not ARXIV_ID.match(paper_id) or not title:
        return None
    organization = paper.get("organization") or raw.get("organization") or {}
    authors = [_clean(author.get("name"), 60) for author in paper.get("authors") or []
               if isinstance(author, dict) and not author.get("hidden") and author.get("name")]
    stills = [url for url in paper.get("mediaUrls") or [] if safe_image_url(url) and STILL.search(urlsplit(url).path)]
    summary = _clean(paper.get("ai_summary"), 400) or _clean(paper.get("summary"), 400)
    return {
        "id": paper_id,
        "day": day,
        "title": title,
        "summary": summary,
        "keywords": [_clean(word, 40) for word in (paper.get("ai_keywords") or [])[:6] if str(word or "").strip()],
        "upvotes": _int(paper.get("upvotes")),
        "comments": _int(raw.get("numComments")),
        "stars": _int(paper.get("githubStars")),
        "organization": _clean(organization.get("fullname") or organization.get("name"), 80),
        "org_avatar": safe_image_url(organization.get("avatar")),
        "authors": authors[:8],
        "author_count": len(authors),
        "image": stills[0] if stills else None,
        "published": str(paper.get("publishedAt") or "")[:10],
    }


def _get_json(url, context, *, params=None):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", url, context=context, timeout=REQUEST_TIMEOUT, max_bytes=LIST_MAX_BYTES, params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    context.raise_if_cancelled()
    return response.data


def fetch_day(day, context):
    """One day's papers as Hugging Face lists them."""
    payload = _get_json(DAILY_PAPERS, context, params={"date": day, "limit": 100})
    papers = [paper for paper in (normalize_paper(raw, day) for raw in payload if isinstance(payload, list))
              if paper is not None] if isinstance(payload, list) else []
    return {"day": day, "papers": papers}


def valid_day(data):
    return isinstance((data or {}).get("papers"), list)


def ranked(days):
    """The days' papers, each once (its latest listing), most upvoted first."""
    seen, papers = set(), []
    for data in days:
        for paper in (data or {}).get("papers") or []:
            if paper["id"] not in seen:
                seen.add(paper["id"])
                papers.append(paper)
    return sorted(papers, key=lambda paper: (-paper["upvotes"], -paper["comments"], paper["id"]))


def fetch_arxiv_figure(paper_id, context):
    """The first few figures of a paper's arXiv HTML version, in order (a logo is not one)."""
    if not ARXIV_ID.match(str(paper_id)):
        raise ValueError("not an arXiv id")
    context.raise_if_cancelled()
    page = ARXIV_HTML.format(id=paper_id)
    response = get_http_client().request_text(
        "GET", page, context=context, timeout=REQUEST_TIMEOUT, max_bytes=ARXIV_MAX_BYTES,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    context.raise_if_cancelled()
    urls = []
    for block in FIGURE.findall(response.data or ""):
        for source in FIGURE_IMAGE.findall(block):
            url = safe_image_url(urljoin(page, source))
            if url and not NOT_A_FIGURE.search(source) and STILL.search(urlsplit(url).path) and url not in urls:
                urls.append(url)
        if len(urls) >= FIGURE_CANDIDATES:
            break
    return {"urls": urls[:FIGURE_CANDIDATES]}


def valid_figure(data):
    return isinstance(data, dict) and isinstance(data.get("urls"), list)
