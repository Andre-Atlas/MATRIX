import os
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from matrix_server.domain.spans import Media
from matrix_server.storage.db import Base, init_db, make_engine, make_session_factory
from matrix_server.storage.models import (
    BenchmarkRun,
    BenchmarkTaskResult,
    Dataset,
    DatasetBranch,
    DatasetCommit,
    DatasetCommitItem,
    DatasetItem,
    ModelVersion,
    RegisteredModel,
    SandboxRun,
    SandboxTaskTranscript,
)

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def session(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'sota_test.db'}")
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


# --- Domain Media Model Tests ----------------------------------------------


def test_media_domain_model_extended_kinds():
    kinds = ["image", "audio", "pdf", "bbox", "mask", "text_diff", "file"]
    for k in kinds:
        media = Media(kind=k, url=f"https://example.com/asset/{k}")
        assert media.kind == k
        assert media.url == f"https://example.com/asset/{k}"

    with pytest.raises(ValidationError):
        Media(kind="video", url="https://example.com/video.mp4")  # type: ignore[arg-type]


def test_media_domain_model_all_multimodal_fields():
    boxes = [{"x": 10, "y": 20, "w": 100, "h": 200, "label": "dog", "score": 0.95}]
    diff = {"before": "hello world", "after": "hello sota", "hunks": []}
    media = Media(
        kind="bbox",
        url="https://example.com/sample.jpg",
        mime="image/jpeg",
        artifact_id="art_123",
        sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        width=1920,
        height=1080,
        page=1,
        duration_ms=450.5,
        boxes=boxes,
        mask_uri="s3://bucket/masks/dog.png",
        diff=diff,
    )

    dumped = media.model_dump()
    assert dumped["kind"] == "bbox"
    assert dumped["boxes"] == boxes
    assert dumped["mask_uri"] == "s3://bucket/masks/dog.png"
    assert dumped["diff"] == diff
    assert dumped["width"] == 1920
    assert dumped["height"] == 1080
    assert dumped["duration_ms"] == 450.5

    rehydrated = Media.model_validate(dumped)
    assert rehydrated == media


# --- DatasetItem.media Tests -----------------------------------------------


def test_dataset_item_media_serialization_and_defaults(session):
    ds = Dataset(project_id="proj_1", name="multimodal_ds")
    session.add(ds)
    session.flush()

    item_empty = DatasetItem(
        dataset_id=ds.id,
        project_id="proj_1",
        input={"text": "hello"},
    )
    session.add(item_empty)

    media_data: list[dict[str, Any]] = [
        {
            "kind": "image",
            "url": "https://example.com/sample.png",
            "mime": "image/png",
            "width": 640,
            "height": 480,
        },
        {
            "kind": "bbox",
            "url": "https://example.com/sample.png",
            "boxes": [{"x": 10, "y": 20, "w": 50, "h": 60, "label": "cat"}],
        },
    ]
    item_with_media = DatasetItem(
        dataset_id=ds.id,
        project_id="proj_1",
        input={"text": "describe image"},
        media=media_data,
    )
    session.add(item_with_media)
    session.commit()
    session.expire_all()

    loaded_empty = session.get(DatasetItem, item_empty.id)
    assert loaded_empty is not None
    assert loaded_empty.media == []

    loaded_media = session.get(DatasetItem, item_with_media.id)
    assert loaded_media is not None
    assert len(loaded_media.media) == 2
    assert loaded_media.media[0]["kind"] == "image"
    assert loaded_media.media[1]["boxes"][0]["label"] == "cat"


# --- Zero-Copy Data Versioning Storage Tests -------------------------------


def test_dataset_branch_crud_unique_and_cascade(session):
    ds1 = Dataset(project_id="proj_1", name="ds_branch_test")
    ds2 = Dataset(project_id="proj_2", name="ds_branch_test")
    session.add_all([ds1, ds2])
    session.flush()

    branch_main = DatasetBranch(
        project_id="proj_1",
        dataset_id=ds1.id,
        name="main",
        head_commit_id=None,
        is_default=True,
        created_by="user_alice",
    )
    branch_dev = DatasetBranch(
        project_id="proj_1",
        dataset_id=ds1.id,
        name="dev",
        head_commit_id="commit_abc",
        is_default=False,
    )
    # Same branch name in different dataset must succeed
    branch_ds2_main = DatasetBranch(
        project_id="proj_2",
        dataset_id=ds2.id,
        name="main",
        head_commit_id=None,
        is_default=True,
    )
    session.add_all([branch_main, branch_dev, branch_ds2_main])
    session.commit()

    # Unique constraint on (dataset_id, name)
    dup_branch = DatasetBranch(
        project_id="proj_1",
        dataset_id=ds1.id,
        name="main",
    )
    session.add(dup_branch)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Verify relationship from Dataset
    ds_loaded = session.get(Dataset, ds1.id)
    assert ds_loaded is not None
    assert len(ds_loaded.branches) == 2

    # Cascade delete on dataset
    session.delete(ds_loaded)
    session.commit()

    branches = session.execute(
        select(DatasetBranch).where(DatasetBranch.dataset_id == ds1.id)
    ).scalars().all()
    assert len(branches) == 0

    # Dataset 2 branch remains untouched
    assert session.get(DatasetBranch, branch_ds2_main.id) is not None


def test_dataset_commit_and_commit_items_crud_and_cascade(session):
    ds = Dataset(project_id="proj_1", name="ds_commit_test")
    session.add(ds)
    session.flush()

    commit = DatasetCommit(
        project_id="proj_1",
        dataset_id=ds.id,
        parent_commit_id=None,
        second_parent_commit_id=None,
        tree_hash="a" * 64,
        message="Initial dataset commit",
        author="alice@example.com",
        item_count=2,
        stats={"added": 2, "deleted": 0, "modified": 0},
    )
    session.add(commit)
    session.flush()

    item1 = DatasetCommitItem(
        project_id="proj_1",
        commit_id=commit.id,
        item_key="row-0001",
        content_hash="b" * 64,
        input={"instruction": "translate to french", "input": "good morning"},
        expected_output="bonjour",
        context=["greeting"],
        media=[{"kind": "audio", "url": "https://example.com/greeting.mp3", "duration_ms": 1200}],
        metadata_={"quality": "gold"},
        artifact_refs=[{"sha256": "c" * 64, "path": "audio/greeting.mp3"}],
    )
    item2 = DatasetCommitItem(
        project_id="proj_1",
        commit_id=commit.id,
        item_key="row-0002",
        content_hash="d" * 64,
        input={"instruction": "translate to french", "input": "good evening"},
        expected_output="bonsoir",
    )
    session.add_all([item1, item2])
    session.commit()

    # Unique constraint on (commit_id, item_key)
    dup_item = DatasetCommitItem(
        project_id="proj_1",
        commit_id=commit.id,
        item_key="row-0001",
        content_hash="e" * 64,
        input={"q": "dup"},
    )
    session.add(dup_item)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Check retrieval and relationships
    c_loaded = session.get(DatasetCommit, commit.id)
    assert c_loaded is not None
    assert len(c_loaded.items) == 2
    assert c_loaded.items[0].media[0]["kind"] == "audio"
    assert c_loaded.items[0].metadata_ == {"quality": "gold"}

    # Cascade delete on commit
    session.delete(c_loaded)
    session.commit()

    items = session.execute(
        select(DatasetCommitItem).where(DatasetCommitItem.commit_id == commit.id)
    ).scalars().all()
    assert len(items) == 0


# --- Benchmarks Storage Tests ----------------------------------------------


def test_benchmark_run_and_task_results_crud_and_fks(session):
    ds = Dataset(project_id="proj_1", name="eval_benchmark_ds")
    reg_model = RegisteredModel(project_id="proj_1", name="deepseek-v3")
    session.add_all([ds, reg_model])
    session.flush()

    mv = ModelVersion(
        project_id="proj_1",
        model_id=reg_model.id,
        version=1,
    )
    session.add(mv)
    session.flush()

    run = BenchmarkRun(
        project_id="proj_1",
        name="mmlu-eval-run",
        suite="lm_eval",
        tasks=["mmlu_pro", "gsm8k"],
        runner_backend="local_sim",
        runner_config={"seed": 42},
        eval_mode="generate_until",
        num_fewshot=5,
        target_model="deepseek-v3",
        model_version_id=mv.id,
        dataset_id=ds.id,
        dataset_commit_id="commit_123",
        status="running",
        summary_metrics={"mmlu_pro/acc": 0.82},
    )
    session.add(run)
    session.flush()

    res1 = BenchmarkTaskResult(
        project_id="proj_1",
        benchmark_run_id=run.id,
        task_name="mmlu_pro",
        modality="text",
        eval_mode="generate_until",
        num_fewshot=5,
        sample_count=100,
        primary_metric="acc",
        primary_score=0.82,
        metrics={"acc": 0.82, "acc_norm": 0.84},
        latency_ms_p50=120.0,
        latency_ms_p95=250.0,
    )
    res2 = BenchmarkTaskResult(
        project_id="proj_1",
        benchmark_run_id=run.id,
        task_name="gsm8k",
        modality="text",
        eval_mode="generate_until",
        num_fewshot=8,
        sample_count=50,
        primary_metric="exact_match",
        primary_score=0.91,
        metrics={"exact_match": 0.91},
    )
    session.add_all([res1, res2])
    session.commit()

    # Unique constraint on (benchmark_run_id, task_name)
    dup_res = BenchmarkTaskResult(
        project_id="proj_1",
        benchmark_run_id=run.id,
        task_name="mmlu_pro",
        primary_metric="acc",
        primary_score=0.5,
    )
    session.add(dup_res)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Verify relationships
    loaded_run = session.get(BenchmarkRun, run.id)
    assert loaded_run is not None
    assert len(loaded_run.task_results) == 2

    # Check SET NULL on model_version_id and dataset_id
    session.delete(mv)
    session.delete(ds)
    session.commit()
    session.expire_all()

    loaded_run = session.get(BenchmarkRun, run.id)
    assert loaded_run is not None
    assert loaded_run.model_version_id is None
    assert loaded_run.dataset_id is None

    # Cascade delete on benchmark run
    session.delete(loaded_run)
    session.commit()

    results = session.execute(
        select(BenchmarkTaskResult).where(BenchmarkTaskResult.benchmark_run_id == run.id)
    ).scalars().all()
    assert len(results) == 0


# --- Inspect AI Sandboxes Storage Tests ------------------------------------


def test_sandbox_run_and_transcripts_crud_and_cascade(session):
    ds = Dataset(project_id="proj_1", name="inspect_sandbox_ds")
    session.add(ds)
    session.flush()

    srun = SandboxRun(
        project_id="proj_1",
        name="swe-bench-lite-eval",
        runner_backend="local_sandbox",
        dataset_id=ds.id,
        dataset_commit_id="commit_456",
        solver_config={"name": "react", "max_steps": 30},
        scorer_config={"name": "unit_test"},
        sandbox_policy={"network_mode": "none", "timeout_seconds": 300},
        status="completed",
        pass_rate=0.75,
        total_tasks=4,
        passed_tasks=3,
        total_tokens=15000,
        total_cost_usd=0.045,
    )
    session.add(srun)
    session.flush()

    steps_data = [
        {"step": 1, "tool_name": "bash", "tool_args": {"cmd": "ls"}, "stdout": "src/\n", "duration_ms": 12.0},
        {"step": 2, "tool_name": "pytest", "tool_args": {}, "stdout": "2 passed\n", "duration_ms": 350.0},
    ]
    verified_artifacts = [
        {"path": "patch.diff", "sha256": "f" * 64, "size_bytes": 1024}
    ]

    t1 = SandboxTaskTranscript(
        project_id="proj_1",
        sandbox_run_id=srun.id,
        task_key="django__django-12345",
        status="passed",
        score=1.0,
        scorer_details={"test_results": {"test_fix": "PASS"}},
        steps=steps_data,
        verified_artifacts=verified_artifacts,
        duration_ms=4500.0,
        input_tokens=8000,
        output_tokens=2500,
    )
    t2 = SandboxTaskTranscript(
        project_id="proj_1",
        sandbox_run_id=srun.id,
        task_key="django__django-12346",
        status="failed",
        score=0.0,
        error="Test assertion failed",
    )
    session.add_all([t1, t2])
    session.commit()

    # Unique constraint on (sandbox_run_id, task_key)
    dup_t = SandboxTaskTranscript(
        project_id="proj_1",
        sandbox_run_id=srun.id,
        task_key="django__django-12345",
        status="passed",
        score=1.0,
    )
    session.add(dup_t)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    loaded_srun = session.get(SandboxRun, srun.id)
    assert loaded_srun is not None
    assert len(loaded_srun.transcripts) == 2
    assert loaded_srun.transcripts[0].steps[0]["tool_name"] == "bash"
    assert loaded_srun.transcripts[0].verified_artifacts[0]["path"] == "patch.diff"

    # SET NULL on dataset_id
    session.delete(ds)
    session.commit()
    session.expire_all()

    loaded_srun = session.get(SandboxRun, srun.id)
    assert loaded_srun is not None
    assert loaded_srun.dataset_id is None

    # Cascade delete on SandboxRun
    session.delete(loaded_srun)
    session.commit()

    transcripts = session.execute(
        select(SandboxTaskTranscript).where(SandboxTaskTranscript.sandbox_run_id == srun.id)
    ).scalars().all()
    assert len(transcripts) == 0


def test_multitenant_project_isolation(session):
    # Ensure items across projects cannot collide or be mixed up
    ds_p1 = Dataset(project_id="proj_A", name="bench_ds")
    ds_p2 = Dataset(project_id="proj_B", name="bench_ds")
    session.add_all([ds_p1, ds_p2])
    session.flush()

    b_p1 = DatasetBranch(project_id="proj_A", dataset_id=ds_p1.id, name="feat")
    b_p2 = DatasetBranch(project_id="proj_B", dataset_id=ds_p2.id, name="feat")
    session.add_all([b_p1, b_p2])

    run_p1 = BenchmarkRun(project_id="proj_A", name="run1", suite="lm_eval", target_model="m")
    run_p2 = BenchmarkRun(project_id="proj_B", name="run1", suite="lm_eval", target_model="m")
    session.add_all([run_p1, run_p2])
    session.commit()

    # Querying by project_A returns only project_A's records
    p1_branches = session.execute(
        select(DatasetBranch).where(DatasetBranch.project_id == "proj_A")
    ).scalars().all()
    assert len(p1_branches) == 1
    assert p1_branches[0].dataset_id == ds_p1.id

    p2_runs = session.execute(
        select(BenchmarkRun).where(BenchmarkRun.project_id == "proj_B")
    ).scalars().all()
    assert len(p2_runs) == 1
    assert p2_runs[0].id == run_p2.id
