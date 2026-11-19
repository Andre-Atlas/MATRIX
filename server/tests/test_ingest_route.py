import gzip
import json
import logging
import time
import tracemalloc

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from otlp_payloads import (
    AGENT_ID,
    AGENT_TRACE_ID,
    ERROR_TRACE_ID,
    GENAI_TRACE_ID,
    export_request,
    full_request,
    pb_span,
    to_otlp_json,
)

from matrix_server.api import ingest
from matrix_server.auth import Role, project_of
from matrix_server.config import Settings
from matrix_server.ingest import openinference
from matrix_server.storage.spans import MemorySpanStore

PB = {"content-type": "application/x-protobuf"}
JSON = {"content-type": "application/json"}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(ingest.router)
    app.state.settings = Settings(max_body_bytes=200_000, max_attribute_chars=1000)
    app.state.span_store = MemorySpanStore()
    app.dependency_overrides[project_of(Role.MEMBER, scope="ingest")] = lambda: "p1"
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def hooks():
    saved = list(ingest.on_spans_ingested)
    ingest.on_spans_ingested.clear()
    yield ingest.on_spans_ingested
    ingest.on_spans_ingested[:] = saved


def _trace_ids(app, project="p1"):
    items, _ = app.state.span_store.list_traces(project)
    return {t.trace_id for t in items}


def test_protobuf_ingest(app, client):
    resp = client.post("/v1/traces", content=full_request().SerializeToString(), headers=PB)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-protobuf"
    ExportTraceServiceResponse.FromString(resp.content)
    assert _trace_ids(app) == {AGENT_TRACE_ID, GENAI_TRACE_ID, ERROR_TRACE_ID}
    assert _trace_ids(app, "p2") == set()


def test_json_ingest(app, client):
    resp = client.post("/v1/traces", content=to_otlp_json(full_request()), headers=JSON)
    assert resp.status_code == 200
    assert resp.json() == {}
    summary, spans = app.state.span_store.get_trace("p1", AGENT_TRACE_ID)
    assert summary.span_count == 5
    assert all(s.project_id == "p1" for s in spans)


def test_gzip_ingest(app, client):
    body = gzip.compress(full_request().SerializeToString())
    resp = client.post("/v1/traces", content=body, headers={**PB, "content-encoding": "gzip"})
    assert resp.status_code == 200
    assert len(_trace_ids(app)) == 3


def test_attribute_limit_from_settings(app, client):
    app.state.settings = Settings(max_body_bytes=200_000, max_attribute_chars=5)
    client.post("/v1/traces", content=full_request().SerializeToString(), headers=PB)
    summary, _ = app.state.span_store.get_trace("p1", AGENT_TRACE_ID)
    assert summary.input_preview == "Where…[truncated]"


def test_body_too_large(app, client):
    app.state.settings = Settings(max_body_bytes=100)
    resp = client.post("/v1/traces", content=full_request().SerializeToString(), headers=PB)
    assert resp.status_code == 413
    assert _trace_ids(app) == set()


def test_gzip_bomb_rejected(app, client):
    app.state.settings = Settings(max_body_bytes=10_000)
    body = gzip.compress(b"\x00" * 1_000_000)
    assert len(body) < 10_000
    resp = client.post("/v1/traces", content=body, headers={**PB, "content-encoding": "gzip"})
    assert resp.status_code == 413


def test_invalid_gzip(client):
    resp = client.post("/v1/traces", content=b"not gzip", headers={**PB, "content-encoding": "gzip"})
    assert resp.status_code == 400


def test_undecodable_bodies(client):
    assert client.post("/v1/traces", content=b"{not json", headers=JSON).status_code == 400
    assert client.post("/v1/traces", content=b"\xff\xff\xff\xff", headers=PB).status_code == 400


def test_unsupported_media(client):
    assert client.post("/v1/traces", content=b"x", headers={"content-type": "text/plain"}).status_code == 415
    resp = client.post("/v1/traces", content=b"x", headers={**PB, "content-encoding": "br"})
    assert resp.status_code == 415


def test_requires_auth(app):
    app.dependency_overrides.clear()
    resp = TestClient(app).post("/v1/traces", content=b"{}", headers=JSON)
    assert resp.status_code == 401


def test_hooks_called_and_failures_isolated(app, client, hooks, caplog):
    calls = []

    def failing(project_id, spans):
        raise RuntimeError("secret payload must not be logged")

    hooks.append(failing)
    hooks.append(lambda project_id, spans: calls.append((project_id, len(spans))))
    with caplog.at_level(logging.ERROR, logger="matrix_server.api.ingest"):
        resp = client.post("/v1/traces", content=full_request().SerializeToString(), headers=PB)
    assert resp.status_code == 200
    assert calls == [("p1", 7)]
    assert len(_trace_ids(app)) == 3
    assert "failing" in caplog.text and "RuntimeError" in caplog.text
    assert "secret payload" not in caplog.text


def test_empty_request_skips_hooks(client, hooks):
    calls = []
    hooks.append(lambda project_id, spans: calls.append(spans))
    assert client.post("/v1/traces", content=b"{}", headers=JSON).status_code == 200
    assert calls == []


# --------------------------------------------------------------------------- review regressions

TID = "0123456789abcdef0123456789abcdef"


def _amp_body(spans: int, resource_attrs: int, value_chars: int = 99_000) -> bytes:
    """Same shape as the review's amp.py: huge resource attributes shared by many tiny spans."""
    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    for i in range(resource_attrs):
        kv = rs.resource.attributes.add()
        kv.key = f"k{i}"
        kv.value.string_value = "x" * value_chars
    ss = rs.scope_spans.add()
    for i in range(spans):
        s = ss.spans.add()
        s.trace_id = (i + 1).to_bytes(16, "big")
        s.span_id = (i + 1).to_bytes(8, "big")
    return req.SerializeToString()


def test_resource_amplification_rejected_quickly_with_bounded_memory(app, client):
    body = _amp_body(spans=20_000, resource_attrs=50)
    assert len(body) < 6_000_000
    app.state.settings = Settings(max_body_bytes=6_000_000)
    tracemalloc.start()
    started = time.monotonic()
    try:
        resp = client.post("/v1/traces", content=body, headers=PB)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert resp.status_code == 413
    assert time.monotonic() - started < 5
    # body + decoded resource attributes (capped) + headroom; the unbounded path needed > 1 GB
    assert peak < 80 * 2**20
    assert _trace_ids(app) == set()


def test_normalized_budget_uses_factor_setting(app, client):
    body = _amp_body(spans=10, resource_attrs=10, value_chars=4096)  # ~41 kB body, ~410 kB normalized
    app.state.settings = Settings(max_body_bytes=200_000, max_normalized_factor=4)
    assert client.post("/v1/traces", content=body, headers=PB).status_code == 200
    app.state.settings = Settings(max_body_bytes=200_000, max_normalized_factor=1)
    assert client.post("/v1/traces", content=body, headers=PB).status_code == 413


def test_resource_and_scope_attributes_capped(app, client):
    request = export_request(
        [pb_span(TID, AGENT_ID, "s", 0, 1, {})],
        resource_attrs={**{f"r{i}": i for i in range(300)}, "big": "y" * 10_000},
        scope_attrs={f"s{i}": i for i in range(260)},
    )
    app.state.settings = Settings(max_body_bytes=200_000, max_attribute_chars=100_000)
    assert client.post("/v1/traces", content=request.SerializeToString(), headers=PB).status_code == 200
    span = app.state.span_store.get_span("p1", TID, AGENT_ID)
    assert span.attributes["matrix.attributes_dropped"] == (301 - 256) + (260 - 256)
    assert sum(1 for k in span.attributes if k.startswith("r")) == 256
    request = export_request([pb_span(TID, AGENT_ID, "s", 0, 1, {})], resource_attrs={"big": "y" * 10_000})
    client.post("/v1/traces", content=request.SerializeToString(), headers=PB)
    span = app.state.span_store.get_span("p1", TID, AGENT_ID)
    assert span.attributes["big"] == "y" * 4096 + openinference.TRUNCATION_MARK


def _json_span(span_id, **fields):
    return {"traceId": TID, "spanId": span_id, "name": "s", **fields}


def _json_body(*spans):
    return json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": list(spans)}]}]}).encode()


def test_bad_spans_rejected_via_partial_success_json(app, client):
    body = _json_body(
        _json_span("a" * 16, startTimeUnixNano="1000", endTimeUnixNano="2000"),
        _json_span("zz"),  # invalid id
        _json_span("b" * 16, startTimeUnixNano="-5"),  # negative nanos
        _json_span("c" * 16, startTimeUnixNano=1e30),  # beyond 2200
        _json_span("d" * 16, endTimeUnixNano=str(8_000_000_000 * 10**9)),  # year > 2200
    )
    resp = client.post("/v1/traces", content=body, headers=JSON)
    assert resp.status_code == 200
    partial = resp.json()["partialSuccess"]
    assert int(partial["rejectedSpans"]) == 4
    assert "invalid span id" in partial["errorMessage"] and "out of range" in partial["errorMessage"]
    _, spans = app.state.span_store.get_trace("p1", TID)
    assert [s.span_id for s in spans] == ["a" * 16]


def test_bad_spans_rejected_via_partial_success_protobuf(app, client):
    request = export_request([pb_span(TID, AGENT_ID, "ok", 0, 1, {}), pb_span(TID, "e" * 16, "bad", 0, 1, {})])
    request.resource_spans[0].scope_spans[0].spans[1].start_time_unix_nano = 2**64 - 1
    resp = client.post("/v1/traces", content=request.SerializeToString(), headers=PB)
    assert resp.status_code == 200
    parsed = ExportTraceServiceResponse.FromString(resp.content)
    assert parsed.partial_success.rejected_spans == 1
    assert "start time out of range" in parsed.partial_success.error_message
    assert [s.span_id for s in app.state.span_store.get_trace("p1", TID)[1]] == [AGENT_ID]


def test_invalid_values_sanitized_not_500(app, client):
    body = (
        '{"resourceSpans":[{"scopeSpans":[{"spans":[{"traceId":"TID","spanId":"SID","name":"x\\ud800",'
        '"attributes":[{"key":"llm.ttft_ms","value":{"doubleValue":NaN}},'
        '{"key":"score","value":{"doubleValue":"Infinity"}},'
        '{"key":"k\\udfff","value":{"stringValue":"v\\ud83d"}},'
        '{"key":"llm.model_name","value":{"stringValue":"gpt-4o"}},'
        '{"key":"llm.token_count.prompt","value":{"intValue":"99999999999999999999999"}},'
        '{"key":"llm.token_count.completion","value":{"doubleValue":Infinity}}]}]}]}]}'
    ).replace("TID", TID).replace("SID", "a" * 16)
    resp = client.post("/v1/traces", content=body.encode(), headers=JSON)
    assert resp.status_code == 200 and resp.json() == {}
    span = app.state.span_store.get_span("p1", TID, "a" * 16)
    assert span.name == "x�"
    assert span.attributes["k�"] == "v�"
    assert span.ttft_ms is None and span.attributes["score"] is None
    assert (span.input_tokens, span.output_tokens) == (2**63 - 1, 0)
    span.model_dump_json()  # encodable


def test_normalize_failure_drops_only_that_span(app, client, monkeypatch):
    real = openinference.normalize

    def flaky(raw, project_id, max_chars):
        if raw.span_id == "b" * 16:
            raise RuntimeError("secret payload")
        return real(raw, project_id, max_chars)

    monkeypatch.setattr(openinference, "normalize", flaky)
    resp = client.post("/v1/traces", content=_json_body(_json_span("a" * 16), _json_span("b" * 16)), headers=JSON)
    partial = resp.json()["partialSuccess"]
    assert resp.status_code == 200 and partial["rejectedSpans"] == "1"
    assert "RuntimeError" in partial["errorMessage"] and "secret" not in partial["errorMessage"]
    assert [s.span_id for s in app.state.span_store.get_trace("p1", TID)[1]] == ["a" * 16]


def test_store_failure_is_retryable_503(app, client, caplog):
    class Broken(MemorySpanStore):
        def insert(self, spans):
            raise ConnectionError("clickhouse down: secret payload")

    app.state.span_store = Broken()
    with caplog.at_level(logging.ERROR, logger="matrix_server.api.ingest"):
        resp = client.post("/v1/traces", content=full_request().SerializeToString(), headers=PB)
    assert resp.status_code == 503
    assert "secret payload" not in caplog.text and "ConnectionError" in caplog.text


def test_multi_member_gzip(app, client):
    request = full_request()
    raw = request.SerializeToString()
    body = gzip.compress(raw[:100]) + gzip.compress(raw[100:])
    resp = client.post("/v1/traces", content=body, headers={**PB, "content-encoding": "gzip"})
    assert resp.status_code == 200
    assert len(_trace_ids(app)) == 3


def test_multi_member_gzip_still_bounded(app, client):
    app.state.settings = Settings(max_body_bytes=10_000)
    body = gzip.compress(b"\x00" * 6_000) + gzip.compress(b"\x00" * 6_000)
    resp = client.post("/v1/traces", content=body, headers={**PB, "content-encoding": "gzip"})
    assert resp.status_code == 413
