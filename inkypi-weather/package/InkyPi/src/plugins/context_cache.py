from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _cache_dir() -> Path:
    raw = os.getenv("INKYPI_CONTEXT_CACHE_DIR", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
    else:
        path = Path(__file__).resolve().parent / ".context_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_plugin_id(plugin_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(plugin_id or "").strip())
    return safe.strip("._-") or "unknown"


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def write_context(
    plugin_id: str,
    payload: dict[str, Any],
    *,
    generated_at: Any = None,
    ttl_seconds: int = 3600,
) -> bool:
    now = datetime.now(timezone.utc)
    generated = _parse_time(generated_at) or now
    safe_id = _safe_plugin_id(plugin_id)
    entry = {
        "schema_version": SCHEMA_VERSION,
        "plugin_id": safe_id,
        "generated_at": _to_iso(generated),
        "cached_at": _to_iso(now),
        "ttl_seconds": max(60, int(ttl_seconds or 3600)),
        "payload": payload if isinstance(payload, dict) else {"value": payload},
    }

    path = _cache_dir() / f"{safe_id}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.replace(tmp, path)
        except PermissionError:
            path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
        return True
    except Exception as exc:
        logger.warning("Could not write context cache for %s: %s", safe_id, exc)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def read_contexts(
    plugin_ids: list[str] | tuple[str, ...] | None = None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 86400,
    include_stale: bool = False,
) -> list[dict[str, Any]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    allowed = {_safe_plugin_id(plugin_id) for plugin_id in plugin_ids or []}
    entries: list[dict[str, Any]] = []

    for path in _cache_dir().glob("*.json"):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read context cache %s: %s", path, exc)
            continue

        plugin_id = _safe_plugin_id(entry.get("plugin_id") or path.stem)
        if allowed and plugin_id not in allowed:
            continue
        if entry.get("schema_version") != SCHEMA_VERSION:
            continue

        generated = _parse_time(entry.get("generated_at"))
        cached = _parse_time(entry.get("cached_at")) or generated
        if not generated:
            continue

        age_seconds = max(0, int((current - generated).total_seconds()))
        ttl_seconds = max(60, int(entry.get("ttl_seconds") or 3600))
        stale = age_seconds > ttl_seconds
        if age_seconds > max_age_seconds:
            continue
        if stale and not include_stale:
            continue

        entries.append({
            "plugin_id": plugin_id,
            "generated_at": _to_iso(generated),
            "cached_at": _to_iso(cached or generated),
            "age_seconds": age_seconds,
            "ttl_seconds": ttl_seconds,
            "stale": stale,
            "payload": entry.get("payload") if isinstance(entry.get("payload"), dict) else {},
        })

    entries.sort(key=lambda item: (item["stale"], item["age_seconds"], item["plugin_id"]))
    return entries
