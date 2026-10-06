"""The CI gate rejects actual boundary regressions, not a stored source snapshot."""

import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "architecture_gate", Path(__file__).resolve().parents[4] / "tools/check_architecture.py",
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_domain_rejects_a_provider_dependency_and_wildcard():
    assert gate.check_source("from .common import *\n", "plugins/sports_dashboard/f1_domain.py")
    assert gate.check_source("import requests\n", "plugins/sports_dashboard/f1_domain.py")
    assert not gate.check_source("from datetime import datetime\n", "plugins/sports_dashboard/f1_domain.py")
    assert gate.check_source("from PIL import Image\n", "plugins/stocktracker/trend_chart.py")
    assert not gate.check_source("from collections.abc import Sequence\n", "plugins/stocktracker/trend_chart.py")


def test_scheduler_planning_rejects_device_access_and_runtime_model_import():
    assert gate.check_source("from refresh_task import RefreshTask\n", "runtime/refresh_planning.py")
    assert gate.check_source("from model import PluginInstanceSnapshot\n", "runtime/refresh_planning.py")
    assert not gate.check_source(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from model import PluginInstanceSnapshot\n",
        "runtime/refresh_planning.py",
    )


def test_canonical_import_gate_detects_duplicate_module_identity_in_any_file():
    assert gate.check_source("from src.runtime.runtime_state import RefreshLane\n", "some_test.py")
    assert not gate.check_source("from runtime.runtime_state import RefreshLane\n", "some_test.py")


def test_gate_rejects_an_extracted_function_growing_back_into_a_coordinator():
    source = "def oversized():\n" + "    x = 1\n" * 85
    assert gate.check_source(source, "runtime/refresh_planning.py")


def test_gate_keeps_queue_entry_orchestration_small():
    oversized = "class RefreshTask:\n    def _execute_queue_entry(self):\n" + "        x = 1\n" * 125
    allowed = "class RefreshTask:\n    def _execute_queue_entry(self):\n" + "        x = 1\n" * 100
    assert gate.check_source(oversized, "refresh_task.py")
    assert not gate.check_source(allowed, "refresh_task.py")


def test_cancellation_swallow_count_ignores_guarded_and_reraising_handlers():
    guarded = (
        "try:\n    fetch()\nexcept TaskCancelled:\n    raise\n"
        "except Exception:\n    pass\n"
    )
    reraised = "try:\n    fetch()\nexcept Exception:\n    cleanup()\n    raise\n"
    narrow = "try:\n    fetch()\nexcept (OSError, ValueError):\n    pass\n"
    assert gate.cancellation_swallowing_handlers(guarded) == 0
    assert gate.cancellation_swallowing_handlers(reraised) == 0
    assert gate.cancellation_swallowing_handlers(narrow) == 0


def test_cancellation_swallow_count_flags_handlers_that_catch_task_cancelled():
    source = (
        "try:\n    fetch()\nexcept Exception:\n    pass\n"
        "try:\n    fetch()\nexcept RuntimeError as error:\n    log(error)\n"
        "try:\n    fetch()\nexcept:\n    pass\n"
        "try:\n    fetch()\nexcept (ValueError, BaseException):\n    pass\n"
    )
    assert gate.cancellation_swallowing_handlers(source) == 4


def test_plugin_cancellation_swallowing_cannot_grow(tmp_path):
    plugin = tmp_path / "plugins" / "example"
    plugin.mkdir(parents=True)
    (plugin / "example.py").write_text("try:\n    x()\nexcept Exception:\n    pass\n", encoding="utf-8")
    (tmp_path / "plugins" / "base_plugin").mkdir()
    (tmp_path / "plugins" / "base_plugin" / "base.py").write_text(
        "try:\n    x()\nexcept Exception:\n    pass\n", encoding="utf-8",
    )

    assert gate.plugin_cancellation_swallowing(tmp_path) == 1
    assert gate.check_cancellation_ratchet(tmp_path, ceiling=1) == []
    assert gate.check_cancellation_ratchet(tmp_path, ceiling=0)
