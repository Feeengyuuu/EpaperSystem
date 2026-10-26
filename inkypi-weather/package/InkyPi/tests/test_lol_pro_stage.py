"""The LoL pro page's pro stage (lolesports schedule, series and game lines) and ranked form chart."""

import json
import sys
import time
import urllib.parse
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.lol_info import lolesports_pro, pro_render, pro_tracker  # noqa: E402
from plugins.lol_info.lol_info import LoLInfo  # noqa: E402


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _team(code, wins=None, outcome="", record=None):
    row = {"code": code, "name": code + " Esports", "image": f"https://static.lolesports.com/{code}.png"}
    if wins is not None:
        row["result"] = {"gameWins": wins, "outcome": outcome}
    if record:
        row["record"] = {"wins": record[0], "losses": record[1]}
    return row


def _event(match_id, start, state, teams, best_of=3):
    return {"startTime": _iso(start), "state": state, "type": "match", "blockName": "Swiss",
            "league": {"slug": "worlds"}, "match": {"id": match_id, "teams": teams,
                                                    "strategy": {"type": "bestOf", "count": best_of}}}


NOW = time.time()
SCHEDULE = {"data": {"schedule": {"events": [
    _event("old", NOW - 9 * 86400, "completed", [_team("T1", 2, "win"), _team("HLE", 1, "loss")]),
    _event("won", NOW - 86400, "completed", [_team("DK", 1, "loss", (1, 1)), _team("T1", 2, "win", (2, 0))]),
    _event("other", NOW - 3600, "completed", [_team("GEN", 1, "win"), _team("BLG", 0, "loss")]),
    _event("tbd", NOW + 3600, "unstarted", [_team("T1"), _team("TBD")]),
    _event("next", NOW + 2 * 86400, "unstarted", [_team("T1", 0, "", (2, 0)), _team("CFO", 0, "", (1, 1))]),
]}}}


def _player(participant, name, champion, role, line):
    return ({"participantId": participant, "summonerName": name, "championId": champion, "role": role},
            {"participantId": participant, "kills": line[0], "deaths": line[1], "assists": line[2],
             "creepScore": line[3], "totalGold": 12000, "level": 16})


def _window(faker_line, towers, state="finished"):
    blue = [_player(1, "T1 Doran", "Jayce", "top", (1, 1, 1, 200)), _player(3, "T1 Faker", "Anivia", "mid", faker_line)]
    red = [_player(8, "DK ShowMaker", "Azir", "mid", (2, 3, 4, 280))]
    frame = {"rfc460Timestamp": _iso(NOW - 80000), "gameState": state,
             "blueTeam": {"totalKills": 12, "totalGold": 60000, "towers": towers[0], "inhibitors": 1, "barons": 1,
                          "dragons": ["ocean", "cloud"], "participants": [line for _meta, line in blue]},
             "redTeam": {"totalKills": 7, "totalGold": 52000, "towers": towers[1], "inhibitors": 0, "barons": 0,
                         "dragons": ["infernal"], "participants": [line for _meta, line in red]}}
    return {"gameMetadata": {"blueTeamMetadata": {"esportsTeamId": "t1", "participantMetadata": [m for m, _l in blue]},
                             "redTeamMetadata": {"esportsTeamId": "dk", "participantMetadata": [m for m, _l in red]}},
            "frames": [frame]}


DETAILS = {"frames": [{"participants": [{"participantId": 3, "killParticipation": 0.75, "championDamageShare": 0.31,
                                         "wardsPlaced": 12, "items": [3089, 3020]}]}]}
EVENT = {"data": {"event": {"match": {
    "teams": [{"id": "dk", "code": "DK", "name": "Dplus KIA", "image": "x"}, {"id": "t1", "code": "T1", "name": "T1"}],
    "games": [{"id": "g2", "number": 2, "state": "completed"}, {"id": "g1", "number": 1, "state": "completed"},
              {"id": "g3", "number": 3, "state": "completed"}, {"id": "g4", "number": 4, "state": "unneeded"}]}}}}
WINDOWS = {"g1": _window((5, 0, 7, 300), (9, 2)), "g2": _window((1, 4, 2, 250), (3, 10)), "g3": _window((3, 1, 9, 310), (8, 4))}


class JsonBody:
    def __init__(self, payload, status=200):
        self.status_code, self.body = status, json.dumps(payload).encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, _size):
        yield self.body


class Feed:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, stream=None):
        path = urllib.parse.urlsplit(url).path
        self.calls.append(path.rsplit("/", 2)[-2] + "/" + path.rsplit("/", 1)[-1] if "livestats" in url
                          else path.rsplit("/", 1)[-1])
        if path.endswith("getSchedule"):
            return JsonBody(SCHEDULE)
        if path.endswith("getEventDetails"):
            return JsonBody(EVENT)
        if "/window/" in path:
            return JsonBody(WINDOWS[path.rsplit("/", 1)[-1]])
        if "/details/" in path:
            return JsonBody(DETAILS)
        raise AssertionError(url)


@pytest.fixture
def plugin(tmp_path):
    instance = LoLInfo({"id": "lol_info"})
    instance._cache_dir = lambda: tmp_path
    instance._image_from_url = lambda url, label="": None
    return instance


def test_the_schedule_gives_a_teams_next_series_and_its_results_newest_first():
    events = lolesports_pro.parse_schedule(SCHEDULE)
    upcoming, results = lolesports_pro.team_series(events, "T1", NOW)

    # A series against a team not yet drawn (TBD) is not the next one.
    assert upcoming["id"] == "next" and upcoming["theirs"]["code"] == "CFO" and upcoming["ours"]["record"] == [2, 0]
    assert [row["id"] for row in results] == ["won", "old"] and all(row["won"] for row in results)
    assert (results[0]["ours"]["wins"], results[0]["theirs"]["wins"], results[0]["best_of"]) == (2, 1, 3)


def test_a_game_line_names_the_pro_by_their_broadcast_name_with_both_teams_objectives():
    game = lolesports_pro.parse_game(_window((3, 1, 9, 310), (8, 4)))
    game["shares"] = lolesports_pro.parse_shares(DETAILS)
    line = lolesports_pro.player_line(game, "Faker")

    assert (line["champion"], line["kills"], line["deaths"], line["assists"], line["cs"]) == ("Anivia", 3, 1, 9, 310)
    assert (line["kp"], line["damage_share"], line["side"]) == (0.75, 0.31, "blue")
    assert line["ours"]["towers"] == 8 and line["theirs"]["dragons"] == ["infernal"] and "players" not in line["ours"]
    assert lolesports_pro.player_line(game, "Keria") is None
    assert lolesports_pro.feed_stamp(1000.0).endswith(":15:40.000Z")


def test_a_series_last_game_goes_to_its_winner_and_the_rest_by_towers():
    games = [lolesports_pro.player_line(lolesports_pro.parse_game(WINDOWS[key]), "Faker") for key in ("g1", "g2", "g3")]

    assert lolesports_pro.series_winners(games, True, 2) == {2: True, 0: True, 1: False}
    assert lolesports_pro.series_winners(games, False, 1) == {2: False, 0: True, 1: False}
    # While the series runs, its score so far goes to the games with the better tower count.
    assert lolesports_pro.series_winners(games[:2], None, 1, finished=False) == {0: True, 1: False}


def test_the_pro_stage_reads_each_finished_game_once_for_every_pro_of_the_team(plugin, monkeypatch):
    feed = Feed()
    monkeypatch.setattr(pro_tracker, "get_http_session", lambda: feed)
    faker = {"label": "Faker", "team": "T1"}

    stage = plugin._lol_esports(faker)

    assert feed.calls == ["getSchedule", "getEventDetails", "window/g1", "window/g2", "window/g3", "details/g3"]
    assert stage["next"]["id"] == "next" and stage["series"]["id"] == "won"
    assert [(game["number"], game["champion"], game["won"]) for game in stage["series"]["games"]] == [
        (1, "Anivia", True), (2, "Anivia", False), (3, "Anivia", True)]
    assert stage["series"]["games"][-1]["kp"] == 0.75
    feed.calls.clear()
    # Doran plays in the same games: nothing is read again within the hour.
    doran = plugin._lol_esports({"label": "Doran", "team": "T1"})
    assert feed.calls == [] and doran["series"]["games"][0]["champion"] == "Jayce"
    assert plugin._lol_esports({"label": "Nobody", "team": ""}) == {}


def test_ranked_form_scores_each_axis_against_the_role_mark(plugin):
    match = {"lane": "MIDDLE", "duration": 1800, "kills": 11, "deaths": 0, "assists": 5, "cs": 240, "kp": 55,
             "vision": 24, "damage_share": 0.27}
    aram = {"lane": "Invalid", "duration": 1200, "kills": 30, "deaths": 9, "cs": 50, "kp": 90}

    scores = LoLInfo._lol_form_scores([match, dict(match), aram], "MID")

    # CS 8/min, 55% of the kills, 0.8 vision/min and 27% of the damage are the mid mark (50); no
    # deaths scores 100 and 11 kills (twice the mark) 100.
    assert scores == {"damage": 50, "farm": 50, "teamfight": 50, "vision": 50, "survival": 100, "kills": 100}
    assert LoLInfo._lol_form_scores([match, aram], "MID") == {}


def test_the_form_chart_compares_against_the_other_pros_once_enough_are_known(plugin):
    match = {"lane": "TOP", "duration": 1800, "kills": 4, "deaths": 4, "cs": 220, "kp": 45, "vision": 21,
             "damage_share": 0.24}
    for index in range(3):
        plugin._lol_form({"id": f"p{index}", "role": "TOP"}, [match, match])

    shown = plugin._lol_form({"id": "me", "role": "TOP"}, [match, match], refresh=False)

    assert shown["games"] == 2 and shown["scores"]["damage"] == 50
    assert shown["average"]["damage"] == 50 and set(shown["average"]) == set(pro_tracker.LOL_FORM_AXES)
    assert plugin._lol_form({"id": "me"}, [], refresh=False)["average"]["farm"] == round(50 * 220 / 30 / 7.5)


def test_the_lol_card_shows_the_newest_pro_game_or_falls_back_to_ranked(plugin, monkeypatch):
    feed = Feed()
    monkeypatch.setattr(pro_tracker, "get_http_session", lambda: feed)
    payload = plugin._sample_payload()
    payload["form"] = {"scores": {axis: 60 for axis in pro_tracker.LOL_FORM_AXES}, "average": {}, "games": 5}
    colors = plugin._render_colors({"mode": "night"})

    def card(data):
        image = Image.new("RGB", (800, 480), colors["background"])
        plugin._draw_lol_card(image, ImageDraw.Draw(image), data, {"team": "T1"}, colors, plugin._pro_fonts())
        return image

    pro = card({**payload, "esports": plugin._lol_esports({"label": "Faker", "team": "T1"})})
    ranked = card(payload)

    x0, y0, _x1, _y1 = pro_render.LEFT
    gold = colors["gold"]
    # The newest game was a win: its gold tag sits on the corner's picture under the next series and two
    # results; without a pro season the corner holds the latest ranked game under three ranked rows.
    def tag_area(image):
        return {image.getpixel((x, y)) for x in range(x0 + 219, x0 + 226) for y in range(y0 + 131, y0 + 147)}

    assert gold in tag_area(pro) and gold not in tag_area(ranked)
    assert LoLInfo({"id": "lol_info"})._lane_label("Invalid") == ""


def test_the_identity_rows_show_the_teams_league_logo_and_the_servers_flag(plugin):
    flag = Image.new("RGB", (40, 27), (200, 30, 160))
    plugin._image_from_url = lambda url, label="": flag.copy() if str(url).endswith("flagcdn.com/w40/kr.png") else None
    colors = plugin._render_colors({"mode": "night"})
    image = Image.new("RGB", (800, 480), colors["background"])
    account = {"label": "Faker", "team": "T1", "role": "MID", "gameName": "Hide on bush", "tagLine": "KR1",
               "platformRoute": "kr"}

    plugin._draw_lol_identity(image, ImageDraw.Draw(image), account, (150, 10, 240), colors, plugin._pro_fonts())

    assert (200, 30, 160) in set(image.crop((150, 120, 400, 150)).get_flattened_data())
    for league in set(pro_render.TEAM_LEAGUES.values()):
        assert plugin._asset_logo(f"leagues/{league}.png", pro_render.LEAGUE_MARK) is not None


def _stage_row(league, stage="Swiss"):
    return {"start": time.time() - 3600, "won": False, "league": league, "stage": stage,
            "ours": {"code": "HLE", "wins": 0, "record": [1, 2]}, "theirs": {"code": "IG", "wins": 1}}


@pytest.mark.parametrize("league", ["worlds", "msi", "lck", "first_stand"])
def test_the_schedule_is_headed_by_the_events_official_mark(plugin, league):
    colors = plugin._render_colors({"mode": "night"})
    image = Image.new("RGB", (800, 480), colors["background"])
    draw = ImageDraw.Draw(image)
    fonts = plugin._pro_fonts()
    texts = []
    original = plugin._text

    def record(draw_obj, xy, text, *args, **kwargs):
        texts.append(str(text))
        return original(draw_obj, xy, text, *args, **kwargs)

    plugin._text = record
    box = (442, 180, 604, 470)  # the real column, 162 px wide
    plugin._draw_lol_stage(image, draw, {"esports": {"results": [_stage_row(league)]}}, {"team": "HLE"}, box, colors, fonts)

    assert "瑞士轮 1胜2负" in texts
    if league == "first_stand":
        # No mark for this event: the title stays.
        assert "赛程与战绩" in texts
        return
    assert "赛程与战绩" not in texts
    mark = image.crop((box[0], box[1], box[0] + 30, box[1] + 16))
    assert set(mark.get_flattened_data()) - {tuple(colors["background"])}, f"no {league} mark at the section's head"


def test_an_event_without_a_mark_leaves_the_header_clear(plugin):
    colors = plugin._render_colors({"mode": "night"})
    assert plugin._event_mark("first_stand", None, (80, 15), colors) is None
    worlds = plugin._event_mark("worlds", time.time(), (80, 15), colors)
    assert worlds is not None and worlds.height <= 15 and worlds.width > worlds.height * 3
