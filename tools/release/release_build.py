"""Baseline proof, clean packaging and baseline-backup preparation for one round.

The base of a round is whatever the device runs now. Its expected source
identity is the ``current-manifest.json`` of the round that deployed it, found
through that round's ``deployment.json``; the device's own file hashes must then
match it before anything is built on top of it.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import zipfile

from release_device import CONFIG, CURRENT_LINK, DISPLAY, RELEASES_DIR, Device, digest, release_name
from release_checks import config_identity
from release_task import DEPLOYMENT_RECORD, PACKAGE, ReleaseError, TaskContext, require, require_commit

PREVIOUS_BACKUP_DIR = ".backups/previous"
RETENTION = "exactly one previous-release archive plus identity manifest"
REQUIREMENT_PIN = re.compile(r"^([a-zA-Z0-9_.-]+)==([^\s\\]+)", re.MULTILINE)

BASELINE_PROBE = r'''import datetime,hashlib,json,os,pathlib,shutil,subprocess
expected=EXPECTED
root=pathlib.Path('/opt/inkypi/current')
actual={name:hashlib.sha256((root/name).read_bytes()).hexdigest() if (root/name).is_file() else None for name in expected}
service=subprocess.check_output(['systemctl','show','inkypi','-p','ActiveState','-p','SubState','-p','NRestarts','-p','MainPID'],text=True)
meminfo=dict(line.split(':',1) for line in open('/proc/meminfo'))
print(json.dumps({'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'active':str(root.resolve()),'previous':str(pathlib.Path('/opt/inkypi/previous').resolve()),
 'releases':sorted(p.name for p in pathlib.Path('/opt/inkypi/releases').iterdir()),
 'service':dict(row.split('=',1) for row in service.splitlines()),
 'available_disk_kib':shutil.disk_usage('/').free//1024,
 'available_memory_mib':int(meminfo['MemAvailable'].split()[0])//1024,
 'var_tmp_transport_zips':sorted(p.name for p in pathlib.Path('/var/tmp').glob('inkypi-*.zip')),
 'file_count':len(actual),'source_hashes_match':actual==expected,
 'mismatched_files':[name for name in expected if actual[name]!=expected[name]]}))
'''


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, encoding="utf-8").strip()


def file_sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def short(commit: str) -> str:
    return require_commit(commit, "commit")[:8]


def backup_archive_name(commit: str) -> str:
    return "inkypi-" + short(commit) + ".zip"


def baseline_archive_name(commit: str) -> str:
    return "baseline-" + short(commit) + ".zip"


def load_archive_policy(repo: Path):
    """Load the package's own release_archive policy under a private module name."""
    path = Path(repo) / PACKAGE / "install/lib/release_archive.py"
    spec = importlib.util.spec_from_file_location("epaper_release_archive_policy", path)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    return policy


def is_packaged(policy, path: PurePosixPath) -> bool:
    return not (any(part in policy.EXCLUDED_NAMES for part in path.parts)
                or path.as_posix() in policy.MIGRATION_CONTROL_MEMBERS
                or path.name in policy.MIGRATION_CONTROL_NAMES
                or policy.is_device_owned_yahei_font(path) or policy.is_runtime_plugin_cache(path)
                or policy.is_runtime_display_image(path) or path.suffix == ".pyc")


def package(repo: Path, commit: str, target: Path) -> tuple[dict[str, str], int]:
    """Write the tracked package tree of ``commit`` to a new ZIP; return member hashes and shebang count.

    The archive comes from ``git archive``, so untracked or ignored files can
    never enter a release, and Linux entry points are stored with LF endings.
    """
    policy = load_archive_policy(repo)
    process = subprocess.Popen(["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                                "archive", "--format=tar", commit + ":" + PACKAGE],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    hashes: dict[str, str] = {}
    shebangs = 0
    with tarfile.open(fileobj=process.stdout, mode="r|") as source, zipfile.ZipFile(target, "x") as output:
        for member in source:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts, f"Unsafe archive member: {member.name}")
            require(member.isfile() or member.isdir(), f"Unexpected archive member type: {member.name}")
            if not member.isfile() or not is_packaged(policy, path):
                continue
            data = source.extractfile(member).read()
            if policy.is_linux_text_member(path):
                data = data.replace(b"\r\n", b"\n")
            if data.startswith(b"#!"):
                require(b"\r" not in data, f"Carriage return in Linux entry point: {member.name}")
                shebangs += 1
            info = zipfile.ZipInfo(member.name, datetime.fromtimestamp(member.mtime, timezone.utc).timetuple()[:6])
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | member.mode) << 16
            output.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            hashes[member.name] = hashlib.sha256(data).hexdigest()
    while process.stdout.read(65536):
        pass
    require(process.wait() == 0, "git archive failed")
    with zipfile.ZipFile(target) as check:
        require(check.testzip() is None, "Packaged archive failed its CRC check")
    return hashes, shebangs


def changed_members(before: dict[str, str], after: dict[str, str]) -> set[str]:
    """Members added, modified or removed between two package hash sets."""
    return {name for name in after if after[name] != before.get(name)} | (set(before) - set(after))


def requirement_versions(lock_text: str) -> dict[str, str]:
    return dict(REQUIREMENT_PIN.findall(lock_text))


def dependency_changes(old_lock: str, new_lock: str) -> dict[str, dict[str, str | None]]:
    old, new = requirement_versions(old_lock), requirement_versions(new_lock)
    return {name: {"before": old.get(name), "after": new.get(name)}
            for name in sorted(set(old) | set(new)) if old.get(name) != new.get(name)}


def read_expected_changes(path: Path) -> set[str]:
    """Read a whitespace-separated list of package-relative paths (the old expected-changes.txt)."""
    names = set(Path(path).read_text(encoding="utf-8").split())
    for name in names:
        member = PurePosixPath(name)
        require(not member.is_absolute() and ".." not in member.parts, f"Unsafe expected change: {name}")
    return names


def discover_base_manifest(ctx: TaskContext, release_id: str) -> Path:
    """Find the ``current-manifest.json`` of the round whose passed deployment is ``release_id``."""
    matches = []
    for record in sorted((ctx.repo / "outputs").glob("*/" + DEPLOYMENT_RECORD)):
        if record.parent == ctx.task_dir:
            continue
        try:
            deployment = TaskContext(ctx.repo, record.parent).load(DEPLOYMENT_RECORD)
        except (ValueError, ReleaseError):
            continue
        if deployment.get("status") == "passed" and deployment.get("release_id") == release_id:
            matches.append((record.parent, deployment))
    require(matches, f"No task folder under outputs/ has a passed deployment of {release_id}; pass --base-manifest")
    require(len(matches) == 1, "Several task folders claim the live release: "
            + ", ".join(folder.name for folder, _ in matches) + "; pass --base-manifest")
    folder, deployment = matches[0]
    manifest = TaskContext(ctx.repo, folder).load("current-manifest.json")
    require(manifest.get("commit") == deployment.get("commit"),
            f"{folder.name}: current-manifest.json does not match its deployment.json commit")
    return folder / "current-manifest.json"


def load_base_manifest(path: Path) -> dict:
    manifest = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    require(manifest.get("status") == "passed", f"Base manifest {path} did not pass")
    require_commit(manifest.get("commit"), "Base manifest commit")
    require(isinstance(manifest.get("hashes"), dict) and manifest["hashes"], "Base manifest has no source hashes")
    return manifest


def baseline(ctx: TaskContext, device: Device, base_manifest: Path | None = None) -> dict:
    """Prove the live release's source against its manifest and record preflight.json."""
    ctx.require_not_deployed()
    active = device.resolved_link(CURRENT_LINK)
    base_release = release_name(active)
    source = Path(base_manifest) if base_manifest else discover_base_manifest(ctx, base_release)
    expected = load_base_manifest(source)
    base_commit = expected["commit"]
    ctx.save("base-manifest.json", expected)

    result = device.run("python3 -", timeout=120,
                        input=BASELINE_PROBE.replace("EXPECTED", repr(expected["hashes"])).encode())
    require(result.returncode == 0, "Read-only baseline probe failed")
    record = json.loads(result.stdout)
    require(record["active"] == RELEASES_DIR + "/" + base_release, f"Active release moved: {record['active']}")
    require(record["source_hashes_match"], f"Live source differs from the base manifest: {record['mismatched_files']}")
    record.update(commit=base_commit, status="passed", base_release=base_release,
                  base_manifest_source=str(source))
    ctx.save("baseline-source-proof.json", record)

    record["configuration"] = config_identity(device.document(CONFIG))
    record["health_before"] = device.http_json("/healthz")
    record["readiness_before"] = device.http_json("/readyz")
    record["display_commit_before"] = device.document(DISPLAY).get("commit_id")
    require(record["health_before"]["payload"].get("status") == "alive", "Device is not alive before deployment")
    require(record["service"].get("ActiveState") == "active", "inkypi.service is not active before deployment")
    ctx.save("preflight.json", record)
    return record


def build(ctx: TaskContext, expect_changes: set[str] | None = None) -> dict:
    """Package committed HEAD and prove its change set against the base manifest."""
    ctx.require_not_deployed()
    repo = ctx.repo
    require(not git(repo, "status", "--porcelain"), "Commit all reviewed source before building")
    before = ctx.load("base-manifest.json")
    preflight = ctx.load("preflight.json")
    base_commit = require_commit(before.get("commit"), "Base manifest commit")
    require(preflight.get("commit") == base_commit and preflight.get("status") == "passed",
            "Run baseline in this task folder first")
    commit = git(repo, "rev-parse", "HEAD")
    require(commit != base_commit, "HEAD is already the live release")
    require(git(repo, "merge-base", base_commit, commit) == base_commit, "HEAD does not descend from the live release")

    target = ctx.path("inkypi-" + short(commit) + ".zip")
    require(not target.exists(), f"Inspect the existing {target.name} instead of rebuilding over it")
    hashes, shebangs = package(repo, commit, target)
    changed = changed_members(before["hashes"], hashes)
    declared = {name.removeprefix(PACKAGE + "/")
                for name in git(repo, "diff", "--name-only", base_commit, commit, "--", PACKAGE).splitlines()}
    require(changed == declared, f"Packaged changes differ from git: {sorted(changed ^ declared)}")
    if expect_changes is not None:
        require(changed == expect_changes,
                f"Change set differs from the expected list: {sorted(changed ^ expect_changes)}")

    sys.path.insert(0, str(repo / PACKAGE / "install/lib"))
    from update_engine import inspect_artifact

    sha256 = file_sha256(target)
    inspection = inspect_artifact(target, sha256)
    lock = PACKAGE + "/install/requirements.txt"
    lock_member = "install/requirements.txt"
    changes = dependency_changes(git(repo, "show", base_commit + ":" + lock), git(repo, "show", commit + ":" + lock))
    require(changes == {}, f"Runtime dependency changes need their own reviewed round: {changes}")
    record = {"status": "passed", "commit": commit, "base_commit": base_commit,
              "base_release": preflight["base_release"], "artifact": str(target), "artifact_sha256": sha256,
              "file_count": inspection.file_count, "changed_files": sorted(changed),
              "shebangs_lf_verified": shebangs, "hashes": hashes,
              "runtime_lock_unchanged": hashes[lock_member] == before["hashes"][lock_member],
              "built_at": datetime.now(timezone.utc).isoformat(), "runtime_dependency_changes": changes}
    ctx.save("current-manifest.json", record)
    return {key: value for key, value in record.items() if key != "hashes"}


def backup(ctx: TaskContext) -> dict:
    """Re-package the live base commit and prove it equals the device's source identity."""
    ctx.require_not_deployed()
    manifest = ctx.load("base-manifest.json")
    preflight = ctx.load("preflight.json")
    base_commit = require_commit(manifest.get("commit"), "Base manifest commit")
    require(preflight.get("commit") == base_commit and preflight.get("status") == "passed",
            "Run baseline in this task folder first")
    target = ctx.path(baseline_archive_name(base_commit))
    require(not target.exists(), f"Inspect the existing {target.name} instead of rebuilding over it")
    hashes, _ = package(ctx.repo, base_commit, target)
    require(hashes == manifest["hashes"], "Baseline source identity mismatch")
    sha256 = file_sha256(target)
    draft = {"commit": base_commit,
             "artifact": str(ctx.repo / PREVIOUS_BACKUP_DIR / backup_archive_name(base_commit)),
             "sha256": sha256, "matches_device_previous": preflight["active"],
             "file_count": len(hashes), "source_hash_set_sha256": digest(hashes), "retention": RETENTION}
    ctx.save("previous-manifest-draft.json", draft)
    record = {"status": "prepared_verified_not_rotated", "commit": base_commit,
              "artifact": str(target), "artifact_sha256": sha256, "file_count": len(hashes)}
    ctx.save("backup-preparation.json", record)
    return record
