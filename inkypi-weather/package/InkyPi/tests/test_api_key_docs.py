"""The API key guides stay in step with the installer's key registry."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from secret_schema import DEFAULT_SCHEMA_PATH, SecretSchema  # noqa: E402

DOCS = ROOT / "docs"


@pytest.mark.parametrize("guide", ["api_keys.md", "api_keys.zh-CN.md"])
def test_every_registry_key_is_documented(guide):
    text = (DOCS / guide).read_text(encoding="utf-8")
    keys = [entry["key"] for entry in SecretSchema.load(DEFAULT_SCHEMA_PATH).public_registry()]

    missing = [key for key in keys if f"`{key}`" not in text]

    assert keys and missing == []


@pytest.mark.parametrize("guide", ["api_keys.md", "api_keys.zh-CN.md"])
def test_guides_point_at_the_runtime_key_file(guide):
    text = (DOCS / guide).read_text(encoding="utf-8")

    assert "/etc/inkypi/inkypi.env" in text
    assert "--env-file .env" not in text
