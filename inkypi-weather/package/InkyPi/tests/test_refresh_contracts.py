from collections import UserDict, UserList
from dataclasses import FrozenInstanceError
from enum import Enum
import threading

import pytest

from src.runtime import refresh_contracts as refresh_contracts_module
from src.runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobRecord,
    JobStatus,
    RefreshCommand,
    TaskCancelled,
    TaskContext,
    TaskDeadlineExceeded,
    freeze_payload,
)


class MutableCustomLeaf:
    def __init__(self, values):
        self.values = list(values)


class MutableEnumLeaf(Enum):
    VALUE = ["mutable"]


def test_refresh_command_is_immutable_and_freezes_nested_payload():
    source = {"settings": {"refreshOnDisplay": "false"}}
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="sports_dashboard",
        payload=source,
        now_monotonic=10.0,
        deadline_monotonic=20.0,
    )
    source["settings"]["refreshOnDisplay"] = "true"
    assert command.payload["settings"]["refreshOnDisplay"] == "false"
    with pytest.raises(FrozenInstanceError):
        command.priority = 0


def test_refresh_command_payload_rejects_direct_tuple_nested_mutation():
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="sports_dashboard",
        payload={"wrapped": ({"mutable": 1},)},
        now_monotonic=10.0,
        deadline_monotonic=20.0,
    )

    with pytest.raises(TypeError):
        command.payload["added"] = True
    with pytest.raises(TypeError):
        command.payload["wrapped"][0]["mutable"] = 2


def test_refresh_command_payload_freezes_mapping_implementations():
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="sports_dashboard",
        payload=UserDict({"nested": UserDict({"mutable": 1})}),
        now_monotonic=10.0,
        deadline_monotonic=20.0,
    )

    with pytest.raises(TypeError):
        command.payload["added"] = True
    with pytest.raises(TypeError):
        command.payload["nested"]["mutable"] = 2


@pytest.mark.parametrize(
    "payload",
    [
        {"leaf": MutableCustomLeaf(["mutable"])},
        {MutableCustomLeaf(["mutable-key"]): "value"},
        {"leaf": MutableEnumLeaf.VALUE},
    ],
)
def test_refresh_command_strictly_rejects_custom_mutable_payload_leaves_and_keys(
    payload,
):
    with pytest.raises(TypeError):
        RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="sports_dashboard",
            payload=payload,
            now_monotonic=10.0,
            deadline_monotonic=20.0,
        )
    with pytest.raises(TypeError):
        freeze_payload(payload)


def test_cancel_requested_is_metadata_not_a_job_status():
    job = JobRecord.from_command(
        RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="weather",
            payload={},
            now_monotonic=1.0,
            deadline_monotonic=2.0,
        ),
        submitted_at=100.0,
    )
    job.mark_running(101.0)
    job.request_cancel(102.0)
    assert job.status is JobStatus.RUNNING
    assert job.cancel_requested_at == 102.0
    with pytest.raises(ValueError):
        job.mark_succeeded(103.0)


def test_task_context_classifies_event_only_as_cooperative_cancellation():
    cancel_event = threading.Event()
    cancel_event.set()
    context = TaskContext(cancel_event, deadline_monotonic=11.0, clock=lambda: 10.0)

    with pytest.raises(TaskCancelled) as canceled:
        context.raise_if_cancelled()

    assert type(canceled.value) is TaskCancelled


def test_task_context_classifies_deadline_only_as_deadline_exceeded():
    context = TaskContext(threading.Event(), deadline_monotonic=10.0, clock=lambda: 10.0)

    with pytest.raises(TaskDeadlineExceeded):
        context.raise_if_cancelled()


def test_task_context_deadline_wins_when_event_and_deadline_are_both_set():
    cancel_event = threading.Event()
    cancel_event.set()
    context = TaskContext(cancel_event, deadline_monotonic=10.0, clock=lambda: 10.0)

    with pytest.raises(TaskDeadlineExceeded):
        context.raise_if_cancelled()


def test_task_context_samples_moving_clock_once_per_cancellation_check():
    samples = iter((9.0, 11.0))
    calls = []

    def moving_clock():
        calls.append(None)
        return next(samples)

    context = TaskContext(
        threading.Event(),
        deadline_monotonic=10.0,
        clock=moving_clock,
    )

    context.raise_if_cancelled()

    assert len(calls) == 1


def test_thaw_payload_recursively_returns_mutable_detached_plugin_data():
    leaf = MutableCustomLeaf(["original"])
    source = UserDict(
        {
            ("tuple", "key"): UserDict(
                {
                    "nested": UserList(
                        [
                            {"value": "original"},
                            {"items": UserList([1, 2])},
                        ]
                    )
                }
            ),
            "set": {"one", "two"},
            "text": "unchanged",
            "bytes": b"unchanged",
        }
    )
    frozen = freeze_payload(source)

    thawed = refresh_contracts_module.thaw_payload(
        UserDict({"payload": frozen, "leaf": leaf})
    )
    payload = thawed["payload"]

    assert isinstance(thawed, dict)
    assert isinstance(payload[("tuple", "key")]["nested"], list)
    assert isinstance(payload[("tuple", "key")]["nested"][1]["items"], list)
    assert isinstance(payload["set"], set)
    assert payload["text"] == "unchanged"
    assert payload["bytes"] == b"unchanged"
    assert ("tuple", "key") in payload
    assert isinstance(next(key for key in payload if isinstance(key, tuple)), tuple)

    payload[("tuple", "key")]["nested"][0]["value"] = "mutated"
    payload[("tuple", "key")]["nested"][1]["items"].append(3)
    payload["set"].add("three")
    thawed["leaf"].values.append("mutated")

    assert frozen[("tuple", "key")]["nested"][0]["value"] == "original"
    assert frozen[("tuple", "key")]["nested"][1]["items"] == (1, 2)
    assert frozen["set"] == frozenset({"one", "two"})
    assert leaf.values == ["original"]


def test_thaw_payload_calls_are_fully_isolated_including_custom_leaves_and_keys():
    tuple_key = ("stable", "key")
    leaf = MutableCustomLeaf(["source"])
    frozen = freeze_payload(
        {
            tuple_key: {"values": [{"nested": [1, 2]}]},
        }
    )
    thaw_source = {"payload": frozen, "leaf": leaf}

    first = refresh_contracts_module.thaw_payload(thaw_source)
    second = refresh_contracts_module.thaw_payload(thaw_source)

    first["payload"][tuple_key]["values"][0]["nested"].append(3)
    first["leaf"].values.append("first-only")

    assert second["payload"][tuple_key]["values"][0]["nested"] == [1, 2]
    assert second["leaf"].values == ["source"]
    assert frozen[tuple_key]["values"][0]["nested"] == (1, 2)
    assert first is not second
    assert first["payload"][tuple_key] is not second["payload"][tuple_key]
    assert first["leaf"] is not second["leaf"]
    assert leaf.values == ["source"]


def test_thaw_payload_preserves_hashable_detached_members_inside_mutable_set():
    tuple_member = ("nested", ("tuple", 1))
    frozen_member = frozenset({"alpha", "beta"})
    frozen = freeze_payload(
        {
            "members": {tuple_member, frozen_member},
        }
    )

    thawed = refresh_contracts_module.thaw_payload(frozen)

    assert isinstance(thawed["members"], set)
    assert tuple_member in thawed["members"]
    assert frozen_member in thawed["members"]
    assert all(hash(member) is not None for member in thawed["members"])
    thawed["members"].add(("new", "member"))
    assert ("new", "member") not in frozen["members"]
