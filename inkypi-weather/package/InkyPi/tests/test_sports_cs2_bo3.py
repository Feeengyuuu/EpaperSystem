"""bo3.gg as the CS2 card's keyless source: rows in PandaScore's shape, cached feeds, live maps."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard import cs2_bo3
from plugins.sports_dashboard.common import _ACTIVE_COLORS, COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.esports_render import ESPORTS_SOURCE_LOGO_DIR
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from runtime.refresh_contracts import TaskCancelled

NOW = datetime(2026, 11, 3, 0, 45, tzinfo=timezone.utc)
TOURNAMENT = {"name": "Intel Extreme Masters Beijing 2026", "tier": "s", "start_date": "2026-11-02T07:00:00.000+00:00",
              "end_date": "2026-11-07T23:00:00.000+00:00", "last_match_date": "2026-11-08T07:30:00.000+00:00",
              "image_url": "https://files.bo3.gg/uploads/tournament/5113/image/webp-b3.webp"}


def match(match_id, team1, team2, start, status="upcoming", scores=(0, 0), stage="Group B", end=None):
    return {"id": match_id, "slug": f"{team1.lower()}-vs-{team2.lower()}", "status": status, "bo_type": 3, "tier": "s",
            "start_date": start.isoformat(), "end_date": end.isoformat() if end else None,
            "team1_score": scores[0], "team2_score": scores[1], "tournament": TOURNAMENT,
            "stage": {"title": f"Intel Extreme Masters Beijing 2026 {stage}"},
            "team1": {"id": match_id * 10 + 1, "name": team1, "image_url": f"https://files.bo3.gg/uploads/team/{team1}.webp"},
            "team2": {"id": match_id * 10 + 2, "name": team2, "image_url": "https://evil.example/logo.png"}}


ACTIVE = [match(1, "Liquid", "PARIVISION", NOW + timedelta(hours=1, minutes=15)),
          match(2, "Aurora", "G2", NOW + timedelta(hours=1, minutes=15)),
          match(3, "Unknown Five", "Nobody", NOW + timedelta(hours=2))]
PAST = [match(9, "Natus Vincere", "Team Falcons", NOW - timedelta(hours=13), "finished", (2, 0), "Group A",
              end=NOW - timedelta(hours=11))]
GAMES = {"games": [
    {"number": 2, "status": "current", "map_name": "de_inferno", "winner_clan_name": "Liquid", "winner_clan_score": 7,
     "loser_clan_name": "PARIVISION", "loser_clan_score": 5},
    {"number": 1, "status": "finished", "map_name": "de_mirage", "winner_clan_name": "PARIVISION", "winner_clan_score": 13,
     "loser_clan_name": "Liquid", "loser_clan_score": 11},
    {"number": 3, "status": "upcoming", "map_name": "de_nuke"},
]}


def test_bo3_matches_become_pandascore_rows():
    row = cs2_bo3.bo3_row(ACTIVE[0], "active")
    assert row["id"] == "bo3:1" and row["status"] == "not_started" and row["number_of_games"] == 3
    assert row["serie"]["full_name"] == "IEM Beijing 2026"
    assert row["tournament"]["name"] == "Group B"
    assert [entry["opponent"]["id"] for entry in row["opponents"]] == [-11, -12]
    assert row["results"] == [{"team_id": -11, "score": 0}, {"team_id": -12, "score": 0}]
    assert cs2_bo3.bo3_row({**ACTIVE[0], "status": "deleted"}) is None
    assert cs2_bo3.bo3_row({**ACTIVE[0], "team2": {}}) is None
    assert cs2_bo3.parse_match_maps(GAMES, "Liquid", "PARIVISION") == [
        {"name": "Mirage", "team_a_score": 11, "team_b_score": 13},
        {"name": "Inferno", "team_a_score": 7, "team_b_score": 5}]


def loader(monkeypatch, tmp_path, responses):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    calls = []

    def request(_method, url, **kwargs):
        key = "detail" if url.rsplit("/", 1)[-1] != "matches" else kwargs["params"]["filter[matches.status][in]"]
        calls.append((key, kwargs))
        response = responses[key]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(data=response)

    @contextmanager
    def client():
        yield SimpleNamespace(request_json=request)

    monkeypatch.setattr(cs2_bo3, "create_single_attempt_http_client", client)
    monkeypatch.setattr(cs2_bo3, "enrich_event_branding", lambda _plugin, card, *_args, **_kwargs: card)
    monkeypatch.setattr(cs2_bo3, "enrich_team_logos", lambda _plugin, card, *_args, **_kwargs: card)
    return plugin, calls


def load(plugin, now=NOW, settings=None):
    return cs2_bo3.load_bo3_cs2_card(plugin, settings or {}, timezone.utc, now, session=None, game_logo_path="cs.png")


def test_the_card_follows_the_s_tier_event_and_credits_bo3(monkeypatch, tmp_path):
    plugin, calls = loader(monkeypatch, tmp_path, {"upcoming,current": {"results": ACTIVE}, "finished": {"results": PAST}})
    card, state = load(plugin)
    assert state == "BO3 LIVE" and card["status"] == "NEXT" and card["source"] == "bo3.gg"
    assert card["event_name"] == "IEM Beijing 2026"
    assert (card["main"]["team_a"], card["main"]["team_b"]) == ("Liquid", "PARIVISION")
    assert card["main"]["team_a_logo"].startswith("https://files.bo3.gg/") and card["main"]["team_b_logo"] == ""
    assert [event["team_a"] for event in card["recent"]] == ["Natus Vincere"] and card["recent"][0]["wins_a"] == 2
    assert all(event["source"] == "bo3.gg" for event in card["events"])
    params = calls[0][1]["params"]
    assert params["filter[matches.tier][in]"] == "s" and params["with"] == "teams,tournament,stage"
    assert all(call[1]["max_bytes"] == cs2_bo3.MAX_BYTES for call in calls)
    # Within the TTL nothing is fetched again; a theme-only render never fetches.
    assert load(plugin, NOW + timedelta(minutes=5))[1] == "BO3 CACHE" and len(calls) == 2
    assert load(plugin, NOW + timedelta(hours=1), {"_theme_render_only": True})[0]["main"]["team_a"] == "Liquid"
    assert len(calls) == 2


def test_an_outage_keeps_the_last_rows_as_stale(monkeypatch, tmp_path):
    responses = {"upcoming,current": {"results": ACTIVE}, "finished": {"results": PAST}}
    plugin, calls = loader(monkeypatch, tmp_path, responses)
    load(plugin)
    responses["upcoming,current"] = TimeoutError("down")
    responses["finished"] = {"results": "broken"}
    card, state = load(plugin, NOW + timedelta(minutes=20))
    assert state == "BO3 STALE" and card["main"]["team_a"] == "Liquid"
    assert load(plugin, NOW + timedelta(days=3))[1] == "BO3 NO DATA"


def test_a_live_featured_match_reads_its_maps(monkeypatch, tmp_path):
    live = [{**ACTIVE[0], "status": "current", "team1_score": 0, "team2_score": 1,
             "start_date": (NOW - timedelta(minutes=40)).isoformat()}, *ACTIVE[1:]]
    plugin, calls = loader(monkeypatch, tmp_path, {"upcoming,current": {"results": live}, "finished": {"results": PAST},
                                                   "detail": GAMES})
    card, _state = load(plugin)
    assert card["status"] == "LIVE" and card["main"]["wins_b"] == 1
    assert [item["name"] for item in card["main"]["maps"]] == ["Mirage", "Inferno"]
    assert calls[-1][1]["params"] == {"with": "games"}
    assert sum(call[0] == "detail" for call in calls) == 1
    load(plugin, NOW + timedelta(minutes=1))
    assert sum(call[0] == "detail" for call in calls) == 1


def test_cancellation_propagates(monkeypatch, tmp_path):
    plugin, _calls = loader(monkeypatch, tmp_path, {"upcoming,current": TaskCancelled("stop"), "finished": {"results": []}})
    with pytest.raises(TaskCancelled):
        load(plugin)


@pytest.mark.parametrize("pandascore, expected", [
    ((None, "PANDASCORE NO KEY"), "bo3"),
    (({"status": "NEXT"}, "PANDASCORE STALE"), "bo3"),
    (({"status": "NEXT"}, "PANDASCORE LIVE"), "pandascore"),
    (({"status": "RECENT"}, "PANDASCORE LIVE"), "bo3"),
])
def test_bo3_steps_in_only_when_pandascore_has_nothing_fresh_to_show(monkeypatch, pandascore, expected):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    calls = []

    def fake(*_args, **_kwargs):
        calls.append(1)
        return {"status": "NEXT", "source": "bo3.gg"}, "BO3 LIVE"

    monkeypatch.setattr(cs2_bo3, "load_bo3_cs2_card", fake)
    card, state = plugin._cs2_with_bo3_fallback(*pandascore, {}, timezone.utc, NOW)
    assert (state == "BO3 LIVE") is (expected == "bo3")
    assert bool(calls) is (expected == "bo3" or pandascore[0] is None or pandascore[0]["status"] == "RECENT")
    assert plugin._cs2_with_bo3_fallback(None, "PANDASCORE NO KEY", {"cs2Bo3Enabled": False}, timezone.utc, NOW)[0] is None


def test_a_recent_pandascore_result_stays_when_bo3_has_only_results_too(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(cs2_bo3, "load_bo3_cs2_card", lambda *_args, **_kwargs: ({"status": "RECENT"}, "BO3 CACHE"))
    assert plugin._cs2_with_bo3_fallback({"status": "RECENT"}, "PANDASCORE CACHE", {}, timezone.utc, NOW)[1] == "PANDASCORE CACHE"


def test_bo3_mark_has_provenance_and_credits_the_card():
    folder = Path(ESPORTS_SOURCE_LOGO_DIR)
    entry = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["logos"]["bo3"]
    assert hashlib.sha256((folder / "bo3.png").read_bytes()).hexdigest() == entry["sha256"]
    assert entry["official_site"] == "https://bo3.gg/"
    with Image.open(folder / "bo3.png") as mark:
        assert mark.mode == "RGBA" and list(mark.size) == entry["size"]
    plugin = SportsDashboard({"id": "sports_dashboard"})
    drawn = []
    original = plugin._draw_text_in_box
    plugin._draw_text_in_box = lambda draw, box, text, *args, **kwargs: (drawn.append(text), original(draw, box, text, *args, **kwargs))
    for colors in (DAY_COLORS, DEEP_NIGHT_COLORS):
        token = _ACTIVE_COLORS.set(colors)
        try:
            image = Image.new("RGB", (240, 20), COLORS["panel"])
            assert plugin._draw_valve_feed_credit(image, ImageDraw.Draw(image), (4, 4, 200, 14), "bo3.gg", COLORS["text"])
        finally:
            _ACTIVE_COLORS.reset(token)
    assert drawn.count("bo3.gg") == 2
    assert SportsDashboard._valve_source_attribution("bo3.gg") == "SOURCE: BO3.GG"
