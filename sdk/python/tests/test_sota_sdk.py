"""Comprehensive tests for SOTA completion SDK: Benchmarks, Sandboxes, and Datasets versioning."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from matrix_sdk import (
    BenchmarksClient,
    DatasetsClient,
    MatrixClient,
    SandboxesClient,
)


def _make_client(handler: Any) -> MatrixClient:
    return MatrixClient(
        api_key="test-key",
        endpoint="https://matrix.local",
        transport=httpx.MockTransport(handler),
    )


# ============================================================================
# BenchmarksClient Tests
# ============================================================================


def test_benchmarks_catalog() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        parsed = urlparse(str(request.url))
        qs = parse_qs(parsed.query)
        suite = qs.get("suite", [None])[0]
        if suite == "lm_eval":
            return httpx.Response(200, json={"tasks": [{"suite": "lm_eval", "name": "gsm8k"}]})
        return httpx.Response(200, json={"tasks": [{"name": "gsm8k"}, {"name": "mmbench"}]})

    client = _make_client(handler)
    assert isinstance(client.benchmarks, BenchmarksClient)

    # Without suite filter
    res_all = client.benchmarks.catalog()
    assert len(res_all["tasks"]) == 2
    assert calls[0].url.path == "/api/v1/benchmarks/catalog"
    assert calls[0].url.query.decode() == ""

    # With suite filter
    res_suite = client.benchmarks.catalog(suite="lm_eval")
    assert len(res_suite["tasks"]) == 1
    assert calls[1].url.query.decode() == "suite=lm_eval"


def test_benchmarks_run_creation() -> None:
    recorded_body: dict[str, Any] = {}
    recorded_params: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal recorded_body, recorded_params
        recorded_body = json.loads(request.content.decode())
        parsed = urlparse(str(request.url))
        recorded_params = parse_qs(parsed.query)
        return httpx.Response(
            201,
            json={
                "id": "bench_run_01",
                "name": recorded_body["name"],
                "status": "completed" if recorded_body.get("sync_exec") else "queued",
                "tasks": recorded_body["tasks"],
            },
        )

    client = _make_client(handler)

    # Asynchronous run dispatch
    run_async = client.benchmarks.run(
        name="llama3-eval",
        suite="lm_eval",
        tasks=["gsm8k", "mmlu"],
        target_model="meta-llama/Llama-3-8B",
        runner_backend="vllm",
        eval_mode="generate_until",
        num_fewshot=5,
        runner_config={"endpoint_url": "http://vllm.internal:8000/v1"},
        model_version_id="mv_123",
        dataset_id="ds_456",
        dataset_commit_id="commit_789",
        sync_exec=False,
    )
    assert run_async["id"] == "bench_run_01"
    assert run_async["status"] == "queued"
    assert recorded_body["name"] == "llama3-eval"
    assert recorded_body["suite"] == "lm_eval"
    assert recorded_body["tasks"] == ["gsm8k", "mmlu"]
    assert recorded_body["runner_backend"] == "vllm"
    assert recorded_body["eval_mode"] == "generate_until"
    assert recorded_body["num_fewshot"] == 5
    assert recorded_body["runner_config"]["endpoint_url"] == "http://vllm.internal:8000/v1"
    assert recorded_body["model_version_id"] == "mv_123"
    assert recorded_body["dataset_id"] == "ds_456"
    assert recorded_body["dataset_commit_id"] == "commit_789"
    assert "sync_exec" not in recorded_params

    # Synchronous run dispatch
    run_sync = client.benchmarks.run(
        name="sync-eval",
        suite="lm_eval",
        tasks=["arc_easy"],
        target_model="test-model",
        sync_exec=True,
    )
    assert run_sync["status"] == "completed"
    assert recorded_params.get("sync_exec") == ["true"]


def test_benchmarks_get_list_cancel() -> None:
    calls: list[tuple[str, str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        calls.append((request.method, parsed.path, parse_qs(parsed.query)))
        if request.method == "GET" and parsed.path == "/api/v1/benchmarks/runs/bench_1":
            return httpx.Response(200, json={"id": "bench_1", "status": "running"})
        if request.method == "GET" and parsed.path == "/api/v1/benchmarks/runs":
            return httpx.Response(200, json={"runs": [{"id": "bench_1"}]})
        if request.method == "POST" and parsed.path == "/api/v1/benchmarks/runs/bench_1/cancel":
            return httpx.Response(200, json={"id": "bench_1", "status": "cancelled"})
        raise AssertionError(f"Unexpected: {request.method} {request.url}")

    client = _make_client(handler)

    run = client.benchmarks.get_run("bench_1")
    assert run["id"] == "bench_1"
    assert calls[-1][0:2] == ("GET", "/api/v1/benchmarks/runs/bench_1")

    runs = client.benchmarks.list_runs(
        suite="lm_eval",
        status="completed",
        model_version_id="mv_1",
        limit=25,
        cursor="cur_123",
    )
    assert len(runs["runs"]) == 1
    assert calls[-1][0:2] == ("GET", "/api/v1/benchmarks/runs")
    qs = calls[-1][2]
    assert qs["suite"] == ["lm_eval"]
    assert qs["status"] == ["completed"]
    assert qs["model_version_id"] == ["mv_1"]
    assert qs["limit"] == ["25"]
    assert qs["cursor"] == ["cur_123"]

    cancelled = client.benchmarks.cancel("bench_1")
    assert cancelled["status"] == "cancelled"
    assert calls[-1][0:2] == ("POST", "/api/v1/benchmarks/runs/bench_1/cancel")


def test_benchmarks_wait_completion_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        poll_count += 1
        status = "running" if poll_count < 3 else "completed"
        return httpx.Response(200, json={"id": "bench_wait", "status": status})

    client = _make_client(handler)
    monkeypatch.setattr("time.sleep", lambda _: None)

    # Wait successfully
    completed = client.benchmarks.wait("bench_wait", poll_interval=0.01, timeout=10.0)
    assert completed["status"] == "completed"
    assert poll_count == 3

    # Wait timeout
    def hang_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "bench_hang", "status": "running"})

    client_hang = _make_client(hang_handler)
    clock = [0.0]

    def fake_monotonic() -> float:
        clock[0] += 5.0
        return clock[0]

    monkeypatch.setattr("time.monotonic", fake_monotonic)
    with pytest.raises(TimeoutError, match="timed out after 10.0s"):
        client_hang.benchmarks.wait("bench_hang", poll_interval=1.0, timeout=10.0)


# ============================================================================
# SandboxesClient Tests
# ============================================================================


def test_sandboxes_run_creation() -> None:
    recorded_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal recorded_body
        recorded_body = json.loads(request.content.decode())
        return httpx.Response(
            201,
            json={
                "id": "sbx_run_1",
                "name": recorded_body["name"],
                "status": "completed" if recorded_body.get("sync_exec") else "queued",
                "pass_rate": 1.0 if recorded_body.get("sync_exec") else None,
            },
        )

    client = _make_client(handler)
    assert isinstance(client.sandboxes, SandboxesClient)

    run = client.sandboxes.run(
        name="react-sandbox-eval",
        solver_config={"name": "react", "max_steps": 10},
        scorer_config={"unit_test": {"test_cmd": "pytest -q"}},
        runner_backend="docker",
        dataset_id="ds_code",
        dataset_commit_id="commit_head",
        sandbox_policy={"network_mode": "none", "timeout_seconds": 60},
        sync_exec=True,
    )
    assert run["id"] == "sbx_run_1"
    assert run["status"] == "completed"
    assert recorded_body["name"] == "react-sandbox-eval"
    assert recorded_body["runner_backend"] == "docker"
    assert recorded_body["solver_config"] == {"name": "react", "max_steps": 10}
    assert recorded_body["scorer_config"] == {"unit_test": {"test_cmd": "pytest -q"}}
    assert recorded_body["dataset_id"] == "ds_code"
    assert recorded_body["dataset_commit_id"] == "commit_head"
    assert recorded_body["sandbox_policy"]["network_mode"] == "none"
    assert recorded_body["sync_exec"] is True


def test_sandboxes_get_list_cancel() -> None:
    calls: list[tuple[str, str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        calls.append((request.method, parsed.path, parse_qs(parsed.query)))
        if request.method == "GET" and parsed.path == "/api/v1/sandboxes/runs/sbx_1":
            return httpx.Response(200, json={"id": "sbx_1", "status": "running"})
        if request.method == "GET" and parsed.path == "/api/v1/sandboxes/runs":
            return httpx.Response(200, json={"items": [{"id": "sbx_1"}], "next_cursor": None})
        if request.method == "POST" and parsed.path == "/api/v1/sandboxes/runs/sbx_1/cancel":
            return httpx.Response(200, json={"id": "sbx_1", "status": "cancelled"})
        raise AssertionError(f"Unexpected: {request.method} {request.url}")

    client = _make_client(handler)

    run = client.sandboxes.get_run("sbx_1")
    assert run["id"] == "sbx_1"
    assert calls[-1][0:2] == ("GET", "/api/v1/sandboxes/runs/sbx_1")

    runs = client.sandboxes.list_runs(status="running", limit=10, cursor="cur_sbx")
    assert len(runs["items"]) == 1
    qs = calls[-1][2]
    assert qs["status"] == ["running"]
    assert qs["limit"] == ["10"]
    assert qs["cursor"] == ["cur_sbx"]

    cancelled = client.sandboxes.cancel("sbx_1")
    assert cancelled["status"] == "cancelled"


def test_sandboxes_transcripts() -> None:
    calls: list[tuple[str, str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        calls.append((request.method, parsed.path, parse_qs(parsed.query)))
        if parsed.path == "/api/v1/sandboxes/runs/sbx_1/transcripts":
            return httpx.Response(
                200,
                json=[{"id": "t1", "task_key": "task_math", "status": "passed", "score": 1.0}],
            )
        if parsed.path == "/api/v1/sandboxes/runs/sbx_1/transcripts/task_math":
            return httpx.Response(
                200,
                json={
                    "id": "t1",
                    "task_key": "task_math",
                    "steps": [{"tool_name": "bash", "stdout": "42\n"}],
                    "verified_artifacts": [{"path": "solution.py", "sha256": "a" * 64}],
                },
            )
        raise AssertionError(f"Unexpected: {request.method} {request.url}")

    client = _make_client(handler)

    # List transcripts wraps server list response into dict with items
    transcripts = client.sandboxes.get_transcripts("sbx_1", status="passed", limit=20, cursor="c_tx")
    assert "items" in transcripts
    assert len(transcripts["items"]) == 1
    assert transcripts["items"][0]["task_key"] == "task_math"
    qs = calls[-1][2]
    assert qs["status"] == ["passed"]
    assert qs["limit"] == ["20"]
    assert qs["cursor"] == ["c_tx"]

    # Single transcript detail
    detail = client.sandboxes.get_transcript("sbx_1", "task_math")
    assert detail["task_key"] == "task_math"
    assert len(detail["steps"]) == 1
    assert detail["verified_artifacts"][0]["path"] == "solution.py"


def test_sandboxes_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    poll_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        poll_count += 1
        return httpx.Response(
            200,
            json={"id": "sbx_wait", "status": "completed" if poll_count >= 2 else "running"},
        )

    client = _make_client(handler)
    monkeypatch.setattr("time.sleep", lambda _: None)

    res = client.sandboxes.wait("sbx_wait", poll_interval=0.01, timeout=5.0)
    assert res["status"] == "completed"


# ============================================================================
# DatasetsClient Tests (Multimodal & Zero-Copy Versioning)
# ============================================================================


def test_datasets_crud_and_multimodal_add_item() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        body = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, parsed.path, body))
        if request.method == "POST" and parsed.path == "/api/v1/datasets":
            return httpx.Response(201, json={"id": "ds_1", "name": body["name"], "item_count": 0})
        if request.method == "GET" and parsed.path == "/api/v1/datasets":
            return httpx.Response(200, json={"items": [{"id": "ds_1", "name": "vision-qa"}]})
        if request.method == "GET" and parsed.path == "/api/v1/datasets/ds_1":
            return httpx.Response(200, json={"dataset": {"id": "ds_1"}, "items": []})
        if request.method == "GET" and parsed.path == "/api/v1/datasets/ds_1/items":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if request.method == "POST" and parsed.path == "/api/v1/datasets/ds_1/items":
            return httpx.Response(
                201,
                json={
                    "id": "item_1",
                    "dataset_id": "ds_1",
                    "input": body["input"],
                    "media": body.get("media", []),
                },
            )
        raise AssertionError(f"Unexpected: {request.method} {request.url}")

    client = _make_client(handler)
    assert isinstance(client.datasets, DatasetsClient)

    # Create dataset
    ds = client.datasets.create("vision-qa", description="Multimodal golden test set")
    assert ds["id"] == "ds_1"
    assert calls[-1][0:2] == ("POST", "/api/v1/datasets")
    assert calls[-1][2]["description"] == "Multimodal golden test set"

    # List & get
    assert len(client.datasets.list()["items"]) == 1
    assert client.datasets.get("ds_1")["dataset"]["id"] == "ds_1"
    assert client.datasets.list_items("ds_1")["items"] == []

    # Add multimodal item via DatasetsClient
    media = [
        {"kind": "image", "url": "https://example.com/chart.png", "mime": "image/png", "width": 800},
        {"kind": "bbox", "boxes": [{"x": 10, "y": 20, "w": 100, "h": 50, "label": "header"}]},
    ]
    item = client.datasets.add_item(
        dataset_id="ds_1",
        input={"prompt": "Extract header text from chart"},
        expected_output="Quarterly Growth",
        context=["context line 1"],
        media=media,
        metadata={"domain": "finance"},
    )
    assert item["id"] == "item_1"
    assert len(item["media"]) == 2
    assert calls[-1][2]["media"] == media
    assert calls[-1][2]["metadata"] == {"domain": "finance"}

    # Add multimodal item via client.create_dataset_item (MatrixClient direct method)
    item2 = client.create_dataset_item(
        dataset_id="ds_1",
        input={"prompt": "Direct method test"},
        media=media,
    )
    assert item2["id"] == "item_1"
    assert calls[-1][2]["media"] == media


def test_datasets_branching_and_versioning() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        body = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, parsed.path, body, parse_qs(parsed.query)))

        if request.method == "POST" and parsed.path == "/api/v1/datasets/ds_v/branches":
            return httpx.Response(
                201,
                json={"id": "br_1", "dataset_id": "ds_v", "name": body["name"], "head_commit_id": "c_0"},
            )
        if request.method == "GET" and parsed.path == "/api/v1/datasets/ds_v/branches":
            return httpx.Response(200, json={"items": [{"id": "br_1", "name": "experiment"}]})
        if request.method == "POST" and parsed.path == "/api/v1/datasets/ds_v/commits":
            return httpx.Response(
                201,
                json={
                    "id": "c_1",
                    "dataset_id": "ds_v",
                    "message": body["message"],
                    "tree_hash": "th_123",
                    "item_count": 5,
                },
            )
        if request.method == "GET" and parsed.path == "/api/v1/datasets/ds_v/commits":
            return httpx.Response(200, json={"items": [{"id": "c_1"}], "next_cursor": None})
        if request.method == "GET" and parsed.path == "/api/v1/datasets/ds_v/commits/c_1/items":
            return httpx.Response(
                200,
                json={"items": [{"id": "ci_1", "item_key": "item-001"}], "next_cursor": None},
            )
        if request.method == "GET" and parsed.path == "/api/v1/datasets/ds_v/diff":
            return httpx.Response(
                200,
                json={
                    "dataset_id": "ds_v",
                    "base_commit_id": "c_0",
                    "head_commit_id": "c_1",
                    "added": ["item-002"],
                    "modified": [],
                    "removed": [],
                },
            )
        if request.method == "POST" and parsed.path == "/api/v1/datasets/ds_v/merge":
            return httpx.Response(
                200,
                json={"id": "c_merge", "dataset_id": "ds_v", "message": "Merged experiment"},
            )
        if request.method == "POST" and parsed.path == "/api/v1/datasets/ds_v/revert":
            return httpx.Response(
                201,
                json={"id": "c_revert", "dataset_id": "ds_v", "message": "Revert to c_0"},
            )
        raise AssertionError(f"Unexpected: {request.method} {request.url}")

    client = _make_client(handler)
    ds_client = client.datasets

    # 1. create_branch
    branch = ds_client.create_branch("ds_v", "experiment", from_branch="main")
    assert branch["name"] == "experiment"
    assert calls[-1][2] == {"name": "experiment", "from_branch": "main"}

    # 2. list_branches unrolls items into a list
    branches = ds_client.list_branches("ds_v")
    assert isinstance(branches, list)
    assert len(branches) == 1
    assert branches[0]["name"] == "experiment"

    # 3. commit
    commit = ds_client.commit(
        dataset_id="ds_v",
        branch="experiment",
        message="Add 5 math items",
        changes=[{"op": "add", "item_key": "item-001", "data": {"input": {"x": 1}}}],
        snapshot_from_working_tree=False,
    )
    assert commit["id"] == "c_1"
    assert calls[-1][2]["message"] == "Add 5 math items"
    assert len(calls[-1][2]["changes"]) == 1

    # 4. list_commits
    commits = ds_client.list_commits("ds_v", branch="experiment", limit=25, cursor="cur_c")
    assert len(commits["items"]) == 1
    assert calls[-1][3]["branch"] == ["experiment"]
    assert calls[-1][3]["limit"] == ["25"]
    assert calls[-1][3]["cursor"] == ["cur_c"]

    # 5. get_commit_items
    commit_items = ds_client.get_commit_items("ds_v", "c_1", limit=10, cursor="cur_ci")
    assert commit_items["items"][0]["item_key"] == "item-001"
    assert calls[-1][1] == "/api/v1/datasets/ds_v/commits/c_1/items"
    assert calls[-1][3]["limit"] == ["10"]
    assert calls[-1][3]["cursor"] == ["cur_ci"]

    # 6. diff
    diff_res = ds_client.diff("ds_v", base="main", head="experiment")
    assert diff_res["added"] == ["item-002"]
    assert calls[-1][3]["base"] == ["main"]
    assert calls[-1][3]["head"] == ["experiment"]

    # 7. merge
    merge_res = ds_client.merge(
        dataset_id="ds_v",
        source_branch="experiment",
        target_branch="main",
        conflict_strategy="fail",
        message="Merge experiment into main",
    )
    assert merge_res["id"] == "c_merge"
    assert calls[-1][2]["conflict_strategy"] == "fail"

    # 8. revert
    revert_res = ds_client.revert(
        dataset_id="ds_v",
        branch="main",
        target_commit_id="c_0",
        message="Rollback to c_0",
    )
    assert revert_res["id"] == "c_revert"
    assert calls[-1][2]["target_commit_id"] == "c_0"
