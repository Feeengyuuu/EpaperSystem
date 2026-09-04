import re
import sys
import uuid
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


TEST_TMP_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "pytest-fixtures"
TMP_PATH_SLUG_MAX_CHARS = 32
WINDOWS_SAFE_PATH_CHARS = 240
NESTED_VENV_SUFFIX = Path(
    "opt/releases/current-release/venv_inkypi/Scripts/python.exe"
)


def _tmp_root_with_windows_headroom(root: Path) -> Path:
    """Keep enough room for release/venv fixtures under a nested basetemp."""

    longest_leaf = "x" * (TMP_PATH_SLUG_MAX_CHARS + 1 + 32)
    nested = root / longest_leaf / NESTED_VENV_SUFFIX
    if len(str(nested)) <= WINDOWS_SAFE_PATH_CHARS:
        return root
    return TEST_TMP_ROOT


@pytest.fixture
def tmp_path(request, tmp_path_factory):
    configured_root = request.config.getoption("basetemp")
    root = tmp_path_factory.getbasetemp() if configured_root else TEST_TMP_ROOT
    root = _tmp_root_with_windows_headroom(root)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)
    slug = slug[-TMP_PATH_SLUG_MAX_CHARS:]
    path = root / f"{slug}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path
