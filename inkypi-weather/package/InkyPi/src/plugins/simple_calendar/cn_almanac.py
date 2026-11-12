"""今日黄历 for the calendar: each day's 干支, what it favours (宜) and warns against (忌) and the
solar terms, from the keyless calendar card behind Baidu's search (opendata.baidu.com, GBK).

One request answers three months (the month asked for and its neighbours: 92 days, 50 KB), so the
source is asked once when a month begins. Only what the page prints is kept, in a small state
file beside the calendar's other schedules; a request that fails keeps the last good days and
waits CHECK_INTERVAL_SECONDS before the next one. Reading never fetches or writes.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.atomic_file import atomic_write_json
from utils.http_client import get_http_client

logger = logging.getLogger(__name__)
URL = "https://opendata.baidu.com/api.php"
MAX_BYTES = 256 * 1024
TIMEOUT_SECONDS = 15
CHECK_INTERVAL_SECONDS = 6 * 60 * 60
MAX_STATE_BYTES = 128 * 1024
STATE_VERSION = 1
# Each list keeps this many of its entries, each at most this long (the page shows the first few).
MAX_ITEMS = 12
MAX_ITEM_CHARS = 6
# Baidu's "term" field mixes the solar terms with festivals and the 数九 count.
SOLAR_TERMS = frozenset((
    "小寒", "大寒", "立春", "雨水", "惊蛰", "春分", "清明", "谷雨", "立夏", "小满", "芒种", "夏至",
    "小暑", "大暑", "立秋", "处暑", "白露", "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "冬至"))
# How far ahead the next solar term is looked for (they are about fifteen days apart).
TERM_LOOKAHEAD_DAYS = 31
# A day's 宜 or 忌 that is a saying rather than a list of things.
SAYINGS = frozenset(("诸事不宜", "馀事勿取", "余事勿取", "大事勿用"))


def fetch_month(year, month, context=None) -> dict:
    """Baidu's calendar card for ``year``-``month`` (it answers the neighbouring months too)."""
    response = get_http_client().request_text(
        "GET", URL, context=context, timeout=TIMEOUT_SECONDS, max_bytes=MAX_BYTES, encoding="gbk", errors="replace",
        params={"tn": "wisetpl", "format": "json", "resource_id": "39043", "query": f"{int(year)}年{int(month)}月"},
        headers={"User-Agent": "InkyPi SimpleCalendar/1.0", "Accept": "application/json"})
    return json.loads(response.data)


def _items(value):
    items = [" ".join(item.split()) for item in re.split(r"[.、，,]", str(value or ""))]
    return [item for item in items if item and len(item) <= MAX_ITEM_CHARS][:MAX_ITEMS]


def days_from_payload(payload) -> dict:
    """{"YYYY-MM-DD": {"ganzhi": [year, month, day], "suit": [...], "avoid": [...], "term"?}} for
    every day the card lists; ValueError when it lists none."""
    days = {}
    cards = payload.get("data") if isinstance(payload, dict) else None
    for card in cards if isinstance(cards, list) else []:
        listed = card.get("almanac") if isinstance(card, dict) else None
        for raw in listed if isinstance(listed, list) else []:
            if not isinstance(raw, dict):
                continue
            try:
                day = date(int(raw.get("year")), int(raw.get("month")), int(raw.get("day")))
            except (TypeError, ValueError):
                continue
            ganzhi = [str(raw.get(key) or "").strip() for key in ("gzYear", "gzMonth", "gzDate")]
            if not all(len(part) == 2 for part in ganzhi):
                continue
            entry = {"ganzhi": ganzhi, "suit": _items(raw.get("suit")), "avoid": _items(raw.get("avoid"))}
            named = re.split(r"[\s,，]+", f"{raw.get('term') or ''} {raw.get('festivalList') or ''}")
            term = next((name for name in named if name in SOLAR_TERMS), "")
            if term:
                entry["term"] = term
            days[day.isoformat()] = entry
    if not days:
        raise ValueError("the almanac card listed no days")
    return days


def day_note(days, day):
    """What the page prints for ``day``: {"ganzhi": "丙午年 己亥月 己丑日", "suit", "avoid", "term"?},
    the term line naming today's solar term or the next one and how far off it is; None when the
    day is not among ``days``."""
    entry = (days or {}).get(day.isoformat())
    if not isinstance(entry, dict) or len(entry.get("ganzhi") or ()) != 3:
        return None
    year, month, sun = entry["ganzhi"]
    note = {"ganzhi": f"{year}年 {month}月 {sun}日", "suit": list(entry.get("suit") or []),
            "avoid": list(entry.get("avoid") or [])}
    for ahead in range(TERM_LOOKAHEAD_DAYS + 1):
        later = day + timedelta(days=ahead)
        term = ((days.get(later.isoformat()) or {}).get("term"))
        if term:
            note["term"] = (f"今日{term}" if ahead == 0 else f"明日{term}" if ahead == 1
                            else f"{term} {later.month}/{later.day} 还有{ahead}天")
            break
    return note


def balloon_line(note):
    """The almanac in one short line for the host: the first thing the day favours and the first it
    warns against ("宜会亲友 忌结婚"), a saying quoted whole; "" without either."""
    if not isinstance(note, dict):
        return ""
    suit = next((item for item in note.get("suit") or [] if item != "无"), "")
    avoid = next((item for item in note.get("avoid") or [] if item != "无"), "")
    parts = [f"宜{suit}" if suit and suit not in SAYINGS else suit,
             avoid if avoid in SAYINGS else (f"忌{avoid}" if avoid else "")]
    return " ".join(part for part in parts if part)


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


class AlmanacProvider:
    """Asks for the almanac once a month and replays its state file without provider IO."""

    state_subdir = "cn_almanac"

    def __init__(self, directory=None, *, fetch=None):
        base = Path(os.environ.get("INKYPI_DATA_DIR", "/var/lib/inkypi/data"))
        self.directory = Path(directory) if directory is not None else base / "plugins/simple_calendar" / self.state_subdir
        self.path = self.directory / "state.json"
        self.fetch = fetch

    def _load(self):
        try:
            if self.path.is_symlink() or self.path.stat().st_size > MAX_STATE_BYTES:
                return {}
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(state, dict) or state.get("version") != STATE_VERSION or not isinstance(state.get("days"), dict):
            return {}
        return state

    def _save(self, state):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, state)
        except (OSError, ValueError) as exc:
            logger.warning("Simple Calendar almanac state not saved: %s", type(exc).__name__)

    def read(self):
        """The kept days; no network, directory creation or writes."""
        return self._load().get("days") or {}

    def refresh(self, today, *, now=None):
        """The days to show from: the kept ones while they are this month's, else asked for anew
        (one request). A failure keeps the last good days and is not retried for CHECK_INTERVAL_SECONDS."""
        now = now or datetime.now(timezone.utc)
        state = self._load()
        kept = state.get("days") or {}
        month = f"{today.year:04d}-{today.month:02d}"
        if state.get("month") == month and today.isoformat() in kept:
            return kept
        due = _instant(state.get("next_check_at"))
        if due and now < due <= now + timedelta(seconds=CHECK_INTERVAL_SECONDS):
            return kept
        try:
            days = days_from_payload((self.fetch or fetch_month)(today.year, today.month))
        except (TaskCancelled, TaskDeadlineExceeded):
            raise
        except Exception as exc:  # noqa: BLE001 - the almanac is an extra: the page is drawn without it.
            logger.warning("Simple Calendar almanac check failed: %s", type(exc).__name__)
            self._save({"version": STATE_VERSION, "month": state.get("month"), "fetched_at": state.get("fetched_at"),
                        "next_check_at": (now + timedelta(seconds=CHECK_INTERVAL_SECONDS)).isoformat(), "days": kept})
            return kept
        self._save({"version": STATE_VERSION, "month": month, "fetched_at": now.isoformat(), "days": days})
        return days
