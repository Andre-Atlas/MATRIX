"""Job worker: claims jobs from `storage.jobs` and dispatches them to registered handlers."""

from __future__ import annotations

import importlib
import logging
import threading
import traceback
from collections.abc import Callable
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from matrix_server.storage import jobs
from matrix_server.storage.models import Job
from matrix_server.workers.context import JobContext, JobLost, current_job

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


# Hooks run once a job is failed for good (handler error on its last attempt, a non-retryable
# failure, or a lease that expired after the last attempt): `hook(session_factory, job)`, where `job`
# is a detached snapshot (kind, project_id, payload, attempts, error). Handlers register them to
# settle the state of the object the job was working on (e.g. mark an experiment `failed`).
FinalFailureHook = Callable[[sessionmaker[Session], Job], None]
FINAL_FAILURE_HOOKS: dict[str, list[FinalFailureHook]] = {}


def on_final_failure(kind: str) -> Callable[[FinalFailureHook], FinalFailureHook]:
    """Decorator: `@on_final_failure("experiment.run") def settle(session_factory, job): ...`."""

    def decorator(fn: FinalFailureHook) -> FinalFailureHook:
        hooks = FINAL_FAILURE_HOOKS.setdefault(kind, [])
        if fn not in hooks:
            hooks.append(fn)
        return fn

    return decorator


def run_final_failure_hooks(session_factory: sessionmaker[Session], job: Job) -> None:
    for hook in FINAL_FAILURE_HOOKS.get(job.kind, [])[:]:
        try:
            hook(session_factory, job)
        except Exception as exc:  # noqa: BLE001 - a hook must never stop the worker loop
            log.error("job %s: final-failure hook %s failed: %s", job.id,
                      getattr(hook, "__qualname__", repr(hook)), type(exc).__name__)


class _Heartbeat:
    """Renews the job lease every `lease_seconds / 3`; sets `ctx.lost` once the job is no longer ours."""

    def __init__(self, session_factory: sessionmaker[Session], ctx: JobContext, lease_seconds: float):
        self._factory = session_factory
        self._ctx = ctx
        self._interval = max(lease_seconds / 3, 0.01)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"heartbeat-{ctx.job_id}", daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                owned = jobs.heartbeat(self._factory, self._ctx.job_id, self._ctx.worker_id)
            except Exception as exc:  # noqa: BLE001 - transient DB error: retry on the next tick
                log.warning("job %s: lease renewal failed: %s", self._ctx.job_id, type(exc).__name__)
                continue
            if not owned:
                log.warning("job %s: lease lost (reclaimed or failed elsewhere)", self._ctx.job_id)
                self._ctx.lost.set()
                return


def run_once(session_factory: sessionmaker[Session], worker_id: str, lease_seconds: float = 300) -> bool:
    """Process at most one job. Returns True if a job was claimed.

    The handler runs in its own transaction, which also marks the job done, so its writes and the
    status change commit together. Handler errors roll back and schedule a retry; unknown kinds fail
    without retry. A heartbeat thread renews the lease every `lease_seconds / 3`; if the job is lost
    meanwhile, the result is rolled back and the job is left to its new owner. Final failures run
    the `on_final_failure` hooks of the job kind.
    """
    job = jobs.claim(
        session_factory,
        worker_id,
        lease_seconds=lease_seconds,
        on_expired=lambda expired: run_final_failure_hooks(session_factory, expired),
    )
    if job is None:
        return False
    handler = HANDLERS.get(job.kind)
    if handler is None:
        log.error("job %s: no handler for kind %r", job.id, job.kind)
        error = f"no handler for job kind {job.kind!r}"
        if jobs.fail(session_factory, job.id, error, retry=False, worker_id=worker_id):
            job.error = error
            run_final_failure_hooks(session_factory, job)
        return True
    ctx = JobContext(job_id=job.id, worker_id=worker_id)
    token = current_job.set(ctx)
    session = session_factory()
    try:
        with _Heartbeat(session_factory, ctx, lease_seconds):
            try:
                handler(session, job)
                if ctx.lost.is_set():
                    raise JobLost
                done = jobs.mark_done(session, job.id, worker_id)
                if done:
                    session.commit()
                else:
                    log.warning("job %s: lease lost before completion, discarding result", job.id)
                    session.rollback()
            except Exception as exc:
                session.rollback()
                if isinstance(exc, JobLost) or ctx.lost.is_set():
                    log.warning("job %s: lease lost, result discarded", job.id)
                    return True
                log.exception("job %s (%s) failed on attempt %d", job.id, job.kind, job.attempts)
                detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
                status = jobs.fail_status(session_factory, job.id, detail, retry=True, worker_id=worker_id)
                if status == "failed":
                    job.error = jobs._truncate(detail)
                    run_final_failure_hooks(session_factory, job)
    finally:
        session.close()
        current_job.reset(token)
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
