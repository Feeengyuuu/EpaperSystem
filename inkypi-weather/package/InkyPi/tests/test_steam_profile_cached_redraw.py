"""Provider-free Steam recomposition preserves source and native DATA state."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time

from PIL import Image
import pytest

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.plugin_manifest import PluginManifest
from plugins.steam_profile_dashboard import game_assets
from plugins.steam_profile_dashboard import steam_profile_dashboard as steam_module
from plugins.steam_profile_dashboard.steam_profile_dashboard import SteamProfileDashboard
from utils.theme_utils import get_theme_palette


OWNER = "76561198176386838"
FRIEND = "76561198000000042"
OLD_STYLE = "midnight-console-official-assets-v36"
ICON_HASH = "a" * 40
SETTINGS = {"steamId": OWNER, "friendRemarks": f"{FRIEND}=Chao Chao"}


def forbidden(*_args, **_kwargs):
    pytest.fail("cached Steam display must not use credentials, providers, pruning or source writes")


class Device:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, _key, default=None):
        return default

    load_env_key = forbidden


def theme(mode="day"):
    return {"mode": mode, "requested_mode": mode, "palette": get_theme_palette(mode)}


def subject(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "source"))
    plugin = SteamProfileDashboard({"id": "steam_profile_dashboard"})
    for name in ("_get_dashboard_data", "_fetch_dashboard_data", "_write_cache", "_write_steam_profile_context"):
        monkeypatch.setattr(plugin, name, forbidden)
    monkeypatch.setattr(steam_module, "get_http_session", forbidden)
    monkeypatch.setattr(steam_module, "prune_resource_images", forbidden)
    monkeypatch.setattr(game_assets, "create_single_attempt_http_client", forbidden)
    monkeypatch.setattr(game_assets.SteamGameAssets, "_save_metadata", forbidden)
    monkeypatch.setattr(game_assets.SteamGameAssets, "_prune_cache", forbidden)
    return plugin


def seed(plugin, *, settings=None, style=OLD_STYLE, mode="day", age=5, owner=OWNER, now=None):
    settings = SETTINGS if settings is None else settings
    now = time.time() if now is None else now
    root = plugin.cache_dir(leaf=".steam_profile_dashboard_cache", create=True)
    key = plugin._cache_key(dict(settings, _theme_mode=mode), (800, 480), OWNER, style_version=style)
    record = {"appid": 730, "name": "Counter-Strike 2", "playtime_forever": 3600,
              "playtime_2weeks": 120, "img_icon_url": "b" * 40}
    data = {
        "profile": {"steamid": owner, "personaname": "AshenOne", "personastate": 1,
                    "gameid": "730", "gameextrainfo": "Counter-Strike 2", "avatarfull": "https://avatars.steamstatic.com/owner.jpg"},
        "friends": [{"steamid": FRIEND, "personaname": "Public name", "personastate": 1,
                     "avatarfull": "https://avatars.steamstatic.com/friend.jpg"}],
        "recent_games": [record], "owned_games": [record], "spotlight_game": record,
        "friend_count": 57, "online_friend_count": 1, "level": 103,
        "badges": {"badges": [{}], "player_xp": 123},
        "updated_at": "saved source timestamp", "refresh_mode": "full", "api_calls": 10,
    }
    entry = {"steam_id": owner, "status_updated_at": now - age, "full_updated_at": now - age,
             "image_path": str(root / f"{key}.png"), "data": data}
    (root / f"{key}.json").write_text(json.dumps(entry), encoding="utf-8")
    Image.new("RGB", (800, 480), (255, 0, 255)).save(root / f"{key}.png")
    media = root / "official_games"
    media.mkdir(exist_ok=True)
    icon_url = f"https://shared.fastly.steamstatic.com/community_assets/images/apps/730/{ICON_HASH}.jpg"
    background_url = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/730/header.jpg"
    (media / "730.json").write_text(json.dumps({"appid": "730", "version": 1,
        "icon_url": icon_url, "icon_image_url": icon_url, "icon_checked_at": 1, "icon_image_at": 1,
        "background_url": background_url, "background_image_url": background_url,
        "background_checked_at": 1, "background_image_at": 1}), encoding="utf-8")
    Image.new("RGB", (32, 32), (220, 100, 40)).save(media / "730-icon.png")
    Image.new("RGB", (460, 215), (130, 110, 70)).save(media / "730-background.png")
    for url in (data["profile"]["avatarfull"], data["friends"][0]["avatarfull"]):
        Image.new("RGB", (64, 64), (50, 100, 150)).save(plugin._avatar_cache_path(url))
    return root, entry


def tree_snapshot(root):
    return {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("age,provenance", [(5, SourceProvenance.FRESH_CACHE), (4000, SourceProvenance.STALE_CACHE)])
@pytest.mark.parametrize("source_theme,display_theme", [("day", "day"), ("night", "day"), ("day", "night")])
@pytest.mark.parametrize("source_style", [OLD_STYLE, "midnight-console-black-base-aligned-v37"])
def test_compatible_styles_recompose_with_readonly_media_and_original_timestamps(
    tmp_path, monkeypatch, age, provenance, source_theme, display_theme, source_style,
):
    plugin = subject(tmp_path, monkeypatch)
    root, entry = seed(plugin, mode=source_theme, age=age, style=source_style)
    before = tree_snapshot(root)
    captured = []
    original = plugin._render_dashboard

    def capture(data, *args):
        captured.append(deepcopy(data))
        return original(data, *args)

    monkeypatch.setattr(plugin, "_render_dashboard", capture)
    image = plugin.render_cached_display(SETTINGS, Device(), resolved_theme_context=theme(display_theme))
    assert image.size == (800, 480)
    assert image.getpixel((0, 0)) != (255, 0, 255)
    assert read_source_provenance(image) is provenance
    assert image.info["inkypi_theme_mode"] == display_theme
    assert image.info["steam_profile_status_updated_at"] == entry["status_updated_at"]
    assert image.info["steam_profile_full_updated_at"] == entry["full_updated_at"]
    assert captured[0]["updated_at"] == "saved source timestamp"
    assert captured[0]["friends"][0]["_inkypi_friend_remark"] == "Chao Chao"
    assert captured[0]["_cached_source_stale"] is (provenance is SourceProvenance.STALE_CACHE)
    assert plugin._game_assets.diagnostics["requests"] == 0
    assert plugin._game_assets.diagnostics["disk_hits"] == 2
    assert set(plugin._game_assets.sources) == {"730:background", "730:icon"}
    assert tree_snapshot(root) == before
    assert plugin._media_read_only is False


@pytest.mark.parametrize("changed", [{"steamId": "76561198000000003"}, {"friendLimit": 3},
                                      {"includeFriends": "false"}, {"language": "english"},
                                      {"friendRemarks": f"{FRIEND}=Different"}])
def test_other_account_or_settings_never_reuse_existing_cache(tmp_path, monkeypatch, changed):
    plugin = subject(tmp_path, monkeypatch)
    root, _ = seed(plugin)
    before = tree_snapshot(root)
    with pytest.raises(RuntimeError, match="缓存"):
        plugin.render_cached_display(dict(SETTINGS, **changed), Device(), resolved_theme_context=theme())
    assert tree_snapshot(root) == before


def test_wrong_owner_payload_is_rejected_even_at_expected_filename(tmp_path, monkeypatch):
    plugin = subject(tmp_path, monkeypatch)
    seed(plugin, owner="76561198000000003")
    with pytest.raises(RuntimeError, match="缓存"):
        plugin.render_cached_display(SETTINGS, Device(), resolved_theme_context=theme())


def test_missing_cache_does_not_create_directories_or_contact_providers(tmp_path, monkeypatch):
    plugin = subject(tmp_path, monkeypatch)
    expected = plugin.cache_dir(leaf=".steam_profile_dashboard_cache", create=False)
    with pytest.raises(RuntimeError, match="缓存"):
        plugin.render_cached_display(SETTINGS, Device(), resolved_theme_context=theme())
    assert not expected.exists()


def test_each_display_redraw_deals_a_new_avatar_frame_from_the_data_dir(tmp_path, monkeypatch):
    from plugins.steam_profile_dashboard import avatar_frame

    monkeypatch.setattr(avatar_frame, "_memory_states", {})
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    plugin = subject(tmp_path, monkeypatch)
    root, _ = seed(plugin)
    before = tree_snapshot(root)
    dealt = []
    original = avatar_frame.load_avatar_frame
    monkeypatch.setattr(avatar_frame, "load_avatar_frame",
                        lambda frame_id: dealt.append(frame_id) or original(frame_id))

    rails = [plugin.render_cached_display(SETTINGS, Device(), resolved_theme_context=theme())
             .crop((0, 0, 182, 178)) for _ in range(3)]

    assert len(set(dealt)) == 3
    assert rails[0].tobytes() != rails[1].tobytes() != rails[2].tobytes()
    state_path = tmp_path / "data" / "plugins" / "steam_profile_dashboard" / ".steam_avatar_frame_rotation.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last"] == dealt[-1] and len(state["queue"]) == 27
    # The rotation is presentation state; the saved Steam source stays untouched.
    assert tree_snapshot(root) == before


def test_current_style_selects_newer_source_and_missing_icons_remain_readonly(tmp_path, monkeypatch):
    plugin = subject(tmp_path, monkeypatch)
    seed(plugin, age=4000)
    root, newest = seed(plugin, style=steam_module.STEAM_DASHBOARD_STYLE_VERSION, age=5)
    # A valid API hash must not make the cache-only icon provider write metadata.
    (root / "official_games" / "730.json").unlink()
    (root / "official_games" / "730-icon.png").unlink()
    for path in root.glob("avatar_*.png"):
        path.unlink()
    before = tree_snapshot(root)
    image = plugin.render_cached_display(SETTINGS, Device(), resolved_theme_context=theme())
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert image.info["steam_profile_status_updated_at"] == newest["status_updated_at"]
    assert plugin._game_assets.diagnostics["missing"] == 2
    assert tree_snapshot(root) == before


@pytest.mark.parametrize("missing_source", [False, True])
def test_native_display_redraw_does_not_advance_data_lane_or_last_good_cache(tmp_path, monkeypatch, missing_source):
    import refresh_task as runtime_module
    from refresh_task import RefreshTask
    from runtime.refresh_contracts import CommandSource, JobStatus, RefreshIntent
    from runtime.refresh_policy import ResourceSample
    from runtime.runtime_state import RefreshLane
    from tests.test_refresh_task import (
        PresentationTransactionDisplayManager, RuntimeClock, RuntimeDeviceConfig,
        _queue_and_process, _runtime_playlist, _runtime_plugin_data, _write_runtime_theme_cache,
    )

    plugin = subject(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    root, _ = seed(plugin, age=4000, now=now.timestamp())
    if missing_source:
        for path in root.glob("*.json"):
            path.unlink()
    source_before = tree_snapshot(root)
    data = _runtime_plugin_data("steam_profile_dashboard", "Steam", latest_refresh_time=None)
    data["plugin_settings"].update(dict(SETTINGS, themeMode="day"))
    playlist = _runtime_playlist(data)
    device = RuntimeDeviceConfig(tmp_path / "display", [playlist])
    device.config.update({"theme_mode": "day", "active_theme": "day"})
    device.get_resolution = lambda: (800, 480)
    device.load_env_key = forbidden
    manifest = PluginManifest.from_path(Path(steam_module.__file__).with_name("plugin-info.json"))
    assert manifest.capabilities.supports_cached_display_redraw
    plugin.config = {"id": "steam_profile_dashboard", "_manifest": manifest}
    device.get_plugin = lambda _plugin_id: plugin.config
    clock = RuntimeClock(wall=now.timestamp())
    display = PresentationTransactionDisplayManager()
    task = RefreshTask(device, display, clock=clock.monotonic, wall_clock=clock.wall_time)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(available_mb=512, swap_percent=0))
    monkeypatch.setattr(runtime_module, "get_plugin_instance", lambda _config: plugin)
    instance = playlist.plugins[0].snapshot()
    old_image = _write_runtime_theme_cache(task, instance, "day", Image.new("RGB", (800, 480), (255, 0, 255)))
    old_bytes = old_image.read_bytes()
    task.runtime_state.record_attempt(instance.instance_uuid, (now - timedelta(minutes=2)).isoformat(), lane=RefreshLane.DATA)
    task.runtime_state.record_failure(instance.instance_uuid, (now - timedelta(minutes=1)).isoformat(),
                                     "existing DATA failure", lane=RefreshLane.DATA)
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    command = task._playlist_command(playlist.name, instance, source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE, force=False, display_cached_only=True,
        cache_theme_mode="day", current_dt=now)
    result = _queue_and_process(task, command)
    if missing_source:
        assert result.job.status is JobStatus.FAILED
        assert not display.calls
    else:
        assert result.job.status is JobStatus.SUCCEEDED
        assert display.calls[-1]["image"].getpixel((0, 0)) != (255, 0, 255)
    after = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert after.data == before.data
    assert after.last_good_cache == before.last_good_cache
    assert after.presentation_request == before.presentation_request
    assert old_image.read_bytes() == old_bytes
    assert tree_snapshot(root) == source_before
