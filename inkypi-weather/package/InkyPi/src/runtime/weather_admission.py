"""Weather admission margins and recovery receipts, without device I/O."""

import math

from runtime.runtime_state import InstanceRuntimeState


class WeatherPressureRecovery:
    """Remember admission-only deferrals while unrelated work keeps running."""

    def __init__(self):
        self._receipts = {}

    def remember(self, candidate, state):
        self._receipts[candidate.instance.instance_uuid] = (candidate, state)

    def discard(self, instance_uuid):
        self._receipts.pop(instance_uuid, None)

    def select(self, active_instances, candidates, runtime_instances):
        # A new attempt, success, failure, retry change, or instance revision
        # invalidates recovery. The caller supplies candidates that have passed
        # due evaluation and normal resource/rotation admission filters.
        active_by_uuid = {item.instance_uuid: item for item in active_instances}
        for uuid, (deferred, state) in list(self._receipts.items()):
            current = active_by_uuid.get(uuid)
            if (
                current is None
                or current.structural_generation != deferred.instance.structural_generation
                or current.settings_revision != deferred.instance.settings_revision
                or runtime_instances.get(uuid, InstanceRuntimeState()).data != state
            ):
                self.discard(uuid)
        return next((
            candidate for candidate in candidates
            if candidate.instance.instance_uuid in self._receipts
        ), None)


def weather_start_margin(sample, *, min_available_mb=150, max_swap_percent=70):
    if not math.isfinite(min_available_mb) or min_available_mb < 0:
        min_available_mb = 150
    if not math.isfinite(max_swap_percent) or not 0 <= max_swap_percent <= 100:
        max_swap_percent = 70
    try:
        available_mb = float(sample.available_mb)
        swap_percent = float(sample.swap_percent)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False, min_available_mb, max_swap_percent
    return (
        math.isfinite(available_mb) and math.isfinite(swap_percent)
        and available_mb >= min_available_mb and swap_percent < max_swap_percent,
        min_available_mb, max_swap_percent,
    )
