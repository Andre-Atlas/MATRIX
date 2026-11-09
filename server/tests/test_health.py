from fastapi.testclient import TestClient
from otlp_payloads import AGENT_TRACE_ID, full_request

from matrix_server.app import create_app
from matrix_server.config import Settings


def _app(tmp_path):
    return create_app(Settings(database_url=f"sqlite:///{tmp_path}/m.db", admin_token="adm"))


def test_health(tmp_path):
    client = TestClient(_app(tmp_path))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_project_then_ingest_end_to_end(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    created = client.post("/api/v1/admin/projects", json={"name": "demo"}, headers={"Authorization": "Bearer adm"})
    assert created.status_code == 201
    key = created.json()["api_key"]
    project_id = created.json()["project"]["id"]

    body = full_request().SerializeToString()
    denied = client.post("/v1/traces", content=body, headers={"Content-Type": "application/x-protobuf"})
    assert denied.status_code == 401

    resp = client.post(
        "/v1/traces",
        content=body,
        headers={"Content-Type": "application/x-protobuf", "Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200
    found = app.state.span_store.get_trace(project_id, AGENT_TRACE_ID)
    assert found is not None
    assert app.state.span_store.get_trace("other-project", AGENT_TRACE_ID) is None
