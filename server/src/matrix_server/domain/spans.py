"""Span domain model: the `Span`, `TraceSummary` and `SessionSummary` API contract (spec section 4).

Times are timezone-aware UTC datetimes, serialized as ISO 8601 with a `Z` suffix.
`Span.project_id` is internal: it is never part of the API output.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, PlainSerializer


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc), PlainSerializer(_iso_utc, when_used="json")]


class SpanKind(StrEnum):
    LLM = "LLM"
    RETRIEVER = "RETRIEVER"
    RERANKER = "RERANKER"
    TOOL = "TOOL"
    AGENT = "AGENT"
    GUARDRAIL = "GUARDRAIL"
    CHAIN = "CHAIN"
    EMBEDDING = "EMBEDDING"
    EVALUATOR = "EVALUATOR"
    UNKNOWN = "UNKNOWN"


class ToolCall(BaseModel):
    name: str
    arguments: str


class Message(BaseModel):
    role: str
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)


class Document(BaseModel):
    id: str | None = None
    content: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Media(BaseModel):
    kind: Literal["image", "audio", "pdf", "bbox", "mask", "text_diff", "file"]
    url: str
    mime: str | None = None
    artifact_id: str | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    page: int | None = None
    duration_ms: float | None = None
    boxes: list[dict[str, Any]] | None = None
    mask_uri: str | None = None
    diff: dict[str, Any] | None = None


class ToolInfo(BaseModel):
    name: str
    description: str | None = None
    parameters: str | None = None


class SpanEvent(BaseModel):
    name: str
    time: UtcDatetime
    attributes: dict[str, Any] = Field(default_factory=dict)


class Span(BaseModel):
    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    name: str
    kind: SpanKind = SpanKind.UNKNOWN
    start_time: UtcDatetime
    end_time: UtcDatetime
    duration_ms: float = 0.0
    status: Literal["ok", "error", "unset"] = "unset"
    status_message: str | None = None
    model: str | None = None
    provider: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    ttft_ms: float | None = None
    input: str | None = None
    output: str | None = None
    input_messages: list[Message] = Field(default_factory=list)
    output_messages: list[Message] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    tool: ToolInfo | None = None
    media: list[Media] = Field(default_factory=list)
    invocation_parameters: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[SpanEvent] = Field(default_factory=list)
    session_id: str | None = None
    user_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    # Internal tenant key: every store query filters by it; never serialized to API clients.
    project_id: str = Field(default="", exclude=True)


class TraceSummary(BaseModel):
    trace_id: str
    name: str
    session_id: str | None = None
    user_id: str | None = None
    start_time: UtcDatetime
    end_time: UtcDatetime
    duration_ms: float
    status: Literal["ok", "error"]
    span_count: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
    root_kind: SpanKind = SpanKind.UNKNOWN
    input_preview: str | None = None
    output_preview: str | None = None
    tags: list[str] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)


class SessionSummary(BaseModel):
    session_id: str
    user_id: str | None = None
    trace_count: int
    first_seen: UtcDatetime
    last_seen: UtcDatetime
    total_cost_usd: float = 0.0
