"""Online friends are listed by presence: in a game, online, then the rest."""

from PIL import Image
import pytest

from plugins.steam_profile_dashboard.console_renderer import _Console, render_console
from plugins.steam_profile_dashboard.steam_profile_dashboard import SteamProfileDashboard

OFFLINE, ONLINE, BUSY, AWAY, SNOOZE, TRADE, PLAY = range(7)


def friend(name, state, game=None, **extra):
    record = {"steamid": name, "personaname": name, "personastate": state, **extra}
    if game:
        record.update(gameid=game, gameextrainfo=f"Game {game}")
    return record


@pytest.fixture
def plugin(monkeypatch):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    monkeypatch.setattr(plugin, "_game_background", lambda *_args: None, raising=False)
    monkeypatch.setattr(plugin, "_game_square_icon", lambda *_args: None)
    monkeypatch.setattr(plugin, "_avatar_image",
                        lambda _url, size, **_kwargs: Image.new("RGB", (size, size)))
    monkeypatch.setattr(plugin, "_profile_avatar_image", lambda _url, size: Image.new("RGB", (size, size)))
    monkeypatch.setattr(plugin, "_next_avatar_frame", lambda: None)
    return plugin


def panel_names(plugin, monkeypatch, friends):
    names = []
    original = _Console.text

    def record(self, box, value, *args, **kwargs):
        # Friend names sit in the panel's text column, one per 39 px row.
        if box[0] == 646 and (box[1] - 49) % 39 == 0:
            names.append(str(value))
        return original(self, box, value, *args, **kwargs)

    monkeypatch.setattr(_Console, "text", record)
    data = {"profile": {"personaname": "Owner"}, "friends": friends, "friend_count": len(friends),
            "online_friend_count": sum(1 for item in friends if item["personastate"]),
            "recent_games": [], "owned_games": [], "badges": {}}
    render_console(plugin, data, (800, 480))
    return names


def test_sorted_friends_put_games_then_online_then_other_presence(plugin):
    players = [friend("snooze", SNOOZE), friend("offline", OFFLINE), friend("away", AWAY),
               friend("busy", BUSY), friend("online", ONLINE), friend("trade", TRADE),
               friend("play", PLAY), friend("gamer", AWAY, game="730")]

    ordered = [item["personaname"] for item in plugin._sort_friends(players)]

    assert ordered == ["gamer", "online", "play", "trade", "busy", "away", "snooze", "offline"]


def test_panel_lists_playing_friends_before_online_before_away(plugin, monkeypatch):
    # Data cached by an older release can arrive in any order.
    friends = [friend("城", AWAY), friend("Feed Me", ONLINE), friend("Jw", ONLINE),
               friend("Zed", ONLINE, game="570"), friend("Ann", ONLINE, game="730"),
               friend("Offline Pal", OFFLINE)]

    assert panel_names(plugin, monkeypatch, friends) == ["Ann", "Zed", "Feed Me", "Jw"]


def test_panel_orders_ties_by_the_name_it_shows(plugin, monkeypatch):
    friends = [friend("b-public", ONLINE, _inkypi_friend_remark="Alpha"),
               friend("a-public", ONLINE, _inkypi_friend_remark="Bravo"),
               friend("dozing", SNOOZE)]

    assert panel_names(plugin, monkeypatch, friends) == ["Alpha", "Bravo", "dozing"]
