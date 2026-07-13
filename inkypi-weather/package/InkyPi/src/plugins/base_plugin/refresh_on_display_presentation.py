"""Explicit adapter for plugins that re-render internet data after display."""

from __future__ import annotations

from plugins.base_plugin.presentation import PresentationMode, PresentationPreparation
from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance


SKIP_CACHE_IMAGE_INFO_KEY = "inkypi_skip_cache"


class RefreshOnDisplayPresentationMixin:
    """Prepare a fresh image off-display while keeping DISPLAY_CACHE provider-free."""

    def presentation_mode(self, settings):
        if self.wants_refresh_on_display(settings):
            return PresentationMode.PREPARED_BANK
        return PresentationMode.NO_CHANGE

    def reconcile_presentation_receipt(self, settings, receipt):
        """Generic re-renders have no plugin-local display cursor to advance."""
        return None

    def prepare_presentation(
        self,
        settings,
        device_config,
        *,
        request,
        resolved_theme_context,
    ):
        if self.presentation_mode(settings) is not PresentationMode.PREPARED_BANK:
            return PresentationPreparation(
                request_id=request.request_id,
                image=None,
                changed=False,
            )
        refresh_settings = dict(settings or {})
        refresh_settings["forceRefresh"] = True
        refresh_settings["_inkypiPresentationRefresh"] = True
        image = self.render_themed_image(
            refresh_settings,
            device_config,
            resolved_theme_context=resolved_theme_context,
        )
        provenance = read_source_provenance(image)
        if (
            image.info.get(SKIP_CACHE_IMAGE_INFO_KEY)
            or provenance
            not in {SourceProvenance.LIVE, SourceProvenance.FRESH_CACHE}
        ):
            source = provenance.value if provenance is not None else "non_cacheable"
            raise RuntimeError(
                f"refresh-on-display source did not produce a fresh cacheable image: {source}"
            )
        return PresentationPreparation(
            request_id=request.request_id,
            image=image,
            changed=True,
        )
