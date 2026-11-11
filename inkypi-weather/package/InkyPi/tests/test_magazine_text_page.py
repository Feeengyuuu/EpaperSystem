"""A scan that is a page of type is told from a cover, and gives way to covers when there are enough."""

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.presentation import bind_presentation_instance_identity  # noqa: E402
from plugins.magazine_covers import magazine_covers as magazine_module  # noqa: E402
from plugins.magazine_covers.magazine_covers import MagazineCovers  # noqa: E402
from plugins.magazine_covers.text_page import looks_like_text_page  # noqa: E402

SIZE = (600, 800)


def typed_page(paper=(255, 255, 255)):
    """A bulletin: a ruled heading, then paragraphs of thin dark lines of uneven length."""
    page = Image.new("RGB", SIZE, paper)
    draw = ImageDraw.Draw(page)
    draw.line((60, 60, 540, 60), fill=(20, 20, 20), width=3)
    draw.line((60, 90, 540, 90), fill=(20, 20, 20), width=3)
    for row, y in enumerate(range(130, 740, 14)):
        if row % 9 == 8:
            continue  # the gap between paragraphs
        draw.line((60, y, 540 - (row * 37) % 150, y), fill=(30, 30, 30), width=3)
    return page


def photo_cover():
    """A full-bleed photograph: a sky fading into dark hills, a masthead across the top."""
    cover = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(cover)
    for y in range(SIZE[1]):
        draw.line((0, y, SIZE[0], y), fill=(40 + y // 8, 110 + y // 10, 200 - y // 6))
    draw.polygon(((0, 800), (0, 560), (220, 430), (400, 600), (600, 480), (600, 800)), fill=(30, 60, 40))
    draw.rectangle((40, 40, 420, 120), fill=(240, 240, 240))
    return cover


def solid_cover():
    return Image.new("RGB", SIZE, (200, 30, 40))


def dark_cover():
    """A black cover with a light title and a few lines of small type."""
    cover = Image.new("RGB", SIZE, (16, 16, 20))
    draw = ImageDraw.Draw(cover)
    draw.rectangle((50, 60, 480, 150), fill=(235, 235, 230))
    for y in range(640, 720, 16):
        draw.line((50, y, 360, y), fill=(220, 220, 220), width=3)
    return cover


def illustrated_cover():
    """Black and white: a masthead in heavy lettering over a large dark engraving on white paper."""
    cover = Image.new("RGB", SIZE, (250, 250, 248))
    draw = ImageDraw.Draw(cover)
    for index in range(5):
        draw.rectangle((60 + index * 98, 50, 130 + index * 98, 140), fill=(15, 15, 15))
    draw.ellipse((120, 260, 480, 620), fill=(25, 25, 25))
    draw.ellipse((220, 340, 330, 450), fill=(250, 250, 248))
    for y in range(680, 750, 16):
        draw.line((60, y, 540, y), fill=(30, 30, 30), width=3)
    return cover


def typographic_cover():
    """A cover of type alone: its masthead's display lettering makes dark areas no bulletin has."""
    cover = typed_page()
    draw = ImageDraw.Draw(cover)
    draw.rectangle((40, 30, 560, 120), fill=(255, 255, 255))
    for index in range(6):
        draw.rectangle((70 + index * 80, 40, 120 + index * 80, 110), fill=(10, 10, 10))
    return cover


# Every synthetic scan with what the check must say of it (True: a page of text).
SAMPLES = (
    ("typed bulletin", typed_page, True),
    ("photograph", photo_cover, False),
    ("solid colour", solid_cover, False),
    ("dark, light title", dark_cover, False),
    ("b/w illustration", illustrated_cover, False),
    ("masthead + type", typographic_cover, False),
    ("type on tinted paper", lambda: typed_page((226, 204, 160)), False),
)


@pytest.mark.parametrize("name, make, text_page", SAMPLES, ids=[sample[0] for sample in SAMPLES])
def test_a_typed_page_is_told_from_covers(name, make, text_page):
    scan = make()
    assert looks_like_text_page(scan) is text_page
    # The answer does not turn on the scan's size or mode.
    assert looks_like_text_page(scan.resize((247, 320)).convert("RGBA")) is text_page


def test_the_check_reads_a_grey_scan_and_shrugs_at_what_it_cannot_read():
    assert looks_like_text_page(typed_page().convert("L")) is True
    assert looks_like_text_page(None) is False


def _cover(source, image, text_page=False):
    return {"image": image, "image_url": f"https://example.com/{source['name'].lower()}.jpg",
            "page_url": source["url"], "title": source["name"], "text_page": text_page}


def _live_page(monkeypatch, tmp_path, names, text_pages):
    """The stateless three-cover page from ``names`` in order; the sources whose covers it showed."""
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_load_cover", lambda source, dimensions, force_refresh=False, deadline=None: _cover(
        source, Image.new("RGB", (200, 400), (90, 90, 90)), source["name"] in text_pages))
    shown = []
    monkeypatch.setattr(plugin, "_fit_cover_triptych", lambda covers, dimensions, settings, records=None: (
        shown.extend(source["name"] for source, _cover in covers) or Image.new("RGB", dimensions)))
    plugin.generate_image({"sources": "\n".join(f"{name}|https://example.com/{name.lower()}" for name in names),
                           "rotationMode": "rotate", "fitMode": "triptych", "showSourceLabel": "false",
                           "dailyLibraryMode": "false"},
                          type("Device", (), {"get_resolution": lambda self: (800, 480),
                                              "get_config": lambda self, _key, default=None: default})())
    return shown


def test_the_live_page_passes_over_a_text_page_for_the_next_source(monkeypatch, tmp_path):
    assert _live_page(monkeypatch, tmp_path, ["Alpha", "Beta", "Gamma", "Delta"], {"Beta"}) == ["Alpha", "Gamma", "Delta"]


def test_the_live_page_keeps_a_text_page_rather_than_an_empty_place(monkeypatch, tmp_path):
    assert _live_page(monkeypatch, tmp_path, ["Alpha", "Beta", "Gamma"], {"Beta"}) == ["Alpha", "Gamma", "Beta"]


def _banked_titles(monkeypatch, tmp_path, names, text_pages):
    """The titles in the cover bank after one scan of ``names`` in order."""
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)
    monkeypatch.setattr(plugin, "_load_cover", lambda source, dimensions, force_refresh=False, deadline=None: _cover(
        source, Image.new("RGB", (200, 400), (40 * (names.index(source["name"]) + 1), 60, 90)),
        source["name"] in text_pages))
    settings = bind_presentation_instance_identity({
        "sources": "\n".join(f"{name}|https://example.com/{name.lower()}" for name in names),
        "rotationMode": "random", "fitMode": "triptych", "showSourceLabel": "false",
        "dailyLibraryMode": "true", "contentMode": "latest"}, "text-page-bank")
    plugin.generate_image(settings, type("Device", (), {"get_resolution": lambda self: (800, 480),
                                                        "get_config": lambda self, _key, default=None: default})())
    state = json.loads(plugin._presentation_state_path().read_text(encoding="utf-8"))
    return {record["title"] for profile in state["profiles"].values() for record in profile["records"]}


def test_the_bank_leaves_out_a_text_page_once_it_can_fill_the_page(monkeypatch, tmp_path):
    names = ["Alpha", "Beta", "Gamma", "Delta", "Echo", "Foxtrot"]
    assert magazine_module.TRIPTYCH_COVER_COUNT == 3
    assert _banked_titles(monkeypatch, tmp_path, names, {"Delta"}) == set(names) - {"Delta"}


def test_the_bank_takes_a_text_page_while_it_cannot_fill_the_page_without_it(monkeypatch, tmp_path):
    names = ["Alpha", "Beta", "Gamma", "Delta", "Echo", "Foxtrot"]
    assert _banked_titles(monkeypatch, tmp_path, names, {"Beta"}) == set(names)


# --- Covers already in the bank, and sources already known to be pages of type ---

NOW = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)
DEVICE = type("Device", (), {"get_resolution": lambda self: (800, 480),
                             "get_config": lambda self, _key, default=None: default})()


def _noise_cover(seed):
    """A cover unlike any other seed's: coloured noise under a dark bar."""
    rng = random.Random(seed)
    cover = Image.new("RGB", (90, 120))
    cover.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _pixel in range(90 * 120)])
    ImageDraw.Draw(cover).rectangle((6 + seed * 9 % 50, 8, 26 + seed * 9 % 50, 110), fill=(10, 10, 10))
    return cover


def _bank_settings(names, instance):
    return bind_presentation_instance_identity({
        "sources": "\n".join(f"{name}|https://example.com/{name.lower()}" for name in names),
        "rotationMode": "random", "fitMode": "triptych", "showSourceLabel": "false",
        "dailyLibraryMode": "true", "contentMode": "latest"}, instance)


def _plugin_at(monkeypatch, tmp_path, clock):
    """A plugin whose clock reads ``clock[0]``, with its cache under ``tmp_path``."""
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(tmp_path))
    monkeypatch.setattr(plugin, "_now_utc", lambda: clock[0])
    monkeypatch.setattr(random, "shuffle", lambda values: None)
    return plugin


def _seed_old_bank(plugin, settings, names, typed, *, current=None):
    """A bank as written before scans were checked: archive covers of ``names``, no verdicts.

    The cover named ``typed`` is a typed page. ``current`` names the group on screen, if any.
    """
    bank = plugin._presentation_bank(settings, (800, 480), plugin._presentation_date_key(None))
    document, profile = bank.load_for_data()
    for index, name in enumerate(names):
        bank.ingest(profile, {"name": name, "url": f"https://example.com/{name.lower()}"}, {
            "cover_id": f"archive:{name.lower()}", "provider": "internet_archive", "temporal_class": "latest",
            "image_url": f"https://example.com/{name.lower()}.jpg", "page_url": f"https://example.com/{name.lower()}",
            "title": name}, typed_page() if name == typed else _noise_cover(index), fetched_at=NOW)
    assert not any("text_page" in record for record in profile["records"])
    if current:
        by_title = {record["title"]: record for record in profile["records"]}
        profile["current_selection"] = bank.choose_selection(
            profile, [by_title[name] for name in current], "triptych", "sequential", now=NOW)
        assert len(profile["current_selection"]["record_keys"]) == len(current)
    bank.save(document)


def _bank_state(plugin):
    """(the whole state file, {title: record}, the titles of the current selection)."""
    state = json.loads(plugin._presentation_state_path().read_text(encoding="utf-8"))
    (profile,) = state["profiles"].values()
    by_key = {record["record_key"]: record for record in profile["records"]}
    shown = [by_key[key]["title"] for key in profile["current_selection"]["record_keys"]]
    return state, {record["title"]: record for record in profile["records"]}, shown


def _count_loads(monkeypatch, plugin, names, text_pages=()):
    """Every source load from here on, by source name; ``text_pages`` come back as pages of type."""
    loads = []

    def load_cover(source, dimensions, force_refresh=False, deadline=None):
        loads.append(source["name"])
        return _cover(source, _noise_cover(100 + names.index(source["name"])), source["name"] in text_pages)

    monkeypatch.setattr(plugin, "_load_cover", load_cover)
    return loads


def _count_judgements(monkeypatch):
    """Every stored picture the bank reads from here on to tell a page of type."""
    from plugins.magazine_covers import presentation_bank

    judged = []
    monkeypatch.setattr(presentation_bank, "looks_like_text_page",
                        lambda image: judged.append(image.size) or looks_like_text_page(image))
    return judged


def test_a_typed_page_banked_before_the_check_leaves_the_bank(monkeypatch, tmp_path):
    names = ["Alpha", "Beta", "Bulletin", "Gamma", "Delta"]
    plugin = _plugin_at(monkeypatch, tmp_path, [NOW])
    settings = _bank_settings(names, "old-bank")
    _seed_old_bank(plugin, settings, names, "Bulletin")
    loads = _count_loads(monkeypatch, plugin, names)
    judged = _count_judgements(monkeypatch)

    plugin.generate_image(settings, DEVICE)

    state, records, shown = _bank_state(plugin)
    assert len(judged) == len(names)  # each stored picture read once
    assert "Bulletin" not in records and "Bulletin" not in shown and len(shown) == 3
    assert {title: record["text_page"] for title, record in records.items()} == dict.fromkeys(
        ["Alpha", "Beta", "Gamma", "Delta"], False)
    assert list(state["text_page_sources"]) == ["Bulletin|https://example.com/bulletin"]
    # Its source is back in the scan's queue, and is passed over without a download.
    assert loads == []

    plugin.generate_image(settings, DEVICE)
    assert len(judged) == len(names) and loads == []  # nothing is read or fetched a second time


def test_a_typed_page_on_screen_gives_way_and_its_verdict_is_kept(monkeypatch, tmp_path):
    names = ["Alpha", "Beta", "Bulletin", "Gamma", "Delta"]
    plugin = _plugin_at(monkeypatch, tmp_path, [NOW])
    settings = _bank_settings(names, "old-bank-current")
    _seed_old_bank(plugin, settings, names, "Bulletin", current=["Alpha", "Bulletin", "Beta"])
    loads = _count_loads(monkeypatch, plugin, names)
    judged = _count_judgements(monkeypatch)

    plugin.generate_image(settings, DEVICE)

    state, records, shown = _bank_state(plugin)
    # The group that was on screen protected the page from removal; it is recorded, not shown.
    assert records["Bulletin"]["text_page"] is True
    assert "Bulletin" not in shown and len(shown) == 3
    assert "text_page_sources" not in state and len(judged) == len(names)

    plugin.generate_image(settings, DEVICE)

    state, records, shown = _bank_state(plugin)
    assert "Bulletin" not in records and "Bulletin" not in shown
    assert list(state["text_page_sources"]) == ["Bulletin|https://example.com/bulletin"]
    assert len(judged) == len(names) and loads == []  # the recorded verdicts served the second refresh


def test_a_banked_typed_page_stays_while_the_covers_cannot_fill_the_page(monkeypatch, tmp_path):
    names = ["Alpha", "Bulletin", "Beta"]
    plugin = _plugin_at(monkeypatch, tmp_path, [NOW])
    settings = _bank_settings(names, "short-bank")
    _seed_old_bank(plugin, settings, names, "Bulletin")
    loads = _count_loads(monkeypatch, plugin, names)

    plugin.generate_image(settings, DEVICE)

    state, records, shown = _bank_state(plugin)
    assert sorted(shown) == ["Alpha", "Beta", "Bulletin"]
    assert records["Bulletin"]["text_page"] is True and "text_page_sources" not in state
    assert loads == []


def test_the_display_pick_goes_by_the_recorded_verdict_alone():
    """Choosing what to show reads the records, never a picture: a judged page gives way to three covers."""
    plugin = MagazineCovers({"id": "magazine_covers"})
    settings = {"contentMode": "latest"}
    covers = [{"record_key": name, "temporal_class": "latest"} for name in ("a", "b", "c")]
    typed = {"record_key": "t", "temporal_class": "latest", "text_page": True}
    unjudged = {"record_key": "u", "temporal_class": "latest"}
    assert plugin._eligible_bank_records(settings, [covers[0], typed, covers[1], unjudged]) == [
        covers[0], covers[1], unjudged]
    assert plugin._eligible_bank_records(settings, [covers[0], typed, covers[1]]) == [covers[0], typed, covers[1]]


def test_only_archive_covers_in_the_bank_are_judged(monkeypatch, tmp_path):
    names = ["Alpha", "Beta", "Bulletin", "Gamma", "Delta"]
    plugin = _plugin_at(monkeypatch, tmp_path, [NOW])
    settings = _bank_settings(names, "modern-bank")
    _seed_old_bank(plugin, settings, names, "Bulletin")
    path = plugin._presentation_state_path()
    state = json.loads(path.read_text(encoding="utf-8"))
    for profile in state["profiles"].values():
        for record in profile["records"]:
            record["provider"] = "publisher_web"  # a modern magazine's own site
    path.write_bytes(json.dumps(state).encode("utf-8"))
    _count_loads(monkeypatch, plugin, names)
    judged = _count_judgements(monkeypatch)

    plugin.generate_image(settings, DEVICE)

    _state, records, _shown = _bank_state(plugin)
    assert judged == [] and sorted(records) == sorted(names)
    assert not any("text_page" in record for record in records.values())


def test_a_rejected_source_is_not_downloaded_again_until_its_record_expires(monkeypatch, tmp_path):
    from plugins.magazine_covers.presentation_bank import TEXT_PAGE_SOURCE_SECONDS

    names = ["Alpha", "Beta", "Gamma", "Delta", "Echo", "Foxtrot"]
    clock = [NOW]
    plugin = _plugin_at(monkeypatch, tmp_path, clock)
    settings = _bank_settings(names, "rejected-source")
    loads = _count_loads(monkeypatch, plugin, names, text_pages={"Delta"})

    plugin.generate_image(settings, DEVICE)
    state, records, _shown = _bank_state(plugin)
    assert sorted(loads) == sorted(names) and "Delta" not in records
    assert state["text_page_sources"] == {"Delta|https://example.com/delta": NOW.isoformat()}

    # The bank is still short of its target, so the next refresh goes looking for Delta again.
    clock[0] = NOW + timedelta(hours=1)
    plugin.generate_image(settings, DEVICE)
    assert loads.count("Delta") == 1 and len(loads) == len(names)

    # The note grows old (the covers in the bank are left as they are): Delta is tried again.
    path = plugin._presentation_state_path()
    state = json.loads(path.read_text(encoding="utf-8"))
    state["text_page_sources"] = {"Delta|https://example.com/delta": (
        clock[0] - timedelta(seconds=TEXT_PAGE_SOURCE_SECONDS + 60)).isoformat()}
    path.write_bytes(json.dumps(state).encode("utf-8"))
    plugin.generate_image(settings, DEVICE)
    assert loads.count("Delta") == 2 and len(loads) == len(names) + 1
    state, _records, _shown = _bank_state(plugin)
    assert state["text_page_sources"] == {"Delta|https://example.com/delta": clock[0].isoformat()}


def test_the_rejected_sources_are_bounded_and_survive_a_damaged_file():
    from plugins.magazine_covers.presentation_bank import (
        TEXT_PAGE_SOURCE_LIMIT,
        _bounded_text_page_sources,
        validate_state_shape,
    )

    entries = {f"source-{index:03d}": (NOW + timedelta(minutes=index)).isoformat()
               for index in range(TEXT_PAGE_SOURCE_LIMIT + 5)}
    kept = _bounded_text_page_sources(entries)
    assert len(kept) == TEXT_PAGE_SOURCE_LIMIT and "source-000" not in kept and "source-068" in kept
    assert _bounded_text_page_sources({"ok": NOW.isoformat(), "bad": "soon", "": NOW.isoformat(), "n": 5}) == {
        "ok": NOW.isoformat()}
    assert _bounded_text_page_sources(["not", "a", "map"]) == {}
    validate_state_shape({"text_page_sources": kept})
    with pytest.raises(RuntimeError):
        validate_state_shape({"text_page_sources": entries})
