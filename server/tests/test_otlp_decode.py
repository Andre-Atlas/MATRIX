import json

import pytest
from otlp_payloads import (
    AGENT_ID,
    AGENT_TRACE_ID,
    ERROR_ID,
    LLM1_ID,
    T0_NS,
    export_request,
    full_request,
    pb_span,
    to_otlp_json,
)

from matrix_server.ingest.otlp import (
    OtlpDecodeError,
    UnsupportedContentType,
    decode_request,
    encode_response,
)


def _by_id(raw_spans):
    return {s.span_id: s for s in raw_spans}


def test_decode_protobuf_structure():
    spans = decode_request(full_request().SerializeToString(), "application/x-protobuf")
    assert len(spans) == 7
    by_id = _by_id(spans)
    agent = by_id[AGENT_ID]
    assert agent.trace_id == AGENT_TRACE_ID
    assert agent.parent_span_id is None
    assert agent.name == "support-agent"
    assert agent.start_time_unix_nano == T0_NS
    assert agent.end_time_unix_nano == T0_NS + 5000 * 1_000_000
    assert agent.status_code == 1
    assert agent.scope_name == "openinference.instrumentation.test"
    llm = by_id[LLM1_ID]
    assert llm.parent_span_id == AGENT_ID
    assert llm.kind == 3  # SPAN_KIND_CLIENT
    assert [e.name for e in llm.events] == ["first_token"]
    assert llm.attributes["llm.token_count.prompt"] == 120
    err = by_id[ERROR_ID]
    assert err.status_code == 2
    assert err.status_message == "pipeline failed"
    assert err.events[0].attributes["exception.message"] == "boom"


def test_attributes_flatten_resource_scope_span_precedence():
    by_id = _by_id(decode_request(full_request().SerializeToString(), "application/x-protobuf"))
    agent, llm = by_id[AGENT_ID], by_id[LLM1_ID]
    assert agent.attributes["service.name"] == "demo-app"
    assert agent.attributes["deployment.environment"] == "test"
    assert agent.attributes["scope.only"] is True
    assert agent.attributes["shared"] == "span"  # span wins
    assert llm.attributes["shared"] == "scope"  # scope wins over resource


def test_json_matches_protobuf():
    request = full_request()
    from_pb = decode_request(request.SerializeToString(), "application/x-protobuf")
    from_json = decode_request(to_otlp_json(request), "application/json; charset=utf-8")
    assert from_json == from_pb


def test_json_uppercase_ids_and_enum_names():
    request = full_request()
    from_pb = decode_request(request.SerializeToString(), "application/x-protobuf")
    from_json = decode_request(to_otlp_json(request, upper_ids=True, enum_names=True), "application/json")
    assert from_json == from_pb
    assert all(s.trace_id == s.trace_id.lower() for s in from_json)


def test_any_value_shapes_protobuf_and_json():
    attrs = {
        "s": "text",
        "i": 2**40,
        "d": 1.5,
        "b": False,
        "arr": ["a", 1, True],
        "kv": {"inner": {"deep": 3}, "x": "y"},
        "raw": b"\x00\x01binary",
    }
    request = export_request([pb_span(AGENT_TRACE_ID, AGENT_ID, "s", 0, 1, attrs)], resource_attrs={"r": 1})
    for body, ctype in (
        (request.SerializeToString(), "application/x-protobuf"),
        (to_otlp_json(request), "application/json"),
    ):
        (span,) = decode_request(body, ctype)
        got = span.attributes
        assert got["s"] == "text"
        assert got["i"] == 2**40 and isinstance(got["i"], int)
        assert got["d"] == 1.5
        assert got["b"] is False
        assert got["arr"] == ["a", 1, True]
        assert got["kv"] == {"inner": {"deep": 3}, "x": "y"}
        assert got["raw"] == "AAFiaW5hcnk="  # base64
        assert got["r"] == 1


def test_json_handwritten_otlp():
    body = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "svc"}}]},
                "scopeSpans": [
                    {
                        "scope": {"name": "manual"},
                        "spans": [
                            {
                                "traceId": "5B8EFFF798038103D269B633813FC60C",
                                "spanId": "EEE19B7EC3C1B174",
                                "parentSpanId": "",
                                "name": "root",
                                "kind": 1,
                                "startTimeUnixNano": "1544712660000000000",
                                "endTimeUnixNano": 1544712661000000000,
                                "attributes": [
                                    {"key": "llm.token_count.prompt", "value": {"intValue": "12"}},
                                    {"key": "temp", "value": {"doubleValue": 0.5}},
                                ],
                                "events": [{"name": "first_token", "timeUnixNano": "1544712660500000000"}],
                                "status": {"code": 2, "message": "bad"},
                            }
                        ],
                    }
                ],
            }
        ]
    }
    (span,) = decode_request(json.dumps(body).encode(), "application/json")
    assert span.trace_id == "5b8efff798038103d269b633813fc60c"
    assert span.span_id == "eee19b7ec3c1b174"
    assert span.parent_span_id is None
    assert span.start_time_unix_nano == 1544712660000000000
    assert span.end_time_unix_nano == 1544712661000000000
    assert span.attributes == {"service.name": "svc", "llm.token_count.prompt": 12, "temp": 0.5}
    assert span.events[0].time_unix_nano == 1544712660500000000
    assert (span.status_code, span.status_message) == (2, "bad")


def test_empty_requests():
    assert decode_request(b"", "application/x-protobuf") == []
    assert decode_request(b"{}", "application/json") == []


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"resourceSpans": "x"}',
        b'{"resourceSpans": [{"scopeSpans": [{"spans": [{"traceId": "zz", "spanId": "eee19b7ec3c1b174"}]}]}]}',
        b'{"resourceSpans": [{"scopeSpans": [{"spans": [{"traceId": "5b8efff798038103d269b633813fc60c"}]}]}]}',
        (
            b'{"resourceSpans": [{"scopeSpans": [{"spans": [{"traceId": "5b8efff798038103d269b633813fc60c",'
            b' "spanId": "eee19b7ec3c1b174", "startTimeUnixNano": "soon"}]}]}]}'
        ),
    ],
)
def test_invalid_json_raises(body):
    with pytest.raises(OtlpDecodeError):
        decode_request(body, "application/json")


def test_invalid_protobuf_raises():
    with pytest.raises(OtlpDecodeError):
        decode_request(b"\xff\xff\xff\xff", "application/x-protobuf")
    bad_id = export_request([pb_span(AGENT_TRACE_ID, AGENT_ID, "s", 0, 1, {})])
    bad_id.resource_spans[0].scope_spans[0].spans[0].trace_id = b"\x01\x02"
    with pytest.raises(OtlpDecodeError):
        decode_request(bad_id.SerializeToString(), "application/x-protobuf")


def test_unsupported_content_type():
    with pytest.raises(UnsupportedContentType):
        decode_request(b"{}", "text/plain")


def test_encode_response():
    assert encode_response("application/x-protobuf") == (b"", "application/x-protobuf")
    assert encode_response("application/json") == (b"{}", "application/json")
