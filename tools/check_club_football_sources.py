from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INKYPI_ROOT = ROOT / "inkypi-weather" / "package" / "InkyPi"
SRC = INKYPI_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugins.sports_dashboard.sports_dashboard import (  # noqa: E402
    CLUB_FOOTBALL_LEAGUES,
    SportsDashboard,
)


JSON_LIMIT = 6 * 1024 * 1024
LOGO_LIMIT = 2 * 1024 * 1024
USER_AGENT = "InkyPi club-football source check/1.0"
FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"


def summarize_checks(results):
    lines = []
    failed = False
    for result in results or []:
        status = str(result.get("status") or "FAIL").upper()
        name = str(result.get("name") or "unnamed check")
        detail = str(result.get("detail") or "")
        if status == "FAIL":
            failed = True
        lines.append(f"{status} {name}: {detail}")
    return (1 if failed else 0), lines


def _read_limited(response, max_bytes):
    data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} bytes")
    return data


def _request_bytes(url, *, headers=None, timeout=20, max_bytes=JSON_LIMIT):
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _read_limited(response, max_bytes)


def _request_json(url, *, headers=None, timeout=20):
    return json.loads(_request_bytes(url, headers=headers, timeout=timeout).decode("utf-8"))


def _safe_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return type(exc).__name__


def _espn_smoke_url(league_code, now):
    base_url = SportsDashboard._club_espn_scoreboard_url(league_code)
    start = (now - timedelta(days=30)).strftime("%Y%m%d")
    end = (now + timedelta(days=120)).strftime("%Y%m%d")
    return f"{base_url}?{urllib.parse.urlencode({'dates': f'{start}-{end}', 'limit': '1000'})}"


def _payload_team_logo(payload):
    for event in payload.get("events") or []:
        competitions = (event or {}).get("competitions") or []
        competition = competitions[0] if competitions else {}
        for competitor in competition.get("competitors") or []:
            logo_url = SportsDashboard._club_first_logo((competitor or {}).get("team") or {})
            if logo_url:
                return logo_url
    return ""


def _payload_team_name_coverage(payload, league_code):
    checked = set()
    missing = set()
    for event in payload.get("events") or []:
        competitions = (event or {}).get("competitions") or []
        competition = competitions[0] if competitions else {}
        for competitor in competition.get("competitors") or []:
            team = (competitor or {}).get("team") or {}
            english_name = str(
                team.get("displayName")
                or team.get("shortDisplayName")
                or team.get("name")
                or ""
            ).strip()
            if not english_name:
                continue
            checked.add(english_name)
            localized = SportsDashboard._club_team_zh_name(
                league_code, english_name, team_id=team.get("id")
            )
            if localized == "待定球队" or not any(
                "\u3400" <= character <= "\u9fff" for character in localized
            ):
                missing.add(english_name)

    if missing:
        return {
            "name": f"{league_code} Chinese team names",
            "status": "FAIL",
            "detail": "unmapped: " + ", ".join(sorted(missing)),
        }
    return {
        "name": f"{league_code} Chinese team names",
        "status": "PASS",
        "detail": f"{len(checked)} unique teams localized",
    }


def _payload_league_logo(payload, expected_slug):
    leagues = payload.get("leagues") or []
    league = next(
        (item for item in leagues if str((item or {}).get("slug") or "") == expected_slug),
        leagues[0] if leagues else {},
    )
    return SportsDashboard._club_first_logo(league)


def _check_logo(name, url):
    if not url:
        return {"name": name, "status": "FAIL", "detail": "missing URL"}
    try:
        data = _request_bytes(url, timeout=20, max_bytes=LOGO_LIMIT)
        if not SportsDashboard._team_logo_data_is_safe_to_decode(data):
            raise ValueError("unsafe or undecodable image")
        logo = SportsDashboard._team_logo_from_bytes(data, 64)
        if logo is None or logo.width <= 0 or logo.height <= 0:
            raise ValueError("decode returned no image")
        return {
            "name": name,
            "status": "PASS",
            "detail": f"decoded {logo.width}x{logo.height}",
        }
    except Exception as exc:
        return {"name": name, "status": "FAIL", "detail": _safe_error(exc)}


def run_espn_checks(now=None):
    current = now or datetime.now(timezone.utc)
    results = []
    for league_code, registry in CLUB_FOOTBALL_LEAGUES.items():
        try:
            payload = _request_json(_espn_smoke_url(league_code, current), timeout=25)
            if not isinstance(payload, dict):
                raise ValueError("JSON root is not an object")
            leagues = payload.get("leagues") or []
            returned_slugs = {str((item or {}).get("slug") or "") for item in leagues}
            if registry["espn_slug"] not in returned_slugs:
                raise ValueError("unexpected league slug")
            events = payload.get("events") or []
            if not isinstance(events, list):
                raise ValueError("events is not a list")
            results.append(
                {
                    "name": f"ESPN {league_code}",
                    "status": "PASS",
                    "detail": f"scoreboard parsed, {len(events)} events",
                }
            )
            results.append(_payload_team_name_coverage(payload, league_code))
            results.append(
                _check_logo(
                    f"{league_code} league logo",
                    _payload_league_logo(payload, registry["espn_slug"]),
                )
            )
            results.append(
                _check_logo(f"{league_code} team logo", _payload_team_logo(payload))
            )
        except Exception as exc:
            results.append(
                {"name": f"ESPN {league_code}", "status": "FAIL", "detail": _safe_error(exc)}
            )
    return results


def _football_data_key():
    for name in (
        "FOOTBALL_DATA",
        "FOOTBALL_DATA_KEY",
        "FOOTBALL_DATA_API_KEY",
        "FOOTBALL_DATA_TOKEN",
        "FOOTBALLDATA_KEY",
        "FOOTBALLDATA_TOKEN",
        "footballDataKey",
    ):
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def run_football_data_checks(now=None):
    key = _football_data_key()
    if not key:
        return [{"name": "football-data.org", "status": "SKIP", "detail": "no key"}]
    current = now or datetime.now(timezone.utc)
    date_from = (current - timedelta(days=7)).date().isoformat()
    date_to = (current + timedelta(days=45)).date().isoformat()
    results = []
    for league_code in CLUB_FOOTBALL_LEAGUES:
        url = (
            f"{FOOTBALL_DATA_BASE_URL}/competitions/{league_code}/matches?"
            + urllib.parse.urlencode({"dateFrom": date_from, "dateTo": date_to})
        )
        try:
            payload = _request_json(
                url,
                headers={"X-Auth-Token": key},
                timeout=25,
            )
            matches = payload.get("matches") if isinstance(payload, dict) else None
            if not isinstance(matches, list):
                raise ValueError("matches is not a list")
            results.append(
                {
                    "name": f"football-data.org {league_code}",
                    "status": "PASS",
                    "detail": f"{len(matches)} matches",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "name": f"football-data.org {league_code}",
                    "status": "FAIL",
                    "detail": _safe_error(exc),
                }
            )
    return results


def main():
    results = run_espn_checks() + run_football_data_checks()
    exit_code, lines = summarize_checks(results)
    for line in lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())