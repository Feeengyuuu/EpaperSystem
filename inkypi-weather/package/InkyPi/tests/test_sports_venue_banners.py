"""Official venue names on MotoGP, UFC and PGA cards retain their dates and city."""

from datetime import datetime, timezone

import pytest
from PIL import Image, ImageDraw

from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.scorebug_kit import ScorebugSpec
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


NOW = datetime(2026, 11, 13, 20, tzinfo=timezone.utc)


@pytest.mark.parametrize("venue", [
    "Lusail International Circuit", "Losail International Circuit", "Circuit Ricardo Tormo",
    "Circuito Ricardo Tormo", "Circuit de la Comunitat Valenciana Ricardo Tormo",
    "T-Mobile Arena", "Madison Square Garden", "Port Royal Golf Course", "Port Royal Golf Club",
])
def test_supported_venues_have_real_local_art(venue):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    logo = plugin._kit_venue_logo(venue, (130, 18))
    assert logo is not None and logo.getchannel("A").getbbox()
    assert logo.width <= 130 and logo.height <= 18
    assert plugin._kit_venue_logo("Unlisted Community Field", (130, 18)) is None


@pytest.mark.parametrize("colours", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
@pytest.mark.parametrize("sport, venue, known", [
    ("MOTOGP", "Lusail International Circuit", True),
    ("MOTOGP", "Circuit Ricardo Tormo", True),
    ("UFC", "T-Mobile Arena", True),
    ("PGA", "Port Royal Golf Course", True),
    ("MOTOGP", "Unknown Circuit", False),
    ("UFC", "Unknown Arena", False),
    ("PGA", "Unknown Golf Course", False),
])
def test_banner_marks_keep_dates_city_and_unknown_names(monkeypatch, colours, sport, venue, known):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    image = Image.new("RGB", (556, 80), colours["panel"])
    draw = ImageDraw.Draw(image)
    texts, pasted = [], []
    original_text = plugin._draw_text_in_box
    original_paste = image.paste
    monkeypatch.setattr(plugin, "_draw_text_in_box", lambda d, box, text, *args, **kw:
                        texts.append((str(text), box)) or original_text(d, box, text, *args, **kw))
    monkeypatch.setattr(image, "paste", lambda logo, xy, *args, **kw:
                        pasted.append((xy[0], xy[1], xy[0] + logo.width, xy[1] + logo.height)) or
                        original_paste(logo, xy, *args, **kw))
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda *_: None)
    token = _ACTIVE_COLORS.set(colours)
    try:
        if sport == "MOTOGP":
            event = {"name_zh": "卡塔尔大奖赛", "circuit": venue, "start": "2026-11-13", "end": "2026-11-15"}
            plugin._draw_motogp_banner(image, draw, (12, 6, 543, 66), event, colours["orange"])
            assert any(text == "11/13–11/15" for text, _ in texts)
        elif sport == "UFC":
            event = {"label": "UFC 334", "venue": venue, "city": "Las Vegas", "start": NOW,
                     "main_start": NOW, "headline": "Fighter A vs. Fighter B", "fights": [{"broadcast": "ESPN+"}]}
            plugin._draw_ufc_banner(image, draw, (12, 6, 543, 44), event, "NEXT", NOW, colours["red"])
            time_text, time_box = next((text, box) for text, box in texts if "开打" in text)
            assert time_text == "11/13 周五 20:00 开打"
            assert any("Las Vegas" in text for text, _ in texts)
            assert all(box[2] < time_box[0] for box in pasted if box[0] < time_box[0])
        else:
            event = {"name": "Bermuda Championship", "venue": venue, "start": NOW,
                     "end": datetime(2026, 11, 16, tzinfo=timezone.utc)}
            plugin._draw_pga_banner(image, draw, (12, 6, 543, 48), event, "scheduled", NOW,
                                   ScorebugSpec("PGA", "pga_accent", "pga_live", "pga_tag", "pga_course_tint"))
            assert any("11/13–11/16" in text for text, _ in texts)
    finally:
        _ACTIVE_COLORS.reset(token)
    if known:
        assert not any(venue in text for text, _ in texts)
        assert pasted
    else:
        assert any(venue in text for text, _ in texts)
    assert all(0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height for x1, y1, x2, y2 in pasted)
