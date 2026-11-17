"""Benchmarks API: foundational & multimodal evaluations (LM-Eval & VLMEvalKit)."""

from __future__ import annotations

import json
import math
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.auth import AuthContext, require_role
from matrix_server.benchmarks.catalog import VALID_SUITES, list_catalog, validate_tasks
from matrix_server.benchmarks.runner import run_benchmark_job, validate_runner_url
from matrix_server.storage import jobs
from matrix_server.storage.db import as_utc, get_db, utcnow
from matrix_server.storage.models import (
    BenchmarkRun,
    BenchmarkTaskResult,
    Dataset,
    DatasetCommit,
    Job,
    ModelVersion,
)

DbSession = Annotated[Session, Depends(get_db)]
ViewerAuth = Annotated[AuthContext, Depends(require_role("viewer"))]
MemberAuth = Annotated[AuthContext, Depends(require_role("member"))]

router = APIRouter(prefix="/api/v1/benchmarks", tags=["benchmarks"])

VALID_BACKENDS: set[str] = {"local_sim", "http", "vllm"}
VALID_EVAL_MODES: set[str] = {"generate_until", "loglikelihood", "multimodal"}


def _iso_utc(value: Any) -> str | None:
    value = as_utc(value)
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def _reject_non_finite(obj: Any) -> None:
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise HTTPException(status_code=422, detail="body must not contain NaN or Infinity")
    elif isinstance(obj, dict):
        for v in obj.values():
            _reject_non_finite(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _reject_non_finite(v)


def parse_body(model: type[BaseModel], payload: Any) -> Any:
    """Validate body schema and reject NaN / Infinity without echoing sensitive input."""
    _reject_non_finite(payload)
    try:
        body = model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_input=False, include_context=False, include_url=False),
        ) from exc
    dumped = body.model_dump(mode="python")
    _reject_non_finite(dumped)
    try:
        json.dumps(dumped, allow_nan=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="body must not contain NaN or Infinity") from exc
    return body


class BenchmarkRunCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    suite: str
    tasks: list[str] = Field(min_length=1)
    runner_backend: str = Field(default="local_sim")
    runner_config: dict[str, Any] = Field(default_factory=dict)
    eval_mode: str = Field(default="generate_until")
    num_fewshot: int = Field(default=0, ge=0, le=100)
    target_model: str = Field(min_length=1, max_length=200)
    model_version_id: str | None = None
    dataset_id: str | None = None
    dataset_commit_id: str | None = None
    sync_exec: bool = False


def _task_result_out(tr: BenchmarkTaskResult) -> dict[str, Any]:
    return {
        "id": tr.id,
        "project_id": tr.project_id,
        "benchmark_run_id": tr.benchmark_run_id,
        "task_name": tr.task_name,
        "modality": tr.modality,
        "eval_mode": tr.eval_mode,
        "num_fewshot": tr.num_fewshot,
        "sample_count": tr.sample_count,
        "primary_metric": tr.primary_metric,
        "primary_score": tr.primary_score,
        "metrics": tr.metrics,
        "latency_ms_p50": tr.latency_ms_p50,
        "latency_ms_p95": tr.latency_ms_p95,
        "created_at": _iso_utc(tr.created_at),
    }


def _run_out(run: BenchmarkRun, task_results: list[BenchmarkTaskResult] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": run.id,
        "project_id": run.project_id,
        "name": run.name,
        "suite": run.suite,
        "tasks": list(run.tasks),
        "runner_backend": run.runner_backend,
        "runner_config": run.runner_config,
        "eval_mode": run.eval_mode,
        "num_fewshot": run.num_fewshot,
        "target_model": run.target_model,
        "model_version_id": run.model_version_id,
        "dataset_id": run.dataset_id,
        "dataset_commit_id": run.dataset_commit_id,
        "status": run.status,
        "summary_metrics": run.summary_metrics,
        "job_id": run.job_id,
        "error": run.error,
        "created_at": _iso_utc(run.created_at),
        "started_at": _iso_utc(run.started_at),
        "finished_at": _iso_utc(run.finished_at),
    }
    if task_results is not None:
        data["task_results"] = [_task_result_out(tr) for tr in task_results]
    return data


# --- Routes -----------------------------------------------------------------


@router.get("/catalog")
def get_catalog(
    ctx: ViewerAuth,
    suite: str | None = Query(default=None),
) -> dict[str, Any]:
    """Retrieve catalog of supported benchmark tasks (filtered optionally by suite)."""
    if suite is not None and suite not in VALID_SUITES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid suite '{suite}'. Must be one of: {sorted(VALID_SUITES)}",
        )
    return {"tasks": list_catalog(suite)}


@router.post("/runs", status_code=status.HTTP_201_CREATED)
def create_run(
    raw_body: Annotated[Any, Body()],
    db: DbSession,
    ctx: MemberAuth,
    sync_exec: bool | None = Query(default=None),
) -> Response:
    """Create a new benchmark run and dispatch for execution."""
    body = parse_body(BenchmarkRunCreate, raw_body)
    is_sync = body.sync_exec if sync_exec is None else sync_exec

    if body.suite not in VALID_SUITES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid benchmark suite '{body.suite}'. Must be one of: {sorted(VALID_SUITES)}",
        )

    if body.runner_backend not in VALID_BACKENDS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid runner_backend '{body.runner_backend}'. Must be one of: {sorted(VALID_BACKENDS)}",
        )

    if body.eval_mode not in VALID_EVAL_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid eval_mode '{body.eval_mode}'. Must be one of: {sorted(VALID_EVAL_MODES)}",
        )

    try:
        validate_tasks(body.suite, body.tasks)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Anti-SSRF validation for external runners
    if body.runner_backend in ("http", "vllm"):
        endpoint_url = body.runner_config.get("endpoint_url")
        if not endpoint_url:
            raise HTTPException(
                status_code=422,
                detail="runner_config.endpoint_url is required when runner_backend is 'http' or 'vllm'",
            )
        try:
            validate_runner_url(endpoint_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Anti-IDOR validation on tenant resources
    if body.model_version_id is not None:
        mv = db.scalars(
            select(ModelVersion).where(
                ModelVersion.id == body.model_version_id,
                ModelVersion.project_id == ctx.project_id,
            )
        ).first()
        if mv is None:
            raise HTTPException(status_code=404, detail="model version not found")

    if body.dataset_id is not None:
        ds = db.scalars(
            select(Dataset).where(
                Dataset.id == body.dataset_id,
                Dataset.project_id == ctx.project_id,
            )
        ).first()
        if ds is None:
            raise HTTPException(status_code=404, detail="dataset not found")

    if body.dataset_commit_id is not None:
        dc = db.scalars(
            select(DatasetCommit).where(
                DatasetCommit.id == body.dataset_commit_id,
                DatasetCommit.project_id == ctx.project_id,
            )
        ).first()
        if dc is None:
            raise HTTPException(status_code=404, detail="dataset commit not found")

    run = BenchmarkRun(
        project_id=ctx.project_id,
        name=body.name,
        suite=body.suite,
        tasks=body.tasks,
        runner_backend=body.runner_backend,
        runner_config=body.runner_config,
        eval_mode=body.eval_mode,
        num_fewshot=body.num_fewshot,
        target_model=body.target_model,
        model_version_id=body.model_version_id,
        dataset_id=body.dataset_id,
        dataset_commit_id=body.dataset_commit_id,
        status="queued",
        summary_metrics={},
    )
    db.add(run)
    db.flush()

    if is_sync:
        db.commit()
        run_benchmark_job(db, run.id)
        db.expire_all()
        run = db.get(BenchmarkRun, run.id)  # type: ignore[assignment]
        task_results = db.scalars(
            select(BenchmarkTaskResult)
            .where(BenchmarkTaskResult.benchmark_run_id == run.id)
            .order_by(BenchmarkTaskResult.task_name)
        ).all()
        return Response(
            content=json.dumps(_run_out(run, list(task_results))),
            status_code=201,
            media_type="application/json",
        )

    job = jobs.enqueue(db, ctx.project_id, "benchmark.run", {"benchmark_run_id": run.id})
    run.job_id = job.id
    db.commit()
    db.refresh(run)

    return Response(
        content=json.dumps(_run_out(run, [])),
        status_code=201,
        media_type="application/json",
    )


@router.get("/runs")
def list_runs(
    db: DbSession,
    ctx: ViewerAuth,
    suite: str | None = Query(default=None),
    status: str | None = Query(default=None),
    model_version_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List benchmark runs in the current project with optional filtering."""
    query = select(BenchmarkRun).where(BenchmarkRun.project_id == ctx.project_id)
    if suite is not None:
        query = query.where(BenchmarkRun.suite == suite)
    if status is not None:
        query = query.where(BenchmarkRun.status == status)
    if model_version_id is not None:
        query = query.where(BenchmarkRun.model_version_id == model_version_id)

    query = query.order_by(BenchmarkRun.created_at.desc()).limit(limit)
    runs = db.scalars(query).all()
    return {"runs": [_run_out(r) for r in runs]}


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    db: DbSession,
    ctx: ViewerAuth,
) -> dict[str, Any]:
    """Get benchmark run details including per-task results."""
    run = db.scalars(
        select(BenchmarkRun).where(
            BenchmarkRun.id == run_id,
            BenchmarkRun.project_id == ctx.project_id,
        )
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="benchmark run not found")

    task_results = db.scalars(
        select(BenchmarkTaskResult)
        .where(BenchmarkTaskResult.benchmark_run_id == run.id)
        .order_by(BenchmarkTaskResult.task_name)
    ).all()

    return _run_out(run, list(task_results))


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    db: DbSession,
    ctx: MemberAuth,
) -> dict[str, Any]:
    """Cancel a queued or running benchmark run."""
    run = db.scalars(
        select(BenchmarkRun).where(
            BenchmarkRun.id == run_id,
            BenchmarkRun.project_id == ctx.project_id,
        )
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="benchmark run not found")

    if run.status in ("completed", "failed", "cancelled"):
        task_results = db.scalars(
            select(BenchmarkTaskResult)
            .where(BenchmarkTaskResult.benchmark_run_id == run.id)
            .order_by(BenchmarkTaskResult.task_name)
        ).all()
        return _run_out(run, list(task_results))

    run.status = "cancelled"
    run.finished_at = utcnow()

    if run.job_id is not None:
        job = db.get(Job, run.job_id)
        if job is not None and job.status == "queued":
            job.status = "cancelled"

    db.commit()
    db.refresh(run)

    task_results = db.scalars(
        select(BenchmarkTaskResult)
        .where(BenchmarkTaskResult.benchmark_run_id == run.id)
        .order_by(BenchmarkTaskResult.task_name)
    ).all()

    return _run_out(run, list(task_results))
