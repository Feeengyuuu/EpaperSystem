from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from runtime.liveness_window import (
    LivenessOutcome,
    LivenessSettings,
    LivenessWindow,
    StarvationLiveness,
)


NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
SETTINGS = LivenessSettings(
    label="Example",
    reserve_subject="Example data",
    window_key="example_window_seconds",
    window_default=120.0,
    cooldown_key="example_cooldown_seconds",
    cooldown_default=600.0,
)


CANDIDATE_A = SimpleNamespace(instance=SimpleNamespace(instance_uuid="a"))


class Sample:
    available_mb = 90.0
    swap_percent = 40.0


def _liveness(clock, seconds=None):
    def default_seconds(key, default, maximum):
        return min(float(maximum), float(default))

    return StarvationLiveness(SETTINGS, clock=lambda: clock[0], seconds=seconds or default_seconds)


def _decide(liveness, **overrides):
    arguments = {
        "current_dt": NOW,
        "active_uuids": frozenset({"a", "b"}),
        "candidates_by_uuid": {"a": CANDIDATE_A},
        "retry_pending": lambda _uuid: False,
        "margin_available": False,
        "select_target": lambda: (CANDIDATE_A, NOW - timedelta(hours=3)),
        "resource_sample": Sample(),
        "required_mb": 115.0,
        "max_swap": 85.0,
    }
    arguments.update(overrides)
    return liveness.decide(**arguments)


def test_runnable_target_runs_without_reserving_a_window():
    liveness = _liveness([10.0])

    decision = _decide(liveness, margin_available=True)

    assert decision.outcome is LivenessOutcome.RUN_TARGET
    assert decision.target is CANDIDATE_A
    assert not decision.holds_independent
    assert liveness.window is None


def test_starved_target_without_margin_reserves_one_bounded_window():
    clock = [10.0]
    maintenance = []
    liveness = _liveness(clock)

    decision = _decide(liveness, before_window=lambda: maintenance.append("run"))

    assert decision.outcome is LivenessOutcome.STARTED
    assert decision.holds_independent
    assert maintenance == ["run"]
    assert liveness.window == LivenessWindow(
        instance_uuid="a",
        due_since=NOW - timedelta(hours=3),
        started_monotonic=10.0,
        deadline_monotonic=130.0,
    )


def test_open_window_holds_until_margin_then_runs_its_target():
    clock = [10.0]
    liveness = _liveness(clock)
    _decide(liveness)
    clock[0] = 50.0

    held = _decide(liveness)
    ran = _decide(liveness, margin_available=True)

    assert held.outcome is LivenessOutcome.HOLD and held.holds_independent
    assert ran.outcome is LivenessOutcome.RUN_WINDOW_TARGET and ran.target is CANDIDATE_A
    assert liveness.window is not None


def test_expired_window_enters_cooldown_and_cooldown_blocks_a_new_window():
    clock = [10.0]
    liveness = _liveness(clock)
    _decide(liveness)
    clock[0] = 131.0

    expired = _decide(liveness)
    cooling = _decide(liveness)

    assert expired.outcome is LivenessOutcome.EXPIRED
    assert liveness.window is None
    assert liveness.cooldown_until_monotonic == 731.0
    assert cooling.outcome is LivenessOutcome.COOLDOWN
    clock[0] = 800.0
    assert _decide(liveness).outcome is LivenessOutcome.STARTED


def test_window_cancels_when_target_leaves_playlist():
    clock = [10.0]
    liveness = _liveness(clock)
    _decide(liveness)

    decision = _decide(liveness, active_uuids=frozenset({"b"}))

    assert decision.outcome is LivenessOutcome.TARGET_LEFT
    assert decision.instance_uuid == "a"
    assert liveness.window is None
    assert liveness.cooldown_until_monotonic == 0.0


def test_window_survives_pending_retry_but_cancels_when_no_longer_due():
    clock = [10.0]
    liveness = _liveness(clock)
    _decide(liveness)

    waiting = _decide(liveness, candidates_by_uuid={}, retry_pending=lambda uuid: uuid == "a")
    canceled = _decide(liveness, candidates_by_uuid={})

    assert waiting.outcome is LivenessOutcome.HOLD
    assert canceled.outcome is LivenessOutcome.NO_LONGER_DUE
    assert canceled.instance_uuid == "a"
    assert liveness.window is None


def test_no_target_or_disabled_window_never_holds_other_work():
    liveness = _liveness([10.0])
    assert _decide(liveness, select_target=lambda: (None, None)).outcome is LivenessOutcome.NO_TARGET

    disabled = _liveness([10.0], seconds=lambda key, default, maximum: 0.0)
    decision = _decide(disabled, before_window=lambda: (_ for _ in ()).throw(AssertionError))
    assert decision.outcome is LivenessOutcome.DISABLED
    assert not decision.holds_independent
    assert disabled.window is None


def test_configured_window_and_cooldown_are_capped():
    requested = []

    def seconds(key, default, maximum):
        requested.append((key, default, maximum))
        return maximum

    clock = [0.0]
    liveness = _liveness(clock, seconds=seconds)
    _decide(liveness)
    clock[0] = 10_000.0
    _decide(liveness)

    assert requested == [
        ("example_window_seconds", 120.0, 300.0),
        ("example_cooldown_seconds", 600.0, 3600.0),
    ]
