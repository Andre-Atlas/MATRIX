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
    get_category,
    get_probe,
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


def _compute_risk_level(global_score: float) -> str:
    if global_score >= 85.0:
        return "low"
    if global_score >= 70.0:
        return "medium"
    if global_score >= 50.0:
        return "high"
    return "critical"


def _enrich_finding(
    f: dict[str, Any],
    scan_id: str,
    created_at: str | None,
    target_prompt: str | None = None,
) -> dict[str, Any]:
    probe_id = f.get("probe_id", "")
    category_id = f.get("category_id", "")
    passed = bool(f.get("passed", True))
    probe = get_probe(probe_id)
    cat = get_category(category_id)

    probe_name = probe.name if probe else f.get("probe_name") or ("Custom Target Prompt Audit" if probe_id == "probe-adhoc-target" else probe_id)
    attack_prompt = probe.prompt if probe else f.get("attack_prompt") or (target_prompt or "")
    cat_name = cat.name if cat else f.get("category_name") or category_id

    return {
        **f,
        "id": f.get("id") or f"{scan_id}-{probe_id}",
        "scan_id": scan_id,
        "category_code": category_id,
        "category_name": cat_name,
        "probe_name": probe_name,
        "status": "passed" if passed else "failed",
        "attack_prompt": attack_prompt,
        "observed_output": f.get("observed_output", f.get("raw_output_snippet", "")),
        "defense_analysis": f.get("defense_analysis", f.get("details", "")),
        "recommended_guardrail": f.get(
            "recommended_guardrail",
            "Enforce strict input validation, context isolation, and output guardrails.",
        ),
        "created_at": f.get("created_at") or created_at,
    }


class CreateSafetyScanRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=200)
    target_prompt: str | None = Field(default=None, max_length=10_000)
    target_system_prompt: str | None = Field(default=None, max_length=10_000)
    target_model: str | None = Field(default=None, max_length=200)
    categories: list[str] | None = Field(default=None)
    max_probes_per_category: int | None = Field(default=None, ge=1, le=50)
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
                "code": cat.id,
                "name": cat.name,
                "description": cat.description,
                "short_description": cat.description,
                "weight": cat.weight,
                "default_severity": cat.default_severity,
                "severity_default": cat.default_severity,
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
    effective_prompt = payload.target_prompt if payload.target_prompt is not None else payload.target_system_prompt
    report = run_safety_scan(
        category_ids=payload.categories,
        target_prompt=effective_prompt,
        target_model=payload.target_model,
        simulated_outputs=payload.simulated_outputs,
    )

    enriched_findings = [
        _enrich_finding(f.model_dump(), report.scan_id, report.created_at, effective_prompt)
        for f in report.findings
    ]
    categories_dump = {k: v.model_dump() for k, v in report.categories.items()}

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
        categories_summary=categories_dump,
        findings=enriched_findings,
    )
    db.add(scan_record)
    db.flush()

    scan_name = payload.name or f"Audit {report.scan_id[:8]}"
    categories_tested = list(categories_dump.keys())

    return {
        **report.model_dump(),
        "id": report.scan_id,
        "scan_id": report.scan_id,
        "project_id": ctx.project_id,
        "name": scan_name,
        "safety_score": round(report.global_score, 1),
        "probes_passed": report.passed_count,
        "probes_failed": report.failed_count,
        "risk_level": _compute_risk_level(report.global_score),
        "categories_tested": categories_tested,
        "findings_count": report.failed_count,
        "categories": categories_dump,
        "findings": enriched_findings,
    }


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
        cats_summary = s.categories_summary or {}
        categories_tested = list(cats_summary.keys())
        created_iso = _iso_utc(s.created_at)
        items.append(
            {
                "id": s.id,
                "scan_id": s.id,
                "project_id": s.project_id,
                "name": f"Audit {s.id[:8]}",
                "target_prompt": s.target_prompt,
                "target_model": s.target_model,
                "status": s.status,
                "global_score": s.global_score,
                "safety_score": round(s.global_score, 1),
                "total_probes": s.total_probes,
                "passed_count": s.passed_count,
                "failed_count": s.failed_count,
                "probes_passed": s.passed_count,
                "probes_failed": s.failed_count,
                "pass_rate": pass_rate,
                "risk_level": _compute_risk_level(s.global_score),
                "categories_tested": categories_tested,
                "findings_count": s.failed_count,
                "categories": cats_summary,
                "created_at": created_iso,
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
    created_iso = _iso_utc(scan.created_at)
    cats_summary = scan.categories_summary or {}
    categories_tested = list(cats_summary.keys())
    enriched_findings = [
        _enrich_finding(f, scan.id, created_iso, scan.target_prompt)
        for f in (scan.findings or [])
    ]

    return {
        "id": scan.id,
        "scan_id": scan.id,
        "project_id": scan.project_id,
        "name": f"Audit {scan.id[:8]}",
        "target_prompt": scan.target_prompt,
        "target_model": scan.target_model,
        "status": scan.status,
        "global_score": scan.global_score,
        "safety_score": round(scan.global_score, 1),
        "total_probes": scan.total_probes,
        "passed_count": scan.passed_count,
        "failed_count": scan.failed_count,
        "probes_passed": scan.passed_count,
        "probes_failed": scan.failed_count,
        "pass_rate": pass_rate,
        "risk_level": _compute_risk_level(scan.global_score),
        "categories_tested": categories_tested,
        "findings_count": scan.failed_count,
        "categories": cats_summary,
        "findings": enriched_findings,
        "created_at": created_iso,
    }

