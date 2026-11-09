import io
import logging
import logging.config
import sys
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from utils.secret_redaction import RedactingFormatter, redact_sensitive_text  # noqa: E402


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


def _urllib3_retry_warning(marker):
    """urllib3's own retry warning: the retried request's full URL, its query key and all."""

    return logging.LogRecord(
        "urllib3.connectionpool", logging.WARNING, __file__, 1,
        "Retrying (%r) after connection broken by '%r': %s",
        ("Retry(total=0)", "NameResolutionError()",
         f"/ISteamUser/GetPlayerSummaries/v2/?key={marker}&format=json&steamids=7"),
        None,
    )


def test_the_redacting_formatter_hides_credentials_in_messages_args_and_tracebacks():
    marker = "credential-marker-123"
    formatter = RedactingFormatter("%(levelname)s - %(name)s - %(message)s")

    line = formatter.format(_urllib3_retry_warning(marker))

    assert marker not in line
    assert line.startswith("WARNING - urllib3.connectionpool - Retrying")
    assert "/ISteamUser/GetPlayerSummaries/v2/?key=<redacted>&format=json&steamids=7" in line

    try:
        raise RuntimeError(f"401 for url: https://api.example.test/x?api_key={marker}")
    except RuntimeError:
        record = logging.LogRecord("plugins.apod", logging.ERROR, __file__, 1, "fetch failed", (),
                                   sys.exc_info())
    line = formatter.format(record)
    assert "Traceback" in line and "api_key=<redacted>" in line
    assert marker not in line


def test_the_service_log_config_redacts_every_loggers_lines():
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        logging.config.fileConfig(SRC / "config" / "logging.conf", disable_existing_loggers=False)
        stream = io.StringIO()
        for handler in root.handlers:
            assert isinstance(handler.formatter, RedactingFormatter)
            handler.setStream(stream)
        record = _urllib3_retry_warning("credential-marker-123")
        logging.getLogger(record.name).handle(record)
    finally:
        for handler in root.handlers:
            if handler not in saved[0]:
                handler.close()
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])

    assert "credential-marker-123" not in stream.getvalue()
    assert "key=<redacted>&format=json" in stream.getvalue()
