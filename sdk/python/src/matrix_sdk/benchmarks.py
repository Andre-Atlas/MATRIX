"""Benchmarks SDK client: evaluate foundational and multimodal models (LM-Eval & VLMEvalKit)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .client import _seg

if TYPE_CHECKING:
    from .client import MatrixClient

TERMINAL_STATUSES: set[str] = {"completed", "failed", "cancelled"}


class BenchmarksClient:
    """Client for matrix benchmarks catalog, runs, monitoring, and cancellation."""

    def __init__(self, client: MatrixClient) -> None:
        self._client = client

    def catalog(self, suite: str | None = None) -> dict[str, Any]:
        """Retrieve catalog of supported benchmark tasks (filtered optionally by suite)."""
        params: dict[str, Any] = {}
        if suite is not None:
            params["suite"] = suite
        return self._client._request("GET", "/api/v1/benchmarks/catalog", params=params)

    def run(
        self,
        name: str,
        suite: str,
        tasks: list[str],
        target_model: str,
        runner_backend: str = "local_sim",
        eval_mode: str = "loglikelihood",
        num_fewshot: int = 0,
        runner_config: dict[str, Any] | None = None,
        model_version_id: str | None = None,
        dataset_id: str | None = None,
        dataset_commit_id: str | None = None,
        sync_exec: bool = False,
    ) -> dict[str, Any]:
        """Create and dispatch a benchmark evaluation run."""
        body: dict[str, Any] = {
            "name": name,
            "suite": suite,
            "tasks": tasks,
            "target_model": target_model,
            "runner_backend": runner_backend,
            "eval_mode": eval_mode,
            "num_fewshot": num_fewshot,
            "runner_config": dict(runner_config or {}),
            "sync_exec": sync_exec,
        }
        if model_version_id is not None:
            body["model_version_id"] = model_version_id
        if dataset_id is not None:
            body["dataset_id"] = dataset_id
        if dataset_commit_id is not None:
            body["dataset_commit_id"] = dataset_commit_id

        params: dict[str, Any] = {}
        if sync_exec:
            params["sync_exec"] = "true"

        return self._client._request("POST", "/api/v1/benchmarks/runs", json=body, params=params)

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Get details for a benchmark run, including per-task results."""
        return self._client._request("GET", f"/api/v1/benchmarks/runs/{_seg(run_id)}")

    def list_runs(
        self,
        suite: str | None = None,
        status: str | None = None,
        model_version_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List benchmark runs with optional filtering and pagination."""
        params: dict[str, Any] = {"limit": limit}
        if suite is not None:
            params["suite"] = suite
        if status is not None:
            params["status"] = status
        if model_version_id is not None:
            params["model_version_id"] = model_version_id
        if cursor is not None:
            params["cursor"] = cursor
        return self._client._request("GET", "/api/v1/benchmarks/runs", params=params)

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Cancel a queued or running benchmark run."""
        return self._client._request("POST", f"/api/v1/benchmarks/runs/{_seg(run_id)}/cancel")

    def wait(
        self,
        run_id: str,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Wait synchronously for a benchmark run to reach a terminal status."""
        deadline = time.monotonic() + timeout
        while True:
            run = self.get_run(run_id)
            current_status = run.get("status")
            if current_status in TERMINAL_STATUSES:
                return run
            now = time.monotonic()
            if now + poll_interval > deadline:
                raise TimeoutError(
                    f"Benchmark run {run_id} timed out after {timeout}s (last status: {current_status})"
                )
            time.sleep(poll_interval)
