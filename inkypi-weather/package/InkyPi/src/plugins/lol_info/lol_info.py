from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import time
import urllib.parse
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.refresh_on_display_presentation import RefreshOnDisplayPresentationMixin
from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from plugins.context_cache import write_context
from utils.app_utils import coerce_bool, get_base_ui_font
from utils.http_client import get_http_session
from utils.image_utils import text_width
from utils.safe_image import safe_open_image, safe_open_image_response
from utils.theme_utils import get_theme_context

logger = logging.getLogger(__name__)

PLUGIN_ID = "lol_info"
STYLE_VERSION = "lol-info-v17-pro-account-rotation"
DEFAULT_GAME_NAME = "Hide on bush"
DEFAULT_TAG_LINE = "KR1"
DEFAULT_PLATFORM = "kr"
DEFAULT_REGION = "asia"
DEFAULT_PRO_ACCOUNTS = [
    {"label": "Faker", "gameName": "Hide on bush", "tagLine": "KR1", "platformRoute": "kr", "regionalRoute": "asia"},
    {"label": "Bin", "gameName": "BLG \uc628", "tagLine": "KR1", "platformRoute": "kr", "regionalRoute": "asia"},
    {"label": "ShowMaker", "gameName": "DK ShowMaker", "tagLine": "KR1", "platformRoute": "kr", "regionalRoute": "asia"},
    {"label": "Chovy", "gameName": "\ud5c8\uac70\ub369", "tagLine": "0303", "platformRoute": "kr", "regionalRoute": "asia"},
]
DEFAULT_PRO_ACCOUNTS_TEXT = "\n".join(
    "|".join([item["label"], item["gameName"], item["tagLine"], item["platformRoute"], item["regionalRoute"]])
    for item in DEFAULT_PRO_ACCOUNTS
)
DDRAGON_VERSION_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_BASE = "https://ddragon.leagueoflegends.com/cdn"
CDRAGON_RAW_BASE = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default"
CDRAGON_SKINS_URL = f"{CDRAGON_RAW_BASE}/v1/skins.json"
CDRAGON_SHARED_IMAGES = "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-shared-components/global/default/images"
LOL_LOGO_FILE = "league-of-legends-logo.png"
RIOT_LOGO_FILE = "riot-games-logo.png"
RANK_EMBLEM_TIERS = {"iron", "bronze", "silver", "gold", "platinum", "emerald", "diamond", "master", "grandmaster", "challenger"}
_ALLOW_PROVIDER_MEDIA = ContextVar("lol_info_allow_provider_media", default=True)

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


class LoLInfo(RefreshOnDisplayPresentationMixin, BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["defaultGameName"] = DEFAULT_GAME_NAME
        params["defaultTagLine"] = DEFAULT_TAG_LINE
        params["defaultProAccounts"] = DEFAULT_PRO_ACCOUNTS_TEXT
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        effective_settings = self._settings_for_selected_pro_account(settings)
        dimensions = self.get_dimensions(device_config)

        identity = self._identity(effective_settings)
        now = time.time()
        refresh_minutes = self._bounded_int(effective_settings.get("refreshMinutes"), 120, 15, 1440)
        cache_key = self._cache_key(effective_settings, dimensions, identity)
        cache = self._read_json(self._cache_path(cache_key), {})
        theme_render_only = self._enabled(
            effective_settings.get("_theme_render_only"),
            default=False,
        )
        force_refresh = (
            self._enabled(effective_settings.get("forceRefresh"), default=False)
            and not theme_render_only
        )
        source_cache_valid = (
            cache.get("schema") == STYLE_VERSION
            and isinstance(cache.get("data"), dict)
            and bool(cache.get("data"))
        )

        cache_valid = (
            not force_refresh
            and source_cache_valid
            and now - float(cache.get("updated_ts", 0) or 0) < refresh_minutes * 60
            and cache.get("image_path")
            and Path(cache["image_path"]).exists()
        )

        try:
            provenance = SourceProvenance.LIVE
            if theme_render_only:
                if not source_cache_valid:
                    raise RuntimeError(
                        "LoLInfo theme-only render requires matching cached source data."
                    )
                data = cache.get("data") or {}
                data_updated_ts = float(cache.get("updated_ts", now) or now)
                provenance = SourceProvenance.FRESH_CACHE
            elif cache_valid:
                data = cache.get("data") or {}
                data_updated_ts = float(cache.get("updated_ts", now) or now)
                provenance = SourceProvenance.FRESH_CACHE
            else:
                use_mock_data = self._enabled(effective_settings.get("useMockData"), default=False)
                data = self._sample_payload() if use_mock_data else self._fetch_dashboard_data(effective_settings, device_config)
                data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                data_updated_ts = now
                if use_mock_data:
                    provenance = SourceProvenance.LOCAL_FALLBACK
            data = self._with_pro_account_context(data, effective_settings)
            theme_context = self._theme_context(effective_settings, device_config)
            media_token = _ALLOW_PROVIDER_MEDIA.set(not theme_render_only)
            try:
                image = self._render_dashboard(
                    data,
                    dimensions,
                    effective_settings,
                    theme_context,
                )
            finally:
                _ALLOW_PROVIDER_MEDIA.reset(media_token)
            if theme_render_only:
                return attach_source_provenance(image, provenance)
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
            if not theme_render_only:
                self._write_context(data, data_updated_ts, refresh_minutes)
            return attach_source_provenance(image, provenance)
        except Exception as exc:
            logger.error("LoLInfo generation failed: %s", exc)
            if theme_render_only:
                raise RuntimeError(f"LoLInfo theme-only render failed: {exc}") from exc
            if cache.get("image_path") and Path(cache["image_path"]).exists():
                logger.warning("Using stale LoLInfo cache.")
                self._write_context(cache.get("data") or {}, cache.get("updated_ts", now), refresh_minutes)
                return attach_source_provenance(
                    safe_open_image(cache["image_path"]).convert("RGB"),
                    SourceProvenance.STALE_CACHE,
                )
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
        local_matches = self._local_match_summaries(settings, account, champions)
        matches = self._merge_match_summaries(local_matches, matches, recent_limit)
        match_source_counts = self._match_source_counts(matches)

        mastery_summaries = [self._mastery_summary(item, champions) for item in mastery]
        featured_champions = self._featured_champions(mastery_summaries, matches)
        skin_art_pool = self._skin_art_pool(featured_champions, champions, settings)
        ranked = self._best_rank(leagues)
        return {
            "source": self._match_source_label(match_source_counts),
            "api_calls": calls,
            "region": region.upper(),
            "platform": platform.upper(),
            "account": account,
            "summoner": summoner,
            "ranked": ranked,
            "leagues": leagues,
            "mastery": mastery_summaries,
            "matches": matches,
            "match_source_counts": match_source_counts,
            "featured_champions": featured_champions,
            "skin_art_pool": skin_art_pool,
            "summary": self._recent_summary(matches),
            "match_history_status": self._match_history_status(matches, summoner),
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

    def _merge_match_summaries(self, preferred, fallback, recent_limit):
        combined = {}
        for row in list(preferred or []) + list(fallback or []):
            if not isinstance(row, dict):
                continue
            key = row.get("match_id") or "|".join(
                [
                    str(row.get("champion_key") or row.get("champion_name") or ""),
                    str(row.get("timestamp") or ""),
                    str(row.get("kills") or 0),
                    str(row.get("deaths") or 0),
                    str(row.get("assists") or 0),
                ]
            )
            if key and key not in combined:
                combined[key] = row
        return sorted(
            combined.values(),
            key=lambda row: int(row.get("timestamp") or 0),
            reverse=True,
        )[:recent_limit]

    def _local_match_summaries(self, settings, account, champions):
        payload = self._local_match_history_payload(settings)
        if not isinstance(payload, dict):
            return []
        if not self._local_match_history_matches_account(payload, account):
            return []
        matches = payload.get("matches")
        if isinstance(matches, list):
            return [
                row
                for row in (
                    self._normalize_local_match(item, champions)
                    for item in matches
                    if self._local_match_row_matches_account(item, account)
                )
                if row
            ]
        games = payload.get("games")
        if isinstance(games, dict):
            games = games.get("games")
        if isinstance(games, dict):
            games = games.get("games")
        if not isinstance(games, list):
            return []
        account_puuid = str((account or {}).get("puuid") or "").strip()
        puuid_candidates = {
            account_puuid,
        }
        if not account_puuid:
            puuid_candidates.update([
                str(payload.get("puuid") or "").strip(),
                str(payload.get("subject") or "").strip(),
            ])
        puuid_candidates = {value for value in puuid_candidates if value}
        return [
            summary
            for summary in (self._lcu_game_summary(game, puuid_candidates, champions) for game in games)
            if summary
        ]

    def _local_match_history_matches_account(self, payload, account):
        account = account or {}
        account_puuid = str(account.get("puuid") or "").strip()
        payload_puuids = self._local_payload_puuids(payload)
        if account_puuid and payload_puuids and account_puuid not in payload_puuids:
            return False

        account_riot_id = self._normalized_riot_id(account.get("gameName"), account.get("tagLine"))
        payload_riot_ids = self._local_payload_riot_ids(payload)
        if account_riot_id and payload_riot_ids and account_riot_id not in payload_riot_ids:
            return False
        return True

    def _local_match_row_matches_account(self, row, account):
        if not isinstance(row, dict):
            return False
        account = account or {}
        account_puuid = str(account.get("puuid") or "").strip()
        row_puuids = {
            str(row.get(key) or "").strip()
            for key in ("puuid", "player_puuid", "playerPuuid", "participantPuuid", "subject")
        }
        row_puuids = {value for value in row_puuids if value}
        if account_puuid and row_puuids and account_puuid not in row_puuids:
            return False

        account_riot_id = self._normalized_riot_id(account.get("gameName"), account.get("tagLine"))
        row_riot_ids = {
            self._normalized_riot_id(row.get("gameName"), row.get("tagLine")),
            self._normalized_riot_id(row.get("riotGameName"), row.get("riotTagLine")),
        }
        row_riot_ids = {value for value in row_riot_ids if value}
        if account_riot_id and row_riot_ids and account_riot_id not in row_riot_ids:
            return False
        return True

    def _local_payload_puuids(self, payload):
        candidates = set()
        for source in self._local_payload_identity_sources(payload):
            for key in ("puuid", "subject"):
                value = str(source.get(key) or "").strip()
                if value:
                    candidates.add(value)
        return candidates

    def _local_payload_riot_ids(self, payload):
        candidates = set()
        for source in self._local_payload_identity_sources(payload):
            candidates.add(self._normalized_riot_id(source.get("gameName"), source.get("tagLine")))
            candidates.add(self._normalized_riot_id(source.get("riotGameName"), source.get("riotTagLine")))
        return {value for value in candidates if value}

    @staticmethod
    def _local_payload_identity_sources(payload):
        sources = [payload]
        for key in ("summoner", "account", "player"):
            value = payload.get(key)
            if isinstance(value, dict):
                sources.append(value)
        return sources

    @staticmethod
    def _normalized_riot_id(game_name, tag_line):
        game = str(game_name or "").strip().casefold()
        tag = str(tag_line or "").strip().lstrip("#").casefold()
        return f"{game}#{tag}" if game and tag else ""

    def _local_match_history_payload(self, settings):
        for path in self._local_match_history_paths(settings):
            try:
                if path.exists():
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        return payload
            except Exception as exc:
                logger.warning("LoL local match history unavailable from %s: %s", path, exc)
        return {}

    def _local_match_history_paths(self, settings):
        candidates = []
        for value in (
            settings.get("localMatchHistoryPath"),
            settings.get("matchHistoryPath"),
            os.getenv("INKYPI_LOL_INFO_MATCH_HISTORY"),
        ):
            if value:
                candidates.append(Path(str(value)))
        candidates.extend([
            self._cache_dir() / "league_client_matches.json",
            Path(self.get_plugin_dir("league_client_matches.json")),
        ])
        result = []
        seen = set()
        for path in candidates:
            try:
                resolved = str(path.expanduser())
            except Exception:
                resolved = str(path)
            if resolved and resolved not in seen:
                seen.add(resolved)
                result.append(Path(resolved))
        return result

    def _local_match_history_signature(self, settings):
        parts = []
        for path in self._local_match_history_paths(settings):
            try:
                stat = path.stat()
            except Exception:
                continue
            parts.append(f"{path}:{int(stat.st_mtime)}:{stat.st_size}")
        return "|".join(parts)

    def _lcu_game_summary(self, game, puuid_candidates, champions):
        if not isinstance(game, dict):
            return None
        participants = game.get("participants") or []
        identities = game.get("participantIdentities") or []
        identity_by_id = {}
        for identity in identities:
            if not isinstance(identity, dict):
                continue
            participant_id = identity.get("participantId")
            if participant_id is not None:
                identity_by_id[int(participant_id)] = identity.get("player") or {}
        player = None
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            identity = identity_by_id.get(int(participant.get("participantId") or 0), {})
            possible_ids = {
                str(participant.get("puuid") or "").strip(),
                str(identity.get("puuid") or "").strip(),
                str(identity.get("currentAccountId") or "").strip(),
            }
            if puuid_candidates and possible_ids & puuid_candidates:
                player = dict(participant)
                player["_identity"] = identity
                break
        if player is None and len(participants) == 1:
            player = dict(participants[0])
            player["_identity"] = identity_by_id.get(int(player.get("participantId") or 0), {})
        if player is None:
            return None
        stats = player.get("stats") or {}
        timeline = player.get("timeline") or {}
        champion = self._champion_info(player.get("championId"), player.get("championName"), champions)
        team_id = player.get("teamId")
        team_kills = 0
        for participant in participants:
            if participant.get("teamId") == team_id:
                team_stats = participant.get("stats") or {}
                team_kills += int(team_stats.get("kills") or participant.get("kills") or 0)
        duration = self._safe_int(game.get("gameDuration")) or 0
        timestamp = self._safe_int(game.get("gameEndTimestamp"))
        if timestamp is None:
            creation = self._safe_int(game.get("gameCreation")) or self._safe_int(game.get("gameStartTimestamp")) or 0
            timestamp = creation + max(0, duration) * 1000 if creation else 0
        kills = int(stats.get("kills") or player.get("kills") or 0)
        deaths = int(stats.get("deaths") or player.get("deaths") or 0)
        assists = int(stats.get("assists") or player.get("assists") or 0)
        return {
            "match_id": str(game.get("gameId") or game.get("matchId") or ""),
            "champion_id": player.get("championId"),
            "champion_key": champion.get("id"),
            "champion_name": champion.get("name") or player.get("championName") or "未知英雄",
            "champion_icon": champion.get("icon_url") or "",
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "win": self._win_bool(stats.get("win") if "win" in stats else player.get("win")),
            "lane": stats.get("teamPosition") or timeline.get("lane") or timeline.get("role") or "-",
            "queue": self._queue_label(game.get("queueId")),
            "timestamp": int(timestamp or 0),
            "duration": max(1, int(duration or 0)),
            "cs": int(stats.get("totalMinionsKilled") or 0) + int(stats.get("neutralMinionsKilled") or 0),
            "gold": int(stats.get("goldEarned") or 0),
            "damage": int(stats.get("totalDamageDealtToChampions") or 0),
            "kp": self._kill_participation(kills, assists, team_kills),
            "source": "local_lcu",
        }

    def _normalize_local_match(self, row, champions):
        if not isinstance(row, dict):
            return None
        if row.get("participants") or row.get("participantIdentities"):
            return self._lcu_game_summary(row, set(), champions)
        champion = self._champion_info(row.get("champion_id") or row.get("championId"), row.get("champion_key") or row.get("championName"), champions)
        try:
            return {
                "match_id": str(row.get("match_id") or row.get("matchId") or row.get("gameId") or ""),
                "champion_id": row.get("champion_id") or row.get("championId"),
                "champion_key": row.get("champion_key") or champion.get("id"),
                "champion_name": row.get("champion_name") or row.get("championName") or champion.get("name") or "未知英雄",
                "champion_icon": row.get("champion_icon") or champion.get("icon_url") or "",
                "kills": int(row.get("kills") or 0),
                "deaths": int(row.get("deaths") or 0),
                "assists": int(row.get("assists") or 0),
                "win": self._win_bool(row.get("win")),
                "lane": row.get("lane") or "-",
                "queue": row.get("queue") or self._queue_label(row.get("queueId")),
                "timestamp": int(row.get("timestamp") or row.get("gameEndTimestamp") or row.get("gameCreation") or 0),
                "duration": max(1, int(row.get("duration") or row.get("gameDuration") or 0)),
                "cs": int(row.get("cs") or 0),
                "gold": int(row.get("gold") or row.get("goldEarned") or 0),
                "damage": int(row.get("damage") or row.get("totalDamageDealtToChampions") or 0),
                "kp": float(row.get("kp") or 0),
                "source": row.get("source") or "local_lcu",
            }
        except Exception:
            return None

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
        if settings or owned_refs or include_latest:
            cache_hours = self._bounded_int(settings.get("latestSkinCacheHours"), 6, 1, 168)
            catalog = self._communitydragon_skins(
                cache_hours=cache_hours,
                force_refresh=self._enabled(settings.get("forceRefresh"), default=False),
            )
        owned_pool = self._owned_skin_art_pool(owned_refs, catalog, champions)
        latest_pool = self._latest_skin_art_pool(catalog, champions, latest_count) if include_latest and latest_count > 0 else []
        catalog_pool = self._all_skin_art_pool(catalog, champions)
        return self._dedupe_skin_art_pool(owned_pool + latest_pool + featured_pool + catalog_pool)

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

    def _all_skin_art_pool(self, records, champions):
        pool = []
        for record in records or []:
            art = self._skin_art_from_cdragon(record, champions, "catalog")
            if art:
                pool.append(art)
        return sorted(pool, key=self._skin_art_release_key, reverse=True)

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

    @staticmethod
    def _win_bool(value):
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"win", "won", "victory", "true", "1", "yes"}:
            return True
        if text in {"fail", "loss", "lost", "defeat", "false", "0", "no"}:
            return False
        return bool(value)

    @staticmethod
    def _kill_participation(kills, assists, team_kills):
        if not team_kills:
            return 0
        return max(0, min(100, (int(kills or 0) + int(assists or 0)) / int(team_kills) * 100))

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

    def _champion_full_name_from_match(self, match, champions):
        match = match or {}
        champion = self._champion_info(match.get("champion_id"), match.get("champion_key") or match.get("champion_name"), champions or {})
        name = str(champion.get("name") or match.get("champion_name") or "未知英雄").strip()
        title = str(champion.get("title") or "").strip()
        if title and name and title not in name and name not in title:
            return f"{name} {title}"
        return name or str(match.get("champion_name") or "未知英雄")

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

    def _match_source_counts(self, matches):
        local = sum(1 for row in matches or [] if row.get("source") == "local_lcu")
        total = len(matches or [])
        return {
            "local_lcu": local,
            "match_v5": max(0, total - local),
            "total": total,
        }

    @staticmethod
    def _match_source_label(source_counts):
        counts = source_counts or {}
        if int(counts.get("local_lcu") or 0) > 0:
            return "Riot API + 本机记录"
        return "Riot Games API"

    def _match_history_status(self, matches, summoner):
        latest_match_ts = 0
        for row in matches or []:
            try:
                latest_match_ts = max(latest_match_ts, int(row.get("timestamp") or 0))
            except Exception:
                continue
        try:
            summoner_revision_ts = int((summoner or {}).get("revisionDate") or 0)
        except Exception:
            summoner_revision_ts = 0
        stale_gap_ms = 14 * 24 * 60 * 60 * 1000
        stale = bool(latest_match_ts and summoner_revision_ts and summoner_revision_ts - latest_match_ts > stale_gap_ms)
        return {
            "stale": stale,
            "latest_match_ts": latest_match_ts,
            "summoner_revision_ts": summoner_revision_ts,
        }

    def _challenge_points(self, challenge_data):
        total = (challenge_data or {}).get("totalPoints") or {}
        return int(total.get("current") or total.get("levelPoints") or 0)

    def _theme_context(self, settings, device_config):
        injected = (settings or {}).get("_inkypi_theme")
        if isinstance(injected, dict):
            return injected
        resolver = getattr(self, "resolve_theme", None)
        if callable(resolver):
            return resolver(settings, device_config)
        return get_theme_context(device_config)

    @staticmethod
    def _theme_role(theme_context, name, fallback):
        palette = (
            theme_context.get("palette")
            if isinstance(theme_context, dict)
            else None
        )
        value = palette.get(name) if isinstance(palette, dict) else None
        try:
            result = tuple(int(channel) for channel in value)
        except (TypeError, ValueError):
            return fallback
        return result if len(result) == 3 else fallback

    @staticmethod
    def _blend(foreground, background, amount):
        amount = max(0.0, min(1.0, float(amount)))
        return tuple(
            int(background[index] + (foreground[index] - background[index]) * amount)
            for index in range(3)
        )

    def _render_colors(self, theme_context):
        mode = str((theme_context or {}).get("mode") or "night").lower()
        night = mode == "night"
        if not night:
            return {
                "background": (5, 7, 12),
                "background_stripe": (5, 7, 12),
                "panel": (18, 22, 35),
                "surface": (18, 22, 35),
                "bar": (18, 22, 35),
                "rule": (236, 232, 206),
                "ink": (255, 250, 222),
                "muted": (202, 190, 150),
                "gold": (255, 205, 54),
                "cyan": (107, 204, 255),
                "green": (82, 202, 128),
                "red": (255, 82, 74),
            }
        background = self._theme_role(theme_context, "background", (5, 7, 12))
        panel = self._theme_role(theme_context, "panel", (18, 22, 35))
        ink = self._theme_role(theme_context, "ink", (255, 250, 222))
        muted = self._theme_role(theme_context, "muted", (202, 190, 150))
        rule = self._theme_role(theme_context, "rule", (236, 232, 206))
        accent = self._theme_role(theme_context, "accent", (107, 204, 255))
        return {
            "background": background,
            "background_stripe": self._blend(accent, background, 0.08),
            "panel": panel,
            "surface": self._blend(accent, panel, 0.06),
            "bar": self._blend(muted, panel, 0.32),
            "rule": rule,
            "ink": ink,
            "muted": muted,
            "gold": (255, 205, 54) if night else (142, 98, 18),
            "cyan": accent,
            "green": (82, 202, 128) if night else (31, 111, 70),
            "red": (255, 82, 74) if night else (164, 43, 43),
        }

    def _render_dashboard(self, data, dimensions, settings=None, theme_context=None):
        width, height = dimensions
        colors = self._render_colors(theme_context)
        bg = colors["background"]
        panel = colors["panel"]
        border = colors["rule"]
        ink = colors["ink"]
        muted = colors["muted"]
        gold = colors["gold"]
        cyan = colors["cyan"]
        green = colors["green"]
        red = colors["red"]

        image = Image.new("RGB", dimensions, bg)
        draw = ImageDraw.Draw(image)
        self._draw_background(draw, width, height, colors["background_stripe"])
        fonts = {
            "title": self._font(25, bold=True),
            "section": self._font(20, bold=True),
            "body": self._font(15),
            "small": self._font(13),
            "skin_label": self._font(14),
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

        self._draw_profile(
            image,
            draw,
            data,
            left_box,
            fonts,
            ink,
            muted,
            gold,
            cyan,
            green,
            surface=colors["surface"],
            rule=border,
        )
        self._draw_recent(image, draw, data, center_box, fonts, ink, muted, gold, green, red, cyan)
        self._draw_rank_mastery(
            image,
            draw,
            data,
            right_box,
            fonts,
            ink,
            muted,
            gold,
            green,
            cyan,
            bar_fill=colors["bar"],
        )
        self._draw_overview(
            image,
            draw,
            data,
            bottom_box,
            fonts,
            ink,
            muted,
            gold,
            green,
            cyan,
            red,
            rule=border,
        )
        return image

    def _draw_profile(
        self,
        image,
        draw,
        data,
        box,
        fonts,
        ink,
        muted,
        gold,
        cyan,
        green,
        surface=None,
        rule=None,
    ):
        x0, y0, x1, y1 = box
        surface = surface or (12, 17, 27)
        rule = rule or (78, 68, 40)
        account = data.get("account") or {}
        summoner = data.get("summoner") or {}
        ranked = data.get("ranked") or {}
        game_name = account.get("gameName") or DEFAULT_GAME_NAME
        tagline = account.get("tagLine") or DEFAULT_TAG_LINE
        route_text = f"{data.get('platform') or '-'} / {data.get('region') or '-'}"
        active = bool(data.get("active_game"))
        status_text = "对局中" if active else "空闲"
        status_color = green if active else muted

        logo_w = 124
        logo_left = x0 + (x1 - x0 - logo_w) // 2
        self._paste_asset_logo(image, LOL_LOGO_FILE, (logo_left, y0 + 9, logo_left + logo_w, y0 + 43))
        draw.line((x0 + 14, y0 + 53, x1 - 14, y0 + 53), fill=rule, width=1)

        icon_size = 68
        icon_x = x0 + 15
        icon_y = y0 + 65
        icon = self._profile_icon(summoner.get("profileIconId"), data.get("champions") or {}, icon_size)
        image.paste(icon, (icon_x, icon_y), icon)

        info_x = x0 + 94
        info_w = x1 - info_x - 14
        self._single(draw, (info_x, y0 + 63), game_name, fonts["body"], ink, info_w, 9)
        self._single(draw, (info_x, y0 + 85), f"#{tagline}", fonts["tiny"], muted, info_w, 8)
        self._single(draw, (info_x, y0 + 104), route_text, fonts["tiny"], cyan, info_w, 8)
        draw.ellipse((info_x, y0 + 126, info_x + 7, y0 + 133), fill=status_color)
        self._text(draw, (info_x + 13, y0 + 122), status_text, fonts["small"], status_color)

        stat_y0 = y0 + 147
        stat_y1 = y0 + 195
        stat_mid = x0 + 104
        draw.rectangle(
            (x0 + 14, stat_y0, x1 - 14, stat_y1),
            fill=surface,
            outline=rule,
            width=1,
        )
        draw.line((stat_mid, stat_y0 + 5, stat_mid, stat_y1 - 5), fill=rule, width=1)
        self._text(draw, (x0 + 23, stat_y0 + 7), "等级", fonts["tiny"], gold)
        self._single(draw, (x0 + 23, stat_y0 + 22), self._fmt(summoner.get("summonerLevel")), fonts["section"], ink, stat_mid - x0 - 33, 10)
        self._text(draw, (stat_mid + 11, stat_y0 + 7), "排位", fonts["tiny"], cyan)
        self._single(draw, (stat_mid + 11, stat_y0 + 22), self._rank_text(ranked) or "暂无", fonts["section"], ink, x1 - stat_mid - 25, 10)

        source_label = str(data.get("source") or "Riot API")
        source_label = source_label.replace("Riot API", "API").replace("本机记录", "本机").replace(" + ", "+")
        updated = str(data.get("updated_at") or "-")
        if len(updated) >= 16 and updated[:2] == "20":
            updated = updated[5:]
        self._single(draw, (x0 + 16, y1 - 35), f"来源 {source_label} · {data.get('api_calls', 0)}次", fonts["tiny"], muted, x1 - x0 - 32, 8)
        self._single(draw, (x0 + 16, y1 - 19), f"更新 {updated}", fonts["tiny"], muted, x1 - x0 - 32, 8)

    def _draw_recent(self, image, draw, data, box, fonts, ink, muted, gold, green, red, cyan):
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 14, y0 + 12), "最近比赛", fonts["section"], ink)
        champions = data.get("champions") or {}
        name_x = x0 + 52
        kda_x = x0 + 213
        result_x = x0 + 274
        position_x = x0 + 306
        headers = [("英雄", name_x), ("K/D/A", kda_x), ("结果", result_x - 2), ("位置", position_x)]
        for label, x in headers:
            self._text(draw, (x, y0 + 40), label, fonts["tiny"], muted)
        y = y0 + 62
        for match in (data.get("matches") or [])[:5]:
            if y > y1 - 34:
                break
            icon = self._icon_from_url(match.get("champion_icon"), 30, match.get("champion_name"))
            image.paste(icon, (x0 + 14, y - 2), icon)
            champion_name = self._champion_full_name_from_match(match, champions)
            self._single(draw, (name_x, y), champion_name, fonts["small"], ink, kda_x - name_x - 8, 8)
            self._text(draw, (kda_x, y), f"{match['kills']}/{match['deaths']}/{match['assists']}", fonts["small"], ink)
            self._text(draw, (result_x, y), "胜" if match.get("win") else "负", fonts["small"], green if match.get("win") else red)
            self._text(draw, (position_x, y), self._lane_label(match.get("lane")), fonts["tiny"], muted)
            self._text(draw, (position_x, y + 14), self._relative(match.get("timestamp")), fonts["micro"], muted)
            y += 35
        if not data.get("matches"):
            self._text(draw, (x0 + 16, y0 + 70), "没有可显示的近期比赛", fonts["body"], muted)

    def _draw_rank_mastery(
        self,
        image,
        draw,
        data,
        box,
        fonts,
        ink,
        muted,
        gold,
        green,
        cyan,
        bar_fill=None,
    ):
        x0, y0, x1, y1 = box
        bar_fill = bar_fill or (55, 51, 42)
        self._text(draw, (x0 + 12, y0 + 12), "排位 / 熟练度", fonts["section"], ink)
        ranked = data.get("ranked") or {}
        rank_text = self._rank_text(ranked)
        lp = int(ranked.get("leaguePoints") or 0) if ranked else 0
        wins = int(ranked.get("wins") or 0)
        losses = int(ranked.get("losses") or 0)
        rate = wins / max(1, wins + losses) * 100 if ranked else 0
        emblem = self._rank_emblem_image(ranked, 74) if ranked else None
        if emblem:
            emblem_x = x0 + 12
            emblem_y = y0 + 41
            image.paste(emblem, (emblem_x, emblem_y), emblem)
            self._single(draw, (emblem_x, emblem_y + emblem.height + 3), rank_text, fonts["tiny"], gold, emblem.width, 7)
            info_x = emblem_x + emblem.width + 6
            info_w = max(42, x1 - info_x - 12)
            self._single(draw, (info_x, y0 + 51), f"{lp} LP", fonts["body"], gold, info_w, 9)
            self._single(draw, (info_x, y0 + 75), f"{wins}W/{losses}L", fonts["small"], ink, info_w, 8)
            self._single(draw, (info_x, y0 + 97), f"{rate:.1f}%", fonts["tiny"], green if rate >= 50 else muted, info_w, 8)
        else:
            self._single(draw, (x0 + 14, y0 + 48), rank_text or "暂无排位", fonts["title"], gold if ranked else muted, x1 - x0 - 28, 12)
            self._text(draw, (x0 + 16, y0 + 80), f"{lp} LP · {wins}W/{losses}L", fonts["small"], ink if ranked else muted)
            self._text(draw, (x0 + 16, y0 + 102), f"胜率 {rate:.1f}%", fonts["small"], green if rate >= 50 else muted)
        self._text(draw, (x0 + 12, y0 + 140), "常用英雄", fonts["small"], cyan)
        mastery = data.get("mastery") or []
        max_points = max([int(item.get("points") or 0) for item in mastery] + [1])
        y = y0 + 158
        row_step = 28
        for item in mastery[:3]:
            icon = self._icon_from_url(item.get("champion_icon"), 24, item.get("champion_name"))
            image.paste(icon, (x0 + 12, y), icon)
            self._single(draw, (x0 + 44, y - 1), item.get("champion_name"), fonts["tiny"], ink, 72, 8)
            bar_x = x0 + 112
            bar_right = x1 - 20
            bar_w = max(10, int((bar_right - bar_x) * int(item.get("points") or 0) / max_points))
            draw.rectangle((bar_x, y + 9, bar_right, y + 15), fill=bar_fill)
            draw.rectangle((bar_x, y + 9, bar_x + bar_w, y + 15), fill=gold)
            self._text(draw, (x0 + 44, y + 12), f"L{item.get('level', 0)} · {self._compact(item.get('points'))}", fonts["micro"], muted)
            y += row_step
    def _draw_overview(
        self,
        image,
        draw,
        data,
        box,
        fonts,
        ink,
        muted,
        gold,
        green,
        cyan,
        red,
        rule=None,
    ):
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
        selected_skin_art = self._draw_skin_art_feature(
            image,
            draw,
            data,
            art_box,
            fonts,
            ink,
            muted,
            gold,
            cyan,
            rule=rule,
        )
        self._draw_skin_art_label(draw, selected_skin_art, logo_box, box, fonts, ink)

    def _overview_layout(self, box):
        x0, y0, x1, y1 = box
        total_w = max(1, x1 - x0)
        art_w = min(270, max(245, int(total_w * 0.35)))
        art_box = (x1 - 12 - art_w, y0 + 16, x1 - 12, y1 - 12)
        logo_w = min(96, max(82, int(total_w * 0.12)))
        logo_box = (art_box[0] - 22 - logo_w, y1 - 110, art_box[0] - 22, y1 - 69)
        content_x1 = max(x0 + 320, logo_box[0] - 14)
        return content_x1, logo_box, art_box

    def _draw_skin_art_feature(
        self,
        image,
        draw,
        data,
        box,
        fonts,
        ink,
        muted,
        gold,
        cyan,
        rule=None,
    ):
        x0, y0, x1, y1 = box
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        selected = self._choose_skin_art(data)
        raw = self._image_from_url((selected or {}).get("splash_url"), (selected or {}).get("skin_name"))
        if raw is None:
            raw = self._placeholder_splash(width, height, (selected or {}).get("champion_name") or "LoL")
        art = ImageOps.fit(ImageOps.exif_transpose(raw).convert("RGB"), (width, height), method=Image.Resampling.LANCZOS, centering=(0.42, 0.5))
        image.paste(art, (x0, y0))
        draw.rectangle(
            (x0, y0, x1, y1),
            outline=rule or (236, 232, 206),
            width=2,
        )
        return selected

    def _draw_skin_art_label(self, draw, selected, logo_box, overview_box, fonts, ink):
        skin_name = str((selected or {}).get("skin_name") or "").strip()
        if not skin_name:
            return
        x0, _y0, x1, y1 = logo_box
        _ox0, _oy0, _ox1, overview_y1 = overview_box
        label_x0 = x0 + 7
        label_x1 = x1 - 4
        label_y1 = min(overview_y1 - 15, y1 + 42)
        label_y0 = max(y1 + 17, label_y1 - 20)
        if label_x1 - label_x0 < 24 or label_y1 - label_y0 < 14:
            return
        self._single(draw, (label_x0, label_y0 + 5), skin_name, fonts["skin_label"], ink, label_x1 - label_x0, 8)

    def _write_context(self, data, generated_at, refresh_minutes):
        account = data.get("account") or {}
        summary = data.get("summary") or {}
        ranked = data.get("ranked") or {}
        matches = data.get("matches") or []
        source_counts = data.get("match_source_counts") or self._match_source_counts(matches)
        source = data.get("source") or self._match_source_label(source_counts)
        latest = matches[0] if matches else {}
        latest_text = ""
        if latest:
            latest_text = f", 最近一局 {latest.get('champion_name', '-')}: {latest.get('kills', 0)}/{latest.get('deaths', 0)}/{latest.get('assists', 0)}"
        write_context(
            PLUGIN_ID,
            {
                "kind": "lol_info",
                "source": source,
                "summary": f"{account.get('gameName', '')}#{account.get('tagLine', '')}: {self._rank_text(ranked) or '暂无排位'}, 最近{summary.get('games', 0)}场 {summary.get('wins', 0)}W/{summary.get('losses', 0)}L, KDA {summary.get('kda', 0):.2f}, 胜率 {summary.get('winrate', 0):.1f}%{latest_text}",
                "game_name": account.get("gameName"),
                "tag_line": account.get("tagLine"),
                "rank": self._rank_text(ranked),
                "recent_games": summary.get("games", 0),
                "recent_wins": summary.get("wins", 0),
                "recent_losses": summary.get("losses", 0),
                "recent_kda": round(float(summary.get("kda") or 0), 2),
                "recent_winrate": round(float(summary.get("winrate") or 0), 1),
                "local_match_count": source_counts.get("local_lcu", 0),
                "match_v5_count": source_counts.get("match_v5", 0),
                "latest_match": latest,
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

    def _rank_emblem_url(self, ranked):
        tier = str((ranked or {}).get("tier") or "").strip().lower()
        if tier not in RANK_EMBLEM_TIERS:
            return ""
        return f"{CDRAGON_SHARED_IMAGES}/{tier}.png"

    def _rank_emblem_image(self, ranked, size):
        url = self._rank_emblem_url(ranked)
        if not url:
            return None
        cache_path = self._image_cache_path(url)
        try:
            cache_fresh = (
                cache_path.exists()
                and time.time() - cache_path.stat().st_mtime < 30 * 24 * 60 * 60
            )
            if cache_path.exists() and (
                cache_fresh or not _ALLOW_PROVIDER_MEDIA.get()
            ):
                raw = safe_open_image(cache_path)
            elif not _ALLOW_PROVIDER_MEDIA.get():
                return None
            else:
                response = get_http_session().get(url, timeout=20, stream=True)
                raw = safe_open_image_response(response)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                raw.save(cache_path)
            return self._contain_transparent(raw, size)
        except Exception as exc:
            logger.warning("LoL rank emblem unavailable for %s: %s", ranked.get("tier") if isinstance(ranked, dict) else "", exc)
            return None

    def _contain_transparent(self, raw, size):
        icon = ImageOps.exif_transpose(raw).convert("RGBA")
        icon.thumbnail((size, size), Image.Resampling.LANCZOS)
        result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        result.alpha_composite(icon, ((size - icon.width) // 2, (size - icon.height) // 2))
        return result
    def _icon_from_url(self, url, size, label=""):
        if url:
            cache_path = self._image_cache_path(url)
            try:
                cache_fresh = (
                    cache_path.exists()
                    and time.time() - cache_path.stat().st_mtime < 30 * 24 * 60 * 60
                )
                if cache_path.exists() and (
                    cache_fresh or not _ALLOW_PROVIDER_MEDIA.get()
                ):
                    raw = safe_open_image(cache_path)
                elif not _ALLOW_PROVIDER_MEDIA.get():
                    return self._placeholder_icon(label, size)
                else:
                    response = get_http_session().get(url, timeout=20, stream=True)
                    raw = safe_open_image_response(response)
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
        last_id = str(state.get("last") or "")
        if not _ALLOW_PROVIDER_MEDIA.get():
            return next(
                (item for item in pool if str(item.get("id")) == last_id),
                pool[0],
            )
        previous_pool_ids = state.get("pool_ids")
        pool_changed = isinstance(previous_pool_ids, list) and previous_pool_ids != pool_ids
        try:
            last_index = -1 if pool_changed else pool_ids.index(last_id)
        except ValueError:
            last_index = -1

        next_index = (last_index + 1) % len(pool)
        selected = pool[next_index]
        selected_id = str(selected.get("id"))
        recent = [item_id for item_id in (state.get("recent") or []) if item_id in pool_ids]
        recent.append(selected_id)
        keep = max(1, len(pool_ids) - 1)
        self._write_json(state_path, {
            "last": selected_id,
            "index": next_index,
            "recent": recent[-keep:],
            "pool_ids": pool_ids,
            "pool_size": len(pool_ids),
            "updated_ts": time.time(),
        })
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
            cache_fresh = (
                cache_path.exists()
                and time.time() - cache_path.stat().st_mtime < 30 * 24 * 60 * 60
            )
            if cache_path.exists() and (
                cache_fresh or not _ALLOW_PROVIDER_MEDIA.get()
            ):
                raw = safe_open_image(cache_path)
            elif not _ALLOW_PROVIDER_MEDIA.get():
                return None
            else:
                session = get_http_session()
                if not session:
                    return None
                response = session.get(url, timeout=25, stream=True)
                raw = safe_open_image_response(response)
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
        labels = {420: "单双排", 440: "灵活排位", 450: "极地乱斗", 400: "匹配", 430: "匹配", 2400: "大混战", 3140: "自定义"}
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
        text = str(text)
        draw.text(position, text, font=self._font_for_text(font, text), fill=fill)

    def _single(self, draw, position, text, font, fill, max_width, min_size=8):
        text = str(text or "")
        font = self._font_for_text(font, text)
        fitted = self._fit_font(draw, text, font, max_width, min_size)
        draw.text(position, text, font=fitted, fill=fill)

    def _fit_font(self, draw, text, font, max_width, min_size):
        font = self._font_for_text(font, text)
        if self._text_width(draw, text, font) <= max_width:
            return font
        current = int(getattr(font, "size", 0) or 0)
        bold = self._is_bold_font(font)
        prefer_hangul = self._contains_hangul(text)
        for size in range(current - 1, min_size - 1, -1):
            candidate = self._font(size, bold=bold, prefer_hangul=prefer_hangul)
            if self._text_width(draw, text, candidate) <= max_width:
                return candidate
        return self._font(min_size, bold=bold, prefer_hangul=prefer_hangul)

    def _text_width(self, draw, text, font):
        text = str(text)
        return text_width(draw, text, self._font_for_text(font, text))

    def _font_for_text(self, font, text):
        if not self._contains_hangul(text):
            return font
        size = int(getattr(font, "size", 0) or 12)
        return self._font(size, bold=self._is_bold_font(font), prefer_hangul=True)

    def _contains_hangul(self, text):
        return any(
            0xAC00 <= ord(ch) <= 0xD7AF
            or 0x1100 <= ord(ch) <= 0x11FF
            or 0x3130 <= ord(ch) <= 0x318F
            for ch in str(text or "")
        )

    def _is_bold_font(self, font):
        path = str(getattr(font, "path", "") or "").lower()
        return "bd" in path or "bold" in path

    def _rect(self, draw, box, fill, outline):
        draw.rectangle(box, fill=fill, outline=outline, width=2)

    def _draw_background(self, draw, width, height, stripe=(18, 16, 20)):
        for x in range(-40, width, 88):
            draw.line((x, 0, x + 80, height), fill=stripe, width=8)

    def _font(self, size, bold=False, prefer_hangul=False):
        font = get_base_ui_font(int(size), bold=bool(bold))
        if not prefer_hangul or self._font_supports_text(font, "\ud55c"):
            return font

        plugin_dir = Path(self.get_plugin_dir())
        static_fonts = plugin_dir.parent.parent / "static" / "fonts"
        candidates = [
            static_fonts / "LXGWWenKai-Regular.ttf",
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc") if bold else Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc") if bold else Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        ]
        for path in candidates:
            if path.exists():
                try:
                    candidate = ImageFont.truetype(str(path), size=int(size))
                    if self._font_supports_text(candidate, "\ud55c"):
                        return candidate
                except Exception:
                    continue
        return font

    @staticmethod
    def _font_supports_text(font, text):
        if font is None or not hasattr(font, "getmask"):
            return False
        try:
            replacement = font.getmask("\ufffd")
            replacement_signature = (replacement.size, bytes(replacement))
            for char in str(text or ""):
                if char.isspace():
                    continue
                glyph = font.getmask(char)
                if glyph.getbbox() is None:
                    return False
                if (glyph.size, bytes(glyph)) == replacement_signature:
                    return False
        except Exception:
            return False
        return True

    def _settings_for_selected_pro_account(self, settings):
        effective = dict(settings or {})
        selected = self._select_pro_account(settings)
        if not selected:
            return effective
        effective.update({
            "gameName": selected["gameName"],
            "tagLine": selected["tagLine"],
            "platformRoute": selected["platformRoute"],
            "regionalRoute": selected["regionalRoute"],
            "_proAccountLabel": selected["label"],
            "_proAccountId": selected["id"],
            "_proAccountPoolSize": selected.get("pool_size", 0),
            "_proAccountPoolSignature": selected.get("pool_signature", ""),
        })
        return effective

    def _select_pro_account(self, settings):
        accounts = self._pro_accounts(settings)
        if not accounts:
            return None
        pool_ids = [item["id"] for item in accounts]
        pool_signature = hashlib.sha256("\n".join(pool_ids).encode("utf-8")).hexdigest()[:16]
        by_id = {item["id"]: item for item in accounts}
        if len(accounts) == 1:
            selected = dict(accounts[0])
            selected["pool_signature"] = pool_signature
            selected["pool_size"] = 1
            return selected

        state_path = self._pro_account_rotation_path(pool_signature)
        state = self._read_json(state_path, {})
        previous_pool_ids = state.get("pool_ids")
        queue = [str(item_id) for item_id in (state.get("queue") or []) if str(item_id) in by_id]
        if previous_pool_ids != pool_ids or not queue:
            queue = pool_ids[:]
            random.shuffle(queue)

        selected_id = queue.pop(0)
        selected = dict(by_id[selected_id])
        selected["pool_signature"] = pool_signature
        selected["pool_size"] = len(pool_ids)
        recent = [str(item_id) for item_id in (state.get("recent") or []) if str(item_id) in by_id]
        recent.append(selected_id)
        self._write_json(state_path, {
            "pool_ids": pool_ids,
            "queue": queue,
            "last": selected_id,
            "recent": recent[-len(pool_ids):],
            "pool_size": len(pool_ids),
            "updated_ts": time.time(),
        })
        return selected

    def _pro_accounts(self, settings):
        raw = (settings or {}).get("proAccounts")
        if raw is None:
            return []
        entries = raw if isinstance(raw, list) else str(raw).splitlines()
        accounts = []
        seen = set()
        for entry in entries:
            parsed = self._pro_account_from_entry(entry)
            if not parsed or parsed["id"] in seen:
                continue
            seen.add(parsed["id"])
            accounts.append(parsed)
        return accounts

    def _pro_account_from_entry(self, entry):
        if isinstance(entry, dict):
            label = str(entry.get("label") or entry.get("name") or "").strip()
            game_name = str(entry.get("gameName") or entry.get("game_name") or "").strip()
            tag_line = str(entry.get("tagLine") or entry.get("tag_line") or "").strip().lstrip("#")
            platform = entry.get("platformRoute") or entry.get("platform") or DEFAULT_PLATFORM
            region = entry.get("regionalRoute") or entry.get("region") or DEFAULT_REGION
        else:
            line = str(entry or "").strip()
            if not line or line.startswith("#"):
                return None
            if "|" in line:
                parts = [part.strip() for part in line.split("|")]
                if len(parts) < 3:
                    return None
                label, game_name, tag_line = parts[:3]
                platform = parts[3] if len(parts) > 3 and parts[3] else DEFAULT_PLATFORM
                region = parts[4] if len(parts) > 4 and parts[4] else DEFAULT_REGION
            else:
                label = ""
                riot_id = line
                if "=" in line:
                    label, riot_id = [part.strip() for part in line.split("=", 1)]
                if "#" not in riot_id:
                    return None
                game_name, tag_line = [part.strip() for part in riot_id.rsplit("#", 1)]
                platform = DEFAULT_PLATFORM
                region = DEFAULT_REGION
            tag_line = str(tag_line or "").strip().lstrip("#")
        game_name = str(game_name or "").strip()
        tag_line = str(tag_line or "").strip().lstrip("#")
        if not game_name or not tag_line:
            return None
        platform = self._route(platform, DEFAULT_PLATFORM, PLATFORM_ROUTES)
        region = self._route(region, DEFAULT_REGION, REGIONAL_ROUTES)
        label = str(label or game_name).strip()
        account_id = "|".join([label.casefold(), game_name.casefold(), tag_line.casefold(), platform, region])
        return {
            "id": account_id,
            "label": label,
            "gameName": game_name,
            "tagLine": tag_line,
            "platformRoute": platform,
            "regionalRoute": region,
        }

    def _pro_account_rotation_path(self, pool_signature):
        signature = str(pool_signature or "default").strip() or "default"
        return self._cache_dir() / f"pro_account_rotation_{signature}.json"

    def _with_pro_account_context(self, data, settings):
        if not isinstance(data, dict):
            return data
        label = str(settings.get("_proAccountLabel") or "").strip()
        if label:
            data["pro_account"] = {
                "label": label,
                "id": str(settings.get("_proAccountId") or ""),
                "pool_size": int(settings.get("_proAccountPoolSize") or 0),
                "pool_signature": str(settings.get("_proAccountPoolSignature") or ""),
            }
        return data

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
            self._local_match_history_signature(settings),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]

    def _cache_dir(self):
        return self.cache_dir(env_var="INKYPI_LOL_INFO_CACHE", leaf=".lol_info_cache", create=False)

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
        return coerce_bool(value, default=default, truthy=tuple({"1", "true", "yes", "on"}))

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
