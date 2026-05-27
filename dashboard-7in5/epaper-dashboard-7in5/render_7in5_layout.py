import calendar
import math
import os
from datetime import datetime
from typing import Any, Dict

from PIL import Image, ImageDraw, ImageFont


WIDTH = 800
HEIGHT = 480
MARGIN = 12
COL1_X = 12
COL2_X = 274
COL3_X = 538
COL_W = 250


def load_7in5_fonts(font_dir: str) -> Dict[str, Any]:
    def load_font(name, size):
        return ImageFont.truetype(os.path.join(font_dir, name), size)

    return {
        "14": load_font("Aldrich-Regular.ttc", 14),
        "16": load_font("Aldrich-Regular.ttc", 16),
        "18": load_font("Aldrich-Regular.ttc", 18),
        "20": load_font("Aldrich-Regular.ttc", 20),
        "22": load_font("Aldrich-Regular.ttc", 22),
        "24": load_font("Aldrich-Regular.ttc", 24),
        "28": load_font("Aldrich-Regular.ttc", 28),
        "32": load_font("Aldrich-Regular.ttc", 32),
        "48": load_font("Aldrich-Regular.ttc", 48),
        "64": load_font("Aldrich-Regular.ttc", 64),
        "clock": load_font("advanced_led_board-7.ttc", 88),
    }


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except AttributeError:
        return draw.textsize(text, font=font)[0]


def _fit_text(text: str, draw: ImageDraw.ImageDraw, font, max_width: int) -> str:
    if _text_width(draw, text, font) <= max_width:
        return text
    if max_width <= 0:
        return ""
    suffix = "..."
    trimmed = text
    while trimmed and _text_width(draw, trimmed + suffix, font) > max_width:
        trimmed = trimmed[:-1]
    return trimmed + suffix if trimmed else ""


def _draw_icon(dashboard, draw, x, y, name, size=(40, 40), is_white=False):
    icon = dashboard.get_cached_icon(name, size, is_white)
    if icon:
        draw.bitmap((x, y), icon, fill=255 if is_white else 0)
    else:
        draw.rectangle((x, y, x + size[0], y + size[1]), outline=255 if is_white else 0)


def _draw_sparkline(dashboard, draw, x, y, data, width, height, max_items=50):
    dashboard.draw_sparkline(draw, x, y, list(data), max_items=max_items, width=width, height=height, style="bar")


def _section_line(draw, x1, y, x2):
    draw.line((x1, y, x2, y), fill=0, width=2)


def _draw_weather_compass(draw, fonts, cx, cy, radius, wind_dir, wind_spd):
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=0, width=2)

    for angle in range(0, 360, 45):
        rad_tick = math.radians(angle)
        inner_r = radius - 8 if angle % 90 == 0 else radius - 4
        tx1 = cx + inner_r * math.cos(rad_tick)
        ty1 = cy + inner_r * math.sin(rad_tick)
        tx2 = cx + radius * math.cos(rad_tick)
        ty2 = cy + radius * math.sin(rad_tick)
        draw.line((tx1, ty1, tx2, ty2), fill=0, width=2)

    draw.text((cx - 7, cy - radius - 20), "N", font=fonts["16"], fill=0)
    draw.text((cx - 7, cy + radius + 3), "S", font=fonts["16"], fill=0)
    draw.text((cx + radius + 5, cy - 8), "E", font=fonts["16"], fill=0)
    draw.text((cx - radius - 20, cy - 8), "W", font=fonts["16"], fill=0)

    rad_arrow = math.radians(wind_dir - 90)
    tip_x = cx + (radius - 12) * math.cos(rad_arrow)
    tip_y = cy + (radius - 12) * math.sin(rad_arrow)
    base_angle = math.radians(150)
    left_x = cx + 18 * math.cos(rad_arrow + base_angle)
    left_y = cy + 18 * math.sin(rad_arrow + base_angle)
    right_x = cx + 18 * math.cos(rad_arrow - base_angle)
    right_y = cy + 18 * math.sin(rad_arrow - base_angle)
    draw.polygon([(tip_x, tip_y), (left_x, left_y), (right_x, right_y)], fill=0)
    draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=0)

    speed = _fit_text(f"{wind_spd} km/h", draw, fonts["16"], radius * 2 - 12)
    draw.text((cx - _text_width(draw, speed, fonts["16"]) / 2, cy + 24), speed, font=fonts["16"], fill=0)


def _draw_progress(draw, fonts, x, y, label, pct):
    draw.text((x, y), label, font=fonts["20"], fill=0)
    bx = x + 82
    bw = 112
    bh = 18
    draw.rectangle((bx, y + 2, bx + bw, y + bh + 2), outline=0, width=2)
    fill_w = int((bw - 4) * min(max(pct, 0.0), 1.0))
    if fill_w > 0:
        draw.rectangle((bx + 2, y + 4, bx + 2 + fill_w, y + bh), fill=0)
    draw.text((bx + bw + 10, y), f"{int(pct * 100)}%", font=fonts["20"], fill=0)


def render_screen_7in5(dashboard, fonts) -> Image.Image:
    image = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)

    if not dashboard.data_store.lock.acquire(timeout=2.0):
        return image
    try:
        weather = dashboard.data_store.weather.copy()
        aqi = dashboard.data_store.aqi
        sysload = dashboard.data_store.sysload.copy()
        crypto = dashboard.data_store.crypto.copy()
        ping = dashboard.data_store.ping.copy()
        gmail_unread = dashboard.data_store.gmail_unread
    finally:
        dashboard.data_store.lock.release()

    draw.line((COL2_X - 12, 10, COL2_X - 12, HEIGHT - 10), fill=0, width=2)
    draw.line((COL3_X - 12, 10, COL3_X - 12, HEIGHT - 10), fill=0, width=2)

    # Column 1: system, crypto, network.
    _draw_icon(dashboard, draw, COL1_X, 16, "icon_cpu", (42, 42))
    draw.text((COL1_X + 52, 14), f"SYSTEM LOAD: {sysload.get('cpu', 0)}%", font=fonts["24"], fill=0)
    draw.text((COL1_X + 52, 46), f"RAM Free: {sysload.get('ram_free', 0)} MB", font=fonts["18"], fill=0)
    _draw_sparkline(dashboard, draw, COL1_X + 52, 76, sysload.get("history", []), 170, 34, max_items=30)

    _section_line(draw, COL1_X, 128, COL1_X + COL_W - 10)

    _draw_icon(dashboard, draw, COL1_X, 150, "icon_btc", (42, 42))
    draw.text((COL1_X + 52, 151), f"BTC: ${crypto.get('btc', 0)}", font=fonts["24"], fill=0)
    _draw_sparkline(dashboard, draw, COL1_X + 52, 188, crypto.get("btc_hist", []), 170, 30, max_items=50)

    _draw_icon(dashboard, draw, COL1_X, 232, "icon_eth", (42, 42))
    draw.text((COL1_X + 52, 233), f"ETH: ${crypto.get('eth', 0)}", font=fonts["24"], fill=0)
    _draw_sparkline(dashboard, draw, COL1_X + 52, 270, crypto.get("eth_hist", []), 170, 30, max_items=50)

    _section_line(draw, COL1_X, 320, COL1_X + COL_W - 10)

    _draw_icon(dashboard, draw, COL1_X, 340, "icon_wifi", (42, 42))
    draw.text((COL1_X + 52, 336), "Internet Quality", font=fonts["22"], fill=0)
    draw.text((COL1_X + 52, 366), f"{ping.get('current', 0)} ms", font=fonts["24"], fill=0)
    _draw_sparkline(dashboard, draw, COL1_X + 20, 405, ping.get("history", []), 205, 38, max_items=50)

    # Column 2: weather and air quality.
    if "current" in weather:
        cur = weather["current"]
        temp = math.floor(cur.get("temperature_2m", 0) + 0.5)
        hum = cur.get("relative_humidity_2m", 0)
        pres = cur.get("surface_pressure", 0)
        w_code = cur.get("weather_code", 0)
        is_day = cur.get("is_day", 1)
        wind_dir = cur.get("wind_direction_10m", 0)
        wind_spd = cur.get("wind_speed_10m", 0)
        uv = math.floor(cur.get("uv_index", 0.0) + 0.5)

        _draw_icon(dashboard, draw, COL2_X, 18, dashboard.get_weather_icon(w_code, is_day), (72, 72))
        draw.text((COL2_X + 82, 0), f"{temp}C", font=fonts["64"], fill=0)
        draw.text((COL2_X + 184, 18), "UV", font=fonts["20"], fill=0)
        draw.text((COL2_X + 218, 10), str(uv), font=fonts["32"], fill=0)
        draw.text((COL2_X + 82, 80), f"Humidity: {hum}%", font=fonts["18"], fill=0)
        draw.text((COL2_X + 82, 106), f"Press: {pres} hPa", font=fonts["18"], fill=0)

        _section_line(draw, COL2_X, 136, COL2_X + COL_W)

        _draw_icon(dashboard, draw, COL2_X, 154, "icon_wind", (28, 28))
        _draw_weather_compass(draw, fonts, COL2_X + 74, 238, 54, wind_dir, wind_spd)

        draw.text((COL2_X + 146, 164), "AIR QUALITY", font=fonts["18"], fill=0)
        draw.text((COL2_X + 146, 204), "AQI:", font=fonts["24"], fill=0)
        aqi_text = str(aqi)
        aqi_x = COL2_X + 184
        if aqi >= 50:
            tw = _text_width(draw, aqi_text, fonts["48"])
            draw.rectangle((aqi_x - 6, 218, aqi_x + tw + 8, 270), fill=0)
            draw.text((aqi_x, 210), aqi_text, font=fonts["48"], fill=255)
        else:
            draw.text((aqi_x, 210), aqi_text, font=fonts["48"], fill=0)

        _section_line(draw, COL2_X, 320, COL2_X + COL_W)

        hourly = weather.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        codes = hourly.get("weather_code", [])
        cur_iso = datetime.now().strftime("%Y-%m-%dT%H:00")
        try:
            start_idx = times.index(cur_iso) + 1
        except ValueError:
            start_idx = 0

        for i in range(4):
            idx = start_idx + i
            if idx < len(times):
                off_x = COL2_X + i * 62
                draw.text((off_x + 2, 338), times[idx].split("T")[1][:5], font=fonts["18"], fill=0)
                _draw_icon(dashboard, draw, off_x + 6, 372, dashboard.get_weather_icon(codes[idx], 1), (46, 46))
                f_temp = math.floor(temps[idx] + 0.5)
                draw.text((off_x + 8, 426), f"{f_temp}C", font=fonts["18"], fill=0)

    # Column 3: clock, progress, mail.
    dt = datetime.now()
    draw.text((COL3_X, 4), dt.strftime("%H:%M"), font=fonts["clock"], fill=0)
    draw.text((COL3_X, 132), dt.strftime("%d %b %Y"), font=fonts["24"], fill=0)
    draw.text((COL3_X + 178, 132), dt.strftime("%a").upper(), font=fonts["24"], fill=0)

    _section_line(draw, COL3_X, 190, WIDTH - MARGIN)

    draw.text((COL3_X, 210), "TIME PROGRESS", font=fonts["24"], fill=0)
    day_pct = (dt.hour * 3600 + dt.minute * 60 + dt.second) / 86400.0
    days_in_m = calendar.monthrange(dt.year, dt.month)[1]
    month_pct = (dt.day - 1 + (dt.hour / 24.0)) / days_in_m
    days_in_y = 366 if calendar.isleap(dt.year) else 365
    year_pct = (dt.timetuple().tm_yday - 1 + (dt.hour / 24.0)) / days_in_y
    _draw_progress(draw, fonts, COL3_X, 252, "DAY", day_pct)
    _draw_progress(draw, fonts, COL3_X, 290, "MONTH", month_pct)
    _draw_progress(draw, fonts, COL3_X, 328, "YEAR", year_pct)

    _section_line(draw, COL3_X, 386, WIDTH - MARGIN)

    _draw_icon(dashboard, draw, COL3_X, 410, "icon_mail", (50, 50))
    mail_text = _fit_text(f"Inbox: {gmail_unread}", draw, fonts["28"], WIDTH - (COL3_X + 62) - MARGIN)
    draw.text((COL3_X + 62, 418), mail_text, font=fonts["28"], fill=0)

    return image
