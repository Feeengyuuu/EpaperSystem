from __future__ import annotations

import hashlib
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from publication import Delivered, PublicationModule, ReadRequest  # noqa: E402
import publication_bootstrap as bootstrap_module  # noqa: E402
from publication_bootstrap import (  # noqa: E402
    attach_cached_raster_snapshot,
    publish_cached_raster_bundle,
)
from web_portal.factories import LedgerPublicationSource  # noqa: E402
from web_portal.health import PublicationReadiness  # noqa: E402


CAPTURED_AT = datetime(2026, 8, 2, 20, 30, tzinfo=timezone.utc)


def _write_png(path: Path) -> bytes:
    path.parent.mkdir(parents=True)
    Image.new("RGB", (800, 480), "#17314f").save(path, format="PNG")
    return path.read_bytes()


def _write_bundle(root: Path) -> bytes:
    instance_uuid = "weather-fremont"
    web_config = {
        "format_version": 1,
        "source": {"schema_version": 1, "config_revision": 42},
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
        "active_playlist": "Drive",
        "playlists": [
            {
                "name": "Drive",
                "start_time": "06:00",
                "end_time": "23:30",
                "plugins": [
                    {
                        "instance_uuid": instance_uuid,
                        "plugin_id": "weather",
                        "name": "Fremont Weather",
                        "plugin_settings": {},
                        "refresh": {"interval": 300},
                        "structural_generation": 2,
                        "settings_revision": 7,
                    }
                ],
            }
        ],
    }
    root.mkdir(parents=True)
    config_path = root / "web_config.json"
    config_path.write_text(json.dumps(web_config), encoding="utf-8")
    config_body = config_path.read_bytes()

    temporary_raster = root / "rasters" / "frame.png"
    raster_body = _write_png(temporary_raster)
    raster_sha256 = hashlib.sha256(raster_body).hexdigest()
    raster_path = root / "rasters" / f"{raster_sha256}.png"
    temporary_raster.replace(raster_path)
    relative_raster_path = raster_path.relative_to(root).as_posix()
    manifest = {
        "format_version": 1,
        "captured_at": CAPTURED_AT.isoformat().replace("+00:00", "Z"),
        "files": [
            {
                "path": "web_config.json",
                "sha256": hashlib.sha256(config_body).hexdigest(),
                "size": len(config_body),
            },
            {
                "path": relative_raster_path,
                "sha256": raster_sha256,
                "size": len(raster_body),
            },
        ],
        "rasters": [
            {
                "instance_uuid": instance_uuid,
                "status": "available",
                "path": relative_raster_path,
                "sha256": raster_sha256,
                "size": len(raster_body),
                "structural_generation": 2,
                "settings_revision": 7,
                "source_updated_at": "2026-08-02T20:30:00Z",
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return raster_body


def test_bootstrap_publishes_exact_cached_raster_through_formal_reader(tmp_path):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    expected_raster = _write_bundle(bundle)

    report = publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)

    assert report.attempted == 1
    assert report.published == 1
    assert report.unavailable == 0

    source = LedgerPublicationSource(
        PublicationModule(
            ledger,
            read_only=True,
            timezone_name="America/Los_Angeles",
            clock=lambda: CAPTURED_AT,
        )
    )
    catalog = source.list_publications()
    assert catalog["active_playlist"] == catalog["playlists"][0]["slug"]
    assert catalog["playlists"][0]["name"] == "Drive"
    assert catalog["playlists"][0]["publication_slugs"] == ["weather-fremont"]

    publication = source.get_publication("weather-fremont")
    assert publication["kind"] == "legacy_png"
    assert publication["payload"] == {
        "alt": "Fremont Weather original plugin frame",
        "data_mode": "cache",
        "height": 480,
        "source_label": "Fremont Weather",
        "source_updated_at": "2026-08-02T20:30:00Z",
        "width": 800,
    }
    asset = source.get_asset(publication["asset_id"])
    assert asset["body"] == expected_raster
    assert asset["etag"] == hashlib.sha256(expected_raster).hexdigest()
    assert source.inspect_readiness() == PublicationReadiness(
        configured=1,
        displayable=1,
        assets_valid=True,
    )


def test_formal_readiness_probe_rejects_a_missing_current_raster(tmp_path):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    raster = _write_bundle(bundle)
    publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)
    digest = hashlib.sha256(raster).hexdigest()
    stored_asset = ledger / "assets" / "sha256" / digest[:2] / digest
    stored_asset.unlink()
    source = LedgerPublicationSource(
        PublicationModule(
            ledger,
            read_only=True,
            timezone_name="America/Los_Angeles",
            clock=lambda: CAPTURED_AT,
        )
    )

    assert source.inspect_readiness() == PublicationReadiness(
        configured=1,
        displayable=0,
        assets_valid=False,
    )


def test_bootstrap_source_revision_stays_compatible_with_cloud_worker(tmp_path):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    _write_bundle(bundle)

    publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)

    response = PublicationModule(
        ledger,
        read_only=True,
        timezone_name="America/Los_Angeles",
        clock=lambda: CAPTURED_AT,
    ).read(ReadRequest.publication("weather-fremont", authorized=True))
    assert isinstance(response, Delivered)
    assert response.document["sourceRevision"] == int(CAPTURED_AT.timestamp() * 1_000_000)


def test_bootstrap_preserves_instance_order_and_declared_unavailable(tmp_path):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    _write_bundle(bundle)
    config_path = bundle / "web_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["playlists"][0]["plugins"].insert(
        0,
        {
            "instance_uuid": "weather-oakland",
            "plugin_id": "weather",
            "name": "Oakland Weather",
            "plugin_settings": {},
            "refresh": {"interval": 300},
            "structural_generation": 5,
            "settings_revision": 9,
        },
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")
    config_body = config_path.read_bytes()
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_record = next(item for item in manifest["files"] if item["path"] == "web_config.json")
    config_record.update(
        sha256=hashlib.sha256(config_body).hexdigest(),
        size=len(config_body),
    )
    manifest["rasters"].insert(
        0,
        {
            "instance_uuid": "weather-oakland",
            "status": "unavailable",
            "reason": "missing",
            "structural_generation": 5,
            "settings_revision": 9,
        },
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)

    assert report.attempted == 2
    assert report.published == 1
    assert report.unavailable == 1
    source = LedgerPublicationSource(
        PublicationModule(
            ledger,
            read_only=True,
            timezone_name="America/Los_Angeles",
            clock=lambda: CAPTURED_AT,
        )
    )
    catalog = source.list_publications()
    assert catalog["playlists"][0]["publication_slugs"] == [
        "weather-oakland",
        "weather-fremont",
    ]
    assert source.get_publication("weather-oakland")["freshness"] == "unavailable"
    assert source.get_publication("weather-fremont")["kind"] == "legacy_png"


def test_bootstrap_rejects_globally_reused_instance_uuid_before_writing(tmp_path):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    _write_bundle(bundle)
    config_path = bundle / "web_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    duplicate = dict(config["playlists"][0]["plugins"][0])
    config["playlists"].append(
        {
            "name": "Evening",
            "start_time": "18:00",
            "end_time": "24:00",
            "plugins": [duplicate],
        }
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")
    config_body = config_path.read_bytes()
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_record = next(item for item in manifest["files"] if item["path"] == "web_config.json")
    config_record.update(
        sha256=hashlib.sha256(config_body).hexdigest(),
        size=len(config_body),
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="instance_uuid"):
        publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)

    assert not ledger.exists()


def test_attach_snapshot_content_addresses_exact_bytes_without_source_names(tmp_path):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    _write_bundle(bundle)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_raster_paths = [item["path"] for item in manifest["files"] if item["path"].startswith("rasters/")]
    manifest["files"] = [item for item in manifest["files"] if not item["path"].startswith("rasters/")]
    manifest.pop("captured_at")
    manifest.pop("rasters")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for relative_path in old_raster_paths:
        (bundle / relative_path).unlink()

    snapshot = tmp_path / "snapshot"
    source_path = snapshot / "frames" / "private-device-cache-name.png"
    expected_raster = _write_png(source_path)
    snapshot_document = {
        "format_version": 1,
        "captured_at": "2026-08-02T20:30:00Z",
        "rasters": [
            {
                "instance_uuid": "weather-fremont",
                "status": "available",
                "path": "frames/private-device-cache-name.png",
                "structural_generation": 2,
                "settings_revision": 7,
                "source_updated_at": "2026-08-02T20:30:00Z",
            }
        ],
    }
    (snapshot / "snapshot.json").write_text(json.dumps(snapshot_document), encoding="utf-8")

    attachment = attach_cached_raster_snapshot(bundle, snapshot)

    assert attachment.configured == 1
    assert attachment.available == 1
    assert attachment.unavailable == 0
    attached_manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "private-device-cache-name" not in attached_manifest_text
    attached_manifest = json.loads(attached_manifest_text)
    record = attached_manifest["rasters"][0]
    digest = hashlib.sha256(expected_raster).hexdigest()
    assert record["path"] == f"rasters/{digest}.png"
    assert (bundle / record["path"]).read_bytes() == expected_raster

    publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)
    source = LedgerPublicationSource(
        PublicationModule(
            ledger,
            read_only=True,
            timezone_name="America/Los_Angeles",
            clock=lambda: CAPTURED_AT,
        )
    )
    publication = source.get_publication("weather-fremont")
    assert source.get_asset(publication["asset_id"])["body"] == expected_raster


def test_bootstrap_refuses_to_rewrite_an_existing_ledger(tmp_path):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    _write_bundle(bundle)
    publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)
    before = LedgerPublicationSource(
        PublicationModule(
            ledger,
            read_only=True,
            timezone_name="America/Los_Angeles",
            clock=lambda: CAPTURED_AT,
        )
    ).list_publications()

    with pytest.raises(FileExistsError, match="bootstrap target"):
        publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)

    after = LedgerPublicationSource(
        PublicationModule(
            ledger,
            read_only=True,
            timezone_name="America/Los_Angeles",
            clock=lambda: CAPTURED_AT,
        )
    ).list_publications()
    assert after["etag"] == before["etag"]


@pytest.mark.parametrize(
    "unsafe_path",
    ["C:/escape", "alias//file", "alias/./file", "alias/file.", "alias/file "],
)
def test_bootstrap_rejects_noncanonical_manifest_paths_before_writing(tmp_path, unsafe_path):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    _write_bundle(bundle)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": unsafe_path,
            "sha256": "0" * 64,
            "size": 0,
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle path"):
        publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)

    assert not ledger.exists()


@pytest.mark.parametrize("mutation", ["duplicate_playlist", "unknown_active"])
def test_bootstrap_rejects_ambiguous_playlist_configuration(tmp_path, mutation):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    _write_bundle(bundle)
    config_path = bundle / "web_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if mutation == "duplicate_playlist":
        config["playlists"].append(
            {
                "name": "Drive",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [],
            }
        )
    else:
        config["active_playlist"] = "Does Not Exist"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    config_body = config_path.read_bytes()
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_record = next(item for item in manifest["files"] if item["path"] == "web_config.json")
    config_record.update(
        sha256=hashlib.sha256(config_body).hexdigest(),
        size=len(config_body),
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="playlist"):
        publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)

    assert not ledger.exists()


def test_bootstrap_target_is_invisible_until_complete_ledger_is_atomically_installed(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    _write_bundle(bundle)
    original_replace = bootstrap_module.os.replace
    observed = []

    def observe_final_install(source, destination):
        source = Path(source)
        destination = Path(destination)
        if destination == ledger:
            assert not ledger.exists()
            staged_source = LedgerPublicationSource(
                PublicationModule(
                    source,
                    read_only=True,
                    timezone_name="America/Los_Angeles",
                    clock=lambda: CAPTURED_AT,
                )
            )
            staged_catalog = staged_source.list_publications()
            assert staged_catalog["playlists"][0]["publication_slugs"] == ["weather-fremont"]
            staged_publication = staged_source.get_publication("weather-fremont")
            assert staged_source.get_asset(staged_publication["asset_id"])["body"].startswith(b"\x89PNG")
            observed.append(source)
        return original_replace(source, destination)

    monkeypatch.setattr(bootstrap_module.os, "replace", observe_final_install)

    publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)

    assert len(observed) == 1
    assert ledger.is_dir()
    assert not observed[0].exists()


def test_bootstrap_interruption_before_final_install_removes_staging_and_leaves_target_absent(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    _write_bundle(bundle)
    original_replace = bootstrap_module.os.replace

    def interrupt_final_install(source, destination):
        if Path(destination) == ledger:
            raise KeyboardInterrupt("simulated operator interruption")
        return original_replace(source, destination)

    monkeypatch.setattr(bootstrap_module.os, "replace", interrupt_final_install)

    with pytest.raises(KeyboardInterrupt, match="operator interruption"):
        publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)

    assert not ledger.exists()
    assert [path for path in tmp_path.iterdir() if ".bootstrap-" in path.name] == []


def test_bootstrap_two_writers_leave_one_complete_winner_and_no_staging(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    ledger = tmp_path / "ledger"
    expected_raster = _write_bundle(bundle)
    original_replace = bootstrap_module.os.replace
    commit_barrier = threading.Barrier(2)
    outcomes = []

    def race_final_install(source, destination):
        if Path(destination) == ledger:
            commit_barrier.wait(timeout=10)
        return original_replace(source, destination)

    def publish():
        try:
            report = publish_cached_raster_bundle(bundle, ledger, clock=lambda: CAPTURED_AT)
            outcomes.append(("ok", report))
        except Exception as error:
            outcomes.append(("error", error))

    monkeypatch.setattr(bootstrap_module.os, "replace", race_final_install)
    writers = [threading.Thread(target=publish) for _ in range(2)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=20)

    assert all(not writer.is_alive() for writer in writers)
    assert [kind for kind, _value in outcomes].count("ok") == 1
    errors = [value for kind, value in outcomes if kind == "error"]
    assert len(errors) == 1
    assert isinstance(errors[0], FileExistsError)
    source = LedgerPublicationSource(
        PublicationModule(
            ledger,
            read_only=True,
            timezone_name="America/Los_Angeles",
            clock=lambda: CAPTURED_AT,
        )
    )
    publication = source.get_publication("weather-fremont")
    assert source.get_asset(publication["asset_id"])["body"] == expected_raster
    assert [path for path in tmp_path.iterdir() if ".bootstrap-" in path.name] == []
