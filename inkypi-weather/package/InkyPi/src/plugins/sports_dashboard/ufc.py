"""UFC for the lower panel: ESPN's keyless MMA scoreboard (one request of about 100 KB covers the
last card and the next few).

Each card is read main event first: every bout's weight class (in Chinese), scheduled rounds, state
and, once over, its winner with the round and time of a finish or a decision; each fighter's name,
record, country flag and ESPN headshot. The panel shows the card in progress, else the one that
ended within a day and a half, else the next one; a numbered card (UFC 334) ranks with the
leagues' big games on fight night, a Fight Night with their regular ones.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import event_ranking, extra_feeds

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
HEADSHOT_URL = "https://a.espncdn.com/i/headshots/mma/players/full/{id}.png"
VERSION = "ufc-scoreboard-v1"
MAX_BYTES = 768 * 1024
DAYS_BACK, DAYS_AHEAD = 10, 35
RECENT_WINDOW = timedelta(hours=36)
CARD_LENGTH = timedelta(hours=3)
LIVE_WINDOW = timedelta(hours=4)
NUMBERED = re.compile(r"^UFC\s+\d+$", re.IGNORECASE)
WEIGHTS_ZH = {"heavyweight": "重量级", "light heavyweight": "轻重量级", "middleweight": "中量级",
              "welterweight": "次中量级", "lightweight": "轻量级", "featherweight": "羽量级", "bantamweight": "雏量级",
              "flyweight": "蝇量级", "strawweight": "草量级", "catchweight": "协议体重"}


def _date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def weight_zh(weight):
    text = " ".join(str(weight or "").split())
    women = text.upper().startswith("W ")
    base = WEIGHTS_ZH.get((text[2:] if women else text).lower(), "")
    return ("女子" + base) if women and base else base


def _fighter(raw):
    athlete = raw.get("athlete") if isinstance(raw.get("athlete"), dict) else {}
    flag = athlete.get("flag") if isinstance(athlete.get("flag"), dict) else {}
    record = next((item.get("summary") for item in raw.get("records") or []
                   if isinstance(item, dict) and item.get("name") == "overall"), "")
    fighter_id = str(raw.get("id") or "")
    return {"id": fighter_id, "name": str(athlete.get("displayName") or athlete.get("fullName") or "TBD")[:40],
            "short": str(athlete.get("shortName") or "")[:24], "record": str(record or "")[:12],
            "country": str(flag.get("alt") or "")[:30], "flag": str(flag.get("href") or ""),
            "headshot": HEADSHOT_URL.format(id=fighter_id) if fighter_id.isdigit() else "",
            "winner": bool(raw.get("winner")), "order": raw.get("order") or 0}


def _fight(raw):
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    kind = status.get("type") if isinstance(status.get("type"), dict) else {}
    rounds = ((raw.get("format") or {}).get("regulation") or {}).get("periods")
    fighters = sorted((_fighter(item) for item in raw.get("competitors") or [] if isinstance(item, dict)),
                      key=lambda fighter: fighter["order"])
    weight = str((raw.get("type") or {}).get("abbreviation") or "")
    clock = str(status.get("displayClock") or "")
    period = status.get("period") if isinstance(status.get("period"), int) else 0
    state = str(kind.get("state") or "pre")
    distance = isinstance(rounds, int) and period == rounds and clock == "5:00"
    decision = state == "post" and (distance or any(fighter.get("linescores") for fighter in raw.get("competitors") or []
                                                     if isinstance(fighter, dict)))
    broadcast = next((name for item in raw.get("broadcasts") or [] if isinstance(item, dict)
                      for name in item.get("names") or []), "")
    return {"weight": weight, "weight_zh": weight_zh(weight), "rounds": rounds if isinstance(rounds, int) else 3,
            "date": str(raw.get("date") or ""), "state": state, "period": period, "clock": clock,
            "decision": bool(decision), "detail": str(kind.get("detail") or ""), "broadcast": str(broadcast)[:20],
            "fighters": [{key: value for key, value in fighter.items() if key != "order"} for fighter in fighters[:2]]}


def parse_scoreboard(payload):
    """{"events": [...]} in date order, each card's bouts main event first."""
    events = []
    for raw in (payload or {}).get("events") or [] if isinstance(payload, dict) else []:
        if not isinstance(raw, dict) or not raw.get("competitions"):
            continue
        name = str(raw.get("name") or "UFC").strip()
        label, _, headline = name.partition(":")
        fights = [_fight(item) for item in raw["competitions"] if isinstance(item, dict)]
        fights = [fight for fight in reversed(fights) if len(fight["fighters"]) == 2]
        if not fights:
            continue
        venue = (raw.get("venues") or [{}])[0] if isinstance((raw.get("venues") or [None])[0], dict) else {}
        address = venue.get("address") if isinstance(venue.get("address"), dict) else {}
        dates = [fight["date"] for fight in fights if _date(fight["date"])]
        events.append({"id": str(raw.get("id") or ""), "name": name[:80], "label": label.strip()[:30],
                       "headline": headline.strip()[:60], "numbered": bool(NUMBERED.match(label.strip())),
                       "venue": str(venue.get("fullName") or "")[:40], "city": str(address.get("city") or "")[:30],
                       "start": min(dates, key=_date) if dates else str(raw.get("date") or ""),
                       "main_start": fights[0]["date"] or str(raw.get("date") or ""), "fights": fights[:8]})
    return {"events": sorted(events, key=lambda event: _date(event["start"]) or datetime.max.replace(tzinfo=timezone.utc))}


def result_label(fight):
    """"R3 1:28" for a finish (or the round under way), "判定胜" for a decision, "" before the bout."""
    if fight.get("state") == "post":
        if fight.get("decision"):
            return "判定胜"
        return f"R{fight['period']} {fight['clock']}" if fight.get("period") else "已结束"
    if fight.get("state") == "in":
        return f"R{fight['period']} {fight['clock']}".strip() if fight.get("period") else "进行中"
    return ""


def _window(event):
    start, main = _date(event.get("start")), _date(event.get("main_start"))
    return start, main or start


def build_card(events, now):
    """The card in progress (LIVE), else the one finished within RECENT_WINDOW (RECENT), else the
    next one (NEXT); None without any."""
    now = now.astimezone(timezone.utc)
    timed = [(event, *_window(event)) for event in events or [] if _window(event)[0]]
    for event, start, main in timed:
        done = all(fight["state"] == "post" for fight in event["fights"])
        if any(fight["state"] == "in" for fight in event["fights"]) or (start <= now <= main + LIVE_WINDOW and not done):
            return _card("LIVE", event, timed, now)
    finished = [(event, main) for event, _start, main in timed
                if all(fight["state"] == "post" for fight in event["fights"]) and timedelta(0) <= now - (main + CARD_LENGTH) <= RECENT_WINDOW]
    if finished:
        return _card("RECENT", max(finished, key=lambda item: item[1])[0], timed, now)
    upcoming = [event for event, start, _main in timed if start > now]
    return _card("NEXT", upcoming[0], timed, now) if upcoming else None


def _card(status, event, timed, now):
    later = [item for item, start, _main in timed if start > now and item is not event]
    return {"sport": "UFC", "status": status, "main": event, "upcoming": later[:2]}


def candidate(card, now):
    if not card:
        return None
    event = card["main"]
    _start, main = _window(event)
    state = {"LIVE": "live", "RECENT": "final"}.get(card["status"], "scheduled")
    tier = "A" if event.get("numbered") else "C"
    score = event_ranking.score(state, main, now, tier, end=main + CARD_LENGTH if main else None)
    return {"kind": "extra", "key": "UFC", "card": card, "score": score, "order": 7}


def ttl_seconds(now):
    # Cards are on Saturdays (Sunday in UTC); the rest of the week a few readings a day do.
    return 20 * 60 if now.astimezone(timezone.utc).weekday() in (4, 5, 6) else 3 * 3600


def load_card(plugin, settings, now):
    now = now.astimezone(timezone.utc)

    def fetch(client, deadline):
        first, last = (now - timedelta(days=DAYS_BACK)).date(), (now + timedelta(days=DAYS_AHEAD)).date()
        response = client.request_json("GET", SCOREBOARD_URL, params={"dates": f"{first:%Y%m%d}-{last:%Y%m%d}"},
                                       timeout=extra_feeds.remaining(deadline), max_bytes=MAX_BYTES)
        return parse_scoreboard(response.data)

    data, state = extra_feeds.load(plugin, settings, "ufc", VERSION, fetch, ttl_seconds(now), now, label="ESPN UFC")
    return build_card(data.get("events") or [], now), state
