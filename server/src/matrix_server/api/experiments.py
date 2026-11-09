"""Experiments API: prompt x model matrix over a dataset (spec section 4, Datasets e experimentos).

Regressions against the baseline are computed on read from the stored results (no extra column):
they always reflect the current baseline/experiment results.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from matrix_server.api.datasets import get_dataset_or_404, iso_utc, parse_body
from matrix_server.auth import current_project_id
from matrix_server.evals.metrics import build_metric
from matrix_server.evals.providers import get_provider
from matrix_server.evals.runner import find_regressions
from matrix_server.prompts import PROMPT_TYPES, PromptValidationError, validate_template
from matrix_server.storage import jobs
from matrix_server.storage.db import get_db
from matrix_server.storage.models import (
    DatasetItem,
    Experiment,
    ExperimentResult,
    PromptLabel,
    PromptVersion,
)

DbSession = Annotated[Session, Depends(get_db)]
ProjectId = Annotated[str, Depends(current_project_id)]

router = APIRouter(prefix="/api/v1", tags=["experiments"])

MAX_VARIANTS = 10
MAX_METRICS = 20
MAX_PARAMS_BYTES = 10_000
# Keys the caller must not override in the provider request payload.
RESERVED_PARAMS = frozenset({"model", "messages", "stream", "system"})


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dataset_id: str = Field(min_length=1, max_length=32)
    baseline_experiment_id: str | None = Field(default=None, max_length=32)
    variants: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_VARIANTS)
    metrics: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_METRICS)


def _bad(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def _validate_prompt(db: Session, project_id: str, i: int, prompt: Any) -> dict[str, Any]:
    if not isinstance(prompt, dict):
        raise _bad(f"variants[{i}].prompt must be an object")
    if "template" in prompt:
        if set(prompt) - {"template", "type"}:
            raise _bad(f"variants[{i}].prompt: inline prompts take only 'template' and 'type'")
        ptype = prompt.get("type", "text")
        if ptype not in PROMPT_TYPES:
            raise _bad(f"variants[{i}].prompt.type must be 'text' or 'chat'")
        try:
            validate_template(ptype, prompt["template"])
        except PromptValidationError as exc:
            raise _bad(f"variants[{i}].prompt: {exc}") from exc
        return {"template": prompt["template"], "type": ptype}
    name, version = prompt.get("name"), prompt.get("version")
    if not isinstance(name, str) or not name or set(prompt) - {"name", "version"}:
        raise _bad(f"variants[{i}].prompt must be {{name, version}} or {{template, type}}")
    if isinstance(version, str):  # a label (e.g. "production") is pinned to its current version
        version = db.scalar(
            select(PromptLabel.version).where(
                PromptLabel.project_id == project_id, PromptLabel.name == name, PromptLabel.label == version
            )
        )
    if not isinstance(version, int) or isinstance(version, bool):
        raise _bad(f"variants[{i}].prompt: version of prompt {name!r} not found")
    exists = db.scalar(
        select(PromptVersion.id).where(
            PromptVersion.project_id == project_id, PromptVersion.name == name, PromptVersion.version == version
        )
    )
    if exists is None:
        raise _bad(f"variants[{i}].prompt: prompt {name!r} version {version} not found")
    return {"name": name, "version": version}


def _validate_model(i: int, model: Any) -> dict[str, Any]:
    if not isinstance(model, dict) or set(model) - {"provider", "name", "params"}:
        raise _bad(f"variants[{i}].model must be {{provider, name, params?}}")
    provider, name, params = model.get("provider"), model.get("name"), model.get("params")
    if not isinstance(provider, str) or not provider.strip():
        raise _bad(f"variants[{i}].model.provider is required")
    try:
        get_provider(provider)
    except ValueError as exc:
        raise _bad(f"variants[{i}].model: {exc}") from exc
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise _bad(f"variants[{i}].model.name must be a non-empty string")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise _bad(f"variants[{i}].model.params must be an object")
    reserved = sorted(set(params) & RESERVED_PARAMS)
    if reserved:
        raise _bad(f"variants[{i}].model.params must not set {reserved}")
    if len(json.dumps(params, allow_nan=False)) > MAX_PARAMS_BYTES:
        raise _bad(f"variants[{i}].model.params is too large")
    return {"provider": provider.strip().lower(), "name": name, "params": params}


def _validate_variants(db: Session, project_id: str, variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out, labels = [], set()
    for i, v in enumerate(variants):
        if set(v) - {"label", "prompt", "model"}:
            raise _bad(f"variants[{i}] takes only label, prompt and model")
        label = v.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            raise _bad(f"variants[{i}].label must be a non-empty string of at most 100 characters")
        if label in labels:
            raise _bad(f"duplicate variant label {label!r}")
        labels.add(label)
        out.append(
            {
                "label": label,
                "prompt": _validate_prompt(db, project_id, i, v.get("prompt")),
                "model": _validate_model(i, v.get("model")),
            }
        )
    return out


def _validate_metrics(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = set()
    for i, spec in enumerate(metrics):
        try:
            metric = build_metric(spec)
        except ValueError as exc:
            raise _bad(f"metrics[{i}]: {exc}") from exc
        if metric.name in names:
            raise _bad(f"duplicate metric name {metric.name!r}")
        names.add(metric.name)
    return metrics


def _experiment_out(exp: Experiment) -> dict[str, Any]:
    return {
        "id": exp.id,
        "name": exp.name,
        "dataset_id": exp.dataset_id,
        "baseline_experiment_id": exp.baseline_experiment_id,
        "variants": exp.variants,
        "metrics": exp.metrics,
        "status": exp.status,
        "error": exp.error,
        "summary": exp.summary,
        "created_at": iso_utc(exp.created_at),
        "started_at": iso_utc(exp.started_at),
        "finished_at": iso_utc(exp.finished_at),
    }


def _result_out(r: ExperimentResult) -> dict[str, Any]:
    return {
        "item_id": r.item_id,
        "variant": r.variant,
        "output": r.output,
        "latency_ms": r.latency_ms,
        "cost_usd": r.cost_usd,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "error": r.error,
        "scores": r.scores or {},
    }


def _results(db: Session, experiment_id: str) -> list[ExperimentResult]:
    return list(
        db.scalars(
            select(ExperimentResult)
            .where(ExperimentResult.experiment_id == experiment_id)
            .order_by(ExperimentResult.variant, ExperimentResult.created_at, ExperimentResult.item_id)
        ).all()
    )


@router.post("/experiments", status_code=201)
def create_experiment(
    db: DbSession, project_id: ProjectId, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    body = parse_body(ExperimentCreate, payload)
    dataset = get_dataset_or_404(db, project_id, body.dataset_id)
    item_count = db.scalar(select(func.count(DatasetItem.id)).where(DatasetItem.dataset_id == dataset.id))
    if not item_count:
        raise _bad("dataset has no items")
    variants = _validate_variants(db, project_id, body.variants)
    metrics = _validate_metrics(body.metrics)
    if body.baseline_experiment_id is not None:
        baseline = db.scalars(
            select(Experiment).where(
                Experiment.id == body.baseline_experiment_id, Experiment.project_id == project_id
            )
        ).first()
        if baseline is None:
            raise _bad("baseline experiment not found")
        if baseline.status != "done" or baseline.dataset_id != dataset.id:
            raise _bad("baseline must be a finished experiment on the same dataset")
    exp = Experiment(
        project_id=project_id,
        name=body.name,
        dataset_id=dataset.id,
        baseline_experiment_id=body.baseline_experiment_id,
        variants=variants,
        metrics=metrics,
        status="queued",
    )
    db.add(exp)
    db.flush()
    jobs.enqueue(db, project_id, "experiment.run", {"experiment_id": exp.id})
    return {"id": exp.id, "status": exp.status}


@router.get("/experiments")
def list_experiments(db: DbSession, project_id: ProjectId) -> dict[str, Any]:
    rows = db.scalars(
        select(Experiment)
        .where(Experiment.project_id == project_id)
        .order_by(Experiment.created_at.desc(), Experiment.id)
    ).all()
    return {
        "items": [
            {
                "id": e.id,
                "name": e.name,
                "dataset_id": e.dataset_id,
                "status": e.status,
                "created_at": iso_utc(e.created_at),
                "finished_at": iso_utc(e.finished_at),
                "summary": e.summary,
            }
            for e in rows
        ]
    }


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, db: DbSession, project_id: ProjectId) -> dict[str, Any]:
    exp = db.scalars(
        select(Experiment).where(Experiment.id == experiment_id, Experiment.project_id == project_id)
    ).first()
    if exp is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    results = _results(db, exp.id)
    regressions: list[dict[str, Any]] = []
    if exp.baseline_experiment_id and exp.status == "done":
        regressions = find_regressions(_results(db, exp.baseline_experiment_id), results)
    return {
        "experiment": _experiment_out(exp),
        "results": [_result_out(r) for r in results],
        "summary": exp.summary or {},
        "regressions": regressions,
    }
