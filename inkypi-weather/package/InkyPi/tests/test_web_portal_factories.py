from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from publication import (  # noqa: E402
    AdapterRegistry,
    AssetInput,
    ContextPayloadNativeAdapter,
    PlaylistEntry,
    PlaylistSpec,
    ProducerBatch,
    PublicationDraft,
    PublicationModule,
    RasterAssetLegacyAdapter,
    TimeWindow,
    WorkBudget,
)
from web_portal.factories import (  # noqa: E402
    LedgerPublicationSource,
    PublicationWorker,
    create_reader,
    create_worker,
)


NOW = datetime(2026, 8, 2, 18, 30, tzinfo=timezone.utc)


class _Producer:
    def collect_due(self, _budget):
        return ProducerBatch(
            catalog_revision=11,
            playlists=(
                PlaylistSpec(
                    slug="drive",
                    title="行车简报",
                    window=TimeWindow("00:00", "24:00"),
                    entries=(
                        PlaylistEntry("weather-home", "弗里蒙特天气", "weather", 0),
                        PlaylistEntry("photo-frame", "家庭照片", "image_upload", 1),
                        PlaylistEntry("not-ready", "尚未生成", "ticketmaster_events", 2),
                    ),
                ),
            ),
            drafts=(
                PublicationDraft(
                    instance_uuid="weather-home",
                    plugin_id="weather",
                    title="弗里蒙特天气",
                    settings_revision=1,
                    source_revision=4,
                    generated_at=NOW,
                    fresh_until=NOW + timedelta(hours=1),
                    stale_until=NOW + timedelta(days=1),
                    snapshot={
                        "context": {
                            "kind": "weather",
                            "summary": "晴，72°F",
                            "facts": [{"label": "湿度", "value": "46%"}],
                        }
                    },
                ),
                PublicationDraft(
                    instance_uuid="photo-frame",
                    plugin_id="image_upload",
                    title="家庭照片",
                    settings_revision=1,
                    source_revision=3,
                    generated_at=NOW,
                    fresh_until=NOW + timedelta(hours=1),
                    stale_until=NOW + timedelta(days=1),
                    snapshot={
                        "rasterMetadata": {
                            "alt": "家庭照片",
                            "width": 800,
                            "height": 480,
                        }
                    },
                    source_assets=(AssetInput("raster", "image/png", b"\x89PNG\r\n\x1a\nprivate"),),
                ),
            ),
        )


def _published_module(tmp_path):
    registry = AdapterRegistry()
    registry.register_native("weather", ContextPayloadNativeAdapter())
    registry.register_default_legacy(RasterAssetLegacyAdapter())
    module = PublicationModule(
        tmp_path,
        producer=_Producer(),
        adapters=registry,
        clock=lambda: NOW,
    )
    report = module.publish_due(WorkBudget())
    assert report.published == 2
    return module


def test_ledger_source_translates_catalog_native_legacy_and_unavailable(tmp_path):
    source = LedgerPublicationSource(_published_module(tmp_path))

    catalog = source.list_publications()

    assert catalog["active_playlist"] == "drive"
    assert catalog["etag"]
    assert catalog["playlists"] == [
        {
            "slug": "drive",
            "name": "行车简报",
            "active": True,
            "window_active": True,
            "window": "00:00–24:00",
            "publication_slugs": ["weather-home", "photo-frame", "not-ready"],
        }
    ]
    by_slug = {item["slug"]: item for item in catalog["publications"]}
    assert by_slug["weather-home"] == {
        "slug": "weather-home",
        "title": "弗里蒙特天气",
        "plugin": "weather",
        "kind": "native",
        "freshness": "fresh",
        "updated_at": "2026-08-02T18:30:00Z",
        "payload": {
            "kind": "weather",
            "summary": "晴，72°F",
            "facts": [{"label": "湿度", "value": "46%"}],
        },
    }
    assert by_slug["photo-frame"]["kind"] == "legacy_png"
    assert by_slug["photo-frame"]["asset_id"].endswith("." + by_slug["photo-frame"]["asset_sha256"])
    assert by_slug["not-ready"]["freshness"] == "unavailable"
    assert by_slug["not-ready"]["message"] == "尚未生成"


def test_ledger_source_asset_token_is_stateless_scoped_and_etagged(tmp_path):
    module = _published_module(tmp_path)
    first_source = LedgerPublicationSource(module)
    publication = first_source.get_publication("photo-frame")
    token = publication["asset_id"]

    second_source = LedgerPublicationSource(module)
    asset = second_source.get_asset(token)

    assert asset["body"].startswith(b"\x89PNG")
    assert asset["content_type"] == "image/png"
    assert asset["etag"] == publication["asset_sha256"]
    assert second_source.get_asset("../private") is None
    assert second_source.get_asset("0" * 32 + "." + "f" * 64) is None


def test_create_reader_uses_publication_root_without_creating_a_producer(
    tmp_path,
    monkeypatch,
):
    _published_module(tmp_path / "ledger")
    monkeypatch.setenv("PUBLICATION_ROOT", str(tmp_path / "ledger"))

    source = create_reader()

    assert source.get_publication("weather-home")["payload"]["summary"] == "晴，72°F"


def test_create_reader_never_initializes_a_missing_ledger(tmp_path, monkeypatch):
    root = tmp_path / "missing-ledger"
    monkeypatch.setenv("PUBLICATION_ROOT", str(root))

    with pytest.raises(FileNotFoundError, match="publication ledger"):
        create_reader()

    assert not root.exists()


def test_create_reader_validates_the_configured_portal_timezone(
    tmp_path,
    monkeypatch,
):
    _published_module(tmp_path / "ledger")
    monkeypatch.setenv("PUBLICATION_ROOT", str(tmp_path / "ledger"))
    monkeypatch.setenv("WEB_PORTAL_TIMEZONE", "Not/A-Timezone")

    with pytest.raises(ValueError, match="invalid publication timezone"):
        create_reader()


def test_ledger_source_prefers_a_valid_configured_playlist_over_time_window(
    tmp_path,
):
    class Producer:
        def collect_due(self, _budget):
            return ProducerBatch(
                catalog_revision=12,
                active_playlist="night",
                playlists=(
                    PlaylistSpec(
                        "day",
                        "Day",
                        order=0,
                        window=TimeWindow("00:00", "24:00"),
                    ),
                    PlaylistSpec(
                        "night",
                        "Night",
                        order=1,
                        window=TimeWindow("20:00", "21:00"),
                    ),
                ),
            )

    module = PublicationModule(tmp_path, producer=Producer(), clock=lambda: NOW)
    module.publish_due(WorkBudget())

    catalog = LedgerPublicationSource(module).list_publications()

    assert catalog["active_playlist"] == "night"
    assert [item["window_active"] for item in catalog["playlists"]] == [
        True,
        False,
    ]


def test_publication_worker_calls_module_with_bounded_work_budget():
    calls = []

    class Module:
        def publish_due(self, budget):
            calls.append(budget)
            return "published"

    worker = PublicationWorker(
        Module(),
        max_items=17,
        deadline_seconds=45,
        clock=lambda: NOW,
    )

    assert worker.run_once() == "published"
    assert calls[0].max_items == 17
    assert calls[0].deadline == NOW + timedelta(seconds=45)


def test_create_worker_uses_the_imported_device_timezone(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    local_now = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Los_Angeles"))
    start_hour = (local_now.hour - 1) % 24
    end_hour = (local_now.hour + 2) % 24
    document = {
        "format_version": 1,
        "source": {"schema_version": 1, "config_revision": 1},
        "device": {
            "name": "Model Y Portal",
            "resolution": [800, 480],
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
            "time_format": "12h",
            "plugin_cycle_interval_seconds": 300,
            "image_settings": {},
        },
        "plugin_catalog": [],
        "active_playlist": None,
        "playlists": [
            {
                "name": "Drive",
                "start_time": f"{start_hour:02d}:00",
                "end_time": f"{end_hour:02d}:00",
                "plugins": [],
            }
        ],
    }
    (bundle / "web_config.json").write_text(
        json.dumps(document),
        encoding="utf-8",
    )
    monkeypatch.setenv("WEB_CONFIG", str(bundle / "web_config.json"))
    monkeypatch.setenv("WEB_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("PUBLICATION_ROOT", str(tmp_path / "ledger"))
    monkeypatch.setenv("WEB_SECRET_DIR", str(tmp_path / "secrets"))

    worker = create_worker()
    worker.run_once()
    catalog = LedgerPublicationSource(worker._module).list_publications()

    assert catalog["playlists"][0]["window_active"] is True

    monkeypatch.setenv("WEB_EDITION_RETENTION_PER_INSTANCE", "0")
    with pytest.raises(ValueError, match="WEB_EDITION_RETENTION_PER_INSTANCE"):
        create_worker()
    monkeypatch.delenv("WEB_EDITION_RETENTION_PER_INSTANCE")
    monkeypatch.setenv("WEB_CATALOG_RETENTION", "10001")
    with pytest.raises(ValueError, match="WEB_CATALOG_RETENTION"):
        create_worker()
