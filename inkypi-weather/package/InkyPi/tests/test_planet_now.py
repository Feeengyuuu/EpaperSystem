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
    zhu = tiangong["crew"][0]
    assert (zhu["role"], zhu["craft"], zhu["country"]) == ("指令长", "神舟二十三号", "cn")
    assert iss["crew"][0]["craft"] == "联盟 MS-29" and iss["crew"][1]["craft"] == "龙飞船 Crew-13"


def test_crew_names_come_from_chinese_wikipedia_and_fall_back_to_surnames(monkeypatch):
    assert sources.chinese_number(23) == "二十三" and sources.chinese_number(10) == "十"
    assert sources.chinese_number(7) == "七" and sources.chinese_number(31) == "三十一"
    assert sources.wiki_title("https://en.wikipedia.org/wiki/Luke_Delaney_(astronaut)") == "Luke Delaney (astronaut)"
    assert sources.wiki_title("https://example.com/wiki/X") == ""
    assert sources.short_chinese_name("张志远 (航天员)") == "张志远"
    assert sources.short_chinese_name("安娜·基金娜") == "基金娜"

    people = {"stations": [{"station": "tiangong", "crew": [
        {"name": "Zhang Zhiyuan", "wiki": "Zhang Zhiyuan (astronaut)"},
        {"name": "Pyotr Dubrov", "wiki": "Pyotr Dubrov"}]}]}
    answers = {
        "en": {"query": {"pages": [{"title": "Zhang Zhiyuan (astronaut)", "langlinks": [{"title": "張志遠 (太空人)"}]},
                                   {"title": "Pyotr Dubrov"}]}},
        "zh": {"query": {"pages": [{"title": "張志遠 (太空人)", "varianttitles": {"zh-cn": "张志远 (航天员)"}}]}},
    }
    monkeypatch.setattr(sources, "_json", lambda url, _ctx, **_kw: answers["zh" if "zh." in url else "en"])

    crew = sources.add_chinese_names(people, None)["stations"][0]["crew"]

    assert crew[0]["zh_name"] == "张志远" and "zh_name" not in crew[1]
    assert render._crew_name(crew[0]) == "张志远" and render._crew_name(crew[1]) == "Dubrov"


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
    crew_left = right - 176
    ring = [image.getpixel((x, top + HEADER_TOP + 40)) for x in range(crew_left, crew_left + 42)]
    assert palette["gold"] in ring


def test_the_grid_table_fills_its_column_to_the_card_foot(monkeypatch):
    canvas = render.PageCanvas(render._palette({}))
    labels = []
    original = canvas.text
    monkeypatch.setattr(canvas, "text", lambda xy, text, *args, **kwargs: (
        labels.append((xy, text, args[0] if args else kwargs.get("size"))), original(xy, text, *args, **kwargs))[1])

    render._grid(canvas, _payload())

    left, top, _right, bottom = render.CARDS["grid"]
    rows = [(xy, size) for xy, text, size in labels if text in render.FUEL_LABELS.values()]
    share = next(size for _xy, text, size in labels if text.endswith("%") and size > 20)
    assert share >= 36 and len(rows) >= 5
    # The source rows share the column down to the card's foot, at a legible size.
    assert bottom - 16 <= rows[-1][0][1] <= bottom - 6
    assert all(size >= 11 for _xy, size in rows)


def test_the_whole_earth_is_cut_from_the_black_of_space():
    shot = Image.new("RGB", (200, 200), (0, 0, 0))
    ImageDraw.Draw(shot).ellipse((40, 30, 160, 150), fill=(30, 90, 170))

    disc = render._earth_disc(shot, 62)

    assert disc.size == (62, 62)
    # No black ring: the corners are clear and the disc reaches close to the edge.
    assert disc.getpixel((0, 0))[3] == 0 and disc.getpixel((61, 61))[3] == 0
    assert disc.getpixel((31, 31)) == (30, 90, 170, 255)
    assert disc.getpixel((4, 31))[3] == 255 and disc.getpixel((4, 31))[:3] != (0, 0, 0)
    assert render._earth_disc(None, 62) is None


@pytest.mark.parametrize("night", [False, True])
def test_each_source_is_named_by_its_official_logo_at_its_header_height(night):
    for name, (height, _words) in render.SOURCE_LOGOS.items():
        logo = render._logo(name, night)
        assert logo is not None and logo.height == height and logo.mode == "RGBA"
    # California ISO's own white-lettered version by night; NASA has one version.
    assert render._logo("caiso", True).tobytes() != render._logo("caiso", False).tobytes()
    assert render._logo("nasa", True).tobytes() == render._logo("nasa", False).tobytes()


def test_a_missing_logo_falls_back_to_the_source_in_words(monkeypatch):
    drawn = []
    canvas = render.PageCanvas(render._palette({}))
    monkeypatch.setattr(render, "_logo", lambda name, night: None)
    monkeypatch.setattr(canvas, "text", lambda xy, text, *args, **kwargs: drawn.append(text) or 10)

    render._card(canvas, "space", "太空近况", "· wheretheiss.at", source="nasa")

    assert drawn == ["太空近况", "NASA · wheretheiss.at"]


def test_the_page_says_what_is_missing_without_failing():
    image = render.render_page((800, 480), {}, {}, datetime(2026, 10, 25, 8, 5, tzinfo=sources.PACIFIC), {})

    assert image.size == (800, 480)


def test_mars_prefers_a_colour_mosaic_frame_at_the_skyline():
    def frame(sol, instrument, sequence, azimuth, elevation, product="EBY", width=1648):
        return {"sol": sol, "sample_type": "Full", "camera": {"instrument": instrument},
                "imageid": f"ZL0_{sol}_0846179643_598{product}_N0920000{sequence}_1100LMJ",
                "extended": {"mastAz": str(azimuth), "mastEl": str(elevation), "dimension": f"({width},1200)"},
                "date_taken_utc": "2026-10-25T06:19:25", "image_files": {
                    "medium": f"https://mars.nasa.gov/{sequence}-{azimuth}-{product}-m.jpg",
                    "large": f"https://mars.nasa.gov/{sequence}-{azimuth}-{product}-l.jpg"}}

    images = [frame(2019, "MCZ_LEFT", "ZCAM05133", -20, -1.3) for _ in range(6)]  # a close-up held on one target
    images += [frame(2019, "MCZ_LEFT", "ZCAM10050", azimuth, elevation, product)
               for azimuth, elevation in ((-25, -2.9), (0, -1.2), (15, -1.9), (-20, -6.8)) for product in ("EBY", "ECM")]
    images += [frame(2019, "MCZ_LEFT", "ZCAM05072", 5, -1.0, width=288), frame(2019, "SHERLOC_WATSON", "SRLC0001", 0, 0)]

    picked = sources.pick_mars({"images": images})

    # The panorama's colour frame nearest the skyline, at its large size.
    assert picked["image"] == "https://mars.nasa.gov/ZCAM10050-15-EBY-l.jpg"
    assert sources.scenic_rank(images[-1]) is None and sources.scenic_rank(images[-2]) is None


def test_mars_distance_the_sun_and_the_iss_orbit():
    from datetime import timezone

    # Mars at its 2027 opposition and its 2026 conjunction.
    assert 0.98e8 < sources.mars_distance_km(datetime(2027, 2, 19, tzinfo=timezone.utc)) < 1.05e8
    assert 3.5e8 < sources.mars_distance_km(datetime(2026, 1, 9, tzinfo=timezone.utc)) < 3.7e8
    # The Sun stands over the Tropic of Cancer at the June solstice, near Greenwich at noon UTC.
    lat, lon = sources.subsolar_point(datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc))
    assert abs(lat - 23.4) < 0.3 and abs(lon) < 2.5
    assert sources.sun_elevation(lat, lon, (lat, lon)) > 89.9

    track = sources.iss_ground_track(10.0, -80.0, True, [0, 23.2, 46.4])
    assert abs(track[0][1] - 10.0) < 1e-6 and abs(track[0][2] + 80.0) < 1e-6
    assert max(point[1] for point in track) <= sources.ISS_INCLINATION + 1e-6
    assert track[1][1] > 45  # a quarter orbit after an ascending pass it nears the top of its track

    iss = {"lat": 0.0, "lon": -150.0, "altitude": 420, "ascending": True}
    switch = sources.iss_next_switch(iss, datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc))
    assert switch is not None and 1 <= switch[0] <= 60


def test_each_grid_source_has_an_icon_in_its_colour_and_each_station_its_mark():
    for name in (*render.FUEL_ORDER, "renewables", *render.MARS_ICONS.values()):
        icon = render._fuel_icon(name, (37, 99, 235), 13)
        assert icon is not None and icon.height == 13, name
        opaque = [pixel for pixel in icon.get_flattened_data() if pixel[3] > 200]
        assert opaque and all(pixel[:3] == (37, 99, 235) for pixel in opaque), name
    # A source colour too close to the page is moved toward the ink until it reads.
    assert render._legible((203, 213, 225), (255, 255, 255), (20, 28, 34)) != (203, 213, 225)
    assert render._legible((37, 99, 235), (255, 255, 255), (20, 28, 34)) == (37, 99, 235)
    for station in render.STATIONS:
        logo = render._station_logo(station)
        assert logo is not None and logo.height == render.STATION_LOGO_HEIGHT
    assert render._station_logo("mir") is None


def test_the_mars_feed_is_asked_again_every_two_hours():
    from plugins.planet_now import planet_now

    assert planet_now.MARS_REFRESH == 2 * planet_now.HOUR
    # Both the regular load and the single-photo cache upgrade use it; the photos stay usable 4 days.
    assert Path(planet_now.__file__).read_text(encoding="utf-8").count("MARS_REFRESH, 4 * DAY") == 2


def test_the_iss_lines_always_fill_the_column_to_the_cards_foot():
    assert render.ISS_LINE_SIZES[1] >= 16 and min(render.ISS_LINE_SIZES) >= 11


def test_wikipedias_most_read_line_is_marked_by_its_globe_on_fire_not_a_text_tag(monkeypatch):
    drawn = []
    original = render._chip
    monkeypatch.setattr(render, "_chip", lambda canvas, xy, text, **kwargs: drawn.append(text) or original(canvas, xy, text,
                                                                                                         **kwargs))
    hot = render._hot_wiki()
    assert hot is not None and hot.height == render.HOT_WIKI_HEIGHT
    # The flames are the art's own colours: red and orange pixels around the grey globe.
    assert any(r > 200 and g < 140 and b < 80 and a > 200 for r, g, b, a in hot.get_flattened_data())
    for night in (False, True):
        assert render._logo("wikipedia", night) is not None

    render.render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": "day"}},
                       datetime(2026, 10, 25, 8, 5, tzinfo=sources.PACIFIC), {})

    assert "维基热读" not in drawn


def test_the_iss_place_is_led_by_the_countrys_flag_or_the_oceans_emblem():
    from plugins.planet_now import render as page

    names = {"CN", "US", "ID", "BR", "AR", "ZA", "GL", "MN"}
    assert all(page._asset(f"flags/{code.lower()}", page.PLACE_MARK_HEIGHT) is not None for code in names)
    for ocean in page.OCEAN_EMBLEMS.values():
        assert page._asset(f"oceans/{ocean}", page.PLACE_MARK_HEIGHT + 4) is not None
    assert page._coordinates(18.4, -163.2) == "北纬 18° 西经 163°"
    assert page._coordinates(-8.8, 108.0) == "南纬 9° 东经 108°"
    # Mean solar time: 15° of longitude is an hour from Greenwich.
    assert page._solar_time(datetime(2026, 10, 30, 4, 0, tzinfo=page.timezone.utc), -165) == "17:00"
