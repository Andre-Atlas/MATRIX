"""Project egress allowlist API (contract G0 section 1): admin only, scoped to the caller's project."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server import audit
from matrix_server.auth import Principal, Role, require
from matrix_server.egress import EgressError, EgressHost, validate_allowlist_entry
from matrix_server.storage.db import as_utc, get_db

DbSession = Annotated[Session, Depends(get_db)]
# Module-level dependency object so tests can override it: `app.dependency_overrides[admin_principal]`.
admin_principal = require(Role.ADMIN)
AdminPrincipal = Annotated[Principal, Depends(admin_principal)]

router = APIRouter(prefix="/api/v1/projects/{project_id}/egress-hosts", tags=["egress"])


class HostIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=300)


def _scope(project_id: str, principal: Principal) -> str:
    # Not revealing other projects: a path project the principal does not act on is a 404.
    if principal.project_id != project_id:
        raise HTTPException(status_code=404, detail="project not found")
    return project_id


def _out(row: EgressHost) -> dict[str, Any]:
    return {"id": row.id, "host": row.host, "created_at": as_utc(row.created_at), "created_by": row.created_by}


@router.get("")
def list_hosts(project_id: str, db: DbSession, principal: AdminPrincipal) -> dict[str, Any]:
    _scope(project_id, principal)
    rows = db.scalars(
        select(EgressHost).where(EgressHost.project_id == project_id).order_by(EgressHost.created_at, EgressHost.id)
    )
    return {"items": [_out(r) for r in rows]}


@router.post("", status_code=201)
def add_host(project_id: str, payload: HostIn, db: DbSession, principal: AdminPrincipal) -> dict[str, Any]:
    _scope(project_id, principal)
    try:
        host = validate_allowlist_entry(payload.host)
    except EgressError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    exists = db.scalar(
        select(EgressHost.id).where(EgressHost.project_id == project_id, EgressHost.host == host)
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail="host already allowed")
    row = EgressHost(project_id=project_id, host=host, created_by=principal.user_id)
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="egress.host_add",
        project_id=project_id,
        actor_kind=principal.kind,
        actor_id=principal.user_id,
        target=host,
    )
    return _out(row)


@router.delete("/{host_id}", status_code=204)
def remove_host(project_id: str, host_id: str, db: DbSession, principal: AdminPrincipal) -> Response:
    _scope(project_id, principal)
    row = db.scalar(select(EgressHost).where(EgressHost.id == host_id, EgressHost.project_id == project_id))
    if row is None:
        raise HTTPException(status_code=404, detail="egress host not found")
    audit.record(
        db,
        action="egress.host_remove",
        project_id=project_id,
        actor_kind=principal.kind,
        actor_id=principal.user_id,
        target=row.host,
    )
    db.delete(row)
    return Response(status_code=204)
