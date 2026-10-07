from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
from pathlib import Path

import pytest

import publication.storage as publication_storage

from publication import (
    AdapterRegistry,
    AssetInput,
    ContextPayloadNativeAdapter,
    Denied,
    Delivered,
    EditionMaterial,
    LegacyRasterAdapter,
    Missing,
    NotModified,
    PlaylistEntry,
    PlaylistSpec,
    NativeEditionAdapter,
    ProducerBatch,
    ProductionFailure,
    PublicationDraft,
    PublicationModule,
    RasterAssetLegacyAdapter,
    ReadRequest,
    TimeWindow,
    Unavailable,
    WorkBudget,
)


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


class OneNativeAdapter(NativeEditionAdapter):
    def render_native(self, draft: PublicationDraft) -> EditionMaterial:
        return EditionMaterial(payload={"temperature": draft.snapshot["temperature"]})


class OneRasterAdapter(LegacyRasterAdapter):
    def render_raster(self, draft: PublicationDraft) -> EditionMaterial:
        return EditionMaterial(
            payload={"width": 800, "height": 480},
            assets=(
                AssetInput(
                    name="display",
                    media_type="image/png",
                    body=b"valid-enough-png-payload",
                ),
            ),
        )


class FailingNativeAdapter(NativeEditionAdapter):
    def render_native(self, draft: PublicationDraft) -> EditionMaterial:
        raise RuntimeError("provider exploded with token=do-not-leak")


class DuplicateAssetNativeAdapter(NativeEditionAdapter):
    def render_native(self, draft: PublicationDraft) -> EditionMaterial:
        return EditionMaterial(
            payload={"temperature": 99},
            assets=(
                AssetInput("duplicate", "image/png", b"first"),
                AssetInput("duplicate", "image/png", b"second"),
            ),
        )


class BarrierNativeAdapter(NativeEditionAdapter):
    def __init__(self, barrier: threading.Barrier):
        self.barrier = barrier

    def render_native(self, draft: PublicationDraft) -> EditionMaterial:
        self.barrier.wait(timeout=5)
        return EditionMaterial(payload=dict(draft.snapshot))


class MediaTypedRasterAdapter(LegacyRasterAdapter):
    def __init__(self, media_type: str):
        self.media_type = media_type

    def render_raster(self, draft: PublicationDraft) -> EditionMaterial:
        return EditionMaterial(
            payload={},
            assets=(AssetInput("display", self.media_type, b"same-body"),),
        )


class RevisionRasterAdapter(LegacyRasterAdapter):
    def render_raster(self, draft: PublicationDraft) -> EditionMaterial:
        return EditionMaterial(
            payload={"revision": draft.source_revision},
            assets=(
                AssetInput(
                    "display",
                    "image/png",
                    f"png-revision-{draft.source_revision}".encode(),
                ),
            ),
        )


class StaticProducer:
    def __init__(
        self,
        *drafts: PublicationDraft,
        playlists: tuple[PlaylistSpec, ...] | None = None,
        failures: tuple[ProductionFailure, ...] = (),
        catalog_revision: int = 1,
        schema_version: int = 1,
        active_playlist: str | None = None,
    ):
        self.drafts = drafts
        self.playlists = (
            playlists
            if playlists is not None
            else (
                PlaylistSpec(
                    "default",
                    "Default",
                    entries=tuple(
                        PlaylistEntry(
                            item.instance_uuid,
                            item.title,
                            item.plugin_id,
                            order=index,
                        )
                        for index, item in enumerate(drafts)
                    ),
                ),
            )
        )
        self.failures = failures
        self.catalog_revision = catalog_revision
        self.schema_version = schema_version
        self.active_playlist = active_playlist

    def collect_due(self, budget: WorkBudget) -> ProducerBatch:
        return ProducerBatch(
            catalog_revision=self.catalog_revision,
            drafts=self.drafts,
            playlists=self.playlists,
            failures=self.failures,
            schema_version=self.schema_version,
            active_playlist=self.active_playlist,
        )


class CountingProducer(StaticProducer):
    def __init__(self, *drafts: PublicationDraft):
        super().__init__(*drafts)
        self.calls = 0

    def collect_due(self, budget: WorkBudget) -> ProducerBatch:
        self.calls += 1
        return super().collect_due(budget)


def _draft(**changes) -> PublicationDraft:
    values = {
        "instance_uuid": "weather-home",
        "plugin_id": "weather",
        "title": "Home weather",
        "settings_revision": 3,
        "source_revision": 7,
        "generated_at": NOW,
        "fresh_until": NOW + timedelta(minutes=15),
        "stale_until": NOW + timedelta(hours=2),
        "snapshot": {"temperature": 72},
    }
    values.update(changes)
    return PublicationDraft(**values)


def _module(tmp_path, *drafts, now=NOW):
    registry = AdapterRegistry()
    registry.register_native("weather", OneNativeAdapter())
    return PublicationModule(
        tmp_path,
        producer=StaticProducer(*drafts),
        adapters=registry,
        clock=lambda: now,
    )


def test_worker_publishes_native_edition_that_reader_can_deliver(tmp_path):
    module = _module(tmp_path, _draft())

    report = module.publish_due(WorkBudget(max_items=1))
    response = module.read(ReadRequest.publication("weather-home", authorized=True))

    assert report.published == 1
    assert isinstance(response, Delivered)
    assert response.document["schemaVersion"] == 1
    assert response.document["editionId"] == report.outcomes[0].edition_id
    assert response.document["instanceUuid"] == "weather-home"
    assert response.document["presentation"] == "native"
    assert response.document["freshness"] == "fresh"
    assert response.document["payload"] == {"temperature": 72}
    assert response.cache_control == "private, max-age=0, must-revalidate"


def test_older_settings_revision_cannot_replace_current_edition(tmp_path):
    current = _module(tmp_path, _draft()).publish_due(WorkBudget())
    older = _module(
        tmp_path,
        _draft(
            settings_revision=2,
            source_revision=99,
            snapshot={"temperature": 41},
        ),
    ).publish_due(WorkBudget())
    response = _module(tmp_path).read(ReadRequest.publication("weather-home", authorized=True))

    assert current.published == 1
    assert older.published == 0
    assert older.skipped == 1
    assert older.outcomes[0].reason_code == "older_settings_revision"
    assert isinstance(response, Delivered)
    assert response.document["settingsRevision"] == 3
    assert response.document["payload"] == {"temperature": 72}


def test_out_of_order_source_revision_cannot_replace_current_edition(tmp_path):
    _module(tmp_path, _draft(source_revision=8)).publish_due(WorkBudget())

    report = _module(
        tmp_path,
        _draft(source_revision=7, snapshot={"temperature": 39}),
    ).publish_due(WorkBudget())
    response = _module(tmp_path).read(ReadRequest.publication("weather-home", authorized=True))

    assert report.published == 0
    assert report.skipped == 1
    assert report.outcomes[0].reason_code == "older_source_revision"
    assert isinstance(response, Delivered)
    assert response.document["sourceRevision"] == 8
    assert response.document["payload"] == {"temperature": 72}


def test_legacy_asset_is_content_addressed_and_read_through_current_instance(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", OneRasterAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(
                instance_uuid="clock-one",
                plugin_id="legacy-clock",
                title="Clock",
            )
        ),
        adapters=registry,
        clock=lambda: NOW,
    )

    report = module.publish_due(WorkBudget())
    publication = module.read(ReadRequest.publication("clock-one", authorized=True))
    assert isinstance(publication, Delivered)
    asset_id = publication.document["assets"][0]["assetId"]
    asset = module.read(ReadRequest.asset("clock-one", asset_id, authorized=True))

    assert report.published == 1
    assert publication.document["presentation"] == "legacy_raster"
    assert publication.document["assets"] == [
        {
            "assetId": asset_id,
            "mediaType": "image/png",
            "name": "display",
            "size": len(b"valid-enough-png-payload"),
        }
    ]
    assert isinstance(asset, Delivered)
    assert asset.body == b"valid-enough-png-payload"
    assert asset.content_type == "image/png"
    assert asset.etag == f'"{asset_id}"'
    assert asset.cache_control == "private, max-age=31536000, immutable"
    not_modified = module.read(
        ReadRequest.asset(
            "clock-one",
            asset_id,
            authorized=True,
            if_none_match=asset.etag,
        )
    )
    assert isinstance(not_modified, NotModified)
    assert not_modified.cache_control == asset.cache_control


def test_asset_read_uses_one_bounded_descriptor_instead_of_reopening_path(
    tmp_path,
    monkeypatch,
):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", OneRasterAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(instance_uuid="clock-one", plugin_id="legacy-clock", title="Clock")
        ),
        adapters=registry,
        clock=lambda: NOW,
    )
    assert module.publish_due(WorkBudget()).published == 1
    publication = module.read(ReadRequest.publication("clock-one", authorized=True))
    assert isinstance(publication, Delivered)
    asset_id = publication.document["assets"][0]["assetId"]

    def reject_path_read(*_args, **_kwargs):
        raise AssertionError("Path.read_bytes would reopen an already checked CAS path")

    monkeypatch.setattr(Path, "read_bytes", reject_path_read)

    asset = module.read(ReadRequest.asset("clock-one", asset_id, authorized=True))

    assert isinstance(asset, Delivered)
    assert asset.body == b"valid-enough-png-payload"


def test_asset_read_rejects_a_database_path_outside_the_content_addressed_root(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", OneRasterAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(
                instance_uuid="clock-one",
                plugin_id="legacy-clock",
                title="Clock",
            )
        ),
        adapters=registry,
        clock=lambda: NOW,
    )
    assert module.publish_due(WorkBudget()).published == 1
    publication = module.read(ReadRequest.publication("clock-one", authorized=True))
    assert isinstance(publication, Delivered)
    asset_id = publication.document["assets"][0]["assetId"]
    (tmp_path / "outside.bin").write_bytes(b"valid-enough-png-payload")
    with module._store.connect() as connection:
        connection.execute(
            "UPDATE assets SET relative_path = ? WHERE asset_id = ?",
            ("../../outside.bin", asset_id),
        )

    result = module.read(ReadRequest.asset("clock-one", asset_id, authorized=True))

    assert isinstance(result, Unavailable)
    assert result.reason_code == "asset_unavailable"


def test_asset_directory_fsync_failure_never_promotes_edition(tmp_path, monkeypatch):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", OneRasterAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(
                instance_uuid="clock-one",
                plugin_id="legacy-clock",
                title="Clock",
            )
        ),
        adapters=registry,
        clock=lambda: NOW,
    )

    def fail_shard_sync(path):
        if path.parent == module._store.asset_root:
            raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(publication_storage, "fsync_directory", fail_shard_sync, raising=False)

    report = module.publish_due(WorkBudget())
    publication = module.read(ReadRequest.publication("clock-one", authorized=True))

    assert report.published == 0
    assert report.unavailable == 1
    assert isinstance(publication, Unavailable)


def test_failed_new_publication_keeps_last_good_without_leaking_error(tmp_path):
    _module(tmp_path, _draft()).publish_due(WorkBudget())
    registry = AdapterRegistry()
    registry.register_native("weather", FailingNativeAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(source_revision=8, snapshot={"temperature": 100})),
        adapters=registry,
        clock=lambda: NOW,
    )

    report = module.publish_due(WorkBudget())
    response = module.read(ReadRequest.publication("weather-home", authorized=True))

    assert report.published == 0
    assert report.unavailable == 1
    assert report.outcomes[0].reason_code == "adapter_failed"
    assert "token" not in repr(report)
    assert isinstance(response, Delivered)
    assert response.document["sourceRevision"] == 7
    assert response.document["freshness"] == "last_good"
    assert response.document["payload"] == {"temperature": 72}


def test_matching_publication_etag_returns_private_not_modified(tmp_path):
    module = _module(tmp_path, _draft())
    module.publish_due(WorkBudget())
    delivered = module.read(ReadRequest.publication("weather-home", authorized=True))
    assert isinstance(delivered, Delivered)

    response = module.read(
        ReadRequest.publication(
            "weather-home",
            authorized=True,
            if_none_match=delivered.etag,
        )
    )

    assert isinstance(response, NotModified)
    assert response.etag == delivered.etag
    assert response.cache_control == "private, max-age=0, must-revalidate"


def test_catalog_preserves_playlist_and_entry_order_and_marks_active_window(tmp_path):
    playlists = (
        PlaylistSpec(
            slug="later",
            title="Later",
            order=20,
            window=TimeWindow("23:00", "02:00"),
            entries=(
                PlaylistEntry("never-published", "Missing", "weather", 30),
                PlaylistEntry("weather-work", "Work", "weather", 10),
            ),
        ),
        PlaylistSpec(
            slug="current",
            title="Current",
            order=10,
            window=TimeWindow("11:00", "13:00"),
            entries=(PlaylistEntry("weather-home", "Home", "weather", 20),),
        ),
    )
    registry = AdapterRegistry()
    registry.register_native("weather", OneNativeAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(),
            _draft(instance_uuid="weather-work", snapshot={"temperature": 68}),
            playlists=playlists,
            catalog_revision=4,
        ),
        adapters=registry,
        clock=lambda: NOW,
    )

    module.publish_due(WorkBudget())
    response = module.read(ReadRequest.catalog(authorized=True))

    assert isinstance(response, Delivered)
    assert response.document["schemaVersion"] == 1
    assert response.document["catalogRevision"] == 4
    assert [item["slug"] for item in response.document["playlists"]] == [
        "current",
        "later",
    ]
    assert [item["active"] for item in response.document["playlists"]] == [
        True,
        False,
    ]
    later_entries = response.document["playlists"][1]["entries"]
    assert [item["instanceUuid"] for item in later_entries] == [
        "weather-work",
        "never-published",
    ]
    assert later_entries[0]["available"] is True
    assert later_entries[0]["freshness"] == "fresh"
    assert later_entries[1]["available"] is False
    assert later_entries[1]["reasonCode"] == "not_published"


def test_native_adapter_failure_falls_back_to_default_legacy_adapter(tmp_path):
    registry = AdapterRegistry()
    registry.register_native("weather", FailingNativeAdapter())
    registry.register_default_legacy(OneRasterAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft()),
        adapters=registry,
        clock=lambda: NOW,
    )

    report = module.publish_due(WorkBudget())
    response = module.read(ReadRequest.publication("weather-home", authorized=True))

    assert report.published == 1
    assert report.unavailable == 0
    assert report.outcomes[0].presentation == "legacy_raster"
    assert isinstance(response, Delivered)
    assert response.document["presentation"] == "legacy_raster"


def test_producer_failure_isolated_from_catalog_and_exposes_only_safe_reason(tmp_path):
    playlists = (
        PlaylistSpec(
            "current",
            "Current",
            entries=(PlaylistEntry("broken", "Broken", "weather"),),
        ),
    )
    failure = ProductionFailure(
        instance_uuid="broken",
        plugin_id="weather",
        title="Broken",
        settings_revision=1,
        source_revision=1,
        attempted_at=NOW,
        reason_code="RuntimeError token=do-not-leak",
    )
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            playlists=playlists,
            failures=(failure,),
            catalog_revision=2,
        ),
        clock=lambda: NOW,
    )

    report = module.publish_due(WorkBudget())
    publication = module.read(ReadRequest.publication("broken", authorized=True))
    catalog = module.read(ReadRequest.catalog(authorized=True))

    assert report.catalog_updated is True
    assert report.unavailable == 1
    assert report.outcomes[0].reason_code == "publication_failed"
    assert "token" not in repr(report)
    assert isinstance(publication, Unavailable)
    assert publication.reason_code == "publication_failed"
    assert isinstance(catalog, Delivered)
    assert catalog.document["playlists"][0]["entries"][0]["reasonCode"] == ("publication_failed")


def test_same_revisions_are_idempotent_and_conflicting_body_cannot_replace(tmp_path):
    module = _module(tmp_path, _draft())
    first = module.publish_due(WorkBudget())
    duplicate = _module(tmp_path, _draft()).publish_due(WorkBudget())
    conflict = _module(
        tmp_path,
        _draft(snapshot={"temperature": 99}),
    ).publish_due(WorkBudget())
    response = _module(tmp_path).read(ReadRequest.publication("weather-home", authorized=True))

    assert first.published == 1
    assert duplicate.published == 0
    assert duplicate.skipped == 1
    assert duplicate.outcomes[0].reason_code == "already_published"
    assert conflict.published == 0
    assert conflict.skipped == 1
    assert conflict.outcomes[0].reason_code == "revision_conflict"
    assert isinstance(response, Delivered)
    assert response.document["payload"] == {"temperature": 72}


def test_expired_work_budget_does_not_invoke_producer(tmp_path):
    producer = CountingProducer(_draft())
    module = PublicationModule(tmp_path, producer=producer, clock=lambda: NOW)

    report = module.publish_due(WorkBudget(max_items=1, deadline=NOW - timedelta(microseconds=1)))

    assert producer.calls == 0
    assert report.attempted == 0
    assert report.published == 0


def test_missing_adapter_records_safe_unavailable_reason(tmp_path):
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(plugin_id="unknown-plugin")),
        clock=lambda: NOW,
    )

    report = module.publish_due(WorkBudget())
    response = module.read(ReadRequest.publication("weather-home", authorized=True))

    assert report.unavailable == 1
    assert report.outcomes[0].reason_code == "adapter_not_found"
    assert isinstance(response, Unavailable)
    assert response.reason_code == "adapter_not_found"


def test_invalid_adapter_material_isolated_and_does_not_replace_last_good(tmp_path):
    _module(tmp_path, _draft()).publish_due(WorkBudget())
    registry = AdapterRegistry()
    registry.register_native("weather", DuplicateAssetNativeAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(source_revision=8)),
        adapters=registry,
        clock=lambda: NOW,
    )

    report = module.publish_due(WorkBudget())
    response = module.read(ReadRequest.publication("weather-home", authorized=True))

    assert report.unavailable == 1
    assert report.outcomes[0].reason_code == "invalid_material"
    assert isinstance(response, Delivered)
    assert response.document["sourceRevision"] == 7
    assert response.document["freshness"] == "last_good"


def test_unsupported_batch_schema_fails_before_mutating_current_edition(tmp_path):
    _module(tmp_path, _draft()).publish_due(WorkBudget())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(source_revision=8, snapshot={"temperature": 99}),
            catalog_revision=2,
            schema_version=2,
        ),
        adapters=AdapterRegistry(),
        clock=lambda: NOW,
    )

    try:
        module.publish_due(WorkBudget())
    except ValueError as error:
        assert str(error) == "unsupported producer schema version"
    else:
        raise AssertionError("unsupported schema must fail closed")

    response = _module(tmp_path).read(ReadRequest.publication("weather-home", authorized=True))
    assert isinstance(response, Delivered)
    assert response.document["sourceRevision"] == 7


def test_invalid_draft_is_safe_failure_and_cannot_replace_last_good(tmp_path):
    _module(tmp_path, _draft()).publish_due(WorkBudget())
    invalid = _draft(
        source_revision=8,
        generated_at=NOW.replace(tzinfo=None),
        snapshot={"temperature": 99},
    )
    module = _module(tmp_path, invalid)

    report = module.publish_due(WorkBudget())
    response = module.read(ReadRequest.publication("weather-home", authorized=True))

    assert report.unavailable == 1
    assert report.outcomes[0].reason_code == "invalid_snapshot"
    assert isinstance(response, Delivered)
    assert response.document["sourceRevision"] == 7


def test_invalid_catalog_window_cannot_replace_last_valid_catalog(tmp_path):
    valid = PlaylistSpec("valid", "Valid", window=TimeWindow("00:00", "24:00"))
    PublicationModule(
        tmp_path,
        producer=StaticProducer(playlists=(valid,), catalog_revision=1),
        clock=lambda: NOW,
    ).publish_due(WorkBudget())
    invalid = PlaylistSpec("invalid", "Invalid", window=TimeWindow("99:00", "24:00"))
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(playlists=(invalid,), catalog_revision=2),
        clock=lambda: NOW,
    )

    try:
        module.publish_due(WorkBudget())
    except ValueError as error:
        assert str(error) == "invalid publication catalog"
    else:
        raise AssertionError("invalid catalog must fail closed")

    response = module.read(ReadRequest.catalog(authorized=True))
    assert isinstance(response, Delivered)
    assert response.document["catalogRevision"] == 1
    assert response.document["playlists"][0]["slug"] == "valid"


def test_read_path_never_invokes_injected_producer(tmp_path):
    _module(tmp_path, _draft()).publish_due(WorkBudget())
    producer = CountingProducer(_draft(source_revision=999))
    reader = PublicationModule(tmp_path, producer=producer, clock=lambda: NOW)

    response = reader.read(ReadRequest.publication("weather-home", authorized=True))

    assert isinstance(response, Delivered)
    assert response.document["sourceRevision"] == 7
    assert producer.calls == 0


def test_read_only_module_reads_existing_ledger_without_writing(tmp_path):
    root = tmp_path / "ledger"
    writer = _module(root, _draft())
    assert writer.publish_due(WorkBudget(max_items=1)).published == 1
    files_before = {
        path.relative_to(root): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    reader = PublicationModule(root, read_only=True, clock=lambda: NOW)
    response = reader.read(ReadRequest.publication("weather-home", authorized=True))

    assert isinstance(response, Delivered)
    assert response.document["instanceUuid"] == "weather-home"
    with pytest.raises(RuntimeError, match="read-only"):
        reader.publish_due(WorkBudget(max_items=1))
    files_after = {
        path.relative_to(root): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert files_after == files_before


def test_read_only_module_does_not_create_a_missing_ledger(tmp_path):
    root = tmp_path / "missing-ledger"

    with pytest.raises(FileNotFoundError, match="publication ledger"):
        PublicationModule(root, read_only=True)

    assert not root.exists()


def test_unauthorized_request_is_denied_before_missing_identity_is_disclosed(tmp_path):
    module = PublicationModule(tmp_path, clock=lambda: NOW)

    denied = module.read(ReadRequest.publication("does-not-exist"))
    missing = module.read(ReadRequest.publication("does-not-exist", authorized=True))

    assert isinstance(denied, Denied)
    assert denied.reason_code == "authentication_required"
    assert denied.cache_control == "no-store"
    assert isinstance(missing, Missing)
    assert missing.reason_code == "not_found"


def test_asset_isolation_requires_reference_from_requested_current_instance(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", OneRasterAdapter())
    registry.register_native("weather", OneNativeAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(instance_uuid="clock-one", plugin_id="legacy-clock"),
            _draft(instance_uuid="weather-two"),
        ),
        adapters=registry,
        clock=lambda: NOW,
    )
    module.publish_due(WorkBudget())
    clock = module.read(ReadRequest.publication("clock-one", authorized=True))
    assert isinstance(clock, Delivered)
    asset_id = clock.document["assets"][0]["assetId"]

    response = module.read(ReadRequest.asset("weather-two", asset_id, authorized=True))

    assert isinstance(response, Missing)


def test_identical_assets_share_content_identity_across_isolated_instances(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", OneRasterAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(instance_uuid="clock-one", plugin_id="legacy-clock"),
            _draft(instance_uuid="clock-two", plugin_id="legacy-clock"),
        ),
        adapters=registry,
        clock=lambda: NOW,
    )
    module.publish_due(WorkBudget())

    first = module.read(ReadRequest.publication("clock-one", authorized=True))
    second = module.read(ReadRequest.publication("clock-two", authorized=True))

    assert isinstance(first, Delivered)
    assert isinstance(second, Delivered)
    assert first.document["assets"][0]["assetId"] == (second.document["assets"][0]["assetId"])


def test_retention_bounds_editions_and_collects_orphaned_cas_assets(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", RevisionRasterAdapter())
    for revision in range(1, 5):
        module = PublicationModule(
            tmp_path,
            producer=StaticProducer(
                _draft(
                    plugin_id="legacy-clock",
                    source_revision=revision,
                )
            ),
            adapters=registry,
            clock=lambda: NOW,
            max_editions_per_instance=2,
        )
        assert module.publish_due(WorkBudget()).published == 1

    database = tmp_path / "publication.sqlite3"
    with sqlite3.connect(database) as connection:
        edition_count = connection.execute(
            "SELECT COUNT(*) FROM editions WHERE instance_uuid = ?",
            ("weather-home",),
        ).fetchone()[0]
        asset_count = connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    asset_files = [path for path in (tmp_path / "assets" / "sha256").rglob("*") if path.is_file()]
    current = module.read(ReadRequest.publication("weather-home", authorized=True))

    assert edition_count == 2
    assert asset_count == 2
    assert len(asset_files) == 2
    assert isinstance(current, Delivered)
    assert current.document["sourceRevision"] == 4
    asset_id = current.document["assets"][0]["assetId"]
    asset = module.read(ReadRequest.asset("weather-home", asset_id, authorized=True))
    assert isinstance(asset, Delivered)
    assert asset.body == b"png-revision-4"


def test_failed_prune_transaction_does_not_delete_rollback_referenced_asset(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", RevisionRasterAdapter())
    first = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(plugin_id="legacy-clock", source_revision=1)),
        adapters=registry,
        clock=lambda: NOW,
        max_editions_per_instance=1,
    )
    assert first.publish_due(WorkBudget()).published == 1

    database = tmp_path / "publication.sqlite3"
    with sqlite3.connect(database) as connection:
        asset_id, relative_path = connection.execute("SELECT asset_id, relative_path FROM assets").fetchone()
        connection.execute(
            """
            CREATE TABLE retained_asset_guard (
                asset_id TEXT REFERENCES assets(asset_id)
                    DEFERRABLE INITIALLY DEFERRED
            )
            """
        )
        connection.execute(
            "INSERT INTO retained_asset_guard(asset_id) VALUES (?)",
            (asset_id,),
        )
    asset_path = tmp_path / "assets" / "sha256" / relative_path
    assert asset_path.is_file()

    second = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(plugin_id="legacy-clock", source_revision=2)),
        adapters=registry,
        clock=lambda: NOW,
        max_editions_per_instance=1,
    )

    with pytest.raises(sqlite3.IntegrityError):
        second.publish_due(WorkBudget())

    assert asset_path.is_file()
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0]
            == 1
        )


def test_cas_gc_removes_untracked_asset_from_a_skipped_publication(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", RevisionRasterAdapter())
    current = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(plugin_id="legacy-clock", source_revision=4)),
        adapters=registry,
        clock=lambda: NOW,
    )
    assert current.publish_due(WorkBudget()).published == 1
    older = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(plugin_id="legacy-clock", source_revision=3)),
        adapters=registry,
        clock=lambda: NOW,
    )

    report = older.publish_due(WorkBudget())
    asset_files = [path for path in (tmp_path / "assets" / "sha256").rglob("*") if path.is_file()]
    publication = older.read(ReadRequest.publication("weather-home", authorized=True))

    assert report.skipped == 1
    assert len(asset_files) == 1
    assert isinstance(publication, Delivered)
    asset_id = publication.document["assets"][0]["assetId"]
    asset = older.read(ReadRequest.asset("weather-home", asset_id, authorized=True))
    assert isinstance(asset, Delivered)
    assert asset.body == b"png-revision-4"


def test_freshness_transitions_from_fresh_to_stale_to_last_good(tmp_path):
    _module(tmp_path, _draft()).publish_due(WorkBudget())

    fresh = _module(tmp_path, now=NOW).read(ReadRequest.publication("weather-home", authorized=True))
    stale = _module(tmp_path, now=NOW + timedelta(minutes=16)).read(
        ReadRequest.publication("weather-home", authorized=True)
    )
    last_good = _module(tmp_path, now=NOW + timedelta(hours=3)).read(
        ReadRequest.publication("weather-home", authorized=True)
    )

    assert isinstance(fresh, Delivered)
    assert isinstance(stale, Delivered)
    assert isinstance(last_good, Delivered)
    assert fresh.document["freshness"] == "fresh"
    assert stale.document["freshness"] == "stale"
    assert last_good.document["freshness"] == "last_good"
    assert len({fresh.etag, stale.etag, last_good.etag}) == 3


def test_older_catalog_revision_cannot_replace_current_catalog(tmp_path):
    newer = PlaylistSpec("newer", "Newer")
    older = PlaylistSpec("older", "Older")
    current = PublicationModule(
        tmp_path,
        producer=StaticProducer(playlists=(newer,), catalog_revision=5),
        clock=lambda: NOW,
    ).publish_due(WorkBudget())
    rollback = PublicationModule(
        tmp_path,
        producer=StaticProducer(playlists=(older,), catalog_revision=4),
        clock=lambda: NOW,
    ).publish_due(WorkBudget())
    catalog = PublicationModule(tmp_path, clock=lambda: NOW).read(ReadRequest.catalog(authorized=True))

    assert current.catalog_updated is True
    assert rollback.catalog_updated is False
    assert isinstance(catalog, Delivered)
    assert catalog.document["catalogRevision"] == 5
    assert catalog.document["playlists"][0]["slug"] == "newer"


def test_retention_bounds_catalog_history_without_deleting_current(tmp_path):
    for revision in range(1, 5):
        module = PublicationModule(
            tmp_path,
            producer=StaticProducer(
                playlists=(
                    PlaylistSpec(
                        f"catalog-{revision}",
                        f"Catalog {revision}",
                    ),
                ),
                catalog_revision=revision,
            ),
            clock=lambda: NOW,
            max_catalog_editions=2,
        )
        module.publish_due(WorkBudget())

    with sqlite3.connect(tmp_path / "publication.sqlite3") as connection:
        catalog_count = connection.execute("SELECT COUNT(*) FROM catalogs").fetchone()[0]
        current_revision = connection.execute(
            """
            SELECT catalogs.catalog_revision
            FROM current_catalog
            JOIN catalogs ON catalogs.catalog_id = current_catalog.catalog_id
            """
        ).fetchone()[0]
    current = module.read(ReadRequest.catalog(authorized=True))

    assert catalog_count == 2
    assert current_revision == 4
    assert isinstance(current, Delivered)
    assert current.document["catalogRevision"] == 4


def test_cross_midnight_playlist_window_is_active_after_midnight(tmp_path):
    playlist = PlaylistSpec(
        "night",
        "Night",
        window=TimeWindow("23:00", "02:00"),
    )
    after_midnight = NOW.replace(hour=0, minute=30)
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(playlists=(playlist,)),
        clock=lambda: after_midnight,
    )
    module.publish_due(WorkBudget())

    catalog = module.read(ReadRequest.catalog(authorized=True))

    assert isinstance(catalog, Delivered)
    assert catalog.document["playlists"][0]["active"] is True


def test_playlist_window_uses_the_configured_timezone(tmp_path):
    now = datetime(2026, 8, 2, 1, 30, tzinfo=timezone.utc)
    playlist = PlaylistSpec(
        "evening",
        "Evening",
        window=TimeWindow("18:00", "19:00"),
    )
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(playlists=(playlist,)),
        clock=lambda: now,
        timezone_name="America/Los_Angeles",
    )
    module.publish_due(WorkBudget())

    catalog = module.read(ReadRequest.catalog(authorized=True))

    assert isinstance(catalog, Delivered)
    assert catalog.document["playlists"][0]["active"] is True


def test_configured_active_playlist_is_preserved_alongside_window_state(tmp_path):
    playlists = (
        PlaylistSpec(
            "day",
            "Day",
            order=0,
            window=TimeWindow("08:00", "17:00"),
        ),
        PlaylistSpec(
            "night",
            "Night",
            order=1,
            window=TimeWindow("20:00", "23:00"),
        ),
    )
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            playlists=playlists,
            active_playlist="night",
        ),
        clock=lambda: NOW,
    )
    module.publish_due(WorkBudget())

    catalog = module.read(ReadRequest.catalog(authorized=True))

    assert isinstance(catalog, Delivered)
    assert catalog.document["activePlaylist"] == "night"
    assert [item["windowActive"] for item in catalog.document["playlists"]] == [
        True,
        False,
    ]


def test_concurrent_promotions_atomically_leave_highest_revision_current(tmp_path):
    barrier = threading.Barrier(2)
    registry = AdapterRegistry()
    registry.register_native("weather", BarrierNativeAdapter(barrier))
    lower = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(source_revision=8, snapshot={"temperature": 80})),
        adapters=registry,
        clock=lambda: NOW,
    )
    higher = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(source_revision=9, snapshot={"temperature": 90})),
        adapters=registry,
        clock=lambda: NOW,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = tuple(executor.map(lambda item: item.publish_due(WorkBudget()), (lower, higher)))
    response = PublicationModule(tmp_path, clock=lambda: NOW).read(
        ReadRequest.publication("weather-home", authorized=True)
    )

    assert sum(report.published for report in reports) in {1, 2}
    assert isinstance(response, Delivered)
    assert response.document["sourceRevision"] == 9
    assert response.document["payload"] == {"temperature": 90}


def test_concurrent_gc_cannot_delete_asset_before_its_promotion(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", RevisionRasterAdapter())
    seed = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(plugin_id="legacy-clock", source_revision=7)),
        adapters=registry,
        clock=lambda: NOW,
        max_editions_per_instance=2,
    )
    assert seed.publish_due(WorkBudget()).published == 1
    high = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(plugin_id="legacy-clock", source_revision=9)),
        adapters=registry,
        clock=lambda: NOW,
        max_editions_per_instance=2,
    )
    high_asset_is_untracked = threading.Event()
    allow_high_promotion = threading.Event()
    original_connect = high._store.connect
    connection_count = 0

    def delayed_connect():
        nonlocal connection_count
        connection_count += 1
        if connection_count == 2:
            high_asset_is_untracked.set()
            assert allow_high_promotion.wait(timeout=5)
        return original_connect()

    high._store.connect = delayed_connect
    with ThreadPoolExecutor(max_workers=1) as executor:
        high_future = executor.submit(high.publish_due, WorkBudget())
        assert high_asset_is_untracked.wait(timeout=5)
        middle = PublicationModule(
            tmp_path,
            producer=StaticProducer(_draft(plugin_id="legacy-clock", source_revision=8)),
            adapters=registry,
            clock=lambda: NOW,
            max_editions_per_instance=2,
        )
        assert middle.publish_due(WorkBudget()).published == 1
        allow_high_promotion.set()
        assert high_future.result(timeout=5).published == 1

    publication = high.read(ReadRequest.publication("weather-home", authorized=True))
    assert isinstance(publication, Delivered)
    asset_id = publication.document["assets"][0]["assetId"]
    asset = high.read(ReadRequest.asset("weather-home", asset_id, authorized=True))

    assert isinstance(asset, Delivered)
    assert asset.body == b"png-revision-9"


def test_builtin_context_and_raster_adapters_publish_without_runtime_dependencies(tmp_path):
    registry = AdapterRegistry()
    registry.register_native("weather", ContextPayloadNativeAdapter())
    registry.register_default_legacy(RasterAssetLegacyAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(snapshot={"context": {"temperature": 72}}),
            _draft(
                instance_uuid="legacy-one",
                plugin_id="legacy-plugin",
                snapshot={"rasterMetadata": {"width": 800, "height": 480}},
                source_assets=(AssetInput("raster", "image/png", b"png"),),
            ),
        ),
        adapters=registry,
        clock=lambda: NOW,
    )

    report = module.publish_due(WorkBudget())
    native = module.read(ReadRequest.publication("weather-home", authorized=True))
    raster = module.read(ReadRequest.publication("legacy-one", authorized=True))

    assert report.published == 2
    assert isinstance(native, Delivered)
    assert native.document["payload"] == {"temperature": 72}
    assert isinstance(raster, Delivered)
    assert raster.document["presentation"] == "legacy_raster"
    assert raster.document["assets"][0]["name"] == "raster"


def test_deduplicated_asset_keeps_media_type_scoped_to_each_edition(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("png-plugin", MediaTypedRasterAdapter("image/png"))
    registry.register_legacy(
        "binary-plugin",
        MediaTypedRasterAdapter("application/octet-stream"),
    )
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(
            _draft(instance_uuid="png-one", plugin_id="png-plugin"),
            _draft(instance_uuid="binary-one", plugin_id="binary-plugin"),
        ),
        adapters=registry,
        clock=lambda: NOW,
    )
    module.publish_due(WorkBudget())
    png_publication = module.read(ReadRequest.publication("png-one", authorized=True))
    binary_publication = module.read(ReadRequest.publication("binary-one", authorized=True))
    assert isinstance(png_publication, Delivered)
    assert isinstance(binary_publication, Delivered)
    asset_id = png_publication.document["assets"][0]["assetId"]
    assert binary_publication.document["assets"][0]["assetId"] == asset_id

    png = module.read(ReadRequest.asset("png-one", asset_id, authorized=True))
    binary = module.read(ReadRequest.asset("binary-one", asset_id, authorized=True))

    assert isinstance(png, Delivered)
    assert isinstance(binary, Delivered)
    assert png.content_type == "image/png"
    assert binary.content_type == "application/octet-stream"


def test_removing_instance_from_new_catalog_hides_its_retained_edition_and_assets(tmp_path):
    registry = AdapterRegistry()
    registry.register_legacy("legacy-clock", OneRasterAdapter())
    module = PublicationModule(
        tmp_path,
        producer=StaticProducer(_draft(instance_uuid="clock-one", plugin_id="legacy-clock")),
        adapters=registry,
        clock=lambda: NOW,
    )
    module.publish_due(WorkBudget())
    publication = module.read(ReadRequest.publication("clock-one", authorized=True))
    assert isinstance(publication, Delivered)
    asset_id = publication.document["assets"][0]["assetId"]

    PublicationModule(
        tmp_path,
        producer=StaticProducer(playlists=(), catalog_revision=2),
        clock=lambda: NOW,
    ).publish_due(WorkBudget())

    hidden_publication = module.read(ReadRequest.publication("clock-one", authorized=True))
    hidden_asset = module.read(ReadRequest.asset("clock-one", asset_id, authorized=True))
    assert isinstance(hidden_publication, Missing)
    assert isinstance(hidden_asset, Missing)
