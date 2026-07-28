"""Adapt immutable refresh commands to Ian's staged-work contract."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math

from .ian import (
    IanPriority,
    IanRequest,
    IanResourceClaim,
    IanStage,
    IanStageKind,
)
from .refresh_contracts import (
    CommandKind,
    CommandSource,
    RefreshCommand,
    RefreshIntent,
)

_IAN_REFRESH_ADAPTER_SCHEMA = 1


def refresh_command_to_ian_request(command: RefreshCommand) -> IanRequest:
    """Return one exact, legacy-stage Ian request for ``command``."""

    if not isinstance(command, RefreshCommand):
        raise TypeError("Ian refresh adapter requires a RefreshCommand")
    if command.intent is None:
        raise ValueError("Ian refresh adapter requires an explicit refresh intent")
    if (
        command.intent is RefreshIntent.DISPLAY_CACHE
        and command.kind is not CommandKind.DISPLAY
    ):
        raise ValueError(
            "Ian DISPLAY_CACHE pressure bypass requires a DISPLAY command"
        )

    semantic_plan = {
        "adapter_schema": _IAN_REFRESH_ADAPTER_SCHEMA,
        "kind": command.kind.value,
        "source": command.source.value,
        "plugin_id": command.plugin_id,
        "instance_uuid": command.instance_uuid,
        "structural_generation": command.structural_generation,
        "settings_revision": command.settings_revision,
        "intent": command.intent.value,
        "force": command.force,
        "idempotency_key": command.idempotency_key,
        "coalescing_scope": command.coalescing_scope,
        "allow_prepared_presentation": command.allow_prepared_presentation,
        "payload": command.payload,
    }
    canonical = _canonical_bytes(semantic_plan)
    plan_token = f"sha256:{hashlib.sha256(canonical).hexdigest()}"

    return IanRequest(
        request_id=command.id,
        plugin_id=command.plugin_id,
        instance_uuid=command.instance_uuid,
        structural_generation=command.structural_generation,
        settings_revision=command.settings_revision,
        intent=command.intent.value,
        plan_token=plan_token,
        priority=_ian_priority(command),
        deadline_monotonic=command.deadline_monotonic,
        stages=(_legacy_stage(command),),
        payload=command.payload,
    )


def _ian_priority(command: RefreshCommand) -> IanPriority:
    if command.intent is RefreshIntent.DISPLAY_CACHE:
        return IanPriority.DISPLAY
    return {
        CommandSource.MANUAL: IanPriority.MANUAL,
        CommandSource.LIVE: IanPriority.LIVE,
        CommandSource.SCHEDULER: IanPriority.SCHEDULED,
        CommandSource.BACKGROUND: IanPriority.BACKGROUND,
    }[command.source]


def _legacy_stage(command: RefreshCommand) -> IanStage:
    if command.intent is RefreshIntent.DISPLAY_CACHE:
        return IanStage(
            "legacy_execute",
            IanStageKind.DISPLAY,
            IanResourceClaim(
                memory_mb=0.0,
                swap_growth_percent=0.0,
                pressure_safe=True,
            ),
        )
    if (
        command.plugin_id == "sports_dashboard"
        and command.kind is CommandKind.CACHE_REFRESH
        and command.intent
        in {
            RefreshIntent.DATA_REFRESH,
            RefreshIntent.LIVE_REFRESH,
        }
        and bool(command.payload.get("playlist_name"))
    ):
        return IanStage(
            "legacy_execute",
            IanStageKind.RENDER,
            IanResourceClaim(memory_mb=45.0),
        )
    return IanStage("legacy_execute", IanStageKind.RENDER)


def _canonical_bytes(value: object) -> bytes:
    canonical = _canonical_value(value)
    return json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_value(value: object):
    value_type = type(value)
    if value is None:
        return ["none"]
    if value_type is bool:
        return ["bool", value]
    if value_type is int:
        return ["int", str(value)]
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError("Ian plan contains a non-finite float")
        return ["float", value.hex()]
    if value_type is complex:
        if not math.isfinite(value.real) or not math.isfinite(value.imag):
            raise ValueError("Ian plan contains a non-finite complex number")
        return ["complex", value.real.hex(), value.imag.hex()]
    if value_type is str:
        return ["str", value]
    if value_type is bytes:
        return ["bytes", value.hex()]
    if isinstance(value, Mapping):
        entries = [
            (_canonical_bytes(key), _canonical_value(key), _canonical_value(item))
            for key, item in value.items()
        ]
        entries.sort(key=lambda entry: entry[0])
        return ["mapping", [[key, item] for _encoded, key, item in entries]]
    if value_type is tuple:
        return ["tuple", [_canonical_value(item) for item in value]]
    if value_type is frozenset:
        items = [
            (_canonical_bytes(item), _canonical_value(item))
            for item in value
        ]
        items.sort(key=lambda item: item[0])
        return ["frozenset", [item for _encoded, item in items]]
    raise TypeError(
        f"Ian plan contains unsupported value type: {value_type.__name__}"
    )
