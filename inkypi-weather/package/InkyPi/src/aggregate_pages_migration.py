"""Add the four aggregate pages to the owner's rotation once.

The owner asked for Bay Alerts, Money Radar, Tech & Play and Bay Druid to join the
DailyDoseOfDay playlist. Creating instances otherwise needs an admin login on the
web page, so this release-bound migration adds them at startup instead: only on
the owner's device (recognised by its saved LiveRadar instance), never twice, and
without touching a page that is already in any playlist.
"""
from collections.abc import Mapping
import hashlib
import logging

logger = logging.getLogger(__name__)
MIGRATION_ID = "aggregate_pages_playlist_v1"
PLAYLIST = "DailyDoseOfDay"
# sha256(instance_uuid)[:16] of the owner's LiveRadar instance, as config.py pins it.
OWNER_LIVE_RADAR_UUID_SHA256_PREFIX = "14c36f0ef259f0b5"
REFRESH_INTERVAL_SECONDS = 30 * 60
PAGES = (
    ("bay_alerts", "BayAlerts"),
    ("money_radar", "MoneyRadar"),
    ("tech_play", "TechPlay"),
    ("nature_nearby", "BayDruid"),
)


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _is_owner_device(instances):
    return any(
        item.plugin_id == "live_radar"
        and hashlib.sha256(str(item.instance_uuid).encode("utf-8")).hexdigest()[:16]
        == OWNER_LIVE_RADAR_UUID_SHA256_PREFIX
        for item in instances
    )


def apply_aggregate_pages_playlist_migration(config):
    markers = config.get_config("runtime_migrations", default={})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    version, authoritative, playlist, manager = config.capture_detached_playlist_transaction()
    markers = authoritative.get("runtime_migrations", {})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    if not isinstance(markers, Mapping):
        raise ValueError("Aggregate pages migration state is malformed")
    instances = manager.snapshot_all_instances()
    if manager.get_playlist(PLAYLIST) is None or not _is_owner_device(instances):
        return
    present = {item.plugin_id for item in instances}
    added = []
    for plugin_id, name in PAGES:
        if plugin_id in present:
            continue
        instance = {
            "plugin_id": plugin_id,
            "name": name,
            "plugin_settings": {},
            "refresh": {"interval": REFRESH_INTERVAL_SECONDS},
        }
        if manager.add_plugin_to_playlist_snapshot(PLAYLIST, instance) is None:
            raise ValueError(f"Could not add {plugin_id} to {PLAYLIST}")
        added.append(plugin_id)
    config.commit_detached_playlist_transaction(
        expected_config_version=version,
        expected_config_data=authoritative,
        expected_playlist_config=playlist,
        playlist_manager=manager,
        config_updates={"runtime_migrations": {**_thaw(markers), MIGRATION_ID: True}},
    )
    if added:
        logger.warning(
            "Added the aggregate pages to the rotation. | playlist: %s | pages: %s",
            PLAYLIST,
            ",".join(added),
        )
