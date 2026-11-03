"""ESPN's NFL matchup predictor and FPI on the NFL card."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard import nfl_insights
from plugins.sports_dashboard.common import _ACTIVE_COLORS, COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from runtime.refresh_contracts import TaskCancelled

NOW = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
TEAM = "http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/teams/{}?lang=en"


def predictor(home="26", away="3"):
    def side(team, win, margin):
        return {"team": {"$ref": TEAM.format(team)}, "statistics": [
            {"name": "gameProjection", "value": win}, {"name": "matchupQuality", "value": 88.872},
            {"name": "teamPredPtDiff", "value": margin}]}
    return {"name": "Chicago Bears at Seattle Seahawks", "homeTeam": side(home, 56.627, 1.795), "awayTeam": side(away, 43.027, -1.795)}


def fpi():
    def item(team, rank, playoffs, title):
        return {"team": {"$ref": TEAM.format(team)}, "predictives": [
            {"name": "fpi", "value": 3.1}, {"name": "fpirank", "value": float(rank)},
            {"name": "probmakeplayoffs", "value": playoffs}, {"name": "probwintitle", "value": title}]}
    return {"count": 3, "items": [item("26", 4, 68.1, 5.2), item("3", 7, 72.9, 5.4), item("25", 1, 99.1, 23.2), {"team": {}}]}


def card(state="scheduled", start=NOW + timedelta(hours=5)):
    main = {"event_id": "401873036", "state": state, "start": start, "team_a": "CHI", "team_b": "SEA",
            "team_a_id": "3", "team_b_id": "26", "team_a_code": "CHI", "team_b_code": "SEA", "record_a": "5-2", "record_b": "4-3"}
    return {"sport": "NFL", "main": main, "events": [main]}


def test_predictor_and_fpi_parse():
    parsed = nfl_insights.parse_predictor(predictor())
    assert parsed["home"] == {"team_id": "26", "win": pytest.approx(0.56627), "margin": 1.795}
    assert parsed["quality"] == pytest.approx(88.872)
    assert nfl_insights.parse_predictor({"homeTeam": {"statistics": []}, "awayTeam": {}}) == {}
    teams = nfl_insights.parse_fpi(fpi())
    assert teams["3"] == {"rank": 7, "playoffs": pytest.approx(0.729), "title": pytest.approx(0.054)}
    assert set(teams) == {"26", "3", "25"}
    assert nfl_insights.nfl_season(datetime(2027, 1, 10)) == 2026 and nfl_insights.nfl_season(datetime(2026, 9, 10)) == 2026


def loader(monkeypatch, tmp_path, responses):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    calls = []

    def request(_method, url, **kwargs):
        kind = "fpi" if "powerindex" in url else "predictor"
        calls.append((kind, url, kwargs))
        response = responses[kind]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(data=response)

    @contextmanager
    def client():
        yield SimpleNamespace(request_json=request)

    monkeypatch.setattr(nfl_insights, "create_single_attempt_http_client", client)
    return plugin, calls


def test_the_featured_game_takes_the_predictor_and_both_teams_fpi(monkeypatch, tmp_path):
    plugin, calls = loader(monkeypatch, tmp_path, {"predictor": predictor(), "fpi": fpi()})
    main = nfl_insights.attach_nfl_insights(plugin, card(), {}, NOW)["main"]
    assert main["predictor"] == {"win_a": pytest.approx(0.43027), "win_b": pytest.approx(0.56627),
                                 "margin_a": -1.795, "quality": pytest.approx(88.872)}
    assert main["fpi_a"]["rank"] == 7 and main["fpi_b"]["rank"] == 4
    assert [call[0] for call in calls] == ["predictor", "fpi"]
    assert calls[1][1].endswith("/seasons/2026/powerindex") and calls[1][2]["params"] == {"limit": 40}
    assert all(call[2]["max_bytes"] == nfl_insights.MAX_BYTES for call in calls)
    # Cached: no request within the TTLs, nor from a cache-only render.
    nfl_insights.attach_nfl_insights(plugin, card(), {}, NOW + timedelta(hours=2))
    nfl_insights.attach_nfl_insights(plugin, card(), {"_inkypi_ewc_cache_only": True}, NOW + timedelta(hours=4))
    assert len(calls) == 2


def test_a_live_game_keeps_the_fpi_but_not_the_pregame_predictor(monkeypatch, tmp_path):
    plugin, calls = loader(monkeypatch, tmp_path, {"predictor": predictor(), "fpi": fpi()})
    main = nfl_insights.attach_nfl_insights(plugin, card("live", NOW - timedelta(hours=1)), {}, NOW)["main"]
    assert "predictor" not in main and main["fpi_b"]["title"] == pytest.approx(0.052)
    assert [call[0] for call in calls] == ["fpi"]


def test_failures_leave_the_card_unchanged_and_cancellation_propagates(monkeypatch, tmp_path):
    plugin, calls = loader(monkeypatch, tmp_path, {"predictor": TimeoutError("down"), "fpi": {"items": "broken"}})
    original = card()
    assert nfl_insights.attach_nfl_insights(plugin, original, {}, NOW) == original
    nfl_insights.attach_nfl_insights(plugin, original, {}, NOW + timedelta(minutes=5))
    assert len(calls) == 2
    plugin, _calls = loader(monkeypatch, tmp_path / "other", {"predictor": TaskCancelled("stop"), "fpi": fpi()})
    (tmp_path / "other").mkdir()
    with pytest.raises(TaskCancelled):
        nfl_insights.attach_nfl_insights(plugin, card(), {}, NOW)


def test_swapped_predictor_sides_follow_the_team_ids(monkeypatch, tmp_path):
    # ESPN naming the Bears home: the ids still put their chance on the away side of the card.
    plugin, _calls = loader(monkeypatch, tmp_path, {"predictor": predictor(home="3", away="26"), "fpi": fpi()})
    main = nfl_insights.attach_nfl_insights(plugin, card(), {}, NOW)["main"]
    assert main["predictor"]["win_a"] == pytest.approx(0.56627)


def test_card_rows_and_pregame_details_show_them(monkeypatch):
    event = {**card()["main"], "predictor": {"win_a": 0.43, "win_b": 0.566, "margin_a": -1.8, "quality": 88.9},
             "fpi_a": {"rank": 7, "playoffs": 0.729, "title": 0.054}, "fpi_b": {"rank": 31, "playoffs": 0.007, "title": 0.0},
             "team_a_zh": "熊", "team_b_zh": "海鹰", "leaders": [
                 {"label": "PASS", "name": "S. Darnold", "value": "1290 YDS", "side": "b"},
                 {"label": "RUSH", "name": "D. Swift", "value": "476 YDS", "side": "a"}]}
    assert SportsDashboard._football_row_note(event, "a") == "5-2 · FPI#7 · 季后赛73% · 夺冠5%"
    assert SportsDashboard._football_row_note(event, "b") == "4-3 · FPI#31 · 季后赛<1% · 夺冠0%"
    assert SportsDashboard._football_row_note({"record_a": "5-2"}, "a") == "5-2"
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *_args: None)
    drawn = []
    original = plugin._draw_text_in_box
    monkeypatch.setattr(plugin, "_draw_text_in_box", lambda draw, box, text, *args, **kwargs: (
        drawn.append(str(text)), original(draw, box, text, *args, **kwargs)))
    for colors in (DAY_COLORS, DEEP_NIGHT_COLORS):
        token = _ACTIVE_COLORS.set(colors)
        try:
            image = Image.new("RGB", (340, 120), COLORS["paper"])
            plugin._draw_nfl_pregame_details(image, ImageDraw.Draw(image), (12, 4, 330, 100), event, NOW)
        finally:
            _ACTIVE_COLORS.reset(token)
    caption = SportsDashboard._nfl_predictor_caption(event, event["predictor"])
    assert caption.startswith("对阵质量 89 · 预计 ") and caption.endswith("+1.8")
    assert "WIN" in drawn and caption in drawn and "S. Darnold" in drawn and "D. Swift" not in drawn


def test_the_lower_panel_attaches_the_insights_to_an_nfl_card_only(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    seen = []
    import plugins.sports_dashboard.lower_panel as lower_panel

    monkeypatch.setattr(lower_panel, "attach_nfl_insights", lambda _plugin, card, *_args: seen.append(card["sport"]) or card)
    monkeypatch.setattr(plugin, "_draw_offseason_hub_compact_panel", lambda *_args: None)
    monkeypatch.setattr(plugin, "_load_nba_events", lambda *_args: ([], "NBA NO DATA"))
    monkeypatch.setattr(plugin, "_load_lower_f1", lambda *_args: (None, ""))
    for sport in ("NFL", "MLB"):
        hub = {"cards": [{"sport": sport, "main": {"start": NOW}}]}
        monkeypatch.setattr(plugin, "_load_offseason_hub", lambda *_args, hub=hub: (hub, "HUB LIVE"))
        monkeypatch.setattr(SportsDashboard, "_choose_lower_panel",
                            staticmethod(lambda *_args, hub=hub, **_kwargs: {"kind": "hub", "key": "X", "card": hub["cards"][0]}))
        monkeypatch.setattr(plugin, "_write_offseason_hub_state", lambda *_args: None)
        monkeypatch.setattr(plugin, "_write_nba_live_state", lambda *_args: None)
        plugin._draw_lower_sports_region(Image.new("RGB", (800, 480)), None, {}, None, timezone.utc, NOW, (0, 212, 555, 479))
    assert seen == ["NFL"]
