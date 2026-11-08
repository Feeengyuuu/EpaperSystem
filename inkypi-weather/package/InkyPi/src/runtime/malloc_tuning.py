"""Bound glibc's malloc in the long-lived service.

By default a thread that allocates while another holds the main arena gets an arena of its
own (up to eight per core), and glibc raises its mmap threshold each time a large block is
freed, so later image buffers of that size come from the arenas and stay scattered there
between renders. On the 448 MB device the service process sat at 180–220 MB in November
2026 and restarted itself when a render pushed free memory under 70 MB. Two arenas are
plenty under the GIL, and a fixed 256 KiB threshold sends every image-sized buffer to mmap,
which hands it back to the system when it is freed.

``mallopt`` must run before the worker threads start; an operator's MALLOC_ARENA_MAX or
MALLOC_MMAP_THRESHOLD_ in the environment wins over these values.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Any, Callable, Mapping

M_MMAP_THRESHOLD = -3
M_ARENA_MAX = -8
ARENA_MAX = 2
MMAP_THRESHOLD_BYTES = 256 * 1024
_SETTINGS = (
    ("arena_max", M_ARENA_MAX, ARENA_MAX, "MALLOC_ARENA_MAX"),
    ("mmap_threshold", M_MMAP_THRESHOLD, MMAP_THRESHOLD_BYTES, "MALLOC_MMAP_THRESHOLD_"),
)


def _load_libc() -> Any:
    return ctypes.CDLL("libc.so.6")


def limit_malloc_arenas(*, platform: str | None = None, environ: Mapping[str, str] | None = None,
                        load_libc: Callable[[], Any] = _load_libc) -> dict[str, int]:
    """Apply the settings glibc accepts; return those applied (empty where nothing changed)."""

    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if not platform.startswith("linux"):
        return {}
    wanted = [setting for setting in _SETTINGS if not str(environ.get(setting[3], "")).strip()]
    if not wanted:
        return {}
    try:
        mallopt = getattr(load_libc(), "mallopt", None)
    except OSError:
        return {}
    if mallopt is None:
        return {}
    applied = {}
    for name, param, value, _variable in wanted:
        try:
            if mallopt(param, value):
                applied[name] = value
        except (OSError, TypeError, ValueError):
            continue
    return applied
