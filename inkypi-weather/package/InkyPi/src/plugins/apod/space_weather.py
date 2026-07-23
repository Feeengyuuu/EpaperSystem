"""Pure NOAA space-weather normalization and independent source caches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

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

SOURCE_CACHE_SCHEMA = 1
NOAA_TIMEOUT_SECONDS = 20
MAX_PROVIDER_JSON_BYTES = 2 * 1024 * 1024
MAX_SOURCE_CACHE_BYTES = 2 * 1024 * 1024
_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
_FRESH_FETCH_AGE = timedelta(minutes=30)

SourceState = Literal["live", "fresh_cache", "stale_cache", "unavailable"]


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


def normalize_scales(
    raw: Mapping[str, Any], *, now_utc: datetime
) -> Mapping[str, Any]:
    """Normalize NOAA's keyed yesterday/current/three-day scales product."""

    now = _as_utc(now_utc)
    if not isinstance(raw, Mapping):
        raise ValueError("NOAA scales payload must be an object")

    timeline = []
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
        timeline.append(normalized_entry)
        entries[product_key] = (provider_time, entry)

    observed_at = entries["0"][0]
    if observed_at > now + _FUTURE_CLOCK_SKEW:
        raise ValueError("NOAA scales provider time is unexpectedly in the future")

    current = timeline[1]
    probabilities = None
    for product_key in ("1", "2", "3"):
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
                "valid_at_utc": timeline[index]["valid_at_utc"],
                "g": timeline[index]["g"],
            }
            for index in range(2, 5)
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

    def _refresh_one(
        self,
        *,
        name: str,
        endpoint: str,
        filename: str,
        normalizer,
        freshness_kind: Literal["scales", "kp", "wind"],
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
            atomic_write_json(path, _serialize_envelope(envelope))
        except Exception as error:
            cached = _read_envelope(path, expected_endpoint=endpoint)
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

        state = _classify_freshness(
            envelope,
            now_utc=now_utc,
            freshness_kind=freshness_kind,
            live=True,
        )
        if state == "unavailable":
            return SourceResult(
                name=name,
                state=state,
                envelope=None,
                error=f"{name} provider data is outside its diagnostic age window",
            )
        return SourceResult(
            name=name,
            state=state,
            envelope=envelope,
            error=(f"{name} provider data is stale" if state == "stale_cache" else None),
        )


def _build_envelope(
    *,
    endpoint: str,
    fetched_at_utc: datetime,
    payload: Mapping[str, Any],
    raw_digest: str,
    freshness_kind: Literal["scales", "kp", "wind"],
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
    freshness_kind: Literal["scales", "kp", "wind"],
    live: bool = False,
) -> SourceState:
    now = _as_utc(now_utc)
    provider_time = envelope.observed_at_utc or envelope.issued_at_utc
    if provider_time is None or provider_time > now + _FUTURE_CLOCK_SKEW:
        return "unavailable"
    fetch_age = now - envelope.fetched_at_utc
    provider_age = now - provider_time
    if fetch_age < timedelta(0):
        return "unavailable"

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


def _read_envelope(path: Path, *, expected_endpoint: str) -> SourceEnvelope | None:
    try:
        if path.stat().st_size > MAX_SOURCE_CACHE_BYTES:
            return None
        encoded = path.read_bytes()
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
        return SourceEnvelope(
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
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


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
