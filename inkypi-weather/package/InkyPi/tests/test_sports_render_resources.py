"""Exercise font ownership at the same render entrypoint used by workers."""

from datetime import datetime, timezone
from types import SimpleNamespace
import weakref
import gc

import pytest

from PIL import Image

from plugins.base_plugin.render_provenance import SourceProvenance
from plugins.sports_dashboard import common
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from plugins.sports_dashboard.render_fonts import render_font, render_font_scope


def test_region_reuses_identical_fonts_and_releases_them_between_renders(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    calls = []
    original = common.get_base_ui_font

    def load(size, bold=False):
        calls.append((size, bold))
        return original(size, bold=bold)

    monkeypatch.setattr(common, "get_base_ui_font", load)
    monkeypatch.setattr(plugin, "_sports_dashboard_theme_context", lambda *args: {"mode": "day"})

    def draw(*args):
        fonts = []
        for _ in range(6):
            fonts.append(plugin._font(12, True))
            fonts.append(plugin._font(10, False))
        assert len(fonts) == 12
        return SourceProvenance.LIVE

    monkeypatch.setattr(plugin, "_render_dashboard_region", draw)
    device = SimpleNamespace(get_config=lambda key, default=None: default, get_resolution=lambda: (800, 480))
    for expected in (2, 4):
        image, _ = plugin.render_isolated_region(
            {},
            device,
            region="esports",
            now=datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        image.close()
        assert len(calls) == expected


def test_unselected_league_payload_is_released_before_pillow_drawing(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    retained = []

    class ProviderPayload:
        pass

    def cards(*args):
        unused = ProviderPayload()
        retained.append(weakref.ref(unused))
        return [
            {"league_key": "LPL", "selected": {}, "source_state": "LIVE DATA", "priority": 0},
            {"league_key": "LCK", "selected": {"raw": unused}, "source_state": "LCK LIVE DATA", "priority": 1},
        ]

    monkeypatch.setattr(plugin, "_load_lol_esports_sidebar_cards", cards)
    monkeypatch.setattr(plugin, "_attach_lpl_realtime_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin, "_write_lol_live_state", lambda *args, **kwargs: None)

    def draw(*args, **kwargs):
        assert retained[0]() is None, "Unselected provider data overlaps font/image allocations"

    monkeypatch.setattr(plugin, "_draw_lpl_sidebar", draw)
    with Image.new("RGB", (800, 480)) as image:
        plugin._draw_right_esports_region(
            image,
            {"lolEsportsSidebarOverride": "LPL"},
            None,
            timezone.utc,
            datetime(2026, 9, 10, tzinfo=timezone.utc),
            550,
        )


def test_font_sharing_never_keeps_native_font_alive_or_leaks_across_scopes():
    class Font:
        pass

    def load(*args, **kwargs):
        return Font()

    with render_font_scope():
        regular = render_font(load, 12)
        assert render_font(load, 12) is regular
        assert render_font(load, 12, True) is not regular
        reference = weakref.ref(regular)
        with pytest.raises(RuntimeError), render_font_scope():
            assert render_font(load, 12) is not regular
            raise RuntimeError("render canceled")
        assert render_font(load, 12) is regular
        del regular
        gc.collect()
        assert reference() is None
    assert render_font(load, 12) is not render_font(load, 12)


def test_valve_tracking_sees_all_candidates_but_drawing_only_owns_primary(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    refs, tracked = [], []
    primary = {"status": "LIVE"}

    class Unselected:
        pass

    monkeypatch.setattr(plugin, "_load_lol_esports_sidebar_cards", lambda *args: [])
    monkeypatch.setattr(plugin, "_load_pandascore_cs2_card", lambda *args: (primary, "PANDASCORE LIVE"))
    monkeypatch.setattr(plugin, "_read_json_file", lambda *args: {})
    monkeypatch.setattr(plugin, "_valve_esports_live_state_path", lambda: "unused")
    monkeypatch.setattr(plugin, "_valve_sidebar_candidate_phase", lambda *args: 0)

    def select(cards, now):
        extra = Unselected()
        refs.append(weakref.ref(extra))
        return {"primary": primary, "cards": [primary, {"payload": extra}]}

    monkeypatch.setattr(plugin, "_select_valve_esports", select)
    monkeypatch.setattr(
        plugin,
        "_select_right_esports_sidebar",
        lambda lol, selected, source, now: {"kind": "valve", "selected": selected, "source_state": source},
    )
    monkeypatch.setattr(
        plugin, "_write_valve_esports_live_state", lambda selected, *args: tracked.append(len(selected["cards"]))
    )

    def draw(image, left, selected, *args):
        assert tracked == [2]
        assert selected == {"primary": primary}
        assert refs[0]() is None

    monkeypatch.setattr(plugin, "_draw_valve_esports_sidebar", draw)
    with Image.new("RGB", (800, 480)) as image:
        plugin._draw_right_esports_region(
            image,
            {"valveEsportsTiOfficialEnabled": False},
            None,
            timezone.utc,
            datetime(2026, 9, 10, tzinfo=timezone.utc),
            550,
        )
