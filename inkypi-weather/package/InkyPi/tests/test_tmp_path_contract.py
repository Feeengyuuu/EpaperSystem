import os
import subprocess
import sys
import tempfile
from pathlib import Path


def test_tmp_path_is_absolute_with_relative_basetemp(tmp_path):
    if os.environ.get("INKYPI_TMP_PATH_CONTRACT_PROBE") == "1":
        assert tmp_path.is_absolute()
        return

    with tempfile.TemporaryDirectory(prefix="inkypi-basetemp-contract-") as cwd:
        env = os.environ.copy()
        env["INKYPI_TMP_PATH_CONTRACT_PROBE"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                str(Path(__file__).resolve()),
                "-q",
                "--basetemp=relative-basetemp",
            ],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 0, result.stdout + result.stderr


def test_tmp_path_reserves_headroom_for_nested_windows_venv(tmp_path):
    if os.name != "nt":
        return

    nested_python = (
        tmp_path
        / "opt"
        / "releases"
        / "current-release"
        / "venv_inkypi"
        / "Scripts"
        / "python.exe"
    )

    assert len(str(nested_python)) <= 240
