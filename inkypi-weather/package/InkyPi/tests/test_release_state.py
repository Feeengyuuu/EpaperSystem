import json
from pathlib import Path
import sys

import pytest


INSTALL_LIB = Path(__file__).resolve().parents[1] / "install" / "lib"
sys.path.insert(0, str(INSTALL_LIB))

from release_state import (  # noqa: E402
    InvalidTransition,
    RecoveryAction,
    ReleaseLayout,
    ReleaseStateError,
    UpdateJournal,
    UpdatePhase,
    atomic_symlink,
    recover_incomplete_update,
)


def _journal_at(tmp_path, phase):
    journal = UpdateJournal.create(
        tmp_path / "update-state.json",
        release_id="release-new",
    )
    sequence = (
        UpdatePhase.DOWNLOADED,
        UpdatePhase.PREFLIGHTED,
        UpdatePhase.SWITCHED,
        UpdatePhase.STARTING,
        UpdatePhase.HEALTHY,
        UpdatePhase.COMMITTED,
    )
    if phase == UpdatePhase.ROLLING_BACK:
        journal.transition(UpdatePhase.DOWNLOADED)
        journal.transition(UpdatePhase.PREFLIGHTED)
        journal.transition(UpdatePhase.SWITCHED)
        journal.transition(UpdatePhase.ROLLING_BACK)
        return journal
    if phase in {UpdatePhase.ROLLED_BACK, UpdatePhase.ROLLBACK_FAILED}:
        journal.transition(UpdatePhase.DOWNLOADED)
        journal.transition(UpdatePhase.PREFLIGHTED)
        journal.transition(UpdatePhase.SWITCHED)
        journal.transition(UpdatePhase.ROLLING_BACK)
        journal.transition(phase)
        return journal
    for candidate in sequence:
        if phase == UpdatePhase.CREATED:
            break
        journal.transition(candidate)
        if candidate == phase:
            break
    return journal


def test_update_phase_transitions_are_strict_and_durable(tmp_path):
    journal = UpdateJournal.create(
        tmp_path / "update-state.json",
        release_id="release-new",
        metadata={"artifact_sha256": "a" * 64},
    )

    journal.transition(UpdatePhase.DOWNLOADED)
    with pytest.raises(InvalidTransition):
        journal.transition(UpdatePhase.HEALTHY)

    reloaded = UpdateJournal.load(journal.path)
    assert reloaded.phase is UpdatePhase.DOWNLOADED
    assert reloaded.release_id == "release-new"
    assert reloaded.metadata["artifact_sha256"] == "a" * 64
    assert json.loads(journal.path.read_text(encoding="utf-8"))["version"] == 1


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (UpdatePhase.CREATED, RecoveryAction.CLEAN_STAGING),
        (UpdatePhase.DOWNLOADED, RecoveryAction.CLEAN_STAGING),
        (UpdatePhase.PREFLIGHTED, RecoveryAction.CLEAN_STAGING),
        (UpdatePhase.SWITCHED, RecoveryAction.ROLL_BACK),
        (UpdatePhase.STARTING, RecoveryAction.ROLL_BACK),
        (UpdatePhase.ROLLING_BACK, RecoveryAction.ROLL_BACK),
        (UpdatePhase.HEALTHY, RecoveryAction.FINISH_COMMIT),
        (UpdatePhase.COMMITTED, RecoveryAction.NONE),
        (UpdatePhase.ROLLED_BACK, RecoveryAction.NONE),
        (UpdatePhase.ROLLBACK_FAILED, RecoveryAction.MANUAL_INTERVENTION),
    ],
)
def test_power_loss_recovery_is_deterministic(tmp_path, phase, expected):
    journal = _journal_at(tmp_path, phase)

    assert journal.recovery_action() is expected


def test_recover_incomplete_update_invokes_exactly_one_action(tmp_path):
    journal = _journal_at(tmp_path, UpdatePhase.SWITCHED)
    calls = []

    action = recover_incomplete_update(
        journal,
        clean_staging=lambda _journal: calls.append("clean"),
        roll_back=lambda _journal: calls.append("rollback"),
        finish_commit=lambda _journal: calls.append("commit"),
    )

    assert action is RecoveryAction.ROLL_BACK
    assert calls == ["rollback"]


def test_corrupt_or_unknown_journal_fails_closed(tmp_path):
    path = tmp_path / "update-state.json"
    path.write_text('{"version": 1, "phase": "future"}', encoding="utf-8")

    with pytest.raises(ReleaseStateError):
        UpdateJournal.load(path)


def test_release_layout_rejects_unsafe_release_ids(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")

    with pytest.raises(ValueError):
        layout.release_path("../escape")
    with pytest.raises(ValueError):
        layout.release_path("bad/name")
    assert layout.release_path("release-20260710") == (
        tmp_path / "opt" / "releases" / "release-20260710"
    )


def test_atomic_symlink_uses_same_directory_replace_and_fsync(tmp_path, monkeypatch):
    target = tmp_path / "releases" / "new"
    link = tmp_path / "current"
    target.mkdir(parents=True)
    calls = []

    monkeypatch.setattr(
        "release_state.os.symlink",
        lambda source, destination, **kwargs: calls.append(
            ("symlink", Path(source), Path(destination), kwargs)
        ),
    )
    monkeypatch.setattr(
        "release_state.os.replace",
        lambda source, destination: calls.append(
            ("replace", Path(source), Path(destination))
        ),
    )
    monkeypatch.setattr(
        "release_state.fsync_directory",
        lambda directory: calls.append(("fsync", Path(directory))),
    )

    atomic_symlink(target, link)

    temporary = calls[0][2]
    assert temporary.parent == link.parent
    assert calls[0][0:2] == ("symlink", target)
    assert calls[1] == ("replace", temporary, link)
    assert calls[2] == ("fsync", link.parent)
