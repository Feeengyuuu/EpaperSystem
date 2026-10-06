"""Bounded quiet windows that let one starved renderer reacquire resources.

A heavyweight background refresh can starve indefinitely when ordinary work
keeps memory below its start margin. A quiet window holds other independent
work for a bounded time so that one overdue instance can start, then enters a
cooldown if the margin never appears. The coordinator decides *which*
instances are starved; this component owns only the window lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import logging
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

WINDOW_MAX_SECONDS = 5 * 60
COOLDOWN_MAX_SECONDS = 60 * 60


@dataclass(frozen=True)
class LivenessWindow:
    instance_uuid: str
    due_since: datetime
    started_monotonic: float
    deadline_monotonic: float


@dataclass(frozen=True)
class LivenessSettings:
    label: str
    reserve_subject: str
    window_key: str
    window_default: float
    cooldown_key: str
    cooldown_default: float


class LivenessOutcome(Enum):
    TARGET_LEFT = "target_left"
    NO_LONGER_DUE = "no_longer_due"
    EXPIRED = "expired"
    RUN_WINDOW_TARGET = "run_window_target"
    HOLD = "hold"
    RUN_TARGET = "run_target"
    COOLDOWN = "cooldown"
    NO_TARGET = "no_target"
    DISABLED = "disabled"
    STARTED = "started"


_HOLDING = frozenset({LivenessOutcome.HOLD, LivenessOutcome.STARTED})


@dataclass(frozen=True)
class LivenessDecision:
    outcome: LivenessOutcome
    target: Any = None
    instance_uuid: str | None = None

    @property
    def holds_independent(self) -> bool:
        return self.outcome in _HOLDING


def _uuid_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class StarvationLiveness:
    """Own one plugin's quiet window and cooldown across scheduler turns."""

    def __init__(
        self,
        settings: LivenessSettings,
        *,
        clock: Callable[[], float],
        seconds: Callable[[str, float, float], float],
    ):
        self.settings = settings
        self._clock = clock
        self._seconds = seconds
        self.window: LivenessWindow | None = None
        self.cooldown_until_monotonic = 0.0

    def decide(
        self,
        *,
        current_dt: datetime,
        active_uuids: frozenset[str],
        candidates_by_uuid: Mapping[str, Any],
        retry_pending: Callable[[str], bool],
        margin_available: bool,
        select_target: Callable[[], tuple[Any, datetime | None]],
        resource_sample: Any,
        required_mb: float,
        max_swap: float,
        before_window: Callable[[], None] | None = None,
    ) -> LivenessDecision:
        now = self._clock()
        if self.window is not None:
            return self._continue_window(
                now, active_uuids, candidates_by_uuid, retry_pending,
                margin_available, resource_sample,
            )
        target, due_since = select_target()
        if target is not None and margin_available:
            return LivenessDecision(LivenessOutcome.RUN_TARGET, target)
        if not margin_available and now < self.cooldown_until_monotonic:
            return LivenessDecision(LivenessOutcome.COOLDOWN)
        if target is None or due_since is None:
            return LivenessDecision(LivenessOutcome.NO_TARGET)
        return self._start_window(
            now, current_dt, target, due_since, resource_sample, required_mb, max_swap, before_window,
        )

    def _continue_window(
        self,
        now: float,
        active_uuids: frozenset[str],
        candidates_by_uuid: Mapping[str, Any],
        retry_pending: Callable[[str], bool],
        margin_available: bool,
        resource_sample: Any,
    ) -> LivenessDecision:
        window = self.window
        assert window is not None
        label = self.settings.label
        if window.instance_uuid not in active_uuids:
            logger.info(
                "Canceling %s quiet window because its target left the active "
                "playlist. | instance_uuid_hash: %s",
                label, _uuid_hash(window.instance_uuid),
            )
            self.window = None
            return LivenessDecision(LivenessOutcome.TARGET_LEFT, instance_uuid=window.instance_uuid)
        target = candidates_by_uuid.get(window.instance_uuid)
        if target is None and not retry_pending(window.instance_uuid):
            logger.info(
                "Canceling %s quiet window because its target is no longer "
                "due. | instance_uuid_hash: %s",
                label, _uuid_hash(window.instance_uuid),
            )
            self.window = None
            return LivenessDecision(LivenessOutcome.NO_LONGER_DUE, instance_uuid=window.instance_uuid)
        if now >= window.deadline_monotonic:
            cooldown_seconds = self._seconds(
                self.settings.cooldown_key, self.settings.cooldown_default, COOLDOWN_MAX_SECONDS,
            )
            self.window = None
            self.cooldown_until_monotonic = now + cooldown_seconds
            logger.warning(
                "%s quiet window expired before a refresh completed; ordinary "
                "refreshes resume. | instance_uuid_hash: %s | window_seconds: %.1f | "
                "cooldown_seconds: %.1f | available_mb: %s | swap_percent: %s",
                label,
                _uuid_hash(window.instance_uuid),
                max(0.0, window.deadline_monotonic - window.started_monotonic),
                cooldown_seconds,
                resource_sample.available_mb,
                resource_sample.swap_percent,
            )
            return LivenessDecision(LivenessOutcome.EXPIRED, instance_uuid=window.instance_uuid)
        if target is not None and margin_available:
            # Keep the window until execution has actually completed. Execution
            # samples resources again; retaining the original deadline makes a
            # scheduler/execution margin race expire into cooldown instead of
            # silently starting over.
            return LivenessDecision(LivenessOutcome.RUN_WINDOW_TARGET, target)
        return LivenessDecision(LivenessOutcome.HOLD)

    def _start_window(
        self,
        now: float,
        current_dt: datetime,
        target: Any,
        due_since: datetime,
        resource_sample: Any,
        required_mb: float,
        max_swap: float,
        before_window: Callable[[], None] | None,
    ) -> LivenessDecision:
        window_seconds = self._seconds(
            self.settings.window_key, self.settings.window_default, WINDOW_MAX_SECONDS,
        )
        if window_seconds <= 0:
            return LivenessDecision(LivenessOutcome.DISABLED)
        if before_window is not None:
            before_window()
        instance_uuid = target.instance.instance_uuid
        self.window = LivenessWindow(
            instance_uuid=instance_uuid,
            due_since=due_since,
            started_monotonic=now,
            deadline_monotonic=now + window_seconds,
        )
        logger.warning(
            "Reserving bounded quiet window for starved %s. | instance_uuid_hash: %s | "
            "overdue_seconds: %.1f | window_seconds: %.1f | available_mb: %s | "
            "swap_percent: %s | required_available_mb: %s | max_swap_percent: %s",
            self.settings.reserve_subject,
            _uuid_hash(instance_uuid),
            max(0.0, (current_dt - due_since).total_seconds()),
            window_seconds,
            resource_sample.available_mb,
            resource_sample.swap_percent,
            required_mb,
            max_swap,
        )
        return LivenessDecision(LivenessOutcome.STARTED, instance_uuid=instance_uuid)
