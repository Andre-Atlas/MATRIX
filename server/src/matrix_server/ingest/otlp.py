"""OTLP/HTTP trace decoding (protobuf and JSON) into flat `RawSpan` records.

Resource, scope and span attributes are flattened into one dict (span wins over scope,
scope wins over resource). Ids are lowercase hex. Error messages never echo the payload.

Because resource and scope attributes are copied into every span, a small body can expand
into a huge normalized batch. Decoding is therefore bounded: at most `MAX_ATTRIBUTES` per
resource/scope/span (extras dropped and counted in `matrix.attributes_dropped`), resource and
scope string values cut to `RESOURCE_VALUE_CHARS`, and an optional budget on the total
normalized attribute size of the request (`AttributeBudgetExceeded`, mapped to HTTP 413).

A structurally invalid body raises `OtlpDecodeError`. A span with invalid ids or values (bad
id, timestamp outside [1970, 2200], negative nanos) is rejected on its own and reported in
`DecodedBatch.rejected` / `errors` so the rest of the batch is kept (OTLP partial success).
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from google.protobuf.message import DecodeError as _ProtoDecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTracePartialSuccess,
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue

PROTOBUF_TYPES = frozenset({"application/x-protobuf", "application/protobuf"})
JSON_TYPES = frozenset({"application/json"})

_SPAN_KIND_NAMES = {
    "SPAN_KIND_UNSPECIFIED": 0,
    "SPAN_KIND_INTERNAL": 1,
    "SPAN_KIND_SERVER": 2,
    "SPAN_KIND_CLIENT": 3,
    "SPAN_KIND_PRODUCER": 4,
    "SPAN_KIND_CONSUMER": 5,
}
_STATUS_CODE_NAMES = {"STATUS_CODE_UNSET": 0, "STATUS_CODE_OK": 1, "STATUS_CODE_ERROR": 2}

TRUNCATION_MARK = "…[truncated]"
MAX_ATTRIBUTES = 256  # per resource and per scope
MAX_SPAN_ATTRIBUTES = 4096  # per span and per event (OpenInference flattens message lists)
RESOURCE_VALUE_CHARS = 4096  # resource/scope string values are copied into every span
DROPPED_ATTRIBUTES_KEY = "matrix.attributes_dropped"
# Accepted timestamps: [1970-01-01, 2200-01-01) in Unix nanoseconds.
MAX_TIME_UNIX_NANO = 7_258_118_400 * 1_000_000_000
_ENTRY_OVERHEAD = 16  # rough per-attribute cost on top of key and value sizes
_MAX_ERRORS = 5
_SURROGATE = re.compile("[\ud800-\udfff]")
_SURROGATE_ESCAPE = re.compile(rb"\\u[dD][89a-fA-F][0-9a-fA-F]{2}")


class OtlpDecodeError(ValueError):
    """The body is not a valid OTLP trace export request."""


class UnsupportedContentType(ValueError):
    """The content type is neither OTLP protobuf nor OTLP JSON."""


class AttributeBudgetExceeded(ValueError):
    """The normalized attributes of the request exceed the configured budget."""


class _SpanRejected(ValueError):
    """One span is invalid; the rest of the batch is still accepted."""


@dataclass
class RawEvent:
    name: str
    time_unix_nano: int
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: int
    start_time_unix_nano: int
    end_time_unix_nano: int
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[RawEvent] = field(default_factory=list)
    status_code: int = 0
    status_message: str | None = None
    scope_name: str | None = None


def media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def is_protobuf(content_type: str | None) -> bool:
    return media_type(content_type) in PROTOBUF_TYPES


def is_supported(content_type: str | None) -> bool:
    mt = media_type(content_type)
    return mt in PROTOBUF_TYPES or mt in JSON_TYPES


@dataclass
class DecodedBatch:
    spans: list[RawSpan] = field(default_factory=list)
    rejected: int = 0
    errors: list[str] = field(default_factory=list)

    def reject(self, reason: str, count: int = 1) -> None:
        self.rejected += count
        if reason not in self.errors and len(self.errors) < _MAX_ERRORS:
            self.errors.append(reason)


class _Budget:
    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.used = 0

    def consume(self, amount: int) -> None:
        self.used += amount
        if self.limit is not None and self.used > self.limit:
            raise AttributeBudgetExceeded("normalized attributes exceed the request budget")


def decode_batch(body: bytes, content_type: str, *, max_attribute_bytes: int | None = None) -> DecodedBatch:
    """Decode leniently: invalid spans are counted in `rejected` instead of failing the batch."""
    mt = media_type(content_type)
    budget = _Budget(max_attribute_bytes)
    if mt in PROTOBUF_TYPES:
        return _decode_protobuf(body, budget)
    if mt in JSON_TYPES:
        return _decode_json(body, budget)
    raise UnsupportedContentType("unsupported content type")


def decode_request(body: bytes, content_type: str) -> list[RawSpan]:
    """Strict decode: any invalid span raises `OtlpDecodeError`."""
    batch = decode_batch(body, content_type)
    if batch.rejected:
        raise OtlpDecodeError(batch.errors[0] if batch.errors else "invalid span")
    return batch.spans


def encode_response(content_type: str, rejected: int = 0, error_message: str = "") -> tuple[bytes, str]:
    """OTLP response matching the request encoding; `partial_success` set when spans were rejected."""
    if is_protobuf(content_type):
        response = ExportTraceServiceResponse()
        if rejected:
            response.partial_success.CopyFrom(
                ExportTracePartialSuccess(rejected_spans=rejected, error_message=error_message)
            )
        return response.SerializeToString(), "application/x-protobuf"
    if rejected:
        payload = {"partialSuccess": {"rejectedSpans": str(rejected), "errorMessage": error_message}}
        return json.dumps(payload).encode(), "application/json"
    return b"{}", "application/json"


# --------------------------------------------------------------------------- shared


def _cap_strings(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + TRUNCATION_MARK
    if isinstance(value, list):
        return [_cap_strings(v, max_chars) for v in value]
    if isinstance(value, dict):
        return {k: _cap_strings(v, max_chars) for k, v in value.items()}
    return value


def _value_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return 8 + sum(_value_size(v) for v in value)
    if isinstance(value, dict):
        return _attrs_size(value)
    return 8


def _attrs_size(attrs: dict[str, Any]) -> int:
    return sum(len(k) + _value_size(v) + _ENTRY_OVERHEAD for k, v in attrs.items())


def _check_time(nanos: int, what: str) -> int:
    if nanos < 0 or nanos >= MAX_TIME_UNIX_NANO:
        raise _SpanRejected(f"{what} out of range")
    return nanos


def _finite_or_none(number: float) -> float | None:
    return number if math.isfinite(number) else None


def _merge_attrs(
    resource: tuple[dict[str, Any], int], scope: tuple[dict[str, Any], int], span: tuple[dict[str, Any], int]
) -> dict[str, Any]:
    attrs = {**resource[0], **scope[0], **span[0]}
    dropped = resource[1] + scope[1] + span[1]
    if dropped:
        attrs[DROPPED_ATTRIBUTES_KEY] = dropped
    return attrs


class _Shared:
    """Resource or scope attributes, capped once and shared by all of their spans."""

    def __init__(self, attrs: dict[str, Any], dropped: int) -> None:
        self.attrs = _cap_strings(attrs, RESOURCE_VALUE_CHARS)
        self.dropped = dropped
        self.size = _attrs_size(self.attrs)

    @property
    def pair(self) -> tuple[dict[str, Any], int]:
        return self.attrs, self.dropped


# --------------------------------------------------------------------------- protobuf


def _proto_value(value: AnyValue) -> Any:
    which = value.WhichOneof("value")
    if which == "string_value":
        return value.string_value
    if which == "bool_value":
        return value.bool_value
    if which == "int_value":
        return value.int_value
    if which == "double_value":
        return _finite_or_none(value.double_value)
    if which == "array_value":
        return [_proto_value(v) for v in value.array_value.values]
    if which == "kvlist_value":
        return {kv.key: _proto_value(kv.value) for kv in value.kvlist_value.values}
    if which == "bytes_value":
        return base64.b64encode(value.bytes_value).decode("ascii")
    return None


def _proto_attrs_capped(attributes: Any, max_count: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    total = len(attributes)
    for i, kv in enumerate(attributes):
        if i >= max_count:
            break
        out[kv.key] = _proto_value(kv.value)
    return out, max(total - max_count, 0)


def _bytes_id(raw: bytes, size: int, what: str, *, optional: bool = False) -> str | None:
    if not raw:
        if optional:
            return None
        raise _SpanRejected(f"missing {what}")
    if len(raw) != size:
        raise _SpanRejected(f"invalid {what}")
    if raw == b"\x00" * size:
        if optional:
            return None
        raise _SpanRejected(f"invalid {what}")
    return raw.hex()


def _decode_protobuf(body: bytes, budget: _Budget) -> DecodedBatch:
    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(body)
    except (_ProtoDecodeError, RuntimeError, ValueError) as exc:
        raise OtlpDecodeError("invalid OTLP protobuf body") from exc
    batch = DecodedBatch()
    for resource_spans in request.resource_spans:
        resource = _Shared(*_proto_attrs_capped(resource_spans.resource.attributes, MAX_ATTRIBUTES))
        for scope_spans in resource_spans.scope_spans:
            scope = _Shared(*_proto_attrs_capped(scope_spans.scope.attributes, MAX_ATTRIBUTES))
            scope_name = scope_spans.scope.name or None
            for span in scope_spans.spans:
                try:
                    raw = _proto_span(span, resource, scope, scope_name, budget)
                except _SpanRejected as exc:
                    batch.reject(str(exc))
                    continue
                batch.spans.append(raw)
    return batch


def _proto_span(span: Any, resource: _Shared, scope: _Shared, scope_name: str | None, budget: _Budget) -> RawSpan:
    trace_id = _bytes_id(span.trace_id, 16, "trace id")
    span_id = _bytes_id(span.span_id, 8, "span id")
    parent_span_id = _bytes_id(span.parent_span_id, 8, "parent span id", optional=True)
    start = _check_time(int(span.start_time_unix_nano), "start time")
    end = _check_time(int(span.end_time_unix_nano), "end time")
    own = _proto_attrs_capped(span.attributes, MAX_SPAN_ATTRIBUTES)
    events = []
    for e in span.events:
        event_attrs, _ = _proto_attrs_capped(e.attributes, MAX_SPAN_ATTRIBUTES)
        events.append(RawEvent(e.name, _check_time(int(e.time_unix_nano), "event time"), event_attrs))
    budget.consume(
        resource.size + scope.size + _attrs_size(own[0]) + sum(_attrs_size(e.attributes) for e in events)
    )
    return RawSpan(
        trace_id=trace_id,  # type: ignore[arg-type]
        span_id=span_id,  # type: ignore[arg-type]
        parent_span_id=parent_span_id,
        name=span.name,
        kind=int(span.kind),
        start_time_unix_nano=start,
        end_time_unix_nano=end,
        attributes=_merge_attrs(resource.pair, scope.pair, own),
        events=events,
        status_code=int(span.status.code),
        status_message=span.status.message or None,
        scope_name=scope_name,
    )


# --------------------------------------------------------------------------- JSON


def _get(obj: dict[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    if camel in obj:
        return obj[camel]
    return obj.get(snake, default)


def _json_int(value: Any, what: str) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise OtlpDecodeError(f"invalid {what}")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OtlpDecodeError(f"invalid {what}") from exc


def _json_time(value: Any, what: str) -> int:
    try:
        nanos = _json_int(value, what)
    except OtlpDecodeError as exc:
        raise _SpanRejected(str(exc)) from exc
    return _check_time(nanos, what)


def _json_enum(value: Any, names: dict[str, int], what: str) -> int:
    if isinstance(value, str) and value in names:
        return names[value]
    return _json_int(value, what)


def _json_value(value: Any) -> Any:
    if not isinstance(value, dict):
        raise OtlpDecodeError("invalid attribute value")
    if "stringValue" in value or "string_value" in value:
        return str(_get(value, "stringValue", "string_value"))
    if "boolValue" in value or "bool_value" in value:
        return bool(_get(value, "boolValue", "bool_value"))
    if "intValue" in value or "int_value" in value:
        return _json_int(_get(value, "intValue", "int_value"), "intValue")
    if "doubleValue" in value or "double_value" in value:
        try:
            return _finite_or_none(float(_get(value, "doubleValue", "double_value")))
        except (TypeError, ValueError) as exc:
            raise OtlpDecodeError("invalid doubleValue") from exc
    if "arrayValue" in value or "array_value" in value:
        arr = _get(value, "arrayValue", "array_value") or {}
        return [_json_value(v) for v in _as_list(arr.get("values"))]
    if "kvlistValue" in value or "kvlist_value" in value:
        kv = _get(value, "kvlistValue", "kvlist_value") or {}
        return _json_attrs(kv.get("values"))
    if "bytesValue" in value or "bytes_value" in value:
        raw = _get(value, "bytesValue", "bytes_value")
        if not isinstance(raw, str):
            raise OtlpDecodeError("invalid bytesValue")
        return raw
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OtlpDecodeError("expected a list")
    return value


def _json_attrs(items: Any) -> dict[str, Any]:
    return _json_attrs_capped(items, None)[0]


def _json_attrs_capped(items: Any, max_count: int | None) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    items = _as_list(items)
    for i, item in enumerate(items):
        if max_count is not None and i >= max_count:
            return out, len(items) - max_count
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise OtlpDecodeError("invalid attribute")
        out[item["key"]] = _json_value(item.get("value") or {})
    return out, 0


_HEX = frozenset("0123456789abcdef")


def _hex_id(value: Any, size: int, what: str, *, optional: bool = False) -> str | None:
    if value in (None, ""):
        if optional:
            return None
        raise _SpanRejected(f"missing {what}")
    if not isinstance(value, str):
        raise _SpanRejected(f"invalid {what}")
    hex_id = value.lower()
    if len(hex_id) != size * 2 or not set(hex_id) <= _HEX:
        raise _SpanRejected(f"invalid {what}")
    if hex_id == "0" * (size * 2):
        if optional:
            return None
        raise _SpanRejected(f"invalid {what}")
    return hex_id


def _replace_surrogates(value: Any) -> Any:
    """Lone UTF-16 surrogates (from `\\udXXX` JSON escapes) cannot be encoded as UTF-8."""
    if isinstance(value, str):
        return _SURROGATE.sub("\ufffd", value)
    if isinstance(value, list):
        return [_replace_surrogates(v) for v in value]
    if isinstance(value, dict):
        return {_replace_surrogates(k): _replace_surrogates(v) for k, v in value.items()}
    return value


def _decode_json(body: bytes, budget: _Budget) -> DecodedBatch:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise OtlpDecodeError("invalid OTLP JSON body") from exc
    if not isinstance(payload, dict):
        raise OtlpDecodeError("invalid OTLP JSON body")
    try:
        if _SURROGATE_ESCAPE.search(body):
            payload = _replace_surrogates(payload)
        return _json_request(payload, budget)
    except (AttributeError, TypeError, binascii.Error, RecursionError) as exc:
        raise OtlpDecodeError("invalid OTLP JSON body") from exc


def _count(items: Any) -> int:
    return len(items) if isinstance(items, list) else 0


def _json_request(payload: dict[str, Any], budget: _Budget) -> DecodedBatch:
    batch = DecodedBatch()
    for resource_spans in _as_list(_get(payload, "resourceSpans", "resource_spans")):
        if not isinstance(resource_spans, dict):
            raise OtlpDecodeError("invalid resourceSpans")
        all_scopes = _as_list(_get(resource_spans, "scopeSpans", "scope_spans"))
        resource = resource_spans.get("resource") or {}
        try:
            resource_shared = _Shared(*_json_attrs_capped(resource.get("attributes"), MAX_ATTRIBUTES))
        except OtlpDecodeError as exc:
            count = sum(_count(s.get("spans")) for s in all_scopes if isinstance(s, dict))
            batch.reject(f"invalid resource: {exc}", count)
            continue
        for scope_spans in all_scopes:
            if not isinstance(scope_spans, dict):
                raise OtlpDecodeError("invalid scopeSpans")
            spans = _as_list(scope_spans.get("spans"))
            scope = scope_spans.get("scope") or {}
            try:
                scope_shared = _Shared(*_json_attrs_capped(scope.get("attributes"), MAX_ATTRIBUTES))
            except OtlpDecodeError as exc:
                batch.reject(f"invalid scope: {exc}", len(spans))
                continue
            scope_name = scope.get("name") or None
            if scope_name is not None:
                scope_name = str(scope_name)
            for span in spans:
                try:
                    raw = _json_span(span, resource_shared, scope_shared, scope_name, budget)
                except (_SpanRejected, OtlpDecodeError) as exc:
                    batch.reject(str(exc))
                    continue
                batch.spans.append(raw)
    return batch


def _json_span(
    span: Any, resource: _Shared, scope: _Shared, scope_name: str | None, budget: _Budget
) -> RawSpan:
    if not isinstance(span, dict):
        raise _SpanRejected("invalid span")
    trace_id = _hex_id(_get(span, "traceId", "trace_id"), 16, "trace id")
    span_id = _hex_id(_get(span, "spanId", "span_id"), 8, "span id")
    parent_span_id = _hex_id(_get(span, "parentSpanId", "parent_span_id"), 8, "parent span id", optional=True)
    start = _json_time(_get(span, "startTimeUnixNano", "start_time_unix_nano"), "start time")
    end = _json_time(_get(span, "endTimeUnixNano", "end_time_unix_nano"), "end time")
    status = span.get("status") or {}
    if not isinstance(status, dict):
        raise _SpanRejected("invalid status")
    own = _json_attrs_capped(span.get("attributes"), MAX_SPAN_ATTRIBUTES)
    events = []
    for e in _as_list(span.get("events")):
        if not isinstance(e, dict):
            raise _SpanRejected("invalid event")
        events.append(
            RawEvent(
                name=str(e.get("name") or ""),
                time_unix_nano=_json_time(_get(e, "timeUnixNano", "time_unix_nano"), "event time"),
                attributes=_json_attrs_capped(e.get("attributes"), MAX_SPAN_ATTRIBUTES)[0],
            )
        )
    budget.consume(
        resource.size + scope.size + _attrs_size(own[0]) + sum(_attrs_size(e.attributes) for e in events)
    )
    return RawSpan(
        trace_id=trace_id,  # type: ignore[arg-type]
        span_id=span_id,  # type: ignore[arg-type]
        parent_span_id=parent_span_id,
        name=str(span.get("name") or ""),
        kind=_json_enum(span.get("kind"), _SPAN_KIND_NAMES, "span kind"),
        start_time_unix_nano=start,
        end_time_unix_nano=end,
        attributes=_merge_attrs(resource.pair, scope.pair, own),
        events=events,
        status_code=_json_enum(status.get("code"), _STATUS_CODE_NAMES, "status code"),
        status_message=str(status["message"]) if status.get("message") else None,
        scope_name=scope_name,
    )
