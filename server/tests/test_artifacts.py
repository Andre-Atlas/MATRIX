"""Artifact store (ADR-007, contract G0 section 2): local backend always; S3 backend against a real
S3-compatible server when `MATRIX_TEST_S3_ENDPOINT` is set (one bucket per test session).

API tests mount the artifacts router on a minimal app whose `require(...)` dependencies are replaced
by a header-driven principal (`X-Test-Project`, `X-Test-Role`), so they test this module and not
the RBAC implementation; `test_real_app_with_api_key` exercises the real app and auth end to end.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import os
import stat
import threading
import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from matrix_server.api import artifacts as artifacts_api
from matrix_server.artifacts import BlobTooLarge, InvalidKey, build_artifact_store
from matrix_server.artifacts import service as svc
from matrix_server.artifacts.local import LocalArtifactStore
from matrix_server.artifacts.models import Artifact
from matrix_server.auth import Principal, Role
from matrix_server.config import Settings
from matrix_server.limits import BodyLimitMiddleware
from matrix_server.storage.db import Base, init_db, make_engine, make_session_factory, utcnow
from matrix_server.storage.models import AuditLog, Project

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
S3_ENDPOINT = os.environ.get("MATRIX_TEST_S3_ENDPOINT")
S3_KEY = os.environ.get("MATRIX_TEST_S3_ACCESS_KEY", "matrixminio")
S3_SECRET = os.environ.get("MATRIX_TEST_S3_SECRET_KEY", "matrixminio-secret")
S3_REGION = "us-east-1"
MiB = 1024 * 1024


# --- fixtures -------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def s3_bucket():
    if not S3_ENDPOINT:
        pytest.skip("MATRIX_TEST_S3_ENDPOINT not set")
    import boto3
    from botocore.config import Config

    bucket = f"matrix-test-{uuid.uuid4().hex[:12]}"
    client = boto3.client(
        "s3", endpoint_url=S3_ENDPOINT, region_name=S3_REGION, aws_access_key_id=S3_KEY,
        aws_secret_access_key=S3_SECRET, config=Config(s3={"addressing_style": "path"}),
    )
    client.create_bucket(Bucket=bucket)
    yield bucket
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=obj["Key"])
    with contextlib.suppress(Exception):
        for up in client.list_multipart_uploads(Bucket=bucket).get("Uploads", []):
            client.abort_multipart_upload(Bucket=bucket, Key=up["Key"], UploadId=up["UploadId"])
    client.delete_bucket(Bucket=bucket)


def _s3_settings(bucket: str, **kw) -> dict:
    return dict(
        artifact_backend="s3", s3_endpoint=S3_ENDPOINT, s3_bucket=bucket, s3_region=S3_REGION,
        s3_access_key=S3_KEY, s3_secret_key=S3_SECRET, **kw,
    )


@pytest.fixture(params=["local", "s3"])
def backend(request, tmp_path):
    if request.param == "local":
        return {"artifact_backend": "local", "artifact_local_dir": str(tmp_path / "blobs")}
    return _s3_settings(request.getfixturevalue("s3_bucket"))


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path}/m.db"
        return
    if not PG_URL:
        pytest.skip("MATRIX_TEST_DATABASE_URL not set")
    eng = make_engine(PG_URL)
    Base.metadata.drop_all(eng)
    eng.dispose()
    yield PG_URL
    eng = make_engine(PG_URL)
    Base.metadata.drop_all(eng)
    eng.dispose()


def _min_role(call) -> Role:
    nonlocals = inspect.getclosurevars(call).nonlocals
    if "min_role" in nonlocals:
        return Role(nonlocals["min_role"])
    spec = nonlocals.get("spec") or getattr(call, "rbac", None)
    return Role(spec.min_role)


def _fake_require(min_role: Role):
    def dependency(request: Request) -> Principal:
        pid = request.headers.get("x-test-project")
        if not pid:
            raise HTTPException(status_code=401, detail="unauthenticated")
        role = Role[request.headers.get("x-test-role", "member").upper()]
        if role < min_role:
            raise HTTPException(status_code=403, detail=f"requires role {min_role.name.lower()}")
        return Principal(pid, "user", "u1", role, frozenset({"ingest", "read", "write"}))

    return dependency


class Env:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        app = FastAPI()
        app.add_middleware(
            BodyLimitMiddleware, max_body_bytes=settings.max_body_bytes,
            max_json_body_bytes=settings.max_json_body_bytes, raw_upload_limit=settings.max_artifact_bytes,
        )
        app.state.settings = settings
        self.engine = make_engine(settings.database_url)
        init_db(self.engine)
        app.state.session_factory = self.factory = make_session_factory(self.engine)
        app.include_router(artifacts_api.router)
        for route in artifacts_api.router.routes:
            for dep in route.dependant.dependencies:
                if "require" in getattr(dep.call, "__qualname__", ""):
                    app.dependency_overrides[dep.call] = _fake_require(_min_role(dep.call))
        self.app = app
        self.client = TestClient(app)
        self.store = artifacts_api.get_store(app)
        with self.factory() as s, s.begin():
            self.p1 = Project(name=f"one-{uuid.uuid4().hex[:6]}")
            self.p2 = Project(name=f"two-{uuid.uuid4().hex[:6]}")
            s.add_all([self.p1, self.p2])
        self.h1 = {"X-Test-Project": self.p1.id}
        self.h2 = {"X-Test-Project": self.p2.id}

    def upload(self, data: bytes, name: str = "f.bin", headers=None, project=None, **extra):
        h = {**(project or self.h1), "X-Filename": name, "Content-Type": "application/octet-stream"}
        h.update(headers or {})
        return self.client.post("/api/v1/artifacts", content=data, headers=h, **extra)

    def blobs(self, project_id: str) -> list[str]:
        return sorted(b.sha256 for b in self.store.iter_blobs() if b.project_id == project_id)

    def leftovers(self, project_id: str) -> list[str]:
        """Temp/staged objects and open multipart uploads of a project."""
        if isinstance(self.store, LocalArtifactStore):
            tmp = self.store.root / project_id / ".tmp"
            return sorted(p.name for p in tmp.iterdir()) if tmp.exists() else []
        client, bucket = self.store.client, self.store.bucket
        keys = [o["Key"] for o in client.list_objects_v2(Bucket=bucket, Prefix=f"{project_id}/").get("Contents", [])]
        out = [k for k in keys if k.split("/")[1] in ("tmp", "uploads")]
        with contextlib.suppress(Exception):
            ups = client.list_multipart_uploads(Bucket=bucket, Prefix=f"{project_id}/").get("Uploads", [])
            out += [u["Key"] for u in ups]
        return out

    def fetch(self, artifact_id: str, project=None, **params) -> httpx.Response:
        """Download, following a presigned redirect (S3) with a plain HTTP client."""
        resp = self.client.get(
            f"/api/v1/artifacts/{artifact_id}/download", headers=project or self.h1, params=params,
            follow_redirects=False,
        )
        return resp

    def close(self) -> None:
        self.client.close()
        self.engine.dispose()


@pytest.fixture
def make_env(db_url, backend):
    envs: list[Env] = []

    def factory(**overrides) -> Env:
        env = Env(Settings(database_url=db_url, **{**backend, **overrides}))
        envs.append(env)
        return env

    yield factory
    for env in envs:
        env.close()


@pytest.fixture
def env(make_env):
    return make_env()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def follow(env: Env, resp) -> httpx.Response:
    assert resp.status_code == 302, resp.text
    assert resp.headers["x-content-type-options"] == "nosniff"
    return httpx.get(resp.headers["location"])


def content_of(env: Env, artifact_id: str) -> bytes:
    resp = env.fetch(artifact_id)
    if resp.status_code == 302:
        resp = follow(env, resp)
    assert resp.status_code == 200, resp.text
    return resp.content


# --- API ----------------------------------------------------------------------------------------


def test_direct_upload_download_headers(env):
    data = b"hello artifact\n" * 100
    # Header values travel as latin-1; percent-encoding is the supported way to send UTF-8 names.
    resp = env.upload(data, name="r%C3%A9sum%C3%A9%20%22final%22.txt",
                      headers={"Content-Type": "text/plain; charset=utf-8", "X-Sha256": sha(data),
                               "X-Owner-Kind": "run", "X-Owner-Id": "run_1"})
    assert resp.status_code == 201, resp.text
    art = resp.json()
    assert art["name"] == 'r\u00e9sum\u00e9 "final".txt' and art["media_type"] == "text/plain"
    assert art["size"] == len(data) and art["sha256"] == sha(data) and art["verified"] is True
    assert art["owner_kind"] == "run" and art["owner_id"] == "run_1" and art["created_by"] == "user:u1"
    assert env.client.get(f"/api/v1/artifacts/{art['id']}", headers=env.h1).json() == art

    resp = env.fetch(art["id"])
    if resp.status_code == 302:
        assert resp.headers["cache-control"] == "private, no-store"
        resp = follow(env, resp)
        assert resp.headers["content-disposition"].startswith("attachment;")
    else:
        assert resp.status_code == 200
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-security-policy"] == "sandbox"
        cd = resp.headers["content-disposition"]
        assert cd.startswith('attachment; filename="r_sum_ _final_.txt"')
        assert "filename*=UTF-8''r%C3%A9sum%C3%A9%20%22final%22.txt" in cd
    assert resp.content == data


def test_body_over_json_limit_is_accepted_for_uploads(make_env):
    env = make_env(max_json_body_bytes=1000)
    data = os.urandom(3000)
    assert env.upload(data).status_code == 201
    assert env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                           content=b"{" + b" " * 2000 + b"}").status_code == 413


def test_dedup_within_project_not_across(env):
    data = os.urandom(4096)
    a = env.upload(data, name="a.bin").json()
    b = env.upload(data, name="b.bin").json()
    c = env.upload(data, name="c.bin", project=env.h2).json()
    assert a["id"] != b["id"] and a["sha256"] == b["sha256"] == c["sha256"]
    assert env.blobs(env.p1.id) == [sha(data)]
    assert env.blobs(env.p2.id) == [sha(data)]  # a second, separate blob for project two
    assert content_of(env, a["id"]) == data


def test_project_isolation(env):
    art = env.upload(b"secret").json()
    for method, path in [
        ("GET", f"/api/v1/artifacts/{art['id']}"),
        ("GET", f"/api/v1/artifacts/{art['id']}/download"),
        ("DELETE", f"/api/v1/artifacts/{art['id']}"),
        ("POST", f"/api/v1/artifacts/{art['id']}/complete"),
        ("PUT", f"/api/v1/artifacts/{art['id']}/content"),
    ]:
        assert env.client.request(method, path, headers=env.h2, content=b"x").status_code == 404, path
    assert env.client.get("/api/v1/artifacts", headers=env.h2).json()["items"] == []
    assert content_of(env, art["id"]) == b"secret"


def test_roles(env):
    art = env.upload(b"x").json()
    viewer = {**env.h1, "X-Test-Role": "viewer"}
    assert env.client.get(f"/api/v1/artifacts/{art['id']}", headers=viewer).status_code == 200
    assert env.client.delete(f"/api/v1/artifacts/{art['id']}", headers=viewer).status_code == 403
    assert env.upload(b"y", headers={"X-Test-Role": "viewer"}).status_code == 403


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("..%2F..%2F..%2Fetc%2Fshadow", "shadow"),
        ("C:\\Windows\\system.ini", "system.ini"),
        ("a%00b%0d%0ac%e2%80%aetxt.exe", "abctxt.exe"),
        ("x" * 300 + ".tar.gz", "x" * 252 + ".gz"),
    ],
)
def test_filename_normalization(make_env, raw, expected):
    env = make_env()
    resp = env.upload(b"data", name=raw)
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == expected
    # Whatever the name, the store only holds <project>/<aa>/<sha256>.
    assert env.blobs(env.p1.id) == [sha(b"data")]
    if isinstance(env.store, LocalArtifactStore):
        files = [p.relative_to(env.store.root).as_posix() for p in env.store.root.rglob("*") if p.is_file()]
        assert files == [f"{env.p1.id}/{sha(b'data')[:2]}/{sha(b'data')}"]


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Filename": ".."},
        {"X-Filename": "%2F%2E%2E%2F"},
        {"X-Filename": "%ff%fe"},
        {"X-Sha256": "../../" + "a" * 58},
        {"X-Owner-Kind": "../x"},
        {"X-Owner-Kind": "run", "X-Owner-Id": "../../etc"},
        {"Content-Type": "text/html/../../x"},
    ],
)
def test_traversal_and_bad_headers_rejected(make_env, headers):
    env = make_env()
    resp = env.upload(b"data", headers=headers)
    assert resp.status_code == 422, resp.text
    assert env.blobs(env.p1.id) == []


def test_missing_filename_rejected(env):
    resp = env.client.post("/api/v1/artifacts", content=b"x", headers=env.h1)
    assert resp.status_code == 422


def test_hash_mismatch_persists_nothing(env):
    resp = env.upload(b"payload", headers={"X-Sha256": sha(b"other")})
    assert resp.status_code == 422
    assert env.blobs(env.p1.id) == [] and env.leftovers(env.p1.id) == []
    assert env.client.get("/api/v1/artifacts", headers=env.h1).json()["items"] == []


def _chunks(total: int, size: int = 256 * 1024):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        sent += n
        yield os.urandom(n)


def test_oversize_aborts_mid_stream(make_env):
    env = make_env(max_artifact_bytes=10 * MiB)
    # Chunked (no Content-Length): the limit trips while reading; for S3 this is mid-multipart.
    resp = env.upload(_chunks(12 * MiB))
    assert resp.status_code == 413
    assert env.blobs(env.p1.id) == [] and env.leftovers(env.p1.id) == []
    # Declared Content-Length over the limit is refused before reading.
    assert env.upload(b"x" * (10 * MiB + 1)).status_code == 413


def test_quota_counts_distinct_blobs(make_env):
    env = make_env(project_quota_bytes=100)
    a = os.urandom(60)
    assert env.upload(a).status_code == 201
    assert env.upload(a, name="again").status_code == 201  # dedup: no extra bytes
    resp = env.upload(os.urandom(60))
    assert resp.status_code == 413 and "quota" in resp.json()["detail"]
    assert env.blobs(env.p1.id) == [sha(a)] and env.leftovers(env.p1.id) == []
    big = os.urandom(60)
    resp = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                           json={"filename": "b", "size": 60, "sha256": sha(big)})
    assert resp.status_code == 413
    assert env.upload(os.urandom(40)).status_code == 201  # exactly fills the quota
    assert env.upload(os.urandom(60), project=env.h2).status_code == 201  # per project


def _presigned_put(env: Env, upload: dict, data: bytes) -> httpx.Response:
    if upload["url"].startswith("http://testserver"):
        return env.client.request(upload["method"], upload["url"], content=data,
                                  headers={**upload["headers"], **env.h1})
    return httpx.request(upload["method"], upload["url"], content=data, headers=upload["headers"])


def test_presigned_flow_end_to_end(env):
    data = os.urandom(300_000)
    resp = env.client.post("/api/v1/artifacts/uploads", headers=env.h1, json={
        "filename": "model.safetensors", "size": len(data), "sha256": sha(data),
        "media_type": "application/octet-stream", "owner_kind": "model", "owner_id": "m1"})
    assert resp.status_code == 201, resp.text
    art, upload = resp.json()["artifact"], resp.json()["upload"]
    assert art["status"] == "pending" and art["verified"] is False and upload["method"] == "PUT"
    assert upload["expires_at"]
    assert env.fetch(art["id"]).status_code == 409
    assert env.client.get("/api/v1/artifacts", headers=env.h1).json()["items"] == []
    # Nothing uploaded yet.
    assert env.client.post(f"/api/v1/artifacts/{art['id']}/complete", headers=env.h1).status_code == 409

    put = _presigned_put(env, upload, data)
    assert put.status_code in (200, 204), put.text
    done = env.client.post(f"/api/v1/artifacts/{art['id']}/complete", headers=env.h1)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "ready" and done.json()["verified"] is True
    assert env.blobs(env.p1.id) == [sha(data)] and env.leftovers(env.p1.id) == []
    assert content_of(env, art["id"]) == data
    items = env.client.get("/api/v1/artifacts", headers=env.h1, params={"owner_kind": "model"}).json()
    assert [i["id"] for i in items["items"]] == [art["id"]]


def test_presigned_mismatch_removes_object(env):
    data = os.urandom(5000)
    resp = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                           json={"filename": "x", "size": len(data), "sha256": sha(data)})
    art, upload = resp.json()["artifact"], resp.json()["upload"]
    bad = os.urandom(5000)
    put = _presigned_put(env, upload, bad)
    if upload["url"].startswith("http://testserver"):
        assert put.status_code == 422  # local: checked while streaming, nothing kept
    else:
        assert put.status_code == 200
        done = env.client.post(f"/api/v1/artifacts/{art['id']}/complete", headers=env.h1)
        assert done.status_code == 422
        assert env.client.get(f"/api/v1/artifacts/{art['id']}", headers=env.h1).status_code == 404
    assert env.blobs(env.p1.id) == [] and env.leftovers(env.p1.id) == []


def test_unverified_above_hash_limit(make_env):
    env = make_env(verify_hash_max_bytes=1000)
    data = os.urandom(2000)
    resp = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                           json={"filename": "x", "size": len(data), "sha256": sha(data)})
    art, upload = resp.json()["artifact"], resp.json()["upload"]
    assert _presigned_put(env, upload, data).status_code in (200, 204)
    done = env.client.post(f"/api/v1/artifacts/{art['id']}/complete", headers=env.h1).json()
    assert done["status"] == "ready" and done["verified"] is False


def test_svg_never_inline_png_inline_sandboxed(env):
    svg = env.upload(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
                     name="x.svg", headers={"Content-Type": "image/svg+xml"}).json()
    resp = env.fetch(svg["id"], inline=1)
    if resp.status_code == 302:
        resp = follow(env, resp)
    assert resp.headers["content-disposition"].startswith("attachment;")
    if "content-security-policy" in resp.headers:
        assert resp.headers["content-security-policy"] == "sandbox"

    png = env.upload(b"\x89PNG\r\n\x1a\nfake", name="p.png", headers={"Content-Type": "image/png"}).json()
    resp = env.fetch(png["id"], inline=1)  # inline is always served by the API, never redirected
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("inline;")
    assert resp.headers["content-security-policy"] == "sandbox"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-type"] == "image/png"
    resp = env.fetch(png["id"])
    if resp.status_code == 302:
        resp = follow(env, resp)
    assert resp.headers["content-disposition"].startswith("attachment;")


def test_list_pagination_and_owner_filter(env):
    ids = [env.upload(os.urandom(10), name=f"{i}", headers={"X-Owner-Kind": "run", "X-Owner-Id": f"r{i % 2}"})
           .json()["id"] for i in range(5)]
    seen, cursor = [], None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = env.client.get("/api/v1/artifacts", headers=env.h1, params=params).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(ids) and len(seen) == 5
    r0 = env.client.get("/api/v1/artifacts", headers=env.h1,
                        params={"owner_kind": "run", "owner_id": "r0"}).json()["items"]
    assert len(r0) == 3
    assert env.client.get("/api/v1/artifacts", headers=env.h1, params={"cursor": "!!"}).status_code == 400


def test_delete_audits_and_gc_keeps_referenced(env):
    shared, lone = os.urandom(100), os.urandom(100)
    a = env.upload(shared).json()
    b = env.upload(shared, name="b").json()
    c = env.upload(lone).json()
    for art in (a, c):
        assert env.client.delete(f"/api/v1/artifacts/{art['id']}", headers=env.h1).status_code == 204
    assert env.client.get(f"/api/v1/artifacts/{a['id']}", headers=env.h1).status_code == 404
    assert sorted(env.blobs(env.p1.id)) == sorted([sha(shared), sha(lone)])  # blobs leave only on GC
    with env.factory() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == "artifact.delete")).all()
        assert {r.target for r in rows} == {a["id"], c["id"]}
        assert all(r.actor_kind == "user" and r.actor_id == "u1" for r in rows)
    with env.factory() as s:
        result = svc.gc(s, env.store)  # within the grace period: nothing deleted
        assert result.deleted_blobs == 0
        result = svc.gc(s, env.store, grace=timedelta(0))
        assert result.deleted_blobs >= 1  # the S3 bucket is shared by the session's tests
    assert env.blobs(env.p1.id) == [sha(shared)]
    assert content_of(env, b["id"]) == shared


def test_pending_expiry_and_gc_job(env):
    from matrix_server.storage.models import Job
    from matrix_server.workers import artifact_handlers, runner

    data = os.urandom(1000)
    resp = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                           json={"filename": "x", "size": len(data), "sha256": sha(data)})
    art, upload = resp.json()["artifact"], resp.json()["upload"]
    if not upload["url"].startswith("http://testserver"):
        assert _presigned_put(env, upload, data).status_code == 200  # staged, never completed
    with env.factory() as s, s.begin():
        s.execute(update(Artifact).where(Artifact.id == art["id"])
                  .values(created_at=utcnow() - timedelta(hours=25)))
    assert env.client.post(f"/api/v1/artifacts/{art['id']}/complete", headers=env.h1).status_code == 410

    artifact_handlers.set_artifact_store(env.store)
    try:
        assert svc.schedule_gc(env.factory) is True
        assert svc.schedule_gc(env.factory) is False  # one at a time
        runner.load_handler_modules(["matrix_server.workers.artifact_handlers"])
        assert runner.run_once(env.factory, "w1")
    finally:
        artifact_handlers.set_artifact_store(None)
    with env.factory() as s:
        assert s.get(Artifact, art["id"]) is None
        job = s.scalars(select(Job).where(Job.kind == "artifacts.gc")).one()
        assert job.status == "done", job.error
    # The staged object is younger than 24h, so purge_stale keeps it until it ages out.
    with env.factory() as s:
        svc.gc(s, env.store, now=utcnow() + timedelta(hours=25))
    assert env.leftovers(env.p1.id) == []


def test_concurrent_identical_uploads(env):
    data = os.urandom(200_000)
    results: list[int] = []
    barrier = threading.Barrier(6)

    def worker() -> None:
        barrier.wait()
        with env.factory() as s:
            try:
                svc.store_bytes(s, env.store, (env.p1.id, None), "same.bin", data)
                s.commit()
                results.append(201)
            except Exception as exc:  # noqa: BLE001
                results.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [201] * 6
    assert env.blobs(env.p1.id) == [sha(data)] and env.leftovers(env.p1.id) == []
    items = env.client.get("/api/v1/artifacts", headers=env.h1).json()["items"]
    assert len(items) == 6 and {i["sha256"] for i in items} == {sha(data)}
    assert content_of(env, items[0]["id"]) == data


def test_store_bytes_helper(env):
    with env.factory() as s:
        art = svc.store_bytes(s, env.store, (env.p1.id, "system"), "report.json", b"{}",
                              "application/json", "experiment", "e1")
        s.commit()
        assert art.status == "ready" and art.verified and art.created_by == "system"
    assert content_of(env, art.id) == b"{}"


# --- stores -------------------------------------------------------------------------------------


def test_local_store_permissions_and_keys(tmp_path):
    store = LocalArtifactStore(tmp_path / "root")
    import io

    info = store.put("proj1", io.BytesIO(b"abc"), max_bytes=10)
    path = store.root / "proj1" / info.sha256[:2] / info.sha256
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / "proj1").stat().st_mode) == 0o700
    assert store.put("proj1", io.BytesIO(b"abc"), max_bytes=10).created is False
    for bad in ("../x", "..", "a/b", "", ".tmp", "x" * 65):
        with pytest.raises(InvalidKey):
            store.exists(bad, info.sha256)
    for bad in ("../" + "a" * 61, info.sha256.upper(), "a" * 63):
        with pytest.raises(InvalidKey):
            store.open("proj1", bad)
    with pytest.raises(BlobTooLarge):
        store.put("proj1", io.BytesIO(b"x" * 11), max_bytes=10)
    assert list((store.root / "proj1" / ".tmp").iterdir()) == []
    # A symlinked project dir escaping the root is refused.
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "evil").symlink_to(outside)
    with pytest.raises(InvalidKey):
        store.put("evil", io.BytesIO(b"x"), max_bytes=10)
    assert list(outside.iterdir()) == []


def test_s3_store_multipart_public_endpoint_and_dedup(s3_bucket):
    import io

    from matrix_server.artifacts.s3 import S3ArtifactStore

    public = S3_ENDPOINT.replace("localhost", "127.0.0.1")
    store = S3ArtifactStore(bucket=s3_bucket, endpoint=S3_ENDPOINT, region=S3_REGION, access_key=S3_KEY,
                            secret_key=S3_SECRET, public_endpoint=public)
    pid = uuid.uuid4().hex
    data = os.urandom(17 * MiB + 123)  # three multipart parts
    info = store.put(pid, io.BytesIO(data), max_bytes=20 * MiB)
    assert info.created and info.size == len(data) and info.sha256 == sha(data)
    assert store.size(pid, info.sha256) == len(data)
    assert store.put(pid, io.BytesIO(data), max_bytes=20 * MiB).created is False
    presigned = store.presign_get(pid, info.sha256, 60, filename="m.bin", media_type="application/octet-stream")
    assert presigned.url.startswith(public)
    got = httpx.get(presigned.url)
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == info.sha256
    put = store.presign_put(pid, info.sha256, 3, 60, upload_id="up1")
    assert put.url.startswith(public) and "/uploads/up1" in put.url
    assert [b.sha256 for b in store.iter_blobs() if b.project_id == pid] == [info.sha256]
    store.delete(pid, info.sha256)
    assert not store.exists(pid, info.sha256)


def test_s3_auto_create_bucket(s3_bucket):
    from matrix_server.artifacts.s3 import S3ArtifactStore

    name = f"{s3_bucket}-auto"
    store = build_artifact_store(Settings(**_s3_settings(name, s3_auto_create_bucket=True)))
    assert isinstance(store, S3ArtifactStore)
    store.client.head_bucket(Bucket=name)
    store.client.delete_bucket(Bucket=name)


# --- real app and auth --------------------------------------------------------------------------


def test_real_app_with_api_key(tmp_path):
    from matrix_server.app import create_app

    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/m.db", admin_token="adm",
                              artifact_local_dir=str(tmp_path / "blobs"), max_json_body_bytes=1000))
    with TestClient(app) as client:
        resp = client.post("/api/v1/admin/projects", json={"name": "p"}, headers={"Authorization": "Bearer adm"})
        assert resp.status_code == 201, resp.text
        key = {"Authorization": f"Bearer {resp.json()['api_key']}"}
        assert client.post("/api/v1/artifacts", content=b"x", headers={"X-Filename": "f"}).status_code == 401
        data = os.urandom(5000)  # over the JSON body limit: raw uploads are exempt
        resp = client.post("/api/v1/artifacts", content=data, headers={**key, "X-Filename": "f.bin"})
        assert resp.status_code == 201, resp.text
        got = client.get(f"/api/v1/artifacts/{resp.json()['id']}/download", headers=key)
        assert got.status_code == 200 and got.content == data
    app.state.db_engine.dispose()
