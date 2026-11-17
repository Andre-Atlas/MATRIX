import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from matrix_server.config import Settings
from matrix_server.mlops.metrics_store import (
    MemoryTrainingMetricStore,
    TrainingMetricStore,
    build_training_metric_store,
)
from matrix_server.storage.db import Base, init_db, make_engine, make_session_factory
from matrix_server.storage.models import (
    Artifact,
    ArtifactLineage,
    MLOpsSweep,
    ModelAlias,
    ModelVersion,
    RegisteredModel,
    TrainingRun,
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
    yield s
    s.close()
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


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

    classic_eval_payload = {
        "roc_auc": 0.942,
        "confusion_matrix": [[120, 8], [5, 117]],
        "shap_importance": [{"feature": "token_entropy", "importance": 0.41}],
    }
    run_p1 = TrainingRun(
        project_id="p1",
        name="qwen-lora-01",
        run_type="lora",
        base_model="Qwen/Qwen2.5-7B-Instruct",
        sweep_id=sweep_p1.id,
        hyperparams={"lr": 2e-4, "lora_r": 16},
        summary_metrics={"train/loss": 0.312},
        hardware_info={"gpu": "A100-80GB", "count": 2},
        classic_eval=classic_eval_payload,
        tags=["lora", "dpo"],
        total_steps=1000,
    )
    run_p2 = TrainingRun(
        project_id="p2",
        name="qwen-lora-01",
        run_type="sft",
        sweep_id=sweep_p2.id,
    )
    session.add_all([run_p1, run_p2])
    session.flush()

    sweep_p1.best_run_id = run_p1.id
    sweep_p1.best_metric_value = 0.312

    large_size = 12 * 1024 * 1024 * 1024  # 12 GiB (> 32-bit int max)
    art_p1 = Artifact(
        project_id="p1",
        run_id=run_p1.id,
        name="adapter-weights",
        kind="lora_adapter",
        version=1,
        storage_backend="s3",
        storage_uri="s3://matrix-artifacts/p1/adapter.safetensors",
        sha256="a" * 64,
        size_bytes=large_size,
        status="committed",
        metadata_={"format": "safetensors"},
    )
    art_p2 = Artifact(
        project_id="p2",
        run_id=run_p2.id,
        name="adapter-weights",
        kind="lora_adapter",
        version=1,
        storage_backend="local",
        storage_uri="local://p2/adapter.safetensors",
        status="committed",
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

    loaded_run = session.get(TrainingRun, run_p1.id)
    assert loaded_run is not None
    assert loaded_run.classic_eval == classic_eval_payload
    loaded_art = session.get(Artifact, art_p1.id)
    assert loaded_art is not None
    assert loaded_art.size_bytes == large_size

    # Multi-tenant isolation checks
    p1_runs = session.scalars(select(TrainingRun).where(TrainingRun.project_id == "p1")).all()
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

    # Duplicate version fails
    session.add(ModelVersion(project_id="p1", model_id=model.id, version=1))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Duplicate alias fails
    session.add(ModelAlias(project_id="p1", model_id=model.id, alias="champion", version=1))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Cascade delete on RegisteredModel removes versions and aliases
    session.execute(delete(RegisteredModel).where(RegisteredModel.id == model.id))
    session.commit()
    assert session.scalars(select(ModelVersion)).all() == []
    assert session.scalars(select(ModelAlias)).all() == []


def test_memory_metric_store_protocol_and_builder():
    store = build_training_metric_store(Settings(span_store="memory"))
    assert isinstance(store, MemoryTrainingMetricStore)
    assert isinstance(store, TrainingMetricStore)


def test_memory_metric_store_insert_filters_and_isolation():
    store = MemoryTrainingMetricStore()
    t0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

    inserted = store.insert_points(
        "p1",
        "run-1",
        [
            {"category": "train", "metric_name": "loss", "step": 1, "epoch": 0.1, "value": 2.5, "recorded_at": t0},
            {"category": "train", "metric_name": "loss", "step": 2, "epoch": 0.2, "value": 2.0, "recorded_at": t0 + timedelta(seconds=1)},
            {"category": "eval", "metric_name": "loss", "step": 2, "epoch": 0.2, "value": 2.1, "recorded_at": t0 + timedelta(seconds=2)},
            {"category": "gpu", "metric_name": "util_pct", "step": 2, "value": 95.0, "recorded_at": t0 + timedelta(seconds=3)},
            {"category": "train", "metric_name": "lr", "step": 3, "value": 1e-4, "recorded_at": t0 + timedelta(seconds=4)},
        ],
    )
    assert inserted == 5

    # Different project / run is isolated
    store.insert_points(
        "p2",
        "run-1",
        [{"category": "train", "metric_name": "loss", "step": 1, "value": 99.0, "recorded_at": t0}],
    )
    assert store.query_metrics("p3", "run-1") == {}
    p2_data = store.query_metrics("p2", "run-1")
    assert len(p2_data["loss"]) == 1 and p2_data["loss"][0]["value"] == 99.0

    # Category filter
    eval_only = store.query_metrics("p1", "run-1", category="eval")
    assert list(eval_only.keys()) == ["loss"]
    assert len(eval_only["loss"]) == 1
    assert eval_only["loss"][0]["value"] == 2.1
    assert eval_only["loss"][0]["min"] == 2.1
    assert eval_only["loss"][0]["max"] == 2.1

    # Metric names + step range filter
    filtered = store.query_metrics(
        "p1", "run-1", metric_names=["loss", "lr"], category="train", min_step=2, max_step=3
    )
    assert sorted(filtered.keys()) == ["loss", "lr"]
    assert [p["step"] for p in filtered["loss"]] == [2]
    assert [p["step"] for p in filtered["lr"]] == [3]


@pytest.mark.parametrize("total_points,max_points", [(1000, 50), (250, 10), (105, 100)])
def test_memory_metric_store_deterministic_downsampling(total_points: int, max_points: int):
    store = MemoryTrainingMetricStore()
    t0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    points = [
        {
            "category": "train",
            "metric_name": "loss",
            "step": i,
            "epoch": i / 100.0,
            "value": float(total_points - i),
            "recorded_at": t0 + timedelta(seconds=i),
        }
        for i in range(total_points)
    ]
    store.insert_points("p1", "run-ds", points)

    res1 = store.query_metrics("p1", "run-ds", max_points=max_points)
    res2 = store.query_metrics("p1", "run-ds", max_points=max_points)
    assert res1 == res2  # deterministic

    series = res1["loss"]
    assert 1 <= len(series) <= max_points
    steps = [p["step"] for p in series]
    assert steps == sorted(steps)
    for bucket in series:
        assert bucket["min"] <= bucket["value"] <= bucket["max"]
        assert isinstance(bucket["recorded_at"], datetime)


def test_memory_metric_store_rejects_nan_and_inf():
    store = MemoryTrainingMetricStore()
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            store.insert_points("p1", "run-1", [{"metric_name": "loss", "step": 1, "value": bad}])


def test_memory_metric_store_thread_safety():
    store = MemoryTrainingMetricStore()

    def worker(worker_id: int) -> None:
        pts = [
            {"metric_name": "loss", "step": worker_id * 20 + i, "value": float(i)}
            for i in range(20)
        ]
        store.insert_points("p1", "run-mt", pts)
        store.query_metrics("p1", "run-mt", max_points=50)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))

    res = store.query_metrics("p1", "run-mt", max_points=500)
    assert len(res["loss"]) == 160
