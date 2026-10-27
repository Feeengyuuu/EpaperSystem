"""Post-deployment installation of one owner-provided, device-only registered font."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import stat
import subprocess

from release_device import CURRENT_LINK, RELEASES_DIR
from release_task import PACKAGE, ReleaseError, is_reparse_point, require, require_commit

HELPER = "install/lib/private_score_fonts.py"
UPDATER = "install/inkypi-update"


def font_policy():
    path = Path(__file__).resolve().parents[2] / PACKAGE / HELPER
    spec = importlib.util.spec_from_file_location("release_private_score_font_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plan(ctx, league, source, *, purpose=None):
    deployment = ctx.require_deployed()
    manifest = ctx.load("current-manifest.json")
    commit = require_commit(deployment.get("commit"), "Deployed commit")
    require(manifest.get("status") == "passed" and manifest.get("commit") == commit,
            "Private font installation requires the deployed round's matching manifest")
    release_id = deployment.get("release_id")
    require(isinstance(release_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", release_id)
            and release_id.endswith("-" + commit[:8]), "Invalid deployed release id")
    artifact_sha = manifest.get("artifact_sha256")
    require(isinstance(artifact_sha, str) and re.fullmatch(r"[0-9a-f]{64}", artifact_sha),
            "Deployed artifact SHA256 is missing")
    hashes = manifest.get("hashes", {})
    require(all(re.fullmatch(r"[0-9a-f]{64}", hashes.get(path, "")) for path in (HELPER, UPDATER)),
            "The deployed release does not contain the private-font installer capability")
    policy = font_policy()
    require(bool(league) != bool(purpose), "Select exactly one private font league or purpose")
    if purpose:
        require(purpose in policy.PURPOSES, "Unsupported private font purpose")
        selection = {"purpose": purpose}
    else:
        require(league in policy.LEAGUES, "Unsupported private score font league")
        selection = {"league": league}
    source = Path(source)
    source = source if source.is_absolute() else ctx.repo / source
    require(not is_reparse_point(source) and source.is_file(), "Private font source must be a regular local file")
    name = policy.PURPOSES[purpose]["basename"] if purpose else f"sports-{league}-score{source.suffix.lower()}"
    require(source.suffix.lower() == Path(name).suffix, "Private font format is not allowed for this purpose")
    try:
        info = source.stat()
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= policy.MAX_BYTES, "Private font is empty or exceeds 2 MiB")
        with source.open("rb") as stream:
            payload = stream.read(policy.MAX_BYTES + 1)
        sha256 = hashlib.sha256(payload).hexdigest()
        font = policy.validate_font(payload, name, sha256, info.st_size)
    except (OSError, policy.PrivateFontError) as error:
        raise ReleaseError(str(error)) from error
    return {"status": "dry_run", **selection, "source": str(source.absolute()), "font": font,
            "destination": str(policy.FONT_DIRECTORY / name).replace("\\", "/"), "basename": name,
            "bytes": len(payload), "sha256": sha256, "release_id": release_id, "commit": commit,
            "artifact_sha256": artifact_sha, "transport": "pinned SSH stdin; no transport file",
            "mode": "0640", "owner": "root:inkypi"}, payload


def _save_attempt(ctx, league, record):
    name = f"private-fonts-{league}.json"
    history = ctx.load(name) if ctx.has(name) else {"attempts": []}
    history["attempts"].append(record)
    ctx.save(name, history)


def install(ctx, league, source, *, purpose=None, execute=False, connect):
    plan, payload = _plan(ctx, league, source, purpose=purpose)
    attempt_key = purpose or league
    if not execute:
        ctx.save(f"private-fonts-{attempt_key}-plan.json", plan)
        return plan
    record = {**plan, "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        device = connect()
        expected = RELEASES_DIR + "/" + plan["release_id"]
        require(device.resolved_link(CURRENT_LINK) == expected, "Deployed round is no longer the current release")
        command = shlex.join(["sudo", "-n", "/usr/local/sbin/inkypi-update", "--private-score-font",
                              plan["basename"], plan["sha256"], str(plan["bytes"]), plan["release_id"],
                              plan["commit"], plan["artifact_sha256"]])
        result = device.run(command, input=payload, timeout=90)
        record["exit_code"] = result.returncode
        if result.returncode:
            # Do not persist arbitrary SSH stderr: it can contain unrelated secrets.
            try:
                reason = json.loads(result.stderr).get("reason")
            except (ValueError, AttributeError):
                reason = None
            raise ReleaseError("Private font installer refused (exit " + str(result.returncode) + ")"
                               + (": " + reason if isinstance(reason, str) else ""))
        receipt = json.loads(result.stdout)
        require(receipt.get("status") in {"installed", "unchanged"}, "Private font receipt has no terminal success")
        for key in ("destination", "sha256", "bytes", "release_id", "commit", "artifact_sha256", "mode"):
            require(receipt.get(key) == plan[key], "Private font receipt does not match " + key)
        require(receipt.get("uid") == 0 and isinstance(receipt.get("gid"), int), "Private font receipt has invalid ownership")
        record.update(status=receipt["status"], device_font=receipt.get("font"), uid=0, gid=receipt["gid"])
    except (ReleaseError, OSError, ValueError, subprocess.SubprocessError) as error:
        record.update(status="failed_or_unconfirmed", error=str(error))
        raise ReleaseError(str(error)) from error
    finally:
        record["completed_at"] = datetime.now(timezone.utc).isoformat()
        _save_attempt(ctx, attempt_key, record)
    return record
