"""Read API for traces, sessions and stats, plus scores (spec section 4)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.auth import Role, project_of
from matrix_server.domain.spans import SessionSummary, Span, TraceSummary, UtcDatetime
from matrix_server.storage.db import as_utc
from matrix_server.storage.models import Score
from matrix_server.storage.spans import MAX_LIMIT, InvalidCursor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# Called after a score is flushed with (db session, project_id, score), in the transaction that
# created it; the caller commits afterwards (see `run_score_hooks`). Rules register here.
# A failing hook is logged (without payload) and never fails the request.
on_score_created: list[Callable[[Session, str, Score], None]] = []

_SHORTHAND = re.compile(r"^(\d+)([mhdw])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


class ScoreOut(BaseModel):
    id: str
    trace_id: str
    span_id: str | None = None
    name: str
    value: float
    label: str | None = None
    comment: str | None = None
    source: Literal["human", "llm", "rule", "sdk"]
    created_at: UtcDatetime


class ScoreIn(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    span_id: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    value: float = Field(allow_inf_nan=False)
    label: str | None = Field(default=None, max_length=200)
    comment: str | None = None
    source: Literal["human", "llm", "rule", "sdk"]


class TracePage(BaseModel):
    items: list[TraceSummary]
    next_cursor: str | None = None


class TraceDetail(BaseModel):
    trace: TraceSummary
    spans: list[Span]


class SessionPage(BaseModel):
    items: list[SessionSummary]
    next_cursor: str | None = None


class SessionDetail(BaseModel):
    session: SessionSummary
    traces: list[TraceSummary]
    next_cursor: str | None = None


def _span_store(request: Request) -> Any:
    return request.app.state.span_store


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def parse_since(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    match = _SHORTHAND.match(text)
    if match:
        return datetime.now(UTC) - timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="since must be an ISO datetime or 1h/24h/7d") from exc
    return _utc(parsed)


def _fill_scores(request: Request, project_id: str, traces: list[TraceSummary]) -> None:
    """Set `scores` (latest value per name) on every trace with one batched query."""
    if not traces:
        return
    ids = list({t.trace_id for t in traces})
    latest: dict[str, dict[str, float]] = {}
    session: Session = request.app.state.session_factory()
    try:
        rows = session.execute(
            select(Score.trace_id, Score.name, Score.value)
            .where(Score.project_id == project_id, Score.trace_id.in_(ids))
            .order_by(Score.created_at, Score.id)
        ).all()
    finally:
        session.close()
    for trace_id, name, value in rows:  # ascending: later rows overwrite earlier ones
        latest.setdefault(trace_id, {})[name] = value
    for trace in traces:
        trace.scores = latest.get(trace.trace_id, {})


def _to_out(score: Score) -> ScoreOut:
    return ScoreOut(
        id=score.id,
        trace_id=score.trace_id,
        span_id=score.span_id,
        name=score.name,
        value=score.value,
        label=score.label,
        comment=score.comment,
        source=score.source,  # type: ignore[arg-type]
        created_at=as_utc(score.created_at),  # type: ignore[arg-type]
    )


@router.get("/traces", response_model=TracePage)
def list_traces(
    request: Request,
    project_id: str = Depends(project_of(Role.VIEWER)),
    session_id: str | None = None,
    user_id: str | None = None,
    name: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    cursor: str | None = None,
) -> TracePage:
    try:
        items, next_cursor = _span_store(request).list_traces(
            project_id,
            session_id=session_id,
            user_id=user_id,
            name=name,
            status=status,
            kind=kind,
            since=_utc(since),
            until=_utc(until),
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursor as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    _fill_scores(request, project_id, items)
    return TracePage(items=items, next_cursor=next_cursor)


@router.get("/traces/{trace_id}", response_model=TraceDetail)
def get_trace(request: Request, trace_id: str, project_id: str = Depends(project_of(Role.VIEWER))) -> TraceDetail:
    found = _span_store(request).get_trace(project_id, trace_id)
    if found is None:
        raise HTTPException(status_code=404, detail="trace not found")
    summary, spans = found
    _fill_scores(request, project_id, [summary])
    return TraceDetail(trace=summary, spans=spans)


@router.get("/sessions", response_model=SessionPage)
def list_sessions(
    request: Request,
    project_id: str = Depends(project_of(Role.VIEWER)),
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    cursor: str | None = None,
) -> SessionPage:
    try:
        items, next_cursor = _span_store(request).list_sessions(project_id, limit=limit, cursor=cursor)
    except InvalidCursor as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    return SessionPage(items=items, next_cursor=next_cursor)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    request: Request,
    session_id: str,
    project_id: str = Depends(project_of(Role.VIEWER)),
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    cursor: str | None = None,
) -> SessionDetail:
    try:
        found = _span_store(request).get_session(project_id, session_id, limit=limit, cursor=cursor)
    except InvalidCursor as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if found is None:
        raise HTTPException(status_code=404, detail="session not found")
    summary, traces, next_cursor = found
    _fill_scores(request, project_id, traces)
    return SessionDetail(session=summary, traces=traces, next_cursor=next_cursor)


@router.get("/stats/overview")
def stats_overview(
    request: Request, project_id: str = Depends(project_of(Role.VIEWER)), since: str | None = None
) -> dict[str, Any]:
    return _span_store(request).overview(project_id, parse_since(since))


def run_score_hooks(session: Session, project_id: str, score: Score) -> None:
    """Run `on_score_created` in the caller's transaction (the score must be flushed). Each hook runs
    in a savepoint so a failure only undoes its own writes (logged by type only, never the payload).
    The caller commits: score and hook writes are atomic."""
    for hook in on_score_created[:]:
        savepoint = session.begin_nested()
        try:
            hook(session, project_id, score)
            if savepoint.is_active:
                savepoint.commit()
        except Exception as exc:  # noqa: BLE001 - a hook must never fail score creation
            if savepoint.is_active:
                savepoint.rollback()
            logger.error(
                "score hook %s failed: %s", getattr(hook, "__qualname__", repr(hook)), type(exc).__name__
            )



@router.post("/scores", response_model=ScoreOut, status_code=201)
def create_score(
    request: Request,
    payload: Annotated[dict[str, Any], Body()],
    project_id: str = Depends(project_of(Role.MEMBER)),
) -> ScoreOut:
    # Validated by hand: FastAPI's 422 body would echo a NaN/Infinity input and fail to serialize.
    try:
        body = ScoreIn.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_input=False, include_context=False)
        ) from exc
    store = _span_store(request)
    if store.get_trace(project_id, body.trace_id) is None:
        raise HTTPException(status_code=404, detail="trace not found")
    if body.span_id is not None and store.get_span(project_id, body.trace_id, body.span_id) is None:
        raise HTTPException(status_code=404, detail="span not found")
    session: Session = request.app.state.session_factory()
    try:
        score = Score(project_id=project_id, **body.model_dump())
        session.add(score)
        session.flush()
        run_score_hooks(session, project_id, score)
        session.commit()
        out = _to_out(score)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return out


@router.get("/traces/{trace_id}/scores", response_model=list[ScoreOut])
def list_scores(
    request: Request, trace_id: str, project_id: str = Depends(project_of(Role.VIEWER))
) -> list[ScoreOut]:
    session: Session = request.app.state.session_factory()
    try:
        rows = session.scalars(
            select(Score)
            .where(Score.project_id == project_id, Score.trace_id == trace_id)
            .order_by(Score.created_at, Score.id)
        ).all()
        return [_to_out(r) for r in rows]
    finally:
        session.close()
