"""Helpers and page-shape limits shared by the 整点新闻 modules.

Modules call the helpers through this module (``common._safe_json_write``), so a test
that patches one of them here reaches every module."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from utils.app_utils import bounded_int
from utils.plugin_cache import read_json, write_json

# Stories each column shows.
MAINLAND_NEWS_MAX = 5
WORLD_NEWS_MAX = 5


def _enabled(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _clean_text(value: str, max_len: int = 260) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len]


def _parse_int(value: Any, default: int, low: int, high: int) -> int:
    return bounded_int(value, default, low, high)


def _safe_json_load(path: Path, default: Any) -> Any:
    return read_json(path, default=default)


def _safe_json_write(path: Path, payload: Any) -> None:
    write_json(path, payload, ensure_ascii=False, indent=2)
