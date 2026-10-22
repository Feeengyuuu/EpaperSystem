"""Recent display commits per playlist instance.

The display manifest only knows the image on the panel now. A page that
rotates its content (the Sports Dashboard) needs to know which of its renders
actually reached the panel, and it renders far more often than it is shown.
Every display commit therefore appends its time to that instance's short
history here. Recording is best effort: it never affects the commit itself.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from utils.atomic_file import atomic_write_json

logger = logging.getLogger(__name__)

HISTORY_NAME = "display_history.json"
# Render setting through which a plugin receives its own recent display times.
RECENT_DISPLAYS_SETTING = "_inkypiRecentDisplays"
SCHEMA_VERSION = 1
DISPLAYS_PER_INSTANCE = 8
MAX_INSTANCES = 96
MAX_HISTORY_BYTES = 64 * 1024


def _parse(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _load(path):
    try:
        if path.is_symlink() or path.stat().st_size > MAX_HISTORY_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return {}
    instances = payload.get("instances")
    return instances if isinstance(instances, dict) else {}


def record_display(display_dir, logical_target, committed_at):
    """Append one commit time to the displayed instance's history."""
    target = logical_target if isinstance(logical_target, Mapping) else {}
    instance_uuid = target.get("instance_uuid")
    stamp = _parse(committed_at)
    if not isinstance(instance_uuid, str) or not instance_uuid.strip() or stamp is None:
        return False
    path = Path(display_dir) / HISTORY_NAME
    instances = _load(path)
    entry = instances.get(instance_uuid) if isinstance(instances.get(instance_uuid), dict) else {}
    displays = [value for value in entry.get("displays") or [] if _parse(value) is not None]
    displays = [stamp.isoformat(), *[value for value in displays if value != stamp.isoformat()]]
    instances[instance_uuid] = {
        "plugin_id": str(target.get("plugin_id") or "")[:64],
        "displays": displays[:DISPLAYS_PER_INSTANCE],
    }
    if len(instances) > MAX_INSTANCES:
        newest = sorted(
            instances.items(),
            key=lambda item: str(((item[1] or {}).get("displays") or [""])[0]),
            reverse=True,
        )
        instances = dict(newest[:MAX_INSTANCES])
    atomic_write_json(path, {"schema_version": SCHEMA_VERSION, "instances": instances}, mode=0o600)
    return True


def recent_displays(display_dir, instance_uuid, *, limit=DISPLAYS_PER_INSTANCE):
    """This instance's display times, newest first, as ISO strings."""
    if display_dir is None or not isinstance(instance_uuid, str) or not instance_uuid:
        return ()
    entry = _load(Path(display_dir) / HISTORY_NAME).get(instance_uuid)
    if not isinstance(entry, dict):
        return ()
    stamps = sorted((stamp for stamp in map(_parse, entry.get("displays") or []) if stamp), reverse=True)
    return tuple(stamp.isoformat() for stamp in stamps[: max(0, int(limit))])
