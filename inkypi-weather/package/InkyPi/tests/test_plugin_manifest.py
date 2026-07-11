import importlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Config  # noqa: E402
from plugins import plugin_manifest  # noqa: E402
from plugins.plugin_manifest import (  # noqa: E402
    CapabilityCache,
    PluginManifest,
    inspect_v1_capabilities,
)


PLUGIN_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "plugins"
_UNSET = object()


def _write_plugin(
    root,
    *,
    plugin_id="example",
    class_name="Example",
    schema_version=2,
    supports_live_refresh=False,
    supports_day_night_theme=_UNSET,
    theme=_UNSET,
    source="class Example:\n    pass\n",
):
    plugin_dir = root / "plugins" / plugin_id
    plugin_dir.mkdir(parents=True)
    payload = {
        "display_name": "Example",
        "id": plugin_id,
        "class": class_name,
    }
    if schema_version is not None:
        payload["schema_version"] = schema_version
    if schema_version == 2:
        payload["capabilities"] = {
            "supports_live_refresh": supports_live_refresh,
        }
        if supports_day_night_theme is not _UNSET:
            payload["capabilities"]["supports_day_night_theme"] = (
                supports_day_night_theme
            )
    if theme is not _UNSET:
        payload["theme"] = theme
    manifest_path = plugin_dir / "plugin-info.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    (plugin_dir / f"{plugin_id}.py").write_text(source, encoding="utf-8")
    return manifest_path


def _load_builtin_manifests():
    return [
        PluginManifest.from_path(path)
        for path in sorted(PLUGIN_SOURCE_ROOT.glob("*/plugin-info.json"))
    ]


def test_v2_manifest_declares_live_refresh_without_import(tmp_path, monkeypatch):
    manifest_path = _write_plugin(tmp_path, supports_live_refresh=True)
    imported = []
    monkeypatch.setattr(importlib, "import_module", lambda name: imported.append(name))

    manifest = PluginManifest.from_path(manifest_path)

    assert manifest.capabilities.supports_live_refresh is True
    assert manifest.capabilities.supports_day_night_theme is False
    assert manifest.theme is None
    assert imported == []


def test_v2_manifest_parses_theme_contract(tmp_path):
    manifest_path = _write_plugin(
        tmp_path,
        supports_day_night_theme=True,
        theme={
            "presentation": "media",
            "day": {"background": "#f6f0e4", "accent": "#b33a2b"},
            "night": {"background": "#101318", "accent": "#ff7868"},
        },
    )

    manifest = PluginManifest.from_path(manifest_path)

    assert manifest.capabilities.supports_day_night_theme is True
    assert type(manifest.theme).__name__ == "PluginTheme"
    assert manifest.theme.presentation == "media"
    assert manifest.theme.day == {
        "background": "#f6f0e4",
        "accent": "#b33a2b",
    }
    assert manifest.theme.night == {
        "background": "#101318",
        "accent": "#ff7868",
    }


def test_v2_manifest_theme_contract_is_deeply_immutable(tmp_path):
    manifest_path = _write_plugin(
        tmp_path,
        supports_day_night_theme=True,
        theme={
            "presentation": "ui",
            "day": {"background": "#ffffff", "accent": "#123456"},
            "night": {"background": "#000000", "accent": "#abcdef"},
        },
    )
    manifest = PluginManifest.from_path(manifest_path)

    with pytest.raises(FrozenInstanceError):
        manifest.theme.presentation = "media"
    with pytest.raises(TypeError):
        manifest.theme.day["background"] = "#111111"
    with pytest.raises(TypeError):
        manifest.theme.night["accent"] = "#222222"


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, []])
def test_v2_manifest_rejects_coerced_day_night_theme_booleans(tmp_path, value):
    manifest_path = _write_plugin(
        tmp_path,
        supports_day_night_theme=value,
    )

    with pytest.raises(TypeError, match="supports_day_night_theme"):
        PluginManifest.from_path(manifest_path)


@pytest.mark.parametrize("theme", [_UNSET, None, [], "theme", True])
def test_v2_manifest_requires_theme_object_when_capability_enabled(
    tmp_path,
    theme,
):
    manifest_path = _write_plugin(
        tmp_path,
        supports_day_night_theme=True,
        theme=theme,
    )

    with pytest.raises(TypeError, match="theme"):
        PluginManifest.from_path(manifest_path)


@pytest.mark.parametrize("presentation", [None, "", "UI", "photo", 1, True])
def test_v2_manifest_rejects_unsupported_theme_presentations(
    tmp_path,
    presentation,
):
    manifest_path = _write_plugin(
        tmp_path,
        supports_day_night_theme=True,
        theme={
            "presentation": presentation,
            "day": {"background": "#ffffff", "accent": "#123456"},
            "night": {"background": "#000000", "accent": "#abcdef"},
        },
    )

    with pytest.raises((TypeError, ValueError), match="presentation"):
        PluginManifest.from_path(manifest_path)


@pytest.mark.parametrize("mode", ["day", "night"])
@pytest.mark.parametrize("palette", [None, [], "palette", True])
def test_v2_manifest_requires_theme_palette_objects(tmp_path, mode, palette):
    theme = {
        "presentation": "ui",
        "day": {"background": "#ffffff", "accent": "#123456"},
        "night": {"background": "#000000", "accent": "#abcdef"},
    }
    theme[mode] = palette
    manifest_path = _write_plugin(
        tmp_path,
        supports_day_night_theme=True,
        theme=theme,
    )

    with pytest.raises(TypeError, match=rf"theme\.{mode}"):
        PluginManifest.from_path(manifest_path)


@pytest.mark.parametrize("mode", ["day", "night"])
@pytest.mark.parametrize("role", ["background", "accent"])
@pytest.mark.parametrize(
    "color",
    [None, True, 123456, "ffffff", "#fff", "#12345g", "#1234567"],
)
def test_v2_manifest_requires_six_digit_theme_seed_colors(
    tmp_path,
    mode,
    role,
    color,
):
    theme = {
        "presentation": "ui",
        "day": {"background": "#ffffff", "accent": "#123456"},
        "night": {"background": "#000000", "accent": "#abcdef"},
    }
    theme[mode][role] = color
    manifest_path = _write_plugin(
        tmp_path,
        supports_day_night_theme=True,
        theme=theme,
    )

    with pytest.raises((TypeError, ValueError), match=rf"theme\.{mode}\.{role}"):
        PluginManifest.from_path(manifest_path)


@pytest.mark.parametrize("mode", ["day", "night"])
@pytest.mark.parametrize("role", ["background", "accent"])
def test_v2_manifest_requires_each_theme_seed_role(tmp_path, mode, role):
    theme = {
        "presentation": "ui",
        "day": {"background": "#ffffff", "accent": "#123456"},
        "night": {"background": "#000000", "accent": "#abcdef"},
    }
    del theme[mode][role]
    manifest_path = _write_plugin(
        tmp_path,
        supports_day_night_theme=True,
        theme=theme,
    )

    with pytest.raises(TypeError, match=rf"theme\.{mode}\.{role}"):
        PluginManifest.from_path(manifest_path)


@pytest.mark.parametrize("schema_version", [0, 3, -1, True, "2"])
def test_manifest_rejects_unsupported_or_coerced_schema_versions(
    tmp_path,
    schema_version,
):
    manifest_path = _write_plugin(tmp_path, schema_version=schema_version)

    with pytest.raises((TypeError, ValueError), match="schema_version"):
        PluginManifest.from_path(manifest_path)


@pytest.mark.parametrize("capabilities", [None, [], "live", True])
def test_v2_manifest_rejects_non_object_capabilities(tmp_path, capabilities):
    manifest_path = _write_plugin(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["capabilities"] = capabilities
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TypeError, match="capabilities"):
        PluginManifest.from_path(manifest_path)


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, []])
def test_v2_manifest_rejects_coerced_live_refresh_booleans(tmp_path, value):
    manifest_path = _write_plugin(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["capabilities"]["supports_live_refresh"] = value
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TypeError, match="supports_live_refresh"):
        PluginManifest.from_path(manifest_path)


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, []])
def test_manifest_rejects_coerced_refresh_on_display_booleans(tmp_path, value):
    manifest_path = _write_plugin(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["refresh_on_display"] = value
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TypeError, match="refresh_on_display"):
        PluginManifest.from_path(manifest_path)


def test_all_builtin_manifests_are_v2_and_only_sports_is_live():
    manifests = _load_builtin_manifests()

    assert manifests
    assert all(item.schema_version == 2 for item in manifests)
    assert {
        item.id for item in manifests if item.capabilities.supports_live_refresh
    } == {"sports_dashboard"}
    assert all(
        inspect_v1_capabilities(
            PLUGIN_SOURCE_ROOT / item.id / f"{item.id}.py"
        ).supports_live_refresh
        for item in manifests
        if item.capabilities.supports_live_refresh
    )


def test_v1_capability_inspection_uses_ast_without_executing_source(tmp_path):
    marker = tmp_path / "plugins" / "legacy" / "executed.txt"
    manifest_path = _write_plugin(
        tmp_path,
        plugin_id="legacy",
        class_name="Legacy",
        schema_version=None,
        source=(
            "from pathlib import Path\n"
            "Path(__file__).with_name('executed.txt').write_text('bad')\n"
            "class Legacy:\n"
            "    def get_live_refresh_state(self, settings, current_dt):\n"
            "        return None\n"
        ),
    )

    manifest = PluginManifest.from_path(manifest_path)

    assert manifest.schema_version == 1
    assert manifest.capabilities.supports_live_refresh is True
    assert manifest.capabilities.supports_day_night_theme is False
    assert manifest.theme is None
    assert not marker.exists()


@pytest.mark.parametrize(
    "source",
    [
        (
            "class Helper:\n"
            "    def get_live_refresh_state(self, settings, current_dt):\n"
            "        return None\n"
            "class Example:\n"
            "    pass\n"
        ),
        (
            "class Example:\n"
            "    class Nested:\n"
            "        def get_live_refresh_state(self, settings, current_dt):\n"
            "            return None\n"
        ),
    ],
    ids=["sibling-helper", "nested-helper"],
)
def test_v1_inspection_only_counts_hook_on_manifest_class(tmp_path, source):
    manifest_path = _write_plugin(
        tmp_path,
        plugin_id="legacy",
        schema_version=None,
        source=source,
    )

    manifest = PluginManifest.from_path(manifest_path)

    assert manifest.capabilities.supports_live_refresh is False


def test_v1_capability_cache_is_keyed_by_source_sha(tmp_path, monkeypatch):
    source = (
        "class Legacy:\n"
        "    def get_live_refresh_state(self, settings, current_dt):\n"
        "        return None\n"
    )
    first_path = _write_plugin(
        tmp_path / "one",
        plugin_id="legacy",
        schema_version=None,
        source=source,
    ).with_name("legacy.py")
    second_path = _write_plugin(
        tmp_path / "two",
        plugin_id="legacy",
        schema_version=None,
        source=source,
    ).with_name("legacy.py")
    cache = CapabilityCache()

    first = inspect_v1_capabilities(first_path, cache)
    assert len(cache) == 1
    monkeypatch.setattr(
        plugin_manifest.ast,
        "parse",
        lambda *_args, **_kwargs: pytest.fail("cached source was reparsed"),
    )
    second = inspect_v1_capabilities(second_path, cache)

    assert first.supports_live_refresh is True
    assert second is first


def test_capability_cache_computes_each_source_and_class_once_under_concurrency():
    cache = CapabilityCache()
    factory_entered = threading.Event()
    release_factory = threading.Event()
    factory_calls = []

    def factory():
        factory_calls.append("called")
        factory_entered.set()
        assert release_factory.wait(timeout=2)
        return plugin_manifest.PluginCapabilities(supports_live_refresh=True)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(cache.resolve, "same-sha", "Example", factory)
            for _ in range(8)
        ]
        assert factory_entered.wait(timeout=2)
        release_factory.set()
        results = [future.result(timeout=2) for future in futures]

    assert factory_calls == ["called"]
    assert all(result is results[0] for result in results)


def test_config_plugin_list_attaches_manifest_without_importing(tmp_path, monkeypatch):
    manifest_path = _write_plugin(tmp_path, plugin_id="lazy")
    marker = manifest_path.with_name("imported.txt")
    manifest_path.with_name("lazy.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('imported.txt').write_text('bad')\n"
        "class Example:\n"
        "    pass\n",
        encoding="utf-8",
    )
    config = Config.__new__(Config)
    config.BASE_DIR = str(tmp_path)
    imported = []
    monkeypatch.setattr(importlib, "import_module", lambda name: imported.append(name))

    plugins = config.read_plugins_list()

    assert len(plugins) == 1
    assert plugins[0]["id"] == "lazy"
    assert isinstance(plugins[0]["_manifest"], PluginManifest)
    assert plugins[0]["_manifest"].capabilities.supports_live_refresh is False
    assert imported == []
    assert not marker.exists()


def test_config_public_plugins_are_json_serializable_but_runtime_keeps_manifest(
    tmp_path,
):
    _write_plugin(tmp_path, plugin_id="lazy")
    config = Config.__new__(Config)
    config.BASE_DIR = str(tmp_path)
    config.config = {}
    config.plugins_list = config.read_plugins_list()

    public_plugins = config.get_plugins()
    runtime_plugins = config.get_runtime_plugins()

    assert "_manifest" not in public_plugins[0]
    assert json.loads(json.dumps(public_plugins))[0]["id"] == "lazy"
    assert isinstance(runtime_plugins[0]["_manifest"], PluginManifest)
