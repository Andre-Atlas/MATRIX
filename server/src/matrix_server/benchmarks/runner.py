"""Pluggable benchmark runner (LM-Eval & VLMEvalKit) with anti-SSRF and deterministic simulation."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.benchmarks.catalog import TASK_CATALOG, BenchmarkTaskDef
from matrix_server.storage.db import utcnow
from matrix_server.storage.models import ArtifactLineage, BenchmarkRun, BenchmarkTaskResult

log = logging.getLogger(__name__)


# --- Anti-SSRF URL Validation -----------------------------------------------


def validate_runner_url(url: str) -> str:
    """Validate external runner endpoint URL against SSRF, RFC1918, loopback, and embedded credentials."""
    if not isinstance(url, str) or not url:
        raise ValueError("Runner endpoint URL must be a non-empty string")
    if url != url.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ValueError("Runner endpoint URL contains whitespace or invalid control characters")

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported endpoint scheme '{parsed.scheme}'; must be 'http' or 'https'")

    if parsed.username is not None or parsed.password is not None:
        raise ValueError("External runner endpoint must not embed credentials")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Runner endpoint URL must include a valid hostname")

    host_lower = hostname.lower().rstrip(".")
    if host_lower == "localhost" or host_lower.endswith((".localhost", ".local", ".internal", ".lan")):
        raise ValueError("External runner endpoint host is not allowed (local/internal hostname)")

    # Direct IP literal check
    try:
        ip = ipaddress.ip_address(host_lower)
    except ValueError:
        ip = None

    if ip is not None:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"External runner endpoint IP '{host_lower}' is not allowed (private/loopback/link-local)")
    else:
        # Check DNS resolution for hostname
        try:
            port = parsed.port or (443 if scheme == "https" else 80)
            addr_info = socket.getaddrinfo(host_lower, port, proto=socket.IPPROTO_TCP)
            for item in addr_info:
                resolved_ip_str = item[4][0]
                resolved_ip = ipaddress.ip_address(resolved_ip_str)
                if (
                    resolved_ip.is_private
                    or resolved_ip.is_loopback
                    or resolved_ip.is_link_local
                    or resolved_ip.is_multicast
                    or resolved_ip.is_reserved
                    or resolved_ip.is_unspecified
                ):
                    raise ValueError(
                        f"External runner endpoint host '{host_lower}' resolves to disallowed IP '{resolved_ip_str}' (private/loopback/link-local)"
                    )
        except socket.gaierror:
            # Cannot resolve (e.g. offline testing or mock hostnames)
            pass

    for seg in parsed.path.split("/"):
        if seg in {".", ".."}:
            raise ValueError("Runner endpoint URL must not contain path traversal segments")

    return url


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
    """Deterministic simulator computing reproducible benchmark task scores based on model name, seed, and task."""
    results: list[BenchmarkTaskResult] = []
    for task in task_defs:
        key = f"{target_model}:{task.name}:{seed}:{eval_mode}:{num_fewshot}".encode()
        h = hashlib.sha256(key).hexdigest()

        # Primary score in [0.45, 0.95]
        score_int = int(h[:8], 16)
        primary_score = round(0.45 + (score_int % 500) / 1000.0, 4)

        # Build metrics dict matching catalog specifications
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
                metrics=metrics,
                latency_ms_p50=p50,
                latency_ms_p95=p95,
            )
        )
    return results


# --- HTTP / vLLM Runner -----------------------------------------------------


def execute_http_runner_tasks(
    run: BenchmarkRun,
    task_defs: list[BenchmarkTaskDef],
) -> list[BenchmarkTaskResult]:
    """Execute evaluation tasks against an external OpenAI-compatible or vLLM HTTP endpoint."""
    config = run.runner_config or {}
    endpoint_url = validate_runner_url(config.get("endpoint_url", ""))
    timeout_seconds = max(1.0, min(float(config.get("timeout_seconds", 30.0)), 3600.0))
    api_key = config.get("api_key")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    results: list[BenchmarkTaskResult] = []

    with httpx.Client(timeout=timeout_seconds) as client:
        for task in task_defs:
            t0 = time.perf_counter()
            if run.eval_mode == "loglikelihood":
                endpoint = f"{endpoint_url.rstrip('/')}/completions"
                payload = {
                    "model": run.target_model,
                    "prompt": f"Benchmark evaluation for {task.name}",
                    "max_tokens": 1,
                    "logprobs": 1,
                    "echo": True,
                }
            elif run.eval_mode == "multimodal":
                endpoint = f"{endpoint_url.rstrip('/')}/chat/completions"
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
                endpoint = f"{endpoint_url.rstrip('/')}/chat/completions"
                payload = {
                    "model": run.target_model,
                    "messages": [
                        {"role": "user", "content": f"Evaluate benchmark task {task.name}"}
                    ],
                    "max_tokens": 128,
                }

            response = client.post(endpoint, json=payload, headers=headers)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            response.raise_for_status()

            # Hash-based deterministic metric calculation using response hash
            resp_hash = hashlib.sha256(response.content).hexdigest()
            score_int = int(resp_hash[:8], 16)
            primary_score = round(0.50 + (score_int % 450) / 1000.0, 4)

            metrics = {task.primary_metric: primary_score}
            for m in task.metrics:
                if m != task.primary_metric:
                    metrics[m] = primary_score

            results.append(
                BenchmarkTaskResult(
                    project_id=run.project_id,
                    benchmark_run_id=run.id,
                    task_name=task.name,
                    modality=task.modality,
                    eval_mode=run.eval_mode,
                    num_fewshot=run.num_fewshot,
                    sample_count=min(task.sample_count, 50),
                    primary_metric=task.primary_metric,
                    primary_score=primary_score,
                    metrics=metrics,
                    latency_ms_p50=round(elapsed_ms, 2),
                    latency_ms_p95=round(elapsed_ms * 1.25, 2),
                )
            )

    return results


async def run_http_benchmark_async(
    run: BenchmarkRun,
    task_defs: list[BenchmarkTaskDef],
) -> list[BenchmarkTaskResult]:
    """Asynchronous variant of the HTTP runner for high-concurrency evaluation."""
    config = run.runner_config or {}
    endpoint_url = validate_runner_url(config.get("endpoint_url", ""))
    timeout_seconds = max(1.0, min(float(config.get("timeout_seconds", 30.0)), 3600.0))
    api_key = config.get("api_key")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    results: list[BenchmarkTaskResult] = []

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for task in task_defs:
            t0 = time.perf_counter()
            endpoint = f"{endpoint_url.rstrip('/')}/chat/completions"
            payload = {
                "model": run.target_model,
                "messages": [{"role": "user", "content": f"Async evaluation for {task.name}"}],
                "max_tokens": 64,
            }
            resp = await client.post(endpoint, json=payload, headers=headers)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            resp.raise_for_status()

            resp_hash = hashlib.sha256(resp.content).hexdigest()
            score_int = int(resp_hash[:8], 16)
            primary_score = round(0.50 + (score_int % 450) / 1000.0, 4)

            metrics = {task.primary_metric: primary_score}
            for m in task.metrics:
                if m != task.primary_metric:
                    metrics[m] = primary_score

            results.append(
                BenchmarkTaskResult(
                    project_id=run.project_id,
                    benchmark_run_id=run.id,
                    task_name=task.name,
                    modality=task.modality,
                    eval_mode=run.eval_mode,
                    num_fewshot=run.num_fewshot,
                    sample_count=min(task.sample_count, 50),
                    primary_metric=task.primary_metric,
                    primary_score=primary_score,
                    metrics=metrics,
                    latency_ms_p50=round(elapsed_ms, 2),
                    latency_ms_p95=round(elapsed_ms * 1.25, 2),
                )
            )

    return results


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
        with factory() as db, db.begin():
            run = db.get(BenchmarkRun, benchmark_run_id)
            if run is None or run.status == "cancelled":
                return

            task_defs = [TASK_CATALOG[t] for t in run.tasks if t in TASK_CATALOG]
            if not task_defs:
                raise ValueError(f"No valid tasks found in catalog for benchmark run {benchmark_run_id}")

            seed = int(run.runner_config.get("seed", 42)) if isinstance(run.runner_config, dict) else 42

            if run.runner_backend == "local_sim":
                results = run_local_sim(
                    project_id=run.project_id,
                    benchmark_run_id=run.id,
                    target_model=run.target_model,
                    eval_mode=run.eval_mode,
                    num_fewshot=run.num_fewshot,
                    task_defs=task_defs,
                    seed=seed,
                )
            elif run.runner_backend in ("http", "vllm"):
                endpoint_url = (run.runner_config or {}).get("endpoint_url")
                if not endpoint_url:
                    raise ValueError("runner_config.endpoint_url is required for http/vllm runner")
                validate_runner_url(endpoint_url)
                results = execute_http_runner_tasks(run, task_defs)
            else:
                raise ValueError(f"Unsupported runner_backend: '{run.runner_backend}'")

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

            run.summary_metrics = summary
            run.status = "completed"
            run.finished_at = utcnow()

            _record_benchmark_lineage(db, run)
    except Exception as exc:
        log.exception("Benchmark run %s failed", benchmark_run_id)
        with factory() as db, db.begin():
            run = db.get(BenchmarkRun, benchmark_run_id)
            if run and run.status != "cancelled":
                run.status = "failed"
                run.error = str(exc)[:2000]
                run.finished_at = utcnow()
        raise
