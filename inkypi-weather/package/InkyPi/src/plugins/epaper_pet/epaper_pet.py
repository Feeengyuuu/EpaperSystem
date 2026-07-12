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
from utils.app_utils import bounded_int, get_base_ui_font, get_font
from utils.image_utils import text_width

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
LOCAL_AI_TEXT_MODEL = "local-rules-v1"
DEFAULT_AI_DAILY_LIMIT = 24
DEFAULT_CONTEXT_MAX_ITEMS = 12

# Color tokens follow docs/color-ui-guidelines.md: process black linework,
# paper/white grounds, and vintage comic process-color accents.
PET_PAPER = (255, 248, 220)  # 25Y PANTONE 100, warm comic paper ground
PET_PANEL = (255, 255, 255)  # paper white, preserves maximum text contrast
PET_PANEL_BLUE = (235, 246, 255)  # 25B PANTONE 304 family, paper-tinted
PET_PANEL_YELLOW = (255, 239, 176)  # 50Y PANTONE 101 family, paper-tinted
PET_PANEL_GREEN = (232, 247, 224)  # 100Y-25B PANTONE 374 family, paper-tinted
PET_INK = (0, 0, 0)  # PROCESS BLACK
PET_MUTED = (126, 112, 82)  # 50Y-25R-25B PANTONE 465 family
PET_RULE = (190, 177, 134)  # 50Y-25R-25B PANTONE 465 family, lightened
PET_BLUE = (0, 92, 185)  # 100B-25R PANTONE 285 family
PET_YELLOW = (255, 196, 30)  # 100Y-25R PANTONE 123 family
PET_ORANGE = (245, 122, 38)  # 100Y-50R PANTONE ORANGE 021 family
PET_RED = (222, 45, 38)  # 100Y-100R PANTONE RED 032 family
PET_GREEN = (0, 152, 82)  # 100Y-100B PANTONE 354 family
PET_PURPLE = (102, 62, 153)  # 100R-100B PANTONE 266 family
PET_BROWN = (122, 78, 43)  # 100Y-50R-50B PANTONE 470 family
PET_NIGHT_PAPER = (0, 0, 0)  # PROCESS BLACK, deep-night ground
PET_NIGHT_PANEL = (12, 15, 27)  # PROCESS BLACK with 100B-25R PANTONE 285 family lift
PET_NIGHT_PANEL_BLUE = (8, 24, 52)  # 100B-25R PANTONE 285 family, night-calibrated
PET_NIGHT_PANEL_YELLOW = (54, 42, 10)  # 100Y-25R PANTONE 123 family, night-calibrated
PET_NIGHT_PANEL_GREEN = (8, 38, 25)  # 100Y-100B PANTONE 354 family, night-calibrated
PET_NIGHT_INK = (255, 255, 255)  # paper white for night contrast
PET_NIGHT_MUTED = (202, 190, 150)  # 50Y-25R-25B PANTONE 465 family, lightened
PET_NIGHT_RULE = (92, 88, 70)  # muted print rule for dark panels
PET_NIGHT_BLUE = (88, 165, 232)  # 100B-25R PANTONE 285 family, night-calibrated
PET_NIGHT_YELLOW = (255, 205, 54)  # 100Y-25R PANTONE 123 family
PET_NIGHT_ORANGE = (255, 136, 47)  # 100Y-50R PANTONE ORANGE 021 family
PET_NIGHT_RED = (255, 82, 74)  # 100Y-100R PANTONE RED 032 family, night-calibrated
PET_NIGHT_GREEN = (80, 201, 128)  # 100Y-100B PANTONE 354 family, night-calibrated
PET_NIGHT_PURPLE = (177, 142, 230)  # 100R-100B PANTONE 266 family, night-calibrated
PET_NIGHT_BROWN = (203, 145, 87)  # 100Y-50R-50B PANTONE 470 family, night-calibrated
PET_BAR_COLORS = {
    "food": PET_ORANGE,
    "happiness": PET_YELLOW,
    "energy": PET_BLUE,
    "cleanliness": PET_PURPLE,
    "health": PET_GREEN,
}
PET_NIGHT_BAR_COLORS = {
    "food": PET_NIGHT_ORANGE,
    "happiness": PET_NIGHT_YELLOW,
    "energy": PET_NIGHT_BLUE,
    "cleanliness": PET_NIGHT_PURPLE,
    "health": PET_NIGHT_GREEN,
}
PET_MOOD_COLORS = {
    "happy": PET_GREEN,
    "calm": PET_BLUE,
    "curious": PET_BLUE,
    "playful": PET_YELLOW,
    "alert": PET_ORANGE,
    "grooming": PET_PURPLE,
    "exploring": PET_GREEN,
    "hungry": PET_RED,
    "dirty": PET_BROWN,
    "sick": PET_RED,
    "tired": PET_PURPLE,
    "sleeping": PET_BLUE,
    "lonely": PET_PURPLE,
    "bored": PET_BROWN,
    "working": PET_BLUE,
    "selfcare": PET_GREEN,
    "hunting": PET_ORANGE,
    "zoomies": PET_RED,
    "belly": PET_YELLOW,
}
PET_NIGHT_MOOD_COLORS = {
    "happy": PET_NIGHT_GREEN,
    "calm": PET_NIGHT_BLUE,
    "curious": PET_NIGHT_BLUE,
    "playful": PET_NIGHT_YELLOW,
    "alert": PET_NIGHT_ORANGE,
    "grooming": PET_NIGHT_PURPLE,
    "exploring": PET_NIGHT_GREEN,
    "hungry": PET_NIGHT_RED,
    "dirty": PET_NIGHT_BROWN,
    "sick": PET_NIGHT_RED,
    "tired": PET_NIGHT_PURPLE,
    "sleeping": PET_NIGHT_BLUE,
    "lonely": PET_NIGHT_PURPLE,
    "bored": PET_NIGHT_BROWN,
    "working": PET_NIGHT_BLUE,
    "selfcare": PET_NIGHT_GREEN,
    "hunting": PET_NIGHT_ORANGE,
    "zoomies": PET_NIGHT_RED,
    "belly": PET_NIGHT_YELLOW,
}
DEFAULT_CONTEXT_PLUGIN_IDS = [
    "weather",
    "daily_ai_news",
    "steam_daily_art",
    "steam_profile_dashboard",
    "steam_charts",
    "live_radar",
    "daily_word_poem",
    "apod",
    "natgeo_photo_of_the_day",
    "magazine_covers",
    "comic",
    "wpotd",
]
MAX_OFFLINE_TICKS = 96
HUNTING_FOOD_THRESHOLD = 25
LEVEL_XP_STEP = 100

LEVEL_TIERS = [
    {"min_level": 15, "title": "Night Hunter", "prey_size": "huge", "reserve_cap": 720},
    {"min_level": 10, "title": "Cache Hunter", "prey_size": "large", "reserve_cap": 540},
    {"min_level": 7, "title": "Screen Prowler", "prey_size": "medium", "reserve_cap": 360},
    {"min_level": 4, "title": "Nest Stalker", "prey_size": "small", "reserve_cap": 240},
    {"min_level": 1, "title": "Crumb Hunter", "prey_size": "tiny", "reserve_cap": 120},
]

PREY_SIZE_RANK = {"tiny": 0, "small": 1, "medium": 2, "large": 3, "huge": 4}
PREY_SIZE_ORDER = ["tiny", "small", "medium", "large", "huge"]

HUNTED_FOODS = [
    {"id": "springtail", "level_min": 1, "size": "tiny", "prey_group": "microarthropod", "prey_mass_g": 0.002, "food": "springtail", "food_zh": "跳虫", "food_gain": 18, "reserve_gain": 4, "energy_cost": 3, "happiness_gain": 2, "xp_gain": 3},
    {"id": "aphid", "level_min": 1, "size": "tiny", "prey_group": "insect", "prey_mass_g": 0.003, "food": "aphid", "food_zh": "蚜虫", "food_gain": 18, "reserve_gain": 4, "energy_cost": 3, "happiness_gain": 2, "xp_gain": 3},
    {"id": "fruit_fly", "level_min": 1, "size": "tiny", "prey_group": "insect", "prey_mass_g": 0.001, "food": "fruit fly", "food_zh": "果蝇", "food_gain": 20, "reserve_gain": 5, "energy_cost": 4, "happiness_gain": 3, "xp_gain": 4},
    {"id": "fungus_gnat", "level_min": 1, "size": "tiny", "prey_group": "insect", "prey_mass_g": 0.002, "food": "fungus gnat", "food_zh": "蕈蚊", "food_gain": 20, "reserve_gain": 6, "energy_cost": 4, "happiness_gain": 2, "xp_gain": 4},
    {"id": "worker_ant", "level_min": 2, "size": "tiny", "prey_group": "insect", "prey_mass_g": 0.004, "food": "worker ant", "food_zh": "工蚁", "food_gain": 24, "reserve_gain": 8, "energy_cost": 5, "happiness_gain": 3, "xp_gain": 5},
    {"id": "mosquito", "level_min": 2, "size": "tiny", "prey_group": "insect", "prey_mass_g": 0.003, "food": "mosquito", "food_zh": "蚊子", "food_gain": 22, "reserve_gain": 7, "energy_cost": 5, "happiness_gain": 3, "xp_gain": 5},
    {"id": "housefly", "level_min": 4, "size": "small", "prey_group": "insect", "prey_mass_g": 0.012, "food": "housefly", "food_zh": "家蝇", "food_gain": 34, "reserve_gain": 18, "energy_cost": 8, "happiness_gain": 3, "xp_gain": 8},
    {"id": "mealworm", "level_min": 4, "size": "small", "prey_group": "insect larva", "prey_mass_g": 0.1, "food": "mealworm", "food_zh": "黄粉虫", "food_gain": 40, "reserve_gain": 24, "energy_cost": 9, "happiness_gain": 4, "xp_gain": 9},
    {"id": "small_cricket", "level_min": 4, "size": "small", "prey_group": "insect", "prey_mass_g": 0.25, "food": "small cricket", "food_zh": "小蟋蟀", "food_gain": 44, "reserve_gain": 28, "energy_cost": 11, "happiness_gain": 4, "xp_gain": 11},
    {"id": "pantry_moth", "level_min": 5, "size": "small", "prey_group": "insect", "prey_mass_g": 0.08, "food": "pantry moth", "food_zh": "谷蛾", "food_gain": 38, "reserve_gain": 24, "energy_cost": 10, "happiness_gain": 4, "xp_gain": 10},
    {"id": "earthworm", "level_min": 5, "size": "small", "prey_group": "annelid", "prey_mass_g": 0.5, "food": "earthworm", "food_zh": "蚯蚓", "food_gain": 48, "reserve_gain": 34, "energy_cost": 12, "happiness_gain": 3, "xp_gain": 12},
    {"id": "grasshopper", "level_min": 7, "size": "medium", "prey_group": "insect", "prey_mass_g": 1.0, "food": "grasshopper", "food_zh": "蚱蜢", "food_gain": 58, "reserve_gain": 58, "energy_cost": 16, "happiness_gain": 5, "xp_gain": 16},
    {"id": "cicada", "level_min": 7, "size": "medium", "prey_group": "insect", "prey_mass_g": 2.0, "food": "cicada", "food_zh": "蝉", "food_gain": 62, "reserve_gain": 72, "energy_cost": 18, "happiness_gain": 5, "xp_gain": 18},
    {"id": "tree_frog", "level_min": 8, "size": "medium", "prey_group": "amphibian", "prey_mass_g": 5.0, "food": "tree frog", "food_zh": "树蛙", "food_gain": 68, "reserve_gain": 90, "energy_cost": 20, "happiness_gain": 5, "xp_gain": 20},
    {"id": "house_gecko", "level_min": 8, "size": "medium", "prey_group": "reptile", "prey_mass_g": 6.0, "food": "house gecko", "food_zh": "壁虎", "food_gain": 70, "reserve_gain": 96, "energy_cost": 22, "happiness_gain": 5, "xp_gain": 22},
    {"id": "small_skink", "level_min": 9, "size": "medium", "prey_group": "reptile", "prey_mass_g": 9.0, "food": "small skink", "food_zh": "小石龙子", "food_gain": 74, "reserve_gain": 110, "energy_cost": 24, "happiness_gain": 5, "xp_gain": 24},
    {"id": "field_mouse", "level_min": 10, "size": "large", "prey_group": "small mammal", "prey_mass_g": 18.0, "food": "field mouse", "food_zh": "田鼠", "food_gain": 82, "reserve_gain": 180, "energy_cost": 28, "happiness_gain": 6, "xp_gain": 30},
    {"id": "meadow_vole", "level_min": 11, "size": "large", "prey_group": "small mammal", "prey_mass_g": 35.0, "food": "meadow vole", "food_zh": "草原田鼠", "food_gain": 86, "reserve_gain": 230, "energy_cost": 31, "happiness_gain": 6, "xp_gain": 34},
    {"id": "young_brown_rat", "level_min": 12, "size": "large", "prey_group": "small mammal", "prey_mass_g": 75.0, "food": "young brown rat", "food_zh": "幼年褐鼠", "food_gain": 90, "reserve_gain": 300, "energy_cost": 35, "happiness_gain": 6, "xp_gain": 38},
    {"id": "adult_brown_rat", "level_min": 15, "size": "huge", "prey_group": "small mammal", "prey_mass_g": 250.0, "food": "adult brown rat", "food_zh": "成年褐鼠", "food_gain": 96, "reserve_gain": 430, "energy_cost": 42, "happiness_gain": 7, "xp_gain": 48},
    {"id": "cottontail_rabbit", "level_min": 16, "size": "huge", "prey_group": "lagomorph", "prey_mass_g": 900.0, "food": "cottontail rabbit", "food_zh": "棉尾兔", "food_gain": 100, "reserve_gain": 620, "energy_cost": 56, "happiness_gain": 8, "xp_gain": 64},
    {"id": "muskrat", "level_min": 18, "size": "huge", "prey_group": "semi-aquatic mammal", "prey_mass_g": 1000.0, "food": "muskrat", "food_zh": "麝鼠", "food_gain": 100, "reserve_gain": 700, "energy_cost": 62, "happiness_gain": 8, "xp_gain": 72},
]

AI_DIALOGUE_ANGLES = [
    "current body state",
    "food reserve economics",
    "prey ecology",
    "level ambition",
    "daily routine",
    "time-of-day instinct",
    "last hunt memory",
    "near-future hunt plan",
    "visible body pose",
    "short-front-paw comedy",
    "tail and ear body language",
    "day-night visual mood",
    "ambient weather",
    "ambient news",
    "ambient games",
    "tiny survival philosophy",
    "e-paper physics",
    "quiet black humor",
    "compact aphorism",
]

AI_LINE_SHAPES = [
    "deadpan field note",
    "small confession",
    "mini proverb",
    "sleepy report",
    "hunter boast",
    "inventory audit",
    "weather aside",
    "game-adjacent mutter",
    "one-breath diary",
    "pose caption",
    "motion snapshot",
    "tiny stage direction",
]

AI_TONE_COLORS = [
    "dry",
    "warm",
    "feral",
    "sleepy",
    "suspicious",
    "proud",
    "melancholy",
    "absurd",
    "matter-of-fact",
]

AI_DETAIL_LENSES = [
    "prey name",
    "prey mass",
    "prey group",
    "reserve days",
    "xp to next level",
    "next prey unlock",
    "daily favorite",
    "daily goal",
    "weakest stat",
    "fresh ambient fact",
    "current pose image",
    "daily motion theme",
    "body focus",
    "visual motif",
    "pose line hook",
]

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
    "hunting": ("(=O_O=)", "Hunting"),
    "zoomies": ("(=O_O=)!", "Zoomies"),
    "belly": ("(. o .)", "Belly"),
}

PET_STATE_IMAGE_DIR = Path(__file__).resolve().parent / "assets" / "cat_states"
PET_STATE_IMAGE_MAP = {
    "happy": "happy",
    "calm": "calm",
    "curious": "curious",
    "playful": "playful",
    "alert": "alert",
    "grooming": "grooming",
    "exploring": "tail_swish",
    "hungry": "hungry",
    "dirty": "unwell",
    "sick": "unwell",
    "tired": "tired",
    "sleeping": "sleeping",
    "lonely": "tired",
    "bored": "tired",
    "working": "alert_listening",
    "selfcare": "kneading",
    "hunting": "hunting",
    "zoomies": "zoomies",
    "belly": "belly",
}
PET_ACTIVITY_IMAGE_MAP = {
    "bedtime curl": "dreaming",
    "blanket kneading": "kneading",
    "cache guarding": "alert_listening",
    "cleaning": "kneading",
    "crumb audit": "tail_swish",
    "dream cache": "dreaming",
    "dusk grooming": "grooming",
    "edge watch": "alert_listening",
    "face practice": "alert_listening",
    "face wash": "grooming",
    "foraging": "tail_swish",
    "hunting": "hunting",
    "listening": "alert_listening",
    "micro dance": "zoomies",
    "morning boot": "alert_listening",
    "napping": "dreaming",
    "needs care": "unwell",
    "nest check": "alert_listening",
    "nest sweeping": "kneading",
    "pixel hoarding": "tail_swish",
    "pixel patrol": "tail_swish",
    "playing": "pounce",
    "resting": "dreaming",
    "scheduled nap": "dreaming",
    "self grooming": "grooming",
    "shadow pounce": "pounce",
    "shadow prank": "zoomies",
    "snack tracking": "hunting",
    "snacking": "snacking",
    "soft dreaming": "dreaming",
    "solo game": "pounce",
    "sorting thoughts": "alert_listening",
    "stash meal": "snacking",
    "stretching": "stretch",
    "sun chase": "pounce",
    "sun guessing": "alert_listening",
    "thing inspection": "tail_swish",
    "thought collecting": "tail_swish",
    "tidying": "kneading",
    "tiny zoomies": "zoomies",
    "waking": "alert_listening",
    "warm listening": "alert_listening",
    "window watch": "tail_swish",
    "world sniffing": "tail_swish",
}

PET_PHYSICAL_IDENTITY = {
    "species": "domestic cat",
    "coat": "white fur with gray and black tabby patches on head, back, and tail",
    "eyes": "green eyes",
    "nose": "small pink nose",
    "body": "compact low-to-ground body",
    "front_legs": "short stubby front legs with compact paws",
    "voice_rule": "when physical detail helps, mention the body gently and never as a defect",
}
PET_POSE_LIBRARY = {
    "alert": {
        "label": "alert standing",
        "body_language": "ears forward, body held still, watching the edge of the screen",
        "line_hook": "notices small movement before anyone else",
    },
    "alert_listening": {
        "label": "alert listening",
        "body_language": "upright listening pose, green eyes open, short front paws planted",
        "line_hook": "hears the room, the screen, or a tiny future snack",
    },
    "belly": {
        "label": "belly sprawl",
        "body_language": "fully relaxed belly-up sprawl with the whole cat visible",
        "line_hook": "trusts the room enough to be unserious",
    },
    "calm": {
        "label": "calm loaf",
        "body_language": "low quiet body, soft eyes, settled into the panel",
        "line_hook": "saves energy between refreshes",
    },
    "curious": {
        "label": "curious watch",
        "body_language": "head angled toward a small mystery, whiskers awake",
        "line_hook": "inspects one strange thing without overreacting",
    },
    "dreaming": {
        "label": "curled dream",
        "body_language": "curled sleeping body with tail tucked around the paws",
        "line_hook": "stores a small dream in cache",
    },
    "grooming": {
        "label": "grooming",
        "body_language": "careful paw-and-face grooming with tidy fur",
        "line_hook": "edits one pixel of fur until it behaves",
    },
    "happy": {
        "label": "happy sit",
        "body_language": "upright relaxed sit, bright eyes, compact paws visible",
        "line_hook": "pleased in a quiet e-paper way",
    },
    "hungry": {
        "label": "hungry sit",
        "body_language": "round seated posture, paws close, attention on snacks",
        "line_hook": "counts crumbs with serious intent",
    },
    "hunting": {
        "label": "hunting crawl",
        "body_language": "low stalking body, short front legs forward, tail balanced",
        "line_hook": "tracks a tiny non-graphic prey clue",
    },
    "kneading": {
        "label": "kneading",
        "body_language": "front paws working an invisible blanket, eyes half closed",
        "line_hook": "turns comfort into a small task",
    },
    "playful": {
        "label": "playful crouch",
        "body_language": "low playful crouch, paws ready, tail lively",
        "line_hook": "starts a harmless game with a shadow",
    },
    "pounce": {
        "label": "pounce",
        "body_language": "crouched pounce pose with short front legs tucked and ready",
        "line_hook": "makes a brave tiny ambush",
    },
    "sleeping": {
        "label": "sleeping curl",
        "body_language": "small curled nap pose, breathing quietly",
        "line_hook": "runs in low-power dream mode",
    },
    "snacking": {
        "label": "snacking",
        "body_language": "sitting upright while holding a tiny snack in both front paws",
        "line_hook": "protects one snack as if it were treasure",
    },
    "stretch": {
        "label": "stretch",
        "body_language": "front stretch with short forelegs extended and tail up",
        "line_hook": "does a serious little stretch before the next plan",
    },
    "tail_swish": {
        "label": "tail swish",
        "body_language": "standing curious pose with tail raised and swishing",
        "line_hook": "uses the tail as a question mark",
    },
    "tired": {
        "label": "tired sit",
        "body_language": "sleepy seated pose, eyelids heavy, body low",
        "line_hook": "asks the screen to become quieter",
    },
    "unwell": {
        "label": "unwell rest",
        "body_language": "careful low rest pose, needs gentle handling",
        "line_hook": "moves softly and asks for care without drama",
    },
    "zoomies": {
        "label": "tiny zoomies",
        "body_language": "low running pose, compact body stretched forward, tail streaming",
        "line_hook": "short legs still manage six brave seconds of speed",
    },
}
PET_POSE_KEYS = list(PET_POSE_LIBRARY.keys())
PET_DAILY_MOTION_THEMES = [
    {
        "id": "short_leg_zoomies",
        "goal": "turn one tiny movement into a dramatic event",
        "line_hook": "short front legs, serious speed, very small chaos",
    },
    {
        "id": "quiet_body_language",
        "goal": "let the pose say more than the status text",
        "line_hook": "ears, paws, tail, and posture carry the mood",
    },
    {
        "id": "snack_theater",
        "goal": "treat food and crumbs like a daily expedition",
        "line_hook": "snacks, prey memory, and reserve logic become tiny drama",
    },
    {
        "id": "dream_cache",
        "goal": "turn sleep and rest into little cached dreams",
        "line_hook": "low-power sleep, dream bubbles, and tomorrow's plan",
    },
    {
        "id": "tail_question",
        "goal": "make curiosity visible through tail and ear motion",
        "line_hook": "a tail swish becomes a question mark",
    },
    {
        "id": "comfort_engine",
        "goal": "make grooming, kneading, and stretching feel like daily rituals",
        "line_hook": "small paws turn comfort into work",
    },
]
PET_BODY_DETAIL_FOCUS = [
    "short front paws",
    "compact low body",
    "green eyes",
    "pink nose",
    "tabby patches",
    "raised tail",
    "soft white belly",
    "careful whiskers",
]
PET_VISUAL_MOTIFS = [
    "motion lines behind the pose",
    "small dream bubbles",
    "tail-swish arcs",
    "crumb-sized drama",
    "kneading arcs under the paws",
    "quiet e-paper stillness",
    "day-paper warmth",
    "deep-night contrast",
]


def _pet_state_lookup_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("_", " ").replace("-", " "))

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
    {
        "id": "belly_roll",
        "mood": "belly",
        "activity": "belly roll",
        "message": "Rolled belly-up because the room felt safe.",
        "delta": {"happiness": 5, "energy": -2, "cleanliness": -1, "xp": 3},
    },
    {
        "id": "tiny_zoomies",
        "mood": "zoomies",
        "activity": "tiny zoomies",
        "message": "Had six seconds of brave little chaos.",
        "delta": {"happiness": 6, "energy": -5, "food": -2, "xp": 4},
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

DAILY_LIFE_THEMES = [
    {"id": "cozy", "goal": "keep the nest warm and tidy", "tone": "soft"},
    {"id": "hunter", "goal": "find one tiny snack before night", "tone": "alert"},
    {"id": "curious", "goal": "inspect one strange thing on the screen", "tone": "curious"},
    {"id": "tidy", "goal": "make the pixel nest cleaner than yesterday", "tone": "careful"},
    {"id": "mischief", "goal": "play a harmless trick on its own shadow", "tone": "playful"},
    {"id": "dreamy", "goal": "collect a quiet thought for bedtime", "tone": "calm"},
]

DAILY_FAVORITES = [
    "warm cache nut",
    "bright pixel seed",
    "moonlit crumb",
    "static-crackle morsel",
    "soft corner of the screen",
    "sleepy refresh noise",
]

ROUTINE_EVENTS = {
    "morning": [
        {
            "id": "morning_nest_check",
            "mood": "alert",
            "activity": "nest check",
            "message": "Checked the nest corners before breakfast.",
            "delta": {"energy": -1, "happiness": 1, "xp": 2},
        },
        {
            "id": "face_wash",
            "mood": "grooming",
            "activity": "face wash",
            "message": "Washed its face with two careful paws.",
            "delta": {"cleanliness": 4, "energy": -1, "xp": 2},
        },
        {
            "id": "sunbeam_chase",
            "mood": "playful",
            "activity": "sun chase",
            "message": "Chased a sunbeam that may have been imaginary.",
            "delta": {"happiness": 4, "energy": -3, "food": -1, "xp": 3},
        },
    ],
    "midday": [
        {
            "id": "crumb_audit",
            "mood": "working",
            "activity": "crumb audit",
            "message": "Counted crumbs, then declared one of them interesting.",
            "delta": {"food": -1, "happiness": 2, "xp": 2},
        },
        {
            "id": "warm_screen_listen",
            "mood": "calm",
            "activity": "warm listening",
            "message": "Sat very still and listened to the warm screen.",
            "delta": {"energy": 1, "happiness": 1, "xp": 1},
        },
        {
            "id": "safe_belly_sprawl",
            "mood": "belly",
            "activity": "belly sprawl",
            "message": "Flopped belly-up in a very serious comfort test.",
            "delta": {"happiness": 5, "energy": -1, "cleanliness": -1, "xp": 3},
        },
    ],
    "afternoon": [
        {
            "id": "shadow_pounce",
            "mood": "playful",
            "activity": "shadow pounce",
            "message": "Practiced a tiny ambush on a harmless shadow.",
            "delta": {"happiness": 4, "energy": -4, "food": -1, "xp": 4},
        },
        {
            "id": "pixel_hoard",
            "mood": "exploring",
            "activity": "pixel hoarding",
            "message": "Dragged one bright pixel back to the nest.",
            "delta": {"energy": -2, "happiness": 2, "xp": 3},
        },
    ],
    "evening": [
        {
            "id": "dusk_groom",
            "mood": "grooming",
            "activity": "dusk grooming",
            "message": "Groomed until every pixel looked negotiable.",
            "delta": {"cleanliness": 5, "energy": -2, "xp": 2},
        },
        {
            "id": "nest_tuck",
            "mood": "calm",
            "activity": "nest tucking",
            "message": "Tucked the day's noises under the sleeping mat.",
            "delta": {"happiness": 2, "energy": 1, "xp": 2},
        },
    ],
    "night": [
        {
            "id": "cache_guard",
            "mood": "alert",
            "activity": "cache guarding",
            "message": "Curled around a warm cache and guarded it softly.",
            "delta": {"energy": 1, "happiness": 1, "xp": 2},
        },
        {
            "id": "soft_dream",
            "mood": "sleeping",
            "activity": "soft dreaming",
            "message": "Dreamed of catching a headline by the tail.",
            "delta": {"energy": 3, "happiness": 1, "xp": 1},
        },
    ],
}

THEME_EVENTS = {
    "cozy": [
        {
            "id": "blanket_knead",
            "mood": "calm",
            "activity": "blanket kneading",
            "message": "Kneaded the invisible blanket until the nest approved.",
            "delta": {"happiness": 3, "energy": -1, "xp": 2},
        },
    ],
    "hunter": [
        {
            "id": "snack_trail",
            "mood": "hunting",
            "activity": "snack tracking",
            "message": "Tracked a suspicious snack trail across three pixels.",
            "delta": {"food": 2, "energy": -3, "happiness": 2, "xp": 4},
        },
    ],
    "curious": [
        {
            "id": "strange_thing",
            "mood": "curious",
            "activity": "thing inspection",
            "message": "Inspected one strange thing and blinked twice at it.",
            "delta": {"happiness": 2, "energy": -1, "xp": 4},
        },
    ],
    "tidy": [
        {
            "id": "nest_sweep",
            "mood": "grooming",
            "activity": "nest sweeping",
            "message": "Swept the pixel nest into a better kind of messy.",
            "delta": {"cleanliness": 6, "energy": -2, "xp": 3},
        },
    ],
    "mischief": [
        {
            "id": "shadow_prank",
            "mood": "zoomies",
            "activity": "shadow prank",
            "message": "Set a trap for its shadow and immediately forgot it.",
            "delta": {"happiness": 5, "energy": -3, "food": -1, "xp": 4},
        },
    ],
    "dreamy": [
        {
            "id": "thought_collect",
            "mood": "calm",
            "activity": "thought collecting",
            "message": "Collected one quiet thought and hid it for bedtime.",
            "delta": {"happiness": 2, "energy": 1, "xp": 3},
        },
    ],
}

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


LOCALIZED_TEXT.setdefault("zh-Hans", {}).setdefault("moods", {})["hunting"] = "\u72e9\u730e"
LOCALIZED_TEXT.setdefault("zh-Hans", {}).setdefault("activity", {})["hunting"] = "\u51fa\u53bb\u72e9\u730e"
LOCALIZED_TEXT.setdefault("zh-Hans", {}).setdefault("activity", {})["stash meal"] = "\u5403\u50a8\u5907\u7cae"
LOCALIZED_TEXT.setdefault("zh-Hans", {}).setdefault("message", {})[
    "Autonomy: hunted a small meal and ate it."
] = "\u81ea\u4e3b\uff1a\u51fa\u53bb\u72e9\u730e\uff0c\u7136\u540e\u5403\u6389\u4e86\u81ea\u5df1\u627e\u5230\u7684\u98df\u7269\u3002"
LOCALIZED_TEXT.setdefault("zh-Hans", {}).setdefault("message", {})[
    "Autonomy: hunted a meal and stored the leftovers."
] = "\u81ea\u4e3b\uff1a\u51fa\u53bb\u72e9\u730e\uff0c\u5403\u9971\u540e\u628a\u5269\u4e0b\u7684\u98df\u7269\u5b58\u4e86\u8d77\u6765\u3002"
LOCALIZED_TEXT.setdefault("zh-Hans", {}).setdefault("message", {})[
    "Ate from yesterday's hunting stash."
] = "\u4ece\u6628\u5929\u7684\u72e9\u730e\u50a8\u5907\u91cc\u5403\u4e86\u4e00\u70b9\uff0c\u53c8\u80fd\u6491\u4e00\u4f1a\u513f\u3002"
LOCALIZED_TEXT.setdefault("zh-Hans", {}).setdefault("moods", {}).update({
    "zoomies": "\u6492\u6b22",
    "belly": "\u7ffb\u809a\u76ae",
})
LOCALIZED_TEXT.setdefault("zh-Hans", {}).setdefault("activity", {}).update({
    "belly roll": "\u7ffb\u809a\u76ae",
    "tiny zoomies": "\u7a81\u7136\u6492\u6b22",
    "nest check": "\u68c0\u67e5\u5c0f\u7a9d",
    "face wash": "\u6d17\u8138",
    "sun chase": "\u8ffd\u65e5\u5149",
    "crumb audit": "\u76d8\u70b9\u788e\u5c51",
    "warm listening": "\u542c\u6696\u5c4f",
    "belly sprawl": "\u653e\u677e\u644a\u5e73",
    "shadow pounce": "\u6251\u5f71\u5b50",
    "pixel hoarding": "\u85cf\u50cf\u7d20",
    "dusk grooming": "\u508d\u665a\u68b3\u6bdb",
    "nest tucking": "\u6574\u7406\u5c0f\u7a9d",
    "cache guarding": "\u5b88\u62a4\u7f13\u5b58",
    "soft dreaming": "\u8f7b\u8f7b\u505a\u68a6",
    "blanket kneading": "\u8e29\u9690\u5f62\u6bef\u5b50",
    "snack tracking": "\u8ffd\u8e2a\u96f6\u98df",
    "thing inspection": "\u68c0\u67e5\u5947\u602a\u4e1c\u897f",
    "nest sweeping": "\u6253\u626b\u5c0f\u7a9d",
    "shadow prank": "\u6349\u5f04\u5f71\u5b50",
    "thought collecting": "\u6536\u96c6\u5c0f\u5ff5\u5934",
    "self grooming": "\u81ea\u5df1\u68b3\u6bdb",
    "scheduled nap": "\u65e5\u7a0b\u5c0f\u7761",
    "bedtime curl": "\u7761\u524d\u56e2\u6210\u4e00\u5708",
})
LOCALIZED_TEXT.setdefault("zh-Hans", {}).setdefault("message", {}).update({
    "Rolled belly-up because the room felt safe.": "\u56e0\u4e3a\u89c9\u5f97\u5b89\u5168\uff0c\u7ffb\u8fc7\u6765\u6652\u4e86\u4e00\u4e0b\u5c0f\u809a\u76ae\u3002",
    "Had six seconds of brave little chaos.": "\u52c7\u6562\u5730\u6492\u6b22\u4e86\u516d\u79d2\uff0c\u7136\u540e\u88c5\u4f5c\u65e0\u4e8b\u53d1\u751f\u3002",
    "Checked the nest corners before breakfast.": "\u65e9\u996d\u524d\u5148\u68c0\u67e5\u4e86\u5c0f\u7a9d\u7684\u56db\u4e2a\u89d2\u843d\u3002",
    "Washed its face with two careful paws.": "\u7528\u4e24\u53ea\u5c0f\u722a\u5f88\u8ba4\u771f\u5730\u6d17\u4e86\u8138\u3002",
    "Chased a sunbeam that may have been imaginary.": "\u8ffd\u4e86\u4e00\u9053\u53ef\u80fd\u662f\u60f3\u8c61\u51fa\u6765\u7684\u65e5\u5149\u3002",
    "Counted crumbs, then declared one of them interesting.": "\u6570\u4e86\u4e00\u904d\u788e\u5c51\uff0c\u5e76\u5ba3\u5e03\u5176\u4e2d\u4e00\u9897\u5f88\u6709\u610f\u601d\u3002",
    "Sat very still and listened to the warm screen.": "\u5750\u5f97\u5f88\u9759\uff0c\u542c\u4e86\u4e00\u4f1a\u513f\u6696\u6696\u7684\u5c4f\u5e55\u3002",
    "Flopped belly-up in a very serious comfort test.": "\u4e25\u8083\u5730\u7ffb\u809a\u76ae\uff0c\u6d4b\u8bd5\u4eca\u5929\u8212\u4e0d\u8212\u670d\u3002",
    "Practiced a tiny ambush on a harmless shadow.": "\u5bf9\u4e00\u4e2a\u65e0\u5bb3\u7684\u5f71\u5b50\u7ec3\u4e60\u4e86\u5c0f\u578b\u4f0f\u51fb\u3002",
    "Dragged one bright pixel back to the nest.": "\u62d6\u4e86\u4e00\u9897\u4eae\u4eae\u7684\u50cf\u7d20\u56de\u5c0f\u7a9d\u3002",
    "Groomed until every pixel looked negotiable.": "\u68b3\u5230\u6bcf\u9897\u50cf\u7d20\u90fd\u663e\u5f97\u53ef\u4ee5\u5546\u91cf\u3002",
    "Tucked the day's noises under the sleeping mat.": "\u628a\u4eca\u5929\u7684\u58f0\u97f3\u90fd\u585e\u5230\u7761\u57ab\u4e0b\u9762\u3002",
    "Curled around a warm cache and guarded it softly.": "\u56f4\u7740\u4e00\u5757\u6696\u6696\u7684\u7f13\u5b58\u8725\u6210\u4e00\u5708\u5b88\u7740\u3002",
    "Dreamed of catching a headline by the tail.": "\u68a6\u89c1\u81ea\u5df1\u6293\u4f4f\u4e86\u4e00\u6761\u65b0\u95fb\u7684\u5c3e\u5df4\u3002",
    "Kneaded the invisible blanket until the nest approved.": "\u5bf9\u9690\u5f62\u5c0f\u6bef\u5b50\u8e29\u6765\u8e29\u53bb\uff0c\u76f4\u5230\u5c0f\u7a9d\u8868\u793a\u6ee1\u610f\u3002",
    "Tracked a suspicious snack trail across three pixels.": "\u6cbf\u7740\u53ef\u7591\u7684\u96f6\u98df\u8f68\u8ff9\uff0c\u8ffd\u8fc7\u4e86\u4e09\u9897\u50cf\u7d20\u3002",
    "Inspected one strange thing and blinked twice at it.": "\u68c0\u67e5\u4e86\u4e00\u4e2a\u5947\u602a\u4e1c\u897f\uff0c\u7136\u540e\u5bf9\u5b83\u7728\u4e86\u4e24\u4e0b\u773c\u3002",
    "Swept the pixel nest into a better kind of messy.": "\u628a\u50cf\u7d20\u5c0f\u7a9d\u6253\u626b\u6210\u4e86\u66f4\u597d\u7684\u4e71\u6cd5\u3002",
    "Set a trap for its shadow and immediately forgot it.": "\u7ed9\u81ea\u5df1\u7684\u5f71\u5b50\u8bbe\u4e86\u4e2a\u5708\u5957\uff0c\u7136\u540e\u7acb\u523b\u5fd8\u4e86\u3002",
    "Collected one quiet thought and hid it for bedtime.": "\u6536\u96c6\u4e86\u4e00\u4e2a\u5b89\u9759\u7684\u5c0f\u5ff5\u5934\uff0c\u7559\u5230\u7761\u524d\u7528\u3002",
    "Found a spare crumb and saved half for later.": "\u627e\u5230\u4e00\u9897\u591a\u51fa\u6765\u7684\u788e\u5c51\uff0c\u8fd8\u7559\u4e86\u534a\u9897\u665a\u70b9\u5403\u3002",
    "Cleaned one paw, then judged the floor.": "\u6e05\u7406\u4e86\u4e00\u53ea\u722a\u5b50\uff0c\u7136\u540e\u5bf9\u5730\u677f\u505a\u51fa\u8bc4\u4ef7\u3002",
    "Took a scheduled nap on the warmest pixel.": "\u8eba\u5728\u6700\u6696\u7684\u50cf\u7d20\u4e0a\uff0c\u5b8c\u6210\u4e86\u4eca\u5929\u7684\u65e5\u7a0b\u5c0f\u7761\u3002",
    "Curled up because its own schedule said so.": "\u56e0\u4e3a\u81ea\u5df1\u7684\u4f5c\u606f\u8bf4\u8be5\u7761\u4e86\uff0c\u5c31\u56e2\u6210\u4e86\u4e00\u5708\u3002",
})


def _clamp(value: int | float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def _blend_rgb(fg: tuple[int, int, int], bg: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    amount = min(max(float(amount), 0.0), 1.0)
    return tuple(int(round(f * amount + b * (1.0 - amount))) for f, b in zip(fg, bg))


def _theme_mode(theme: Any) -> str:
    if isinstance(theme, dict):
        return str(theme.get("mode") or "day").strip().lower()
    return str(theme or "day").strip().lower()


def _pet_palette(mood: str | None = None, theme: Any = None) -> dict[str, Any]:
    night = _theme_mode(theme) == "night"
    mood_key = str(mood or "").lower()
    if night:
        mood_accent = PET_NIGHT_MOOD_COLORS.get(mood_key, PET_NIGHT_BLUE)
        palette = {
            "mode": "night",
            "background": PET_NIGHT_PAPER,
            "panel": PET_NIGHT_PANEL,
            "panel_blue": PET_NIGHT_PANEL_BLUE,
            "panel_yellow": PET_NIGHT_PANEL_YELLOW,
            "panel_green": PET_NIGHT_PANEL_GREEN,
            "ink": PET_NIGHT_INK,
            "muted": PET_NIGHT_MUTED,
            "rule": PET_NIGHT_RULE,
            "border": PET_NIGHT_INK,
            "blue": PET_NIGHT_BLUE,
            "yellow": PET_NIGHT_YELLOW,
            "orange": PET_NIGHT_ORANGE,
            "red": PET_NIGHT_RED,
            "green": PET_NIGHT_GREEN,
            "purple": PET_NIGHT_PURPLE,
            "brown": PET_NIGHT_BROWN,
            "accent": mood_accent,
            "bar_colors": PET_NIGHT_BAR_COLORS,
            "badge_mix": 0.30,
            "bar_track_mix": 0.18,
            "face_back_mix": 0.16,
            "halftone_mix": 0.18,
        }
    else:
        mood_accent = PET_MOOD_COLORS.get(mood_key, PET_BLUE)
        palette = {
            "mode": "day",
            "background": PET_PAPER,
            "panel": PET_PANEL,
            "panel_blue": PET_PANEL_BLUE,
            "panel_yellow": PET_PANEL_YELLOW,
            "panel_green": PET_PANEL_GREEN,
            "ink": PET_INK,
            "muted": PET_MUTED,
            "rule": PET_RULE,
            "border": PET_INK,
            "blue": PET_BLUE,
            "yellow": PET_YELLOW,
            "orange": PET_ORANGE,
            "red": PET_RED,
            "green": PET_GREEN,
            "purple": PET_PURPLE,
            "brown": PET_BROWN,
            "accent": mood_accent,
            "bar_colors": PET_BAR_COLORS,
            "badge_mix": 0.18,
            "bar_track_mix": 0.10,
            "face_back_mix": 0.10,
            "halftone_mix": 0.20,
        }

    canonical = theme.get("palette") if isinstance(theme, dict) else None
    if not isinstance(canonical, dict):
        return palette

    panel = canonical["panel"]
    accent = canonical["accent"]
    muted = canonical["muted"]
    palette.update(
        {
            "background": canonical["background"],
            "panel": panel,
            "panel_blue": _blend_rgb(accent, panel, 0.10),
            "panel_yellow": _blend_rgb(accent, panel, 0.18),
            "panel_green": _blend_rgb(muted, panel, 0.14),
            "ink": canonical["ink"],
            "muted": muted,
            "rule": canonical["rule"],
            "border": canonical["ink"],
            "blue": accent,
            "yellow": _blend_rgb(accent, muted, 0.82),
            "orange": _blend_rgb(accent, muted, 0.72),
            "red": _blend_rgb(accent, muted, 0.92),
            "green": _blend_rgb(accent, muted, 0.58),
            "purple": _blend_rgb(accent, muted, 0.46),
            "brown": _blend_rgb(accent, muted, 0.34),
            "accent": accent,
            "bar_colors": {
                "food": _blend_rgb(accent, muted, 0.92),
                "happiness": _blend_rgb(accent, muted, 0.80),
                "energy": accent,
                "cleanliness": _blend_rgb(accent, muted, 0.62),
                "health": _blend_rgb(accent, muted, 0.48),
            },
        }
    )
    return palette


def _enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "on", "yes"}


def _parse_int(value: Any, default: int, low: int, high: int) -> int:
    return bounded_int(value, default, low, high)


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
        settings = settings or {}
        dimensions = self.get_dimensions(device_config)

        now = self._now(device_config)
        theme = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config, now=now)
        render_settings = dict(settings)
        render_settings["_inkypi_theme"] = theme
        state = self._load_state(settings, now)
        if _enabled(settings.get("_theme_render_only"), False):
            return self._render(dimensions, render_settings, state, now)

        changed = self._apply_elapsed(state, render_settings, now, device_config)
        if not changed and self._should_hunt_now(state, render_settings):
            changed = self._apply_autonomous_care(state, render_settings, now, device_config)
        if not changed and self._needs_initial_event(state):
            self._apply_autonomous_event(state, render_settings, now, steps=0, device_config=device_config)
            changed = True
        elif not changed and _enabled(render_settings.get("ai_dialogue"), False) and _enabled(render_settings.get("ai_each_render"), True):
            changed = self._maybe_generate_ai_message(state, render_settings, now, device_config)

        self._finalize_state(state, render_settings, now)
        if changed:
            self._save_state(render_settings, state)

        return self._render(dimensions, render_settings, state, now)

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
        return self.data_dir(leaf="pets", legacy_leaf=Path("cache") / "pets")

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
                    "food_reserve": 0,
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
            "food_reserve": 0,
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
        self._ensure_daily_life(state, now)
        return state

    def _ensure_daily_life(self, state: dict[str, Any], now: datetime) -> dict[str, Any]:
        today = now.strftime("%Y-%m-%d")
        current = state.get("daily_life") if isinstance(state.get("daily_life"), dict) else {}
        if current.get("date") == today:
            pet_key = str(state.get("pet_id") or state.get("name") or DEFAULT_PET_NAME)
            self._ensure_daily_visual_fields(current, pet_key, today)
            return current

        if current:
            history = state.get("daily_history") if isinstance(state.get("daily_history"), list) else []
            history.append({
                "date": current.get("date"),
                "theme": current.get("theme"),
                "favorite": current.get("favorite"),
                "ended_at": now.isoformat(),
            })
            state["daily_history"] = history[-7:]

        pet_key = str(state.get("pet_id") or state.get("name") or DEFAULT_PET_NAME)
        theme = self._stable_pick(DAILY_LIFE_THEMES, pet_key, today, "theme")
        favorite = self._stable_pick(DAILY_FAVORITES, pet_key, today, "favorite")
        wake_hour = 6 + self._stable_int(pet_key, today, "wake", modulo=3)
        nap_hour = 12 + self._stable_int(pet_key, today, "nap", modulo=5)
        bed_hour = 21 + self._stable_int(pet_key, today, "bed", modulo=3)
        daily = {
            "date": today,
            "theme": theme["id"],
            "goal": theme["goal"],
            "tone": theme["tone"],
            "favorite": favorite,
            "wake_hour": wake_hour,
            "nap_hour": nap_hour,
            "bed_hour": bed_hour,
            "curiosity": 1 + self._stable_int(pet_key, today, "curiosity", modulo=5),
            "boldness": 1 + self._stable_int(pet_key, today, "boldness", modulo=5),
        }
        self._ensure_daily_visual_fields(daily, pet_key, today)
        state["daily_life"] = daily
        state.setdefault("daily_event_counts", {})
        return daily

    def _ensure_daily_visual_fields(self, daily: dict[str, Any], pet_key: str, today: str) -> None:
        if not isinstance(daily.get("motion_theme"), dict):
            daily["motion_theme"] = self._stable_pick(PET_DAILY_MOTION_THEMES, pet_key, today, "motion_theme")
        if not daily.get("body_focus"):
            daily["body_focus"] = self._stable_pick(PET_BODY_DETAIL_FOCUS, pet_key, today, "body_focus")
        if not daily.get("visual_motif"):
            daily["visual_motif"] = self._stable_pick(PET_VISUAL_MOTIFS, pet_key, today, "visual_motif")
        if not isinstance(daily.get("pose_focus"), dict):
            pose_key = self._stable_pick(PET_POSE_KEYS, pet_key, today, "pose_focus")
            pose = PET_POSE_LIBRARY.get(pose_key, {})
            daily["pose_focus"] = {
                "key": pose_key,
                "label": pose.get("label", pose_key),
                "line_hook": pose.get("line_hook", ""),
            }

    def _stable_pick(self, values: list[Any], *parts: Any) -> Any:
        return values[self._stable_int(*parts, modulo=len(values))]

    def _stable_int(self, *parts: Any, modulo: int | None = None) -> int:
        seed = "|".join(str(part) for part in parts)
        digest = hashlib.blake2s(seed.encode("utf-8"), digest_size=4).hexdigest()
        value = int(digest, 16)
        return value % modulo if modulo else value

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
        self._ensure_daily_life(state, now)
        sleeping = self._is_sleeping(state, now)
        for _ in range(steps):
            food_loss = self._consume_food_reserve(state, profile["food"])
            stats["food"] = _clamp(stats["food"] - food_loss)
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
        if not self._apply_autonomous_care(state, settings, now, device_config) and not self._apply_daily_instinct(state, settings, now, device_config):
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
        level_info = self._level_info(state, settings)
        stats["level"] = level_info["level"]
        stats["food_reserve"] = min(max(0, int(stats.get("food_reserve", 0))), int(level_info["reserve_cap"]))
        state["mood"] = self._choose_mood(state, now)

    def _level_info(self, state: dict[str, Any], settings=None) -> dict[str, Any]:
        stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
        xp = max(0, int(stats.get("xp", 0)))
        level = max(1, xp // LEVEL_XP_STEP + 1)
        next_xp = level * LEVEL_XP_STEP
        tier = self._level_tier(level)
        reserve_days = self._reserve_days(state, settings or {})
        return {
            "level": level,
            "xp": xp,
            "next_xp": next_xp,
            "xp_to_next": max(0, next_xp - xp),
            "title": tier["title"],
            "prey_size": tier["prey_size"],
            "reserve_cap": int(tier["reserve_cap"]),
            "reserve": max(0, int(stats.get("food_reserve", 0))),
            "reserve_days": reserve_days,
            "next_prey_unlock": self._next_prey_unlock(level),
        }

    def _level_tier(self, level: int) -> dict[str, Any]:
        for tier in LEVEL_TIERS:
            if int(level) >= int(tier["min_level"]):
                return tier
        return LEVEL_TIERS[-1]

    def _next_prey_unlock(self, level: int) -> dict[str, Any]:
        future = [tier for tier in LEVEL_TIERS if int(tier["min_level"]) > int(level)]
        if not future:
            return {}
        tier = sorted(future, key=lambda item: int(item["min_level"]))[0]
        return {
            "level": int(tier["min_level"]),
            "prey_size": tier["prey_size"],
            "title": tier["title"],
        }

    def _reserve_days(self, state: dict[str, Any], settings) -> float:
        stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
        reserve = max(0, int(stats.get("food_reserve", 0)))
        tick_minutes = _parse_int((settings or {}).get("tick_minutes"), DEFAULT_TICK_MINUTES, 5, 240)
        profile = CARE_PROFILES.get((settings or {}).get("care_profile"), CARE_PROFILES["normal"])
        daily_food_need = max(1, int(profile["food"]) * max(1, 1440 // tick_minutes))
        return round(reserve / daily_food_need, 1)

    def _consume_food_reserve(self, state: dict[str, Any], amount: int) -> int:
        stats = state["stats"]
        need = max(0, int(amount))
        reserve = max(0, int(stats.get("food_reserve", 0)))
        if reserve <= 0 or need <= 0:
            stats["food_reserve"] = reserve
            return need
        used = min(reserve, need)
        stats["food_reserve"] = reserve - used
        return need - used

    def _add_food_with_reserve(self, state: dict[str, Any], food_gain: int, reserve_gain: int, reserve_cap: int) -> int:
        stats = state["stats"]
        before = int(stats.get("food", 0))
        direct_gain = max(0, int(food_gain))
        overflow = max(0, before + direct_gain - 100)
        stats["food"] = _clamp(before + direct_gain)
        reserve_before = max(0, int(stats.get("food_reserve", 0)))
        reserve_added = max(0, int(reserve_gain)) + overflow
        stats["food_reserve"] = min(max(0, int(reserve_cap)), reserve_before + reserve_added)
        return stats["food_reserve"] - reserve_before

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
        if stats["food"] < HUNTING_FOOD_THRESHOLD:
            if int(stats.get("food_reserve", 0)) > 0:
                self._eat_from_reserve(state, now)
                self._maybe_generate_ai_message(state, settings, now, device_config)
                return True
            self._apply_hunting_meal(state, now)
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

    def _eat_from_reserve(self, state: dict[str, Any], now: datetime) -> None:
        stats = state["stats"]
        reserve = max(0, int(stats.get("food_reserve", 0)))
        serving = min(reserve, max(10, 62 - int(stats.get("food", 0))))
        stats["food_reserve"] = reserve - serving
        stats["food"] = _clamp(int(stats.get("food", 0)) + serving)
        stats["happiness"] = _clamp(stats["happiness"] + 2)
        stats["health"] = _clamp(stats["health"] + 1)
        stats["xp"] = int(stats["xp"]) + 1
        state.pop("sleep_until", None)
        state["mood_hint"] = "hunting"
        state["activity"] = "stash meal"
        state["message"] = "Ate from yesterday's hunting stash."
        state["last_stash_meal"] = {
            "serving": int(serving),
            "reserve_after": int(stats.get("food_reserve", 0)),
            "at": now.isoformat(),
        }

    def _apply_daily_instinct(self, state: dict[str, Any], settings, now: datetime, device_config=None) -> bool:
        if not _enabled(settings.get("autonomous_care"), True):
            return False
        if self._is_sleeping(state, now):
            return False

        daily = self._ensure_daily_life(state, now)
        stats = state["stats"]
        hour = now.hour
        theme = str(daily.get("theme") or "")

        if stats["food"] < 42 and 6 <= hour < 20 and self._daily_gate(state, daily, "small_forage", 72):
            level_info = self._level_info(state, settings)
            self._add_food_with_reserve(state, 16, 6 + int(level_info["level"]), int(level_info["reserve_cap"]))
            stats["energy"] = _clamp(stats["energy"] - 4)
            stats["happiness"] = _clamp(stats["happiness"] + 2)
            stats["xp"] = int(stats["xp"]) + 4
            state.pop("sleep_until", None)
            state["mood_hint"] = "hunting"
            state["activity"] = "foraging"
            state["message"] = "Found a spare crumb and saved half for later."
            state["daily_life"]["last_instinct"] = "small_forage"
            self._maybe_generate_ai_message(state, settings, now, device_config)
            return True

        if stats["cleanliness"] < 48 and theme in {"tidy", "cozy"} and self._daily_gate(state, daily, "self_groom", 85):
            stats["cleanliness"] = _clamp(stats["cleanliness"] + 16)
            stats["energy"] = _clamp(stats["energy"] - 2)
            stats["happiness"] = _clamp(stats["happiness"] + 1)
            stats["xp"] = int(stats["xp"]) + 3
            state["mood_hint"] = "selfcare"
            state["activity"] = "self grooming"
            state["message"] = "Cleaned one paw, then judged the floor."
            state["daily_life"]["last_instinct"] = "self_groom"
            self._maybe_generate_ai_message(state, settings, now, device_config)
            return True

        nap_hour = int(daily.get("nap_hour") or 14)
        if nap_hour <= hour < nap_hour + 2 and stats["energy"] < 55 and self._daily_gate(state, daily, "daily_nap", 88):
            state["sleep_until"] = (now + timedelta(minutes=55)).isoformat()
            stats["happiness"] = _clamp(stats["happiness"] + 2)
            stats["xp"] = int(stats["xp"]) + 2
            state["activity"] = "scheduled nap"
            state["message"] = "Took a scheduled nap on the warmest pixel."
            state["daily_life"]["last_instinct"] = "daily_nap"
            self._maybe_generate_ai_message(state, settings, now, device_config)
            return True

        bed_hour = int(daily.get("bed_hour") or 22)
        if (hour >= bed_hour or hour < 5) and stats["energy"] < 68 and self._daily_gate(state, daily, "bedtime", 90):
            wake_hour = int(daily.get("wake_hour") or 7)
            wake_day = now.date() + timedelta(days=1 if hour >= bed_hour else 0)
            wake_at = now.replace(year=wake_day.year, month=wake_day.month, day=wake_day.day, hour=wake_hour, minute=0, second=0, microsecond=0)
            state["sleep_until"] = wake_at.isoformat()
            stats["happiness"] = _clamp(stats["happiness"] + 2)
            stats["xp"] = int(stats["xp"]) + 2
            state["activity"] = "bedtime curl"
            state["message"] = "Curled up because its own schedule said so."
            state["daily_life"]["last_instinct"] = "bedtime"
            self._maybe_generate_ai_message(state, settings, now, device_config)
            return True

        return False

    def _daily_gate(self, state: dict[str, Any], daily: dict[str, Any], key: str, chance: int) -> bool:
        date = str(daily.get("date") or "")
        event_key = f"{date}:{key}"
        counts = state.get("daily_event_counts") if isinstance(state.get("daily_event_counts"), dict) else {}
        if counts.get(event_key):
            state["daily_event_counts"] = counts
            return False
        roll = self._stable_int(state.get("pet_id") or state.get("name") or DEFAULT_PET_NAME, date, key, state.get("event_index") or 0, modulo=100)
        if roll >= chance:
            return False
        counts[event_key] = 1
        state["daily_event_counts"] = {k: v for k, v in counts.items() if str(k).startswith(date)}
        return True

    def _should_hunt_now(self, state: dict[str, Any], settings) -> bool:
        if not _enabled(settings.get("autonomous_care"), True):
            return False
        stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
        return int(stats.get("food", 100)) < HUNTING_FOOD_THRESHOLD

    def _apply_hunting_meal(self, state: dict[str, Any], now: datetime) -> None:
        stats = state["stats"]
        level_info = self._level_info(state)
        food = self._select_hunted_food(state, now)
        reserve_added = self._add_food_with_reserve(
            state,
            int(food["food_gain"]),
            int(food.get("reserve_gain", 0)),
            int(level_info["reserve_cap"]),
        )
        stats["energy"] = _clamp(stats["energy"] - int(food["energy_cost"]))
        stats["happiness"] = _clamp(stats["happiness"] + int(food["happiness_gain"]))
        stats["health"] = _clamp(stats["health"] + 2)
        stats["xp"] = int(stats["xp"]) + int(food["xp_gain"])
        state.pop("sleep_until", None)
        state["mood_hint"] = "hunting"
        state["activity"] = "hunting"
        state["message"] = "Autonomy: hunted a meal and stored the leftovers."
        state["last_hunt"] = {
            "id": food["id"],
            "food": food["food"],
            "food_zh": food.get("food_zh", ""),
            "size": food.get("size", "tiny"),
            "prey_group": food.get("prey_group", ""),
            "prey_mass_g": food.get("prey_mass_g", 0),
            "level": int(level_info["level"]),
            "title": level_info["title"],
            "food_gain": int(food["food_gain"]),
            "reserve_gain": int(food.get("reserve_gain", 0)),
            "reserve_added": int(reserve_added),
            "reserve_after": int(stats.get("food_reserve", 0)),
            "energy_cost": int(food["energy_cost"]),
            "at": now.isoformat(),
        }

    def _select_hunted_food(self, state: dict[str, Any], now: datetime) -> dict[str, Any]:
        level = int(self._level_info(state)["level"])
        available = self._available_hunted_foods(level)
        seed = "|".join([
            str(state.get("pet_id") or state.get("name") or DEFAULT_PET_NAME),
            str(state.get("last_tick_at") or ""),
            now.strftime("%Y-%m-%d-%H"),
            str(state.get("event_index") or 0),
            str(level),
        ])
        digest = hashlib.blake2s(seed.encode("utf-8"), digest_size=2).hexdigest()
        return available[int(digest, 16) % len(available)]

    def _available_hunted_foods(self, level: int) -> list[dict[str, Any]]:
        unlocked = [food for food in HUNTED_FOODS if int(food.get("level_min", 1)) <= int(level)]
        if not unlocked:
            return [HUNTED_FOODS[0]]
        tier = self._level_tier(level)
        current_rank = PREY_SIZE_RANK.get(str(tier.get("prey_size")), 0)
        min_rank = max(0, current_rank - 1)
        focused = [food for food in unlocked if PREY_SIZE_RANK.get(str(food.get("size")), 0) >= min_rank]
        return focused or unlocked

    def _prey_ai_item(self, food: dict[str, Any], settings) -> dict[str, Any]:
        return {
            "id": food.get("id") or "",
            "name": food.get("food_zh") if self._is_chinese(settings) and food.get("food_zh") else food.get("food") or "",
            "name_en": food.get("food") or "",
            "name_zh": food.get("food_zh") or "",
            "size": food.get("size") or "",
            "prey_group": food.get("prey_group") or "",
            "prey_mass_g": food.get("prey_mass_g"),
            "level_min": int(food.get("level_min", 1)),
            "food_gain": int(food.get("food_gain", 0)),
            "reserve_gain": int(food.get("reserve_gain", 0)),
            "energy_cost": int(food.get("energy_cost", 0)),
            "happiness_gain": int(food.get("happiness_gain", 0)),
            "xp_gain": int(food.get("xp_gain", 0)),
        }

    def _prey_ecology_context(self, state: dict[str, Any], settings, level_info: dict[str, Any]) -> dict[str, Any]:
        level = int(level_info.get("level") or 1)
        available = self._available_hunted_foods(level)
        future = sorted(
            [food for food in HUNTED_FOODS if int(food.get("level_min", 1)) > level],
            key=lambda food: (
                int(food.get("level_min", 1)),
                PREY_SIZE_RANK.get(str(food.get("size")), 0),
                str(food.get("id") or ""),
            ),
        )
        catalog = []
        for size in PREY_SIZE_ORDER:
            prey = [food for food in HUNTED_FOODS if food.get("size") == size]
            if not prey:
                continue
            catalog.append({
                "size": size,
                "min_level": min(int(food.get("level_min", 1)) for food in prey),
                "prey": [self._prey_ai_item(food, settings) for food in prey],
            })
        return {
            "rule": "The pet grows up the food web from tiny prey to huge prey; only unlocked prey may be treated as real catches.",
            "size_order": list(PREY_SIZE_ORDER),
            "current_title": level_info.get("title") or "",
            "current_prey_size": level_info.get("prey_size") or "tiny",
            "available_sizes": sorted(
                {str(food.get("size") or "") for food in available if food.get("size")},
                key=lambda value: PREY_SIZE_RANK.get(value, 0),
            ),
            "available_now": [self._prey_ai_item(food, settings) for food in available],
            "next_locked_prey": [self._prey_ai_item(food, settings) for food in future[:8]],
            "catalog": catalog,
        }

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
        events = self._event_catalog(settings, now, state)
        if not events:
            return

        event_index = int(state.get("event_index") or 0)
        event = self._choose_autonomous_event(events, state, now, steps)
        previous = state.get("last_event_key")
        if previous == event.get("id") and len(events) > 1:
            event = events[(events.index(event) + 1) % len(events)]

        self._apply_event_delta(state, event.get("delta", {}))
        state["event_index"] = event_index + max(1, steps)
        state["last_event_key"] = event.get("id", "")
        state["mood_hint"] = event.get("mood", "calm")
        state["activity"] = event.get("activity", "wandering")
        state["message"] = event.get("message", "Moved quietly between refreshes.")
        self._maybe_generate_ai_message(state, settings, now, device_config)

    def _choose_autonomous_event(self, events: list[dict[str, Any]], state: dict[str, Any], now: datetime, steps: int) -> dict[str, Any]:
        daily = self._ensure_daily_life(state, now)
        stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
        seed_parts = [
            state.get("pet_id") or state.get("name") or DEFAULT_PET_NAME,
            daily.get("date"),
            daily.get("theme"),
            self._time_band(now),
            int(state.get("event_index") or 0),
            max(1, steps),
            int(stats.get("food", 0)) // 10,
            int(stats.get("energy", 0)) // 10,
            int(stats.get("happiness", 0)) // 10,
        ]
        return events[self._stable_int(*seed_parts, modulo=len(events))]

    def _event_catalog(self, settings, now: datetime, state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        density = settings.get("event_density") or "expressive"
        if density == "quiet":
            events = list(QUIET_EVENTS)
        else:
            events = list(BASE_EVENTS)
            if density in {"normal", "expressive"}:
                events.extend(EXPRESSIVE_EVENTS)

        band = self._time_band(now)
        if state is not None:
            daily = self._ensure_daily_life(state, now)
            events.extend(ROUTINE_EVENTS.get(band, []))
            events.extend(THEME_EVENTS.get(str(daily.get("theme") or ""), []))

        if band == "morning":
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
        elif band == "night":
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

    def _time_band(self, now: datetime) -> str:
        hour = now.hour
        if 5 <= hour < 10:
            return "morning"
        if 10 <= hour < 14:
            return "midday"
        if 14 <= hour < 18:
            return "afternoon"
        if 18 <= hour < 22:
            return "evening"
        return "night"

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

        base_message = state.get("message", "")
        ambient_context = self._ambient_context(settings, now)
        prompt_settings = dict(settings or {})
        if device_config is not None and not isinstance(prompt_settings.get("_inkypi_theme"), dict):
            prompt_settings["_inkypi_theme"] = self.resolve_theme(prompt_settings, device_config, now=now)
        attempts: list[dict[str, Any]] = []
        fallback_from = ""
        fallback_reason = ""

        for index, backend in enumerate(backends):
            provider = backend["provider"]
            api_key = backend.get("api_key", "")
            model = backend.get("model", "")
            next_backend = backends[index + 1] if index + 1 < len(backends) else None
            if backend.get("fallback_from") and not fallback_from:
                fallback_from = str(backend.get("fallback_from") or "")
                fallback_reason = str(backend.get("fallback_reason") or "")

            if provider != "local":
                if daily_limit <= 0:
                    attempts.append({"provider": provider, "model": model, "status": "skipped", "reason": "daily_limit_disabled"})
                    if self._can_use_local_fallback(provider, next_backend):
                        fallback_from = provider
                        fallback_reason = "daily_limit_disabled"
                        continue
                    state["ai_message_status"] = "daily_limit_disabled"
                    state["ai_message_attempts"] = attempts[-4:]
                    return False
                if not self._reserve_ai_request(state, now, daily_limit):
                    attempts.append({"provider": provider, "model": model, "status": "skipped", "reason": "daily_limit_reached"})
                    if self._can_use_local_fallback(provider, next_backend):
                        fallback_from = provider
                        fallback_reason = "daily_limit_reached"
                        continue
                    state["ai_message_status"] = "daily_limit_reached"
                    state["ai_message_attempts"] = attempts[-4:]
                    return False

            try:
                generated = self._request_ai_message(
                    provider,
                    api_key,
                    model,
                    state,
                    prompt_settings,
                    now,
                    base_message,
                    ambient_context,
                )
                self._record_ai_provider_usage(state, now, provider)
                generated = self._clean_ai_message(generated, settings)
                attempts.append({"provider": provider, "model": model, "status": "response"})
                if not generated:
                    if next_backend:
                        fallback_from = provider
                        fallback_reason = "empty_response"
                        continue
                    state["ai_message_status"] = "empty_response"
                    state["ai_message_attempts"] = attempts[-4:]
                    return False

                fingerprint = self._message_fingerprint(generated)
                if fingerprint in set(state.get("ai_message_fingerprints", [])):
                    if next_backend:
                        fallback_from = provider
                        fallback_reason = "duplicate_rejected"
                        continue
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
                if self._should_use_fallback(provider, exc, next_backend):
                    fallback_from = provider
                    fallback_reason = reason
                    logger.warning("AI provider failed; trying fallback provider: %s", reason)
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
                backends.append(self._local_ai_backend())
            else:
                backends.append(self._local_ai_backend("groq", "missing_groq_key"))
            return backends

        if provider == "groq":
            groq_key = self._load_env_key(device_config, "GROQ_API_KEY")
            if groq_key:
                return [{
                    "provider": "groq",
                    "api_key": groq_key,
                    "model": settings.get("ai_groq_model") or settings.get("ai_text_model") or DEFAULT_GROQ_TEXT_MODEL,
                }, self._local_ai_backend()]
            return [self._local_ai_backend("groq", "missing_groq_key")]

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

    def _local_ai_backend(self, fallback_from: str = "", fallback_reason: str = "") -> dict[str, str]:
        backend = {
            "provider": "local",
            "api_key": "",
            "model": LOCAL_AI_TEXT_MODEL,
        }
        if fallback_from:
            backend["fallback_from"] = fallback_from
            backend["fallback_reason"] = fallback_reason
        return backend

    def _can_use_local_fallback(self, provider: str, next_backend: dict[str, str] | None) -> bool:
        return provider == "groq" and bool(next_backend) and next_backend.get("provider") == "local"

    def _should_use_fallback(self, provider: str, exc: Exception, next_backend: dict[str, str] | None) -> bool:
        if provider != "groq" or not next_backend:
            return False
        if next_backend.get("provider") == "local":
            return True
        if next_backend.get("provider") != "openai":
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

    def _pose_library_context(self) -> list[dict[str, str]]:
        return [
            {
                "key": key,
                "label": str(value.get("label") or key),
                "body_language": str(value.get("body_language") or ""),
                "line_hook": str(value.get("line_hook") or ""),
            }
            for key, value in PET_POSE_LIBRARY.items()
        ]

    def _visual_state_context(self, state: dict[str, Any], settings, daily_life: dict[str, Any]) -> dict[str, Any]:
        mood_id = state.get("mood_hint") or state.get("mood") or "calm"
        activity_id = state.get("activity", "")
        pose_key = self._resolve_state_image_key(mood_id, activity_id)
        pose = PET_POSE_LIBRARY.get(pose_key, PET_POSE_LIBRARY["calm"])
        activity_pose_key = PET_ACTIVITY_IMAGE_MAP.get(_pet_state_lookup_key(activity_id))
        theme_context = settings.get("_inkypi_theme") if isinstance(settings, dict) else {}
        theme_mode = _theme_mode(theme_context)
        return {
            "identity": dict(PET_PHYSICAL_IDENTITY),
            "current_pose": {
                "key": pose_key,
                "asset": f"{pose_key}.png",
                "source": "activity" if activity_pose_key else "mood",
                "activity_id": activity_id,
                "mood_id": mood_id,
                "label": pose.get("label", pose_key),
                "body_language": pose.get("body_language", ""),
                "line_hook": pose.get("line_hook", ""),
            },
            "today": {
                "motion_theme": daily_life.get("motion_theme") or {},
                "body_focus": daily_life.get("body_focus") or "",
                "visual_motif": daily_life.get("visual_motif") or "",
                "pose_focus": daily_life.get("pose_focus") or {},
            },
            "render_style": {
                "mode": theme_mode,
                "day": "warm paper background with comic process-color accents",
                "night": "deep black panels with bright e-paper contrast accents",
            },
            "pose_library": self._pose_library_context(),
        }

    def _life_context(self, state: dict[str, Any], settings, now: datetime, base_message: str) -> dict[str, Any]:
        stats = state.get("stats", {})
        values = {
            "food": int(stats.get("food", 0)),
            "happiness": int(stats.get("happiness", 0)),
            "energy": int(stats.get("energy", 0)),
            "cleanliness": int(stats.get("cleanliness", 0)),
            "health": int(stats.get("health", 0)),
            "food_reserve": int(stats.get("food_reserve", 0)),
            "level": int(stats.get("level", 1)),
            "age_days": int(stats.get("age_days", 0)),
        }
        level_info = self._level_info(state, settings)
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

        time_band = self._time_band(now)

        mood_id = state.get("mood_hint") or state.get("mood") or "calm"
        last_hunt = state.get("last_hunt") if isinstance(state.get("last_hunt"), dict) else {}
        daily_life = self._ensure_daily_life(state, now)
        state_notes = [item["hint"] for item in priorities[:3]] or ["healthy enough to focus on the current small activity"]
        if state.get("activity") == "hunting":
            state_notes.insert(0, "the pet hunted for food and ate what it found; keep it non-graphic")
        return {
            "stats": values,
            "time_band": time_band,
            "sleeping": self._is_sleeping(state, now),
            "mood_id": mood_id,
            "mood": self._mood_label(mood_id, settings),
            "activity_id": state.get("activity", ""),
            "activity": self._activity_text(settings, state.get("activity", "")),
            "base_event": self._message_text(settings, base_message),
            "daily_life": {
                "theme": daily_life.get("theme"),
                "goal": daily_life.get("goal"),
                "tone": daily_life.get("tone"),
                "favorite": daily_life.get("favorite"),
                "wake_hour": daily_life.get("wake_hour"),
                "nap_hour": daily_life.get("nap_hour"),
                "bed_hour": daily_life.get("bed_hour"),
                "curiosity": daily_life.get("curiosity"),
                "boldness": daily_life.get("boldness"),
                "last_instinct": daily_life.get("last_instinct") or "",
                "motion_theme": daily_life.get("motion_theme") or {},
                "body_focus": daily_life.get("body_focus") or "",
                "visual_motif": daily_life.get("visual_motif") or "",
                "pose_focus": daily_life.get("pose_focus") or {},
            },
            "visual_state": self._visual_state_context(state, settings, daily_life),
            "level_system": level_info,
            "prey_ecology": self._prey_ecology_context(state, settings, level_info),
            "care_priority": priorities[:5],
            "top_priority": top_priority,
            "state_notes": state_notes,
            "last_hunt": {
                "food": last_hunt.get("food") or "",
                "food_label": last_hunt.get("food_zh") if self._is_chinese(settings) and last_hunt.get("food_zh") else last_hunt.get("food") or "",
                "food_zh": last_hunt.get("food_zh") or "",
                "size": last_hunt.get("size") or "",
                "prey_group": last_hunt.get("prey_group") or "",
                "prey_mass_g": last_hunt.get("prey_mass_g"),
                "level": last_hunt.get("level"),
                "title": last_hunt.get("title") or "",
                "food_gain": last_hunt.get("food_gain"),
                "reserve_gain": last_hunt.get("reserve_gain"),
                "reserve_added": last_hunt.get("reserve_added"),
                "reserve_after": last_hunt.get("reserve_after"),
                "at": last_hunt.get("at") or "",
            } if last_hunt else {},
        }

    def _ambient_context(self, settings, now: datetime) -> dict[str, Any]:
        if not _enabled(settings.get("ai_use_plugin_context"), True):
            return {"available": False, "reason": "disabled", "sources": []}

        from plugins.context_cache import read_contexts

        max_age_hours = _parse_int(settings.get("ai_context_max_age_hours"), 24, 1, 72)
        max_items = _parse_int(settings.get("ai_context_max_items"), DEFAULT_CONTEXT_MAX_ITEMS, 0, 24)
        max_sources = _parse_int(settings.get("ai_context_max_sources"), 12, 1, 24)
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
        for entry in entries[:max_sources]:
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
                for item in self._context_items_from_payload(payload)[:remaining_items]:
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
            return list(DEFAULT_CONTEXT_PLUGIN_IDS)
        values = [part.strip() for part in re.split(r"[,;\s]+", raw) if part.strip()]
        return values or None

    def _context_items_from_payload(self, payload: dict[str, Any]) -> list[Any]:
        raw_items = payload.get("items")
        items = list(raw_items) if isinstance(raw_items, list) else []
        for key in ("live", "replay", "forecast", "recent_games", "games", "photos", "covers"):
            value = payload.get(key)
            if isinstance(value, list):
                items.extend(value)
        return items

    def _context_item(self, item: Any) -> dict[str, str]:
        if isinstance(item, dict):
            result = {}
            for key in (
                "title",
                "why",
                "summary",
                "name",
                "source",
                "publication",
                "date",
                "word",
                "definition",
                "example",
                "author",
                "line",
                "caption",
                "rank",
                "appid",
                "secondary_name",
                "current_players",
                "peak_players",
                "change_24h",
                "peak_time",
                "platform",
                "owner",
                "heat",
                "filename",
                "page_url",
                "rotation_key",
                "two_week_hours",
                "total_hours",
            ):
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

    def _ai_prompt_context(
        self,
        state: dict[str, Any],
        settings,
        now: datetime,
        base_message: str,
        ambient_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        language = self._language(settings)
        language_name = "Simplified Chinese" if language == "zh-Hans" else "English"
        life_context = self._life_context(state, settings, now, base_message)
        recent_messages = state.get("ai_recent_messages", [])[-12:]
        if not isinstance(recent_messages, list):
            recent_messages = []
        ambient = ambient_context or {"available": False, "sources": []}
        variation = self._dialogue_variation(state, now, life_context, ambient, recent_messages)
        return {
            "pet_name": state.get("name", DEFAULT_PET_NAME),
            "personality": settings.get("personality", ""),
            "language": language_name,
            "time": now.strftime("%Y-%m-%d %H:%M"),
            "life": life_context,
            "ambient": ambient,
            "variation": variation,
            "chat_style": settings.get("ai_chat_style") or "wry",
            "recent_lines_to_avoid": recent_messages,
        }

    def _dialogue_variation(
        self,
        state: dict[str, Any],
        now: datetime,
        life_context: dict[str, Any],
        ambient_context: dict[str, Any],
        recent_messages: list[Any],
    ) -> dict[str, Any]:
        stats = life_context.get("stats") if isinstance(life_context.get("stats"), dict) else {}
        last_hunt = life_context.get("last_hunt") if isinstance(life_context.get("last_hunt"), dict) else {}
        prey_context = life_context.get("prey_ecology") if isinstance(life_context.get("prey_ecology"), dict) else {}
        available_prey = prey_context.get("available_now") if isinstance(prey_context.get("available_now"), list) else []
        locked_prey = prey_context.get("next_locked_prey") if isinstance(prey_context.get("next_locked_prey"), list) else []
        visual_state = life_context.get("visual_state") if isinstance(life_context.get("visual_state"), dict) else {}
        pose_library = visual_state.get("pose_library") if isinstance(visual_state.get("pose_library"), list) else []
        visual_today = visual_state.get("today") if isinstance(visual_state.get("today"), dict) else {}
        ambient_sources = (ambient_context or {}).get("sources") if isinstance((ambient_context or {}).get("sources"), list) else []
        seed = "|".join([
            str(state.get("pet_id") or state.get("name") or DEFAULT_PET_NAME),
            now.strftime("%Y-%m-%dT%H:%M:%S"),
            str(state.get("event_index") or 0),
            str(state.get("activity") or ""),
            str(state.get("mood") or state.get("mood_hint") or ""),
            json.dumps(stats, sort_keys=True, separators=(",", ":")),
            str(last_hunt.get("id") or ""),
            str(len(recent_messages)),
        ])
        digest = hashlib.blake2s(seed.encode("utf-8"), digest_size=16).digest()
        last_variation = state.get("ai_last_variation") if isinstance(state.get("ai_last_variation"), dict) else {}

        def pick(options: list[Any], offset: int, avoid: Any = None) -> Any:
            choices = [item for item in options if item != avoid] or list(options)
            return choices[digest[offset % len(digest)] % len(choices)]

        primary = pick(AI_DIALOGUE_ANGLES, 0, last_variation.get("primary_angle"))
        secondary = pick(AI_DIALOGUE_ANGLES, 1, primary)
        prey_focus = pick(available_prey, 5) if available_prey else {}
        locked_prey_focus = pick(locked_prey, 6) if locked_prey else {}
        ambient_focus = pick(ambient_sources, 7) if ambient_sources else {}
        weakest_stat = ""
        if stats:
            weakest_stat = min(
                ("food", "happiness", "energy", "cleanliness", "health"),
                key=lambda key: int(stats.get(key, 100)),
            )

        return {
            "novelty_seed": hashlib.blake2s(seed.encode("utf-8"), digest_size=4).hexdigest(),
            "primary_angle": primary,
            "secondary_angle": secondary,
            "line_shape": pick(AI_LINE_SHAPES, 2, last_variation.get("line_shape")),
            "tone_color": pick(AI_TONE_COLORS, 3, last_variation.get("tone_color")),
            "detail_lens": pick(AI_DETAIL_LENSES, 4, last_variation.get("detail_lens")),
            "prey_focus": prey_focus,
            "locked_prey_focus": locked_prey_focus,
            "ambient_focus": ambient_focus,
            "pose_focus": pick(pose_library, 8, last_variation.get("pose_focus")) if pose_library else {},
            "daily_motion_theme": visual_today.get("motion_theme") or {},
            "daily_body_focus": visual_today.get("body_focus") or "",
            "daily_visual_motif": visual_today.get("visual_motif") or "",
            "daily_pose_focus": visual_today.get("pose_focus") or {},
            "weakest_stat": weakest_stat,
            "must_consider": [
                "stats",
                "top_priority",
                "care_priority",
                "daily_life",
                "visual_state",
                "pose_library",
                "level_system",
                "prey_ecology",
                "last_hunt",
                "ambient",
                "recent_lines_to_avoid",
            ],
        }

    def _ai_length_rule(self, language: str) -> str:
        if language == "zh-Hans":
            return (
                "Write exactly one natural Simplified Chinese sentence, 12 to 30 Chinese characters. "
                "Mostly use Simplified Chinese, but a short natural English word is acceptable when it fits the pet's voice."
            )
        return "Write exactly one natural English sentence, 6 to 14 words."

    def _ai_system_content(self, language: str) -> str:
        length_rule = self._ai_length_rule(language)
        return (
            "You write tiny dialogue lines for a Tamagotchi-like e-paper pet. "
            "The pet has no buttons and expresses itself through a state image, mood, activity, and one log line. "
            "You must make the line feel state-aware and strongly varied, never like a fixed status template. "
            "Always inspect variation.must_consider before writing: stats, top_priority, care_priority, daily_life, visual_state, pose_library, level_system, prey_ecology, last_hunt, ambient, and recent_lines_to_avoid. "
            "Follow variation.primary_angle first, then optionally variation.secondary_angle; use variation.line_shape, tone_color, detail_lens, pose_focus, daily_motion_theme, daily_body_focus, daily_visual_motif, daily_pose_focus, prey_focus, locked_prey_focus, ambient_focus, and weakest_stat to make this line distinct from recent lines. "
            "prey_ecology contains the full prey catalog, current available prey, next locked prey, names, ecological group, mass, unlock level, food gain, reserve gain, energy cost, happiness gain, and XP gain. "
            "visual_state contains the real cat identity, visible current pose, all available transparent pose images, today's motion theme, body focus, visual motif, and day/night render style. "
            "The cat is a white and gray-black tabby with green eyes, a pink nose, a compact low body, and short front legs; you may use those physical details when they match the current pose. "
            "Do not cram every field into one sentence; pick one surprising slice while still respecting all provided facts. "
            "daily_life is the pet's own plan for today: theme, goal, favorite, wake hour, nap hour, bedtime, curiosity, boldness, motion theme, body focus, visual motif, and pose focus. "
            "level_system describes survival growth: higher levels unlock larger prey, larger reserve capacity, and more stored food days. "
            "If ambient.available is true, naturally connect the line to exactly one fresh ambient source such as weather, news, Steam promotion, Steam activity, live streams, game charts, a daily word or poem, space/photo sources, magazine covers, comics, or Wikipedia photos. "
            "Use only facts present in ambient; do not invent headlines, prices, forecasts, game names, or current events. "
            "Follow chat_style: soft means gentle small talk, wry means dry wit, black_humor means mild dark humor without cruelty or graphic content, aphorism means a compact memorable line. "
            "If life.top_priority.severity is 3 or higher, the line must naturally imply that need. "
            "If life.activity_id is hunting or life.last_hunt exists, you may mention the actual hunt, prey size, prey name, or stored leftovers without making it graphic. "
            "If no hunt just happened, treat prey details as instinct, smell, memory, plan, dream, or ambition, not a new catch. "
            "If all needs are mild, rotate among current activity, visible body pose, time of day, daily plan, level ambition, prey ecology, ambient world data, and personality. "
            "Do not claim the image is truly animated; treat motion as a still pose, printed motion mark, or change between refreshes. "
            "Do not contradict the stats; for example, do not sound energetic when energy is low, or full when food is low. "
            f"{length_rule} "
            "Do not use emoji, markdown, labels, quotes, brackets, or line breaks. "
            "Do not mention OpenAI, prompts, APIs, models, or being generated. "
            "Do not repeat or closely paraphrase any recent line. "
            "Keep the voice quiet, alive, slightly playful, and suitable for an e-paper screen."
        )

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
        if provider == "local":
            return self._local_ai_message(state, settings, now, base_message, ambient_context)

        from openai import OpenAI

        language = self._language(settings)
        context = self._ai_prompt_context(state, settings, now, base_message, ambient_context)
        state["ai_last_variation"] = context.get("variation", {})
        system_content = self._ai_system_content(language)
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
            temperature=1.25,
            max_tokens=90,
        )
        return (response.choices[0].message.content or "").strip()

    def _local_ai_message(
        self,
        state: dict[str, Any],
        settings,
        now: datetime,
        base_message: str,
        ambient_context: dict[str, Any] | None = None,
    ) -> str:
        context = self._ai_prompt_context(state, settings, now, base_message, ambient_context)
        state["ai_last_variation"] = context.get("variation", {})
        life = context.get("life") if isinstance(context.get("life"), dict) else {}
        variation = context.get("variation") if isinstance(context.get("variation"), dict) else {}
        seed = str(variation.get("novelty_seed") or now.strftime("%H%M%S"))
        if self._is_chinese(settings):
            options = self._local_ai_message_options_zh(life, ambient_context or {})
        else:
            options = self._local_ai_message_options_en(life, ambient_context or {})
        options = [item for item in options if item]
        if not options:
            return str(base_message or "")
        return options[int(seed, 16) % len(options)]

    def _local_ai_message_options_zh(self, life: dict[str, Any], ambient_context: dict[str, Any]) -> list[str]:
        stats = life.get("stats") if isinstance(life.get("stats"), dict) else {}
        top_priority = life.get("top_priority") if isinstance(life.get("top_priority"), dict) else {}
        last_hunt = life.get("last_hunt") if isinstance(life.get("last_hunt"), dict) else {}
        prey = life.get("prey_ecology") if isinstance(life.get("prey_ecology"), dict) else {}
        available_prey = prey.get("available_now") if isinstance(prey.get("available_now"), list) else []
        visual = life.get("visual_state") if isinstance(life.get("visual_state"), dict) else {}
        current_pose = visual.get("current_pose") if isinstance(visual.get("current_pose"), dict) else {}
        ambient_sources = ambient_context.get("sources") if isinstance(ambient_context.get("sources"), list) else []
        activity = str(life.get("activity") or life.get("activity_id") or "\u5de1\u89c6")
        time_band = {
            "morning": "\u65e9\u6668",
            "midday": "\u6b63\u5348",
            "afternoon": "\u5348\u540e",
            "evening": "\u508d\u665a",
            "night": "\u591c\u91cc",
        }.get(str(life.get("time_band") or ""), "\u4eca\u5929")

        metric = str(top_priority.get("metric") or "")
        options: list[str] = []
        need_lines = {
            "food": "\u809a\u5b50\u54cd\u6210\u5c0f\u949f\uff0c\u5b83\u628a\u997f\u5199\u5f97\u5f88\u542b\u84c4\u3002",
            "energy": "\u7535\u91cf\u4f4e\u5230\u50cf\u7eb8\uff0c\u5148\u628a\u5f71\u5b50\u6536\u8d77\u6765\u3002",
            "cleanliness": "\u7070\u5c18\u5728\u6bdb\u8fb9\u6392\u961f\uff0c\u5b83\u5047\u88c5\u6ca1\u770b\u89c1\u3002",
            "happiness": "\u5c4f\u5e55\u5f88\u5b89\u9759\uff0c\u5b83\u628a\u60f3\u5ff5\u538b\u6210\u4e00\u884c\u3002",
            "health": "\u4eca\u5929\u7684\u811a\u6b65\u653e\u8f7b\uff0c\u5065\u5eb7\u5148\u6162\u6162\u8865\u4e01\u3002",
        }
        if int(top_priority.get("severity") or 0) >= 3 and metric in need_lines:
            options.append(need_lines[metric])

        if last_hunt:
            food = str(last_hunt.get("food_label") or last_hunt.get("food") or "\u730e\u7269")
            options.append(f"{food}\u8fd8\u5728\u68a6\u91cc\u53d1\u54cd\uff0c\u50a8\u7cae\u8868\u793a\u7406\u89e3\u3002")
        if available_prey:
            focus = available_prey[0] if isinstance(available_prey[0], dict) else {}
            food = str(focus.get("food_label") or focus.get("food_zh") or focus.get("food") or "\u5c0f\u730e\u7269")
            options.append(f"\u5b83\u628a{food}\u5199\u8fdb\u5c0f\u672c\uff0c\u7559\u7ed9\u4e0b\u6b21\u5237\u65b0\u3002")

        reserve = int(stats.get("food_reserve") or 0)
        level = int(stats.get("level") or 1)
        pose_key = str(current_pose.get("key") or "")
        pose_label = {
            "alert": "\u8b66\u89c9\u7ad9\u59ff",
            "alert_listening": "\u7ad6\u8033\u5077\u542c",
            "belly": "\u9732\u809a\u76ae",
            "calm": "\u5b89\u9759\u63e3\u624b",
            "curious": "\u597d\u5947\u89c2\u5bdf",
            "dreaming": "\u8737\u7740\u505a\u68a6",
            "grooming": "\u8ba4\u771f\u6d17\u8138",
            "happy": "\u5f00\u5fc3\u7aef\u5750",
            "hungry": "\u997f\u997f\u7aef\u5750",
            "hunting": "\u4f4e\u8eab\u6f5c\u884c",
            "kneading": "\u8e29\u5976\u5de5\u4f5c",
            "playful": "\u51c6\u5907\u5f00\u73a9",
            "pounce": "\u5c0f\u578b\u4f0f\u51fb",
            "sleeping": "\u8737\u6210\u4e00\u56e2",
            "snacking": "\u62b1\u7740\u96f6\u98df",
            "stretch": "\u90d1\u91cd\u4f38\u5c55",
            "tail_swish": "\u5c3e\u5df4\u63d0\u95ee",
            "tired": "\u56f0\u56f0\u7aef\u5750",
            "unwell": "\u4f4e\u4f4e\u4f11\u606f",
            "zoomies": "\u77ed\u817f\u51b2\u523a",
        }.get(pose_key, "\u5f53\u524d\u59ff\u52bf")
        options.extend([
            f"\u50a8\u7cae{reserve}\u683c\u7684\u5e95\u6c14\uff0c\u591f\u5b83\u51b7\u9759\u4e00\u4e0b\u3002",
            f"\u5b83\u5728{activity}\u91cc\u7f29\u6210\u5c0f\u9017\u53f7\uff0c\u4e0d\u6025\u7740\u7ed3\u675f\u3002",
            f"\u77ed\u524d\u722a\u6309\u4f4f\u4eca\u5929\uff0c{pose_label}\u8d1f\u8d23\u88c5\u9177\u3002",
            f"\u7b49\u7ea7{level}\u7684\u91ce\u5fc3\u5f88\u5c0f\uff0c\u5148\u4ece\u4e00\u53e3\u6c14\u5f00\u59cb\u3002",
            f"\u5b83\u628a{time_band}\u53e0\u597d\uff0c\u585e\u8fdb\u4e0b\u4e00\u6b21\u5237\u65b0\u91cc\u3002",
            "\u5c0f\u5c3e\u5df4\u626b\u8fc7\u7eb8\u9762\uff0c\u4eca\u5929\u6682\u65f6\u5f52\u6863\u4e3a\u5e73\u9759\u3002",
        ])
        if ambient_sources:
            source = ambient_sources[0] if isinstance(ambient_sources[0], dict) else {}
            name = str(source.get("plugin") or source.get("source") or "\u5916\u9762")
            options.append(f"\u542c\u5230{name}\u7684\u65b0\u9c9c\u4e8b\uff0c\u5b83\u53ea\u52a8\u4e86\u4e00\u4e0b\u8033\u6735\u3002")
        return options

    def _local_ai_message_options_en(self, life: dict[str, Any], ambient_context: dict[str, Any]) -> list[str]:
        stats = life.get("stats") if isinstance(life.get("stats"), dict) else {}
        top_priority = life.get("top_priority") if isinstance(life.get("top_priority"), dict) else {}
        last_hunt = life.get("last_hunt") if isinstance(life.get("last_hunt"), dict) else {}
        prey = life.get("prey_ecology") if isinstance(life.get("prey_ecology"), dict) else {}
        available_prey = prey.get("available_now") if isinstance(prey.get("available_now"), list) else []
        visual = life.get("visual_state") if isinstance(life.get("visual_state"), dict) else {}
        current_pose = visual.get("current_pose") if isinstance(visual.get("current_pose"), dict) else {}
        ambient_sources = ambient_context.get("sources") if isinstance(ambient_context.get("sources"), list) else []
        activity = str(life.get("activity") or life.get("activity_id") or "watching")
        time_band = str(life.get("time_band") or "today")

        metric = str(top_priority.get("metric") or "")
        need_lines = {
            "food": "Its stomach rings softly; hunger stays politely documented.",
            "energy": "Power is thin, so the shadow folds itself smaller.",
            "cleanliness": "Dust queues at the fur edge; it pretends not to see.",
            "happiness": "The screen is quiet, and wanting attention fits on one line.",
            "health": "Today it walks carefully and lets health patch itself slowly.",
        }
        options: list[str] = []
        if int(top_priority.get("severity") or 0) >= 3 and metric in need_lines:
            options.append(need_lines[metric])
        if last_hunt:
            food = str(last_hunt.get("food_label") or last_hunt.get("food") or "the catch")
            options.append(f"{food} still echoes in its dream inventory.")
        if available_prey:
            focus = available_prey[0] if isinstance(available_prey[0], dict) else {}
            food = str(focus.get("food") or "small prey")
            options.append(f"It files {food} under plans for the next refresh.")

        reserve = int(stats.get("food_reserve") or 0)
        level = int(stats.get("level") or 1)
        pose_label = str(current_pose.get("label") or "the current pose")
        options.extend([
            f"{reserve} reserve points are enough confidence for one calm minute.",
            f"It turns {activity} into a small comma and stays there.",
            f"Short front paws hold today down; {pose_label} handles the attitude.",
            f"Level {level} ambition starts with one carefully budgeted breath.",
            f"It folds {time_band} into the next screen refresh.",
            "The tail sweeps the paper; today is filed under quiet.",
        ])
        if ambient_sources:
            source = ambient_sources[0] if isinstance(ambient_sources[0], dict) else {}
            name = str(source.get("plugin") or source.get("source") or "outside")
            options.append(f"It hears {name} update and moves one ear.")
        return options

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
        parsed = self._parse_time(sleep_until, now)
        if parsed.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=parsed.tzinfo)
        elif parsed.tzinfo is None and now.tzinfo is not None:
            parsed = parsed.replace(tzinfo=now.tzinfo)
        return parsed > now

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
        daily_life = state.get("daily_life") if isinstance(state.get("daily_life"), dict) else {}
        level_info = self._level_info(state, settings or {})
        return {
            "name": state.get("name", DEFAULT_PET_NAME),
            "mood": label,
            "mood_id": mood,
            "face": face,
            "activity": self._activity_text(settings or {}, state.get("activity", "")),
            "message": self._message_text(settings or {}, state.get("message", "")),
            "daily_life": {
                "theme": daily_life.get("theme") or "",
                "goal": daily_life.get("goal") or "",
                "favorite": daily_life.get("favorite") or "",
                "wake_hour": daily_life.get("wake_hour"),
                "nap_hour": daily_life.get("nap_hour"),
                "bed_hour": daily_life.get("bed_hour"),
            },
            "level_system": level_info,
            "stats": {
                "food": int(stats["food"]),
                "happiness": int(stats["happiness"]),
                "energy": int(stats["energy"]),
                "cleanliness": int(stats["cleanliness"]),
                "health": int(stats["health"]),
                "food_reserve": int(stats.get("food_reserve", 0)),
                "level": int(stats["level"]),
                "xp": int(stats["xp"]),
                "age_days": int(stats["age_days"]),
            },
        }

    def _render(self, dimensions, settings, state: dict[str, Any], now: datetime):
        width, height = dimensions
        mood = state.get("mood") or "calm"
        palette = _pet_palette(mood, settings.get("_inkypi_theme"))
        border = palette["border"]
        image = Image.new("RGB", dimensions, palette["background"])
        draw = ImageDraw.Draw(image)
        self._draw_halftone(draw, (0, 0, width, height), palette["orange"], palette["background"], spacing=30, radius=1, mix=palette["halftone_mix"])

        face = FACE_MAP.get(mood, FACE_MAP["calm"])[0]
        mood_label = self._mood_label(mood, settings)
        state["face"] = face
        activity_id = state.get("activity", "quiet watch")
        activity = self._activity_text(settings, activity_id)
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

        draw.rectangle((1, 1, width - 2, height - 2), outline=border, width=2)
        self._draw_registration_marks(draw, width, palette)
        self._draw_header(draw, (pad, pad, width - pad, pad + header_h), settings, state, mood_label, title_font, meta_font, label_font, now, palette)
        self._draw_face_panel(image, draw, (left, content_top, left + left_w, footer_top - gap), settings, mood, face, mood_label, activity_id, activity, face_font, label_font, palette)
        self._draw_stats_panel(draw, (right, content_top, right + right_w, footer_top - gap), settings, stats, label_font, value_font, palette)
        self._draw_message_panel(draw, (pad, footer_top, width - pad, height - pad), settings, state, message_font, journal_font, label_font, telemetry_font, palette)
        return image

    def _draw_panel(self, draw, box: tuple[int, int, int, int], palette, *, fill=None, accent=None, shadow=None, stripe: int = 7) -> None:
        x1, y1, x2, y2 = box
        fill = fill or palette["panel"]
        accent = accent or palette["accent"]
        shadow = shadow or palette["orange"]
        draw.rectangle((x1 + 4, y1 + 5, x2 + 4, y2 + 5), fill=shadow)
        draw.rectangle(box, fill=fill)
        if stripe > 0:
            draw.rectangle((x1, y1, x2, min(y2, y1 + stripe)), fill=accent)
        draw.rectangle(box, outline=palette["border"], width=2)

    def _draw_registration_marks(self, draw, width: int, palette) -> None:
        x = width - 86
        y = 7
        for color in (palette["blue"], palette["red"], palette["yellow"], palette["green"]):
            draw.rectangle((x, y, x + 12, y + 12), fill=color)
            draw.rectangle((x, y, x + 12, y + 12), outline=palette["ink"], width=1)
            x += 17

    def _draw_halftone(self, draw, box: tuple[int, int, int, int], color, paper, *, spacing: int = 20, radius: int = 1, mix: float = 0.20) -> None:
        x1, y1, x2, y2 = box
        dot = _blend_rgb(color, paper, mix)
        for y in range(y1 + spacing // 2, y2, spacing):
            for x in range(x1 + spacing // 2, x2, spacing):
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=dot)

    def _draw_header(self, draw, box: tuple[int, int, int, int], settings, state: dict[str, Any], mood_label: str, title_font, meta_font, label_font, now: datetime, palette) -> None:
        x1, y1, x2, y2 = box
        ink = palette["ink"]
        border = palette["border"]
        stats = state["stats"]
        name = str(state.get("name", DEFAULT_PET_NAME)).strip() or DEFAULT_PET_NAME
        if len(name) > 15:
            name = name[:15]

        self._draw_panel(draw, box, palette, fill=palette["panel_yellow"], accent=palette["blue"], shadow=palette["red"])
        title = name if self._is_chinese(settings) or not name.isascii() else name.upper()
        draw.text((x1 + 18, y1 + 8), title, font=title_font, fill=ink)

        level_info = self._level_info(state, settings)
        reserve_days = float(level_info.get("reserve_days") or 0)
        if self._is_chinese(settings):
            meta = f"{self._ui(settings, 'level', 'LV')} {stats['level']}  {self._ui(settings, 'age', 'AGE')} {stats['age_days']}{self._ui(settings, 'day', 'D')}  XP {stats['xp']}/{level_info['next_xp']}  储 {reserve_days:.1f}天"
        else:
            meta = f"LV {stats['level']}  AGE {stats['age_days']}D  XP {stats['xp']}/{level_info['next_xp']}  R {reserve_days:.1f}D"
        meta = self._clip_text(draw, meta, meta_font, x2 - x1 - 188)
        draw.text((x1 + 20, y2 - 28), meta, font=meta_font, fill=palette["muted"])

        stamp = now.strftime("%m/%d %H:%M")
        stamp_w = self._text_w(draw, stamp, meta_font)
        draw.text((x2 - 18 - stamp_w, y1 + 12), stamp, font=meta_font, fill=palette["muted"])
        self._draw_badge(draw, x2 - 148, y2 - 34, 130, 24, self._badge_text(settings, mood_label), label_font, palette, accent=palette["accent"])

    def _draw_face_panel(self, image, draw, box: tuple[int, int, int, int], settings, mood: str, face: str, mood_label: str, activity_id: str, activity: str, face_font, label_font, palette) -> None:
        x1, y1, x2, y2 = box
        ink = palette["ink"]
        border = palette["border"]
        self._draw_panel(draw, box, palette, fill=palette["panel_blue"], accent=palette["accent"], shadow=palette["yellow"])
        self._draw_halftone(draw, (x1 + 16, y1 + 42, x2 - 16, y2 - 54), palette["blue"], palette["panel_blue"], spacing=22, radius=1, mix=palette["halftone_mix"])
        draw.text((x1 + 16, y1 + 12), self._badge_text(settings, self._ui(settings, "face", "FACE")), font=label_font, fill=ink)
        self._draw_badge(draw, x2 - 124, y1 + 10, 108, 24, self._badge_text(settings, mood_label), label_font, palette, accent=palette["accent"])

        pose_box = (x1 + 18, y1 + 40, x2 - 18, y2 - 48)
        cx = (pose_box[0] + pose_box[2]) // 2
        cy = (pose_box[1] + pose_box[3]) // 2
        face_back = pose_box
        draw.rectangle(face_back, fill=_blend_rgb(palette["accent"], palette["panel_blue"], palette["face_back_mix"]), outline=palette["rule"], width=1)
        if not self._draw_state_image(image, draw, face_back, mood, activity_id, palette):
            draw.text((cx, cy), face, anchor="mm", font=face_font, fill=ink)
        draw.line((x1 + 28, y2 - 42, x2 - 28, y2 - 42), fill=border, width=1)
        activity_label = f"{self._ui(settings, 'activity', 'ACTIVITY')}: {str(activity or self._activity_text(settings, 'quiet watch'))}"
        activity_label = self._badge_text(settings, activity_label)
        activity_label = self._clip_text(draw, activity_label, label_font, x2 - x1 - 46)
        draw.text((cx, y2 - 27), activity_label, anchor="mm", font=label_font, fill=ink)

    def _draw_state_image(self, image, draw, box: tuple[int, int, int, int], mood: str, activity_id: str = "", palette=None) -> bool:
        pose, state_key = self._load_state_image(mood, activity_id)
        if pose is None:
            return False
        x1, y1, x2, y2 = box
        max_w = max(1, x2 - x1 - 12)
        max_h = max(1, y2 - y1 - 8)
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
        pose.thumbnail((max_w, max_h), resampling)
        x = x1 + (x2 - x1 - pose.width) // 2
        y = y2 - pose.height - 4
        if palette:
            self._draw_pose_motion_marks(draw, box, state_key, palette)
        image.paste(pose, (x, y), pose)
        return True

    def _load_state_image(self, mood: str, activity_id: str = ""):
        state_key = self._resolve_state_image_key(mood, activity_id)
        path = PET_STATE_IMAGE_DIR / f"{state_key}.png"
        if not path.is_file():
            return None, state_key
        cache = getattr(self, "_state_image_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._state_image_cache = cache
        key = str(path)
        try:
            if key not in cache:
                cache[key] = Image.open(path).convert("RGBA")
            return cache[key].copy(), state_key
        except Exception as exc:
            logger.warning("Could not load epaper pet state image %s: %s", path, exc)
            return None, state_key

    def _resolve_state_image_key(self, mood: str, activity_id: str = "") -> str:
        activity_key = PET_ACTIVITY_IMAGE_MAP.get(_pet_state_lookup_key(activity_id))
        if activity_key and (PET_STATE_IMAGE_DIR / f"{activity_key}.png").is_file():
            return activity_key
        mood_key = PET_STATE_IMAGE_MAP.get(_pet_state_lookup_key(mood), "calm")
        if (PET_STATE_IMAGE_DIR / f"{mood_key}.png").is_file():
            return mood_key
        return "calm"

    def _draw_pose_motion_marks(self, draw, box: tuple[int, int, int, int], state_key: str, palette) -> None:
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        accent = palette["accent"]
        warm = palette["orange"]
        cool = palette["blue"]
        soft = _blend_rgb(palette["accent"], palette["panel_blue"], 0.45)
        rule = palette["rule"]

        if state_key == "zoomies":
            for i in range(5):
                y = y1 + int(height * (0.22 + i * 0.12))
                draw.line((x1 + 18, y, x1 + 78, y - 16), fill=warm if i % 2 else accent, width=2)
                draw.line((x1 + 24, y + 8, x1 + 58, y), fill=rule, width=1)
        elif state_key == "pounce":
            draw.arc((x1 + 18, y1 + 44, x1 + width // 2, y1 + height - 18), 205, 300, fill=warm, width=2)
            draw.arc((x1 + 34, y1 + 62, x1 + width // 2 + 18, y1 + height - 6), 205, 290, fill=accent, width=1)
            for x in (x1 + 40, x2 - 58):
                draw.line((x, y2 - 24, x + 24, y2 - 24), fill=rule, width=1)
        elif state_key == "stretch":
            for i in range(3):
                y = y2 - 24 - i * 11
                draw.line((x1 + 30 + i * 5, y, x2 - 42, y - 8), fill=soft, width=2)
        elif state_key == "snacking":
            crumbs = [
                (x1 + width * 0.63, y1 + height * 0.42),
                (x1 + width * 0.70, y1 + height * 0.48),
                (x1 + width * 0.59, y1 + height * 0.52),
            ]
            for cx, cy in crumbs:
                draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=warm)
        elif state_key == "kneading":
            for i in range(4):
                x = x1 + int(width * (0.24 + i * 0.12))
                draw.arc((x, y2 - 44, x + 36, y2 - 16), 200, 340, fill=warm if i % 2 else accent, width=2)
        elif state_key == "alert_listening":
            ear_x = x1 + width // 2
            for i in range(3):
                pad = 22 + i * 16
                draw.arc((ear_x - pad, y1 + 30 - i * 3, ear_x + pad, y1 + 60 + i * 10), 200, 340, fill=cool if i % 2 else accent, width=1)
        elif state_key in {"dreaming", "sleeping"}:
            bubbles = [
                (x1 + width * 0.36, y1 + height * 0.27, 5),
                (x1 + width * 0.29, y1 + height * 0.20, 7),
                (x1 + width * 0.22, y1 + height * 0.13, 10),
            ]
            for cx, cy, r in bubbles:
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=cool, width=2)
        elif state_key == "tail_swish":
            for i in range(3):
                draw.arc((x2 - 100 - i * 16, y1 + 38 + i * 8, x2 - 20, y1 + height - 42 + i * 8), 260, 42, fill=warm if i == 1 else accent, width=2)

    def _draw_stats_panel(self, draw, box: tuple[int, int, int, int], settings, stats: dict[str, Any], label_font, value_font, palette) -> None:
        x1, y1, x2, y2 = box
        ink = palette["ink"]
        self._draw_panel(draw, box, palette, fill=palette["panel"], accent=palette["green"], shadow=palette["blue"])
        draw.text((x1 + 16, y1 + 12), self._badge_text(settings, self._ui(settings, "vitals", "VITALS")), font=label_font, fill=ink)
        self._draw_badge(draw, x2 - 122, y1 + 10, 106, 24, self._badge_text(settings, self._ui(settings, "auto", "AUTO")), label_font, palette, accent=palette["green"])

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
            self._draw_bar(draw, x1 + 16, row_y, x2 - x1 - 32, self._badge_text(settings, label), int(stats[key]), label_font, value_font, palette, key)
            row_y += row_h

    def _draw_bar(self, draw, x: int, y: int, width: int, label: str, value: int, label_font, value_font, palette, key: str) -> None:
        ink = palette["ink"]
        border = palette["border"]
        color = palette["bar_colors"].get(key, palette["accent"])
        value = _clamp(value)
        value_text = f"{value:03d}"
        label_w = max(48, self._text_w(draw, label, label_font) + 10)
        value_w = 44
        bar_h = 14
        bar_x = x + label_w
        bar_y = y + 8
        bar_w = max(80, width - label_w - value_w - 10)

        draw.text((x, y + 3), label, font=label_font, fill=ink)
        draw.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), fill=_blend_rgb(color, palette["panel"], palette["bar_track_mix"]), outline=border, width=1)
        fill_w = int((bar_w - 4) * value / 100)
        if fill_w > 0:
            draw.rectangle((bar_x + 2, bar_y + 2, bar_x + 2 + fill_w, bar_y + bar_h - 2), fill=color)
            draw.line((bar_x + 2, bar_y + 2, bar_x + 2 + fill_w, bar_y + 2), fill=ink, width=1)
        draw.text((bar_x + bar_w + 8, y - 2), value_text, font=value_font, fill=ink)

    def _draw_badge(self, draw, x: int, y: int, width: int, height: int, text: str, font, palette, *, accent=None) -> None:
        ink = palette["ink"]
        accent = accent or palette["accent"]
        fill = _blend_rgb(accent, palette["panel"], palette["badge_mix"])
        draw.rectangle((x, y, x + width, y + height), fill=fill, outline=palette["border"], width=1)
        draw.rectangle((x, y, x + 6, y + height), fill=accent)
        text = self._clip_text(draw, text, font, width - 14)
        tw = self._text_w(draw, text, font)
        th = self._text_h(draw, text, font)
        draw.text((x + (width - tw) // 2, y + (height - th) // 2 - 1), text, font=font, fill=ink)

    def _draw_message_panel(self, draw, box: tuple[int, int, int, int], settings, state: dict[str, Any], message_font, journal_font, label_font, telemetry_font, palette) -> None:
        x1, y1, x2, y2 = box
        ink = palette["ink"]
        self._draw_panel(draw, box, palette, fill=palette["panel_green"], accent=palette["orange"], shadow=palette["green"])
        log_label = self._badge_text(settings, self._ui(settings, "log", "LOG"))
        self._draw_badge(draw, x1 + 14, y1 + 11, 62, 24, log_label, label_font, palette, accent=palette["orange"])

        message = self._message_text(settings, state.get("message", "Quiet heartbeat."))
        raw_journal = self._latest_journal_line(settings) if _enabled(settings.get("show_journal"), True) else ""
        journal_line = self._message_text(settings, raw_journal) if raw_journal else ""
        telemetry = self._ai_telemetry_text(settings, state)
        text_x = x1 + 92
        max_w = x2 - text_x - 18
        lines = self._wrap(draw, message, message_font, max_w, max_lines=2)
        y = y1 + 12
        for line in lines:
            draw.text((text_x, y), line, font=message_font, fill=ink)
            y += 28

        telemetry_w = 0
        if telemetry:
            telemetry = self._clip_text(draw, telemetry, telemetry_font, x2 - x1 - 32)
            telemetry_w = self._text_w(draw, telemetry, telemetry_font)
            draw.text((x2 - 16 - telemetry_w, y2 - 22), telemetry, font=telemetry_font, fill=palette["muted"])

        if journal_line and journal_line != message:
            journal_prefix = self._ui(settings, "last", "LAST")
            journal_w = max(80, max_w - telemetry_w - (20 if telemetry_w else 0))
            journal = self._wrap(draw, f"{journal_prefix}: {journal_line}", journal_font, journal_w, max_lines=1)[0]
            draw.text((text_x, y2 - 24), journal, font=journal_font, fill=palette["muted"])

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
        if provider in {"local", "openai"} and fallback_from == "groq":
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
        if str(family or "").strip().casefold() in {
            "jost",
            "lxgw wenkai",
            "microsoft yahei",
            "\u5fae\u8f6f\u96c5\u9ed1",
        }:
            return get_base_ui_font(int(size), bold=weight == "bold")

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
        return get_base_ui_font(int(size), bold=weight == "bold")

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
        return text_width(draw, text, font)

    def _text_h(self, draw, text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]
