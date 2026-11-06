"""Delta artifacts built by the release toolkit (tools/release/release_delta.py).

Every repository is a temporary git fixture; no test contacts a device. The
device engine (install/lib/update_engine.py) applies the built delta to an
extracted base release to prove that delta and full ZIP produce the same tree.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "install" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "tools" / "release"))

import release_build  # noqa: E402
import release_delta  # noqa: E402
from release_task import ReleaseError, TaskContext  # noqa: E402
import update_engine  # noqa: E402

BASE_RELEASE = "deploy-20261105T180244Z-quindouyin-"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "user.name=release-test",
                           "-c", "user.email=release-test@example.invalid", "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


BASE_FILES = {
    "src/inkypi.py": b"print('base')\n",
    "src/a.py": b"a = 1\n",
    "src/gone.py": b"gone = True\n",
    "src/static/app.css": b"body { color: black; }\n",
    "install/inkypi.service": b"[Unit]\nDescription=InkyPi\n",
    "install/inkypi": b"#!/bin/bash\necho launcher\n",
    "install/inkypi-update": b"#!/usr/bin/env python3\nprint('updater')\n",
    "install/requirements.txt": b"example==1.0 \\\n    --hash=sha256:" + b"0" * 64 + b"\n",
    "install/tool.sh": b"#!/bin/bash\necho tool\n",
}


def write_files(package, files):
    for name, data in files.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


@pytest.fixture
def round_repo(tmp_path):
    """A repository with a base commit (the live release) and a reviewed HEAD on top of it."""
    repo = tmp_path / "repo"
    package = repo / release_build.PACKAGE
    write_files(package, BASE_FILES)
    (package / "install/lib").mkdir(parents=True)
    shutil.copyfile(PACKAGE_ROOT / "install/lib/release_archive.py", package / "install/lib/release_archive.py")
    (repo / ".gitignore").write_text("outputs/\n.tmp/\n", encoding="utf-8")
    git(tmp_path, "init", "-q", str(repo))
    git(repo, "add", "--all")
    git(repo, "update-index", "--chmod=+x", release_build.PACKAGE + "/install/tool.sh")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (package / "src/a.py").write_bytes(b"a = 2\n")
    (package / "src/gone.py").unlink()
    (package / "src/new.py").write_bytes(b"new = True\n")
    git(repo, "add", "--all")
    git(repo, "commit", "-q", "-m", "head")
    return repo, base, git(repo, "rev-parse", "HEAD")


def baseline_round(repo, base, *, proof=True):
    (repo / "outputs").mkdir(exist_ok=True)
    ctx = TaskContext.open("outputs/round", repo, create=True)
    base_zip = repo.parent / ("base-" + base[:8] + ".zip")
    hashes, _ = release_build.package(repo, base, base_zip)
    release = BASE_RELEASE + base[:8]
    ctx.save("base-manifest.json", {"status": "passed", "commit": base, "hashes": hashes})
    ctx.save("preflight.json", {"status": "passed", "commit": base, "base_release": release,
                                "active": "/opt/inkypi/releases/" + release})
    if proof:
        ctx.save("baseline-source-proof.json", {"status": "passed", "commit": base, "base_release": release,
                                                "source_hashes_match": True})
    return ctx, base_zip, hashes


def test_build_writes_a_delta_of_only_the_changed_files_with_the_full_target_manifest(round_repo):
    repo, base, head = round_repo
    ctx, _base_zip, base_hashes = baseline_round(repo, base)

    summary = release_build.build(ctx)

    manifest = ctx.load("current-manifest.json")
    delta = manifest["delta"]
    assert summary["delta"] == delta and "delta_unavailable" not in manifest
    assert delta["format"] == "delta_v1" and delta["base_commit"] == base
    assert delta["base_release"] == BASE_RELEASE + base[:8]
    assert (delta["changed_files"], delta["removed_files"]) == (2, 1)
    artifact = Path(delta["artifact"])
    assert artifact.name == "inkypi-" + head[:8] + "-delta.zip" and artifact.parent == ctx.task_dir
    assert delta["artifact_sha256"] == sha(artifact.read_bytes()) == release_build.file_sha256(artifact)
    assert delta["artifact_bytes"] == artifact.stat().st_size
    with zipfile.ZipFile(artifact) as archive, zipfile.ZipFile(manifest["artifact"]) as full:
        names = archive.namelist()
        document = json.loads(archive.read(release_delta.MANIFEST_MEMBER))
        assert sorted(names) == sorted([release_delta.MANIFEST_MEMBER, "delta/files/src/a.py",
                                        "delta/files/src/new.py"])
        for name in ("src/a.py", "src/new.py"):
            assert archive.read("delta/files/" + name) == full.read(name)
    assert document["format"] == "inkypi-delta-v1" and document["target_commit"] == head
    assert document["base_commit"] == base and document["full_artifact_sha256"] == manifest["artifact_sha256"]
    assert document["hashes"] == manifest["hashes"] and set(document["hashes"]) == set(base_hashes) - {
        "src/gone.py"} | {"src/new.py"}
    assert document["changed"] == ["src/a.py", "src/new.py"] and document["removed"] == ["src/gone.py"]
    assert document["modes"]["install/tool.sh"] == "0755" and document["modes"]["src/a.py"] == "0644"
    assert set(document["modes"]) == set(document["hashes"])


def test_the_device_engine_turns_base_plus_delta_into_exactly_the_full_artifact_tree(round_repo, tmp_path):
    repo, base, _head = round_repo
    ctx, base_zip, _hashes = baseline_round(repo, base)
    release_build.build(ctx)
    manifest = ctx.load("current-manifest.json")
    base_release = tmp_path / "releases" / manifest["delta"]["base_release"]
    base_release.parent.mkdir(parents=True)
    # Extract like the device does, so the base files carry the full ZIP's modes.
    update_engine.safe_extract_zip(update_engine.inspect_artifact(base_zip, sha(base_zip.read_bytes())),
                                   base_release)
    delta = Path(manifest["delta"]["artifact"])

    inspection = update_engine.inspect_artifact(delta, manifest["delta"]["artifact_sha256"])
    plan = update_engine.read_delta_manifest(inspection)
    counts = update_engine.apply_delta(inspection, plan, base_release, tmp_path / "candidate")
    assert update_engine.verify_package_tree(tmp_path / "candidate", plan) == len(manifest["hashes"])

    assert counts["written"] == 2 and counts["removed"] == 1
    assert counts["linked"] + counts["copied"] == len(manifest["hashes"]) - 2
    with zipfile.ZipFile(manifest["artifact"]) as full:
        expected = {name: full.read(name) for name in full.namelist()}
    candidate = tmp_path / "candidate"
    actual = {path.relative_to(candidate).as_posix(): path.read_bytes()
              for path in candidate.rglob("*") if path.is_file()}
    assert actual == expected
    assert os.path.samefile(candidate / "src/inkypi.py", base_release / "src/inkypi.py")
    assert not os.path.samefile(candidate / "src/a.py", base_release / "src/a.py")


@pytest.mark.parametrize("change, reason", [
    ("no-proof", "no passed base manifest proof"),
    ("other-proof", "belongs to another release"),
])
def test_a_round_without_a_safe_delta_records_why_and_still_builds_the_full_zip(round_repo, change, reason):
    repo, base, _head = round_repo
    ctx, _base_zip, _hashes = baseline_round(repo, base, proof=change != "no-proof")
    if change == "other-proof":
        proof = ctx.load("baseline-source-proof.json")
        ctx.save("baseline-source-proof.json", {**proof, "base_release": "deploy-other"})

    release_build.build(ctx)

    manifest = ctx.load("current-manifest.json")
    assert manifest["delta"] is None and reason in manifest["delta_unavailable"]
    assert Path(manifest["artifact"]).is_file()
    assert sorted(path.name for path in ctx.task_dir.glob("*.zip")) == ["inkypi-" + manifest["commit"][:8] + ".zip"]


def test_a_changed_dependency_lock_never_ships_as_a_delta(tmp_path):
    reason = release_delta.blocker(tmp_path, preflight={}, base_manifest={"hashes": {}}, hashes={},
                                   dependency_changes={"pillow": {"before": "1", "after": "2"}},
                                   proof={"status": "passed"})
    assert "runtime lock changes" in reason
    proof = {"status": "passed", "source_hashes_match": True, "commit": "c", "base_release": "r"}
    reason = release_delta.blocker(tmp_path, preflight={"base_release": "r"},
                                   base_manifest={"commit": "c", "hashes": {release_delta.LOCK: "1" * 64}},
                                   hashes={release_delta.LOCK: "2" * 64}, dependency_changes={}, proof=proof)
    assert "lock differs" in reason


@pytest.mark.parametrize("name", ["venv_inkypi/bin/python", ".release-id", "cli/inkypi-plugin",
                                  "install/.release-migrations.json", "src/__pycache__/a.cpython-311.pyc"])
def test_paths_the_device_would_refuse_keep_the_round_on_the_full_zip(tmp_path, name):
    proof = {"status": "passed", "source_hashes_match": True, "commit": "c", "base_release": "r"}
    hashes = {release_delta.LOCK: "1" * 64, name: "2" * 64}

    reason = release_delta.blocker(tmp_path, preflight={"base_release": "r"},
                                   base_manifest={"commit": "c", "hashes": {release_delta.LOCK: "1" * 64}},
                                   hashes=hashes, dependency_changes={}, proof=proof)

    assert reason.startswith("delta_v1 refused: ") and name in reason


def test_a_package_that_contains_the_delta_manifest_name_is_refused(round_repo):
    repo, base, _head = round_repo
    package = repo / release_build.PACKAGE
    (package / release_delta.MANIFEST_MEMBER).write_text("{}", encoding="utf-8")
    git(repo, "add", "--all")
    git(repo, "commit", "-q", "-m", "collision")
    ctx, _base_zip, _hashes = baseline_round(repo, base)

    with pytest.raises(ReleaseError, match="would read it as a delta"):
        release_build.build(ctx)


def test_engine_verification_rejects_a_delta_that_describes_another_build(round_repo):
    repo, base, _head = round_repo
    ctx, _base_zip, _hashes = baseline_round(repo, base)
    release_build.build(ctx)
    manifest = ctx.load("current-manifest.json")
    delta = Path(manifest["delta"]["artifact"])

    with pytest.raises(ReleaseError, match="does not describe this build"):
        release_delta.verify_with_engine(repo, delta, manifest["delta"]["artifact_sha256"],
                                         {**manifest["hashes"], "src/extra.py": "3" * 64}, ["src/a.py", "src/new.py"],
                                         ["src/gone.py"])
    with pytest.raises(ReleaseError, match="engine refuses the delta"):
        release_delta.verify_with_engine(repo, delta, "0" * 64, manifest["hashes"], [], [])
