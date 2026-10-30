"""Add AI 论文日报 to the owner's rotation once.

The owner asked for a page of the day's AI papers (Hugging Face's daily papers, ranked by the
community's upvotes) beside the other pages in the DailyDoseOfDay playlist, refreshing hourly as
the day's list fills and its votes come in. Like the pages before it, it is added at startup, only
on the owner's device, never twice and never beside an instance that already exists.
"""
from aggregate_pages_migration import add_pages_to_owner_rotation

MIGRATION_ID = "ai_papers_page_playlist_v1"
PAGES = (("ai_papers", "AiPapers"),)
REFRESH_INTERVAL_SECONDS = {"ai_papers": 60 * 60}


def apply_ai_papers_page_migration(config):
    add_pages_to_owner_rotation(config, MIGRATION_ID, PAGES, REFRESH_INTERVAL_SECONDS, "AI 论文日报 page")
