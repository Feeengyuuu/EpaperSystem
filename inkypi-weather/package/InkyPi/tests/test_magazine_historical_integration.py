from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from plugins.base_plugin.presentation import (
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from plugins.magazine_covers import magazine_covers as magazine_module
from plugins.magazine_covers.historical_catalog import MagazineIssueCandidate
from plugins.magazine_covers.magazine_covers import MagazineCovers
from plugins.magazine_covers.presentation_bank import MagazinePresentationBank
from runtime.refresh_contracts import TaskCancelled
from runtime.runtime_state import PresentationCommitReceipt


class DummyDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, _key, default=None):
        return default


def comprehensive_settings(instance_uuid="magazine-history-integration"):
    settings = {
        "sources": "Latest fallback|https://magazineshop.us/collections/latest",
        "rotationMode": "sequential",
        "fitMode": "triptych",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
        "contentMode": "comprehensive",
        "selectionHoldHours": "3",
        "historicalPercent": "80",
        "categories": (
            "art_design,sports,news_politics,fashion_culture,science_nature,"
            "entertainment_music,adult,general_history"
        ),
        "includeAdult": "true",
        "historyStartYear": "",
        "overlayMode": "none",
        "catalogRefreshHours": "24",
        "latestRefreshHours": "6",
    }
    return bind_presentation_instance_identity(settings, instance_uuid)


def make_plugin(tmp_path):
    plugin = MagazineCovers({"id": "magazine_covers"})
    plugin._cache_dir = lambda: Path(tmp_path)
    return plugin


def request(request_id, when, *, origin):
    return PresentationRequestContext(
        request_id=request_id,
        requested_at=when.isoformat(),
        origin_display_commit_id=origin,
        last_receipt=None,
    )


def receipt(request_id, when, *, display):
    return PresentationCommitReceipt(
        request_id=request_id,
        committed_at=when.isoformat(),
        display_commit_id=display,
        structural_generation=1,
        settings_revision=1,
        theme_mode=None,
    )


def cover_image(index):
    rng = random.Random(index)
    image = Image.new("RGB", (80, 120))
    image.putdata(
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for _pixel in range(80 * 120)
        ]
    )
    draw = ImageDraw.Draw(image)
    draw.rectangle((4 + index % 17, 5, 24 + index % 17, 110), fill="black")
    draw.line((0, 10 + index * 7 % 100, 79, 118 - index * 5 % 100), fill="white", width=3)
    return image


def seed_comprehensive_bank(
    plugin,
    settings,
    *,
    fetched_at,
    count=18,
    initialize_current=True,
):
    sources = plugin._sources_from_settings(settings)
    bank = plugin._presentation_bank(settings, (800, 480), "2026-09-01", sources)
    document, profile = bank.load_for_data()
    categories = (
        "art_design",
        "sports",
        "news_politics",
        "fashion_culture",
        "science_nature",
        "entertainment_music",
        "adult",
        "general_history",
    )
    for index in range(count):
        publication = f"Publication {index:02d}"
        source = {
            "name": publication,
            "url": f"https://archive.org/details/magazine-{index:02d}",
        }
        bank.ingest(
            profile,
            source,
            {
                "cover_id": f"archive:issue:{index:03d}",
                "publication": publication,
                "category": categories[index % len(categories)],
                "temporal_class": "latest" if index % 5 == 4 else "historical",
                "curation_tier": "featured" if index % 3 == 0 else "discovery",
                "image_url": f"https://archive.org/download/magazine-{index:02d}/page/n0_w600.jpg",
                "page_url": f"https://archive.org/details/magazine-{index:02d}",
                "title": f"{publication} issue",
            },
            cover_image(index),
            fetched_at=fetched_at,
        )
    ready = bank.ready_records(profile, prune=False, now=fetched_at)
    if initialize_current:
        profile["current_selection"] = bank.choose_selection(
            profile,
            ready,
            "triptych",
            "sequential",
        )
    bank.save(document)
    return bank


def profile_state(plugin, instance_uuid="magazine-history-integration"):
    state = json.loads(plugin._presentation_state_path().read_text(encoding="utf-8"))
    fingerprint = state["instance_profiles"][instance_uuid]
    return state, state["profiles"][fingerprint]


def selection_cover_ids(profile, selection):
    records = {record["record_key"]: record for record in profile["records"]}
    return [records[key]["cover_id"] for key in selection["record_keys"]]


def forbid_presentation_network(plugin, monkeypatch):
    monkeypatch.setattr(
        plugin,
        "_load_cover",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("presentation used the latest-cover provider")
        ),
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("presentation fetched provider HTML")
        ),
    )
    monkeypatch.setattr(
        plugin,
        "_download_candidate_to_temp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("presentation downloaded provider media")
        ),
    )
    monkeypatch.setattr(
        magazine_module,
        "get_http_client",
        lambda: (_ for _ in ()).throw(AssertionError("presentation opened HTTP")),
        raising=False,
    )


def catalog_candidate(index, *, temporal_class="historical"):
    category = (
        "art_design",
        "sports",
        "news_politics",
        "fashion_culture",
        "science_nature",
        "entertainment_music",
        "adult",
        "general_history",
    )[index % 8]
    record_id = f"catalog-{index:03d}"
    return MagazineIssueCandidate(
        provider="internet_archive",
        source="Internet Archive / Magazine Rack",
        source_record_id=record_id,
        publication=f"Historical Publication {index:02d}",
        issue=f"Issue {index}",
        year=1930 + index,
        issue_title=f"Historical Publication {index:02d}, Issue {index}",
        issue_date=str(1930 + index),
        category=category,
        temporal_class=temporal_class,
        curation_tier="featured" if index % 3 == 0 else "discovery",
        adult=category == "adult",
        cover_url=f"https://archive.org/download/{record_id}/page/n0_w600.jpg",
        fallback_cover_url=f"https://archive.org/services/img/{record_id}",
        record_url=f"https://archive.org/details/{record_id}",
        rights="Copyright status not supplied",
        rights_uri="",
        attribution="Internet Archive test fixture",
        personal_use_only=True,
    )


def test_fresh_partial_catalog_remains_due_until_coverage_target(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    plugin._historical_catalog_path().write_text("{}", encoding="utf-8")
    now = datetime.fromtimestamp(
        plugin._historical_catalog_path().stat().st_mtime,
        timezone.utc,
    )
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    settings = comprehensive_settings(instance_uuid="catalog-coverage")

    assert plugin._historical_catalog_due(
        settings,
        candidates=tuple(catalog_candidate(index) for index in range(239)),
    ) is True
    assert plugin._historical_catalog_due(
        settings,
        candidates=tuple(catalog_candidate(index) for index in range(240)),
    ) is False


def test_comprehensive_data_hydrates_catalog_and_keeps_latest_fallback_channel(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = comprehensive_settings(instance_uuid="catalog-and-latest")
    now = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    candidates = tuple(catalog_candidate(index) for index in range(12))
    catalog_calls = []

    class FixedCatalog:
        def __init__(self, path, **kwargs):
            catalog_calls.append(("init", Path(path), kwargs))

        def load(self):
            catalog_calls.append(("load",))
            return ()

        def refresh(self, **kwargs):
            catalog_calls.append(("refresh", kwargs))
            return SimpleNamespace(
                candidates=candidates,
                added_count=len(candidates),
                request_count=4,
                errors=(),
            )

    monkeypatch.setattr(magazine_module, "MagazineHistoricalCatalog", FixedCatalog)
    loaded_kinds = []

    def load_cover(source, _dimensions, force_refresh=False, deadline=None):
        assert force_refresh is True
        assert deadline is not None
        candidate = source.get("historical_candidate")
        loaded_kinds.append("historical" if candidate else "latest")
        if candidate:
            return {
                "image": cover_image(len(loaded_kinds)),
                "image_url": candidate["cover_url"],
                "page_url": candidate["record_url"],
                "title": candidate["issue_title"],
            }
        return {
            "image": cover_image(99),
            "image_url": "https://cdn.shopify.com/latest-fallback.jpg",
            "page_url": source["url"],
            "title": source["name"],
        }

    monkeypatch.setattr(plugin, "_load_cover", load_cover)
    generated = plugin.generate_image(settings, DummyDeviceConfig())

    assert generated.size == (800, 480)
    assert any(call[0] == "refresh" for call in catalog_calls)
    assert "historical" in loaded_kinds
    assert "latest" in loaded_kinds
    _state, profile = profile_state(plugin, "catalog-and-latest")
    assert {record["temporal_class"] for record in profile["records"]} == {
        "historical",
        "latest",
    }
    assert any(record["cover_id"].startswith("mc1_") for record in profile["records"])


def test_comprehensive_data_catalog_failure_falls_back_to_legacy_latest_sources(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = comprehensive_settings(instance_uuid="catalog-failure-fallback")
    now = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)

    class FailingCatalog:
        refresh_calls = 0

        def __init__(self, _path, **_kwargs):
            pass

        def load(self):
            return ()

        def refresh(self, **_kwargs):
            type(self).refresh_calls += 1
            raise RuntimeError("all historical providers are offline")

    monkeypatch.setattr(magazine_module, "MagazineHistoricalCatalog", FailingCatalog)
    latest_calls = []

    def latest_cover(source, _dimensions, force_refresh=False, deadline=None):
        assert source.get("historical_candidate") is None
        assert force_refresh is True
        assert deadline is not None
        latest_calls.append(source["name"])
        return {
            "image": cover_image(101),
            "image_url": "https://cdn.shopify.com/latest-only.jpg",
            "page_url": source["url"],
            "title": source["name"],
        }

    monkeypatch.setattr(plugin, "_load_cover", latest_cover)
    generated = plugin.generate_image(settings, DummyDeviceConfig())

    assert generated.size == (800, 480)
    assert FailingCatalog.refresh_calls == 1
    assert latest_calls == ["Latest fallback"]
    _state, profile = profile_state(plugin, "catalog-failure-fallback")
    assert profile["records"]
    assert {record["temporal_class"] for record in profile["records"]} == {"latest"}


def test_comprehensive_data_refresh_failure_keeps_warm_historical_catalog(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = comprehensive_settings(instance_uuid="warm-catalog-fallback")
    now = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    warm_candidates = tuple(catalog_candidate(index) for index in range(8))

    class WarmFailingCatalog:
        def __init__(self, _path, **_kwargs):
            pass

        def load(self):
            return warm_candidates

        def refresh(self, **_kwargs):
            raise RuntimeError("catalog refresh failed after a previous good run")

    monkeypatch.setattr(
        magazine_module,
        "MagazineHistoricalCatalog",
        WarmFailingCatalog,
    )
    loaded_kinds = []

    def warm_or_latest_cover(source, _dimensions, force_refresh=False, deadline=None):
        assert force_refresh is True
        assert deadline is not None
        candidate = source.get("historical_candidate")
        loaded_kinds.append("historical" if candidate else "latest")
        return {
            "image": cover_image(len(loaded_kinds) + 120),
            "image_url": (
                candidate["cover_url"]
                if candidate
                else "https://cdn.shopify.com/warm-latest.jpg"
            ),
            "page_url": candidate["record_url"] if candidate else source["url"],
            "title": candidate["issue_title"] if candidate else source["name"],
        }

    monkeypatch.setattr(plugin, "_load_cover", warm_or_latest_cover)
    generated = plugin.generate_image(settings, DummyDeviceConfig())

    assert generated.size == (800, 480)
    assert "historical" in loaded_kinds
    assert "latest" in loaded_kinds
    _state, profile = profile_state(plugin, "warm-catalog-fallback")
    assert {record["temporal_class"] for record in profile["records"]} == {
        "historical",
        "latest",
    }


def test_three_hour_hold_uses_trusted_display_time_and_rotates_at_boundary(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = comprehensive_settings()
    committed_at = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    now = [committed_at]
    monkeypatch.setattr(plugin, "_now_utc", lambda: now[0])
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    seed_comprehensive_bank(plugin, settings, fetched_at=committed_at)
    forbid_presentation_network(plugin, monkeypatch)

    _state, before_profile = profile_state(plugin)
    before_progress = {
        "date_buckets": before_profile["date_buckets"],
        "selection_time_bag": before_profile["selection_time_bag"],
        "selection_time_bag_cursor": before_profile["selection_time_bag_cursor"],
        "epoch_seen_cover_ids": before_profile["epoch_seen_cover_ids"],
        "current_selection_committed_at": before_profile[
            "current_selection_committed_at"
        ],
        "committed_selection_record_keys": before_profile[
            "committed_selection_record_keys"
        ],
        "last_applied_origin_commit_id": before_profile[
            "last_applied_origin_commit_id"
        ],
    }

    first_id = "a" * 32
    plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=request(first_id, now[0], origin="display-before-magazine"),
        resolved_theme_context=None,
    )
    _state, pending_profile = profile_state(plugin)
    first_ids = selection_cover_ids(
        pending_profile,
        pending_profile["pending_selection"],
    )
    assert {
        "date_buckets": pending_profile["date_buckets"],
        "selection_time_bag": pending_profile["selection_time_bag"],
        "selection_time_bag_cursor": pending_profile["selection_time_bag_cursor"],
        "epoch_seen_cover_ids": pending_profile["epoch_seen_cover_ids"],
        "current_selection_committed_at": pending_profile[
            "current_selection_committed_at"
        ],
        "committed_selection_record_keys": pending_profile[
            "committed_selection_record_keys"
        ],
        "last_applied_origin_commit_id": pending_profile[
            "last_applied_origin_commit_id"
        ],
    } == before_progress
    plugin.reconcile_presentation_receipt(
        settings,
        receipt(first_id, committed_at, display="magazine-display-a"),
    )
    _state, committed_profile = profile_state(plugin)
    assert committed_profile["current_selection_committed_at"] == committed_at.isoformat()
    assert committed_profile["last_applied_origin_commit_id"] is None
    committed_seen = committed_profile["date_buckets"]["2026-09-01"][
        "seen_source_ids"
    ]
    records_by_cover_id = {
        record["cover_id"]: record for record in committed_profile["records"]
    }
    assert committed_seen[-3:] == [
        records_by_cover_id[cover_id]["source_id"] for cover_id in first_ids
    ]

    now[0] = committed_at + timedelta(hours=3) - timedelta(seconds=1)
    plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=request("b" * 32, now[0], origin="magazine-display-a"),
        resolved_theme_context=None,
    )
    _state, held_profile = profile_state(plugin)
    assert selection_cover_ids(held_profile, held_profile["pending_selection"]) == first_ids
    assert held_profile["current_selection_committed_at"] == committed_at.isoformat()
    assert held_profile["date_buckets"]["2026-09-01"]["seen_source_ids"] == committed_seen

    # No receipt means the 02:59:59 preparation was canceled and cannot restart the hold.
    now[0] = committed_at + timedelta(hours=3)
    plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=request("c" * 32, now[0], origin="magazine-display-a"),
        resolved_theme_context=None,
    )
    _state, due_profile = profile_state(plugin)
    due_ids = selection_cover_ids(due_profile, due_profile["pending_selection"])
    assert len(due_ids) == 3
    assert not set(due_ids).intersection(first_ids)
    assert due_profile["current_selection_committed_at"] == committed_at.isoformat()
    assert due_profile["date_buckets"]["2026-09-01"]["seen_source_ids"] == committed_seen


def test_first_pending_group_starts_hold_only_after_successful_receipt(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = comprehensive_settings(instance_uuid="first-pending-receipt")
    requested_at = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    committed_at = requested_at + timedelta(seconds=10)
    now = [requested_at]
    monkeypatch.setattr(plugin, "_now_utc", lambda: now[0])
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    seed_comprehensive_bank(
        plugin,
        settings,
        fetched_at=requested_at,
        initialize_current=False,
    )
    forbid_presentation_network(plugin, monkeypatch)

    request_id = "1" * 32
    plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=request(request_id, requested_at, origin="other-plugin-display"),
        resolved_theme_context=None,
    )
    _state, pending_profile = profile_state(plugin, "first-pending-receipt")
    pending_ids = selection_cover_ids(
        pending_profile,
        pending_profile["pending_selection"],
    )
    assert pending_profile["current_selection"] is None
    assert pending_profile["current_selection_committed_at"] is None

    plugin.reconcile_presentation_receipt(
        settings,
        receipt(request_id, committed_at, display="first-magazine-display"),
    )
    _state, committed_profile = profile_state(plugin, "first-pending-receipt")
    assert selection_cover_ids(
        committed_profile,
        committed_profile["current_selection"],
    ) == pending_ids
    assert committed_profile["current_selection_committed_at"] == committed_at.isoformat()


def test_comprehensive_receipt_failures_foreign_and_replay_do_not_advance_hold(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = comprehensive_settings()
    clock = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: clock)
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    seed_comprehensive_bank(plugin, settings, fetched_at=clock)
    forbid_presentation_network(plugin, monkeypatch)

    request_id = "d" * 32
    plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=request(request_id, clock, origin="origin-display"),
        resolved_theme_context=None,
    )
    baseline = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(
        settings,
        receipt("e" * 32, clock, display="foreign-display"),
    )
    plugin.reconcile_presentation_receipt(
        settings,
        receipt(request_id, clock, display="origin-display"),
    )
    assert plugin._presentation_state_path().read_bytes() == baseline

    plugin.reconcile_presentation_receipt(
        settings,
        receipt(request_id, clock, display="magazine-display"),
    )
    committed = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(
        settings,
        receipt(request_id, clock - timedelta(hours=1), display="replayed-display"),
    )
    assert plugin._presentation_state_path().read_bytes() == committed


def test_legacy_profile_migration_preserves_current_pending_and_is_offline(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = comprehensive_settings(instance_uuid="legacy-magazine-instance")
    now = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    seed_comprehensive_bank(plugin, settings, fetched_at=now)
    first_request = request("f" * 32, now, origin="legacy-origin")
    plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=first_request,
        resolved_theme_context=None,
    )
    state, before = profile_state(plugin, "legacy-magazine-instance")
    current = dict(before["current_selection"])
    pending = dict(before["pending_selection"])
    for record in before["records"]:
        for name in (
            "cover_id",
            "publication",
            "category",
            "temporal_class",
            "curation_tier",
            "perceptual_hash",
        ):
            record.pop(name, None)
    plugin._presentation_state_path().write_text(json.dumps(state), encoding="utf-8")

    restarted = make_plugin(tmp_path)
    monkeypatch.setattr(restarted, "_now_utc", lambda: now)
    monkeypatch.setattr(restarted, "_presentation_date_key", lambda _device: "2026-09-01")
    forbid_presentation_network(restarted, monkeypatch)
    restarted.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=first_request,
        resolved_theme_context=None,
    )
    _state, migrated = profile_state(restarted, "legacy-magazine-instance")
    assert migrated["current_selection"] == current
    assert migrated["pending_selection"] == pending
    assert all(
        record["category"]
        in {
            "art_design",
            "sports",
            "news_politics",
            "fashion_culture",
            "science_nature",
            "entertainment_music",
            "adult",
            "general_history",
        }
        for record in migrated["records"]
    )
    assert all(
        record["temporal_class"] in {"historical", "latest"}
        for record in migrated["records"]
    )
    assert all(record["cover_id"] for record in migrated["records"])


def test_historical_and_latest_media_have_separate_retention_ttls(tmp_path):
    bank = MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="ttl-instance",
        date_key="2026-09-01",
    )
    _document, profile = bank.load_for_data()
    now = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    def add(index, temporal_class, fetched_at):
        source = {
            "name": f"TTL publication {index}",
            "url": f"https://archive.org/details/ttl-{index}",
        }
        return bank.ingest(
            profile,
            source,
            {
                "cover_id": f"ttl:{index}",
                "publication": source["name"],
                "category": "general_history",
                "temporal_class": temporal_class,
                "curation_tier": "discovery",
                "image_url": f"https://archive.org/download/ttl-{index}/page/n0_w600.jpg",
                "page_url": source["url"],
                "title": source["name"],
            },
            cover_image(index),
            fetched_at=fetched_at,
        )

    historical = add(1, "historical", now - timedelta(days=29, hours=23))
    latest = add(2, "latest", now - timedelta(hours=19, minutes=59))
    ready = bank.ready_records(profile, prune=False, now=now)
    assert {historical["record_key"], latest["record_key"]}.issubset(
        {record["record_key"] for record in ready}
    )

    assert bank.record_provenance(historical, now=now) == "fresh_cache"
    assert bank.record_provenance(latest, now=now) == "fresh_cache"
    assert bank.record_provenance(
        historical,
        now=now + timedelta(hours=2),
    ) == "stale_cache"
    assert bank.record_provenance(
        latest,
        now=now + timedelta(minutes=2),
    ) == "stale_cache"


def test_latest_current_is_fresh_until_twenty_hours_then_data_reselects(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = {
        **comprehensive_settings(instance_uuid="latest-ttl-boundary"),
        "contentMode": "latest",
        "fitMode": "single",
    }
    fetched_at = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    now = [fetched_at]
    monkeypatch.setattr(plugin, "_now_utc", lambda: now[0])
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    bank = seed_comprehensive_bank(plugin, settings, fetched_at=fetched_at)
    document, profile = bank.load_for_data()
    latest_keys = [
        record["record_key"]
        for record in profile["records"]
        if record["temporal_class"] == "latest"
    ]
    assert latest_keys
    current_keys = latest_keys[:1]
    profile["current_selection"] = {
        "record_keys": current_keys,
        "request_id": None,
        "date_key": "2026-09-01",
        "layout": "triptych",
        "reset_seen": False,
    }
    profile["current_selection_committed_at"] = fetched_at.isoformat()
    profile["committed_selection_record_keys"] = list(current_keys)
    bank.save(document)

    now[0] = fetched_at + timedelta(hours=20) - timedelta(seconds=1)
    before = plugin.generate_image(
        {**settings, "_theme_render_only": True},
        DummyDeviceConfig(),
    )
    assert before.size == (800, 480)
    _state, before_profile = profile_state(plugin, "latest-ttl-boundary")
    before_bank = MagazinePresentationBank.from_profile(
        plugin._presentation_state_path(),
        plugin._presentation_media_dir(),
        _state["instance_profiles"]["latest-ttl-boundary"],
        before_profile,
    )
    assert all(
        before_bank.record_provenance(record, now=now[0]) == "fresh_cache"
        for record in before_profile["records"]
        if record["record_key"] in current_keys
    )

    refreshed_calls = []

    def refreshed_latest(source, _dimensions, force_refresh=False, deadline=None):
        assert force_refresh is True
        assert deadline is not None
        refreshed_calls.append(source["name"])
        return {
            "image": cover_image(220),
            "image_url": "https://cdn.shopify.com/latest-after-twenty-hours.jpg",
            "page_url": source["url"],
            "title": "Latest after twenty hours",
        }

    monkeypatch.setattr(plugin, "_load_cover", refreshed_latest)
    now[0] = fetched_at + timedelta(hours=20, seconds=1)
    after = plugin.generate_image(settings, DummyDeviceConfig())

    assert after.size == (800, 480)
    assert refreshed_calls == ["Latest fallback"]
    _state, after_profile = profile_state(plugin, "latest-ttl-boundary")
    after_keys = after_profile["current_selection"]["record_keys"]
    assert after_keys != current_keys
    after_records = {record["record_key"]: record for record in after_profile["records"]}
    assert all(
        before_bank.record_provenance(after_records[key], now=now[0]) == "fresh_cache"
        for key in after_keys
    )


def test_latest_refresh_refetches_cached_latest_at_six_hours_before_media_ttl(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = comprehensive_settings(instance_uuid="latest-six-hour-refresh")
    fetched_at = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    now = [fetched_at + timedelta(hours=5, minutes=59, seconds=59)]
    monkeypatch.setattr(plugin, "_now_utc", lambda: now[0])
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    bank = seed_comprehensive_bank(
        plugin,
        settings,
        fetched_at=fetched_at,
        count=36,
    )
    document, profile = bank.load_for_data()
    sources = [
        {
            "name": record["source_name"],
            "url": record["source_url"],
        }
        for record in profile["records"]
    ]
    latest_source_ids = {
        record["source_id"]
        for record in profile["records"]
        if record["temporal_class"] == "latest"
    }
    latest_names = {
        record["source_name"]
        for record in profile["records"]
        if record["temporal_class"] == "latest"
    }
    assert latest_source_ids
    profile["library_pool_key"] = plugin._pool_key(sources)
    profile["library_refreshed_at"] = fetched_at.isoformat()
    profile["library_scan_source_ids"] = []
    profile["library_scan_started_at"] = None
    profile["refill_in_progress"] = False
    bank.save(document)

    monkeypatch.setattr(
        plugin,
        "_comprehensive_data_sources",
        lambda *_args, **_kwargs: list(sources),
    )
    refreshed = []

    def refreshed_latest(source, _dimensions, force_refresh=False, deadline=None):
        assert force_refresh is True
        assert deadline is not None
        assert plugin._source_id(source) in latest_source_ids
        refreshed.append(source["name"])
        return {
            "image": cover_image(300 + len(refreshed)),
            "image_url": f"https://cdn.shopify.com/refreshed-{len(refreshed)}.jpg",
            "page_url": source["url"],
            "title": f"{source['name']} refreshed",
        }

    monkeypatch.setattr(plugin, "_load_cover", refreshed_latest)

    before_due = plugin.generate_image(settings, DummyDeviceConfig())
    assert before_due.size == (800, 480)
    assert refreshed == []

    now[0] = fetched_at + timedelta(hours=6)
    first_at_due = plugin.generate_image(settings, DummyDeviceConfig())

    assert first_at_due.size == (800, 480)
    assert len(refreshed) == magazine_module.DATA_PROVIDER_ATTEMPT_LIMIT
    assert set(refreshed).issubset(latest_names)
    _state, partial_profile = profile_state(plugin, "latest-six-hour-refresh")
    assert any(
        source_id in latest_source_ids
        for source_id in partial_profile["library_scan_source_ids"]
    )

    second_at_due = plugin.generate_image(settings, DummyDeviceConfig())

    assert second_at_due.size == (800, 480)
    assert set(refreshed) == latest_names
    assert len(refreshed) == len(latest_names)
    _state, refreshed_profile = profile_state(plugin, "latest-six-hour-refresh")
    assert refreshed_profile["library_scan_source_ids"] == []
    assert refreshed_profile["library_refreshed_at"] == now[0].isoformat()


def test_settings_switch_preserves_protected_state_but_excludes_old_ready_records(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    instance_uuid = "settings-policy-switch"
    old_settings = comprehensive_settings(instance_uuid=instance_uuid)
    now = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    sources = plugin._sources_from_settings(old_settings)
    bank = plugin._presentation_bank(old_settings, (800, 480), "2026-09-01", sources)
    document, profile = bank.load_for_data()
    old_records = []
    for index in range(9):
        source = {
            "source_id": f"old-adult-{index}",
            "name": f"Old Adult Publication {index}",
            "url": f"https://archive.org/details/old-adult-{index}",
        }
        old_records.append(
            bank.ingest(
                profile,
                source,
                {
                    "cover_id": f"old-adult-cover-{index}",
                    "publication": source["name"],
                    "category": "adult",
                    "temporal_class": "historical",
                    "curation_tier": "discovery",
                    "adult": True,
                    "year": 1960 + index,
                    "image_url": f"https://archive.org/download/old-adult-{index}/page/n0_w600.jpg",
                    "page_url": source["url"],
                    "title": source["name"],
                },
                cover_image(230 + index),
                fetched_at=now,
            )
        )
    current = {
        "record_keys": [record["record_key"] for record in old_records[:3]],
        "request_id": None,
        "date_key": "2026-09-01",
        "layout": "triptych",
        "reset_seen": False,
    }
    pending = {
        "record_keys": [record["record_key"] for record in old_records[3:6]],
        "request_id": "2" * 32,
        "origin_display_commit_id": "old-origin",
        "requested_at": now.isoformat(),
        "date_key": "2026-09-01",
        "layout": "triptych",
        "reset_seen": False,
    }
    profile["current_selection"] = current
    profile["pending_selection"] = pending
    bank.save(document)

    new_settings = {
        **old_settings,
        "contentMode": "latest",
        "includeAdult": "false",
        "historyStartYear": "2000",
        "fitMode": "single",
    }

    new_sources = plugin._sources_from_settings(new_settings)
    migrated_bank = plugin._presentation_bank(
        new_settings,
        (800, 480),
        "2026-09-01",
        new_sources,
    )
    migrated_document, migrated_profile = migrated_bank.load_for_data()
    assert migrated_profile["current_selection"] == current
    assert migrated_profile["pending_selection"] == pending
    migrated_records = {
        record["record_key"]: record for record in migrated_profile["records"]
    }
    protected_keys = set(current["record_keys"] + pending["record_keys"])
    assert protected_keys.issubset(migrated_records)
    assert all(
        migrated_records[key]["cover_id"]
        and migrated_records[key]["publication"]
        and migrated_records[key]["adult"] is True
        for key in protected_keys
    )
    migrated_bank.save(migrated_document)

    def current_latest(source, _dimensions, force_refresh=False, deadline=None):
        assert force_refresh is True
        assert deadline is not None
        return {
            "image": cover_image(250),
            "image_url": "https://cdn.shopify.com/allowed-latest.jpg",
            "page_url": source["url"],
            "title": "Allowed latest cover",
        }

    monkeypatch.setattr(plugin, "_load_cover", current_latest)
    plugin.generate_image(new_settings, DummyDeviceConfig())
    _state, migrated = profile_state(plugin, instance_uuid)
    assert migrated["pending_selection"] == pending
    migrated_records = {record["record_key"]: record for record in migrated["records"]}
    assert protected_keys.issubset(migrated_records)
    assert all(
        migrated_records[key]["cover_id"]
        and migrated_records[key]["publication"]
        and migrated_records[key]["adult"] is True
        for key in protected_keys
    )

    plugin.prepare_presentation(
        new_settings,
        DummyDeviceConfig(),
        request=request("3" * 32, now, origin="new-settings-origin"),
        resolved_theme_context=None,
    )
    _state, selected_profile = profile_state(plugin, instance_uuid)
    selected = selection_cover_ids(
        selected_profile,
        selected_profile["pending_selection"],
    )
    assert selected
    selected_records = {
        record["cover_id"]: record for record in selected_profile["records"]
    }
    assert all(
        selected_records[cover_id]["temporal_class"] == "latest"
        and selected_records[cover_id]["adult"] is False
        for cover_id in selected
    )


def test_catalog_cancellation_is_propagated_without_state_or_catalog_commit(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = comprehensive_settings(instance_uuid="catalog-cancel")
    now = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    seed_comprehensive_bank(plugin, settings, fetched_at=now)
    catalog_path = plugin._historical_catalog_path()
    catalog_path.write_bytes(b'{"last_good":true}\n')
    state_before = plugin._presentation_state_path().read_bytes()
    catalog_before = catalog_path.read_bytes()
    signal = TaskCancelled("cancel historical catalog refresh")

    class CancelingCatalog:
        def __init__(self, _path, **_kwargs):
            pass

        def load(self):
            return ()

        def refresh(self, **_kwargs):
            raise signal

    monkeypatch.setattr(magazine_module, "MagazineHistoricalCatalog", CancelingCatalog)
    with pytest.raises(TaskCancelled) as caught:
        plugin.generate_image(
            {**settings, "forceRefresh": "true"},
            DummyDeviceConfig(),
        )

    assert caught.value is signal
    assert plugin._presentation_state_path().read_bytes() == state_before
    assert catalog_path.read_bytes() == catalog_before


def test_media_cancellation_is_propagated_without_cursor_or_selection_commit(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = {
        **comprehensive_settings(instance_uuid="media-cancel"),
        "contentMode": "latest",
    }
    now = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-09-01")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    seed_comprehensive_bank(plugin, settings, fetched_at=now)
    state_before = plugin._presentation_state_path().read_bytes()
    signal = TaskCancelled("cancel magazine media loading")
    monkeypatch.setattr(
        plugin,
        "_load_cover",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(signal),
    )

    with pytest.raises(TaskCancelled) as caught:
        plugin.generate_image(
            {**settings, "forceRefresh": "true"},
            DummyDeviceConfig(),
        )

    assert caught.value is signal
    assert plugin._presentation_state_path().read_bytes() == state_before


def test_historical_image_fallback_does_not_downgrade_cancellation(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    source = plugin._candidate_source(catalog_candidate(0))
    signal = TaskCancelled("cancel historical image loading")
    monkeypatch.setattr(
        plugin,
        "_download_candidate_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(signal),
    )

    with pytest.raises(TaskCancelled) as caught:
        plugin._load_cover(
            source,
            (800, 480),
            force_refresh=True,
            deadline=plugin._monotonic() + 75.0,
        )

    assert caught.value is signal
