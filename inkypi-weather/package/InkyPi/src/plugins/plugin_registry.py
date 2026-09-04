"""Compatibility facade; production plugin ownership lives on the Flask app."""

import logging
from pathlib import Path
from flask import current_app, has_app_context

from utils.app_utils import resolve_path
from plugins.registry import PluginRegistry, construct_plugin

logger = logging.getLogger(__name__)
PLUGINS_DIR = "plugins"
# Mutable views are retained only for older standalone integrations and fixtures.
# Every production app owns a different registry through app.extensions.
_DEFAULT_REGISTRY = PluginRegistry(
    Path(resolve_path(PLUGINS_DIR)), factory=lambda config: _load_plugin_instance(config),
)
PLUGIN_CONFIGS = _DEFAULT_REGISTRY._configs
PLUGIN_CLASSES = _DEFAULT_REGISTRY._instances


def plugin_supports_live_refresh(plugin_config):
    """Read live-refresh capability metadata without importing plugin code.

    Config attaches ``_manifest`` to every discovered manifest. Metadata-free
    dictionaries remain opt-in compatible for legacy callers that construct
    plugin configs directly.
    """
    manifest = plugin_config.get("_manifest") if plugin_config else None
    if manifest is None:
        return True
    capabilities = getattr(manifest, "capabilities", None)
    return bool(getattr(capabilities, "supports_live_refresh", False))


def plugin_supports_day_night_theme(plugin_config):
    """Read the opt-in theme capability without importing plugin code."""

    manifest = plugin_config.get("_manifest") if plugin_config else None
    if manifest is None:
        return False
    capabilities = getattr(manifest, "capabilities", None)
    return bool(getattr(capabilities, "supports_day_night_theme", False))


def plugin_supports_cached_display_redraw(plugin_config):
    """Opt in to audited local-only redraws of time-sensitive cached values."""
    manifest = plugin_config.get("_manifest") if plugin_config else None
    capabilities = getattr(manifest, "capabilities", None)
    return bool(getattr(capabilities, "supports_cached_display_redraw", False))


def plugin_supports_presentation_refresh(plugin_config):
    """Read the opt-in presentation capability without importing plugin code."""

    manifest = plugin_config.get("_manifest") if plugin_config else None
    if manifest is None:
        return False
    capabilities = getattr(manifest, "capabilities", None)
    return bool(getattr(capabilities, "supports_presentation_refresh", False))


def plugin_presentation_refresh_is_provider_free(plugin_config):
    """Read the audited provider-free presentation attestation from metadata."""

    manifest = plugin_config.get("_manifest") if plugin_config else None
    if manifest is None:
        return False
    capabilities = getattr(manifest, "capabilities", None)
    return bool(
        getattr(capabilities, "supports_presentation_refresh", False)
        and getattr(
            capabilities,
            "presentation_refresh_is_provider_free",
            False,
        )
    )


def plugin_allows_display_triggered_provider_refresh(plugin_config):
    """Read the explicit provider-refresh exception from plugin metadata."""

    manifest = plugin_config.get("_manifest") if plugin_config else None
    if manifest is None:
        return False
    capabilities = getattr(manifest, "capabilities", None)
    return bool(
        getattr(capabilities, "supports_presentation_refresh", False)
        and getattr(
            capabilities,
            "allows_display_triggered_provider_refresh",
            False,
        )
    )


def load_plugins(plugins_config, *, registry=None):
    """Register metadata in the explicitly supplied or standalone registry."""
    target = registry if registry is not None else _DEFAULT_REGISTRY
    if registry is None:
        # Legacy tests/launchers may set SRC_DIR after importing this module.
        target._root = Path(resolve_path(PLUGINS_DIR))
    target.load(plugins_config)


def _load_plugin_instance(plugin_config):
    """Compatibility construction hook; the implementation has one authority."""
    return construct_plugin(plugin_config)


def register_plugin_blueprints(app):
    registry = app.extensions.get("plugin_registry", _DEFAULT_REGISTRY)
    registry.register_blueprints(app)


def get_plugin_instance(plugin_config):
    registry = _DEFAULT_REGISTRY
    if has_app_context():
        registry = current_app.extensions.get("plugin_registry", registry)
    return registry.get(plugin_config)
