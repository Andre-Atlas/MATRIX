"""Online evaluations API (spec section 4, Anotação, regras e avaliação online).

The judge model is only `{provider, name}`: API keys and endpoints come from the server's
environment variables and are never accepted from clients nor persisted.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.auth import current_project_id
from matrix_server.domain.spans import SpanKind
from matrix_server.evals import build_metric, get_provider
from matrix_server.online_evals import invalidate_cache
from matrix_server.storage.db import as_utc, get_db
from matrix_server.storage.models import OnlineEval

DbSession = Annotated[Session, Depends(get_db)]
ProjectId = Annotated[str, Depends(current_project_id)]

router = APIRouter(prefix="/api/v1", tags=["online-evals"])


class EvalFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SpanKind | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)


class JudgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)


class OnlineEvalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    metric: str = Field(min_length=1, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)
    sampling_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    filter: EvalFilter | None = None
    model: JudgeModel | None = None


def _parse(payload: Any) -> OnlineEvalCreate:
    """Validated by hand so a 422 never echoes the input (NaN would not serialize)."""
    try:
        body = OnlineEvalCreate.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_input=False, include_context=False, include_url=False)
        ) from exc
    try:
        json.dumps(body.params, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="params must be finite JSON") from exc
    try:
        build_metric({"type": body.metric, "params": body.params})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid metric: {str(exc)[:300]}") from exc
    if body.model is not None:
        try:
            get_provider(body.model.provider)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"unknown provider {body.model.provider!r}") from exc
    return body


def _out(ev: OnlineEval) -> dict[str, Any]:
    created = as_utc(ev.created_at)
    return {
        "id": ev.id,
        "name": ev.name,
        "metric": ev.metric,
        "params": ev.params or {},
        "sampling_rate": ev.sampling_rate,
        "filter": ev.filter or {},
        "model": ev.model,
        "enabled": ev.enabled,
        "created_at": created.isoformat().replace("+00:00", "Z") if created else None,
    }


@router.post("/online-evals", status_code=201)
def create_online_eval(
    db: DbSession, project_id: ProjectId, payload: Annotated[Any, Body()] = None
) -> dict[str, Any]:
    body = _parse(payload)
    duplicate = db.scalars(
        select(OnlineEval.id).where(OnlineEval.project_id == project_id, OnlineEval.name == body.name)
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="an online eval with this name already exists")
    ev = OnlineEval(
        project_id=project_id,
        name=body.name,
        metric=body.metric,
        params=body.params,
        sampling_rate=body.sampling_rate,
        filter=body.filter.model_dump(mode="json", exclude_none=True) if body.filter else {},
        model=body.model.model_dump() if body.model else None,
        enabled=True,
    )
    db.add(ev)
    db.flush()
    out = _out(ev)
    db.commit()
    invalidate_cache(project_id)
    return out


@router.get("/online-evals")
def list_online_evals(db: DbSession, project_id: ProjectId) -> dict[str, Any]:
    rows = db.scalars(
        select(OnlineEval)
        .where(OnlineEval.project_id == project_id)
        .order_by(OnlineEval.created_at, OnlineEval.id)
    ).all()
    return {"items": [_out(r) for r in rows]}


@router.delete("/online-evals/{online_eval_id}", status_code=204)
def delete_online_eval(online_eval_id: str, db: DbSession, project_id: ProjectId) -> Response:
    ev = db.scalars(
        select(OnlineEval).where(OnlineEval.id == online_eval_id, OnlineEval.project_id == project_id)
    ).first()
    if ev is None:
        raise HTTPException(status_code=404, detail="online eval not found")
    db.delete(ev)
    db.commit()
    invalidate_cache(project_id)
    return Response(status_code=204)
