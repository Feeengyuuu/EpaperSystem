"""The date panel's stage: backgrounds by time of day, host art and placement."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytz
from PIL import Image, ImageChops, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.presentation import PresentationRequestContext  # noqa: E402
from plugins.base_plugin.render_provenance import SourceProvenance  # noqa: E402
from plugins.simple_calendar import panel_stage  # noqa: E402
from plugins.simple_calendar.panel_scene import DAYPARTS, Scene  # noqa: E402
from plugins.simple_calendar.simple_calendar import LOCALE_DATA, SimpleCalendar  # noqa: E402

COLOR_DIR = Path(panel_stage.PLUGIN_DIR) / panel_stage.COLOR_BACKGROUND_DIR
TZ = pytz.timezone("America/Los_Angeles")
FREE_BACKGROUND = COLOR_DIR / "img2_original_heroes_weather" / "clear_day.png"


class Device:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal", "timezone": "America/Los_Angeles"}
        return values if key is None else values.get(key, default)


def scene(host="clockwork_robot", pose=None, costume="appointment", lines=("上午 11:45 预约", "还有 2 小时")):
    return Scene(host, pose, costume, lines, "day", costume or "everyday")


def render(plugin, selected_date, panel_scene=None, background=FREE_BACKGROUND):
    return plugin._render_calendar(
        (800, 480), selected_date, (46, 46, 46), (163, 13, 13), LOCALE_DATA["en"], "en", "left",
        [], str(background), True, None, None, panel_scene,
    )


def test_occupied_corner_list_names_real_backgrounds():
    manifest = json.loads((COLOR_DIR / panel_stage.OCCUPIED_CORNERS_FILE).read_text(encoding="utf-8"))

    assert manifest
    for relative, corners in manifest.items():
        assert (COLOR_DIR / relative).is_file(), relative
        assert corners and set(corners) <= {"bl", "br"}


def test_background_turns_over_with_the_time_of_day():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    day = date(2026, 5, 28)

    paths = [plugin._weather_background_path("clear_day", {}, day, daypart=part) for part in DAYPARTS]

    assert len(set(paths)) == len(DAYPARTS)
    assert paths == [plugin._weather_background_path("clear_day", {}, day, daypart=part) for part in DAYPARTS]


def test_each_daypart_still_steps_through_the_whole_pool():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    pool = plugin._weather_background_candidates("clear_night", "img2_original_heroes_mixed")
    pool += plugin._clean_plate_candidates("clear_night", "night")

    seen = {
        plugin._weather_background_path("clear_night", {}, date.fromordinal(date(2026, 1, 1).toordinal() + offset),
                                        daypart="night")
        for offset in range(len(pool))
    }

    assert seen == {str(path) for path in pool}


def test_occasions_prefer_a_background_with_free_corners():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    for offset in range(20):
        day = date.fromordinal(date(2026, 3, 1).toordinal() + offset)
        path = plugin._weather_background_path("rain", {}, day, daypart="day", free_stage=True)
        assert not plugin._occupied_corners(path), path
    # Every thunderstorm background has a figure; the pool is still used.
    assert plugin._weather_background_path("thunderstorm", {}, date(2026, 3, 1), daypart="day", free_stage=True)


def test_clean_plates_join_the_pool_for_their_daypart(tmp_path, monkeypatch):
    plates = tmp_path / panel_stage.COLOR_BACKGROUND_DIR / panel_stage.CLEAN_PLATE_DIR
    plates.mkdir(parents=True)
    for name in ("clear_day.png", "clear_dusk_01.png", "rain_night.png"):
        Image.new("RGB", (304, 480), "white").save(plates / name)
    monkeypatch.setattr(panel_stage, "PLUGIN_DIR", tmp_path)
    plugin = SimpleCalendar({"id": "simple_calendar"})

    assert [path.name for path in plugin._clean_plate_candidates("clear_day", "dusk")] == ["clear_dusk_01.png"]
    assert [path.name for path in plugin._clean_plate_candidates("clear_night", "dawn")] == ["clear_day.png"]
    assert plugin._clean_plate_candidates("clear_night", "night") == []
    chosen = {
        Path(plugin._weather_background_path("rain", {}, date.fromordinal(738000 + offset), daypart="night")).name
        for offset in range(12)
    }
    assert "rain_night.png" in chosen
    classic = plugin._weather_background_path("rain", {"weatherPanelBackgroundStyle": "classic"}, date(2026, 1, 1),
                                              daypart="night")
    assert Path(classic).name == "rain.png"


def test_host_art_prefers_costume_then_pose_then_cutout(tmp_path, monkeypatch):
    figure = Image.new("RGBA", (40, 60), (0, 0, 0, 0))
    figure.paste((200, 30, 30, 255), (5, 5, 35, 55))
    costume = tmp_path / panel_stage.PANEL_CAST_DIR / "costumes" / "appointment.png"
    pose = tmp_path / panel_stage.PANEL_CAST_DIR / "poses" / "clockwork_robot_point.png"
    cutout = tmp_path / panel_stage.DATE_HERO_CUTOUT_DIR / panel_stage.HOST_CUTOUTS["clockwork_robot"]
    for path in (costume, pose, cutout):
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.save(path)
    monkeypatch.setattr(panel_stage, "PLUGIN_DIR", tmp_path)
    plugin = SimpleCalendar({"id": "simple_calendar"})
    robot = scene(pose="point")

    assert plugin._host_art_path(robot) == (costume, "left")
    costume.unlink()
    assert plugin._host_art_path(robot) == (pose, "left")
    pose.unlink()
    assert plugin._host_art_path(robot) == (cutout, None)
    assert plugin._load_host_art(robot, "right").size == (30, 50)


def test_host_turns_to_face_its_balloon():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    skater = scene(host="shield_skater", costume=None)
    original = Image.open(plugin._host_art_path(skater)[0]).convert("RGBA")
    original = original.crop(original.getchannel("A").getbbox())

    assert plugin._load_host_art(skater, "left").tobytes() == original.tobytes()
    assert plugin._load_host_art(skater, "right").tobytes() == ImageOps.mirror(original).tobytes()


def test_stage_never_covers_the_date_text():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    for selected_date in (date(2026, 10, 22), date(2026, 10, 23)):
        plain = render(plugin, selected_date)
        for host in panel_stage.HOST_CUTOUTS:
            for costume, lines in ((None, ("离周末还有 2 天",)), ("halloween", ("万圣节快乐！", "下午 3:00 会议"))):
                staged = render(plugin, selected_date, scene(host=host, costume=costume, lines=lines))
                difference = ImageChops.difference(plain, staged)

                # Weekday, digits and rule above, the month line and the grid stay untouched.
                assert difference.crop((0, 0, 304, 345)).getbbox() is None, (host, costume)
                assert difference.crop((84, 362, 220, 386)).getbbox() is None, (host, costume)
                assert difference.crop((304, 0, 800, 480)).getbbox() is None, (host, costume)
                assert difference.crop((0, 345, 304, 480)).getbbox() is not None, (host, costume)


def test_ordinary_day_lets_the_backgrounds_figure_speak(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    pasted = []
    monkeypatch.setattr(plugin, "_paste_host", lambda *args: pasted.append(args))
    image = Image.new("RGB", (800, 480), "white")
    stage = (10, 392, 298, 475)
    occupied_left = COLOR_DIR / "img2_original_heroes_weather" / "rain.png"
    colors = plugin._stage_colors(None, (255, 255, 255), (0, 0, 0), (120, 120, 120))

    box = plugin._draw_panel_stage(image, scene(costume=None, lines=("下雨记得带伞",)), stage, [],
                                   str(occupied_left), colors)

    assert pasted == []
    assert box[2] == stage[2] - panel_stage.COMIC_SHADOW_PX


def test_occasion_host_stands_in_the_free_corner(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    pasted = []
    monkeypatch.setattr(plugin, "_paste_host", lambda img, art, box, colors: pasted.append(box))
    image = Image.new("RGB", (800, 480), "white")
    stage = (10, 392, 298, 475)
    occupied_right = COLOR_DIR / "img2_original_heroes_weather" / "snow.png"
    colors = plugin._stage_colors(None, (255, 255, 255), (0, 0, 0), (120, 120, 120))

    box = plugin._draw_panel_stage(image, scene(), stage, [], str(occupied_right), colors, side_seed=1)

    assert pasted and pasted[0][0] == stage[0]
    assert box[0] > pasted[0][2]


def test_generate_image_casts_the_scene_for_the_shown_moment(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    plugin._read_weather_context_background_slug = lambda *, include_stale=False: "rain"
    captured = {}

    def capture(*args):
        captured["background"] = args[8]
        captured["scene"] = args[12]
        return Image.new("RGB", (800, 480), "white")

    monkeypatch.setattr(plugin, "_render_calendar", capture)
    day = date(2026, 10, 22)
    appointment = {"date": day, "title": "Dentist appointment", "label": "ME", "kind": "personal",
                   "starts_at": TZ.localize(datetime(2026, 10, 22, 11, 45))}
    settings = {
        "customDate": day.isoformat(), "_theme_render_only": True, "showGameEvents": "false",
        "showAppleEvents": "false", SimpleCalendar._CACHED_EVENTS_SETTING: [appointment],
        SimpleCalendar._SCENE_TIME_SETTING: TZ.localize(datetime(2026, 10, 22, 9, 45)).isoformat(),
    }

    plugin.generate_image(settings, Device())

    assert captured["scene"].lines == ("上午 11:45 预约", "还有 2 小时")
    assert not plugin._occupied_corners(captured["background"])
    assert captured["background"] == plugin._weather_background_path("rain", settings, day, daypart="day",
                                                                     free_stage=True)
    settings["dateHeroOverlays"] = "false"
    plugin.generate_image(settings, Device())
    assert captured["scene"] is None


def test_scene_time_keeps_the_hour_on_the_selected_date():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    day = date(2026, 10, 22)
    other_day = TZ.localize(datetime(2026, 10, 30, 21, 15)).isoformat()

    moment = plugin._scene_time({SimpleCalendar._SCENE_TIME_SETTING: other_day}, day, TZ)

    assert (moment.date(), moment.hour, moment.minute) == (day, 21, 15)
    assert plugin._scene_time({}, day, TZ).hour == 12
    assert plugin._scene_time({SimpleCalendar._SCENE_TIME_SETTING: "not a time"}, day, TZ).hour == 12


def test_prepared_presentation_carries_the_requested_moment(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    captured = {}
    monkeypatch.setattr(plugin, "_load_calendar_event_snapshots",
                        lambda *args, **kwargs: ([], SourceProvenance.LIVE))
    monkeypatch.setattr(plugin, "generate_image",
                        lambda settings, device: captured.update(settings) or Image.new("RGB", (800, 480)))

    plugin.prepare_presentation(
        {}, Device(),
        request=PresentationRequestContext(
            request_id="e" * 32, requested_at="2026-10-22T16:45:00+00:00",
            origin_display_commit_id="calendar-origin", last_receipt=None,
        ),
        resolved_theme_context=None,
    )

    assert captured[SimpleCalendar._SCENE_TIME_SETTING] == "2026-10-22T09:45:00-07:00"
