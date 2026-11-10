import os
import threading

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import owner_project

from matrix_server.api import prompts as prompts_api
from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.prompts import extract_variables, render
from matrix_server.storage.db import Base

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
ADMIN = {"Authorization": "Bearer adm"}


def _project(client, name):
    # Owner session: rules, online evals and the production label are admin-only (G0 RBAC).
    return owner_project(client, name, ADMIN)


def _build(url):
    app = create_app(Settings(database_url=url, admin_token="adm"))
    if not any(getattr(r, "path", "") == "/api/v1/prompts" for r in app.routes):
        app.include_router(prompts_api.router)
    return app


@pytest.fixture
def client(tmp_path):
    return TestClient(_build(f"sqlite:///{tmp_path}/m.db"))


@pytest.fixture
def auth(client):
    return _project(client, "one")


def _create(client, auth, name="greet", template="Hi {{ name }}", **extra):
    body = {"name": name, "type": "text", "template": template, **extra}
    resp = client.post("/api/v1/prompts", json=body, headers=auth)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_variables_helpers():
    assert extract_variables("{{a}} {{ b }} {{a}} {{  c.d  }}") == ["a", "b", "c.d"]
    chat = [{"role": "system", "content": "Be {{tone}}"}, {"role": "user", "content": "{{ q }} {{tone}}"}]
    assert extract_variables(chat) == ["tone", "q"]
    assert render("Hi {{ name }}!", {"name": "Ana"}) == "Hi Ana!"
    assert render(chat, {"tone": "kind", "q": "?"})[1]["content"] == "? kind"
    with pytest.raises(KeyError):
        render("{{x}}", {})


def test_create_versions_and_shape(client, auth):
    v1 = _create(client, auth, labels=["production"], commit_message="first", config={"temperature": 0.2})
    assert v1["version"] == 1 and v1["variables"] == ["name"] and v1["labels"] == ["production"]
    assert v1["config"] == {"temperature": 0.2} and v1["response_schema"] is None
    assert set(v1) == {"name", "version", "type", "template", "variables", "config", "response_schema",
                       "labels", "commit_message", "created_at"}
    v2 = _create(client, auth, template="Hello {{ who }} {{ name }}")
    assert v2["version"] == 2 and v2["variables"] == ["who", "name"] and v2["labels"] == []
    versions = client.get("/api/v1/prompts/greet/versions", headers=auth).json()["items"]
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[1]["labels"] == ["production"]


def test_labels_move_rollback_and_default_resolution(client, auth):
    _create(client, auth)
    _create(client, auth, template="v2")
    _create(client, auth, template="v3")
    get = lambda **p: client.get("/api/v1/prompts/greet", params=p, headers=auth)
    assert get().json()["version"] == 3  # no production label -> latest
    assert get(label="production").status_code == 404
    moved = client.post("/api/v1/prompts/greet/labels", json={"label": "production", "version": 2}, headers=auth)
    assert moved.status_code == 200 and moved.json()["labels"] == ["production"]
    assert get().json()["version"] == 2  # production wins over latest
    client.post("/api/v1/prompts/greet/labels", json={"label": "production", "version": 3}, headers=auth)
    assert get().json()["version"] == 3
    client.post("/api/v1/prompts/greet/labels", json={"label": "production", "version": 1}, headers=auth)  # rollback
    assert get().json()["version"] == 1
    assert get(label="production").json()["version"] == 1
    assert get(version=3).json()["labels"] == []
    assert get(version=9).status_code == 404
    assert get(version=0).status_code == 422
    # creating with a label moves it to the new version
    v4 = _create(client, auth, template="v4", labels=["production", "staging"])
    assert v4["labels"] == ["production", "staging"]
    assert get(version=1).json()["labels"] == []
    listing = client.get("/api/v1/prompts", headers=auth).json()["items"]
    assert len(listing) == 1
    assert listing[0]["latest_version"] == 4 and listing[0]["labels"] == {"production": 4, "staging": 4}
    assert listing[0]["updated_at"]
    bad = client.post("/api/v1/prompts/greet/labels", json={"label": "production", "version": 99}, headers=auth)
    assert bad.status_code == 404
    assert client.post("/api/v1/prompts/nope/labels", json={"label": "x", "version": 1}, headers=auth).status_code == 404


def test_read_headers_missing_and_auth(client, auth):
    _create(client, auth)
    resp = client.get("/api/v1/prompts/greet", headers=auth)
    assert resp.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/prompts", headers=auth).headers["cache-control"] == "no-store"
    assert client.get("/api/v1/prompts/missing", headers=auth).status_code == 404
    assert client.get("/api/v1/prompts/missing/versions", headers=auth).status_code == 404
    assert client.get("/api/v1/prompts").status_code == 401
    assert client.post("/api/v1/prompts", json={}).status_code == 401
    assert client.get("/api/v1/prompts/greet", params={"label": "a", "version": 1}, headers=auth).status_code == 422


def test_names_with_slashes(client, auth):
    _create(client, auth, name="team/support/greet", labels=["production"])
    _create(client, auth, name="team/support/greet", template="v2")
    got = client.get("/api/v1/prompts/team/support/greet", headers=auth)
    assert got.status_code == 200 and got.json()["version"] == 1
    assert client.get("/api/v1/prompts/team/support/greet", params={"version": 2}, headers=auth).json()["template"] == "v2"
    versions = client.get("/api/v1/prompts/team/support/greet/versions", headers=auth).json()["items"]
    assert [v["version"] for v in versions] == [2, 1]
    moved = client.post(
        "/api/v1/prompts/team/support/greet/labels", json={"label": "production", "version": 2}, headers=auth
    )
    assert moved.status_code == 200 and moved.json()["name"] == "team/support/greet"
    assert client.get("/api/v1/prompts/team/support/greet", headers=auth).json()["version"] == 2


def test_chat_prompt(client, auth):
    tpl = [{"role": "system", "content": "You are {{ persona }}"}, {"role": "user", "content": "{{question}}"}]
    resp = client.post("/api/v1/prompts", json={"name": "chat", "type": "chat", "template": tpl}, headers=auth)
    assert resp.status_code == 201
    assert resp.json()["variables"] == ["persona", "question"] and resp.json()["template"] == tpl


@pytest.mark.parametrize(
    "body",
    [
        {"name": "-bad", "type": "text", "template": "x"},
        {"name": "has space", "type": "text", "template": "x"},
        {"name": "a" * 201, "type": "text", "template": "x"},
        {"name": "ok", "type": "other", "template": "x"},
        {"name": "ok", "type": "text", "template": ["x"]},
        {"name": "ok", "type": "text", "template": "x" * 200_001},
        {"name": "ok", "type": "chat", "template": "x"},
        {"name": "ok", "type": "chat", "template": []},
        {"name": "ok", "type": "chat", "template": [{"role": "bot", "content": "x"}]},
        {"name": "ok", "type": "chat", "template": [{"role": "user", "content": 1}]},
        {"name": "ok", "type": "chat", "template": [{"role": "user"}]},
        {"name": "ok", "type": "text", "template": "x", "labels": ["Prod"]},
        {"name": "ok", "type": "text", "template": "x", "labels": ["-x"]},
        {"name": "ok", "type": "text", "template": "x", "response_schema": {"type": "nonsense"}},
    ],
)
def test_validation_errors(client, auth, body):
    assert client.post("/api/v1/prompts", json=body, headers=auth).status_code == 422
    assert client.get("/api/v1/prompts", headers=auth).json()["items"] == []


def test_valid_response_schema_and_label_validation(client, auth):
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    assert _create(client, auth, response_schema=schema)["response_schema"] == schema
    resp = client.post("/api/v1/prompts/greet/labels", json={"label": "BAD", "version": 1}, headers=auth)
    assert resp.status_code == 422


def test_project_isolation(client, auth):
    other = _project(client, "two")
    _create(client, auth, labels=["production"])
    assert client.get("/api/v1/prompts", headers=other).json()["items"] == []
    assert client.get("/api/v1/prompts/greet", headers=other).status_code == 404
    assert client.get("/api/v1/prompts/greet/versions", headers=other).status_code == 404
    assert client.post("/api/v1/prompts/greet/labels", json={"label": "x", "version": 1}, headers=other).status_code == 404
    again = _create(client, other)  # same name in another project starts at v1
    assert again["version"] == 1 and again["labels"] == []
    assert client.get("/api/v1/prompts/greet", headers=auth).json()["labels"] == ["production"]


def test_concurrent_creation_postgres():
    if not PG_URL:
        pytest.skip("MATRIX_TEST_DATABASE_URL not set")
    app = _build(PG_URL)
    Base.metadata.drop_all(app.state.db_engine)
    Base.metadata.create_all(app.state.db_engine)
    try:
        client = TestClient(app)
        auth = _project(client, "conc")
        n = 4
        barrier = threading.Barrier(n)
        results: list[int] = []
        errors: list[int] = []

        def worker(i):
            barrier.wait()
            r = client.post(
                "/api/v1/prompts",
                json={"name": "race", "type": "text", "template": f"t{i}", "labels": ["production"]},
                headers=auth,
            )
            (results if r.status_code == 201 else errors).append(r.json()["version"] if r.status_code == 201 else r.status_code)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(set(results)) == len(results)  # versions never duplicated
        assert sorted(results) == list(range(1, len(results) + 1))
        assert len(results) >= 3, (results, errors)  # retries absorb nearly all collisions
        versions = client.get("/api/v1/prompts/race/versions", headers=auth).json()["items"]
        assert len(versions) == len(results)
        assert sum("production" in v["labels"] for v in versions) == 1
    finally:
        Base.metadata.drop_all(app.state.db_engine)
        app.state.db_engine.dispose()
