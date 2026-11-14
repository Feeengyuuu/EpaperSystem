from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageChops, ImageDraw
import pytest

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.game_deals.game_deals import GameDeals
from plugins.game_deals.render import HERO_ART, LIST_LEFT, LIST_TOP, ROW_PITCH, THUMB, pick_hero, render_page
from plugins.game_deals.source import DealRepository, DealSnapshot


NOW = datetime(2026, 9, 30, 1, tzinfo=timezone.utc)


def offers():
    titles = ["The Witcher 2: Assassins of Kings Enhanced Edition", "Ori and the Will of the Wisps",
              "ENDLESS Legend 2", "A" * 200, "A Very Long Game Title With Many Actual Words " * 4,
              "Short", "Deaths Door", "The Next Penelope", "TowerFall Ascension", "Fly'N"]
    return tuple({"title": title, "game_id": str(index), "sale_price": "199.99" if index == 3 else "2.99",
                  "normal_price": "299.99", "discount_percent": 100 if index == 3 else 90}
                 for index, title in enumerate(titles))


def test_ten_realistic_and_extreme_titles_fit_day_and_night():
    snapshot = DealSnapshot(offers(), NOW, "live", "steam")
    day = render_page(snapshot, {}, theme={"mode": "day"}, now=NOW)
    night = render_page(snapshot, {}, theme={"mode": "night"}, now=NOW)
    assert day.size == night.size == (800, 480)
    assert ImageChops.difference(day, night).getbbox() is not None
    layout = day.info["game_deals_layout"]
    assert len(layout) == 10
    for card in layout:
        assert len(card["lines"]) == 1
        assert max(card["title_widths"]) <= card["text_width"]
        # The prices never run into the discount block.
        assert card["prices_right"] <= card["badge_left"] - 4
    titles = {card["title"]: card["lines"][0] for card in layout}
    assert titles["Short"] == "Short"
    assert titles["A Very Long Game Title With Many Actual Words " * 4].endswith("…")


@pytest.mark.parametrize("mode", ["day", "night"])
def test_steam_footer_uses_its_official_logo_and_falls_back_to_its_name(monkeypatch, mode):
    from plugins.game_deals import render

    texts, marks = [], []
    original_text, original_mark = ImageDraw.ImageDraw.text, render._mark

    def mark(name, height, *args):
        marks.append((name, height))
        return original_mark(name, height, *args)

    monkeypatch.setattr(render, "_mark", mark)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda self, xy, text, *a, **k: (
        texts.append((xy, str(text))), original_text(self, xy, text, *a, **k))[1])
    snapshot = DealSnapshot(offers(), NOW, "live", "steam")
    render_page(snapshot, {}, theme={"mode": mode}, now=NOW)
    footer = [text for xy, text in texts if xy[1] >= 450]
    assert ("steam_logo", 13) in marks and "美元报价" in footer
    assert not any("Steam" in text for text in footer)
    monkeypatch.setattr(render, "_mark", lambda name, height, *a: (
        None if name == "steam_logo" else original_mark(name, height, *a)))
    texts.clear()
    render_page(snapshot, {}, theme={"mode": mode}, now=NOW)
    footer = [text for xy, text in texts if xy[1] >= 450]
    assert {"Steam", "美元报价"} <= set(footer)


def test_chinese_store_name_replaces_only_the_title_line():
    english = offers()
    chinese = tuple(dict(deal, title_zh=name) for deal, name in zip(english, ["巫师2：国王刺客 加强版", "", "无尽传奇2"]))
    plain = render_page(DealSnapshot(english[:3], NOW, "live", "steam"), {}, theme={"mode": "day"}, now=NOW)
    named = render_page(DealSnapshot(chinese, NOW, "live", "steam"), {}, theme={"mode": "day"}, now=NOW)
    layout = named.info["game_deals_layout"]
    lines = {card["title"]: card["lines"][0] for card in layout}
    assert lines[english[0]["title"]] == "巫师2：国王刺客 加强版"
    assert lines[english[2]["title"]] == "无尽传奇2"
    assert all(max(card["title_widths"]) <= card["text_width"] for card in layout)
    # The art and the price columns stay where they were; only the name changes.
    for card, before in zip(layout, plain.info["game_deals_layout"]):
        assert card["box"] == before["box"] and card["art_rect"] == before["art_rect"]
        assert card["badge_left"] == before["badge_left"]
        assert ImageChops.difference(named.crop(card["art_rect"]), plain.crop(card["art_rect"])).getbbox() is None


def test_the_best_value_leads_and_the_other_nine_are_ranked_down_the_right():
    snapshot = DealSnapshot(offers(), NOW, "live", "steam")
    layout = render_page(snapshot, {}, theme={"mode": "day"}, now=NOW).info["game_deals_layout"]
    hero, rows = layout[0], layout[1:]
    assert hero["role"] == "hero" and hero["box"][0] == 14 and hero["box"][2] < LIST_LEFT
    assert [row["rank"] for row in rows] == list(range(2, 11))
    assert {row["box"][0] for row in rows} == {LIST_LEFT}
    tops = [row["box"][1] for row in rows]
    assert tops[0] == LIST_TOP and all(later - earlier == ROW_PITCH for earlier, later in zip(tops, tops[1:]))
    assert rows[-1]["box"][3] < 445
    # The rows keep the source's order, the hero taken out.
    assert [row["title"] for row in rows] == [deal["title"] for deal in offers() if deal["title"] != hero["title"]]


def test_hero_prefers_an_all_time_low_then_the_reviews_then_the_discount():
    plain = dict(offers()[0], discount_percent=95)
    reviewed = dict(offers()[1], metacritic=94, steam_rating="Very Positive", steam_rating_percent=92)
    lowest = dict(offers()[2], sale_price="2.99", lowest_price="2.99", metacritic=80)
    assert pick_hero([plain, reviewed, lowest]) == 2
    assert pick_hero([plain, reviewed]) == 1
    assert pick_hero([plain, dict(offers()[1], discount_percent=50)]) == 0
    assert pick_hero([]) is None


def test_empty_and_unavailable_have_different_messages_and_no_invented_cards():
    empty = render_page(DealSnapshot((), NOW, "live", "steam"), {}, now=NOW)
    missing = render_page(DealSnapshot((), None, "unavailable", "steam"), {}, now=NOW)
    assert empty.info["game_deals_layout"] == missing.info["game_deals_layout"] == []
    assert ImageChops.difference(empty, missing).getbbox() is not None


@pytest.mark.parametrize("mode", ["day", "night"])
def test_wide_cover_is_complete_proportional_and_surrounded_by_page_background(mode):
    # Edge colors prove the complete 6:1 source survives; the square detects stretch.
    cover = Image.new("RGB", (600, 100), (0, 0, 255))
    source = ImageDraw.Draw(cover)
    source.rectangle((0, 0, 39, 99), fill=(0, 255, 0))
    source.rectangle((250, 0, 349, 99), fill=(255, 0, 0))
    source.rectangle((560, 0, 599, 99), fill=(255, 255, 0))
    snapshot = DealSnapshot(offers()[:1], NOW, "live", "steam")
    result = render_page(snapshot, {"0": cover}, theme={"mode": mode}, now=NOW)
    frame = result.crop(HERO_ART)
    background = result.getpixel((0, 0))
    visible = ImageChops.difference(frame, Image.new("RGB", frame.size, background)).getbbox()
    # The hero's art spans its card's width, its height following the source's ratio, centred.
    assert visible[0] == 0 and visible[2] == frame.width
    assert abs((visible[2] - visible[0]) / (visible[3] - visible[1]) - 6) < 0.2
    artwork = frame.crop(visible)
    assert artwork.getpixel((0, artwork.height // 2)) == (0, 255, 0)
    assert artwork.getpixel((artwork.width - 1, artwork.height // 2)) == (255, 255, 0)
    # Subtract green so the yellow right edge does not count as the red square.
    mask = ImageChops.subtract(artwork.getchannel("R"), artwork.getchannel("G")).point(
        lambda value: 255 if value > 200 else 0)
    left, top, right, bottom = mask.getbbox()
    assert abs((right - left) - (bottom - top)) <= 2


def _thumb_frame(result, row):
    box = result.info["game_deals_layout"][row]["box"]
    left = box[0] + 26
    middle = (box[1] + box[3]) // 2
    return result.crop((left, middle - THUMB[1] // 2, left + THUMB[0], middle + THUMB[1] // 2))


@pytest.mark.parametrize("mode", ["day", "night"])
def test_small_cover_is_not_enlarged_and_keeps_every_source_pixel(mode):
    cover = Image.new("RGB", (60, 20), (27, 144, 233))
    ImageDraw.Draw(cover).rectangle((0, 0, 5, 19), fill=(200, 25, 40))
    snapshot = DealSnapshot(offers()[:2], NOW, "live", "steam")
    result = render_page(snapshot, {"1": cover}, theme={"mode": mode}, now=NOW)
    frame = _thumb_frame(result, 1)
    background = Image.new("RGB", frame.size, result.getpixel((0, 0)))
    visible = ImageChops.difference(frame, background).getbbox()
    assert (visible[2] - visible[0], visible[3] - visible[1]) == (60, 20)
    assert ImageChops.difference(frame.crop(visible), cover).getbbox() is None


def test_steam_capsule_keeps_its_ratio_at_full_thumbnail_width():
    # CheapShark serves 231x87 Steam capsules; they must shrink, not crop or stretch.
    cover = Image.new("RGB", (231, 87), (27, 144, 233))
    snapshot = DealSnapshot(offers()[:2], NOW, "live", "steam")
    result = render_page(snapshot, {"1": cover}, theme={"mode": "day"}, now=NOW)
    frame = _thumb_frame(result, 1)
    background = Image.new("RGB", frame.size, result.getpixel((0, 0)))
    left, top, right, bottom = ImageChops.difference(frame, background).getbbox()
    assert right - left == THUMB[0]
    assert abs((right - left) / (bottom - top) - 231 / 87) < 0.1


class Device:
    def get_resolution(self):
        return [800, 480]

    def get_config(self, key, default=None):
        return {"orientation": "horizontal", "timezone": "America/Los_Angeles"}.get(key, default)


def test_plugin_theme_redraw_marks_stale_without_fetching_or_changing_cache(tmp_path, monkeypatch):
    import plugins.game_deals.game_deals as module
    http = SimpleNamespace(request_json=lambda *a, **k: SimpleNamespace(data=[{
        "title": "Real title", "dealID": "id", "gameID": "123", "storeID": "1",
        "salePrice": "2.99", "normalPrice": "29.99", "thumb": "",
    }]))
    DealRepository(tmp_path / "data", http=http).load({}, now=datetime.now(timezone.utc) - timedelta(hours=3))
    path = tmp_path / "data" / "deals-steam.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    class NoNetwork:
        def request_json(self, *a, **k):
            raise AssertionError("cache-only redraw made a provider request")

        request_bytes = request_json
    monkeypatch.setattr(module, "get_http_client", lambda: NoNetwork())
    plugin = GameDeals.__new__(GameDeals)
    plugin.config = {"id": "game_deals"}
    monkeypatch.setattr(plugin, "cache_dir", lambda **kwargs: tmp_path / "data")
    result = plugin.render_cached_display({}, Device(), resolved_theme_context={"mode": "night"})
    assert result.info["inkypi_theme_mode"] == "night"
    assert result.info["inkypi_skip_cache"] is True
    assert read_source_provenance(result) in {SourceProvenance.STALE_CACHE, SourceProvenance.LOCAL_FALLBACK}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_manifest_cadence_and_display_redraw_do_not_request_provider_refresh():
    manifest = json.loads((Path(__file__).parents[1] / "src/plugins/game_deals/plugin-info.json").read_text(encoding="utf-8"))
    assert manifest["recommended_refresh"] == {"interval": 7200}
    assert manifest["refresh_on_display"] is False
    assert manifest["capabilities"]["supports_cached_display_redraw"] is True
    assert not manifest["capabilities"].get("allows_display_triggered_provider_refresh", False)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_sale_price_sits_on_the_page_in_greenback_green(mode, monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    page = render_page(DealSnapshot(offers()[:1], NOW, "live", "steam"), {}, theme={"mode": mode}, now=NOW)
    x0, y0, x1, y1 = page.info["game_deals_layout"][0]["price_tag_box"]
    middle = [page.getpixel((x, y)) for x in range(x0, x1) for y in range(y0, y1)]
    # Orange digits on the page's own ground: no dark tag behind them.
    assert sum(g > r + 30 and g > b + 20 for r, g, b in middle) > 30
    paper = page.getpixel((x1 - 1, y0 + 1))
    assert (max(paper) < 40) if mode == "night" else (min(paper) > 230)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_long_price_keeps_all_digits_and_the_original_clear_of_discount(mode):
    deal = {"title": "Full amount", "game_id": "123", "sale_price": "99999.99",
            "normal_price": "100000.00", "discount_percent": 1}
    image = render_page(DealSnapshot((deal,), NOW, "live", "steam"), {}, theme={"mode": mode}, now=NOW)
    card = image.info["game_deals_layout"][0]
    assert card["sale_price"] == "$99999.99"
    assert card["prices_right"] <= card["badge_left"] - 4
    assert card["price_tag_box"][2] < card["badge_left"]
    # Shrunk from the hero's 64 px to keep every digit beside the struck original.
    assert card["price_font_size"] < 50


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_store_and_the_price_source_are_named_by_their_official_marks(mode):
    page = render_page(DealSnapshot(offers()[:2], NOW, "live", "steam"), {}, theme={"mode": mode}, now=NOW)
    ink = (250, 250, 240) if mode == "night" else (8, 12, 10)

    # Steam's round mark after the title rule: its own black by day, the page's ink at night.
    header = {page.getpixel((x, y)) for x in range(220, 252) for y in range(24, 52)}
    assert ink in header if mode == "night" else any(max(pixel) < 30 for pixel in header)
    # CheapShark's gold-and-grey shark opens the footer, its lettering in ink after it.
    shark = [page.getpixel((x, y)) for x in range(14, 40) for y in range(452, 474)]
    assert any(red > 180 and green > 120 and blue < 90 for red, green, blue in shark)
    assert ink in {page.getpixel((x, y)) for x in range(40, 120) for y in range(456, 470)}


@pytest.mark.parametrize("mode", ["day", "night"])
def test_reviews_and_all_time_low_are_named_by_their_marks_beside_the_offer(mode):
    cover = Image.new("RGB", (231, 87), (120, 120, 120))
    marked = dict(offers()[0], metacritic=89, steam_rating="Very Positive", steam_rating_percent=90,
                  steam_rating_count=123165, sale_price="2.99", lowest_price="2.99")
    above = dict(offers()[1], metacritic=62, steam_rating="Mixed", steam_rating_percent=55,
                 steam_rating_count=900, sale_price="2.99", lowest_price="1.49")
    plain = offers()[2]
    page = render_page(DealSnapshot((marked, above, plain), NOW, "live", "steam"),
                       {"0": cover, "1": cover, "2": cover}, theme={"mode": mode}, now=NOW)
    hero, second, third = page.info["game_deals_layout"]
    assert hero["title"] == marked["title"]  # at its all-time low, it leads
    assert hero["marks"] == [("low", "史低"), ("metacritic", "89"), ("steam_rating", "90%")]
    assert hero["note"] == "特别好评 ‧ 12.3 万 篇评测"
    # The card's right foot: what the sale saves, then (when the offer has them) CheapShark's rating
    # and when the all-time low was seen.
    assert hero["aside"][0] == ("saved", "立省 $297.00")
    assert second["marks"] == [("low", "史低 $1.49"), ("metacritic", "62"), ("steam_rating", "55%")]
    assert third["marks"] == []
    # The art keeps its own pixels: no mark is drawn on it any more.
    left, top, right, bottom = hero["art_rect"]
    assert {page.getpixel((x, y)) for x in (left + 3, right - 3) for y in (top + 3, bottom - 3)} == {(120, 120, 120)}
    # Metacritic's yellow roundel names the score in the second offer's row; the figures sit on the
    # page with no coloured chip (only the all-time low keeps one).
    row = second["box"]
    pixels = [page.getpixel((x, y)) for x in range(row[0], row[2]) for y in range(row[1] + 20, row[3])]
    assert any(red > 200 and 140 < green < 200 and blue < 70 for red, green, blue in pixels)
    cobalt = {(37, 84, 199), (59, 110, 230)}
    assert not cobalt & {page.getpixel((x, y)) for x in range(0, 800) for y in range(row[1], row[3])}
