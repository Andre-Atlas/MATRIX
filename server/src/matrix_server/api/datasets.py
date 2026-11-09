"""Datasets API: golden datasets and their items (spec section 4, Datasets e experimentos)."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server.auth import current_project_id
from matrix_server.domain.spans import Span, SpanKind
from matrix_server.storage.db import as_utc, get_db
from matrix_server.storage.models import Dataset, DatasetItem

DbSession = Annotated[Session, Depends(get_db)]
ProjectId = Annotated[str, Depends(current_project_id)]

router = APIRouter(prefix="/api/v1", tags=["datasets"])

MAX_CONTEXT_ITEMS = 100
MAX_EXPECTED_OUTPUT_CHARS = 100_000
MAX_CONTEXT_CHARS = 50_000
MAX_PAGE = 500
DEFAULT_PAGE = 500
_EXPORT_BATCH = 500


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)


class ItemCreate(BaseModel):
    input: dict[str, Any]
    expected_output: str | None = Field(default=None, max_length=MAX_EXPECTED_OUTPUT_CHARS)
    context: list[Annotated[str, Field(max_length=MAX_CONTEXT_CHARS)]] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class FromSpan(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    span_id: str | None = Field(default=None, min_length=1, max_length=32)


def parse_body(model: type[BaseModel], payload: Any) -> Any:
    """Validate by hand so a 422 never echoes the input (it may hold NaN or large/PII payloads)."""
    try:
        body = model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_input=False, include_context=False, include_url=False)
        ) from exc
    try:
        json.dumps(body.model_dump(), allow_nan=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="body must not contain NaN or Infinity") from exc
    return body


def iso_utc(value: Any) -> str | None:
    value = as_utc(value)
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def dataset_out(ds: Dataset, item_count: int) -> dict[str, Any]:
    return {
        "id": ds.id,
        "name": ds.name,
        "description": ds.description,
        "item_count": item_count,
        "created_at": iso_utc(ds.created_at),
    }


def item_out(item: DatasetItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "dataset_id": item.dataset_id,
        "input": item.input,
        "expected_output": item.expected_output,
        "context": item.context or [],
        "metadata": item.metadata_ or {},
        "source_trace_id": item.source_trace_id,
        "source_span_id": item.source_span_id,
        "created_at": iso_utc(item.created_at),
    }


def get_dataset_or_404(db: Session, project_id: str, dataset_id: str) -> Dataset:
    ds = db.scalars(select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)).first()
    if ds is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return ds


@router.post("/datasets", status_code=201)
def create_dataset(
    db: DbSession, project_id: ProjectId, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    body = parse_body(DatasetCreate, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    exists = db.scalar(select(Dataset.id).where(Dataset.project_id == project_id, Dataset.name == name))
    if exists is not None:
        raise HTTPException(status_code=409, detail="dataset name already exists")
    ds = Dataset(project_id=project_id, name=name, description=body.description)
    db.add(ds)
    try:
        db.flush()
    except IntegrityError as exc:  # concurrent create with the same name
        db.rollback()
        raise HTTPException(status_code=409, detail="dataset name already exists") from exc
    return dataset_out(ds, 0)


@router.get("/datasets")
def list_datasets(db: DbSession, project_id: ProjectId) -> dict[str, Any]:
    count = func.count(DatasetItem.id)
    rows = db.execute(
        select(Dataset, count)
        .outerjoin(DatasetItem, DatasetItem.dataset_id == Dataset.id)
        .where(Dataset.project_id == project_id)
        .group_by(Dataset.id)
        .order_by(Dataset.created_at.desc(), Dataset.id)
    ).all()
    return {"items": [dataset_out(ds, n) for ds, n in rows]}


def _encode_cursor(item: DatasetItem) -> str:
    raw = f"{iso_utc(item.created_at)}|{item.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        stamp, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        ts = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    return ts, row_id


def _same_tz(db: Session, ts: datetime) -> datetime:
    # SQLite stores naive UTC datetimes; compare like with like.
    return ts.replace(tzinfo=None) if db.get_bind().dialect.name == "sqlite" else ts


def _items_page(
    db: Session, dataset_id: str, limit: int, cursor: str | None
) -> tuple[list[DatasetItem], str | None]:
    """Items oldest first, keyset-paginated on (created_at, id)."""
    stmt = select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
    if cursor:
        ts, row_id = _decode_cursor(cursor)
        ts = _same_tz(db, ts)
        stmt = stmt.where(
            or_(DatasetItem.created_at > ts, and_(DatasetItem.created_at == ts, DatasetItem.id > row_id))
        )
    rows = list(db.scalars(stmt.order_by(DatasetItem.created_at, DatasetItem.id).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1])
    return rows, next_cursor


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: str,
    db: DbSession,
    project_id: ProjectId,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Dataset with one page of its items: `{dataset, items, next_cursor}` (`item_count` is the total)."""
    ds = get_dataset_or_404(db, project_id, dataset_id)
    total = db.scalar(select(func.count(DatasetItem.id)).where(DatasetItem.dataset_id == ds.id)) or 0
    items, next_cursor = _items_page(db, ds.id, limit, cursor)
    return {"dataset": dataset_out(ds, total), "items": [item_out(i) for i in items], "next_cursor": next_cursor}


@router.post("/datasets/{dataset_id}/items", status_code=201)
def add_item(
    dataset_id: str, db: DbSession, project_id: ProjectId, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    ds = get_dataset_or_404(db, project_id, dataset_id)
    body = parse_body(ItemCreate, payload)
    item = DatasetItem(
        dataset_id=ds.id,
        project_id=project_id,
        input=body.input,
        expected_output=body.expected_output,
        context=body.context,
        metadata_=body.metadata,
    )
    db.add(item)
    db.flush()
    return item_out(item)


# --- from-span ----------------------------------------------------------------


def _root_span(spans: list[Span]) -> Span | None:
    ids = {s.span_id for s in spans}
    roots = [s for s in spans if s.parent_span_id is None or s.parent_span_id not in ids]
    roots.sort(key=lambda s: (s.parent_span_id is not None, s.start_time, s.span_id))
    return roots[0] if roots else None


def _retrieved_context(span: Span, spans: list[Span]) -> list[str]:
    """Document contents of RETRIEVER/RERANKER spans in the subtree of `span` (itself included)."""
    children: dict[str, list[Span]] = {}
    for s in spans:
        if s.parent_span_id:
            children.setdefault(s.parent_span_id, []).append(s)
    subtree: list[Span] = []
    stack, seen = [span], {span.span_id}
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


def item_input_from_span(span: Span) -> dict[str, Any]:
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


@router.post("/datasets/{dataset_id}/items/from-span", status_code=201)
def add_item_from_span(
    dataset_id: str,
    request: Request,
    db: DbSession,
    project_id: ProjectId,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    ds = get_dataset_or_404(db, project_id, dataset_id)
    body = parse_body(FromSpan, payload)
    found = request.app.state.span_store.get_trace(project_id, body.trace_id)
    if found is None:
        raise HTTPException(status_code=404, detail="span not found")
    _, spans = found
    if body.span_id is None:
        span = _root_span(spans)
    else:
        span = next((s for s in spans if s.span_id == body.span_id), None)
    if span is None:
        raise HTTPException(status_code=404, detail="span not found")
    item = DatasetItem(
        dataset_id=ds.id,
        project_id=project_id,
        input=item_input_from_span(span),
        expected_output=span.output,
        context=_retrieved_context(span, spans),
        metadata_={"model": span.model, "kind": str(span.kind)},
        source_trace_id=span.trace_id,
        source_span_id=span.span_id,
    )
    db.add(item)
    db.flush()
    return item_out(item)


@router.delete("/datasets/{dataset_id}/items/{item_id}", status_code=204)
def delete_item(dataset_id: str, item_id: str, db: DbSession, project_id: ProjectId) -> Response:
    ds = get_dataset_or_404(db, project_id, dataset_id)
    item = db.scalars(
        select(DatasetItem).where(
            DatasetItem.id == item_id, DatasetItem.dataset_id == ds.id, DatasetItem.project_id == project_id
        )
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    db.delete(item)
    return Response(status_code=204)


@router.get("/datasets/{dataset_id}/export.jsonl")
def export_jsonl(dataset_id: str, request: Request, db: DbSession, project_id: ProjectId) -> StreamingResponse:
    ds = get_dataset_or_404(db, project_id, dataset_id)
    factory = request.app.state.session_factory
    ds_id = ds.id

    def lines() -> Iterator[bytes]:
        # Own session: the request-scoped one may be closed before the body is streamed.
        session: Session = factory()
        try:
            rows = session.execute(
                select(DatasetItem.input, DatasetItem.expected_output, DatasetItem.context, DatasetItem.metadata_)
                .where(DatasetItem.dataset_id == ds_id, DatasetItem.project_id == project_id)
                .order_by(DatasetItem.created_at, DatasetItem.id)
                .execution_options(yield_per=_EXPORT_BATCH)
            )
            for inp, expected, context, metadata in rows:
                record = {
                    "input": inp,
                    "expected_output": expected,
                    "context": context or [],
                    "metadata": metadata or {},
                }
                yield (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        finally:
            session.close()

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="dataset-{ds_id}.jsonl"'},
    )
