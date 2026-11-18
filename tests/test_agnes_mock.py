import json
import logging

import httpx
import pytest

from lecturemate.ai.agnes_adapter import AgnesAdapter
from lecturemate.ai.llm_provider import LLMError
from lecturemate.ai.secrets import RedactingFilter, mask

KEY = "sk-test-SECRET-123456"
MSG = [{"role": "user", "content": "Hello"}]


def adapter(handler, stream=False, **kw):
    return AgnesAdapter("https://api.example.test/v1", KEY, "agnes-3.0-flash", stream=stream,
                        transport=httpx.MockTransport(handler), **kw)


def test_json_contract_and_usage():
    seen = {}

    def h(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"model": "agnes-3.0-flash",
                                         "choices": [{"message": {"content": " Xin chào "}}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    r = adapter(h, extra_body={"reasoning": {"enabled": False}}).complete(MSG, max_tokens=50)
    assert seen["url"] == "https://api.example.test/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["model"] == "agnes-3.0-flash" and seen["body"]["max_tokens"] == 50
    assert seen["body"]["reasoning"] == {"enabled": False}
    assert r.text == "Xin chào" and r.input_tokens == 12 and r.output_tokens == 3 and not r.usage_estimated


def test_streaming_ttft_and_usage():
    def h(req):
        body = (
            'data: {"choices":[{"delta":{"content":"Xin"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":" chào"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":2}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    r = adapter(h, stream=True).complete(MSG)
    assert r.text == "Xin chào" and r.input_tokens == 9 and r.ttft_ms is not None


def test_stream_without_usage_is_estimated():
    def h(req):
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"abc"}}]}\n\ndata: [DONE]\n\n')

    r = adapter(h, stream=True).complete(MSG)
    assert r.usage_estimated and r.output_tokens >= 1


@pytest.mark.parametrize("status,code,retryable", [
    (401, "auth", False), (403, "auth", False), (404, "bad_request", False),
    (429, "rate_limit", True), (500, "server", True), (503, "server", True), (400, "bad_request", False),
])
def test_http_errors(status, code, retryable):
    def h(req):
        return httpx.Response(status, json={"error": {"message": "x"}}, headers={"retry-after": "2"})

    with pytest.raises(LLMError) as ei:
        adapter(h).complete(MSG)
    assert ei.value.code == code and ei.value.retryable == retryable
    if status == 429:
        assert ei.value.retry_after_s == 2
    assert KEY not in str(ei.value)


def test_timeout_and_offline():
    def to(req):
        raise httpx.ReadTimeout("t", request=req)

    def off(req):
        raise httpx.ConnectError("no route", request=req)

    with pytest.raises(LLMError) as a:
        adapter(to).complete(MSG)
    with pytest.raises(LLMError) as b:
        adapter(off).complete(MSG)
    assert a.value.code == "timeout" and b.value.code == "offline"


def test_bad_json():
    def h(req):
        return httpx.Response(200, json={"nope": 1})

    with pytest.raises(LLMError) as e:
        adapter(h).complete(MSG)
    assert e.value.code == "bad_response"


def test_missing_key():
    a = AgnesAdapter("https://x.test/v1", None, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(LLMError) as e:
        a.complete(MSG)
    assert e.value.code == "auth"


def test_key_not_in_repr_and_logs_redacted(caplog):
    a = adapter(lambda r: httpx.Response(200))
    assert KEY not in repr(a)
    logger = logging.getLogger("lecturemate.test")
    logger.addFilter(RedactingFilter(lambda: KEY))
    with caplog.at_level(logging.INFO):
        logger.info("calling with %s", KEY)
        logger.info("Authorization: Bearer abc")
    assert KEY not in caplog.text and "Bearer abc" not in caplog.text
    assert mask(KEY) == "sk-…56"
