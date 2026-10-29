"""Add 全网热搜 and 世界赔率榜 to the owner's rotation once.

The owner asked for the two new pages (the Chinese hot lists side by side, and the prediction
markets' odds) to join the DailyDoseOfDay playlist, each refreshing as often as its sources
turn over: the hot lists every 20 minutes, the odds every 30. Like the pages before them, they
are added at startup, only on the owner's device, never twice and never beside an instance that
already exists.
"""
from aggregate_pages_migration import add_pages_to_owner_rotation

MIGRATION_ID = "hot_odds_pages_playlist_v1"
PAGES = (("cn_hot_search", "CnHotSearch"), ("world_odds", "WorldOdds"))
REFRESH_INTERVAL_SECONDS = {"cn_hot_search": 20 * 60, "world_odds": 30 * 60}


def apply_hot_and_odds_pages_migration(config):
    add_pages_to_owner_rotation(config, MIGRATION_ID, PAGES, REFRESH_INTERVAL_SECONDS,
                                "全网热搜 and 世界赔率榜 pages")
