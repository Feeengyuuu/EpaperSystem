"""The UFC, tennis and MotoGP lower panels: drawn by day and night, chosen by the ranking, left out
when their feeds fail; and the golf board's places and countries as medal and flag icons."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.sports_dashboard import motogp, tennis, ufc  # noqa: E402
from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS  # noqa: E402
from plugins.sports_dashboard.sports_dashboard import SportsDashboard  # noqa: E402
from tests import test_sports_extra_feeds as feeds  # noqa: E402

LA = timezone(timedelta(hours=-8))
BOX = (0, 0, 555, 267)


def _cards():
    fight_night = datetime(2026, 11, 14, 22, 30, tzinfo=timezone.utc)
    ufc_card = ufc.build_card(ufc.parse_scoreboard(feeds.UFC)["events"], fight_night)
    tennis_card = tennis.build_card({"ATP": tennis.parse_scoreboard(feeds.ATP, "ATP")}, feeds.NOW)
    moto_data = {"current": motogp.pick_events(feeds.EVENTS, feeds.NOW)["current"],
                 "sessions": motogp.normalize_sessions(feeds.SESSIONS),
                 "result": {"session": "RAC", "event": "MAL", "event_zh": "马来西亚大奖赛",
                            "rows": motogp.normalize_classification(feeds.CLASSIFICATION)},
                 "standings": motogp.normalize_standings(feeds.STANDINGS)}
    return {"UFC": (ufc_card, fight_night), "TENNIS": (tennis_card, feeds.NOW),
            "MOTOGP": (motogp.build_card(moto_data, feeds.NOW), feeds.NOW)}


def _render(plugin, drawer, card, now, colours, monkeypatch, drawn):
    original = plugin._draw_text_in_box
    monkeypatch.setattr(plugin, "_draw_text_in_box", lambda draw, box, text, *args, **kwargs: drawn.append(text) or
                        original(draw, box, text, *args, **kwargs))
    centred = plugin._draw_centered_in_box
    monkeypatch.setattr(plugin, "_draw_centered_in_box", lambda draw, box, text, *args, **kwargs: drawn.append(text) or
                        centred(draw, box, text, *args, **kwargs))
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda url, size: Image.new("RGBA", (size, size // 2), (90, 60, 40, 255)))
    token = _ACTIVE_COLORS.set(colours)
    try:
        image = Image.new("RGB", (556, 268), colours["paper"])
        getattr(plugin, drawer)(image, ImageDraw.Draw(image), BOX, card, "ESPN LIVE", now.astimezone(LA))
    finally:
        _ACTIVE_COLORS.reset(token)
    return image


@pytest.mark.parametrize("colours", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
@pytest.mark.parametrize("key, drawer, expected", [
    ("UFC", "_draw_ufc_panel", {"UFC 334", "Gane vs. Hokit", "Ciryl Gane", "13-3-0"}),
    ("TENNIS", "_draw_tennis_panel", {"巴黎大师赛", "1/4决赛", "Carlos Alcaraz", "进行中"}),
    ("MOTOGP", "_draw_motogp_panel", {"卡塔尔大奖赛", "冲刺赛", "正赛成绩 · 马来西亚大奖赛", "车手积分榜", "399",
                                       "MARQUEZ", "ACOSTA", "+0.865"}),
])
def test_each_extra_panel_draws_its_card_by_day_and_night(monkeypatch, key, drawer, expected, colours):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    card, now = _cards()[key]
    drawn = []
    image = _render(plugin, drawer, card, now, colours, monkeypatch, drawn)

    assert expected <= set(drawn), set(drawn)
    assert image.getbbox() is not None
    # Flags are drawn as icons (the stand-in blue), never as their country codes.
    assert (0, 80, 200) in set(image.convert("RGB").get_flattened_data())


def test_the_ranking_hands_the_panel_to_a_numbered_ufc_card_on_fight_night():
    card, now = _cards()["UFC"]
    extra = ufc.candidate(card, now)
    nba = {"live": [], "upcoming": [{"start": now + timedelta(hours=3), "state": "scheduled"}], "recent": []}

    choice = SportsDashboard._choose_lower_panel({}, nba, None, "", None, now, extras=[extra])
    assert (choice["kind"], choice["key"]) == ("extra", "UFC")
    # Switched off, the extra leagues stay out of the ranking.
    plugin = SportsDashboard({"id": "sports_dashboard"})
    assert plugin._load_lower_extras({"extraSportsPanels": "off"}, now) == ([], {})


def test_a_league_whose_feed_fails_is_left_out(tmp_path, monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_sports_dashboard_cache_dir", lambda: tmp_path)

    candidates, states = plugin._load_lower_extras({}, feeds.NOW)

    assert candidates == []
    assert states == {"UFC": "ESPN UFC NO DATA", "TENNIS": "ESPN TENNIS NO DATA", "MOTOGP": "MOTOGP NO DATA"}


def test_the_golf_board_shows_places_as_medals_and_countries_as_flags(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    medals, flags = [], []
    original_medal, original_flag = plugin._draw_rank_medal, plugin._draw_flag_icon
    monkeypatch.setattr(plugin, "_draw_rank_medal", lambda draw, box, number, tied=False: medals.append((number, tied)) or
                        original_medal(draw, box, number, tied))
    monkeypatch.setattr(plugin, "_draw_flag_icon", lambda image, draw, url, box, fallback="": flags.append((url, fallback)) or
                        original_flag(image, draw, url, box, fallback))
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    rows = [{"position": 1, "position_label": "P1", "name": "A. Smotherman", "country": "USA", "score": "-16",
             "flag": "https://a.espncdn.com/i/teamlogos/countries/500/usa.png", "rounds": ["-6", "-5"]},
            {"position": 3, "position_label": "T3", "name": "T. Moore", "country": "CAN", "score": "-14", "rounds": ["-6"]}]
    from plugins.sports_dashboard.scorebug_kit import ScorebugSpec

    image = Image.new("RGB", (556, 200), DAY_COLORS["paper"])
    plugin._draw_pga_board(image, ImageDraw.Draw(image), (10, 10, 540, 190), rows, "final",
                           ScorebugSpec("PGA", "pga_accent", "pga_live", "pga_tag", "pga_course_tint"))

    assert medals == [(1, False), (3, True)]
    assert flags == [("https://a.espncdn.com/i/teamlogos/countries/500/usa.png", "USA"),
                     ("https://a.espncdn.com/i/teamlogos/countries/500/can.png", "CAN")]


def test_tournaments_are_named_by_their_official_marks():
    # A tour-level event by its own logo; one without a bundled logo by the tour's tier stamp.
    paris = {"tour": "ATP", "name": "Rolex Paris Masters"}
    assert (tennis.tournament_category(paris), tennis.tournament_mark(paris)) == ("1000", "tennis_event_paris")
    assert tennis.tournament_mark({"tour": "ATP", "name": "Swiss Indoors Basel"}) == "tennis_event_basel"
    assert tennis.tournament_mark({"tour": "ATP", "name": "Moselle Open"}) == "tennis_atp_250"
    assert tennis.tournament_mark({"tour": "WTA", "name": "Australian Open"}) == "tennis_australian_open"
    # A WTA event with a brand of its own carries the WTA's tier tag; Wimbledon its crest, the
    # Finals their own logos.
    hong_kong = {"tour": "WTA", "name": "Prudential Hong Kong Tennis Open"}
    assert (tennis.tournament_category(hong_kong), tennis.tournament_mark(hong_kong)) == ("250", "tennis_wta_250")
    assert tennis.tournament_mark({"tour": "WTA", "name": "Wuhan Open"}) == "tennis_wta_1000"
    assert tennis.tournament_mark({"tour": "WTA", "name": "Wimbledon"}) == "tennis_wimbledon"
    assert tennis.tournament_mark({"tour": "ATP", "name": "Nitto ATP Finals"}) == "tennis_event_nitto_atp_finals"
    assert tennis.tournament_mark({"tour": "WTA", "name": "WTA Finals Riyadh"}) is None
    # The place by its own logo: the host city's where ESPN names only the city, else the venue's.
    assert tennis.place_mark({"city": "Paris"}) == "tennis_place_paris"
    assert tennis.place_mark({"city": "Indian Wells Tennis Garden"}) == "tennis_place_indian_wells"
    assert tennis.place_mark({"city": "Turin"}) == tennis.place_mark({"city": "Inalpi Arena"}) == "tennis_place_inalpi_arena"
    assert tennis.place_mark({"city": "Stockholm"}) is None
    from plugins.sports_dashboard.common import LOCAL_TEAM_LOGO_DIR

    for key in ("tennis_atp_1000", "tennis_atp_500", "tennis_atp_250", "tennis_australian_open", "tennis_roland_garros",
                "tennis_us_open", "tennis_wimbledon", "tennis_atp_finals", "tennis_wta_1000", "tennis_wta_500",
                "tennis_wta_250", "motogp_ducati", "motogp_ktm", "motogp_aprilia", "motogp_honda", "motogp_yamaha",
                "tennis_place_paris", "tennis_place_indian_wells", "tennis_place_inalpi_arena"):
        assert (Path(LOCAL_TEAM_LOGO_DIR) / f"{key}.png").is_file(), key


def test_each_tour_level_event_finds_its_own_mark_and_venue():
    from plugins.sports_dashboard import tennis_events

    def event(tour, name, place=""):
        return tennis_events.event_for({"tour": tour, "name": name, "place": place})

    stockholm = event("ATP", "Bybit Stockholm Open", "Stockholm, Sweden")
    assert (stockholm["mark"], stockholm["venue"]) == ("tennis_event_stockholm", "Kungliga tennishallen")
    # The WTA Finals at Indian Wells by their own logo, before Indian Wells' own row matches the place.
    finals = event("WTA", "FAGE WTA Finals Indian Wells", "Indian Wells Tennis Garden, Indian Wells, USA")
    assert finals["mark"] == "tennis_event_wta_finals"
    assert event("WTA", "WTA Finals Riyadh", "Riyadh, Saudi Arabia") is None
    assert event("ATP", "Next Gen ATP Finals")["mark"] == "tennis_event_next_gen_atp_finals"
    # A combined event's WTA draw shares the ATP logo; a same-city event with its own brand does not.
    assert event("WTA", "Mutua Madrid Open", "Madrid, Spain")["mark"] == "tennis_event_madrid"
    assert event("ATP", "Qatar ExxonMobil Open", "Doha, Qatar")["mark"] == "tennis_event_doha"
    doha = event("WTA", "Qatar TotalEnergies Open", "Doha, Qatar")
    assert (doha["mark"], doha["venue"]) == (None, "Khalifa Tennis Complex")
    assert tennis.tournament_mark({"tour": "WTA", "name": "Qatar TotalEnergies Open"}) == "tennis_wta_1000"
    # A Grand Slam keeps its crest and names its grounds; an unknown event finds nothing.
    assert event("ATP", "Australian Open", "Melbourne, Australia")["venue"] == "Melbourne Park"
    assert tennis.tournament_mark({"tour": "ATP", "name": "Australian Open"}) == "tennis_australian_open"
    assert event("ATP", "Moselle Open", "Metz, France") is None


def test_every_event_mark_is_bundled_with_its_source():
    import hashlib
    import json

    from plugins.sports_dashboard import tennis_events
    from plugins.sports_dashboard.common import LOCAL_TEAM_LOGO_DIR

    logos = Path(LOCAL_TEAM_LOGO_DIR)
    records = json.loads((logos / "logos.source.json").read_text(encoding="utf-8"))["logos"]
    keys = tennis_events.mark_keys()
    boxed = {f"tennis_event_{key}" for key, _tours, _pattern, _venue, ink in tennis_events.EVENTS if ink == "boxed"}
    assert len(keys) >= 55 and len(set(keys)) == len(keys)
    for key in keys:
        path = logos / f"{key}.png"
        with Image.open(path) as mark:
            assert mark.mode == "RGBA" and mark.height == 64, key
            # Each mark stands on transparency, but for one printed on its own box.
            assert key in boxed or mark.getchannel("A").getextrema()[0] == 0, key
        record = records[key]
        assert record["source"].startswith("https://") and record["page"].startswith("https://"), key
        assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest(), key


def test_the_tennis_header_shows_the_events_mark_and_its_venue(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    tournament = {"tour": "ATP", "name": "Bybit Stockholm Open", "city": "Stockholm", "place": "Stockholm, Sweden",
                  "name_zh": ""}
    pasted = []
    original = plugin._load_local_logo
    monkeypatch.setattr(plugin, "_load_local_logo", lambda path, *args, **kwargs: pasted.append(Path(path).stem)
                        or original(path, *args, **kwargs))
    drawn = []
    image = _render(plugin, "_draw_tennis_panel", {"sport": "TENNIS", "status": "NEXT",
                                                   "tours": {"ATP": {"tournament": tournament, "shown": []}}},
                    feeds.NOW, DAY_COLORS, monkeypatch, drawn)

    assert "tennis_event_stockholm" in pasted and "tennis_atp_250" not in pasted
    # The venue by its own logo (the Royal Tennis Hall's crest), not in words.
    assert "tennis_place_kungliga_tennishallen" in pasted
    assert "Bybit Stockholm Open" in drawn and "Kungliga tennishallen" not in drawn and "Stockholm" not in drawn
    assert image.getbbox() is not None


def test_venues_with_their_own_logo_show_it_and_the_rest_are_named():
    import hashlib
    import json

    from plugins.sports_dashboard import tennis_events
    from plugins.sports_dashboard.common import LOCAL_TEAM_LOGO_DIR

    def place(tour, name, where):
        return tennis.place_mark({"tour": tour, "name": name, "place": where, "city": where.split(",")[0]})

    assert place("ATP", "Bybit Stockholm Open", "Stockholm, Sweden") == "tennis_place_kungliga_tennishallen"
    assert place("ATP", "ABN AMRO Open", "Rotterdam, Netherlands") == "tennis_place_rotterdam_ahoy"
    assert place("ATP", "Erste Bank Open", "Vienna, Austria") == "tennis_place_wiener_stadthalle"
    assert place("WTA", "Miami Open presented by Itau", "Miami, USA") == "tennis_place_hard_rock_stadium"
    # The city's or venue's logo matched in ESPN's line still stands; a venue without a logo has none.
    assert place("ATP", "Rolex Paris Masters", "Paris, France") == "tennis_place_paris"
    assert place("ATP", "Swiss Indoors Basel", "Basel, Switzerland") is None
    logos = Path(LOCAL_TEAM_LOGO_DIR)
    records = json.loads((logos / "logos.source.json").read_text(encoding="utf-8"))["logos"]
    for key in tennis_events.venue_mark_keys():
        path = logos / f"{key}.png"
        with Image.open(path) as mark:
            assert mark.mode == "RGBA" and mark.height == 64, key
        assert records[key]["source"].startswith("https://") and records[key]["page"].startswith("https://"), key
        assert records[key]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest(), key


def test_a_seeded_player_carries_a_tennis_ball_with_the_seed_on_it():
    from plugins.sports_dashboard.tennis_render import SEED_BALL, SEED_INK

    plugin = SportsDashboard({"id": "sports_dashboard"})
    icon = plugin._tennis_seed_icon(5, 16)
    assert icon.size == (16, 16) and icon.getpixel((0, 0))[3] == 0
    pixels = [icon.getpixel((x, y)) for x in range(16) for y in range(16)]
    # The optic-yellow felt and the dark number are both there, the number in the middle.
    assert any(abs(r - SEED_BALL[0]) < 30 and abs(g - SEED_BALL[1]) < 30 and b < 120 and a > 200 for r, g, b, a in pixels)
    dark = [(x, y) for x in range(16) for y in range(16) if sum(icon.getpixel((x, y))[:3]) < 200 and icon.getpixel((x, y))[3] > 200]
    assert dark and 5 <= sum(x for x, _ in dark) / len(dark) <= 10 and 5 <= sum(y for _, y in dark) / len(dark) <= 10
    assert plugin._tennis_seed_icon(5, 16) is icon and plugin._tennis_seed_icon(12, 16) is not icon
    assert SEED_INK[0] < 60


def test_the_live_dot_leads_its_label(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    match = {"state": "in", "round_zh": "小组赛", "date": "2026-11-10T19:00Z", "detail": "",
             "players": [{"name": "A", "seed": 2, "sets": [[4, None]]}, {"name": "B", "seed": 7, "sets": [[4, None]]}]}
    token = _ACTIVE_COLORS.set(DAY_COLORS)
    try:
        image = Image.new("RGB", (270, 64), DAY_COLORS["paper"])
        draw = ImageDraw.Draw(image)
        plugin._draw_tennis_match(image, draw, (2, 2, 266, 60), match, feeds.NOW.astimezone(LA), DAY_COLORS["blue"])
        label_left = 266 - 6 - plugin._text_width(draw, "进行中", plugin._font(9, True))
    finally:
        _ACTIVE_COLORS.reset(token)
    reds = [x for x in range(150, 266) for y in range(7, 14) if image.getpixel((x, y)) == DAY_COLORS["red"]]
    dot_right = max(x for x in reds if x < label_left)
    assert label_left - dot_right <= 4


def test_each_rider_carries_his_manufacturers_mark():
    from plugins.sports_dashboard.motogp_render import maker_mark

    assert [maker_mark(name) for name in ("Ducati", "KTM", "Aprilia", "Honda", "Yamaha", "Unknown")] == [
        "motogp_ducati", "motogp_ktm", "motogp_aprilia", "motogp_honda", "motogp_yamaha", None]


def test_at_night_only_dark_neutral_strokes_take_the_light_ink():
    from plugins.sports_dashboard.icon_marks import night_inked

    mark = Image.new("RGBA", (3, 1))
    mark.putpixel((0, 0), (10, 10, 10, 255))     # black lettering
    mark.putpixel((1, 0), (230, 0, 18, 255))     # a brand red (Aprilia)
    mark.putpixel((2, 0), (20, 30, 90, 255))     # ATP navy
    token = _ACTIVE_COLORS.set(DEEP_NIGHT_COLORS)
    try:
        inked = night_inked(mark)
    finally:
        _ACTIVE_COLORS.reset(token)
    light = (*DEEP_NIGHT_COLORS["text"], 255)
    assert inked.getpixel((0, 0)) == light and inked.getpixel((2, 0)) == light
    assert inked.getpixel((1, 0)) == (230, 0, 18, 255)
    assert night_inked(mark) is mark  # by day the mark is drawn as printed


def test_an_upcoming_tennis_match_shows_both_players_headshots(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    asked = []
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda url, size: asked.append(url) or
                        Image.new("RGBA", (size, size * 3 // 4), (90, 60, 40, 255)))
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    event = tennis.parse_scoreboard(feeds.ATP, "ATP")["tournaments"][0]
    upcoming = next(match for match in event["matches"] if match["state"] == "pre")
    played = next(match for match in event["matches"] if match["state"] == "post")
    image = Image.new("RGB", (270, 70), DAY_COLORS["paper"])

    plugin._draw_tennis_match(image, ImageDraw.Draw(image), (0, 0, 260, 58), upcoming, feeds.NOW, DAY_COLORS["blue"])
    assert asked == [player["headshot"] for player in upcoming["players"]]
    assert asked[0] == "https://a.espncdn.com/i/headshots/tennis/players/full/7.png"
    # The pictures stand at the block's right edge, where a played match keeps its set scores.
    assert (90, 60, 40) in set(image.crop((170, 16, 258, 58)).get_flattened_data())
    asked.clear()
    image = Image.new("RGB", (270, 70), DAY_COLORS["paper"])
    plugin._draw_tennis_match(image, ImageDraw.Draw(image), (0, 0, 260, 58), played, feeds.NOW, DAY_COLORS["blue"])
    # A played match shows each face in its player's row, after the seed; the set scores keep the right.
    assert asked == [player["headshot"] for player in played["players"]]
    assert (90, 60, 40) in set(image.crop((20, 16, 50, 58)).get_flattened_data())
    assert (90, 60, 40) not in set(image.crop((170, 16, 258, 58)).get_flattened_data())


def test_a_player_espn_has_no_photo_of_shows_the_tours_own(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    asked = []

    def load(url, size):
        asked.append(url)
        return None if "espncdn" in url else Image.new("RGBA", (size, size), (200, 40, 160, 255))

    monkeypatch.setattr(plugin, "_load_team_logo_for_render", load)
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    event = tennis.parse_scoreboard(feeds.ATP, "ATP")["tournaments"][0]
    upcoming = next(match for match in event["matches"] if match["state"] == "pre")
    upcoming["players"][0]["photo"] = "https://photoresources.wtatennis.com/photo-resources/x/Eala-Torso_330332.png?width=480"
    image = Image.new("RGB", (270, 70), DAY_COLORS["paper"])

    plugin._draw_tennis_match(image, ImageDraw.Draw(image), (0, 0, 260, 58), upcoming, feeds.NOW, DAY_COLORS["blue"])

    assert asked[:2] == [upcoming["players"][0]["headshot"], upcoming["players"][0]["photo"]]
    assert (200, 40, 160) in set(image.crop((170, 16, 258, 58)).get_flattened_data())


def test_a_tournaments_place_is_its_logo_and_every_shown_player_gets_a_photo(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    card, now = _cards()["TENNIS"]
    asked = []
    monkeypatch.setattr(tennis.tennis_photos, "official_photos",
                        lambda plugin, settings, players, now: asked.extend(players) or {})
    tennis.attach_photos(plugin, {}, card, now)
    states = {match["state"] for entry in card["tours"].values() for match in entry["shown"]}
    assert {"post", "in"} & states
    assert len(asked) == sum(len(match["players"]) for entry in card["tours"].values() for match in entry["shown"])
    drawn = []
    image = _render(plugin, "_draw_tennis_panel", card, now, DAY_COLORS, monkeypatch, drawn)
    # Paris by the city's own logo (its navy ship and letters), no longer its name in words.
    assert "Paris" not in drawn and "巴黎大师赛" in drawn
    assert image.getbbox() is not None


def test_a_wikidata_photo_is_cut_to_the_head_and_shown_round(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    photo = Image.new("RGBA", (120, 180), (30, 60, 200, 255))
    ImageDraw.Draw(photo).rectangle((40, 0, 80, 60), fill=(220, 170, 140, 255))  # the head, top middle
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: photo.copy())

    face = plugin._tennis_face("https://upload.wikimedia.org/wikipedia/commons/thumb/d/da/P.jpg/250px-P.jpg", 20)

    assert face.size == (18, 18)
    assert face.getpixel((0, 0))[3] == 0 and face.getpixel((9, 9))[3] == 255
    assert face.getpixel((9, 6))[:3] == (220, 170, 140)


def test_a_photo_that_failed_to_load_is_asked_for_again_later(monkeypatch, tmp_path):
    """Commons answers 429 at times: the face comes back on a later render, not after a restart."""
    import io

    from plugins.sports_dashboard import icon_marks

    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_team_logo_disk_cache_dir", lambda: None)
    clock = [1000.0]
    monkeypatch.setattr(icon_marks.time, "monotonic", lambda: clock[0])
    buffer = io.BytesIO()
    Image.new("RGB", (40, 60), (30, 60, 200)).save(buffer, format="PNG")
    answers = [RuntimeError("429 Too Many Requests"), buffer.getvalue()]
    asked = []

    def fetch(url, timeout):
        asked.append(url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(SportsDashboard, "_fetch_remote_image_bytes", staticmethod(fetch))
    url = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Retry.jpg/250px-Retry.jpg"
    assert plugin._load_icon_flag(url, (60, 60)) is None
    clock[0] += 60
    assert plugin._load_icon_flag(url, (60, 60)) is None and len(asked) == 1
    clock[0] += icon_marks.FLAG_RETRY_SECONDS
    assert plugin._load_icon_flag(url, (60, 60)) is not None and len(asked) == 2
