"""Claude Messages API requests that return JSON, over the shared HTTPS session.

The AI news brief and Telegram curation ask Claude first when ANTHROPIC_API_KEY is set (the
owner's Console credit, 2026-11-07). Plain HTTPS was the owner's choice: the Anthropic SDK
would bring pydantic and httpx2 back into the 448 MB device's long-lived service, the stack
utils.openai_http removed from it the same day.

One request: a system prompt, one user message, a JSON schema the reply must follow
(``output_config.format``), the effort level, and ``fallbacks: "default"`` so a request
Claude's safety classifiers decline is re-run on Anthropic's recommended fallback model
inside the same call. Claude Opus 5.5 always thinks; effort is the only depth control.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from utils.http_client import get_http_session

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_MODEL = "claude-opus-5-5"
# Thinking counts against max_tokens; this leaves room for it under a non-streaming request.
DEFAULT_MAX_TOKENS = 16000
DEFAULT_TIMEOUT_SECONDS = 90
MAX_REPLY_BYTES = 4 * 1024 * 1024


class ClaudeError(RuntimeError):
    """Claude could not be reached, declined, or answered with something that is not a reply."""


class ClaudeStatusError(ClaudeError):
    """Claude answered with an HTTP error; ``status_code`` and ``response`` as in the SDKs."""

    def __init__(self, message: str, *, status_code: int, response: Any):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


def _error_message(response: Any) -> str:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        error = None
    if isinstance(error, dict):
        return f"{error.get('type') or 'error'}: {str(error.get('message') or '').strip()}"
    return " ".join(str(getattr(response, "text", "") or "").split())[:300]


def ask_json(api_key: str, *, system: str, prompt: str, schema: dict, effort: str,
             model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS,
             timeout: float = DEFAULT_TIMEOUT_SECONDS, session: Any = None) -> SimpleNamespace:
    """Ask once; return ``text`` (the JSON reply), ``model`` and ``usage`` {input, output}."""

    request = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        "fallbacks": "default",
    }
    response = (session if session is not None else get_http_session()).post(
        API_URL,
        json=request,
        headers={"x-api-key": api_key, "anthropic-version": API_VERSION, "anthropic-beta": FALLBACK_BETA,
                 "content-type": "application/json"},
        timeout=timeout,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    if status >= 400:
        raise ClaudeStatusError(f"HTTP {status}: {_error_message(response)}", status_code=status, response=response)
    if len(getattr(response, "content", b"") or b"") > MAX_REPLY_BYTES:
        raise ClaudeError(f"reply larger than {MAX_REPLY_BYTES} bytes")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ClaudeError("reply is not JSON") from exc
    if not isinstance(payload, dict):
        raise ClaudeError("reply is not a JSON object")
    stop_reason = payload.get("stop_reason")
    if stop_reason == "refusal":
        details = payload.get("stop_details") if isinstance(payload.get("stop_details"), dict) else {}
        raise ClaudeError(f"refusal ({details.get('category') or 'no category'})")
    if stop_reason == "max_tokens":
        raise ClaudeError(f"reply cut off at max_tokens ({max_tokens})")
    text = "".join(str(block.get("text") or "") for block in payload.get("content") or []
                   if isinstance(block, dict) and block.get("type") == "text").strip()
    if not text:
        raise ClaudeError(f"reply has no text (stop_reason {stop_reason})")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return SimpleNamespace(text=text, model=str(payload.get("model") or model),
                           usage={"input": usage.get("input_tokens"), "output": usage.get("output_tokens")})
