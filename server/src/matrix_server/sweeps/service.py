"""Sweep domain service over canonical `runs` and `run_metrics` (contract G2)."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from matrix_server.runs import service as runs_service
from matrix_server.runs.models import Run
from matrix_server.runs.store import MAX_MAX_POINTS, MetricStore
from matrix_server.storage.db import as_utc, utcnow
from matrix_server.sweeps.engine import (
    SweepExhaustedError,
    SweepValidationError,
    assign_bracket,
    compute_importance,
    evaluate_pruning,
    suggest_next,
    validate_sweep_config,
)
from matrix_server.sweeps.models import Sweep, SweepRun

MAX_NEXT_RETRIES = 25
PAUSED_RETRY_AFTER = 10


class SweepConflictError(Exception):
    """Raised on state conflicts (duplicate name in compat, invalid status transition)."""


@dataclass(frozen=True, slots=True)
class NextTrialResult:
    outcome: str  # "ok" | "paused" | "exhausted"
    run: Run | None = None
    sweep_run: SweepRun | None = None
    params: dict[str, Any] | None = None
    retry_after: int | None = None


@dataclass(frozen=True, slots=True)
class ReportResult:
    action: str  # "continue" | "stop"
    reason: str | None = None


def _iso_utc(value: Any) -> str | None:
    dt = as_utc(value)
    return None if dt is None else dt.isoformat().replace("+00:00", "Z")


def _is_better(new_val: float, current_best: float | None, goal: str) -> bool:
    if not math.isfinite(new_val):
        return False
    if current_best is None or not math.isfinite(current_best):
        return True
    return new_val < current_best if goal == "minimize" else new_val > current_best


def render_sweep(sweep: Sweep, run_count: int) -> dict[str, Any]:
    return {
        "id": sweep.id,
        "name": sweep.name,
        "status": sweep.status,
        "config": dict(sweep.config or {}),
        "created_at": _iso_utc(sweep.created_at),
        "created_by": sweep.created_by,
        "run_count": run_count,
        "best_run_id": sweep.best_run_id,
        "best_value": sweep.best_value,
    }


def create_sweep(
    db: Session,
    project_id: str,
    *,
    name: str,
    config: Mapping[str, Any],
    created_by: str | None = None,
    description: str | None = None,
    unique_name: bool = False,
    allow_median: bool = False,
    allow_float_grid: bool = False,
) -> Sweep:
    clean_name = name.strip()
    if not clean_name:
        raise SweepValidationError({"name": "name must not be blank"})
    if len(clean_name) > 200:
        raise SweepValidationError({"name": "name must be at most 200 characters"})

    if unique_name:
        exists = db.scalar(select(Sweep.id).where(Sweep.project_id == project_id, Sweep.name == clean_name))
        if exists is not None:
            raise SweepConflictError("sweep name already exists")

    normalized = validate_sweep_config(config, allow_median=allow_median, allow_float_grid=allow_float_grid)
    sweep = Sweep(
        project_id=project_id,
        name=clean_name,
        description=description,
        status="running",
        config=normalized,
        metric_key=normalized["metric"]["key"],
        metric_goal=normalized["metric"]["goal"],
        seed=normalized.get("seed"),
        next_index=0,
        created_by=created_by,
    )
    db.add(sweep)
    db.flush()
    return sweep


def get_sweep(db: Session, project_id: str, sweep_id: str, *, for_update: bool = False) -> Sweep | None:
    stmt = select(Sweep).where(Sweep.id == sweep_id, Sweep.project_id == project_id)
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalars(stmt).first()


def count_runs_for_sweeps(db: Session, project_id: str, sweep_ids: Sequence[str]) -> dict[str, int]:
    if not sweep_ids:
        return {}
    rows = db.execute(
        select(Run.sweep_id, func.count(Run.id))
        .where(Run.project_id == project_id, Run.sweep_id.in_(sweep_ids))
        .group_by(Run.sweep_id)
    ).all()
    return {str(sid): int(cnt) for sid, cnt in rows if sid is not None}


def list_sweeps(
    db: Session,
    project_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    stmt = select(Sweep).where(Sweep.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Sweep.status == status)
    rows = list(db.scalars(stmt.order_by(Sweep.created_at.desc(), Sweep.id.desc()).limit(limit)))
    counts = count_runs_for_sweeps(db, project_id, [s.id for s in rows])
    return [render_sweep(s, counts.get(s.id, 0)) for s in rows]


def _scored_trials_for_tpe(
    db: Session,
    store: MetricStore,
    sweep: Sweep,
) -> list[tuple[dict[str, Any], float]]:
    """Return [(params, best_or_last_metric)] for runs of this sweep that already have a metric value."""
    runs = list(
        db.scalars(
            select(Run)
            .where(Run.project_id == sweep.project_id, Run.sweep_id == sweep.id)
            .order_by(Run.started_at.asc(), Run.id.asc())
        )
    )
    if not runs:
        return []
    sr_by_run = {
        sr.run_id: sr
        for sr in db.scalars(select(SweepRun).where(SweepRun.sweep_id == sweep.id))
    }
    missing_ids = [r.id for r in runs if r.id not in sr_by_run or sr_by_run[r.id].best_value is None]
    summaries = store.summaries(sweep.project_id, missing_ids) if missing_ids else {}

    scored: list[tuple[dict[str, Any], float]] = []
    for r in runs:
        sr = sr_by_run.get(r.id)
        val: float | None = sr.best_value if sr is not None and sr.best_value is not None else None
        if val is None:
            raw = summaries.get(r.id, {}).get(sweep.metric_key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(float(raw)):
                val = float(raw)
        if val is not None and math.isfinite(val):
            scored.append((dict(r.params or {}), val))
    return scored


def next_trial(
    db: Session,
    store: MetricStore,
    settings: Any,
    project_id: str,
    sweep_id: str,
    *,
    agent_id: str | None = None,
    created_by: str | None = None,
    run_name: str | None = None,
    run_type: str | None = None,
    base_model: str | None = None,
    dataset_version_id: str | None = None,
    tags: Sequence[str] = (),
) -> NextTrialResult | None:
    """Atomically allocate the next configuration and create a canonical `Run` linked to the sweep.

    Returns None if the sweep does not exist in `project_id`.
    """
    is_pg = db.get_bind().dialect.name == "postgresql"

    for attempt in range(MAX_NEXT_RETRIES):
        sweep = get_sweep(db, project_id, sweep_id, for_update=is_pg)
        if sweep is None:
            return None
        if sweep.status == "paused":
            return NextTrialResult(outcome="paused", retry_after=PAUSED_RETRY_AFTER)
        if sweep.status in ("finished", "stopped"):
            return NextTrialResult(outcome="exhausted")

        trial_index = int(sweep.next_index or 0)
        max_runs = sweep.config.get("max_runs")
        if max_runs is not None and trial_index >= int(max_runs):
            sweep.status = "finished"
            db.flush()
            return NextTrialResult(outcome="exhausted")

        method = str(sweep.config.get("method", "random")).lower()
        scored = _scored_trials_for_tpe(db, store, sweep) if method in ("tpe", "bayes") else []

        try:
            params = suggest_next(sweep.config, trial_index, scored)
        except SweepExhaustedError:
            sweep.status = "finished"
            db.flush()
            return NextTrialResult(outcome="exhausted")

        runs_service.check_capacity(db, settings, project_id)

        try:
            with db.begin_nested():
                # Compare-and-swap on next_index ensures no two concurrent callers allocate the same index.
                res = db.execute(
                    update(Sweep)
                    .where(Sweep.id == sweep.id, Sweep.next_index == trial_index)
                    .values(next_index=trial_index + 1)
                )
                if res.rowcount != 1:
                    db.expire(sweep)
                    continue

                name = run_name.strip() if run_name else f"{sweep.name}-{trial_index + 1}"
                run = runs_service.new_run(
                    db,
                    project_id,
                    name=name[:200],
                    tags=tags,
                    params=params,
                    sweep_id=sweep.id,
                    run_type=run_type,
                    base_model=base_model,
                    dataset_version_id=dataset_version_id,
                    created_by=created_by,
                )
                bracket = assign_bracket(sweep.config.get("early_stop"), trial_index)
                sr = SweepRun(
                    sweep_id=sweep.id,
                    project_id=project_id,
                    run_id=run.id,
                    trial_index=trial_index,
                    bracket=bracket,
                    agent_id=agent_id,
                )
                db.add(sr)
                db.flush()
                db.refresh(sweep)
            return NextTrialResult(outcome="ok", run=run, sweep_run=sr, params=params)
        except (IntegrityError, OperationalError):
            db.expire_all()
            if attempt == MAX_NEXT_RETRIES - 1:
                raise
            time.sleep(0.005 * (attempt + 1))

    raise SweepConflictError("concurrent sweep allocation failed after retries")


def _get_or_create_sweep_run(db: Session, sweep: Sweep, run: Run) -> SweepRun:
    sr = db.scalars(
        select(SweepRun).where(SweepRun.sweep_id == sweep.id, SweepRun.run_id == run.id)
    ).first()
    if sr is not None:
        return sr
    # Fallback for runs created directly via POST /runs with sweep_id
    max_idx = db.scalar(select(func.max(SweepRun.trial_index)).where(SweepRun.sweep_id == sweep.id))
    next_idx = (int(max_idx) + 1) if max_idx is not None else int(sweep.next_index or 0)
    sr = SweepRun(
        sweep_id=sweep.id,
        project_id=sweep.project_id,
        run_id=run.id,
        trial_index=next_idx,
        bracket=assign_bracket(sweep.config.get("early_stop"), next_idx),
    )
    db.add(sr)
    db.flush()
    return sr


def report_metric(
    db: Session,
    store: MetricStore,
    settings: Any,
    sweep: Sweep,
    run: Run,
    *,
    step: int,
    value: float,
    record_point: bool = True,
) -> ReportResult:
    """Record a target metric point (if `record_point`) and evaluate sweep early-stopping."""
    now = utcnow()
    if record_point and runs_service.accepts_points(run, now):
        runs_service.ingest_points(
            run,
            [{"key": sweep.metric_key, "step": int(step), "value": float(value)}],
            store,
            settings,
            now,
        )

    sr = _get_or_create_sweep_run(db, sweep, run)
    if sr.last_step is None or int(step) >= int(sr.last_step):
        sr.last_step = int(step)
        sr.last_value = float(value)
    if _is_better(float(value), sr.best_value, sweep.metric_goal):
        sr.best_value = float(value)
    if _is_better(float(value), sweep.best_value, sweep.metric_goal):
        sweep.best_value = float(value)
        sweep.best_run_id = run.id

    if sweep.status in ("stopped", "finished"):
        db.flush()
        return ReportResult(action="stop", reason=f"sweep is {sweep.status}")

    if sr.pruned:
        db.flush()
        return ReportResult(action="stop", reason="run already pruned")

    early_stop = sweep.config.get("early_stop")
    if early_stop:
        peer_ids = list(
            db.scalars(
                select(SweepRun.run_id).where(
                    SweepRun.sweep_id == sweep.id,
                    SweepRun.bracket == sr.bracket,
                    SweepRun.run_id != run.id,
                )
            )
        )
        # Also include any runs linked via Run.sweep_id that don't have a SweepRun row yet (legacy)
        if not peer_ids:
            peer_ids = list(
                db.scalars(
                    select(Run.id).where(
                        Run.project_id == sweep.project_id,
                        Run.sweep_id == sweep.id,
                        Run.id != run.id,
                    )
                )
            )
        peer_series_raw: list[list[tuple[int, float]]] = []
        if peer_ids:
            series_map, _ = store.read_series(
                sweep.project_id, peer_ids, [sweep.metric_key], max_points=MAX_MAX_POINTS
            )
            for by_key in series_map.values():
                s = by_key.get(sweep.metric_key)
                if s is not None:
                    pts = [
                        (int(st), float(val))
                        for st, val in zip(s.steps, s.values, strict=True)
                        if val is not None and math.isfinite(float(val))
                    ]
                    if pts:
                        peer_series_raw.append(pts)

        decision = evaluate_pruning(
            early_stop,
            sweep.metric_goal,
            int(step),
            float(value),
            peer_series_raw,
            bracket=sr.bracket,
        )
        if decision.should_stop:
            sr.pruned = True
            db.flush()
            return ReportResult(action="stop", reason=decision.reason)

    db.flush()
    return ReportResult(action="continue", reason=None)


def sweep_detail(db: Session, store: MetricStore, sweep: Sweep) -> dict[str, Any]:
    """Return {sweep, runs, importance, importance_note} per G2 contract."""
    runs = list(
        db.scalars(
            select(Run)
            .where(Run.project_id == sweep.project_id, Run.sweep_id == sweep.id)
            .order_by(Run.started_at.asc(), Run.id.asc())
        )
    )
    sr_by_run = {
        sr.run_id: sr
        for sr in db.scalars(select(SweepRun).where(SweepRun.sweep_id == sweep.id))
    }

    run_ids = [r.id for r in runs]
    # Fetch metric summaries and last steps for runs missing cached SweepRun values
    need_metrics = [r.id for r in runs if r.id not in sr_by_run or sr_by_run[r.id].last_value is None]
    summaries = store.summaries(sweep.project_id, need_metrics) if need_metrics else {}
    last_steps = store.last_steps(sweep.project_id, need_metrics) if need_metrics else {}

    runs_out: list[dict[str, Any]] = []
    completed_for_imp: list[tuple[dict[str, Any], float]] = []

    for r in runs:
        sr = sr_by_run.get(r.id)
        last_val = sr.last_value if sr is not None else None
        best_val = sr.best_value if sr is not None else None
        step = sr.last_step if sr is not None else None
        pruned = bool(sr.pruned) if sr is not None else False

        if last_val is None:
            raw = summaries.get(r.id, {}).get(sweep.metric_key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(float(raw)):
                last_val = float(raw)
                if best_val is None:
                    best_val = last_val
        if step is None:
            step = last_steps.get(r.id)

        runs_out.append(
            {
                "run_id": r.id,
                "status": r.status,
                "params": dict(r.params or {}),
                "last_value": last_val,
                "best_value": best_val,
                "pruned": pruned,
                "step": step,
            }
        )
        if r.status == "finished" and best_val is not None and math.isfinite(best_val):
            completed_for_imp.append((dict(r.params or {}), best_val))

    completed_count = len(completed_for_imp)
    cache = sweep.importance_cache if isinstance(sweep.importance_cache, dict) else None
    if cache is not None and cache.get("count") == completed_count and cache.get("importance") is not None:
        importance = cache["importance"]
        importance_note = cache.get("note")
    else:
        importance, importance_note = compute_importance(
            sweep.config.get("parameters", {}),
            completed_for_imp,
            seed=sweep.seed,
        )
        if importance is not None:
            sweep.importance_cache = {"count": completed_count, "importance": importance, "note": importance_note}
            db.flush()

    return {
        "sweep": render_sweep(sweep, len(run_ids)),
        "runs": runs_out,
        "importance": importance,
        "importance_note": importance_note,
    }


def patch_sweep_status(db: Session, sweep: Sweep, new_status: str) -> Sweep:
    if new_status not in ("paused", "running", "stopped"):
        raise SweepValidationError({"status": "status must be 'paused', 'running', or 'stopped'"})
    if sweep.status in ("finished", "stopped") and new_status != "stopped":
        raise SweepConflictError(f"cannot transition sweep from '{sweep.status}' to '{new_status}'")
    sweep.status = new_status
    if new_status == "stopped":
        sweep.stopped_at = utcnow()
    elif new_status == "running":
        sweep.stopped_at = None
    db.flush()
    return sweep


def delete_sweep(db: Session, sweep: Sweep) -> None:
    db.execute(
        update(Run)
        .where(Run.project_id == sweep.project_id, Run.sweep_id == sweep.id)
        .values(sweep_id=None)
    )
    db.delete(sweep)
    db.flush()
