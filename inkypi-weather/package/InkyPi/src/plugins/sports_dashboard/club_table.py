"""Where each club stands in its league: place, points and games played from ESPN's key-free
standings resource.

The club panel shows one league at a time, so one table is asked for per page. A table only
changes when a match ends: it is kept an hour, asked for again a quarter of an hour after a
failed request, and no longer shown once it is three days old.
"""

from collections.abc import Mapping

from utils.http_client import create_single_attempt_http_client

CACHE_VERSION = "sports-dashboard-club-espn-table-v1"
CACHE_SECONDS = 60 * 60
RETRY_SECONDS = 15 * 60
MAX_AGE_SECONDS = 3 * 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 8


def table_url(host, slug):
    # The "apis/site/v2" path the scoreboards use answers this resource with an empty body.
    return f"https://{host}/apis/v2/sports/soccer/{slug}/standings"


def fetch_table(session, url, timeout=REQUEST_TIMEOUT_SECONDS):
    """ESPN's standings payload for ``url``, asked once; raises when the request or its JSON fails."""
    if getattr(session, "_inkypi_adapter_retries", False):
        with create_single_attempt_http_client() as owned:
            return fetch_table(owned.session, url, timeout)
    response = session.get(url, headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"}, timeout=timeout)
    try:
        response.raise_for_status()
        payload = response.json()
    finally:
        if hasattr(response, "close"):
            response.close()
    if not isinstance(payload, Mapping):
        raise ValueError("football ESPN table response is not an object")
    return payload


def _whole(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_table(payload):
    """{ESPN team id: {"rank", "points", "played", "name"}} from a standings payload.

    A league split into several tables (MLS's conferences) has no single order, so its clubs
    carry ``rank`` None. Raises ValueError when the payload holds no table of at least two clubs.
    """
    payload = payload if isinstance(payload, Mapping) else {}
    groups = [child.get("standings") for child in payload.get("children") or [] if isinstance(child, Mapping)]
    groups = [group for group in groups or [payload.get("standings")]
              if isinstance(group, Mapping) and isinstance(group.get("entries"), list) and group["entries"]]
    table = {}
    for group in groups:
        for entry in group["entries"]:
            team = entry.get("team") if isinstance(entry, Mapping) else None
            team_id = str((team or {}).get("id") or "").strip()
            stats = {str(stat.get("name")): stat.get("value", stat.get("displayValue"))
                     for stat in entry.get("stats") or [] if isinstance(stat, Mapping)} if team_id else {}
            points = _whole(stats.get("points"))
            if not team_id or points is None:
                continue
            table[team_id] = {
                "rank": _whole(stats.get("rank")) if len(groups) == 1 else None,
                "points": points,
                "played": _whole(stats.get("gamesPlayed")),
                "name": str(team.get("displayName") or team.get("name") or "").strip(),
            }
    if len(table) < 2:
        raise ValueError("football ESPN table response holds no league table")
    return table


def team_row(table, team_id, name=""):
    """The club's row: by its ESPN id, else (an event from another provider) by its name."""
    row = (table or {}).get(str(team_id or "").strip())
    if row is None:
        wanted = str(name or "").strip().casefold()
        row = next((item for item in (table or {}).values()
                    if wanted and str(item.get("name") or "").casefold() == wanted), None)
    return dict(row) if isinstance(row, Mapping) else None
