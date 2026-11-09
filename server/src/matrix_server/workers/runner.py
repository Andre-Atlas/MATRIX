"""Job worker: claims jobs from `storage.jobs` and dispatches them to registered handlers."""

from __future__ import annotations

import importlib
import logging
import threading
import traceback
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from matrix_server.storage import jobs
from matrix_server.storage.models import Job

log = logging.getLogger(__name__)

Handler = Callable[[Session, Job], None]
HANDLERS: dict[str, Handler] = {}

# Modules whose import registers handlers via `@register`; `run_worker` imports them at start-up.
HANDLER_MODULES: list[str] = ["matrix_server.workers.handlers", "matrix_server.workers.online_handlers"]


def load_handler_modules(modules: list[str] | None = None) -> None:
    for name in HANDLER_MODULES if modules is None else modules:
        importlib.import_module(name)


def register(kind: str) -> Callable[[Handler], Handler]:
    """Decorator: `@register("experiment.run") def handle(session, job): ...`."""

    def decorator(fn: Handler) -> Handler:
        if kind in HANDLERS and HANDLERS[kind] is not fn:
            raise ValueError(f"handler for job kind {kind!r} already registered")
        HANDLERS[kind] = fn
        return fn

    return decorator


def run_once(session_factory: sessionmaker[Session], worker_id: str, lease_seconds: float = 300) -> bool:
    """Process at most one job. Returns True if a job was claimed.

    The handler runs in its own transaction, which also marks the job done, so its writes and the
    status change commit together. Handler errors roll back and schedule a retry; unknown kinds fail
    without retry.
    """
    job = jobs.claim(session_factory, worker_id, lease_seconds=lease_seconds)
    if job is None:
        return False
    handler = HANDLERS.get(job.kind)
    if handler is None:
        log.error("job %s: no handler for kind %r", job.id, job.kind)
        jobs.fail(session_factory, job.id, f"no handler for job kind {job.kind!r}", retry=False,
                  worker_id=worker_id)
        return True
    session = session_factory()
    try:
        handler(session, job)
        if jobs.mark_done(session, job.id, worker_id):
            session.commit()
        else:
            log.warning("job %s: lease lost before completion, discarding result", job.id)
            session.rollback()
    except Exception as exc:
        session.rollback()
        log.exception("job %s (%s) failed on attempt %d", job.id, job.kind, job.attempts)
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        jobs.fail(session_factory, job.id, detail, retry=True, worker_id=worker_id)
    finally:
        session.close()
    return True


def run_forever(
    session_factory: sessionmaker[Session],
    poll_interval: float = 1.0,
    stop_event: threading.Event | None = None,
    worker_id: str = "worker",
) -> None:
    """Loop until `stop_event` is set; sleeps `poll_interval` when the queue is empty."""
    stop_event = stop_event or threading.Event()
    log.info("worker %s started (kinds: %s)", worker_id, ", ".join(sorted(HANDLERS)) or "none")
    while not stop_event.is_set():
        try:
            worked = run_once(session_factory, worker_id)
        except Exception:
            log.exception("worker %s: error while polling the job queue", worker_id)
            worked = False
        if not worked:
            stop_event.wait(poll_interval)
    log.info("worker %s stopped", worker_id)
