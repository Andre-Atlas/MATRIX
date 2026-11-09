import json
import os

import pytest
from fastapi.testclient import TestClient
from otlp_payloads import AGENT_ID, AGENT_TRACE_ID, LLM1_ID, RETRIEVER_ID, full_request

from matrix_server.api import datasets, experiments
from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.storage.db import Base, make_engine

ADMIN = {"Authorization": "Bearer adm"}
PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


def make_client(db_url: str) -> TestClient:
    app = create_app(Settings(database_url=db_url, admin_token="adm"))
    # Until app.py wires them (see report): include the routers here.
    if not any(getattr(r, "path", "") == "/api/v1/datasets" for r in app.routes):
        app.include_router(datasets.router)
        app.include_router(experiments.router)
    return TestClient(app)


def project(client, name):
    resp = client.post("/api/v1/admin/projects", json={"name": name}, headers=ADMIN)
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['api_key']}"}


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path}/m.db"
        return
    if not PG_URL:
        pytest.skip("MATRIX_TEST_DATABASE_URL not set")
    eng = make_engine(PG_URL)
    Base.metadata.drop_all(eng)
    yield PG_URL
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def env(db_url):
    client = make_client(db_url)
    one, two = project(client, "one"), project(client, "two")
    resp = client.post(
        "/v1/traces",
        content=full_request().SerializeToString(),
        headers={"Content-Type": "application/x-protobuf", **one},
    )
    assert resp.status_code == 200
    yield client, one, two
    client.app.state.db_engine.dispose()


def test_requires_auth(env):
    client, _, _ = env
    assert client.get("/api/v1/datasets").status_code == 401
    assert client.post("/api/v1/datasets", json={"name": "x"}).status_code == 401


def test_crud_and_item_count(env):
    client, auth, _ = env
    resp = client.post("/api/v1/datasets", json={"name": "golden", "description": "d"}, headers=auth)
    assert resp.status_code == 201
    ds = resp.json()
    assert ds["name"] == "golden" and ds["item_count"] == 0 and ds["created_at"].endswith("Z")
    assert client.post("/api/v1/datasets", json={"name": "golden"}, headers=auth).status_code == 409
    empty = client.post("/api/v1/datasets", json={"name": "empty"}, headers=auth).json()

    item = {"input": {"question": "q1"}, "expected_output": "a1", "context": ["c1"], "metadata": {"k": 1}}
    resp = client.post(f"/api/v1/datasets/{ds['id']}/items", json=item, headers=auth)
    assert resp.status_code == 201
    created = resp.json()
    assert set(created) == {
        "id", "dataset_id", "input", "expected_output", "context", "metadata",
        "source_trace_id", "source_span_id", "created_at",
    }
    assert created["input"] == {"question": "q1"} and created["context"] == ["c1"]
    assert created["metadata"] == {"k": 1} and created["source_trace_id"] is None
    second = client.post(f"/api/v1/datasets/{ds['id']}/items", json={"input": {"q": 2}}, headers=auth).json()
    assert second["expected_output"] is None and second["context"] == [] and second["metadata"] == {}

    listed = {d["name"]: d for d in client.get("/api/v1/datasets", headers=auth).json()["items"]}
    assert listed["golden"]["item_count"] == 2 and listed["empty"]["item_count"] == 0
    assert set(listed["golden"]) == {"id", "name", "description", "item_count", "created_at"}

    detail = client.get(f"/api/v1/datasets/{ds['id']}", headers=auth).json()
    assert detail["dataset"]["id"] == ds["id"]
    assert [i["id"] for i in detail["items"]] == [created["id"], second["id"]]

    url = f"/api/v1/datasets/{ds['id']}/items/{created['id']}"
    assert client.delete(url, headers=auth).status_code == 204
    assert client.delete(url, headers=auth).status_code == 404
    assert len(client.get(f"/api/v1/datasets/{ds['id']}", headers=auth).json()["items"]) == 1
    assert client.get(f"/api/v1/datasets/{empty['id']}", headers=auth).json()["items"] == []


def test_item_validation(env):
    client, auth, _ = env
    ds = client.post("/api/v1/datasets", json={"name": "v"}, headers=auth).json()
    url = f"/api/v1/datasets/{ds['id']}/items"
    assert client.post(url, json={"input": "not an object"}, headers=auth).status_code == 422
    assert client.post(url, json={"input": {}, "context": [1]}, headers=auth).status_code == 422
    resp = client.post(url, content=b'{"input": {"x": NaN}}', headers={**auth, "content-type": "application/json"})
    assert resp.status_code == 422
    assert client.post("/api/v1/datasets", json={"name": ""}, headers=auth).status_code == 422


def test_project_isolation(env):
    client, one, two = env
    ds = client.post("/api/v1/datasets", json={"name": "mine"}, headers=one).json()
    item = client.post(f"/api/v1/datasets/{ds['id']}/items", json={"input": {"a": 1}}, headers=one).json()
    # Same name is fine in another project.
    assert client.post("/api/v1/datasets", json={"name": "mine"}, headers=two).status_code == 201
    assert [d["name"] for d in client.get("/api/v1/datasets", headers=two).json()["items"]] == ["mine"]
    assert client.get("/api/v1/datasets", headers=two).json()["items"][0]["item_count"] == 0
    base = f"/api/v1/datasets/{ds['id']}"
    assert client.get(base, headers=two).status_code == 404
    assert client.post(f"{base}/items", json={"input": {}}, headers=two).status_code == 404
    assert client.delete(f"{base}/items/{item['id']}", headers=two).status_code == 404
    assert client.get(f"{base}/export.jsonl", headers=two).status_code == 404
    resp = client.post(f"{base}/items/from-span", json={"trace_id": AGENT_TRACE_ID}, headers=two)
    assert resp.status_code == 404
    # Project two's own dataset cannot read project one's trace either.
    other = client.post("/api/v1/datasets", json={"name": "theirs"}, headers=two).json()
    resp = client.post(
        f"/api/v1/datasets/{other['id']}/items/from-span", json={"trace_id": AGENT_TRACE_ID}, headers=two
    )
    assert resp.status_code == 404


def test_from_span_root_and_explicit(env):
    client, auth, _ = env
    ds = client.post("/api/v1/datasets", json={"name": "spans"}, headers=auth).json()
    url = f"/api/v1/datasets/{ds['id']}/items/from-span"

    root = client.post(url, json={"trace_id": AGENT_TRACE_ID}, headers=auth)
    assert root.status_code == 201
    item = root.json()
    assert item["source_trace_id"] == AGENT_TRACE_ID and item["source_span_id"] == AGENT_ID
    assert item["input"] == {"input": "Where is my order 42?"}
    assert item["expected_output"] == "Your order ships tomorrow."
    assert item["context"] == ["Orders ship in 24h.", "Returns take 7 days.", "FAQ"]
    assert item["metadata"] == {"model": None, "kind": "AGENT"}

    llm = client.post(url, json={"trace_id": AGENT_TRACE_ID, "span_id": LLM1_ID}, headers=auth).json()
    assert llm["source_span_id"] == LLM1_ID
    # input.value is a JSON object: parsed, plus the last user message as `question`.
    assert llm["input"]["messages"] == "..." and llm["input"]["question"] == "Where is my order 42?"
    assert llm["context"] == [] and llm["metadata"] == {"model": "gpt-4o", "kind": "LLM"}

    retr = client.post(url, json={"trace_id": AGENT_TRACE_ID, "span_id": RETRIEVER_ID}, headers=auth).json()
    assert retr["input"] == {"input": "shipping policy"} and len(retr["context"]) == 3

    assert client.post(url, json={"trace_id": "0" * 32}, headers=auth).status_code == 404
    resp = client.post(url, json={"trace_id": AGENT_TRACE_ID, "span_id": "ffffffffffffffff"}, headers=auth)
    assert resp.status_code == 404
    assert client.post(url, json={}, headers=auth).status_code == 422


def test_export_jsonl(env):
    client, auth, _ = env
    ds = client.post("/api/v1/datasets", json={"name": "exp"}, headers=auth).json()
    client.post(
        f"/api/v1/datasets/{ds['id']}/items",
        json={"input": {"question": "olá"}, "expected_output": "a", "context": ["c"], "metadata": {"m": 1}},
        headers=auth,
    )
    client.post(f"/api/v1/datasets/{ds['id']}/items/from-span", json={"trace_id": AGENT_TRACE_ID}, headers=auth)
    resp = client.get(f"/api/v1/datasets/{ds['id']}/export.jsonl", headers=auth)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in resp.text.splitlines()]
    assert len(lines) == 2
    assert lines[0] == {"input": {"question": "olá"}, "expected_output": "a", "context": ["c"], "metadata": {"m": 1}}
    assert set(lines[1]) == {"input", "expected_output", "context", "metadata"}
    assert lines[1]["expected_output"] == "Your order ships tomorrow."
