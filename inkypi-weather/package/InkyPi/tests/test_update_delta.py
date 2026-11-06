"""Device-side delta_v1 installs, hard-link venv clones and copy-on-write discipline.

A delta candidate shares inodes with the live release, which is also the
rollback target of the next round, so these tests prove the base release is
byte-for-byte and mode-for-mode unchanged after a delta install. POSIX modes
are tracked per inode by a fake on Windows hosts (InodeModes), which models
exactly the hazard: chmod on a shared inode changes every release using it.
"""

import base64
import hashlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from types import SimpleNamespace
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALL_ROOT = PROJECT_ROOT / "install"
sys.path.insert(0, str(INSTALL_ROOT / "lib"))

import release_archive  # noqa: E402
from release_state import ReleaseLayout, UpdateJournal, UpdatePhase  # noqa: E402
import update_engine  # noqa: E402
from update_engine import (  # noqa: E402
    ArtifactError,
    ArtifactPreparer,
    DeltaRefused,
    UpdateCoordinator,
    inspect_artifact,
)

BASE_ID = "deploy-20261105T180244Z-base-a2ac1dff"
TARGET_ID = "deploy-20261106T120000Z-next-b3bd2e00"
VENDOR = b"/* pinned vendor asset */\n"
VENDOR_SCRIPT = (
    "#!/usr/bin/env bash\nset -Eeuo pipefail\ndeclare -a VENDORS=(\n"
    f'  "Demo|https://cdn.example.invalid/demo.js|{hashlib.sha256(VENDOR).hexdigest()}|src/static/scripts/demo.js"\n'
    ")\n"
).encode()
BASE_FILES = {
    "src/inkypi.py": b"print('base')\n",
    "src/a.py": b"a = 1\n",
    "src/gone.py": b"gone = True\n",
    "src/static/app.css": b"body {}\n",
    "install/inkypi.service": b"[Unit]\n",
    "install/inkypi": b"#!/bin/bash\necho launcher\n",
    "install/inkypi-update": b"#!/usr/bin/env python3\n",
    "install/preflight.py": b"print('preflight')\n",
    "install/requirements.txt": b"example==1.0\n",
    "install/update_vendors.sh": VENDOR_SCRIPT,
    "install/cli/inkypi-plugin": b"#!/bin/bash\n",
}
CHANGED = {"src/a.py": b"a = 2\n", "src/new.py": b"new = True\n"}
REMOVED = ("src/gone.py",)
EXECUTABLES = ("install/inkypi", "install/inkypi-update", "install/preflight.py",
               "install/update_vendors.sh", "cli/inkypi-plugin")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def target_files():
    files = {name: data for name, data in BASE_FILES.items() if name not in REMOVED}
    files.update(CHANGED)
    return files


class InodeModes:
    """POSIX modes kept per inode, like the kernel does, on any host."""

    def __init__(self):
        self.modes = {}

    @staticmethod
    def key(path):
        info = os.lstat(path)
        return info.st_dev, info.st_ino

    def get(self, path):
        return self.modes.get(self.key(path), 0o644)

    def set(self, path, mode):
        self.modes[self.key(path)] = int(mode)


@pytest.fixture
def modes(monkeypatch):
    tracker = InodeModes()
    monkeypatch.setattr(update_engine, "_MODES_ENFORCED", True)
    monkeypatch.setattr(update_engine, "_file_mode", tracker.get)
    monkeypatch.setattr(update_engine, "_set_mode", tracker.set)
    return tracker


class Links:
    def __init__(self, current):
        self.targets = {"current": Path(current)}

    def read(self, link):
        return self.targets.get(Path(link).name)

    def replace(self, target, link):
        self.targets[Path(link).name] = Path(target)

    def remove(self, link):
        self.targets.pop(Path(link).name, None)


class Service:
    def __init__(self):
        self.events = []
        self.active = True

    def is_active(self):
        return self.active

    def is_enabled(self):
        return True

    def stop(self):
        self.events.append("stop")
        self.active = False

    def start(self):
        self.events.append("start")
        self.active = True

    def daemon_reload(self):
        self.events.append("daemon_reload")

    def wait_ready(self, release_id):
        self.events.append(("ready", release_id))
        return True


def record_value(path):
    payload = Path(path).read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return f"sha256={digest.decode('ascii')},{len(payload)}"


def make_base_release(layout, modes=None, *, vendor=VENDOR):
    """A committed release as a full install leaves it: package, generated files and venv."""
    release = layout.release_path(BASE_ID)
    for name, data in BASE_FILES.items():
        (release / name).parent.mkdir(parents=True, exist_ok=True)
        (release / name).write_bytes(data)
    (release / ".release-id").write_text(BASE_ID + "\n", encoding="utf-8")
    shutil.copytree(release / "install" / "cli", release / "cli")
    if vendor is not None:
        (release / "src/static/scripts").mkdir(parents=True)
        (release / "src/static/scripts/demo.js").write_bytes(vendor)
    venv = release / "venv_inkypi"
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, python)
    tool = venv / "bin" / "example-tool"
    tool.write_text(f"#!{python}\nprint('ok')\n", encoding="utf-8")
    site = venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    site.mkdir(parents=True)
    (site / "example.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    metadata = site / "example-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: example\nVersion: 1.0\n", encoding="utf-8")
    (metadata / "RECORD").write_text(
        f"../../../bin/example-tool,{record_value(tool)}\n"
        f"example-1.0.dist-info/METADATA,{record_value(metadata / 'METADATA')}\n"
        "example-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    (venv / "pyvenv.cfg").write_text(f"home = {sys.base_prefix}\ncommand = python -m venv {venv}\n",
                                     encoding="utf-8")
    if modes is not None:
        for name in EXECUTABLES:
            modes.set(release / name, 0o755)
        modes.set(tool, 0o755)
    return release


def make_delta(path, *, base_release=BASE_ID, target=None, changed=None, removed=REMOVED, payload=None,
               mode_map=None, overrides=None, extra_members=()):
    target = target_files() if target is None else target
    changed = sorted(CHANGED) if changed is None else changed
    manifest = {"format": "inkypi-delta-v1", "base_commit": "a" * 40, "base_release": base_release,
                "target_commit": "b" * 40, "hashes": {name: sha(data) for name, data in target.items()},
                "modes": {name: (mode_map or {}).get(name, "0644") for name in target},
                "removed": sorted(removed), "changed": changed}
    manifest.update(overrides or {})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(update_engine.DELTA_MANIFEST_MEMBER, json.dumps(manifest))
        for name in changed:
            archive.writestr(update_engine.DELTA_PAYLOAD_PREFIX + name, (payload or {}).get(name, target.get(name, b"")))
        for name, data in extra_members:
            archive.writestr(name, data)
    return inspect_artifact(path, sha(Path(path).read_bytes()))


def snapshot(root, modes=None):
    """Every entry below ``root``: content hash, real mode, tracked mode and inode."""
    entries = {}
    for path in sorted(Path(root).rglob("*")):
        info = os.lstat(path)
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(info.st_mode):
            entries[relative] = ("link", os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            entries[relative] = ("dir", stat.S_IMODE(info.st_mode))
        else:
            entries[relative] = ("file", sha(path.read_bytes()), stat.S_IMODE(info.st_mode),
                                 modes.get(path) if modes else None, info.st_ino, info.st_size, info.st_mtime_ns)
    return entries


def fake_vendor_script(candidate):
    """What install/update_vendors.sh does: keep a pinned file with its hash, else replace it."""
    for relative, expected in update_engine._vendor_pins(candidate / "install" / "update_vendors.sh"):
        output = candidate / relative
        if output.is_file() and not output.is_symlink() and sha(output.read_bytes()) == expected:
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(".vendor.tmp")
        temporary.write_bytes(VENDOR)
        os.replace(temporary, output)


def runner(calls, downloads=None):
    def run_command(command, **kwargs):
        calls.append((command, kwargs))
        if command[:1] == ["bash"]:
            candidate = Path(command[1]).parents[1]
            before = {path for path in candidate.rglob("*") if path.is_file()}
            fake_vendor_script(candidate)
            if downloads is not None:
                downloads.extend(sorted({path for path in candidate.rglob("*") if path.is_file()} - before))
        elif any("preflight.py" in str(item) for item in command):
            root = Path(command[command.index("--release-root") + 1])
            (root / "install" / ".nasapics-space-weather-v1.expectation.json").write_text("{}", encoding="utf-8")
    return run_command


def prepared_delta(tmp_path, monkeypatch, modes, **delta_options):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    base = make_base_release(layout, modes)
    inspection = make_delta(tmp_path / "delta.zip", **delta_options)
    journal = UpdateJournal.create(layout.journal_path, release_id=TARGET_ID)
    journal.transition(UpdatePhase.DOWNLOADED)
    calls, lines, required = [], [], []
    preparer = ArtifactPreparer(layout, config_path=tmp_path / "device.json", run_command=runner(calls),
                                disk_checker=lambda _root, size: required.append(size), links=Links(base),
                                artifact_format="delta_v1", progress=lines.append)
    return SimpleNamespace(layout=layout, base=base, inspection=inspection, journal=journal, calls=calls,
                           lines=lines, required=required, preparer=preparer)


# --- the base release survives a delta install --------------------------------------------------------------


def test_delta_install_leaves_the_base_release_byte_and_mode_identical(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes)
    before = snapshot(case.base, modes)

    release = case.preparer.prepare(case.inspection, TARGET_ID, case.journal)

    assert snapshot(case.base, modes) == before
    assert release == case.layout.release_path(TARGET_ID)
    assert not case.layout.staging_path(TARGET_ID).exists()
    for name, data in target_files().items():
        assert (release / name).read_bytes() == data
    assert not (release / "src/gone.py").exists()
    assert (release / ".release-id").read_text(encoding="utf-8") == TARGET_ID + "\n"
    # Unchanged package files, the seeded vendor asset and venv payloads are shared inodes.
    for name in ("src/inkypi.py", "src/static/app.css", "install/requirements.txt",
                 "src/static/scripts/demo.js", "venv_inkypi/bin/python", "venv_inkypi/lib"):
        if (release / name).is_file():
            assert os.path.samefile(release / name, case.base / name), name
    site = f"venv_inkypi/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    assert os.path.samefile(release / site / "example.py", case.base / site / "example.py")
    # Everything a later step rewrote, or whose mode differs, is a new inode.
    for name in ("src/a.py", "venv_inkypi/bin/example-tool", "venv_inkypi/pyvenv.cfg",
                 site + "/example-1.0.dist-info/RECORD", *EXECUTABLES):
        assert not os.path.samefile(release / name, case.base / name), name
    assert str(release / "venv_inkypi") in (release / "venv_inkypi/bin/example-tool").read_text(encoding="utf-8")
    assert str(case.base) in (case.base / "venv_inkypi/bin/example-tool").read_text(encoding="utf-8")
    for name in EXECUTABLES:
        assert modes.get(release / name) == 0o755 and modes.get(case.base / name) == 0o755
    assert modes.get(release / "src/inkypi.py") == 0o644


def test_delta_install_reports_one_timing_line_per_phase(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes)

    case.preparer.prepare(case.inspection, TARGET_ID, case.journal)

    phases = [line.split()[1] for line in case.lines]
    assert phases == ["delta-apply", "delta-verify", "vendors", "venv-check", "venv-clone", "venv-finalize",
                      "preflight", "fsync", "publish"]
    assert all(line.startswith("timing: ") and len(line) < 100 for line in case.lines)
    apply_line = case.lines[0]
    assert " written=2 " in apply_line and " removed=1" in apply_line and " linked=" in apply_line
    assert case.lines[1].endswith(f" files={len(target_files())}")
    assert case.lines[2].endswith(" seeded=1") and case.lines[3].endswith(" reuse=yes")
    metadata = case.journal.metadata
    assert metadata["artifact_format"] == "delta_v1" and metadata["delta_base_release"] == BASE_ID
    assert (metadata["delta_changed_files"], metadata["delta_removed_files"]) == (2, 1)


def test_delta_disk_requirement_counts_new_bytes_not_linked_ones(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes)
    case.preparer.prepare(case.inspection, TARGET_ID, case.journal)
    payload = sum(len(data) for data in CHANGED.values())

    assert case.required == [payload * 2 + update_engine.DELTA_OVERHEAD_BYTES]
    plan = SimpleNamespace(payload_bytes=payload, hashes={"install/requirements.txt": "0" * 64})
    assert update_engine.delta_disk_requirement(plan, case.layout.release_path(TARGET_ID)) == (
        payload * 2 + update_engine.DELTA_OVERHEAD_BYTES + update_engine.FRESH_VENV_RESERVE_BYTES)


def test_activation_reports_stop_switch_start_and_wait_ready(tmp_path):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    old, new = layout.release_path("old"), layout.release_path("new")
    for release in (old, new):
        (release / "install").mkdir(parents=True)
        (release / "install" / "inkypi.service").write_text(release.name, encoding="utf-8")
    journal = UpdateJournal.create(layout.journal_path, release_id="new")
    journal.transition(UpdatePhase.DOWNLOADED)
    journal.transition(UpdatePhase.PREFLIGHTED)
    lines = []
    coordinator = UpdateCoordinator(layout, Service(), links=Links(old), progress=lines.append)

    coordinator.activate(journal, new)

    assert [line.split()[1] for line in lines] == ["stop", "switch", "start", "wait-ready", "activation"]
    assert lines[3].endswith(" ready=yes") and journal.phase is UpdatePhase.COMMITTED


# --- refusals happen before anything outside staging changes ------------------------------------------------


def refused(case, match):
    before = snapshot(case.base)
    with pytest.raises(DeltaRefused, match=match):
        case.preparer.prepare(case.inspection, TARGET_ID, case.journal)
    assert snapshot(case.base) == before
    assert not case.layout.release_path(TARGET_ID).exists()
    assert not any(command[:1] == ["bash"] for command, _ in case.calls)
    return case


def test_delta_refuses_an_active_release_that_is_not_its_base(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes, base_release="deploy-20261101T000000Z-other-12345678")
    refused(case, "is not the delta base")
    assert not case.layout.staging_path(TARGET_ID).exists()


def test_delta_refuses_a_base_whose_identity_file_disagrees(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes)
    (case.base / ".release-id").write_text("deploy-other\n", encoding="utf-8")
    refused(case, "identity does not match")


def test_delta_refuses_a_payload_that_does_not_match_the_target_hash(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes, payload={"src/a.py": b"a = 3\n"})
    refused(case, "src/a.py does not match its target sha256")


def test_delta_refuses_a_base_file_that_differs_from_the_target(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes)
    (case.base / "src/static/app.css").write_bytes(b"body { color: red; }\n")
    refused(case, "src/static/app.css does not match its target sha256")


def test_delta_refuses_a_missing_base_file(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes)
    (case.base / "src/inkypi.py").unlink()
    refused(case, "the base release lacks src/inkypi.py")


def test_delta_refuses_extra_archive_members(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes, extra_members=(("delta/files/src/extra.py", b"x"),))
    refused(case, "payload does not match its manifest")
    other = prepared_delta(tmp_path / "other", monkeypatch, modes, extra_members=(("src/inkypi.py", b"x"),))
    refused(other, "unexpected archive member src/inkypi.py")


def test_delta_refuses_a_symlinked_base_directory(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes)
    outside = tmp_path / "outside"
    shutil.move(str(case.base / "src" / "static"), outside)
    try:
        (case.base / "src" / "static").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    refused(case, "src/static in the base release is not a directory")


def test_verification_refuses_unlisted_and_missing_candidate_files(tmp_path, modes):
    root = tmp_path / "candidate"
    files = target_files()
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    plan = SimpleNamespace(hashes={name: sha(data) for name, data in files.items()},
                           modes={name: 0o644 for name in files})
    assert update_engine.verify_package_tree(root, plan) == len(files)

    (root / "src" / "planted.py").write_bytes(b"import os\n")
    with pytest.raises(DeltaRefused, match="unlisted file src/planted.py"):
        update_engine.verify_package_tree(root, plan)
    (root / "src" / "planted.py").unlink()
    (root / "src" / "new.py").unlink()
    with pytest.raises(DeltaRefused, match="1 target files are missing, e.g. src/new.py"):
        update_engine.verify_package_tree(root, plan)


def test_a_failed_delta_leaves_both_pointers_and_the_service_untouched(tmp_path, monkeypatch, modes):
    module = load_updater()
    case = prepared_delta(tmp_path, monkeypatch, modes, payload={"src/new.py": b"tampered"})
    service = Service()
    links = case.preparer.links
    coordinator = UpdateCoordinator(case.layout, service, links=links)
    before = snapshot(case.base)

    with pytest.raises(DeltaRefused) as error:
        case.preparer.prepare(case.inspection, TARGET_ID, case.journal)
    module._record_pre_switch_failure(case.layout, coordinator, case.journal, error.value)

    assert links.targets == {"current": case.base} and service.events == []
    assert snapshot(case.base) == before
    assert not case.layout.staging_path(TARGET_ID).exists() and not case.layout.journal_path.exists()
    assert sorted(case.layout.release_path(TARGET_ID).parent.iterdir()) == [case.base]


# --- modes: full-ZIP semantics without chmod on a shared inode ----------------------------------------------


def test_a_mode_that_differs_from_the_base_gets_a_private_copy(tmp_path, monkeypatch, modes):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    base = make_base_release(layout, modes)
    modes.set(base / "src/static/app.css", 0o755)
    inspection = make_delta(tmp_path / "delta.zip", mode_map={"src/inkypi.py": "0755"})
    plan = update_engine.read_delta_manifest(inspection)

    counts = update_engine.apply_delta(inspection, plan, base, tmp_path / "candidate")
    update_engine.verify_package_tree(tmp_path / "candidate", plan)

    candidate = tmp_path / "candidate"
    for name, base_mode in (("src/static/app.css", 0o755), ("src/inkypi.py", 0o644), ("install/inkypi", 0o755)):
        assert not os.path.samefile(candidate / name, base / name)
        assert modes.get(base / name) == base_mode
    assert modes.get(candidate / "src/static/app.css") == 0o644 and modes.get(candidate / "src/inkypi.py") == 0o755
    assert os.path.samefile(candidate / "install/requirements.txt", base / "install/requirements.txt")
    assert counts["copied"] == 2 + len([name for name in EXECUTABLES if not name.startswith("cli/")])


def test_ensure_mode_breaks_a_hard_link_instead_of_chmodding_it(tmp_path, modes):
    shared, link = tmp_path / "shared", tmp_path / "link"
    shared.write_bytes(b"payload")
    os.link(shared, link)
    modes.set(shared, 0o644)

    assert update_engine._ensure_mode_copy_on_write(link, 0o755) is True

    assert not os.path.samefile(shared, link) and link.read_bytes() == b"payload"
    assert modes.get(shared) == 0o644 and modes.get(link) == 0o755
    assert update_engine._ensure_mode_copy_on_write(link, 0o755) is False
    single = tmp_path / "single"
    single.write_bytes(b"x")
    inode = os.lstat(single).st_ino
    assert update_engine._ensure_mode_copy_on_write(single, 0o755) is True
    assert os.lstat(single).st_ino == inode and modes.get(single) == 0o755


# --- venv: hard-link clone, relocation writes new inodes ------------------------------------------------------


def test_full_artifacts_also_clone_the_venv_as_links_without_touching_the_source(tmp_path, monkeypatch, modes):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    base = make_base_release(layout, modes)
    artifact = tmp_path / "full.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        for name, data in target_files().items():
            archive.writestr(name, data)
    journal = UpdateJournal.create(layout.journal_path, release_id=TARGET_ID)
    journal.transition(UpdatePhase.DOWNLOADED)
    lines = []
    preparer = ArtifactPreparer(layout, config_path=tmp_path / "device.json", run_command=runner([]),
                                disk_checker=lambda *_args: None, links=Links(base), progress=lines.append)
    before = snapshot(base / "venv_inkypi", modes)

    release = preparer.prepare(inspect_artifact(artifact, sha(artifact.read_bytes())), TARGET_ID, journal)

    assert snapshot(base / "venv_inkypi", modes) == before
    assert [line.split()[1] for line in lines][:2] == ["extract", "vendors"]
    assert os.path.samefile(release / "venv_inkypi/bin/python", base / "venv_inkypi/bin/python")
    assert not os.path.samefile(release / "venv_inkypi/bin/example-tool", base / "venv_inkypi/bin/example-tool")
    assert modes.get(release / "venv_inkypi/bin/example-tool") == 0o755
    assert "linked=" in next(line for line in lines if line.split()[1] == "venv-clone")


def test_link_tree_recreates_symlinks_and_directories_without_following_them(tmp_path):
    source = tmp_path / "source"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "module.py").write_bytes(b"x = 1\n")
    try:
        (source / "alias").symlink_to("pkg", target_is_directory=True)
        (source / "python").symlink_to(sys.executable)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")

    counts = update_engine._link_tree(source, tmp_path / "copy")

    assert counts == {"linked": 1, "copied": 0}
    assert os.readlink(tmp_path / "copy" / "alias") == "pkg" and (tmp_path / "copy" / "alias").is_symlink()
    assert os.readlink(tmp_path / "copy" / "python") == sys.executable
    assert os.path.samefile(tmp_path / "copy" / "pkg" / "module.py", source / "pkg" / "module.py")
    assert update_engine._venv_metadata_digest(tmp_path / "copy") == update_engine._venv_metadata_digest(source)


def test_link_or_copy_falls_back_to_a_new_file_when_linking_fails(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"payload")

    def cross_device(*_args, **_kwargs):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(update_engine.os, "link", cross_device)
    assert update_engine._link_or_copy(source, tmp_path / "copy") == "copied"
    assert (tmp_path / "copy").read_bytes() == b"payload"
    assert not os.path.samefile(source, tmp_path / "copy")
    with pytest.raises(FileExistsError):
        update_engine._link_or_copy(source, tmp_path / "copy")


# --- vendor seeding --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("vendor, seeded", [(VENDOR, 1), (b"stale vendor\n", 0), (None, 0)])
def test_vendor_assets_are_seeded_only_when_they_match_their_pin(tmp_path, modes, vendor, seeded):
    layout = ReleaseLayout(tmp_path / "opt", tmp_path / "state")
    layout.ensure()
    base = make_base_release(layout, modes, vendor=vendor)
    candidate = tmp_path / "candidate"
    for name, data in target_files().items():
        (candidate / name).parent.mkdir(parents=True, exist_ok=True)
        (candidate / name).write_bytes(data)
    preparer = ArtifactPreparer(layout, links=Links(base), run_command=lambda *_a, **_k: None)

    assert preparer._seed_vendor_assets(candidate) == seeded

    asset = candidate / "src/static/scripts/demo.js"
    assert asset.exists() == bool(seeded)
    if seeded:
        assert os.path.samefile(asset, base / "src/static/scripts/demo.js")
        assert modes.get(asset) == 0o644
    downloads = []
    runner([], downloads)(["bash", str(candidate / "install" / "update_vendors.sh")])
    assert asset.read_bytes() == VENDOR and len(downloads) == (0 if seeded else 1)
    if vendor is not None:
        assert (base / "src/static/scripts/demo.js").read_bytes() == vendor


def test_the_vendor_script_verifies_a_present_pinned_file_before_downloading():
    script = (INSTALL_ROOT / "update_vendors.sh").read_text(encoding="utf-8")
    check = script.index('if [[ -f "$output" && ! -L "$output" ]]; then')
    assert check < script.index("curl") and 'echo "Verified $relative_output (already present)"' in script
    assert 'mv -f "$temporary" "$output"' in script
    assert len(update_engine._vendor_pins(INSTALL_ROOT / "update_vendors.sh")) == 5


# --- manifest validation ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["../escape.py", "/etc/passwd", "src//a.py", "src/./a.py", "src\\a.py",
                                  "venv_inkypi/bin/python", ".release-id", "cli/inkypi-plugin",
                                  "src/__pycache__/a.cpython-311.pyc", "install/.release-migrations.json",
                                  "fonts/msyh.ttc", "c:/windows.py"])
def test_manifest_paths_must_be_plain_package_files(tmp_path, name):
    target = {**target_files(), name: b"x"}
    with pytest.raises(DeltaRefused):
        update_engine.read_delta_manifest(make_delta(tmp_path / "delta.zip", target=target))


@pytest.mark.parametrize("overrides, match", [
    ({"format": "inkypi-delta-v2"}, "unsupported delta format"),
    ({"base_commit": "abc"}, "base_commit is not a full commit id"),
    ({"base_release": "../x"}, "base_release is not a release id"),
    ({"extra": 1}, "missing or unexpected keys"),
    ({"removed": ["src/a.py"]}, "removed path is still part of the target"),
    ({"removed": ["src/gone.py", "src/gone.py"]}, "removed repeats a path"),
    ({"full_artifact_sha256": "xyz"}, "full_artifact_sha256 is malformed"),
    ({"hashes": {}}, "empty or too large"),
])
def test_manifest_fields_are_validated(tmp_path, overrides, match):
    with pytest.raises(DeltaRefused, match=match):
        update_engine.read_delta_manifest(make_delta(tmp_path / "delta.zip", overrides=overrides))


def test_manifest_modes_changes_and_clashes_are_validated(tmp_path):
    with pytest.raises(DeltaRefused, match="0644 or 0755"):
        update_engine.read_delta_manifest(make_delta(tmp_path / "a.zip", mode_map={"src/a.py": "0777"}))
    with pytest.raises(DeltaRefused, match="changed path is not part of the target"):
        update_engine.read_delta_manifest(make_delta(tmp_path / "b.zip", changed=["src/a.py", "src/zzz.py"]))
    with pytest.raises(DeltaRefused, match="src/static is both a file and a directory"):
        update_engine.read_delta_manifest(make_delta(tmp_path / "c.zip", target={**target_files(),
                                                                                 "src/static": b"x"}))
    path = tmp_path / "d.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(update_engine.DELTA_MANIFEST_MEMBER, '{"format": 1, "format": 2}')
    with pytest.raises(DeltaRefused, match="not strict JSON"):
        update_engine.read_delta_manifest(inspect_artifact(path, sha(path.read_bytes())))


def test_migration_control_names_match_the_release_archive_policy():
    assert update_engine.DELTA_MIGRATION_CONTROL_NAMES == release_archive.MIGRATION_CONTROL_NAMES


# --- format negotiation and older engines ---------------------------------------------------------------------


def test_the_full_path_refuses_a_delta_artifact(tmp_path, monkeypatch, modes):
    case = prepared_delta(tmp_path, monkeypatch, modes)
    case.preparer.artifact_format = "full"
    with pytest.raises(ArtifactError, match="needs --artifact-format delta_v1"):
        case.preparer.prepare(case.inspection, TARGET_ID, case.journal)


def test_an_older_engine_cannot_mistake_a_delta_for_a_package(tmp_path):
    every_root_file = ["src/inkypi.py", "install/inkypi.service", "install/inkypi", "install/inkypi-update"]
    inspection = make_delta(tmp_path / "delta.zip", changed=every_root_file,
                            target={name: b"x" for name in every_root_file})
    extracted = update_engine.safe_extract_zip(inspection, tmp_path / "extracted")
    with pytest.raises(ArtifactError, match="exactly one InkyPi package root"):
        update_engine.find_release_root(extracted)


def load_updater():
    loader = SourceFileLoader("inkypi_update_delta_test_module", str(INSTALL_ROOT / "inkypi-update"))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def test_print_capabilities_reports_delta_v1_without_root(monkeypatch, capsys):
    module = load_updater()
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000, raising=False)

    assert module.main(["--print-capabilities"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["capabilities"] == ["delta_v1", "timing_v1"]
    assert document["artifact_formats"] == ["full", "delta_v1"] and document["schema_version"] == 1
    with pytest.raises(SystemExit):
        module.main(["--print-capabilities", "--artifact", "x"])


def test_print_capabilities_reflects_an_older_library(monkeypatch, capsys):
    module = load_updater()
    monkeypatch.setattr(module._engine, "UPDATER_FEATURES", ())

    assert module.main(["--print-capabilities"]) == 0

    assert json.loads(capsys.readouterr().out)["artifact_formats"] == ["full"]


def updater_args(tmp_path, artifact_format):
    return SimpleNamespace(
        artifact=tmp_path / "release.zip", sha256="0" * 64, release_id="candidate",
        install_root=tmp_path / "opt", state_root=tmp_path / "state", config=tmp_path / "device.json",
        service_name="inkypi.service", systemctl="systemctl", health_url="http://127.0.0.1/readyz",
        health_timeout=1.0, python=sys.executable, unit_target=tmp_path / "etc" / "inkypi.service",
        launcher_target=tmp_path / "bin" / "inkypi", updater_target=tmp_path / "sbin" / "inkypi-update",
        artifact_format=artifact_format)


def test_the_updater_hands_delta_v1_to_the_preparer_and_records_it(tmp_path, monkeypatch, capsys):
    module = load_updater()
    seen = {}

    class Lock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Coordinator:
        def __init__(self, _layout, _service, **_kwargs):
            self.links = None

        def activate(self, journal, _release):
            seen["metadata"] = journal.metadata
            for phase in (UpdatePhase.SWITCHED, UpdatePhase.STARTING, UpdatePhase.HEALTHY, UpdatePhase.COMMITTED):
                journal.transition(phase)

    class Preparer:
        def __init__(self, layout, **kwargs):
            self.layout = layout
            seen["kwargs"] = kwargs

        def prepare(self, _inspection, release_id, _journal):
            release = self.layout.release_path(release_id)
            release.mkdir(parents=True)
            return release

        def ensure_bootstrap_token(self, _release):
            pass

    monkeypatch.setattr(module, "UpdateLock", Lock)
    monkeypatch.setattr(module, "SystemdService", lambda **_kwargs: None)
    monkeypatch.setattr(module, "UpdateCoordinator", Coordinator)
    monkeypatch.setattr(module, "ArtifactPreparer", Preparer)
    monkeypatch.setattr(module, "inspect_artifact", lambda _a, _s: SimpleNamespace(sha256="verified"))
    monkeypatch.setattr(module, "_recover_existing", lambda *_args: None)
    monkeypatch.setattr(module, "_bootstrap_attested_headless_capability", lambda *_args: ())
    monkeypatch.setattr(module, "prune_releases", lambda *_args: None)

    assert module.run_update(updater_args(tmp_path, "delta_v1")) == 0

    assert seen["kwargs"]["artifact_format"] == "delta_v1"
    assert seen["metadata"]["artifact_format"] == "delta_v1"
    output = capsys.readouterr().out.splitlines()
    timing = [line.split()[1] for line in output if line.startswith("timing: ")]
    assert timing == ["recovery-bootstrap", "inspect", "bootstrap-token", "prune", "total"]
    assert output[-1].startswith("timing: total ") and output[-1].endswith(" committed")

    seen.clear()
    assert module.run_update(updater_args(tmp_path / "full", "full")) == 0
    assert "artifact_format" not in seen["kwargs"] and "artifact_format" not in seen["metadata"]


def test_the_updater_refuses_delta_v1_with_an_older_library(tmp_path, monkeypatch, capsys):
    module = load_updater()
    monkeypatch.setattr(module._engine, "UPDATER_FEATURES", ())
    monkeypatch.setattr(module, "ReleaseLayout", lambda *_args: pytest.fail("state was touched"))

    with pytest.raises(RuntimeError, match="does not support delta_v1"):
        module.run_update(updater_args(tmp_path, "delta_v1"))

    assert capsys.readouterr().out.startswith("timing: total ")
