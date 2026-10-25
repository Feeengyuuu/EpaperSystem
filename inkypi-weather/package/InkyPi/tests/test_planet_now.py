"""环球此刻 (Planet Now): its feeds' parsers, the satellite crop, the rain check and the page."""

import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.planet_now import render, sources  # noqa: E402

HEADER_TOP = render.HEADER + 6

FUEL = """Time,Solar,Wind,Geothermal,Biomass,Biogas,Small hydro,Coal,Nuclear,Natural Gas,Large Hydro,Batteries,Imports,Other
00:00,-61,1114,777,190,120,147,-7,1099,8924,1452,3688,3726,0
00:05,-60,1100,777,190,120,147,-7,1099,8900,1450,3600,3700,0
00:15,0,1000,777,190,120,147,0,1100,9000,1400,3000,3600,0
12:00,15000,2000,780,200,120,150,0,1100,1500,1500,-2500,1000,0
"""
DEMAND = """Time,Day ahead forecast,Hour ahead forecast,Current demand,Demand response
00:00,21904,21308,21255,
00:15,21500,21200,21100,
12:00,22000,21800,,
18:45,26000,25500,,
"""
TRENDS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel><title>Daily Search Trends</title>
<item><title>world series</title><ht:approx_traffic>200,000+</ht:approx_traffic>
<ht:picture>https://encrypted-tbn0.gstatic.com/images?q=a</ht:picture>
<ht:news_item><ht:news_item_title>Game 1 &amp; more</ht:news_item_title></ht:news_item></item>
<item><title>tank dell</title><ht:approx_traffic>500+</ht:approx_traffic></item>
<item><title>epl</title><ht:approx_traffic>500,000+</ht:approx_traffic>
<ht:news_item><ht:news_item_title>Spurs win at Chelsea</ht:news_item_title><ht:news_item_source>ESPN</ht:news_item_source>
</ht:news_item></item>
</channel></rss>"""
PEOPLE = {"number": 4, "iss_expedition": 75, "people": [
    {"name": "Zhang Zhiyuan", "flag_code": "cn", "position": "Pilot", "spacecraft": "Shenzhou 23",
     "launched": 1779628116, "iss": False, "image": "https://upload.wikimedia.org/wikipedia/commons/6/68/Zhang_(2026).jpg"},
    {"name": "Zhu Yangzhu", "flag_code": "cn", "position": "Commander", "spacecraft": "Shenzhou 23",
     "launched": 1779628116, "iss": False, "image": "https://upload.wikimedia.org/wikipedia/commons/9/97/Zhu.jpg"},
    {"name": "Jessica Watkins", "flag_code": "us", "position": "Flight Engineer", "spacecraft": "Crew-13 Dragon",
     "launched": 1790867406, "iss": True, "image": "https://example.com/watkins.jpg"},
    {"name": "Pyotr Dubrov", "flag_code": "ru", "position": "Commander", "spacecraft": "Soyuz MS-29",
     "launched": 1784040463, "iss": True, "image": ""},
]}


def test_the_grid_parser_steps_the_day_and_counts_renewables():
    grid = sources.parse_grid(FUEL, DEMAND)

    # 15-minute steps plus the newest reading; batteries count only while discharging.
    assert [point["time"] for point in grid["series"]] == ["00:00", "00:15", "12:00"]
    assert grid["now"]["solar"] == 15000 and grid["now"]["batteries"] == 0 and grid["now"]["hydro"] == 1650
    renewables = 15000 + 2000 + 780 + 200 + 120 + 150
    assert grid["renewable_share"] == pytest.approx(renewables / grid["supply_mw"])
    assert grid["demand"][-1] == {"time": "00:15", "mw": 21100}
    assert grid["peak"] == {"time": "18:45", "mw": 26000}
    assert sources.parse_grid("Time,Solar\n", DEMAND) == {}


def test_the_satellite_crop_is_centred_on_home():
    image = Image.new("RGB", (2400, 2400), (0, 0, 0))
    ImageDraw.Draw(image).rectangle((sources.GOES_HOME[0] - 3, sources.GOES_HOME[1] - 3,
                                     sources.GOES_HOME[0] + 3, sources.GOES_HOME[1] + 3), fill=(255, 0, 0))

    crop, home = sources.cut_goes(image)

    assert crop.size == sources.GOES_SIZE
    assert home == (sources.GOES_SIZE[0] // 2, sources.GOES_SIZE[1] // 2)
    assert crop.getpixel(tuple(home))[0] > 150
    assert sources.decode_image(sources.encode_jpeg(crop)).size == sources.GOES_SIZE
    # The Mercator grid puts San Francisco up and left of home, San Jose down and right, and the
    # rain check's 30 km ring about 47 pixels round home.
    sf, san_jose = sources.sky_position(37.7793, -122.4193), sources.sky_position(37.3382, -121.8863)
    assert sf[0] < home[0] - 50 and sf[1] < home[1] - 30
    assert san_jose[0] > home[0] and san_jose[1] > home[1] + 25
    assert 40 < sources.sky_pixels(30) < 55


def test_the_rain_check_measures_echoes_round_home():
    x, y, px, py = sources.tile_position(sources.HOME[0], sources.HOME[1], 7)
    assert (x, y) == (20, 49) and 0 <= px < 256 and 0 <= py < 256
    tile = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    assert sources.rain_coverage(tile, (px, py), 10) == 0
    ImageDraw.Draw(tile).rectangle((0, 0, 255, py), fill=(0, 120, 255, 255))
    assert 0.4 < sources.rain_coverage(tile, (px, py), 10) < 0.7


def test_feeds_pick_what_the_page_shows():
    views = [{"image": f"e{lon}", "date": "2026-10-23 00:00:00", "centroid_coordinates": {"lon": lon}}
             for lon in (162, 54, -108, -162)]
    assert sources.pick_epic(views)["image"] == "e-108"

    # The most searched first, each with its headline and outlet.
    trends = sources.parse_trends(TRENDS)
    assert [item["title"] for item in trends] == ["epl", "world series", "tank dell"]
    assert trends[0]["news"] == "Spurs win at Chelsea" and trends[0]["source"] == "ESPN"
    assert trends[1] == {"title": "world series", "traffic": 200000, "source": "",
                         "picture": "https://encrypted-tbn0.gstatic.com/images?q=a", "news": "Game 1 & more"}
    assert trends[2]["traffic"] == 500 and trends[2]["picture"] == ""

    feed = {"mostread": {"date": "2026-10-23Z", "articles": [
        {"title": "Main_Page", "normalizedtitle": "Main Page", "views": 9},
        {"title": "Special:Search", "normalizedtitle": "Special:Search", "views": 8},
        {"title": "Deaths_in_2026", "normalizedtitle": "Deaths in 2026", "views": 7},
        {"title": "Tom_Skerritt", "normalizedtitle": "Tom Skerritt", "views": 406636, "description": "Actor",
         "thumbnail": {"source": "https://upload.wikimedia.org/a.jpg"}}]}}
    assert sources.parse_most_read(feed) == {"date": "2026-10-23", "articles": [
        {"title": "Tom Skerritt", "views": 406636, "description": "Actor",
         "thumbnail": "https://upload.wikimedia.org/a.jpg"}]}

    def raw(sol, instrument, sample="Full"):
        return {"sol": sol, "sample_type": sample, "camera": {"instrument": instrument},
                "date_taken_utc": "2026-10-25T07:00:00", "image_files": {"medium": f"https://mars.nasa.gov/{instrument}.jpg"}}

    mars = sources.pick_mars({"images": [raw(2019, "SHERLOC_WATSON"), raw(2019, "NAVCAM_LEFT"),
                                         raw(2019, "MCZ_LEFT", "Thumbnail"), raw(2010, "MCZ_LEFT")]})
    # The best camera of the last few sols; a thumbnail is never shown.
    assert (mars["sol"], mars["camera"]) == (2019, "导航相机")
    assert sources.ocean_name(-8.8, 108.0) == "印度洋" and sources.ocean_name(30, -150) == "太平洋"
    assert sources.safe_image_url("https://mars.nasa.gov/x.jpg") and not sources.safe_image_url("http://mars.nasa.gov/x")


def test_the_crews_in_orbit_group_by_station_with_commanders_first():
    people = sources.parse_people(PEOPLE)

    assert people["total"] == 4
    tiangong, iss = people["stations"]
    assert tiangong["station"] == "tiangong" and tiangong["launched"] == 1779628116
    assert [person["name"] for person in tiangong["crew"]] == ["Zhu Yangzhu", "Zhang Zhiyuan"]
    # Commons portraits come as small thumbnails; other links are kept as they are.
    assert tiangong["crew"][0]["image"] == (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/9/97/Zhu.jpg/120px-Zhu.jpg")
    assert iss["station"] == "iss" and iss["expedition"] == 75 and iss["launched"] == 1784040463
    assert [person["commander"] for person in iss["crew"]] == [True, False]
    assert iss["crew"][1]["image"] == "https://example.com/watkins.jpg"
    assert sources.parse_people({}) == {"total": 0, "stations": []} and not sources.valid_people({"stations": []})


def _payload():
    picture = Image.new("RGB", sources.GOES_SIZE, (40, 60, 120))
    return {
        "goes": {"data": {"image": sources.encode_jpeg(picture), "home": [109, 102],
                          "taken_at": "2026-10-25T14:55:45+00:00"}},
        "rain": {"data": {"coverage": 0.25}},
        "epic": {"data": {"image": sources.encode_jpeg(Image.new("RGB", (120, 120), (20, 90, 160))),
                          "date": "2026-10-23"}},
        "grid": {"data": sources.parse_grid(FUEL, DEMAND)},
        "trends": {"data": {"items": sources.parse_trends(TRENDS)}},
        "wiki": {"data": {"articles": [{"title": "Tom Skerritt", "views": 406636, "description": "Actor",
                                        "thumbnail": ""}]}},
        "apple": {"data": {"songs": [{"name": "Housewife", "artist": "Tate McRae", "artwork": ""}]}},
        "mars": {"data": {"sol": 2019, "camera": "导航相机", "image": "https://mars.nasa.gov/a.jpg"}},
        "iss": {"data": {"lat": -8.8, "lon": 108.0, "country": "ID", "altitude": 417, "velocity": 27589,
                         "daylight": False, "track": [[-20, 90], [0, 120]]}},
        "people": {"data": sources.parse_people(PEOPLE)},
    }


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_draws_four_cards_in_their_own_colours(mode):
    mars = Image.new("RGB", (400, 300), (190, 120, 80))
    now = datetime(2026, 10, 25, 8, 5, tzinfo=sources.PACIFIC)

    image = render.render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, now,
                               {"https://mars.nasa.gov/a.jpg": mars})

    palette = render._palette({"_inkypi_theme": {"mode": mode}})
    assert image.size == (800, 480)
    for key, (left, top, right, _bottom) in render.CARDS.items():
        assert image.getpixel(((left + right) // 2, top + 2)) == palette[key]
    # The satellite picture fills the sky card and the Mars photo the space card.
    left, top, _right, _bottom = render.CARDS["earth"]
    assert image.getpixel((left + 60, top + 150)) != palette["paper"]
    left, top, right, _bottom = render.CARDS["space"]
    assert image.getpixel((left + 90, top + 110)) == (190, 120, 80)
    # The commander's portrait is ringed in gold in the crew column.
    crew_left = right - 124
    ring = [image.getpixel((x, top + HEADER_TOP + 47)) for x in range(crew_left, crew_left + 30)]
    assert palette["gold"] in ring


def test_the_page_says_what_is_missing_without_failing():
    image = render.render_page((800, 480), {}, {}, datetime(2026, 10, 25, 8, 5, tzinfo=sources.PACIFIC), {})

    assert image.size == (800, 480)
