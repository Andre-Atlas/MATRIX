"""`main`'s MLOps tables after compat removal: runs are `runs`, checkpoints are `model_artifacts`
(metadata over the G0 blob store), metric points live in the G1 `MetricStore` (`run_metrics`)."""

import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from matrix_server.config import Settings
from matrix_server.runs import service
from matrix_server.runs.models import Run, RunReport
from matrix_server.runs.store import MemoryMetricStore, MetricStore, build_metric_store
from matrix_server.storage.db import Base, init_db, make_engine, make_session_factory
from matrix_server.storage.models import (
    ArtifactLineage,
    MLOpsSweep,
    ModelAlias,
    ModelArtifact,
    ModelVersion,
    Project,
    RegisteredModel,
)

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def session(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'mlops.db'}")
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        eng = make_engine(PG_URL)
        Base.metadata.drop_all(eng)
    init_db(eng)
    s = make_session_factory(eng)()
    s.add_all([Project(id="p1", name="p1"), Project(id="p2", name="p2")])
    s.commit()
    yield s
    s.close()
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


def _insert(store: MetricStore, project_id: str, run_id: str, points: list[dict]) -> int:
    parsed, rejected = service.parse_points(points, datetime.now(UTC))
    assert rejected == []
    store.insert(project_id, run_id, [p for _, p in parsed])
    return len(points)


def test_mlops_models_crud_and_multitenant_isolation(session):
    sweep_p1 = MLOpsSweep(
        project_id="p1",
        name="lr-sweep",
        method="bayes",
        metric_name="eval/loss",
        metric_goal="minimize",
        search_space={"lr": {"min": 1e-5, "max": 1e-3, "distribution": "log_uniform"}},
        early_stopping={"method": "asha", "grace_period": 100},
        max_trials=12,
    )
    sweep_p2 = MLOpsSweep(
        project_id="p2",
        name="lr-sweep",
        method="grid",
        metric_name="eval/loss",
        search_space={"lr": [1e-4, 5e-4]},
    )
    session.add_all([sweep_p1, sweep_p2])
    session.flush()

    report_payload = {
        "labels": ["neg", "pos"],
        "metrics": {"roc_auc": 0.942},
        "per_class": [],
        "confusion_matrix": [[120, 8], [5, 117]],
        "roc": [],
        "pr": [],
    }
    run_p1 = Run(
        project_id="p1",
        name="qwen-lora-01",
        run_type="lora",
        base_model="Qwen/Qwen2.5-7B-Instruct",
        sweep_id=sweep_p1.id,
        params={"lr": 2e-4, "lora_r": 16},
        env={"hardware": {"gpu": "A100-80GB", "count": 2}},
        tags=["lora", "dpo"],
    )
    run_p2 = Run(project_id="p2", name="qwen-lora-01", run_type="sft", sweep_id=sweep_p2.id)
    session.add_all([run_p1, run_p2])
    session.flush()
    session.add(RunReport(run_id=run_p1.id, project_id="p1", kind="classification", name="classic_eval", data=report_payload))

    sweep_p1.best_run_id = run_p1.id
    sweep_p1.best_metric_value = 0.312

    large_size = 12 * 1024 * 1024 * 1024  # 12 GiB (> 32-bit int max)
    art_p1 = ModelArtifact(
        project_id="p1",
        run_id=run_p1.id,
        name="adapter-weights",
        kind="lora_adapter",
        version=1,
        external_uri="s3://matrix-artifacts/p1/adapter.safetensors",
        sha256="a" * 64,
        size_bytes=large_size,
        status="committed",
        metadata_={"format": "safetensors"},
    )
    art_p2 = ModelArtifact(
        project_id="p2",
        run_id=run_p2.id,
        name="adapter-weights",
        kind="lora_adapter",
        version=1,
        status="pending_upload",
    )
    session.add_all([art_p1, art_p2])
    session.flush()

    edge_p1 = ArtifactLineage(
        project_id="p1",
        source_type="training_run",
        source_id=run_p1.id,
        target_type="artifact",
        target_id=art_p1.id,
        relation="produced",
        metadata_={"step": 1000},
    )
    edge_p2 = ArtifactLineage(
        project_id="p2",
        source_type="training_run",
        source_id=run_p1.id,
        target_type="artifact",
        target_id=art_p1.id,
        relation="produced",
    )
    session.add_all([edge_p1, edge_p2])

    model_p1 = RegisteredModel(
        project_id="p1",
        name="support-lora",
        model_type="lora",
        base_model="Qwen/Qwen2.5-7B-Instruct",
        tags=["support"],
    )
    model_p2 = RegisteredModel(
        project_id="p2",
        name="support-lora",
        model_type="lora",
    )
    session.add_all([model_p1, model_p2])
    session.flush()

    mv_p1 = ModelVersion(
        project_id="p1",
        model_id=model_p1.id,
        version=1,
        artifact_id=art_p1.id,
        run_id=run_p1.id,
        stage="staging",
        lora_config={"r": 16, "alpha": 32},
        signature={"inputs": ["messages"], "outputs": ["text"]},
        metrics_snapshot={"eval/loss": 0.312},
        created_by="user-1",
    )
    alias_p1 = ModelAlias(
        project_id="p1",
        model_id=model_p1.id,
        alias="challenger",
        version=1,
    )
    session.add_all([mv_p1, alias_p1])
    session.commit()
    session.expire_all()

    loaded_run = session.get(Run, run_p1.id)
    assert loaded_run is not None and loaded_run.status == "running" and loaded_run.run_type == "lora"
    loaded_report = session.scalars(
        select(RunReport).where(RunReport.run_id == run_p1.id, RunReport.name == "classic_eval")
    ).one()
    assert loaded_report.data == report_payload
    loaded_art = session.get(ModelArtifact, art_p1.id)
    assert loaded_art is not None
    assert loaded_art.size_bytes == large_size

    p1_runs = session.scalars(select(Run).where(Run.project_id == "p1")).all()
    assert [r.id for r in p1_runs] == [run_p1.id]
    p2_models = session.scalars(select(RegisteredModel).where(RegisteredModel.project_id == "p2")).all()
    assert [m.id for m in p2_models] == [model_p2.id]


def test_mlops_unique_constraints_and_cascades(session):
    edge = ArtifactLineage(
        project_id="p1",
        source_type="dataset",
        source_id="ds-1",
        target_type="training_run",
        target_id="run-1",
        relation="trained_on",
    )
    session.add(edge)
    session.commit()

    dup_edge = ArtifactLineage(
        project_id="p1",
        source_type="dataset",
        source_id="ds-1",
        target_type="training_run",
        target_id="run-1",
        relation="trained_on",
    )
    session.add(dup_edge)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    model = RegisteredModel(project_id="p1", name="chat-model")
    session.add(model)
    session.flush()
    session.add(ModelVersion(project_id="p1", model_id=model.id, version=1))
    session.add(ModelAlias(project_id="p1", model_id=model.id, alias="champion", version=1))
    session.commit()

    session.add(ModelVersion(project_id="p1", model_id=model.id, version=1))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.add(ModelAlias(project_id="p1", model_id=model.id, alias="champion", version=1))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.execute(delete(RegisteredModel).where(RegisteredModel.id == model.id))
    session.commit()
    assert session.scalars(select(ModelVersion)).all() == []
    assert session.scalars(select(ModelAlias)).all() == []


def test_metric_store_protocol_and_builder():
    store = build_metric_store(Settings(span_store="memory"))
    assert isinstance(store, MemoryMetricStore)
    assert isinstance(store, MetricStore)


def test_canonical_points_land_in_the_g1_store_with_isolation():
    store = MemoryMetricStore()
    t0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    inserted = _insert(
        store,
        "p1",
        "run-1",
        [
            {"key": "loss", "step": 1, "value": 2.5, "ts": t0.isoformat()},
            {"key": "epoch", "step": 1, "value": 0.1, "ts": t0.isoformat()},
            {"key": "loss", "step": 2, "value": 2.0, "ts": (t0 + timedelta(seconds=1)).isoformat()},
            {"key": "epoch", "step": 2, "value": 0.2, "ts": (t0 + timedelta(seconds=1)).isoformat()},
            {"key": "eval/loss", "step": 2, "value": 2.1, "ts": (t0 + timedelta(seconds=2)).isoformat()},
            {"key": "sys/gpu.0.utilization", "step": 2, "value": 95.0, "ts": (t0 + timedelta(seconds=3)).isoformat()},
            {"key": "lr", "step": 3, "value": 1e-4, "ts": (t0 + timedelta(seconds=4)).isoformat()},
        ],
    )
    assert inserted == 7
    assert store.keys("p1", "run-1") == ["epoch", "eval/loss", "loss", "lr", "sys/gpu.0.utilization"]
    series, _ = store.read_series("p1", ["run-1"], ["epoch"])
    assert series["run-1"]["epoch"].steps == [1, 2] and series["run-1"]["epoch"].values == [0.1, 0.2]
    assert store.summaries("p1", ["run-1"])["run-1"] == {"epoch": 0.2, "eval/loss": 2.1, "loss": 2.0, "lr": 1e-4}
    assert store.last_steps("p1", ["run-1"]) == {"run-1": 3}

    _insert(store, "p2", "run-1", [{"key": "loss", "step": 1, "value": 99.0}])
    assert store.keys("p3", "run-1") == []
    p2, _ = store.read_series("p2", ["run-1"], ["loss"])
    assert p2["run-1"]["loss"].values == [99.0]
    ranged, _ = store.read_series("p1", ["run-1"], ["loss", "lr"], from_step=2, to_step=3)
    assert ranged["run-1"]["loss"].steps == [2] and ranged["run-1"]["lr"].steps == [3]


def test_resending_a_point_replaces_it():
    store = MemoryMetricStore()
    _insert(store, "p1", "r", [{"key": "loss", "step": 5, "value": 1.0}])
    _insert(store, "p1", "r", [{"key": "loss", "step": 5, "value": 0.5}])
    series, _ = store.read_series("p1", ["r"], ["loss"])
    assert series["r"]["loss"].steps == [5] and series["r"]["loss"].values == [0.5]


@pytest.mark.parametrize("total_points,max_points", [(1000, 52), (250, 12), (105, 100)])
def test_m4_downsampling_is_deterministic_and_keeps_the_ends(total_points: int, max_points: int):
    store = MemoryMetricStore()
    t0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    points = [
        {"key": "loss", "step": i, "value": float(total_points - i), "ts": (t0 + timedelta(seconds=i)).isoformat()}
        for i in range(total_points)
    ]
    _insert(store, "p1", "run-ds", points)
    res1, down1 = store.read_series("p1", ["run-ds"], ["loss"], max_points=max_points)
    res2, _ = store.read_series("p1", ["run-ds"], ["loss"], max_points=max_points)
    series = res1["run-ds"]["loss"]
    assert series == res2["run-ds"]["loss"]
    assert down1 is (total_points > max_points)
    assert 1 <= len(series.steps) <= max_points
    assert series.steps == sorted(series.steps)
    assert series.steps[0] == 0 and series.steps[-1] == total_points - 1


def test_non_finite_values_are_stored_and_read_as_null():
    store = MemoryMetricStore()
    parsed, _ = service.parse_points([{"key": "loss", "step": 1, "value": "nan"}], datetime.now(UTC))
    store.insert("p1", "run-1", [p for _, p in parsed])
    series, _ = store.read_series("p1", ["run-1"], ["loss"])
    assert series["run-1"]["loss"].values == [None]
    assert math.isnan(parsed[0][1].value)


def test_memory_metric_store_thread_safety():
    store = MemoryMetricStore()

    def worker(worker_id: int) -> None:
        _insert(store, "p1", "run-mt", [{"key": "loss", "step": worker_id * 20 + i, "value": float(i)} for i in range(20)])
        store.read_series("p1", ["run-mt"], ["loss"], max_points=52)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))

    res, _ = store.read_series("p1", ["run-mt"], ["loss"], max_points=500)
    assert len(res["run-mt"]["loss"].steps) == 160
