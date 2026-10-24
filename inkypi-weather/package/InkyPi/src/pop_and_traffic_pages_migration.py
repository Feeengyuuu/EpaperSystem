"""Add Daily Pop and Bay Traffic Cams to the owner's rotation once.

The owner asked for the two new pages, 今日热榜 and 湾区路况, to join the DailyDoseOfDay
playlist. Like the aggregate pages before them they are added at startup, only on the
owner's device, never twice and never beside an instance that already exists.
"""
from aggregate_pages_migration import add_pages_to_owner_rotation

MIGRATION_ID = "pop_traffic_pages_playlist_v1"
PAGES = (
    ("daily_pop", "DailyPop"),
    ("bay_traffic_cams", "BayTrafficCams"),
)
# Bilibili's chart turns over every half hour; Caltrans keeps a camera still every 15 minutes.
REFRESH_INTERVAL_SECONDS = {"daily_pop": 30 * 60, "bay_traffic_cams": 15 * 60}


def apply_pop_and_traffic_pages_migration(config):
    add_pages_to_owner_rotation(config, MIGRATION_ID, PAGES, REFRESH_INTERVAL_SECONDS,
                                "Daily Pop and Bay Traffic Cams pages")
