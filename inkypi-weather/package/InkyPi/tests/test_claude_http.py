"""Claude Messages API calls over the shared HTTPS session, for pages that want JSON back.

Plain HTTPS (the owner's choice, 2026-11-07) keeps the Anthropic SDK and its pydantic/httpx2
stack out of the 448 MB device's long-lived service.
"""

import json
from pathlib import Path

import pytest

from utils import claude_http

SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}}},
          "required": ["items"], "additionalProperties": False}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


def reply(stop_reason="end_turn", text='{"items": ["a"]}'):
    return {"model": "claude-opus-5-5", "stop_reason": stop_reason,
            "content": [{"type": "thinking", "thinking": "", "signature": "x"},
                        {"type": "text", "text": text[:5]}, {"type": "text", "text": text[5:]}],
            "usage": {"input_tokens": 5900, "output_tokens": 2100}}


def test_ask_json_posts_one_messages_request_with_the_schema_effort_and_fallback():
    session = FakeSession(FakeResponse(payload=reply()))

    answer = claude_http.ask_json("sk-ant-test", system="Rules", prompt="Items", schema=SCHEMA, effort="medium",
                                  timeout=90, session=session)

    call = session.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-ant-test"
    assert call["headers"]["anthropic-version"] == "2026-10-21"
    assert call["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert call["timeout"] == 90
    body = call["json"]
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000
    assert body["system"] == "Rules"
    assert body["messages"] == [{"role": "user", "content": "Items"}]
    assert body["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert body["fallbacks"] == "default"
    assert "thinking" not in body and "temperature" not in body
    assert answer.text == '{"items": ["a"]}'
    assert answer.usage == {"input": 5900, "output": 2100}
    assert answer.model == "claude-opus-5-5"


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_a_refused_or_cut_off_reply_is_an_error_not_an_answer(stop_reason):
    session = FakeSession(FakeResponse(payload=reply(stop_reason)))

    with pytest.raises(claude_http.ClaudeError, match=stop_reason):
        claude_http.ask_json("sk", system="s", prompt="p", schema=SCHEMA, effort="low", session=session)


def test_an_http_error_keeps_the_status_and_headers_without_the_key():
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "Number of requests exceeded"}}
    session = FakeSession(FakeResponse(status_code=429, payload=body, headers={"retry-after": "30"}))

    with pytest.raises(claude_http.ClaudeStatusError) as raised:
        claude_http.ask_json("sk-ant-secret", system="s", prompt="p", schema=SCHEMA, effort="low", session=session)

    assert raised.value.status_code == 429
    assert raised.value.response.headers["retry-after"] == "30"
    assert "rate_limit_error" in str(raised.value) and "sk-ant-secret" not in str(raised.value)


def test_a_reply_without_text_or_json_is_an_error():
    empty = FakeSession(FakeResponse(payload={**reply(), "content": [{"type": "thinking", "thinking": ""}]}))
    with pytest.raises(claude_http.ClaudeError):
        claude_http.ask_json("sk", system="s", prompt="p", schema=SCHEMA, effort="low", session=empty)

    garbled = FakeSession(FakeResponse(payload=None, text="<html>bad gateway</html>"))
    with pytest.raises(claude_http.ClaudeError):
        claude_http.ask_json("sk", system="s", prompt="p", schema=SCHEMA, effort="low", session=garbled)


def test_the_device_key_page_knows_the_anthropic_key():
    path = Path(__file__).resolve().parents[1] / "src" / "config" / "secret_schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    entry = next(item for item in schema["entries"] if item["canonical"] == "ANTHROPIC_API_KEY")

    assert {"Daily AI News", "Telegram Digest"} <= set(entry["features"])
    assert entry["value_type"] == "secret"
