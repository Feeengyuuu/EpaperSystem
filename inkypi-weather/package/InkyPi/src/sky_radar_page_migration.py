"""Add Sky Radar to the owner's rotation once.

The owner asked for the redesigned Sky Radar (the airport board, the charted scope and the
flight strips) to join the DailyDoseOfDay playlist. Like the pages before it, it is added at
startup, only on the owner's device, never twice and never beside an instance that already
exists.
"""
from aggregate_pages_migration import add_pages_to_owner_rotation

MIGRATION_ID = "sky_radar_page_playlist_v1"
PAGES = (("flight_radar", "SkyRadar"),)
REFRESH_INTERVAL_SECONDS = {"flight_radar": 10 * 60}


def apply_sky_radar_page_migration(config):
    add_pages_to_owner_rotation(config, MIGRATION_ID, PAGES, REFRESH_INTERVAL_SECONDS, "Sky Radar page")
