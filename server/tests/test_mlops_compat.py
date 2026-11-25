"""Checks verifying `mlops_compat` route removal, OpenAPI cleanup, and G0 BlobArtifact -> ModelVersion bridging."""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import project_with_roles

from matrix_server.app import create_app
from matrix_server.config import Settings


@pytest.fixture
def env(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path}/compat.db",
            span_store="memory",
            artifact_backend="local",
            artifact_local_dir=str(tmp_path / "artifacts"),
        )
    )
    client = TestClient(app)
    factory = app.state.session_factory
    _, roles_a = project_with_roles(factory, "Project A")
    _, roles_b = project_with_roles(factory, "Project B")
    yield client, roles_a, roles_b
    app.state.db_engine.dispose()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/mlops/runs"),
        ("POST", "/api/v1/mlops/runs"),
        ("GET", "/api/v1/mlops/runs/r1"),
        ("POST", "/api/v1/mlops/runs/r1/metrics"),
        ("GET", "/api/v1/mlops/sweeps"),
        ("POST", "/api/v1/mlops/sweeps/s1/trials/next"),
        ("GET", "/api/v1/mlops/artifacts"),
        ("POST", "/api/v1/mlops/artifacts/presign-upload"),
    ],
)
def test_removed_compat_routes_return_404(env, method: str, path: str) -> None:
    client, roles_a, _ = env
    resp = client.request(method, path, headers=roles_a["member"], json={})
    assert resp.status_code in (404, 405), (method, path, resp.status_code, resp.text)


def test_openapi_excludes_removed_compat_routes_and_keeps_canonical(env) -> None:
    client, _roles_a, _ = env
    paths = set(client.app.openapi()["paths"])

    for path in paths:
        if path.startswith(("/api/v1/mlops/runs", "/api/v1/mlops/sweeps", "/api/v1/mlops/artifacts")):
            assert path.endswith("/lineage"), f"Deprecated compat route still in OpenAPI: {path}"

    for expected in (
        "/api/v1/runs",
        "/api/v1/sweeps",
        "/api/v1/artifacts",
        "/api/v1/models/{model_id}/versions/{version}/files",
        "/api/v1/mlops/models",
        "/api/v1/lineage",
    ):
        assert expected in paths, f"Missing expected route in OpenAPI: {expected}"


def test_create_model_version_bridges_ready_g0_blob_artifact(env) -> None:
    client, roles_a, roles_b = env
    member_a = roles_a["member"]
    viewer_a = roles_a["viewer"]
    admin_b = roles_b["admin"]

    blob_bytes = b"g0-weights-payload-v1"
    sha = hashlib.sha256(blob_bytes).hexdigest()

    run = client.post("/api/v1/runs", headers=member_a, json={"name": "bridge-run"}).json()
    up = client.post(
        "/api/v1/artifacts",
        content=blob_bytes,
        headers={
            **member_a,
            "x-filename": "weights.safetensors",
            "content-type": "application/octet-stream",
            "x-sha256": sha,
            "x-owner-kind": "run",
            "x-owner-id": run["id"],
        },
    )
    assert up.status_code == 201, up.text
    blob_id = up.json()["id"]

    model = client.post("/api/v1/mlops/models", headers=member_a, json={"name": "bridged-model"}).json()

    # Create v1 passing the G0 BlobArtifact ID directly
    mv1_resp = client.post(
        f"/api/v1/mlops/models/{model['id']}/versions",
        headers=member_a,
        json={"artifact_id": blob_id, "run_id": run["id"]},
    )
    assert mv1_resp.status_code == 201, mv1_resp.text
    mv1 = mv1_resp.json()
    assert mv1["artifact_id"] is not None

    # /files serves the bridged G0 blob download URL & bytes
    files_resp = client.get(f"/api/v1/models/{model['id']}/versions/1/files", headers=viewer_a)
    assert files_resp.status_code == 200, files_resp.text
    files = files_resp.json()["files"]
    assert len(files) == 1
    assert files[0]["artifact_id"] == blob_id
    assert files[0]["model_artifact_id"] == mv1["artifact_id"]
    assert files[0]["sha256"] == sha
    dl_resp = client.get(files[0]["url"], headers=viewer_a)
    assert dl_resp.status_code == 200 and dl_resp.content == blob_bytes

    # Creating v2 with the same G0 BlobArtifact ID reuses the bridged ModelArtifact
    mv2_resp = client.post(
        f"/api/v1/mlops/models/{model['id']}/versions",
        headers=member_a,
        json={"artifact_id": blob_id},
    )
    assert mv2_resp.status_code == 201, mv2_resp.text
    assert mv2_resp.json()["artifact_id"] == mv1["artifact_id"]

    # Lineage connects artifact -> model_version and run -> artifact
    lin_resp = client.get(f"/api/v1/lineage?node=model_version:{mv1['id']}&direction=up&depth=2", headers=viewer_a)
    assert lin_resp.status_code == 200, lin_resp.text
    rels = {(e["source_type"], e["target_type"], e["relation"]) for e in lin_resp.json()["edges"]}
    assert ("artifact", "model_version", "packaged_as") in rels
    assert ("run", "artifact", "produced") in rels

    # Pending G0 blob -> 409; unknown / cross-project G0 blob -> 404
    pending_up = client.post(
        "/api/v1/artifacts/uploads",
        headers=member_a,
        json={"filename": "pending.bin", "size": 4, "sha256": hashlib.sha256(b"test").hexdigest()},
    )
    assert pending_up.status_code == 201, pending_up.text
    pending_id = pending_up.json()["artifact"]["id"]
    assert (
        client.post(
            f"/api/v1/mlops/models/{model['id']}/versions",
            headers=member_a,
            json={"artifact_id": pending_id},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/mlops/models/{model['id']}/versions",
            headers=member_a,
            json={"artifact_id": "missing-blob-id"},
        ).status_code
        == 404
    )
    model_b = client.post("/api/v1/mlops/models", headers=admin_b, json={"name": "b-model"}).json()
    assert (
        client.post(
            f"/api/v1/mlops/models/{model_b['id']}/versions",
            headers=admin_b,
            json={"artifact_id": blob_id},
        ).status_code
        == 404
    )
