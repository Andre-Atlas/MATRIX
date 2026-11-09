"""Builders for real OTLP export requests (protobuf messages and OTLP/JSON bodies)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import (
    AnyValue,
    ArrayValue,
    InstrumentationScope,
    KeyValue,
    KeyValueList,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Status
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan

T0 = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
T0_NS = int(T0.timestamp()) * 1_000_000_000
MS = 1_000_000

AGENT_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
GENAI_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
ERROR_TRACE_ID = "5b8efff798038103d269b633813fc60c"

AGENT_ID = "00f067aa0ba902b7"
LLM1_ID = "00f067aa0ba902b8"
TOOL_ID = "00f067aa0ba902b9"
RETRIEVER_ID = "00f067aa0ba902ba"
LLM2_ID = "00f067aa0ba902bb"
GENAI_ID = "1111111111111111"
ERROR_ID = "2222222222222222"

IMAGE_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"
STACKTRACE = "Traceback (most recent call last):\n" + "  File x.py, line 1\n" * 50


def any_value(value: Any) -> AnyValue:
    if isinstance(value, bool):
        return AnyValue(bool_value=value)
    if isinstance(value, int):
        return AnyValue(int_value=value)
    if isinstance(value, float):
        return AnyValue(double_value=value)
    if isinstance(value, str):
        return AnyValue(string_value=value)
    if isinstance(value, bytes):
        return AnyValue(bytes_value=value)
    if isinstance(value, list):
        return AnyValue(array_value=ArrayValue(values=[any_value(v) for v in value]))
    if isinstance(value, dict):
        return AnyValue(kvlist_value=KeyValueList(values=kvs(value)))
    raise TypeError(type(value))


def kvs(attrs: dict[str, Any]) -> list[KeyValue]:
    return [KeyValue(key=k, value=any_value(v)) for k, v in attrs.items()]


def pb_span(
    trace_id: str,
    span_id: str,
    name: str,
    start_ms: float,
    end_ms: float,
    attrs: dict[str, Any],
    *,
    parent: str | None = None,
    events: list[tuple[str, float, dict[str, Any]]] = (),  # type: ignore[assignment]
    status_code: int = 0,
    status_message: str = "",
    kind: int = PbSpan.SPAN_KIND_INTERNAL,
) -> PbSpan:
    return PbSpan(
        trace_id=bytes.fromhex(trace_id),
        span_id=bytes.fromhex(span_id),
        parent_span_id=bytes.fromhex(parent) if parent else b"",
        name=name,
        kind=kind,
        start_time_unix_nano=T0_NS + int(start_ms * MS),
        end_time_unix_nano=T0_NS + int(end_ms * MS),
        attributes=kvs(attrs),
        events=[
            PbSpan.Event(name=ev_name, time_unix_nano=T0_NS + int(at_ms * MS), attributes=kvs(ev_attrs))
            for ev_name, at_ms, ev_attrs in events
        ],
        status=Status(code=status_code, message=status_message),
    )


def export_request(
    spans: list[PbSpan],
    *,
    resource_attrs: dict[str, Any] | None = None,
    scope_attrs: dict[str, Any] | None = None,
) -> ExportTraceServiceRequest:
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=kvs(resource_attrs or {"service.name": "demo-app"})),
                scope_spans=[
                    ScopeSpans(
                        scope=InstrumentationScope(
                            name="openinference.instrumentation.test",
                            version="1.0",
                            attributes=kvs(scope_attrs or {}),
                        ),
                        spans=spans,
                    )
                ],
            )
        ]
    )


def to_otlp_json(
    request: ExportTraceServiceRequest, *, upper_ids: bool = False, enum_names: bool = False
) -> bytes:
    """OTLP/JSON body: camelCase, int64 as strings, ids as hex (not base64)."""
    data = MessageToDict(request, use_integers_for_enums=not enum_names)
    for rs in data.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                for key in ("traceId", "spanId", "parentSpanId"):
                    if key in span:
                        hex_id = base64.b64decode(span[key]).hex()
                        span[key] = hex_id.upper() if upper_ids else hex_id
    return json.dumps(data).encode()


def agent_trace_spans(trace_id: str = AGENT_TRACE_ID) -> list[PbSpan]:
    """AGENT root -> LLM (messages, tool call, image) -> TOOL -> RETRIEVER (3 docs) -> LLM."""
    agent = pb_span(
        trace_id,
        AGENT_ID,
        "support-agent",
        0,
        5000,
        {
            "openinference.span.kind": "AGENT",
            "input.value": "Where is my order 42?",
            "input.mime_type": "text/plain",
            "output.value": "Your order ships tomorrow.",
            "output.mime_type": "text/plain",
            "session.id": "sess-1",
            "user.id": "user-1",
            "tag.tags": ["prod", "support"],
            "metadata": json.dumps({"tenant": "acme", "tier": 2}),
            "shared": "span",
        },
        status_code=1,
    )
    llm1 = pb_span(
        trace_id,
        LLM1_ID,
        "ChatOpenAI",
        100,
        1100,
        {
            "openinference.span.kind": "LLM",
            "llm.model_name": "gpt-4o",
            "llm.provider": "openai",
            "llm.system": "openai",
            "llm.invocation_parameters": json.dumps({"temperature": 0.2, "max_tokens": 256}),
            "llm.token_count.prompt": 120,
            "llm.token_count.completion": 30,
            "llm.token_count.total": 150,
            "llm.token_count.completion_details.reasoning": 5,
            "llm.token_count.prompt_details.cache_read": 20,
            "llm.token_count.prompt_details.cache_write": 10,
            "llm.input_messages.0.message.role": "system",
            "llm.input_messages.0.message.content": "You are a support agent.",
            "llm.input_messages.1.message.role": "user",
            "llm.input_messages.1.message.contents.0.message_content.type": "text",
            "llm.input_messages.1.message.contents.0.message_content.text": "Where is my order 42?",
            "llm.input_messages.1.message.contents.1.message_content.type": "image",
            "llm.input_messages.1.message.contents.1.message_content.image.image.url": IMAGE_URL,
            "llm.output_messages.0.message.role": "assistant",
            "llm.output_messages.0.message.content": "",
            "llm.output_messages.0.message.tool_calls.0.tool_call.function.name": "lookup_order",
            "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments": '{"order_id": 42}',
            "input.value": '{"messages": "..."}',
            "output.value": '{"tool_calls": "..."}',
        },
        parent=AGENT_ID,
        events=[("first_token", 350, {})],
        kind=PbSpan.SPAN_KIND_CLIENT,
    )
    tool = pb_span(
        trace_id,
        TOOL_ID,
        "lookup_order",
        1200,
        1500,
        {
            "openinference.span.kind": "TOOL",
            "tool.name": "lookup_order",
            "tool.description": "Look up an order by id",
            "tool.parameters": json.dumps(
                {"type": "object", "properties": {"order_id": {"type": "integer"}}}
            ),
            "input.value": '{"order_id": 42}',
            "output.value": '{"status": "shipped"}',
        },
        parent=AGENT_ID,
    )
    retriever = pb_span(
        trace_id,
        RETRIEVER_ID,
        "retrieve-policy",
        1600,
        1900,
        {
            "openinference.span.kind": "RETRIEVER",
            "input.value": "shipping policy",
            **{
                f"retrieval.documents.{i}.document.{field}": value
                for i, (doc_id, content, score) in enumerate(
                    [
                        ("doc-a", "Orders ship in 24h.", 0.92),
                        ("doc-b", "Returns take 7 days.", 0.55),
                        (7, "FAQ", 0.1),
                    ]
                )
                for field, value in (
                    ("id", doc_id),
                    ("content", content),
                    ("score", score),
                    ("metadata", json.dumps({"source": f"kb/{i}.md"})),
                )
            },
        },
        parent=AGENT_ID,
    )
    llm2 = pb_span(
        trace_id,
        LLM2_ID,
        "ChatOpenAI",
        2000,
        4900,
        {
            "openinference.span.kind": "LLM",
            "llm.model_name": "gpt-4o-mini",
            "llm.provider": "openai",
            "llm.token_count.prompt": 300,
            "llm.token_count.completion": 40,
            "llm.input_messages.0.message.role": "user",
            "llm.input_messages.0.message.content": "Summarize: shipped",
            "llm.output_messages.0.message.role": "assistant",
            "llm.output_messages.0.message.content": "Your order ships tomorrow.",
            "image.url": "https://example.com/chart.png",
        },
        parent=AGENT_ID,
        status_code=1,
    )
    return [agent, llm1, tool, retriever, llm2]


def genai_span(trace_id: str = GENAI_TRACE_ID) -> PbSpan:
    """A span with only OpenTelemetry GenAI conventions (no OpenInference)."""
    return pb_span(
        trace_id,
        GENAI_ID,
        "chat claude-sonnet-4-5",
        0,
        800,
        {
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": "claude-sonnet-4-5",
            "gen_ai.request.temperature": 0.5,
            "gen_ai.usage.input_tokens": 1000,
            "gen_ai.usage.output_tokens": 200,
            "gen_ai.prompt.0.role": "user",
            "gen_ai.prompt.0.content": "Hello",
            "gen_ai.completion.0.role": "assistant",
            "gen_ai.completion.0.content": "Hi there",
            "llm.ttft_ms": 120.5,
        },
        kind=PbSpan.SPAN_KIND_CLIENT,
    )


def error_span(trace_id: str = ERROR_TRACE_ID) -> PbSpan:
    return pb_span(
        trace_id,
        ERROR_ID,
        "pipeline",
        0,
        50,
        {"session.id": "sess-2", "user.id": "user-2"},
        events=[
            (
                "exception",
                40,
                {
                    "exception.type": "ValueError",
                    "exception.message": "boom",
                    "exception.stacktrace": STACKTRACE,
                },
            )
        ],
        status_code=2,
        status_message="pipeline failed",
    )


def full_request() -> ExportTraceServiceRequest:
    return export_request(
        [*agent_trace_spans(), genai_span(), error_span()],
        resource_attrs={"service.name": "demo-app", "deployment.environment": "test", "shared": "resource"},
        scope_attrs={"shared": "scope", "scope.only": True},
    )
