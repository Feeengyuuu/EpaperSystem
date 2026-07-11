import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flask import Flask

import plugins  # noqa: E402
from plugins import plugin_registry  # noqa: E402
from plugins.plugin_manifest import (  # noqa: E402
    PluginCapabilities,
    PluginManifest,
)


def _activate_plugin_root(src_root, *plugin_ids):
    plugin_path = str(src_root / "plugins")
    if plugin_path not in plugins.__path__:
        plugins.__path__.append(plugin_path)
    for module_name in list(sys.modules):
        if any(module_name == f"plugins.{plugin_id}" or module_name.startswith(f"plugins.{plugin_id}.") for plugin_id in plugin_ids):
            del sys.modules[module_name]


def _write_plugin(root, plugin_id, class_name, body):
    plugin_dir = root / "plugins" / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / f"{plugin_id}.py").write_text(body, encoding="utf-8")
    return {"id": plugin_id, "class": class_name}


def test_load_plugins_registers_metadata_without_importing_modules(tmp_path, monkeypatch):
    src_root = tmp_path / "src"
    marker = src_root / "plugins" / "lazy_plugin" / "imported.txt"
    plugin_config = _write_plugin(
        src_root,
        "lazy_plugin",
        "LazyPlugin",
        "from pathlib import Path\n"
        "Path(__file__).with_name('imported.txt').write_text('imported', encoding='utf-8')\n"
        "class LazyPlugin:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n",
    )
    monkeypatch.setenv("SRC_DIR", str(src_root))
    monkeypatch.syspath_prepend(str(src_root))
    _activate_plugin_root(src_root, "lazy_plugin")
    importlib.invalidate_caches()

    plugin_registry.load_plugins([plugin_config])

    assert "lazy_plugin" in plugin_registry.PLUGIN_CONFIGS
    assert "lazy_plugin" not in plugin_registry.PLUGIN_CLASSES
    assert not marker.exists()

    instance = plugin_registry.get_plugin_instance(plugin_config)

    assert marker.exists()
    assert instance is plugin_registry.get_plugin_instance(plugin_config)
    assert instance.config["id"] == "lazy_plugin"


def test_load_plugins_preserves_lazy_manifest_metadata(tmp_path, monkeypatch):
    src_root = tmp_path / "src"
    plugin_config = _write_plugin(
        src_root,
        "manifest_plugin",
        "ManifestPlugin",
        "class ManifestPlugin:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n",
    )
    manifest_path = src_root / "plugins" / "manifest_plugin" / "plugin-info.json"
    manifest_path.write_text(
        '{"schema_version": 2, "display_name": "Manifest", '
        '"id": "manifest_plugin", "class": "ManifestPlugin", '
        '"capabilities": {"supports_live_refresh": false}}',
        encoding="utf-8",
    )
    manifest = PluginManifest.from_path(manifest_path)
    plugin_config["_manifest"] = manifest
    monkeypatch.setenv("SRC_DIR", str(src_root))

    plugin_registry.load_plugins([plugin_config])

    assert plugin_registry.PLUGIN_CONFIGS["manifest_plugin"]["_manifest"] is manifest
    assert "manifest_plugin" not in plugin_registry.PLUGIN_CLASSES


def test_manifest_live_refresh_capability_is_read_without_loading_plugin():
    manifest = PluginManifest(
        schema_version=2,
        id="ordinary_plugin",
        class_name="OrdinaryPlugin",
        display_name="Ordinary Plugin",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_live_refresh=False),
        raw={},
    )

    assert plugin_registry.plugin_supports_live_refresh({"_manifest": manifest}) is False
    assert plugin_registry.plugin_supports_live_refresh({"id": "legacy-caller"}) is True


def test_manifest_day_night_capability_is_opt_in_and_metadata_only(monkeypatch):
    manifest = SimpleNamespace(
        capabilities=SimpleNamespace(supports_day_night_theme=True),
    )
    imported = []
    monkeypatch.setattr(importlib, "import_module", lambda name: imported.append(name))

    assert plugin_registry.plugin_supports_day_night_theme(
        {"_manifest": manifest}
    ) is True
    assert plugin_registry.plugin_supports_day_night_theme(
        {"id": "legacy-caller"}
    ) is False
    assert imported == []


def test_base_plugin_exposes_day_night_capability_in_template_params(tmp_path):
    from plugins.base_plugin.base_plugin import BasePlugin

    manifest = SimpleNamespace(
        capabilities=SimpleNamespace(supports_day_night_theme=True),
    )
    plugin = BasePlugin.__new__(BasePlugin)
    plugin.config = {"id": "themed", "_manifest": manifest}
    plugin.get_plugin_dir = lambda _path=None: str(tmp_path / "missing")

    template_params = plugin.generate_settings_template()

    assert template_params["supports_day_night_theme"] is True


def test_register_plugin_blueprints_imports_only_declared_blueprint_plugins(tmp_path, monkeypatch):
    src_root = tmp_path / "src"
    lazy_marker = src_root / "plugins" / "lazy_plugin" / "imported.txt"
    blueprint_marker = src_root / "plugins" / "route_plugin" / "imported.txt"
    lazy_config = _write_plugin(
        src_root,
        "lazy_plugin",
        "LazyPlugin",
        "from pathlib import Path\n"
        "Path(__file__).with_name('imported.txt').write_text('imported', encoding='utf-8')\n"
        "class LazyPlugin:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n",
    )
    route_config = _write_plugin(
        src_root,
        "route_plugin",
        "RoutePlugin",
        "from pathlib import Path\n"
        "from flask import Blueprint\n"
        "Path(__file__).with_name('imported.txt').write_text('imported', encoding='utf-8')\n"
        "class RoutePlugin:\n"
        "    def __init__(self, config):\n"
        "        self.config = config\n"
        "    def get_blueprint(self):\n"
        "        bp = Blueprint('route_plugin_test', __name__, url_prefix='/route-plugin-test')\n"
        "        @bp.route('/ping')\n"
        "        def ping():\n"
        "            return 'pong'\n"
        "        return bp\n",
    )
    route_config["has_blueprint"] = True
    monkeypatch.setenv("SRC_DIR", str(src_root))
    monkeypatch.syspath_prepend(str(src_root))
    _activate_plugin_root(src_root, "lazy_plugin", "route_plugin")
    importlib.invalidate_caches()

    plugin_registry.load_plugins([lazy_config, route_config])
    app = Flask(__name__)
    plugin_registry.register_plugin_blueprints(app)

    assert not lazy_marker.exists()
    assert blueprint_marker.exists()
    assert app.test_client().get("/route-plugin-test/ping").data == b"pong"


def test_concurrent_first_render_constructs_single_instance(monkeypatch):
    import threading
    import time

    monkeypatch.setitem(plugin_registry.PLUGIN_CONFIGS, "race_plugin", {"id": "race_plugin", "class": "Race"})
    monkeypatch.delitem(plugin_registry.PLUGIN_CLASSES, "race_plugin", raising=False)
    constructed = []

    def slow_loader(config):
        constructed.append(config["id"])
        time.sleep(0.05)
        return object()

    monkeypatch.setattr(plugin_registry, "_load_plugin_instance", slow_loader)

    barrier = threading.Barrier(4)
    instances = []

    def worker():
        barrier.wait()
        instances.append(plugin_registry.get_plugin_instance({"id": "race_plugin"}))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    plugin_registry.PLUGIN_CLASSES.pop("race_plugin", None)

    assert len(constructed) == 1
    assert all(instance is instances[0] for instance in instances)
