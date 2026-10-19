"""Steam Simplified Chinese store names for offers; English stays when Steam has none."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.atomic_file import atomic_write_json
from utils.plugin_cache import parse_datetime


# Keyless batch lookup: one request names every offer on the page.
ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
NAME_TTL = timedelta(days=30)
MAX_NAMES = 200
MAX_NAME_LENGTH = 200
MAX_RESPONSE_BYTES = 256 * 1024
MAX_CACHE_BYTES = 64 * 1024
SCHEMA_VERSION = 1
_TRADEMARKS = {0x2122: None, 0x00AE: None, 0x00A9: None}


def chinese_title(value):
    """A cleaned store name that contains Chinese characters, otherwise ""."""
    if not isinstance(value, str):
        return ""
    name = " ".join(value.translate(_TRADEMARKS).split())
    if not name or len(name) > MAX_NAME_LENGTH:
        return ""
    # CJK Unified Ideographs and Extension A; an untranslated English name has none.
    return name if any(0x3400 <= ord(char) <= 0x9FFF for char in name) else ""


def _app_id(value):
    text = str(value or "")
    return text if text.isdecimal() and 0 < len(text) <= 12 else ""


class ChineseNameCache:
    """Remembers both found and absent Chinese names so most refreshes send nothing."""

    def __init__(self, path, *, http, user_agent):
        self.path = Path(path)
        self.http = http
        self.user_agent = user_agent

    def _read(self):
        try:
            if self.path.is_symlink() or self.path.stat().st_size > MAX_CACHE_BYTES:
                return {}
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            names = envelope.get("names") if isinstance(envelope, dict) and envelope.get("schema") == SCHEMA_VERSION else None
            if not isinstance(names, dict):
                return {}
            return {app_id: {"name": chinese_title(entry.get("name")), "checked_at": entry.get("checked_at")}
                    for app_id, entry in names.items()
                    if _app_id(app_id) and isinstance(entry, dict) and parse_datetime(entry.get("checked_at"))}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def _lookup(self, app_ids, context):
        query = {"ids": [{"appid": int(app_id)} for app_id in app_ids],
                 "context": {"language": "schinese", "country_code": "US"}, "data_request": {}}
        result = self.http.request_json(
            "GET", ITEMS_URL, params={"input_json": json.dumps(query, separators=(",", ":"))},
            context=context, timeout=8, max_bytes=MAX_RESPONSE_BYTES,
            headers={"User-Agent": self.user_agent}, allow_redirects=False,
        )
        items = (result.data or {}).get("response", {}).get("store_items") if isinstance(result.data, dict) else None
        if not isinstance(items, list):
            raise ValueError("unexpected Steam store items response")
        found = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            app_id = _app_id(item.get("appid"))
            # Unlisted or unknown apps (success != 1) simply have no Chinese name.
            if app_id in app_ids:
                found[app_id] = chinese_title(item.get("name")) if item.get("success") == 1 else ""
        # Apps Steam omitted are answered too, so they are not asked again every refresh.
        return {app_id: found.get(app_id, "") for app_id in app_ids}

    def names_for(self, app_ids, *, now, context=None):
        """Return {app_id: chinese name or ""}; a failed lookup only skips the overlay."""
        wanted = list(dict.fromkeys(app_id for app_id in map(_app_id, app_ids) if app_id))
        cache = self._read()
        due = [app_id for app_id in wanted
               if app_id not in cache or now - _aware(parse_datetime(cache[app_id]["checked_at"])) >= NAME_TTL]
        if due:
            try:
                fresh = self._lookup(due, context)
            except (TaskCancelled, TaskDeadlineExceeded):
                raise
            except Exception:
                fresh = {}
            if fresh:
                cache.update({app_id: {"name": name, "checked_at": now.isoformat()} for app_id, name in fresh.items()})
                newest = sorted(cache.items(), key=lambda pair: str(pair[1]["checked_at"]), reverse=True)[:MAX_NAMES]
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(self.path, {"schema": SCHEMA_VERSION, "names": dict(newest)})
                except OSError:
                    pass
        return {app_id: cache[app_id]["name"] for app_id in wanted if app_id in cache}


def _aware(value):
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
