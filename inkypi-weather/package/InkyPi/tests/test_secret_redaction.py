import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.secret_redaction import redact_sensitive_text  # noqa: E402


@pytest.mark.parametrize(
    "name",
    [
        "apiKey",
        "api_key",
        "apikey",
        "key",
        "token",
        "access_token",
        "api-key",
        "x-api-key",
        "client_id",
        "client_secret",
        "secret",
        "password",
        "authorization",
    ],
)
def test_redact_sensitive_text_hides_query_credentials_and_preserves_context(name):
    marker = "credential-marker-123"
    original = (
        f"503 Server Error for url: https://api.example.test/v1/items?"
        f"{name}={marker}&steamids=7&format=json"
    )

    redacted = redact_sensitive_text(original)

    assert marker not in redacted
    assert f"{name}=<redacted>" in redacted
    assert "503 Server Error" in redacted
    assert "https://api.example.test/v1/items?" in redacted
    assert "steamids=7&format=json" in redacted
    assert redact_sensitive_text(redacted) == redacted


def test_redact_sensitive_text_hides_bearer_credentials():
    marker = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
    original = f"Authorization: Bearer {marker}; upstream rejected request"

    redacted = redact_sensitive_text(original)

    assert marker not in redacted
    assert redacted == (
        "Authorization: Bearer <redacted>; upstream rejected request"
    )
    assert redact_sensitive_text(redacted) == redacted


def test_redact_sensitive_text_leaves_non_secret_query_parameters_unchanged():
    original = "https://api.example.test/v1/items?steamids=7&format=json"

    assert redact_sensitive_text(original) == original
