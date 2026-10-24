"""Retire the separate North America box office page from the owner's rotation once.

The official China chart now shows the North America weekly chart in a second column,
and the owner asked for the two pages to become one. Deleting an instance otherwise
needs an admin login on the web page, so this release-bound migration removes it at
startup instead: only on the owner's device, only while an official China chart that
shows both markets is in the rotation, and never twice. The plugin itself stays, since
that column reads the chart through its source, cache and posters.
"""
from collections.abc import Mapping
import logging

from aggregate_pages_migration import _is_owner_device, _thaw

logger = logging.getLogger(__name__)
MIGRATION_ID = "box_office_two_markets_v1"
RETIRED_PLUGIN = "china_box_office_top_movies"
HOST_PLUGIN = "box_office_top_movies"


def _shows_both_markets(item):
    settings = item.settings if isinstance(item.settings, Mapping) else {}
    column = str(settings.get("northAmericaColumn", "true")).strip().lower()
    return (str(settings.get("sourceMode") or "").strip().lower() == "official_china"
            and column not in {"0", "false", "off", "no", "none"})


def apply_box_office_two_markets_migration(config):
    markers = config.get_config("runtime_migrations", default={})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    version, authoritative, playlist, manager = config.capture_detached_playlist_transaction()
    markers = authoritative.get("runtime_migrations", {})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    if not isinstance(markers, Mapping):
        raise ValueError("Box office two-market migration state is malformed")
    instances = manager.snapshot_all_instances()
    if not _is_owner_device(instances) or not any(
        item.plugin_id == HOST_PLUGIN and _shows_both_markets(item) for item in instances
    ):
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
            "Retired the separate North America box office page; the China chart shows both markets. | instances: %s",
            ",".join(removed),
        )
