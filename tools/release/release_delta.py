"""Delta artifact of one round: only the package files that changed against the live release.

``build`` always writes the full ZIP too: backup rotation and every fallback
need it. The delta ZIP holds the added and changed files under
``delta/files/`` plus ``inkypi-delta.json``, which names the complete target
package (path -> sha256, the shape of current-manifest.json), the removed
paths and the mode the full ZIP gives every file. The delta's own sha256,
passed to the device like the full ZIP's, covers that manifest.

The device engine builds the candidate from hard links into its active
release, refuses unless that release is exactly the delta's base, and proves
the whole candidate tree against the manifest before anything else runs.
``deploy`` uses the delta only when the device's updater reports
``delta_v1``; otherwise it sends the full ZIP and records why.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

from release_device import release_name
from release_task import PACKAGE, ReleaseError, TaskContext, require, same_path

DELTA_FORMAT = "inkypi-delta-v1"
ARTIFACT_FORMAT = "delta_v1"
FULL_FORMAT = "full"
MANIFEST_MEMBER = "inkypi-delta.json"
PAYLOAD_PREFIX = "delta/files/"
LOCK = "install/requirements.txt"
CAPABILITIES_COMMAND = "sudo -n /usr/local/sbin/inkypi-update --print-capabilities"
MAX_CAPABILITIES_BYTES = 64 * 1024


def delta_archive_name(commit: str) -> str:
    return "inkypi-" + commit[:8] + "-delta.zip"


def engine(repo: Path):
    """The package's own device update engine, which also validates the delta locally."""
    library = str(Path(repo) / PACKAGE / "install/lib")
    if library not in sys.path:
        sys.path.insert(0, library)
    import update_engine

    return update_engine


def zip_modes(full_zip: Path) -> dict[str, str]:
    """The mode extraction gives each member of the full ZIP: 0755 with any execute bit, else 0644."""
    with zipfile.ZipFile(full_zip) as archive:
        return {info.filename: "0755" if (info.external_attr >> 16) & 0o111 else "0644"
                for info in archive.infolist() if not info.is_dir()}


def blocker(repo: Path, *, preflight: dict, base_manifest: dict, hashes: dict, dependency_changes: dict,
            proof: dict | None) -> str | None:
    """Why this round cannot ship a delta, or None. Each reason falls back to the full ZIP."""
    if dependency_changes:
        return "the runtime lock changes; the device builds a fresh venv from the full artifact"
    if not proof or proof.get("status") != "passed" or proof.get("source_hashes_match") is not True:
        return "no passed base manifest proof (baseline-source-proof.json)"
    if proof.get("commit") != base_manifest.get("commit") or proof.get("base_release") != preflight.get("base_release"):
        return "the base manifest proof belongs to another release"
    if base_manifest.get("hashes", {}).get(LOCK) != hashes.get(LOCK):
        return "the runtime lock differs from the live release"
    update_engine = engine(repo)
    for name in sorted(set(hashes) | set(base_manifest["hashes"])):
        try:
            update_engine._validated_delta_path(name)
        except update_engine.ArtifactError as error:
            return str(error)
    return None


def build_delta(full_zip: Path, target: Path, *, base_hashes: dict, hashes: dict, base_commit: str,
                base_release: str, target_commit: str, full_sha256: str) -> dict:
    """Write the delta ZIP from the exact bytes of the full ZIP and return its manifest (without hashes)."""
    modes = zip_modes(full_zip)
    require(set(modes) == set(hashes), "Full artifact members differ from the packaged hash set")
    changed = sorted(name for name in hashes if base_hashes.get(name) != hashes[name])
    removed = sorted(set(base_hashes) - set(hashes))
    manifest = {"format": DELTA_FORMAT, "base_commit": base_commit, "base_release": base_release,
                "target_commit": target_commit, "full_artifact_sha256": full_sha256,
                "hashes": dict(sorted(hashes.items())), "modes": dict(sorted(modes.items())),
                "removed": removed, "changed": changed}
    with zipfile.ZipFile(full_zip) as source, zipfile.ZipFile(target, "x") as output:
        info = zipfile.ZipInfo(MANIFEST_MEMBER, (2026, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        output.writestr(info, json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                        compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        for name in changed:
            original = source.getinfo(name)
            data = source.read(original)
            require(file_digest(data) == hashes[name], f"Full artifact member differs from its hash: {name}")
            member = zipfile.ZipInfo(PAYLOAD_PREFIX + name, original.date_time)
            member.create_system = 3
            member.external_attr = original.external_attr
            output.writestr(member, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(target) as check:
        require(check.testzip() is None, "Delta archive failed its CRC check")
    return {key: value for key, value in manifest.items() if key not in {"hashes", "modes"}}


def file_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_with_engine(repo: Path, artifact: Path, sha256: str, hashes: dict, changed: list, removed: list) -> None:
    """Parse the delta exactly like the device will and require the same target, payload and removals."""
    update_engine = engine(repo)
    try:
        plan = update_engine.read_delta_manifest(update_engine.inspect_artifact(artifact, sha256))
    except update_engine.ArtifactError as error:
        raise ReleaseError(f"The device engine refuses the delta: {error}") from error
    require(plan.hashes == hashes and list(plan.changed) == changed and list(plan.removed) == removed,
            "The delta manifest does not describe this build")


def round_delta(ctx: TaskContext, *, full_zip: Path, full_sha256: str, commit: str, base_manifest: dict,
                preflight: dict, hashes: dict, dependency_changes: dict) -> dict:
    """Build and verify this round's delta, or record why the round deploys only the full ZIP."""
    from release_build import file_sha256

    proof = ctx.load("baseline-source-proof.json") if ctx.has("baseline-source-proof.json") else None
    reason = blocker(ctx.repo, preflight=preflight, base_manifest=base_manifest, hashes=hashes,
                     dependency_changes=dependency_changes, proof=proof)
    if reason is not None:
        return {"delta": None, "delta_unavailable": reason}
    target = ctx.path(delta_archive_name(commit))
    require(not target.exists(), f"Inspect the existing {target.name} instead of rebuilding over it")
    summary = build_delta(full_zip, target, base_hashes=base_manifest["hashes"], hashes=hashes,
                          base_commit=base_manifest["commit"], base_release=preflight["base_release"],
                          target_commit=commit, full_sha256=full_sha256)
    sha256 = file_sha256(target)
    verify_with_engine(ctx.repo, target, sha256, hashes, summary["changed"], summary["removed"])
    return {"delta": {"format": ARTIFACT_FORMAT, "artifact": str(target), "artifact_sha256": sha256,
                      "artifact_bytes": target.stat().st_size, "base_release": summary["base_release"],
                      "base_commit": summary["base_commit"], "changed_files": len(summary["changed"]),
                      "removed_files": len(summary["removed"])}}


def delta_candidate(ctx: TaskContext, manifest: dict, before: dict) -> tuple[dict | None, str]:
    """This round's verified delta record, or (None, reason). A tampered delta file is refused."""
    delta = manifest.get("delta")
    if not delta:
        return None, manifest.get("delta_unavailable") or "the build recorded no delta artifact"
    require(delta.get("format") == ARTIFACT_FORMAT, "Unknown delta format in current-manifest.json")
    from release_build import file_sha256

    artifact = Path(os.path.abspath(str(delta.get("artifact", ""))))
    require(same_path(artifact.parent, ctx.task_dir), "Delta artifact escaped task directory.")
    require(artifact.is_file(), "Delta artifact is missing; rebuild in a new task folder or deploy with --full.")
    require(file_sha256(artifact) == delta.get("artifact_sha256"),
            "Delta artifact hash mismatch; rebuild in a new task folder or deploy with --full.")
    if delta.get("base_release") != release_name(str(before.get("active"))):
        return None, "the active release is not the delta's base release"
    return {**delta, "artifact": str(artifact)}, ""


def parse_capabilities(result) -> list[str] | None:
    """Capabilities from ``inkypi-update --print-capabilities``; None when the updater predates it."""
    if result.returncode != 0 or len(result.stdout or b"") > MAX_CAPABILITIES_BYTES:
        return None
    try:
        document = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, ValueError):
        return None
    capabilities = document.get("capabilities") if isinstance(document, dict) else None
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        return None
    return sorted(set(capabilities))


def choose_artifact(manifest: dict, full: Path, delta: dict | None, delta_reason: str,
                    capabilities: list[str] | None, force_full: bool) -> dict:
    """Pick the artifact to send: the delta only when the device can apply it."""
    full_choice = {"format": FULL_FORMAT, "path": Path(full), "sha256": manifest["artifact_sha256"]}
    if force_full:
        return {**full_choice, "reason": "--full requested"}
    if delta is None:
        return {**full_choice, "reason": delta_reason}
    if capabilities is None:
        return {**full_choice, "reason": "the device updater does not report capabilities (older engine)"}
    if ARTIFACT_FORMAT not in capabilities:
        return {**full_choice, "reason": "the device updater lacks delta_v1"}
    return {"format": ARTIFACT_FORMAT, "path": Path(delta["artifact"]), "sha256": delta["artifact_sha256"],
            "reason": "the device updater reports delta_v1 and runs the delta's base release"}
