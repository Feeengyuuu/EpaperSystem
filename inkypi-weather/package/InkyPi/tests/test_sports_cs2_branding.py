from datetime import datetime, timezone
from types import SimpleNamespace

import requests
import pytest
from PIL import Image, ImageDraw

from plugins.sports_dashboard.cs2_branding import match_event, parse_catalog, parse_event_logos

CATALOG = """<a href="/events/8266/fissure-playground-3" class="big-event">
<div class="big-event-name">FISSURE Playground 3</div>
<span data-unix="1788861600000"></span><span data-unix="1789293600000"></span></a>"""
DETAIL = """<img class="event-logo day-only" title="FISSURE Playground 3" src="https://img-cdn.hltv.org/eventlogo/day.png">"""


def branding_card(now):
    return {
        "event_id": "series:1001",
        "event_name": "FISSURE Playground 3",
        "event_logo_url": "https://cdn.pandascore.co/images/league/fissure.png",
        "source_state": "PANDASCORE LIVE",
        "status": "NEXT",
        "main": {"start": now, "event_name": "FISSURE Playground 3", "league_name": "FISSURE"},
    }


def test_hltv_matches_exact_edition_and_date_not_qualifier_or_previous_event():
    html = """<a href="/events/8266/fissure-playground-3" class="big-event">
      <div class="big-event-name">FISSURE Playground 3</div>
      <span data-unix="1788861600000"></span><span data-unix="1789293600000"></span>
      <img class="small-team-logo" src="https://img-cdn.hltv.org/teamlogo/wrong.png">
    </a>"""
    entries = parse_catalog(html)
    main = {
        "event_name": "FISSURE Playground 3",
        "league_name": "FISSURE",
        "start": datetime(2026, 9, 10, tzinfo=timezone.utc),
    }
    assert match_event(entries, main)["id"] == "8266"
    assert match_event(entries, {**main, "event_name": "FISSURE Playground 2"}) is None
    assert match_event(entries, {**main, "event_name": "FISSURE Playground 3 Asia Closed Qualifier"}) is None
    assert match_event(entries, {**main, "start": datetime(2027, 9, 10, tzinfo=timezone.utc)}) is None


def test_hltv_detail_ignores_related_event_logos_and_preserves_theme_variants():
    html = """<img class="event-logo" title="FISSURE Playground 2" src="https://img-cdn.hltv.org/eventlogo/wrong.png">
    <img class="event-logo day-only" title="FISSURE Playground 3" src="https://img-cdn.hltv.org/eventlogo/day.png">
    <img class="event-logo night-only" alt="FISSURE Playground 3" src="https://img-cdn.hltv.org/eventlogo/night.png">
    <img class="event-logo" title="FISSURE Playground 3" src="http://localhost/wrong.png">"""
    logos = parse_event_logos(html, "FISSURE Playground 3")
    assert logos == {
        "event_logo_url": "https://img-cdn.hltv.org/eventlogo/day.png",
        "event_logo_url_dark": "https://img-cdn.hltv.org/eventlogo/night.png",
    }


def test_live_exort_provider_alias_uses_bundled_logo_without_crossing_editions():
    from pathlib import Path
    from plugins.sports_dashboard.cs2_branding import local_event_branding

    card = {
        "event_name": "Exort Fiesta Season 2 2026",
        "main": {"start": datetime(2026, 9, 12, 17, 9, tzinfo=timezone.utc)},
    }
    logo = local_event_branding(card)
    assert Path(logo["event_logo_path"]).name == "84e762f29baae2c7fb9e.png"
    assert Path(logo["event_logo_path"]).is_file()
    for name in ("Exort Fiesta Season 1 2026", "Exort Fiesta Season 2 Closed Qualifier 2026"):
        assert not local_event_branding({**card, "event_name": name})
    assert not local_event_branding({
        **card, "main": {"start": datetime(2027, 9, 12, tzinfo=timezone.utc)},
    })


def test_live_exort_alias_renders_with_no_provider_logo_or_network(monkeypatch):
    from plugins.sports_dashboard import common
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard({"id": "sports_dashboard"})
    def no_network(*args, **kwargs):
        raise AssertionError("Live Exort card must use its bundled event logo")
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", no_network)
    card = {
        "series": "CS", "event_name": "Exort Fiesta Season 2 2026",
        "event_logo_url": "",
        "main": {"start": datetime(2026, 9, 12, 17, 9, tzinfo=timezone.utc)},
    }
    for palette in (common.DAY_COLORS, common.DEEP_NIGHT_COLORS):
        token = common._ACTIVE_COLORS.set(palette)
        try:
            with Image.new("RGB", (200, 65), palette["panel"]) as canvas:
                assert plugin._draw_valve_focus_event_logo(canvas, (0, 0, 199, 64), card)
        finally:
            common._ACTIVE_COLORS.reset(token)


def test_onewin_provider_name_resolves_only_the_matching_edition_and_date():
    from pathlib import Path
    from plugins.sports_dashboard.cs2_branding import local_event_branding

    card = {
        "event_name": "1win Private Club #1 2026",
        "main": {"start": datetime(2026, 9, 28, 11, tzinfo=timezone.utc)},
    }
    logo = local_event_branding(card)
    assert Path(logo["event_logo_path"]).name == "e51752285b21738e984a.png"
    assert Path(logo["event_logo_path"]).is_file()
    for name in ("1win Private Club #2 2026", "1win Private Club #1 Closed Qualifier 2026", "1win"):
        assert not local_event_branding({**card, "event_name": name})
    assert not local_event_branding({
        **card, "main": {"start": datetime(2027, 9, 28, tzinfo=timezone.utc)},
    })


def test_onewin_numbered_editions_match_hltv_season_names_and_stage():
    def entry(event_id, name, start, end):
        return {"id": event_id, "name": name, "start": start, "end": end}

    catalog = [
        entry("9408", "1win Private Club Season 2 Closed Qualifier",
              "2026-10-07T10:00:00+00:00", "2026-10-10T10:00:00+00:00"),
        entry("9407", "1win Private Club Season 2",
              "2026-10-19T10:00:00+00:00", "2026-10-22T10:00:00+00:00"),
    ]
    qualifier_day = datetime(2026, 10, 8, 2, tzinfo=timezone.utc)
    main_day = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)

    def matched(name, start):
        found = match_event(catalog, {"event_name": name, "start": start})
        return found and found["id"]

    assert matched("1win Private Club #2: Closed Qualifier", qualifier_day) == "9408"
    assert matched("1win Private Club #2 2026", main_day) == "9407"
    assert matched("1win Private Club #3: Closed Qualifier", qualifier_day) is None
    assert matched("1win Private Club #2", qualifier_day) is None
    assert matched("1win Private Club #2: Closed Qualifier", main_day) is None


@pytest.mark.parametrize("theme", ["DAY_COLORS", "DEEP_NIGHT_COLORS"])
def test_onewin_persisted_card_renders_offline_in_both_themes(monkeypatch, theme):
    from plugins.sports_dashboard import common
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard({"id": "sports_dashboard"})
    def no_network(*args, **kwargs):
        raise AssertionError("Persisted 1win cards must resolve their local event asset")
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", no_network)
    palette = getattr(common, theme)
    token = common._ACTIVE_COLORS.set(palette)
    try:
        with Image.new("RGB", (100, 65), palette["panel"]) as canvas:
            assert plugin._draw_valve_focus_event_logo(canvas, (5, 5, 94, 59), {
                "series": "CS", "event_name": "1win Private Club #1 2026",
                "event_logo_url": "", "main": {"start": datetime(2026, 9, 28, tzinfo=timezone.utc)},
            })
            assert len(canvas.getcolors(10000)) > 1
            assert canvas.getpixel((0, 0)) == palette["panel"]
    finally:
        common._ACTIVE_COLORS.reset(token)


@pytest.mark.parametrize("theme", ["DAY_COLORS", "DEEP_NIGHT_COLORS"])
def test_official_cs2_header_preserves_transparency_and_night_contrast(theme):
    from plugins.sports_dashboard import common
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard({"id": "sports_dashboard"})
    palette = getattr(common, theme)
    logo = plugin._load_local_logo(common.LOCAL_CS2_TITLE_WORDMARK_PATH, (180, 36), alpha_threshold=8)
    original = logo.tobytes()
    token = common._ACTIVE_COLORS.set(palette)
    try:
        with Image.new("RGB", (200, 50), palette["panel"]) as canvas:
            assert plugin._draw_cs2_title_wordmark(canvas, 10, 5, 180, 36)
            assert canvas.getpixel((0, 0)) == palette["panel"]
            y = 5 + (36 - logo.height) // 2
            opaque = transparent = 0
            for row in range(logo.height):
                for col in range(logo.width):
                    pixel = canvas.getpixel((10 + col, y + row))
                    alpha = logo.getpixel((col, row))[3]
                    if alpha == 0:
                        assert pixel == palette["panel"]
                        transparent += 1
                    elif alpha == 255:
                        assert abs(sum(pixel) - sum(palette["panel"])) > 400
                        opaque += 1
            assert opaque > 500 and transparent > 500
            assert logo.tobytes() == original, "Rendering must not recolor the shared asset cache"
    finally:
        common._ACTIVE_COLORS.reset(token)


@pytest.mark.parametrize("theme", ["DAY_COLORS", "DEEP_NIGHT_COLORS"])
def test_cs2_sidebar_keeps_original_game_icon_beside_official_wordmark(theme):
    from plugins.sports_dashboard import common
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard({"id": "sports_dashboard"})
    palette = getattr(common, theme)
    primary = {"series": "CS", "logo_path": common.LOCAL_CS_MAJOR_LOGO_PATH, "status": "NEXT"}
    icon = plugin._load_local_logo(common.LOCAL_CS_MAJOR_LOGO_PATH, (40, 40), alpha_threshold=8)
    title = plugin._load_local_logo(common.LOCAL_CS2_TITLE_WORDMARK_PATH, (160, 28), alpha_threshold=8)
    token = common._ACTIVE_COLORS.set(palette)
    try:
        with Image.new("RGB", (800, 480), palette["paper"]) as canvas:
            plugin._draw_valve_esports_sidebar(
                canvas, 552, {"primary": primary}, "PANDASCORE CACHE", datetime(2026, 9, 28, tzinfo=timezone.utc),
            )
            # Keep both identities visible in their separate original header slots.
            # Compare solid asset pixels, excluding antialiasing and halftone gaps.
            for logo, x, y, recolor in ((icon, 570, 13, False), (title, 619, 9, theme == "DEEP_NIGHT_COLORS")):
                checked = 0
                for row in range(logo.height):
                    for col in range(logo.width):
                        pixel = logo.getpixel((col, row))
                        if pixel[3] == 255:
                            assert canvas.getpixel((x + col, y + row)) == (palette["text"] if recolor else pixel[:3])
                            checked += 1
                assert checked > 100
    finally:
        common._ACTIVE_COLORS.reset(token)


def test_branding_is_persisted_and_does_not_refetch_for_a_new_plugin(monkeypatch, tmp_path):
    from plugins.sports_dashboard import cs2_branding
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    monkeypatch.setattr(cs2_branding, "_local_events", lambda: [])

    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    calls = []

    def response(client, path, context):
        calls.append(path)
        return CATALOG if path == "/events" else DETAIL

    monkeypatch.setattr(cs2_branding, "_text", response)
    original = branding_card(now)
    original["event_logo_url_dark"] = "https://cdn-api.pandascore.co/images/league/generic-dark.png"
    first = cs2_branding.enrich_event_branding(plugin, dict(original), {}, now, SimpleNamespace())
    assert first["event_logo_source"] == "HLTV" and len(calls) == 2
    assert first["event_logo_url_dark"] == "", "A specific event's day logo must not inherit generic league night art"
    restarted = SportsDashboard({"id": "sports_dashboard"})
    restarted._sports_dashboard_cache_dir = lambda: tmp_path
    cached = cs2_branding.enrich_event_branding(restarted, dict(original), {}, now, SimpleNamespace())
    assert cached["event_logo_url"] == first["event_logo_url"] and len(calls) == 2


def test_hltv_failure_retains_match_and_provider_branding_without_changing_freshness(monkeypatch, tmp_path):
    from plugins.sports_dashboard import cs2_branding
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    monkeypatch.setattr(cs2_branding, "_local_events", lambda: [])

    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    calls = []

    def failure(*args):
        calls.append(True)
        raise requests.Timeout("unavailable")

    monkeypatch.setattr(cs2_branding, "_text", failure)
    card = branding_card(now)
    card["source_state"] = "PANDASCORE LIVE"
    original = dict(card)
    for _ in range(2):
        result = cs2_branding.enrich_event_branding(plugin, card, {}, now, SimpleNamespace())
        # Only the branding source is recorded; the provider logo and freshness stay as they were.
        assert {key: value for key, value in result.items() if key != "event_logo_source"} == original
        assert result["event_logo_source"] == "PANDASCORE"
    assert len(calls) == 1


def test_day_only_black_event_logo_stays_visible_at_night_without_mutating_cached_pixels(monkeypatch):
    from plugins.sports_dashboard import common
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard({"id": "sports_dashboard"})
    with Image.new("RGBA", (32, 32), (0, 0, 0, 0)) as logo:
        ImageDraw.Draw(logo).rectangle((8, 8, 23, 23), fill=(0, 0, 0, 255))
        original = logo.tobytes()
        monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *args: logo)
        token = common._ACTIVE_COLORS.set(common.DEEP_NIGHT_COLORS)
        try:
            with Image.new("RGB", (48, 48), common.DEEP_NIGHT_COLORS["panel"]) as canvas:
                assert plugin._draw_valve_focus_event_logo(
                    canvas, (8, 8, 39, 39), {"event_logo_url": "https://cdn.pandascore.co/event/black.png"}
                )
                assert canvas.getpixel((12, 12)) == common.DEEP_NIGHT_COLORS["panel"], "Transparent margins must reveal the panel"
                assert sum(canvas.getpixel((15, 24))) > 600, "Only the logo contour needs contrast"
                assert canvas.getpixel((24, 24)) == (0, 0, 0), "Brand pixels must keep their original color"
                assert canvas.getpixel((2, 2)) == common.DEEP_NIGHT_COLORS["panel"]
                assert logo.tobytes() == original
        finally:
            common._ACTIVE_COLORS.reset(token)


def test_low_contrast_gold_logo_has_no_solid_backing_in_daylight(monkeypatch):
    from plugins.sports_dashboard import common
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard({"id": "sports_dashboard"})
    with Image.new("RGBA", (32, 32), (0, 0, 0, 0)) as logo:
        ImageDraw.Draw(logo).rectangle((8, 8, 23, 23), fill=(202, 165, 85, 255))
        original = logo.tobytes()
        monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *args: logo)
        token = common._ACTIVE_COLORS.set(common.DAY_COLORS)
        try:
            with Image.new("RGB", (48, 48), common.DAY_COLORS["panel"]) as canvas:
                assert plugin._draw_valve_focus_event_logo(
                    canvas, (8, 8, 39, 39), {"event_logo_url": "https://cdn.pandascore.co/event/gold.png"}
                )
                assert canvas.getpixel((12, 12)) == common.DAY_COLORS["panel"]
                assert canvas.getpixel((24, 24)) == (202, 165, 85)
                assert sum(canvas.getpixel((15, 24))) < 100
                assert logo.tobytes() == original
        finally:
            common._ACTIVE_COLORS.reset(token)


def _entry(event_id, name, start, end, logo):
    return {"id": event_id, "name": name, "start": start, "end": end, "event_logo_path": logo}


FUZZY_ENTRIES = [
    _entry("9426", "WINLINE MPKBK CIS LAN Season 9", "2026-10-20T00:00:00+00:00", "2026-10-23T00:00:00+00:00", "cis9.png"),
    _entry("9361", "WINLINE MPKBK CIS LAN Season 8", "2026-09-29T00:00:00+00:00", "2026-10-02T00:00:00+00:00", "cis.png"),
    _entry("9312", "Digital Crusade DraculaN Season 7", "2026-10-19T00:00:00+00:00", "2026-10-23T00:00:00+00:00", "dracula.png"),
    _entry("9457", "CCT 2026 Europe Series 12", "2026-10-19T00:00:00+00:00", "2026-10-30T00:00:00+00:00", "cct.png"),
    _entry("9456", "CCT 2026 Europe Series 11", "2026-10-12T00:00:00+00:00", "2026-10-22T00:00:00+00:00", "cct.png"),
    _entry("9458", "CCT 2026 Challengers Europe Series 8", "2026-10-19T00:00:00+00:00", "2026-10-26T00:00:00+00:00", "cctc.png"),
    _entry("9460", "Stake Ranked Episode 6 Closed Qualifier", "2026-10-18T00:00:00+00:00", "2026-10-21T00:00:00+00:00", "stake.png"),
]


@pytest.mark.parametrize("name, expected", [
    ("CIS LAN Championship Season 9 2026", "9426"),
    ("CIS LAN Championship #9 2026", "9426"),
    ("DraculaN Season 7 2026", "9312"),
    ("CCT Europe Series #12 2026", "9457"),
    ("CIS LAN Championship Season 9 Closed Qualifier 2026", None),
    ("CIS LAN Championship Season 10 2026", None),
    ("CIS Esports League Season 2 2026", None),
    ("Stake Ranked Episode 6 2026", None),
    ("DraculaN 2026", None),
])
def test_fuzzy_edition_match_tolerates_provider_naming_but_never_crosses_editions(name, expected):
    from plugins.sports_dashboard.cs2_branding import fuzzy_match_event

    main = {"event_name": name, "start": datetime(2026, 10, 21, 12, tzinfo=timezone.utc)}

    match = fuzzy_match_event(FUZZY_ENTRIES, main)

    assert (match or {}).get("id") == expected


def test_fuzzy_edition_match_needs_the_event_dates():
    from plugins.sports_dashboard.cs2_branding import fuzzy_match_event

    main = {"event_name": "DraculaN Season 7 2026", "start": datetime(2026, 11, 20, tzinfo=timezone.utc)}

    assert fuzzy_match_event(FUZZY_ENTRIES, main) is None


def test_family_match_reuses_the_latest_past_edition_of_the_same_series():
    from plugins.sports_dashboard.cs2_branding import family_match_event

    past = [entry for entry in FUZZY_ENTRIES if entry["id"] != "9426"]
    main = {"event_name": "CIS LAN Championship Season 9 2026", "start": datetime(2026, 10, 21, tzinfo=timezone.utc)}

    assert family_match_event(past, main)["id"] == "9361"
    assert family_match_event(past, {**main, "event_name": "CIS Esports League Season 2 2026"}) is None
    assert family_match_event(past, {**main, "start": datetime(2027, 9, 1, tzinfo=timezone.utc)}) is None


def test_bundled_dracula_logo_matches_the_provider_name_offline_and_reports_its_source():
    from pathlib import Path
    from plugins.sports_dashboard.cs2_branding import local_event_branding

    card = {
        "event_name": "DraculaN Season 7 2026",
        "main": {"start": datetime(2026, 10, 20, 11, tzinfo=timezone.utc)},
    }

    branding = local_event_branding(card)

    assert Path(branding["event_logo_path"]).name == "65b6bb43d275c8fb3c73.png"
    assert branding["event_logo_source"] == "LOCAL"


def test_enrich_falls_back_to_the_series_logo_only_without_any_provider_logo(monkeypatch, tmp_path):
    from pathlib import Path
    from plugins.sports_dashboard import cs2_branding
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    now = datetime(2026, 10, 20, 20, tzinfo=timezone.utc)
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    monkeypatch.setattr(cs2_branding, "_text", lambda *args: (_ for _ in ()).throw(requests.Timeout("offline")))
    card = {
        "event_id": "series:9001",
        "event_name": "CIS LAN Championship Season 9 2026",
        "event_logo_url": "",
        "main": {"start": now, "event_name": "CIS LAN Championship Season 9 2026", "league_name": "CIS LAN"},
    }

    family = cs2_branding.enrich_event_branding(plugin, dict(card), {}, now, SimpleNamespace())
    provider = cs2_branding.enrich_event_branding(
        plugin, {**card, "event_logo_url": "https://cdn.pandascore.co/images/league/cis.png"}, {}, now, SimpleNamespace(),
    )

    assert Path(family["event_logo_path"]).name == "b42d8dd7fbbcb894a498.png"
    assert family["event_logo_source"] == "LOCAL FAMILY"
    assert "event_logo_path" not in provider and provider["event_logo_source"] == "PANDASCORE"


def test_hltv_catalog_matches_sponsor_prefixed_editions(monkeypatch, tmp_path):
    from plugins.sports_dashboard import cs2_branding
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    monkeypatch.setattr(cs2_branding, "_local_events", lambda: [])
    now = datetime(2026, 10, 20, 20, tzinfo=timezone.utc)
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    catalog = """<a href="/events/9426/winline-mpkbk-cis-lan-season-9" class="big-event">
    <div class="big-event-name">WINLINE MPKBK CIS LAN Season 9</div>
    <span data-unix="1792454400000"></span><span data-unix="1792713600000"></span></a>"""
    detail = """<img class="event-logo" title="WINLINE MPKBK CIS LAN Season 9" src="https://img-cdn.hltv.org/eventlogo/cis9.png">"""
    monkeypatch.setattr(cs2_branding, "_text", lambda client, path, context: catalog if path == "/events" else detail)
    card = {
        "event_id": "series:9001",
        "event_name": "CIS LAN Championship Season 9 2026",
        "event_logo_url": "",
        "main": {"start": now, "event_name": "CIS LAN Championship Season 9 2026", "league_name": "CIS LAN"},
    }

    enriched = cs2_branding.enrich_event_branding(plugin, card, {}, now, SimpleNamespace())

    assert enriched["event_logo_url"] == "https://img-cdn.hltv.org/eventlogo/cis9.png"
    assert enriched["event_logo_source"] == "HLTV"


@pytest.mark.parametrize("name, lines", [
    ("CIS LAN Championship Season 9 2026", ("CIS LAN", "SEASON 9")),
    ("1win Private Club #2 2026", ("1WIN PRIVATE", "#2")),
    ("CCT Europe Series #12 2026", ("CCT", "#12")),
    ("DraculaN Season 7 2026", ("DRACULAN", "SEASON 7")),
    ("PGL Masters Bucharest 2026", ("PGL MASTERS", "2026")),
])
def test_event_badge_lines_name_the_organiser_and_edition(name, lines):
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    assert SportsDashboard._cs2_event_badge_lines(name) == lines


@pytest.mark.parametrize("theme", ["day", "night"])
def test_cs2_card_without_any_event_logo_draws_a_badge_instead_of_the_tag_bar(monkeypatch, theme):
    from plugins.sports_dashboard import common
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *args: None)
    primary = {
        "series": "CS",
        "auto_follow": True,
        "event_name": "Nova Showdown Season 1 2026",
        "main": {"start": datetime(2027, 3, 1, tzinfo=timezone.utc)},
    }
    palette = common.DAY_COLORS if theme == "day" else common.DEEP_NIGHT_COLORS
    token = common._ACTIVE_COLORS.set(palette)
    try:
        with Image.new("RGB", (120, 80), palette["panel"]) as canvas:
            assert plugin._draw_valve_focus_event_logo(canvas, (8, 8, 87, 65), primary)
            inside = set(canvas.crop((8, 8, 88, 66)).getdata())
            assert len(inside) > 2
            assert canvas.getpixel((100, 70)) == palette["panel"]
    finally:
        common._ACTIVE_COLORS.reset(token)


def test_fuzzy_match_does_not_hand_a_main_series_the_contenders_logo():
    from plugins.sports_dashboard.cs2_branding import family_match_event, fuzzy_match_event

    entries = [
        _entry("9372", "CCT 2026 Contenders Europe Series 9", "2026-09-10T00:00:00+00:00", "2026-09-13T00:00:00+00:00", "contenders.png"),
        _entry("9404", "CCT 2026 Europe Series 9", "2026-09-15T00:00:00+00:00", "2026-09-27T00:00:00+00:00", "cct.png"),
    ]
    main = {"event_name": "CCT Europe Series #9 2026", "start": datetime(2026, 9, 12, 14, tzinfo=timezone.utc)}

    assert fuzzy_match_event(entries, main) is None
    assert family_match_event(entries, {**main, "start": datetime(2026, 9, 14, tzinfo=timezone.utc)}) is None


def test_event_badge_never_reads_a_year_as_the_edition():
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    brand, edition = SportsDashboard._cs2_event_badge_lines("Esports World Cup 2026")

    assert brand not in ("", "CS2") and edition == "2026"
