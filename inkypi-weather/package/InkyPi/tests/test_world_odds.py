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
