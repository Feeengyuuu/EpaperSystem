"""Shared validation for playlist instance refresh configuration."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from runtime.refresh_contracts import freeze_payload
from utils.time_utils import calculate_seconds


_SCHEDULED_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_INTERVAL_UNITS = frozenset({"second", "minute", "hour", "day"})


class RefreshValidationError(ValueError):
    """A client-safe refresh configuration validation failure."""

    def __init__(self, message: str, *, error_code: str = "invalid_refresh_config"):
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class ParsedRefreshConfig:
    """Detached request data and normalized persisted refresh settings."""

    request: Mapping[str, Any]
    refresh: Mapping[str, Any]


def parse_refresh_config(value: str | Mapping[str, Any]) -> ParsedRefreshConfig:
    """Parse and validate refresh settings before any model mutation."""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RefreshValidationError("Refresh configuration is not valid JSON") from exc
    elif isinstance(value, Mapping):
        decoded = dict(value)
    else:
        raise RefreshValidationError("Refresh configuration must be an object")

    if not isinstance(decoded, dict):
        raise RefreshValidationError("Refresh configuration must be an object")

    refresh_type = decoded.get("refreshType")
    if refresh_type not in {"interval", "scheduled"}:
        raise RefreshValidationError("Refresh type must be interval or scheduled")

    if refresh_type == "interval":
        unit = decoded.get("unit")
        if unit not in _INTERVAL_UNITS:
            raise RefreshValidationError("Refresh interval unit is invalid")

        interval = decoded.get("interval")
        if isinstance(interval, bool):
            raise RefreshValidationError("Refresh interval must be a positive integer")
        if isinstance(interval, int):
            normalized_interval = interval
        elif isinstance(interval, str) and re.fullmatch(r"[0-9]+", interval.strip()):
            normalized_interval = int(interval)
        else:
            raise RefreshValidationError("Refresh interval must be a positive integer")
        if normalized_interval <= 0:
            raise RefreshValidationError("Refresh interval must be a positive integer")

        refresh = {"interval": calculate_seconds(normalized_interval, unit)}
    else:
        refresh_time = decoded.get("refreshTime")
        if not isinstance(refresh_time, str) or not _SCHEDULED_TIME.fullmatch(
            refresh_time
        ):
            raise RefreshValidationError("Refresh time must use 24-hour HH:MM format")
        refresh = {"scheduled": refresh_time}

    return ParsedRefreshConfig(
        request=freeze_payload(decoded),
        refresh=freeze_payload(refresh),
    )


def validation_error_payload(error: RefreshValidationError) -> dict[str, Any]:
    """Return the stable JSON shape expected by existing and newer clients."""
    message = str(error)
    return {
        "success": False,
        "error_code": error.error_code,
        "error": message,
        "message": message,
    }
