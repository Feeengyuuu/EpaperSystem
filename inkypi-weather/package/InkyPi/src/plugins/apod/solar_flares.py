"""GOES X-ray flares and SWPC's 1-3 day flare probabilities, as pure normalizers.

``xray-flares-latest.json`` (one row, ~450 B) gives the current X-ray class and the latest
flare; ``xray-flares-7-day.json`` (~2 KB gzipped) lists the week's flares, from which the
page takes the strongest of the last 24 hours. ``solar_probabilities.json`` (~600 B) holds
SWPC's daily C/M/X flare and S1 proton probabilities for the next three days.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

XRAY_FLARES_ENDPOINT = "https://services.swpc.noaa.gov/json/goes/primary/xray-flares-latest.json"
XRAY_FLARES_WEEK_ENDPOINT = "https://services.swpc.noaa.gov/json/goes/primary/xray-flares-7-day.json"
SOLAR_PROBABILITIES_ENDPOINT = "https://services.swpc.noaa.gov/json/solar_probabilities.json"
# The cache identity of the X-ray source, which reads both flare products.
XRAY_CACHE_ENDPOINT = f"{XRAY_FLARES_ENDPOINT}|{XRAY_FLARES_WEEK_ENDPOINT}"

_CLASS = re.compile(r"^([ABCMX])(\d+(?:\.\d+)?)$")
_LETTER_FLUX = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}
PEAK_WINDOW = timedelta(hours=24)
KEEP_WINDOW = timedelta(hours=48)
FLARE_CLASSES = ("c", "m", "x")


def flare_class(value: Any) -> str | None:
    """A GOES class such as "B3.2" or "X1.0", normalised; None when it is not one."""
    if not isinstance(value, str):
        return None
    match = _CLASS.match(value.strip().upper())
    if match is None:
        return None
    return f"{match.group(1)}{float(match.group(2)):.1f}"


def class_flux(value: Any) -> float | None:
    """The peak 1-8 A flux (W/m2) a class names, for comparing classes."""
    normalised = flare_class(value)
    if normalised is None:
        return None
    return _LETTER_FLUX[normalised[0]] * float(normalised[1:])


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("flare timestamp must be a non-empty string")
    text = value.strip()
    parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _flare_row(row: Any) -> dict[str, str] | None:
    if not isinstance(row, Mapping):
        return None
    klass = flare_class(row.get("max_class"))
    try:
        when = _utc(row.get("max_time"))
    except ValueError:
        return None
    return {"class": klass, "max_time_utc": _stamp(when)} if klass else None


def normalize_xray(latest: Any, week: Any, *, now_utc: datetime) -> Mapping[str, Any]:
    """{"observed_at_utc", "current_class", "flares": [{"class", "max_time_utc"}, ...]} newest last.

    The current class is required; the flares are those of the last 48 hours from both
    products (the week list may be missing), without duplicates.
    """
    rows = latest if isinstance(latest, Sequence) and not isinstance(latest, (str, bytes)) else []
    current = rows[-1] if rows and isinstance(rows[-1], Mapping) else {}
    current_class = flare_class(current.get("current_class"))
    if current_class is None:
        raise ValueError("GOES X-ray current class is missing")
    observed = _utc(current.get("time_tag"))
    if observed > now_utc + timedelta(minutes=5):
        raise ValueError("GOES X-ray time is in the future")
    week_rows = week if isinstance(week, Sequence) and not isinstance(week, (str, bytes)) else []
    flares = {}
    for row in [*week_rows, current]:
        flare = _flare_row(row)
        if flare is None:
            continue
        when = _utc(flare["max_time_utc"])
        if now_utc - KEEP_WINDOW <= when <= now_utc + timedelta(minutes=5):
            flares[flare["max_time_utc"]] = flare
    return {
        "observed_at_utc": _stamp(observed),
        "current_class": current_class,
        "flares": [flares[key] for key in sorted(flares)],
    }


def normalize_flare_probabilities(raw: Any, *, now_utc: datetime) -> Mapping[str, Any]:
    """The newest issue's 1-3 day chances: {"issued_date", "c": [d1, d2, d3], "m": ..., "x": ..., "protons": ...}."""
    rows = [row for row in (raw if isinstance(raw, Sequence) else []) if isinstance(row, Mapping)]
    dated = []
    for row in rows:
        try:
            dated.append((_utc(row.get("date")), row))
        except ValueError:
            continue
    if not dated:
        raise ValueError("SWPC flare probabilities have no dated issue")
    issued, row = max(dated, key=lambda item: item[0])
    if issued > now_utc + timedelta(days=1):
        raise ValueError("SWPC flare probabilities are dated in the future")
    payload = {"issued_date": issued.date().isoformat()}
    for key, prefix in (("c", "c_class"), ("m", "m_class"), ("x", "x_class"), ("protons", "10mev_protons")):
        payload[key] = [_percent(row.get(f"{prefix}_{day}_day")) for day in (1, 2, 3)]
    return payload


def _percent(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("SWPC probability is invalid") from None
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError("SWPC probability must be between 0 and 100")
    return int(round(number))


def validate_xray(payload: Mapping[str, Any]) -> None:
    if flare_class(payload.get("current_class")) is None:
        raise ValueError("cached X-ray class is invalid")
    _utc(payload.get("observed_at_utc"))
    flares = payload.get("flares")
    if not isinstance(flares, Sequence):
        raise ValueError("cached flares must be a list")
    for flare in flares:
        if _flare_row({"max_class": (flare or {}).get("class"), "max_time": (flare or {}).get("max_time_utc")}) is None:
            raise ValueError("cached flare is invalid")


def validate_flare_probabilities(payload: Mapping[str, Any]) -> None:
    datetime.fromisoformat(str(payload.get("issued_date")))
    for key in (*FLARE_CLASSES, "protons"):
        values = payload.get(key)
        if not isinstance(values, Sequence) or len(values) != 3:
            raise ValueError("cached flare probabilities need three days")
        for value in values:
            _percent(value)


def project_xray(payload: Mapping[str, Any], *, now_utc: datetime) -> dict[str, Any]:
    """What the page shows: the class now and the strongest flare of the last 24 hours."""
    if not payload:
        return {}
    recent = []
    for flare in payload.get("flares") or ():
        when = _utc(flare["max_time_utc"])
        if now_utc - PEAK_WINDOW <= when <= now_utc + timedelta(minutes=5):
            recent.append((class_flux(flare["class"]) or 0.0, when, flare["class"]))
    peak = max(recent) if recent else None
    return {
        "current_class": payload.get("current_class"),
        "time_tag": payload.get("observed_at_utc"),
        "peak_class": peak[2] if peak else None,
        "peak_time": _stamp(peak[1]) if peak else None,
    }


def project_flare_probabilities(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    return {"issued_date": payload.get("issued_date"),
            **{key: list(payload.get(key) or ()) for key in (*FLARE_CLASSES, "protons")}}
