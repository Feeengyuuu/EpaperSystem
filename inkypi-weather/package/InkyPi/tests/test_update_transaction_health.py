"""Update transaction acceptance at the systemd and HTTP boundaries."""

import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


INSTALL_LIB = Path(__file__).resolve().parents[1] / "install" / "lib"
sys.path.insert(0, str(INSTALL_LIB))

from release_state import ReleaseLayout, UpdateJournal, UpdatePhase  # noqa: E402
from update_engine import (  # noqa: E402
    ManagedFile,
    SystemdService,
    UpdateCoordinator,
    UpdateFailed,
)
import update_engine as update_engine_module  # noqa: E402


class LinkBoundary:
    """Model OS symlinks without requiring Windows symlink privileges."""

    def __init__(self, current):
        self.targets = {"current": current}

    def read(self, link):
        return self.targets.get(Path(link).name)

    def replace(self, target, link):
        self.targets[Path(link).name] = Path(target)

    def remove(self, link):
        self.targets.pop(Path(link).name, None)


class HostBoundary:
    """Only external OS, HTTP, and time surfaces are simulated."""

    def __init__(self, layout, links, release_ids, previous_response):
        self.layout = layout
        self.links = links
        self.release_ids = release_ids
        self.active = True
        self.enabled = True
        self.now = 0.0
        self.running_release = "old-release"
        self.previous_response = previous_response

    def run(self, command, **_kwargs):
        action = command[1]
        if action == "--no-block":
            action = command[2]
        if action == "is-active":
            return SimpleNamespace(returncode=0 if self.active else 3)
        if action == "is-enabled":
            return SimpleNamespace(returncode=0 if self.enabled else 1)
        if action == "stop":
            self.active = False
        elif action == "start":
            self.active = True
            self.running_release = self.release_ids[
                self.links.read(self.layout.current_link)
            ]
        elif action == "enable":
            self.enabled = True
        elif action == "disable":
            self.enabled = False
        return SimpleNamespace(returncode=0)

    def open(self, _url, **_kwargs):
        if self.running_release == "new-release":
            status, body = 503, {"release_id": "new-release", "status": "starting"}
        else:
            status, body = self.previous_response
        response = io.BytesIO(json.dumps(body).encode("utf-8"))
        response.status = status
        return response

    def sleep(self, seconds):
        self.now += seconds


def transaction(tmp_path, monkeypatch, previous_response):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    # A moved legacy directory need not have the same name as its release ID.
    old = layout.release_path("old-directory")
    new = layout.release_path("new-release")
    for release, release_id in ((old, "old-release"), (new, "new-release")):
        (release / "install").mkdir(parents=True)
        (release / ".release-id").write_text(release_id + "\n", encoding="utf-8")
        (release / "install" / "inkypi.service").write_text(release_id, encoding="utf-8")
    links = LinkBoundary(old)
    unit = tmp_path / "inkypi.service"
    unit.write_text("old-release", encoding="utf-8")
    host = HostBoundary(
        layout, links, {old: "old-release", new: "new-release"}, previous_response
    )
    monkeypatch.setattr(update_engine_module, "build_opener", lambda *_args: host)
    service = SystemdService(
        runner=host.run,
        clock=lambda: host.now,
        sleep=host.sleep,
        health_timeout_seconds=1,
    )
    coordinator = UpdateCoordinator(
        layout,
        service,
        links=links,
        managed_files=(ManagedFile("install/inkypi.service", unit, 0o644),),
    )
    journal = UpdateJournal.create(layout.journal_path, release_id="new-release")
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    return coordinator, journal, host, old, new, unit


@pytest.mark.parametrize(
    "previous_response",
    [
        (200, {"release_id": "wrong-release", "status": "ready"}),
        (200, {"release_id": "old-release", "status": "starting"}),
        (503, {"release_id": "old-release", "status": "ready"}),
    ],
    ids=["wrong-release", "not-ready", "unhealthy-http"],
)
def test_failed_upgrade_keeps_recovery_pending_until_previous_release_ready(
    tmp_path, monkeypatch, previous_response
):
    coordinator, journal, host, old, new, unit = transaction(
        tmp_path, monkeypatch, previous_response
    )

    with pytest.raises(UpdateFailed, match="rollback failed"):
        coordinator.activate(journal, new)

    assert host.active
    assert host.links.read(coordinator.layout.current_link) == old
    assert unit.read_text(encoding="utf-8") == "old-release"
    assert journal.phase is UpdatePhase.ROLLING_BACK
    assert all(
        Path(record["backup"]).is_file()
        for record in journal.metadata["managed_backups"]
    )

    host.previous_response = (200, {"release_id": "old-release", "status": "ready"})
    coordinator.recover(journal)

    assert journal.phase is UpdatePhase.ROLLED_BACK
    assert not (coordinator.layout.backup_dir / journal.release_id).exists()


@pytest.mark.parametrize(
    "previous_response",
    [
        (200, {"release_id": "wrong-release", "status": "ready"}),
        (200, {"release_id": "old-release", "status": "starting"}),
    ],
    ids=["wrong-release", "not-ready"],
)
def test_boot_recovery_waits_for_previous_application_before_discarding_backups(
    tmp_path, monkeypatch, previous_response
):
    coordinator, journal, host, _old, new, _unit = transaction(
        tmp_path, monkeypatch, previous_response
    )
    with pytest.raises(UpdateFailed, match="rollback failed"):
        coordinator.activate(journal, new)
    coordinator.recover(journal, defer_service_starts=True)

    with pytest.raises(UpdateFailed, match="restored release did not become ready"):
        coordinator.finalize_boot_recovery(journal)

    assert host.active
    assert journal.phase is UpdatePhase.ROLLBACK_PENDING_SERVICES
    assert all(
        Path(record["backup"]).is_file()
        for record in journal.metadata["managed_backups"]
    )

    host.previous_response = (200, {"release_id": "old-release", "status": "ready"})
    coordinator.finalize_boot_recovery(journal)

    assert journal.phase is UpdatePhase.ROLLED_BACK
    assert not (coordinator.layout.backup_dir / journal.release_id).exists()


def test_rollback_accepts_previous_release_identity_after_directory_rename(
    tmp_path, monkeypatch
):
    coordinator, journal, host, old, new, _unit = transaction(
        tmp_path,
        monkeypatch,
        (200, {"release_id": "old-release", "status": "ready"}),
    )

    with pytest.raises(UpdateFailed, match="was rolled back"):
        coordinator.activate(journal, new)

    assert host.links.read(coordinator.layout.current_link) == old
    assert journal.phase is UpdatePhase.ROLLED_BACK


@pytest.mark.parametrize("identity", [None, "invalid/release\n"])
def test_rollback_retains_backups_when_previous_identity_cannot_be_verified(
    tmp_path, monkeypatch, identity
):
    coordinator, journal, host, old, new, _unit = transaction(
        tmp_path,
        monkeypatch,
        (200, {"release_id": "old-release", "status": "ready"}),
    )
    identity_file = old / ".release-id"
    if identity is None:
        identity_file.unlink()
    else:
        identity_file.write_text(identity, encoding="utf-8")

    with pytest.raises(UpdateFailed, match="restored release identity is unavailable or invalid"):
        coordinator.activate(journal, new)

    assert host.active
    assert journal.phase is UpdatePhase.ROLLING_BACK
    assert all(
        Path(record["backup"]).is_file()
        for record in journal.metadata["managed_backups"]
    )
    identity_file.write_text("old-release\n", encoding="utf-8")
    coordinator.recover(journal)
    assert journal.phase is UpdatePhase.ROLLED_BACK


def test_rollback_preserves_originally_inactive_service_without_requiring_readiness(
    tmp_path, monkeypatch
):
    coordinator, journal, host, _old, new, _unit = transaction(
        tmp_path,
        monkeypatch,
        (503, {"release_id": "old-release", "status": "starting"}),
    )
    host.active = False

    with pytest.raises(UpdateFailed, match="was rolled back"):
        coordinator.activate(journal, new)

    assert not host.active
    assert journal.phase is UpdatePhase.ROLLED_BACK
    assert not (coordinator.layout.backup_dir / journal.release_id).exists()
