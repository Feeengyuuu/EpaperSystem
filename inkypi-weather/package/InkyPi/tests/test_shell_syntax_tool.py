"""Behavior of the release syntax gate, exercised through its CLI."""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[4]
TOOL = ROOT / "tools" / "check_shell_syntax.py"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_invalid_second_script_fails_the_gate(tmp_path):
    good = tmp_path / "good.sh"
    bad = tmp_path / "bad.sh"
    good.write_text("true\n", encoding="utf-8")
    bad.write_text("if then\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(TOOL), str(good), str(bad)],
        capture_output=True, text=True, timeout=15,
    )

    assert result.returncode != 0
    assert "bad.sh" in result.stderr
    assert "syntax error" in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_valid_scripts_are_parsed_without_execution(tmp_path):
    script = tmp_path / "valid.sh"
    script.write_text("printf executed > marker.txt\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(TOOL), str(script)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "marker.txt").exists()
