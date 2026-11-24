"""Tests for MATRIX Benchmarks API, BenchmarkEngine, egress policy, secret hygiene, and lineage (T7)."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import api_key_headers, project_with_roles
from sqlalchemy import select

import matrix_server.benchmarks.runner as bench_runner_mod
from matrix_server import egress
from matrix_server.app import create_app
from matrix_server.benchmarks.runner import (
    BenchmarkCredentialError,
    EngineUnavailable,
    HttpBenchmarkEngine,
    get_benchmark_engine,
    run_benchmark_job,
    validate_runner_config,
)
from matrix_server.config import Settings
from matrix_server.egress import EgressError, EgressHost
from matrix_server.storage.models import (
    ArtifactLineage,
    AuditLog,
    BenchmarkRun,
    Dataset,
    ModelVersion,
    RegisteredModel,
)


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/benchmarks_test.db",
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
        "viewer_token_a": roles_a["viewer"],
        "member_token_a": roles_a["member"],
        "admin_token_a": roles_a["admin"],
        "admin_token_b": roles_b["admin"],
        "key_a": api_key_headers(factory, project_a),
    }


def auth(token_or_hdrs: Any) -> dict[str, str]:
    if isinstance(token_or_hdrs, dict):
        return token_or_hdrs
    return {"Authorization": f"Bearer {token_or_hdrs}"}


# --- 1. Catalog Endpoint Tests ----------------------------------------------


def test_catalog_endpoint_all_and_filtered(env):
    client: TestClient = env["client"]
    viewer = env["viewer_token_a"]

    resp = client.get("/api/v1/benchmarks/catalog")
    assert resp.status_code == 401

    resp = client.get("/api/v1/benchmarks/catalog", headers=auth(viewer))
    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    task_map = {t["name"]: t for t in tasks}

    for name in ["mmlu_pro", "gpqa_diamond", "ifeval", "bbh", "humaneval", "gsm8k"]:
        assert name in task_map
        assert task_map[name]["suite"] == "lm_eval"
        assert task_map[name]["modality"] == "text"

    for name in ["mmmu_pro", "mathvista", "docvqa", "chartqa", "ocrbench", "pope", "vidore"]:
        assert name in task_map
        assert task_map[name]["suite"] == "vlmevalkit"
        assert task_map[name]["modality"] == "multimodal"

    resp_lm = client.get("/api/v1/benchmarks/catalog?suite=lm_eval", headers=auth(viewer))
    assert resp_lm.status_code == 200
    assert len(resp_lm.json()["tasks"]) == 6

    resp_vlm = client.get("/api/v1/benchmarks/catalog?suite=vlmevalkit", headers=auth(viewer))
    assert resp_vlm.status_code == 200
    assert len(resp_vlm.json()["tasks"]) == 7

    resp_bad = client.get("/api/v1/benchmarks/catalog?suite=unknown_suite", headers=auth(viewer))
    assert resp_bad.status_code == 422


# --- 2. Benchmark Run Creation (local_sim -> simulated=True) ----------------


def test_benchmark_run_lm_eval_sync_local_sim_marks_simulated(env):
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
    assert data["simulated"] is True
    assert data["suite"] == "lm_eval"
    assert len(data["task_results"]) == 2
    assert all(r["simulated"] is True for r in data["task_results"])

    task_res = {r["task_name"]: r for r in data["task_results"]}
    assert task_res["mmlu_pro"]["primary_metric"] == "acc"
    assert 0.0 <= task_res["mmlu_pro"]["primary_score"] <= 1.0
    assert task_res["gsm8k"]["primary_metric"] == "exact_match"

    summary = data["summary_metrics"]
    assert summary["total_tasks"] == 2
    assert summary["total_samples"] == 100 + 1319

    run_id = data["id"]
    detail = client.get(f"/api/v1/benchmarks/runs/{run_id}", headers=auth(viewer)).json()
    assert detail["simulated"] is True
    assert all(r["simulated"] is True for r in detail["task_results"])

    runs = client.get("/api/v1/benchmarks/runs?suite=lm_eval", headers=auth(viewer)).json()["runs"]
    assert any(r["id"] == run_id and r["simulated"] is True for r in runs)


def test_benchmark_run_vlmevalkit_sync_with_lineage(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]
    app = env["app"]
    project_a = env["project_a"]

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
    assert data["simulated"] is True

    run_id = data["id"]
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
        assert edge_map["model_version"].source_id == mv_id
        assert edge_map["dataset"].source_id == ds_id


# --- 3. Secret Hygiene (api_key rejected -> 422, api_key_env validated) -----


def test_literal_api_key_rejected_and_api_key_env_validated(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]

    # Literal api_key -> 422 telling client to use api_key_env
    resp = client.post(
        "/api/v1/benchmarks/runs",
        headers=auth(member),
        json={
            "name": "leak-key-run",
            "suite": "lm_eval",
            "tasks": ["mmlu_pro"],
            "runner_backend": "http",
            "runner_config": {
                "endpoint_url": "https://bench.example.com/v1",
                "api_key": "sk-plaintext-never-store",
            },
            "target_model": "gpt-4o",
        },
    )
    assert resp.status_code == 422
    assert "api_key_env" in resp.text
    assert "sk-plaintext-never-store" not in resp.text

    # Invalid api_key_env formats -> 422
    for bad_env in ("lowercase_var", "1STARTS_WITH_DIGIT", "BAD-DASH", "HAS SPACE", "", "A" * 65):
        r_bad = client.post(
            "/api/v1/benchmarks/runs",
            headers=auth(member),
            json={
                "name": "bad-env-run",
                "suite": "lm_eval",
                "tasks": ["mmlu_pro"],
                "runner_backend": "http",
                "runner_config": {
                    "endpoint_url": "https://bench.example.com/v1",
                    "api_key_env": bad_env,
                },
                "target_model": "gpt-4o",
            },
        )
        assert r_bad.status_code == 422, f"Expected 422 for api_key_env={bad_env!r}"


# --- 4. SSRF & EgressClient Integration ------------------------------------


def test_custom_validate_runner_url_removed():
    assert not hasattr(bench_runner_mod, "validate_runner_url")


@pytest.mark.parametrize(
    "disallowed_url",
    [
        "http://127.0.0.1:8000/v1",
        "http://127.0.0.1/v1",
        "http://10.0.0.1:8000/v1",
        "http://192.168.1.1:8000/v1",
        "http://172.16.0.1:8000/v1",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/v1",
        "http://user:pass@api.together.xyz/v1",
        "ftp://api.together.xyz/v1",
        "gopher://api.together.xyz/v1",
    ],
)
def test_anti_ssrf_blocks_private_and_invalid_urls(disallowed_url: str):
    with pytest.raises(ValueError):
        validate_runner_config({"endpoint_url": disallowed_url}, backend="http")


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
    assert "not allowed" in resp.text


def test_http_runner_host_outside_allowlist_fails_job_with_audit(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]
    app = env["app"]
    project_a = env["project_a"]

    resp = client.post(
        "/api/v1/benchmarks/runs",
        headers=auth(member),
        json={
            "name": "unlisted-host-run",
            "suite": "lm_eval",
            "tasks": ["gsm8k"],
            "runner_backend": "http",
            "runner_config": {"endpoint_url": "http://unlisted.example.com/v1"},
            "target_model": "gpt-4o",
            "sync_exec": False,
        },
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]

    with pytest.raises(EgressError) as exc_info:
        run_benchmark_job(app.state.session_factory, run_id)
    assert exc_info.value.reason == "host_not_allowed"

    detail = client.get(f"/api/v1/benchmarks/runs/{run_id}", headers=auth(env["viewer_token_a"])).json()
    assert detail["status"] == "failed"
    assert "allowlist" in (detail["error"] or "")

    with app.state.session_factory() as db:
        audits = db.scalars(
            select(AuditLog).where(
                AuditLog.project_id == project_a,
                AuditLog.action == "egress.request",
            )
        ).all()
        assert any(a.target == "unlisted.example.com" and a.detail.get("reason") == "host_not_allowed" for a in audits)


def test_http_runner_via_egress_client_against_fake_openai_server(env, monkeypatch, caplog):
    """End-to-end HTTP benchmark via egress_client with resolver/dialer seams and sentinel secret."""
    client: TestClient = env["client"]
    member = env["member_token_a"]
    viewer = env["viewer_token_a"]
    app = env["app"]
    project_a = env["project_a"]

    sentinel = "SENTINEL_SECRET_KEY_987654321"
    monkeypatch.setenv("MATRIX_BENCH_API_KEY_TEST", sentinel)

    received_auth: list[str] = []
    received_paths: list[str] = []

    class FakeOpenAIHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length)
            received_auth.append(self.headers.get("Authorization", ""))
            received_paths.append(self.path)
            body = json.dumps(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "42"}}],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass  # silence stderr in tests

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Allowlist `bench.example.test` for Project A and route simulated public IP -> local fake server
    with app.state.session_factory() as db, db.begin():
        db.add(EgressHost(project_id=project_a, host="bench.example.test"))

    egress.set_resolver_for_tests(lambda host, p: ["93.184.216.34"] if host == "bench.example.test" else [])
    egress.set_dialer_for_tests(lambda ip, p: ("127.0.0.1", port) if ip == "93.184.216.34" else (ip, p))

    try:
        with caplog.at_level(logging.DEBUG):
            resp = client.post(
                "/api/v1/benchmarks/runs",
                headers=auth(member),
                json={
                    "name": "real-http-bench",
                    "suite": "lm_eval",
                    "tasks": ["gsm8k"],
                    "runner_backend": "http",
                    "runner_config": {
                        "endpoint_url": "http://bench.example.test/v1",
                        "api_key_env": "MATRIX_BENCH_API_KEY_TEST",
                    },
                    "eval_mode": "generate_until",
                    "target_model": "fake-openai-model",
                    "sync_exec": True,
                },
            )

        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "completed"
        assert data["simulated"] is True
        assert len(data["task_results"]) == 1
        assert data["task_results"][0]["simulated"] is True
        assert received_paths == ["/v1/chat/completions"]
        assert received_auth == [f"Bearer {sentinel}"]

        # Sentinel secret must NEVER appear in API response, DB, or logs
        assert sentinel not in resp.text
        detail_resp = client.get(f"/api/v1/benchmarks/runs/{data['id']}", headers=auth(viewer))
        assert sentinel not in detail_resp.text
        assert sentinel not in caplog.text

        with app.state.session_factory() as db:
            db_run = db.get(BenchmarkRun, data["id"])
            assert db_run is not None
            assert sentinel not in json.dumps(db_run.runner_config)
            assert db_run.simulated is True

            audits = db.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == project_a,
                    AuditLog.action == "egress.request",
                )
            ).all()
            assert any(
                a.target == "bench.example.test"
                and a.detail.get("purpose") == "benchmark.run"
                and a.detail.get("status") == 200
                for a in audits
            )
    finally:
        egress.set_resolver_for_tests(None)
        egress.set_dialer_for_tests(None)
        server.shutdown()
        server.server_close()


# --- 5. BenchmarkEngine Stubs (lm_eval / vlmeval -> EngineUnavailable) ------


@pytest.mark.parametrize("backend", ["lm_eval", "vlmeval"])
def test_real_engine_stubs_raise_engine_unavailable(env, backend: str):
    engine = get_benchmark_engine(backend)
    assert engine.simulated is False
    with pytest.raises(EngineUnavailable, match="install the real engine"):
        engine.run(None, [], session_factory=env["app"].state.session_factory)  # type: ignore[arg-type]


# --- 6. Anti-IDOR and Multi-Tenant Isolation --------------------------------


def test_anti_idor_and_isolation(env):
    client: TestClient = env["client"]
    admin_a = env["admin_token_a"]
    admin_b = env["admin_token_b"]
    app = env["app"]
    project_b = env["project_b"]

    factory = app.state.session_factory
    with factory() as db, db.begin():
        ds_b = Dataset(project_id=project_b, name="secret-b-dataset")
        db.add(ds_b)
        db.flush()
        ds_b_id = ds_b.id

    bad_payload = {
        "name": "cross-tenant-run",
        "suite": "lm_eval",
        "tasks": ["mmlu_pro"],
        "target_model": "llama-3",
        "dataset_id": ds_b_id,
    }
    assert client.post("/api/v1/benchmarks/runs", headers=auth(admin_a), json=bad_payload).status_code == 404

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

    assert client.get(f"/api/v1/benchmarks/runs/{run_a_id}", headers=auth(admin_b)).status_code == 404
    list_b = client.get("/api/v1/benchmarks/runs", headers=auth(admin_b)).json()["runs"]
    assert not any(r["id"] == run_a_id for r in list_b)


# --- 7. Anti-NaN Validation -------------------------------------------------


def test_anti_nan_rejection(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]

    bad_nan = b'{"name": "nan-run", "suite": "lm_eval", "tasks": ["mmlu_pro"], "target_model": "test-model", "runner_config": {"temperature": NaN}}'
    resp_nan = client.post(
        "/api/v1/benchmarks/runs",
        headers={**auth(member), "content-type": "application/json"},
        content=bad_nan,
    )
    assert resp_nan.status_code == 422
    assert "NaN or Infinity" in resp_nan.text

    bad_inf = b'{"name": "inf-run", "suite": "lm_eval", "tasks": ["mmlu_pro"], "target_model": "test-model", "runner_config": {"temperature": Infinity}}'
    resp_inf = client.post(
        "/api/v1/benchmarks/runs",
        headers={**auth(member), "content-type": "application/json"},
        content=bad_inf,
    )
    assert resp_inf.status_code == 422
    assert "NaN or Infinity" in resp_inf.text


# --- 8. RBAC and Cancellation -----------------------------------------------


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

    assert client.post("/api/v1/benchmarks/runs", headers=auth(viewer), json=payload).status_code == 403

    resp_member = client.post("/api/v1/benchmarks/runs", headers=auth(member), json=payload)
    assert resp_member.status_code == 201
    run_data = resp_member.json()
    run_id = run_data["id"]
    assert run_data["status"] == "queued"
    assert run_data["job_id"] is not None

    assert client.post(f"/api/v1/benchmarks/runs/{run_id}/cancel", headers=auth(viewer)).status_code == 403

    resp_cancel = client.post(f"/api/v1/benchmarks/runs/{run_id}/cancel", headers=auth(member))
    assert resp_cancel.status_code == 200
    assert resp_cancel.json()["status"] == "cancelled"
    assert resp_cancel.json()["finished_at"] is not None

    resp_recancel = client.post(f"/api/v1/benchmarks/runs/{run_id}/cancel", headers=auth(member))
    assert resp_recancel.status_code == 200
    assert resp_recancel.json()["status"] == "cancelled"


# --- 9. Worker Execution Logic ----------------------------------------------


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

    run_benchmark_job(app.state.session_factory, run_id)

    detail = client.get(f"/api/v1/benchmarks/runs/{run_id}", headers=auth(viewer)).json()
    assert detail["status"] == "completed"
    assert detail["simulated"] is True
    assert len(detail["task_results"]) == 2
    assert all(r["simulated"] is True for r in detail["task_results"])


# --- 10. T12 Security & Resilience Hardening Tests --------------------------


def test_api_key_env_restricted_to_prefix_and_allowlist(env, monkeypatch):
    client: TestClient = env["client"]
    member = env["member_token_a"]

    monkeypatch.delenv("MATRIX_BENCHMARK_ALLOWED_ENV_VARS", raising=False)
    for forbidden_env in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MATRIX_SECRET_KEY",
        "MATRIX_ADMIN_TOKEN",
        "DATABASE_URL",
        "MATRIX_TEST_BENCH_KEY",
    ):
        resp = client.post(
            "/api/v1/benchmarks/runs",
            headers=auth(member),
            json={
                "name": "exfil-attempt",
                "suite": "lm_eval",
                "tasks": ["mmlu_pro"],
                "runner_backend": "http",
                "runner_config": {
                    "endpoint_url": "https://bench.example.com/v1",
                    "api_key_env": forbidden_env,
                },
                "target_model": "gpt-4o",
            },
        )
        assert resp.status_code == 422, f"Expected 422 for {forbidden_env}, got {resp.status_code}"

    # Default prefix MATRIX_BENCH_API_KEY_* is allowed
    ok_prefixed = client.post(
        "/api/v1/benchmarks/runs",
        headers=auth(member),
        json={
            "name": "prefixed-key-run",
            "suite": "lm_eval",
            "tasks": ["mmlu_pro"],
            "runner_backend": "http",
            "runner_config": {
                "endpoint_url": "https://bench.example.com/v1",
                "api_key_env": "MATRIX_BENCH_API_KEY_OPENAI",
            },
            "target_model": "gpt-4o",
            "sync_exec": False,
        },
    )
    assert ok_prefixed.status_code == 201

    # Explicit operator allowlist expands allowed env vars without allowing arbitrary secrets
    monkeypatch.setenv("MATRIX_BENCHMARK_ALLOWED_ENV_VARS", "CUSTOM_BENCH_KEY,SECOND_BENCH_KEY")
    ok_listed = client.post(
        "/api/v1/benchmarks/runs",
        headers=auth(member),
        json={
            "name": "allowlisted-key-run",
            "suite": "lm_eval",
            "tasks": ["mmlu_pro"],
            "runner_backend": "http",
            "runner_config": {
                "endpoint_url": "https://bench.example.com/v1",
                "api_key_env": "CUSTOM_BENCH_KEY",
            },
            "target_model": "gpt-4o",
            "sync_exec": False,
        },
    )
    assert ok_listed.status_code == 201

    still_blocked = client.post(
        "/api/v1/benchmarks/runs",
        headers=auth(member),
        json={
            "name": "still-blocked",
            "suite": "lm_eval",
            "tasks": ["mmlu_pro"],
            "runner_backend": "http",
            "runner_config": {
                "endpoint_url": "https://bench.example.com/v1",
                "api_key_env": "DATABASE_URL",
            },
            "target_model": "gpt-4o",
        },
    )
    assert still_blocked.status_code == 422


def test_egress_checked_before_env_lookup_and_no_credential_oracle(env, monkeypatch):
    client: TestClient = env["client"]
    member = env["member_token_a"]
    viewer = env["viewer_token_a"]
    app = env["app"]
    project_a = env["project_a"]

    monkeypatch.delenv("MATRIX_BENCH_API_KEY_MISSING", raising=False)

    # 1. Unlisted host + missing env var: must fail on egress allowlist BEFORE reading os.environ
    r1 = client.post(
        "/api/v1/benchmarks/runs",
        headers=auth(member),
        json={
            "name": "unlisted-before-env",
            "suite": "lm_eval",
            "tasks": ["gsm8k"],
            "runner_backend": "http",
            "runner_config": {
                "endpoint_url": "http://unlisted-oracle.example.com/v1",
                "api_key_env": "MATRIX_BENCH_API_KEY_MISSING",
            },
            "target_model": "gpt-4o",
            "sync_exec": False,
        },
    )
    assert r1.status_code == 201
    run1_id = r1.json()["id"]
    with pytest.raises(EgressError) as exc1:
        run_benchmark_job(app.state.session_factory, run1_id)
    assert exc1.value.reason == "host_not_allowed"
    assert getattr(exc1.value, "non_retryable", False) is True

    # 2. Allowed host + missing env var: must fail with generic error, never echoing env var name
    with app.state.session_factory() as db, db.begin():
        db.add(EgressHost(project_id=project_a, host="allowed-oracle.example.test"))
    egress.set_resolver_for_tests(lambda host, p: ["93.184.216.34"] if host == "allowed-oracle.example.test" else [])
    try:
        r2 = client.post(
            "/api/v1/benchmarks/runs",
            headers=auth(member),
            json={
                "name": "missing-cred-generic",
                "suite": "lm_eval",
                "tasks": ["gsm8k"],
                "runner_backend": "http",
                "runner_config": {
                    "endpoint_url": "http://allowed-oracle.example.test/v1",
                    "api_key_env": "MATRIX_BENCH_API_KEY_MISSING",
                },
                "target_model": "gpt-4o",
                "sync_exec": False,
            },
        )
        assert r2.status_code == 201
        run2_id = r2.json()["id"]
        with pytest.raises(BenchmarkCredentialError) as exc2:
            run_benchmark_job(app.state.session_factory, run2_id)
        assert getattr(exc2.value, "non_retryable", False) is True

        detail2 = client.get(f"/api/v1/benchmarks/runs/{run2_id}", headers=auth(viewer)).json()
        assert detail2["status"] == "failed"
        assert detail2["error"] == "Benchmark credential configuration error"
        assert "MATRIX_BENCH_API_KEY_MISSING" not in (detail2["error"] or "")
    finally:
        egress.set_resolver_for_tests(None)


def test_sync_exec_ignored_outside_test_env(env, monkeypatch):
    monkeypatch.setenv("MATRIX_ENV", "production")
    client: TestClient = env["client"]
    member = env["member_token_a"]

    for qs in ("", "?sync_exec=true"):
        resp = client.post(
            f"/api/v1/benchmarks/runs{qs}",
            headers=auth(member),
            json={
                "name": "prod-sync-ignored",
                "suite": "lm_eval",
                "tasks": ["gsm8k"],
                "runner_backend": "local_sim",
                "target_model": "llama-3",
                "sync_exec": True,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "queued"
        assert data["job_id"] is not None


def test_sync_exec_engine_unavailable_and_egress_error_do_not_500(env):
    client: TestClient = env["client"]
    member = env["member_token_a"]
    app = env["app"]

    # 1. lm_eval EngineUnavailable -> 201 with persisted failed run (no 500)
    resp_eng = client.post(
        "/api/v1/benchmarks/runs",
        headers=auth(member),
        json={
            "name": "sync-lm-eval-unavailable",
            "suite": "lm_eval",
            "tasks": ["gsm8k"],
            "runner_backend": "lm_eval",
            "target_model": "llama-3",
            "sync_exec": True,
        },
    )
    assert resp_eng.status_code == 201, resp_eng.text
    eng_data = resp_eng.json()
    assert eng_data["status"] == "failed"
    assert "install the real engine" in (eng_data["error"] or "")

    # 2. Async run_benchmark_job sets non_retryable=True on EngineUnavailable
    resp_async = client.post(
        "/api/v1/benchmarks/runs",
        headers=auth(member),
        json={
            "name": "async-vlmeval-unavailable",
            "suite": "vlmevalkit",
            "tasks": ["docvqa"],
            "runner_backend": "vlmeval",
            "target_model": "qwen-vl",
            "sync_exec": False,
        },
    )
    assert resp_async.status_code == 201
    with pytest.raises(EngineUnavailable) as exc_info:
        run_benchmark_job(app.state.session_factory, resp_async.json()["id"])
    assert getattr(exc_info.value, "non_retryable", False) is True

    # 3. EgressError during sync_exec -> 201 with persisted failed run (no 500)
    resp_egress = client.post(
        "/api/v1/benchmarks/runs",
        headers=auth(member),
        json={
            "name": "sync-egress-blocked",
            "suite": "lm_eval",
            "tasks": ["gsm8k"],
            "runner_backend": "http",
            "runner_config": {"endpoint_url": "http://blocked.example.com/v1"},
            "target_model": "gpt-4o",
            "sync_exec": True,
        },
    )
    assert resp_egress.status_code == 201, resp_egress.text
    egress_data = resp_egress.json()
    assert egress_data["status"] == "failed"
    assert "allowlist" in (egress_data["error"] or "")


def test_http_runner_timeout_bounded_concurrent_and_simulated_true(env, monkeypatch):
    client: TestClient = env["client"]
    member = env["member_token_a"]
    app = env["app"]
    project_a = env["project_a"]

    assert HttpBenchmarkEngine.simulated is True

    # Invalid timeout_seconds rejected at API with 422
    for bad_timeout in ("abc", True, -5, 0):
        r_bad = client.post(
            "/api/v1/benchmarks/runs",
            headers=auth(member),
            json={
                "name": "bad-timeout",
                "suite": "lm_eval",
                "tasks": ["gsm8k"],
                "runner_backend": "http",
                "runner_config": {"endpoint_url": "https://bench.example.com/v1", "timeout_seconds": bad_timeout},
                "target_model": "gpt-4o",
            },
        )
        assert r_bad.status_code == 422

    observed_timeouts: list[float] = []
    real_egress_client = bench_runner_mod.egress_client

    def _spy_egress_client(*args, **kwargs):
        observed_timeouts.append(float(kwargs["timeout"]))
        return real_egress_client(*args, **kwargs)

    monkeypatch.setattr(bench_runner_mod, "egress_client", _spy_egress_client)

    active = 0
    max_active = 0
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    class ConcurrentFakeHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            nonlocal active, max_active
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length)
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                barrier.wait(timeout=3.0)
            finally:
                with lock:
                    active -= 1
            body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ConcurrentFakeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    with app.state.session_factory() as db, db.begin():
        db.add(EgressHost(project_id=project_a, host="concurrent.example.test"))

    egress.set_resolver_for_tests(lambda host, p: ["93.184.216.34"] if host == "concurrent.example.test" else [])
    egress.set_dialer_for_tests(lambda ip, p: ("127.0.0.1", port) if ip == "93.184.216.34" else (ip, p))

    try:
        resp = client.post(
            "/api/v1/benchmarks/runs",
            headers=auth(member),
            json={
                "name": "concurrent-http-bench",
                "suite": "lm_eval",
                "tasks": ["mmlu_pro", "gpqa_diamond", "ifeval", "gsm8k"],
                "runner_backend": "http",
                "runner_config": {
                    "endpoint_url": "http://concurrent.example.test/v1",
                    "timeout_seconds": 3600,
                },
                "target_model": "fake-openai-model",
                "sync_exec": True,
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "completed"
        assert data["simulated"] is True
        assert [r["task_name"] for r in data["task_results"]] == sorted(
            ["mmlu_pro", "gpqa_diamond", "ifeval", "gsm8k"]
        )
        assert all(r["simulated"] is True for r in data["task_results"])
        assert max_active == 4
        assert observed_timeouts and all(t == 300.0 for t in observed_timeouts)
    finally:
        egress.set_resolver_for_tests(None)
        egress.set_dialer_for_tests(None)
        server.shutdown()
        server.server_close()
