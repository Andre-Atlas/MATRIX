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
import io
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
from matrix_server.storage.db import Base, as_utc, init_db, make_engine, make_session_factory, utcnow
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


def test_above_hash_limit_local_content_route_is_hashed(make_env, backend):
    if backend["artifact_backend"] != "local":
        pytest.skip("local content route only")
    env = make_env(verify_hash_max_bytes=1000)
    data = os.urandom(2000)
    resp = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                           json={"filename": "x", "size": len(data), "sha256": sha(data)})
    art, upload = resp.json()["artifact"], resp.json()["upload"]
    assert _presigned_put(env, upload, os.urandom(2000)).status_code == 422  # hashed while streaming
    assert _presigned_put(env, upload, data).status_code == 204
    done = env.client.post(f"/api/v1/artifacts/{art['id']}/complete", headers=env.h1).json()
    assert done["status"] == "ready" and done["verified"] is True


class StagingStore(LocalArtifactStore):
    """Local store with an S3-like staging area: staged bytes are not checked on upload, and
    `promote_upload` copies them verbatim to the declared sha's key (like an S3 copy)."""

    def __init__(self, root, checksum: str | None = None) -> None:
        super().__init__(root)
        self.staged: dict[tuple[str, str], bytes] = {}
        self.checksum = checksum

    def stat_upload(self, project_id, upload_id):
        data = self.staged.get((project_id, upload_id))
        return None if data is None else len(data)

    def open_upload(self, project_id, upload_id, *, etag=None):
        import io

        return io.BytesIO(self.staged[(project_id, upload_id)])

    def upload_checksum(self, project_id, upload_id, *, etag=None):
        return self.checksum

    def promote_upload(self, project_id, upload_id, sha256, *, etag=None):
        data = self.staged.pop((project_id, upload_id))
        path = self._blob_path(project_id, sha256)
        self._mkdir(path.parent)
        path.write_bytes(data)

    def delete_upload(self, project_id, upload_id):
        self.staged.pop((project_id, upload_id), None)


def _pending(env: Env, data_size: int, declared_sha: str) -> Artifact:
    with env.factory() as s, s.begin():
        art = Artifact(project_id=env.p1.id, name="big", media_type="application/octet-stream",
                       size=data_size, sha256=declared_sha, verified=False, status="pending")
        s.add(art)
    return art


def _complete(env: Env, store, art: Artifact, verify_max: int):
    with env.factory() as s:
        row = s.get(Artifact, art.id)
        try:
            svc.complete_upload(s, store, row, svc.Limits(verify_hash_max_bytes=verify_max))
            return row.status
        except svc.ArtifactError as exc:
            return exc.status_code
        finally:
            s.commit()


def test_s11_unproven_upload_above_hash_limit_is_never_promoted(env, tmp_path):
    store = StagingStore(tmp_path / "staging")
    legit, forged = os.urandom(2000), os.urandom(2000)
    art = _pending(env, len(forged), sha(legit))  # declares the sha of other content
    store.staged[(env.p1.id, art.id)] = forged
    assert _complete(env, store, art, verify_max=1000) == 422
    assert not store.exists(env.p1.id, sha(legit)) and store.staged == {}
    with env.factory() as s:
        assert s.get(Artifact, art.id) is None
    # Later uploads of the real content do not dedup against forged bytes.
    with env.factory() as s:
        real = svc.store_bytes(s, store, env.p1.id, "real", legit)
        s.commit()
    with store.open(env.p1.id, real.sha256) as fh:
        assert fh.read() == legit


def test_s11_trusted_server_checksum_promotes_above_limit(env, tmp_path):
    data = os.urandom(2000)
    store = StagingStore(tmp_path / "staging", checksum=sha(data))
    art = _pending(env, len(data), sha(data))
    store.staged[(env.p1.id, art.id)] = data
    assert _complete(env, store, art, verify_max=1000) == "ready"
    with env.factory() as s:
        assert s.get(Artifact, art.id).verified is True
    # A checksum for different content is refused.
    store.checksum = sha(b"other")
    art = _pending(env, len(data), sha(os.urandom(8)))
    store.staged[(env.p1.id, art.id)] = data
    assert _complete(env, store, art, verify_max=1000) == 422


def test_s10_staged_size_must_match_declared(env, tmp_path):
    store = StagingStore(tmp_path / "staging")
    data = os.urandom(100)
    art = _pending(env, len(data), sha(data))
    store.staged[(env.p1.id, art.id)] = data + os.urandom(10_000)  # more than declared/reserved
    assert _complete(env, store, art, verify_max=10**9) == 422
    assert store.staged == {} and env.blobs(env.p1.id) == []


def test_s10_presigned_put_signs_length_and_checksum():
    import base64
    from urllib.parse import parse_qs, urlparse

    from matrix_server.artifacts.s3 import S3ArtifactStore

    # Signing is offline: no server needed.
    store = S3ArtifactStore(bucket="b", endpoint="http://s3.internal:9000", access_key="k", secret_key="s",
                            public_endpoint="http://s3.example.test")
    digest = sha(b"abc")
    req = store.presign_put("proj1", digest, 3, 60, upload_id="up1")
    query = parse_qs(urlparse(req.url).query)
    signed = query["X-Amz-SignedHeaders"][0].split(";")
    assert "content-length" in signed and "x-amz-checksum-sha256" in signed
    assert req.url.startswith("http://s3.example.test/b/proj1/uploads/up1?")
    assert req.headers["Content-Length"] == "3"
    assert req.headers["x-amz-checksum-sha256"] == base64.b64encode(bytes.fromhex(digest)).decode()
    assert store.upload_checksum("proj1", "up1") is None  # checksums untrusted by default: no network


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
    # The unreferenced blob goes with its last row (disk freed with the quota); the shared one stays.
    assert env.blobs(env.p1.id) == [sha(shared)]
    with env.factory() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == "artifact.delete")).all()
        assert {r.target for r in rows} == {a["id"], c["id"]}
        assert all(r.actor_kind == "user" and r.actor_id == "u1" for r in rows)
    env.store.put(env.p1.id, io.BytesIO(lone), max_bytes=1000)  # an orphan (e.g. a crash) for the GC
    with env.factory() as s:
        result = svc.gc(s, env.store)  # within the grace period: nothing deleted
        assert sha(lone) in env.blobs(env.p1.id)
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
        gc_jobs = s.scalars(select(Job).where(Job.kind == "artifacts.gc").order_by(Job.created_at)).all()
        assert [j.status for j in gc_jobs] == ["done", "queued"], [j.error for j in gc_jobs]
        # The job re-arms itself: its successor runs GC_INTERVAL later (periodic GC).
        assert as_utc(gc_jobs[1].run_after) > utcnow() + svc.GC_INTERVAL - timedelta(minutes=5)
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


# --- review 5 (cycle 2): quota bypass, GC/disk, TOCTOU -------------------------------------------


def test_zero_size_reservations_never_bypass_the_quota(make_env):
    """HIGH: a pending reservation (size 0) used to make its digest count as "already referenced",
    so a direct upload with that `X-Sha256` skipped the quota. Scenario of review5/quota.py."""
    env = make_env(project_quota_bytes=1000, max_artifact_bytes=100_000)
    blobs = [os.urandom(50_000) for _ in range(6)]
    for data in blobs:
        r = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                            json={"filename": "z", "size": 0, "sha256": sha(data)})
        assert r.status_code == 201, r.text
    for data in blobs[:5]:
        assert env.upload(data, headers={"X-Sha256": sha(data)}).status_code == 413
    # presigned variant: a real-size reservation of the same digest is checked against the quota
    r = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                        json={"filename": "big", "size": 50_000, "sha256": sha(blobs[5])})
    assert r.status_code == 413
    # the size-0 reservation itself cannot receive more than it declared
    pending = env.client.get("/api/v1/artifacts", headers=env.h1).json()["items"]
    assert pending == []  # pending rows are not listed; none became ready
    assert env.blobs(env.p1.id) == []
    assert env.upload(os.urandom(500)).status_code == 201  # the quota itself still works


def test_reservations_hold_quota_and_dedup_needs_a_ready_blob(make_env):
    env = make_env(project_quota_bytes=1000, max_artifact_bytes=100_000)
    data = os.urandom(600)
    r = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                        json={"filename": "r", "size": len(data), "sha256": sha(data)})
    assert r.status_code == 201
    assert env.upload(os.urandom(600)).status_code == 413  # the 600-byte reservation counts
    # same digest (declared up front, so the streaming budget knows it): counted once, fits
    assert env.upload(data, headers={"X-Sha256": sha(data)}).status_code == 201
    assert env.upload(data).status_code == 201  # now a ready blob exists: dedup is free


def test_quota_rechecked_with_real_size_on_complete(env, tmp_path):
    store = StagingStore(tmp_path / "staging")
    data = os.urandom(800)
    art = _pending(env, len(data), sha(data))
    store.staged[(env.p1.id, art.id)] = data
    with env.factory() as s:
        row = s.get(Artifact, art.id)
        with pytest.raises(svc.QuotaExceeded):
            svc.complete_upload(s, store, row, svc.Limits(project_quota_bytes=500))
        s.commit()
    with env.factory() as s:
        assert s.get(Artifact, art.id) is None  # the reservation is released
    assert store.staged == {} and sha(data) not in store_blobs(store, env.p1.id)


def store_blobs(store, project_id):
    return [b.sha256 for b in store.iter_blobs() if b.project_id == project_id]


def test_quota_rechecked_on_put_content_and_complete(make_env):
    env = make_env(project_quota_bytes=1000, max_artifact_bytes=100_000)
    if not isinstance(env.store, LocalArtifactStore):
        pytest.skip("the content route exists only with the local backend")
    data = os.urandom(800)
    art = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                          json={"filename": "r", "size": len(data), "sha256": sha(data)}).json()["artifact"]
    # The operator lowers the quota (or other reservations settled first): the received bytes no longer fit.
    env.app.state.settings = env.settings.model_copy(update={"project_quota_bytes": 500})
    put = env.client.put(f"/api/v1/artifacts/{art['id']}/content", content=data, headers=env.h1)
    assert put.status_code == 413
    assert env.blobs(env.p1.id) == []
    assert env.client.get(f"/api/v1/artifacts/{art['id']}", headers=env.h1).status_code == 404
    # complete re-checks too (blob written under the old quota, completed under the new one)
    env.app.state.settings = env.settings
    art = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                          json={"filename": "r", "size": len(data), "sha256": sha(data)}).json()["artifact"]
    assert env.client.put(f"/api/v1/artifacts/{art['id']}/content", content=data, headers=env.h1).status_code == 204
    env.app.state.settings = env.settings.model_copy(update={"project_quota_bytes": 500})
    assert env.client.post(f"/api/v1/artifacts/{art['id']}/complete", headers=env.h1).status_code == 413
    assert env.blobs(env.p1.id) == []
    assert env.client.get(f"/api/v1/artifacts/{art['id']}", headers=env.h1).status_code == 404


def _disk_bytes(env: Env) -> int:
    return sum(env.store.size(b.project_id, b.sha256) or 0 for b in env.store.iter_blobs()
               if b.project_id == env.p1.id)


def test_upload_delete_cycles_do_not_grow_disk_beyond_quota(make_env):
    """MEDIUM (review5/fill.py): DELETE freed the quota but left the blob for a GC that never ran."""
    env = make_env(project_quota_bytes=100_000, max_artifact_bytes=100_000)
    for _ in range(10):
        r = env.upload(os.urandom(90_000))
        assert r.status_code == 201, r.text
        assert env.client.delete(f"/api/v1/artifacts/{r.json()['id']}", headers=env.h1).status_code == 204
        assert _disk_bytes(env) <= 100_000
    assert env.blobs(env.p1.id) == []
    # deleting a pending upload also drops its staged object / content
    data = os.urandom(1000)
    art = env.client.post("/api/v1/artifacts/uploads", headers=env.h1,
                          json={"filename": "p", "size": len(data), "sha256": sha(data)}).json()
    if art["upload"]["url"].startswith("http://testserver"):
        env.client.put(f"/api/v1/artifacts/{art['artifact']['id']}/content", content=data, headers=env.h1)
    else:
        assert _presigned_put(env, art["upload"], data).status_code == 200
    assert env.client.delete(f"/api/v1/artifacts/{art['artifact']['id']}", headers=env.h1).status_code == 204
    assert env.blobs(env.p1.id) == [] and env.leftovers(env.p1.id) == []


def test_worker_start_arms_gc_and_reaper(env):
    from matrix_server.cli import schedule_periodic_jobs
    from matrix_server.storage.models import Job

    schedule_periodic_jobs(env.factory)
    schedule_periodic_jobs(env.factory)  # idempotent: at most one of each queued
    with env.factory() as s:
        kinds = sorted(j.kind for j in s.scalars(select(Job).where(Job.status == "queued")))
    assert kinds == ["artifacts.gc", "runs.reaper"]


def test_project_purge_removes_blobs_and_metrics(env):
    from matrix_server import projects as project_ops
    from matrix_server.runs.store import MemoryMetricStore, MetricPoint
    from matrix_server.storage.models import Job
    from matrix_server.storage.spans import MemorySpanStore
    from matrix_server.workers import artifact_handlers, handlers, online_handlers, run_handlers

    a = env.upload(os.urandom(300)).json()
    env.upload(os.urandom(300), project=env.h2)
    metrics = MemoryMetricStore()
    for pid in (env.p1.id, env.p2.id):
        metrics.insert(pid, "run1", [MetricPoint("loss", 0, 1.0, 0)])
    with env.factory() as s, s.begin():
        project_ops.delete_project(s, env.p1.id)
    with env.factory() as s:
        assert s.get(Artifact, a["id"]) is None
        job = s.scalars(select(Job).where(Job.kind == project_ops.PURGE_SPANS_JOB)).one()
    artifact_handlers.set_artifact_store(env.store)
    run_handlers.set_metric_store(metrics)
    online_handlers.set_span_store(MemorySpanStore())
    try:
        with env.factory() as s:
            handlers.purge_project_spans(s, job)
            handlers.purge_project_spans(s, job)  # idempotent (the job retries)
    finally:
        artifact_handlers.set_artifact_store(None)
        run_handlers.set_metric_store(None)
        online_handlers.set_span_store(None)
    assert env.blobs(env.p1.id) == [] and env.leftovers(env.p1.id) == []
    assert len(env.blobs(env.p2.id)) == 1
    assert metrics.keys(env.p1.id, "run1") == [] and metrics.keys(env.p2.id, "run1") == ["loss"]


# --- S11 / TOCTOU: the staged object is pinned to the version that was hashed ----------------------


class _FakeS3:
    """In-memory S3 client double with ETag preconditions (IfMatch / CopySourceIfMatch) and a hook
    that runs when the hashing read of a staged object is closed."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.after_read = None
        self._n = 0

    def _err(self, code: str, status: int):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, "op")

    def put(self, key: str, data: bytes) -> None:
        self._n += 1
        self.objects[key] = (data, f'"etag-{self._n}"')

    def _get(self, key, if_match=None):
        if key not in self.objects:
            raise self._err("404", 404)
        data, etag = self.objects[key]
        if if_match is not None and if_match != etag:
            raise self._err("PreconditionFailed", 412)
        return data, etag

    def head_object(self, Bucket, Key, IfMatch=None, ChecksumMode=None):
        data, etag = self._get(Key, IfMatch)
        return {"ContentLength": len(data), "ETag": etag}

    def get_object(self, Bucket, Key, IfMatch=None):
        data, _ = self._get(Key, IfMatch)
        fake = self

        class Body(io.BytesIO):
            def close(self_inner):
                super().close()
                if fake.after_read is not None:
                    hook, fake.after_read = fake.after_read, None
                    hook(Key)

        return {"Body": Body(data)}

    def copy(self, CopySource, Bucket, Key, ExtraArgs=None):
        data, _ = self._get(CopySource["Key"], (ExtraArgs or {}).get("CopySourceIfMatch"))
        self.put(Key, data)

    def copy_object(self, **kw):
        return {}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


def _fake_s3_store():
    from matrix_server.artifacts.s3 import S3ArtifactStore

    store = S3ArtifactStore(bucket="b", endpoint="http://127.0.0.1:9", access_key="x", secret_key="y")
    store.client = _FakeS3()
    return store


def test_s11_s3_object_swapped_between_hash_and_promotion_is_refused(env):
    store = _fake_s3_store()
    legit, forged = os.urandom(2000), os.urandom(2000)
    art = _pending(env, len(legit), sha(legit))
    key = store._upload_key(env.p1.id, art.id)
    store.client.put(key, legit)
    # The client re-PUTs the (still valid) presigned URL right after the server hashed the object.
    store.client.after_read = lambda k: store.client.put(k, forged)
    assert _complete(env, store, art, verify_max=10_000) == 422
    final = f"{env.p1.id}/{sha(legit)[:2]}/{sha(legit)}"
    assert final not in store.client.objects  # forged content never reaches the blob key
    assert key not in store.client.objects  # the staged object is removed
    with env.factory() as s:
        assert s.get(Artifact, art.id) is None


def test_s11_s3_unchanged_object_is_promoted(env):
    store = _fake_s3_store()
    data = os.urandom(2000)
    art = _pending(env, len(data), sha(data))
    store.client.put(store._upload_key(env.p1.id, art.id), data)
    assert _complete(env, store, art, verify_max=10_000) == "ready"
    assert store.client.objects[f"{env.p1.id}/{sha(data)[:2]}/{sha(data)}"][0] == data


def test_s11_local_put_refuses_a_file_changed_before_rename(tmp_path, monkeypatch):
    from matrix_server.artifacts import ArtifactStoreError
    from matrix_server.artifacts import local as local_mod

    store = LocalArtifactStore(tmp_path / "blobs")
    real_link = os.link  # the blob is published with an atomic, non-overwriting hard link (T3)

    def tampering_link(src, dst):
        with open(src, "r+b") as f:  # another writer changes the temp file after it was hashed
            f.write(b"forged!!")
        real_link(src, dst)

    monkeypatch.setattr(local_mod.os, "link", tampering_link)
    data = os.urandom(4000)
    with pytest.raises(ArtifactStoreError):
        store.put("p1", io.BytesIO(data), max_bytes=10_000)
    assert [b.sha256 for b in store.iter_blobs()] == []


# --- review 7: delete vs dedup upload, quota races ------------------------------------------------


def _local_env(db_url, tmp_path, **kw) -> Env:
    return Env(Settings(database_url=db_url, artifact_backend="local", artifact_local_dir=str(tmp_path / "b"), **kw))


def test_delete_never_leaves_a_dedup_upload_ready_without_content(db_url, tmp_path):
    """MEDIUM (review 7): a DELETE deciding the blob was orphaned removed it after a concurrent
    upload of the same content had deduplicated onto it and committed its row (ready, no content)."""
    env = _local_env(db_url, tmp_path)
    try:
        data = os.urandom(5000)
        first = env.upload(data).json()
        real_delete = env.store.delete
        out: dict = {}

        def racing_delete(pid, digest):
            # The same content is uploaded again right before the blob goes. Without the blob lock
            # it completes (deduplicated, 201) inside this window; with it, it waits for our commit.
            t = threading.Thread(target=lambda: out.setdefault("up", env.upload(data)))
            t.start()
            t.join(1.5)
            out["thread"] = t
            return real_delete(pid, digest)

        env.store.delete = racing_delete
        assert env.client.delete(f"/api/v1/artifacts/{first['id']}", headers=env.h1).status_code == 204
        env.store.delete = real_delete
        out["thread"].join(30)
        up = out["up"]
        assert up.status_code in (201, 409), up.text
        with env.factory() as s:
            ready = s.scalars(select(Artifact).where(Artifact.status == "ready")).all()
        for art in ready:  # the invariant: a ready artifact always has its content
            assert env.store.exists(art.project_id, art.sha256), "ready artifact without content"
        if up.status_code == 201:
            assert env.fetch(up.json()["id"]).content == data
        else:
            assert ready == [] and "retry" in up.json()["detail"]
            assert env.upload(data).status_code == 201  # the retry works
    finally:
        env.close()


def _slow_quota_check(monkeypatch, *modules):
    orig = svc.check_quota_for

    def slow(*a, **k):
        result = orig(*a, **k)
        import time

        time.sleep(0.3)  # widen the window between the check and the insert/commit
        return result

    for mod in modules:
        monkeypatch.setattr(mod, "check_quota_for", slow)


def test_parallel_direct_uploads_never_exceed_the_quota(db_url, tmp_path, monkeypatch):
    """MEDIUM (review 7): check_quota_for -> insert -> commit ran without a lock."""
    quota = 1_000_000
    env = _local_env(db_url, tmp_path, project_quota_bytes=quota, max_artifact_bytes=quota)
    _slow_quota_check(monkeypatch, svc)
    barrier = threading.Barrier(5)
    codes: list[int] = []
    limits = svc.Limits(project_quota_bytes=quota, max_artifact_bytes=quota)

    def upload():
        data = os.urandom(900_000)
        with env.factory() as s:
            barrier.wait()
            try:
                svc.store_stream(s, env.store, (env.p1.id, None), "x", io.BytesIO(data), limits=limits)
                s.commit()
                codes.append(201)
            except svc.QuotaExceeded:
                s.rollback()
                codes.append(413)

    try:
        threads = [threading.Thread(target=upload) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        with env.factory() as s:
            used = svc.project_usage(s, env.p1.id)
        assert sorted(codes) == [201, 413, 413, 413, 413]
        assert used <= quota
        assert len(env.blobs(env.p1.id)) == 1  # the refused uploads' blobs are removed
    finally:
        env.close()


def test_parallel_reservations_never_exceed_the_quota(db_url, tmp_path, monkeypatch):
    quota = 1_000_000
    env = _local_env(db_url, tmp_path, project_quota_bytes=quota, max_artifact_bytes=quota)
    _slow_quota_check(monkeypatch, artifacts_api)
    barrier = threading.Barrier(5)
    codes: list[int] = []

    def reserve():
        barrier.wait()
        body = {"filename": "r.bin", "size": 900_000, "sha256": os.urandom(32).hex()}
        codes.append(env.client.post("/api/v1/artifacts/uploads", json=body, headers=env.h1).status_code)

    try:
        threads = [threading.Thread(target=reserve) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        with env.factory() as s:
            used = svc.project_usage(s, env.p1.id)
        assert sorted(codes) == [201, 413, 413, 413, 413]
        assert used <= quota
    finally:
        env.close()
