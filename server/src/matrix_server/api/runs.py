"""Training runs API (contract G1): runs, params, heartbeat, metrics, artifacts links and reports.

Reads need viewer, writes member, deleting a run admin (`require`, contract G0). Every query is
scoped by the principal's project: an id from another project is indistinguishable from an
unknown one (404, or ignored in `compare`). Bodies are validated by hand so a 422 never echoes the
input (which may hold NaN and would fail to serialize).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.orm import Session

from matrix_server import audit
from matrix_server.auth import Principal, Role, require
from matrix_server.domain.spans import UtcDatetime
from matrix_server.runs import service
from matrix_server.runs.models import Run, RunArtifact, RunReport, RunTag
from matrix_server.runs.names import generate_name
from matrix_server.runs.reports import ReportError, validate_report
from matrix_server.runs.store import (
    DEFAULT_MAX_POINTS,
    MAX_MAX_POINTS,
    MAX_SUMMARY_KEYS,
    MIN_MAX_POINTS,
    MetricStore,
    Series,
)
from matrix_server.storage import jobs
from matrix_server.storage.db import as_utc, get_db, utcnow

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["runs"])

# Module-level so tests (and future wiring) can override them in `app.dependency_overrides`.
VIEWER = require(Role.VIEWER)
MEMBER = require(Role.MEMBER)
ADMIN = require(Role.ADMIN)
Viewer = Annotated[Principal, Depends(VIEWER)]
Member = Annotated[Principal, Depends(MEMBER)]
Admin = Annotated[Principal, Depends(ADMIN)]
DbSession = Annotated[Session, Depends(get_db)]

MAX_PAGE = 200
MAX_TAGS = 50
MAX_PARAMS = 1000
MAX_PARAM_KEY_CHARS = 250
MAX_PARAMS_BYTES = 256 * 1024
MAX_PACKAGES = 500
MAX_ERROR_CHARS = 10_000
MAX_SERIES_KEYS = 100
MAX_COMPARE_RUNS = 20
MAX_COMPARE_KEYS = 20
MAX_REPORTS_PER_RUN = 500
MAX_ARTIFACTS_PER_RUN = 10_000
SORTS = ("-started_at", "started_at", "-name", "name")

_NO_CONTROL = re.compile(r"^[^\x00-\x1f\x7f]*$")

ShortText = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f\x7f]+$")]
RefId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]


# --------------------------------------------------------------------------- schemas


def _check_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    if len(tags) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")
    for tag in tags:
        if not isinstance(tag, str) or not (1 <= len(tag) <= 100) or not _NO_CONTROL.match(tag):
            raise ValueError("tags must be 1-100 characters without control characters")
    return list(dict.fromkeys(tags))


def _check_params(params: dict[str, Any]) -> dict[str, Any]:
    if len(params) > MAX_PARAMS:
        raise ValueError(f"at most {MAX_PARAMS} params")
    for key in params:
        if not (1 <= len(key) <= MAX_PARAM_KEY_CHARS) or not _NO_CONTROL.match(key):
            raise ValueError(f"param names must be 1-{MAX_PARAM_KEY_CHARS} characters")
    try:
        size = len(json.dumps(params, allow_nan=False, separators=(",", ":")).encode())
    except ValueError as exc:
        raise ValueError("params must not contain NaN or Infinity") from exc
    if size > MAX_PARAMS_BYTES:
        raise ValueError("params exceed 256 KiB")
    return params


class GitInfo(BaseModel):
    commit: Annotated[str | None, Field(max_length=100)] = None
    branch: Annotated[str | None, Field(max_length=300)] = None
    dirty: bool | None = None
    remote: Annotated[str | None, Field(max_length=1000)] = None


class EnvInfo(BaseModel):
    python: Annotated[str | None, Field(max_length=100)] = None
    platform: Annotated[str | None, Field(max_length=300)] = None
    hostname: Annotated[str | None, Field(max_length=300)] = None
    packages: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=MAX_PACKAGES)


class RunCreate(BaseModel):
    name: ShortText | None = None
    group: ShortText | None = None
    tags: list[str] | None = None
    params: dict[str, Any] | None = None
    git: GitInfo | None = None
    env: EnvInfo | None = None
    dataset_version_id: RefId | None = None
    sweep_id: RefId | None = None
    parent_run_id: RefId | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str] | None:
        return _check_tags(value)

    @field_validator("params")
    @classmethod
    def _params(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _check_params(value)


class RunPatch(BaseModel):
    name: ShortText | None = None
    tags: list[str] | None = None
    status: Literal["finished", "failed", "killed"] | None = None
    error: Annotated[str | None, Field(max_length=MAX_ERROR_CHARS)] = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str] | None:
        return _check_tags(value)


class ParamsIn(BaseModel):
    params: dict[str, Any]

    @field_validator("params")
    @classmethod
    def _params(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_params(value)


class MetricsIn(BaseModel):
    points: list[Any] = Field(max_length=service.MAX_POINTS_PER_REQUEST)


class ArtifactLinkIn(BaseModel):
    artifact_id: RefId
    name: Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f]+$")]
    kind: Literal["file", "model", "plot", "table", "report"]


class ReportIn(BaseModel):
    kind: Literal["classification", "regression", "importance", "table", "custom"]
    name: ShortText
    data: Any


class RunOut(BaseModel):
    id: str
    name: str
    status: str
    group: str | None
    tags: list[str]
    params: dict[str, Any]
    git: dict[str, Any] | None
    env: dict[str, Any] | None
    summary: dict[str, float | None]
    dataset_version_id: str | None
    sweep_id: str | None
    parent_run_id: str | None
    created_by: str | None
    started_at: UtcDatetime
    ended_at: UtcDatetime | None
    heartbeat_at: UtcDatetime | None
    error: str | None


class RunPage(BaseModel):
    items: list[RunOut]
    next_cursor: str | None = None


class ArtifactOut(BaseModel):
    id: str
    name: str
    media_type: str
    size: int
    sha256: str
    verified: bool
    owner_kind: str | None
    owner_id: str | None
    created_at: UtcDatetime
    created_by: str | None


class RunArtifactOut(BaseModel):
    artifact: ArtifactOut
    name: str
    kind: str


class RunArtifactList(BaseModel):
    items: list[RunArtifactOut]


class ReportOut(BaseModel):
    id: str
    run_id: str
    kind: str
    name: str
    data: Any
    created_at: UtcDatetime


class ReportList(BaseModel):
    items: list[ReportOut]


# --------------------------------------------------------------------------- helpers


def _parse(model: type[BaseModel], payload: Any) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_input=False, include_context=False, include_url=False)
        ) from exc


def _store(request: Request) -> MetricStore:
    return request.app.state.metric_store


def _not_found(what: str = "run") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def _get_run(db: Session, project_id: str, run_id: str, *, for_update: bool = False) -> Run:
    query = select(Run).where(Run.id == run_id, Run.project_id == project_id)
    if for_update:
        query = query.with_for_update()
    run = db.scalars(query).first()
    if run is None:
        raise _not_found()
    return run


def _run_out(run: Run, summary: dict[str, float | None] | None = None) -> RunOut:
    return RunOut(
        id=run.id,
        name=run.name,
        status=run.status,
        group=run.group,
        tags=list(run.tags or []),
        params=dict(run.params or {}),
        git=run.git,
        env=run.env,
        summary=summary or {},
        dataset_version_id=run.dataset_version_id,
        sweep_id=run.sweep_id,
        parent_run_id=run.parent_run_id,
        created_by=run.created_by,
        started_at=as_utc(run.started_at),  # type: ignore[arg-type]
        ended_at=as_utc(run.ended_at),
        heartbeat_at=as_utc(run.heartbeat_at),
        error=run.error,
    )


def _with_summary(request: Request, project_id: str, run: Run) -> RunOut:
    summaries = _store(request).summaries(project_id, [run.id], MAX_SUMMARY_KEYS)
    return _run_out(run, summaries.get(run.id))


def _set_tags(db: Session, run: Run, tags: list[str]) -> None:
    run.tags = tags
    db.execute(delete(RunTag).where(RunTag.run_id == run.id))
    for tag in tags:
        db.add(RunTag(run_id=run.id, project_id=run.project_id, tag=tag))


def _encode_cursor(sort: str, value: Any, run_id: str) -> str:
    raw = json.dumps([sort, value, run_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, sort: str) -> tuple[Any, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        cursor_sort, value, run_id = json.loads(raw)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    expected = int if sort.lstrip("-") == "started_at" else str
    if (
        cursor_sort != sort
        or not isinstance(run_id, str)
        or not isinstance(value, expected)
        or isinstance(value, bool)
    ):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return value, run_id


def _like(text: str) -> str:
    escaped = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _csv(value: str | None, *, limit: int, what: str) -> list[str]:
    items = list(dict.fromkeys(s.strip() for s in (value or "").split(",") if s.strip()))
    if len(items) > limit:
        raise HTTPException(status_code=422, detail=f"at most {limit} {what}")
    return items


def _iso(micros: int) -> str:
    return service.from_micros(micros).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _series_out(series: Series | None) -> dict[str, list[Any]]:
    if series is None:
        return {"steps": [], "values": [], "ts": []}
    return {"steps": series.steps, "values": series.values, "ts": [_iso(t) for t in series.ts_us]}


MaxPoints = Annotated[int, Query(ge=MIN_MAX_POINTS, le=MAX_MAX_POINTS)]
StepBound = Annotated[int | None, Query(ge=0, le=service.MAX_STEP)]


# --------------------------------------------------------------------------- runs


@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(principal: Member, db: DbSession, payload: Annotated[Any, Body()] = None) -> RunOut:
    body: RunCreate = _parse(RunCreate, payload if payload is not None else {})
    project_id = principal.project_id
    if body.parent_run_id is not None and db.scalar(
        select(Run.id).where(Run.id == body.parent_run_id, Run.project_id == project_id)
    ) is None:
        raise HTTPException(status_code=422, detail="parent run not found")
    name = body.name
    if name is None:
        ordinal = db.scalar(select(func.count()).select_from(Run).where(Run.project_id == project_id)) or 0
        name = generate_name(ordinal + 1)
    now = utcnow()
    run = Run(
        project_id=project_id,
        name=name,
        status="running",
        group=body.group,
        tags=[],
        params=body.params or {},
        git=body.git.model_dump() if body.git else None,
        env=body.env.model_dump() if body.env else None,
        dataset_version_id=body.dataset_version_id,
        sweep_id=body.sweep_id,
        parent_run_id=body.parent_run_id,
        created_by=principal.user_id,
        started_at=now,
        heartbeat_at=now,
        point_count=0,
        key_counts={},
    )
    db.add(run)
    db.flush()
    _set_tags(db, run, body.tags or [])
    # Runs need the reaper; the worker keeps it rescheduled from then on.
    service.ensure_reaper_scheduled(db)
    db.commit()
    return _run_out(run)


@router.get("/runs", response_model=RunPage)
def list_runs(
    request: Request,
    principal: Viewer,
    db: DbSession,
    status: Literal["running", "finished", "failed", "killed"] | None = None,
    group: str | None = None,
    tag: str | None = None,
    name: str | None = None,
    sweep_id: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["-started_at", "started_at", "-name", "name"] = "-started_at",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    cursor: str | None = None,
) -> RunPage:
    project_id = principal.project_id
    query = select(Run).where(Run.project_id == project_id)
    if status is not None:
        query = query.where(Run.status == status)
    if group is not None:
        query = query.where(Run.group == group)
    if name is not None:
        query = query.where(Run.name == name)
    if sweep_id is not None:
        query = query.where(Run.sweep_id == sweep_id)
    if tag is not None:
        query = query.where(
            exists().where(RunTag.run_id == Run.id, RunTag.project_id == project_id, RunTag.tag == tag)
        )
    if q:
        pattern = _like(q)
        query = query.where(
            or_(
                func.lower(Run.name).like(pattern, escape="\\"),
                func.lower(Run.group).like(pattern, escape="\\"),
                exists().where(
                    RunTag.run_id == Run.id,
                    RunTag.project_id == project_id,
                    func.lower(RunTag.tag).like(pattern, escape="\\"),
                ),
            )
        )
    by_time = sort.lstrip("-") == "started_at"
    column = Run.started_at if by_time else Run.name
    descending = sort.startswith("-")
    if cursor:
        value, after_id = _decode_cursor(cursor, sort)
        bound = service.from_micros(value) if by_time else value
        if descending:
            query = query.where(or_(column < bound, and_(column == bound, Run.id < after_id)))
        else:
            query = query.where(or_(column > bound, and_(column == bound, Run.id > after_id)))
    order = (column.desc(), Run.id.desc()) if descending else (column.asc(), Run.id.asc())
    rows = list(db.scalars(query.order_by(*order).limit(limit + 1)).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        key = service.to_micros(as_utc(last.started_at)) if by_time else last.name  # type: ignore[arg-type]
        next_cursor = _encode_cursor(sort, key, last.id)
    summaries = _store(request).summaries(project_id, [r.id for r in rows], MAX_SUMMARY_KEYS) if rows else {}
    return RunPage(items=[_run_out(r, summaries.get(r.id)) for r in rows], next_cursor=next_cursor)


@router.get("/runs/compare")
def compare_runs(
    request: Request,
    principal: Viewer,
    db: DbSession,
    run_ids: str,
    keys: str,
    max_points: MaxPoints = DEFAULT_MAX_POINTS,
) -> dict[str, Any]:
    ids = _csv(run_ids, limit=MAX_COMPARE_RUNS, what="runs")
    names = _csv(keys, limit=MAX_COMPARE_KEYS, what="keys")
    if not ids or not names:
        raise HTTPException(status_code=422, detail="run_ids and keys are required")
    project_id = principal.project_id
    # Only runs of this project: unknown or foreign ids are left out of the answer.
    found = dict(db.execute(select(Run.id, Run.name).where(Run.project_id == project_id, Run.id.in_(ids))).all())
    owned = [i for i in ids if i in found]
    series, _ = _store(request).read_series(project_id, owned, names, max_points=max_points)
    return {
        "runs": {
            run_id: {
                "name": found[run_id],
                "series": {key: _series_out(series.get(run_id, {}).get(key)) for key in names},
            }
            for run_id in owned
        }
    }


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(request: Request, run_id: str, principal: Viewer, db: DbSession) -> RunOut:
    return _with_summary(request, principal.project_id, _get_run(db, principal.project_id, run_id))


@router.patch("/runs/{run_id}", response_model=RunOut)
def patch_run(
    request: Request, run_id: str, principal: Member, db: DbSession, payload: Annotated[Any, Body()] = None
) -> RunOut:
    body: RunPatch = _parse(RunPatch, payload if payload is not None else {})
    run = _get_run(db, principal.project_id, run_id, for_update=True)
    if body.name is not None:
        run.name = body.name
    if body.tags is not None:
        _set_tags(db, run, body.tags)
    if body.status is not None:
        if run.ended_at is None:
            run.ended_at = utcnow()
        run.status = body.status
    if "error" in body.model_fields_set:
        run.error = body.error
    db.commit()
    return _with_summary(request, principal.project_id, run)


@router.post("/runs/{run_id}/params", response_model=RunOut)
def log_params(
    request: Request, run_id: str, principal: Member, db: DbSession, payload: Annotated[Any, Body()] = None
) -> Any:
    body: ParamsIn = _parse(ParamsIn, payload)
    run = _get_run(db, principal.project_id, run_id, for_update=True)
    merged, conflicts = service.merge_params(dict(run.params or {}), body.params)
    if conflicts:
        db.rollback()
        return JSONResponse(
            status_code=409, content={"detail": "params are immutable once set", "conflicts": conflicts}
        )
    run.params = merged
    db.commit()
    return _with_summary(request, principal.project_id, run)


@router.post("/runs/{run_id}/heartbeat", status_code=204)
def heartbeat(run_id: str, principal: Member, db: DbSession) -> Response:
    run = _get_run(db, principal.project_id, run_id, for_update=True)
    now = utcnow()
    if run.status == "running":
        run.heartbeat_at = now
    elif run.status == "failed" and run.error == service.LOST_HEARTBEAT:
        # The reaper gave up on a run that is still alive (e.g. a long network outage): revive it.
        run.status, run.error, run.ended_at, run.heartbeat_at = "running", None, None, now
    db.commit()
    return Response(status_code=204)


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(request: Request, run_id: str, principal: Admin, db: DbSession) -> Response:
    project_id = principal.project_id
    run = _get_run(db, project_id, run_id, for_update=True)
    name = run.name
    db.execute(delete(RunReport).where(RunReport.run_id == run.id))
    db.execute(delete(RunArtifact).where(RunArtifact.run_id == run.id))
    db.execute(delete(RunTag).where(RunTag.run_id == run.id))
    db.execute(
        update(Run)
        .where(Run.parent_run_id == run.id)
        .values(parent_run_id=None)
        .execution_options(synchronize_session=False)
    )
    db.delete(run)
    # The points go asynchronously: the job retries the store delete if the inline one fails.
    jobs.enqueue(db, project_id, service.PURGE_KIND, {"run_id": run_id})
    audit.record(
        db,
        action="run.delete",
        project_id=project_id,
        actor_kind=principal.kind,
        actor_id=principal.user_id,
        target=run_id,
        detail={"name": name},
    )
    db.commit()
    try:
        _store(request).delete_run(project_id, run_id)
    except Exception as exc:  # noqa: BLE001 - the purge job removes the points later
        log.warning("run %s: inline metric delete failed (%s); left to the purge job", run_id, type(exc).__name__)
    return Response(status_code=204)


# --------------------------------------------------------------------------- metrics


@router.post("/runs/{run_id}/metrics", status_code=202)
def log_metrics(
    request: Request, run_id: str, principal: Member, db: DbSession, payload: Annotated[Any, Body()] = None
) -> dict[str, Any]:
    body: MetricsIn = _parse(MetricsIn, payload)
    project_id = principal.project_id
    # Row lock: concurrent batches of one run serialize on the counters.
    run = _get_run(db, project_id, run_id, for_update=True)
    now = utcnow()
    if not service.accepts_points(run, now):
        raise HTTPException(status_code=409, detail=f"run is {run.status}")
    parsed, rejected = service.parse_points(body.points, now)
    settings = request.app.state.settings
    kept, capped = service.apply_point_caps(
        run,
        parsed,
        max_per_run=settings.max_points_per_run,
        max_per_key=settings.max_points_per_key,
    )
    if kept:
        _store(request).insert(project_id, run.id, kept)
        if run.status == "running":
            run.heartbeat_at = now
    db.commit()
    rejected = sorted(rejected + capped, key=lambda r: r.index)
    return {"accepted": len(kept), "rejected": [{"index": r.index, "reason": r.reason} for r in rejected]}


@router.get("/runs/{run_id}/metric-keys")
def metric_keys(request: Request, run_id: str, principal: Viewer, db: DbSession) -> dict[str, Any]:
    run = _get_run(db, principal.project_id, run_id)
    stats = _store(request).key_stats(principal.project_id, run.id)
    return {
        "keys": [
            {
                "key": s.key,
                "count": s.count,
                "first_step": s.first_step,
                "last_step": s.last_step,
                "last_value": s.last_value,
                "min": s.min,
                "max": s.max,
            }
            for s in stats
        ]
    }


@router.get("/runs/{run_id}/metrics")
def read_metrics(
    request: Request,
    run_id: str,
    principal: Viewer,
    db: DbSession,
    keys: str | None = None,
    max_points: MaxPoints = DEFAULT_MAX_POINTS,
    x: Literal["step", "time"] = "step",
    from_step: StepBound = None,
    to_step: StepBound = None,
) -> dict[str, Any]:
    project_id = principal.project_id
    run = _get_run(db, project_id, run_id)
    store = _store(request)
    names = _csv(keys, limit=MAX_SERIES_KEYS, what="keys")
    if not names:
        names = store.keys(project_id, run.id, limit=MAX_SERIES_KEYS + 1)
        if len(names) > MAX_SERIES_KEYS:
            raise HTTPException(status_code=422, detail=f"run has more than {MAX_SERIES_KEYS} keys; pass keys")
    series, downsampled = store.read_series(
        project_id, [run.id], names, max_points=max_points, x=x, from_step=from_step, to_step=to_step
    )
    mine = series.get(run.id, {})
    return {"series": {key: _series_out(mine.get(key)) for key in names}, "downsampled": downsampled}


# --------------------------------------------------------------------------- artifacts


@router.post("/runs/{run_id}/artifacts", response_model=RunArtifactOut, status_code=201)
def link_artifact(
    run_id: str, principal: Member, db: DbSession, payload: Annotated[Any, Body()] = None
) -> RunArtifactOut:
    body: ArtifactLinkIn = _parse(ArtifactLinkIn, payload)
    project_id = principal.project_id
    run = _get_run(db, project_id, run_id, for_update=True)
    if not service.artifact_exists(db, project_id, body.artifact_id):
        raise _not_found("artifact")
    link = db.scalars(
        select(RunArtifact).where(RunArtifact.run_id == run.id, RunArtifact.artifact_id == body.artifact_id)
    ).first()
    if link is None:
        count = db.scalar(select(func.count()).select_from(RunArtifact).where(RunArtifact.run_id == run.id))
        if (count or 0) >= MAX_ARTIFACTS_PER_RUN:
            raise HTTPException(status_code=409, detail=f"run has the maximum of {MAX_ARTIFACTS_PER_RUN} artifacts")
        link = RunArtifact(run_id=run.id, project_id=project_id, artifact_id=body.artifact_id)
        db.add(link)
    # Re-linking the same artifact (an SDK retry) updates the name and kind.
    link.name, link.kind = body.name, body.kind
    db.flush()
    artifact = service.load_artifacts(db, project_id, [body.artifact_id])[body.artifact_id]
    db.commit()
    return RunArtifactOut(artifact=ArtifactOut(**artifact), name=link.name, kind=link.kind)


@router.get("/runs/{run_id}/artifacts", response_model=RunArtifactList)
def list_run_artifacts(run_id: str, principal: Viewer, db: DbSession) -> RunArtifactList:
    project_id = principal.project_id
    run = _get_run(db, project_id, run_id)
    links = db.scalars(
        select(RunArtifact)
        .where(RunArtifact.run_id == run.id, RunArtifact.project_id == project_id)
        .order_by(RunArtifact.created_at, RunArtifact.id)
    ).all()
    artifacts = service.load_artifacts(db, project_id, [link.artifact_id for link in links])
    return RunArtifactList(
        items=[
            RunArtifactOut(artifact=ArtifactOut(**artifacts[link.artifact_id]), name=link.name, kind=link.kind)
            for link in links
            if link.artifact_id in artifacts
        ]
    )


# --------------------------------------------------------------------------- reports


def _report_out(report: RunReport) -> ReportOut:
    return ReportOut(
        id=report.id,
        run_id=report.run_id,
        kind=report.kind,
        name=report.name,
        data=report.data,
        created_at=as_utc(report.created_at),  # type: ignore[arg-type]
    )


@router.post("/runs/{run_id}/reports", response_model=ReportOut, status_code=201)
def create_report(
    run_id: str, principal: Member, db: DbSession, payload: Annotated[Any, Body()] = None
) -> ReportOut:
    body: ReportIn = _parse(ReportIn, payload)
    try:
        data = validate_report(body.kind, body.data)
    except ReportError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    project_id = principal.project_id
    run = _get_run(db, project_id, run_id, for_update=True)
    count = db.scalar(select(func.count()).select_from(RunReport).where(RunReport.run_id == run.id))
    if (count or 0) >= MAX_REPORTS_PER_RUN:
        raise HTTPException(status_code=409, detail=f"run has the maximum of {MAX_REPORTS_PER_RUN} reports")
    report = RunReport(run_id=run.id, project_id=project_id, kind=body.kind, name=body.name, data=data)
    db.add(report)
    db.commit()
    return _report_out(report)


@router.get("/runs/{run_id}/reports", response_model=ReportList)
def list_reports(
    run_id: str,
    principal: Viewer,
    db: DbSession,
    kind: Literal["classification", "regression", "importance", "table", "custom"] | None = None,
) -> ReportList:
    project_id = principal.project_id
    run = _get_run(db, project_id, run_id)
    query = select(RunReport).where(RunReport.run_id == run.id, RunReport.project_id == project_id)
    if kind is not None:
        query = query.where(RunReport.kind == kind)
    rows = db.scalars(query.order_by(RunReport.created_at, RunReport.id)).all()
    return ReportList(items=[_report_out(r) for r in rows])


@router.delete("/runs/{run_id}/reports/{report_id}", status_code=204)
def delete_report(run_id: str, report_id: str, principal: Member, db: DbSession) -> Response:
    project_id = principal.project_id
    run = _get_run(db, project_id, run_id)
    result = db.execute(
        delete(RunReport).where(
            RunReport.id == report_id, RunReport.run_id == run.id, RunReport.project_id == project_id
        )
    )
    if not result.rowcount:
        raise _not_found("report")
    db.commit()
    return Response(status_code=204)

