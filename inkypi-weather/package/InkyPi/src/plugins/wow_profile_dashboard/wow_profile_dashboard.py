from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.http_client import get_http_session
from utils.image_utils import text_width

logger = logging.getLogger(__name__)

PLUGIN_ID = "wow_profile_dashboard"
STYLE_VERSION = "wow-profile-dashboard-v1"
DEFAULT_REGION = "us"
DEFAULT_LOCALE = "en_US"

REGION_CONFIG = {
    "us": {
        "api": "https://us.api.blizzard.com",
        "oauth": "https://us.battle.net/oauth/token",
        "namespace": "profile-us",
        "locale": "en_US",
        "label": "US",
    },
    "eu": {
        "api": "https://eu.api.blizzard.com",
        "oauth": "https://eu.battle.net/oauth/token",
        "namespace": "profile-eu",
        "locale": "en_GB",
        "label": "EU",
    },
    "kr": {
        "api": "https://kr.api.blizzard.com",
        "oauth": "https://kr.battle.net/oauth/token",
        "namespace": "profile-kr",
        "locale": "ko_KR",
        "label": "KR",
    },
    "tw": {
        "api": "https://tw.api.blizzard.com",
        "oauth": "https://tw.battle.net/oauth/token",
        "namespace": "profile-tw",
        "locale": "zh_TW",
        "label": "TW",
    },
}


class WowProfileDashboard(BasePlugin):
    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self._token_cache: dict[str, Any] = {}

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["api_key"] = {
            "required": True,
            "service": "Battle.net API",
            "expected_key": "BLIZZARD_CLIENT_ID + BLIZZARD_CLIENT_SECRET",
        }
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        refresh_minutes = self._bounded_int(settings.get("refreshMinutes"), 90, 15, 1440)
        cache_key = self._cache_key(settings, dimensions)
        cache_path = self._cache_path(cache_key)
        cache = self._read_json(cache_path, {})
        now = time.time()
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

        data: dict[str, Any]
        try:
            if self._enabled(settings.get("useMockData"), default=False):
                data = self._sample_payload()
            else:
                data = self._fetch_dashboard_data(settings, device_config)
        except Exception as exc:
            logger.warning("WoW profile dashboard fetch failed: %s", exc)
            if cache.get("image_path") and Path(cache["image_path"]).exists():
                self._write_context(cache.get("data") or {}, cache.get("updated_ts", now), refresh_minutes)
                return Image.open(cache["image_path"]).convert("RGB")
            data = self._status_payload(
                "Fetch failed",
                str(exc),
                settings=settings,
                source="Battle.net",
            )

        data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        image = self._render_dashboard(data, dimensions)
        image_path = self._cache_image_path(cache_key)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(image_path)
        self._write_json(cache_path, {
            "schema": STYLE_VERSION,
            "updated_ts": now,
            "image_path": str(image_path),
            "data": self._json_safe(data),
        })
        self._write_context(data, now, refresh_minutes)
        return image

    def _fetch_dashboard_data(self, settings, device_config):
        region = self._region(settings)
        locale = self._locale(settings, region)
        realm_slug = self._slug(settings.get("realmSlug") or settings.get("realm") or "")
        character_name = self._character_slug(settings.get("characterName") or settings.get("character") or "")

        if not realm_slug or not character_name:
            user_token = self._user_access_token(settings, device_config)
            if user_token:
                return self._fetch_account_profile(region, locale, user_token)
            return self._status_payload(
                "Character required",
                "Add a US realm slug and character name, or provide BLIZZARD_USER_ACCESS_TOKEN for account mode.",
                settings=settings,
                source="setup",
            )

        token = self._public_access_token(settings, device_config, region)
        if not token:
            return self._status_payload(
                "API key required",
                "Add BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET in API Keys. Common BNET/BATTLE_NET/WOW aliases are accepted.",
                settings=settings,
                source="setup",
            )

        return self._fetch_character_dashboard(region, locale, realm_slug, character_name, token)

    def _fetch_character_dashboard(self, region, locale, realm_slug, character_name, token):
        profile = self._api_get(region, f"/profile/wow/character/{realm_slug}/{character_name}", token, locale)
        media = self._api_get_optional(region, f"/profile/wow/character/{realm_slug}/{character_name}/character-media", token, locale)
        equipment = self._api_get_optional(region, f"/profile/wow/character/{realm_slug}/{character_name}/equipment", token, locale)
        mythic = self._api_get_optional(region, f"/profile/wow/character/{realm_slug}/{character_name}/mythic-keystone-profile", token, locale)
        pvp = self._api_get_optional(region, f"/profile/wow/character/{realm_slug}/{character_name}/pvp-summary", token, locale)

        return self._normalize_character_payload(region, locale, profile, media, equipment, mythic, pvp, source="Battle.net public profile")

    def _fetch_account_profile(self, region, locale, token):
        account = self._api_get(region, "/profile/user/wow", token, locale)
        characters = self._account_characters(account)
        if characters:
            top = characters[0]
            realm_slug = self._slug(((top.get("realm") or {}).get("slug") or ""))
            character_name = self._character_slug(top.get("name") or "")
            if realm_slug and character_name:
                data = self._fetch_character_dashboard(region, locale, realm_slug, character_name, token)
                data["mode"] = "account"
                data["account_characters"] = characters[:8]
                data["subtitle"] = "Highest visible character from account OAuth"
                return data
        return {
            "kind": "account",
            "mode": "account",
            "status": "ok",
            "title": "WoW Account",
            "subtitle": "Account OAuth profile",
            "region": REGION_CONFIG[region]["label"],
            "locale": locale,
            "source": "Battle.net account profile",
            "account_characters": characters[:8],
            "notice": "" if characters else "No characters returned by account profile.",
        }

    def _api_get(self, region, path, token, locale):
        cfg = REGION_CONFIG[region]
        params = {"namespace": cfg["namespace"], "locale": locale}
        headers = {"Authorization": f"Bearer {token}"}
        response = get_http_session().get(f"{cfg['api']}{path}", params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    def _api_get_optional(self, region, path, token, locale):
        try:
            return self._api_get(region, path, token, locale)
        except Exception as exc:
            logger.info("Optional WoW endpoint unavailable for %s: %s", path, exc)
            return {}

    def _public_access_token(self, settings, device_config, region):
        token = (
            str(settings.get("accessToken") or "").strip()
            or self._device_key(device_config, "BLIZZARD_ACCESS_TOKEN")
            or self._device_key(device_config, "BNET_ACCESS_TOKEN")
            or self._device_key(device_config, "BATTLE_NET_ACCESS_TOKEN")
            or self._device_key(device_config, "WOW_ACCESS_TOKEN")
        )
        if token:
            return token
        return self._client_credentials_token(settings, device_config, region)

    def _user_access_token(self, settings, device_config):
        return (
            str(settings.get("userAccessToken") or "").strip()
            or self._device_key(device_config, "BLIZZARD_USER_ACCESS_TOKEN")
            or self._device_key(device_config, "BNET_USER_ACCESS_TOKEN")
            or self._device_key(device_config, "BATTLE_NET_USER_ACCESS_TOKEN")
            or self._device_key(device_config, "WOW_PROFILE_ACCESS_TOKEN")
        )

    def _client_credentials_token(self, settings, device_config, region):
        client_id = (
            str(settings.get("clientId") or "").strip()
            or self._device_key(device_config, "BLIZZARD_CLIENT_ID")
            or self._device_key(device_config, "BNET_CLIENT_ID")
            or self._device_key(device_config, "BATTLE_NET_CLIENT_ID")
            or self._device_key(device_config, "WOW_CLIENT_ID")
        )
        client_secret = (
            str(settings.get("clientSecret") or "").strip()
            or self._device_key(device_config, "BLIZZARD_CLIENT_SECRET")
            or self._device_key(device_config, "BNET_CLIENT_SECRET")
            or self._device_key(device_config, "BATTLE_NET_CLIENT_SECRET")
            or self._device_key(device_config, "WOW_CLIENT_SECRET")
        )
        if not client_id or not client_secret:
            return ""

        cfg = REGION_CONFIG[region]
        cache_key = f"{region}:{hashlib.sha1(client_id.encode('utf-8')).hexdigest()[:12]}"
        cached = self._token_cache.get(cache_key) or {}
        if cached.get("token") and time.time() < float(cached.get("expires_at", 0) or 0):
            return cached["token"]

        response = get_http_session().post(
            cfg["oauth"],
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Battle.net OAuth response did not include access_token")
        expires_in = max(60, int(payload.get("expires_in") or 1800))
        self._token_cache[cache_key] = {
            "token": token,
            "expires_at": time.time() + max(60, expires_in - 90),
        }
        return token

    def _normalize_character_payload(self, region, locale, profile, media, equipment, mythic, pvp, source):
        profile = profile if isinstance(profile, dict) else {}
        media = media if isinstance(media, dict) else {}
        equipment = equipment if isinstance(equipment, dict) else {}
        mythic = mythic if isinstance(mythic, dict) else {}
        pvp = pvp if isinstance(pvp, dict) else {}

        items = self._equipment_rows(equipment)
        mythic_rating = (((mythic.get("current_mythic_rating") or {}).get("rating")) or 0)
        pvp_bracket = self._best_pvp_bracket(pvp)
        last_login = self._format_timestamp(profile.get("last_login_timestamp"))
        title = str(profile.get("name") or "WoW Character").strip()
        realm = (profile.get("realm") or {}).get("name") or (profile.get("realm") or {}).get("slug") or "-"
        char_class = (profile.get("character_class") or {}).get("name") or "-"
        spec = (profile.get("active_spec") or {}).get("name") or "-"

        return {
            "kind": "character",
            "mode": "public-character",
            "status": "ok",
            "title": title,
            "subtitle": f"{realm} / {REGION_CONFIG[region]['label']}",
            "region": REGION_CONFIG[region]["label"],
            "locale": locale,
            "source": source,
            "level": profile.get("level") or "-",
            "race": (profile.get("race") or {}).get("name") or "-",
            "class": char_class,
            "spec": spec,
            "faction": (profile.get("faction") or {}).get("name") or "-",
            "achievement_points": profile.get("achievement_points") or 0,
            "average_item_level": profile.get("average_item_level") or "-",
            "equipped_item_level": profile.get("equipped_item_level") or "-",
            "last_login": last_login,
            "portrait_url": self._media_url(media, ("avatar", "inset", "main-raw", "main")),
            "media_url": self._media_url(media, ("main-raw", "main", "inset", "avatar")),
            "equipment": items[:6],
            "mythic_rating": mythic_rating,
            "mythic_best": self._mythic_best_runs(mythic)[:3],
            "pvp_bracket": pvp_bracket,
            "notice": "",
        }

    def _account_characters(self, account):
        rows = []
        for wow_account in (account or {}).get("wow_accounts") or []:
            for character in wow_account.get("characters") or []:
                if not isinstance(character, dict):
                    continue
                rows.append({
                    "name": character.get("name") or "-",
                    "realm": character.get("realm") or {},
                    "level": int(character.get("level") or 0),
                    "class": ((character.get("playable_class") or {}).get("name") or ""),
                    "race": ((character.get("playable_race") or {}).get("name") or ""),
                    "faction": ((character.get("faction") or {}).get("name") or ""),
                })
        rows.sort(key=lambda item: (item.get("level", 0), item.get("name", "")), reverse=True)
        return rows

    def _equipment_rows(self, equipment):
        rows = []
        for item in equipment.get("equipped_items") or []:
            if not isinstance(item, dict):
                continue
            slot = ((item.get("slot") or {}).get("name") or "").strip()
            name = str(item.get("name") or "").strip()
            level = ((item.get("level") or {}).get("value") or item.get("item_level") or "")
            quality = ((item.get("quality") or {}).get("type") or "").strip()
            if name:
                rows.append({
                    "slot": slot or "-",
                    "name": name,
                    "level": level or "-",
                    "quality": quality or "-",
                })
        rows.sort(key=lambda item: self._numeric(item.get("level")), reverse=True)
        return rows

    def _mythic_best_runs(self, mythic):
        runs = []
        for run in mythic.get("current_period", {}).get("best_runs") or []:
            dungeon = ((run.get("dungeon") or {}).get("name") or "-")
            level = run.get("keystone_level") or "-"
            score = ((run.get("score") or {}).get("rating")) or 0
            runs.append({"dungeon": dungeon, "level": level, "score": score})
        runs.sort(key=lambda item: self._numeric(item.get("score")), reverse=True)
        return runs

    def _best_pvp_bracket(self, pvp):
        best = {}
        for bracket in pvp.get("brackets") or []:
            if not isinstance(bracket, dict):
                continue
            rating = int(bracket.get("rating") or 0)
            if rating > int(best.get("rating") or 0):
                best = {
                    "type": ((bracket.get("bracket") or {}).get("type") or "-"),
                    "rating": rating,
                    "season_match_statistics": bracket.get("season_match_statistics") or {},
                }
        return best

    def _media_url(self, media, keys):
        assets = media.get("assets") or []
        by_key = {asset.get("key"): asset.get("value") for asset in assets if isinstance(asset, dict)}
        for key in keys:
            value = by_key.get(key)
            if value:
                return value
        return ""

    def _render_dashboard(self, data, dimensions):
        width, height = dimensions
        palette = {
            "bg": (19, 20, 24),
            "pattern": (31, 32, 37),
            "panel": (232, 220, 185),
            "panel_dark": (44, 40, 35),
            "ink": (28, 27, 25),
            "light": (246, 239, 218),
            "muted": (98, 91, 79),
            "gold": (199, 151, 54),
            "blue": (56, 136, 178),
            "red": (150, 52, 48),
            "green": (82, 141, 91),
            "line": (84, 72, 55),
        }
        image = Image.new("RGB", dimensions, palette["bg"])
        draw = ImageDraw.Draw(image)
        self._draw_background(draw, width, height, palette)
        fonts = {
            "hero": self._font(31, bold=True),
            "title": self._font(25, bold=True),
            "section": self._font(18, bold=True),
            "body": self._font(14),
            "small": self._font(12),
            "tiny": self._font(10),
            "micro": self._font(9),
        }

        if data.get("status") != "ok" or data.get("kind") == "status":
            self._draw_status_screen(image, draw, data, fonts, palette)
            return image
        if data.get("kind") == "account":
            self._draw_account_screen(image, draw, data, fonts, palette)
            return image
        self._draw_character_screen(image, draw, data, fonts, palette)
        return image

    def _draw_character_screen(self, image, draw, data, fonts, palette):
        width, height = image.size
        margin = 22
        left_w = 374
        gap = 14
        right_x = margin + left_w + gap
        top_h = 136

        left_box = (margin, margin, margin + left_w, height - margin)
        top_box = (right_x, margin, width - margin, margin + top_h)
        bottom_box = (right_x, top_box[3] + gap, width - margin, height - margin)

        self._panel(draw, left_box, palette["panel_dark"], palette["gold"], radius=6)
        self._panel(draw, top_box, palette["panel"], palette["gold"], radius=6)
        self._panel(draw, bottom_box, palette["panel_dark"], palette["blue"], radius=6)

        self._draw_gear_paperdoll(image, draw, data, left_box, fonts, palette)
        self._draw_metrics(draw, data, top_box, fonts, palette)
        self._draw_activity(draw, data, bottom_box, fonts, palette)

    def _draw_gear_paperdoll(self, image, draw, data, box, fonts, palette):
        x0, y0, x1, y1 = box
        title_color = palette["light"]
        muted = (190, 178, 150)
        self._text(draw, (x0 + 18, y0 + 12), "Gear", fonts["section"], palette["gold"])
        self._single(draw, (x1 - 70, y0 + 17), data.get("region") or "-", fonts["tiny"], muted, 50, 8)

        character_box = (x0 + 120, y0 + 58, x0 + 254, y0 + 333)
        draw.ellipse((character_box[0] - 22, character_box[1] - 18, character_box[2] + 22, character_box[3] - 38), outline=(65, 60, 52), width=1)
        self._draw_character_model(image, draw, data, character_box, fonts, palette)

        gear = self._equipment_by_slot(data.get("equipment") or [])
        left_slots = [
            ("Head", "Head"),
            ("Neck", "Neck"),
            ("Shoulder", "Shoulder"),
            ("Chest", "Chest"),
            ("Hands", "Hands"),
            ("Legs", "Legs"),
            ("Feet", "Feet"),
        ]
        right_slots = [
            ("Weapon", "Weapon"),
            ("Off Hand", "Off Hand"),
            ("Finger", "Ring 1"),
            ("Finger 2", "Ring 2"),
            ("Trinket", "Trinket 1"),
            ("Trinket 2", "Trinket 2"),
            ("Back", "Back"),
        ]
        slot_y = y0 + 48
        slot_h = 36
        slot_gap = 7
        for index, (slot, label) in enumerate(left_slots):
            item = self._find_equipment_item(gear, slot)
            self._draw_gear_slot_card(draw, (x0 + 14, slot_y + index * (slot_h + slot_gap), x0 + 116, slot_y + index * (slot_h + slot_gap) + slot_h), label, item, fonts, palette)
        for index, (slot, label) in enumerate(right_slots):
            item = self._find_equipment_item(gear, slot)
            self._draw_gear_slot_card(draw, (x1 - 116, slot_y + index * (slot_h + slot_gap), x1 - 14, slot_y + index * (slot_h + slot_gap) + slot_h), label, item, fonts, palette)

        footer = (x0 + 118, y1 - 76, x1 - 18, y1 - 16)
        draw.rounded_rectangle(footer, radius=5, fill=(31, 29, 25), outline=(86, 72, 50), width=1)
        self._single(draw, (footer[0] + 12, footer[1] + 9), data.get("title"), fonts["title"], title_color, footer[2] - footer[0] - 24, 11)
        meta = f"Level {data.get('level')} / {data.get('spec')} {data.get('class')}"
        self._single(draw, (footer[0] + 12, footer[1] + 36), meta, fonts["small"], muted, footer[2] - footer[0] - 24, 8)
        self._pill(draw, x0 + 18, y1 - 64, 86, 28, data.get("faction") or "-", fonts["tiny"], palette["red"], palette["light"])
        self._pill(draw, x0 + 18, y1 - 31, 86, 24, data.get("region") or "-", fonts["tiny"], palette["blue"], palette["light"])

    def _draw_metrics(self, draw, data, box, fonts, palette):
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 16, y0 + 12), "Profile Snapshot", fonts["section"], palette["ink"])
        metrics = [
            ("Level", data.get("level"), palette["red"]),
            ("Item Level", data.get("equipped_item_level") or data.get("average_item_level"), palette["gold"]),
            ("Achievements", self._compact_number(data.get("achievement_points")), palette["blue"]),
            ("Mythic+", self._rating(data.get("mythic_rating")), palette["green"]),
        ]
        cell_w = (x1 - x0 - 46) // 4
        y = y0 + 46
        for index, (label, value, color) in enumerate(metrics):
            cx = x0 + 16 + index * (cell_w + 5)
            draw.rounded_rectangle((cx, y, cx + cell_w, y + 76), radius=5, fill=(246, 238, 211), outline=(134, 111, 76), width=1)
            self._center_single(draw, (cx + 6, y + 9, cx + cell_w - 6, y + 37), str(value or "-"), fonts["title"], color, 10)
            self._center_single(draw, (cx + 6, y + 48, cx + cell_w - 6, y + 67), label, fonts["tiny"], palette["muted"], 8)

    def _draw_equipment(self, draw, data, box, fonts, palette):
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 16, y0 + 10), "Top Gear", fonts["section"], palette["ink"])
        rows = data.get("equipment") or []
        if not rows:
            self._wrapped(draw, (x0 + 18, y0 + 46), "Equipment endpoint did not return public items.", fonts["body"], palette["muted"], x1 - x0 - 36)
            return
        card_gap = 8
        card_w = (x1 - x0 - 32 - card_gap * 2) // 3
        card_h = 51
        start_x = x0 + 16
        start_y = y0 + 43
        for index, item in enumerate(rows[:6]):
            col = index % 3
            row = index // 3
            cx = start_x + col * (card_w + card_gap)
            cy = start_y + row * (card_h + 8)
            if cy + card_h > y1 - 8:
                break
            quality = self._quality_color(item.get("quality"), palette)
            draw.rounded_rectangle((cx, cy, cx + card_w, cy + card_h), radius=4, fill=(245, 236, 207), outline=(130, 111, 82), width=1)
            draw.rounded_rectangle((cx + 7, cy + 8, cx + 31, cy + 32), radius=3, fill=quality, outline=(86, 71, 52), width=1)
            self._center_single(draw, (cx + 8, cy + 8, cx + 30, cy + 31), self._slot_initial(item.get("slot")), fonts["micro"], (250, 247, 235), 7)
            self._single(draw, (cx + 38, cy + 7), item.get("slot") or "-", fonts["tiny"], palette["muted"], card_w - 45, 8)
            self._single(draw, (cx + 38, cy + 23), item.get("name") or "-", fonts["tiny"], palette["ink"], card_w - 50, 7)
            self._single(draw, (cx + card_w - 34, cy + 7), str(item.get("level") or "-"), fonts["tiny"], palette["red"], 28, 7)

    def _draw_activity(self, draw, data, box, fonts, palette):
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 16, y0 + 10), "Activity", fonts["section"], palette["light"])
        muted = (188, 177, 151)
        self._single(draw, (x1 - 160, y0 + 15), f"Updated {data.get('updated_at', '-')}", fonts["tiny"], muted, 142, 8)
        col_gap = 8
        col_w = (x1 - x0 - 32 - col_gap * 2) // 3
        col_y0 = y0 + 40
        col_y1 = y1 - 10
        runs = data.get("mythic_best") or []
        pvp = data.get("pvp_bracket") or {}
        columns = [
            ("Mythic+", self._activity_mythic_lines(runs)),
            ("PVP", self._activity_pvp_lines(pvp)),
            ("Account", self._activity_account_lines(data)),
        ]
        for index, (title, lines) in enumerate(columns):
            cx = x0 + 16 + index * (col_w + col_gap)
            draw.rounded_rectangle((cx, col_y0, cx + col_w, col_y1), radius=4, fill=(35, 32, 28), outline=(74, 62, 47), width=1)
            self._text(draw, (cx + 9, col_y0 + 7), title, fonts["tiny"], palette["gold"])
            y = col_y0 + 25
            for line in lines[:3]:
                if y + self._line_height(draw, fonts["tiny"]) > col_y1 - 5:
                    break
                self._single(draw, (cx + 9, y), line, fonts["tiny"], palette["light"], col_w - 18, 7)
                y += 17

    def _draw_character_model(self, image, draw, data, box, fonts, palette):
        x0, y0, x1, y1 = box
        model = self._remote_image(data.get("media_url"), (x1 - x0, y1 - y0))
        if model:
            image.paste(model, (x0, y0))
            draw.rounded_rectangle((x0, y0, x1, y1), radius=6, outline=(119, 96, 56), width=1)
            return

        cx = (x0 + x1) // 2
        head_r = 24
        head_y = y0 + 38
        cape = [(cx, y0 + 74), (x0 + 15, y1 - 46), (cx, y1 - 18), (x1 - 15, y1 - 46)]
        draw.polygon(cape, fill=(53, 48, 42), outline=(111, 84, 45))
        draw.ellipse((cx - head_r, head_y - head_r, cx + head_r, head_y + head_r), fill=(108, 92, 65), outline=palette["gold"], width=2)
        draw.polygon([(cx - 42, y0 + 75), (cx + 42, y0 + 75), (cx + 31, y0 + 148), (cx - 31, y0 + 148)], fill=(45, 85, 105), outline=palette["gold"])
        draw.rounded_rectangle((cx - 29, y0 + 145, cx + 29, y0 + 224), radius=7, fill=(69, 62, 51), outline=(133, 102, 54), width=2)
        draw.line((cx - 43, y0 + 96, cx - 68, y0 + 174), fill=(169, 131, 57), width=8)
        draw.line((cx + 43, y0 + 96, cx + 68, y0 + 174), fill=(169, 131, 57), width=8)
        draw.line((cx - 18, y0 + 222, cx - 38, y1 - 24), fill=(76, 65, 50), width=13)
        draw.line((cx + 18, y0 + 222, cx + 38, y1 - 24), fill=(76, 65, 50), width=13)
        draw.line((cx - 30, y1 - 24, cx - 58, y1 - 24), fill=palette["gold"], width=5)
        draw.line((cx + 30, y1 - 24, cx + 58, y1 - 24), fill=palette["gold"], width=5)
        self._center_text(draw, (cx, y0 + 145), "80", fonts["section"], palette["light"])

    def _draw_gear_slot_card(self, draw, box, label, item, fonts, palette):
        x0, y0, x1, y1 = box
        quality = self._quality_color((item or {}).get("quality"), palette) if item else (72, 65, 54)
        fill = (41, 38, 33) if item else (31, 29, 26)
        outline = quality if item else (78, 68, 52)
        draw.rounded_rectangle(box, radius=4, fill=fill, outline=outline, width=1)
        draw.rounded_rectangle((x0 + 5, y0 + 6, x0 + 28, y1 - 6), radius=3, fill=quality, outline=(24, 22, 19), width=1)
        self._center_single(draw, (x0 + 7, y0 + 7, x0 + 27, y1 - 7), self._slot_initial(label), fonts["micro"], (248, 241, 218), 7)
        if item:
            self._single(draw, (x0 + 34, y0 + 4), label, fonts["micro"], (195, 181, 148), x1 - x0 - 62, 7)
            self._single(draw, (x1 - 25, y0 + 4), str(item.get("level") or "-"), fonts["micro"], palette["gold"], 22, 7)
            self._single(draw, (x0 + 34, y0 + 18), item.get("name") or "-", fonts["micro"], palette["light"], x1 - x0 - 40, 7)
        else:
            self._single(draw, (x0 + 34, y0 + 6), label, fonts["micro"], (135, 124, 103), x1 - x0 - 40, 7)
            self._single(draw, (x0 + 34, y0 + 20), "Empty", fonts["micro"], (101, 92, 77), x1 - x0 - 40, 7)

    def _equipment_by_slot(self, equipment):
        slots = {}
        for item in equipment:
            key = self._equipment_slot_key(item.get("slot"))
            if not key:
                continue
            slots.setdefault(key, []).append(item)
        return slots

    def _find_equipment_item(self, slots, slot):
        slot = str(slot or "").strip()
        key = self._equipment_slot_key(slot)
        if not key:
            return None
        index = 1 if slot.endswith(" 2") else 0
        items = slots.get(key) or []
        if index < len(items):
            return items[index]
        return items[0] if items else None

    def _equipment_slot_key(self, slot):
        text = str(slot or "").strip().lower()
        if not text:
            return ""
        text = text.replace("_", " ").replace("-", " ")
        text = re.sub(r"\s+", " ", text)
        aliases = [
            ("main hand", "weapon"),
            ("weapon", "weapon"),
            ("off hand", "off hand"),
            ("head", "head"),
            ("neck", "neck"),
            ("shoulder", "shoulder"),
            ("back", "back"),
            ("chest", "chest"),
            ("wrist", "wrist"),
            ("hands", "hands"),
            ("hand", "hands"),
            ("waist", "waist"),
            ("legs", "legs"),
            ("leg", "legs"),
            ("feet", "feet"),
            ("foot", "feet"),
            ("finger", "finger"),
            ("ring", "finger"),
            ("trinket", "trinket"),
        ]
        for needle, key in aliases:
            if needle in text:
                return key
        return text

    def _draw_account_screen(self, image, draw, data, fonts, palette):
        width, height = image.size
        box = (28, 28, width - 28, height - 28)
        self._panel(draw, box, palette["panel"], palette["gold"], radius=7)
        x0, y0, x1, y1 = box
        self._text(draw, (x0 + 24, y0 + 20), "WoW Account", fonts["hero"], palette["ink"])
        self._text(draw, (x0 + 25, y0 + 60), data.get("subtitle") or "Account OAuth profile", fonts["small"], palette["muted"])
        rows = data.get("account_characters") or []
        y = y0 + 105
        for index, character in enumerate(rows[:7], start=1):
            realm = (character.get("realm") or {}).get("name") or (character.get("realm") or {}).get("slug") or "-"
            line = f"{index}. {character.get('name')} / {realm}"
            self._single(draw, (x0 + 30, y), line, fonts["section"], palette["ink"], 440, 10)
            meta = f"Level {character.get('level')}  {character.get('race')} {character.get('class')}  {character.get('faction')}"
            self._single(draw, (x0 + 500, y + 2), meta, fonts["small"], palette["muted"], x1 - x0 - 530, 8)
            y += 43
        if not rows:
            self._wrapped(draw, (x0 + 30, y), data.get("notice") or "No characters returned.", fonts["body"], palette["muted"], x1 - x0 - 60)
        self._single(draw, (x0 + 24, y1 - 30), f"{data.get('source')} / Updated {data.get('updated_at', '-')}", fonts["tiny"], palette["muted"], x1 - x0 - 48, 8)

    def _draw_status_screen(self, image, draw, data, fonts, palette):
        width, height = image.size
        x0, y0, x1, y1 = (42, 42, width - 42, height - 42)
        self._panel(draw, (x0, y0, x1, y1), palette["panel"], palette["gold"], radius=8)
        self._draw_crest(draw, (x0 + 28, y0 + 36, x0 + 188, y0 + 196), palette)
        self._center_text(draw, (x0 + 108, y0 + 115), "WoW", fonts["section"], palette["light"])
        self._text(draw, (x0 + 220, y0 + 48), "WoW Profile Dashboard", fonts["hero"], palette["ink"])
        self._text(draw, (x0 + 222, y0 + 90), data.get("title") or "Setup required", fonts["section"], palette["red"])
        self._wrapped(draw, (x0 + 222, y0 + 126), data.get("message") or "", fonts["body"], palette["muted"], x1 - x0 - 260, 5)
        y = y0 + 220
        tips = [
            "US default: region=us, namespace=profile-us, locale=en_US.",
            "Public mode: set realm slug and character name.",
            "Keys: BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET in API Keys.",
            "Account mode needs user OAuth token with wow.profile scope.",
        ]
        for tip in tips:
            self._single(draw, (x0 + 36, y), tip, fonts["small"], palette["ink"], x1 - x0 - 72, 8)
            y += 28
        self._single(draw, (x0 + 36, y1 - 28), f"Source: {data.get('source', 'setup')} / Updated {data.get('updated_at', '-')}", fonts["tiny"], palette["muted"], x1 - x0 - 72, 8)

    def _status_payload(self, title, message, *, settings=None, source="setup"):
        settings = settings or {}
        region = self._region(settings)
        return {
            "kind": "status",
            "status": "setup",
            "title": title,
            "message": message,
            "region": REGION_CONFIG[region]["label"],
            "locale": self._locale(settings, region),
            "source": source,
        }

    def _sample_payload(self):
        return {
            "kind": "character",
            "mode": "mock",
            "status": "ok",
            "title": "Azerothia",
            "subtitle": "Area 52 / US",
            "region": "US",
            "locale": "en_US",
            "source": "mock Battle.net profile",
            "level": 80,
            "race": "Void Elf",
            "class": "Mage",
            "spec": "Frost",
            "faction": "Alliance",
            "achievement_points": 18420,
            "average_item_level": 641,
            "equipped_item_level": 639,
            "last_login": "2026-06-04",
            "portrait_url": "",
            "equipment": [
                {"slot": "Head", "name": "Crown of the Violet Citadel", "level": 645, "quality": "EPIC"},
                {"slot": "Neck", "name": "Pendant of Ley Sparks", "level": 638, "quality": "RARE"},
                {"slot": "Shoulder", "name": "Mantle of Cold Stars", "level": 641, "quality": "EPIC"},
                {"slot": "Chest", "name": "Robes of Rewound Time", "level": 642, "quality": "EPIC"},
                {"slot": "Hands", "name": "Gloves of Arcane Frost", "level": 639, "quality": "EPIC"},
                {"slot": "Legs", "name": "Trousers of the Rift", "level": 640, "quality": "EPIC"},
                {"slot": "Feet", "name": "Boots of the Ley Line", "level": 637, "quality": "RARE"},
                {"slot": "Weapon", "name": "Staff of Falling Stars", "level": 649, "quality": "EPIC"},
                {"slot": "Off Hand", "name": "Chronicle of Ice", "level": 635, "quality": "RARE"},
                {"slot": "Finger", "name": "Band of the Kirin Tor", "level": 636, "quality": "RARE"},
                {"slot": "Finger", "name": "Seal of Frozen Time", "level": 634, "quality": "RARE"},
                {"slot": "Trinket", "name": "Runed Hourglass", "level": 636, "quality": "RARE"},
                {"slot": "Trinket", "name": "Starfire Lens", "level": 642, "quality": "EPIC"},
                {"slot": "Back", "name": "Cloak of the Violet Eye", "level": 640, "quality": "EPIC"},
            ],
            "mythic_rating": 2476,
            "mythic_best": [
                {"dungeon": "The Dawnbreaker", "level": 10, "score": 332.1},
                {"dungeon": "Ara-Kara", "level": 9, "score": 318.6},
                {"dungeon": "Grim Batol", "level": 9, "score": 304.2},
            ],
            "pvp_bracket": {"type": "ARENA_3v3", "rating": 1602},
            "notice": "",
        }

    def _draw_background(self, draw, width, height, palette):
        draw.rectangle((0, 0, width, height), fill=palette["bg"])
        for x in range(-80, width + 80, 90):
            draw.line((x, 0, x + 180, height), fill=palette["pattern"], width=2)
        for y in range(28, height, 64):
            draw.line((0, y, width, y - 34), fill=(26, 27, 31), width=1)
        draw.rectangle((0, 0, width - 1, height - 1), outline=(87, 68, 37), width=4)

    def _draw_crest(self, draw, box, palette):
        x0, y0, x1, y1 = box
        cx = (x0 + x1) // 2
        draw.polygon([(cx, y0), (x1, y0 + 50), (x1 - 30, y1), (x0 + 30, y1), (x0, y0 + 50)], fill=(70, 55, 37), outline=palette["gold"])
        draw.polygon([(cx, y0 + 16), (x1 - 20, y0 + 60), (x1 - 44, y1 - 18), (x0 + 44, y1 - 18), (x0 + 20, y0 + 60)], fill=(36, 75, 102), outline=(229, 184, 76))
        draw.arc((x0 + 44, y0 + 46, x1 - 44, y1 - 34), 210, 510, fill=palette["gold"], width=5)
        draw.line((cx, y0 + 42, cx, y1 - 24), fill=palette["gold"], width=3)

    def _panel(self, draw, box, fill, outline, radius=5):
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2)

    def _pill(self, draw, x, y, width, height, text, font, fill, ink):
        draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=fill, outline=(238, 208, 129), width=1)
        self._center_single(draw, (x + 8, y + 6, x + width - 8, y + height - 4), text, font, ink, 8)

    def _label_value(self, draw, x, y, label, value, fonts, label_color, value_color, max_width):
        self._text(draw, (x, y), label.upper(), fonts["tiny"], label_color)
        self._single(draw, (x + 56, y - 3), value, fonts["body"], value_color, max_width - 56, 8)

    def _remote_image(self, url, size):
        url = str(url or "").strip()
        if not url:
            return None
        try:
            response = get_http_session().get(url, timeout=20)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content))
            return ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"), size, method=Image.Resampling.LANCZOS)
        except Exception as exc:
            logger.info("Could not load WoW media image: %s", exc)
            return None

    def _text(self, draw, position, text, font, fill):
        draw.text(position, str(text or ""), font=font, fill=fill)

    def _single(self, draw, position, text, font, fill, max_width, min_size=8):
        text = str(text or "")
        fitted = self._fit_font(draw, text, font, max_width, min_size)
        draw.text(position, text, font=fitted, fill=fill)

    def _center_single(self, draw, box, text, font, fill, min_size=8):
        x0, y0, x1, y1 = box
        text = str(text or "")
        fitted = self._fit_font(draw, text, font, x1 - x0, min_size)
        bbox = draw.textbbox((0, 0), text, font=fitted)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((x0 + (x1 - x0 - tw) / 2, y0 + (y1 - y0 - th) / 2 - 1), text, font=fitted, fill=fill)

    def _center_text(self, draw, center, text, font, fill):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        draw.text((center[0] - (bbox[2] - bbox[0]) / 2, center[1] - (bbox[3] - bbox[1]) / 2), str(text), font=font, fill=fill)

    def _wrapped(self, draw, position, text, font, fill, max_width, line_gap=3):
        x, y = position
        line_h = self._line_height(draw, font)
        for line in self._wrap_text(draw, str(text or ""), font, max_width):
            draw.text((x, y), line, font=font, fill=fill)
            y += line_h + line_gap
        return y

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
        words = text.split(" ")
        if len(words) > 1:
            lines = []
            current = ""
            for word in words:
                candidate = word if not current else f"{current} {word}"
                if current and self._text_width(draw, candidate, font) > max_width:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                lines.append(current)
            return lines
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
        return text_width(draw, str(text or ""), font)

    def _line_height(self, draw, font):
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        return max(1, bbox[3] - bbox[1] + 3)

    def _font(self, size, bold=False):
        plugin_dir = Path(self.get_plugin_dir())
        candidates = []
        shared_fonts = plugin_dir.parent / "sports_dashboard" / "fonts"
        if bold:
            candidates.extend([
                plugin_dir / "fonts" / "msyhbd.ttc",
                shared_fonts / "msyhbd.ttc",
                Path("C:/Windows/Fonts/msyhbd.ttc"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            ])
        candidates.extend([
            plugin_dir / "fonts" / "msyh.ttc",
            shared_fonts / "msyh.ttc",
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ])
        for path in candidates:
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size=size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def _region(self, settings):
        region = str(settings.get("region") or DEFAULT_REGION).strip().lower()
        return region if region in REGION_CONFIG else DEFAULT_REGION

    def _locale(self, settings, region):
        locale = str(settings.get("locale") or "").strip()
        return locale or REGION_CONFIG[region]["locale"] or DEFAULT_LOCALE

    def _display_dimensions(self, device_config):
        return self.get_dimensions(device_config)

    def _cache_key(self, settings, dimensions):
        relevant = {
            "region": self._region(settings),
            "realm": self._slug(settings.get("realmSlug") or settings.get("realm") or ""),
            "character": self._character_slug(settings.get("characterName") or settings.get("character") or ""),
            "locale": settings.get("locale") or "",
            "mock": self._enabled(settings.get("useMockData"), default=False),
            "dimensions": dimensions,
        }
        raw = json.dumps(relevant, sort_keys=True, ensure_ascii=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:18]

    def _cache_dir(self):
        return self.cache_dir(leaf=".wow_profile_dashboard_cache", create=True)

    def _cache_path(self, cache_key):
        return self._cache_dir() / f"{cache_key}.json"

    def _cache_image_path(self, cache_key):
        return self._cache_dir() / f"image_{cache_key}.png"

    def _write_context(self, data, generated_ts, refresh_minutes):
        payload = self._json_safe(data)
        try:
            write_context(PLUGIN_ID, payload, generated_at=datetime.fromtimestamp(float(generated_ts)), ttl_seconds=max(60, refresh_minutes * 60))
        except Exception as exc:
            logger.debug("Could not write WoW context cache: %s", exc)

    def _device_key(self, device_config, name):
        if device_config is not None and hasattr(device_config, "load_env_key"):
            value = str(device_config.load_env_key(name) or "").strip()
            if value:
                return value
        return str(os.environ.get(name) or "").strip()

    def _slug(self, value):
        value = str(value or "").strip().lower().replace("'", "")
        value = re.sub(r"[^a-z0-9]+", "-", value)
        return value.strip("-")

    def _character_slug(self, value):
        return self._slug(value)

    def _enabled(self, value, default=False):
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _bounded_int(self, value, default, minimum, maximum):
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _numeric(self, value):
        try:
            return float(value)
        except Exception:
            return 0.0

    def _rating(self, value):
        number = self._numeric(value)
        return "-" if number <= 0 else f"{number:,.0f}"

    def _quality_color(self, quality, palette):
        quality = str(quality or "").upper()
        return {
            "POOR": (122, 122, 122),
            "COMMON": (232, 232, 232),
            "UNCOMMON": (84, 172, 65),
            "RARE": (55, 127, 214),
            "EPIC": (147, 89, 201),
            "LEGENDARY": (214, 128, 45),
        }.get(quality, palette["blue"])

    def _slot_initial(self, slot):
        slot = str(slot or "").strip()
        if not slot:
            return "?"
        initials = {
            "Head": "H",
            "Neck": "N",
            "Shoulder": "S",
            "Back": "B",
            "Chest": "C",
            "Wrist": "W",
            "Hands": "G",
            "Waist": "W",
            "Legs": "L",
            "Feet": "F",
            "Finger": "R",
            "Trinket": "T",
            "Weapon": "W",
            "Off Hand": "O",
        }
        return initials.get(slot, slot[:1].upper())

    def _activity_mythic_lines(self, runs):
        if not runs:
            return ["No public runs", "Check profile", "later"]
        lines = []
        for run in runs:
            level = run.get("level") or "-"
            dungeon = run.get("dungeon") or "-"
            score = self._rating(run.get("score"))
            lines.append(f"+{level} {dungeon} / {score}")
        return lines

    def _activity_pvp_lines(self, pvp):
        if not pvp:
            return ["No PVP summary", "publicly shown"]
        bracket = str(pvp.get("type") or "Bracket").replace("_", " ")
        rating = pvp.get("rating") or "-"
        stats = pvp.get("season_match_statistics") or {}
        played = stats.get("played") or stats.get("played_matches") or ""
        lines = [bracket, f"Rating {rating}"]
        if played:
            lines.append(f"Played {played}")
        return lines

    def _activity_account_lines(self, data):
        source = str(data.get("source") or "Battle.net")
        mode = str(data.get("mode") or "public")
        region = str(data.get("region") or "-")
        return [region, mode.replace("-", " "), source]

    def _compact_number(self, value):
        number = self._numeric(value)
        if number >= 1000000:
            return f"{number / 1000000:.1f}M"
        if number >= 10000:
            return f"{number / 1000:.1f}K"
        if number == int(number):
            return str(int(number))
        return f"{number:.1f}"

    def _format_timestamp(self, value):
        try:
            ts = int(value) / 1000
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            return "-"

    def _read_json(self, path, default):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, path, data):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _json_safe(self, data):
        try:
            return json.loads(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            return {"value": str(data)}
