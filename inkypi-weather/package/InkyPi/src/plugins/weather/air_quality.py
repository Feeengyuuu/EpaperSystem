"""The US EPA Air Quality Index, for both weather providers.

Open-Meteo reports the US AQI directly. OpenWeatherMap only has its own 1-5 index, so the
page computes the US AQI from OpenWeatherMap's pollutant concentrations with the EPA
breakpoints (PM2.5 as revised in 2024). OpenWeatherMap's readings are current values
rather than the 24-hour / 8-hour averages the EPA index is defined on, so its figure is an
estimate of the same scale.
"""

from __future__ import annotations

import math

# (upper AQI of the category, English name). 301 and up is Hazardous.
CATEGORIES = (
    (50, "Good"),
    (100, "Moderate"),
    (150, "Unhealthy for Sensitive Groups"),
    (200, "Unhealthy"),
    (300, "Very Unhealthy"),
    (math.inf, "Hazardous"),
)
SHORT_NAMES = {"Unhealthy for Sensitive Groups": "USG"}
_AQI_STEPS = ((0, 50), (51, 100), (101, 150), (151, 200), (201, 300), (301, 500))
# Concentration breakpoints per category, in the units named.
BREAKPOINTS = {
    "pm2_5": ((0.0, 9.0), (9.1, 35.4), (35.5, 55.4), (55.5, 125.4), (125.5, 225.4), (225.5, 325.4)),  # ug/m3, 24 h
    "pm10": ((0, 54), (55, 154), (155, 254), (255, 354), (355, 424), (425, 604)),  # ug/m3, 24 h
    "o3": ((0, 54), (55, 70), (71, 85), (86, 105), (106, 200), None),  # ppb, 8 h
    "no2": ((0, 53), (54, 100), (101, 360), (361, 649), (650, 1249), (1250, 2049)),  # ppb, 1 h
    "so2": ((0, 35), (36, 75), (76, 185), (186, 304), (305, 604), (605, 1004)),  # ppb, 1 h
    "co": ((0.0, 4.4), (4.5, 9.4), (9.5, 12.4), (12.5, 15.4), (15.5, 30.4), (30.5, 50.4)),  # ppm, 8 h
}
# EPA truncation of each pollutant's concentration before the lookup.
_DECIMALS = {"pm2_5": 1, "pm10": 0, "o3": 0, "no2": 0, "so2": 0, "co": 1}
# ug/m3 -> ppb at 25 degC and 1 atm (24.45 / molar mass); CO goes on to ppm.
_UGM3_PER_PPB = {"o3": 48.00 / 24.45, "no2": 46.01 / 24.45, "so2": 64.07 / 24.45, "co": 28.01 / 24.45}
POLLUTANT_NAMES = {"pm2_5": "PM2.5", "pm10": "PM10", "o3": "O3", "no2": "NO2", "so2": "SO2", "co": "CO"}


def category(aqi):
    """(level 0-5, English name) of a US AQI value, or (None, "") when unknown."""
    try:
        value = float(aqi)
    except (TypeError, ValueError):
        return None, ""
    if not math.isfinite(value) or value < 0:
        return None, ""
    # Category edges are whole numbers (50 Good, 51 Moderate); a fraction rounds first.
    value = math.floor(value + 0.5)
    return next((level, name) for level, (upper, name) in enumerate(CATEGORIES) if value <= upper)


def sub_index(pollutant, concentration):
    """The AQI for one pollutant's concentration (EPA linear interpolation), or None."""
    table = BREAKPOINTS.get(pollutant)
    try:
        value = float(concentration)
    except (TypeError, ValueError):
        return None
    if table is None or not math.isfinite(value) or value < 0:
        return None
    factor = 10 ** _DECIMALS[pollutant]
    value = math.floor(value * factor) / factor
    top = 0
    for (low_aqi, high_aqi), band in zip(_AQI_STEPS, table):
        if band is None:
            break
        low, high = band
        if value <= high:
            return round((high_aqi - low_aqi) / (high - low) * (max(value, low) - low) + low_aqi)
        top = high_aqi
    return top  # beyond the table: the top of its last category


def from_components(components):
    """US AQI and per-pollutant sub-indices from OpenWeatherMap ``components`` (ug/m3).

    Returns {"aqi": int, "pollutants": {"pm2_5": 53, ...}, "dominant": "pm2_5"} or {}.
    """
    if not isinstance(components, dict):
        return {}
    indices = {}
    for pollutant in ("pm2_5", "pm10", "o3", "no2", "so2", "co"):
        raw = components.get(pollutant)
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            continue
        if pollutant in _UGM3_PER_PPB:
            amount /= _UGM3_PER_PPB[pollutant]
            if pollutant == "co":
                amount /= 1000.0
        index = sub_index(pollutant, amount)
        if index is not None:
            indices[pollutant] = index
    if not indices:
        return {}
    dominant = max(indices, key=indices.get)
    return {"aqi": indices[dominant], "pollutants": indices, "dominant": dominant}


def data_point(aqi, pollutants=None, icon=""):
    """The page's "Air Quality" reading on the US scale; "N/A" with no level when unknown."""
    level, name = category(aqi)
    pollutants = {key: int(value) for key, value in (pollutants or {}).items()
                  if isinstance(value, (int, float)) and math.isfinite(value)}
    dominant = max(pollutants, key=pollutants.get) if pollutants else ""
    return {
        "label": "Air Quality",
        "measurement": int(math.floor(float(aqi) + 0.5)) if level is not None else "N/A",
        # The classic page prints the unit after the number, so the long category goes short there.
        "unit": SHORT_NAMES.get(name, name),
        "category": name,
        "icon": icon,
        "level": level,
        "scale": "us",
        "pollutants": pollutants,
        "dominant": dominant,
    }
