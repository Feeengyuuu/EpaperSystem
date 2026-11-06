"""Add 打印灵感 to the owner's rotation once.

The owner, who prints on a Bambu printer, asked for a page of what the 3D-printing community likes
and prints this week (Printables' public lists) beside the other pages in the DailyDoseOfDay
playlist, refreshing every three hours. Like the pages before it, it is added at startup, only on
the owner's device, never twice and never beside an instance that already exists.
"""
from aggregate_pages_migration import add_pages_to_owner_rotation

MIGRATION_ID = "print_inspiration_page_playlist_v1"
PAGES = (("print_inspiration", "PrintInspiration"),)
REFRESH_INTERVAL_SECONDS = {"print_inspiration": 3 * 60 * 60}


def apply_print_inspiration_page_migration(config):
    add_pages_to_owner_rotation(config, MIGRATION_ID, PAGES, REFRESH_INTERVAL_SECONDS, "打印灵感 page")
