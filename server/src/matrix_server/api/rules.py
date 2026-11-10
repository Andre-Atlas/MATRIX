"""Rules API (spec section 4, Anotação, regras e avaliação online)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.auth import Role, project_of
from matrix_server.rules import RuleError, validate_rule
from matrix_server.storage.db import as_utc, get_db
from matrix_server.storage.models import Rule

DbSession = Annotated[Session, Depends(get_db)]
ProjectId = Annotated[str, Depends(project_of(Role.VIEWER))]  # reads
AdminProjectId = Annotated[str, Depends(project_of(Role.ADMIN))]  # configuration (sessions only)

router = APIRouter(prefix="/api/v1", tags=["rules"])


def rule_out(rule: Rule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "enabled": rule.enabled,
        "condition": rule.condition,
        "actions": rule.actions,
        "dataset_id": rule.dataset_id,
        "created_at": as_utc(rule.created_at),
    }


@router.post("/rules", status_code=201)
def create_rule(
    db: DbSession, project_id: AdminProjectId, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    try:
        data = validate_rule(db, project_id, payload)
    except RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rule = Rule(project_id=project_id, **data)
    db.add(rule)
    db.flush()
    return rule_out(rule)


@router.get("/rules")
def list_rules(db: DbSession, project_id: ProjectId) -> dict[str, Any]:
    rows = db.scalars(select(Rule).where(Rule.project_id == project_id).order_by(Rule.created_at, Rule.id))
    return {"items": [rule_out(r) for r in rows]}


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: str, db: DbSession, project_id: AdminProjectId) -> Response:
    rule = db.scalars(select(Rule).where(Rule.id == rule_id, Rule.project_id == project_id)).first()
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    db.delete(rule)
    return Response(status_code=204)
