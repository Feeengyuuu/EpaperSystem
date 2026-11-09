"""Take 时光书签 and 晨读 back out of the owner's rotation once.

The owner asked for the two composite pages to stay out of the rotation for now. Deleting an
instance otherwise needs an admin login on the web page, so this release-bound migration removes
every instance of both at startup instead: only on the owner's device and never twice. The
plugins stay in the repository, and composite_pages_migration has already set its own marker, so
the pages are not added again.
"""
from collections.abc import Mapping
import logging

from aggregate_pages_migration import _is_owner_device, _thaw

logger = logging.getLogger(__name__)
MIGRATION_ID = "composite_pages_retired_v1"
RETIRED_PLUGINS = frozenset({"time_almanac", "morning_read"})


def apply_composite_pages_retirement_migration(config):
    markers = config.get_config("runtime_migrations", default={})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    version, authoritative, playlist, manager = config.capture_detached_playlist_transaction()
    markers = authoritative.get("runtime_migrations", {})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    if not isinstance(markers, Mapping):
        raise ValueError("Composite pages retirement migration state is malformed")
    instances = manager.snapshot_all_instances()
    if not _is_owner_device(instances):
        return
    removed = []
    for item in instances:
        if item.plugin_id not in RETIRED_PLUGINS:
            continue
        if manager.delete_plugin_instance(item.instance_uuid) is None:
            raise ValueError(f"Could not remove {item.plugin_id} instance {item.name}")
        removed.append(item.name)
    config.commit_detached_playlist_transaction(
        expected_config_version=version,
        expected_config_data=authoritative,
        expected_playlist_config=playlist,
        playlist_manager=manager,
        config_updates={"runtime_migrations": {**_thaw(markers), MIGRATION_ID: True}},
    )
    if removed:
        logger.warning("Took the 时光书签 and 晨读 pages out of the rotation. | instances: %s", ",".join(removed))
