"""Chunked, verified upload and transactional deployment of one built release.

On 2026-10-10 a single 157 MB scp stalled the device's brcmfmac (SDIO) Wi-Fi
and needed a power cycle. The artifact is therefore sent in 16 MiB parts, each
checked by sha256 on the device, with a pause after every part and bounded,
backed-off retries. Parts keep fixed numbered names, so retrying an ambiguous
copy overwrites that exact part and can never duplicate bytes. The parts are
joined and re-hashed only after all of them verified; then ``inkypi-update``
installs the release transactionally and the transport file is removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from release_build import file_sha256, short
from release_device import CURRENT_LINK, Device, release_name
from release_task import DEPLOYMENT_RECORD, ReleaseError, TaskContext, require, require_commit, same_path

CHUNK_BYTES = 16 * 1024 * 1024
UPLOAD_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 20  # multiplied by the attempt number
CHUNK_PAUSE_SECONDS = 5
LOG_TAIL_LINES = 22
CHUNK_DIR = "upload-chunks"
RELEASE_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")
RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REMOTE_PART = re.compile(r"/var/tmp/inkypi-deploy-[A-Za-z0-9._-]+\.zip\.part[0-9]{3,}")
SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Chunk:
    path: Path
    remote_path: str
    sha256: str


def require_label(label: str) -> str:
    require(isinstance(label, str) and RELEASE_LABEL.fullmatch(label),
            f"Release label must be 1-24 lowercase letters, digits or hyphens: {label!r}")
    return label


def compose_release_id(label: str, commit: str, now: datetime) -> str:
    """``deploy-<UTC yyyymmddThhmmssZ>-<label>-<commit8>``, e.g. deploy-20261016T195416Z-layout-85008032."""
    release = ("deploy-" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
               + "-" + require_label(label) + "-" + short(commit))
    require(RELEASE_ID.fullmatch(release), "Unsafe release id.")
    return release


def remote_artifact_path(release_id: str) -> str:
    require(RELEASE_ID.fullmatch(release_id), "Unsafe release id.")
    return "/var/tmp/inkypi-" + release_id + ".zip"


def split_artifact(artifact: Path, chunk_dir: Path, remote_path: str, chunk_bytes: int = CHUNK_BYTES) -> list[Chunk]:
    """Write numbered parts of ``artifact`` into a new ``chunk_dir`` and hash each part as written."""
    require(not chunk_dir.exists(), "Existing chunk directory must be inspected before another upload.")
    chunk_dir.mkdir()
    chunks: list[Chunk] = []
    try:
        with Path(artifact).open("rb") as source:
            while data := source.read(chunk_bytes):
                name = f"part{len(chunks):03d}"
                path = chunk_dir / name
                require(same_path(path.parent, chunk_dir), "Chunk path escaped task chunk directory.")
                remote_part = remote_path + "." + name
                require(REMOTE_PART.fullmatch(remote_part), "Unsafe remote chunk path.")
                with path.open("xb") as stream:
                    stream.write(data)
                chunks.append(Chunk(path, remote_part, file_sha256(path)))
        require(chunks, "Release artifact was empty.")
    except BaseException:
        remove_chunks(chunk_dir, chunks)
        raise
    return chunks


def remove_chunks(chunk_dir: Path, chunks: list[Chunk]) -> None:
    """Delete exactly the parts this run wrote, then the (now empty) directory; never recurse."""
    for chunk in chunks:
        require(same_path(chunk.path.parent, chunk_dir), "Chunk cleanup escaped task chunk directory.")
        chunk.path.unlink(missing_ok=True)
    if chunk_dir.exists():
        chunk_dir.rmdir()  # any unknown file makes this stop


def collision_check_command(remote_path: str, chunks: list[Chunk]) -> str:
    return "set -e; " + "; ".join("test ! -e " + path for path in [remote_path, *(c.remote_path for c in chunks)])


def verify_command(chunk: Chunk) -> str:
    return f"set -e; test \"$(sha256sum {chunk.remote_path} | cut -d' ' -f1)\" = '{chunk.sha256}'; echo verified"


def assemble_command(remote_path: str, chunks: list[Chunk], sha256: str) -> str:
    parts = " ".join(chunk.remote_path for chunk in chunks)
    return (f"set -e; cat {parts} > {remote_path}; "
            f"test \"$(sha256sum {remote_path} | cut -d' ' -f1)\" = '{sha256}'; rm -f -- {parts}; echo assembled")


def cleanup_command(remote_path: str, chunks: list[Chunk]) -> str:
    return "rm -f -- " + " ".join([remote_path, *(chunk.remote_path for chunk in chunks)])


def update_command(remote_path: str, sha256: str, expected_active: str, release_id: str) -> str:
    """Re-check the transport hash and the active release, then run the transactional updater."""
    return (f"set -Eeuo pipefail; trap 'rm -f -- {remote_path}' EXIT; "
            f"test \"$(sha256sum {remote_path} | cut -d' ' -f1)\" = '{sha256}'; "
            f"test \"$(readlink -f {CURRENT_LINK})\" = '{expected_active}'; "
            f"sudo -n /usr/local/sbin/inkypi-update --artifact {remote_path} --sha256 {sha256} "
            f"--release-id {release_id}; "
            "systemctl is-active --quiet inkypi.service")


def validate_inputs(ctx: TaskContext) -> tuple[dict, dict, Path]:
    """Return (current manifest, preflight, artifact) after every local precondition holds."""
    ctx.require_not_deployed()
    manifest = ctx.load("current-manifest.json")
    before = ctx.load("preflight.json")
    require(before.get("status") == "passed" and manifest.get("status") == "passed",
            "Validated source and baseline required.")
    require_commit(manifest.get("commit"), "Release commit")
    require(manifest.get("base_commit") == before.get("commit") and manifest["commit"] != before.get("commit"),
            "Unexpected release ancestry.")
    require(RELEASE_ID.fullmatch(release_name(str(before.get("active")))), "Unsafe active release path.")
    require(SHA256.fullmatch(str(manifest.get("artifact_sha256"))), "Manifest artifact hash is malformed.")
    artifact = Path(os.path.abspath(manifest["artifact"]))
    require(same_path(artifact.parent, ctx.task_dir), "Artifact escaped task directory.")
    require(artifact.is_file(), "Built artifact is missing.")
    require(file_sha256(artifact) == manifest["artifact_sha256"], "Artifact hash mismatch.")
    require(not ctx.path(CHUNK_DIR).exists(), "Existing chunk directory must be inspected before another upload.")
    return manifest, before, artifact


def upload(device: Device, chunks: list[Chunk], remote_path: str, sha256: str, sleep, out) -> None:
    for chunk in chunks:
        done = False
        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            if device.copy(chunk.path, chunk.remote_path) == 0:
                check = device.run(verify_command(chunk))
                done = check.returncode == 0 and check.stdout.decode("utf-8", "replace").strip() == "verified"
            if done:
                break
            if attempt < UPLOAD_ATTEMPTS:
                sleep(RETRY_PAUSE_SECONDS * attempt)
        require(done, f"Chunk {chunk.path.name} failed after retries.")
        out(f"uploaded {chunk.path.name}")
        sleep(CHUNK_PAUSE_SECONDS)
    check = device.run(assemble_command(remote_path, chunks, sha256))
    require(check.returncode == 0 and check.stdout.decode("utf-8", "replace").strip() == "assembled",
            "Final transport assembly or checksum failed.")


def deploy(ctx: TaskContext, label: str, *, execute: bool, connect, sleep=time.sleep,
           clock=lambda: datetime.now(timezone.utc), out=print) -> dict:
    """Deploy the task's built artifact; without ``execute`` only validate and describe the plan."""
    require_label(label)
    manifest, before, artifact = validate_inputs(ctx)
    sha256 = manifest["artifact_sha256"]
    if not execute:
        size = artifact.stat().st_size
        return {"status": "dry_run", "commit": manifest["commit"], "base_commit": manifest["base_commit"],
                "expected_active": before["active"], "artifact_sha256": sha256, "artifact_bytes": size,
                "chunks": -(-size // CHUNK_BYTES), "chunk_bytes": CHUNK_BYTES,
                "release_id_example": compose_release_id(label, manifest["commit"], clock())}

    device = connect()
    active = device.run("readlink -f " + CURRENT_LINK)
    require(active.returncode == 0 and active.stdout.decode().strip() == before["active"],
            "Active release changed after preflight.")
    release_id = compose_release_id(label, manifest["commit"], clock())
    remote_path = remote_artifact_path(release_id)
    chunk_dir = ctx.path(CHUNK_DIR)
    chunks = split_artifact(artifact, chunk_dir, remote_path, CHUNK_BYTES)
    try:
        cleanup = cleanup_command(remote_path, chunks)
        ctx.save("upload-plan.json", {"release_id": release_id, "artifact": remote_path,
                                      "parts": [chunk.remote_path for chunk in chunks], "cleanup_command": cleanup})
        require(device.run(collision_check_command(remote_path, chunks)).returncode == 0,
                "Remote upload paths already exist or could not be checked; inspect before retrying.")
        try:
            upload(device, chunks, remote_path, sha256, sleep, out)
        except BaseException:
            if device.run(cleanup).returncode != 0:
                print("warning: transport cleanup could not be confirmed; inspect the exact paths in upload-plan.json",
                      file=sys.stderr)
            raise
    finally:
        remove_chunks(chunk_dir, chunks)

    started = clock().isoformat()
    ctx.save(DEPLOYMENT_RECORD, {"status": "deploying", "release_id": release_id, "commit": manifest["commit"],
                                 "started_at": started, "remote_path": remote_path,
                                 "upload": "16MiB chunks, per-chunk sha256"})
    log_path = ctx.path("deployment.log")
    with log_path.open("wb") as log:
        exit_code = device.run(update_command(remote_path, sha256, before["active"], release_id),
                               stdout=log, stderr=subprocess.STDOUT).returncode
    record = {"status": "passed" if exit_code == 0 else "failed", "release_id": release_id,
              "commit": manifest["commit"], "started_at": started, "completed_at": clock().isoformat(),
              "exit_code": exit_code, "remote_path": remote_path}
    ctx.save(DEPLOYMENT_RECORD, record)
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:]:
        out(line)
    if exit_code != 0:
        raise ReleaseError(f"Transactional deployment returned exit {exit_code}; inspect rollback state.")
    return record
