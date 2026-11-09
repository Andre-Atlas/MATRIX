"""Per-job execution context shared between the worker runner and long-running handlers.

The runner renews the job lease from a heartbeat thread; when a renewal finds the job is no longer
ours (another worker reclaimed it, or it was failed after its last attempt) it sets `lost`.
Handlers that run for a long time poll `job_lost()` and stop early by raising `JobLost`.
"""

from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field


class JobLost(Exception):
    """The worker lost the job lease; the handler stops and its result is discarded."""


@dataclass
class JobContext:
    job_id: str
    worker_id: str
    lost: threading.Event = field(default_factory=threading.Event)


current_job: contextvars.ContextVar[JobContext | None] = contextvars.ContextVar(
    "matrix_current_job", default=None
)


def lost_event() -> threading.Event | None:
    """The running job's `lost` flag (None outside a worker). Safe to hand to other threads."""
    ctx = current_job.get()
    return None if ctx is None else ctx.lost


def job_lost() -> bool:
    ctx = current_job.get()
    return ctx is not None and ctx.lost.is_set()
