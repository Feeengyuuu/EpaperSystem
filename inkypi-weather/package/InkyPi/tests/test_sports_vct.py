"""VALORANT Champions Tour in the esports sidebar: Riot's Valorant schedule, tiers, ranking against the
other esports, its own live state and its header."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

import plugins.sports_dashboard.sports_dashboard as sports_dashboard_module
from plugins.sports_dashboard import event_ranking
from plugins.sports_dashboard.common import (
    _ACTIVE_COLORS,
    DAY_COLORS,
    DEEP_NIGHT_COLORS,
    DEFAULT_VCT_LEAGUE_IDS,
    LOCAL_TEAM_LOGO_DIR,
    LOCAL_VCT_LOGO_DIR,
    LOCAL_VCT_WORDMARK_DIR,
    VCT_LEAGUE_LABELS,
    VCT_LIVE_STATE_VERSION,
    VCT_WORDMARKS,
)
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from tests.test_lol_worlds import _FakeResponse, _match, _pinned_worlds_card, _upcoming_cs2_card
from tests.test_sports_dashboard import _plugin

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _riot_event(match_id, start, state, slug, block, teams, wins=(None, None), best_of=3):
    """One event as Riot's Valorant esports getSchedule returns it."""
    return {
        "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state": state,
        "type": "match",
        "blockName": block,
        "league": {"name": slug.replace("_", " ").title(), "slug": slug,
                   "image": "http://static.lolesports.com/leagues/spark.png", "region": "INTERNATIONAL"},
        "tournament": {"split": {"name": "Champions"}, "season": {"name": "valorant_champions_tour_2026"}},
        "match": {
            "id": match_id,
            "flags": [],
            "teams": [
                {"name": name, "code": code, "image": f"http://static.lolesports.com/teams/{code}.png",
                 "result": None if win is None else {"outcome": None, "gameWins": win}, "record": None}
                for (code, name), win in zip(teams, wins)
            ],
            "strategy": {"type": "bestOf", "count": best_of},
        },
    }


def _schedule():
    return {"data": {"schedule": {"pages": {"older": None, "newer": None}, "events": [
        _riot_event("v-1", NOW - timedelta(days=1), "completed", "champions", "Playoffs",
                    (("G2", "G2 Esports"), ("100T", "100 Thieves")), (1, 2)),
        _riot_event("v-2", NOW + timedelta(hours=20), "unstarted", "champions", "Finals",
                    (("VIT", "Team Vitality"), ("LOUD", "LOUD")), best_of=5),
    ]}}}


def _vct_card(events, now=NOW):
    return {"league_key": "VCT", "selected": SportsDashboard._select_lck_events(events, now),
            "source_state": "VCT LIVE DATA", "priority": 4}


def _vct_match(match_id, start, state, slug, block, wins=(None, None)):
    return dict(_match(match_id, start, state, "VIT", "LOUD", wins, block=block), league_key="VCT", league_slug=slug)


def test_vct_schedule_comes_from_riots_valorant_api_in_one_request(monkeypatch):
    plugin = _plugin()
    calls = []

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            calls.append((url, headers))
            return _FakeResponse(_schedule())

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    events = plugin._fetch_vct_events({}, timezone.utc)

    # One request for every top-tier league, with the lolesports key; Valorant has no getLive.
    assert len(calls) == 1
    url, headers = calls[0]
    assert url.startswith("https://esports-api.service.valorantesports.com/persisted/val/getSchedule?")
    assert "sport=val" in url and f"leagueId={','.join(DEFAULT_VCT_LEAGUE_IDS)}" in url
    assert headers["x-api-key"]
    assert [event["event_id"] for event in events] == ["v-1", "v-2"]
    assert {event["league_key"] for event in events} == {"VCT"}
    assert [event["league_slug"] for event in events] == ["champions", "champions"]
    # Team logos come through Riot's resizer, as LoL's do.
    assert "static.lolesports.com/teams/G2.png" in unquote(events[0]["team_a_logo"])
    assert (events[0]["wins_a"], events[0]["wins_b"]) == (1, 2)


def test_a_failing_vct_schedule_leaves_no_card_material(monkeypatch):
    plugin = _plugin()

    def fail(*_args, **_kwargs):
        raise ConnectionError("down")

    plugin._fetch_vct_events = fail
    assert plugin._load_vct_events({}, timezone.utc) == ([], "VCT NO DATA")


def test_vct_tiers_follow_the_lol_rules():
    assert event_ranking.vct_tier("champions", "Groups") == "S"
    assert event_ranking.vct_tier("vct_masters", "Swiss Stage") == "A"
    assert event_ranking.vct_tier("game_changers_championship", "Group Stage") == "A"
    assert event_ranking.vct_tier("vct_americas", "Playoffs") == "A"
    assert event_ranking.vct_tier("vct_emea", "Grand Finals") == "A"
    assert event_ranking.vct_tier("vct_pacific", "Week 3") == "C"
    card = _vct_card([_vct_match("f", NOW + timedelta(hours=3), "unstarted", "champions", "Finals")])
    assert SportsDashboard._lol_card_tier(card) == "S"


def test_out_of_season_vct_is_no_candidate():
    results_only = _vct_card([_vct_match("old", NOW - timedelta(days=30), "completed", "champions", "Finals", (3, 1))])
    assert SportsDashboard._lol_sidebar_candidate_phase(results_only) is None


def test_a_champions_final_tomorrow_takes_the_sidebar_from_a_worlds_rest_day():
    worlds_next = _match("w", NOW + timedelta(days=4), "unstarted", "GEN", "BLG", block="Final")
    worlds = {**_pinned_worlds_card(SportsDashboard._select_worlds_events([worlds_next], NOW)),
              "in_window": True, "sidebar_pinned": False, "pinned_until": None}
    vct = _vct_card([_vct_match("f", NOW + timedelta(hours=20), "unstarted", "champions", "Finals")])

    choice = SportsDashboard._select_right_esports_sidebar([worlds, vct], None, "", NOW)

    assert choice["kind"] == "lol" and choice["choice"]["league_key"] == "VCT"


def test_a_live_cs2_match_outranks_an_upcoming_regional_vct_match():
    vct = _vct_card([_vct_match("r", NOW + timedelta(hours=4), "unstarted", "vct_emea", "Week 2")])
    cs2 = _upcoming_cs2_card(NOW - timedelta(minutes=20))
    cs2["primary"].update(status="LIVE", live=[cs2["primary"]["main"]], upcoming=[])

    choice = SportsDashboard._select_right_esports_sidebar([vct], cs2, "PANDASCORE LIVE", NOW)

    assert choice["kind"] == "valve"


def test_live_vct_keeps_its_own_live_state(tmp_path):
    plugin = _plugin()
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    live = _vct_match("l", NOW - timedelta(minutes=30), "inprogress", "champions", "Finals", (1, 0))

    plugin._write_lol_live_state(SportsDashboard._select_lck_events([live], NOW), NOW, "VCT LIVE DATA", league_key="VCT")

    state = json.loads((tmp_path / "vct_live_state.json").read_text(encoding="utf-8"))
    assert state["version"] == VCT_LIVE_STATE_VERSION and state["league_key"] == "VCT" and state["has_live"]
    # LPL's live state is untouched.
    assert not (tmp_path / "lpl_live_state.json").exists()


def test_the_sidebar_override_can_force_vct():
    assert SportsDashboard._lol_esports_sidebar_override({"lolEsportsSidebarOverride": "vct"}) == "VCT"


def _draw_vct_sidebar(plugin, monkeypatch, slug):
    """Draw the VCT sidebar by day and night for a league; (logos loaded, texts fitted)."""
    marks, texts = [], []
    original = plugin._load_local_logo
    monkeypatch.setattr(plugin, "_load_local_logo", lambda path, *args, **kwargs: marks.append(
        "/".join(Path(path).parts[-2:])) or original(path, *args, **kwargs))
    events = [_vct_match("n", NOW + timedelta(hours=5), "unstarted", slug, "Playoffs"),
              _vct_match("p", NOW - timedelta(days=1), "completed", slug, "Playoffs", (2, 1))]
    selected = SportsDashboard._select_lck_events(events, NOW)
    fit_text = plugin._fit_text
    monkeypatch.setattr(plugin, "_fit_text", lambda draw, text, *args, **kwargs: texts.append(text)
                        or fit_text(draw, text, *args, **kwargs))
    for colours in (DAY_COLORS, DEEP_NIGHT_COLORS):
        token = _ACTIVE_COLORS.set(colours)
        try:
            image = Image.new("RGB", (800, 480), colours["paper"])
            plugin._draw_lpl_sidebar(image, 555, selected, "VCT LIVE DATA", NOW, league_key="VCT")
        finally:
            _ACTIVE_COLORS.reset(token)
        assert image.getbbox() is not None
    return marks, texts


def test_the_vct_header_names_the_league_by_its_official_wordmark(monkeypatch):
    # A partner league: its mark, then its wordmark; no typed name.
    marks, texts = _draw_vct_sidebar(_plugin(), monkeypatch, "vct_americas")
    assert "vct/vct_americas.png" in marks and "wordmarks/vct_americas.png" in marks
    assert "VCT" not in texts and "Americas" not in texts
    # An international event: its coloured lockup alone.
    marks, _texts = _draw_vct_sidebar(_plugin(), monkeypatch, "champions")
    assert "wordmarks/champions.png" in marks and "vct/champions.png" not in marks
    config = SportsDashboard._lol_sidebar_config("VCT")
    assert (config["accent"], config["empty_upcoming"]) == ("vct_accent", "No more VCT schedule")


def test_a_league_without_a_wordmark_is_named_in_type_by_the_champions_spark(monkeypatch):
    marks, texts = _draw_vct_sidebar(_plugin(), monkeypatch, "vct_someday")
    assert "vct/champions.png" in marks and "VCT" in texts


def test_every_vct_mark_is_bundled_with_its_source():
    records = json.loads((Path(LOCAL_TEAM_LOGO_DIR) / "logos.source.json").read_text(encoding="utf-8"))["logos"]
    assert set(VCT_LEAGUE_LABELS) == {
        "champions", "vct_masters", "game_changers_championship", "vct_americas", "vct_emea", "vct_pacific", "vct_china"}
    for slug in VCT_LEAGUE_LABELS:
        path = Path(LOCAL_VCT_LOGO_DIR) / f"{slug}.png"
        with Image.open(path) as mark:
            assert mark.mode == "RGBA" and max(mark.size) == 128, slug
        record = records[f"vct/{slug}"]
        assert record["source"].startswith("https://static.lolesports.com/leagues/"), slug
        assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest(), slug


def test_every_vct_wordmark_is_bundled_with_its_source():
    records = json.loads((Path(LOCAL_TEAM_LOGO_DIR) / "logos.source.json").read_text(encoding="utf-8"))["logos"]
    assert set(VCT_WORDMARKS) == set(VCT_LEAGUE_LABELS)
    for slug in VCT_WORDMARKS:
        path = Path(LOCAL_VCT_WORDMARK_DIR) / f"{slug}.png"
        with Image.open(path) as wordmark:
            assert wordmark.mode == "RGBA" and wordmark.height == 96, slug
        record = records[f"vct/wordmarks/{slug}"]
        assert record["source"].startswith("https://liquipedia.net/commons/images/"), slug
        assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest(), slug


def test_the_empty_upcoming_line_names_the_league_by_its_logo_and_fits_the_sidebar(monkeypatch):
    plugin = _plugin()
    drawn, pasted = [], []
    worlds = plugin._lol_sidebar_config("WORLDS")
    real_wordmark, real_logo = plugin._draw_worlds_header_wordmark, plugin._draw_lpl_logo
    monkeypatch.setattr(plugin, "_draw_worlds_header_wordmark",
                        lambda image, path, box: pasted.append(("wordmark", box)) or real_wordmark(image, path, box))
    monkeypatch.setattr(plugin, "_draw_lpl_logo", lambda image, draw, x, y, w, h, **options: pasted.append(
        ("logo", (x, y, x + w, y + h))) or real_logo(image, draw, x, y, w, h, **options))

    def line(config, now=None, right=None):
        image = Image.new("RGB", (224, 60), (255, 251, 240))
        draw = sports_dashboard_module.ImageDraw.Draw(image)
        real_text = draw.text
        draw.text = lambda xy, text, **options: drawn.append(text) or real_text(xy, text, **options)
        plugin._draw_empty_upcoming_line(image, draw, 18, 20, config, now, right)
        return image

    # Worlds reads "No more [WORLDS 26] schedule": the edition wordmark its header wears, inside the sidebar.
    line(worlds, datetime(2026, 11, 10, tzinfo=timezone.utc), 212)
    assert drawn == ["No more ", " schedule"] and pasted[0][0] == "wordmark"
    box = pasted[0][1]
    assert 18 < box[0] < box[2] < 212 and 10 <= box[3] - box[1] <= 18
    # A league with a mark of its own is named by it; one whose logo file is missing keeps its name in type.
    drawn.clear(), pasted.clear()
    image = line(plugin._lol_sidebar_config("LPL"))
    assert drawn == ["No more ", " schedule"] and pasted[0][0] == "logo"
    assert len(image.getcolors(100000)) > 3
    drawn.clear(), pasted.clear()
    line({**worlds, "logo_path": "missing.png"})
    assert drawn == ["No more Worlds schedule"] and not pasted
