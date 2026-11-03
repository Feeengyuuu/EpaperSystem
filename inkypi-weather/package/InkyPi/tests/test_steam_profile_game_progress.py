"""The hero game's progress: the owner's achievements, their rarity and the latest announcement.

GetPlayerAchievements needs the owner's key and is exercised with fixtures only.
"""

from datetime import datetime
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from plugins.steam_profile_dashboard import console_renderer, steam_profile_dashboard as module
from plugins.steam_profile_dashboard.console_renderer import _Console, render_console
from plugins.steam_profile_dashboard.steam_profile_dashboard import SteamProfileDashboard
from runtime.refresh_contracts import TaskCancelled
from utils.http_client import HttpStatusError

PLAYER = {"playerstats": {"steamID": "1", "gameName": "ELDEN RING", "success": True, "achievements": [
    {"apiname": "ACH00", "achieved": 1, "unlocktime": 1790000000, "name": "艾尔登之王"},
    {"apiname": "ACH03", "achieved": 1, "unlocktime": 1790000001, "name": "群星的世纪"},
    {"apiname": "ACH39", "achieved": 1, "unlocktime": 1790000002, "name": "  宁姆格福的  英雄 "},
    {"apiname": "ACH16", "achieved": 0, "unlocktime": 0, "name": "癫火之王"},
]}}
RARITY = {"achievementpercentages": {"achievements": [
    {"name": "ACH39", "percent": "74.9"}, {"name": "ACH03", "percent": 16.54},
    {"name": "ACH16", "percent": "2.1"}, {"name": "ACH00", "percent": "not a number"}]}}
NEWS = {"appnews": {"appid": 1245620, "newsitems": [
    {"title": "Older notes", "date": 1792000000, "tags": ["patchnotes"]},
    {"title": "ELDEN RING  Patch 1.16", "date": 1793224089, "tags": ["patchnotes", "mod_reviewed"]},
    {"title": "Keycap event", "date": 1792500000}]}}


class FakeClient:
    def __init__(self, player=PLAYER, rarity=RARITY, news=NEWS):
        self.answers = {"GetPlayerAchievements": player, "GetGlobalAchievementPercentagesForApp": rarity,
                        "GetNewsForApp": news}
        self.calls = []

    def request_json(self, method, url, **kwargs):
        name = url.rstrip("/").split("/")[-2]
        self.calls.append((name, kwargs))
        answer = self.answers[name]
        if isinstance(answer, BaseException):
            raise answer
        return SimpleNamespace(data=answer)


@pytest.fixture
def plugin():
    return SteamProfileDashboard({"id": "steam_profile_dashboard"})


def test_progress_counts_unlocked_and_names_the_rarest_one_unlocked(plugin, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(module, "get_http_client", lambda: client)

    progress, calls = plugin._fetch_game_progress("secret-key", "76561198000000000", 1245620)

    # ACH16 is rarer but locked; ACH00 has no usable share; ACH03 is the rarest one unlocked.
    assert progress == {"appid": "1245620",
                        "achievements": {"total": 4, "unlocked": 3, "rarest": {"name": "群星的世纪", "percent": 16.5}},
                        "news": {"date": 1793224089, "title": "ELDEN RING Patch 1.16", "patch": True}}
    assert calls == 3
    player, rarity, news = client.calls
    assert player[0] == "GetPlayerAchievements" and player[1]["params"]["key"] == "secret-key"
    assert player[1]["params"]["l"] == "schinese" and player[1]["max_bytes"] <= 1024 * 1024
    # The public lookups never carry the owner's key.
    assert "key" not in rarity[1]["params"] and "key" not in news[1]["params"]
    assert news[1]["params"]["feeds"] == "steam_community_announcements"


def test_a_game_without_stats_keeps_its_news_and_no_progress_bar(plugin, monkeypatch):
    client = FakeClient(player=HttpStatusError("GET", "https://api.steampowered.com/x?key=secret", 400))
    monkeypatch.setattr(module, "get_http_client", lambda: client)

    progress, calls = plugin._fetch_game_progress("secret-key", "1", "108600")

    assert progress["achievements"] is None and progress["news"]["patch"] is True
    assert [name for name, _ in client.calls] == ["GetPlayerAchievements", "GetNewsForApp"] and calls == 2


def test_a_private_profile_answer_without_achievements_is_no_progress(plugin, monkeypatch):
    client = FakeClient(player={"playerstats": {"error": "Profile is not public", "success": False}},
                        news={"appnews": {"newsitems": []}})
    monkeypatch.setattr(module, "get_http_client", lambda: client)

    progress, _calls = plugin._fetch_game_progress("k", "1", "730")

    assert progress == {"appid": "730", "achievements": None, "news": None}


def test_cancellation_is_never_swallowed(plugin, monkeypatch):
    monkeypatch.setattr(module, "get_http_client", lambda: FakeClient(news=TaskCancelled("stop")))
    with pytest.raises(TaskCancelled):
        plugin._fetch_game_progress("k", "1", "730")


def test_no_hero_game_asks_nothing(plugin, monkeypatch):
    monkeypatch.setattr(module, "get_http_client", lambda: pytest.fail("no request expected"))
    assert plugin._fetch_game_progress("k", "1", None) == ({}, 0)


def _data(progress):
    return {
        "profile": {"personaname": "AshenOne", "personastate": 1, "gameid": "1245620", "gameextrainfo": "ELDEN RING"},
        "recent_games": [{"appid": 1245620, "name": "ELDEN RING", "playtime_2weeks": 60, "playtime_forever": 180}],
        "owned_games": [{"appid": 1245620, "name": "ELDEN RING", "playtime_forever": 180}],
        "spotlight_game": {"appid": 1245620}, "game_progress": progress,
    }


@pytest.fixture
def drawn(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_game_background", lambda _data, _appid, size: Image.new("RGB", size, (40, 60, 80)),
                        raising=False)
    monkeypatch.setattr(plugin, "_game_square_icon", lambda _data, _appid, size: Image.new("RGB", (size, size)))
    monkeypatch.setattr(plugin, "_avatar_image", lambda _url, size, **_kwargs: Image.new("RGB", (size, size)))
    monkeypatch.setattr(plugin, "_profile_avatar_image", lambda _url, size: Image.new("RGB", (size, size)),
                        raising=False)
    monkeypatch.setattr(plugin, "_next_avatar_frame", lambda: None)
    monkeypatch.setattr(console_renderer, "load_mosaic_avatars", lambda owner, records, size: [])
    texts = []
    original_text, original_draw = _Console.text, ImageDraw.ImageDraw.text

    def record(self, box, value, *args, **kwargs):
        texts.append(str(value))
        return original_text(self, box, value, *args, **kwargs)

    def record_draw(self, xy, text, *args, **kwargs):
        texts.append(str(text))
        return original_draw(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(_Console, "text", record)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_draw)
    return texts


def test_hero_shows_the_latest_update_progress_bar_and_rarest_unlock(plugin, drawn):
    progress = {"appid": "1245620", "news": {"date": 1793224089, "title": "Patch", "patch": True},
                "achievements": {"total": 42, "unlocked": 25, "rarest": {"name": "群星的世纪", "percent": 16.5}}}

    image = render_console(plugin, _data(progress), (800, 480))

    day = datetime.fromtimestamp(1793224089).strftime("%m/%d")
    assert f"游戏中 · {day} 更新" in drawn
    assert {"成就", "25/42", "60%", "最稀有", "群星的世纪", "16.5% 玩家"} <= set(drawn)
    assert not any("AppID" in text for text in drawn if text.startswith("游戏中"))
    # Everything stays inside the hero panel.
    hero = image.crop((195, 43, 583, 205))
    assert hero.getbbox() is not None


def test_progress_for_another_game_is_not_shown_and_appid_stays(plugin, drawn):
    progress = {"appid": "730", "news": {"date": 1793224089, "patch": False},
                "achievements": {"total": 1, "unlocked": 1, "rarest": None}}

    render_console(plugin, _data(progress), (800, 480))

    assert "游戏中 · AppID 1245620" in drawn and "成就" not in drawn


def test_an_announcement_without_patch_notes_reads_as_a_notice_and_no_rarest_line(plugin, drawn):
    progress = {"appid": "1245620", "news": {"date": 1793224089, "title": "Keycaps", "patch": False},
                "achievements": {"total": 10, "unlocked": 0, "rarest": None}}

    render_console(plugin, _data(progress), (800, 480))

    day = datetime.fromtimestamp(1793224089).strftime("%m/%d")
    assert f"游戏中 · {day} 公告" in drawn and "0/10" in drawn and "0%" in drawn and "最稀有" not in drawn
