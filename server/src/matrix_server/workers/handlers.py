"""Job handlers registered with the worker (listed in `runner.HANDLER_MODULES`)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.evals.runner import ExperimentConfigError, run_experiment
from matrix_server.storage.db import utcnow
from matrix_server.storage.models import Experiment, Job
from matrix_server.workers.runner import register

log = logging.getLogger(__name__)


def _side_session(session: Session) -> Session:
    """Short-lived session on the same engine, committed independently of the handler transaction."""
    return Session(bind=session.get_bind(), expire_on_commit=False)


def _set_status(session: Session, experiment_id: str, **values: object) -> None:
    with _side_session(session) as side, side.begin():
        exp = side.get(Experiment, experiment_id)
        if exp is not None:
            for key, value in values.items():
                setattr(exp, key, value)


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ExperimentConfigError):
        return str(exc)[:1000]
    return f"internal error: {type(exc).__name__}"


@register("experiment.run")
def run_experiment_job(session: Session, job: Job) -> None:
    """Run an experiment. Idempotent: results are upserted per (item, variant); a done experiment is
    skipped. Systemic errors are recorded on the experiment (sanitized) and re-raised so the queue
    retries; after the last attempt the experiment is marked `failed`."""
    experiment_id = (job.payload or {}).get("experiment_id")
    found = session.execute(
        select(Experiment.id, Experiment.status).where(
            Experiment.id == experiment_id, Experiment.project_id == job.project_id
        )
    ).first()
    session.rollback()  # release the read before the side session writes (SQLite locking)
    if found is None:
        log.warning("job %s: experiment not found, nothing to do", job.id)
        return
    exp_id, status = found
    if status == "done":
        log.info("job %s: experiment %s already done", job.id, exp_id)
        return
    _set_status(session, exp_id, status="running", started_at=utcnow(), error=None)
    try:
        exp = session.get(Experiment, exp_id, populate_existing=True)
        run_experiment(session, exp)
    except Exception as exc:
        session.rollback()
        final = job.attempts >= job.max_attempts
        log.error("experiment %s failed (attempt %d): %s", exp_id, job.attempts, type(exc).__name__)
        _set_status(
            session,
            exp_id,
            status="failed" if final else "queued",
            error=_safe_error(exc),
            finished_at=utcnow() if final else None,
        )
        raise
