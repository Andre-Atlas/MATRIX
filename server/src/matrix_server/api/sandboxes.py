"""Inspect AI Sandboxes API (ADR-007).

Provides endpoints for creating, monitoring, inspecting transcripts, and cancelling
Inspect AI agent sandbox runs with strict multi-tenant isolation, RBAC, anti-NaN
validation, and hardened container execution policies.
"""

from __future__ import annotations

import base64
import math
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from matrix_server.auth import AuthContext, require_role
from matrix_server.sandboxes.runner import (
    FORBIDDEN_SOCKET_PATTERNS,
    run_sandbox_job,
    validate_sandbox_path,
)
from matrix_server.storage import jobs
from matrix_server.storage.db import as_utc, get_db, utcnow
from matrix_server.storage.models import (
    Dataset,
    SandboxRun,
    SandboxTaskTranscript,
)

router = APIRouter(prefix="/api/v1/sandboxes", tags=["sandboxes"])

DbSession = Annotated[Session, Depends(get_db)]
ViewerAuth = Annotated[AuthContext, Depends(require_role("viewer"))]
MemberAuth = Annotated[AuthContext, Depends(require_role("member"))]
AdminAuth = Annotated[AuthContext, Depends(require_role("admin"))]

DEFAULT_PAGE = 50
MAX_PAGE = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_utc(value: Any) -> str | None:
    dt = as_utc(value)
    return None if dt is None else dt.isoformat().replace("+00:00", "Z")


def _reject_non_finite(obj: Any) -> None:
    """Recursively reject NaN and Infinity in payloads."""
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


def _run_out(run: SandboxRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "name": run.name,
        "runner_backend": run.runner_backend,
        "dataset_id": run.dataset_id,
        "dataset_commit_id": run.dataset_commit_id,
        "solver_config": run.solver_config,
        "scorer_config": run.scorer_config,
        "sandbox_policy": run.sandbox_policy,
        "status": run.status,
        "pass_rate": run.pass_rate,
        "total_tasks": run.total_tasks,
        "passed_tasks": run.passed_tasks,
        "total_tokens": run.total_tokens,
        "total_cost_usd": run.total_cost_usd,
        "job_id": run.job_id,
        "error": run.error,
        "created_at": _iso_utc(run.created_at),
        "started_at": _iso_utc(run.started_at),
        "finished_at": _iso_utc(run.finished_at),
    }


def _transcript_summary_out(t: SandboxTaskTranscript) -> dict[str, Any]:
    return {
        "id": t.id,
        "sandbox_run_id": t.sandbox_run_id,
        "task_key": t.task_key,
        "status": t.status,
        "score": t.score,
        "duration_ms": t.duration_ms,
        "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens,
        "error": t.error,
        "created_at": _iso_utc(t.created_at),
    }


def _transcript_detail_out(t: SandboxTaskTranscript) -> dict[str, Any]:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "sandbox_run_id": t.sandbox_run_id,
        "task_key": t.task_key,
        "status": t.status,
        "score": t.score,
        "scorer_details": t.scorer_details,
        "steps": t.steps,
        "verified_artifacts": t.verified_artifacts,
        "duration_ms": t.duration_ms,
        "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens,
        "error": t.error,
        "created_at": _iso_utc(t.created_at),
    }


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class CreateSandboxRunIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    runner_backend: Literal["local_sandbox", "docker"] = "local_sandbox"
    dataset_id: str | None = Field(default=None, max_length=32)
    dataset_commit_id: str | None = Field(default=None, max_length=32)
    solver_config: dict[str, Any] = Field(default_factory=dict)
    scorer_config: dict[str, Any] = Field(default_factory=dict)
    sandbox_policy: dict[str, Any] = Field(default_factory=dict)
    sync_exec: bool = False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/runs", status_code=201)
def create_sandbox_run(
    request: Request,
    payload: Annotated[dict[str, Any], Body()],
    db: DbSession,
    ctx: MemberAuth,
) -> dict[str, Any]:
    """Create and enqueue an Inspect AI Sandbox run."""
    _reject_non_finite(payload)
    try:
        body = CreateSandboxRunIn.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_input=False, include_context=False, include_url=False),
        ) from exc

    policy = body.sandbox_policy or {}

    # Security rule 1: Non-admin requesting network access rejected with 403
    network_mode = policy.get("network_mode", "none")
    if network_mode != "none" and ctx.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="network_mode != 'none' requires admin role",
        )

    # Security rule 2: Docker socket mounting strictly forbidden
    mounts = policy.get("mounts", [])
    if isinstance(mounts, list):
        for m in mounts:
            m_str = str(m)
            if any(sock in m_str for sock in FORBIDDEN_SOCKET_PATTERNS):
                raise HTTPException(
                    status_code=400,
                    detail="mounting /var/run/docker.sock is strictly forbidden",
                )

    # Security rule 3: Path traversal checks
    # Check initial files in solver_config
    initial_files = body.solver_config.get("initial_files", {})
    if isinstance(initial_files, dict):
        for fpath in initial_files:
            try:
                validate_sandbox_path(fpath, "initial_file path")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Check tasks if specified
    tasks = body.solver_config.get("tasks", [])
    if isinstance(tasks, list):
        for t in tasks:
            if isinstance(t, dict):
                t_key = t.get("task_key", "")
                if t_key:
                    try:
                        validate_sandbox_path(t_key, "task_key")
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
                for fpath in t.get("initial_files", {}):
                    try:
                        validate_sandbox_path(fpath, "task initial_file")
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Check timeout & step bounds
    timeout = policy.get("timeout_seconds")
    if timeout is not None and (not isinstance(timeout, (int, float)) or not (1 <= timeout <= 3600)):
        raise HTTPException(status_code=422, detail="timeout_seconds must be between 1 and 3600")

    task_timeout = policy.get("task_timeout_seconds")
    if task_timeout is not None and (
        not isinstance(task_timeout, (int, float)) or not (1 <= task_timeout <= 600)
    ):
        raise HTTPException(status_code=422, detail="task_timeout_seconds must be between 1 and 600")

    max_steps = policy.get("max_steps")
    if max_steps is not None and (not isinstance(max_steps, int) or not (1 <= max_steps <= 100)):
        raise HTTPException(status_code=422, detail="max_steps must be between 1 and 100")

    # Anti-IDOR: Check dataset ownership if provided
    if body.dataset_id:
        ds = db.scalar(
            select(Dataset).where(
                Dataset.id == body.dataset_id,
                Dataset.project_id == ctx.project_id,
            )
        )
        if ds is None:
            raise HTTPException(status_code=404, detail="dataset not found")

    run = SandboxRun(
        project_id=ctx.project_id,
        name=body.name,
        runner_backend=body.runner_backend,
        dataset_id=body.dataset_id,
        dataset_commit_id=body.dataset_commit_id,
        solver_config=body.solver_config,
        scorer_config=body.scorer_config,
        sandbox_policy=body.sandbox_policy,
        status="queued",
    )
    db.add(run)
    db.flush()

    job = jobs.enqueue(
        session=db,
        project_id=ctx.project_id,
        kind="sandbox.run",
        payload={"sandbox_run_id": run.id},
    )
    run.job_id = job.id
    db.commit()
    db.refresh(run)

    if body.sync_exec:
        run_sandbox_job(
            request.app.state.session_factory,
            run.id,
            getattr(request.app.state, "artifact_store", None),
        )
        db.refresh(run)

    return _run_out(run)


@router.get("/runs")
def list_sandbox_runs(
    db: DbSession,
    ctx: ViewerAuth,
    status: str | None = None,
    dataset_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List sandbox runs with keyset cursor pagination."""
    stmt = select(SandboxRun).where(SandboxRun.project_id == ctx.project_id)
    if status is not None:
        stmt = stmt.where(SandboxRun.status == status)
    if dataset_id is not None:
        stmt = stmt.where(SandboxRun.dataset_id == dataset_id)

    if cursor is not None:
        ts, row_id = _decode_cursor(cursor)
        ts = _same_tz(db, ts)
        stmt = stmt.where(
            or_(
                SandboxRun.created_at < ts,
                and_(SandboxRun.created_at == ts, SandboxRun.id < row_id),
            )
        )

    rows = list(
        db.scalars(
            stmt.order_by(SandboxRun.created_at.desc(), SandboxRun.id.desc()).limit(limit + 1)
        )
    )

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id)

    return {"items": [_run_out(r) for r in rows], "next_cursor": next_cursor}


@router.get("/runs/{run_id}")
def get_sandbox_run(
    run_id: str,
    db: DbSession,
    ctx: ViewerAuth,
) -> dict[str, Any]:
    """Get detailed information about a sandbox run."""
    run = db.scalar(
        select(SandboxRun).where(
            SandboxRun.id == run_id,
            SandboxRun.project_id == ctx.project_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="sandbox run not found")
    return _run_out(run)


@router.get("/runs/{run_id}/transcripts")
def list_run_transcripts(
    run_id: str,
    db: DbSession,
    ctx: ViewerAuth,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    """List summary transcripts for tasks in a sandbox run."""
    run = db.scalar(
        select(SandboxRun.id).where(
            SandboxRun.id == run_id,
            SandboxRun.project_id == ctx.project_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="sandbox run not found")

    stmt = select(SandboxTaskTranscript).where(
        SandboxTaskTranscript.sandbox_run_id == run_id,
        SandboxTaskTranscript.project_id == ctx.project_id,
    )
    if status is not None:
        stmt = stmt.where(SandboxTaskTranscript.status == status)

    rows = db.scalars(
        stmt.order_by(SandboxTaskTranscript.created_at.asc(), SandboxTaskTranscript.task_key.asc())
        .offset(offset)
        .limit(limit)
    ).all()

    return [_transcript_summary_out(t) for t in rows]


@router.get("/runs/{run_id}/transcripts/{task_key:path}")
def get_run_transcript_detail(
    run_id: str,
    task_key: str,
    db: DbSession,
    ctx: ViewerAuth,
) -> dict[str, Any]:
    """Get full transcript details including steps and verified artifacts for a task."""
    try:
        clean_key = validate_sandbox_path(task_key, "task_key")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run = db.scalar(
        select(SandboxRun.id).where(
            SandboxRun.id == run_id,
            SandboxRun.project_id == ctx.project_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="sandbox run not found")

    transcript = db.scalar(
        select(SandboxTaskTranscript).where(
            SandboxTaskTranscript.sandbox_run_id == run_id,
            SandboxTaskTranscript.task_key == clean_key,
            SandboxTaskTranscript.project_id == ctx.project_id,
        )
    )
    if transcript is None:
        raise HTTPException(status_code=404, detail="transcript not found")

    return _transcript_detail_out(transcript)


@router.post("/runs/{run_id}/cancel")
def cancel_sandbox_run(
    run_id: str,
    db: DbSession,
    ctx: MemberAuth,
) -> dict[str, Any]:
    """Cancel an ongoing or queued sandbox run."""
    run = db.scalar(
        select(SandboxRun).where(
            SandboxRun.id == run_id,
            SandboxRun.project_id == ctx.project_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="sandbox run not found")

    if run.status in ("completed", "failed", "cancelled"):
        return _run_out(run)

    run.status = "cancelled"
    run.finished_at = utcnow()
    db.commit()
    db.refresh(run)
    return _run_out(run)
