import json
import os

import pytest
from fastapi.testclient import TestClient
from otlp_payloads import (
    AGENT_TRACE_ID,
    ERROR_TRACE_ID,
    GENAI_TRACE_ID,
    error_span,
    export_request,
    full_request,
)
from rbac_helpers import owner_project
from sqlalchemy import create_engine, select

from matrix_server import rules as rules_engine
from matrix_server.api import annotations as annotations_api
from matrix_server.api import datasets as datasets_api
from matrix_server.api import rules as rules_api
from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.storage.db import Base
from matrix_server.storage.models import Project

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
ADMIN = {"Authorization": "Bearer adm"}
PB = {"Content-Type": "application/x-protobuf"}


def _project(client, name):
    # Owner session: rules, online evals and the production label are admin-only (G0 RBAC).
    return owner_project(client, name, ADMIN)


@pytest.fixture(params=["sqlite", "pg"])
def client(request, tmp_path):
    if request.param == "pg":
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        url = PG_URL
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
        engine.dispose()
    else:
        url = f"sqlite:///{tmp_path}/m.db"
    app = create_app(Settings(database_url=url, admin_token="adm"))
    paths = {getattr(r, "path", "") for r in app.routes}
    if "/api/v1/datasets" not in paths:
        app.include_router(datasets_api.router)
    if "/api/v1/rules" not in paths:
        app.include_router(rules_api.router)
    if "/api/v1/annotations" not in paths:
        app.include_router(annotations_api.router)
    # create_app already installs the rules ingest and score hooks.
    try:
        yield TestClient(app)
    finally:
        if request.param == "pg":
            app.state.db_engine.dispose()


@pytest.fixture
def auth(client):
    return _project(client, "one")


def _rule(client, auth, condition, actions=("annotate",), name="r", **extra):
    resp = client.post(
        "/api/v1/rules",
        json={"name": name, "enabled": True, "condition": condition, "actions": list(actions), **extra},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ingest(client, auth, request=None):
    resp = client.post(
        "/v1/traces", content=(request or full_request()).SerializeToString(), headers={**PB, **auth}
    )
    assert resp.status_code == 200, resp.text


def _annotations(client, auth, **params):
    return client.get("/api/v1/annotations", params=params, headers=auth).json()["items"]


def _dataset(client, auth, name="golden"):
    return client.post("/api/v1/datasets", json={"name": name}, headers=auth).json()["id"]


def test_rule_validation(client, auth):
    def post(body):
        return client.post("/api/v1/rules", json=body, headers=auth).status_code

    ok = {"name": "x", "enabled": True, "condition": {"status": "error"}, "actions": ["alert"]}
    assert post(ok) == 201
    assert post({**ok, "condition": {}}) == 422
    assert post({**ok, "condition": {"status": "ok"}}) == 422
    assert post({**ok, "condition": {"score_name": "a", "op": "~", "value": 1}}) == 422
    assert post({**ok, "condition": {"score_name": "a", "op": "<"}}) == 422
    assert post({**ok, "condition": {"feedback": "positive"}}) == 422
    assert post({**ok, "condition": {"bogus": 1}}) == 422
    assert post({**ok, "actions": []}) == 422
    assert post({**ok, "actions": ["email"]}) == 422
    assert post({**ok, "actions": ["add_to_dataset"]}) == 422
    assert post({**ok, "actions": ["add_to_dataset"], "dataset_id": "nope"}) == 422
    assert client.post("/api/v1/rules", json=ok).status_code == 401


def test_rule_crud_and_isolation(client, auth):
    other = _project(client, "two")
    rule = _rule(client, auth, {"status": "error"})
    assert [r["id"] for r in client.get("/api/v1/rules", headers=auth).json()["items"]] == [rule["id"]]
    assert client.get("/api/v1/rules", headers=other).json()["items"] == []
    assert client.delete(f"/api/v1/rules/{rule['id']}", headers=other).status_code == 404
    # a dataset of another project cannot be used
    ds = _dataset(client, other)
    body = {"name": "x", "condition": {"status": "error"}, "actions": ["add_to_dataset"], "dataset_id": ds}
    assert client.post("/api/v1/rules", json=body, headers=auth).status_code == 422
    assert client.delete(f"/api/v1/rules/{rule['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/rules", headers=auth).json()["items"] == []


def test_status_error_rule_queues_annotation_on_ingest(client, auth):
    _rule(client, auth, {"status": "error"}, actions=["annotate", "alert"], name="errors")
    _ingest(client, auth)
    items = _annotations(client, auth, status="pending")
    assert [a["trace_id"] for a in items] == [ERROR_TRACE_ID]
    assert items[0]["reason"] == "rule:errors"
    assert items[0]["trace_name"] == "pipeline"
    alerts = client.get("/api/v1/alerts", headers=auth).json()["items"]
    assert len(alerts) == 1 and alerts[0]["trace_id"] == ERROR_TRACE_ID and alerts[0]["rule_id"]
    # re-ingesting the same trace does not duplicate anything
    _ingest(client, auth)
    assert len(_annotations(client, auth)) == 1
    assert len(client.get("/api/v1/alerts", headers=auth).json()["items"]) == 1


def test_late_child_span_does_not_refire(client, auth):
    _rule(client, auth, {"status": "error"}, actions=["alert"])
    _ingest(client, auth, export_request([error_span()]))
    child = error_span()
    child.span_id = bytes.fromhex("3333333333333333")
    child.parent_span_id = bytes.fromhex("2222222222222222")
    _ingest(client, auth, export_request([child]))
    assert len(client.get("/api/v1/alerts", headers=auth).json()["items"]) == 1


def test_latency_rule(client, auth):
    _rule(client, auth, {"min_latency_ms": 1000}, name="slow")
    _ingest(client, auth)
    ids = {a["trace_id"] for a in _annotations(client, auth)}
    assert AGENT_TRACE_ID in ids and ERROR_TRACE_ID not in ids


def test_combined_conditions_are_anded(client, auth):
    _rule(client, auth, {"status": "error", "min_latency_ms": 1000})
    _ingest(client, auth)
    assert _annotations(client, auth) == []


def test_disabled_rule_does_not_fire(client, auth):
    body = {"name": "off", "enabled": False, "condition": {"status": "error"}, "actions": ["annotate"]}
    assert client.post("/api/v1/rules", json=body, headers=auth).status_code == 201
    _ingest(client, auth)
    assert _annotations(client, auth) == []


def test_score_rule_queues_annotation_and_alert(client, auth):
    _rule(
        client, auth, {"score_name": "faithfulness", "op": "<", "value": 0.7}, ["annotate", "alert"], "faith"
    )
    _ingest(client, auth)
    assert _annotations(client, auth) == []  # score rules never fire on ingest

    def score(name, value, **extra):
        body = {"trace_id": GENAI_TRACE_ID, "name": name, "value": value, "source": "llm", **extra}
        assert client.post("/api/v1/scores", json=body, headers=auth).status_code == 201

    score("faithfulness", 0.9)
    score("relevancy", 0.1)
    assert _annotations(client, auth) == []
    score("faithfulness", 0.4)
    items = _annotations(client, auth)
    assert [(a["trace_id"], a["reason"]) for a in items] == [(GENAI_TRACE_ID, "rule:faith")]
    score("faithfulness", 0.2)
    assert len(_annotations(client, auth)) == 1
    alerts = client.get("/api/v1/alerts", headers=auth).json()["items"]
    assert len(alerts) == 1 and "faith" in alerts[0]["message"]


def test_negative_feedback_rule(client, auth):
    _rule(client, auth, {"feedback": "negative"}, name="fb")
    _ingest(client, auth)

    def feedback(trace_id, name, value, label=None):
        body = {"trace_id": trace_id, "name": name, "value": value, "label": label, "source": "sdk"}
        assert client.post("/api/v1/scores", json=body, headers=auth).status_code == 201

    feedback(AGENT_TRACE_ID, "user_feedback", 1, "thumbs_up")
    feedback(AGENT_TRACE_ID, "other", 0)
    assert _annotations(client, auth) == []
    feedback(GENAI_TRACE_ID, "user_feedback", 0)
    feedback(ERROR_TRACE_ID, "feedback", 1, "👎")
    assert {a["trace_id"] for a in _annotations(client, auth)} == {GENAI_TRACE_ID, ERROR_TRACE_ID}


def test_add_to_dataset_action_is_idempotent(client, auth):
    ds = _dataset(client, auth)
    _rule(client, auth, {"status": "error"}, ["add_to_dataset"], dataset_id=ds)
    _ingest(client, auth)
    _ingest(client, auth)
    items = client.get(f"/api/v1/datasets/{ds}", headers=auth).json()["items"]
    assert len(items) == 1 and items[0]["source_trace_id"] == ERROR_TRACE_ID


def test_create_annotation_manual(client, auth):
    _ingest(client, auth)
    assert client.post("/api/v1/annotations", json={"trace_id": "f" * 32}, headers=auth).status_code == 404
    first = client.post(
        "/api/v1/annotations", json={"trace_id": AGENT_TRACE_ID, "reason": "look"}, headers=auth
    )
    again = client.post("/api/v1/annotations", json={"trace_id": AGENT_TRACE_ID, "reason": "x"}, headers=auth)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    item = _annotations(client, auth, status="pending")[0]
    assert item["input_preview"] == "Where is my order 42?"
    assert _annotations(client, auth, status="done") == []


def test_complete_annotation_flywheel(client, auth):
    _ingest(client, auth)
    ds = _dataset(client, auth)
    ann = client.post("/api/v1/annotations", json={"trace_id": AGENT_TRACE_ID}, headers=auth).json()
    resp = client.post(
        f"/api/v1/annotations/{ann['id']}/complete",
        json={
            "scores": [{"name": "quality", "value": 2}],
            "corrected_output": "It ships today.",
            "add_to_dataset_id": ds,
            "comment": "wrong date",
        },
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "done" and resp.json()["completed_at"]
    again = client.post(f"/api/v1/annotations/{ann['id']}/complete", json={}, headers=auth)
    assert again.status_code == 409

    scores = client.get(f"/api/v1/traces/{AGENT_TRACE_ID}/scores", headers=auth).json()
    assert [(s["name"], s["value"], s["source"]) for s in scores] == [("quality", 2.0, "human")]
    items = client.get(f"/api/v1/datasets/{ds}", headers=auth).json()["items"]
    assert len(items) == 1 and items[0]["expected_output"] == "It ships today."
    assert items[0]["source_trace_id"] == AGENT_TRACE_ID

    dpo = client.get("/api/v1/exports/dpo.jsonl", headers=auth)
    assert dpo.status_code == 200
    lines = [json.loads(line) for line in dpo.text.splitlines()]
    assert len(lines) == 1
    assert lines[0]["prompt"] == "Where is my order 42?"
    assert lines[0]["chosen"] == "It ships today."
    assert lines[0]["rejected"] == "Your order ships tomorrow."
    assert lines[0]["trace_id"] == AGENT_TRACE_ID and lines[0]["created_at"].endswith("Z")
    assert _annotations(client, auth, status="done")[0]["id"] == ann["id"]


def test_complete_without_correction_uses_original_output(client, auth):
    _ingest(client, auth)
    ds = _dataset(client, auth)
    ann = client.post("/api/v1/annotations", json={"trace_id": AGENT_TRACE_ID}, headers=auth).json()
    bad = client.post(
        f"/api/v1/annotations/{ann['id']}/complete", json={"add_to_dataset_id": "nope"}, headers=auth
    )
    assert bad.status_code == 404
    ok = client.post(
        f"/api/v1/annotations/{ann['id']}/complete", json={"add_to_dataset_id": ds}, headers=auth
    )
    assert ok.status_code == 200
    items = client.get(f"/api/v1/datasets/{ds}", headers=auth).json()["items"]
    assert items[0]["expected_output"] == "Your order ships tomorrow."
    assert client.get("/api/v1/exports/dpo.jsonl", headers=auth).text == ""


def test_human_scores_on_complete_run_rule_hooks(client, auth):
    _ingest(client, auth)
    _rule(client, auth, {"score_name": "quality", "op": "<=", "value": 1}, ["alert"])
    ann = client.post("/api/v1/annotations", json={"trace_id": AGENT_TRACE_ID}, headers=auth).json()
    client.post(
        f"/api/v1/annotations/{ann['id']}/complete",
        json={"scores": [{"name": "quality", "value": 1}]},
        headers=auth,
    )
    assert len(client.get("/api/v1/alerts", headers=auth).json()["items"]) == 1


def test_isolation_between_projects(client, auth):
    other = _project(client, "two")
    _rule(client, auth, {"status": "error"}, ["annotate", "alert"])
    _ingest(client, auth)
    ann = _annotations(client, auth)[0]
    assert _annotations(client, other) == []
    assert client.get("/api/v1/alerts", headers=other).json()["items"] == []
    assert client.post(f"/api/v1/annotations/{ann['id']}/complete", json={}, headers=other).status_code == 404
    assert (
        client.post("/api/v1/annotations", json={"trace_id": ERROR_TRACE_ID}, headers=other).status_code
        == 404
    )
    assert client.get("/api/v1/exports/dpo.jsonl", headers=other).text == ""
    # rules of one project never fire for another project's ingestion
    _ingest(client, other)
    assert _annotations(client, other) == []


def test_hooks_never_raise(client, auth):
    class Boom:
        def get_trace(self, *args):
            raise RuntimeError("secret payload")

    _rule(client, auth, {"status": "error"})
    _ingest(client, auth)
    with client.app.state.session_factory() as db:
        project_id = db.scalars(select(Project.id)).first()
    spans = client.app.state.span_store.get_trace(project_id, ERROR_TRACE_ID)[1]
    hook = rules_engine.make_ingest_hook(client.app.state.session_factory, Boom())
    hook(project_id, spans)  # must not raise
