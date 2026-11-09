import json

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import matrix_sdk as mx
from matrix_sdk import client as client_mod
from matrix_sdk import tracing


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(tracing.SessionSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing._set_provider(provider)
    yield exporter
    tracing._set_provider(None)


def test_llm_attributes(spans):
    with mx.trace_llm("chat", model="gpt-4o", provider="openai") as s:
        s.set_input("hi")
        s.set_output({"a": 1})
        s.set_messages(input=[{"role": "user", "content": "hi"}], output=[{"role": "assistant", "content": "yo"}])
        s.set_tokens(10, 5, reasoning=3)
        s.mark_first_token()
    (span,) = spans.get_finished_spans()
    a = span.attributes
    assert a["openinference.span.kind"] == "LLM"
    assert a["llm.model_name"] == "gpt-4o" and a["llm.provider"] == "openai"
    assert a["input.value"] == "hi" and a["output.mime_type"] == "application/json"
    assert a["llm.input_messages.0.message.role"] == "user"
    assert a["llm.output_messages.0.message.content"] == "yo"
    assert a["llm.token_count.prompt"] == 10 and a["llm.token_count.total"] == 15
    assert a["llm.token_count.completion_details.reasoning"] == 3
    assert [e.name for e in span.events] == ["first_token"]


def test_other_kinds_and_documents(spans):
    with mx.trace_retriever("r") as r:
        r.set_documents([{"id": "d", "content": "c", "score": 0.5}, {"content": "x"}])
    with mx.trace_tool("t"):
        pass
    with mx.trace_agent("a"):
        pass
    with mx.trace_chain("c"):
        pass
    r, t, a, c = spans.get_finished_spans()
    assert r.attributes["openinference.span.kind"] == "RETRIEVER"
    assert r.attributes["retrieval.documents.0.document.score"] == 0.5
    assert r.attributes["retrieval.documents.1.document.content"] == "x"
    assert "retrieval.documents.1.document.id" not in r.attributes
    assert t.attributes["openinference.span.kind"] == "TOOL" and t.attributes["tool.name"] == "t"
    assert a.attributes["openinference.span.kind"] == "AGENT"
    assert c.attributes["openinference.span.kind"] == "CHAIN"


def test_decorator_and_error(spans):
    @mx.trace_tool("boom")
    def boom():
        raise ValueError("bad")

    with pytest.raises(ValueError):
        boom()
    (span,) = spans.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in span.events)


def test_error_in_context(spans):
    with pytest.raises(RuntimeError), mx.trace_chain("c"):
        raise RuntimeError("x")
    (span,) = spans.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert "x" in span.status.description


def test_session_propagation(spans):
    with mx.session("s1", user_id="u1"), mx.trace_chain("outer"), mx.trace_tool("inner"):
        pass
    with mx.trace_chain("outside"):
        pass
    inner, outer, outside = spans.get_finished_spans()
    for s in (inner, outer):
        assert s.attributes["session.id"] == "s1" and s.attributes["user.id"] == "u1"
    assert "session.id" not in outside.attributes


def test_current_trace_id(spans):
    assert mx.current_trace_id() is None
    with mx.trace_chain("c"):
        tid = mx.current_trace_id()
    assert tid == format(spans.get_finished_spans()[0].context.trace_id, "032x")


def test_init_exporter_config(monkeypatch):
    monkeypatch.delenv("MATRIX_API_KEY", raising=False)
    with pytest.raises(mx.MatrixError):
        mx.init(set_global=False)
    monkeypatch.setenv("MATRIX_API_KEY", "mx_env")
    monkeypatch.setenv("MATRIX_ENDPOINT", "http://matrix.test")
    c = mx.init(set_global=False, batch=False, project_name="p", service_name="svc")
    try:
        processors = c.tracer_provider._active_span_processor._span_processors
        exporter = next(p.span_exporter for p in processors if hasattr(p, "span_exporter"))
        assert exporter._endpoint == "http://matrix.test/v1/traces"
        assert exporter._client._headers["authorization"] == "Bearer mx_env"
        assert c.tracer_provider.resource.attributes["service.name"] == "svc"
    finally:
        tracing._set_provider(None)


def test_init_batch_uses_batch_processor():
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    c = mx.init(api_key="k", set_global=False)
    try:
        procs = c.tracer_provider._active_span_processor._span_processors
        assert any(isinstance(p, BatchSpanProcessor) for p in procs)
    finally:
        tracing._set_provider(None)
        c.shutdown()


# -- HTTP client

def make_client(handler):
    return mx.MatrixClient("mx_k", "http://m.test", transport=httpx.MockTransport(handler))


def prompt_json(version, template="Hello {{name}}"):
    return {"name": "greet", "version": version, "type": "text", "template": template, "config": {"model": "m"},
            "labels": ["production"], "variables": ["name"]}


def test_score_body():
    seen = {}

    def handler(req):
        seen["req"] = req
        return httpx.Response(200, json={"id": "1"})

    make_client(handler).score("t" * 32, "quality", 0.9, span_id="s", comment="ok")
    req = seen["req"]
    assert req.method == "POST" and req.url.path == "/api/v1/scores"
    assert req.headers["authorization"] == "Bearer mx_k"
    assert json.loads(req.content) == {"trace_id": "t" * 32, "name": "quality", "value": 0.9, "source": "sdk",
                                       "span_id": "s", "comment": "ok"}


def test_prompt_compile():
    p = mx.Prompt.from_api(prompt_json(1))
    assert p.compile(name="Ana") == "Hello Ana"
    assert p.compile() == "Hello {{name}}"
    chat = mx.Prompt.from_api(prompt_json(1, [{"role": "system", "content": "You are {{ role }}"},
                                              {"role": "user", "content": "{{q}}"}]))
    assert chat.compile(role="kind", q="hi") == [{"role": "system", "content": "You are kind"},
                                                 {"role": "user", "content": "hi"}]


def test_prompt_cache_hit_and_swr(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(client_mod, "_clock", lambda: now[0])
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        return httpx.Response(200, json=prompt_json(len(calls)))

    c = make_client(handler)
    p1 = c.get_prompt("greet")
    assert p1.version == 1 and p1.config == {"model": "m"} and calls == [{"label": "production"}]
    assert c.get_prompt("greet").version == 1 and len(calls) == 1  # cache hit
    c.get_prompt("greet", version=3)
    assert calls[-1] == {"version": "3"}
    now[0] += 61
    assert c.get_prompt("greet").version == 1  # stale returned immediately
    c.wait_for_refreshes()
    assert c.get_prompt("greet").version == 3  # refreshed in background (3rd fetch)


def test_prompt_stale_on_error(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(client_mod, "_clock", lambda: now[0])
    state = {"fail": False}

    def handler(req):
        return httpx.Response(500) if state["fail"] else httpx.Response(200, json=prompt_json(1))

    c = make_client(handler)
    c.get_prompt("greet")
    state["fail"] = True
    now[0] += 1000
    assert c.get_prompt("greet").version == 1
    c.wait_for_refreshes()
    assert c.get_prompt("greet").version == 1


def test_prompt_first_fetch_error_raises():
    c = make_client(lambda req: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        c.get_prompt("nope")


def test_create_dataset_item():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["path"] = req.url.path
        return httpx.Response(201, json={"id": "i"})

    make_client(handler).create_dataset_item("d1", {"q": "x"}, expected_output="y", context=["c"])
    assert seen["path"] == "/api/v1/datasets/d1/items"
    assert seen["body"] == {"input": {"q": "x"}, "expected_output": "y", "context": ["c"]}
