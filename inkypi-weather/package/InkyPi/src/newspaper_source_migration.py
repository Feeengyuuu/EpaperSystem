"""Replace the blocked Luoyang source in saved newspaper lists with Chinese editions once.

The Luoyang Evening News site rejects requests from the device network (HTTP 403
for every page, including its home page). Saved instances keep their own source
list, so a new default does not reach them; this one-time migration drops the
Luoyang line and adds reachable Simplified Chinese editions without duplicating
entries the user already has.
"""
from collections.abc import Mapping
import logging

from config_store import ConfigConflictError

logger = logging.getLogger(__name__)
MIGRATION_ID = "newspaper_chinese_editions_v1"
LUOYANG_TYPES = frozenset({"lywb", "luoyang", "luoyang_evening_news"})
CHINESE_EDITION_LINES = (
    "Lianhe Zaobao|newspaper|sing_lz",
    "Guangming Daily|epaper|gmrb",
    "Economic Daily|epaper|jjrb",
    "China Youth Daily|epaper|zgqnb",
    "Nanfang Daily|epaper|nfrb",
    "Yangtse Evening Post|epaper|yzwb",
)
_TYPE_ALIASES = {
    "digital": "epaper",
    "digital_edition": "epaper",
    "paper": "newspaper",
    "slug": "newspaper",
    "frontpage": "newspaper",
}


def _identity(line):
    parts = [part.strip() for part in str(line).split("|")]
    if len(parts) < 3:
        return None
    kind = parts[1].lower()
    return _TYPE_ALIASES.get(kind, kind), parts[2].upper()


def migrate_media_sources(text):
    """Return (updated_text, changed) for one saved mediaSources value."""
    lines = str(text).splitlines()
    kept = [line for line in lines if (_identity(line) or ("",))[0] not in LUOYANG_TYPES]
    present = {_identity(line) for line in kept}
    additions = [line for line in CHINESE_EDITION_LINES if _identity(line) not in present]
    if not additions and len(kept) == len(lines):
        return text, False
    anchor = next(
        (index for index, line in enumerate(kept) if _identity(line) == ("newspaper", "CHI_PD")),
        None,
    )
    if anchor is None:
        result = kept + additions
    else:
        result = kept[: anchor + 1] + additions + kept[anchor + 1:]
    return "\n".join(result), True


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def apply_newspaper_chinese_editions_migration(config):
    markers = config.get_config("runtime_migrations", default={})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    version, authoritative, playlist, manager = config.capture_detached_playlist_transaction()
    markers = authoritative.get("runtime_migrations", {})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    if not isinstance(markers, Mapping):
        raise ValueError("Newspaper source migration state is malformed")
    newspapers = [item for item in manager.snapshot_all_instances() if item.plugin_id == "newspaper"]
    if not newspapers:
        return
    migrated = 0
    for before in newspapers:
        saved = before.settings.get("mediaSources")
        if not isinstance(saved, str) or not saved.strip():
            continue
        updated_text, changed = migrate_media_sources(saved)
        if not changed:
            continue
        mutation = manager.update_plugin_instance_atomic(
            before.instance_uuid,
            settings={**_thaw(before.settings), "mediaSources": updated_text},
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
        )
        if mutation is None or mutation.new_snapshot is None:
            raise ConfigConflictError(before.settings_revision, before.settings_revision)
        migrated += 1
    config.commit_detached_playlist_transaction(
        expected_config_version=version,
        expected_config_data=authoritative,
        expected_playlist_config=playlist,
        playlist_manager=manager,
        config_updates={"runtime_migrations": {**_thaw(markers), MIGRATION_ID: True}},
    )
    if migrated:
        logger.warning(
            "Replaced the blocked Luoyang newspaper source with Chinese digital editions. "
            "| instances: %s",
            migrated,
        )
