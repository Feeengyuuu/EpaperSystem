from dataclasses import replace
from types import MappingProxyType

import pytest

from runtime.ian import IanPriority, IanStageKind
from runtime.ian_refresh_adapter import refresh_command_to_ian_request
from runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    RefreshCommand,
    RefreshIntent,
)


def _command(
    *,
    payload,
    kind=CommandKind.CACHE_REFRESH,
    source=CommandSource.BACKGROUND,
    plugin_id="weather",
    instance_uuid="weather-instance",
    structural_generation=3,
    settings_revision=7,
    force=False,
    priority=10,
    deadline_monotonic=100.0,
    idempotency_key=None,
    intent=RefreshIntent.DATA_REFRESH,
    coalescing_scope=None,
    allow_prepared_presentation=False,
):
    return RefreshCommand.create(
        kind=kind,
        source=source,
        plugin_id=plugin_id,
        instance_uuid=instance_uuid,
        structural_generation=structural_generation,
        settings_revision=settings_revision,
        payload=payload,
        now_monotonic=10.0,
        deadline_monotonic=deadline_monotonic,
        force=force,
        priority=priority,
        idempotency_key=idempotency_key,
        intent=intent,
        coalescing_scope=coalescing_scope,
        allow_prepared_presentation=allow_prepared_presentation,
    )


def test_refresh_command_adapter_preserves_identity_and_canonicalizes_mapping_order():
    first = _command(
        payload={
            "playlist_name": "Daily",
            "settings": {"units": "metric", "cities": ("London", "Tokyo")},
        }
    )
    reordered = _command(
        payload={
            "settings": {"cities": ("London", "Tokyo"), "units": "metric"},
            "playlist_name": "Daily",
        }
    )

    request = refresh_command_to_ian_request(first)
    reordered_request = refresh_command_to_ian_request(reordered)

    assert request.request_id == first.id
    assert request.plugin_id == first.plugin_id
    assert request.instance_uuid == first.instance_uuid
    assert request.structural_generation == first.structural_generation
    assert request.settings_revision == first.settings_revision
    assert request.intent == RefreshIntent.DATA_REFRESH.value
    assert request.deadline_monotonic == first.deadline_monotonic
    assert request.priority is IanPriority.BACKGROUND
    assert request.payload is first.payload
    assert len(request.stages) == 1
    assert request.stages[0].name == "legacy_execute"
    assert request.stages[0].kind is IanStageKind.RENDER
    assert request.stages[0].claim is None
    assert request.plan_token == reordered_request.plan_token


def test_priority_and_deadline_only_changes_retain_the_same_plan_token():
    original = refresh_command_to_ian_request(
        _command(payload={"playlist_name": "Daily"}, priority=10, deadline_monotonic=100)
    )
    more_urgent = refresh_command_to_ian_request(
        _command(payload={"playlist_name": "Daily"}, priority=97, deadline_monotonic=25)
    )

    assert original.plan_token == more_urgent.plan_token
    assert original.deadline_monotonic == 100
    assert more_urgent.deadline_monotonic == 25


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (CommandSource.MANUAL, IanPriority.MANUAL),
        (CommandSource.LIVE, IanPriority.LIVE),
        (CommandSource.SCHEDULER, IanPriority.SCHEDULED),
        (CommandSource.BACKGROUND, IanPriority.BACKGROUND),
    ],
)
def test_non_display_commands_map_source_to_semantic_priority(source, expected):
    request = refresh_command_to_ian_request(
        _command(payload={"playlist_name": "Daily"}, source=source)
    )

    assert request.priority is expected


@pytest.mark.parametrize(
    "change",
    [
        {"kind": CommandKind.DISPLAY},
        {"source": CommandSource.SCHEDULER},
        {"plugin_id": "sports_dashboard"},
        {"instance_uuid": "replacement-instance"},
        {"structural_generation": 4},
        {"settings_revision": 8},
        {"intent": RefreshIntent.LIVE_REFRESH},
        {"force": True},
        {"idempotency_key": "request-key"},
        {"coalescing_scope": "presentation-followup:abc"},
        {"allow_prepared_presentation": True},
        {"payload": {"playlist_name": "Evening"}},
    ],
)
def test_every_execution_or_cas_semantic_change_restarts_the_plan(change):
    values = {
        "payload": {"playlist_name": "Daily"},
        "idempotency_key": None,
        "coalescing_scope": None,
        "allow_prepared_presentation": False,
    }
    original = refresh_command_to_ian_request(_command(**values))
    changed = refresh_command_to_ian_request(_command(**(values | change)))

    assert changed.plan_token != original.plan_token


def test_display_cache_is_a_zero_claim_pressure_safe_display_stage():
    command = _command(
        payload={"playlist_name": "Daily", "display_cached_only": True},
        kind=CommandKind.DISPLAY,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DISPLAY_CACHE,
    )

    request = refresh_command_to_ian_request(command)

    assert request.priority is IanPriority.DISPLAY
    assert request.stages[0].kind is IanStageKind.DISPLAY
    assert request.stages[0].claim.memory_mb == 0
    assert request.stages[0].claim.swap_growth_percent == 0
    assert request.stages[0].claim.pressure_safe is True


@pytest.mark.parametrize(
    "intent",
    [RefreshIntent.DATA_REFRESH, RefreshIntent.LIVE_REFRESH],
)
def test_isolated_sports_refresh_reserves_45_mb_above_the_70_mb_floor(intent):
    command = _command(
        payload={"playlist_name": "Daily"},
        plugin_id="sports_dashboard",
        kind=CommandKind.CACHE_REFRESH,
        intent=intent,
    )

    request = refresh_command_to_ian_request(command)

    assert request.stages[0].kind is IanStageKind.RENDER
    assert request.stages[0].claim.memory_mb == 45
    assert request.stages[0].claim.swap_growth_percent == 0
    assert request.stages[0].claim.pressure_safe is False


def test_nonisolated_sports_and_other_renderers_use_conservative_unknown_claim():
    manual_sports = _command(
        payload={"settings": {}},
        plugin_id="sports_dashboard",
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        instance_uuid=None,
        structural_generation=None,
        settings_revision=None,
        intent=RefreshIntent.MANUAL_RENDER,
    )
    ordinary = _command(payload={"playlist_name": "Daily"})

    assert refresh_command_to_ian_request(manual_sports).stages[0].claim is None
    assert refresh_command_to_ian_request(ordinary).stages[0].claim is None


def test_display_cache_pressure_bypass_requires_a_display_command():
    mismatched = _command(
        payload={"playlist_name": "Daily"},
        kind=CommandKind.CACHE_REFRESH,
        intent=RefreshIntent.DISPLAY_CACHE,
    )

    with pytest.raises(ValueError, match="DISPLAY_CACHE.*DISPLAY"):
        refresh_command_to_ian_request(mismatched)


def test_bytes_complex_numbers_and_frozen_sets_have_deterministic_tokens():
    first = _command(
        payload={
            "blob": b"\x00\xff",
            "coordinate": complex(1.25, -2.5),
            "labels": {("beta", 2), ("alpha", 1)},
        }
    )
    reordered = _command(
        payload={
            "labels": {("alpha", 1), ("beta", 2)},
            "coordinate": complex(1.25, -2.5),
            "blob": b"\x00\xff",
        }
    )

    assert (
        refresh_command_to_ian_request(first).plan_token
        == refresh_command_to_ian_request(reordered).plan_token
    )


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        complex(float("inf"), 0),
        complex(0, float("nan")),
    ],
)
def test_nonfinite_payload_numbers_are_rejected(value):
    with pytest.raises(ValueError, match="non-finite"):
        refresh_command_to_ian_request(_command(payload={"value": value}))


def test_unsupported_payload_type_is_rejected_without_rendering_its_value():
    secret = "DO-NOT-LEAK-UNSUPPORTED"

    class Opaque:
        def __repr__(self):
            return secret

    command = replace(
        _command(payload={}),
        payload=MappingProxyType({"opaque": Opaque()}),
    )

    with pytest.raises(TypeError) as captured:
        refresh_command_to_ian_request(command)

    assert secret not in str(captured.value)


def test_secret_payload_values_are_absent_from_request_repr_and_logs(caplog):
    secret = "DO-NOT-LEAK-API-KEY"

    request = refresh_command_to_ian_request(
        _command(payload={"settings": {"api_key": secret}})
    )

    assert secret not in repr(request)
    assert secret not in caplog.text
