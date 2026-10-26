"""Where to watch on the football cards: the networks' logos beside the status pill and after a row's date."""

from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from plugins.sports_dashboard.common import COLORS
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from plugins.sports_dashboard.tv_networks import tv_watch_rank

LA = ZoneInfo("America/Los_Angeles")
EVENT = {"start": datetime(2026, 10, 26, 13, 0, tzinfo=LA), "team_a": "塞维利亚", "team_b": "奥萨苏纳",
         "team_a_tla": "SEV", "team_b_tla": "OSA", "broadcast": "ESPN Deportes / ESPN+",
         "block": "第10轮 | ESPN Deportes / ESPN+ | Ramón Sánchez Pizjuán Stadium", "state": "PRE",
         "status": "Scheduled", "odds": {"team_a": "1.87", "draw": "3.90", "team_b": "3.80"}}


def test_streaming_comes_first_and_spanish_language_feeds_last():
    assert tv_watch_rank("espn_plus") < tv_watch_rank("espn") < tv_watch_rank("espn_deportes")
    assert tv_watch_rank(None) == tv_watch_rank("fox")


def test_the_first_choice_shrinks_to_fit_and_the_rest_stay_named():
    plugin = SportsDashboard({"id": "sports_dashboard"})

    narrow, unshown = plugin._broadcast_logo_strip(EVENT, 15, 56)
    wide, nothing = plugin._broadcast_logo_strip(EVENT, 15, 200)

    # ESPN+ is 74 px wide at 15 px: it is made smaller rather than swapped for ESPN Deportes.
    assert narrow.width <= 56 and narrow.height == 15 and unshown == ["ESPN Deportes"]
    assert wide.width > narrow.width and nothing == []
    assert SportsDashboard._stage_without_broadcast(EVENT, unshown) == "第10轮 | ESPN Deportes | Ramón Sánchez Pizjuán Stadium"
    assert SportsDashboard._stage_without_broadcast(EVENT) == "第10轮 | Ramón Sánchez Pizjuán Stadium"
    assert plugin._broadcast_logo_strip({**EVENT, "broadcast": "Tele"}, 15, 200) == (None, [])


def test_the_card_and_its_rows_carry_the_logos_and_keep_the_odds_inside_the_row():
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._draw_worldcup_presented_flag = lambda *args, **kwargs: 22
    presentation = {"competition": "club", "team_asset_kind": "logo", "main_status_include_score": False}
    image = Image.new("RGB", (270, 200), COLORS["paper"])
    draw = ImageDraw.Draw(image)

    plugin._draw_worldcup_main_card(image, draw, 4, 4, 255, 148, EVENT, datetime(2026, 10, 26, 8, 47, tzinfo=LA),
                                    "next", presentation)
    plugin._draw_worldcup_mini_match_row(image, draw, 4, 264, 156, 33, EVENT, "VS", show_time=True,
                                         presentation=presentation)

    # ESPN+'s black lettering stands between the pill and the date on the card's top row.
    top = {image.getpixel((x, y)) for x in range(130, 190) for y in range(12, 28)}
    assert any(max(pixel) < 60 for pixel in top)
    # The row's last two pixel rows above its border carry no text.
    foot = {image.getpixel((x, y)) for x in range(20, 250) for y in (156 + 30, 156 + 31)}
    assert foot <= {COLORS["panel"]}


def test_the_wnba_series_card_names_its_data_source_by_espns_logo():
    plugin = SportsDashboard({"id": "sports_dashboard"})
    image = Image.new("RGB", (300, 60), COLORS["panel"])

    left = plugin._draw_wnba_data_source(image, (120, 10, 290, 38))

    # ESPN's red wordmark ends at the box's right edge and the title stops short of it.
    reds = [x for x in range(120, 300) for y in range(10, 38)
            if (lambda r, g, b: r > 170 and g < 80 and b < 80)(*image.getpixel((x, y)))]
    assert 200 < left < 290 and reds and min(reds) >= left and max(reds) <= 290
