from datetime import datetime, timedelta, timezone

from plugins.sports_dashboard.sports_dashboard import SportsDashboard


def test_wnba_shared_nba_prestate_remains_eligible_for_upcoming_card():
    now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    event = {"state": "unstarted", "start": now + timedelta(hours=3),
             "team_a": "NY", "team_b": "LV", "event_id": "real-prestate"}
    card = SportsDashboard._offseason_hub_card("WNBA", {"events": [event]}, now)
    assert card["main"]["event_id"] == "real-prestate"
    assert card["status"] != "BREAK"
