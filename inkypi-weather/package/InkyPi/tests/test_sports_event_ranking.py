"""Scoring that decides what each Sports Dashboard region shows."""

from datetime import datetime, timedelta, timezone

import pytest

from plugins.sports_dashboard import event_ranking as ranking

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def test_live_beats_upcoming_beats_old_results():
    live = ranking.state_points("live", NOW - timedelta(hours=1), NOW)
    soon = ranking.state_points("scheduled", NOW + timedelta(minutes=30), NOW)
    later = ranking.state_points("scheduled", NOW + timedelta(days=3), NOW)
    fresh_result = ranking.state_points("final", NOW - timedelta(hours=4), NOW)
    old_result = ranking.state_points("final", NOW - timedelta(days=5), NOW)

    assert live > soon > fresh_result > later > old_result == 0


def test_upcoming_points_fall_steadily_with_distance():
    points = [ranking.state_points("scheduled", NOW + timedelta(hours=hours), NOW) for hours in range(0, 200, 6)]
    assert points == sorted(points, reverse=True)
    assert ranking.state_points("scheduled", NOW - timedelta(minutes=5), NOW) == 70.0


def test_big_game_bonus_is_full_only_on_its_day():
    tomorrow = NOW + timedelta(hours=20)
    in_two_days = NOW + timedelta(hours=48)
    assert ranking.score("scheduled", tomorrow, NOW, "S") - ranking.score("scheduled", tomorrow, NOW, "C") == 60
    assert ranking.score("scheduled", in_two_days, NOW, "S") - ranking.score("scheduled", in_two_days, NOW, "C") == 15
    assert ranking.on_game_day("final", NOW - timedelta(hours=20), NOW)
    assert not ranking.on_game_day("final", NOW - timedelta(hours=30), NOW)


@pytest.mark.parametrize(
    ("sport", "event", "tier"),
    [
        ("MLB", {"series_game": "World Series Game 1"}, "S"),
        ("MLB", {"series_game": "ALCS Game 6"}, "A"),
        ("MLB", {"series_game": ""}, "C"),
        ("WNBA", {"headline": "WNBA Finals - Game 4"}, "S"),
        ("NBA", {"headline": "East Conference Finals - Game 2"}, "A"),
        ("NBA", {"headline": "West 1st Round - Game 3"}, "A"),
        ("NBA", {"headline": "Emirates NBA Cup - Championship"}, "A"),
        ("NBA", {"block": "REGULAR SEASON"}, "C"),
        ("NFL", {"headline": "Super Bowl LXI"}, "S"),
        ("NFL", {"headline": "AFC Divisional Playoff"}, "A"),
        ("NFL", {"week_label": "WEEK 7"}, "B"),
        ("NCAA", {"note": "CFP National Championship"}, "S"),
        ("NCAA", {"team_a_rank": 4, "team_b_rank": 99}, "B"),
        ("NCAA", {"team_a_rank": 12, "team_b_rank": 18}, "B"),
        ("NCAA", {"team_a_rank": 99, "team_b_rank": 99}, "C"),
        ("PGA", {"name_en": "Masters Tournament"}, "A"),
        ("PGA", {"name_en": "Butterfield Bermuda Championship"}, "C"),
    ],
)
def test_team_sport_tiers(sport, event, tier):
    assert ranking.team_sport_tier(sport, event) == tier


def test_esports_tiers():
    assert ranking.esports_tier("lol", "WORLDS", "Swiss") == "S"
    assert ranking.esports_tier("valve", "TI", "The International 2026") == "S"
    assert ranking.esports_tier("valve", "CS", "PGL Major Copenhagen Playoffs") == "S"
    assert ranking.esports_tier("lol", "MSI", "Bracket") == "A"
    assert ranking.esports_tier("lol", "LPL", "Playoffs") == "A"
    assert ranking.esports_tier("lol", "LPL", "Week 3") == "C"
    # Every CS2 tournament has playoffs; only its grand final stands out.
    assert ranking.esports_tier("valve", "CS", "FISSURE Playground 3 Playoffs") == "C"
    assert ranking.esports_tier("valve", "CS", "IEM Chengdu Grand Final") == "A"


def test_f1_sessions():
    assert ranking.f1_session_tier("RACE") == "A"
    assert ranking.f1_session_tier("QUALIFYING") == "B"
    assert ranking.f1_session_tier("SPRINT") == "B"
    assert ranking.f1_session_tier("FP2") == "C"


def test_golf_is_live_only_while_someone_is_on_the_course():
    assert not ranking.golf_in_play({"leaderboard": [{"thru": ""}, {"thru": "F"}]})
    assert ranking.golf_in_play({"leaderboard": [{"thru": "F"}, {"thru": "12"}]})
    assert not ranking.golf_in_play({})


def test_fairness_lets_a_close_runner_up_take_the_next_display():
    candidates = [{"key": "NFL", "score": 60.0, "order": 1}, {"key": "MLB", "score": 50.0, "order": 2},
                  {"key": "NBA", "score": 20.0, "order": 0}]
    assert [item["key"] for item in ranking.rank(candidates)] == ["NFL", "MLB", "NBA"]
    assert ranking.rank(candidates, [("NFL",)])[0]["key"] == "MLB"
    assert ranking.rank(candidates, [("MLB",), ("NFL",)])[0]["key"] == "NFL"
    # A far stronger candidate keeps the panel even right after being shown.
    assert ranking.rank([{"key": "WS", "score": 140.0}, {"key": "NBA", "score": 60.0}], [("WS",)])[0]["key"] == "WS"
