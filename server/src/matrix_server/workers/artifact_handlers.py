"""Worker handler for `artifacts.gc` jobs (ADR-007): remove unreferenced blobs and expired pending
uploads. Enqueue with `matrix_server.artifacts.service.schedule_gc(session_factory)`."""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy.orm import Session

from matrix_server.artifacts import ArtifactStore, build_artifact_store
from matrix_server.artifacts.service import GC_JOB_KIND, gc
from matrix_server.storage.models import Job
from matrix_server.workers.runner import register

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
    result = gc(session, get_artifact_store())
    log.info(
        "artifacts.gc: %d expired uploads, %d stale objects, %d blobs deleted, %d kept",
        result.expired_uploads, result.purged_objects, result.deleted_blobs, result.kept_blobs,
    )
