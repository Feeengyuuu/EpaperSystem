from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .contracts import (
    EditionMaterial,
    LegacyRasterAdapter,
    NativeEditionAdapter,
    PublicationDraft,
)


@dataclass(frozen=True, slots=True)
class AdaptedMaterial:
    presentation: str
    material: EditionMaterial


class AdapterResolutionError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class AdapterRegistry:
    def __init__(self) -> None:
        self._native: dict[str, NativeEditionAdapter] = {}
        self._legacy: dict[str, LegacyRasterAdapter] = {}
        self._default_legacy: LegacyRasterAdapter | None = None

    def register_native(self, plugin_id: str, adapter: NativeEditionAdapter) -> None:
        self._native[plugin_id] = adapter

    def register_legacy(self, plugin_id: str, adapter: LegacyRasterAdapter) -> None:
        self._legacy[plugin_id] = adapter

    def register_default_legacy(self, adapter: LegacyRasterAdapter) -> None:
        self._default_legacy = adapter

    def adapt(self, draft: PublicationDraft) -> AdaptedMaterial:
        native = self._native.get(draft.plugin_id)
        native_failed = False
        if native is not None:
            try:
                return AdaptedMaterial("native", native.render_native(draft))
            except Exception:
                native_failed = True
        legacy = self._legacy.get(draft.plugin_id) or self._default_legacy
        if legacy is not None:
            return AdaptedMaterial("legacy_raster", legacy.render_raster(draft))
        raise AdapterResolutionError("adapter_failed" if native_failed else "adapter_not_found")


class ContextPayloadNativeAdapter:
    def __init__(self, payload_key: str = "context") -> None:
        self._payload_key = payload_key

    def render_native(self, draft: PublicationDraft) -> EditionMaterial:
        payload = draft.snapshot.get(self._payload_key)
        if not isinstance(payload, Mapping):
            raise ValueError("native_payload_unavailable")
        return EditionMaterial(payload=dict(payload))


class RasterAssetLegacyAdapter:
    def __init__(
        self,
        asset_name: str = "raster",
        metadata_key: str = "rasterMetadata",
    ) -> None:
        self._asset_name = asset_name
        self._metadata_key = metadata_key

    def render_raster(self, draft: PublicationDraft) -> EditionMaterial:
        asset = next(
            (item for item in draft.source_assets if item.name == self._asset_name),
            None,
        )
        if asset is None:
            raise ValueError("legacy_raster_unavailable")
        metadata = draft.snapshot.get(self._metadata_key, {})
        if not isinstance(metadata, Mapping):
            raise ValueError("legacy_metadata_invalid")
        return EditionMaterial(payload=dict(metadata), assets=(asset,))
