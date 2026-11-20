"""Model artifact & canonical G0 blob checks after removing `mlops_compat`."""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import api_key_headers, project_with_roles

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.storage.models import ModelArtifact, ModelVersion, RegisteredModel


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


def test_canonical_upload_counts_against_quota_and_supports_api_keys(env) -> None:
    _app, client, factory, pid, roles = env
    assert (
        client.post(
            "/api/v1/artifacts",
            content=b"x" * 1001,
            headers={**roles["member"], "x-filename": "big.bin", "content-type": "application/octet-stream"},
        ).status_code
        == 413
    )

    key = api_key_headers(factory, pid)
    payload = b"from-sdk"
    sha = hashlib.sha256(payload).hexdigest()
    up = client.post(
        "/api/v1/artifacts",
        content=payload,
        headers={**key, "x-filename": "sdk.bin", "content-type": "application/octet-stream", "x-sha256": sha},
    )
    assert up.status_code == 201, up.text
    dl = client.get(f"/api/v1/artifacts/{up.json()['id']}/download", headers=roles["viewer"])
    assert dl.status_code == 200 and dl.content == payload


def test_model_version_files_handles_external_and_legacy_artifacts(env) -> None:
    _app, client, factory, pid, roles = env
    with factory() as db:
        model = RegisteredModel(project_id=pid, name="m-ext")
        db.add(model)
        db.flush()
        ext_art = ModelArtifact(
            project_id=pid,
            name="ext",
            kind="model",
            version=1,
            external_uri="hf://org/model",
            sha256="a" * 64,
            size_bytes=7,
            status="committed",
        )
        legacy_art = ModelArtifact(
            project_id=pid,
            name="legacy",
            kind="model",
            version=1,
            status="committed",
            metadata_={"legacy_storage_uri": "file:///old/path"},
        )
        db.add_all([ext_art, legacy_art])
        db.flush()
        db.add_all(
            [
                ModelVersion(project_id=pid, model_id=model.id, version=1, artifact_id=ext_art.id),
                ModelVersion(project_id=pid, model_id=model.id, version=2, artifact_id=legacy_art.id),
            ]
        )
        db.commit()
        model_id = model.id

    ext_files = client.get(f"/api/v1/models/{model_id}/versions/1/files", headers=roles["viewer"])
    assert ext_files.status_code == 200
    assert ext_files.json()["files"][0]["external_uri"] == "hf://org/model"
    assert ext_files.json()["files"][0]["url"] is None

    legacy_files = client.get(f"/api/v1/models/{model_id}/versions/2/files", headers=roles["viewer"])
    assert legacy_files.status_code == 410
