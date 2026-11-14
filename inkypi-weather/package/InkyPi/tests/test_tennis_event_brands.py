"""Tournament identities, edition boundaries and venue attribution from real event names."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from plugins.sports_dashboard import tennis, tennis_events, tennis_places
from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS, LOCAL_TEAM_LOGO_DIR
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


# Independent examples: do not generate expected identities from the production mapping.
WTA_BRANDS = [
    ("Hobart International", "wta_hobart"),
    ("Mubadala Abu Dhabi Open", "wta_abu_dhabi"),
    ("Qatar TotalEnergies Open", "wta_doha"),
    ("Credit One Charleston Open", "wta_charleston"),
    ("Upper Austria Ladies Linz", "wta_linz"),
    ("Porsche Tennis Grand Prix", "wta_stuttgart"),
    ("Open Capfinances Rouen Metropole", "wta_rouen"),
    ("Internationaux de Strasbourg presented by Mammotion", "wta_strasbourg"),
    ("Lexus Nottingham Open", "wta_nottingham"),
    ("Vanda Pharmaceuticals Berlin Tennis Open", "wta_berlin"),
    ("Hamburg European Open", "wta_hamburg"),
    ("National Bank Open presented by Rogers", "wta_toronto"),
    ("Abierto GNP Seguros", "wta_monterrey"),
    ("Guadalajara Open Akron presented by Santander", "wta_guadalajara"),
    ("Hana Bank Korea Open", "wta_seoul"),
    ("Dongfeng Voyah Wuhan Open", "wta_wuhan"),
    ("Kinoshita Group Japan Open Tennis Championships", "wta_osaka"),
    ("Toray Pan Pacific Open", "wta_tokyo"),
    ("Prudential Hong Kong Tennis Open", "wta_hong_kong"),
    ("Chennai Open", "wta_chennai"),
]


@pytest.mark.parametrize("name,key", WTA_BRANDS, ids=[key for _, key in WTA_BRANDS])
def test_official_wta_event_names_resolve_to_their_own_brand(name, key):
    tournament = {"tour": "WTA", "name": name, "start": "2026-02-08T00:00:00Z"}
    assert tennis_events.event_for(tournament)["key"] == key
    assert tennis.tournament_mark(tournament) == f"tennis_event_{key}"


@pytest.mark.parametrize("atp_name,wta_name,atp_mark,wta_mark", [
    ("Bank of China Hong Kong Tennis Open", "Prudential Hong Kong Tennis Open", "hong_kong", "wta_hong_kong"),
    ("Qatar ExxonMobil Open", "Qatar TotalEnergies Open", "doha", "wta_doha"),
    ("Hamburg Open", "Hamburg European Open", "hamburg", "wta_hamburg"),
    ("Kinoshita Group Japan Open Tennis Championships", "Toray Pan Pacific Open", "tokyo", "wta_tokyo"),
])
def test_same_city_atp_and_wta_events_do_not_share_the_wrong_sponsor(atp_name, wta_name, atp_mark, wta_mark):
    atp = {"tour": "ATP", "name": atp_name, "start": "2026-02-08T00:00:00Z"}
    wta = {"tour": "WTA", "name": wta_name, "start": "2026-02-08T00:00:00Z"}
    assert tennis.tournament_mark(atp) == f"tennis_event_{atp_mark}"
    assert tennis.tournament_mark(wta) == f"tennis_event_{wta_mark}"
    assert tennis.tournament_mark(atp) != tennis.tournament_mark(wta)


@pytest.mark.parametrize("edition,expected", [
    ({"start": "2026-02-08T00:00:00Z"}, "tennis_event_wta_doha"),
    ({"start": "2026-02-08"}, "tennis_event_wta_doha"),
    ({"year": 2026}, "tennis_event_wta_doha"),
    ({"year": "2026"}, "tennis_event_wta_doha"),
    ({"start": "2027-02-08T00:00:00Z"}, "tennis_wta_1000"),
    ({"start": "2025-02-08T00:00:00Z"}, "tennis_wta_1000"),
    ({"year": 2027}, "tennis_wta_1000"),
    ({}, "tennis_wta_1000"),
    ({"year": "2026 edition"}, "tennis_wta_1000"),
    ({"start": "unconfirmed"}, "tennis_wta_1000"),
    ({"start": "2027-02-08T00:00:00Z", "year": 2026}, "tennis_wta_1000"),
    ({"start": "2026-02-08T00:00:00Z", "year": 2027}, "tennis_event_wta_doha"),
])
def test_the_dated_doha_logo_is_used_only_for_a_known_2026_edition(edition, expected):
    tournament = {"tour": "WTA", "name": "Qatar TotalEnergies Open", **edition}
    assert tennis.tournament_mark(tournament) == expected
    assert tennis_events.event_for(tournament)["venue"] == "Khalifa Tennis Complex"


@pytest.mark.parametrize("tour,mark", [("ATP", "tennis_event_montreal"), ("WTA", "tennis_event_wta_toronto")])
@pytest.mark.parametrize("location,venue", [
    ({"city": "Toronto"}, "Sobeys Stadium"),
    ({"place": "Toronto, Canada"}, "Sobeys Stadium"),
    ({"venue": "Sobeys Stadium"}, "Sobeys Stadium"),
    ({"city": "Montreal"}, "IGA Stadium"),
    ({"place": "Montréal, Canada"}, "IGA Stadium"),
    ({"venue": "IGA Stadium"}, "IGA Stadium"),
    ({"city": "Toronto", "place": "Montreal, Canada"}, ""),
    ({"place": "Canada"}, ""),
    ({}, ""),
])
def test_canadian_event_venue_follows_explicit_host_data_not_the_tour(tour, mark, location, venue):
    tournament = {"tour": tour, "name": "National Bank Open presented by Rogers", **location}
    assert tennis.tournament_mark(tournament) == mark
    assert tennis_events.event_for(tournament)["venue"] == venue
    expected_place = {"Sobeys Stadium": "tennis_place_sobeys_stadium", "IGA Stadium": "tennis_place_iga_stadium"}.get(venue)
    assert tennis_events.event_for(tournament)["venue_mark"] == expected_place
    assert tennis.place_mark(tournament) == expected_place


@pytest.mark.parametrize("tour", ["ATP", "WTA"])
@pytest.mark.parametrize("name,location,venue", [
    ("Omnium Banque Nationale", {}, ""),
    ("National Bank Open Toronto", {}, "Sobeys Stadium"),
    ("Omnium Banque Nationale Montreal", {}, "IGA Stadium"),
    ("National Bank Open Toronto", {"city": "Montreal"}, "IGA Stadium"),
    ("Omnium Banque Nationale Montreal", {"place": "Toronto, Canada"}, "Sobeys Stadium"),
])
def test_canadian_brand_name_does_not_override_explicit_host_location(tour, name, location, venue):
    event = tennis_events.event_for({"tour": tour, "name": name, **location})
    assert event is not None
    assert event["venue"] == venue


def _render_header(monkeypatch, tournament, colours):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    image = Image.new("RGB", (261, 64), colours["panel"])
    loaded, drawn, pasted, icon_positions = [], [], [], []
    load = plugin._load_local_logo
    text = plugin._draw_text_in_box
    paste = image.paste
    court_icon = plugin._draw_tennis_venue_icon

    def record_load(path, *args, **kwargs):
        loaded.append(Path(path).stem)
        return load(path, *args, **kwargs)

    def record_text(draw, box, label, *args, **kwargs):
        drawn.append(label)
        return text(draw, box, label, *args, **kwargs)

    def record_paste(source, box=None, mask=None):
        if isinstance(source, Image.Image):
            pasted.append((source.copy(), box))
        return paste(source, box, mask)

    def record_icon(draw, x, y):
        icon_positions.append((x, y))
        return court_icon(draw, x, y)

    monkeypatch.setattr(plugin, "_load_local_logo", record_load)
    monkeypatch.setattr(plugin, "_draw_text_in_box", record_text)
    monkeypatch.setattr(image, "paste", record_paste)
    monkeypatch.setattr(plugin, "_draw_tennis_venue_icon", record_icon)
    token = _ACTIVE_COLORS.set(colours)
    try:
        plugin._draw_tennis_column(image, ImageDraw.Draw(image), (0, 0, 261, 64), tournament["tour"],
                                   {"tournament": tournament, "shown": []},
                                   datetime(2026, 11, 14, tzinfo=timezone.utc), colours["blue"])
    finally:
        _ACTIVE_COLORS.reset(token)
    icon_patches = [image.crop((int(x), int(y), int(x) + 15, int(y) + 11)) for x, y in icon_positions]
    return loaded, drawn, pasted, icon_patches


@pytest.mark.parametrize("colours", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_hong_kong_event_mark_is_twenty_pixels_high_visible_and_keeps_the_venue(monkeypatch, colours):
    tournament = {"tour": "WTA", "name": "Prudential Hong Kong Tennis Open", "city": "Hong Kong"}
    loaded, drawn, pasted, icons = _render_header(monkeypatch, tournament, colours)
    assert loaded == ["tennis_event_wta_hong_kong"]
    assert "维园网球场" in drawn
    assert "Victoria Park" not in drawn
    assert "Hong Kong" not in drawn
    mark, _box = pasted[0]
    assert mark.height == 20
    opaque = [pixel for pixel in mark.get_flattened_data() if pixel[3] > 128]
    assert opaque
    visible = sum(max(abs(pixel[channel] - colours["panel"][channel]) for channel in range(3)) > 64
                  for pixel in opaque)
    assert visible / len(opaque) > 0.75
    assert len(icons) == 1
    icon_pixels = list(icons[0].get_flattened_data())
    assert icon_pixels.count(colours["text"]) >= 40
    assert any(max(abs(pixel[channel] - colours["text"][channel]) for channel in range(3)) > 64
               for pixel in icon_pixels)


@pytest.mark.parametrize("colours", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_unregistered_event_keeps_its_tier_mark_and_supplied_place_text(monkeypatch, colours):
    tournament = {"tour": "WTA", "name": "New Invitational", "city": "Riverside Court"}
    assert tennis_events.event_for(tournament) is None
    assert tennis.tournament_mark(tournament) == "tennis_wta_250"
    loaded, drawn, _pasted, icons = _render_header(monkeypatch, tournament, colours)
    assert loaded == ["tennis_wta_250"]
    assert "Riverside Court" in drawn
    assert len(icons) == 1


@pytest.mark.parametrize("colours", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_boxed_stuttgart_mark_keeps_its_original_dark_ground_and_light_letters(monkeypatch, colours):
    loaded, _drawn, pasted, _icons = _render_header(
        monkeypatch, {"tour": "WTA", "name": "Porsche Tennis Grand Prix"}, colours)
    assert loaded[0] == "tennis_event_wta_stuttgart"
    mark, _box = pasted[0]
    # The opaque official black/white lockup must not be knocked out using its dark corner colour.
    assert mark.getchannel("A").getextrema() == (255, 255)
    pixels = list(mark.get_flattened_data())
    assert sum(max(pixel[:3]) < 80 for pixel in pixels) > len(pixels) * 0.1
    assert sum(min(pixel[:3]) > 200 for pixel in pixels) > len(pixels) * 0.1


def test_every_curated_venue_has_a_short_display_label_without_changing_feed_identity():
    venues = {row[3] for row in tennis_events.EVENTS if row[3]} | {"Sobeys Stadium", "IGA Stadium"}
    assert venues <= tennis_places.VENUE_LABELS.keys()
    assert all(tennis_places.venue_label(venue) != venue for venue in venues)
    assert tennis_events.event_for({"tour": "WTA", "name": "Prudential Hong Kong Tennis Open"})["venue"] == "Victoria Park"


@pytest.mark.parametrize("venue", [
    "Victoria Park", "VictoriaPark", "Victoria Park Tennis Stadium", "VictoriaParkTennisStadium",
    "Victoria Park Centre Court", "Victoria Park Tennis Centre Court",
    "Victoria Park Tennis Stadium / Centre Court", " VICTORIA PARK ",
])
def test_hong_kong_venue_aliases_share_the_requested_label(venue):
    assert tennis_places.venue_label(venue) == "维园网球场"


@pytest.mark.parametrize("venue", ["Centre Court", "New Sponsor Stadium", "新球场", " Unmapped Court ", ""])
def test_unfamiliar_venue_names_remain_the_providers_text(venue):
    assert tennis_places.venue_label(venue) == venue


def test_an_absent_venue_does_not_invent_a_label():
    assert tennis_places.venue_label(None) == ""


def _luminance(rgb):
    channels = [value / 255 for value in rgb[:3]]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


@pytest.mark.parametrize("name,key", [
    ("Toray Pan Pacific Open", "tennis_event_wta_tokyo"),
    ("Guadalajara Open Akron presented by Santander", "tennis_event_wta_guadalajara"),
])
def test_dark_coloured_event_letters_read_at_night_without_recolouring_day_or_cached_art(monkeypatch, name, key):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    path = str(Path(LOCAL_TEAM_LOGO_DIR) / f"{key}.png")
    original = plugin._load_local_logo(path, (66, 20), alpha_threshold=8)
    source_bytes = original.tobytes()
    tournament = {"tour": "WTA", "name": name}
    _loaded, _drawn, day, _icons = _render_header(monkeypatch, tournament, DAY_COLORS)
    assert day[0][0].tobytes() == source_bytes
    _loaded, _drawn, night, _icons = _render_header(monkeypatch, tournament, DEEP_NIGHT_COLORS)
    night_mark = night[0][0]
    assert night_mark.tobytes() != source_bytes
    background = _luminance(DEEP_NIGHT_COLORS["panel"])
    ink_pixels = [pixel for pixel in night_mark.get_flattened_data() if pixel[3] >= 220]
    assert ink_pixels
    contrasts = [(max(_luminance(pixel), background) + 0.05) / (min(_luminance(pixel), background) + 0.05)
                 for pixel in ink_pixels]
    assert sum(contrast >= 2.0 for contrast in contrasts) / len(contrasts) > 0.95
    assert original.tobytes() == source_bytes
    assert plugin._load_local_logo(path, (66, 20), alpha_threshold=8).tobytes() == source_bytes
    _loaded, _drawn, day_after_night, _icons = _render_header(monkeypatch, tournament, DAY_COLORS)
    assert day_after_night[0][0].tobytes() == source_bytes


@pytest.mark.parametrize("name,city,venue,mark", [
    ("Hobart International", "Hobart", "Domain Tennis Centre", "tennis_place_domain_tennis_centre"),
    ("Mubadala Abu Dhabi Open", "Abu Dhabi", "Zayed Sports City", "tennis_place_zayed_sports_city"),
    ("Credit One Charleston Open", "Charleston", "Credit One Stadium", "tennis_place_credit_one_stadium"),
    ("Upper Austria Ladies Linz", "Linz", "Design Center Linz", "tennis_place_design_center_linz"),
    ("Porsche Tennis Grand Prix", "Stuttgart", "Porsche Arena", "tennis_place_porsche_arena"),
    ("Open Capfinances Rouen Metropole", "Rouen", "Kindarena", "tennis_place_kindarena"),
    ("Internationaux de Strasbourg", "Strasbourg", "Tennis Club de Strasbourg", "tennis_place_tennis_club_strasbourg"),
    ("Lexus Nottingham Open", "Nottingham", "Nottingham Tennis Centre", "tennis_place_nottingham_tennis_centre"),
    ("Vanda Pharmaceuticals Berlin Tennis Open", "Berlin", "LTTC Rot-Weiß Berlin", "tennis_place_lttc_rot_weiss_berlin"),
    ("National Bank Open presented by Rogers", "Toronto", "Sobeys Stadium", "tennis_place_sobeys_stadium"),
    ("Omnium Banque Nationale", "Montréal", "IGA Stadium", "tennis_place_iga_stadium"),
    ("Abierto GNP Seguros", "Monterrey", "Club Sonoma", "tennis_place_club_sonoma"),
    ("Hana Bank Korea Open", "Seoul", "Seoul Olympic Park", "tennis_place_seoul_olympic_park"),
    ("Dongfeng Voyah Wuhan Open", "Wuhan", "Optics Valley Tennis Center", "tennis_place_optics_valley_tennis_center"),
])
def test_the_fourteen_verified_venues_are_selected_independently_of_the_event_mark(name, city, venue, mark):
    tournament = {"tour": "WTA", "name": name, "city": city}
    event = tennis_events.event_for(tournament)
    assert event["venue"] == venue
    assert event["venue_mark"] == mark
    assert tennis.place_mark(tournament) == mark
    assert mark in tennis_events.venue_mark_keys()
    assert tennis.tournament_mark(tournament) != mark


@pytest.mark.parametrize("tournament", [
    {"tour": "ATP", "name": "Toronto Challenger", "city": "Toronto"},
    {"tour": "ATP", "name": "National Bank Challenger", "place": "Drummondville, Canada"},
    {"tour": "WTA", "name": "Lexus Nottingham Open WTA 125", "city": "Nottingham"},
])
def test_lower_tier_events_do_not_borrow_a_same_city_tour_brand_or_venue(tournament):
    assert tennis_events.event_for(tournament) is None
    assert tennis.place_mark(tournament) is None
    assert not tennis.tournament_mark(tournament).startswith("tennis_event_")


@pytest.mark.parametrize("name,key", [
    ("Hobart International", "tennis_place_domain_tennis_centre"),
    ("Mubadala Abu Dhabi Open", "tennis_place_zayed_sports_city"),
])
def test_verified_venue_art_replaces_the_pictogram_and_stays_visible_without_mutating_cache(monkeypatch, name, key):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    path = str(Path(LOCAL_TEAM_LOGO_DIR) / f"{key}.png")
    original = plugin._load_local_logo(path, (72, 20), alpha_threshold=8)
    source_bytes = original.tobytes()
    tournament = {"tour": "WTA", "name": name}
    loaded, drawn, day, icons = _render_header(monkeypatch, tournament, DAY_COLORS)
    assert key in loaded and not icons
    assert tennis_places.venue_label(tennis_events.event_for(tournament)["venue"]) not in drawn
    assert day[-1][0].tobytes() == source_bytes
    loaded, _drawn, night, icons = _render_header(monkeypatch, tournament, DEEP_NIGHT_COLORS)
    assert key in loaded and not icons
    night_mark = night[-1][0]
    if key == "tennis_place_domain_tennis_centre":
        assert night_mark.tobytes() != source_bytes
    else:
        # Zayed's original colours already meet the night contrast threshold.
        assert night_mark.tobytes() == source_bytes
    background = _luminance(DEEP_NIGHT_COLORS["panel"])
    pixels = [pixel for pixel in night_mark.get_flattened_data() if pixel[3] >= 220]
    assert pixels
    contrasts = [(max(_luminance(pixel), background) + 0.05) / (min(_luminance(pixel), background) + 0.05)
                 for pixel in pixels]
    assert sum(contrast >= 2.0 for contrast in contrasts) / len(contrasts) > 0.95
    assert original.tobytes() == source_bytes
    assert plugin._load_local_logo(path, (72, 20), alpha_threshold=8).tobytes() == source_bytes
    _loaded, _drawn, day_after_night, _icons = _render_header(monkeypatch, tournament, DAY_COLORS)
    assert day_after_night[-1][0].tobytes() == source_bytes
