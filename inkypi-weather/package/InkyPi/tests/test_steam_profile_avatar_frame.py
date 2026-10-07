"""The rail avatar frame is a cached, e-paper-legible RGBA overlay."""

from PIL import Image

from plugins.steam_profile_dashboard.avatar_frame import (
    AVATAR_WINDOW,
    FRAME_SIZE,
    gilded_avatar_frame,
)

SPECTRA6 = (0, 0, 0, 255, 255, 255, 255, 255, 0, 255, 0, 0, 0, 0, 0, 0, 0, 255, 0, 255, 0)
CORNER_GEMS = ((10, 10), (168, 10), (10, 168), (168, 168))


def _is_gold(pixel):
    red, green, blue = pixel[:3]
    return red >= 150 and red >= green and green > blue + 40


def test_frame_is_a_cached_rgba_overlay_with_a_clear_avatar_window():
    frame = gilded_avatar_frame()

    assert frame.mode == "RGBA"
    assert frame.size == FRAME_SIZE
    assert gilded_avatar_frame() is frame
    assert frame.getchannel("A").crop(AVATAR_WINDOW).getextrema() == (0, 0)


def test_frame_stays_inside_the_rail_above_the_persona_name():
    left, top, right, bottom = gilded_avatar_frame().getchannel("A").getbbox()

    assert (left, top) >= (0, 0)
    assert right <= 180
    assert bottom <= 178


def test_frame_band_is_gold_with_ruby_corners_and_a_centred_crown():
    frame = gilded_avatar_frame()
    alpha = frame.getchannel("A")

    for xy in ((10, 60), (168, 120), (60, 168), (40, 10)):
        assert frame.getpixel(xy)[3] == 255
        assert _is_gold(frame.getpixel(xy))
    for xy in CORNER_GEMS:
        red, green, blue, opacity = frame.getpixel(xy)
        assert opacity == 255 and red > 150 and green < 90 and blue < 90
    assert alpha.crop((74, 0, 105, 4)).getbbox() is not None
    assert alpha.crop((25, 0, 60, 4)).getbbox() is None


def test_frame_survives_spectra6_dithering_as_yellow_gold_and_red_gems():
    canvas = Image.new("RGB", FRAME_SIZE, (0, 0, 0))
    frame = gilded_avatar_frame()
    canvas.paste(frame, (0, 0), frame)
    palette = Image.new("P", (1, 1))
    palette.putpalette(SPECTRA6 + (0, 0, 0) * 249)

    paper = canvas.quantize(palette=palette).convert("RGB")

    band_colors = paper.crop((6, 30, 15, 150)).getcolors()
    assert max(band_colors)[1] == (255, 255, 0)
    assert [paper.getpixel(xy) for xy in CORNER_GEMS] == [(255, 0, 0)] * 4
