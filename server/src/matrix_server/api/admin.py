"""Platform admin API: users, projects and API keys. Protected by MATRIX_ADMIN_TOKEN (bootstrap and
CLI only, ADR-006)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server import audit, users
from matrix_server.api.auth import parse_body, request_detail, user_out
from matrix_server.auth import ProjectExistsError, create_api_key, create_project, require_admin
from matrix_server.storage.db import as_utc, get_db
from matrix_server.storage.models import Project

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owner_email: str | None = Field(default=None, min_length=1, max_length=users.EMAIL_MAX_CHARS)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=users.EMAIL_MAX_CHARS)
    name: str = Field(default="", max_length=users.NAME_MAX_CHARS)
    password: str = Field(min_length=1, max_length=4096)


class ProjectRef(BaseModel):
    id: str
    name: str


class ProjectCreated(BaseModel):
    project: ProjectRef
    api_key: str


class ApiKeyCreated(BaseModel):
    api_key: str


class ProjectItem(BaseModel):
    id: str
    name: str
    created_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectItem]


@router.post("/users", status_code=201)
def post_user(request: Request, db: DbSession, payload: Annotated[Any, Body()] = None) -> dict[str, Any]:
    body: UserCreate = parse_body(UserCreate, payload)  # never echoes the password on 422
    try:
        user = users.create_user(db, body.email, body.name, body.password)
    except users.UserExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except users.UserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    audit.record(db, action="user.create", target=user.id, detail=request_detail(request))
    return user_out(user)


@router.post("/projects", response_model=ProjectCreated, status_code=201)
def post_project(request: Request, body: ProjectCreate, db: DbSession) -> ProjectCreated:
    """Creates the project, its initial key (all three scopes) and, with `owner_email`, the owner."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    owner_id = None
    if body.owner_email is not None:
        owner = users.get_user_by_email(db, body.owner_email)
        if owner is None:
            raise HTTPException(status_code=404, detail="owner user not found")
        owner_id = owner.id
    try:
        with db.begin_nested():
            project, api_key = create_project(db, name, owner_user_id=owner_id)
    except (ProjectExistsError, IntegrityError):  # IntegrityError: concurrent insert of the same name
        raise HTTPException(status_code=409, detail="project name already exists") from None
    assert api_key is not None
    audit.record(
        db, action="project.create", project_id=project.id, target=project.id,
        detail={"name": project.name, "owner": owner_id, **request_detail(request)},
    )
    return ProjectCreated(project=ProjectRef(id=project.id, name=project.name), api_key=api_key)


@router.post("/projects/{project_id}/keys", response_model=ApiKeyCreated, status_code=201)
def post_key(project_id: str, db: DbSession) -> ApiKeyCreated:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    api_key = create_api_key(db, project_id)
    audit.record(db, action="key.create", project_id=project_id, detail={"prefix": api_key.split("_")[1]})
    return ApiKeyCreated(api_key=api_key)


@router.get("/projects", response_model=ProjectList)
def list_projects(db: DbSession) -> ProjectList:
    rows = db.scalars(select(Project).order_by(Project.created_at, Project.name)).all()
    return ProjectList(
        items=[ProjectItem(id=p.id, name=p.name, created_at=as_utc(p.created_at)) for p in rows]
    )
