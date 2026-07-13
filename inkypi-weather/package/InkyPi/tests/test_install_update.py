import base64
import hashlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[2]
INSTALL_ROOT = PROJECT_ROOT / "install"
INSTALL_LIB = INSTALL_ROOT / "lib"
sys.path.insert(0, str(INSTALL_LIB))
sys.path.insert(0, str(INSTALL_ROOT))

from release_state import (  # noqa: E402
    ReleaseLayout,
    ReleaseStateError,
    UpdateJournal,
    UpdatePhase,
)
from update_engine import (  # noqa: E402
    ArtifactError,
    ArtifactPreparer,
    ManagedFile,
    UpdateCoordinator,
    UpdateFailed,
    _safe_remove_tree,
    inspect_artifact,
    prune_releases,
)
import update_engine as update_engine_module  # noqa: E402
from preflight import (  # noqa: E402
    PreflightError,
    REQUIRED_RELEASE_PATHS,
    prepare_config_copy,
    validate_release_tree,
)


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


def _is_isolated_pip_check(command):
    return (
        len(command) >= 5
        and command[1:4] == ["-I", "-S", "-c"]
        and "dependency_errors" in str(command[-1])
    )


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


def _set_directory_mtime(path, value):
    timestamp_ns = int(value) * 1_000_000_000
    os.utime(path, ns=(timestamp_ns, timestamp_ns))


def _journal_at_phase(layout, release_id, target, phase):
    journal = UpdateJournal.create(
        layout.journal_path,
        release_id=release_id,
        metadata={"target_path": str(target)},
    )
    paths = {
        UpdatePhase.CREATED: (),
        UpdatePhase.DOWNLOADED: (UpdatePhase.DOWNLOADED,),
        UpdatePhase.PREFLIGHTED: (
            UpdatePhase.DOWNLOADED,
            UpdatePhase.PREFLIGHTED,
        ),
        UpdatePhase.SWITCHED: (
            UpdatePhase.DOWNLOADED,
            UpdatePhase.PREFLIGHTED,
            UpdatePhase.SWITCHED,
        ),
        UpdatePhase.STARTING: (
            UpdatePhase.DOWNLOADED,
            UpdatePhase.PREFLIGHTED,
            UpdatePhase.SWITCHED,
            UpdatePhase.STARTING,
        ),
        UpdatePhase.HEALTHY: (
            UpdatePhase.DOWNLOADED,
            UpdatePhase.PREFLIGHTED,
            UpdatePhase.SWITCHED,
            UpdatePhase.STARTING,
            UpdatePhase.HEALTHY,
        ),
        UpdatePhase.COMMITTED: (
            UpdatePhase.DOWNLOADED,
            UpdatePhase.PREFLIGHTED,
            UpdatePhase.SWITCHED,
            UpdatePhase.STARTING,
            UpdatePhase.HEALTHY,
            UpdatePhase.COMMITTED,
        ),
        UpdatePhase.ROLLING_BACK: (
            UpdatePhase.DOWNLOADED,
            UpdatePhase.PREFLIGHTED,
            UpdatePhase.SWITCHED,
            UpdatePhase.ROLLING_BACK,
        ),
        UpdatePhase.ROLLED_BACK: (
            UpdatePhase.DOWNLOADED,
            UpdatePhase.PREFLIGHTED,
            UpdatePhase.SWITCHED,
            UpdatePhase.ROLLING_BACK,
            UpdatePhase.ROLLED_BACK,
        ),
        UpdatePhase.ROLLBACK_FAILED: (
            UpdatePhase.DOWNLOADED,
            UpdatePhase.PREFLIGHTED,
            UpdatePhase.SWITCHED,
            UpdatePhase.ROLLING_BACK,
            UpdatePhase.ROLLBACK_FAILED,
        ),
    }
    for destination in paths[phase]:
        journal.transition(destination)
    return journal


def _directory_symlink(target, link):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")


def _load_updater_module():
    loader = SourceFileLoader("inkypi_update_test_module", str(INSTALL_ROOT / "inkypi-update"))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def _updater_args(tmp_path):
    return SimpleNamespace(
        artifact=tmp_path / "release.zip",
        sha256="0" * 64,
        release_id="candidate",
        install_root=tmp_path / "opt",
        state_root=tmp_path / "state",
        config=tmp_path / "device.json",
        service_name="inkypi.service",
        systemctl="systemctl",
        health_url="http://127.0.0.1/readyz",
        health_timeout=1.0,
        python=sys.executable,
        unit_target=tmp_path / "etc" / "inkypi.service",
        launcher_target=tmp_path / "bin" / "inkypi",
        updater_target=tmp_path / "sbin" / "inkypi-update",
        legacy_root=tmp_path / "legacy",
    )


def _configure_successful_updater(monkeypatch, module, events, holders):
    class FakeLock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback):
            return False

    class Service:
        def __init__(self, **_kwargs):
            self.active = True

    class Coordinator:
        def __init__(self, _layout, service, **_kwargs):
            self.service = service
            self.links = {"current": "candidate", "previous": "old"}
            self.recover_calls = 0
            holders["coordinator"] = self

        def activate(self, journal, _release):
            events.append("activate")
            journal.transition(UpdatePhase.SWITCHED)
            journal.transition(UpdatePhase.STARTING)
            journal.transition(UpdatePhase.HEALTHY)
            journal.transition(UpdatePhase.COMMITTED)

        def recover(self, _journal):
            self.recover_calls += 1

    class Preparer:
        def __init__(self, layout, **_kwargs):
            self.layout = layout

        def prepare(self, _inspection, release_id, _journal):
            release = self.layout.release_path(release_id)
            release.mkdir(parents=True)
            return release

        def ensure_bootstrap_token(self, _release):
            return None

    monkeypatch.setattr(module, "UpdateLock", FakeLock)
    monkeypatch.setattr(module, "SystemdService", Service)
    monkeypatch.setattr(module, "UpdateCoordinator", Coordinator)
    monkeypatch.setattr(module, "ArtifactPreparer", Preparer)
    monkeypatch.setattr(
        module,
        "inspect_artifact",
        lambda _artifact, _sha256: SimpleNamespace(sha256="verified"),
    )
    monkeypatch.setattr(module, "_recover_existing", lambda _layout, _coordinator: None)


def test_prune_releases_keeps_current_and_previous_and_converges_to_two(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current = _release(layout, "current-release", "current")
    previous = _release(layout, "previous-release", "previous")
    stale = [
        _release(layout, f"stale-{index}", f"stale-{index}")
        for index in range(3)
    ]
    for index, release in enumerate((*stale, previous, current), start=1):
        _set_directory_mtime(release, index)

    removed = prune_releases(layout, FakeLinks(current, previous), keep=2)

    assert set(removed) == set(stale)
    assert {path.name for path in layout.releases_dir.iterdir()} == {
        current.name,
        previous.name,
    }


def test_prune_releases_same_current_and_previous_keeps_newest_fallback(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current = _release(layout, "current-release", "current")
    newest = _release(layout, "newest-fallback", "newest")
    stale = _release(layout, "stale", "stale")
    _set_directory_mtime(stale, 1)
    _set_directory_mtime(current, 2)
    _set_directory_mtime(newest, 3)

    removed = prune_releases(layout, FakeLinks(current, current), keep=1)

    assert removed == (stale,)
    assert {path.name for path in layout.releases_dir.iterdir()} == {
        current.name,
        newest.name,
    }


@pytest.mark.parametrize("phase", tuple(UpdatePhase))
def test_prune_releases_refuses_every_unarchived_journal_phase(tmp_path, phase):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current = _release(layout, "current-release", "current")
    target = _release(layout, "target-release", "target")
    stale = _release(layout, "stale", "stale")
    _journal_at_phase(layout, "target-release", target, phase)
    original = {path.name for path in layout.releases_dir.iterdir()}

    with pytest.raises(ReleaseStateError, match="journal"):
        prune_releases(layout, FakeLinks(current, current), keep=2)

    assert {path.name for path in layout.releases_dir.iterdir()} == original
    assert target.is_dir()
    assert stale.is_dir()


@pytest.mark.parametrize("unsafe_target", ["outside", "broken"])
def test_prune_releases_fails_closed_for_outside_or_broken_link_target(
    tmp_path, unsafe_target
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    releases = [
        _release(layout, f"release-{index}", f"release-{index}")
        for index in range(3)
    ]
    if unsafe_target == "outside":
        target = tmp_path / "outside-release"
        target.mkdir()
        sentinel = target / "sentinel.txt"
        sentinel.write_text("do not delete", encoding="utf-8")
    else:
        target = layout.releases_dir / "missing-release"
        sentinel = None
    original = {path.name for path in layout.releases_dir.iterdir()}

    with pytest.raises(ReleaseStateError, match="release link target"):
        prune_releases(layout, FakeLinks(target, releases[0]), keep=2)

    assert {path.name for path in layout.releases_dir.iterdir()} == original
    if sentinel is not None:
        assert sentinel.read_text(encoding="utf-8") == "do not delete"


def test_prune_releases_fails_closed_when_install_root_is_symlink(tmp_path):
    external_install = tmp_path / "external-install"
    external_releases = external_install / "releases"
    external_releases.mkdir(parents=True)
    protected = external_releases / "protected"
    protected.mkdir()
    install_link = tmp_path / "install-link"
    _directory_symlink(external_install, install_link)
    layout = ReleaseLayout(install_link, tmp_path / "state")
    layout.state_root.mkdir()

    with pytest.raises(ReleaseStateError, match="install root"):
        prune_releases(layout, FakeLinks(protected, protected), keep=2)

    assert protected.is_dir()


def test_prune_releases_fails_closed_when_releases_root_is_symlink(tmp_path):
    install_root = tmp_path / "opt"
    install_root.mkdir()
    external_releases = tmp_path / "external-releases"
    external_releases.mkdir()
    protected = external_releases / "protected"
    protected.mkdir()
    _directory_symlink(external_releases, install_root / "releases")
    layout = ReleaseLayout(install_root, tmp_path / "state")
    layout.state_root.mkdir()

    with pytest.raises(ReleaseStateError, match="releases root"):
        prune_releases(layout, FakeLinks(protected, protected), keep=2)

    assert protected.is_dir()


def test_prune_releases_fails_closed_when_release_child_is_symlink(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current = _release(layout, "current-release", "current")
    previous = _release(layout, "previous-release", "previous")
    stale = _release(layout, "stale", "stale")
    external = tmp_path / "external-release"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    _directory_symlink(external, layout.releases_dir / "linked-release")
    original = {path.name for path in layout.releases_dir.iterdir()}

    with pytest.raises(ReleaseStateError, match="symlink"):
        prune_releases(layout, FakeLinks(current, previous), keep=2)

    assert {path.name for path in layout.releases_dir.iterdir()} == original
    assert stale.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "do not delete"


def test_safe_remove_tree_refuses_symlinked_managed_root(tmp_path):
    external_root = tmp_path / "external-root"
    candidate = external_root / "candidate"
    candidate.mkdir(parents=True)
    sentinel = candidate / "sentinel.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    managed_root = tmp_path / "managed-root"
    _directory_symlink(external_root, managed_root)

    with pytest.raises(ReleaseStateError, match="managed root"):
        _safe_remove_tree(managed_root / "candidate", managed_root)

    assert sentinel.read_text(encoding="utf-8") == "do not delete"


def test_safe_remove_tree_rechecks_directory_identity_before_delete(
    tmp_path, monkeypatch
):
    managed_root = tmp_path / "managed-root"
    candidate = managed_root / "candidate"
    candidate.mkdir(parents=True)
    sentinel = candidate / "sentinel.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    validate = update_engine_module._validated_descendant_directory
    calls = 0

    def changed_identity(path, root, *, label):
        nonlocal calls
        calls += 1
        validated, identities = validate(path, root, label=label)
        if calls == 2:
            member, identity = identities[-1]
            changed = (identity[0], identity[1] + 1, identity[2])
            identities = (*identities[:-1], (member, changed))
        return validated, identities

    monkeypatch.setattr(
        update_engine_module,
        "_validated_descendant_directory",
        changed_identity,
    )

    with pytest.raises(ReleaseStateError, match="changed during validation"):
        _safe_remove_tree(candidate, managed_root)

    assert sentinel.read_text(encoding="utf-8") == "do not delete"


def test_updater_archives_committed_journal_before_pruning(
    tmp_path, monkeypatch
):
    module = _load_updater_module()
    events = []
    holders = {}
    _configure_successful_updater(monkeypatch, module, events, holders)

    def archive(_layout, journal):
        assert journal.phase is UpdatePhase.COMMITTED
        events.append("archive")
        journal.path.unlink()

    def prune(layout, _links):
        assert not layout.journal_path.exists()
        events.append("prune")

    monkeypatch.setattr(module, "archive_journal", archive)
    monkeypatch.setattr(module, "prune_releases", prune)

    result = module.run_update(_updater_args(tmp_path))

    assert result == 0
    assert events == ["activate", "archive", "prune"]


def test_updater_archive_failure_skips_prune_and_leaves_healthy_release_active(
    tmp_path, monkeypatch, capsys
):
    module = _load_updater_module()
    events = []
    holders = {}
    _configure_successful_updater(monkeypatch, module, events, holders)

    def archive(_layout, _journal):
        events.append("archive")
        raise OSError("archive unavailable")

    monkeypatch.setattr(module, "archive_journal", archive)
    monkeypatch.setattr(
        module,
        "prune_releases",
        lambda _layout, _links: events.append("prune"),
    )

    result = module.run_update(_updater_args(tmp_path))

    assert result != 0
    assert events == ["activate", "archive"]
    assert holders["coordinator"].service.active
    assert holders["coordinator"].recover_calls == 0
    assert "post-commit cleanup pending" in capsys.readouterr().err.lower()


def test_updater_prune_failure_preserves_archived_commit_and_active_links(
    tmp_path, monkeypatch, capsys
):
    module = _load_updater_module()
    events = []
    holders = {}
    _configure_successful_updater(monkeypatch, module, events, holders)

    def archive(_layout, journal):
        events.append("archive")
        journal.path.unlink()

    def prune(_layout, _links):
        events.append("prune")
        raise OSError("prune unavailable")

    monkeypatch.setattr(module, "archive_journal", archive)
    monkeypatch.setattr(module, "prune_releases", prune)

    result = module.run_update(_updater_args(tmp_path))

    assert result == 0
    assert events == ["activate", "archive", "prune"]
    assert holders["coordinator"].links == {
        "current": "candidate",
        "previous": "old",
    }
    assert holders["coordinator"].recover_calls == 0
    assert not (tmp_path / "state" / "update-state.json").exists()
    assert "release pruning warning" in capsys.readouterr().err.lower()


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


@pytest.mark.parametrize(
    "member_name",
    ("msyh.ttf", "nested/fonts/MSYHBD.TTC", "vendor/deep/MsYhL.TtF"),
)
def test_artifact_inspection_rejects_yahei_font_members_in_any_directory(
    tmp_path, member_name
):
    artifact = tmp_path / "release.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("src/inkypi.py", "# candidate")
        archive.writestr(member_name, "proprietary font")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    with pytest.raises(ArtifactError, match="YaHei font"):
        inspect_artifact(artifact, digest)


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
        if failure == "pip_check" and _is_isolated_pip_check(command):
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


@pytest.mark.parametrize(
    "missing_asset",
    (
        "src/static/styles/main.css",
        "src/static/scripts/dark_mode.js",
        "src/static/scripts/i18n.js",
        "src/static/scripts/image_modal.js",
        "src/static/scripts/refresh_settings_manager.js",
        "src/static/scripts/response_modal.js",
    ),
)
def test_preflight_rejects_release_missing_application_static_asset(
    tmp_path,
    missing_asset,
):
    release = tmp_path / "release"
    for relative_path in REQUIRED_RELEASE_PATHS:
        target = release / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "candidate\n" if relative_path != ".release-id" else "candidate",
            encoding="utf-8",
        )
    (release / missing_asset).unlink()

    with pytest.raises(PreflightError, match=re.escape(missing_asset)):
        validate_release_tree(release)


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
        archive.writestr("install/requirements.txt", "example==1.0\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    inspection = inspect_artifact(artifact, digest)
    journal = UpdateJournal.create(layout.journal_path, release_id="candidate")
    journal.transition(UpdatePhase.DOWNLOADED)
    calls = []

    def run_command(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] != ["-m", "venv"]:
            return
        staging_venv = Path(command[-1])
        (staging_venv / "bin").mkdir(parents=True, exist_ok=True)
        shutil.copy2(sys.executable, staging_venv / "bin" / "python")
        (staging_venv / "bin" / "example-tool").write_text(
            f"#!{staging_venv / 'bin' / 'python'}\n",
            encoding="utf-8",
        )
        (staging_venv / "pyvenv.cfg").write_text(
            f"command = python -m venv {staging_venv}\n",
            encoding="utf-8",
        )

    preparer = ArtifactPreparer(
        layout,
        config_path=tmp_path / "device.json",
        run_command=run_command,
        disk_checker=lambda _directory, _required: None,
    )

    release = preparer.prepare(inspection, "candidate", journal)

    assert release == layout.release_path("candidate")
    assert (release / ".release-id").read_text(encoding="utf-8") == "candidate\n"
    assert (release / "cli" / "inkypi-plugin").is_file()
    assert not layout.staging_path("candidate").exists()
    commands = [command for command, _kwargs in calls]
    assert len(commands) == 7
    pip_command = next(
        command for command in commands if command[1:4] == ["-m", "pip", "install"]
    )
    assert "--require-hashes" in pip_command
    assert "--no-deps" in pip_command
    assert "--no-compile" in pip_command
    assert "--no-cache-dir" not in pip_command
    pip_kwargs = calls[commands.index(pip_command)][1]
    pip_tmpdir = Path(pip_kwargs["env"]["TMPDIR"])
    assert pip_tmpdir.parent == layout.staging_path("candidate")
    assert pip_tmpdir.name.startswith("pip-")
    assert not pip_tmpdir.exists()
    published_venv = release / "venv_inkypi"
    published_tool = (published_venv / "bin" / "example-tool").read_text(
        encoding="utf-8"
    )
    published_config = (published_venv / "pyvenv.cfg").read_text(encoding="utf-8")
    assert str(published_venv) in published_tool
    assert str(published_venv) in published_config
    assert str(layout.staging_path("candidate")) not in published_tool
    assert str(layout.staging_path("candidate")) not in published_config
    install_index = commands.index(pip_command)
    check_index = next(
        index
        for index, command in enumerate(commands)
        if _is_isolated_pip_check(command)
    )
    preflight_index = next(
        index
        for index, command in enumerate(commands)
        if any("preflight.py" in str(item) for item in command)
    )
    assert install_index < check_index < preflight_index


def _compatible_venv_fixture(layout, tmp_path):
    current = layout.release_path("current-release")
    requirements = current / "install" / "requirements.txt"
    requirements.parent.mkdir(parents=True, exist_ok=True)
    requirements.write_bytes(b"example==1.0\n")

    venv = current / "venv_inkypi"
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sys.executable, python)
    tool = venv / "bin" / "example-tool"
    tool.write_text(f"#!{python}\nprint('ok')\n", encoding="utf-8")
    package = (
        venv
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
        / "example.py"
    )
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text("VALUE = 'source'\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text(
        f"home = {sys.base_prefix}\ncommand = python -m venv {venv}\n",
        encoding="utf-8",
    )

    candidate_requirements = tmp_path / "candidate" / "install" / "requirements.txt"
    candidate_requirements.parent.mkdir(parents=True, exist_ok=True)
    candidate_requirements.write_bytes(requirements.read_bytes())
    destination = tmp_path / "candidate" / "venv_inkypi"
    return current, venv, candidate_requirements, destination, package


def _write_example_distribution_record(site_packages, tool):
    metadata_dir = site_packages / "example-1.0.dist-info"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata = metadata_dir / "METADATA"
    metadata.write_text(
        "Metadata-Version: 2.1\nName: example\nVersion: 1.0\n",
        encoding="utf-8",
    )

    def record_value(path):
        payload = path.read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        return f"sha256={digest.decode('ascii')},{len(payload)}"

    (metadata_dir / "RECORD").write_text(
        "\n".join(
            (
                f"../../../bin/{tool.name},{record_value(tool)}",
                f"example-1.0.dist-info/METADATA,{record_value(metadata)}",
                "example-1.0.dist-info/RECORD,,",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def test_preparer_clones_compatible_current_venv_as_independent_tree(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, source_venv, requirements, destination, source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    calls = []
    preparer = ArtifactPreparer(
        layout,
        run_command=lambda command, **kwargs: calls.append((command, kwargs)),
        links=FakeLinks(current),
    )

    assert preparer._try_clone_current_venv(requirements, destination) is True

    destination_package = destination / source_package.relative_to(source_venv)
    destination_package.write_text("VALUE = 'candidate'\n", encoding="utf-8")
    assert source_package.read_text(encoding="utf-8") == "VALUE = 'source'\n"
    cloned_tool = (destination / "bin" / "example-tool").read_text(encoding="utf-8")
    assert str(destination / "bin" / "python") in cloned_tool
    assert str(source_venv / "bin" / "python") not in cloned_tool
    assert any(_is_isolated_pip_check(command) for command, _ in calls)


def test_preparer_relocates_stale_console_script_and_preserves_record_integrity(
    tmp_path,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, source_venv, requirements, destination, _source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    source_python = source_venv / "bin" / "python"
    shutil.copy2(sys.executable, source_python)
    stale_venv = layout.staging_path("old-release") / "release" / "venv_inkypi"
    source_tool = source_venv / "bin" / "example-tool"
    source_tool.write_text(
        f"#!{stale_venv / 'bin' / 'python'}\nprint('ok')\n",
        encoding="utf-8",
    )
    source_site_packages = (
        source_venv
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    _write_example_distribution_record(source_site_packages, source_tool)
    source_record = source_site_packages / "example-1.0.dist-info" / "RECORD"
    source_tool_before = source_tool.read_bytes()
    source_record_before = source_record.read_bytes()
    published_venv = layout.release_path("candidate") / "venv_inkypi"

    def run_command(command, **kwargs):
        if _is_isolated_pip_check(command):
            return
        if command[1:4] != ["-I", "-S", "-c"]:
            return
        subprocess.run(
            command,
            cwd=kwargs.get("cwd"),
            timeout=kwargs.get("timeout"),
            check=True,
            capture_output=True,
            text=True,
        )

    preparer = ArtifactPreparer(
        layout,
        run_command=run_command,
        links=FakeLinks(current),
    )

    assert (
        preparer._try_clone_current_venv(
            requirements,
            destination,
            published_destination=published_venv,
        )
        is True
    )

    relocated_tool = (destination / "bin" / "example-tool").read_text(
        encoding="utf-8"
    )
    assert str(published_venv / "bin" / "python") in relocated_tool
    assert str(stale_venv) not in relocated_tool
    assert source_tool.read_bytes() == source_tool_before
    assert source_record.read_bytes() == source_record_before


def test_preparer_rejects_clone_when_copied_environment_probe_fails(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, _source_venv, requirements, destination, _source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    probe_calls = 0

    def run_command(command, **_kwargs):
        nonlocal probe_calls
        if _is_isolated_pip_check(command):
            return
        if command[1:4] != ["-I", "-S", "-c"]:
            return
        probe_calls += 1
        if probe_calls == 2:
            raise subprocess.CalledProcessError(1, command)

    preparer = ArtifactPreparer(
        layout,
        run_command=run_command,
        links=FakeLinks(current),
    )

    with pytest.raises(subprocess.CalledProcessError):
        preparer._try_clone_current_venv(requirements, destination)

    assert probe_calls == 2


def test_preparer_does_not_clone_current_venv_when_dependency_lock_differs(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, _source_venv, requirements, destination, _source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    requirements.write_bytes(b"example==2.0\n")
    calls = []
    preparer = ArtifactPreparer(
        layout,
        run_command=lambda command, **kwargs: calls.append((command, kwargs)),
        links=FakeLinks(current),
    )

    assert preparer._try_clone_current_venv(requirements, destination) is False

    assert not destination.exists()
    assert calls == []


def test_preparer_rejects_unsupported_dependency_lock_syntax(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, _source_venv, requirements, destination, _source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    marker_lock = b'example==1.0 ; python_version >= "3.11"\n'
    (current / "install" / "requirements.txt").write_bytes(marker_lock)
    requirements.write_bytes(marker_lock)
    preparer = ArtifactPreparer(
        layout,
        run_command=lambda _command, **_kwargs: None,
        links=FakeLinks(current),
    )

    with pytest.raises(ArtifactError, match="unsupported syntax"):
        preparer._try_clone_current_venv(requirements, destination)

    assert not destination.exists()


def test_isolated_dependency_check_probe_is_valid_python(tmp_path):
    probe = update_engine_module._isolated_pip_check_probe(tmp_path / "site-packages")

    compile(probe, "<isolated-dependency-check>", "exec")


def test_preparer_rejects_current_venv_missing_locked_top_level_distribution_even_when_pip_check_passes(
    tmp_path,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current = layout.release_path("current-release")
    source_requirements = current / "install" / "requirements.txt"
    source_requirements.parent.mkdir(parents=True, exist_ok=True)
    source_requirements.write_bytes(b"definitely-missing-package==1.0\n")

    source_venv = current / "venv_inkypi"
    subprocess.run(
        [sys.executable, "-m", "venv", str(source_venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    source_python = source_venv / "bin" / "python"
    source_site_packages = (
        source_venv
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    if os.name == "nt":
        source_python.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_venv / "Scripts" / "python.exe", source_python)
        shutil.copytree(source_venv / "Lib" / "site-packages", source_site_packages)

    pip_check = subprocess.run(
        [str(source_python), "-m", "pip", "check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert pip_check.returncode == 0
    assert "No broken requirements found" in pip_check.stdout

    candidate_requirements = tmp_path / "candidate" / "install" / "requirements.txt"
    candidate_requirements.parent.mkdir(parents=True, exist_ok=True)
    candidate_requirements.write_bytes(source_requirements.read_bytes())
    destination = tmp_path / "candidate" / "venv_inkypi"
    preparer = ArtifactPreparer(layout, links=FakeLinks(current))

    assert (
        preparer._try_clone_current_venv(candidate_requirements, destination) is False
    )
    assert not destination.exists()


def test_preparer_rejects_current_venv_with_tampered_record_file_even_when_pip_check_passes(
    tmp_path,
):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current = layout.release_path("current-release")
    source_venv = current / "venv_inkypi"
    subprocess.run(
        [sys.executable, "-m", "venv", str(source_venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    source_python = source_venv / "bin" / "python"
    source_site_packages = (
        source_venv
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    if os.name == "nt":
        source_python.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_venv / "Scripts" / "python.exe", source_python)
        shutil.copytree(source_venv / "Lib" / "site-packages", source_site_packages)

    distributions = subprocess.run(
        [
            str(source_python),
            "-I",
            "-S",
            "-c",
            "import json,re;from importlib import metadata;"
            "normalize=lambda value:re.sub(r'[-_.]+','-',value).lower();"
            "print(json.dumps(sorted((normalize(item.metadata['Name']),item.version)"
            f" for item in metadata.distributions(path=[{str(source_site_packages)!r}]))))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    locked_versions = json.loads(distributions.stdout)
    assert any(name == "pip" for name, _version in locked_versions)
    requirements_text = "".join(
        f"{name}=={version}\n" for name, version in locked_versions
    )
    source_requirements = current / "install" / "requirements.txt"
    source_requirements.parent.mkdir(parents=True, exist_ok=True)
    source_requirements.write_text(requirements_text, encoding="utf-8")

    record_entry = subprocess.run(
        [
            str(source_python),
            "-I",
            "-S",
            "-c",
            "import json,re;from importlib import metadata;"
            "normalize=lambda value:re.sub(r'[-_.]+','-',value).lower();"
            "distribution=next(item for item in metadata.distributions("
            f"path=[{str(source_site_packages)!r}])"
            " if normalize(item.metadata['Name'])=='pip');"
            "item=next(item for item in distribution.files or ()"
            " if item.hash is not None and item.hash.mode=='sha256'"
            " and str(item).endswith('.py'));"
            "print(json.dumps({'relative':str(item),"
            "'hash':item.hash.value}))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    recorded = json.loads(record_entry.stdout)
    tampered_file = source_site_packages / recorded["relative"]
    with tampered_file.open("ab") as stream:
        stream.write(b"\n# tampered RECORD payload\n")
    if os.name == "nt":
        with (source_venv / "Lib" / "site-packages" / recorded["relative"]).open(
            "ab"
        ) as stream:
            stream.write(b"\n# tampered RECORD payload\n")
    actual_hash = base64.urlsafe_b64encode(
        hashlib.sha256(tampered_file.read_bytes()).digest()
    ).rstrip(b"=")
    assert actual_hash.decode("ascii") != recorded["hash"]

    pip_check = subprocess.run(
        [str(source_python), "-m", "pip", "check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert pip_check.returncode == 0
    assert "No broken requirements found" in pip_check.stdout

    candidate_requirements = tmp_path / "candidate" / "install" / "requirements.txt"
    candidate_requirements.parent.mkdir(parents=True, exist_ok=True)
    candidate_requirements.write_bytes(source_requirements.read_bytes())
    destination = tmp_path / "candidate" / "venv_inkypi"
    preparer = ArtifactPreparer(layout, links=FakeLinks(current))

    assert (
        preparer._try_clone_current_venv(candidate_requirements, destination) is False
    )
    assert not destination.exists()


def test_preparer_accepts_normal_system_python_symlink_in_current_venv(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, source_venv, requirements, destination, _source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    source_python = source_venv / "bin" / "python"
    source_python.unlink()
    try:
        source_python.symlink_to(Path(sys.executable).resolve())
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    preparer = ArtifactPreparer(
        layout,
        run_command=lambda _command, **_kwargs: None,
        links=FakeLinks(current),
    )

    assert preparer._try_clone_current_venv(requirements, destination) is True

    assert (destination / "bin" / "python").is_symlink()
    assert (destination / "bin" / "python").resolve() == Path(sys.executable).resolve()


def test_preparer_accepts_normal_chained_system_python_symlinks(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, source_venv, requirements, destination, _source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    source_python = source_venv / "bin" / "python"
    source_python.unlink()
    python3 = source_venv / "bin" / "python3"
    try:
        python3.symlink_to(Path(sys.executable).resolve())
        source_python.symlink_to("python3")
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    preparer = ArtifactPreparer(
        layout,
        run_command=lambda _command, **_kwargs: None,
        links=FakeLinks(current),
    )

    assert preparer._try_clone_current_venv(requirements, destination) is True

    assert (destination / "bin" / "python").resolve() == Path(sys.executable).resolve()


def test_preparer_rejects_python_symlink_to_untrusted_external_executable(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, source_venv, requirements, destination, _source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    source_python = source_venv / "bin" / "python"
    source_python.unlink()
    untrusted_python = tmp_path / "untrusted" / "python"
    untrusted_python.parent.mkdir()
    shutil.copy2(sys.executable, untrusted_python)
    try:
        source_python.symlink_to(untrusted_python)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    preparer = ArtifactPreparer(
        layout,
        run_command=lambda _command, **_kwargs: None,
        links=FakeLinks(current),
    )

    assert preparer._try_clone_current_venv(requirements, destination) is False

    assert not destination.exists()


def test_preparer_rejects_external_path_in_current_venv_pth_file(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, source_venv, requirements, destination, source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    external = tmp_path / "external-packages"
    external.mkdir()
    (source_package.parent / "external.pth").write_text(
        f"{external}\n",
        encoding="utf-8",
    )
    preparer = ArtifactPreparer(
        layout,
        run_command=lambda _command, **_kwargs: None,
        links=FakeLinks(current),
    )

    assert preparer._try_clone_current_venv(requirements, destination) is False

    assert not destination.exists()


def test_preparer_rejects_unrecorded_import_pth_without_executing_it(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, source_venv, requirements, destination, source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    sentinel = tmp_path / "pth-executed"
    (source_package.parent / "unrecorded.pth").write_text(
        f"import pathlib; pathlib.Path({str(sentinel)!r}).touch()\n",
        encoding="utf-8",
    )
    calls = []
    preparer = ArtifactPreparer(
        layout,
        run_command=lambda command, **kwargs: calls.append((command, kwargs)),
        links=FakeLinks(current),
    )

    assert preparer._try_clone_current_venv(requirements, destination) is False

    assert calls == []
    assert not sentinel.exists()
    assert not destination.exists()


def test_preparer_rejects_external_directory_symlink_in_current_venv(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, source_venv, requirements, destination, _source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    external = tmp_path / "external-packages"
    external.mkdir()
    link = source_venv / "lib" / "external-packages"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    preparer = ArtifactPreparer(
        layout,
        run_command=lambda _command, **_kwargs: None,
        links=FakeLinks(current),
    )

    assert preparer._try_clone_current_venv(requirements, destination) is False

    assert not destination.exists()


def test_prepare_uses_compatible_venv_without_reinstalling_locked_dependencies(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    current, source_venv, _requirements, _destination, source_package = (
        _compatible_venv_fixture(layout, tmp_path)
    )
    artifact = tmp_path / "release.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("src/inkypi.py", "# candidate")
        archive.writestr("install/inkypi.service", "candidate-unit")
        archive.writestr("install/inkypi", "candidate-launcher")
        archive.writestr("install/inkypi-update", "# candidate-updater")
        archive.writestr("install/cli/inkypi-plugin", "# candidate-cli")
        archive.writestr("install/requirements.txt", "example==1.0\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    inspection = inspect_artifact(artifact, digest)
    journal = UpdateJournal.create(layout.journal_path, release_id="candidate")
    journal.transition(UpdatePhase.DOWNLOADED)
    calls = []
    preparer = ArtifactPreparer(
        layout,
        config_path=tmp_path / "device.json",
        run_command=lambda command, **kwargs: calls.append((command, kwargs)),
        disk_checker=lambda _directory, _required: None,
        links=FakeLinks(current),
    )
    release = preparer.prepare(inspection, "candidate", journal)

    commands = [command for command, _kwargs in calls]
    assert not any(command[1:3] == ["-m", "venv"] for command in commands)
    assert not any(command[1:4] == ["-m", "pip", "install"] for command in commands)
    assert sum(_is_isolated_pip_check(command) for command in commands) == 2
    assert any("preflight.py" in " ".join(map(str, command)) for command in commands)
    cloned_package = (
        release / "venv_inkypi" / source_package.relative_to(source_venv)
    )
    assert cloned_package.read_text(encoding="utf-8") == "VALUE = 'source'\n"
    published_tool = (release / "venv_inkypi" / "bin" / "example-tool").read_text(
        encoding="utf-8"
    )
    assert str(release / "venv_inkypi" / "bin" / "python") in published_tool
    assert str(layout.staging_path("candidate")) not in published_tool
    assert str(source_venv) not in published_tool


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


def test_installer_honors_tmpdir_for_release_artifact():
    installer = (INSTALL_ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'mktemp -d "${TMPDIR:-/tmp}/inkypi-install.XXXXXX"' in installer


def test_optional_packaged_driver_validation_returns_success_when_unset():
    installer = (INSTALL_ROOT / "install.sh").read_text(encoding="utf-8")

    assert '[[ -n "$WS_TYPE" ]] || return 0' in installer


def test_installed_launcher_exports_release_identity_and_update_entrypoint():
    launcher = (INSTALL_ROOT / "inkypi").read_text(encoding="utf-8")

    assert '.release-id' in launcher
    assert "INKYPI_RELEASE_ID" in launcher
    assert "$PROGRAM_PATH/install/bootstrap_admin.py" in launcher
    assert (INSTALL_ROOT / "inkypi-update").is_file()


def test_installed_launchers_use_relocated_virtual_environment_binaries():
    launcher = (INSTALL_ROOT / "inkypi").read_text(encoding="utf-8")
    plugin_cli = (INSTALL_ROOT / "cli" / "inkypi-plugin").read_text(
        encoding="utf-8"
    )

    assert 'source "$VENV_PATH/bin/activate"' not in launcher
    assert 'exec "$VENV_PATH/bin/python" -u' in launcher
    assert (
        'exec "$VENV_PATH/bin/python" '
        '"$PROGRAM_PATH/install/bootstrap_admin.py" "$subcommand"'
    ) in launcher
    assert 'source "$VENV_PATH/bin/activate"' not in plugin_cli
    assert '"$VENV_PATH/bin/python" -m pip install -r "$REQ_FILE"' in plugin_cli
