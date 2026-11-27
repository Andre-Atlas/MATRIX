"""Sweep jobs: `sweeps.importance` refreshes the cached hyperparameter importance off the request path."""

from __future__ import annotations

from sqlalchemy.orm import Session

from matrix_server.storage.models import Job
from matrix_server.sweeps import service
from matrix_server.workers.run_handlers import get_metric_store
from matrix_server.workers.runner import register


@register(service.IMPORTANCE_JOB_KIND)
def refresh_sweep_importance(session: Session, job: Job) -> None:
    sweep_id = (job.payload or {}).get("sweep_id")
    if not isinstance(sweep_id, str):
        return
    sweep = service.get_sweep(session, job.project_id, sweep_id, for_update=True)
    if sweep is None:
        return
    service.refresh_importance(session, get_metric_store(), sweep)
