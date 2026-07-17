# Club Football Live Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inactive World Cup slot with a locally verified, real-data panel for the Premier League, La Liga, Bundesliga, Serie A, and Ligue 1 while preserving automatic World Cup return, honest live status, existing neighboring panels, and the approved 536×240 layout.

**Architecture:** Add one data/selection mixin and one renderer mixin, then route only the existing top-left panel through a World Cup/club selector. football-data.org remains the schedule/standings source, ESPN supplies live status and logos, normalized events merge before selection, and a dedicated atomic live-state file plugs into the existing generic SportsDashboard refresh hook.

**Tech Stack:** Python 3.11, Pillow, Requests through the existing HTTP session, existing JSON/cache helpers, pytest, PowerShell test runner, and a small opt-in local network smoke script.

## Global Constraints

- Follow `docs/superpowers/specs/2026-07-16-club-football-live-tracking-design.md` as the source of truth.
- The five allowed league codes are exactly `PL`, `PD`, `BL1`, `SA`, and `FL1`; never interpolate an unvalidated setting into a request URL.
- Draw directly into the final top-left slot. The 800×480 target must produce an exact 536×240 club panel and must not resize or move the right LPL or lower NBA pixels.
- In every rail row, the home name is left-aligned and the away name is right-aligned around a fixed central score/`VS` column.
- Only provider-confirmed live state may render `LIVE`; a local kickoff window may schedule polling but must render as pending confirmation.
- A failed source or logo must degrade independently. Never blank the complete panel and never delete a last-good cache after a failed fetch.
- Reuse `_football_data_key`, `_football_data_get_json`, `_load_team_logo`, `_read_json_file`, `_write_json_file`, `_sports_dashboard_cache_dir`, and provenance helpers where their contracts fit; do not duplicate generic network or image-safety code.
- Do not refactor World Cup, LPL, NBA, offseason hub, or presentation scheduling beyond the narrow integration seams listed below.
- Keep `.learnings/LEARNINGS.md` out of feature commits unless the root agent explicitly decides to include the already-recorded learning.
- Every implementation task starts with a failing test and ends with focused green tests plus `git diff --check`.

---

### Task 1: Add the league registry, settings parser, mode selector, and mixin wiring

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py:4-38,1763-1775`
- Test: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`

**Interfaces:**
- Produces `CLUB_FOOTBALL_LEAGUES`, `CLUB_FOOTBALL_LIVE_STATE_VERSION`, and `ClubFootballMixin`.
- Produces `SportsDashboard._club_football_enabled_leagues(settings) -> tuple[str, ...]`.
- Produces `SportsDashboard._football_panel_mode(settings) -> str`.
- Produces `SportsDashboard._select_football_panel_kind(mode, now, worldcup_summary) -> str`.
- `worldcup_summary` is either `None` or a mapping with aware `first_start`, `final_start`, optional `final_end`, and `final_complete`.

- [ ] **Step 1: Write failing registry, whitelist, manual-mode, and boundary tests**

Add tests next to the existing World Cup configuration tests:

```python
def test_club_football_registry_maps_all_five_leagues():
    assert {
        code: data["espn_slug"]
        for code, data in CLUB_FOOTBALL_LEAGUES.items()
    } == {
        "PL": "eng.1",
        "PD": "esp.1",
        "BL1": "ger.1",
        "SA": "ita.1",
        "FL1": "fra.1",
    }


def test_club_football_enabled_leagues_preserves_registry_order_and_whitelist():
    settings = {"clubFootballEnabledLeagues": "FL1,PL,unknown,PD,PL"}
    assert SportsDashboard._club_football_enabled_leagues(settings) == ("PL", "PD", "FL1")
    assert SportsDashboard._club_football_enabled_leagues({}) == ("PL", "PD", "BL1", "SA", "FL1")


def test_football_panel_manual_modes_override_schedule():
    now = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    summary = {
        "first_start": now + timedelta(days=30),
        "final_start": now + timedelta(days=60),
        "final_complete": False,
    }
    assert SportsDashboard._select_football_panel_kind("worldcup", now, None) == "worldcup"
    assert SportsDashboard._select_football_panel_kind("club", now, summary) == "club"


def test_football_panel_auto_mode_uses_fourteen_day_lead_and_twenty_four_hour_tail():
    first = datetime(2030, 6, 8, 19, tzinfo=timezone.utc)
    final = datetime(2030, 7, 8, 19, tzinfo=timezone.utc)
    summary = {
        "first_start": first,
        "final_start": final,
        "final_end": final + timedelta(hours=3),
        "final_complete": True,
    }
    assert SportsDashboard._select_football_panel_kind("auto", first - timedelta(days=14), summary) == "worldcup"
    assert SportsDashboard._select_football_panel_kind("auto", first - timedelta(days=14, seconds=1), summary) == "club"
    assert SportsDashboard._select_football_panel_kind("auto", final + timedelta(hours=27), summary) == "worldcup"
    assert SportsDashboard._select_football_panel_kind("auto", final + timedelta(hours=27, seconds=1), summary) == "club"
    assert SportsDashboard._select_football_panel_kind("auto", first, None) == "club"
```

Import `CLUB_FOOTBALL_LEAGUES` from `plugins.sports_dashboard.club_football` in the test module.

- [ ] **Step 2: Run the new tests and confirm the module is missing**

Run:

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "club_football_registry or club_football_enabled_leagues or football_panel_manual_modes or football_panel_auto_mode" -q
```

Expected: collection fails with `ModuleNotFoundError` or the new assertions fail because the interfaces do not exist.

- [ ] **Step 3: Implement the registry and pure selectors**

Create `club_football.py` with this initial complete surface:

```python
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping

from .common import *

SportsDashboard = None

CLUB_FOOTBALL_LIVE_STATE_VERSION = "sports-dashboard-club-football-live-v1"
CLUB_FOOTBALL_WORLD_CUP_LEAD = timedelta(days=14)
CLUB_FOOTBALL_WORLD_CUP_TAIL = timedelta(hours=24)
CLUB_FOOTBALL_LEAGUES = {
    "PL": {"name": "英超", "short_name": "英超", "espn_slug": "eng.1"},
    "PD": {"name": "西甲", "short_name": "西甲", "espn_slug": "esp.1"},
    "BL1": {"name": "德甲", "short_name": "德甲", "espn_slug": "ger.1"},
    "SA": {"name": "意甲", "short_name": "意甲", "espn_slug": "ita.1"},
    "FL1": {"name": "法甲", "short_name": "法甲", "espn_slug": "fra.1"},
}


class ClubFootballMixin:
    @staticmethod
    def _club_football_enabled_leagues(settings):
        raw = str((settings or {}).get("clubFootballEnabledLeagues") or ",".join(CLUB_FOOTBALL_LEAGUES))
        requested = {item.strip().upper() for item in raw.split(",") if item.strip()}
        enabled = tuple(code for code in CLUB_FOOTBALL_LEAGUES if code in requested)
        return enabled or tuple(CLUB_FOOTBALL_LEAGUES)

    @staticmethod
    def _football_panel_mode(settings):
        mode = str((settings or {}).get("footballPanelMode") or "auto").strip().lower()
        return mode if mode in {"auto", "worldcup", "club"} else "auto"

    @staticmethod
    def _select_football_panel_kind(mode, now, worldcup_summary):
        if mode in {"worldcup", "club"}:
            return mode
        if not isinstance(now, datetime) or not isinstance(worldcup_summary, Mapping):
            return "club"
        first = worldcup_summary.get("first_start")
        final = worldcup_summary.get("final_start")
        if not isinstance(first, datetime) or not isinstance(final, datetime):
            return "club"
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        if final.tzinfo is None:
            final = final.replace(tzinfo=timezone.utc)
        final_end = worldcup_summary.get("final_end")
        if not isinstance(final_end, datetime):
            final_end = final + timedelta(hours=3)
        if final_end.tzinfo is None:
            final_end = final_end.replace(tzinfo=timezone.utc)
        return "worldcup" if first - CLUB_FOOTBALL_WORLD_CUP_LEAD <= now <= final_end + CLUB_FOOTBALL_WORLD_CUP_TAIL else "club"
```

- [ ] **Step 4: Wire both new mixin modules into `SportsDashboard` without changing rendering yet**

Add imports for `club_football` and, temporarily, only `ClubFootballMixin`. Put `ClubFootballMixin` after `WorldCupRenderMixin` in the class bases and add `_club_football_module` to `_SPLIT_MODULES`. This preserves the test monkeypatch relay used by split modules.

- [ ] **Step 5: Run focused tests and commit**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "club_football_registry or club_football_enabled_leagues or football_panel_manual_modes or football_panel_auto_mode" -q
git diff --check -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git add inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git commit -m "feat: add club football panel selection"
```

Expected: focused tests pass and the commit contains only Task 1 files.

---

### Task 2: Normalize and merge ESPN and football-data.org events

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`

**Interfaces:**
- Produces `_parse_club_espn_events(league_code, payload, timezone_info) -> list[dict]`.
- Produces `_parse_club_football_data_events(league_code, matches, timezone_info) -> list[dict]`.
- Produces `_merge_club_football_events(schedule_events, score_events, tolerance=timedelta(minutes=15)) -> list[dict]`.
- Every event contains the schema from design section 3.3, with aware `start_utc`, stable `event_key`, and explicit `provider_status_confirmed` / `inferred_live_window` booleans.

- [ ] **Step 1: Add minimal provider fixtures and failing parser tests**

Add `_sample_club_espn_payload()` and `_sample_club_football_data_matches()` fixtures containing one Arsenal–Chelsea event with different provider names (`Arsenal` versus `Arsenal FC`) and transparent logo URLs. Add tests asserting:

```python
def test_parse_club_espn_event_preserves_confirmed_live_state_and_logos():
    events = SportsDashboard._parse_club_espn_events("PL", _sample_club_espn_payload(), timezone.utc)
    event = events[0]
    assert event["status"] == "LIVE"
    assert event["provider_status_confirmed"] is True
    assert event["inferred_live_window"] is False
    assert event["home_score"] == 2
    assert event["away_score"] == 1
    assert event["display_clock"] == "67'"
    assert event["league_logo_url"].endswith(".png")
    assert event["home_logo_url"].endswith(".png")


def test_merge_club_events_matches_aliases_without_swapping_home_and_away():
    schedule = SportsDashboard._parse_club_football_data_events(
        "PL", _sample_club_football_data_matches(), timezone.utc
    )
    scores = SportsDashboard._parse_club_espn_events("PL", _sample_club_espn_payload(), timezone.utc)
    merged = SportsDashboard._merge_club_football_events(schedule, scores)
    assert len(merged) == 1
    assert merged[0]["home_name"] == "Arsenal FC"
    assert merged[0]["away_name"] == "Chelsea FC"
    assert (merged[0]["home_score"], merged[0]["away_score"]) == (2, 1)
    assert merged[0]["provider"] == "football-data.org+ESPN"
```

Also add a reversed-provider-order fixture and assert the merge explicitly flips score and logo fields while retaining the schedule source's home/away order.

- [ ] **Step 2: Run the parser tests and verify missing methods**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "parse_club_espn or parse_club_football_data or merge_club_events" -q
```

Expected: failures identify the missing parser and merge methods.

- [ ] **Step 3: Implement normalization, stable keys, and both parsers**

Use the existing `_normalize_country_alias` normalization base, strip common club suffixes only for matching, and keep the display name unchanged:

```python
CLUB_NAME_SUFFIXES = ("footballclub", "fc", "cf", "calcio")

@staticmethod
def _club_team_match_key(name):
    key = _normalize_country_alias(name)
    for suffix in CLUB_NAME_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            key = key[:-len(suffix)]
    return key

@staticmethod
def _club_event_key(league_code, start_utc, home_name, away_name):
    start_key = start_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M")
    home = SportsDashboard._club_team_match_key(home_name)
    away = SportsDashboard._club_team_match_key(away_name)
    return f"{league_code}:{start_key}:{home}:{away}"
```

Map ESPN completed states to `FINAL`, active states to `LIVE`, and all other states to `SCHEDULED`. Set `provider_status_confirmed=True` only when the ESPN status object explicitly reports active play. football-data.org may confirm `FINISHED` but must not infer `LIVE` from kickoff time.

- [ ] **Step 4: Implement same-league, time-bounded merge including reverse order**

The merge candidate condition must be explicit:

```python
same_league = schedule["league_code"] == score["league_code"]
close_start = abs(schedule["start_utc"] - score["start_utc"]) <= tolerance
same_order = (
    SportsDashboard._club_team_match_key(schedule["home_name"]) == SportsDashboard._club_team_match_key(score["home_name"])
    and SportsDashboard._club_team_match_key(schedule["away_name"]) == SportsDashboard._club_team_match_key(score["away_name"])
)
reverse_order = (
    SportsDashboard._club_team_match_key(schedule["home_name"]) == SportsDashboard._club_team_match_key(score["away_name"])
    and SportsDashboard._club_team_match_key(schedule["away_name"]) == SportsDashboard._club_team_match_key(score["home_name"])
)
```

For `reverse_order`, copy `away_score`/`away_logo_url` to the merged home side and `home_score`/`home_logo_url` to the merged away side. Unmatched events remain available and are de-duplicated by `event_key`.

- [ ] **Step 5: Run tests and commit**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "club_espn or club_football_data or merge_club or club_event_key" -q
git diff --check -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git add inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git commit -m "feat: normalize club football data"
```

Expected: parser, alias, forward merge, and reversed merge tests all pass.

---

### Task 3: Add per-league caches, adaptive refresh windows, standings cache, and last-good fallback

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py:65-66`
- Test: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`

**Interfaces:**
- Produces `_load_club_football_data(settings, device_config, timezone_info, now) -> tuple[dict[str, list[dict]], dict[str, dict], str, str | None]`.
- Produces `_club_espn_cache_seconds(events, now) -> int`, returning 60 in confirmed-live/pregame windows, 900 on a matchday, and 21600 otherwise.
- Produces `_club_football_cache_path(provider, league_code)` and `_club_football_standings_cache_path(league_code)` below the SportsDashboard cache directory.
- Returns the last valid provider payload on request, decode, or cache-corruption failure and exposes source state as `LIVE`, `CACHE`, `PARTIAL`, or `STALE`.
- Reuses the existing football-data request counter with a 60-call default so one initial five-league matches-plus-standings fill is possible; an explicitly configured lower limit remains authoritative.
- Adds a separate ESPN club-football request counter with a 720-call default and the existing 60-second live TTL, preventing unrestricted polling while keeping World Cup counter files compatible.

- [ ] **Step 1: Write failing cache-policy and failure-isolation tests**

Cover these cases with monkeypatched provider methods and a temporary `INKYPI_CACHE_DIR`:

```python
def test_club_espn_cache_seconds_is_sixty_only_for_relevant_live_or_pregame_event():
    now = datetime(2026, 8, 15, 18, tzinfo=timezone.utc)
    live = {"start_utc": now - timedelta(minutes=20), "status": "LIVE", "provider_status_confirmed": True}
    pregame = {"start_utc": now + timedelta(minutes=10), "status": "SCHEDULED", "provider_status_confirmed": False}
    later_today = {"start_utc": now + timedelta(hours=4), "status": "SCHEDULED", "provider_status_confirmed": False}
    later_week = {"start_utc": now + timedelta(days=3), "status": "SCHEDULED", "provider_status_confirmed": False}
    assert SportsDashboard._club_espn_cache_seconds([live], now) == 60
    assert SportsDashboard._club_espn_cache_seconds([pregame], now) == 60
    assert SportsDashboard._club_espn_cache_seconds([later_today], now) == 900
    assert SportsDashboard._club_espn_cache_seconds([later_week], now) == 21600


def test_club_loader_keeps_football_data_when_espn_fails(monkeypatch):
    monkeypatch.setattr(SportsDashboard, "_fetch_club_football_data_payload", lambda *args: _sample_club_football_data_payload())
    monkeypatch.setattr(SportsDashboard, "_fetch_club_espn_payload", lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
    by_league, standings, source_state, fetched_at = plugin._load_club_football_data(settings, device_config, timezone.utc, now)
    assert by_league["PL"]
    assert source_state == "CLUB PARTIAL"
    assert all(event["provider_status_confirmed"] is False for event in by_league["PL"])
```

Add parallel tests for football-data.org failure with ESPN success, both failures with last-good cache, corrupt current cache with valid `.last_good` cache, disabled league causing zero calls, and no cache producing empty per-league collections rather than an exception.

Add a request-budget test asserting a blank configuration allows the first ten football-data calls needed for five matches and five standings payloads, while `footballDataDailyLimit="4"` still stops after four calls.

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "club_espn_cache_seconds or club_loader or club_cache" -q
```

Expected: the new loader and cache-policy assertions fail.

- [ ] **Step 3: Implement fixed URL builders and request methods**

Construct URLs only from the registry:

```python
CLUB_ESPN_SCOREBOARD_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

@staticmethod
def _club_espn_scoreboard_url(league_code):
    slug = CLUB_FOOTBALL_LEAGUES[league_code]["espn_slug"]
    return f"{CLUB_ESPN_SCOREBOARD_BASE_URL}/{slug}/scoreboard"

def _fetch_club_espn_payload(self, league_code):
    response = get_http_session().get(
        self._club_espn_scoreboard_url(league_code),
        headers={"User-Agent": "InkyPi/1.0"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()
```

Use the existing authenticated `_football_data_get_json` with `/competitions/{code}/matches` and `/competitions/{code}/standings`; the `code` comes from the registry key, never raw settings. Raise `DEFAULT_FOOTBALL_DATA_DAILY_LIMIT` from 8 to 60 so a blank configuration can service all five leagues, but preserve any explicit `footballDataDailyLimit` value. Reuse the shared football-data daily counter so World Cup and club requests cannot independently exceed the configured allowance.

Add `_club_espn_calls_left`, `_record_club_espn_call`, and `_club_espn_state_path`. Check the budget before each ESPN fetch and record only completed HTTP attempts. Use the same date-reset pattern as `_worldcup_scoreboard_calls_left` without writing to the World Cup state file.

- [ ] **Step 4: Implement provider caches and last-good promotion**

Write a cache only after its payload parses successfully. Before replacing a valid cache, copy its JSON content to the provider/league `.last_good.json` path through `_write_json_file`. On read corruption, ignore the current file and load last-good. Store `version`, `league_code`, `fetched_at`, provider payload, and encoded normalized events.

The standings cache follows the same rules, with 21600 seconds normally and 3600 seconds when that league has a match on the local date. It is returned to the data layer but not passed to the renderer in this phase.

- [ ] **Step 5: Implement the five-league orchestrator and source-state aggregation**

Iterate only `_club_football_enabled_leagues(settings)`. For each league, independently load both sources, parse, merge, and preserve whichever succeeds. Aggregate source state as:

```python
if fresh_provider_count == provider_attempt_count:
    source_state = "CLUB LIVE"
elif usable_event_count and fresh_provider_count:
    source_state = "CLUB PARTIAL"
elif usable_event_count:
    source_state = "CLUB STALE"
else:
    source_state = "CLUB UNAVAILABLE"
```

- [ ] **Step 6: Run cache/failure tests and commit**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "club_espn_cache_seconds or club_loader or club_cache or club_standings or club_request_budget" -q
git diff --check -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git add inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git commit -m "feat: cache club football sources"
```

Expected: all adaptive TTL, partial-source, stale-cache, corrupt-cache, no-data, and disabled-league tests pass.

---

### Task 4: Add event priority, fair league rotation, and dedicated live-state output

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`

**Interfaces:**
- Produces `_select_club_league_event(events, now) -> dict | None`.
- Produces `_select_club_football_events(by_league, enabled_leagues, now, rotation_seed) -> dict` with `focus`, `rail`, and `priority`.
- Produces `_club_football_rotation_seed(now) -> int` and persisted `_club_football_rotation_state_path()`.
- Produces `_write_club_football_live_state(selected, now, source_state, fetched_at)` and `_club_football_live_state_path()`.

- [ ] **Step 1: Write failing priority, fairness, and data-honesty tests**

Add tests proving:

- Confirmed `LIVE` beats any upcoming or recent event.
- Nearest upcoming beats latest completed when no event is confirmed live.
- Kickoff time alone sets `inferred_live_window=True` but does not change `status` to `LIVE`.
- Five equal-priority leagues all become focus within five consecutive seeds.
- The persisted previous league is not immediately repeated when another equal-priority candidate exists.
- Every enabled league retains one rail entry; an empty league yields a no-schedule row.

Use this assertion for fairness:

```python
focus_codes = {
    SportsDashboard._select_club_football_events(by_league, tuple(CLUB_FOOTBALL_LEAGUES), now, seed)["focus"]["league_code"]
    for seed in range(5)
}
assert focus_codes == set(CLUB_FOOTBALL_LEAGUES)
```

- [ ] **Step 2: Write failing live-window extension tests**

Create a selected structure with a confirmed live match and another kickoff inside the first match's refresh tail. Assert `live_until` extends through the second match's default two-hour window. Assert the JSON state includes version, `has_live`, `active_leagues`, selected event summary, provider, freshness, and `updated_at`.

- [ ] **Step 3: Run focused tests and confirm missing behavior**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "select_club_football or club_football_rotation or club_football_live_state or inferred_live_window" -q
```

Expected: selection and state tests fail.

- [ ] **Step 4: Implement tiered selection and rotation**

Classify candidates without mutating provider status:

```python
def priority(event):
    if event.get("status") == "LIVE" and event.get("provider_status_confirmed"):
        return 0
    if event.get("status") == "SCHEDULED" and event.get("start_utc") >= now:
        return 1
    if event.get("status") == "FINAL":
        return 2
    return 3
```

Choose one rail event per league, find the best tier among rail candidates, order equal-tier leagues by registry order, then index by `rotation_seed % len(candidates)`. Persist only the last focus league and timestamp; corrupt rotation state falls back to deterministic seed behavior.

- [ ] **Step 5: Implement pregame/live bridge and atomic state write**

Use a 15-minute pregame window and a two-hour default match window. Start the state window for either a confirmed live event or a scheduled event whose kickoff is within 15 minutes. Extend it through any subsequent kickoff whose pregame boundary begins before the current `live_until`. Set `has_live` to indicate fast-refresh activity, while the selected event's `provider_status_confirmed` remains the sole source of the rendered `LIVE` label.

- [ ] **Step 6: Run tests and commit**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "club_football and (select or rotation or live_state or inferred)" -q
git diff --check -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git add inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git commit -m "feat: select and track club football matches"
```

Expected: priority, fairness, non-fake-live, state schema, expiration, and consecutive-match tests pass.

---

### Task 5: Build the exact 536×240 renderer with safe logo fallback and mirrored rail names

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football_render.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py:7-38,1763-1775`
- Test: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`

**Interfaces:**
- Produces `ClubFootballRenderMixin._render_club_football_panel(dimensions, selected, source_state, fetched_at, now) -> PIL.Image.Image`.
- Produces `_draw_club_logo_contained(image, logo_url, box, fallback_text, cache_dir) -> tuple[int, int]`.
- Produces `_club_rail_text_anchors(box) -> dict` so alignment can be tested without OCR.
- Uses the existing safe disk logo loader and returns text initials when a single logo cannot be decoded.

- [ ] **Step 1: Write failing renderer geometry and contain tests**

Add tests asserting:

```python
def test_club_football_panel_is_exact_final_slot_size(monkeypatch):
    panel = plugin._render_club_football_panel(
        (536, 240), _sample_club_selection(), "CLUB LIVE", "2026-08-15T18:00:00+00:00", now
    )
    assert panel.mode == "RGB"
    assert panel.size == (536, 240)


def test_club_rail_names_are_mirrored_around_fixed_score_column():
    anchors = SportsDashboard._club_rail_text_anchors((302, 33, 531, 69))
    assert anchors["home_align"] == "left"
    assert anchors["away_align"] == "right"
    assert anchors["home_x"] < anchors["score_left"] < anchors["score_right"] < anchors["away_x"]
```

Monkeypatch `_load_team_logo` to return a 100×50 RGBA image and assert the pasted result inside a 28×28 box is 28×14, proving `ImageOps.contain` rather than stretch/crop. Add a failure test where only one logo returns `None` and the panel still renders.

- [ ] **Step 2: Add an overflow instrumentation test**

Monkeypatch `_fit_text`, `_draw_left_aligned`, and `_draw_right_aligned` to record bounding boxes for deliberately long English and Chinese team names. Assert every recorded box stays within `(0, 0, 535, 239)` and the right-team text ends at its right anchor rather than starting from it.

- [ ] **Step 3: Run renderer tests and verify failure**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "club_football_panel or club_rail or club_logo_contained" -q
```

Expected: the new render module and methods are missing.

- [ ] **Step 4: Implement the renderer at final pixel dimensions**

Use fixed regions for the 536×240 target:

```python
FOCUS_BOX = (4, 4, 294, 235)
RAIL_BOX = (300, 4, 531, 235)
RAIL_HEADER_HEIGHT = 27
RAIL_ROW_HEIGHT = 40
RAIL_SCORE_BOX = (402, 0, 429, 0)
```

Inside each rail row reserve, in order: league logo, home logo, home-name field, centered score/time, away-name field, away logo. Call `_draw_left_aligned` for the home field and `_draw_right_aligned` for the away field. Do not center both team names and do not derive the away anchor from measured text width.

Render source freshness as one of `LIVE DATA`, `PARTIAL DATA`, `CACHED`, or `UNAVAILABLE`; render the red `LIVE` badge only when `focus["provider_status_confirmed"]` is true.

- [ ] **Step 5: Implement safe logo resolution without new image decoding code**

Use:

```python
logo = self._load_team_logo(logo_url, min(box_width, box_height), cache_dir=cache_dir)
if logo is not None:
    logo = ImageOps.contain(logo, (box_width, box_height), Image.LANCZOS)
```

Use `_sports_dashboard_cache_dir() / "team_logos"` as the cache directory. If loading fails, draw a dark bold two- or three-character abbreviation inside the same box; do not raise from the panel renderer.

- [ ] **Step 6: Wire `ClubFootballRenderMixin` into the class and split-module relay**

Import `_club_football_render_module`, import `ClubFootballRenderMixin`, place it after `ClubFootballMixin` in the class bases, and add the module to `_SPLIT_MODULES`.

- [ ] **Step 7: Run visual-unit tests and commit**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "club_football_panel or club_rail or club_logo_contained" -q
git diff --check -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football_render.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git add inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football_render.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git commit -m "feat: render five league football panel"
```

Expected: panel size, coordinate bounds, mirrored alignment, contain scaling, missing-logo isolation, and no-data rendering tests pass.

---

### Task 6: Route the top-left panel, expose settings, and connect the live-state refresh hook

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py:2180-2250`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py:85-150`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/settings.html:1-25`
- Test: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_refresh_task.py`

**Interfaces:**
- Produces `_worldcup_schedule_summary(settings, device_config, timezone_info, now) -> dict | None`, reading fresh World Cup events first and compatible last-good cache second without forcing an extra fetch solely for mode choice. It returns the first kickoff plus the final kickoff, estimated/actual end, completion flag, and source state.
- Produces `_render_selected_football_panel(...) -> tuple[Image.Image, SourceProvenance, str]`.
- Adds `club_football` to `_active_live_refresh_sources`, with settings `clubFootballLiveRefreshEnabled` and `clubFootballLiveRefreshIntervalSeconds`.

- [ ] **Step 1: Write failing composition tests**

Monkeypatch mode selection and panel methods to return solid marker images. Assert:

- `club` mode calls only the club loader/renderer for the top-left panel.
- `worldcup` mode preserves the current World Cup call order and screenshot fallback.
- `auto` selects each branch at the documented boundaries.
- On an 800×480 device with the approved top height, the club panel paste box is exactly `(0, 0, 536, 240)`.
- A pixel mask outside `(0, 0, 536, 240)` is identical between a World Cup marker render and club marker render when NBA/LPL methods are fixed.
- A club data exception renders the structured unavailable panel rather than falling back to a misleading World Cup page outside its active window.
- `settings.html` contains one scalar hidden `clubFootballEnabledLeagues` field, five visual league checkboxes, and synchronization code that serializes them in registry order.
- The live-refresh checkbox pairs hidden `false` with checked `true`, so disabling it survives `parse_form` rather than disappearing from `FormData`.

- [ ] **Step 2: Write failing settings and refresh-hook tests**

Extend `test_live_refresh_state_reads_active_source_files` with:

```python
(
    "club_football_live_state.json",
    "sports-dashboard-club-football-live-v1",
    "clubFootballLiveRefreshIntervalSeconds",
),
```

Add assertions that the source is ignored after `live_until`, ignored when `clubFootballLiveRefreshEnabled=False`, and returns the clamped 60-second interval. In `test_refresh_task.py`, add one named regression proving a real `SportsDashboard.get_live_refresh_state()` result with an active club state enters the existing high-priority `DISPLAY_CACHE` path while the existing low-memory guard remains active.

- [ ] **Step 3: Run the new integration tests and verify failure**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py tests/test_refresh_task.py -k "football_panel_route or club_football_live or sports_dashboard_live" -q
```

Expected: routing and refresh-hook tests fail before integration.

- [ ] **Step 4: Extract the current World Cup branch into a narrow helper**

Move lines currently responsible for `_try_worldcup_scoreboard_panel` through `_prepare_worldcup_panel` into `_render_worldcup_slot(...)` without changing their internal order or fallback rules. The helper returns the prepared image, provenance, source label, and content box so screenshot local-time overlay remains unchanged.

- [ ] **Step 5: Add club routing while preserving exact slot geometry**

In `_generate_image_with_active_colors`, calculate `left_width` and the top-slot height once. For the approved 800×480 configuration, assert through tests that they resolve to 536 and 240. Select the kind before loading panel data:

```python
mode = self._football_panel_mode(settings)
worldcup_summary = self._worldcup_schedule_summary(settings, device_config, timezone_info, now)
panel_kind = self._select_football_panel_kind(mode, now, worldcup_summary)
if panel_kind == "club":
    left, left_provenance, left_source = self._render_club_football_slot(
        settings, device_config, (left_width, worldcup_height), timezone_info, now
    )
else:
    left, left_provenance, left_source, worldcup_content_box = self._render_worldcup_slot(
        settings, device_config, (left_width, worldcup_height), timezone_info, visible_worldcup_matches, now
    )
```

Do not run World Cup screenshot overlay for the club branch. Keep separator, NBA, offseason hub, right-sidebar, and final provenance attestation lines untouched.

- [ ] **Step 6: Add settings fields**

Add a select for `footballPanelMode` with `auto`, `worldcup`, and `club`. Store leagues in one hidden scalar:

```html
<input type="hidden" id="clubFootballEnabledLeagues" name="clubFootballEnabledLeagues" value="PL,PD,BL1,SA,FL1">
```

Render five visual checkboxes with `data-club-football-league="PL"` through `FL1` and no `name` attribute. On change, write the checked codes to the hidden input in registry order. During `DOMContentLoaded`, initialize the visual boxes from `pluginSettings.clubFootballEnabledLeagues` and then synchronize the hidden value. This matches `parse_form`'s scalar contract and avoids losing repeated checkbox values.

Pair the checked-by-default `clubFootballLiveRefreshEnabled` checkbox with a same-name hidden `false` field so the existing `parse_form` true/false normalization persists both states. Add `clubFootballLiveRefreshIntervalSeconds` with `min="60"`, `max="900"`, and `placeholder="60"`. Change the existing `footballDataDailyLimit` placeholder from `8` to `60`, reuse the existing `footballDataKey` input, and do not add another secret field.

- [ ] **Step 7: Connect state path, enable flag, and interval**

In `_active_live_refresh_sources`, test `_club_football_live_state_path()` against `CLUB_FOOTBALL_LIVE_STATE_VERSION`. Add:

```python
if source == "club_football":
    return self._bool_setting(settings, "clubFootballLiveRefreshEnabled", True)
```

and:

```python
if source == "club_football":
    return self._int_setting(settings, "clubFootballLiveRefreshIntervalSeconds", 60, 60, 900)
```

No `refresh_task.py` production change is expected because it already consumes the generic plugin hook; if the integration test exposes a real contract gap, make the smallest change in `src/refresh_task.py` and add that path to this task's commit.

- [ ] **Step 8: Run integration and regression tests, then commit**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py tests/test_refresh_task.py -k "club_football or football_panel or live_refresh_state or sports_live" -q
git diff --check -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/settings.html inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py inkypi-weather/package/InkyPi/tests/test_refresh_task.py
git add inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/settings.html inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py inkypi-weather/package/InkyPi/tests/test_refresh_task.py
git commit -m "feat: integrate club football live panel"
```

Expected: routing, unchanged-neighbor mask, settings, state expiry, disabled refresh, interval clamp, and scheduler priority tests pass.

---

### Task 7: Add opt-in source smoke checks and produce the final full-size PNG evidence

**Files:**
- Create: `tools/check_club_football_sources.py`
- Create: `tools/render_club_football_preview.py`
- Modify: `docs/superpowers/specs/2026-07-16-club-football-live-tracking-design.md:3-5`
- Test: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`
- Output: `output/playwright/club-football-final.png` (local review artifact; do not commit if ignored)

**Interfaces:**
- `python tools/check_club_football_sources.py` exits 0 only when all five ESPN endpoints return parseable scoreboards and sampled league/team logos decode; football-data.org checks report `SKIP: no key` when no supported key environment variable is present.
- `python tools/render_club_football_preview.py --output output/playwright/club-football-final.png` renders the real 800×480 SportsDashboard with `footballPanelMode=club` and no HTML review layer.

- [ ] **Step 1: Write a unit test for smoke-script result accounting**

Keep the script import-safe and expose `summarize_checks(results) -> tuple[int, list[str]]`. Test that any failed ESPN or logo check makes the exit code nonzero, while a missing football-data key produces a skip line and does not fail otherwise healthy ESPN checks.

- [ ] **Step 2: Implement the opt-in source checker**

For each registry item, call the same fixed URL builder as production, parse JSON, verify the response league slug, choose one league logo and one team logo URL, fetch with explicit timeout and byte limit, and decode with the existing safe-image limits. Redact secret-bearing error text. Never print API keys or full authenticated headers.

- [ ] **Step 3: Implement the direct PNG preview script**

The script must instantiate the real plugin, set an isolated cache directory under `.tmp`, pass an 800×480 device configuration and `footballPanelMode=club`, save the exact returned image, re-open it, and fail unless `size == (800, 480)`. It must not start a web server or emit an HTML file.

- [ ] **Step 4: Run the local network smoke check**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python tools\check_club_football_sources.py
```

Expected: five ESPN JSON checks and sampled logo decode checks pass. football-data.org either passes for all enabled leagues or reports an explicit keyless skip.

- [ ] **Step 5: Generate and inspect the final PNG**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python tools\render_club_football_preview.py --output output\playwright\club-football-final.png
Get-Item output\playwright\club-football-final.png | Select-Object FullName,Length
```

Open the PNG with the local image viewer and verify:

- Canvas is 800×480.
- Club slot is exactly 536×240.
- All five rail rows are present in registry order.
- Away names end at the right anchor and visually mirror home names.
- League and team logos are transparent, contained, and not stretched.
- Right LPL and lower NBA regions remain at their current coordinates.
- Source freshness and confirmed-live state are honest.

- [ ] **Step 6: Run focused, full plugin, and scheduler suites**

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -q
tools\run_inkypi_tests.ps1 tests/test_refresh_task.py -q
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py tests/test_refresh_task.py -q
git diff --check
```

Expected: all commands exit 0. Record exact pass counts in the final handoff.

- [ ] **Step 7: Mark the design implemented and commit verification tooling**

Change the spec status to `已实现并通过本机验证` without changing approved design decisions, then run:

```powershell
git diff --check -- tools/check_club_football_sources.py tools/render_club_football_preview.py docs/superpowers/specs/2026-07-16-club-football-live-tracking-design.md inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git add tools/check_club_football_sources.py tools/render_club_football_preview.py docs/superpowers/specs/2026-07-16-club-football-live-tracking-design.md inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git commit -m "test: verify club football live tracking"
```

Expected: the commit contains verification tooling, the smoke-script unit test, and the status-only spec update. Device deployment remains out of scope.

---

## Final Review Gate

- [ ] Compare every implemented behavior with design sections 2 through 10.
- [ ] Run `$forbidden = @('TO' + 'DO', 'FIX' + 'ME', 'T' + 'BD', '<place' + 'holder>'); Select-String -Path docs\superpowers\plans\2026-07-16-club-football-live-tracking.md -Pattern $forbidden` and require no matches.
- [ ] Run `git status --short` and explain any pre-existing `.learnings/LEARNINGS.md` modification separately.
- [ ] Confirm no secret value, cache JSON, downloaded logo, or local PNG is staged.
- [ ] Confirm no production change was made to unrelated SportsDashboard panels.
- [ ] Present `output/playwright/club-football-final.png` directly to the user for final visual approval.
