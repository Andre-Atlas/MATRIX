"""MLOps Studio API: Training Runs, Metrics, Sweeps, Artifacts, Lineage & Model/LoRA Registry."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server.auth import AuthContext, require_role
from matrix_server.mlops.artifact_store import (
    ArtifactCorruptionError,
    ArtifactSha256MismatchError,
    ArtifactTooLargeError,
    LocalFSArtifactStore,
    validate_external_uri,
    validate_sha256,
)
from matrix_server.mlops.sweeps import (
    InvalidSearchSpaceError,
    SweepExhaustedError,
    build_sweep_analysis,
    evaluate_early_stopping,
    suggest_next_params,
    validate_search_space,
)
from matrix_server.storage.db import as_utc, get_db, utcnow
from matrix_server.storage.jobs import enqueue
from matrix_server.storage.models import (
    Artifact,
    ArtifactLineage,
    Dataset,
    Experiment,
    MLOpsSweep,
    ModelAlias,
    ModelVersion,
    RegisteredModel,
    SafetyScan,
    TrainingRun,
)

router = APIRouter(prefix="/api/v1/mlops", tags=["mlops"])

DbSession = Annotated[Session, Depends(get_db)]
ViewerAuth = Annotated[AuthContext, Depends(require_role("viewer"))]
MemberAuth = Annotated[AuthContext, Depends(require_role("member"))]
AdminAuth = Annotated[AuthContext, Depends(require_role("admin"))]

MAX_CREATE_ATTEMPTS = 3
MAX_PAGE = 500
DEFAULT_PAGE = 50
MAX_METRIC_POINTS_BATCH = 1000

VALID_RUN_STATUSES = {"queued", "running", "completed", "failed", "cancelled", "stopped"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "stopped"}
VALID_MODEL_STAGES = {"none", "staging", "production", "archived"}
ADMIN_ONLY_ALIASES = {"production", "champion"}


# ---------------------------------------------------------------------------
# Helpers: validation, serialization, cursor pagination, anti-IDOR
# ---------------------------------------------------------------------------


def _iso_utc(value: Any) -> str | None:
    dt = as_utc(value)
    return None if dt is None else dt.isoformat().replace("+00:00", "Z")


def _reject_non_finite(obj: Any) -> None:
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise HTTPException(status_code=422, detail="body must not contain NaN or Infinity")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _reject_non_finite(k)
            _reject_non_finite(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _reject_non_finite(item)


def _parse_body(model: type[BaseModel], payload: Any) -> Any:
    """Validate payload without echoing potentially sensitive/NaN inputs on 422."""
    _reject_non_finite(payload)
    try:
        body = model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_input=False, include_context=False, include_url=False),
        ) from exc
    dumped = body.model_dump(mode="json")
    _reject_non_finite(dumped)
    try:
        json.dumps(dumped, allow_nan=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="body must not contain NaN or Infinity") from exc
    return body


def _encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = f"{_iso_utc(created_at)}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        stamp, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        ts = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    return ts, row_id


def _same_tz(db: Session, ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if db.get_bind().dialect.name == "sqlite" else ts


def _require_admin(ctx: AuthContext) -> None:
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail=f"role '{ctx.role}' insufficient; 'admin' required")


def _get_dataset_or_404(db: Session, project_id: str, dataset_id: str) -> Dataset:
    row = db.scalars(select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return row


def _get_sweep_or_404(db: Session, project_id: str, sweep_id: str) -> MLOpsSweep:
    row = db.scalars(select(MLOpsSweep).where(MLOpsSweep.id == sweep_id, MLOpsSweep.project_id == project_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="sweep not found")
    return row


def _get_run_or_404(db: Session, project_id: str, run_id: str) -> TrainingRun:
    row = db.scalars(select(TrainingRun).where(TrainingRun.id == run_id, TrainingRun.project_id == project_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="training run not found")
    return row


def _get_artifact_or_404(db: Session, project_id: str, artifact_id: str) -> Artifact:
    row = db.scalars(select(Artifact).where(Artifact.id == artifact_id, Artifact.project_id == project_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return row


def _get_experiment_or_404(db: Session, project_id: str, experiment_id: str) -> Experiment:
    row = db.scalars(
        select(Experiment).where(Experiment.id == experiment_id, Experiment.project_id == project_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return row


def _get_safety_scan_or_404(db: Session, project_id: str, scan_id: str) -> SafetyScan:
    row = db.scalars(select(SafetyScan).where(SafetyScan.id == scan_id, SafetyScan.project_id == project_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="safety scan not found")
    return row


def _get_model_or_404(db: Session, project_id: str, model_id: str) -> RegisteredModel:
    row = db.scalars(
        select(RegisteredModel).where(RegisteredModel.id == model_id, RegisteredModel.project_id == project_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="registered model not found")
    return row


def _get_model_version_or_404(db: Session, project_id: str, model_id: str, version: int) -> ModelVersion:
    row = db.scalars(
        select(ModelVersion).where(
            ModelVersion.project_id == project_id,
            ModelVersion.model_id == model_id,
            ModelVersion.version == version,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="model version not found")
    return row


def _add_lineage_edge(
    db: Session,
    project_id: str,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    relation: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    existing = db.scalar(
        select(ArtifactLineage.id).where(
            ArtifactLineage.project_id == project_id,
            ArtifactLineage.source_type == source_type,
            ArtifactLineage.source_id == source_id,
            ArtifactLineage.target_type == target_type,
            ArtifactLineage.target_id == target_id,
            ArtifactLineage.relation == relation,
        )
    )
    if existing is not None:
        return
    try:
        with db.begin_nested():
            db.add(
                ArtifactLineage(
                    project_id=project_id,
                    source_type=source_type,
                    source_id=source_id,
                    target_type=target_type,
                    target_id=target_id,
                    relation=relation,
                    metadata_=metadata or {},
                )
            )
            db.flush()
    except IntegrityError:
        pass


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _run_out(run: TrainingRun) -> dict[str, Any]:
    summary = dict(run.summary_metrics or {})
    last_heartbeat_at = summary.get("_last_heartbeat_at")
    latest_metrics = {k: v for k, v in summary.items() if not k.startswith("_")}
    return {
        "id": run.id,
        "project_id": run.project_id,
        "name": run.name,
        "run_type": run.run_type,
        "status": run.status,
        "base_model": run.base_model,
        "dataset_id": run.dataset_id,
        "sweep_id": run.sweep_id,
        "parent_run_id": run.parent_run_id,
        "hyperparams": dict(run.hyperparams or {}),
        "summary_metrics": latest_metrics,
        "latest_metrics": latest_metrics,
        "hardware_info": run.hardware_info,
        "classic_eval": run.classic_eval,
        "tags": list(run.tags or []),
        "total_steps": run.total_steps,
        "error": run.error,
        "last_heartbeat_at": last_heartbeat_at,
        "created_at": _iso_utc(run.created_at),
        "started_at": _iso_utc(run.started_at),
        "finished_at": _iso_utc(run.finished_at),
    }


def _sweep_out(sweep: MLOpsSweep) -> dict[str, Any]:
    return {
        "id": sweep.id,
        "project_id": sweep.project_id,
        "name": sweep.name,
        "description": sweep.description,
        "method": sweep.method,
        "metric_name": sweep.metric_name,
        "metric_goal": sweep.metric_goal,
        "search_space": dict(sweep.search_space or {}),
        "early_stopping": sweep.early_stopping,
        "max_trials": sweep.max_trials,
        "status": sweep.status,
        "best_run_id": sweep.best_run_id,
        "best_metric_value": sweep.best_metric_value,
        "created_at": _iso_utc(sweep.created_at),
        "stopped_at": _iso_utc(sweep.stopped_at),
    }


def _artifact_out(art: Artifact) -> dict[str, Any]:
    return {
        "id": art.id,
        "project_id": art.project_id,
        "run_id": art.run_id,
        "name": art.name,
        "kind": art.kind,
        "version": art.version,
        "storage_backend": art.storage_backend,
        "storage_uri": art.storage_uri,
        "sha256": art.sha256,
        "size_bytes": art.size_bytes,
        "content_type": art.content_type,
        "status": art.status,
        "metadata": dict(art.metadata_ or {}),
        "created_at": _iso_utc(art.created_at),
        "committed_at": _iso_utc(art.committed_at),
    }


def _model_out(model: RegisteredModel) -> dict[str, Any]:
    return {
        "id": model.id,
        "project_id": model.project_id,
        "name": model.name,
        "description": model.description,
        "model_type": model.model_type,
        "base_model": model.base_model,
        "tags": list(model.tags or []),
        "created_at": _iso_utc(model.created_at),
        "updated_at": _iso_utc(model.updated_at),
    }


def _version_out(mv: ModelVersion) -> dict[str, Any]:
    return {
        "id": mv.id,
        "project_id": mv.project_id,
        "model_id": mv.model_id,
        "version": mv.version,
        "artifact_id": mv.artifact_id,
        "run_id": mv.run_id,
        "eval_experiment_id": mv.eval_experiment_id,
        "safety_scan_id": mv.safety_scan_id,
        "stage": mv.stage,
        "lora_config": mv.lora_config,
        "signature": mv.signature,
        "metrics_snapshot": dict(mv.metrics_snapshot or {}),
        "model_card": mv.model_card,
        "created_by": mv.created_by,
        "created_at": _iso_utc(mv.created_at),
    }


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class RunCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, populate_by_name=True, extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    run_type: str = Field(default="sft", min_length=1, max_length=32)
    status: str = Field(default="running", min_length=1, max_length=16)
    base_model: str | None = Field(default=None, max_length=200)
    dataset_id: str | None = Field(default=None, max_length=32)
    sweep_id: str | None = Field(default=None, max_length=32)
    parent_run_id: str | None = Field(default=None, max_length=32)
    hyperparams: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("hyperparams", "hyperparameters"),
    )
    hardware_info: dict[str, Any] | None = None
    classic_eval: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list, max_length=50)


class RunUpdate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    status: str | None = Field(default=None, min_length=1, max_length=16)
    total_steps: int | None = Field(default=None, ge=0)
    hardware_info: dict[str, Any] | None = None
    classic_eval: dict[str, Any] | None = None
    summary_metrics: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=10_000)
    finished_at: datetime | None = None


class MetricPointIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    category: str = Field(default="train", min_length=1, max_length=64)
    metric_name: str = Field(min_length=1, max_length=200)
    step: int = Field(ge=0)
    epoch: float | None = None
    value: float
    recorded_at: datetime | str | None = None


class MetricsBatchIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    points: list[MetricPointIn] = Field(min_length=1, max_length=MAX_METRIC_POINTS_BATCH)


class SweepCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    method: Literal["grid", "random", "bayes"] = "random"
    metric_name: str = Field(min_length=1, max_length=200)
    metric_goal: Literal["minimize", "maximize"] = "minimize"
    search_space: dict[str, Any]
    early_stopping: dict[str, Any] | None = None
    max_trials: int | None = Field(default=None, ge=1)


class SweepNextTrialIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    run_type: str = Field(default="sft", min_length=1, max_length=32)
    base_model: str | None = Field(default=None, max_length=200)
    dataset_id: str | None = Field(default=None, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=50)


class PresignUploadIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, populate_by_name=True, extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="checkpoint", min_length=1, max_length=32)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    content_type: str = Field(default="application/octet-stream", min_length=1, max_length=200)
    run_id: str | None = Field(
        default=None,
        max_length=32,
        validation_alias=AliasChoices("run_id", "source_run_id"),
    )
    external_uri: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactCommitIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    source_run_id: str | None = Field(default=None, max_length=32)
    size_bytes: int | None = Field(default=None, ge=0)


class ModelCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    model_type: str = Field(default="llm", min_length=1, max_length=32)
    base_model: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=50)


class ModelVersionCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    artifact_id: str | None = Field(default=None, max_length=32)
    run_id: str | None = Field(default=None, max_length=32)
    eval_experiment_id: str | None = Field(default=None, max_length=32)
    safety_scan_id: str | None = Field(default=None, max_length=32)
    stage: Literal["none", "staging", "production", "archived"] = "none"
    lora_config: dict[str, Any] | None = None
    signature: dict[str, Any] | None = None
    metrics_snapshot: dict[str, Any] = Field(default_factory=dict)
    model_card: str | None = Field(default=None, max_length=100_000)


class ModelStagePatch(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    stage: Literal["none", "staging", "production", "archived"]


class ModelAliasIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    alias: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)


class ModelEvaluateIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    dataset_id: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, max_length=200)
    metrics: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


# ---------------------------------------------------------------------------
# Runs & Metrics
# ---------------------------------------------------------------------------


@router.post("/runs", status_code=201)
def create_run(
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: RunCreate = _parse_body(RunCreate, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    if body.status not in VALID_RUN_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid status '{body.status}'")

    if body.dataset_id is not None:
        _get_dataset_or_404(db, ctx.project_id, body.dataset_id)
    if body.sweep_id is not None:
        _get_sweep_or_404(db, ctx.project_id, body.sweep_id)
    if body.parent_run_id is not None:
        _get_run_or_404(db, ctx.project_id, body.parent_run_id)

    now = utcnow()
    run = TrainingRun(
        project_id=ctx.project_id,
        name=name,
        run_type=body.run_type,
        status=body.status,
        base_model=body.base_model,
        dataset_id=body.dataset_id,
        sweep_id=body.sweep_id,
        parent_run_id=body.parent_run_id,
        hyperparams=body.hyperparams,
        hardware_info=body.hardware_info,
        classic_eval=body.classic_eval,
        tags=body.tags,
        started_at=now if body.status == "running" else None,
        finished_at=now if body.status in TERMINAL_RUN_STATUSES else None,
    )
    db.add(run)
    db.flush()

    if body.dataset_id is not None:
        _add_lineage_edge(
            db,
            ctx.project_id,
            "dataset",
            body.dataset_id,
            "run",
            run.id,
            "trained_on",
        )
    if body.parent_run_id is not None:
        _add_lineage_edge(
            db,
            ctx.project_id,
            "run",
            body.parent_run_id,
            "run",
            run.id,
            "forked_from",
        )

    return _run_out(run)


@router.get("/runs")
def list_runs(
    db: DbSession,
    ctx: ViewerAuth,
    status: str | None = None,
    sweep_id: str | None = None,
    run_type: str | None = None,
    base_model: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
    cursor: str | None = None,
) -> dict[str, Any]:
    stmt = select(TrainingRun).where(TrainingRun.project_id == ctx.project_id)
    if status is not None:
        stmt = stmt.where(TrainingRun.status == status)
    if sweep_id is not None:
        stmt = stmt.where(TrainingRun.sweep_id == sweep_id)
    if run_type is not None:
        stmt = stmt.where(TrainingRun.run_type == run_type)
    if base_model is not None:
        stmt = stmt.where(TrainingRun.base_model == base_model)
    if cursor is not None:
        ts, row_id = _decode_cursor(cursor)
        ts = _same_tz(db, ts)
        stmt = stmt.where(
            or_(
                TrainingRun.created_at < ts,
                and_(TrainingRun.created_at == ts, TrainingRun.id < row_id),
            )
        )
    rows = list(db.scalars(stmt.order_by(TrainingRun.created_at.desc(), TrainingRun.id.desc()).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id)
    return {"items": [_run_out(r) for r in rows], "next_cursor": next_cursor}


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: DbSession, ctx: ViewerAuth) -> dict[str, Any]:
    run = _get_run_or_404(db, ctx.project_id, run_id)
    artifacts = list(
        db.scalars(
            select(Artifact)
            .where(Artifact.project_id == ctx.project_id, Artifact.run_id == run.id)
            .order_by(Artifact.created_at.desc())
        )
    )
    out = _run_out(run)
    out["artifacts"] = [_artifact_out(a) for a in artifacts]
    return out


@router.patch("/runs/{run_id}")
def update_run(
    run_id: str,
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    run = _get_run_or_404(db, ctx.project_id, run_id)
    body: RunUpdate = _parse_body(RunUpdate, payload)

    if body.status is not None:
        if body.status not in VALID_RUN_STATUSES:
            raise HTTPException(status_code=422, detail=f"invalid status '{body.status}'")
        run.status = body.status
        if body.status == "running" and run.started_at is None:
            run.started_at = utcnow()
        if body.status in TERMINAL_RUN_STATUSES and run.finished_at is None and body.finished_at is None:
            run.finished_at = utcnow()
    if body.total_steps is not None:
        run.total_steps = body.total_steps
    if body.hardware_info is not None:
        run.hardware_info = body.hardware_info
    if body.classic_eval is not None:
        run.classic_eval = body.classic_eval
    if body.summary_metrics is not None:
        merged = dict(run.summary_metrics or {})
        merged.update(body.summary_metrics)
        run.summary_metrics = merged
    if body.error is not None:
        run.error = body.error
    if body.finished_at is not None:
        run.finished_at = body.finished_at

    db.flush()
    return _run_out(run)


@router.post("/runs/{run_id}/metrics", status_code=202)
def ingest_run_metrics(
    run_id: str,
    request: Request,
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    run = _get_run_or_404(db, ctx.project_id, run_id)
    body: MetricsBatchIn = _parse_body(MetricsBatchIn, payload)

    point_dicts = [
        {
            "category": p.category,
            "metric_name": p.metric_name,
            "step": p.step,
            "epoch": p.epoch,
            "value": p.value,
            "recorded_at": p.recorded_at,
        }
        for p in body.points
    ]

    metric_store = request.app.state.training_metric_store
    accepted = metric_store.insert_points(ctx.project_id, run.id, point_dicts)

    # Update latest_metrics, total_steps, heartbeat
    summary = dict(run.summary_metrics or {})
    latest_step_by_metric: dict[str, tuple[int, float]] = {}
    max_step = run.total_steps or 0
    for p in body.points:
        max_step = max(max_step, p.step)
        prev = latest_step_by_metric.get(p.metric_name)
        if prev is None or p.step >= prev[0]:
            latest_step_by_metric[p.metric_name] = (p.step, p.value)
    for mname, (_, val) in latest_step_by_metric.items():
        summary[mname] = val
    summary["_last_heartbeat_at"] = _iso_utc(utcnow())
    run.summary_metrics = summary
    run.total_steps = max_step

    should_stop = False
    stop_reason: str | None = None

    if run.sweep_id is not None:
        sweep = db.scalars(
            select(MLOpsSweep).where(MLOpsSweep.id == run.sweep_id, MLOpsSweep.project_id == ctx.project_id)
        ).first()
        if sweep is not None:
            if sweep.status in {"cancelled", "stopped", "completed"}:
                should_stop = True
                stop_reason = f"Sweep is {sweep.status}"
            elif sweep.metric_name in latest_step_by_metric:
                cur_step, cur_val = latest_step_by_metric[sweep.metric_name]
                is_better = (
                    sweep.best_metric_value is None
                    or (sweep.metric_goal == "minimize" and cur_val < sweep.best_metric_value)
                    or (sweep.metric_goal == "maximize" and cur_val > sweep.best_metric_value)
                )
                if is_better:
                    sweep.best_metric_value = cur_val
                    sweep.best_run_id = run.id

                es_cfg = sweep.early_stopping if isinstance(sweep.early_stopping, dict) else None
                strategy = es_cfg.get("strategy") if es_cfg else None
                if strategy and strategy != "none":
                    peer_runs = list(
                        db.scalars(
                            select(TrainingRun).where(
                                TrainingRun.project_id == ctx.project_id,
                                TrainingRun.sweep_id == sweep.id,
                                TrainingRun.id != run.id,
                            )
                        )
                    )
                    peer_series: list[list[dict[str, Any]]] = []
                    for peer in peer_runs:
                        queried = metric_store.query_metrics(
                            ctx.project_id,
                            peer.id,
                            metric_names=[sweep.metric_name],
                            max_points=5000,
                        )
                        pts = queried.get(sweep.metric_name, [])
                        if pts:
                            peer_series.append(pts)
                    decision = evaluate_early_stopping(
                        strategy=strategy,
                        metric_goal=sweep.metric_goal,
                        early_stopping_cfg=es_cfg,
                        current_step=cur_step,
                        current_value=cur_val,
                        peer_runs_metrics=peer_series,
                    )
                    if decision.should_stop:
                        should_stop = True
                        stop_reason = decision.reason

    db.flush()
    return {"accepted": accepted, "should_stop": should_stop, "stop_reason": stop_reason}


@router.get("/runs/{run_id}/metrics")
def get_run_metrics(
    run_id: str,
    request: Request,
    db: DbSession,
    ctx: ViewerAuth,
    metric_names: Annotated[list[str] | None, Query()] = None,
    category: str | None = None,
    min_step: int | None = None,
    max_step: int | None = None,
    max_points: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> dict[str, Any]:
    _get_run_or_404(db, ctx.project_id, run_id)
    parsed_names: list[str] | None = None
    if metric_names is not None:
        parsed_names = [m.strip() for item in metric_names for m in item.split(",") if m.strip()]
    series = request.app.state.training_metric_store.query_metrics(
        ctx.project_id,
        run_id,
        metric_names=parsed_names,
        category=category,
        min_step=min_step,
        max_step=max_step,
        max_points=max_points,
    )
    formatted: dict[str, list[dict[str, Any]]] = {}
    for mname, pts in series.items():
        formatted[mname] = [
            {
                "step": p["step"],
                "value": p["value"],
                "min": p["min"],
                "max": p["max"],
                "epoch": p["epoch"],
                "recorded_at": _iso_utc(p["recorded_at"]),
            }
            for p in pts
        ]
    return {"run_id": run_id, "series": formatted}


# ---------------------------------------------------------------------------
# Lineage Graph Helpers & Endpoints
# ---------------------------------------------------------------------------


def _build_lineage_graph(
    db: Session,
    project_id: str,
    seed_nodes: list[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Traverse ArtifactLineage + direct structural FKs in `project_id` from `seed_nodes`."""
    all_edges = list(
        db.scalars(select(ArtifactLineage).where(ArtifactLineage.project_id == project_id))
    )

    edge_map: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    adj: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def _register_edge(
        src_type: str,
        src_id: str,
        tgt_type: str,
        tgt_id: str,
        relation: str,
        edge_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = (src_type, src_id, tgt_type, tgt_id, relation)
        if key not in edge_map:
            edge_map[key] = {
                "id": edge_id or f"{src_type}:{src_id}->{tgt_type}:{tgt_id}:{relation}",
                "source": f"{src_type}:{src_id}",
                "target": f"{tgt_type}:{tgt_id}",
                "source_type": src_type,
                "source_id": src_id,
                "target_type": tgt_type,
                "target_id": tgt_id,
                "relation": relation,
                "metadata": metadata or {},
            }
            adj.setdefault((src_type, src_id), []).append((tgt_type, tgt_id))
            adj.setdefault((tgt_type, tgt_id), []).append((src_type, src_id))

    for e in all_edges:
        _register_edge(
            e.source_type,
            e.source_id,
            e.target_type,
            e.target_id,
            e.relation,
            edge_id=e.id,
            metadata=dict(e.metadata_ or {}),
        )

    # Also ensure structural FKs are represented even if created out-of-band
    for run in db.scalars(select(TrainingRun).where(TrainingRun.project_id == project_id)):
        if run.dataset_id:
            _register_edge("dataset", run.dataset_id, "run", run.id, "trained_on")
        if run.parent_run_id:
            _register_edge("run", run.parent_run_id, "run", run.id, "forked_from")
    for art in db.scalars(select(Artifact).where(Artifact.project_id == project_id)):
        if art.run_id:
            _register_edge("run", art.run_id, "artifact", art.id, "produced")
    for mv in db.scalars(select(ModelVersion).where(ModelVersion.project_id == project_id)):
        if mv.artifact_id:
            _register_edge("artifact", mv.artifact_id, "model_version", mv.id, "packaged_as")
        if mv.run_id:
            _register_edge("run", mv.run_id, "model_version", mv.id, "registered_as")
        if mv.eval_experiment_id:
            _register_edge("model_version", mv.id, "experiment", mv.eval_experiment_id, "evaluated_by")
        if mv.safety_scan_id:
            _register_edge("model_version", mv.id, "safety_scan", mv.safety_scan_id, "audited_by")

    visited: set[tuple[str, str]] = set(seed_nodes)
    queue: list[tuple[str, str]] = list(seed_nodes)
    while queue:
        cur = queue.pop(0)
        for nxt in adj.get(cur, []):
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)

    # Enrich visited nodes
    datasets_by_id = {
        d.id: d
        for d in db.scalars(select(Dataset).where(Dataset.project_id == project_id))
        if ("dataset", d.id) in visited
    }
    runs_by_id = {
        r.id: r
        for r in db.scalars(select(TrainingRun).where(TrainingRun.project_id == project_id))
        if ("run", r.id) in visited
    }
    artifacts_by_id = {
        a.id: a
        for a in db.scalars(select(Artifact).where(Artifact.project_id == project_id))
        if ("artifact", a.id) in visited
    }
    mvs_by_id = {
        m.id: m
        for m in db.scalars(select(ModelVersion).where(ModelVersion.project_id == project_id))
        if ("model_version", m.id) in visited
    }
    models_by_id = {
        rm.id: rm
        for rm in db.scalars(select(RegisteredModel).where(RegisteredModel.project_id == project_id))
    }
    exps_by_id = {
        ex.id: ex
        for ex in db.scalars(select(Experiment).where(Experiment.project_id == project_id))
        if ("experiment", ex.id) in visited
    }
    scans_by_id = {
        sc.id: sc
        for sc in db.scalars(select(SafetyScan).where(SafetyScan.project_id == project_id))
        if ("safety_scan", sc.id) in visited
    }

    nodes: list[dict[str, Any]] = []
    for ntype, nid in sorted(visited):
        label = f"{ntype}:{nid}"
        meta: dict[str, Any] = {}
        if ntype == "dataset" and nid in datasets_by_id:
            ds = datasets_by_id[nid]
            label = ds.name
            meta = {"description": ds.description}
        elif ntype == "run" and nid in runs_by_id:
            r = runs_by_id[nid]
            label = r.name
            meta = {"run_type": r.run_type, "status": r.status, "base_model": r.base_model}
        elif ntype == "artifact" and nid in artifacts_by_id:
            a = artifacts_by_id[nid]
            label = f"{a.name}:v{a.version}"
            meta = {"kind": a.kind, "sha256": a.sha256, "size_bytes": a.size_bytes, "status": a.status}
        elif ntype == "model_version" and nid in mvs_by_id:
            mv = mvs_by_id[nid]
            rm = models_by_id.get(mv.model_id)
            label = f"{rm.name if rm else mv.model_id} v{mv.version}"
            meta = {"model_id": mv.model_id, "version": mv.version, "stage": mv.stage}
        elif ntype == "experiment" and nid in exps_by_id:
            ex = exps_by_id[nid]
            label = ex.name
            meta = {"status": ex.status}
        elif ntype == "safety_scan" and nid in scans_by_id:
            sc = scans_by_id[nid]
            label = f"SafetyScan ({sc.status})"
            meta = {"status": sc.status, "global_score": sc.global_score}

        nodes.append(
            {
                "id": f"{ntype}:{nid}",
                "node_type": ntype,
                "entity_id": nid,
                "label": label,
                "metadata": meta,
            }
        )

    edges = [
        edge
        for edge in edge_map.values()
        if (edge["source_type"], edge["source_id"]) in visited
        and (edge["target_type"], edge["target_id"]) in visited
    ]
    edges.sort(key=lambda e: (e["source"], e["target"], e["relation"]))
    return nodes, edges


@router.get("/runs/{run_id}/lineage")
def get_run_lineage(run_id: str, db: DbSession, ctx: ViewerAuth) -> dict[str, Any]:
    _get_run_or_404(db, ctx.project_id, run_id)
    nodes, edges = _build_lineage_graph(db, ctx.project_id, [("run", run_id)])
    return {"run_id": run_id, "nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------


@router.post("/sweeps", status_code=201)
def create_sweep(
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: SweepCreate = _parse_body(SweepCreate, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")

    exists = db.scalar(
        select(MLOpsSweep.id).where(MLOpsSweep.project_id == ctx.project_id, MLOpsSweep.name == name)
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail="sweep name already exists")

    try:
        normalized_space = validate_search_space(body.search_space)
    except InvalidSearchSpaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.early_stopping is not None:
        strat = body.early_stopping.get("strategy", "none")
        if strat not in {"none", "median", "asha"}:
            raise HTTPException(status_code=422, detail=f"unsupported early stopping strategy '{strat}'")

    sweep = MLOpsSweep(
        project_id=ctx.project_id,
        name=name,
        description=body.description,
        method=body.method,
        metric_name=body.metric_name.strip(),
        metric_goal=body.metric_goal,
        search_space=normalized_space,
        early_stopping=body.early_stopping,
        max_trials=body.max_trials,
        status="running",
    )
    db.add(sweep)
    db.flush()
    return _sweep_out(sweep)


@router.get("/sweeps")
def list_sweeps(db: DbSession, ctx: ViewerAuth) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(MLOpsSweep)
            .where(MLOpsSweep.project_id == ctx.project_id)
            .order_by(MLOpsSweep.created_at.desc())
        )
    )
    return {"items": [_sweep_out(s) for s in rows]}


@router.get("/sweeps/{sweep_id}")
def get_sweep(sweep_id: str, db: DbSession, ctx: ViewerAuth) -> dict[str, Any]:
    sweep = _get_sweep_or_404(db, ctx.project_id, sweep_id)
    runs = list(
        db.scalars(
            select(TrainingRun)
            .where(TrainingRun.project_id == ctx.project_id, TrainingRun.sweep_id == sweep.id)
            .order_by(TrainingRun.created_at.asc(), TrainingRun.id.asc())
        )
    )
    analysis = build_sweep_analysis(sweep, runs)
    out = _sweep_out(sweep)
    out["trials"] = [_run_out(r) for r in runs]
    out["analysis"] = analysis
    return out


@router.post("/sweeps/{sweep_id}/trials/next", status_code=201)
def suggest_sweep_trial(
    sweep_id: str,
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    sweep = _get_sweep_or_404(db, ctx.project_id, sweep_id)
    if sweep.status in {"cancelled", "stopped", "completed"}:
        raise HTTPException(status_code=409, detail=f"sweep is {sweep.status}")

    body: SweepNextTrialIn = _parse_body(SweepNextTrialIn, payload or {})
    if body.dataset_id is not None:
        _get_dataset_or_404(db, ctx.project_id, body.dataset_id)

    existing_runs = list(
        db.scalars(
            select(TrainingRun)
            .where(TrainingRun.project_id == ctx.project_id, TrainingRun.sweep_id == sweep.id)
            .order_by(TrainingRun.created_at.asc(), TrainingRun.id.asc())
        )
    )
    try:
        suggested = suggest_next_params(
            method=sweep.method,
            search_space=sweep.search_space,
            completed_or_existing_trials=existing_runs,
            metric_goal=sweep.metric_goal,
            seed=sweep.id,
            max_trials=sweep.max_trials,
            metric_name=sweep.metric_name,
        )
    except SweepExhaustedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    trial_idx = len(existing_runs)
    run_name = body.name.strip() if body.name else f"{sweep.name}-trial-{trial_idx + 1}"
    now = utcnow()
    run = TrainingRun(
        project_id=ctx.project_id,
        name=run_name,
        run_type=body.run_type,
        status="running",
        base_model=body.base_model,
        dataset_id=body.dataset_id,
        sweep_id=sweep.id,
        hyperparams=suggested,
        tags=body.tags,
        started_at=now,
    )
    db.add(run)
    sweep.status = "running"
    db.flush()

    if body.dataset_id is not None:
        _add_lineage_edge(db, ctx.project_id, "dataset", body.dataset_id, "run", run.id, "trained_on")

    return {
        "sweep_id": sweep.id,
        "trial_index": trial_idx,
        "hyperparams": suggested,
        "run": _run_out(run),
    }


@router.post("/sweeps/{sweep_id}/stop")
def stop_sweep(sweep_id: str, db: DbSession, ctx: MemberAuth) -> dict[str, Any]:
    sweep = _get_sweep_or_404(db, ctx.project_id, sweep_id)
    sweep.status = "cancelled"
    sweep.stopped_at = utcnow()
    db.flush()
    out = _sweep_out(sweep)
    out["stopped"] = True
    return out


# ---------------------------------------------------------------------------
# Artifacts & Local Presigned Endpoints
# ---------------------------------------------------------------------------


@router.post("/artifacts/presign-upload", status_code=201)
def presign_artifact_upload(
    request: Request,
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: PresignUploadIn = _parse_body(PresignUploadIn, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")

    try:
        sha256_norm = validate_sha256(body.sha256)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    settings = request.app.state.settings
    if body.size_bytes > settings.max_artifact_bytes:
        raise HTTPException(
            status_code=422,
            detail=f"size_bytes exceeds max_artifact_bytes ({settings.max_artifact_bytes})",
        )

    if body.run_id is not None:
        _get_run_or_404(db, ctx.project_id, body.run_id)

    if body.external_uri is not None:
        try:
            ext_uri = validate_external_uri(body.external_uri)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        ext_uri = None

    artifact_store = request.app.state.artifact_store

    # Check CAS deduplication for same (project_id, sha256) when already committed
    existing_cas = None
    if ext_uri is None:
        existing_cas = db.scalars(
            select(Artifact)
            .where(
                Artifact.project_id == ctx.project_id,
                Artifact.sha256 == sha256_norm,
                Artifact.status == "committed",
            )
            .order_by(Artifact.created_at.asc())
        ).first()

    if existing_cas is not None:
        storage_backend = existing_cas.storage_backend
        storage_uri = existing_cas.storage_uri
        upload_url: str | None = None
        status_val = "committed"
        committed_at = utcnow()
        deduplicated = True
    elif ext_uri is not None:
        storage_backend = "external"
        storage_uri = ext_uri
        upload_url = None
        status_val = "pending_upload"
        committed_at = None
        deduplicated = False
    else:
        storage_uri, upload_url = artifact_store.presign_put(
            ctx.project_id,
            sha256_norm,
            body.size_bytes,
            body.content_type,
        )
        storage_backend = settings.artifact_store
        status_val = "pending_upload"
        committed_at = None
        deduplicated = False

    art: Artifact | None = None
    for attempt in range(MAX_CREATE_ATTEMPTS):
        try:
            with db.begin_nested():
                current_ver = db.scalar(
                    select(func.max(Artifact.version)).where(
                        Artifact.project_id == ctx.project_id, Artifact.name == name
                    )
                )
                art = Artifact(
                    project_id=ctx.project_id,
                    run_id=body.run_id,
                    name=name,
                    kind=body.kind,
                    version=(current_ver or 0) + 1,
                    storage_backend=storage_backend,
                    storage_uri=storage_uri,
                    sha256=sha256_norm,
                    size_bytes=body.size_bytes,
                    content_type=body.content_type,
                    status=status_val,
                    metadata_=body.metadata,
                    committed_at=committed_at,
                )
                db.add(art)
                db.flush()
            break
        except IntegrityError:
            if attempt == MAX_CREATE_ATTEMPTS - 1:
                raise HTTPException(status_code=409, detail="concurrent artifact creation, retry") from None

    assert art is not None
    if deduplicated and body.run_id is not None:
        _add_lineage_edge(db, ctx.project_id, "run", body.run_id, "artifact", art.id, "produced")

    out = _artifact_out(art)
    out["deduplicated"] = deduplicated
    out["upload_url"] = upload_url
    return out


@router.put("/artifacts/local/{token}")
async def upload_local_artifact(token: str, request: Request) -> dict[str, Any]:
    artifact_store = request.app.state.artifact_store
    if not isinstance(artifact_store, LocalFSArtifactStore):
        raise HTTPException(status_code=404, detail="local artifact store not enabled")

    try:
        key = artifact_store.verify_local_token(token, expected_op="put")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    max_bytes = request.app.state.settings.max_artifact_bytes

    async def _bounded_stream() -> AsyncIterator[bytes]:
        received = 0
        async for chunk in request.stream():
            if not chunk:
                continue
            received += len(chunk)
            if received > max_bytes:
                raise ArtifactTooLargeError("artifact exceeds max_artifact_bytes")
            yield chunk

    try:
        _, size_bytes = await artifact_store.write_local_blob_stream(key, _bounded_stream())
    except ArtifactTooLargeError as exc:
        raise HTTPException(status_code=413, detail="artifact exceeds max_artifact_bytes") from exc
    except ArtifactSha256MismatchError as exc:
        raise HTTPException(status_code=422, detail="sha256 mismatch") from exc
    except ArtifactCorruptionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"status": "uploaded", "size_bytes": size_bytes}


@router.get("/artifacts/local/{token}")
def download_local_artifact(token: str, request: Request) -> FileResponse:
    artifact_store = request.app.state.artifact_store
    if not isinstance(artifact_store, LocalFSArtifactStore):
        raise HTTPException(status_code=404, detail="local artifact store not enabled")

    try:
        key = artifact_store.verify_local_token(token, expected_op="get")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        path = artifact_store.read_local_blob_path(key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact blob not found") from exc

    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.post("/artifacts/{artifact_id}/commit")
def commit_artifact(
    artifact_id: str,
    request: Request,
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    art = _get_artifact_or_404(db, ctx.project_id, artifact_id)
    body: ArtifactCommitIn = _parse_body(ArtifactCommitIn, payload or {})

    source_run_id = body.source_run_id or art.run_id
    if source_run_id is not None:
        _get_run_or_404(db, ctx.project_id, source_run_id)
        art.run_id = source_run_id

    if art.storage_backend != "external":
        artifact_store = request.app.state.artifact_store
        exists, actual_size = artifact_store.exists_and_size(art.storage_uri)
        if not exists:
            raise HTTPException(status_code=400, detail="artifact blob not found in storage")
        if actual_size is not None:
            art.size_bytes = actual_size
    elif body.size_bytes is not None:
        art.size_bytes = body.size_bytes

    art.status = "committed"
    if art.committed_at is None:
        art.committed_at = utcnow()
    db.flush()

    if source_run_id is not None:
        _add_lineage_edge(db, ctx.project_id, "run", source_run_id, "artifact", art.id, "produced")

    return _artifact_out(art)


@router.get("/artifacts")
def list_artifacts(
    db: DbSession,
    ctx: ViewerAuth,
    run_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    stmt = select(Artifact).where(Artifact.project_id == ctx.project_id)
    if run_id is not None:
        stmt = stmt.where(Artifact.run_id == run_id)
    if kind is not None:
        stmt = stmt.where(Artifact.kind == kind)
    if status is not None:
        stmt = stmt.where(Artifact.status == status)
    if name is not None:
        stmt = stmt.where(Artifact.name == name)
    rows = list(db.scalars(stmt.order_by(Artifact.created_at.desc(), Artifact.version.desc())))
    return {"items": [_artifact_out(a) for a in rows]}


@router.get("/artifacts/{artifact_id}/download-url")
def get_artifact_download_url(
    artifact_id: str,
    request: Request,
    db: DbSession,
    ctx: ViewerAuth,
) -> dict[str, Any]:
    art = _get_artifact_or_404(db, ctx.project_id, artifact_id)
    if art.storage_backend == "external":
        download_url = art.storage_uri
    else:
        download_url = request.app.state.artifact_store.presign_get(ctx.project_id, art.storage_uri)
    return {
        "artifact_id": art.id,
        "download_url": download_url,
        "storage_backend": art.storage_backend,
        "sha256": art.sha256,
        "size_bytes": art.size_bytes,
    }


# ---------------------------------------------------------------------------
# Model & LoRA Registry
# ---------------------------------------------------------------------------


@router.post("/models", status_code=201)
def create_model(
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: ModelCreate = _parse_body(ModelCreate, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")

    exists = db.scalar(
        select(RegisteredModel.id).where(RegisteredModel.project_id == ctx.project_id, RegisteredModel.name == name)
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail="model name already exists")

    model = RegisteredModel(
        project_id=ctx.project_id,
        name=name,
        description=body.description,
        model_type=body.model_type,
        base_model=body.base_model,
        tags=body.tags,
    )
    db.add(model)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="model name already exists") from exc
    return _model_out(model)


@router.get("/models")
def list_models(db: DbSession, ctx: ViewerAuth) -> dict[str, Any]:
    models = list(
        db.scalars(
            select(RegisteredModel)
            .where(RegisteredModel.project_id == ctx.project_id)
            .order_by(RegisteredModel.updated_at.desc())
        )
    )
    versions = list(
        db.scalars(
            select(ModelVersion)
            .where(ModelVersion.project_id == ctx.project_id)
            .order_by(ModelVersion.version.desc())
        )
    )
    aliases = list(
        db.scalars(select(ModelAlias).where(ModelAlias.project_id == ctx.project_id))
    )

    versions_by_model: dict[str, list[ModelVersion]] = {}
    for mv in versions:
        versions_by_model.setdefault(mv.model_id, []).append(mv)

    aliases_by_model: dict[str, dict[str, int]] = {}
    for al in aliases:
        aliases_by_model.setdefault(al.model_id, {})[al.alias] = al.version

    items: list[dict[str, Any]] = []
    for m in models:
        mvs = versions_by_model.get(m.id, [])
        latest = mvs[0] if mvs else None
        stages: dict[str, list[int]] = {}
        for mv in mvs:
            stages.setdefault(mv.stage, []).append(mv.version)
        item = _model_out(m)
        item["latest_version"] = latest.version if latest else None
        item["aliases"] = dict(sorted(aliases_by_model.get(m.id, {}).items()))
        item["stages"] = stages
        item["latest_metrics"] = dict(latest.metrics_snapshot or {}) if latest else {}
        items.append(item)
    return {"items": items}


@router.get("/models/{model_id}")
def get_model(model_id: str, db: DbSession, ctx: ViewerAuth) -> dict[str, Any]:
    model = _get_model_or_404(db, ctx.project_id, model_id)
    versions = list(
        db.scalars(
            select(ModelVersion)
            .where(ModelVersion.project_id == ctx.project_id, ModelVersion.model_id == model.id)
            .order_by(ModelVersion.version.desc())
        )
    )
    aliases = list(
        db.scalars(
            select(ModelAlias).where(ModelAlias.project_id == ctx.project_id, ModelAlias.model_id == model.id)
        )
    )
    out = _model_out(model)
    out["versions"] = [_version_out(v) for v in versions]
    out["aliases"] = {a.alias: a.version for a in sorted(aliases, key=lambda x: x.alias)}
    return out


@router.delete("/models/{model_id}")
def delete_model(
    model_id: str,
    db: DbSession,
    ctx: AdminAuth,
    force: bool = False,
) -> dict[str, Any]:
    model = _get_model_or_404(db, ctx.project_id, model_id)
    if not force:
        has_protected_alias = db.scalar(
            select(ModelAlias.id).where(
                ModelAlias.project_id == ctx.project_id,
                ModelAlias.model_id == model.id,
                ModelAlias.alias.in_(ADMIN_ONLY_ALIASES),
            )
        )
        has_prod_stage = db.scalar(
            select(ModelVersion.id).where(
                ModelVersion.project_id == ctx.project_id,
                ModelVersion.model_id == model.id,
                ModelVersion.stage == "production",
            )
        )
        if has_protected_alias is not None or has_prod_stage is not None:
            raise HTTPException(
                status_code=409,
                detail="cannot delete model with production/champion alias or production stage; pass force=true",
            )
    db.delete(model)
    db.flush()
    return {"deleted": True, "id": model_id}


@router.post("/models/{model_id}/versions", status_code=201)
def create_model_version(
    model_id: str,
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    model = _get_model_or_404(db, ctx.project_id, model_id)
    body: ModelVersionCreate = _parse_body(ModelVersionCreate, payload)

    if body.stage == "production":
        _require_admin(ctx)

    if body.artifact_id is not None:
        _get_artifact_or_404(db, ctx.project_id, body.artifact_id)
    if body.run_id is not None:
        _get_run_or_404(db, ctx.project_id, body.run_id)
    if body.eval_experiment_id is not None:
        _get_experiment_or_404(db, ctx.project_id, body.eval_experiment_id)
    if body.safety_scan_id is not None:
        _get_safety_scan_or_404(db, ctx.project_id, body.safety_scan_id)

    mv: ModelVersion | None = None
    for attempt in range(MAX_CREATE_ATTEMPTS):
        try:
            with db.begin_nested():
                current_ver = db.scalar(
                    select(func.max(ModelVersion.version)).where(
                        ModelVersion.project_id == ctx.project_id, ModelVersion.model_id == model.id
                    )
                )
                mv = ModelVersion(
                    project_id=ctx.project_id,
                    model_id=model.id,
                    version=(current_ver or 0) + 1,
                    artifact_id=body.artifact_id,
                    run_id=body.run_id,
                    eval_experiment_id=body.eval_experiment_id,
                    safety_scan_id=body.safety_scan_id,
                    stage=body.stage,
                    lora_config=body.lora_config,
                    signature=body.signature,
                    metrics_snapshot=body.metrics_snapshot,
                    model_card=body.model_card,
                    created_by=ctx.user_email or ctx.user_id or "api_key",
                )
                db.add(mv)
                db.flush()
            break
        except IntegrityError:
            if attempt == MAX_CREATE_ATTEMPTS - 1:
                raise HTTPException(status_code=409, detail="concurrent version creation, retry") from None

    assert mv is not None
    if body.artifact_id is not None:
        _add_lineage_edge(db, ctx.project_id, "artifact", body.artifact_id, "model_version", mv.id, "packaged_as")
    if body.run_id is not None:
        _add_lineage_edge(db, ctx.project_id, "run", body.run_id, "model_version", mv.id, "registered_as")
    if body.eval_experiment_id is not None:
        _add_lineage_edge(
            db, ctx.project_id, "model_version", mv.id, "experiment", body.eval_experiment_id, "evaluated_by"
        )
    if body.safety_scan_id is not None:
        _add_lineage_edge(
            db, ctx.project_id, "model_version", mv.id, "safety_scan", body.safety_scan_id, "audited_by"
        )

    model.updated_at = utcnow()
    db.flush()
    return _version_out(mv)


@router.patch("/models/{model_id}/versions/{version}/stage")
def update_model_version_stage(
    model_id: str,
    version: int,
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    model = _get_model_or_404(db, ctx.project_id, model_id)
    body: ModelStagePatch = _parse_body(ModelStagePatch, payload)
    if body.stage == "production":
        _require_admin(ctx)
    mv = _get_model_version_or_404(db, ctx.project_id, model.id, version)
    if mv.stage == "production":
        _require_admin(ctx)
    mv.stage = body.stage
    model.updated_at = utcnow()
    db.flush()
    return _version_out(mv)


@router.post("/models/{model_id}/aliases")
def set_model_alias(
    model_id: str,
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    model = _get_model_or_404(db, ctx.project_id, model_id)
    body: ModelAliasIn = _parse_body(ModelAliasIn, payload)
    alias = body.alias.strip().lstrip("@")
    if not alias:
        raise HTTPException(status_code=422, detail="alias must not be blank")
    if alias.lower() in ADMIN_ONLY_ALIASES:
        _require_admin(ctx)

    _get_model_version_or_404(db, ctx.project_id, model.id, body.version)

    existing = db.scalars(
        select(ModelAlias).where(
            ModelAlias.project_id == ctx.project_id,
            ModelAlias.model_id == model.id,
            ModelAlias.alias == alias,
        )
    ).first()
    if existing is None:
        db.add(ModelAlias(project_id=ctx.project_id, model_id=model.id, alias=alias, version=body.version))
    else:
        existing.version = body.version
        existing.updated_at = utcnow()
    model.updated_at = utcnow()
    db.flush()
    return {"model_id": model.id, "alias": alias, "version": body.version}


@router.get("/models/{model_id}/lineage")
def get_model_lineage(model_id: str, db: DbSession, ctx: ViewerAuth) -> dict[str, Any]:
    model = _get_model_or_404(db, ctx.project_id, model_id)
    mvs = list(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.project_id == ctx.project_id,
                ModelVersion.model_id == model.id,
            )
        )
    )
    seed_nodes = [("model_version", mv.id) for mv in mvs]
    if not seed_nodes:
        return {"model_id": model.id, "nodes": [], "edges": []}
    nodes, edges = _build_lineage_graph(db, ctx.project_id, seed_nodes)
    return {"model_id": model.id, "nodes": nodes, "edges": edges}


@router.post("/models/{model_id}/versions/{version}/evaluate", status_code=202)
def evaluate_model_version(
    model_id: str,
    version: int,
    db: DbSession,
    ctx: MemberAuth,
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    model = _get_model_or_404(db, ctx.project_id, model_id)
    mv = _get_model_version_or_404(db, ctx.project_id, model.id, version)
    body: ModelEvaluateIn = _parse_body(ModelEvaluateIn, payload or {})

    if body.dataset_id is not None:
        _get_dataset_or_404(db, ctx.project_id, body.dataset_id)

    job = enqueue(
        db,
        ctx.project_id,
        "mlops.evaluate",
        {
            "model_id": model.id,
            "model_name": model.name,
            "version": mv.version,
            "model_version_id": mv.id,
            "dataset_id": body.dataset_id,
            "name": body.name or f"{model.name}-v{mv.version}-eval",
            "metrics": body.metrics,
        },
    )
    return {
        "status": "queued",
        "job_id": job.id,
        "model_id": model.id,
        "version": mv.version,
    }
