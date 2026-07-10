import time
from pathlib import Path

import pytest
from PIL import Image

from src.display import display_transaction as transaction_module
from src.display.display_transaction import (
    DisplayCommitUnknownError,
    DisplayTransaction,
)
from src.runtime.refresh_contracts import TaskContext
from src.runtime.runtime_state import RuntimeStateStore


def _image(color):
    return Image.new("RGB", (8, 6), color)


def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 5)


class FakeManager:
    def __init__(self):
        self.calls = []
        self.error = None

    def prepare_image(self, image, *, image_settings=()):
        return image.copy()

    def hardware_fingerprint(self, image_settings=()):
        return f"fake:{tuple(image_settings)}"

    def write_hardware_path(self, image_path, *, image_settings=(), task_context):
        task_context.raise_if_cancelled()
        self.calls.append((Path(image_path), tuple(image_settings)))
        if self.error is not None:
            raise self.error


@pytest.fixture
def display_transaction(tmp_path):
    display_dir = tmp_path / "display"
    display_dir.mkdir()
    runtime_state = RuntimeStateStore(tmp_path / "runtime.json")
    manager = FakeManager()
    transaction = DisplayTransaction(
        manager,
        display_dir=display_dir,
        compatibility_image_path=display_dir / "current_image.png",
        runtime_state_store=runtime_state,
    )
    return transaction, manager, runtime_state


def test_hardware_failure_keeps_previous_manifest(display_transaction):
    transaction, manager, _runtime_state = display_transaction
    first = transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "one"}),
        task_context=_context(),
    )
    manager.error = RuntimeError("busy")

    with pytest.raises(RuntimeError, match="busy"):
        transaction.commit(
            transaction.prepare(_image("blue"), logical_target={"id": "two"}),
            task_context=_context(),
        )

    assert transaction.current().commit_id == first.commit_id


def test_same_pixels_new_logical_target_creates_metadata_only_commit(
    display_transaction,
):
    transaction, manager, _runtime_state = display_transaction
    first = transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "one"}),
        task_context=_context(),
    )
    second = transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "two"}),
        task_context=_context(),
    )

    assert len(manager.calls) == 1
    assert second.commit_id != first.commit_id
    assert dict(second.logical_target) == {"id": "two"}
    assert second.hardware_written is False


def test_manifest_failure_after_hardware_marks_display_unknown(
    display_transaction,
    monkeypatch,
):
    transaction, manager, runtime_state = display_transaction
    prepared = transaction.prepare(_image("red"), logical_target={"id": "one"})
    monkeypatch.setattr(
        transaction_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(DisplayCommitUnknownError) as raised:
        transaction.commit(prepared, task_context=_context())

    assert raised.value.commit_id == prepared.commit_id
    assert len(manager.calls) == 1
    snapshot = runtime_state.snapshot()
    assert snapshot.display_state == "display_unknown"
    assert snapshot.display_commit_id == prepared.commit_id


def test_recover_resubmits_last_manifest_after_newer_orphan(display_transaction):
    transaction, manager, runtime_state = display_transaction
    first = transaction.commit(
        transaction.prepare(
            _image("red"),
            logical_target={"instance_uuid": "one"},
        ),
        task_context=_context(),
    )
    manager.error = RuntimeError("busy")
    with pytest.raises(RuntimeError):
        transaction.commit(
            transaction.prepare(_image("blue"), logical_target={"id": "two"}),
            task_context=_context(),
        )
    manager.error = None

    recovered = transaction.recover(task_context=_context())

    assert recovered.commit_id == first.commit_id
    assert manager.calls[-1][0] == first.image_path
    snapshot = runtime_state.snapshot()
    assert snapshot.display_state == "committed"
    assert snapshot.display_commit_id == first.commit_id
    assert snapshot.displayed_instance_uuid == "one"


def test_recover_without_valid_manifest_stays_not_ready(display_transaction):
    transaction, manager, runtime_state = display_transaction

    assert transaction.recover(task_context=_context()) is None
    assert manager.calls == []
    assert runtime_state.snapshot().display_state == "not_ready"
