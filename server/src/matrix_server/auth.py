"""Project and User authentication (ADR-004) and RBAC.

Authentication methods:
1. API Key: `mx_<prefix>_<secret>` in `Authorization: Bearer ...` or `x-matrix-api-key: ...`.
   Full programmatic access to the associated project (role: admin).
2. User JWT: `Authorization: Bearer <jwt>` or session cookie.
   Scoped to projects where the user is an active member, with role:
   - admin: full project control, member invites/roles, key generation/revocation.
   - member: create/edit prompts, datasets, experiments, annotations, rules.
   - viewer: read-only access to traces, sessions, evals, prompts, and datasets.

Routes depend on `current_project_id` or `current_auth_context`.
Tests may override with `app.dependency_overrides[current_project_id] = lambda: "p1"`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.storage.db import as_utc, utcnow
from matrix_server.storage.models import ApiKey, Project, ProjectMember, User

KEY_SCHEME = "mx"
PREFIX_BYTES = 4  # 8 hex chars
SECRET_BYTES = 32  # 43 url-safe chars
LAST_USED_RESOLUTION = timedelta(minutes=1)
API_KEY_HEADER = "x-matrix-api-key"
PROJECT_ID_HEADER = "x-matrix-project-id"

ROLE_HIERARCHY = {"viewer": 1, "member": 2, "admin": 3}

_UNAUTHORIZED = "invalid or missing API key"


class ProjectExistsError(ValueError):
    pass


class UserExistsError(ValueError):
    pass


@dataclass
class AuthContext:
    project_id: str
    role: str  # "admin", "member", "viewer"
    user_id: str | None = None
    user_email: str | None = None
    is_api_key: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# --- API Key helpers ---


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


# --- Password & JWT helpers ---


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"pbkdf2:sha256:100000${salt}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        method, salt, expected_hex = hashed.split("$", 2)
        if not method.startswith("pbkdf2:sha256:"):
            return False
        iterations = int(method.split(":")[2])
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
        return hmac.compare_digest(dk.hex(), expected_hex)
    except (ValueError, TypeError, KeyError):
        return False


def _b64encode_str(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode_str(s: str) -> bytes:
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def create_jwt(payload: dict[str, Any], secret: str, expires_in_seconds: int = 72 * 3600) -> str:
    now = int(datetime.now(UTC).timestamp())
    full_payload = {**payload, "iat": now, "exp": now + expires_in_seconds}
    header = {"alg": "HS256", "typ": "JWT"}
    h_b64 = _b64encode_str(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = _b64encode_str(json.dumps(full_payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    s_b64 = _b64encode_str(sig)
    return f"{h_b64}.{p_b64}.{s_b64}"


def decode_jwt(token: str, secret: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    h_b64, p_b64, s_b64 = parts
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        provided_sig = _b64decode_str(s_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected_sig, provided_sig):
        return None
    try:
        payload_bytes = _b64decode_str(p_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    now = int(datetime.now(UTC).timestamp())
    exp = payload.get("exp")
    if exp is not None and now > exp:
        return None
    return payload


# --- Extraction & Authentication Dependencies ---


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    header = request.headers.get(API_KEY_HEADER)
    if header and header.strip():
        return header.strip()
    cookie = request.cookies.get("matrix_session")
    if cookie and cookie.strip():
        return cookie.strip()
    return None


def current_auth_context(request: Request) -> AuthContext:
    if current_project_id in request.app.dependency_overrides:
        override_pid = request.app.dependency_overrides[current_project_id]()
        return AuthContext(project_id=override_pid, role="admin", is_api_key=True)

    raw_token = _extract_token(request)
    if raw_token is None:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})

    session: Session = request.app.state.session_factory()
    try:
        # 1. Try API Key format
        if raw_token.startswith(f"{KEY_SCHEME}_"):
            project = authenticate(session, raw_token)
            if project is None:
                raise HTTPException(status_code=401, detail=_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})
            session.commit()
            return AuthContext(project_id=project.id, role="admin", is_api_key=True)

        # 2. Try User JWT
        secret = getattr(request.app.state.settings, "jwt_secret", "matrix-insecure-dev-secret-change-in-prod")
        payload = decode_jwt(raw_token, secret)
        if payload is None:
            raise HTTPException(status_code=401, detail=_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail=_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})

        user = session.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail=_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})

        # Target project from header, query param, or first active membership
        target_pid = (
            request.headers.get(PROJECT_ID_HEADER)
            or request.query_params.get("project_id")
        )
        if target_pid:
            membership = session.scalar(
                select(ProjectMember).where(
                    ProjectMember.user_id == user.id, ProjectMember.project_id == target_pid
                )
            )
            if membership is None:
                raise HTTPException(status_code=403, detail="not a member of this project")
        else:
            membership = session.scalar(
                select(ProjectMember).where(ProjectMember.user_id == user.id).order_by(ProjectMember.created_at.asc())
            )
            if membership is None:
                raise HTTPException(status_code=403, detail="user has no assigned projects")

        session.commit()
        return AuthContext(
            project_id=membership.project_id,
            role=membership.role,
            user_id=user.id,
            user_email=user.email,
            is_api_key=False,
        )
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_project_id(request: Request) -> str:
    ctx = current_auth_context(request)
    return ctx.project_id


def require_role(min_role: str):
    def dependency(request: Request) -> AuthContext:
        ctx = current_auth_context(request)
        user_level = ROLE_HIERARCHY.get(ctx.role, 0)
        req_level = ROLE_HIERARCHY.get(min_role, 0)
        if user_level < req_level:
            raise HTTPException(
                status_code=403,
                detail=f"role '{ctx.role}' insufficient; '{min_role}' required",
            )
        return ctx

    return dependency


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

