"""Durable optional game schedule, independent from personal calendar snapshots."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from plugins.base_plugin.render_provenance import SourceProvenance
from plugins.simple_calendar.game_event_sources import (
    DEFAULT_TIMEZONES, SERIES, SOURCE_FAMILIES, fetch_source, valid_source_url,
)
from runtime.refresh_contracts import TaskContext
from utils.atomic_file import atomic_write_json
from utils.http_client import DeadlineRetry, get_http_session, provider_io_lease

CHECK_INTERVAL_SECONDS = 3 * 60 * 60
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_ITEMS = 256
_REFRESH_LOCK = threading.RLock()


def enabled(value):
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def selected_series(settings):
    values = settings.get("gameEventSeries[]", list(SERIES))
    if isinstance(values, str):
        values = [values]
    return [key for key in SERIES if key in (values or [])]


def selected_sources(settings):
    families = set(selected_series(settings))
    sources = [key for key, series in SOURCE_FAMILIES.items()
               if key not in {"ign", "eurogamer", "sgf"} and families.intersection(series)]
    if families.intersection({"summer_game_fest", "the_game_awards"}):
        sources.append("sgf")
    if families and enabled(settings.get("allowGameEventMedia", True)):
        sources.extend(("eurogamer", "ign"))
    return sources


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


@dataclass
class GameEventResult:
    """Read-only projection with per-source diagnostic state."""

    events: list[dict]
    sources: dict
    provenance: SourceProvenance


class GameEventProvider:
    """Poll at most once per three hours and replay state without provider IO."""

    def __init__(self, directory=None, *, fetch_text=None):
        base = Path(os.environ.get("INKYPI_DATA_DIR", "/var/lib/inkypi/data"))
        self.directory = Path(directory) if directory is not None else base / "plugins/simple_calendar/game_events"
        self.path = self.directory / "state.json"
        self.fetch_text = fetch_text

    def _load(self):
        empty = {"version": 1, "sources": {}}
        try:
            if self.path.is_symlink() or self.path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError("invalid cache file")
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_STATE_BYTES + 1)
            if len(raw) > MAX_STATE_BYTES:
                raise ValueError("cache too large")
            payload = json.loads(raw)
            self._validate_state(payload)
            return payload
        except FileNotFoundError:
            return empty
        except (OSError, ValueError, TypeError):
            empty["cache_error"] = "game event cache unreadable"
            return empty

    @staticmethod
    def _validate_state(state):
        if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("sources"), dict):
            raise ValueError("invalid game state")
        for name, source in state["sources"].items():
            if name not in SOURCE_FAMILIES or not isinstance(source, dict):
                raise ValueError("invalid game source")
            items = source.get("items")
            if not isinstance(items, list) or len(items) > MAX_SOURCE_ITEMS:
                raise ValueError("invalid game items")
            for key in ("last_attempt_at", "last_success_at", "next_check_at"):
                if source.get(key) is not None and _instant(source[key]) is None:
                    raise ValueError("invalid source timestamp")
            if not isinstance(source.get("error", ""), str):
                raise ValueError("invalid source error")
            for item in items:
                if not isinstance(item, dict) or item.get("source_id") != name or item.get("family") not in SERIES:
                    raise ValueError("invalid game event")
                for key in ("id", "title", "event_date", "edition", "external_id", "source_url", "official_url"):
                    if not isinstance(item.get(key), str) or len(item[key]) > 2048:
                        raise ValueError("invalid event field")
                if not re.fullmatch(r"[0-9a-f]{24}", item["id"]) or not valid_source_url(item["source_url"]):
                    raise ValueError("invalid event identity")
                if item["official_url"] and not valid_source_url(item["official_url"]):
                    raise ValueError("invalid evidence URL")
                date.fromisoformat(item["event_date"])
                if item.get("previous_date") is not None:
                    date.fromisoformat(item["previous_date"])
                if item.get("event_timezone"):
                    try:
                        ZoneInfo(item["event_timezone"])
                    except (KeyError, TypeError) as exc:
                        raise ValueError("invalid event timezone") from exc
                if type(item.get("revision", 0)) is not int or item.get("revision", 0) < 0:
                    raise ValueError("invalid event revision")
                if not _instant(item.get("checked_at")) or item.get("starts_at") is not None and not _instant(item["starts_at"]):
                    raise ValueError("invalid event timestamp")
                if item.get("published_at") is not None and not _instant(item["published_at"]):
                    raise ValueError("invalid announcement timestamp")
                if item.get("authority") not in (1, 2, 3) or item.get("status") not in ("confirmed", "cancelled", "postponed"):
                    raise ValueError("invalid event status")

    def _save(self, state):
        state.pop("cache_error", None)
        if len(json.dumps(state, ensure_ascii=False).encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError("game event state exceeds size limit")
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, state)

    def _network_reader(self):
        context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 90)

        def fetch(url):
            for _ in range(4):
                if not valid_source_url(url):
                    raise ValueError("untrusted game source URL")
                with provider_io_lease(url, context=context), DeadlineRetry.for_context(context):
                    response = get_http_session().get(
                        url, timeout=max(0.1, min(8, context.remaining_seconds())), stream=True,
                        allow_redirects=False, headers={"User-Agent": "InkyPi-GameCalendar/1.0", "Accept": "text/html,application/rss+xml,text/calendar"},
                    )
                    try:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("Location")
                            if not location:
                                raise ValueError("missing redirect location")
                            url = urljoin(url, location)
                            continue
                        response.raise_for_status()
                        content = bytearray()
                        for chunk in response.iter_content(64 * 1024):
                            context.raise_if_cancelled()
                            content.extend(chunk)
                            if len(content) > 4 * 1024 * 1024:
                                raise ValueError("game source response too large")
                        return content.decode("utf-8-sig")
                    finally:
                        response.close()
            raise ValueError("too many game source redirects")
        return fetch

    def refresh(self, settings, *, now=None):
        """DATA-only synchronization; failures also advance the durable poll gate."""
        now = now or datetime.now(timezone.utc)
        if not enabled(settings.get("showGameEvents")):
            return GameEventResult([], {}, SourceProvenance.FRESH_CACHE)
        with _REFRESH_LOCK:
            state = self._load()
            fetch = self.fetch_text or self._network_reader()
            for source_id in selected_sources(settings):
                source = state["sources"].setdefault(source_id, {"items": []})
                due = _instant(source.get("next_check_at"))
                if due and now < due <= now + timedelta(seconds=CHECK_INTERVAL_SECONDS):
                    continue
                source.update(last_attempt_at=now.isoformat(), next_check_at=(now + timedelta(seconds=CHECK_INTERVAL_SECONDS)).isoformat(), error="check interrupted")
                self._save(state)
                known = [item["source_url"] for item in source.get("items", []) if str(item.get("event_date", "")) >= now.date().isoformat()]
                batch = fetch_source(source_id, fetch, known, now)
                self._merge_source(state, source_id, batch.announcements, now)
                source["error"] = "; ".join(batch.errors)[:500]
                if not batch.errors:
                    source["last_success_at"] = now.isoformat()
                self._save(state)
            return self._result(state, settings, now)

    def read(self, settings, *, now=None):
        """No network, directory creation or writes; safe for presentation and UI."""
        now = now or datetime.now(timezone.utc)
        if not enabled(settings.get("showGameEvents")):
            return GameEventResult([], {}, SourceProvenance.FRESH_CACHE)
        return self._result(self._load(), settings, now)

    def _merge_source(self, state, source_id, announcements, now):
        previous = state["sources"][source_id].get("items", [])
        items = {(item["source_url"], item.get("external_id", ""), item["family"]): item for item in previous}
        all_items = [item for source in state["sources"].values() for item in source.get("items", [])]
        for announcement in announcements:
            if announcement.event_date < (now - timedelta(days=32)).date() or announcement.event_date > (now + timedelta(days=730)).date():
                continue
            item = asdict(announcement)
            item["event_date"] = announcement.event_date.isoformat()
            item["starts_at"] = announcement.starts_at.isoformat() if announcement.starts_at else None
            item["published_at"] = announcement.published_at.isoformat() if announcement.published_at else None
            item["previous_date"] = announcement.previous_date.isoformat() if announcement.previous_date else None
            item["checked_at"] = now.isoformat()
            matching = next((old for old in all_items if self._same_event(old, item)), None)
            if not matching and item["previous_date"]:
                previous_matches = {old["id"]: old for old in all_items
                                    if old["family"] == item["family"] and old["edition"] == item["edition"]
                                    and old["event_date"] == item["previous_date"]}
                if len(previous_matches) == 1:
                    matching = next(iter(previous_matches.values()))
            seed = announcement.family + "|" + (announcement.external_id or announcement.source_url) + "|" + announcement.edition
            item["id"] = matching["id"] if matching else hashlib.sha256(seed.encode()).hexdigest()[:24]
            key = (announcement.source_url, announcement.external_id, announcement.family)
            old = items.get(key)
            if old and announcement.external_id and old.get("revision", 0) > announcement.revision:
                continue
            items[key] = item
            all_items.append(item)
        cutoff = (now - timedelta(days=32)).date().isoformat()
        state["sources"][source_id]["items"] = sorted(
            (item for item in items.values() if item.get("event_date", "") >= cutoff),
            key=lambda item: item["event_date"],
        )[:MAX_SOURCE_ITEMS]

    @staticmethod
    def _same_event(left, right):
        if left.get("family") != right.get("family") or left.get("edition") != right.get("edition"):
            return False
        if left.get("external_id") and left.get("external_id") == right.get("external_id"):
            return True
        if left.get("source_id") == right.get("source_id") and left.get("external_id") and right.get("external_id"):
            return False
        if not left.get("external_id") and not right.get("external_id") and left.get("source_url") == right.get("source_url"):
            return True
        if left.get("official_url") == right.get("source_url") or right.get("official_url") == left.get("source_url"):
            return True
        return left.get("event_date") == right.get("event_date") or GameEventProvider._dates_agree(left, right)

    def _result(self, state, settings, now):
        families, sources = set(selected_series(settings)), selected_sources(settings)
        groups, diagnostics = {}, {}
        for source_id in sources:
            source = state["sources"].get(source_id, {})
            diagnostics[source_id] = {key: source.get(key) for key in ("last_attempt_at", "last_success_at", "next_check_at", "error")}
            if state.get("cache_error"):
                diagnostics[source_id]["error"] = state["cache_error"]
            for item in source.get("items", []):
                if item.get("family") in families:
                    groups.setdefault(item["id"], []).append(item)
        events = []
        for candidates in groups.values():
            winner = max(candidates, key=lambda item: (
                item.get("authority", 0), item.get("published_at") or "",
                item.get("checked_at", ""), item.get("status") != "confirmed", bool(item.get("starts_at")),
            ))
            candidates = [item for item in candidates if not (
                winner.get("previous_date") == item.get("event_date")
                and winner.get("authority", 0) >= item.get("authority", 0)
                and item.get("source_url") != winner.get("source_url")
            )]
            agreeing = [item for item in candidates if self._agree(winner, item)]
            if winner.get("authority", 0) < 2 and not winner.get("official_url") and len({item["source_id"] for item in agreeing}) < 2:
                continue
            checked = _instant(winner.get("checked_at"))
            pending = not checked or (now - checked).total_seconds() >= CHECK_INTERVAL_SECONDS
            pending = pending or bool(state["sources"].get(winner["source_id"], {}).get("error"))
            conflict = any(not self._agree(winner, item) for item in candidates)
            if winner.get("authority", 0) < 2 and not winner.get("official_url"):
                fresh_publishers = {item["source_id"] for item in agreeing
                                    if _instant(item.get("checked_at")) and (now - _instant(item["checked_at"])).total_seconds() < CHECK_INTERVAL_SECONDS
                                    and not state["sources"].get(item["source_id"], {}).get("error")}
                pending = pending or len(fresh_publishers) < 2
            event = dict(winner, pending_verification=pending or conflict, conflict=conflict)
            if not event.get("starts_at"):
                timed = [item for item in agreeing if item.get("starts_at") and (item.get("authority", 0) >= 2 or item.get("official_url"))]
                if timed:
                    time_evidence = max(timed, key=lambda item: (item.get("authority", 0), item.get("published_at") or "", item.get("checked_at", "")))
                    event["starts_at"] = time_evidence["starts_at"]
                    event["time_source_url"] = time_evidence["source_url"]
                    verified = _instant(time_evidence.get("checked_at"))
                    event["pending_verification"] |= not verified or (now - verified).total_seconds() >= CHECK_INTERVAL_SECONDS or bool(state["sources"][time_evidence["source_id"]].get("error"))
            instant = _instant(event.get("starts_at"))
            still_upcoming = instant >= now if instant else event["event_date"] >= now.date().isoformat()
            if not still_upcoming:
                event["pending_verification"] = False
            if event.get("status") == "confirmed":
                events.append(event)
        events.sort(key=lambda item: (item["event_date"], item.get("starts_at") or "", item["title"]))
        stale = any(item["pending_verification"] for item in events)
        cold_failure = any(status.get("error") and not status.get("last_success_at") for status in diagnostics.values())
        provenance = SourceProvenance.STALE_CACHE if stale else SourceProvenance.FRESH_CACHE
        if cold_failure and not events:
            provenance = SourceProvenance.LOCAL_FALLBACK
        return GameEventResult(events, diagnostics, provenance)

    @staticmethod
    def _agree(left, right):
        if left.get("status") != right.get("status"):
            return False
        return GameEventProvider._dates_agree(left, right)

    @staticmethod
    def _dates_agree(left, right):
        left_time, right_time = _instant(left.get("starts_at")), _instant(right.get("starts_at"))
        if left_time and right_time:
            return left_time == right_time
        if left_time or right_time:
            date_only, instant = (right, left_time) if left_time else (left, right_time)
            zone = date_only.get("event_timezone") or DEFAULT_TIMEZONES[date_only["family"]]
            return instant.astimezone(ZoneInfo(zone)).date().isoformat() == date_only["event_date"]
        return left.get("event_date") == right.get("event_date")

    def calendar_events(self, result, selected_date, tz):
        """Project persisted records onto the existing SimpleCalendar event shape."""
        events = []
        for item in result.events:
            instant = _instant(item.get("starts_at"))
            local = instant.astimezone(tz) if instant else None
            event_date = local.date() if local else date.fromisoformat(item["event_date"])
            if (event_date.year, event_date.month) != (selected_date.year, selected_date.month):
                continue
            title = item["title"]
            if item["pending_verification"]:
                title = "[待核验] " + title
            time_label = local.strftime("%H:%M") if local else "时间待定"
            event = {"date": event_date, "title": title, "label": "GAME", "color": (93, 45, 121), "kind": "personal", "time": time_label}
            if local:
                event["starts_at"] = local
            events.append(event)
        return events
