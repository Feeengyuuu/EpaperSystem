import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = REPO_ROOT / "tools" / "run_simulated_soak.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("run_simulated_soak", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def test_short_virtual_soak_exercises_retry_cache_and_display(tmp_path):
    tool = _load_tool()
    clock = FakeClock()

    report = tool.run_soak(
        duration_seconds=12,
        interval_seconds=1,
        clock=clock,
        sleeper=clock.sleep,
        rss_reader=lambda: 64 * 1024 * 1024,
        child_pid_reader=lambda: (),
        temp_parent=tmp_path,
    )

    tool.validate_report(report)
    assert report.iterations == 12
    assert report.retry_delays[:4] == (30.0, 60.0, 120.0, 300.0)
    assert report.cache_files <= report.cache_max_files
    assert report.cache_bytes <= report.cache_max_bytes
    assert report.display_updates == 12
    assert report.child_pids_remaining == ()


def test_soak_rejects_sustained_rss_growth(tmp_path):
    tool = _load_tool()
    clock = FakeClock()
    samples = iter(range(64, 512, 16))

    report = tool.run_soak(
        duration_seconds=12,
        interval_seconds=1,
        clock=clock,
        sleeper=clock.sleep,
        rss_reader=lambda: next(samples) * 1024 * 1024,
        child_pid_reader=lambda: (),
        temp_parent=tmp_path,
    )

    with pytest.raises(tool.SoakFailure, match="RSS"):
        tool.validate_report(report)


def test_soak_rejects_small_but_linear_rss_growth(tmp_path):
    tool = _load_tool()
    clock = FakeClock()
    samples = iter(range(64, 96))

    report = tool.run_soak(
        duration_seconds=12,
        interval_seconds=1,
        clock=clock,
        sleeper=clock.sleep,
        rss_reader=lambda: next(samples) * 1024 * 1024,
        child_pid_reader=lambda: (),
        temp_parent=tmp_path,
    )

    assert report.rss_growth_bytes < report.rss_growth_limit_bytes
    with pytest.raises(tool.SoakFailure, match="linearly"):
        tool.validate_report(report)


def test_soak_rejects_remaining_child_process(tmp_path):
    tool = _load_tool()
    clock = FakeClock()
    report = tool.run_soak(
        duration_seconds=6,
        interval_seconds=1,
        clock=clock,
        sleeper=clock.sleep,
        rss_reader=lambda: 64 * 1024 * 1024,
        child_pid_reader=lambda: (4242,),
        temp_parent=tmp_path,
    )

    with pytest.raises(tool.SoakFailure, match="child"):
        tool.validate_report(report)


def test_soak_removes_only_its_owned_temp_tree(tmp_path):
    tool = _load_tool()
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    clock = FakeClock()

    report = tool.run_soak(
        duration_seconds=6,
        interval_seconds=1,
        clock=clock,
        sleeper=clock.sleep,
        rss_reader=lambda: 64 * 1024 * 1024,
        child_pid_reader=lambda: (),
        temp_parent=tmp_path,
    )

    tool.validate_report(report)
    assert list(tmp_path.iterdir()) == [sentinel]


def test_report_validation_rejects_cache_budget_regression(tmp_path):
    tool = _load_tool()
    clock = FakeClock()
    report = tool.run_soak(
        duration_seconds=6,
        interval_seconds=1,
        clock=clock,
        sleeper=clock.sleep,
        rss_reader=lambda: 64 * 1024 * 1024,
        child_pid_reader=lambda: (),
        temp_parent=tmp_path,
    )

    over_budget = replace(report, cache_bytes=report.cache_max_bytes + 1)
    with pytest.raises(tool.SoakFailure, match="cache"):
        tool.validate_report(over_budget)
