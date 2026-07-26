import re
import uuid
from pathlib import Path

import pytest


TEST_TMP_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "pytest-fixtures"
TMP_PATH_SLUG_MAX_CHARS = 32


@pytest.fixture
def tmp_path(request, tmp_path_factory):
    configured_root = request.config.getoption("basetemp")
    root = tmp_path_factory.getbasetemp() if configured_root else TEST_TMP_ROOT
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)
    slug = slug[-TMP_PATH_SLUG_MAX_CHARS:]
    path = root / f"{slug}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path
