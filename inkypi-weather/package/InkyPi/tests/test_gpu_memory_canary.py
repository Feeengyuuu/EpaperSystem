from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALL_ROOT = PROJECT_ROOT / "install"
INSTALL_LIB = INSTALL_ROOT / "lib"
sys.path.insert(0, str(INSTALL_LIB))

from host_migration import TrustedGpuMemoryCanary  # noqa: E402
import host_migration as host_migration_module  # noqa: E402
from release_state import ReleaseLayout  # noqa: E402


def _load_updater_module(name="inkypi_update_gpu_memory_canary_test_module"):
    loader = SourceFileLoader(name, str(INSTALL_ROOT / "inkypi-update"))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_gpu_memory_canary_cli_accepts_only_fixed_actions(monkeypatch):
    module = _load_updater_module()
    actions = []
    monkeypatch.setattr(
        module,
        "run_gpu_memory_canary",
        lambda action: actions.append(action) or 0,
    )

    assert module.main(["--gpu-memory-canary", "start"]) == 0
    assert module.main(["--gpu-memory-canary", "status"]) == 0
    assert module.main(["--gpu-memory-canary", "rollback"]) == 0
    assert actions == ["start", "status", "rollback"]

    with pytest.raises(SystemExit):
        module.main(["--gpu-memory-canary", "64"])
    with pytest.raises(SystemExit):
        module.main(["--gpu-memory-canary", "start", "--systemctl", "/tmp/x"])
    assert "--gpu-memory-canary {start,status,rollback}" in module.build_parser().format_help()


def _canary_fixture(tmp_path, *, runner=None, mounted=True):
    boot_root = tmp_path / "boot" / "firmware"
    boot_root.mkdir(parents=True)
    config = b"[all]\narm_64bit=1\n"
    (boot_root / "config.txt").write_bytes(config)
    model_path = tmp_path / "proc" / "device-tree" / "model"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"Raspberry Pi Zero 2 W Rev 1.0\x00")
    boot_id_path = tmp_path / "proc" / "boot_id"
    boot_id_path.parent.mkdir(parents=True, exist_ok=True)
    boot_id_path.write_text(
        "11111111-1111-4111-8111-111111111111\n",
        encoding="ascii",
    )
    tryboot_flag_path = tmp_path / "proc" / "tryboot"
    calls = []

    def default_runner(command, **kwargs):
        calls.append((tuple(command), kwargs))

    canary = TrustedGpuMemoryCanary(
        state_root=tmp_path / "state",
        boot_root=boot_root,
        board_model_path=model_path,
        boot_id_path=boot_id_path,
        tryboot_flag_path=tryboot_flag_path,
        identity={
            "release_id": "release-1",
            "updater_sha256": "a" * 64,
        },
        mount_validator=lambda path: mounted and Path(path) == boot_root,
        runner=runner or default_runner,
    )
    return canary, boot_root, config, calls


def test_start_persists_hash_bound_tryboot_before_fixed_reboot(tmp_path):
    canary, boot_root, config, calls = _canary_fixture(tmp_path)

    result = canary.start()

    config_sha256 = hashlib.sha256(config).hexdigest()
    tryboot = (boot_root / "tryboot.txt").read_text(encoding="ascii")
    assert tryboot == (
        "# Managed by InkyPi gpu_memory_32_tryboot_v1\n"
        "# release_id=release-1\n"
        f"# updater_sha256={'a' * 64}\n"
        f"# config_sha256={config_sha256}\n"
        "[all]\n"
        "include config.txt\n"
        "[all]\n"
        "gpu_mem=32\n"
    )
    state = json.loads((tmp_path / "state" / "gpu-memory-canary-v1.json").read_text())
    assert state == {
        "schema_version": 1,
        "canary": "gpu_memory_32_tryboot_v1",
        "phase": "armed",
        "release_id": "release-1",
        "updater_sha256": "a" * 64,
        "config_sha256": config_sha256,
        "tryboot_sha256": hashlib.sha256(tryboot.encode("ascii")).hexdigest(),
        "start_boot_id": "11111111-1111-4111-8111-111111111111",
        "phase_boot_id": "11111111-1111-4111-8111-111111111111",
    }
    assert calls == [
        (
            ("/usr/sbin/reboot", "0 tryboot"),
            {
                "check": True,
                "stdin": -3,
                "stdout": -3,
                "stderr": -3,
            },
        )
    ]
    assert result == {
        "canary": "gpu_memory_32_tryboot_v1",
        "status": "reboot_requested",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("wrong_board", "requires Raspberry Pi Zero 2 W"),
        ("not_mounted", "trusted mount"),
    ],
)
def test_start_requires_exact_zero_2_w_and_boot_mount(tmp_path, mutation, message):
    canary, boot_root, _config, calls = _canary_fixture(
        tmp_path,
        mounted=mutation != "not_mounted",
    )
    if mutation == "wrong_board":
        canary.board_model_path.write_bytes(b"Raspberry Pi 4 Model B Rev 1.5\x00")

    with pytest.raises(RuntimeError, match=message):
        canary.start()

    assert not (boot_root / "tryboot.txt").exists()
    assert not canary.state_path.exists()
    assert calls == []


def test_start_rejects_partition_level_tryboot_configuration(tmp_path):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    (boot_root / "autoboot.txt").write_text(
        "[all]\ntryboot_a_b=1\n",
        encoding="ascii",
    )

    with pytest.raises(RuntimeError, match="alternate tryboot configuration"):
        canary.start()

    assert not canary.state_path.exists()
    assert not (boot_root / "tryboot.txt").exists()
    assert calls == []


def test_status_reports_only_redacted_one_shot_boot_state(tmp_path):
    canary, _boot_root, _config, calls = _canary_fixture(tmp_path)
    canary.start()

    assert canary.status() == {
        "canary": "gpu_memory_32_tryboot_v1",
        "status": "armed",
    }

    canary.boot_id_path.write_text(
        "22222222-2222-4222-8222-222222222222\n",
        encoding="ascii",
    )
    canary.tryboot_flag_path.write_bytes((1).to_bytes(4, "big"))
    assert canary.status() == {
        "canary": "gpu_memory_32_tryboot_v1",
        "status": "testing",
    }

    canary.tryboot_flag_path.unlink()
    assert canary.status() == {
        "canary": "gpu_memory_32_tryboot_v1",
        "status": "auto_rolled_back",
    }
    assert len(calls) == 1


def test_rollback_removes_only_owned_tryboot_then_reboots_normally(tmp_path):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    canary.start()

    assert canary.rollback() == {
        "canary": "gpu_memory_32_tryboot_v1",
        "status": "reboot_requested",
    }
    assert not (boot_root / "tryboot.txt").exists()
    state = json.loads(canary.state_path.read_text(encoding="ascii"))
    assert state["phase"] == "rollback_pending_reboot"
    assert [call[0] for call in calls] == [
        ("/usr/sbin/reboot", "0 tryboot"),
        ("/usr/sbin/reboot",),
    ]

    canary.boot_id_path.write_text(
        "33333333-3333-4333-8333-333333333333\n",
        encoding="ascii",
    )
    assert canary.status() == {
        "canary": "gpu_memory_32_tryboot_v1",
        "status": "rollback_complete_pending_finalize",
    }
    assert canary.rollback() == {
        "canary": "gpu_memory_32_tryboot_v1",
        "status": "inactive",
    }
    assert not canary.state_path.exists()
    assert len(calls) == 2


def test_start_resumes_after_power_loss_between_preparing_state_and_tryboot(
    tmp_path,
    monkeypatch,
):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    real_replace = host_migration_module.os.replace

    def fail_tryboot_replace(source, destination):
        if Path(destination) == canary.tryboot_path:
            raise OSError("simulated power loss before tryboot rename")
        return real_replace(source, destination)

    monkeypatch.setattr(host_migration_module.os, "replace", fail_tryboot_replace)
    with pytest.raises(OSError, match="power loss"):
        canary.start()

    state = json.loads(canary.state_path.read_text(encoding="ascii"))
    assert state["phase"] == "preparing"
    assert not (boot_root / "tryboot.txt").exists()
    assert calls == []

    monkeypatch.setattr(host_migration_module.os, "replace", real_replace)
    assert canary.start()["status"] == "reboot_requested"
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "armed"
    assert len(calls) == 1


def test_start_resumes_after_power_loss_between_tryboot_and_armed_state(
    tmp_path,
    monkeypatch,
):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    real_replace = host_migration_module.os.replace
    state_replaces = 0

    def fail_second_state_replace(source, destination):
        nonlocal state_replaces
        if Path(destination) == canary.state_path:
            state_replaces += 1
            if state_replaces == 2:
                raise OSError("simulated power loss before armed state")
        return real_replace(source, destination)

    monkeypatch.setattr(host_migration_module.os, "replace", fail_second_state_replace)
    with pytest.raises(OSError, match="power loss"):
        canary.start()

    assert (boot_root / "tryboot.txt").is_file()
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "preparing"
    assert calls == []

    monkeypatch.setattr(host_migration_module.os, "replace", real_replace)
    assert canary.start()["status"] == "reboot_requested"
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "armed"
    assert len(calls) == 1


def test_start_resumes_if_fat_directory_fsync_fails_after_rename(
    tmp_path,
    monkeypatch,
):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    real_fsync_directory = host_migration_module._fsync_directory
    boot_fsync_attempts = 0

    def fail_boot_fsync(directory):
        nonlocal boot_fsync_attempts
        if Path(directory) == boot_root:
            boot_fsync_attempts += 1
            raise OSError("simulated FAT directory fsync failure")
        return real_fsync_directory(directory)

    monkeypatch.setattr(host_migration_module, "_fsync_directory", fail_boot_fsync)
    with pytest.raises(OSError, match="directory fsync"):
        canary.start()
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "preparing"
    assert (boot_root / "tryboot.txt").is_file()
    assert calls == []

    with pytest.raises(OSError, match="directory fsync"):
        canary.start()
    assert boot_fsync_attempts == 2
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "preparing"
    assert calls == []

    monkeypatch.setattr(
        host_migration_module,
        "_fsync_directory",
        real_fsync_directory,
    )
    assert canary.start()["status"] == "reboot_requested"
    assert len(calls) == 1


def test_start_retries_failed_reboot_but_not_a_completed_tryboot(tmp_path):
    reboot_calls = []

    def runner(command, **_kwargs):
        reboot_calls.append(tuple(command))
        if len(reboot_calls) == 1:
            raise OSError("simulated reboot failure")

    canary, _boot_root, _config, _calls = _canary_fixture(tmp_path, runner=runner)
    with pytest.raises(OSError, match="reboot failure"):
        canary.start()
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "armed"

    assert canary.start()["status"] == "reboot_requested"
    assert reboot_calls == [
        ("/usr/sbin/reboot", "0 tryboot"),
        ("/usr/sbin/reboot", "0 tryboot"),
    ]

    canary.boot_id_path.write_text(
        "44444444-4444-4444-8444-444444444444\n",
        encoding="ascii",
    )
    canary.tryboot_flag_path.write_bytes((1).to_bytes(4, "big"))
    assert canary.start()["status"] == "testing"
    assert len(reboot_calls) == 2


def test_updater_runner_refuses_active_update_journal(tmp_path):
    module = _load_updater_module("inkypi_update_gpu_journal_test_module")
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    layout.journal_path.write_text("{}\n", encoding="ascii")

    with pytest.raises(RuntimeError, match="active update journal"):
        module.run_gpu_memory_canary("start", layout=layout)


def test_status_never_bootstraps_an_uninstalled_layout(tmp_path):
    module = _load_updater_module("inkypi_update_gpu_read_only_status_module")
    layout = ReleaseLayout(tmp_path / "missing-opt", tmp_path / "missing-state")

    with pytest.raises(RuntimeError, match="installed layout is unavailable"):
        module.run_gpu_memory_canary("status", layout=layout)

    assert not layout.install_root.exists()
    assert not layout.state_root.exists()


def test_updater_runner_binds_controller_to_live_committed_attestation(
    tmp_path,
    monkeypatch,
    capsys,
):
    module = _load_updater_module("inkypi_update_gpu_attestation_test_module")
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    identities = []

    class Canary:
        def __init__(self, *, identity, state_root):
            identities.append((identity, Path(state_root)))

        def status(self):
            return {
                "canary": "gpu_memory_32_tryboot_v1",
                "status": "inactive",
            }

    monkeypatch.setattr(
        module,
        "_headless_capability_attested",
        lambda _layout: {
            "schema_version": 1,
            "capability": "headless_mode_v1",
            "release_id": "committed-release",
            "updater_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(module, "_new_gpu_memory_canary", Canary)

    assert module.run_gpu_memory_canary("status", layout=layout) == 0

    assert identities == [
        (
            {
                "release_id": "committed-release",
                "updater_sha256": "b" * 64,
            },
            layout.state_root,
        )
    ]
    assert json.loads(capsys.readouterr().out) == {
        "canary": "gpu_memory_32_tryboot_v1",
        "status": "inactive",
    }


def test_start_and_rollback_never_claim_foreign_or_symlink_tryboot(tmp_path):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    foreign = boot_root / "tryboot.txt"
    foreign.write_text("gpu_mem=16\n", encoding="ascii")

    with pytest.raises(RuntimeError, match="not owned"):
        canary.start()
    with pytest.raises(RuntimeError, match="without canary state"):
        canary.rollback()
    assert foreign.read_text(encoding="ascii") == "gpu_mem=16\n"

    foreign.unlink()
    target = tmp_path / "foreign-target"
    target.write_text("do not remove\n", encoding="ascii")
    try:
        foreign.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    with pytest.raises(RuntimeError, match="not owned"):
        canary.start()
    assert foreign.is_symlink()
    assert target.read_text(encoding="ascii") == "do not remove\n"
    assert calls == []


def test_hash_bound_state_refuses_config_or_identity_changes(tmp_path):
    canary, boot_root, _config, _calls = _canary_fixture(tmp_path)
    canary.start()
    tryboot_before = (boot_root / "tryboot.txt").read_bytes()

    (boot_root / "config.txt").write_text(
        "[all]\narm_64bit=0\n",
        encoding="ascii",
    )
    with pytest.raises(RuntimeError, match="config no longer matches"):
        canary.status()
    with pytest.raises(RuntimeError, match="config no longer matches"):
        canary.rollback()
    assert (boot_root / "tryboot.txt").read_bytes() == tryboot_before

    (boot_root / "config.txt").write_text(
        "[all]\narm_64bit=1\n",
        encoding="ascii",
    )
    canary.identity["updater_sha256"] = "c" * 64
    with pytest.raises(RuntimeError, match="identity no longer matches"):
        canary.rollback()
    assert (boot_root / "tryboot.txt").read_bytes() == tryboot_before


def test_capability_uses_only_files_already_managed_by_n_minus_one_updater():
    module = _load_updater_module("inkypi_update_gpu_n_minus_one_test_module")

    managed_sources = {
        item.source_relative for item in module._default_managed_files()
    }

    assert module.GPU_MEMORY_CANARY_STATE_NAME == host_migration_module.GPU_MEMORY_CANARY_STATE_NAME
    assert "install/inkypi-update" in managed_sources
    assert "install/lib/host_migration.py" in managed_sources
    assert not any("gpu" in source for source in managed_sources)


def test_updater_runner_rejects_forged_capability_attestation(
    tmp_path,
    monkeypatch,
):
    module = _load_updater_module("inkypi_update_gpu_forged_attestation_module")
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    monkeypatch.setattr(
        module,
        "_headless_capability_attested",
        lambda _layout: {
            "schema_version": 1,
            "capability": "forged_capability",
            "release_id": "committed-release",
            "updater_sha256": "d" * 64,
        },
    )

    with pytest.raises(RuntimeError, match="attestation is invalid"):
        module.run_gpu_memory_canary("status", layout=layout)


def test_normal_update_recovers_old_journal_then_refuses_active_canary(
    tmp_path,
    monkeypatch,
):
    module = _load_updater_module("inkypi_update_gpu_admission_test_module")
    args = SimpleNamespace(
        artifact=tmp_path / "must-not-be-inspected.zip",
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
        recovery_unit_target=tmp_path / "etc" / "inkypi-update-recover.service",
        launcher_target=tmp_path / "bin" / "inkypi",
        updater_target=tmp_path / "sbin" / "inkypi-update",
        legacy_root=tmp_path / "legacy",
    )
    layout = ReleaseLayout(args.install_root, args.state_root)
    layout.ensure()
    layout.journal_path.write_text("old journal\n", encoding="ascii")
    canary_state = layout.state_root / "gpu-memory-canary-v1.json"
    canary_state.write_text("malformed but fail closed\n", encoding="ascii")
    events = []

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

    def recover(recovery_layout, _coordinator):
        events.append("recover")
        recovery_layout.journal_path.unlink()

    monkeypatch.setattr(module, "UpdateLock", Lock)
    monkeypatch.setattr(module, "SystemdService", lambda **_kwargs: object())
    monkeypatch.setattr(module, "UpdateCoordinator", Coordinator)
    monkeypatch.setattr(module, "ArtifactPreparer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_recover_existing", recover)
    monkeypatch.setattr(
        module,
        "_bootstrap_attested_headless_capability",
        lambda *_args: events.append("bootstrap") or (),
    )
    monkeypatch.setattr(
        module,
        "inspect_artifact",
        lambda *_args: (_ for _ in ()).throw(AssertionError("artifact inspected")),
    )

    with pytest.raises(RuntimeError, match="GPU memory canary is active"):
        module.run_update(args)

    assert events == ["recover"]
    assert not layout.journal_path.exists()
    assert canary_state.read_text(encoding="ascii") == "malformed but fail closed\n"


def test_start_failure_before_preparing_state_never_creates_tryboot(
    tmp_path,
    monkeypatch,
):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    real_replace = host_migration_module.os.replace

    def fail_state_replace(source, destination):
        if Path(destination) == canary.state_path:
            raise OSError("simulated state persistence failure")
        return real_replace(source, destination)

    monkeypatch.setattr(host_migration_module.os, "replace", fail_state_replace)
    with pytest.raises(OSError, match="persistence failure"):
        canary.start()
    assert not canary.state_path.exists()
    assert not (boot_root / "tryboot.txt").exists()
    assert calls == []


def test_rollback_resumes_after_owned_tryboot_unlink_failure(
    tmp_path,
    monkeypatch,
):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    canary.start()
    real_unlink = Path.unlink
    failed = False

    def fail_owned_unlink(path, *args, **kwargs):
        nonlocal failed
        if Path(path) == canary.tryboot_path and not failed:
            failed = True
            raise OSError("simulated FAT unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_owned_unlink)
    with pytest.raises(OSError, match="unlink failure"):
        canary.rollback()
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "rolling_back"
    assert (boot_root / "tryboot.txt").is_file()

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert canary.rollback()["status"] == "reboot_requested"
    assert not (boot_root / "tryboot.txt").exists()
    assert [call[0] for call in calls][-1] == ("/usr/sbin/reboot",)


def test_rollback_resumes_when_pending_state_write_fails_after_file_removal(
    tmp_path,
    monkeypatch,
):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    canary.start()
    real_replace = host_migration_module.os.replace
    state_replaces = 0

    def fail_pending_state_replace(source, destination):
        nonlocal state_replaces
        if Path(destination) == canary.state_path:
            state_replaces += 1
            if state_replaces == 2:
                raise OSError("simulated pending-state persistence failure")
        return real_replace(source, destination)

    monkeypatch.setattr(host_migration_module.os, "replace", fail_pending_state_replace)
    with pytest.raises(OSError, match="pending-state"):
        canary.rollback()
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "rolling_back"
    assert not (boot_root / "tryboot.txt").exists()
    assert len(calls) == 1

    monkeypatch.setattr(host_migration_module.os, "replace", real_replace)
    assert canary.rollback()["status"] == "reboot_requested"
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "rollback_pending_reboot"
    assert len(calls) == 2


def test_rollback_resumes_if_fat_unlink_fsync_fails(tmp_path, monkeypatch):
    canary, boot_root, _config, calls = _canary_fixture(tmp_path)
    canary.start()
    real_fsync_directory = host_migration_module._fsync_directory
    boot_fsync_attempts = 0

    def fail_boot_fsync(directory):
        nonlocal boot_fsync_attempts
        if Path(directory) == boot_root:
            boot_fsync_attempts += 1
            raise OSError("simulated FAT unlink fsync failure")
        return real_fsync_directory(directory)

    monkeypatch.setattr(host_migration_module, "_fsync_directory", fail_boot_fsync)
    with pytest.raises(OSError, match="unlink fsync"):
        canary.rollback()
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "rolling_back"
    assert not (boot_root / "tryboot.txt").exists()

    with pytest.raises(OSError, match="unlink fsync"):
        canary.rollback()
    assert boot_fsync_attempts == 2
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "rolling_back"
    assert len(calls) == 1

    monkeypatch.setattr(
        host_migration_module,
        "_fsync_directory",
        real_fsync_directory,
    )
    assert canary.rollback()["status"] == "reboot_requested"
    assert len(calls) == 2


def test_rollback_retries_normal_reboot_and_finalizes_after_new_boot(tmp_path):
    reboot_calls = []

    def runner(command, **_kwargs):
        reboot_calls.append(tuple(command))
        if len(reboot_calls) == 2:
            raise OSError("simulated normal reboot failure")

    canary, _boot_root, _config, _calls = _canary_fixture(tmp_path, runner=runner)
    canary.start()
    with pytest.raises(OSError, match="normal reboot failure"):
        canary.rollback()
    assert json.loads(canary.state_path.read_text(encoding="ascii"))["phase"] == "rollback_pending_reboot"

    assert canary.rollback()["status"] == "reboot_requested"
    assert reboot_calls[-1] == ("/usr/sbin/reboot",)
    canary.boot_id_path.write_text(
        "55555555-5555-4555-8555-555555555555\n",
        encoding="ascii",
    )
    assert canary.rollback()["status"] == "inactive"
    assert not canary.state_path.exists()
