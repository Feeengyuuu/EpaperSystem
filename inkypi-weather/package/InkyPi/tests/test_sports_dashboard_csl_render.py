import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.sports_dashboard.sports_dashboard import (
    DAY_COLORS,
    DEEP_NIGHT_COLORS,
    SportsDashboard,
    _ACTIVE_COLORS,
)


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _plugin():
    return SportsDashboard({"id": "sports_dashboard"})


def _empty_selected(presentation=None):
    selected = {
        "live": [],
        "upcoming": [],
        "recent": [],
        "main": None,
        "visible_matches": 4,
        "season": "2026",
    }
    if presentation is not None:
        selected["presentation"] = presentation
    return selected


def _csl_presentation(**overrides):
    presentation = {
        "competition": "csl",
        "title": "2026 中超联赛",
        "league_logo_url": "asset://csl-league",
        "team_asset_kind": "logo",
        "empty_schedule_text": "暂无中超赛程",
        "upcoming_empty_text": "暂无后续中超赛程",
        "recent_empty_text": "暂无近期赛果",
        "show_worldcup_banner": False,
        "show_five_leagues_filler": False,
        "upcoming_max_rows": 3,
        "upcoming_row_gap": 0,
        "main_team_logo_scale": 1.4,
        "main_team_name_max_size": 14,
        "main_team_name_min_size": 7,
        "main_team_points_offset": 11,
        "main_team_odds_offset": 6,
    }
    presentation.update(overrides)
    return presentation


def _event(
    event_id,
    start,
    team_a,
    team_b,
    team_a_logo,
    team_b_logo,
    *,
    state="NS",
    wins_a=None,
    wins_b=None,
):
    return {
        "event_id": event_id,
        "start": start,
        "state": state,
        "status": "FT" if state == "FT" else "Scheduled",
        "team_a": team_a,
        "team_b": team_b,
        "team_a_tla": team_a[:3],
        "team_b_tla": team_b[:3],
        # CSL's provider keeps the remote badge URL in the historical flag slots.
        "team_a_flag": team_a_logo,
        "team_b_flag": team_b_logo,
        "wins_a": wins_a,
        "wins_b": wins_b,
        "block": "中超 第20轮",
        "provider": "ESPN",
        "score_source": "ESPN",
        "provider_status_confirmed": state == "FT",
        "score_confirmed": state == "FT",
    }


def test_csl_title_wordmark_is_a_transparent_high_contrast_cutout():
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "sports_dashboard"
        / "assets"
        / "decor"
        / "csl_2026_title_wordmark.png"
    )

    assert path.is_file()
    with Image.open(path) as source:
        wordmark = source.convert("RGBA")

    assert wordmark.width > wordmark.height * 5
    assert wordmark.getchannel("A").getextrema() == (0, 255)
    assert all(
        wordmark.getpixel(point)[3] == 0
        for point in (
            (0, 0),
            (wordmark.width - 1, 0),
            (0, wordmark.height - 1),
            (wordmark.width - 1, wordmark.height - 1),
        )
    )
    visible_colors = [
        color
        for _count, color in (wordmark.getcolors(maxcolors=65536) or [])
        if color[3] > 0
    ]
    assert any(max(red, green, blue) < 70 for red, green, blue, _alpha in visible_colors)
    assert any(red > 180 and green < 110 for red, green, _blue, _alpha in visible_colors)
    assert any(red > 200 and green > 130 and blue < 100 for red, green, blue, _alpha in visible_colors)


def test_csl_title_wordmark_adapts_dark_ink_for_night_without_mutating_day_asset(
    monkeypatch,
):
    plugin = _plugin()
    selected = _empty_selected(_csl_presentation())
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda _url, _size: None)

    token = _ACTIVE_COLORS.set(DEEP_NIGHT_COLORS)
    try:
        night = plugin._render_worldcup_api_panel(
            (536, 240),
            selected,
            "CSL ESPN LIVE",
            NOW,
            4,
            NOW,
        )
    finally:
        _ACTIVE_COLORS.reset(token)

    token = _ACTIVE_COLORS.set(DAY_COLORS)
    try:
        day = plugin._render_worldcup_api_panel(
            (536, 240),
            selected,
            "CSL ESPN LIVE",
            NOW,
            4,
            NOW,
        )
    finally:
        _ACTIVE_COLORS.reset(token)

    night_palette = {
        color
        for _count, color in (
            night.crop((61, 3, 229, 32)).getcolors(maxcolors=168 * 29) or []
        )
    }
    day_palette = {
        color
        for _count, color in (
            day.crop((61, 3, 229, 32)).getcolors(maxcolors=168 * 29) or []
        )
    }

    assert DEEP_NIGHT_COLORS["text"] in night_palette
    assert any(red > 180 and green < 110 for red, green, _blue in night_palette)
    assert any(
        red > 200 and green > 130 and blue < 100
        for red, green, blue in night_palette
    )
    assert any(max(color) < 70 for color in day_palette)


def test_csl_title_wordmark_preserves_accent_pixels_and_alpha_in_night_theme(
    monkeypatch,
):
    plugin = _plugin()
    source = Image.new("RGBA", (6, 1))
    source.putdata(
        [
            (20, 18, 22, 255),
            (226, 34, 43, 255),
            (242, 112, 28, 255),
            (246, 194, 38, 255),
            (21, 19, 23, 96),
            (20, 18, 22, 0),
        ]
    )
    source_bytes = source.tobytes()
    pasted = []

    class PasteRecorder:
        def paste(self, wordmark, _position, mask):
            pasted.append((wordmark.copy(), mask.copy()))

    monkeypatch.setattr(plugin, "_load_local_logo", lambda *_args, **_kwargs: source)

    def draw_with_palette(palette):
        token = _ACTIVE_COLORS.set(palette)
        try:
            assert plugin._draw_csl_2026_title_wordmark(
                PasteRecorder(),
                0,
                0,
                6,
                1,
            )
        finally:
            _ACTIVE_COLORS.reset(token)
        return pasted[-1]

    day_wordmark, day_mask = draw_with_palette(DAY_COLORS)
    night_wordmark, night_mask = draw_with_palette(DEEP_NIGHT_COLORS)

    assert day_wordmark.tobytes() == source_bytes
    assert day_mask.tobytes() == source_bytes
    assert [night_wordmark.getpixel((pixel_x, 0)) for pixel_x in range(6)] == [
        (*DEEP_NIGHT_COLORS["text"], 255),
        (226, 34, 43, 255),
        (242, 112, 28, 255),
        (246, 194, 38, 255),
        (*DEEP_NIGHT_COLORS["text"], 96),
        (20, 18, 22, 0),
    ]
    assert night_mask.tobytes() == night_wordmark.tobytes()
    assert source.tobytes() == source_bytes


def test_csl_header_uses_art_wordmark_and_enlarges_league_logo_by_thirty_percent(
    monkeypatch,
):
    plugin = _plugin()
    rendered_text = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(draw, xy, text, *args, **kwargs):
        rendered_text.append(str(text))
        return original_text(draw, xy, text, *args, **kwargs)

    def load_logo(url, size):
        if url == "asset://csl-league":
            return Image.new("RGBA", (size, size), (18, 154, 72, 255))
        return None

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", load_logo)

    image = plugin._render_worldcup_api_panel(
        (536, 240),
        _empty_selected(_csl_presentation()),
        "CSL ESPN LIVE",
        NOW,
        4,
        NOW,
    )

    assert "2026 中超联赛" not in rendered_text
    header_colors = image.crop((0, 0, 280, 49)).getcolors(maxcolors=280 * 49)
    assert header_colors is not None
    header_counts = {color: count for count, color in header_colors}
    assert header_counts.get((18, 154, 72), 0) >= 39 * 39
    title_colors = image.crop((60, 4, 260, 42)).getcolors(maxcolors=200 * 38)
    assert title_colors is not None
    title_palette = {color for _count, color in title_colors}
    assert any(red > 180 and green < 110 for red, green, _blue in title_palette)
    assert any(
        red > 200 and green > 130 and blue < 100
        for red, green, blue in title_palette
    )


def test_csl_header_falls_back_to_plain_title_when_wordmark_is_unavailable(
    monkeypatch,
):
    plugin = _plugin()
    rendered_text = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(draw, xy, text, *args, **kwargs):
        rendered_text.append(str(text))
        return original_text(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(plugin, "_draw_csl_2026_title_wordmark", lambda *_args: False)
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda _url, size: Image.new("RGBA", (size, size)))

    plugin._render_worldcup_api_panel(
        (536, 240),
        _empty_selected(_csl_presentation()),
        "CSL ESPN LIVE",
        NOW,
        4,
        NOW,
    )

    assert "2026 中超联赛" in rendered_text


def test_csl_header_does_not_reuse_2026_wordmark_for_another_season(monkeypatch):
    plugin = _plugin()
    rendered_text = []
    wordmark_calls = []
    original_text = ImageDraw.ImageDraw.text
    selected = _empty_selected(_csl_presentation(title="2027 中超联赛"))
    selected["season"] = "2027"

    def record_text(draw, xy, text, *args, **kwargs):
        rendered_text.append(str(text))
        return original_text(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(
        plugin,
        "_draw_csl_2026_title_wordmark",
        lambda *_args: wordmark_calls.append(True),
    )
    monkeypatch.setattr(
        plugin,
        "_load_team_logo_for_render",
        lambda _url, size: Image.new("RGBA", (size, size)),
    )

    plugin._render_worldcup_api_panel(
        (536, 240),
        selected,
        "CSL ESPN LIVE",
        NOW,
        4,
        NOW,
    )

    assert "2027 中超联赛" in rendered_text
    assert wordmark_calls == []


def test_csl_profile_renders_its_title_and_league_logo_without_worldcup_header_art(
    monkeypatch,
):
    plugin = _plugin()
    rendered_text = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(draw, xy, text, *args, **kwargs):
        rendered_text.append(str(text))
        return original_text(draw, xy, text, *args, **kwargs)

    def load_logo(url, size):
        if url == "asset://csl-league":
            return Image.new("RGBA", (size, size), (18, 154, 72, 255))
        return None

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", load_logo)

    image = plugin._render_worldcup_api_panel(
        (536, 240),
        _empty_selected(_csl_presentation()),
        "ESPN LIVE",
        NOW,
        4,
        NOW,
    )

    assert image.size == (536, 240)
    assert "2026 中超联赛" not in rendered_text
    league_logo_colors = image.crop((14, 7, 53, 46)).getcolors(maxcolors=1600)
    assert league_logo_colors is not None
    assert any(color == (18, 154, 72) for _count, color in league_logo_colors)
    header_art_colors = image.crop((235, 0, 441, 48)).getcolors(maxcolors=206 * 48)
    assert header_art_colors is not None
    assert len(header_art_colors) <= 3


def test_csl_profile_uses_team_badges_in_main_upcoming_and_recent_rows(monkeypatch):
    plugin = _plugin()
    main = _event(
        "main",
        datetime(2026, 7, 26, 11, 35, tzinfo=timezone.utc),
        "成都蓉城",
        "北京国安",
        "asset://main-a",
        "asset://main-b",
    )
    upcoming = _event(
        "upcoming",
        datetime(2026, 7, 31, 11, 35, tzinfo=timezone.utc),
        "河南队",
        "大连英博",
        "asset://upcoming-a",
        "asset://upcoming-b",
    )
    recent = _event(
        "recent",
        datetime(2026, 7, 25, 11, 35, tzinfo=timezone.utc),
        "上海海港",
        "上海申花",
        "asset://recent-a",
        "asset://recent-b",
        state="FT",
        wins_a=2,
        wins_b=0,
    )
    colors = {
        "asset://csl-league": (18, 154, 72, 255),
        "asset://main-a": (224, 36, 46, 255),
        "asset://main-b": (26, 79, 214, 255),
        "asset://upcoming-a": (242, 179, 24, 255),
        "asset://upcoming-b": (131, 53, 190, 255),
        "asset://recent-a": (12, 171, 184, 255),
        "asset://recent-b": (232, 105, 23, 255),
    }

    def load_logo(url, size):
        color = colors.get(url)
        return Image.new("RGBA", (size, size), color) if color else None

    monkeypatch.setattr(plugin, "_load_team_logo_for_render", load_logo)
    selected = {
        "live": [],
        "upcoming": [main, upcoming],
        "recent": [recent],
        "main": main,
        "visible_matches": 4,
        "season": "2026",
        "presentation": _csl_presentation(),
    }

    image = plugin._render_worldcup_api_panel(
        (536, 240),
        selected,
        "ESPN LIVE",
        NOW,
        4,
        NOW,
    )

    main_colors = image.crop((25, 108, 245, 151)).getcolors(maxcolors=220 * 43)
    upcoming_colors = image.crop((272, 78, 524, 113)).getcolors(maxcolors=252 * 35)
    recent_colors = image.crop((272, 199, 524, 232)).getcolors(maxcolors=252 * 33)

    assert main_colors is not None
    assert upcoming_colors is not None
    assert recent_colors is not None
    assert {(224, 36, 46), (26, 79, 214)} <= {
        color for _count, color in main_colors
    }
    assert {(242, 179, 24), (131, 53, 190)} <= {
        color for _count, color in upcoming_colors
    }
    assert {(12, 171, 184), (232, 105, 23)} <= {
        color for _count, color in recent_colors
    }


def test_csl_profile_reserves_recent_row_at_production_height(monkeypatch):
    plugin = _plugin()
    starts = [
        datetime(2026, 7, 26, 11, 35, tzinfo=timezone.utc),
        datetime(2026, 7, 27, 11, 35, tzinfo=timezone.utc),
        datetime(2026, 7, 28, 11, 35, tzinfo=timezone.utc),
        datetime(2026, 7, 29, 11, 35, tzinfo=timezone.utc),
    ]
    upcoming = [
        _event(
            f"upcoming-{index}",
            start,
            f"Team {index}A",
            f"Team {index}B",
            f"asset://upcoming-{index}-a",
            f"asset://upcoming-{index}-b",
        )
        for index, start in enumerate(starts)
    ]
    recent = _event(
        "recent",
        datetime(2026, 7, 25, 11, 35, tzinfo=timezone.utc),
        "Recent A",
        "Recent B",
        "asset://recent-a",
        "asset://recent-b",
        state="FT",
        wins_a=1,
        wins_b=0,
    )
    upcoming_boxes = []
    recent_boxes = []

    def record_upcoming(
        _image,
        _draw,
        _x1,
        _x2,
        y,
        row_h,
        event,
        _center_text,
        **_kwargs,
    ):
        upcoming_boxes.append((event["event_id"], y, row_h))

    def record_recent(
        _image,
        _draw,
        _x1,
        _x2,
        y,
        row_h,
        event,
        **_kwargs,
    ):
        recent_boxes.append((event["event_id"], y, row_h))

    monkeypatch.setattr(plugin, "_draw_worldcup_mini_match_row", record_upcoming)
    monkeypatch.setattr(plugin, "_draw_worldcup_recent_match_row", record_recent)
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda _url, _size: None)

    selected = plugin._select_csl_event_sections(
        [recent, *upcoming],
        NOW,
        4,
    )
    image = plugin._render_worldcup_api_panel(
        (536, 208),
        selected,
        "CSL ESPN CACHE",
        NOW,
        4,
        NOW,
    )

    assert image.size == (536, 208)
    assert [event_id for event_id, _y, _row_h in upcoming_boxes] == [
        "upcoming-1",
        "upcoming-2",
    ]
    assert len(recent_boxes) == 1
    assert max(y + row_h for _event_id, y, row_h in upcoming_boxes) < recent_boxes[0][1]
    assert recent_boxes[0][1] + recent_boxes[0][2] <= 200


def test_csl_profile_enlarges_main_badges_and_keeps_odds_above_bottom_border(
    monkeypatch,
):
    plugin = _plugin()
    event = _event(
        "main",
        datetime(2026, 7, 26, 11, 35, tzinfo=timezone.utc),
        "Main Team A",
        "Main Team B",
        "asset://main-a",
        "asset://main-b",
    )
    event["odds"] = {
        "team_a": "1.80",
        "draw": "3.20",
        "team_b": "4.00",
    }
    badge_slots = []
    team_name_sizes = {}
    team_name_centers = []
    lower_text_boxes = []
    original_fit_text = plugin._fit_text
    original_draw_centered = plugin._draw_centered
    original_draw_odds = plugin._draw_worldcup_odds_text

    def record_badge(
        _image,
        _draw,
        _url,
        _x,
        _y,
        max_width,
        height,
        _fallback,
        **_kwargs,
    ):
        badge_slots.append((max_width, height))
        return min(max_width, height)

    def record_fit_text(draw, text, max_width, max_size, **kwargs):
        if text in {"Main Team A", "Main Team B"}:
            team_name_sizes[text] = (max_size, kwargs.get("min_size"))
        return original_fit_text(draw, text, max_width, max_size, **kwargs)

    def record_centered(draw, center, text, font, fill):
        if text in {"Main Team A", "Main Team B"}:
            team_name_centers.append(center)
        return original_draw_centered(draw, center, text, font, fill)

    def record_odds(draw, box, value, **kwargs):
        lower_text_boxes.append((box, value))
        return original_draw_odds(draw, box, value, **kwargs)

    monkeypatch.setattr(plugin, "_draw_worldcup_presented_flag", record_badge)
    monkeypatch.setattr(plugin, "_fit_text", record_fit_text)
    monkeypatch.setattr(plugin, "_draw_centered", record_centered)
    monkeypatch.setattr(plugin, "_draw_worldcup_odds_text", record_odds)
    image = Image.new("RGB", (536, 208), "white")
    plugin._draw_worldcup_main_card(
        image,
        ImageDraw.Draw(image),
        12,
        57,
        258,
        200,
        event,
        NOW,
        "next",
        _csl_presentation(),
    )

    assert badge_slots == [(76, 38), (76, 38)]
    assert team_name_sizes == {
        "Main Team A": (14, 7),
        "Main Team B": (14, 7),
    }
    assert [center[1] for center in team_name_centers] == [169, 169]
    assert [box[1] for box, _value in lower_text_boxes] == [
        180,
        180,
        186,
        186,
        186,
    ]
    assert max(box[3] for box, value in lower_text_boxes if value in {"1.80", "X 3.20", "4.00"}) <= 198


def test_csl_profile_uses_csl_empty_copy_without_five_leagues_filler(monkeypatch):
    plugin = _plugin()
    rendered_text = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(draw, xy, text, *args, **kwargs):
        rendered_text.append(str(text))
        return original_text(draw, xy, text, *args, **kwargs)

    def reject_worldcup_filler(_size):
        raise AssertionError("CSL must not load the World Cup five-leagues filler")

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(
        plugin,
        "_load_worldcup_five_leagues_upcoming",
        reject_worldcup_filler,
    )
    monkeypatch.setattr(
        plugin,
        "_draw_worldcup_tactics_strip",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CSL must not render World Cup pitch art")
        ),
    )
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda _url, _size: None)

    plugin._render_worldcup_api_panel(
        (536, 240),
        _empty_selected(_csl_presentation()),
        "CSL ESPN LIVE",
        NOW,
        4,
        NOW,
    )

    assert "暂无中超赛程" in rendered_text
    assert "暂无后续中超赛程" in rendered_text
    assert "No World Cup schedule" not in rendered_text
    assert "No more World Cup schedule" not in rendered_text


def test_csl_profile_skips_worldcup_pitch_art_between_upcoming_and_recent(
    monkeypatch,
):
    plugin = _plugin()
    upcoming = _event(
        "upcoming",
        datetime(2026, 7, 26, 11, 35, tzinfo=timezone.utc),
        "成都蓉城",
        "北京国安",
        "asset://upcoming-a",
        "asset://upcoming-b",
    )
    recent = _event(
        "recent",
        datetime(2026, 7, 25, 11, 35, tzinfo=timezone.utc),
        "上海海港",
        "上海申花",
        "asset://recent-a",
        "asset://recent-b",
        state="FT",
        wins_a=2,
        wins_b=0,
    )
    monkeypatch.setattr(
        plugin,
        "_draw_worldcup_pitch_strip_in_gap",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CSL must not render World Cup pitch art")
        ),
    )
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda _url, _size: None)

    image = plugin._render_worldcup_api_panel(
        (536, 240),
        {
            "live": [],
            "upcoming": [upcoming],
            "recent": [recent],
            "main": upcoming,
            "visible_matches": 4,
            "season": "2026",
            "presentation": _csl_presentation(),
        },
        "CSL ESPN CACHE",
        NOW,
        4,
        NOW,
    )

    assert image.size == (536, 240)


def test_csl_source_states_have_explicit_labels():
    expected_prefixes = {
        "CSL ESPN LIVE": "CSL ESPN DATA",
        "CSL ESPN CACHE": "CSL ESPN CACHE",
        "CSL ESPN STALE": "CSL ESPN STALE",
        "CSL ESPN UNAVAILABLE": "CSL ESPN UNAVAILABLE",
    }

    for state, expected_prefix in expected_prefixes.items():
        label = SportsDashboard._worldcup_api_source_label(state, None)
        assert label == expected_prefix


def test_competition_source_timestamp_uses_the_passed_device_timezone():
    label = SportsDashboard._worldcup_api_source_label(
        "CSL ESPN LIVE",
        "2026-07-25T16:30:00+00:00",
        ZoneInfo("Asia/Shanghai"),
    )

    assert label == "CSL ESPN DATA 12:30 AM"


def test_worldcup_without_profile_matches_pre_profile_pixels():
    plugin = _plugin()

    image = plugin._render_worldcup_api_panel(
        (536, 240),
        _empty_selected(),
        "ESPN CACHE",
        None,
        4,
        NOW,
    )

    assert hashlib.sha256(image.tobytes()).hexdigest() == (
        "51a83d5e61e34acd1184bd89550f8ef05eb82d0ac2576f49f91721e9e4bd7227"
    )
