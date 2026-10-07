"""Original plugin frames for the local portal showcase."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping

from publication import (
    AssetInput,
    PlaylistEntry,
    PlaylistSpec,
    ProducerBatch,
    PublicationDraft,
    TimeWindow,
)


@dataclass(frozen=True)
class RasterPreview:
    body: bytes
    width: int
    height: int
    source_updated_at: datetime


_SHOWCASE_SPECS = (
    ("local-sports-preview", "sports_dashboard", "赛事速览"),
    ("local-live-radar-preview", "live_radar", "直播雷达"),
    ("local-steam-preview", "steam_charts", "Steam 热门"),
    ("local-stock-preview", "stocktracker", "投资组合"),
    ("local-events-preview", "ticketmaster_events", "附近活动"),
)
SHOWCASE_PLUGIN_IDS = frozenset(plugin_id for _, plugin_id, _ in _SHOWCASE_SPECS)


def _raster_draft(
    instance_uuid: str,
    plugin_id: str,
    title: str,
    preview: RasterPreview,
    generated_at: datetime,
) -> PublicationDraft:
    return PublicationDraft(
        instance_uuid=instance_uuid,
        plugin_id=plugin_id,
        title=title,
        settings_revision=1,
        source_revision=1,
        generated_at=generated_at,
        fresh_until=generated_at + timedelta(hours=6),
        stale_until=generated_at + timedelta(days=7),
        snapshot={
            "rasterMetadata": {
                "alt": f"{title}原版插件画面",
                "width": preview.width,
                "height": preview.height,
                "data_mode": "cache",
                "source_label": title,
                "source_updated_at": preview.source_updated_at.isoformat(),
            }
        },
        source_assets=(AssetInput("raster", "image/png", preview.body),),
    )


def build_showcase_batch(
    weather_draft: PublicationDraft,
    generated_at: datetime,
    rasters: Mapping[str, RasterPreview] | None = None,
) -> ProducerBatch:
    """Build playlists only from exact plugin rasters supplied by the caller."""

    rasters = rasters or {}
    drafts = [weather_draft]
    for instance_uuid, plugin_id, title in _SHOWCASE_SPECS:
        preview = rasters.get(plugin_id)
        if preview is not None:
            drafts.append(_raster_draft(instance_uuid, plugin_id, title, preview, generated_at))
    by_instance = {draft.instance_uuid: draft for draft in drafts}

    def playlist(slug: str, title: str, order: int, instance_uuids: tuple[str, ...]) -> PlaylistSpec | None:
        available = tuple(instance_uuid for instance_uuid in instance_uuids if instance_uuid in by_instance)
        if not available:
            return None
        return PlaylistSpec(
            slug=slug,
            title=title,
            order=order,
            window=TimeWindow("00:00", "24:00"),
            entries=tuple(
                PlaylistEntry(
                    instance_uuid=instance_uuid,
                    title=by_instance[instance_uuid].title,
                    plugin_id=by_instance[instance_uuid].plugin_id,
                    order=entry_order,
                )
                for entry_order, instance_uuid in enumerate(available)
            ),
        )

    playlists = tuple(
        item
        for item in (
            playlist(
                "showcase-overview",
                "概览",
                0,
                (
                    "local-weather-preview",
                    "local-sports-preview",
                    "local-live-radar-preview",
                    "local-steam-preview",
                    "local-stock-preview",
                    "local-events-preview",
                ),
            ),
            playlist(
                "showcase-interests",
                "兴趣",
                1,
                ("local-live-radar-preview", "local-steam-preview"),
            ),
            playlist(
                "showcase-planning",
                "计划",
                2,
                ("local-stock-preview", "local-events-preview"),
            ),
        )
        if item is not None
    )
    return ProducerBatch(
        catalog_revision=1,
        active_playlist="showcase-overview",
        playlists=playlists,
        drafts=tuple(drafts),
    )


__all__ = ["RasterPreview", "SHOWCASE_PLUGIN_IDS", "build_showcase_batch"]
