"""Training-run jobs (contract G1): `runs.reaper` and `runs.purge_metrics`.

`runs.reaper` runs every minute and keeps itself scheduled: each run first enqueues its successor
(one minute later) in a separate, committed transaction, so the chain survives a failing reap,
and `ensure_reaper_scheduled` skips the enqueue when a reaper is already queued (at most one is
ever queued). The first one is enqueued when a run is created (`POST /runs`); a reaper that fails
for good re-arms the chain from its final-failure hook.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session, sessionmaker

from matrix_server.config import get_settings
from matrix_server.runs import service
from matrix_server.runs.store import MetricStore, build_metric_store
from matrix_server.storage.models import Job
from matrix_server.workers.runner import on_final_failure, register

log = logging.getLogger(__name__)

_metric_store: MetricStore | None = None


def set_metric_store(store: MetricStore | None) -> None:
    """Store used by `runs.purge_metrics` (the API process shares its own; tests inject one)."""
    global _metric_store
    _metric_store = store


def get_metric_store() -> MetricStore:
    global _metric_store
    if _metric_store is None:
        _metric_store = build_metric_store(get_settings())
    return _metric_store


def _side_session(session: Session) -> Session:
    return Session(bind=session.get_bind(), expire_on_commit=False)


@register(service.REAPER_KIND)
def reap(session: Session, job: Job) -> None:
    with _side_session(session) as side, side.begin():
        service.ensure_reaper_scheduled(
            side, delay=service.REAPER_INTERVAL, exclude_job_id=job.id, count_running=False
        )
    reaped = service.reap_stale_runs(session)
    if reaped:
        log.info("runs.reaper: %d run(s) marked failed (%s)", reaped, service.LOST_HEARTBEAT)


@on_final_failure(service.REAPER_KIND)
def rearm_reaper(session_factory: sessionmaker[Session], job: Job) -> None:
    with session_factory() as session, session.begin():
        service.ensure_reaper_scheduled(session, delay=service.REAPER_INTERVAL, exclude_job_id=job.id)


@register(service.PURGE_KIND)
def purge_metrics(session: Session, job: Job) -> None:
    """Delete the points of a deleted run (idempotent; the API usually did it inline already)."""
    run_id = (job.payload or {}).get("run_id")
    if not isinstance(run_id, str) or not run_id:
        log.warning("job %s: purge without run_id, nothing to do", job.id)
        return
    get_metric_store().delete_run(job.project_id, run_id)
