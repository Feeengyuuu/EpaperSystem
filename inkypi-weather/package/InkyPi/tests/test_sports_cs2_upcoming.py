from datetime import datetime, timezone

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard import common
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from tests.test_sports_cs2_follow import cs_match


def test_live_cs2_keeps_visible_upcoming_heading_even_when_no_further_fixture_is_listed(monkeypatch):
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    card = SportsDashboard._parse_pandascore_cs2_card(
        [cs_match(now, status="running", offset=-1)], timezone.utc, now, {}
    )
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *args: None)
    original = ImageDraw.ImageDraw.text
    visible_text = []

    def record_text(draw, xy, text, *args, **kwargs):
        visible_text.append(str(text))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    with Image.new("RGB", (800, 480), "white") as canvas:
        plugin._draw_valve_esports_sidebar(canvas, 552, {"primary": card}, "PANDASCORE LIVE", now)
    assert "UPCOMING" in visible_text
    assert "RECENT" in visible_text
    assert "No further event matches" in visible_text


@pytest.mark.parametrize(
    "schedule_state,expected", [("fresh", "No further event matches"), ("unavailable", "Schedule unavailable")]
)
def test_empty_upcoming_uses_own_schedule_status_independent_of_discovery(monkeypatch, schedule_state, expected):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    text = []
    with Image.new("RGB", (800, 480), "white") as canvas:
        draw = ImageDraw.Draw(canvas)
        monkeypatch.setattr(draw, "text", lambda xy, value, **kwargs: text.append(value))
        plugin._draw_valve_esports_recent_rows(
            canvas,
            draw,
            552,
            248,
            282,
            [],
            {"auto_follow": True, "schedule_state": schedule_state, "source_state": "PANDASCORE CACHE PARTIAL"},
            "black",
            title="UPCOMING",
        )
    assert expected in text


@pytest.mark.parametrize("palette", [common.DAY_COLORS, common.DEEP_NIGHT_COLORS])
def test_upcoming_row_keeps_both_teams_without_repeating_event_branding(monkeypatch, palette):
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    live = cs_match(now, status="running", offset=-1, event="Current Cup")
    future = cs_match(now, match_id=502, offset=24, event="Current Cup")
    future["tournament"]["id"] = 23002
    future["tournament"]["image_url"] = "https://cdn-api.pandascore.co/future.png"
    future["opponents"][0]["opponent"]["image_url"] = "https://cdn-api.pandascore.co/left.png"
    future["opponents"][1]["opponent"]["image_url"] = "https://cdn-api.pandascore.co/right.png"
    colors = {
        future["tournament"]["image_url"]: (47, 170, 187),
        future["opponents"][0]["opponent"]["image_url"]: (255, 51, 119),
        future["opponents"][1]["opponent"]["image_url"]: (102, 170, 51),
    }
    logos = {url: Image.new("RGBA", (16, 16), color + (255,)) for url, color in colors.items()}
    card = SportsDashboard._parse_pandascore_cs2_card([live, future], timezone.utc, now, {})
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda url, size: logos.get(url))
    captions = []
    original_caption = plugin._draw_cs2_event_caption
    def record_caption(draw, box, caption):
        captions.append((box, caption))
        return original_caption(draw, box, caption)
    monkeypatch.setattr(plugin, "_draw_cs2_event_caption", record_caption)
    token = common._ACTIVE_COLORS.set(palette)
    try:
        with Image.new("RGB", (800, 480), palette["paper"]) as canvas:
            plugin._draw_valve_esports_sidebar(canvas, 552, {"primary": card}, "PANDASCORE LIVE", now)
            with canvas.crop((570, 311, 787, 373)) as upcoming:
                pixels = {upcoming.getpixel((x, y)) for x in range(upcoming.width) for y in range(upcoming.height)}
                team_colors = [colors[entry['opponent']['image_url']] for entry in future['opponents']]
                assert all(color in pixels for color in team_colors), "Upcoming keeps both team logos"
                assert colors[future['tournament']['image_url']] not in pixels, "Event logo is already above"
                assert all(box[1] < 311 for box, caption in captions), "Event caption must appear only in the focus card"
    finally:
        common._ACTIVE_COLORS.reset(token)
        for logo in logos.values():
            logo.close()


def test_next_focus_match_is_not_repeated_in_upcoming_after_deserialization():
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    card = SportsDashboard._parse_pandascore_cs2_card([cs_match(now)], timezone.utc, now, {})
    card["main"] = dict(card["main"])
    assert SportsDashboard._valve_sidebar_secondary_sections(card) == [("UPCOMING", []), ("RECENT", [])]


def test_other_event_cannot_replace_current_events_stale_schedule():
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    live = cs_match(now, status="running", offset=-1)
    stale = cs_match(now, match_id=502, offset=1)
    stale["_cs2_feed_fresh"] = False
    fresh = cs_match(now, match_id=503, offset=24, event="Next Cup")
    fresh["serie"]["id"] = 1002
    card = SportsDashboard._parse_pandascore_cs2_card([live, stale, fresh], timezone.utc, now, {})
    assert card["main"]["match_id"] == "501"
    assert SportsDashboard._valve_sidebar_secondary_sections(card)[0][1][0]["match_id"] == "502"


@pytest.mark.parametrize("with_stale_fixture", [False, True])
def test_failed_upcoming_feed_does_not_present_unavailable_schedule_as_fresh(monkeypatch, with_stale_fixture):
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    rows = [cs_match(now, status="running", offset=-1)]
    if with_stale_fixture:
        upcoming = cs_match(now, match_id=502)
        upcoming["_cs2_feed_fresh"] = False
        rows.append(upcoming)
    card = SportsDashboard._parse_pandascore_cs2_card(rows, timezone.utc, now, {})
    card["source_state"] = "PANDASCORE CACHE PARTIAL"
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *args: None)
    original = ImageDraw.ImageDraw.text
    visible_text = []

    def record_text(draw, xy, text, *args, **kwargs):
        visible_text.append(str(text))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    with Image.new("RGB", (800, 480), "white") as canvas:
        plugin._draw_valve_esports_sidebar(canvas, 552, {"primary": card}, card["source_state"], now)
    assert ("STALE" if with_stale_fixture else "Schedule unavailable") in visible_text
    assert "No further event matches" not in visible_text
