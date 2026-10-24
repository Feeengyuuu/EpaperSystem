"""Read-only vehicle status from the private Epaper Vehicle Bridge."""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import hmac
import ipaddress
import json
import logging
import math
from pathlib import Path
import threading
import time
from urllib.parse import urlencode, urlsplit

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from security.ssrf import get_ssrf_policy
from utils.app_utils import get_base_ui_font
from utils.atomic_file import atomic_write_json
from utils.http_client import HttpStatusError, get_http_client
from utils.safe_image import ImageLimits, safe_open_image


logger = logging.getLogger(__name__)

# The bridge is a Worker you deploy yourself (cloudflare/vehicle-auth-bridge).
# EPAPER_VEHICLE_BRIDGE_ORIGIN names its https origin; without it the
# maintainer's deployment is used.
BRIDGE_ORIGIN = "https://epaper-vehicle-bridge.superxfy.workers.dev"
BRIDGE_ORIGIN_ENV = "EPAPER_VEHICLE_BRIDGE_ORIGIN"
BRIDGE_SUMMARY_PATH = "/api/vehicle-summary?schema_version=3"
BRIDGE_SUMMARY_URL = f"{BRIDGE_ORIGIN}{BRIDGE_SUMMARY_PATH}"
BRIDGE_TOKEN_ENV = "EPAPER_VEHICLE_BRIDGE_TOKEN"
GOOGLE_MAPS_KEY_ENVS = ("GOOGLE_MAPS_API_KEY", "Google_KEY", "GOOGLE_KEY")
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_STATIC_MAP_URL = "https://maps.googleapis.com/maps/api/staticmap"
SUMMARY_MAX_BYTES = 64 * 1024
CACHE_MAX_BYTES = 64 * 1024
CACHE_FILE_NAME = "summary-v3.json"
LEGACY_CACHE_FILE_NAME = "summary-v2.json"
OLDEST_CACHE_FILE_NAME = "summary-v1.json"
LOCATION_PRESENTATION_FILE_NAME = "location-presentation-v1.json"
LOCATION_PRESENTATION_MAX_BYTES = 256 * 1024
LOCATION_MAP_MAX_BYTES = 128 * 1024
LOCATION_ADDRESS_MAX_CHARS = 256
LOCATION_MAX_AGE_SECONDS = 86_400
LOCATION_FUTURE_TOLERANCE_SECONDS = 300
# The map takes the location section down to a rule; the address sits under it on the column's foot.
LOCATION_MAP_BOX = (538, 240, 764, 344)
LOCATION_MAP_SIZE = (
    LOCATION_MAP_BOX[2] - LOCATION_MAP_BOX[0],
    LOCATION_MAP_BOX[3] - LOCATION_MAP_BOX[1],
)
# Like the car's own map, the location map melts into the panel: its top and sides fade
# out over these widths. The bottom strip keeps full strength, because Google's logo and
# attribution sit in its corners and must stay legible.
LOCATION_MAP_FADE_TOP = 18
LOCATION_MAP_FADE_SIDE = 18
LOCATION_MAP_ATTRIBUTION_H = 22
LOCATION_MAP_LIMITS = ImageLimits(
    max_bytes=LOCATION_MAP_MAX_BYTES,
    max_width=LOCATION_MAP_SIZE[0],
    max_height=LOCATION_MAP_SIZE[1],
    max_pixels=LOCATION_MAP_SIZE[0] * LOCATION_MAP_SIZE[1],
    allowed_formats=frozenset({"PNG"}),
)
VEHICLE_IMAGE_NAME = "vehicle.png"
# The same car plugged in with its charge port lit and energy flowing, shown while it charges.
VEHICLE_CHARGING_IMAGE_NAME = "vehicle_charging.png"
VEHICLE_CHARGING_ART_STATES = frozenset({"charging", "starting"})
VEHICLE_WORDMARK_NAME = "grey_bullet_wordmark.png"
DASHBOARD_ICON_DIR_NAME = "dashboard_icons"
DASHBOARD_ICON_FILES = {
    "energy": "energy.png",
    "security": "security.png",
    "climate": "climate.png",
    "vehicle": "vehicle_info.png",
    "tires": "tires.png",
    "freshness": "freshness.png",
    "charging": "charging.png",
    "location": "location.png",
}
DASHBOARD_ICON_LIMITS = ImageLimits(
    max_bytes=64 * 1024,
    max_width=64,
    max_height=64,
    max_pixels=64 * 64,
    allowed_formats=frozenset({"PNG"}),
)
VEHICLE_WORDMARK_LIMITS = ImageLimits(
    max_bytes=64 * 1024,
    max_width=1024,
    max_height=256,
    max_pixels=1024 * 256,
    allowed_formats=frozenset({"PNG"}),
)
SKIP_CACHE_IMAGE_INFO_KEY = "inkypi_skip_cache"
LOCAL_MAX_STALE_SECONDS = 86_400
VEHICLE_WORDMARK_BOX = (36, 22, 196, 54)
GREY_BULLET_NAMES = frozenset({"gray bullet", "grey bullet"})

_CACHE_LOCK = threading.RLock()
_TOP_KEYS_V1 = {
    "schema_version",
    "served_at",
    "snapshot",
    "vehicle",
    "battery",
    "climate",
    "closures",
}
_TOP_KEYS_V2 = {
    "schema_version",
    "served_at",
    "snapshot",
    "vehicle",
    "battery",
    "charging",
    "climate",
    "closures",
    "tires",
    "software_update",
    "preferences",
}
_TOP_KEYS_V3 = _TOP_KEYS_V2 | {"location"}
_LOCATION_KEYS = {"captured_at", "age_seconds", "latitude", "longitude"}
_SNAPSHOT_KEYS = {"captured_at", "freshness", "age_seconds", "vehicle_connectivity"}
_VEHICLE_KEYS_V1 = {
    "key",
    "display_name",
    "model",
    "trim",
    "locked",
    "software_version",
    "odometer",
}
_BATTERY_KEYS_V1 = {
    "level_percent",
    "estimated_range",
    "charging_state",
    "charge_limit_percent",
    "time_to_full_minutes",
    "power_kw",
}
_CLIMATE_KEYS_V1 = {"inside_temp_c", "outside_temp_c", "is_climate_on"}
_CLOSURE_KEYS_V1 = {"all_closed", "open", "charge_port_open"}
_VEHICLE_KEYS_V2 = _VEHICLE_KEYS_V1 | {
    "exterior_color",
    "wheel_type",
    "roof_color",
    "charge_port_type",
    "efficiency_package",
    "rear_seat_heaters",
    "right_hand_drive",
    "europe_vehicle",
    "sunroof_installed",
    "sentry_mode",
    "service_mode",
    "valet_mode",
    "center_display_state",
    "speed_limit_mode",
}
_SPEED_LIMIT_MODE_KEYS = {"active", "limit"}
_BATTERY_KEYS_V2 = {
    "level_percent",
    "usable_level_percent",
    "rated_range",
    "estimated_range",
}
_CHARGING_KEYS_V2 = {
    "state",
    "charge_limit_percent",
    "time_to_full_minutes",
    "power_kw",
    "energy_added_kwh",
    "rate",
    "actual_current_a",
    "voltage_v",
    "phases",
    "requested_current_a",
    "max_current_a",
    "enabled",
    "cable_type",
    "fast_charger_present",
    "fast_charger_type",
    "port_latch",
    "port_cold_weather_mode",
    "preconditioning",
    "not_enough_power_to_heat",
    "supercharger_trip_planner",
    "scheduled",
}
_SCHEDULED_KEYS = {"pending", "mode"}
_CLIMATE_KEYS_V2 = _CLIMATE_KEYS_V1 | {
    "driver_target_temp_c",
    "passenger_target_temp_c",
    "keeper_mode",
    "defrost_mode",
    "rear_defroster_on",
    "battery_heater_on",
    "wiper_heater_on",
    "hvac_auto_mode",
    "fan_status",
    "steering_wheel_heat_level",
    "steering_wheel_heat_auto",
    "seat_heaters",
    "seat_cooling",
    "auto_seat_climate",
    "cabin_overheat",
}
_SEAT_HEATER_KEYS = {
    "front_left",
    "front_right",
    "rear_left",
    "rear_right",
    "rear_center",
}
_FRONT_SEAT_KEYS = {"front_left", "front_right"}
_CABIN_OVERHEAT_KEYS = {"mode", "temp_limit"}
_CLOSURE_KEYS_V2 = _CLOSURE_KEYS_V1 | {"doors", "windows"}
_DOOR_KEYS = {
    "driver_front",
    "driver_rear",
    "passenger_front",
    "passenger_rear",
    "front_trunk",
    "rear_trunk",
}
_WINDOW_KEYS = {
    "driver_front",
    "driver_rear",
    "passenger_front",
    "passenger_rear",
}
_TIRES_KEYS = {"pressures", "soft_warnings", "hard_warnings"}
_TIRE_POSITIONS = {"front_left", "front_right", "rear_left", "rear_right"}
_SOFTWARE_UPDATE_KEYS = {
    "version",
    "download_percent",
    "install_percent",
    "expected_duration_minutes",
}
_PREFERENCE_KEYS = {
    "distance_unit",
    "temperature_unit",
    "pressure_unit",
    "charge_display_unit",
    "use_24_hour_time",
}
_MEASUREMENT_KEYS = {"value", "unit"}
_FRESHNESS = {"live", "fresh_cache", "stale_cache"}
_CONNECTIVITY = {"online", "asleep", "offline", "unknown", "unavailable", "in_service"}
# The bridge never wakes a resting vehicle; "unavailable" marks a provider fallback.
_RESTING_CONNECTIVITY = frozenset({"asleep", "offline"})
_OPEN_CLOSURES = {
    "driver_front_door",
    "driver_rear_door",
    "passenger_front_door",
    "passenger_rear_door",
    "front_trunk",
    "rear_trunk",
    "driver_front_window",
    "driver_rear_window",
    "passenger_front_window",
    "passenger_rear_window",
}

_UI_TEXT = {
    "en": {
        "vehicle_fallback": "Vehicle",
        "charging": "CHARGING",
        "cable": "CABLE {value}",
        "cable_unknown": "CABLE TYPE UNKNOWN",
        "sentry_mode": "SENTRY",
        "charge_port": "CHARGE PORT",
        "special_modes": "MODES",
        "open_state": "OPEN",
        "closed_state": "CLOSED",
        "off_state": "OFF",
        "driver_target": "DRIVER",
        "passenger_target": "PASSENGER",
        "auto_hvac": "AUTO",
        "fan": "FAN",
        "defrost": "DEFROST",
        "overheat_protect": "OVERHEAT",
        "location": "LOCATION",
        "screen": "SCREEN",
        "progress_line": "DL {download} / INSTALL {install} / EST {duration}",
        "energy": "BATTERY",
        "rated_range": "RATED RANGE",
        "estimated_range": "EST. RANGE",
        "usable": "USABLE {value}%",
        "usable_with_label": "USABLE {value}% / {label}",
        "limit_range": "LIMIT {limit}",
        "limit_range_projection": "LIMIT {limit} / ~{range}",
        "limit_left": "LIMIT / LEFT",
        "power_added": "POWER / ADDED",
        "input": "INPUT",
        "request_max": "REQUEST / MAX",
        "rate": "CHARGE RATE",
        "status": "CHARGING",
        "limit": "LIMIT",
        "time_left": "TIME LEFT",
        "power": "POWER",
        "security": "SECURITY",
        "locked": "LOCKED",
        "unlocked": "UNLOCKED",
        "sentry": "SENTRY {value}",
        "sentry_not_reported": "SENTRY NOT REPORTED",
        "all_closed": "ALL CLOSED",
        "open_count": "{count} OPEN",
        "status_unknown": "STATUS UNKNOWN",
        "all_access_secure": "ALL ACCESS POINTS SECURE",
        "opening_incomplete": "OPENING DATA INCOMPLETE",
        "more_open": "+{count} MORE",
        "port_open": "PORT OPEN",
        "port_closed": "PORT CLOSED",
        "service_on": "SERVICE ON",
        "valet_on": "VALET ON",
        "speed_limit": "LIMIT {value}",
        "modes_off": "MODES OFF",
        "modes_unknown": "MODES --",
        "climate": "CLIMATE",
        "hvac_on": "HVAC ON",
        "hvac_off": "HVAC OFF",
        "inside_outside": "{inside} IN / {outside} OUT",
        "target": "TARGET D {driver} / P {passenger}",
        "auto_fan_defrost": "AUTO {auto} / FAN {fan} / DEF {defrost}",
        "rear_defrost": "REAR DEF",
        "battery_heat": "BAT HEAT",
        "wiper_heat": "WIPER HEAT",
        "wheel_heat": "WHEEL {level}",
        "seat_heat": "SEAT {value}",
        "seat_cool": "COOL {value}",
        "overheat": "COP {mode}{limit}",
        "no_active_climate": "NO ACTIVE HEAT / COOL",
        "vehicle": "VEHICLE",
        "display": "DISPLAY {value}",
        "odometer": "ODOMETER",
        "software": "SOFTWARE",
        "update": "UPDATE",
        "progress": "PROGRESS",
        "body": "BODY",
        "body_wheel": "BODY/WHEEL",
        "wheels": "WHEELS",
        "config": "CONFIG",
        "equipment": "EQUIPMENT",
        "progress_value": "D {download} / I {install} / {duration}",
        "rhd": "RHD",
        "lhd": "LHD",
        "eu": "EU",
        "non_eu": "NON-EU",
        "tires": "TIRES",
        "tire_fl": "FL",
        "tire_fr": "FR",
        "tire_rl": "RL",
        "tire_rr": "RR",
        "seat_front_left": "FL",
        "seat_front_right": "FR",
        "seat_rear_left": "RL",
        "seat_rear_right": "RR",
        "seat_rear_center": "RC",
        "not_reported_v1": "NOT REPORTED BY V1",
        "hard_warning": "HARD WARNING {positions}",
        "low_pressure": "LOW PRESSURE {positions}",
        "pressure_unknown": "STATUS UNKNOWN",
        "pressure_partial": "PRESSURES PARTIAL",
        "pressure_ok": "PRESSURES OK",
        "updated": "UPDATED {age} AGO / {freshness}",
        "read_only_footer": "READ ONLY / NO WAKE / NO COMMANDS",
        "location_current": "CURRENT / GOOGLE MAPS",
        "location_last_known": "LAST KNOWN {age} / GOOGLE MAPS",
        "location_unavailable": "LOCATION UNAVAILABLE",
        "unknown": "UNKNOWN",
        "none": "NONE",
        "not_reported": "NOT REPORTED",
        "reported": "REPORTED",
        "enabled": "ENABLED",
        "disabled": "DISABLED",
        "heat_power_low": "HEAT PWR LOW",
        "port_heat": "PORT HEAT",
        "precondition": "PRECOND",
        "fast_charge": "FAST {kind}",
        "schedule": "SCHED {mode}",
        "trip_plan": "TRIP PLAN",
        "latch": "LATCH {state}",
        "unit_kw": "KW",
        "unit_kwh": "KWH",
        "unit_amp": "A",
        "unit_volt": "V",
        "unit_phase": "P",
        "unit_mile": "mi",
        "unit_km": "km",
        "unit_mph": "MI/H",
        "unit_kph": "KM/H",
        "unit_bar": "BAR",
        "unit_psi": "PSI",
        "unit_c": " C",
        "unit_f": " F",
        "age_seconds": "{value}S",
        "age_minutes": "{value}M",
        "age_hours": "{value}H",
        "duration_minutes": "{value}M",
        "duration_hours": "{hours}H {minutes}M",
        "setup_footer": "Read-only / no wake / no commands",
        "connect_bridge_title": "CONNECT BRIDGE",
        "connect_bridge_message": "Add {token} in API Keys.",
        "bridge_origin_invalid_title": "CHECK BRIDGE ADDRESS",
        "bridge_origin_invalid_message": "{setting} must be https://<bridge host>.",
        "status_old_title": "STATUS TOO OLD",
        "status_old_message": "Refresh the bridge before trusting vehicle status.",
        "status_unavailable_title": "STATUS UNAVAILABLE",
        "status_unavailable_message": "No cached vehicle status is available yet.",
        "no_cache_title": "NO CACHED STATUS",
        "no_cache_message": "Refresh the plugin once after authorization.",
    },
    "zh-CN": {
        "vehicle_fallback": "车辆",
        "charging": "充电",
        "cable": "接口 {value}",
        "cable_unknown": "接口类型未知",
        "sentry_mode": "哨兵模式",
        "charge_port": "充电口",
        "special_modes": "特殊模式",
        "open_state": "开启",
        "closed_state": "关闭",
        "off_state": "关闭",
        "driver_target": "主驾目标",
        "passenger_target": "副驾目标",
        "auto_hvac": "自动开启",
        "fan": "风量",
        "defrost": "除霜",
        "overheat_protect": "过热保护",
        "location": "位置",
        "screen": "屏幕",
        "progress_line": "下载 {download} / 安装 {install} / 预计 {duration}",
        "energy": "电量",
        "rated_range": "标称续航",
        "estimated_range": "预估续航",
        "usable": "可用电量 {value}%",
        "usable_with_label": "可用 {value}% · {label}",
        "limit_range": "上限 {limit}",
        "limit_range_projection": "上限 {limit} · 约 {range}",
        "limit_left": "上限 / 剩余",
        "power_added": "功率 / 已补能",
        "input": "输入",
        "request_max": "请求 / 最大",
        "rate": "充电速度",
        "status": "充电功能",
        "limit": "上限",
        "time_left": "剩余时间",
        "power": "功率",
        "security": "安全",
        "locked": "已锁车",
        "unlocked": "未锁车",
        "sentry": "哨兵 {value}",
        "sentry_not_reported": "哨兵 未上报",
        "all_closed": "全部关闭",
        "open_count": "{count} 处开启",
        "status_unknown": "状态未知",
        "all_access_secure": "门窗均已关闭",
        "opening_incomplete": "门窗数据不完整",
        "more_open": "另 {count} 处",
        "port_open": "充电口开启",
        "port_closed": "充电口关闭",
        "service_on": "维修模式",
        "valet_on": "代客模式",
        "speed_limit": "限速 {value}",
        "modes_off": "特殊模式关闭",
        "modes_unknown": "模式未知",
        "climate": "温控",
        "hvac_on": "温控已开",
        "hvac_off": "温控已关",
        "inside_outside": "车内 {inside} / 车外 {outside}",
        "target": "目标 主 {driver} / 副 {passenger}",
        "auto_fan_defrost": "自动 {auto} / 风量 {fan} / 除霜 {defrost}",
        "rear_defrost": "后窗除霜",
        "battery_heat": "电池加热",
        "wiper_heat": "雨刷加热",
        "wheel_heat": "方向盘 {level}",
        "seat_heat": "座椅 {value}",
        "seat_cool": "通风 {value}",
        "overheat": "过热保护 {mode}{limit}",
        "no_active_climate": "无主动温控",
        "vehicle": "车辆",
        "display": "屏幕 {value}",
        "odometer": "里程",
        "software": "软件",
        "update": "更新",
        "progress": "进度",
        "body": "外观",
        "body_wheel": "外观/轮毂",
        "wheels": "轮毂",
        "config": "配置",
        "equipment": "装备",
        "progress_value": "下 {download} / 装 {install} / {duration}",
        "rhd": "右舵",
        "lhd": "左舵",
        "eu": "欧规",
        "non_eu": "非欧规",
        "tires": "轮胎",
        "tire_fl": "左前",
        "tire_fr": "右前",
        "tire_rl": "左后",
        "tire_rr": "右后",
        "seat_front_left": "前左",
        "seat_front_right": "前右",
        "seat_rear_left": "后左",
        "seat_rear_right": "后右",
        "seat_rear_center": "后中",
        "not_reported_v1": "V1 未上报",
        "hard_warning": "严重告警 {positions}",
        "low_pressure": "胎压偏低 {positions}",
        "pressure_unknown": "状态未知",
        "pressure_partial": "胎压数据不完整",
        "pressure_ok": "胎压正常",
        "updated": "{age}前更新 / {freshness}",
        "read_only_footer": "只读 / 不唤醒 / 不发送指令",
        "location_current": "当前位置 / GOOGLE MAPS",
        "location_last_known": "上次位置 {age} 前 / GOOGLE MAPS",
        "location_unavailable": "位置不可用",
        "unknown": "未知",
        "none": "无更新",
        "not_reported": "未上报",
        "reported": "已上报",
        "enabled": "已启用",
        "disabled": "已停用",
        "heat_power_low": "加热功率不足",
        "port_heat": "充电口加热",
        "precondition": "电池预热",
        "fast_charge": "快充 {kind}",
        "schedule": "计划 {mode}",
        "trip_plan": "超充行程规划",
        "latch": "锁扣 {state}",
        "unit_kw": "千瓦",
        "unit_kwh": "千瓦时",
        "unit_amp": "安",
        "unit_volt": "伏",
        "unit_phase": "相",
        "unit_mile": "英里",
        "unit_km": "公里",
        "unit_mph": "英里/时",
        "unit_kph": "公里/时",
        "unit_bar": "巴",
        "unit_psi": "PSI",
        "unit_c": "°C",
        "unit_f": "°F",
        "age_seconds": "{value}秒",
        "age_minutes": "{value}分",
        "age_hours": "{value}小时",
        "duration_minutes": "{value}分",
        "duration_hours": "{hours}小时{minutes}分",
        "setup_footer": "只读 / 不唤醒 / 不发送指令",
        "connect_bridge_title": "连接车辆桥接",
        "connect_bridge_message": "请在 API 密钥中添加 {token}。",
        "bridge_origin_invalid_title": "请检查桥接地址",
        "bridge_origin_invalid_message": "{setting} 应为 https://<桥接域名>。",
        "status_old_title": "车辆状态已过期",
        "status_old_message": "请刷新桥接后再使用车辆信息。",
        "status_unavailable_title": "车辆状态不可用",
        "status_unavailable_message": "当前没有可用的车辆状态缓存。",
        "no_cache_title": "暂无车辆缓存",
        "no_cache_message": "授权后请先刷新一次此插件。",
    },
}

_ENUM_TEXT = {
    "en": {
        "connectivity": {
            "online": "ONLINE",
            "asleep": "ASLEEP",
            "offline": "OFFLINE",
            "unknown": "UNKNOWN",
            "unavailable": "UNAVAILABLE",
            "in_service": "IN SERVICE",
        },
        "freshness": {
            "live": "LIVE",
            "fresh_cache": "FRESH CACHE",
            "stale_cache": "STALE CACHE",
        },
        "provider_compact": {
            "midnightsilver": "SILV",
            "colored": "BODY",
            "uberturbine20": "20T",
            "us": "US",
            "installed": "REAR HT",
            "not_installed": "NO SUN",
            "my2021": "MY21",
        },
    },
    "zh-CN": {
        "connectivity": {
            "online": "在线",
            "asleep": "休眠",
            "offline": "离线",
            "unknown": "未知",
            "unavailable": "不可用",
            "in_service": "维修中",
        },
        "freshness": {
            "live": "实时",
            "fresh_cache": "缓存",
            "stale_cache": "过期缓存",
        },
        "charging": {
            "charging": "充电中",
            "complete": "已充满",
            "disconnected": "未连接",
            "stopped": "已停止",
            "starting": "准备充电",
            "no_power": "无电源",
            "unknown": "未知",
        },
        "state": {
            "on": "开启",
            "off": "关闭",
            "true": "开启",
            "false": "关闭",
            "unknown": "未知",
            "engaged": "已锁定",
            "not_installed": "未安装",
            "start_at": "定时启动",
            "depart_by": "按时出发",
        },
        "keeper": {
            "dog": "爱犬模式",
            "camp": "露营模式",
            "keep": "保持温度",
            "off": "关闭",
            "unknown": "未知",
        },
        "provider": {
            "performance": "高性能版",
            "midnightsilver": "午夜银",
            "colored": "同色车顶",
            "uberturbine20": "20寸涡轮轮毂",
            "us": "美规",
            "installed": "已安装",
            "not_installed": "未安装",
            "modely": "Model Y",
        },
        "provider_compact": {
            "midnightsilver": "午夜银",
            "colored": "同色",
            "uberturbine20": "20涡轮",
            "us": "美规",
            "installed": "后排加热",
            "not_installed": "无天窗",
            "my2021": "MY21",
        },
    },
}

_OPENING_TEXT = {
    "en": {
        "driver_front_door": "DRIVER FRONT DOOR",
        "driver_rear_door": "DRIVER REAR DOOR",
        "passenger_front_door": "PASS FRONT DOOR",
        "passenger_rear_door": "PASS REAR DOOR",
        "front_trunk": "FRONT TRUNK",
        "rear_trunk": "REAR TRUNK",
        "driver_front_window": "DRIVER FRONT WINDOW",
        "driver_rear_window": "DRIVER REAR WINDOW",
        "passenger_front_window": "PASS FRONT WINDOW",
        "passenger_rear_window": "PASS REAR WINDOW",
        "charge_port": "CHARGE PORT",
    },
    "zh-CN": {
        "driver_front_door": "驾驶侧前门",
        "driver_rear_door": "驾驶侧后门",
        "passenger_front_door": "副驾侧前门",
        "passenger_rear_door": "副驾侧后门",
        "front_trunk": "前备箱",
        "rear_trunk": "后备箱",
        "driver_front_window": "驾驶侧前窗",
        "driver_rear_window": "驾驶侧后窗",
        "passenger_front_window": "副驾侧前窗",
        "passenger_rear_window": "副驾侧后窗",
        "charge_port": "充电口",
    },
}


class SummaryContractError(ValueError):
    """The bridge returned data outside the public sanitized contract."""


class BridgeOriginError(ValueError):
    """EPAPER_VEHICLE_BRIDGE_ORIGIN is not a bare https origin of a named host."""


def bridge_origin(value):
    """The bridge origin to call: the configured https origin, or the default one.

    Only ``https://host[:port]`` is accepted, so the token is never sent in clear,
    the summary path stays fixed, and an IP address or local name cannot be used
    to reach the device's own network.
    """
    text = str(value or "").strip()
    if not text:
        return BRIDGE_ORIGIN
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise BridgeOriginError("not a URL") from exc
    if parsed.scheme.lower() != "https":
        raise BridgeOriginError("must use https")
    if parsed.username is not None or parsed.password is not None:
        raise BridgeOriginError("must not contain credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise BridgeOriginError("must be an origin without a path or query")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise BridgeOriginError("has no host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise BridgeOriginError("must use a host name, not an IP address")
    if "." not in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise BridgeOriginError("must be a public host name")
    return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"


class VehicleStatus(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["api_key"] = {
            "required": False,
            "service": "Epaper Vehicle Bridge",
            "expected_key": BRIDGE_TOKEN_ENV,
        }
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = dict(settings or {})
        language = _language(settings)
        dimensions = self.get_dimensions(device_config)
        theme = settings.get("_inkypi_theme")
        if not isinstance(theme, dict):
            theme = self.resolve_theme(settings, device_config)

        if _bool_setting(settings, "_theme_render_only", False):
            return self._theme_only_image(settings, dimensions, theme)

        cache_seconds = _int_setting(settings, "cacheSeconds", 900, 0, 86_400)
        force_refresh = any(_bool_setting(settings, key, False) for key in ("forceRefresh", "force_refresh"))

        with _CACHE_LOCK:
            cached = self._read_cache_unlocked()
            now = time.time()
            cached_is_usable = _cache_within_max_stale(cached, now)
            if not force_refresh and cached_is_usable and self._cache_is_fresh(cached, cache_seconds, now):
                summary = _advance_cached_age(cached["summary"], cached["fetched_at"], now)
                provenance = _local_cache_provenance(summary)
                return self._render_attested(
                    summary,
                    dimensions,
                    theme,
                    settings,
                    provenance,
                    self._presentation_for_cache(cached, now, language),
                )

            token = str(device_config.load_env_key(BRIDGE_TOKEN_ENV) or "").strip()
            if not token:
                if cached_is_usable:
                    summary = _advance_cached_age(cached["summary"], cached["fetched_at"], now)
                    return self._render_attested(
                        summary,
                        dimensions,
                        theme,
                        settings,
                        SourceProvenance.STALE_CACHE,
                        self._presentation_for_cache(cached, now, language),
                    )
                if cached:
                    return self._render_local_message(
                        dimensions,
                        theme,
                        _t(language, "status_old_title"),
                        _t(language, "status_old_message"),
                        language,
                    )
                return self._render_local_message(
                    dimensions,
                    theme,
                    _t(language, "connect_bridge_title"),
                    _t(
                        language,
                        "connect_bridge_message",
                        token=BRIDGE_TOKEN_ENV,
                    ),
                    language,
                )

            try:
                origin = bridge_origin(device_config.load_env_key(BRIDGE_ORIGIN_ENV))
            except BridgeOriginError as exc:
                logger.warning("Vehicle bridge origin rejected reason=%s", exc)
                return self._render_local_message(
                    dimensions,
                    theme,
                    _t(language, "bridge_origin_invalid_title"),
                    _t(language, "bridge_origin_invalid_message", setting=BRIDGE_ORIGIN_ENV),
                    language,
                )

            try:
                if origin != BRIDGE_ORIGIN:
                    # A configured bridge must resolve only to public addresses.
                    get_ssrf_policy().resolve_and_validate(origin)
                client = get_http_client()
                result = client.request_json(
                    "GET",
                    f"{origin}{BRIDGE_SUMMARY_PATH}",
                    headers={"Authorization": f"Bearer {token}"},
                    allow_redirects=False,
                    timeout=(4, 12),
                    max_bytes=SUMMARY_MAX_BYTES,
                )
                summary = sanitize_summary(result.data)
                if _same_cached_snapshot(cached, summary) and summary["snapshot"]["freshness"] == "stale_cache":
                    # A newer bridge response cannot make the same vehicle values younger.
                    previous_age = _cached_content_age(cached, now)
                    if previous_age is not None:
                        summary["snapshot"]["age_seconds"] = max(
                            summary["snapshot"]["age_seconds"] or 0, previous_age
                        )
                if not _summary_within_max_stale(summary):
                    raise SummaryContractError("bridge summary is too stale")
            except Exception as exc:
                _log_bridge_failure(exc, has_cache=bool(cached))
                if cached_is_usable:
                    summary = _advance_cached_age(cached["summary"], cached["fetched_at"], now)
                    return self._render_attested(
                        summary,
                        dimensions,
                        theme,
                        settings,
                        SourceProvenance.STALE_CACHE,
                        self._presentation_for_cache(cached, now, language),
                    )
                if cached:
                    return self._render_local_message(
                        dimensions,
                        theme,
                        _t(language, "status_old_title"),
                        _t(language, "status_old_message"),
                        language,
                    )
                return self._render_local_message(
                    dimensions,
                    theme,
                    _t(language, "status_unavailable_title"),
                    _t(language, "status_unavailable_message"),
                    language,
                )

            should_replace_cache = _should_replace_local_cache(cached, summary, now)
            if not should_replace_cache and cached_is_usable:
                cached_summary = _advance_cached_age(
                    cached["summary"],
                    cached["fetched_at"],
                    now,
                )
                return self._render_attested(
                    cached_summary,
                    dimensions,
                    theme,
                    settings,
                    _local_cache_provenance(cached_summary),
                    self._presentation_for_cache(cached, now, language),
                )

            location_fingerprint = None
            location_presentation = None
            if summary["schema_version"] == 3 and summary["location"] is not None:
                location_age = _timestamp_age(summary["location"]["captured_at"], now)
                if -LOCATION_FUTURE_TOLERANCE_SECONDS <= location_age <= LOCATION_MAX_AGE_SECONDS:
                    location_fingerprint = _location_fingerprint(
                        summary["location"],
                        token,
                        language,
                    )
                    location_presentation = self._location_presentation_for_live(
                        client,
                        device_config,
                        summary["location"],
                        location_fingerprint,
                        language,
                        now,
                    )
                else:
                    logger.warning("Vehicle location map unavailable reason=outside_time_window")

            provenance = _bridge_provenance(summary)
            image = self._render_attested(
                summary,
                dimensions,
                theme,
                settings,
                provenance,
                location_presentation,
            )
            cache_committed = False
            if should_replace_cache:
                cache_summary = summary
                fetched_at = now
                if (
                    cached_is_usable
                    and _same_cached_snapshot(cached, summary)
                    and provenance is SourceProvenance.STALE_CACHE
                ):
                    # Keep the original polling deadline while storing the latest status.
                    fetched_at = cached["fetched_at"]
                    cache_summary = deepcopy(summary)
                    cache_summary["snapshot"]["age_seconds"] = max(
                        0, summary["snapshot"]["age_seconds"] - max(0, now - fetched_at)
                    )
                try:
                    self._write_cache_unlocked(
                        {
                            "fetched_at": fetched_at,
                            "summary": cache_summary,
                            "location_fingerprint": location_fingerprint,
                            "location_language": language,
                        }
                    )
                    cache_committed = True
                except Exception as exc:
                    logger.warning(
                        "Vehicle cache write failed type=%s",
                        type(exc).__name__,
                    )
            if (
                cache_committed
                and location_presentation is not None
                and location_presentation.get("_pending_write") is True
            ):
                try:
                    self._write_location_presentation(location_presentation)
                except Exception as exc:
                    _log_location_cache_failure("write", exc)
            return image

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        # Re-evaluate age/offline status at the actual display time. The theme-only
        # path never obtains credentials, requests providers, or writes caches.
        return self.render_themed_image(
            settings,
            device_config,
            theme_render_only=True,
            resolved_theme_context=resolved_theme_context,
        )

    def _theme_only_image(self, settings, dimensions, theme):
        language = _language(settings)
        with _CACHE_LOCK:
            cached = self._read_cache_unlocked(create=False)
        if not cached or not cached.get("summary"):
            return self._render_local_message(
                dimensions,
                theme,
                _t(language, "no_cache_title"),
                _t(language, "no_cache_message"),
                language,
            )
        now = time.time()
        if not _cache_within_max_stale(cached, now):
            return self._render_local_message(
                dimensions,
                theme,
                _t(language, "status_old_title"),
                _t(language, "status_old_message"),
                language,
            )
        cache_seconds = _int_setting(settings, "cacheSeconds", 900, 0, 86_400)
        summary = _advance_cached_age(cached["summary"], cached["fetched_at"], now)
        provenance = (
            _local_cache_provenance(summary)
            if self._cache_is_fresh(cached, cache_seconds, now)
            else SourceProvenance.STALE_CACHE
        )
        return self._render_attested(
            summary,
            dimensions,
            theme,
            settings,
            provenance,
            self._presentation_for_cache(cached, now, language),
        )

    def _render_attested(
        self,
        summary,
        dimensions,
        theme,
        settings,
        provenance,
        location_presentation=None,
    ):
        display_summary = dict(summary)
        display_snapshot = dict(summary["snapshot"])
        display_snapshot["freshness"] = {
            SourceProvenance.LIVE: "live",
            SourceProvenance.FRESH_CACHE: "fresh_cache",
            SourceProvenance.STALE_CACHE: "stale_cache",
            SourceProvenance.LOCAL_FALLBACK: "stale_cache",
        }[provenance]
        display_summary["snapshot"] = display_snapshot
        image = self._render_summary(
            display_summary,
            dimensions,
            theme,
            settings,
            location_presentation=location_presentation,
        )
        if provenance in {SourceProvenance.STALE_CACHE, SourceProvenance.LOCAL_FALLBACK}:
            image.info[SKIP_CACHE_IMAGE_INFO_KEY] = True
        return attach_source_provenance(image, provenance)

    def _render_local_message(self, dimensions, theme, title, message, language):
        image = self._render_message(dimensions, theme, title, message, language)
        image.info[SKIP_CACHE_IMAGE_INFO_KEY] = True
        return attach_source_provenance(image, SourceProvenance.LOCAL_FALLBACK)

    def _cache_file(self, *, create=True):
        root = self.cache_dir(leaf="cache", create=create)
        return Path(root) / CACHE_FILE_NAME

    def _legacy_cache_file(self, *, create=False):
        root = self.cache_dir(leaf="cache", create=create)
        return Path(root) / LEGACY_CACHE_FILE_NAME

    def _oldest_cache_file(self, *, create=False):
        root = self.cache_dir(leaf="cache", create=create)
        return Path(root) / OLDEST_CACHE_FILE_NAME

    def _location_presentation_file(self, *, create=True):
        root = self.cache_dir(leaf="cache", create=create)
        return Path(root) / LOCATION_PRESENTATION_FILE_NAME

    def _read_cache(self, *, create=False):
        with _CACHE_LOCK:
            return self._read_cache_unlocked(create=create)

    def _read_cache_unlocked(self, *, create=False):
        paths = (
            self._cache_file(create=create),
            self._legacy_cache_file(create=False),
            self._oldest_cache_file(create=False),
        )
        candidates = []
        for path in paths:
            try:
                if not path.is_file() or path.stat().st_size > CACHE_MAX_BYTES:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                if type(payload) is not dict or set(payload) not in (
                    {"fetched_at", "summary"},
                    {"fetched_at", "summary", "location_fingerprint"},
                    {
                        "fetched_at",
                        "summary",
                        "location_fingerprint",
                        "location_language",
                    },
                ):
                    continue
                fetched_at = _number(payload.get("fetched_at"), 0, 100_000_000_000)
                if fetched_at is None:
                    continue
                location_fingerprint = _sanitize_location_fingerprint(payload.get("location_fingerprint"))
                if (
                    "location_fingerprint" in payload
                    and payload["location_fingerprint"] is not None
                    and location_fingerprint is None
                ):
                    continue
                location_language = _sanitize_location_language(payload.get("location_language"))
                if "location_language" in payload and (location_fingerprint is None or location_language is None):
                    continue
                summary = sanitize_summary(payload.get("summary"))
                if summary.get("schema_version") == 3 and summary.get("location") is not None:
                    continue
                candidates.append(
                    {
                        "fetched_at": fetched_at,
                        "summary": summary,
                        "location_fingerprint": location_fingerprint,
                        "location_language": location_language,
                    }
                )
            except (OSError, UnicodeError, json.JSONDecodeError, SummaryContractError):
                continue
        if not candidates:
            return None
        now = time.time()

        def recency(candidate):
            content_age = _cached_content_age(candidate, now)
            return (
                content_age is None,
                math.inf if content_age is None else content_age,
                -candidate["fetched_at"],
            )

        return min(candidates, key=recency)

    def _write_cache(self, payload):
        with _CACHE_LOCK:
            self._write_cache_unlocked(payload)

    def _write_cache_unlocked(self, payload):
        fetched_at = _number(payload.get("fetched_at"), 0, 100_000_000_000)
        if fetched_at is None:
            raise SummaryContractError("cache fetched_at is invalid")
        summary = sanitize_summary(payload.get("summary"))
        location_fingerprint = _sanitize_location_fingerprint(payload.get("location_fingerprint"))
        if payload.get("location_fingerprint") is not None and location_fingerprint is None:
            raise SummaryContractError("location fingerprint is invalid")
        location_language = _sanitize_location_language(payload.get("location_language"))
        if location_fingerprint is not None and location_language is None:
            raise SummaryContractError("location language is invalid")
        disk_summary = deepcopy(summary)
        if disk_summary["schema_version"] == 3:
            disk_summary["location"] = None
        disk_payload = {"fetched_at": fetched_at, "summary": disk_summary}
        if location_fingerprint is not None:
            disk_payload["location_fingerprint"] = location_fingerprint
            disk_payload["location_language"] = location_language
        path = self._cache_file(create=True)
        atomic_write_json(
            path,
            disk_payload,
            mode=0o600,
        )

    def _presentation_for_cache(self, cached, now, language):
        if not cached or cached.get("location_language") != language:
            return None
        return self._read_location_presentation(
            now=now,
            expected_fingerprint=cached.get("location_fingerprint"),
            expected_language=language,
        )

    def _location_presentation_for_live(
        self,
        client,
        device_config,
        location,
        location_fingerprint,
        language,
        now,
    ):
        existing = self._read_location_presentation(
            now=now,
            expected_fingerprint=location_fingerprint,
            expected_language=language,
        )
        if existing is not None:
            return self._refresh_matching_location_presentation(
                existing,
                location["captured_at"],
                now,
            )

        api_key = self._google_maps_api_key(device_config)
        if not api_key:
            logger.warning("Vehicle location map unavailable reason=missing_google_key")
            return None

        coordinate = _location_coordinate(location)
        try:
            geocode_result = client.request_json(
                "GET",
                f"{GOOGLE_GEOCODE_URL}?{urlencode({'latlng': coordinate, 'language': language, 'key': api_key})}",
                allow_redirects=False,
                timeout=(4, 8),
                max_bytes=LOCATION_MAP_MAX_BYTES,
            )
            address = _google_formatted_address(geocode_result.data)
        except Exception as exc:
            _log_google_failure("geocode", exc)
            return None

        map_query = urlencode(
            {
                "center": coordinate,
                "zoom": "15",
                "size": f"{LOCATION_MAP_SIZE[0]}x{LOCATION_MAP_SIZE[1]}",
                "scale": "1",
                "format": "png",
                "maptype": "roadmap",
                "language": language,
                "markers": f"color:red|{coordinate}",
                "key": api_key,
            }
        )
        try:
            map_result = client.request_bytes(
                "GET",
                f"{GOOGLE_STATIC_MAP_URL}?{map_query}",
                allow_redirects=False,
                timeout=(4, 10),
                max_bytes=LOCATION_MAP_MAX_BYTES,
            )
            map_image, map_png_base64 = _validated_location_map(map_result.data)
        except Exception as exc:
            _log_google_failure("static_map", exc)
            return None

        presentation = {
            "location_fingerprint": location_fingerprint,
            "captured_at": location["captured_at"],
            "fetched_at": now,
            "formatted_address": address,
            "language": language,
            "map_png_base64": map_png_base64,
            "map_image": map_image,
            "age_seconds": _timestamp_age(location["captured_at"], now),
            "_pending_write": True,
        }
        return presentation

    def _google_maps_api_key(self, device_config):
        for key_name in GOOGLE_MAPS_KEY_ENVS:
            value = str(device_config.load_env_key(key_name) or "").strip()
            if value:
                return value
        return ""

    def _read_location_presentation(
        self,
        *,
        now,
        expected_fingerprint,
        expected_language,
    ):
        expected_fingerprint = _sanitize_location_fingerprint(expected_fingerprint)
        if expected_fingerprint is None:
            return None
        path = self._location_presentation_file(create=False)
        try:
            if not path.is_file() or path.stat().st_size > LOCATION_PRESENTATION_MAX_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if type(payload) is not dict or set(payload) != {
                "version",
                "location_fingerprint",
                "captured_at",
                "fetched_at",
                "formatted_address",
                "language",
                "map_png_base64",
            }:
                return None
            if type(payload["version"]) is not int or payload["version"] != 1:
                return None
            fingerprint = _sanitize_location_fingerprint(payload["location_fingerprint"])
            if fingerprint != expected_fingerprint:
                return None
            language = _sanitize_location_language(payload["language"])
            if language is None or language != expected_language:
                return None
            captured_at = _timestamp(payload["captured_at"], "captured_at")
            fetched_at = _number(payload["fetched_at"], 0, 100_000_000_000)
            if fetched_at is None:
                return None
            address = _string(
                payload["formatted_address"],
                LOCATION_ADDRESS_MAX_CHARS,
                "formatted_address",
            )
            encoded_map = payload["map_png_base64"]
            if type(encoded_map) is not str or len(encoded_map) > ((LOCATION_MAP_MAX_BYTES * 4) // 3) + 8:
                return None
            map_bytes = base64.b64decode(encoded_map, validate=True)
            if len(map_bytes) > LOCATION_MAP_MAX_BYTES:
                return None
            map_image, clean_encoded_map = _validated_location_map(map_bytes)
            age_seconds = _timestamp_age(captured_at, now)
            if age_seconds < -LOCATION_FUTURE_TOLERANCE_SECONDS or age_seconds > LOCATION_MAX_AGE_SECONDS:
                return None
            return {
                "location_fingerprint": fingerprint,
                "captured_at": captured_at,
                "fetched_at": fetched_at,
                "formatted_address": address,
                "language": language,
                "map_png_base64": clean_encoded_map,
                "map_image": map_image,
                "age_seconds": max(0, age_seconds),
            }
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
        ) as exc:
            _log_location_cache_failure("read", exc)
            return None

    def _refresh_matching_location_presentation(self, presentation, captured_at, now):
        existing_epoch = _timestamp_epoch(presentation["captured_at"])
        updated_epoch = _timestamp_epoch(captured_at)
        if updated_epoch < existing_epoch:
            return presentation
        refreshed = dict(presentation)
        refreshed["captured_at"] = captured_at
        refreshed["fetched_at"] = now
        refreshed["age_seconds"] = max(0, _timestamp_age(captured_at, now))
        if captured_at != presentation["captured_at"]:
            try:
                self._write_location_presentation(refreshed)
            except Exception as exc:
                _log_location_cache_failure("refresh", exc)
        return refreshed

    def _write_location_presentation(self, presentation):
        fingerprint = _sanitize_location_fingerprint(presentation.get("location_fingerprint"))
        if fingerprint is None:
            raise SummaryContractError("location fingerprint is invalid")
        captured_at = _timestamp(presentation.get("captured_at"), "captured_at")
        fetched_at = _number(
            presentation.get("fetched_at"),
            0,
            100_000_000_000,
        )
        if fetched_at is None:
            raise SummaryContractError("location fetched_at is invalid")
        address = _string(
            presentation.get("formatted_address"),
            LOCATION_ADDRESS_MAX_CHARS,
            "formatted_address",
        )
        language = _sanitize_location_language(presentation.get("language"))
        if language is None:
            raise SummaryContractError("location language is invalid")
        encoded_map = presentation.get("map_png_base64")
        if type(encoded_map) is not str:
            raise SummaryContractError("location map is invalid")
        map_bytes = base64.b64decode(encoded_map, validate=True)
        _, clean_encoded_map = _validated_location_map(map_bytes)
        payload = {
            "version": 1,
            "location_fingerprint": fingerprint,
            "captured_at": captured_at,
            "fetched_at": fetched_at,
            "formatted_address": address,
            "language": language,
            "map_png_base64": clean_encoded_map,
        }
        encoded_size = len((json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
        if encoded_size > LOCATION_PRESENTATION_MAX_BYTES:
            raise SummaryContractError("location presentation is too large")
        atomic_write_json(
            self._location_presentation_file(create=True),
            payload,
            mode=0o600,
        )

    @staticmethod
    def _cache_is_fresh(cached, cache_seconds, now):
        if not cached or cache_seconds <= 0:
            return False
        age = now - cached["fetched_at"]
        return 0 <= age < cache_seconds

    def _render_summary(
        self,
        summary,
        dimensions,
        theme,
        settings,
        location_presentation=None,
    ):
        colors = _render_colors(theme)
        language = _language(settings)
        canvas = Image.new("RGB", (800, 480), colors["background"])
        draw = ImageDraw.Draw(canvas)
        _draw_dashboard(canvas, draw, summary, theme, settings, colors, language, location_presentation)

        if dimensions != (800, 480):
            canvas = canvas.resize(dimensions, Image.Resampling.LANCZOS)
        return canvas

    def _render_message(self, dimensions, theme, title, message, language):
        colors = _render_colors(theme)
        canvas = Image.new("RGB", (800, 480), colors["background"])
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((52, 48, 748, 432), radius=28, fill=colors["surface"], outline=colors["rule"], width=2)
        _draw_vehicle_silhouette(draw, (282, 105), colors, scale=1.55)
        _center_text(draw, 400, 252, title, _font(31, True), colors["ink"])
        _center_text(draw, 400, 305, message, _font(17), colors["muted"])
        _center_text(
            draw,
            400,
            365,
            _t(language, "setup_footer"),
            _font(14, True),
            colors["accent"],
        )
        if dimensions != (800, 480):
            canvas = canvas.resize(dimensions, Image.Resampling.LANCZOS)
        return canvas


def sanitize_summary(payload):
    if type(payload) is not dict:
        raise SummaryContractError("summary fields are invalid")
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int:
        raise SummaryContractError("schema version is unsupported")
    if schema_version == 1:
        return _sanitize_summary_v1(payload)
    if schema_version == 2:
        return _sanitize_summary_v2(payload)
    if schema_version == 3:
        return _sanitize_summary_v3(payload)
    raise SummaryContractError("schema version is unsupported")


def _sanitize_snapshot(root):
    snapshot_raw = _object(root["snapshot"], _SNAPSHOT_KEYS, "snapshot")
    freshness = _string(snapshot_raw["freshness"], 20, "snapshot.freshness")
    if freshness not in _FRESHNESS:
        raise SummaryContractError("snapshot freshness is invalid")
    connectivity = _string(
        snapshot_raw["vehicle_connectivity"],
        32,
        "snapshot.vehicle_connectivity",
    ).lower()
    if connectivity not in _CONNECTIVITY:
        connectivity = "unknown"
    return {
        "captured_at": _nullable_timestamp(
            snapshot_raw["captured_at"],
            "snapshot.captured_at",
        ),
        "freshness": freshness,
        "age_seconds": _nullable_number(
            snapshot_raw["age_seconds"],
            0,
            604_800,
            "snapshot.age_seconds",
        ),
        "vehicle_connectivity": connectivity,
    }


def _sanitize_open_items(open_raw):
    if type(open_raw) is not list or len(open_raw) > len(_OPEN_CLOSURES):
        raise SummaryContractError("closures.open is invalid")
    open_items = []
    for item in open_raw:
        label = _string(item, 40, "closures.open")
        if label not in _OPEN_CLOSURES or label in open_items:
            raise SummaryContractError("closures.open contains an invalid label")
        open_items.append(label)
    return open_items


def _sanitize_summary_v1(payload):
    root = _object(payload, _TOP_KEYS_V1, "summary")
    served_at = _timestamp(root["served_at"], "served_at")
    snapshot = _sanitize_snapshot(root)

    vehicle_raw = _object(root["vehicle"], _VEHICLE_KEYS_V1, "vehicle")
    if vehicle_raw["key"] != "primary":
        raise SummaryContractError("vehicle key is invalid")
    vehicle = {
        "key": "primary",
        "display_name": _string(vehicle_raw["display_name"], 64, "vehicle.display_name"),
        "model": _nullable_string(vehicle_raw["model"], 40, "vehicle.model"),
        "trim": _nullable_string(vehicle_raw["trim"], 40, "vehicle.trim"),
        "locked": _nullable_bool(vehicle_raw["locked"], "vehicle.locked"),
        "software_version": _nullable_string(
            vehicle_raw["software_version"],
            64,
            "vehicle.software_version",
        ),
        "odometer": _measurement(
            vehicle_raw["odometer"],
            0,
            2_000_000,
            "vehicle.odometer",
        ),
    }

    battery_raw = _object(root["battery"], _BATTERY_KEYS_V1, "battery")
    battery = {
        "level_percent": _nullable_number(
            battery_raw["level_percent"],
            0,
            100,
            "battery.level_percent",
        ),
        "estimated_range": _measurement(
            battery_raw["estimated_range"],
            0,
            2_500,
            "battery.estimated_range",
        ),
        "charging_state": _nullable_string(
            battery_raw["charging_state"],
            40,
            "battery.charging_state",
        ),
        "charge_limit_percent": _nullable_number(
            battery_raw["charge_limit_percent"],
            0,
            100,
            "battery.charge_limit_percent",
        ),
        "time_to_full_minutes": _nullable_number(
            battery_raw["time_to_full_minutes"],
            0,
            10_000,
            "battery.time_to_full_minutes",
        ),
        "power_kw": _nullable_number(
            battery_raw["power_kw"],
            0,
            1_000,
            "battery.power_kw",
        ),
    }

    climate_raw = _object(root["climate"], _CLIMATE_KEYS_V1, "climate")
    climate = {
        "inside_temp_c": _nullable_number(
            climate_raw["inside_temp_c"],
            -100,
            100,
            "climate.inside_temp_c",
        ),
        "outside_temp_c": _nullable_number(
            climate_raw["outside_temp_c"],
            -100,
            100,
            "climate.outside_temp_c",
        ),
        "is_climate_on": _nullable_bool(
            climate_raw["is_climate_on"],
            "climate.is_climate_on",
        ),
    }

    closures_raw = _object(root["closures"], _CLOSURE_KEYS_V1, "closures")
    open_items = _sanitize_open_items(closures_raw["open"])
    all_closed = _nullable_bool(closures_raw["all_closed"], "closures.all_closed")
    charge_port_open = _nullable_bool(
        closures_raw["charge_port_open"],
        "closures.charge_port_open",
    )
    if all_closed is True and (open_items or charge_port_open is not False):
        raise SummaryContractError("closures contradict all_closed")
    if all_closed is False and not open_items and charge_port_open is not True:
        raise SummaryContractError("closures do not identify an opening")
    closures = {
        "all_closed": all_closed,
        "open": open_items,
        "charge_port_open": charge_port_open,
    }

    return {
        "schema_version": 1,
        "served_at": served_at,
        "snapshot": snapshot,
        "vehicle": vehicle,
        "battery": battery,
        "climate": climate,
        "closures": closures,
    }


def _sanitize_summary_v2(payload):
    root = _object(payload, _TOP_KEYS_V2, "summary")
    served_at = _timestamp(root["served_at"], "served_at")
    snapshot = _sanitize_snapshot(root)

    vehicle_raw = _object(root["vehicle"], _VEHICLE_KEYS_V2, "vehicle")
    if vehicle_raw["key"] != "primary":
        raise SummaryContractError("vehicle key is invalid")
    speed_limit_raw = _object(
        vehicle_raw["speed_limit_mode"],
        _SPEED_LIMIT_MODE_KEYS,
        "vehicle.speed_limit_mode",
    )
    vehicle = {
        "key": "primary",
        "display_name": _string(vehicle_raw["display_name"], 64, "vehicle.display_name"),
        "model": _nullable_string(vehicle_raw["model"], 40, "vehicle.model"),
        "trim": _nullable_string(vehicle_raw["trim"], 40, "vehicle.trim"),
        "locked": _nullable_bool(vehicle_raw["locked"], "vehicle.locked"),
        "software_version": _nullable_string(
            vehicle_raw["software_version"],
            64,
            "vehicle.software_version",
        ),
        "odometer": _measurement(
            vehicle_raw["odometer"],
            0,
            2_000_000,
            "vehicle.odometer",
        ),
        "exterior_color": _nullable_string(
            vehicle_raw["exterior_color"],
            64,
            "vehicle.exterior_color",
        ),
        "wheel_type": _nullable_string(vehicle_raw["wheel_type"], 64, "vehicle.wheel_type"),
        "roof_color": _nullable_string(vehicle_raw["roof_color"], 64, "vehicle.roof_color"),
        "charge_port_type": _nullable_string(
            vehicle_raw["charge_port_type"],
            64,
            "vehicle.charge_port_type",
        ),
        "efficiency_package": _nullable_string(
            vehicle_raw["efficiency_package"],
            64,
            "vehicle.efficiency_package",
        ),
        "rear_seat_heaters": _nullable_string(
            vehicle_raw["rear_seat_heaters"],
            64,
            "vehicle.rear_seat_heaters",
        ),
        "right_hand_drive": _nullable_bool(
            vehicle_raw["right_hand_drive"],
            "vehicle.right_hand_drive",
        ),
        "europe_vehicle": _nullable_bool(
            vehicle_raw["europe_vehicle"],
            "vehicle.europe_vehicle",
        ),
        "sunroof_installed": _nullable_string(
            vehicle_raw["sunroof_installed"],
            64,
            "vehicle.sunroof_installed",
        ),
        "sentry_mode": _nullable_string(
            vehicle_raw["sentry_mode"],
            32,
            "vehicle.sentry_mode",
        ),
        "service_mode": _nullable_bool(
            vehicle_raw["service_mode"],
            "vehicle.service_mode",
        ),
        "valet_mode": _nullable_bool(vehicle_raw["valet_mode"], "vehicle.valet_mode"),
        "center_display_state": _nullable_string(
            vehicle_raw["center_display_state"],
            32,
            "vehicle.center_display_state",
        ),
        "speed_limit_mode": {
            "active": _nullable_bool(
                speed_limit_raw["active"],
                "vehicle.speed_limit_mode.active",
            ),
            "limit": _measurement(
                speed_limit_raw["limit"],
                0,
                1_000,
                "vehicle.speed_limit_mode.limit",
                {"mi/h"},
            ),
        },
    }

    battery_raw = _object(root["battery"], _BATTERY_KEYS_V2, "battery")
    battery = {
        "level_percent": _nullable_number(battery_raw["level_percent"], 0, 100, "battery.level_percent"),
        "usable_level_percent": _nullable_number(
            battery_raw["usable_level_percent"],
            0,
            100,
            "battery.usable_level_percent",
        ),
        "rated_range": _measurement(battery_raw["rated_range"], 0, 2_500, "battery.rated_range"),
        "estimated_range": _measurement(battery_raw["estimated_range"], 0, 2_500, "battery.estimated_range"),
    }

    charging_raw = _object(root["charging"], _CHARGING_KEYS_V2, "charging")
    scheduled_raw = _object(
        charging_raw["scheduled"],
        _SCHEDULED_KEYS,
        "charging.scheduled",
    )
    charging = {
        "state": _nullable_string(charging_raw["state"], 40, "charging.state"),
        "charge_limit_percent": _nullable_number(
            charging_raw["charge_limit_percent"],
            0,
            100,
            "charging.charge_limit_percent",
        ),
        "time_to_full_minutes": _nullable_number(
            charging_raw["time_to_full_minutes"],
            0,
            10_000,
            "charging.time_to_full_minutes",
        ),
        "power_kw": _nullable_number(charging_raw["power_kw"], 0, 1_000, "charging.power_kw"),
        "energy_added_kwh": _nullable_number(
            charging_raw["energy_added_kwh"],
            0,
            1_000,
            "charging.energy_added_kwh",
        ),
        "rate": _measurement(charging_raw["rate"], 0, 5_000, "charging.rate", {"mi/h"}),
        "actual_current_a": _nullable_number(
            charging_raw["actual_current_a"],
            0,
            2_000,
            "charging.actual_current_a",
        ),
        "voltage_v": _nullable_number(charging_raw["voltage_v"], 0, 2_000, "charging.voltage_v"),
        "phases": _nullable_number(charging_raw["phases"], 0, 10, "charging.phases"),
        "requested_current_a": _nullable_number(
            charging_raw["requested_current_a"],
            0,
            2_000,
            "charging.requested_current_a",
        ),
        "max_current_a": _nullable_number(
            charging_raw["max_current_a"],
            0,
            2_000,
            "charging.max_current_a",
        ),
        "enabled": _nullable_bool(charging_raw["enabled"], "charging.enabled"),
        "cable_type": _nullable_string(charging_raw["cable_type"], 40, "charging.cable_type"),
        "fast_charger_present": _nullable_bool(
            charging_raw["fast_charger_present"],
            "charging.fast_charger_present",
        ),
        "fast_charger_type": _nullable_string(
            charging_raw["fast_charger_type"],
            40,
            "charging.fast_charger_type",
        ),
        "port_latch": _nullable_string(charging_raw["port_latch"], 40, "charging.port_latch"),
        "port_cold_weather_mode": _nullable_bool(
            charging_raw["port_cold_weather_mode"],
            "charging.port_cold_weather_mode",
        ),
        "preconditioning": _nullable_bool(
            charging_raw["preconditioning"],
            "charging.preconditioning",
        ),
        "not_enough_power_to_heat": _nullable_bool(
            charging_raw["not_enough_power_to_heat"],
            "charging.not_enough_power_to_heat",
        ),
        "supercharger_trip_planner": _nullable_bool(
            charging_raw["supercharger_trip_planner"],
            "charging.supercharger_trip_planner",
        ),
        "scheduled": {
            "pending": _nullable_bool(
                scheduled_raw["pending"],
                "charging.scheduled.pending",
            ),
            "mode": _nullable_string(
                scheduled_raw["mode"],
                40,
                "charging.scheduled.mode",
            ),
        },
    }

    climate_raw = _object(root["climate"], _CLIMATE_KEYS_V2, "climate")
    seat_heaters = _sanitize_nullable_number_object(
        climate_raw["seat_heaters"],
        _SEAT_HEATER_KEYS,
        -1,
        10,
        "climate.seat_heaters",
    )
    seat_cooling = _sanitize_nullable_number_object(
        climate_raw["seat_cooling"],
        _FRONT_SEAT_KEYS,
        -1,
        10,
        "climate.seat_cooling",
    )
    auto_seat_climate = _sanitize_nullable_bool_object(
        climate_raw["auto_seat_climate"],
        _FRONT_SEAT_KEYS,
        "climate.auto_seat_climate",
    )
    cabin_overheat_raw = _object(
        climate_raw["cabin_overheat"],
        _CABIN_OVERHEAT_KEYS,
        "climate.cabin_overheat",
    )
    climate = {
        "inside_temp_c": _nullable_number(climate_raw["inside_temp_c"], -100, 100, "climate.inside_temp_c"),
        "outside_temp_c": _nullable_number(climate_raw["outside_temp_c"], -100, 100, "climate.outside_temp_c"),
        "is_climate_on": _nullable_bool(climate_raw["is_climate_on"], "climate.is_climate_on"),
        "driver_target_temp_c": _nullable_number(
            climate_raw["driver_target_temp_c"],
            -100,
            100,
            "climate.driver_target_temp_c",
        ),
        "passenger_target_temp_c": _nullable_number(
            climate_raw["passenger_target_temp_c"],
            -100,
            100,
            "climate.passenger_target_temp_c",
        ),
        "keeper_mode": _nullable_string(climate_raw["keeper_mode"], 40, "climate.keeper_mode"),
        "defrost_mode": _nullable_string(climate_raw["defrost_mode"], 40, "climate.defrost_mode"),
        "rear_defroster_on": _nullable_bool(
            climate_raw["rear_defroster_on"],
            "climate.rear_defroster_on",
        ),
        "battery_heater_on": _nullable_bool(
            climate_raw["battery_heater_on"],
            "climate.battery_heater_on",
        ),
        "wiper_heater_on": _nullable_bool(
            climate_raw["wiper_heater_on"],
            "climate.wiper_heater_on",
        ),
        "hvac_auto_mode": _nullable_string(climate_raw["hvac_auto_mode"], 40, "climate.hvac_auto_mode"),
        "fan_status": _nullable_number(climate_raw["fan_status"], -1, 20, "climate.fan_status"),
        "steering_wheel_heat_level": _nullable_number(
            climate_raw["steering_wheel_heat_level"],
            -1,
            10,
            "climate.steering_wheel_heat_level",
        ),
        "steering_wheel_heat_auto": _nullable_bool(
            climate_raw["steering_wheel_heat_auto"],
            "climate.steering_wheel_heat_auto",
        ),
        "seat_heaters": seat_heaters,
        "seat_cooling": seat_cooling,
        "auto_seat_climate": auto_seat_climate,
        "cabin_overheat": {
            "mode": _nullable_string(
                cabin_overheat_raw["mode"],
                40,
                "climate.cabin_overheat.mode",
            ),
            "temp_limit": _nullable_string(
                cabin_overheat_raw["temp_limit"],
                40,
                "climate.cabin_overheat.temp_limit",
            ),
        },
    }

    closures = _sanitize_closures_v2(root["closures"])

    tires_raw = _object(root["tires"], _TIRES_KEYS, "tires")
    pressures_raw = _object(
        tires_raw["pressures"],
        _TIRE_POSITIONS,
        "tires.pressures",
    )
    pressures = {
        position: _measurement(
            pressures_raw[position],
            0,
            20,
            f"tires.pressures.{position}",
            {"bar"},
        )
        for position in _TIRE_POSITIONS
    }
    soft_warnings = _sanitize_nullable_bool_object(
        tires_raw["soft_warnings"],
        _TIRE_POSITIONS,
        "tires.soft_warnings",
    )
    hard_warnings = _sanitize_nullable_bool_object(
        tires_raw["hard_warnings"],
        _TIRE_POSITIONS,
        "tires.hard_warnings",
    )
    tires = {
        "pressures": pressures,
        "soft_warnings": soft_warnings,
        "hard_warnings": hard_warnings,
    }

    update_raw = _object(
        root["software_update"],
        _SOFTWARE_UPDATE_KEYS,
        "software_update",
    )
    software_update = {
        "version": _nullable_string(update_raw["version"], 64, "software_update.version"),
        "download_percent": _nullable_number(
            update_raw["download_percent"],
            0,
            100,
            "software_update.download_percent",
        ),
        "install_percent": _nullable_number(
            update_raw["install_percent"],
            0,
            100,
            "software_update.install_percent",
        ),
        "expected_duration_minutes": _nullable_number(
            update_raw["expected_duration_minutes"],
            0,
            10_080,
            "software_update.expected_duration_minutes",
        ),
    }

    preferences_raw = _object(root["preferences"], _PREFERENCE_KEYS, "preferences")
    preferences = {
        "distance_unit": _nullable_enum(
            preferences_raw["distance_unit"],
            {"mi", "km"},
            "preferences.distance_unit",
        ),
        "temperature_unit": _nullable_enum(
            preferences_raw["temperature_unit"],
            {"C", "F"},
            "preferences.temperature_unit",
        ),
        "pressure_unit": _nullable_enum(
            preferences_raw["pressure_unit"],
            {"psi", "bar"},
            "preferences.pressure_unit",
        ),
        "charge_display_unit": _nullable_enum(
            preferences_raw["charge_display_unit"],
            {"distance", "percent", "unknown"},
            "preferences.charge_display_unit",
        ),
        "use_24_hour_time": _nullable_bool(
            preferences_raw["use_24_hour_time"],
            "preferences.use_24_hour_time",
        ),
    }

    return {
        "schema_version": 2,
        "served_at": served_at,
        "snapshot": snapshot,
        "vehicle": vehicle,
        "battery": battery,
        "charging": charging,
        "climate": climate,
        "closures": closures,
        "tires": tires,
        "software_update": software_update,
        "preferences": preferences,
    }


def _sanitize_summary_v3(payload):
    root = _object(payload, _TOP_KEYS_V3, "summary")
    v2_payload = {key: root[key] for key in _TOP_KEYS_V2}
    v2_payload["schema_version"] = 2
    result = _sanitize_summary_v2(v2_payload)
    result["schema_version"] = 3
    result["location"] = _sanitize_location(root["location"])
    return result


def _sanitize_location(value):
    if value is None:
        return None
    raw = _object(value, _LOCATION_KEYS, "location")
    return {
        "captured_at": _timestamp(raw["captured_at"], "location.captured_at"),
        "age_seconds": _required_number(
            raw["age_seconds"],
            0,
            LOCATION_MAX_AGE_SECONDS,
            "location.age_seconds",
        ),
        "latitude": _required_number(raw["latitude"], -90, 90, "location.latitude"),
        "longitude": _required_number(
            raw["longitude"],
            -180,
            180,
            "location.longitude",
        ),
    }


def _sanitize_nullable_bool_object(value, expected_keys, field):
    raw = _object(value, expected_keys, field)
    return {key: _nullable_bool(raw[key], f"{field}.{key}") for key in expected_keys}


def _sanitize_nullable_number_object(
    value,
    expected_keys,
    minimum,
    maximum,
    field,
):
    raw = _object(value, expected_keys, field)
    return {key: _nullable_number(raw[key], minimum, maximum, f"{field}.{key}") for key in expected_keys}


def _sanitize_closures_v2(value):
    raw = _object(value, _CLOSURE_KEYS_V2, "closures")
    open_items = _sanitize_open_items(raw["open"])
    all_closed = _nullable_bool(raw["all_closed"], "closures.all_closed")
    charge_port_open = _nullable_bool(
        raw["charge_port_open"],
        "closures.charge_port_open",
    )
    doors = _sanitize_nullable_bool_object(raw["doors"], _DOOR_KEYS, "closures.doors")
    windows = _sanitize_nullable_bool_object(
        raw["windows"],
        _WINDOW_KEYS,
        "closures.windows",
    )
    nested = {
        "driver_front_door": doors["driver_front"],
        "driver_rear_door": doors["driver_rear"],
        "passenger_front_door": doors["passenger_front"],
        "passenger_rear_door": doors["passenger_rear"],
        "front_trunk": doors["front_trunk"],
        "rear_trunk": doors["rear_trunk"],
        "driver_front_window": windows["driver_front"],
        "driver_rear_window": windows["driver_rear"],
        "passenger_front_window": windows["passenger_front"],
        "passenger_rear_window": windows["passenger_rear"],
    }
    for label, state in nested.items():
        if (label in open_items) != (state is True):
            raise SummaryContractError("closures open list contradicts nested state")
    known_open = charge_port_open is True or any(state is True for state in nested.values())
    all_known_closed = charge_port_open is False and all(state is False for state in nested.values())
    expected_all_closed = False if known_open else True if all_known_closed else None
    if all_closed is not expected_all_closed:
        raise SummaryContractError("closures contradict all_closed")
    return {
        "all_closed": all_closed,
        "open": open_items,
        "charge_port_open": charge_port_open,
        "doors": doors,
        "windows": windows,
    }


def _object(value, expected_keys, field):
    if type(value) is not dict or set(value) != expected_keys:
        raise SummaryContractError(f"{field} fields are invalid")
    return value


def _string(value, max_length, field):
    if not isinstance(value, str):
        raise SummaryContractError(f"{field} must be text")
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > max_length or any(ord(char) < 32 for char in normalized):
        raise SummaryContractError(f"{field} is invalid")
    return normalized


def _nullable_string(value, max_length, field):
    return None if value is None else _string(value, max_length, field)


def _nullable_enum(value, allowed, field):
    if value is None:
        return None
    if type(value) is not str or value not in allowed:
        raise SummaryContractError(f"{field} is invalid")
    return value


def _nullable_bool(value, field):
    if value is None or type(value) is bool:
        return value
    raise SummaryContractError(f"{field} must be boolean or null")


def _number(value, minimum, maximum):
    if type(value) not in {int, float}:
        return None
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        return None
    return number


def _nullable_number(value, minimum, maximum, field):
    if value is None:
        return None
    number = _number(value, minimum, maximum)
    if number is None:
        raise SummaryContractError(f"{field} is invalid")
    return number


def _required_number(value, minimum, maximum, field):
    number = _number(value, minimum, maximum)
    if number is None:
        raise SummaryContractError(f"{field} is invalid")
    return number


def _measurement(
    value,
    minimum,
    maximum,
    field,
    allowed_units=frozenset({"mi", "km"}),
):
    if value is None:
        return None
    item = _object(value, _MEASUREMENT_KEYS, field)
    number = _number(item["value"], minimum, maximum)
    if number is None:
        raise SummaryContractError(f"{field}.value is invalid")
    unit = item["unit"]
    if type(unit) is not str or unit not in allowed_units:
        raise SummaryContractError(f"{field}.unit is invalid")
    return {"value": number, "unit": unit}


def _timestamp(value, field):
    if not isinstance(value, str) or len(value) > 40:
        raise SummaryContractError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SummaryContractError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise SummaryContractError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nullable_timestamp(value, field):
    return None if value is None else _timestamp(value, field)


def _timestamp_epoch(value):
    normalized = _timestamp(value, "timestamp")
    return datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp()


def _timestamp_age(value, now):
    return float(now) - _timestamp_epoch(value)


def _location_coordinate(location):
    return f"{location['latitude']:.6f},{location['longitude']:.6f}"


def _location_fingerprint(location, token, language):
    presentation_identity = f"{language}\0{_location_coordinate(location)}"
    return hmac.new(
        str(token).encode("utf-8"),
        presentation_identity.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _sanitize_location_fingerprint(value):
    if value is None:
        return None
    if type(value) is not str or len(value) != 64:
        return None
    if any(character not in "0123456789abcdef" for character in value):
        return None
    return value


def _sanitize_location_language(value):
    return value if type(value) is str and value in {"en", "zh-CN"} else None


def _google_formatted_address(payload):
    if type(payload) is not dict or payload.get("status") != "OK":
        raise SummaryContractError("Google geocode status is invalid")
    results = payload.get("results")
    if type(results) is not list or not results or len(results) > 20:
        raise SummaryContractError("Google geocode results are invalid")
    first = results[0]
    if type(first) is not dict:
        raise SummaryContractError("Google geocode result is invalid")
    return _string(
        first.get("formatted_address"),
        LOCATION_ADDRESS_MAX_CHARS,
        "formatted_address",
    )


def _validated_location_map(payload):
    if type(payload) is not bytes:
        raise SummaryContractError("Google map payload is invalid")
    with safe_open_image(payload, limits=LOCATION_MAP_LIMITS) as source:
        if source.size != LOCATION_MAP_SIZE:
            raise SummaryContractError("Google map dimensions are invalid")
        map_image = source.convert("RGB")
    buffer = BytesIO()
    map_image.save(buffer, format="PNG")
    clean_payload = buffer.getvalue()
    if len(clean_payload) > LOCATION_MAP_MAX_BYTES:
        raise SummaryContractError("Google map payload is too large")
    return map_image, base64.b64encode(clean_payload).decode("ascii")


def _advance_cached_age(summary, fetched_at, now):
    result = deepcopy(summary)
    age = result["snapshot"]["age_seconds"]
    if age is not None:
        result["snapshot"]["age_seconds"] = min(
            604_800,
            max(0, int(age + max(0, now - fetched_at))),
        )
    return result


def _summary_within_max_stale(summary):
    age = summary["snapshot"]["age_seconds"]
    return age is None or age <= LOCAL_MAX_STALE_SECONDS


def _cache_within_max_stale(cached, now):
    content_age = _cached_content_age(cached, now)
    return content_age is not None and content_age <= LOCAL_MAX_STALE_SECONDS


def _cached_content_age(cached, now):
    if not cached or not cached.get("summary"):
        return None
    elapsed = now - cached["fetched_at"]
    if not math.isfinite(elapsed) or elapsed < 0:
        return None
    age = cached["summary"]["snapshot"]["age_seconds"]
    return elapsed if age is None else age + elapsed


def _should_replace_local_cache(cached, summary, now):
    if summary["snapshot"]["freshness"] != "stale_cache" or not cached:
        return True
    if not _cache_within_max_stale(cached, now):
        return True
    if _same_cached_snapshot(cached, summary):
        # served_at orders bridge responses, not Tesla connectivity checks.
        return _timestamp_epoch(summary["served_at"]) > _timestamp_epoch(cached["summary"]["served_at"])
    new_captured_at = summary["snapshot"]["captured_at"]
    old_captured_at = cached["summary"]["snapshot"]["captured_at"]
    if new_captured_at and old_captured_at:
        return _timestamp_epoch(new_captured_at) > _timestamp_epoch(old_captured_at)
    new_age = summary["snapshot"]["age_seconds"]
    old_age = _cached_content_age(cached, now)
    return new_age is not None and (old_age is None or new_age < old_age)


def _same_cached_snapshot(cached, summary):
    captured_at = summary["snapshot"]["captured_at"]
    return bool(captured_at and cached and captured_at == cached["summary"]["snapshot"]["captured_at"])


def _bridge_provenance(summary):
    if _is_resting_vehicle_snapshot(summary):
        return SourceProvenance.FRESH_CACHE
    return {
        "live": SourceProvenance.LIVE,
        "fresh_cache": SourceProvenance.FRESH_CACHE,
        "stale_cache": SourceProvenance.STALE_CACHE,
    }[summary["snapshot"]["freshness"]]


def _local_cache_provenance(summary):
    if summary["snapshot"]["freshness"] == "stale_cache" and not _is_resting_vehicle_snapshot(summary):
        return SourceProvenance.STALE_CACHE
    return SourceProvenance.FRESH_CACHE


def _is_resting_vehicle_snapshot(summary):
    # An asleep/offline vehicle is not woken, so the bridge marks its last snapshot
    # stale even though nothing newer exists; only a provider fallback is unhealthy.
    snapshot = summary["snapshot"]
    return snapshot["freshness"] == "stale_cache" and snapshot["vehicle_connectivity"] in _RESTING_CONNECTIVITY


def _log_bridge_failure(exc, *, has_cache):
    status = exc.status if isinstance(exc, HttpStatusError) else None
    detail = f" status={status}" if status is not None else ""
    logger.warning(
        "Vehicle bridge unavailable type=%s%s cached=%s",
        type(exc).__name__,
        detail,
        bool(has_cache),
    )


def _log_google_failure(kind, exc):
    status = exc.status if isinstance(exc, HttpStatusError) else None
    detail = f" status={status}" if status is not None else ""
    logger.warning(
        "Vehicle Google location unavailable kind=%s type=%s%s",
        kind,
        type(exc).__name__,
        detail,
    )


def _log_location_cache_failure(operation, exc):
    logger.warning(
        "Vehicle location cache unavailable operation=%s type=%s",
        operation,
        type(exc).__name__,
    )


def _language(settings):
    value = str((settings or {}).get("language") or "zh-CN").strip().lower()
    return "en" if value in {"en", "en-us", "english"} else "zh-CN"


def _t(language, key, **values):
    template = _UI_TEXT[language][key]
    return template.format(**values) if values else template


def _enum_text(language, category, value):
    if value is None:
        return _t(language, "unknown")
    raw = str(value).strip()
    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    mappings = _ENUM_TEXT.get(language, {}).get(category, {})
    translated = mappings.get(normalized) or mappings.get(normalized.replace("_", ""))
    if translated is not None:
        return translated
    if language == "en":
        return raw.replace("_", " ").upper()
    provider = _ENUM_TEXT["zh-CN"].get("provider", {})
    return provider.get(normalized) or provider.get(normalized.replace("_", "")) or raw


def _fixed_ui_characters(language):
    chunks = list(_UI_TEXT[language].values())
    for values in _ENUM_TEXT.get(language, {}).values():
        chunks.extend(values.values())
    chunks.extend(_OPENING_TEXT[language].values())
    return tuple(
        sorted(
            {character for chunk in chunks for character in chunk if ord(character) > 127 and not character.isspace()}
        )
    )


def _bool_setting(settings, key, default):
    value = settings.get(key, default)
    if type(value) is bool:
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(default)


def _int_setting(settings, key, default, minimum, maximum):
    try:
        value = int(settings.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _font(size, bold=False):
    return get_base_ui_font(size, bold=bold)


def _ellipsize_text(draw, text, font, max_width):
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    ellipsis = "..."
    ellipsis_width = draw.textbbox((0, 0), ellipsis, font=font)[2]
    if ellipsis_width > max_width:
        return ""
    low, high = 0, len(text)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = text[:midpoint].rstrip() + ellipsis
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            low = midpoint
        else:
            high = midpoint - 1
    return text[:low].rstrip() + ellipsis


def _render_colors(theme):
    night = isinstance(theme, dict) and theme.get("mode") == "night"
    roles = theme.get("palette", {}) if isinstance(theme, dict) else {}
    defaults = {
        "background": (11, 16, 23) if night else (242, 237, 228),
        "surface": (22, 29, 38) if night else (251, 249, 244),
        "ink": (240, 244, 247) if night else (24, 28, 33),
        "muted": (161, 174, 188) if night else (91, 96, 102),
        "rule": (59, 70, 83) if night else (203, 199, 190),
        "accent": (244, 111, 96) if night else (180, 54, 44),
    }

    def role(name, fallback=None):
        fallback = defaults.get(name, defaults["surface"]) if fallback is None else fallback
        value = roles.get(name, fallback) if isinstance(roles, dict) else fallback
        if isinstance(value, (tuple, list)) and len(value) == 3:
            try:
                channels = tuple(max(0, min(255, int(item))) for item in value)
                return channels
            except (TypeError, ValueError):
                pass
        return fallback

    background = role("background")
    surface = role("panel", defaults["surface"])
    return {
        "background": background,
        "surface": surface,
        "ink": role("ink"),
        "muted": role("muted"),
        "rule": role("rule"),
        "accent": role("accent"),
        "good": (35, 116, 79) if not night else (91, 207, 146),
        "warning": (138, 79, 0) if not night else (239, 179, 83),
        "track": (219, 215, 206) if not night else (50, 59, 70),
        "battery_high": (46, 160, 82) if not night else (88, 214, 128),
        "battery_mid": (230, 168, 0) if not night else (247, 199, 64),
        "battery_low": (214, 48, 38) if not night else (255, 104, 90),
    }


# One colour per section, so energy, charging, security, tyres, climate, location and the
# vehicle read apart at a glance (solid tiles only: small text stays in ink).
SECTION_COLOURS = {
    "energy": (46, 160, 82),
    "charging": (224, 140, 0),
    "security": (31, 95, 191),
    "tires": (112, 72, 232),
    "climate": (12, 150, 160),
    "location": (214, 48, 38),
    "vehicle_info": (70, 76, 84),
    "freshness": (70, 76, 84),
}


def _card(draw, box, colors):
    draw.rounded_rectangle(box, radius=17, fill=colors["background"], outline=colors["rule"], width=1)


def _section_label(canvas, draw, left, top, text, colors, icon_kind):
    """The section's badge (its icon on its own colour) and name; returns the name's right edge."""
    has_icon = _draw_section_badge(canvas, draw, icon_kind, left, top - 3, colors)
    text_left = left + 27 if has_icon else left
    font = _font(13, True)
    draw.text((text_left, top), text, font=font, fill=colors["ink"])
    return text_left + draw.textbbox((0, 0), text, font=font)[2]


def _draw_section_badge(canvas, draw, kind, left, top, colors, size=20):
    """A rounded tile in the section's colour with its icon knocked out in white (the icon's own 20 px box)."""
    mask = _load_dashboard_icon_mask(kind)
    if mask is None:
        return False
    fill = SECTION_COLOURS.get(kind, colors["muted"])
    draw.rounded_rectangle((left, top, left + size - 1, top + size - 1), radius=5, fill=fill)
    glyph_size = size - 6
    glyph = Image.new("RGBA", (glyph_size, glyph_size), (255, 255, 255, 0))
    glyph.putalpha(mask.resize((glyph_size, glyph_size), Image.Resampling.LANCZOS))
    canvas.paste(glyph, (left + 3, top + 3), glyph)
    return True


def _dashboard_row(
    draw,
    left,
    right,
    top,
    label,
    value,
    colors,
    *,
    label_size=11,
    value_size=12,
    label_width=72,
    value_color=None,
):
    label_font = _font(label_size, True)
    value_font = _font(value_size, True)
    draw.text((left, top), label, font=label_font, fill=colors["muted"])
    available = max(24, right - left - label_width)
    value_text = _ellipsize_text(draw, str(value), value_font, available)
    _right_text(draw, right, top - 1, value_text, value_font, value_color or colors["ink"])


def _v2_energy_rows(
    battery,
    charging,
    settings,
    preferences,
    limit_text,
    power_text,
    language,
):
    estimated_range = _measurement_text(
        battery["estimated_range"],
        settings,
        preferences,
        language,
    )
    left_text = _minutes_text(charging["time_to_full_minutes"], language)
    energy_added = _number_with_unit(
        charging["energy_added_kwh"],
        "unit_kwh",
        1,
        language,
    )
    input_text = _charging_input_text(charging, language)
    request_text = _charging_request_text(charging, language)
    return (
        (_t(language, "estimated_range"), estimated_range),
        (_t(language, "limit_left"), f"{limit_text} / {left_text}"),
        (_t(language, "power_added"), f"{power_text} / {energy_added}"),
        (_t(language, "input"), input_text),
        (_t(language, "request_max"), request_text),
        (
            _t(language, "rate"),
            _speed_text(charging["rate"], settings, preferences, language),
        ),
        (_t(language, "status"), _charging_flags_text(charging, language)),
    )


def _number_with_unit(value, unit_key, decimals=0, language="en"):
    if value is None:
        return "--"
    number = f"{value:g}" if decimals is None else f"{value:.{decimals}f}"
    return f"{number} {_t(language, unit_key)}"


def _charging_input_text(charging, language):
    current = _number_with_unit(charging["actual_current_a"], "unit_amp", 0, language)
    voltage = _number_with_unit(charging["voltage_v"], "unit_volt", 0, language)
    phases = charging["phases"]
    phase_text = "--" if phases is None else f"{phases:g} {_t(language, 'unit_phase')}"
    return f"{current} / {voltage} / {phase_text}"


def _charging_request_text(charging, language):
    requested = _number_with_unit(
        charging["requested_current_a"],
        "unit_amp",
        0,
        language,
    )
    maximum = _number_with_unit(
        charging["max_current_a"],
        "unit_amp",
        0,
        language,
    )
    return f"{requested} / {maximum}"


def _charging_flags_text(charging, language):
    flags = []
    if charging["not_enough_power_to_heat"] is True:
        flags.append(_t(language, "heat_power_low"))
    if charging["enabled"] is False:
        flags.append(_t(language, "disabled"))
    latch = charging["port_latch"]
    if latch and str(latch).lower() not in {"engaged", "unknown"}:
        flags.append(
            _t(
                language,
                "latch",
                state=_enum_text(language, "state", latch),
            )
        )
    if charging["port_cold_weather_mode"] is True:
        flags.append(_t(language, "port_heat"))
    if charging["preconditioning"] is True:
        flags.append(_t(language, "precondition"))
    if charging["fast_charger_present"] is True:
        charger = _enum_text(language, "provider", charging["fast_charger_type"])
        flags.append(_t(language, "fast_charge", kind=charger))
    if charging["scheduled"]["pending"] is True:
        mode = _enum_text(language, "state", charging["scheduled"]["mode"])
        flags.append(_t(language, "schedule", mode=mode))
    if charging["supercharger_trip_planner"] is True:
        flags.append(_t(language, "trip_plan"))
    if not flags and charging["enabled"] is True:
        return _t(language, "enabled")
    return " / ".join(flags[:2]) or "--"


def _security_flag_text(vehicle, settings, preferences, language):
    flags = []
    if vehicle["service_mode"] is True:
        flags.append(_t(language, "service_on"))
    if vehicle["valet_mode"] is True:
        flags.append(_t(language, "valet_on"))
    speed_limit = vehicle["speed_limit_mode"]
    if speed_limit["active"] is True:
        flags.append(
            _t(
                language,
                "speed_limit",
                value=_speed_text(
                    speed_limit["limit"],
                    settings,
                    preferences,
                    language,
                ),
            )
        )
    if (
        not flags
        and vehicle["service_mode"] is False
        and vehicle["valet_mode"] is False
        and speed_limit["active"] is False
    ):
        return _t(language, "modes_off")
    return " / ".join(flags) or _t(language, "modes_unknown")


def _percent_text(value):
    return "--" if value is None else f"{value:.0f}%"


def _compact_provider_text(language, value):
    if value is None:
        return "--"
    return _enum_text(language, "provider_compact", value)


def _vehicle_appearance_text(vehicle, language):
    values = [
        _compact_provider_text(language, value)
        for value in (
            vehicle["exterior_color"],
            vehicle["roof_color"],
            vehicle["wheel_type"],
        )
        if value is not None
    ]
    return "/".join(values) or _t(language, "not_reported")


def _vehicle_config_text(vehicle, language):
    drive = (
        _t(language, "rhd")
        if vehicle["right_hand_drive"] is True
        else _t(language, "lhd")
        if vehicle["right_hand_drive"] is False
        else "--"
    )
    region = (
        _t(language, "eu")
        if vehicle["europe_vehicle"] is True
        else _t(language, "non_eu")
        if vehicle["europe_vehicle"] is False
        else "--"
    )
    values = [
        drive,
        region,
        _compact_provider_text(language, vehicle["charge_port_type"]),
    ]
    return "/".join(values)


def _tire_warning_level(tires, position):
    if tires["hard_warnings"][position] is True:
        return "hard"
    if tires["soft_warnings"][position] is True:
        return "soft"
    if tires["hard_warnings"][position] is None or tires["soft_warnings"][position] is None:
        return "unknown"
    return "none"


def _tire_status(tires, colors, language):
    hard = [
        _tire_abbreviation(position, language)
        for position, warning in tires["hard_warnings"].items()
        if warning is True
    ]
    soft = [
        _tire_abbreviation(position, language)
        for position, warning in tires["soft_warnings"].items()
        if warning is True and _tire_abbreviation(position, language) not in hard
    ]
    if hard:
        return (
            _t(language, "hard_warning", positions=" ".join(hard)),
            colors["accent"],
        )
    if soft:
        return (
            _t(language, "low_pressure", positions=" ".join(soft)),
            colors["warning"],
        )
    warnings = tuple(tires["hard_warnings"].values()) + tuple(tires["soft_warnings"].values())
    if any(warning is None for warning in warnings):
        return _t(language, "pressure_unknown"), colors["warning"]
    if any(value is None for value in tires["pressures"].values()):
        return _t(language, "pressure_partial"), colors["warning"]
    return _t(language, "pressure_ok"), colors["ink"]


def _tire_abbreviation(position, language):
    return _t(
        language,
        {
            "front_left": "tire_fl",
            "front_right": "tire_fr",
            "rear_left": "tire_rl",
            "rear_right": "tire_rr",
        }[position],
    )


def _pill(draw, box, text, fill, surface):
    draw.rounded_rectangle(box, radius=16, fill=fill)
    left, top, right, bottom = box
    font = _font(12, True)
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text(((left + right - width) / 2, (top + bottom - height) / 2 - 2), text, font=font, fill=surface)


def _connectivity_color(connectivity, colors):
    if connectivity == "online":
        return colors["good"]
    if connectivity in {"asleep", "unknown"}:
        return colors["muted"]
    if connectivity in {"offline", "unavailable", "in_service"}:
        return colors["warning"]
    return colors["muted"]


def _charging_state_color(state, colors):
    normalized = str(state or "").strip().lower().replace(" ", "_")
    if normalized in {"charging", "complete"}:
        return colors["good"]
    if normalized in {"disconnected", "stopped", "unknown", ""}:
        return colors["muted"]
    if normalized in {"fault", "error", "no_power"}:
        return colors["accent"]
    return colors["warning"]


def _bool_status(value, true_text, false_text, unknown_text="UNKNOWN"):
    if value is True:
        return true_text
    if value is False:
        return false_text
    return unknown_text


def _minutes_text(value, language="en"):
    if value is None:
        return "--"
    minutes = max(0, int(round(value)))
    if minutes < 60:
        return _t(language, "duration_minutes", value=minutes)
    hours, remainder = divmod(minutes, 60)
    if remainder == 0:
        return _t(language, "age_hours", value=hours)
    return _t(
        language,
        "duration_hours",
        hours=hours,
        minutes=remainder,
    )


def _closure_detail_lines(closures, language="en"):
    labels = _OPENING_TEXT[language]
    items = [labels[item] for item in closures["open"]]
    if closures["charge_port_open"] is True:
        items.append(labels["charge_port"])
    if not items:
        return [
            _t(language, "all_access_secure") if closures["all_closed"] is True else _t(language, "opening_incomplete")
        ]
    return items


def _closure_detail_line(draw, items, font, max_width, language="en"):
    """As many open items as fit on one line, then a "+N more" count."""

    for shown in range(len(items), 0, -1):
        text = " / ".join(items[:shown])
        if shown < len(items):
            text = f"{text} / {_t(language, 'more_open', count=len(items) - shown)}"
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return text
    # Not even one full item fits beside the count: shorten the item, keep the count.
    more = f" / {_t(language, 'more_open', count=len(items) - 1)}" if len(items) > 1 else ""
    head = _ellipsize_text(draw, items[0], font, max_width - draw.textbbox((0, 0), more, font=font)[2])
    return head + more if head else _ellipsize_text(draw, items[0], font, max_width)


def _draw_vehicle_silhouette(draw, origin, colors, scale=1.0):
    x, y = origin
    points = [(0, 48), (24, 22), (70, 10), (132, 12), (169, 35), (205, 42), (216, 61), (0, 61)]
    points = [(x + int(px * scale), y + int(py * scale)) for px, py in points]
    draw.polygon(points, fill=colors["accent"])
    for wheel_x in (48, 170):
        cx = x + int(wheel_x * scale)
        cy = y + int(61 * scale)
        radius = int(14 * scale)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colors["ink"])
        hub = max(3, int(6 * scale))
        draw.ellipse((cx - hub, cy - hub, cx + hub, cy + hub), fill=colors["surface"])


def _draw_dashboard_icon(canvas, kind, left, top, colors, size=20):
    mask = _load_dashboard_icon_mask(kind)
    if mask is None:
        return False

    resized_mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    glyph = Image.new("RGBA", (size, size), (*colors["ink"], 0))
    glyph.putalpha(resized_mask)
    canvas.paste(glyph, (left, top), glyph)
    return True


def _load_dashboard_icon_mask(kind):
    filename = DASHBOARD_ICON_FILES.get(kind)
    if filename is None:
        return None
    path = Path(__file__).with_name(DASHBOARD_ICON_DIR_NAME) / filename
    try:
        with safe_open_image(path, limits=DASHBOARD_ICON_LIMITS) as source:
            if source.size != (64, 64):
                raise ValueError("dashboard icon dimensions are invalid")
            return source.convert("RGBA").getchannel("A").copy()
    except (OSError, TypeError, ValueError) as exc:
        logger.warning(
            "Vehicle dashboard icon unavailable kind=%s type=%s",
            kind,
            type(exc).__name__,
        )
        return None


def _load_vehicle_art(charging_state=None):
    normalized = str(charging_state or "").strip().lower().replace(" ", "_")
    name = VEHICLE_CHARGING_IMAGE_NAME if normalized in VEHICLE_CHARGING_ART_STATES else VEHICLE_IMAGE_NAME
    image = safe_open_image(
        Path(__file__).with_name(name),
        limits=ImageLimits(
            max_bytes=2 * 1024 * 1024,
            max_width=2048,
            max_height=1024,
            max_pixels=2_000_000,
            allowed_formats=frozenset({"PNG"}),
        ),
    )
    return image.convert("RGBA")


def _draw_vehicle_wordmark(canvas, display_name, colors):
    normalized_name = " ".join(str(display_name).casefold().split())
    if normalized_name not in GREY_BULLET_NAMES:
        return False

    mask = _load_vehicle_wordmark_mask()
    if mask is None:
        return False

    left, top, right, bottom = VEHICLE_WORDMARK_BOX
    fitted_mask = mask.copy()
    fitted_mask.thumbnail(
        (right - left, bottom - top),
        Image.Resampling.LANCZOS,
    )
    glyph = Image.new("RGBA", fitted_mask.size, (*colors["ink"], 0))
    glyph.putalpha(fitted_mask)
    y = top + ((bottom - top - fitted_mask.height) // 2)
    canvas.paste(glyph, (left, y), glyph)
    return left + fitted_mask.width


def _load_vehicle_wordmark_mask():
    path = Path(__file__).with_name(VEHICLE_WORDMARK_NAME)
    try:
        with safe_open_image(path, limits=VEHICLE_WORDMARK_LIMITS) as source:
            if source.size != (736, 172):
                raise ValueError("vehicle wordmark dimensions are invalid")
            alpha = source.convert("RGBA").getchannel("A")
            if alpha.getbbox() is None:
                raise ValueError("vehicle wordmark is empty")
            return alpha.copy()
    except (OSError, TypeError, ValueError) as exc:
        logger.warning(
            "Vehicle wordmark unavailable type=%s",
            type(exc).__name__,
        )
        return None


def _measurement_text(measurement, settings, preferences=None, language="en"):
    if measurement is None:
        return "--"
    value = measurement["value"]
    if value is None:
        return "--"
    unit = measurement["unit"]
    requested = _preferred_unit(
        settings,
        preferences,
        "distanceUnit",
        "distance_unit",
        {"mi", "km"},
    )
    if requested == "km" and unit == "mi":
        value, unit = value * 1.609344, "km"
    elif requested == "mi" and unit == "km":
        value, unit = value / 1.609344, "mi"
    unit_key = "unit_km" if unit == "km" else "unit_mile"
    return f"{value:,.0f} {_t(language, unit_key)}"


def _temperature_text(value, settings=None, preferences=None, language="en"):
    if value is None:
        return "--"
    unit = _preferred_unit(
        settings,
        preferences,
        "temperatureUnit",
        "temperature_unit",
        {"C", "F"},
        normalize=str.upper,
    )
    if unit == "F":
        return f"{((value * 9 / 5) + 32):.0f}{_t(language, 'unit_f')}"
    return f"{value:.0f}{_t(language, 'unit_c')}"


def _pressure_text(measurement, settings=None, preferences=None, language="en"):
    if measurement is None:
        return "--"
    value = measurement["value"]
    if value is None:
        return "--"
    unit = _preferred_unit(
        settings,
        preferences,
        "pressureUnit",
        "pressure_unit",
        {"psi", "bar"},
    )
    if unit == "psi":
        return f"{(value * 14.5037738):.0f} {_t(language, 'unit_psi')}"
    return f"{value:.1f} {_t(language, 'unit_bar')}"


def _speed_text(measurement, settings=None, preferences=None, language="en"):
    if measurement is None:
        return "--"
    value = measurement["value"]
    if value is None:
        return "--"
    unit = _preferred_unit(
        settings,
        preferences,
        "distanceUnit",
        "distance_unit",
        {"mi", "km"},
    )
    if unit == "km":
        return f"{(value * 1.609344):.0f} {_t(language, 'unit_kph')}"
    return f"{value:.0f} {_t(language, 'unit_mph')}"


def _preferred_unit(
    settings,
    preferences,
    setting_key,
    preference_key,
    allowed,
    *,
    normalize=str.lower,
):
    settings = settings if isinstance(settings, dict) else {}
    preferences = preferences if isinstance(preferences, dict) else {}
    requested = normalize(str(settings.get(setting_key) or "auto").strip())
    if requested in allowed:
        return requested
    preferred = preferences.get(preference_key)
    if preferred is None:
        return None
    normalized = normalize(str(preferred).strip())
    return normalized if normalized in allowed else None


def _age_text(seconds, language="en"):
    if seconds is None:
        return _t(language, "unknown")
    seconds = max(0, int(seconds))
    if seconds < 60:
        return _t(language, "age_seconds", value=seconds)
    if seconds < 3_600:
        return _t(language, "age_minutes", value=seconds // 60)
    return _t(language, "age_hours", value=seconds // 3_600)


def _right_text(draw, right, top, text, font, fill):
    bounds = draw.textbbox((0, 0), text, font=font)
    draw.text((right - (bounds[2] - bounds[0]), top), text, font=font, fill=fill)


def _center_text(draw, center_x, top, text, font, fill):
    bounds = draw.textbbox((0, 0), text, font=font)
    draw.text((center_x - (bounds[2] - bounds[0]) / 2, top), text, font=font, fill=fill)


# -- The dashboard page --------------------------------------------------------------------
# Three columns under a one-line header: battery and charging with the car; security above
# a top-down tyre map; climate above the location map. A strip of vehicle facts and the
# freshness footer close the page.
PAGE_LEFT, PAGE_RIGHT = 36, 764
HEADER_RULE_Y = 62
COLUMNS = ((36, 318), (338, 516), (538, 764))
COLUMN_TOP, COLUMN_SPLIT_Y, COLUMN_BOTTOM = 72, 212, 392
STRIP_RULE_Y = 398
FOOTER_Y = 441
VEHICLE_TOP_IMAGE_NAME = "vehicle_top.png"


def _draw_dashboard(canvas, draw, summary, theme, settings, colors, language, location_presentation):
    vehicle = summary["vehicle"]
    snapshot = summary["snapshot"]
    extended = summary["schema_version"] in {2, 3}
    battery = summary["battery"]
    if extended:
        charging, preferences = summary["charging"], summary["preferences"]
        tires, software_update = summary["tires"], summary["software_update"]
    else:
        charging = {
            "state": battery["charging_state"],
            "charge_limit_percent": battery["charge_limit_percent"],
            "time_to_full_minutes": battery["time_to_full_minutes"],
            "power_kw": battery["power_kw"],
        }
        preferences, tires, software_update = {}, None, None

    draw.rounded_rectangle((20, 16, 780, 464), radius=18, fill=colors["surface"], outline=colors["rule"], width=2)
    _draw_dashboard_header(canvas, draw, vehicle, snapshot, colors, language)
    for x in (COLUMNS[0][1] + 10, COLUMNS[1][1] + 11):
        draw.line((x, COLUMN_TOP - 4, x, COLUMN_BOTTOM), fill=colors["rule"], width=1)
    _draw_energy_column(canvas, draw, summary, charging, extended, settings, preferences, colors, language)
    _draw_security_column(canvas, draw, vehicle, summary["closures"], extended, settings, preferences, colors, language)
    _draw_tires_column(canvas, draw, tires, settings, preferences, colors, language)
    _draw_climate_column(canvas, draw, summary["climate"], extended, settings, preferences, colors, language)
    _draw_location_column(canvas, draw, location_presentation, colors, language)
    _draw_vehicle_strip(canvas, draw, vehicle, software_update, extended, settings, preferences, colors, language)
    _draw_dashboard_footer(canvas, draw, snapshot, colors, language)


def _draw_dashboard_header(canvas, draw, vehicle, snapshot, colors, language):
    wordmark_right = _draw_vehicle_wordmark(canvas, vehicle["display_name"], colors)
    if not wordmark_right:
        name_font = _font(26, True)
        name = _ellipsize_text(draw, vehicle["display_name"], name_font, 260)
        draw.text((PAGE_LEFT, 22), name, font=name_font, fill=colors["ink"])
        wordmark_right = PAGE_LEFT + draw.textbbox((0, 0), name, font=name_font)[2]
    model_line = " / ".join(
        _enum_text(language, "provider", item) for item in (vehicle["model"], vehicle["trim"]) if item
    ) or _t(language, "vehicle_fallback")
    model_font = _font(15)
    model_left = wordmark_right + 12
    draw.text((model_left, 33), _ellipsize_text(draw, model_line, model_font, 520 - model_left),
              font=model_font, fill=colors["muted"])

    freshness = _enum_text(language, "freshness", snapshot["freshness"])
    freshness_text = f"{freshness} / {_age_text(snapshot['age_seconds'], language)}"
    status_font = _font(14, True)
    freshness_width = draw.textbbox((0, 0), freshness_text, font=status_font)[2]
    _right_text(draw, PAGE_RIGHT, 30, freshness_text, status_font, colors["muted"])
    divider_x = PAGE_RIGHT - freshness_width - 12
    draw.line((divider_x, 30, divider_x, 48), fill=colors["rule"], width=1)
    status_label = _enum_text(language, "connectivity", snapshot["vehicle_connectivity"])
    status_color = _connectivity_color(snapshot["vehicle_connectivity"], colors)
    label_width = draw.textbbox((0, 0), status_label, font=status_font)[2]
    label_left = divider_x - 12 - label_width
    draw.text((label_left, 30), status_label, font=status_font, fill=status_color)
    draw.ellipse((label_left - 15, 35, label_left - 6, 44), fill=status_color)
    draw.line((PAGE_LEFT, HEADER_RULE_Y, PAGE_RIGHT, HEADER_RULE_Y), fill=colors["ink"], width=1)


def _draw_energy_column(canvas, draw, summary, charging, extended, settings, preferences, colors, language):
    left, right = COLUMNS[0]
    battery = summary["battery"]
    _section_label(canvas, draw, left, COLUMN_TOP, _t(language, "energy"), colors, "energy")
    level = battery["level_percent"]
    level_text = "--" if level is None else str(int(round(level)))
    level_font = _font(54 if len(level_text) >= 3 else 62, True)
    draw.text((left - 2, 142), level_text, font=level_font, fill=colors["ink"], anchor="ls")
    level_right = draw.textbbox((left - 2, 142), level_text, font=level_font, anchor="ls")[2]
    draw.text((level_right + 6, 142), "%", font=_font(24, True), fill=colors["muted"], anchor="ls")

    rated = extended and battery["rated_range"] is not None
    hero_range = battery["rated_range"] if rated else battery["estimated_range"]
    range_label = _t(language, "rated_range" if rated else "estimated_range")
    usable = battery["usable_level_percent"] if extended else None
    if usable is not None and level is not None and abs(usable - level) >= 1:
        # Shown only when it differs from the level: the range is figured on the usable part.
        range_label = _t(language, "usable_with_label", value=int(round(usable)), label=range_label)
    _right_text(draw, right, 92, range_label, _font(11, True), colors["muted"])
    limit = charging["charge_limit_percent"]
    _draw_range_figure(draw, right, 131, hero_range, settings, preferences, colors, language)
    _draw_limit_legend(draw, right, 141, hero_range, level, limit, settings, preferences, colors, language)
    _battery_gauge(draw, (left, 156, right, 168), level, limit, colors)
    # The side-on car spans the column between the gauge's scale and the charging rule.
    _paste_vehicle_art(canvas, _load_vehicle_art(charging["state"]), (left + 6, 185, right - 6, 270))

    draw.line((left, 273, right, 273), fill=colors["rule"], width=1)
    heading_right = _section_label(canvas, draw, left, 281, _t(language, "charging"), colors, "charging")
    state = charging["state"] or "unknown"
    draw.text((heading_right + 16, 278), _ellipsize_text(draw, _enum_text(language, "charging", state),
                                                          _font(15, True), 110),
              font=_font(15, True), fill=_charging_state_color(state, colors))
    cable = charging.get("cable_type") if extended else None
    if cable:
        cable_text = (_t(language, "cable_unknown") if str(cable).strip().lower() == "unknown"
                      else _t(language, "cable", value=str(cable).upper()))
        _right_text(draw, right, 283, _ellipsize_text(draw, cable_text, _font(10, True), 104),
                    _font(10, True), colors["muted"])

    limit_text = "--" if limit is None else f"{int(round(limit))}%"
    power_text = _number_with_unit(charging["power_kw"], "unit_kw", None, language)
    if extended:
        rows = _v2_energy_rows(battery, charging, settings, preferences, limit_text, power_text, language)
    else:
        rows = ((_t(language, "limit"), limit_text),
                (_t(language, "time_left"), _minutes_text(charging["time_to_full_minutes"], language)),
                (_t(language, "power"), power_text))
    for index, (label, value) in enumerate(rows):
        _dashboard_row(draw, left, right, 301 + index * 13, label, value, colors,
                       label_size=10, value_size=10, label_width=82)


def _draw_range_figure(draw, right, baseline, measurement, settings, preferences, colors, language):
    """ "214" large and "英里" small after it, ending at ``right`` on ``baseline``."""
    number, _space, unit = _measurement_text(measurement, settings, preferences, language).rpartition(" ")
    if not number:
        number, unit = unit, ""
    unit_left = right
    if unit:
        unit_font = _font(14, True)
        unit_left = right - draw.textbbox((0, 0), unit, font=unit_font)[2]
        draw.text((unit_left, baseline), unit, font=unit_font, fill=colors["muted"], anchor="ls")
        unit_left -= 4
    draw.text((unit_left, baseline), number, font=_font(30, True), fill=colors["ink"], anchor="rs")


def _draw_limit_legend(draw, right, middle, measurement, level, limit, settings, preferences, colors, language):
    """ "▼ 上限 80% · 约 241 英里": the gauge's marker, explained, with the range a charge to it gives."""
    if limit is None:
        return
    limit_text = f"{int(round(limit))}%"
    value = (measurement or {}).get("value")
    if value is not None and level is not None and level >= 5:
        projected = {"value": value * limit / level, "unit": measurement["unit"]}
        legend = _t(language, "limit_range_projection", limit=limit_text,
                    range=_measurement_text(projected, settings, preferences, language))
    else:
        legend = _t(language, "limit_range", limit=limit_text)
    font = _font(10, True)
    width = draw.textbbox((0, 0), legend, font=font)[2]
    draw.text((right, middle), legend, font=font, fill=colors["ink"], anchor="rm")
    x = right - width - 6
    draw.polygon(((x - 4, middle - 3), (x + 4, middle - 3), (x, middle + 3)), fill=colors["ink"])


def _battery_gauge(draw, box, level, limit, colors):
    """The battery bar on a 0-100 ruler (a tick per 10%, numbers per 20%), the charge limit marked.

    The fill takes a battery's usual colours: green above half, amber down to 20%, red below.
    """
    left, top, right, bottom = box
    span = right - left
    radius = (bottom - top) // 2
    draw.rounded_rectangle(box, radius=radius, fill=colors["track"])
    if level is not None:
        filled = round(span * max(0, min(100, level)) / 100)
        fill = (colors["battery_high"] if level > 50 else colors["battery_mid"] if level > 20
                else colors["battery_low"])
        if filled > 0:
            draw.rounded_rectangle((left, top, left + max(filled, 2 * radius), bottom), radius=radius, fill=fill)
    font = _font(9, True)
    for step in range(11):
        x = round(left + span * step / 10)
        if 0 < step < 10:
            draw.line((x, top + 2, x, bottom - 2), fill=colors["surface"], width=1)
        draw.line((x, bottom + 2, x, bottom + (6 if step % 2 == 0 else 4)), fill=colors["muted"], width=1)
        if step % 2 == 0:
            label = str(step * 10)
            width = draw.textbbox((0, 0), label, font=font)[2]
            label_x = left if step == 0 else right - width if step == 10 else x - width / 2
            draw.text((label_x, bottom + 7), label, font=font, fill=colors["muted"])
    if limit is not None:
        x = round(left + span * max(0, min(100, limit)) / 100)
        draw.line((x, top - 3, x, bottom + 3), fill=colors["ink"], width=2)
        draw.polygon(((x - 4, top - 8), (x + 4, top - 8), (x, top - 3)), fill=colors["ink"])


def _draw_security_column(canvas, draw, vehicle, closures, extended, settings, preferences, colors, language):
    left, right = COLUMNS[1]
    _section_label(canvas, draw, left, COLUMN_TOP, _t(language, "security"), colors, "security")
    lock_text = _bool_status(vehicle["locked"], _t(language, "locked"), _t(language, "unlocked"),
                             _t(language, "unknown"))
    lock_color = (colors["accent"] if vehicle["locked"] is False
                  else colors["warning"] if vehicle["locked"] is None else colors["ink"])
    _draw_padlock(draw, (left, 94, left + 22, 120), vehicle["locked"], lock_color, colors["surface"])
    draw.text((left + 30, 92), lock_text, font=_font(25, True), fill=lock_color)
    confirmed_open = bool(closures["open"]) or closures["charge_port_open"] is True
    closure_color = (colors["accent"] if closures["all_closed"] is False or confirmed_open
                     else colors["warning"] if closures["all_closed"] is None else colors["ink"])
    detail_font = _font(13, True)
    draw.text((left, 124), _closure_detail_line(draw, _closure_detail_lines(closures, language), detail_font,
                                                right - left, language),
              font=detail_font, fill=closure_color)

    if extended:
        sentry = _enum_text(language, "state", vehicle["sentry_mode"])
        flags = _security_flag_text(vehicle, settings, preferences, language)
        special = _t(language, "off_state") if flags == _t(language, "modes_off") else flags
    else:
        sentry = special = _t(language, "not_reported")
    port = _bool_status(closures["charge_port_open"], _t(language, "open_state"), _t(language, "closed_state"),
                        _t(language, "unknown"))
    rows = ((_t(language, "sentry_mode"), sentry, colors["ink"]),
            (_t(language, "charge_port"), port,
             colors["accent"] if closures["charge_port_open"] is True else colors["ink"]),
            (_t(language, "special_modes"), special, colors["ink"]))
    _ruled_rows(draw, left, right, 148, rows, colors)


def _draw_padlock(draw, box, locked, color, knockout):
    """A padlock beside the lock state: shackle down when locked, lifted open when not, a ? when unknown."""
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    body_top = top + round(height * 0.44)
    stroke = max(2, round(width * 0.16))
    shackle_left, shackle_right = left + round(width * 0.2), right - round(width * 0.2)
    lift = 0 if locked is True else round(height * 0.16)
    arc_box = (shackle_left, top - lift, shackle_right, body_top + (shackle_right - shackle_left) // 2 - lift)
    draw.arc(arc_box, 180, 360, fill=color, width=stroke)
    arc_middle = (arc_box[1] + arc_box[3]) // 2
    # The right leg always reaches the body; the left one only when the shackle is shut.
    draw.line((shackle_right - stroke // 2, arc_middle, shackle_right - stroke // 2, body_top), fill=color, width=stroke)
    if locked is True:
        draw.line((shackle_left + stroke // 2, arc_middle, shackle_left + stroke // 2, body_top), fill=color,
                  width=stroke)
    draw.rounded_rectangle((left, body_top, right, bottom), radius=max(2, width // 6), fill=color)
    center_x = (left + right) // 2
    if locked is None:
        font = _font(max(9, height // 2), True)
        bounds = draw.textbbox((0, 0), "?", font=font)
        draw.text((center_x - (bounds[2] - bounds[0]) / 2 - bounds[0], body_top + 1 - bounds[1] + 1), "?",
                  font=font, fill=knockout)
        return
    hole = max(2, width // 7)
    hole_y = body_top + (bottom - body_top) // 2 - 1
    draw.ellipse((center_x - hole, hole_y - hole, center_x + hole, hole_y + hole), fill=knockout)
    draw.rectangle((center_x - hole // 2, hole_y, center_x + hole // 2, hole_y + hole * 2), fill=knockout)


def _ruled_rows(draw, left, right, top, rows, colors, step=21):
    for index, (label, value, value_color) in enumerate(rows):
        y = top + index * step
        if index:
            draw.line((left, y - 5, right, y - 5), fill=colors["rule"], width=1)
        _dashboard_row(draw, left, right, y, label, value, colors, label_size=11, value_size=11,
                       label_width=74, value_color=value_color)


def _draw_tires_column(canvas, draw, tires, settings, preferences, colors, language):
    left, right = COLUMNS[1]
    draw.line((left, COLUMN_SPLIT_Y, right, COLUMN_SPLIT_Y), fill=colors["rule"], width=1)
    _section_label(canvas, draw, left, COLUMN_SPLIT_Y + 8, _t(language, "tires"), colors, "tires")
    center = (left + right) // 2
    if tires is None:
        _center_text(draw, center, 300, _t(language, "not_reported_v1"), _font(11, True), colors["muted"])
        return
    unit = _preferred_unit(settings, preferences, "pressureUnit", "pressure_unit", {"psi", "bar"}) or "bar"
    _right_text(draw, right, COLUMN_SPLIT_Y + 10, _t(language, "unit_psi" if unit == "psi" else "unit_bar"),
                _font(10, True), colors["muted"])
    car_box = _paste_vehicle_art(canvas, _load_vehicle_top_art(), (center - 32, 238, center + 32, 358))
    car_left, car_top, car_right, car_bottom = car_box or (center - 22, 238, center + 22, 358)
    for position, row_top, wheel_y in (("front_left", 240, car_top + (car_bottom - car_top) * 0.2),
                                       ("front_right", 240, car_top + (car_bottom - car_top) * 0.2),
                                       ("rear_left", 298, car_top + (car_bottom - car_top) * 0.74),
                                       ("rear_right", 298, car_top + (car_bottom - car_top) * 0.74)):
        warning = _tire_warning_level(tires, position)
        color = (colors["accent"] if warning == "hard"
                 else colors["warning"] if warning in {"soft", "unknown"} else colors["ink"])
        pressure = _pressure_text(tires["pressures"][position], settings, preferences, language).split(" ", 1)[0]
        label = _tire_abbreviation(position, language)
        value_font = _font(22, True)
        if position.endswith("left"):
            draw.text((left + 2, row_top), label, font=_font(10, True), fill=colors["muted"])
            draw.text((left + 2, row_top + 13), pressure, font=value_font, fill=color)
            text_right = left + 4 + draw.textbbox((0, 0), pressure, font=value_font)[2]
            draw.line((text_right + 6, wheel_y, car_left - 4, wheel_y), fill=colors["rule"], width=1)
            draw.line((car_left - 4, wheel_y - 8, car_left - 4, wheel_y + 8), fill=colors["muted"], width=2)
        else:
            _right_text(draw, right - 2, row_top, label, _font(10, True), colors["muted"])
            _right_text(draw, right - 2, row_top + 13, pressure, value_font, color)
            text_left = right - 4 - draw.textbbox((0, 0), pressure, font=value_font)[2]
            draw.line((car_right + 4, wheel_y, text_left - 6, wheel_y), fill=colors["rule"], width=1)
            draw.line((car_right + 4, wheel_y - 8, car_right + 4, wheel_y + 8), fill=colors["muted"], width=2)
    status, status_color = _tire_status(tires, colors, language)
    _center_text(draw, center, 366, _ellipsize_text(draw, status, _font(12, True), right - left), _font(12, True),
                 status_color)


def _draw_climate_column(canvas, draw, climate, extended, settings, preferences, colors, language):
    left, right = COLUMNS[2]
    _section_label(canvas, draw, left, COLUMN_TOP, _t(language, "climate"), colors, "climate")
    status = _bool_status(climate["is_climate_on"], _t(language, "hvac_on"), _t(language, "hvac_off"),
                          _t(language, "unknown"))
    draw.text((left, 92), status, font=_font(25, True), fill=colors["ink"])
    if extended:
        keeper = _enum_text(language, "keeper", climate["keeper_mode"])
        _right_text(draw, right, 100, _ellipsize_text(draw, keeper, _font(10, True), 80), _font(10, True),
                    colors["muted"])
    temperature_line = _t(language, "inside_outside",
                          inside=_temperature_text(climate["inside_temp_c"], settings, preferences, language),
                          outside=_temperature_text(climate["outside_temp_c"], settings, preferences, language))
    draw.text((left, 124), _ellipsize_text(draw, temperature_line, _font(14, True), right - left),
              font=_font(14, True), fill=colors["ink"])

    middle = (left + right) // 2
    for index, cells in enumerate(_climate_cells(climate, extended, settings, preferences, language)):
        y = 148 + index * 21
        if index:
            draw.line((left, y - 5, right, y - 5), fill=colors["rule"], width=1)
        for (label, value), (cell_left, cell_right) in zip(cells, ((left, middle - 8), (middle + 8, right))):
            _dashboard_row(draw, cell_left, cell_right, y, label, value, colors, label_size=10, value_size=11,
                           label_width=50)
    draw.line((middle, 144, middle, 200), fill=colors["rule"], width=1)


def _climate_cells(climate, extended, settings, preferences, language):
    if not extended:
        unknown = _t(language, "not_reported")
        return (((_t(language, "driver_target"), unknown), (_t(language, "passenger_target"), unknown)),)
    fan = "--" if climate["fan_status"] is None else f"{climate['fan_status']:g}"
    overheat = climate["cabin_overheat"]
    overheat_text = "--"
    if overheat["mode"]:
        overheat_text = _enum_text(language, "state", overheat["mode"])
        if overheat["temp_limit"]:
            overheat_text += f"/{_enum_text(language, 'state', overheat['temp_limit'])}"
    return (
        ((_t(language, "driver_target"),
          _temperature_text(climate["driver_target_temp_c"], settings, preferences, language)),
         (_t(language, "passenger_target"),
          _temperature_text(climate["passenger_target_temp_c"], settings, preferences, language))),
        ((_t(language, "auto_hvac"), _enum_text(language, "state", climate["hvac_auto_mode"])),
         (_t(language, "fan"), fan)),
        ((_t(language, "defrost"), _enum_text(language, "state", climate["defrost_mode"])),
         (_t(language, "overheat_protect"), overheat_text)),
    )


def _draw_location_column(canvas, draw, location_presentation, colors, language):
    left, right = COLUMNS[2]
    draw.line((left, COLUMN_SPLIT_Y, right, COLUMN_SPLIT_Y), fill=colors["rule"], width=1)
    _section_label(canvas, draw, left, COLUMN_SPLIT_Y + 8, _t(language, "location"), colors, "location")
    map_left, map_top, map_right, map_bottom = LOCATION_MAP_BOX
    location_map = (location_presentation or {}).get("map_image")
    if not (isinstance(location_map, Image.Image) and location_map.size == LOCATION_MAP_SIZE):
        draw.rounded_rectangle(LOCATION_MAP_BOX, radius=6, outline=colors["rule"], width=1)
        _center_text(draw, (map_left + map_right) // 2, (map_top + map_bottom) // 2 - 7,
                     _t(language, "location_unavailable"), _font(11, True), colors["muted"])
        return
    _paste_faded_map(canvas, location_map.convert("RGB"), (map_left, map_top), colors["surface"])
    # A rule parts the map (and Google's attribution) from the address under it.
    draw.line((left, map_bottom + 4, right, map_bottom + 4), fill=colors["rule"], width=1)
    address_font = _font(13, True)
    lines = _address_lines(draw, location_presentation["formatted_address"], address_font, right - left)
    age = max(0, location_presentation.get("age_seconds", 0))
    stale = age > 900
    address_top = map_bottom + 9
    for index, line in enumerate(lines):
        # The street leads in ink; the city and postcode follow smaller and muted.
        font, fill = (address_font, colors["ink"]) if index == 0 else (_font(11, True), colors["muted"])
        draw.text((left, address_top + index * 16), line.rstrip(",，") if len(lines) > 1 else line, font=font,
                  fill=fill)
    if stale:
        draw.text((left, address_top + len(lines) * 16 - 1),
                  _ellipsize_text(draw, _t(language, "location_last_known", age=_age_text(age, language)),
                                  _font(9, True), right - left),
                  font=_font(9, True), fill=colors["muted"])


def _faded_map_mask(size):
    """Opaque in the middle and along the attribution strip, fading out at the top and sides."""
    width, height = size
    mask = Image.new("L", size, 255)
    pixels = mask.load()
    solid_from = height - LOCATION_MAP_ATTRIBUTION_H
    for y in range(height):
        top = min(1.0, y / LOCATION_MAP_FADE_TOP)
        # The side fade eases off over the 10 px above the attribution strip.
        side_weight = max(0.0, min(1.0, (solid_from - y) / 10))
        for x in range(width):
            edge = min(x, width - 1 - x) / LOCATION_MAP_FADE_SIDE
            side = 1.0 - side_weight * (1.0 - min(1.0, edge))
            pixels[x, y] = round(255 * min(top, side) ** 1.6)
    return mask


def _paste_faded_map(canvas, location_map, origin, surface):
    """Lay the map on the panel through its fade mask, the panel colour showing at its edges."""
    panel = Image.new("RGB", location_map.size, surface)
    canvas.paste(Image.composite(location_map, panel, _faded_map_mask(location_map.size)), origin)


def _address_lines(draw, address, font, width):
    """The address on two lines, broken after the street when it has one."""
    text = str(address or "").strip()
    if draw.textbbox((0, 0), text, font=font)[2] <= width:
        return [text]
    street, comma, rest = text.partition(",")
    if comma and draw.textbbox((0, 0), street + ",", font=font)[2] <= width:
        return [street + ",", _ellipsize_text(draw, rest.strip(), font, width)]
    return [_ellipsize_text(draw, text, font, width)]


def _draw_vehicle_strip(canvas, draw, vehicle, software_update, extended, settings, preferences, colors, language):
    draw.line((PAGE_LEFT, STRIP_RULE_Y, PAGE_RIGHT, STRIP_RULE_Y), fill=colors["rule"], width=1)
    heading_right = _section_label(canvas, draw, PAGE_LEFT, STRIP_RULE_Y + 11, _t(language, "vehicle"), colors,
                                   "vehicle")
    not_reported = _t(language, "not_reported")
    display_state = vehicle["center_display_state"] if extended else None
    update = software_update or {}
    cells = [
        (_t(language, "odometer"), _measurement_text(vehicle["odometer"], settings, preferences, language)),
        (_t(language, "software"), vehicle["software_version"] or not_reported),
        (_t(language, "screen"),
         _enum_text(language, "state", display_state) if display_state is not None else not_reported),
        (_t(language, "update"), update.get("version") or not_reported),
    ]
    if extended:
        cells += [(_t(language, "body_wheel"), _vehicle_appearance_text(vehicle, language)),
                  (_t(language, "config"), _vehicle_config_text(vehicle, language))]
    widths = (74, 96, 54, 140, 134, 0)
    x = max(heading_right + 18, 112)
    for index, (label, value) in enumerate(cells):
        cell_right = PAGE_RIGHT if index == len(cells) - 1 else x + widths[index]
        draw.text((x, STRIP_RULE_Y + 5), label, font=_font(9, True), fill=colors["muted"])
        draw.text((x, STRIP_RULE_Y + 17), _ellipsize_text(draw, str(value), _font(11, True), cell_right - x - 6),
                  font=_font(11, True), fill=colors["ink"])
        if label == _t(language, "update") and any(
            update.get(key) is not None for key in ("download_percent", "install_percent", "expected_duration_minutes")
        ):
            progress = _t(language, "progress_line", download=_percent_text(update["download_percent"]),
                          install=_percent_text(update["install_percent"]),
                          duration=_minutes_text(update["expected_duration_minutes"], language))
            draw.text((x, STRIP_RULE_Y + 31), _ellipsize_text(draw, progress, _font(9, True), cell_right - x - 6),
                      font=_font(9, True), fill=colors["muted"])
        x = cell_right + 6


def _draw_dashboard_footer(canvas, draw, snapshot, colors, language):
    left = PAGE_LEFT + 22 if _draw_dashboard_icon(canvas, "freshness", PAGE_LEFT, FOOTER_Y - 2, colors, size=16) \
        else PAGE_LEFT
    freshness = _enum_text(language, "freshness", snapshot["freshness"])
    draw.text((left, FOOTER_Y), _t(language, "updated", age=_age_text(snapshot["age_seconds"], language),
                                   freshness=freshness),
              font=_font(12, True), fill=colors["muted"])
    _right_text(draw, PAGE_RIGHT, FOOTER_Y, _t(language, "read_only_footer"), _font(12, True), colors["accent"])


def _paste_vehicle_art(canvas, art, box):
    """The car art fitted into ``box`` (centred) as it is, by day and night; its pasted bounds."""
    if art is None:
        return None
    bounds = art.getchannel("A").getbbox()
    if bounds is not None:
        art = art.crop(bounds)
    left, top, right, bottom = box
    art = art.copy()
    art.thumbnail((right - left, bottom - top), Image.Resampling.LANCZOS)
    x = left + (right - left - art.width) // 2
    y = top + (bottom - top - art.height) // 2
    canvas.paste(art, (x, y), art)
    return x, y, x + art.width, y + art.height


def _load_vehicle_top_art():
    try:
        image = safe_open_image(
            Path(__file__).with_name(VEHICLE_TOP_IMAGE_NAME),
            limits=ImageLimits(max_bytes=512 * 1024, max_width=512, max_height=1024, max_pixels=400_000,
                               allowed_formats=frozenset({"PNG"})),
        )
        return image.convert("RGBA")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Vehicle top view unavailable type=%s", type(exc).__name__)
        return None
