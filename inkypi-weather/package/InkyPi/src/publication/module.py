from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .adapters import AdapterRegistry, AdapterResolutionError
from .contracts import (
    Denied,
    Delivered,
    Missing,
    NotModified,
    PRIVATE_IMMUTABLE,
    PRIVATE_REVALIDATE,
    PublishOutcome,
    PublishReport,
    ProducerBatch,
    ProductionFailure,
    PublicationDraft,
    PublicationProducer,
    ReadRequest,
    ReadResult,
    ReadTarget,
    SCHEMA_VERSION,
    Unavailable,
    WorkBudget,
)
from .storage import LedgerStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("publication timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _etag_matches(header_value: str | None, current_etag: str) -> bool:
    if not header_value:
        return False
    for candidate in header_value.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == current_etag:
            return True
    return False


class _PublicationInputError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class PublicationModule:
    def __init__(
        self,
        root: str | Path,
        *,
        producer: PublicationProducer | None = None,
        adapters: AdapterRegistry | None = None,
        clock: Callable[[], datetime] = _utc_now,
        read_only: bool = False,
        timezone_name: str = "UTC",
        max_editions_per_instance: int = 32,
        max_catalog_editions: int = 64,
    ) -> None:
        if (
            isinstance(max_editions_per_instance, bool)
            or not isinstance(max_editions_per_instance, int)
            or not 1 <= max_editions_per_instance <= 10_000
        ):
            raise ValueError("invalid edition retention limit")
        if (
            isinstance(max_catalog_editions, bool)
            or not isinstance(max_catalog_editions, int)
            or not 1 <= max_catalog_editions <= 10_000
        ):
            raise ValueError("invalid catalog retention limit")
        try:
            self._timezone = ZoneInfo(timezone_name)
        except (TypeError, ZoneInfoNotFoundError) as exc:
            raise ValueError("invalid publication timezone") from exc
        self._store = LedgerStore(Path(root), read_only=read_only)
        self._producer = producer
        self._adapters = adapters or AdapterRegistry()
        self._clock = clock
        self._read_only = read_only
        self._max_editions_per_instance = max_editions_per_instance
        self._max_catalog_editions = max_catalog_editions

    def publish_due(self, budget: WorkBudget) -> PublishReport:
        if self._read_only:
            raise RuntimeError("publication ledger is read-only")
        if budget.max_items < 0:
            raise ValueError("work budget max_items cannot be negative")
        if budget.max_items == 0:
            return PublishReport()
        if budget.deadline is not None:
            if budget.deadline.tzinfo is None:
                raise ValueError("work budget deadline must be timezone-aware")
            if self._clock() >= budget.deadline:
                return PublishReport()
        if self._producer is None:
            return PublishReport()
        batch = self._producer.collect_due(budget)
        if batch.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported producer schema version")
        _validate_catalog(batch)
        catalog_updated = self._publish_catalog(batch)
        outcomes: list[PublishOutcome] = []
        for draft in batch.drafts[: budget.max_items]:
            try:
                _validate_draft(draft)
                adapted = self._adapters.adapt(draft)
                _validate_material(adapted.material)
                stored_assets = tuple(self._store.persist_asset(asset) for asset in adapted.material.assets)
                asset_document = [
                    {
                        "assetId": item.asset_id,
                        "mediaType": item.media_type,
                        "name": item.name,
                        "size": item.size,
                    }
                    for item in stored_assets
                ]
                document = self._document(
                    draft,
                    adapted.presentation,
                    adapted.material.payload,
                    asset_document,
                )
                body = _canonical_json(document)
            except Exception as exc:
                reason_code = (
                    exc.reason_code
                    if isinstance(exc, (AdapterResolutionError, _PublicationInputError))
                    else "adapter_failed"
                )
                self._record_failure(draft, reason_code)
                outcomes.append(
                    PublishOutcome(
                        draft.instance_uuid,
                        "unavailable",
                        reason_code=reason_code,
                    )
                )
                continue
            edition_id = hashlib.sha256(body).hexdigest()
            etag = f'"{edition_id}"'
            with self._store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    """
                    SELECT editions.edition_id, editions.settings_revision,
                        editions.source_revision
                    FROM current_editions
                    JOIN editions ON editions.edition_id = current_editions.edition_id
                    WHERE current_editions.instance_uuid = ?
                    """,
                    (draft.instance_uuid,),
                ).fetchone()
                if current is not None and current["settings_revision"] > draft.settings_revision:
                    outcomes.append(
                        PublishOutcome(
                            draft.instance_uuid,
                            "skipped",
                            reason_code="older_settings_revision",
                        )
                    )
                    continue
                if (
                    current is not None
                    and current["settings_revision"] == draft.settings_revision
                    and current["source_revision"] > draft.source_revision
                ):
                    outcomes.append(
                        PublishOutcome(
                            draft.instance_uuid,
                            "skipped",
                            reason_code="older_source_revision",
                        )
                    )
                    continue
                if (
                    current is not None
                    and current["settings_revision"] == draft.settings_revision
                    and current["source_revision"] == draft.source_revision
                ):
                    outcomes.append(
                        PublishOutcome(
                            draft.instance_uuid,
                            "skipped",
                            reason_code=(
                                "already_published" if current["edition_id"] == edition_id else "revision_conflict"
                            ),
                            edition_id=current["edition_id"],
                        )
                    )
                    continue
                locked_assets = tuple(self._store.persist_asset(asset) for asset in adapted.material.assets)
                if [item.asset_id for item in locked_assets] != [item.asset_id for item in stored_assets]:
                    raise RuntimeError("content-addressed asset identity changed")
                stored_assets = locked_assets
                connection.execute(
                    """
                    INSERT OR IGNORE INTO editions(
                        edition_id, schema_version, instance_uuid, plugin_id, title,
                        settings_revision, source_revision, generated_at, fresh_until,
                        stale_until, presentation, document, etag, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edition_id,
                        SCHEMA_VERSION,
                        draft.instance_uuid,
                        draft.plugin_id,
                        draft.title,
                        draft.settings_revision,
                        draft.source_revision,
                        _iso(draft.generated_at),
                        _iso(draft.fresh_until),
                        _iso(draft.stale_until),
                        adapted.presentation,
                        body,
                        etag,
                        _iso(self._clock()),
                    ),
                )
                for ordinal, asset in enumerate(stored_assets):
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO assets(
                            asset_id, schema_version, media_type, size, relative_path
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            asset.asset_id,
                            SCHEMA_VERSION,
                            asset.media_type,
                            asset.size,
                            asset.relative_path,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO edition_assets(
                            edition_id, ordinal, name, asset_id, media_type
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            edition_id,
                            ordinal,
                            asset.name,
                            asset.asset_id,
                            asset.media_type,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO current_editions(instance_uuid, edition_id)
                    VALUES (?, ?)
                    ON CONFLICT(instance_uuid) DO UPDATE SET edition_id = excluded.edition_id
                    """,
                    (draft.instance_uuid, edition_id),
                )
                connection.execute(
                    """
                    INSERT INTO instance_status(
                        instance_uuid, plugin_id, title, settings_revision,
                        source_revision, reason_code, attempted_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(instance_uuid) DO UPDATE SET
                        plugin_id = excluded.plugin_id,
                        title = excluded.title,
                        settings_revision = excluded.settings_revision,
                        source_revision = excluded.source_revision,
                        reason_code = NULL,
                        attempted_at = excluded.attempted_at
                    """,
                    (
                        draft.instance_uuid,
                        draft.plugin_id,
                        draft.title,
                        draft.settings_revision,
                        draft.source_revision,
                        _iso(self._clock()),
                    ),
                )
            outcomes.append(
                PublishOutcome(
                    draft.instance_uuid,
                    "published",
                    edition_id=edition_id,
                    presentation=adapted.presentation,
                )
            )
        remaining = max(0, budget.max_items - len(outcomes))
        for failure in batch.failures[:remaining]:
            reason_code = _safe_reason_code(failure.reason_code)
            self._record_failure(
                failure,
                reason_code,
                attempted_at=failure.attempted_at,
            )
            outcomes.append(
                PublishOutcome(
                    failure.instance_uuid,
                    "unavailable",
                    reason_code=reason_code,
                )
            )
        self._store.prune(
            self._max_editions_per_instance,
            self._max_catalog_editions,
        )
        return PublishReport(
            attempted=len(outcomes),
            published=sum(item.status == "published" for item in outcomes),
            skipped=sum(item.status == "skipped" for item in outcomes),
            unavailable=sum(item.status == "unavailable" for item in outcomes),
            outcomes=tuple(outcomes),
            catalog_updated=catalog_updated,
        )

    def read(self, request: ReadRequest) -> ReadResult:
        if not request.authorized:
            return Denied()
        if request.target is ReadTarget.CATALOG:
            return self._read_catalog(request)
        if request.target is ReadTarget.ASSET:
            return self._read_asset(request)
        if request.target is not ReadTarget.PUBLICATION or not request.instance_uuid:
            return Missing()
        with self._store.connect() as connection:
            if not self._instance_is_configured(connection, request.instance_uuid):
                return Missing()
            row = connection.execute(
                """
                SELECT editions.*,
                    instance_status.reason_code AS last_failure_reason,
                    instance_status.settings_revision AS status_settings_revision,
                    instance_status.source_revision AS status_source_revision
                FROM current_editions
                JOIN editions ON editions.edition_id = current_editions.edition_id
                LEFT JOIN instance_status
                    ON instance_status.instance_uuid = current_editions.instance_uuid
                WHERE current_editions.instance_uuid = ?
                """,
                (request.instance_uuid,),
            ).fetchone()
            if row is None:
                status = connection.execute(
                    "SELECT reason_code FROM instance_status WHERE instance_uuid = ?",
                    (request.instance_uuid,),
                ).fetchone()
                if status is None:
                    return Missing()
                return Unavailable(status["reason_code"] or "not_published")
        document = json.loads(bytes(row["document"]).decode("utf-8"))
        document["editionId"] = row["edition_id"]
        document["freshness"] = self._freshness(row)
        body = _canonical_json(document)
        etag = f'"{hashlib.sha256(body).hexdigest()}"'
        if _etag_matches(request.if_none_match, etag):
            return NotModified(etag, PRIVATE_REVALIDATE)
        return Delivered(
            body=body,
            content_type="application/json; charset=utf-8",
            etag=etag,
            cache_control=PRIVATE_REVALIDATE,
            document=document,
        )

    def _document(
        self,
        draft: PublicationDraft,
        presentation: str,
        payload: object,
        assets: list[dict],
    ) -> dict:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "instanceUuid": draft.instance_uuid,
            "pluginId": draft.plugin_id,
            "title": draft.title,
            "settingsRevision": draft.settings_revision,
            "sourceRevision": draft.source_revision,
            "generatedAt": _iso(draft.generated_at),
            "freshUntil": _iso(draft.fresh_until),
            "staleUntil": _iso(draft.stale_until),
            "presentation": presentation,
            "payload": payload,
            "assets": assets,
        }

    def _read_asset(self, request: ReadRequest) -> ReadResult:
        if not request.instance_uuid or not request.asset_id:
            return Missing()
        with self._store.connect() as connection:
            if not self._instance_is_configured(connection, request.instance_uuid):
                return Missing()
            row = connection.execute(
                """
                SELECT assets.*,
                    edition_assets.media_type AS edition_media_type
                FROM current_editions
                JOIN edition_assets
                    ON edition_assets.edition_id = current_editions.edition_id
                JOIN assets ON assets.asset_id = edition_assets.asset_id
                WHERE current_editions.instance_uuid = ? AND assets.asset_id = ?
                """,
                (request.instance_uuid, request.asset_id),
            ).fetchone()
        if row is None:
            return Missing()
        body = self._store.read_asset(
            row["relative_path"],
            row["asset_id"],
            row["size"],
        )
        if body is None:
            return Unavailable("asset_unavailable", cache_control="no-store")
        etag = f'"{row["asset_id"]}"'
        if _etag_matches(request.if_none_match, etag):
            return NotModified(etag, PRIVATE_IMMUTABLE)
        return Delivered(
            body=body,
            content_type=row["edition_media_type"],
            etag=etag,
            cache_control=PRIVATE_IMMUTABLE,
        )

    def _freshness(self, row) -> str:
        now = self._clock()
        if row["last_failure_reason"] is not None and (
            row["status_settings_revision"],
            row["status_source_revision"],
        ) >= (row["settings_revision"], row["source_revision"]):
            return "last_good"
        fresh_until = datetime.fromisoformat(row["fresh_until"].replace("Z", "+00:00"))
        stale_until = datetime.fromisoformat(row["stale_until"].replace("Z", "+00:00"))
        if now <= fresh_until:
            return "fresh"
        if now <= stale_until:
            return "stale"
        return "last_good"

    def _record_failure(
        self,
        draft: PublicationDraft | ProductionFailure,
        reason_code: str,
        *,
        attempted_at: datetime | None = None,
    ) -> None:
        with self._store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO instance_status(
                    instance_uuid, plugin_id, title, settings_revision,
                    source_revision, reason_code, attempted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_uuid) DO UPDATE SET
                    plugin_id = excluded.plugin_id,
                    title = excluded.title,
                    settings_revision = excluded.settings_revision,
                    source_revision = excluded.source_revision,
                    reason_code = excluded.reason_code,
                    attempted_at = excluded.attempted_at
                WHERE
                    excluded.settings_revision > instance_status.settings_revision
                    OR (
                        excluded.settings_revision = instance_status.settings_revision
                        AND excluded.source_revision >= instance_status.source_revision
                    )
                """,
                (
                    draft.instance_uuid,
                    draft.plugin_id,
                    draft.title,
                    draft.settings_revision,
                    draft.source_revision,
                    reason_code,
                    _iso(attempted_at or self._clock()),
                ),
            )

    def _publish_catalog(self, batch: ProducerBatch) -> bool:
        document = {
            "schemaVersion": SCHEMA_VERSION,
            "catalogRevision": batch.catalog_revision,
            "activePlaylist": batch.active_playlist,
            "playlists": [
                {
                    "slug": playlist.slug,
                    "title": playlist.title,
                    "order": playlist.order,
                    "window": {
                        "start": playlist.window.start,
                        "end": playlist.window.end,
                    },
                    "entries": [
                        {
                            "instanceUuid": entry.instance_uuid,
                            "title": entry.title,
                            "pluginId": entry.plugin_id,
                            "order": entry.order,
                        }
                        for entry in sorted(
                            playlist.entries,
                            key=lambda item: (item.order, item.instance_uuid),
                        )
                    ],
                }
                for playlist in sorted(
                    batch.playlists,
                    key=lambda item: (item.order, item.slug),
                )
            ],
        }
        body = _canonical_json(document)
        catalog_id = hashlib.sha256(body).hexdigest()
        with self._store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT catalogs.catalog_id, catalogs.catalog_revision
                FROM current_catalog
                JOIN catalogs ON catalogs.catalog_id = current_catalog.catalog_id
                WHERE current_catalog.singleton = 1
                """
            ).fetchone()
            if current is not None and current["catalog_revision"] >= batch.catalog_revision:
                return False
            connection.execute(
                """
                INSERT INTO catalogs(
                    catalog_id, schema_version, catalog_revision, document, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    catalog_id,
                    SCHEMA_VERSION,
                    batch.catalog_revision,
                    body,
                    _iso(self._clock()),
                ),
            )
            connection.execute(
                """
                INSERT INTO current_catalog(singleton, catalog_id) VALUES (1, ?)
                ON CONFLICT(singleton) DO UPDATE SET catalog_id = excluded.catalog_id
                """,
                (catalog_id,),
            )
            for playlist in document["playlists"]:
                for entry in playlist["entries"]:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO catalog_instances(
                            catalog_id, instance_uuid
                        ) VALUES (?, ?)
                        """,
                        (catalog_id, entry["instanceUuid"]),
                    )
        return True

    def _read_catalog(self, request: ReadRequest) -> ReadResult:
        with self._store.connect() as connection:
            catalog = connection.execute(
                """
                SELECT catalogs.document
                FROM current_catalog
                JOIN catalogs ON catalogs.catalog_id = current_catalog.catalog_id
                WHERE current_catalog.singleton = 1
                """
            ).fetchone()
            if catalog is None:
                return Missing()
            document = json.loads(bytes(catalog["document"]).decode("utf-8"))
            now = self._clock()
            if now.tzinfo is None:
                raise ValueError("publication clock must be timezone-aware")
            local_now = now.astimezone(self._timezone)
            for playlist in document["playlists"]:
                window_active = _window_is_active(
                    playlist["window"]["start"],
                    playlist["window"]["end"],
                    local_now,
                )
                playlist["windowActive"] = window_active
                playlist["active"] = window_active
                for entry in playlist["entries"]:
                    row = connection.execute(
                        """
                        SELECT editions.*,
                            instance_status.reason_code AS last_failure_reason,
                            instance_status.settings_revision AS status_settings_revision,
                            instance_status.source_revision AS status_source_revision
                        FROM current_editions
                        JOIN editions
                            ON editions.edition_id = current_editions.edition_id
                        LEFT JOIN instance_status
                            ON instance_status.instance_uuid = current_editions.instance_uuid
                        WHERE current_editions.instance_uuid = ?
                        """,
                        (entry["instanceUuid"],),
                    ).fetchone()
                    if row is not None:
                        entry["available"] = True
                        entry["freshness"] = self._freshness(row)
                        entry["editionId"] = row["edition_id"]
                        entry["presentation"] = row["presentation"]
                        entry["generatedAt"] = row["generated_at"]
                        continue
                    status = connection.execute(
                        "SELECT reason_code FROM instance_status WHERE instance_uuid = ?",
                        (entry["instanceUuid"],),
                    ).fetchone()
                    entry["available"] = False
                    entry["reasonCode"] = (
                        status["reason_code"] if status is not None and status["reason_code"] else "not_published"
                    )
        body = _canonical_json(document)
        etag = f'"{hashlib.sha256(body).hexdigest()}"'
        if _etag_matches(request.if_none_match, etag):
            return NotModified(etag, PRIVATE_REVALIDATE)
        return Delivered(
            body=body,
            content_type="application/json; charset=utf-8",
            etag=etag,
            cache_control=PRIVATE_REVALIDATE,
            document=document,
        )

    @staticmethod
    def _instance_is_configured(connection, instance_uuid: str) -> bool:
        return (
            connection.execute(
                """
                SELECT 1
                FROM current_catalog
                JOIN catalog_instances
                    ON catalog_instances.catalog_id = current_catalog.catalog_id
                WHERE current_catalog.singleton = 1
                    AND catalog_instances.instance_uuid = ?
                """,
                (instance_uuid,),
            ).fetchone()
            is not None
        )


def _window_is_active(start: str, end: str, now: datetime) -> bool:
    start_minute = _parse_clock_minute(start, allow_24=False)
    end_minute = _parse_clock_minute(end, allow_24=True)
    current_minute = now.hour * 60 + now.minute
    if start_minute == end_minute or (start_minute == 0 and end_minute == 1440):
        return True
    if start_minute < end_minute:
        return start_minute <= current_minute < end_minute
    return current_minute >= start_minute or current_minute < end_minute


def _parse_clock_minute(value: str, *, allow_24: bool) -> int:
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("playlist time must use HH:MM")
    hour, minute = (int(part) for part in parts)
    if hour == 24 and minute == 0 and allow_24:
        return 1440
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("playlist time is outside the supported range")
    return hour * 60 + minute


_SAFE_REASON_CODES = frozenset(
    {
        "adapter_failed",
        "adapter_not_found",
        "asset_unavailable",
        "credentials_unavailable",
        "deadline_exceeded",
        "invalid_snapshot",
        "invalid_material",
        "legacy_raster_unavailable",
        "native_payload_unavailable",
        "not_published",
        "plugin_unavailable",
        "render_failed",
        "resource_unavailable",
        "source_unavailable",
    }
)


def _safe_reason_code(reason_code: str) -> str:
    return reason_code if reason_code in _SAFE_REASON_CODES else "publication_failed"


_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_MEDIA_TYPE = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+\Z")


def _validate_material(material) -> None:
    if not isinstance(material.payload, dict) and not hasattr(material.payload, "items"):
        raise _PublicationInputError("invalid_material")
    names: set[str] = set()
    for asset in material.assets:
        if not isinstance(asset.name, str) or _ASSET_NAME.fullmatch(asset.name) is None:
            raise _PublicationInputError("invalid_material")
        if asset.name in names:
            raise _PublicationInputError("invalid_material")
        names.add(asset.name)
        if (
            not isinstance(asset.media_type, str)
            or _MEDIA_TYPE.fullmatch(asset.media_type) is None
            or not isinstance(asset.body, bytes)
        ):
            raise _PublicationInputError("invalid_material")


def _validate_draft(draft: PublicationDraft) -> None:
    if (
        not isinstance(draft.instance_uuid, str)
        or not draft.instance_uuid
        or len(draft.instance_uuid) > 200
        or not isinstance(draft.plugin_id, str)
        or not draft.plugin_id
        or len(draft.plugin_id) > 200
        or not isinstance(draft.title, str)
        or len(draft.title) > 500
        or isinstance(draft.settings_revision, bool)
        or not isinstance(draft.settings_revision, int)
        or draft.settings_revision < 0
        or isinstance(draft.source_revision, bool)
        or not isinstance(draft.source_revision, int)
        or draft.source_revision < 0
        or not isinstance(draft.snapshot, Mapping)
    ):
        raise _PublicationInputError("invalid_snapshot")
    timestamps = (draft.generated_at, draft.fresh_until, draft.stale_until)
    if any(not isinstance(value, datetime) or value.tzinfo is None for value in timestamps):
        raise _PublicationInputError("invalid_snapshot")
    if not draft.generated_at <= draft.fresh_until <= draft.stale_until:
        raise _PublicationInputError("invalid_snapshot")


def _validate_catalog(batch: ProducerBatch) -> None:
    try:
        if (
            isinstance(batch.catalog_revision, bool)
            or not isinstance(batch.catalog_revision, int)
            or batch.catalog_revision < 0
        ):
            raise ValueError
        playlist_slugs: set[str] = set()
        for playlist in batch.playlists:
            if (
                not _safe_catalog_text(playlist.slug, 128, allow_empty=False)
                or "/" in playlist.slug
                or playlist.slug in playlist_slugs
                or not _safe_catalog_text(playlist.title, 500, allow_empty=False)
                or isinstance(playlist.order, bool)
                or not isinstance(playlist.order, int)
            ):
                raise ValueError
            playlist_slugs.add(playlist.slug)
            _parse_clock_minute(playlist.window.start, allow_24=False)
            _parse_clock_minute(playlist.window.end, allow_24=True)
            instance_uuids: set[str] = set()
            for entry in playlist.entries:
                if (
                    not _safe_catalog_text(entry.instance_uuid, 200, allow_empty=False)
                    or entry.instance_uuid in instance_uuids
                    or not _safe_catalog_text(entry.title, 500, allow_empty=True)
                    or not _safe_catalog_text(entry.plugin_id, 200, allow_empty=False)
                    or isinstance(entry.order, bool)
                    or not isinstance(entry.order, int)
                ):
                    raise ValueError
                instance_uuids.add(entry.instance_uuid)
        if batch.active_playlist is not None and batch.active_playlist not in playlist_slugs:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid publication catalog") from None


def _safe_catalog_text(value: object, maximum: int, *, allow_empty: bool) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= maximum
        and (allow_empty or bool(value))
        and not any(ord(character) < 32 for character in value)
    )
