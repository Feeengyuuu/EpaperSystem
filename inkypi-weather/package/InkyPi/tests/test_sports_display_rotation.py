"""Which Sports Dashboard render reached the panel, and what each region showed."""

from datetime import datetime, timedelta, timezone

from plugins.sports_dashboard import display_rotation as rotation
from plugins.sports_dashboard.sports_dashboard import SportsDashboard

T0 = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)


def _render(state, render_at, keys_by_region, finished_at):
    for offset, region in enumerate(rotation.REGIONS):
        state = rotation.with_render(state, region, render_at, keys_by_region[region],
                                     recorded_at=finished_at - timedelta(seconds=40 * (2 - offset)))
    return state


def _settings(*displays):
    return {rotation.RECENT_DISPLAYS_SETTING: [stamp.isoformat() for stamp in displays]}


def test_a_display_shows_the_newest_render_finished_before_it():
    state = _render({}, T0, {"football": ["PL"], "lower": ["NFL"], "esports": ["LPL"]}, T0 + timedelta(minutes=2))
    state = _render(state, T0 + timedelta(minutes=15), {"football": ["BL1"], "lower": ["MLB"], "esports": ["CS"]},
                    T0 + timedelta(minutes=17))

    # Shown at 18:16 the second render was still running, so the first was on the panel.
    assert rotation.recent_shown(state, _settings(T0 + timedelta(minutes=16)), "lower") == [("NFL",)]
    assert rotation.recent_shown(state, _settings(T0 + timedelta(minutes=30)), "esports") == [("CS",)]


def test_an_incomplete_render_is_never_credited_with_a_display():
    state = _render({}, T0, {"football": ["PL"], "lower": ["NFL"], "esports": ["LPL"]}, T0 + timedelta(minutes=2))
    # Only two regions of the newer render finished (the third failed).
    state = rotation.with_render(state, "esports", T0 + timedelta(minutes=15), ["CS"],
                                 recorded_at=T0 + timedelta(minutes=15, seconds=20))
    state = rotation.with_render(state, "football", T0 + timedelta(minutes=15), ["BL1"],
                                 recorded_at=T0 + timedelta(minutes=15, seconds=50))

    assert rotation.recent_shown(state, _settings(T0 + timedelta(minutes=40)), "esports") == [("LPL",)]


def test_the_last_two_displays_are_reported_newest_first():
    state = _render({}, T0, {"football": ["PL"], "lower": ["NFL"], "esports": ["LPL"]}, T0 + timedelta(minutes=2))
    state = _render(state, T0 + timedelta(hours=2), {"football": ["SA"], "lower": ["WALL"], "esports": ["LCK"]},
                    T0 + timedelta(hours=2, minutes=2))
    settings = _settings(T0 + timedelta(minutes=30), T0 + timedelta(hours=2, minutes=30), T0 - timedelta(hours=3))

    assert rotation.recent_shown(state, settings, "lower") == [("WALL",), ("NFL",)]
    # Without display times there is nothing to be fair about.
    assert rotation.recent_shown(state, {}, "lower") == []


def test_old_renders_are_pruned():
    state = _render({}, T0, {"football": ["PL"], "lower": ["NFL"], "esports": ["LPL"]}, T0 + timedelta(minutes=2))
    later = T0 + rotation.RENDER_RETENTION + timedelta(hours=1)
    state = rotation.with_render(state, "lower", later, ["MLB"], recorded_at=later)

    assert [entry["keys"] for entry in state["renders"]["lower"]] == [["MLB"]]
    assert state["renders"]["football"] == []


def test_the_plugin_records_and_reads_back_its_choices(tmp_path):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    for region, key in (("esports", "LPL"), ("football", "PL"), ("lower", "NFL")):
        plugin._record_region_choice(region, T0, [key])

    settings = _settings(datetime.now(timezone.utc) + timedelta(minutes=1))
    assert plugin._region_recent_shown(settings, "lower") == [("NFL",)]


def test_an_unusable_cache_directory_never_breaks_a_render():
    plugin = SportsDashboard.__new__(SportsDashboard)  # no config, so no cache directory

    plugin._record_region_choice("lower", T0, ["NFL"])
    # The display is known but nothing can be credited to it.
    assert plugin._region_recent_shown(_settings(T0), "lower") == [()]
