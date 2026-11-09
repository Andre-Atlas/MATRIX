"""Job queue on the relational store (ADR-003).

PostgreSQL claims with `SELECT ... FOR UPDATE SKIP LOCKED`; SQLite (tests/dev) with a conditional
`UPDATE ... WHERE status = <observed>` whose rowcount tells whether this worker won the race.
A `running` job whose lease (`locked_at + lease_seconds`) expired is claimed again; once it has used
all its attempts it is marked `failed` instead. Delivery is at-least-once: handlers must be idempotent.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.storage.db import utcnow
from matrix_server.storage.models import Job

MAX_ERROR_CHARS = 2000
_SQLITE_CANDIDATES = 10


def _truncate(error: str | None) -> str | None:
    return None if error is None else error[:MAX_ERROR_CHARS]


def enqueue(
    session: Session,
    project_id: str,
    kind: str,
    payload: dict[str, Any],
    run_after: datetime | None = None,
    max_attempts: int = 3,
) -> Job:
    """Add a job in the caller's transaction (it becomes visible to workers on commit)."""
    job = Job(
        project_id=project_id,
        kind=kind,
        payload=payload,
        status="queued",
        attempts=0,
        max_attempts=max_attempts,
        run_after=run_after or utcnow(),
    )
    session.add(job)
    session.flush()
    return job


def _claimable(now: datetime, lease_seconds: float, kinds: list[str] | None):
    cutoff = now - timedelta(seconds=lease_seconds)
    cond = or_(
        and_(Job.status == "queued", Job.run_after <= now),
        and_(Job.status == "running", Job.locked_at < cutoff, Job.attempts < Job.max_attempts),
    )
    if kinds:
        cond = and_(cond, Job.kind.in_(kinds))
    return cond


def _expire_exhausted(session: Session, now: datetime, lease_seconds: float) -> None:
    cutoff = now - timedelta(seconds=lease_seconds)
    session.execute(
        update(Job)
        .where(Job.status == "running", Job.locked_at < cutoff, Job.attempts >= Job.max_attempts)
        .values(status="failed", error="lease expired after last attempt", finished_at=now)
        .execution_options(synchronize_session=False)
    )


def claim(
    session_factory: sessionmaker[Session],
    worker_id: str,
    kinds: list[str] | None = None,
    lease_seconds: float = 300,
) -> Job | None:
    """Atomically take the oldest runnable job (or an expired lease) and mark it `running`."""
    with session_factory() as session:
        now = utcnow()
        with session.begin():
            _expire_exhausted(session, now, lease_seconds)
        if session.get_bind().dialect.name == "postgresql":
            return _claim_postgres(session, worker_id, now, lease_seconds, kinds)
        return _claim_conditional(session, worker_id, now, lease_seconds, kinds)


def _claim_postgres(
    session: Session, worker_id: str, now: datetime, lease_seconds: float, kinds: list[str] | None
) -> Job | None:
    with session.begin():
        job = session.scalars(
            select(Job)
            .where(_claimable(now, lease_seconds, kinds))
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if job is None:
            return None
        job.status = "running"
        job.locked_by = worker_id
        job.locked_at = now
        job.attempts += 1
    return job


def _claim_conditional(
    session: Session, worker_id: str, now: datetime, lease_seconds: float, kinds: list[str] | None
) -> Job | None:
    candidates = session.scalars(
        select(Job.id)
        .where(_claimable(now, lease_seconds, kinds))
        .order_by(Job.created_at)
        .limit(_SQLITE_CANDIDATES)
    ).all()
    session.rollback()
    for job_id in candidates:
        with session.begin():
            result = session.execute(
                update(Job)
                .where(Job.id == job_id, _claimable(now, lease_seconds, kinds))
                .values(status="running", locked_by=worker_id, locked_at=now, attempts=Job.attempts + 1)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                continue
        return session.get(Job, job_id, populate_existing=True)
    return None


def mark_done(session: Session, job_id: str, worker_id: str | None = None) -> bool:
    """Mark a running job done in the caller's transaction; False if it is no longer ours."""
    cond = [Job.id == job_id, Job.status == "running"]
    if worker_id is not None:
        cond.append(Job.locked_by == worker_id)
    result = session.execute(
        update(Job)
        .where(*cond)
        .values(status="done", finished_at=utcnow(), error=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def complete(session_factory: sessionmaker[Session], job_id: str, worker_id: str | None = None) -> bool:
    with session_factory() as session, session.begin():
        return mark_done(session, job_id, worker_id)


def fail(
    session_factory: sessionmaker[Session],
    job_id: str,
    error: str,
    retry: bool = True,
    worker_id: str | None = None,
) -> bool:
    """Record a failure: back to `queued` after `2**attempts` seconds while attempts remain,
    otherwise (or with `retry=False`) `failed`. False if the job is no longer ours."""
    with session_factory() as session, session.begin():
        job = session.scalars(
            select(Job).where(Job.id == job_id).with_for_update()
        ).first()
        if job is None or job.status != "running":
            return False
        if worker_id is not None and job.locked_by != worker_id:
            return False
        now = utcnow()
        job.error = _truncate(error)
        if retry and job.attempts < job.max_attempts:
            job.status = "queued"
            job.run_after = now + timedelta(seconds=2**job.attempts)
            job.locked_by = None
            job.locked_at = None
        else:
            job.status = "failed"
            job.finished_at = now
        return True
