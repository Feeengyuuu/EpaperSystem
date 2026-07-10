from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.http_client import get_http_session
from utils.safe_image import safe_open_image, safe_open_image_response
from utils.theme_utils import get_theme_context, get_theme_palette
from PIL import Image, ImageDraw, ImageFont, ImageOps
from datetime import datetime, timezone
import hashlib
import html
import json
import logging
import math
import os
import random
import re
import time

logger = logging.getLogger(__name__)

STEAM_API_BASE = "https://api.steampowered.com"
STEAM_STORE_APPDETAILS = "https://store.steampowered.com/api/appdetails"
STEAM_APP_ICON_URL = "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/{appid}/{icon_hash}.jpg"
STEAM_APP_CAPSULE_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/capsule_184x69.jpg"
STEAM_COMMUNITY_BADGES_URL = "https://steamcommunity.com/profiles/{steam_id}/badges/"
DEFAULT_STEAM_ID = "76561198176386838"
STEAM_NAME_DISPLAY_VERSION = "zh-store-full-single-fetch-v1"
STEAM_DASHBOARD_STYLE_VERSION = "avatar-clean-coverwall-allgameicons-badgerice-v35"
STEAM_BACKGROUND_DAY_IMAGE = "background_day.png"
STEAM_BACKGROUND_NIGHT_IMAGE = "background_night.png"
STEAM_GAME_BACKDROP_IMAGE = "game_backdrop.png"
STEAM_GAME_STRIP_IMAGE = "game_strip.png"
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
STEAM_BADGE_ICON_LIMIT = 48

PERSONA_STATES = {
    0: "离线",
    1: "在线",
    2: "忙碌",
    3: "离开",
    4: "打盹",
    5: "想交易",
    6: "想玩游戏",
}


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

        if cache_entry and self._cache_is_fresh(cache_entry, now, status_cache_seconds, full_cache_minutes):
            image_path = cache_entry.get("image_path")
            if image_path and os.path.exists(image_path):
                logger.info("Using cached Steam profile dashboard.")
                self._write_steam_profile_context(cache_entry.get("data") or {}, now)
                return safe_open_image(image_path).convert("RGB")

        try:
            data = self._get_dashboard_data(
                api_key,
                steam_id,
                settings,
                cache_entry,
                now,
                status_cache_seconds,
                full_cache_minutes,
            )
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
            return image
        except Exception as e:
            logger.error(f"Steam Profile Dashboard failed: {e}")
            if cache_entry and cache_entry.get("image_path") and os.path.exists(cache_entry["image_path"]):
                logger.warning("Using stale Steam profile dashboard cache.")
                self._write_steam_profile_context(cache_entry.get("data") or {}, now)
                return safe_open_image(cache_entry["image_path"]).convert("RGB")
            raise RuntimeError(f"Steam 个人资料看板生成失败：{str(e)}")

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

    def _get_dashboard_data(self, api_key, steam_id, settings, cache_entry, now, status_cache_seconds, full_cache_minutes):
        cached_data = cache_entry.get("data") if isinstance(cache_entry, dict) else None
        full_is_fresh = bool(
            cached_data
            and now - cache_entry.get("full_updated_at", 0) < full_cache_minutes * 60
        )
        status_is_fresh = bool(
            cached_data
            and now - cache_entry.get("status_updated_at", 0) < status_cache_seconds
        )

        if not full_is_fresh:
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
                logger.warning(f"Steam friend status refresh unavailable: {e}")
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
                    logger.warning(f"Steam recent games refresh unavailable after game change: {e}")
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
                    logger.warning(f"Steam app details refresh unavailable after game change: {e}")
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
            return players[0], 1, warnings
        except Exception as e:
            warnings.append("实时状态不可用")
            raise RuntimeError(f"Steam 实时状态不可用：{e}")

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
                logger.warning(f"{message}: {e}")
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
        profile = players[0]

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
            logger.warning(f"Steam store appdetails unavailable for {','.join(normalized)} ({language}): {e}")
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

    def _render_dashboard(self, data, dimensions, theme_context=None):
        width, height = dimensions
        palette = get_theme_palette(theme_context)
        bg = palette["background"]
        panel = palette["panel"]
        panel_border = palette["border"]
        ink = palette["ink"]
        gray = palette["muted"]
        light = palette["rule"]
        accent = palette["accent"]
        accent_online = palette["green"]
        image = self._dashboard_background((width, height), bg, theme_mode=(theme_context or {}).get("mode", "day"))
        draw = ImageDraw.Draw(image)

        fonts = self._fonts(width, height)

        margin = 26
        avatar_size = min(170, max(120, int(height * 0.34)))
        panel_x = margin + avatar_size + 38
        panel_y = 34
        panel_w = width - panel_x - margin
        panel_h = 176
        online_avatar_friends = self._online_friends_for_avatars(data)
        friend_avatar_size = max(30, min(32, panel_h // 5))
        friend_avatar_gap = 6
        friend_text_line_h = self._line_height(draw, fonts["tiny"])
        friend_text_group_h = friend_text_line_h * 2 + 1
        friend_row_h = max(friend_avatar_size, friend_text_group_h)
        friend_row_count = min(4, len(online_avatar_friends))
        friend_group_h = friend_row_count * friend_row_h + max(0, friend_row_count - 1) * friend_avatar_gap
        friend_panel_w = min(220, max(190, int(panel_w * 0.41)))
        friend_panel_x = panel_x + panel_w - 18 - friend_panel_w
        friend_panel_y = panel_y + max(0, (panel_h - friend_group_h) // 2)
        top_text_right = friend_panel_x - 22 if online_avatar_friends else panel_x + panel_w - 18
        lower_y = panel_y + panel_h + 38

        avatar_box = (margin + 10, panel_y + 2, margin + 10 + avatar_size, panel_y + 2 + avatar_size)
        avatar = self._avatar_image(data["profile"].get("avatarfull"), avatar_size)
        image.paste(avatar, (avatar_box[0], avatar_box[1]), avatar if avatar.mode == "RGBA" else None)
        self._draw_avatar_gamepad_frame(draw, avatar_box, (255, 255, 255), (232, 238, 246), fonts)
        self._rounded_rect(
            draw,
            (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
            radius=0,
            outline=panel_border,
            width=3,
            fill=panel,
        )
        self._draw_badge_icon_scatter(
            image,
            data,
            anchor_box=avatar_box,
            avoid_boxes=[
                (avatar_box[0] - 3, avatar_box[1] - 3, avatar_box[2] + 3, avatar_box[3] + 3),
                (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
            ],
        )

        profile = data["profile"]
        status_text, status_color = self._persona_text(profile)
        self._draw_wrapped_text(
            draw,
            (panel_x + 18, panel_y + 10),
            profile.get("personaname", "Steam User"),
            fonts["title"],
            ink,
            max(160, top_text_right - (panel_x + 18)),
        )

        y = panel_y + 50
        top_line_width = max(220, top_text_right - (panel_x + 18))
        has_current_game = profile.get("gameid") or profile.get("gameextrainfo")
        if has_current_game:
            current_game = self._display_game_name(data, profile.get("gameid"), profile.get("gameextrainfo"))
            y, _ = self._draw_current_game_line(
                image,
                draw,
                (panel_x + 18, y),
                current_game,
                profile.get("gameid"),
                fonts["body"],
                ink,
                top_line_width,
                data,
                label_fill=accent_online,
            )
        else:
            y, _ = self._draw_status_line(draw, (panel_x + 18, y), "状态：", status_text, fonts["body"], ink, status_color, top_line_width)
        y += 7

        owned_count = len(data.get("owned_games", []))
        total_hours = self._minutes_to_hours(sum(game.get("playtime_forever", 0) for game in data.get("owned_games", [])))
        recent_hours = self._minutes_to_hours(sum(game.get("playtime_2weeks", 0) for game in data.get("recent_games", [])))
        level = self._display_value(data.get("level"))
        friend_count = self._display_value(data.get("friend_count"))
        online_count = self._display_value(data.get("online_friend_count"))
        y, _ = self._draw_wrapped_text(draw, (panel_x + 18, y), f"等级 {level}  |  游戏 {owned_count or '-'}  |  好友 {online_count}/{friend_count}", fonts["small"], ink, top_line_width)
        y += 5
        y, _ = self._draw_wrapped_text(draw, (panel_x + 18, y), f"近2周 {recent_hours} 小时  |  总计 {total_hours} 小时", fonts["small"], ink, top_line_width)
        y += 5
        self._draw_wrapped_text(draw, (panel_x + 18, y), self._last_seen(profile), fonts["small"], gray, top_line_width)

        if online_avatar_friends:
            self._draw_online_friend_activity(
                image,
                draw,
                online_avatar_friends,
                friend_panel_x,
                friend_panel_y,
                friend_panel_w,
                friend_row_h,
                friend_avatar_size,
                friend_avatar_gap,
                fonts,
                data,
                ink,
            )


        lower_h = height - lower_y - 28
        self._rounded_rect(
            draw,
            (margin, lower_y, width - margin, lower_y + lower_h),
            radius=0,
            outline=panel_border,
            width=3,
            fill=panel,
        )

        col_gap = 18
        col_w = (width - margin * 2 - col_gap) // 2
        left_x = margin + 18
        right_x = margin + 18 + col_w + col_gap
        content_y = lower_y + 18

        draw.line((left_x + col_w, lower_y + 14, left_x + col_w, lower_y + lower_h - 14), fill=light, width=2)

        if self._draw_section_wordmark(image, "recent_live", left_x, content_y) is None:
            self._text(draw, (left_x, content_y), "最近 / 实时", fonts["section"], ink)
        y = content_y + 29
        left_line_width = col_w - 22
        self._draw_recent_grid(
            image,
            draw,
            self._recent_items(data)[:STEAM_LEFT_GAME_ITEM_TARGET],
            left_x,
            y,
            left_line_width,
            lower_y + lower_h - 14,
            fonts["recent"],
            ink,
            accent_online,
            light,
            data,
            gray,
        )
        if self._draw_section_wordmark(image, "library_friends", right_x, content_y) is None:
            self._text(draw, (right_x, content_y), "游戏库 / 好友", fonts["section"], ink)
        y = content_y + 31
        right_line_width = width - margin - (right_x + 18) - 12
        top_game_items = self._top_game_items(data)
        if top_game_items:
            self._text(draw, (right_x + 18, y), "\u5e38\u73a9 TOP 3", fonts["tiny"], gray)
            y += 21
            for item in top_game_items:
                next_y, fits = self._draw_top_game_item(
                    image,
                    draw,
                    item,
                    right_x + 18,
                    y,
                    fonts["small"],
                    ink,
                    right_line_width,
                    lower_y + lower_h - 18,
                    data,
                )
                if not fits:
                    break
                y = next_y + 5

            if y <= lower_y + lower_h - 44:
                draw.line((right_x + 18, y - 3, width - margin - 18, y - 3), fill=light, width=1)
                y += 9

        for item in self._library_items(data):
            next_y, fits = self._draw_library_item(
                image,
                draw,
                item,
                right_x,
                y,
                fonts["small"],
                ink,
                accent,
                right_line_width,
                lower_y + lower_h - 18,
                data,
            )
            if not fits:
                break
            y = next_y + 5

        refresh_mode = self._refresh_mode_label(data.get("refresh_mode", "full"))
        footer = f"更新 {data.get('updated_at')}  |  Steam 请求 {data.get('api_calls', 0)} 次（{refresh_mode}）"
        warnings = data.get("warnings") or []
        if warnings:
            footer += f"  |  {len(warnings)} 项隐私/缺失"
        self._text(draw, (margin, height - 19), footer, fonts["tiny"], gray)
        return image

    def _draw_avatar_gamepad_frame(self, draw, avatar_box, outline, muted, fonts):
        x0, y0, x1, y1 = avatar_box
        size = x1 - x0
        cx = x0 + size / 2
        cy = y0 + size / 2
        radius = size / 2
        line_width = max(2, size // 72)
        outer_pad = max(5, size // 22)
        outer = (
            int(cx - radius - outer_pad),
            int(cy - radius - outer_pad),
            int(cx + radius + outer_pad),
            int(cy + radius + outer_pad),
        )
        inner = (
            int(cx - radius - outer_pad + 5),
            int(cy - radius - outer_pad + 5),
            int(cx + radius + outer_pad - 5),
            int(cy + radius + outer_pad - 5),
        )

        draw.ellipse(outer, outline=muted, width=1)
        for start, end in ((-38, 34), (58, 122), (148, 214), (238, 304)):
            draw.arc(outer, start=start, end=end, fill=outline, width=line_width)
        draw.ellipse(inner, outline=outline, width=1)

        tick_outer = radius + outer_pad + 2
        tick_inner = radius + outer_pad - 6
        for index in range(32):
            angle = math.radians(index * 360 / 32)
            length_boost = 4 if index % 4 == 0 else 0
            x_a = cx + (tick_inner - length_boost) * math.cos(angle)
            y_a = cy + (tick_inner - length_boost) * math.sin(angle)
            x_b = cx + tick_outer * math.cos(angle)
            y_b = cy + tick_outer * math.sin(angle)
            draw.line((x_a, y_a, x_b, y_b), fill=outline if index % 4 == 0 else muted, width=1)

        node_font = self._font(max(10, size // 14), bold=True)
        nodes = [
            (-70, "+"),
            (-18, "B"),
            (42, "▢"),
            (132, "Y"),
            (218, "X"),
        ]
        node_radius = max(8, size // 18)
        node_distance = radius + outer_pad + node_radius * 0.35
        for angle_deg, label in nodes:
            angle = math.radians(angle_deg)
            node_x = cx + node_distance * math.cos(angle)
            node_y = cy + node_distance * math.sin(angle)
            self._draw_avatar_frame_node(draw, node_x, node_y, node_radius, label, node_font, outline, muted, line_width)

        self._draw_micro_dpad(
            draw,
            cx - radius * 0.58,
            cy + radius * 0.58,
            max(11, size // 13),
            outline,
            muted,
            max(1, line_width - 1),
        )

    def _draw_avatar_frame_node(self, draw, center_x, center_y, radius, label, font, outline, muted, width):
        cx = int(round(center_x))
        cy = int(round(center_y))
        r = int(round(radius))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=muted, width=1)
        draw.ellipse((cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2), outline=outline, width=width)
        if label == "+":
            arm = max(4, int(r * 0.48))
            draw.line((cx - arm, cy, cx + arm, cy), fill=outline, width=max(1, width - 1))
            draw.line((cx, cy - arm, cx, cy + arm), fill=outline, width=max(1, width - 1))
        elif label == "▢":
            q = max(4, int(r * 0.38))
            draw.rectangle((cx - q, cy - q, cx + q, cy + q), outline=outline, width=max(1, width - 1))
        else:
            self._center_text(draw, (cx, cy), label, font, outline)

    def _draw_micro_dpad(self, draw, center_x, center_y, size, outline, muted, width):
        cx = int(round(center_x))
        cy = int(round(center_y))
        unit = max(3, int(round(size / 4)))
        boxes = [
            (cx - unit, cy - unit * 3, cx + unit, cy - unit),
            (cx - unit, cy + unit, cx + unit, cy + unit * 3),
            (cx - unit * 3, cy - unit, cx - unit, cy + unit),
            (cx + unit, cy - unit, cx + unit * 3, cy + unit),
            (cx - unit, cy - unit, cx + unit, cy + unit),
        ]
        for box in boxes:
            self._rounded_rect(draw, box, radius=1, outline=muted, width=1, fill=None)
        for box in boxes:
            self._rounded_rect(
                draw,
                (box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1),
                radius=1,
                outline=outline,
                width=width,
                fill=None,
            )

    def _center_text(self, draw, center, text, font, fill):
        if not text:
            return
        cx, cy = center
        bbox = draw.textbbox((0, 0), str(text), font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.text((cx - text_w / 2 - bbox[0], cy - text_h / 2 - bbox[1]), str(text), font=font, fill=fill)

    def _dashboard_background(self, dimensions, fallback_color, theme_mode="day"):
        theme_mode = str(theme_mode or "day").lower()
        image_name = STEAM_BACKGROUND_NIGHT_IMAGE if theme_mode == "night" else STEAM_BACKGROUND_DAY_IMAGE
        path = self.get_plugin_dir(image_name)
        try:
            background = Image.open(path).convert("RGB")
            return ImageOps.fit(background, dimensions, method=Image.Resampling.LANCZOS)
        except Exception as e:
            logger.warning(f"Steam dashboard background {image_name} unavailable: {e}")
            return Image.new("RGB", dimensions, fallback_color)

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
            logger.warning(f"Steam badge icons unavailable: {e}")
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

    def _draw_badge_icon_scatter(self, image, data, anchor_box=None, avoid_boxes=None):
        badge_icons = [
            str(record.get("icon_url") or "").strip()
            for record in (data.get("badge_icons") or [])
            if isinstance(record, dict) and record.get("icon_url")
        ]
        if not badge_icons:
            return

        width, height = image.size
        if width <= 0 or height <= 0:
            return

        rng = random.Random(self._badge_scatter_seed(data, (width, height)))
        focused = anchor_box is not None
        if focused:
            min_size = max(20, min(width, height) // 24)
            max_size = max(min_size + 8, min(width, height) // 10)
            icon_count = min(34, max(22, len(badge_icons) * 2))
            zones = self._badge_scatter_focus_zones(anchor_box, (width, height), max_size)
            avoid_boxes = [tuple(int(value) for value in box) for box in (avoid_boxes or [])]
        else:
            min_size = max(16, min(width, height) // 28)
            max_size = max(min_size + 4, min(width, height) // 13)
            icon_count = min(44, max(18, int((width * height) / 13500)), len(badge_icons) * 3)
            zones = [(0, 0, width, height)]
            avoid_boxes = []

        for _ in range(icon_count):
            size = rng.randint(min_size, max_size)
            icon = self._badge_icon_image(rng.choice(badge_icons), size)
            if icon is None:
                continue

            if rng.random() < (0.88 if focused else 0.78):
                icon = icon.rotate(rng.uniform(-34, 34), resample=Image.Resampling.BICUBIC, expand=True)

            opacity = rng.uniform(0.70, 0.95) if focused else rng.uniform(0.38, 0.72)
            if icon.mode != "RGBA":
                icon = icon.convert("RGBA")
            alpha = icon.getchannel("A").point(lambda value: int(value * opacity))
            icon.putalpha(alpha)

            placed = False
            for _attempt in range(14):
                zone = rng.choice(zones)
                x_min, y_min, x_max, y_max = zone
                if x_max <= x_min or y_max <= y_min:
                    continue
                x = rng.randint(x_min - icon.width // 4, max(x_min, x_max - (icon.width * 3) // 4))
                y = rng.randint(y_min - icon.height // 4, max(y_min, y_max - (icon.height * 3) // 4))
                bbox = (x, y, x + icon.width, y + icon.height)
                if any(self._rects_overlap(bbox, box) for box in avoid_boxes):
                    continue
                image.paste(icon, (x, y), icon)
                placed = True
                break
            if not placed and not focused:
                x = rng.randint(-icon.width // 3, max(0, width - (icon.width * 2) // 3))
                y = rng.randint(-icon.height // 3, max(0, height - (icon.height * 2) // 3))
                image.paste(icon, (x, y), icon)

    def _badge_scatter_focus_zones(self, anchor_box, dimensions, max_size):
        width, height = dimensions
        left, top, right, bottom = [int(value) for value in anchor_box]
        pad = max(18, int(max_size))
        raw_zones = [
            (left - pad // 2, top - pad, right + pad, top + pad // 2),
            (left - pad, top + pad // 3, left + pad // 2, bottom + pad // 3),
            (right - pad // 3, top + pad // 2, right + pad, bottom - pad // 4),
            (left - pad // 3, bottom - pad // 4, right + pad, bottom + pad),
        ]
        zones = []
        for x1, y1, x2, y2 in raw_zones:
            zone = (
                max(0, min(width, x1)),
                max(0, min(height, y1)),
                max(0, min(width, x2)),
                max(0, min(height, y2)),
            )
            if zone[2] - zone[0] >= max(18, pad // 2) and zone[3] - zone[1] >= max(18, pad // 2):
                zones.append(zone)
        return zones or [(0, 0, width, height)]

    @staticmethod
    def _rects_overlap(a, b):
        return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])

    def _badge_scatter_seed(self, data, dimensions):
        profile = data.get("profile") or {}
        icon_urls = [
            str(record.get("icon_url") or "")
            for record in (data.get("badge_icons") or [])
            if isinstance(record, dict)
        ]
        seed_material = "|".join([
            str(profile.get("steamid") or DEFAULT_STEAM_ID),
            str(dimensions),
            STEAM_DASHBOARD_STYLE_VERSION,
            *sorted(icon_urls),
        ])
        return int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)

    def _badge_icon_image(self, url, size):
        url = self._normalize_steam_image_url(url)
        if not url or size <= 0:
            return None

        icon = None
        icon_cache_path = self._badge_icon_cache_path(url)
        try:
            if os.path.exists(icon_cache_path) and time.time() - os.path.getmtime(icon_cache_path) < 30 * 24 * 60 * 60:
                icon = safe_open_image(icon_cache_path).convert("RGBA")
            else:
                session = get_http_session()
                response = session.get(url, timeout=25, stream=True)
                icon = safe_open_image_response(response).convert("RGBA")
                os.makedirs(os.path.dirname(icon_cache_path), exist_ok=True)
                icon.save(icon_cache_path)
        except Exception as e:
            logger.warning(f"Steam badge icon unavailable: {e}")
            return None

        return ImageOps.fit(icon, (size, size), method=Image.Resampling.LANCZOS)

    def _draw_game_backdrop(self, image, x, y, width, height):
        if width <= 0 or height <= 0:
            return
        backdrop = self._game_backdrop_image((width, height))
        if backdrop:
            image.paste(backdrop, (int(x), int(y)))

    def _game_backdrop_image(self, size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = self.get_plugin_dir(STEAM_GAME_BACKDROP_IMAGE)
        try:
            with Image.open(path) as source:
                backdrop = source.convert("RGB")
            return ImageOps.fit(backdrop, (width, height), method=Image.Resampling.LANCZOS)
        except Exception as e:
            logger.warning(f"Steam dashboard game backdrop unavailable: {e}")
            return None

    def _draw_game_strip(self, image, x, y, width, height):
        if width <= 0 or height <= 0:
            return
        strip = self._game_strip_image((width, height))
        if strip:
            image.paste(strip, (int(x), int(y)))

    def _game_strip_image(self, size):
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            return None
        path = self.get_plugin_dir(STEAM_GAME_STRIP_IMAGE)
        try:
            with Image.open(path) as source:
                strip = source.convert("RGB")
            return ImageOps.fit(strip, (width, height), method=Image.Resampling.LANCZOS)
        except Exception as e:
            logger.warning(f"Steam dashboard game strip unavailable: {e}")
            return None

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
            logger.warning(f"Steam dashboard section wordmark unavailable: {image_name}: {e}")
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

    def _recent_lines(self, data):
        return [self._item_text(item) for item in self._recent_items(data)]

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

    def _top_game_lines(self, data):
        return [f"TOP {item.get('rank')}  {item.get('name')}  {item.get('suffix')}" for item in self._top_game_items(data)]

    def _library_items(self, data):
        items = []
        badges = data.get("badges") or {}
        if badges:
            badge_count = len(badges.get("badges", []))
            xp = badges.get("player_xp")
            items.append({"text": f"徽章：{badge_count}  XP：{self._display_value(xp)}"})

        bans = data.get("bans") or {}
        if bans:
            vac = "VAC" if bans.get("VACBanned") else "无 VAC"
            game_bans = bans.get("NumberOfGameBans", 0)
            days = bans.get("DaysSinceLastBan", 0)
            items.append({"text": f"封禁：{vac}，游戏封禁 {game_bans}，距上次 {days} 天"})

        online_friends = [friend for friend in data.get("friends", []) if int(friend.get("personastate", 0)) != 0]
        for friend in online_friends[:3]:
            name = friend.get("personaname", "好友")
            has_game = friend.get("gameid") or friend.get("gameextrainfo")
            if has_game:
                game = self._display_game_name(data, friend.get("gameid"), friend.get("gameextrainfo"))
                items.append({
                    "friend": name,
                    "name": game,
                    "appid": friend.get("gameid"),
                })
            else:
                items.append({"text": f"{name}: {PERSONA_STATES.get(int(friend.get('personastate', 0)), '在线')}"})

        if not items:
            items.append({"text": "游戏库/好友数据为隐私"})
        return items

    def _library_lines(self, data):
        lines = []
        for item in self._library_items(data):
            if item.get("friend") and item.get("name"):
                lines.append(f"{item.get('friend')}: {item.get('name')}")
            else:
                lines.append(item.get("text", ""))
        return lines

    def _item_text(self, item):
        if not isinstance(item, dict):
            return str(item or "")
        if item.get("text"):
            return str(item.get("text") or "")
        return f"{item.get('prefix', '')}{item.get('name', '')}{item.get('suffix', '')}"

    def _online_friends_for_avatars(self, data):
        friends = []
        for friend in data.get("friends", []) or []:
            try:
                state = int(friend.get("personastate", 0) or 0)
            except Exception:
                state = 0
            if state != 0:
                friends.append(friend)
        return friends[:4]

    def _draw_online_friend_activity(self, image, draw, friends, x, y, width, row_height, size, gap, fonts, data, ink):
        for index, friend in enumerate(friends[:4]):
            row_y = int(y + index * (row_height + gap))
            avatar_x = int(x)
            avatar_y = int(row_y + max(0, (row_height - size) // 2))
            url = friend.get("avatarfull") or friend.get("avatarmedium") or friend.get("avatar")
            avatar = self._avatar_image(url, size)
            image.paste(avatar, (avatar_x, avatar_y), avatar if avatar.mode == "RGBA" else None)

            status_fill = self._friend_status_color(friend)
            name = self._friend_display_id(friend)
            dot_size = 8
            text_x = avatar_x + size + 10
            line_height = self._line_height(draw, fonts["tiny"])
            line_gap = 1
            text_group_h = line_height * 2 + line_gap
            text_y = row_y + max(0, (row_height - text_group_h) // 2)
            text_width = max(20, width - (text_x - x))
            self._draw_single_line_text(
                draw,
                (text_x, text_y),
                name,
                fonts["tiny"],
                ink,
                text_width,
                min_size=8,
            )

            status_y = text_y + line_height + line_gap
            dot_y = status_y + max(0, (line_height - dot_size) // 2)
            draw.ellipse((text_x, dot_y, text_x + dot_size, dot_y + dot_size), fill=status_fill)
            status_x = text_x + dot_size + 7
            status_width = max(20, width - (status_x - x))
            if friend.get("gameid") or friend.get("gameextrainfo"):
                self._draw_friend_game_status(
                    image,
                    draw,
                    friend,
                    (status_x, status_y),
                    fonts["tiny"],
                    ink,
                    status_width,
                    data,
                    row_height=line_height,
                )
            else:
                status_text = self._friend_live_text(data, friend)
                self._draw_single_line_text(
                    draw,
                    (status_x, status_y),
                    status_text,
                    fonts["tiny"],
                    ink,
                    status_width,
                    min_size=8,
                )

    def _friend_display_id(self, friend):
        return self._clean_game_name(friend.get("personaname")) or str(friend.get("steamid") or "好友")

    def _friend_live_text(self, data, friend):
        if friend.get("gameid") or friend.get("gameextrainfo"):
            game = self._display_game_name(data, friend.get("gameid"), friend.get("gameextrainfo"))
            return f"正在游玩： {game}"
        return "在线"

    def _friend_status_color(self, friend):
        online_green = (88, 205, 118)
        if friend.get("gameid") or friend.get("gameextrainfo"):
            return online_green
        try:
            state = int(friend.get("personastate", 0) or 0)
        except Exception:
            state = 0
        if state != 0:
            return online_green
        return (120, 132, 146)

    def _game_square_icon(self, data, appid, size):
        url = self._game_icon_url(data, appid)
        if not url:
            return None

        icon = None
        icon_cache_path = self._game_icon_cache_path(url)
        try:
            if os.path.exists(icon_cache_path) and time.time() - os.path.getmtime(icon_cache_path) < 14 * 24 * 60 * 60:
                icon = safe_open_image(icon_cache_path).convert("RGB")
            else:
                session = get_http_session()
                response = session.get(url, timeout=25, stream=True)
                icon = safe_open_image_response(response).convert("RGB")
                os.makedirs(os.path.dirname(icon_cache_path), exist_ok=True)
                icon.save(icon_cache_path)
        except Exception as e:
            logger.warning(f"Steam game icon unavailable: {e}")
            return None

        icon = ImageOps.fit(icon, (size, size), method=Image.Resampling.LANCZOS)
        result = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        result.paste(icon, (0, 0))
        outline = ImageDraw.Draw(result)
        outline.rectangle((0, 0, size - 1, size - 1), outline=(255, 255, 255, 190), width=1)
        return result

    def _game_icon_url(self, data, appid):
        appid = self._normalize_appid(appid)
        if not appid:
            return ""

        game = self._game_record(data, appid)
        icon_hash = str((game or {}).get("img_icon_url") or "").strip()
        if icon_hash:
            if icon_hash.startswith("http://") or icon_hash.startswith("https://"):
                return icon_hash
            return STEAM_APP_ICON_URL.format(appid=appid, icon_hash=icon_hash)

        details = data.get("app_details") or {}
        for key in ("capsule_image", "header_image"):
            url = str(details.get(key) or "").strip()
            if url.startswith("http://") or url.startswith("https://"):
                return url
        return STEAM_APP_CAPSULE_URL.format(appid=appid)

    def _game_record(self, data, appid):
        game = data.get("spotlight_game") or {}
        if self._normalize_appid(game.get("appid")) == appid:
            return game

        for collection in ("recent_games", "owned_games"):
            for game in data.get(collection, []) or []:
                if self._normalize_appid(game.get("appid")) == appid:
                    return game
        return {}

    def _avatar_image(self, url, size):
        avatar = None
        if url:
            avatar_cache_path = self._avatar_cache_path(url)
            try:
                if os.path.exists(avatar_cache_path) and time.time() - os.path.getmtime(avatar_cache_path) < 7 * 24 * 60 * 60:
                    avatar = safe_open_image(avatar_cache_path).convert("RGB")
                else:
                    session = get_http_session()
                    response = session.get(url, timeout=25, stream=True)
                    avatar = safe_open_image_response(response).convert("RGB")
                    os.makedirs(os.path.dirname(avatar_cache_path), exist_ok=True)
                    avatar.save(avatar_cache_path)
            except Exception as e:
                logger.warning(f"Steam avatar unavailable: {e}")

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

    def _sort_friends(self, players):
        return sorted(
            players,
            key=lambda friend: (
                0 if friend.get("gameid") or friend.get("gameextrainfo") else 1,
                0 if int(friend.get("personastate", 0)) else 1,
                str(friend.get("personaname", "")).lower(),
            ),
        )

    def _last_seen(self, profile):
        if profile.get("gameextrainfo"):
            return f"游戏中，AppID {profile.get('gameid', '-')}"
        lastlogoff = profile.get("lastlogoff")
        if not lastlogoff:
            return "最后在线：未知"
        try:
            seen = datetime.fromtimestamp(int(lastlogoff))
            return f"最后在线：{seen.strftime('%Y-%m-%d %H:%M')}"
        except Exception:
            return "最后在线：未知"

    def _refresh_mode_label(self, mode):
        return {
            "full": "完整刷新",
            "live": "实时状态",
            "cache": "缓存",
        }.get(str(mode), str(mode))

    def _fonts(self, width, height):
        base = max(14, min(width // 46, height // 27))
        return {
            "title": self._font(base + 9, bold=True),
            "section": self._font(base + 4, bold=True),
            "body": self._font(base + 3, bold=True),
            "small": self._font(max(13, base - 1)),
            "recent": self._font(max(14, base - 3), bold=True),
            "tiny": self._font(max(11, base - 4)),
        }

    def _font(self, size, bold=False):
        candidates = []
        plugin_dir = self.get_plugin_dir()
        src_dir = os.path.abspath(os.path.join(plugin_dir, "..", ".."))
        yahei_dir = os.path.join(plugin_dir, "..", "sports_dashboard", "fonts")
        if bold:
            candidates.extend([
                os.path.join(plugin_dir, "fonts", "msyhbd.ttc"),
                os.path.join(yahei_dir, "msyhbd.ttc"),
                "C:/Windows/Fonts/msyhbd.ttc",
            ])
        candidates.extend([
            os.path.join(plugin_dir, "fonts", "msyh.ttc"),
            os.path.join(yahei_dir, "msyh.ttc"),
            "C:/Windows/Fonts/msyh.ttc",
            os.path.join(src_dir, "static", "fonts", "LXGWWenKai-Regular.ttf"),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ])
        if bold:
            candidates.extend([
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
            ])
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ])
        for path in candidates:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        return ImageFont.load_default()

    def _text(self, draw, position, text, font, fill):
        draw.text(position, str(text), font=font, fill=fill)

    def _game_icon_size(self, draw, font, min_size=10, max_size=24):
        line_height = self._line_height(draw, font)
        return max(min_size, min(max_size, max(1, line_height - 2)))

    def _draw_playing_title_text(self, draw, position, text, font, fill, label_fill, max_width, max_bottom=None, min_size=9):
        label = "\u6b63\u5728\u73a9\uff1a"
        text = str(text or "")
        if not text.startswith(label):
            return self._draw_single_line_clipped_text(
                draw,
                position,
                text,
                font,
                fill,
                max_width,
                max_bottom=max_bottom,
                min_size=min_size,
            )

        max_width = int(max_width or 0)
        if max_width <= 0:
            return position[1], False

        x, y = position
        fitted_font = self._fit_single_line_font(draw, text, font, max_width, min_size=min_size)
        line_height = self._line_height(draw, fitted_font)
        if max_bottom is not None and y + line_height > max_bottom:
            return y, False

        label_width = self._text_width(draw, label, fitted_font)
        if label_width >= max_width:
            self._draw_clipped_text(draw, (x, y), label, fitted_font, label_fill, max_width, line_height)
            return y + line_height, True

        self._text(draw, (x, y), label, fitted_font, label_fill)
        rest = text[len(label):]
        rest_width = max(1, max_width - label_width)
        rest_x = x + label_width
        if self._text_width(draw, rest, fitted_font) <= rest_width:
            self._text(draw, (rest_x, y), rest, fitted_font, fill)
        else:
            self._draw_clipped_text(draw, (rest_x, y), rest, fitted_font, fill, rest_width, line_height)
        return y + line_height, True

    def _draw_current_game_line(self, image, draw, position, game_name, appid, font, fill, max_width, data, max_bottom=None, label_fill=None):
        x, y = position
        label = "\u6b63\u5728\u73a9\uff1a"
        label_fill = label_fill or fill
        game_name = str(game_name or "").strip()
        max_width = int(max_width or 0)
        if not game_name or max_width <= 0:
            return y, True

        line_height = self._line_height(draw, font)
        icon_size = self._game_icon_size(draw, font, min_size=18, max_size=24)
        icon = self._game_square_icon(data, appid, icon_size)
        if icon is None:
            return self._draw_playing_title_text(
                draw,
                (x, y),
                f"{label}{game_name}",
                font,
                fill,
                label_fill,
                max_width,
                max_bottom=max_bottom,
                min_size=10,
            )

        label_width = self._text_width(draw, label, font)
        icon_x = x + label_width + 6
        text_x = icon_x + icon_size + 7
        game_width = max_width - (text_x - x)
        if game_width < 80:
            return self._draw_playing_title_text(
                draw,
                (x, y),
                f"{label}{game_name}",
                font,
                fill,
                label_fill,
                max_width,
                max_bottom=max_bottom,
                min_size=10,
            )

        if max_bottom is not None and y + line_height > max_bottom:
            return y, False

        self._text(draw, (x, y), label, font, label_fill)
        icon_y = y + max(0, (line_height - icon_size) // 2)
        image.paste(icon, (int(icon_x), int(icon_y)), icon if icon.mode == "RGBA" else None)
        next_y, fits = self._draw_single_line_text(
            draw,
            (text_x, y),
            game_name,
            font,
            fill,
            game_width,
            max_bottom=max_bottom,
            min_size=10,
        )
        return max(next_y, y + icon_size), fits

    def _recent_stat_lines(self, item):
        detail = str((item or {}).get("detail") or "").strip()
        if detail:
            return [part.strip() for part in detail.split("|") if part.strip()][:2]

        compact = str((item or {}).get("compact_suffix") or (item or {}).get("suffix") or "").strip()
        compact = compact.lstrip("\uff1a: -")
        return [compact] if compact else []

    def _recent_stat_text(self, item):
        return "  ".join(self._recent_stat_lines(item))

    def _draw_recent_grid(self, image, draw, items, x, y, width, max_bottom, font, fill, marker_fill, rule_fill, data, muted_fill=None):
        items = list(items or [])[:STEAM_LEFT_GAME_ITEM_TARGET]
        if not items:
            return y, True

        x = int(x)
        y = int(y)
        width = int(width or 0)
        if width <= 0:
            return y, False

        muted_fill = muted_fill or self._muted_text_fill(fill)
        row_count = len(items)
        available_h = 36 * row_count
        if max_bottom is not None:
            available_h = int(max_bottom) - y
        if available_h <= 0:
            return y, False

        row_h = max(28, min(36, available_h // row_count))
        rows_h = row_h * row_count
        if max_bottom is not None and y + rows_h > int(max_bottom):
            row_h = max(24, (int(max_bottom) - y) // row_count)
            rows_h = row_h * row_count
        if row_h < 24:
            return y, False

        right = x + width
        bottom = y + rows_h
        icon_col_w = min(40, max(34, int(width * 0.12)))
        stat_col_w = min(154, max(118, int(width * 0.40)))
        if width - icon_col_w - stat_col_w < 112:
            stat_col_w = max(0, width - icon_col_w - 112)
        if stat_col_w < 78:
            stat_col_w = 0

        name_x = x + icon_col_w + 8
        stat_x = right - stat_col_w if stat_col_w else right
        name_right = (stat_x - 10) if stat_col_w else (right - 8)
        name_w = max(34, name_right - name_x)
        stat_w = max(0, stat_col_w)

        title_font = font
        stat_font = self._font(max(12, int(getattr(font, "size", 14) or 14) - 1))
        title_line_h = self._line_height(draw, title_font)
        stat_line_h = self._line_height(draw, stat_font)

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                item = {"text": str(item or "")}
            row_y = y + index * row_h
            row_bottom = row_y + row_h
            title_text = self._item_text(item)
            if item.get("name") and item.get("appid"):
                title_text = f"{item.get('prefix', '')}{item.get('name', '')}"

            base_icon_size = max(16, min(24, row_h - 10))
            icon_size = min(
                max(base_icon_size, min(row_h - 4, icon_col_w - 6)),
                int(math.ceil(base_icon_size * 1.2)),
            )
            icon_x = x + max(0, (icon_col_w - icon_size) // 2)
            icon_y = row_y + max(0, (row_h - icon_size) // 2)
            icon = self._game_square_icon(data, item.get("appid"), icon_size) if item.get("appid") else None
            if icon is not None:
                image.paste(icon, (int(icon_x), int(icon_y)), icon if icon.mode == "RGBA" else None)
            else:
                self._bullet(draw, x + icon_col_w // 2 - 4, row_y + row_h // 2 - 4, marker_fill)

            stat_text = self._recent_stat_text(item)
            title_y = row_y + max(0, (row_h - title_line_h) // 2)
            self._draw_playing_title_text(
                draw,
                (name_x, title_y),
                title_text,
                title_font,
                fill,
                marker_fill,
                name_w,
                max_bottom=row_bottom,
                min_size=9,
            )
            if stat_col_w and stat_text:
                stat_y = row_y + max(0, (row_h - stat_line_h) // 2)
                self._draw_single_line_clipped_text(
                    draw,
                    (stat_x, stat_y),
                    stat_text,
                    stat_font,
                    muted_fill,
                    stat_w,
                    max_bottom=row_bottom,
                    min_size=10,
                )

        return bottom, True

    def _draw_recent_item(self, image, draw, item, x, y, font, fill, marker_fill, max_width, max_bottom, data, muted_fill=None):
        if not isinstance(item, dict):
            item = {"text": str(item or "")}
        text = self._item_text(item)
        if not text:
            return y, True

        max_right = x + int(max_width or 0)
        muted_fill = muted_fill or self._muted_text_fill(fill)
        if item.get("name") and item.get("appid"):
            prefix = str(item.get("prefix") or "")
            title_text = f"{prefix}{item.get('name', '')}"
            detail_text = str(item.get("detail") or "").strip()
            if not detail_text:
                detail_text = str(item.get("suffix") or item.get("compact_suffix") or "").strip().lstrip("\uff1a: -")

            title_font = font
            detail_font = self._font(max(10, int(getattr(font, "size", 14) or 14) - 5))
            title_height = self._line_height(draw, title_font)
            detail_height = self._line_height(draw, detail_font) if detail_text else 0
            text_gap = 1 if detail_text else 0
            text_height = title_height + text_gap + detail_height
            icon_size = self._game_icon_size(draw, title_font, min_size=18, max_size=22)
            row_height = max(icon_size, text_height)
            if max_bottom is not None and y + row_height > max_bottom:
                return y, False

            icon = self._game_square_icon(data, item.get("appid"), icon_size)
            if icon is not None:
                icon_x = x
                icon_y = y + max(0, (row_height - icon_size) // 2)
                image.paste(icon, (int(icon_x), int(icon_y)), icon if icon.mode == "RGBA" else None)
                text_x = icon_x + icon_size + 7
            else:
                self._bullet(draw, x, y + max(6, row_height // 2 - 2), marker_fill)
                text_x = x + 16

            text_width = max(24, max_right - text_x)
            text_y = y + max(0, (row_height - text_height) // 2)
            self._draw_single_line_clipped_text(
                draw,
                (text_x, text_y),
                title_text,
                title_font,
                fill,
                text_width,
                max_bottom=max_bottom,
                min_size=10,
            )
            if detail_text:
                self._draw_single_line_clipped_text(
                    draw,
                    (text_x, text_y + title_height + text_gap),
                    detail_text,
                    detail_font,
                    muted_fill,
                    text_width,
                    max_bottom=max_bottom,
                    min_size=8,
                )
            return y + row_height, True

        text_x = x + 16
        line_height = self._line_height(draw, font)
        if max_bottom is not None and y + line_height > max_bottom:
            return y, False
        self._bullet(draw, x, y + max(6, line_height // 2 - 2), marker_fill)
        self._draw_single_line_clipped_text(
            draw,
            (text_x, y),
            text,
            font,
            fill,
            max_width - 16,
            max_bottom=max_bottom,
            min_size=9,
        )
        return y + line_height, True

    def _muted_text_fill(self, fill):
        try:
            r, g, b = [int(value) for value in fill[:3]]
        except Exception:
            return fill
        if r + g + b > 384:
            return (154, 163, 177)
        return (78, 88, 104)

    def _draw_top_game_item(self, image, draw, item, x, y, font, fill, max_width, max_bottom, data):
        name = str((item or {}).get("name") or "")
        if not name:
            return y, True

        prefix = f"TOP {item.get('rank', '-')}"
        suffix = str((item or {}).get("suffix") or "")
        line_height = self._line_height(draw, font)
        icon_size = self._game_icon_size(draw, font, min_size=12, max_size=18)
        icon = self._game_square_icon(data, (item or {}).get("appid"), icon_size)
        suffix_text = f" {suffix}" if suffix else ""
        prefix_width = self._text_width(draw, prefix, font)
        suffix_width = self._text_width(draw, suffix_text, font)
        max_right = x + int(max_width or 0)
        icon_x = x + prefix_width + 8
        game_x = icon_x + (icon_size + 7 if icon is not None else 0)
        suffix_x = max_right - suffix_width
        game_width = max(30, suffix_x - game_x - 6)

        if max_bottom is not None and y + max(line_height, icon_size) > max_bottom:
            return y, False

        self._text(draw, (x, y), prefix, font, fill)
        if icon is not None:
            icon_y = y + max(0, (line_height - icon_size) // 2)
            image.paste(icon, (int(icon_x), int(icon_y)), icon if icon.mode == "RGBA" else None)

        fitted_font = self._fit_single_line_font(draw, name, font, game_width, min_size=9)
        game_line_height = self._line_height(draw, fitted_font)
        self._text(draw, (game_x, y + max(0, (line_height - game_line_height) // 2)), name, fitted_font, fill)
        if suffix_text:
            self._text(draw, (suffix_x, y), suffix_text, font, fill)
        return y + max(line_height, icon_size, game_line_height), True

    def _draw_library_item(self, image, draw, item, x, y, font, fill, marker_fill, max_width, max_bottom, data):
        if not isinstance(item, dict):
            item = {"text": str(item or "")}

        if item.get("friend") and item.get("name"):
            label = f"{item.get('friend')}: "
            name = str(item.get("name") or "")
            line_height = self._line_height(draw, font)
            icon_size = self._game_icon_size(draw, font, min_size=12, max_size=18)
            icon = self._game_square_icon(data, item.get("appid"), icon_size)
            text_x = x + 18
            max_right = text_x + int(max_width or 0)
            label_width = self._text_width(draw, label, font)
            icon_x = text_x + label_width
            game_x = icon_x + (icon_size + 7 if icon is not None else 0)
            game_width = max(20, max_right - game_x)
            if max_bottom is not None and y + max(line_height, icon_size) > max_bottom:
                return y, False

            self._text(draw, (text_x, y), label, font, fill)
            if icon is not None:
                icon_y = y + max(0, (line_height - icon_size) // 2)
                image.paste(icon, (int(icon_x), int(icon_y)), icon if icon.mode == "RGBA" else None)
            return self._draw_single_line_text(
                draw,
                (game_x, y),
                name,
                font,
                fill,
                game_width,
                max_bottom=max_bottom,
                min_size=9,
            )

        text = str(item.get("text") or "")
        next_y, fits = self._draw_wrapped_text(
            draw,
            (x + 18, y),
            text,
            font,
            fill,
            max_width,
            max_bottom=max_bottom,
        )
        if not fits:
            return y, False
        self._bullet(draw, x, y + 7, marker_fill)
        return next_y, True

    def _draw_friend_game_status(self, image, draw, friend, position, font, fill, max_width, data, row_height=None):
        x, y = position
        prefix = "\u6b63\u5728\u6e38\u73a9\uff1a"
        game = self._display_game_name(data, friend.get("gameid"), friend.get("gameextrainfo"))
        line_height = self._line_height(draw, font)
        row_height = max(line_height, int(row_height or line_height))
        icon_size = max(8, min(11, row_height - 2))
        icon = self._game_square_icon(data, friend.get("gameid"), icon_size)

        icon_prefix_gap = 2 if icon is not None else 0
        icon_title_gap = 3 if icon is not None else 0

        def group_width(candidate_font):
            icon_width = icon_size + icon_prefix_gap + icon_title_gap if icon is not None else 0
            return (
                self._text_width(draw, prefix, candidate_font)
                + icon_width
                + self._text_width(draw, game, candidate_font)
            )

        fitted_font = font
        if group_width(fitted_font) > max_width:
            current_size = int(getattr(font, "size", 0) or 0)
            for size in range(current_size - 1, 4, -1):
                candidate = self._font(size)
                if group_width(candidate) <= max_width or size == 5:
                    fitted_font = candidate
                    break

        line_height = self._line_height(draw, fitted_font)
        row_height = max(line_height, row_height)
        start_x = x
        text_y = y + max(0, (row_height - line_height) // 2)

        self._text(draw, (start_x, text_y), prefix, fitted_font, fill)
        cursor_x = start_x + self._text_width(draw, prefix, fitted_font)
        if icon is not None:
            cursor_x += icon_prefix_gap
            icon_y = y + max(0, (row_height - icon_size) // 2)
            image.paste(icon, (int(cursor_x), int(icon_y)), icon if icon.mode == "RGBA" else None)
            cursor_x += icon_size + icon_title_gap

        remaining_width = max(1, int(x + max_width - cursor_x))
        if self._text_width(draw, game, fitted_font) <= remaining_width:
            self._text(draw, (cursor_x, text_y), game, fitted_font, fill)
        else:
            self._draw_clipped_text(draw, (cursor_x, text_y), game, fitted_font, fill, remaining_width, line_height)
        return y + row_height, True

    def _draw_centered_single_line_text(self, draw, position, text, font, fill, max_width, row_height=None, min_size=8):
        text = str(text or "")
        max_width = int(max_width or 0)
        if not text or max_width <= 0:
            return position[1], True

        x, y = position
        fitted_font = self._fit_single_line_font(draw, text, font, max_width, min_size=min_size)
        line_height = self._line_height(draw, fitted_font)
        row_height = max(line_height, int(row_height or line_height))
        text_width = self._text_width(draw, text, fitted_font)
        text_x = x + max(0, (max_width - text_width) // 2)
        text_y = y + max(0, (row_height - line_height) // 2)
        if text_width <= max_width:
            self._text(draw, (text_x, text_y), text, fitted_font, fill)
        else:
            self._draw_clipped_text(draw, (x, text_y), text, fitted_font, fill, max_width, line_height)
        return y + row_height, True

    def _draw_clipped_text(self, draw, position, text, font, fill, max_width, height):
        x, y = position
        max_width = max(1, int(max_width or 0))
        height = max(1, int(height or self._line_height(draw, font)))
        layer = Image.new("RGBA", (max_width, height), (255, 255, 255, 0))
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.text((0, 0), str(text or ""), font=font, fill=fill)
        target = getattr(draw, "_image", None)
        if target is not None:
            target.paste(layer, (int(x), int(y)), layer)

    def _draw_status_line(self, draw, position, label, status, font, fill, dot_fill, max_width, max_bottom=None):
        x, y = position
        label = str(label or "")
        status = str(status or "")
        line_height = self._line_height(draw, font)
        if max_bottom is not None and y + line_height > max_bottom:
            return y, False

        self._text(draw, (x, y), label, font, fill)
        label_width = self._text_width(draw, label, font)
        dot_size = max(8, min(12, line_height // 2))
        dot_x = x + label_width + 6
        dot_y = y + max(0, (line_height - dot_size) // 2)
        draw.ellipse((dot_x, dot_y, dot_x + dot_size, dot_y + dot_size), fill=dot_fill)

        status_x = dot_x + dot_size + 7
        _, fits = self._draw_single_line_text(
            draw,
            (status_x, y),
            status,
            font,
            fill,
            max(20, max_width - (status_x - x)),
            max_bottom=max_bottom,
            min_size=10,
        )
        return y + line_height, fits

    def _draw_single_line_text(self, draw, position, text, font, fill, max_width, max_bottom=None, min_size=10):
        text = str(text or "")
        max_width = int(max_width or 0)
        if not text or max_width <= 0:
            return position[1], True

        x, y = position
        fitted_font = self._fit_single_line_font(draw, text, font, max_width, min_size=min_size)
        line_height = self._line_height(draw, fitted_font)
        if max_bottom is not None and y + line_height > max_bottom:
            return y, False

        self._text(draw, (x, y), text, fitted_font, fill)
        return y + line_height, True

    def _draw_single_line_clipped_text(self, draw, position, text, font, fill, max_width, max_bottom=None, min_size=10):
        text = str(text or "")
        max_width = int(max_width or 0)
        if not text or max_width <= 0:
            return position[1], True

        x, y = position
        fitted_font = self._fit_single_line_font(draw, text, font, max_width, min_size=min_size)
        line_height = self._line_height(draw, fitted_font)
        if max_bottom is not None and y + line_height > max_bottom:
            return y, False
        if self._text_width(draw, text, fitted_font) <= max_width:
            self._text(draw, (x, y), text, fitted_font, fill)
        else:
            self._draw_clipped_text(draw, (x, y), text, fitted_font, fill, max_width, line_height)
        return y + line_height, True

    def _fit_single_line_font(self, draw, text, font, max_width, min_size=10):
        if self._text_width(draw, text, font) <= max_width:
            return font

        current_size = int(getattr(font, "size", 0) or 0)
        if current_size <= min_size:
            return font

        for size in range(current_size - 1, min_size - 1, -1):
            candidate = self._font(size)
            if self._text_width(draw, text, candidate) <= max_width:
                return candidate
        return self._font(min_size)

    def _draw_wrapped_text(self, draw, position, text, font, fill, max_width, max_bottom=None, line_gap=2):
        text = str(text or "")
        max_width = int(max_width or 0)
        if not text or max_width <= 0:
            return position[1], True

        x, y = position
        lines = self._wrap_text(draw, text, font, max_width)
        line_height = self._line_height(draw, font)
        total_height = len(lines) * line_height + max(0, len(lines) - 1) * line_gap
        if max_bottom is not None and y + total_height > max_bottom:
            return y, False

        cursor_y = y
        for line in lines:
            self._text(draw, (x, cursor_y), line, font, fill)
            cursor_y += line_height + line_gap
        return cursor_y - line_gap, True

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

    def _bullet(self, draw, x, y, fill):
        draw.ellipse((x, y, x + 8, y + 8), fill=fill)

    def _rounded_rect(self, draw, box, radius, outline, width, fill=None):
        try:
            draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
        except AttributeError:
            draw.rectangle(box, fill=fill, outline=outline, width=width)

    def _minutes_to_hours(self, minutes):
        try:
            return int(round(int(minutes) / 60))
        except Exception:
            return 0

    def _display_value(self, value):
        return "-" if value is None else str(value)

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

    def _cache_dir(self):
        return self.cache_dir(leaf=".steam_profile_dashboard_cache", create=True)

    def _cache_path(self, cache_key):
        return os.path.join(self._cache_dir(), f"{cache_key}.json")

    def _cache_image_path(self, cache_key):
        return os.path.join(self._cache_dir(), f"{cache_key}.png")

    def _avatar_cache_path(self, url):
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        return os.path.join(self._cache_dir(), f"avatar_{digest}.png")

    def _game_icon_cache_path(self, url):
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        return os.path.join(self._cache_dir(), f"gameicon_{digest}.png")

    def _badge_icon_cache_path(self, url):
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        return os.path.join(self._cache_dir(), f"badgeicon_{digest}.png")

    def _cache_key(self, settings, dimensions, steam_id):
        parts = [
            STEAM_DASHBOARD_STYLE_VERSION,
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
