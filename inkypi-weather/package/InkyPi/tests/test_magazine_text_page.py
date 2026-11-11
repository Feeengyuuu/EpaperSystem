"""A scan that is a page of type is told from a cover, and gives way to covers when there are enough."""

import json
import random
import sys
from datetime import datetime, timezone
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
