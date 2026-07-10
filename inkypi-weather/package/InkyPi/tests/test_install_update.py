import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[2]
INSTALL_ROOT = PROJECT_ROOT / "install"
INSTALL_LIB = INSTALL_ROOT / "lib"
sys.path.insert(0, str(INSTALL_LIB))
sys.path.insert(0, str(INSTALL_ROOT))

from release_state import ReleaseLayout, UpdateJournal, UpdatePhase  # noqa: E402
from update_engine import (  # noqa: E402
    ArtifactError,
    ArtifactPreparer,
    ManagedFile,
    UpdateCoordinator,
    UpdateFailed,
    inspect_artifact,
)
from preflight import prepare_config_copy  # noqa: E402


class FakeLinks:
    def __init__(self, current, previous=None, fail_switch=False):
        self.targets = {"current": Path(current)}
        if previous is not None:
            self.targets["previous"] = Path(previous)
        self.fail_switch = fail_switch
        self.new_target = None

    def read(self, link):
        return self.targets.get(Path(link).name)

    def replace(self, target, link):
        name = Path(link).name
        target = Path(target)
        if name == "current" and self.fail_switch and target == self.new_target:
            self.fail_switch = False
            raise OSError("injected switch failure")
        self.targets[name] = target

    def remove(self, link):
        self.targets.pop(Path(link).name, None)


class FakeService:
    def __init__(self, fail_stage=None, *, enabled=False):
        self.fail_stage = fail_stage
        self.failed = False
        self.active = True
        self.enabled = enabled
        self.events = []

    def _fail_once(self, stage):
        if self.fail_stage == stage and not self.failed:
            self.failed = True
            raise RuntimeError(f"injected {stage} failure")

    def is_active(self):
        return self.active

    def is_enabled(self):
        return self.enabled

    def stop(self):
        self.events.append("stop")
        self._fail_once("stop")
        self.active = False

    def daemon_reload(self):
        self.events.append("daemon_reload")
        self._fail_once("daemon_reload")

    def start(self):
        self.events.append("start")
        self._fail_once("start")
        self.active = True

    def enable(self):
        self.events.append("enable")
        self._fail_once("enable")
        self.enabled = True

    def disable(self):
        self.events.append("disable")
        self.enabled = False

    def wait_ready(self, release_id):
        self.events.append(("ready", release_id))
        return self.fail_stage != "ready"


def _release(layout, name, unit_text):
    release = layout.release_path(name)
    (release / "install").mkdir(parents=True)
    (release / "install" / "inkypi.service").write_text(unit_text, encoding="utf-8")
    (release / "install" / "inkypi").write_text(f"launcher-{name}", encoding="utf-8")
    return release


def _prepared_journal(layout, release_id):
    journal = UpdateJournal.create(layout.journal_path, release_id=release_id)
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    return journal


def test_successful_activation_commits_target_release_and_managed_files(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = _release(layout, "old", "old-unit")
    new = _release(layout, "new", "new-unit")
    unit_target = tmp_path / "etc" / "inkypi.service"
    launcher_target = tmp_path / "bin" / "inkypi"
    unit_target.parent.mkdir(parents=True)
    launcher_target.parent.mkdir(parents=True)
    unit_target.write_text("old-unit", encoding="utf-8")
    launcher_target.write_text("launcher-old", encoding="utf-8")
    links = FakeLinks(old)
    links.new_target = new
    service = FakeService()
    journal = _prepared_journal(layout, "new")
    coordinator = UpdateCoordinator(
        layout,
        service,
        links=links,
        managed_files=(
            ManagedFile("install/inkypi.service", unit_target, 0o644),
            ManagedFile("install/inkypi", launcher_target, 0o755),
        ),
    )

    coordinator.activate(journal, new)

    assert links.read(layout.current_link) == new
    assert links.read(layout.previous_link) == old
    assert unit_target.read_text(encoding="utf-8") == "new-unit"
    assert launcher_target.read_text(encoding="utf-8") == "launcher-new"
    assert journal.phase is UpdatePhase.COMMITTED
    assert service.active
    assert service.events == [
        "stop",
        "daemon_reload",
        "enable",
        "start",
        ("ready", "new"),
    ]


@pytest.mark.parametrize(
    "stage",
    ["stop", "switch", "install", "daemon_reload", "enable", "start", "ready"],
)
def test_activation_failure_restores_old_release_unit_and_service(tmp_path, stage):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = _release(layout, "old", "old-unit")
    new = _release(layout, "new", "new-unit")
    unit_target = tmp_path / "etc" / "inkypi.service"
    unit_target.parent.mkdir(parents=True)
    unit_target.write_text("old-unit", encoding="utf-8")
    links = FakeLinks(old, fail_switch=stage == "switch")
    links.new_target = new
    service = FakeService(None if stage in {"switch", "install"} else stage)
    copy_calls = []

    def copy_file(source, destination, mode):
        copy_calls.append((source, destination, mode))
        if stage == "install" and len(copy_calls) == 1:
            raise OSError("injected managed-file failure")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(source).read_bytes())

    journal = _prepared_journal(layout, "new")
    coordinator = UpdateCoordinator(
        layout,
        service,
        links=links,
        managed_files=(ManagedFile("install/inkypi.service", unit_target, 0o644),),
        copy_file=copy_file,
    )

    with pytest.raises(UpdateFailed, match="rolled back"):
        coordinator.activate(journal, new)

    assert links.read(layout.current_link) == old
    assert unit_target.read_text(encoding="utf-8") == "old-unit"
    assert journal.phase is UpdatePhase.ROLLED_BACK
    assert service.active
    assert service.enabled is False


def test_artifact_hash_and_zip_paths_fail_before_release_switch(tmp_path):
    artifact = tmp_path / "release.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("../escape", "bad")
    actual = hashlib.sha256(artifact.read_bytes()).hexdigest()

    with pytest.raises(ArtifactError, match="SHA256"):
        inspect_artifact(artifact, "0" * 64)
    with pytest.raises(ArtifactError, match="unsafe archive path"):
        inspect_artifact(artifact, actual)


@pytest.mark.parametrize("failure", ["disk", "pip", "pip_check", "migration"])
def test_pre_switch_failure_cleans_candidate_without_touching_current(tmp_path, failure):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old = _release(layout, "old", "old-unit")
    links = FakeLinks(old)
    service = FakeService()
    artifact = tmp_path / "release.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("src/inkypi.py", "# candidate")
        archive.writestr("install/inkypi.service", "candidate-unit")
        archive.writestr("install/inkypi", "candidate-launcher")
        archive.writestr("install/inkypi-update", "# candidate-updater")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    inspection = inspect_artifact(artifact, digest)
    journal = UpdateJournal.create(layout.journal_path, release_id="candidate")
    journal.transition(UpdatePhase.DOWNLOADED)

    def disk_checker(_directory, _required):
        if failure == "disk":
            raise ArtifactError("injected disk failure")

    def run_command(command, **_kwargs):
        joined = " ".join(str(item) for item in command)
        if failure == "pip" and " pip install " in f" {joined} ":
            raise subprocess.CalledProcessError(1, command)
        if failure == "pip_check" and " pip check" in f" {joined}":
            raise subprocess.CalledProcessError(1, command)
        if failure == "migration" and "preflight.py" in joined:
            raise subprocess.CalledProcessError(1, command)

    preparer = ArtifactPreparer(
        layout,
        config_path=tmp_path / "device.json",
        run_command=run_command,
        disk_checker=disk_checker,
    )
    coordinator = UpdateCoordinator(layout, service, links=links)

    with pytest.raises((ArtifactError, subprocess.CalledProcessError)):
        preparer.prepare(inspection, "candidate", journal)
    coordinator.recover(journal)

    assert links.read(layout.current_link) == old
    assert not layout.staging_path("candidate").exists()
    assert not layout.release_path("candidate").exists()
    assert service.events == []


def test_preflight_config_migration_uses_copy_and_forces_mock_display(tmp_path):
    source = tmp_path / "device.json"
    source.write_text(
        json.dumps({"display_type": "epd7in3e", "startup": True, "name": "frame"}),
        encoding="utf-8",
    )
    destination = tmp_path / "probe" / "device_dev.json"

    prepare_config_copy(source, destination)

    assert json.loads(source.read_text(encoding="utf-8"))["display_type"] == "epd7in3e"
    migrated = json.loads(destination.read_text(encoding="utf-8"))
    assert migrated["display_type"] == "mock"
    assert migrated["startup"] is False
    assert migrated["name"] == "frame"


def test_preparer_publishes_only_after_all_candidate_checks_pass(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    artifact = tmp_path / "release.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("src/inkypi.py", "# candidate")
        archive.writestr("install/inkypi.service", "candidate-unit")
        archive.writestr("install/inkypi", "candidate-launcher")
        archive.writestr("install/inkypi-update", "# candidate-updater")
        archive.writestr("install/cli/inkypi-plugin", "# candidate-cli")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    inspection = inspect_artifact(artifact, digest)
    journal = UpdateJournal.create(layout.journal_path, release_id="candidate")
    journal.transition(UpdatePhase.DOWNLOADED)
    commands = []
    preparer = ArtifactPreparer(
        layout,
        config_path=tmp_path / "device.json",
        run_command=lambda command, **_kwargs: commands.append(command),
        disk_checker=lambda _directory, _required: None,
    )

    release = preparer.prepare(inspection, "candidate", journal)

    assert release == layout.release_path("candidate")
    assert (release / ".release-id").read_text(encoding="utf-8") == "candidate\n"
    assert (release / "cli" / "inkypi-plugin").is_file()
    assert not layout.staging_path("candidate").exists()
    assert len(commands) == 5
    pip_command = next(
        command for command in commands if command[1:4] == ["-m", "pip", "install"]
    )
    assert "--require-hashes" in pip_command
    assert "--no-deps" in pip_command
    assert "--no-compile" in pip_command
    assert "--no-cache-dir" not in pip_command
    install_index = commands.index(pip_command)
    check_index = next(
        index
        for index, command in enumerate(commands)
        if command[1:4] == ["-m", "pip", "check"]
    )
    preflight_index = next(
        index
        for index, command in enumerate(commands)
        if any("preflight.py" in str(item) for item in command)
    )
    assert install_index < check_index < preflight_index


def test_operations_scripts_are_strict_and_forbid_mutable_deployment_paths():
    for name in (
        "install.sh",
        "update.sh",
        "bootstrap.sh",
        "healthcheck.sh",
        "update_vendors.sh",
        "uninstall.sh",
    ):
        source = (INSTALL_ROOT / name).read_text(encoding="utf-8")
        assert "set -Eeuo pipefail" in source, name
        assert "|| true" not in source, name

    combined = "\n".join(
        (INSTALL_ROOT / name).read_text(encoding="utf-8")
        for name in ("install.sh", "update.sh", "update_vendors.sh")
    )
    assert "/master/" not in combined
    assert "refs/heads/master" not in combined
    assert "cdn.jsdelivr.net/npm/chart.js|" not in combined
    assert "unzip -o" not in combined

    deploy = (REPO_ROOT / "tools" / "epaperpod-deploy-zip.ps1").read_text(
        encoding="utf-8"
    )
    assert "Get-FileHash" in deploy
    assert "scp.exe" in deploy
    assert "StrictHostKeyChecking=yes" in deploy
    assert "inkypi-update" in deploy
    assert "wget" not in deploy
    assert "accept-new" not in deploy


def test_optional_packaged_driver_validation_returns_success_when_unset():
    installer = (INSTALL_ROOT / "install.sh").read_text(encoding="utf-8")

    assert '[[ -n "$WS_TYPE" ]] || return 0' in installer


def test_installed_launcher_exports_release_identity_and_update_entrypoint():
    launcher = (INSTALL_ROOT / "inkypi").read_text(encoding="utf-8")

    assert '.release-id' in launcher
    assert "INKYPI_RELEASE_ID" in launcher
    assert "$PROGRAM_PATH/install/bootstrap_admin.py" in launcher
    assert (INSTALL_ROOT / "inkypi-update").is_file()
