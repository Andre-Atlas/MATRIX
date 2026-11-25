"""End-to-end in-memory tests for matrix_sdk.sweep(), matrix_sdk.agent(), run.config, and run.should_stop()."""

from __future__ import annotations

import json
import threading
import warnings
from typing import Any

import httpx

import matrix_sdk as mx
from matrix_sdk import tracking


class FakeSweepServer:
    """In-memory G2 sweeps + G1 runs server for SDK e2e testing (with ASHA-like pruning)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sweeps: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.metrics: dict[str, list[dict[str, Any]]] = {}
        self.reports: list[tuple[str, str, int, float]] = []
        self._grid = [{"lr": 0.001}, {"lr": 0.01}, {"lr": 0.1}, {"lr": 1.0}]
        self._next_idx: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        raw = request.read()
        body = json.loads(raw) if raw else {}
        m, path = request.method, request.url.path

        with self.lock:
            if m == "POST" and path == "/api/v1/sweeps":
                sid = f"sw_{len(self.sweeps) + 1}"
                sw = {
                    "id": sid,
                    "name": body["name"],
                    "status": "running",
                    "config": body["config"],
                    "run_count": 0,
                    "best_run_id": None,
                    "best_value": None,
                }
                self.sweeps[sid] = sw
                self._next_idx[sid] = 0
                return httpx.Response(201, json=sw)

            if m == "GET" and path.startswith("/api/v1/sweeps/"):
                sid = path.split("/")[4]
                sw = self.sweeps.get(sid)
                if sw is None:
                    return httpx.Response(404, json={"detail": "not found"})
                return httpx.Response(200, json={"sweep": sw, "runs": [], "importance": None})

            if m == "POST" and path.startswith("/api/v1/sweeps/") and path.endswith("/next"):
                sid = path.split("/")[4]
                sw = self.sweeps[sid]
                idx = self._next_idx[sid]
                if idx >= len(self._grid):
                    sw["status"] = "finished"
                    return httpx.Response(204)
                self._next_idx[sid] = idx + 1
                params = dict(self._grid[idx])
                rid = f"run_{idx + 1}"
                run = {
                    "id": rid,
                    "name": f"{sw['name']}-{idx + 1}",
                    "status": "running",
                    "params": params,
                    "sweep_id": sid,
                    "agent_id": body.get("agent_id"),
                    "error": None,
                }
                self.runs[rid] = run
                self.metrics[rid] = []
                return httpx.Response(200, json={"run": run, "params": params})

            if m == "POST" and path.startswith("/api/v1/sweeps/") and path.endswith("/report"):
                parts = path.split("/")
                sid, rid = parts[4], parts[6]
                step, val = int(body["step"]), float(body["value"])
                self.reports.append((sid, rid, step, val))
                # Prune any trial with loss > 0.5 at step >= 5
                action = "stop" if step >= 5 and val > 0.5 else "continue"
                return httpx.Response(200, json={"action": action})

            if m == "POST" and path.startswith("/api/v1/runs/") and path.endswith("/metrics"):
                rid = path.split("/")[4]
                self.metrics.setdefault(rid, []).extend(body.get("points", []))
                return httpx.Response(202, json={"accepted": len(body.get("points", [])), "rejected": []})

            if m == "POST" and path.startswith("/api/v1/runs/") and path.endswith("/heartbeat"):
                return httpx.Response(204)

            if m == "PATCH" and path.startswith("/api/v1/runs/"):
                rid = path.split("/")[4]
                run = self.runs[rid]
                run.update(body)
                return httpx.Response(200, json=run)

        return httpx.Response(404, json={"detail": f"no route {m} {path}"})


def test_sdk_sweep_agent_pruning_and_error_recovery() -> None:
    srv = FakeSweepServer()
    client = mx.MatrixClient("mx_test", "http://sweep.test", transport=httpx.MockTransport(srv.handler))
    prev_default = mx.client._default_client
    mx.client._default_client = client
    try:
        sweep_id = mx.sweep(
            config={
                "method": "grid",
                "metric": {"key": "loss", "goal": "minimize"},
                "parameters": {"lr": {"type": "choice", "values": [0.001, 0.01, 0.1, 1.0]}},
                "early_stop": {"type": "asha", "min_iter": 5, "eta": 3},
            },
            name="sdk-e2e-sweep",
        )
        assert sweep_id == "sw_1"

        seen_lrs: list[float] = []
        pruned_lrs: list[float] = []

        def train() -> None:
            run = mx.start_run(system_metrics=False, capture_git=False, capture_env=False, backoff_base=0.01)
            lr = run.config["lr"]
            seen_lrs.append(lr)
            if lr == 0.01:
                raise RuntimeError("diverged NaN")
            for step in range(1, 10):
                loss = 0.9 if lr >= 0.1 else 0.1
                run.log({"loss": loss}, step=step)
                if run.should_stop():
                    pruned_lrs.append(lr)
                    break

        executed = mx.agent(sweep_id, function=train, count=4, agent_id="agent-alpha")
        assert executed == 4
        assert seen_lrs == [0.001, 0.01, 0.1, 1.0]
        assert pruned_lrs == [0.1, 1.0]

        # Run 1 (lr=0.001): finished
        assert srv.runs["run_1"]["status"] == "finished"
        assert srv.runs["run_1"]["agent_id"] == "agent-alpha"
        # Run 2 (lr=0.01): crashed -> marked failed, agent kept going
        assert srv.runs["run_2"]["status"] == "failed"
        assert "RuntimeError: diverged NaN" in (srv.runs["run_2"]["error"] or "")
        # Runs 3 & 4 (lr=0.1, 1.0): pruned at step 5 -> marked killed
        assert srv.runs["run_3"]["status"] == "killed"
        assert srv.runs["run_4"]["status"] == "killed"
    finally:
        mx.client._default_client = prev_default
        tracking._current = None
        client._http.close()


def test_deprecated_mlops_sweep_wrappers_emit_deprecation_warning() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/sweeps":
            return httpx.Response(201, json={"id": "sw_old", "name": "old"})
        if request.method == "GET" and request.url.path == "/api/v1/sweeps/sw_old":
            return httpx.Response(200, json={"sweep": {"id": "sw_old", "config": {"metric": {"key": "eval/loss"}}}})
        if request.method == "POST" and request.url.path == "/api/v1/sweeps/sw_old/next":
            return httpx.Response(204)
        return httpx.Response(404)

    client = mx.MatrixClient("mx_test", "http://sweep.test", transport=httpx.MockTransport(handler))
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.mlops.create_sweep("old", search_space={"lr": {"min": 1e-4, "max": 1e-2}})
            client.mlops.run_sweep_agent("sw_old", lambda r, hp: None)
        dep_msgs = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
        assert any("create_sweep" in m for m in dep_msgs)
        assert any("run_sweep_agent" in m for m in dep_msgs)
    finally:
        client._http.close()
