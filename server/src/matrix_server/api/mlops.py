"""MLOps Studio API: Training Runs, Metrics, Sweeps, Artifacts, Lineage & Model/LoRA Registry."""

from __future__ import annotations

import base64
import json
import math
import re
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server import audit
from matrix_server.api.artifacts import get_store
from matrix_server.artifacts.models import Artifact as BlobArtifact
from matrix_server.auth import Principal, Role, ensure_role, require
from matrix_server.mlops.lineage import (
    DEFAULT_LINEAGE_DEPTH,
    MAX_LINEAGE_DEPTH,
    MAX_LINEAGE_NODES,
    Direction,
    node_exists_in_project,
    parse_node_ref,
    query_bounded_lineage,
)
from matrix_server.mlops.registry_models import ModelStageHistory, ModelWebhook, WebhookDelivery
from matrix_server.mlops.webhooks import (
    SecretKeyMissingError,
    WebhookValidationError,
    dispatch_model_event,
    encrypt_secret,
    generate_webhook_secret,
    resolve_secret_key,
    validate_webhook_events,
    validate_webhook_url,
)
from matrix_server.runs.models import Run
from matrix_server.storage.db import as_utc, get_db, utcnow
from matrix_server.storage.jobs import enqueue
from matrix_server.storage.models import (
    ArtifactLineage,
    BenchmarkRun,
    Dataset,
    Experiment,
    ModelAlias,
    ModelVersion,
    RegisteredModel,
    SafetyScan,
)
from matrix_server.storage.models import ModelArtifact as Artifact

router = APIRouter(prefix="/api/v1/mlops", tags=["mlops"])
canonical_router = APIRouter(prefix="/api/v1", tags=["mlops"])

DbSession = Annotated[Session, Depends(get_db)]
ViewerAuth = Annotated[Principal, Depends(require(Role.VIEWER))]
MemberAuth = Annotated[Principal, Depends(require(Role.MEMBER))]
AdminAuth = Annotated[Principal, Depends(require(Role.ADMIN))]

MAX_CREATE_ATTEMPTS = 3
MAX_PAGE = 500
DEFAULT_PAGE = 50
VALID_MODEL_STAGES = {"none", "staging", "production", "archived"}
ADMIN_ONLY_ALIASES = {"production", "champion"}
# Promoting to production and archiving are ADMIN decisions (an API key is never ADMIN).
ADMIN_ONLY_STAGES = {"production", "archived"}
ALIAS_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")


# ---------------------------------------------------------------------------
# Helpers: validation, serialization, cursor pagination, anti-IDOR
# ---------------------------------------------------------------------------


def _iso_utc(value: Any) -> str | None:
    dt = as_utc(value)
    return None if dt is None else dt.isoformat().replace("+00:00", "Z")


def _reject_non_finite(obj: Any) -> None:
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise HTTPException(status_code=422, detail="body must not contain NaN or Infinity")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _reject_non_finite(k)
            _reject_non_finite(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _reject_non_finite(item)


def _parse_body(model: type[BaseModel], payload: Any) -> Any:
    """Validate payload without echoing potentially sensitive/NaN inputs on 422."""
    _reject_non_finite(payload)
    try:
        body = model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_input=False, include_context=False, include_url=False),
        ) from exc
    dumped = body.model_dump(mode="json")
    _reject_non_finite(dumped)
    try:
        json.dumps(dumped, allow_nan=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="body must not contain NaN or Infinity") from exc
    return body


def _encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = f"{_iso_utc(created_at)}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        stamp, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        ts = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    return ts, row_id


def _same_tz(db: Session, ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if db.get_bind().dialect.name == "sqlite" else ts


def _get_dataset_or_404(db: Session, project_id: str, dataset_id: str) -> Dataset:
    row = db.scalars(select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return row


def _get_run_or_404(db: Session, project_id: str, run_id: str) -> Run:
    row = db.scalars(select(Run).where(Run.id == run_id, Run.project_id == project_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="training run not found")
    return row


def _get_artifact_or_404(db: Session, project_id: str, artifact_id: str) -> Artifact:
    row = db.scalars(select(Artifact).where(Artifact.id == artifact_id, Artifact.project_id == project_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return row


def _resolve_or_bridge_artifact(
    db: Session,
    project_id: str,
    artifact_id: str,
    run_id: str | None,
) -> Artifact:
    """Resolve a ModelArtifact by ID, or bridge a ready G0 BlobArtifact into a committed ModelArtifact."""
    row = db.scalars(select(Artifact).where(Artifact.id == artifact_id, Artifact.project_id == project_id)).first()
    if row is not None:
        return row

    blob = db.scalars(
        select(BlobArtifact).where(BlobArtifact.id == artifact_id, BlobArtifact.project_id == project_id)
    ).first()
    if blob is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    if blob.status != "ready":
        raise HTTPException(status_code=409, detail="artifact is not ready")

    existing = db.scalars(
        select(Artifact)
        .where(Artifact.project_id == project_id, Artifact.artifact_id == blob.id)
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
    ).first()
    if existing is not None:
        if existing.run_id is None and run_id is not None:
            existing.run_id = run_id
        return existing

    name = (blob.name or "artifact")[:200]
    for attempt in range(MAX_CREATE_ATTEMPTS):
        try:
            with db.begin_nested():
                current_ver = db.scalar(
                    select(func.max(Artifact.version)).where(
                        Artifact.project_id == project_id,
                        Artifact.name == name,
                    )
                )
                bridged = Artifact(
                    project_id=project_id,
                    run_id=run_id,
                    artifact_id=blob.id,
                    name=name,
                    kind="model",
                    version=(current_ver or 0) + 1,
                    sha256=blob.sha256,
                    size_bytes=blob.size,
                    content_type=blob.media_type,
                    status="committed",
                    committed_at=utcnow(),
                )
                db.add(bridged)
                db.flush()
            return bridged
        except IntegrityError:
            if attempt == MAX_CREATE_ATTEMPTS - 1:
                raise HTTPException(status_code=409, detail="concurrent artifact registration, retry") from None
    raise HTTPException(status_code=409, detail="concurrent artifact registration, retry")


def _get_experiment_or_404(db: Session, project_id: str, experiment_id: str) -> Experiment:
    row = db.scalars(
        select(Experiment).where(Experiment.id == experiment_id, Experiment.project_id == project_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return row


def _get_safety_scan_or_404(db: Session, project_id: str, scan_id: str) -> SafetyScan:
    row = db.scalars(select(SafetyScan).where(SafetyScan.id == scan_id, SafetyScan.project_id == project_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="safety scan not found")
    return row


def _get_model_or_404(db: Session, project_id: str, model_id: str) -> RegisteredModel:
    row = db.scalars(
        select(RegisteredModel).where(RegisteredModel.id == model_id, RegisteredModel.project_id == project_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="registered model not found")
    return row


def _get_model_version_or_404(db: Session, project_id: str, model_id: str, version: int) -> ModelVersion:
    row = db.scalars(
        select(ModelVersion).where(
            ModelVersion.project_id == project_id,
            ModelVersion.model_id == model_id,
            ModelVersion.version == version,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="model version not found")
    return row


def _add_lineage_edge(
    db: Session,
    project_id: str,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    relation: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    existing = db.scalar(
        select(ArtifactLineage.id).where(
            ArtifactLineage.project_id == project_id,
            ArtifactLineage.source_type == source_type,
            ArtifactLineage.source_id == source_id,
            ArtifactLineage.target_type == target_type,
            ArtifactLineage.target_id == target_id,
            ArtifactLineage.relation == relation,
        )
    )
    if existing is not None:
        return
    try:
        with db.begin_nested():
            db.add(
                ArtifactLineage(
                    project_id=project_id,
                    source_type=source_type,
                    source_id=source_id,
                    target_type=target_type,
                    target_id=target_id,
                    relation=relation,
                    metadata_=metadata or {},
                )
            )
            db.flush()
    except IntegrityError:
        pass


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _model_out(model: RegisteredModel) -> dict[str, Any]:
    return {
        "id": model.id,
        "project_id": model.project_id,
        "name": model.name,
        "description": model.description,
        "model_type": model.model_type,
        "base_model": model.base_model,
        "tags": list(model.tags or []),
        "created_at": _iso_utc(model.created_at),
        "updated_at": _iso_utc(model.updated_at),
    }


def _version_out(mv: ModelVersion) -> dict[str, Any]:
    return {
        "id": mv.id,
        "project_id": mv.project_id,
        "model_id": mv.model_id,
        "version": mv.version,
        "artifact_id": mv.artifact_id,
        "run_id": mv.run_id,
        "eval_experiment_id": mv.eval_experiment_id,
        "safety_scan_id": mv.safety_scan_id,
        "stage": mv.stage,
        "lora_config": mv.lora_config,
        "signature": mv.signature,
        "metrics_snapshot": dict(mv.metrics_snapshot or {}),
        "model_card": mv.model_card,
        "created_by": mv.created_by,
        "created_at": _iso_utc(mv.created_at),
    }


def _history_out(h: ModelStageHistory) -> dict[str, Any]:
    return {
        "id": h.id,
        "project_id": h.project_id,
        "model_id": h.model_id,
        "version": h.version,
        "from_stage": h.from_stage,
        "to_stage": h.to_stage,
        "actor_kind": h.actor_kind,
        "actor_id": h.actor_id,
        "comment": h.comment,
        "ts": _iso_utc(h.ts),
    }


def _webhook_out(wh: ModelWebhook, *, secret: str | None = None) -> dict[str, Any]:
    """Serialize a webhook. `secret` is ONLY included on the initial 201 creation response."""
    out: dict[str, Any] = {
        "id": wh.id,
        "project_id": wh.project_id,
        "model_id": wh.model_id,
        "url": wh.url,
        "events": list(wh.events or []),
        "active": bool(wh.active),
        "created_by": wh.created_by,
        "created_at": _iso_utc(wh.created_at),
    }
    if secret is not None:
        out["secret"] = secret
    return out


def _delivery_out(d: WebhookDelivery) -> dict[str, Any]:
    """Serialize a webhook delivery status. Never exposes response body or secret."""
    return {
        "id": d.id,
        "project_id": d.project_id,
        "webhook_id": d.webhook_id,
        "model_id": d.model_id,
        "event": d.event,
        "status": d.status,
        "attempts": d.attempts,
        "last_status_code": d.last_status_code,
        "last_error": d.last_error,
        "created_at": _iso_utc(d.created_at),
        "last_attempt_at": _iso_utc(d.last_attempt_at),
        "delivered_at": _iso_utc(d.delivered_at),
    }


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class ModelCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    model_type: str = Field(default="llm", min_length=1, max_length=32)
    base_model: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=50)


class ModelVersionCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    artifact_id: str | None = Field(default=None, max_length=32)
    run_id: str | None = Field(default=None, max_length=32)
    eval_experiment_id: str | None = Field(default=None, max_length=32)
    safety_scan_id: str | None = Field(default=None, max_length=32)
    stage: Literal["none", "staging", "production", "archived"] = "none"
    lora_config: dict[str, Any] | None = None
    signature: dict[str, Any] | None = None
    metrics_snapshot: dict[str, Any] = Field(default_factory=dict)
    model_card: str | None = Field(default=None, max_length=100_000)


class ModelStagePatch(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    stage: Literal["none", "staging", "production", "archived"]
    comment: str | None = Field(default=None, max_length=2000)


class ModelAliasIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    alias: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)


class ModelAliasVersionIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    version: int = Field(ge=1)


class WebhookCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    url: str = Field(min_length=1, max_length=2000)
    events: list[str] = Field(min_length=1, max_length=10)
    secret: str | None = Field(default=None, min_length=8, max_length=256)


class ModelEvaluateIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="ignore")

    dataset_id: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, max_length=200)
    metrics: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


# ---------------------------------------------------------------------------
# Lineage Graph Endpoints (Bounded, Indexed Per-Hop BFS)
# ---------------------------------------------------------------------------


@canonical_router.get("/lineage")
def get_bounded_lineage(
    db: DbSession,
    principal: ViewerAuth,
    node: Annotated[str, Query(min_length=3, max_length=100)],
    direction: Direction = "both",
    depth: Annotated[int, Query(ge=0, le=MAX_LINEAGE_DEPTH)] = DEFAULT_LINEAGE_DEPTH,
    max_nodes: Annotated[int, Query(ge=1, le=MAX_LINEAGE_NODES)] = MAX_LINEAGE_NODES,
) -> dict[str, Any]:
    try:
        kind, entity_id = parse_node_ref(node)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not node_exists_in_project(db, principal.project_id, kind, entity_id):
        raise HTTPException(status_code=404, detail="lineage node not found")
    nodes, edges, truncated = query_bounded_lineage(
        db,
        principal.project_id,
        [(kind, entity_id)],
        direction=direction,
        depth=depth,
        max_nodes=max_nodes,
    )
    return {
        "root": f"{kind}:{entity_id}",
        "direction": direction,
        "depth": depth,
        "truncated": truncated,
        "nodes": nodes,
        "edges": edges,
    }


@router.get("/runs/{run_id}/lineage")
def get_run_lineage(
    run_id: str,
    db: DbSession,
    principal: ViewerAuth,
    direction: Direction = "both",
    depth: Annotated[int, Query(ge=0, le=MAX_LINEAGE_DEPTH)] = DEFAULT_LINEAGE_DEPTH,
) -> dict[str, Any]:
    _get_run_or_404(db, principal.project_id, run_id)
    nodes, edges, truncated = query_bounded_lineage(
        db,
        principal.project_id,
        [("run", run_id)],
        direction=direction,
        depth=depth,
    )
    return {"run_id": run_id, "nodes": nodes, "edges": edges, "truncated": truncated}


# ---------------------------------------------------------------------------
# Model & LoRA Registry
# ---------------------------------------------------------------------------


@router.post("/models", status_code=201)
def create_model(
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: ModelCreate = _parse_body(ModelCreate, payload)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")

    exists = db.scalar(
        select(RegisteredModel.id).where(RegisteredModel.project_id == principal.project_id, RegisteredModel.name == name)
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail="model name already exists")

    model = RegisteredModel(
        project_id=principal.project_id,
        name=name,
        description=body.description,
        model_type=body.model_type,
        base_model=body.base_model,
        tags=body.tags,
    )
    db.add(model)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="model name already exists") from exc
    return _model_out(model)


MODEL_CARD_EXCERPT_CHARS = 280
MODELS_DEFAULT_LIMIT = 200
VERSIONS_DEFAULT_LIMIT = 50
VERSIONS_MAX_LIMIT = 200


def _excerpt(text: str | None) -> tuple[str | None, bool]:
    if text is None or len(text) <= MODEL_CARD_EXCERPT_CHARS:
        return text, False
    return text[:MODEL_CARD_EXCERPT_CHARS], True


@router.get("/models")
def list_models(
    db: DbSession,
    principal: ViewerAuth,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MODELS_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Models page (`limit`/`offset`, newest update first) with a per-model summary.

    Only version numbers, stages and the latest metrics are read (never full model cards); the
    latest card comes back as a short excerpt (`latest_model_card_excerpt`), the full card is in
    `GET /models/{id}` or `GET /models/{id}/versions/{version}`.
    """
    models = list(
        db.scalars(
            select(RegisteredModel)
            .where(RegisteredModel.project_id == principal.project_id)
            .order_by(RegisteredModel.updated_at.desc(), RegisteredModel.id.desc())
            .limit(limit + 1)
            .offset(offset)
        )
    )
    next_offset = offset + limit if len(models) > limit else None
    models = models[:limit]
    model_ids = [m.id for m in models]

    version_rows = (
        db.execute(
            select(ModelVersion.model_id, ModelVersion.version, ModelVersion.stage).where(
                ModelVersion.project_id == principal.project_id, ModelVersion.model_id.in_(model_ids)
            )
        ).all()
        if model_ids
        else []
    )
    versions_by_model: dict[str, list[tuple[int, str]]] = {}
    for model_id, version, stage in version_rows:
        versions_by_model.setdefault(model_id, []).append((version, stage))

    latest_by_model: dict[str, int] = {mid: max(v for v, _ in vs) for mid, vs in versions_by_model.items()}
    # `version IN (...)` can also match another model's number; the (model, version) key filters it.
    latest_rows = (
        db.execute(
            select(
                ModelVersion.model_id,
                ModelVersion.version,
                ModelVersion.metrics_snapshot,
                func.substr(ModelVersion.model_card, 1, MODEL_CARD_EXCERPT_CHARS + 1),
            ).where(
                ModelVersion.project_id == principal.project_id,
                ModelVersion.model_id.in_(list(latest_by_model)),
                ModelVersion.version.in_(sorted(set(latest_by_model.values()))),
            )
        ).all()
        if latest_by_model
        else []
    )
    latest_by_key = {(r[0], r[1]): (r[2], r[3]) for r in latest_rows}
    latest_info: dict[str, tuple[dict[str, Any], str | None]] = {}
    for mid, ver in latest_by_model.items():
        metrics, card = latest_by_key.get((mid, ver), ({}, None))
        latest_info[mid] = (dict(metrics or {}), card)

    aliases = (
        list(
            db.scalars(
                select(ModelAlias).where(
                    ModelAlias.project_id == principal.project_id, ModelAlias.model_id.in_(model_ids)
                )
            )
        )
        if model_ids
        else []
    )
    aliases_by_model: dict[str, dict[str, int]] = {}
    for al in aliases:
        aliases_by_model.setdefault(al.model_id, {})[al.alias] = al.version

    items: list[dict[str, Any]] = []
    for m in models:
        mvs = sorted(versions_by_model.get(m.id, []), reverse=True)
        stages: dict[str, list[int]] = {}
        for ver, stage in mvs:
            stages.setdefault(stage, []).append(ver)
        metrics, card = latest_info.get(m.id, ({}, None))
        card_excerpt, truncated = _excerpt(card)
        item = _model_out(m)
        item["latest_version"] = mvs[0][0] if mvs else None
        item["aliases"] = dict(sorted(aliases_by_model.get(m.id, {}).items()))
        item["stages"] = stages
        item["latest_metrics"] = metrics
        item["latest_model_card_excerpt"] = card_excerpt
        item["latest_model_card_truncated"] = truncated
        items.append(item)
    return {"items": items, "next_offset": next_offset}


@router.get("/models/{model_id}")
def get_model(
    model_id: str,
    db: DbSession,
    principal: ViewerAuth,
    versions_limit: Annotated[int, Query(ge=1, le=VERSIONS_MAX_LIMIT)] = VERSIONS_DEFAULT_LIMIT,
    versions_offset: Annotated[int, Query(ge=0)] = 0,
    model_card: Annotated[Literal["full", "excerpt"], Query()] = "full",
) -> dict[str, Any]:
    """Model detail with a page of versions (newest first, `versions_limit`/`versions_offset`).

    `model_card=excerpt` trims each version's card to a short excerpt; `GET
    /models/{id}/versions/{version}` always returns the full card.
    """
    model = _get_model_or_404(db, principal.project_id, model_id)
    total = int(
        db.scalar(
            select(func.count(ModelVersion.id)).where(
                ModelVersion.project_id == principal.project_id, ModelVersion.model_id == model.id
            )
        )
        or 0
    )
    versions = list(
        db.scalars(
            select(ModelVersion)
            .where(ModelVersion.project_id == principal.project_id, ModelVersion.model_id == model.id)
            .order_by(ModelVersion.version.desc())
            .limit(versions_limit)
            .offset(versions_offset)
        )
    )
    aliases = list(
        db.scalars(
            select(ModelAlias).where(ModelAlias.project_id == principal.project_id, ModelAlias.model_id == model.id)
        )
    )
    out = _model_out(model)
    version_items = [_version_out(v) for v in versions]
    if model_card == "excerpt":
        for item in version_items:
            item["model_card"], item["model_card_truncated"] = _excerpt(item["model_card"])
    out["versions"] = version_items
    out["versions_total"] = total
    next_versions = versions_offset + len(versions)
    out["versions_next_offset"] = next_versions if next_versions < total else None
    out["aliases"] = {a.alias: a.version for a in sorted(aliases, key=lambda x: x.alias)}
    return out


@router.get("/models/{model_id}/versions/{version}")
def get_model_version(model_id: str, version: int, db: DbSession, principal: ViewerAuth) -> dict[str, Any]:
    """One version with its full model card."""
    model = _get_model_or_404(db, principal.project_id, model_id)
    return _version_out(_get_model_version_or_404(db, principal.project_id, model.id, version))


@router.delete("/models/{model_id}")
def delete_model(
    model_id: str,
    db: DbSession,
    principal: AdminAuth,
    force: bool = False,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    if not force:
        has_protected_alias = db.scalar(
            select(ModelAlias.id).where(
                ModelAlias.project_id == principal.project_id,
                ModelAlias.model_id == model.id,
                ModelAlias.alias.in_(ADMIN_ONLY_ALIASES),
            )
        )
        has_prod_stage = db.scalar(
            select(ModelVersion.id).where(
                ModelVersion.project_id == principal.project_id,
                ModelVersion.model_id == model.id,
                ModelVersion.stage == "production",
            )
        )
        if has_protected_alias is not None or has_prod_stage is not None:
            raise HTTPException(
                status_code=409,
                detail="cannot delete model with production/champion alias or production stage; pass force=true",
            )
    db.delete(model)
    db.flush()
    return {"deleted": True, "id": model_id}


def _lock_model_row(db: Session, project_id: str, model_id: str) -> None:
    """Serialize concurrent stage promotions / version creations on Postgres."""
    if db.get_bind().dialect.name == "postgresql":
        db.scalars(
            select(RegisteredModel)
            .where(RegisteredModel.id == model_id, RegisteredModel.project_id == project_id)
            .with_for_update()
        ).first()


def _demote_existing_production(
    db: Session,
    *,
    project_id: str,
    model_id: str,
    promoted_version: int,
    exclude_version_id: str | None,
    principal: Principal,
) -> list[int]:
    """Demote any existing `production` version of `model_id` to `archived` in the same transaction."""
    stmt = select(ModelVersion).where(
        ModelVersion.project_id == project_id,
        ModelVersion.model_id == model_id,
        ModelVersion.stage == "production",
    )
    if exclude_version_id is not None:
        stmt = stmt.where(ModelVersion.id != exclude_version_id)
    prev_rows = list(db.scalars(stmt))
    demoted: list[int] = []
    now = utcnow()
    for prev in prev_rows:
        prev.stage = "archived"
        demoted.append(prev.version)
        db.add(
            ModelStageHistory(
                project_id=project_id,
                model_id=model_id,
                version=prev.version,
                from_stage="production",
                to_stage="archived",
                actor_kind=principal.actor_kind,
                actor_id=principal.actor_id,
                comment=f"Demoted automatically by promotion of v{promoted_version}",
                ts=now,
            )
        )
    if prev_rows:
        db.flush()
    return demoted


def _validate_and_normalize_alias(raw_alias: str, principal: Principal) -> str:
    alias = raw_alias.strip().lstrip("@")
    if not alias:
        raise HTTPException(status_code=422, detail="alias must not be blank")
    # Check admin-only aliases case-insensitively BEFORE regex validation so a member trying
    # e.g. "CHAMPION" or "Production" gets 403 rather than 422.
    if alias.lower() in ADMIN_ONLY_ALIASES:
        ensure_role(principal, Role.ADMIN)
    if not ALIAS_PATTERN.match(alias):
        raise HTTPException(status_code=422, detail="alias must match ^[a-z][a-z0-9_-]{0,49}$")
    return alias


def _get_webhook_or_404(db: Session, project_id: str, model_id: str, webhook_id: str) -> ModelWebhook:
    row = db.scalars(
        select(ModelWebhook).where(
            ModelWebhook.id == webhook_id,
            ModelWebhook.model_id == model_id,
            ModelWebhook.project_id == project_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="webhook not found")
    return row


@router.post("/models/{model_id}/versions", status_code=201)
def create_model_version(
    model_id: str,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    body: ModelVersionCreate = _parse_body(ModelVersionCreate, payload)

    if body.stage in ADMIN_ONLY_STAGES:
        ensure_role(principal, Role.ADMIN)

    if body.run_id is not None:
        _get_run_or_404(db, principal.project_id, body.run_id)
    if body.eval_experiment_id is not None:
        _get_experiment_or_404(db, principal.project_id, body.eval_experiment_id)
    if body.safety_scan_id is not None:
        _get_safety_scan_or_404(db, principal.project_id, body.safety_scan_id)
    resolved_artifact_id: str | None = None
    if body.artifact_id is not None:
        resolved_artifact_id = _resolve_or_bridge_artifact(
            db, principal.project_id, body.artifact_id, body.run_id
        ).id

    _lock_model_row(db, principal.project_id, model.id)

    mv: ModelVersion | None = None
    demoted: list[int] = []
    for attempt in range(MAX_CREATE_ATTEMPTS):
        try:
            with db.begin_nested():
                current_ver = db.scalar(
                    select(func.max(ModelVersion.version)).where(
                        ModelVersion.project_id == principal.project_id, ModelVersion.model_id == model.id
                    )
                )
                next_ver = (current_ver or 0) + 1
                if body.stage == "production":
                    demoted = _demote_existing_production(
                        db,
                        project_id=principal.project_id,
                        model_id=model.id,
                        promoted_version=next_ver,
                        exclude_version_id=None,
                        principal=principal,
                    )
                mv = ModelVersion(
                    project_id=principal.project_id,
                    model_id=model.id,
                    version=next_ver,
                    artifact_id=resolved_artifact_id,
                    run_id=body.run_id,
                    eval_experiment_id=body.eval_experiment_id,
                    safety_scan_id=body.safety_scan_id,
                    stage=body.stage,
                    lora_config=body.lora_config,
                    signature=body.signature,
                    metrics_snapshot=body.metrics_snapshot,
                    model_card=body.model_card,
                    created_by=principal.actor_id,
                )
                db.add(mv)
                db.flush()
            break
        except IntegrityError:
            if attempt == MAX_CREATE_ATTEMPTS - 1:
                raise HTTPException(status_code=409, detail="concurrent version creation, retry") from None

    assert mv is not None
    if body.stage != "none":
        db.add(
            ModelStageHistory(
                project_id=principal.project_id,
                model_id=model.id,
                version=mv.version,
                from_stage="none",
                to_stage=body.stage,
                actor_kind=principal.actor_kind,
                actor_id=principal.actor_id,
                comment="Initial stage on version creation",
            )
        )
        if body.stage in ADMIN_ONLY_STAGES:
            audit.record(
                db,
                action="model.stage_change",
                project_id=principal.project_id,
                actor_kind=principal.actor_kind,
                actor_id=principal.actor_id,
                target=f"{model.id}:v{mv.version}",
                detail={
                    "model_id": model.id,
                    "version": mv.version,
                    "from_stage": "none",
                    "to_stage": body.stage,
                    "demoted_versions": demoted,
                },
            )

    if resolved_artifact_id is not None:
        _add_lineage_edge(db, principal.project_id, "artifact", resolved_artifact_id, "model_version", mv.id, "packaged_as")
    if body.run_id is not None:
        _add_lineage_edge(db, principal.project_id, "run", body.run_id, "model_version", mv.id, "registered_as")
    if body.eval_experiment_id is not None:
        _add_lineage_edge(
            db, principal.project_id, "model_version", mv.id, "experiment", body.eval_experiment_id, "evaluated_by"
        )
    if body.safety_scan_id is not None:
        _add_lineage_edge(
            db, principal.project_id, "model_version", mv.id, "safety_scan", body.safety_scan_id, "audited_by"
        )

    dispatch_model_event(
        db,
        project_id=principal.project_id,
        model_id=model.id,
        event="version.created",
        payload={
            "event": "version.created",
            "model_id": model.id,
            "model_name": model.name,
            "version": mv.version,
            "stage": mv.stage,
            "run_id": mv.run_id,
            "artifact_id": mv.artifact_id,
            "created_by": mv.created_by,
            "ts": _iso_utc(mv.created_at),
        },
    )

    model.updated_at = utcnow()
    db.flush()
    return _version_out(mv)


def _change_model_version_stage(
    model_id: str,
    version: int,
    db: Session,
    principal: Principal,
    payload: dict[str, Any],
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    body: ModelStagePatch = _parse_body(ModelStagePatch, payload)
    if body.stage in ADMIN_ONLY_STAGES:
        ensure_role(principal, Role.ADMIN)

    _lock_model_row(db, principal.project_id, model.id)
    mv = _get_model_version_or_404(db, principal.project_id, model.id, version)
    if mv.stage == "production":
        ensure_role(principal, Role.ADMIN)

    old_stage = mv.stage
    demoted: list[int] = []
    if body.stage == "production" and old_stage != "production":
        demoted = _demote_existing_production(
            db,
            project_id=principal.project_id,
            model_id=model.id,
            promoted_version=mv.version,
            exclude_version_id=mv.id,
            principal=principal,
        )

    now = utcnow()
    mv.stage = body.stage
    db.add(
        ModelStageHistory(
            project_id=principal.project_id,
            model_id=model.id,
            version=mv.version,
            from_stage=old_stage,
            to_stage=body.stage,
            actor_kind=principal.actor_kind,
            actor_id=principal.actor_id,
            comment=body.comment,
            ts=now,
        )
    )
    audit.record(
        db,
        action="model.stage_change",
        project_id=principal.project_id,
        actor_kind=principal.actor_kind,
        actor_id=principal.actor_id,
        target=f"{model.id}:v{mv.version}",
        detail={
            "model_id": model.id,
            "version": mv.version,
            "from_stage": old_stage,
            "to_stage": body.stage,
            "demoted_versions": demoted,
        },
    )
    dispatch_model_event(
        db,
        project_id=principal.project_id,
        model_id=model.id,
        event="stage.changed",
        payload={
            "event": "stage.changed",
            "model_id": model.id,
            "model_name": model.name,
            "version": mv.version,
            "from_stage": old_stage,
            "to_stage": body.stage,
            "demoted_versions": demoted,
            "comment": body.comment,
            "actor_id": principal.actor_id,
            "ts": _iso_utc(now),
        },
    )
    model.updated_at = now
    db.flush()
    return _version_out(mv)


@router.patch("/models/{model_id}/versions/{version}/stage")
def update_model_version_stage(
    model_id: str,
    version: int,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return _change_model_version_stage(model_id, version, db, principal, payload)


@router.post("/models/{model_id}/versions/{version}/stage")
@canonical_router.post("/models/{model_id}/versions/{version}/stage")
def post_model_version_stage(
    model_id: str,
    version: int,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return _change_model_version_stage(model_id, version, db, principal, payload)


@router.get("/models/{model_id}/history")
@canonical_router.get("/models/{model_id}/history")
def get_model_stage_history(
    model_id: str,
    db: DbSession,
    principal: ViewerAuth,
    version: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    stmt = select(ModelStageHistory).where(
        ModelStageHistory.project_id == principal.project_id,
        ModelStageHistory.model_id == model.id,
    )
    if version is not None:
        stmt = stmt.where(ModelStageHistory.version == version)
    rows = list(db.scalars(stmt.order_by(ModelStageHistory.ts.desc(), ModelStageHistory.id.desc()).limit(limit)))
    return {"model_id": model.id, "items": [_history_out(h) for h in rows]}


@router.get("/models/{model_id}/versions/{version}/files")
@canonical_router.get("/models/{model_id}/versions/{version}/files")
def get_model_version_files(
    model_id: str,
    version: int,
    request: Request,
    response: Response,
    db: DbSession,
    principal: ViewerAuth,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    mv = _get_model_version_or_404(db, principal.project_id, model.id, version)

    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"

    files: list[dict[str, Any]] = []
    if mv.artifact_id is not None:
        ma = db.scalars(
            select(Artifact).where(
                Artifact.id == mv.artifact_id,
                Artifact.project_id == principal.project_id,
            )
        ).first()
        if ma is not None:
            if ma.artifact_id is not None:
                blob = db.scalars(
                    select(BlobArtifact).where(
                        BlobArtifact.id == ma.artifact_id,
                        BlobArtifact.project_id == principal.project_id,
                    )
                ).first()
                if blob is not None and blob.status == "ready":
                    store = get_store(request.app)
                    ttl = request.app.state.settings.artifact_url_ttl_seconds
                    presigned = store.presign_get(
                        blob.project_id, blob.sha256, ttl, filename=blob.name, media_type=blob.media_type
                    )
                    url = presigned.url if presigned is not None else f"/api/v1/artifacts/{blob.id}/download"
                    files.append(
                        {
                            "artifact_id": blob.id,
                            "model_artifact_id": ma.id,
                            "path": blob.name,
                            "size": blob.size,
                            "sha256": blob.sha256,
                            "media_type": blob.media_type,
                            "url": url,
                        }
                    )
            elif ma.external_uri is not None:
                files.append(
                    {
                        "artifact_id": None,
                        "model_artifact_id": ma.id,
                        "path": ma.name,
                        "size": ma.size_bytes,
                        "sha256": ma.sha256,
                        "media_type": ma.content_type,
                        "external_uri": ma.external_uri,
                        "url": None,
                    }
                )
            elif (ma.metadata_ or {}).get("legacy_storage_uri"):
                raise HTTPException(
                    status_code=410,
                    detail="legacy artifact blob is no longer servable; re-upload a new version",
                )

    return {"model_id": model.id, "version": mv.version, "files": files}


def _upsert_model_alias(
    db: Session,
    *,
    principal: Principal,
    model_id: str,
    raw_alias: str,
    version: int,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    alias = _validate_and_normalize_alias(raw_alias, principal)
    _get_model_version_or_404(db, principal.project_id, model.id, version)

    existing = db.scalars(
        select(ModelAlias).where(
            ModelAlias.project_id == principal.project_id,
            ModelAlias.model_id == model.id,
            ModelAlias.alias == alias,
        )
    ).first()
    previous_version = existing.version if existing is not None else None
    now = utcnow()
    if existing is None:
        db.add(ModelAlias(project_id=principal.project_id, model_id=model.id, alias=alias, version=version))
    else:
        existing.version = version
        existing.updated_at = now
    audit.record(
        db,
        action="model.alias_set",
        project_id=principal.project_id,
        actor_kind=principal.actor_kind,
        actor_id=principal.actor_id,
        target=f"{model.id}:@{alias}",
        detail={
            "model_id": model.id,
            "alias": alias,
            "previous_version": previous_version,
            "version": version,
        },
    )
    dispatch_model_event(
        db,
        project_id=principal.project_id,
        model_id=model.id,
        event="alias.moved",
        payload={
            "event": "alias.moved",
            "model_id": model.id,
            "model_name": model.name,
            "alias": alias,
            "previous_version": previous_version,
            "version": version,
            "actor_id": principal.actor_id,
            "ts": _iso_utc(now),
        },
    )
    model.updated_at = now
    db.flush()
    return {"model_id": model.id, "alias": alias, "version": version}


@router.post("/models/{model_id}/aliases")
def set_model_alias(
    model_id: str,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: ModelAliasIn = _parse_body(ModelAliasIn, payload)
    return _upsert_model_alias(db, principal=principal, model_id=model_id, raw_alias=body.alias, version=body.version)


@router.put("/models/{model_id}/aliases/{alias}")
@canonical_router.put("/models/{model_id}/aliases/{alias}")
def put_model_alias(
    model_id: str,
    alias: str,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    body: ModelAliasVersionIn = _parse_body(ModelAliasVersionIn, payload)
    return _upsert_model_alias(db, principal=principal, model_id=model_id, raw_alias=alias, version=body.version)


@router.delete("/models/{model_id}/aliases/{alias}")
@canonical_router.delete("/models/{model_id}/aliases/{alias}")
def delete_model_alias(
    model_id: str,
    alias: str,
    db: DbSession,
    principal: MemberAuth,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    cleaned = _validate_and_normalize_alias(alias, principal)
    existing = db.scalars(
        select(ModelAlias).where(
            ModelAlias.project_id == principal.project_id,
            ModelAlias.model_id == model.id,
            ModelAlias.alias == cleaned,
        )
    ).first()
    if existing is None:
        raise HTTPException(status_code=404, detail="alias not found")
    previous_version = existing.version
    now = utcnow()
    db.delete(existing)
    audit.record(
        db,
        action="model.alias_delete",
        project_id=principal.project_id,
        actor_kind=principal.actor_kind,
        actor_id=principal.actor_id,
        target=f"{model.id}:@{cleaned}",
        detail={"model_id": model.id, "alias": cleaned, "previous_version": previous_version},
    )
    dispatch_model_event(
        db,
        project_id=principal.project_id,
        model_id=model.id,
        event="alias.moved",
        payload={
            "event": "alias.moved",
            "model_id": model.id,
            "model_name": model.name,
            "alias": cleaned,
            "previous_version": previous_version,
            "version": None,
            "actor_id": principal.actor_id,
            "ts": _iso_utc(now),
        },
    )
    model.updated_at = now
    db.flush()
    return {"deleted": True, "model_id": model.id, "alias": cleaned}


# ---------------------------------------------------------------------------
# CI/CD Webhooks (ADMIN)
# ---------------------------------------------------------------------------


@router.post("/models/{model_id}/webhooks", status_code=201)
@canonical_router.post("/models/{model_id}/webhooks", status_code=201)
def create_model_webhook(
    model_id: str,
    request: Request,
    db: DbSession,
    principal: AdminAuth,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    body: WebhookCreate = _parse_body(WebhookCreate, payload)
    try:
        url = validate_webhook_url(body.url)
        events = validate_webhook_events(body.events)
    except WebhookValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        master_key = resolve_secret_key(request.app.state.settings.secret_key)
    except SecretKeyMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    plaintext_secret = body.secret if body.secret is not None else generate_webhook_secret()
    encrypted = encrypt_secret(plaintext_secret, master_key)

    wh = ModelWebhook(
        project_id=principal.project_id,
        model_id=model.id,
        url=url,
        events=events,
        secret_encrypted=encrypted,
        active=True,
        created_by=principal.actor_id,
    )
    db.add(wh)
    db.flush()
    audit.record(
        db,
        action="model.webhook_create",
        project_id=principal.project_id,
        actor_kind=principal.actor_kind,
        actor_id=principal.actor_id,
        target=wh.id,
        detail={"model_id": model.id, "events": events},
    )
    return _webhook_out(wh, secret=plaintext_secret)


@router.get("/models/{model_id}/webhooks")
@canonical_router.get("/models/{model_id}/webhooks")
def list_model_webhooks(
    model_id: str,
    db: DbSession,
    principal: AdminAuth,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    rows = list(
        db.scalars(
            select(ModelWebhook)
            .where(ModelWebhook.project_id == principal.project_id, ModelWebhook.model_id == model.id)
            .order_by(ModelWebhook.created_at.desc(), ModelWebhook.id.desc())
        )
    )
    return {"model_id": model.id, "items": [_webhook_out(wh) for wh in rows]}


@router.delete("/models/{model_id}/webhooks/{webhook_id}")
@canonical_router.delete("/models/{model_id}/webhooks/{webhook_id}")
def delete_model_webhook(
    model_id: str,
    webhook_id: str,
    db: DbSession,
    principal: AdminAuth,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    wh = _get_webhook_or_404(db, principal.project_id, model.id, webhook_id)
    db.delete(wh)
    audit.record(
        db,
        action="model.webhook_delete",
        project_id=principal.project_id,
        actor_kind=principal.actor_kind,
        actor_id=principal.actor_id,
        target=webhook_id,
        detail={"model_id": model.id},
    )
    db.flush()
    return {"deleted": True, "id": webhook_id, "model_id": model.id}


@router.get("/models/{model_id}/webhooks/{webhook_id}/deliveries")
@canonical_router.get("/models/{model_id}/webhooks/{webhook_id}/deliveries")
def list_webhook_deliveries(
    model_id: str,
    webhook_id: str,
    db: DbSession,
    principal: ViewerAuth,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    wh = _get_webhook_or_404(db, principal.project_id, model.id, webhook_id)
    rows = list(
        db.scalars(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.project_id == principal.project_id,
                WebhookDelivery.webhook_id == wh.id,
            )
            .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
            .limit(limit)
        )
    )
    return {"webhook_id": wh.id, "model_id": model.id, "items": [_delivery_out(d) for d in rows]}


@router.get("/models/{model_id}/lineage")
def get_model_lineage(
    model_id: str,
    db: DbSession,
    principal: ViewerAuth,
    direction: Direction = "both",
    depth: Annotated[int, Query(ge=0, le=MAX_LINEAGE_DEPTH)] = DEFAULT_LINEAGE_DEPTH,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    mvs = list(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.project_id == principal.project_id,
                ModelVersion.model_id == model.id,
            )
        )
    )
    seed_nodes = [("model_version", mv.id) for mv in mvs]
    if not seed_nodes:
        return {"model_id": model.id, "nodes": [], "edges": [], "truncated": False}
    nodes, edges, truncated = query_bounded_lineage(
        db,
        principal.project_id,
        seed_nodes,
        direction=direction,
        depth=depth,
    )
    return {"model_id": model.id, "nodes": nodes, "edges": edges, "truncated": truncated}


@router.post("/models/{model_id}/versions/{version}/evaluate", status_code=202)
def evaluate_model_version(
    model_id: str,
    version: int,
    db: DbSession,
    principal: MemberAuth,
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    model = _get_model_or_404(db, principal.project_id, model_id)
    mv = _get_model_version_or_404(db, principal.project_id, model.id, version)
    body: ModelEvaluateIn = _parse_body(ModelEvaluateIn, payload or {})

    if body.dataset_id is not None:
        _get_dataset_or_404(db, principal.project_id, body.dataset_id)

    eval_name = (body.name or f"{model.name}-v{mv.version}-eval")[:200]
    benchmark_run = BenchmarkRun(
        project_id=principal.project_id,
        name=eval_name,
        suite="lm_eval",
        tasks=["gsm8k"],
        runner_backend="local_sim",
        runner_config={"metrics": body.metrics} if body.metrics else {},
        eval_mode="generate_until",
        num_fewshot=0,
        target_model=(f"{model.name}:v{mv.version}")[:200],
        model_version_id=mv.id,
        dataset_id=body.dataset_id,
        status="queued",
        simulated=True,
        summary_metrics={},
    )
    db.add(benchmark_run)
    db.flush()

    job = enqueue(
        db,
        principal.project_id,
        "mlops.evaluate",
        {
            "benchmark_run_id": benchmark_run.id,
            "model_id": model.id,
            "model_name": model.name,
            "version": mv.version,
            "model_version_id": mv.id,
            "dataset_id": body.dataset_id,
            "name": eval_name,
            "metrics": body.metrics,
        },
    )
    benchmark_run.job_id = job.id
    db.flush()
    return {
        "status": "queued",
        "job_id": job.id,
        "benchmark_run_id": benchmark_run.id,
        "model_id": model.id,
        "version": mv.version,
    }
