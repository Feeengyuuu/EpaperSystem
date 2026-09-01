from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from plugins.magazine_covers import presentation_bank


EXPECTED_CATEGORIES = (
    "art_design",
    "sports",
    "news_politics",
    "fashion_culture",
    "science_nature",
    "entertainment_music",
    "adult",
    "general_history",
)


def make_bank(tmp_path, *, date_key="2026-09-01"):
    return presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="magazine-history",
        date_key=date_key,
    )


def cover_image(index: int, *, color=None):
    image = Image.new("RGB", (48, 72), color or (40 + index * 7 % 180, 90, 150))
    draw = ImageDraw.Draw(image)
    x = 3 + (index * 5) % 31
    draw.rectangle((x, 4, min(x + 7, 46), 67), fill=(5, 5, 5))
    draw.line((0, (index * 11) % 70, 47, (index * 17 + 13) % 70), fill="white", width=2)
    signature = (index * 0x9E3779B1) & 0xFFFFFFFF
    for cell in range(16):
        if signature & (1 << cell):
            left = 2 + (cell % 4) * 11
            top = 6 + (cell // 4) * 15
            draw.rectangle((left, top, left + 5, top + 7), fill=(240, 240, 240))
    return image


def source(publication="TIME", *, source_id=None):
    slug = publication.lower().replace(" ", "-")
    value = {
        "name": publication,
        "url": f"https://example.com/magazines/{slug}",
    }
    if source_id is not None:
        value["source_id"] = source_id
    return value


def cover(index: int, *, publication="TIME", **overrides):
    value = {
        "cover_id": f"archive:{publication.lower()}:{index:04d}",
        "publication": publication,
        "category": EXPECTED_CATEGORIES[index % len(EXPECTED_CATEGORIES)],
        "temporal_class": "historical" if index % 5 else "latest",
        "curation_tier": "featured" if index % 3 == 0 else "discovery",
        "image_url": f"https://images.example.com/{publication.lower()}-{index}.jpg",
        "page_url": f"https://example.com/archive/{publication.lower()}/{index}",
        "title": f"{publication} archive cover {index}",
    }
    value.update(overrides)
    return value


def request(request_id, *, origin, requested_at):
    return SimpleNamespace(
        request_id=request_id,
        origin_display_commit_id=origin,
        requested_at=requested_at,
    )


def receipt(request_id, *, display, committed_at):
    return SimpleNamespace(
        request_id=request_id,
        display_commit_id=display,
        committed_at=committed_at,
    )


def test_historical_bank_contract_and_normalized_records(tmp_path):
    assert presentation_bank.READY_TARGET == 36
    assert presentation_bank.MAX_RECORDS_PER_PROFILE == 36
    assert presentation_bank.UNSEEN_REFILL_THRESHOLD == 12
    assert presentation_bank.MAGAZINE_CATEGORIES == EXPECTED_CATEGORIES

    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    first = bank.ingest(
        profile,
        source("TIME", source_id="archive:time:0001"),
        cover(
            1,
            publication="TIME",
            perceptual_hash="f" * 16,
            provider="internet_archive",
            source_record_id="time-1936-11-23",
            issue="Vol. 28 No. 21",
            issue_date="1936-11-23",
            year=1936,
            adult=False,
            rights="Public domain review required",
            rights_uri="http://rightsstatements.org/vocab/NoC-US/1.0/",
            attribution="Internet Archive / TIME",
            personal_use_only=True,
        ),
        cover_image(1),
        fetched_at="2026-09-01T00:00:00+00:00",
    )
    second = bank.ingest(
        profile,
        source("TIME"),
        cover(
            2,
            publication="TIME",
            category="not-a-real-category",
            temporal_class="archive",
            curation_tier="ordinary",
        ),
        cover_image(2),
        fetched_at="2026-09-01T00:01:00+00:00",
    )

    assert len(profile["records"]) == 2
    assert first["cover_id"] == "archive:time:0001"
    assert first["publication"] == "TIME"
    assert first["category"] == "sports"
    assert first["temporal_class"] == "historical"
    assert first["curation_tier"] == "discovery"
    assert len(first["perceptual_hash"]) == 16
    assert first["source_id"] == "archive:time:0001"
    assert first["provider"] == "internet_archive"
    assert first["provider_record_id"] == "time-1936-11-23"
    assert first["issue"] == "Vol. 28 No. 21"
    assert first["issue_date"] == "1936-11-23"
    assert first["year"] == 1936
    assert first["adult"] is False
    assert first["rights"] == "Public domain review required"
    assert first["rights_uri"].startswith("https://")
    assert first["attribution"] == "Internet Archive / TIME"
    assert first["personal_use_only"] is True
    assert second["category"] == "general_history"
    assert second["temporal_class"] == "historical"
    assert second["curation_tier"] == "discovery"


def test_bank_deduplicates_cover_pixels_and_perceptual_identity(tmp_path):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    first_image = cover_image(10)
    first = bank.ingest(
        profile,
        source("Archive One"),
        cover(10, publication="Archive One"),
        first_image,
        fetched_at="2026-09-01T00:00:00+00:00",
    )

    cover_duplicate = bank.ingest(
        profile,
        source("Mirror"),
        cover(
            11,
            publication="Mirror",
            cover_id=first["cover_id"],
        ),
        first_image.copy(),
        fetched_at="2026-09-01T00:01:00+00:00",
    )
    content_duplicate = bank.ingest(
        profile,
        source("Pixel Mirror"),
        cover(12, publication="Pixel Mirror"),
        first_image.copy(),
        fetched_at="2026-09-01T00:02:00+00:00",
    )
    near_duplicate_image = first_image.crop((1, 1, 47, 71)).resize((48, 72))
    perceptual_duplicate = bank.ingest(
        profile,
        source("Visual Mirror"),
        cover(13, publication="Visual Mirror"),
        near_duplicate_image,
        fetched_at="2026-09-01T00:03:00+00:00",
    )

    assert len(profile["records"]) == 1
    assert cover_duplicate["record_key"] == first["record_key"]
    assert content_duplicate["record_key"] == first["record_key"]
    assert perceptual_duplicate["record_key"] == first["record_key"]

    flat_dark = bank.ingest(
        profile,
        source("Flat Dark"),
        cover(14, publication="Flat Dark"),
        Image.new("RGB", (48, 72), (32, 32, 32)),
        fetched_at="2026-09-01T00:04:00+00:00",
    )
    flat_light = bank.ingest(
        profile,
        source("Flat Light"),
        cover(15, publication="Flat Light"),
        Image.new("RGB", (48, 72), (63, 63, 63)),
        fetched_at="2026-09-01T00:05:00+00:00",
    )
    assert flat_light["record_key"] != flat_dark["record_key"]
    assert len(profile["records"]) == 3


def test_same_cover_identity_replaces_changed_pixels_and_preserves_selection_keys(
    tmp_path,
):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    fixed_cover = cover(
        20,
        publication="Fixed CDN",
        cover_id="fixed-cdn-current-issue",
        image_url="https://images.example.com/fixed/current.jpg",
    )
    first = bank.ingest(
        profile,
        source("Fixed CDN"),
        fixed_cover,
        Image.new("RGB", (48, 72), (180, 20, 20)),
        fetched_at="2026-09-01T00:00:00+00:00",
    )
    profile["current_selection"] = {
        "record_keys": [first["record_key"]],
        "request_id": None,
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
    }
    profile["pending_selection"] = {
        "record_keys": [first["record_key"]],
        "request_id": "9" * 32,
        "origin_display_commit_id": "fixed-origin",
        "requested_at": "2026-09-01T00:01:00+00:00",
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
    }

    updated = bank.ingest(
        profile,
        source("Fixed CDN"),
        {**fixed_cover, "title": "Fixed CDN updated pixels"},
        Image.new("RGB", (48, 72), (20, 20, 180)),
        fetched_at="2026-09-01T00:02:00+00:00",
    )

    assert updated["record_key"] == first["record_key"]
    assert updated["content_hash"] != first["content_hash"]
    assert updated["title"] == "Fixed CDN updated pixels"
    assert profile["current_selection"]["record_keys"] == [first["record_key"]]
    assert profile["pending_selection"]["record_keys"] == [first["record_key"]]
    assert len(profile["records"]) == 1
    loaded = bank.load_media(updated, now="2026-09-01T00:02:01+00:00")
    assert loaded.getpixel((0, 0)) == (20, 20, 180)


def test_content_addressed_replacement_is_rollback_safe_until_state_save(
    tmp_path,
    monkeypatch,
):
    bank = make_bank(tmp_path)
    document, profile = bank.load_for_data()
    fixed_cover = cover(
        25,
        publication="Transactional CDN",
        cover_id="transactional-current-issue",
        image_url="https://images.example.com/transactional/current.jpg",
    )
    first = bank.ingest(
        profile,
        source("Transactional CDN"),
        fixed_cover,
        Image.new("RGB", (48, 72), (180, 20, 20)),
        fetched_at="2026-09-01T00:00:00+00:00",
    )
    other = bank.ingest(
        profile,
        source("Evictable Archive"),
        cover(26, publication="Evictable Archive"),
        cover_image(526),
        fetched_at="2026-09-01T00:00:00+00:00",
    )
    profile["current_selection"] = {
        "record_keys": [first["record_key"]],
        "request_id": None,
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
    }
    profile["pending_selection"] = {
        "record_keys": [first["record_key"]],
        "request_id": "7" * 32,
        "origin_display_commit_id": "transaction-origin",
        "requested_at": "2026-09-01T00:01:00+00:00",
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
    }
    bank.save(document)
    old_media_key = first["media_key"]
    old_content_hash = first["content_hash"]
    old_path = bank.media.path(old_media_key, suffix=".png")
    other_path = bank.media.path(other["media_key"], suffix=".png")
    monkeypatch.setattr(presentation_bank, "MEDIA_MAX_FILES", 2)

    updated = bank.ingest(
        profile,
        source("Transactional CDN"),
        fixed_cover,
        Image.new("RGB", (48, 72), (20, 20, 180)),
        fetched_at="2026-09-01T00:02:00+00:00",
    )

    assert updated["media_key"] == updated["content_hash"]
    assert updated["media_key"] != old_media_key
    assert old_path.exists()
    assert not other_path.exists()

    rolled_back_bank = make_bank(tmp_path)
    rolled_back_document, rolled_back_profile = rolled_back_bank.load_warm()
    rolled_back = next(
        record
        for record in rolled_back_profile["records"]
        if record["record_key"] == first["record_key"]
    )
    assert rolled_back["media_key"] == old_media_key
    assert rolled_back["content_hash"] == old_content_hash
    assert rolled_back_bank.load_media(
        rolled_back,
        now="2026-09-01T00:02:01+00:00",
    ).getpixel((0, 0)) == (180, 20, 20)

    committed = rolled_back_bank.ingest(
        rolled_back_profile,
        source("Transactional CDN"),
        fixed_cover,
        Image.new("RGB", (48, 72), (20, 20, 180)),
        fetched_at="2026-09-01T00:03:00+00:00",
    )
    rolled_back_bank.save(rolled_back_document)
    final_bank = make_bank(tmp_path)
    _final_document, final_profile = final_bank.load_warm()
    final = next(
        record
        for record in final_profile["records"]
        if record["record_key"] == first["record_key"]
    )
    assert final["media_key"] == committed["content_hash"]
    assert final_bank.load_media(
        final,
        now="2026-09-01T00:03:01+00:00",
    ).getpixel((0, 0)) == (20, 20, 180)


def test_load_media_rejects_valid_png_whose_payload_hash_was_tampered(tmp_path):
    bank = make_bank(tmp_path)
    document, profile = bank.load_for_data()
    record = bank.ingest(
        profile,
        source("Integrity Archive"),
        cover(27, publication="Integrity Archive"),
        Image.new("RGB", (48, 72), (180, 20, 20)),
        fetched_at="2026-09-01T00:00:00+00:00",
    )
    bank.save(document)
    tampered = BytesIO()
    Image.new("RGB", (48, 72), (20, 20, 180)).save(
        tampered,
        format="PNG",
        optimize=True,
    )
    bank.media.path(record["media_key"], suffix=".png").write_bytes(
        tampered.getvalue()
    )

    with pytest.raises(RuntimeError, match="hash|integrity"):
        bank.load_media(record, now="2026-09-01T00:01:00+00:00")


def test_near_capacity_legacy_url_media_stays_in_place_while_hash_is_backfilled(
    tmp_path,
    monkeypatch,
):
    bank = make_bank(tmp_path)
    document, profile = bank.load_for_data()
    records = []
    for index in range(3):
        publication = f"Legacy Capacity Publication {index}"
        records.append(
            bank.ingest(
                profile,
                source(publication),
                cover(index + 30, publication=publication),
                cover_image(index + 30),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    legacy_keys = []
    for record in records:
        content_path = bank.media.path(record["media_key"], suffix=".png")
        payload = content_path.read_bytes()
        legacy_key = sha256(record["image_url"].encode("utf-8")).hexdigest()
        bank.media.put_bytes(legacy_key, payload, suffix=".png")
        content_path.unlink()
        record["media_key"] = legacy_key
        legacy_keys.append(legacy_key)
    records[0].pop("content_hash")
    profile["current_selection"] = {
        "record_keys": [records[0]["record_key"]],
        "request_id": None,
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
    }
    profile["pending_selection"] = {
        "record_keys": [records[1]["record_key"]],
        "request_id": "9" * 32,
        "origin_display_commit_id": "legacy-capacity-origin",
        "requested_at": "2026-09-01T00:01:00+00:00",
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
    }
    filler_key = "f" * 64
    bank.media.put_bytes(filler_key, b"filler", suffix=".png")
    bank.save(document)
    monkeypatch.setattr(presentation_bank, "MEDIA_MAX_FILES", 4)

    restarted = make_bank(tmp_path)
    _restarted_document, restarted_profile = restarted.load_warm()
    restarted_by_key = {
        record["record_key"]: record for record in restarted_profile["records"]
    }

    assert len(list(restarted.media_dir.glob("*.png"))) == 4
    assert [
        restarted_by_key[record["record_key"]]["media_key"] for record in records
    ] == legacy_keys
    assert restarted_by_key[records[0]["record_key"]]["content_hash"]
    for record in records:
        loaded = restarted.load_media(
            restarted_by_key[record["record_key"]],
            now="2026-09-01T00:02:00+00:00",
        )
        assert loaded.size == (48, 72)


def test_legacy_url_identity_migrates_and_survives_fallback_to_primary_reload(
    tmp_path,
):
    bank = make_bank(tmp_path)
    document, profile = bank.load_for_data()
    stable_source = source("Stable Archive", source_id="archive:stable:issue-1")
    fallback_cover = cover(
        21,
        publication="Stable Archive",
        cover_id="archive:stable:issue-1",
        image_url="https://archive.example.com/services/img/issue-1",
    )
    first = bank.ingest(
        profile,
        stable_source,
        fallback_cover,
        Image.new("RGB", (48, 72), (170, 40, 20)),
        fetched_at="2026-09-01T00:00:00+00:00",
    )
    legacy_key = sha256(
        f"{first['source_id']}\0{first['image_url']}".encode("utf-8")
    ).hexdigest()
    profile["records"][0]["record_key"] = legacy_key
    rotation_state = {
        "category_bags": {
            category: [legacy_key] if category == first["category"] else []
            for category in EXPECTED_CATEGORIES
        },
        "selection_time_bag": [legacy_key],
        "selection_time_bag_cursor": 0,
        "selection_time_bag_historical_percent": 80,
    }
    profile["current_selection"] = {
        "record_keys": [legacy_key],
        "request_id": None,
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
        "rotation_state": rotation_state,
    }
    profile["pending_selection"] = {
        "record_keys": [legacy_key],
        "request_id": "8" * 32,
        "origin_display_commit_id": "stable-origin",
        "requested_at": "2026-09-01T00:01:00+00:00",
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
        "rotation_state": rotation_state,
    }
    profile["current_selection_committed_at"] = "2026-09-01T00:00:30+00:00"
    profile["committed_selection_record_keys"] = [legacy_key]
    profile["category_bags"][first["category"]] = [legacy_key]
    profile["selection_time_bag"] = [legacy_key]
    profile["selection_time_bag_historical_percent"] = 80
    bank.save(document)

    reloaded_bank = make_bank(tmp_path)
    reloaded_document, reloaded_profile = reloaded_bank.load_warm()
    migrated = reloaded_profile["records"][0]
    stable_key = migrated["record_key"]
    assert stable_key != legacy_key
    assert reloaded_profile["current_selection"]["record_keys"] == [stable_key]
    assert reloaded_profile["pending_selection"]["record_keys"] == [stable_key]
    assert reloaded_profile["committed_selection_record_keys"] == [stable_key]
    assert reloaded_profile["selection_time_bag"] == [migrated["cover_id"]]
    assert reloaded_profile["category_bags"][migrated["category"]] == [
        migrated["cover_id"]
    ]
    assert reloaded_profile["pending_selection"]["rotation_state"][
        "selection_time_bag"
    ] == [migrated["cover_id"]]

    primary_cover = {
        **fallback_cover,
        "image_url": "https://archive.example.com/download/issue-1/page/n0_w600.jpg",
        "title": "Stable Archive primary image",
    }
    updated = reloaded_bank.ingest(
        reloaded_profile,
        stable_source,
        primary_cover,
        Image.new("RGB", (48, 72), (20, 40, 170)),
        fetched_at="2026-09-01T00:02:00+00:00",
    )
    assert updated["record_key"] == stable_key
    assert updated["media_key"] != migrated["media_key"]
    assert updated["image_url"] == primary_cover["image_url"]
    assert reloaded_profile["current_selection"]["record_keys"] == [stable_key]
    assert reloaded_profile["pending_selection"]["record_keys"] == [stable_key]
    assert reloaded_bank.load_media(
        updated,
        now="2026-09-01T00:02:01+00:00",
    ).getpixel((0, 0)) == (20, 40, 170)
    reloaded_bank.save(reloaded_document)

    final_bank = make_bank(tmp_path)
    _final_document, final_profile = final_bank.load_warm()
    assert final_profile["records"][0]["record_key"] == stable_key
    assert final_profile["current_selection"]["record_keys"] == [stable_key]
    assert final_profile["pending_selection"]["record_keys"] == [stable_key]
    assert final_profile["committed_selection_record_keys"] == [stable_key]


def test_unseen_low_water_interface_excludes_current_and_pending(tmp_path):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    records = [
        bank.ingest(
            profile,
            source(f"Publication {index}"),
            cover(index, publication=f"Publication {index}"),
            cover_image(index),
            fetched_at="2026-09-01T00:00:00+00:00",
        )
        for index in range(13)
    ]
    ready = bank.ready_records(
        profile,
        prune=False,
        now="2026-09-01T00:10:00+00:00",
    )

    assert bank.unseen_ready_count(profile, ready) == 13
    assert bank.needs_unseen_refill(profile, ready) is False

    profile["current_selection"] = {
        "record_keys": [records[0]["record_key"], records[1]["record_key"]],
        "request_id": None,
        "date_key": "2026-09-01",
        "layout": "triptych",
        "reset_seen": False,
    }
    assert bank.unseen_ready_count(profile, ready) == 11
    assert bank.needs_unseen_refill(profile, ready) is True


def test_persistent_time_bag_has_temporal_and_editorial_mix(tmp_path):
    bank = make_bank(tmp_path)
    document, profile = bank.load_for_data()
    records = []
    for index in range(24):
        temporal_class = "historical" if index < 19 else "latest"
        if temporal_class == "historical":
            curation_tier = "featured" if index < 6 else "discovery"
        else:
            curation_tier = "featured" if index == 19 else "discovery"
        publication = f"Archive Publication {index}"
        records.append(
            bank.ingest(
                profile,
                source(publication),
                cover(
                    index + 40,
                    publication=publication,
                    category=EXPECTED_CATEGORIES[index % len(EXPECTED_CATEGORIES)],
                    temporal_class=temporal_class,
                    curation_tier=curation_tier,
                ),
                cover_image(index + 40),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    assert len(profile["records"]) == 24
    ready = bank.ready_records(
        profile,
        prune=False,
        now="2026-09-01T00:10:00+00:00",
    )

    selections = []
    active_bank = bank
    active_document = document
    active_profile = profile
    active_ready = ready
    for index in range(5):
        before = {
            "category_bags": dict(active_profile["category_bags"]),
            "selection_time_bag": list(active_profile["selection_time_bag"]),
            "selection_time_bag_cursor": active_profile["selection_time_bag_cursor"],
            "selection_epoch": active_profile["selection_epoch"],
            "epoch_seen_cover_ids": list(active_profile["epoch_seen_cover_ids"]),
        }
        selection = active_bank.choose_selection(
            active_profile,
            active_ready,
            "triptych",
            "sequential",
        )
        assert {
            "category_bags": active_profile["category_bags"],
            "selection_time_bag": active_profile["selection_time_bag"],
            "selection_time_bag_cursor": active_profile["selection_time_bag_cursor"],
            "selection_epoch": active_profile["selection_epoch"],
            "epoch_seen_cover_ids": active_profile["epoch_seen_cover_ids"],
        } == before
        selections.append(selection)
        request_id = f"{index + 1:032x}"
        active_bank.set_pending(
            active_document,
            active_profile,
            request(
                request_id,
                origin=f"origin-{index}",
                requested_at=f"2026-09-01T00:{index:02d}:00+00:00",
            ),
            selection,
        )
        assert active_profile["selection_time_bag_cursor"] == before["selection_time_bag_cursor"]
        active_bank.reconcile_receipt(
            active_document,
            active_profile,
            receipt(
                request_id,
                display=f"display-{index}",
                committed_at=f"2026-09-01T00:{index:02d}:30+00:00",
            ),
        )
        if index == 0:
            active_bank = make_bank(tmp_path)
            active_document, active_profile = active_bank.load_warm()
            active_ready = active_bank.ready_records(
                active_profile,
                prune=False,
                now="2026-09-01T00:10:00+00:00",
            )

    record_by_key = {record["record_key"]: record for record in active_profile["records"]}
    selected = [record_by_key[key] for selection in selections for key in selection["record_keys"]]
    assert len(selected) == 15
    assert len({record["cover_id"] for record in selected}) == 15
    assert sum(record["temporal_class"] == "historical" for record in selected) == 12
    assert sum(record["temporal_class"] == "latest" for record in selected) == 3
    for offset in range(0, 15, 3):
        group = selected[offset : offset + 3]
        assert [record["curation_tier"] for record in group].count("featured") == 1
        assert [record["curation_tier"] for record in group].count("discovery") == 2
        assert len({record["category"] for record in group}) == 3
        assert len({record["publication"] for record in group}) == 3

    assert len(active_profile["selection_time_bag"]) == 15
    assert active_profile["selection_time_bag_cursor"] == 15
    assert set(active_profile["category_bags"]) == set(EXPECTED_CATEGORIES)


def test_historical_percent_controls_the_persistent_fifteen_slot_bag(tmp_path):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    for index in range(30):
        temporal_class = "historical" if index < 15 else "latest"
        publication = f"Percent Publication {index}"
        bank.ingest(
            profile,
            source(publication),
            cover(
                index + 140,
                publication=publication,
                temporal_class=temporal_class,
                curation_tier="featured" if index % 3 == 0 else "discovery",
            ),
            cover_image(index + 140),
            fetched_at="2026-09-01T00:00:00+00:00",
        )
    ready = bank.ready_records(profile, prune=False, now="2026-09-01T00:01:00+00:00")
    by_cover_id = {record["cover_id"]: record for record in ready}

    latest_only = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        historical_percent=0,
    )
    historical_only = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        historical_percent=100,
    )

    for selection, expected in ((latest_only, "latest"), (historical_only, "historical")):
        bag = selection["rotation_state"]["selection_time_bag"]
        assert len(bag) == 15
        assert {by_cover_id[cover_id]["temporal_class"] for cover_id in bag} == {expected}


def test_fifteen_slot_bag_keeps_exact_temporal_mix_when_latest_has_no_featured(
    tmp_path,
):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    records = []
    for index in range(16):
        historical = index < 13
        publication = "Temporal Shape Weekly"
        records.append(
            bank.ingest(
                profile,
                source(publication),
                cover(
                    index + 180,
                    publication=publication,
                    category="sports",
                    temporal_class="historical" if historical else "latest",
                    curation_tier=(
                        "featured" if historical and index < 5 else "discovery"
                    ),
                ),
                cover_image(index + 180),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    ready = bank.ready_records(
        profile,
        prune=False,
        now="2026-09-01T00:01:00+00:00",
    )
    selection = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        historical_percent=80,
    )
    bag = selection["rotation_state"]["selection_time_bag"]
    by_cover_id = {record["cover_id"]: record for record in records}
    bag_records = [by_cover_id[cover_id] for cover_id in bag]

    assert len(bag_records) == 15
    assert sum(record["temporal_class"] == "historical" for record in bag_records) == 12
    assert sum(record["temporal_class"] == "latest" for record in bag_records) == 3
    for offset in range(0, 15, 3):
        group = bag_records[offset : offset + 3]
        assert [record["curation_tier"] for record in group].count("featured") == 1
        assert [record["curation_tier"] for record in group].count("discovery") == 2


def test_epoch_and_bag_state_advance_only_after_matching_successful_receipt(tmp_path):
    bank = make_bank(tmp_path)
    document, profile = bank.load_for_data()
    records = []
    for index in range(8):
        publication = f"Epoch Publication {index}"
        records.append(
            bank.ingest(
                profile,
                source(publication),
                cover(index + 200, publication=publication),
                cover_image(index + 200),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    ready = bank.ready_records(profile, prune=False, now="2026-09-01T00:01:00+00:00")
    all_cover_ids = [record["cover_id"] for record in records]
    profile["epoch_seen_cover_ids"] = list(all_cover_ids)
    profile["recent_cover_ids"] = list(all_cover_ids[-3:])
    baseline_epoch = profile["selection_epoch"]

    selection = bank.choose_selection(profile, ready, "triptych", "sequential")
    assert selection["reset_seen"] is True
    assert profile["selection_epoch"] == baseline_epoch
    assert profile["epoch_seen_cover_ids"] == all_cover_ids
    assert profile["selection_time_bag"] == []

    bank.set_pending(
        document,
        profile,
        request(
            "a" * 32,
            origin="epoch-origin",
            requested_at="2026-09-01T00:02:00+00:00",
        ),
        selection,
    )
    assert profile["selection_epoch"] == baseline_epoch
    assert profile["selection_time_bag"] == []

    bank.reconcile_receipt(
        document,
        profile,
        receipt(
            "a" * 32,
            display="epoch-display",
            committed_at="2026-09-01T00:02:01+00:00",
        ),
    )
    selected_ids = {
        record["cover_id"]
        for record in records
        if record["record_key"] in selection["record_keys"]
    }
    assert profile["selection_epoch"] == baseline_epoch + 1
    assert profile["epoch_guard_cover_ids"] == all_cover_ids[-3:]
    assert set(profile["epoch_seen_cover_ids"]) == selected_ids
    assert profile["selection_time_bag_cursor"] == 3


def test_partial_editorial_bank_uses_only_complete_compliant_triptychs(tmp_path):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    records = []
    for index in range(6):
        publication = f"Partial Publication {index}"
        records.append(
            bank.ingest(
                profile,
                source(publication),
                cover(
                    index + 260,
                    publication=publication,
                    category=EXPECTED_CATEGORIES[index],
                    temporal_class="historical" if index < 5 else "latest",
                    curation_tier="featured" if index in {0, 3} else "discovery",
                ),
                cover_image(index + 260),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    ready = bank.ready_records(profile, prune=False, now="2026-09-01T00:01:00+00:00")

    selection = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
    )
    bag = selection["rotation_state"]["selection_time_bag"]
    by_cover_id = {record["cover_id"]: record for record in records}

    assert len(bag) == 6
    assert len(selection["record_keys"]) == 3
    for offset in range(0, len(bag), 3):
        group = [by_cover_id[cover_id] for cover_id in bag[offset : offset + 3]]
        assert [record["curation_tier"] for record in group].count("featured") == 1
        assert len({record["category"] for record in group}) == 3
        assert len({record["publication"] for record in group}) == 3


def test_editorial_triptych_allows_single_category_and_publication_fallback(tmp_path):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    records = []
    for index in range(3):
        records.append(
            bank.ingest(
                profile,
                source("Sports Weekly"),
                cover(
                    index + 280,
                    publication="Sports Weekly",
                    category="sports",
                    temporal_class="historical",
                    curation_tier="featured" if index == 0 else "discovery",
                ),
                cover_image(index + 280),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    ready = bank.ready_records(profile, prune=False, now="2026-09-01T00:01:00+00:00")

    selection = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
    )
    selected = {
        record["record_key"]: record for record in records
        if record["record_key"] in selection["record_keys"]
    }

    assert len(selected) == 3
    assert [record["curation_tier"] for record in selected.values()].count("featured") == 1
    assert {record["category"] for record in selected.values()} == {"sports"}
    assert {record["publication"] for record in selected.values()} == {"Sports Weekly"}


def test_editorial_triptych_shortage_fails_or_keeps_committed_compliant_group(tmp_path):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    records = []
    for index in range(36):
        publication = f"Shortage Publication {index}"
        records.append(
            bank.ingest(
                profile,
                source(publication),
                cover(
                    index + 220,
                    publication=publication,
                    category=EXPECTED_CATEGORIES[index] if index < 3 else "general_history",
                    temporal_class="historical" if index < 15 else "latest",
                    curation_tier="featured" if index == 0 else "discovery",
                ),
                cover_image(index + 220),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    ready = bank.ready_records(profile, prune=False, now="2026-09-01T00:01:00+00:00")

    with pytest.raises(RuntimeError, match="compliant editorial triptych"):
        bank.choose_selection(
            profile,
            ready[1:13],
            "triptych",
            "sequential",
            selection_hold_hours=3,
        )

    sparse_tier_selection = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
    )
    by_key = {record["record_key"]: record for record in records}
    sparse_tier_records = [
        by_key[record_key] for record_key in sparse_tier_selection["record_keys"]
    ]
    assert len(sparse_tier_records) == 3
    assert (
        [record["curation_tier"] for record in sparse_tier_records].count("featured")
        == 1
    )
    assert (
        [record["curation_tier"] for record in sparse_tier_records].count("discovery")
        == 2
    )

    current = {
        "record_keys": [record["record_key"] for record in records[:3]],
        "request_id": None,
        "date_key": "2026-09-01",
        "layout": "triptych",
        "reset_seen": False,
    }
    profile["current_selection"] = current
    profile["current_selection_committed_at"] = "2026-09-01T00:00:00+00:00"
    profile["committed_selection_record_keys"] = list(current["record_keys"])
    held = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        now="2026-09-01T04:00:00+00:00",
    )
    assert held == current


def test_same_instance_settings_migration_preserves_protected_selections(tmp_path):
    sources = [source("Migration Library")]
    omitted_defaults = presentation_bank.settings_key({}, sources)
    explicit_defaults = presentation_bank.settings_key(
        {
            "contentMode": "comprehensive",
            "categories": ",".join(EXPECTED_CATEGORIES),
            "includeAdult": "true",
            "historyStartYear": "",
            "historicalPercent": "80",
            "overlayMode": "none",
        },
        sources,
    )
    changed_eligibility = presentation_bank.settings_key(
        {
            "contentMode": "comprehensive",
            "categories": "sports,art_design",
            "includeAdult": "false",
            "historyStartYear": "1950",
            "historicalPercent": "60",
            "overlayMode": "source",
        },
        sources,
    )
    assert omitted_defaults == explicit_defaults
    assert changed_eligibility != omitted_defaults

    old_bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key=omitted_defaults,
        instance_uuid="magazine-history",
        date_key="2026-09-01",
    )
    document, profile = old_bank.load_for_data()
    records = []
    for index in range(2):
        publication = f"Migration Publication {index}"
        records.append(
            old_bank.ingest(
                profile,
                source(publication),
                cover(index + 240, publication=publication),
                cover_image(index + 240),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    profile["current_selection"] = {
        "record_keys": [records[0]["record_key"]],
        "request_id": None,
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
    }
    profile["pending_selection"] = {
        "record_keys": [records[1]["record_key"]],
        "request_id": "b" * 32,
        "origin_display_commit_id": "migration-origin",
        "requested_at": "2026-09-01T00:01:00+00:00",
        "date_key": "2026-09-01",
        "layout": "single",
        "reset_seen": False,
    }
    current = dict(profile["current_selection"])
    pending = dict(profile["pending_selection"])
    old_bank.save(document)

    migrated_bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="d" * 64,
        base_fingerprint="e" * 64,
        profile_settings_key=changed_eligibility,
        instance_uuid="magazine-history",
        date_key="2026-09-01",
    )
    migrated_document, migrated_profile = migrated_bank.load_warm()

    assert migrated_document["instance_profiles"]["magazine-history"] == "d" * 64
    assert migrated_profile["settings_key"] == changed_eligibility
    assert migrated_profile["current_selection"] == current
    assert migrated_profile["pending_selection"] == pending
    assert len(migrated_bank.protected_records(migrated_profile)) == 2


def test_uncommitted_degraded_current_upgrades_when_triptych_becomes_available(
    tmp_path,
):
    bank = make_bank(tmp_path)
    document, profile = bank.load_for_data()
    first = bank.ingest(
        profile,
        source("Cold Discovery"),
        cover(
            300,
            publication="Cold Discovery",
            category="art_design",
            temporal_class="historical",
            curation_tier="discovery",
        ),
        cover_image(300),
        fetched_at="2026-09-01T00:00:00+00:00",
    )
    ready = bank.ready_records(profile, prune=False, now="2026-09-01T00:01:00+00:00")
    degraded = bank.ensure_current(
        document,
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        now="2026-09-01T00:01:00+00:00",
    )
    assert degraded["record_keys"] == [first["record_key"]]
    assert profile["current_selection_committed_at"] is None

    for index, tier in ((301, "featured"), (302, "discovery")):
        publication = f"Refill Publication {index}"
        bank.ingest(
            profile,
            source(publication),
            cover(
                index,
                publication=publication,
                category=EXPECTED_CATEGORIES[index - 300],
                temporal_class="historical",
                curation_tier=tier,
            ),
            cover_image(index),
            fetched_at="2026-09-01T00:02:00+00:00",
        )
    ready = bank.ready_records(profile, prune=False, now="2026-09-01T00:03:00+00:00")

    upgraded = bank.ensure_current(
        document,
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        now="2026-09-01T00:03:00+00:00",
    )
    selected_records = {
        record["record_key"]: record
        for record in profile["records"]
        if record["record_key"] in upgraded["record_keys"]
    }

    assert len(upgraded["record_keys"]) == 3
    assert len(set(upgraded["record_keys"])) == 3
    assert (
        [record["curation_tier"] for record in selected_records.values()].count(
            "featured"
        )
        == 1
    )
    assert profile["current_selection_committed_at"] is None


def test_three_hour_hold_starts_on_matching_receipt_and_same_group_does_not_extend_it(
    tmp_path,
):
    bank = make_bank(tmp_path)
    document, profile = bank.load_for_data()
    for index in range(18):
        publication = f"Hold Publication {index}"
        bank.ingest(
            profile,
            source(publication),
            cover(
                index + 80,
                publication=publication,
                temporal_class="historical" if index < 15 else "latest",
                curation_tier="featured" if index % 3 == 0 else "discovery",
            ),
            cover_image(index + 80),
            fetched_at="2026-09-01T00:00:00+00:00",
        )
    ready = bank.ready_records(
        profile,
        prune=False,
        now="2026-09-01T00:00:00+00:00",
    )
    first = bank.ensure_current(
        document,
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        now="2026-09-01T00:00:00+00:00",
    )
    assert profile["current_selection_committed_at"] is None

    before_origin = deepcopy(profile)
    assert bank.apply_trusted_origin(
        document,
        profile,
        request(
            "1" * 32,
            origin="display-first",
            requested_at="2026-09-01T00:00:00+00:00",
        ),
    ) is None
    assert profile == before_origin
    bank.set_pending(
        document,
        profile,
        request(
            "1" * 32,
            origin="display-first",
            requested_at="2026-09-01T00:00:00+00:00",
        ),
        first,
    )
    bank.reconcile_receipt(
        document,
        profile,
        receipt(
            "1" * 32,
            display="display-first-receipt",
            committed_at="2026-09-01T00:00:00+00:00",
        ),
    )
    assert profile["current_selection_committed_at"] == "2026-09-01T00:00:00+00:00"

    held = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        now="2026-09-01T02:59:59+00:00",
    )
    assert held["record_keys"] == first["record_keys"]
    bank.set_pending(
        document,
        profile,
        request(
            "2" * 32,
            origin="display-first",
            requested_at="2026-09-01T02:59:59+00:00",
        ),
        held,
    )
    bank.reconcile_receipt(
        document,
        profile,
        receipt(
            "2" * 32,
            display="display-first-replay",
            committed_at="2026-09-01T02:59:59+00:00",
        ),
    )
    assert profile["current_selection_committed_at"] == "2026-09-01T00:00:00+00:00"

    changed = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        now="2026-09-01T03:00:00+00:00",
    )
    assert changed["record_keys"] != first["record_keys"]
    assert profile["current_selection_committed_at"] == "2026-09-01T00:00:00+00:00"
    bank.set_pending(
        document,
        profile,
        request(
            "3" * 32,
            origin="display-first-replay",
            requested_at="2026-09-01T03:00:00+00:00",
        ),
        changed,
    )
    bank.reconcile_receipt(
        document,
        profile,
        receipt(
            "3" * 32,
            display="display-second",
            committed_at="2026-09-01T03:00:01+00:00",
        ),
    )
    assert profile["current_selection_committed_at"] == "2026-09-01T03:00:01+00:00"


def test_non_multiple_epoch_tail_rolls_over_transactionally_without_recent_repeats(
    tmp_path,
):
    bank = make_bank(tmp_path)
    document, profile = bank.load_for_data()
    records = []
    for index in range(24):
        publication = f"Tail Publication {index}"
        records.append(
            bank.ingest(
                profile,
                source(publication),
                cover(
                    index + 400,
                    publication=publication,
                    temporal_class="historical",
                    curation_tier="featured" if index % 3 == 0 else "discovery",
                ),
                cover_image(index + 400),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    ready = bank.ready_records(
        profile,
        prune=False,
        now="2026-09-01T04:00:00+00:00",
    )
    current = records[:3]
    current_keys = [record["record_key"] for record in current]
    current_ids = {record["cover_id"] for record in current}
    tail = records[-2:]
    tail_ids = {record["cover_id"] for record in tail}
    recent_guard = [record["cover_id"] for record in records[6:18]]
    profile["current_selection"] = {
        "record_keys": current_keys,
        "request_id": "0" * 32,
        "date_key": "2026-09-01",
        "layout": "triptych",
        "reset_seen": False,
    }
    profile["current_selection_committed_at"] = "2026-09-01T00:00:00+00:00"
    profile["committed_selection_record_keys"] = current_keys
    profile["epoch_seen_cover_ids"] = [
        record["cover_id"] for record in records if record not in tail
    ]
    profile["recent_cover_ids"] = recent_guard
    baseline = {
        "selection_epoch": profile["selection_epoch"],
        "epoch_seen_cover_ids": list(profile["epoch_seen_cover_ids"]),
        "epoch_guard_cover_ids": list(profile["epoch_guard_cover_ids"]),
        "recent_cover_ids": list(profile["recent_cover_ids"]),
        "selection_time_bag": list(profile["selection_time_bag"]),
        "selection_time_bag_cursor": profile["selection_time_bag_cursor"],
    }

    selection = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        now="2026-09-01T04:00:00+00:00",
    )
    by_key = {record["record_key"]: record for record in records}
    selected = [by_key[record_key] for record_key in selection["record_keys"]]
    selected_ids = {record["cover_id"] for record in selected}

    assert selection["reset_seen"] is True
    assert len(selected) == 3
    assert [record["curation_tier"] for record in selected].count("featured") == 1
    assert [record["curation_tier"] for record in selected].count("discovery") == 2
    assert tail_ids.issubset(selected_ids)
    assert selected_ids.isdisjoint(current_ids)
    assert selected_ids.isdisjoint(recent_guard)
    assert {
        "selection_epoch": profile["selection_epoch"],
        "epoch_seen_cover_ids": profile["epoch_seen_cover_ids"],
        "epoch_guard_cover_ids": profile["epoch_guard_cover_ids"],
        "recent_cover_ids": profile["recent_cover_ids"],
        "selection_time_bag": profile["selection_time_bag"],
        "selection_time_bag_cursor": profile["selection_time_bag_cursor"],
    } == baseline

    bank.set_pending(
        document,
        profile,
        request(
            "c" * 32,
            origin="tail-origin",
            requested_at="2026-09-01T04:00:00+00:00",
        ),
        selection,
    )
    assert profile["selection_epoch"] == baseline["selection_epoch"]
    assert profile["epoch_seen_cover_ids"] == baseline["epoch_seen_cover_ids"]
    bank.reconcile_receipt(
        document,
        profile,
        receipt(
            "c" * 32,
            display="tail-display",
            committed_at="2026-09-01T04:00:01+00:00",
        ),
    )

    assert profile["selection_epoch"] == baseline["selection_epoch"] + 1
    assert profile["epoch_guard_cover_ids"] == recent_guard
    assert set(profile["epoch_seen_cover_ids"]) == selected_ids
    assert profile["selection_time_bag_cursor"] == 3


def test_tier_short_epoch_tail_combines_with_guarded_new_epoch_candidates(tmp_path):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    records = []
    for index in range(24):
        publication = f"Tier Tail Publication {index}"
        records.append(
            bank.ingest(
                profile,
                source(publication),
                cover(
                    index + 440,
                    publication=publication,
                    temporal_class="historical",
                    curation_tier=(
                        "discovery"
                        if index >= 21
                        else ("featured" if index % 3 == 0 else "discovery")
                    ),
                ),
                cover_image(index + 440),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    ready = bank.ready_records(
        profile,
        prune=False,
        now="2026-09-01T04:00:00+00:00",
    )
    current_keys = [record["record_key"] for record in records[:3]]
    tail = records[-3:]
    tail_ids = {record["cover_id"] for record in tail}
    recent_guard = [record["cover_id"] for record in records[6:18]]
    profile["current_selection"] = {
        "record_keys": current_keys,
        "request_id": "0" * 32,
        "date_key": "2026-09-01",
        "layout": "triptych",
        "reset_seen": False,
    }
    profile["current_selection_committed_at"] = "2026-09-01T00:00:00+00:00"
    profile["committed_selection_record_keys"] = current_keys
    profile["epoch_seen_cover_ids"] = [
        record["cover_id"] for record in records if record not in tail
    ]
    profile["recent_cover_ids"] = recent_guard

    selection = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        now="2026-09-01T04:00:00+00:00",
    )
    by_key = {record["record_key"]: record for record in records}
    selected = [by_key[record_key] for record_key in selection["record_keys"]]
    selected_ids = {record["cover_id"] for record in selected}

    assert selection["reset_seen"] is True
    assert [record["curation_tier"] for record in selected].count("featured") == 1
    assert [record["curation_tier"] for record in selected].count("discovery") == 2
    assert len(selected_ids & tail_ids) == 2
    assert selected_ids.isdisjoint(recent_guard)


def test_epoch_rollover_keeps_current_when_guard_excludes_every_featured_cover(
    tmp_path,
):
    bank = make_bank(tmp_path)
    _document, profile = bank.load_for_data()
    records = []
    for index in range(18):
        publication = f"Guard Publication {index}"
        records.append(
            bank.ingest(
                profile,
                source(publication),
                cover(
                    index + 480,
                    publication=publication,
                    temporal_class="historical",
                    curation_tier="featured" if index < 4 else "discovery",
                ),
                cover_image(index + 480),
                fetched_at="2026-09-01T00:00:00+00:00",
            )
        )
    ready = bank.ready_records(
        profile,
        prune=False,
        now="2026-09-01T04:00:00+00:00",
    )
    current_keys = [
        records[0]["record_key"],
        records[4]["record_key"],
        records[5]["record_key"],
    ]
    profile["current_selection"] = {
        "record_keys": current_keys,
        "request_id": "0" * 32,
        "date_key": "2026-09-01",
        "layout": "triptych",
        "reset_seen": False,
    }
    profile["current_selection_committed_at"] = "2026-09-01T00:00:00+00:00"
    profile["committed_selection_record_keys"] = current_keys
    profile["epoch_seen_cover_ids"] = [record["cover_id"] for record in records[:-2]]
    profile["recent_cover_ids"] = [record["cover_id"] for record in records[:12]]

    selection = bank.choose_selection(
        profile,
        ready,
        "triptych",
        "sequential",
        selection_hold_hours=3,
        now="2026-09-01T04:00:00+00:00",
    )

    assert selection == profile["current_selection"]
    assert profile["selection_epoch"] == 0
