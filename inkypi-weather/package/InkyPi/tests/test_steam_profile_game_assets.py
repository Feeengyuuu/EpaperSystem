"""Offline source-identity, cache and resource limits for Steam artwork."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import os
import threading
import time

from PIL import Image
import pytest
import requests

from plugins.steam_profile_dashboard.game_assets import SteamGameAssets
from plugins.steam_profile_dashboard import game_assets
from runtime.long_task_executor import InstanceIdentity, bind_long_task_runtime
from runtime.refresh_contracts import TaskCancelled, TaskContext


APPID = "9876543"  # Intentionally not a game used in the design screenshot.
HEADER = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{APPID}/abcdef012345/header.jpg?t=9"
ICON_HASH = "a" * 40
ICON = f"https://shared.fastly.steamstatic.com/community_assets/images/apps/{APPID}/{ICON_HASH}.jpg"


class Response:
    def __init__(self, payload=b"", status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert self.responses, f"Unexpected network request: {url}"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def picture(size=(460, 215), color=(30, 80, 120)):
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return Response(stream.getvalue())


def details(appid=APPID, **overrides):
    data = {"steam_appid": int(appid), "header_image": HEADER, **overrides}
    return Response(json.dumps({APPID: {"success": True, "data": data}}).encode())


def hub(url=ICON):
    return Response(f'<div class="apphub_AppIcon"><img src="{url}"></div>'.encode())


def expire(tmp_path, *fields):
    path = tmp_path / f"{APPID}.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    for field in fields:
        meta[field] = 0
    path.write_text(json.dumps(meta), encoding="utf-8")


def test_future_app_uses_returned_hashed_header_and_reuses_source_for_sizes(tmp_path):
    metadata, image = details(), picture()
    session = Session(metadata, image)
    assets = SteamGameAssets(tmp_path, session)
    assert assets.get_background(APPID, (390, 150)).size == (390, 150)
    assert assets.get_background(APPID, (185, 60)).size == (185, 60)
    assert len(session.calls) == 2
    assert session.calls[1][1] == HEADER
    assert session.calls[0][2]["params"]["appids"] == APPID
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert metadata.closed and image.closed
    assert assets.diagnostics["downloaded"] == 1
    assert assets.diagnostics["memory_hits"] == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_next_render_reads_disk_without_redownloading_or_metadata_request(tmp_path):
    SteamGameAssets(tmp_path, Session(details(), picture())).get_background(APPID, (200, 80))
    session = Session()
    assets = SteamGameAssets(tmp_path, session)
    assert assets.get_background(APPID, (400, 150)) is not None
    assert not session.calls
    assert assets.diagnostics["disk_hits"] == 1


@pytest.mark.parametrize("metadata", [
    details(appid="123"),
    Response(b'{"9876543":{"success":false}}'),
    Response(b'{"9876543":{"success":1,"data":{"steam_appid":9876543}}}'),
    Response(b'{"9876543":{"success":true,"data":[]}}'),
    Response(b"not json"),
    Response(b"[]"),
    details(header_image=HEADER.replace(APPID, "123")),
    details(header_image=HEADER.replace("shared.akamai.steamstatic.com", "steamstatic.com.evil.test")),
    details(header_image=HEADER.replace("https://", "http://")),
    details(header_image=HEADER.replace("/abcdef012345/", "/%2e%2e/123/")),
])
def test_rejects_unavailable_malformed_or_wrong_identity_metadata(tmp_path, metadata):
    assets = SteamGameAssets(tmp_path, Session(metadata))
    assert assets.get_background(APPID, (200, 80)) is None
    assert assets.diagnostics["downloaded"] == 0
    assert SteamGameAssets(tmp_path, Session()).get_background(APPID, (200, 80)) is None


@pytest.mark.parametrize("status", [404, 429])
def test_failures_have_persistent_negative_cache_and_no_retry_storm(tmp_path, status):
    response = Response(status=status)
    session = Session(response)
    assets = SteamGameAssets(tmp_path, session)
    assert assets.get_background(APPID, (200, 80)) is None
    assert assets.get_background(APPID, (300, 100)) is None
    assert response.closed
    assert len(session.calls) == 1
    assert SteamGameAssets(tmp_path, Session()).get_background(APPID, (200, 80)) is None
    expire(tmp_path, "background_retry_after")
    assert SteamGameAssets(tmp_path, Session(details(), picture())).get_background(APPID, (200, 80)) is not None


def test_header_download_404_is_negatively_cached(tmp_path):
    session = Session(details(), Response(status=404))
    assert SteamGameAssets(tmp_path, session).get_background(APPID, (200, 80)) is None
    assert SteamGameAssets(tmp_path, Session()).get_background(APPID, (200, 80)) is None


def test_offline_expired_same_game_cache_survives(tmp_path):
    SteamGameAssets(tmp_path, Session(details(), picture())).get_background(APPID, (200, 80))
    expire(tmp_path, "background_checked_at", "background_image_at")
    session = Session(requests.ConnectionError("offline"), requests.ConnectionError("offline"))
    assets = SteamGameAssets(tmp_path, session)
    cached = assets.get_background(APPID, (200, 80))
    assert cached.getpixel((100, 40))[:3] == (30, 80, 120)
    assert assets.diagnostics["disk_hits"] == 1


def test_never_reuses_a_different_games_cache(tmp_path):
    SteamGameAssets(tmp_path, Session(details(), picture())).get_background(APPID, (200, 80))
    assert SteamGameAssets(tmp_path, Session(Response(b'{"123":{"success":false}}'))).get_background("123", (200, 80)) is None
    path = tmp_path / f"{APPID}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["appid"] = "123"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert SteamGameAssets(tmp_path, Session(), budget_seconds=0).get_background(APPID, (200, 80)) is None


def test_icon_hash_uses_same_app_record_and_independent_asset(tmp_path):
    session = Session(picture((32, 32), (200, 70, 40)))
    assets = SteamGameAssets(tmp_path, session)
    icon = assets.get_icon(APPID, (40, 40), {"appid": int(APPID), "img_icon_url": ICON_HASH})
    assert icon.size == (40, 40)
    assert icon.getpixel((20, 20))[:3] == (200, 70, 40)
    assert session.calls[0][1] == ICON
    assert SteamGameAssets(tmp_path, Session()).get_icon(APPID, (25, 25)) is not None


def test_missing_or_wrong_game_record_falls_back_to_bound_apphub_icon(tmp_path):
    session = Session(hub(), picture((32, 32)))
    assets = SteamGameAssets(tmp_path, session)
    assert assets.get_icon(APPID, (30, 30), {"appid": 123, "img_icon_url": "b" * 40}) is not None
    assert session.calls[0][1] == f"https://steamcommunity.com/app/{APPID}/"
    assert session.calls[1][1] == ICON


def test_stale_api_icon_hash_uses_current_apphub_then_keeps_cache(tmp_path):
    stale_record = {"appid": APPID, "img_icon_url": "b" * 40}
    session = Session(Response(status=404), hub(), picture((32, 32)))
    assert SteamGameAssets(tmp_path, session).get_icon(APPID, (30, 30), stale_record) is not None
    assert len(session.calls) == 3
    assert session.calls[2][1] == ICON
    assert SteamGameAssets(tmp_path, Session()).get_icon(APPID, (40, 40), stale_record) is not None


def test_apphub_repeating_missing_hash_does_not_retry_image(tmp_path):
    record = {"appid": APPID, "img_icon_url": ICON_HASH}
    session = Session(Response(status=404), hub())
    assert SteamGameAssets(tmp_path, session).get_icon(APPID, (30, 30), record) is None
    assert len(session.calls) == 2
    assert SteamGameAssets(tmp_path, Session()).get_icon(APPID, (30, 30), record) is None


@pytest.mark.parametrize("body", [
    f'<img src="{ICON}">',
    f'<div class="apphub_AppIcon"><img src="{ICON.replace(APPID, "123")}"></div>',
    f'<div class="apphub_AppIcon"><img src="{HEADER}"></div>',
])
def test_apphub_icons_reject_other_games_and_cover_images(tmp_path, body):
    assert SteamGameAssets(tmp_path, Session(Response(body.encode()))).get_icon(APPID, (30, 30)) is None


def test_a_banner_cannot_be_used_as_a_square_logo(tmp_path):
    assert SteamGameAssets(tmp_path, Session(hub(), picture())).get_icon(APPID, (30, 30)) is None


def test_cache_only_budget_does_not_start_network(tmp_path):
    assets = SteamGameAssets(tmp_path, Session(), budget_seconds=0)
    assert assets.get_background(APPID, (200, 80)) is None
    assert assets.diagnostics["requests"] == 0
    assert assets.diagnostics["budget_skips"] > 0


def test_max_games_is_bounded_and_invalid_appids_never_form_paths(tmp_path):
    assets = SteamGameAssets(tmp_path, Session(), budget_seconds=0, max_games=1)
    for value in ("../123", "0", "0123", 1.5, True, "123456789012"):
        assert assets.get_icon(value, (30, 30)) is None
    assert assets.get_icon(APPID, (30, 30)) is None
    assert assets.get_icon("123", (30, 30)) is None
    assert assets.diagnostics["rejected"] == 6
    assert len(assets._metadata) == 1


def test_shared_instance_fetches_a_source_only_once_under_concurrency(tmp_path):
    session = Session(details(), picture())
    assets = SteamGameAssets(tmp_path, session)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: assets.get_background(APPID, (200, 80)), range(3)))
    assert all(result is not None for result in results)
    assert len(session.calls) == 2


def test_declared_oversize_response_and_corrupt_images_are_closed(tmp_path):
    bad = Response(b"not an image", headers={"Content-Length": str(4 * 1024 * 1024)})
    assert SteamGameAssets(tmp_path, Session(details(), bad)).get_background(APPID, (200, 80)) is None
    assert bad.closed
    expire(tmp_path, "background_image_retry_after")
    corrupt = Response(b"not an image")
    assert SteamGameAssets(tmp_path, Session(corrupt)).get_background(APPID, (200, 80)) is None
    assert corrupt.closed


def test_cancellation_propagates_instead_of_turning_into_missing_art(tmp_path):
    event = threading.Event()
    event.set()
    context = TaskContext(event, time.monotonic() + 10)
    with pytest.raises(TaskCancelled):
        SteamGameAssets(tmp_path, Session(), context=context).get_background(APPID, (200, 80))


def test_default_provider_inherits_active_runtime_cancellation_and_deadline(tmp_path):
    event = threading.Event()
    context = TaskContext(event, time.monotonic() + 1)
    with bind_long_task_runtime(context, InstanceIdentity("steam", 1, 1)):
        assets = SteamGameAssets(tmp_path, Session())
        assert assets.context.deadline_monotonic == context.deadline_monotonic
        event.set()
        with pytest.raises(TaskCancelled):
            assets.get_background(APPID, (200, 80))


@pytest.mark.parametrize("max_games,max_bytes,remaining", [
    (2, 1024 * 1024, {"125", "126"}),
    (64, 1, {"126"}),
])
def test_cache_growth_prunes_owned_old_groups_preserving_visible_and_unknown_files(
    tmp_path, monkeypatch, max_games, max_bytes, remaining,
):
    for appid in ("123", "124", "125"):
        with SteamGameAssets(tmp_path, Session(picture((32, 32)))) as assets:
            assert assets.get_icon(appid, (30, 30), {"appid": appid, "img_icon_url": ICON_HASH}) is not None
    old = time.time() - 7200
    for index, path in enumerate(sorted(tmp_path.iterdir())):
        os.utime(path, (old + index, old + index))
    unknown = tmp_path / "notes.txt"
    unknown.write_text("user file", encoding="utf-8")
    invalid = tmp_path / "777.json"
    invalid.write_text('{"appid":"888","version":1}', encoding="utf-8")
    monkeypatch.setattr(game_assets, "CACHE_MAX_GAMES", max_games)
    monkeypatch.setattr(game_assets, "CACHE_MAX_BYTES", max_bytes)
    with SteamGameAssets(tmp_path, Session(picture((32, 32)))) as assets:
        result = assets.get_icon("126", (30, 30), {"appid": "126", "img_icon_url": ICON_HASH})
        assert result is not None
    assert {path.stem for path in tmp_path.glob("*.json") if path.stem != "777"} == remaining
    assert {path.name.split("-")[0] for path in tmp_path.glob("*.png")} == remaining
    assert unknown.read_text(encoding="utf-8") == "user file"
    assert invalid.exists()
    assert result.size == (30, 30)  # close releases originals, never returned copies.
