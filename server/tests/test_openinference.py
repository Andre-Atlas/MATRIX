import json

import pytest
from otlp_payloads import (
    AGENT_ID,
    AGENT_TRACE_ID,
    ERROR_ID,
    GENAI_ID,
    IMAGE_URL,
    LLM1_ID,
    LLM2_ID,
    RETRIEVER_ID,
    T0,
    TOOL_ID,
    export_request,
    full_request,
    pb_span,
    to_otlp_json,
)

from matrix_server.domain.spans import SpanKind
from matrix_server.ingest.openinference import TRUNCATION_MARK, normalize
from matrix_server.ingest.otlp import decode_request
from matrix_server.pricing import compute_cost

MAX = 100_000


def _normalized(request=None, max_chars=MAX, project_id="p1"):
    request = request or full_request()
    raw = decode_request(request.SerializeToString(), "application/x-protobuf")
    return {s.span_id: s for s in (normalize(r, project_id, max_chars) for r in raw)}


@pytest.fixture(scope="module")
def spans():
    return _normalized()


def test_agent_root(spans):
    agent = spans[AGENT_ID]
    assert agent.kind is SpanKind.AGENT
    assert agent.trace_id == AGENT_TRACE_ID
    assert agent.parent_span_id is None
    assert agent.project_id == "p1"
    assert agent.start_time == T0
    assert agent.duration_ms == 5000
    assert agent.status == "ok"
    assert agent.input == "Where is my order 42?"
    assert agent.output == "Your order ships tomorrow."
    assert agent.session_id == "sess-1"
    assert agent.user_id == "user-1"
    assert agent.tags == ["prod", "support"]
    assert agent.attributes["metadata"] == {"tenant": "acme", "tier": 2}
    assert agent.attributes["input.mime_type"] == "text/plain"
    assert "input.value" not in agent.attributes and "output.value" not in agent.attributes
    assert agent.cost_usd == 0


def test_llm_span_with_messages_tokens_and_image(spans):
    llm = spans[LLM1_ID]
    assert llm.kind is SpanKind.LLM
    assert llm.model == "gpt-4o"
    assert llm.provider == "openai"
    assert llm.invocation_parameters == {"temperature": 0.2, "max_tokens": 256}
    assert "llm.invocation_parameters" not in llm.attributes
    assert (llm.input_tokens, llm.output_tokens, llm.reasoning_tokens) == (120, 30, 5)
    assert (llm.cache_read_tokens, llm.cache_write_tokens) == (20, 10)
    assert llm.cost_usd == compute_cost("gpt-4o", 120, 30, 20, 10)
    assert llm.cost_usd > 0
    assert llm.ttft_ms == 250
    assert [(m.role, m.content) for m in llm.input_messages] == [
        ("system", "You are a support agent."),
        ("user", "Where is my order 42?"),
    ]
    (out,) = llm.output_messages
    assert out.role == "assistant"
    assert [(tc.name, tc.arguments) for tc in out.tool_calls] == [("lookup_order", '{"order_id": 42}')]
    assert [(m.kind, m.url, m.mime) for m in llm.media] == [("image", IMAGE_URL, "image/png")]
    assert not any(k.startswith(("llm.input_messages", "llm.output_messages")) for k in llm.attributes)
    assert llm.attributes["llm.token_count.total"] == 150


def test_tool_span(spans):
    tool = spans[TOOL_ID]
    assert tool.kind is SpanKind.TOOL
    assert tool.tool is not None
    assert tool.tool.name == "lookup_order"
    assert tool.tool.description == "Look up an order by id"
    assert json.loads(tool.tool.parameters)["type"] == "object"
    assert tool.input == '{"order_id": 42}'
    assert tool.output == '{"status": "shipped"}'
    assert tool.cost_usd == 0


def test_retriever_documents(spans):
    ret = spans[RETRIEVER_ID]
    assert ret.kind is SpanKind.RETRIEVER
    assert [(d.id, d.content, d.score) for d in ret.documents] == [
        ("doc-a", "Orders ship in 24h.", 0.92),
        ("doc-b", "Returns take 7 days.", 0.55),
        ("7", "FAQ", 0.1),
    ]
    assert ret.documents[1].metadata == {"source": "kb/1.md"}
    assert not any(k.startswith("retrieval.documents") for k in ret.attributes)


def test_second_llm_and_image_url_attribute(spans):
    llm = spans[LLM2_ID]
    assert llm.model == "gpt-4o-mini"
    assert llm.cost_usd == compute_cost("gpt-4o-mini", 300, 40)
    assert llm.output_messages[0].content == "Your order ships tomorrow."
    assert [(m.kind, m.url, m.mime) for m in llm.media] == [
        ("image", "https://example.com/chart.png", "image/png")
    ]
    assert llm.ttft_ms is None


def test_genai_fallback(spans):
    span = spans[GENAI_ID]
    assert span.kind is SpanKind.LLM  # root with a model attribute
    assert span.model == "claude-sonnet-4-5"
    assert span.provider == "anthropic"
    assert (span.input_tokens, span.output_tokens) == (1000, 200)
    assert span.cost_usd == compute_cost("claude-sonnet-4-5", 1000, 200)
    assert span.invocation_parameters == {"temperature": 0.5}
    assert [(m.role, m.content) for m in span.input_messages] == [("user", "Hello")]
    assert [(m.role, m.content) for m in span.output_messages] == [("assistant", "Hi there")]
    assert span.ttft_ms == 120.5


def test_error_span(spans):
    span = spans[ERROR_ID]
    assert span.status == "error"
    assert span.status_message == "pipeline failed"
    assert span.kind is SpanKind.CHAIN  # root without model
    assert span.events[0].name == "exception"
    assert span.events[0].attributes["exception.type"] == "ValueError"
    assert span.session_id == "sess-2"


@pytest.mark.parametrize(
    ("attrs", "parent", "expected"),
    [
        ({}, None, SpanKind.CHAIN),
        ({}, AGENT_ID, SpanKind.UNKNOWN),
        ({"gen_ai.request.model": "gpt-4o"}, AGENT_ID, SpanKind.LLM),
        ({"openinference.span.kind": "reranker"}, None, SpanKind.RERANKER),
        ({"openinference.span.kind": "weird"}, None, SpanKind.UNKNOWN),
        ({"gen_ai.operation.name": "embeddings", "gen_ai.request.model": "x"}, None, SpanKind.EMBEDDING),
        ({"gen_ai.operation.name": "execute_tool"}, AGENT_ID, SpanKind.TOOL),
    ],
)
def test_kind_defaults(attrs, parent, expected):
    request = export_request([pb_span(AGENT_TRACE_ID, LLM1_ID, "x", 0, 1, attrs, parent=parent)])
    assert _normalized(request)[LLM1_ID].kind is expected


@pytest.mark.parametrize(("code", "expected"), [(0, "unset"), (1, "ok"), (2, "error")])
def test_status_mapping(code, expected):
    request = export_request([pb_span(AGENT_TRACE_ID, AGENT_ID, "x", 0, 1, {}, status_code=code)])
    assert _normalized(request)[AGENT_ID].status == expected


def test_error_message_from_exception_event():
    span = pb_span(
        AGENT_TRACE_ID,
        AGENT_ID,
        "x",
        0,
        1,
        {},
        status_code=2,
        events=[("exception", 0.5, {"exception.message": "kaput"})],
    )
    assert _normalized(export_request([span]))[AGENT_ID].status_message == "kaput"


@pytest.mark.parametrize("event_name", ["first_token", "gen_ai.first_token", "llm.first_token"])
def test_ttft_event_names(event_name):
    span = pb_span(
        AGENT_TRACE_ID, AGENT_ID, "x", 0, 1000, {"llm.ttft_ms": 999}, events=[(event_name, 42, {})]
    )
    assert _normalized(export_request([span]))[AGENT_ID].ttft_ms == 42


def test_reranker_and_genai_json_messages():
    attrs = {
        "openinference.span.kind": "RERANKER",
        "reranker.output_documents.0.document.content": "best",
        "reranker.output_documents.0.document.score": 0.9,
        "reranker.output_documents.0.document.metadata": "not-json",
        "gen_ai.input.messages": json.dumps(
            [
                {
                    "role": "user",
                    "parts": [{"type": "text", "content": "hi"}, {"type": "text", "content": "there"}],
                }
            ]
        ),
        "gen_ai.output.messages": json.dumps(
            [{"role": "assistant", "parts": [{"type": "tool_call", "name": "f", "arguments": {"a": 1}}]}]
        ),
        "tag.tags": json.dumps(["a", "b", "a"]),
    }
    span = _normalized(export_request([pb_span(AGENT_TRACE_ID, AGENT_ID, "r", 0, 1, attrs)]))[AGENT_ID]
    assert [(d.id, d.content, d.score, d.metadata) for d in span.documents] == [
        (None, "best", 0.9, {"value": "not-json"})
    ]
    assert [(m.role, m.content) for m in span.input_messages] == [("user", "hi\nthere")]
    assert span.output_messages[0].tool_calls[0].name == "f"
    assert json.loads(span.output_messages[0].tool_calls[0].arguments) == {"a": 1}
    assert span.tags == ["a", "b"]
    assert "gen_ai.input.messages" not in span.attributes


def test_truncation_of_fields_attributes_and_events():
    long = "x" * 50
    attrs = {
        "openinference.span.kind": "LLM",
        "llm.model_name": "gpt-4o",
        "input.value": long,
        "output.value": long,
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": long,
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.tool_calls.0.tool_call.function.name": "f",
        "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments": long,
        "retrieval.documents.0.document.content": long,
        "retrieval.documents.0.document.metadata": json.dumps({"k": long}),
        "llm.invocation_parameters": json.dumps({"stop": [long]}),
        "custom.attr": long,
        "custom.list": [long, "ok"],
        "metadata": json.dumps({"nested": {"v": long}}),
        "image.url": "https://example.com/" + long,
        "session.id": long,
    }
    span = pb_span(
        AGENT_TRACE_ID,
        AGENT_ID,
        long,
        0,
        1,
        attrs,
        events=[("log", 0.5, {"msg": long})],
        status_code=2,
        status_message=long,
    )
    s = _normalized(export_request([span]), max_chars=10)[AGENT_ID]
    cut = "x" * 10 + TRUNCATION_MARK
    assert s.name == cut
    assert s.input == cut and s.output == cut
    assert s.status_message == cut
    assert s.session_id == cut
    assert s.input_messages[0].content == cut
    assert s.output_messages[0].tool_calls[0].arguments == cut
    assert s.documents[0].content == cut
    assert s.documents[0].metadata == {"k": cut}
    assert s.invocation_parameters == {"stop": [cut]}
    assert s.attributes["custom.attr"] == cut
    assert s.attributes["custom.list"] == [cut, "ok"]
    assert s.attributes["metadata"] == {"nested": {"v": cut}}
    assert s.events[0].attributes["msg"] == cut
    # a truncated URL is useless: oversized media is dropped and counted
    assert s.media == []
    assert s.attributes["matrix.media_dropped"] == 1


def test_json_and_protobuf_normalize_identically():
    request = full_request()
    via_pb = [
        normalize(r, "p", MAX) for r in decode_request(request.SerializeToString(), "application/x-protobuf")
    ]
    via_json = [normalize(r, "p", MAX) for r in decode_request(to_otlp_json(request), "application/json")]
    assert via_pb == via_json


def test_project_id_not_serialized(spans):
    dumped = spans[AGENT_ID].model_dump(mode="json")
    assert "project_id" not in dumped
    assert dumped["start_time"] == "2026-11-09T12:00:00.000000Z"


def _one(attrs, *, parent=None):
    request = export_request([pb_span(AGENT_TRACE_ID, LLM1_ID, "s", 0, 1, attrs, parent=parent)])
    return _normalized(request)[LLM1_ID]


@pytest.mark.parametrize(
    "read_key,write_key",
    [
        ("gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_creation.input_tokens"),
        ("gen_ai.usage.cache_read_input_tokens", "gen_ai.usage.cache_creation_input_tokens"),
        ("llm.token_count.prompt_details.cache_read", "llm.token_count.prompt_details.cache_write"),
    ],
)
def test_cache_token_names_and_cost(read_key, write_key):
    span = _one(
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "claude-sonnet-4",
            "gen_ai.usage.input_tokens": 1000,
            "gen_ai.usage.output_tokens": 10,
            read_key: 200,
            write_key: 300,
        }
    )
    assert (span.cache_read_tokens, span.cache_write_tokens) == (200, 300)
    # cache writes are part of input_tokens: billed once at the cache-write price
    assert span.cost_usd == pytest.approx((500 * 3 + 200 * 0.3 + 300 * 3.75 + 10 * 15) / 1_000_000)


def test_unknown_model_flagged_and_free():
    span = _one({"openinference.span.kind": "LLM", "llm.model_name": "o3-pro", "llm.token_count.prompt": 10})
    assert span.cost_usd == 0.0
    assert span.attributes["matrix.cost_unknown_model"] is True
    known = _one({"openinference.span.kind": "LLM", "llm.model_name": "o3", "llm.token_count.prompt": 10})
    assert "matrix.cost_unknown_model" not in known.attributes and known.cost_usd > 0


def test_embedding_vectors_removed_and_kind_inferred():
    span = _one(
        {
            "embedding.model_name": "text-embedding-3-small",
            "embedding.embeddings.0.embedding.text": "hello",
            "embedding.embeddings.0.embedding.vector": [0.1] * 1536,
            "embedding.embeddings.1.embedding.vector": [0.2] * 1536,
            "llm.token_count.prompt": 1000,
        },
        parent=AGENT_ID,
    )
    assert span.kind == SpanKind.EMBEDDING
    assert span.attributes["embedding.embeddings.0.embedding.text"] == "hello"
    assert not any(k.endswith(".embedding.vector") for k in span.attributes)
    assert span.attributes["matrix.embedding_vectors_removed"] == 2
    assert span.cost_usd == compute_cost("text-embedding-3-small", 1000, 0)


def test_token_counts_clamped():
    span = _one({"llm.model_name": "gpt-4o", "llm.token_count.prompt": 1e30, "llm.token_count.completion": -5})
    assert (span.input_tokens, span.output_tokens) == (2**63 - 1, 0)
