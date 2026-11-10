"""Project authentication by API key (ADR-004) and admin token.

Key format: `mx_<prefix>_<secret>`. Only the SHA-256 of the full key is stored;
lookup is by prefix and comparison uses `hmac.compare_digest`.

Routes depend on `current_project_id`; tests may override it with
`app.dependency_overrides[current_project_id] = lambda: "p1"`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import IntEnum
from typing import Literal

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.storage.db import as_utc, utcnow
from matrix_server.storage.models import ApiKey, Project

KEY_SCHEME = "mx"
PREFIX_BYTES = 4  # 8 hex chars
SECRET_BYTES = 32  # 43 url-safe chars
LAST_USED_RESOLUTION = timedelta(minutes=1)
API_KEY_HEADER = "x-matrix-api-key"

_UNAUTHORIZED = "invalid or missing API key"


class ProjectExistsError(ValueError):
    pass


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Return (prefix, plaintext_key). The prefix is hex, so it never contains `_`."""
    prefix = secrets.token_hex(PREFIX_BYTES)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return prefix, f"{KEY_SCHEME}_{prefix}_{secret}"


def parse_prefix(raw_key: str) -> str | None:
    parts = raw_key.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_SCHEME or not parts[1] or not parts[2]:
        return None
    return parts[1]


def create_api_key(session: Session, project_id: str) -> str:
    prefix, plaintext = generate_api_key()
    session.add(ApiKey(project_id=project_id, prefix=prefix, key_hash=hash_key(plaintext)))
    session.flush()
    return plaintext


def create_project(session: Session, name: str) -> tuple[Project, str]:
    if session.scalar(select(Project.id).where(Project.name == name)) is not None:
        raise ProjectExistsError(name)
    project = Project(name=name)
    session.add(project)
    session.flush()
    return project, create_api_key(session, project.id)


def revoke_api_key(session: Session, prefix: str) -> bool:
    key = session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None:
        return False
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        session.flush()
    return True


def authenticate(session: Session, raw_key: str) -> Project | None:
    prefix = parse_prefix(raw_key) if raw_key else None
    if prefix is None:
        return None
    key = session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None:
        # Spend comparable work so unknown prefixes are not cheaper to probe.
        hmac.compare_digest(hash_key(raw_key), "0" * 64)
        return None
    if not hmac.compare_digest(hash_key(raw_key), key.key_hash):
        return None
    if key.revoked_at is not None:
        return None
    now = utcnow()
    last = as_utc(key.last_used_at)
    if last is None or now - last >= LAST_USED_RESOLUTION:
        key.last_used_at = now
        session.flush()
    return key.project


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    header = request.headers.get(API_KEY_HEADER)
    if header and header.strip():
        return header.strip()
    return None


def current_project_id(request: Request) -> str:
    raw_key = _extract_key(request)
    if raw_key is None:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})
    session: Session = request.app.state.session_factory()
    try:
        project = authenticate(session, raw_key)
        project_id = project.id if project is not None else None
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    if project_id is None:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})
    return project_id


def require_admin(request: Request) -> None:
    expected = request.app.state.settings.admin_token
    if not expected:
        raise HTTPException(status_code=403, detail="admin API disabled")
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    provided = value.strip() if scheme.lower() == "bearer" else ""
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=401, detail="invalid admin token", headers={"WWW-Authenticate": "Bearer"}
        )


# --- RBAC skeleton (contract: docs/atlas/2026-11-10-plataforma-completa/contracts/g0.md) ---------
# Interface shared by every router. T21 replaces the body of `require` with the full
# implementation (user sessions, memberships, key scopes); routers only depend on the signature.


class Role(IntEnum):
    VIEWER = 1
    MEMBER = 2
    ADMIN = 3
    OWNER = 4


@dataclass(frozen=True)
class Principal:
    project_id: str
    kind: Literal["api_key", "user"]
    user_id: str | None
    role: Role
    scopes: frozenset[str]


def require(min_role: Role = Role.VIEWER, *, scope: str | None = None) -> Callable[..., Principal]:
    """FastAPI dependency factory: authenticate and require at least `min_role`."""

    def dependency(request: Request) -> Principal:
        project_id = current_project_id(request)
        # Interim behaviour until T21: API keys are trusted as members with every scope.
        role = Role.MEMBER
        if min_role > role:
            raise HTTPException(status_code=403, detail=f"requires role {min_role.name.lower()}")
        return Principal(project_id, "api_key", None, role, frozenset({"ingest", "read", "write"}))

    return dependency
