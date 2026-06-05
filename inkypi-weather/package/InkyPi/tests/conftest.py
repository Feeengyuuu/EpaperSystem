import re
import uuid
from pathlib import Path

import pytest


TEST_TMP_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "pytest-fixtures"


@pytest.fixture
def tmp_path(request):
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)
    slug = slug[-80:] if len(slug) > 80 else slug
    path = TEST_TMP_ROOT / f"{slug}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path
