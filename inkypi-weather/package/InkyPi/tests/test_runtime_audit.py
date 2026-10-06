import importlib.util
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[4] / "tools" / "runtime_audit.py"
spec = importlib.util.spec_from_file_location("runtime_audit", TOOL)
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


def _config(*plugins):
    return {"plugin_cycle_interval_seconds": 300, "playlist_config": {"playlists": [{
        "name": "Default",
        "plugins": [{"plugin_id": plugin_id, "instance_uuid": f"uuid-{plugin_id}"} for plugin_id in plugins],
    }]}}


def _lane(success=None, failure=None, attempt=None, error=None):
    return {"last_success_at": success, "last_failure_at": failure, "last_attempt_at": attempt, "last_error": error}


def _state(**lanes_by_plugin):
    return {"instances": {f"uuid-{plugin}": {"lanes": lanes} for plugin, lanes in lanes_by_plugin.items()}}


def _observation(state, committed_at, commit_id, hardware_written=True, captured_at="2026-10-06T20:05:30+00:00"):
    return {"captured_at": captured_at, "runtime": state,
            "display": {"commit_id": commit_id, "committed_at": committed_at, "hardware_written": hardware_written,
                        "logical_target": {"plugin_id": "weather"}}}


def test_lane_change_classifies_progress_failure_and_idle():
    before = _lane(success="2026-10-06T20:00:00+00:00")

    assert audit.lane_change(before, _lane(success="2026-10-06T20:04:00+00:00")) == "succeeded"
    assert audit.lane_change(before, _lane(success="2026-10-06T20:00:00+00:00",
                                           failure="2026-10-06T20:03:00+00:00", error="boom")) == "failed"
    assert audit.lane_change(before, before) == "idle"
    assert audit.lane_change({}, {}) == "idle"


def test_stage_report_separates_fetch_render_and_write():
    config = _config("weather", "apod", "newspaper")
    first = _observation(_state(
        weather={"data": _lane(success="2026-10-06T19:50:00+00:00")},
        apod={"data": _lane(success="2026-10-06T19:00:00+00:00"),
              "presentation": _lane(success="2026-10-06T19:00:00+00:00")},
        newspaper={"data": _lane(success="2026-10-06T19:55:00+00:00")},
    ), "2026-10-06T20:00:00+00:00", "a", captured_at="2026-10-06T20:00:00+00:00")
    second = _observation(_state(
        weather={"data": _lane(success="2026-10-06T20:03:00+00:00")},
        apod={"data": _lane(success="2026-10-06T19:00:00+00:00", failure="2026-10-06T20:02:00+00:00",
                            error="scales provider data is stale"),
              "presentation": _lane(success="2026-10-06T20:04:00+00:00")},
        newspaper={"data": _lane(success="2026-10-06T19:55:00+00:00")},
    ), "2026-10-06T20:05:00+00:00", "b")

    report = audit.stage_report(config, first, second, journal_writes=1)

    assert report["fetch"]["succeeded"] == ["weather"]
    assert report["fetch"]["failed"] == [{"plugin_id": "apod", "error": "scales provider data is stale"}]
    assert report["fetch"]["idle"] == ["newspaper"]
    assert report["render"]["succeeded"] == ["apod"]
    assert report["write"] == {"commits_observed": True, "journal_writes": 1, "expected_at_least": 1,
                               "hardware_written": True, "last_commit_age_seconds": 30.0, "status": "ok"}
    assert report["verdict"] == "attention"


def test_write_stage_flags_a_stalled_panel():
    config = _config("weather")
    state = _state(weather={"data": _lane(success="2026-10-06T19:00:00+00:00")})
    first = _observation(state, "2026-10-06T19:40:00+00:00", "a", captured_at="2026-10-06T20:00:00+00:00")
    second = _observation(state, "2026-10-06T19:40:00+00:00", "a", captured_at="2026-10-06T20:05:30+00:00")

    report = audit.stage_report(config, first, second, journal_writes=0)

    assert report["write"]["status"] == "stalled"
    assert report["write"]["last_commit_age_seconds"] == 1530.0
    assert report["verdict"] == "stalled"


def test_journal_summary_counts_writes_failures_and_command_peaks():
    lines = [
        "12:00:01 - INFO - display.waveshare_display - Displaying image to Waveshare display.",
        "12:00:05 - ERROR - refresh_task - Refresh command failed. | source: background | plugin_id: apod",
        "12:00:09 - INFO - refresh_task - Memory maintenance completed. | plugin_id: weather | "
        "process_rss_mb: 120.0 | command_peak_mb: 171.5",
        "12:01:09 - INFO - refresh_task - Memory maintenance skipped for command. | plugin_id: weather | "
        "command_peak_mb: 160.0",
        "12:02:09 - INFO - refresh_task - Memory maintenance completed. | plugin_id: apod | command_peak_mb: None",
        "12:05:01 - INFO - display.waveshare_display - Displaying image to Waveshare display.",
    ]

    assert audit.journal_summary(lines) == {
        "writes": 2,
        "failed_commands": {"apod": 1},
        "largest_command_peaks_mb": {"weather": 171.5},
    }


def test_instances_outside_the_playlist_are_ignored():
    config = _config("weather")
    state = _state(weather={"data": _lane()}, removed={"data": _lane(failure="2026-10-06T20:01:00+00:00", error="x")})
    first = _observation(_state(weather={"data": _lane()}), "2026-10-06T20:00:00+00:00", "a",
                         captured_at="2026-10-06T20:00:00+00:00")
    second = _observation(state, "2026-10-06T20:05:00+00:00", "b")

    report = audit.stage_report(config, first, second, journal_writes=1)

    assert report["fetch"]["failed"] == []
    assert report["verdict"] == "ok"
