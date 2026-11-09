"""Redact credentials from exception text before it reaches logs or callers."""

from __future__ import annotations

import logging
import re


_QUERY_CREDENTIAL_RE = re.compile(
    r"(?P<prefix>[?&](?:apiKey|api_key|api-key|x-api-key|apikey|key|token|"
    r"access_token|client_id|client_secret|secret|password|authorization)=)"
    r"[^&#\s]*",
    re.IGNORECASE,
)
_BEARER_CREDENTIAL_RE = re.compile(
    r"(?P<prefix>\bBearer[ \t]+)[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)


def redact_sensitive_text(value) -> str:
    """Return useful error context without query or bearer credentials."""

    text = str(value or "")
    text = _QUERY_CREDENTIAL_RE.sub(r"\g<prefix><redacted>", text)
    return _BEARER_CREDENTIAL_RE.sub(r"\g<prefix><redacted>", text)


class RedactingFormatter(logging.Formatter):
    """The service's log formatter: every formatted line, traceback included, without credentials.

    Plugins redact their own messages, but a library logs on its own: urllib3's retry
    warning prints the full request URL, query key and all. config/logging.conf sets
    this formatter on the service's handler so no logger's line reaches the journal
    with a secret in it.
    """

    def format(self, record):
        return redact_sensitive_text(super().format(record))
