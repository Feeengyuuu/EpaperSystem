import importlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from PIL import Image

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
    supports_presentation_refresh=_UNSET,
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
        if supports_presentation_refresh is not _UNSET:
            payload["capabilities"]["supports_presentation_refresh"] = (
                supports_presentation_refresh
            )
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
    assert manifest.capabilities.supports_presentation_refresh is False
    assert manifest.capabilities.supports_day_night_theme is False
    assert manifest.theme is None
    assert imported == []


@pytest.mark.parametrize("enabled", [False, True])
def test_v2_manifest_declares_presentation_capability_without_import(
    tmp_path,
    monkeypatch,
    enabled,
):
    manifest_path = _write_plugin(
        tmp_path,
        supports_presentation_refresh=enabled,
    )
    imported = []
    monkeypatch.setattr(importlib, "import_module", lambda name: imported.append(name))

    manifest = PluginManifest.from_path(manifest_path)

    assert manifest.capabilities.supports_presentation_refresh is enabled
    assert imported == []


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, []])
def test_v2_manifest_rejects_coerced_presentation_refresh_booleans(
    tmp_path,
    value,
):
    manifest_path = _write_plugin(
        tmp_path,
        supports_presentation_refresh=value,
    )

    with pytest.raises(TypeError, match="supports_presentation_refresh"):
        PluginManifest.from_path(manifest_path)


def test_presentation_contract_types_are_frozen_and_detach_mutable_image():
    presentation = importlib.import_module("plugins.base_plugin.presentation")
    request = presentation.PresentationRequestContext(
        request_id="a" * 32,
        requested_at=" 2026-07-12T10:00:00+00:00 ",
        origin_display_commit_id=" display-commit ",
        last_receipt=None,
    )
    source_image = Image.new("RGB", (1, 1), "red")
    preparation = presentation.PresentationPreparation(
        request_id=request.request_id,
        image=source_image,
        changed=True,
    )
    source_image.putpixel((0, 0), (0, 0, 255))

    assert [mode.value for mode in presentation.PresentationMode] == [
        "no_change",
        "prepared_bank",
        "legacy_async",
    ]
    assert request.requested_at == "2026-07-12T10:00:00+00:00"
    assert request.origin_display_commit_id == "display-commit"
    assert preparation.image is not source_image
    assert preparation.image.getpixel((0, 0)) == (255, 0, 0)
    with pytest.raises(FrozenInstanceError):
        request.request_id = "b" * 32
    with pytest.raises(FrozenInstanceError):
        preparation.changed = False


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("request_id", 1, TypeError),
        ("request_id", "request", ValueError),
        ("requested_at", None, TypeError),
        ("requested_at", "not-a-timestamp", ValueError),
        ("origin_display_commit_id", 1, TypeError),
        ("origin_display_commit_id", "  ", ValueError),
        ("last_receipt", object(), TypeError),
    ],
)
def test_presentation_request_context_strictly_validates_required_fields(
    field,
    value,
    error,
):
    presentation = importlib.import_module("plugins.base_plugin.presentation")
    values = {
        "request_id": "a" * 32,
        "requested_at": "2026-07-12T10:00:00+00:00",
        "origin_display_commit_id": "display-commit",
        "last_receipt": None,
    }
    values[field] = value

    with pytest.raises(error, match=field):
        presentation.PresentationRequestContext(**values)


@pytest.mark.parametrize(
    ("request_id", "image", "changed", "error"),
    [
        (1, None, False, TypeError),
        ("request", None, False, ValueError),
        ("a" * 32, object(), True, TypeError),
        ("a" * 32, None, 1, TypeError),
        ("a" * 32, None, True, ValueError),
        ("a" * 32, Image.new("RGB", (1, 1)), False, ValueError),
    ],
)
def test_presentation_preparation_strictly_validates_image_contract(
    request_id,
    image,
    changed,
    error,
):
    presentation = importlib.import_module("plugins.base_plugin.presentation")

    with pytest.raises(error):
        presentation.PresentationPreparation(
            request_id=request_id,
            image=image,
            changed=changed,
        )


def test_base_presentation_defaults_do_not_render_or_select_legacy_async():
    from plugins.base_plugin.base_plugin import BasePlugin

    presentation = importlib.import_module("plugins.base_plugin.presentation")
    rendered = []
    plugin = BasePlugin.__new__(BasePlugin)
    plugin.config = {
        "id": "ordinary",
        "_manifest": type(
            "Manifest",
            (),
            {
                "capabilities": type(
                    "Capabilities",
                    (),
                    {"supports_presentation_refresh": True},
                )()
            },
        )(),
    }
    plugin.generate_image = lambda *_args, **_kwargs: rendered.append(True)
    request = presentation.PresentationRequestContext(
        request_id="a" * 32,
        requested_at="2026-07-12T10:00:00+00:00",
        origin_display_commit_id="display-commit",
        last_receipt=None,
    )

    assert plugin.presentation_mode({}) is presentation.PresentationMode.NO_CHANGE
    assert plugin.reconcile_presentation_receipt({}, None) is None
    assert plugin.reconcile_presentation_receipt({}, None) is None
    with pytest.raises(NotImplementedError, match="presentation"):
        plugin.prepare_presentation(
            {},
            {},
            request=request,
            resolved_theme_context={},
        )
    assert rendered == []


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


def test_all_builtin_manifests_are_v2_and_only_audited_plugins_are_live():
    manifests = _load_builtin_manifests()

    assert manifests
    assert all(item.schema_version == 2 for item in manifests)
    assert {item.id for item in manifests if item.capabilities.supports_live_refresh} == {
        "live_radar",
        "sports_dashboard",
    }
    assert all(
        inspect_v1_capabilities(PLUGIN_SOURCE_ROOT / item.id / f"{item.id}.py").supports_live_refresh
        for item in manifests
        if item.capabilities.supports_live_refresh
    )


def test_live_radar_manifest_declares_live_and_presentation_capabilities():
    manifest = PluginManifest.from_path(PLUGIN_SOURCE_ROOT / "live_radar" / "plugin-info.json")

    assert manifest.capabilities.supports_live_refresh is True
    assert manifest.capabilities.supports_presentation_refresh is True


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
    assert manifest.capabilities.supports_presentation_refresh is False
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
