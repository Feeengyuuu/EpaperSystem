"""Apple's optional schedule uses its own durable state and the shared poll gate."""
from datetime import datetime, timezone

from plugins.simple_calendar.apple_event_sources import FAMILY, fetch_source, valid_source_url
from plugins.simple_calendar.game_events import GameEventProvider, _instant


class AppleEventProvider(GameEventProvider):
    """Reuse calendar synchronization without modifying saved game selections."""

    enabled_setting = "showAppleEvents"
    state_subdir = "apple_events"
    series = {FAMILY: "苹果发布会"}
    source_families = {"apple": (FAMILY,)}
    default_timezones = {FAMILY: "America/Los_Angeles"}
    fetch_source = staticmethod(fetch_source)
    valid_source_url = staticmethod(valid_source_url)
    event_label = "APPLE"
    event_color = (45, 80, 121)

    @staticmethod
    def selected_series(settings):
        return [FAMILY]

    @staticmethod
    def selected_sources(settings):
        return ["apple"]

    def _merge_source(self, state, source_id, announcements, now):
        # Calendar entries are identified by UID; Newsroom stories have none and
        # are identified by their own URL.
        previous = {item["external_id"] or item["source_url"]: item for item in state["sources"][source_id].get("items", [])}
        unique = {}
        for item in sorted(announcements, key=lambda row: (
            row.revision, row.published_at or datetime.min.replace(tzinfo=timezone.utc), row.status == "cancelled"
        )):
            key = item.external_id or item.source_url
            old = previous.get(key, {})
            old_stamp = _instant(old.get("published_at"))
            if old.get("revision", 0) > item.revision or (
                old.get("revision", 0) == item.revision and old_stamp and item.published_at
                and old_stamp > item.published_at
            ):
                continue
            unique[key] = item
        super()._merge_source(state, source_id, list(unique.values()), now)
