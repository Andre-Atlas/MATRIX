"""G0 security tests: users, sessions, login throttling, RBAC on every route, members, keys, audit."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from otlp_payloads import full_request
from rbac_helpers import (
    PASSWORD,
    add_membership,
    make_user,
    member_headers,
    session_headers,
    session_token,
)
from sqlalchemy import select, text

from matrix_server import auth, users
from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.projects import PURGE_SPANS_JOB
from matrix_server.storage.db import Base, make_engine, utcnow
from matrix_server.storage.models import ApiKey, AuditLog, Dataset, Job, LoginAttempt, Membership, WebSession
from matrix_server.workers import handlers, online_handlers

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
ADMIN = {"Authorization": "Bearer adm"}
PB = {"Content-Type": "application/x-protobuf"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _drop(url):
    eng = make_engine(url)
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path}/m.db"
        return
    if not PG_URL:
        pytest.skip("MATRIX_TEST_DATABASE_URL not set")
    _drop(PG_URL)
    yield PG_URL
    _drop(PG_URL)


def _make(url, **settings):
    app = create_app(Settings(database_url=url, admin_token="adm", **settings))
    return app, TestClient(app)


@pytest.fixture
def env(db_url):
    app, client = _make(db_url)
    try:
        yield app, client
    finally:
        app.state.db_engine.dispose()


def _factory(app):
    return app.state.session_factory


def _project(client, name="one", owner_email=None):
    body = {"name": name, **({"owner_email": owner_email} if owner_email else {})}
    resp = client.post("/api/v1/admin/projects", json=body, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    return resp.json()["project"]["id"], resp.json()["api_key"]


def _key(app, project_id, scopes):
    with _factory(app)() as s:
        plaintext = auth.create_api_key(s, project_id, name="t", scopes=scopes)
        s.commit()
    return {"Authorization": f"Bearer {plaintext}"}


def _create_user(client, email="ana@example.com", password=PASSWORD, name="Ana"):
    resp = client.post("/api/v1/admin/users", json={"email": email, "name": name, "password": password}, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(client, email="ana@example.com", password=PASSWORD, **headers):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=headers)


def _bearer(token, project_id=None):
    h = {"Authorization": f"Bearer {token}"}
    if project_id:
        h["X-Matrix-Project"] = project_id
    return h


# --- route inventory ---------------------------------------------------------------------------------


def _routes(app):
    for r in app.routes:
        if hasattr(r, "effective_route_contexts"):
            yield from r.effective_route_contexts()
        elif isinstance(r, APIRoute):
            yield r


def _guards(dependant, out=None):
    out = [] if out is None else out
    for d in dependant.dependencies:
        if hasattr(d.call, "rbac"):
            out.append(d.call.rbac)
        if d.call is auth.require_admin:
            out.append("platform")
        _guards(d, out)
    return out


def _project_routes(app):
    """(method, path, spec) of every mutating route guarded by a project-scoped `require`."""
    found = []
    for route in _routes(app):
        specs = [g for g in _guards(route.dependant) if g != "platform" and g.project_from == "project"]
        for method in route.methods & MUTATING:
            for spec in specs:
                found.append((method, route.path, spec))
    return found


def _fill(path, project_id):
    return re.sub(r"\{(\w+)(:\w+)?\}", lambda m: project_id if m.group(1) == "project_id" else "nope", path)


def test_every_mutating_route_declares_rbac(tmp_path):
    app, _ = _make(f"sqlite:///{tmp_path}/m.db")
    public = {("POST", "/api/v1/auth/login")}
    seen = 0
    for route in _routes(app):
        if not (route.path.startswith("/api/v1/") or route.path == "/v1/traces"):
            continue
        guards = _guards(route.dependant)
        for method in route.methods:
            if (method, route.path) in public:
                continue
            seen += 1
            if route.path.startswith("/api/v1/admin/"):
                assert "platform" in guards, (method, route.path)
            else:
                assert any(g != "platform" for g in guards), f"{method} {route.path} has no require(...)"
            if method in MUTATING and not route.path.startswith(("/api/v1/admin/", "/api/v1/auth/")):
                specs = [g for g in guards if g != "platform"]
                # Writes need member (or a write/ingest scope); only session routes have no project.
                assert all(
                    s.project_from == "none" or s.min_role >= auth.Role.MEMBER or s.scope in ("write", "ingest")
                    for s in specs
                ), (method, route.path)
    assert seen > 40


# --- RBAC on every route family ------------------------------------------------------------------------


def test_viewer_cannot_write_anywhere(env):
    app, client = env
    pid, _ = _project(client)
    viewer = member_headers(_factory(app), pid, auth.Role.VIEWER)
    routes = _project_routes(app)
    assert len(routes) > 30
    for method, path, _spec in routes:
        resp = client.request(method, _fill(path, pid), json={}, headers=viewer)
        assert resp.status_code == 403, (method, path, resp.status_code, resp.text)
        assert resp.json()["detail"].startswith("requires ")
    # ...but reads work
    for path in ("/api/v1/traces", "/api/v1/datasets", "/api/v1/prompts", "/api/v1/rules", "/api/v1/alerts"):
        assert client.get(path, headers=viewer).status_code == 200, path


def test_member_cannot_reach_admin_routes(env):
    app, client = env
    pid, _ = _project(client)
    member = member_headers(_factory(app), pid, auth.Role.MEMBER)
    for method, path, spec in _project_routes(app):
        if spec.min_role >= auth.Role.ADMIN:
            resp = client.request(method, _fill(path, pid), json={}, headers=member)
            assert resp.status_code == 403, (method, path)
    for path in ("members", "keys", "audit-log"):
        assert client.get(f"/api/v1/projects/{pid}/{path}", headers=member).status_code == 403


def test_read_key_cannot_write(env):
    app, client = env
    pid, _ = _project(client)
    read_key = _key(app, pid, ["read"])
    for method, path, _spec in _project_routes(app):
        resp = client.request(method, _fill(path, pid), json={}, headers=read_key)
        assert resp.status_code == 403, (method, path, resp.status_code)
    assert client.get("/api/v1/traces", headers=read_key).status_code == 200


def test_key_never_reaches_admin_routes(env):
    app, client = env
    pid, full_key = _project(client)
    headers = {"Authorization": f"Bearer {full_key}"}
    admin_routes = [(m, p) for m, p, s in _project_routes(app) if s.min_role >= auth.Role.ADMIN]
    assert {("POST", "/api/v1/rules"), ("POST", "/api/v1/projects/{project_id}/keys")} <= set(admin_routes)
    for method, path in admin_routes:
        resp = client.request(method, _fill(path, pid), json={}, headers=headers)
        assert resp.status_code == 403, (method, path)
    for path in ("members", "keys", "audit-log"):
        assert client.get(f"/api/v1/projects/{pid}/{path}", headers=headers).status_code == 403
    # Session-only routes refuse keys too.
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 403
    assert client.get("/api/v1/projects", headers=headers).status_code == 403


def test_write_key_writes_and_production_label_is_admin_only(env):
    app, client = env
    pid, _ = _project(client)
    write_key = _key(app, pid, ["write"])  # write implies read
    prompt = {"name": "greet", "type": "text", "template": "hi"}
    assert client.post("/api/v1/prompts", json=prompt, headers=write_key).status_code == 201
    assert client.get("/api/v1/prompts", headers=write_key).status_code == 200
    move = {"label": "production", "version": 1}
    assert client.post("/api/v1/prompts/greet/labels", json=move, headers=write_key).status_code == 403
    assert client.post("/api/v1/prompts", json={**prompt, "labels": ["production"]}, headers=write_key).status_code == 403
    assert client.post("/api/v1/prompts/greet/labels", json={"label": "staging", "version": 1}, headers=write_key).status_code == 200
    member = member_headers(_factory(app), pid, auth.Role.MEMBER)
    assert client.post("/api/v1/prompts/greet/labels", json=move, headers=member).status_code == 403
    admin = member_headers(_factory(app), pid, auth.Role.ADMIN)
    assert client.post("/api/v1/prompts/greet/labels", json=move, headers=admin).status_code == 200


def test_ingest_scope(env):
    app, client = env
    pid, _ = _project(client)
    body = full_request().SerializeToString()
    ingest_key = _key(app, pid, ["ingest"])
    assert client.post("/v1/traces", content=body, headers={**PB, **ingest_key}).status_code == 200
    assert client.get("/api/v1/traces", headers=ingest_key).status_code == 403
    assert client.post("/api/v1/datasets", json={"name": "d"}, headers=ingest_key).status_code == 403
    read_key = _key(app, pid, ["read"])
    resp = client.post("/v1/traces", content=body, headers={**PB, **read_key})
    assert resp.status_code == 403 and resp.json()["detail"] == "requires scope ingest"
    factory = _factory(app)
    assert client.post("/v1/traces", content=body, headers={**PB, **member_headers(factory, pid, "member")}).status_code == 200
    assert client.post("/v1/traces", content=body, headers={**PB, **member_headers(factory, pid, "viewer")}).status_code == 403


def test_legacy_keys_keep_all_scopes(env):
    app, client = env
    _pid, key = _project(client)
    with _factory(app)() as s:  # a key row from before G0: scopes left to the column default
        s.execute(text("UPDATE api_keys SET scopes = DEFAULT" if s.get_bind().dialect.name != "sqlite"
                       else "UPDATE api_keys SET scopes = '[\"ingest\", \"read\", \"write\"]'"))
        s.commit()
        assert sorted(s.scalar(select(ApiKey.scopes))) == ["ingest", "read", "write"]
    for i, headers in enumerate(({"Authorization": f"Bearer {key}"}, {"x-matrix-api-key": key})):
        assert client.post("/api/v1/datasets", json={"name": f"d{i}"}, headers=headers).status_code == 201
    assert auth.key_scopes(None) == auth.ALL_SCOPES


# --- credentials and project selection ------------------------------------------------------------------


def test_credential_errors(env):
    app, client = env
    pid, key = _project(client)
    resp = client.get("/api/v1/traces")
    assert resp.status_code == 401 and resp.headers["www-authenticate"] == "Bearer"
    assert resp.json() == {"detail": "invalid or missing credentials"}
    assert client.get("/api/v1/traces", headers=_bearer("mxs_" + "x" * 43, pid)).status_code == 401
    user = make_user(_factory(app))
    add_membership(_factory(app), user, pid, "viewer")
    token = session_token(_factory(app), user)
    resp = client.get("/api/v1/traces", headers=_bearer(token))
    assert resp.status_code == 400 and resp.json()["detail"] == "X-Matrix-Project required"
    # A key may not name another project.
    other, _ = _project(client, "two")
    assert client.get("/api/v1/traces", headers={"Authorization": f"Bearer {key}", "X-Matrix-Project": other}).status_code == 404
    assert client.get("/api/v1/traces", headers={"Authorization": f"Bearer {key}", "X-Matrix-Project": pid}).status_code == 200


def test_session_of_user_a_on_project_b_is_404(env):
    app, client = env
    factory = _factory(app)
    pa, _ = _project(client, "a")
    pb, _ = _project(client, "b")
    user = make_user(factory)
    add_membership(factory, user, pa, "owner")
    token = session_token(factory, user)
    assert client.get("/api/v1/traces", headers=_bearer(token, pa)).status_code == 200
    for method, path in [("GET", "/api/v1/traces"), ("POST", "/api/v1/datasets"), ("GET", "/api/v1/rules")]:
        resp = client.request(method, path, json={"name": "x"}, headers=_bearer(token, pb))
        assert resp.status_code == 404 and resp.json() == {"detail": "project not found"}
    for path in ("members", "keys", "audit-log"):
        assert client.get(f"/api/v1/projects/{pb}/{path}", headers=_bearer(token)).status_code == 404
        # header and path disagree
        assert client.get(f"/api/v1/projects/{pb}/{path}", headers=_bearer(token, pa)).status_code == 404
    assert client.delete(f"/api/v1/projects/{pb}", headers=_bearer(token)).status_code == 404
    assert client.get("/api/v1/traces", headers=_bearer(token, "does-not-exist")).status_code == 404


# --- login, sessions, passwords ------------------------------------------------------------------------


def test_login_me_logout_and_token_reuse(env):
    _app, client = env
    user = _create_user(client)
    pid, _ = _project(client, "one", owner_email="ANA@example.com")
    resp = _login(client, " Ana@Example.com ")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    token = body["token"]
    assert re.fullmatch(r"mxs_[A-Za-z0-9_-]{43}", token)
    assert body["user"]["id"] == user["id"] and body["user"]["last_login_at"]
    assert body["memberships"] == [{"project": {"id": pid, "name": "one"}, "role": "owner"}]
    assert body["expires_at"].endswith("Z")
    me = client.get("/api/v1/auth/me", headers=_bearer(token)).json()
    assert me["user"]["email"] == "ana@example.com" and me["memberships"][0]["role"] == "owner"
    assert client.get("/api/v1/projects", headers=_bearer(token)).json()["items"] == body["memberships"]
    assert client.get("/api/v1/traces", headers=_bearer(token, pid)).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=_bearer(token)).status_code == 204
    for path, headers in (("/api/v1/auth/me", _bearer(token)), ("/api/v1/traces", _bearer(token, pid))):
        assert client.get(path, headers=headers).status_code == 401
    assert client.post("/api/v1/auth/logout", headers=_bearer(token)).status_code == 401


def test_login_failures_are_generic(env):
    _app, client = env
    _create_user(client)
    wrong = _login(client, password="x" * 20)
    unknown = _login(client, email="nobody@example.com", password="x" * 20)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "invalid email or password"}
    assert _login(client, password=PASSWORD + "x" * 300).status_code == 401  # too long: verified, refused
    resp = client.post("/api/v1/auth/login", json={"email": "ana@example.com"})
    assert resp.status_code == 422 and "input" not in resp.text


class _SpyHasher:
    def __init__(self, real):
        self.real = real
        self.verified = []

    def verify(self, hashed, password):
        self.verified.append(hashed)
        return self.real.verify(hashed, password)

    def __getattr__(self, name):
        return getattr(self.real, name)


def test_unknown_user_costs_one_verification(env, monkeypatch):
    _app, client = env
    _create_user(client)
    spy = _SpyHasher(users.password_hasher)
    monkeypatch.setattr(users, "password_hasher", spy)
    _login(client, password="wrong password!!")
    _login(client, email="ghost@example.com", password="wrong password!!")
    assert len(spy.verified) == 2
    known, unknown = spy.verified
    assert unknown == users.dummy_hash() and known != unknown
    assert known.startswith("$argon2id$") and unknown.startswith("$argon2id$")


def test_rehash_on_login_when_parameters_change(env):
    app, client = env
    from argon2 import PasswordHasher

    _create_user(client)
    weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)
    with _factory(app)() as s:
        s.execute(text("UPDATE users SET password_hash = :h"), {"h": weak})
        s.commit()
    assert _login(client).status_code == 200
    with _factory(app)() as s:
        stored = s.scalar(text("SELECT password_hash FROM users"))
    assert stored != weak and not users.password_hasher.check_needs_rehash(stored)


def test_lockout_retry_after_and_reset(env):
    app, client = env
    _create_user(client)
    for _ in range(users.PAIR_MAX_FAILURES):
        assert _login(client, password="bad password 1").status_code == 401
    locked = _login(client)  # even the right password is refused while locked
    assert locked.status_code == 429 and locked.headers["retry-after"] == "60"
    # An unknown e-mail is locked exactly the same way.
    for _ in range(users.PAIR_MAX_FAILURES):
        assert _login(client, email="ghost@example.com", password="bad password 1").status_code == 401
    assert _login(client, email="ghost@example.com", password="bad password 1").status_code == 429

    def expire_locks():
        with _factory(app)() as s:
            for row in s.scalars(select(LoginAttempt)):
                if row.locked_until is not None:
                    row.locked_until = utcnow() - timedelta(seconds=1)
            s.commit()

    expire_locks()
    for _ in range(users.PAIR_MAX_FAILURES):
        _login(client, password="bad password 2")
    again = _login(client)
    assert again.status_code == 429 and again.headers["retry-after"] == "120"  # escalated
    expire_locks()
    assert _login(client).status_code == 200  # success resets the pair
    pair = users.attempt_key("ana@example.com", "testclient")
    with _factory(app)() as s:
        assert s.get(LoginAttempt, pair) is None
        dump = " ".join(str(v) for row in s.execute(text("SELECT * FROM login_attempts")) for v in row)
    assert "ana@example.com" not in dump and "ghost" not in dump


def test_ip_limit_across_emails_and_trusted_proxy_header(db_url):
    app, client = _make(db_url)
    try:
        for i in range(users.IP_MAX_FAILURES):
            assert _login(client, email=f"spray{i}@example.com", password="bad password!").status_code == 401
        resp = _login(client, email="fresh@example.com", password="bad password!")
        assert resp.status_code == 429 and int(resp.headers["retry-after"]) > 0
        # Without the setting, a forged header changes nothing.
        resp = _login(client, email="fresh@example.com", password="bad password!", **{"X-Forwarded-For": "203.0.113.9"})
        assert resp.status_code == 429
    finally:
        app.state.db_engine.dispose()
    app, client = _make(db_url, trusted_proxy_header="X-Forwarded-For")
    try:
        _create_user(client, email="bia@example.com")
        proxied = {"X-Forwarded-For": "198.51.100.1, 203.0.113.7"}
        for _ in range(users.PAIR_MAX_FAILURES):
            assert _login(client, email="bia@example.com", password="bad password!", **proxied).status_code == 401
        assert _login(client, email="bia@example.com", **proxied).status_code == 429
        # Another real client behind the same proxy is not locked by the first one's pair.
        assert _login(client, email="bia@example.com", **{"X-Forwarded-For": "203.0.113.8"}).status_code == 200
        with _factory(app)() as s:
            ips = set(s.scalars(select(WebSession.ip)))
        assert ips == {"203.0.113.8"}
    finally:
        app.state.db_engine.dispose()


def test_email_limit_across_ips(db_url):
    app, client = _make(db_url, trusted_proxy_header="X-Real-IP")
    try:
        _create_user(client)
        for i in range(users.EMAIL_MAX_FAILURES):
            assert _login(client, password="bad password!", **{"X-Real-IP": f"192.0.2.{i + 1}"}).status_code == 401
        assert _login(client, **{"X-Real-IP": "192.0.2.200"}).status_code == 429
    finally:
        app.state.db_engine.dispose()


def test_hasher_saturation_returns_503(env, monkeypatch):
    _app, client = env
    _create_user(client)
    users.dummy_hash()
    monkeypatch.setattr(users, "HASH_WAIT_SECONDS", 0.01)
    users.configure_hash_concurrency(1)
    try:
        assert users._hash_slots.acquire(timeout=1)
        resp = _login(client)
        assert resp.status_code == 503 and resp.headers["retry-after"] == "1"
        users._hash_slots.release()
        assert _login(client).status_code == 200
    finally:
        users.configure_hash_concurrency(users.DEFAULT_HASH_CONCURRENCY)


def test_sessions_store_only_hashes(env):
    app, client = env
    _create_user(client)
    token = _login(client).json()["token"]
    with _factory(app)() as s:
        tables = [t for t in Base.metadata.sorted_tables]
        dump = " ".join(str(v) for t in tables for row in s.execute(t.select()) for v in row)
        ids = list(s.scalars(select(WebSession.id)))
    assert token not in dump and token[4:] not in dump and PASSWORD not in dump
    assert ids == [hashlib.sha256(token.encode()).hexdigest()]


def test_session_idle_absolute_expiry_and_sliding(env):
    app, client = env
    factory = _factory(app)
    pid, _ = _project(client)
    user = make_user(factory)
    add_membership(factory, user, pid, "viewer")
    token = session_token(factory, user)
    sid = hashlib.sha256(token.encode()).hexdigest()

    def set_row(**values):
        with factory() as s:
            row = s.get(WebSession, sid)
            for k, v in values.items():
                setattr(row, k, v)
            s.commit()

    def last_seen():
        with factory() as s:
            return s.get(WebSession, sid).last_seen_at

    first = last_seen()
    assert client.get("/api/v1/traces", headers=_bearer(token, pid)).status_code == 200
    assert last_seen() == first  # written at most once a minute
    set_row(last_seen_at=utcnow() - timedelta(minutes=2))
    before = last_seen()
    assert client.get("/api/v1/traces", headers=_bearer(token, pid)).status_code == 200
    assert last_seen() > before  # slid
    set_row(last_seen_at=utcnow() - timedelta(days=7, seconds=1))
    assert client.get("/api/v1/traces", headers=_bearer(token, pid)).status_code == 401
    set_row(last_seen_at=utcnow(), expires_at=utcnow() - timedelta(seconds=1))
    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 401


def test_password_change_revokes_other_sessions(env):
    _app, client = env
    _create_user(client)
    a = _login(client).json()["token"]
    b = _login(client).json()["token"]
    url = "/api/v1/auth/password"
    assert client.post(url, json={"current_password": "wrong password", "new_password": "n" * 12}, headers=_bearer(a)).status_code == 403
    for bad in ("short", "ana@example.com".upper(), "x" * 257):
        resp = client.post(url, json={"current_password": PASSWORD, "new_password": bad}, headers=_bearer(a))
        assert resp.status_code == 422 and bad not in resp.text
    new = "a brand new passphrase"
    assert client.post(url, json={"current_password": PASSWORD, "new_password": new}, headers=_bearer(a)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=_bearer(a)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=_bearer(b)).status_code == 401
    assert _login(client).status_code == 401
    assert _login(client, password=new).status_code == 200


def test_list_and_revoke_own_sessions(env):
    _app, client = env
    _create_user(client)
    _create_user(client, email="bob@example.com")
    a = _login(client).json()["token"]
    b = _login(client, **{"User-Agent": "phone"}).json()["token"]
    bob = _login(client, email="bob@example.com").json()["token"]
    items = client.get("/api/v1/auth/sessions", headers=_bearer(a)).json()["items"]
    assert len(items) == 2 and sum(i["current"] for i in items) == 1
    other = next(i for i in items if not i["current"])
    assert other["user_agent"] == "phone" and b not in str(items)
    bob_sid = hashlib.sha256(bob.encode()).hexdigest()
    assert client.delete(f"/api/v1/auth/sessions/{bob_sid}", headers=_bearer(a)).status_code == 404
    assert client.delete(f"/api/v1/auth/sessions/{other['id']}", headers=_bearer(a)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=_bearer(b)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(bob)).status_code == 200
    assert len(client.get("/api/v1/auth/sessions", headers=_bearer(a)).json()["items"]) == 1


def test_password_rules():
    for bad in ("", "short", "x" * 257):
        with pytest.raises(users.UserError):
            users.validate_password(bad)
    with pytest.raises(users.UserError):
        users.validate_password("Someone@Example.com", "someone@example.com")
    users.validate_password("x" * 12, "someone@example.com")
    users.validate_password("x" * 256)
    for bad in ("no-at", "a@b", "a b@example.com", "@example.com", "a@@example.com"):
        with pytest.raises(users.UserError):
            users.validate_email(bad)
    assert users.validate_email(" A@Example.COM ") == "a@example.com"
    assert users.mask_email("Ana.Silva@Example.com") == "a***@example.com"
    assert users.password_hasher.type.name == "ID"


# --- admin API and CLI --------------------------------------------------------------------------------------


def test_admin_users_and_project_owner(env):
    app, client = env
    user = _create_user(client)
    assert set(user) == {"id", "email", "name", "created_at", "last_login_at"}
    dup = client.post("/api/v1/admin/users", json={"email": "ANA@example.com", "password": PASSWORD}, headers=ADMIN)
    assert dup.status_code == 409
    weak = client.post("/api/v1/admin/users", json={"email": "c@example.com", "password": "tiny pw"}, headers=ADMIN)
    assert weak.status_code == 422 and "tiny pw" not in weak.text
    same = client.post("/api/v1/admin/users", json={"email": "longname@example.com", "password": "longname@example.com"}, headers=ADMIN)
    assert same.status_code == 422
    assert client.post("/api/v1/admin/users", json={"email": "d@example.com", "password": PASSWORD}).status_code == 401
    missing = client.post("/api/v1/admin/projects", json={"name": "p", "owner_email": "ghost@example.com"}, headers=ADMIN)
    assert missing.status_code == 404
    pid, _key_unused = _project(client, "p", owner_email="ana@example.com")
    with _factory(app)() as s:
        assert s.scalar(select(Membership.role).where(Membership.project_id == pid)) == "owner"
        assert sorted(s.scalar(select(ApiKey.scopes).where(ApiKey.project_id == pid))) == ["ingest", "read", "write"]


def test_cli_create_user_project_and_member(tmp_path, monkeypatch, capsys):
    import io

    from matrix_server.cli import admin as admin_cli

    db_url = f"sqlite:///{tmp_path}/cli.db"
    monkeypatch.setenv("MATRIX_DATABASE_URL", db_url)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    admin_cli(["create-user", "ana@example.com", "--name", "Ana"])
    out = capsys.readouterr()
    assert "user_id:" in out.out and PASSWORD not in out.out + out.err
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    with pytest.raises(SystemExit):
        admin_cli(["create-user", "bob@example.com"])
    admin_cli(["create-project", "demo", "--owner-email", "ana@example.com"])
    pid = re.search(r"project_id: (\w+)", capsys.readouterr().out).group(1)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    admin_cli(["create-user", "bob@example.com"])
    capsys.readouterr()
    admin_cli(["add-member", pid, "bob@example.com", "--role", "viewer"])
    assert "viewer" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        admin_cli(["add-member", pid, "bob@example.com"])
    engine = make_engine(db_url)
    with engine.connect() as c:
        roles = dict(c.execute(text("SELECT u.email, m.role FROM memberships m JOIN users u ON u.id = m.user_id")).all())
    engine.dispose()
    assert roles == {"ana@example.com": "owner", "bob@example.com": "viewer"}


# --- projects ---------------------------------------------------------------------------------------------------


def test_user_projects_create_and_delete(env):
    app, client = env
    factory = _factory(app)
    user = make_user(factory)
    token = session_token(factory, user)
    resp = client.post("/api/v1/projects", json={"name": "mine"}, headers=_bearer(token))
    assert resp.status_code == 201 and resp.json()["role"] == "owner"
    pid = resp.json()["project"]["id"]
    assert client.post("/api/v1/projects", json={"name": "mine"}, headers=_bearer(token)).status_code == 409
    assert [m["project"]["id"] for m in client.get("/api/v1/projects", headers=_bearer(token)).json()["items"]] == [pid]
    h = _bearer(token, pid)
    assert client.post("/api/v1/datasets", json={"name": "d"}, headers=h).status_code == 201
    assert client.post("/v1/traces", content=full_request().SerializeToString(), headers={**PB, **h}).status_code == 200
    admin = member_headers(factory, pid, "admin")
    assert client.delete(f"/api/v1/projects/{pid}", headers=admin).status_code == 403
    assert client.delete(f"/api/v1/projects/{pid}", headers=h).status_code == 204
    with factory() as s:
        assert s.scalars(select(Dataset)).all() == []
        assert s.scalars(select(Membership).where(Membership.project_id == pid)).all() == []
        job = s.scalars(select(Job).where(Job.kind == PURGE_SPANS_JOB)).one()
        assert s.scalars(select(AuditLog).where(AuditLog.action == "project.delete")).one().project_id == pid
    assert client.get("/api/v1/traces", headers=h).status_code == 404
    store = app.state.span_store
    assert store.list_traces(pid)[0]
    online_handlers.set_span_store(store)
    try:
        with factory() as s:
            handlers.purge_project_spans(s, job)
    finally:
        online_handlers.set_span_store(None)
    assert store.list_traces(pid)[0] == []


def test_project_creation_can_be_disabled(db_url):
    app, client = _make(db_url, allow_project_creation=False)
    try:
        token = session_token(_factory(app), make_user(_factory(app)))
        assert client.post("/api/v1/projects", json={"name": "x"}, headers=_bearer(token)).status_code == 403
    finally:
        app.state.db_engine.dispose()


# --- members ------------------------------------------------------------------------------------------------------


def test_member_rules_and_last_owner(env):
    app, client = env
    factory = _factory(app)
    owner_id = make_user(factory, "owner@example.com")
    pid, _ = _project(client, owner_email="owner@example.com")
    owner = session_headers(factory, owner_id, pid)
    admin_id = make_user(factory, "admin@example.com")
    add_membership(factory, admin_id, pid, "admin")
    admin = session_headers(factory, admin_id, pid)
    make_user(factory, "carol@example.com")
    base = f"/api/v1/projects/{pid}/members"

    assert client.post(base, json={"email": "ghost@example.com", "role": "viewer"}, headers=admin).status_code == 404
    assert client.post(base, json={"email": "carol@example.com", "role": "admin"}, headers=admin).status_code == 403
    added = client.post(base, json={"email": "Carol@example.com", "role": "viewer"}, headers=admin)
    assert added.status_code == 201 and added.json()["role"] == "viewer"
    carol = added.json()["user"]["id"]
    assert client.post(base, json={"email": "carol@example.com", "role": "member"}, headers=admin).status_code == 409
    assert client.patch(f"{base}/{carol}", json={"role": "member"}, headers=admin).json()["role"] == "member"
    assert client.patch(f"{base}/{carol}", json={"role": "admin"}, headers=admin).status_code == 403
    assert client.patch(f"{base}/{carol}", json={"role": "boss"}, headers=admin).status_code == 422
    # admin never touches an owner
    assert client.patch(f"{base}/{owner_id}", json={"role": "viewer"}, headers=admin).status_code == 403
    assert client.delete(f"{base}/{owner_id}", headers=admin).status_code == 403
    # last owner can be neither demoted nor removed, even by themself
    assert client.patch(f"{base}/{owner_id}", json={"role": "admin"}, headers=owner).status_code == 409
    assert client.delete(f"{base}/{owner_id}", headers=owner).status_code == 409
    assert client.patch(f"{base}/{carol}", json={"role": "owner"}, headers=owner).status_code == 200
    assert client.patch(f"{base}/{owner_id}", json={"role": "admin"}, headers=owner).status_code == 200
    items = {m["user"]["email"]: m["role"] for m in client.get(base, headers=admin).json()["items"]}
    assert items == {"owner@example.com": "admin", "admin@example.com": "admin", "carol@example.com": "owner"}
    assert client.delete(f"{base}/nope", headers=admin).status_code == 404


def test_member_removal_revokes_nothing_else(env):
    app, client = env
    factory = _factory(app)
    pa, _ = _project(client, "a")
    pb, _ = _project(client, "b")
    dave = make_user(factory, "dave@example.com")
    add_membership(factory, dave, pa, "admin")
    add_membership(factory, dave, pb, "member")
    token = session_token(factory, dave)
    created = client.post(f"/api/v1/projects/{pa}/keys", json={"name": "ci", "scopes": ["read"]}, headers=_bearer(token, pa))
    assert created.status_code == 201
    dave_key = {"Authorization": f"Bearer {created.json()['api_key']}"}
    with factory() as s:
        assert s.get(ApiKey, created.json()["id"]).created_by == dave
    remover = member_headers(factory, pa, "owner")
    assert client.delete(f"/api/v1/projects/{pa}/members/{dave}", headers=remover).status_code == 204
    assert client.get("/api/v1/traces", headers=_bearer(token, pa)).status_code == 404
    assert client.get("/api/v1/traces", headers=_bearer(token, pb)).status_code == 200  # session survives
    assert client.get("/api/v1/traces", headers=dave_key).status_code == 200  # their key survives


# --- keys --------------------------------------------------------------------------------------------------------------


def test_keys_crud_and_isolation(env):
    app, client = env
    factory = _factory(app)
    pa, key_a = _project(client, "a")
    pb, _ = _project(client, "b")
    admin_a = member_headers(factory, pa, "admin")
    admin_b = member_headers(factory, pb, "admin")
    base = f"/api/v1/projects/{pa}/keys"
    assert client.post(base, json={"name": "x", "scopes": []}, headers=admin_a).status_code == 422
    assert client.post(base, json={"name": "x", "scopes": ["admin"]}, headers=admin_a).status_code == 422
    created = client.post(base, json={"name": "ingestor", "scopes": ["ingest"]}, headers=admin_a)
    assert created.status_code == 201 and set(created.json()) == {"id", "prefix", "api_key"}
    plaintext = created.json()["api_key"]
    items = client.get(base, headers=admin_a).json()["items"]
    assert {i["name"] for i in items} == {"initial", "ingestor"}
    assert plaintext not in str(items) and key_a not in str(items)
    ingestor = next(i for i in items if i["name"] == "ingestor")
    assert ingestor["scopes"] == ["ingest"] and ingestor["revoked_at"] is None
    assert client.get(f"/api/v1/projects/{pb}/keys", headers=admin_b).json()["items"][0]["name"] == "initial"
    assert len(client.get(f"/api/v1/projects/{pb}/keys", headers=admin_b).json()["items"]) == 1
    assert client.delete(f"/api/v1/projects/{pb}/keys/{ingestor['id']}", headers=admin_b).status_code == 404
    members_b = client.get(f"/api/v1/projects/{pb}/members", headers=admin_b).json()["items"]
    assert len(members_b) == 1  # project A's members never show up
    body = full_request().SerializeToString()
    h = {**PB, "Authorization": f"Bearer {plaintext}"}
    assert client.post("/v1/traces", content=body, headers=h).status_code == 200
    assert client.delete(f"{base}/{ingestor['id']}", headers=admin_a).status_code == 204
    assert client.delete(f"{base}/{ingestor['id']}", headers=admin_a).status_code == 204  # idempotent
    assert client.post("/v1/traces", content=body, headers=h).status_code == 401


# --- audit ---------------------------------------------------------------------------------------------------------------


def test_audit_rows_written_without_secrets(env):
    app, client = env
    factory = _factory(app)
    _create_user(client)
    _create_user(client, email="eve@example.com")
    pid, initial_key = _project(client, owner_email="ana@example.com")
    _login(client, password="not the password")
    _login(client, email="ghost.user@example.com", password="not the password")
    token = _login(client, **{"User-Agent": "audit-test"}).json()["token"]
    h = _bearer(token, pid)
    base = f"/api/v1/projects/{pid}"
    key = client.post(f"{base}/keys", json={"name": "k", "scopes": ["read"]}, headers=h).json()
    client.delete(f"{base}/keys/{key['id']}", headers=h)
    eve = client.post(f"{base}/members", json={"email": "eve@example.com", "role": "viewer"}, headers=h).json()["user"]["id"]
    client.patch(f"{base}/members/{eve}", json={"role": "member"}, headers=h)
    client.delete(f"{base}/members/{eve}", headers=h)
    client.post("/api/v1/auth/logout", headers=_bearer(token))

    with factory() as s:
        actions = [r.action for r in s.scalars(select(AuditLog))]
        dump = " ".join(str(v) for row in s.execute(text("SELECT * FROM audit_log")) for v in row)
        login = s.scalars(select(AuditLog).where(AuditLog.action == "auth.login")).one()
    for action in ("auth.login", "auth.login_failed", "auth.logout", "key.create", "key.revoke",
                   "member.add", "member.update", "member.remove", "project.create", "user.create"):
        assert action in actions, action
    assert actions.count("auth.login_failed") == 2
    assert login.detail["user_agent"] == "audit-test" and login.detail["ip"] == "testclient"
    for secret in (PASSWORD, "not the password", token, token[4:], key["api_key"], initial_key, "ghost.user@example.com"):
        assert secret not in dump, secret
    assert "g***@example.com" in dump

    # Admin read API: newest first, filter, cursor; scoped to the project.
    admin = member_headers(factory, pid, "admin")
    page = client.get(f"{base}/audit-log", params={"limit": 3}, headers=admin).json()
    assert len(page["items"]) == 3 and page["next_cursor"]
    assert page["items"][0]["action"] == "member.remove"
    assert set(page["items"][0]["actor"]) == {"kind", "id"}
    seen = [i["id"] for i in page["items"]]
    while page["next_cursor"]:
        page = client.get(f"{base}/audit-log", params={"limit": 3, "cursor": page["next_cursor"]}, headers=admin).json()
        seen += [i["id"] for i in page["items"]]
    assert len(seen) == len(set(seen)) == 6
    only = client.get(f"{base}/audit-log", params={"action": "key.create"}, headers=admin).json()["items"]
    assert [i["action"] for i in only] == ["key.create"]
    assert client.get(f"{base}/audit-log", params={"cursor": "!!"}, headers=admin).status_code == 400
    other, _ = _project(client, "two")
    other_admin = member_headers(factory, other, "admin")
    assert {i["action"] for i in client.get(f"/api/v1/projects/{other}/audit-log", headers=other_admin).json()["items"]} == {"project.create"}


@pytest.mark.parametrize("kind", ["memory", "clickhouse"])
def test_span_store_delete_project(kind):
    import time
    import uuid

    from matrix_server.ingest.openinference import normalize
    from matrix_server.ingest.otlp import decode_request
    from matrix_server.storage.spans import ClickHouseSpanStore, MemorySpanStore

    if kind == "memory":
        store = MemorySpanStore()
    else:
        url = os.environ.get("MATRIX_TEST_CLICKHOUSE_URL")
        if not url:
            pytest.skip("MATRIX_TEST_CLICKHOUSE_URL not set")
        store = ClickHouseSpanStore(url, f"matrix_test_{uuid.uuid4().hex[:12]}", async_insert=False)
        store.ensure_schema()
    try:
        raw = decode_request(full_request().SerializeToString(), "application/x-protobuf")
        for pid in ("gone", "kept"):
            store.insert([normalize(r, pid, 100_000) for r in raw])
        store.delete_project("gone")
        deadline = time.time() + 10
        while store.list_traces("gone")[0] and time.time() < deadline:  # ClickHouse mutation is async
            time.sleep(0.2)
        assert store.list_traces("gone")[0] == []
        assert store.list_traces("kept")[0]
    finally:
        if kind == "clickhouse":
            store.drop_database()
