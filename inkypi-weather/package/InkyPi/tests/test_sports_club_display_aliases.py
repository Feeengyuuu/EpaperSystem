"""Provider aliases must also repair previously cached English display labels."""

from plugins.sports_dashboard.sports_dashboard import SportsDashboard
import pytest


def test_schalke_provider_formal_name_is_chinese():
    plugin = SportsDashboard({"id": "sports_dashboard"})
    event = {"league_code": "BL1", "away_name": "FC Schalke 04"}
    assert plugin._club_team_display_name(event, "away") == "沙尔克04"
    assert event["away_name"] == "FC Schalke 04"


def test_schalke_cached_english_label_is_relocalized_without_changing_identity():
    plugin = SportsDashboard({"id": "sports_dashboard"})
    event = {
        "league_code": "BL1", "away_name": "FC Schalke 04", "away_team_id": "133",
        "away_name_zh": "FC Schalke 04",
    }
    original = dict(event)
    assert plugin._club_team_display_name(event, "away") == "沙尔克04"
    assert event == original


@pytest.mark.parametrize('league,name,expected', [('PD', 'Deportivo', '拉科鲁尼亚'), ('BL1', 'FC Schalke 04', '沙尔克04')])
def test_current_provider_aliases_work_without_cross_provider_ids(league, name, expected):
    assert SportsDashboard._club_team_zh_name(league, name) == expected


def test_chinese_override_and_unknown_club_are_preserved():
    plugin = SportsDashboard({'id': 'sports_dashboard'})
    assert plugin._club_team_display_name({'away_name': 'Arsenal', 'away_name_zh': '阿森纳'}, 'away') == '阿森纳'
    assert plugin._club_team_display_name({'league_code': 'PL', 'away_name': 'Unknown City', 'away_name_zh': 'Unknown City'}, 'away') == 'Unknown City'
