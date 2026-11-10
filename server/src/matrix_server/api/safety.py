"""API endpoints for OWASP Top 10 for LLMs Defensive Security & Compliance (MATRIX Track C)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from matrix_server.auth import AuthContext, require_role
from matrix_server.safety import (
    PROBES,
    list_categories,
    list_probes,
    run_safety_scan,
)
from matrix_server.storage.db import as_utc, get_db
from matrix_server.storage.models import SafetyScan

router = APIRouter(prefix="/api/v1/safety", tags=["safety"])

DbSession = Annotated[Session, Depends(get_db)]
ViewerAuth = Annotated[AuthContext, Depends(require_role("viewer"))]
MemberAuth = Annotated[AuthContext, Depends(require_role("member"))]


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    utc_dt = as_utc(dt)
    return utc_dt.isoformat().replace("+00:00", "Z") if utc_dt else None


class CreateSafetyScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_prompt: str | None = Field(default=None, max_length=10_000)
    target_model: str | None = Field(default=None, max_length=200)
    categories: list[str] | None = Field(default=None)
    simulated_outputs: dict[str, str] | None = Field(default=None)


@router.get("/categories")
def get_safety_categories(ctx: ViewerAuth) -> dict[str, Any]:
    """List supported OWASP Top 10 for LLMs compliance categories and probe statistics."""
    categories_list = []
    for cat in list_categories():
        cat_probes = list_probes(cat.id)
        categories_list.append(
            {
                "id": cat.id,
                "name": cat.name,
                "description": cat.description,
                "weight": cat.weight,
                "default_severity": cat.default_severity,
                "probes_count": len(cat_probes),
            }
        )

    return {
        "categories": categories_list,
        "total_categories": len(categories_list),
        "total_probes": len(PROBES),
    }


@router.post("/scans", status_code=status.HTTP_201_CREATED)
def create_safety_scan(
    payload: CreateSafetyScanRequest,
    db: DbSession,
    ctx: MemberAuth,
) -> dict[str, Any]:
    """Execute a defensive security audit scan and record the compliance report."""
    report = run_safety_scan(
        category_ids=payload.categories,
        target_prompt=payload.target_prompt,
        target_model=payload.target_model,
        simulated_outputs=payload.simulated_outputs,
    )

    scan_record = SafetyScan(
        id=report.scan_id,
        project_id=ctx.project_id,
        target_prompt=report.target_prompt,
        target_model=report.target_model,
        status=report.status,
        global_score=report.global_score,
        total_probes=report.total_probes,
        passed_count=report.passed_count,
        failed_count=report.failed_count,
        categories_summary={k: v.model_dump() for k, v in report.categories.items()},
        findings=[f.model_dump() for f in report.findings],
    )
    db.add(scan_record)
    db.flush()

    return report.model_dump()


@router.get("/scans")
def list_safety_scans(
    db: DbSession,
    ctx: ViewerAuth,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """List audit scan history for the current project."""
    base_query = select(SafetyScan).where(SafetyScan.project_id == ctx.project_id)
    total = db.scalar(select(func.count()).select_from(base_query.subquery())) or 0

    scans = db.scalars(
        base_query.order_by(SafetyScan.created_at.desc()).limit(limit).offset(offset)
    ).all()

    items = []
    for s in scans:
        pass_rate = round(s.passed_count / s.total_probes, 4) if s.total_probes > 0 else 1.0
        items.append(
            {
                "id": s.id,
                "project_id": s.project_id,
                "target_prompt": s.target_prompt,
                "target_model": s.target_model,
                "status": s.status,
                "global_score": s.global_score,
                "total_probes": s.total_probes,
                "passed_count": s.passed_count,
                "failed_count": s.failed_count,
                "pass_rate": pass_rate,
                "created_at": _iso_utc(s.created_at),
            }
        )

    return {
        "scans": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/scans/{scan_id}")
def get_safety_scan(
    scan_id: str,
    db: DbSession,
    ctx: ViewerAuth,
) -> dict[str, Any]:
    """Retrieve full audit report and findings for a given scan ID."""
    scan = db.scalar(
        select(SafetyScan).where(
            SafetyScan.id == scan_id,
            SafetyScan.project_id == ctx.project_id,
        )
    )
    if scan is None:
        raise HTTPException(status_code=404, detail="safety scan not found")

    pass_rate = round(scan.passed_count / scan.total_probes, 4) if scan.total_probes > 0 else 1.0

    return {
        "scan_id": scan.id,
        "project_id": scan.project_id,
        "target_prompt": scan.target_prompt,
        "target_model": scan.target_model,
        "status": scan.status,
        "global_score": scan.global_score,
        "total_probes": scan.total_probes,
        "passed_count": scan.passed_count,
        "failed_count": scan.failed_count,
        "pass_rate": pass_rate,
        "categories": scan.categories_summary,
        "findings": scan.findings,
        "created_at": _iso_utc(scan.created_at),
    }
