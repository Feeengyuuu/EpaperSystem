"""Lazy plugin instances owned by one application, with no process-global state."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable, Mapping


logger = logging.getLogger(__name__)
PluginConfig = Mapping[str, Any]


def construct_plugin(config: PluginConfig) -> Any:
    plugin_id = config.get("id")
    module_name = f"plugins.{plugin_id}.{plugin_id}"
    module = importlib.import_module(module_name)
    class_name = config.get("class")
    plugin_class = getattr(module, class_name, None) if isinstance(class_name, str) else None
    if plugin_class is None:
        raise ValueError(f"Plugin '{plugin_id}' class '{config.get('class')}' is not registered.")
    return plugin_class(dict(config))


class PluginRegistry:
    """Own metadata and serialize construction for an application's lifetime."""

    def __init__(
        self, root: Path, *, factory: Callable[[PluginConfig], Any] = construct_plugin,
    ) -> None:
        self._root = Path(root)
        self._factory = factory
        self._configs: dict[str, dict[str, Any]] = {}
        self._instances: dict[str, Any] = {}
        self._lock = RLock()

    def load(self, configs: Iterable[PluginConfig]) -> None:
        """Validate metadata without importing plugins; call before starting workers."""
        registered = {}
        for config in configs:
            plugin_id = config.get("id")
            if config.get("disabled", False):
                continue
            if (
                not isinstance(plugin_id, str) or not plugin_id.strip()
                or plugin_id in {".", ".."} or "/" in plugin_id or "\\" in plugin_id
            ):
                logger.error("Invalid plugin id %r, skipping", plugin_id)
                continue
            module_path = self._root / plugin_id / f"{plugin_id}.py"
            if not module_path.is_file():
                logger.error("Could not find plugin module %s, skipping", module_path)
                continue
            registered[plugin_id] = dict(config)
        with self._lock:
            self._configs.clear()
            self._configs.update(registered)
            self._instances.clear()

    def get(self, config: PluginConfig) -> Any:
        plugin_id = config.get("id")
        with self._lock:
            if plugin_id not in self._configs:
                raise ValueError(f"Plugin '{plugin_id}' is not registered.")
            if plugin_id not in self._instances:
                metadata = {**self._configs[plugin_id], **config}
                self._instances[plugin_id] = self._factory(metadata)
            return self._instances[plugin_id]

    def register_blueprints(self, app: Any) -> None:
        with self._lock:
            configs = tuple(dict(config) for config in self._configs.values())
        for config in configs:
            if not config.get("has_blueprint", False):
                continue
            try:
                instance = self.get(config)
                factory = getattr(instance, "get_blueprint", None)
                blueprint = factory() if factory is not None else None
                if blueprint:
                    app.register_blueprint(blueprint)
            except Exception:
                logger.exception("Failed to register blueprint for %s", config["id"])
