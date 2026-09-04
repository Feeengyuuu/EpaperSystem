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
