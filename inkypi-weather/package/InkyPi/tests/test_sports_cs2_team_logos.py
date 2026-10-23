"""CS2 team logos: night variants, the bundled catalog, an HLTV fallback and a legible badge."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from PIL import Image, ImageDraw

from plugins.sports_dashboard import common, cs2_team_logos
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from tests.test_sports_cs2_follow import cs_match, loader

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
SOLID_INKS = {(0, 0, 0), (0, 0, 255), (255, 0, 0), (255, 255, 0), (0, 255, 0)}


def hltv_team(team_id, name, day, night=None):
    return {
        "id": team_id,
        "name": name,
        "teamLogoDay": day,
        "teamLogoNight": night or day,
        "location": f"/team/{team_id}/{name.lower()}",
        "type": "TEAM",
    }


K27_DAY = "https://img-cdn.hltv.org/teamlogo/k27day.png?ixlib=java-2.1.0&w=100&s=abc"
K27_NIGHT = "https://img-cdn.hltv.org/teamlogo/k27night.png?ixlib=java-2.1.0&w=100&s=def"
SEARCH = {
    "K27": [{"players": [], "teams": [
        hltv_team(12895, "K27", K27_DAY, K27_NIGHT),
        hltv_team(13001, "K27 Academy", "https://img-cdn.hltv.org/teamlogo/other.png?w=100&s=1"),
    ]}],
    "XMETA": [{"players": [], "teams": [
        hltv_team(14103, "XMETA", "https://www.hltv.org/dynamic-svg/teamplaceholder?letter=X"),
    ]}],
    "SINNERS": [{"players": [], "teams": [
        hltv_team(10577, "SINNERS", "https://img-cdn.hltv.org/teamlogo/sinners.svg?ixlib=java-2.1.0&s=1"),
    ]}],
    "SAW": [{"players": [], "teams": [
        hltv_team(10567, "SAW", "https://img-cdn.hltv.org/teamlogo/saw.png?w=100&s=1"),
        hltv_team(7784, "Saw", "https://img-cdn.hltv.org/teamlogo/saw-us.png?w=100&s=2"),
    ]}],
}


def test_pandascore_night_logo_is_kept_beside_the_day_logo():
    item = cs_match(NOW)
    item["opponents"][0]["opponent"].update(
        image_url="https://cdn.pandascore.co/images/team/image/1/mouz.png",
        dark_mode_image_url="https://cdn.pandascore.co/images/team/image/1/mouz_dark.png",
    )

    card = SportsDashboard._parse_pandascore_cs2_card([item], timezone.utc, NOW, {})

    assert card["main"]["team_a_logo"].endswith("/mouz.png")
    assert card["main"]["team_a_logo_dark"].endswith("/mouz_dark.png")
    assert card["main"]["team_b_logo_dark"] == ""


@pytest.mark.parametrize("name, expected", [
    ("Team Spirit", "spirit.png"),
    ("Natus Vincere", "natusvincere.png"),
    ("NAVI", "natusvincere.png"),
    ("G2 Esports", "g2.png"),
    ("Team Vitality", "vitality.png"),
    ("BetBoom Team", "betboom.png"),
    ("Aurora Gaming", "aurora.png"),
])
def test_bundled_cs2_logos_match_provider_names_never_provider_ids(name, expected):
    names = [Path(path).name for path in SportsDashboard._valve_local_team_logo_candidates(name, 4608, "CS")]

    assert names and names[0] == expected
    assert not any(candidate.startswith("4608") for candidate in names)
    assert all(Path(path).is_file() for path in SportsDashboard._valve_local_team_logo_candidates(name, 4608, "CS"))


def test_dota_team_logos_keep_their_numeric_files():
    names = [Path(path).name for path in SportsDashboard._valve_local_team_logo_candidates("Team Spirit", 7119388, "TI")]

    assert "7119388.png" in names


def test_night_theme_draws_the_dark_team_logo(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    seen = []
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda url, size: seen.append(url) or None)
    event = {
        "series": "CS",
        "team_a": "MOUZ",
        "team_a_logo": "https://cdn.pandascore.co/day.png",
        "team_a_logo_dark": "https://cdn.pandascore.co/night.png",
    }
    for palette in (common.DAY_COLORS, common.DEEP_NIGHT_COLORS):
        token = common._ACTIVE_COLORS.set(palette)
        try:
            with Image.new("RGB", (60, 60), palette["panel"]) as canvas:
                plugin._draw_valve_team_icon(canvas, ImageDraw.Draw(canvas), event, "a", 10, 10, 30)
        finally:
            common._ACTIVE_COLORS.reset(token)

    # Day uses the day logo; night prefers the night logo (and may still try the day one).
    assert seen[:2] == ["https://cdn.pandascore.co/day.png", "https://cdn.pandascore.co/night.png"]


class TextSpy:
    def __init__(self, draw, seen):
        self._draw = draw
        self._seen = seen

    def __getattr__(self, name):
        return getattr(self._draw, name)

    def text(self, xy, text, fill=None, *args, **kwargs):
        self._seen.append((text, fill))
        return self._draw.text(xy, text, fill, *args, **kwargs)


@pytest.mark.parametrize("palette", [common.DAY_COLORS, common.DEEP_NIGHT_COLORS])
def test_team_without_any_logo_gets_a_solid_acronym_badge(monkeypatch, palette):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *args: None)
    texts = []
    event = {"series": "CS", "team_b": "XMETA", "team_b_tag": "XMETA", "team_b_id": 99001}
    token = common._ACTIVE_COLORS.set(palette)
    try:
        with Image.new("RGB", (60, 60), palette["panel"]) as canvas:
            plugin._draw_valve_team_icon(canvas, TextSpy(ImageDraw.Draw(canvas), texts), event, "b", 10, 10, 30)
            fill = canvas.getpixel((13, 13))
    finally:
        common._ACTIVE_COLORS.reset(token)

    (text, ink), = texts
    assert text == "XMETA"
    assert fill in SOLID_INKS
    assert ink == ((0, 0, 0) if fill in {(255, 255, 0), (0, 255, 0)} else (255, 255, 255))


def test_white_team_logo_shows_in_ink_without_a_backing_on_the_light_board(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    with Image.new("RGBA", (30, 30), (0, 0, 0, 0)) as logo:
        ImageDraw.Draw(logo).rectangle((8, 8, 21, 21), fill=(255, 255, 255, 255))
        monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *args: logo)
        token = common._ACTIVE_COLORS.set(common.DAY_COLORS)
        try:
            board = common.DAY_COLORS["panel_blue"]
            with Image.new("RGB", (60, 60), board) as canvas:
                event = {"series": "CS", "team_a": "White", "team_a_logo": "https://cdn.pandascore.co/white.png"}
                plugin._draw_valve_team_icon(canvas, ImageDraw.Draw(canvas), event, "a", 10, 10, 30)
                assert canvas.getpixel((25, 25)) == common.DAY_COLORS["text"]
                assert canvas.getpixel((14, 25)) == board, "no backing is drawn around the mark"
        finally:
            common._ACTIVE_COLORS.reset(token)


def test_hltv_search_takes_only_a_unique_exact_png_team_logo():
    assert cs2_team_logos.parse_team_search(SEARCH["K27"], "K27") == {
        "logo_url": K27_DAY,
        "logo_url_dark": K27_NIGHT,
        "hltv_id": 12895,
    }
    assert cs2_team_logos.parse_team_search(SEARCH["XMETA"], "XMETA") == {}
    assert cs2_team_logos.parse_team_search(SEARCH["SINNERS"], "SINNERS") == {}
    assert cs2_team_logos.parse_team_search(SEARCH["SAW"], "SAW") == {}
    assert cs2_team_logos.parse_team_search({"bad": "shape"}, "K27") == {}


def event(name, logo="", **extra):
    return {"team_a": name, "team_a_logo": logo, "team_b": "MOUZ",
            "team_b_logo": "https://cdn.pandascore.co/images/team/image/1/mouz.png", **extra}


def card_with(*events):
    return {"main": events[0], "live": [], "upcoming": list(events[1:]), "recent": [], "events": [events[0]]}


def plugin_at(tmp_path):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    return plugin


def test_missing_team_logos_come_from_hltv_once_and_are_cached(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cs2_team_logos, "_search", lambda client, term, context: calls.append(term) or SEARCH[term])

    first = cs2_team_logos.enrich_team_logos(
        plugin_at(tmp_path), card_with(event("K27"), event("XMETA")), {}, NOW, SimpleNamespace(),
    )

    assert first["main"]["team_a_logo"] == K27_DAY and first["main"]["team_a_logo_dark"] == K27_NIGHT
    assert first["main"]["team_b_logo"].endswith("/mouz.png"), "provider logos are never replaced"
    assert first["upcoming"][0]["team_a_logo"] == ""
    assert sorted(calls) == ["K27", "XMETA"]

    second = cs2_team_logos.enrich_team_logos(
        plugin_at(tmp_path), card_with(event("K27"), event("XMETA")), {}, NOW + timedelta(hours=1), SimpleNamespace(),
    )

    assert second["main"]["team_a_logo"] == K27_DAY
    assert sorted(calls) == ["K27", "XMETA"], "found and missing teams are both remembered"


def test_team_logo_lookup_honours_the_hltv_setting_and_cache_only_renders(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cs2_team_logos, "_search", lambda client, term, context: calls.append(term) or SEARCH[term])

    off = cs2_team_logos.enrich_team_logos(
        plugin_at(tmp_path), card_with(event("K27")), {"pandaScoreCs2HltvLogos": "false"}, NOW, SimpleNamespace(),
    )
    cached_only = cs2_team_logos.enrich_team_logos(
        plugin_at(tmp_path), card_with(event("K27")), {}, NOW, SimpleNamespace(), allow_network=False,
    )

    assert off["main"]["team_a_logo"] == "" and cached_only["main"]["team_a_logo"] == ""
    assert calls == []


def test_team_logo_lookups_are_bounded_per_refresh(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cs2_team_logos, "_search", lambda client, term, context: calls.append(term) or [])
    teams = [event(f"Team {index}") for index in range(12)]

    cs2_team_logos.enrich_team_logos(plugin_at(tmp_path), card_with(*teams), {}, NOW, SimpleNamespace())

    assert 0 < len(calls) <= cs2_team_logos.MAX_LOOKUPS_PER_REFRESH


def test_hltv_failure_backs_off_without_touching_the_card(monkeypatch, tmp_path):
    calls = []

    def failure(client, term, context):
        calls.append(term)
        raise requests.Timeout("unavailable")

    monkeypatch.setattr(cs2_team_logos, "_search", failure)
    for _ in range(2):
        card = cs2_team_logos.enrich_team_logos(
            plugin_at(tmp_path), card_with(event("K27"), event("Wildcard")), {}, NOW, SimpleNamespace(),
        )
        assert card["main"]["team_a_logo"] == ""

    assert len(calls) == 1


def test_pandascore_loader_fills_missing_team_logos_from_hltv(monkeypatch, tmp_path):
    from plugins.sports_dashboard import cs2_branding

    item = cs_match(NOW, team_a="K27", team_b="Spirit")
    plugin, device, _session = loader(monkeypatch, tmp_path, {"running": [], "upcoming": [item], "past": []})
    monkeypatch.setattr(cs2_branding, "_text", lambda *args: (_ for _ in ()).throw(requests.Timeout("offline")))
    monkeypatch.setattr(cs2_team_logos, "_search", lambda client, term, context: SEARCH.get(term, []))

    card, _source = plugin._load_pandascore_cs2_card({}, device, timezone.utc, NOW)

    assert card["main"]["team_a_logo"] == K27_DAY


@pytest.mark.parametrize("size", [17, 30])
@pytest.mark.parametrize("tag", ["XMETA", "ALTERNATE", "HEROIC", ""])
def test_team_badge_text_always_stays_inside_the_badge(size, tag):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    background = (12, 34, 56)
    event = {"team_a": "ALTERNATE aTTaX" if not tag else tag, "team_a_tag": tag, "team_a_id": 7}
    with Image.new("RGB", (120, 60), background) as canvas:
        plugin._draw_valve_team_badge(ImageDraw.Draw(canvas), event, "a", 40, 10, size)
        changed = [
            (x, y) for y in range(canvas.height) for x in range(canvas.width) if canvas.getpixel((x, y)) != background
        ]

    assert changed
    assert min(x for x, _y in changed) >= 40 and max(x for x, _y in changed) <= 40 + size
    assert min(y for _x, y in changed) >= 10 and max(y for _x, y in changed) <= 10 + size


def test_theme_only_redraw_never_reaches_pandascore_or_hltv(monkeypatch, tmp_path):
    from plugins.sports_dashboard import cs2_branding

    item = cs_match(NOW, team_a="K27", team_b="Spirit")
    plugin, device, session = loader(monkeypatch, tmp_path, {"running": [], "upcoming": [item], "past": []})
    plugin._load_pandascore_cs2_card({"pandaScoreCs2HltvLogos": "false"}, device, timezone.utc, NOW)
    primed = len(session.calls)
    calls = []
    monkeypatch.setattr(cs2_branding, "_text", lambda *args: calls.append("event") or "")
    monkeypatch.setattr(cs2_team_logos, "_search", lambda client, term, context: calls.append(term) or [])

    card, _source = plugin._load_pandascore_cs2_card(
        {"_theme_render_only": True}, device, timezone.utc, NOW + timedelta(seconds=30),
    )

    assert card and card["main"]["team_a"] == "K27"
    assert calls == [] and len(session.calls) == primed
