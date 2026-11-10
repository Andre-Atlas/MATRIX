"""Project lifecycle helpers shared by the API and the worker (contract G0 section 1)."""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from matrix_server.storage import jobs
from matrix_server.storage.db import Base
from matrix_server.storage.models import Project

PURGE_SPANS_JOB = "project.purge_spans"
# Kept after a project is deleted: the audit trail outlives the project.
_RETAINED_TABLES = {"audit_log"}


def delete_project(session: Session, project_id: str) -> None:
    """Delete every relational row of the project (every table with a `project_id` column, in
    reverse dependency order, so tables added by later modules are covered too) and schedule the
    removal of its spans. Artifact blobs are left to the artifact GC (no row references them)."""
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in _RETAINED_TABLES or "project_id" not in table.c:
            continue
        session.execute(delete(table).where(table.c.project_id == project_id))
    session.execute(delete(Project.__table__).where(Project.__table__.c.id == project_id))
    jobs.enqueue(session, project_id, PURGE_SPANS_JOB, {"project_id": project_id}, max_attempts=10)
