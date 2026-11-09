import json
import math

import httpx
import pytest

from matrix_server.evals.providers import (
    AnthropicProvider,
    Completion,
    FakeProvider,
    HashingEmbedder,
    LLMProvider,
    OpenAICompatibleProvider,
    OpenAIEmbedder,
    ProviderError,
    get_provider,
    register_provider,
)

SECRET = "sk-test-SUPERSECRET-1234567890abcdef"


def _openai_ok(model="gpt-4o-mini"):
    return {
        "id": "x",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello there"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
    }


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0)
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body)


def _openai(rec, **kw):
    return OpenAICompatibleProvider(transport=httpx.MockTransport(rec), sleep=lambda s: None, **kw)


def test_openai_request_shape_and_auth(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    rec = Recorder([(200, _openai_ok())])
    p = _openai(rec)
    out = p.complete([{"role": "user", "content": "hi"}], "gpt-4o-mini", {"temperature": 0})
    req = rec.requests[0]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(req.content)
    assert body == {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "temperature": 0}
    assert out.text == "hello there"
    assert (out.input_tokens, out.output_tokens) == (1000, 500)
    assert out.cost_usd > 0
    assert out.raw["model"] == "gpt-4o-mini"
    assert isinstance(p, LLMProvider)


def test_openai_custom_base_url_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rec = Recorder([(200, _openai_ok("llama3"))])
    p = _openai(rec, base_url="http://localhost:11434/v1/")
    p.complete([{"role": "user", "content": "hi"}], "llama3")
    assert str(rec.requests[0].url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in rec.requests[0].headers


def test_openai_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError) as ei:
        _openai(Recorder([])).complete([{"role": "user", "content": "x"}], "gpt-4o")
    assert "OPENAI_API_KEY" in str(ei.value)


def test_retries_on_429_and_5xx(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    sleeps = []
    rec = Recorder([(429, {"error": {"message": "slow down"}}), (503, "unavailable"), (200, _openai_ok())])
    p = OpenAICompatibleProvider(transport=httpx.MockTransport(rec), sleep=sleeps.append, backoff=0.1)
    assert p.complete([{"role": "user", "content": "x"}], "gpt-4o").text == "hello there"
    assert len(rec.requests) == 3
    assert sleeps == [0.1, 0.2]


def test_gives_up_after_two_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    rec = Recorder([(500, "boom")] * 3)
    with pytest.raises(ProviderError) as ei:
        _openai(rec).complete([{"role": "user", "content": "x"}], "gpt-4o")
    assert ei.value.status == 500
    assert len(rec.requests) == 3


def test_no_retry_on_4xx(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    rec = Recorder([(400, {"error": {"message": "bad request"}})])
    with pytest.raises(ProviderError) as ei:
        _openai(rec).complete([{"role": "user", "content": "x"}], "gpt-4o")
    assert len(rec.requests) == 1
    assert ei.value.status == 400 and "bad request" in str(ei.value)


def test_error_never_leaks_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    echo = {"error": {"message": f"Incorrect API key provided: {SECRET}. " + "x" * 500}}
    p = _openai(Recorder([(401, echo)]))
    with pytest.raises(ProviderError) as ei:
        p.complete([{"role": "user", "content": "x"}], "gpt-4o")
    exc = ei.value
    assert SECRET not in str(exc) and SECRET not in repr(exc)
    assert "SUPERSECRET" not in str(exc)
    assert len(exc.message) <= 200
    assert exc.__cause__ is None
    assert SECRET not in repr(p) and "SUPERSECRET" not in str(vars(p))


def test_network_error_sanitized(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)

    def boom(request):
        raise httpx.ConnectError(f"failed {request.headers['authorization']}")

    p = OpenAICompatibleProvider(transport=httpx.MockTransport(boom), sleep=lambda s: None)
    with pytest.raises(ProviderError) as ei:
        p.complete([{"role": "user", "content": "x"}], "gpt-4o")
    assert SECRET not in str(ei.value) and ei.value.status is None


def test_anthropic_request_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    resp = {
        "model": "claude-sonnet-4-20250514",
        "content": [{"type": "text", "text": "Hi"}, {"type": "text", "text": "!"}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100},
    }
    rec = Recorder([(200, resp)])
    p = AnthropicProvider(transport=httpx.MockTransport(rec), sleep=lambda s: None)
    out = p.complete(
        [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hello"}],
        "claude-sonnet-4",
        {"temperature": 0.2, "logprobs": True, "stop": "END"},
    )
    req = rec.requests[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == SECRET
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body["system"] == "Be brief."
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["max_tokens"] == 1024 and body["temperature"] == 0.2
    assert body["stop_sequences"] == ["END"] and "logprobs" not in body
    assert out.text == "Hi!" and out.input_tokens == 110 and out.output_tokens == 5
    assert out.cost_usd > 0


def test_anthropic_error_sanitized(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    rec = Recorder([(401, {"type": "error", "error": {"type": "auth", "message": f"invalid x-api-key {SECRET}"}})])
    p = AnthropicProvider(transport=httpx.MockTransport(rec))
    with pytest.raises(ProviderError) as ei:
        p.complete([{"role": "user", "content": "x"}], "claude-sonnet-4")
    assert SECRET not in str(ei.value)
    assert SECRET not in repr(p)


def test_fake_provider_deterministic():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "What is MATRIX?"}]
    a = FakeProvider().complete(msgs, "model-a")
    assert a == FakeProvider().complete(msgs, "model-a")
    assert a.text.endswith(": What is MATRIX?")
    assert a.output_tokens == len(a.text.split()) and a.input_tokens == 4
    outs = {FakeProvider().complete(msgs, m).text for m in ("model-a", "model-b", "model-c", "model-d")}
    assert len(outs) > 1
    assert FakeProvider(seed=1).complete(msgs, "model-a").latency_ms > 0


def test_registry():
    assert isinstance(get_provider("fake"), FakeProvider)
    assert isinstance(get_provider("OpenAI", base_url="http://x/v1"), OpenAICompatibleProvider)
    assert isinstance(get_provider("anthropic"), AnthropicProvider)
    with pytest.raises(ValueError):
        get_provider("nope")

    class Echo:
        def complete(self, messages, model, params=None):
            return Completion("echo", 0, 0, 0.0, 0.0, model)

    register_provider("echo", Echo)
    assert get_provider("echo").complete([], "m").text == "echo"


def test_openai_embedder(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    body = {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}
    rec = Recorder([(200, body)])
    e = OpenAIEmbedder(transport=httpx.MockTransport(rec))
    assert e.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    req = json.loads(rec.requests[0].content)
    assert req == {"model": "text-embedding-3-small", "input": ["a", "b"]}
    assert str(rec.requests[0].url).endswith("/v1/embeddings")
    assert SECRET not in repr(e)


def test_hashing_embedder():
    e = HashingEmbedder(dim=64)
    v1, v2, v3 = e.embed(["the cat sat", "the cat sat", ""])
    assert v1 == v2 and len(v1) == 64
    assert math.isclose(sum(x * x for x in v1), 1.0)
    assert all(x == 0 for x in v3)
