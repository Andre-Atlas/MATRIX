"""Rule engine: turns scores and freshly ingested traces into annotations, dataset cases and alerts."""

from __future__ import annotations

import json
import logging
import math
import operator
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.domain.spans import Span, SpanKind
from matrix_server.storage.annotations import open_annotation
from matrix_server.storage.models import Alert, Annotation, Dataset, DatasetItem, Rule, Score

logger = logging.getLogger(__name__)

OPS: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
}
ACTIONS = ("annotate", "add_to_dataset", "alert")
FEEDBACK_SCORE_NAMES = frozenset({"user_feedback", "feedback"})
NEGATIVE_LABELS = frozenset({"negative", "thumbs_down", "👎"})
_CONDITION_KEYS = {"score_name", "op", "value", "feedback", "min_latency_ms", "status"}
MAX_CONTEXT_ITEMS = 100


class RuleError(ValueError):
    """Invalid rule definition (message is safe to return to the client)."""


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RuleError(f"{what} must be a finite number")
    return float(value)


def validate_rule(session: Session, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a rule payload; returns `{name, enabled, condition, actions, dataset_id}`."""
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise RuleError("name must be a non-empty string up to 200 chars")
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RuleError("enabled must be a boolean")
    raw = payload.get("condition")
    if not isinstance(raw, dict):
        raise RuleError("condition must be an object")
    unknown = set(raw) - _CONDITION_KEYS
    if unknown:
        raise RuleError(f"unknown condition keys: {', '.join(sorted(map(str, unknown)))}")

    cond: dict[str, Any] = {}
    if raw.get("score_name") is not None:
        score_name = raw["score_name"]
        if not isinstance(score_name, str) or not score_name:
            raise RuleError("score_name must be a non-empty string")
        if raw.get("op") not in OPS:
            raise RuleError("op must be one of <, <=, >, >=, ==")
        cond.update(score_name=score_name, op=raw["op"], value=_number(raw.get("value"), "value"))
    elif raw.get("op") is not None or raw.get("value") is not None:
        raise RuleError("op and value require score_name")
    if raw.get("feedback") is not None:
        if raw["feedback"] != "negative":
            raise RuleError('feedback must be "negative"')
        cond["feedback"] = "negative"
    if raw.get("min_latency_ms") is not None:
        latency = _number(raw["min_latency_ms"], "min_latency_ms")
        if latency < 0:
            raise RuleError("min_latency_ms must be >= 0")
        cond["min_latency_ms"] = latency
    if raw.get("status") is not None:
        if raw["status"] != "error":
            raise RuleError('status must be "error"')
        cond["status"] = "error"
    if not cond:
        raise RuleError("condition needs at least one of score_name, feedback, min_latency_ms, status")
    if ("score_name" in cond or "feedback" in cond) and ("min_latency_ms" in cond or "status" in cond):
        raise RuleError("score/feedback conditions cannot be combined with latency/status conditions")

    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions or any(a not in ACTIONS for a in actions):
        raise RuleError(f"actions must be a non-empty list of: {', '.join(ACTIONS)}")
    actions = list(dict.fromkeys(actions))

    dataset_id = payload.get("dataset_id")
    if dataset_id is not None and not isinstance(dataset_id, str):
        raise RuleError("dataset_id must be a string")
    if "add_to_dataset" in actions:
        if not dataset_id:
            raise RuleError("add_to_dataset requires dataset_id")
        found = session.scalars(
            select(Dataset.id).where(Dataset.id == dataset_id, Dataset.project_id == project_id)
        ).first()
        if found is None:
            raise RuleError("dataset_id does not exist in this project")
    return {
        "name": name.strip(),
        "enabled": enabled,
        "condition": cond,
        "actions": actions,
        "dataset_id": dataset_id or None,
    }


# --- Trace -> dataset item ----------------------------------------------------


def _root_span(spans: list[Span]) -> Span | None:
    ids = {s.span_id for s in spans}
    roots = [s for s in spans if s.parent_span_id is None or s.parent_span_id not in ids]
    roots.sort(key=lambda s: (s.parent_span_id is not None, s.start_time, s.span_id))
    return roots[0] if roots else None


def _retrieved_context(root: Span, spans: list[Span]) -> list[str]:
    children: dict[str, list[Span]] = {}
    for s in spans:
        if s.parent_span_id:
            children.setdefault(s.parent_span_id, []).append(s)
    subtree: list[Span] = []
    stack, seen = [root], {root.span_id}
    while stack:
        cur = stack.pop()
        subtree.append(cur)
        for child in children.get(cur.span_id, []):
            if child.span_id not in seen:
                seen.add(child.span_id)
                stack.append(child)
    subtree.sort(key=lambda s: (s.start_time, s.span_id))
    out: list[str] = []
    for s in subtree:
        if s.kind in (SpanKind.RETRIEVER, SpanKind.RERANKER):
            out.extend(d.content for d in s.documents if d.content)
    return out[:MAX_CONTEXT_ITEMS]


def _item_input(span: Span) -> dict[str, Any]:
    parsed: Any = None
    if span.input is not None:
        try:
            parsed = json.loads(span.input)
        except ValueError:
            parsed = None
    if isinstance(parsed, dict):
        data: dict[str, Any] = parsed
    elif span.input is not None:
        data = {"input": span.input}
    else:
        data = {}
    for msg in reversed(span.input_messages):
        if msg.role == "user" and msg.content:
            data["question"] = msg.content
            break
    return data


def root_of_trace(span_store: Any, project_id: str, trace_id: str) -> tuple[Span, list[Span]] | None:
    found = span_store.get_trace(project_id, trace_id)
    if found is None:
        return None
    _, spans = found
    root = _root_span(spans)
    return (root, spans) if root is not None else None


def item_from_trace(span_store: Any, project_id: str, trace_id: str) -> dict[str, Any] | None:
    """DatasetItem fields built from the trace root span (None if the trace is unknown)."""
    found = root_of_trace(span_store, project_id, trace_id)
    if found is None:
        return None
    root, spans = found
    return {
        "input": _item_input(root),
        "expected_output": root.output,
        "context": _retrieved_context(root, spans),
        "metadata_": {"model": root.model, "kind": str(root.kind)},
        "source_trace_id": root.trace_id,
        "source_span_id": root.span_id,
    }


# --- Matching and actions ---------------------------------------------------------


def _is_negative_feedback(score: Score) -> bool:
    if score.name not in FEEDBACK_SCORE_NAMES:
        return False
    label = (score.label or "").strip().lower()
    return score.value <= 0 or label in NEGATIVE_LABELS


def _match_score(rule: Rule, score: Score) -> str | None:
    cond = rule.condition or {}
    if "score_name" not in cond and "feedback" not in cond:
        return None
    if "min_latency_ms" in cond or "status" in cond:
        return None
    parts: list[str] = []
    if "score_name" in cond:
        op = OPS.get(cond.get("op"))
        value = cond.get("value")
        if op is None or not isinstance(value, (int, float)) or score.name != cond["score_name"]:
            return None
        if not op(score.value, float(value)):
            return None
        parts.append(f"{score.name}={score.value:g} {cond['op']} {value:g}")
    if "feedback" in cond:
        if not _is_negative_feedback(score):
            return None
        parts.append("negative feedback")
    return ", ".join(parts)


def _match_trace(rule: Rule, summary: Any) -> str | None:
    cond = rule.condition or {}
    if "score_name" in cond or "feedback" in cond:
        return None  # score rules never fire on ingest
    parts: list[str] = []
    if "min_latency_ms" in cond:
        if summary.duration_ms < float(cond["min_latency_ms"]):
            return None
        parts.append(f"latency {summary.duration_ms:.0f}ms >= {cond['min_latency_ms']:g}ms")
    if cond.get("status") == "error":
        if summary.status != "error":
            return None
        parts.append("status error")
    return ", ".join(parts) or None


def _fire(
    session: Session, rule: Rule, project_id: str, trace_id: str, why: str, span_store: Any | None
) -> None:
    """Run the rule's actions for a trace. Idempotent per (rule, trace)."""
    actions = rule.actions or []
    if "annotate" in actions:
        reason = f"rule:{rule.name}"
        seen = session.scalars(
            select(Annotation.id).where(
                Annotation.project_id == project_id,
                Annotation.trace_id == trace_id,
                Annotation.reason == reason,
            )
        ).first()
        if seen is None:
            open_annotation(session, project_id, trace_id, reason)
    if "add_to_dataset" in actions and rule.dataset_id and span_store is not None:
        dataset = session.scalars(
            select(Dataset.id).where(Dataset.id == rule.dataset_id, Dataset.project_id == project_id)
        ).first()
        exists = session.scalars(
            select(DatasetItem.id).where(
                DatasetItem.dataset_id == rule.dataset_id, DatasetItem.source_trace_id == trace_id
            )
        ).first()
        if dataset is not None and exists is None:
            fields = item_from_trace(span_store, project_id, trace_id)
            if fields is not None:
                session.add(DatasetItem(dataset_id=rule.dataset_id, project_id=project_id, **fields))
    if "alert" in actions:
        seen = session.scalars(
            select(Alert.id).where(
                Alert.project_id == project_id, Alert.rule_id == rule.id, Alert.trace_id == trace_id
            )
        ).first()
        if seen is None:
            session.add(
                Alert(
                    project_id=project_id,
                    rule_id=rule.id,
                    trace_id=trace_id,
                    message=f"Rule '{rule.name}' matched trace {trace_id}: {why}",
                )
            )
    session.flush()


def _enabled_rules(session: Session, project_id: str) -> list[Rule]:
    return list(
        session.scalars(
            select(Rule)
            .where(Rule.project_id == project_id, Rule.enabled.is_(True))
            .order_by(Rule.created_at)
        )
    )


def evaluate_on_score(session: Session, project_id: str, score: Score, span_store: Any | None = None) -> int:
    """Fire the project's score/feedback rules matching `score`; returns how many rules fired.

    Writes are flushed in the caller's transaction, never committed: the caller commits them
    together with the score (API request or worker job), so a rollback leaves neither behind."""
    fired = 0
    for rule in _enabled_rules(session, project_id):
        why = _match_score(rule, score)
        if why is None:
            continue
        _fire(session, rule, project_id, score.trace_id, why, span_store)
        fired += 1
    session.flush()
    return fired


def evaluate_on_trace(
    session_factory: sessionmaker[Session], span_store: Any, project_id: str, spans: list[Span]
) -> int:
    """Fire latency/status rules for traces whose root span is part of `spans`."""
    trace_ids = list(dict.fromkeys(s.trace_id for s in spans if s.parent_span_id is None))
    if not trace_ids:
        return 0
    session = session_factory()
    try:
        rules = [
            r
            for r in _enabled_rules(session, project_id)
            if "score_name" not in (r.condition or {}) and "feedback" not in (r.condition or {})
        ]
        if not rules:
            return 0
        fired = 0
        for trace_id in trace_ids:
            found = span_store.get_trace(project_id, trace_id)
            if found is None:
                continue
            summary, _ = found
            for rule in rules:
                why = _match_trace(rule, summary)
                if why is None:
                    continue
                _fire(session, rule, project_id, trace_id, why, span_store)
                fired += 1
        session.commit()
        return fired
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --- Hooks ---------------------------------------------------------------------------


def make_ingest_hook(
    session_factory: sessionmaker[Session], span_store: Any
) -> Callable[[str, list[Span]], None]:
    """Hook for `api.ingest.on_spans_ingested`. Never raises."""

    def rules_ingest_hook(project_id: str, spans: list[Span]) -> None:
        try:
            evaluate_on_trace(session_factory, span_store, project_id, spans)
        except Exception as exc:  # noqa: BLE001 - hooks must never break ingestion
            logger.error("rules ingest hook failed: %s", type(exc).__name__)

    return rules_ingest_hook


def make_score_hook(span_store: Any | None) -> Callable[[Session, str, Score], None]:
    """Hook for `api.query.on_score_created`. `span_store` enables the add_to_dataset action.

    Runs in the caller's transaction inside a savepoint: a failure undoes only the rule writes."""

    def rules_score_hook(session: Session, project_id: str, score: Score) -> None:
        try:
            with session.begin_nested():
                evaluate_on_score(session, project_id, score, span_store)
        except Exception as exc:  # noqa: BLE001 - hooks must never fail score creation
            logger.error("rules score hook failed: %s", type(exc).__name__)

    return rules_score_hook


# Without a span store: annotate and alert work, add_to_dataset is skipped. Prefer `make_score_hook`.
score_hook = make_score_hook(None)
