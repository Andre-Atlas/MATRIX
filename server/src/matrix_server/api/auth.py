"""User authentication API (contract G0 section 1): login, logout, me, password, sessions.

Bodies are validated by hand so a 422 never echoes the input (it may carry a password).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server import audit, users
from matrix_server.auth import (
    UserContext,
    client_ip,
    require_user,
    session_absolute_ttl,
    session_idle_ttl,
)
from matrix_server.storage.db import as_utc, get_db, utcnow
from matrix_server.storage.models import Membership, Project, User, WebSession

logger = logging.getLogger("matrix_server.auth")

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[UserContext, Depends(require_user())]

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

LOGIN_FAILED = "invalid email or password"


def parse_body(model: type[BaseModel], payload: Any) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_input=False, include_context=False, include_url=False)
        ) from exc


def iso(value: Any) -> str | None:
    value = as_utc(value)
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def user_out(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "created_at": iso(user.created_at),
        "last_login_at": iso(user.last_login_at),
    }


def memberships_out(db: Session, user_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Project.id, Project.name, Membership.role)
        .join(Membership, Membership.project_id == Project.id)
        .where(Membership.user_id == user_id)
        .order_by(Project.name, Project.id)
    ).all()
    return [{"project": {"id": pid, "name": name}, "role": role} for pid, name, role in rows]


def request_detail(request: Request) -> dict[str, Any]:
    return {"ip": client_ip(request), "user_agent": (request.headers.get("user-agent") or "")[:300] or None}


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str = Field(min_length=1, max_length=1024)
    password: str = Field(min_length=1, max_length=4096)


class PasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=4096)
    new_password: str = Field(min_length=1, max_length=4096)


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429, detail="too many failed attempts, try again later",
        headers={"Retry-After": str(retry_after)},
    )


@router.post("/login")
def login(request: Request, db: DbSession, payload: Annotated[Any, Body()] = None) -> dict[str, Any]:
    body: LoginIn = parse_body(LoginIn, payload)
    email = users.normalize_email(body.email)
    ip = client_ip(request)
    key = users.attempt_key(email, ip)
    retry_after = users.locked_for(db, key)
    if retry_after is not None:
        raise _too_many(retry_after)

    user = users.get_user_by_email(db, email)
    # Always exactly one argon2 verification (dummy hash for unknown users), so both paths cost
    # the same; over-long passwords are verified too and then rejected.
    ok = users.verify_password(user.password_hash if user else None, body.password)
    ok = ok and len(body.password) <= users.PASSWORD_MAX_CHARS
    if not ok or user is None:
        lock = users.register_failure(db, key)
        audit.record(
            db,
            action="auth.login_failed",
            actor_kind="user",
            actor_id=user.id if user else None,
            target=user.id if user else None,
            detail={"email": users.mask_email(email), **request_detail(request), "locked": lock is not None},
        )
        db.commit()
        logger.info("login failed for %s from %s", users.mask_email(email), ip)
        raise HTTPException(status_code=401, detail=LOGIN_FAILED, headers={"WWW-Authenticate": "Bearer"})

    users.clear_failures(db, key)
    users.rehash_if_needed(user, body.password)
    user.last_login_at = utcnow()
    token, web_session = users.create_session(
        db,
        user,
        absolute_ttl=session_absolute_ttl(request),
        user_agent=request.headers.get("user-agent"),
        ip=ip,
    )
    audit.record(
        db, action="auth.login", actor_kind="user", actor_id=user.id, target=user.id, detail=request_detail(request)
    )
    out = {
        "token": token,
        "expires_at": iso(web_session.expires_at),
        "user": user_out(user),
        "memberships": memberships_out(db, user.id),
    }
    db.commit()
    logger.info("login ok for user %s", user.id)
    return out


@router.post("/logout", status_code=204)
def logout(request: Request, db: DbSession, ctx: CurrentUser) -> Response:
    row = db.get(WebSession, ctx.session_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
    audit.record(
        db, action="auth.logout", actor_kind="user", actor_id=ctx.user.id, target=ctx.user.id,
        detail=request_detail(request),
    )
    return Response(status_code=204)


@router.get("/me")
def me(db: DbSession, ctx: CurrentUser) -> dict[str, Any]:
    return {"user": user_out(ctx.user), "memberships": memberships_out(db, ctx.user.id)}


@router.post("/password", status_code=204)
def change_password(
    request: Request, db: DbSession, ctx: CurrentUser, payload: Annotated[Any, Body()] = None
) -> Response:
    body: PasswordIn = parse_body(PasswordIn, payload)
    user = db.get(User, ctx.user.id)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or missing credentials")
    key = users.attempt_key(user.email, client_ip(request))
    retry_after = users.locked_for(db, key)
    if retry_after is not None:
        raise _too_many(retry_after)
    if not users.verify_password(user.password_hash, body.current_password):
        users.register_failure(db, key)
        audit.record(
            db, action="auth.password_change_failed", actor_kind="user", actor_id=user.id, target=user.id,
            detail=request_detail(request),
        )
        db.commit()
        raise HTTPException(status_code=403, detail="current password is incorrect")
    try:
        users.set_password(db, user, body.new_password)
    except users.UserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    users.clear_failures(db, key)
    revoked = users.revoke_user_sessions(db, user.id, except_id=ctx.session_id)
    audit.record(
        db, action="auth.password_change", actor_kind="user", actor_id=user.id, target=user.id,
        detail={**request_detail(request), "revoked_sessions": revoked},
    )
    return Response(status_code=204)


def _session_out(row: WebSession, current_id: str) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": iso(row.created_at),
        "last_seen_at": iso(row.last_seen_at),
        "expires_at": iso(row.expires_at),
        "user_agent": row.user_agent,
        "ip": row.ip,
        "current": row.id == current_id,
    }


@router.get("/sessions")
def list_sessions(request: Request, db: DbSession, ctx: CurrentUser) -> dict[str, Any]:
    rows = users.active_sessions(db, ctx.user.id, session_idle_ttl(request))
    return {"items": [_session_out(r, ctx.session_id) for r in rows]}


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(session_id: str, request: Request, db: DbSession, ctx: CurrentUser) -> Response:
    row = db.get(WebSession, session_id) if len(session_id) == 64 else None
    if row is None or row.user_id != ctx.user.id or row.revoked_at is not None:
        raise HTTPException(status_code=404, detail="session not found")
    row.revoked_at = utcnow()
    audit.record(
        db, action="auth.session_revoke", actor_kind="user", actor_id=ctx.user.id, target=ctx.user.id,
        detail=request_detail(request),
    )
    return Response(status_code=204)
