"""End-to-end API, RBAC, multi-tenant isolation, and security tests for MLOps Studio (canonical routes)."""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import api_key_headers, project_with_roles

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.storage.models import Experiment, SafetyScan


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/mlops_api.db",
        span_store="memory",
        artifact_backend="local",
        artifact_local_dir=str(tmp_path / "artifacts"),
    )
    app = create_app(settings)
    client = TestClient(app)
    factory = app.state.session_factory
    project_a, roles_a = project_with_roles(factory, "Project A")
    project_b, roles_b = project_with_roles(factory, "Project B")

    return {
        "app": app,
        "client": client,
        "project_a": project_a,
        "project_b": project_b,
        "admin_a": roles_a["admin"],
        "member_a": roles_a["member"],
        "viewer_a": roles_a["viewer"],
        "admin_b": roles_b["admin"],
        "key_a": api_key_headers(factory, project_a),
    }


def test_end_to_end_mlops_flow_and_deduplication(env):
    client: TestClient = env["client"]
    app = env["app"]
    hdrs = env["member_a"]
    admin_hdrs = env["admin_a"]
    pid_a = env["project_a"]

    ds_resp = client.post("/api/v1/datasets", headers=hdrs, json={"name": "sft-golden-ds", "description": "SFT dataset"})
    assert ds_resp.status_code == 201, ds_resp.text
    dataset_id = ds_resp.json()["id"]
    c_resp = client.post(
        f"/api/v1/datasets/{dataset_id}/commits",
        headers=hdrs,
        json={"branch": "main", "message": "init", "changes": {"upsert": [{"item_key": "k1", "input": {"q": "hi"}}]}},
    )
    assert c_resp.status_code == 201, c_resp.text
    commit_id = c_resp.json()["id"]

    with app.state.session_factory() as db:
        exp = Experiment(
            project_id=pid_a,
            name="lora-eval-exp",
            dataset_id=dataset_id,
            variants=[{"name": "v1"}],
            metrics=[{"name": "exact_match"}],
            status="done",
        )
        scan = SafetyScan(project_id=pid_a, target_model="llama3-lora", status="passed", global_score=96.0)
        db.add_all([exp, scan])
        db.commit()
        exp_id = exp.id
        scan_id = scan.id

    # 1. POST /api/v1/runs
    run_resp = client.post(
        "/api/v1/runs",
        headers=hdrs,
        json={
            "name": "llama3-lora-sft-1",
            "group": "lora",
            "dataset_version_id": commit_id,
            "params": {"lr": 2e-4, "lora_r": 16, "epochs": 3},
            "tags": ["sft", "lora"],
        },
    )
    assert run_resp.status_code == 201, run_resp.text
    run = run_resp.json()
    run_id = run["id"]
    assert run["dataset_version_id"] == commit_id
    assert run["params"]["lora_r"] == 16

    # 2. POST /api/v1/runs/{id}/metrics (60 points to test M4 downsampling)
    points = [{"key": "loss", "step": i, "value": 2.0 / (i + 1)} for i in range(1, 61)]
    m_resp = client.post(
        f"/api/v1/runs/{run_id}/metrics",
        headers=hdrs,
        json={"points": points},
    )
    assert m_resp.status_code == 202, m_resp.text
    assert m_resp.json()["accepted"] == 60

    # 3. GET /api/v1/runs/{id}/metrics with downsampling
    q_resp = client.get(
        f"/api/v1/runs/{run_id}/metrics?keys=loss&max_points=10",
        headers=env["viewer_a"],
    )
    assert q_resp.status_code == 200
    series = q_resp.json()["series"]["loss"]
    assert 1 <= len(series["steps"]) <= 10
    assert len(series["steps"]) == len(series["values"])

    # 4. Report + PATCH /api/v1/runs/{id}
    rep_resp = client.post(
        f"/api/v1/runs/{run_id}/reports",
        headers=hdrs,
        json={"kind": "custom", "name": "classic_eval", "data": {"roc_auc": 0.94, "f1": 0.89}},
    )
    assert rep_resp.status_code == 201, rep_resp.text
    patch_resp = client.patch(f"/api/v1/runs/{run_id}", headers=hdrs, json={"status": "finished"})
    assert patch_resp.status_code == 200 and patch_resp.json()["status"] == "finished"

    # 5. Canonical G0 artifact upload + link to run + deduplication
    blob = b"safetensors-binary-weights-payload-v1"
    sha = hashlib.sha256(blob).hexdigest()

    up1 = client.post(
        "/api/v1/artifacts",
        content=blob,
        headers={
            **hdrs,
            "x-filename": "adapter-weights.safetensors",
            "content-type": "application/octet-stream",
            "x-sha256": sha,
            "x-owner-kind": "run",
            "x-owner-id": run_id,
        },
    )
    assert up1.status_code == 201, up1.text
    art1 = up1.json()
    dl_url = f"/api/v1/artifacts/{art1['id']}/download"
    assert client.get(dl_url).status_code == 401
    assert client.get(dl_url, headers=env["admin_b"]).status_code == 404
    get_blob = client.get(dl_url, headers=env["viewer_a"])
    assert get_blob.status_code == 200 and get_blob.content == blob

    link1 = client.post(
        f"/api/v1/runs/{run_id}/artifacts",
        headers=hdrs,
        json={"artifact_id": art1["id"], "name": "adapter-weights", "kind": "model"},
    )
    assert link1.status_code == 201, link1.text

    up2 = client.post(
        "/api/v1/artifacts",
        content=blob,
        headers={
            **hdrs,
            "x-filename": "adapter-weights-copy.safetensors",
            "content-type": "application/octet-stream",
            "x-sha256": sha,
            "x-owner-kind": "run",
            "x-owner-id": run_id,
        },
    )
    assert up2.status_code == 201
    art2 = up2.json()
    assert art2["id"] != art1["id"] and art2["sha256"] == sha
    assert client.post(
        f"/api/v1/runs/{run_id}/artifacts",
        headers=hdrs,
        json={"artifact_id": art2["id"], "name": "adapter-weights-copy", "kind": "model"},
    ).status_code == 201

    run_arts = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=env["viewer_a"])
    assert run_arts.status_code == 200
    assert len(run_arts.json()["items"]) == 2

    # 6. Model & LoRA Registry (passing bridged G0 BlobArtifact ID)
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

    files_resp = client.get(f"/api/v1/models/{model_id}/versions/1/files", headers=env["viewer_a"])
    assert files_resp.status_code == 200
    assert files_resp.json()["files"][0]["artifact_id"] == art1["id"]

    alias_resp = client.put(
        f"/api/v1/models/{model_id}/aliases/champion",
        headers=admin_hdrs,
        json={"version": 1},
    )
    assert alias_resp.status_code == 200
    assert alias_resp.json()["alias"] == "champion"

    # 7. Bounded Lineage DAGs
    model_lineage = client.get(
        f"/api/v1/lineage?node=model_version:{mv['id']}&direction=both&depth=3",
        headers=env["viewer_a"],
    )
    assert model_lineage.status_code == 200
    ml_data = model_lineage.json()
    node_types = {n["node_type"] for n in ml_data["nodes"]}
    assert {"dataset_commit", "run", "artifact", "model_version", "experiment", "safety_scan"}.issubset(node_types)
    relations = {e["relation"] for e in ml_data["edges"]}
    assert {"trained_on", "produced", "packaged_as", "registered_as", "evaluated_by", "audited_by"}.issubset(relations)

    run_lineage = client.get(f"/api/v1/mlops/runs/{run_id}/lineage", headers=env["viewer_a"])
    assert run_lineage.status_code == 200
    assert len(run_lineage.json()["nodes"]) >= 4

    # 8. Evaluate model version
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

    sweep_cfg = {
        "method": "random",
        "metric": {"key": "eval_loss", "goal": "minimize"},
        "parameters": {
            "lr": {"type": "float", "min": 1e-5, "max": 1e-2, "log": True},
            "batch_size": {"type": "choice", "values": [8, 16, 32]},
        },
        "early_stop": {"type": "asha", "min_iter": 5, "eta": 3},
        "max_runs": 5,
    }
    sweep_resp = client.post("/api/v1/sweeps", headers=hdrs, json={"name": "lr-asha-sweep", "config": sweep_cfg})
    assert sweep_resp.status_code == 201, sweep_resp.text
    sweep_id = sweep_resp.json()["id"]

    # Trial 1: good loss (0.2 at step 5)
    t1 = client.post(f"/api/v1/sweeps/{sweep_id}/next", headers=hdrs, json={})
    assert t1.status_code == 200
    r1_id = t1.json()["run"]["id"]
    rep1 = client.post(f"/api/v1/sweeps/{sweep_id}/runs/{r1_id}/report", headers=hdrs, json={"step": 5, "value": 0.2})
    assert rep1.status_code == 200 and rep1.json()["action"] == "continue"

    # Trial 2: good loss (0.3 at step 5)
    t2 = client.post(f"/api/v1/sweeps/{sweep_id}/next", headers=hdrs, json={})
    assert t2.status_code == 200
    r2_id = t2.json()["run"]["id"]
    client.post(f"/api/v1/sweeps/{sweep_id}/runs/{r2_id}/report", headers=hdrs, json={"step": 5, "value": 0.3})

    # Trial 3: poor loss (0.95 at step 5) -> stop
    t3 = client.post(f"/api/v1/sweeps/{sweep_id}/next", headers=hdrs, json={})
    assert t3.status_code == 200
    r3_id = t3.json()["run"]["id"]
    rep3 = client.post(f"/api/v1/sweeps/{sweep_id}/runs/{r3_id}/report", headers=hdrs, json={"step": 5, "value": 0.95})
    assert rep3.status_code == 200 and rep3.json()["action"] == "stop"

    detail = client.get(f"/api/v1/sweeps/{sweep_id}", headers=env["viewer_a"])
    assert detail.status_code == 200
    d_json = detail.json()
    assert d_json["sweep"]["best_run_id"] == r1_id
    assert len(d_json["runs"]) == 3
    assert "importance" in d_json and any(r["pruned"] for r in d_json["runs"])

    stop_resp = client.patch(f"/api/v1/sweeps/{sweep_id}", headers=hdrs, json={"status": "stopped"})
    assert stop_resp.status_code == 200 and stop_resp.json()["status"] == "stopped"
    assert client.post(f"/api/v1/sweeps/{sweep_id}/next", headers=hdrs, json={}).status_code == 204


def test_rbac_multi_tenant_isolation_and_security(env):
    client: TestClient = env["client"]
    viewer_a = env["viewer_a"]
    member_a = env["member_a"]
    admin_a = env["admin_a"]
    admin_b = env["admin_b"]

    # 1. Viewer cannot create run or model (403)
    assert client.post("/api/v1/runs", headers=viewer_a, json={"name": "v-run"}).status_code == 403
    assert client.post("/api/v1/mlops/models", headers=viewer_a, json={"name": "v-model"}).status_code == 403

    # 2. Member creates model & version, can set staging/@challenger, but 403 for production/@champion/DELETE
    m_resp = client.post("/api/v1/mlops/models", headers=member_a, json={"name": "rbac-model"})
    assert m_resp.status_code == 201
    model_id = m_resp.json()["id"]

    mv_resp = client.post(f"/api/v1/mlops/models/{model_id}/versions", headers=member_a, json={})
    assert mv_resp.status_code == 201

    assert (
        client.post(
            f"/api/v1/models/{model_id}/versions/1/stage",
            headers=member_a,
            json={"stage": "staging"},
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/v1/models/{model_id}/aliases/challenger",
            headers=member_a,
            json={"version": 1},
        ).status_code
        == 200
    )

    assert (
        client.post(
            f"/api/v1/models/{model_id}/versions/1/stage",
            headers=member_a,
            json={"stage": "production"},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/models/{model_id}/aliases/champion",
            headers=member_a,
            json={"version": 1},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/models/{model_id}/aliases/production",
            headers=member_a,
            json={"version": 1},
        ).status_code
        == 403
    )
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

    assert (
        client.post(
            f"/api/v1/models/{model_id}/versions/1/stage",
            headers=admin_a,
            json={"stage": "production"},
        ).status_code
        == 200
    )
    for target_stage in ("staging", "archived", "none"):
        assert (
            client.post(
                f"/api/v1/models/{model_id}/versions/1/stage",
                headers=member_a,
                json={"stage": target_stage},
            ).status_code
            == 403
        )
    assert (
        client.put(
            f"/api/v1/models/{model_id}/aliases/champion",
            headers=admin_a,
            json={"version": 1},
        ).status_code
        == 200
    )

    assert client.delete(f"/api/v1/mlops/models/{model_id}", headers=admin_a).status_code == 409

    # 3. Multi-tenant isolation
    run_a = client.post("/api/v1/runs", headers=member_a, json={"name": "tenant-a-run"}).json()
    run_a_id = run_a["id"]
    sweep_a = client.post(
        "/api/v1/sweeps",
        headers=member_a,
        json={
            "name": "tenant-a-sweep",
            "config": {
                "method": "random",
                "metric": {"key": "loss", "goal": "minimize"},
                "parameters": {"lr": {"type": "float", "min": 0.001, "max": 0.01}},
            },
        },
    ).json()
    art_a = client.post(
        "/api/v1/artifacts",
        content=b"0123456789",
        headers={**member_a, "x-filename": "tenant-a-art.bin", "content-type": "application/octet-stream"},
    ).json()

    assert client.get(f"/api/v1/runs/{run_a_id}", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/runs/{run_a_id}/metrics", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/lineage?node=run:{run_a_id}", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/sweeps/{sweep_a['id']}", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/artifacts/{art_a['id']}/download", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/mlops/models/{model_id}", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/mlops/models/{model_id}/lineage", headers=admin_b).status_code == 404

    ds_a = client.post("/api/v1/datasets", headers=member_a, json={"name": "secret-ds-a"}).json()
    dc_a = client.post(
        f"/api/v1/datasets/{ds_a['id']}/commits",
        headers=member_a,
        json={"branch": "main", "message": "a", "changes": {"upsert": [{"item_key": "k1", "input": {"q": "a"}}]}},
    ).json()
    dc_a_id = dc_a["id"]

    assert (
        client.post("/api/v1/runs", headers=admin_b, json={"name": "idor-run", "dataset_version_id": dc_a_id}).status_code
        == 422
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
    nan_run_body = json.dumps({"name": "nan-run", "params": {"lr": float("nan")}})
    assert (
        client.post(
            "/api/v1/runs",
            headers={**member_a, "Content-Type": "application/json"},
            content=nan_run_body,
        ).status_code
        == 422
    )
    nan_mv_body = json.dumps({"metrics_snapshot": {"roc_auc": float("nan")}})
    assert (
        client.post(
            f"/api/v1/mlops/models/{model_id}/versions",
            headers={**member_a, "Content-Type": "application/json"},
            content=nan_mv_body,
        ).status_code
        == 422
    )

    # 5. Legacy HMAC-token routes remain absent (404/405)
    bad = "/api/v1/mlops/artifacts/local/invalid.token"
    assert client.put(bad, content=b"x", headers=member_a).status_code in (404, 405)
    assert client.get(bad, headers=member_a).status_code in (404, 405)

    # 6. Admin force delete works
    assert client.delete(f"/api/v1/mlops/models/{model_id}?force=true", headers=admin_a).status_code == 200


def test_bayes_sweep_api_passes_metric_name_with_heartbeat(env, monkeypatch: pytest.MonkeyPatch) -> None:
    from matrix_server.sweeps import engine as engine_mod

    client: TestClient = env["client"]
    hdrs = env["member_a"]

    sweep_resp = client.post(
        "/api/v1/sweeps",
        headers=hdrs,
        json={
            "name": "bayes-api-sweep",
            "config": {
                "method": "bayes",
                "metric": {"key": "val_loss", "goal": "minimize"},
                "parameters": {
                    "x": {"type": "float", "min": 0.0, "max": 1.0},
                    "y": {"type": "float", "min": 0.0, "max": 1.0},
                },
            },
        },
    )
    assert sweep_resp.status_code == 201, sweep_resp.text
    sweep_id = sweep_resp.json()["id"]

    for idx in range(6):
        t_resp = client.post(f"/api/v1/sweeps/{sweep_id}/next", headers=hdrs, json={})
        assert t_resp.status_code == 200
        run_id = t_resp.json()["run"]["id"]
        hp = t_resp.json()["params"]
        val = (hp["x"] - 0.2) ** 2 + (hp["y"] - 0.8) ** 2
        rep_resp = client.post(
            f"/api/v1/sweeps/{sweep_id}/runs/{run_id}/report",
            headers=hdrs,
            json={"step": idx + 1, "value": val},
        )
        assert rep_resp.status_code == 200

    seen_scored_counts: list[int] = []
    real_suggest_tpe = engine_mod._suggest_tpe

    def spy_suggest_tpe(parameters, scored_trials, metric_goal, rng, **kwargs):
        seen_scored_counts.append(len(scored_trials))
        return real_suggest_tpe(parameters, scored_trials, metric_goal, rng, **kwargs)

    monkeypatch.setattr(engine_mod, "_suggest_tpe", spy_suggest_tpe)
    t7 = client.post(f"/api/v1/sweeps/{sweep_id}/next", headers=hdrs, json={})
    assert t7.status_code == 200
    assert seen_scored_counts == [6]


def test_local_artifact_upload_bypasses_json_limit_and_validates_sha256(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/art_limits.db",
        span_store="memory",
        artifact_backend="local",
        artifact_local_dir=str(tmp_path / "artifacts"),
        max_json_body_bytes=1000,
        max_body_bytes=2000,
        max_artifact_bytes=10_000,
    )
    app = create_app(settings)
    client = TestClient(app)
    try:
        _, roles = project_with_roles(app.state.session_factory, "Art Project")
        hdrs = roles["member"]

        payload = b"A" * 5000
        sha = hashlib.sha256(payload).hexdigest()

        bad_post = client.post(
            "/api/v1/artifacts",
            content=b"B" * 5000,
            headers={**hdrs, "x-filename": "big-weights", "content-type": "application/octet-stream", "x-sha256": sha},
        )
        assert bad_post.status_code == 422

        big_post = client.post(
            "/api/v1/artifacts",
            content=b"C" * 10_001,
            headers={**hdrs, "x-filename": "too-big", "content-type": "application/octet-stream"},
        )
        assert big_post.status_code == 413

        ok_post = client.post(
            "/api/v1/artifacts",
            content=payload,
            headers={**hdrs, "x-filename": "big-weights", "content-type": "application/octet-stream", "x-sha256": sha},
        )
        assert ok_post.status_code == 201, ok_post.text
        art_id = ok_post.json()["id"]
        assert client.get(f"/api/v1/artifacts/{art_id}/download", headers=hdrs).content == payload
    finally:
        app.state.db_engine.dispose()
