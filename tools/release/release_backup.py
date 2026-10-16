"""Replace the single previous-release backup after the new release proved itself.

``.backups/previous`` holds exactly one archive plus its identity manifest. It
is rotated only after the deployed release physically displayed the accepted
plugin, and the old archive is deleted only after the new archive and manifest
were both published and re-verified.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import uuid

from release_build import PREVIOUS_BACKUP_DIR, RETENTION, backup_archive_name, baseline_archive_name, file_sha256
from release_device import RELEASES_DIR
from release_checks import parse_time, require_label
from release_task import TaskContext, is_reparse_point, require, require_commit, same_path

MANIFEST = "manifest.json"


def check_rotation_evidence(prepared: dict, draft: dict, deployment: dict, current: dict,
                            acceptance: dict, plugin: str) -> None:
    """Refuse unless the deployment, the prepared backup and the physical display proof all agree."""
    require(deployment.get("status") == "passed" and deployment.get("commit") == current.get("commit")
            and current.get("status") == "passed", "Successful validated deployment required.")
    require(prepared.get("status") == "prepared_verified_not_rotated" and prepared.get("commit") == draft.get("commit")
            and prepared.get("commit") == current.get("base_commit")
            and draft.get("sha256") == prepared.get("artifact_sha256"), "Prepared backup identity mismatch.")
    require_commit(draft.get("commit"), "Replacement backup commit")
    release_id = deployment["release_id"]
    require(acceptance.get("active_release") == RELEASES_DIR + "/" + release_id
            and acceptance.get("previous_release") == draft.get("matches_device_previous"),
            "Acceptance release identity mismatch.")
    expected = sorted([release_id, str(draft.get("matches_device_previous")).split("/")[-1]])
    require(sorted(acceptance.get("releases") or []) == expected, "Device must retain exactly current and previous.")
    source = acceptance.get("source") or {}
    require(acceptance.get("configuration_preserved") is True and not source.get("mismatched")
            and not source.get("removed_still_present") and (source.get("checked_files") or 0) > 0,
            "Source and configuration acceptance required.")
    service = acceptance.get("service") or {}
    healthz, readyz = acceptance.get("healthz") or {}, acceptance.get("readyz") or {}
    require(service.get("ActiveState") == "active" and service.get("SubState") == "running"
            and healthz.get("http_status") == 200 and healthz.get("payload", {}).get("status") == "alive"
            and healthz.get("payload", {}).get("release_id") == release_id
            and readyz.get("http_status") == 200 and readyz.get("payload", {}).get("status") == "ready"
            and readyz.get("payload", {}).get("release_id") == release_id, "Healthy ready deployed service required.")
    display, image = acceptance.get("display") or {}, acceptance.get("image") or {}
    require(display.get("hardware_written") is True
            and (display.get("logical_target") or {}).get("plugin_id") == plugin
            and image.get("http_status") == 200 and image.get("matches_manifest") is True
            and image.get("matches_runtime_commit") is True
            and (acceptance.get("runtime_display") or {}).get("state") == "committed",
            "Matching physical display proof of the accepted plugin required.")
    committed, completed = parse_time(display.get("committed_at")), parse_time(deployment.get("completed_at"))
    require(committed and completed and committed >= completed, "Display proof predates deployment completion.")
    require(str(acceptance.get("var_tmp_transport_zips")) == "0", "Device transport archive cleanup is incomplete.")


def existing_backup(previous: Path) -> tuple[Path | None, dict | None]:
    """Return the current single backup archive and manifest, or (None, None) for an empty folder."""
    entries = sorted(previous.iterdir(), key=lambda item: item.name)
    if not entries:
        return None, None
    for item in entries:
        require(item.is_file() and not is_reparse_point(item) and same_path(item.parent, previous),
                f"Unsafe existing backup entry: {item.name}")
    manifest_path = previous / MANIFEST
    require(manifest_path.is_file(), "Previous backup has no identity manifest.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    archive = previous / backup_archive_name(require_commit(manifest.get("commit"), "Existing backup commit"))
    require(sorted(item.name for item in entries) == sorted([archive.name, MANIFEST]),
            "Previous backup layout changed.")
    require(same_path(Path(manifest.get("artifact", "")), archive), "Previous backup identity changed.")
    require(file_sha256(archive) == manifest.get("sha256"), "Existing backup hash mismatch.")
    return archive, manifest


def rotate(ctx: TaskContext, plugin: str, label: str | None = None, *, execute: bool,
           clock=lambda: datetime.now(timezone.utc)) -> dict:
    """Swap ``.backups/previous`` to the prepared baseline archive (dry run unless ``execute``)."""
    label = require_label(label or plugin + "-shown")
    prepared = ctx.load("backup-preparation.json")
    draft = ctx.load("previous-manifest-draft.json")
    deployment = ctx.load("deployment.json")
    current = ctx.load("current-manifest.json")
    acceptance = ctx.load("acceptance-" + label + ".json")
    check_rotation_evidence(prepared, draft, deployment, current, acceptance, plugin)

    previous = Path(os.path.abspath(ctx.repo / PREVIOUS_BACKUP_DIR))
    source = Path(os.path.abspath(prepared["artifact"]))
    target = Path(os.path.abspath(draft["artifact"]))
    require(same_path(source, ctx.task_dir / baseline_archive_name(draft["commit"])),
            "Prepared archive escaped its exact task path.")
    require(same_path(target, previous / backup_archive_name(draft["commit"])),
            "Backup target escaped its exact previous path.")
    require(previous.is_dir(), f"Missing backup folder: {previous}")
    for path in (ctx.task_dir, previous, source):
        require(not is_reparse_point(path), "Refusing backup rotation through a reparse point.")
    require(source.is_file(), "Prepared archive is missing; was this round already rotated?")
    require(file_sha256(source) == prepared["artifact_sha256"], "Prepared archive hash changed.")
    old_archive, old_manifest = existing_backup(previous)
    require(old_archive is None or not same_path(old_archive, target), "The previous backup already holds this commit.")
    plan = f"would replace: {old_archive.name if old_archive else '(empty)'}, {MANIFEST} -> {target.name}, {MANIFEST}"
    if not execute:
        return {"status": "dry_run", "plan": plan}

    staging = previous / (".staging-" + uuid.uuid4().hex)
    staging.mkdir()
    staged_archive, staged_manifest = staging / target.name, staging / MANIFEST
    shutil.copyfile(source, staged_archive)
    require(file_sha256(staged_archive) == prepared["artifact_sha256"],
            "Copied archive hash mismatch; old backup retained.")
    replacement = {"commit": draft["commit"], "artifact": str(target), "sha256": prepared["artifact_sha256"],
                   "matches_device_previous": draft["matches_device_previous"], "file_count": draft["file_count"],
                   "source_hash_set_sha256": draft["source_hash_set_sha256"],
                   "replaced_at_utc": clock().isoformat(), "retention": RETENTION}
    staged_manifest.write_text(json.dumps(replacement, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if old_manifest is not None:
        ctx.save("previous-backup-before.json", old_manifest)
    require(not target.exists(), "Backup target appeared during rotation.")
    os.rename(staged_archive, target)
    # Publish the replacement manifest only after its archive is in place, and keep
    # the old archive until the new archive and manifest have both been verified.
    os.replace(staged_manifest, previous / MANIFEST)
    published = json.loads((previous / MANIFEST).read_text(encoding="utf-8"))
    require(published.get("commit") == draft["commit"] and published.get("sha256") == prepared["artifact_sha256"]
            and file_sha256(target) == prepared["artifact_sha256"],
            "Published backup verification failed; old archive retained.")
    if old_archive is not None:
        old_archive.unlink()
    staging.rmdir()
    source.unlink()
    entries = sorted(item.name for item in previous.iterdir())
    require(entries == sorted([target.name, MANIFEST]), "Post-rotation layout verification failed.")
    record = {"status": "rotated_verified", "current_release": deployment["release_id"],
              "previous_commit": draft["commit"], "artifact": str(target),
              "artifact_sha256": prepared["artifact_sha256"],
              "entries": entries, "display_commit": acceptance["display"].get("commit_id"),
              "acceptance_label": label, "checked_at_utc": clock().isoformat()}
    ctx.save("backup-rotation.json", record)
    return record
