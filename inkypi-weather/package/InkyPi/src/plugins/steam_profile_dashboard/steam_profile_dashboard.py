from utils.resource_cache import cached_resource_image, measured_image_response, prune_resource_images
from pathlib import Path
from plugins.steam_profile_dashboard.console_renderer import render_console
from plugins.steam_profile_dashboard.avatar_frame import next_avatar_frame
from plugins.steam_profile_dashboard.game_assets import SteamGameAssets
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.context_cache import write_context
from utils.app_utils import get_base_ui_font
from utils.http_client import get_http_session
from utils.safe_image import safe_open_image
from utils.secret_redaction import redact_sensitive_text
from utils.theme_utils import get_theme_context, get_theme_palette
from PIL import Image, ImageDraw, ImageOps
from datetime import datetime, timezone
from xml.etree import ElementTree
import hashlib
import html
import json
import logging
import math
import os
import re
import time

logger = logging.getLogger(__name__)

STEAM_API_BASE = "https://api.steampowered.com"
STEAM_PROFILE_ITEM_IMAGE_BASE = "https://shared.akamai.steamstatic.com/community_assets/images/"
STEAM_STORE_APPDETAILS = "https://store.steampowered.com/api/appdetails"
STEAM_COMMUNITY_BADGES_URL = "https://steamcommunity.com/profiles/{steam_id}/badges/"
STEAM_COMMUNITY_PROFILE_URL = "https://steamcommunity.com/profiles/{steam_id}/"
DEFAULT_STEAM_ID = "76561198176386838"
STEAM_NAME_DISPLAY_VERSION = "zh-store-full-single-fetch-v1"
STEAM_DASHBOARD_STYLE_VERSION = "midnight-console-whole-avatar-frame-v47"
STEAM_CACHED_DISPLAY_COMPATIBLE_STYLES = (
    "midnight-console-profile-backdrop-v46",
    "midnight-console-game-hue-v45",
    "midnight-console-outlined-hero-title-v44",
    "midnight-console-presence-ordered-friends-v43",
    "midnight-console-breakout-avatar-frames-v42",
    "midnight-console-rotating-avatar-frames-v41",
    "midnight-console-gilded-avatar-v40",
    "midnight-console-borderless-friends-v39",
    "midnight-console-sidebar-generated-icons-v38",
    "midnight-console-black-base-aligned-v37",
    "midnight-console-official-assets-v36",
)
STEAM_AVATAR_FRAME_STATE_FILE = ".steam_avatar_frame_rotation.json"
STEAM_SECTION_WORDMARK_IMAGES = {
    "recent_live": "section_recent_live_wordmark.png",
    "library_friends": "section_library_friends_wordmark.png",
}
STEAM_SECTION_WORDMARK_SIZES = {
    "recent_live": (160, 29),
    "library_friends": (190, 29),
}
STEAM_SECTION_WORDMARK_Y_OFFSET = -3
STEAM_SECTION_WORDMARK_DARK_TINT = (72, 224, 202)
STEAM_SECTION_WORDMARK_LIGHT_TINT = (232, 255, 248)
STEAM_SECTION_WORDMARK_WARM_TINT = (255, 222, 78)
STEAM_PRIMARY_GAME_LANGUAGE = "schinese"
STEAM_SECONDARY_GAME_LANGUAGE = "english"
STEAM_RECENT_GAME_LIMIT = 6
STEAM_LEFT_GAME_ITEM_TARGET = 4
# Distinct games one console render may load: hero 1, recent rows 4, top three 3
# and the games of four visible online friends.
STEAM_CONSOLE_GAME_ASSET_LIMIT = 12
STEAM_BADGE_ICON_LIMIT = 48
OPTIONAL_MEDIA_NEGATIVE_TTL_SECONDS = 6 * 60 * 60
OPTIONAL_MEDIA_NEGATIVE_CACHE_LIMIT = 256

PERSONA_STATES = {
    0: "离线",
    1: "在线",
    2: "忙碌",
    3: "离开",
    4: "打盹",
    5: "想交易",
    6: "想玩游戏",
}
# Friends are listed by presence: anyone in a game first, then these signed-in
# states in order (online, looking to play, looking to trade, busy, away,
# snooze), then offline.
FRIEND_PRESENCE_ORDER = (1, 6, 5, 2, 3, 4)


class SteamProfileDashboard(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["api_key"] = {
            "required": True,
            "service": "Steam",
            "expected_key": "STEAM_API_KEY",
        }
        template_params["style_settings"] = False
        return template_params

    def generate_image(self, settings, device_config):
        # Native DISPLAY_CACHE and theme redraws never obtain credentials or
        # attest a new DATA fetch. They recompose only this account's saved data.
        if settings.get("_theme_render_only") is True:
            return self._render_cached_snapshot(settings, device_config)
        dimensions = self.get_dimensions(device_config)

        api_key = device_config.load_env_key("STEAM_API_KEY")
        if not api_key:
            raise RuntimeError("未配置 Steam API Key。请在 API Keys 中添加 STEAM_API_KEY。")

        steam_id = str(settings.get("steamId") or DEFAULT_STEAM_ID).strip()
        if not steam_id:
            raise RuntimeError("需要填写 SteamID64。")

        theme_context = get_theme_context(device_config)
        status_cache_seconds = self._int_setting(settings, "statusCacheSeconds", 60, 30, 3600)
        full_cache_minutes = self._int_setting(
            settings,
            "fullCacheMinutes",
            self._int_setting(settings, "cacheMinutes", 30, 5, 1440),
            5,
            1440,
        )
        cache_settings = dict(settings or {})
        cache_settings["_theme_mode"] = theme_context.get("mode", "day")
        cache_key = self._cache_key(cache_settings, dimensions, steam_id)
        cache_entry = self._read_cache(cache_key)
        now = time.time()
        force_refresh = self._force_refresh_requested(settings)

        if (
            not force_refresh
            and cache_entry
            and self._cache_is_fresh(
                cache_entry,
                now,
                status_cache_seconds,
                full_cache_minutes,
            )
        ):
            image_path = cache_entry.get("image_path")
            if image_path and os.path.exists(image_path):
                logger.info("Using cached Steam profile dashboard.")
                self._write_steam_profile_context(cache_entry.get("data") or {}, now)
                return attach_source_provenance(
                    safe_open_image(image_path).convert("RGB"),
                    SourceProvenance.FRESH_CACHE,
                )

        try:
            data = self._get_dashboard_data(
                api_key,
                steam_id,
                settings,
                cache_entry,
                now,
                status_cache_seconds,
                full_cache_minutes,
                force_refresh=force_refresh,
            )
            self._apply_friend_remarks(data, settings)
            image = self._render_dashboard(data, dimensions, theme_context)
            image_path = self._cache_image_path(cache_key)
            os.makedirs(os.path.dirname(image_path), exist_ok=True)
            image.save(image_path)
            self._write_cache(cache_key, {
                "status_updated_at": data.get("_status_updated_at", now),
                "full_updated_at": data.get("_full_updated_at", now),
                "image_path": image_path,
                "api_calls": data.get("api_calls", 0),
                "steam_id": steam_id,
                "persona": data.get("profile", {}).get("personaname", ""),
                "data": self._json_safe(data),
            })
            self._write_steam_profile_context(data, now)
            provenance = (
                SourceProvenance.FRESH_CACHE
                if data.get("refresh_mode") == "cache"
                else SourceProvenance.LIVE
            )
            return attach_source_provenance(image, provenance)
        except Exception as e:
            safe_error = redact_sensitive_text(e)
            logger.error("Steam Profile Dashboard failed: %s", safe_error)
            if cache_entry and cache_entry.get("image_path") and os.path.exists(cache_entry["image_path"]):
                logger.warning("Using stale Steam profile dashboard cache.")
                image = safe_open_image(cache_entry["image_path"]).convert("RGB")
                image.info["inkypi_skip_cache"] = True
                return attach_source_provenance(
                    image,
                    SourceProvenance.STALE_CACHE,
                )
            public_error = RuntimeError(
                f"Steam 个人资料看板生成失败：{safe_error}"
            )
        # Raise after leaving the handler so the private requests exception is
        # not retained in __context__ for callers that inspect exception objects.
        raise public_error

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        return self.render_themed_image(
            settings, device_config, theme_render_only=True,
            resolved_theme_context=resolved_theme_context,
        )

    def _compatible_display_cache(self, settings, dimensions, steam_id, theme_context):
        """Read only exact owner/settings keys for this style and the v36 upgrade.

        Day/night is a presentation choice. Both exact theme keys have identical
        source settings; using either avoids provider work on a theme transition.
        No directory scan or unrelated account/configuration fallback is allowed.
        """
        root = self.cache_dir(leaf=".steam_profile_dashboard_cache", create=False)
        modes = [str(theme_context.get("mode") or "day")]
        modes.append("day" if modes[0] == "night" else "night")
        candidates = []
        now = time.time()
        for style in (STEAM_DASHBOARD_STYLE_VERSION, *STEAM_CACHED_DISPLAY_COMPATIBLE_STYLES):
            for mode in modes:
                cache_settings = dict(settings, _theme_mode=mode)
                key = self._cache_key(cache_settings, dimensions, steam_id, style_version=style)
                path = root / f"{key}.json"
                try:
                    if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
                        continue
                    entry = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(entry, dict) or str(entry.get("steam_id") or "") != steam_id:
                        continue
                    data = entry.get("data")
                    if not isinstance(data, dict) or not isinstance(data.get("profile"), dict):
                        continue
                    if str(data["profile"].get("steamid") or "") != steam_id:
                        continue
                    stamps = [entry.get(name) for name in ("status_updated_at", "full_updated_at")]
                    if any(type(stamp) not in (int, float) or not math.isfinite(stamp)
                           or stamp <= 0 or stamp > now + 300 for stamp in stamps):
                        continue
                    candidates.append(entry)
                except (OSError, ValueError):
                    continue
        return max(candidates, key=lambda item: (item["status_updated_at"], item["full_updated_at"]), default=None)

    def _render_cached_snapshot(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)
        theme = get_theme_context(device_config)
        steam_id = str(settings.get("steamId") or DEFAULT_STEAM_ID).strip()
        entry = self._compatible_display_cache(settings, dimensions, steam_id, theme)
        if entry is None:
            # DISPLAY_CACHE failure preserves the already displayed last-good
            # image; it must never silently fetch or invent a fresh account view.
            raise RuntimeError("没有与当前 Steam 账号及设置匹配的资料缓存，保留原显示。")
        status_seconds = self._int_setting(settings, "statusCacheSeconds", 60, 30, 3600)
        full_minutes = self._int_setting(
            settings, "fullCacheMinutes", self._int_setting(settings, "cacheMinutes", 30, 5, 1440), 5, 1440,
        )
        fresh = self._cache_is_fresh(entry, time.time(), status_seconds, full_minutes)
        data = self._clone_data(entry["data"])
        data.update(_status_updated_at=entry["status_updated_at"], _full_updated_at=entry["full_updated_at"],
                    _cached_source_stale=not fresh, refresh_mode="cache", api_calls=0)
        self._apply_friend_remarks(data, settings)
        previous_read_only = getattr(self, "_media_read_only", False)
        self._media_read_only = True
        try:
            image = self._render_dashboard(data, dimensions, theme)
        finally:
            self._media_read_only = previous_read_only
        image.info["steam_profile_status_updated_at"] = entry["status_updated_at"]
        image.info["steam_profile_full_updated_at"] = entry["full_updated_at"]
        if not fresh:
            image.info["inkypi_skip_cache"] = True
        return attach_source_provenance(image, SourceProvenance.FRESH_CACHE if fresh else SourceProvenance.STALE_CACHE)

    def _write_steam_profile_context(self, data, generated_at):
        if not isinstance(data, dict):
            return
        profile = data.get("profile") or {}
        persona = str(profile.get("personaname") or "Steam user").strip()
        current_game = ""
        if profile.get("gameid") or profile.get("gameextrainfo"):
            current_game = self._display_game_name(data, profile.get("gameid"), profile.get("gameextrainfo"))

        recent = []
        for game in (data.get("recent_games") or [])[:STEAM_RECENT_GAME_LIMIT]:
            name = self._display_game_name(data, game.get("appid"), game.get("name"))
            if name:
                recent.append({
                    "name": name,
                    "two_week_hours": self._minutes_to_hours(game.get("playtime_2weeks", 0)),
                    "total_hours": self._minutes_to_hours(game.get("playtime_forever", 0)),
                })

        spotlight = data.get("spotlight_game") or {}
        spotlight_name = ""
        if spotlight:
            spotlight_name = self._display_game_name(data, spotlight.get("appid"), spotlight.get("name"))

        status_text, _status_color = self._persona_text(profile) if profile else ("unknown", None)
        summary_parts = [f"{persona} is {status_text}"]
        if current_game:
            summary_parts.append(f"currently playing {current_game}")
        elif recent:
            summary_parts.append(f"recently played {recent[0]['name']}")
        if spotlight_name:
            summary_parts.append(f"spotlight game {spotlight_name}")

        write_context(
            "steam_profile_dashboard",
            {
                "kind": "steam_profile",
                "source": "Steam Profile Dashboard",
                "summary": "; ".join(summary_parts),
                "current_game": current_game,
                "recent_games": recent,
                "spotlight_game": spotlight_name,
                "friend_count": data.get("friend_count"),
                "online_friend_count": data.get("online_friend_count"),
                "refresh_mode": data.get("refresh_mode"),
            },
            generated_at=datetime.fromtimestamp(float(generated_at), timezone.utc),
            ttl_seconds=90 * 60,
        )

    def _cache_is_fresh(self, cache_entry, now, status_cache_seconds, full_cache_minutes):
        return (
            now - cache_entry.get("status_updated_at", 0) < status_cache_seconds
            and now - cache_entry.get("full_updated_at", 0) < full_cache_minutes * 60
        )

    def _get_dashboard_data(
        self,
        api_key,
        steam_id,
        settings,
        cache_entry,
        now,
        status_cache_seconds,
        full_cache_minutes,
        *,
        force_refresh=False,
    ):
        cached_data = cache_entry.get("data") if isinstance(cache_entry, dict) else None
        full_is_fresh = bool(
            cached_data
            and now - cache_entry.get("full_updated_at", 0) < full_cache_minutes * 60
        )
        status_is_fresh = bool(
            cached_data
            and now - cache_entry.get("status_updated_at", 0) < status_cache_seconds
        )

        if force_refresh or not full_is_fresh:
            data = self._fetch_dashboard_data(api_key, steam_id, settings)
            data["_status_updated_at"] = now
            data["_full_updated_at"] = now
            data["refresh_mode"] = "full"
            return data

        data = self._clone_data(cached_data)
        if status_is_fresh:
            data["api_calls"] = 0
            data["refresh_mode"] = "cache"
            data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            data["_status_updated_at"] = cache_entry.get("status_updated_at", now)
            data["_full_updated_at"] = cache_entry.get("full_updated_at", now)
            return data

        profile, api_calls, warnings = self._fetch_profile_status(api_key, steam_id)
        old_game_id = str((data.get("profile") or {}).get("gameid") or "")
        new_game_id = str(profile.get("gameid") or "")

        data["profile"] = profile
        data["api_calls"] = api_calls
        data["warnings"] = warnings
        data["refresh_mode"] = "live"
        data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        data["_status_updated_at"] = now
        data["_full_updated_at"] = cache_entry.get("full_updated_at", now)

        if data.get("friends"):
            try:
                data["api_calls"] += self._refresh_cached_friend_statuses(api_key, data)
            except Exception as e:
                logger.warning(
                    "Steam friend status refresh unavailable: %s",
                    redact_sensitive_text(e),
                )
                data.setdefault("warnings", []).append("好友状态不可用")

        if (
            self._bool_setting(settings, "refreshRecentOnGameChange", True)
            and new_game_id
            and new_game_id != old_game_id
        ):
            recent_limit = self._int_setting(settings, "recentLimit", STEAM_RECENT_GAME_LIMIT, 0, 10)
            if recent_limit:
                try:
                    recent_data = self._steam_api(
                        "/IPlayerService/GetRecentlyPlayedGames/v1/",
                        api_key,
                        {"steamid": steam_id, "count": recent_limit},
                    )
                    data["api_calls"] += 1
                    data["recent_games"] = recent_data.get("response", {}).get("games", [])
                except Exception as e:
                    logger.warning(
                        "Steam recent games refresh unavailable after game change: %s",
                        redact_sensitive_text(e),
                    )
                    data.setdefault("warnings", []).append("近期游戏不可用")

            data["spotlight_game"] = self._spotlight_game(
                data.get("profile", {}),
                data.get("recent_games", []),
                data.get("owned_games", []),
            )
            data["app_details"] = {}
            if self._bool_setting(settings, "includeAppDetails", True) and data.get("spotlight_game"):
                try:
                    data["app_details"] = self._fetch_store_appdetails(data["spotlight_game"].get("appid"), settings)
                    data["api_calls"] += 1
                except Exception as e:
                    logger.warning(
                        "Steam app details refresh unavailable after game change: %s",
                        redact_sensitive_text(e),
                    )
                    data.setdefault("warnings", []).append("商店详情不可用")

        data["api_calls"] += self._enrich_localized_game_names(data)
        return data

    def _refresh_cached_friend_statuses(self, api_key, data):
        friend_ids = [
            str(friend.get("steamid"))
            for friend in data.get("friends", [])[:100]
            if friend.get("steamid")
        ]
        if not friend_ids:
            return 0

        summary = self._steam_api(
            "/ISteamUser/GetPlayerSummaries/v2/",
            api_key,
            {"steamids": ",".join(friend_ids)},
        )
        players = summary.get("response", {}).get("players", [])
        if players:
            data["friends"] = self._sort_friends(players)
            data["online_friend_count"] = sum(1 for friend in players if int(friend.get("personastate", 0) or 0) != 0)
        return 1

    def _fetch_profile_status(self, api_key, steam_id):
        warnings = []
        try:
            summary = self._steam_api(
                "/ISteamUser/GetPlayerSummaries/v2/",
                api_key,
                {"steamids": steam_id},
            )
            players = summary.get("response", {}).get("players", [])
            if not players:
                raise RuntimeError("未找到 Steam 个人资料。")
            profile = self._reconcile_community_presence(players[0], steam_id)
            return profile, 1, warnings
        except Exception as e:
            warnings.append("实时状态不可用")
            public_error = RuntimeError(
                f"Steam 实时状态不可用：{redact_sensitive_text(e)}"
            )
        raise public_error

    def _fetch_dashboard_data(self, api_key, steam_id, settings):
        api_calls = 0
        warnings = []

        def call(path, params, required=False):
            nonlocal api_calls
            api_calls += 1
            try:
                return self._steam_api(path, api_key, params)
            except Exception as e:
                message = f"{path.split('/')[-2]} 不可用"
                logger.warning("%s: %s", message, redact_sensitive_text(e))
                warnings.append(message)
                if required:
                    raise
                return {}

        summary = call(
            "/ISteamUser/GetPlayerSummaries/v2/",
            {"steamids": steam_id},
            required=True,
        )
        players = summary.get("response", {}).get("players", [])
        if not players:
            raise RuntimeError("未找到 Steam 个人资料。")
        profile = self._reconcile_community_presence(players[0], steam_id)

        level_data = call("/IPlayerService/GetSteamLevel/v1/", {"steamid": steam_id})
        badges_data = {}
        if self._bool_setting(settings, "includeBadges", True):
            badges_data = call("/IPlayerService/GetBadges/v1/", {"steamid": steam_id})

        owned_data = call(
            "/IPlayerService/GetOwnedGames/v1/",
            {
                "steamid": steam_id,
                "include_appinfo": 1,
                "include_played_free_games": 1,
            },
        )
        recent_limit = self._int_setting(settings, "recentLimit", STEAM_RECENT_GAME_LIMIT, 0, 10)
        recent_data = {}
        if recent_limit:
            recent_data = call(
                "/IPlayerService/GetRecentlyPlayedGames/v1/",
                {"steamid": steam_id, "count": recent_limit},
            )

        equipped = call("/IPlayerService/GetProfileItemsEquipped/v1/", {"steamid": steam_id})

        bans_data = {}
        if self._bool_setting(settings, "includeBans", True):
            bans_data = call("/ISteamUser/GetPlayerBans/v1/", {"steamids": steam_id})

        friends = []
        friend_count = None
        online_friend_count = None
        if self._bool_setting(settings, "includeFriends", True):
            friend_limit = self._int_setting(settings, "friendLimit", 100, 0, 100)
            friend_list = call(
                "/ISteamUser/GetFriendList/v1/",
                {"steamid": steam_id, "relationship": "friend"},
            )
            raw_friends = friend_list.get("friendslist", {}).get("friends", [])
            friend_count = len(raw_friends)
            friend_ids = [str(friend.get("steamid")) for friend in raw_friends[:friend_limit] if friend.get("steamid")]
            if friend_ids:
                friend_summary = call(
                    "/ISteamUser/GetPlayerSummaries/v2/",
                    {"steamids": ",".join(friend_ids)},
                )
                friend_players = friend_summary.get("response", {}).get("players", [])
                friends = self._sort_friends(friend_players)
                online_friend_count = sum(1 for friend in friend_players if int(friend.get("personastate", 0)) != 0)

        owned_games = owned_data.get("response", {}).get("games", [])
        recent_games = recent_data.get("response", {}).get("games", [])
        badges = badges_data.get("response", {})
        badge_icons = self._fetch_badge_icon_records(steam_id, badges) if badges else []
        bans = self._first(bans_data.get("players", []), {})

        spotlight_game = self._spotlight_game(profile, recent_games, owned_games)
        app_details = {}
        if spotlight_game and self._bool_setting(settings, "includeAppDetails", True):
            api_calls += 1
            details = self._fetch_store_appdetails(spotlight_game.get("appid"), settings)
            if details:
                app_details = details

        data = {
            "profile": profile,
            "level": level_data.get("response", {}).get("player_level"),
            "owned_games": owned_games,
            "recent_games": recent_games,
            "badges": badges,
            "badge_icons": badge_icons,
            "bans": bans,
            "friends": friends,
            "friend_count": friend_count,
            "online_friend_count": online_friend_count,
            "spotlight_game": spotlight_game,
            "app_details": app_details,
            "profile_background": self._profile_background_record(equipped),
            "api_calls": api_calls,
            "warnings": warnings,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        data["api_calls"] += self._enrich_localized_game_names(data)
        return data

    def _steam_api(self, path, api_key, params):
        session = get_http_session()
        payload = {"key": api_key, "format": "json"}
        payload.update(params)
        response = session.get(f"{STEAM_API_BASE}{path}", params=payload, timeout=35)
        response.raise_for_status()
        return response.json()

    def _reconcile_community_presence(self, profile, steam_id):
        """Cross-check an API-reported offline state against Steam Community.

        GetPlayerSummaries can temporarily report personastate=0 while the
        public Steam Community profile already reports the account online.
        Only an explicit online/in-game Community response overrides the API;
        errors and ambiguous responses preserve the original state.
        """
        if not isinstance(profile, dict):
            return profile
        try:
            if int(profile.get("personastate", 0) or 0) != 0:
                return profile
        except (TypeError, ValueError):
            return profile

        normalized_id = str(steam_id or profile.get("steamid") or "").strip()
        if not normalized_id.isdigit():
            return profile

        try:
            session = get_http_session()
            response = session.get(
                STEAM_COMMUNITY_PROFILE_URL.format(steam_id=normalized_id),
                params={"xml": 1},
                timeout=12,
            )
            response.raise_for_status()
            root = ElementTree.fromstring(response.text)
            community_state = str(root.findtext("onlineState") or "").strip().lower()
            if community_state not in {"online", "in-game"}:
                return profile

            reconciled = dict(profile)
            reconciled["personastate"] = 1
            reconciled["_inkypi_presence_source"] = "steam_community_xml"
            if community_state == "in-game" and not reconciled.get("gameextrainfo"):
                game_name = str(root.findtext("./inGameInfo/gameName") or "").strip()
                if game_name:
                    reconciled["gameextrainfo"] = game_name
            logger.info(
                "Steam Community presence corrected API offline state for SteamID %s.",
                normalized_id,
            )
            return reconciled
        except Exception as e:
            logger.warning(
                "Steam Community presence cross-check unavailable: %s",
                redact_sensitive_text(e),
            )
            return profile

    def _fetch_store_appdetails(self, appid, settings):
        if not appid:
            return {}
        language = settings.get("language", "schinese") or "schinese"
        details, _ = self._fetch_store_appdetails_map(
            [appid],
            language,
            "basic,genres,price_overview,metacritic,release_date",
        )
        return details.get(str(appid), {})

    def _fetch_store_appdetails_map(self, appids, language, filters):
        normalized = []
        seen = set()
        for appid in appids or []:
            normalized_appid = self._normalize_appid(appid)
            if normalized_appid and normalized_appid not in seen:
                normalized.append(normalized_appid)
                seen.add(normalized_appid)

        if not normalized:
            return {}, 0

        if len(normalized) > 1:
            result = {}
            calls = 0
            for appid in normalized:
                single_result, single_calls = self._fetch_store_appdetails_map([appid], language, filters)
                result.update(single_result)
                calls += single_calls
            return result, calls

        try:
            session = get_http_session()
            response = session.get(
                STEAM_STORE_APPDETAILS,
                params={
                    "appids": normalized[0],
                    "filters": filters,
                    "l": language,
                },
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return {}, 1

            result = {}
            for appid in normalized:
                entry = payload.get(appid, {})
                if entry.get("success") and isinstance(entry.get("data"), dict):
                    result[appid] = entry["data"]
            return result, 1
        except Exception as e:
            logger.warning(
                "Steam store appdetails unavailable for %s (%s): %s",
                ",".join(normalized),
                language,
                redact_sensitive_text(e),
            )
        return {}, 1

    def _enrich_localized_game_names(self, data):
        appids = self._visible_game_appids(data)
        existing = data.get("localized_game_names")
        if not isinstance(existing, dict):
            existing = {}

        missing = [
            appid for appid in appids
            if not isinstance(existing.get(appid), dict) or not existing[appid].get("display")
        ]
        if not missing:
            data["localized_game_names"] = existing
            return 0

        primary_details, primary_calls = self._fetch_store_appdetails_map(
            missing,
            STEAM_PRIMARY_GAME_LANGUAGE,
            "basic",
        )
        secondary_details, secondary_calls = self._fetch_store_appdetails_map(
            missing,
            STEAM_SECONDARY_GAME_LANGUAGE,
            "basic",
        )

        for appid in missing:
            primary_name = self._clean_game_name((primary_details.get(appid) or {}).get("name"))
            secondary_name = self._clean_game_name((secondary_details.get(appid) or {}).get("name"))
            fallback_name = self._fallback_game_name(data, appid)
            existing[appid] = {
                "schinese": primary_name,
                "english": secondary_name,
                "fallback": fallback_name,
                "display": self._format_game_name(primary_name, secondary_name, fallback_name, appid),
            }

        data["localized_game_names"] = existing
        return primary_calls + secondary_calls

    def _visible_game_appids(self, data):
        appids = []

        def add(appid):
            normalized = self._normalize_appid(appid)
            if normalized and normalized not in appids:
                appids.append(normalized)

        profile = data.get("profile") or {}
        add(profile.get("gameid"))

        for game in (data.get("recent_games") or [])[:STEAM_RECENT_GAME_LIMIT]:
            add(game.get("appid"))

        spotlight = data.get("spotlight_game") or {}
        add(spotlight.get("appid"))

        owned_games = data.get("owned_games") or []
        sorted_games = sorted(owned_games, key=lambda game: game.get("playtime_forever", 0), reverse=True)
        for game in sorted_games[:STEAM_LEFT_GAME_ITEM_TARGET]:
            add(game.get("appid"))

        online_friends = [friend for friend in data.get("friends", []) if int(friend.get("personastate", 0)) != 0]
        for friend in online_friends[:4]:
            add(friend.get("gameid"))

        return appids

    def _fallback_game_name(self, data, appid):
        appid = self._normalize_appid(appid)
        if not appid:
            return ""

        profile = data.get("profile") or {}
        if self._normalize_appid(profile.get("gameid")) == appid:
            name = self._clean_game_name(profile.get("gameextrainfo"))
            if name:
                return name

        for collection in ("recent_games", "owned_games"):
            for game in data.get(collection, []) or []:
                if self._normalize_appid(game.get("appid")) == appid:
                    name = self._clean_game_name(game.get("name"))
                    if name:
                        return name

        for friend in data.get("friends", []) or []:
            if self._normalize_appid(friend.get("gameid")) == appid:
                name = self._clean_game_name(friend.get("gameextrainfo"))
                if name:
                    return name

        return ""

    def _display_game_name(self, data, appid=None, fallback=None):
        normalized_appid = self._normalize_appid(appid)
        localized = (data.get("localized_game_names") or {}).get(normalized_appid, {})
        if isinstance(localized, dict) and localized.get("display"):
            return localized["display"]
        return self._format_game_name("", "", fallback, normalized_appid)

    def _format_game_name(self, primary_name, secondary_name, fallback_name, appid):
        primary = self._clean_game_name(primary_name)
        secondary = self._clean_game_name(secondary_name)
        fallback = self._clean_game_name(fallback_name)
        if primary:
            return primary
        if secondary:
            return secondary
        if fallback:
            return fallback
        return f"应用 {appid}" if appid else "未知游戏"

    def _clean_game_name(self, name):
        return " ".join(str(name or "").split())

    def _normalize_appid(self, appid):
        text = str(appid or "").strip()
        return text if text.isdigit() else ""

    @staticmethod
    def _render_palette(theme_context):
        if not isinstance(theme_context, dict) or theme_context.get("mode") != "night":
            return get_theme_palette("day")
        return get_theme_palette(theme_context)

    def _render_dashboard(self, data, dimensions, theme_context=None):
        # One bounded acquisition budget is shared by all visible game slots.
        self._game_assets = None
        try:
            image = render_console(self, data, dimensions, theme_context)
            assets = self._game_assets
            data["game_media"] = {"stats": dict(assets.diagnostics) if assets else {},
                                  "sources": dict(assets.sources) if assets else {}}
            logger.info("Steam console game media: %s", data["game_media"]["stats"])
            return image
        finally:
            if self._game_assets is not None:
                self._game_assets.close()

    def _official_game_assets(self):
        if getattr(self, "_game_assets", None) is None:
            self._game_assets = SteamGameAssets(
                Path(self._cache_dir()) / "official_games",
                max_games=STEAM_CONSOLE_GAME_ASSET_LIMIT,
                read_only=bool(getattr(self, "_media_read_only", False)),
            )
        return self._game_assets

    def _game_background(self, data, appid, size):
        appid = self._normalize_appid(appid)
        return self._official_game_assets().get_background(
            appid, size, record=self._game_record(data, appid),
        ) if appid else None

    def _avatar_frame_state_path(self):
        # Production keeps the rotation across restarts in its data directory;
        # a development tree without INKYPI_DATA_DIR rotates in memory only.
        if not os.getenv("INKYPI_DATA_DIR", "").strip():
            return None
        return self.data_dir(create=False) / STEAM_AVATAR_FRAME_STATE_FILE

    def _next_avatar_frame(self):
        frame = next_avatar_frame(self._avatar_frame_state_path())
        if frame is not None:
            logger.info("Steam avatar frame: %s %s", frame.frame_id, frame.title)
        return frame

    @staticmethod
    def _profile_background_record(equipped):
        """The equipped profile background's still image (Steam also offers a video), or None."""
        background = ((equipped or {}).get("response") or {}).get("profile_background") or {}
        image = str(background.get("image_large") or "").strip()
        if not re.fullmatch(r"items/\d+/[0-9a-f]{40}\.(?:jpg|png)", image):
            return None
        name = str(background.get("item_title") or background.get("name") or "")[:80]
        return {"url": STEAM_PROFILE_ITEM_IMAGE_BASE + image, "name": name}

    def _profile_background_image(self, url):
        """The equipped profile background reduced to the page's height, cached for a week."""
        if not url:
            return None
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        path = os.path.join(self._cache_dir(), f"profilebg_{digest}.png")

        def fetch():
            response = get_http_session().get(url, timeout=25, stream=True)
            image = measured_image_response(response).convert("RGB")
            # A 1920 x 1080 background is kept at 480 px tall: the page never shows more.
            image.thumbnail((4096, 480), Image.Resampling.LANCZOS)
            return image

        read_only = bool(getattr(self, "_media_read_only", False))
        return cached_resource_image(path, fetch, ttl=7 * 24 * 3600, label="steam_profile_background",
                                     read_only=read_only)

    def _profile_avatar_image(self, url, size):
        source = self._cached_profile_media(
            url, self._avatar_cache_path(url), 7 * 24 * 3600, "RGB",
        ) if url else None
        if source is None:
            return self._avatar_image(None, size).convert("RGB")
        try:
            return ImageOps.fit(source, (size, size), method=Image.Resampling.LANCZOS)
        finally:
            source.close()

    def _center_text(self, draw, center, text, font, fill):
        if not text:
            return
        cx, cy = center
        bbox = draw.textbbox((0, 0), str(text), font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.text((cx - text_w / 2 - bbox[0], cy - text_h / 2 - bbox[1]), str(text), font=font, fill=fill)

    def _fetch_badge_icon_records(self, steam_id, badges):
        badge_rows = (badges or {}).get("badges", []) if isinstance(badges, dict) else []
        if not steam_id or not badge_rows:
            return []

        try:
            session = get_http_session()
            response = session.get(
                STEAM_COMMUNITY_BADGES_URL.format(steam_id=steam_id),
                params={"l": "schinese"},
                timeout=25,
            )
            response.raise_for_status()
            return self._extract_badge_icon_records(response.text, badges)
        except Exception as e:
            logger.warning(
                "Steam badge icons unavailable: %s",
                redact_sensitive_text(e),
            )
            return []

    def _extract_badge_icon_records(self, page_html, badges):
        page_html = str(page_html or "")
        if not page_html:
            return []

        owned_appids = {
            str(badge.get("appid"))
            for badge in ((badges or {}).get("badges", []) if isinstance(badges, dict) else [])
            if badge.get("appid")
        }
        records = []
        seen_urls = set()
        row_pattern = re.compile(
            r'(<div[^>]+class=["\'][^"\']*\bbadge_row\b[^"\']*["\'][\s\S]*?)(?=<div[^>]+class=["\'][^"\']*\bbadge_row\b[^"\']*["\']|\Z)',
            re.IGNORECASE,
        )

        for match in row_pattern.finditer(page_html):
            fragment = match.group(1)
            appid = self._badge_row_appid(fragment)
            if appid and owned_appids and appid not in owned_appids:
                continue
            icon_url = self._badge_row_icon_url(fragment)
            if not icon_url or icon_url in seen_urls:
                continue
            seen_urls.add(icon_url)
            records.append({"appid": appid, "icon_url": icon_url})
            if len(records) >= STEAM_BADGE_ICON_LIMIT:
                break

        return records

    def _badge_row_appid(self, fragment):
        for pattern in (r"/gamecards/(\d+)", r"[?&]appid=(\d+)"):
            match = re.search(pattern, fragment or "", re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    def _badge_row_icon_url(self, fragment):
        icon_blocks = re.findall(
            r'<div[^>]+class=["\'][^"\']*\bbadge_icon\b[^"\']*["\'][^>]*>[\s\S]*?</div>',
            fragment or "",
            flags=re.IGNORECASE,
        )
        candidates = []
        for block in icon_blocks:
            candidates.extend(self._html_image_sources(block))
        if not candidates:
            candidates = self._html_image_sources(fragment)

        for url in candidates:
            normalized = self._normalize_steam_image_url(url)
            if self._looks_like_badge_icon_url(normalized):
                return normalized
        return ""

    def _html_image_sources(self, fragment):
        fragment = fragment or ""
        sources = []
        for attr in ("src", "data-delayed-image", "data-src", "data-original"):
            sources.extend(
                re.findall(
                    rf'<img\b[^>]*\b{attr}=["\']([^"\']+)["\']',
                    fragment,
                    flags=re.IGNORECASE,
                )
            )
        for srcset in re.findall(r'<img\b[^>]*\bsrcset=["\']([^"\']+)["\']', fragment, flags=re.IGNORECASE):
            for candidate in srcset.split(","):
                url = candidate.strip().split(" ", 1)[0]
                if url:
                    sources.append(url)
        return sources

    def _normalize_steam_image_url(self, url):
        url = html.unescape(str(url or "").strip())
        if url.startswith("//"):
            url = f"https:{url}"
        return url if url.startswith(("http://", "https://")) else ""

    def _looks_like_badge_icon_url(self, url):
        lower = str(url or "").lower()
        if not lower.startswith(("http://", "https://")):
            return False
        if "avatar" in lower:
            return False
        return any(token in lower for token in (
            "/badges/",
            "/economy/image/",
            "/public/images/items/",
            "/community_assets/images/items/",
            "community_assets/images/items/",
            "/public/images/badges/",
            "steamstatic.com/steamcommunity/public/images/",
        ))

    def _cached_profile_media(self, url, path, ttl, mode):
        def fetch():
            response = get_http_session().get(url, timeout=25, stream=True)
            if int(getattr(response, "status_code", 0) or 0) == 404:
                self._remember_optional_media_negative(url)
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                raise RuntimeError("Optional Steam resource returned 404")
            return measured_image_response(response).convert(mode)

        read_only = bool(getattr(self, "_media_read_only", False))
        image = cached_resource_image(path, fetch, ttl=ttl, label="steam_profile_media", read_only=read_only)
        if not read_only:
            prune_resource_images(Path(path).parent, prefixes=("avatar_", "gameicon_", "badgeicon_", "profilebg_"),
                                  max_files=384, max_bytes=32 * 1024 * 1024,
                                  max_age=90 * 24 * 3600, label="steam_profile_media", protected=(path,))
        return image

    def _draw_section_wordmark(self, image, key, x, y):
        wordmark = self._section_wordmark_image(key)
        if wordmark is None:
            return None
        target_size = STEAM_SECTION_WORDMARK_SIZES.get(key, wordmark.size)
        target_w, target_h = int(target_size[0]), int(target_size[1])
        if wordmark.size != (target_w, target_h):
            fitted = ImageOps.contain(wordmark, (target_w, target_h), method=Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 0))
            canvas.alpha_composite(
                fitted,
                ((target_w - fitted.width) // 2, (target_h - fitted.height) // 2),
            )
            wordmark = canvas
        px = int(x)
        py = int(y + STEAM_SECTION_WORDMARK_Y_OFFSET)
        image.paste(wordmark, (px, py), wordmark)
        return (px, py, px + wordmark.width, py + wordmark.height)

    def _section_wordmark_image(self, key):
        image_name = STEAM_SECTION_WORDMARK_IMAGES.get(key)
        if not image_name:
            return None
        path = self.get_plugin_dir(image_name)
        try:
            with Image.open(path) as source:
                return self._readable_section_wordmark(source.convert("RGBA"))
        except Exception as e:
            logger.warning(
                "Steam dashboard section wordmark unavailable: %s: %s",
                image_name,
                redact_sensitive_text(e),
            )
            return None

    def _readable_section_wordmark(self, wordmark):
        if wordmark.mode != "RGBA":
            wordmark = wordmark.convert("RGBA")
        result = Image.new("RGBA", wordmark.size, (255, 255, 255, 0))
        source = wordmark.load()
        target = result.load()
        for py in range(wordmark.height):
            for px in range(wordmark.width):
                r, g, b, a = source[px, py]
                if a == 0:
                    continue
                luminance = (r * 299 + g * 587 + b * 114) // 1000
                if r >= 150 and g >= 105 and b <= 150:
                    target[px, py] = (*STEAM_SECTION_WORDMARK_WARM_TINT, a)
                elif luminance < 118:
                    target[px, py] = (*STEAM_SECTION_WORDMARK_DARK_TINT, a)
                else:
                    target[px, py] = (*STEAM_SECTION_WORDMARK_LIGHT_TINT, a)
        return result

    def _recent_items(self, data):
        items = []
        listed_appids = set()

        def add_game_item(item):
            appid = self._normalize_appid(item.get("appid"))
            if appid:
                listed_appids.add(appid)
            items.append(item)

        def game_item_count():
            return sum(1 for item in items if item.get("appid") and item.get("name"))

        profile = data.get("profile", {})
        if profile.get("gameid") or profile.get("gameextrainfo"):
            name = self._display_game_name(data, profile.get("gameid"), profile.get("gameextrainfo"))
            add_game_item({"prefix": "\u6b63\u5728\u73a9\uff1a", "name": name, "appid": profile.get("gameid")})

        for game in data.get("recent_games", [])[:STEAM_RECENT_GAME_LIMIT]:
            if game_item_count() >= STEAM_LEFT_GAME_ITEM_TARGET:
                break
            name = self._display_game_name(data, game.get("appid"), game.get("name"))
            two_weeks = self._minutes_to_hours(game.get("playtime_2weeks", 0))
            forever = self._minutes_to_hours(game.get("playtime_forever", 0))
            add_game_item({
                "name": name,
                "suffix": f"\uff1a\u8fd12\u5468 {two_weeks}h / \u603b\u8ba1 {forever}h",
                "compact_suffix": f" - {two_weeks}h" if two_weeks else f" - {forever}h",
                "detail": f"\u8fd12\u5468 {two_weeks}h | \u603b\u8ba1 {forever}h",
                "appid": game.get("appid"),
            })

        if game_item_count() < STEAM_LEFT_GAME_ITEM_TARGET:
            owned_games = sorted(
                data.get("owned_games", []) or [],
                key=lambda game: game.get("playtime_forever", 0),
                reverse=True,
            )
            for game in owned_games:
                appid = self._normalize_appid(game.get("appid"))
                if not appid or appid in listed_appids:
                    continue
                name = self._display_game_name(data, game.get("appid"), game.get("name"))
                forever = self._minutes_to_hours(game.get("playtime_forever", 0))
                add_game_item({
                    "prefix": "\u5e38\u73a9\uff1a",
                    "name": name,
                    "suffix": f"\uff1a\u603b\u8ba1 {forever}h",
                    "compact_suffix": f" - {forever}h",
                    "detail": f"\u603b\u8ba1 {forever}h",
                    "appid": game.get("appid"),
                })
                if game_item_count() >= STEAM_LEFT_GAME_ITEM_TARGET:
                    break

        spotlight = data.get("spotlight_game") or {}
        details = data.get("app_details") or {}
        if details:
            genres = ", ".join(genre.get("description", "") for genre in details.get("genres", [])[:2])
            if genres:
                items.append({"text": f"\u7c7b\u578b\uff1a{genres}"})
            if details.get("metacritic", {}).get("score"):
                items.append({"text": f"\u5a92\u4f53\u8bc4\u5206\uff1a{details['metacritic']['score']}"})
        elif spotlight and (spotlight.get("appid") or spotlight.get("name")):
            name = self._display_game_name(data, spotlight.get("appid"), spotlight.get("name"))
            items.append({"prefix": "\u91cd\u70b9\u6e38\u620f\uff1a", "name": name, "appid": spotlight.get("appid")})

        if not items:
            items.append({"text": "\u6ca1\u6709\u516c\u5f00\u7684\u8fd1\u671f\u6e38\u620f\u6570\u636e"})
        return items

    def _top_game_items(self, data):
        items = []
        games = data.get("owned_games", [])
        sorted_games = sorted(games, key=lambda game: game.get("playtime_forever", 0), reverse=True)

        for index, game in enumerate(sorted_games[:3], start=1):
            hours = self._minutes_to_hours(game.get("playtime_forever", 0))
            if hours <= 0:
                continue
            name = self._display_game_name(data, game.get("appid"), game.get("name"))
            items.append({
                "rank": index,
                "name": name,
                "suffix": f"{hours}h",
                "appid": game.get("appid"),
            })
        return items

    @staticmethod
    def _friend_remarks(settings):
        """Parse user-provided remarks; stable SteamIDs survive persona changes."""
        raw = settings.get("friendRemarks") or {}
        if isinstance(raw, str):
            value = raw.strip()
            if value.startswith("{"):
                try:
                    raw = json.loads(value)
                except (ValueError, TypeError):
                    return {}
            else:
                raw = dict(line.split("=", 1) for line in value.splitlines()
                           if "=" in line and not line.lstrip().startswith("#"))
        if not isinstance(raw, dict):
            return {}
        return {str(key).strip(): " ".join(value.split())[:80]
                for key, value in raw.items()
                if re.fullmatch(r"[0-9]{17}", str(key).strip())
                and isinstance(value, str) and value.strip()}

    def _apply_friend_remarks(self, data, settings):
        remarks = self._friend_remarks(settings)
        for friend in data.get("friends") or []:
            friend.pop("_inkypi_friend_remark", None)
            remark = remarks.get(str(friend.get("steamid") or ""))
            if remark:
                friend["_inkypi_friend_remark"] = remark

    def _friend_display_id(self, friend):
        return (self._clean_game_name(friend.get("_inkypi_friend_remark"))
                or self._clean_game_name(friend.get("personaname"))
                or str(friend.get("steamid") or "好友"))

    def _game_square_icon(self, data, appid, size):
        appid = self._normalize_appid(appid)
        return self._official_game_assets().get_icon(
            appid, (size, size), record=self._game_record(data, appid),
        ) if appid else None

    def _optional_media_negative_hit(self, url):
        now = float(getattr(self, "_optional_media_clock", time.monotonic)())
        cache = getattr(self, "_optional_media_negative_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._optional_media_negative_cache = cache
        expires_at = float(cache.get(str(url), 0.0) or 0.0)
        if expires_at > now:
            return True
        cache.pop(str(url), None)
        return False

    def _remember_optional_media_negative(self, url):
        now = float(getattr(self, "_optional_media_clock", time.monotonic)())
        cache = getattr(self, "_optional_media_negative_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._optional_media_negative_cache = cache
        if len(cache) >= OPTIONAL_MEDIA_NEGATIVE_CACHE_LIMIT:
            oldest = min(cache, key=cache.get)
            cache.pop(oldest, None)
        cache[str(url)] = now + OPTIONAL_MEDIA_NEGATIVE_TTL_SECONDS

    def _game_record(self, data, appid):
        game = data.get("spotlight_game") or {}
        if self._normalize_appid(game.get("appid")) == appid:
            return game

        for collection in ("recent_games", "owned_games"):
            for game in data.get(collection, []) or []:
                if self._normalize_appid(game.get("appid")) == appid:
                    return game
        return {}

    def _avatar_image(self, url, size, *, decorative_outline=True):
        avatar = self._cached_profile_media(
            url, self._avatar_cache_path(url), 7 * 24 * 3600, "RGB",
        ) if url else None

        if avatar is None:
            avatar = Image.new("RGB", (size, size), (0, 0, 0))
            draw = ImageDraw.Draw(avatar)
            draw.ellipse((size * 0.26, size * 0.18, size * 0.74, size * 0.62), outline=(255, 255, 255), width=4)
            draw.arc((size * 0.2, size * 0.45, size * 0.8, size * 1.08), 200, 340, fill=(255, 255, 255), width=4)
        else:
            avatar = ImageOps.fit(avatar, (size, size), method=Image.Resampling.LANCZOS)

        mask = Image.new("L", (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, size - 1, size - 1), fill=255)
        result = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        result.paste(avatar, (0, 0), mask)

        if decorative_outline:
            outline = ImageDraw.Draw(result)
            outline.ellipse((2, 2, size - 3, size - 3), outline=(255, 255, 255), width=4)
        return result

    def _persona_text(self, profile):
        if profile.get("gameextrainfo"):
            return ("游戏中", (88, 205, 118))
        state = int(profile.get("personastate", 0) or 0)
        if state == 0:
            return (PERSONA_STATES[state], (120, 132, 146))
        if state == 1:
            return (PERSONA_STATES[state], (88, 205, 118))
        return (PERSONA_STATES.get(state, "在线"), (234, 176, 72))

    def _spotlight_game(self, profile, recent_games, owned_games):
        gameid = profile.get("gameid")
        if gameid:
            for game in recent_games + owned_games:
                if str(game.get("appid")) == str(gameid):
                    return game
            return {"appid": gameid, "name": profile.get("gameextrainfo")}
        if recent_games:
            return recent_games[0]
        if owned_games:
            return max(owned_games, key=lambda game: game.get("playtime_forever", 0))
        return {}

    def _friend_presence_rank(self, friend):
        if friend.get("gameid") or friend.get("gameextrainfo"):
            return 0
        try:
            state = int(friend.get("personastate", 0) or 0)
        except (TypeError, ValueError):
            state = 0
        if state in FRIEND_PRESENCE_ORDER:
            return 1 + FRIEND_PRESENCE_ORDER.index(state)
        # An unrecognised signed-in state still ranks above offline.
        return 1 + len(FRIEND_PRESENCE_ORDER) + (0 if state else 1)

    def _sort_friends(self, players):
        return sorted(
            players,
            key=lambda friend: (
                self._friend_presence_rank(friend),
                str(friend.get("personaname", "")).lower(),
            ),
        )

    def _font(self, size, bold=False):
        return get_base_ui_font(int(size), bold=bool(bold))

    def _text(self, draw, position, text, font, fill):
        draw.text(position, str(text), font=font, fill=fill)

    def _wrap_text(self, draw, text, font, max_width):
        text = str(text or "")
        if not text or self._text_width(draw, text, font) <= max_width:
            return [text] if text else []

        lines = []
        current = ""
        for unit in self._wrap_units(text):
            candidate = current + unit
            if current and self._text_width(draw, candidate, font) > max_width:
                lines.append(current.rstrip())
                if self._text_width(draw, unit, font) > max_width:
                    current = ""
                    for char in unit:
                        char_candidate = current + char
                        if current and self._text_width(draw, char_candidate, font) > max_width:
                            lines.append(current.rstrip())
                            current = char
                        else:
                            current = char_candidate
                else:
                    current = "" if unit.isspace() else unit
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
        return [line for line in lines if line]

    def _wrap_units(self, text):
        units = []
        ascii_word = ""
        for char in str(text or ""):
            if ord(char) < 128 and not char.isspace():
                ascii_word += char
                continue
            if ascii_word:
                units.append(ascii_word)
                ascii_word = ""
            units.append(char)
        if ascii_word:
            units.append(ascii_word)
        return units

    def _line_height(self, draw, font):
        try:
            bbox = draw.textbbox((0, 0), "测试Ag", font=font)
            return max(1, bbox[3] - bbox[1]) + 3
        except Exception:
            return int(getattr(font, "size", 14)) + 4

    def _minutes_to_hours(self, minutes):
        try:
            return int(round(int(minutes) / 60))
        except Exception:
            return 0

    def _text_width(self, draw, text, font):
        text = str(text or "")
        try:
            return draw.textlength(text, font=font)
        except Exception:
            bbox = draw.textbbox((0, 0), text, font=font)
            return bbox[2] - bbox[0]

    def _first(self, items, default=None):
        if isinstance(items, list) and items:
            return items[0]
        return default

    def _int_setting(self, settings, key, default, min_value, max_value):
        try:
            value = int(settings.get(key, default))
        except Exception:
            value = default
        return max(min_value, min(max_value, value))

    def _bool_setting(self, settings, key, default):
        value = settings.get(key)
        if value is None:
            return default
        return str(value).lower() in ("true", "1", "yes", "on")

    def _force_refresh_requested(self, settings):
        return any(
            self._bool_setting(settings or {}, key, False)
            for key in ("forceRefresh", "force_refresh")
        )

    def _cache_dir(self):
        return self.cache_dir(leaf=".steam_profile_dashboard_cache",
                              create=not bool(getattr(self, "_media_read_only", False)))

    def _cache_path(self, cache_key):
        return os.path.join(self._cache_dir(), f"{cache_key}.json")

    def _cache_image_path(self, cache_key):
        return os.path.join(self._cache_dir(), f"{cache_key}.png")

    def _avatar_cache_path(self, url):
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        return os.path.join(self._cache_dir(), f"avatar_{digest}.png")

    def _cache_key(self, settings, dimensions, steam_id, *, style_version=None):
        parts = [
            style_version or STEAM_DASHBOARD_STYLE_VERSION,
            STEAM_NAME_DISPLAY_VERSION,
            steam_id,
            str(dimensions),
            str(settings.get("statusCacheSeconds", 60)),
            str(settings.get("fullCacheMinutes", settings.get("cacheMinutes", 30))),
            str(settings.get("friendLimit", 100)),
            str(settings.get("recentLimit", STEAM_RECENT_GAME_LIMIT)),
            str(settings.get("includeFriends", "true")),
            str(settings.get("refreshRecentOnGameChange", "true")),
            str(settings.get("includeAppDetails", "true")),
            str(settings.get("includeBadges", "true")),
            str(settings.get("includeBans", "true")),
            str(settings.get("language", "schinese")),
            str(settings.get("_theme_mode", "day")),
            json.dumps(self._friend_remarks(settings), sort_keys=True, ensure_ascii=False),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]

    def _read_cache(self, cache_key):
        try:
            with open(self._cache_path(cache_key), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write_cache(self, cache_key, data):
        with open(self._cache_path(cache_key), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _clone_data(self, data):
        return json.loads(json.dumps(data))

    def _json_safe(self, data):
        clean = self._clone_data(data)
        clean.pop("_status_updated_at", None)
        clean.pop("_full_updated_at", None)
        return clean
