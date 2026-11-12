"""Two boards that take turns with OpenRouter's new models in Tech Pulse's bottom strip (keyless).

LMArena's text leaderboard, read through the Hugging Face datasets server (the file lists the
overall board first, so one small request is its top), and the weekly downloads of a short fixed
list of AI SDKs from npm and PyPI Stats. The datasets server answers 503 now and then and the
counts move once a day, so everything is kept on disk: the board is asked again after
ARENA_FRESH_HOURS, a count after COUNT_FRESH_HOURS and only one stale count per registry in a
refresh, and whatever fails keeps its last good copy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.http_client import get_http_client

logger = logging.getLogger(__name__)

USER_AGENT = "InkyPi TechPulse/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (4, 10)
MAX_BYTES = 128 * 1024
ARENA_URL = "https://datasets-server.huggingface.co/rows"
ARENA_PARAMS = {"dataset": "lmarena-ai/leaderboard-dataset", "config": "text", "split": "latest", "offset": "0",
                "length": "15"}
ARENA_FRESH_HOURS, ARENA_KEPT = 6, 6
NPM_URL = "https://api.npmjs.org/downloads/point/last-week/{packages}"
PYPI_URL = "https://pypistats.org/api/packages/{package}/recent"
# npm answers several plain packages at once; a scoped one (400 in a bulk request) is asked alone.
NPM_BULK = ("openai", "langchain", "ai")
NPM_SINGLE = ("@anthropic-ai/sdk",)
PYPI = ("openai", "anthropic", "langchain", "transformers", "torch")
COUNT_FRESH_HOURS = 24
# The strip's rows: (registry, its name on the page, [(package, its name on the page)]).
SDK_ROWS = (("npm", "npm", (("openai", "openai"), ("@anthropic-ai/sdk", "anthropic"), ("ai", "ai (Vercel)"),
                            ("langchain", "langchain"))),
            ("pypi", "PyPI", (("openai", "openai"), ("anthropic", "anthropic"), ("torch", "torch"),
                              ("transformers", "transformers"), ("langchain", "langchain"))))
# The strip's turns, one every TURN_MINUTES: OpenRouter keeps every other one.
TURN_MINUTES = 20
TURNS = ("openrouter", "arena", "openrouter", "sdk")
SDK_MIN_COUNTS = 3


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


def parse_npm(payload):
    """{package: downloads last week} from npm's answer for one package or for several."""
    if isinstance(payload, dict) and payload.get("package") and _whole(payload.get("downloads")) is not None:
        return {str(payload["package"]): _whole(payload["downloads"])}
    counts = {str(name): _whole(row.get("downloads")) for name, row in (payload.items() if isinstance(payload, dict) else ())
              if isinstance(row, dict) and _whole(row.get("downloads")) is not None}
    if not counts:
        raise ValueError("npm gave no download counts")
    return counts


def parse_pypi(payload):
    """{package: downloads last week} from PyPI Stats' recent-downloads answer."""
    data = payload.get("data") if isinstance(payload, dict) else None
    count = _whole(data.get("last_week")) if isinstance(data, dict) else None
    if count is None or not payload.get("package"):
        raise ValueError("PyPI Stats gave no download count")
    return {str(payload["package"]): count}


def count_text(count):
    """57910951 -> '5791万', 123456789 -> '1.2亿'."""
    count = int(count or 0)
    if count >= 100_000_000:
        return f"{count / 100_000_000:.1f}亿"
    return f"{round(count / 10_000)}万" if count >= 10_000 else str(count)


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
    try:
        return ask()
    except (TaskCancelled, TaskDeadlineExceeded):
        raise
    except Exception as exc:
        logger.warning("Tech Pulse %s unavailable. | error_type: %s", what, type(exc).__name__)
        return None


def refresh(previous, now, fetch=None):
    """The kept state brought up to date: {"arena": {date, models, fetched_at}, "downloads": {"npm":
    {package: {count, fetched_at}}, "pypi": {...}}}. Asks LMArena when its copy is ARENA_FRESH_HOURS
    old, and per registry the first count that is COUNT_FRESH_HOURS old (npm's plain packages in one
    request); a failure keeps what was there."""
    fetch = fetch or fetch_json
    now = now.astimezone(timezone.utc)
    stamp = now.isoformat()
    previous = previous if isinstance(previous, dict) else {}
    arena = previous.get("arena") if isinstance(previous.get("arena"), dict) else {}
    kept = previous.get("downloads") if isinstance(previous.get("downloads"), dict) else {}
    downloads = {registry: dict(kept.get(registry)) if isinstance(kept.get(registry), dict) else {}
                 for registry in ("npm", "pypi")}
    if not arena.get("models") or _stale(arena, now, ARENA_FRESH_HOURS):
        board = _attempt("LMArena", lambda: parse_arena(fetch(ARENA_URL, params=ARENA_PARAMS)))
        if board:
            arena = {**board, "fetched_at": stamp}
    npm = downloads["npm"]
    wanted = (NPM_BULK if any(_stale(npm.get(name), now, COUNT_FRESH_HOURS) for name in NPM_BULK) else
              next(((name,) for name in NPM_SINGLE if _stale(npm.get(name), now, COUNT_FRESH_HOURS)), ()))
    if wanted:
        counts = _attempt("npm downloads", lambda: parse_npm(fetch(NPM_URL.format(packages=",".join(wanted)))))
        npm.update({name: {"count": counts[name], "fetched_at": stamp} for name in wanted if name in (counts or {})})
    package = next((name for name in PYPI if _stale(downloads["pypi"].get(name), now, COUNT_FRESH_HOURS)), None)
    if package:
        counts = _attempt("PyPI downloads", lambda: parse_pypi(fetch(PYPI_URL.format(package=package))))
        if counts and package in counts:
            downloads["pypi"][package] = {"count": counts[package], "fetched_at": stamp}
    return {"arena": arena, "downloads": downloads}


def sdk_rows(state):
    """[(registry name, [(package name, count)])] of the counts at hand, in the strip's order."""
    downloads = state.get("downloads") if isinstance(state, dict) and isinstance(state.get("downloads"), dict) else {}
    rows = []
    for registry, label, packages in SDK_ROWS:
        kept = downloads.get(registry) if isinstance(downloads.get(registry), dict) else {}
        counts = [(name, kept[package]["count"]) for package, name in packages
                  if isinstance(kept.get(package), dict) and isinstance(kept[package].get("count"), int)]
        if counts:
            rows.append((label, counts))
    return rows


def arena_models(state):
    arena = state.get("arena") if isinstance(state, dict) and isinstance(state.get("arena"), dict) else {}
    models = arena.get("models")
    return [model for model in models if isinstance(model, dict)] if isinstance(models, list) else []


def strip_turn(now, state):
    """Whose turn the bottom strip is: 'openrouter', 'arena' or 'sdk'. A board without data gives
    its turn back to OpenRouter."""
    turn = TURNS[int(now.timestamp() // (TURN_MINUTES * 60)) % len(TURNS)]
    if turn == "arena" and not arena_models(state):
        return "openrouter"
    if turn == "sdk" and sum(len(counts) for _label, counts in sdk_rows(state)) < SDK_MIN_COUNTS:
        return "openrouter"
    return turn
