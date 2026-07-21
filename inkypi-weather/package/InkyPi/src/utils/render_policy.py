"""Select a renderer that fits the runtime without changing refresh cadence."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_CONSTRAINED_MEMORY_KIB = 1024 * 1024


def _optional_bool(value):
    normalized = str(value or "").strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def _mem_total_kib(path: str | os.PathLike[str]) -> int | None:
    try:
        for line in Path(path).read_text(encoding="ascii", errors="ignore").splitlines():
            if not line.startswith("MemTotal:"):
                continue
            return int(line.split()[1])
    except (OSError, IndexError, TypeError, ValueError):
        return None
    return None


def prefer_native_renderer(
    settings: Mapping | None,
    *,
    feature_env: str | None = None,
    meminfo_path: str | os.PathLike[str] = "/proc/meminfo",
) -> bool:
    """Return whether Pillow/native rendering should run before Chromium.

    Explicit instance settings win, followed by a feature-specific environment
    override, the global override, and finally a conservative memory check.
    """

    settings = settings or {}
    for key in ("preferNativeRenderer", "preferPilFallback"):
        explicit = _optional_bool(settings.get(key))
        if explicit is not None:
            return explicit

    if feature_env:
        feature_override = _optional_bool(os.environ.get(feature_env))
        if feature_override is not None:
            return feature_override

    global_override = _optional_bool(os.environ.get("INKYPI_NATIVE_RENDERER_FIRST"))
    if global_override is not None:
        return global_override

    total_kib = _mem_total_kib(meminfo_path)
    return total_kib is not None and total_kib <= _CONSTRAINED_MEMORY_KIB
