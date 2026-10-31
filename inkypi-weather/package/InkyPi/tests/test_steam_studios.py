"""Studio logos off their avatars' grounds and plates (plugins.base_plugin.steam_studios)."""

from PIL import Image, ImageDraw, ImageFilter

from plugins.base_plugin import steam_studios


def _valve_like():
    """A red avatar with a white box round red letters, its edges blurred as a JPEG's are."""
    avatar = Image.new("RGB", (96, 96), (244, 80, 77))
    draw = ImageDraw.Draw(avatar)
    draw.rectangle((8, 36, 87, 59), fill=(255, 255, 255))
    for left in range(14, 80, 13):
        draw.rectangle((left, 41, left + 7, 54), fill=(244, 80, 77))
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
