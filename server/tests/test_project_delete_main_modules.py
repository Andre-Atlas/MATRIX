"""Deleting a project leaves no rows of the modules merged from `main` (T3).

Their tables (`mlops_*`, `dataset_branches/commits/commit_items`, `benchmark_*`, `sandbox_*`,
`safety_scans`) have a `project_id` without a FK to `projects`, so nothing cascades: the generic
sweep of `projects.delete_project` (every table with a `project_id` column) must reach them all.
"""

from __future__ import annotations

import hashlib
import os

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import project_with_roles
from sqlalchemy import func, select

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.storage.db import Base, make_engine

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
MAIN_TABLES = {
    "benchmark_runs",
    "benchmark_task_results",
    "dataset_branches",
    "dataset_commits",
    "dataset_commit_items",
    "mlops_artifact_lineage",
    "mlops_artifacts",
    "mlops_model_aliases",
    "mlops_model_versions",
    "mlops_registered_models",
    "mlops_sweeps",
    "mlops_training_runs",
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
    run = _ok(client.post("/api/v1/mlops/runs", json={"name": "r"}, headers=h))
    model = _ok(client.post("/api/v1/mlops/models", json={"name": "m"}, headers=h))
    _ok(client.post(f"/api/v1/mlops/models/{model['id']}/versions", json={"run_id": run["id"]}, headers=h))
    _ok(client.post(f"/api/v1/mlops/models/{model['id']}/aliases", json={"alias": "candidate", "version": 1}, headers=h))
    sweep = {
        "name": "s", "method": "random", "metric_name": "loss", "metric_goal": "minimize",
        "search_space": {"lr": {"type": "float", "min": 1e-5, "max": 1e-2}}, "max_trials": 2,
    }
    _ok(client.post("/api/v1/mlops/sweeps", json=sweep, headers=h))
    sha = hashlib.sha256(b"w").hexdigest()
    _ok(client.post("/api/v1/mlops/artifacts/presign-upload",
                    json={"name": "w", "sha256": sha, "size_bytes": 1, "run_id": run["id"]}, headers=h))

    ds = _ok(client.post("/api/v1/datasets", json={"name": "d"}, headers=h))
    _ok(client.post(f"/api/v1/datasets/{ds['id']}/items", json={"input": {"q": "1"}}, headers=h))
    _ok(client.post(f"/api/v1/datasets/{ds['id']}/commits",
                    json={"branch": "main", "message": "c", "snapshot_from_working_tree": True}, headers=h))

    _ok(client.post("/api/v1/safety/scans", json={"categories": ["LLM01"], "target_model": "m"}, headers=h))
    _ok(client.post("/api/v1/benchmarks/runs", json={
        "name": "b", "suite": "lm_eval", "tasks": ["gsm8k"], "runner_backend": "local_sim", "target_model": "m", "sync_exec": True,
    }, headers=h))
    _ok(client.post("/api/v1/sandboxes/runs", json={
        "name": "sb", "runner_backend": "local_sandbox",
        "solver_config": {"steps": [{"tool_name": "file_edit", "tool_args": {"path": "a.txt", "content": "x"}}]},
        "scorer_config": {"name": "file_exists", "path": "a.txt"},
        "sync_exec": True,
    }, headers=h))


def _counts(app, project_id: str) -> dict[str, int]:
    with app.state.session_factory() as db:
        return {
            t.name: db.scalar(select(func.count()).select_from(t).where(t.c.project_id == project_id))
            for t in Base.metadata.sorted_tables
            if "project_id" in t.c
        }


def test_delete_project_removes_rows_of_every_main_module(app):
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
