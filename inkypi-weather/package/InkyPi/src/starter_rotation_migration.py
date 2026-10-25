"""Give a fresh install a starter rotation, so the screen has something to show at once.

A new device used to start with an empty Default playlist and a blank screen until the
owner signed in and built one. The installer's starting config asks for a starter rotation
(``runtime_migrations.starter_rotation_v1`` is ``false``); on such a device with no plugin
instances yet, this adds pages that need no API key and no location (sports, Tech Pulse,
launches, art, hot lists, Wikipedia, Steam charts, magazines and the calendar), once, and
marks the request done. A config that never asked for it (an existing device, a developer
config) or already has any instance is left exactly as it is. The installer's --no-starter
option marks the request done before the first start.
"""
from collections.abc import Mapping
import logging

from aggregate_pages_migration import _thaw

logger = logging.getLogger(__name__)
MIGRATION_ID = "starter_rotation_v1"
PLAYLIST = "Default"
# (plugin_id, instance name, refresh interval in seconds): each renders keyless and location-free.
PAGES = (
    ("sports_dashboard", "Sports", 15 * 60),
    ("tech_pulse", "Tech Pulse", 60 * 60),
    ("orbital_signal", "Orbital Signal", 60 * 60),
    ("daily_art", "Daily Art", 6 * 3600),
    ("daily_pop", "Daily Pop", 60 * 60),
    ("daily_wiki_page", "Daily Wiki", 6 * 3600),
    ("steam_charts", "Steam Charts", 3 * 3600),
    ("magazine_covers", "Magazine Covers", 6 * 3600),
    ("simple_calendar", "Calendar", 60 * 60),
)


def _requested(markers):
    return isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is False


def apply_starter_rotation_migration(config):
    if not _requested(config.get_config("runtime_migrations", default={})):
        return
    version, authoritative, playlist, manager = config.capture_detached_playlist_transaction()
    markers = authoritative.get("runtime_migrations", {})
    if not _requested(markers):
        return
    if not manager.snapshot_all_instances():
        if manager.get_playlist(PLAYLIST) is None:
            manager.add_default_playlist()
        for plugin_id, name, interval in PAGES:
            instance = {"plugin_id": plugin_id, "name": name, "plugin_settings": {},
                        "refresh": {"interval": interval}}
            if manager.add_plugin_to_playlist_snapshot(PLAYLIST, instance) is None:
                raise ValueError(f"Could not add {plugin_id} to {PLAYLIST}")
        logger.warning("Fresh install: added the starter rotation. | playlist: %s | pages: %s",
                       PLAYLIST, ",".join(plugin_id for plugin_id, _name, _interval in PAGES))
    config.commit_detached_playlist_transaction(
        expected_config_version=version,
        expected_config_data=authoritative,
        expected_playlist_config=playlist,
        playlist_manager=manager,
        config_updates={"runtime_migrations": {**_thaw(markers), MIGRATION_ID: True}},
    )
