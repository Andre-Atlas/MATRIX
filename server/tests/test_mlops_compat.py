"""T4: `main`'s `/api/v1/mlops/runs*` routes are deprecated adapters over the G1 stack.

One store: a series sent through the adapter and through `/api/v1/runs/{id}/metrics` is the same
data and comes back as the same M4 series (memory store, and ClickHouse when configured)."""

from __future__ import annotations

import math
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import project_with_roles

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.mlops.compat import classic_eval_to_reports, reports_to_classic_eval

CH_URL = os.environ.get("MATRIX_TEST_CLICKHOUSE_URL")
METRIC_BACKENDS = ["memory", *(["clickhouse"] if CH_URL else [])]


@pytest.fixture(params=METRIC_BACKENDS)
def env(request, tmp_path):
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path}/c.db", span_store="memory", artifact_local_dir=str(tmp_path / "a"))
    )
    ch = None
    if request.param == "clickhouse":
        from matrix_server.runs.store import ClickHouseMetricStore

        ch = ClickHouseMetricStore(CH_URL, f"matrix_test_t4compat_{uuid.uuid4().hex[:12]}")
        ch.ensure_schema()
        app.state.metric_store = ch
    client = TestClient(app)
    _, roles = project_with_roles(app.state.session_factory, "P")
    yield client, roles
    app.state.db_engine.dispose()
    if ch is not None:
        ch.drop_database()


def _series(n: int) -> list[tuple[int, float]]:
    return [(i, math.sin(i / 7.0) + (5.0 if i % 97 == 0 else 0.0)) for i in range(n)]


def test_same_series_through_both_apis_gives_the_same_m4(env):
    client, roles = env
    h = roles["member"]
    points = _series(3000)
    legacy = client.post("/api/v1/mlops/runs", json={"name": "legacy"}, headers=h).json()
    g1 = client.post("/api/v1/runs", json={"name": "g1"}, headers=h).json()
    for start in range(0, len(points), 1000):  # the adapter takes 1000 points per request
        chunk = points[start : start + 1000]
        r = client.post(
            f"/api/v1/mlops/runs/{legacy['id']}/metrics",
            json={"points": [{"category": "train", "metric_name": "loss", "step": s, "value": v} for s, v in chunk]},
            headers=h,
        )
        assert r.status_code == 202 and r.json()["accepted"] == len(chunk), r.text
        assert r.headers["Deprecation"] == "true"
    r = client.post(
        f"/api/v1/runs/{g1['id']}/metrics", json={"points": [{"key": "loss", "step": s, "value": v} for s, v in points]}, headers=h
    )
    assert r.status_code == 202 and r.json()["accepted"] == len(points)

    def m4(run_id: str) -> dict:
        resp = client.get(f"/api/v1/runs/{run_id}/metrics?keys=loss&max_points=200", headers=h)
        assert resp.status_code == 200
        body = resp.json()
        assert body["downsampled"] is True
        return {"steps": body["series"]["loss"]["steps"], "values": body["series"]["loss"]["values"]}

    a, b = m4(legacy["id"]), m4(g1["id"])
    assert a == b and len(a["steps"]) <= 200
    # ...and the adapter's read is the same M4 series in `main`'s point shape
    old = client.get(f"/api/v1/mlops/runs/{legacy['id']}/metrics?metric_names=loss&max_points=200", headers=h).json()
    assert [p["step"] for p in old["series"]["loss"]] == a["steps"]
    assert [p["value"] for p in old["series"]["loss"]] == a["values"]
    assert all(p["min"] == p["max"] == p["value"] for p in old["series"]["loss"])
    # re-sending through the adapter does not duplicate (idempotent per (key, step))
    client.post(
        f"/api/v1/mlops/runs/{legacy['id']}/metrics",
        json={"points": [{"metric_name": "loss", "step": s, "value": v} for s, v in points[:1000]]},
        headers=h,
    )
    keys = client.get(f"/api/v1/runs/{legacy['id']}/metric-keys", headers=h).json()["keys"]
    assert [k["count"] for k in keys if k["key"] == "loss"] == [3000]


def test_adapter_run_is_a_g1_run(env):
    client, roles = env
    h = roles["member"]
    run = client.post(
        "/api/v1/mlops/runs",
        json={
            "name": "lora-1",
            "run_type": "lora",
            "base_model": "llama",
            "hyperparameters": {"lr": 0.1},
            "hardware_info": {"gpu_name": "A100", "gpu_count": 2},
            "tags": ["a"],
        },
        headers=h,
    ).json()
    pts = [
        {"category": "train", "metric_name": "train/loss", "step": 10, "epoch": 0.5, "value": 0.7},
        {"category": "system", "metric_name": "cpu.percent", "step": 10, "value": 40.0},
    ]
    assert client.post(f"/api/v1/mlops/runs/{run['id']}/metrics", json={"points": pts}, headers=h).json()["accepted"] == 2

    g1 = client.get(f"/api/v1/runs/{run['id']}", headers=h).json()
    assert g1["params"] == {"lr": 0.1} and g1["status"] == "running" and g1["tags"] == ["a"]
    assert g1["env"] == {"hardware": {"gpu_name": "A100", "gpu_count": 2}}
    assert g1["summary"] == {"epoch": 0.5, "train/loss": 0.7}
    keys = {k["key"] for k in client.get(f"/api/v1/runs/{run['id']}/metric-keys", headers=h).json()["keys"]}
    assert keys == {"train/loss", "epoch", "sys/cpu.percent", "sys/hw.gpu_count"}

    legacy = client.get(f"/api/v1/mlops/runs/{run['id']}", headers=h).json()
    assert legacy["hyperparams"] == {"lr": 0.1} and legacy["run_type"] == "lora" and legacy["base_model"] == "llama"
    assert legacy["hardware_info"] == {"gpu_name": "A100", "gpu_count": 2} and legacy["total_steps"] == 10
    assert legacy["summary_metrics"]["train/loss"] == 0.7
    series = client.get(f"/api/v1/mlops/runs/{run['id']}/metrics?category=system", headers=h).json()["series"]
    assert set(series) == {"sys/cpu.percent", "sys/hw.gpu_count"}
    train = client.get(f"/api/v1/mlops/runs/{run['id']}/metrics?metric_names=train/loss", headers=h).json()["series"]
    assert train["train/loss"][0]["epoch"] == 0.5

    # status vocabulary: completed -> finished in G1, shown as completed by the adapter
    done = client.patch(f"/api/v1/mlops/runs/{run['id']}", json={"status": "completed", "summary_metrics": {"acc": 0.9}}, headers=h)
    assert done.status_code == 200 and done.json()["status"] == "completed"
    assert done.json()["summary_metrics"]["acc"] == 0.9
    assert client.get(f"/api/v1/runs/{run['id']}", headers=h).json()["status"] == "finished"
    assert client.patch(f"/api/v1/mlops/runs/{run['id']}", json={"status": "running"}, headers=h).status_code == 409
    assert client.get("/api/v1/mlops/runs?status=completed", headers=h).json()["items"][0]["id"] == run["id"]
    cancelled = client.post("/api/v1/mlops/runs", json={"name": "c", "status": "cancelled"}, headers=h).json()
    assert cancelled["status"] == "cancelled" and cancelled["finished_at"] is not None
    assert client.get(f"/api/v1/runs/{cancelled['id']}", headers=h).json()["status"] == "killed"

    # a run created by the G1 API is listed by the adapter too (one table)
    other = client.post("/api/v1/runs", json={"name": "plain"}, headers=h).json()
    ids = {r["id"] for r in client.get("/api/v1/mlops/runs", headers=h).json()["items"]}
    assert other["id"] in ids


def test_classic_eval_becomes_typed_reports(env):
    client, roles = env
    h = roles["member"]
    ce = {
        "accuracy": 0.9,
        "roc_auc": 0.95,
        "roc_curve": [{"fpr": 0.0, "tpr": 0.0}, {"fpr": 0.1, "tpr": 0.8}, {"fpr": 1.0, "tpr": 1.0}],
        "confusion_matrix": {"labels": ["neg", "pos"], "matrix": [[50, 5], [3, 42]]},
        "feature_importance": [{"feature": "a", "importance": 0.7, "direction": "positive"}, {"feature": "b", "importance": 0.3}],
        "notes": "free text",
    }
    run = client.post("/api/v1/mlops/runs", json={"name": "clf", "run_type": "classic_ml", "classic_eval": ce}, headers=h).json()
    assert run["classic_eval"] == ce
    reports = client.get(f"/api/v1/runs/{run['id']}/reports", headers=h).json()["items"]
    assert sorted(r["kind"] for r in reports) == ["classification", "custom", "importance"]
    cls = next(r for r in reports if r["kind"] == "classification")["data"]
    assert cls["labels"] == ["neg", "pos"] and cls["roc"][0]["label"] == "pos" and cls["metrics"]["accuracy"] == 0.9
    # replacing it replaces the reports
    client.patch(f"/api/v1/mlops/runs/{run['id']}", json={"classic_eval": {"f1": 0.5}}, headers=h)
    reports = client.get(f"/api/v1/runs/{run['id']}/reports", headers=h).json()["items"]
    assert [r["kind"] for r in reports] == ["custom"]


def test_classic_eval_that_does_not_validate_is_kept_as_custom():
    bad = {"confusion_matrix": {"labels": ["a", "b"], "matrix": [[1]]}, "roc_auc": 0.5}
    reports = classic_eval_to_reports(bad)
    assert [k for k, _, _ in reports] == ["custom"]
    assert reports_to_classic_eval([(k, d) for k, _, d in reports]) == bad


def test_deprecated_routes_are_marked_in_openapi(env):
    client, _roles = env
    spec = client.app.openapi()
    for path, ops in spec["paths"].items():
        for op in ops.values():
            legacy = path.startswith(("/api/v1/mlops/runs", "/api/v1/mlops/artifacts", "/api/v1/mlops/sweeps"))
            if legacy and not path.endswith("/lineage"):
                assert op.get("deprecated") is True, path
            if path.startswith(("/api/v1/runs", "/api/v1/artifacts", "/api/v1/sweeps", "/api/v1/mlops/models")):
                assert not op.get("deprecated"), path
    assert not any(p.startswith("/api/v1/mlops/artifacts/local") for p in spec["paths"])
