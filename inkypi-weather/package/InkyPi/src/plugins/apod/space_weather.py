"""Pure NOAA space-weather normalization and independent source caches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from plugins.base_plugin.render_provenance import SourceProvenance
from runtime.refresh_contracts import TaskContext
from utils.atomic_file import atomic_write_json
from utils.http_client import HttpClient


SCALES_ENDPOINT = "https://services.swpc.noaa.gov/products/noaa-scales.json"
KP_ENDPOINT = (
    "https://services.swpc.noaa.gov/products/"
    "noaa-planetary-k-index-forecast.json"
)
WIND_SPEED_ENDPOINT = (
    "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json"
)
WIND_MAG_ENDPOINT = (
    "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json"
)
ALERTS_ENDPOINT = "https://services.swpc.noaa.gov/products/alerts.json"
DONKI_FLR_ENDPOINT = "https://api.nasa.gov/DONKI/FLR"
DONKI_CME_ENDPOINT = "https://api.nasa.gov/DONKI/CME"
_DONKI_CACHE_ENDPOINT = f"{DONKI_FLR_ENDPOINT}|{DONKI_CME_ENDPOINT}"

SOURCE_CACHE_SCHEMA = 1
NOAA_TIMEOUT_SECONDS = 20
MAX_PROVIDER_JSON_BYTES = 2 * 1024 * 1024
MAX_SOURCE_CACHE_BYTES = 2 * 1024 * 1024
_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
_FRESH_FETCH_AGE = timedelta(minutes=30)

SourceState = Literal["live", "fresh_cache", "stale_cache", "unavailable"]
AlertState = Literal["active", "confirmed_empty", "unavailable"]
FreshnessKind = Literal["scales", "kp", "wind", "alerts", "donki"]


@dataclass(frozen=True)
class SourceEnvelope:
    schema: int
    endpoint: str
    fetched_at_utc: datetime
    observed_at_utc: datetime | None
    issued_at_utc: datetime | None
    valid_from_utc: datetime | None
    valid_until_utc: datetime | None
    payload: Mapping[str, Any]
    raw_digest: str


@dataclass(frozen=True)
class SourceResult:
    name: str
    state: SourceState
    envelope: SourceEnvelope | None
    error: str | None = None


@dataclass(frozen=True)
class SpaceWeatherSnapshot:
    fetched_at_utc: datetime
    oldest_core_observed_at_utc: datetime | None
    current_scales: Mapping[str, Any]
    current_kp: Mapping[str, Any]
    forecast_48h: Mapping[str, Any]
    solar_wind: Mapping[str, Any]
    magnetic_field: Mapping[str, Any]
    probabilities: Mapping[str, Any]
    scales: SourceResult
    kp: SourceResult
    wind_speed: SourceResult
    wind_magnetic: SourceResult
    alerts: SourceResult
    donki: SourceResult
    alert_state: AlertState
    alert: Mapping[str, Any] | None
    donki_event: Mapping[str, Any] | None
    sources: Mapping[str, SourceResult]
    errors: Sequence[str]
    aggregate_state: SourceProvenance


def normalize_scales(
    raw: Mapping[str, Any], *, now_utc: datetime
) -> Mapping[str, Any]:
    """Normalize NOAA's keyed yesterday/current/three-day scales product."""

    now = _as_utc(now_utc)
    if not isinstance(raw, Mapping):
        raise ValueError("NOAA scales payload must be an object")

    normalized_entries: dict[str, Mapping[str, Any]] = {}
    entries: dict[str, tuple[datetime, Mapping[str, Any]]] = {}
    for product_key in ("-1", "0", "1", "2", "3"):
        entry = raw.get(product_key)
        if not isinstance(entry, Mapping):
            raise ValueError(f"NOAA scales key {product_key} is missing or invalid")
        provider_time = _parse_utc_parts(entry.get("DateStamp"), entry.get("TimeStamp"))
        normalized_entry = {
            "product_key": product_key,
            "valid_at_utc": _format_utc(provider_time),
            "g": _scale_value(entry, "G", required=product_key in {"-1", "0"}),
            "r": _scale_value(entry, "R", required=product_key in {"-1", "0"}),
            "s": _scale_value(entry, "S", required=product_key in {"-1", "0"}),
        }
        normalized_entries[product_key] = normalized_entry
        entries[product_key] = (provider_time, entry)

    forecast_keys = sorted(
        ("1", "2", "3"),
        key=lambda product_key: (entries[product_key][0].date(), product_key),
    )
    timeline = [
        normalized_entries["-1"],
        normalized_entries["0"],
        *(normalized_entries[product_key] for product_key in forecast_keys),
    ]

    observed_at = entries["0"][0]
    if observed_at > now + _FUTURE_CLOCK_SKEW:
        raise ValueError("NOAA scales provider time is unexpectedly in the future")

    current = timeline[1]
    probabilities = None
    for product_key in forecast_keys:
        valid_at, entry = entries[product_key]
        raw_probabilities = (
            _nested(entry, "R").get("MinorProb"),
            _nested(entry, "R").get("MajorProb"),
            _nested(entry, "S").get("Prob"),
        )
        missing = tuple(
            value is None or str(value).strip() == "" for value in raw_probabilities
        )
        for value, is_missing in zip(raw_probabilities, missing):
            if not is_missing:
                _probability(value)
        if all(missing):
            continue
        if any(missing):
            continue
        probabilities = {
            "valid_at_utc": _format_utc(valid_at),
            "r_minor": _probability(raw_probabilities[0]),
            "r_major": _probability(raw_probabilities[1]),
            "s": _probability(raw_probabilities[2]),
        }
        break

    payload = {
        "observed_at_utc": _format_utc(observed_at),
        "current": {key: current[key] for key in ("g", "r", "s")},
        "forecast_g": [
            {
                "valid_at_utc": normalized_entries[product_key]["valid_at_utc"],
                "g": normalized_entries[product_key]["g"],
            }
            for product_key in forecast_keys
        ],
        "probabilities": probabilities,
        "timeline": timeline,
    }
    return _freeze(payload)


def normalize_kp(raw: Sequence[Any], *, now_utc: datetime) -> Mapping[str, Any]:
    """Normalize both current object rows and NOAA's legacy header/row form."""

    now = _as_utc(now_utc)
    rows = _kp_object_rows(raw)
    normalized_rows = []
    for row in rows:
        time_tag = _parse_utc(row.get("time_tag"))
        kp = _finite_number(row.get("kp"), label="Kp")
        if not 0 <= kp <= 9:
            raise ValueError("Kp must be between 0 and 9")
        observed = str(row.get("observed") or "").strip().lower()
        if observed not in {"observed", "estimated", "predicted"}:
            raise ValueError("Kp row has an invalid observation state")
        scale = row.get("noaa_scale")
        if scale is not None and not isinstance(scale, str):
            raise ValueError("Kp noaa_scale must be a string or null")
        normalized_rows.append(
            {
                "_time": time_tag,
                "time_tag": _format_utc(time_tag),
                "kp": kp,
                "observed": observed,
                "noaa_scale": scale,
            }
        )

    current_candidates = [
        row
        for row in normalized_rows
        if row["observed"] in {"observed", "estimated"}
        and row["_time"] <= now + _FUTURE_CLOCK_SKEW
    ]
    if not current_candidates:
        raise ValueError("Kp payload has no current observed or estimated row")
    current = max(current_candidates, key=lambda row: row["_time"])

    future_limit = now + timedelta(hours=48)
    forecast = sorted(
        (
            row
            for row in normalized_rows
            if row["observed"] == "predicted" and now < row["_time"] <= future_limit
        ),
        key=lambda row: row["_time"],
    )
    predicted_peak = None
    if forecast:
        peak_kp = max(row["kp"] for row in forecast)
        predicted_peak = next(row for row in forecast if row["kp"] == peak_kp)

    payload = {
        "observed_at_utc": current["time_tag"],
        "current": _public_kp_row(current),
        "forecast_48h": [_public_kp_row(row) for row in forecast],
        "predicted_peak": (
            _public_kp_row(predicted_peak) if predicted_peak is not None else None
        ),
    }
    return _freeze(payload)


def normalize_wind_speed(
    raw: Sequence[Mapping[str, Any]], *, now_utc: datetime
) -> Mapping[str, Any]:
    """Select the newest valid solar-wind observation, independent of row order."""

    row, observed_at = _newest_timed_row(raw, now_utc=now_utc)
    speed = _finite_number(row.get("proton_speed"), label="solar-wind speed")
    if speed < 0:
        raise ValueError("solar-wind speed must not be negative")
    return _freeze(
        {
            "observed_at_utc": _format_utc(observed_at),
            "speed_km_s": speed,
        }
    )


def normalize_wind_magnetic_field(
    raw: Sequence[Mapping[str, Any]], *, now_utc: datetime
) -> Mapping[str, Any]:
    """Select the newest field row and retain the unrounded Bz sign."""

    row, observed_at = _newest_timed_row(raw, now_utc=now_utc)
    bt = _finite_number(row.get("bt"), label="Bt")
    bz = _finite_number(row.get("bz_gsm"), label="Bz")
    direction = "south" if bz < 0 else "north" if bz > 0 else "neutral"
    return _freeze(
        {
            "observed_at_utc": _format_utc(observed_at),
            "bt_nt": bt,
            "bz_gsm_nt": bz,
            "bz_direction": direction,
        }
    )


def fold_alerts(
    raw: Sequence[Mapping[str, Any]], *, now_utc: datetime
) -> tuple[AlertState, Mapping[str, Any] | None]:
    """Fold NOAA alert state in issue order and return one active candidate."""

    candidates = _fold_alert_candidates(raw)
    selected = _select_alert_candidate(candidates, now_utc=now_utc)
    if selected is None:
        return "confirmed_empty", None
    return "active", selected


def select_donki_event(
    *,
    flr: Sequence[Mapping[str, Any]],
    cme: Sequence[Mapping[str, Any]],
    now_utc: datetime,
) -> Mapping[str, Any] | None:
    """Select one significant DONKI event from explicit provider evidence."""

    now = _as_utc(now_utc)
    cme_candidates = _donki_cme_candidates(cme, now_utc=now)
    if cme_candidates:
        selected = min(
            cme_candidates,
            key=lambda item: (
                abs(item["_arrival"] - now),
                -item["_submitted"].timestamp(),
                item["event_id"],
            ),
        )
        return _freeze({key: value for key, value in selected.items() if key[0] != "_"})

    flare_candidates = _donki_flare_candidates(flr, now_utc=now)
    if not flare_candidates:
        return None
    selected = max(
        flare_candidates,
        key=lambda item: (
            item["_class_rank"],
            item["_class_value"],
            item["_peak"],
            item["event_id"],
        ),
    )
    return _freeze({key: value for key, value in selected.items() if key[0] != "_"})


def _fold_alert_candidates(
    raw: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise ValueError("NOAA alerts payload must be an array")
    ordered = []
    for row in raw:
        if not isinstance(row, Mapping):
            raise ValueError("NOAA alert row must be an object")
        try:
            issued_at = _parse_utc(row.get("issue_datetime"))
        except ValueError:
            continue
        ordered.append((issued_at, row))
    ordered.sort(key=lambda item: item[0])

    active: dict[str, Mapping[str, Any]] = {}
    for issued_at, row in ordered:
        parsed = _parse_alert_message(row, issued_at=issued_at)
        if parsed is None:
            continue
        cancel_serial = parsed.pop("_cancel_serial", None)
        if cancel_serial is not None:
            active.pop(cancel_serial, None)
            continue
        replaced_serial = parsed.pop("_replaced_serial", None)
        if replaced_serial is not None:
            previous = active.pop(replaced_serial, None)
            if previous is not None:
                parsed = _inherit_alert_extension(parsed, previous)
        if parsed.pop("_supersedes_watches", False):
            active = {
                serial: candidate
                for serial, candidate in active.items()
                if candidate.get("kind") != "WATCH"
            }
        serial = parsed.get("serial")
        if isinstance(serial, str):
            active[serial] = _freeze(parsed)
    return tuple(active.values())


def _parse_alert_message(
    row: Mapping[str, Any], *, issued_at: datetime
) -> dict[str, Any] | None:
    message = row.get("message")
    product_id = row.get("product_id")
    if not isinstance(message, str) or not isinstance(product_id, str):
        return None

    serial_match = re.search(r"(?im)^Serial Number:\s*([A-Za-z0-9-]+)\s*$", message)
    cancel_match = re.search(
        r"(?im)^Cancel Serial Number:\s*([A-Za-z0-9-]+)\s*$", message
    )
    if cancel_match is not None:
        return {"_cancel_serial": cancel_match.group(1)}
    if serial_match is None:
        return None

    kind_match = re.search(
        r"(?im)^(?:EXTENDED\s+)?(ALERT|WARNING|WATCH|SUMMARY)\s*:\s*(.+?)\s*$",
        message,
    )
    if kind_match is None:
        return None
    kind = kind_match.group(1).upper()
    headline = kind_match.group(2).strip()
    severity_match = re.search(r"(?im)^NOAA Scale:\s*([GRS][1-5])\b", message)
    severity = severity_match.group(1).upper() if severity_match else None

    try:
        valid_until = _labeled_alert_datetime(
            message, labels=("Now Valid Until", "Valid To")
        )
    except ValueError:
        return None
    display_source = "provider"
    display_until = valid_until
    if display_until is None and kind == "ALERT":
        display_until = issued_at + timedelta(hours=3)
        display_source = "local_alert"
    elif display_until is None and kind == "SUMMARY":
        display_until = issued_at + timedelta(hours=24)
        display_source = "local_summary"
    elif display_until is None and kind == "WATCH":
        forecast_dates = _watch_forecast_dates(message, issued_at=issued_at)
        if forecast_dates:
            last_day_end = datetime.combine(
                max(forecast_dates), datetime.max.time().replace(microsecond=0), timezone.utc
            )
            display_until = min(last_day_end, issued_at + timedelta(hours=96))
            display_source = "local_watch_forecast"
    if display_until is None:
        return None
    if kind == "WARNING" and valid_until is None:
        return None
    if kind == "SUMMARY" and severity is None:
        return None

    try:
        event_start, event_end = _synoptic_period(message, issued_at=issued_at)
    except ValueError:
        event_start, event_end = None, None
    replaced_match = re.search(
        r"(?im)^(?:Extension to|Replaces|Supersedes) Serial Number:\s*"
        r"([A-Za-z0-9-]+)\s*$",
        message,
    )
    supersedes_watches = bool(
        kind == "WATCH"
        and re.search(
            r"(?i)THIS SUPERSEDES (?:ANY|ALL) PRIOR WATCHES", message
        )
    )
    return {
        "product_id": product_id,
        "serial": serial_match.group(1),
        "kind": kind,
        "severity": severity,
        "headline": headline,
        "issue_datetime": _format_utc(issued_at),
        "event_period_start": _optional_utc(event_start),
        "event_period_end": _optional_utc(event_end),
        "valid_until": _optional_utc(valid_until),
        "display_until": _format_utc(display_until),
        "display_until_source": display_source,
        "_replaced_serial": replaced_match.group(1) if replaced_match else None,
        "_supersedes_watches": supersedes_watches,
    }


def _inherit_alert_extension(
    candidate: dict[str, Any], previous: Mapping[str, Any]
) -> dict[str, Any]:
    for key in ("kind", "severity", "headline", "event_period_start", "event_period_end"):
        if candidate.get(key) is None:
            candidate[key] = previous.get(key)
    return candidate


def _select_alert_candidate(
    candidates: Sequence[Mapping[str, Any]], *, now_utc: datetime
) -> Mapping[str, Any] | None:
    now = _as_utc(now_utc)
    eligible = []
    for candidate in candidates:
        try:
            issued_at = _parse_utc(candidate.get("issue_datetime"))
            display_until = _parse_utc(candidate.get("display_until"))
        except ValueError:
            continue
        if issued_at > now + _FUTURE_CLOCK_SKEW or display_until < now:
            continue
        eligible.append((candidate, issued_at))
    if not eligible:
        return None
    primary = [item for item in eligible if item[0].get("kind") != "SUMMARY"]
    if primary:
        eligible = primary
    kind_rank = {"SUMMARY": 0, "WATCH": 1, "WARNING": 2, "ALERT": 3}

    def priority(item):
        candidate, issued_at = item
        severity = candidate.get("severity")
        severity_level = int(severity[1]) if isinstance(severity, str) else 0
        return severity_level, kind_rank.get(candidate.get("kind"), -1), issued_at

    return _freeze(dict(max(eligible, key=priority)[0]))


def _labeled_alert_datetime(
    message: str, *, labels: Sequence[str]
) -> datetime | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?im)^(?:{label_pattern}):\s*(.+?)\s*$", message)
    if match is None:
        return None
    return _parse_provider_datetime_text(match.group(1))


def _parse_provider_datetime_text(value: str) -> datetime:
    cleaned = re.sub(r"\s+(UTC|UT|Z)$", "", value.strip(), flags=re.IGNORECASE)
    try:
        return _parse_utc(cleaned)
    except ValueError:
        pass
    for pattern in (
        "%Y %b %d %H%M",
        "%Y %B %d %H%M",
        "%Y-%m-%d %H%M",
        "%Y %b %d %H:%M",
        "%Y %B %d %H:%M",
    ):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError("NOAA alert datetime is invalid")


def _watch_forecast_dates(message: str, *, issued_at: datetime) -> list[Any]:
    marker = re.search(r"(?i)NOAA\s+Kp\s+index\s+forecast", message)
    forecast_text = message[marker.start() :] if marker is not None else "\n".join(
        line for line in message.splitlines() if re.search(r"(?i)\bDay\s*\d+\b", line)
    )
    dates = set()
    for match in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", forecast_text):
        try:
            dates.add(
                datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    tzinfo=timezone.utc,
                ).date()
            )
        except ValueError:
            continue
    month_pattern = r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    textual = [
        (match.group(1), match.group(2))
        for match in re.finditer(
            rf"(?i)\b({month_pattern})[a-z]*\s+(\d{{1,2}})\b", forecast_text
        )
    ]
    textual.extend(
        (match.group(2), match.group(1))
        for match in re.finditer(
            rf"(?i)\b(\d{{1,2}})\s+({month_pattern})[a-z]*\b", forecast_text
        )
    )
    for month, day in textual:
        try:
            parsed = datetime.strptime(
                f"{issued_at.year} {month} {day}", "%Y %b %d"
            ).date()
            if parsed < issued_at.date() - timedelta(days=30):
                parsed = parsed.replace(year=parsed.year + 1)
            dates.add(parsed)
        except ValueError:
            continue
    return sorted(dates)


def _synoptic_period(
    message: str, *, issued_at: datetime
) -> tuple[datetime | None, datetime | None]:
    match = re.search(
        r"(?im)^Synoptic Period:\s*(?:(20\d{2}-\d{2}-\d{2})\s+)?"
        r"(\d{2}):?(\d{2})-(\d{2}):?(\d{2})\s*(?:UTC|UT|Z)?\s*$",
        message,
    )
    if match is None:
        return None, None
    day = (
        datetime.strptime(match.group(1), "%Y-%m-%d").date()
        if match.group(1)
        else issued_at.date()
    )
    start = datetime(
        day.year, day.month, day.day, int(match.group(2)), int(match.group(3)),
        tzinfo=timezone.utc,
    )
    end = datetime(
        day.year, day.month, day.day, int(match.group(4)), int(match.group(5)),
        tzinfo=timezone.utc,
    )
    if end < start:
        end += timedelta(days=1)
    return start, end


def _donki_flare_candidates(
    raw: Sequence[Mapping[str, Any]], *, now_utc: datetime
) -> list[dict[str, Any]]:
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise ValueError("DONKI FLR payload must be an array")
    candidates = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        class_type = row.get("classType")
        match = re.fullmatch(r"\s*([A-Za-z])\s*(\d+(?:\.\d+)?)\s*", str(class_type or ""))
        if match is None:
            continue
        letter = match.group(1).upper()
        value = float(match.group(2))
        if not (letter == "X" or letter == "M" and value >= 5.0):
            continue
        try:
            peak = _parse_utc(row.get("peakTime"))
        except ValueError:
            continue
        if not now_utc - timedelta(hours=24) <= peak <= now_utc:
            continue
        event_id = row.get("flrID")
        if not isinstance(event_id, str) or not event_id:
            continue
        candidates.append(
            {
                "kind": "FLR",
                "event_id": event_id,
                "class_type": f"{letter}{match.group(2)}",
                "peak_time_utc": _format_utc(peak),
                "source_location": row.get("sourceLocation"),
                "source_note": "NASA experimental/model estimate",
                "_class_rank": 2 if letter == "X" else 1,
                "_class_value": value,
                "_peak": peak,
            }
        )
    return candidates


def _donki_cme_candidates(
    raw: Sequence[Mapping[str, Any]], *, now_utc: datetime
) -> list[dict[str, Any]]:
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise ValueError("DONKI CME payload must be an array")
    latest_analysis: dict[str, tuple[datetime, Mapping[str, Any]]] = {}
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        event_id = row.get("activityID")
        analyses = row.get("cmeAnalyses")
        if (
            not isinstance(event_id, str)
            or not event_id
            or isinstance(analyses, (str, bytes, bytearray))
            or not isinstance(analyses, Sequence)
        ):
            continue
        for analysis in analyses:
            if not isinstance(analysis, Mapping) or analysis.get("isMostAccurate") is not True:
                continue
            try:
                submitted = _parse_utc(analysis.get("submissionTime"))
            except ValueError:
                continue
            previous = latest_analysis.get(event_id)
            if previous is None or submitted > previous[0]:
                latest_analysis[event_id] = submitted, analysis

    candidates = []
    for event_id, (submitted, analysis) in latest_analysis.items():
        enlil_list = analysis.get("enlilList")
        if isinstance(enlil_list, (str, bytes, bytearray)) or not isinstance(
            enlil_list, Sequence
        ):
            continue
        arrivals = []
        for enlil in enlil_list:
            if not isinstance(enlil, Mapping) or not _has_earth_impact(enlil):
                continue
            try:
                arrival = _parse_utc(enlil.get("estimatedShockArrivalTime"))
            except ValueError:
                continue
            if now_utc - timedelta(hours=6) <= arrival <= now_utc + timedelta(
                hours=72
            ):
                arrivals.append(arrival)
        if not arrivals:
            continue
        arrival = min(arrivals, key=lambda value: (abs(value - now_utc), value))
        try:
            speed = _finite_number(analysis.get("speed"), label="CME speed")
        except ValueError:
            speed = None
        candidates.append(
            {
                "kind": "CME",
                "event_id": event_id,
                "predicted_arrival_utc": _format_utc(arrival),
                "analysis_submitted_at_utc": _format_utc(submitted),
                "speed_km_s": speed,
                "source_note": "NASA experimental/model estimate",
                "_arrival": arrival,
                "_submitted": submitted,
            }
        )
    return candidates


def _has_earth_impact(enlil: Mapping[str, Any]) -> bool:
    if any(
        enlil.get(field) is True
        for field in ("earthImpact", "isEarthGB", "isEarthMinorImpact")
    ):
        return True
    impacts = enlil.get("impactList")
    if isinstance(impacts, (str, bytes, bytearray)) or not isinstance(
        impacts, Sequence
    ):
        return False
    return any(
        isinstance(impact, Mapping)
        and isinstance(impact.get("location"), str)
        and impact["location"].strip().casefold() == "earth"
        for impact in impacts
    )


class SpaceWeatherRepository:
    """Fetch and cache each NOAA product without cross-source transactions."""

    def __init__(self, *, cache_dir: Path, http: HttpClient):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.http = http

    def refresh_core(
        self, *, now_utc: datetime, context: TaskContext | None
    ) -> tuple[SourceResult, SourceResult]:
        now = _as_utc(now_utc)
        scales = self._refresh_one(
            name="scales",
            endpoint=SCALES_ENDPOINT,
            filename="scales.json",
            normalizer=normalize_scales,
            freshness_kind="scales",
            now_utc=now,
            context=context,
        )
        kp = self._refresh_one(
            name="kp",
            endpoint=KP_ENDPOINT,
            filename="kp.json",
            normalizer=normalize_kp,
            freshness_kind="kp",
            now_utc=now,
            context=context,
        )
        return scales, kp

    def refresh_wind(
        self, *, now_utc: datetime, context: TaskContext | None
    ) -> tuple[SourceResult, SourceResult]:
        now = _as_utc(now_utc)
        speed = self._refresh_one(
            name="wind_speed",
            endpoint=WIND_SPEED_ENDPOINT,
            filename="wind_speed.json",
            normalizer=normalize_wind_speed,
            freshness_kind="wind",
            now_utc=now,
            context=context,
        )
        magnetic = self._refresh_one(
            name="wind_mag",
            endpoint=WIND_MAG_ENDPOINT,
            filename="wind_mag.json",
            normalizer=normalize_wind_magnetic_field,
            freshness_kind="wind",
            now_utc=now,
            context=context,
        )
        return speed, magnetic

    def refresh_alerts(
        self, *, now_utc: datetime, context: TaskContext | None
    ) -> tuple[SourceResult, AlertState, Mapping[str, Any] | None]:
        now = _as_utc(now_utc)
        path = self.cache_dir / "alerts.json"
        try:
            response = self.http.request_bytes(
                "GET",
                ALERTS_ENDPOINT,
                context=context,
                timeout=NOAA_TIMEOUT_SECONDS,
                max_bytes=MAX_PROVIDER_JSON_BYTES,
            )
            raw_bytes = response.data
            if not isinstance(raw_bytes, bytes):
                raise ValueError("HTTP client returned a non-bytes response")
            raw = json.loads(raw_bytes)
            candidates = _fold_alert_candidates(raw)
            payload = _freeze({"candidates": candidates})
            envelope = _build_optional_envelope(
                endpoint=ALERTS_ENDPOINT,
                fetched_at_utc=now,
                payload=payload,
                raw_digest=hashlib.sha256(raw_bytes).hexdigest(),
            )
            atomic_write_json(path, _serialize_envelope(envelope))
            result = SourceResult(name="alerts", state="live", envelope=envelope)
            selected = _select_alert_candidate(candidates, now_utc=now)
            if selected is None:
                return result, "confirmed_empty", None
            return result, "active", selected
        except Exception as error:
            cached = _read_envelope(
                path, expected_endpoint=ALERTS_ENDPOINT, freshness_kind="alerts"
            )
            result = self._cached_optional_result(
                name="alerts",
                cached=cached,
                error=error,
                now_utc=now,
                freshness_kind="alerts",
            )
            if result.state == "fresh_cache" and result.envelope is not None:
                candidates = result.envelope.payload["candidates"]
                selected = _select_alert_candidate(candidates, now_utc=now)
                if selected is not None:
                    return result, "active", selected
            return result, "unavailable", None

    def refresh_donki(
        self,
        *,
        nasa_api_key: str,
        now_utc: datetime,
        context: TaskContext | None,
    ) -> tuple[SourceResult, Mapping[str, Any] | None]:
        now = _as_utc(now_utc)
        path = self.cache_dir / "donki.json"
        today = now.date()
        try:
            flr_response = self.http.request_bytes(
                "GET",
                DONKI_FLR_ENDPOINT,
                params={
                    "startDate": (today - timedelta(days=1)).isoformat(),
                    "endDate": today.isoformat(),
                    "api_key": nasa_api_key,
                },
                context=context,
                timeout=NOAA_TIMEOUT_SECONDS,
                max_bytes=MAX_PROVIDER_JSON_BYTES,
            )
            cme_response = self.http.request_bytes(
                "GET",
                DONKI_CME_ENDPOINT,
                params={
                    "startDate": (today - timedelta(days=7)).isoformat(),
                    "endDate": today.isoformat(),
                    "api_key": nasa_api_key,
                },
                context=context,
                timeout=NOAA_TIMEOUT_SECONDS,
                max_bytes=MAX_PROVIDER_JSON_BYTES,
            )
            flr_bytes = flr_response.data
            cme_bytes = cme_response.data
            if not isinstance(flr_bytes, bytes) or not isinstance(cme_bytes, bytes):
                raise ValueError("HTTP client returned a non-bytes response")
            flr = json.loads(flr_bytes)
            cme = json.loads(cme_bytes)
            selected = select_donki_event(flr=flr, cme=cme, now_utc=now)
            payload = _freeze({"flr": flr, "cme": cme})
            envelope = _build_optional_envelope(
                endpoint=_DONKI_CACHE_ENDPOINT,
                fetched_at_utc=now,
                payload=payload,
                raw_digest=hashlib.sha256(flr_bytes + b"\0" + cme_bytes).hexdigest(),
            )
            atomic_write_json(path, _serialize_envelope(envelope))
            return SourceResult(name="donki", state="live", envelope=envelope), selected
        except Exception as error:
            cached = _read_envelope(
                path,
                expected_endpoint=_DONKI_CACHE_ENDPOINT,
                freshness_kind="donki",
            )
            result = self._cached_optional_result(
                name="donki",
                cached=cached,
                error=error,
                now_utc=now,
                freshness_kind="donki",
            )
            if result.state == "fresh_cache" and result.envelope is not None:
                selected = select_donki_event(
                    flr=result.envelope.payload["flr"],
                    cme=result.envelope.payload["cme"],
                    now_utc=now,
                )
                return result, selected
            return result, None

    def _cached_optional_result(
        self,
        *,
        name: str,
        cached: SourceEnvelope | None,
        error: Exception,
        now_utc: datetime,
        freshness_kind: FreshnessKind,
    ) -> SourceResult:
        if cached is None:
            return SourceResult(
                name=name, state="unavailable", envelope=None, error=_error_text(error)
            )
        state = _classify_freshness(
            cached, now_utc=now_utc, freshness_kind=freshness_kind
        )
        return SourceResult(
            name=name,
            state=state,
            envelope=None if state == "unavailable" else cached,
            error=_error_text(error),
        )

    def _refresh_one(
        self,
        *,
        name: str,
        endpoint: str,
        filename: str,
        normalizer,
        freshness_kind: FreshnessKind,
        now_utc: datetime,
        context: TaskContext | None,
    ) -> SourceResult:
        path = self.cache_dir / filename
        try:
            response = self.http.request_bytes(
                "GET",
                endpoint,
                context=context,
                timeout=NOAA_TIMEOUT_SECONDS,
                max_bytes=MAX_PROVIDER_JSON_BYTES,
            )
            raw_bytes = response.data
            if not isinstance(raw_bytes, bytes):
                raise ValueError("HTTP client returned a non-bytes response")
            raw = json.loads(raw_bytes)
            payload = normalizer(raw, now_utc=now_utc)
            envelope = _build_envelope(
                endpoint=endpoint,
                fetched_at_utc=now_utc,
                payload=payload,
                raw_digest=hashlib.sha256(raw_bytes).hexdigest(),
                freshness_kind=freshness_kind,
            )
            state = _classify_freshness(
                envelope,
                now_utc=now_utc,
                freshness_kind=freshness_kind,
                live=True,
            )
            if state == "unavailable":
                raise ValueError(
                    f"{name} provider data is outside its diagnostic age window"
                )
            atomic_write_json(path, _serialize_envelope(envelope))
        except Exception as error:
            cached = _read_envelope(
                path,
                expected_endpoint=endpoint,
                freshness_kind=freshness_kind,
            )
            if cached is None:
                return SourceResult(
                    name=name,
                    state="unavailable",
                    envelope=None,
                    error=_error_text(error),
                )
            state = _classify_freshness(
                cached, now_utc=now_utc, freshness_kind=freshness_kind
            )
            if state == "unavailable":
                return SourceResult(
                    name=name,
                    state=state,
                    envelope=None,
                    error=_error_text(error),
                )
            return SourceResult(
                name=name,
                state=state,
                envelope=cached,
                error=_error_text(error),
            )

        return SourceResult(
            name=name,
            state=state,
            envelope=envelope,
            error=(f"{name} provider data is stale" if state == "stale_cache" else None),
        )


def refresh_space_weather(
    repository: SpaceWeatherRepository,
    *,
    nasa_api_key: str,
    now_utc: datetime,
    context: TaskContext | None,
) -> SpaceWeatherSnapshot:
    """Refresh independent sources and project them into one display snapshot."""

    now = _as_utc(now_utc)
    scales, kp = repository.refresh_core(now_utc=now, context=context)
    wind_speed, wind_magnetic = repository.refresh_wind(
        now_utc=now, context=context
    )
    alerts, alert_state, alert = repository.refresh_alerts(
        now_utc=now, context=context
    )
    donki, donki_event = repository.refresh_donki(
        nasa_api_key=nasa_api_key, now_utc=now, context=context
    )
    sources = {
        result.name: result
        for result in (scales, kp, wind_speed, wind_magnetic, alerts, donki)
    }

    scales_payload = _result_payload(scales)
    kp_payload = _result_payload(kp)
    speed_payload = _result_payload(wind_speed)
    magnetic_payload = _result_payload(wind_magnetic)
    current_scales = _mapping_copy(scales_payload.get("current"))
    kp_current = _mapping_copy(kp_payload.get("current"))
    current_kp = (
        {
            "value": kp_current.get("kp"),
            "mode": kp_current.get("observed"),
            "time_tag": kp_current.get("time_tag"),
            "noaa_scale": kp_current.get("noaa_scale"),
        }
        if kp_current
        else {}
    )
    peak = _mapping_copy(kp_payload.get("predicted_peak"))
    forecast_48h = (
        {
            "max_kp": peak.get("kp"),
            "noaa_scale": peak.get("noaa_scale"),
            "time_tag": peak.get("time_tag"),
        }
        if peak
        else {}
    )
    solar_wind = (
        {
            "speed_km_s": speed_payload.get("speed_km_s"),
            "time_tag": speed_payload.get("observed_at_utc"),
        }
        if speed_payload
        else {}
    )
    magnetic_field = (
        {
            "bt_nt": magnetic_payload.get("bt_nt"),
            "bz_nt": magnetic_payload.get("bz_gsm_nt"),
            "direction": magnetic_payload.get("bz_direction"),
            "time_tag": magnetic_payload.get("observed_at_utc"),
        }
        if magnetic_payload
        else {}
    )
    scale_probabilities = _mapping_copy(scales_payload.get("probabilities"))
    probabilities = (
        {
            "r1_r2": scale_probabilities.get("r_minor"),
            "r3_r5": scale_probabilities.get("r_major"),
            "s1_plus": scale_probabilities.get("s"),
            "forecast_date": scale_probabilities.get("valid_at_utc"),
        }
        if scale_probabilities
        else {}
    )

    displayed = []
    for result, value in (
        (scales, current_scales),
        (kp, current_kp),
        (kp, forecast_48h),
        (wind_speed, solar_wind),
        (wind_magnetic, magnetic_field),
        (scales, probabilities),
        (alerts, alert),
        (donki, donki_event),
    ):
        if value:
            displayed.append(result.state)
    aggregate_state = _aggregate_provenance(displayed)

    errors = []
    for result in (scales, kp):
        if result.state != "live" or result.error is not None:
            errors.append(f"mandatory-core failure: {result.name}")
    for result in sources.values():
        if result.error:
            errors.append(f"{result.name}: {result.error}")

    core_times = [
        result.envelope.observed_at_utc
        for result in (scales, kp)
        if result.envelope is not None and result.envelope.observed_at_utc is not None
    ]
    return SpaceWeatherSnapshot(
        fetched_at_utc=now,
        oldest_core_observed_at_utc=min(core_times) if core_times else None,
        current_scales=_freeze(current_scales),
        current_kp=_freeze(current_kp),
        forecast_48h=_freeze(forecast_48h),
        solar_wind=_freeze(solar_wind),
        magnetic_field=_freeze(magnetic_field),
        probabilities=_freeze(probabilities),
        scales=scales,
        kp=kp,
        wind_speed=wind_speed,
        wind_magnetic=wind_magnetic,
        alerts=alerts,
        donki=donki,
        alert_state=alert_state,
        alert=_freeze(alert) if alert is not None else None,
        donki_event=_freeze(donki_event) if donki_event is not None else None,
        sources=_freeze(sources),
        errors=tuple(errors),
        aggregate_state=aggregate_state,
    )


def _result_payload(result: SourceResult) -> Mapping[str, Any]:
    return result.envelope.payload if result.envelope is not None else {}


def _mapping_copy(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _aggregate_provenance(states: Sequence[SourceState]) -> SourceProvenance:
    if not states:
        return SourceProvenance.LOCAL_FALLBACK
    if "stale_cache" in states:
        return SourceProvenance.STALE_CACHE
    if all(state == "fresh_cache" for state in states):
        return SourceProvenance.FRESH_CACHE
    return SourceProvenance.LIVE


def _build_optional_envelope(
    *,
    endpoint: str,
    fetched_at_utc: datetime,
    payload: Mapping[str, Any],
    raw_digest: str,
) -> SourceEnvelope:
    return SourceEnvelope(
        schema=SOURCE_CACHE_SCHEMA,
        endpoint=endpoint,
        fetched_at_utc=_as_utc(fetched_at_utc),
        observed_at_utc=None,
        issued_at_utc=None,
        valid_from_utc=None,
        valid_until_utc=None,
        payload=_freeze(payload),
        raw_digest=raw_digest,
    )


def _build_envelope(
    *,
    endpoint: str,
    fetched_at_utc: datetime,
    payload: Mapping[str, Any],
    raw_digest: str,
    freshness_kind: FreshnessKind,
) -> SourceEnvelope:
    observed_at = _parse_utc(payload["observed_at_utc"])
    issued_at = observed_at if freshness_kind == "scales" else None
    valid_from = None
    valid_until = None
    if freshness_kind == "kp":
        valid_from = observed_at
        valid_until = observed_at + timedelta(hours=3, minutes=30)
    elif freshness_kind == "scales":
        forecast = payload.get("forecast_g")
        if isinstance(forecast, Sequence) and forecast:
            valid_from = _parse_utc(forecast[0]["valid_at_utc"])
            valid_until = _parse_utc(forecast[-1]["valid_at_utc"]) + timedelta(days=1)
    return SourceEnvelope(
        schema=SOURCE_CACHE_SCHEMA,
        endpoint=endpoint,
        fetched_at_utc=_as_utc(fetched_at_utc),
        observed_at_utc=observed_at,
        issued_at_utc=issued_at,
        valid_from_utc=valid_from,
        valid_until_utc=valid_until,
        payload=_freeze(payload),
        raw_digest=raw_digest,
    )


def _classify_freshness(
    envelope: SourceEnvelope,
    *,
    now_utc: datetime,
    freshness_kind: FreshnessKind,
    live: bool = False,
) -> SourceState:
    now = _as_utc(now_utc)
    if envelope.fetched_at_utc > now + _FUTURE_CLOCK_SKEW:
        return "unavailable"
    fetch_age = max(timedelta(0), now - envelope.fetched_at_utc)
    if freshness_kind == "alerts":
        if fetch_age <= timedelta(minutes=30):
            return "live" if live else "fresh_cache"
        return "stale_cache" if fetch_age <= timedelta(hours=2) else "unavailable"
    if freshness_kind == "donki":
        if fetch_age <= timedelta(minutes=60):
            return "live" if live else "fresh_cache"
        return "stale_cache" if fetch_age <= timedelta(hours=24) else "unavailable"

    provider_time = envelope.observed_at_utc or envelope.issued_at_utc
    if provider_time is None or provider_time > now + _FUTURE_CLOCK_SKEW:
        return "unavailable"
    provider_age = now - provider_time

    if freshness_kind == "scales":
        fresh_provider = provider_age <= timedelta(minutes=30)
        diagnostic = (
            fetch_age <= timedelta(hours=2)
            and provider_age <= timedelta(hours=2)
        )
    elif freshness_kind == "kp":
        fresh_provider = now <= provider_time + timedelta(hours=3, minutes=30)
        diagnostic = (
            fetch_age <= timedelta(hours=6)
            and now <= provider_time + timedelta(hours=6)
        )
    else:
        fresh_provider = provider_age <= timedelta(minutes=30)
        diagnostic = (
            fetch_age <= timedelta(minutes=60)
            and provider_age <= timedelta(minutes=60)
        )

    if fetch_age <= _FRESH_FETCH_AGE and fresh_provider:
        return "live" if live else "fresh_cache"
    if diagnostic:
        return "stale_cache"
    return "unavailable"


def _serialize_envelope(envelope: SourceEnvelope) -> dict[str, Any]:
    return {
        "schema": envelope.schema,
        "endpoint": envelope.endpoint,
        "fetched_at_utc": _format_utc(envelope.fetched_at_utc),
        "observed_at_utc": _optional_utc(envelope.observed_at_utc),
        "issued_at_utc": _optional_utc(envelope.issued_at_utc),
        "valid_from_utc": _optional_utc(envelope.valid_from_utc),
        "valid_until_utc": _optional_utc(envelope.valid_until_utc),
        "payload": _json_value(envelope.payload),
        "raw_digest": envelope.raw_digest,
    }


def _read_envelope(
    path: Path,
    *,
    expected_endpoint: str,
    freshness_kind: FreshnessKind,
) -> SourceEnvelope | None:
    try:
        with path.open("rb") as stream:
            encoded = stream.read(MAX_SOURCE_CACHE_BYTES + 1)
        if len(encoded) > MAX_SOURCE_CACHE_BYTES:
            return None
        raw = json.loads(encoded)
        if not isinstance(raw, Mapping):
            return None
        if raw.get("schema") != SOURCE_CACHE_SCHEMA:
            return None
        if raw.get("endpoint") != expected_endpoint:
            return None
        digest = raw.get("raw_digest")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return None
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            return None
        envelope = SourceEnvelope(
            schema=SOURCE_CACHE_SCHEMA,
            endpoint=expected_endpoint,
            fetched_at_utc=_parse_utc(raw["fetched_at_utc"]),
            observed_at_utc=_parse_optional_utc(raw.get("observed_at_utc")),
            issued_at_utc=_parse_optional_utc(raw.get("issued_at_utc")),
            valid_from_utc=_parse_optional_utc(raw.get("valid_from_utc")),
            valid_until_utc=_parse_optional_utc(raw.get("valid_until_utc")),
            payload=_freeze(payload),
            raw_digest=digest,
        )
        _validate_cached_payload(envelope, freshness_kind=freshness_kind)
        return envelope
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _validate_cached_payload(
    envelope: SourceEnvelope,
    *,
    freshness_kind: FreshnessKind,
) -> None:
    payload = envelope.payload
    if freshness_kind == "alerts":
        candidates = _required_sequence(payload, "candidates")
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                raise ValueError("cached alert candidate must be an object")
            if candidate.get("kind") not in {"ALERT", "WARNING", "WATCH", "SUMMARY"}:
                raise ValueError("cached alert kind is invalid")
            _parse_utc(candidate.get("issue_datetime"))
            _parse_utc(candidate.get("display_until"))
        return
    if freshness_kind == "donki":
        flr = _required_sequence(payload, "flr")
        cme = _required_sequence(payload, "cme")
        if not all(isinstance(row, Mapping) for row in (*flr, *cme)):
            raise ValueError("cached DONKI row must be an object")
        return

    observed = _parse_utc(payload["observed_at_utc"])
    if observed != envelope.observed_at_utc:
        raise ValueError("cached provider time does not match normalized payload")

    if freshness_kind == "scales":
        _validate_cached_scales(payload)
        return
    if freshness_kind == "kp":
        _validate_cached_kp(payload)
        return
    if envelope.endpoint == WIND_SPEED_ENDPOINT:
        speed = _finite_number(payload["speed_km_s"], label="solar-wind speed")
        if speed < 0:
            raise ValueError("cached solar-wind speed must not be negative")
        return
    if envelope.endpoint == WIND_MAG_ENDPOINT:
        _finite_number(payload["bt_nt"], label="Bt")
        bz = _finite_number(payload["bz_gsm_nt"], label="Bz")
        direction = payload["bz_direction"]
        expected = "south" if bz < 0 else "north" if bz > 0 else "neutral"
        if direction != expected:
            raise ValueError("cached Bz direction does not match its sign")
        return
    raise ValueError("cached wind source endpoint is unknown")


def _validate_cached_scales(payload: Mapping[str, Any]) -> None:
    current = _required_mapping(payload, "current")
    for key in ("g", "r", "s"):
        _cached_scale(current[key], required=True)

    forecast = _required_sequence(payload, "forecast_g")
    forecast_times = []
    for item in forecast:
        if not isinstance(item, Mapping):
            raise ValueError("cached scales forecast row must be an object")
        forecast_times.append(_parse_utc(item["valid_at_utc"]))
        _cached_scale(item["g"], required=False)
    if not forecast_times or forecast_times != sorted(forecast_times):
        raise ValueError("cached scales forecast must be date ordered")

    timeline = _required_sequence(payload, "timeline")
    product_keys = []
    for item in timeline:
        if not isinstance(item, Mapping):
            raise ValueError("cached scales timeline row must be an object")
        product_keys.append(item["product_key"])
        _parse_utc(item["valid_at_utc"])
        for key in ("g", "r", "s"):
            _cached_scale(item[key], required=item["product_key"] in {"-1", "0"})
    if set(product_keys) != {"-1", "0", "1", "2", "3"}:
        raise ValueError("cached scales timeline has invalid product keys")

    probabilities = payload["probabilities"]
    if probabilities is not None:
        if not isinstance(probabilities, Mapping):
            raise ValueError("cached scales probabilities must be an object or null")
        _parse_utc(probabilities["valid_at_utc"])
        for key in ("r_minor", "r_major", "s"):
            _probability(probabilities[key])


def _validate_cached_kp(payload: Mapping[str, Any]) -> None:
    current = _required_mapping(payload, "current")
    _validate_cached_kp_row(current, predicted=False)
    if _parse_utc(current["time_tag"]) != _parse_utc(payload["observed_at_utc"]):
        raise ValueError("cached current Kp time does not match provider time")

    forecast = _required_sequence(payload, "forecast_48h")
    forecast_times = []
    for row in forecast:
        if not isinstance(row, Mapping):
            raise ValueError("cached Kp forecast row must be an object")
        _validate_cached_kp_row(row, predicted=True)
        forecast_times.append(_parse_utc(row["time_tag"]))
    if forecast_times != sorted(forecast_times):
        raise ValueError("cached Kp forecast must be date ordered")

    peak = payload["predicted_peak"]
    if peak is not None:
        if not isinstance(peak, Mapping):
            raise ValueError("cached Kp predicted peak must be an object or null")
        _validate_cached_kp_row(peak, predicted=True)


def _validate_cached_kp_row(row: Mapping[str, Any], *, predicted: bool) -> None:
    _parse_utc(row["time_tag"])
    kp = _finite_number(row["kp"], label="Kp")
    if not 0 <= kp <= 9:
        raise ValueError("cached Kp must be between 0 and 9")
    expected_states = {"predicted"} if predicted else {"observed", "estimated"}
    if row["observed"] not in expected_states:
        raise ValueError("cached Kp row has an invalid observation state")
    scale = row["noaa_scale"]
    if scale is not None and not isinstance(scale, str):
        raise ValueError("cached Kp noaa_scale must be a string or null")


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload[key]
    if not isinstance(value, Mapping):
        raise ValueError(f"cached {key} must be an object")
    return value


def _required_sequence(payload: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = payload[key]
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"cached {key} must be an array")
    return value


def _cached_scale(value: Any, *, required: bool) -> int | None:
    if value is None:
        if required:
            raise ValueError("cached NOAA scale is missing")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5:
        raise ValueError("cached NOAA scale must be an integer from 0 to 5")
    return value


def _kp_object_rows(raw: Sequence[Any]) -> list[Mapping[str, Any]]:
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise ValueError("Kp payload must be an array")
    if not raw:
        raise ValueError("Kp payload is empty")
    if all(isinstance(row, Mapping) for row in raw):
        return list(raw)
    header = raw[0]
    if isinstance(header, (str, bytes, bytearray)) or not isinstance(header, Sequence):
        raise ValueError("Kp legacy payload has no header row")
    columns = [str(column) for column in header]
    required = {"time_tag", "kp", "observed", "noaa_scale"}
    if not required.issubset(columns):
        raise ValueError("Kp legacy header is missing required columns")
    result = []
    for values in raw[1:]:
        if isinstance(values, (str, bytes, bytearray)) or not isinstance(
            values, Sequence
        ):
            raise ValueError("Kp legacy row must be an array")
        if len(values) != len(columns):
            raise ValueError("Kp legacy row width does not match its header")
        result.append(dict(zip(columns, values)))
    if not result:
        raise ValueError("Kp payload has no data rows")
    return result


def _public_kp_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("time_tag", "kp", "observed", "noaa_scale")}


def _newest_timed_row(
    raw: Sequence[Mapping[str, Any]], *, now_utc: datetime
) -> tuple[Mapping[str, Any], datetime]:
    now = _as_utc(now_utc)
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise ValueError("solar-wind payload must be an array")
    candidates = []
    for row in raw:
        if not isinstance(row, Mapping):
            raise ValueError("solar-wind row must be an object")
        observed_at = _parse_utc(row.get("time_tag"))
        if observed_at <= now + _FUTURE_CLOCK_SKEW:
            candidates.append((observed_at, row))
    if not candidates:
        raise ValueError("solar-wind payload has no current observation")
    observed_at, row = max(candidates, key=lambda item: item[0])
    return row, observed_at


def _scale_value(entry: Mapping[str, Any], key: str, *, required: bool) -> int | None:
    section = _nested(entry, key)
    raw_value = section.get("Scale")
    if raw_value is None or str(raw_value).strip() == "":
        if required:
            raise ValueError(f"NOAA {key} scale is missing")
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"NOAA {key} scale is invalid") from None
    if not 0 <= value <= 5:
        raise ValueError(f"NOAA {key} scale must be between 0 and 5")
    return value


def _nested(entry: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = entry.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"NOAA scales section {key} is invalid")
    return value


def _probability(value: Any) -> int:
    try:
        probability = int(value)
    except (TypeError, ValueError):
        raise ValueError("NOAA probability is invalid") from None
    if not 0 <= probability <= 100:
        raise ValueError("NOAA probability must be between 0 and 100")
    return probability


def _finite_number(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be numeric") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _parse_utc_parts(date_value: Any, time_value: Any) -> datetime:
    if not isinstance(date_value, str) or not isinstance(time_value, str):
        raise ValueError("NOAA provider date and time must be strings")
    return _parse_utc(f"{date_value.strip()}T{time_value.strip()}")


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("provider timestamp must be a non-empty string")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise ValueError("provider timestamp is not valid ISO-8601") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_optional_utc(value: Any) -> datetime | None:
    return None if value is None else _parse_utc(value)


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("now_utc must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _optional_utc(value: datetime | None) -> str | None:
    return None if value is None else _format_utc(value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _error_text(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__
