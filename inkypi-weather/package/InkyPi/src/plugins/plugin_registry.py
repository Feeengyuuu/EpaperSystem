# app_registry.py

import importlib
import logging
import threading
from pathlib import Path

from utils.app_utils import resolve_path

logger = logging.getLogger(__name__)
PLUGINS_DIR = "plugins"
PLUGIN_CONFIGS = {}
PLUGIN_CLASSES = {}
# Serializes lazy plugin construction so concurrent first renders of the same
# plugin cannot build two instances
_REGISTRY_LOCK = threading.Lock()


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


def plugin_supports_presentation_refresh(plugin_config):
    """Read the opt-in presentation capability without importing plugin code."""

    manifest = plugin_config.get("_manifest") if plugin_config else None
    if manifest is None:
        return False
    capabilities = getattr(manifest, "capabilities", None)
    return bool(getattr(capabilities, "supports_presentation_refresh", False))


def load_plugins(plugins_config):
    """Register plugin metadata without importing plugin modules."""
    PLUGIN_CONFIGS.clear()
    PLUGIN_CLASSES.clear()
    plugins_module_path = Path(resolve_path(PLUGINS_DIR))
    for plugin in plugins_config:
        plugin_id = plugin.get("id")
        if plugin.get("disabled", False):
            logger.info(f"Plugin {plugin_id} is disabled, skipping.")
            continue

        plugin_dir = plugins_module_path / plugin_id
        if not plugin_dir.is_dir():
            logger.error(f"Could not find plugin directory {plugin_dir} for '{plugin_id}', skipping.")
            continue

        module_path = plugin_dir / f"{plugin_id}.py"
        if not module_path.is_file():
            logger.error(f"Could not find module path {module_path} for '{plugin_id}', skipping.")
            continue

        PLUGIN_CONFIGS[plugin_id] = dict(plugin)


def _load_plugin_instance(plugin_config):
    plugin_id = plugin_config.get("id")
    module_name = f"plugins.{plugin_id}.{plugin_id}"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        logger.error(f"Failed to import plugin module {module_name}: {exc}")
        raise

    plugin_class = getattr(module, plugin_config.get("class"), None)
    if not plugin_class:
        raise ValueError(f"Plugin '{plugin_id}' class '{plugin_config.get('class')}' is not registered.")
    return plugin_class(plugin_config)


def register_plugin_blueprints(app):
    """Register optional Flask blueprints for plugins that declare startup routes."""
    for plugin_id, plugin_config in PLUGIN_CONFIGS.items():
        if not plugin_config.get("has_blueprint", False):
            continue
        try:
            plugin_instance = get_plugin_instance(plugin_config)
            if not hasattr(plugin_instance, "get_blueprint"):
                continue

            blueprint = plugin_instance.get_blueprint()
            if blueprint:
                app.register_blueprint(blueprint)
                logger.info(f"Registered blueprint for plugin '{plugin_id}'")
        except Exception as e:
            logger.warning(f"Failed to register blueprint for plugin '{plugin_id}': {e}")


def get_plugin_instance(plugin_config):
    plugin_id = plugin_config.get("id")
    if plugin_id not in PLUGIN_CONFIGS:
        raise ValueError(f"Plugin '{plugin_id}' is not registered.")

    plugin_class = PLUGIN_CLASSES.get(plugin_id)
    if plugin_class:
        return plugin_class

    with _REGISTRY_LOCK:
        plugin_class = PLUGIN_CLASSES.get(plugin_id)
        if plugin_class:
            return plugin_class

        registered_config = dict(PLUGIN_CONFIGS[plugin_id])
        registered_config.update(plugin_config)
        plugin_class = _load_plugin_instance(registered_config)
        PLUGIN_CLASSES[plugin_id] = plugin_class
        return plugin_class
