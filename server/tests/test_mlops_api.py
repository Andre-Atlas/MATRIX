"""End-to-end API, RBAC, multi-tenant isolation, and security tests for MLOps Studio."""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from matrix_server.app import create_app
from matrix_server.auth import create_jwt
from matrix_server.config import Settings
from matrix_server.storage.models import Dataset, Experiment, SafetyScan


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/mlops_api.db",
        span_store="memory",
        artifact_store="local",
        artifact_dir=str(tmp_path / "artifacts"),
        jwt_secret="test-mlops-jwt-secret",
    )
    app = create_app(settings)
    client = TestClient(app)

    # Register Project A (Alice = admin)
    reg_a = client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "alice-password-123",
            "name": "Alice Admin",
            "project_name": "Project A",
        },
    )
    assert reg_a.status_code == 201
    data_a = reg_a.json()
    project_a = data_a["project"]["id"]
    admin_token_a = data_a["token"]

    # Register Project B (Bob = admin)
    reg_b = client.post(
        "/api/v1/auth/register",
        json={
            "email": "bob@example.com",
            "password": "bob-password-123",
            "name": "Bob Admin",
            "project_name": "Project B",
        },
    )
    assert reg_b.status_code == 201
    data_b = reg_b.json()
    project_b = data_b["project"]["id"]
    admin_token_b = data_b["token"]

    # Create member & viewer users in Project A
    member_reg = client.post(
        "/api/v1/auth/register",
        json={
            "email": "member@example.com",
            "password": "member-password-123",
            "name": "Mike Member",
            "project_name": "Member Own Project",
        },
    )
    assert member_reg.status_code == 201
    member_id = member_reg.json()["user"]["id"]

    viewer_reg = client.post(
        "/api/v1/auth/register",
        json={
            "email": "viewer@example.com",
            "password": "viewer-password-123",
            "name": "Vera Viewer",
            "project_name": "Viewer Own Project",
        },
    )
    assert viewer_reg.status_code == 201
    viewer_id = viewer_reg.json()["user"]["id"]

    # Add member and viewer to Project A via DB
    from matrix_server.storage.models import ProjectMember

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
    }


def test_end_to_end_mlops_flow_and_deduplication(env):
    client: TestClient = env["client"]
    app = env["app"]
    hdrs = env["member_a"]
    admin_hdrs = env["admin_a"]
    pid_a = env["project_a"]

    # Seed dataset, experiment, and safety_scan in Project A
    with app.state.session_factory() as db:
        ds = Dataset(project_id=pid_a, name="sft-golden-ds", description="SFT dataset")
        db.add(ds)
        db.flush()
        exp = Experiment(
            project_id=pid_a,
            name="lora-eval-exp",
            dataset_id=ds.id,
            variants=[{"name": "v1"}],
            metrics=[{"name": "exact_match"}],
            status="done",
        )
        scan = SafetyScan(project_id=pid_a, target_model="llama3-lora", status="passed", global_score=96.0)
        db.add_all([exp, scan])
        db.commit()
        dataset_id = ds.id
        exp_id = exp.id
        scan_id = scan.id

    # 1. POST /runs
    run_resp = client.post(
        "/api/v1/mlops/runs",
        headers=hdrs,
        json={
            "name": "llama3-lora-sft-1",
            "run_type": "lora",
            "base_model": "meta-llama/Llama-3-8B",
            "dataset_id": dataset_id,
            "hyperparameters": {"lr": 2e-4, "lora_r": 16, "epochs": 3},
            "tags": ["sft", "lora"],
        },
    )
    assert run_resp.status_code == 201, run_resp.text
    run = run_resp.json()
    run_id = run["id"]
    assert run["dataset_id"] == dataset_id
    assert run["hyperparams"]["lora_r"] == 16

    # 2. POST /runs/{id}/metrics (60 points to test downsampling)
    points = [
        {
            "category": "train",
            "metric_name": "loss",
            "step": i,
            "epoch": i / 20.0,
            "value": 2.0 / (i + 1),
        }
        for i in range(1, 61)
    ]
    m_resp = client.post(
        f"/api/v1/mlops/runs/{run_id}/metrics",
        headers=hdrs,
        json={"points": points},
    )
    assert m_resp.status_code == 202, m_resp.text
    m_data = m_resp.json()
    assert m_data["accepted"] == 60
    assert m_data["should_stop"] is False

    # 3. GET /runs/{id}/metrics with downsampling
    q_resp = client.get(
        f"/api/v1/mlops/runs/{run_id}/metrics?metric_names=loss&max_points=10",
        headers=env["viewer_a"],
    )
    assert q_resp.status_code == 200
    series = q_resp.json()["series"]["loss"]
    assert 1 <= len(series) <= 10
    assert all("min" in p and "max" in p and "step" in p for p in series)

    # 4. PATCH /runs/{id} with classic_eval and completed
    patch_resp = client.patch(
        f"/api/v1/mlops/runs/{run_id}",
        headers=hdrs,
        json={
            "status": "completed",
            "classic_eval": {"roc_auc": 0.94, "f1": 0.89},
            "hardware_info": {"gpu": "A100-80GB", "count": 1},
        },
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "completed"
    assert patch_resp.json()["classic_eval"]["roc_auc"] == 0.94

    # 5. Presign upload + PUT local + commit
    blob = b"safetensors-binary-weights-payload-v1"
    sha = hashlib.sha256(blob).hexdigest()

    presign_resp = client.post(
        "/api/v1/mlops/artifacts/presign-upload",
        headers=hdrs,
        json={
            "name": "adapter-weights",
            "kind": "lora_adapter",
            "sha256": sha,
            "size_bytes": len(blob),
            "run_id": run_id,
        },
    )
    assert presign_resp.status_code == 201, presign_resp.text
    art1 = presign_resp.json()
    assert art1["deduplicated"] is False
    assert art1["upload_url"] is not None
    assert art1["version"] == 1

    # PUT local presigned URL (no Bearer header required)
    put_resp = client.put(art1["upload_url"], content=blob)
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["size_bytes"] == len(blob)

    # Commit artifact
    commit_resp = client.post(f"/api/v1/mlops/artifacts/{art1['id']}/commit", headers=hdrs)
    assert commit_resp.status_code == 200, commit_resp.text
    assert commit_resp.json()["status"] == "committed"

    # Download URL & GET local
    dl_resp = client.get(f"/api/v1/mlops/artifacts/{art1['id']}/download-url", headers=env["viewer_a"])
    assert dl_resp.status_code == 200
    dl_url = dl_resp.json()["download_url"]
    get_blob = client.get(dl_url)
    assert get_blob.status_code == 200
    assert get_blob.content == blob

    # 6. Deduplicated presign-upload with same sha256
    dedup_resp = client.post(
        "/api/v1/mlops/artifacts/presign-upload",
        headers=hdrs,
        json={
            "name": "adapter-weights",
            "kind": "lora_adapter",
            "sha256": sha,
            "size_bytes": len(blob),
            "run_id": run_id,
        },
    )
    assert dedup_resp.status_code == 201
    art2 = dedup_resp.json()
    assert art2["deduplicated"] is True
    assert art2["upload_url"] is None
    assert art2["version"] == 2
    assert art2["status"] == "committed"

    # GET /runs/{id} includes produced artifacts
    run_detail = client.get(f"/api/v1/mlops/runs/{run_id}", headers=env["viewer_a"])
    assert run_detail.status_code == 200
    assert len(run_detail.json()["artifacts"]) == 2

    # 7. Model & LoRA Registry
    model_resp = client.post(
        "/api/v1/mlops/models",
        headers=hdrs,
        json={
            "name": "support-bot-lora",
            "model_type": "lora",
            "base_model": "meta-llama/Llama-3-8B",
            "description": "Customer support LoRA adapter",
        },
    )
    assert model_resp.status_code == 201
    model_id = model_resp.json()["id"]

    mv_resp = client.post(
        f"/api/v1/mlops/models/{model_id}/versions",
        headers=hdrs,
        json={
            "artifact_id": art1["id"],
            "run_id": run_id,
            "eval_experiment_id": exp_id,
            "safety_scan_id": scan_id,
            "lora_config": {"r": 16, "lora_alpha": 32},
            "signature": {"inputs": ["prompt"], "outputs": ["response"]},
            "metrics_snapshot": {"loss": 0.033, "roc_auc": 0.94},
        },
    )
    assert mv_resp.status_code == 201, mv_resp.text
    mv = mv_resp.json()
    assert mv["version"] == 1

    # Admin sets @champion alias
    alias_resp = client.post(
        f"/api/v1/mlops/models/{model_id}/aliases",
        headers=admin_hdrs,
        json={"alias": "champion", "version": 1},
    )
    assert alias_resp.status_code == 200
    assert alias_resp.json()["alias"] == "champion"

    # 8. Lineage DAGs
    model_lineage = client.get(f"/api/v1/mlops/models/{model_id}/lineage", headers=env["viewer_a"])
    assert model_lineage.status_code == 200
    ml_data = model_lineage.json()
    node_types = {n["node_type"] for n in ml_data["nodes"]}
    assert {"dataset", "run", "artifact", "model_version", "experiment", "safety_scan"}.issubset(node_types)
    relations = {e["relation"] for e in ml_data["edges"]}
    assert {"trained_on", "produced", "packaged_as", "registered_as", "evaluated_by", "audited_by"}.issubset(relations)

    run_lineage = client.get(f"/api/v1/mlops/runs/{run_id}/lineage", headers=env["viewer_a"])
    assert run_lineage.status_code == 200
    assert len(run_lineage.json()["nodes"]) >= 4

    # 9. Evaluate model version
    eval_resp = client.post(
        f"/api/v1/mlops/models/{model_id}/versions/1/evaluate",
        headers=hdrs,
        json={"dataset_id": dataset_id, "metrics": [{"name": "exact_match"}]},
    )
    assert eval_resp.status_code == 202
    assert eval_resp.json()["status"] == "queued"


def test_sweep_ask_and_tell_with_early_stopping(env):
    client: TestClient = env["client"]
    hdrs = env["member_a"]

    sweep_resp = client.post(
        "/api/v1/mlops/sweeps",
        headers=hdrs,
        json={
            "name": "lr-asha-sweep",
            "method": "random",
            "metric_name": "eval_loss",
            "metric_goal": "minimize",
            "search_space": {
                "lr": {"type": "float", "min": 1e-5, "max": 1e-2, "log": True},
                "batch_size": {"type": "categorical", "values": [8, 16, 32]},
            },
            "early_stopping": {"strategy": "median", "min_step": 5},
            "max_trials": 5,
        },
    )
    assert sweep_resp.status_code == 201, sweep_resp.text
    sweep_id = sweep_resp.json()["id"]

    # Duplicate sweep name -> 409
    dup_resp = client.post(
        "/api/v1/mlops/sweeps",
        headers=hdrs,
        json={
            "name": "lr-asha-sweep",
            "method": "random",
            "metric_name": "eval_loss",
            "metric_goal": "minimize",
            "search_space": {"lr": {"type": "float", "min": 1e-5, "max": 1e-2}},
        },
    )
    assert dup_resp.status_code == 409

    # Trial 1: good loss (0.2 at step 10)
    t1 = client.post(f"/api/v1/mlops/sweeps/{sweep_id}/trials/next", headers=hdrs, json={})
    assert t1.status_code == 201
    r1_id = t1.json()["run"]["id"]
    m1 = client.post(
        f"/api/v1/mlops/runs/{r1_id}/metrics",
        headers=hdrs,
        json={"points": [{"category": "eval", "metric_name": "eval_loss", "step": 10, "value": 0.2}]},
    )
    assert m1.status_code == 202
    assert m1.json()["should_stop"] is False

    # Trial 2: good loss (0.3 at step 10)
    t2 = client.post(f"/api/v1/mlops/sweeps/{sweep_id}/trials/next", headers=hdrs, json={})
    assert t2.status_code == 201
    r2_id = t2.json()["run"]["id"]
    client.post(
        f"/api/v1/mlops/runs/{r2_id}/metrics",
        headers=hdrs,
        json={"points": [{"category": "eval", "metric_name": "eval_loss", "step": 10, "value": 0.3}]},
    )

    # Trial 3: worse than peer median (0.25) -> should_stop=True
    t3 = client.post(f"/api/v1/mlops/sweeps/{sweep_id}/trials/next", headers=hdrs, json={})
    assert t3.status_code == 201
    r3_id = t3.json()["run"]["id"]
    m3 = client.post(
        f"/api/v1/mlops/runs/{r3_id}/metrics",
        headers=hdrs,
        json={"points": [{"category": "eval", "metric_name": "eval_loss", "step": 10, "value": 0.95}]},
    )
    assert m3.status_code == 202
    assert m3.json()["should_stop"] is True
    assert m3.json()["stop_reason"] is not None

    # GET /sweeps/{id} returns trials and analysis
    detail = client.get(f"/api/v1/mlops/sweeps/{sweep_id}", headers=env["viewer_a"])
    assert detail.status_code == 200
    d_json = detail.json()
    assert d_json["best_run_id"] == r1_id
    assert len(d_json["trials"]) == 3
    assert "parallel_coordinates" in d_json["analysis"]
    assert "importance" in d_json["analysis"]

    # Stop sweep -> next trial returns 409
    stop_resp = client.post(f"/api/v1/mlops/sweeps/{sweep_id}/stop", headers=hdrs)
    assert stop_resp.status_code == 200
    assert stop_resp.json()["stopped"] is True

    after_stop = client.post(f"/api/v1/mlops/sweeps/{sweep_id}/trials/next", headers=hdrs, json={})
    assert after_stop.status_code == 409


def test_rbac_multi_tenant_isolation_and_security(env):
    client: TestClient = env["client"]
    viewer_a = env["viewer_a"]
    member_a = env["member_a"]
    admin_a = env["admin_a"]
    admin_b = env["admin_b"]
    app = env["app"]

    # 1. Viewer cannot create run or model (403)
    assert client.post("/api/v1/mlops/runs", headers=viewer_a, json={"name": "v-run"}).status_code == 403
    assert client.post("/api/v1/mlops/models", headers=viewer_a, json={"name": "v-model"}).status_code == 403

    # 2. Member creates model & version, can set staging/@challenger, but 403 for production/@champion/DELETE
    m_resp = client.post("/api/v1/mlops/models", headers=member_a, json={"name": "rbac-model"})
    assert m_resp.status_code == 201
    model_id = m_resp.json()["id"]

    mv_resp = client.post(f"/api/v1/mlops/models/{model_id}/versions", headers=member_a, json={})
    assert mv_resp.status_code == 201

    assert (
        client.patch(
            f"/api/v1/mlops/models/{model_id}/versions/1/stage",
            headers=member_a,
            json={"stage": "staging"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/mlops/models/{model_id}/aliases",
            headers=member_a,
            json={"alias": "@challenger", "version": 1},
        ).status_code
        == 200
    )

    # Member forbidden on production / @champion / DELETE
    assert (
        client.patch(
            f"/api/v1/mlops/models/{model_id}/versions/1/stage",
            headers=member_a,
            json={"stage": "production"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/mlops/models/{model_id}/aliases",
            headers=member_a,
            json={"alias": "@champion", "version": 1},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/mlops/models/{model_id}/aliases",
            headers=member_a,
            json={"alias": "production", "version": 1},
        ).status_code
        == 403
    )
    # Case-insensitive admin-only aliases also forbidden for member
    for case_alias in ("Production", "CHAMPION", "@Champion", "@PrOdUcTiOn"):
        assert (
            client.post(
                f"/api/v1/mlops/models/{model_id}/aliases",
                headers=member_a,
                json={"alias": case_alias, "version": 1},
            ).status_code
            == 403
        )
    assert client.delete(f"/api/v1/mlops/models/{model_id}", headers=member_a).status_code == 403

    # Admin succeeds on production & @champion
    assert (
        client.patch(
            f"/api/v1/mlops/models/{model_id}/versions/1/stage",
            headers=admin_a,
            json={"stage": "production"},
        ).status_code
        == 200
    )
    # Member cannot demote a version currently in production
    for target_stage in ("staging", "archived", "none"):
        assert (
            client.patch(
                f"/api/v1/mlops/models/{model_id}/versions/1/stage",
                headers=member_a,
                json={"stage": target_stage},
            ).status_code
            == 403
        )
    assert (
        client.post(
            f"/api/v1/mlops/models/{model_id}/aliases",
            headers=admin_a,
            json={"alias": "@champion", "version": 1},
        ).status_code
        == 200
    )

    # DELETE blocked 409 unless ?force=true
    assert client.delete(f"/api/v1/mlops/models/{model_id}", headers=admin_a).status_code == 409

    # 3. Multi-tenant isolation (Project B gets 404 on Project A resources)
    run_a = client.post("/api/v1/mlops/runs", headers=member_a, json={"name": "tenant-a-run"}).json()
    run_a_id = run_a["id"]
    sweep_a = client.post(
        "/api/v1/mlops/sweeps",
        headers=member_a,
        json={
            "name": "tenant-a-sweep",
            "method": "random",
            "metric_name": "loss",
            "metric_goal": "minimize",
            "search_space": {"lr": {"type": "float", "min": 0.001, "max": 0.01}},
        },
    ).json()
    art_a = client.post(
        "/api/v1/mlops/artifacts/presign-upload",
        headers=member_a,
        json={"name": "tenant-a-art", "sha256": "a" * 64, "size_bytes": 10},
    ).json()

    assert client.get(f"/api/v1/mlops/runs/{run_a_id}", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/mlops/runs/{run_a_id}/metrics", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/mlops/runs/{run_a_id}/lineage", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/mlops/sweeps/{sweep_a['id']}", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/mlops/artifacts/{art_a['id']}/download-url", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/mlops/models/{model_id}", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/mlops/models/{model_id}/lineage", headers=admin_b).status_code == 404

    # Anti-IDOR: Project B cannot link Project A's dataset_id or artifact_id
    with app.state.session_factory() as db:
        ds_a = Dataset(project_id=env["project_a"], name="secret-ds-a")
        db.add(ds_a)
        db.commit()
        ds_a_id = ds_a.id

    assert (
        client.post(
            "/api/v1/mlops/runs",
            headers=admin_b,
            json={"name": "idor-run", "dataset_id": ds_a_id},
        ).status_code
        == 404
    )
    model_b = client.post("/api/v1/mlops/models", headers=admin_b, json={"name": "model-b"}).json()
    assert (
        client.post(
            f"/api/v1/mlops/models/{model_b['id']}/versions",
            headers=admin_b,
            json={"artifact_id": art_a["id"]},
        ).status_code
        == 404
    )

    # 4. Anti-NaN / Infinity (422)
    nan_run_body = json.dumps({"name": "nan-run", "hyperparameters": {"lr": float("nan")}})
    assert (
        client.post(
            "/api/v1/mlops/runs",
            headers={**member_a, "Content-Type": "application/json"},
            content=nan_run_body,
        ).status_code
        == 422
    )
    inf_metrics_body = json.dumps(
        {"points": [{"category": "train", "metric_name": "loss", "step": 1, "value": float("inf")}]}
    )
    assert (
        client.post(
            f"/api/v1/mlops/runs/{run_a_id}/metrics",
            headers={**member_a, "Content-Type": "application/json"},
            content=inf_metrics_body,
        ).status_code
        == 422
    )
    nan_eval_body = json.dumps({"classic_eval": {"roc_auc": float("nan")}})
    assert (
        client.patch(
            f"/api/v1/mlops/runs/{run_a_id}",
            headers={**member_a, "Content-Type": "application/json"},
            content=nan_eval_body,
        ).status_code
        == 422
    )

    # 5. Anti-SSRF on external_uri (422)
    for bad_uri in ("file:///etc/passwd", "http://169.254.169.254/latest/meta-data", "s3://localhost/bucket"):
        resp = client.post(
            "/api/v1/mlops/artifacts/presign-upload",
            headers=member_a,
            json={
                "name": "ssrf-art",
                "sha256": "b" * 64,
                "size_bytes": 100,
                "external_uri": bad_uri,
            },
        )
        assert resp.status_code == 422, f"Expected 422 for {bad_uri}, got {resp.status_code}"

    # 6. Invalid local presigned token returns 403
    assert client.put("/api/v1/mlops/artifacts/local/invalid.token", content=b"x").status_code == 403
    assert client.get("/api/v1/mlops/artifacts/local/invalid.token").status_code == 403

    # 7. Admin force delete works
    assert client.delete(f"/api/v1/mlops/models/{model_id}?force=true", headers=admin_a).status_code == 200


def test_bayes_sweep_api_passes_metric_name_with_heartbeat(env, monkeypatch: pytest.MonkeyPatch) -> None:
    from matrix_server.mlops import sweeps as sweeps_mod

    client: TestClient = env["client"]
    hdrs = env["member_a"]

    sweep_resp = client.post(
        "/api/v1/mlops/sweeps",
        headers=hdrs,
        json={
            "name": "bayes-api-sweep",
            "method": "bayes",
            "metric_name": "val_loss",
            "metric_goal": "minimize",
            "search_space": {
                "x": {"type": "float", "min": 0.0, "max": 1.0},
                "y": {"type": "float", "min": 0.0, "max": 1.0},
            },
        },
    )
    assert sweep_resp.status_code == 201
    sweep_id = sweep_resp.json()["id"]

    for idx in range(6):
        t_resp = client.post(f"/api/v1/mlops/sweeps/{sweep_id}/trials/next", headers=hdrs, json={})
        assert t_resp.status_code == 201
        run_id = t_resp.json()["run"]["id"]
        hp = t_resp.json()["hyperparams"]
        val = (hp["x"] - 0.2) ** 2 + (hp["y"] - 0.8) ** 2
        m_resp = client.post(
            f"/api/v1/mlops/runs/{run_id}/metrics",
            headers=hdrs,
            json={"points": [{"category": "eval", "metric_name": "val_loss", "step": idx + 1, "value": val}]},
        )
        assert m_resp.status_code == 202

    seen_metric_names: list[str | None] = []
    real_suggest_bayes = sweeps_mod._suggest_bayes

    def spy_suggest_bayes(space, trials, metric_goal, rng, **kwargs):
        seen_metric_names.append(kwargs.get("metric_name"))
        # Ensure at least warmup_trials have non-None extracted metric
        extracted = [
            sweeps_mod._extract_trial_metric(t, metric_name=kwargs.get("metric_name"))
            for t in trials
        ]
        assert sum(1 for m in extracted if m is not None) == 6
        return real_suggest_bayes(space, trials, metric_goal, rng, **kwargs)

    monkeypatch.setattr(sweeps_mod, "_suggest_bayes", spy_suggest_bayes)
    t7 = client.post(f"/api/v1/mlops/sweeps/{sweep_id}/trials/next", headers=hdrs, json={})
    assert t7.status_code == 201
    assert seen_metric_names == ["val_loss"]


def test_local_artifact_upload_bypasses_json_limit_and_validates_sha256(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/art_limits.db",
        span_store="memory",
        artifact_store="local",
        artifact_dir=str(tmp_path / "artifacts"),
        jwt_secret="test-mlops-jwt-secret",
        max_json_body_bytes=1000,
        max_body_bytes=2000,
        max_artifact_bytes=10_000,
    )
    app = create_app(settings)
    client = TestClient(app)
    try:
        reg = client.post(
            "/api/v1/auth/register",
            json={
                "email": "art@example.com",
                "password": "art-password-123",
                "name": "Art User",
                "project_name": "Art Project",
            },
        )
        assert reg.status_code == 201
        hdrs = {
            "Authorization": f"Bearer {reg.json()['token']}",
            "x-matrix-project-id": reg.json()["project"]["id"],
        }

        # Payload > max_json_body_bytes (1000) and > max_body_bytes (2000), but <= max_artifact_bytes (10_000)
        payload = b"A" * 5000
        sha = hashlib.sha256(payload).hexdigest()
        presign = client.post(
            "/api/v1/mlops/artifacts/presign-upload",
            headers=hdrs,
            json={"name": "big-weights", "sha256": sha, "size_bytes": len(payload)},
        )
        assert presign.status_code == 201
        upload_url = presign.json()["upload_url"]

        # Wrong content -> 422 sha256 mismatch
        bad_put = client.put(upload_url, content=b"B" * 5000)
        assert bad_put.status_code == 422
        assert "sha256" in bad_put.json()["detail"].lower()

        # Correct content -> 200
        ok_put = client.put(upload_url, content=payload)
        assert ok_put.status_code == 200, ok_put.text
        assert ok_put.json()["size_bytes"] == len(payload)

        # Oversized payload (> max_artifact_bytes=10_000) -> 413
        big_put = client.put(upload_url, content=b"C" * 10_001)
        assert big_put.status_code == 413
    finally:
        app.state.db_engine.dispose()

