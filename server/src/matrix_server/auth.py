"""Authentication and RBAC for `/api/v1/*` and `/v1/traces` (ADR-004, ADR-006, contract G0 §1).

Credentials:
- API key `mx_<prefix>_<secret>` in `Authorization: Bearer` (or the legacy `x-matrix-api-key`
  header). Only the SHA-256 is stored; lookup by prefix, comparison by `hmac.compare_digest`.
  The project comes from the key; its power from its scopes (`ingest`, `read`, `write`).
- User session `mxs_<token>` in `Authorization: Bearer`, plus `X-Matrix-Project: <project_id>`.
  The user's membership role in that project is the power.

Every route declares its minimum with one dependency: `require(min_role, scope=...)` for project
routes (project from the key; for a session from `X-Matrix-Project`, or the `{project_id}` path
parameter of `/api/v1/projects/{project_id}/...`), or `require_user()` for session-only routes
without a project. Semantics of `require`:

- user: membership role >= `min_role`; when `scope` is given it must also be in the role's scopes
  (viewer: read; member and above: ingest, read, write).
- API key: never passes `min_role >= ADMIN`. With `scope` given, the key must hold that scope (the
  role check is replaced by it: `require(Role.MEMBER, scope="ingest")` admits an ingest-only key
  and any member). Without `scope`, the role derived from the key's scopes must reach `min_role`
  (`write` -> member, which includes read; `read` -> viewer; `ingest` alone -> no role).

Errors: 401 (generic message, `WWW-Authenticate: Bearer`) for a missing/invalid/expired credential;
400 when a session omits `X-Matrix-Project`; 404 when the user is not a member of the project (or a
key is used against another project); 403 `requires role <r>` / `requires scope <s>` otherwise.

Tests may override a dependency by identity: `require(...)` is cached per arguments, so
`app.dependency_overrides[require(Role.MEMBER, scope="ingest")] = lambda: Principal(...)` works.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import IntEnum
from functools import cache
from typing import Literal

from fastapi import Depends, HTTPException, Request
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from matrix_server import users
from matrix_server.storage.db import as_utc, utcnow
from matrix_server.storage.models import API_KEY_SCOPES, ApiKey, Membership, Project, User, WebSession

KEY_SCHEME = "mx"
PREFIX_BYTES = 4  # 8 hex chars
SECRET_BYTES = 32  # 43 url-safe chars
LAST_USED_RESOLUTION = timedelta(minutes=1)
API_KEY_HEADER = "x-matrix-api-key"
PROJECT_HEADER = "x-matrix-project"

UNAUTHORIZED_DETAIL = "invalid or missing credentials"


class ProjectExistsError(ValueError):
    pass


# --- roles, principals -------------------------------------------------------------------------


class Role(IntEnum):
    VIEWER = 1
    MEMBER = 2
    ADMIN = 3
    OWNER = 4

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def parse(cls, value: str | None) -> Role | None:
        if not value:
            return None
        try:
            return cls[value.upper()]
        except KeyError:
            return None


ROLE_NAMES = tuple(r.label for r in Role)
ALL_SCOPES = frozenset(API_KEY_SCOPES)


def role_scopes(role: Role) -> frozenset[str]:
    return frozenset({"read"}) if role == Role.VIEWER else ALL_SCOPES


def key_scopes(stored: Iterable[str] | None) -> frozenset[str]:
    """Effective scopes of a key: unknown values dropped, `write` implies `read`; NULL = all."""
    if stored is None:
        return ALL_SCOPES
    scopes = {s for s in stored if s in ALL_SCOPES}
    if "write" in scopes:
        scopes.add("read")
    return frozenset(scopes)


def key_role(scopes: frozenset[str]) -> Role | None:
    if "write" in scopes:
        return Role.MEMBER
    if "read" in scopes:
        return Role.VIEWER
    return None


@dataclass(frozen=True)
class Principal:
    project_id: str
    kind: Literal["api_key", "user"]
    user_id: str | None
    role: Role  # key: VIEWER (read) or MEMBER (write), VIEWER for ingest-only keys; never ADMIN
    scopes: frozenset[str]
    key_id: str | None = None

    @property
    def actor_kind(self) -> str:
        return self.kind

    @property
    def actor_id(self) -> str | None:
        return self.user_id if self.kind == "user" else self.key_id


@dataclass(frozen=True)
class UserContext:
    """Result of `require_user()`: the logged-in user and the current session id (token hash)."""

    user: User
    session_id: str


@dataclass(frozen=True)
class RbacSpec:
    """Marker attached to every RBAC dependency (`dependency.rbac`); tests use it to prove that
    every mutating route declares one."""

    min_role: Role | None
    scope: str | None
    project_from: Literal["project", "none"]


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=UNAUTHORIZED_DETAIL, headers={"WWW-Authenticate": "Bearer"})


def _project_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="project not found")


def check_access(
    kind: str, role: Role | None, scopes: frozenset[str], min_role: Role, scope: str | None = None
) -> None:
    """Raise 403 unless a principal of `kind` with `role`/`scopes` meets `min_role`/`scope`."""
    if kind == "api_key":
        if min_role >= Role.ADMIN:
            raise HTTPException(status_code=403, detail=f"requires role {min_role.label}")
        if scope is not None:
            if scope not in scopes:
                raise HTTPException(status_code=403, detail=f"requires scope {scope}")
            return
        if role is None or role < min_role:
            raise HTTPException(status_code=403, detail=f"requires role {min_role.label}")
        return
    if role is None or role < min_role:
        raise HTTPException(status_code=403, detail=f"requires role {min_role.label}")
    if scope is not None and scope not in scopes:
        raise HTTPException(status_code=403, detail=f"requires scope {scope}")


def ensure_role(principal: Principal, min_role: Role) -> None:
    """In-handler check for data-dependent requirements (e.g. moving the `production` label)."""
    role = key_role(principal.scopes) if principal.kind == "api_key" else principal.role
    check_access(principal.kind, role, principal.scopes, min_role)


# --- API keys ------------------------------------------------------------------------------------


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


def validate_scopes(scopes: Iterable[str]) -> list[str]:
    values = list(dict.fromkeys(scopes))
    if not values or any(s not in ALL_SCOPES for s in values):
        raise ValueError(f"scopes must be a non-empty subset of {sorted(ALL_SCOPES)}")
    return sorted(values)


def issue_api_key(
    session: Session,
    project_id: str,
    *,
    name: str | None = None,
    scopes: Iterable[str] | None = None,
    created_by: str | None = None,
) -> tuple[ApiKey, str]:
    prefix, plaintext = generate_api_key()
    row = ApiKey(
        project_id=project_id,
        prefix=prefix,
        key_hash=hash_key(plaintext),
        name=name,
        scopes=validate_scopes(scopes) if scopes is not None else list(API_KEY_SCOPES),
        created_by=created_by,
    )
    session.add(row)
    session.flush()
    return row, plaintext


def create_api_key(
    session: Session,
    project_id: str,
    *,
    name: str | None = None,
    scopes: Iterable[str] | None = None,
    created_by: str | None = None,
) -> str:
    return issue_api_key(session, project_id, name=name, scopes=scopes, created_by=created_by)[1]


def create_project(
    session: Session, name: str, *, owner_user_id: str | None = None, with_key: bool = True
) -> tuple[Project, str | None]:
    """Create a project, optionally its owner membership and its first key (all scopes)."""
    if session.scalar(select(Project.id).where(Project.name == name)) is not None:
        raise ProjectExistsError(name)
    project = Project(name=name)
    session.add(project)
    session.flush()
    if owner_user_id is not None:
        session.add(Membership(user_id=owner_user_id, project_id=project.id, role=Role.OWNER.label))
        session.flush()
    key = create_api_key(session, project.id, name="initial", created_by=owner_user_id) if with_key else None
    return project, key


def revoke_api_key(session: Session, prefix: str) -> bool:
    key = session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None:
        return False
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        session.flush()
    return True


def authenticate_key(session: Session, raw_key: str) -> ApiKey | None:
    """The active key matching `raw_key` (updating `last_used_at` at most once a minute), or None."""
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
    return key


def authenticate(session: Session, raw_key: str) -> Project | None:
    key = authenticate_key(session, raw_key)
    return key.project if key is not None else None


def require_admin(request: Request) -> None:
    """Platform admin (`MATRIX_ADMIN_TOKEN`), used by `/api/v1/admin/*` only."""
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


# --- request credentials -------------------------------------------------------------------------


def _credential(request: Request) -> tuple[Literal["api_key", "session"], str] | None:
    auth = request.headers.get("authorization")
    if auth:
        scheme, _, value = auth.partition(" ")
        value = value.strip()
        if scheme.lower() == "bearer" and value:
            return ("session" if users.is_session_token(value) else "api_key"), value
    header = request.headers.get(API_KEY_HEADER)
    if header and header.strip():
        return "api_key", header.strip()
    return None


def client_ip(request: Request) -> str | None:
    """Client address for throttling and audit: the socket peer, or the value of
    `settings.trusted_proxy_header` when configured (rightmost entry of a comma-separated list,
    i.e. the address the trusted proxy saw). An absent or invalid header falls back to the peer."""
    peer = request.client.host if request.client else None
    settings = getattr(request.app.state, "settings", None)
    header = getattr(settings, "trusted_proxy_header", None)
    if header:
        raw = request.headers.get(header)
        if raw:
            candidate = raw.split(",")[-1].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    return peer


def _settings_ttls(request: Request) -> tuple[timedelta, timedelta]:
    settings = request.app.state.settings
    return (
        timedelta(seconds=settings.session_idle_ttl_seconds),
        timedelta(seconds=settings.session_absolute_ttl_seconds),
    )


def session_absolute_ttl(request: Request) -> timedelta:
    return _settings_ttls(request)[1]


def session_idle_ttl(request: Request) -> timedelta:
    return _settings_ttls(request)[0]


def _requested_project(request: Request) -> tuple[str | None, bool]:
    """(project, conflict): `X-Matrix-Project`, else the `{project_id}` path parameter of routes
    under `/api/v1/projects/{project_id}/...`; `conflict` when both are present and differ (404,
    raised only after the credential is verified so a bad credential still gets 401)."""
    header = (request.headers.get(PROJECT_HEADER) or "").strip() or None
    path = request.path_params.get("project_id")
    conflict = header is not None and path is not None and header != path
    return header or path, conflict


def _resolve(request: Request, spec: RbacSpec) -> Principal:
    assert spec.min_role is not None
    credential = _credential(request)
    if credential is None:
        raise _unauthorized()
    kind, raw = credential
    project_id, conflict = _requested_project(request)
    db: Session = request.app.state.session_factory()
    try:
        if kind == "api_key":
            key = authenticate_key(db, raw)
            key_values = (key.id, key.project_id, key.scopes) if key is not None else None
            db.commit()
            if key_values is None:
                raise _unauthorized()
            key_id, key_project, stored_scopes = key_values
            if conflict or (project_id is not None and project_id != key_project):
                raise _project_not_found()
            scopes = key_scopes(stored_scopes)
            role = key_role(scopes)
            check_access("api_key", role, scopes, spec.min_role, spec.scope)
            principal = Principal(key_project, "api_key", None, role or Role.VIEWER, scopes, key_id)
        else:
            idle_ttl, _ = _settings_ttls(request)
            token_hash = users.hash_token(raw)
            role_name: str | None = None
            if project_id is not None:  # session and membership in one round-trip
                row = db.execute(
                    select(WebSession, Membership.role)
                    .outerjoin(
                        Membership,
                        and_(Membership.user_id == WebSession.user_id, Membership.project_id == project_id),
                    )
                    .where(WebSession.id == token_hash)
                ).first()
                web_session, role_name = row if row is not None else (None, None)
            else:
                web_session = db.get(WebSession, token_hash)
            now = utcnow()
            if web_session is None or not users.session_is_active(web_session, now, idle_ttl):
                raise _unauthorized()
            user_id = web_session.user_id
            if users.touch_session(web_session, now):
                db.commit()
            if project_id is None:
                raise HTTPException(status_code=400, detail="X-Matrix-Project required")
            if conflict:
                raise _project_not_found()
            role = Role.parse(role_name)
            if role is None:
                raise _project_not_found()
            scopes = role_scopes(role)
            check_access("user", role, scopes, spec.min_role, spec.scope)
            principal = Principal(project_id, "user", user_id, role, scopes)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    request.state.principal = principal
    return principal


@cache
def require(min_role: Role = Role.VIEWER, *, scope: str | None = None) -> Callable[..., Principal]:
    """FastAPI dependency factory: authenticate and require at least `min_role` (and `scope`) in the
    project owning the key, or named by `X-Matrix-Project` / the `{project_id}` path parameter."""
    if scope is not None and scope not in ALL_SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    spec = RbacSpec(Role(min_role), scope, "project")

    def dependency(request: Request) -> Principal:
        return _resolve(request, spec)

    dependency.rbac = spec  # type: ignore[attr-defined]
    dependency.__name__ = f"require_{spec.min_role.label}" + (f"_{scope}" if scope else "")
    return dependency


def _resolve_user(request: Request) -> UserContext:
    credential = _credential(request)
    if credential is None:
        raise _unauthorized()
    kind, raw = credential
    if kind != "session":
        if kind == "api_key":
            raise HTTPException(status_code=403, detail="requires a user session")
        raise _unauthorized()
    idle_ttl, _ = _settings_ttls(request)
    db: Session = request.app.state.session_factory()
    try:
        row = db.execute(
            select(WebSession, User)
            .join(User, User.id == WebSession.user_id)
            .where(WebSession.id == users.hash_token(raw))
        ).first()
        now = utcnow()
        if row is None or not users.session_is_active(row[0], now, idle_ttl):
            raise _unauthorized()
        web_session, user = row
        if users.touch_session(web_session, now):
            db.commit()
        db.expunge(user)
        return UserContext(user=user, session_id=web_session.id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@cache
def require_user() -> Callable[..., UserContext]:
    """Session-only routes without a project (`/api/v1/auth/*`, `/api/v1/projects` list/create)."""

    def dependency(request: Request) -> UserContext:
        return _resolve_user(request)

    dependency.rbac = RbacSpec(None, None, "none")  # type: ignore[attr-defined]
    dependency.__name__ = "require_user"
    return dependency


@cache
def project_of(min_role: Role = Role.VIEWER, *, scope: str | None = None) -> Callable[..., str]:
    """`require(...)` narrowed to the project id, for handlers that only need the project."""
    dep = require(min_role, scope=scope)

    def project_id(principal: Principal = Depends(dep)) -> str:  # noqa: B008 (closure over dep)
        return principal.project_id

    project_id.__name__ = f"project_of_{dep.__name__}"
    return project_id
