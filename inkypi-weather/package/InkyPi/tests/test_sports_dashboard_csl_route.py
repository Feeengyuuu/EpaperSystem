import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_auto_football_route_prefers_active_csl_when_world_cup_is_inactive(
    monkeypatch,
):
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard.__new__(SportsDashboard)
    now = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    csl_panel = object()
    calls = []

    monkeypatch.setattr(plugin, "_worldcup_schedule_summary", lambda *_args: None)
    monkeypatch.setattr(
        plugin,
        "_load_csl_route_summary",
        lambda *_args: {
            "first_start": now - timedelta(days=120),
            "final_end": now + timedelta(days=100),
            "has_relevant_events": True,
            "selection_priority": "UPCOMING",
            "main_start": now + timedelta(hours=2),
            "next_start": now + timedelta(hours=2),
            "fetched_at": now.isoformat(),
        },
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "_load_club_route_summary",
        lambda *_args: {
            "has_relevant_events": True,
            "selection_priority": "UPCOMING",
            "main_start": now + timedelta(days=2),
            "next_start": now + timedelta(days=2),
            "fetched_at": now.isoformat(),
        },
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "_render_csl_slot",
        lambda *_args: (calls.append("csl") or csl_panel, "fresh", "CSL ESPN LIVE"),
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "_render_club_football_slot",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("active CSL route fell through to the five-league panel")
        ),
    )

    rendered = plugin._render_selected_football_panel(
        {"footballPanelMode": "auto"},
        {},
        (536, 240),
        timezone.utc,
        4,
        now,
    )

    assert calls == ["csl"]
    assert rendered == (csl_panel, "fresh", "CSL ESPN LIVE", None)


def test_auto_football_route_falls_back_to_club_for_stale_previous_season_csl():
    from plugins.sports_dashboard.csl import CSLMixin
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard.__new__(SportsDashboard)
    now = datetime(2027, 2, 1, 12, tzinfo=timezone.utc)
    stale_final = {
        "event_id": "2026-final",
        "start": datetime(2026, 11, 8, 11, 35, tzinfo=timezone.utc),
        "state": "FT",
    }
    csl_summary = CSLMixin._csl_schedule_summary(
        [stale_final],
        now,
        source_state="CSL ESPN STALE",
    )

    assert (
        plugin._select_football_panel_kind(
            "auto",
            now,
            None,
            csl_summary,
        )
        == "club"
    )


def test_auto_football_route_selects_csl_for_relevant_future_fixture():
    from plugins.sports_dashboard.csl import CSLMixin
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    now = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    future_fixture = {
        "event_id": "next",
        "start": now + timedelta(hours=2),
        "state": "TIMED",
    }
    summary = CSLMixin._csl_schedule_summary([future_fixture], now)

    assert summary["has_relevant_events"] is True
    assert (
        SportsDashboard._select_football_panel_kind(
            "auto",
            now,
            None,
            summary,
        )
        == "csl"
    )


def test_auto_football_route_selects_csl_for_relevant_recent_result():
    from plugins.sports_dashboard.csl import CSLMixin
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    now = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    recent_result = {
        "event_id": "recent",
        "start": now - timedelta(days=1),
        "state": "FT",
    }
    summary = CSLMixin._csl_schedule_summary([recent_result], now)

    assert summary["has_relevant_events"] is True
    assert (
        SportsDashboard._select_football_panel_kind(
            "auto",
            now,
            None,
            summary,
        )
        == "csl"
    )


def test_auto_football_route_keeps_provider_confirmed_long_running_live_csl():
    from plugins.sports_dashboard.csl import CSLMixin
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    now = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    long_running_live = {
        "event_id": "delayed-live",
        "start": now - timedelta(hours=3, minutes=1),
        "state": "2H",
        "provider_status_confirmed": True,
    }
    selected = CSLMixin._select_csl_event_sections(
        [long_running_live],
        now,
        source_state="CSL ESPN LIVE",
        fetched_at=now.isoformat(),
    )
    summary = CSLMixin._csl_schedule_summary(
        [long_running_live],
        now,
        source_state="CSL ESPN LIVE",
        fetched_at=now.isoformat(),
    )
    refresh_until = CSLMixin._csl_live_refresh_until(
        selected,
        now,
        "CSL ESPN LIVE",
        now.isoformat(),
    )

    assert summary["has_relevant_events"] is True
    assert (
        SportsDashboard._select_football_panel_kind(
            "auto",
            now,
            None,
            summary,
        )
        == "csl"
    )
    assert refresh_until > now


def test_auto_football_route_rejects_day_old_stale_live_confirmation():
    from plugins.sports_dashboard.csl import CSLMixin
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    now = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    stale_live = {
        "event_id": "stale-live",
        "start": now - timedelta(days=1),
        "state": "2H",
        "provider_status_confirmed": True,
    }
    fetched_at = (now - timedelta(days=1)).isoformat()
    selected = CSLMixin._select_csl_event_sections(
        [stale_live],
        now,
        source_state="CSL ESPN STALE",
        fetched_at=fetched_at,
    )
    summary = CSLMixin._csl_schedule_summary(
        [stale_live],
        now,
        source_state="CSL ESPN STALE",
        fetched_at=fetched_at,
    )
    refresh_until = CSLMixin._csl_live_refresh_until(
        selected,
        now,
        "CSL ESPN STALE",
        fetched_at,
    )

    assert summary["has_relevant_events"] is False
    assert (
        SportsDashboard._select_football_panel_kind(
            "auto",
            now,
            None,
            summary,
        )
        == "club"
    )
    assert refresh_until is None


def test_auto_csl_route_reuses_the_scoreboard_read_when_rendering(monkeypatch):
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard.__new__(SportsDashboard)
    now = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    scoreboard = {"events": [{"id": "csl-1"}]}
    event = {
        "id": "csl-1",
        "start": now + timedelta(hours=2),
        "state": "SCHEDULED",
    }
    panel = Image.new("RGB", (536, 240), (240, 240, 240))
    scoreboard_reads = []

    monkeypatch.setattr(plugin, "_worldcup_schedule_summary", lambda *_args: None)

    def load_scoreboard(*_args):
        scoreboard_reads.append("read")
        return scoreboard, "CSL ESPN LIVE", now

    monkeypatch.setattr(plugin, "_load_csl_scoreboard", load_scoreboard)
    monkeypatch.setattr(
        plugin,
        "_parse_csl_espn_events",
        lambda payload, _timezone_info: [event] if payload is scoreboard else [],
    )
    monkeypatch.setattr(
        plugin,
        "_csl_schedule_summary",
        lambda *_args, **_kwargs: {
            "active": True,
            "has_relevant_events": True,
            "first_start": now,
            "final_end": now + timedelta(days=1),
            "selection_priority": "UPCOMING",
            "main_start": now + timedelta(hours=2),
            "next_start": now + timedelta(hours=2),
            "fetched_at": now.isoformat(),
        },
    )
    monkeypatch.setattr(
        plugin,
        "_load_club_route_summary",
        lambda *_args: {
            "has_relevant_events": True,
            "selection_priority": "UPCOMING",
            "main_start": now + timedelta(days=2),
            "next_start": now + timedelta(days=2),
            "fetched_at": now.isoformat(),
        },
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "_select_csl_event_sections",
        lambda events, *_args, **_kwargs: {
            "live": [],
            "upcoming": list(events),
            "recent": [],
            "main": event,
            "presentation": {"competition": "csl"},
        },
    )
    monkeypatch.setattr(plugin, "_write_csl_live_state", lambda *_args: None)
    monkeypatch.setattr(plugin, "_render_worldcup_api_panel", lambda *_args: panel)

    rendered = plugin._render_selected_football_panel(
        {"footballPanelMode": "auto", "forceRefresh": True},
        {},
        (536, 240),
        timezone.utc,
        4,
        now,
    )

    assert scoreboard_reads == ["read"]
    assert rendered[0].tobytes() == panel.tobytes()
    assert rendered[2] == "CSL ESPN LIVE"


def test_auto_football_route_does_not_fetch_csl_during_world_cup_window(
    monkeypatch,
):
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard.__new__(SportsDashboard)
    now = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)
    world_cup_panel = object()

    monkeypatch.setattr(
        plugin,
        "_worldcup_schedule_summary",
        lambda *_args: {
            "first_start": now - timedelta(days=9),
            "final_start": now + timedelta(days=29),
            "final_end": now + timedelta(days=29, hours=3),
        },
    )
    monkeypatch.setattr(
        plugin,
        "_load_csl_route_summary",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("CSL should not be fetched while World Cup has priority")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "_load_club_route_summary",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("Club should not be fetched while World Cup has priority")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "_render_worldcup_slot",
        lambda *_args: (world_cup_panel, "fresh", "ESPN LIVE", None),
    )

    rendered = plugin._render_selected_football_panel(
        {"footballPanelMode": "auto"},
        {},
        (536, 240),
        timezone.utc,
        4,
        now,
    )

    assert rendered == (world_cup_panel, "fresh", "ESPN LIVE", None)


def test_sports_settings_expose_csl_and_default_to_auto():
    settings_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "sports_dashboard"
        / "settings.html"
    )
    html = settings_path.read_text(encoding="utf-8")

    assert '<option value="csl">Chinese Super League</option>' in html
    assert "Auto: World Cup, Chinese Super League, or club leagues" in html
    assert "pluginSettings.footballPanelMode || 'auto'" in html


def test_csl_live_refresh_uses_club_interval_and_only_runs_for_csl_routes(
    tmp_path,
):
    from plugins.sports_dashboard.csl import CSL_LIVE_STATE_VERSION
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    plugin = SportsDashboard.__new__(SportsDashboard)
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    now = datetime(2026, 7, 26, 11, 45, tzinfo=timezone.utc)
    (tmp_path / "csl_live_state.json").write_text(
        json.dumps(
            {
                "version": CSL_LIVE_STATE_VERSION,
                "has_live": True,
                "live_until": "2026-07-26T14:35:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    assert plugin.get_live_refresh_state(
        {
            "footballPanelMode": "csl",
            "clubFootballLiveRefreshIntervalSeconds": "1",
        },
        now,
    ) == {"active": True, "interval_seconds": 60}
    assert plugin.get_live_refresh_state(
        {"footballPanelMode": "club"},
        now,
    ) is None
    assert plugin.get_live_refresh_state(
        {
            "footballPanelMode": "auto",
            "clubFootballLiveRefreshEnabled": "false",
        },
        now,
    ) is None


def test_generate_image_auto_route_places_csl_in_exact_top_left_slot(
    monkeypatch,
):
    from plugins.base_plugin.render_provenance import SourceProvenance
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    class DeviceConfig:
        def get_resolution(self):
            return (800, 480)

        def get_config(self, key=None, default=None):
            values = {
                "orientation": "horizontal",
                "timezone": "America/Los_Angeles",
            }
            if key is None:
                return values
            return values.get(key, default)

    plugin = SportsDashboard({"id": "sports_dashboard"})
    route_now = datetime.now(timezone.utc)
    marker_color = (17, 93, 41)
    csl_panel = Image.new("RGB", (536, 240), marker_color)
    calls = []

    monkeypatch.setattr(plugin, "_worldcup_schedule_summary", lambda *_args: None)
    monkeypatch.setattr(
        plugin,
        "_load_csl_route_summary",
        lambda *_args: {
            "active": True,
            "has_relevant_events": True,
            "selection_priority": "UPCOMING",
            "main_start": route_now + timedelta(hours=1),
            "next_start": route_now + timedelta(hours=1),
            "fetched_at": route_now.isoformat(),
        },
    )
    monkeypatch.setattr(
        plugin,
        "_load_club_route_summary",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        plugin,
        "_render_csl_slot",
        lambda *_args: (
            calls.append("csl") or csl_panel,
            SourceProvenance.LIVE,
            "CSL ESPN LIVE",
        ),
    )
    monkeypatch.setattr(
        plugin,
        "_draw_lower_sports_region",
        lambda *_args: SourceProvenance.FRESH_CACHE,
    )

    def draw_right(image, *_args):
        ImageDraw.Draw(image).rectangle((536, 0, 799, 479), fill=(80, 80, 80))
        return SourceProvenance.FRESH_CACHE

    monkeypatch.setattr(plugin, "_draw_right_esports_region", draw_right)
    monkeypatch.setattr(
        plugin,
        "_apply_sports_region_cache",
        lambda _image, _region, _box, provenance: provenance,
    )
    monkeypatch.setattr(
        plugin,
        "_attest_sports_dashboard_image",
        lambda image, *_args, **_kwargs: image,
    )
    monkeypatch.setattr(
        plugin,
        "_worldcup_release_one_shot_window_active",
        lambda *_args: False,
    )

    image = plugin.generate_image(
        {
            "footballPanelMode": "auto",
            "worldCupLeftWidth": "536",
            "worldCupTopHeight": "240",
        },
        DeviceConfig(),
    )

    assert calls == ["csl"]
    assert image.size == (800, 480)
    assert image.getpixel((0, 0)) == marker_color
    assert image.getpixel((535, 239)) == marker_color
    assert image.getpixel((536, 0)) == (80, 80, 80)
