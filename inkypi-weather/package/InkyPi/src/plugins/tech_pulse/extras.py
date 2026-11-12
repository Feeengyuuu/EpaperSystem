"""The board that takes turns with OpenRouter's new models in Tech Pulse's bottom strip (keyless).

LMArena's text leaderboard, read through the Hugging Face datasets server (the file lists the
overall board first, so one small request is its top). The datasets server answers 503 now and
then, so the board is kept on disk: it is asked again after ARENA_FRESH_HOURS and a failure keeps
its last good copy. (The AI SDKs' weekly downloads shared the strip for a day; the owner found
them useless and they are gone.)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from runtime.long_task_executor import current_task_context
from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.http_client import get_http_client

logger = logging.getLogger(__name__)

USER_AGENT = "InkyPi TechPulse/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (4, 10)
RENDER_RESERVE_SECONDS = 4
MAX_BYTES = 128 * 1024
ARENA_URL = "https://datasets-server.huggingface.co/rows"
ARENA_PARAMS = {"dataset": "lmarena-ai/leaderboard-dataset", "config": "text", "split": "latest", "offset": "0",
                "length": "15"}
ARENA_FRESH_HOURS, ARENA_KEPT = 6, 6
# The strip's turns, one every TURN_MINUTES: OpenRouter keeps every other one.
TURN_MINUTES = 20
TURNS = ("openrouter", "arena")


def fetch_json(url, **options):
    return get_http_client().request_json("GET", url, timeout=REQUEST_TIMEOUT, max_bytes=MAX_BYTES,
                                          headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                                          **options).data


def _whole(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number) if number == number and abs(number) < 1e15 else None


def parse_arena(payload):
    """{"date", "models": [{rank, name, maker, score, votes}]}: the overall board's first
    ARENA_KEPT places. ValueError for an answer without them (the server's 503 body, an empty page)."""
    rows = payload.get("rows") if isinstance(payload, dict) else None
    models, date = [], ""
    for item in rows if isinstance(rows, list) else ():
        row = item.get("row") if isinstance(item, dict) else None
        if not isinstance(row, dict) or str(row.get("category") or "overall") != "overall":
            continue
        rank, score, name = _whole(row.get("rank")), _whole(row.get("rating")), " ".join(str(row.get("model_name") or "").split())
        if not name or rank is None or score is None or rank < 1:
            continue
        date = date or str(row.get("leaderboard_publish_date") or "")[:10]
        models.append({"rank": rank, "name": name[:48], "maker": str(row.get("organization") or "").strip().lower()[:32],
                       "score": score, "votes": _whole(row.get("vote_count")) or 0})
    if not models:
        raise ValueError("LMArena listed no models")
    models.sort(key=lambda model: model["rank"])
    return {"date": date, "models": models[:ARENA_KEPT]}


def _hours(stamp, now):
    try:
        fetched = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    return (now - fetched).total_seconds() / 3600 if fetched.tzinfo else None


def _stale(entry, now, hours):
    age = _hours(entry.get("fetched_at"), now) if isinstance(entry, dict) else None
    return age is None or not 0 <= age < hours


def _attempt(what, ask):
    parent = current_task_context()
    if parent is not None:
        parent.raise_if_cancelled()
        if parent.remaining_seconds() < max(REQUEST_TIMEOUT) + RENDER_RESERVE_SECONDS:
            return None
    try:
        return ask()
    except TaskDeadlineExceeded:
        # HTTP has its own short deadline, including Retry-After. Only a stopped parent
        # ends the page; a provider that used its slice leaves its previous board in place.
        if parent is not None:
            parent.raise_if_cancelled()
        logger.warning("Tech Pulse %s exceeded its request budget", what)
        return None
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.warning("Tech Pulse %s unavailable. | error_type: %s", what, type(exc).__name__)
        return None


def refresh(previous, now, fetch=None):
    """The kept state brought up to date: {"arena": {date, models, fetched_at}}. Asks LMArena when
    its copy is ARENA_FRESH_HOURS old; a failure keeps what was there."""
    parent = current_task_context()
    if parent is not None:
        parent.raise_if_cancelled()
    fetch = fetch or fetch_json
    now = now.astimezone(timezone.utc)
    stamp = now.isoformat()
    previous = previous if isinstance(previous, dict) else {}
    arena = previous.get("arena") if isinstance(previous.get("arena"), dict) else {}
    if not arena.get("models") or _stale(arena, now, ARENA_FRESH_HOURS):
        board = _attempt("LMArena", lambda: parse_arena(fetch(ARENA_URL, params=ARENA_PARAMS)))
        if board:
            arena = {**board, "fetched_at": stamp}
    return {"arena": arena}


def arena_models(state):
    arena = state.get("arena") if isinstance(state, dict) and isinstance(state.get("arena"), dict) else {}
    models = arena.get("models")
    return [model for model in models if isinstance(model, dict)] if isinstance(models, list) else []


def strip_turn(now, state):
    """Whose turn the bottom strip is: 'openrouter' or 'arena'. The board without data gives its
    turn back to OpenRouter."""
    turn = TURNS[int(now.timestamp() // (TURN_MINUTES * 60)) % len(TURNS)]
    if turn == "arena" and not arena_models(state):
        return "openrouter"
    return turn
