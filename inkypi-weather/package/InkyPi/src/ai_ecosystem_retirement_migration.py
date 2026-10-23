"""Retire the AI Ecosystem Pulse page from the owner's rotation once.

Tech Pulse now shows the same skills, models and repos boards beside Hacker News,
and the owner asked for the original page to go once the merged one was live.
Deleting an instance otherwise needs an admin login on the web page, so this
release-bound migration removes it at startup instead: only on the owner's device,
only while a Tech Pulse instance carries the boards, and never twice. The plugin
itself stays, since Tech Pulse reads the boards through its sources and caches.
"""
from collections.abc import Mapping
import logging

from aggregate_pages_migration import _is_owner_device, _thaw

logger = logging.getLogger(__name__)
MIGRATION_ID = "ai_ecosystem_pulse_retired_v1"
RETIRED_PLUGIN = "ai_ecosystem_pulse"
REPLACEMENT_PLUGIN = "tech_pulse"


def apply_ai_ecosystem_retirement_migration(config):
    markers = config.get_config("runtime_migrations", default={})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    version, authoritative, playlist, manager = config.capture_detached_playlist_transaction()
    markers = authoritative.get("runtime_migrations", {})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    if not isinstance(markers, Mapping):
        raise ValueError("AI Ecosystem retirement migration state is malformed")
    instances = manager.snapshot_all_instances()
    if not _is_owner_device(instances) or not any(item.plugin_id == REPLACEMENT_PLUGIN for item in instances):
        return
    removed = []
    for item in instances:
        if item.plugin_id != RETIRED_PLUGIN:
            continue
        if manager.delete_plugin_instance(item.instance_uuid) is None:
            raise ValueError(f"Could not remove {RETIRED_PLUGIN} instance {item.name}")
        removed.append(item.name)
    config.commit_detached_playlist_transaction(
        expected_config_version=version,
        expected_config_data=authoritative,
        expected_playlist_config=playlist,
        playlist_manager=manager,
        config_updates={"runtime_migrations": {**_thaw(markers), MIGRATION_ID: True}},
    )
    if removed:
        logger.warning(
            "Retired the AI Ecosystem Pulse page; Tech Pulse carries its boards. | instances: %s",
            ",".join(removed),
        )
