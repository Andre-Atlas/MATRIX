"""Worker handler for `online_eval.score` jobs: judge one sampled span and record a Score.

Judge selection: the online eval's `model` ({provider, name}) when set, else the environment
(`MATRIX_JUDGE_PROVIDER`, `MATRIX_JUDGE_MODEL`, default `fake`). Provider `fake` uses the offline
heuristic judge. API keys are read from environment variables by the providers and never stored.
The OpenAI-compatible endpoint is `MATRIX_OPENAI_BASE_URL` (shared with experiments), overridden for
judges by `MATRIX_JUDGE_BASE_URL` when set (env only, never request input).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server.domain.spans import Span, SpanKind
from matrix_server.evals import (
    EvalCase,
    EvalDeps,
    HashingEmbedder,
    LLMJudge,
    OpenAIEmbedder,
    build_metric,
    evaluate_case,
    get_provider,
)
from matrix_server.evals.runner import (
    JUDGE_METRIC_TYPES,
    judge_base_url,
    judge_kind,
    warn_heuristic_judge_once,
)
from matrix_server.online_evals import JOB_KIND
from matrix_server.storage.models import Job, OnlineEval, Score
from matrix_server.workers.runner import register

logger = logging.getLogger(__name__)

MAX_COMMENT_CHARS = 1000
MAX_CONTEXTS = 100
# Scores from the offline heuristic judge keep `source="llm"` (the score contract accepts only
# human|llm|rule|sdk) but are marked with this label and a comment prefix.
HEURISTIC_LABEL = "heuristic"
HEURISTIC_COMMENT_PREFIX = "[heuristic judge] "

_span_store: Any = None
_span_store_lock = threading.Lock()


def set_span_store(store: Any) -> None:
    """Override the span store (tests, or a process that already built one)."""
    global _span_store
    with _span_store_lock:
        _span_store = store


def get_span_store() -> Any:
    global _span_store
    with _span_store_lock:
        if _span_store is None:
            from matrix_server.config import get_settings
            from matrix_server.storage.spans import build_span_store

            _span_store = build_span_store(get_settings())
        return _span_store


# --------------------------------------------------------------------------- case building


def case_input(span: Span) -> dict[str, Any]:
    if span.input is not None:
        try:
            parsed = json.loads(span.input)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        return {"question": span.input}
    for msg in reversed(span.input_messages):
        if msg.role == "user" and msg.content:
            return {"question": msg.content}
    return {}


def case_output(span: Span) -> str:
    if span.output is not None:
        return span.output
    for msg in reversed(span.output_messages):
        if msg.content:
            return msg.content
    return ""


def build_case(span: Span, trace_spans: list[Span], trace_cost_usd: float | None = None) -> EvalCase:
    contexts: list[str] = []
    for s in sorted(trace_spans, key=lambda s: (s.start_time, s.span_id)):
        if s.kind in (SpanKind.RETRIEVER, SpanKind.RERANKER):
            contexts.extend(d.content for d in s.documents if d.content)
    # A root span stands for the whole request: its cost is the trace total.
    cost = trace_cost_usd if span.parent_span_id is None and trace_cost_usd is not None else span.cost_usd
    return EvalCase(
        input=case_input(span),
        output=case_output(span),
        contexts=contexts[:MAX_CONTEXTS],
        latency_ms=span.duration_ms,
        cost_usd=cost,
    )


# --------------------------------------------------------------------------- judge


def build_deps(model: dict[str, Any] | None) -> EvalDeps:
    if model:
        provider_name, model_name = str(model.get("provider", "")), str(model.get("name", ""))
    else:
        provider_name = os.environ.get("MATRIX_JUDGE_PROVIDER", "")
        model_name = os.environ.get("MATRIX_JUDGE_MODEL", "")
        if not provider_name.strip():
            warn_heuristic_judge_once()
    provider_name = provider_name.strip().lower() or "fake"
    if provider_name == "fake":
        return EvalDeps.offline()
    base_url = judge_base_url()
    opts: dict[str, Any] = {"base_url": base_url} if provider_name == "openai" and base_url else {}
    provider = get_provider(provider_name, **opts)
    if not model_name:
        raise ValueError("judge model name is not configured (MATRIX_JUDGE_MODEL)")
    embedder: Any = OpenAIEmbedder(base_url=base_url) if provider_name == "openai" else HashingEmbedder()
    return EvalDeps(judge=LLMJudge(provider, model_name), embedder=embedder)


# --------------------------------------------------------------------------- handler


def dedup_key(online_eval_id: str, trace_id: str, span_id: str) -> str:
    return f"online:{online_eval_id}:{trace_id}:{span_id}"


def _existing_score(
    session: Session, project_id: str, online_eval_id: str, trace_id: str, span_id: str, name: str
) -> bool:
    return (
        session.scalars(
            select(Score.id).where(
                Score.project_id == project_id,
                or_(
                    Score.dedup_key == dedup_key(online_eval_id, trace_id, span_id),
                    (Score.trace_id == trace_id) & (Score.span_id == span_id) & (Score.name == name),
                ),
            )
        ).first()
        is not None
    )


def _run_score_hooks(session: Session, project_id: str, score: Score) -> None:
    """Score hooks run in the job's transaction (committed with the job), one savepoint each."""
    from matrix_server.api.query import run_score_hooks

    run_score_hooks(session, project_id, score)


@register(JOB_KIND)
def handle_online_eval_score(session: Session, job: Job) -> None:
    payload = job.payload or {}
    online_eval_id, trace_id, span_id = (
        payload.get("online_eval_id"),
        payload.get("trace_id"),
        payload.get("span_id"),
    )
    if not (isinstance(online_eval_id, str) and isinstance(trace_id, str) and isinstance(span_id, str)):
        logger.warning("job %s: malformed online eval payload, skipped", job.id)
        return
    project_id = job.project_id
    ev = session.scalars(
        select(OnlineEval).where(OnlineEval.id == online_eval_id, OnlineEval.project_id == project_id)
    ).first()
    if ev is None or not ev.enabled:
        return
    if _existing_score(session, project_id, online_eval_id, trace_id, span_id, ev.name):
        return

    found = get_span_store().get_trace(project_id, trace_id)
    span = None
    if found is not None:
        summary, spans = found
        span = next((s for s in spans if s.span_id == span_id), None)
    if span is None:
        # Spans are written before the ingest hook runs; a miss is a lagging store: retry later.
        raise LookupError("span not found in the span store")

    metric = build_metric({"type": ev.metric, "params": ev.params or {}})
    case = build_case(span, spans, summary.total_cost_usd)
    deps = build_deps(ev.model)
    result = evaluate_case([metric], case, deps)[metric.name]
    if result.details.get("error"):
        raise RuntimeError(f"metric evaluation failed: {result.reason[:200]}")
    if result.value is None:
        return  # not applicable (e.g. no contexts): nothing to record

    heuristic = metric.type in JUDGE_METRIC_TYPES and judge_kind(deps) == "heuristic"
    comment = result.reason or ""
    if heuristic:
        comment = HEURISTIC_COMMENT_PREFIX + comment
    score = Score(
        project_id=project_id,
        trace_id=trace_id,
        span_id=span_id,
        name=ev.name,
        value=float(result.value),
        label=HEURISTIC_LABEL if heuristic else None,
        comment=comment[:MAX_COMMENT_CHARS] or None,
        source="llm",
        dedup_key=dedup_key(online_eval_id, trace_id, span_id),
    )
    session.add(score)
    try:
        # No savepoint: on SQLite (pysqlite) a SAVEPOINT opened before any write would commit on
        # release. The score is this handler's first write, so a full rollback loses nothing.
        session.flush()
    except IntegrityError:
        # A duplicate job (or a concurrent worker) already recorded this span: nothing more to do.
        session.rollback()
        logger.info("job %s: span already scored by online eval %s", job.id, online_eval_id)
        return
    _run_score_hooks(session, project_id, score)
