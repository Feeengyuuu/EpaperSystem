from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import sys

import pytest
from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parents[4] / "tools"
SCRIPT_PATH = TOOLS_DIR / "live_shuffle_round_acceptance.py"


@pytest.fixture(scope="module")
def acceptance():
    assert SCRIPT_PATH.is_file(), "live shuffle-round acceptance script is missing"
    spec = spec_from_file_location("live_shuffle_round_acceptance", SCRIPT_PATH)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _config(count=26):
    plugins = []
    for index in range(count):
        plugins.append({
            "plugin_id": f"plugin_{index:02d}",
            "name": f"Private instance {index}",
            "plugin_settings": {
                "api_key": f"private-{index}",
                "chat_id": f"private-chat-{index}",
                "refreshOnDisplay": False,
            },
            "instance_uuid": f"{index + 1:032x}",
            "structural_generation": 2,
            "settings_revision": 3,
        })
    return {
        "timezone": "UTC",
        "plugin_cycle_interval_seconds": 300,
        "unrelated_secret": "must-survive",
        "refresh_info": {
            "refresh_type": "Playlist",
            "playlist": "Factory",
            "plugin_id": "plugin_25",
            "plugin_instance": "Private instance 25",
            "refresh_time": "2026-07-13T11:55:00+00:00",
            "image_hash": "old-image-hash",
        },
        "playlist_config": {
            "playlists": [{
                "name": "Factory",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": plugins,
                "current_plugin_index": 25,
                "plugin_rotation_pool": [plugins[-1]["instance_uuid"]],
                "plugin_rotation_queue": [plugins[-1]["instance_uuid"]],
                "plugin_rotation_recent_history": [plugins[-1]["instance_uuid"]],
            }],
        },
    }


def _manifest(config, *, index=25, commit_id="baseline-commit"):
    plugin = config["playlist_config"]["playlists"][0]["plugins"][index]
    return {
        "commit_id": commit_id,
        "logical_target": {
            "kind": "playlist",
            "playlist": "Factory",
            "plugin_id": plugin["plugin_id"],
            "plugin_instance": plugin["name"],
            "instance_uuid": plugin["instance_uuid"],
        },
    }


def _reverse(values):
    values.reverse()


def _png_bytes(color=(10, 20, 30)):
    buffer = BytesIO()
    Image.new("RGB", (800, 480), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_prepare_rotation_config_builds_full_uuid_bag_and_future_gate(acceptance):
    config = _config()
    original = json.loads(json.dumps(config))
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)

    prepared = acceptance.prepare_rotation_config(
        config,
        current_manifest=_manifest(config),
        now=now,
        test_interval_seconds=1,
        startup_window_seconds=30,
        shuffle=_reverse,
    )

    playlist = prepared.document["playlist_config"]["playlists"][0]
    expected_uuids = {
        item["instance_uuid"] for item in playlist["plugins"]
    }
    assert len(prepared.plan) == 26
    assert len(playlist["plugin_rotation_queue"]) == 26
    assert len(set(playlist["plugin_rotation_queue"])) == 26
    assert set(playlist["plugin_rotation_queue"]) == expected_uuids
    assert playlist["plugin_rotation_pool"] == [
        item["instance_uuid"] for item in playlist["plugins"]
    ]
    assert playlist["plugin_rotation_queue"][0] != prepared.current_uuid
    assert playlist["plugin_rotation_recent_history"] == [prepared.current_uuid]
    assert prepared.document["plugin_cycle_interval_seconds"] == 1
    assert prepared.marker_refresh_time == (
        now + timedelta(seconds=30)
    ).isoformat()
    assert prepared.document["refresh_info"]["refresh_time"] == (
        now + timedelta(seconds=30)
    ).isoformat()
    assert prepared.document["unrelated_secret"] == "must-survive"
    assert (
        prepared.document["playlist_config"]["playlists"][0]["plugins"]
        == original["playlist_config"]["playlists"][0]["plugins"]
    )
    assert config == original, "preparation must not mutate the caller's document"


def test_prepare_rotation_config_requires_exactly_26_unique_instances(acceptance):
    with pytest.raises(acceptance.AuditAbort) as captured:
        acceptance.prepare_rotation_config(
            _config(25),
            current_manifest=None,
            now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
            test_interval_seconds=1,
            startup_window_seconds=30,
            shuffle=_reverse,
        )

    assert captured.value.code == "config_instance_count"


def test_tracker_counts_only_one_member_removal_and_ignores_followup(acceptance):
    configured = tuple(f"{index + 1:032x}" for index in range(26))
    tracker = acceptance.ShuffleRoundTracker(
        configured_uuids=configured,
        initial_queue=configured,
        initial_pool=configured,
        baseline_commit_id="baseline",
        current_uuid=configured[-1],
    )

    first = tracker.observe(
        queue=configured[1:],
        pool=configured,
        manifest_uuid=configured[0],
        commit_id="commit-1",
    )
    followup = tracker.observe(
        queue=configured[1:],
        pool=configured,
        manifest_uuid=configured[0],
        commit_id="followup-1",
    )

    assert first is not None
    assert first.round_number == 1
    assert first.round_index == 1
    assert first.slot == 1
    assert first.instance_uuid == configured[0]
    assert first.queue_remaining == 25
    assert followup is None
    assert tracker.first_round_count == 1
    assert tracker.ignored_same_uuid_followups == 1


def test_tracker_proves_full_first_round_and_next_round_boundary(acceptance):
    configured = tuple(f"{index + 1:032x}" for index in range(26))
    tracker = acceptance.ShuffleRoundTracker(
        configured_uuids=configured,
        initial_queue=configured,
        initial_pool=configured,
        baseline_commit_id="baseline",
        current_uuid=None,
    )
    remaining = list(configured)
    events = []
    for index, instance_uuid in enumerate(configured, start=1):
        remaining.remove(instance_uuid)
        event = tracker.observe(
            queue=tuple(remaining),
            pool=configured,
            manifest_uuid=instance_uuid,
            commit_id=f"round-1-{index}",
        )
        events.append(event)

    next_uuid = configured[0]
    next_queue = tuple(item for item in reversed(configured) if item != next_uuid)
    boundary = tracker.observe(
        queue=next_queue,
        pool=configured,
        manifest_uuid=next_uuid,
        commit_id="round-2-1",
    )

    assert all(event is not None for event in events)
    assert len({event.instance_uuid for event in events}) == 26
    assert events[-1].instance_uuid == configured[-1]
    assert events[-1].queue_remaining == 0
    assert boundary is not None
    assert boundary.round_number == 2
    assert boundary.round_index == 1
    assert boundary.slot == 27
    assert boundary.instance_uuid != events[-1].instance_uuid
    assert tracker.complete is True


@pytest.mark.parametrize(
    ("queue", "manifest_uuid", "expected_code"),
    [
        (lambda values: values[2:], lambda values: values[0], "rotation_queue_not_single_ack"),
        (lambda values: values[1:], lambda values: values[1], "ack_manifest_target_mismatch"),
    ],
)
def test_tracker_rejects_non_atomic_or_mismatched_ack(
    acceptance,
    queue,
    manifest_uuid,
    expected_code,
):
    configured = tuple(f"{index + 1:032x}" for index in range(26))
    tracker = acceptance.ShuffleRoundTracker(
        configured_uuids=configured,
        initial_queue=configured,
        initial_pool=configured,
        baseline_commit_id="baseline",
        current_uuid=None,
    )

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        tracker.observe(
            queue=queue(configured),
            pool=configured,
            manifest_uuid=manifest_uuid(configured),
            commit_id="commit-1",
        )

    assert captured.value.code == expected_code


def test_tracker_rejects_cross_round_boundary_repeat(acceptance):
    configured = tuple(f"{index + 1:032x}" for index in range(26))
    tracker = acceptance.ShuffleRoundTracker(
        configured_uuids=configured,
        initial_queue=configured,
        initial_pool=configured,
        baseline_commit_id="baseline",
        current_uuid=None,
    )
    remaining = list(configured)
    for index, instance_uuid in enumerate(configured, start=1):
        remaining.remove(instance_uuid)
        tracker.observe(
            queue=remaining,
            pool=configured,
            manifest_uuid=instance_uuid,
            commit_id=f"round-1-{index}",
        )

    repeated = configured[-1]
    with pytest.raises(acceptance.EvidenceFailure) as captured:
        tracker.observe(
            queue=[item for item in configured if item != repeated],
            pool=configured,
            manifest_uuid=repeated,
            commit_id="round-2-1",
        )

    assert captured.value.code == "rotation_round_boundary_repeat"


def test_restore_runtime_config_only_reverts_test_controls(acceptance):
    original = _config()
    prepared = acceptance.prepare_rotation_config(
        original,
        current_manifest=_manifest(original),
        now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        test_interval_seconds=1,
        startup_window_seconds=30,
        shuffle=_reverse,
    )

    no_ack = json.loads(json.dumps(prepared.document))
    no_ack["runtime_written_field"] = {"keep": True}
    restored_no_ack = acceptance.restore_runtime_config(no_ack, prepared)
    assert restored_no_ack["plugin_cycle_interval_seconds"] == 300
    assert restored_no_ack["refresh_info"] == original["refresh_info"]
    assert restored_no_ack["runtime_written_field"] == {"keep": True}

    after_ack = json.loads(json.dumps(prepared.document))
    after_ack["refresh_info"] = {
        **after_ack["refresh_info"],
        "refresh_time": "2026-07-13T12:31:00+00:00",
        "image_hash": "real-automatic-commit",
    }
    restored_after_ack = acceptance.restore_runtime_config(after_ack, prepared)
    assert restored_after_ack["plugin_cycle_interval_seconds"] == 300
    assert restored_after_ack["refresh_info"]["image_hash"] == "real-automatic-commit"


def test_restore_runtime_config_removes_interval_when_originally_absent(acceptance):
    original = _config()
    original.pop("plugin_cycle_interval_seconds")
    prepared = acceptance.prepare_rotation_config(
        original,
        current_manifest=_manifest(original),
        now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        test_interval_seconds=1,
        startup_window_seconds=30,
        shuffle=_reverse,
    )

    restored = acceptance.restore_runtime_config(prepared.document, prepared)

    assert "plugin_cycle_interval_seconds" not in restored


def test_restore_runtime_config_removes_artificial_refresh_info_when_absent(
    acceptance,
):
    original = _config()
    original.pop("refresh_info")
    prepared = acceptance.prepare_rotation_config(
        original,
        current_manifest=_manifest(_config()),
        now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        test_interval_seconds=1,
        startup_window_seconds=30,
        shuffle=_reverse,
    )

    restored = acceptance.restore_runtime_config(prepared.document, prepared)

    assert "refresh_info" not in restored


def test_safe_filename_token_blocks_path_components(acceptance):
    assert acceptance.safe_filename_token("../private/plugin") == "private_plugin"


def test_atomic_write_json_preserves_mode_and_writes_complete_document(
    acceptance,
    tmp_path,
):
    path = tmp_path / "device.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    os.chmod(path, 0o640)
    original_mode = stat.S_IMODE(path.stat().st_mode)

    acceptance.atomic_write_json(path, {"new": {"complete": True}})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "new": {"complete": True},
    }
    assert stat.S_IMODE(path.stat().st_mode) == original_mode
    assert not list(tmp_path.glob(".*.tmp"))


class _FakeCompletedProcess:
    returncode = 0
    stdout = ""
    stderr = ""


def test_systemd_controller_uses_argument_vector_without_shell(acceptance):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return _FakeCompletedProcess()

    controller = acceptance.SystemdController(
        service_name="inkypi.service",
        run=run,
    )

    controller.stop()
    controller.start()

    assert calls[0][0] == ["systemctl", "stop", "inkypi.service"]
    assert calls[1][0] == ["systemctl", "start", "inkypi.service"]
    assert all(call[1].get("shell") is False for call in calls)


class _FakeServiceController:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")


class _JsonResponse:
    def __init__(self, payload, *, status_code=200, content=b"", headers=None):
        self._payload = payload
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._payload


class _ReadySession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _JsonResponse({
            "status": "ready",
            "release_id": "release-test",
            "boot_id": f"private-boot-{len(self.calls)}",
        })


def test_runner_finally_restores_service_interval_and_ready_on_failure(
    acceptance,
    tmp_path,
):
    config_path = tmp_path / "device.json"
    manifest_path = tmp_path / "display_manifest.json"
    runtime_path = tmp_path / "runtime_state.json"
    output_dir = tmp_path / "evidence"
    original = _config()
    config_path.write_text(json.dumps(original), encoding="utf-8")
    manifest_path.write_text(json.dumps(_manifest(original)), encoding="utf-8")
    runtime_path.write_text("{}", encoding="utf-8")
    controller = _FakeServiceController()
    session = _ReadySession()
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    runner = acceptance.ShuffleRoundAcceptance(
        session=session,
        controller=controller,
        base_url="http://127.0.0.1",
        config_path=config_path,
        runtime_state_path=runtime_path,
        display_manifest_path=manifest_path,
        output_dir=output_dir,
        test_interval_seconds=1,
        startup_window_seconds=30,
        round_timeout_seconds=60,
        utcnow=lambda: now,
        sleep=lambda _seconds: None,
        shuffle=_reverse,
    )

    def fail_monitor(_prepared, _baseline_manifest, _started_at):
        raise acceptance.EvidenceFailure("forced_monitor_failure")

    runner._monitor_round = fail_monitor
    summary = runner.run()

    restored = json.loads(config_path.read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert summary["error_code"] == "forced_monitor_failure"
    assert summary["service_ready_restored"] is True
    assert summary["restored_cycle_interval_seconds"] == 300
    assert controller.calls == ["stop", "start", "stop", "start"]
    assert restored["plugin_cycle_interval_seconds"] == 300
    assert restored["refresh_info"] == original["refresh_info"]
    assert len(
        restored["playlist_config"]["playlists"][0]["plugin_rotation_queue"]
    ) == 26
    persisted_summary = json.loads(
        (output_dir / "summary.json").read_text(encoding="utf-8")
    )
    rendered = json.dumps(persisted_summary)
    assert "Private instance" not in rendered
    assert "private-" not in rendered
    assert persisted_summary == summary


def test_runner_keeps_service_stopped_when_restore_write_fails(
    acceptance,
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "device.json"
    manifest_path = tmp_path / "display_manifest.json"
    runtime_path = tmp_path / "runtime_state.json"
    original = _config()
    config_path.write_text(json.dumps(original), encoding="utf-8")
    manifest_path.write_text(json.dumps(_manifest(original)), encoding="utf-8")
    runtime_path.write_text("{}", encoding="utf-8")
    controller = _FakeServiceController()
    session = _ReadySession()
    real_atomic_write = acceptance.atomic_write_json
    writes = 0

    def fail_second_write(path, document):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise acceptance.AuditAbort("injected_restore_write_failure")
        return real_atomic_write(path, document)

    monkeypatch.setattr(acceptance, "atomic_write_json", fail_second_write)
    runner = acceptance.ShuffleRoundAcceptance(
        session=session,
        controller=controller,
        base_url="http://127.0.0.1",
        config_path=config_path,
        runtime_state_path=runtime_path,
        display_manifest_path=manifest_path,
        output_dir=tmp_path / "evidence",
        test_interval_seconds=1,
        startup_window_seconds=30,
        round_timeout_seconds=60,
        utcnow=lambda: datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
        shuffle=_reverse,
    )
    runner._monitor_round = lambda *_args: (_ for _ in ()).throw(
        acceptance.EvidenceFailure("forced_monitor_failure")
    )

    summary = runner.run()

    current = json.loads(config_path.read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert summary["restore_error_code"] == "restore_config_failed"
    assert summary["service_ready_restored"] is False
    assert summary["service_left_stopped"] is True
    assert controller.calls == ["stop", "start", "stop"]
    assert current["plugin_cycle_interval_seconds"] == 1


class _CurrentImageSession:
    def __init__(self, state):
        self.state = state

    def get(self, url, **_kwargs):
        assert url.endswith("/api/current_image")
        return _JsonResponse(
            {},
            content=self.state["image"],
            headers=self.state["headers"],
        )


def test_monitor_correlates_manifest_before_ack_when_followup_overwrites_it(
    acceptance,
    tmp_path,
):
    now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    original = _config()
    baseline = _manifest(original, commit_id="f" * 32)
    prepared = acceptance.prepare_rotation_config(
        original,
        current_manifest=baseline,
        now=now,
        test_interval_seconds=1,
        startup_window_seconds=30,
        shuffle=_reverse,
    )
    config_path = tmp_path / "device.json"
    manifest_path = tmp_path / "display_manifest.json"
    runtime_path = tmp_path / "runtime_state.json"
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    config = json.loads(json.dumps(prepared.document))
    config_path.write_text(json.dumps(config), encoding="utf-8")
    manifest_path.write_text(json.dumps(baseline), encoding="utf-8")
    runtime_path.write_text("{}", encoding="utf-8")
    plugins = {
        item["instance_uuid"]: item
        for item in config["playlist_config"]["playlists"][0]["plugins"]
    }
    state = {
        "image": _png_bytes(),
        "headers": {},
        "stage": 0,
        "next_slot": 1,
        "last_uuid": None,
    }

    def publish(instance_uuid, slot, *, hardware=True, followup=False):
        plugin = plugins[instance_uuid]
        commit_id = ("b" if followup else "a") + f"{slot:031x}"
        committed = now + timedelta(seconds=30 + slot)
        image = _png_bytes((slot % 255, (slot * 3) % 255, (slot * 7) % 255))
        pixel_hash = acceptance.inspect_png(image).pixel_hash
        manifest = {
            "schema_version": 1,
            "commit_id": commit_id,
            "image": f"objects/{commit_id}.png",
            "pixel_hash": pixel_hash,
            "hardware_fingerprint": "safe-test-hardware",
            "logical_target": {
                "kind": "playlist",
                "playlist": "Factory",
                "plugin_id": plugin["plugin_id"],
                "plugin_instance": plugin["name"],
                "instance_uuid": instance_uuid,
            },
            "instance_revision": [
                plugin["structural_generation"],
                plugin["settings_revision"],
            ],
            "image_settings": [],
            "hardware_written": hardware,
            "committed_at": committed.isoformat(),
        }
        runtime = {
            "display": {
                "state": "committed",
                "commit_id": commit_id,
                "instance_uuid": instance_uuid,
            },
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        state["image"] = image
        state["headers"] = {
            "ETag": f'"{commit_id}"',
            "Last-Modified": format_datetime(committed),
            "Content-Type": "image/png",
            "Content-Length": str(len(image)),
        }

    def remove_from_first_round(instance_uuid):
        playlist = config["playlist_config"]["playlists"][0]
        playlist["plugin_rotation_queue"].remove(instance_uuid)
        config_path.write_text(json.dumps(config), encoding="utf-8")

    def advance(_seconds):
        # The first real automatic commit is visible before its config ack.  A
        # same-target refreshOnDisplay follow-up then replaces the manifest at
        # the same moment the config queue records the ack.
        if state["stage"] == 0:
            first_uuid = prepared.initial_queue[0]
            publish(first_uuid, 1, hardware=True)
            state["last_uuid"] = first_uuid
            state["stage"] = 1
            return
        if state["stage"] == 1:
            remove_from_first_round(state["last_uuid"])
            publish(state["last_uuid"], 1, hardware=False, followup=True)
            state["next_slot"] = 2
            state["stage"] = 2
            return

        slot = state["next_slot"]
        if slot <= 26:
            instance_uuid = prepared.initial_queue[slot - 1]
            remove_from_first_round(instance_uuid)
            publish(instance_uuid, slot, hardware=True)
            state["last_uuid"] = instance_uuid
            state["next_slot"] += 1
            return
        if slot == 27:
            next_uuid = next(
                value
                for value in prepared.configured_uuids
                if value != state["last_uuid"]
            )
            playlist = config["playlist_config"]["playlists"][0]
            playlist["plugin_rotation_queue"] = [
                value for value in reversed(prepared.configured_uuids)
                if value != next_uuid
            ]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            publish(next_uuid, 27, hardware=True)
            state["next_slot"] += 1

    runner = acceptance.ShuffleRoundAcceptance(
        session=_CurrentImageSession(state),
        controller=_FakeServiceController(),
        base_url="http://127.0.0.1",
        config_path=config_path,
        runtime_state_path=runtime_path,
        display_manifest_path=manifest_path,
        output_dir=output_dir,
        test_interval_seconds=1,
        startup_window_seconds=30,
        round_timeout_seconds=60,
        utcnow=lambda: now,
        sleep=advance,
    )

    result = runner._monitor_round(prepared, baseline, now)

    assert result["accepted_slots"] == 27
    assert result["first_round_unique"] == 26
    assert result["boundary_no_repeat"] is True
    assert len(result["events"]) == 27
    assert all(event["hardware_written"] is True for event in result["events"])
    assert len(list(output_dir.glob("slot-*.png"))) == 27
    rendered = json.dumps(result)
    assert "Private instance" not in rendered
    assert "plugin_settings" not in rendered
    assert "api_key" not in rendered
    assert all(value not in rendered for value in prepared.configured_uuids)
