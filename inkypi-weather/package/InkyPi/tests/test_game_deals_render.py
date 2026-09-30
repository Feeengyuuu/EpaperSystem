from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageChops, ImageDraw
import pytest

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.game_deals.game_deals import GameDeals
from plugins.game_deals.render import render_page
from plugins.game_deals.source import DealRepository, DealSnapshot


NOW = datetime(2026, 9, 30, 1, tzinfo=timezone.utc)


def offers():
    titles = ["The Witcher 2: Assassins of Kings Enhanced Edition", "Ori and the Will of the Wisps",
              "ENDLESS Legend 2", "A" * 200, "A Very Long Game Title With Many Actual Words " * 4,
              "Short"]
    return tuple({"title": title, "game_id": str(index), "sale_price": "199.99" if index == 3 else "2.99",
                  "normal_price": "299.99", "discount_percent": 100 if index == 3 else 90}
                 for index, title in enumerate(titles))


def test_six_realistic_and_extreme_titles_fit_day_and_night():
    snapshot = DealSnapshot(offers(), NOW, "live", "steam")
    day = render_page(snapshot, {}, theme={"mode": "day"}, now=NOW)
    night = render_page(snapshot, {}, theme={"mode": "night"}, now=NOW)
    assert day.size == night.size == (800, 480)
    assert ImageChops.difference(day, night).getbbox() is not None
    assert len(day.info["game_deals_layout"]) == 6
    for card in day.info["game_deals_layout"]:
        assert len(card["lines"]) <= 3
        assert max(card["title_widths"]) <= card["text_width"]
    assert day.info["game_deals_layout"][0]["lines"][-1].endswith("Edition")
    assert day.info["game_deals_layout"][3]["lines"][-1].endswith("…")


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
    frame = result.crop((14, 83, 164, 184))
    assert frame.size == (150, 101)
    background = result.getpixel((0, 0))
    visible = ImageChops.difference(frame, Image.new("RGB", frame.size, background)).getbbox()
    assert visible == (0, 38, 150, 63)
    artwork = frame.crop(visible)
    assert artwork.getpixel((0, 12)) == (0, 255, 0)
    assert artwork.getpixel((149, 12)) == (255, 255, 0)
    assert artwork.getpixel((35, 0)) == artwork.getpixel((35, 24)) == (0, 0, 255)
    # Subtract green so the yellow right edge does not count as the red square.
    mask = ImageChops.subtract(artwork.getchannel("R"), artwork.getchannel("G")).point(
        lambda value: 255 if value > 200 else 0)
    left, top, right, bottom = mask.getbbox()
    assert abs((right - left) - (bottom - top)) <= 2


@pytest.mark.parametrize("mode", ["day", "night"])
def test_small_cover_is_not_enlarged_and_keeps_every_source_pixel(mode):
    cover = Image.new("RGB", (60, 20), (27, 144, 233))
    ImageDraw.Draw(cover).rectangle((0, 0, 5, 19), fill=(200, 25, 40))
    snapshot = DealSnapshot(offers()[:1], NOW, "live", "steam")
    result = render_page(snapshot, {"0": cover}, theme={"mode": mode}, now=NOW)
    frame = result.crop((14, 83, 164, 184))
    background = Image.new("RGB", frame.size, result.getpixel((0, 0)))
    visible = ImageChops.difference(frame, background).getbbox()
    assert visible == (45, 40, 105, 60)
    assert ImageChops.difference(frame.crop(visible), cover).getbbox() is None


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
