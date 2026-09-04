import configparser
import hashlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALL_ROOT = PROJECT_ROOT / "install"
INSTALL_LIB = INSTALL_ROOT / "lib"
sys.path.insert(0, str(INSTALL_LIB))
sys.path.insert(0, str(INSTALL_ROOT))

from host_migration import TrustedHostMigrator  # noqa: E402
from release_state import ReleaseLayout, UpdateJournal, UpdatePhase  # noqa: E402
from update_engine import UpdateCoordinator  # noqa: E402
from update_engine import BootRecoveryUnitInstaller, ManagedFile, UpdateFailed  # noqa: E402
import update_engine as update_engine_module  # noqa: E402


RECOVERY_UNIT_NAME = "inkypi-update-recover.service"


def _load_updater_module(name="inkypi_update_boot_recovery_test_module"):
    loader = SourceFileLoader(name, str(INSTALL_ROOT / "inkypi-update"))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def _parse_unit(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


class _Links:
    def __init__(self, current, previous=None):
        self.targets = {"current": Path(current)}
        if previous is not None:
            self.targets["previous"] = Path(previous)

    def read(self, link):
        return self.targets.get(Path(link).name)

    def replace(self, target, link):
        self.targets[Path(link).name] = Path(target)

    def remove(self, link):
        self.targets.pop(Path(link).name, None)


class _BootService:
    def __init__(self):
        self.active = False
        self.enabled = True
        self.events = []

    def is_active(self):
        return self.active

    def is_enabled(self):
        return self.enabled

    def stop(self):
        self.events.append("stop")
        self.active = False

    def start(self):
        self.events.append("start")
        self.active = True

    def start_deferred(self):
        self.events.append("start_deferred")
        self.active = True

    def enable(self):
        self.events.append("enable")
        self.enabled = True

    def disable(self):
        self.events.append("disable")
        self.enabled = False

    def daemon_reload(self):
        self.events.append("daemon_reload")

    def wait_ready(self, release_id):
        return self.active and release_id == "old"


def _applying_headless_journal(layout, old, new):
    (old / ".release-id").write_text("old\n", encoding="utf-8")
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
            "migration": "headless_mode_v1",
            "snapshot": {
                "default_target": "graphical.target",
                "lightdm_enabled": "enabled",
                "lightdm_active": "active",
            },
        },
    )
    journal.transition(UpdatePhase.SWITCHED)
    journal.transition(UpdatePhase.APPLYING_HOST_MIGRATION)
    return journal


def test_boot_recovery_unit_runs_as_root_before_display_and_app_services():
    unit_path = INSTALL_ROOT / RECOVERY_UNIT_NAME

    assert unit_path.is_file()
    unit = _parse_unit(unit_path)
    before = set(unit["Unit"]["Before"].split())
    assert before == {
        "display-manager.service",
        "lightdm.service",
        "inkypi.service",
    }
    assert unit["Service"]["Type"] == "oneshot"
    assert unit["Service"]["RemainAfterExit"].lower() == "yes"
    assert unit["Service"]["User"] == "root"
    assert unit["Service"]["Group"] == "root"
    assert unit["Service"]["ExecStart"] == (
        "/usr/local/sbin/inkypi-update --recover-only"
    )
    assert unit["Install"]["WantedBy"] == "multi-user.target"
    assert "EnvironmentFile" not in unit["Service"]


def test_recover_only_cli_is_exact_and_cannot_be_combined_with_update_inputs(
    monkeypatch,
):
    module = _load_updater_module()
    monkeypatch.setattr(module.os, "geteuid", lambda: 0, raising=False)
    calls = []
    monkeypatch.setattr(module, "run_recovery", lambda: calls.append("recover") or 0)
    monkeypatch.setattr(
        module,
        "run_update",
        lambda _args: calls.append("update") or 0,
    )

    assert module.main(["--recover-only"]) == 0
    assert calls == ["recover"]

    with pytest.raises(SystemExit) as error:
        module.main(
            [
                "--recover-only",
                "--artifact",
                "/tmp/release.zip",
                "--sha256",
                "0" * 64,
                "--release-id",
                "candidate",
            ]
        )
    assert error.value.code == 2
    assert calls == ["recover"]


def test_recover_only_no_journal_is_an_idempotent_noop(tmp_path):
    module = _load_updater_module("inkypi_update_no_journal_test_module")
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()

    class Coordinator:
        def recover(self, _journal, **_kwargs):
            raise AssertionError("no journal must not invoke recovery")

    assert module.run_recovery(layout=layout, coordinator=Coordinator()) == 0
    assert not layout.journal_path.exists()
    assert list(layout.history_dir.iterdir()) == []


def test_recover_only_acquires_the_normal_update_lock(tmp_path, monkeypatch):
    module = _load_updater_module("inkypi_update_shared_lock_test_module")
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    observed = []

    class Lock:
        def __init__(self, path):
            observed.append(Path(path))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(module, "UpdateLock", Lock)
    monkeypatch.setattr(
        module,
        "_headless_capability_attested",
        lambda _layout: {"capability": "headless_mode_v1"},
    )
    monkeypatch.setattr(module, "_persist_capability_attestation", lambda *_args: None)
    monkeypatch.setattr(
        module,
        "_read_capability_attestation",
        lambda _layout: {"capability": "headless_mode_v1"},
    )

    assert module.run_recovery(layout=layout, coordinator=object()) == 0
    assert observed == [layout.lock_path]


@pytest.mark.parametrize("completed_actions", range(4))
def test_boot_recovery_restores_each_partial_headless_action_without_blocking_starts(
    tmp_path,
    completed_actions,
):
    module = _load_updater_module(
        f"inkypi_update_partial_{completed_actions}_test_module"
    )
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
    commands = []

    def runner(command, **_kwargs):
        arguments = tuple(command[1:])
        commands.append(arguments)
        queries = {
            ("get-default",): state["default_target"],
            ("is-enabled", "lightdm.service"): state["lightdm_enabled"],
            ("is-active", "lightdm.service"): state["lightdm_active"],
        }
        if arguments in queries:
            return SimpleNamespace(
                returncode=0,
                stdout=(queries[arguments] + "\n").encode(),
            )
        if arguments[0] == "set-default":
            state["default_target"] = arguments[1]
        elif arguments[0] in {"enable", "disable"}:
            state["lightdm_enabled"] = (
                "enabled" if arguments[0] == "enable" else "disabled"
            )
        elif arguments[:2] == ("--no-block", "start"):
            state["lightdm_active"] = "active"
        elif arguments[0] in {"start", "stop"}:
            state["lightdm_active"] = (
                "active" if arguments[0] == "start" else "inactive"
            )
        return SimpleNamespace(returncode=0, stdout=b"")

    journal = _applying_headless_journal(layout, old, new)
    links = _Links(new, old)
    service = _BootService()
    coordinator = UpdateCoordinator(
        layout,
        service,
        links=links,
        host_migrator=TrustedHostMigrator(runner=runner),
    )

    assert module.run_recovery(layout=layout, coordinator=coordinator) == 0

    pending = UpdateJournal.load(layout.journal_path)
    assert pending.phase is UpdatePhase.ROLLBACK_PENDING_SERVICES
    assert tuple(layout.history_dir.glob("*.json")) == ()
    assert links.read(layout.current_link) == old
    assert state == {
        "default_target": "graphical.target",
        "lightdm_enabled": "enabled",
        "lightdm_active": "active",
    }
    assert ("--no-block", "start", "lightdm.service") in commands
    assert service.events[0] == "stop"
    assert "start" not in service.events
    assert "start_deferred" in service.events

    assert module.run_finalize_recovery(layout=layout, coordinator=coordinator) == 0
    assert not layout.journal_path.exists()
    histories = tuple(layout.history_dir.glob("*.json"))
    assert len(histories) == 1
    assert json.loads(histories[0].read_text(encoding="utf-8"))["phase"] == (
        "rolled_back"
    )


def test_boot_recovery_failure_exits_nonzero_and_keeps_a_retryable_journal(tmp_path):
    module = _load_updater_module("inkypi_update_failed_recovery_test_module")
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    journal = _applying_headless_journal(layout, old, new)
    links = _Links(new, old)

    def runner(command, **_kwargs):
        if tuple(command[1:]) == ("set-default", "graphical.target"):
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0, stdout=b"")

    coordinator = UpdateCoordinator(
        layout,
        _BootService(),
        links=links,
        host_migrator=TrustedHostMigrator(runner=runner),
    )

    assert module.run_recovery(layout=layout, coordinator=coordinator) == 1
    reloaded = UpdateJournal.load(layout.journal_path)
    assert reloaded.phase is UpdatePhase.ROLLING_BACK
    assert reloaded.metadata["rollback_failure_count"] == 1
    assert list(layout.history_dir.iterdir()) == []


def test_second_stage_bootstraps_recovery_unit_before_host_capture(tmp_path, monkeypatch):
    module = _load_updater_module("inkypi_update_stage_two_test_module")
    args = SimpleNamespace(
        artifact=tmp_path / "release.zip",
        sha256="0" * 64,
        release_id="headless-candidate",
        install_root=tmp_path / "opt",
        state_root=tmp_path / "state",
        config=tmp_path / "device.json",
        service_name="inkypi.service",
        systemctl="/usr/bin/systemctl",
        health_url="http://127.0.0.1/readyz",
        health_timeout=1.0,
        python=sys.executable,
        unit_target=tmp_path / "etc" / "inkypi.service",
        recovery_unit_target=tmp_path / "etc" / RECOVERY_UNIT_NAME,
        launcher_target=tmp_path / "bin" / "inkypi",
        updater_target=tmp_path / "sbin" / "inkypi-update",
        legacy_root=tmp_path / "legacy",
    )
    layout = ReleaseLayout(args.install_root, args.state_root)
    layout.ensure()
    # A real deployment uses a symlink.  A directory at the same public
    # current-release path avoids requiring Windows symlink privileges here.
    current = layout.current_link
    (current / "install").mkdir(parents=True)
    (current / "install" / RECOVERY_UNIT_NAME).write_text(
        "capability recovery unit\n",
        encoding="utf-8",
    )
    events = []

    class Lock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            events.append("lock-enter")
            return self

        def __exit__(self, *_args):
            events.append("lock-exit")
            return False

    class RecoveryInstaller:
        def __init__(self, **_kwargs):
            pass

        def install_capability(self, source):
            assert Path(source) == current
            events.append("recovery-unit-enabled")

        def wait_ready(self):
            events.append("recovery-unit-ready")

    class Service:
        def __init__(self, **_kwargs):
            pass

    class Coordinator:
        def __init__(self, _layout, _service, **_kwargs):
            self.links = SimpleNamespace()

        def activate(self, journal, _release):
            events.append("host-capture")
            journal.transition(UpdatePhase.SWITCHED)
            journal.transition(UpdatePhase.STARTING)
            journal.transition(UpdatePhase.HEALTHY)
            journal.transition(UpdatePhase.COMMITTED)

    class Preparer:
        def __init__(self, _layout, **_kwargs):
            pass

        def prepare(self, _inspection, _release_id, _journal):
            target = layout.release_path(args.release_id)
            target.mkdir(parents=True)
            return target

        def ensure_bootstrap_token(self, _release):
            pass

    monkeypatch.setattr(module, "UpdateLock", Lock)
    monkeypatch.setattr(
        module,
        "_headless_capability_attested",
        lambda _layout: {"capability": "headless_mode_v1"},
    )
    monkeypatch.setattr(
        module,
        "_persist_capability_attestation",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        module,
        "_read_capability_attestation",
        lambda _layout: {"capability": "headless_mode_v1"},
    )
    monkeypatch.setattr(
        update_engine_module,
        "BootRecoveryUnitInstaller",
        RecoveryInstaller,
    )
    monkeypatch.setattr(module, "SystemdService", Service)
    monkeypatch.setattr(module, "UpdateCoordinator", Coordinator)
    monkeypatch.setattr(module, "ArtifactPreparer", Preparer)
    monkeypatch.setattr(
        module,
        "inspect_artifact",
        lambda _artifact, _sha: SimpleNamespace(sha256="verified"),
    )
    monkeypatch.setattr(module, "archive_journal", lambda _layout, journal: journal.path.unlink())
    monkeypatch.setattr(module, "prune_releases", lambda *_args, **_kwargs: None)

    assert module.run_update(args) == 0
    assert events[:5] == [
        "lock-enter",
        "recovery-unit-enabled",
        "lock-exit",
        "recovery-unit-ready",
        "lock-enter",
    ]
    assert events.index("recovery-unit-enabled") < events.index("host-capture")


def test_concurrent_updater_cannot_enter_locked_capability_bootstrap(
    tmp_path,
    monkeypatch,
):
    module = _load_updater_module("inkypi_update_concurrent_bootstrap_test_module")
    args = SimpleNamespace(
        artifact=tmp_path / "release.zip",
        sha256="0" * 64,
        release_id="candidate",
        install_root=tmp_path / "opt",
        state_root=tmp_path / "state",
        config=tmp_path / "device.json",
        service_name="inkypi.service",
        systemctl="/usr/bin/systemctl",
        health_url="http://127.0.0.1/readyz",
        health_timeout=1.0,
        python=sys.executable,
        unit_target=tmp_path / "etc" / "inkypi.service",
        recovery_unit_target=tmp_path / "etc" / RECOVERY_UNIT_NAME,
        launcher_target=tmp_path / "bin" / "inkypi",
        updater_target=tmp_path / "sbin" / "inkypi-update",
        legacy_root=tmp_path / "legacy",
    )
    gate = threading.Lock()
    bootstrap_entered = threading.Event()
    release_bootstrap = threading.Event()
    second_bootstrap = threading.Event()
    marker = tmp_path / "managed-capability"
    first_errors = []

    class Lock:
        def __init__(self, _path, *, blocking=False):
            self.blocking = blocking
            self.acquired = False

        def __enter__(self):
            self.acquired = gate.acquire(blocking=self.blocking)
            if not self.acquired:
                raise UpdateFailed("another InkyPi update is already running")
            return self

        def __exit__(self, *_args):
            if self.acquired:
                gate.release()
            return False

    class Coordinator:
        def __init__(self, *_args, **_kwargs):
            self.links = SimpleNamespace()

    def bootstrap(_layout, _args):
        if threading.current_thread().name == "first-updater":
            marker.write_text("first\n", encoding="utf-8")
            bootstrap_entered.set()
            assert release_bootstrap.wait(timeout=5)
            raise RuntimeError("stop first updater after concurrency probe")
        second_bootstrap.set()
        marker.write_text("second\n", encoding="utf-8")
        raise RuntimeError("second updater entered capability bootstrap")

    monkeypatch.setattr(module, "UpdateLock", Lock)
    monkeypatch.setattr(module, "SystemdService", lambda **_kwargs: object())
    monkeypatch.setattr(module, "UpdateCoordinator", Coordinator)
    monkeypatch.setattr(module, "ArtifactPreparer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_recover_existing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_bootstrap_attested_headless_capability", bootstrap)

    def first_update():
        try:
            module.run_update(args)
        except BaseException as error:
            first_errors.append(error)

    first = threading.Thread(target=first_update, name="first-updater")
    first.start()
    assert bootstrap_entered.wait(timeout=5)
    try:
        with pytest.raises(UpdateFailed, match="another InkyPi update"):
            module.run_update(args)
        assert not second_bootstrap.is_set()
        assert marker.read_text(encoding="utf-8") == "first\n"
        assert not (Path(args.state_root) / "update-state.json").exists()
    finally:
        release_bootstrap.set()
        first.join(timeout=5)

    assert not first.is_alive()
    assert len(first_errors) == 1
    assert "stop first updater" in str(first_errors[0])


def test_recover_only_waits_for_locked_bootstrap_then_acquires_same_lock(
    tmp_path,
    monkeypatch,
):
    module = _load_updater_module("inkypi_update_recovery_lock_handoff_test_module")
    args = SimpleNamespace(
        artifact=tmp_path / "release.zip",
        sha256="0" * 64,
        release_id="candidate",
        install_root=tmp_path / "opt",
        state_root=tmp_path / "state",
        config=tmp_path / "device.json",
        service_name="inkypi.service",
        systemctl="/usr/bin/systemctl",
        health_url="http://127.0.0.1/readyz",
        health_timeout=1.0,
        python=sys.executable,
        unit_target=tmp_path / "etc" / "inkypi.service",
        recovery_unit_target=tmp_path / "etc" / RECOVERY_UNIT_NAME,
        launcher_target=tmp_path / "bin" / "inkypi",
        updater_target=tmp_path / "sbin" / "inkypi-update",
        legacy_root=tmp_path / "legacy",
    )
    layout = ReleaseLayout(args.install_root, args.state_root)
    layout.ensure()
    gate = threading.Lock()
    events = []
    recovery_thread = []
    recovery_result = []
    recovery_attempted = threading.Event()
    allow_recovery_lock_attempt = threading.Event()
    recovery_waiting_on_second = threading.Event()
    second_holds_lock = threading.Event()
    release_second = threading.Event()
    second_errors = []
    expected_attestation = {
        "release_id": "capability",
        "updater_sha256": "a" * 64,
    }

    class Lock:
        def __init__(self, _path, *, blocking=False):
            self.blocking = blocking
            self.acquired = False

        def __enter__(self):
            actor = threading.current_thread().name
            events.append((actor, "attempt", self.blocking))
            if actor == "recover-only":
                recovery_attempted.set()
                assert allow_recovery_lock_attempt.wait(timeout=5)
                recovery_waiting_on_second.set()
            self.acquired = gate.acquire(blocking=self.blocking)
            if not self.acquired:
                raise UpdateFailed("another InkyPi update is already running")
            events.append((actor, "acquired", self.blocking))
            return self

        def __exit__(self, *_args):
            actor = threading.current_thread().name
            if self.acquired:
                gate.release()
                events.append((actor, "released", self.blocking))
            return False

    class Coordinator:
        def __init__(self, *_args, **kwargs):
            self.links = SimpleNamespace()
            self.managed_files = kwargs.get("managed_files", ())

    def bootstrap(_layout, _args):
        if threading.current_thread().name == "second-updater":
            second_holds_lock.set()
            assert release_second.wait(timeout=5)
            raise RuntimeError("stop second updater after lock priority probe")

        def recover():
            recovery_result.append(
                module.run_recovery(layout=layout, coordinator=object())
            )

        thread = threading.Thread(target=recover, name="recover-only")
        recovery_thread.append(thread)
        thread.start()
        assert recovery_attempted.wait(timeout=5)
        return ("headless_mode_v1",)

    def wait_ready(_args):
        events.append((threading.current_thread().name, "wait", None))
        def second_update():
            try:
                module.run_update(args)
            except BaseException as error:
                second_errors.append(error)

        second = threading.Thread(target=second_update, name="second-updater")
        second.start()
        assert second_holds_lock.wait(timeout=5)
        allow_recovery_lock_attempt.set()
        assert recovery_waiting_on_second.wait(timeout=5)
        assert ("recover-only", "acquired", True) not in events
        release_second.set()
        second.join(timeout=5)
        assert not second.is_alive()
        recovery_thread[0].join(timeout=5)
        assert not recovery_thread[0].is_alive()
        assert recovery_result == [0]

    monkeypatch.setattr(module, "UpdateLock", Lock)
    monkeypatch.setattr(module, "SystemdService", lambda **_kwargs: object())
    monkeypatch.setattr(module, "UpdateCoordinator", Coordinator)
    monkeypatch.setattr(module, "ArtifactPreparer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_recover_existing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_bootstrap_attested_headless_capability", bootstrap)
    monkeypatch.setattr(
        module,
        "_wait_boot_recovery_capability",
        wait_ready,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "_read_capability_attestation",
        lambda _layout: expected_attestation,
    )
    monkeypatch.setattr(
        module,
        "_headless_capability_attested",
        lambda _layout: expected_attestation,
    )
    monkeypatch.setattr(
        module,
        "inspect_artifact",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("stop after handoff")),
    )

    with pytest.raises(RuntimeError, match="stop after handoff"):
        module.run_update(args)

    recovery_attempt = events.index(("recover-only", "attempt", True))
    updater_first_release = events.index(("MainThread", "released", False))
    recovery_acquire = events.index(("recover-only", "acquired", True))
    recovery_release = events.index(("recover-only", "released", True))
    updater_wait = events.index(("MainThread", "wait", None))
    second_acquire = events.index(("second-updater", "acquired", False))
    second_release = events.index(("second-updater", "released", False))
    updater_second_acquire = len(events) - 1 - events[::-1].index(
        ("MainThread", "acquired", False)
    )
    assert recovery_attempt < updater_first_release < recovery_acquire
    assert updater_first_release < updater_wait < second_acquire
    assert second_acquire < second_release < recovery_acquire
    assert recovery_acquire < recovery_release < updater_second_acquire
    assert len(second_errors) == 1
    assert "lock priority probe" in str(second_errors[0])


def test_bootstrap_failure_prevents_journal_and_host_capture(tmp_path, monkeypatch):
    module = _load_updater_module("inkypi_update_bootstrap_failure_test_module")
    args = SimpleNamespace(
        artifact=tmp_path / "release.zip",
        sha256="0" * 64,
        release_id="headless-candidate",
        install_root=tmp_path / "opt",
        state_root=tmp_path / "state",
        config=tmp_path / "device.json",
        service_name="inkypi.service",
        systemctl="/usr/bin/systemctl",
        health_url="http://127.0.0.1/readyz",
        health_timeout=1.0,
        python=sys.executable,
        unit_target=tmp_path / "etc" / "inkypi.service",
        recovery_unit_target=tmp_path / "etc" / RECOVERY_UNIT_NAME,
        launcher_target=tmp_path / "bin" / "inkypi",
        updater_target=tmp_path / "sbin" / "inkypi-update",
        legacy_root=tmp_path / "legacy",
    )
    layout = ReleaseLayout(args.install_root, args.state_root)
    layout.ensure()
    current = layout.current_link
    (current / "install").mkdir(parents=True)
    (current / "install" / RECOVERY_UNIT_NAME).write_text("unit\n", encoding="utf-8")
    events = []

    class Lock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Installer:
        def __init__(self, **_kwargs):
            pass

        def install_capability(self, _source):
            events.append("bootstrap")
            raise RuntimeError("enable failed")

    class Coordinator:
        def __init__(self, *_args, **_kwargs):
            self.links = SimpleNamespace()

        def activate(self, *_args):
            events.append("host-capture")

    monkeypatch.setattr(module, "UpdateLock", Lock)
    monkeypatch.setattr(update_engine_module, "BootRecoveryUnitInstaller", Installer)
    monkeypatch.setattr(
        module,
        "_headless_capability_attested",
        lambda _layout: {"capability": "headless_mode_v1"},
    )
    monkeypatch.setattr(module, "SystemdService", lambda **_kwargs: object())
    monkeypatch.setattr(module, "UpdateCoordinator", Coordinator)

    with pytest.raises(RuntimeError, match="enable failed"):
        module.run_update(args)

    assert events == ["bootstrap"]
    assert not layout.journal_path.exists()


def test_updater_manages_recovery_unit_transactionally(tmp_path):
    module = _load_updater_module("inkypi_update_managed_unit_test_module")
    args = SimpleNamespace(
        unit_target=tmp_path / "etc" / "inkypi.service",
        recovery_unit_target=tmp_path / "etc" / RECOVERY_UNIT_NAME,
        launcher_target=tmp_path / "bin" / "inkypi",
        updater_target=tmp_path / "sbin" / "inkypi-update",
    )

    managed = {
        item.source_relative: item
        for item in module._managed_files(args, include_boot_recovery=True)
    }

    recovery = managed[f"install/{RECOVERY_UNIT_NAME}"]
    assert recovery.destination == args.recovery_unit_target
    assert recovery.mode == 0o644
    assert managed["install/lib/update_engine.py"].destination == Path(
        "/usr/local/lib/inkypi-update/update_engine.py"
    )

    capability_stage = tuple(
        item.source_relative
        for item in module._managed_files(args, include_boot_recovery=False)
    )
    assert capability_stage == (
        "install/inkypi.service",
        "install/inkypi",
        "install/inkypi-update",
    )


def test_recovery_unit_installer_copies_then_reloads_and_enables_fixed_unit(tmp_path):
    source = tmp_path / "release" / "install" / RECOVERY_UNIT_NAME
    source.parent.mkdir(parents=True)
    source.write_text("trusted unit\n", encoding="utf-8")
    target = tmp_path / "etc" / RECOVERY_UNIT_NAME
    events = []

    def copy_file(source_path, destination, mode):
        events.append(("copy", Path(source_path), Path(destination), mode))
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(source_path).read_bytes())

    def runner(command, **kwargs):
        events.append(("systemctl", tuple(command), kwargs))
        return SimpleNamespace(returncode=0)

    installer = BootRecoveryUnitInstaller(
        unit_target=target,
        runner=runner,
        copy_file=copy_file,
    )

    installer.install(source)
    installer.wait_ready()

    assert target.read_text(encoding="utf-8") == "trusted unit\n"
    assert events[0] == ("copy", source, target, 0o644)
    assert [event[1] for event in events[1:]] == [
        ("/usr/bin/systemctl", "daemon-reload"),
        ("/usr/bin/systemctl", "enable", RECOVERY_UNIT_NAME),
        ("/usr/bin/systemctl", "--no-block", "start", RECOVERY_UNIT_NAME),
        ("/usr/bin/systemctl", "start", RECOVERY_UNIT_NAME),
    ]


def test_activation_failure_restores_previous_managed_recovery_unit(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (old / ".release-id").write_text("old\n", encoding="utf-8")
    (new / "install").mkdir(parents=True)
    (new / "install" / RECOVERY_UNIT_NAME).write_text(
        "candidate unit\n",
        encoding="utf-8",
    )
    target = tmp_path / "etc" / RECOVERY_UNIT_NAME
    target.parent.mkdir(parents=True)
    target.write_text("capability unit\n", encoding="utf-8")
    links = _Links(old)
    service = _BootService()
    service.active = True

    def wait_ready(release_id):
        return release_id == "old"

    service.wait_ready = wait_ready
    journal = UpdateJournal.create(layout.journal_path, release_id="new")
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    coordinator = UpdateCoordinator(
        layout,
        service,
        links=links,
        managed_files=(
            ManagedFile(f"install/{RECOVERY_UNIT_NAME}", target, 0o644),
        ),
    )

    with pytest.raises(UpdateFailed, match="rolled back"):
        coordinator.activate(journal, new)

    assert target.read_text(encoding="utf-8") == "capability unit\n"
    assert links.read(layout.current_link) == old
    assert journal.phase is UpdatePhase.ROLLED_BACK


def test_release_preflight_and_uninstall_manage_boot_recovery_unit():
    from preflight import REQUIRED_RELEASE_PATHS

    assert f"install/{RECOVERY_UNIT_NAME}" in REQUIRED_RELEASE_PATHS
    uninstall = (INSTALL_ROOT / "uninstall.sh").read_text(encoding="utf-8")
    assert f"systemctl disable {RECOVERY_UNIT_NAME}" in uninstall
    assert '"$RECOVERY_UNIT"' in uninstall
    assert '"$UPDATE_LIB_ROOT"' in uninstall


def test_recover_only_uses_only_fixed_managed_file_destinations():
    module = _load_updater_module("inkypi_update_fixed_targets_test_module")

    managed = module._default_managed_files()

    destinations = {
        item.source_relative: (
            item.destination.as_posix().replace("\\", "/"),
            item.mode,
        )
        for item in managed
    }
    assert destinations["install/inkypi.service"] == (
        "/etc/systemd/system/inkypi.service",
        0o644,
    )
    assert destinations[f"install/{RECOVERY_UNIT_NAME}"] == (
        f"/etc/systemd/system/{RECOVERY_UNIT_NAME}",
        0o644,
    )
    assert destinations["install/inkypi-update-finalize.service"] == (
        "/etc/systemd/system/inkypi-update-finalize.service",
        0o644,
    )
    assert destinations[
        "install/systemd/inkypi.service.d/10-update-recovery.conf"
    ] == (
        "/etc/systemd/system/inkypi.service.d/10-update-recovery.conf",
        0o644,
    )
    assert destinations[
        "install/systemd/lightdm.service.d/10-inkypi-update-recovery.conf"
    ] == (
        "/etc/systemd/system/lightdm.service.d/10-inkypi-update-recovery.conf",
        0o644,
    )
    for library in (
        "release_state.py",
        "release_archive.py",
        "host_migration.py",
        "update_engine.py",
    ):
        assert destinations[f"install/lib/{library}"] == (
            f"/usr/local/lib/inkypi-update/{library}",
            0o644,
        )
    assert destinations["install/inkypi"] == ("/usr/local/bin/inkypi", 0o755)
    assert destinations["install/inkypi-update"] == (
        "/usr/local/sbin/inkypi-update",
        0o755,
    )


@pytest.mark.parametrize(
    ("was_enabled", "was_active", "expected_events"),
    [
        (False, False, ["stop", "disable", "daemon_reload"]),
        (False, True, ["stop", "disable", "daemon_reload", "start_deferred"]),
        (True, False, ["stop", "daemon_reload", "enable"]),
        (True, True, ["stop", "daemon_reload", "enable", "start_deferred"]),
    ],
)
def test_boot_recovery_restores_every_app_enable_active_combination(
    tmp_path,
    was_enabled,
    was_active,
    expected_events,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    journal = _applying_headless_journal(layout, old, new)
    document = json.loads(journal.path.read_text(encoding="utf-8"))
    document["metadata"]["service_was_enabled"] = was_enabled
    document["metadata"]["service_was_active"] = was_active
    journal.path.write_text(json.dumps(document), encoding="utf-8")
    journal = UpdateJournal.load(journal.path)
    service = _BootService()
    service.enabled = not was_enabled
    links = _Links(new, old)
    class Migrator:
        def restore_for_boot(self, _migration, _snapshot):
            pass

        def snapshot_matches(self, _migration, _snapshot):
            return True

    coordinator = UpdateCoordinator(
        layout,
        service,
        links=links,
        host_migrator=Migrator(),
    )
    action = coordinator.recover(journal, defer_service_starts=True)

    assert action.value == "roll_back"
    assert service.events == expected_events
    assert journal.phase is UpdatePhase.ROLLBACK_PENDING_SERVICES
    coordinator.finalize_boot_recovery(journal)
    assert journal.phase is UpdatePhase.ROLLED_BACK


def test_starting_power_loss_restores_all_host_managed_files(tmp_path):
    module = _load_updater_module("inkypi_update_starting_managed_files_test_module")
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    managed = tuple(
        ManagedFile(
            item.source_relative,
            tmp_path / "managed" / f"{index:02d}-{Path(item.source_relative).name}",
            item.mode,
        )
        for index, item in enumerate(module._default_managed_files())
    )
    assert len(managed) == 11
    for index, item in enumerate(managed):
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        item.destination.write_text(f"old-{index}\n", encoding="utf-8")
    journal = UpdateJournal.create(layout.journal_path, release_id="new")
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    coordinator = UpdateCoordinator(
        layout,
        _BootService(),
        links=_Links(old),
        managed_files=managed,
    )
    records = coordinator._capture_managed_file_backups(journal)
    journal.update_metadata(
        previous_target=str(old),
        target_path=str(new),
        legacy_source=None,
        service_was_active=False,
        service_was_enabled=True,
        managed_backups=records,
    )
    journal.transition(UpdatePhase.SWITCHED)
    journal.transition(UpdatePhase.STARTING)
    for index, item in enumerate(managed):
        item.destination.write_text(f"candidate-{index}\n", encoding="utf-8")

    UpdateCoordinator(
        layout,
        _BootService(),
        links=_Links(new, old),
        managed_files=managed,
    ).recover(journal, defer_service_starts=True)

    assert [item.destination.read_text(encoding="utf-8") for item in managed] == [
        f"old-{index}\n" for index in range(len(managed))
    ]


def test_healthy_headless_power_loss_rolls_back_before_commit(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    journal = _applying_headless_journal(layout, old, new)
    journal.transition(UpdatePhase.STARTING)
    journal.transition(UpdatePhase.HEALTHY)
    state = {
        "default_target": "multi-user.target",
        "lightdm_enabled": "disabled",
        "lightdm_active": "inactive",
    }

    def runner(command, **_kwargs):
        arguments = tuple(command[1:])
        if arguments[0] == "set-default":
            state["default_target"] = arguments[1]
        elif arguments[0] in {"enable", "disable"}:
            state["lightdm_enabled"] = (
                "enabled" if arguments[0] == "enable" else "disabled"
            )
        elif arguments[:2] == ("--no-block", "start"):
            state["lightdm_active"] = "active"
        return SimpleNamespace(returncode=0, stdout=b"")

    links = _Links(new, old)
    action = UpdateCoordinator(
        layout,
        _BootService(),
        links=links,
        host_migrator=TrustedHostMigrator(runner=runner),
    ).recover(journal, defer_service_starts=True)

    assert action.value == "roll_back"
    assert journal.phase is UpdatePhase.ROLLBACK_PENDING_SERVICES
    assert links.read(layout.current_link) == old
    assert state == {
        "default_target": "graphical.target",
        "lightdm_enabled": "enabled",
        "lightdm_active": "active",
    }


def test_committed_headless_release_is_never_rolled_back_at_boot(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    journal = _applying_headless_journal(layout, old, new)
    journal.transition(UpdatePhase.STARTING)
    journal.transition(UpdatePhase.HEALTHY)
    journal.transition(UpdatePhase.COMMITTED)
    commands = []
    links = _Links(new, old)

    action = UpdateCoordinator(
        layout,
        _BootService(),
        links=links,
        host_migrator=TrustedHostMigrator(
            runner=lambda command, **_kwargs: commands.append(tuple(command))
        ),
    ).recover(journal, defer_service_starts=True)

    assert action.value == "none"
    assert journal.phase is UpdatePhase.COMMITTED
    assert links.read(layout.current_link) == new
    assert commands == []


def test_headless_update_script_never_falls_back_to_source_updater():
    script = (INSTALL_ROOT / "update.sh").read_text(encoding="utf-8")

    installed_check = 'if [[ ! -x "$UPDATER" ]]; then'
    headless_gate = (
        'if [[ " ${MIGRATIONS[*]} " == *" headless_mode_v1 "* ]]; then'
    )
    fallback = 'UPDATER="$SCRIPT_DIR/inkypi-update"'
    assert script.index(installed_check) < script.index(headless_gate) < script.index(
        fallback
    )
    assert "requires the committed installed capability updater" in script


def test_artifact_preparer_advertises_headless_only_when_attested(tmp_path):
    from update_engine import ArtifactPreparer

    without = ArtifactPreparer(tmp_path)
    with_capability = ArtifactPreparer(
        tmp_path,
        updater_capabilities=("headless_mode_v1", "untrusted"),
    )

    assert without.updater_capabilities == ()
    assert with_capability.updater_capabilities == ("headless_mode_v1",)


def test_direct_source_updater_cannot_attest_headless_capability(tmp_path, monkeypatch):
    module = _load_updater_module("inkypi_update_source_attestation_test_module")
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current = layout.current_link
    (current / "install").mkdir(parents=True)
    (current / "install" / "inkypi-update").write_bytes(b"same")
    (current / ".release-id").write_text("capability\n", encoding="utf-8")
    installed = tmp_path / "sbin" / "inkypi-update"
    installed.parent.mkdir()
    installed.write_bytes(b"same")
    monkeypatch.setattr(module, "INSTALLED_UPDATER", installed)

    assert module._headless_capability_attested(layout) is None


def test_bootstrap_recovers_existing_journal_before_requiring_capability_files(
    tmp_path,
    monkeypatch,
):
    module = _load_updater_module("inkypi_update_recover_first_test_module")
    args = SimpleNamespace(
        artifact=tmp_path / "release.zip",
        sha256="0" * 64,
        release_id="candidate",
        install_root=tmp_path / "opt",
        state_root=tmp_path / "state",
        config=tmp_path / "device.json",
        service_name="inkypi.service",
        systemctl="/usr/bin/systemctl",
        health_url="http://127.0.0.1/readyz",
        health_timeout=1.0,
        python=sys.executable,
        unit_target=tmp_path / "etc" / "inkypi.service",
        recovery_unit_target=tmp_path / "etc" / RECOVERY_UNIT_NAME,
        launcher_target=tmp_path / "bin" / "inkypi",
        updater_target=tmp_path / "sbin" / "inkypi-update",
        legacy_root=tmp_path / "legacy",
    )
    layout = ReleaseLayout(args.install_root, args.state_root)
    layout.ensure()
    journal = UpdateJournal.create(layout.journal_path, release_id="old-attempt")
    events = []

    class Lock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Coordinator:
        def __init__(self, *_args, **_kwargs):
            self.links = SimpleNamespace()

        def recover(self, recovered):
            assert recovered.phase is UpdatePhase.CREATED
            events.append("recover")
            return SimpleNamespace(value="clean_staging")

        def cleanup_candidate(self, _journal):
            pass

        def cleanup_backups(self, _journal):
            pass

    monkeypatch.setattr(module, "UpdateLock", Lock)
    monkeypatch.setattr(module, "SystemdService", lambda **_kwargs: object())
    monkeypatch.setattr(module, "UpdateCoordinator", Coordinator)
    monkeypatch.setattr(
        module,
        "archive_journal",
        lambda _layout, recovered: recovered.path.unlink(),
    )
    monkeypatch.setattr(
        module,
        "_bootstrap_attested_headless_capability",
        lambda *_args: events.append("bootstrap") or (_ for _ in ()).throw(
            RuntimeError("no capability files")
        ),
    )

    with pytest.raises(RuntimeError, match="no capability files"):
        module.run_update(args)

    assert events == ["recover", "bootstrap"]
    assert not journal.path.exists()


def test_recovery_dependencies_fail_closed_for_display_and_inkypi():
    inkypi_dropin = (
        INSTALL_ROOT
        / "systemd"
        / "inkypi.service.d"
        / "10-update-recovery.conf"
    ).read_text(encoding="utf-8")
    lightdm_dropin = (
        INSTALL_ROOT
        / "systemd"
        / "lightdm.service.d"
        / "10-inkypi-update-recovery.conf"
    ).read_text(encoding="utf-8")

    for source in (inkypi_dropin, lightdm_dropin):
        assert "Requires=inkypi-update-recover.service" in source
        assert "After=inkypi-update-recover.service" in source


def test_current_release_drift_after_bootstrap_wait_aborts_before_journal(
    tmp_path,
    monkeypatch,
):
    module = _load_updater_module("inkypi_update_bootstrap_drift_test_module")
    args = SimpleNamespace(
        artifact=tmp_path / "missing.zip",
        sha256="0" * 64,
        release_id="candidate",
        install_root=tmp_path / "opt",
        state_root=tmp_path / "state",
        config=tmp_path / "device.json",
        service_name="inkypi.service",
        systemctl="/usr/bin/systemctl",
        health_url="http://127.0.0.1/readyz",
        health_timeout=1.0,
        python=sys.executable,
        unit_target=tmp_path / "etc" / "inkypi.service",
        recovery_unit_target=tmp_path / "etc" / RECOVERY_UNIT_NAME,
        launcher_target=tmp_path / "bin" / "inkypi",
        updater_target=tmp_path / "sbin" / "inkypi-update",
        legacy_root=tmp_path / "legacy",
    )
    expected = {"release_id": "capability", "updater_sha256": "a" * 64}

    class Lock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Coordinator:
        def __init__(self, *_args, **kwargs):
            self.links = SimpleNamespace()
            self.managed_files = kwargs.get("managed_files", ())

    monkeypatch.setattr(module, "UpdateLock", Lock)
    monkeypatch.setattr(module, "SystemdService", lambda **_kwargs: object())
    monkeypatch.setattr(module, "UpdateCoordinator", Coordinator)
    monkeypatch.setattr(module, "ArtifactPreparer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_recover_existing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "_bootstrap_attested_headless_capability",
        lambda *_args: ("headless_mode_v1",),
    )
    monkeypatch.setattr(module, "_wait_boot_recovery_capability", lambda _args: None)
    monkeypatch.setattr(module, "_read_capability_attestation", lambda _layout: expected)
    monkeypatch.setattr(
        module,
        "_headless_capability_attested",
        lambda _layout: {"release_id": "other", "updater_sha256": "b" * 64},
    )

    with pytest.raises(RuntimeError, match="changed during bootstrap"):
        module.run_update(args)

    assert not (Path(args.state_root) / "update-state.json").exists()


def test_boot_finalizer_runs_after_recovery_display_and_app():
    unit = _parse_unit(INSTALL_ROOT / "inkypi-update-finalize.service")

    assert set(unit["Unit"]["After"].split()) == {
        "inkypi-update-recover.service",
        "lightdm.service",
        "inkypi.service",
    }
    assert unit["Service"]["ExecStart"] == (
        "/usr/local/sbin/inkypi-update --finalize-recovery"
    )
    assert unit["Install"]["WantedBy"] == "multi-user.target"


def test_capability_attestation_requires_matching_committed_installed_updater(
    tmp_path,
    monkeypatch,
):
    module = _load_updater_module("inkypi_update_valid_attestation_test_module")
    current = tmp_path / "opt" / "releases" / "capability"
    (current / "install").mkdir(parents=True)
    updater = current / "install" / "inkypi-update"
    updater.write_bytes(b"trusted-updater")
    if os.name != "nt":
        updater.chmod(0o755)
    (current / ".release-id").write_text("capability\n", encoding="utf-8")
    history_dir = tmp_path / "state" / "history"
    history_dir.mkdir(parents=True)
    layout = SimpleNamespace(current_link=current, history_dir=history_dir)
    installed = tmp_path / "sbin" / "inkypi-update"
    installed.parent.mkdir()
    installed.write_bytes(b"trusted-updater")
    if os.name != "nt":
        installed.chmod(0o755)
    history = history_dir / "committed.json"
    history.write_text(
        json.dumps({"release_id": "capability", "phase": "committed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "INSTALLED_UPDATER", installed)
    monkeypatch.setattr(module, "__file__", str(installed))
    monkeypatch.setattr(module.os, "access", lambda path, mode: Path(path) == installed)

    attestation = module._headless_capability_attested(layout)

    assert attestation == {
        "schema_version": 1,
        "capability": "headless_mode_v1",
        "release_id": "capability",
        "updater_sha256": hashlib.sha256(b"trusted-updater").hexdigest(),
    }

    history.unlink()
    assert module._headless_capability_attested(layout) is None
    history.write_text(
        json.dumps({"release_id": "capability", "phase": "committed"}),
        encoding="utf-8",
    )
    installed.write_bytes(b"forged-updater")
    assert module._headless_capability_attested(layout) is None


def test_capability_attestation_is_durable_and_strictly_read_back(tmp_path):
    module = _load_updater_module("inkypi_update_attestation_io_test_module")
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    document = {
        "schema_version": 1,
        "capability": "headless_mode_v1",
        "release_id": "capability",
        "updater_sha256": "a" * 64,
    }

    module._persist_capability_attestation(layout, document)

    assert module._read_capability_attestation(layout) == document
    path = layout.state_root / module.HEADLESS_CAPABILITY_ATTESTATION
    path.write_text('{"capability":"a","capability":"b"}', encoding="utf-8")
    assert module._read_capability_attestation(layout) is None


def test_headless_request_is_detected_before_an_n_minus_one_preflight(tmp_path):
    module = _load_updater_module("inkypi_update_request_probe_test_module")
    artifact = tmp_path / "release.zip"
    import zipfile

    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr(
            "install/.release-migrations.json",
            json.dumps(
                {"schema_version": 1, "migrations": ["headless_mode_v1"]}
            ),
        )

    assert module._artifact_requests_headless(artifact) is True

    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr(
            "install/.release-migrations.json",
            '{"schema_version":1,"migrations":[]}',
        )
    assert module._artifact_requests_headless(artifact) is False


def test_recover_only_rejects_journal_injected_managed_destination(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = layout.release_path("old")
    new = layout.release_path("new")
    (old / "install").mkdir(parents=True)
    (new / "install").mkdir(parents=True)
    protected = tmp_path / "protected.txt"
    protected.write_text("protected\n", encoding="utf-8")
    backup = layout.backup_dir / "new" / "000.bak"
    backup.parent.mkdir()
    backup.write_text("forged\n", encoding="utf-8")
    journal = UpdateJournal.create(layout.journal_path, release_id="new")
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    journal.update_metadata(
        previous_target=str(old),
        target_path=str(new),
        legacy_source=None,
        service_was_active=False,
        service_was_enabled=True,
        managed_backups=[
            {
                "destination": str(protected),
                "backup": str(backup),
                "existed": True,
                "mode": 0o600,
            }
        ],
    )
    journal.transition(UpdatePhase.SWITCHED)

    coordinator = UpdateCoordinator(
        layout,
        _BootService(),
        links=_Links(new, old),
        managed_files=(
            ManagedFile(
                "install/inkypi.service",
                tmp_path / "etc" / "inkypi.service",
                0o644,
            ),
        ),
    )

    with pytest.raises(UpdateFailed, match="allowlisted"):
        coordinator.recover(journal, defer_service_starts=True)

    assert protected.read_text(encoding="utf-8") == "protected\n"
    assert journal.phase is UpdatePhase.ROLLING_BACK
