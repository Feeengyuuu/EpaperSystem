"""The plain-HTTPS OpenAI client the AI pages use instead of the official SDK.

The SDK pulls about 780 modules (pydantic, httpx, anyio) into the long-lived service, about
24 MB of Python objects measured 2026-11-07, for one JSON POST per call.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import openai_http


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


RESPONSES_REPLY = {
    "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": '{"papers": '},
                                        {"type": "refusal", "refusal": "no"},
                                        {"type": "output_text", "text": "[]}"}]},
    ],
    "usage": {"input_tokens": 120, "output_tokens": 40, "output_tokens_details": {"reasoning_tokens": 16}},
}


def test_responses_create_posts_the_request_and_reads_the_output_text_and_usage():
    session = FakeSession(FakeResponse(payload=RESPONSES_REPLY))
    client = openai_http.OpenAI(api_key="sk-test", timeout=30, max_retries=0, session=session)

    reply = client.responses.create(model="gpt-6-luna", input="hi", text={"format": {"type": "json_schema"}})

    call = session.calls[0]
    assert call["url"] == "https://api.openai.com/v1/responses"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["json"] == {"model": "gpt-6-luna", "input": "hi", "text": {"format": {"type": "json_schema"}}}
    assert call["timeout"] == 30
    assert reply.output_text == '{"papers": []}'
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (120, 40)
    assert reply.usage.output_tokens_details.reasoning_tokens == 16


def test_chat_completions_use_the_given_base_url_and_expose_choices():
    payload = {"choices": [{"finish_reason": "stop", "message": {"content": "{}", "refusal": None}}]}
    session = FakeSession(FakeResponse(payload=payload))

    with openai_http.OpenAI(api_key="gsk", base_url="https://api.groq.com/openai/v1/", timeout=20,
                            session=session) as client:
        reply = client.chat.completions.create(model="openai/gpt-oss-120b", messages=[], reasoning_effort="low")

    assert session.calls[0]["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert session.calls[0]["json"]["reasoning_effort"] == "low"
    choice = reply.choices[0]
    assert (choice.finish_reason, choice.message.content, choice.message.refusal) == ("stop", "{}", None)


def test_an_http_error_carries_status_headers_and_the_api_message():
    body = {"error": {"message": "The model `gpt-9` does not exist", "code": "model_not_found"}}
    session = FakeSession(FakeResponse(status_code=404, payload=body, headers={"retry-after": "120"}))
    client = openai_http.OpenAI(api_key="sk", session=session)

    with pytest.raises(openai_http.APIStatusError) as raised:
        client.responses.create(model="gpt-9", input="x")

    error = raised.value
    assert error.status_code == 404
    assert error.response.headers["retry-after"] == "120"
    assert "model_not_found" in str(error) and "does not exist" in str(error)
    assert "sk" not in str(error).split("model_not_found")[0].split()


def test_a_reply_that_is_not_json_is_an_error_not_an_empty_answer():
    session = FakeSession(FakeResponse(payload=None, text="<html>bad gateway</html>"))
    client = openai_http.OpenAI(api_key="sk", session=session)

    with pytest.raises(openai_http.APIError):
        client.chat.completions.create(model="m", messages=[])


def test_an_oversized_reply_is_refused():
    big = FakeResponse(payload={"output": []})
    big.content = b"x" * (openai_http.MAX_REPLY_BYTES + 1)
    client = openai_http.OpenAI(api_key="sk", session=FakeSession(big))

    with pytest.raises(openai_http.APIError):
        client.responses.create(model="m", input="x")


def test_the_ai_pages_never_load_the_openai_sdk():
    """Importing and calling every AI page path in a fresh interpreter leaves openai and pydantic unloaded."""
    src = Path(__file__).resolve().parents[1] / "src"
    script = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(src)!r})
        from types import SimpleNamespace
        from utils import openai_http

        reply = {{"output": [{{"type": "message", "content": [{{"type": "output_text", "text": "{{}}"}}]}}],
                  "choices": [{{"finish_reason": "stop", "message": {{"content": "{{}}", "refusal": None}}}}]}}

        class Session:
            def post(self, url, **kwargs):
                return SimpleNamespace(status_code=200, headers={{}}, content=json.dumps(reply).encode(),
                                       text=json.dumps(reply), json=lambda: reply)

        openai_http.get_http_session = lambda: Session()
        from plugins.daily_ai_news import news_brief
        from plugins.ai_papers import translate
        from plugins.steam_daily_art import blurb_ai
        from plugins.telegram_digest import curation
        translate.translate([{{"id": "1", "title": "t", "summary": "s"}}], "sk")
        try:
            blurb_ai.request_lines({{"1": {{"appid": "1", "description": "d", "kind": "game"}}}}, "gsk")
        except Exception:
            pass
        print(json.dumps(sorted(name for name in ("openai", "pydantic", "httpx", "anyio") if name in sys.modules)))
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_telegram_curation_builds_the_plain_client(monkeypatch):
    from plugins.telegram_digest import curation

    made = []
    monkeypatch.setattr(openai_http, "OpenAI", lambda **kwargs: made.append(kwargs) or SimpleNamespace())

    curation.Curator._client(SimpleNamespace(ai_client_factory=None, api_key="sk-x"))

    assert made == [{"api_key": "sk-x", "timeout": curation.AI_TIMEOUT_SECONDS, "max_retries": 0}]


def test_paper_translation_builds_the_plain_client(monkeypatch):
    from plugins.ai_papers import translate

    made = []

    class Client:
        def __init__(self, **kwargs):
            made.append(kwargs)
            self.responses = SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(output_text='{"papers": []}'))

    monkeypatch.setattr(openai_http, "OpenAI", Client)

    assert translate.translate([{"id": "1", "title": "t", "summary": "s"}], "sk-y") == []
    assert made == [{"api_key": "sk-y", "timeout": translate.TIMEOUT_SECONDS, "max_retries": 0}]
