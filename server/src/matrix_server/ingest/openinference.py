"""Normalize a decoded OTLP span into the MATRIX `Span` using OpenInference conventions.

OpenTelemetry GenAI attributes (`gen_ai.*`) are the fallback when OpenInference ones are
absent. Every string (fields and attribute values, recursively) is truncated to `max_chars`.
Payload attributes lifted into structured fields (`input.value`, `output.value`, message and
document lists, image URLs) are removed from `attributes` so they are not stored twice.
This module never logs: payloads may contain PII.
"""

from __future__ import annotations

import json
import math
import mimetypes
from datetime import UTC, datetime, timedelta
from typing import Any

from matrix_server.domain.spans import (
    Document,
    Media,
    Message,
    Span,
    SpanEvent,
    SpanKind,
    ToolCall,
    ToolInfo,
)
from matrix_server.ingest.otlp import TRUNCATION_MARK, RawSpan
from matrix_server.pricing import compute_cost, get_price

__all__ = ["TRUNCATION_MARK", "normalize", "normalize_all", "normalize_partial", "truncate"]

MAX_TOKENS = 2**63 - 1
UNKNOWN_MODEL_KEY = "matrix.cost_unknown_model"
EMBEDDING_VECTORS_KEY = "matrix.embedding_vectors_removed"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_FIRST_TOKEN_EVENTS = frozenset({"first_token", "gen_ai.first_token", "llm.first_token"})
_COSTED_KINDS = frozenset({SpanKind.LLM, SpanKind.EMBEDDING})
_LLM_MODEL_KEYS = ("llm.model_name", "gen_ai.request.model", "gen_ai.response.model")
_MODEL_KEYS = (*_LLM_MODEL_KEYS, "embedding.model_name")
_CACHE_READ_KEYS = (
    "llm.token_count.prompt_details.cache_read",
    "gen_ai.usage.cache_read_input_tokens",
    "gen_ai.usage.cache_read.input_tokens",
)
_CACHE_WRITE_KEYS = (
    "llm.token_count.prompt_details.cache_write",
    "gen_ai.usage.cache_creation_input_tokens",
    "gen_ai.usage.cache_creation.input_tokens",
)
_PROVIDER_KEYS = ("llm.provider", "llm.system", "gen_ai.system", "gen_ai.provider.name")
_GENAI_OPERATION_KINDS = {
    "chat": SpanKind.LLM,
    "text_completion": SpanKind.LLM,
    "generate_content": SpanKind.LLM,
    "embeddings": SpanKind.EMBEDDING,
    "execute_tool": SpanKind.TOOL,
    "invoke_agent": SpanKind.AGENT,
    "create_agent": SpanKind.AGENT,
}
_STATUS = {0: "unset", 1: "ok", 2: "error"}


# --------------------------------------------------------------------------- helpers


def truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + TRUNCATION_MARK


def _deep_truncate(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return truncate(value, max_chars)
    if isinstance(value, dict):
        return {k: _deep_truncate(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_truncate(v, max_chars) for v in value]
    return value


def _text(value: Any) -> str | None:
    """Attribute value as text: strings as-is, structures as JSON."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _int(value: Any) -> int:
    """Token count: clamped to [0, 2^63-1]; non-numeric and non-finite values count as 0."""
    if value is None or isinstance(value, bool):
        return 0
    if not isinstance(value, int):
        number = _float(value)
        if number is None:
            return 0
        value = int(number)
    return min(max(value, 0), MAX_TOKENS)


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _first(attrs: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = attrs.get(key)
        if value not in (None, ""):
            return value
    return None


def _nanos_to_dt(nanos: int) -> datetime:
    return _EPOCH + timedelta(microseconds=nanos // 1000)


def _flatten(obj: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in obj.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{full}."))
        else:
            out[full] = value
    return out


def _indexed(attrs: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    """Group `prefix.{i}.rest` keys into `[{rest: value}]` ordered by `i`.

    Also accepts the list form (`prefix` holding a list of kvlists) some exporters use.
    """
    groups: dict[int, dict[str, Any]] = {}
    dotted = prefix + "."
    for key, value in attrs.items():
        if not key.startswith(dotted):
            continue
        index, _, rest = key[len(dotted) :].partition(".")
        if index.isdigit() and rest:
            groups.setdefault(int(index), {})[rest] = value
    if not groups and isinstance(attrs.get(prefix), list):
        for i, item in enumerate(attrs[prefix]):
            if isinstance(item, dict):
                groups[i] = _flatten(item)
    return [groups[i] for i in sorted(groups)]


def _has_prefix(attrs: dict[str, Any], prefix: str) -> bool:
    dotted = prefix + "."
    return prefix in attrs or any(k.startswith(dotted) for k in attrs)


def _drop_prefix(attrs: dict[str, Any], prefix: str) -> None:
    dotted = prefix + "."
    for key in [k for k in attrs if k == prefix or k.startswith(dotted)]:
        del attrs[key]


def _media_from_url(kind: str, url: str, mime: str | None = None) -> Media:
    if mime is None:
        if url.startswith("data:"):
            mime = url[5:].split(",", 1)[0].split(";", 1)[0] or None
        else:
            mime = mimetypes.guess_type(url.split("?", 1)[0])[0]
    return Media(kind=kind, url=url, mime=mime)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- messages


def _oi_message(sub: dict[str, Any], media: list[Media]) -> Message:
    texts: list[str] = []
    for part in _indexed(sub, "message.contents"):
        text = part.get("message_content.text")
        if text is not None:
            texts.append(str(text))
        url = part.get("message_content.image.image.url")
        if isinstance(url, str) and url:
            media.append(_media_from_url("image", url))
    content = sub.get("message.content")
    tool_calls = [
        ToolCall(
            name=str(tc.get("tool_call.function.name") or ""),
            arguments=_text(tc.get("tool_call.function.arguments")) or "",
        )
        for tc in _indexed(sub, "message.tool_calls")
    ]
    return Message(
        role=str(sub.get("message.role") or ""),
        content=_text(content) if content is not None else "\n".join(texts),
        tool_calls=tool_calls,
    )


def _genai_indexed_message(sub: dict[str, Any]) -> Message:
    tool_calls = [
        ToolCall(name=str(tc.get("name") or ""), arguments=_text(tc.get("arguments")) or "")
        for tc in _indexed(sub, "tool_calls")
    ]
    return Message(
        role=str(sub.get("role") or ""), content=_text(sub.get("content")) or "", tool_calls=tool_calls
    )


def _genai_json_messages(value: Any) -> list[Message] | None:
    """`gen_ai.input.messages` / `gen_ai.output.messages`: JSON list of {role, parts}."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            return None
    if not isinstance(value, list):
        return None
    messages: list[Message] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in item.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_call":
                tool_calls.append(
                    ToolCall(name=str(part.get("name") or ""), arguments=_text(part.get("arguments")) or "")
                )
            elif part.get("content") is not None:
                texts.append(_text(part.get("content")) or "")
        messages.append(
            Message(role=str(item.get("role") or ""), content="\n".join(texts), tool_calls=tool_calls)
        )
    return messages


def _messages(attrs: dict[str, Any], direction: str, media: list[Media]) -> list[Message]:
    """Lift messages for `direction` ("input"|"output") and remove their source attributes."""
    oi_prefix = f"llm.{direction}_messages"
    if _has_prefix(attrs, oi_prefix):
        msgs = [_oi_message(sub, media) for sub in _indexed(attrs, oi_prefix)]
        _drop_prefix(attrs, oi_prefix)
        return msgs
    json_key = f"gen_ai.{direction}.messages"
    if json_key in attrs:
        parsed = _genai_json_messages(attrs[json_key])
        if parsed is not None:
            del attrs[json_key]
            return parsed
    legacy_prefix = "gen_ai.prompt" if direction == "input" else "gen_ai.completion"
    legacy = _indexed(attrs, legacy_prefix)
    if legacy:
        _drop_prefix(attrs, legacy_prefix)
        return [_genai_indexed_message(sub) for sub in legacy]
    return []


# --------------------------------------------------------------------------- documents


def _document(sub: dict[str, Any]) -> Document:
    metadata_raw = sub.get("document.metadata")
    metadata = _json_object(metadata_raw)
    if metadata is None:
        metadata = {} if metadata_raw in (None, "") else {"value": metadata_raw}
    doc_id = sub.get("document.id")
    return Document(
        id=None if doc_id in (None, "") else str(doc_id),
        content=_text(sub.get("document.content")) or "",
        score=_float(sub.get("document.score")),
        metadata=metadata,
    )


def _documents(attrs: dict[str, Any]) -> list[Document]:
    for prefix in ("retrieval.documents", "reranker.output_documents"):
        if _has_prefix(attrs, prefix):
            docs = [_document(sub) for sub in _indexed(attrs, prefix)]
            _drop_prefix(attrs, prefix)
            return docs
    return []


# --------------------------------------------------------------------------- kind, status, ttft


def _kind(attrs: dict[str, Any], model: str | None, is_root: bool) -> SpanKind:
    raw = attrs.get("openinference.span.kind")
    if isinstance(raw, str) and raw.strip():
        try:
            return SpanKind(raw.strip().upper())
        except ValueError:
            return SpanKind.UNKNOWN
    operation = attrs.get("gen_ai.operation.name")
    if isinstance(operation, str) and operation.lower() in _GENAI_OPERATION_KINDS:
        return _GENAI_OPERATION_KINDS[operation.lower()]
    if model:
        if _first(attrs, _LLM_MODEL_KEYS) is None:
            return SpanKind.EMBEDDING  # only `embedding.model_name` is set
        return SpanKind.LLM
    return SpanKind.CHAIN if is_root else SpanKind.UNKNOWN


def _drop_embedding_vectors(attrs: dict[str, Any]) -> None:
    """Embedding vectors are large and useless in the UI: remove them, keep how many there were."""
    removed = 0
    for key in [k for k in attrs if k.startswith("embedding.embeddings.") and k.endswith(".embedding.vector")]:
        del attrs[key]
        removed += 1
    listed = attrs.get("embedding.embeddings")
    if isinstance(listed, list):
        for item in listed:
            if not isinstance(item, dict):
                continue
            if "embedding.vector" in item:
                del item["embedding.vector"]
                removed += 1
            nested = item.get("embedding")
            if isinstance(nested, dict) and "vector" in nested:
                del nested["vector"]
                removed += 1
    if removed:
        attrs[EMBEDDING_VECTORS_KEY] = removed


def _ttft_ms(raw: RawSpan, attrs: dict[str, Any]) -> float | None:
    for event in raw.events:
        name = event.name.lower()
        if name in _FIRST_TOKEN_EVENTS or name.endswith(".first_token"):
            return max((event.time_unix_nano - raw.start_time_unix_nano) / 1e6, 0.0)
    return _float(attrs.get("llm.ttft_ms"))


def _status_message(raw: RawSpan, status: str) -> str | None:
    if raw.status_message:
        return raw.status_message
    if status == "error":
        for event in raw.events:
            if event.name == "exception":
                msg = event.attributes.get("exception.message") or event.attributes.get("exception.type")
                if msg:
                    return str(msg)
    return None


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            parsed = [value]
        value = parsed if isinstance(parsed, list) else [value]
    if not isinstance(value, list):
        return []
    seen: dict[str, None] = {}
    for item in value:
        if item not in (None, ""):
            seen[str(item)] = None
    return list(seen)


# --------------------------------------------------------------------------- normalize


def normalize(raw: RawSpan, project_id: str, max_chars: int) -> Span:
    attrs = dict(raw.attributes)
    is_root = raw.parent_span_id is None

    model = _text(_first(attrs, _MODEL_KEYS))
    provider = _text(_first(attrs, _PROVIDER_KEYS))
    kind = _kind(attrs, model, is_root)

    invocation_parameters = _json_object(attrs.get("llm.invocation_parameters"))
    if invocation_parameters is not None:
        del attrs["llm.invocation_parameters"]
    else:
        invocation_parameters = {
            k[len("gen_ai.request.") :]: v
            for k, v in attrs.items()
            if k.startswith("gen_ai.request.") and k != "gen_ai.request.model"
        }

    input_tokens = _int(
        _first(attrs, ("llm.token_count.prompt", "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"))
    )
    output_tokens = _int(
        _first(
            attrs,
            ("llm.token_count.completion", "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"),
        )
    )
    reasoning_tokens = _int(attrs.get("llm.token_count.completion_details.reasoning"))
    cache_read_tokens = _int(_first(attrs, _CACHE_READ_KEYS))
    cache_write_tokens = _int(_first(attrs, _CACHE_WRITE_KEYS))
    cost = 0.0
    if kind in _COSTED_KINDS:
        if model and get_price(model) is None:
            attrs[UNKNOWN_MODEL_KEY] = True
        cost = compute_cost(model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
    _drop_embedding_vectors(attrs)

    span_input = _text(attrs.pop("input.value", None))
    span_output = _text(attrs.pop("output.value", None))

    media: list[Media] = []
    input_messages = _messages(attrs, "input", media)
    output_messages = _messages(attrs, "output", media)
    documents = _documents(attrs)
    for key, kind_name, mime_key in (("image.url", "image", None), ("audio.url", "audio", "audio.mime_type")):
        url = attrs.get(key)
        if isinstance(url, str) and url:
            mime = attrs.get(mime_key) if mime_key else None
            media.append(_media_from_url(kind_name, url, mime if isinstance(mime, str) else None))
            del attrs[key]
    kept_media = [m for m in media if len(m.url) <= max_chars]
    if len(kept_media) < len(media):
        # A truncated data URI is unusable; drop it and record how many were dropped.
        attrs["matrix.media_dropped"] = len(media) - len(kept_media)

    tool = None
    tool_name = _first(attrs, ("tool.name", "gen_ai.tool.name"))
    if tool_name is not None:
        tool = ToolInfo(
            name=truncate(str(tool_name), max_chars),
            description=_opt(_first(attrs, ("tool.description", "gen_ai.tool.description")), max_chars),
            parameters=_opt(attrs.get("tool.parameters"), max_chars),
        )

    metadata = _json_object(attrs.get("metadata"))
    if metadata is not None:
        attrs["metadata"] = metadata

    start = _nanos_to_dt(raw.start_time_unix_nano)
    end_nanos = max(raw.end_time_unix_nano, raw.start_time_unix_nano)
    end = _nanos_to_dt(end_nanos)
    status = _STATUS.get(raw.status_code, "unset")

    def t(value: str | None) -> str | None:
        return None if value is None else truncate(value, max_chars)

    return Span(
        span_id=raw.span_id,
        trace_id=raw.trace_id,
        parent_span_id=raw.parent_span_id,
        name=truncate(raw.name, max_chars),
        kind=kind,
        start_time=start,
        end_time=end,
        duration_ms=(end_nanos - raw.start_time_unix_nano) / 1e6,
        status=status,  # type: ignore[arg-type]
        status_message=t(_status_message(raw, status)),
        model=t(model),
        provider=t(provider),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=cost,
        ttft_ms=_ttft_ms(raw, attrs),
        input=t(span_input),
        output=t(span_output),
        input_messages=[_truncate_message(m, max_chars) for m in input_messages],
        output_messages=[_truncate_message(m, max_chars) for m in output_messages],
        documents=[
            Document(
                id=t(d.id),
                content=truncate(d.content, max_chars),
                score=d.score,
                metadata=_deep_truncate(d.metadata, max_chars),
            )
            for d in documents
        ],
        tool=tool,
        media=kept_media,
        invocation_parameters=_deep_truncate(invocation_parameters, max_chars),
        attributes=_deep_truncate(attrs, max_chars),
        events=[
            SpanEvent(
                name=truncate(e.name, max_chars),
                time=_nanos_to_dt(e.time_unix_nano),
                attributes=_deep_truncate(e.attributes, max_chars),
            )
            for e in raw.events
        ],
        session_id=t(_text(_first(attrs, ("session.id", "gen_ai.conversation.id")))),
        user_id=t(_text(attrs.get("user.id"))),
        tags=[truncate(tag, max_chars) for tag in _tags(attrs.get("tag.tags"))],
        project_id=project_id,
    )


def _opt(value: Any, max_chars: int) -> str | None:
    text = _text(value)
    return None if text is None else truncate(text, max_chars)


def _truncate_message(message: Message, max_chars: int) -> Message:
    return Message(
        role=truncate(message.role, max_chars),
        content=truncate(message.content, max_chars),
        tool_calls=[
            ToolCall(name=truncate(tc.name, max_chars), arguments=truncate(tc.arguments, max_chars))
            for tc in message.tool_calls
        ],
    )


def normalize_all(raw_spans: list[RawSpan], project_id: str, max_chars: int) -> list[Span]:
    return [normalize(raw, project_id, max_chars) for raw in raw_spans]


def normalize_partial(
    raw_spans: list[RawSpan], project_id: str, max_chars: int
) -> tuple[list[Span], int, list[str]]:
    """Normalize span by span: a span that fails is dropped and counted, never the batch.

    Returns (spans, rejected count, distinct reasons). Reasons carry the exception type only.
    """
    spans: list[Span] = []
    rejected = 0
    reasons: list[str] = []
    for raw in raw_spans:
        try:
            spans.append(normalize(raw, project_id, max_chars))
        except Exception as exc:  # noqa: BLE001 - one bad span must not fail the batch
            rejected += 1
            reason = f"span normalization failed: {type(exc).__name__}"
            if reason not in reasons:
                reasons.append(reason)
    return spans, rejected, reasons
