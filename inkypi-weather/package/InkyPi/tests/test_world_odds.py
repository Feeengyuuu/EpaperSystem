import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.world_odds import sources  # noqa: E402
from plugins.world_odds.render import board_value, chance_text, money_text, render_page  # noqa: E402

NOW = datetime(2026, 10, 28, 22, 0, tzinfo=sources.PACIFIC)


def _market(name, yes, change=0.0, volume=0.0, image=None, token="11"):
    return {"groupItemTitle": name, "outcomePrices": json.dumps([str(yes), str(1 - yes)]), "oneDayPriceChange": change,
            "volume24hr": volume, "image": image, "clobTokenIds": json.dumps([token, "22"]), "active": True}


def test_a_multi_outcome_event_lists_its_markets_likeliest_first():
    event = {"id": 1, "title": "Prime Minister of Israel after the next election?", "volume24hr": 5148981,
             "volume": 78795591, "commentCount": 1605, "endDate": "2026-10-28T03:59:00Z",
             "image": "https://polymarket-upload.s3.us-east-2.amazonaws.com/pm.png",
             "markets": [_market("Gadi Eizenkot", 0.249, -0.08), _market("Benjamin Netanyahu", 0.455, -0.06,
                                                                          image="https://polymarket-upload.s3.us-east-2.amazonaws.com/bn.png"),
                         {**_market("Closed", 0.9), "closed": True}]}
    parsed = sources.normalize_polymarket_event(event, "政治")

    assert [outcome["name"] for outcome in parsed["outcomes"]] == ["Benjamin Netanyahu", "Gadi Eizenkot"]
    assert (parsed["comments"], parsed["ends"]) == (1605, "2026-10-28")
    assert parsed["outcomes"][0]["image"].endswith("/bn.png") and parsed["outcomes"][0]["token"] == "11"


def test_a_binary_event_shows_yes_and_no_with_opposite_moves():
    event = {"id": 2, "title": "Strait of Hormuz traffic returns to normal?", "volume24hr": 72000,
             "markets": [{"outcomes": '["Yes", "No"]', "outcomePrices": '["0.105", "0.895"]', "oneDayPriceChange": -0.01,
                          "clobTokenIds": '["5", "6"]'}]}
    outcomes = sources.normalize_polymarket_event(event, "经济")["outcomes"]

    assert [(o["name"], o["chance"], o["change"], o["token"]) for o in outcomes] == [
        ("No", 0.895, 0.01, "6"), ("Yes", 0.105, -0.01, "5")]


def test_the_lead_is_the_busiest_and_tiles_skip_what_kalshi_prices():
    def event(identifier, category, title, volume):
        return {"id": identifier, "title": title, "category": category, "volume_24h": volume,
                "outcomes": [{"name": "Yes", "chance": 0.5, "change": 0}]}
    events = [event("a", "政治", "Israel PM", 5e6), event("b", "政治", "House 2026", 2e6),
              event("c", "经济", "Fed Decision in December?", 2e6), event("d", "经济", "Hormuz traffic", 7e4),
              event("e", "科技", "Best AI model", 1e5)]
    lead, tiles = sources.lead_and_categories(events)

    assert lead["id"] == "a"
    assert [tile["id"] for tile in tiles] == ["b", "d", "e"]


def test_kalshi_reads_the_fed_ranges_and_thresholds():
    fed = sources.normalize_kalshi_event({"title": "Fed decision", "sub_title": "On Dec 9, 2026", "strike_date": "2026-12-09T19:00:00Z",
                                          "markets": [{"yes_sub_title": "Fed maintains rate", "last_price_dollars": "0.20",
                                                       "previous_price_dollars": "0.24"},
                                                      {"yes_sub_title": "Hike 25bps", "last_price_dollars": "0.79",
                                                       "previous_price_dollars": "0.75"}]}, "decision")
    cpi = sources.normalize_kalshi_event({"sub_title": "In Nov 2026", "markets": [
        {"strike_type": "greater", "floor_strike": 3.7, "last_price_dollars": "0.90"},
        {"strike_type": "greater", "floor_strike": 3.8, "last_price_dollars": "0.60"},
        {"strike_type": "greater", "floor_strike": 3.9, "last_price_dollars": "0.20"}]}, "percent")
    index = sources.normalize_kalshi_event({"sub_title": "On Dec 31, 2026 at 4pm EST", "markets": [
        {"strike_type": "between", "floor_strike": 7800, "cap_strike": 7999.99, "last_price_dollars": "0.24"},
        {"strike_type": "between", "floor_strike": 8000, "cap_strike": 8199.99, "last_price_dollars": "0.19"}]}, "index")

    assert [(o["name"], round(o["chance"], 2), round(o["change"], 2)) for o in fed["outcomes"]] == [
        ("维持", 0.2, -0.04), ("加息25", 0.79, 0.04)]
    assert cpi["median"] == pytest.approx(3.825)
    assert (index["low"], index["chance"], len(index["ranges"])) == (7800, 0.24, 2)
    assert board_value({**cpi, "kind": "percent"}) == ("≈3.8%", "中位预期 · 2026年11月")
    assert board_value({**index, "kind": "index"})[0] == "7,800–8,000"


def test_chinese_dates_money_and_chances():
    assert sources.chinese_when("In Q3 2026") == "2026年第三季度"
    assert sources.chinese_when("On Oct 31, 2026") == "10月31日"
    assert sources.chinese_when("On Dec 31, 2026 at 4pm EST") == "12月31日收盘"
    assert (money_text(5148981), money_text(177642)) == ("$5.1M", "$178K")
    assert (chance_text(0.455), chance_text(0.004), chance_text(0.996)) == ("46%", "<1%", ">99%")


@pytest.mark.parametrize("mode", ["day", "night"])
def test_page_renders_with_and_without_data(mode):
    lead = {"id": "a", "title": "Prime Minister of Israel after the next election?", "category": "政治",
            "volume_24h": 5e6, "volume": 7.8e7, "comments": 10, "ends": "2026-10-28", "image": None,
            "outcomes": [{"name": f"Candidate {n}", "chance": 0.4 / n, "change": 0.01, "token": str(n)} for n in range(1, 5)]}
    tiles = [{**lead, "id": c, "category": c} for c in ("地缘", "经济", "科技", "加密")]
    board = {"KXRECSSNBER": {"label": "年内经济衰退", "kind": "yes", "chance": 0.03, "change": 0.0, "when": "In 2026"},
             "KXGDP": {"label": "GDP 年化增速", "kind": "percent", "median": 3.1, "when": "In Q3 2026",
                       "ladder": [[1.0, 0.99], [3.0, 0.6], [4.0, 0.1]]}}
    payload = {"lead": lead, "tiles": tiles, "board": board, "histories": {"1": [[1, 0.3], [2, 0.4]]}}
    for data in (payload, {"lead": None, "tiles": [], "board": {}}):
        image = render_page((800, 480), data, {"_inkypi_theme": {"mode": mode}}, NOW, {})
        assert image.size == (800, 480)


def test_tile_outcomes_without_a_picture_get_a_drawn_mark():
    from plugins.world_odds.render import _outcome_kind

    assert [_outcome_kind(name) for name in ("Yes", "no", "October 28", "↑ 82,500", "Google", "October")] == [
        "yes", "no", ("date", 28), "level", None, None]


def test_dated_deadlines_with_a_year_get_the_calendar_page():
    from plugins.world_odds.render import _outcome_kind

    assert [_outcome_kind(name) for name in ("June 30, 2027", "December 31, 2026", "Dec 31, 2026", "Sept 3")] == [
        ("date", 30), ("date", 31), ("date", 31), ("date", 3)]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_every_outcome_without_a_picture_or_kind_gets_an_initial_badge(mode):
    from plugins.world_odds.render import _mark_room, _outcome_mark, _palette
    from utils.page_canvas import PageCanvas

    assert _mark_room(None, None, 17) == _mark_room("yes", None, 17) == 22
    for name in ("Google", "Ohio", "7,600-7,800", "特朗普"):
        palette = _palette({"_inkypi_theme": {"mode": mode}})
        canvas = PageCanvas(palette, family="Microsoft YaHei", size=(40, 40))
        before = canvas.image.convert("RGB").copy()
        room = _outcome_mark(canvas, {}, {"name": name}, {}, 10, 20, 17, (0x1F, 0x6F, 0xEB))
        badge = canvas.image.convert("RGB").crop((10, 11, 28, 29))
        assert room == 22
        assert badge.tobytes() != before.crop((10, 11, 28, 29)).tobytes()
        # A disc in the row's tint with the name's first character in white on it (inside the disc,
        # away from the page around it).
        colours = badge.getcolors(4096)
        assert any(colour == (0x1F, 0x6F, 0xEB) and count > 80 for count, colour in colours)
        letter = canvas.image.convert("RGB").crop((14, 16, 24, 26)).getcolors(4096)
        assert sum(count for count, pixel in letter if min(pixel) >= 200) >= 4


@pytest.mark.parametrize("mode", ["day", "night"])
def test_rate_decision_outcomes_get_a_direction_disc_in_the_fed_colours(mode):
    from plugins.world_odds.render import FED_COLORS, _mark_room, _outcome_kind, _outcome_mark, _palette
    from utils.page_canvas import PageCanvas

    names = ("No change", "No Change", "25 bps decrease", "50+ bps decrease", "25 bps increase", "50+ bps increase",
             "75 bps cut", "No")
    assert [_outcome_kind(name) for name in names] == [
        ("rate", "hold", 0), ("rate", "hold", 0), ("rate", "cut", 1), ("rate", "cut", 2), ("rate", "hike", 1),
        ("rate", "hike", 2), ("rate", "cut", 2), "no"]
    assert _mark_room(("rate", "cut", 1), None, 17) == _mark_room("yes", None, 17)
    for name, key in (("No change", "维持"), ("25 bps decrease", "降息25"), ("50+ bps increase", "加息>25")):
        canvas = PageCanvas(_palette({"_inkypi_theme": {"mode": mode}}), family="Microsoft YaHei", size=(40, 40))
        room = _outcome_mark(canvas, {}, {"name": name}, {}, 10, 20, 17, (0, 0, 0))
        disc = canvas.image.convert("RGB").crop((10, 11, 28, 29)).getcolors(4096)
        assert room == 22
        # The disc in its Fed colour with a white glyph on it.
        assert any(colour == FED_COLORS[key] and count > 80 for count, colour in disc)
        assert any(colour == (255, 255, 255) and count > 6 for count, colour in disc)


def test_none_and_us_state_outcomes_get_their_own_marks():
    from plugins.world_odds.render import CATEGORY_DOODLES, US_STATES, _icon, _mark_room, _outcome_kind

    assert [_outcome_kind(name) for name in ("None", "IA, KS", "IA", "Ohio", "IA, Ohio")] == [
        "none", ("states", ("IA", "KS")), ("states", ("IA",)), None, None]
    assert len(US_STATES) == 50
    assert all(_icon(name, 11, (255, 255, 255)) is not None for name in [*CATEGORY_DOODLES.values(), "none"])
    # A second state's flag overlaps the first's right half.
    one, two = _mark_room(("states", ("IA",)), None, 22), _mark_room(("states", ("IA", "KS")), None, 22)
    assert one < two < one * 2
    assert _mark_room("none", None, 22) == _mark_room("yes", None, 22) == 27


@pytest.mark.parametrize("mode", ["day", "night"])
def test_a_tile_without_a_picture_shows_its_category_icon_on_the_page(mode):
    from PIL import ImageColor

    from plugins.world_odds.render import DAY, LEAD, LEFT, NIGHT

    tile = {"id": "t", "title": "Prime Minister of Israel after the next election?", "category": "地缘",
            "volume_24h": 5e5, "image": None, "outcomes": [{"name": "Yes", "chance": 0.4, "change": 0.0}]}
    image = render_page((800, 480), {"lead": None, "tiles": [tile], "board": {}}, {"_inkypi_theme": {"mode": mode}}, NOW, {})
    left, top = LEFT[0], LEAD[3] + 8
    track = ImageColor.getrgb((DAY if mode == "day" else NIGHT)["track"])
    badge = image.convert("RGB").crop((left + 10, top + 8, left + 44, top + 42))

    assert sum(1 for pixel in badge.get_flattened_data() if pixel == track) < 10


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_lead_card_is_framed_in_polymarket_blue_under_a_band(mode):
    from plugins.world_odds.render import LEAD, POLYMARKET

    image = render_page((800, 480), {"lead": None, "tiles": [], "board": {}}, {"_inkypi_theme": {"mode": mode}}, NOW, {})
    left, top, right, bottom = LEAD
    middle = (top + bottom) // 2

    assert image.getpixel(((left + right) // 2, top + 3)) == POLYMARKET
    assert image.getpixel((left, middle)) == image.getpixel((right, middle)) == POLYMARKET
    assert image.getpixel(((left + right) // 2, bottom)) == POLYMARKET


def test_the_lead_picture_loses_its_flat_ground_but_a_photo_keeps_its_frame():
    from PIL import Image, ImageDraw

    from plugins.world_odds.render import cutout

    seal = Image.new("RGB", (120, 120), (76, 105, 113))
    ImageDraw.Draw(seal).ellipse((10, 10, 110, 110), fill=(250, 250, 250))
    ImageDraw.Draw(seal).ellipse((50, 50, 70, 70), fill=(76, 105, 113))
    art = cutout(seal, 60)
    # The ground goes; the same colour inside the seal stays.
    assert art.size == (101, 101) and art.getpixel((0, 0))[3] == 0
    assert art.getpixel((50, 50)) == (76, 105, 113, 255)
    # A picture already cut out keeps its transparency, cropped to the seal.
    round_png = Image.new("RGBA", (120, 120), (76, 105, 113, 0))
    ImageDraw.Draw(round_png).ellipse((20, 0, 100, 119), fill=(250, 250, 250, 255))
    assert cutout(round_png, 60).size == (81, 120)
    assert cutout(Image.effect_noise((120, 120), 80).convert("RGB"), 60) is None


def test_pictures_keep_their_transparency_in_their_own_cache(tmp_path):
    import io
    import time

    from PIL import Image

    from runtime.refresh_contracts import TaskContext
    from utils.remote_thumbnails import load_thumbnails

    buffer = io.BytesIO()
    Image.new("RGBA", (40, 40), (76, 105, 113, 0)).save(buffer, format="PNG")
    url = "https://polymarket-upload.s3.us-east-2.amazonaws.com/seal.png"

    class Http:
        def request_bytes(self, _method, _url, **_kwargs):
            return type("Response", (), {"data": buffer.getvalue()})()

    kwargs = {"http": Http(), "context": TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30),
              "safe_url": sources.safe_image_url, "user_agent": "test"}
    kept = load_thumbnails([url], tmp_path, cached_only=False, keep_alpha=True, **kwargs)[url]
    flat = load_thumbnails([url], tmp_path, cached_only=False, **kwargs)[url]
    cached = load_thumbnails([url], tmp_path, cached_only=True, keep_alpha=True, **kwargs)[url]

    assert (kept.mode, flat.mode, cached.mode) == ("RGBA", "RGB", "RGBA")
    assert cached.getpixel((0, 0))[3] == 0 and len(list(tmp_path.iterdir())) == 2


def _listing(*rows):
    return [{"id": str(identifier), "title": title, "volume24hr": volume} for identifier, title, volume in rows]


def _full(identifier, title, volume):
    return {"id": str(identifier), "title": title, "volume24hr": volume, "liquidity": 3.8e7, "openInterest": 4.2e6,
            "markets": [_market("Yes side", 0.6, 0.02), _market("Other side", 0.3, -0.01)]}


class _FakeGamma:
    """Polymarket's listing (without markets) per tag and each event in full, with chosen failures."""

    def __init__(self, lists, events, broken=()):
        self.lists, self.events, self.broken, self.calls = lists, events, set(broken), []

    def __call__(self, url, context, *, max_bytes, params=None):
        self.calls.append((url, dict(params or {}), max_bytes))
        if url == sources.POLYMARKET_EVENTS:
            assert params["include_markets"] == "false" and max_bytes <= 256 * 1024
            if params["tag_slug"] in self.broken:
                raise RuntimeError("HTTP response exceeded the cap")
            return self.lists.get(params["tag_slug"], [])
        identifier = url.rsplit("/", 1)[-1]
        if identifier in self.broken:
            raise RuntimeError("event failed")
        return self.events[identifier]


def _gamma(broken=()):
    lists = {"politics": _listing((1, "Senate swing states", 2.4e7), (2, "Balance of Power", 8e6)),
             "geopolitics": _listing((3, "US-Iran ceasefire", 1.3e6)),
             "economy": _listing((4, "Fed Decision in December?", 6e5), (5, "Hormuz traffic", 9e4),
                                 (6, "China sanctions", 4e4)),
             "tech": _listing((7, "Best AI model", 4.6e4)),
             "crypto": _listing((8, "Bitcoin in November", 1.6e6))}
    events = {row["id"]: _full(row["id"], row["title"], row["volume24hr"]) for rows in lists.values() for row in rows}
    return _FakeGamma(lists, events, broken)


def test_polymarket_lists_without_markets_then_reads_only_the_lead_and_tiles(monkeypatch):
    gamma = _gamma()
    monkeypatch.setattr(sources, "_get", gamma)
    events = sources.fetch_polymarket(object())["events"]
    read = [url.rsplit("/", 1)[-1] for url, _params, _cap in gamma.calls if url != sources.POLYMARKET_EVENTS]

    # The lead (busiest of all), then each category's busiest that the Kalshi board does not price.
    assert read == ["1", "2", "3", "5", "7", "8"]
    lead, tiles = sources.lead_and_categories(events)
    assert lead["id"] == "1" and [tile["id"] for tile in tiles] == ["2", "3", "5", "7", "8"]
    assert (lead["liquidity"], lead["open_interest"]) == (3.8e7, 4.2e6)


def test_one_failing_category_or_event_leaves_the_others(monkeypatch):
    # The politics listing is too large and Hormuz's event fails: the lead moves to the next
    # busiest, economy falls back to its next event, the rest stand.
    monkeypatch.setattr(sources, "_get", _gamma(broken={"politics", "5"}))
    lead, tiles = sources.lead_and_categories(sources.fetch_polymarket(object())["events"])

    assert lead["id"] == "8"
    assert sorted(tile["id"] for tile in tiles) == ["3", "6", "7"]


def test_cancellation_still_ends_the_polymarket_walk(monkeypatch):
    from runtime.refresh_contracts import TaskCancelled

    def cancelled(*_args, **_kwargs):
        raise TaskCancelled("stop")

    monkeypatch.setattr(sources, "_get", cancelled)
    with pytest.raises(TaskCancelled):
        sources.fetch_polymarket(object())


def test_nothing_read_is_an_error_so_the_cache_stands(monkeypatch):
    monkeypatch.setattr(sources, "_get", _gamma(broken=set(sources.CATEGORIES)))
    with pytest.raises(ValueError):
        sources.fetch_polymarket(object())


def test_outcomes_carry_their_week_move_when_the_market_has_one():
    week_old = {**_market("Google", 0.555, 0.02), "oneWeekPriceChange": 0.22}
    young = _market("Anthropic", 0.385, 0.02)
    multi = sources.normalize_polymarket_event({"id": 1, "title": "Best AI model", "markets": [week_old, young]}, "科技")
    binary = sources.normalize_polymarket_event(
        {"id": 2, "title": "Hormuz", "markets": [{"outcomes": '["Yes", "No"]', "outcomePrices": '["0.1", "0.9"]',
                                                  "oneDayPriceChange": -0.01, "oneWeekPriceChange": "0.03"}]}, "经济")

    assert [(o["name"], o["week"]) for o in multi["outcomes"]] == [("Google", 0.22), ("Anthropic", None)]
    assert [(o["name"], o["week"]) for o in binary["outcomes"]] == [("No", -0.03), ("Yes", 0.03)]


def test_kalshi_rows_carry_their_money_and_headline_market():
    fed = sources.normalize_kalshi_event({"markets": [
        {"ticker": "KXFEDDECISION-26DEC-H0", "yes_sub_title": "Fed maintains rate", "last_price_dollars": "0.27",
         "volume_24h_fp": "29896.84", "open_interest_fp": "488266.88", "notional_value_dollars": "1.0000"},
        {"ticker": "KXFEDDECISION-26DEC-H25", "yes_sub_title": "Hike 25bps", "last_price_dollars": "0.73",
         "volume_24h_fp": "34064.58", "open_interest_fp": "378726.49"}]}, "decision")
    cpi = sources.normalize_kalshi_event({"markets": [
        {"ticker": "KXCPIYOY-26NOV-T3.8", "strike_type": "greater", "floor_strike": 3.8, "last_price_dollars": "0.61"},
        {"ticker": "KXCPIYOY-26NOV-T3.9", "strike_type": "greater", "floor_strike": 3.9, "last_price_dollars": "0.41"},
        {"ticker": "bad ticker/../x", "strike_type": "greater", "floor_strike": 4.0, "last_price_dollars": "0.50"}]},
        "percent")

    assert fed["volume_24h"] == pytest.approx(63961.42) and fed["open_interest"] == pytest.approx(866993.37)
    assert sources.spark_market("KXFEDDECISION", {**fed, "kind": "decision"}) == {
        "ticker": "KXFEDDECISION-26DEC-H25", "name": "加息25"}
    # CPI's pivot is the valid threshold nearest even odds.
    assert sources.spark_market("KXCPIYOY", {**cpi, "kind": "percent"}) == {
        "ticker": "KXCPIYOY-26NOV-T3.9", "strike": 3.9}
    assert sources.spark_market("KXGDP", {**cpi, "kind": "percent"}) is None


def test_kalshi_candles_close_on_the_last_trade_else_the_quote(monkeypatch):
    seen = {}

    def fake_get(url, _context, *, max_bytes, params=None):
        seen.update(url=url, params=params, cap=max_bytes)
        return {"candlesticks": [
            {"end_period_ts": 200, "price": {"close_dollars": None},
             "yes_bid": {"close_dollars": "0.41"}, "yes_ask": {"close_dollars": "0.43"}},
            {"end_period_ts": 100, "price": {"close_dollars": "0.29"}},
            {"end_period_ts": 300, "price": {}, "yes_bid": {}, "yes_ask": {}}, "junk"]}

    monkeypatch.setattr(sources, "_get", fake_get)
    points = sources.fetch_candles("KXCPIYOY", "KXCPIYOY-26NOV-T3.9", object(), NOW)["points"]

    assert points == [[100, 0.29], [200, pytest.approx(0.42)]]
    assert seen["url"].endswith("/series/KXCPIYOY/markets/KXCPIYOY-26NOV-T3.9/candlesticks")
    assert seen["params"]["period_interval"] == 1440
    assert seen["params"]["end_ts"] - seen["params"]["start_ts"] == 30 * 86400
    with pytest.raises(ValueError):
        sources.fetch_candles("KXCPIYOY", "../evil", object(), NOW)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_shows_week_moves_money_and_month_sparklines(mode):
    from PIL import ImageChops

    lead = {"id": "a", "title": "Which Senate Swing States will Republicans win?", "category": "政治",
            "volume_24h": 2.4e7, "volume": 4.8e7, "liquidity": 3.8e7, "open_interest": 4.2e6, "comments": 14,
            "ends": "2026-11-03", "image": None,
            "outcomes": [{"name": f"Outcome {n}", "chance": 0.4 / n, "change": 0.01,
                          "week": (-0.2 if n > 1 else None), "token": str(n)} for n in range(1, 5)]}
    tiles = [{**lead, "id": c, "category": c} for c in ("地缘", "经济", "科技", "加密")]
    board = {"KXFEDDECISION": {"label": "美联储议息", "kind": "decision", "date": "2026-12-09", "volume_24h": 7.8e4,
                               "open_interest": 1.9e6,
                               "outcomes": [{"name": "维持", "chance": 0.27, "change": -0.02},
                                            {"name": "加息25", "chance": 0.73, "change": 0.02}]},
             "KXCPIYOY": {"label": "CPI 同比", "kind": "percent", "median": 3.9, "when": "In Nov 2026",
                          "ladder": [[3.0, 0.99], [3.9, 0.41], [5.0, 0.01]]}}
    sparks = {"KXFEDDECISION": {"name": "加息25", "points": [[n * 86400, 0.7 + n / 300] for n in range(30)]},
              "KXCPIYOY": {"strike": 3.9, "points": [[n * 86400, 0.3 + n / 100] for n in range(30)]}}
    payload = {"lead": lead, "tiles": tiles, "board": board, "histories": {}, "sparks": sparks}
    theme = {"_inkypi_theme": {"mode": mode}}
    full = render_page((800, 480), payload, theme, NOW, {})
    bare = render_page((800, 480), {**payload, "sparks": {}}, theme, NOW, {})
    # A page cached before these details (no week, money or sparks) still draws.
    old = render_page((800, 480), {"lead": {key: value for key, value in lead.items()
                                            if key not in ("liquidity", "open_interest")},
                                   "tiles": [], "board": {}}, theme, NOW, {})

    assert full.size == old.size == (800, 480)
    # The sparklines land on the Kalshi board and nowhere else.
    box = ImageChops.difference(full.convert("RGB"), bare.convert("RGB")).getbbox()
    assert box is not None and box[0] >= 528
