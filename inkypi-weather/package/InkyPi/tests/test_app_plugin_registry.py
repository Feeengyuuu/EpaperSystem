"""Application lifetime, lazy loading, and concurrent plugin ownership."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from flask import Flask

from plugins.registry import PluginRegistry
from plugins.plugin_registry import get_plugin_instance


def plugin_tree(tmp_path):
    directory = tmp_path / "example"
    directory.mkdir()
    (directory / "example.py").write_text("# factory injected by test\n", encoding="utf-8")
    return {"id": "example", "class": "Example"}


def test_two_applications_do_not_share_or_reset_plugin_instances(tmp_path):
    config = plugin_tree(tmp_path)
    created = []

    def factory(metadata):
        instance = dict(metadata)
        created.append(instance)
        return instance

    first = PluginRegistry(tmp_path, factory=factory)
    second = PluginRegistry(tmp_path, factory=factory)
    first.load([config])
    second.load([config])
    assert created == []
    a = first.get(config)
    b = second.get(config)
    assert a is not b
    second.load([])
    assert first.get(config) is a
    for registry in (first,):
        app = Flask(__name__)
        app.extensions["plugin_registry"] = registry
        with app.app_context():
            assert get_plugin_instance(config) is a


def test_concurrent_first_access_constructs_one_instance_per_registry(tmp_path):
    config = plugin_tree(tmp_path)
    constructed = []
    lock = Lock()

    def factory(metadata):
        with lock:
            constructed.append(metadata)
        return object()

    registry = PluginRegistry(tmp_path, factory=factory)
    registry.load([config])
    barrier = Barrier(4)

    def access(_):
        barrier.wait(timeout=5)
        return registry.get(config)

    with ThreadPoolExecutor(max_workers=4) as executor:
        instances = list(executor.map(access, range(4)))
    assert len(constructed) == 1
    assert all(instance is instances[0] for instance in instances)


def test_failed_construction_can_retry_and_disabled_plugins_are_not_loaded(tmp_path):
    import pytest

    config = plugin_tree(tmp_path)
    attempts = []

    def factory(metadata):
        attempts.append(metadata)
        if len(attempts) == 1:
            raise ImportError("temporarily unavailable")
        return object()

    registry = PluginRegistry(tmp_path, factory=factory)
    registry.load([config])
    with pytest.raises(ImportError):
        registry.get(config)
    assert registry.get(config) is registry.get(config)
    registry.load([{**config, "disabled": True}])
    with pytest.raises(ValueError, match="not registered"):
        registry.get(config)


def test_registry_preserves_legacy_hyphenated_manifest_ids(tmp_path):
    directory = tmp_path / "my-plugin"
    directory.mkdir()
    (directory / "my-plugin.py").write_text("# legacy extension\n", encoding="utf-8")
    config = {"id": "my-plugin", "class": "Example"}
    registry = PluginRegistry(tmp_path, factory=lambda metadata: dict(metadata))
    registry.load([config])
    assert registry.get(config)["id"] == "my-plugin"
