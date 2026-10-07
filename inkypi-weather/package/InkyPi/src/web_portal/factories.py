"""Production glue between the ledger, portal, and cloud plugin worker."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from publication import (
    Delivered,
    PublicationModule,
    ReadRequest,
    Unavailable,
    WorkBudget,
)
from publication_producer import (
    CloudPublicationProducer,
    build_default_adapter_registry,
)

from .health import PublicationReadiness


DEFAULT_PUBLICATION_ROOT = "/app/data/publication"
DEFAULT_WEB_CONFIG = "/app/config/web_config.json"
DEFAULT_WEB_DATA_ROOT = "/app/data"
DEFAULT_WEB_SECRET_DIR = "/run/secrets"
_ASSET_TOKEN = re.compile(r"^([0-9a-f]{32})\.([0-9a-f]{64})$")


def _etag(value):
    return str(value or "").strip().strip('"')


def _asset_token(instance_uuid, asset_id):
    owner = hashlib.sha256(str(instance_uuid).encode("utf-8")).hexdigest()[:32]
    return f"{owner}.{asset_id}"


def _unavailable_message(reason_code):
    return {
        "not_published": "尚未生成",
        "credentials_unavailable": "缺少服务端凭据",
        "plugin_unavailable": "插件不可用",
        "resource_unavailable": "导入资源不可用",
        "render_failed": "本次生成失败，等待重试",
        "publication_failed": "本次生成失败，等待重试",
    }.get(str(reason_code or ""), "暂不可用")


class LedgerPublicationSource:
    """Translate authenticated ledger reads into the portal's tiny read seam."""

    def __init__(self, module: PublicationModule):
        self._module = module

    def list_publications(self):
        response = self._module.read(ReadRequest.catalog(authorized=True))
        if not isinstance(response, Delivered):
            return {
                "etag": "empty",
                "active_playlist": "",
                "playlists": [],
                "publications": [],
            }
        document = response.document or {}
        playlists = []
        publications = []
        seen = set()
        configured_playlist = str(document.get("activePlaylist") or "")
        window_playlist = ""
        for raw_playlist in document.get("playlists", ()):
            slug = str(raw_playlist.get("slug") or "")
            window_active = bool(raw_playlist.get("windowActive", raw_playlist.get("active")))
            if window_active and not window_playlist:
                window_playlist = slug
            window = raw_playlist.get("window") or {}
            publication_slugs = []
            for entry in raw_playlist.get("entries", ()):
                instance_uuid = str(entry.get("instanceUuid") or "")
                if not instance_uuid:
                    continue
                publication_slugs.append(instance_uuid)
                if instance_uuid in seen:
                    continue
                seen.add(instance_uuid)
                publications.append(self._from_catalog_entry(entry))
            playlists.append(
                {
                    "slug": slug,
                    "name": str(raw_playlist.get("title") or slug),
                    "active": False,
                    "window_active": window_active,
                    "window": (f"{window.get('start', '00:00')}–{window.get('end', '24:00')}"),
                    "publication_slugs": publication_slugs,
                }
            )
        playlist_slugs = {item["slug"] for item in playlists}
        active_playlist = configured_playlist if configured_playlist in playlist_slugs else window_playlist
        if not active_playlist and playlists:
            active_playlist = playlists[0]["slug"]
        for playlist in playlists:
            playlist["active"] = playlist["slug"] == active_playlist
        return {
            "etag": _etag(response.etag),
            "active_playlist": active_playlist,
            "playlists": playlists,
            "publications": publications,
        }

    def inspect_readiness(self):
        """Verify the current catalog and every displayable raster through read-only seams."""

        response = self._module.read(ReadRequest.catalog(authorized=True))
        if not isinstance(response, Delivered):
            return PublicationReadiness(0, 0, False)
        configured = 0
        displayable = 0
        assets_valid = True
        seen = set()
        for playlist in (response.document or {}).get("playlists", ()):
            for entry in playlist.get("entries", ()):
                instance_uuid = str(entry.get("instanceUuid") or "")
                if not instance_uuid or instance_uuid in seen:
                    continue
                seen.add(instance_uuid)
                configured += 1
                if not bool(entry.get("available")):
                    continue
                publication = self._module.read(
                    ReadRequest.publication(instance_uuid, authorized=True)
                )
                if not isinstance(publication, Delivered):
                    assets_valid = False
                    continue
                document = publication.document or {}
                if document.get("presentation") == "legacy_raster":
                    assets = document.get("assets")
                    if not isinstance(assets, list) or not assets:
                        assets_valid = False
                        continue
                    valid = True
                    for asset in assets:
                        asset_id = asset.get("assetId") if isinstance(asset, dict) else None
                        if not isinstance(asset_id, str) or not re.fullmatch(r"[0-9a-f]{64}", asset_id):
                            valid = False
                            break
                        delivered = self._module.read(
                            ReadRequest.asset(instance_uuid, asset_id, authorized=True)
                        )
                        if not isinstance(delivered, Delivered):
                            valid = False
                            break
                    if not valid:
                        assets_valid = False
                        continue
                displayable += 1
        return PublicationReadiness(configured, displayable, assets_valid)

    def get_publication(self, slug):
        response = self._module.read(ReadRequest.publication(str(slug), authorized=True))
        if isinstance(response, Delivered):
            return self._delivered_publication(response.document or {})
        if isinstance(response, Unavailable):
            return {
                "slug": str(slug),
                "title": str(slug),
                "plugin": "",
                "kind": "native",
                "freshness": "unavailable",
                "updated_at": None,
                "message": _unavailable_message(response.reason_code),
                "payload": {},
            }
        return None

    def get_asset(self, asset_token):
        match = _ASSET_TOKEN.fullmatch(str(asset_token or ""))
        if match is None:
            return None
        owner_digest, asset_id = match.groups()
        instance_uuid = self._resolve_asset_owner(owner_digest)
        if instance_uuid is None:
            return None
        response = self._module.read(ReadRequest.asset(instance_uuid, asset_id, authorized=True))
        if not isinstance(response, Delivered):
            return None
        return {
            "body": response.body,
            "content_type": response.content_type,
            "etag": _etag(response.etag),
        }

    def _resolve_asset_owner(self, owner_digest):
        response = self._module.read(ReadRequest.catalog(authorized=True))
        if not isinstance(response, Delivered):
            return None
        matches = []
        for playlist in (response.document or {}).get("playlists", ()):
            for entry in playlist.get("entries", ()):
                instance_uuid = str(entry.get("instanceUuid") or "")
                digest = hashlib.sha256(instance_uuid.encode("utf-8")).hexdigest()[:32]
                if digest == owner_digest and instance_uuid not in matches:
                    matches.append(instance_uuid)
        return matches[0] if len(matches) == 1 else None

    def _from_catalog_entry(self, entry):
        instance_uuid = str(entry.get("instanceUuid") or "")
        if bool(entry.get("available")):
            publication = self.get_publication(instance_uuid)
            if publication is not None:
                return publication
        return {
            "slug": instance_uuid,
            "title": str(entry.get("title") or instance_uuid),
            "plugin": str(entry.get("pluginId") or ""),
            "kind": "native",
            "freshness": "unavailable",
            "updated_at": entry.get("generatedAt"),
            "message": _unavailable_message(entry.get("reasonCode")),
            "payload": {},
        }

    @staticmethod
    def _delivered_publication(document):
        instance_uuid = str(document.get("instanceUuid") or "")
        presentation = str(document.get("presentation") or "native")
        freshness = str(document.get("freshness") or "stale")
        result = {
            "slug": instance_uuid,
            "title": str(document.get("title") or instance_uuid),
            "plugin": str(document.get("pluginId") or ""),
            "kind": "legacy_png" if presentation == "legacy_raster" else "native",
            "freshness": "fresh" if freshness == "fresh" else "stale",
            "updated_at": document.get("generatedAt"),
            "payload": document.get("payload") if isinstance(document.get("payload"), dict) else {},
        }
        assets = document.get("assets")
        if presentation == "legacy_raster" and isinstance(assets, list) and assets:
            asset_id = str(assets[0].get("assetId") or "")
            if re.fullmatch(r"[0-9a-f]{64}", asset_id):
                result["asset_id"] = _asset_token(instance_uuid, asset_id)
                result["asset_sha256"] = asset_id
        if freshness == "last_good":
            result["message"] = "显示最近一次成功发布的内容"
        return result


class PublicationWorker:
    def __init__(
        self,
        module,
        *,
        max_items=100,
        deadline_seconds=120,
        clock=None,
    ):
        self._module = module
        self._max_items = max(1, min(1000, int(max_items)))
        self._deadline_seconds = max(1, min(3600, int(deadline_seconds)))
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self):
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("worker clock must return an aware datetime")
        return self._module.publish_due(
            WorkBudget(
                max_items=self._max_items,
                deadline=now + timedelta(seconds=self._deadline_seconds),
            )
        )


def _environment_integer(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported range")
    return value


def create_reader():
    root = Path(os.environ.get("PUBLICATION_ROOT", DEFAULT_PUBLICATION_ROOT))
    timezone_name = os.environ.get("WEB_PORTAL_TIMEZONE", "UTC")
    return LedgerPublicationSource(
        PublicationModule(
            root,
            read_only=True,
            timezone_name=timezone_name,
        )
    )


def create_worker():
    config_path = Path(os.environ.get("WEB_CONFIG", DEFAULT_WEB_CONFIG))
    data_root = Path(os.environ.get("WEB_DATA_ROOT", DEFAULT_WEB_DATA_ROOT))
    publication_root = Path(os.environ.get("PUBLICATION_ROOT", DEFAULT_PUBLICATION_ROOT))
    secret_dir = Path(os.environ.get("WEB_SECRET_DIR", DEFAULT_WEB_SECRET_DIR))
    producer = CloudPublicationProducer(
        config_path.parent,
        data_dir=data_root / "producer",
        cache_dir=data_root / "cache",
        secret_dir=secret_dir,
    )
    module = PublicationModule(
        publication_root,
        producer=producer,
        adapters=build_default_adapter_registry(),
        timezone_name=producer.device_config.get_config("timezone", "UTC"),
        max_editions_per_instance=_environment_integer("WEB_EDITION_RETENTION_PER_INSTANCE", 32, 1, 512),
        max_catalog_editions=_environment_integer("WEB_CATALOG_RETENTION", 64, 1, 1024),
    )
    return PublicationWorker(
        module,
        max_items=_environment_integer("WEB_WORKER_MAX_ITEMS", 100, 1, 1000),
        deadline_seconds=_environment_integer("WEB_WORKER_DEADLINE_SECONDS", 120, 1, 3600),
    )


__all__ = [
    "LedgerPublicationSource",
    "PublicationWorker",
    "create_reader",
    "create_worker",
]
