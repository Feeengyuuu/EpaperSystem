"""OpenAI-compatible Responses and Chat Completions calls over the shared HTTPS session.

The AI pages (AI 新闻摘要, paper and 海外热议 translation, Steam blurbs, Telegram 精选) each send one
JSON POST. The official SDK would load about 780 modules (pydantic, httpx, anyio) into the
long-lived service for that, about 24 MB of Python objects that never leave (measured
2026-11-07, when the device was restarting itself for memory). This client keeps the SDK's
call shape for those pages: ``OpenAI(api_key=..., base_url=..., timeout=...)``,
``client.responses.create(**request)`` with ``.output_text`` and ``.usage``, and
``client.chat.completions.create(**request)`` with ``.choices``. Requests go through the
shared session, so a bound refresh task's deadline and cancellation apply; nothing is retried.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from utils.http_client import get_http_session

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 60
MAX_REPLY_BYTES = 4 * 1024 * 1024


class APIError(RuntimeError):
    """The provider could not be reached or answered with something that is not a reply."""


class APIStatusError(APIError):
    """The provider answered with an HTTP error; ``status_code`` and ``response`` as in the SDK."""

    def __init__(self, message: str, *, status_code: int, response: Any):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


def _namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{str(key): _namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value


def _output_text(payload: dict) -> str:
    """Every output_text part of the reply's messages, joined, as the SDK's ``output_text``."""
    parts = []
    for item in payload.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    parts.append(str(content.get("text") or ""))
    return "".join(parts)


def _error_message(response: Any) -> str:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        error = None
    if isinstance(error, dict):
        details = [str(error.get(key)) for key in ("code", "type") if error.get(key)]
        return " ".join([str(error.get("message") or "").strip(), *(f"({detail})" for detail in details)]).strip()
    return " ".join(str(getattr(response, "text", "") or "").split())[:300]


class OpenAI:
    """The small part of the OpenAI SDK client the AI pages use."""

    def __init__(self, *, api_key: str, base_url: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 max_retries: int = 0, session: Any = None):
        self._api_key = api_key
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = timeout
        self._session = session
        self.responses = SimpleNamespace(create=self._create_response)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_chat_completion))

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def close(self) -> None:
        """Nothing to release: the shared session stays open for the other pages."""

    def _post(self, path: str, request: dict) -> dict:
        session = self._session if self._session is not None else get_http_session()
        response = session.post(
            f"{self._base_url}/{path}",
            json=request,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            timeout=self._timeout,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise APIStatusError(f"HTTP {status}: {_error_message(response)}", status_code=status, response=response)
        if len(getattr(response, "content", b"") or b"") > MAX_REPLY_BYTES:
            raise APIError(f"reply larger than {MAX_REPLY_BYTES} bytes")
        try:
            payload = response.json()
        except ValueError as exc:
            raise APIError("reply is not JSON") from exc
        if not isinstance(payload, dict):
            raise APIError("reply is not a JSON object")
        return payload

    def _create_response(self, **request: Any) -> SimpleNamespace:
        payload = self._post("responses", request)
        reply = _namespace(payload)
        reply.output_text = _output_text(payload)
        reply.usage = _namespace(payload.get("usage")) if isinstance(payload.get("usage"), dict) else None
        return reply

    def _create_chat_completion(self, **request: Any) -> SimpleNamespace:
        reply = _namespace(self._post("chat/completions", request))
        reply.choices = getattr(reply, "choices", None) or []
        return reply
