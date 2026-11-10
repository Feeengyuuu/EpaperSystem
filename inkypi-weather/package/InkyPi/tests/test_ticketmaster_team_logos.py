import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.ticketmaster_events import team_logos  # noqa: E402
from plugins.ticketmaster_events.team_logos import read_title, team_logo  # noqa: E402
from plugins.ticketmaster_events.ticketmaster_events import TicketmasterEvent, TicketmasterEvents  # noqa: E402
from utils.theme_utils import contrast_ratio, relative_luminance  # noqa: E402

NOW = datetime(2026, 11, 2, 8, 26, tzinfo=ZoneInfo("America/Los_Angeles"))


def game(title, genre="Basketball", subgenre="", segment="Sports", **fields):
    fields = {"venue_name": "Chase Center", "city": "San Francisco", "distance": "30.5 mi", **fields}
    return TicketmasterEvent(rank=1, title=title, event_id="e1", local_date="2026-11-02", local_time="19:00:00",
                             segment=segment, genre=genre, status="onsale", extra={"subgenre": subgenre}, **fields)


def keys(teams):
    return [side.team.key if side.team else None for side in teams.sides]


def test_a_game_names_both_teams_by_the_names_ticketmaster_writes():
    teams = read_title("Golden State Warriors vs. Phoenix Suns", "Sports", "Basketball", "NBA")
    assert keys(teams) == ["nba/golden-state-warriors", "nba/phoenix-suns"]
    assert [side.text for side in teams.sides] == ["Golden State Warriors", "Phoenix Suns"]
    assert teams.joiner == "vs." and not teams.prefix and not teams.sport
    # Accents and the word between the teams are read as Ticketmaster writes them.
    away = read_title("Montréal Canadiens at San Jose Sharks", "Sports", "Hockey", "NHL")
    assert keys(away) == ["nhl/montreal-canadiens", "nhl/san-jose-sharks"] and away.joiner == "at"
    preseason = read_title("Preseason: Golden State Warriors vs. Los Angeles Lakers", "Sports", "Basketball", "NBA")
    assert preseason.prefix == "Preseason" and preseason.sides[0].text == "Golden State Warriors"


def test_a_college_game_says_its_sport_once_and_knows_a_school_by_its_short_name():
    teams = read_title("California Golden Bears Mens Basketball vs. Cal State Bakersfield Roadrunners Mens Basketball",
                       "Sports", "Basketball", "College")
    assert keys(teams) == ["ncaa/california-golden-bears", "ncaa/cal-state-bakersfield-roadrunners"]
    assert teams.sport == "男篮" and not teams.sides[0].rest and not teams.trailing
    volleyball = read_title("Stanford Cardinal Womens Volleyball vs. Cal", "Sports", "Volleyball", "")
    assert keys(volleyball) == ["ncaa/stanford-cardinal", "ncaa/california-golden-bears"]
    assert volleyball.sport == "女排" and volleyball.sides[1].text == "Cal"
    # A Division II opponent has no logo, but its sport still comes off its name.
    division_two = read_title("California Golden Bears Mens Basketball vs. Adams State Grizzlies Mens Basketball",
                              "Sports", "Basketball", "College")
    assert keys(division_two) == ["ncaa/california-golden-bears", None] and division_two.sport == "男篮"
    assert division_two.sides[1].text == "Adams State Grizzlies" and not division_two.trailing
    by_place = read_title("Stanford Cardinal Mens Basketball vs. Oregon State", "Sports", "Basketball", "College")
    assert by_place.sides[1].team.key == "ncaa/oregon-state-beavers"
    assert by_place.sides[0].team.place == "Stanford"


def test_a_show_leads_its_title_and_other_titles_name_no_team():
    show = read_title("Harlem Globetrotters 2026 World Tour", "Sports", "Basketball", "")
    assert not show.is_game and keys(show) == ["show/harlem-globetrotters"] and show.sides[0].rest == "2026 World Tour"
    assert read_title("Psychedelic Furs", "Music", "Alternative", "") is None
    # A concert's 'vs.' is not a game, and a team's words in a concert are not the team.
    assert read_title("Live at the Fillmore vs. Nobody", "Music", "Rock", "") is None
    assert read_title("Golden State Warriors Tribute Band", "Music", "Rock", "") is None
    # An opponent without a logo keeps its name.
    reign = read_title("San Jose Barracuda vs. Ontario Reign", "Sports", "Hockey", "AHL")
    assert keys(reign) == ["ahl/san-jose-barracuda", None] and reign.sides[1].text == "Ontario Reign"


def test_every_logo_has_its_source_and_names():
    record = json.loads((team_logos.TEAM_DIR / "teams.source.json").read_text(encoding="utf-8"))["teams"]
    files = {path.relative_to(team_logos.TEAM_DIR).as_posix()[:-4] for path in team_logos.TEAM_DIR.glob("*/*.png")}
    expected = set(record) | {f"{key}-dark" for key, entry in record.items() if entry.get("dark")}
    assert files == expected
    assert all(entry["names"] and entry["short"] and entry["source"] for entry in record.values())
    with Image.open(team_logos.TEAM_DIR / "nba" / "golden-state-warriors.png") as logo:
        assert max(logo.size) == 48


def test_a_dark_logo_takes_the_night_ink_and_keeps_its_brand_colours_by_day():
    team = read_title("Stanford Cardinal Womens Volleyball vs. Cal", "Sports", "Volleyball", "").sides[1].team
    night_paper, night_ink = (16, 20, 26), (236, 238, 240)
    night = team_logo(team, (36, 24), True, night_paper, night_ink)
    visible = [pixel for pixel in night.get_flattened_data() if pixel[3] >= 8]
    assert visible and not any(relative_luminance(pixel[:3]) < 0.18 and contrast_ratio(pixel[:3], night_paper) < 2.0
                               for pixel in visible)
    # By day Cal's navy stays as printed.
    day = team_logo(team, (36, 24), False, (232, 238, 235), (18, 26, 32))
    assert any(blue > red + 30 for red, _green, blue, alpha in day.get_flattened_data() if alpha > 200)


def record_text(draw):
    calls = []
    original = draw.text

    def text(position, value, **kwargs):
        calls.append((value, draw.textbbox(position, value, font=kwargs["font"])))
        original(position, value, **kwargs)

    draw.text = text
    return calls


@pytest.mark.parametrize("mode", ["day", "night"])
def test_a_list_row_puts_each_teams_logo_before_its_name_and_shortens_names_before_cutting(mode):
    from tests.test_ticketmaster_events import _canonical_theme

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({"_inkypi_theme": _canonical_theme(mode)})
    image = Image.new("RGB", (800, 480), colors["paper"])
    draw = ImageDraw.Draw(image)
    calls = record_text(draw)
    sharks = game("San Jose Sharks vs. Calgary Flames", genre="Hockey", subgenre="NHL")
    plugin._draw_slip(image, draw, sharks, (332, 92, 784, 138), "今天", colors, NOW)
    texts = [value for value, _box in calls]
    assert "San Jose Sharks" in texts and "Calgary Flames" in texts and " vs. " in texts
    home, away = (next(box for value, box in calls if value == name) for name in ("San Jose Sharks", "Calgary Flames"))
    # Each name sits after a logo: the gap before it is not the paper.
    for box in (home, away):
        gap = image.crop((box[0] - 22, 95, box[0] - 4, 112))
        assert any(contrast_ratio(pixel, colors["paper"]) > 1.5 for pixel in gap.get_flattened_data())
    assert all(331 <= box[0] <= box[2] <= 784 and 92 <= box[1] <= box[3] <= 138
               for value, box in calls if value != "今天")

    calls.clear()
    college = game("California Golden Bears Mens Basketball vs. Cal State Bakersfield Roadrunners Mens Basketball",
                   subgenre="College", venue_name="Haas Pavilion", city="Berkeley")
    plugin._draw_slip(image, draw, college, (332, 140, 784, 186), "", colors, NOW)
    texts = [value for value, _box in calls]
    # Too long whole: the schools by their places, never cut, the sport under the time.
    assert "California" in texts and "Cal State Bakersfield" in texts and not any("..." in text for text in texts)
    assert "男篮" in texts


def test_the_featured_game_gives_each_team_a_line_after_its_logo():
    from tests.test_ticketmaster_events import _canonical_theme

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({"_inkypi_theme": _canonical_theme("day")})
    image = Image.new("RGB", (800, 480), colors["paper"])
    draw = ImageDraw.Draw(image)
    calls = record_text(draw)
    plugin._draw_featured(image, draw, game("Golden State Warriors vs. Phoenix Suns", subgenre="NBA"), NOW.date(), colors)
    first = next(box for value, box in calls if value == "Golden State Warriors")
    second = next(box for value, box in calls if value == "Phoenix Suns")
    versus = next(box for value, box in calls if value == " vs.")
    assert second[1] > first[3] - 4 and abs(first[0] - second[0]) <= 1
    # 'vs.' ends the first line, on its baseline.
    assert abs(versus[3] - first[3]) <= 1 and versus[0] >= first[2] - 1
    # The place keeps the venue's whole name rather than cutting the city.
    assert any(value == "Chase Center" for value, _box in calls)
