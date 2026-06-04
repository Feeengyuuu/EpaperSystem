from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import time
import urllib.parse
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.http_client import get_http_session
from utils.theme_utils import get_theme_context

logger = logging.getLogger(__name__)

PLUGIN_ID = "lol_info"
STYLE_VERSION = "lol-info-v7-owned-latest-skin-pool"
DEFAULT_GAME_NAME = "Hide on bush"
DEFAULT_TAG_LINE = "KR1"
DEFAULT_PLATFORM = "kr"
DEFAULT_REGION = "asia"
DDRAGON_VERSION_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_BASE = "https://ddragon.leagueoflegends.com/cdn"
CDRAGON_RAW_BASE = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default"
CDRAGON_SKINS_URL = f"{CDRAGON_RAW_BASE}/v1/skins.json"
LOL_LOGO_FILE = "league-of-legends-logo.png"
RIOT_LOGO_FILE = "riot-games-logo.png"

PLATFORM_ROUTES = {"br1", "eun1", "euw1", "jp1", "kr", "la1", "la2", "na1", "oc1", "tr1"}
REGIONAL_ROUTES = {"americas", "asia", "europe", "sea"}

TIER_LABELS = {
    "IRON": "黑铁",
    "BRONZE": "青铜",
    "SILVER": "白银",
    "GOLD": "黄金",
    "PLATINUM": "铂金",
    "EMERALD": "翡翠",
    "DIAMOND": "钻石",
    "MASTER": "大师",
    "GRANDMASTER": "宗师",
    "CHALLENGER": "王者",
}

QUEUE_LABELS = {
    "RANKED_SOLO_5x5": "单双排",
    "RANKED_FLEX_SR": "灵活排位",
    "RANKED_FLEX_TT": "灵活排位",
}


class LoLInfo(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["defaultGameName"] = DEFAULT_GAME_NAME
        params["defaultTagLine"] = DEFAULT_TAG_LINE
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        identity = self._identity(settings)
        now = time.time()
        refresh_minutes = self._bounded_int(settings.get("refreshMinutes"), 120, 15, 1440)
        cache_key = self._cache_key(settings, dimensions, identity)
        cache = self._read_json(self._cache_path(cache_key), {})
        force_refresh = self._enabled(settings.get("forceRefresh"), default=False)

        cache_valid = (
            not force_refresh
            and cache.get("schema") == STYLE_VERSION
            and now - float(cache.get("updated_ts", 0) or 0) < refresh_minutes * 60
            and cache.get("image_path")
            and Path(cache["image_path"]).exists()
            and cache.get("data")
        )

        try:
            if cache_valid:
                data = cache.get("data") or {}
                data_updated_ts = float(cache.get("updated_ts", now) or now)
            else:
                data = self._sample_payload() if self._enabled(settings.get("useMockData"), default=False) else self._fetch_dashboard_data(settings, device_config)
                data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                data_updated_ts = now
            image = self._render_dashboard(data, dimensions, settings, get_theme_context(device_config))
            image_path = self._cache_image_path(cache_key)
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(image_path)
            self._write_json(self._cache_path(cache_key), {
                "schema": STYLE_VERSION,
                "updated_ts": data_updated_ts,
                "image_updated_ts": now,
                "identity": identity,
                "image_path": str(image_path),
                "data": self._json_safe(data),
            })
            self._write_context(data, data_updated_ts, refresh_minutes)
            return image
        except Exception as exc:
            logger.error("LoLInfo generation failed: %s", exc)
            if cache.get("image_path") and Path(cache["image_path"]).exists():
                logger.warning("Using stale LoLInfo cache.")
                self._write_context(cache.get("data") or {}, cache.get("updated_ts", now), refresh_minutes)
                return Image.open(cache["image_path"]).convert("RGB")
            raise RuntimeError(f"LoLInfo 生成失败：{exc}")

    def _fetch_dashboard_data(self, settings, device_config):
        api_key = self._riot_key(settings, device_config)
        if not api_key:
            raise RuntimeError("需要 Riot API key，可在 API key 页面保存 Riot_KEY。")

        game_name = str(settings.get("gameName") or DEFAULT_GAME_NAME).strip()
        tag_line = str(settings.get("tagLine") or DEFAULT_TAG_LINE).strip().lstrip("#")
        platform = self._route(settings.get("platformRoute"), DEFAULT_PLATFORM, PLATFORM_ROUTES)
        region = self._route(settings.get("regionalRoute"), DEFAULT_REGION, REGIONAL_ROUTES)
        recent_limit = self._bounded_int(settings.get("recentLimit"), 5, 3, 10)
        mastery_limit = self._bounded_int(settings.get("masteryLimit"), 5, 3, 8)

        champions = self._dragon_champions()
        headers = {"X-Riot-Token": api_key}
        calls = 0

        def riot_get(scope, path, params=None, optional=False):
            nonlocal calls
            calls += 1
            try:
                base = f"https://{scope}.api.riotgames.com"
                response = get_http_session().get(f"{base}{path}", headers=headers, params=params or {}, timeout=35)
                if optional and response.status_code in {404, 403}:
                    return None
                response.raise_for_status()
                return response.json()
            except Exception:
                if optional:
                    return None
                raise

        account = riot_get(
            region,
            "/riot/account/v1/accounts/by-riot-id/"
            f"{urllib.parse.quote(game_name)}/{urllib.parse.quote(tag_line)}",
        )
        puuid = account.get("puuid")
        if not puuid:
            raise RuntimeError("Riot ID 未返回 PUUID。")

        summoner = riot_get(platform, f"/lol/summoner/v4/summoners/by-puuid/{puuid}", optional=True) or {}
        leagues = riot_get(platform, f"/lol/league/v4/entries/by-puuid/{puuid}", optional=True) or []
        mastery = riot_get(
            platform,
            f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top",
            params={"count": mastery_limit},
            optional=True,
        ) or []
        challenge_data = {}
        if self._enabled(settings.get("includeChallenges"), default=True):
            challenge_data = riot_get(platform, f"/lol/challenges/v1/player-data/{puuid}", optional=True) or {}
        active_game = None
        if self._enabled(settings.get("includeActiveGame"), default=True):
            active_game = riot_get(platform, f"/lol/spectator/v5/active-games/by-summoner/{puuid}", optional=True)

        match_ids = riot_get(
            region,
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            params={"start": 0, "count": recent_limit},
            optional=True,
        ) or []
        matches = []
        for match_id in match_ids[:recent_limit]:
            detail = riot_get(region, f"/lol/match/v5/matches/{match_id}", optional=True)
            summary = self._match_summary(detail, puuid, champions)
            if summary:
                matches.append(summary)

        mastery_summaries = [self._mastery_summary(item, champions) for item in mastery]
        featured_champions = self._featured_champions(mastery_summaries, matches)
        skin_art_pool = self._skin_art_pool(featured_champions, champions, settings)
        ranked = self._best_rank(leagues)
        return {
            "source": "Riot Games API",
            "api_calls": calls,
            "region": region.upper(),
            "platform": platform.upper(),
            "account": account,
            "summoner": summoner,
            "ranked": ranked,
            "leagues": leagues,
            "mastery": mastery_summaries,
            "matches": matches,
            "featured_champions": featured_champions,
            "skin_art_pool": skin_art_pool,
            "summary": self._recent_summary(matches),
            "challenge_points": self._challenge_points(challenge_data),
            "active_game": active_game,
            "champions": champions,
        }

    def _match_summary(self, detail, puuid, champions):
        if not isinstance(detail, dict):
            return None
        info = detail.get("info") or {}
        participants = info.get("participants") or []
        player = next((row for row in participants if row.get("puuid") == puuid), None)
        if not player:
            return None
        team_id = player.get("teamId")
        team_kills = sum(int(row.get("kills") or 0) for row in participants if row.get("teamId") == team_id)
        duration = max(1, int(info.get("gameDuration") or 0))
        champion = self._champion_info(player.get("championId"), player.get("championName"), champions)
        return {
            "match_id": detail.get("metadata", {}).get("matchId") or str(info.get("gameId") or ""),
            "champion_id": player.get("championId"),
            "champion_key": champion.get("id"),
            "champion_name": champion.get("name") or player.get("championName") or "未知英雄",
            "champion_icon": champion.get("icon_url") or "",
            "kills": int(player.get("kills") or 0),
            "deaths": int(player.get("deaths") or 0),
            "assists": int(player.get("assists") or 0),
            "win": bool(player.get("win")),
            "lane": player.get("teamPosition") or player.get("individualPosition") or "-",
            "queue": self._queue_label(info.get("queueId")),
            "timestamp": int(info.get("gameEndTimestamp") or info.get("gameStartTimestamp") or 0),
            "duration": duration,
            "cs": int(player.get("totalMinionsKilled") or 0) + int(player.get("neutralMinionsKilled") or 0),
            "gold": int(player.get("goldEarned") or 0),
            "damage": int(player.get("totalDamageDealtToChampions") or 0),
            "kp": (int(player.get("kills") or 0) + int(player.get("assists") or 0)) / team_kills * 100 if team_kills else 0,
        }

    def _mastery_summary(self, item, champions):
        champion = self._champion_info(item.get("championId"), None, champions)
        return {
            "champion_id": item.get("championId"),
            "champion_key": champion.get("id"),
            "champion_name": champion.get("name") or "未知英雄",
            "champion_icon": champion.get("icon_url") or "",
            "level": int(item.get("championLevel") or 0),
            "points": int(item.get("championPoints") or 0),
        }

    def _dragon_champions(self):
        cache_path = self._cache_dir() / "ddragon_champions_zh.json"
        cache = self._read_json(cache_path, {})
        if cache.get("updated_ts") and time.time() - float(cache["updated_ts"]) < 3 * 24 * 60 * 60:
            champions = cache.get("champions") or {}
            if champions.get("by_key") and champions.get("version"):
                return champions
        try:
            session = get_http_session()
            version_response = session.get(DDRAGON_VERSION_URL, timeout=25)
            version_response.raise_for_status()
            version = version_response.json()[0]
            champion_response = session.get(f"{DDRAGON_BASE}/{version}/data/zh_CN/champion.json", timeout=35)
            champion_response.raise_for_status()
            data = champion_response.json().get("data") or {}
            by_key = {}
            by_id = {}
            for item in data.values():
                champ = {
                    "id": item.get("id"),
                    "key": item.get("key"),
                    "name": item.get("name"),
                    "title": item.get("title"),
                    "icon_url": f"{DDRAGON_BASE}/{version}/img/champion/{item.get('id')}.png",
                }
                if str(item.get("key", "")).isdigit():
                    by_key[str(int(item["key"]))] = champ
                by_id[str(item.get("id") or "").lower()] = champ
            champions = {"version": version, "by_key": by_key, "by_id": by_id, "profile_icon_base": f"{DDRAGON_BASE}/{version}/img/profileicon"}
            self._write_json(cache_path, {"updated_ts": time.time(), "champions": champions})
            return champions
        except Exception as exc:
            logger.warning("LoL Data Dragon champion data unavailable: %s", exc)
        return {"version": "", "by_key": {}, "by_id": {}, "profile_icon_base": ""}

    def _featured_champions(self, mastery, matches, limit=5):
        ranked = {}

        def ensure(item):
            key = str(item.get("champion_key") or "").strip()
            if not key:
                return None
            if key not in ranked:
                ranked[key] = {
                    "champion_key": key,
                    "champion_name": item.get("champion_name") or key,
                    "champion_icon": item.get("champion_icon") or "",
                    "mastery_points": 0,
                    "recent_games": 0,
                    "score": 0,
                }
            return ranked[key]

        for idx, item in enumerate(mastery or []):
            row = ensure(item)
            if not row:
                continue
            points = int(item.get("points") or 0)
            row["mastery_points"] = max(row["mastery_points"], points)
            row["score"] += points + max(0, 5 - idx) * 50000

        for item in matches or []:
            row = ensure(item)
            if not row:
                continue
            row["recent_games"] += 1
            row["score"] += 180000

        return sorted(
            ranked.values(),
            key=lambda row: (row["score"], row["mastery_points"], row["recent_games"]),
            reverse=True,
        )[:limit]

    def _skin_art_pool(self, featured_champions, champions, settings=None, max_per_champion=5):
        version = (champions or {}).get("version") or ""
        if not version:
            return []
        settings = settings or {}
        featured_pool = []
        for champion in (featured_champions or [])[:5]:
            champion_key = str(champion.get("champion_key") or "").strip()
            if not champion_key:
                continue
            detail = self._dragon_champion_detail(champion_key, version)
            skins = detail.get("skins") or []
            if not skins:
                continue
            non_chroma = [skin for skin in skins if "parentSkin" not in skin]
            non_default = []
            for skin in non_chroma:
                try:
                    if int(skin.get("num") or 0) != 0:
                        non_default.append(skin)
                except Exception:
                    continue
            selected_skins = (non_default or non_chroma)[:max_per_champion]
            for skin in selected_skins:
                try:
                    skin_num = int(skin.get("num") or 0)
                except Exception:
                    continue
                skin_name = str(skin.get("name") or "").strip()
                if not skin_name or skin_name.lower() == "default":
                    skin_name = champion.get("champion_name") or champion_key
                featured_pool.append({
                    "id": f"{champion_key}:{skin_num}",
                    "champion_key": champion_key,
                    "champion_name": champion.get("champion_name") or champion_key,
                    "champion_icon": champion.get("champion_icon") or "",
                    "skin_num": skin_num,
                    "skin_name": skin_name,
                    "splash_url": f"{DDRAGON_BASE}/img/champion/splash/{champion_key}_{skin_num}.jpg",
                    "loading_url": f"{DDRAGON_BASE}/img/champion/loading/{champion_key}_{skin_num}.jpg",
                    "pool_source": "featured",
                    "mastery_points": champion.get("mastery_points") or 0,
                    "recent_games": champion.get("recent_games") or 0,
                })
        owned_refs = self._skin_ref_tokens(settings.get("ownedSkinIds"))
        include_latest = self._enabled(settings.get("includeLatestSkins"), default=bool(settings))
        latest_count = self._bounded_int(settings.get("latestSkinCount"), 8, 0, 24)
        catalog = []
        if owned_refs or (include_latest and latest_count > 0):
            cache_hours = self._bounded_int(settings.get("latestSkinCacheHours"), 6, 1, 168)
            catalog = self._communitydragon_skins(
                cache_hours=cache_hours,
                force_refresh=self._enabled(settings.get("forceRefresh"), default=False),
            )
        owned_pool = self._owned_skin_art_pool(owned_refs, catalog, champions)
        latest_pool = self._latest_skin_art_pool(catalog, champions, latest_count) if include_latest and latest_count > 0 else []
        return self._dedupe_skin_art_pool(owned_pool + latest_pool + featured_pool)

    def _communitydragon_skins(self, cache_hours=6, force_refresh=False):
        cache_path = self._cache_dir() / "communitydragon_skins_latest.json"
        cache = self._read_json(cache_path, {})
        if (
            not force_refresh
            and cache.get("updated_ts")
            and time.time() - float(cache["updated_ts"]) < max(1, int(cache_hours)) * 60 * 60
            and isinstance(cache.get("records"), list)
        ):
            return cache.get("records") or []
        try:
            response = get_http_session().get(CDRAGON_SKINS_URL, timeout=35)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                records = list(payload.values())
            elif isinstance(payload, list):
                records = payload
            else:
                records = []
            self._write_json(cache_path, {"updated_ts": time.time(), "records": records})
            return records
        except Exception as exc:
            logger.warning("LoL CommunityDragon skin catalog unavailable: %s", exc)
            return cache.get("records") or []

    def _owned_skin_art_pool(self, refs, records, champions):
        raw_refs = [str(ref).strip() for ref in refs if str(ref).strip()]
        normalized_refs = [ref.lower() for ref in raw_refs]
        if not raw_refs:
            return []
        pool = []
        matched = set()
        for record in records or []:
            art = self._skin_art_from_cdragon(record, champions, "owned")
            if not art:
                continue
            keys = self._skin_art_match_keys(art)
            hit = next((ref for ref in normalized_refs if ref in keys), None)
            if not hit:
                continue
            matched.add(hit)
            pool.append(art)
        for raw_ref, normalized_ref in zip(raw_refs, normalized_refs):
            if normalized_ref in matched:
                continue
            manual = self._skin_art_from_manual_ref(raw_ref, champions)
            if manual:
                pool.append(manual)
        return pool

    def _latest_skin_art_pool(self, records, champions, limit):
        pool = []
        for record in records or []:
            art = self._skin_art_from_cdragon(record, champions, "latest")
            if art:
                pool.append(art)
        pool = sorted(pool, key=self._skin_art_release_key, reverse=True)
        return pool[: max(0, int(limit))]

    def _skin_art_from_cdragon(self, skin, champions, source):
        if not isinstance(skin, dict):
            return None
        champion_id = self._safe_int(skin.get("championId") or skin.get("champion_id"))
        skin_id = self._safe_int(skin.get("id") or skin.get("skinId") or skin.get("skin_id"))
        skin_num = self._safe_int(skin.get("skinNum") or skin.get("num"))
        if champion_id is None and skin_id is not None:
            champion_id = skin_id // 1000
        if skin_num is None and skin_id is not None and champion_id:
            skin_num = skin_id - champion_id * 1000
        if skin.get("isBase") is True or skin_num in (None, 0):
            return None
        if skin.get("parentSkin") or skin.get("parentSkinId"):
            return None
        champion = ((champions or {}).get("by_key") or {}).get(str(champion_id)) or {}
        champion_key = str(
            champion.get("id")
            or skin.get("championName")
            or skin.get("championAlias")
            or ""
        ).strip()
        champion_key = "".join(ch for ch in champion_key if ch.isalnum())
        if not champion_key:
            return None
        champion_name = champion.get("name") or skin.get("championName") or champion_key
        skin_name = str(skin.get("name") or skin.get("skinName") or "").strip() or f"{champion_name} {skin_num}"
        splash_url = (
            self._communitydragon_asset_url(skin.get("uncenteredSplashPath"))
            or self._communitydragon_asset_url(skin.get("splashPath"))
            or f"{DDRAGON_BASE}/img/champion/splash/{champion_key}_{skin_num}.jpg"
        )
        loading_url = (
            self._communitydragon_asset_url(skin.get("loadScreenPath"))
            or self._communitydragon_asset_url(skin.get("loadScreenVintagePath"))
            or f"{DDRAGON_BASE}/img/champion/loading/{champion_key}_{skin_num}.jpg"
        )
        return {
            "id": f"{champion_key}:{skin_num}",
            "skin_id": str(skin_id or ""),
            "champion_key": champion_key,
            "champion_name": champion_name,
            "champion_icon": champion.get("icon_url") or "",
            "skin_num": int(skin_num),
            "skin_name": skin_name,
            "splash_url": splash_url,
            "loading_url": loading_url,
            "release_date": str(skin.get("releaseDate") or skin.get("release_date") or skin.get("lastUpdated") or ""),
            "pool_source": source,
        }

    def _skin_art_from_manual_ref(self, ref, champions):
        champion_key = ""
        skin_num = None
        text = str(ref or "").strip()
        for sep in (":", "_"):
            if sep in text:
                left, right = text.rsplit(sep, 1)
                if right.strip().isdigit():
                    champion_key = "".join(ch for ch in left.strip() if ch.isalnum())
                    skin_num = int(right.strip())
                break
        if not champion_key or skin_num in (None, 0):
            return None
        by_id = (champions or {}).get("by_id") or {}
        champion = by_id.get(champion_key.lower()) or {}
        champion_key = champion.get("id") or champion_key
        champion_name = champion.get("name") or champion_key
        return {
            "id": f"{champion_key}:{skin_num}",
            "skin_id": "",
            "champion_key": champion_key,
            "champion_name": champion_name,
            "champion_icon": champion.get("icon_url") or "",
            "skin_num": skin_num,
            "skin_name": f"{champion_name} {skin_num}",
            "splash_url": f"{DDRAGON_BASE}/img/champion/splash/{champion_key}_{skin_num}.jpg",
            "loading_url": f"{DDRAGON_BASE}/img/champion/loading/{champion_key}_{skin_num}.jpg",
            "release_date": "",
            "pool_source": "owned",
        }

    def _communitydragon_asset_url(self, path):
        path = str(path or "").strip().replace("\\", "/")
        if not path:
            return ""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        path = path.lstrip("/")
        prefix = "lol-game-data/assets/"
        if path.lower().startswith(prefix):
            path = path[len(prefix):]
        return f"{CDRAGON_RAW_BASE}/{path.lower()}"

    @staticmethod
    def _skin_ref_tokens(value):
        raw = value if isinstance(value, str) else ""
        for sep in ("\n", "\r", ";", "|"):
            raw = raw.replace(sep, ",")
        return [part.strip() for part in raw.split(",") if part.strip()]

    @staticmethod
    def _skin_art_match_keys(art):
        keys = {
            str(art.get("id") or "").lower(),
            str(art.get("skin_id") or "").lower(),
            f"{art.get('champion_key')}:{art.get('skin_num')}".lower(),
            f"{art.get('champion_key')}_{art.get('skin_num')}".lower(),
        }
        return {key for key in keys if key and key != "none"}

    def _dedupe_skin_art_pool(self, pool):
        result = []
        seen = set()
        for item in pool or []:
            keys = self._skin_art_match_keys(item)
            if keys & seen:
                continue
            seen.update(keys)
            result.append(item)
        return result

    @staticmethod
    def _skin_art_release_key(item):
        raw = str(item.get("release_date") or "")
        digits = "".join(ch for ch in raw if ch.isdigit())
        date_value = int((digits + "0" * 14)[:14]) if digits else 0
        try:
            skin_id = int(item.get("skin_id") or 0)
        except Exception:
            skin_id = 0
        return date_value, skin_id, int(item.get("skin_num") or 0)

    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except Exception:
            return None

    def _dragon_champion_detail(self, champion_key, version):
        safe_key = "".join(ch for ch in str(champion_key or "") if ch.isalnum())
        safe_version = "".join(ch for ch in str(version or "") if ch.isalnum() or ch in ".-_")
        if not safe_key or not safe_version:
            return {}
        cache_path = self._cache_dir() / f"ddragon_champion_{safe_version}_{safe_key}_zh.json"
        cache = self._read_json(cache_path, {})
        if cache.get("updated_ts") and time.time() - float(cache["updated_ts"]) < 14 * 24 * 60 * 60:
            detail = cache.get("detail") or {}
            if detail.get("skins"):
                return detail
        try:
            response = get_http_session().get(f"{DDRAGON_BASE}/{version}/data/zh_CN/champion/{safe_key}.json", timeout=25)
            response.raise_for_status()
            detail = (response.json().get("data") or {}).get(safe_key) or {}
            self._write_json(cache_path, {"updated_ts": time.time(), "detail": detail})
            return detail
        except Exception as exc:
            logger.warning("LoL Data Dragon champion detail unavailable for %s: %s", safe_key, exc)
            return {}

    def _champion_info(self, champion_id, champion_name, champions):
        by_key = champions.get("by_key") or {}
        by_id = champions.get("by_id") or {}
        if str(champion_id or "").isdigit() and str(int(champion_id)) in by_key:
            return by_key[str(int(champion_id))]
        if champion_name and str(champion_name).lower() in by_id:
            return by_id[str(champion_name).lower()]
        return {"id": str(champion_name or champion_id or ""), "name": str(champion_name or "未知英雄"), "icon_url": ""}

    def _best_rank(self, leagues):
        if not leagues:
            return {}
        order = {"RANKED_SOLO_5x5": 0, "RANKED_FLEX_SR": 1}
        return sorted(leagues, key=lambda item: order.get(item.get("queueType"), 9))[0]

    def _recent_summary(self, matches):
        games = len(matches)
        kills = sum(row["kills"] for row in matches)
        deaths = sum(row["deaths"] for row in matches)
        assists = sum(row["assists"] for row in matches)
        wins = sum(1 for row in matches if row["win"])
        minutes = sum(row["duration"] for row in matches) / 60
        return {
            "games": games,
            "wins": wins,
            "losses": max(0, games - wins),
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "kda": (kills + assists) / max(1, deaths),
            "winrate": wins / games * 100 if games else 0,
            "cs_per_min": sum(row["cs"] for row in matches) / minutes if minutes else 0,
            "kp": sum(row["kp"] for row in matches) / games if games else 0,
        }

    def _challenge_points(self, challenge_data):
        total = (challenge_data or {}).get("totalPoints") or {}
        return int(total.get("current") or total.get("levelPoints") or 0)

    def _render_dashboard(self, data, dimensions, settings=None, theme_context=None):
        width, height = dimensions
        # Tokens follow docs/color-ui-guidelines.md: process black base,
        # warm paper linework, and limited vintage comic accent colors.
        bg = (5, 7, 12)
        panel = (18, 22, 35)
        border = (236, 232, 206)
        ink = (255, 250, 222)
        muted = (202, 190, 150)
        gold = (255, 205, 54)
        cyan = (107, 204, 255)
        green = (82, 202, 128)
        red = (255, 82, 74)

        image = Image.new("RGB", dimensions, bg)
        draw = ImageDraw.Draw(image)
        self._draw_background(draw, width, height)
        fonts = {
            "title": self._font(25, bold=True),
            "section": self._font(20, bold=True),
            "body": self._font(15),
            "small": self._font(13),
            "tiny": self._font(10),
            "micro": self._font(9),
        }

        margin = 22
        gap = 14
        top_y = 22
        top_h = 242
        left_w = 208
        center_w = 344
        right_w = width - margin * 2 - left_w - center_w - gap * 2
        left_box = (margin, top_y, margin + left_w, top_y + top_h)
        center_box = (left_box[2] + gap, top_y, left_box[2] + gap + center_w, top_y + top_h)
        right_box = (center_box[2] + gap, top_y, width - margin, top_y + top_h)
        bottom_box = (margin, top_y + top_h + 16, width - margin, height - 24)
        for box in (left_box, center_box, right_box, bottom_box):
            self._rect(draw, box, panel, border)

        self._draw_profile(image, draw, data, left_box, fonts, ink, muted, gold, cyan, green)
        self._draw_recent(image, draw, data, center_box, fonts, ink, muted, gold, green, red, cyan)
        self._draw_rank_mastery(image, draw, data, right_box, fonts, ink, muted, gold, green, cyan)
        self._draw_overview(image, draw, data, bottom_box, fonts, ink, muted, gold, green, cyan, red)
        return image

    def _draw_profile(self, image, draw, data, box, fonts, ink, muted, gold, cyan, green):
        x0, y0, x1, y1 = box
        account = data.get("account") or {}
        summoner = data.get("summoner") or {}
        name = f"{account.get('gameName') or DEFAULT_GAME_NAME} #{account.get('tagLine') or DEFAULT_TAG_LINE}"
        self._paste_asset_logo(image, LOL_LOGO_FILE, (x0 + 13, y0 + 8, x0 + 95, y0 + 48))
        self._text(draw, (x0 + 104, y0 + 15), "LoLInfo", fonts["section"], cyan)
        self._text(draw, (x0 + 104, y0 + 39), "账号档案", fonts["tiny"], muted)
        icon = self._profile_icon(summoner.get("profileIconId"), data.get("champions") or {}, 78)
        image.paste(icon, (x0 + 15, y0 + 62), icon)
        self._single(draw, (x0 + 104, y0 + 64), name, fonts["body"], ink, x1 - x0 - 118, 9)
        route_text = f"{data.get('platform') or '-'} / {data.get('region') or '-'}"
        self._single(draw, (x0 + 104, y0 + 92), route_text, fonts["tiny"], cyan, x1 - x0 - 118, 8)
        self._stat_line(draw, x0 + 16, y0 + 146, "等级", self._fmt(summoner.get("summonerLevel")), fonts, gold, ink, x1 - 18)
        ranked = data.get("ranked") or {}
        self._stat_line(draw, x0 + 16, y0 + 173, "排位", self._rank_text(ranked), fonts, cyan, ink, x1 - 18)
        self._stat_line(draw, x0 + 16, y0 + 194, "在线", "对局中" if data.get("active_game") else "未在对局中", fonts, green if data.get("active_game") else muted, ink, x1 - 18)
        self._text(draw, (x0 + 16, y1 - 35), f"Riot API · {data.get('api_calls', 0)} calls", fonts["tiny"], muted)
        self._text(draw, (x0 + 16, y1 - 19), f"更新 {data.get('updated_at') or '-'}", fonts["tiny"], muted)

    def _draw_recent(self, image, draw, data, box, fonts, ink, muted, gold, green, red, cyan):
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 14, y0 + 12), "最近比赛", fonts["section"], ink)
        headers = [("英雄", x0 + 52), ("K/D/A", x0 + 178), ("结果", x0 + 250), ("位置", x0 + 294)]
        for label, x in headers:
            self._text(draw, (x, y0 + 40), label, fonts["tiny"], muted)
        y = y0 + 62
        for match in (data.get("matches") or [])[:5]:
            if y > y1 - 34:
                break
            icon = self._icon_from_url(match.get("champion_icon"), 30, match.get("champion_name"))
            image.paste(icon, (x0 + 14, y - 2), icon)
            self._single(draw, (x0 + 52, y), match.get("champion_name"), fonts["small"], ink, 116, 9)
            self._text(draw, (x0 + 178, y), f"{match['kills']}/{match['deaths']}/{match['assists']}", fonts["small"], ink)
            self._text(draw, (x0 + 254, y), "胜" if match.get("win") else "负", fonts["small"], green if match.get("win") else red)
            self._text(draw, (x0 + 294, y), self._lane_label(match.get("lane")), fonts["tiny"], muted)
            self._text(draw, (x0 + 294, y + 14), self._relative(match.get("timestamp")), fonts["micro"], muted)
            y += 35
        if not data.get("matches"):
            self._text(draw, (x0 + 16, y0 + 70), "没有可显示的近期比赛", fonts["body"], muted)

    def _draw_rank_mastery(self, image, draw, data, box, fonts, ink, muted, gold, green, cyan):
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 12, y0 + 12), "排位 / 熟练度", fonts["section"], ink)
        ranked = data.get("ranked") or {}
        rank_text = self._rank_text(ranked)
        lp = int(ranked.get("leaguePoints") or 0) if ranked else 0
        wins = int(ranked.get("wins") or 0)
        losses = int(ranked.get("losses") or 0)
        rate = wins / max(1, wins + losses) * 100 if ranked else 0
        self._single(draw, (x0 + 14, y0 + 48), rank_text or "暂无排位", fonts["title"], gold if ranked else muted, x1 - x0 - 28, 12)
        self._text(draw, (x0 + 16, y0 + 80), f"{lp} LP · {wins}W/{losses}L", fonts["small"], ink if ranked else muted)
        self._text(draw, (x0 + 16, y0 + 102), f"胜率 {rate:.1f}%", fonts["small"], green if rate >= 50 else muted)
        self._text(draw, (x0 + 12, y0 + 128), "常用英雄", fonts["small"], cyan)
        mastery = data.get("mastery") or []
        max_points = max([int(item.get("points") or 0) for item in mastery] + [1])
        y = y0 + 148
        row_step = 28
        for item in mastery[:3]:
            icon = self._icon_from_url(item.get("champion_icon"), 24, item.get("champion_name"))
            image.paste(icon, (x0 + 12, y), icon)
            self._single(draw, (x0 + 44, y - 1), item.get("champion_name"), fonts["tiny"], ink, 72, 8)
            bar_x = x0 + 112
            bar_right = x1 - 20
            bar_w = max(10, int((bar_right - bar_x) * int(item.get("points") or 0) / max_points))
            draw.rectangle((bar_x, y + 9, bar_right, y + 15), fill=(55, 51, 42))
            draw.rectangle((bar_x, y + 9, bar_x + bar_w, y + 15), fill=gold)
            self._text(draw, (x0 + 44, y + 12), f"L{item.get('level', 0)} · {self._compact(item.get('points'))}", fonts["micro"], muted)
            y += row_step

    def _draw_overview(self, image, draw, data, box, fonts, ink, muted, gold, green, cyan, red):
        x0, y0, x1, y1 = box
        summary = data.get("summary") or {}
        content_x1, logo_box, art_box = self._overview_layout(box)
        self._text(draw, (x0 + 14, y0 + 12), f"数据总览（最近 {summary.get('games', 0)} 场）", fonts["section"], cyan)
        self._paste_asset_logo(image, RIOT_LOGO_FILE, logo_box, tint=(255, 82, 74), remove_light=True)
        metrics = [
            ("KDA", f"{summary.get('kda', 0):.2f}", f"{summary.get('kills', 0)}/{summary.get('deaths', 0)}/{summary.get('assists', 0)}", gold),
            ("胜率", f"{summary.get('winrate', 0):.1f}%", f"{summary.get('wins', 0)} 胜 / {summary.get('losses', 0)} 负", green if summary.get("winrate", 0) >= 50 else red),
            ("参团率", f"{summary.get('kp', 0):.1f}%", "按近期比赛估算", cyan),
            ("补刀", f"{summary.get('cs_per_min', 0):.1f}/min", "平均每分钟", gold),
            ("挑战点数", self._fmt(data.get("challenge_points")), "Challenges", cyan),
            ("在线状态", "对局中" if data.get("active_game") else "未在对局中", "Spectator-V5", green if data.get("active_game") else muted),
        ]
        col_w = (content_x1 - x0 - 28) // 3
        row_y = y0 + 48
        for idx, (label, value, sub, color) in enumerate(metrics):
            col = idx % 3
            row = idx // 3
            x = x0 + 14 + col * col_w
            y = row_y + row * 54
            self._text(draw, (x, y), label, fonts["tiny"], muted)
            self._single(draw, (x, y + 15), value, fonts["section"], color, col_w - 12, 11)
            self._single(draw, (x, y + 39), sub, fonts["tiny"], ink, col_w - 12, 8)
        self._draw_skin_art_feature(image, draw, data, art_box, fonts, ink, muted, gold, cyan)

    def _overview_layout(self, box):
        x0, y0, x1, y1 = box
        total_w = max(1, x1 - x0)
        art_w = min(270, max(245, int(total_w * 0.35)))
        art_box = (x1 - 12 - art_w, y0 + 16, x1 - 12, y1 - 12)
        logo_w = min(96, max(82, int(total_w * 0.12)))
        logo_box = (art_box[0] - 22 - logo_w, y1 - 110, art_box[0] - 22, y1 - 69)
        content_x1 = max(x0 + 320, logo_box[0] - 14)
        return content_x1, logo_box, art_box

    def _draw_skin_art_feature(self, image, draw, data, box, fonts, ink, muted, gold, cyan):
        x0, y0, x1, y1 = box
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        selected = self._choose_skin_art(data)
        raw = self._image_from_url((selected or {}).get("splash_url"), (selected or {}).get("skin_name"))
        if raw is None:
            raw = self._placeholder_splash(width, height, (selected or {}).get("champion_name") or "LoL")
        art = ImageOps.fit(ImageOps.exif_transpose(raw).convert("RGB"), (width, height), method=Image.Resampling.LANCZOS, centering=(0.42, 0.5))
        image.paste(art, (x0, y0))
        draw.rectangle((x0, y0, x1, y1), outline=(236, 232, 206), width=2)

    def _write_context(self, data, generated_at, refresh_minutes):
        account = data.get("account") or {}
        summary = data.get("summary") or {}
        ranked = data.get("ranked") or {}
        write_context(
            PLUGIN_ID,
            {
                "kind": "lol_info",
                "source": "Riot Games API",
                "summary": f"{account.get('gameName', '')}#{account.get('tagLine', '')}: {self._rank_text(ranked) or '暂无排位'}, {summary.get('wins', 0)}W/{summary.get('losses', 0)}L",
                "game_name": account.get("gameName"),
                "tag_line": account.get("tagLine"),
                "rank": self._rank_text(ranked),
                "recent_games": summary.get("games", 0),
                "active_game": bool(data.get("active_game")),
            },
            generated_at=datetime.fromtimestamp(float(generated_at), timezone.utc),
            ttl_seconds=int(refresh_minutes) * 60,
        )

    def _profile_icon(self, profile_icon_id, champions, size):
        base = (champions or {}).get("profile_icon_base") or ""
        url = f"{base}/{profile_icon_id}.png" if base and str(profile_icon_id or "").isdigit() else ""
        icon = self._icon_from_url(url, size, "LoL")
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        result.paste(icon, (0, 0), mask)
        ImageDraw.Draw(result).ellipse((1, 1, size - 2, size - 2), outline=(236, 190, 78), width=3)
        return result

    def _paste_asset_logo(self, image, filename, box, tint=None, remove_light=False):
        logo = self._asset_logo(filename, (box[2] - box[0], box[3] - box[1]), tint=tint, remove_light=remove_light)
        if not logo:
            return
        x = box[0] + ((box[2] - box[0]) - logo.width) // 2
        y = box[1] + ((box[3] - box[1]) - logo.height) // 2
        image.paste(logo, (x, y), logo)

    def _asset_logo(self, filename, max_size, tint=None, remove_light=False):
        path = Path(self.get_plugin_dir()) / "assets" / filename
        if not path.exists():
            return None
        try:
            raw = Image.open(path).convert("RGBA")
            if remove_light:
                raw = self._remove_light_background(raw)
            raw = self._trim_alpha(raw)
            if tint:
                raw = self._tint_opaque_pixels(raw, tint)
            raw.thumbnail(max_size, Image.Resampling.LANCZOS)
            return raw
        except Exception as exc:
            logger.warning("LoLInfo asset logo unavailable %s: %s", filename, exc)
            return None

    def _remove_light_background(self, image):
        image = image.convert("RGBA")
        pixels = image.load()
        width, height = image.size
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                if a and r >= 220 and g >= 220 and b >= 220:
                    pixels[x, y] = (255, 255, 255, 0)
        return image

    def _trim_alpha(self, image):
        alpha = image.getchannel("A")
        bbox = alpha.getbbox()
        return image.crop(bbox) if bbox else image

    def _tint_opaque_pixels(self, image, tint):
        image = image.convert("RGBA")
        pixels = image.load()
        width, height = image.size
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                if a:
                    shade = max(0.35, min(1.0, 1.0 - (r + g + b) / 765 * 0.5))
                    pixels[x, y] = (int(tint[0] * shade), int(tint[1] * shade), int(tint[2] * shade), a)
        return image

    def _icon_from_url(self, url, size, label=""):
        if url:
            cache_path = self._image_cache_path(url)
            try:
                if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 30 * 24 * 60 * 60:
                    raw = Image.open(cache_path)
                else:
                    response = get_http_session().get(url, timeout=20)
                    response.raise_for_status()
                    raw = Image.open(BytesIO(response.content))
                    raw = ImageOps.exif_transpose(raw)
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    raw.save(cache_path)
                return self._square_icon(raw, size)
            except Exception as exc:
                logger.warning("LoL icon unavailable for %s: %s", label, exc)
        return self._placeholder_icon(label, size)

    def _square_icon(self, raw, size):
        raw = ImageOps.exif_transpose(raw).convert("RGBA")
        icon = ImageOps.fit(raw, (size, size), method=Image.Resampling.LANCZOS)
        result = Image.new("RGBA", (size, size), (0, 0, 0, 255))
        result.alpha_composite(icon)
        ImageDraw.Draw(result).rectangle((0, 0, size - 1, size - 1), outline=(255, 205, 54), width=1)
        return result

    def _placeholder_icon(self, label, size):
        image = Image.new("RGBA", (size, size), (0, 0, 0, 255))
        draw = ImageDraw.Draw(image)
        text = str(label or "?")[:2].upper()
        font = self._font(max(8, size // 3), bold=True)
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((size - (bbox[2] - bbox[0])) / 2, (size - (bbox[3] - bbox[1])) / 2 - 1), text, font=font, fill=(255, 250, 222))
        draw.rectangle((0, 0, size - 1, size - 1), outline=(255, 205, 54), width=1)
        return image

    def _choose_skin_art(self, data):
        pool = [item for item in (data.get("skin_art_pool") or []) if item.get("id") and item.get("splash_url")]
        if not pool:
            return None
        if len(pool) == 1:
            return pool[0]

        state_path = self._skin_art_rotation_path(data)
        state = self._read_json(state_path, {})
        pool_ids = [str(item.get("id")) for item in pool]
        recent = [item_id for item_id in (state.get("recent") or []) if item_id in pool_ids]
        available = [item for item in pool if str(item.get("id")) not in recent]
        if not available:
            available = pool[:]
            recent = []
        last_id = str(state.get("last") or "")
        if len(available) > 1:
            available = [item for item in available if str(item.get("id")) != last_id] or available
        selected = random.choice(available)
        selected_id = str(selected.get("id"))
        recent.append(selected_id)
        keep = max(1, len(pool_ids) - 1)
        self._write_json(state_path, {"last": selected_id, "recent": recent[-keep:], "updated_ts": time.time()})
        return selected

    def _skin_art_rotation_path(self, data):
        account = data.get("account") or {}
        identity = account.get("puuid") or f"{account.get('gameName', '')}#{account.get('tagLine', '')}"
        digest = hashlib.sha256(str(identity or "lol-info").encode("utf-8")).hexdigest()[:16]
        return self._cache_dir() / f"skin_art_rotation_{digest}.json"

    def _image_from_url(self, url, label=""):
        if not url:
            return None
        cache_path = self._image_cache_path(url)
        try:
            if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 30 * 24 * 60 * 60:
                raw = Image.open(cache_path)
            else:
                session = get_http_session()
                if not session:
                    return None
                response = session.get(url, timeout=25)
                response.raise_for_status()
                raw = Image.open(BytesIO(response.content))
                raw = ImageOps.exif_transpose(raw)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                raw.save(cache_path)
            return raw
        except Exception as exc:
            logger.warning("LoL splash art unavailable for %s: %s", label, exc)
            return None

    def _placeholder_splash(self, width, height, label=""):
        image = Image.new("RGB", (width, height), (12, 14, 22))
        draw = ImageDraw.Draw(image)
        for offset in range(-height, width, 18):
            draw.line((offset, height, offset + height, 0), fill=(35, 36, 44), width=3)
        draw.rectangle((0, 0, width - 1, height - 1), outline=(255, 205, 54), width=2)
        return image

    def _rank_text(self, ranked):
        if not ranked:
            return ""
        tier = str(ranked.get("tier") or "").upper()
        rank = str(ranked.get("rank") or "")
        label = TIER_LABELS.get(tier, tier)
        return f"{label} {rank}".strip()

    def _queue_label(self, queue_id):
        labels = {420: "单双排", 440: "灵活排位", 450: "极地乱斗", 400: "匹配", 430: "匹配"}
        try:
            return labels.get(int(queue_id), str(queue_id or "-"))
        except Exception:
            return "-"

    def _lane_label(self, lane):
        labels = {"TOP": "上路", "JUNGLE": "打野", "MIDDLE": "中路", "BOTTOM": "下路", "UTILITY": "辅助"}
        return labels.get(str(lane or "").upper(), str(lane or "-"))

    def _relative(self, timestamp_ms):
        try:
            seconds = max(0, time.time() - int(timestamp_ms) / 1000)
        except Exception:
            return "-"
        if seconds < 3600:
            return f"{int(seconds // 60)}分钟前"
        if seconds < 86400:
            return f"{int(seconds // 3600)}小时前"
        return f"{int(seconds // 86400)}天前"

    def _stat_line(self, draw, x, y, label, value, fonts, accent, ink, max_x):
        self._text(draw, (x, y), label, fonts["tiny"], accent)
        self._single(draw, (x + 50, y - 4), value, fonts["body"], ink, max_x - x - 52, 9)

    def _text(self, draw, position, text, font, fill):
        draw.text(position, str(text), font=font, fill=fill)

    def _single(self, draw, position, text, font, fill, max_width, min_size=8):
        text = str(text or "")
        fitted = self._fit_font(draw, text, font, max_width, min_size)
        draw.text(position, text, font=fitted, fill=fill)

    def _fit_font(self, draw, text, font, max_width, min_size):
        if self._text_width(draw, text, font) <= max_width:
            return font
        current = int(getattr(font, "size", 0) or 0)
        bold = bool(getattr(font, "path", "") and "bd" in str(getattr(font, "path", "")).lower())
        for size in range(current - 1, min_size - 1, -1):
            candidate = self._font(size, bold=bold)
            if self._text_width(draw, text, candidate) <= max_width:
                return candidate
        return self._font(min_size, bold=bold)

    def _text_width(self, draw, text, font):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        return bbox[2] - bbox[0]

    def _rect(self, draw, box, fill, outline):
        draw.rectangle(box, fill=fill, outline=outline, width=2)

    def _draw_background(self, draw, width, height):
        for x in range(-40, width, 88):
            draw.line((x, 0, x + 80, height), fill=(18, 16, 20), width=8)

    def _font(self, size, bold=False):
        plugin_dir = Path(self.get_plugin_dir())
        sports_fonts = plugin_dir.parent / "sports_dashboard" / "fonts"
        candidates = []
        if bold:
            candidates.extend([sports_fonts / "msyhbd.ttc", Path("C:/Windows/Fonts/msyhbd.ttc")])
        candidates.extend([
            sports_fonts / "msyh.ttc",
            Path("C:/Windows/Fonts/msyh.ttc"),
            plugin_dir.parent / "literature_clock" / "fonts" / "LXGWWenKai-Regular.ttf",
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ])
        for path in candidates:
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size=size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def _identity(self, settings):
        return "|".join([
            str(settings.get("gameName") or DEFAULT_GAME_NAME).strip(),
            str(settings.get("tagLine") or DEFAULT_TAG_LINE).strip().lstrip("#"),
            self._route(settings.get("platformRoute"), DEFAULT_PLATFORM, PLATFORM_ROUTES),
            self._route(settings.get("regionalRoute"), DEFAULT_REGION, REGIONAL_ROUTES),
        ])

    def _cache_key(self, settings, dimensions, identity):
        parts = [
            STYLE_VERSION,
            identity,
            str(dimensions),
            str(self._bounded_int(settings.get("recentLimit"), 5, 3, 10)),
            str(self._bounded_int(settings.get("masteryLimit"), 5, 3, 8)),
            str(self._enabled(settings.get("includeChallenges"), default=True)),
            str(self._enabled(settings.get("includeActiveGame"), default=True)),
            str(self._enabled(settings.get("useMockData"), default=False)),
            str(settings.get("ownedSkinIds") or ""),
            str(self._enabled(settings.get("includeLatestSkins"), default=True)),
            str(self._bounded_int(settings.get("latestSkinCount"), 8, 0, 24)),
            str(self._bounded_int(settings.get("latestSkinCacheHours"), 6, 1, 168)),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]

    def _cache_dir(self):
        path = os.getenv("INKYPI_LOL_INFO_CACHE")
        if path:
            return Path(path)
        return Path(self.get_plugin_dir(".lol_info_cache"))

    def _cache_path(self, key):
        return self._cache_dir() / f"{key}.json"

    def _cache_image_path(self, key):
        return self._cache_dir() / f"{key}.png"

    def _image_cache_path(self, url):
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        return self._cache_dir() / f"image_{digest}.png"

    def _read_json(self, path, default):
        try:
            if Path(path).exists():
                return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed reading LoLInfo cache %s: %s", path, exc)
        return default

    def _write_json(self, path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp.replace(path)
        except PermissionError:
            path.write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")
            try:
                tmp.unlink()
            except Exception:
                pass

    def _json_safe(self, value):
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except Exception:
            return {}

    def _riot_key(self, settings, device_config):
        return (
            str(settings.get("apiKey") or "").strip()
            or self._device_key(device_config, "Riot_KEY")
            or self._device_key(device_config, "RIOT_API_KEY")
            or self._device_key(device_config, "RIOT_KEY")
        )

    def _device_key(self, device_config, key):
        try:
            return str(device_config.load_env_key(key) or "").strip()
        except Exception:
            return ""

    def _route(self, value, default, allowed):
        route = str(value or default).strip().lower()
        return route if route in allowed else default

    def _enabled(self, value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _bounded_int(self, value, default, minimum, maximum):
        try:
            number = int(value)
        except Exception:
            number = default
        return max(minimum, min(maximum, number))

    def _fmt(self, value):
        try:
            value = float(value)
        except Exception:
            return "-" if value in (None, "") else str(value)
        if math.isnan(value):
            return "-"
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        if value == int(value):
            return str(int(value))
        return f"{value:.1f}"

    def _compact(self, value):
        try:
            value = int(value or 0)
        except Exception:
            return "-"
        if value >= 1000000:
            return f"{value / 1000000:.1f}M"
        if value >= 1000:
            return f"{value / 1000:.0f}K"
        return str(value)

    def _sample_payload(self):
        champions = {
            "version": "mock",
            "by_key": {},
            "by_id": {},
            "profile_icon_base": "",
        }
        matches = [
            {"champion_name": "阿卡丽", "champion_icon": "", "kills": 6, "deaths": 10, "assists": 6, "win": False, "lane": "MIDDLE", "timestamp": int((time.time() - 22 * 60) * 1000), "duration": 1420, "cs": 163, "gold": 9453, "damage": 18200, "kp": 58},
            {"champion_name": "阿狸", "champion_icon": "", "kills": 8, "deaths": 3, "assists": 12, "win": True, "lane": "MIDDLE", "timestamp": int((time.time() - 3200) * 1000), "duration": 1880, "cs": 214, "gold": 12640, "damage": 24400, "kp": 72},
            {"champion_name": "盲僧", "champion_icon": "", "kills": 4, "deaths": 5, "assists": 9, "win": True, "lane": "JUNGLE", "timestamp": int((time.time() - 5400) * 1000), "duration": 1750, "cs": 52, "gold": 10120, "damage": 13200, "kp": 61},
            {"champion_name": "亚索", "champion_icon": "", "kills": 5, "deaths": 7, "assists": 4, "win": False, "lane": "MIDDLE", "timestamp": int((time.time() - 7100) * 1000), "duration": 1680, "cs": 190, "gold": 9800, "damage": 17400, "kp": 46},
            {"champion_name": "锐雯", "champion_icon": "", "kills": 9, "deaths": 4, "assists": 5, "win": True, "lane": "TOP", "timestamp": int((time.time() - 9600) * 1000), "duration": 2020, "cs": 226, "gold": 13770, "damage": 26800, "kp": 64},
        ]
        return {
            "source": "Mock Riot API",
            "api_calls": 0,
            "region": "ASIA",
            "platform": "KR",
            "account": {"gameName": DEFAULT_GAME_NAME, "tagLine": DEFAULT_TAG_LINE, "puuid": "mock"},
            "summoner": {"profileIconId": 0, "summonerLevel": 708},
            "ranked": {"queueType": "RANKED_SOLO_5x5", "tier": "MASTER", "rank": "I", "leaguePoints": 219, "wins": 134, "losses": 103},
            "mastery": [
                {"champion_key": "Ahri", "champion_name": "阿狸", "champion_icon": "", "level": 7, "points": 1248617},
                {"champion_key": "Riven", "champion_name": "锐雯", "champion_icon": "", "level": 7, "points": 875320},
                {"champion_key": "Yasuo", "champion_name": "亚索", "champion_icon": "", "level": 7, "points": 721445},
            ],
            "matches": matches,
            "featured_champions": [
                {"champion_key": "Ahri", "champion_name": "阿狸", "mastery_points": 1248617, "recent_games": 1, "score": 1300000},
                {"champion_key": "Riven", "champion_name": "锐雯", "mastery_points": 875320, "recent_games": 1, "score": 1000000},
                {"champion_key": "Yasuo", "champion_name": "亚索", "mastery_points": 721445, "recent_games": 1, "score": 900000},
            ],
            "skin_art_pool": [
                {"id": "Ahri:1", "champion_key": "Ahri", "champion_name": "阿狸", "skin_num": 1, "skin_name": "高丽风情 阿狸", "splash_url": f"{DDRAGON_BASE}/img/champion/splash/Ahri_1.jpg", "loading_url": f"{DDRAGON_BASE}/img/champion/loading/Ahri_1.jpg"},
                {"id": "Riven:2", "champion_key": "Riven", "champion_name": "锐雯", "skin_num": 2, "skin_name": "血色精锐 锐雯", "splash_url": f"{DDRAGON_BASE}/img/champion/splash/Riven_2.jpg", "loading_url": f"{DDRAGON_BASE}/img/champion/loading/Riven_2.jpg"},
                {"id": "Yasuo:1", "champion_key": "Yasuo", "champion_name": "亚索", "skin_num": 1, "skin_name": "西部牛仔 亚索", "splash_url": f"{DDRAGON_BASE}/img/champion/splash/Yasuo_1.jpg", "loading_url": f"{DDRAGON_BASE}/img/champion/loading/Yasuo_1.jpg"},
            ],
            "summary": self._recent_summary(matches),
            "challenge_points": 812,
            "active_game": None,
            "champions": champions,
        }
