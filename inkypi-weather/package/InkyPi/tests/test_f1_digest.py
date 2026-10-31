from datetime import datetime, timedelta, timezone

from plugins.sports_dashboard import f1_digest
from plugins.sports_dashboard.sports_dashboard import SportsDashboard

START = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
LATER = START + timedelta(hours=3)
DRIVERS = [{"driver_number": 12, "name_acronym": "ANT", "team_name": "Mercedes"},
           {"driver_number": 1, "name_acronym": "VER", "team_name": "Red Bull Racing"}]


def _session(key, kind, start, minutes=60, name=None):
    return {"session_key": key, "session_type": kind, "session_name": name or kind,
            "date_start": start.isoformat(), "date_end": (start + timedelta(minutes=minutes)).isoformat()}


def test_a_practice_or_qualifying_keeps_each_drivers_best_lap():
    assert f1_digest.best_lap(77.565) == 77.565
    # Qualifying: Q1-Q3, the last part a driver reached.
    assert f1_digest.best_lap([76.9, 76.5, None]) == 76.5
    assert f1_digest.best_lap([None, None, None]) is None
    assert f1_digest.lap_label(76.402) == "1:16.402"
    assert f1_digest.lap_label(59.87) == "59.870"


def test_a_race_keeps_its_tyres_and_how_often_the_safety_car_came_out():
    session = _session(9, "Race", START, name="Race")
    stints = [{"driver_number": 12, "stint_number": 2, "compound": "HARD"},
              {"driver_number": 12, "stint_number": 1, "compound": "MEDIUM"},
              {"driver_number": 1, "stint_number": 1, "compound": "SOFT"},
              {"driver_number": 1, "stint_number": 2, "compound": "UNKNOWN"}]
    control = [{"category": "SafetyCar", "message": "SAFETY CAR DEPLOYED"},
               {"category": "SafetyCar", "message": "SAFETY CAR IN THIS LAP"},
               {"category": "SafetyCar", "message": "VIRTUAL SAFETY CAR DEPLOYED"},
               {"category": "Flag", "flag": "RED", "message": "RED FLAG"},
               {"category": "Flag", "flag": "YELLOW", "message": "YELLOW IN TRACK SECTOR 4"}]
    results = [{"position": 2, "driver_number": 1, "duration": 5800.1}, {"position": 1, "driver_number": 12, "duration": 5790.0}]

    digest = f1_digest.session_digest(session, results, DRIVERS, stints, control)

    assert [row["code"] for row in digest["top"]] == ["ANT", "VER"]
    assert digest["race"] and digest["top"][0]["best"] is None
    assert digest["tyres"] == {"ANT": ["M", "H"], "VER": ["S"]}
    assert digest["control"] == {"SC": 1, "VSC": 1, "RED": 1}


def test_calendar_sessions_match_openf1s_by_start_and_open_half_an_hour_after_the_end():
    sessions = [_session(1, "Practice", START), _session(2, "Qualifying", START + timedelta(hours=4))]
    assert f1_digest.match_session(sessions, START + timedelta(minutes=10))["session_key"] == 1
    assert f1_digest.match_session(sessions, START + timedelta(hours=2)) is None
    assert not f1_digest.historical(sessions[0], START + timedelta(minutes=80))
    assert f1_digest.historical(sessions[0], START + timedelta(minutes=95))


class _Plugin(SportsDashboard):
    def __init__(self, tmp_path, answers):
        super().__init__({"id": "sports_dashboard"})
        self.tmp_path, self.answers, self.calls = tmp_path, answers, []

    def _f1_digest_cache_path(self):
        return self.tmp_path / "digests.json"

    def _f1_openf1_state_path(self):
        return self.tmp_path / "state.json"

    def _f1_openf1_get(self, settings, endpoint, params):
        self.calls.append(endpoint)
        return self.answers(endpoint, params)


def test_finished_sessions_are_fetched_once_between_sessions_and_kept(tmp_path):
    sessions = [_session(1, "Practice", START, name="Practice 1"), _session(2, "Race", START + timedelta(days=1))]
    rows = {"sessions": sessions, "session_result": [{"position": 1, "driver_number": 12, "duration": 76.4}],
            "drivers": DRIVERS}
    plugin = _Plugin(tmp_path, lambda endpoint, _params: rows[endpoint])
    selected = {"weekend_sessions": [{"label": "FP1", "start": START}, {"label": "RACE", "start": START + timedelta(days=1)}]}

    plugin._attach_f1_session_digests(selected, {}, LATER)
    assert plugin.calls == ["sessions", "session_result", "drivers"]
    assert selected["weekend_sessions"][0]["digest"]["top"][0] == {
        "position": 1, "code": "ANT", "team": "Mercedes", "best": 76.4, "laps": None}
    assert "digest" not in selected["weekend_sessions"][1]

    again = {"weekend_sessions": [{"label": "FP1", "start": START}]}
    plugin._attach_f1_session_digests(again, {}, LATER)
    assert plugin.calls == ["sessions", "session_result", "drivers"]
    assert again["weekend_sessions"][0]["digest"]["top"][0]["code"] == "ANT"


def test_while_a_session_runs_it_waits_and_serves_what_it_has(tmp_path):
    plugin = _Plugin(tmp_path, lambda _endpoint, _params: None)
    selected = {"weekend_sessions": [{"label": "FP1", "start": START}]}

    plugin._attach_f1_session_digests(selected, {}, LATER)
    plugin._attach_f1_session_digests(selected, {}, LATER)
    assert plugin.calls == ["sessions"]
    assert "digest" not in selected["weekend_sessions"][0]

