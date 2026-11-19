"""RBAC of the modules merged from `main` (benchmarks, datasets versioning, gateway, mlops, safety,
sandboxes, vectors) on the branch's auth (ADR-006/ADR-013).

`main`'s version of this file tested its own auth (PBKDF2 + JWT, open `POST /auth/register`,
`project_members` management). That auth was discarded in the merge (T2); login, sessions, members
and the generic route inventory are covered by `test_rbac.py`. What stays here is what is specific
to `main`'s modules: their role matrix, the role checks inside handlers (production/archived model
stages, protected aliases, model deletion, sandbox network), and proof that the old flows are gone.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import api_key_headers, project_with_roles

from matrix_server.app import create_app
from matrix_server.config import Settings


@pytest.fixture
def env(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path}/auth_rbac.db",
            span_store="memory",
            artifact_local_dir=str(tmp_path / "artifacts"),
        )
    )
    client = TestClient(app)
    factory = app.state.session_factory
    pid, roles = project_with_roles(factory, "Project A")
    other_pid, other_roles = project_with_roles(factory, "Project B")
    yield {
        "app": app,
        "client": client,
        "pid": pid,
        "roles": roles,
        "other_pid": other_pid,
        "other_admin": other_roles["admin"],
        "key": api_key_headers(factory, pid),
        "read_key": api_key_headers(factory, pid, ("read",)),
    }
    app.state.db_engine.dispose()


READS = [
    "/api/v1/benchmarks/catalog",
    "/api/v1/benchmarks/runs",
    "/api/v1/mlops/runs",
    "/api/v1/mlops/sweeps",
    "/api/v1/mlops/artifacts",
    "/api/v1/mlops/models",
    "/api/v1/sandboxes/runs",
    "/api/v1/safety/scans",
    "/api/v1/gateway/stats",
    "/api/v1/gateway/logs",
    "/api/v1/vectors/drift",
]


def test_main_auth_flows_are_gone(env):
    client = env["client"]
    body = {"email": "eve@example.com", "password": "x" * 16, "name": "Eve", "project_name": "P"}
    assert client.post("/api/v1/auth/register", json=body).status_code in (404, 405)
    # A JWT-shaped bearer (main's format) is just an invalid credential now.
    jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.c2ln"
    for path in READS[:3]:
        assert client.get(path, headers={"Authorization": f"Bearer {jwt_like}"}).status_code == 401
    # main's project header is not a credential and does not select a project for a key.
    assert client.get(READS[0], headers={"x-matrix-project-id": env["pid"]}).status_code == 401


@pytest.mark.parametrize("path", READS)
def test_reads_need_viewer_and_stay_in_the_project(env, path):
    client, roles = env["client"], env["roles"]
    assert client.get(path).status_code == 401
    assert client.get(path, headers=roles["viewer"]).status_code == 200, path
    assert client.get(path, headers=env["read_key"]).status_code == 200, path
    # A user of project B naming project A: 404, never A's data.
    foreign = {**env["other_admin"], "X-Matrix-Project": env["pid"]}
    assert client.get(path, headers=foreign).status_code == 404


def test_member_writes_viewer_and_read_key_cannot(env):
    client, roles = env["client"], env["roles"]
    writes = [
        ("/api/v1/mlops/runs", {"name": "r1"}),
        ("/api/v1/mlops/models", {"name": "m1"}),
        ("/api/v1/safety/scans", {"categories": ["LLM01"]}),
        ("/api/v1/sandboxes/runs", {"name": "s1"}),
        ("/api/v1/gateway/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}),
    ]
    for path, body in writes:
        for denied in (roles["viewer"], env["read_key"]):
            resp = client.post(path, json=body, headers=denied)
            assert resp.status_code == 403, (path, resp.text)
            assert resp.json()["detail"] == "requires role member"
        resp = client.post(path, json=body, headers=roles["member"])
        assert resp.status_code in (200, 201, 202), (path, resp.text)
        # A key with the write scope acts as MEMBER on data routes.
        resp = client.post(path, json={**body, "name": body.get("name", "") + "-k"} if "name" in body else body,
                           headers=env["key"])
        assert resp.status_code in (200, 201, 202), (path, resp.text)


def _model_with_version(client, member, name="clf"):
    model = client.post("/api/v1/mlops/models", json={"name": name}, headers=member)
    assert model.status_code == 201, model.text
    model_id = model.json()["id"]
    version = client.post(f"/api/v1/mlops/models/{model_id}/versions", json={}, headers=member)
    assert version.status_code == 201, version.text
    return model_id


@pytest.mark.parametrize("stage", ["production", "archived"])
def test_promote_and_archive_are_admin_only(env, stage):
    client, roles = env["client"], env["roles"]
    model_id = _model_with_version(client, roles["member"])
    url = f"/api/v1/mlops/models/{model_id}/versions/1/stage"
    for denied in (roles["viewer"], roles["member"], env["key"]):
        assert client.patch(url, json={"stage": stage}, headers=denied).status_code == 403
    # creating a version straight into the stage is the same decision
    created = client.post(f"/api/v1/mlops/models/{model_id}/versions", json={"stage": stage}, headers=env["key"])
    assert created.status_code == 403
    assert client.patch(url, json={"stage": "staging"}, headers=roles["member"]).status_code == 200
    for allowed in (roles["admin"], roles["owner"]):
        assert client.patch(url, json={"stage": stage}, headers=allowed).status_code == 200


def test_protected_alias_and_model_delete_are_admin_only(env):
    client, roles = env["client"], env["roles"]
    model_id = _model_with_version(client, roles["member"])
    url = f"/api/v1/mlops/models/{model_id}/aliases"
    assert client.post(url, json={"alias": "candidate", "version": 1}, headers=env["key"]).status_code == 200
    for denied in (roles["member"], env["key"]):
        assert client.post(url, json={"alias": "@production", "version": 1}, headers=denied).status_code == 403
        assert client.delete(f"/api/v1/mlops/models/{model_id}", headers=denied).status_code == 403
    assert client.post(url, json={"alias": "@production", "version": 1}, headers=roles["admin"]).status_code == 200
    assert client.delete(f"/api/v1/mlops/models/{model_id}?force=true", headers=roles["owner"]).status_code == 200


def test_sandbox_network_needs_an_admin_principal(env):
    client, roles = env["client"], env["roles"]
    policy = {"network_mode": "bridge"}
    for denied in (roles["member"], env["key"]):
        resp = client.post("/api/v1/sandboxes/runs", json={"name": "n", "sandbox_policy": policy}, headers=denied)
        assert resp.status_code == 403
    resp = client.post("/api/v1/sandboxes/runs", json={"name": "n", "sandbox_policy": policy}, headers=roles["admin"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["sandbox_policy"]["network_approved_by"]
