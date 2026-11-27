"""Deleting a project leaves no rows of the modules merged from `main` (T3), and, since T4 put
`main`'s runs, metrics and artifacts on `runs` / `run_metrics` / `artifacts`, no metric points and
no blobs either (the `project.purge_spans` job of the G1/G0 stores).

Their tables (`mlops_*`, `dataset_branches/commits/commit_items`, `benchmark_*`, `sandbox_*`,
`safety_scans`) have a `project_id` without a FK to `projects`, so nothing cascades: the generic
sweep of `projects.delete_project` (every table with a `project_id` column) must reach them all.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import project_with_roles
from sqlalchemy import func, select

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.storage.db import Base, make_engine

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
CH_URL = os.environ.get("MATRIX_TEST_CLICKHOUSE_URL")
METRIC_BACKENDS = ["memory", *(["clickhouse"] if CH_URL else [])]
MAIN_TABLES = {
    "benchmark_runs",
    "benchmark_task_results",
    "dataset_branches",
    "dataset_commits",
    "dataset_commit_items",
    "data_sets",
    "data_set_versions",
    "data_set_files",
    "data_set_tags",
    "mlops_artifact_lineage",
    "model_artifacts",
    "artifacts",
    "mlops_model_aliases",
    "mlops_model_versions",
    "mlops_registered_models",
    "sweeps",
    "gateway_requests",
    "gateway_rate_windows",
    "runs",
    "run_reports",
    "safety_scans",
    "sandbox_runs",
    "sandbox_task_transcripts",
}


@pytest.fixture(params=["sqlite", "postgres"])
def app(request, tmp_path):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path}/purge.db"
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        url = PG_URL
        eng = make_engine(url)
        Base.metadata.drop_all(eng)
        eng.dispose()
    app = create_app(Settings(database_url=url, span_store="memory", artifact_local_dir=str(tmp_path / "a")))
    yield app
    app.state.db_engine.dispose()
    if request.param == "postgres":
        eng = make_engine(url)
        Base.metadata.drop_all(eng)
        eng.dispose()


def _ok(resp, *codes):
    assert resp.status_code in (codes or (200, 201, 202)), resp.text
    return resp.json()


def _populate(client: TestClient, h: dict[str, str]) -> None:
    run = _ok(client.post("/api/v1/runs", json={"name": "r"}, headers=h))
    _ok(client.post(f"/api/v1/runs/{run['id']}/reports", json={"kind": "custom", "name": "classic_eval", "data": {"f1": 0.5}}, headers=h))
    sha = hashlib.sha256(b"w").hexdigest()
    art = _ok(
        client.post(
            "/api/v1/artifacts",
            content=b"w",
            headers={**h, "x-filename": "w", "content-type": "application/octet-stream", "x-sha256": sha, "x-owner-kind": "run", "x-owner-id": run["id"]},
        )
    )
    _ok(client.post("/api/v1/data-sets", json={"name": "files"}, headers=h))
    ver = _ok(client.post("/api/v1/data-sets/files/versions", json={"files": [{"path": "w.bin", "artifact_id": art["id"]}]}, headers=h))
    _ok(client.put("/api/v1/data-sets/files/tags/gold", json={"version": 1}, headers=h))
    _ok(client.post("/api/v1/runs", json={"name": "trained", "dataset_version_id": ver["id"]}, headers=h))
    model = _ok(client.post("/api/v1/mlops/models", json={"name": "m"}, headers=h))
    _ok(client.post(f"/api/v1/mlops/models/{model['id']}/versions", json={"run_id": run["id"], "artifact_id": art["id"]}, headers=h))
    _ok(client.post(f"/api/v1/mlops/models/{model['id']}/aliases", json={"alias": "candidate", "version": 1}, headers=h))
    sweep = {
        "name": "s",
        "config": {
            "method": "random",
            "metric": {"key": "loss", "goal": "minimize"},
            "parameters": {"lr": {"type": "float", "min": 1e-5, "max": 1e-2}},
            "max_runs": 2,
        },
    }
    _ok(client.post("/api/v1/sweeps", json=sweep, headers=h))

    ds = _ok(client.post("/api/v1/datasets", json={"name": "d"}, headers=h))
    _ok(client.post(f"/api/v1/datasets/{ds['id']}/items", json={"input": {"q": "1"}}, headers=h))
    _ok(client.post(f"/api/v1/datasets/{ds['id']}/commits",
                    json={"branch": "main", "message": "c", "snapshot_from_working_tree": True}, headers=h))

    _ok(client.post("/api/v1/safety/scans", json={"categories": ["LLM01"], "target_model": "m"}, headers=h))
    _ok(client.post("/api/v1/benchmarks/runs", json={
        "name": "b", "suite": "lm_eval", "tasks": ["gsm8k"], "runner_backend": "local_sim", "target_model": "m", "sync_exec": True,
    }, headers=h))
    _ok(client.post("/api/v1/sandboxes/runs", json={
        "name": "sb", "runner_backend": "fake",
        "solver_config": {"steps": [{"tool_name": "file_edit", "tool_args": {"path": "a.txt", "content": "x"}}]},
        "scorer_config": {"name": "file_exists", "path": "a.txt"},
        "sync_exec": True,
    }, headers=h))
    _ok(client.post("/api/v1/gateway/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "provider": "mock",
    }, headers=h))


def _counts(app, project_id: str) -> dict[str, int]:
    with app.state.session_factory() as db:
        return {
            t.name: db.scalar(select(func.count()).select_from(t).where(t.c.project_id == project_id))
            for t in Base.metadata.sorted_tables
            if "project_id" in t.c
        }


def test_delete_project_removes_rows_of_every_main_module(app, monkeypatch):
    monkeypatch.setenv("MATRIX_ENV", "test")
    client = TestClient(app)
    factory = app.state.session_factory
    pid, roles = project_with_roles(factory, "doomed")
    keep_pid, keep_roles = project_with_roles(factory, "kept")
    _populate(client, roles["owner"])
    _populate(client, keep_roles["owner"])

    before = _counts(app, pid)
    assert {t for t in MAIN_TABLES if before.get(t, 0) == 0} == set(), before

    assert client.delete(f"/api/v1/projects/{pid}", headers=roles["owner"]).status_code == 204

    after = _counts(app, pid)
    leftovers = {t: n for t, n in after.items() if n and t not in ("audit_log", "jobs")}
    assert leftovers == {}
    # only the purge job (outside-the-DB data) and the audit trail remain
    with factory() as db:
        from matrix_server.storage.models import Job

        kinds = set(db.scalars(select(Job.kind).where(Job.project_id == pid)))
    assert kinds == {"project.purge_spans"}
    kept = _counts(app, keep_pid)
    assert all(kept[t] > 0 for t in MAIN_TABLES), kept


def _mlops_data(client: TestClient, h: dict[str, str]) -> tuple[list[str], str]:
    """Canonical runs with metric points and a ready G0 blob linked to the run."""
    run = _ok(client.post("/api/v1/runs", json={"name": "m", "env": {"platform": "linux"}}, headers=h))
    pts = [{"key": "loss", "step": i, "value": 1.0 / (i + 1)} for i in range(5)]
    _ok(client.post(f"/api/v1/runs/{run['id']}/metrics", json={"points": pts}, headers=h))
    g1 = _ok(client.post("/api/v1/runs", json={"name": "g"}, headers=h))
    _ok(client.post(f"/api/v1/runs/{g1['id']}/metrics", json={"points": [{"key": "acc", "step": 0, "value": 0.9}]}, headers=h))
    data = os.urandom(64)
    sha = hashlib.sha256(data).hexdigest()
    art = _ok(
        client.post(
            "/api/v1/artifacts",
            content=data,
            headers={**h, "x-filename": "ckpt", "content-type": "application/octet-stream", "x-sha256": sha, "x-owner-kind": "run", "x-owner-id": run["id"]},
        )
    )
    _ok(client.post(f"/api/v1/runs/{run['id']}/artifacts", json={"artifact_id": art["id"], "name": "ckpt", "kind": "model"}, headers=h))
    return [run["id"], g1["id"]], sha


@pytest.mark.parametrize("metric_backend", METRIC_BACKENDS)
def test_delete_project_purges_mlops_points_and_blobs(app, metric_backend):
    from matrix_server.api.artifacts import get_store
    from matrix_server.runs.store import ClickHouseMetricStore
    from matrix_server.storage.models import Job
    from matrix_server.workers import artifact_handlers, handlers, online_handlers, run_handlers

    ch = None
    if metric_backend == "clickhouse":
        ch = ClickHouseMetricStore(CH_URL, f"matrix_test_t4purge_{uuid.uuid4().hex[:12]}")
        ch.ensure_schema()
        app.state.metric_store = ch
    try:
        client = TestClient(app)
        factory = app.state.session_factory
        pid, roles = project_with_roles(factory, "doomed")
        keep_pid, keep_roles = project_with_roles(factory, "kept")
        runs, sha = _mlops_data(client, roles["owner"])
        kept_runs, kept_sha = _mlops_data(client, keep_roles["owner"])
        metrics, blobs = app.state.metric_store, get_store(app)
        assert all(metrics.keys(pid, r) for r in runs) and blobs.exists(pid, sha)

        assert client.delete(f"/api/v1/projects/{pid}", headers=roles["owner"]).status_code == 204
        with factory() as db:
            job = db.scalars(select(Job).where(Job.project_id == pid, Job.kind == "project.purge_spans")).one()
        purged: list[str] = []

        class _Recording:  # the job's own call; ClickHouse waits for its lightweight delete
            def delete_project(self, project_id: str) -> None:
                purged.append(project_id)
                if ch is not None:
                    ch.delete_project(project_id, wait=True)
                else:
                    metrics.delete_project(project_id)

        artifact_handlers.set_artifact_store(blobs)
        run_handlers.set_metric_store(_Recording())
        online_handlers.set_span_store(app.state.span_store)
        try:
            with factory() as db:
                handlers.purge_project_spans(db, job)
        finally:
            artifact_handlers.set_artifact_store(None)
            run_handlers.set_metric_store(None)
            online_handlers.set_span_store(None)
        assert purged == [pid]

        leftovers = {t: n for t, n in _counts(app, pid).items() if n and t not in ("audit_log", "jobs")}
        assert leftovers == {}
        assert [metrics.keys(pid, r) for r in runs] == [[], []]
        assert not blobs.exists(pid, sha)
        # the other project keeps everything
        assert all(metrics.keys(keep_pid, r) for r in kept_runs) and blobs.exists(keep_pid, kept_sha)
    finally:
        if ch is not None:
            ch.drop_database()
