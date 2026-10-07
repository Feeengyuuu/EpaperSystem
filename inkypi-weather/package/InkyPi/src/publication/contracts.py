from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable


SCHEMA_VERSION = 1
PRIVATE_REVALIDATE = "private, max-age=0, must-revalidate"
PRIVATE_IMMUTABLE = "private, max-age=31536000, immutable"


class ReadTarget(str, Enum):
    CATALOG = "catalog"
    PUBLICATION = "publication"
    ASSET = "asset"


@dataclass(frozen=True, slots=True)
class ReadRequest:
    target: ReadTarget
    authorized: bool = False
    instance_uuid: str | None = None
    asset_id: str | None = None
    if_none_match: str | None = None

    @classmethod
    def catalog(
        cls,
        *,
        authorized: bool = False,
        if_none_match: str | None = None,
    ) -> "ReadRequest":
        return cls(ReadTarget.CATALOG, authorized=authorized, if_none_match=if_none_match)

    @classmethod
    def publication(
        cls,
        instance_uuid: str,
        *,
        authorized: bool = False,
        if_none_match: str | None = None,
    ) -> "ReadRequest":
        return cls(
            ReadTarget.PUBLICATION,
            authorized=authorized,
            instance_uuid=instance_uuid,
            if_none_match=if_none_match,
        )

    @classmethod
    def asset(
        cls,
        instance_uuid: str,
        asset_id: str,
        *,
        authorized: bool = False,
        if_none_match: str | None = None,
    ) -> "ReadRequest":
        return cls(
            ReadTarget.ASSET,
            authorized=authorized,
            instance_uuid=instance_uuid,
            asset_id=asset_id,
            if_none_match=if_none_match,
        )


@dataclass(frozen=True, slots=True)
class Delivered:
    body: bytes
    content_type: str
    etag: str
    cache_control: str
    document: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class NotModified:
    etag: str
    cache_control: str


@dataclass(frozen=True, slots=True)
class Denied:
    reason_code: str = "authentication_required"
    cache_control: str = "no-store"


@dataclass(frozen=True, slots=True)
class Missing:
    reason_code: str = "not_found"
    cache_control: str = "no-store"


@dataclass(frozen=True, slots=True)
class Unavailable:
    reason_code: str
    cache_control: str = PRIVATE_REVALIDATE


ReadResult = Delivered | NotModified | Denied | Missing | Unavailable


@dataclass(frozen=True, slots=True)
class AssetInput:
    name: str
    media_type: str
    body: bytes


@dataclass(frozen=True, slots=True)
class EditionMaterial:
    payload: Mapping[str, Any]
    assets: tuple[AssetInput, ...] = ()


@dataclass(frozen=True, slots=True)
class PublicationDraft:
    instance_uuid: str
    plugin_id: str
    title: str
    settings_revision: int
    source_revision: int
    generated_at: datetime
    fresh_until: datetime
    stale_until: datetime
    snapshot: Mapping[str, Any]
    source_assets: tuple[AssetInput, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductionFailure:
    instance_uuid: str
    plugin_id: str
    title: str
    settings_revision: int
    source_revision: int
    attempted_at: datetime
    reason_code: str


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: str = "00:00"
    end: str = "24:00"


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    instance_uuid: str
    title: str
    plugin_id: str
    order: int = 0


@dataclass(frozen=True, slots=True)
class PlaylistSpec:
    slug: str
    title: str
    order: int = 0
    window: TimeWindow = field(default_factory=TimeWindow)
    entries: tuple[PlaylistEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class ProducerBatch:
    catalog_revision: int
    drafts: tuple[PublicationDraft, ...] = ()
    playlists: tuple[PlaylistSpec, ...] = ()
    failures: tuple[ProductionFailure, ...] = ()
    schema_version: int = SCHEMA_VERSION
    active_playlist: str | None = None


@dataclass(frozen=True, slots=True)
class WorkBudget:
    max_items: int = 100
    deadline: datetime | None = None


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    instance_uuid: str
    status: str
    reason_code: str | None = None
    edition_id: str | None = None
    presentation: str | None = None


@dataclass(frozen=True, slots=True)
class PublishReport:
    attempted: int = 0
    published: int = 0
    skipped: int = 0
    unavailable: int = 0
    outcomes: tuple[PublishOutcome, ...] = ()
    catalog_updated: bool = False


@runtime_checkable
class NativeEditionAdapter(Protocol):
    def render_native(self, draft: PublicationDraft) -> EditionMaterial: ...


@runtime_checkable
class LegacyRasterAdapter(Protocol):
    def render_raster(self, draft: PublicationDraft) -> EditionMaterial: ...


@runtime_checkable
class PublicationProducer(Protocol):
    def collect_due(self, budget: WorkBudget) -> ProducerBatch: ...
