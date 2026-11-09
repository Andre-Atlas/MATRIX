"""Annotation queue, DPO export and alerts (spec section 4, Anotação, regras e avaliação online)."""

from __future__ import annotations

import base64
import json
import logging
import math
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from matrix_server.api import query
from matrix_server.auth import current_project_id
from matrix_server.rules import item_from_trace, root_of_trace
from matrix_server.storage.annotations import complete_annotation, open_annotation
from matrix_server.storage.db import as_utc
from matrix_server.storage.models import Alert, Annotation, Dataset, DatasetItem, PreferencePair, Score

logger = logging.getLogger(__name__)

ProjectId = Annotated[str, Depends(current_project_id)]
router = APIRouter(prefix="/api/v1", tags=["annotations"])

_EXPORT_BATCH = 500


class ScoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    value: float = Field(allow_inf_nan=False)


class AnnotationCreate(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=10_000)


class AnnotationComplete(BaseModel):
    scores: list[ScoreIn] = Field(default_factory=list, max_length=50)
    corrected_output: str | None = None
    add_to_dataset_id: str | None = Field(default=None, max_length=32)
    comment: str | None = None


def _parse(model: type[BaseModel], payload: Any) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_input=False, include_context=False, include_url=False)
        ) from exc


def _iso(value: datetime | None) -> str | None:
    value = as_utc(value)
    return None if value is None else value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode(created_at: datetime, row_id: str) -> str:
    raw = f"{_iso(created_at)}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode(cursor: str) -> tuple[datetime, str]:
    try:
        text = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, row_id = text.split("|", 1)
        return datetime.fromisoformat(stamp), row_id
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


def _page(
    db: Session, model: Any, conditions: list[Any], limit: int, cursor: str | None
) -> tuple[list, str | None]:
    stmt = select(model).where(*conditions)
    if cursor:
        ts, row_id = _decode(cursor)
        stmt = stmt.where(or_(model.created_at < ts, and_(model.created_at == ts, model.id < row_id)))
    rows = list(db.scalars(stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode(rows[-1].created_at, rows[-1].id)
    return rows, next_cursor


def annotation_out(a: Annotation) -> dict[str, Any]:
    return {
        "id": a.id,
        "trace_id": a.trace_id,
        "reason": a.reason,
        "status": a.status,
        "created_at": _iso(a.created_at),
        "completed_at": _iso(a.completed_at),
    }


def _db(request: Request) -> Session:
    return request.app.state.session_factory()


@router.get("/annotations")
def list_annotations(
    request: Request,
    project_id: ProjectId,
    status: Literal["pending", "done"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    db = _db(request)
    try:
        conds: list[Any] = [Annotation.project_id == project_id]
        if status is not None:
            conds.append(Annotation.status == status)
        rows, next_cursor = _page(db, Annotation, conds, limit, cursor)
        items = [annotation_out(a) for a in rows]
    finally:
        db.close()
    store = request.app.state.span_store
    summaries: dict[str, Any] = {}
    for item in items:  # one lookup per distinct trace on this (bounded) page
        tid = item["trace_id"]
        if tid not in summaries:
            try:
                found = store.get_trace(project_id, tid)
            except Exception as exc:  # noqa: BLE001 - enrichment is best effort
                logger.error("annotation trace lookup failed: %s", type(exc).__name__)
                found = None
            summaries[tid] = found[0] if found else None
        s = summaries[tid]
        item["trace_name"] = s.name if s else None
        item["input_preview"] = s.input_preview if s else None
        item["output_preview"] = s.output_preview if s else None
    return {"items": items, "next_cursor": next_cursor}


@router.post("/annotations")
def create_annotation(
    request: Request,
    project_id: ProjectId,
    payload: Annotated[dict[str, Any], Body()],
) -> Any:
    body = _parse(AnnotationCreate, payload)
    if request.app.state.span_store.get_trace(project_id, body.trace_id) is None:
        raise HTTPException(status_code=404, detail="trace not found")
    db = _db(request)
    try:
        before = db.scalars(
            select(Annotation.id).where(
                Annotation.project_id == project_id,
                Annotation.dedup_key == body.trace_id,
                Annotation.status == "pending",
            )
        ).first()
        annotation = open_annotation(db, project_id, body.trace_id, body.reason)
        db.commit()
        out = annotation_out(annotation)
    finally:
        db.close()
    return JSONResponse(out, status_code=200 if before else 201)


def _rendered_input(root: Any) -> str:
    if root.input:
        return root.input
    if root.input_messages:
        return json.dumps([m.model_dump(exclude_none=True) for m in root.input_messages], ensure_ascii=False)
    return ""


def _original_output(root: Any) -> str:
    if root.output:
        return root.output
    for msg in reversed(root.output_messages):
        if msg.content:
            return msg.content
    return ""


@router.post("/annotations/{annotation_id}/complete")
def complete(
    annotation_id: str,
    request: Request,
    project_id: ProjectId,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body = _parse(AnnotationComplete, payload)
    if any(not math.isfinite(s.value) for s in body.scores):
        raise HTTPException(status_code=422, detail="score values must be finite")
    store = request.app.state.span_store
    db = _db(request)
    try:
        annotation = db.scalars(
            select(Annotation).where(Annotation.id == annotation_id, Annotation.project_id == project_id)
        ).first()
        if annotation is None:
            raise HTTPException(status_code=404, detail="annotation not found")
        if annotation.status == "done":
            raise HTTPException(status_code=409, detail="annotation already completed")
        if body.add_to_dataset_id is not None:
            ds = db.scalars(
                select(Dataset.id).where(
                    Dataset.id == body.add_to_dataset_id, Dataset.project_id == project_id
                )
            ).first()
            if ds is None:
                raise HTTPException(status_code=404, detail="dataset not found")

        trace_id = annotation.trace_id
        scores = [
            Score(
                project_id=project_id,
                trace_id=trace_id,
                name=s.name,
                value=s.value,
                source="human",
                comment=body.comment,
            )
            for s in body.scores
        ]
        db.add_all(scores)

        root_found = root_of_trace(store, project_id, trace_id)
        if body.corrected_output is not None and root_found is not None:
            root = root_found[0]
            db.add(
                PreferencePair(
                    project_id=project_id,
                    trace_id=trace_id,
                    annotation_id=annotation.id,
                    prompt=_rendered_input(root),
                    chosen=body.corrected_output,
                    rejected=_original_output(root),
                )
            )
        if body.add_to_dataset_id is not None:
            fields = item_from_trace(store, project_id, trace_id)
            if fields is None:
                raise HTTPException(status_code=404, detail="trace not found")
            if body.corrected_output is not None:
                fields["expected_output"] = body.corrected_output
            db.add(DatasetItem(dataset_id=body.add_to_dataset_id, project_id=project_id, **fields))

        annotation.scores = [s.model_dump() for s in body.scores]
        annotation.corrected_output = body.corrected_output
        annotation.comment = body.comment
        complete_annotation(db, annotation)
        db.commit()
        out = annotation_out(annotation)
        for score in scores:  # after commit, like POST /scores
            query._run_hooks(db, project_id, score)
        return out
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/exports/dpo.jsonl")
def export_dpo(request: Request, project_id: ProjectId) -> StreamingResponse:
    factory = request.app.state.session_factory

    def lines() -> Iterator[str]:
        db = factory()
        try:
            rows = db.execute(
                select(PreferencePair)
                .where(PreferencePair.project_id == project_id)
                .order_by(PreferencePair.created_at, PreferencePair.id)
                .execution_options(yield_per=_EXPORT_BATCH)
            ).scalars()
            for p in rows:
                yield (
                    json.dumps(
                        {
                            "prompt": p.prompt,
                            "chosen": p.chosen,
                            "rejected": p.rejected,
                            "trace_id": p.trace_id,
                            "created_at": _iso(p.created_at),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        finally:
            db.close()

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="dpo.jsonl"'},
    )


@router.get("/alerts")
def list_alerts(
    request: Request,
    project_id: ProjectId,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    db = _db(request)
    try:
        rows, next_cursor = _page(db, Alert, [Alert.project_id == project_id], limit, cursor)
        items = [
            {
                "id": a.id,
                "rule_id": a.rule_id,
                "trace_id": a.trace_id,
                "message": a.message,
                "created_at": _iso(a.created_at),
            }
            for a in rows
        ]
    finally:
        db.close()
    return {"items": items, "next_cursor": next_cursor}
