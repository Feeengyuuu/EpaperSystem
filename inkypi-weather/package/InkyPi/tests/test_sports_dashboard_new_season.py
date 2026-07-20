import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_sports_dashboard_defaults_directly_to_five_club_leagues():
    from plugins.sports_dashboard.club_football import ClubFootballMixin

    assert ClubFootballMixin._football_panel_mode({}) == "club"
    assert ClubFootballMixin._football_panel_mode(
        {"footballPanelMode": "not-a-valid-mode"}
    ) == "club"


def test_default_football_route_never_enters_world_cup_path(monkeypatch):
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard.__new__(SportsDashboard)
    panel = object()

    def world_cup_path_must_not_run(*_args, **_kwargs):
        raise AssertionError("default club route entered the World Cup path")

    monkeypatch.setattr(plugin, "_worldcup_schedule_summary", world_cup_path_must_not_run)
    monkeypatch.setattr(plugin, "_render_worldcup_slot", world_cup_path_must_not_run)
    monkeypatch.setattr(
        plugin,
        "_render_club_football_slot",
        lambda *_args, **_kwargs: (panel, "fresh", "CLUB CACHE"),
    )

    rendered = plugin._render_selected_football_panel(
        {},
        {},
        (420, 250),
        timezone.utc,
        3,
        datetime(2026, 7, 19, tzinfo=timezone.utc),
    )

    assert rendered == (panel, "fresh", "CLUB CACHE", None)
