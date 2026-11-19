"""The lower region loads six leagues before it chooses one to draw. Each finished load is one
journal line with the worker's peak memory so far: when the parent stops a worker that grew too
large, the last line names the load it finished before the one that did it."""

import logging
import re
from datetime import datetime, timezone

from PIL import Image

import plugins.sports_dashboard.lower_panel as lower_panel
from plugins.sports_dashboard.sports_dashboard import SportsDashboard

NOW = datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc)
STEP = re.compile(r"Sports lower region step finished\. \| step: (\S+) \| peak_rss_mb: (\S+)")


def _region(monkeypatch, peaks):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    card = {"sport": "MLB", "main": {"start": NOW}}
    readings = iter(peaks)
    monkeypatch.setattr(lower_panel, "process_memory_mb", lambda: next(readings))
    monkeypatch.setattr(plugin, "_load_nba_events", lambda *_args: ([], "NBA NO DATA"))
    monkeypatch.setattr(plugin, "_load_offseason_hub", lambda *_args: ({"cards": [card]}, "HUB LIVE"))
    monkeypatch.setattr(plugin, "_load_lower_f1", lambda *_args: (None, ""))
    monkeypatch.setattr(lower_panel, "_EXTRA_LEAGUES", (
        ("UFC", lambda *_args: ({}, "UFC LIVE"), lambda _card, _now: None, "_draw_ufc_panel"),
        ("TENNIS", lambda *_args: ({}, "TENNIS LIVE"), lambda _card, _now: None, "_draw_tennis_panel"),
    ))
    monkeypatch.setattr(SportsDashboard, "_choose_lower_panel",
                        staticmethod(lambda *_args, **_kwargs: {"kind": "hub", "key": "MLB", "card": card}))
    monkeypatch.setattr(plugin, "_draw_offseason_hub_compact_panel", lambda *_args: None)
    monkeypatch.setattr(plugin, "_write_offseason_hub_state", lambda *_args: None)
    monkeypatch.setattr(plugin, "_write_nba_live_state", lambda *_args: None)
    monkeypatch.setattr(plugin, "_record_region_choice", lambda *_args: None)
    return plugin


def test_each_finished_load_and_the_choice_are_one_line_with_the_peak_so_far(monkeypatch, caplog):
    peaks = [{"rss": 70.0, "peak": value, "swap": 0.0} for value in (81.2, 96.0, 96.0, 97.5, 131.4, 131.4)]
    plugin = _region(monkeypatch, peaks)

    with caplog.at_level(logging.INFO):
        plugin._draw_lower_sports_region(Image.new("RGB", (800, 480)), None, {}, None, timezone.utc, NOW,
                                         (0, 212, 555, 479))

    assert STEP.findall(caplog.text) == [("nba", "81.2"), ("hub", "96.0"), ("f1", "96.0"), ("UFC", "97.5"),
                                         ("TENNIS", "131.4"), ("chosen:hub/MLB", "131.4")]


def test_a_platform_without_a_process_table_still_names_the_steps(monkeypatch, caplog):
    plugin = _region(monkeypatch, [None] * 6)

    with caplog.at_level(logging.INFO):
        plugin._draw_lower_sports_region(Image.new("RGB", (800, 480)), None, {}, None, timezone.utc, NOW,
                                         (0, 212, 555, 479))

    assert [step for step, _peak in STEP.findall(caplog.text)] == ["nba", "hub", "f1", "UFC", "TENNIS", "chosen:hub/MLB"]
    assert {peak for _step, peak in STEP.findall(caplog.text)} == {"unknown"}
