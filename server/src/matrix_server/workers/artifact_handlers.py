"""Worker handler for `artifacts.gc` jobs (ADR-007): remove unreferenced blobs and expired pending
uploads. The job keeps itself scheduled every `GC_INTERVAL` like `runs.reaper`: each run first
enqueues its successor in a separate, committed transaction; the worker arms the chain at start-up
(`cli.run_worker`) and a GC that fails for good re-arms it from its final-failure hook."""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from matrix_server.artifacts import ArtifactStore, build_artifact_store
from matrix_server.artifacts.service import GC_INTERVAL, GC_JOB_KIND, ensure_gc_scheduled, gc
from matrix_server.storage.models import Job
from matrix_server.workers.runner import on_final_failure, register

log = logging.getLogger(__name__)

_store: ArtifactStore | None = None
_lock = threading.Lock()


def set_artifact_store(store: Any) -> None:
    """Override the store (tests, or a process that already built one)."""
    global _store
    with _lock:
        _store = store


def get_artifact_store() -> ArtifactStore:
    global _store
    with _lock:
        if _store is None:
            from matrix_server.config import get_settings

            _store = build_artifact_store(get_settings())
        return _store


@register(GC_JOB_KIND)
def handle_gc(session: Session, job: Job) -> None:
    with Session(bind=session.get_bind(), expire_on_commit=False) as side, side.begin():
        ensure_gc_scheduled(side, delay=GC_INTERVAL, exclude_job_id=job.id, count_running=False)
    result = gc(session, get_artifact_store())
    log.info(
        "artifacts.gc: %d expired uploads, %d stale objects, %d blobs deleted, %d kept",
        result.expired_uploads, result.purged_objects, result.deleted_blobs, result.kept_blobs,
    )


@on_final_failure(GC_JOB_KIND)
def rearm_gc(session_factory: sessionmaker[Session], job: Job) -> None:
    with session_factory() as session, session.begin():
        ensure_gc_scheduled(session, delay=GC_INTERVAL, exclude_job_id=job.id)
