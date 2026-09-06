from datetime import datetime, timezone
from types import SimpleNamespace

import requests
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


def test_branding_is_persisted_and_does_not_refetch_for_a_new_plugin(monkeypatch, tmp_path):
    from plugins.sports_dashboard import cs2_branding
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

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
    assert cs2_branding.enrich_event_branding(plugin, card, {}, now, SimpleNamespace()) == original
    assert cs2_branding.enrich_event_branding(plugin, card, {}, now, SimpleNamespace()) == original
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
                assert sum(canvas.getpixel((12, 12))) > 600, "Transparent margins need a contrasting backing"
                assert canvas.getpixel((24, 24)) == (0, 0, 0), "Brand pixels must keep their original color"
                assert canvas.getpixel((2, 2)) == common.DEEP_NIGHT_COLORS["panel"]
                assert logo.tobytes() == original
        finally:
            common._ACTIVE_COLORS.reset(token)
