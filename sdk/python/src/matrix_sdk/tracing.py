"""OpenInference span helpers and session propagation."""

from __future__ import annotations

import contextvars
import functools
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Self

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.trace import Status, StatusCode

SPAN_KIND = "openinference.span.kind"
_session: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar("matrix_session", default=None)
_provider: TracerProvider | None = None


def _set_provider(provider: TracerProvider | None) -> None:
    global _provider
    _provider = provider


def _tracer() -> trace.Tracer:
    if _provider is not None:
        return _provider.get_tracer("matrix_sdk")
    return trace.get_tracer("matrix_sdk")


class SessionSpanProcessor(SpanProcessor):
    """Copies session.id / user.id from the active `session()` onto every started span."""

    def on_start(self, span, parent_context=None) -> None:
        data = _session.get()
        if data:
            for key, value in data.items():
                span.set_attribute(key, value)


@contextmanager
def session(session_id: str, user_id: str | None = None) -> Iterator[None]:
    data = {"session.id": session_id}
    if user_id is not None:
        data["user.id"] = user_id
    token = _session.set(data)
    try:
        yield
    finally:
        _session.reset(token)


def _serialize(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return value, "text/plain"
    return json.dumps(value, default=str, ensure_ascii=False), "application/json"


class SpanHelper:
    """Context manager and decorator that opens an OpenInference span of a given kind."""

    def __init__(self, name: str, kind: str, attributes: dict[str, Any] | None = None) -> None:
        self.name = name
        self.kind = kind
        self._initial = {k: v for k, v in (attributes or {}).items() if v is not None}
        self.span: trace.Span | None = None
        self._cm = None

    # -- lifecycle
    def __enter__(self) -> Self:
        # Exceptions are recorded once, in __exit__ (with a clear status message).
        self._cm = _tracer().start_as_current_span(
            self.name,
            record_exception=False,
            set_status_on_exception=False,
            attributes={SPAN_KIND: self.kind, **self._initial},
        )
        self.span = self._cm.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None and self.span is not None:
            self.span.record_exception(exc)
            self.span.set_status(Status(StatusCode.ERROR, f"{exc_type.__name__}: {exc}"))
        assert self._cm is not None
        return bool(self._cm.__exit__(exc_type, exc, tb))

    def __call__(self, fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with SpanHelper(self.name, self.kind, self._initial):
                return fn(*args, **kwargs)

        return wrapper

    # -- attributes
    def _set(self, attrs: dict[str, Any]) -> None:
        if self.span is None:
            raise RuntimeError("span helper used outside of its context")
        for key, value in attrs.items():
            if value is not None:
                self.span.set_attribute(key, value)

    def set_attribute(self, key: str, value: Any) -> None:
        self._set({key: value})

    def set_input(self, value: Any) -> None:
        text, mime = _serialize(value)
        self._set({"input.value": text, "input.mime_type": mime})

    def set_output(self, value: Any) -> None:
        text, mime = _serialize(value)
        self._set({"output.value": text, "output.mime_type": mime})

    def set_messages(self, input: list[dict] | None = None, output: list[dict] | None = None) -> None:
        attrs: dict[str, Any] = {}
        for prefix, messages in (("llm.input_messages", input), ("llm.output_messages", output)):
            for i, msg in enumerate(messages or []):
                attrs[f"{prefix}.{i}.message.role"] = msg.get("role")
                attrs[f"{prefix}.{i}.message.content"] = msg.get("content")
        self._set(attrs)

    def set_tokens(self, prompt: int, completion: int, reasoning: int | None = None) -> None:
        self._set(
            {
                "llm.token_count.prompt": prompt,
                "llm.token_count.completion": completion,
                "llm.token_count.total": prompt + completion,
                "llm.token_count.completion_details.reasoning": reasoning,
            }
        )

    def set_documents(self, documents: list[dict]) -> None:
        attrs: dict[str, Any] = {}
        for i, doc in enumerate(documents):
            base = f"retrieval.documents.{i}.document"
            attrs[f"{base}.content"] = doc.get("content")
            attrs[f"{base}.id"] = doc.get("id")
            attrs[f"{base}.score"] = doc.get("score")
        self._set(attrs)

    def mark_first_token(self) -> None:
        if self.span is None:
            raise RuntimeError("span helper used outside of its context")
        self.span.add_event("first_token")

    @property
    def trace_id(self) -> str | None:
        return current_trace_id()


def trace_llm(name: str, model: str, provider: str | None = None) -> SpanHelper:
    return SpanHelper(name, "LLM", {"llm.model_name": model, "llm.provider": provider})


def trace_tool(name: str) -> SpanHelper:
    return SpanHelper(name, "TOOL", {"tool.name": name})


def trace_retriever(name: str) -> SpanHelper:
    return SpanHelper(name, "RETRIEVER")


def trace_agent(name: str) -> SpanHelper:
    return SpanHelper(name, "AGENT")


def trace_chain(name: str) -> SpanHelper:
    return SpanHelper(name, "CHAIN")


def current_trace_id() -> str | None:
    ctx = trace.get_current_span(otel_context.get_current()).get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
