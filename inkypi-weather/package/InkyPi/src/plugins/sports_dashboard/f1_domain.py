"""F1 provider parsing and event selection, with no runtime, cache, or renderer dependencies.

The dashboard adapter retains the established dictionaries at its compatibility boundary.
Times, windows, and ranking semantics are shared by polling and display selection.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

F1_SESSION_PREGAME_WINDOW = timedelta(minutes=15)
F1_SESSION_RESULT_WINDOW = timedelta(hours=6)


def _int_value(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None



def should_poll(parsed, now):
    for race in (parsed or {}).get("races") or []:
        for session in race.get("sessions") or []:
            if _is_f1_live_session(session, now):
                return True
            start = session.get("start")
            if isinstance(start, datetime) and start - F1_SESSION_PREGAME_WINDOW <= now < start:
                return True
    return False


def parse_bundle(payload, timezone_info):
    races = [
        _parse_f1_jolpica_race(race, timezone_info)
        for race in _f1_races_from_payload((payload or {}).get("schedule") or payload)
    ]
    races = [race for race in races if race]
    races.sort(key=lambda item: item.get("race_start") or datetime.max.replace(tzinfo=timezone.utc))
    return {
        "races": races,
        "last_result": _parse_f1_last_result((payload or {}).get("results"), timezone_info),
        "driver_standings": _parse_f1_driver_standings((payload or {}).get("driver_standings")),
        "constructor_standings": _parse_f1_constructor_standings((payload or {}).get("constructor_standings")),
    }


def _f1_races_from_payload(payload):
    mr_data = (payload or {}).get("MRData") or {}
    race_table = mr_data.get("RaceTable") or {}
    races = race_table.get("Races") or []
    return races if isinstance(races, list) else []


def _parse_f1_jolpica_race(race, timezone_info):
    if not isinstance(race, Mapping):
        return None
    circuit = race.get("Circuit") or {}
    location = circuit.get("Location") or {}
    race_start = _parse_f1_date_time(race.get("date"), race.get("time"), timezone_info)
    sessions = []
    for source_key, label, title, duration in _f1_session_specs():
        if source_key == "Race":
            start = race_start
        else:
            source = race.get(source_key) or {}
            start = _parse_f1_date_time(source.get("date"), source.get("time"), timezone_info)
        if not start:
            continue
        sessions.append(
            {
                "key": source_key,
                "label": label,
                "title": title,
                "start": start,
                "duration": duration,
            }
        )
    sessions.sort(key=lambda item: item["start"])
    return {
        "season": str(race.get("season") or ""),
        "round": str(race.get("round") or ""),
        "race_name": str(race.get("raceName") or "Formula 1").strip() or "Formula 1",
        "circuit_name": str(circuit.get("circuitName") or "").strip(),
        "locality": str(location.get("locality") or "").strip(),
        "country": str(location.get("country") or "").strip(),
        "race_start": race_start,
        "sessions": sessions,
    }


def _f1_session_specs():
    return (
        ("FirstPractice", "FP1", "FP1", timedelta(hours=2)),
        ("SecondPractice", "FP2", "FP2", timedelta(hours=2)),
        ("ThirdPractice", "FP3", "FP3", timedelta(hours=2)),
        ("SprintQualifying", "SQ", "SPRINT Q", timedelta(hours=2)),
        ("SprintShootout", "SQ", "SPRINT Q", timedelta(hours=2)),
        ("Sprint", "SPRINT", "SPRINT", timedelta(hours=2)),
        ("Qualifying", "Q", "QUALIFYING", timedelta(hours=2)),
        ("Race", "RACE", "RACE", timedelta(hours=4)),
    )


def _parse_f1_date_time(date_value, time_value, timezone_info):
    date_text = str(date_value or "").strip()
    if not date_text:
        return None
    time_text = str(time_value or "00:00:00Z").strip() or "00:00:00Z"
    if time_text.endswith("Z"):
        time_text = f"{time_text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(f"{date_text}T{time_text}")
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone_info)


def _parse_f1_last_result(payload, timezone_info):
    races = _f1_races_from_payload(payload)
    if not races:
        return None
    race = races[0]
    top = []
    for item in (race.get("Results") or [])[:5]:
        driver = item.get("Driver") or {}
        constructor = item.get("Constructor") or {}
        top.append(
            {
                "position": _int_value(item.get("position")) or len(top) + 1,
                "driver_code": _f1_driver_code(driver),
                "driver_name": _f1_driver_name(driver),
                "constructor": str(constructor.get("name") or "").strip(),
                "gap": _f1_result_gap(item),
                "status": str(item.get("status") or "").strip(),
            }
        )
    return {
        "round": str(race.get("round") or ""),
        "race_name": str(race.get("raceName") or "").strip(),
        "start": _parse_f1_date_time(race.get("date"), race.get("time"), timezone_info),
        "top": top,
    }


def _parse_f1_driver_standings(payload):
    standings = _f1_standings_list(payload, "DriverStandings")
    result = []
    for item in standings[:5]:
        driver = item.get("Driver") or {}
        result.append(
            {
                "position": _int_value(item.get("position")) or len(result) + 1,
                "driver_code": _f1_driver_code(driver),
                "points": str(item.get("points") or "0"),
                "wins": str(item.get("wins") or "0"),
            }
        )
    return result


def _parse_f1_constructor_standings(payload):
    standings = _f1_standings_list(payload, "ConstructorStandings")
    result = []
    for item in standings[:5]:
        constructors = item.get("Constructor") or item.get("Constructors") or {}
        result.append(
            {
                "position": _int_value(item.get("position")) or len(result) + 1,
                "constructor": str(constructors.get("name") or "").strip(),
                "points": str(item.get("points") or "0"),
                "wins": str(item.get("wins") or "0"),
            }
        )
    return result


def _f1_standings_list(payload, key):
    mr_data = (payload or {}).get("MRData") or {}
    table = mr_data.get("StandingsTable") or {}
    lists = table.get("StandingsLists") or []
    if not lists:
        return []
    standings = (lists[0] or {}).get(key) or []
    return standings if isinstance(standings, list) else []


def _f1_driver_code(driver):
    code = str((driver or {}).get("code") or (driver or {}).get("name_acronym") or "").strip().upper()
    if code:
        return code[:3]
    family_name = str((driver or {}).get("familyName") or (driver or {}).get("last_name") or "").strip().upper()
    if len(family_name) >= 3:
        return family_name[:3]
    given_name = str((driver or {}).get("givenName") or (driver or {}).get("first_name") or "").strip().upper()
    return (family_name or given_name or "DRV")[:3]


def _f1_driver_name(driver):
    given = str((driver or {}).get("givenName") or (driver or {}).get("first_name") or "").strip()
    family = str((driver or {}).get("familyName") or (driver or {}).get("last_name") or "").strip()
    full = str((driver or {}).get("full_name") or "").strip()
    return full or " ".join(part for part in (given, family) if part) or _f1_driver_code(driver)


def _f1_result_gap(item):
    time_info = (item or {}).get("Time") or {}
    if time_info.get("time"):
        return str(time_info.get("time")).strip()
    status = str((item or {}).get("status") or "").strip()
    return status or "-"


def select_events(data, now):
    races = (data or {}).get("races") or []
    sessions = []
    for race in races:
        for session in race.get("sessions") or []:
            entry = dict(session)
            entry["race"] = race
            sessions.append(entry)
    sessions.sort(key=lambda item: item["start"])
    live_sessions = [session for session in sessions if _is_f1_live_session(session, now)]
    upcoming_sessions = [session for session in sessions if session.get("start") and session["start"] >= now]
    recent_sessions = sorted(
        [session for session in sessions if session.get("start") and session["start"] < now],
        key=lambda item: item["start"],
        reverse=True,
    )
    next_session = upcoming_sessions[0] if upcoming_sessions else None
    live_session = live_sessions[0] if live_sessions else None
    weekend_race = _f1_weekend_race(races, now)
    next_race = _f1_next_race(races, now)
    recent_race = _f1_recent_race(races, now)
    main_race = (live_session or {}).get("race") or weekend_race or next_race or recent_race
    if live_session:
        status = "LIVE"
    elif next_session or next_race:
        status = "NEXT"
    elif (data or {}).get("last_result"):
        status = "RECENT"
    else:
        status = "BREAK"
    weekend_sessions = list((main_race or {}).get("sessions") or [])
    return {
        "status": status,
        "live_session": live_session,
        "next_session": next_session,
        "recent_session": recent_sessions[0] if recent_sessions else None,
        "main_race": main_race,
        "next_race": next_race,
        "recent_race": recent_race,
        "weekend_sessions": weekend_sessions,
        "last_result": (data or {}).get("last_result"),
        "driver_standings": (data or {}).get("driver_standings") or [],
        "constructor_standings": (data or {}).get("constructor_standings") or [],
        "leaderboard": [],
        "weather": None,
    }


def _is_f1_live_session(session, now):
    start = (session or {}).get("start")
    duration = (session or {}).get("duration") or timedelta(hours=2)
    if not isinstance(start, datetime) or now is None:
        return False
    return start - F1_SESSION_PREGAME_WINDOW <= now < start + duration


def _f1_weekend_race(races, now):
    for race in races or []:
        sessions = race.get("sessions") or []
        starts = [session.get("start") for session in sessions if isinstance(session.get("start"), datetime)]
        if not starts:
            continue
        start = min(starts) - timedelta(hours=12)
        end = max(starts) + F1_SESSION_RESULT_WINDOW
        if start <= now <= end:
            return race
    return None


def _f1_next_race(races, now):
    for race in races or []:
        sessions = race.get("sessions") or []
        starts = [session.get("start") for session in sessions if isinstance(session.get("start"), datetime)]
        candidate = min(starts) if starts else race.get("race_start")
        if isinstance(candidate, datetime) and candidate >= now:
            return race
    return None


def _f1_recent_race(races, now):
    recent = []
    for race in races or []:
        race_start = race.get("race_start")
        if isinstance(race_start, datetime) and race_start < now:
            recent.append(race)
    return sorted(recent, key=lambda item: item.get("race_start"), reverse=True)[0] if recent else None
