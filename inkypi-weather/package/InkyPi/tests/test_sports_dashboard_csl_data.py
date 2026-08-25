import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.sports_dashboard.csl import CSLMixin


class _CSLHarness(CSLMixin):
    def __init__(self, cache_dir, session):
        self._cache_dir = cache_dir
        self._session = session

    def _sports_dashboard_cache_dir(self):
        return self._cache_dir

    @staticmethod
    def _read_json_file(path):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _write_json_file(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    @staticmethod
    def _force_refresh_requested(settings):
        return str((settings or {}).get("_forceRefresh") or "").lower() == "true"

    def _csl_http_session(self):
        return self._session


class _ForbiddenSession:
    def get(self, *args, **kwargs):
        raise AssertionError("fresh cache must not access ESPN")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _RecordingSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.payload)


class _RaisingSession:
    def __init__(self):
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        raise ConnectionError("ESPN unavailable")


def _sample_csl_scoreboard():
    return {
        "leagues": [
            {
                "slug": "chn.1",
                "name": "Chinese Super League",
                "logos": [{"href": "https://a.espncdn.com/i/leaguelogos/soccer/500/2350.png"}],
            }
        ],
        "events": [
            {
                "id": "401861999",
                "date": "2026-07-26T11:35:00Z",
                "week": {"number": 21},
                "season": {"year": 2026, "slug": "2026-chinese-super-league"},
                "links": [{"href": "https://www.espn.com/soccer/match/_/gameId/401861999"}],
                "competitions": [
                    {
                        "altGameNote": "Chinese Super League",
                        "odds": [
                            {
                                "provider": {"displayName": "ESPN BET"},
                                "moneyline": {
                                    "home": {"close": {"odds": "+125"}},
                                    "draw": {"close": {"odds": "+220"}},
                                    "away": {"close": {"odds": "-105"}},
                                },
                            }
                        ],
                        "status": {
                            "period": 2,
                            "type": {
                                "name": "STATUS_IN_PROGRESS",
                                "state": "in",
                                "completed": False,
                                "detail": "67'",
                                "shortDetail": "67'",
                            },
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "2",
                                "team": {
                                    "id": "15515",
                                    "displayName": "Shanghai Port",
                                    "shortDisplayName": "Shanghai Port",
                                    "abbreviation": "SIPG",
                                    "logo": "https://a.espncdn.com/i/teamlogos/soccer/500/15515.png",
                                },
                            },
                            {
                                "homeAway": "away",
                                "score": "1",
                                "team": {
                                    "id": "131704",
                                    "displayName": "Chongqing Tonglianglong",
                                    "shortDisplayName": "Chongqing",
                                    "abbreviation": "CHO",
                                    "logo": "",
                                },
                            },
                        ],
                    }
                ],
            }
        ],
    }


def test_csl_scoreboard_window_uses_local_today_minus_seven_through_plus_seven():
    now = datetime(2026, 7, 25, 6, 30, tzinfo=timezone.utc)

    start_date, end_date = CSLMixin._csl_scoreboard_date_range(
        ZoneInfo("America/Los_Angeles"),
        now,
    )

    assert start_date.isoformat() == "2026-07-17"
    assert end_date.isoformat() == "2026-07-31"


def test_csl_espn_adapter_emits_worldcup_contract_with_chinese_names_and_badge_fallback():
    events = CSLMixin._parse_csl_espn_events(
        _sample_csl_scoreboard(),
        ZoneInfo("America/Los_Angeles"),
    )

    assert len(events) == 1
    assert events[0] == {
        "event_id": "401861999",
        "start": datetime(
            2026,
            7,
            26,
            4,
            35,
            tzinfo=ZoneInfo("America/Los_Angeles"),
        ),
        "state": "2H",
        "status": "67'",
        "elapsed": 67,
        "team_a": "上海海港",
        "team_b": "重庆铜梁龙",
        "team_a_tla": "SIPG",
        "team_b_tla": "CHO",
        "team_a_source_name": "Shanghai Port",
        "team_b_source_name": "Chongqing Tonglianglong",
        "team_a_source_aliases": ["Shanghai Port", "SIPG"],
        "team_b_source_aliases": [
            "Chongqing Tonglianglong",
            "Chongqing",
            "CHO",
        ],
        "team_a_flag": "https://a.espncdn.com/i/teamlogos/soccer/500/15515.png",
        "team_b_flag": (
            "https://cdn.sanity.io/images/11hmdf08/production/"
            "c035439e1db9e8dcdf65c1693067f9c0c50f492c-800x800.png"
            "?fm=webp&q=80&w=800"
        ),
        "wins_a": 2,
        "wins_b": 1,
        "block": "中超 · 第21轮",
        "odds": {
            "team_a": "2.25",
            "draw": "3.20",
            "team_b": "1.95",
            "bookmaker": "ESPN BET",
        },
        "score_source": "ESPN",
        "provider": "ESPN",
        "source_url": "https://www.espn.com/soccer/match/_/gameId/401861999",
        "provider_status_confirmed": True,
        "score_confirmed": True,
        "league_logo_url": "https://a.espncdn.com/i/leaguelogos/soccer/500/2350.png",
        "season": "2026",
    }


def test_csl_liaoning_missing_badge_uses_transparent_fallback_and_stable_code():
    team = CSLMixin._csl_team_info(
        {
            "score": "0",
            "team": {
                "id": "131705",
                "displayName": "Liaoning Tieren",
                "shortDisplayName": "Liaoning",
                "abbreviation": "UNSTABLE",
                "logo": "",
            },
        },
        show_score=False,
    )

    assert team["display_name"] == "辽宁铁人"
    assert team["code"] == "LIA"
    assert team["logo_url"] == (
        "https://cdn.sanity.io/images/11hmdf08/production/"
        "50f2cb350ae1447fa1f18814a6435be9a2c880cd-800x800.png"
        "?fm=webp&q=80&w=800"
    )


def test_csl_sections_prioritize_confirmed_live_and_order_upcoming_and_recent():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    live = {
        "event_id": "live",
        "start": now - timedelta(minutes=30),
        "state": "2H",
        "provider_status_confirmed": True,
        "league_logo_url": "https://example.test/csl.png",
        "season": "2026",
    }
    near = {"event_id": "near", "start": now + timedelta(hours=2), "state": "TIMED"}
    far = {"event_id": "far", "start": now + timedelta(days=2), "state": "TIMED"}
    newest = {"event_id": "newest", "start": now - timedelta(days=1), "state": "FT"}
    oldest = {"event_id": "oldest", "start": now - timedelta(days=2), "state": "FT"}

    selected = CSLMixin._select_csl_event_sections(
        [oldest, far, newest, near, live],
        now,
        4,
    )

    assert selected["main"] is live
    assert selected["live"] == [live]
    assert selected["upcoming"] == [near, far]
    assert selected["recent"] == [newest, oldest]
    assert selected["visible_matches"] == 4
    assert selected["presentation"] == {
        "competition": "csl",
        "title": "2026 中超联赛",
        "league_logo_url": "https://example.test/csl.png",
        "team_asset_kind": "logo",
        "empty_schedule_text": "暂无中超赛程",
        "upcoming_empty_text": "暂无后续中超赛程",
        "recent_empty_text": "暂无近期赛果",
        "show_worldcup_banner": False,
        "show_five_leagues_filler": False,
        "upcoming_max_rows": 2,
        "upcoming_row_gap": 0,
        "main_team_logo_scale": 1.4,
        "main_team_name_max_size": 14,
        "main_team_name_min_size": 7,
        "main_team_points_offset": 11,
        "main_team_odds_offset": 6,
    }


def test_csl_sections_fall_back_from_nearest_upcoming_to_latest_result():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    near = {"event_id": "near", "start": now + timedelta(hours=2), "state": "TIMED"}
    far = {"event_id": "far", "start": now + timedelta(days=2), "state": "TIMED"}
    newest = {"event_id": "newest", "start": now - timedelta(days=1), "state": "FT"}
    oldest = {"event_id": "oldest", "start": now - timedelta(days=2), "state": "FT"}

    with_upcoming = CSLMixin._select_csl_event_sections(
        [oldest, far, newest, near],
        now,
    )
    results_only = CSLMixin._select_csl_event_sections(
        [oldest, newest],
        now,
    )

    assert with_upcoming["main"] is near
    assert results_only["main"] is newest


def test_csl_unfinished_events_after_kickoff_stay_pollable_and_never_become_recent():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    timed = {
        "event_id": "timed-lag",
        "start": now - timedelta(minutes=130),
        "state": "TIMED",
    }
    postponed = {
        "event_id": "postponed-lag",
        "start": now - timedelta(minutes=60),
        "state": "POSTPONED",
    }

    selected = CSLMixin._select_csl_event_sections(
        [postponed, timed],
        now,
    )

    assert selected["upcoming"] == [timed, postponed]
    assert selected["recent"] == []
    assert selected["main"] is timed
    assert CSLMixin._csl_live_refresh_until(selected, now) == (
        postponed["start"] + timedelta(hours=3)
    )


def test_csl_stale_day_old_live_state_does_not_outrank_future_fixture():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    stale_live = {
        "event_id": "stale-live",
        "start": now - timedelta(days=1),
        "state": "2H",
        "provider_status_confirmed": True,
    }
    future_fixture = {
        "event_id": "future",
        "start": now + timedelta(hours=2),
        "state": "TIMED",
    }

    selected = CSLMixin._select_csl_event_sections(
        [stale_live, future_fixture],
        now,
        source_state="CSL ESPN STALE",
        fetched_at=(now - timedelta(days=1)).isoformat(),
    )

    assert selected["live"] == []
    assert selected["upcoming"] == [future_fixture]
    assert selected["main"] is future_fixture


def test_csl_schedule_summary_exposes_current_window_priority_and_freshness():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    recent = {"event_id": "recent", "start": now - timedelta(days=1), "state": "FT"}
    live = {
        "event_id": "live",
        "start": now - timedelta(minutes=20),
        "state": "1H",
        "provider_status_confirmed": True,
    }
    upcoming = {
        "event_id": "upcoming",
        "start": now + timedelta(days=1),
        "state": "TIMED",
    }

    summary = CSLMixin._csl_schedule_summary(
        [upcoming, recent, live],
        now,
        source_state="CSL ESPN CACHE",
        fetched_at="2026-07-25T11:55:00+00:00",
    )

    assert summary == {
        "active": True,
        "has_relevant_events": True,
        "has_live": True,
        "main_event_id": "live",
        "first_start": recent["start"],
        "last_start": upcoming["start"],
        "final_end": upcoming["start"] + timedelta(hours=3),
        "next_start": upcoming["start"],
        "latest_result_start": recent["start"],
        "source_state": "CSL ESPN CACHE",
        "fetched_at": "2026-07-25T11:55:00+00:00",
    }


def test_csl_schedule_summary_does_not_activate_auto_route_for_old_season_result():
    now = datetime(2027, 2, 1, 12, 0, tzinfo=timezone.utc)
    season_final = {
        "event_id": "2026-final",
        "start": datetime(2026, 11, 8, 11, 35, tzinfo=timezone.utc),
        "state": "FT",
    }

    summary = CSLMixin._csl_schedule_summary(
        [season_final],
        now,
        source_state="CSL ESPN STALE",
        fetched_at="2026-11-08T15:00:00+00:00",
    )

    assert summary["active"] is False
    assert summary["has_relevant_events"] is False
    assert summary["latest_result_start"] == season_final["start"]
    assert summary["final_end"] == season_final["start"] + timedelta(hours=3)


def test_csl_schedule_summary_ignores_unfinished_event_after_polling_window():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    stale_timed = {
        "event_id": "stale-timed",
        "start": now - timedelta(hours=4),
        "state": "TIMED",
    }

    summary = CSLMixin._csl_schedule_summary([stale_timed], now)

    assert summary["active"] is False
    assert summary["has_relevant_events"] is False
    assert summary["next_start"] is None
    assert summary["latest_result_start"] is None


def test_csl_loader_returns_compatible_fresh_cache_without_network(tmp_path):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    scoreboard = _sample_csl_scoreboard()
    cache = {
        "version": "sports-dashboard-csl-scoreboard-v1",
        "cache_key": (
            "sports-dashboard-csl-scoreboard-v1|"
            "https://site.web.api.espn.com/apis/site/v2/sports/soccer/chn.1/scoreboard|"
            "2026-07-18|2026-08-01|America/Los_Angeles|100"
        ),
        "fetched_at": "2026-07-25T11:59:30+00:00",
        "scoreboard": scoreboard,
    }
    (tmp_path / "csl_espn.json").write_text(
        json.dumps(cache),
        encoding="utf-8",
    )
    plugin = _CSLHarness(tmp_path, _ForbiddenSession())

    payload, source_state, fetched_at = plugin._load_csl_scoreboard(
        {},
        ZoneInfo("America/Los_Angeles"),
        now,
    )

    assert payload == scoreboard
    assert source_state == "CSL ESPN CACHE"
    assert fetched_at == "2026-07-25T11:59:30+00:00"


def test_csl_loader_fetches_only_bounded_window_and_persists_current_last_good_and_budget(
    tmp_path,
):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    scoreboard = _sample_csl_scoreboard()
    session = _RecordingSession(scoreboard)
    plugin = _CSLHarness(tmp_path, session)

    payload, source_state, fetched_at = plugin._load_csl_scoreboard(
        {},
        ZoneInfo("America/Los_Angeles"),
        now,
    )

    assert payload == scoreboard
    assert source_state == "CSL ESPN LIVE"
    assert fetched_at == "2026-07-25T12:00:00+00:00"
    assert session.calls == [
        (
            "https://site.web.api.espn.com/apis/site/v2/sports/soccer/chn.1/scoreboard",
            {
                "params": {
                    "dates": "20260718-20260801",
                    "limit": "100",
                },
                "headers": {
                    "Accept": "application/json",
                    "User-Agent": "InkyPi/1.0",
                },
                "timeout": 20,
            },
        )
    ]
    current = json.loads((tmp_path / "csl_espn.json").read_text(encoding="utf-8"))
    last_good = json.loads((tmp_path / "csl_espn.last_good.json").read_text(encoding="utf-8"))
    request_state = json.loads((tmp_path / "csl_espn_requests.json").read_text(encoding="utf-8"))
    assert current == last_good
    assert current["scoreboard"] == scoreboard
    assert request_state == {
        "version": "sports-dashboard-csl-requests-v1",
        "date": "2026-07-25",
        "count": 1,
        "updated_at": "2026-07-25T12:00:00+00:00",
    }


def test_csl_loader_rejects_http_200_object_without_events_and_preserves_last_good(
    tmp_path,
):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    scoreboard = _sample_csl_scoreboard()
    cached = {
        "version": "sports-dashboard-csl-scoreboard-v1",
        "cache_key": (
            "sports-dashboard-csl-scoreboard-v1|"
            "https://site.web.api.espn.com/apis/site/v2/sports/soccer/chn.1/scoreboard|"
            "2026-07-23|2026-08-01|UTC|100"
        ),
        "fetched_at": "2026-07-25T10:00:00+00:00",
        "scoreboard": scoreboard,
    }
    current_path = tmp_path / "csl_espn.json"
    last_good_path = tmp_path / "csl_espn.last_good.json"
    current_path.write_text(json.dumps(cached), encoding="utf-8")
    last_good_path.write_text(json.dumps(cached), encoding="utf-8")
    plugin = _CSLHarness(tmp_path, _RecordingSession({}))

    payload, source_state, fetched_at = plugin._load_csl_scoreboard(
        {"_forceRefresh": "true"},
        timezone.utc,
        now,
    )

    assert payload == scoreboard
    assert source_state == "CSL ESPN STALE"
    assert fetched_at == "2026-07-25T10:00:00+00:00"
    assert json.loads(current_path.read_text(encoding="utf-8")) == cached
    assert json.loads(last_good_path.read_text(encoding="utf-8")) == cached


def test_csl_live_state_tracks_confirmed_match_and_bridges_consecutive_kickoff(
    tmp_path,
):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    live = {
        "event_id": "live",
        "start": now - timedelta(minutes=30),
        "state": "2H",
        "status": "67'",
        "team_a": "上海海港",
        "team_b": "上海申花",
        "wins_a": 2,
        "wins_b": 1,
        "provider": "ESPN",
        "score_source": "ESPN",
        "provider_status_confirmed": True,
        "score_confirmed": True,
    }
    next_match = {
        "event_id": "next",
        "start": now + timedelta(hours=2, minutes=45),
        "state": "TIMED",
        "team_a": "北京国安",
        "team_b": "山东泰山",
    }
    selected = CSLMixin._select_csl_event_sections([live, next_match], now)
    plugin = _CSLHarness(tmp_path, _ForbiddenSession())

    plugin._write_csl_live_state(
        selected,
        now,
        "CSL ESPN LIVE",
        "2026-07-25T11:59:30+00:00",
    )

    state = json.loads((tmp_path / "csl_live_state.json").read_text(encoding="utf-8"))
    assert state == {
        "version": "sports-dashboard-csl-live-v1",
        "updated_at": "2026-07-25T12:00:00+00:00",
        "source_state": "CSL ESPN LIVE",
        "fetched_at": "2026-07-25T11:59:30+00:00",
        "has_live": True,
        "live_until": "2026-07-25T17:45:00+00:00",
        "event_id": "live",
        "team_a": "上海海港",
        "team_b": "上海申花",
        "score": "2-1",
        "state": "2H",
        "status": "67'",
        "started_at": "2026-07-25T11:30:00+00:00",
        "provider": "ESPN",
        "score_source": "ESPN",
        "provider_status_confirmed": True,
        "score_confirmed": True,
    }


def test_csl_force_refresh_still_respects_daily_budget_and_returns_stale_cache(
    tmp_path,
):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    scoreboard = _sample_csl_scoreboard()
    cache = {
        "version": "sports-dashboard-csl-scoreboard-v1",
        "cache_key": (
            "sports-dashboard-csl-scoreboard-v1|"
            "https://site.web.api.espn.com/apis/site/v2/sports/soccer/chn.1/scoreboard|"
            "2026-07-23|2026-08-01|America/Los_Angeles|100"
        ),
        "fetched_at": "2026-07-25T10:00:00+00:00",
        "scoreboard": scoreboard,
    }
    (tmp_path / "csl_espn.json").write_text(json.dumps(cache), encoding="utf-8")
    (tmp_path / "csl_espn_requests.json").write_text(
        json.dumps(
            {
                "version": "sports-dashboard-csl-requests-v1",
                "date": "2026-07-25",
                "count": 1,
                "updated_at": "2026-07-25T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    session = _RecordingSession(scoreboard)
    plugin = _CSLHarness(tmp_path, session)

    payload, source_state, fetched_at = plugin._load_csl_scoreboard(
        {
            "_forceRefresh": "true",
            "cslEspnDailyLimit": "1",
        },
        ZoneInfo("America/Los_Angeles"),
        now,
    )

    assert payload == scoreboard
    assert source_state == "CSL ESPN STALE"
    assert fetched_at == "2026-07-25T10:00:00+00:00"
    assert session.calls == []


def test_csl_exhausted_budget_without_cache_returns_limit_without_network(tmp_path):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    (tmp_path / "csl_espn_requests.json").write_text(
        json.dumps(
            {
                "version": "sports-dashboard-csl-requests-v1",
                "date": "2026-07-25",
                "count": 1,
                "updated_at": "2026-07-25T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    session = _RecordingSession(_sample_csl_scoreboard())
    plugin = _CSLHarness(tmp_path, session)

    payload, source_state, fetched_at = plugin._load_csl_scoreboard(
        {"cslEspnDailyLimit": "1"},
        ZoneInfo("America/Los_Angeles"),
        now,
    )

    assert payload == {}
    assert source_state == "CSL ESPN LIMIT"
    assert fetched_at is None
    assert session.calls == []


def test_csl_pregame_cache_uses_live_refresh_ttl_at_sixty_second_boundary(
    tmp_path,
):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    scoreboard = copy.deepcopy(_sample_csl_scoreboard())
    event = scoreboard["events"][0]
    event["date"] = "2026-07-25T12:20:00Z"
    competition = event["competitions"][0]
    competition["status"] = {
        "type": {
            "name": "STATUS_SCHEDULED",
            "state": "pre",
            "completed": False,
            "detail": "Scheduled",
            "shortDetail": "Scheduled",
        }
    }
    plugin = _CSLHarness(tmp_path, _ForbiddenSession())

    expired = plugin._csl_scoreboard_cache_is_fresh(
        {
            "fetched_at": "2026-07-25T11:58:59+00:00",
            "scoreboard": scoreboard,
        },
        {},
        timezone.utc,
        now,
    )
    boundary = plugin._csl_scoreboard_cache_is_fresh(
        {
            "fetched_at": "2026-07-25T11:59:00+00:00",
            "scoreboard": scoreboard,
        },
        {},
        timezone.utc,
        now,
    )

    assert expired is False
    assert boundary is True


def test_csl_loader_falls_back_to_independent_last_good_after_fetch_failure(
    tmp_path,
):
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    scoreboard = _sample_csl_scoreboard()
    (tmp_path / "csl_espn.last_good.json").write_text(
        json.dumps(
            {
                "version": "sports-dashboard-csl-scoreboard-v1",
                "cache_key": "previous-window",
                "fetched_at": "2026-07-24T12:00:00+00:00",
                "scoreboard": scoreboard,
            }
        ),
        encoding="utf-8",
    )
    session = _RaisingSession()
    plugin = _CSLHarness(tmp_path, session)

    payload, source_state, fetched_at = plugin._load_csl_scoreboard(
        {},
        timezone.utc,
        now,
    )

    assert payload == scoreboard
    assert source_state == "CSL ESPN STALE"
    assert fetched_at == "2026-07-24T12:00:00+00:00"
    assert session.calls == 1
    request_state = json.loads((tmp_path / "csl_espn_requests.json").read_text(encoding="utf-8"))
    assert request_state["count"] == 1
