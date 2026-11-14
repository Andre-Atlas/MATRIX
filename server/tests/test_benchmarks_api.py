"""Tests for MATRIX Benchmarks API, pluggable runner, anti-SSRF, and lineage."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from matrix_server.app import create_app
from matrix_server.auth import create_jwt
from matrix_server.benchmarks.runner import run_benchmark_job, validate_runner_url
from matrix_server.config import Settings
from matrix_server.storage.models import (
    ArtifactLineage,
    Dataset,
    ModelVersion,
    ProjectMember,
    RegisteredModel,
)


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/benchmarks_test.db",
        span_store="memory",
        artifact_store="local",
        artifact_dir=str(tmp_path / "artifacts"),
        jwt_secret="test-benchmark-jwt-secret",
    )
    app = create_app(settings)
    client = TestClient(app)

    # Register Project A (Alice = Admin)
    resp_a = client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "alice-password-123",
            "name": "Alice Admin",
            "project_name": "Project A",
        },
    )
    assert resp_a.status_code == 201
    data_a = resp_a.json()
    project_a = data_a["project"]["id"]
    admin_token_a = data_a["token"]

    # Register Project B (Bob = Admin)
    resp_b = client.post(
        "/api/v1/auth/register",
        json={
            "email": "bob@example.com",
            "password": "bob-password-123",
            "name": "Bob Admin",
            "project_name": "Project B",
        },
    )
    assert resp_b.status_code == 201
    data_b = resp_b.json()
    project_b = data_b["project"]["id"]
    admin_token_b = data_b["token"]

    # Register Member
    resp_mem = client.post(
        "/api/v1/auth/register",
        json={
            "email": "member@example.com",
            "password": "member-password-123",
            "name": "Project Member",
            "project_name": "Member Project",
        },
    )
    assert resp_mem.status_code == 201
    member_id = resp_mem.json()["user"]["id"]

    # Register Viewer
    resp_view = client.post(
        "/api/v1/auth/register",
        json={
            "email": "viewer@example.com",
            "password": "viewer-password-123",
            "name": "Project Viewer",
            "project_name": "Viewer Project",
        },
    )
    assert resp_view.status_code == 201
    viewer_id = resp_view.json()["user"]["id"]

    # Add member and viewer to Project A
    with app.state.session_factory() as db:
        db.add(ProjectMember(project_id=project_a, user_id=member_id, role="member"))
        db.add(ProjectMember(project_id=project_a, user_id=viewer_id, role="viewer"))
        db.commit()

    member_token_a = create_jwt({"sub": member_id}, settings.jwt_secret)
    viewer_token_a = create_jwt({"sub": viewer_id}, settings.jwt_secret)

    def headers(token: str, project_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "x-matrix-project-id": project_id}

    return {
        "app": app,
        "client": client,
        "project_a": project_a,
        "project_b": project_b,
        "admin_a": headers(admin_token_a, project_a),
        "member_a": headers(member_token_a, project_a),
        "viewer_a": headers(viewer_token_a, project_a),
        "admin_b": headers(admin_token_b, project_b),
        "viewer_token_a": headers(viewer_token_a, project_a),
        "member_token_a": headers(member_token_a, project_a),
        "admin_token_a": headers(admin_token_a, project_a),
        "admin_token_b": headers(admin_token_b, project_b),
    }


def auth(token_or_hdrs: Any) -> dict[str, str]:
    if isinstance(token_or_hdrs, dict):
        return token_or_hdrs
    return {"Authorization": f"Bearer {token_or_hdrs}"}



# --- 1. Catalog Endpoint Tests ----------------------------------------------


def test_catalog_endpoint_all_and_filtered(env):
    client: TestClient = env["client"]
    viewer = env["viewer_token_a"]

    # Unauthenticated fails
    resp = client.get("/api/v1/benchmarks/catalog")
    assert resp.status_code == 401

    # Full catalog
    resp = client.get("/api/v1/benchmarks/catalog", headers=auth(viewer))
    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    task_map = {t["name"]: t for t in tasks}

    # Verify LM-Eval tasks
    for name in ["mmlu_pro", "gpqa_diamond", "ifeval", "bbh", "humaneval", "gsm8k"]:
        assert name in task_map
        assert task_map[name]["suite"] == "lm_eval"
        assert task_map[name]["modality"] == "text"

    # Verify VLMEvalKit tasks
    for name in ["mmmu_pro", "mathvista", "docvqa", "chartqa", "ocrbench", "pope", "vidore"]:
        assert name in task_map
        assert task_map[name]["suite"] == "vlmevalkit"
        assert task_map[name]["modality"] == "multimodal"

    # Filter suite: lm_eval
    resp_lm = client.get("/api/v1/benchmarks/catalog?suite=lm_eval", headers=auth(viewer))
    assert resp_lm.status_code == 200
    lm_tasks = resp_lm.json()["tasks"]
    assert len(lm_tasks) == 6
    assert all(t["suite"] == "lm_eval" for t in lm_tasks)

    # Filter suite: vlmevalkit
    resp_vlm = client.get("/api/v1/benchmarks/catalog?suite=vlmevalkit", headers=auth(viewer))
    assert resp_vlm.status_code == 200
    vlm_tasks = resp_vlm.json()["tasks"]
    assert len(vlm_tasks) == 7
    assert all(t["suite"] == "vlmevalkit" for t in vlm_tasks)

    # Invalid suite returns 422
    resp_bad = client.get("/api/v1/benchmarks/catalog?suite=unknown_suite", headers=auth(viewer))
    assert resp_bad.status_code == 422


# --- 2. Benchmark Run Creation (LM-Eval & VLMEvalKit) with local_sim ---------


def test_benchmark_run_lm_eval_sync_local_sim(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]
    viewer = env["viewer_token_a"]

    payload = {
        "name": "deepseek-r1-lm-eval",
        "suite": "lm_eval",
        "tasks": ["mmlu_pro", "gsm8k"],
        "runner_backend": "local_sim",
        "runner_config": {"seed": 123},
        "eval_mode": "generate_until",
        "num_fewshot": 5,
        "target_model": "deepseek-ai/DeepSeek-R1",
        "sync_exec": True,
    }

    resp = client.post("/api/v1/benchmarks/runs", headers=auth(member), json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "deepseek-r1-lm-eval"
    assert data["status"] == "completed"
    assert data["suite"] == "lm_eval"
    assert len(data["task_results"]) == 2

    # Check task results
    task_res = {r["task_name"]: r for r in data["task_results"]}
    assert "mmlu_pro" in task_res
    assert task_res["mmlu_pro"]["primary_metric"] == "acc"
    assert 0.0 <= task_res["mmlu_pro"]["primary_score"] <= 1.0
    assert "acc" in task_res["mmlu_pro"]["metrics"]
    assert "acc_norm" in task_res["mmlu_pro"]["metrics"]

    assert "gsm8k" in task_res
    assert task_res["gsm8k"]["primary_metric"] == "exact_match"
    assert 0.0 <= task_res["gsm8k"]["primary_score"] <= 1.0

    # Summary metrics
    summary = data["summary_metrics"]
    assert summary["total_tasks"] == 2
    assert summary["total_samples"] == 100 + 1319
    assert 0.0 <= summary["mean_primary_score"] <= 1.0

    run_id = data["id"]

    # Detail GET
    detail_resp = client.get(f"/api/v1/benchmarks/runs/{run_id}", headers=auth(viewer))
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == run_id
    assert len(detail["task_results"]) == 2

    # List GET
    list_resp = client.get("/api/v1/benchmarks/runs?suite=lm_eval", headers=auth(viewer))
    assert list_resp.status_code == 200
    runs = list_resp.json()["runs"]
    assert any(r["id"] == run_id for r in runs)


def test_benchmark_run_vlmevalkit_sync_with_lineage(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]
    app = env["app"]
    project_a = env["project_a"]

    # Setup model version and dataset in Project A
    factory = app.state.session_factory
    with factory() as db, db.begin():
        rm = RegisteredModel(project_id=project_a, name="qwen2-vl-72b")
        db.add(rm)
        db.flush()
        mv = ModelVersion(project_id=project_a, model_id=rm.id, version=1)
        db.add(mv)
        ds = Dataset(project_id=project_a, name="docvqa-eval-set")
        db.add(ds)
        db.flush()
        mv_id = mv.id
        ds_id = ds.id

    payload = {
        "name": "qwen2-vl-multimodal-bench",
        "suite": "vlmevalkit",
        "tasks": ["mmmu_pro", "docvqa"],
        "runner_backend": "local_sim",
        "runner_config": {"seed": 999},
        "eval_mode": "multimodal",
        "num_fewshot": 0,
        "target_model": "Qwen/Qwen2-VL-72B-Instruct",
        "model_version_id": mv_id,
        "dataset_id": ds_id,
        "sync_exec": True,
    }

    resp = client.post("/api/v1/benchmarks/runs", headers=auth(member), json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "completed"
    assert data["suite"] == "vlmevalkit"
    assert len(data["task_results"]) == 2

    run_id = data["id"]

    # Verify lineage edges in mlops_artifact_lineage
    with factory() as db:
        edges = db.scalars(
            select(ArtifactLineage).where(
                ArtifactLineage.project_id == project_a,
                ArtifactLineage.target_type == "benchmark_run",
                ArtifactLineage.target_id == run_id,
            )
        ).all()
        assert len(edges) == 2
        edge_map = {e.source_type: e for e in edges}
        assert "model_version" in edge_map
        assert edge_map["model_version"].source_id == mv_id
        assert edge_map["model_version"].relation == "evaluated_by"

        assert "dataset" in edge_map
        assert edge_map["dataset"].source_id == ds_id
        assert edge_map["dataset"].relation == "evaluated_by"


# --- 3. Anti-SSRF Rejection Tests ------------------------------------------


@pytest.mark.parametrize(
    "disallowed_url",
    [
        "http://127.0.0.1:8000/v1",
        "http://127.0.0.1/v1",
        "http://localhost:8000/v1",
        "http://10.0.0.1:8000/v1",
        "http://192.168.1.1:8000/v1",
        "http://172.16.0.1:8000/v1",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/v1",
        "http://user:pass@api.together.xyz/v1",
        "http://internal.service.local/v1",
        "http://cluster.internal/v1",
        "ftp://api.together.xyz/v1",
        "gopher://api.together.xyz/v1",
    ],
)
def test_anti_ssrf_blocks_private_endpoints(disallowed_url: str):
    with pytest.raises(ValueError):
        validate_runner_url(disallowed_url)


def test_anti_ssrf_rejected_in_api(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]

    payload = {
        "name": "ssrf-exploit-run",
        "suite": "lm_eval",
        "tasks": ["mmlu_pro"],
        "runner_backend": "http",
        "runner_config": {"endpoint_url": "http://169.254.169.254/latest/meta-data/"},
        "target_model": "gpt-4o",
    }
    resp = client.post("/api/v1/benchmarks/runs", headers=auth(member), json=payload)
    assert resp.status_code == 422
    assert "not allowed" in resp.text or "disallowed" in resp.text or "RFC1918" in resp.text


def test_anti_ssrf_allows_public_https_endpoint():
    valid = "https://api.together.xyz/v1"
    assert validate_runner_url(valid) == valid


# --- 4. Anti-IDOR and Multi-Tenant Isolation --------------------------------


def test_anti_idor_and_isolation(env):
    client: TestClient = env["client"]
    admin_a = env["admin_token_a"]
    admin_b = env["admin_token_b"]
    app = env["app"]
    project_b = env["project_b"]

    # Create dataset in Project B
    factory = app.state.session_factory
    with factory() as db, db.begin():
        ds_b = Dataset(project_id=project_b, name="secret-b-dataset")
        db.add(ds_b)
        db.flush()
        ds_b_id = ds_b.id

    # Project A cannot create run referencing Project B's dataset
    bad_payload = {
        "name": "cross-tenant-run",
        "suite": "lm_eval",
        "tasks": ["mmlu_pro"],
        "target_model": "llama-3",
        "dataset_id": ds_b_id,
    }
    resp = client.post("/api/v1/benchmarks/runs", headers=auth(admin_a), json=bad_payload)
    assert resp.status_code == 404

    # Create valid run in Project A
    valid_payload = {
        "name": "tenant-a-run",
        "suite": "lm_eval",
        "tasks": ["gsm8k"],
        "target_model": "llama-3",
        "sync_exec": True,
    }
    resp_a = client.post("/api/v1/benchmarks/runs", headers=auth(admin_a), json=valid_payload)
    assert resp_a.status_code == 201
    run_a_id = resp_a.json()["id"]

    # Project B cannot see Project A's run
    resp_b_get = client.get(f"/api/v1/benchmarks/runs/{run_a_id}", headers=auth(admin_b))
    assert resp_b_get.status_code == 404

    # Project B's run list does not include run_a
    list_b = client.get("/api/v1/benchmarks/runs", headers=auth(admin_b)).json()["runs"]
    assert not any(r["id"] == run_a_id for r in list_b)


# --- 5. Anti-NaN Validation -------------------------------------------------


def test_anti_nan_rejection(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]

    # Body containing NaN in runner_config
    # Body containing NaN in runner_config
    bad_nan = b'{"name": "nan-run", "suite": "lm_eval", "tasks": ["mmlu_pro"], "target_model": "test-model", "runner_config": {"temperature": NaN}}'
    resp_nan = client.post(
        "/api/v1/benchmarks/runs",
        headers={**auth(member), "content-type": "application/json"},
        content=bad_nan,
    )
    assert resp_nan.status_code == 422
    assert "NaN or Infinity" in resp_nan.text

    # Body containing Infinity in runner_config
    bad_inf = b'{"name": "inf-run", "suite": "lm_eval", "tasks": ["mmlu_pro"], "target_model": "test-model", "runner_config": {"temperature": Infinity}}'
    resp_inf = client.post(
        "/api/v1/benchmarks/runs",
        headers={**auth(member), "content-type": "application/json"},
        content=bad_inf,
    )
    assert resp_inf.status_code == 422
    assert "NaN or Infinity" in resp_inf.text



# --- 6. RBAC and Cancellation -----------------------------------------------


def test_rbac_and_run_cancellation(env):
    client: TestClient = env["client"]
    viewer = env["viewer_token_a"]
    member = env["member_token_a"]

    payload = {
        "name": "rbac-cancellation-run",
        "suite": "lm_eval",
        "tasks": ["humaneval"],
        "target_model": "deepseek-coder",
        "sync_exec": False,
    }

    # Viewer cannot create runs (403)
    resp_viewer = client.post("/api/v1/benchmarks/runs", headers=auth(viewer), json=payload)
    assert resp_viewer.status_code == 403

    # Member can create run
    resp_member = client.post("/api/v1/benchmarks/runs", headers=auth(member), json=payload)
    assert resp_member.status_code == 201
    run_data = resp_member.json()
    run_id = run_data["id"]
    assert run_data["status"] == "queued"
    assert run_data["job_id"] is not None

    # Viewer cannot cancel (403)
    resp_cancel_viewer = client.post(f"/api/v1/benchmarks/runs/{run_id}/cancel", headers=auth(viewer))
    assert resp_cancel_viewer.status_code == 403

    # Member can cancel
    resp_cancel_member = client.post(f"/api/v1/benchmarks/runs/{run_id}/cancel", headers=auth(member))
    assert resp_cancel_member.status_code == 200
    cancelled = resp_cancel_member.json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"] is not None

    # Idempotent re-cancel returns existing status
    resp_recancel = client.post(f"/api/v1/benchmarks/runs/{run_id}/cancel", headers=auth(member))
    assert resp_recancel.status_code == 200
    assert resp_recancel.json()["status"] == "cancelled"


# --- 7. Worker Execution Logic ----------------------------------------------


def test_async_run_benchmark_job_execution(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]
    viewer = env["viewer_token_a"]
    app = env["app"]

    payload = {
        "name": "async-worker-test",
        "suite": "lm_eval",
        "tasks": ["bbh", "ifeval"],
        "runner_backend": "local_sim",
        "runner_config": {"seed": 777},
        "target_model": "meta-llama/Llama-3-70B-Instruct",
        "sync_exec": False,
    }
    resp = client.post("/api/v1/benchmarks/runs", headers=auth(member), json=payload)
    assert resp.status_code == 201
    run_id = resp.json()["id"]
    assert resp.json()["status"] == "queued"

    # Manually trigger worker job execution
    run_benchmark_job(app.state.session_factory, run_id)

    # Check that the run transitioned to completed and stored results
    resp_detail = client.get(f"/api/v1/benchmarks/runs/{run_id}", headers=auth(viewer))
    assert resp_detail.status_code == 200
    data = resp_detail.json()
    assert data["status"] == "completed"
    assert len(data["task_results"]) == 2
    assert "bbh" in [r["task_name"] for r in data["task_results"]]
    assert "ifeval" in [r["task_name"] for r in data["task_results"]]
    assert data["summary_metrics"]["total_tasks"] == 2
