"""Offline experiment runner: dataset items x variants -> provider call -> metrics (spec section 4).

Provider calls run in a bounded thread pool and touch no database state; the caller's session
persists the results. Security: provider/judge keys come only from the environment (see
`evals.providers`); per-cell errors keep only the sanitized `ProviderError` text or an exception
type name, never a traceback or request body.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.evals.judge import HeuristicJudge, LLMJudge
from matrix_server.evals.metrics import EvalCase, EvalDeps, Metric, build_metric, evaluate_case
from matrix_server.evals.providers import HashingEmbedder, OpenAIEmbedder, ProviderError, get_provider
from matrix_server.prompts import VARIABLE_RE
from matrix_server.storage.db import utcnow
from matrix_server.storage.models import DatasetItem, Experiment, ExperimentResult, PromptVersion
from matrix_server.workers.context import JobLost, lost_event

log = logging.getLogger(__name__)

REGRESSION_DELTA = 0.1
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 32
MAX_CELL_ERROR_CHARS = 500
DEFAULT_MAX_CELLS = 5000


class ExperimentConfigError(ValueError):
    """Systemic, user-facing problem (message is safe to store on the experiment)."""


# --- configuration --------------------------------------------------------------


def concurrency() -> int:
    raw = os.environ.get("MATRIX_EXPERIMENT_CONCURRENCY", "")
    try:
        value = int(raw) if raw.strip() else DEFAULT_CONCURRENCY
    except ValueError:
        value = DEFAULT_CONCURRENCY
    return max(1, min(value, MAX_CONCURRENCY))


def max_cells() -> int:
    """Upper bound on items x variants per experiment (`MATRIX_MAX_EXPERIMENT_CELLS`, default 5000)."""
    raw = os.environ.get("MATRIX_MAX_EXPERIMENT_CELLS", "")
    try:
        value = int(raw) if raw.strip() else DEFAULT_MAX_CELLS
    except ValueError:
        value = DEFAULT_MAX_CELLS
    return max(1, value)


def build_eval_deps() -> EvalDeps:
    """Judge/embedder from `MATRIX_JUDGE_PROVIDER` / `MATRIX_JUDGE_MODEL` (default: offline heuristics)."""
    provider = (os.environ.get("MATRIX_JUDGE_PROVIDER") or "fake").strip().lower()
    if provider == "fake":
        return EvalDeps(judge=HeuristicJudge(), embedder=HashingEmbedder())
    model = (os.environ.get("MATRIX_JUDGE_MODEL") or "").strip()
    if not model:
        raise ExperimentConfigError("MATRIX_JUDGE_MODEL must be set when MATRIX_JUDGE_PROVIDER is not 'fake'")
    try:
        llm = get_provider(provider, **_provider_opts(provider, judge_base_url()))
    except ValueError as exc:
        raise ExperimentConfigError(f"invalid MATRIX_JUDGE_PROVIDER: {provider!r}") from exc
    if provider == "openai":
        embed_model = (os.environ.get("MATRIX_EMBEDDING_MODEL") or "text-embedding-3-small").strip()
        embedder: Any = OpenAIEmbedder(model=embed_model, base_url=judge_base_url())
    else:
        embedder = HashingEmbedder()
    return EvalDeps(judge=LLMJudge(llm, model), embedder=embedder)


def _env_url(name: str) -> str | None:
    value = (os.environ.get(name) or "").strip()
    return value or None


def _openai_base_url() -> str | None:
    """`MATRIX_OPENAI_BASE_URL`: OpenAI-compatible endpoint for experiment cells and judges."""
    return _env_url("MATRIX_OPENAI_BASE_URL")


def judge_base_url() -> str | None:
    """Judge/embedder endpoint (offline experiments and online evals): `MATRIX_JUDGE_BASE_URL` when
    set (judge-specific override), else `MATRIX_OPENAI_BASE_URL`."""
    return _env_url("MATRIX_JUDGE_BASE_URL") or _openai_base_url()


def _provider_opts(provider: str, base_url: str | None = None) -> dict[str, Any]:
    # base_url is operator configuration (env), never request input: avoids SSRF via the API.
    base_url = base_url if base_url is not None else _openai_base_url()
    if provider == "openai" and base_url:
        return {"base_url": base_url}
    return {}


# --- prompt rendering -------------------------------------------------------------


def _var_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _render(text: str, variables: dict[str, Any], missing: list[str]) -> str:
    def sub(match: Any) -> str:
        key = match.group(1)
        if key not in variables:
            if key not in missing:
                missing.append(key)
            return ""
        return _var_value(variables[key])

    return VARIABLE_RE.sub(sub, text)


def input_text(item_input: dict[str, Any]) -> str:
    for key in ("question", "query", "input"):
        if isinstance(item_input.get(key), str):
            return item_input[key]
    return _var_value(item_input)


def render_messages(prompt_type: str, template: Any, item_input: dict[str, Any]) -> tuple[list[dict], list[str]]:
    """Messages for the provider plus the variables that were missing from the item input.

    Templates without any `{{var}}` get the item input appended as the user message (a text
    template then acts as the system instruction)."""
    missing: list[str] = []
    variables = item_input if isinstance(item_input, dict) else {"input": item_input}
    if prompt_type == "text":
        has_vars = bool(VARIABLE_RE.search(template))
        rendered = _render(template, variables, missing)
        if has_vars:
            return [{"role": "user", "content": rendered}], missing
        return [{"role": "system", "content": rendered}, {"role": "user", "content": input_text(variables)}], []
    has_vars = any(VARIABLE_RE.search(m["content"]) for m in template)
    messages = [{"role": m["role"], "content": _render(m["content"], variables, missing)} for m in template]
    if not has_vars:
        messages.append({"role": "user", "content": input_text(variables)})
    return messages, missing


# --- execution -------------------------------------------------------------------


@dataclass
class ResolvedVariant:
    label: str
    prompt_type: str
    template: Any
    provider: str
    model: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CellResult:
    item_id: str
    variant: str
    output: str | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    scores: dict[str, Any] = field(default_factory=dict)


def _na_scores(metrics: list[Metric], reason: str) -> dict[str, Any]:
    return {m.name: {"value": None, "passed": None, "reason": reason} for m in metrics}


def _clean_number(value: float | None) -> float | None:
    return value if value is None or math.isfinite(value) else None


def _score_dict(result: Any) -> dict[str, Any]:
    return {"value": _clean_number(result.value), "passed": result.passed, "reason": result.reason}


def run_cell(item: dict[str, Any], variant: ResolvedVariant, metrics: list[Metric], deps: EvalDeps) -> CellResult:
    cell = CellResult(item_id=item["id"], variant=variant.label)
    messages, missing = render_messages(variant.prompt_type, variant.template, item["input"])
    notes = [f"missing variables: {', '.join(missing)}"] if missing else []
    try:
        provider = get_provider(variant.provider, **_provider_opts(variant.provider))
        t0 = time.perf_counter()
        completion = provider.complete(messages, variant.model, dict(variant.params) or None)
        measured = (time.perf_counter() - t0) * 1000
    except ProviderError as exc:
        cell.error = "; ".join([*notes, str(exc)])[:MAX_CELL_ERROR_CHARS]
        cell.scores = _na_scores(metrics, "skipped: provider error")
        return cell
    except Exception as exc:  # noqa: BLE001 - one cell must never sink the experiment
        log.warning("experiment cell failed: %s", type(exc).__name__)
        cell.error = "; ".join([*notes, f"provider call failed: {type(exc).__name__}"])[:MAX_CELL_ERROR_CHARS]
        cell.scores = _na_scores(metrics, "skipped: provider error")
        return cell
    cell.output = completion.text
    cell.latency_ms = _clean_number(float(completion.latency_ms or measured))
    cell.cost_usd = _clean_number(float(completion.cost_usd or 0.0))
    cell.input_tokens = int(completion.input_tokens or 0)
    cell.output_tokens = int(completion.output_tokens or 0)
    cell.error = "; ".join(notes) or None
    case = EvalCase(
        input=item["input"],
        output=completion.text or "",
        expected=item["expected_output"],
        contexts=list(item["context"] or []),
        latency_ms=cell.latency_ms,
        cost_usd=cell.cost_usd,
    )
    cell.scores = {name: _score_dict(r) for name, r in evaluate_case(metrics, case, deps).items()}
    return cell


def resolve_variants(session: Session, exp: Experiment) -> list[ResolvedVariant]:
    out = []
    for v in exp.variants:
        prompt, model = v["prompt"], v["model"]
        if "template" in prompt:
            prompt_type, template = prompt.get("type", "text"), prompt["template"]
        else:
            pv = session.scalars(
                select(PromptVersion).where(
                    PromptVersion.project_id == exp.project_id,
                    PromptVersion.name == prompt["name"],
                    PromptVersion.version == prompt["version"],
                )
            ).first()
            if pv is None:
                raise ExperimentConfigError(f"prompt {prompt['name']!r} version {prompt['version']} not found")
            prompt_type, template = pv.type, pv.template
        out.append(
            ResolvedVariant(
                label=v["label"],
                prompt_type=prompt_type,
                template=template,
                provider=model["provider"],
                model=model["name"],
                params=dict(model.get("params") or {}),
            )
        )
    return out


def summarize(results: list[Any], metric_names: list[str]) -> dict[str, dict[str, dict[str, float | None]]]:
    """`{variant: {metric: {mean, pass_rate}}}`; mean over non-null values, pass_rate over non-null passed."""
    acc: dict[str, dict[str, tuple[list[float], list[bool]]]] = {}
    for r in results:
        per = acc.setdefault(r.variant, {name: ([], []) for name in metric_names})
        for name, score in (r.scores or {}).items():
            values, passes = per.setdefault(name, ([], []))
            if score.get("value") is not None:
                values.append(float(score["value"]))
            if score.get("passed") is not None:
                passes.append(bool(score["passed"]))
    return {
        variant: {
            name: {
                "mean": sum(values) / len(values) if values else None,
                "pass_rate": sum(passes) / len(passes) if passes else None,
            }
            for name, (values, passes) in per.items()
        }
        for variant, per in acc.items()
    }


def find_regressions(baseline: list[Any], current: list[Any]) -> list[dict[str, Any]]:
    """Cells (same item and variant label) where a metric went passed -> failed or dropped > 0.1."""
    base = {(r.item_id, r.variant): r.scores or {} for r in baseline}
    out = []
    for r in current:
        before = base.get((r.item_id, r.variant))
        if before is None:
            continue
        for metric, now in sorted((r.scores or {}).items()):
            prev = before.get(metric)
            if prev is None:
                continue
            failed = prev.get("passed") is True and now.get("passed") is False
            pv, cv = prev.get("value"), now.get("value")
            dropped = pv is not None and cv is not None and pv - cv > REGRESSION_DELTA
            if failed or dropped:
                out.append({"item_id": r.item_id, "variant": r.variant, "metric": metric,
                            "baseline": pv, "current": cv})
    return out


def run_experiment(session: Session, exp: Experiment) -> None:
    """Compute every (item, variant) cell and upsert results + summary in `session` (no commit)."""
    items = session.scalars(
        select(DatasetItem)
        .where(DatasetItem.dataset_id == exp.dataset_id, DatasetItem.project_id == exp.project_id)
        .order_by(DatasetItem.created_at, DatasetItem.id)
    ).all()
    if not items:
        raise ExperimentConfigError("dataset has no items")
    try:
        metrics = [build_metric(spec) for spec in exp.metrics]
    except ValueError as exc:
        raise ExperimentConfigError(f"invalid metric: {exc}") from exc
    variants = resolve_variants(session, exp)
    if len(items) * len(variants) > max_cells():
        raise ExperimentConfigError(
            f"experiment has {len(items) * len(variants)} cells (items x variants); the limit is {max_cells()}"
        )
    deps = build_eval_deps()
    snapshots = [
        {"id": i.id, "input": i.input or {}, "expected_output": i.expected_output, "context": i.context or []}
        for i in items
    ]
    tasks = [(item, variant) for item in snapshots for variant in variants]
    # Inside a worker: once the job lease is lost, pending cells are skipped and the run stops.
    lost = lost_event()

    def run(task: tuple[dict[str, Any], ResolvedVariant]) -> CellResult | None:
        if lost is not None and lost.is_set():
            return None
        return run_cell(task[0], task[1], metrics, deps)

    with ThreadPoolExecutor(max_workers=min(concurrency(), len(tasks))) as pool:
        maybe_cells = list(pool.map(run, tasks))
    if lost is not None and lost.is_set():
        raise JobLost
    cells = [c for c in maybe_cells if c is not None]

    existing = {
        (r.item_id, r.variant): r
        for r in session.scalars(select(ExperimentResult).where(ExperimentResult.experiment_id == exp.id))
    }
    rows = []
    for cell in cells:
        row = existing.get((cell.item_id, cell.variant))
        if row is None:
            row = ExperimentResult(experiment_id=exp.id, project_id=exp.project_id,
                                   item_id=cell.item_id, variant=cell.variant)
            session.add(row)
        row.output = cell.output
        row.latency_ms = cell.latency_ms
        row.cost_usd = cell.cost_usd
        row.input_tokens = cell.input_tokens
        row.output_tokens = cell.output_tokens
        row.error = cell.error
        row.scores = cell.scores
        rows.append(row)
    computed = {(c.item_id, c.variant) for c in cells}
    for key, stale in existing.items():  # items deleted since a previous attempt
        if key not in computed:
            session.delete(stale)
    exp.summary = summarize(rows, [m.name for m in metrics])
    exp.status = "done"
    exp.error = None
    exp.finished_at = utcnow()
    session.flush()
