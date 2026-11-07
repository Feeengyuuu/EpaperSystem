"""UFC, ATP/WTA tennis and MotoGP for the sports page's lower panel: their parsers, which card or
weekend each shows, how it ranks against the other leagues, and the cached loader."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.sports_dashboard import event_ranking, extra_feeds, motogp, tennis, ufc  # noqa: E402

NOW = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
FLAG = "https://a.espncdn.com/i/teamlogos/countries/500/{}.png"


def _fighter(fid, name, record, country, code, winner=False, order=1):
    return {"id": fid, "order": order, "winner": winner, "records": [{"name": "overall", "summary": record}],
            "athlete": {"fullName": name, "displayName": name, "shortName": name.split()[0][0] + ". " + name.split()[-1],
                        "flag": {"href": FLAG.format(code), "alt": country}}}


def _fight(weight, date, rounds, state, a, b, period=0, clock="-", linescores=False):
    status = {"period": period, "displayClock": clock,
              "type": {"state": state, "completed": state == "post", "detail": "Final" if state == "post" else "Sat"}}
    if linescores:
        for fighter in (a, b):
            fighter["linescores"] = [{"value": 87.0}]
    return {"id": f"{weight}-{date}", "date": date, "type": {"abbreviation": weight}, "format": {"regulation": {"periods": rounds}},
            "status": status, "competitors": [a, b], "broadcasts": [{"market": "national", "names": ["Paramount+"]}]}


UFC = {"events": [
    {"id": "1", "name": "UFC Fight Night: Moicano vs. Nolan", "date": "2026-10-31T21:00Z",
     "venues": [{"fullName": "Meta APEX", "address": {"city": "Las Vegas", "country": "USA"}}],
     "competitions": [
         _fight("Welterweight", "2026-10-31T21:00Z", 3, "post", _fighter("11", "Randy Brown", "19-7-0", "Jamaica", "jam"),
                _fighter("12", "Jacobe Smith", "11-1-0", "USA", "usa", winner=True), 3, "5:00", linescores=True),
         _fight("Lightweight", "2026-11-01T00:00Z", 5, "post", _fighter("13", "Renato Moicano", "20-7-1", "Brazil", "bra"),
                _fighter("14", "Tom Nolan", "9-1-0", "Australia", "aus", winner=True), 3, "1:28")]},
    {"id": "2", "name": "UFC 334: Gane vs. Hokit", "date": "2026-11-14T22:00Z",
     "venues": [{"fullName": "Madison Square Garden", "address": {"city": "New York", "country": "USA"}}],
     "competitions": [
         _fight("W Strawweight", "2026-11-15T01:00Z", 3, "pre", _fighter("21", "Tatiana Suarez", "13-1-0", "USA", "usa"),
                _fighter("22", "Virna Jandiroba", "23-4-0", "Brazil", "bra")),
         _fight("Heavyweight", "2026-11-15T01:00Z", 5, "pre", _fighter("23", "Ciryl Gane", "13-3-0", "France", "fra"),
                _fighter("24", "Josh Hokit", "8-0-0", "USA", "usa"))]}]}


def test_ufc_cards_lead_with_their_main_event_and_read_results():
    events = ufc.parse_scoreboard(UFC)["events"]

    finished, ppv = events
    assert (finished["label"], finished["headline"], finished["numbered"]) == ("UFC Fight Night", "Moicano vs. Nolan", False)
    assert (ppv["label"], ppv["numbered"], ppv["city"]) == ("UFC 334", True, "New York")
    # The main event is the card's last bout; it comes first, with its weight class in Chinese.
    main, co_main = ppv["fights"][:2]
    assert (main["weight"], main["weight_zh"], main["rounds"]) == ("Heavyweight", "重量级", 5)
    assert co_main["weight_zh"] == "女子草量级"
    assert [fighter["name"] for fighter in main["fighters"]] == ["Ciryl Gane", "Josh Hokit"]
    assert main["fighters"][0]["record"] == "13-3-0" and main["fighters"][0]["flag"].endswith("/fra.png")
    assert main["fighters"][0]["headshot"] == "https://a.espncdn.com/i/headshots/mma/players/full/23.png"
    # A finish names its round and time; a bout that went the distance is a decision.
    finish, decision = finished["fights"]
    assert ufc.result_label(finish) == "R3 1:28" and finish["fighters"][1]["winner"]
    assert ufc.result_label(decision) == "判定胜"


def test_the_ufc_card_shows_the_fight_night_in_progress_then_the_result_then_the_next_card():
    events = ufc.parse_scoreboard(UFC)["events"]

    night = datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc)
    events_live = ufc.parse_scoreboard(UFC)["events"]
    events_live[0]["fights"][0]["state"] = "in"
    assert ufc.build_card(events_live, night)["status"] == "LIVE"
    after = ufc.build_card(events, datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc))
    assert (after["status"], after["main"]["label"]) == ("RECENT", "UFC Fight Night")
    later = ufc.build_card(events, NOW)
    assert (later["status"], later["main"]["label"]) == ("NEXT", "UFC 334")
    assert ufc.build_card([], NOW) is None


def test_a_numbered_ufc_card_takes_the_panel_on_fight_night_over_a_regular_game():
    events = ufc.parse_scoreboard(UFC)["events"]
    fight_day = datetime(2026, 11, 14, 22, 0, tzinfo=timezone.utc)
    card = ufc.build_card(events, fight_day)
    candidate = ufc.candidate(card, fight_day)

    assert candidate["kind"] == "extra" and candidate["key"] == "UFC"
    regular = event_ranking.score("scheduled", fight_day + timedelta(hours=2), fight_day, "C")
    assert candidate["score"] > regular
    # A week out it is a token entry, not a rival to tonight's games.
    week_out = ufc.candidate(ufc.build_card(events, NOW - timedelta(days=6)), NOW - timedelta(days=6))
    assert week_out["score"] < event_ranking.score("scheduled", NOW, NOW - timedelta(hours=6), "C")


def _player(pid, name, country, code, sets=(), seed=None, winner=False, order=1):
    return {"id": pid, "order": order, "winner": winner, "curatedRank": {"current": seed} if seed else None,
            "linescores": [{"value": float(games), **({"tiebreak": tb} if tb else {})} for games, tb in sets],
            "athlete": {"displayName": name, "shortName": name.split()[0][0] + ". " + name.split()[-1],
                        "flag": {"href": FLAG.format(code), "alt": country}}}


def _match(round_name, date, state, a, b, detail="", note=""):
    return {"id": f"{round_name}-{a['id']}", "date": date, "round": {"displayName": round_name},
            "status": {"type": {"state": state, "detail": detail or ("Final" if state == "post" else "Sat")}},
            "notes": [{"text": note, "type": "event"}] if note else [], "competitors": [a, b]}


ATP = {"events": [{"id": "13-2026", "name": "Rolex Paris Masters", "date": "2026-10-31T04:00Z", "endDate": "2026-11-09T04:59Z",
                   "major": False, "venue": {"displayName": "Paris, France"},
                   "groupings": [{"grouping": {"displayName": "Men's Singles"}, "competitions": [
                       _match("Qualifying Final", "2026-11-01T10:00Z", "post", _player("1", "Q One", "Spain", "esp"),
                              _player("2", "Q Two", "Italy", "ita")),
                       _match("Quarterfinal", "2026-11-06T22:10Z", "in", _player("3", "Nuno Borges", "Portugal", "por", [(5, None)]),
                              _player("4", "Casper Ruud", "Norway", "nor", [(6, None)]), "1st Set"),
                       _match("Quarterfinal", "2026-11-06T18:00Z", "post",
                              _player("5", "Carlos Alcaraz", "Spain", "esp", [(6, None), (7, 7)], seed=2, winner=True),
                              _player("6", "Ugo Humbert", "France", "fra", [(3, None), (6, 4)])),
                       _match("Semifinal", "2026-11-07T13:30Z", "pre", _player("7", "Ben Shelton", "USA", "usa", seed=3),
                              _player("8", "Luca Van Assche", "France", "fra"))]},
                                 {"grouping": {"displayName": "Men's Doubles"}, "competitions": []}]}]}


def test_a_tennis_tournament_keeps_its_main_draw_singles_with_sets_and_seeds():
    data = tennis.parse_scoreboard(ATP, "ATP")
    event = data["tournaments"][0]

    assert (event["name"], event["name_zh"], event["city"], event["tier"]) == ("Rolex Paris Masters", "巴黎大师赛", "Paris", "B")
    rounds = [match["round"] for match in event["matches"]]
    assert "Qualifying Final" not in rounds and rounds[0] == "Quarterfinal"
    alcaraz = next(match for match in event["matches"] if match["players"][0]["name"] == "Carlos Alcaraz")
    assert alcaraz["round_zh"] == "1/4决赛" and alcaraz["players"][0]["seed"] == 2
    assert alcaraz["players"][0]["sets"] == [[6, None], [7, 7]] and alcaraz["players"][0]["winner"]
    assert tennis.tournament_tier({"name": "Australian Open", "major": True}) == "A"
    assert tennis.tournament_tier({"name": "Nitto ATP Finals"}) == "A"
    assert tennis.tournament_tier({"name": "Chennai Open"}) == "C"


def test_the_tennis_card_shows_live_matches_first_then_results_then_the_next_ones():
    card = tennis.build_card({"ATP": tennis.parse_scoreboard(ATP, "ATP")}, NOW)

    assert card["status"] == "LIVE"
    shown = card["tours"]["ATP"]["shown"]
    assert [match["state"] for match in shown] == ["in", "post", "pre"]
    assert tennis.build_card({"ATP": {"tournaments": []}}, NOW) is None


def test_an_early_round_at_a_small_event_is_not_a_live_game():
    small = tennis.parse_scoreboard(ATP, "ATP")
    small["tournaments"][0]["name"] = "Chennai Open"
    small["tournaments"][0]["tier"] = "C"
    for match in small["tournaments"][0]["matches"]:
        match["round"] = "Round 1"
    card = tennis.build_card({"ATP": small}, NOW)
    candidate = tennis.candidate(card, NOW)

    assert candidate["score"] < event_ranking.LIVE_POINTS
    paris = tennis.candidate(tennis.build_card({"ATP": tennis.parse_scoreboard(ATP, "ATP")}, NOW), NOW)
    assert paris["score"] >= event_ranking.LIVE_POINTS


SEASONS = [{"id": "s25", "year": 2025, "current": False}, {"id": "s26", "year": 2026, "current": True}]
EVENTS = [
    {"id": "mal", "name": "GRAND PRIX OF MALAYSIA", "sponsored_name": "PETRONAS GRAND PRIX OF MALAYSIA", "short_name": "MAL",
     "date_start": "2026-10-30", "date_end": "2026-11-01", "status": "FINISHED", "test": False,
     "circuit": {"name": "Petronas Sepang International Circuit"}, "country": {"iso": "MY", "name": "Malaysia"}},
    {"id": "qat", "name": "GRAND PRIX OF QATAR", "sponsored_name": "QATAR AIRWAYS GRAND PRIX OF QATAR", "short_name": "QAT",
     "date_start": "2026-11-06", "date_end": "2026-11-08", "status": "CURRENT", "test": False,
     "circuit": {"name": "Lusail International Circuit"}, "country": {"iso": "QA", "name": "Qatar"}},
    {"id": "test", "name": "VALENCIA TEST", "short_name": "VA1", "date_start": "2026-12-01", "date_end": "2026-12-01",
     "status": "NOT-STARTED", "test": True, "circuit": {"name": "Ricardo Tormo"}, "country": {"iso": "ES"}}]
SESSIONS = [{"id": f"q-{kind}{number or ''}", "type": kind, "number": number, "date": date, "status": status}
            for kind, number, date, status in (
                ("FP", 1, "2026-11-06T13:45:00+00:00", "FINISHED"), ("PR", None, "2026-11-06T18:00:00+00:00", "FINISHED"),
                ("Q", 2, "2026-11-07T14:15:00+00:00", "FINISHED"), ("SPR", None, "2026-11-07T18:30:00+00:00", "NOT-STARTED"),
                ("RAC", None, "2026-11-08T17:00:00+00:00", "NOT-STARTED"))]


def _rider(pos, number, name, iso, team, constructor, points, time=None, gap=None):
    row = {"position": pos, "points": points, "rider": {"full_name": name, "number": number, "country": {"iso": iso}},
           "team": {"name": team}, "constructor": {"name": constructor}}
    if time is not None:
        row.update(time=time, gap={"first": gap or "0.000"}, status="INSTND")
    return row


CLASSIFICATION = {"classification": [
    _rider(1, 93, "Marc Marquez", "ES", "Ducati Lenovo Team", "Ducati", 25, "40:14.236"),
    _rider(2, 37, "Pedro Acosta", "ES", "Red Bull KTM Factory Racing", "KTM", 20, "40:15.101", "0.865"),
    _rider(3, 73, "Alex Marquez", "ES", "BK8 Gresini Racing MotoGP", "Ducati", 16, "40:17.002", "2.766")]}
STANDINGS = {"classification": [_rider(1, 93, "Marc Marquez", "ES", "Ducati Lenovo Team", "Ducati", 399),
                                _rider(2, 89, "Jorge Martin", "ES", "Aprilia Racing", "Aprilia", 301)]}


def test_motogp_reads_the_weekend_its_sessions_the_last_race_and_the_standings():
    weekend = motogp.pick_events(EVENTS, NOW)
    assert (weekend["current"]["short"], weekend["current"]["name_zh"], weekend["last"]["short"]) == ("QAT", "卡塔尔大奖赛", "MAL")
    sessions = motogp.normalize_sessions(SESSIONS)
    assert [session["label"] for session in sessions] == ["FP1", "练习", "Q2", "冲刺赛", "正赛"]
    result = motogp.normalize_classification(CLASSIFICATION)
    assert [(row["pos"], row["number"], row["gap"]) for row in result] == [(1, 93, ""), (2, 37, "+0.865"), (3, 73, "+2.766")]
    standings = motogp.normalize_standings(STANDINGS)
    assert standings[0] == {"pos": 1, "number": 93, "name": "Marc Marquez", "iso": "ES", "team": "Ducati Lenovo Team",
                            "constructor": "Ducati", "points": 399}


def test_the_motogp_weekend_ranks_its_race_above_its_practice():
    data = {"current": motogp.pick_events(EVENTS, NOW)["current"], "sessions": motogp.normalize_sessions(SESSIONS),
            "result": {"session": "RAC", "event": "MAL", "rows": motogp.normalize_classification(CLASSIFICATION)},
            "standings": motogp.normalize_standings(STANDINGS)}
    card = motogp.build_card(data, NOW)
    assert card["status"] == "NEXT" and card["next_session"]["label"] == "冲刺赛"

    during_race = datetime(2026, 11, 8, 17, 20, tzinfo=timezone.utc)
    live = motogp.build_card(data, during_race)
    assert live["status"] == "LIVE" and live["live_session"]["label"] == "正赛"
    assert motogp.candidate(live, during_race)["score"] == event_ranking.score("live", None, during_race, "A")
    assert motogp.build_card({}, NOW) is None


class _Plugin:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path

    def _sports_dashboard_cache_dir(self):
        return self.tmp_path

    @staticmethod
    def _read_json_file(path):
        import json
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write_json_file(path, payload):
        import json
        Path(path).write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _provider_cache_only(settings):
        return bool((settings or {}).get("_theme_render_only"))

    @staticmethod
    def _force_refresh_requested(settings):
        return False

    @staticmethod
    def _cache_is_fresh_seconds(cache, seconds, now):
        fetched = cache.get("fetched_at")
        return bool(fetched) and now - datetime.fromisoformat(fetched) <= timedelta(seconds=seconds)


def test_the_loader_keeps_a_fresh_reading_and_falls_back_to_it_when_the_feed_fails(tmp_path, monkeypatch):
    import contextlib

    monkeypatch.setattr(extra_feeds, "create_single_attempt_http_client", lambda: contextlib.nullcontext(None))
    plugin = _Plugin(tmp_path)
    calls = []

    def fetch(client, deadline):
        calls.append(1)
        return {"value": len(calls)}

    data, state = extra_feeds.load(plugin, {}, "demo", "v1", fetch, 600, NOW, label="DEMO")
    assert (data, state) == ({"value": 1}, "DEMO LIVE")
    assert extra_feeds.load(plugin, {}, "demo", "v1", fetch, 600, NOW, label="DEMO") == ({"value": 1}, "DEMO CACHE")

    def broken(client, deadline):
        raise RuntimeError("down")

    later = NOW + timedelta(hours=1)
    assert extra_feeds.load(plugin, {}, "demo", "v1", broken, 600, later, label="DEMO") == ({"value": 1}, "DEMO STALE")
    assert extra_feeds.load(plugin, {"_theme_render_only": True}, "other", "v1", fetch, 600, NOW, label="DEMO") == (
        {}, "DEMO NO DATA")
    # With nothing kept, a failing feed gives no data and the panel stays out.
    assert extra_feeds.load(plugin, {}, "third", "v1", broken, 600, NOW, label="DEMO") == ({}, "DEMO NO DATA")


class _Response:
    def __init__(self, data):
        self.data = data


class _TourClient:
    """ATP's site search, WTA's player search and a WTA player page, as the tours answer them."""

    def __init__(self):
        self.asked = []

    def request_json(self, method, url, **kwargs):
        self.asked.append(url)
        if "atptour.com" in url:
            return _Response({"Players": [{"FirstName": "Luca", "LastName": "Van Assche",
                                           "PlayerHeadshotUrl": "https://www.atptour.com/-/media/alias/player-headshot/V0DZ"}]})
        return _Response({"content": [{"id": 330332, "fullName": "Alexandra Eala"}]})

    def request_text(self, method, url, **kwargs):
        self.asked.append(url)
        return _Response('<img src="https://photoresources.wtatennis.com/photo-resources/2026/04/08/c67/Eala-Torso_330332.png'
                         '?width=790&height=740">')


def test_a_player_without_an_espn_photo_gets_the_tours_official_one(tmp_path, monkeypatch):
    from plugins.sports_dashboard import tennis_photos

    client = _TourClient()
    import contextlib

    monkeypatch.setattr(extra_feeds, "create_single_attempt_http_client", lambda: contextlib.nullcontext(client))
    plugin = _Plugin(tmp_path)
    players = [{"name": "Luca Van Assche", "tour": "ATP"}, {"name": "Alexandra Eala", "tour": "WTA"},
               {"name": "Nobody Known", "tour": "ATP"}]

    found = tennis_photos.official_photos(plugin, {}, players, NOW)

    assert found["ATP:luca van assche"] == "https://www.atptour.com/-/media/alias/player-headshot/V0DZ"
    assert found["WTA:alexandra eala"].startswith("https://photoresources.wtatennis.com/") \
        and found["WTA:alexandra eala"].endswith("Eala-Torso_330332.png?width=480&height=450")
    assert found["ATP:nobody known"] == ""
    # Kept: the next refresh asks nothing; a cache-only redraw reads the same answers.
    client.asked.clear()
    assert tennis_photos.official_photos(plugin, {"_theme_render_only": True}, players, NOW) == found
    assert tennis_photos.official_photos(plugin, {}, players, NOW) == found and client.asked == []


def test_a_wta_player_without_a_cut_out_gets_the_tours_plain_headshot(tmp_path, monkeypatch):
    from plugins.sports_dashboard import tennis_photos
    import contextlib

    class Client(_TourClient):
        def request_json(self, method, url, **kwargs):
            self.asked.append((url, kwargs.get("params")))
            return _Response({"content": [{"id": 323966, "fullName": "Claire Liu"}]})

        def request_text(self, method, url, **kwargs):
            return _Response('<img src="https://wtafiles.blob.core.windows.net/images/headshots/323966.jpg">')

    client = Client()
    monkeypatch.setattr(extra_feeds, "create_single_attempt_http_client", lambda: contextlib.nullcontext(client))

    found = tennis_photos.official_photos(_Plugin(tmp_path), {}, [{"name": "Claire Liu", "tour": "WTA"}], NOW)

    assert found == {"WTA:claire liu": "https://wtafiles.blob.core.windows.net/images/headshots/323966.jpg"}
    # The tour's search matches any part of a name: it is asked for the full name.
    assert client.asked[0][1]["name"] == "Claire Liu"
