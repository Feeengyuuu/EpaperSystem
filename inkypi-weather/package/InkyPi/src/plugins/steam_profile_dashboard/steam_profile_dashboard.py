from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.http_client import get_http_session
from PIL import Image, ImageDraw, ImageFont, ImageOps
from io import BytesIO
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
import time

logger = logging.getLogger(__name__)

STEAM_API_BASE = "https://api.steampowered.com"
STEAM_STORE_APPDETAILS = "https://store.steampowered.com/api/appdetails"
DEFAULT_STEAM_ID = "76561198176386838"
STEAM_NAME_DISPLAY_VERSION = "zh-store-full-single-fetch-v1"
STEAM_DASHBOARD_STYLE_VERSION = "subtle-controller-bg-friend-id-live-v1"
STEAM_BACKGROUND_IMAGE = "background.png"
STEAM_PRIMARY_GAME_LANGUAGE = "schinese"
STEAM_SECONDARY_GAME_LANGUAGE = "english"

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
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        api_key = device_config.load_env_key("STEAM_API_KEY")
        if not api_key:
            raise RuntimeError("未配置 Steam API Key。请在 API Keys 中添加 STEAM_API_KEY。")

        steam_id = str(settings.get("steamId") or DEFAULT_STEAM_ID).strip()
        if not steam_id:
            raise RuntimeError("需要填写 SteamID64。")

        status_cache_seconds = self._int_setting(settings, "statusCacheSeconds", 60, 30, 3600)
        full_cache_minutes = self._int_setting(
            settings,
            "fullCacheMinutes",
            self._int_setting(settings, "cacheMinutes", 30, 5, 1440),
            5,
            1440,
        )
        cache_key = self._cache_key(settings, dimensions, steam_id)
        cache_entry = self._read_cache(cache_key)
        now = time.time()

        if cache_entry and self._cache_is_fresh(cache_entry, now, status_cache_seconds, full_cache_minutes):
            image_path = cache_entry.get("image_path")
            if image_path and os.path.exists(image_path):
                logger.info("Using cached Steam profile dashboard.")
                self._write_steam_profile_context(cache_entry.get("data") or {}, now)
                return Image.open(image_path).convert("RGB")

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
            image = self._render_dashboard(data, dimensions)
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
                return Image.open(cache_entry["image_path"]).convert("RGB")
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
        for game in (data.get("recent_games") or [])[:5]:
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
            recent_limit = self._int_setting(settings, "recentLimit", 5, 0, 10)
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
        recent_limit = self._int_setting(settings, "recentLimit", 5, 0, 10)
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

        for game in (data.get("recent_games") or [])[:5]:
            add(game.get("appid"))

        spotlight = data.get("spotlight_game") or {}
        add(spotlight.get("appid"))

        owned_games = data.get("owned_games") or []
        sorted_games = sorted(owned_games, key=lambda game: game.get("playtime_forever", 0), reverse=True)
        for game in sorted_games[:3]:
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

    def _render_dashboard(self, data, dimensions):
        width, height = dimensions
        bg = (0, 0, 0)
        panel = (0, 0, 0)
        panel_border = (255, 255, 255)
        ink = (255, 255, 255)
        gray = (255, 255, 255)
        light = (255, 255, 255)
        accent = (102, 192, 244)
        accent_online = (88, 205, 118)
        image = self._dashboard_background((width, height), bg)
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

        avatar = self._avatar_image(data["profile"].get("avatarfull"), avatar_size)
        image.paste(avatar, (margin + 10, panel_y + 2), avatar if avatar.mode == "RGBA" else None)

        self._rounded_rect(
            draw,
            (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
            radius=0,
            outline=panel_border,
            width=3,
            fill=panel,
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
            y, _ = self._draw_wrapped_text(draw, (panel_x + 18, y), f"正在玩：{current_game}", fonts["body"], ink, top_line_width)
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

        lower_y = panel_y + panel_h + 38
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

        self._text(draw, (left_x, content_y), "最近 / 实时", fonts["section"], ink)
        y = content_y + 31
        left_line_width = col_w - 34
        for line in self._recent_lines(data):
            next_y, fits = self._draw_wrapped_text(
                draw,
                (left_x + 18, y),
                line,
                fonts["small"],
                ink,
                left_line_width,
                max_bottom=lower_y + lower_h - 18,
            )
            if not fits:
                break
            self._bullet(draw, left_x, y + 7, accent_online)
            y = next_y + 5

        self._text(draw, (right_x, content_y), "游戏库 / 好友", fonts["section"], ink)
        y = content_y + 31
        right_line_width = width - margin - (right_x + 18) - 12
        top_game_lines = self._top_game_lines(data)
        if top_game_lines:
            self._text(draw, (right_x + 18, y), "常玩 TOP 3", fonts["tiny"], gray)
            y += 21
            for line in top_game_lines:
                next_y, fits = self._draw_single_line_text(
                    draw,
                    (right_x + 18, y),
                    line,
                    fonts["small"],
                    ink,
                    right_line_width,
                    max_bottom=lower_y + lower_h - 18,
                    min_size=10,
                )
                if not fits:
                    break
                y = next_y + 4

            if y <= lower_y + lower_h - 44:
                draw.line((right_x + 18, y - 3, width - margin - 18, y - 3), fill=light, width=1)
                y += 9

        for line in self._library_lines(data):
            next_y, fits = self._draw_wrapped_text(
                draw,
                (right_x + 18, y),
                line,
                fonts["small"],
                ink,
                right_line_width,
                max_bottom=lower_y + lower_h - 18,
            )
            if not fits:
                break
            self._bullet(draw, right_x, y + 7, accent)
            y = next_y + 5

        refresh_mode = self._refresh_mode_label(data.get("refresh_mode", "full"))
        footer = f"更新 {data.get('updated_at')}  |  Steam 请求 {data.get('api_calls', 0)} 次（{refresh_mode}）"
        warnings = data.get("warnings") or []
        if warnings:
            footer += f"  |  {len(warnings)} 项隐私/缺失"
        self._text(draw, (margin, height - 19), footer, fonts["tiny"], gray)
        return image

    def _dashboard_background(self, dimensions, fallback_color):
        path = self.get_plugin_dir(STEAM_BACKGROUND_IMAGE)
        try:
            background = Image.open(path).convert("RGB")
            return ImageOps.fit(background, dimensions, method=Image.Resampling.LANCZOS)
        except Exception as e:
            logger.warning(f"Steam dashboard background unavailable: {e}")
            return Image.new("RGB", dimensions, fallback_color)

    def _recent_lines(self, data):
        lines = []
        profile = data.get("profile", {})
        if profile.get("gameid") or profile.get("gameextrainfo"):
            name = self._display_game_name(data, profile.get("gameid"), profile.get("gameextrainfo"))
            lines.append(f"正在玩：{name}")

        for game in data.get("recent_games", [])[:5]:
            name = self._display_game_name(data, game.get("appid"), game.get("name"))
            two_weeks = self._minutes_to_hours(game.get("playtime_2weeks", 0))
            forever = self._minutes_to_hours(game.get("playtime_forever", 0))
            lines.append(f"{name}：近2周 {two_weeks}h / 总计 {forever}h")

        spotlight = data.get("spotlight_game") or {}
        details = data.get("app_details") or {}
        if details:
            genres = ", ".join(genre.get("description", "") for genre in details.get("genres", [])[:2])
            if genres:
                lines.append(f"类型：{genres}")
            if details.get("metacritic", {}).get("score"):
                lines.append(f"媒体评分：{details['metacritic']['score']}")
        elif spotlight and (spotlight.get("appid") or spotlight.get("name")):
            name = self._display_game_name(data, spotlight.get("appid"), spotlight.get("name"))
            lines.append(f"重点游戏：{name}")

        if not lines:
            lines.append("没有公开的近期游戏数据")
        return lines

    def _top_game_lines(self, data):
        lines = []
        games = data.get("owned_games", [])
        sorted_games = sorted(games, key=lambda game: game.get("playtime_forever", 0), reverse=True)

        for index, game in enumerate(sorted_games[:3], start=1):
            hours = self._minutes_to_hours(game.get("playtime_forever", 0))
            if hours <= 0:
                continue
            name = self._display_game_name(data, game.get("appid"), game.get("name"))
            lines.append(f"TOP {index}  {name}  {hours}h")
        return lines

    def _library_lines(self, data):
        lines = []
        badges = data.get("badges") or {}
        if badges:
            badge_count = len(badges.get("badges", []))
            xp = badges.get("player_xp")
            lines.append(f"徽章：{badge_count}  XP：{self._display_value(xp)}")

        bans = data.get("bans") or {}
        if bans:
            vac = "VAC" if bans.get("VACBanned") else "无 VAC"
            game_bans = bans.get("NumberOfGameBans", 0)
            days = bans.get("DaysSinceLastBan", 0)
            lines.append(f"封禁：{vac}，游戏封禁 {game_bans}，距上次 {days} 天")

        online_friends = [friend for friend in data.get("friends", []) if int(friend.get("personastate", 0)) != 0]
        for friend in online_friends[:3]:
            name = friend.get("personaname", "好友")
            has_game = friend.get("gameid") or friend.get("gameextrainfo")
            if has_game:
                game = self._display_game_name(data, friend.get("gameid"), friend.get("gameextrainfo"))
                lines.append(f"{name}: {game}")
            else:
                lines.append(f"{name}: {PERSONA_STATES.get(int(friend.get('personastate', 0)), '在线')}")

        if not lines:
            lines.append("游戏库/好友数据为隐私")
        return lines

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
            status_text = self._friend_live_text(data, friend)
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
            self._draw_single_line_text(
                draw,
                (text_x + dot_size + 7, status_y),
                status_text,
                fonts["tiny"],
                ink,
                max(20, width - (text_x + dot_size + 7 - x)),
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
        if friend.get("gameid") or friend.get("gameextrainfo"):
            return (88, 205, 118)
        try:
            state = int(friend.get("personastate", 0) or 0)
        except Exception:
            state = 0
        if state == 1:
            return (88, 205, 118)
        return (234, 176, 72)

    def _avatar_image(self, url, size):
        avatar = None
        if url:
            avatar_cache_path = self._avatar_cache_path(url)
            try:
                if os.path.exists(avatar_cache_path) and time.time() - os.path.getmtime(avatar_cache_path) < 7 * 24 * 60 * 60:
                    avatar = Image.open(avatar_cache_path).convert("RGB")
                else:
                    session = get_http_session()
                    response = session.get(url, timeout=25)
                    response.raise_for_status()
                    avatar = Image.open(BytesIO(response.content)).convert("RGB")
                    avatar = ImageOps.exif_transpose(avatar)
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
            "tiny": self._font(max(11, base - 4)),
        }

    def _font(self, size, bold=False):
        candidates = []
        plugin_dir = self.get_plugin_dir()
        src_dir = os.path.abspath(os.path.join(plugin_dir, "..", ".."))
        candidates.extend([
            os.path.join(src_dir, "static", "fonts", "LXGWWenKai-Regular.ttf"),
            os.path.join(plugin_dir, "..", "literature_clock", "fonts", "LXGWWenKai-Regular.ttf"),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/msyh.ttc",
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
        path = os.path.join(self.get_plugin_dir(), ".steam_profile_dashboard_cache")
        os.makedirs(path, exist_ok=True)
        return path

    def _cache_path(self, cache_key):
        return os.path.join(self._cache_dir(), f"{cache_key}.json")

    def _cache_image_path(self, cache_key):
        return os.path.join(self._cache_dir(), f"{cache_key}.png")

    def _avatar_cache_path(self, url):
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        return os.path.join(self._cache_dir(), f"avatar_{digest}.png")

    def _cache_key(self, settings, dimensions, steam_id):
        parts = [
            STEAM_DASHBOARD_STYLE_VERSION,
            STEAM_NAME_DISPLAY_VERSION,
            steam_id,
            str(dimensions),
            str(settings.get("statusCacheSeconds", 60)),
            str(settings.get("fullCacheMinutes", settings.get("cacheMinutes", 30))),
            str(settings.get("friendLimit", 100)),
            str(settings.get("recentLimit", 5)),
            str(settings.get("includeFriends", "true")),
            str(settings.get("refreshRecentOnGameChange", "true")),
            str(settings.get("includeAppDetails", "true")),
            str(settings.get("includeBadges", "true")),
            str(settings.get("includeBans", "true")),
            str(settings.get("language", "schinese")),
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
