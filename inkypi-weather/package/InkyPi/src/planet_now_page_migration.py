"""Add 环球此刻 (Planet Now) to the owner's rotation once.

The owner asked for the new page (the sky over the Bay, California's grid, America's
searches and space) to join the DailyDoseOfDay playlist. Like the pages before it, it is
added at startup, only on the owner's device, never twice and never beside an instance
that already exists.
"""
from aggregate_pages_migration import add_pages_to_owner_rotation

MIGRATION_ID = "planet_now_page_playlist_v1"
PAGES = (("planet_now", "PlanetNow"),)
REFRESH_INTERVAL_SECONDS = {"planet_now": 30 * 60}


def apply_planet_now_page_migration(config):
    add_pages_to_owner_rotation(config, MIGRATION_ID, PAGES, REFRESH_INTERVAL_SECONDS, "Planet Now page")
