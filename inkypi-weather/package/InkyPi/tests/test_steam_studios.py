"""Studio logos off their avatars' grounds and plates (plugins.base_plugin.steam_studios)."""

from PIL import Image, ImageDraw, ImageFilter

from plugins.base_plugin import steam_studios


def _valve_like():
    """A red avatar with a white box round red letters, its edges blurred as a JPEG's are."""
    avatar = Image.new("RGB", (96, 96), (244, 80, 77))
    draw = ImageDraw.Draw(avatar)
    draw.rectangle((8, 36, 87, 59), fill=(255, 255, 255))
    # Letter strokes, thin as lettering is.
    for left in range(14, 80, 13):
        draw.rectangle((left, 41, left + 2, 54), fill=(244, 80, 77))
        draw.rectangle((left, 52, left + 6, 54), fill=(244, 80, 77))
    return avatar.filter(ImageFilter.GaussianBlur(0.8))


def test_the_ground_and_its_blended_rim_are_cut_and_the_plate_peeled_to_the_letters():
    cut = steam_studios.cut_ground(_valve_like())
    assert cut is not None and cut.size[1] < 40  # the box, off the red square
    letters = steam_studios.peel_plate(cut)
    shown = [pixel for pixel in letters.get_flattened_data() if pixel[3] > 200]
    # Only the red letters are left: no white from the box.
    assert shown and all(red > 180 and green < 170 for red, green, _blue, _alpha in shown)
    assert letters.size[1] < cut.size[1]


def test_a_photo_has_no_ground_and_a_one_colour_mark_keeps_itself():
    photo = Image.linear_gradient("L").convert("RGB").resize((96, 96))
    assert steam_studios.cut_ground(photo) is None
    disc = Image.new("RGB", (96, 96), (255, 255, 255))
    ImageDraw.Draw(disc).ellipse((20, 20, 76, 76), fill=(20, 30, 40))
    mark = steam_studios.cut_ground(disc)
    assert steam_studios.peel_plate(mark).size == mark.size


def test_developer_clans_come_from_getitems_basic_info():
    items = [{"appid": 730, "basic_info": {"developers": [{"name": "Valve", "creator_clan_account_id": 4}]}},
             {"appid": 9, "basic_info": {"developers": [{"name": "Solo"}]}}, {"appid": "x"}]
    assert steam_studios.developer_clans(items) == {"730": "4"}
    page = "<avatarFull><![CDATA[https://avatars.fastly.steamstatic.com/" + "a" * 40 + "_full.jpg]]></avatarFull>"
    assert steam_studios.avatar_url(page).endswith("_full.jpg") and steam_studios.avatar_url("<x/>") == ""
    assert steam_studios.clan_page_url(4).endswith("/gid/103582791429521412/memberslistxml/?xml=1")


def test_white_letters_on_a_dark_ground_are_the_logo_not_a_plate():
    # Eternity's and PUBG's avatars: white lettering (and a bar) on black.
    avatar = Image.new("RGB", (96, 96), (0, 0, 0))
    draw = ImageDraw.Draw(avatar)
    draw.rectangle((20, 30, 30, 66), fill=(255, 255, 255))
    draw.rectangle((20, 30, 44, 38), fill=(255, 255, 255))
    draw.rectangle((50, 30, 76, 38), fill=(255, 255, 255))
    draw.rectangle((59, 30, 67, 66), fill=(255, 255, 255))
    draw.rectangle((34, 44, 46, 50), fill=(230, 30, 40))
    cut = steam_studios.cut_ground(avatar)
    peeled = steam_studios.peel_plate(cut)
    assert peeled.size == cut.size
    assert sum(1 for pixel in peeled.get_flattened_data() if pixel[3] > 200 and min(pixel[:3]) > 200) > 200


def test_a_studio_without_a_clan_is_matched_on_wikidata_by_name_and_trade():
    found = {"search": [
        {"id": "Q47090375", "label": "Valve", "match": {"text": "Valve"}, "description": "family name"},
        {"id": "Q193559", "label": "Valve Corporation", "match": {"text": "Valve Corporation"},
         "description": "American video game development and digital distribution company"},
    ]}
    # The family name has the name but not the trade; the company has both, its legal form aside.
    assert steam_studios.studio_entity(found, "Valve") == "Q193559"
    assert steam_studios.name_key("miHoYo Co., Ltd.") == steam_studios.name_key("miHoYo") == "mihoyo"
    assert steam_studios.name_key("Infinity Ward®") == "infinityward" and steam_studios.name_key("Co") == "co"
    assert steam_studios.studio_entity(found, "Bunnyhop") == "" and steam_studios.studio_entity({}, "Valve") == ""
    assert steam_studios.studio_search("起源工作室")["language"] == "zh"
    assert steam_studios.studio_search("Infinity Ward")["language"] == "en"


def test_the_square_logo_claim_comes_first_and_commons_serves_it_as_a_png():
    def claim(value, rank="normal"):
        return {"rank": rank, "mainsnak": {"datavalue": {"value": value}}}

    answer = {"claims": {"P154": [claim("Wide logo.svg")],
                         "P8972": [claim("Old icon.png", "deprecated"), claim("Icon.png")]}}
    assert steam_studios.logo_file(answer) == "Icon.png"
    answer["claims"]["P8972"] = []
    assert steam_studios.logo_file(answer) == "Wide logo.svg" and steam_studios.logo_file({}) == ""
    assert steam_studios.logo_file({"claims": {"P154": [claim("Logo.svg", "normal"), claim("Logo 2024.svg", "preferred")]}}) \
        == "Logo 2024.svg"
    assert steam_studios.claims_query("Q1", "P154") == {"action": "wbgetclaims", "entity": "Q1", "property": "P154",
                                                        "format": "json"}
    svg = steam_studios.commons_urls("Infinity Ward.svg")
    assert svg == ["https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/Infinity_Ward.svg/250px-Infinity_Ward.svg.png"]
    png = steam_studios.commons_urls("Icon.png")
    assert png[0].endswith("/250px-Icon.png") and png[1].endswith("/Icon.png") and "thumb" not in png[1]


def test_an_official_logo_is_cut_to_its_mark_and_a_picture_is_not_one():
    wordmark = Image.new("RGBA", (500, 140), (0, 0, 0, 0))
    ImageDraw.Draw(wordmark).rectangle((40, 40, 459, 99), fill=(10, 10, 10, 255))
    mark = steam_studios.official_mark(wordmark)
    assert mark.size == (336, 48)  # cropped to the drawing (420 x 60), then 48 px tall at most
    boxed = Image.new("RGB", (300, 100), (255, 255, 255))
    ImageDraw.Draw(boxed).rectangle((50, 30, 249, 69), fill=(200, 20, 20))
    assert steam_studios.official_mark(boxed).getpixel((0, 0))[:3] == (200, 20, 20)  # off its white ground
    photo = Image.linear_gradient("L").convert("RGB").resize((96, 96))
    assert steam_studios.official_mark(photo) is None


def test_a_cut_out_that_crumbles_into_specks_does_not_read():
    solid = Image.new("RGBA", (30, 11), (0, 0, 0, 0))
    ImageDraw.Draw(solid).rectangle((0, 0, 29, 10), fill=(200, 30, 30, 255))
    assert steam_studios.legible(solid)
    specks = Image.new("RGBA", (30, 11), (0, 0, 0, 0))
    for x in range(0, 30, 3):
        for y in range(0, 11, 3):
            specks.putpixel((x, y), (20, 20, 20, 255))
    ImageDraw.Draw(specks).rectangle((0, 0, 3, 3), fill=(20, 20, 20, 255))
    assert not steam_studios.legible(specks)
    assert not steam_studios.legible(solid.crop((0, 0, 30, 5)))  # too short to read
