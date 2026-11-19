"""DEPRECATED `main` MLOps routes for runs, metrics and artifacts (T4, ADR-013).

Thin adapters over the G1/G0 stack, kept so `main`'s SDK and web keep working until T9/T10 move
them to the canonical API: runs are `runs` rows (`/api/v1/runs*`), metric points go through the
same ingest as `POST /api/v1/runs/{id}/metrics` (`run_metrics`, M4, idempotent, capped) and blobs
through the G0 artifact store (`/api/v1/artifacts*`: dedup, quota, verified sha256). Responses keep
`main`'s shapes (`mlops.views`); translations are in `mlops.compat`. Every route is marked
`deprecated` in the OpenAPI document and answers with a `Deprecation: true` header.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server.api.artifacts import _limits, get_store, reserve_upload
from matrix_server.api.mlops import (
    MAX_CREATE_ATTEMPTS,
    _add_lineage_edge,
    _decode_cursor,
    _encode_cursor,
    _get_dataset_or_404,
    _iso_utc,
    _parse_body,
    _same_tz,
)
from matrix_server.api.runs import _check_params, _check_tags, capacity_http
from matrix_server.artifacts.models import Artifact as Blob
from matrix_server.artifacts.service import (
    PENDING_TTL,
    ArtifactError,
    HashMismatch,
    QuotaExceeded,
    complete_upload,
)
from matrix_server.auth import Principal, Role, require
from matrix_server.mlops import compat
from matrix_server.mlops.sweeps import build_sweep_analysis
from matrix_server.mlops.views import artifact_out, render_run, render_runs
from matrix_server.runs import service
from matrix_server.runs.models import Run, RunReport
from matrix_server.runs.store import MAX_MAX_POINTS, MIN_MAX_POINTS, SYS_PREFIX, MetricStore
from matrix_server.storage.db import as_utc, get_db, new_id, utcnow
from matrix_server.storage.models import ModelArtifact
from matrix_server.sweeps import service as sweep_service
from matrix_server.sweeps.engine import SweepValidationError
from matrix_server.sweeps.models import Sweep


def _deprecation_header(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</api/v1/runs>; rel="successor-version"'


router = APIRouter(
    prefix="/api/v1/mlops",
    tags=["mlops (deprecated)"],
    deprecated=True,
    dependencies=[Depends(_deprecation_header)],
)

DbSession = Annotated[Session, Depends(get_db)]
ViewerAuth = Annotated[Principal, Depends(require(Role.VIEWER))]
MemberAuth = Annotated[Principal, Depends(require(Role.MEMBER))]

MAX_PAGE = 500
DEFAULT_PAGE = 50
MAX_METRIC_POINTS_BATCH = 1000
MAX_LIST_KEYS = 100


# --------------------------------------------------------------------------- schemas (main's)


class RunCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, populate_by_name=True, extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    run_type: str = Field(default="sft", min_length=1, max_length=32)
    status: str = Field(default="running", min_length=1, max_length=16)
    base_model: str | None = Field(default=None, max_length=200)
    dataset_id: str | None = Field(default=None, max_length=32)
    sweep_id: str | None = Field(default=None, max_length=32)
    parent_run_id: str | None = Field(default=None, max_length=32)
    hyperparams: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("hyperparams", "hyperparameters")
    )
    hardware_info: dict[str, Any] | None = None
    classic_eval: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list, max_length=50)


class RunUpdate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    status: str | None = Field(default=None, min_length=1, max_length=16)
    total_steps: int | None = Field(default=None, ge=0)  # accepted, ignored: derived from the points
    hardware_info: dict[str, Any] | None = None
    classic_eval: dict[str, Any] | None = None
    summary_metrics: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=10_000)
    finished_at: datetime | None = None


class MetricPointIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    category: str = Field(default="train", min_length=1, max_length=64)
    metric_name: str = Field(min_length=1, max_length=200)
    step: int = Field(ge=0)
    epoch: float | None = None
    value: float
    recorded_at: datetime | str | None = None


class MetricsBatchIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    points: list[MetricPointIn] = Field(min_length=1, max_length=MAX_METRIC_POINTS_BATCH)


class PresignUploadIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, populate_by_name=True, extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="checkpoint", min_length=1, max_length=32)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    content_type: str = Field(default="application/octet-stream", min_length=1, max_length=200)
    run_id: str | None = Field(default=None, max_length=32, validation_alias=AliasChoices("run_id", "source_run_id"))
    external_uri: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactCommitIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    source_run_id: str | None = Field(default=None, max_length=32)
    size_bytes: int | None = Field(default=None, ge=0)


# --------------------------------------------------------------------------- helpers


def _store(request: Request) -> MetricStore:
    return request.app.state.metric_store


def get_run_or_404(db: Session, project_id: str, run_id: str, *, for_update: bool = False) -> Run:
    query = select(Run).where(Run.id == run_id, Run.project_id == project_id)
    if for_update:
        query = query.with_for_update()
    run = db.scalars(query).first()
    if run is None:
        raise HTTPException(status_code=404, detail="training run not found")
    return run


def _get_artifact_or_404(db: Session, project_id: str, artifact_id: str) -> ModelArtifact:
    row = db.scalars(
        select(ModelArtifact).where(ModelArtifact.id == artifact_id, ModelArtifact.project_id == project_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return row


def _checked(fn: Any, value: Any) -> Any:
    try:
        return fn(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _ingest(request: Request, run: Run, raw: list[dict[str, Any]], now: datetime) -> tuple[list[Any], list[Any]]:
    if not raw:
        return [], []
    return service.ingest_points(run, raw, _store(request), request.app.state.settings, now)


def _replace_classic_eval(db: Session, run: Run, classic_eval: dict[str, Any] | None) -> None:
    db.execute(delete(RunReport).where(RunReport.run_id == run.id, RunReport.name == compat.CLASSIC_EVAL_REPORT))
    for kind, name, data in compat.classic_eval_to_reports(classic_eval):
        db.add(RunReport(run_id=run.id, project_id=run.project_id, kind=kind, name=name, data=data))


def _backend(request: Request) -> str:
    return request.app.state.settings.artifact_backend


def _blob(db: Session, art: ModelArtifact) -> Blob | None:
    if art.artifact_id is None:
        return None
    return db.scalar(select(Blob).where(Blob.id == art.artifact_id, Blob.project_id == art.project_id))


# --------------------------------------------------------------------------- runs


@router.post("/runs", status_code=201)
def create_run(
    request: Request, db: DbSession, principal: MemberAuth, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    body: RunCreate = _parse_body(RunCreate, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    status = compat.status_in(body.status)
    if status is None:
        raise HTTPException(status_code=422, detail=f"invalid status '{body.status}'")
    tags = _checked(_check_tags, body.tags) or []
    params = _checked(_check_params, body.hyperparams)
    pid = principal.project_id
    if body.dataset_id is not None:
        _get_dataset_or_404(db, pid, body.dataset_id)
    if body.sweep_id is not None:
        _get_compat_sweep_or_404(db, pid, body.sweep_id)
    if body.parent_run_id is not None:
        get_run_or_404(db, pid, body.parent_run_id)
    try:
        service.check_capacity(db, request.app.state.settings, pid)
    except service.CapacityError as exc:
        raise capacity_http(exc) from None
    run = service.new_run(
        db,
        pid,
        name=name,
        tags=tags,
        params=params,
        env={"hardware": body.hardware_info} if body.hardware_info else None,
        run_type=body.run_type,
        base_model=body.base_model,
        dataset_version_id=body.dataset_id,
        sweep_id=body.sweep_id,
        parent_run_id=body.parent_run_id,
        created_by=principal.user_id,
    )
    now = utcnow()
    _ingest(request, run, compat.hardware_points(body.hardware_info), now)
    if status != "running":
        run.status, run.ended_at = status, now
    _replace_classic_eval(db, run, body.classic_eval)
    if body.dataset_id is not None:
        _add_lineage_edge(db, pid, "dataset", body.dataset_id, "run", run.id, "trained_on")
    if body.parent_run_id is not None:
        _add_lineage_edge(db, pid, "run", body.parent_run_id, "run", run.id, "forked_from")
    db.flush()
    return render_run(db, _store(request), run)


@router.get("/runs")
def list_runs(
    request: Request,
    db: DbSession,
    principal: ViewerAuth,
    status: str | None = None,
    sweep_id: str | None = None,
    run_type: str | None = None,
    base_model: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
    cursor: str | None = None,
) -> dict[str, Any]:
    stmt = select(Run).where(Run.project_id == principal.project_id)
    if status is not None:
        stmt = stmt.where(Run.status == (compat.status_in(status) or status))
    if sweep_id is not None:
        stmt = stmt.where(Run.sweep_id == sweep_id)
    if run_type is not None:
        stmt = stmt.where(Run.run_type == run_type)
    if base_model is not None:
        stmt = stmt.where(Run.base_model == base_model)
    if cursor is not None:
        ts, row_id = _decode_cursor(cursor)
        ts = _same_tz(db, ts)
        stmt = stmt.where(or_(Run.started_at < ts, and_(Run.started_at == ts, Run.id < row_id)))
    rows = list(db.scalars(stmt.order_by(Run.started_at.desc(), Run.id.desc()).limit(limit + 1)))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1].started_at, rows[-1].id)
    return {"items": render_runs(db, _store(request), rows), "next_cursor": next_cursor}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request, db: DbSession, principal: ViewerAuth) -> dict[str, Any]:
    run = get_run_or_404(db, principal.project_id, run_id)
    arts = db.scalars(
        select(ModelArtifact)
        .where(ModelArtifact.project_id == principal.project_id, ModelArtifact.run_id == run.id)
        .order_by(ModelArtifact.created_at.desc())
    )
    out = render_run(db, _store(request), run)
    out["artifacts"] = [artifact_out(a, _backend(request)) for a in arts]
    return out


@router.patch("/runs/{run_id}")
def update_run(
    run_id: str,
    request: Request,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    run = get_run_or_404(db, principal.project_id, run_id, for_update=True)
    body: RunUpdate = _parse_body(RunUpdate, payload)
    status = None
    if body.status is not None:
        status = compat.status_in(body.status)
        if status is None:
            raise HTTPException(status_code=422, detail=f"invalid status '{body.status}'")
        if status == "running" and run.status != "running":
            raise HTTPException(status_code=409, detail=f"run is {compat.status_out(run.status)}")
    now = utcnow()
    points: list[dict[str, Any]] = []
    if body.hardware_info is not None:
        env = dict(run.env) if isinstance(run.env, dict) else {}
        run.env = {**env, "hardware": body.hardware_info}
        points += compat.hardware_points(body.hardware_info)
    if body.summary_metrics:
        step = _store(request).last_steps(run.project_id, [run.id]).get(run.id, 0)
        points += [
            {"key": key, "step": step, "value": float(value)}
            for key, value in body.summary_metrics.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not key.startswith("_")
        ]
    if points and service.accepts_points(run, now):
        _ingest(request, run, points, now)
    if body.classic_eval is not None:
        _replace_classic_eval(db, run, body.classic_eval)
    if body.error is not None:
        run.error = body.error
    if status is not None and status != "running":
        run.status = status
        run.ended_at = body.finished_at or run.ended_at or now
    elif body.finished_at is not None and run.status != "running":
        run.ended_at = body.finished_at
    db.flush()
    return render_run(db, _store(request), run)


@router.post("/runs/{run_id}/metrics", status_code=202)
def ingest_run_metrics(
    run_id: str,
    request: Request,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    pid = principal.project_id
    run = get_run_or_404(db, pid, run_id, for_update=True)
    body: MetricsBatchIn = _parse_body(MetricsBatchIn, payload)
    now = utcnow()
    if not service.accepts_points(run, now):
        raise HTTPException(status_code=409, detail=f"run is {compat.status_out(run.status)}")
    raw, origin = compat.points_in(p.model_dump() for p in body.points)
    kept, rejected_raw = _ingest(request, run, raw, now)
    # Report in terms of `main`'s points: a point counts as accepted when its value was.
    rejected: dict[int, str] = {}
    for r in rejected_raw:
        index, is_epoch = origin[r.index]
        rejected.setdefault(index, f"epoch: {r.reason}" if is_epoch else r.reason)
    value_rejected = {origin[r.index][0] for r in rejected_raw if not origin[r.index][1]}

    latest: dict[str, tuple[int, float]] = {}
    for p in kept:
        if p.key not in latest or p.step >= latest[p.key][0]:
            latest[p.key] = (p.step, p.value)

    should_stop, stop_reason = False, None
    if run.sweep_id is not None:
        sweep = sweep_service.get_sweep(db, pid, run.sweep_id)
        if sweep is not None:
            if sweep.status in {"stopped", "finished"}:
                should_stop, stop_reason = True, f"Sweep is {sweep.status}"
            elif sweep.metric_key in latest:
                cur_step, cur_val = latest[sweep.metric_key]
                rep = sweep_service.report_metric(
                    db,
                    _store(request),
                    request.app.state.settings,
                    sweep,
                    run,
                    step=cur_step,
                    value=cur_val,
                    record_point=False,
                )
                if rep.action == "stop":
                    should_stop, stop_reason = True, rep.reason
    db.flush()
    return {
        "accepted": len(body.points) - len(value_rejected),
        "rejected": [{"index": i, "reason": reason} for i, reason in sorted(rejected.items())],
        "should_stop": should_stop,
        "stop_reason": stop_reason,
    }


@router.get("/runs/{run_id}/metrics")
def get_run_metrics(
    run_id: str,
    request: Request,
    db: DbSession,
    principal: ViewerAuth,
    metric_names: Annotated[list[str] | None, Query()] = None,
    category: str | None = None,
    min_step: Annotated[int | None, Query(ge=0, le=service.MAX_STEP)] = None,
    max_step: Annotated[int | None, Query(ge=0, le=service.MAX_STEP)] = None,
    max_points: Annotated[int, Query(ge=1, le=MAX_MAX_POINTS)] = 500,
) -> dict[str, Any]:
    pid = principal.project_id
    run = get_run_or_404(db, pid, run_id)
    store = _store(request)
    if metric_names is not None:
        names = [m.strip() for item in metric_names for m in item.split(",") if m.strip()]
        keys = list(dict.fromkeys(compat.metric_key(category, n) for n in names))[:MAX_LIST_KEYS]
    else:
        keys = [k for k in store.keys(pid, run.id) if k != compat.EPOCH_KEY]
    if category == "system":
        keys = [k for k in keys if k.startswith(SYS_PREFIX)]
    elif category is not None:
        keys = [k for k in keys if not k.startswith(SYS_PREFIX)]
    keys = keys[:MAX_LIST_KEYS]
    bounds = {"from_step": min_step, "to_step": max_step}
    series, _ = store.read_series(pid, [run.id], keys, max_points=max(MIN_MAX_POINTS, max_points), **bounds)
    epochs_series, _ = store.read_series(pid, [run.id], [compat.EPOCH_KEY], max_points=MAX_MAX_POINTS, **bounds)
    epoch_s = epochs_series.get(run.id, {}).get(compat.EPOCH_KEY)
    epochs = dict(zip(epoch_s.steps, epoch_s.values, strict=True)) if epoch_s else {}
    mine = series.get(run.id, {})
    formatted: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        s = mine.get(key)
        if s is None:
            continue
        formatted[key] = [
            {
                "step": step,
                "value": value,
                "min": value,
                "max": value,
                "epoch": epochs.get(step),
                "recorded_at": service.from_micros(ts).isoformat().replace("+00:00", "Z"),
            }
            for step, value, ts in zip(s.steps, s.values, s.ts_us, strict=True)
        ]
    return {"run_id": run_id, "series": formatted}


# --------------------------------------------------------------------------- artifacts


@router.post("/artifacts/presign-upload", status_code=201)
def presign_artifact_upload(
    request: Request, db: DbSession, principal: MemberAuth, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    body: PresignUploadIn = _parse_body(PresignUploadIn, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    try:
        sha = compat.validate_sha256(body.sha256)
        external = compat.validate_external_uri(body.external_uri) if body.external_uri is not None else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = request.app.state.settings
    if body.size_bytes > settings.max_artifact_bytes:
        raise HTTPException(
            status_code=422, detail=f"size_bytes exceeds max_artifact_bytes ({settings.max_artifact_bytes})"
        )
    pid = principal.project_id
    if body.run_id is not None:
        get_run_or_404(db, pid, body.run_id)

    art_id = new_id()
    blob: Blob | None = None
    upload: dict[str, Any] | None = None
    deduplicated = False
    if external is None:
        try:
            blob, upload = reserve_upload(
                db,
                request.app,
                principal,
                filename=name,
                size=body.size_bytes,
                sha256=sha,
                media_type=body.content_type,
                owner_kind="model_artifact",
                owner_id=art_id,
                base_url="",  # relative: `main`'s SDK authenticates only same-origin upload URLs
            )
            if get_store(request.app).exists(pid, sha):  # content already in the project: no upload
                complete_upload(db, get_store(request.app), blob, _limits(request.app))
                deduplicated = True
        except ArtifactError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None

    art: ModelArtifact | None = None
    for attempt in range(MAX_CREATE_ATTEMPTS):
        try:
            with db.begin_nested():
                current = db.scalar(
                    select(func.max(ModelArtifact.version)).where(
                        ModelArtifact.project_id == pid, ModelArtifact.name == name
                    )
                )
                art = ModelArtifact(
                    id=art_id,
                    project_id=pid,
                    run_id=body.run_id,
                    artifact_id=blob.id if blob is not None else None,
                    external_uri=external,
                    name=name,
                    kind=body.kind,
                    version=(current or 0) + 1,
                    sha256=sha,
                    size_bytes=body.size_bytes,
                    content_type=body.content_type,
                    status="committed" if deduplicated else "pending_upload",
                    metadata_=body.metadata,
                    committed_at=utcnow() if deduplicated else None,
                )
                db.add(art)
                db.flush()
            break
        except IntegrityError:
            if attempt == MAX_CREATE_ATTEMPTS - 1:
                raise HTTPException(status_code=409, detail="concurrent artifact creation, retry") from None
    assert art is not None
    if deduplicated and body.run_id is not None:
        _add_lineage_edge(db, pid, "run", body.run_id, "artifact", art.id, "produced")
    out = artifact_out(art, _backend(request))
    out["deduplicated"] = deduplicated
    out["upload_url"] = None if deduplicated or upload is None else upload["url"]
    out["upload_headers"] = None if deduplicated or upload is None else upload["headers"]
    return out


@router.post("/artifacts/{artifact_id}/commit")
def commit_artifact(
    artifact_id: str,
    request: Request,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    art = _get_artifact_or_404(db, principal.project_id, artifact_id)
    body: ArtifactCommitIn = _parse_body(ArtifactCommitIn, payload or {})
    source_run_id = body.source_run_id or art.run_id
    if source_run_id is not None:
        get_run_or_404(db, principal.project_id, source_run_id)
        art.run_id = source_run_id
    if art.external_uri is not None:
        if body.size_bytes is not None:
            art.size_bytes = body.size_bytes
    else:
        blob = _blob(db, art)
        if blob is None:
            raise HTTPException(status_code=409, detail="artifact content is not available; upload it again")
        if blob.status == "pending" and as_utc(blob.created_at) < utcnow() - PENDING_TTL:
            raise HTTPException(status_code=410, detail="upload expired")
        try:
            complete_upload(db, get_store(request.app), blob, _limits(request.app))
        except (HashMismatch, QuotaExceeded) as exc:
            db.commit()  # the failed upload's blob row is removed, as in POST /artifacts/{id}/complete
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
        except ArtifactError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
        art.sha256, art.size_bytes = blob.sha256, blob.size
    art.status = "committed"
    if art.committed_at is None:
        art.committed_at = utcnow()
    db.flush()
    if source_run_id is not None:
        _add_lineage_edge(db, principal.project_id, "run", source_run_id, "artifact", art.id, "produced")
    return artifact_out(art, _backend(request))


@router.get("/artifacts")
def list_artifacts(
    request: Request,
    db: DbSession,
    principal: ViewerAuth,
    run_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    stmt = select(ModelArtifact).where(ModelArtifact.project_id == principal.project_id)
    for column, value in (
        (ModelArtifact.run_id, run_id),
        (ModelArtifact.kind, kind),
        (ModelArtifact.status, status),
        (ModelArtifact.name, name),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    rows = db.scalars(stmt.order_by(ModelArtifact.created_at.desc(), ModelArtifact.version.desc()))
    return {"items": [artifact_out(a, _backend(request)) for a in rows]}


@router.get("/artifacts/{artifact_id}/download-url")
def get_artifact_download_url(
    artifact_id: str, request: Request, db: DbSession, principal: ViewerAuth
) -> dict[str, Any]:
    art = _get_artifact_or_404(db, principal.project_id, artifact_id)
    if art.external_uri is not None:
        url = art.external_uri
    else:
        blob = _blob(db, art)
        if blob is None:
            raise HTTPException(status_code=410, detail="artifact content is not available")
        if blob.status != "ready":
            raise HTTPException(status_code=409, detail="artifact upload not completed")
        ttl = request.app.state.settings.artifact_url_ttl_seconds
        presigned = get_store(request.app).presign_get(
            blob.project_id, blob.sha256, ttl, filename=blob.name, media_type=blob.media_type
        )
        url = presigned.url if presigned is not None else f"/api/v1/artifacts/{blob.id}/download"
    out = artifact_out(art, _backend(request))
    return {
        "artifact_id": art.id,
        "download_url": url,
        "storage_backend": out["storage_backend"],
        "sha256": art.sha256,
        "size_bytes": art.size_bytes,
    }


# --------------------------------------------------------------------------- sweeps (deprecated, T5)


class SweepCreateCompat(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    method: Literal["grid", "random", "bayes", "tpe"] = "random"
    metric_name: str = Field(min_length=1, max_length=200)
    metric_goal: Literal["minimize", "maximize"] = "minimize"
    search_space: dict[str, Any]
    early_stopping: dict[str, Any] | None = None
    max_trials: int | None = Field(default=None, ge=1)


class SweepNextTrialCompat(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    run_type: str = Field(default="sft", min_length=1, max_length=32)
    base_model: str | None = Field(default=None, max_length=200)
    dataset_id: str | None = Field(default=None, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=50)


def _get_compat_sweep_or_404(db: Session, project_id: str, sweep_id: str) -> Sweep:
    row = sweep_service.get_sweep(db, project_id, sweep_id)
    if row is None:
        raise HTTPException(status_code=404, detail="sweep not found")
    return row


def _legacy_early_stop_to_g2(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    strat = str(raw.get("strategy") or raw.get("type") or raw.get("method") or "none").lower()
    if strat == "none":
        return None
    if strat not in {"median", "asha", "hyperband"}:
        raise HTTPException(status_code=422, detail=f"unsupported early stopping strategy '{strat}'")
    out: dict[str, Any] = {
        "type": strat,
        "min_iter": int(raw.get("min_iter", raw.get("min_step", raw.get("min_t", 10)))),
        "eta": int(raw.get("eta", raw.get("reduction_factor", 3))),
    }
    if raw.get("max_iter") is not None:
        out["max_iter"] = int(raw["max_iter"])
    return out


def _g2_params_to_legacy(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, spec in params.items():
        if isinstance(spec, dict) and spec.get("type") == "choice":
            out[name] = {**spec, "type": "categorical"}
        elif isinstance(spec, dict) and spec.get("type") == "constant":
            out[name] = {"type": "categorical", "values": [spec.get("value")]}
        else:
            out[name] = dict(spec) if isinstance(spec, dict) else spec
    return out


def _compat_sweep_status(status: str) -> str:
    if status == "stopped":
        return "cancelled"
    if status == "finished":
        return "completed"
    return status


def _compat_sweep_out(sweep: Sweep) -> dict[str, Any]:
    cfg = dict(sweep.config or {})
    es = cfg.get("early_stop")
    legacy_es = None
    if isinstance(es, dict):
        legacy_es = {
            "strategy": es.get("type"),
            "min_step": es.get("min_iter"),
            "reduction_factor": es.get("eta", 3),
        }
        if "max_iter" in es:
            legacy_es["max_iter"] = es["max_iter"]
    return {
        "id": sweep.id,
        "project_id": sweep.project_id,
        "name": sweep.name,
        "description": sweep.description,
        "method": cfg.get("method", "random"),
        "metric_name": sweep.metric_key,
        "metric_goal": sweep.metric_goal,
        "search_space": _g2_params_to_legacy(cfg.get("parameters", {})),
        "early_stopping": legacy_es,
        "max_trials": cfg.get("max_runs"),
        "status": _compat_sweep_status(sweep.status),
        "best_run_id": sweep.best_run_id,
        "best_metric_value": sweep.best_value,
        "created_at": _iso_utc(sweep.created_at),
        "stopped_at": _iso_utc(sweep.stopped_at),
    }


@router.post("/sweeps", status_code=201)
def create_sweep_compat(
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: SweepCreateCompat = _parse_body(SweepCreateCompat, payload)
    g2_config: dict[str, Any] = {
        "method": body.method,
        "metric": {"key": body.metric_name.strip(), "goal": body.metric_goal},
        "parameters": body.search_space,
    }
    if body.max_trials is not None:
        g2_config["max_runs"] = body.max_trials
    es = _legacy_early_stop_to_g2(body.early_stopping)
    if es is not None:
        g2_config["early_stop"] = es
    try:
        sweep = sweep_service.create_sweep(
            db,
            principal.project_id,
            name=body.name,
            config=g2_config,
            created_by=principal.user_id,
            description=body.description,
            unique_name=True,
            allow_median=True,
            allow_float_grid=True,
        )
    except sweep_service.SweepConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except SweepValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _compat_sweep_out(sweep)


@router.get("/sweeps")
def list_sweeps_compat(db: DbSession, principal: ViewerAuth) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(Sweep)
            .where(Sweep.project_id == principal.project_id)
            .order_by(Sweep.created_at.desc(), Sweep.id.desc())
        )
    )
    return {"items": [_compat_sweep_out(s) for s in rows]}


@router.get("/sweeps/{sweep_id}")
def get_sweep_compat(
    sweep_id: str, request: Request, db: DbSession, principal: ViewerAuth
) -> dict[str, Any]:
    sweep = _get_compat_sweep_or_404(db, principal.project_id, sweep_id)
    runs = list(
        db.scalars(
            select(Run)
            .where(Run.project_id == principal.project_id, Run.sweep_id == sweep.id)
            .order_by(Run.started_at.asc(), Run.id.asc())
        )
    )
    trials = render_runs(db, _store(request), runs)
    out = _compat_sweep_out(sweep)
    out["trials"] = trials
    out["analysis"] = build_sweep_analysis(out, trials)
    return out


@router.post("/sweeps/{sweep_id}/trials/next", status_code=201)
def suggest_sweep_trial_compat(
    sweep_id: str,
    request: Request,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    body: SweepNextTrialCompat = _parse_body(SweepNextTrialCompat, payload or {})
    if body.dataset_id is not None:
        _get_dataset_or_404(db, principal.project_id, body.dataset_id)

    tags = list(dict.fromkeys(body.tags))
    try:
        _check_tags(tags)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    try:
        res = sweep_service.next_trial(
            db,
            _store(request),
            request.app.state.settings,
            principal.project_id,
            sweep_id,
            created_by=principal.user_id,
            run_name=body.name,
            run_type=body.run_type,
            base_model=body.base_model,
            dataset_version_id=body.dataset_id,
            tags=tags,
        )
    except service.CapacityError as exc:
        raise capacity_http(exc) from None
    except sweep_service.SweepConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    if res is None:
        raise HTTPException(status_code=404, detail="sweep not found")
    if res.outcome != "ok":
        raise HTTPException(status_code=409, detail=f"sweep is {res.outcome}")

    assert res.run is not None and res.sweep_run is not None and res.params is not None
    if body.dataset_id is not None:
        _add_lineage_edge(db, principal.project_id, "dataset", body.dataset_id, "run", res.run.id, "trained_on")

    return {
        "sweep_id": sweep_id,
        "trial_index": res.sweep_run.trial_index,
        "hyperparams": res.params,
        "run": render_run(db, _store(request), res.run),
    }


@router.post("/sweeps/{sweep_id}/stop")
def stop_sweep_compat(sweep_id: str, db: DbSession, principal: MemberAuth) -> dict[str, Any]:
    sweep = _get_compat_sweep_or_404(db, principal.project_id, sweep_id)
    sweep_service.patch_sweep_status(db, sweep, "stopped")
    out = _compat_sweep_out(sweep)
    out["stopped"] = True
    return out

