"""The pro tracker behind LoLInfo: roster, random pick, boards, HLTV parsing and both pages."""

import json
import sys
import time
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.lol_info import hltv_players, pro_tracker  # noqa: E402
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


def test_a_theme_redraw_neither_calls_riot_nor_writes_the_ladder(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(pro_tracker, "get_http_session", lambda: pytest.fail("theme redraw called the network"))

    board = plugin._lol_board({}, FakeDevice(), refresh=False)

    assert len(board) == 20 and not (tmp_path / pro_tracker.LOL_BOARD_STATE).exists()


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
