import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

SEARCH_URL = "https://openlibrary.org/search.json"
CACHE_VERSION = "open-library-book-metadata-v1"
DEFAULT_CACHE_DAYS = 30
REQUEST_HEADERS = {
    "User-Agent": "InkyPi Chinese Literature Clock/1.0 (local e-paper display)"
}
SEARCH_FIELDS = ",".join((
    "key",
    "title",
    "author_name",
    "first_publish_year",
    "language",
    "cover_i",
    "edition_count",
    "subject",
    "publisher",
    "isbn",
    "ia",
))


def lookup_book_metadata(
    title: str,
    author: str = "",
    cache_dir: str | Path | None = None,
    cache_days: int = DEFAULT_CACHE_DAYS,
    session: Any = None,
    now: datetime | None = None,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    title = (title or "").strip()
    author = (author or "").strip()
    if not title:
        return None

    now = now or datetime.now(timezone.utc)
    cache_dir = Path(cache_dir or ".open_library_cache")
    cache_path = cache_dir / "books.json"
    cache_key = _cache_key(title, author)
    cache = _read_cache(cache_path)
    entry = (cache.get("entries") or {}).get(cache_key)

    if not force_refresh and _entry_is_fresh(entry, now, cache_days):
        payload = entry.get("payload")
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["from_cache"] = True
            return payload
        return None

    try:
        payload = _fetch_metadata(title, author, session=session)
    except Exception as exc:
        logger.warning("Open Library lookup failed for %s / %s: %s", title, author, exc)
        if isinstance(entry, dict) and isinstance(entry.get("payload"), dict):
            stale = dict(entry["payload"])
            stale["from_cache"] = True
            stale["stale"] = True
            return stale
        return None

    _write_entry(cache_path, cache, cache_key, now, payload)
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["from_cache"] = False
        return payload
    return None


def _fetch_metadata(title: str, author: str, session: Any = None) -> dict[str, Any] | None:
    session = session or requests
    params = {
        "title": title,
        "fields": SEARCH_FIELDS,
        "limit": 10,
        "lang": "zh",
    }
    if author:
        params["author"] = author

    response = session.get(
        SEARCH_URL,
        params=params,
        headers=REQUEST_HEADERS,
        timeout=6,
    )
    response.raise_for_status()
    payload = response.json()
    docs = payload.get("docs") if isinstance(payload, dict) else None
    if not docs:
        return None

    best = _best_doc(docs, title, author)
    if not best:
        return None
    return _metadata_from_doc(best)


def _best_doc(docs: list[dict[str, Any]], title: str, author: str) -> dict[str, Any] | None:
    scored = [(_score_doc(doc, title, author), doc) for doc in docs if isinstance(doc, dict)]
    scored = [(score, doc) for score, doc in scored if score > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _score_doc(doc: dict[str, Any], title: str, author: str) -> int:
    score = 0
    wanted_title = _normalize(title)
    found_title = _normalize(str(doc.get("title") or ""))

    if found_title == wanted_title:
        score += 90
    elif wanted_title and (wanted_title in found_title or found_title in wanted_title):
        score += 45
    else:
        score -= 40

    wanted_author = _normalize(author)
    authors = [_normalize(str(item)) for item in _as_list(doc.get("author_name"))]
    if wanted_author and wanted_author in authors:
        score += 45
    elif wanted_author and any(wanted_author in item or item in wanted_author for item in authors):
        score += 22

    languages = {str(item).lower() for item in _as_list(doc.get("language"))}
    if {"chi", "zho", "zh", "cmn"} & languages:
        score += 20

    if doc.get("cover_i"):
        score += 8

    edition_count = _safe_int(doc.get("edition_count"))
    if edition_count:
        score += min(edition_count, 30) // 3

    return score


def _metadata_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    work_key = str(doc.get("key") or "")
    if work_key and not work_key.startswith("/"):
        work_key = f"/works/{work_key}" if work_key.startswith("OL") else f"/{work_key}"

    cover_id = _safe_int(doc.get("cover_i"))
    return {
        "title": str(doc.get("title") or "").strip(),
        "authors": [str(item).strip() for item in _as_list(doc.get("author_name")) if str(item).strip()],
        "first_publish_year": _safe_int(doc.get("first_publish_year")),
        "edition_count": _safe_int(doc.get("edition_count")),
        "languages": [str(item) for item in _as_list(doc.get("language"))],
        "cover_id": cover_id,
        "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else "",
        "publisher": _first_text(doc.get("publisher")),
        "subjects": [str(item).strip() for item in _as_list(doc.get("subject"))[:6] if str(item).strip()],
        "work_key": work_key,
        "open_library_url": f"https://openlibrary.org{work_key}" if work_key else "",
        "source": "Open Library",
    }


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict) and payload.get("version") == CACHE_VERSION:
            payload.setdefault("entries", {})
            return payload
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Could not read Open Library cache %s: %s", path, exc)
    return {"version": CACHE_VERSION, "entries": {}}


def _write_entry(path: Path, cache: dict[str, Any], key: str, now: datetime, payload: dict[str, Any] | None) -> None:
    cache.setdefault("entries", {})[key] = {
        "fetched_at": now.astimezone(timezone.utc).isoformat(),
        "payload": payload,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Could not write Open Library cache %s: %s", path, exc)


def _entry_is_fresh(entry: dict[str, Any] | None, now: datetime, cache_days: int) -> bool:
    if not isinstance(entry, dict):
        return False
    fetched_at = _parse_datetime(entry.get("fetched_at"))
    if not fetched_at:
        return False
    return now.astimezone(timezone.utc) - fetched_at < timedelta(days=max(1, cache_days))


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cache_key(title: str, author: str) -> str:
    raw = f"{_normalize(title)}|{_normalize(author)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)
    return value


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_text(value: Any) -> str:
    for item in _as_list(value):
        text = str(item or "").strip()
        if text:
            return text
    return ""


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
