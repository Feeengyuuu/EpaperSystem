"""The pro tracker behind LoLInfo: roster, random pick, boards, HLTV parsing and both pages."""

import json
import sys
import time
import urllib.parse
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.lol_info import bo3_stats, hltv_players, pro_render, pro_tracker  # noqa: E402
from plugins.lol_info import lol_info as lol_info_module  # noqa: E402
from plugins.lol_info.lol_info import LoLInfo  # noqa: E402
from plugins.lol_info.pro_roster import LOL_ROSTER, TRACKED_LOL_PLAYERS, tracked_lol_accounts  # noqa: E402


class FakeDevice:
    def __init__(self, key="riot-key"):
        self.key = key

    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal"}
        return values if key is None else values.get(key, default)

    def load_env_key(self, key):
        return self.key if key == "Riot_KEY" else ""


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            error = RuntimeError(f"HTTP {self.status_code}")
            error.response = self
            raise error


@pytest.fixture
def plugin(tmp_path):
    instance = LoLInfo({"id": "lol_info"})
    instance._cache_dir = lambda: tmp_path
    instance._image_from_url = lambda url, label="": None
    return instance


def test_recorded_accounts_lead_and_the_roster_fills_twenty_labelled_pros():
    saved = [{"label": "Faker", "gameName": "Hide on bush", "tagLine": "KR1"},
             {"label": "Someone", "gameName": "Smurf", "tagLine": "KR9"}]

    accounts = tracked_lol_accounts(saved)

    assert len(accounts) == TRACKED_LOL_PLAYERS
    assert [account["label"] for account in accounts[:2]] == ["Faker", "Someone"]
    assert (accounts[0]["team"], accounts[0]["role"]) == ("T1", "MID")
    assert accounts[1]["team"] == "" and len({account["label"] for account in accounts}) == 20
    assert all(account["team"] and account["role"] for account in accounts[2:])
    assert len(LOL_ROSTER) == 20


def test_a_saved_account_is_labelled_with_the_player_who_owns_it():
    # The owner recorded Knight's account under Bin; the roster knows whose it is and adds Bin's own.
    saved = [{"label": "Bin", "gameName": "BLG 온", "tagLine": "KR1", "platformRoute": "kr"}]

    accounts = tracked_lol_accounts(saved)

    assert (accounts[0]["label"], accounts[0]["team"], accounts[0]["role"]) == ("Knight", "BLG", "MID")
    bin_account = next(account for account in accounts if account["label"] == "Bin")
    assert (bin_account["gameName"], bin_account["tagLine"]) == ("빈 스토리", "KR1")
    assert len(accounts) == TRACKED_LOL_PLAYERS


def test_a_renamed_account_is_marked_missing_and_the_next_player_is_shown(plugin, monkeypatch):
    settings = {"includeCs2": "false"}
    shown = []
    gone = {}

    def fetch(effective, _device):
        if not gone:
            gone["id"] = effective["_proAccountId"]
            raise lol_info_module.RiotAccountMissing(f"{effective['gameName']}#{effective['tagLine']}")
        shown.append(effective["_proAccountId"])
        return plugin._sample_payload()

    monkeypatch.setattr(plugin, "_fetch_dashboard_data", fetch)
    monkeypatch.setattr(plugin, "_lol_photo", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(plugin, "_write_context", lambda *_args, **_kwargs: None)
    # The bag in roster order, so the first pick is a pro with a single account (Faker).
    monkeypatch.setattr(pro_tracker.random, "shuffle", lambda items: None)

    image = plugin.generate_image(settings, FakeDevice(key=""))

    assert image.size == (800, 480) and len(shown) == 1 and shown[0] != gone["id"]
    board = plugin._lol_board(settings, FakeDevice(key=""), refresh=False)
    assert next(row for row in board if row["id"] == gone["id"])["missing"]
    # The bag passes over it until the ladder refresh finds the account again.
    assert all(plugin._pick(settings)["account"]["id"] != gone["id"] for _ in range(40))


def test_the_pick_is_a_shuffle_bag_over_lol_and_cs2(plugin):
    settings = {}
    pool = plugin._tracked_pool(settings)
    assert len(pool) == 40 and {item["kind"] for item in pool} == {"lol", "cs2"}

    first_round = [plugin._pick(settings)["id"] for _ in range(40)]
    second = plugin._pick(settings)

    assert sorted(first_round) == sorted(item["id"] for item in pool)
    assert second["id"] != first_round[-1] and second["position"] == 1
    assert plugin._pick(settings, advance=False)["id"] == second["id"]
    assert [item["kind"] for item in plugin._tracked_pool({"includeCs2": "false"})] == ["lol"] * 20


def test_the_ladder_rereads_only_a_few_stale_accounts_and_sorts_by_rank(plugin, monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if "/accounts/by-riot-id/" in url:
            name = url.split("/by-riot-id/")[1].split("/")[0]
            return Response({"puuid": f"p-{name}"} if "Smurf" not in url else {}, 200 if "Smurf" not in url else 404)
        lp = 100 + len(calls)
        return Response([{"queueType": "RANKED_SOLO_5x5", "tier": "CHALLENGER", "rank": "I", "leaguePoints": lp,
                          "wins": 10, "losses": 5}])

    monkeypatch.setattr(pro_tracker, "get_http_session", lambda: type("S", (), {"get": staticmethod(fake_get)})())
    settings = {"proAccounts": "Faker|Hide on bush|KR1|kr|asia\nNobody|Smurf|KR9|kr|asia", "includeCs2": "false"}
    featured_id = plugin._tracked_lol(settings)[0]["id"]
    ranked = {"tier": "GRANDMASTER", "rank": "I", "leaguePoints": 900, "wins": 1, "losses": 1}

    board = plugin._lol_board(settings, FakeDevice(), featured=(featured_id, ranked, {"gameId": 1}))

    refreshed = [row for row in board if row["checked_ts"]]
    assert len(refreshed) == 1 + pro_tracker.LOL_BOARD_BATCH
    assert board[0]["tier"] == "CHALLENGER" and board[0]["lp"] >= board[1]["lp"]
    faker = next(row for row in board if row["player"] == "Faker")
    assert faker["tier"] == "GRANDMASTER" and faker["in_game"]
    assert next(row for row in board if row["player"] == "Nobody")["missing"]
    # A second render inside the cadence re-reads the next stale batch, not the same accounts.
    calls.clear()
    plugin._lol_board(settings, FakeDevice())
    assert sum("/league/v4/" in url for url in calls) == pro_tracker.LOL_BOARD_BATCH


def _riot_session(matches=None, ranks=None, calls=None):
    """A fake Riot API: PUUIDs named after the game name, the given newest match ids and ranks."""
    def fake_get(url, params=None, headers=None, timeout=None):
        if calls is not None:
            calls.append(url)
        if "/accounts/by-riot-id/" in url:
            name = urllib.parse.unquote(url.split("/by-riot-id/")[1].split("/")[0])
            return Response({"puuid": f"p-{name}"})
        if "/matches/by-puuid/" in url:
            puuid = url.split("/by-puuid/")[1].split("/")[0]
            return Response([f"KR_{(matches or {})[puuid[2:]]}"] if puuid[2:] in (matches or {}) else [])
        puuid = url.split("/by-puuid/")[1]
        tier, lp = (ranks or {}).get(puuid[2:], ("MASTER", 10))
        return Response([{"queueType": "RANKED_SOLO_5x5", "tier": tier, "rank": "I", "leaguePoints": lp,
                          "wins": 10, "losses": 5}])

    return lambda: type("S", (), {"get": staticmethod(fake_get)})()


def test_a_pro_may_have_more_than_one_account():
    kanavi = next(account for account in tracked_lol_accounts([]) if account["label"] == "Kanavi")

    assert (kanavi["gameName"], kanavi["tagLine"]) == ("vinaka", "KR1")
    assert [(alt["gameName"], alt["tagLine"]) for alt in kanavi["alts"]] == [("PoAtan", "ASP")]
    assert all(alt["label"] == "Kanavi" for alt in kanavi["alts"])


def test_the_ladder_shows_each_pros_best_ranked_account(plugin, monkeypatch):
    monkeypatch.setattr(pro_tracker, "get_http_session",
                        _riot_session(ranks={"vinaka": ("DIAMOND", 75), "PoAtan": ("MASTER", 367)}))
    monkeypatch.setattr(pro_tracker, "LOL_BOARD_BATCH", 40)

    board = plugin._lol_board({"includeCs2": "false"}, FakeDevice())

    kanavi = next(row for row in board if row["player"] == "Kanavi")
    assert (kanavi["tier"], kanavi["lp"], kanavi["riot_id"]) == ("MASTER", 367, "PoAtan#ASP")
    assert len(board) == 20


def test_the_page_shows_the_account_the_pro_played_on_last(plugin, monkeypatch):
    monkeypatch.setattr(pro_tracker, "get_http_session",
                        _riot_session(matches={"vinaka": 7800000000, "PoAtan": 7811111111}))
    kanavi = next(account for account in plugin._tracked_lol({}) if account["label"] == "Kanavi")

    shown = plugin._freshest_lol_account({}, FakeDevice(), kanavi)

    assert (shown["gameName"], shown["label"], shown["player_id"]) == ("PoAtan", "Kanavi", kanavi["id"])
    # A theme redraw makes no calls and keeps the account the live render chose.
    monkeypatch.setattr(pro_tracker, "get_http_session", lambda: pytest.fail("theme redraw called Riot"))
    assert plugin._freshest_lol_account({}, FakeDevice(), kanavi, refresh=False)["gameName"] == "PoAtan"
    # A pro with one account is shown on it without asking Riot.
    faker = next(account for account in plugin._tracked_lol({}) if account["label"] == "Faker")
    assert plugin._freshest_lol_account({}, FakeDevice(), faker) is faker


def test_win_loss_and_stat_icons_are_generated_badges_with_their_source():
    icons = Path(lol_info_module.__file__).with_name("assets") / "icons"
    record = json.loads((icons / "icons.source.json").read_text(encoding="utf-8"))
    names = {"victory", "defeat", "kda", "winrate", "kill_participation", "cs_per_minute"}

    assert set(record["icons"]) == names
    for name in names:
        with Image.open(icons / f"{name}.png") as icon:
            assert icon.mode == "RGBA" and icon.size == (96, 96)
            assert icon.getpixel((0, 0))[3] == 0 and icon.getpixel((48, 48))[3] == 255


def test_a_theme_redraw_neither_calls_riot_nor_writes_the_ladder(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(pro_tracker, "get_http_session", lambda: pytest.fail("theme redraw called the network"))

    board = plugin._lol_board({}, FakeDevice(), refresh=False)

    assert len(board) == 20 and not (tmp_path / pro_tracker.LOL_BOARD_STATE).exists()


def test_a_cs2_player_without_hltv_stats_hands_over_to_the_next_player(plugin, monkeypatch):
    pool = plugin._tracked_pool({})
    cs2 = next(item for item in pool if item["kind"] == "cs2")
    lol = next(item for item in pool if item["kind"] == "lol")
    shown = []
    monkeypatch.setattr(plugin, "_cs2_data", lambda pick, refresh=True: {
        "player": dict(pick["player"]), "board": plugin._cs2_roster(), "top20_year": 2025})
    monkeypatch.setattr(plugin, "_fetch_dashboard_data",
                        lambda effective, _device: shown.append(effective["_proAccountId"]) or plugin._sample_payload())
    monkeypatch.setattr(plugin, "_lol_photo", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(plugin, "_write_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(lol_info_module, "write_context", lambda *_args, **_kwargs: True)

    picks = iter([cs2, lol])
    monkeypatch.setattr(plugin, "_pick", lambda _settings, advance=True: {**next(picks), "position": 1, "size": 40})
    plugin.generate_image({}, FakeDevice(key=""))
    assert shown == [lol["account"]["id"]]

    # With nothing but stat-less CS2 players, the fourth is shown from the built-in Top 20.
    picks = iter([cs2] * 4)
    image = plugin.generate_image({}, FakeDevice(key=""))
    assert image.size == (800, 480) and next(picks, None) is None


TOP20_ROW = ('<tr><td class="text-ellipsis"><div class="top20-year-player-wrapper"><img alt="x" '
             'src="https://img-cdn.hltv.org/playerbodyshot/a.png?w=100" class="top20-table-img"><a href="/news/1/x" '
             'class="top20-year-playername-wrapper text-ellipsis"><b class="top20-year-playernick">ZywOo</b>'
             '<div class="top20-year-playername text-ellipsis"><img alt="France" src="/img/static/flags/30x20/FR.gif" '
             'class="flag">Mathieu Herbaut</div></a></div></td><td><a href="/team/9565/vitality"><img alt="Vitality" '
             'src="https://img-cdn.hltv.org/teamlogo/v.png" class="top20-year-teamlogo day-only"></a></td>'
             '<td class="top20-th-position"><b>#1</b></td></tr>')
PLAYER_PAGE = (
    '<span class="stats-window">(Past 3 months • 63 maps)</span>'
    '<div class="player-stat"><b>Rating 3.0</b><span class="statsVal"><p>1.37</p></span></div>'
    '<b>Firepower</b><span class="statsVal"><p><b>98</b></p></span><b>Sniping</b><span class="statsVal"><p><b>1</b></p></span>'
    '<div class="playerInfoRow playerTeam"><img alt="Spirit" src="https://img-cdn.hltv.org/teamlogo/d.png" class="day-only">'
    '<img alt="Spirit" src="https://img-cdn.hltv.org/teamlogo/n.png" class="night-only"></div>'
    '<img alt="donk" src="https://img-cdn.hltv.org/playerbodyshot/b.png?w=400" class="player-summary-stat-box-left-bodyshot">'
    '<a href="/matches/1/x" class="playerpage-matchbox a-reset"><img alt="FUT" src="f.png" class="team-logo">'
    '<div class="playerpage-matchbox-bottom text-ellipsis">BLAST Rivals</div><span data-unix="1794384000000">x</span></a>'
    '<a href="/stats/matches/2/x" class="playerpage-matchbox a-reset lost-matchbox"><img alt="MOUZ" src="m.png" '
    'class="team-logo"><div class="playerpage-matchbox-bottom text-ellipsis">EPL</div>'
    '<span class="playerpage-match-result">1 : 2</span></a>')


def test_hltv_pages_parse_into_the_top20_search_hit_and_player_stats():
    top = hltv_players.parse_top20(TOP20_ROW)
    assert top == [{"rank": 1, "nick": "ZywOo", "name": "Mathieu Herbaut", "country": "FR", "team": "Vitality",
                    "team_logo": "https://img-cdn.hltv.org/teamlogo/v.png",
                    "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/a.png?w=100"}]
    search = json.dumps([{"players": [{"id": 1, "nickName": "donkey"}, {"id": 21167, "nickName": "donk",
                                                                     "location": "/player/21167/donk",
                                                                     "team": {"name": "Spirit"}}]}])
    assert hltv_players.parse_search(search, "donk")["location"] == "/player/21167/donk"
    page = hltv_players.parse_player_page(PLAYER_PAGE)
    assert (page["rating"], page["maps"], page["roles"]) == (1.37, 63, {"Firepower": 98, "Sniping": 1})
    assert page["team"] == "Spirit" and page["team_logo_night"].endswith("n.png")
    assert page["bodyshot"].endswith("b.png?w=400")
    assert page["upcoming"][0]["opponent"] == "FUT" and page["upcoming"][0]["unix_ms"] == 1794384000000
    assert page["recent"] == [{"opponent": "MOUZ", "opponent_logo": "m.png", "event": "EPL", "score": "1 : 2",
                               "won": False}]


def test_cs2_refresh_backs_off_after_hltv_refuses(plugin, monkeypatch, tmp_path):
    calls = []

    def refuse(_session, path, timeout=None):
        calls.append(path)
        error = RuntimeError("HTTP 403")
        error.response = Response({}, 403)
        raise error

    monkeypatch.setattr(hltv_players, "fetch", refuse)
    monkeypatch.setattr(pro_tracker, "get_http_session", lambda: None)
    pick = next(item for item in plugin._tracked_pool({}) if item["kind"] == "cs2")

    data = plugin._cs2_data(pick)
    state = json.loads((tmp_path / pro_tracker.CS2_STATE).read_text(encoding="utf-8"))

    assert calls == ["/players/top20/" + str(time.localtime().tm_year - 1)]
    assert state["hltv_backoff_until"] > time.time() + 5 * 3600
    # The built-in Top 20 still names everyone while HLTV is unreachable.
    assert len(data["board"]) == 20 and data["board"][0]["nick"] == "ZywOo"
    calls.clear()
    plugin._cs2_data(pick)
    assert calls == []


def test_pro_photos_come_from_the_teams_lolesports_roster_weekly(plugin, monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params["id"])
        return Response({"data": {"teams": [{"players": [{"summonerName": "Faker", "image": "http://x/faker.png"}]}]}})

    monkeypatch.setattr(pro_tracker, "get_http_session", lambda: type("S", (), {"get": staticmethod(fake_get)})())
    account = {"label": "Faker", "team": "T1"}

    assert plugin._lol_photo(account) == "http://x/faker.png"
    assert plugin._lol_photo(account) == "http://x/faker.png" and calls == ["t1"]
    assert plugin._lol_photo({"label": "Nobody", "team": "XYZ"}) == ""


def test_both_pages_render_with_the_shown_player_highlighted(plugin):
    payload = plugin._sample_payload()
    pick = plugin._tracked_pool({})[0]
    board = plugin._lol_board({}, FakeDevice(), refresh=False)
    lol = plugin._render_lol_pro(payload, (800, 480), {"mode": "night"}, {**pick, "position": 3, "size": 40}, board)
    cs2_pick = next(item for item in plugin._tracked_pool({}) if item["kind"] == "cs2")
    data = {"player": {**cs2_pick["player"], "rating": 1.21, "maps": 40, "roles": {"Firepower": 80}},
            "board": plugin._cs2_roster(), "top20_year": 2025}
    cs2 = plugin._render_cs2_pro(data, (800, 480), {"mode": "night"}, {**cs2_pick, "position": 4, "size": 40})

    for image in (lol, cs2):
        assert isinstance(image, Image.Image) and image.size == (800, 480)
        assert len(image.getcolors(maxcolors=1_000_000)) > 40
    gold = plugin._render_colors({"mode": "night"})["gold"]
    # The first board cell (the shown player in both) carries the gold highlight outline.
    assert lol.getpixel((418, 210)) == gold or gold in {lol.getpixel((x, 196)) for x in range(416, 600)}


def test_each_turn_in_the_rotation_prepares_a_new_pro_before_display():
    manifest = json.loads((Path(lol_info_module.__file__).with_name("plugin-info.json")).read_text(encoding="utf-8"))

    # The device keeps display-triggered provider work off by default; this plugin is an audited exception,
    # so its next player is fetched and drawn just before its turn on screen.
    assert manifest["refresh_on_display"] is True
    assert manifest["capabilities"]["supports_presentation_refresh"] is True
    assert manifest["capabilities"]["allows_display_triggered_provider_refresh"] is True
    plugin = LoLInfo({"id": "lol_info"})
    assert plugin.presentation_mode({"refreshOnDisplay": True}).name == "PREPARED_BANK"


LOADOUT_PAGE = (
    '<div class="skins-weapon-columns"><div class="skins-weapon-column ct "><h3>Rifles</h3>'
    '<div class="skins-wrapper rarity-covert"><div class="skin-top skins-loadout-lightbox">'
    '<img alt="" src="https://skins-cdn.hltv.org/skin/m4.png" class="skin-img" title=""><div class="skin-title">Printstream</div>'
    '<div class="skin-more-skins">+1</div><div class="lightbox-content hidden"><div class="weapon-lightbox">'
    '<div class="skins-wrapper rarity-classified"><a href="/skins/rifle/m4a1-s/hot-rod" class="skin-top">'
    '<img alt="" src="https://skins-cdn.hltv.org/skin/hot.png" class="skin-img" title=""><div class="skin-title">Hot Rod</div></a>'
    '<div class="skin-bottom"><a href="https://sih.app/market?item=M4A1-S+|+Hot+Rod+(Factory+New)&amp;x=1">x</a></div></div>'
    '</div></div></div>'
    '<div class="skin-bottom"><a href="https://sih.app/market?item=StatTrak%E2%84%A2+M4A1-S+|+Printstream+(Factory+New)'
    '&amp;sortBy=1">x</a></div></div>'
    '</div><div class="skins-weapon-column t hidden"><h3>Rifles</h3>'
    '<div class="skins-wrapper rarity-covert"><a href="/skins/rifle/ak-47/wild-lotus" class="skin-top">'
    '<img alt="" src="https://skins-cdn.hltv.org/skin/ak.png" class="skin-img" title=""><div class="skin-title">Wild Lotus</div></a>'
    '<div class="skin-bottom"><a href="https://sih.app/market?item=AK-47+|+Wild+Lotus+(Field-Tested)&amp;a=1">x</a></div></div>'
    '</div></div>')
MAP_STATS = [
    {"map_name": "de_ancient", "maps_count": 14, "avg_player_rating": 7.87, "avg_kills": 0.99, "avg_damage": 102.46},
    {"map_name": "de_overpass", "maps_count": 3, "avg_player_rating": 9.1, "avg_kills": 1.2, "avg_damage": 120},
    {"map_name": "de_dust2", "maps_count": 27, "avg_player_rating": 7.15, "avg_kills": 0.87, "avg_damage": 88.6},
    {"map_name": "cs_office", "maps_count": 9, "avg_player_rating": 8.0, "avg_kills": 1, "avg_damage": 90},
]


class JsonBody:
    def __init__(self, payload, status=200):
        self.status_code, self.body = status, json.dumps(payload).encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, _size):
        yield self.body


def test_the_loadout_keeps_each_slots_latest_skin_per_side():
    loadout = hltv_players.parse_loadout(LOADOUT_PAGE)

    assert loadout == {
        "ct": [{"group": "Rifles", "weapon": "M4A1-S", "skin": "Printstream",
                "image": "https://skins-cdn.hltv.org/skin/m4.png", "rarity": "covert"}],
        "t": [{"group": "Rifles", "weapon": "AK-47", "skin": "Wild Lotus",
               "image": "https://skins-cdn.hltv.org/skin/ak.png", "rarity": "covert"}],
    }
    assert hltv_players.parse_player_page(PLAYER_PAGE)["loadout"] == {}


def test_bo3_map_pool_ranks_maps_played_often_enough_by_score():
    pool = bo3_stats.parse_map_stats(MAP_STATS)

    # Overpass scores best but on three maps; Office is not a competitive map.
    assert [row["map"] for row in pool] == ["de_ancient", "de_dust2", "de_overpass"]
    assert (pool[0]["name"], pool[0]["english"], pool[0]["maps"], round(pool[0]["adr"])) == ("远古遗迹", "Ancient", 14, 102)


def test_bo3_lookup_takes_the_hltv_nickname_and_the_real_surname():
    results = [{"slug": "frozen-kr", "nickname": "Frozen", "last_name": "Tae-il"},
               {"slug": "frozen", "nickname": "frozen", "last_name": "Čerňanský"}]

    assert bo3_stats.pick_player(results, "frozen", "David Čerňanský")["slug"] == "frozen"
    assert bo3_stats.pick_player(results, "frozen", "David Cernansky")["slug"] == "frozen"
    assert bo3_stats.pick_player(results, "frozen", "Someone Else") is None
    assert bo3_stats.pick_player([{"slug": "mezil", "nickname": "mezii", "last_name": "Merriman"}], "mezii",
                                 "William Merriman")["slug"] == "mezil"


def test_the_shown_cs2_players_map_pool_is_read_from_bo3_once_a_day(plugin, monkeypatch, tmp_path):
    calls = []

    class Session:
        def get(self, url, timeout=None, headers=None, stream=None):
            calls.append(url)
            if "/players?" in url:
                return JsonBody({"results": [{"slug": "donk", "nickname": "donk", "last_name": "Kryshkovets"}]})
            return JsonBody(MAP_STATS)

    monkeypatch.setattr(pro_tracker, "get_http_session", lambda: Session())
    # HLTV is resting, so only bo3.gg is asked.
    (tmp_path / pro_tracker.CS2_STATE).write_text(json.dumps({"hltv_backoff_until": time.time() + 3600}), encoding="utf-8")
    pick = next(item for item in plugin._tracked_pool({}) if item["kind"] == "cs2" and item["player"]["nick"] == "donk")

    data = plugin._cs2_data(pick)

    assert [urllib.parse.urlsplit(url).path for url in calls] == ["/api/v1/players", "/api/v1/players/donk/map_stats"]
    assert data["player"]["map_pool"][0]["map"] == "de_ancient" and data["player"]["bo3_slug"] == "donk"
    calls.clear()
    assert plugin._cs2_data(pick)["player"]["map_pool"][0]["map"] == "de_ancient" and calls == []
    plugin._cs2_data(pick, refresh=False)
    assert calls == []


def test_the_loadout_leads_with_the_awp_only_for_a_sniper():
    def slot(weapon):
        return {"weapon": weapon, "skin": "x", "image": f"https://skins-cdn.hltv.org/skin/{weapon}.png"}

    loadout = {"t": [slot("Glock-18"), slot("AK-47"), slot("AWP")], "ct": [slot("USP-S"), slot("M4A1-S"), slot("AWP")]}

    rifler = LoLInfo._cs2_guns({"loadout": loadout, "roles": {"Sniping": 3}})
    sniper = LoLInfo._cs2_guns({"loadout": loadout, "roles": {"Sniping": 92}})

    assert [gun["weapon"] for gun in rifler] == ["AK-47", "M4A1-S", "USP-S"]
    assert [gun["weapon"] for gun in sniper] == ["AWP", "AK-47", "M4A1-S"]
    assert LoLInfo._cs2_archetype({"Sniping": 92, "Firepower": 99}) == "主狙手"
    assert LoLInfo._cs2_archetype({"Sniping": 2, "Firepower": 55, "Closing": 78}) == "残局专家"


def test_the_cs2_page_lies_on_the_best_maps_art_with_the_guns_in_the_band(plugin):
    gun = Image.new("RGBA", (512, 384), (0, 0, 0, 0))
    gun.paste((200, 60, 40, 255), (40, 150, 470, 230))
    plugin._image_from_url = lambda url, label="": gun.copy() if "skins-cdn" in str(url) else None
    cs2_pick = next(item for item in plugin._tracked_pool({}) if item["kind"] == "cs2")
    roles = {name: 50 for name in hltv_players.ROLE_NAMES}
    board = [{**row, "rating": 1.0 + index / 100, "roles": dict(roles, Firepower=40 + index)}
             for index, row in enumerate(plugin._cs2_roster())]
    player = {**cs2_pick["player"], "rating": 1.21, "maps": 40, "roles": dict(roles, Firepower=98),
              "loadout": {"t": [{"weapon": "AK-47", "skin": "Wild Lotus", "image": "https://skins-cdn.hltv.org/skin/ak.png"}]},
              "map_pool": bo3_stats.parse_map_stats(MAP_STATS),
              "recent": [{"opponent": "MOUZ", "score": "2 : 1", "won": True},
                         {"opponent": "Aurora", "score": "0 : 2", "won": False}]}
    colors = plugin._render_colors({"mode": "night"})
    pick = {**cs2_pick, "position": 1, "size": 40}

    page = plugin._render_cs2_pro({"player": player, "board": board, "top20_year": 2025}, (800, 480), {"mode": "night"}, pick)
    plain = plugin._render_cs2_pro({"player": {**player, "map_pool": [], "loadout": {}}, "board": board, "top20_year": 2025},
                                   (800, 480), {"mode": "night"}, pick)

    # The gutter between the panels shows the map; without a map pool it is the flat page colour.
    assert page.getpixel((403, 320)) != colors["background"] and plain.getpixel((403, 320)) == colors["background"]
    # The AK's red stands in the hero band between the name block and the rating.
    assert any(page.getpixel((x, y))[0] > 150 and page.getpixel((x, y))[1] < 90
               for x in range(380, 600, 3) for y in range(20, 90, 3))
    assert plugin._cs2_role_average(board)["Firepower"] == pytest.approx(49.5)


def test_board_logos_prefer_the_bundled_cs2_logos_over_hltv_svgs():
    logos = pro_render._bundled_team_logos()

    assert pro_render._team_key("Team Vitality") == "vitality" and pro_render._team_key("NAVI") == "natusvincere"
    assert logos["vitality"].endswith("vitality.png") and logos["mouz"].endswith("mouz.png")


def test_the_player_page_lists_its_newest_gallery_pictures_sharpest_copy_first():
    page = ('<h2 class="standard-headline">Pictures of x</h2><div class="gallery-grid">'
            '<a href="/gallery/view/2"><picture><img alt="" src="https://img-cdn.hltv.org/gallerypicture/b.jpg?w=400&amp;s=1"'
            ' class="thumbnail" srcset="https://img-cdn.hltv.org/gallerypicture/b.jpg?w=800&amp;s=2 2x" title=""></picture></a>'
            '<a href="/gallery/view/1"><picture><img alt="" src="https://img-cdn.hltv.org/gallerypicture/a.jpg?w=400&amp;s=3"'
            ' class="thumbnail" title=""></picture></a></div>')

    assert hltv_players.parse_photos(page) == ["https://img-cdn.hltv.org/gallerypicture/b.jpg?w=800&s=2",
                                               "https://img-cdn.hltv.org/gallerypicture/a.jpg?w=400&s=3"]
    assert hltv_players.parse_player_page(page)["photos"][0].endswith("s=2")
    assert hltv_players.parse_photos("<div>no gallery</div>") == []


def test_the_cs2_results_column_shows_the_newest_photo_or_keeps_its_art(plugin):
    photo = Image.new("RGB", (800, 534), (200, 30, 160))
    plugin._image_from_url = lambda url, label="": photo.copy() if "gallerypicture" in str(url) else None
    colors = plugin._render_colors({"mode": "night"})
    image = Image.new("RGB", (300, 140), colors["background"])
    draw = ImageDraw.Draw(image)
    fonts = {"tiny": plugin._font(9)}

    assert plugin._draw_cs2_photo(image, draw, {"photos": ["https://img-cdn.hltv.org/gallerypicture/a.jpg"]},
                                  (10, 10, 290, 130), colors, fonts) is True
    assert image.getpixel((150, 60)) == (200, 30, 160)
    assert plugin._draw_cs2_photo(image, draw, {"photos": []}, (10, 10, 290, 130), colors, fonts) is False
