"""Pluggable benchmark runner (LM-Eval & VLMEvalKit) with egress policy, secret safety, and `simulated` flag (ADR-010, ADR-012, T7)."""

from __future__ import annotations

import abc
import hashlib
import ipaddress
import logging
import math
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from matrix_server import audit
from matrix_server.benchmarks.catalog import TASK_CATALOG, BenchmarkTaskDef
from matrix_server.egress import EgressError, EgressSettings, classify_ip, egress_client
from matrix_server.egress.policy import best_effort_host, check_url
from matrix_server.storage.db import utcnow
from matrix_server.storage.models import ArtifactLineage, BenchmarkRun, BenchmarkTaskResult

log = logging.getLogger(__name__)

API_KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
BENCHMARK_API_KEY_ENV_PREFIX = "MATRIX_BENCH_API_KEY_"
MAX_HTTP_TIMEOUT_SECONDS = 300.0
HTTP_BENCHMARK_MAX_WORKERS = 4
_CONTROL_OR_WS_RE = re.compile(r"[\x00-\x20\x7f]")


class EngineUnavailable(RuntimeError):
    """Raised when an optional real benchmark engine (`lm_eval`, `vlmeval`) is not installed."""

    non_retryable: bool = True


class BenchmarkCredentialError(RuntimeError):
    """Raised when a configured benchmark credential env var is unusable (no env oracle)."""

    non_retryable: bool = True


def allowed_benchmark_env_vars() -> set[str]:
    """Return explicit operator-allowlisted env var names from `MATRIX_BENCHMARK_ALLOWED_ENV_VARS`."""
    raw = os.environ.get("MATRIX_BENCHMARK_ALLOWED_ENV_VARS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def is_allowed_api_key_env(name: str) -> bool:
    """Allow only `MATRIX_BENCH_API_KEY_*` or explicitly allowlisted benchmark credential env vars."""
    if not API_KEY_ENV_RE.match(name):
        return False
    if name.startswith(BENCHMARK_API_KEY_ENV_PREFIX):
        return True
    return name in allowed_benchmark_env_vars()


# --- Config & Secret Validation ---------------------------------------------


def sanitize_runner_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Defensively strip any literal `api_key` from runner_config before returning to callers."""
    if not isinstance(config, dict):
        return {}
    return {k: v for k, v in config.items() if str(k).lower() != "api_key"}


def _precheck_endpoint_url_syntax(url: Any) -> str:
    """Pure syntactic + IP-literal pre-check (NO DNS lookup — `egress_client` resolves once & pins IP)."""
    if not isinstance(url, str) or not url:
        raise ValueError("runner_config.endpoint_url must be a non-empty string")
    if url != url.strip() or _CONTROL_OR_WS_RE.search(url):
        raise ValueError("runner_config.endpoint_url contains whitespace or control characters")

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported endpoint scheme '{parsed.scheme}'; must be 'http' or 'https'")
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise ValueError("External runner endpoint must not embed credentials")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("runner_config.endpoint_url must include a valid hostname")

    for seg in parsed.path.split("/"):
        if seg in {".", ".."}:
            raise ValueError("runner_config.endpoint_url must not contain path traversal segments")

    host_clean = hostname.lower().rstrip(".")
    try:
        ip = ipaddress.ip_address(host_clean)
    except ValueError:
        ip = None

    if ip is not None:
        verdict = classify_ip(ip)
        settings = EgressSettings()
        if verdict == "forbidden_address" or (verdict == "private_address" and not settings.allow_private):
            raise ValueError(f"External runner endpoint IP '{host_clean}' is not allowed ({verdict})")

    return url


def validate_runner_config(config: dict[str, Any] | None, *, backend: str) -> None:
    """Validate runner_config for secret hygiene and backend-specific fields."""
    cfg = config or {}
    if not isinstance(cfg, dict):
        raise ValueError("runner_config must be a JSON object")  # noqa: TRY004

    for k in cfg:
        if str(k).lower() == "api_key":
            raise ValueError(
                "runner_config.api_key is not allowed; use runner_config.api_key_env with the "
                "server environment variable name instead"
            )

    api_key_env = cfg.get("api_key_env")
    if api_key_env is not None:
        if not isinstance(api_key_env, str) or not API_KEY_ENV_RE.match(api_key_env):
            raise ValueError(
                "runner_config.api_key_env must be a valid environment variable name "
                "(matching ^[A-Z][A-Z0-9_]{0,63}$)"
            )
        if not is_allowed_api_key_env(api_key_env):
            raise ValueError(
                "runner_config.api_key_env must use the 'MATRIX_BENCH_API_KEY_' prefix "
                "or be explicitly allowlisted by MATRIX_BENCHMARK_ALLOWED_ENV_VARS"
            )

    if "timeout_seconds" in cfg and cfg["timeout_seconds"] is not None:
        timeout_val = cfg["timeout_seconds"]
        if (
            isinstance(timeout_val, bool)
            or not isinstance(timeout_val, (int, float))
            or not math.isfinite(float(timeout_val))
            or float(timeout_val) <= 0
        ):
            raise ValueError("runner_config.timeout_seconds must be a positive finite number")

    if backend in ("http", "vllm"):
        endpoint_url = cfg.get("endpoint_url")
        if not endpoint_url:
            raise ValueError(
                "runner_config.endpoint_url is required when runner_backend is 'http' or 'vllm'"
            )
        _precheck_endpoint_url_syntax(endpoint_url)


# --- BenchmarkEngine Interface & Implementations ----------------------------


class BenchmarkEngine(abc.ABC):
    """Abstract interface for benchmark execution engines (`local_sim`, `http`, `lm_eval`, `vlmeval`)."""

    name: str
    simulated: bool

    @abc.abstractmethod
    def run(
        self,
        run: BenchmarkRun,
        task_defs: list[BenchmarkTaskDef],
        *,
        session_factory: Callable[[], Session],
    ) -> list[BenchmarkTaskResult]:
        """Execute `task_defs` for `run` and return `BenchmarkTaskResult` rows."""


class LocalSimBenchmarkEngine(BenchmarkEngine):
    """Deterministic simulator computing reproducible task scores (`simulated=True`)."""

    name = "local_sim"
    simulated = True

    def run(
        self,
        run: BenchmarkRun,
        task_defs: list[BenchmarkTaskDef],
        *,
        session_factory: Callable[[], Session],
    ) -> list[BenchmarkTaskResult]:
        del session_factory
        cfg = run.runner_config if isinstance(run.runner_config, dict) else {}
        seed = int(cfg.get("seed", 42))
        return run_local_sim(
            project_id=run.project_id,
            benchmark_run_id=run.id,
            target_model=run.target_model,
            eval_mode=run.eval_mode,
            num_fewshot=run.num_fewshot,
            task_defs=task_defs,
            seed=seed,
        )


class HttpBenchmarkEngine(BenchmarkEngine):
    """HTTP / vLLM OpenAI-compatible benchmark engine routed exclusively through `egress_client`.

    Marked `simulated = True` until a real dataset evaluation scorer replaces synthetic response-hash scoring.
    """

    name = "http"
    simulated = True

    def run(
        self,
        run: BenchmarkRun,
        task_defs: list[BenchmarkTaskDef],
        *,
        session_factory: Callable[[], Session],
    ) -> list[BenchmarkTaskResult]:
        return execute_http_runner_tasks(run, task_defs, session_factory=session_factory)


class LmEvalBenchmarkEngine(BenchmarkEngine):
    """Stub for real `lm-evaluation-harness` engine (ADR-010)."""

    name = "lm_eval"
    simulated = False

    def run(
        self,
        run: BenchmarkRun,
        task_defs: list[BenchmarkTaskDef],
        *,
        session_factory: Callable[[], Session],
    ) -> list[BenchmarkTaskResult]:
        del run, task_defs, session_factory
        raise EngineUnavailable("install the real engine")


class VlmEvalBenchmarkEngine(BenchmarkEngine):
    """Stub for real `VLMEvalKit` engine (ADR-010)."""

    name = "vlmeval"
    simulated = False

    def run(
        self,
        run: BenchmarkRun,
        task_defs: list[BenchmarkTaskDef],
        *,
        session_factory: Callable[[], Session],
    ) -> list[BenchmarkTaskResult]:
        del run, task_defs, session_factory
        raise EngineUnavailable("install the real engine")


def get_benchmark_engine(backend: str) -> BenchmarkEngine:
    """Resolve a `runner_backend` name to its `BenchmarkEngine` implementation."""
    if backend == "local_sim":
        return LocalSimBenchmarkEngine()
    if backend in ("http", "vllm"):
        return HttpBenchmarkEngine()
    if backend == "lm_eval":
        return LmEvalBenchmarkEngine()
    if backend == "vlmeval":
        return VlmEvalBenchmarkEngine()
    raise ValueError(f"Unsupported runner_backend: '{backend}'")


# --- Deterministic Simulator (local_sim) ------------------------------------


def run_local_sim(
    project_id: str,
    benchmark_run_id: str,
    target_model: str,
    eval_mode: str,
    num_fewshot: int,
    task_defs: list[BenchmarkTaskDef],
    seed: int = 42,
) -> list[BenchmarkTaskResult]:
    """Deterministic simulator computing reproducible benchmark task scores (`simulated=True`)."""
    results: list[BenchmarkTaskResult] = []
    for task in task_defs:
        key = f"{target_model}:{task.name}:{seed}:{eval_mode}:{num_fewshot}".encode()
        h = hashlib.sha256(key).hexdigest()

        score_int = int(h[:8], 16)
        primary_score = round(0.45 + (score_int % 500) / 1000.0, 4)

        metrics: dict[str, float] = {}
        for idx, m_name in enumerate(task.metrics):
            if m_name == task.primary_metric:
                metrics[m_name] = primary_score
            else:
                offset_int = int(h[8 + idx * 4 : 12 + idx * 4], 16)
                offset = ((offset_int % 80) - 40) / 1000.0
                metrics[m_name] = round(max(0.0, min(1.0, primary_score + offset)), 4)

        p50 = round(35.0 + (int(h[20:24], 16) % 120), 2)
        p95 = round(p50 + 20.0 + (int(h[24:28], 16) % 180), 2)

        results.append(
            BenchmarkTaskResult(
                project_id=project_id,
                benchmark_run_id=benchmark_run_id,
                task_name=task.name,
                modality=task.modality,
                eval_mode=eval_mode,
                num_fewshot=num_fewshot,
                sample_count=task.sample_count,
                primary_metric=task.primary_metric,
                primary_score=primary_score,
                simulated=True,
                metrics=metrics,
                latency_ms_p50=p50,
                latency_ms_p95=p95,
            )
        )
    return results


# --- HTTP / vLLM Runner (via EgressClient) ----------------------------------


def _precheck_egress(
    *,
    url: str,
    project_id: str,
    session_factory: Callable[[], Session],
) -> None:
    """Validate project egress allowlist and resolved IP before reading server credentials."""
    settings = EgressSettings()
    try:
        with session_factory() as session:
            check_url(
                url,
                project_id=project_id,
                session=session,
                purpose="benchmark.run",
                settings=settings,
            )
    except EgressError as exc:
        with session_factory() as session, session.begin():
            audit.record(
                session,
                action="egress.request",
                project_id=project_id,
                target=exc.host or best_effort_host(url),
                detail={"purpose": "benchmark.run", "reason": exc.reason},
            )
        raise


def execute_http_runner_tasks(
    run: BenchmarkRun,
    task_defs: list[BenchmarkTaskDef],
    *,
    session_factory: Callable[[], Session],
) -> list[BenchmarkTaskResult]:
    """Execute evaluation tasks against an external OpenAI-compatible or vLLM HTTP endpoint
    strictly through `matrix_server.egress.egress_client` (ADR-012)."""
    config = run.runner_config or {}
    validate_runner_config(config, backend=run.runner_backend)
    endpoint_url = str(config["endpoint_url"]).rstrip("/")
    raw_timeout = config.get("timeout_seconds")
    timeout_seconds = max(
        1.0,
        min(
            float(30.0 if raw_timeout is None else raw_timeout),
            MAX_HTTP_TIMEOUT_SECONDS,
        ),
    )

    if run.eval_mode == "loglikelihood":
        endpoint = f"{endpoint_url}/completions"
    else:
        endpoint = f"{endpoint_url}/chat/completions"

    # Validate egress policy BEFORE accessing any environment credentials (prevents env presence oracle).
    _precheck_egress(url=endpoint, project_id=run.project_id, session_factory=session_factory)

    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key_env = config.get("api_key_env")
    if api_key_env:
        secret_val = os.environ.get(api_key_env)
        if not secret_val:
            raise BenchmarkCredentialError("Benchmark credential configuration error")
        headers["Authorization"] = f"Bearer {secret_val}"

    def _run_single_task(task: BenchmarkTaskDef) -> BenchmarkTaskResult:
        client = egress_client(
            project_id=run.project_id,
            session_factory=session_factory,
            purpose="benchmark.run",
            timeout=timeout_seconds,
        )
        t0 = time.perf_counter()
        if run.eval_mode == "loglikelihood":
            payload: dict[str, Any] = {
                "model": run.target_model,
                "prompt": f"Benchmark evaluation for {task.name}",
                "max_tokens": 1,
                "logprobs": 1,
                "echo": True,
            }
        elif run.eval_mode == "multimodal":
            payload = {
                "model": run.target_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Evaluate multimodal {task.name}"},
                        ],
                    }
                ],
                "max_tokens": 128,
            }
        else:  # generate_until
            payload = {
                "model": run.target_model,
                "messages": [
                    {"role": "user", "content": f"Evaluate benchmark task {task.name}"}
                ],
                "max_tokens": 128,
            }

        response = client.post(endpoint, json=payload, headers=headers)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if not response.is_success:
            raise RuntimeError(
                f"External benchmark endpoint returned HTTP {response.status_code} for task '{task.name}'"
            )

        resp_hash = hashlib.sha256(response.content).hexdigest()
        score_int = int(resp_hash[:8], 16)
        primary_score = round(0.50 + (score_int % 450) / 1000.0, 4)

        metrics = {task.primary_metric: primary_score}
        for m in task.metrics:
            if m != task.primary_metric:
                metrics[m] = primary_score

        return BenchmarkTaskResult(
            project_id=run.project_id,
            benchmark_run_id=run.id,
            task_name=task.name,
            modality=task.modality,
            eval_mode=run.eval_mode,
            num_fewshot=run.num_fewshot,
            sample_count=min(task.sample_count, 50),
            primary_metric=task.primary_metric,
            primary_score=primary_score,
            simulated=True,
            metrics=metrics,
            latency_ms_p50=round(elapsed_ms, 2),
            latency_ms_p95=round(elapsed_ms * 1.25, 2),
        )

    if not task_defs:
        return []
    max_workers = min(HTTP_BENCHMARK_MAX_WORKERS, len(task_defs))
    if max_workers == 1:
        return [_run_single_task(task_defs[0])]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_run_single_task, task) for task in task_defs]
        try:
            return [future.result() for future in futures]
        except Exception:
            for future in futures:
                future.cancel()
            raise


# --- Lineage Recording ------------------------------------------------------


def _record_benchmark_lineage(db: Session, run: BenchmarkRun) -> None:
    """Record lineage edges: model_version / dataset -> benchmark_run in mlops_artifact_lineage."""
    sources: list[tuple[str, str, str]] = []
    if run.model_version_id:
        sources.append(("model_version", run.model_version_id, "evaluated_by"))
    if run.dataset_id:
        sources.append(("dataset", run.dataset_id, "evaluated_by"))
    if run.dataset_commit_id:
        sources.append(("dataset_commit", run.dataset_commit_id, "evaluated_by"))

    for source_type, source_id, relation in sources:
        existing = db.scalar(
            select(ArtifactLineage.id).where(
                ArtifactLineage.project_id == run.project_id,
                ArtifactLineage.source_type == source_type,
                ArtifactLineage.source_id == source_id,
                ArtifactLineage.target_type == "benchmark_run",
                ArtifactLineage.target_id == run.id,
                ArtifactLineage.relation == relation,
            )
        )
        if not existing:
            edge = ArtifactLineage(
                project_id=run.project_id,
                source_type=source_type,
                source_id=source_id,
                target_type="benchmark_run",
                target_id=run.id,
                relation=relation,
                metadata_={
                    "suite": run.suite,
                    "target_model": run.target_model,
                    "runner_backend": run.runner_backend,
                },
            )
            db.add(edge)


# --- Job Execution Function -------------------------------------------------


def run_benchmark_job(
    session_factory: sessionmaker[Session] | Session,
    benchmark_run_id: str,
) -> None:
    """Execute a benchmark run job: updates status to running, evaluates tasks, records summary metrics and lineage."""
    if isinstance(session_factory, Session):
        factory = sessionmaker(bind=session_factory.get_bind(), expire_on_commit=False)
    elif callable(session_factory):
        factory = session_factory
    else:
        raise TypeError(f"Invalid session_factory: {type(session_factory)}")

    # Transition to running
    with factory() as db, db.begin():
        run = db.get(BenchmarkRun, benchmark_run_id)
        if run is None:
            log.warning("Benchmark run %s not found", benchmark_run_id)
            return
        if run.status in ("completed", "failed", "cancelled"):
            log.info("Benchmark run %s is already finished (%s)", benchmark_run_id, run.status)
            return
        run.status = "running"
        run.started_at = utcnow()
        run.error = None

    try:
        with factory() as db:
            run = db.get(BenchmarkRun, benchmark_run_id)
            if run is None or run.status == "cancelled":
                return
            task_defs = [TASK_CATALOG[t] for t in run.tasks if t in TASK_CATALOG]
            if not task_defs:
                raise ValueError(f"No valid tasks found in catalog for benchmark run {benchmark_run_id}")
            engine = get_benchmark_engine(run.runner_backend)
            # Detach read snapshot before external I/O so DB locks aren't held during HTTP calls
            db.expunge(run)

        results = engine.run(run, task_defs, session_factory=factory)

        with factory() as db, db.begin():
            persisted_run = db.get(BenchmarkRun, benchmark_run_id)
            if persisted_run is None or persisted_run.status == "cancelled":
                return

            for r in results:
                db.add(r)
            db.flush()

            primary_scores = [r.primary_score for r in results]
            mean_score = round(sum(primary_scores) / len(primary_scores), 4) if primary_scores else 0.0
            total_samples = sum(r.sample_count for r in results)
            summary = {
                "mean_primary_score": mean_score,
                "total_tasks": len(results),
                "total_samples": total_samples,
                "tasks": {r.task_name: r.primary_score for r in results},
            }

            persisted_run.simulated = engine.simulated
            persisted_run.summary_metrics = summary
            persisted_run.status = "completed"
            persisted_run.finished_at = utcnow()

            _record_benchmark_lineage(db, persisted_run)
    except Exception as exc:
        if isinstance(exc, (EgressError, EngineUnavailable, BenchmarkCredentialError, ValueError)):
            exc.non_retryable = True
            log.warning(
                "Benchmark run %s blocked/unavailable (%s): %s",
                benchmark_run_id,
                type(exc).__name__,
                exc,
            )
        else:
            log.exception("Benchmark run %s failed", benchmark_run_id)
        with factory() as db, db.begin():
            failed_run = db.get(BenchmarkRun, benchmark_run_id)
            if failed_run and failed_run.status != "cancelled":
                failed_run.status = "failed"
                failed_run.error = str(exc)[:2000]
                failed_run.finished_at = utcnow()
        raise
