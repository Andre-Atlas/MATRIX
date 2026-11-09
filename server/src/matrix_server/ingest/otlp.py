"""OTLP/HTTP trace decoding (protobuf and JSON) into flat `RawSpan` records.

Resource, scope and span attributes are flattened into one dict (span wins over scope,
scope wins over resource). Ids are lowercase hex. Error messages never echo the payload.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Any

from google.protobuf.message import DecodeError as _ProtoDecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
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


class OtlpDecodeError(ValueError):
    """The body is not a valid OTLP trace export request."""


class UnsupportedContentType(ValueError):
    """The content type is neither OTLP protobuf nor OTLP JSON."""


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


def decode_request(body: bytes, content_type: str) -> list[RawSpan]:
    mt = media_type(content_type)
    if mt in PROTOBUF_TYPES:
        return _decode_protobuf(body)
    if mt in JSON_TYPES:
        return _decode_json(body)
    raise UnsupportedContentType("unsupported content type")


def encode_response(content_type: str) -> tuple[bytes, str]:
    """Empty successful OTLP response matching the request encoding."""
    if is_protobuf(content_type):
        return ExportTraceServiceResponse().SerializeToString(), "application/x-protobuf"
    return b"{}", "application/json"


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
        return value.double_value
    if which == "array_value":
        return [_proto_value(v) for v in value.array_value.values]
    if which == "kvlist_value":
        return {kv.key: _proto_value(kv.value) for kv in value.kvlist_value.values}
    if which == "bytes_value":
        return base64.b64encode(value.bytes_value).decode("ascii")
    return None


def _proto_attrs(attributes: Any) -> dict[str, Any]:
    return {kv.key: _proto_value(kv.value) for kv in attributes}


def _bytes_id(raw: bytes, size: int, what: str, *, optional: bool = False) -> str | None:
    if not raw:
        if optional:
            return None
        raise OtlpDecodeError(f"missing {what}")
    if len(raw) != size:
        raise OtlpDecodeError(f"invalid {what}")
    if raw == b"\x00" * size:
        if optional:
            return None
        raise OtlpDecodeError(f"invalid {what}")
    return raw.hex()


def _decode_protobuf(body: bytes) -> list[RawSpan]:
    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(body)
    except (_ProtoDecodeError, RuntimeError, ValueError) as exc:
        raise OtlpDecodeError("invalid OTLP protobuf body") from exc
    out: list[RawSpan] = []
    for resource_spans in request.resource_spans:
        resource_attrs = _proto_attrs(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            scope_attrs = _proto_attrs(scope_spans.scope.attributes)
            scope_name = scope_spans.scope.name or None
            for span in scope_spans.spans:
                attrs = {**resource_attrs, **scope_attrs, **_proto_attrs(span.attributes)}
                out.append(
                    RawSpan(
                        trace_id=_bytes_id(span.trace_id, 16, "trace id"),  # type: ignore[arg-type]
                        span_id=_bytes_id(span.span_id, 8, "span id"),  # type: ignore[arg-type]
                        parent_span_id=_bytes_id(span.parent_span_id, 8, "parent span id", optional=True),
                        name=span.name,
                        kind=int(span.kind),
                        start_time_unix_nano=int(span.start_time_unix_nano),
                        end_time_unix_nano=int(span.end_time_unix_nano),
                        attributes=attrs,
                        events=[
                            RawEvent(e.name, int(e.time_unix_nano), _proto_attrs(e.attributes))
                            for e in span.events
                        ],
                        status_code=int(span.status.code),
                        status_message=span.status.message or None,
                        scope_name=scope_name,
                    )
                )
    return out


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
    except (TypeError, ValueError) as exc:
        raise OtlpDecodeError(f"invalid {what}") from exc


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
            return float(_get(value, "doubleValue", "double_value"))
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
    out: dict[str, Any] = {}
    for item in _as_list(items):
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise OtlpDecodeError("invalid attribute")
        out[item["key"]] = _json_value(item.get("value") or {})
    return out


_HEX = frozenset("0123456789abcdef")


def _hex_id(value: Any, size: int, what: str, *, optional: bool = False) -> str | None:
    if value in (None, ""):
        if optional:
            return None
        raise OtlpDecodeError(f"missing {what}")
    if not isinstance(value, str):
        raise OtlpDecodeError(f"invalid {what}")
    hex_id = value.lower()
    if len(hex_id) != size * 2 or not set(hex_id) <= _HEX:
        raise OtlpDecodeError(f"invalid {what}")
    if hex_id == "0" * (size * 2):
        if optional:
            return None
        raise OtlpDecodeError(f"invalid {what}")
    return hex_id


def _decode_json(body: bytes) -> list[RawSpan]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise OtlpDecodeError("invalid OTLP JSON body") from exc
    if not isinstance(payload, dict):
        raise OtlpDecodeError("invalid OTLP JSON body")
    try:
        return _json_request(payload)
    except (AttributeError, TypeError, binascii.Error, RecursionError) as exc:
        raise OtlpDecodeError("invalid OTLP JSON body") from exc


def _json_request(payload: dict[str, Any]) -> list[RawSpan]:
    out: list[RawSpan] = []
    for resource_spans in _as_list(_get(payload, "resourceSpans", "resource_spans")):
        resource = resource_spans.get("resource") or {}
        resource_attrs = _json_attrs(resource.get("attributes"))
        for scope_spans in _as_list(_get(resource_spans, "scopeSpans", "scope_spans")):
            scope = scope_spans.get("scope") or {}
            scope_attrs = _json_attrs(scope.get("attributes"))
            scope_name = scope.get("name") or None
            for span in _as_list(scope_spans.get("spans")):
                status = span.get("status") or {}
                attrs = {**resource_attrs, **scope_attrs, **_json_attrs(span.get("attributes"))}
                events = [
                    RawEvent(
                        name=str(e.get("name") or ""),
                        time_unix_nano=_json_int(_get(e, "timeUnixNano", "time_unix_nano"), "event time"),
                        attributes=_json_attrs(e.get("attributes")),
                    )
                    for e in _as_list(span.get("events"))
                ]
                out.append(
                    RawSpan(
                        trace_id=_hex_id(_get(span, "traceId", "trace_id"), 16, "trace id"),  # type: ignore[arg-type]
                        span_id=_hex_id(_get(span, "spanId", "span_id"), 8, "span id"),  # type: ignore[arg-type]
                        parent_span_id=_hex_id(
                            _get(span, "parentSpanId", "parent_span_id"), 8, "parent span id", optional=True
                        ),
                        name=str(span.get("name") or ""),
                        kind=_json_enum(span.get("kind"), _SPAN_KIND_NAMES, "span kind"),
                        start_time_unix_nano=_json_int(
                            _get(span, "startTimeUnixNano", "start_time_unix_nano"), "start time"
                        ),
                        end_time_unix_nano=_json_int(
                            _get(span, "endTimeUnixNano", "end_time_unix_nano"), "end time"
                        ),
                        attributes=attrs,
                        events=events,
                        status_code=_json_enum(status.get("code"), _STATUS_CODE_NAMES, "status code"),
                        status_message=str(status["message"]) if status.get("message") else None,
                        scope_name=scope_name,
                    )
                )
    return out
