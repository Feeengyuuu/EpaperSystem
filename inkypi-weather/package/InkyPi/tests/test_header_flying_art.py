"""The generated header art: comics on the wind (Comic Covers), dollar bills (stock tracker)."""

import json
from pathlib import Path

from PIL import Image, ImageDraw

from plugins.base_plugin import cover_gallery
from plugins.gcd_comic_covers import gcd_comic_covers
from plugins.stocktracker import stocktracker
from plugins.stocktracker.stocktracker import StockTracker, _active_stock_colors

RED = (220, 20, 20, 255)


def test_header_art_fills_the_room_between_the_subtitle_and_the_date():
    art = Image.new("RGBA", (400, 40), RED)
    page = cover_gallery.compose([Image.new("RGB", (300, 400), (90, 90, 90))], [cover_gallery.Label(title="One")], (800, 480),
                                 title="COMIC COVERS", subtitle="今日漫画", date_text="10月25日 周日", header_art=art)
    header = page.crop((0, 0, 800, cover_gallery.HEADER_RULE))
    reds = [x for x in range(800) if header.getpixel((x, 24))[:3] == RED[:3]]
    assert reds, "the art was not drawn"
    # It keeps clear of the subtitle (left of x 260) and the date at the right edge.
    assert min(reds) > 120 and max(reds) < 800 - cover_gallery.MARGIN - 60


def test_header_art_is_left_out_when_there_is_no_room():
    page = Image.new("RGB", (100, 50), (255, 255, 255))
    cover_gallery._header_art(page, Image.new("RGBA", (40, 10), RED), (10, 2, 50, 40))
    assert page.getpixel((30, 20)) == (255, 255, 255)


def test_stock_header_flies_its_bills_between_the_logo_and_the_source():
    plugin = StockTracker({"id": "stocktracker"})
    image = Image.new("RGB", (800, 60), _active_stock_colors()["paper"])
    pasted = []
    original = plugin._draw_header_money

    def record(img, box, **kwargs):
        pasted.append(box)
        return original(img, box, **kwargs)

    plugin._draw_header_money = record
    plugin._draw_header(image, ImageDraw.Draw(image), 800, [{"symbol": "NVDA"}], header_brand="robinhood")

    left, top, right, bottom = pasted[0]
    assert 100 < left < right and bottom <= stocktracker.HEADER_RULE_Y
    greens = [x for x in range(left, right) if any(
        image.getpixel((x, y))[1] > 120 > image.getpixel((x, y))[0] for y in range(top, bottom))]
    assert greens, "no bills were drawn"
    # The bills and coins are flown again to fill the stretch from end to end.
    assert min(greens) < left + 30 and max(greens) > right - 30


def test_the_flying_art_is_installed_with_its_record():
    for path in (Path(gcd_comic_covers.HEADER_ART_PATH), Path(stocktracker.HEADER_MONEY_PATH)):
        with Image.open(path) as art:
            assert art.mode == "RGBA" and art.getchannel("A").getextrema()[0] == 0, path.name
        record = json.loads(path.with_suffix(".source.json").read_text(encoding="utf-8"))
        assert record["source"].startswith("https://cdn.creativeclaw.co/"), path.name


def test_the_bills_fly_in_turns_from_another_bill_each_hour():
    bills, coins, _height = stocktracker._header_money_pieces()
    plugin = StockTracker({"id": "stocktracker"})
    flights = []
    for turn in (0, 1):
        image = Image.new("RGB", (500, 50), (255, 255, 255))
        plugin._draw_header_money(image, (0, 4, 500, 45), turn=turn)
        flights.append(image.tobytes())

    # Four bills and three coins, the cut-off scrap at the art's right edge left out.
    assert (len(bills), len(coins)) == (4, 3)
    assert flights[0] != flights[1]
