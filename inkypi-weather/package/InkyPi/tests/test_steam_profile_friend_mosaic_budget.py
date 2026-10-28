"""Optional overflow portraits must not consume the refresh task's deadline."""

from io import BytesIO
import os
from pathlib import Path
import threading
from types import SimpleNamespace

from PIL import Image
import pytest

from plugins.steam_profile_dashboard import console_renderer, friend_avatars
from plugins.steam_profile_dashboard.console_renderer import _Console
from plugins.steam_profile_dashboard.steam_profile_dashboard import SteamProfileDashboard
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def subject(tmp_path, monkeypatch):
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda: str(tmp_path))
    clock = Clock()
    parent = TaskContext(threading.Event(), 200.0, clock)
    monkeypatch.setattr(friend_avatars, "current_task_context", lambda: parent)
    calls, clients = [], []

    class Client:
        closed = False

        def request_bytes(self, method, url, **kwargs):
            calls.append((url, kwargs))
            context = kwargs["context"]
            clock.now += context.remaining_seconds()
            context.raise_if_cancelled()

        def close(self):
            self.closed = True

    def create_client():
        client = Client()
        clients.append(client)
        return client

    monkeypatch.setattr(friend_avatars, "create_single_attempt_http_client", create_client)
    return SimpleNamespace(plugin=plugin, clock=clock, parent=parent, calls=calls, clients=clients, client_type=Client)


def records(count):
    return [({"personastate": 1, "avatarfull": f"https://avatars.steamstatic.com/sample-{index}.jpg"},
             f"https://avatars.steamstatic.com/sample-{index}.jpg") for index in range(count)]


def seed(plugin, url):
    path = Path(plugin._avatar_cache_path(url))
    Image.new("RGB", (64, 64), (75, 125, 175)).save(path)
    os.utime(path, (1, 1))  # A usable old avatar should not be refreshed by optional display work.
    return path


def test_slow_missing_avatars_share_eight_seconds_and_later_cached_avatar_still_displays(subject):
    rows = records(9)
    path = seed(subject.plugin, rows[-1][1])
    result = friend_avatars.load_mosaic_avatars(subject.plugin, rows, 12)

    assert subject.clock.now == 108
    assert len(subject.calls) == 4  # 2.5 + 2.5 + 2.5 + 0.5, no further HTTP after that.
    assert [kwargs["context"].deadline_monotonic for _url, kwargs in subject.calls] == [102.5, 105, 107.5, 108]
    assert all(kwargs["timeout"] == 2.5 and kwargs["allow_redirects"] is False for _url, kwargs in subject.calls)
    assert [friend for friend, _image in result] == [rows[-1][0]]
    assert result[0][1].getpixel((6, 6)) == (75, 125, 175, 255)
    assert path.stat().st_mtime == 1
    assert len(subject.clients) == 1 and subject.clients[0].closed


def test_missing_portraits_after_budget_are_counted_without_placeholder_faces(subject, monkeypatch):
    rows = records(9)
    seed(subject.plugin, rows[-1][1])
    console = _Console(subject.plugin, {})
    labels = []
    monkeypatch.setattr(console, "text", lambda _box, text, *_args, **_kwargs: labels.append(text))
    console._friend_tile(None, 710, 140, {}, [friend for friend, _url in rows])
    assert labels == ["+8"]
    assert len(subject.calls) == 4 and subject.clock.now == 108


def test_cached_display_never_creates_network_client_or_writes_for_missing_avatars(subject, tmp_path):
    rows = records(3)
    path = seed(subject.plugin, rows[1][1])
    before = {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in tmp_path.iterdir()}
    subject.plugin._media_read_only = True
    result = friend_avatars.load_mosaic_avatars(subject.plugin, rows, 18)
    assert [friend for friend, _image in result] == [rows[1][0]]
    assert subject.clients == [] and subject.calls == []
    assert before == {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in tmp_path.iterdir()}
    assert path.exists() and subject.plugin._media_read_only is True


@pytest.mark.parametrize("cancel_before_request", [True, False])
def test_parent_cancellation_is_propagated_and_client_is_closed(subject, monkeypatch, cancel_before_request):
    if cancel_before_request:
        subject.parent.cancel_event.set()
    else:
        def cancel(_self, _method, _url, **_kwargs):
            subject.parent.cancel_event.set()
            raise TaskCancelled("owner canceled")
        monkeypatch.setattr(subject.client_type, "request_bytes", cancel)
    with pytest.raises(TaskCancelled):
        friend_avatars.load_mosaic_avatars(subject.plugin, records(9), 12)
    assert all(client.closed for client in subject.clients)
    assert len(subject.clients) == (0 if cancel_before_request else 1)


def test_nearly_expired_parent_keeps_rendering_reserve_and_uses_cached_avatars(subject, monkeypatch):
    parent = TaskContext(subject.parent.cancel_event, 101.0, subject.clock)
    monkeypatch.setattr(friend_avatars, "current_task_context", lambda: parent)
    rows = records(3)
    seed(subject.plugin, rows[-1][1])
    result = friend_avatars.load_mosaic_avatars(subject.plugin, rows, 18)
    assert [friend for friend, _image in result] == [rows[-1][0]]
    assert subject.clock.now == 100 and subject.calls == [] and subject.clients == []


def test_network_budget_stops_two_seconds_before_parent_deadline(subject, monkeypatch):
    parent = TaskContext(subject.parent.cancel_event, 104.0, subject.clock)
    monkeypatch.setattr(friend_avatars, "current_task_context", lambda: parent)
    assert friend_avatars.load_mosaic_avatars(subject.plugin, records(9), 12) == []
    assert subject.clock.now == 102 and len(subject.calls) == 1
    assert parent.remaining_seconds() == 2


def test_expired_parent_deadline_is_not_swallowed_as_an_optional_miss(subject, monkeypatch):
    parent = TaskContext(subject.parent.cancel_event, 104.0, subject.clock)
    monkeypatch.setattr(friend_avatars, "current_task_context", lambda: parent)

    def expire(_self, _method, _url, **_kwargs):
        subject.clock.now = 104
        raise TaskDeadlineExceeded("parent expired during request")

    monkeypatch.setattr(subject.client_type, "request_bytes", expire)
    with pytest.raises(TaskDeadlineExceeded):
        friend_avatars.load_mosaic_avatars(subject.plugin, records(9), 12)
    assert subject.clock.now == 104
    assert subject.clients[0].closed


def test_successful_optional_download_is_reused_without_another_request(subject, monkeypatch):
    payload = BytesIO()
    Image.new("RGB", (64, 64), (75, 125, 175)).save(payload, format="PNG")

    def download(_self, _method, url, **kwargs):
        subject.calls.append((url, kwargs))
        return SimpleNamespace(data=payload.getvalue())

    monkeypatch.setattr(subject.client_type, "request_bytes", download)
    first = friend_avatars.load_mosaic_avatars(subject.plugin, records(1), 38)
    second = friend_avatars.load_mosaic_avatars(subject.plugin, records(1), 18)
    assert len(subject.calls) == 1 and len(subject.clients) == 1
    assert first[0][1].size == (38, 38) and second[0][1].size == (18, 18)
    assert first[0][1].getpixel((19, 19)) == second[0][1].getpixel((9, 9)) == (75, 125, 175, 255)


def test_entire_friend_panel_shares_one_budget_after_game_art_and_counts_real_miniatures(subject, monkeypatch):
    rows = records(8)
    friends = [friend for friend, _url in rows]
    for index, friend in enumerate(friends):
        friend.update(steamid=str(index), personaname=f"Friend {index:02}")
        if index < 2:
            friend.update(gameid=str(730 + index), gameextrainfo=f"Game {index}")
    seed(subject.plugin, rows[-1][1])
    events, loads, acquired = [], [], []

    def game_art(_data, _appid, size):
        events.append("game")
        image = Image.new("RGB", size if isinstance(size, tuple) else (size, size))
        acquired.append(image)
        return image

    original_request = subject.client_type.request_bytes

    def request(client, *args, **kwargs):
        events.append("avatar")
        return original_request(client, *args, **kwargs)

    def avatars(plugin, selected, size):
        loads.append((selected, size))
        result = friend_avatars.load_mosaic_avatars(plugin, selected, size)
        acquired.extend(image for _friend, image in result)
        return result

    monkeypatch.setattr(subject.plugin, "_game_square_icon", game_art)
    monkeypatch.setattr(subject.plugin, "_game_background", game_art)
    monkeypatch.setattr(subject.client_type, "request_bytes", request)
    monkeypatch.setattr(console_renderer, "load_mosaic_avatars", avatars)
    monkeypatch.setattr(subject.plugin, "_avatar_image", lambda *_args, **_kwargs: pytest.fail("Legacy avatar fetch"))
    console = _Console(subject.plugin, {"friends": friends, "online_friend_count": 13, "friend_count": 57})
    labels = []
    monkeypatch.setattr(console, "text", lambda _box, text, *_args, **_kwargs: labels.append(text))
    console.friends()

    assert subject.clock.now == 108 and len(subject.calls) == 4
    assert events == ["game"] * 4 + ["avatar"] * 4
    assert len(loads) == 1 and loads[0] == (rows, 46)
    # Two playing rows and a six-person crowd (a portrait where one was obtained, a ringed
    # place otherwise); only the five online friends beyond the listed eight remain a number.
    assert "+5" in labels and not any(text.endswith("more…") for text in labels)
    assert all(client.closed for client in subject.clients)
    for image in acquired:
        with pytest.raises(ValueError, match="closed"):
            image.getpixel((0, 0))


def test_friend_panel_without_portrait_urls_never_requests_legacy_media(subject, monkeypatch):
    friends = [{"steamid": str(index), "personaname": f"Friend {index}", "personastate": 1}
               for index in range(8)]
    monkeypatch.setattr(subject.plugin, "_avatar_image", lambda *_args, **_kwargs: pytest.fail("Legacy avatar fetch"))
    monkeypatch.setattr(console_renderer, "load_mosaic_avatars",
                        lambda *_args, **_kwargs: pytest.fail("No portrait URLs should not start an optional batch"))
    console = _Console(subject.plugin, {"friends": friends, "online_friend_count": 8, "friend_count": 57})
    labels = []
    monkeypatch.setattr(console, "text", lambda _box, text, *_args, **_kwargs: labels.append(text))
    console.friends()

    # Missing portrait media must not hide identities that fit in the adaptive grid.
    assert all(labels.count(friend["personaname"]) == 1 for friend in friends)
    assert not any("more" in label for label in labels)
    assert subject.clock.now == 100 and subject.clients == [] and subject.calls == []


def test_cancellation_closes_portraits_loaded_before_the_canceled_request(subject, monkeypatch):
    rows = records(2)
    seed(subject.plugin, rows[0][1])
    created = []
    original_new = friend_avatars.Image.new

    def new(mode, *args, **kwargs):
        image = original_new(mode, *args, **kwargs)
        if mode == "RGBA":
            created.append(image)
        return image

    def cancel(_self, _method, _url, **_kwargs):
        subject.parent.cancel_event.set()
        raise TaskCancelled("owner canceled")

    monkeypatch.setattr(friend_avatars.Image, "new", new)
    monkeypatch.setattr(subject.client_type, "request_bytes", cancel)
    with pytest.raises(TaskCancelled):
        friend_avatars.load_mosaic_avatars(subject.plugin, rows, 46)
    assert len(created) == 1
    with pytest.raises(ValueError, match="closed"):
        created[0].getpixel((20, 20))
    assert subject.clients[0].closed
