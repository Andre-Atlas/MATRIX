"""Projects, members, API keys and audit log of a project (contract G0 section 1).

Role rules for members: admins manage viewers and members; only an owner grants, changes or removes
the admin and owner roles; the last owner can be neither demoted nor removed. Egress hosts live in
`api/egress.py`.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server import audit, users
from matrix_server import projects as project_ops
from matrix_server.api.auth import iso, memberships_out, parse_body, request_detail, user_out
from matrix_server.auth import (
    Principal,
    ProjectExistsError,
    Role,
    UserContext,
    create_project,
    issue_api_key,
    require,
    require_user,
)
from matrix_server.storage.db import get_db, utcnow
from matrix_server.storage.models import ApiKey, AuditLog, Membership, Project, User

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[UserContext, Depends(require_user())]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]
Owner = Annotated[Principal, Depends(require(Role.OWNER))]

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

RoleName = Literal["viewer", "member", "admin", "owner"]
ScopeName = Literal["ingest", "read", "write"]


def _actor(principal: Principal) -> dict[str, Any]:
    return {"actor_kind": principal.actor_kind, "actor_id": principal.actor_id}


# --- projects --------------------------------------------------------------------------------------


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)


@router.get("")
def list_my_projects(db: DbSession, ctx: CurrentUser) -> dict[str, Any]:
    return {"items": memberships_out(db, ctx.user.id)}


@router.post("", status_code=201)
def create_my_project(
    request: Request, db: DbSession, ctx: CurrentUser, payload: Annotated[Any, Body()] = None
) -> dict[str, Any]:
    if not request.app.state.settings.allow_project_creation:
        raise HTTPException(status_code=403, detail="project creation is disabled")
    body: ProjectIn = parse_body(ProjectIn, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    try:
        with db.begin_nested():
            project, _ = create_project(db, name, owner_user_id=ctx.user.id, with_key=False)
    except (ProjectExistsError, IntegrityError):
        raise HTTPException(status_code=409, detail="project name already exists") from None
    audit.record(
        db, action="project.create", project_id=project.id, actor_kind="user", actor_id=ctx.user.id,
        target=project.id, detail={"name": project.name, **request_detail(request)},
    )
    return {"project": {"id": project.id, "name": project.name}, "role": Role.OWNER.label}


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, request: Request, db: DbSession, principal: Owner) -> Response:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    name = project.name
    db.expunge(project)
    project_ops.delete_project(db, project_id)
    # project_id stays on the row so the deletion is traceable; the project's audit rows are kept.
    audit.record(
        db, action="project.delete", project_id=project_id, target=project_id,
        detail={"name": name, **request_detail(request)}, **_actor(principal),
    )
    return Response(status_code=204)


# --- members -----------------------------------------------------------------------------------------


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=users.EMAIL_MAX_CHARS)
    role: RoleName


class MemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: RoleName


def _member_out(user: User, membership: Membership) -> dict[str, Any]:
    return {"user": user_out(user), "role": membership.role, "added_at": iso(membership.created_at)}


def _require_owner_for(principal: Principal, *roles: Role | None) -> None:
    """Granting, changing or removing admin/owner is for owners only (admin never touches owners)."""
    if any(r is not None and r >= Role.ADMIN for r in roles) and principal.role < Role.OWNER:
        raise HTTPException(status_code=403, detail="requires role owner")


def _membership(db: Session, project_id: str, user_id: str, *, lock: bool = False) -> Membership:
    stmt = select(Membership).where(Membership.project_id == project_id, Membership.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalars(stmt).first()
    if row is None:
        raise HTTPException(status_code=404, detail="member not found")
    return row


def _ensure_other_owner(db: Session, project_id: str, user_id: str) -> None:
    owners = db.scalars(
        select(Membership.user_id)
        .where(Membership.project_id == project_id, Membership.role == Role.OWNER.label)
        .with_for_update()
    ).all()
    if not [o for o in owners if o != user_id]:
        raise HTTPException(status_code=409, detail="a project must keep at least one owner")


@router.get("/{project_id}/members")
def list_members(project_id: str, db: DbSession, principal: Admin) -> dict[str, Any]:
    rows = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.project_id == principal.project_id)
        .order_by(Membership.created_at, User.email)
    ).all()
    return {"items": [_member_out(u, m) for u, m in rows]}


@router.post("/{project_id}/members", status_code=201)
def add_member(
    project_id: str, request: Request, db: DbSession, principal: Admin, payload: Annotated[Any, Body()] = None
) -> dict[str, Any]:
    body: MemberIn = parse_body(MemberIn, payload)
    role = Role.parse(body.role)
    _require_owner_for(principal, role)
    user = users.get_user_by_email(db, body.email)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    membership = Membership(user_id=user.id, project_id=principal.project_id, role=body.role)
    try:
        with db.begin_nested():
            db.add(membership)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="user is already a member") from None
    audit.record(
        db, action="member.add", project_id=principal.project_id, target=user.id,
        detail={"role": body.role, **request_detail(request)}, **_actor(principal),
    )
    return _member_out(user, membership)


@router.patch("/{project_id}/members/{user_id}")
def update_member(
    project_id: str,
    user_id: str,
    request: Request,
    db: DbSession,
    principal: Admin,
    payload: Annotated[Any, Body()] = None,
) -> dict[str, Any]:
    body: MemberPatch = parse_body(MemberPatch, payload)
    membership = _membership(db, principal.project_id, user_id, lock=True)
    current, new = Role.parse(membership.role), Role.parse(body.role)
    _require_owner_for(principal, current, new)
    if current == Role.OWNER and new != Role.OWNER:
        _ensure_other_owner(db, principal.project_id, user_id)
    previous = membership.role
    membership.role = body.role
    db.flush()
    audit.record(
        db, action="member.update", project_id=principal.project_id, target=user_id,
        detail={"from": previous, "to": body.role, **request_detail(request)}, **_actor(principal),
    )
    user = db.get(User, user_id)
    assert user is not None
    return _member_out(user, membership)


@router.delete("/{project_id}/members/{user_id}", status_code=204)
def remove_member(project_id: str, user_id: str, request: Request, db: DbSession, principal: Admin) -> Response:
    membership = _membership(db, principal.project_id, user_id, lock=True)
    current = Role.parse(membership.role)
    _require_owner_for(principal, current)
    if current == Role.OWNER:
        _ensure_other_owner(db, principal.project_id, user_id)
    # Only the membership goes: the user's sessions (other projects) and the keys they created stay.
    db.delete(membership)
    audit.record(
        db, action="member.remove", project_id=principal.project_id, target=user_id,
        detail={"role": membership.role, **request_detail(request)}, **_actor(principal),
    )
    return Response(status_code=204)


# --- API keys ------------------------------------------------------------------------------------------


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    scopes: list[ScopeName] = Field(min_length=1, max_length=3)


def _key_out(key: ApiKey) -> dict[str, Any]:
    return {
        "id": key.id,
        "prefix": key.prefix,
        "name": key.name,
        "scopes": sorted(key.scopes) if key.scopes is not None else ["ingest", "read", "write"],
        "created_at": iso(key.created_at),
        "created_by": key.created_by,
        "last_used_at": iso(key.last_used_at),
        "revoked_at": iso(key.revoked_at),
    }


@router.get("/{project_id}/keys")
def list_keys(project_id: str, db: DbSession, principal: Admin) -> dict[str, Any]:
    rows = db.scalars(
        select(ApiKey).where(ApiKey.project_id == principal.project_id).order_by(ApiKey.created_at, ApiKey.id)
    ).all()
    return {"items": [_key_out(k) for k in rows]}


@router.post("/{project_id}/keys", status_code=201)
def create_key(
    project_id: str, request: Request, db: DbSession, principal: Admin, payload: Annotated[Any, Body()] = None
) -> dict[str, Any]:
    body: KeyIn = parse_body(KeyIn, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    key, plaintext = issue_api_key(
        db, principal.project_id, name=name, scopes=body.scopes, created_by=principal.user_id
    )
    audit.record(
        db, action="key.create", project_id=principal.project_id, target=key.id,
        detail={"prefix": key.prefix, "name": name, "scopes": key.scopes, **request_detail(request)},
        **_actor(principal),
    )
    return {"id": key.id, "prefix": key.prefix, "api_key": plaintext}


@router.delete("/{project_id}/keys/{key_id}", status_code=204)
def revoke_key(project_id: str, key_id: str, request: Request, db: DbSession, principal: Admin) -> Response:
    key = db.scalars(select(ApiKey).where(ApiKey.id == key_id, ApiKey.project_id == principal.project_id)).first()
    if key is None:
        raise HTTPException(status_code=404, detail="key not found")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        audit.record(
            db, action="key.revoke", project_id=principal.project_id, target=key.id,
            detail={"prefix": key.prefix, **request_detail(request)}, **_actor(principal),
        )
    return Response(status_code=204)


# --- audit log --------------------------------------------------------------------------------------------


def _encode_cursor(row: AuditLog) -> str:
    raw = f"{iso(row.ts)}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(db: Session, cursor: str) -> tuple[datetime, str]:
    try:
        stamp, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        ts = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if db.get_bind().dialect.name == "sqlite":  # naive UTC on SQLite
        ts = ts.replace(tzinfo=None)
    return ts, row_id


@router.get("/{project_id}/audit-log")
def audit_log(
    project_id: str,
    db: DbSession,
    principal: Admin,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    """Newest first, keyset-paginated on (ts, id)."""
    stmt = select(AuditLog).where(AuditLog.project_id == principal.project_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if cursor:
        ts, row_id = _decode_cursor(db, cursor)
        stmt = stmt.where(or_(AuditLog.ts < ts, and_(AuditLog.ts == ts, AuditLog.id < row_id)))
    rows = list(db.scalars(stmt.order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1])
    items = [
        {
            "id": r.id,
            "ts": iso(r.ts),
            "actor": {"kind": r.actor_kind, "id": r.actor_id},
            "action": r.action,
            "target": r.target,
            "detail": r.detail or {},
        }
        for r in rows
    ]
    return {"items": items, "next_cursor": next_cursor}

