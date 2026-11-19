"""`main`'s response shapes for `runs` and `model_artifacts` rows (deprecated `/api/v1/mlops/*`
adapters and the sweep routes, until T5/T9/T10 move their callers to the G1 shapes)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.mlops.compat import CLASSIC_EVAL_REPORT, reports_to_classic_eval, status_out
from matrix_server.runs.models import Run, RunReport
from matrix_server.runs.store import MetricStore
from matrix_server.storage.db import as_utc
from matrix_server.storage.models import ModelArtifact


def iso(value: Any) -> str | None:
    dt = as_utc(value)
    return None if dt is None else dt.isoformat().replace("+00:00", "Z")


def classic_evals(db: Session, run_ids: Sequence[str]) -> dict[str, dict[str, Any] | None]:
    found: dict[str, list[tuple[str, Any]]] = {}
    if run_ids:
        rows = db.execute(
            select(RunReport.run_id, RunReport.kind, RunReport.data)
            .where(RunReport.run_id.in_(list(run_ids)), RunReport.name == CLASSIC_EVAL_REPORT)
            .order_by(RunReport.created_at, RunReport.id)
        )
        for run_id, kind, data in rows:
            found.setdefault(run_id, []).append((kind, data))
    return {run_id: reports_to_classic_eval(items) for run_id, items in found.items()}


def render_runs(db: Session, store: MetricStore, runs: Sequence[Run]) -> list[dict[str, Any]]:
    """Runs in `main`'s shape: summary and total_steps from the metric store, classic_eval from the
    `classic_eval` reports, hardware_info from `env["hardware"]` (one store query of each kind)."""
    if not runs:
        return []
    project_id = runs[0].project_id
    ids = [r.id for r in runs]
    summaries = store.summaries(project_id, ids)
    steps = store.last_steps(project_id, ids)
    evals = classic_evals(db, ids)
    out = []
    for run in runs:
        summary = {k: v for k, v in (summaries.get(run.id) or {}).items() if v is not None}
        env = run.env if isinstance(run.env, dict) else {}
        out.append(
            {
                "id": run.id,
                "project_id": run.project_id,
                "name": run.name,
                "run_type": run.run_type,
                "status": status_out(run.status),
                "base_model": run.base_model,
                "dataset_id": run.dataset_version_id,
                "sweep_id": run.sweep_id,
                "parent_run_id": run.parent_run_id,
                "hyperparams": dict(run.params or {}),
                "summary_metrics": summary,
                "latest_metrics": dict(summary),
                "hardware_info": env.get("hardware"),
                "classic_eval": evals.get(run.id),
                "tags": list(run.tags or []),
                "total_steps": steps.get(run.id),
                "error": run.error,
                "last_heartbeat_at": iso(run.heartbeat_at),
                "created_at": iso(run.started_at),
                "started_at": iso(run.started_at),
                "finished_at": iso(run.ended_at),
            }
        )
    return out


def render_run(db: Session, store: MetricStore, run: Run) -> dict[str, Any]:
    return render_runs(db, store, [run])[0]


def artifact_out(art: ModelArtifact, backend: str) -> dict[str, Any]:
    meta = dict(art.metadata_ or {})
    if art.external_uri:
        storage_backend, storage_uri = "external", art.external_uri
    elif art.artifact_id:
        storage_backend, storage_uri = backend, f"artifact:{art.artifact_id}"
    else:  # migrated from `main`'s own store (0008) or its blob row was deleted
        storage_backend = "legacy" if "legacy_storage_uri" in meta else "missing"
        storage_uri = str(meta.get("legacy_storage_uri") or "")
    return {
        "id": art.id,
        "project_id": art.project_id,
        "run_id": art.run_id,
        "artifact_id": art.artifact_id,
        "name": art.name,
        "kind": art.kind,
        "version": art.version,
        "storage_backend": storage_backend,
        "storage_uri": storage_uri,
        "sha256": art.sha256,
        "size_bytes": art.size_bytes,
        "content_type": art.content_type,
        "status": art.status,
        "metadata": meta,
        "created_at": iso(art.created_at),
        "committed_at": iso(art.committed_at),
    }
