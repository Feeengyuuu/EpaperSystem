"""Chunked, verified upload and transactional deployment of one built release.

On 2026-10-10 a single 157 MB scp stalled the device's brcmfmac (SDIO) Wi-Fi
and needed a power cycle. The artifact is therefore sent in 16 MiB parts, each
checked by sha256 on the device, with a pause after every part and bounded,
backed-off retries. Parts keep fixed numbered names, so retrying an ambiguous
copy overwrites that exact part and can never duplicate bytes. The parts are
joined and re-hashed only after all of them verified; then ``inkypi-update``
installs the release transactionally and the transport file is removed.

The device's updater is asked for its capabilities first: when it reports
``delta_v1`` and runs the delta's base release, only the delta ZIP is sent
(see release_delta); otherwise, or with ``--full``, the full ZIP is. The
updater's ``timing:`` lines are stamped with local time as they arrive and
recorded in deployment.json with the upload and install durations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys
import time

from release_build import file_sha256, short
import release_delta
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
TIMING_LINE = re.compile(r"timing: (?P<phase>[a-z][a-z0-9-]{0,31}) (?P<seconds>\d+(?:\.\d+)?)s(?: (?P<detail>.{1,200}))?")
ARTIFACT_FORMATS = (release_delta.FULL_FORMAT, release_delta.ARTIFACT_FORMAT)


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


def update_command(remote_path: str, sha256: str, expected_active: str, release_id: str,
                   artifact_format: str = release_delta.FULL_FORMAT) -> str:
    """Re-check the transport hash and the active release, then run the transactional updater."""
    require(artifact_format in ARTIFACT_FORMATS, f"Unknown artifact format: {artifact_format!r}")
    format_option = "" if artifact_format == release_delta.FULL_FORMAT else " --artifact-format " + artifact_format
    return (f"set -Eeuo pipefail; trap 'rm -f -- {remote_path}' EXIT; "
            f"test \"$(sha256sum {remote_path} | cut -d' ' -f1)\" = '{sha256}'; "
            f"test \"$(readlink -f {CURRENT_LINK})\" = '{expected_active}'; "
            f"sudo -n /usr/local/sbin/inkypi-update --artifact {remote_path} --sha256 {sha256} "
            f"--release-id {release_id}{format_option}; "
            "systemctl is-active --quiet inkypi.service")


def parse_timing(line: str) -> dict | None:
    """One ``timing: <phase> <seconds>s [detail]`` line of the device updater, or None."""
    match = TIMING_LINE.fullmatch(line.strip())
    if match is None:
        return None
    record = {"phase": match["phase"], "seconds": float(match["seconds"])}
    if match["detail"]:
        record["detail"] = match["detail"]
    return record


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


def dry_run_plan(label: str, manifest: dict, before: dict, artifact: Path,
                 delta: dict | None, delta_reason: str, clock) -> dict:
    """What deploy would send; which artifact goes is decided only once the device answers."""
    size = artifact.stat().st_size
    plan = {"status": "dry_run", "commit": manifest["commit"], "base_commit": manifest["base_commit"],
            "expected_active": before["active"], "artifact_sha256": manifest["artifact_sha256"],
            "artifact_bytes": size, "chunks": -(-size // CHUNK_BYTES), "chunk_bytes": CHUNK_BYTES,
            "release_id_example": compose_release_id(label, manifest["commit"], clock())}
    if delta is None:
        plan["artifact_plan"] = "full: " + delta_reason
    else:
        plan["artifact_plan"] = "delta_v1 if the device updater reports it, else full"
        plan["delta"] = {"artifact_sha256": delta["artifact_sha256"], "artifact_bytes": delta["artifact_bytes"],
                         "chunks": -(-delta["artifact_bytes"] // CHUNK_BYTES), "base_release": delta["base_release"],
                         "changed_files": delta["changed_files"], "removed_files": delta["removed_files"]}
    return plan


def deploy(ctx: TaskContext, label: str, *, execute: bool, connect, sleep=time.sleep,
           clock=lambda: datetime.now(timezone.utc), out=print, force_full: bool = False,
           timer=time.monotonic) -> dict:
    """Deploy the task's built artifact; without ``execute`` only validate and describe the plan.

    The delta is sent when the build made one, ``force_full`` is not set and the
    device's updater reports ``delta_v1``; every other case sends the full ZIP.
    """
    require_label(label)
    manifest, before, artifact = validate_inputs(ctx)
    if force_full:
        delta, delta_reason = None, "--full requested"
    else:
        delta, delta_reason = release_delta.delta_candidate(ctx, manifest, before)
    if not execute:
        return dry_run_plan(label, manifest, before, artifact, delta, delta_reason, clock)

    device = connect()
    active = device.run("readlink -f " + CURRENT_LINK)
    require(active.returncode == 0 and active.stdout.decode().strip() == before["active"],
            "Active release changed after preflight.")
    capabilities = release_delta.parse_capabilities(device.run(release_delta.CAPABILITIES_COMMAND, timeout=60))
    choice = release_delta.choose_artifact(manifest, artifact, delta, delta_reason, capabilities, force_full)
    out(f"artifact {choice['format']}: {choice['reason']}")
    sha256 = choice["sha256"]
    release_id = compose_release_id(label, manifest["commit"], clock())
    remote_path = remote_artifact_path(release_id)
    chunk_dir = ctx.path(CHUNK_DIR)
    chunks = split_artifact(choice["path"], chunk_dir, remote_path, CHUNK_BYTES)
    try:
        cleanup = cleanup_command(remote_path, chunks)
        ctx.save("upload-plan.json", {"release_id": release_id, "artifact": remote_path,
                                      "artifact_format": choice["format"],
                                      "parts": [chunk.remote_path for chunk in chunks], "cleanup_command": cleanup})
        require(device.run(collision_check_command(remote_path, chunks)).returncode == 0,
                "Remote upload paths already exist or could not be checked; inspect before retrying.")
        upload_started = timer()
        try:
            upload(device, chunks, remote_path, sha256, sleep, out)
        except BaseException:
            if device.run(cleanup).returncode != 0:
                print("warning: transport cleanup could not be confirmed; inspect the exact paths in upload-plan.json",
                      file=sys.stderr)
            raise
        upload_seconds = round(timer() - upload_started, 1)
    finally:
        remove_chunks(chunk_dir, chunks)

    started = clock().isoformat()
    common = {"release_id": release_id, "commit": manifest["commit"], "started_at": started,
              "remote_path": remote_path, "artifact_format": choice["format"], "artifact_sha256": sha256,
              "artifact_bytes": Path(choice["path"]).stat().st_size, "format_reason": choice["reason"],
              "device_capabilities": capabilities, "upload_parts": len(chunks), "upload_seconds": upload_seconds}
    ctx.save(DEPLOYMENT_RECORD, {"status": "deploying", **common, "upload": "16MiB chunks, per-chunk sha256"})
    log_path = ctx.path("deployment.log")
    phases: list[dict] = []
    install_started = timer()
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        def on_line(line: str) -> None:
            now = clock().astimezone(timezone.utc)
            log.write(now.strftime("%H:%M:%SZ") + " " + line + "\n")
            log.flush()
            timing = parse_timing(line)
            if timing is not None:
                phases.append({**timing, "at": now.isoformat()})

        exit_code = device.stream(update_command(remote_path, sha256, before["active"], release_id,
                                                 choice["format"]), on_line)
    install_seconds = round(timer() - install_started, 1)
    record = {"status": "passed" if exit_code == 0 else "failed", **common, "completed_at": clock().isoformat(),
              "exit_code": exit_code, "install_seconds": install_seconds, "device_timing": phases}
    ctx.save(DEPLOYMENT_RECORD, record)
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:]:
        out(line)
    out(f"upload {upload_seconds:.1f}s in {len(chunks)} parts, install {install_seconds:.1f}s ({choice['format']})")
    if exit_code != 0:
        if choice["format"] == release_delta.ARTIFACT_FORMAT:
            raise ReleaseError(
                f"Delta deployment returned exit {exit_code}; inspect deployment.log and rollback state "
                "(a refused delta stops before any release switch). To retry with the full artifact, "
                "start a new task folder (baseline, build) and run deploy with --full.")
        raise ReleaseError(f"Transactional deployment returned exit {exit_code}; inspect rollback state.")
    return record
