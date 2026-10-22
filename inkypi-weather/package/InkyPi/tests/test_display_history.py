import json
import time
from datetime import datetime, timedelta, timezone

from PIL import Image

from display import display_history
from display import display_transaction as transaction_module
from display.display_transaction import DisplayTransaction
from runtime.refresh_contracts import TaskContext
from runtime.runtime_state import RuntimeStateStore

T0 = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)


class FakeManager:
    def prepare_image(self, image, *, image_settings=()):
        return image.copy()

    def hardware_fingerprint(self, image_settings=()):
        return "fake"

    def write_hardware_path(self, image_path, *, image_settings=(), task_context):
        task_context.raise_if_cancelled()


def _transaction(tmp_path):
    display_dir = tmp_path / "display"
    return DisplayTransaction(
        FakeManager(),
        display_dir=display_dir,
        compatibility_image_path=display_dir / "current_image.png",
        runtime_state_store=RuntimeStateStore(tmp_path / "runtime.json"),
    ), display_dir


def _target(uuid, plugin_id="sports_dashboard"):
    return {"kind": "playlist", "instance_uuid": uuid, "plugin_id": plugin_id}


def test_history_keeps_each_instances_latest_displays_newest_first(tmp_path):
    for minutes in range(display_history.DISPLAYS_PER_INSTANCE + 3):
        display_history.record_display(tmp_path, _target("sports"), (T0 + timedelta(minutes=minutes)).isoformat())
    display_history.record_display(tmp_path, _target("news", "daily_ai_news"), T0.isoformat())

    recent = display_history.recent_displays(tmp_path, "sports")

    assert len(recent) == display_history.DISPLAYS_PER_INSTANCE
    assert recent[0] == (T0 + timedelta(minutes=display_history.DISPLAYS_PER_INSTANCE + 2)).isoformat()
    assert list(recent) == sorted(recent, reverse=True)
    assert display_history.recent_displays(tmp_path, "news") == (T0.isoformat(),)
    assert display_history.recent_displays(tmp_path, "unknown") == ()


def test_history_ignores_targets_without_an_instance_and_bad_files(tmp_path):
    assert display_history.record_display(tmp_path, {"kind": "manual"}, T0.isoformat()) is False
    assert display_history.record_display(tmp_path, _target("sports"), "not a time") is False
    (tmp_path / display_history.HISTORY_NAME).write_text("{broken", encoding="utf-8")

    assert display_history.recent_displays(tmp_path, "sports") == ()
    assert display_history.record_display(tmp_path, _target("sports"), T0.isoformat()) is True
    assert display_history.recent_displays(tmp_path, "sports") == (T0.isoformat(),)


def test_history_stays_bounded_by_instance_count(tmp_path, monkeypatch):
    monkeypatch.setattr(display_history, "MAX_INSTANCES", 3)
    for index in range(5):
        display_history.record_display(tmp_path, _target(f"i{index}"), (T0 + timedelta(minutes=index)).isoformat())

    stored = json.loads((tmp_path / display_history.HISTORY_NAME).read_text(encoding="utf-8"))

    assert sorted(stored["instances"]) == ["i2", "i3", "i4"]


def test_each_display_commit_records_the_instance(tmp_path):
    transaction, display_dir = _transaction(tmp_path)
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 5)

    first = transaction.commit(transaction.prepare(Image.new("RGB", (4, 4), "red"),
                                                   logical_target=_target("sports")), task_context=context)
    # The same pixels again: no hardware write, but the page is still the one on screen.
    second = transaction.commit(transaction.prepare(Image.new("RGB", (4, 4), "red"),
                                                    logical_target=_target("sports")), task_context=context)

    assert display_history.recent_displays(display_dir, "sports") == (second.committed_at, first.committed_at)


def test_a_history_failure_never_fails_the_display_commit(tmp_path, monkeypatch):
    transaction, display_dir = _transaction(tmp_path)

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(transaction_module, "record_display", broken)
    commit = transaction.commit(
        transaction.prepare(Image.new("RGB", (4, 4), "blue"), logical_target=_target("sports")),
        task_context=TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 5),
    )

    assert transaction.current().commit_id == commit.commit_id
    assert display_history.recent_displays(display_dir, "sports") == ()
