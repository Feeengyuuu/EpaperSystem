from collections import UserDict
from dataclasses import FrozenInstanceError

import pytest

from src.runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobRecord,
    JobStatus,
    RefreshCommand,
)


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
