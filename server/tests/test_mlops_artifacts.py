"""`main`'s MLOps artifacts after T4: metadata (`model_artifacts`) over the G0 blob store.

Replaced on purpose (the behaviour was removed, see t4-notes.md): the tests of `main`'s own
LocalFS/S3 store, its HMAC upload/download tokens, `MATRIX_ARTIFACT_TOKEN_SECRET`/`MATRIX_ENV`
startup checks and the private-IP check of external URIs. What they protected is now covered by
the G0 store (tests/test_artifacts.py: path safety, dedup, quota, verified sha256, S3 presign) and
by the checks below on the deprecated adapter."""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import api_key_headers, project_with_roles

from matrix_server.app import create_app
from matrix_server.artifacts import PresignedRequest
from matrix_server.artifacts.local import LocalArtifactStore
from matrix_server.artifacts.models import Artifact as Blob
from matrix_server.config import Settings
from matrix_server.mlops.compat import validate_external_uri, validate_sha256
from matrix_server.storage.models import ModelArtifact


@pytest.mark.parametrize(
    "valid_uri",
    [
        "s3://my-bucket/models/llama-3/adapter.safetensors",
        "gs://ml-artifacts/runs/run-1/weights.bin",
        "hf://meta-llama/Llama-3.1-8B-Instruct",
        "lakefs://repo/main/checkpoints/step-1000",
        # registered by reference only: never fetched, so an address-like host is just a name
        "s3://169.254.169.254/bucket/key",
        "s3://localhost/bucket/key",
    ],
)
def test_validate_external_uri_accepts_allowed_schemes(valid_uri: str) -> None:
    assert validate_external_uri(valid_uri) == valid_uri


@pytest.mark.parametrize(
    "blocked_uri",
    [
        "",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        "https://example.com/model.safetensors",
        "s3://user:pass@my-bucket/key",
        "s3:///no-host",
        "s3://my-bucket/../../etc/passwd",
        "s3://my-bucket/path\\evil",
        "s3://my-bucket/key\n",
        " s3://my-bucket/key",
    ],
)
def test_validate_external_uri_blocks_invalid(blocked_uri: str) -> None:
    with pytest.raises(ValueError):
        validate_external_uri(blocked_uri)


def test_validate_sha256() -> None:
    assert validate_sha256("A" * 64) == "a" * 64
    for bad in ("", "a" * 63, "g" * 64, "a" * 65):
        with pytest.raises(ValueError):
            validate_sha256(bad)


def test_no_token_secret_or_env_is_required(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The HMAC tokens are gone, so is their mandatory secret: a production deployment starts
    without MATRIX_ARTIFACT_TOKEN_SECRET (the API and the worker share no signing key any more)."""
    monkeypatch.delenv("MATRIX_ARTIFACT_TOKEN_SECRET", raising=False)
    monkeypatch.setenv("MATRIX_ENV", "production")
    settings = Settings(database_url=f"sqlite:///{tmp_path}/a.db", artifact_local_dir=str(tmp_path / "a"))
    assert not hasattr(settings, "artifact_token_secret") and not hasattr(settings, "env")
    for alias in ("artifact_store", "artifact_dir", "artifact_presign_ttl_seconds"):
        assert not hasattr(settings, alias)
    app = create_app(settings)
    assert not hasattr(app.state, "mlops_artifact_store") and not hasattr(app.state, "training_metric_store")
    app.state.db_engine.dispose()


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/m.db",
        artifact_backend="local",
        artifact_local_dir=str(tmp_path / "blobs"),
        project_quota_bytes=1000,
    )
    app = create_app(settings)
    client = TestClient(app)
    factory = app.state.session_factory
    pid, roles = project_with_roles(factory, "P")
    yield app, client, factory, pid, roles
    app.state.db_engine.dispose()


def _presign(client, headers, name: str, data: bytes, **extra):
    return client.post(
        "/api/v1/mlops/artifacts/presign-upload",
        headers=headers,
        json={"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), **extra},
    )


def test_presign_reserves_a_g0_blob_and_commit_verifies_it(env) -> None:
    app, client, factory, pid, roles = env
    member = roles["member"]
    data = b"weights-v1"
    resp = _presign(client, member, "ckpt", data)
    assert resp.status_code == 201, resp.text
    assert resp.headers["Deprecation"] == "true"
    art = resp.json()
    assert art["status"] == "pending_upload" and art["storage_backend"] == "local"
    assert art["storage_uri"] == f"artifact:{art['artifact_id']}"
    with factory() as s:
        blob = s.get(Blob, art["artifact_id"])
        assert blob.status == "pending" and blob.owner_kind == "model_artifact" and blob.owner_id == art["id"]

    # commit before the upload: nothing to verify yet (G0 semantics)
    assert client.post(f"/api/v1/mlops/artifacts/{art['id']}/commit", headers=member).status_code == 409
    assert client.put(art["upload_url"], content=b"tampered!!", headers=member).status_code == 422
    assert client.put(art["upload_url"], content=data, headers=member).status_code == 204
    done = client.post(f"/api/v1/mlops/artifacts/{art['id']}/commit", headers=member)
    assert done.status_code == 200 and done.json()["status"] == "committed"
    with factory() as s:
        blob = s.get(Blob, art["artifact_id"])
        assert blob.status == "ready" and blob.verified is True

    # same content again: deduplicated in the G0 store, no upload, new version
    again = _presign(client, member, "ckpt", data).json()
    assert again["deduplicated"] is True and again["upload_url"] is None and again["version"] == 2
    assert again["artifact_id"] != art["artifact_id"]
    with factory() as s:
        assert s.get(Blob, again["artifact_id"]).sha256 == blob.sha256


def test_presign_counts_against_the_project_quota(env) -> None:
    _, client, _, _, roles = env
    resp = _presign(client, roles["member"], "big", b"x" * 1001)
    assert resp.status_code == 413


def test_download_url_and_blob_deleted_through_g0(env) -> None:
    app, client, factory, pid, roles = env
    member = roles["member"]
    data = b"adapter"
    art = _presign(client, member, "a", data).json()
    client.put(art["upload_url"], content=data, headers=member)
    client.post(f"/api/v1/mlops/artifacts/{art['id']}/commit", headers=member)
    dl = client.get(f"/api/v1/mlops/artifacts/{art['id']}/download-url", headers=roles["viewer"]).json()
    assert dl["download_url"] == f"/api/v1/artifacts/{art['artifact_id']}/download"
    assert client.get(dl["download_url"], headers=roles["viewer"]).content == data

    # the blob is a G0 artifact: deleting it leaves the metadata without content
    assert client.delete(f"/api/v1/artifacts/{art['artifact_id']}", headers=member).status_code == 204
    with factory() as s:
        assert s.get(ModelArtifact, art["id"]).artifact_id is None
    assert client.get(f"/api/v1/mlops/artifacts/{art['id']}/download-url", headers=member).status_code == 410


def test_external_uri_is_registered_by_reference(env) -> None:
    _, client, _, _, roles = env
    member = roles["member"]
    art = _presign(client, member, "ext", b"z", external_uri="hf://org/model").json()
    assert art["storage_backend"] == "external" and art["upload_url"] is None and art["artifact_id"] is None
    done = client.post(f"/api/v1/mlops/artifacts/{art['id']}/commit", headers=member, json={"size_bytes": 7})
    assert done.status_code == 200 and done.json()["size_bytes"] == 7
    dl = client.get(f"/api/v1/mlops/artifacts/{art['id']}/download-url", headers=member).json()
    assert dl["download_url"] == "hf://org/model"


def test_api_key_with_write_scope_uses_the_adapter(env) -> None:
    _, client, factory, pid, _ = env
    key = api_key_headers(factory, pid)
    data = b"from-sdk"
    art = _presign(client, key, "sdk", data).json()
    assert client.put(art["upload_url"], content=data, headers=key).status_code == 204
    assert client.post(f"/api/v1/mlops/artifacts/{art['id']}/commit", headers=key).json()["status"] == "committed"


class _PresigningStore(LocalArtifactStore):
    """Local store that answers like S3: presigned PUT/GET with signed headers."""

    def presign_put(self, project_id, sha256, size, ttl, *, upload_id):
        return PresignedRequest(
            "PUT",
            f"https://s3.example/{upload_id}?X-Amz-Signature=sig",
            {"Content-Length": str(size), "x-amz-checksum-sha256": "abc="},
            datetime.now(UTC) + timedelta(seconds=ttl),
        )

    def presign_get(self, project_id, sha256, ttl, *, filename, media_type):
        return PresignedRequest("GET", f"https://s3.example/{sha256}?X-Amz-Signature=sig")


def test_s3_style_presign_returns_signed_headers_and_presigned_download(env, tmp_path) -> None:
    app, client, factory, pid, roles = env
    member = roles["member"]
    store = _PresigningStore(tmp_path / "s3like")
    app.state.artifact_store = store
    data = b"s3-content"
    art = _presign(client, member, "s3", data).json()
    assert art["upload_url"].startswith("https://s3.example/")
    assert art["upload_headers"]["x-amz-checksum-sha256"] == "abc="
    # simulate the client's PUT to S3 landing in the staging key, then commit verifies it
    store.put(pid, io.BytesIO(data), max_bytes=len(data))
    done = client.post(f"/api/v1/mlops/artifacts/{art['id']}/commit", headers=member)
    assert done.status_code == 200, done.text
    dl = client.get(f"/api/v1/mlops/artifacts/{art['id']}/download-url", headers=member).json()
    assert dl["download_url"].startswith("https://s3.example/") and dl["sha256"] == hashlib.sha256(data).hexdigest()
