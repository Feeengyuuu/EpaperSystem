from __future__ import annotations

import json
import logging
import os
import re
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request
from PIL import Image, ImageDraw, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from refresh_task import PlaylistRefresh
from utils.app_utils import get_font

try:
    import pytz
except ImportError:
    pytz = None
    from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = 1
DEFAULT_PET_NAME = "Mochi"
DEFAULT_TICK_MINUTES = 15
DEFAULT_AI_TEXT_MODEL = "gpt-4o-mini"
DEFAULT_GROQ_TEXT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_AI_DAILY_LIMIT = 24
DEFAULT_CONTEXT_MAX_ITEMS = 8
MAX_OFFLINE_TICKS = 96

FACE_MAP = {
    "happy": ("(=^_^=)", "Happy"),
    "calm": ("(=^-^=)", "Calm"),
    "curious": ("(=?_?=)", "Curious"),
    "playful": ("(=^o^=)", "Play"),
    "alert": ("(=O_O=)", "Alert"),
    "grooming": ("(=~_~=)", "Groom"),
    "exploring": ("(=o.o=)", "Roam"),
    "hungry": ("(=T_T=)", "Hungry"),
    "dirty": ("(=x_x=)", "Messy"),
    "sick": ("(=X_X=)", "Unwell"),
    "tired": ("(=-_-=)", "Tired"),
    "sleeping": ("(-_-) zZ", "Sleeping"),
    "lonely": ("(=o_o=)", "Waiting"),
    "bored": ("(=._.=)", "Bored"),
    "working": ("[=^_^=]", "Thinking"),
    "selfcare": ("(=o_o=)+", "Self-care"),
}

BASE_EVENTS = [
    {
        "id": "blink",
        "mood": "calm",
        "activity": "slow blink",
        "message": "Blinked once and saved a whole refresh.",
        "delta": {"happiness": 1},
    },
    {
        "id": "pixel_patrol",
        "mood": "exploring",
        "activity": "pixel patrol",
        "message": "Patrolled the border and found no stuck pixels.",
        "delta": {"energy": -1, "happiness": 2, "xp": 2},
    },
    {
        "id": "listen",
        "mood": "curious",
        "activity": "listening",
        "message": "Listened to the room hum for a while.",
        "delta": {"happiness": 1, "xp": 1},
    },
    {
        "id": "groom",
        "mood": "grooming",
        "activity": "grooming",
        "message": "Combed the pixel fur into tidy rows.",
        "delta": {"cleanliness": 3, "energy": -1, "xp": 2},
    },
    {
        "id": "stretch",
        "mood": "happy",
        "activity": "stretching",
        "message": "Did a tiny stretch without waking the display.",
        "delta": {"energy": 1, "happiness": 2},
    },
    {
        "id": "screen_edge",
        "mood": "alert",
        "activity": "edge watch",
        "message": "Watched the screen edge like it might move.",
        "delta": {"energy": -1, "xp": 1},
    },
    {
        "id": "sort_thoughts",
        "mood": "working",
        "activity": "sorting thoughts",
        "message": "Sorted small thoughts into quieter folders.",
        "delta": {"energy": -1, "xp": 3},
    },
    {
        "id": "imaginary_game",
        "mood": "playful",
        "activity": "solo game",
        "message": "Played a one-pet game with imaginary crumbs.",
        "delta": {"happiness": 4, "energy": -2, "food": -1, "xp": 2},
    },
]

EXPRESSIVE_EVENTS = [
    {
        "id": "face_practice",
        "mood": "playful",
        "activity": "face practice",
        "message": "Practiced a new face, then pretended it was normal.",
        "delta": {"happiness": 3, "xp": 2},
    },
    {
        "id": "refresh_report",
        "mood": "working",
        "activity": "world sniffing",
        "message": "Sniffed today's headlines and found the world still overacting.",
        "delta": {"energy": -1, "xp": 4},
    },
    {
        "id": "window_watch",
        "mood": "curious",
        "activity": "window watch",
        "message": "Looked for weather through the glass and guessed wrong.",
        "delta": {"happiness": 2, "xp": 2},
    },
    {
        "id": "micro_dance",
        "mood": "playful",
        "activity": "micro dance",
        "message": "Did a two-pixel dance. Very efficient.",
        "delta": {"happiness": 5, "energy": -3, "food": -1, "xp": 3},
    },
]

QUIET_EVENTS = [
    {
        "id": "quiet_watch",
        "mood": "calm",
        "activity": "quiet watch",
        "message": "Kept watch quietly between refreshes.",
        "delta": {"happiness": 1},
    },
    {
        "id": "nap_ready",
        "mood": "tired",
        "activity": "resting",
        "message": "Settled into a low-power pose.",
        "delta": {"energy": 2},
    },
]

CARE_PROFILES = {
    "gentle": {"food": 1, "happiness": 1, "energy": 1, "cleanliness": 1},
    "normal": {"food": 2, "happiness": 1, "energy": 1, "cleanliness": 1},
    "needy": {"food": 3, "happiness": 2, "energy": 1, "cleanliness": 2},
}

ZH_ALIASES = {"zh", "zh-cn", "zh_cn", "zh-hans", "schinese", "simplified_chinese", "cn"}

LOCALIZED_TEXT = {
    "zh-Hans": {
        "ui": {
            "face": "面部",
            "vitals": "状态",
            "auto": "自主",
            "activity": "活动",
            "log": "记录",
            "last": "上次",
            "level": "等级",
            "age": "年龄",
            "day": "天",
        },
        "moods": {
            "happy": "开心",
            "calm": "平静",
            "curious": "好奇",
            "playful": "玩耍",
            "alert": "警觉",
            "grooming": "梳毛",
            "exploring": "巡逻",
            "hungry": "饿了",
            "dirty": "待清理",
            "sick": "不舒服",
            "tired": "累了",
            "sleeping": "睡觉",
            "lonely": "等待",
            "bored": "无聊",
            "working": "思考",
            "selfcare": "自理",
        },
        "stats": {
            "food": "食物",
            "happiness": "心情",
            "energy": "能量",
            "cleanliness": "清洁",
            "health": "健康",
        },
        "activity": {
            "waking up": "刚醒来",
            "quiet watch": "安静值守",
            "slow blink": "慢慢眨眼",
            "pixel patrol": "像素巡逻",
            "listening": "听环境声",
            "grooming": "整理毛毛",
            "stretching": "伸懒腰",
            "edge watch": "边缘观察",
            "sorting thoughts": "整理想法",
            "solo game": "独自玩耍",
            "face practice": "练习表情",
            "refresh report": "偷听世界",
            "world sniffing": "偷听世界",
            "window watch": "观察窗外",
            "micro dance": "微型跳舞",
            "resting": "休息",
            "needs care": "需要照顾",
            "snacking": "吃点心",
            "playing": "安静玩耍",
            "cleaning": "清理屏幕",
            "sleeping": "睡觉",
            "waking": "醒来",
            "foraging": "找零食",
            "tidying": "整理窝",
            "napping": "低功耗小睡",
            "self-soothing": "自我安抚",
            "wandering": "慢慢闲逛",
            "morning boot": "早晨启动",
            "sun guessing": "猜太阳",
            "night watch": "夜间值守",
            "dream cache": "缓存梦境",
        },
        "message": {
            "Hello. I am awake on e-paper.": "你好，我在墨水屏上醒来了。",
            "Quiet heartbeat.": "安静心跳。",
            "Blinked once and saved a whole refresh.": "慢慢眨了一次眼，省下一整次刷新。",
            "Patrolled the border and found no stuck pixels.": "沿着屏幕边缘巡逻，没有发现坏点。",
            "Listened to the room hum for a while.": "听了一会儿房间里的轻微嗡鸣。",
            "Combed the pixel fur into tidy rows.": "把像素毛毛梳成了整齐的小行。",
            "Did a tiny stretch without waking the display.": "轻轻伸了个懒腰，没有吵醒屏幕。",
            "Watched the screen edge like it might move.": "盯着屏幕边缘，好像它下一秒会动。",
            "Sorted small thoughts into quieter folders.": "把小想法整理进更安静的文件夹。",
            "Played a one-pet game with imaginary crumbs.": "用想象中的碎屑玩了一局单宠游戏。",
            "Practiced a new face, then pretended it was normal.": "练了一个新表情，然后假装很平常。",
            "Filed a tiny report about today's refresh budget.": "偷听了一圈世界的动静，决定先眨眨眼。",
            "Sniffed today's headlines and found the world still overacting.": "偷听了一圈世界的动静，决定先眨眨眼。",
            "Looked for weather through the glass and guessed wrong.": "隔着玻璃猜天气，猜得很认真但不太准。",
            "Did a two-pixel dance. Very efficient.": "跳了一段两像素舞，非常省电。",
            "Kept watch quietly between refreshes.": "在两次刷新之间安静值守。",
            "Settled into a low-power pose.": "摆好了低功耗休息姿势。",
            "Snack accepted. Tiny crunch detected.": "接受了点心，检测到轻轻咔嚓声。",
            "Played a quiet no-refresh game.": "玩了一局安静的无刷新游戏。",
            "Screen fur restored to clean pixels.": "屏幕毛毛恢复成干净像素。",
            "Entering low-power nap mode.": "进入低功耗小睡模式。",
            "Awake. Blinking slowly.": "醒来了，正在慢慢眨眼。",
            "Autonomy: found an emergency snack.": "自主照顾：找到了一份应急小零食。",
            "Autonomy: tidied the pixel nest.": "自主照顾：整理了像素小窝。",
            "Autonomy: entering low-power nap.": "自主照顾：进入低功耗小睡。",
            "Autonomy: watched the quiet pixels.": "自主照顾：看了一会儿安静的像素。",
            "Heartbeat: sleeping through the slow refresh.": "心跳：正在慢刷新中睡觉。",
            "Heartbeat: health is low. Needs care.": "心跳：健康偏低，需要照顾。",
            "Heartbeat: food is low.": "心跳：食物偏低。",
            "Heartbeat: wants a clean screen.": "心跳：想要干净屏幕。",
            "Heartbeat: waiting for attention.": "心跳：正在等你注意它。",
            "Moved quietly between refreshes.": "在刷新之间安静地挪了挪。",
            "Ran a morning boot check and twitched both ears.": "完成早晨启动检查，两只耳朵都轻轻动了一下。",
            "Guessed where the sun is without spending pixels.": "不花像素也猜了猜太阳在哪里。",
            "Kept a tiny night watch over the dark screen.": "在黑色屏幕上进行了一次小小夜间值守。",
            "Cached a small dream for the next refresh.": "为下一次刷新缓存了一个小梦。",
        },
    }
}


def _clamp(value: int | float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def _enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "on", "yes"}


def _parse_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _slug(value: str, fallback: str = "pet") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return slug or fallback


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))


def _safe_json_load(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read pet state %s: %s", path, exc)
    return default


def _safe_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=True, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        path.write_text(text, encoding="utf-8")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


class EpaperPet(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def get_blueprint(self):
        bp = Blueprint("epaper_pet", __name__, url_prefix="/epaper_pet")

        @bp.route("/action", methods=["POST"])
        def pet_action():
            data = request.get_json() or {}
            action = (data.get("action") or "").strip().lower()
            instance_name = (data.get("instance_name") or "").strip()
            if not instance_name:
                return jsonify({"error": "instance_name is required"}), 400
            if action not in {"feed", "play", "clean", "sleep", "wake", "status"}:
                return jsonify({"error": "Unsupported pet action"}), 400

            device_config = current_app.config["DEVICE_CONFIG"]
            playlist_manager = device_config.get_playlist_manager()
            found_playlist = None
            found_instance = None
            for playlist in playlist_manager.playlists:
                found_instance = playlist.find_plugin(self.get_plugin_id(), instance_name)
                if found_instance:
                    found_playlist = playlist
                    break

            if not found_playlist or not found_instance:
                return jsonify({"error": "Pet instance not found"}), 404

            now = self._now(device_config)
            state = self._load_state(found_instance.settings, now)
            elapsed_changed = self._apply_elapsed(state, found_instance.settings, now, device_config)
            if action != "status":
                self._apply_action(state, action, now, found_instance.settings, device_config)
                self._finalize_state(state, found_instance.settings, now)
                self._save_state(found_instance.settings, state)

                refresh_task = current_app.config.get("REFRESH_TASK")
                if refresh_task:
                    refresh_task.manual_update(PlaylistRefresh(found_playlist, found_instance, force=True))
            else:
                self._finalize_state(state, found_instance.settings, now)
                if elapsed_changed:
                    self._save_state(found_instance.settings, state)

            return jsonify(self._state_summary(state, found_instance.settings))

        return bp

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        now = self._now(device_config)
        state = self._load_state(settings, now)
        changed = self._apply_elapsed(state, settings, now, device_config)
        if not changed and self._needs_initial_event(state):
            self._apply_autonomous_event(state, settings, now, steps=0, device_config=device_config)
            changed = True
        elif not changed and _enabled(settings.get("ai_dialogue"), False) and _enabled(settings.get("ai_each_render"), True):
            changed = self._maybe_generate_ai_message(state, settings, now, device_config)

        self._finalize_state(state, settings, now)
        if changed:
            self._save_state(settings, state)

        return self._render(dimensions, settings, state, now)

    def _language(self, settings) -> str:
        raw = str((settings or {}).get("language") or "en").strip()
        if raw.lower() in ZH_ALIASES:
            return "zh-Hans"
        return "en"

    def _is_chinese(self, settings) -> bool:
        return self._language(settings) == "zh-Hans"

    def _localized(self, settings, section: str, key: str, fallback: str | None = None) -> str:
        language = self._language(settings)
        value = LOCALIZED_TEXT.get(language, {}).get(section, {}).get(str(key))
        return value if value is not None else (fallback if fallback is not None else str(key))

    def _ui(self, settings, key: str, fallback: str) -> str:
        return self._localized(settings, "ui", key, fallback)

    def _mood_label(self, mood: str, settings) -> str:
        fallback = FACE_MAP.get(mood, FACE_MAP["calm"])[1]
        return self._localized(settings, "moods", mood, fallback)

    def _activity_text(self, settings, activity: str) -> str:
        return self._localized(settings, "activity", activity, activity)

    def _message_text(self, settings, message: str) -> str:
        return self._localized(settings, "message", message, message)

    def _badge_text(self, settings, text: str) -> str:
        return text if self._is_chinese(settings) else str(text).upper()

    def _text_family(self, settings) -> str:
        return "LXGW WenKai" if self._is_chinese(settings) else "Jost"

    def _now(self, device_config) -> datetime:
        tz_name = device_config.get_config("timezone") or "UTC"
        try:
            if pytz:
                return datetime.now(pytz.timezone(tz_name))
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            if pytz:
                return datetime.now(pytz.UTC)
            return datetime.now(ZoneInfo("UTC"))

    def _cache_dir(self) -> Path:
        path = Path(self.get_plugin_dir("cache")) / "pets"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _state_file(self, settings) -> Path:
        pet_id = _slug(settings.get("pet_id") or settings.get("pet_name") or DEFAULT_PET_NAME)
        return self._cache_dir() / f"{pet_id}.json"

    def _journal_file(self, settings) -> Path:
        pet_id = _slug(settings.get("pet_id") or settings.get("pet_name") or DEFAULT_PET_NAME)
        return self._cache_dir() / f"{pet_id}.journal.md"

    def _load_state(self, settings, now: datetime) -> dict[str, Any]:
        state = _safe_json_load(self._state_file(settings), {})
        pet_name = (settings.get("pet_name") or DEFAULT_PET_NAME).strip() or DEFAULT_PET_NAME
        if not state:
            state = {
                "schema_version": STATE_SCHEMA_VERSION,
                "pet_id": _slug(settings.get("pet_id") or pet_name),
                "name": pet_name,
                "born_at": now.isoformat(),
                "last_tick_at": now.isoformat(),
                "last_event_at": now.isoformat(),
                "message": "Hello. I am awake on e-paper.",
                "activity": "waking up",
                "event_index": 0,
                "mood": "calm",
                "stats": {
                    "food": 78,
                    "happiness": 72,
                    "energy": 68,
                    "cleanliness": 82,
                    "health": 90,
                    "xp": 0,
                    "level": 1,
                    "age_days": 0,
                },
            }
        state["name"] = pet_name
        state.setdefault("stats", {})
        defaults = {
            "food": 70,
            "happiness": 70,
            "energy": 70,
            "cleanliness": 80,
            "health": 90,
            "xp": 0,
            "level": 1,
            "age_days": 0,
        }
        for key, value in defaults.items():
            state["stats"].setdefault(key, value)
        state.setdefault("message", "Quiet heartbeat.")
        state.setdefault("activity", "quiet watch")
        state.setdefault("event_index", 0)
        state.setdefault("mood", "calm")
        state.setdefault("born_at", now.isoformat())
        state.setdefault("last_tick_at", now.isoformat())
        return state

    def _save_state(self, settings, state: dict[str, Any]) -> None:
        _safe_json_write(self._state_file(settings), state)
        try:
            latest = f"- {state.get('last_event_at', '')}: {state.get('message', '')}\n"
            with self._journal_file(settings).open("a", encoding="utf-8") as handle:
                handle.write(latest)
        except Exception as exc:
            logger.warning("Could not append pet journal: %s", exc)

    def _apply_elapsed(self, state: dict[str, Any], settings, now: datetime, device_config=None) -> bool:
        last_tick = self._parse_time(state.get("last_tick_at"), now)
        tick_minutes = _parse_int(settings.get("tick_minutes"), DEFAULT_TICK_MINUTES, 5, 240)
        elapsed_minutes = max(0, int((now - last_tick).total_seconds() // 60))
        steps = min(MAX_OFFLINE_TICKS, elapsed_minutes // tick_minutes)
        if steps <= 0:
            return False

        stats = state["stats"]
        profile = CARE_PROFILES.get(settings.get("care_profile"), CARE_PROFILES["normal"])
        sleeping = self._is_sleeping(state, now)
        for _ in range(steps):
            stats["food"] = _clamp(stats["food"] - profile["food"])
            stats["happiness"] = _clamp(stats["happiness"] - profile["happiness"])
            stats["cleanliness"] = _clamp(stats["cleanliness"] - profile["cleanliness"])
            if sleeping:
                stats["energy"] = _clamp(stats["energy"] + 4)
                stats["happiness"] = _clamp(stats["happiness"] + 1)
            else:
                stats["energy"] = _clamp(stats["energy"] - profile["energy"])

            if stats["food"] < 15 or stats["cleanliness"] < 15:
                stats["health"] = _clamp(stats["health"] - 2)
            elif stats["food"] > 45 and stats["cleanliness"] > 45 and stats["energy"] > 35:
                stats["health"] = _clamp(stats["health"] + 1)
            stats["xp"] = int(stats["xp"]) + 1

        state["last_tick_at"] = (last_tick + timedelta(minutes=steps * tick_minutes)).isoformat()
        if not self._apply_autonomous_care(state, settings, now, device_config):
            care_message = self._care_message(state, sleeping)
            if care_message:
                state["activity"] = "needs care"
                state["message"] = care_message
                self._maybe_generate_ai_message(state, settings, now, device_config)
            else:
                self._apply_autonomous_event(state, settings, now, steps, device_config)
        state["last_event_at"] = now.isoformat()
        return True

    def _apply_action(self, state: dict[str, Any], action: str, now: datetime, settings=None, device_config=None) -> None:
        stats = state["stats"]
        if action == "feed":
            stats["food"] = _clamp(stats["food"] + 35)
            stats["happiness"] = _clamp(stats["happiness"] + 6)
            stats["health"] = _clamp(stats["health"] + 2)
            stats["xp"] = int(stats["xp"]) + 4
            state.pop("sleep_until", None)
            state["activity"] = "snacking"
            state["message"] = "Snack accepted. Tiny crunch detected."
        elif action == "play":
            stats["happiness"] = _clamp(stats["happiness"] + 30)
            stats["energy"] = _clamp(stats["energy"] - 14)
            stats["food"] = _clamp(stats["food"] - 8)
            stats["cleanliness"] = _clamp(stats["cleanliness"] - 4)
            stats["xp"] = int(stats["xp"]) + 6
            state.pop("sleep_until", None)
            state["activity"] = "playing"
            state["message"] = "Played a quiet no-refresh game."
        elif action == "clean":
            stats["cleanliness"] = _clamp(stats["cleanliness"] + 42)
            stats["health"] = _clamp(stats["health"] + 4)
            stats["happiness"] = _clamp(stats["happiness"] - 2)
            stats["xp"] = int(stats["xp"]) + 3
            state["activity"] = "cleaning"
            state["message"] = "Screen fur restored to clean pixels."
        elif action == "sleep":
            state["sleep_until"] = (now + timedelta(hours=3)).isoformat()
            stats["happiness"] = _clamp(stats["happiness"] + 2)
            stats["xp"] = int(stats["xp"]) + 2
            state["activity"] = "sleeping"
            state["message"] = "Entering low-power nap mode."
        elif action == "wake":
            state.pop("sleep_until", None)
            stats["energy"] = _clamp(stats["energy"] + 4)
            stats["happiness"] = _clamp(stats["happiness"] - 1)
            state["activity"] = "waking"
            state["message"] = "Awake. Blinking slowly."
        self._maybe_generate_ai_message(state, settings or {}, now, device_config)
        state["last_event_at"] = now.isoformat()

    def _finalize_state(self, state: dict[str, Any], settings, now: datetime) -> None:
        stats = state["stats"]
        born_at = self._parse_time(state.get("born_at"), now)
        stats["age_days"] = max(0, int((now - born_at).total_seconds() // 86400))
        stats["level"] = max(1, int(stats.get("xp", 0)) // 100 + 1)
        state["mood"] = self._choose_mood(state, now)

    def _choose_mood(self, state: dict[str, Any], now: datetime) -> str:
        stats = state["stats"]
        if state.get("mood_hint") in FACE_MAP:
            return state["mood_hint"]
        if self._is_sleeping(state, now):
            return "sleeping"
        if stats["health"] < 35:
            return "sick"
        if stats["food"] < 25:
            return "hungry"
        if stats["cleanliness"] < 25:
            return "dirty"
        if stats["energy"] < 20:
            return "tired"
        if stats["happiness"] < 25:
            return "lonely"
        if stats["happiness"] > 82 and stats["food"] > 45:
            return "happy"
        if stats["energy"] > 70 and stats["food"] > 45:
            return "working"
        return "calm"

    def _apply_autonomous_care(self, state: dict[str, Any], settings, now: datetime, device_config=None) -> bool:
        state.pop("mood_hint", None)
        if not _enabled(settings.get("autonomous_care"), True):
            return False

        stats = state["stats"]
        if stats["food"] < 18:
            stats["food"] = _clamp(stats["food"] + 24)
            stats["happiness"] = _clamp(stats["happiness"] - 2)
            stats["xp"] = int(stats["xp"]) + 2
            state["mood_hint"] = "selfcare"
            state["activity"] = "foraging"
            state["message"] = "Autonomy: found an emergency snack."
            self._maybe_generate_ai_message(state, settings, now, device_config)
            return True
        if stats["cleanliness"] < 18:
            stats["cleanliness"] = _clamp(stats["cleanliness"] + 20)
            stats["energy"] = _clamp(stats["energy"] - 2)
            stats["xp"] = int(stats["xp"]) + 2
            state["mood_hint"] = "selfcare"
            state["activity"] = "tidying"
            state["message"] = "Autonomy: tidied the pixel nest."
            self._maybe_generate_ai_message(state, settings, now, device_config)
            return True
        if stats["energy"] < 18:
            state["sleep_until"] = (now + timedelta(hours=2)).isoformat()
            stats["happiness"] = _clamp(stats["happiness"] + 2)
            stats["xp"] = int(stats["xp"]) + 2
            state["activity"] = "napping"
            state["message"] = "Autonomy: entering low-power nap."
            self._maybe_generate_ai_message(state, settings, now, device_config)
            return True
        if stats["happiness"] < 18:
            stats["happiness"] = _clamp(stats["happiness"] + 12)
            stats["energy"] = _clamp(stats["energy"] - 1)
            stats["xp"] = int(stats["xp"]) + 2
            state["mood_hint"] = "selfcare"
            state["activity"] = "self-soothing"
            state["message"] = "Autonomy: watched the quiet pixels."
            self._maybe_generate_ai_message(state, settings, now, device_config)
            return True
        return False

    def _needs_initial_event(self, state: dict[str, Any]) -> bool:
        message = str(state.get("message") or "")
        return not state.get("last_event_key") or message == "Hello. I am awake on e-paper."

    def _care_message(self, state: dict[str, Any], sleeping: bool) -> str:
        stats = state["stats"]
        if sleeping:
            return "Heartbeat: sleeping through the slow refresh."
        if stats["health"] < 35:
            return "Heartbeat: health is low. Needs care."
        if stats["food"] < 25:
            return "Heartbeat: food is low."
        if stats["cleanliness"] < 25:
            return "Heartbeat: wants a clean screen."
        if stats["happiness"] < 25:
            return "Heartbeat: waiting for attention."
        return ""

    def _apply_autonomous_event(self, state: dict[str, Any], settings, now: datetime, steps: int, device_config=None) -> None:
        events = self._event_catalog(settings, now)
        if not events:
            return

        event_index = int(state.get("event_index") or 0)
        offset = now.hour + (now.minute // max(1, _parse_int(settings.get("tick_minutes"), DEFAULT_TICK_MINUTES, 5, 240)))
        event = events[(event_index + offset) % len(events)]
        previous = state.get("last_event_key")
        if previous == event.get("id") and len(events) > 1:
            event = events[(event_index + offset + 1) % len(events)]

        self._apply_event_delta(state, event.get("delta", {}))
        state["event_index"] = event_index + max(1, steps)
        state["last_event_key"] = event.get("id", "")
        state["mood_hint"] = event.get("mood", "calm")
        state["activity"] = event.get("activity", "wandering")
        state["message"] = event.get("message", "Moved quietly between refreshes.")
        self._maybe_generate_ai_message(state, settings, now, device_config)

    def _event_catalog(self, settings, now: datetime) -> list[dict[str, Any]]:
        density = settings.get("event_density") or "expressive"
        if density == "quiet":
            events = list(QUIET_EVENTS)
        else:
            events = list(BASE_EVENTS)
            if density in {"normal", "expressive"}:
                events.extend(EXPRESSIVE_EVENTS)

        hour = now.hour
        if 5 <= hour < 10:
            events.extend([
                {
                    "id": "morning_boot",
                    "mood": "alert",
                    "activity": "morning boot",
                    "message": "Ran a morning boot check and twitched both ears.",
                    "delta": {"energy": 1, "xp": 2},
                },
                {
                    "id": "sun_guess",
                    "mood": "curious",
                    "activity": "sun guessing",
                    "message": "Guessed where the sun is without spending pixels.",
                    "delta": {"happiness": 2, "xp": 1},
                },
            ])
        elif 21 <= hour or hour < 5:
            events.extend([
                {
                    "id": "night_watch",
                    "mood": "alert",
                    "activity": "night watch",
                    "message": "Kept a tiny night watch over the dark screen.",
                    "delta": {"energy": -1, "xp": 2},
                },
                {
                    "id": "dream_cache",
                    "mood": "sleeping",
                    "activity": "dream cache",
                    "message": "Cached a small dream for the next refresh.",
                    "delta": {"energy": 2, "happiness": 1},
                },
            ])
        return events

    def _apply_event_delta(self, state: dict[str, Any], delta: dict[str, int]) -> None:
        stats = state["stats"]
        for key, change in delta.items():
            if key == "xp":
                stats["xp"] = max(0, int(stats.get("xp", 0)) + int(change))
            elif key in stats:
                stats[key] = _clamp(int(stats.get(key, 0)) + int(change))

    def _maybe_generate_ai_message(self, state: dict[str, Any], settings, now: datetime, device_config=None) -> bool:
        if not _enabled(settings.get("ai_dialogue"), False):
            return False

        backends = self._resolve_ai_backends(settings, device_config)
        if not backends:
            state["ai_message_status"] = "missing_free_provider"
            return False

        daily_limit = _parse_int(settings.get("ai_daily_limit"), DEFAULT_AI_DAILY_LIMIT, 0, 500)
        if daily_limit <= 0:
            state["ai_message_status"] = "daily_limit_disabled"
            return False
        if not self._reserve_ai_request(state, now, daily_limit):
            state["ai_message_status"] = "daily_limit_reached"
            return False

        base_message = state.get("message", "")
        ambient_context = self._ambient_context(settings, now)
        attempts: list[dict[str, Any]] = []
        fallback_from = ""
        fallback_reason = ""

        for index, backend in enumerate(backends):
            provider = backend["provider"]
            api_key = backend["api_key"]
            model = backend["model"]
            try:
                generated = self._request_ai_message(
                    provider,
                    api_key,
                    model,
                    state,
                    settings,
                    now,
                    base_message,
                    ambient_context,
                )
                self._record_ai_provider_usage(state, now, provider)
                generated = self._clean_ai_message(generated, settings)
                attempts.append({"provider": provider, "model": model, "status": "response"})
                if not generated:
                    state["ai_message_status"] = "empty_response"
                    state["ai_message_attempts"] = attempts[-4:]
                    return False

                fingerprint = self._message_fingerprint(generated)
                if fingerprint in set(state.get("ai_message_fingerprints", [])):
                    state["ai_message_status"] = "duplicate_rejected"
                    state["ai_message_attempts"] = attempts[-4:]
                    return False

                state["message"] = generated
                state["ai_message"] = True
                state["ai_message_provider"] = provider
                state["ai_message_model"] = model
                state["ai_message_at"] = now.isoformat()
                state["ai_message_status"] = "generated"
                state["ai_message_attempts"] = attempts[-4:]
                if fallback_from:
                    state["ai_message_fallback_from"] = fallback_from
                    state["ai_message_fallback_reason"] = fallback_reason
                else:
                    state.pop("ai_message_fallback_from", None)
                    state.pop("ai_message_fallback_reason", None)
                state["ai_context_snapshot"] = self._context_snapshot(ambient_context)
                self._remember_ai_message(state, generated, fingerprint)
                return True
            except Exception as exc:
                reason = self._ai_error_reason(exc)
                attempts.append({"provider": provider, "model": model, "status": "failed", "reason": reason})
                next_backend = backends[index + 1] if index + 1 < len(backends) else None
                if self._should_use_paid_fallback(provider, exc, next_backend):
                    fallback_from = provider
                    fallback_reason = reason
                    logger.warning("Free AI provider failed with a limit error; trying paid fallback: %s", reason)
                    continue

                logger.warning("AI pet message generation failed: %s", exc)
                state["ai_message_status"] = "generation_failed"
                state["ai_message_attempts"] = attempts[-4:]
                return False

        state["ai_message_status"] = "generation_failed"
        state["ai_message_attempts"] = attempts[-4:]
        return False

    def _resolve_ai_backends(self, settings, device_config) -> list[dict[str, str]]:
        provider = str(settings.get("ai_provider") or "free_auto").strip().lower()
        if provider in {"free", "auto", "free-auto"}:
            provider = "free_auto"

        if provider == "free_auto":
            backends: list[dict[str, str]] = []
            groq_key = self._load_env_key(device_config, "GROQ_API_KEY")
            if groq_key:
                backends.append({
                    "provider": "groq",
                    "api_key": groq_key,
                    "model": settings.get("ai_groq_model") or DEFAULT_GROQ_TEXT_MODEL,
                })
                if _enabled(settings.get("ai_openai_after_free"), True):
                    openai_key = self._load_env_key(device_config, "OPEN_AI_SECRET") or self._load_env_key(device_config, "OPENAI_API_KEY")
                    if openai_key:
                        backends.append({
                            "provider": "openai",
                            "api_key": openai_key,
                            "model": settings.get("ai_text_model") or DEFAULT_AI_TEXT_MODEL,
                        })
            return backends

        if provider == "groq":
            groq_key = self._load_env_key(device_config, "GROQ_API_KEY")
            if groq_key:
                return [{
                    "provider": "groq",
                    "api_key": groq_key,
                    "model": settings.get("ai_groq_model") or settings.get("ai_text_model") or DEFAULT_GROQ_TEXT_MODEL,
                }]
            return []

        if provider == "openai":
            openai_key = self._load_env_key(device_config, "OPEN_AI_SECRET") or self._load_env_key(device_config, "OPENAI_API_KEY")
            if openai_key:
                return [{
                    "provider": "openai",
                    "api_key": openai_key,
                    "model": settings.get("ai_text_model") or DEFAULT_AI_TEXT_MODEL,
                }]
            return []

        return []

    def _should_use_paid_fallback(self, provider: str, exc: Exception, next_backend: dict[str, str] | None) -> bool:
        if provider != "groq" or not next_backend or next_backend.get("provider") != "openai":
            return False
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        text = str(exc).lower()
        return (
            status == 429
            or "rate limit" in text
            or "rate_limit" in text
            or "quota" in text
            or "exceeded" in text
            or "too many requests" in text
            or "tokens per day" in text
            or "requests per day" in text
        )

    def _ai_error_reason(self, exc: Exception) -> str:
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        text = re.sub(r"\s+", " ", str(exc or "")).strip()
        if len(text) > 180:
            text = text[:180].rstrip()
        return f"HTTP {status}: {text}" if status else (text or type(exc).__name__)

    def _load_env_key(self, device_config, key_name: str) -> str:
        if not device_config or not hasattr(device_config, "load_env_key"):
            return ""
        try:
            return device_config.load_env_key(key_name) or ""
        except Exception:
            return ""

    def _reserve_ai_request(self, state: dict[str, Any], now: datetime, daily_limit: int) -> bool:
        today = now.strftime("%Y-%m-%d")
        usage = state.get("ai_usage") if isinstance(state.get("ai_usage"), dict) else {}
        if usage.get("date") != today:
            usage = {"date": today, "requests": 0}

        requests_today = int(usage.get("requests") or 0)
        if requests_today >= daily_limit:
            state["ai_usage"] = usage
            return False

        usage["requests"] = requests_today + 1
        state["ai_usage"] = usage
        return True

    def _record_ai_provider_usage(self, state: dict[str, Any], now: datetime, provider: str) -> None:
        today = now.strftime("%Y-%m-%d")
        usage = state.get("ai_provider_usage") if isinstance(state.get("ai_provider_usage"), dict) else {}
        if usage.get("date") != today:
            usage = {"date": today}
        key = f"{provider}_requests"
        usage[key] = int(usage.get(key) or 0) + 1
        state["ai_provider_usage"] = usage

    def _life_context(self, state: dict[str, Any], settings, now: datetime, base_message: str) -> dict[str, Any]:
        stats = state.get("stats", {})
        values = {
            "food": int(stats.get("food", 0)),
            "happiness": int(stats.get("happiness", 0)),
            "energy": int(stats.get("energy", 0)),
            "cleanliness": int(stats.get("cleanliness", 0)),
            "health": int(stats.get("health", 0)),
            "level": int(stats.get("level", 1)),
            "age_days": int(stats.get("age_days", 0)),
        }
        priorities: list[dict[str, Any]] = []

        def add(metric: str, value: int, severity: int, state_name: str, hint: str) -> None:
            priorities.append({
                "metric": metric,
                "value": value,
                "severity": severity,
                "state": state_name,
                "hint": hint,
            })

        if values["health"] < 35:
            add("health", values["health"], 5, "unwell", "sound fragile and ask for gentle care")
        elif values["health"] < 60:
            add("health", values["health"], 3, "recovering", "mention moving carefully")

        if values["food"] < 25:
            add("food", values["food"], 4, "hungry", "hint at snacks or tiny crumbs")
        elif values["food"] < 50:
            add("food", values["food"], 2, "getting hungry", "hint that food is on its mind")
        elif values["food"] > 82:
            add("food", values["food"], 1, "well fed", "sound content after eating")

        if values["energy"] < 20:
            add("energy", values["energy"], 4, "exhausted", "prefer rest, low power, or sleep")
        elif values["energy"] < 45:
            add("energy", values["energy"], 2, "tired", "sound slow or sleepy")
        elif values["energy"] > 78:
            add("energy", values["energy"], 1, "energetic", "sound quietly active")

        if values["cleanliness"] < 25:
            add("cleanliness", values["cleanliness"], 4, "messy", "mention dust, pixels, or wanting clean fur")
        elif values["cleanliness"] < 55:
            add("cleanliness", values["cleanliness"], 2, "dusty", "hint at tidying")

        if values["happiness"] < 25:
            add("happiness", values["happiness"], 4, "lonely", "ask softly for attention")
        elif values["happiness"] < 50:
            add("happiness", values["happiness"], 2, "subdued", "sound quiet and reserved")
        elif values["happiness"] > 82:
            add("happiness", values["happiness"], 1, "cheerful", "sound pleased without being loud")

        priorities.sort(key=lambda item: (-int(item["severity"]), item["metric"]))
        top_priority = priorities[0] if priorities else {
            "metric": "stable",
            "value": None,
            "severity": 0,
            "state": "stable",
            "hint": "respond to the current activity and time of day",
        }

        hour = now.hour
        if 5 <= hour < 10:
            time_band = "morning"
        elif 10 <= hour < 14:
            time_band = "midday"
        elif 14 <= hour < 18:
            time_band = "afternoon"
        elif 18 <= hour < 22:
            time_band = "evening"
        else:
            time_band = "night"

        mood_id = state.get("mood_hint") or state.get("mood") or "calm"
        return {
            "stats": values,
            "time_band": time_band,
            "sleeping": self._is_sleeping(state, now),
            "mood_id": mood_id,
            "mood": self._mood_label(mood_id, settings),
            "activity_id": state.get("activity", ""),
            "activity": self._activity_text(settings, state.get("activity", "")),
            "base_event": self._message_text(settings, base_message),
            "care_priority": priorities[:5],
            "top_priority": top_priority,
            "state_notes": [
                item["hint"] for item in priorities[:3]
            ] or ["healthy enough to focus on the current small activity"],
        }

    def _ambient_context(self, settings, now: datetime) -> dict[str, Any]:
        if not _enabled(settings.get("ai_use_plugin_context"), True):
            return {"available": False, "reason": "disabled", "sources": []}

        from plugins.context_cache import read_contexts

        max_age_hours = _parse_int(settings.get("ai_context_max_age_hours"), 24, 1, 72)
        max_items = _parse_int(settings.get("ai_context_max_items"), DEFAULT_CONTEXT_MAX_ITEMS, 0, 24)
        plugin_ids = self._context_plugin_ids(settings)
        try:
            entries = read_contexts(
                plugin_ids,
                now=now,
                max_age_seconds=max_age_hours * 60 * 60,
                include_stale=False,
            )
        except Exception as exc:
            logger.warning("Could not read ambient context cache: %s", exc)
            return {"available": False, "reason": "read_failed", "sources": []}

        sources: list[dict[str, Any]] = []
        remaining_items = max_items
        for entry in entries[:8]:
            payload = entry.get("payload") or {}
            source = {
                "plugin": entry.get("plugin_id"),
                "kind": self._clip_context_text(payload.get("kind") or "context", 40),
                "source": self._clip_context_text(payload.get("source") or entry.get("plugin_id"), 80),
                "age_minutes": int(entry.get("age_seconds", 0) // 60),
                "summary": self._clip_context_text(payload.get("summary") or "", 180),
                "facts": [],
                "items": [],
            }

            for fact in (payload.get("facts") or [])[:5]:
                if not isinstance(fact, dict):
                    continue
                label = self._clip_context_text(fact.get("label") or "", 40)
                value = self._clip_context_text(fact.get("value") or "", 80)
                if label and value:
                    source["facts"].append({"label": label, "value": value})

            if remaining_items > 0:
                for item in (payload.get("items") or [])[:remaining_items]:
                    normalized = self._context_item(item)
                    if normalized:
                        source["items"].append(normalized)
                        remaining_items -= 1
                        if remaining_items <= 0:
                            break

            if source["summary"] or source["facts"] or source["items"]:
                sources.append(source)

        return {
            "available": bool(sources),
            "max_age_hours": max_age_hours,
            "sources": sources,
        }

    def _context_plugin_ids(self, settings) -> list[str] | None:
        raw = str(settings.get("ai_context_plugins") or "").strip()
        if not raw or raw.lower() in {"all", "*"}:
            return ["weather", "daily_ai_news", "steam_daily_art", "steam_profile_dashboard"]
        values = [part.strip() for part in re.split(r"[,;\s]+", raw) if part.strip()]
        return values or None

    def _context_item(self, item: Any) -> dict[str, str]:
        if isinstance(item, dict):
            result = {}
            for key in ("title", "why", "name", "appid", "rotation_key", "two_week_hours", "total_hours"):
                value = self._clip_context_text(item.get(key), 120)
                if value:
                    result[key] = value
            return result
        text = self._clip_context_text(item, 140)
        return {"text": text} if text else {}

    def _clip_context_text(self, value: Any, max_len: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= max_len:
            return text
        return text[:max_len].rstrip()

    def _context_snapshot(self, ambient_context: dict[str, Any]) -> dict[str, Any]:
        sources = []
        for source in (ambient_context or {}).get("sources", [])[:6]:
            sources.append({
                "plugin": source.get("plugin"),
                "kind": source.get("kind"),
                "age_minutes": source.get("age_minutes"),
                "summary": source.get("summary"),
            })
        return {
            "available": bool((ambient_context or {}).get("available")),
            "sources": sources,
        }

    def _request_ai_message(
        self,
        provider: str,
        api_key: str,
        model: str,
        state: dict[str, Any],
        settings,
        now: datetime,
        base_message: str,
        ambient_context: dict[str, Any] | None = None,
    ) -> str:
        from openai import OpenAI

        language = self._language(settings)
        language_name = "Simplified Chinese" if language == "zh-Hans" else "English"
        life_context = self._life_context(state, settings, now, base_message)
        recent_messages = state.get("ai_recent_messages", [])[-12:]
        context = {
            "pet_name": state.get("name", DEFAULT_PET_NAME),
            "personality": settings.get("personality", ""),
            "language": language_name,
            "time": now.strftime("%Y-%m-%d %H:%M"),
            "life": life_context,
            "ambient": ambient_context or {"available": False, "sources": []},
            "chat_style": settings.get("ai_chat_style") or "wry",
            "recent_lines_to_avoid": recent_messages,
        }

        if language == "zh-Hans":
            length_rule = (
                "Write exactly one natural Simplified Chinese sentence, 12 to 30 Chinese characters. "
                "Mostly use Simplified Chinese, but a short natural English word is acceptable when it fits the pet's voice."
            )
        else:
            length_rule = "Write exactly one natural English sentence, 6 to 14 words."

        system_content = (
            "You write tiny dialogue lines for a Tamagotchi-like e-paper pet. "
            "The pet has no buttons and expresses itself through a static face, mood, activity, and one log line. "
            "You must make the line feel state-aware, not random. "
            "Use the provided life.top_priority, care_priority, state_notes, current mood, activity, time_band, and personality. "
            "If ambient.available is true, naturally connect the line to exactly one fresh ambient source such as weather, news, Steam promotion, or Steam activity. "
            "Use only facts present in ambient; do not invent headlines, prices, forecasts, game names, or current events. "
            "Follow chat_style: soft means gentle small talk, wry means dry wit, black_humor means mild dark humor without cruelty or graphic content, aphorism means a compact memorable line. "
            "If life.top_priority.severity is 3 or higher, the line must naturally imply that need. "
            "If all needs are mild, focus on the current activity, time of day, and personality. "
            "Do not contradict the stats; for example, do not sound energetic when energy is low, or full when food is low. "
            f"{length_rule} "
            "Do not use emoji, markdown, labels, quotes, brackets, or line breaks. "
            "Do not mention OpenAI, prompts, APIs, models, or being generated. "
            "Do not repeat or closely paraphrase any recent line. "
            "Keep the voice quiet, alive, slightly playful, and suitable for an e-paper screen."
        )
        user_content = json.dumps(context, ensure_ascii=False, separators=(",", ":"))

        client_kwargs = {"api_key": api_key, "timeout": 8.0}
        if provider == "groq":
            client_kwargs["base_url"] = "https://api.groq.com/openai/v1"
        client = OpenAI(**client_kwargs)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=1.1,
            max_tokens=80,
        )
        return (response.choices[0].message.content or "").strip()

    def _clean_ai_message(self, message: str, settings) -> str:
        text = str(message or "").strip()
        text = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", text)
        text = re.sub(r"^(message|log|pet|宠物|记录|日志|回复|台词)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
        text = text.replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"[#*_`]+", "", text).strip()
        if not text:
            return ""

        if self._is_chinese(settings):
            text = re.sub(r"\s+", "", text)
            max_len = 34
            if len(text) > max_len:
                text = text[:max_len].rstrip("，。！？,.!?:：；; ")
                if text and text[-1] not in "。！？":
                    text += "。"
        else:
            max_len = 96
            if len(text) > max_len:
                text = text[:max_len].rsplit(" ", 1)[0].rstrip(".,!?;: ")
                if text and text[-1] not in ".!?":
                    text += "."
        return text

    def _message_fingerprint(self, message: str) -> str:
        normalized = re.sub(r"\s+", "", str(message or "").lower())
        return hashlib.blake2s(normalized.encode("utf-8"), digest_size=8).hexdigest()

    def _remember_ai_message(self, state: dict[str, Any], message: str, fingerprint: str) -> None:
        fingerprints = state.get("ai_message_fingerprints")
        if not isinstance(fingerprints, list):
            fingerprints = []
        if fingerprint not in fingerprints:
            fingerprints.append(fingerprint)
        state["ai_message_fingerprints"] = fingerprints

        recent = state.get("ai_recent_messages")
        if not isinstance(recent, list):
            recent = []
        recent.append(message)
        state["ai_recent_messages"] = recent[-24:]

    def _is_sleeping(self, state: dict[str, Any], now: datetime) -> bool:
        sleep_until = state.get("sleep_until")
        if not sleep_until:
            return False
        return self._parse_time(sleep_until, now) > now

    def _parse_time(self, value: Any, fallback: datetime) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = fallback.tzinfo.localize(parsed) if hasattr(fallback.tzinfo, "localize") else parsed.replace(tzinfo=fallback.tzinfo)
            return parsed
        except Exception:
            return fallback

    def _state_summary(self, state: dict[str, Any], settings=None) -> dict[str, Any]:
        mood = state.get("mood") or "calm"
        face = FACE_MAP.get(mood, FACE_MAP["calm"])[0]
        label = self._mood_label(mood, settings or {})
        stats = state["stats"]
        return {
            "name": state.get("name", DEFAULT_PET_NAME),
            "mood": label,
            "mood_id": mood,
            "face": face,
            "activity": self._activity_text(settings or {}, state.get("activity", "")),
            "message": self._message_text(settings or {}, state.get("message", "")),
            "stats": {
                "food": int(stats["food"]),
                "happiness": int(stats["happiness"]),
                "energy": int(stats["energy"]),
                "cleanliness": int(stats["cleanliness"]),
                "health": int(stats["health"]),
                "level": int(stats["level"]),
                "xp": int(stats["xp"]),
                "age_days": int(stats["age_days"]),
            },
        }

    def _render(self, dimensions, settings, state: dict[str, Any], now: datetime):
        width, height = dimensions
        image = Image.new("RGB", dimensions, (0, 0, 0))
        draw = ImageDraw.Draw(image)

        mood = state.get("mood") or "calm"
        face = FACE_MAP.get(mood, FACE_MAP["calm"])[0]
        mood_label = self._mood_label(mood, settings)
        state["face"] = face
        activity = self._activity_text(settings, state.get("activity", "quiet watch"))
        stats = state["stats"]

        text_family = self._text_family(settings)
        title_font = self._font(text_family, 44 if self._is_chinese(settings) else 46, "bold")
        meta_font = self._font(text_family, 18)
        label_font = self._font(text_family, 15 if self._is_chinese(settings) else 14, "bold")
        value_font = self._font("Jost", 21, "bold")
        message_font = self._font(text_family, 23 if self._is_chinese(settings) else 24)
        journal_font = self._font(text_family, 16)
        telemetry_font = self._font("Jost", 13, "bold")
        face_font = self._fit_font(draw, face, 86, 310)

        pad = 22
        header_h = 82
        footer_h = 94
        gap = 16
        content_top = pad + header_h + gap
        footer_top = height - pad - footer_h
        left = pad
        left_w = 360
        right = left + left_w + gap
        right_w = width - right - pad

        draw.rectangle((1, 1, width - 2, height - 2), outline=(255, 255, 255), width=2)
        self._draw_header(draw, (pad, pad, width - pad, pad + header_h), settings, state, mood_label, title_font, meta_font, label_font, now)
        self._draw_face_panel(draw, (left, content_top, left + left_w, footer_top - gap), settings, face, mood_label, activity, face_font, label_font)
        self._draw_stats_panel(draw, (right, content_top, right + right_w, footer_top - gap), settings, stats, label_font, value_font)
        self._draw_message_panel(draw, (pad, footer_top, width - pad, height - pad), settings, state, message_font, journal_font, label_font, telemetry_font)
        return image

    def _draw_header(self, draw, box: tuple[int, int, int, int], settings, state: dict[str, Any], mood_label: str, title_font, meta_font, label_font, now: datetime) -> None:
        x1, y1, x2, y2 = box
        stats = state["stats"]
        name = str(state.get("name", DEFAULT_PET_NAME)).strip() or DEFAULT_PET_NAME
        if len(name) > 15:
            name = name[:15]

        draw.rectangle(box, outline=(255, 255, 255), width=2)
        title = name if self._is_chinese(settings) or not name.isascii() else name.upper()
        draw.text((x1 + 18, y1 + 8), title, font=title_font, fill=(255, 255, 255))

        if self._is_chinese(settings):
            meta = f"{self._ui(settings, 'level', 'LV')} {stats['level']}  {self._ui(settings, 'age', 'AGE')} {stats['age_days']}{self._ui(settings, 'day', 'D')}  XP {stats['xp']}"
        else:
            meta = f"LV {stats['level']}  AGE {stats['age_days']}D  XP {stats['xp']}"
        draw.text((x1 + 20, y2 - 28), meta, font=meta_font, fill=(255, 255, 255))

        stamp = now.strftime("%m/%d %H:%M")
        stamp_w = self._text_w(draw, stamp, meta_font)
        draw.text((x2 - 18 - stamp_w, y1 + 12), stamp, font=meta_font, fill=(255, 255, 255))
        self._draw_badge(draw, x2 - 148, y2 - 34, 130, 24, self._badge_text(settings, mood_label), label_font)

    def _draw_face_panel(self, draw, box: tuple[int, int, int, int], settings, face: str, mood_label: str, activity: str, face_font, label_font) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle(box, outline=(255, 255, 255), width=2)
        draw.text((x1 + 16, y1 + 12), self._badge_text(settings, self._ui(settings, "face", "FACE")), font=label_font, fill=(255, 255, 255))
        self._draw_badge(draw, x2 - 124, y1 + 10, 108, 24, self._badge_text(settings, mood_label), label_font)

        cx = (x1 + x2) // 2
        cy = y1 + int((y2 - y1) * 0.53)
        draw.text((cx, cy), face, anchor="mm", font=face_font, fill=(255, 255, 255))
        draw.line((x1 + 28, y2 - 42, x2 - 28, y2 - 42), fill=(255, 255, 255), width=1)
        activity_label = f"{self._ui(settings, 'activity', 'ACTIVITY')}: {str(activity or self._activity_text(settings, 'quiet watch'))}"
        activity_label = self._badge_text(settings, activity_label)
        activity_label = self._clip_text(draw, activity_label, label_font, x2 - x1 - 46)
        draw.text((cx, y2 - 27), activity_label, anchor="mm", font=label_font, fill=(255, 255, 255))

    def _draw_stats_panel(self, draw, box: tuple[int, int, int, int], settings, stats: dict[str, Any], label_font, value_font) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle(box, outline=(255, 255, 255), width=2)
        draw.text((x1 + 16, y1 + 12), self._badge_text(settings, self._ui(settings, "vitals", "VITALS")), font=label_font, fill=(255, 255, 255))
        self._draw_badge(draw, x2 - 122, y1 + 10, 106, 24, self._badge_text(settings, self._ui(settings, "auto", "AUTO")), label_font)

        rows = [
            (self._localized(settings, "stats", "food", "FOOD"), "food"),
            (self._localized(settings, "stats", "happiness", "MOOD"), "happiness"),
            (self._localized(settings, "stats", "energy", "ENERGY"), "energy"),
            (self._localized(settings, "stats", "cleanliness", "CLEAN"), "cleanliness"),
            (self._localized(settings, "stats", "health", "HEALTH"), "health"),
        ]
        row_y = y1 + 58
        row_h = 33
        for label, key in rows:
            self._draw_bar(draw, x1 + 16, row_y, x2 - x1 - 32, self._badge_text(settings, label), int(stats[key]), label_font, value_font)
            row_y += row_h

    def _draw_bar(self, draw, x: int, y: int, width: int, label: str, value: int, label_font, value_font) -> None:
        value = _clamp(value)
        value_text = f"{value:03d}"
        label_w = max(48, self._text_w(draw, label, label_font) + 10)
        value_w = 44
        bar_h = 14
        bar_x = x + label_w
        bar_y = y + 8
        bar_w = max(80, width - label_w - value_w - 10)

        draw.text((x, y + 3), label, font=label_font, fill=(255, 255, 255))
        draw.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), outline=(255, 255, 255), width=1)
        fill_w = int((bar_w - 4) * value / 100)
        if fill_w > 0:
            draw.rectangle((bar_x + 2, bar_y + 2, bar_x + 2 + fill_w, bar_y + bar_h - 2), fill=(255, 255, 255))
        draw.text((bar_x + bar_w + 8, y - 2), value_text, font=value_font, fill=(255, 255, 255))

    def _draw_badge(self, draw, x: int, y: int, width: int, height: int, text: str, font) -> None:
        draw.rectangle((x, y, x + width, y + height), outline=(255, 255, 255), width=1)
        tw = self._text_w(draw, text, font)
        th = self._text_h(draw, text, font)
        draw.text((x + (width - tw) // 2, y + (height - th) // 2 - 1), text, font=font, fill=(255, 255, 255))

    def _draw_message_panel(self, draw, box: tuple[int, int, int, int], settings, state: dict[str, Any], message_font, journal_font, label_font, telemetry_font) -> None:
        x1, y1, x2, y2 = box
        draw.rectangle(box, outline=(255, 255, 255), width=2)
        log_label = self._badge_text(settings, self._ui(settings, "log", "LOG"))
        draw.text((x1 + 16, y1 + 10), log_label, font=label_font, fill=(255, 255, 255))

        message = self._message_text(settings, state.get("message", "Quiet heartbeat."))
        raw_journal = self._latest_journal_line(settings) if _enabled(settings.get("show_journal"), True) else ""
        journal_line = self._message_text(settings, raw_journal) if raw_journal else ""
        telemetry = self._ai_telemetry_text(settings, state)
        text_x = x1 + 16 + self._text_w(draw, log_label, label_font) + 20
        max_w = x2 - text_x - 18
        lines = self._wrap(draw, message, message_font, max_w, max_lines=2)
        y = y1 + 12
        for line in lines:
            draw.text((text_x, y), line, font=message_font, fill=(255, 255, 255))
            y += 28

        telemetry_w = 0
        if telemetry:
            telemetry = self._clip_text(draw, telemetry, telemetry_font, x2 - x1 - 32)
            telemetry_w = self._text_w(draw, telemetry, telemetry_font)
            draw.text((x2 - 16 - telemetry_w, y2 - 22), telemetry, font=telemetry_font, fill=(255, 255, 255))

        if journal_line and journal_line != message:
            journal_prefix = self._ui(settings, "last", "LAST")
            journal_w = max(80, max_w - telemetry_w - (20 if telemetry_w else 0))
            journal = self._wrap(draw, f"{journal_prefix}: {journal_line}", journal_font, journal_w, max_lines=1)[0]
            draw.text((text_x, y2 - 24), journal, font=journal_font, fill=(255, 255, 255))

    def _ai_telemetry_text(self, settings, state: dict[str, Any]) -> str:
        usage = state.get("ai_usage") if isinstance(state.get("ai_usage"), dict) else {}
        requests_today = _parse_int(usage.get("requests"), 0, 0, 9999)
        daily_limit = _parse_int(settings.get("ai_daily_limit"), DEFAULT_AI_DAILY_LIMIT, 0, 500)
        provider = str(state.get("ai_message_provider") or "").strip().lower() or "local"
        engine = {
            "groq": "Groq",
            "openai": "OpenAI",
            "local": "Local",
            "free_auto": "Auto",
        }.get(provider, provider[:10].title() if provider else "Local")

        if daily_limit > 0:
            text = f"AI {engine} {requests_today}/{daily_limit}"
        else:
            text = f"AI {engine} {requests_today}"

        fallback_from = str(state.get("ai_message_fallback_from") or "").strip().lower()
        if provider == "openai" and fallback_from == "groq":
            text = f"{text} <- Groq"
        return text

    def _latest_journal_line(self, settings) -> str:
        try:
            path = self._journal_file(settings)
            if not path.is_file():
                return ""
            lines = [line.strip("- \n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if not lines:
                return ""
            latest = lines[-1]
            if ": " in latest:
                latest = latest.split(": ", 1)[1]
            return latest
        except Exception:
            return ""

    def _font(self, family: str, size: int, weight: str = "normal"):
        families = [family]
        if family != "LXGW WenKai":
            families.append("LXGW WenKai")
        for candidate in families:
            try:
                font = get_font(candidate, int(size), weight)
                if font:
                    return font
            except Exception:
                pass
        return ImageFont.load_default()

    def _fit_font(self, draw, text: str, max_size: int, max_width: int):
        for size in range(int(max_size), 15, -2):
            font = self._font("Jost", size, "bold")
            if self._text_w(draw, text, font) <= max_width:
                return font
        return self._font("Jost", 16, "bold")

    def _wrap(self, draw, text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
        text = str(text or "")
        cjk = _contains_cjk(text)
        words = list(text) if cjk else text.split()
        lines = []
        current = ""
        for word in words:
            if cjk and word == "\n":
                if current:
                    lines.append(current)
                    current = ""
                continue
            candidate = word if not current else (f"{current}{word}" if cjk else f"{current} {word}")
            if self._text_w(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word.strip() if cjk else word
                if len(lines) >= max_lines:
                    return lines
        if current and len(lines) < max_lines:
            lines.append(current)
        return lines or [""]

    def _clip_text(self, draw, text: str, font, max_width: int) -> str:
        text = str(text or "")
        if self._text_w(draw, text, font) <= max_width:
            return text
        suffix = "" if _contains_cjk(text) else "."
        while text and self._text_w(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return text + suffix if suffix and text else text

    def _text_w(self, draw, text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    def _text_h(self, draw, text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]
