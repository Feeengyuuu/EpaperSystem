"""MotoGP for the lower panel: the keyless Pulselive results API behind motogp.com (a handful of
small requests: the season, its events, the weekend's sessions, a race classification and the
riders' standings).

The panel follows the Grand Prix weekend under way, else the next one: its sessions with their
times (a session in progress makes the panel live), the latest race or sprint result (the
weekend's own once one has run, else the last Grand Prix's race) and the championship's top
riders. A race ranks with the leagues' big games, a sprint or qualifying above practice.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

from . import event_ranking, extra_feeds

API = "https://api.motogp.pulselive.com/motogp/v1/results"
VERSION = "motogp-results-v1"
MAX_BYTES = 512 * 1024
WEEKEND_TTL, QUIET_TTL = 15 * 60, 6 * 3600
RESULT_ROWS, STANDING_ROWS = 10, 10
SESSION_LABELS = {"PR": "练习", "SPR": "冲刺赛", "RAC": "正赛", "WUP": "热身"}
SESSION_LENGTH = {"FP": 45, "PR": 60, "Q": 20, "SPR": 30, "RAC": 50, "WUP": 15}
SESSION_TIERS = {"RAC": "A", "SPR": "B", "Q": "B"}
PLACES_ZH = {"QATAR": "卡塔尔", "MALAYSIA": "马来西亚", "AUSTRALIA": "澳大利亚", "PORTUGAL": "葡萄牙",
             "VALENCIA": "瓦伦西亚", "SPAIN": "西班牙", "CATALONIA": "加泰罗尼亚", "CATALUNYA": "加泰罗尼亚",
             "ARAGON": "阿拉贡", "FRANCE": "法国", "ITALY": "意大利", "SAN MARINO": "圣马力诺",
             "SAN MARINO AND THE RIMINI COAST": "圣马力诺", "GERMANY": "德国", "NETHERLANDS": "荷兰",
             "THE NETHERLANDS": "荷兰", "GREAT BRITAIN": "英国", "AUSTRIA": "奥地利", "HUNGARY": "匈牙利",
             "CZECHIA": "捷克", "CZECH REPUBLIC": "捷克", "JAPAN": "日本", "INDONESIA": "印度尼西亚",
             "THAILAND": "泰国", "ARGENTINA": "阿根廷", "THE AMERICAS": "美洲", "AMERICAS": "美洲", "INDIA": "印度",
             "KAZAKHSTAN": "哈萨克斯坦", "BRAZIL": "巴西", "EMILIA-ROMAGNA": "艾米利亚-罗马涅",
             "SOLIDARITY": "团结", "USA": "美国"}


def _when(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def grand_prix_zh(name):
    match = re.search(r"GRAND PRIX OF (.+)$", str(name or "").upper().strip())
    place = PLACES_ZH.get(match.group(1).strip()) if match else None
    return f"{place}大奖赛" if place else ""


def _event(raw):
    circuit = raw.get("circuit") if isinstance(raw.get("circuit"), dict) else {}
    country = raw.get("country") if isinstance(raw.get("country"), dict) else {}
    name = str(raw.get("name") or "").strip()
    return {"id": str(raw.get("id") or ""), "name": name[:60], "sponsored": str(raw.get("sponsored_name") or name).strip()[:70],
            "short": str(raw.get("short_name") or "")[:4], "name_zh": grand_prix_zh(name),
            "circuit": str(circuit.get("name") or "")[:50], "iso": str(country.get("iso") or "")[:2],
            "country": str(country.get("name") or "")[:30], "start": str(raw.get("date_start") or "")[:10],
            "end": str(raw.get("date_end") or "")[:10], "status": str(raw.get("status") or "")}


def pick_events(events, now):
    """{"current", "last", "next"}: the Grand Prix under way (its weekend, or the API's CURRENT), the
    last finished one and the next one; tests are left out."""
    today = now.astimezone(timezone.utc).date()
    grands_prix = sorted((_event(raw) for raw in events or [] if isinstance(raw, dict) and not raw.get("test")),
                         key=lambda event: _day(event["start"]) or date.max)
    current = next((event for event in grands_prix if event["status"] == "CURRENT"
                    or ((_day(event["start"]) or date.max) <= today <= (_day(event["end"]) or date.min))), None)
    finished = [event for event in grands_prix if event is not current and (_day(event["end"]) or date.max) < today]
    upcoming = [event for event in grands_prix if event is not current and (_day(event["start"]) or date.min) > today]
    return {"current": current, "last": finished[-1] if finished else None, "next": upcoming[0] if upcoming else None}


def normalize_sessions(sessions):
    rows = []
    for raw in sessions or []:
        if not isinstance(raw, dict) or not _when(raw.get("date")):
            continue
        kind = str(raw.get("type") or "")
        number = raw.get("number") if isinstance(raw.get("number"), int) else None
        label = SESSION_LABELS.get(kind) or f"{kind}{number or ''}"
        rows.append({"id": str(raw.get("id") or ""), "type": kind, "number": number, "label": label,
                     "start": _when(raw.get("date")).isoformat(), "status": str(raw.get("status") or "")})
    return sorted(rows, key=lambda row: row["start"])


def _rider_row(raw):
    rider = raw.get("rider") if isinstance(raw.get("rider"), dict) else {}
    country = rider.get("country") if isinstance(rider.get("country"), dict) else {}
    return {"pos": raw.get("position"), "number": rider.get("number"), "name": str(rider.get("full_name") or "")[:32],
            "iso": str(country.get("iso") or "")[:2], "team": str((raw.get("team") or {}).get("name") or "")[:40],
            "constructor": str((raw.get("constructor") or {}).get("name") or "")[:20]}


def normalize_classification(payload):
    rows = []
    for raw in (payload or {}).get("classification") or [] if isinstance(payload, dict) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("position"), int):
            continue
        gap = str(((raw.get("gap") or {}).get("first")) or "") if isinstance(raw.get("gap"), dict) else ""
        row = _rider_row(raw)
        row.update(time=str(raw.get("time") or ""), gap=f"+{gap}" if gap and gap.strip("0.:") else "",
                   points=raw.get("points") if isinstance(raw.get("points"), (int, float)) else None)
        rows.append(row)
    return sorted(rows, key=lambda row: row["pos"])[:RESULT_ROWS]


def normalize_standings(payload):
    rows = []
    for raw in (payload or {}).get("classification") or [] if isinstance(payload, dict) else []:
        if isinstance(raw, dict) and isinstance(raw.get("position"), int):
            row = _rider_row(raw)
            row["points"] = raw.get("points") if isinstance(raw.get("points"), (int, float)) else 0
            rows.append(row)
    return sorted(rows, key=lambda row: row["pos"])[:STANDING_ROWS]


def _length(session):
    return timedelta(minutes=SESSION_LENGTH.get(session.get("type"), 45))


def build_card(data, now):
    """{"sport": "MOTOGP", "status", "weekend", "sessions", "live_session", "next_session",
    "result", "standings", "next_event"} or None without a weekend to show."""
    data = data or {}
    weekend = data.get("current") or data.get("next")
    if not weekend:
        return None
    now = now.astimezone(timezone.utc)
    sessions = data.get("sessions") or []
    live = next((session for session in sessions
                 if _when(session["start"]) <= now < _when(session["start"]) + _length(session)), None)
    upcoming = next((session for session in sessions if _when(session["start"]) > now), None)
    status = "LIVE" if live else ("NEXT" if upcoming or not data.get("current") else "RECENT")
    return {"sport": "MOTOGP", "status": status, "weekend": weekend, "sessions": sessions, "live_session": live,
            "next_session": upcoming, "result": data.get("result") or {}, "standings": data.get("standings") or [],
            "next_event": data.get("next") if data.get("current") else None}


def candidate(card, now):
    if not card:
        return None
    if card.get("live_session"):
        tier = SESSION_TIERS.get(card["live_session"]["type"], "C")
        return {"kind": "extra", "key": "MOTOGP", "card": card, "score": event_ranking.score("live", None, now, tier), "order": 9}
    scores = []
    for session in card.get("sessions") or []:
        start = _when(session["start"])
        tier = SESSION_TIERS.get(session["type"], "C")
        if start > now:
            scores.append(event_ranking.score("scheduled", start, now, tier))
        elif session["type"] in ("RAC", "SPR"):
            scores.append(event_ranking.score("final", start, now, tier, end=start + _length(session)))
    if not scores:
        start = datetime.combine(_day(card["weekend"]["start"]) or now.date(), time(12), tzinfo=timezone.utc)
        scores.append(event_ranking.score("scheduled", start, now, "A"))
    return {"kind": "extra", "key": "MOTOGP", "card": card, "score": max(scores), "order": 9}


def _get(client, deadline, path, params=None):
    response = client.request_json("GET", f"{API}/{path}", params=params, timeout=extra_feeds.remaining(deadline),
                                   max_bytes=MAX_BYTES)
    return response.data


def fetch(client, deadline, now):
    seasons = _get(client, deadline, "seasons")
    season = next((item for item in seasons or [] if isinstance(item, dict) and item.get("current")), None)
    if not season:
        raise ValueError("MotoGP lists no current season")
    picked = pick_events(_get(client, deadline, "events", {"seasonUuid": season["id"]}), now)
    weekend = picked["current"] or picked["next"]
    if not weekend:
        return {"season": season.get("year")}
    categories = _get(client, deadline, "categories", {"eventUuid": weekend["id"]})
    category = next((item for item in categories or [] if isinstance(item, dict)
                     and str(item.get("name") or "").startswith("MotoGP")), None)
    if not category:
        raise ValueError("MotoGP category missing")
    sessions = normalize_sessions(_get(client, deadline, "sessions", {"eventUuid": weekend["id"], "categoryUuid": category["id"]}))
    done = [session for session in sessions if session["type"] in ("RAC", "SPR") and session["status"] == "FINISHED"]
    result_session, result_event = (done[-1], weekend) if done else (None, picked["last"])
    if result_session is None and result_event:
        last_sessions = normalize_sessions(_get(client, deadline, "sessions",
                                                {"eventUuid": result_event["id"], "categoryUuid": category["id"]}))
        result_session = next((session for session in reversed(last_sessions) if session["type"] == "RAC"), None)
    result = {}
    if result_session:
        rows = normalize_classification(_get(client, deadline, f"session/{result_session['id']}/classification",
                                             {"test": "false"}))
        result = {"session": result_session["type"], "event": result_event["short"],
                  "event_zh": result_event["name_zh"], "rows": rows}
    standings = normalize_standings(_get(client, deadline, "standings",
                                         {"seasonUuid": season["id"], "categoryUuid": category["id"]}))
    return {"season": season.get("year"), **picked, "sessions": sessions, "result": result, "standings": standings}


def load_card(plugin, settings, now):
    now = now.astimezone(timezone.utc)
    cache = plugin._read_json_file(plugin._sports_dashboard_cache_dir() / "extra_motogp.json")
    weekend = ((cache or {}).get("data") or {}).get("current") if isinstance(cache, dict) else None
    ttl = WEEKEND_TTL if weekend else QUIET_TTL
    data, state = extra_feeds.load(plugin, settings, "motogp", VERSION, lambda client, deadline: fetch(client, deadline, now),
                                   ttl, now, label="MOTOGP")
    return build_card(data, now), state
