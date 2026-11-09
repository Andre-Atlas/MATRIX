"""Online evaluation: deterministic sampling of ingested spans and enqueueing of judge jobs.

`make_ingest_hook(session_factory)` returns a callable for `api.ingest.on_spans_ingested`. For each
enabled online eval of the project and each matching span it decides with `should_sample` (a hash of
`(online_eval_id, trace_id)`, so the same trace is always in or out on every replica and retry) and
enqueues an `online_eval.score` job; `workers.online_handlers` scores it.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.domain.spans import Span
from matrix_server.storage.jobs import enqueue
from matrix_server.storage.models import OnlineEval, Score

logger = logging.getLogger(__name__)

JOB_KIND = "online_eval.score"
CACHE_TTL_SECONDS = 10.0
_SCALE = float(2**64)


def should_sample(trace_id: str, online_eval_id: str, rate: float) -> bool:
    """Deterministic: the first 8 bytes of sha256("<eval id>:<trace id>") as a fraction of 2**64."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(f"{online_eval_id}:{trace_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / _SCALE < rate


def span_matches(span_filter: dict[str, Any] | None, span: Span) -> bool:
    """No filter: root spans only. With `kind` and/or `name`: any span matching all given fields."""
    span_filter = span_filter or {}
    kind, name = span_filter.get("kind"), span_filter.get("name")
    if kind is None and name is None:
        return span.parent_span_id is None
    if kind is not None and str(span.kind) != kind:
        return False
    return name is None or span.name == name


@dataclass(frozen=True)
class EvalRef:
    """Detached snapshot of an online eval (safe to cache across sessions/threads)."""

    id: str
    name: str
    sampling_rate: float
    filter: dict[str, Any] = field(default_factory=dict)


_cache: dict[str, tuple[float, list[EvalRef]]] = {}
_cache_lock = threading.Lock()


def invalidate_cache(project_id: str | None = None) -> None:
    """Drop the cached online-eval list (of one project, or all). Other replicas expire in TTL."""
    with _cache_lock:
        if project_id is None:
            _cache.clear()
        else:
            _cache.pop(project_id, None)


def _enabled_evals(session: Session, project_id: str) -> list[EvalRef]:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(project_id)
        if hit is not None and hit[0] > now:
            return hit[1]
    rows = session.scalars(
        select(OnlineEval).where(OnlineEval.project_id == project_id, OnlineEval.enabled.is_(True))
    ).all()
    refs = [EvalRef(r.id, r.name, float(r.sampling_rate), dict(r.filter or {})) for r in rows]
    with _cache_lock:
        _cache[project_id] = (now + CACHE_TTL_SECONDS, refs)
    return refs


def make_ingest_hook(session_factory: sessionmaker[Session]) -> Callable[[str, list[Span]], None]:
    def online_eval_ingest_hook(project_id: str, spans: list[Span]) -> None:
        try:
            _enqueue_sampled(session_factory, project_id, spans)
        except Exception as exc:  # noqa: BLE001 - never break ingestion; log the type only (PII)
            logger.error("online eval ingest hook failed: %s", type(exc).__name__)

    return online_eval_ingest_hook


def _enqueue_sampled(session_factory: sessionmaker[Session], project_id: str, spans: list[Span]) -> int:
    if not spans:
        return 0
    with session_factory() as session:
        evals = _enabled_evals(session, project_id)
        if not evals:
            return 0
        wanted: list[tuple[EvalRef, Span]] = [
            (ev, span)
            for ev in evals
            for span in spans
            if span_matches(ev.filter, span) and should_sample(span.trace_id, ev.id, ev.sampling_rate)
        ]
        if not wanted:
            return 0
        trace_ids = {span.trace_id for _, span in wanted}
        names = {ev.name for ev, _ in wanted}
        existing = set(
            session.execute(
                select(Score.trace_id, Score.span_id, Score.name).where(
                    Score.project_id == project_id,
                    Score.trace_id.in_(trace_ids),
                    Score.name.in_(names),
                )
            ).all()
        )
        count = 0
        for ev, span in wanted:
            if (span.trace_id, span.span_id, ev.name) in existing:
                continue
            enqueue(
                session,
                project_id,
                JOB_KIND,
                {"online_eval_id": ev.id, "trace_id": span.trace_id, "span_id": span.span_id},
            )
            count += 1
        session.commit()
        return count
