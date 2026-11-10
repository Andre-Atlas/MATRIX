import hashlib
import json
import threading

import httpx
import pytest

import matrix_sdk as mx
from matrix_sdk import tracking


class FakeServer:
    """In-memory runs API (contract G1) + direct artifact upload (contract G0 section 2)."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict, object]] = []  # method, path, headers, body
        self.lock = threading.Lock()
        self.metrics_failures = 0  # next N metrics POSTs answer 503
        self.params: dict = {}
        self.fail_all = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        raw = request.read()
        path = request.url.path
        body: object = raw
        if request.headers.get("content-type", "").startswith("application/json") and raw:
            body = json.loads(raw)
        with self.lock:
            self.requests.append((request.method, path, dict(request.headers), body))
        if self.fail_all:
            raise httpx.ConnectError("connection refused", request=request)
        m = request.method
        if m == "POST" and path == "/api/v1/runs":
            return httpx.Response(201, json={"id": "r1", "name": body.get("name") or "brave-otter-1",
                                             "status": "running", "params": body.get("params", {})})
        if m == "POST" and path == "/api/v1/runs/r1/metrics":
            with self.lock:
                if self.metrics_failures > 0:
                    self.metrics_failures -= 1
                    return httpx.Response(503, json={"detail": "busy"})
            return httpx.Response(202, json={"accepted": len(body["points"]), "rejected": []})
        if m == "POST" and path == "/api/v1/runs/r1/params":
            conflicts = [k for k, v in body["params"].items() if k in self.params and self.params[k] != v]
            if conflicts:
                return httpx.Response(409, json={"conflicts": conflicts})
            self.params.update(body["params"])
            return httpx.Response(200, json={"params": self.params})
        if m == "PATCH" and path == "/api/v1/runs/r1":
            return httpx.Response(200, json={"id": "r1", **body})
        if m == "POST" and path == "/api/v1/runs/r1/heartbeat":
            return httpx.Response(204)
        if m == "POST" and path == "/api/v1/artifacts":
            sha = request.headers.get("x-sha256")
            if sha and sha != hashlib.sha256(raw).hexdigest():
                return httpx.Response(422, json={"detail": "sha mismatch"})
            return httpx.Response(201, json={"id": "a1", "name": request.headers["x-filename"], "size": len(raw),
                                             "sha256": hashlib.sha256(raw).hexdigest(), "verified": True})
        if m == "POST" and path in ("/api/v1/runs/r1/artifacts", "/api/v1/runs/r1/reports"):
            return httpx.Response(201, json={"id": "x", **body})
        return httpx.Response(404, json={"detail": f"no route {m} {path}"})

    def calls(self, method: str, path: str) -> list:
        with self.lock:
            return [r for r in self.requests if r[0] == method and r[1] == path]

    def points(self) -> list[dict]:
        return [p for *_, body in self.calls("POST", "/api/v1/runs/r1/metrics") for p in body["points"]]


@pytest.fixture
def server():
    return FakeServer()


@pytest.fixture
def client(server):
    c = mx.MatrixClient("mx_k", "http://m.test", transport=httpx.MockTransport(server.handler))
    yield c
    c._http.close()


@pytest.fixture
def make_run(client):
    runs = []

    def make(**kwargs):
        kwargs.setdefault("system_metrics", False)
        kwargs.setdefault("capture_git", False)
        kwargs.setdefault("capture_env", False)
        kwargs.setdefault("backoff_base", 0.01)
        run = client.start_run(**kwargs)
        runs.append(run)
        return run

    yield make
    for run in runs:
        run.finish(timeout=2)
    tracking._current = None
