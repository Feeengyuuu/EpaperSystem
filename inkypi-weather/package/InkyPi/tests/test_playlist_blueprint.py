from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from flask import Flask
from PIL import Image
import pytest

import blueprints.playlist as playlist_blueprint
import blueprints.plugin as plugin_blueprint
import utils.app_utils as app_utils
from model import PlaylistManager


INVALID_REFRESH_SETTINGS = [
    "{not-json",
    json.dumps([]),
    json.dumps({}),
    json.dumps({"refreshType": "unknown"}),
    json.dumps({"refreshType": "interval", "unit": "week", "interval": 1}),
    json.dumps({"refreshType": "interval", "unit": "minute", "interval": 0}),
    json.dumps({"refreshType": "interval", "unit": "minute", "interval": -1}),
    json.dumps({"refreshType": "interval", "unit": "minute", "interval": "abc"}),
    json.dumps({"refreshType": "scheduled", "refreshTime": "24:00"}),
    json.dumps({"refreshType": "scheduled", "refreshTime": "not-a-time"}),
]


def _png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _plugin(plugin_id, name, instance_uuid):
    return {
        "plugin_id": plugin_id,
        "name": name,
        "plugin_settings": {"source": name},
        "refresh": {"interval": 300},
        "instance_uuid": instance_uuid,
    }


def _playlist_manager():
    return PlaylistManager.from_dict({
        "playlists": [
            {
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [
                    _plugin("weather", "Home", "home-uuid"),
                    _plugin("clock", "Office", "office-uuid"),
                ],
            },
            {
                "name": "Other",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [],
            },
        ],
    })


class RecordingManager:
    def __init__(self, manager, events):
        self.manager = manager
        self.events = events
        self.add_before_delete = None
        self.add_mutated = threading.Event()

    @contextmanager
    def instance_lifecycle_guard(self):
        with self.manager.instance_lifecycle_guard():
            yield

    def add_plugin_to_playlist_snapshot(self, playlist_name, plugin_data):
        self.events.append(("mutation", "add_plugin", playlist_name))
        result = self.manager.add_plugin_to_playlist_snapshot(playlist_name, plugin_data)
        if result is not None:
            self.add_mutated.set()
        return result

    def delete_playlist_atomic(self, playlist_name):
        if self.add_before_delete is not None:
            added = self.manager.add_plugin_to_playlist_snapshot(
                playlist_name,
                self.add_before_delete,
            )
            assert added is not None
            self.add_before_delete = None
        self.events.append(("mutation", "delete_playlist", playlist_name))
        return self.manager.delete_playlist_atomic(playlist_name)

    def add_playlist(self, name, start_time=None, end_time=None):
        self.events.append(("mutation", "create_playlist", name))
        return self.manager.add_playlist(name, start_time, end_time)

    def update_playlist(self, old_name, new_name, start_time, end_time):
        self.events.append(("mutation", "update_playlist", old_name))
        return self.manager.update_playlist(old_name, new_name, start_time, end_time)

    def resolve_plugin_instance_snapshot(self, playlist_name, plugin_id, instance_name):
        return self.manager.resolve_plugin_instance_snapshot(
            playlist_name,
            plugin_id,
            instance_name,
        )

    def get_playlist_names(self):
        return self.manager.get_playlist_names()


class RecordingQueue:
    def __init__(self, events):
        self.events = events
        self.canceled = []

    def cancel_instance(self, instance_uuid):
        self.events.append(("cancel", instance_uuid))
        self.canceled.append(instance_uuid)
        return 1


class RecordingRetryRegistry:
    def __init__(self, events):
        self.events = events
        self.discarded = []

    def discard(self, instance_uuid):
        self.events.append(("retry_discard", instance_uuid))
        self.discarded.append(instance_uuid)


class RecordingArbiter:
    def __init__(self, events):
        self.events = events
        self.inside = False

    @contextmanager
    def lease(self, plugin_id, _context):
        self.events.append(("lease_enter", plugin_id))
        self.inside = True
        try:
            yield
        finally:
            self.inside = False
            self.events.append(("lease_exit", plugin_id))


class RecordingRefreshTask:
    def __init__(self, events, plugin_image_dir):
        self.events = events
        self.refresh_queue = RecordingQueue(events)
        self.retry_registry = RecordingRetryRegistry(events)
        self.render_arbiter = RecordingArbiter(events)
        self.plugin_image_dir = Path(plugin_image_dir)

    def signal_config_change(self):
        self.events.append(("signal",))

    def make_cleanup_context(self):
        self.events.append(("cleanup_context",))
        return object()

    def managed_cache_paths(self, instance_uuid, **kwargs):
        self.events.append(("managed_paths", instance_uuid, kwargs))
        return (str(self.plugin_image_dir / f"{instance_uuid}-staged.png"),)


class RecordingDeviceConfig:
    def __init__(self, manager, events, plugin_image_dir):
        self.manager = manager
        self.events = events
        self.plugin_image_dir = str(plugin_image_dir)
        self.fail_write = False

    def get_playlist_manager(self):
        return self.manager

    def write_config(self):
        self.events.append(("write",))
        if self.fail_write:
            raise RuntimeError("config write failed")

    def get_plugin(self, plugin_id):
        self.events.append(("get_plugin", plugin_id))
        return {"id": plugin_id}


@pytest.fixture
def playlist_env(tmp_path):
    events = []
    inner_manager = _playlist_manager()
    manager = RecordingManager(inner_manager, events)
    task = RecordingRefreshTask(events, tmp_path)
    device_config = RecordingDeviceConfig(manager, events, tmp_path)
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        DEVICE_CONFIG=device_config,
        REFRESH_TASK=task,
    )
    app.register_blueprint(playlist_blueprint.playlist_bp)
    return SimpleNamespace(
        app=app,
        client=app.test_client(),
        events=events,
        manager=manager,
        inner_manager=inner_manager,
        task=task,
        device_config=device_config,
        tmp_path=tmp_path,
    )


@pytest.mark.parametrize("refresh_settings", INVALID_REFRESH_SETTINGS)
def test_add_plugin_rejects_invalid_refresh_before_any_effect(
    playlist_env,
    refresh_settings,
):
    before = playlist_env.inner_manager.to_dict()

    response = playlist_env.client.post(
        "/add_plugin",
        data={
            "plugin_id": "news",
            "refresh_settings": refresh_settings,
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload == {
        "success": False,
        "error_code": "invalid_refresh_config",
        "error": payload["error"],
        "message": payload["error"],
    }
    assert playlist_env.inner_manager.to_dict() == before
    assert playlist_env.events == []


def test_add_plugin_uses_atomic_snapshot_then_write_and_signal(playlist_env):
    response = playlist_env.client.post(
        "/add_plugin",
        data={
            "plugin_id": "news",
            "region": "us",
            "refresh_settings": json.dumps({
                "playlist": "Other",
                "instance_name": "Headlines",
                "refreshType": "interval",
                "unit": "minute",
                "interval": "15",
            }),
        },
    )

    assert response.status_code == 200
    added = playlist_env.inner_manager.resolve_plugin_instance_snapshot(
        "Other",
        "news",
        "Headlines",
    ).instance
    assert added.settings == {"region": "us"}
    assert added.refresh == {"interval": 900}
    assert [event[0] for event in playlist_env.events] == [
        "mutation",
        "write",
        "signal",
    ]


def test_atomic_add_preserves_global_plugin_name_uniqueness(playlist_env):
    response = playlist_env.client.post(
        "/add_plugin",
        data={
            "plugin_id": "weather",
            "refresh_settings": json.dumps({
                "playlist": "Other",
                "instance_name": "Home",
                "refreshType": "interval",
                "unit": "minute",
                "interval": "5",
            }),
        },
    )

    assert response.status_code == 400
    assert playlist_env.inner_manager.resolve_plugin_instance_snapshot(
        "Other",
        "weather",
        "Home",
    ) is None
    assert not any(event[0] in {"write", "signal"} for event in playlist_env.events)


def test_add_mutation_miss_has_no_write_or_signal(playlist_env):
    response = playlist_env.client.post(
        "/add_plugin",
        data={
            "plugin_id": "news",
            "refresh_settings": json.dumps({
                "playlist": "Missing",
                "instance_name": "Headlines",
                "refreshType": "interval",
                "unit": "minute",
                "interval": "5",
            }),
        },
    )

    assert response.status_code == 400
    assert not any(event[0] in {"write", "signal"} for event in playlist_env.events)


def _create_playlist_cleanup_files(playlist_env, snapshots):
    paths = []
    for snapshot in snapshots:
        canonical = playlist_env.tmp_path / (
            f"{snapshot.plugin_id}_{snapshot.name.replace(' ', '_')}.png"
        )
        staged = playlist_env.tmp_path / f"{snapshot.instance_uuid}-staged.png"
        canonical.write_bytes(snapshot.instance_uuid.encode())
        staged.write_bytes(snapshot.instance_uuid.encode())
        paths.extend([canonical, staged])
    return paths


def test_playlist_delete_cancels_and_cleans_every_atomic_snapshot(
    playlist_env,
    monkeypatch,
):
    playlist_env.manager.add_before_delete = _plugin(
        "news",
        "Late Add",
        "caller-supplied-uuid",
    )
    original_delete = playlist_env.manager.delete_playlist_atomic
    captured = []
    cleanup_paths = []

    def capture_delete(name):
        result = original_delete(name)
        captured.extend(result.removed_instances)
        cleanup_paths.extend(
            _create_playlist_cleanup_files(playlist_env, result.removed_instances)
        )
        return result

    monkeypatch.setattr(
        playlist_env.manager,
        "delete_playlist_atomic",
        capture_delete,
    )
    cleaned = []

    class Plugin:
        def __init__(self, plugin_id):
            self.plugin_id = plugin_id

        def cleanup(self, settings):
            assert playlist_env.task.render_arbiter.inside
            cleaned.append((self.plugin_id, dict(settings)))

    monkeypatch.setattr(
        plugin_blueprint,
        "get_plugin_instance",
        lambda config: Plugin(config["id"]),
    )

    response = playlist_env.client.delete("/delete_playlist/Default")

    assert response.status_code == 200
    removed_uuids = {snapshot.instance_uuid for snapshot in captured}
    assert len(removed_uuids) == 3
    assert set(playlist_env.task.refresh_queue.canceled) == removed_uuids
    assert set(playlist_env.task.retry_registry.discarded) == removed_uuids
    assert {plugin_id for plugin_id, _settings in cleaned} == {
        "weather",
        "clock",
        "news",
    }
    assert playlist_env.inner_manager.get_playlist_names() == ["Other"]
    names = [event[0] for event in playlist_env.events]
    cancel_indexes = [index for index, name in enumerate(names) if name == "cancel"]
    retry_indexes = [index for index, name in enumerate(names) if name == "retry_discard"]
    lease_indexes = [index for index, name in enumerate(names) if name == "lease_enter"]
    assert max(cancel_indexes) < min(retry_indexes)
    assert max(retry_indexes) < names.index("write")
    assert names.index("write") < min(lease_indexes)
    assert max(index for index, name in enumerate(names) if name == "lease_exit") < names.index("signal")
    assert all(not path.exists() for path in cleanup_paths)


def test_playlist_delete_write_failure_never_starts_cleanup(
    playlist_env,
    monkeypatch,
):
    snapshots = tuple(
        playlist_env.inner_manager.resolve_plugin_instance_snapshot(
            "Default",
            plugin_id,
            name,
        ).instance
        for plugin_id, name in [("weather", "Home"), ("clock", "Office")]
    )
    paths = _create_playlist_cleanup_files(playlist_env, snapshots)
    playlist_env.device_config.fail_write = True
    cleanup_called = False

    class Plugin:
        def cleanup(self, _settings):
            nonlocal cleanup_called
            cleanup_called = True

    monkeypatch.setattr(plugin_blueprint, "get_plugin_instance", lambda _config: Plugin())

    response = playlist_env.client.delete("/delete_playlist/Default")

    assert response.status_code == 500
    assert set(playlist_env.task.refresh_queue.canceled) == {
        "home-uuid",
        "office-uuid",
    }
    assert set(playlist_env.task.retry_registry.discarded) == {
        "home-uuid",
        "office-uuid",
    }
    assert all(path.exists() for path in paths)
    assert cleanup_called is False
    assert not any(event[0] in {"lease_enter", "signal"} for event in playlist_env.events)


def test_missing_playlist_delete_has_no_effects(playlist_env):
    response = playlist_env.client.delete("/delete_playlist/Missing")

    assert response.status_code == 400
    assert not any(
        event[0] in {
            "cancel",
            "retry_discard",
            "write",
            "lease_enter",
            "signal",
        }
        for event in playlist_env.events
    )


def test_create_and_update_playlist_use_atomic_result_without_live_precheck(
    playlist_env,
):
    created = playlist_env.client.post(
        "/create_playlist",
        json={
            "playlist_name": "Evening",
            "start_time": "18:00",
            "end_time": "23:00",
        },
    )
    updated = playlist_env.client.put(
        "/update_playlist/Evening",
        json={
            "new_name": "Night",
            "start_time": "19:00",
            "end_time": "23:30",
        },
    )

    assert created.status_code == 200
    assert updated.status_code == 200
    assert "Night" in playlist_env.inner_manager.get_playlist_names()
    names = [event[0] for event in playlist_env.events]
    assert names == [
        "mutation",
        "write",
        "signal",
        "mutation",
        "write",
        "signal",
    ]


def test_failed_create_and_update_mutations_have_no_write_or_signal(playlist_env):
    duplicate = playlist_env.client.post(
        "/create_playlist",
        json={
            "playlist_name": "Default",
            "start_time": "00:00",
            "end_time": "24:00",
        },
    )
    missing = playlist_env.client.put(
        "/update_playlist/Missing",
        json={
            "new_name": "Still Missing",
            "start_time": "00:00",
            "end_time": "24:00",
        },
    )

    assert duplicate.status_code == 400
    assert missing.status_code == 400
    assert not any(event[0] in {"write", "signal"} for event in playlist_env.events)


def test_playlist_mutations_do_not_traverse_live_plugin_lists():
    source = Path(playlist_blueprint.__file__).read_text(encoding="utf-8")

    assert "playlist.plugins" not in source
    assert "delete_playlist_atomic" in source
    assert "add_plugin_to_playlist_snapshot" in source


def test_duplicate_add_upload_never_overwrites_existing_file(
    playlist_env,
    monkeypatch,
):
    saved = playlist_env.tmp_path / "saved"
    saved.mkdir()
    victim = saved / "victim.png"
    victim.write_bytes(b"old-content")
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))

    response = playlist_env.client.post(
        "/add_plugin",
        data={
            "plugin_id": "weather",
            "refresh_settings": json.dumps({
                "refreshType": "interval",
                "unit": "minute",
                "interval": "5",
                "playlist": "Default",
                "instance_name": "Home",
            }),
            "imageFile": (BytesIO(_png_bytes()), "victim.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert victim.read_bytes() == b"old-content"
    assert sorted(path.name for path in saved.iterdir()) == ["victim.png"]


def test_add_write_failure_keeps_live_model_upload_owned(
    playlist_env,
    monkeypatch,
):
    saved = playlist_env.tmp_path / "saved"
    saved.mkdir()
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))
    playlist_env.device_config.fail_write = True

    response = playlist_env.client.post(
        "/add_plugin",
        data={
            "plugin_id": "news",
            "refresh_settings": json.dumps({
                "refreshType": "interval",
                "unit": "minute",
                "interval": "5",
                "playlist": "Other",
                "instance_name": "Headlines",
            }),
            "imageFile": (BytesIO(_png_bytes()), "headline.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 500
    added = playlist_env.inner_manager.resolve_plugin_instance_snapshot(
        "Other",
        "news",
        "Headlines",
    ).instance
    referenced = Path(added.settings["imageFile"])
    assert referenced.read_bytes() == _png_bytes()
    assert not list(saved.glob(".*.pending-*"))
    assert not any(event[0] == "signal" for event in playlist_env.events)
