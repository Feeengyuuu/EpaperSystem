"""MotoGP riders' official profiles (faces and team colours), their MGP faces when the owner installs
them, and the panel that shows them beside each rider."""

import contextlib
import io
import shutil
import sys
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.sports_dashboard import extra_feeds, motogp, motogp_render, motogp_riders, score_fonts  # noqa: E402
from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS  # noqa: E402
from plugins.sports_dashboard.sports_dashboard import SportsDashboard  # noqa: E402
from tests import test_sports_extra_feeds as feeds  # noqa: E402

MARQUEZ = "23e50438-a657-4fb0-a190-3262b5472f29"
ACOSTA = "5b9af34e-da94-4ca2-9c4c-6be0fc8b1bbc"
GREEN = (20, 170, 60, 255)


def _cut_out():
    """A full-length cut-out like the riders API's: a green head at the top left of centre, a body
    under it, on a transparent ground."""
    picture = Image.new("RGBA", (400, 900), (0, 0, 0, 0))
    draw = ImageDraw.Draw(picture)
    draw.ellipse((140, 40, 220, 130), fill=GREEN)
    draw.rectangle((90, 130, 270, 860), fill=(200, 0, 0, 255))
    return picture


def _png(picture):
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    return buffer.getvalue()


class _Client:
    def __init__(self, fail=()):
        self.asked = []
        self.fail = set(fail)

    def request_json(self, method, url, **kwargs):
        self.asked.append(url)
        uuid = url.rsplit("/", 1)[-1]
        if uuid in self.fail:
            raise RuntimeError("down")
        return feeds._Response({"name": "Marc", "surname": "Marquez", "career": [
            {"current": False, "team": {"name": "Gresini", "color": "#0000ff"}},
            {"current": True, "team": {"name": "Ducati Lenovo Team", "color": "#ad0000"},
             "pictures": {"profile": {"main": f"https://photos.motogp.com/riders/{uuid}/profile/main.png"}}}]})

    def request_bytes(self, method, url, **kwargs):
        self.asked.append(url)
        return feeds._Response(_png(_cut_out()))


def test_a_surname_keeps_its_particles():
    assert motogp_riders.surname("Fabio Di Giannantonio") == "Di Giannantonio"
    assert motogp_riders.surname("Marc Marquez") == "Marquez"
    assert motogp_riders.surname("Ai Ogura") == "Ogura"
    assert motogp_riders.surname("") == ""


def test_the_face_is_the_figures_head_and_shoulders():
    face = motogp_riders.head_and_shoulders(_cut_out())
    assert face.size == (motogp_riders.FACE_SIZE, motogp_riders.FACE_SIZE)
    # The head stands in the middle of the square's top, the shoulders fill its foot.
    top = [face.getpixel((x, face.height // 4)) for x in range(face.width)]
    greens = [x for x, pixel in enumerate(top) if pixel[1] > 120 and pixel[0] < 90 and pixel[3] > 200]
    assert greens and abs((greens[0] + greens[-1]) / 2 - face.width / 2) <= 4
    assert face.getpixel((face.width // 2, face.height - 2))[:3] == (200, 0, 0)
    assert motogp_riders.head_and_shoulders(Image.new("RGBA", (40, 80), (0, 0, 0, 0))) is None


def test_profiles_are_looked_up_once_and_read_from_the_cache_after(tmp_path, monkeypatch):
    client = _Client()
    monkeypatch.setattr(extra_feeds, "create_single_attempt_http_client", lambda: contextlib.nullcontext(client))
    plugin = feeds._Plugin(tmp_path)
    rows = [{"uuid": MARQUEZ}, {"uuid": MARQUEZ}, {"uuid": "not-a-uuid"}]

    found = motogp_riders.rider_profiles(plugin, {}, rows, feeds.NOW)
    assert set(found) == {MARQUEZ}
    profile = found[MARQUEZ]
    assert (profile["surname"], profile["team"], profile["color"]) == ("Marquez", "Ducati Lenovo Team", "#ad0000")
    assert Image.open(profile["photo"]).size == (motogp_riders.FACE_SIZE, motogp_riders.FACE_SIZE)
    assert len(client.asked) == 2
    # A theme redraw and a later refresh read the kept answer without the network.
    client.asked.clear()
    assert motogp_riders.rider_profiles(plugin, {"_theme_render_only": True}, rows, feeds.NOW) == found
    assert motogp_riders.rider_profiles(plugin, {}, rows, feeds.NOW + timedelta(days=5)) == found
    assert client.asked == []
    # A month on, the rider is looked up again (a new season's team and picture).
    motogp_riders.rider_profiles(plugin, {}, rows, feeds.NOW + timedelta(days=31))
    assert client.asked


def test_lookups_are_bounded_and_a_failed_rider_is_tried_next_time(tmp_path, monkeypatch):
    uuids = [f"00000000-0000-0000-0000-{index:012d}" for index in range(6)]
    client = _Client(fail={uuids[0]})
    monkeypatch.setattr(extra_feeds, "create_single_attempt_http_client", lambda: contextlib.nullcontext(client))
    plugin = feeds._Plugin(tmp_path)

    found = motogp_riders.rider_profiles(plugin, {}, [{"uuid": uuid} for uuid in uuids], feeds.NOW)
    assert set(found) == set(uuids[1:motogp_riders.LOOKUPS_PER_REFRESH])
    client.fail.clear()
    found = motogp_riders.rider_profiles(plugin, {}, [{"uuid": uuid} for uuid in uuids], feeds.NOW)
    assert set(found) == set(uuids)


def test_the_motogp_faces_are_optional_private_fonts(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    assert Path(score_fonts.score_font("MOTOGP", 20, True).path).name == "BarlowSemiCondensed-Bold.ttf"
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    shutil.copyfile(score_fonts.FONT_DIR / "BarlowCondensed-Bold.ttf", fonts / "sports-motogp-score.ttf")
    assert Path(score_fonts.score_font("MOTOGP", 20, True).path) == fonts / "sports-motogp-score.ttf"
    # MGP Text, for the names, is its own file.
    assert Path(score_fonts.score_font("MOTOGP_TEXT", 20, True).path).name == "BarlowSemiCondensed-Bold.ttf"
    shutil.copyfile(score_fonts.FONT_DIR / "BarlowCondensed-Bold.ttf", fonts / "sports-motogp-text-score.ttf")
    text, font = score_fonts.fit_score(ImageDraw.Draw(Image.new("RGB", (120, 30))), "MARQUEZ", 110, 12, True,
                                      sport="MOTOGP_TEXT")
    assert text == "MARQUEZ" and Path(font.path) == fonts / "sports-motogp-text-score.ttf"


def test_a_team_colour_too_dark_for_the_night_panel_takes_the_ink():
    token = _ACTIVE_COLORS.set(DEEP_NIGHT_COLORS)
    try:
        assert motogp_render.team_colour({}, {"color": "#000000"}) == DEEP_NIGHT_COLORS["text"]
        assert motogp_render.team_colour({}, {"color": "#ff6600"}) == (255, 102, 0)
        assert motogp_render.team_colour({"constructor": "KTM"}, {}) == (255, 102, 0)
    finally:
        _ACTIVE_COLORS.reset(token)
    token = _ACTIVE_COLORS.set(DAY_COLORS)
    try:
        assert motogp_render.team_colour({}, {"color": "#000000"}) == (0, 0, 0)
    finally:
        _ACTIVE_COLORS.reset(token)


def test_the_panel_shows_each_riders_face_and_team_colour(monkeypatch, tmp_path):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    face = tmp_path / "face.png"
    Image.new("RGBA", (72, 72), GREEN).save(face)
    data = {"current": motogp.pick_events(feeds.EVENTS, feeds.NOW)["current"], "sessions": motogp.normalize_sessions(feeds.SESSIONS),
            "result": {"session": "RAC", "event": "MAL", "rows": motogp.normalize_classification(feeds.CLASSIFICATION)},
            "standings": motogp.normalize_standings(feeds.STANDINGS)}
    card = motogp.build_card(data, feeds.NOW)
    for row in card["result"]["rows"] + card["standings"]:
        row["uuid"] = MARQUEZ if row["number"] == 93 else ACOSTA
    card["riders"] = {MARQUEZ: {"surname": "Marquez", "color": "#ad0000", "photo": str(face)}}
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    drawn = []
    original = plugin._draw_text_in_box
    monkeypatch.setattr(plugin, "_draw_text_in_box", lambda draw, box, text, *args, **kwargs: drawn.append(text) or
                        original(draw, box, text, *args, **kwargs))
    token = _ACTIVE_COLORS.set(DAY_COLORS)
    try:
        image = Image.new("RGB", (556, 268), DAY_COLORS["paper"])
        plugin._draw_motogp_panel(image, ImageDraw.Draw(image), (0, 0, 555, 267), card, "MOTOGP LIVE", feeds.NOW)
    finally:
        _ACTIVE_COLORS.reset(token)

    pixels = set(image.get_flattened_data())
    # Marquez's face in both columns, his points bar in Ducati Lenovo's red; Acosta, without a
    # profile, keeps his name and number (no face).
    assert GREEN[:3] in pixels and (173, 0, 0) in pixels
    assert {"MARQUEZ", "ACOSTA", "93", "37"} <= set(drawn)
    greens = [index for index, pixel in enumerate(image.get_flattened_data()) if pixel == GREEN[:3]]
    columns = {index % 556 < 278 for index in greens}
    assert columns == {True, False}
