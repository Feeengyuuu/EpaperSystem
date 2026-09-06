"""Audited opt-in to background LIVE work without changing the display."""
import logging

from runtime.refresh_contracts import thaw_payload

logger = logging.getLogger(__name__)


def background_live_plugin(instance, current_dt, lookup):
    """Resolve only approved providers that currently request offscreen work."""
    if instance.plugin_id not in {"sports_dashboard", "box_office_top_movies"}:
        return None
    plugin = lookup(instance, require_live_refresh=True)
    hook = getattr(plugin, "wants_background_live_refresh", None)
    if not callable(hook):
        return None
    try:
        return plugin if hook(thaw_payload(instance.settings), current_dt) else None
    except Exception:
        logger.exception("Plugin '%s' background live-refresh hook failed.", instance.plugin_id)
        return None
