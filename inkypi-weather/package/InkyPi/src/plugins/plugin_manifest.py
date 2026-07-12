"""Side-effect-free plugin manifest and legacy capability inspection."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal


logger = logging.getLogger(__name__)
_LIVE_REFRESH_HOOK = "get_live_refresh_state"
_HEX_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}\Z")


@dataclass(frozen=True)
class PluginCapabilities:
    supports_live_refresh: bool = False
    supports_presentation_refresh: bool = False
    supports_day_night_theme: bool = False


@dataclass(frozen=True)
class PluginTheme:
    presentation: Literal["ui", "media"]
    day: Mapping[str, str]
    night: Mapping[str, str]


def _parse_theme_palette(raw_theme, mode):
    palette = raw_theme.get(mode)
    if type(palette) is not dict:
        raise TypeError(f"plugin manifest theme.{mode} must be an object")

    colors = {}
    for role in ("background", "accent"):
        color = palette.get(role)
        if type(color) is not str:
            raise TypeError(
                f"plugin manifest theme.{mode}.{role} must be a six-digit hex color"
            )
        if _HEX_COLOR_PATTERN.fullmatch(color) is None:
            raise ValueError(
                f"plugin manifest theme.{mode}.{role} must be a six-digit hex color"
            )
        colors[role] = color
    return MappingProxyType(colors)


class CapabilityCache:
    """Thread-safe source-content cache used by legacy manifest inspection."""

    def __init__(self):
        self._by_source_sha256: dict[
            tuple[str, str | None],
            PluginCapabilities,
        ] = {}
        self._lock = threading.Lock()

    def get(
        self,
        source_sha256: str,
        class_name: str | None = None,
    ) -> PluginCapabilities | None:
        with self._lock:
            return self._by_source_sha256.get((source_sha256, class_name))

    def put(
        self,
        source_sha256: str,
        capabilities: PluginCapabilities,
        class_name: str | None = None,
    ) -> PluginCapabilities:
        with self._lock:
            return self._by_source_sha256.setdefault(
                (source_sha256, class_name),
                capabilities,
            )

    def resolve(
        self,
        source_sha256: str,
        class_name: str | None,
        factory: Callable[[], PluginCapabilities],
    ) -> PluginCapabilities:
        """Return one cached value, serializing the first computation."""

        key = (source_sha256, class_name)
        with self._lock:
            cached = self._by_source_sha256.get(key)
            if cached is not None:
                return cached
            capabilities = factory()
            self._by_source_sha256[key] = capabilities
            return capabilities

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_source_sha256)


_DEFAULT_CAPABILITY_CACHE = CapabilityCache()


def inspect_v1_capabilities(
    source_path,
    capability_cache: CapabilityCache | None = None,
    *,
    class_name: str | None = None,
) -> PluginCapabilities:
    """Inspect a legacy plugin's class methods without importing its module."""

    path = Path(source_path)
    try:
        source = path.read_bytes()
    except OSError as error:
        logger.warning("Could not inspect legacy plugin source %s: %s", path, error)
        return PluginCapabilities()

    source_sha256 = hashlib.sha256(source).hexdigest()
    cache = (
        capability_cache
        if capability_cache is not None
        else _DEFAULT_CAPABILITY_CACHE
    )
    def inspect_source() -> PluginCapabilities:
        try:
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, ValueError) as error:
            logger.warning(
                "Could not parse legacy plugin source %s: %s",
                path,
                error,
            )
            return PluginCapabilities()

        top_level_classes = [
            node for node in tree.body if isinstance(node, ast.ClassDef)
        ]
        target_classes = top_level_classes
        if class_name is not None:
            target_classes = [
                node for node in top_level_classes if node.name == class_name
            ]
        supports_live_refresh = any(
            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            and statement.name == _LIVE_REFRESH_HOOK
            for node in target_classes
            for statement in node.body
        )
        return PluginCapabilities(
            supports_live_refresh=supports_live_refresh,
        )

    return cache.resolve(source_sha256, class_name, inspect_source)


@dataclass(frozen=True)
class PluginManifest:
    schema_version: int
    id: str
    class_name: str
    display_name: str
    refresh_on_display: bool
    capabilities: PluginCapabilities
    raw: Mapping[str, Any]
    theme: PluginTheme | None = None

    @classmethod
    def from_path(
        cls,
        path,
        *,
        capability_cache: CapabilityCache | None = None,
    ) -> "PluginManifest":
        manifest_path = Path(path)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if type(payload) is not dict:
            raise TypeError("plugin manifest root must be an object")

        schema_version = payload.get("schema_version", 1)
        if type(schema_version) is not int:
            raise TypeError("plugin manifest schema_version must be integer 1 or 2")
        if schema_version not in {1, 2}:
            raise ValueError("plugin manifest schema_version must be 1 or 2")

        required_strings = {}
        for field_name in ("id", "class", "display_name"):
            value = payload.get(field_name)
            if type(value) is not str or not value.strip():
                raise TypeError(
                    f"plugin manifest {field_name} must be a non-empty string"
                )
            required_strings[field_name] = value

        refresh_on_display = payload.get("refresh_on_display", False)
        if type(refresh_on_display) is not bool:
            raise TypeError(
                "plugin manifest refresh_on_display must be a boolean"
            )

        theme = None
        if schema_version == 2:
            raw_capabilities = payload.get("capabilities", {})
            if type(raw_capabilities) is not dict:
                raise TypeError("plugin manifest capabilities must be an object")
            supports_live_refresh = raw_capabilities.get(
                "supports_live_refresh",
                False,
            )
            if type(supports_live_refresh) is not bool:
                raise TypeError(
                    "plugin manifest capabilities.supports_live_refresh "
                    "must be a boolean"
                )
            supports_day_night_theme = raw_capabilities.get(
                "supports_day_night_theme",
                False,
            )
            if type(supports_day_night_theme) is not bool:
                raise TypeError(
                    "plugin manifest capabilities.supports_day_night_theme "
                    "must be a boolean"
                )
            supports_presentation_refresh = raw_capabilities.get(
                "supports_presentation_refresh",
                False,
            )
            if type(supports_presentation_refresh) is not bool:
                raise TypeError(
                    "plugin manifest capabilities.supports_presentation_refresh "
                    "must be a boolean"
                )
            capabilities = PluginCapabilities(
                supports_live_refresh=supports_live_refresh,
                supports_presentation_refresh=supports_presentation_refresh,
                supports_day_night_theme=supports_day_night_theme,
            )
            if supports_day_night_theme:
                raw_theme = payload.get("theme")
                if type(raw_theme) is not dict:
                    raise TypeError("plugin manifest theme must be an object")
                presentation = raw_theme.get("presentation")
                if type(presentation) is not str:
                    raise TypeError(
                        "plugin manifest theme.presentation must be ui or media"
                    )
                if presentation not in {"ui", "media"}:
                    raise ValueError(
                        "plugin manifest theme.presentation must be ui or media"
                    )
                theme = PluginTheme(
                    presentation=presentation,
                    day=_parse_theme_palette(raw_theme, "day"),
                    night=_parse_theme_palette(raw_theme, "night"),
                )
        else:
            plugin_id = required_strings["id"]
            capabilities = inspect_v1_capabilities(
                manifest_path.with_name(f"{plugin_id}.py"),
                capability_cache,
                class_name=required_strings["class"],
            )
            logger.warning(
                "Plugin %s uses manifest schema v1; migrate to v2",
                plugin_id,
            )

        return cls(
            schema_version=schema_version,
            id=required_strings["id"],
            class_name=required_strings["class"],
            display_name=required_strings["display_name"],
            refresh_on_display=refresh_on_display,
            capabilities=capabilities,
            raw=MappingProxyType(payload),
            theme=theme,
        )
