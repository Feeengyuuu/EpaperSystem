"""US Steam offers with a two-hour data lane and provider-free display redraw."""

from datetime import datetime, timezone
import time

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from runtime.long_task_executor import current_task_context
from runtime.refresh_contracts import TaskContext
from utils.http_client import get_http_client

from .media import load_covers
from .source import DealRepository


class GameDeals(BasePlugin):
    def generate_image(self, settings, device_config):
        from .render import render_page

        cached_only = bool(settings.get("_theme_render_only"))
        context = current_task_context() or TaskContext.never_cancelled(
            deadline_monotonic=time.monotonic() + 65,
        )
        context.raise_if_cancelled()
        now = datetime.now(timezone.utc)
        http = get_http_client()
        root = self.cache_dir(leaf="data", create=not cached_only)
        repository = DealRepository(root, http=http)
        force = any(str(settings.get(key, "")).lower() in {"1", "true", "yes"}
                    for key in ("forceRefresh", "force_refresh"))
        snapshot = repository.load(settings, now=now, context=context,
                                   cached_only=cached_only, force=force)
        covers = load_covers(snapshot.deals, root / "covers", http=http, context=context,
                             cached_only=cached_only)
        try:
            theme = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
            image = render_page(snapshot, covers, dimensions=self.get_dimensions(device_config),
                                theme=theme, now=now)
        finally:
            for cover in covers.values():
                cover.close()
        provenance = {"live": SourceProvenance.LIVE,
                      "fresh_cache": SourceProvenance.FRESH_CACHE,
                      "stale": SourceProvenance.STALE_CACHE,
                      "unavailable": SourceProvenance.LOCAL_FALLBACK}[snapshot.state]
        if snapshot.state in {"stale", "unavailable"}:
            image.info["inkypi_skip_cache"] = True
        context.raise_if_cancelled()
        return attach_source_provenance(image, provenance)

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        return self.render_themed_image(settings, device_config, theme_render_only=True,
                                        resolved_theme_context=resolved_theme_context)
