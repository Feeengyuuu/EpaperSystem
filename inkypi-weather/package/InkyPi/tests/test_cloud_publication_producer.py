from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from publication import (  # noqa: E402
    AssetInput,
    Delivered,
    PublicationDraft,
    PublicationModule,
    ReadRequest,
    WorkBudget,
)
from publication_producer import (  # noqa: E402
    CloudPublicationProducer,
    NATIVE_PLUGIN_IDS,
    build_default_adapter_registry,
)


NOW = datetime(2026, 8, 2, 18, 30, tzinfo=timezone.utc)


def _write_bundle(root, *, instances, config_revision=7):
    document = {
        "format_version": 1,
        "source": {"schema_version": 1, "config_revision": config_revision},
        "device": {
            "name": "Model Y Portal",
            "resolution": [800, 480],
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
            "time_format": "12h",
            "plugin_cycle_interval_seconds": 300,
            "image_settings": {},
        },
        "plugin_catalog": [
            {
                "id": instance["plugin_id"],
                "class": "FakePlugin",
                "display_name": instance["plugin_id"],
                "disabled": False,
            }
            for instance in instances
        ],
        "active_playlist": "Drive",
        "playlists": [
            {
                "name": "Drive",
                "start_time": "07:00",
                "end_time": "23:00",
                "plugins": instances,
            }
        ],
    }
    root.mkdir(parents=True)
    (root / "web_config.json").write_text(
        json.dumps(document),
        encoding="utf-8",
    )
    return document


def _instance(
    instance_uuid="weather-home",
    *,
    plugin_id="weather",
    settings=None,
    interval=300,
):
    return {
        "instance_uuid": instance_uuid,
        "plugin_id": plugin_id,
        "name": f"Title {instance_uuid}",
        "plugin_settings": settings or {},
        "refresh": {"interval": interval},
        "latest_refresh_time": None,
        "structural_generation": 1,
        "settings_revision": 3,
    }


def test_cloud_producer_builds_ordered_catalog_native_snapshot_and_png(tmp_path):
    bundle = tmp_path / "bundle"
    resource_body = b"local resource"
    digest = hashlib.sha256(resource_body).hexdigest()
    resource_rel = f"resources/{digest}.png"
    instance = _instance(
        settings={
            "apiKey": "${GITHUB_SECRET}",
            "imagePath": resource_rel,
        }
    )
    _write_bundle(bundle, instances=[instance])
    resource = bundle / resource_rel
    resource.parent.mkdir()
    resource.write_bytes(resource_body)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "GITHUB_SECRET").write_text("server-only-value\n", encoding="utf-8")
    captured = {}

    class FakePlugin:
        def generate_image(self, settings, device_config):
            captured["settings"] = settings
            captured["resolution"] = device_config.get_resolution()
            return Image.new("RGB", (800, 480), "white")

    producer = CloudPublicationProducer(
        bundle,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        secret_dir=secrets,
        plugin_factory=lambda _entry: FakePlugin(),
        context_loader=lambda _directory, plugin_id: {
            "kind": "weather",
            "summary": f"native {plugin_id}",
            "facts": [{"label": "temperature", "value": "72°F"}],
        },
        clock=lambda: NOW,
    )

    batch = producer.collect_due(WorkBudget())

    assert batch.schema_version == 1
    assert batch.catalog_revision == 7
    assert batch.active_playlist == batch.playlists[0].slug
    assert batch.failures == ()
    assert [playlist.title for playlist in batch.playlists] == ["Drive"]
    assert batch.playlists[0].window.start == "07:00"
    assert [entry.instance_uuid for entry in batch.playlists[0].entries] == ["weather-home"]
    draft = batch.drafts[0]
    assert draft.instance_uuid == "weather-home"
    assert draft.settings_revision == 3
    assert draft.generated_at == NOW
    assert draft.fresh_until == NOW + timedelta(seconds=300)
    assert draft.stale_until >= draft.fresh_until + timedelta(hours=24)
    assert draft.snapshot["context"]["summary"] == "native weather"
    assert draft.snapshot["rasterMetadata"] == {
        "alt": "Title weather-home",
        "height": 480,
        "width": 800,
    }
    assert set(draft.snapshot) == {"context", "rasterMetadata"}
    assert len(draft.source_assets) == 1
    assert draft.source_assets[0].name == "raster"
    assert draft.source_assets[0].media_type == "image/png"
    assert draft.source_assets[0].body.startswith(b"\x89PNG\r\n\x1a\n")
    assert captured == {
        "settings": {
            "apiKey": "server-only-value",
            "imagePath": str(resource.resolve()),
        },
        "resolution": (800, 480),
    }
    serialized = json.dumps(draft.snapshot)
    assert "server-only-value" not in serialized
    assert str(resource) not in serialized


def test_cloud_producer_reports_safe_failure_and_preserves_catalog(tmp_path):
    bundle = tmp_path / "bundle"
    _write_bundle(bundle, instances=[_instance()])

    class BrokenPlugin:
        def generate_image(self, _settings, _device_config):
            raise RuntimeError("token=must-not-leak C:/private/cache")

    producer = CloudPublicationProducer(
        bundle,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        plugin_factory=lambda _entry: BrokenPlugin(),
        clock=lambda: NOW,
    )

    batch = producer.collect_due(WorkBudget())

    assert batch.drafts == ()
    assert len(batch.playlists) == 1
    assert len(batch.failures) == 1
    assert batch.failures[0].instance_uuid == "weather-home"
    assert batch.failures[0].reason_code == "render_failed"
    assert "must-not-leak" not in repr(batch.failures)
    assert "private" not in repr(batch.failures)


def test_cloud_producer_rejects_wrong_device_resolution_and_keeps_last_good(tmp_path):
    bundle = tmp_path / "bundle"
    _write_bundle(bundle, instances=[_instance()])
    current = [NOW]
    dimensions = [(800, 480)]

    class MutablePlugin:
        def generate_image(self, _settings, _device_config):
            return Image.new("RGB", dimensions[0], "white")

    producer = CloudPublicationProducer(
        bundle,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        plugin_factory=lambda _entry: MutablePlugin(),
        clock=lambda: current[0],
    )
    module = PublicationModule(
        tmp_path / "publication",
        producer=producer,
        adapters=build_default_adapter_registry(),
        clock=lambda: current[0],
    )

    first = module.publish_due(WorkBudget())
    dimensions[0] = (799, 480)
    current[0] += timedelta(seconds=301)
    second = module.publish_due(WorkBudget())
    publication = module.read(ReadRequest.publication("weather-home", authorized=True))

    assert first.published == 1
    assert second.published == 0
    assert second.unavailable == 1
    assert second.outcomes[0].reason_code == "render_failed"
    assert isinstance(publication, Delivered)
    assert publication.document["freshness"] == "last_good"
    assert publication.document["payload"] == {
        "alt": "Title weather-home",
        "height": 480,
        "width": 800,
    }


def test_cloud_producer_requires_placeholder_secret_before_plugin_call(tmp_path):
    bundle = tmp_path / "bundle"
    _write_bundle(
        bundle,
        instances=[_instance(settings={"apiKey": "${GITHUB_SECRET}"})],
    )
    calls = []
    producer = CloudPublicationProducer(
        bundle,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        plugin_factory=lambda entry: calls.append(entry),
        clock=lambda: NOW,
    )

    batch = producer.collect_due(WorkBudget())

    assert calls == []
    assert batch.drafts == ()
    assert [failure.reason_code for failure in batch.failures] == ["credentials_unavailable"]


def test_cloud_producer_honors_budget_and_does_not_rerender_before_interval(tmp_path):
    bundle = tmp_path / "bundle"
    _write_bundle(
        bundle,
        instances=[
            _instance("one", plugin_id="weather"),
            _instance("two", plugin_id="steam_charts"),
        ],
    )
    current = [NOW]
    calls = []

    class FakePlugin:
        def generate_image(self, _settings, _device_config):
            calls.append(current[0])
            return Image.new("RGB", (800, 480), "white")

    producer = CloudPublicationProducer(
        bundle,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        plugin_factory=lambda _entry: FakePlugin(),
        clock=lambda: current[0],
    )

    first = producer.collect_due(WorkBudget(max_items=1))
    second = producer.collect_due(WorkBudget(max_items=1))
    current[0] += timedelta(seconds=301)
    third = producer.collect_due(WorkBudget(max_items=2))

    assert [draft.instance_uuid for draft in first.drafts] == ["one"]
    assert [draft.instance_uuid for draft in second.drafts] == ["two"]
    assert [draft.instance_uuid for draft in third.drafts] == ["one", "two"]
    assert len(calls) == 4


def test_default_registry_tracks_context_capabilities_and_has_global_png_fallback():
    registry = build_default_adapter_registry()

    assert {
        "sports_dashboard",
        "mini_weather",
        "live_radar",
        "steam_charts",
        "stocktracker",
        "ticketmaster_events",
    }.issubset(NATIVE_PLUGIN_IDS)
    assert "weather" not in NATIVE_PLUGIN_IDS

    original_png = b"\x89PNG\r\n\x1a\nexact-custom-plugin-frame"
    draft = PublicationDraft(
        instance_uuid="custom-one",
        plugin_id="custom_plugin",
        title="Custom Plugin",
        settings_revision=1,
        source_revision=1,
        generated_at=NOW,
        fresh_until=NOW + timedelta(minutes=5),
        stale_until=NOW + timedelta(days=1),
        snapshot={
            "rasterMetadata": {
                "alt": "Custom Plugin",
                "width": 800,
                "height": 480,
            }
        },
        source_assets=(AssetInput("raster", "image/png", original_png),),
    )

    adapted = registry.adapt(draft)

    assert adapted.presentation == "legacy_raster"
    assert adapted.material.assets[0].body == original_png


def test_default_registry_publishes_weather_as_the_original_plugin_raster():
    original_png = b"\x89PNG\r\n\x1a\noriginal-weather-frame"
    draft = PublicationDraft(
        instance_uuid="weather-home",
        plugin_id="weather",
        title="Fremont Weather",
        settings_revision=1,
        source_revision=1,
        generated_at=NOW,
        fresh_until=NOW + timedelta(minutes=5),
        stale_until=NOW + timedelta(days=1),
        snapshot={
            "context": {"kind": "weather", "headline": "16°C"},
            "rasterMetadata": {
                "alt": "Fremont Weather",
                "width": 800,
                "height": 480,
            },
        },
        source_assets=(AssetInput("raster", "image/png", original_png),),
    )

    adapted = build_default_adapter_registry().adapt(draft)

    assert adapted.presentation == "legacy_raster"
    assert adapted.material.payload == {
        "alt": "Fremont Weather",
        "width": 800,
        "height": 480,
    }
    assert adapted.material.assets[0].body == original_png


def test_default_registry_publishes_native_capable_plugins_as_exact_original_rasters():
    original_png = b"\x89PNG\r\n\x1a\nexact-steam-frame"
    draft = PublicationDraft(
        instance_uuid="steam-one",
        plugin_id="steam_charts",
        title="Steam Charts",
        settings_revision=1,
        source_revision=1,
        generated_at=NOW,
        fresh_until=NOW + timedelta(minutes=5),
        stale_until=NOW + timedelta(days=1),
        snapshot={
            "context": {"kind": "game_chart", "items": [{"name": "redrawn-card"}]},
            "rasterMetadata": {
                "alt": "Steam Charts",
                "width": 800,
                "height": 480,
            },
        },
        source_assets=(AssetInput("raster", "image/png", original_png),),
    )

    adapted = build_default_adapter_registry().adapt(draft)

    assert adapted.presentation == "legacy_raster"
    assert adapted.material.payload == {
        "alt": "Steam Charts",
        "width": 800,
        "height": 480,
    }
    assert adapted.material.assets[0].body == original_png
