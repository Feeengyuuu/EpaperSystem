"""Add 时光书签 and 晨读 to the owner's rotation once.

The owner asked for the pages left out of the rotation to come back fused into new composite
pages: 时光书签 (the two literature clocks, the moon, the day/week/month/year bars and the days
until the next holidays) and 晨读 (the word and quote of the day, two facts and the day's comic
strip), each refreshing hourly in the DailyDoseOfDay playlist. Like the pages before them they are
added at startup, only on the owner's device, never twice and never beside an existing instance.
"""
from aggregate_pages_migration import add_pages_to_owner_rotation

MIGRATION_ID = "composite_pages_playlist_v1"
PAGES = (("time_almanac", "TimeAlmanac"), ("morning_read", "MorningRead"))
REFRESH_INTERVAL_SECONDS = {"time_almanac": 60 * 60, "morning_read": 60 * 60}


def apply_composite_pages_migration(config):
    add_pages_to_owner_rotation(config, MIGRATION_ID, PAGES, REFRESH_INTERVAL_SECONDS, "时光书签 and 晨读 pages")
