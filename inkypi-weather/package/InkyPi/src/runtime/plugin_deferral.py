"""Typed, bounded retry deferral requested by a plugin data seam."""

from __future__ import annotations

import math
import re

from .refresh_contracts import TaskCancelled


MAX_PLUGIN_REFRESH_DEFERRAL_SECONDS = 24 * 60 * 60
_LOG_TOKEN_PATTERN = re.compile(r"[a-z0-9_]{1,64}\Z", re.ASCII)


class PluginRefreshDeferred(TaskCancelled):
    """Cancel one refresh without failure and retry after a bounded delay."""

    def __init__(self, *, reason, phase, minimum_seconds):
        canonical_reason = _validated_log_token(reason, "reason")
        canonical_phase = _validated_log_token(phase, "phase")
        try:
            canonical_minimum = float(minimum_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("plugin deferral minimum_seconds is invalid") from exc
        if (
            not math.isfinite(canonical_minimum)
            or canonical_minimum <= 0
            or canonical_minimum > MAX_PLUGIN_REFRESH_DEFERRAL_SECONDS
        ):
            raise ValueError("plugin deferral minimum_seconds is outside its safe range")
        self.reason = canonical_reason
        self.phase = canonical_phase
        self.minimum_seconds = canonical_minimum
        super().__init__(
            f"{self.reason} during {self.phase}; retry after at least "
            f"{self.minimum_seconds:g} seconds"
        )


def _validated_log_token(value, field):
    if not isinstance(value, str) or _LOG_TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"plugin deferral {field} must be a safe machine token")
    return value
