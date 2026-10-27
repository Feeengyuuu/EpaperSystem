"""Once-per-source Chinese game lines with a persistent, fail-closed Groq budget."""

from datetime import datetime, timezone
import hashlib
import logging
import math
import threading
import time

from runtime.refresh_contracts import TaskCancelled
from . import blurb_ai as ai

logger = logging.getLogger(__name__)
MAX_BLURBS = 400
MAX_DAILY_REQUESTS = 24
MAX_DAILY_GAMES = 128
MIN_DATA_REMAINING = 35
_GENERATION_LOCK = threading.Lock()


def free_enabled(owner):
    return owner._env_key(ai.FREE_CONFIRM_ENV).lower() in {"1", "true", "yes"} and bool(owner._env_key("GROQ_API_KEY"))


def _number(value, default=0):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else default
    except (TypeError, ValueError):
        return default


def _old_line(entry, source, description_digest):
    if not isinstance(entry, dict):
        return ""
    canonical_digest = hashlib.sha256(source["description"].encode("utf-8")).hexdigest()[:16]
    matches = entry.get("source") == ai.source_key(source) or entry.get("description_sha") in {description_digest, canonical_digest}
    matches = matches or entry.get("source") == description_digest
    text = entry.get("text")
    if not matches or not isinstance(text, str) or not text or len(text) > 90:
        return ""
    if source["kind"] == "dlc" and not any(word in text.lower() for word in ("dlc", "扩展", "资料片", "追加", "附加")):
        return ""
    return text


def lines(owner, details, first_sentence, *, allow_generate=False):
    cache = owner._read_shelf_cache()
    known = cache.get("blurbs") if isinstance(cache.get("blurbs"), dict) else {}
    output, missing = {}, {}
    for appid, facts in details.items():
        appid = str(appid)
        if not appid.isascii() or not appid.isdecimal() or not isinstance(facts, dict):
            continue
        source = ai.public_source(appid, facts)
        digest = hashlib.sha256(str(facts.get("description") or "").encode("utf-8")).hexdigest()[:16]
        entry = known.get(appid)
        previous = _old_line(entry, source, digest)
        tags = [tag for tag in facts.get("tag_names") or [] if isinstance(tag, str)]
        fallback = first_sentence(source["description"]) or " · ".join(tags) or source["name"]
        output[appid] = previous or fallback
        if previous and entry.get("source") == ai.source_key(source):
            continue
        # A title alone cannot support a new statement about gameplay.
        if source["name"] and len(source["description"]) >= 24:
            missing[appid] = source
    deadline = getattr(owner, "_active_data_deadline", None)
    if (not allow_generate or not missing or not free_enabled(owner) or deadline is None
            or deadline - time.monotonic() < MIN_DATA_REMAINING
            or getattr(owner, "_blurb_request_attempted", False)):
        return output
    if not _GENERATION_LOCK.acquire(blocking=False):
        return output
    try:
        output.update(_generate(owner, missing))
    finally:
        _GENERATION_LOCK.release()
    return output


def _generate(owner, missing):
    now = time.time()
    today = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    cache = owner._read_shelf_cache()
    state = cache.get("blurb_budget") if isinstance(cache.get("blurb_budget"), dict) else {}
    day = str(state.get("day") or "")
    # Clock rollback never opens a second budget for the same day.
    if day < today:
        state = {"day": today, "requests": 0, "games": 0, "retry_after": state.get("retry_after", 0)}
    calls, games = int(_number(state.get("requests"))), int(_number(state.get("games")))
    if now < _number(state.get("retry_after")) or calls >= MAX_DAILY_REQUESTS or games >= MAX_DAILY_GAMES:
        return {}
    batch = dict(list(missing.items())[:min(ai.BATCH_SIZE, MAX_DAILY_GAMES - games)])
    state = {**state, "requests": calls + 1, "games": games + len(batch),
             "attempt_at": now, "retry_after": now + 3600, "last_status": "pending", "last_batch_size": len(batch)}
    cache["blurb_budget"] = state
    owner._write_shelf_cache(cache)
    # If the reservation cannot be saved, do not make an unbudgeted network request.
    if owner._read_shelf_cache().get("blurb_budget") != state:
        return {}
    owner._blurb_request_attempted = True
    try:
        result = owner._translate_blurbs(batch)
        valid = {appid: text for appid, text in result.items() if appid in batch} if isinstance(result, dict) else {}
        cooldown = 60 if len(valid) == len(batch) else 3600
        status = "complete" if len(valid) == len(batch) else "partial"
    except TaskCancelled:
        raise
    except ai.BlurbUnavailable as error:
        valid, cooldown = {}, error.retry_after
        status = error.kind
        logger.warning("Steam free blurb generation deferred: %s", error.kind)
    except Exception:
        valid, cooldown = {}, 3600
        status = "adapter_failure"
        logger.warning("Steam free blurb generation deferred: adapter_failure")
    cache = owner._read_shelf_cache()
    known = cache.get("blurbs") if isinstance(cache.get("blurbs"), dict) else {}
    for appid, text in valid.items():
        source = batch[appid]
        known[appid] = {"source": ai.source_key(source), "text": text, "at": now,
                        "description_sha": hashlib.sha256(source["description"].encode("utf-8")).hexdigest()[:16],
                        "provider": "groq", "model": ai.MODEL, "prompt": ai.PROMPT_VERSION, "language": ai.LANGUAGE}
    cache["blurbs"] = dict(sorted(known.items(), key=lambda item: _number(item[1].get("at"))
                                  if isinstance(item[1], dict) else 0)[-MAX_BLURBS:])
    cache["blurb_budget"] = {**state, "retry_after": now + cooldown, "last_status": status,
                             "last_generated": len(valid), "generated": int(_number(state.get("generated"))) + len(valid)}
    if valid:
        cache["blurb_budget"]["last_success_at"] = now
    owner._write_shelf_cache(cache)
    return valid
