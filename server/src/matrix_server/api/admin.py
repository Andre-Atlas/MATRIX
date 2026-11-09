"""Admin API: projects and API keys. Protected by MATRIX_ADMIN_TOKEN."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from matrix_server.auth import ProjectExistsError, create_api_key, create_project, require_admin
from matrix_server.storage.db import as_utc, get_db
from matrix_server.storage.models import Project

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


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


@router.post("/projects", response_model=ProjectCreated, status_code=201)
def post_project(body: ProjectCreate, db: DbSession) -> ProjectCreated:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    try:
        project, api_key = create_project(db, name)
    except (ProjectExistsError, IntegrityError):  # IntegrityError: concurrent insert of the same name
        raise HTTPException(status_code=409, detail="project name already exists") from None
    return ProjectCreated(project=ProjectRef(id=project.id, name=project.name), api_key=api_key)


@router.post("/projects/{project_id}/keys", response_model=ApiKeyCreated, status_code=201)
def post_key(project_id: str, db: DbSession) -> ApiKeyCreated:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return ApiKeyCreated(api_key=create_api_key(db, project_id))


@router.get("/projects", response_model=ProjectList)
def list_projects(db: DbSession) -> ProjectList:
    rows = db.scalars(select(Project).order_by(Project.created_at, Project.name)).all()
    return ProjectList(
        items=[ProjectItem(id=p.id, name=p.name, created_at=as_utc(p.created_at)) for p in rows]
    )
