"""Race-grid identity, completeness and cache regressions (synthetic official-layout PDFs)."""

from __future__ import annotations

import contextlib
import copy
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import fitz
import pytest

from plugins.sports_dashboard import extra_feeds, motogp, motogp_grid as grid
from runtime.refresh_contracts import TaskCancelled

NOW = datetime(2026, 3, 29, 17, 20, tzinfo=timezone.utc)
URL = "https://resources.motogp.com/files/results/2026/USA/MotoGP/RAC/Grid.pdf"
DATA = {"season": 2026, "current": {"id": "usa-2026", "short": "USA", "start": "2026-03-27"},
        "sessions": [{"id": "usa-rac", "type": "RAC", "grid_url": URL, "start": "2026-03-29T19:00:00+00:00"}]}
# This shortened fixture retains the real 2026 USA penalty example: Bezzecchi P4,
# Marini P11. Empty positions are deliberate; we must never renumber them.
ROWS = [{"pos": 1, "number": 49, "name": "Fabio Di Giannantonio", "constructor": "Ducati"},
        {"pos": 4, "number": 72, "name": "Marco Bezzecchi", "constructor": "Aprilia"},
        {"pos": 11, "number": 10, "name": "Luca Marini", "constructor": "Honda"}]


def _json(rows=ROWS):
    return {"penalty": None, "grid": [
        {"position": row["pos"], "rider": {"id": f"rider-{row['number']}",
         "name": row["name"].split()[0], "surname": " ".join(row["name"].split()[1:]) + " ",
         "career": {"number": row["number"]}}, "team_name": "Official team"}
        for row in rows]}


def _pdf(rows=ROWS, *, title="OFFICIAL STARTING GRID", extra="", pages=1, footer=True):
    with fitz.open() as document:
        page = document.new_page()
        lines = [title, "RACE : 20 LAPS", "RED BULL GRAND PRIX OF THE UNITED STATES"]
        for row in rows:
            lines.extend([str(row["pos"]), "2'00.136", f"{row['number']} {row['name']}", row["constructor"]])
        if extra:
            lines.append(extra)
        if footer:
            lines.append("Official MotoGP Timing by")
        page.insert_text((40, 40), "\n".join(lines), fontsize=9)
        for _ in range(pages - 1):
            document.new_page()
        return document.tobytes()


def test_session_files_survive_normalization_and_target_is_explicit():
    sessions = motogp.normalize_sessions([{"id": "usa-rac", "type": "RAC", "date": NOW.isoformat(),
                                          "session_files": {"grid": {"url": URL}}}])
    assert sessions[0]["grid_url"] == URL
    assert grid.target({**DATA, "sessions": sessions})["session_id"] == "usa-rac"
    assert grid.target(DATA)["target_type"] == "RAC"


@pytest.mark.parametrize("path", ["2025/USA/MotoGP/RAC", "2026/QAT/MotoGP/RAC", "2026/USA/Moto2/RAC",
                                   "2026/USA/MotoGP/SPR", "2026/USA/MotoGP/Q2", "2026/USA/MotoGP/WUP"])
def test_wrong_event_year_category_or_ambiguous_target_is_not_requested(path):
    data = copy.deepcopy(DATA)
    data["sessions"][0]["grid_url"] = f"https://resources.motogp.com/files/results/{path}/Grid.pdf"
    assert grid.target(data) is None


def test_2027_grid_requires_its_own_race_document_and_session():
    data = copy.deepcopy(DATA)
    data["season"] = 2027
    data["sessions"][0]["grid_url"] = URL.replace("2026", "2027")
    assert grid.target(data)["season"] == 2027
    data["sessions"][0]["type"] = "SPR"
    assert grid.target(data) is None
    data["sessions"][0]["type"] = "RAC"
    data["sessions"][0]["grid_url"] = data["sessions"][0]["grid_url"].replace("/RAC/", "/Q2/")
    assert grid.target(data) is None


def test_official_document_keeps_penalty_positions_and_empty_slots():
    parsed = grid.parse_document(_pdf())
    assert parsed == ROWS
    payload = _json()
    # Simulate the older qualifying ordering in JSON; only the PDF determines grid.
    payload["grid"][1]["position"] = 2
    payload["grid"][2]["position"] = 9
    result = grid.verified_grid(payload, parsed, grid.target(DATA))
    assert [(row["pos"], row["number"]) for row in result["rows"]] == [(1, 49), (4, 72), (11, 10)]
    assert result["authority"] == "session_grid_pdf"
    assert result["json_positions_match"] is False
    assert result["complete"] is True
    assert result["rows"][1]["team"] == "Official team"


def test_extra_json_riders_cannot_reappear_in_the_authoritative_grid():
    payload = _json([*ROWS, {"pos": 22, "number": 63, "name": "Francesco Bagnaia"}])
    result = grid.verified_grid(payload, grid.parse_document(_pdf()), grid.target(DATA))
    assert [row["number"] for row in result["rows"]] == [49, 72, 10]


def test_identity_match_accepts_accents_case_and_trailing_spaces():
    rows = [{"pos": 1, "number": 73, "name": "Álex MÁRQUEZ", "constructor": "Ducati"}]
    result = grid.verified_grid(_json([{**rows[0], "name": "Alex Marquez"}]), rows, grid.target(DATA))
    assert result["rows"][0]["number"] == 73
    payload = _json(rows)
    payload["grid"][0]["rider"]["surname"] = "Different Rider"
    assert grid.verified_grid(payload, rows, grid.target(DATA)) == {}


@pytest.mark.parametrize("modify", [
    lambda p: p["grid"].pop(),
    lambda p: p["grid"].append(copy.deepcopy(p["grid"][0])),
    lambda p: p["grid"][1].update(position=1),
    lambda p: p["grid"][1].update(position=True),
    lambda p: p["grid"][1]["rider"]["career"].update(number=True),
    lambda p: p["grid"][1]["rider"].update(id="rider-49"),
])
def test_partial_duplicate_or_invalid_json_is_not_a_complete_grid(modify):
    payload = _json()
    modify(payload)
    assert grid.verified_grid(payload, ROWS, grid.target(DATA)) == {}


@pytest.mark.parametrize("kwargs", [{"title": "PROVISIONAL STARTING GRID"},
                                     {"title": "TISSOT SPRINT - STARTING GRID"}, {"pages": 2}, {"footer": False},
                                     {"extra": "5 Johann ZARCO\nUnparsed layout"}])
def test_provisional_sprint_partial_or_changed_document_fails_closed(kwargs):
    assert grid.parse_document(_pdf(**kwargs)) == []


def test_one_unparsed_rider_or_duplicate_position_rejects_the_whole_document():
    broken = [*ROWS, {"pos": 12, "number": 5, "name": "Johann Zarco", "constructor": "Unknown 2"}]
    assert grid.parse_document(_pdf(broken)) == []
    duplicate = [*ROWS, {"pos": 4, "number": 5, "name": "Johann Zarco", "constructor": "Honda"}]
    assert grid.parse_document(_pdf(duplicate)) == []
    assert grid.parse_document(b"") == []
    assert grid.parse_document(b"%PDF-" + b"x" * grid.PDF_MAX_BYTES) == []
    assert grid.parse_document(b"%PDF-invalid") == []


@pytest.mark.parametrize("extra", ["12\n2'01.000\n5\nJohann ZARCO\nHonda",
                                  "12\n5\nJohann ZARCO\nHonda", "12\n2'01.000\n5 Johann ZARCO"])
def test_split_name_or_missing_constructor_cannot_escape_both_row_counters(extra):
    assert grid.parse_document(_pdf(extra=extra)) == []


def test_an_unparsed_first_rider_is_not_skipped_when_finding_grid_body():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((40, 40), "OFFICIAL STARTING GRID\nRACE : 20 LAPS\n"
                         "1\n2'00.136\n49\nFabio DI GIANNANTONIO\nDucati\n"
                         "4\n2'00.329\n72 Marco BEZZECCHI\nAprilia\nOfficial MotoGP Timing by", fontsize=9)
        assert grid.parse_document(document.tobytes()) == []


class _Plugin:
    def __init__(self, path):
        self.path = path

    def _sports_dashboard_cache_dir(self):
        return self.path

    def _read_json_file(self, path):
        return json.loads(path.read_text()) if path.exists() else {}

    def _write_json_file(self, path, value):
        path.write_text(json.dumps(value))

    def _cache_is_fresh_seconds(self, cache, seconds, now):
        return now - datetime.fromisoformat(cache["fetched_at"]) < timedelta(seconds=seconds)

    def _force_refresh_requested(self, settings):
        return settings.get("force", False)

    def _provider_cache_only(self, settings):
        return settings.get("_theme_render_only", False)


class _Client:
    def __init__(self, payload=None, content=None):
        self.payload = _json() if payload is None else payload
        self.content = _pdf() if content is None else content
        self.calls = []
        self.error = None

    def _get(self, url, kwargs, data):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        assert 0 < kwargs["timeout"] <= 10
        return SimpleNamespace(data=data)

    def request_json(self, method, url, **kwargs):
        return self._get(url, kwargs, self.payload)

    def request_bytes(self, method, url, **kwargs):
        return self._get(url, kwargs, self.content)


def test_fetch_is_bounded_and_records_document_provenance():
    client = _Client()
    result = grid.fetch(client, time.monotonic() + 12, grid.target(DATA), NOW)["grid"]
    assert result["verified_at"] == NOW.isoformat()
    assert len(result["source_sha256"]) == 64
    assert result["source_url"] == URL
    assert client.calls[0][1]["params"] == {"season": 2026, "shortname": "USA", "category": "MotoGP"}
    assert client.calls[1][1]["max_bytes"] == 1024 * 1024
    with pytest.raises(TimeoutError):
        grid.fetch(client, time.monotonic(), grid.target(DATA))


def test_cache_only_no_document_and_empty_json_do_not_reach_unneeded_network(tmp_path, monkeypatch):
    plugin, client = _Plugin(tmp_path), _Client({"grid": []})
    monkeypatch.setattr(extra_feeds, "create_single_attempt_http_client", lambda: contextlib.nullcontext(client))
    assert grid.load(plugin, {"_theme_render_only": True}, DATA, NOW)[0] == {}
    unpublished = copy.deepcopy(DATA)
    unpublished["sessions"][0]["grid_url"] = ""
    assert grid.load(plugin, {}, unpublished, NOW) == ({}, "")
    assert client.calls == []
    assert grid.load(plugin, {}, DATA, NOW)[0] == {}
    assert len(client.calls) == 1
    cache = plugin._read_json_file(tmp_path / "extra_motogp_grid.json")
    assert cache["data"]["identity"]["event_id"] == "usa-2026"
    assert cache["data"]["grid"] == {}


def test_same_event_error_keeps_stale_grid_but_event_switch_never_leaks_it(tmp_path, monkeypatch):
    plugin, client = _Plugin(tmp_path), _Client()
    monkeypatch.setattr(extra_feeds, "create_single_attempt_http_client", lambda: contextlib.nullcontext(client))
    first, state = grid.load(plugin, {}, DATA, NOW)
    assert first["complete"] and state == "MOTOGP GRID LIVE"
    client.error = RuntimeError("offline")
    stale, state = grid.load(plugin, {}, DATA, NOW + timedelta(hours=1))
    assert state == stale["state"] == "MOTOGP GRID STALE"
    assert stale["rows"] == first["rows"]
    calls = len(client.calls)
    assert grid.load(plugin, {"_theme_render_only": True}, DATA, NOW + timedelta(hours=1))[0] == stale
    assert len(client.calls) == calls
    switched = copy.deepcopy(DATA)
    switched["current"]["id"] = "another-event"
    assert grid.load(plugin, {}, switched, NOW + timedelta(hours=1))[0] == {}
    with pytest.raises(TaskCancelled):
        client.error = TaskCancelled("cancelled")
        grid.load(plugin, {"force": True}, DATA, NOW)


def test_new_invalid_document_clears_older_grid_instead_of_preserving_it(tmp_path, monkeypatch):
    plugin, client = _Plugin(tmp_path), _Client()
    monkeypatch.setattr(extra_feeds, "create_single_attempt_http_client", lambda: contextlib.nullcontext(client))
    assert grid.load(plugin, {}, DATA, NOW)[0]["complete"]
    client.content = _pdf(extra="6 Unknown Rider\nUnparsed layout")
    assert grid.load(plugin, {}, DATA, NOW + timedelta(hours=1))[0] == {}
    client.error = RuntimeError("offline")
    assert grid.load(plugin, {"force": True}, DATA, NOW + timedelta(hours=2))[0] == {}


def test_build_card_rejects_cross_event_session_and_preserves_existing_rows():
    official = grid.verified_grid(_json(), ROWS, grid.target(DATA))
    data = {**DATA, "grid": official, "result": {"rows": [{"pos": 1}]}, "standings": [{"pos": 2}]}
    card = motogp.build_card(data, NOW)
    assert card["grid"] == official
    assert card["result"] == data["result"] and card["standings"] == data["standings"]
    for key, value in (("event_id", "different"), ("session_id", "different"), ("target_type", "SPR"), ("complete", False)):
        assert motogp.build_card({**data, "grid": {**official, key: value}}, NOW)["grid"] == {}


def test_optional_grid_error_does_not_discard_results_or_standings(tmp_path, monkeypatch):
    plugin, client = _Plugin(tmp_path), _Client()
    client.error = RuntimeError("offline")
    monkeypatch.setattr(extra_feeds, "create_single_attempt_http_client", lambda: contextlib.nullcontext(client))
    data = {**DATA, "result": {"rows": [{"pos": 1}]}, "standings": [{"pos": 2}]}
    plugin._write_json_file(tmp_path / "extra_motogp.json", {"version": motogp.VERSION, "fetched_at": NOW.isoformat(), "data": data})
    monkeypatch.setattr(motogp.motogp_riders, "rider_profiles", lambda *args: {})
    card, state = motogp.load_card(plugin, {}, NOW)
    assert card["result"] == data["result"] and card["standings"] == data["standings"]
    assert card["grid"] == {}
    assert "MOTOGP CACHE" in state and "MOTOGP GRID NO DATA" in state
