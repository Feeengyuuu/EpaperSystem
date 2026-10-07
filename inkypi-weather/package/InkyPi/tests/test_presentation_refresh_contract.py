import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.plugin_manifest import PluginManifest  # noqa: E402
from refresh_task import _presentation_refresh_enabled  # noqa: E402


PLUGINS_ROOT = Path(__file__).resolve().parents[1] / "src" / "plugins"
REFRESH_ON_DISPLAY_MIXIN = "RefreshOnDisplayPresentationMixin"


class DefaultDeviceConfig:
    """A device that never opted in to display-triggered provider work."""

    def get_config(self, _key, default=None):
        return default


def _plugin_class(manifest):
    for path in sorted((PLUGINS_ROOT / manifest.id).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == manifest.class_name:
                return node
    raise AssertionError(f"{manifest.id}: class {manifest.class_name} not found")


def _defines(class_node, method_name):
    return any(
        isinstance(node, ast.FunctionDef) and node.name == method_name
        for node in class_node.body
    )


def _base_names(class_node):
    return {base.id for base in class_node.bases if isinstance(base, ast.Name)}


def _presentation_plugins():
    plugins = []
    for path in sorted(PLUGINS_ROOT.glob("*/plugin-info.json")):
        manifest = PluginManifest.from_path(path)
        if manifest.capabilities.supports_presentation_refresh:
            plugins.append((manifest, _plugin_class(manifest)))
    return plugins


def test_contract_discovers_presentation_plugins():
    ids = {manifest.id for manifest, _class_node in _presentation_plugins()}

    assert {"backtothedate", "newspaper", "telegram_digest"} <= ids


def test_plugin_owned_presentation_banks_are_dispatched_by_default():
    """A bank the scheduler never dispatches silently freezes on one selection."""

    undispatched = sorted(
        manifest.id
        for manifest, class_node in _presentation_plugins()
        if _defines(class_node, "prepare_presentation")
        and not _presentation_refresh_enabled(
            DefaultDeviceConfig(),
            {"id": manifest.id, "_manifest": manifest},
        )
    )

    assert undispatched == []


@pytest.mark.parametrize(
    ("manifest", "class_node"),
    [
        pytest.param(manifest, class_node, id=manifest.id)
        for manifest, class_node in _presentation_plugins()
        if REFRESH_ON_DISPLAY_MIXIN in _base_names(class_node)
        and not _defines(class_node, "prepare_presentation")
    ],
)
def test_refresh_on_display_rerenders_never_claim_provider_free(manifest, class_node):
    """The mixin forces a provider refresh, so it must stay behind the device gate."""

    del class_node
    assert manifest.capabilities.presentation_refresh_is_provider_free is False
