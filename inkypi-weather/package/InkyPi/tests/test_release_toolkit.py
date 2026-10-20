"""Argument handling and pure helpers of the tracked release toolkit (tools/release).

No test contacts a device: SSH/SCP are replaced by in-memory fakes and every
repository is a temporary folder. 192.0.2.0/24 is the documentation network.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLKIT = REPO_ROOT / "tools" / "release"
sys.path.insert(0, str(TOOLKIT))

import epaper_release  # noqa: E402
import release_backup  # noqa: E402
import release_build  # noqa: E402
import release_checks  # noqa: E402
import release_deploy  # noqa: E402
import release_device  # noqa: E402
from release_task import ReleaseError, TaskContext, resolve_task_dir  # noqa: E402

BASE = "95b4c029d58d2c6fd50ed599bab1bbda6a28d97e"
HEAD = "85008032a6154a38d000ac39079b8a9fc66971a1"
OLD = "2050df7d4a7a4f76c8fc0b5f083aaac2bf77a94d"
ACTIVE = "/opt/inkypi/releases/deploy-20261016T185606Z-tidyfont-95b4c029"
NEW_RELEASE = "deploy-20261016T195416Z-layout-85008032"
DEPLOYED_AT = datetime(2026, 10, 16, 19, 54, 16, tzinfo=timezone.utc)
ADDRESS = "192.0.2.10"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def restore_process_state(monkeypatch):
    """Commands isolate TEMP for their process; keep that from leaking into other tests."""
    for name in ("TEMP", "TMP", "TMPDIR", "PYTHONDONTWRITEBYTECODE"):
        original = os.environ.get(name)
        monkeypatch.setenv(name, original or "")
        if original is None:
            monkeypatch.delenv(name)
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "outputs").mkdir(parents=True)
    return root


@pytest.fixture
def ctx(repo):
    return TaskContext.open("outputs/round-b", repo, create=True)


# --- task folder rules -------------------------------------------------------------------------------------------


def test_task_dir_is_a_direct_child_of_outputs_relative_to_the_repository(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert resolve_task_dir("outputs/deploy-20261017", repo) == repo / "outputs" / "deploy-20261017"
    assert resolve_task_dir(repo / "outputs" / "x.y_z-1", repo) == repo / "outputs" / "x.y_z-1"


@pytest.mark.parametrize("value", ["outputs", "outputs/a/b", "tools", "outputs/../tools", "../outputs/a",
                                   "outputs/bad name", "outputs/-lead", "outputs/" + "a" * 65])
def test_task_dir_outside_outputs_or_with_unsafe_name_is_refused(repo, value):
    with pytest.raises(ReleaseError):
        resolve_task_dir(value, repo)


def test_task_dir_that_is_a_link_is_refused(repo, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = repo / "outputs" / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            pytest.skip("symlinks are not available to this account")
        import _winapi

        _winapi.CreateJunction(str(target), str(link))  # junctions need no privilege

    with pytest.raises(ReleaseError, match="reparse point"):
        resolve_task_dir("outputs/linked", repo)


def test_task_folder_must_exist_unless_created(repo):
    with pytest.raises(ReleaseError, match="does not exist"):
        TaskContext.open("outputs/missing", repo)

    assert TaskContext.open("outputs/missing", repo, create=True).task_dir.is_dir()


@pytest.mark.parametrize("name", ["../escape.json", "a/b.json", "a\\b.json", "", ".hidden"])
def test_evidence_names_cannot_leave_the_task_folder(ctx, name):
    with pytest.raises(ReleaseError, match="Unsafe evidence"):
        ctx.path(name)


def test_evidence_round_trips_with_byte_order_mark(ctx):
    ctx.path("deployment.json").write_text('﻿{"status": "passed"}', encoding="utf-8")
    ctx.save("record.json", {"name": "日本"})

    assert ctx.load("deployment.json") == {"status": "passed"}
    assert ctx.load("record.json") == {"name": "日本"}
    assert ctx.require_deployed() == {"status": "passed"}


def test_a_task_folder_with_deployment_json_is_never_reused(ctx):
    ctx.save("deployment.json", {"status": "deploying"})

    with pytest.raises(ReleaseError, match="already has deployment.json"):
        ctx.require_not_deployed()
    with pytest.raises(ReleaseError, match="terminal and passed"):
        ctx.require_deployed()
    for step in (release_build.build, release_build.backup, release_deploy.validate_inputs):
        with pytest.raises(ReleaseError, match="already has deployment.json"):
            step(ctx)


def test_isolate_temp_keeps_temporary_files_in_the_project(ctx):
    temp = ctx.isolate_temp()

    assert temp == ctx.repo / ".tmp" / "release" / "round-b"
    assert temp.is_dir()
    assert {os.environ[name] for name in ("TEMP", "TMP", "TMPDIR")} == {str(temp)}
    assert Path(tempfile.gettempdir()) == temp


# --- command line ------------------------------------------------------------------------------------------------


def parse(*argv):
    return epaper_release.build_parser().parse_args(list(argv))


def test_every_command_needs_a_task_folder(capsys):
    for command in ("baseline", "build", "backup", "health-gate", "snapshot", "journal", "rotate-backup"):
        with pytest.raises(SystemExit):
            parse(command)
    capsys.readouterr()


def test_deploy_and_rotation_are_dry_runs_unless_executed():
    deploy = parse("deploy", "--task-dir", "outputs/r", "--release-label", "layout")
    rotate = parse("rotate-backup", "--task-dir", "outputs/r")

    assert deploy.execute is False and deploy.address is None
    assert rotate.execute is False and rotate.acceptance_plugin == "apod" and rotate.acceptance_label is None
    assert parse("deploy", "--task-dir", "outputs/r", "--release-label", "x", "--execute",
                 "--address", ADDRESS).execute is True


def test_deploy_requires_a_release_label(capsys):
    with pytest.raises(SystemExit):
        parse("deploy", "--task-dir", "outputs/r")
    assert "--release-label" in capsys.readouterr().err


def test_acceptance_command_defaults():
    gate = parse("health-gate", "--task-dir", "outputs/r")
    watch = parse("watch", "--task-dir", "outputs/r")
    shown = parse("show-plugins", "--task-dir", "outputs/r", "apod", "vehicle_status")

    assert (gate.before, gate.after, gate.allow_not_ready) == ("predeploy", "postdeploy", False)
    assert (watch.timeout, watch.interval, watch.count) == (1050, 25, 2)
    assert shown.plugins == ["apod", "vehicle_status"]
    assert parse("snapshot", "--task-dir", "outputs/r").label == "postdeploy"
    assert parse("baseline", "--task-dir", "outputs/r", "--base-manifest", "m.json").base_manifest == Path("m.json")


def test_show_plugins_and_probe_need_plugin_ids(capsys):
    with pytest.raises(SystemExit):
        parse("show-plugins", "--task-dir", "outputs/r")
    with pytest.raises(SystemExit):
        parse("probe", "--task-dir", "outputs/r", "--label", "x")
    capsys.readouterr()


def test_main_refuses_a_task_folder_outside_outputs(repo, capsys):
    assert epaper_release.main(["build", "--task-dir", "tools"], repo=repo) == 2
    assert "refused: Task folder must be a direct child" in capsys.readouterr().err


def test_main_refuses_a_missing_task_folder_for_later_steps(repo, capsys):
    assert epaper_release.main(["backup", "--task-dir", "outputs/new"], repo=repo) == 2
    assert not (repo / "outputs" / "new").exists()
    assert "does not exist" in capsys.readouterr().err


def test_main_deploy_dry_run_validates_without_touching_the_device(ctx, capsys, monkeypatch):
    prepare_deploy(ctx)
    monkeypatch.setattr(release_device.Device, "connect", classmethod(lambda *a, **k: pytest.fail("contacted")))

    assert epaper_release.main(["deploy", "--task-dir", "outputs/round-b", "--release-label", "layout"],
                               repo=ctx.repo) == 0
    plan = json.loads(capsys.readouterr().out)

    assert plan["status"] == "dry_run" and plan["chunks"] == 1 and plan["chunk_bytes"] == 16 * 1024 * 1024
    assert re.fullmatch(r"deploy-\d{8}T\d{6}Z-layout-85008032", plan["release_id_example"])
    assert sorted(path.name for path in ctx.task_dir.iterdir()) == [
        "current-manifest.json", "inkypi-85008032.zip", "preflight.json"]


# --- device access -----------------------------------------------------------------------------------------------


def test_ssh_options_pin_the_host_key_and_identity(tmp_path):
    options = release_device.ssh_options(tmp_path)

    assert "StrictHostKeyChecking=yes" in options
    assert "HostKeyAlias=coloredepaperframe.local" in options
    assert "UserKnownHostsFile=" + str(tmp_path / ".tmp/epaperpod_known_hosts") in options
    assert {"BatchMode=yes", "IdentitiesOnly=yes"} <= set(options)
    assert options[options.index("-i") + 1] == str(tmp_path / ".ssh/epaperpod_codex_20260525")
    assert not any("accept-new" in option for option in options)


def test_address_is_resolved_from_the_device_name_and_validated():
    calls = []

    def resolver(host, port, family, kind):
        calls.append((host, family))
        return [(family, kind, 6, "", (ADDRESS, port))]

    assert release_device.resolve_address(None, resolver) == ADDRESS
    assert calls == [("ColoredEpaperFrame.local", release_device.socket.AF_INET)]
    assert release_device.resolve_address("192.0.2.200", resolver) == "192.0.2.200"
    for bad in ("999.1.1.1", "192.0.2", "device.local", "192.0.2.1; rm"):
        with pytest.raises(ReleaseError, match="could not be resolved"):
            release_device.resolve_address(bad, resolver)

    def unavailable(*_args):
        raise OSError("no mdns")

    with pytest.raises(ReleaseError):
        release_device.resolve_address(None, unavailable)


def test_release_name_accepts_only_release_folders():
    assert release_device.release_name(ACTIVE) == "deploy-20261016T185606Z-tidyfont-95b4c029"
    for bad in ("/opt/inkypi/current", "/opt/inkypi/releases/a/b", "/opt/inkypi/releases/", ""):
        with pytest.raises(ReleaseError):
            release_device.release_name(bad)


def test_device_commands_are_pinned_and_never_echo_stderr(tmp_path):
    seen = []

    def runner(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 5 if argv[-1] == "fail" else 0, b"ok", b"password=hunter2")

    device = release_device.Device(tmp_path, ADDRESS, runner=runner)

    assert device.remote("hostname") == b"ok"
    with pytest.raises(ReleaseError) as refused:
        device.remote("fail")
    assert "hunter2" not in str(refused.value) and "returned 5" in str(refused.value)
    assert device.copy(tmp_path / "part000", "/var/tmp/x.part000") == 0
    ssh, scp = seen[0], seen[2]
    assert ssh[-2:] == ["feeengyuuu@" + ADDRESS, "hostname"] and "StrictHostKeyChecking=yes" in ssh
    assert scp[1] == "-O" and scp[-1] == "feeengyuuu@" + ADDRESS + ":/var/tmp/x.part000"
    with pytest.raises(ReleaseError):
        release_device.Device(tmp_path, "ColoredEpaperFrame.local")


def test_safe_drops_secret_keys_and_provider_urls():
    value = {"api_key": "k", "Session_Cookie": "c", "nested": [{"token": "t", "note": "see https://x.test/a?b=1 now"}],
             "status": "ok"}

    assert release_device.safe(value) == {"nested": [{"note": "see [provider-url] now"}], "status": "ok"}


# --- deployment ---------------------------------------------------------------------------------------------------


def prepare_deploy(ctx, payload=b"release-bytes-" * 3):
    artifact = ctx.path("inkypi-85008032.zip")
    artifact.write_bytes(payload)
    ctx.save("preflight.json", {"status": "passed", "commit": BASE, "active": ACTIVE})
    ctx.save("current-manifest.json", {"status": "passed", "commit": HEAD, "base_commit": BASE,
                                       "artifact": str(artifact), "artifact_sha256": sha(payload)})
    return payload


class FakeDevice:
    """In-memory device that hashes uploaded parts like the real shell commands do."""

    def __init__(self, active=ACTIVE, bad_parts=(), flaky_parts=(), collision=False, update_exit=0):
        self.active, self.bad_parts, self.flaky = active, set(bad_parts), set(flaky_parts)
        self.collision, self.update_exit = collision, update_exit
        self.files, self.commands, self.copies = {}, [], []

    def copy(self, local, remote_path):
        self.copies.append(remote_path)
        self.files[remote_path] = Path(local).read_bytes()
        return 0

    def run(self, command, *, timeout=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, input=None):
        self.commands.append(command)

        def done(code, output=b""):
            return subprocess.CompletedProcess(command, code, output, b"")

        if command.startswith("readlink -f"):
            return done(0, (self.active + "\n").encode())
        if command.startswith("set -e; test ! -e"):
            return done(1 if self.collision else 0)
        if command.endswith("echo verified"):
            path, expected = re.search(r"sha256sum (\S+) .* = '([0-9a-f]{64})'", command).groups()
            name = path.rsplit(".", 1)[1]
            if name in self.bad_parts or name in self.flaky:
                self.flaky.discard(name)
                return done(1)
            return done(0 if sha(self.files[path]) == expected else 1, b"verified\n")
        if command.endswith("echo assembled"):
            parts, target = re.match(r"set -e; cat (.+) > (\S+);", command).groups()
            self.files[target] = b"".join(self.files.pop(part) for part in parts.split())
            expected = re.search(r"= '([0-9a-f]{64})'", command).group(1)
            return done(0 if sha(self.files[target]) == expected else 1, b"assembled\n")
        if command.startswith("rm -f -- "):
            return done(0)
        if "inkypi-update" in command:
            stdout.write(b"InkyPi release preflight passed\nInkyPi release committed\n")
            return done(self.update_exit)
        raise AssertionError("unexpected command: " + command)


def run_deploy(ctx, device, monkeypatch, chunk_bytes=16, execute=True):
    monkeypatch.setattr(release_deploy, "CHUNK_BYTES", chunk_bytes)
    sleeps, printed = [], []
    result = release_deploy.deploy(ctx, "layout", execute=execute, connect=lambda: device, sleep=sleeps.append,
                                   clock=lambda: DEPLOYED_AT, out=printed.append)
    return result, sleeps, printed


def test_release_id_names_time_label_and_commit():
    pacific = timezone(timedelta(hours=-7))

    assert release_deploy.compose_release_id("layout", HEAD, DEPLOYED_AT) == NEW_RELEASE
    assert release_deploy.compose_release_id("layout", HEAD, DEPLOYED_AT.astimezone(pacific)) == NEW_RELEASE
    assert release_deploy.remote_artifact_path(NEW_RELEASE) == "/var/tmp/inkypi-" + NEW_RELEASE + ".zip"
    for label in ("", "Layout", "a b", "-a", "a/b", "x" * 25):
        with pytest.raises(ReleaseError, match="Release label"):
            release_deploy.compose_release_id(label, HEAD, DEPLOYED_AT)
    with pytest.raises(ReleaseError, match="full commit"):
        release_deploy.compose_release_id("layout", "85008032", DEPLOYED_AT)


def test_transport_keeps_the_wifi_safe_chunk_policy():
    assert release_deploy.CHUNK_BYTES == 16 * 1024 * 1024
    assert (release_deploy.UPLOAD_ATTEMPTS, release_deploy.RETRY_PAUSE_SECONDS,
            release_deploy.CHUNK_PAUSE_SECONDS) == (3, 20, 5)


def test_artifact_splits_into_numbered_hashed_parts(tmp_path):
    artifact = tmp_path / "a.zip"
    artifact.write_bytes(b"0123456789")
    remote = release_deploy.remote_artifact_path(NEW_RELEASE)

    chunks = release_deploy.split_artifact(artifact, tmp_path / "chunks", remote, chunk_bytes=4)

    assert [chunk.path.name for chunk in chunks] == ["part000", "part001", "part002"]
    assert [chunk.remote_path for chunk in chunks] == [remote + ".part000", remote + ".part001", remote + ".part002"]
    assert [chunk.sha256 for chunk in chunks] == [sha(b"0123"), sha(b"4567"), sha(b"89")]
    with pytest.raises(ReleaseError, match="Existing chunk directory"):
        release_deploy.split_artifact(artifact, tmp_path / "chunks", remote, chunk_bytes=4)
    release_deploy.remove_chunks(tmp_path / "chunks", chunks)
    assert not (tmp_path / "chunks").exists()


def test_empty_artifact_is_refused_and_leaves_no_chunk_folder(tmp_path):
    artifact = tmp_path / "a.zip"
    artifact.write_bytes(b"")

    with pytest.raises(ReleaseError, match="empty"):
        release_deploy.split_artifact(artifact, tmp_path / "chunks", "/var/tmp/inkypi-deploy-x.zip", chunk_bytes=4)
    assert not (tmp_path / "chunks").exists()


def test_remote_parts_must_stay_in_var_tmp(tmp_path):
    artifact = tmp_path / "a.zip"
    artifact.write_bytes(b"data")

    with pytest.raises(ReleaseError, match="Unsafe remote chunk path"):
        release_deploy.split_artifact(artifact, tmp_path / "chunks", "/home/x/inkypi-deploy-x.zip", chunk_bytes=4)
    assert not (tmp_path / "chunks").exists()


def test_remote_commands_check_hashes_and_the_active_release():
    chunks = [release_deploy.Chunk(Path("part000"), "/var/tmp/inkypi-r.zip.part000", "a" * 64),
              release_deploy.Chunk(Path("part001"), "/var/tmp/inkypi-r.zip.part001", "b" * 64)]

    assert release_deploy.collision_check_command("/var/tmp/inkypi-r.zip", chunks) == (
        "set -e; test ! -e /var/tmp/inkypi-r.zip; test ! -e /var/tmp/inkypi-r.zip.part000; "
        "test ! -e /var/tmp/inkypi-r.zip.part001")
    assert release_deploy.verify_command(chunks[0]) == (
        "set -e; test \"$(sha256sum /var/tmp/inkypi-r.zip.part000 | cut -d' ' -f1)\" = '" + "a" * 64
        + "'; echo verified")
    assert release_deploy.assemble_command("/var/tmp/inkypi-r.zip", chunks, "c" * 64) == (
        "set -e; cat /var/tmp/inkypi-r.zip.part000 /var/tmp/inkypi-r.zip.part001 > /var/tmp/inkypi-r.zip; "
        "test \"$(sha256sum /var/tmp/inkypi-r.zip | cut -d' ' -f1)\" = '" + "c" * 64 + "'; "
        "rm -f -- /var/tmp/inkypi-r.zip.part000 /var/tmp/inkypi-r.zip.part001; echo assembled")
    assert release_deploy.cleanup_command("/var/tmp/inkypi-r.zip", chunks) == (
        "rm -f -- /var/tmp/inkypi-r.zip /var/tmp/inkypi-r.zip.part000 /var/tmp/inkypi-r.zip.part001")
    assert release_deploy.update_command("/var/tmp/inkypi-r.zip", "c" * 64, ACTIVE, "r") == (
        "set -Eeuo pipefail; trap 'rm -f -- /var/tmp/inkypi-r.zip' EXIT; "
        "test \"$(sha256sum /var/tmp/inkypi-r.zip | cut -d' ' -f1)\" = '" + "c" * 64 + "'; "
        "test \"$(readlink -f /opt/inkypi/current)\" = '" + ACTIVE + "'; "
        "sudo -n /usr/local/sbin/inkypi-update --artifact /var/tmp/inkypi-r.zip --sha256 " + "c" * 64
        + " --release-id r; systemctl is-active --quiet inkypi.service")


def test_deploy_uploads_verified_chunks_with_pauses_then_updates(ctx, monkeypatch):
    payload = prepare_deploy(ctx)
    device = FakeDevice()

    result, sleeps, printed = run_deploy(ctx, device, monkeypatch)

    remote = "/var/tmp/inkypi-" + NEW_RELEASE + ".zip"
    assert result["status"] == "passed" and result["release_id"] == NEW_RELEASE and result["exit_code"] == 0
    assert ctx.load("deployment.json") == result
    assert ctx.load("upload-plan.json")["parts"] == [remote + f".part00{index}" for index in range(3)]
    assert device.copies == ctx.load("upload-plan.json")["parts"]
    assert sleeps == [5, 5, 5]
    assert printed[:3] == ["uploaded part000", "uploaded part001", "uploaded part002"]
    assert printed[3:] == ["InkyPi release preflight passed", "InkyPi release committed"]
    assert device.files == {remote: payload}
    assert device.commands[0] == "readlink -f /opt/inkypi/current"
    assert device.commands[-1] == release_deploy.update_command(remote, sha(payload), ACTIVE, NEW_RELEASE)
    assert not ctx.has("upload-chunks")
    assert "InkyPi release committed" in ctx.path("deployment.log").read_text(encoding="utf-8")


def test_deploy_retries_an_unverified_chunk_with_backoff(ctx, monkeypatch):
    prepare_deploy(ctx)
    device = FakeDevice(flaky_parts={"part001"})

    result, sleeps, _printed = run_deploy(ctx, device, monkeypatch)

    assert result["status"] == "passed"
    assert [path.rsplit(".", 1)[1] for path in device.copies] == ["part000", "part001", "part001", "part002"]
    assert sleeps == [5, 20, 5, 5]


def test_deploy_cleans_the_device_and_local_chunks_after_a_failed_chunk(ctx, monkeypatch):
    prepare_deploy(ctx)
    device = FakeDevice(bad_parts={"part001"})

    with pytest.raises(ReleaseError, match="Chunk part001 failed after retries"):
        run_deploy(ctx, device, monkeypatch)

    assert device.commands[-1] == ctx.load("upload-plan.json")["cleanup_command"]
    assert not ctx.has("deployment.json") and not ctx.has("upload-chunks")


def test_deploy_never_cleans_remote_paths_it_did_not_create(ctx, monkeypatch):
    prepare_deploy(ctx)
    device = FakeDevice(collision=True)

    with pytest.raises(ReleaseError, match="already exist"):
        run_deploy(ctx, device, monkeypatch)

    assert not any(command.startswith("rm -f") for command in device.commands)
    assert device.copies == [] and not ctx.has("upload-chunks") and not ctx.has("deployment.json")


def test_deploy_refuses_when_the_active_release_moved(ctx, monkeypatch):
    prepare_deploy(ctx)
    device = FakeDevice(active="/opt/inkypi/releases/deploy-other")

    with pytest.raises(ReleaseError, match="Active release changed"):
        run_deploy(ctx, device, monkeypatch)

    assert device.commands == ["readlink -f /opt/inkypi/current"] and not ctx.has("upload-plan.json")


def test_failed_update_is_recorded_as_a_terminal_failure(ctx, monkeypatch):
    prepare_deploy(ctx)

    with pytest.raises(ReleaseError, match="returned exit 1; inspect rollback state"):
        run_deploy(ctx, FakeDevice(update_exit=1), monkeypatch)

    record = ctx.load("deployment.json")
    assert record["status"] == "failed" and record["exit_code"] == 1
    with pytest.raises(ReleaseError, match="already has deployment.json"):
        run_deploy(ctx, FakeDevice(), monkeypatch)


def test_dry_run_deploy_never_connects(ctx, monkeypatch):
    prepare_deploy(ctx)

    result = release_deploy.deploy(ctx, "layout", execute=False, connect=lambda: pytest.fail("contacted"),
                                   clock=lambda: DEPLOYED_AT)

    assert result["status"] == "dry_run" and result["release_id_example"] == NEW_RELEASE
    assert not ctx.has("upload-plan.json")


@pytest.mark.parametrize("change, message", [
    (lambda manifest, preflight: manifest.update(artifact_sha256="0" * 64), "Artifact hash mismatch"),
    (lambda manifest, preflight: manifest.update(base_commit=OLD), "Unexpected release ancestry"),
    (lambda manifest, preflight: manifest.update(commit=BASE), "Unexpected release ancestry"),
    (lambda manifest, preflight: preflight.update(status="failed"), "Validated source and baseline"),
    (lambda manifest, preflight: preflight.update(active="/tmp/x"), "Unexpected release path"),
])
def test_deploy_validates_local_evidence_first(ctx, change, message):
    prepare_deploy(ctx)
    manifest, preflight = ctx.load("current-manifest.json"), ctx.load("preflight.json")
    change(manifest, preflight)
    ctx.save("current-manifest.json", manifest)
    ctx.save("preflight.json", preflight)

    with pytest.raises(ReleaseError, match=message):
        release_deploy.validate_inputs(ctx)


def test_deploy_refuses_an_artifact_outside_the_task_folder(ctx):
    prepare_deploy(ctx)
    other = ctx.repo / "outputs" / "other"
    other.mkdir()
    outside = other / "inkypi-85008032.zip"
    shutil.copyfile(ctx.path("inkypi-85008032.zip"), outside)
    manifest = ctx.load("current-manifest.json")
    ctx.save("current-manifest.json", {**manifest, "artifact": str(outside)})

    with pytest.raises(ReleaseError, match="escaped task directory"):
        release_deploy.validate_inputs(ctx)


# --- baseline, packaging and build helpers ------------------------------------------------------------------------


def write_round(repo, name, release_id, commit, status="passed"):
    folder = repo / "outputs" / name
    folder.mkdir()
    (folder / "deployment.json").write_text(json.dumps({"status": status, "release_id": release_id,
                                                        "commit": commit}), encoding="utf-8")
    (folder / "current-manifest.json").write_text(json.dumps({"status": "passed", "commit": commit,
                                                              "hashes": {"src/a.py": "1" * 64}}), encoding="utf-8")
    return folder


def test_base_manifest_is_found_through_the_round_that_deployed_the_live_release(ctx):
    write_round(ctx.repo, "deploy-a", "deploy-old", OLD)
    found = write_round(ctx.repo, "deploy-b", NEW_RELEASE, HEAD)
    write_round(ctx.repo, "deploy-c", NEW_RELEASE, HEAD, status="failed")

    assert release_build.discover_base_manifest(ctx, NEW_RELEASE) == found / "current-manifest.json"
    with pytest.raises(ReleaseError, match="No task folder"):
        release_build.discover_base_manifest(ctx, "deploy-unknown")


def test_ambiguous_or_inconsistent_base_rounds_are_refused(ctx):
    write_round(ctx.repo, "deploy-a", NEW_RELEASE, HEAD)
    write_round(ctx.repo, "deploy-b", NEW_RELEASE, HEAD)
    with pytest.raises(ReleaseError, match="Several task folders"):
        release_build.discover_base_manifest(ctx, NEW_RELEASE)

    shutil.rmtree(ctx.repo / "outputs" / "deploy-b")
    manifest = ctx.repo / "outputs" / "deploy-a" / "current-manifest.json"
    manifest.write_text(json.dumps({"status": "passed", "commit": OLD}), encoding="utf-8")
    with pytest.raises(ReleaseError, match="does not match its deployment.json"):
        release_build.discover_base_manifest(ctx, NEW_RELEASE)


class BaselineDevice:
    def __init__(self, match=True, active=ACTIVE):
        self.match, self.active = match, active

    def resolved_link(self, link):
        return self.active

    def run(self, command, *, timeout=None, input=None, **_kwargs):
        assert command == "python3 -" and b"expected={'src/a.py': '" in input
        record = {"active": self.active, "previous": "/opt/inkypi/releases/deploy-old", "releases": [],
                  "service": {"ActiveState": "active", "SubState": "running"},
                  "source_hashes_match": self.match, "mismatched_files": [] if self.match else ["src/a.py"]}
        return subprocess.CompletedProcess(command, 0, json.dumps(record).encode(), b"")

    def document(self, path):
        if path == release_device.CONFIG:
            return {"plugin_cycle_interval_seconds": 300, "playlist_config": {"playlists": []}}
        return {"commit_id": "display-1"}

    def http_json(self, route):
        return {"http_status": 200, "payload": {"status": "alive" if route == "/healthz" else "ready"}}


def test_baseline_records_the_live_release_and_its_proven_source(ctx):
    write_round(ctx.repo, "deploy-a", "deploy-20261016T185606Z-tidyfont-95b4c029", BASE)

    record = release_build.baseline(ctx, BaselineDevice())

    assert record["commit"] == BASE and record["base_release"] == "deploy-20261016T185606Z-tidyfont-95b4c029"
    assert record["display_commit_before"] == "display-1" and record["configuration"]["instance_count"] == 0
    assert ctx.load("base-manifest.json")["commit"] == BASE
    assert ctx.load("preflight.json") == record
    assert ctx.load("baseline-source-proof.json")["status"] == "passed"


def test_baseline_refuses_live_source_that_differs_from_the_manifest(ctx):
    write_round(ctx.repo, "deploy-a", "deploy-20261016T185606Z-tidyfont-95b4c029", BASE)

    with pytest.raises(ReleaseError, match="Live source differs"):
        release_build.baseline(ctx, BaselineDevice(match=False))
    assert not ctx.has("preflight.json")


def test_changed_members_and_dependency_changes():
    before = {"a": "1", "b": "2", "gone": "3"}
    after = {"a": "1", "b": "9", "new": "4"}
    old_lock = "requests==2.32.3 \\\n    --hash=sha256:x\npillow==11.0.0\n"
    new_lock = "requests==2.32.4 \\\n    --hash=sha256:y\npillow==11.0.0\nidna==3.10\n"

    assert release_build.changed_members(before, after) == {"b", "gone", "new"}
    assert release_build.requirement_versions(old_lock) == {"requests": "2.32.3", "pillow": "11.0.0"}
    assert release_build.dependency_changes(old_lock, new_lock) == {
        "idna": {"before": None, "after": "3.10"}, "requests": {"before": "2.32.3", "after": "2.32.4"}}
    assert release_build.dependency_changes(old_lock, old_lock) == {}


def test_expected_change_lists_must_be_package_relative(tmp_path):
    good, bad = tmp_path / "good.txt", tmp_path / "bad.txt"
    good.write_text("src/utils/glyph_text.py\ntests/test_glyph_text.py\n", encoding="utf-8")
    bad.write_text("src/a.py ../../etc/passwd\n", encoding="utf-8")

    assert release_build.read_expected_changes(good) == {"src/utils/glyph_text.py", "tests/test_glyph_text.py"}
    with pytest.raises(ReleaseError, match="Unsafe expected change"):
        release_build.read_expected_changes(bad)


def test_archive_names_use_the_short_commit():
    assert release_build.backup_archive_name(BASE) == "inkypi-95b4c029.zip"
    assert release_build.baseline_archive_name(BASE) == "baseline-95b4c029.zip"
    with pytest.raises(ReleaseError):
        release_build.backup_archive_name("95b4c029")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "user.name=release-test",
                           "-c", "user.email=release-test@example.invalid", "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_package_uses_tracked_files_and_the_release_archive_policy(tmp_path):
    repo = tmp_path / "repo"
    package = repo / release_build.PACKAGE
    (package / "install/lib").mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / release_build.PACKAGE / "install/lib/release_archive.py",
                    package / "install/lib/release_archive.py")
    files = {"src/app.py": b"print(1)\r\n", "install/inkypi": b"#!/bin/bash\r\necho hi\r\n",
             "README.md": b"a\r\nb\r\n", "src/plugins/demo/cache/state.json": b"{}",
             "src/static/images/current_image.png": b"png", "fonts/msyh.ttc": b"font"}
    for name, data in files.items():
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_bytes(data)
    (package / "untracked.py").parent.mkdir(exist_ok=True)
    git(tmp_path, "init", "-q", str(repo))
    git(repo, "add", "--", release_build.PACKAGE)
    git(repo, "commit", "-q", "-m", "fixture")
    (package / "untracked.py").write_bytes(b"x = 1\n")
    commit = git(repo, "rev-parse", "HEAD")

    target = tmp_path / "out.zip"
    hashes, shebangs = release_build.package(repo, commit, target)

    assert sorted(hashes) == ["README.md", "install/inkypi", "install/lib/release_archive.py", "src/app.py"]
    with zipfile.ZipFile(target) as archive:
        assert archive.read("src/app.py") == b"print(1)\n"
        assert archive.read("install/inkypi") == b"#!/bin/bash\necho hi\n"
        assert archive.read("README.md") == b"a\r\nb\r\n"
        assert archive.getinfo("src/app.py").create_system == 3
    assert hashes["src/app.py"] == sha(b"print(1)\n") and shebangs == 1
    with pytest.raises(FileExistsError):
        release_build.package(repo, commit, target)


# --- acceptance checks --------------------------------------------------------------------------------------------


def config(**plugins):
    return {"plugin_cycle_interval_seconds": 300, "active_theme_info": {"sunset": "18:00"},
            "playlist_config": {"playlists": [{"name": "Default", "plugins": [
                {"plugin_id": plugin, "instance_uuid": "uuid-" + plugin, "name": plugin, "refresh": {"interval": interval},
                 "plugin_settings": {"city": "x"}} for plugin, interval in plugins.items()]}]}}


def test_health_classifies_failing_and_stalled_instances():
    now = datetime(2026, 10, 16, 20, 0, tzinfo=timezone.utc)
    runtime = {"instances": {
        "uuid-weather": {"lanes": {"data": {"last_success_at": "2026-10-16T19:00:00+00:00"}}},
        "uuid-apod": {"lanes": {"data": {"last_success_at": "2026-10-16T19:55:00+00:00",
                                         "last_failure_at": "2026-10-16T19:58:00Z",
                                         "last_error": "fetch https://api.test/x?key=1 failed"}}},
        "uuid-news": {"lanes": {"data": {"last_success_at": "2026-10-16T19:50:00+00:00"}}},
    }}

    failing, stalled = release_checks.classify_instances(config(weather=600, apod=3600, news=3600), runtime, now)

    assert failing == {"apod": "fetch [provider-url] failed"}
    assert stalled == {"weather": 1.0}


def health(label, readyz="ready", failing=(), stalled=()):
    return {"label": label, "readyz": readyz, "failing": {name: "x" for name in failing},
            "stalled_hours": {name: 1.0 for name in stalled}}


def test_health_gate_fails_on_new_failures_or_lost_readiness():
    before = health("predeploy", failing=["apod"], stalled=["weather"])

    passed = release_checks.compare_health(before, health("postdeploy", stalled=["news"]), True)
    assert passed["passed"] and passed["cleared_failing"] == ["apod"] and passed["new_stalled"] == ["news"]
    assert not release_checks.compare_health(before, health("postdeploy", failing=["news"]), True)["passed"]
    assert not release_checks.compare_health(before, health("postdeploy", readyz="degraded"), True)["passed"]
    assert release_checks.compare_health(before, health("postdeploy", readyz="degraded"), False)["passed"]


def test_health_gate_saves_its_verdict(ctx):
    ctx.save("health-predeploy.json", health("predeploy"))
    ctx.save("health-postdeploy.json", health("postdeploy", failing=["apod"]))

    verdict = release_checks.health_gate(ctx, "predeploy", "postdeploy", True)

    assert verdict["new_failing"] == ["apod"] and not verdict["passed"]
    assert ctx.load("health-gate-postdeploy.json") == verdict
    with pytest.raises(ReleaseError, match="Unsafe evidence label"):
        release_checks.health_gate(ctx, "../x", "postdeploy", True)


def test_configuration_identity_separates_runtime_theme_from_user_settings():
    before = release_checks.config_identity(config(weather=600))
    themed = config(weather=600)
    themed["active_theme_info"] = {"sunset": "18:05"}
    edited = config(weather=600)
    edited["playlist_config"]["playlists"][0]["plugins"][0]["plugin_settings"] = {"city": "y"}

    assert release_checks.configuration_note(before, release_checks.config_identity(config(weather=600))) is None
    assert release_checks.configuration_note(before, release_checks.config_identity(themed)) == (
        "Only runtime active_theme_info changed")
    with pytest.raises(ReleaseError, match="instance_settings_sha256"):
        release_checks.configuration_note(before, release_checks.config_identity(edited))


class ConfigDevice:
    def __init__(self, document):
        self.config = document

    def document(self, path):
        assert path == release_device.CONFIG
        return self.config


def deployed_with_preflight(ctx, before):
    ctx.save("deployment.json", {"status": "passed", "release_id": NEW_RELEASE, "commit": HEAD})
    ctx.save("preflight.json", {"commit": BASE, "configuration": release_checks.config_identity(before)})


def migrated(document, migration="room_v1"):
    changed = json.loads(json.dumps(document))
    changed["runtime_migrations"] = {migration: True}
    changed["playlist_config"]["playlists"][0]["plugins"][0]["plugin_settings"] = {"city": "x", "rooms": "kick|q"}
    return changed


def test_rebaseline_accepts_the_releases_own_migration_and_keeps_the_original_identity(ctx):
    before = config(weather=600)
    deployed_with_preflight(ctx, before)
    after = migrated(before)

    record = release_checks.rebaseline_configuration(ctx, ConfigDevice(after), "Kick room migration", "room_v1")

    identity = release_checks.config_identity(after)
    assert record["changed"] == ["stable_top_level_settings_sha256", "instance_settings_sha256"]
    assert record["before"] == release_checks.config_identity(before) and record["after"] == identity
    assert record["reason"] == "Kick room migration" and record["migration"] == "room_v1"
    assert ctx.load("preflight.json")["configuration"] == identity
    assert ctx.load("preflight.json")["configuration_rebaselines"] == [record]
    assert ctx.load("configuration-rebaseline.json") == record
    assert release_checks.configuration_note(ctx.load("preflight.json")["configuration"], identity) is None


def _copy(document):
    return json.loads(json.dumps(document))


def _second_instance(document):
    plugins = document["playlist_config"]["playlists"][0]["plugins"]
    plugins.append(dict(plugins[0], instance_uuid="uuid-second"))


@pytest.mark.parametrize("make, message", [
    (lambda before: _copy(before), "did not change"),
    (lambda before: migrated(before, "other_v1"), "room_v1 is not recorded"),
    (lambda before: {**migrated(before), "plugin_cycle_interval_seconds": 600}, "cycle_seconds"),
    (lambda before: (lambda after: (_second_instance(after), after)[1])(migrated(before)), "instance_count"),
])
def test_rebaseline_refuses_anything_but_a_recorded_settings_migration(ctx, make, message):
    before = config(weather=600)
    deployed_with_preflight(ctx, before)
    original = ctx.load("preflight.json")

    with pytest.raises(ReleaseError, match=message):
        release_checks.rebaseline_configuration(ctx, ConfigDevice(make(before)), "reason", "room_v1")

    assert ctx.load("preflight.json") == original
    assert not ctx.has("configuration-rebaseline.json")


def test_rebaseline_needs_a_reason_and_a_finished_deployment(ctx):
    before = config(weather=600)
    ctx.save("preflight.json", {"commit": BASE, "configuration": release_checks.config_identity(before)})

    with pytest.raises(ReleaseError, match="Missing task evidence: deployment.json"):
        release_checks.rebaseline_configuration(ctx, ConfigDevice(migrated(before)), "reason", "room_v1")
    deployed_with_preflight(ctx, before)
    with pytest.raises(ReleaseError, match="reason"):
        release_checks.rebaseline_configuration(ctx, ConfigDevice(migrated(before)), "  ", "room_v1")


def test_rebaseline_command_requires_reason_and_migration(capsys):
    parsed = parse("rebaseline-config", "--task-dir", "outputs/r", "--reason", "Kick room", "--migration", "m_v1")

    assert (parsed.reason, parsed.migration) == ("Kick room", "m_v1")
    with pytest.raises(SystemExit):
        parse("rebaseline-config", "--task-dir", "outputs/r", "--migration", "m_v1")
    with pytest.raises(SystemExit):
        parse("rebaseline-config", "--task-dir", "outputs/r", "--reason", "Kick room")
    capsys.readouterr()


def test_display_origin_finds_the_command_that_drew_the_commit():
    entries = [
        {"timestamp": "2026-10-16T20:00:00+00:00", "message": "Refresh command started. source: scheduler, "
                                                              "intent: display, plugin_id: weather"},
        {"timestamp": "2026-10-16T20:05:00+00:00", "message": "Refresh command started. source: scheduler, "
                                                              "intent: display, plugin_id: apod"},
        {"timestamp": "2026-10-16T20:09:00+00:00", "message": "Refresh command started. source: manual, "
                                                              "intent: display, plugin_id: news"},
    ]
    manifest = {"committed_at": "2026-10-16T20:06:00+00:00", "logical_target": {"plugin_id": "apod"}}

    assert release_checks.display_origin(manifest, entries) == {
        "source": "scheduler", "intent": "display", "plugin_id": "apod", "started_at": "2026-10-16T20:05:00+00:00"}
    manifest["logical_target"]["plugin_id"] = "weather"
    assert release_checks.display_origin(manifest, entries) is None


def test_journal_summary_counts_errors_by_plugin():
    selected = [{"priority": "6", "message": "12:00 - ERROR - plugins.apod.apod - boom"},
                {"priority": "3", "message": "Traceback plugin_id: weather"},
                {"priority": "6", "message": "Waveshare display write"}]

    summary = release_checks.summarize_journal("2026-10-16T20:00:00Z", selected, {"6": 2, "3": 1}, {})

    assert summary["app_errors_by_plugin"] == {"plugins.apod.apod": 1, "weather": 1}
    assert len(summary["error_lines"]) == 2 and summary["marker_counts"]["Waveshare display"] == 1


def test_probe_file_names_are_stable_per_instance():
    first = release_checks.render_file_name("before", "apod", "NASA 每日")

    assert first == release_checks.render_file_name("before", "apod", "NASA 每日")
    assert re.fullmatch(r"before-apod-[0-9a-f]{8}\.png", first)
    assert first != release_checks.render_file_name("before", "apod", "other")


# --- previous-backup rotation -------------------------------------------------------------------------------------


def prepare_rotation(ctx, old=True):
    previous = ctx.repo / ".backups" / "previous"
    previous.mkdir(parents=True)
    if old:
        old_zip = previous / "inkypi-2050df7d.zip"
        old_zip.write_bytes(b"old release")
        (previous / "manifest.json").write_text(json.dumps({"commit": OLD, "artifact": str(old_zip),
                                                            "sha256": sha(b"old release")}), encoding="utf-8")
    baseline = ctx.path("baseline-95b4c029.zip")
    baseline.write_bytes(b"base release")
    ctx.save("backup-preparation.json", {"status": "prepared_verified_not_rotated", "commit": BASE,
                                         "artifact": str(baseline), "artifact_sha256": sha(b"base release")})
    ctx.save("previous-manifest-draft.json", {"commit": BASE, "artifact": str(previous / "inkypi-95b4c029.zip"),
                                              "sha256": sha(b"base release"), "matches_device_previous": ACTIVE,
                                              "file_count": 3, "source_hash_set_sha256": "f" * 64})
    ctx.save("deployment.json", {"status": "passed", "release_id": NEW_RELEASE, "commit": HEAD,
                                 "completed_at": "2026-10-16T20:05:30.7102124Z"})
    ctx.save("current-manifest.json", {"status": "passed", "commit": HEAD, "base_commit": BASE})
    ctx.save("acceptance-apod-shown.json", acceptance())
    return previous


def acceptance():
    payload = {"status": "alive", "release_id": NEW_RELEASE}
    return {"active_release": "/opt/inkypi/releases/" + NEW_RELEASE, "previous_release": ACTIVE,
            "releases": [ACTIVE.rsplit("/", 1)[1], NEW_RELEASE], "configuration_preserved": True,
            "source": {"checked_files": 8, "mismatched": [], "removed_still_present": []},
            "service": {"ActiveState": "active", "SubState": "running"},
            "healthz": {"http_status": 200, "payload": payload},
            "readyz": {"http_status": 200, "payload": {**payload, "status": "ready"}},
            "display": {"hardware_written": True, "logical_target": {"plugin_id": "apod"}, "commit_id": "d1",
                        "committed_at": "2026-10-16T13:15:59.43-07:00"},
            "image": {"http_status": 200, "matches_manifest": True, "matches_runtime_commit": True},
            "runtime_display": {"state": "committed"}, "var_tmp_transport_zips": "0"}


def test_rotation_dry_run_changes_nothing(ctx):
    previous = prepare_rotation(ctx)

    result = release_backup.rotate(ctx, "apod", execute=False)

    assert result == {"status": "dry_run",
                      "plan": "would replace: inkypi-2050df7d.zip, manifest.json -> inkypi-95b4c029.zip, manifest.json"}
    assert sorted(path.name for path in previous.iterdir()) == ["inkypi-2050df7d.zip", "manifest.json"]
    assert ctx.has("baseline-95b4c029.zip") and not ctx.has("backup-rotation.json")


def test_rotation_keeps_exactly_one_verified_previous_backup(ctx):
    previous = prepare_rotation(ctx)

    record = release_backup.rotate(ctx, "apod", execute=True)

    assert sorted(path.name for path in previous.iterdir()) == ["inkypi-95b4c029.zip", "manifest.json"]
    manifest = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["commit"] == BASE and manifest["sha256"] == sha(b"base release")
    assert manifest["matches_device_previous"] == ACTIVE and manifest["retention"] == release_build.RETENTION
    assert (previous / "inkypi-95b4c029.zip").read_bytes() == b"base release"
    assert record["status"] == "rotated_verified" and record["display_commit"] == "d1"
    assert ctx.load("previous-backup-before.json")["commit"] == OLD
    assert not ctx.has("baseline-95b4c029.zip")
    with pytest.raises(ReleaseError):
        release_backup.rotate(ctx, "apod", execute=True)


def test_rotation_into_an_empty_backup_folder(ctx):
    previous = prepare_rotation(ctx, old=False)

    release_backup.rotate(ctx, "apod", execute=True)

    assert sorted(path.name for path in previous.iterdir()) == ["inkypi-95b4c029.zip", "manifest.json"]
    assert not ctx.has("previous-backup-before.json")


@pytest.mark.parametrize("change, message", [
    (lambda a: a["display"].update(committed_at="2026-10-16T13:00:00-07:00"), "predates deployment"),
    (lambda a: a["display"]["logical_target"].update(plugin_id="weather"), "physical display proof"),
    (lambda a: a.update(var_tmp_transport_zips="1"), "transport archive cleanup"),
    (lambda a: a["releases"].append("deploy-extra"), "exactly current and previous"),
    (lambda a: a.update(configuration_preserved=False), "configuration acceptance"),
    (lambda a: a["readyz"]["payload"].update(status="degraded"), "Healthy ready"),
    (lambda a: a.update(previous_release="/opt/inkypi/releases/deploy-other"), "release identity"),
])
def test_rotation_requires_matching_display_and_health_proof(ctx, change, message):
    previous = prepare_rotation(ctx)
    proof = acceptance()
    change(proof)
    ctx.save("acceptance-apod-shown.json", proof)

    with pytest.raises(ReleaseError, match=message):
        release_backup.rotate(ctx, "apod", execute=True)
    assert sorted(path.name for path in previous.iterdir()) == ["inkypi-2050df7d.zip", "manifest.json"]


def test_rotation_refuses_a_changed_backup_layout_or_hash(ctx):
    previous = prepare_rotation(ctx)
    (previous / "stray.zip").write_bytes(b"x")
    with pytest.raises(ReleaseError, match="layout changed"):
        release_backup.rotate(ctx, "apod", execute=True)

    (previous / "stray.zip").unlink()
    (previous / "inkypi-2050df7d.zip").write_bytes(b"tampered")
    with pytest.raises(ReleaseError, match="Existing backup hash mismatch"):
        release_backup.rotate(ctx, "apod", execute=True)
    assert ctx.has("baseline-95b4c029.zip")


def test_rotation_refuses_archives_outside_their_exact_paths(ctx):
    prepare_rotation(ctx)
    draft = ctx.load("previous-manifest-draft.json")
    ctx.save("previous-manifest-draft.json", {**draft, "artifact": str(ctx.repo / "inkypi-95b4c029.zip")})
    with pytest.raises(ReleaseError, match="Backup target escaped"):
        release_backup.rotate(ctx, "apod", execute=True)

    ctx.save("previous-manifest-draft.json", draft)
    prepared = ctx.load("backup-preparation.json")
    ctx.save("backup-preparation.json", {**prepared, "artifact": str(ctx.task_dir / "other.zip")})
    with pytest.raises(ReleaseError, match="Prepared archive escaped"):
        release_backup.rotate(ctx, "apod", execute=True)


def test_rotation_refuses_a_changed_prepared_archive(ctx):
    prepare_rotation(ctx)
    ctx.path("baseline-95b4c029.zip").write_bytes(b"changed")

    with pytest.raises(ReleaseError, match="Prepared archive hash changed"):
        release_backup.rotate(ctx, "apod", execute=False)
