"""Tests for canonical /api/v1/sweeps API, engine (grid/random/TPE/ASHA/Hyperband/RF), concurrency, RBAC, and migration fragment."""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from rbac_helpers import project_with_roles
from sqlalchemy import MetaData, inspect, select, text

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.runs.models import Run
from matrix_server.storage.db import Base, make_engine, utcnow
from matrix_server.storage.models import MLOpsSweep, Project
from matrix_server.sweeps.engine import (
    SweepExhaustedError,
    SweepValidationError,
    assign_bracket,
    compute_importance,
    evaluate_pruning,
    num_brackets,
    suggest_next,
    validate_sweep_config,
)
from matrix_server.sweeps.models import Sweep, SweepRun

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
FRAGMENT = Path(__file__).resolve().parents[1] / "migrations" / "fragments" / "t5_sweeps.py"


def _drop_all(url: str) -> None:
    eng = make_engine(url)
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def env(tmp_path: Path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'sweeps.db'}", span_store="memory")
    app = create_app(settings)
    client = TestClient(app)
    try:
        pid_a, roles_a = project_with_roles(app.state.session_factory, "Sweep Project A")
        pid_b, roles_b = project_with_roles(app.state.session_factory, "Sweep Project B")
        yield {
            "app": app,
            "client": client,
            "project_a": pid_a,
            "project_b": pid_b,
            "viewer_a": roles_a["viewer"],
            "member_a": roles_a["member"],
            "admin_a": roles_a["admin"],
            "member_b": roles_b["member"],
        }
    finally:
        client.close()
        app.state.db_engine.dispose()


# --------------------------------------------------------------------------- engine unit tests


def test_validate_sweep_config_rejects_invalid_with_per_param_errors() -> None:
    # float in grid
    with pytest.raises(SweepValidationError) as exc:
        validate_sweep_config(
            {
                "method": "grid",
                "metric": {"key": "loss", "goal": "minimize"},
                "parameters": {"lr": {"type": "float", "min": 1e-4, "max": 1e-2}},
            }
        )
    assert "parameters.lr" in exc.value.errors

    # grid > 100_000 combinations
    with pytest.raises(SweepValidationError) as exc:
        validate_sweep_config(
            {
                "method": "grid",
                "metric": {"key": "loss", "goal": "minimize"},
                "parameters": {
                    "a": {"type": "int", "min": 1, "max": 500, "step": 1},
                    "b": {"type": "int", "min": 1, "max": 500, "step": 1},
                },
            }
        )
    assert "parameters" in exc.value.errors

    # log with min <= 0 and max_runs > 10_000
    with pytest.raises(SweepValidationError) as exc:
        validate_sweep_config(
            {
                "method": "random",
                "metric": {"key": "loss", "goal": "minimize"},
                "parameters": {"lr": {"type": "float", "min": 0.0, "max": 1.0, "log": True}},
                "max_runs": 10_001,
            }
        )
    assert "parameters.lr" in exc.value.errors
    assert "max_runs" in exc.value.errors


def test_bayes_alias_normalizes_to_tpe() -> None:
    cfg = validate_sweep_config(
        {
            "method": "bayes",
            "metric": {"key": "loss", "goal": "minimize"},
            "parameters": {"x": {"type": "float", "min": 0.0, "max": 1.0}},
        }
    )
    assert cfg["method"] == "tpe"


def test_grid_exhausts_without_repeats_and_respects_seed() -> None:
    cfg = validate_sweep_config(
        {
            "method": "grid",
            "metric": {"key": "loss", "goal": "minimize"},
            "parameters": {
                "opt": {"type": "choice", "values": ["adam", "sgd"]},
                "layers": {"type": "int", "min": 2, "max": 6, "step": 2},
                "fixed": {"type": "constant", "value": "v1"},
            },
            "seed": 42,
        }
    )
    seen = [suggest_next(cfg, i) for i in range(6)]
    tuples = [(p["opt"], p["layers"], p["fixed"]) for p in seen]
    assert len(set(tuples)) == 6
    assert all(t[2] == "v1" for t in tuples)
    # Deterministic with same seed
    assert [suggest_next(cfg, i) for i in range(6)] == seen
    # Exhausted at index 6
    with pytest.raises(SweepExhaustedError):
        suggest_next(cfg, 6)


def test_random_respects_types_bounds_log_and_q() -> None:
    cfg = validate_sweep_config(
        {
            "method": "random",
            "metric": {"key": "loss", "goal": "minimize"},
            "parameters": {
                "lr": {"type": "float", "min": 1e-5, "max": 1e-1, "log": True},
                "dropout": {"type": "float", "min": 0.0, "max": 0.5, "q": 0.1},
                "units": {"type": "int", "min": 8, "max": 32, "step": 8},
                "act": {"type": "choice", "values": ["relu", "gelu"]},
                "tag": {"type": "constant", "value": "baseline"},
            },
            "seed": 123,
        }
    )
    lrs: list[float] = []
    for i in range(150):
        p = suggest_next(cfg, i)
        assert 1e-5 <= p["lr"] <= 1e-1
        assert 0.0 <= p["dropout"] <= 0.5
        assert math.isclose(p["dropout"] * 10, round(p["dropout"] * 10), abs_tol=1e-9)
        assert p["units"] in {8, 16, 24, 32}
        assert p["act"] in {"relu", "gelu"}
        assert p["tag"] == "baseline"
        lrs.append(p["lr"])

    mean_log10 = sum(math.log10(v) for v in lrs) / len(lrs)
    assert -3.4 < mean_log10 < -2.6


def test_tpe_converges_on_2d_quadratic_better_than_random() -> None:
    def objective(p: dict[str, float]) -> float:
        return (p["x"] - 0.25) ** 2 + (p["y"] - 0.75) ** 2

    base_params = {
        "x": {"type": "float", "min": 0.0, "max": 1.0},
        "y": {"type": "float", "min": 0.0, "max": 1.0},
    }
    tpe_bests: list[float] = []
    rand_bests: list[float] = []

    for seed in range(1, 11):
        tpe_cfg = validate_sweep_config(
            {
                "method": "tpe",
                "metric": {"key": "loss", "goal": "minimize"},
                "parameters": base_params,
                "seed": seed,
            }
        )
        rand_cfg = validate_sweep_config(
            {
                "method": "random",
                "metric": {"key": "loss", "goal": "minimize"},
                "parameters": base_params,
                "seed": seed,
            }
        )

        tpe_hist: list[tuple[dict[str, Any], float]] = []
        tpe_best = float("inf")
        rand_best = float("inf")
        for idx in range(60):
            tp = suggest_next(tpe_cfg, idx, tpe_hist)
            tv = objective(tp)
            tpe_hist.append((tp, tv))
            tpe_best = min(tpe_best, tv)

            rp = suggest_next(rand_cfg, idx)
            rand_best = min(rand_best, objective(rp))

        tpe_bests.append(tpe_best)
        rand_bests.append(rand_best)

    assert sum(tpe_bests) / len(tpe_bests) < sum(rand_bests) / len(rand_bests)
    assert sum(tpe_bests) / len(tpe_bests) < 0.005

    # Reproducibility for same seed and history
    cfg = validate_sweep_config(
        {
            "method": "tpe",
            "metric": {"key": "loss", "goal": "minimize"},
            "parameters": base_params,
            "seed": 7,
        }
    )
    assert suggest_next(cfg, 40, tpe_hist) == suggest_next(cfg, 40, tpe_hist)


def test_asha_never_prunes_before_min_iter_and_keeps_one_over_eta() -> None:
    es = {"type": "asha", "min_iter": 10, "max_iter": 90, "eta": 3}
    # Never prunes before min_iter even with terrible loss
    peers = [[(5, 0.1), (10, 0.1)], [(5, 0.2), (10, 0.2)], [(5, 0.3), (10, 0.3)]]
    assert evaluate_pruning(es, "minimize", 9, 100.0, peers).should_stop is False

    # Simulate 30 runs reaching rung 10 with distinct losses; ~1/3 should continue when evaluated against all
    losses = [0.1 * (i + 1) for i in range(30)]
    all_series = [[(10, v)] for v in losses]
    kept = 0
    for idx, val in enumerate(losses):
        other = all_series[:idx] + all_series[idx + 1 :]
        if not evaluate_pruning(es, "minimize", 10, val, other).should_stop:
            kept += 1
    assert kept == 10  # exact 1/eta (30 // 3 = 10)


def test_hyperband_round_robin_brackets() -> None:
    es = {"type": "hyperband", "min_iter": 3, "max_iter": 27, "eta": 3}
    # log_3(27/3) = 2 -> s_max = 2 -> 3 brackets (0, 1, 2) with r0 = 3, 9, 27
    assert num_brackets(es) == 3
    assert [assign_bracket(es, i) for i in range(6)] == [0, 1, 2, 0, 1, 2]
    # Bracket 1 starts at r0 = 9, so step 3 is never pruned in bracket 1
    peers = [[(3, 0.1), (9, 0.1)], [(3, 0.2), (9, 0.2)], [(3, 0.3), (9, 0.3)]]
    assert evaluate_pruning(es, "minimize", 3, 99.0, peers, bracket=1).should_stop is False
    assert evaluate_pruning(es, "minimize", 9, 99.0, peers, bracket=1).should_stop is True


def test_importance_without_sklearn_and_with_mock_rf(monkeypatch: pytest.MonkeyPatch) -> None:
    params = {
        "lr": {"type": "float", "min": 1e-4, "max": 1e-1, "log": True},
        "opt": {"type": "choice", "values": ["adam", "sgd"]},
        "fixed": {"type": "constant", "value": 1},
    }
    trials = [({"lr": 1e-4 * (i + 1), "opt": "adam" if i % 2 == 0 else "sgd", "fixed": 1}, 0.1 * i) for i in range(12)]

    # < 10 runs -> None with note
    imp_few, note_few = compute_importance(params, trials[:5])
    assert imp_few is None and note_few is not None

    # Force ImportError for sklearn -> None with note
    monkeypatch.setitem(sys.modules, "sklearn", None)
    monkeypatch.setitem(sys.modules, "sklearn.ensemble", None)
    imp_none, note_none = compute_importance(params, trials)
    assert imp_none is None and note_none is not None and "scikit-learn" in note_none

    # Mock sklearn.ensemble.RandomForestRegressor -> aggregates one-hot and normalizes to 1
    class FakeRF:
        def __init__(self, n_estimators: int = 64, random_state: int = 42) -> None:
            self.feature_importances_: list[float] = []

        def fit(self, X: list[list[float]], y: list[float]) -> FakeRF:
            assert len(X) == 12 and len(X[0]) == 3  # lr + 2 one-hot for opt
            self.feature_importances_ = [0.6, 0.25, 0.15]
            return self

    fake_sklearn = types.ModuleType("sklearn")
    fake_ensemble = types.ModuleType("sklearn.ensemble")
    fake_ensemble.RandomForestRegressor = FakeRF  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sklearn", fake_sklearn)
    monkeypatch.setitem(sys.modules, "sklearn.ensemble", fake_ensemble)

    imp, note = compute_importance(params, trials, seed=7)
    assert note is None and imp is not None
    assert math.isclose(sum(item["importance"] for item in imp), 1.0, rel_tol=1e-9)
    by_param = {item["param"]: item["importance"] for item in imp}
    assert math.isclose(by_param["lr"], 0.6)
    assert math.isclose(by_param["opt"], 0.4)
    assert math.isclose(by_param["fixed"], 0.0)


# --------------------------------------------------------------------------- API & RBAC tests


def test_canonical_sweeps_lifecycle_report_pruning_pause_delete(env: dict[str, Any]) -> None:
    client: TestClient = env["client"]
    member = env["member_a"]
    viewer = env["viewer_a"]
    admin = env["admin_a"]
    member_b = env["member_b"]

    # Invalid search space -> 422
    bad = client.post(
        "/api/v1/sweeps",
        headers=member,
        json={
            "name": "bad-grid",
            "config": {
                "method": "grid",
                "metric": {"key": "loss", "goal": "minimize"},
                "parameters": {"lr": {"type": "float", "min": 1e-4, "max": 1e-2}},
            },
        },
    )
    assert bad.status_code == 422

    # Create valid ASHA sweep
    create = client.post(
        "/api/v1/sweeps",
        headers=member,
        json={
            "name": "asha-sweep",
            "config": {
                "method": "random",
                "metric": {"key": "val/loss", "goal": "minimize"},
                "parameters": {
                    "lr": {"type": "float", "min": 1e-4, "max": 1e-2, "log": True},
                    "opt": {"type": "choice", "values": ["adam", "sgd"]},
                },
                "early_stop": {"type": "asha", "min_iter": 10, "eta": 3},
                "max_runs": 10,
                "seed": 42,
            },
        },
    )
    assert create.status_code == 201, create.text
    sweep = create.json()
    sid = sweep["id"]
    assert sweep["status"] == "running"
    assert sweep["run_count"] == 0

    # List & cross-tenant isolation
    assert len(client.get("/api/v1/sweeps", headers=viewer).json()["items"]) == 1
    assert client.get(f"/api/v1/sweeps/{sid}", headers=member_b).status_code == 404

    # Allocate 3 runs and report metrics at rung 10
    run_ids: list[str] = []
    for loss in (0.1, 0.2, 0.9):
        nxt = client.post(f"/api/v1/sweeps/{sid}/next", headers=member, json={"agent_id": "a1"})
        assert nxt.status_code == 200, nxt.text
        data = nxt.json()
        rid = data["run"]["id"]
        run_ids.append(rid)
        # Canonical run has sweep_id and fixed params
        run_get = client.get(f"/api/v1/runs/{rid}", headers=viewer).json()
        assert run_get["sweep_id"] == sid
        assert run_get["params"] == data["params"]

        rep = client.post(
            f"/api/v1/sweeps/{sid}/runs/{rid}/report",
            headers=member,
            json={"step": 10, "value": loss},
        )
        assert rep.status_code == 200
        if loss == 0.9:
            assert rep.json()["action"] == "stop"
        else:
            assert rep.json()["action"] == "continue"

    # Metric was also persisted in canonical run_metrics
    m_resp = client.get(f"/api/v1/runs/{run_ids[0]}/metrics", headers=viewer, params={"keys": "val/loss"})
    assert m_resp.status_code == 200
    assert m_resp.json()["series"]["val/loss"]["values"] == [0.1]

    # Detail shows runs, best, pruned flag, importance=null (< 10 runs)
    detail = client.get(f"/api/v1/sweeps/{sid}", headers=viewer).json()
    assert detail["sweep"]["best_run_id"] == run_ids[0]
    assert detail["sweep"]["best_value"] == pytest.approx(0.1)
    assert detail["sweep"]["run_count"] == 3
    assert detail["importance"] is None
    pruned_by_id = {r["run_id"]: r["pruned"] for r in detail["runs"]}
    assert pruned_by_id[run_ids[2]] is True
    assert pruned_by_id[run_ids[0]] is False

    # Pause -> next returns 204 with Retry-After; resume -> 200
    paused = client.patch(f"/api/v1/sweeps/{sid}", headers=member, json={"status": "paused"})
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    p_next = client.post(f"/api/v1/sweeps/{sid}/next", headers=member, json={})
    assert p_next.status_code == 204
    assert "retry-after" in p_next.headers

    resumed = client.patch(f"/api/v1/sweeps/{sid}", headers=member, json={"status": "running"})
    assert resumed.status_code == 200 and resumed.json()["status"] == "running"
    r_next = client.post(f"/api/v1/sweeps/{sid}/next", headers=member, json={})
    assert r_next.status_code == 200

    # Stop -> next returns 204 without Retry-After; resume rejected 409
    stopped = client.patch(f"/api/v1/sweeps/{sid}", headers=member, json={"status": "stopped"})
    assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
    assert client.post(f"/api/v1/sweeps/{sid}/next", headers=member, json={}).status_code == 204
    assert client.patch(f"/api/v1/sweeps/{sid}", headers=member, json={"status": "running"}).status_code == 409

    # RBAC: viewer cannot write, member cannot delete, admin can delete
    assert client.post("/api/v1/sweeps", headers=viewer, json={"name": "x", "config": {}}).status_code == 403
    assert client.delete(f"/api/v1/sweeps/{sid}", headers=viewer).status_code == 403
    assert client.delete(f"/api/v1/sweeps/{sid}", headers=member).status_code == 403
    assert client.delete(f"/api/v1/sweeps/{sid}", headers=admin).status_code == 204

    # Runs survive sweep deletion with sweep_id cleared
    for rid in run_ids:
        r_after = client.get(f"/api/v1/runs/{rid}", headers=viewer)
        assert r_after.status_code == 200
        assert r_after.json()["sweep_id"] is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_10_concurrent_next_calls_produce_10_distinct_configs(backend: str, tmp_path: Path) -> None:
    if backend == "postgres":
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        _drop_all(PG_URL)
        db_url = PG_URL
    else:
        db_url = f"sqlite:///{tmp_path / 'concurrent_sweeps.db'}"

    app = create_app(Settings(database_url=db_url, span_store="memory"))
    try:
        _, roles = project_with_roles(app.state.session_factory, "Concurrent Project")
        member = roles["member"]
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/sweeps",
                headers=member,
                json={
                    "name": "grid-10",
                    "config": {
                        "method": "grid",
                        "metric": {"key": "loss", "goal": "minimize"},
                        "parameters": {
                            "k": {"type": "int", "min": 1, "max": 10, "step": 1},
                        },
                        "seed": 7,
                    },
                },
            )
            assert resp.status_code == 201
            sid = resp.json()["id"]

            def call_next(idx: int) -> tuple[int, Any]:
                with TestClient(app) as local_client:
                    r = local_client.post(
                        f"/api/v1/sweeps/{sid}/next",
                        headers=member,
                        json={"agent_id": f"worker-{idx}"},
                    )
                    return r.status_code, (r.json() if r.content else None)

            with ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(call_next, range(10)))

            assert all(code == 200 for code, _ in results), results
            ks = [body["params"]["k"] for _, body in results]
            run_ids = [body["run"]["id"] for _, body in results]
            assert len(set(ks)) == 10
            assert set(ks) == set(range(1, 11))
            assert len(set(run_ids)) == 10

            # 11th call exhausts the grid -> 204
            assert client.post(f"/api/v1/sweeps/{sid}/next", headers=member, json={}).status_code == 204
    finally:
        app.state.db_engine.dispose()
        if backend == "postgres" and PG_URL:
            _drop_all(PG_URL)


# --------------------------------------------------------------------------- migration fragment test


def _load_fragment():
    spec = importlib.util.spec_from_file_location("t5_sweeps", FRAGMENT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_t5_sweeps_migration_fragment_matches_models_and_copies_data(backend: str, tmp_path: Path) -> None:
    if backend == "postgres":
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        eng = make_engine(PG_URL)
        Base.metadata.drop_all(eng)
    else:
        eng = make_engine(f"sqlite:///{tmp_path / 't5_mig.db'}")

    try:
        # Seed 0008 prerequisite tables: projects, runs, mlops_sweeps
        Project.__table__.create(eng)
        Run.__table__.create(eng)
        MLOpsSweep.__table__.create(eng)

        now = utcnow()
        with eng.begin() as conn:
            conn.execute(
                Project.__table__.insert().values(id="proj1", name="P1", created_at=now)
            )
            conn.execute(
                MLOpsSweep.__table__.insert().values(
                    id="sw1",
                    project_id="proj1",
                    name="legacy-bayes",
                    description="migrated",
                    method="bayes",
                    metric_name="val_loss",
                    metric_goal="minimize",
                    search_space={"opt": {"type": "categorical", "values": ["adam", "sgd"]}},
                    early_stopping={"strategy": "asha", "min_step": 5, "reduction_factor": 3},
                    max_trials=20,
                    status="cancelled",
                    best_run_id="run1",
                    best_metric_value=0.15,
                    created_at=now,
                    stopped_at=now,
                )
            )
            conn.execute(
                Run.__table__.insert().values(
                    id="run1",
                    project_id="proj1",
                    name="trial-1",
                    status="finished",
                    tags=[],
                    params={"opt": "adam"},
                    sweep_id="sw1",
                    started_at=now,
                    point_count=0,
                    key_counts={},
                )
            )

        fragment = _load_fragment()
        with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            fragment.upgrade()

        # Schema matches SQLAlchemy models exactly
        target = MetaData()
        for name in ("projects", "runs", "mlops_sweeps", *fragment.TABLES):
            Base.metadata.tables[name].to_metadata(target)
        with eng.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), target)
        assert diff == []

        # Seeded legacy sweep & linked run were migrated properly
        with eng.connect() as conn:
            row = conn.execute(select(Sweep).where(Sweep.id == "sw1")).first()
            assert row is not None
            sw = row._mapping
            assert sw["status"] == "stopped"
            assert sw["metric_key"] == "val_loss"
            assert sw["next_index"] == 1
            assert sw["best_run_id"] == "run1"
            assert sw["best_value"] == pytest.approx(0.15)
            cfg = sw["config"]
            assert cfg["method"] == "tpe"
            assert cfg["parameters"]["opt"]["type"] == "choice"
            assert cfg["early_stop"] == {"type": "asha", "min_iter": 5, "eta": 3}
            assert cfg["max_runs"] == 20

            sr_count = conn.execute(text("SELECT count(*) FROM sweep_runs WHERE sweep_id = 'sw1'")).scalar()
            assert sr_count == 1
            sr_row = conn.execute(select(SweepRun).where(SweepRun.sweep_id == "sw1")).first()
            assert sr_row is not None and sr_row._mapping["run_id"] == "run1"

        # Downgrade drops sweep_runs and sweeps cleanly
        with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            fragment.downgrade()
        assert set(inspect(eng).get_table_names()) == {"projects", "runs", "mlops_sweeps"}
    finally:
        if backend == "postgres":
            Base.metadata.drop_all(eng)
        eng.dispose()


def test_sweep_detail_paginates_and_importance_is_off_the_get_path(env: dict[str, Any], monkeypatch) -> None:
    from matrix_server.sweeps import service as sweep_service
    from matrix_server.workers import run_handlers
    from matrix_server.workers import runner as worker_runner

    client: TestClient = env["client"]
    member, viewer = env["member_a"], env["viewer_a"]
    factory = env["app"].state.session_factory
    calls: list[int] = []

    def fake_importance(parameters, completed, *, seed=None):
        calls.append(len(completed))
        return [{"param": "lr", "importance": 1.0}], None

    monkeypatch.setattr(sweep_service, "compute_importance", fake_importance)
    run_handlers.set_metric_store(env["app"].state.metric_store)
    worker_runner.load_handler_modules()
    sid = client.post(
        "/api/v1/sweeps",
        headers=member,
        json={
            "name": "many",
            "config": {
                "method": "random",
                "metric": {"key": "val/loss", "goal": "minimize"},
                "parameters": {"lr": {"type": "float", "min": 1e-4, "max": 1e-2}},
            },
        },
    ).json()["id"]
    for i in range(12):
        nxt = client.post(f"/api/v1/sweeps/{sid}/next", headers=member, json={}).json()
        rid = nxt["run"]["id"]
        assert client.post(f"/api/v1/sweeps/{sid}/runs/{rid}/report", headers=member, json={"step": 1, "value": 1.0 + i}).status_code == 200
        assert client.patch(f"/api/v1/runs/{rid}", headers=member, json={"status": "finished"}).status_code == 200

    # Pagination: default keeps the g2 shape; limit/offset page through, with totals.
    full = client.get(f"/api/v1/sweeps/{sid}", headers=viewer).json()
    assert len(full["runs"]) == 12 and full["runs_total"] == 12 and full["runs_next_offset"] is None
    p1 = client.get(f"/api/v1/sweeps/{sid}", headers=viewer, params={"limit": 5}).json()
    p2 = client.get(f"/api/v1/sweeps/{sid}", headers=viewer, params={"limit": 5, "offset": 5}).json()
    p3 = client.get(f"/api/v1/sweeps/{sid}", headers=viewer, params={"limit": 5, "offset": 10}).json()
    assert [len(p["runs"]) for p in (p1, p2, p3)] == [5, 5, 2]
    assert (p1["runs_next_offset"], p2["runs_next_offset"], p3["runs_next_offset"]) == (5, 10, None)
    assert [r["run_id"] for p in (p1, p2, p3) for r in p["runs"]] == [r["run_id"] for r in full["runs"]]
    assert p1["sweep"]["run_count"] == 12
    assert client.get(f"/api/v1/sweeps/{sid}", headers=viewer, params={"limit": 100000}).status_code == 422

    # GET never fits the model: importance is pending/stale until the worker job ran.
    assert calls == []
    assert full["importance"] is None and full["importance_stale"] is True

    while worker_runner.run_once(factory, "imp-worker"):
        pass
    assert calls == [12]  # one deduped job for 12 finished runs
    done = client.get(f"/api/v1/sweeps/{sid}", headers=viewer).json()
    assert done["importance"] == [{"param": "lr", "importance": 1.0}]
    assert done["importance_stale"] is False and done["importance_computed_at"].endswith("Z")
    assert calls == [12]  # still not recomputed by GET

    # A new finished run invalidates the cache (stale) until the next refresh.
    nxt = client.post(f"/api/v1/sweeps/{sid}/next", headers=member, json={}).json()
    client.patch(f"/api/v1/runs/{nxt['run']['id']}", headers=member, json={"status": "finished"})
    assert client.get(f"/api/v1/sweeps/{sid}", headers=viewer).json()["importance_stale"] is True
    while worker_runner.run_once(factory, "imp-worker"):
        pass
    assert client.get(f"/api/v1/sweeps/{sid}", headers=viewer).json()["importance_stale"] is False
    run_handlers.set_metric_store(None)
