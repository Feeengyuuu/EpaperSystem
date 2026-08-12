import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


INSTALL_ROOT = Path(__file__).resolve().parents[1] / "install"
INSTALL_LIB = INSTALL_ROOT / "lib"
sys.path.insert(0, str(INSTALL_LIB))
sys.path.insert(0, str(INSTALL_ROOT))

from preflight import (  # noqa: E402
    PreflightError,
    capture_release_migration_expectations,
)
from host_migration import TrustedHostMigrator  # noqa: E402
from release_archive import build_release_archive  # noqa: E402
from release_state import (  # noqa: E402
    RecoveryAction,
    ReleaseLayout,
    UpdateJournal,
    UpdatePhase,
)
from update_engine import UpdateCoordinator, UpdateFailed  # noqa: E402


HEADLESS_MODE_MIGRATION_ID = "headless_mode_v1"
HEADLESS_MODE_UPDATER_CAPABILITY = "headless_mode_v1"
HEADLESS_MODE_EXPECTATION_NAME = ".headless-mode-v1.expectation.json"


def _headless_candidate(tmp_path, *, release_id="candidate"):
    release = tmp_path / release_id
    install = release / "install"
    install.mkdir(parents=True)
    (release / ".release-id").write_text(f"{release_id}\n", encoding="utf-8")
    (install / ".release-migrations.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "migrations": [HEADLESS_MODE_MIGRATION_ID],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return release


class _Links:
    def __init__(self, current):
        self.targets = {"current": Path(current)}

    def read(self, link):
        return self.targets.get(Path(link).name)

    def replace(self, target, link):
        self.targets[Path(link).name] = Path(target)

    def remove(self, link):
        self.targets.pop(Path(link).name, None)


class _Service:
    def __init__(self):
        self.active = True
        self.enabled = True

    def is_active(self):
        return self.active

    def is_enabled(self):
        return self.enabled

    def stop(self):
        self.active = False

    def start(self):
        self.active = True

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def daemon_reload(self):
        return None

    def wait_ready(self, _release_id):
        return True


def _prepared_journal(layout, release_id):
    journal = UpdateJournal.create(layout.journal_path, release_id=release_id)
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    return journal


def test_headless_request_requires_explicit_updater_capability(tmp_path):
    release = _headless_candidate(tmp_path)
    config = tmp_path / "device.json"
    config.write_text("{}\n", encoding="utf-8")

    with pytest.raises(PreflightError, match="updater capability"):
        capture_release_migration_expectations(
            release,
            config,
            "candidate",
        )

    captured = capture_release_migration_expectations(
        release,
        config,
        "candidate",
        updater_capabilities=(HEADLESS_MODE_UPDATER_CAPABILITY,),
    )

    expectation = release / "install" / HEADLESS_MODE_EXPECTATION_NAME
    assert captured == (expectation,)
    assert json.loads(expectation.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "migration": HEADLESS_MODE_MIGRATION_ID,
        "release_id": "candidate",
        "updater_capability": HEADLESS_MODE_UPDATER_CAPABILITY,
    }


def test_headless_migration_uses_fixed_systemctl_commands_and_restores_snapshot(
    tmp_path,
):
    release = _headless_candidate(tmp_path)
    config = tmp_path / "device.json"
    config.write_text("{}\n", encoding="utf-8")
    capture_release_migration_expectations(
        release,
        config,
        "candidate",
        updater_capabilities=(HEADLESS_MODE_UPDATER_CAPABILITY,),
    )
    calls = []

    def runner(command, **kwargs):
        command = tuple(command)
        calls.append((command, kwargs))
        if command[1:] == ("get-default",):
            return SimpleNamespace(returncode=0, stdout=b"graphical.target\n")
        if command[1:] == ("is-enabled", "lightdm.service"):
            return SimpleNamespace(returncode=0, stdout=b"enabled\n")
        if command[1:] == ("is-active", "lightdm.service"):
            return SimpleNamespace(returncode=0, stdout=b"active\n")
        return SimpleNamespace(returncode=0, stdout=b"")

    migrator = TrustedHostMigrator(runner=runner)
    request = migrator.requested_migration(release, "candidate")
    snapshot = migrator.capture_snapshot(request)

    migrator.apply(request, snapshot)
    migrator.restore(request, snapshot)

    commands = [command for command, _kwargs in calls]
    assert all(command[0] == "/usr/bin/systemctl" for command in commands)
    assert commands[3:] == [
        ("/usr/bin/systemctl", "set-default", "multi-user.target"),
        ("/usr/bin/systemctl", "disable", "lightdm.service"),
        ("/usr/bin/systemctl", "stop", "lightdm.service"),
        ("/usr/bin/systemctl", "set-default", "graphical.target"),
        ("/usr/bin/systemctl", "enable", "lightdm.service"),
        ("/usr/bin/systemctl", "start", "lightdm.service"),
    ]
    assert not any("isolate" in command for command in commands)
    assert snapshot == {
        "default_target": "graphical.target",
        "lightdm_enabled": "enabled",
        "lightdm_active": "active",
    }


def test_capability_release_without_request_does_not_touch_host(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    links = _Links(old)
    commands = []
    migrator = TrustedHostMigrator(
        runner=lambda command, **_kwargs: commands.append(tuple(command))
    )
    journal = _prepared_journal(layout, "new")
    coordinator = UpdateCoordinator(
        layout,
        _Service(),
        links=links,
        host_migrator=migrator,
    )

    coordinator.activate(journal, new)

    assert journal.phase is UpdatePhase.COMMITTED
    assert links.read(layout.current_link) == new
    assert commands == []


def test_explicit_headless_request_enters_durable_applying_state(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    (old / "install").mkdir(parents=True)
    new = _headless_candidate(layout.releases_dir, release_id="new")
    config = tmp_path / "device.json"
    config.write_text("{}\n", encoding="utf-8")
    capture_release_migration_expectations(
        new,
        config,
        "new",
        updater_capabilities=(HEADLESS_MODE_UPDATER_CAPABILITY,),
    )
    commands = []

    def runner(command, **_kwargs):
        command = tuple(command)
        commands.append(command)
        states = {
            ("get-default",): b"graphical.target\n",
            ("is-enabled", "lightdm.service"): b"enabled\n",
            ("is-active", "lightdm.service"): b"active\n",
        }
        return SimpleNamespace(returncode=0, stdout=states.get(command[1:], b""))

    journal = _prepared_journal(layout, "new")
    coordinator = UpdateCoordinator(
        layout,
        _Service(),
        links=_Links(old),
        host_migrator=TrustedHostMigrator(runner=runner),
    )

    coordinator.activate(journal, new)

    document = json.loads(journal.path.read_text(encoding="utf-8"))
    assert [entry["phase"] for entry in document["history"]][-4:] == [
        "applying_host_migration",
        "starting",
        "healthy",
        "committed",
    ]
    assert document["metadata"]["host_migration"] == {
        "migration": HEADLESS_MODE_MIGRATION_ID,
        "snapshot": {
            "default_target": "graphical.target",
            "lightdm_enabled": "enabled",
            "lightdm_active": "active",
        },
    }
    assert commands[-3:] == [
        ("/usr/bin/systemctl", "set-default", "multi-user.target"),
        ("/usr/bin/systemctl", "disable", "lightdm.service"),
        ("/usr/bin/systemctl", "stop", "lightdm.service"),
    ]


def test_headless_host_switch_happens_while_app_is_stopped_before_new_ready(
    tmp_path,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    (old / "install").mkdir(parents=True)
    new = _headless_candidate(layout.releases_dir, release_id="new")
    config = tmp_path / "device.json"
    config.write_text("{}\n", encoding="utf-8")
    capture_release_migration_expectations(
        new,
        config,
        "new",
        updater_capabilities=(HEADLESS_MODE_UPDATER_CAPABILITY,),
    )
    events = []

    def runner(command, **_kwargs):
        arguments = tuple(command[1:])
        states = {
            ("get-default",): b"graphical.target\n",
            ("is-enabled", "lightdm.service"): b"enabled\n",
            ("is-active", "lightdm.service"): b"active\n",
        }
        if arguments in states:
            events.append(f"query:{arguments[0]}")
            return SimpleNamespace(returncode=0, stdout=states[arguments])
        events.append("host:" + ":".join(arguments))
        return SimpleNamespace(returncode=0, stdout=b"")

    class RecordingService(_Service):
        def stop(self):
            events.append("service:stop")
            super().stop()

        def start(self):
            events.append("service:start")
            super().start()

        def wait_ready(self, _release_id):
            events.append("service:ready")
            return True

    journal = _prepared_journal(layout, "new")
    coordinator = UpdateCoordinator(
        layout,
        RecordingService(),
        links=_Links(old),
        host_migrator=TrustedHostMigrator(runner=runner),
    )

    coordinator.activate(journal, new)

    assert events.index("service:stop") < events.index(
        "host:set-default:multi-user.target"
    )
    assert events.index("host:stop:lightdm.service") < events.index(
        "service:start"
    )
    assert events.index("service:start") < events.index("service:ready")
    history = [
        entry["phase"]
        for entry in json.loads(journal.path.read_text(encoding="utf-8"))["history"]
    ]
    assert history[-4:] == [
        "applying_host_migration",
        "starting",
        "healthy",
        "committed",
    ]


def test_ready_failure_stops_candidate_before_restoring_graphical_host(
    tmp_path,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    (old / "install").mkdir(parents=True)
    new = _headless_candidate(layout.releases_dir, release_id="new")
    config = tmp_path / "device.json"
    config.write_text("{}\n", encoding="utf-8")
    capture_release_migration_expectations(
        new,
        config,
        "new",
        updater_capabilities=(HEADLESS_MODE_UPDATER_CAPABILITY,),
    )
    events = []

    def runner(command, **_kwargs):
        arguments = tuple(command[1:])
        states = {
            ("get-default",): b"graphical.target\n",
            ("is-enabled", "lightdm.service"): b"enabled\n",
            ("is-active", "lightdm.service"): b"active\n",
        }
        if arguments in states:
            return SimpleNamespace(returncode=0, stdout=states[arguments])
        events.append("host:" + ":".join(arguments))
        return SimpleNamespace(returncode=0, stdout=b"")

    class FailingReadyService(_Service):
        def stop(self):
            events.append("service:stop")
            super().stop()

        def start(self):
            events.append("service:start")
            super().start()

        def wait_ready(self, _release_id):
            events.append("service:ready-failed")
            return False

    journal = _prepared_journal(layout, "new")
    coordinator = UpdateCoordinator(
        layout,
        FailingReadyService(),
        links=_Links(old),
        host_migrator=TrustedHostMigrator(runner=runner),
    )

    with pytest.raises(UpdateFailed, match="rolled back"):
        coordinator.activate(journal, new)

    failure_index = events.index("service:ready-failed")
    rollback_events = events[failure_index + 1 :]
    assert rollback_events[0] == "service:stop"
    assert rollback_events.index("service:stop") < rollback_events.index(
        "host:set-default:graphical.target"
    )
    assert journal.phase is UpdatePhase.ROLLED_BACK


@pytest.mark.parametrize(
    "failed_command",
    [
        ("set-default", "multi-user.target"),
        ("disable", "lightdm.service"),
        ("stop", "lightdm.service"),
    ],
)
def test_headless_apply_failure_restores_exact_host_snapshot(
    tmp_path,
    failed_command,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    (old / "install").mkdir(parents=True)
    new = _headless_candidate(layout.releases_dir, release_id="new")
    config = tmp_path / "device.json"
    config.write_text("{}\n", encoding="utf-8")
    capture_release_migration_expectations(
        new,
        config,
        "new",
        updater_capabilities=(HEADLESS_MODE_UPDATER_CAPABILITY,),
    )
    state = {
        "default_target": "graphical.target",
        "lightdm_enabled": "enabled",
        "lightdm_active": "active",
    }
    failure_pending = True

    def runner(command, **_kwargs):
        nonlocal failure_pending
        arguments = tuple(command[1:])
        query_values = {
            ("get-default",): state["default_target"],
            ("is-enabled", "lightdm.service"): state["lightdm_enabled"],
            ("is-active", "lightdm.service"): state["lightdm_active"],
        }
        if arguments in query_values:
            return SimpleNamespace(
                returncode=0,
                stdout=(query_values[arguments] + "\n").encode(),
            )
        if failure_pending and arguments == failed_command:
            failure_pending = False
            raise subprocess.CalledProcessError(1, command)
        if arguments[0] == "set-default":
            state["default_target"] = arguments[1]
        elif arguments[0] in {"enable", "disable"}:
            state["lightdm_enabled"] = (
                "enabled" if arguments[0] == "enable" else "disabled"
            )
        elif arguments[0] in {"start", "stop"}:
            state["lightdm_active"] = (
                "active" if arguments[0] == "start" else "inactive"
            )
        return SimpleNamespace(returncode=0, stdout=b"")

    links = _Links(old)
    journal = _prepared_journal(layout, "new")
    coordinator = UpdateCoordinator(
        layout,
        _Service(),
        links=links,
        host_migrator=TrustedHostMigrator(runner=runner),
    )

    with pytest.raises(UpdateFailed, match="rolled back"):
        coordinator.activate(journal, new)

    assert state == {
        "default_target": "graphical.target",
        "lightdm_enabled": "enabled",
        "lightdm_active": "active",
    }
    assert links.read(layout.current_link) == old
    assert journal.phase is UpdatePhase.ROLLED_BACK


@pytest.mark.parametrize("completed_actions", range(4))
def test_power_loss_at_each_headless_action_boundary_restores_snapshot(
    tmp_path,
    completed_actions,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    state = {
        "default_target": "graphical.target",
        "lightdm_enabled": "enabled",
        "lightdm_active": "active",
    }
    if completed_actions >= 1:
        state["default_target"] = "multi-user.target"
    if completed_actions >= 2:
        state["lightdm_enabled"] = "disabled"
    if completed_actions >= 3:
        state["lightdm_active"] = "inactive"

    def runner(command, **_kwargs):
        arguments = tuple(command[1:])
        if arguments[0] == "set-default":
            state["default_target"] = arguments[1]
        elif arguments[0] in {"enable", "disable"}:
            state["lightdm_enabled"] = (
                "enabled" if arguments[0] == "enable" else "disabled"
            )
        elif arguments[0] in {"start", "stop"}:
            state["lightdm_active"] = (
                "active" if arguments[0] == "start" else "inactive"
            )
        return SimpleNamespace(returncode=0, stdout=b"")

    journal = UpdateJournal.create(layout.journal_path, release_id="new")
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    journal.update_metadata(
        previous_target=str(old),
        target_path=str(new),
        legacy_source=None,
        service_was_active=True,
        service_was_enabled=True,
        managed_backups=[],
        host_migration={
            "migration": HEADLESS_MODE_MIGRATION_ID,
            "snapshot": {
                "default_target": "graphical.target",
                "lightdm_enabled": "enabled",
                "lightdm_active": "active",
            },
        },
    )
    journal.transition(UpdatePhase.SWITCHED)
    journal.transition(UpdatePhase.APPLYING_HOST_MIGRATION)
    links = _Links(new)
    links.targets["previous"] = old
    coordinator = UpdateCoordinator(
        layout,
        _Service(),
        links=links,
        host_migrator=TrustedHostMigrator(runner=runner),
    )

    action = coordinator.recover(journal)

    assert action is RecoveryAction.ROLL_BACK
    assert journal.phase is UpdatePhase.ROLLED_BACK
    assert links.read(layout.current_link) == old
    assert state == {
        "default_target": "graphical.target",
        "lightdm_enabled": "enabled",
        "lightdm_active": "active",
    }


@pytest.mark.parametrize(
    "transient_restore_command",
    [
        ("set-default", "graphical.target"),
        ("enable", "lightdm.service"),
        ("start", "lightdm.service"),
    ],
)
def test_transient_host_restore_failure_remains_boot_retryable(
    tmp_path,
    transient_restore_command,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    state = {
        "default_target": "multi-user.target",
        "lightdm_enabled": "disabled",
        "lightdm_active": "inactive",
    }
    fail_once = {transient_restore_command}

    def runner(command, **_kwargs):
        arguments = tuple(command[1:])
        if arguments in fail_once:
            fail_once.remove(arguments)
            raise subprocess.CalledProcessError(1, command)
        if arguments[0] == "set-default":
            state["default_target"] = arguments[1]
        elif arguments[0] in {"enable", "disable"}:
            state["lightdm_enabled"] = (
                "enabled" if arguments[0] == "enable" else "disabled"
            )
        elif arguments[0] in {"start", "stop"}:
            state["lightdm_active"] = (
                "active" if arguments[0] == "start" else "inactive"
            )
        return SimpleNamespace(returncode=0, stdout=b"")

    journal = UpdateJournal.create(layout.journal_path, release_id="new")
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    journal.update_metadata(
        previous_target=str(old),
        target_path=str(new),
        legacy_source=None,
        service_was_active=True,
        service_was_enabled=True,
        managed_backups=[],
        host_migration={
            "migration": HEADLESS_MODE_MIGRATION_ID,
            "snapshot": {
                "default_target": "graphical.target",
                "lightdm_enabled": "enabled",
                "lightdm_active": "active",
            },
        },
    )
    journal.transition(UpdatePhase.SWITCHED)
    journal.transition(UpdatePhase.APPLYING_HOST_MIGRATION)
    links = _Links(new)
    links.targets["previous"] = old
    coordinator = UpdateCoordinator(
        layout,
        _Service(),
        links=links,
        host_migrator=TrustedHostMigrator(runner=runner),
    )

    with pytest.raises(subprocess.CalledProcessError):
        coordinator.recover(journal)

    assert journal.phase is UpdatePhase.ROLLING_BACK
    assert journal.recovery_action() is RecoveryAction.ROLL_BACK
    assert journal.metadata["rollback_failure_count"] == 1

    assert coordinator.recover(journal) is RecoveryAction.ROLL_BACK
    assert journal.phase is UpdatePhase.ROLLED_BACK
    assert links.read(layout.current_link) == old
    assert state == {
        "default_target": "graphical.target",
        "lightdm_enabled": "enabled",
        "lightdm_active": "active",
    }


def test_release_builder_injects_only_sanitized_headless_request(tmp_path):
    project = tmp_path / "project"
    install = project / "install"
    install.mkdir(parents=True)
    (project / "app.py").write_text("# candidate\n", encoding="utf-8")
    (install / HEADLESS_MODE_EXPECTATION_NAME).write_text(
        '{"untrusted":"must not ship"}\n',
        encoding="utf-8",
    )
    artifact = tmp_path / "release.zip"

    build_release_archive(
        project,
        artifact,
        migrations=(HEADLESS_MODE_MIGRATION_ID,),
    )

    import zipfile

    with zipfile.ZipFile(artifact) as archive:
        members = set(archive.namelist())
        request = json.loads(
            archive.read("install/.release-migrations.json").decode("utf-8")
        )
    assert request == {
        "schema_version": 1,
        "migrations": [HEADLESS_MODE_MIGRATION_ID],
    }
    assert f"install/{HEADLESS_MODE_EXPECTATION_NAME}" not in members


def test_power_loss_after_headless_migration_health_restores_graphical_snapshot(
    tmp_path,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    journal = UpdateJournal.create(layout.journal_path, release_id="new")
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    journal.update_metadata(
        previous_target=str(old),
        target_path=str(new),
        service_was_active=True,
        service_was_enabled=True,
        managed_backups=[],
        host_migration={
            "migration": HEADLESS_MODE_MIGRATION_ID,
            "snapshot": {
                "default_target": "graphical.target",
                "lightdm_enabled": "enabled",
                "lightdm_active": "active",
            },
        },
    )
    journal.transition(UpdatePhase.SWITCHED)
    journal.transition(UpdatePhase.APPLYING_HOST_MIGRATION)
    journal.transition(UpdatePhase.STARTING)
    journal.transition(UpdatePhase.HEALTHY)
    commands = []
    links = _Links(new)
    links.targets["previous"] = old
    coordinator = UpdateCoordinator(
        layout,
        _Service(),
        links=links,
        host_migrator=TrustedHostMigrator(
            runner=lambda command, **_kwargs: commands.append(tuple(command))
        ),
    )

    action = coordinator.recover(journal)

    assert action is RecoveryAction.ROLL_BACK
    assert journal.phase is UpdatePhase.ROLLED_BACK
    assert links.read(layout.current_link) == old
    assert commands == [
        ("/usr/bin/systemctl", "set-default", "graphical.target"),
        ("/usr/bin/systemctl", "enable", "lightdm.service"),
        ("/usr/bin/systemctl", "start", "lightdm.service"),
    ]
