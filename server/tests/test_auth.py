import hashlib
import os
import re
from datetime import timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from matrix_server import auth
from matrix_server.api import admin as admin_api
from matrix_server.auth import Principal, Role, require
from matrix_server.cli import admin as admin_cli
from matrix_server.config import Settings
from matrix_server.storage.db import Base, init_db, make_engine, make_session_factory, utcnow
from matrix_server.storage.models import ApiKey, Project

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
ADMIN_TOKEN = "admin-secret-token"
KEY_RE = re.compile(r"^mx_[0-9a-f]{8}_[A-Za-z0-9_-]{32,}$")


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'matrix.db'}")
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        eng = make_engine(PG_URL)
        Base.metadata.drop_all(eng)
    init_db(eng)
    yield eng
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


def make_app(session_factory, admin_token=ADMIN_TOKEN):
    app = FastAPI()
    app.state.settings = Settings(admin_token=admin_token)
    app.state.session_factory = session_factory
    app.include_router(admin_api.router)

    @app.get("/whoami")
    def whoami(principal: Annotated[Principal, Depends(require(Role.VIEWER))]):
        return {"project_id": principal.project_id}

    return app


# --- key format and storage ------------------------------------------------


def test_key_format_and_only_hash_stored(session):
    project, key = auth.create_project(session, "p")
    session.commit()
    assert KEY_RE.match(key)
    row = session.scalar(select(ApiKey).where(ApiKey.project_id == project.id))
    assert row.prefix == key.split("_")[1]
    assert row.key_hash == hashlib.sha256(key.encode()).hexdigest()
    secret = key.split("_", 2)[2]
    for value in (row.key_hash, row.prefix, row.id):
        assert secret not in value and key not in value


def test_keys_are_unique(session):
    project, k1 = auth.create_project(session, "p")
    k2 = auth.create_api_key(session, project.id)
    assert k1 != k2 and k1.split("_")[1] != k2.split("_")[1]


# --- authenticate ----------------------------------------------------------


def test_authenticate_ok(session):
    project, key = auth.create_project(session, "p")
    assert auth.authenticate(session, key).id == project.id


def test_authenticate_wrong_secret(session):
    _, key = auth.create_project(session, "p")
    prefix = key.split("_")[1]
    assert auth.authenticate(session, f"mx_{prefix}_{'x' * 43}") is None


@pytest.mark.parametrize("raw", ["mx_deadbeef_" + "a" * 43, "", "garbage", "mx__abc", "mx_deadbeef_"])
def test_authenticate_unknown_or_malformed(session, raw):
    auth.create_project(session, "p")
    assert auth.authenticate(session, raw) is None


def test_authenticate_revoked(session):
    _, key = auth.create_project(session, "p")
    assert auth.revoke_api_key(session, key.split("_")[1]) is True
    assert auth.authenticate(session, key) is None
    assert auth.revoke_api_key(session, "00000000") is False


def test_last_used_at_throttled(session):
    _, key = auth.create_project(session, "p")
    row = session.scalar(select(ApiKey))
    assert row.last_used_at is None
    auth.authenticate(session, key)
    first = row.last_used_at
    assert first is not None
    auth.authenticate(session, key)
    assert row.last_used_at == first
    row.last_used_at = utcnow() - timedelta(minutes=2)
    session.flush()
    auth.authenticate(session, key)
    assert row.last_used_at > first


def test_duplicate_project_name(session):
    auth.create_project(session, "p")
    with pytest.raises(auth.ProjectExistsError):
        auth.create_project(session, "p")


# --- HTTP dependency -------------------------------------------------------


@pytest.fixture
def project_key(session_factory):
    with session_factory() as s:
        project, key = auth.create_project(s, "p")
        s.commit()
        return project.id, key


@pytest.mark.parametrize(
    "headers_for",
    [
        lambda k: {"Authorization": f"Bearer {k}"},
        lambda k: {"authorization": f"bearer {k}"},
        lambda k: {"x-matrix-api-key": k},
        lambda k: {"X-Matrix-Api-Key": k},
    ],
)
def test_current_project_header_variants(session_factory, project_key, headers_for):
    project_id, key = project_key
    client = TestClient(make_app(session_factory))
    resp = client.get("/whoami", headers=headers_for(key))
    assert resp.status_code == 200
    assert resp.json() == {"project_id": project_id}


def test_current_project_missing_or_invalid_does_not_leak(session_factory, project_key):
    _, key = project_key
    client = TestClient(make_app(session_factory))
    resp = client.get("/whoami")
    assert resp.status_code == 401
    bad = key[:-4] + "zzzz"
    for headers in (
        {"Authorization": f"Bearer {bad}"},
        {"x-matrix-api-key": bad},
        {"Authorization": "Basic x"},
    ):
        resp = client.get("/whoami", headers=headers)
        assert resp.status_code == 401
        assert bad not in resp.text and key.split("_")[1] not in resp.text
        assert resp.json() == {"detail": "invalid or missing credentials"}


def test_revoked_key_rejected_over_http(session_factory, project_key):
    _, key = project_key
    with session_factory() as s:
        auth.revoke_api_key(s, key.split("_")[1])
        s.commit()
    client = TestClient(make_app(session_factory))
    assert client.get("/whoami", headers={"Authorization": f"Bearer {key}"}).status_code == 401


# --- admin API -------------------------------------------------------------


def test_admin_forbidden_when_token_unset(session_factory):
    client = TestClient(make_app(session_factory, admin_token=None))
    resp = client.get("/api/v1/admin/projects", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 403
    resp = client.post("/api/v1/admin/projects", json={"name": "x"})
    assert resp.status_code == 403


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": ADMIN_TOKEN}])
def test_admin_unauthorized_wrong_token(session_factory, headers):
    client = TestClient(make_app(session_factory))
    assert client.get("/api/v1/admin/projects", headers=headers).status_code == 401


def test_admin_flow(session_factory):
    client = TestClient(make_app(session_factory))
    h = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    resp = client.post("/api/v1/admin/projects", json={"name": "demo"}, headers=h)
    assert resp.status_code == 201
    body = resp.json()
    assert body["project"]["name"] == "demo" and KEY_RE.match(body["api_key"])
    pid = body["project"]["id"]

    assert client.post("/api/v1/admin/projects", json={"name": "demo"}, headers=h).status_code == 409
    assert client.post("/api/v1/admin/projects", json={"name": ""}, headers=h).status_code == 422

    resp = client.post(f"/api/v1/admin/projects/{pid}/keys", headers=h)
    assert resp.status_code == 201
    key2 = resp.json()["api_key"]
    assert key2 != body["api_key"]
    assert client.post("/api/v1/admin/projects/nope/keys", headers=h).status_code == 404

    for key in (body["api_key"], key2):
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {key}"})
        assert resp.json() == {"project_id": pid}

    resp = client.get("/api/v1/admin/projects", headers=h)
    items = resp.json()["items"]
    assert [(i["id"], i["name"]) for i in items] == [(pid, "demo")]
    assert "created_at" in items[0] and "api_key" not in items[0]


def test_admin_key_cannot_be_used_as_project_key(session_factory, project_key):
    client = TestClient(make_app(session_factory))
    assert client.get("/whoami", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}).status_code == 401


# --- CLI -------------------------------------------------------------------


def test_cli_create_project_create_and_revoke_key(tmp_path, monkeypatch, capsys):
    db_url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("MATRIX_DATABASE_URL", db_url)

    admin_cli(["create-project", "demo"])
    out = capsys.readouterr().out
    pid = re.search(r"project_id: (\w+)", out).group(1)
    key = re.search(r"api_key: (\S+)", out).group(1)
    assert KEY_RE.match(key)

    with pytest.raises(SystemExit) as exc:
        admin_cli(["create-project", "demo"])
    assert exc.value.code == 1

    admin_cli(["create-key", pid])
    key2 = re.search(r"api_key: (\S+)", capsys.readouterr().out).group(1)

    admin_cli(["revoke-key", key.split("_")[1]])
    assert "revoked" in capsys.readouterr().out

    engine = make_engine(db_url)
    with make_session_factory(engine)() as s:
        assert auth.authenticate(s, key) is None
        assert auth.authenticate(s, key2).id == pid
        assert s.scalar(select(Project.name)) == "demo"
        dump = " ".join(str(v) for row in s.execute(text("select * from api_keys")) for v in row)
        assert key.split("_", 2)[2] not in dump and key2.split("_", 2)[2] not in dump
    engine.dispose()


def test_sqlite_foreign_keys_enforced(session):
    from sqlalchemy.exc import IntegrityError

    session.add(ApiKey(project_id="missing", prefix="abcdef01", key_hash="0" * 64))
    with pytest.raises(IntegrityError):
        session.flush()
