import gzip
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
from otlp_payloads import AGENT_TRACE_ID, ERROR_TRACE_ID, GENAI_TRACE_ID, full_request, to_otlp_json

from matrix_server.api import ingest
from matrix_server.auth import current_project_id
from matrix_server.config import Settings
from matrix_server.storage.spans import MemorySpanStore

PB = {"content-type": "application/x-protobuf"}
JSON = {"content-type": "application/json"}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(ingest.router)
    app.state.settings = Settings(max_body_bytes=200_000, max_attribute_chars=1000)
    app.state.span_store = MemorySpanStore()
    app.dependency_overrides[current_project_id] = lambda: "p1"
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
