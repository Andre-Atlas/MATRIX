"""Prompt registry API: immutable versions plus movable labels (spec section 4, Prompts)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server import prompts as helpers
from matrix_server.auth import current_project_id
from matrix_server.domain.spans import UtcDatetime
from matrix_server.storage.db import as_utc, get_db, utcnow
from matrix_server.storage.models import PromptLabel, PromptVersion

DbSession = Annotated[Session, Depends(get_db)]
ProjectId = Annotated[str, Depends(current_project_id)]

MAX_CREATE_ATTEMPTS = 3


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/v1", tags=["prompts"])
reads = APIRouter(dependencies=[Depends(_no_store)])


class PromptVersionOut(BaseModel):
    name: str
    version: int
    type: Literal["text", "chat"]
    template: Any
    variables: list[str]
    config: dict[str, Any]
    response_schema: dict[str, Any] | bool | None = None
    labels: list[str]
    commit_message: str | None = None
    created_at: UtcDatetime


class PromptSummary(BaseModel):
    name: str
    latest_version: int
    labels: dict[str, int]
    updated_at: UtcDatetime


class PromptList(BaseModel):
    items: list[PromptSummary]


class VersionList(BaseModel):
    items: list[PromptVersionOut]


class PromptCreate(BaseModel):
    name: str
    type: Literal["text", "chat"]
    template: Any
    config: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] | bool | None = None
    labels: list[str] = Field(default_factory=list, max_length=20)
    commit_message: str | None = Field(default=None, max_length=10_000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return helpers.validate_name(value)

    @field_validator("labels")
    @classmethod
    def _labels(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(helpers.validate_label(label) for label in value))

    @field_validator("response_schema")
    @classmethod
    def _schema(cls, value: Any) -> Any:
        helpers.validate_response_schema(value)
        return value

    @model_validator(mode="after")
    def _template(self) -> PromptCreate:
        helpers.validate_template(self.type, self.template)
        return self


class LabelMove(BaseModel):
    label: str
    version: int = Field(ge=1)

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return helpers.validate_label(value)


def _labels_by_version(db: Session, project_id: str, name: str) -> dict[int, list[str]]:
    rows = db.execute(
        select(PromptLabel.label, PromptLabel.version).where(
            PromptLabel.project_id == project_id, PromptLabel.name == name
        )
    ).all()
    out: dict[int, list[str]] = {}
    for label, version in rows:
        out.setdefault(version, []).append(label)
    return {v: sorted(ls) for v, ls in out.items()}


def _to_out(row: PromptVersion, labels: list[str]) -> PromptVersionOut:
    return PromptVersionOut(
        name=row.name,
        version=row.version,
        type=row.type,  # type: ignore[arg-type]
        template=row.template,
        variables=list(row.variables or []),
        config=dict(row.config or {}),
        response_schema=row.response_schema,
        labels=labels,
        commit_message=row.commit_message,
        created_at=as_utc(row.created_at),  # type: ignore[arg-type]
    )


def _get_version(db: Session, project_id: str, name: str, version: int) -> PromptVersion | None:
    return db.scalars(
        select(PromptVersion).where(
            PromptVersion.project_id == project_id, PromptVersion.name == name, PromptVersion.version == version
        )
    ).first()


def _not_found(what: str = "prompt") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


@reads.get("/prompts", response_model=PromptList)
def list_prompts(db: DbSession, project_id: ProjectId) -> PromptList:
    versions = db.execute(
        select(PromptVersion.name, func.max(PromptVersion.version), func.max(PromptVersion.created_at))
        .where(PromptVersion.project_id == project_id)
        .group_by(PromptVersion.name)
        .order_by(PromptVersion.name)
    ).all()
    labels: dict[str, dict[str, int]] = {}
    label_updated: dict[str, datetime] = {}
    for row in db.scalars(select(PromptLabel).where(PromptLabel.project_id == project_id)):
        labels.setdefault(row.name, {})[row.label] = row.version
        stamp = as_utc(row.updated_at)
        if stamp is not None and (row.name not in label_updated or stamp > label_updated[row.name]):
            label_updated[row.name] = stamp
    items = []
    for name, latest, created in versions:
        updated = max(as_utc(created), label_updated.get(name, as_utc(created)))  # type: ignore[type-var]
        items.append(
            PromptSummary(name=name, latest_version=latest, labels=dict(sorted(labels.get(name, {}).items())),
                          updated_at=updated)
        )
    return PromptList(items=items)


@router.post("/prompts", response_model=PromptVersionOut, status_code=201)
def create_prompt(body: PromptCreate, db: DbSession, project_id: ProjectId) -> PromptVersionOut:
    for attempt in range(MAX_CREATE_ATTEMPTS):
        try:
            with db.begin_nested():  # savepoint: a unique violation only discards this attempt
                current = db.scalar(
                    select(func.max(PromptVersion.version)).where(
                        PromptVersion.project_id == project_id, PromptVersion.name == body.name
                    )
                )
                row = PromptVersion(
                    project_id=project_id,
                    name=body.name,
                    version=(current or 0) + 1,
                    type=body.type,
                    template=body.template,
                    variables=helpers.extract_variables(body.template),
                    config=body.config,
                    response_schema=body.response_schema,
                    commit_message=body.commit_message,
                )
                db.add(row)
                db.flush()
                for label in body.labels:
                    _set_label(db, project_id, body.name, label, row.version)
                db.flush()
            break
        except IntegrityError:
            if attempt == MAX_CREATE_ATTEMPTS - 1:
                raise HTTPException(status_code=409, detail="concurrent prompt creation, retry") from None
    return _to_out(row, sorted(_labels_by_version(db, project_id, body.name).get(row.version, [])))


def _set_label(db: Session, project_id: str, name: str, label: str, version: int) -> None:
    existing = db.scalars(
        select(PromptLabel).where(
            PromptLabel.project_id == project_id, PromptLabel.name == name, PromptLabel.label == label
        )
    ).first()
    if existing is None:
        db.add(PromptLabel(project_id=project_id, name=name, label=label, version=version))
    else:
        existing.version = version
        existing.updated_at = utcnow()


@reads.get("/prompts/{name:path}/versions", response_model=VersionList)
def list_versions(name: str, db: DbSession, project_id: ProjectId) -> VersionList:
    rows = db.scalars(
        select(PromptVersion)
        .where(PromptVersion.project_id == project_id, PromptVersion.name == name)
        .order_by(PromptVersion.version.desc())
    ).all()
    if not rows:
        raise _not_found()
    by_version = _labels_by_version(db, project_id, name)
    return VersionList(items=[_to_out(r, by_version.get(r.version, [])) for r in rows])


@router.post("/prompts/{name:path}/labels", response_model=PromptVersionOut)
def move_label(name: str, body: LabelMove, db: DbSession, project_id: ProjectId) -> PromptVersionOut:
    row = _get_version(db, project_id, name, body.version)
    if row is None:
        raise _not_found("prompt version")
    _set_label(db, project_id, name, body.label, body.version)
    db.flush()
    return _to_out(row, _labels_by_version(db, project_id, name).get(row.version, []))


@reads.get("/prompts/{name:path}", response_model=PromptVersionOut)
def get_prompt(
    name: str,
    db: DbSession,
    project_id: ProjectId,
    label: Annotated[str | None, Query()] = None,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> PromptVersionOut:
    if label is not None and version is not None:
        raise HTTPException(status_code=422, detail="pass either label or version, not both")
    labels = _labels_by_version(db, project_id, name)
    if version is not None:
        target: int | None = version
    elif label is not None:
        target = next((v for v, ls in labels.items() if label in ls), None)
    else:
        target = next((v for v, ls in labels.items() if "production" in ls), None)
        if target is None:
            target = db.scalar(
                select(func.max(PromptVersion.version)).where(
                    PromptVersion.project_id == project_id, PromptVersion.name == name
                )
            )
    row = _get_version(db, project_id, name, target) if target is not None else None
    if row is None:
        raise _not_found()
    return _to_out(row, labels.get(row.version, []))


router.include_router(reads)
