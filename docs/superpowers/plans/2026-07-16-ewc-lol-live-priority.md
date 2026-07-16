# EWC LoL Live Priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep League of Legends on the EWC panel whenever LoL and another game are simultaneously live, without changing any other EWC rotation path.

**Architecture:** Narrow only the live candidate list passed into the existing EWC game-group selector. Preserve the complete live, upcoming, and recent collections so downstream sidebar data and live-state behavior remain intact.

**Tech Stack:** Python 3, `datetime`/`zoneinfo`, pytest, existing `SportsDashboard` static selection helpers.

## Global Constraints

- The priority applies only when more than one EWC game group is live.
- Live LoL must not be displaced by another simultaneously live game.
- Multiple live LoL matches must continue rotating within the LoL group.
- Upcoming/recent selection and non-LoL multi-game rotation must remain unchanged.
- `all_live_matches`, `all_upcoming_matches`, and `all_recent_matches` must retain the complete source collections.
- Do not change providers, refresh timing, settings, or persisted data formats.

---

### Task 1: Add a LoL-only live candidate gate

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/esports.py:1477-1486,1608-1623`
- Test: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py:2034-2270`

**Interfaces:**
- Consumes: `SportsDashboard._ewc_match_group_key(match) -> str` and `SportsDashboard._ewc_match_group_for_display(candidate_matches, live_matches, upcoming_matches, recent_matches, rotation_bucket) -> dict | None`.
- Produces: `SportsDashboard._is_ewc_lol_match(match) -> bool`; `_select_ewc_events(...)` continues returning the existing dictionary schema.

- [ ] **Step 1: Write failing LoL-priority tests**

Add these tests beside the existing EWC live-rotation tests:

```python
def test_select_ewc_events_prioritizes_lol_when_other_game_is_also_live():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 7, 17, 12, 0, tzinfo=la)

    def live_match(slug, game, event_id, team_a):
        return {
            "kind": "match",
            "event_id": event_id,
            "match_id": event_id,
            "slug": slug,
            "game": game,
            "start": now - timedelta(minutes=20),
            "end": now + timedelta(hours=2),
            "status": "LIVE",
            "stage": "Group Stage",
            "team_a": team_a,
            "team_b": "Opponent",
        }

    lol = live_match("league-of-legends", "League of Legends", "lol-live", "T1")
    dota = live_match("dota2", "Dota 2", "dota-live", "Liquid")

    for seed in range(6):
        selected = SportsDashboard._select_ewc_events([dota, lol], now, 21, rotation_seed=seed)
        assert selected["selected_match_group"]["slug"] == "league-of-legends"
        assert selected["main_match"]["event_id"] == "lol-live"
        assert [item["event_id"] for item in selected["live_matches"]] == ["lol-live"]
        assert {item["event_id"] for item in selected["all_live_matches"]} == {"lol-live", "dota-live"}


def test_select_ewc_events_rotates_multiple_lol_matches_while_other_game_is_live():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 7, 17, 12, 0, tzinfo=la)

    def live_match(slug, game, event_id, team_a):
        return {
            "kind": "match",
            "event_id": event_id,
            "match_id": event_id,
            "slug": slug,
            "game": game,
            "start": now - timedelta(minutes=20),
            "end": now + timedelta(hours=2),
            "status": "LIVE",
            "stage": "Group Stage",
            "team_a": team_a,
            "team_b": "Opponent",
        }

    lol_first = live_match("league-of-legends", "League of Legends", "lol-live-1", "T1")
    lol_second = live_match("league-of-legends", "League of Legends", "lol-live-2", "G2")
    dota = live_match("dota2", "Dota 2", "dota-live", "Liquid")

    first = SportsDashboard._select_ewc_events([dota, lol_first, lol_second], now, 21, rotation_seed=0)
    second = SportsDashboard._select_ewc_events([dota, lol_first, lol_second], now, 21, rotation_seed=1)

    assert first["selected_match_group"]["slug"] == "league-of-legends"
    assert second["selected_match_group"]["slug"] == "league-of-legends"
    assert {first["main_match"]["event_id"], second["main_match"]["event_id"]} == {"lol-live-1", "lol-live-2"}
    assert {item["event_id"] for item in first["all_live_matches"]} == {"lol-live-1", "lol-live-2", "dota-live"}
```

- [ ] **Step 2: Run the new tests and verify the current rotation fails**

Run:

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "prioritizes_lol_when_other_game_is_also_live or rotates_multiple_lol_matches_while_other_game_is_live" -q
```

Expected: at least one assertion fails because a rotation seed selects `dota2`.

- [ ] **Step 3: Implement the narrow live-candidate filter**

Add the helper beside `_ewc_match_group_key`:

```python
@staticmethod
def _is_ewc_lol_match(match):
    match = match or {}
    slug = str(match.get("slug") or "").strip().lower()
    game = re.sub(r"[^a-z0-9]+", "-", str(match.get("game") or "").strip().lower()).strip("-")
    return slug in {"league-of-legends", "lol"} or game in {"league-of-legends", "lol"}
```

Replace only the live branch's candidate argument:

```python
if live_matches:
    live_candidates = live_matches
    live_group_keys = {SportsDashboard._ewc_match_group_key(match) for match in live_matches}
    live_group_keys.discard("")
    if len(live_group_keys) > 1:
        lol_live_matches = [match for match in live_matches if SportsDashboard._is_ewc_lol_match(match)]
        if lol_live_matches:
            live_candidates = lol_live_matches
    selected_match_group = SportsDashboard._ewc_match_group_for_display(
        live_candidates,
        live_matches,
        upcoming_matches,
        recent_matches,
        rotation_bucket,
    )
```

- [ ] **Step 4: Run focused and EWC regression tests**

Run:

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "ewc" -q
```

Expected: all EWC tests pass, including the existing non-LoL live rotation, upcoming multi-game rotation, and live non-LoL versus upcoming LoL cases.

- [ ] **Step 5: Prepare the implementation for the root integration gate**

Review only the intended hunks:

```powershell
git diff --check -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/esports.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git diff -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/esports.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
```

Because the shared test file already contains approved uncommitted work, do not stage it from a subagent. The root agent will perform the integrated commit decision after all tests and live verification.
