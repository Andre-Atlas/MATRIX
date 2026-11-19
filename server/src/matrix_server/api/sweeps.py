"""Canonical hyperparameter sweeps API (`/api/v1/sweeps`, contract G2)."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.api.runs import capacity_http
from matrix_server.auth import Principal, Role, require
from matrix_server.runs import service as runs_service
from matrix_server.runs.models import Run
from matrix_server.runs.store import MetricStore
from matrix_server.storage.db import get_db
from matrix_server.sweeps import service as sweep_service
from matrix_server.sweeps.engine import SweepValidationError
from matrix_server.sweeps.models import Sweep

router = APIRouter(prefix="/api/v1/sweeps", tags=["sweeps"])

DbSession = Annotated[Session, Depends(get_db)]
ViewerAuth = Annotated[Principal, Depends(require(Role.VIEWER))]
MemberAuth = Annotated[Principal, Depends(require(Role.MEMBER))]
AdminAuth = Annotated[Principal, Depends(require(Role.ADMIN))]

MAX_PAGE = 500
DEFAULT_PAGE = 50


class SweepCreateIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any]
    description: str | None = Field(default=None, max_length=10_000)


class SweepNextIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    agent_id: str | None = Field(default=None, max_length=128)


class SweepReportIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    step: int = Field(ge=0, le=runs_service.MAX_STEP)
    value: float


class SweepPatchIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    status: Literal["paused", "running", "stopped"]


def _store(request: Request) -> MetricStore:
    return request.app.state.metric_store


def _parse(model: type[BaseModel], payload: Any) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_input=False, include_context=False, include_url=False),
        ) from None


def _get_sweep_or_404(db: Session, project_id: str, sweep_id: str) -> Sweep:
    sweep = sweep_service.get_sweep(db, project_id, sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail="sweep not found")
    return sweep


@router.post("", status_code=201)
def create_sweep(
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: SweepCreateIn = _parse(SweepCreateIn, payload)
    try:
        sweep = sweep_service.create_sweep(
            db,
            principal.project_id,
            name=body.name,
            config=body.config,
            created_by=principal.user_id,
            description=body.description,
        )
    except SweepValidationError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "errors": exc.errors}) from None
    return sweep_service.render_sweep(sweep, run_count=0)


@router.get("")
def list_sweeps(
    db: DbSession,
    principal: ViewerAuth,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> dict[str, Any]:
    items = sweep_service.list_sweeps(db, principal.project_id, status=status, limit=limit)
    return {"items": items}


@router.get("/{sweep_id}")
def get_sweep(
    sweep_id: str,
    request: Request,
    db: DbSession,
    principal: ViewerAuth,
) -> dict[str, Any]:
    sweep = _get_sweep_or_404(db, principal.project_id, sweep_id)
    return sweep_service.sweep_detail(db, _store(request), sweep)


@router.post("/{sweep_id}/next")
def next_trial(
    sweep_id: str,
    request: Request,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> Response:
    body: SweepNextIn = _parse(SweepNextIn, payload or {})
    try:
        result = sweep_service.next_trial(
            db,
            _store(request),
            request.app.state.settings,
            principal.project_id,
            sweep_id,
            agent_id=body.agent_id,
            created_by=principal.user_id,
        )
    except runs_service.CapacityError as exc:
        raise capacity_http(exc) from None
    except sweep_service.SweepConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    if result is None:
        raise HTTPException(status_code=404, detail="sweep not found")
    if result.outcome == "paused":
        return Response(status_code=204, headers={"Retry-After": str(result.retry_after or 10)})
    if result.outcome == "exhausted":
        return Response(status_code=204)

    assert result.run is not None and result.params is not None
    return JSONResponse(
        status_code=200,
        content={
            "run": {"id": result.run.id, "name": result.run.name, "status": result.run.status},
            "params": result.params,
        },
    )


@router.post("/{sweep_id}/runs/{run_id}/report")
def report_run_metric(
    sweep_id: str,
    run_id: str,
    request: Request,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: SweepReportIn = _parse(SweepReportIn, payload)
    if not math.isfinite(body.value):
        raise HTTPException(status_code=422, detail="value must be finite")

    sweep = _get_sweep_or_404(db, principal.project_id, sweep_id)
    run = db.scalars(
        select(Run).where(Run.id == run_id, Run.project_id == principal.project_id, Run.sweep_id == sweep.id)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found in sweep")

    res = sweep_service.report_metric(
        db,
        _store(request),
        request.app.state.settings,
        sweep,
        run,
        step=body.step,
        value=body.value,
        record_point=True,
    )
    return {"action": res.action}


@router.patch("/{sweep_id}")
def patch_sweep(
    sweep_id: str,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: SweepPatchIn = _parse(SweepPatchIn, payload)
    sweep = _get_sweep_or_404(db, principal.project_id, sweep_id)
    try:
        sweep_service.patch_sweep_status(db, sweep, body.status)
    except SweepValidationError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "errors": exc.errors}) from None
    except sweep_service.SweepConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    counts = sweep_service.count_runs_for_sweeps(db, principal.project_id, [sweep.id])
    return sweep_service.render_sweep(sweep, counts.get(sweep.id, 0))


@router.delete("/{sweep_id}", status_code=204)
def delete_sweep(
    sweep_id: str,
    db: DbSession,
    principal: AdminAuth,
) -> Response:
    sweep = _get_sweep_or_404(db, principal.project_id, sweep_id)
    sweep_service.delete_sweep(db, sweep)
    return Response(status_code=204)
