"""Fail-closed execution classes for registered plugins.

The classification is deliberately explicit.  A new plugin is kept on the
existing inline serial path until its provider, state, and rendering boundaries
have been reviewed.
"""

from __future__ import annotations

from enum import Enum


class ExecutionClass(str, Enum):
    PARALLEL_IMAGE = "parallel_image"
    NESTED_IO = "nested_io"
    SERIAL_HEAVY = "serial_heavy"
    INLINE = "inline"
    INLINE_UNKNOWN = "inline_unknown"


PARALLEL_IMAGE_PLUGINS = frozenset(
    {
        "ai_ecosystem_pulse",
        "backtothedate",
        "bambu_monitor",
        "box_office_top_movies",
        "china_box_office_top_movies",
        "comic",
        "daily_art",
        "daily_knowledge",
        "daily_wiki_page",
        "daily_word_poem",
        "dota_profile_dashboard",
        "flight_radar",
        "gcd_comic_covers",
        "image_album",
        "image_folder",
        "image_upload",
        "image_url",
        "live_radar",
        "lol_info",
        "magazine_covers",
        "natgeo_photo_of_the_day",
        "orbital_signal",
        "pixiv_r18_ranking",
        "reddit_rule34_hot",
        "steam_daily_art",
        "steam_profile_dashboard",
        "unsplash",
        "us_tv_hot_shows",
        "vehicle_status",
        "wow_profile_dashboard",
        "wpotd",
    }
)

NESTED_IO_PLUGINS = frozenset(
    {"apod", "daily_ai_news", "steam_charts", "telegram_digest"}
)

SERIAL_HEAVY_PLUGINS = frozenset(
    {
        "ai_image",
        "ai_image_multiverse",
        "ai_text",
        "calendar",
        "countdown",
        "epaper_pet",
        "github",
        "mini_weather",
        "newspaper",
        "rss",
        "screenshot",
        "sports_dashboard",
        "stocktracker",
        "tech_pulse",
        "ticketmaster_events",
        "todo_list",
        "weather",
        "year_progress",
    }
)

INLINE_PLUGINS = frozenset(
    {
        "chinese_literature_clock",
        "clock",
        "flow_progress",
        "literature_clock",
        "moon_phase",
        "simple_calendar",
        "species_radar",
    }
)


def plugin_execution_class(plugin_id: str) -> ExecutionClass:
    """Return the reviewed execution class or a serial unknown fallback."""

    if not isinstance(plugin_id, str):
        return ExecutionClass.INLINE_UNKNOWN
    key = plugin_id.strip()
    if key in PARALLEL_IMAGE_PLUGINS:
        return ExecutionClass.PARALLEL_IMAGE
    if key in NESTED_IO_PLUGINS:
        return ExecutionClass.NESTED_IO
    if key in SERIAL_HEAVY_PLUGINS:
        return ExecutionClass.SERIAL_HEAVY
    if key in INLINE_PLUGINS:
        return ExecutionClass.INLINE
    return ExecutionClass.INLINE_UNKNOWN
