"""Inspect AI Sandboxes SDK client: run evaluation agents with hardened sandboxes."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .client import _seg

if TYPE_CHECKING:
    from .client import MatrixClient

TERMINAL_STATUSES: set[str] = {"completed", "failed", "cancelled"}


class SandboxesClient:
    """Client for Inspect AI agent sandbox runs, transcripts, and cancellation."""

    def __init__(self, client: MatrixClient) -> None:
        self._client = client

    def run(
        self,
        name: str,
        solver_config: dict[str, Any],
        scorer_config: dict[str, Any],
        runner_backend: str = "local_sandbox",
        dataset_id: str | None = None,
        dataset_commit_id: str | None = None,
        sandbox_policy: dict[str, Any] | None = None,
        sync_exec: bool = False,
    ) -> dict[str, Any]:
        """Create and enqueue an Inspect AI Sandbox evaluation run."""
        body: dict[str, Any] = {
            "name": name,
            "runner_backend": runner_backend,
            "solver_config": solver_config,
            "scorer_config": scorer_config,
            "sandbox_policy": dict(sandbox_policy or {}),
            "sync_exec": sync_exec,
        }
        if dataset_id is not None:
            body["dataset_id"] = dataset_id
        if dataset_commit_id is not None:
            body["dataset_commit_id"] = dataset_commit_id

        return self._client._request("POST", "/api/v1/sandboxes/runs", json=body)

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Get detailed information about a sandbox run."""
        return self._client._request("GET", f"/api/v1/sandboxes/runs/{_seg(run_id)}")

    def list_runs(
        self,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List sandbox runs with keyset pagination."""
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if cursor is not None:
            params["cursor"] = cursor
        return self._client._request("GET", "/api/v1/sandboxes/runs", params=params)

    def get_transcripts(
        self,
        run_id: str,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List task transcripts for a sandbox run."""
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if cursor is not None:
            params["cursor"] = cursor
        res = self._client._request(
            "GET",
            f"/api/v1/sandboxes/runs/{_seg(run_id)}/transcripts",
            params=params,
        )
        if isinstance(res, list):
            return {"items": res}
        return res

    def get_transcript(self, run_id: str, task_key: str) -> dict[str, Any]:
        """Get full transcript details including steps and verified artifacts for a task."""
        encoded_task_key = quote(str(task_key), safe="/")
        return self._client._request(
            "GET",
            f"/api/v1/sandboxes/runs/{_seg(run_id)}/transcripts/{encoded_task_key}",
        )

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Cancel an ongoing or queued sandbox run."""
        return self._client._request("POST", f"/api/v1/sandboxes/runs/{_seg(run_id)}/cancel")

    def wait(
        self,
        run_id: str,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Wait synchronously for a sandbox run to reach a terminal status."""
        deadline = time.monotonic() + timeout
        while True:
            run = self.get_run(run_id)
            current_status = run.get("status")
            if current_status in TERMINAL_STATUSES:
                return run
            now = time.monotonic()
            if now + poll_interval > deadline:
                raise TimeoutError(
                    f"Sandbox run {run_id} timed out after {timeout}s (last status: {current_status})"
                )
            time.sleep(poll_interval)
