"""Audit log writer. `detail` must never carry secrets, request bodies or passwords."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from matrix_server.storage.models import AuditLog

_FORBIDDEN_KEYS = {"password", "token", "secret", "api_key", "authorization", "body", "content"}


def record(
    session: Session,
    *,
    action: str,
    project_id: str | None = None,
    actor_kind: str = "system",
    actor_id: str | None = None,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit row to the caller's session (the caller commits)."""
    clean = {k: v for k, v in (detail or {}).items() if k.lower() not in _FORBIDDEN_KEYS} or None
    row = AuditLog(
        project_id=project_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        action=action[:64],
        target=target[:200] if target else None,
        detail=clean,
    )
    session.add(row)
    return row
