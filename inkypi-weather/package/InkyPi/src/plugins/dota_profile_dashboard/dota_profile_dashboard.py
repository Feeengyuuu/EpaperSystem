from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
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

PLUGIN_ID = "dota_profile_dashboard"
OPENDOTA_BASE_URL = "https://api.opendota.com/api"
DOTA_HERO_LIST_URL = "https://www.dota2.com/datafeed/herolist?language=schinese"
STEAM_ASSET_BASE_URL = "https://cdn.cloudflare.steamstatic.com"
OPENDOTA_ASSET_BASE_URL = "https://api.opendota.com"
STEAMID64_BASE = 76561197960265728
DEFAULT_STEAM_ID64 = "76561198176386838"
DEFAULT_ACCOUNT_ID = str(int(DEFAULT_STEAM_ID64) - STEAMID64_BASE)
STYLE_VERSION = "dota-profile-dashboard-v3-schinese-heroes"

ZH_HERO_NAME_FALLBACK = {
    1: "敌法师",
    2: "斧王",
    3: "祸乱之源",
    4: "血魔",
    8: "主宰",
    14: "帕吉",
    22: "宙斯",
    44: "幻影刺客",
}

RANK_NAMES = {
    1: "先锋",
    2: "卫士",
    3: "中军",
    4: "统帅",
    5: "传奇",
    6: "万古",
    7: "超凡",
    8: "冠绝",
}

RECORD_FIELDS = ("kills", "gold_per_min", "xp_per_min", "hero_damage")


class DotaProfileDashboard(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["defaultSteamId64"] = DEFAULT_STEAM_ID64
        params["defaultAccountId"] = DEFAULT_ACCOUNT_ID
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        account_id = self._account_id(settings)
        if not account_id:
            raise RuntimeError("需要 OpenDota account_id，或可转换的 SteamID64。")

        now = time.time()
        refresh_minutes = self._bounded_int(settings.get("refreshMinutes"), 180, 30, 1440)
        cache_key = self._cache_key(settings, dimensions, account_id)
        cache = self._read_json(self._cache_path(cache_key), {})
        force_refresh = self._enabled(settings.get("forceRefresh"), default=False)

        if (
            not force_refresh
            and cache.get("schema") == STYLE_VERSION
            and now - float(cache.get("updated_ts", 0) or 0) < refresh_minutes * 60
            and cache.get("image_path")
            and Path(cache["image_path"]).exists()
        ):
            self._write_context(cache.get("data") or {}, cache.get("updated_ts", now), refresh_minutes)
            return Image.open(cache["image_path"]).convert("RGB")

        try:
            data = self._sample_payload(account_id) if self._enabled(settings.get("useMockData"), default=False) else self._fetch_dashboard_data(account_id, settings, device_config)
            data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            image = self._render_dashboard(data, dimensions, settings, get_theme_context(device_config))
            image_path = self._cache_image_path(cache_key)
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(image_path)
            self._write_json(self._cache_path(cache_key), {
                "schema": STYLE_VERSION,
                "updated_ts": now,
                "account_id": account_id,
                "image_path": str(image_path),
                "data": self._json_safe(data),
            })
            self._write_context(data, now, refresh_minutes)
            return image
        except Exception as exc:
            logger.error("Dota profile dashboard failed: %s", exc)
            if cache.get("image_path") and Path(cache["image_path"]).exists():
                logger.warning("Using stale Dota profile dashboard cache.")
                self._write_context(cache.get("data") or {}, cache.get("updated_ts", now), refresh_minutes)
                return Image.open(cache["image_path"]).convert("RGB")
            raise RuntimeError(f"Dota 个人信息页生成失败：{exc}")

    def _fetch_dashboard_data(self, account_id, settings, device_config):
        api_key = (
            str(settings.get("apiKey") or "").strip()
            or self._device_key(device_config, "OPENDOTA_API_KEY")
            or self._device_key(device_config, "OpenDota_Key")
        )
        params = {"api_key": api_key} if api_key else None
        recent_limit = self._bounded_int(settings.get("recentLimit"), 8, 3, 20)
        top_hero_limit = self._bounded_int(settings.get("topHeroLimit"), 8, 3, 20)

        calls = 0

        def get(path, query=None):
            nonlocal calls
            calls += 1
            merged = dict(params or {})
            if query:
                merged.update(query)
            return self._get_json(path, merged or None)

        profile = get(f"/players/{account_id}")
        wl = get(f"/players/{account_id}/wl")
        recent = get(f"/players/{account_id}/recentMatches")
        heroes = get(f"/players/{account_id}/heroes")
        totals = get(f"/players/{account_id}/totals")
        counts = get(f"/players/{account_id}/counts")
        rankings = get(f"/players/{account_id}/rankings")
        wordcloud = {}
        if self._enabled(settings.get("includeWordcloud"), default=True):
            wordcloud = get(f"/players/{account_id}/wordcloud")

        records = {}
        if self._enabled(settings.get("includeRecords"), default=True):
            for field in RECORD_FIELDS:
                try:
                    records[field] = get(f"/players/{account_id}/records/{field}")
                except Exception as exc:
                    logger.warning("OpenDota record field %s unavailable: %s", field, exc)

        hero_stats = self._hero_stats(params)

        return {
            "account_id": account_id,
            "profile": profile if isinstance(profile, dict) else {},
            "wl": wl if isinstance(wl, dict) else {},
            "recent_matches": list(recent or [])[:recent_limit],
            "heroes": list(heroes or [])[:top_hero_limit],
            "totals": list(totals or []),
            "counts": counts if isinstance(counts, dict) else {},
            "rankings": list(rankings or []),
            "wordcloud": wordcloud if isinstance(wordcloud, dict) else {},
            "records": records,
            "hero_stats": hero_stats,
            "api_calls": calls,
            "source": "OpenDota",
        }

    def _get_json(self, path, params=None):
        session = get_http_session()
        response = session.get(f"{OPENDOTA_BASE_URL}{path}", params=params or {}, timeout=35)
        response.raise_for_status()
        return response.json()

    def _hero_stats(self, params=None):
        cache_path = self._cache_dir() / "hero_stats.json"
        cache = self._read_json(cache_path, {})
        if cache.get("updated_ts") and time.time() - float(cache["updated_ts"]) < 24 * 60 * 60:
            stats = cache.get("heroes") or []
        else:
            stats = self._get_json("/heroStats", params or {})
            self._write_json(cache_path, {"updated_ts": time.time(), "heroes": stats})
        return self._apply_hero_names(stats, self._hero_name_overrides())

    def _apply_hero_names(self, stats, names):
        hero_map = {}
        for hero in stats:
            if not str(hero.get("id", "")).isdigit():
                continue
            hero_id = int(hero.get("id"))
            item = dict(hero)
            zh_name = names.get(hero_id) or ZH_HERO_NAME_FALLBACK.get(hero_id)
            if zh_name:
                if item.get("localized_name") and item.get("localized_name") != zh_name:
                    item["localized_name_en"] = item.get("localized_name")
                item["localized_name"] = zh_name
            hero_map[hero_id] = item
        return hero_map

    def _hero_name_overrides(self):
        cache_path = self._cache_dir() / "hero_names_schinese.json"
        cache = self._read_json(cache_path, {})
        if cache.get("updated_ts") and time.time() - float(cache["updated_ts"]) < 7 * 24 * 60 * 60:
            names = self._coerce_hero_name_map(cache.get("names") or {})
            if names:
                return names
        try:
            response = get_http_session().get(DOTA_HERO_LIST_URL, timeout=25)
            response.raise_for_status()
            payload = response.json()
            heroes = (((payload.get("result") or {}).get("data") or {}).get("heroes") or [])
            names = {}
            for hero in heroes:
                if str(hero.get("id", "")).isdigit():
                    name = self._simplified_hero_name(hero.get("name_loc"))
                    if name:
                        names[int(hero["id"])] = name
            if names:
                self._write_json(cache_path, {"updated_ts": time.time(), "names": names})
                return names
        except Exception as exc:
            logger.warning("Dota Simplified Chinese hero names unavailable: %s", exc)
        return ZH_HERO_NAME_FALLBACK

    def _coerce_hero_name_map(self, names):
        result = {}
        if isinstance(names, dict):
            for hero_id, name in names.items():
                if str(hero_id).isdigit():
                    name = self._simplified_hero_name(name)
                    if name:
                        result[int(hero_id)] = name
        return result

    def _simplified_hero_name(self, name):
        name = str(name or "").strip()
        return name.replace("獸", "兽")

    def _render_dashboard(self, data, dimensions, settings=None, theme_context=None):
        width, height = dimensions
        bg = (7, 13, 24)
        panel = (16, 27, 43)
        panel_alt = (18, 32, 51)
        border = (83, 142, 174)
        ink = (238, 244, 248)
        muted = (151, 166, 180)
        gold = (232, 185, 82)
        green = (97, 214, 128)
        red = (236, 92, 85)
        cyan = (104, 194, 226)

        image = Image.new("RGB", dimensions, bg)
        draw = ImageDraw.Draw(image)
        self._draw_background_pattern(draw, width, height)

        fonts = {
            "title": self._font(28, bold=True),
            "section": self._font(20, bold=True),
            "body": self._font(15),
            "small": self._font(13),
            "tiny": self._font(10),
            "micro": self._font(9),
        }

        margin = 22
        top_y = 22
        left_w = 208
        center_w = 344
        gap = 14
        right_w = width - margin * 2 - left_w - center_w - gap * 2
        top_h = 242
        bottom_y = top_y + top_h + 16
        bottom_h = height - bottom_y - 24

        left_box = (margin, top_y, margin + left_w, top_y + top_h)
        center_box = (left_box[2] + gap, top_y, left_box[2] + gap + center_w, top_y + top_h)
        right_box = (center_box[2] + gap, top_y, width - margin, top_y + top_h)
        bottom_box = (margin, bottom_y, width - margin, bottom_y + bottom_h)

        for box in (left_box, center_box, right_box, bottom_box):
            self._rect(draw, box, panel, border)

        self._draw_profile_panel(image, draw, data, left_box, fonts, ink, muted, gold, green, red, cyan)
        self._draw_recent_panel(image, draw, data, center_box, fonts, ink, muted, green, red, cyan)
        self._draw_heroes_panel(image, draw, data, right_box, fonts, ink, muted, gold, green)
        self._draw_bottom_panel(draw, data, bottom_box, fonts, ink, muted, gold, green, cyan, panel_alt)
        return image

    def _draw_profile_panel(self, image, draw, data, box, fonts, ink, muted, gold, green, red, cyan):
        x0, y0, x1, y1 = box
        profile = data.get("profile") or {}
        steam_profile = profile.get("profile") or {}
        name = steam_profile.get("personaname") or f"玩家 {data.get('account_id')}"
        avatar = self._avatar_image(steam_profile.get("avatarfull") or steam_profile.get("avatarmedium"), 74)
        image.paste(avatar, (x0 + 14, y0 + 18), avatar if avatar.mode == "RGBA" else None)
        self._text(draw, (x0 + 98, y0 + 17), "DOTA 个人档案", fonts["tiny"], cyan)
        self._single(draw, (x0 + 98, y0 + 35), name, fonts["section"], ink, x1 - x0 - 110, 11)

        rank_text = self._rank_text(profile.get("rank_tier"), profile.get("leaderboard_rank"))
        mmr = ((profile.get("mmr_estimate") or {}).get("estimate") or "-")
        wins = int((data.get("wl") or {}).get("win") or 0)
        losses = int((data.get("wl") or {}).get("lose") or 0)
        total = wins + losses
        winrate = wins / total * 100 if total else 0

        y = y0 + 100
        self._stat_line(draw, x0 + 16, y, "段位", rank_text, fonts, gold, ink, x1 - 18)
        self._stat_line(draw, x0 + 16, y + 27, "MMR", self._fmt(mmr), fonts, cyan, ink, x1 - 18)
        self._stat_line(draw, x0 + 16, y + 54, "胜率", f"{winrate:.1f}%  {wins}W/{losses}L", fonts, green if winrate >= 50 else red, ink, x1 - 18)
        self._stat_line(draw, x0 + 16, y + 81, "比赛", self._fmt(total), fonts, muted, ink, x1 - 18)

        updated = data.get("updated_at") or "-"
        self._text(draw, (x0 + 16, y1 - 36), f"OpenDota · {data.get('api_calls', 0)} calls", fonts["tiny"], muted)
        self._text(draw, (x0 + 16, y1 - 20), f"更新 {updated}", fonts["tiny"], muted)

    def _draw_recent_panel(self, image, draw, data, box, fonts, ink, muted, green, red, cyan):
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 14, y0 + 12), "最近比赛", fonts["section"], ink)
        recent = data.get("recent_matches") or []
        hero_map = data.get("hero_stats") or {}
        y = y0 + 42
        row_h = 37
        for match in recent[:5]:
            if y + row_h > y1 - 10:
                break
            hero_id = int(match.get("hero_id") or 0)
            hero = hero_map.get(hero_id) or {}
            icon = self._hero_icon(hero_id, hero, 28)
            image.paste(icon, (x0 + 14, y + 4), icon if icon.mode == "RGBA" else None)
            won = self._match_won(match)
            marker = green if won else red
            self._rect(draw, (x0 + 48, y + 4, x0 + 75, y + 18), marker, marker)
            self._text(draw, (x0 + 53, y + 5), "胜" if won else "负", fonts["micro"], (5, 12, 20))
            hero_name = hero.get("localized_name") or hero.get("name") or f"英雄 {hero_id}"
            self._single(draw, (x0 + 82, y), hero_name, fonts["small"], ink, 122, 9)
            kda = f"{match.get('kills', 0)}/{match.get('deaths', 0)}/{match.get('assists', 0)}"
            gpm = self._fmt(match.get("gold_per_min"))
            xpm = self._fmt(match.get("xp_per_min"))
            self._text(draw, (x0 + 214, y), f"KDA {kda}", fonts["tiny"], ink)
            self._text(draw, (x0 + 214, y + 16), f"GPM {gpm}  XPM {xpm}", fonts["tiny"], muted)
            y += row_h
        if not recent:
            self._text(draw, (x0 + 16, y0 + 58), "没有公开的近期比赛数据", fonts["body"], muted)

    def _draw_heroes_panel(self, image, draw, data, box, fonts, ink, muted, gold, green):
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 12, y0 + 12), "常用英雄", fonts["section"], ink)
        hero_map = data.get("hero_stats") or {}
        y = y0 + 42
        for hero_entry in (data.get("heroes") or [])[:4]:
            hero_id = int(hero_entry.get("hero_id") or 0)
            hero = hero_map.get(hero_id) or {}
            games = int(hero_entry.get("games") or 0)
            win = int(hero_entry.get("win") or 0)
            rate = win / games * 100 if games else 0
            icon = self._hero_icon(hero_id, hero, 30)
            image.paste(icon, (x0 + 12, y), icon if icon.mode == "RGBA" else None)
            name = hero.get("localized_name") or hero.get("name") or f"英雄 {hero_id}"
            self._single(draw, (x0 + 48, y - 1), name, fonts["small"], ink, x1 - x0 - 58, 9)
            self._text(draw, (x0 + 48, y + 16), f"{games} 场  {rate:.0f}% 胜率", fonts["tiny"], green if rate >= 50 else muted)
            y += 43

        top_rank = self._first_ranking(data)
        if top_rank:
            y = y1 - 37
            self._text(draw, (x0 + 12, y), "英雄排名", fonts["tiny"], muted)
            self._single(draw, (x0 + 12, y + 14), top_rank, fonts["small"], gold, x1 - x0 - 24, 9)

    def _draw_bottom_panel(self, draw, data, box, fonts, ink, muted, gold, green, cyan, panel_alt):
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 14, y0 + 10), "总览", fonts["section"], ink)
        col_w = (x1 - x0 - 44) // 3
        col1 = x0 + 14
        col2 = col1 + col_w + 14
        col3 = col2 + col_w + 14

        totals = data.get("totals") or []
        wl = data.get("wl") or {}
        wins = int(wl.get("win") or 0)
        losses = int(wl.get("lose") or 0)
        matches = wins + losses
        avg_kills = self._avg_total(totals, "kills")
        avg_deaths = self._avg_total(totals, "deaths")
        avg_assists = self._avg_total(totals, "assists")
        avg_gpm = self._avg_total(totals, "gold_per_min")
        avg_xpm = self._avg_total(totals, "xp_per_min")
        duration_hours = self._sum_total(totals, "duration") / 3600

        stats = [
            ("公开比赛", self._fmt(matches)),
            ("游玩时长", f"{duration_hours:.0f}h" if duration_hours else "-"),
            ("平均 KDA", f"{avg_kills:.1f}/{avg_deaths:.1f}/{avg_assists:.1f}" if avg_kills or avg_assists else "-"),
            ("平均 GPM/XPM", f"{avg_gpm:.0f}/{avg_xpm:.0f}" if avg_gpm or avg_xpm else "-"),
        ]
        y = y0 + 42
        for label, value in stats:
            self._metric(draw, col1, y, label, value, fonts, muted, ink)
            y += 28

        self._text(draw, (col2, y0 + 10), "个人纪录", fonts["section"], ink)
        y = y0 + 42
        for label, value in self._record_lines(data)[:4]:
            self._metric(draw, col2, y, label, value, fonts, muted, gold)
            y += 28

        self._text(draw, (col3, y0 + 10), "细项 / 词云", fonts["section"], ink)
        keywords = self._wordcloud_terms(data.get("wordcloud") or {})[:7]
        counts_line = self._counts_line(data.get("counts") or {})
        self._wrapped(draw, (col3, y0 + 42), counts_line, fonts["small"], cyan, col_w)
        self._wrapped(draw, (col3, y0 + 72), " · ".join(keywords) if keywords else "没有公开聊天词云", fonts["small"], ink, col_w)

    def _write_context(self, data, generated_at, refresh_minutes):
        if not isinstance(data, dict):
            return
        profile = data.get("profile") or {}
        steam_profile = profile.get("profile") or {}
        name = steam_profile.get("personaname") or f"Dota player {data.get('account_id', '')}"
        wl = data.get("wl") or {}
        wins = int(wl.get("win") or 0)
        losses = int(wl.get("lose") or 0)
        summary = f"{name}: {self._rank_text(profile.get('rank_tier'), profile.get('leaderboard_rank'))}, {wins}W/{losses}L"
        write_context(
            PLUGIN_ID,
            {
                "kind": "dota_profile",
                "source": "OpenDota",
                "summary": summary,
                "account_id": data.get("account_id"),
                "rank": self._rank_text(profile.get("rank_tier"), profile.get("leaderboard_rank")),
                "wins": wins,
                "losses": losses,
                "recent_matches": len(data.get("recent_matches") or []),
            },
            generated_at=datetime.fromtimestamp(float(generated_at), timezone.utc),
            ttl_seconds=int(refresh_minutes) * 60,
        )

    def _match_won(self, match):
        player_slot = int(match.get("player_slot") or 0)
        radiant = player_slot < 128
        radiant_win = bool(match.get("radiant_win"))
        return radiant == radiant_win

    def _rank_text(self, rank_tier, leaderboard_rank=None):
        try:
            rank_tier = int(rank_tier or 0)
        except Exception:
            rank_tier = 0
        if leaderboard_rank:
            return f"冠绝 #{leaderboard_rank}"
        if rank_tier <= 0:
            return "未校准"
        major = rank_tier // 10
        stars = rank_tier % 10
        base = RANK_NAMES.get(major, "段位")
        return f"{base} {stars}" if stars else base

    def _first_ranking(self, data):
        hero_map = data.get("hero_stats") or {}
        for item in data.get("rankings") or []:
            hero_id = int(item.get("hero_id") or 0)
            hero = hero_map.get(hero_id) or {}
            rank = item.get("rank") or item.get("percent_rank")
            if hero_id and rank:
                name = hero.get("localized_name") or f"英雄 {hero_id}"
                return f"{name} #{rank}"
        return ""

    def _record_lines(self, data):
        labels = {
            "kills": "最高击杀",
            "gold_per_min": "最高 GPM",
            "xp_per_min": "最高 XPM",
            "hero_damage": "最高英雄伤害",
        }
        hero_map = data.get("hero_stats") or {}
        lines = []
        for field in RECORD_FIELDS:
            records = data.get("records", {}).get(field) or []
            if not records:
                continue
            record = records[0]
            value = record.get(field)
            hero = hero_map.get(int(record.get("hero_id") or 0), {})
            hero_name = hero.get("localized_name") or ""
            lines.append((labels.get(field, field), f"{self._fmt(value)} {hero_name}".strip()))
        return lines

    def _counts_line(self, counts):
        for key in ("game_mode", "lobby_type", "region"):
            section = counts.get(key)
            if isinstance(section, dict) and section:
                top_key, top_value = max(section.items(), key=lambda pair: int(pair[1] or 0))
                return f"{key}: {top_key} · {self._fmt(top_value)} 场"
        return "模式/大厅/地区数据不足"

    def _wordcloud_terms(self, wordcloud):
        counts = wordcloud.get("my_word_counts") or wordcloud.get("all_word_counts") or {}
        if not isinstance(counts, dict):
            return []
        blocked = {"gg", "wp", "ez", "the", "and", "you"}
        terms = []
        for word, count in sorted(counts.items(), key=lambda pair: int(pair[1] or 0), reverse=True):
            word = str(word).strip()
            if len(word) < 2 or word.lower() in blocked:
                continue
            terms.append(word)
            if len(terms) >= 8:
                break
        return terms

    def _hero_icon(self, hero_id, hero, size):
        for url in self._hero_image_urls(hero):
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
                logger.warning("Dota hero icon unavailable for %s: %s", hero_id, exc)
        return self._hero_placeholder(hero_id, hero, size)

    def _hero_image_urls(self, hero):
        urls = []
        for key in ("icon", "img"):
            candidate = str((hero or {}).get(key) or "").strip()
            if not candidate:
                continue
            if candidate.startswith("http"):
                urls.append(candidate)
                if candidate.startswith(OPENDOTA_ASSET_BASE_URL):
                    urls.append(f"{STEAM_ASSET_BASE_URL}{candidate[len(OPENDOTA_ASSET_BASE_URL):]}")
            else:
                urls.append(f"{STEAM_ASSET_BASE_URL}{candidate}")
                urls.append(f"{OPENDOTA_ASSET_BASE_URL}{candidate}")
        deduped = []
        for url in urls:
            if url and url not in deduped:
                deduped.append(url)
        return deduped

    def _avatar_image(self, url, size):
        if url:
            cache_path = self._image_cache_path(url)
            try:
                if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 14 * 24 * 60 * 60:
                    raw = Image.open(cache_path).convert("RGB")
                else:
                    response = get_http_session().get(url, timeout=20)
                    response.raise_for_status()
                    raw = Image.open(BytesIO(response.content)).convert("RGB")
                    raw = ImageOps.exif_transpose(raw)
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    raw.save(cache_path)
                icon = ImageOps.fit(raw, (size, size), method=Image.Resampling.LANCZOS)
            except Exception as exc:
                logger.warning("Dota profile avatar unavailable: %s", exc)
                icon = Image.new("RGB", (size, size), (39, 48, 62))
        else:
            icon = Image.new("RGB", (size, size), (39, 48, 62))
        mask = Image.new("L", (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, size - 1, size - 1), fill=255)
        result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        result.paste(icon, (0, 0), mask)
        ImageDraw.Draw(result).ellipse((1, 1, size - 2, size - 2), outline=(232, 185, 82), width=3)
        return result

    def _square_icon(self, raw, size):
        raw = ImageOps.exif_transpose(raw).convert("RGBA")
        raw = self._blacken_light_pixels(raw)
        icon = ImageOps.contain(raw, (size, size), method=Image.Resampling.LANCZOS)
        icon = self._blacken_light_pixels(icon)
        result = Image.new("RGBA", (size, size), (0, 0, 0, 255))
        result.alpha_composite(icon, ((size - icon.width) // 2, (size - icon.height) // 2))
        return result

    def _blacken_light_pixels(self, image):
        image = image.convert("RGBA")
        pixels = image.load()
        width, height = image.size
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                if a and r >= 215 and g >= 215 and b >= 215:
                    pixels[x, y] = (0, 0, 0, a)
        return image

    def _hero_placeholder(self, hero_id, hero, size):
        image = Image.new("RGBA", (size, size), (0, 0, 0, 255))
        draw = ImageDraw.Draw(image)
        text = str(hero.get("localized_name") or hero.get("name") or hero_id or "?")[:2].upper()
        font = self._font(max(8, size // 3), bold=True)
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((size - (bbox[2] - bbox[0])) / 2, (size - (bbox[3] - bbox[1])) / 2 - 1), text, font=font, fill=(255, 255, 255))
        return image

    def _draw_background_pattern(self, draw, width, height):
        for x in range(-40, width, 86):
            for y in range(-30, height, 72):
                draw.line((x, y + 34, x + 34, y), fill=(12, 22, 36), width=10)
                draw.line((x + 34, y, x + 68, y + 34), fill=(12, 22, 36), width=10)

    def _rect(self, draw, box, fill, outline):
        draw.rectangle(box, fill=fill, outline=outline, width=2)

    def _text(self, draw, position, text, font, fill):
        draw.text(position, str(text), font=font, fill=fill)

    def _single(self, draw, position, text, font, fill, max_width, min_size=8):
        text = str(text or "")
        fitted = self._fit_font(draw, text, font, max_width, min_size)
        draw.text(position, text, font=fitted, fill=fill)

    def _wrapped(self, draw, position, text, font, fill, max_width, line_gap=2):
        x, y = position
        line_height = self._line_height(draw, font)
        for line in self._wrap_text(draw, str(text or ""), font, max_width):
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height + line_gap
        return y

    def _stat_line(self, draw, x, y, label, value, fonts, accent, ink, max_x):
        self._text(draw, (x, y), label, fonts["tiny"], accent)
        self._single(draw, (x + 50, y - 4), value, fonts["body"], ink, max_x - x - 52, 9)

    def _metric(self, draw, x, y, label, value, fonts, label_color, value_color):
        self._text(draw, (x, y), label, fonts["tiny"], label_color)
        self._single(draw, (x + 70, y - 3), value, fonts["small"], value_color, 145, 8)

    def _fit_font(self, draw, text, font, max_width, min_size):
        if self._text_width(draw, text, font) <= max_width:
            return font
        current = int(getattr(font, "size", 0) or 0)
        for size in range(current - 1, min_size - 1, -1):
            candidate = self._font(size, bold=bool(getattr(font, "path", "") and "bd" in str(getattr(font, "path", "")).lower()))
            if self._text_width(draw, text, candidate) <= max_width:
                return candidate
        return self._font(min_size)

    def _wrap_text(self, draw, text, font, max_width):
        if not text:
            return []
        lines = []
        current = ""
        for char in text:
            candidate = current + char
            if current and self._text_width(draw, candidate, font) > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def _text_width(self, draw, text, font):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        return bbox[2] - bbox[0]

    def _line_height(self, draw, font):
        bbox = draw.textbbox((0, 0), "Ag国", font=font)
        return max(1, bbox[3] - bbox[1] + 2)

    def _font(self, size, bold=False):
        plugin_dir = Path(self.get_plugin_dir())
        candidates = []
        sports_fonts = plugin_dir.parent / "sports_dashboard" / "fonts"
        if bold:
            candidates.extend([
                plugin_dir / "fonts" / "msyhbd.ttc",
                sports_fonts / "msyhbd.ttc",
                Path("C:/Windows/Fonts/msyhbd.ttc"),
            ])
        candidates.extend([
            plugin_dir / "fonts" / "msyh.ttc",
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

    def _avg_total(self, totals, field):
        for row in totals:
            if row.get("field") == field:
                n = float(row.get("n") or 0)
                return float(row.get("sum") or 0) / n if n else 0.0
        return 0.0

    def _sum_total(self, totals, field):
        for row in totals:
            if row.get("field") == field:
                return float(row.get("sum") or 0)
        return 0.0

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

    def _account_id(self, settings):
        account_id = str(settings.get("accountId") or settings.get("account_id") or "").strip()
        if account_id.isdigit():
            return account_id
        steam_id = str(settings.get("steamId64") or settings.get("steamId") or DEFAULT_STEAM_ID64).strip()
        if steam_id.isdigit():
            value = int(steam_id)
            if value > STEAMID64_BASE:
                return str(value - STEAMID64_BASE)
            return str(value)
        return ""

    def _cache_key(self, settings, dimensions, account_id):
        parts = [
            STYLE_VERSION,
            str(account_id),
            str(dimensions),
            str(self._bounded_int(settings.get("recentLimit"), 8, 3, 20)),
            str(self._bounded_int(settings.get("topHeroLimit"), 8, 3, 20)),
            str(self._enabled(settings.get("includeWordcloud"), default=True)),
            str(self._enabled(settings.get("includeRecords"), default=True)),
            str(self._enabled(settings.get("useMockData"), default=False)),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]

    def _cache_dir(self):
        path = os.getenv("INKYPI_DOTA_PROFILE_CACHE")
        if path:
            return Path(path)
        return Path(self.get_plugin_dir(".dota_profile_cache"))

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
            logger.warning("Failed reading Dota cache %s: %s", path, exc)
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

    def _device_key(self, device_config, key):
        try:
            return str(device_config.load_env_key(key) or "").strip()
        except Exception:
            return ""

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

    def _sample_payload(self, account_id=DEFAULT_ACCOUNT_ID):
        hero_stats = {
            1: {"id": 1, "localized_name": "敌法师", "name": "npc_dota_hero_antimage", "icon": "/apps/dota2/images/dota_react/heroes/icons/antimage.png?", "img": "/apps/dota2/images/dota_react/heroes/antimage.png?"},
            2: {"id": 2, "localized_name": "斧王", "name": "npc_dota_hero_axe", "icon": "/apps/dota2/images/dota_react/heroes/icons/axe.png?", "img": "/apps/dota2/images/dota_react/heroes/axe.png?"},
            8: {"id": 8, "localized_name": "主宰", "name": "npc_dota_hero_juggernaut", "icon": "/apps/dota2/images/dota_react/heroes/icons/juggernaut.png?", "img": "/apps/dota2/images/dota_react/heroes/juggernaut.png?"},
            14: {"id": 14, "localized_name": "帕吉", "name": "npc_dota_hero_pudge", "icon": "/apps/dota2/images/dota_react/heroes/icons/pudge.png?", "img": "/apps/dota2/images/dota_react/heroes/pudge.png?"},
            22: {"id": 22, "localized_name": "宙斯", "name": "npc_dota_hero_zuus", "icon": "/apps/dota2/images/dota_react/heroes/icons/zuus.png?", "img": "/apps/dota2/images/dota_react/heroes/zuus.png?"},
            44: {"id": 44, "localized_name": "幻影刺客", "name": "npc_dota_hero_phantom_assassin", "icon": "/apps/dota2/images/dota_react/heroes/icons/phantom_assassin.png?", "img": "/apps/dota2/images/dota_react/heroes/phantom_assassin.png?"},
        }
        for hero in hero_stats.values():
            hero.pop("icon", None)
            hero.pop("img", None)
        return {
            "account_id": str(account_id),
            "profile": {
                "rank_tier": 65,
                "leaderboard_rank": None,
                "mmr_estimate": {"estimate": 4380},
                "profile": {
                    "personaname": "Shhhhhhh",
                    "avatarfull": "",
                },
            },
            "wl": {"win": 1284, "lose": 1112},
            "recent_matches": [
                {"hero_id": 8, "radiant_win": True, "player_slot": 1, "kills": 13, "deaths": 4, "assists": 11, "gold_per_min": 612, "xp_per_min": 728},
                {"hero_id": 14, "radiant_win": False, "player_slot": 129, "kills": 5, "deaths": 9, "assists": 18, "gold_per_min": 382, "xp_per_min": 496},
                {"hero_id": 22, "radiant_win": True, "player_slot": 132, "kills": 9, "deaths": 3, "assists": 22, "gold_per_min": 548, "xp_per_min": 681},
                {"hero_id": 44, "radiant_win": False, "player_slot": 4, "kills": 10, "deaths": 8, "assists": 7, "gold_per_min": 601, "xp_per_min": 612},
                {"hero_id": 2, "radiant_win": True, "player_slot": 130, "kills": 4, "deaths": 6, "assists": 25, "gold_per_min": 431, "xp_per_min": 552},
            ],
            "heroes": [
                {"hero_id": 14, "games": 312, "win": 176},
                {"hero_id": 8, "games": 226, "win": 128},
                {"hero_id": 44, "games": 188, "win": 96},
                {"hero_id": 22, "games": 144, "win": 84},
            ],
            "totals": [
                {"field": "kills", "n": 2396, "sum": 17892},
                {"field": "deaths", "n": 2396, "sum": 13224},
                {"field": "assists", "n": 2396, "sum": 29811},
                {"field": "gold_per_min", "n": 2396, "sum": 1092380},
                {"field": "xp_per_min", "n": 2396, "sum": 1265150},
                {"field": "duration", "n": 2396, "sum": 6400000},
            ],
            "counts": {"game_mode": {"22": 1480, "2": 402}, "lobby_type": {"7": 1280}},
            "rankings": [{"hero_id": 14, "rank": 391}],
            "wordcloud": {"my_word_counts": {"push": 28, "roshan": 19, "ward": 15, "smoke": 13, "bkb": 12}},
            "records": {
                "kills": [{"hero_id": 8, "kills": 31}],
                "gold_per_min": [{"hero_id": 44, "gold_per_min": 923}],
                "xp_per_min": [{"hero_id": 22, "xp_per_min": 1048}],
                "hero_damage": [{"hero_id": 22, "hero_damage": 78120}],
            },
            "hero_stats": hero_stats,
            "api_calls": 0,
            "source": "OpenDota mock",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
