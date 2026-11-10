"""Test helpers for users, memberships and session headers (G0 RBAC).

Users are inserted directly with one precomputed argon2 hash and sessions are created without a
login, so fixtures stay fast; tests of the login flow itself go through `/api/v1/auth/login`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from matrix_server import users
from matrix_server.auth import Role
from matrix_server.storage.models import Membership, User

PASSWORD = "correct horse battery staple"
_hash: str | None = None


def password_hash() -> str:
    global _hash
    if _hash is None:
        _hash = users.hash_password(PASSWORD)
    return _hash


def make_user(session_factory, email: str | None = None, name: str = "Test User") -> str:
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    with session_factory() as s:
        user = User(email=users.normalize_email(email), name=name, password_hash=password_hash())
        s.add(user)
        s.commit()
        return user.id


def add_membership(session_factory, user_id: str, project_id: str, role: Role | str) -> None:
    label = role.label if isinstance(role, Role) else role
    with session_factory() as s:
        s.add(Membership(user_id=user_id, project_id=project_id, role=label))
        s.commit()


def session_token(session_factory, user_id: str, ttl: timedelta = timedelta(days=30)) -> str:
    with session_factory() as s:
        user = s.get(User, user_id)
        token, _ = users.create_session(s, user, absolute_ttl=ttl, user_agent="pytest", ip="testclient")
        s.commit()
        return token


def session_headers(session_factory, user_id: str, project_id: str | None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {session_token(session_factory, user_id)}"}
    if project_id is not None:
        headers["X-Matrix-Project"] = project_id
    return headers


def member_headers(session_factory, project_id: str, role: Role | str) -> dict[str, str]:
    """A new user with `role` in the project, and session headers for it."""
    user_id = make_user(session_factory)
    add_membership(session_factory, user_id, project_id, role)
    return session_headers(session_factory, user_id, project_id)


def owner_project(client, name: str, admin_headers: dict[str, str]) -> dict[str, str]:
    """Create a project through the admin API with a new owner; return the owner's session headers.

    Owners hold every role and scope (admin routes included), so fixtures written for project-wide
    API keys keep working on admin-only routes such as rules and online evaluations."""
    factory = client.app.state.session_factory
    email = f"owner-{uuid.uuid4().hex[:10]}@example.com"
    user_id = make_user(factory, email)
    resp = client.post("/api/v1/admin/projects", json={"name": name, "owner_email": email}, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    return session_headers(factory, user_id, resp.json()["project"]["id"])
