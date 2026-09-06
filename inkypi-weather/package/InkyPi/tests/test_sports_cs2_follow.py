from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import pytest
import requests

from plugins.sports_dashboard.sports_dashboard import SportsDashboard
import plugins.sports_dashboard.sports_dashboard as sports_module


def cs_match(
    now,
    *,
    match_id=501,
    status="not_started",
    offset=2,
    event="FISSURE Playground 3",
    team_a="MOUZ",
    team_b="Spirit",
    tournament_id=23001,
):
    return {
        "id": match_id,
        "begin_at": (now + timedelta(hours=offset)).isoformat(),
        "status": status,
        "number_of_games": 3,
        "league": {"id": 900, "name": "FISSURE", "image_url": "https://cdn.pandascore.co/images/league/fissure.png"},
        "serie": {"id": 1001, "full_name": event, "year": now.year},
        "tournament": {"id": tournament_id, "name": "Playoffs", "tier": "s"},
        "opponents": [
            {"type": "Team", "opponent": {"id": 1, "name": team_a, "acronym": "MOUZ"}},
            {"type": "Team", "opponent": {"id": 2, "name": team_b, "acronym": "TS"}},
        ],
        "results": [],
    }


def test_cs2_follows_next_top_team_event_after_porto_without_manual_ids():
    now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    card = SportsDashboard._parse_pandascore_cs2_card(
        [cs_match(now)],
        timezone.utc,
        now,
        {},
    )
    assert card is not None, "The next named-team CS2 match must survive the finished Porto tournament"
    assert card["event_name"] == "FISSURE Playground 3"
    assert card["status"] == "NEXT"
    assert card["main"]["match_id"] == "501"
    assert card["main"]["team_a"] == "MOUZ"
    assert card["event_logo_url"] == "https://cdn.pandascore.co/images/league/fissure.png"


def test_upcoming_uses_current_event_schedule_including_unfollowed_teams():
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    live = cs_match(now, status="running", offset=-1)
    other = cs_match(now, match_id=502, offset=1, event="Another Cup")
    other["serie"]["id"] = 1002
    local = cs_match(now, match_id=503, offset=2, team_a="Small A", team_b="Small B", tournament_id=23002)
    local["opponents"][0]["opponent"]["image_url"] = "https://cdn-api.pandascore.co/images/small-a.png"
    card = SportsDashboard._parse_pandascore_cs2_card([other, local, live], timezone.utc, now, {})
    assert card["main"]["match_id"] == "501" and card["event_id"] == "series:1001"
    assert [event["match_id"] for event in card["upcoming"]] == ["503"]
    assert card["upcoming"][0]["team_a"] == "Small A"
    assert card["upcoming"][0]["team_a_logo"] == local["opponents"][0]["opponent"]["image_url"]
    assert all(event["event_id"] == card["event_id"] for event in card["events"])


def test_current_provider_cdn_and_series_name_contract_preserve_branding():
    # PandaScore's documented match payload uses cdn-api and a serie.full_name
    # that does not contain its league name (for example, PGL / Astana 2026).
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    match = cs_match(now, event="Bucharest: European Open Qualifier #2 2026", team_a="HEROIC", team_b="1win")
    match["league"] = {
        "id": 5365,
        "name": "PGL",
        "image_url": "https://cdn-api.pandascore.co/images/league/image/5365/800px-pgl_allmode-png-png",
    }
    match["opponents"][0]["opponent"]["image_url"] = "https://cdn-api.pandascore.co/images/team/image/7175/heroic.png"
    card = SportsDashboard._parse_pandascore_cs2_card([match], timezone.utc, now, {})
    assert card["event_logo_url"] == match["league"]["image_url"]
    assert card["main"]["team_a_logo"] == match["opponents"][0]["opponent"]["image_url"]
    assert card["event_name"] == "PGL Bucharest: European Open Qualifier #2 2026"
    assert card["event_logo_caption"] == "PGL Bucharest: EU Open Q2 2026"


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn-api.pandascore.co.evil.example/logo.png",
        "https://cdn-api.pandascore.co@evil.example/logo.png",
        "http://cdn-api.pandascore.co/logo.png",
    ],
)
def test_provider_logo_host_validation_remains_exact(url):
    from plugins.sports_dashboard.cs2_cards import safe_logo_url

    assert safe_logo_url(url) == ""


class FeedResponse:
    headers = {}
    status_code = 200

    def __init__(self, data, url):
        self.data, self.url = data, url

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield json.dumps(self.data).encode()

    def close(self):
        pass


class FeedSession:
    def __init__(self, feeds):
        self.feeds, self.calls = feeds, []

    def request(self, method, url, *, headers, params, timeout, stream):
        assert method == "GET" and stream
        assert headers["Authorization"] == "Bearer test-token"
        assert "test-token" not in url
        feed = url.rsplit("/", 1)[-1]
        if "/series/" in url:
            feed = "series:" + url.split("/series/", 1)[1].split("/", 1)[0]
        self.calls.append((feed, params))
        result = self.feeds.get(feed, [])
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = result(params)
        if isinstance(result, FeedResponse):
            return result
        return FeedResponse(result, url)


def loader(monkeypatch, tmp_path, feeds):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    session = FeedSession(feeds)
    monkeypatch.setattr(sports_module, "get_http_session", lambda: session)
    device = SimpleNamespace(load_env_key=lambda key: "test-token")
    return plugin, device, session


def test_automatic_loader_discovers_and_caches_matches_after_old_cutoff(monkeypatch, tmp_path):
    now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    plugin, device, session = loader(monkeypatch, tmp_path, {"running": [], "upcoming": [cs_match(now)], "past": []})
    settings = {"pandaScoreCs2HltvLogos": "false"}
    first, source = plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now)
    assert first and first["main"]["match_id"] == "501"
    assert source == "PANDASCORE LIVE"
    assert {feed for feed, params in session.calls} == {"running", "upcoming", "past", "series:1001"}
    cached, source = plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now + timedelta(seconds=30))
    assert cached["event_name"] == "FISSURE Playground 3"
    assert source == "PANDASCORE CACHE"
    assert len(session.calls) == 4


@pytest.mark.parametrize(
    "team_a,team_b",
    [("G2 Ares", "NAVI Junior"), ("MOUZ NXT", "Spirit Academy"), ("TBD", "MOUZ"), ("Unknown", "Vitality")],
)
def test_automatic_follow_rejects_academy_substring_matches_and_tbd(team_a, team_b):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    assert (
        SportsDashboard._parse_pandascore_cs2_card([cs_match(now, team_a=team_a, team_b=team_b)], timezone.utc, now, {})
        is None
    )


def test_next_event_does_not_inherit_finished_events_rows_or_logo():
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    old = cs_match(now, match_id=1, status="finished", offset=-4, event="Old Event")
    old["serie"]["id"] = 999
    old["league"]["image_url"] = "https://cdn.pandascore.co/images/old.png"
    card = SportsDashboard._parse_pandascore_cs2_card([old, cs_match(now)], timezone.utc, now, {})
    assert card["event_name"] == "FISSURE Playground 3"
    assert card["recent"] == []
    assert "old.png" not in card["event_logo_url"]
    completed = SportsDashboard._parse_pandascore_cs2_card([old], timezone.utc, now, {})
    assert completed["status"] == "RECENT" and completed["window_active"] is False


def test_upcoming_paginated_past_small_team_first_page(monkeypatch, tmp_path):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    minor = [cs_match(now, match_id=i + 1000, team_a="Small A", team_b="Small B") for i in range(100)]
    plugin, device, session = loader(
        monkeypatch,
        tmp_path,
        {
            "running": [],
            "past": [],
            "upcoming": lambda params: minor if params["page[number]"] == 1 else [cs_match(now)],
        },
    )
    card, _ = plugin._load_pandascore_cs2_card({"pandaScoreCs2HltvLogos": "false"}, device, timezone.utc, now)
    assert card["main"]["match_id"] == "501"
    assert [p["page[number]"] for f, p in session.calls if f == "upcoming"] == [1, 2]


def test_empty_running_refresh_retires_live_and_fetches_schedule_immediately(monkeypatch, tmp_path):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    running = cs_match(now, status="running", offset=-1)
    upcoming = cs_match(now, match_id=502, offset=4)
    plugin, device, session = loader(monkeypatch, tmp_path, {"running": [running], "past": [], "upcoming": [upcoming]})
    settings = {"pandaScoreCs2HltvLogos": "false"}
    card, _ = plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now)
    assert card["status"] == "LIVE"
    session.feeds.update(running=[], past=[{**running, "status": "finished"}])
    card, source = plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now + timedelta(minutes=4))
    assert card["status"] == "NEXT" and card["main"]["match_id"] == "502"
    assert card["recent"][0]["state"] == "completed"
    assert source == "PANDASCORE LIVE" and len(session.calls) == 8


def test_partial_network_failure_does_not_mark_old_running_as_live(monkeypatch, tmp_path):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    plugin, device, session = loader(
        monkeypatch,
        tmp_path,
        {
            "running": [cs_match(now, status="running", offset=-1)],
            "upcoming": [cs_match(now, match_id=502)],
            "past": [],
        },
    )
    settings = {"pandaScoreCs2HltvLogos": "false"}
    plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now)
    session.feeds["running"] = requests.Timeout("offline")
    card, source = plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now + timedelta(minutes=4))
    assert card["status"] == "NEXT" and card["main"]["match_id"] == "502"
    assert source == "PANDASCORE CACHE PARTIAL"
    assert not card["live"]


@pytest.mark.parametrize("code,label", [(401, "AUTH"), (403, "AUTH"), (429, "LIMIT")])
def test_provider_errors_are_backed_off_and_counted(monkeypatch, tmp_path, code, label):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    response = FeedResponse({}, "https://api.pandascore.co/csgo/matches/running")
    response.status_code = code
    response.headers = {"Retry-After": "1800"}
    plugin, device, session = loader(monkeypatch, tmp_path, {"running": response, "upcoming": [], "past": []})
    settings = {"pandaScoreCs2HltvLogos": "false"}
    card, source = plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now)
    assert card is None and label in source
    plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now + timedelta(minutes=10))
    assert len(session.calls) == 1
    assert plugin._pandascore_cs2_calls_left(settings, now) == 719


def test_cached_render_and_legacy_opt_out_use_their_existing_contracts(monkeypatch, tmp_path):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    plugin, device, session = loader(monkeypatch, tmp_path, {"running": [], "upcoming": [cs_match(now)], "past": []})
    settings = {"pandaScoreCs2HltvLogos": "false"}
    plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now)
    card, source = plugin._load_pandascore_cs2_card(
        {**settings, "_inkypi_ewc_cache_only": True}, device, timezone.utc, now + timedelta(hours=2)
    )
    assert card and "STALE" in source and len(session.calls) == 4
    monkeypatch.setattr(plugin, "_load_pandascore_cs2_fixed_card", lambda *args: ("legacy", "source"))
    assert plugin._load_pandascore_cs2_card({"pandaScoreCs2AutoFollow": "false"}, device, timezone.utc, now) == (
        "legacy",
        "source",
    )


def test_shared_retry_adapter_is_replaced_with_owned_no_retry_transport(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from plugins.sports_dashboard import cs2_follow

    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    plugin, device, shared = loader(monkeypatch, tmp_path, {})
    shared._inkypi_adapter_retries = True
    isolated = FeedSession({"running": [], "upcoming": [cs_match(now)], "past": []})
    lifecycle = []

    @contextmanager
    def owned():
        lifecycle.append("open")
        try:
            yield SimpleNamespace(session=isolated)
        finally:
            lifecycle.append("close")

    monkeypatch.setattr(cs2_follow, "create_single_attempt_http_client", owned)
    card, _ = plugin._load_pandascore_cs2_card({"pandaScoreCs2HltvLogos": "false"}, device, timezone.utc, now)
    assert card and lifecycle == ["open", "close"]
    assert not shared.calls and len(isolated.calls) == 4
