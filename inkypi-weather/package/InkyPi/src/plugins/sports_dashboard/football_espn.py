"""Fetch complete football scoreboards using ESPN's supported month tokens."""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import time

from utils.http_client import create_single_attempt_http_client

FOOTBALL_EVENT_LIMIT = 1000


def _event_start(event):
    competitions = event.get("competitions") or []
    raw = event.get("date") or (competitions[0].get("date") if competitions else None)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _finished(event):
    competitions = event.get("competitions") or []
    competition = competitions[0] if competitions else {}
    kind = (competition.get("status") or event.get("status") or {}).get("type") or {}
    return bool(kind.get("completed")) or str(kind.get("state")).lower() == "post"


def fetch_scoreboard(session, url, start, end, *, can_request, record_request, timeout=15):
    """Return the complete [start, end) window or fail without a partial result.

    Existing callers own cache freshness, live polling and last-good retention.
    Even the live window uses month tokens: football months are small and this
    normally costs one request instead of three daily requests at live cadence.
    """
    if not start.tzinfo or not end.tzinfo or end <= start:
        raise ValueError("Football scoreboard requires an aware, increasing window")
    if getattr(session, "_inkypi_adapter_retries", False):
        with create_single_attempt_http_client() as owned:
            return fetch_scoreboard(
                owned.session, url, start, end, can_request=can_request, record_request=record_request, timeout=timeout
            )

    # Month feeds can include fixtures across UTC midnight. Fetch adjacent
    # boundary months when needed, then filter actual kickoff timestamps.
    first = (start.astimezone(timezone.utc).date() - timedelta(days=1)).replace(day=1)
    last = ((end - timedelta(microseconds=1)).astimezone(timezone.utc).date() + timedelta(days=1)).replace(day=1)
    current = first
    deadline = time.monotonic() + 45
    merged = {}
    result = {}
    while current <= last:
        if not can_request():
            raise RuntimeError("football ESPN daily request limit reached")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("football ESPN window deadline reached")
        response = None
        try:
            try:
                response = session.get(
                    url,
                    params={"dates": current.strftime("%Y%m"), "limit": str(FOOTBALL_EVENT_LIMIT)},
                    headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                    timeout=min(timeout, remaining),
                )
            finally:
                record_request()
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping) or not isinstance(payload.get("events"), list):
                raise ValueError("football ESPN response has no valid events list")
            events = payload["events"]
            if len(events) >= FOOTBALL_EVENT_LIMIT:
                raise ValueError("football ESPN month may be truncated")
            if not result:
                result = {key: value for key, value in payload.items() if key != "events"}
            for event in events:
                if not isinstance(event, Mapping) or not event.get("id") or not _event_start(event):
                    raise ValueError("football ESPN month contains an invalid event")
                key = str(event["id"])
                if key not in merged or not _finished(merged[key]) or _finished(event):
                    merged[key] = event
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)

    result["events"] = sorted(
        (event for event in merged.values() if start <= _event_start(event) < end), key=_event_start
    )
    return result
