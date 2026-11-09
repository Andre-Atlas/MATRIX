"""Annotation queue helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server.storage.db import utcnow
from matrix_server.storage.models import Annotation


def _pending(session: Session, project_id: str, trace_id: str) -> Annotation | None:
    return session.scalars(
        select(Annotation).where(
            Annotation.project_id == project_id,
            Annotation.dedup_key == trace_id,
            Annotation.status == "pending",
        )
    ).first()


def open_annotation(session: Session, project_id: str, trace_id: str, reason: str | None) -> Annotation:
    """Return the pending annotation for the trace, creating it if there is none.

    SELECT-then-INSERT inside the caller's transaction; the unique `(project_id, dedup_key)`
    constraint catches a concurrent insert, in which case the winner's row is returned.
    """
    existing = _pending(session, project_id, trace_id)
    if existing is not None:
        return existing
    annotation = Annotation(
        project_id=project_id, trace_id=trace_id, reason=reason, status="pending", dedup_key=trace_id
    )
    try:
        with session.begin_nested():
            session.add(annotation)
            session.flush()
    except IntegrityError:
        existing = _pending(session, project_id, trace_id)
        if existing is None:
            raise
        return existing
    return annotation


def complete_annotation(session: Session, annotation: Annotation) -> Annotation:
    """Mark an annotation done and release its dedup slot so the trace can be queued again."""
    annotation.status = "done"
    annotation.dedup_key = None
    annotation.completed_at = utcnow()
    session.flush()
    return annotation
