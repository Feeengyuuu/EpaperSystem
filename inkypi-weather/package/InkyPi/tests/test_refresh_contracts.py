from collections import UserDict, UserList, namedtuple
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from collections.abc import Set as SetABC
from dataclasses import FrozenInstanceError
from enum import Enum
import threading
from types import MappingProxyType

import pytest

from src.runtime import refresh_contracts as refresh_contracts_module
from src.runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobRecord,
    JobStatus,
    RefreshCommand,
    RefreshIntent,
    TaskCancelled,
    TaskContext,
    TaskDeadlineExceeded,
    freeze_payload,
)


def test_refresh_command_freezes_explicit_intent():
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="weather",
        payload={},
        now_monotonic=10.0,
        deadline_monotonic=20.0,
        intent=RefreshIntent.DATA_REFRESH,
    )

    assert command.intent is RefreshIntent.DATA_REFRESH
    with pytest.raises(FrozenInstanceError):
        command.intent = RefreshIntent.LIVE_REFRESH


def test_legacy_factory_without_intent_remains_compatible_until_c_integration():
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.SCHEDULER,
        plugin_id="weather",
        payload={},
        now_monotonic=10.0,
        deadline_monotonic=20.0,
    )

    assert command.intent is None


class MutableCustomLeaf:
    def __init__(self, values):
        self.values = list(values)


class MutableEnumLeaf(Enum):
    VALUE = ["mutable"]


class MutableStr(str):
    def __new__(cls, value):
        instance = super().__new__(cls, value)
        instance.values = ["mutable"]
        return instance


class MutableBytes(bytes):
    def __new__(cls, value):
        instance = super().__new__(cls, value)
        instance.values = ["mutable"]
        return instance


class StringSemanticEnum(str, Enum):
    VALUE = "semantic"


class IntegerSemanticEnum(int, Enum):
    VALUE = 1


SemanticPair = namedtuple("SemanticPair", ("left", "right"))


class SemanticSequence(SequenceABC):
    def __init__(self, values):
        self.values = list(values)

    def __getitem__(self, index):
        return self.values[index]

    def __len__(self):
        return len(self.values)

    __hash__ = object.__hash__


class SemanticSet(SetABC):
    def __init__(self, values):
        self.values = set(values)

    def __contains__(self, value):
        return value in self.values

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)


class SemanticMapping(MappingABC):
    def __init__(self, values):
        self.values = dict(values)

    def __getitem__(self, key):
        return self.values[key]

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)


class SemanticDict(dict):
    pass


class SemanticList(list):
    pass


class SemanticTuple(tuple):
    pass


class SemanticBuiltinSet(set):
    pass


class SemanticUserDict(UserDict):
    pass


class SemanticUserList(UserList):
    pass


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


@pytest.mark.parametrize(
    "semantic_value",
    [
        MutableStr("semantic"),
        MutableBytes(b"semantic"),
        StringSemanticEnum.VALUE,
        IntegerSemanticEnum.VALUE,
        SemanticPair("left", "right"),
        range(3),
        SemanticSequence([1, 2]),
        SemanticSet({1, 2}),
        SemanticMapping({}),
        SemanticDict({"one": 1}),
        SemanticList([1, 2]),
        SemanticTuple((1, 2)),
        SemanticBuiltinSet({1, 2}),
        SemanticUserDict({"one": 1}),
        SemanticUserList([1, 2]),
    ],
    ids=[
        "str-subclass",
        "bytes-subclass",
        "str-enum",
        "int-enum",
        "namedtuple",
        "range",
        "custom-sequence",
        "custom-set",
        "custom-mapping",
        "dict-subclass",
        "list-subclass",
        "tuple-subclass",
        "set-subclass",
        "userdict-subclass",
        "userlist-subclass",
    ],
)
def test_freeze_payload_rejects_semantic_or_subclassed_containers(
    semantic_value,
):
    payload = {"semantic": semantic_value}

    with pytest.raises(TypeError):
        freeze_payload(payload)
    with pytest.raises(TypeError):
        RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="sports_dashboard",
            payload=payload,
            now_monotonic=10.0,
            deadline_monotonic=20.0,
        )


def test_refresh_command_does_not_bypass_empty_semantic_payload_validation():
    empty_semantic_mapping = SemanticMapping({})

    with pytest.raises(TypeError):
        freeze_payload(empty_semantic_mapping)
    with pytest.raises(TypeError):
        RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="sports_dashboard",
            payload=empty_semantic_mapping,
            now_monotonic=10.0,
            deadline_monotonic=20.0,
        )


@pytest.mark.parametrize(
    "semantic_key",
    [
        SemanticPair("left", "right"),
        SemanticTuple(("left", "right")),
        MutableStr("semantic-key"),
        StringSemanticEnum.VALUE,
    ],
    ids=["namedtuple", "tuple-subclass", "str-subclass", "str-enum"],
)
def test_freeze_payload_rejects_semantic_mapping_keys(semantic_key):
    with pytest.raises(TypeError):
        freeze_payload({semantic_key: "value"})


@pytest.mark.parametrize(
    "semantic_member",
    [
        SemanticPair("left", "right"),
        SemanticTuple(("left", "right")),
        MutableStr("semantic-member"),
        StringSemanticEnum.VALUE,
        SemanticSequence(["semantic-member"]),
    ],
    ids=[
        "namedtuple",
        "tuple-subclass",
        "str-subclass",
        "str-enum",
        "custom-sequence",
    ],
)
def test_freeze_payload_rejects_semantic_set_members(semantic_member):
    with pytest.raises(TypeError):
        freeze_payload({"members": {semantic_member}})


def test_freeze_payload_exact_safe_container_allowlist_remains_supported():
    source = MappingProxyType(
        {
            "dict": {"nested": [1, 2]},
            "tuple": ("one", "two"),
            "set": {"one", "two"},
            "frozenset": frozenset({"one", "two"}),
            "userdict": UserDict({"nested": UserList([1, 2])}),
            "scalars": [None, True, 1, 1.5, 2 + 3j, "text", b"bytes"],
        }
    )

    frozen = freeze_payload(source)

    assert isinstance(frozen, MappingProxyType)
    assert frozen["dict"]["nested"] == (1, 2)
    assert frozen["tuple"] == ("one", "two")
    assert frozen["set"] == frozenset({"one", "two"})
    assert frozen["frozenset"] == frozenset({"one", "two"})
    assert frozen["userdict"]["nested"] == (1, 2)
    assert frozen["scalars"] == (
        None,
        True,
        1,
        1.5,
        2 + 3j,
        "text",
        b"bytes",
    )


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
