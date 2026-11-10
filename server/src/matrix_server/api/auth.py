"""Auth and Project Membership API endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from matrix_server.auth import (
    AuthContext,
    create_jwt,
    create_project,
    current_auth_context,
    hash_password,
    require_role,
    verify_password,
)
from matrix_server.storage.models import Project, ProjectMember, User

router = APIRouter(prefix="/api/v1", tags=["auth"])

RoleType = Literal["admin", "member", "viewer"]
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    project_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=_EMAIL_PATTERN)
    password: str


class MemberAddRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=_EMAIL_PATTERN)
    role: RoleType = "member"


class MemberUpdateRequest(BaseModel):
    role: RoleType


def _get_db(request: Request):
    session: Session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(_get_db)]


@router.post("/auth/register", status_code=201)
def register(req: RegisterRequest, request: Request, session: DbSession):
    email = req.email.lower().strip()
    existing = session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")

    user = User(
        email=email,
        name=req.name.strip(),
        password_hash=hash_password(req.password),
    )
    session.add(user)
    session.flush()

    # Create default project for the user
    proj_name = (req.project_name or f"{user.name}'s Project").strip()
    # Ensure uniqueness of project name
    base_name = proj_name
    counter = 1
    while session.scalar(select(Project.id).where(Project.name == proj_name)) is not None:
        counter += 1
        proj_name = f"{base_name} ({counter})"

    project, _ = create_project(session, proj_name)
    member = ProjectMember(project_id=project.id, user_id=user.id, role="admin")
    session.add(member)
    session.flush()

    secret = getattr(request.app.state.settings, "jwt_secret", "matrix-insecure-dev-secret-change-in-prod")
    token = create_jwt({"sub": user.id, "email": user.email}, secret)

    return {
        "token": token,
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "project": {"id": project.id, "name": project.name, "role": "admin"},
    }


@router.post("/auth/login")
def login(req: LoginRequest, request: Request, session: DbSession, response: Response):
    email = req.email.lower().strip()
    user = session.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")

    memberships = session.scalars(
        select(ProjectMember).where(ProjectMember.user_id == user.id)
    ).all()

    projects = [
        {"id": m.project.id, "name": m.project.name, "role": m.role}
        for m in memberships
    ]

    secret = getattr(request.app.state.settings, "jwt_secret", "matrix-insecure-dev-secret-change-in-prod")
    token = create_jwt({"sub": user.id, "email": user.email}, secret)

    # Set cookie for web client convenience
    response.set_cookie(
        key="matrix_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=72 * 3600,
    )

    return {
        "token": token,
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "projects": projects,
    }


@router.post("/auth/logout")
@router.post("/logout", include_in_schema=False)
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie("matrix_session", path="/")
    return {"status": "ok"}


@router.get("/auth/me")
def me(
    request: Request,
    session: DbSession,
    ctx: Annotated[AuthContext, Depends(current_auth_context)],
):
    if ctx.is_api_key or ctx.user_id is None:
        # API key context
        proj = session.get(Project, ctx.project_id)
        proj_info = {"id": ctx.project_id, "name": proj.name if proj else "Unknown", "role": "admin"}
        return {
            "is_api_key": True,
            "project": proj_info,
            "current_project": proj_info,
            "projects": [proj_info],
        }

    user = session.get(User, ctx.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    memberships = session.scalars(
        select(ProjectMember).where(ProjectMember.user_id == user.id)
    ).all()

    projects = [
        {"id": m.project.id, "name": m.project.name, "role": m.role}
        for m in memberships
    ]

    curr_proj = session.get(Project, ctx.project_id)

    return {
        "is_api_key": False,
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "current_project": {
            "id": ctx.project_id,
            "name": curr_proj.name if curr_proj else "Unknown",
            "role": ctx.role,
        },
        "projects": projects,
    }


# --- Project Members management ---


@router.get("/projects/{project_id}/members")
def list_project_members(
    project_id: str,
    session: DbSession,
    ctx: Annotated[AuthContext, Depends(require_role("viewer"))],
):
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="access to this project is denied")

    members = session.scalars(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    ).all()

    return {
        "members": [
            {
                "id": m.id,
                "user_id": m.user_id,
                "email": m.user.email,
                "name": m.user.name,
                "role": m.role,
                "created_at": m.created_at.isoformat(),
            }
            for m in members
        ]
    }


@router.post("/projects/{project_id}/members", status_code=201)
def add_project_member(
    project_id: str,
    req: MemberAddRequest,
    session: DbSession,
    ctx: Annotated[AuthContext, Depends(require_role("admin"))],
):
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="access to this project is denied")

    email = req.email.lower().strip()
    target_user = session.scalar(select(User).where(User.email == email))
    if target_user is None:
        raise HTTPException(status_code=404, detail=f"no user found with email '{email}'")

    existing = session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == target_user.id
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="user is already a member of this project")

    member = ProjectMember(project_id=project_id, user_id=target_user.id, role=req.role)
    session.add(member)
    session.flush()

    return {
        "id": member.id,
        "user_id": target_user.id,
        "email": target_user.email,
        "name": target_user.name,
        "role": member.role,
        "created_at": member.created_at.isoformat(),
    }


@router.patch("/projects/{project_id}/members/{user_id}")
def update_project_member(
    project_id: str,
    user_id: str,
    req: MemberUpdateRequest,
    session: DbSession,
    ctx: Annotated[AuthContext, Depends(require_role("admin"))],
):
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="access to this project is denied")

    member = session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")

    # If demoting from admin, make sure there's at least one other admin
    if member.role == "admin" and req.role != "admin":
        admin_count = session.scalar(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.project_id == project_id, ProjectMember.role == "admin"
            )
        )
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="cannot demote the sole project admin")

    member.role = req.role
    session.flush()

    return {
        "id": member.id,
        "user_id": member.user_id,
        "email": member.user.email,
        "name": member.user.name,
        "role": member.role,
    }


@router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
def remove_project_member(
    project_id: str,
    user_id: str,
    session: DbSession,
    ctx: Annotated[AuthContext, Depends(require_role("admin"))],
):
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="access to this project is denied")

    member = session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")

    if member.role == "admin":
        admin_count = session.scalar(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.project_id == project_id, ProjectMember.role == "admin"
            )
        )
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="cannot remove the sole project admin")

    session.delete(member)
    session.flush()
    return Response(status_code=204)
