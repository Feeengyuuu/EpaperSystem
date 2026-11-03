"""Add 万象速览 to the owner's rotation once.

The owner asked for one calm page fusing twelve small public feeds (headlines from DW 中文, the
New York Times' Chinese edition and 少数派, NetEase and Douban charts, the sun and the Webb/Hubble
picture, the US debt, California gas, fear & greed and yesterday's Wordle) beside the other pages
in the DailyDoseOfDay playlist, refreshing hourly. Like the pages before it, it is added at startup,
only on the owner's device, never twice and never beside an instance that already exists.
"""
from aggregate_pages_migration import add_pages_to_owner_rotation

MIGRATION_ID = "daily_digest_page_playlist_v1"
PAGES = (("daily_digest", "DailyDigest"),)
REFRESH_INTERVAL_SECONDS = {"daily_digest": 60 * 60}


def apply_daily_digest_page_migration(config):
    add_pages_to_owner_rotation(config, MIGRATION_ID, PAGES, REFRESH_INTERVAL_SECONDS, "万象速览 page")
