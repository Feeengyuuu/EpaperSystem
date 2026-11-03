"""Bounded, key-free ESPN pregame summaries for the visible NBA matchups: the moneylines, and from
the same response ESPN's matchup predictor, each team's last five results and its injury report."""

from datetime import date, datetime, timedelta, timezone
import logging
import math
import time

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.http_client import create_single_attempt_http_client

logger = logging.getLogger(__name__)
SUMMARY_URL = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
VERSION = "nba-espn-summary-v2"
# An injury report keeps this many players a team: its season leaders first, then those ruled out.
INJURY_LIMIT = 4
INJURY_STATUS_ORDER = {"out": 0, "doubtful": 1, "questionable": 2, "day-to-day": 2}


def _decimal(value):
    text = str(value or "").strip().upper()
    if text in {"EVEN", "EVENS"}:
        return "2.00"
    try:
        price = float(text)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(price) or abs(price) < 100:
        return ""
    return f"{1 + (price / 100 if price > 0 else 100 / abs(price)):.2f}"


def parse_summary_odds(payload, event_id):
    """Match the requested event, keeping both prices from the same bookmaker."""
    if not isinstance(payload, dict) or str((payload.get("header") or {}).get("id")) != event_id:
        return {}
    for market in payload.get("pickcenter") or []:
        if not isinstance(market, dict):
            continue
        prices = {}
        for side, target in (("away", "team_a"), ("home", "team_b")):
            block = (market.get("moneyline") or {}).get(side) or {}
            candidates = [(block.get(phase) or {}).get("odds") for phase in ("close", "open")]
            candidates.append((market.get(side + "TeamOdds") or {}).get("moneyLine"))
            prices[target] = next((price for value in candidates if (price := _decimal(value))), "")
        if all(prices.values()):
            return {**prices, "bookmaker": str((market.get("provider") or {}).get("name") or "ESPN")}
    return {}


def _team_sides(payload):
    """{ESPN team id: "a" (away) | "b" (home)} from the summary's header."""
    competitions = (payload.get("header") or {}).get("competitions") or [{}]
    competition = competitions[0] if isinstance(competitions[0], dict) else {}
    sides = {}
    for competitor in competition.get("competitors") or []:
        if not isinstance(competitor, dict):
            continue
        team_id = str(competitor.get("id") or (competitor.get("team") or {}).get("id") or "")
        side = {"away": "a", "home": "b"}.get(str(competitor.get("homeAway") or "").lower())
        if team_id and side:
            sides[team_id] = side
    return sides


def _team_side(block, sides):
    team = block.get("team") if isinstance(block.get("team"), dict) else {}
    return sides.get(str(team.get("id") or ""))


def _percent(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number / 100 if math.isfinite(number) and 0 <= number <= 100 else None


def _predictor(payload, sides):
    """{"a": share, "b": share} of ESPN's pregame matchup projection, or {}."""
    block = payload.get("predictor") if isinstance(payload.get("predictor"), dict) else {}
    shares = {}
    for key in ("homeTeam", "awayTeam"):
        team = block.get(key) if isinstance(block.get(key), dict) else {}
        side = sides.get(str(team.get("id") or ""))
        share = _percent(team.get("gameProjection"))
        if side and share is not None:
            shares[side] = share
    if set(shares) != {"a", "b"} or not 0.9 <= shares["a"] + shares["b"] <= 1.1:
        return {}
    return shares


def _form(payload, sides):
    """{side: "WWLWL"}: each team's last five results, oldest first."""
    form = {}
    for block in payload.get("lastFiveGames") or []:
        if not isinstance(block, dict):
            continue
        games = sorted((game for game in block.get("events") or [] if isinstance(game, dict)),
                       key=lambda game: str(game.get("gameDate") or ""))
        results = "".join(result for game in games if (result := str(game.get("gameResult") or "").upper()) in {"W", "L"})
        side = _team_side(block, sides)
        if side and results:
            form[side] = results[-5:]
    return form


def _season_leaders(payload, sides):
    """{side: {short names}} of each team's season leaders (points, rebounds, assists)."""
    leaders = {}
    for block in payload.get("leaders") or []:
        if not isinstance(block, dict):
            continue
        side = _team_side(block, sides)
        for group in block.get("leaders") or []:
            for leader in (group.get("leaders") or []) if isinstance(group, dict) else []:
                name = str(((leader or {}).get("athlete") or {}).get("shortName") or "").strip()
                if side and name:
                    leaders.setdefault(side, set()).add(name)
    return leaders


def _return_day(value):
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        return ""


def _injuries(payload, sides):
    """{side: [{"name", "status" ("OUT" | "GTD"), "return" (ISO day or ""), "key"}]}: the
    players who miss or may miss the game, the team's season leaders first, then those out."""
    leaders = _season_leaders(payload, sides)
    report = {}
    for block in payload.get("injuries") or []:
        if not isinstance(block, dict) or not (side := _team_side(block, sides)):
            continue
        rows = []
        for index, item in enumerate(block.get("injuries") or []):
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().lower()
            name = str((item.get("athlete") or {}).get("shortName") or "").strip()
            if status not in INJURY_STATUS_ORDER or not name:
                continue
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            key = name in leaders.get(side, set())
            rows.append(((not key, INJURY_STATUS_ORDER[status], index), {
                "name": name, "status": "OUT" if status == "out" else "GTD",
                "return": _return_day(details.get("returnDate")), "key": key,
            }))
        rows.sort(key=lambda row: row[0])
        if rows:
            report[side] = [row for _order, row in rows[:INJURY_LIMIT]]
    return report


def parse_summary_insights(payload, event_id):
    """The requested game's matchup predictor, last-five form and injury report, keyed
    ``predictor_a``/``_b`` (0-1 shares), ``form_a``/``_b`` and ``injuries_a``/``_b`` (a = away);
    {} when the payload is another game's or names neither side."""
    if not isinstance(payload, dict) or str((payload.get("header") or {}).get("id")) != event_id:
        return {}
    sides = _team_sides(payload)
    if set(sides.values()) != {"a", "b"}:
        return {}
    insights = {}
    for name, values in (("predictor", _predictor(payload, sides)), ("form", _form(payload, sides)),
                         ("injuries", _injuries(payload, sides))):
        for side, value in values.items():
            insights[f"{name}_{side}"] = value
    return insights


def attach_free_odds(plugin, events, settings, now=None, include_odds=True):
    """At most five requests and 15 seconds; never reuse expired prices or reports on error.
    ``include_odds`` False leaves the moneylines to a keyed provider and attaches the rest."""
    now = now or datetime.now(timezone.utc)
    candidates = [event for event in events if str(event.get("event_id") or "").isdigit()
                  and str(event.get("state") or "").lower() in {"pre", "unstarted", "scheduled"}
                  and isinstance(event.get("start"), datetime)
                  and timedelta(0) <= event["start"] - now <= timedelta(days=7)][:5]
    if not candidates:
        return events
    path = plugin._sports_dashboard_cache_dir() / "nba_espn_odds.json"
    cache = plugin._read_json_file(path)
    offers = cache.get("offers", {}) if cache.get("version") == VERSION else {}
    offers = offers if isinstance(offers, dict) else {}
    retained, attached = {}, {}
    deadline = time.monotonic() + 15
    cache_only = plugin._bool_setting(settings, "_inkypi_ewc_cache_only", False)
    with create_single_attempt_http_client() as client:
        for event in candidates:
            event_id = str(event["event_id"])
            record = offers.get(event_id) or {}
            if not isinstance(record, dict):
                record = {}
            found = record.get("odds") or record.get("insights")
            if not plugin._cache_is_fresh_seconds(record, 1800 if found else 300, now):
                remaining = deadline - time.monotonic()
                if cache_only or remaining <= 0:
                    continue
                try:
                    response = client.request_json("GET", SUMMARY_URL, params={"event":event_id},
                                                   timeout=min(5, remaining), max_bytes=2 * 1024 * 1024)
                    odds = parse_summary_odds(response.data, event_id)
                    insights = parse_summary_insights(response.data, event_id)
                except (TaskCancelled, TaskDeadlineExceeded):
                    raise
                except Exception as exc:
                    logger.warning("NBA free odds unavailable for %s: %s", event_id, type(exc).__name__)
                    odds, insights = {}, {}
                record = {"fetched_at":now.isoformat(), "odds":odds, "insights":insights}
            retained[event_id] = record
            extra = dict(record["insights"]) if isinstance(record.get("insights"), dict) else {}
            if include_odds and record.get("odds"):
                extra["odds"] = record["odds"]
            if extra:
                attached[event_id] = extra
    if not cache_only and retained != offers:
        try:
            plugin._write_json_file(path, {"version":VERSION, "offers":retained})
        except OSError:
            logger.warning("Failed to cache NBA free odds")
    return [{**event, **attached[str(event["event_id"])]}
            if str(event.get("event_id")) in attached else event for event in events]
